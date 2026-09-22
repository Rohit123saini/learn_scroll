import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../../utils/api.dart';
import '../../services/auth_service.dart';
import '../config/testseries_config.dart';
import 'testseries_models.dart';

// ============================================================
// TEST SERIES — API SERVICE
//
// Endpoints (testseries/urls.py + reference §12):
//   GET  {mount}/testseries/                         list (?source=) — paginated
//   GET  {mount}/testseries/{id}/                    retrieve
//   GET  {mount}/testseries/{id}/questions/          questions
//   GET  {mount}/attempts/                           my attempts — paginated
//   GET  {mount}/attempts/{id}/                      one attempt + responses
//   POST {mount}/attempts/start/{series_id}/         start (idempotent, 402 = coins kam)
//   POST {mount}/attempts/{id}/submit/               submit
//   GET  {mount}/testseries/{id}/reviews/            reviews (public read)
//   POST {mount}/testseries/{id}/reviews/            review likho
//   POST {mount}/attempts/{id}/ask-query/            checked attempt pe doubt
//
// Is layer ki guarantees:
//   • Har failure `TestSeriesApiException` hai, jisme typed `kind` hai —
//     UI kabhi raw exception ya "NOT_AUTHENTICATED" jaisa string nahi dikhata.
//   • GET requests (network / timeout / 5xx pe) auto-retry hoti hain.
//   • DRF pagination `next` follow hota hai, aur sirf same-host pe (bearer
//     token kabhi doosre host ko nahi jaata).
//   • Server ki `Date` header se clock offset track hota hai (timer ke liye).
// ============================================================

/// Purane callers ke liye alias — asli config `TsConfig.mount` me hai.
const String kTestSeriesMount = TsConfig.mount;

enum TsErrorKind {
  network,
  timeout,
  unauthorized,
  forbidden,
  notFound,
  insufficientCoins,
  validation,
  conflict,
  rateLimited,
  server,
  unknown,
}

TsErrorKind _kindFromStatus(int? code) {
  if (code == null) return TsErrorKind.unknown;
  if (code == 401) return TsErrorKind.unauthorized;
  if (code == 402) return TsErrorKind.insufficientCoins;
  if (code == 403) return TsErrorKind.forbidden;
  if (code == 404) return TsErrorKind.notFound;
  if (code == 409) return TsErrorKind.conflict;
  if (code == 429) return TsErrorKind.rateLimited;
  if (code == 400 || code == 422) return TsErrorKind.validation;
  if (code >= 500) return TsErrorKind.server;
  return TsErrorKind.unknown;
}

class TestSeriesApiException implements Exception {
  /// Backend ka message (validation / conflict me user ko dikhane layak
  /// hota hai), warna internal tag. UI ko `tsErrorMessage()` use karna chahiye.
  final String message;
  final int? statusCode;
  final TsErrorKind kind;

  TestSeriesApiException(this.message, {this.statusCode, TsErrorKind? kind})
      : kind = kind ?? _kindFromStatus(statusCode);

  /// 402 = "coins kam hain" (purchase_and_start_attempt ka ValueError).
  bool get isInsufficientCoins => kind == TsErrorKind.insufficientCoins;
  bool get isUnauthorized => kind == TsErrorKind.unauthorized;
  bool get isNetwork => kind == TsErrorKind.network;
  bool get isTimeout => kind == TsErrorKind.timeout;

  /// Dobara koshish karna sahi hai (connectivity / server-side temporary).
  bool get isRetryable =>
      kind == TsErrorKind.network ||
      kind == TsErrorKind.timeout ||
      kind == TsErrorKind.server ||
      kind == TsErrorKind.rateLimited;

  @override
  String toString() => message;
}

class TestSeriesService {
  TestSeriesService._();

  /// App ise set kar sakta hai (e.g. force-logout) — 401 aane par ek baar call hota hai.
  static void Function()? onUnauthorized;

  /// Unexpected errors ka hook (Crashlytics / Sentry). Optional.
  static void Function(Object error, StackTrace? stack)? onUnexpectedError;

  static final http.Client _client = http.Client();

  static String get _root => '${Api.baseUrl}${TsConfig.mount}';
  static String get _series => '$_root/testseries';
  static String get _attempts => '$_root/attempts';

  // ---------------- server clock ----------------

  static Duration? _serverOffset;

  /// `serverTime - deviceTime`. Sirf tab non-null jab kam se kam ek response
  /// me `Date` header aaya ho.
  static Duration? get serverOffset => _serverOffset;
  static bool get hasServerClock => _serverOffset != null;

  /// Server ke hisaab se ab kitne baje hain (UTC). Header na mile to device clock.
  static DateTime get serverNow => DateTime.now().toUtc().add(_serverOffset ?? Duration.zero);

  static void _syncClock(http.BaseResponse r) {
    final d = r.headers['date'];
    if (d == null) return;
    try {
      final server = HttpDate.parse(d);
      _serverOffset = server.difference(DateTime.now().toUtc());
    } catch (_) {}
  }

  // ---------------- plumbing ----------------

  static Future<Map<String, String>> _headers({bool json = true}) async {
    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) {
      throw TestSeriesApiException('NOT_AUTHENTICATED', kind: TsErrorKind.unauthorized);
    }
    return {
      'Authorization': 'Bearer $token',
      if (json) 'Content-Type': 'application/json',
    };
  }

  static TsErrorKind? _transientKind(Object e) {
    if (e is TimeoutException) return TsErrorKind.timeout;
    if (e is IOException || e is http.ClientException) return TsErrorKind.network;
    return null;
  }

  static Duration _backoff(int attempt) => TsConfig.retryBaseDelay * (1 << (attempt - 1));

  /// Idempotent GET — network / timeout / 5xx pe retry.
  static Future<http.Response> _get(Uri uri) async {
    final headers = await _headers();
    var attempt = 0;
    while (true) {
      try {
        final r = await _client.get(uri, headers: headers).timeout(TsConfig.getTimeout);
        _syncClock(r);
        if (r.statusCode >= 500 && attempt < TsConfig.getRetries) {
          attempt++;
          await Future<void>.delayed(_backoff(attempt));
          continue;
        }
        return r;
      } catch (e) {
        final kind = _transientKind(e);
        if (kind == null) rethrow;
        if (attempt < TsConfig.getRetries) {
          attempt++;
          await Future<void>.delayed(_backoff(attempt));
          continue;
        }
        throw TestSeriesApiException(kind == TsErrorKind.timeout ? 'TIMEOUT' : 'NETWORK', kind: kind);
      }
    }
  }

  /// POST — koi auto-retry nahi (caller decide karta hai; submit ka apna
  /// reconcile flow hai).
  static Future<http.Response> _postJson(Uri uri, Object body, {Duration? timeout}) async {
    final headers = await _headers();
    try {
      final r = await _client
          .post(uri, headers: headers, body: jsonEncode(body))
          .timeout(timeout ?? TsConfig.postTimeout);
      _syncClock(r);
      return r;
    } catch (e) {
      final kind = _transientKind(e);
      if (kind == null) rethrow;
      throw TestSeriesApiException(kind == TsErrorKind.timeout ? 'TIMEOUT' : 'NETWORK', kind: kind);
    }
  }

  static dynamic _decode(http.Response r) {
    try {
      return jsonDecode(utf8.decode(r.bodyBytes));
    } catch (_) {
      throw TestSeriesApiException('BAD_RESPONSE', statusCode: r.statusCode, kind: TsErrorKind.server);
    }
  }

  static Map<String, dynamic> _asMap(http.Response r) {
    final body = _decode(r);
    if (body is! Map) {
      throw TestSeriesApiException('BAD_RESPONSE', statusCode: r.statusCode, kind: TsErrorKind.server);
    }
    return Map<String, dynamic>.from(body);
  }

  static Never _fail(http.Response r) {
    String message = 'Request failed (${r.statusCode})';
    try {
      final body = jsonDecode(utf8.decode(r.bodyBytes));
      String? pick(dynamic v) {
        if (v is String) return v;
        if (v is List && v.isNotEmpty) return pick(v.first);
        if (v is Map && v.isNotEmpty) return pick(v.values.first);
        return v?.toString();
      }

      if (body is Map) {
        // DRF ka error shape: {"detail": "..."} / {"field": ["..."]} / {"non_field_errors": [...]}
        final picked = pick(body['detail'] ?? (body.isNotEmpty ? body.values.first : null));
        if (picked != null && picked.isNotEmpty) message = picked;
      } else if (body is List) {
        final picked = pick(body);
        if (picked != null && picked.isNotEmpty) message = picked;
      }
    } catch (_) {}

    final ex = TestSeriesApiException(message, statusCode: r.statusCode);
    if (ex.isUnauthorized) {
      try {
        onUnauthorized?.call();
      } catch (_) {}
    }
    throw ex;
  }

  /// DRF `next` URL ko safe banata hai: sirf same host, aur reverse-proxy
  /// ki wajah se aane wala `http://` https base pe upgrade.
  static String? _safeNext(String? next) {
    if (next == null || next.isEmpty || next == 'null') return null;
    final n = Uri.tryParse(next);
    final base = Uri.tryParse(Api.baseUrl);
    if (n == null || base == null || !n.hasAuthority) return null;
    if (n.host != base.host) return null; // bearer token kisi aur host ko nahi
    if (base.scheme == 'https' && n.scheme == 'http') {
      return n.replace(scheme: 'https').toString();
    }
    return n.toString();
  }

  static Future<TsPage<T>> _getPage<T>(Uri uri, T Function(Map<String, dynamic>) parse) async {
    final r = await _get(uri);
    if (r.statusCode != 200) _fail(r);
    final body = _decode(r);

    List raw = const [];
    String? next;
    if (body is List) {
      raw = body;
    } else if (body is Map && body['results'] is List) {
      raw = body['results'] as List;
      next = _safeNext(body['next']?.toString());
    }
    return TsPage<T>(
      items: raw.whereType<Map>().map((e) => parse(Map<String, dynamic>.from(e))).toList(),
      nextUrl: next,
    );
  }

  static Future<List<T>> _getAll<T>(Uri first, T Function(Map<String, dynamic>) parse) async {
    final out = <T>[];
    Uri? uri = first;
    var pages = 0;
    while (uri != null && pages < TsConfig.maxPages) {
      final page = await _getPage<T>(uri, parse);
      out.addAll(page.items);
      uri = page.nextUrl == null ? null : Uri.parse(page.nextUrl!);
      pages++;
    }
    return out;
  }

  // ---------------- media ----------------

  /// Question / answer attachment ka full URL. Relative path ho to API ke
  /// origin se jodta hai; `http://` ko https base pe upgrade karta hai.
  static String? resolveMedia(String? raw) {
    if (raw == null || raw.isEmpty) return null;
    final base = Uri.tryParse(Api.baseUrl);
    if (base == null) return raw;
    final u = Uri.tryParse(raw);
    if (u == null) return null;
    if (u.hasAuthority) {
      if (base.scheme == 'https' && u.scheme == 'http' && u.host == base.host) {
        return u.replace(scheme: 'https').toString();
      }
      return raw;
    }
    final origin = Uri(scheme: base.scheme, host: base.host, port: base.hasPort ? base.port : null);
    return origin.resolve(raw.startsWith('/') ? raw : '/$raw').toString();
  }

  // ---------------- series ----------------

  /// Ek page (infinite scroll ke liye). `pageUrl` pichhle page ka `nextUrl`.
  static Future<TsPage<TestSeriesModel>> listSeriesPage({String? source, String? pageUrl}) {
    final uri = pageUrl != null
        ? Uri.parse(pageUrl)
        : Uri.parse('$_series/').replace(queryParameters: source == null ? null : {'source': source});
    return _getPage<TestSeriesModel>(uri, TestSeriesModel.fromJson);
  }

  /// Compat: pehla page hi (purane callers ke liye).
  static Future<List<TestSeriesModel>> listSeries({String? source}) async =>
      (await listSeriesPage(source: source)).items;

  static Future<TestSeriesModel> getSeries(String id) async {
    final r = await _get(Uri.parse('$_series/$id/'));
    if (r.statusCode != 200) _fail(r);
    return TestSeriesModel.fromJson(_asMap(r));
  }

  static Future<List<TsQuestion>> getQuestions(String seriesId) async {
    final list = await _getAll<TsQuestion>(Uri.parse('$_series/$seriesId/questions/'), TsQuestion.fromJson);
    list.sort((a, b) => a.order.compareTo(b.order));
    return list;
  }

  // ---------------- attempts ----------------

  /// Meri SAARI attempts (saare pages follow karke).
  static Future<List<TestAttemptModel>> listMyAttempts() =>
      _getAll<TestAttemptModel>(Uri.parse('$_attempts/'), TestAttemptModel.fromJson);

  static Future<TestAttemptModel> getAttempt(String id) async {
    final r = await _get(Uri.parse('$_attempts/$id/'));
    if (r.statusCode != 200) _fail(r);
    return TestAttemptModel.fromJson(_asMap(r));
  }

  /// Submit fail hone ke baad check: kya server pe attempt asal me submit ho
  /// chuka hai (response raaste me kho gaya)? Ho chuka ho to attempt, warna null.
  static Future<TestAttemptModel?> tryReconcile(String attemptId) async {
    try {
      final a = await getAttempt(attemptId);
      return a.isFinished ? a : null;
    } catch (_) {
      return null;
    }
  }

  /// Attempt shuru karo. Idempotent hai: pehle se ek `in_progress` attempt
  /// ho to wahi wapas milta hai aur paid series dobara charge nahi hoti.
  /// Coins kam hone par 402 → `TestSeriesApiException.isInsufficientCoins`.
  static Future<TestAttemptModel> startAttempt(
    String seriesId, {
    String? rollNumber,
    String? enrollmentNo,
  }) async {
    final body = <String, dynamic>{};
    if (rollNumber != null && rollNumber.isNotEmpty) body['roll_number'] = rollNumber;
    if (enrollmentNo != null && enrollmentNo.isNotEmpty) body['enrollment_no'] = enrollmentNo;

    final r = await _postJson(Uri.parse('$_attempts/start/$seriesId/'), body);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return TestAttemptModel.fromJson(_asMap(r));
  }

  /// Submit.
  ///
  /// `answers` ek MAP hai (`{question_id: answer_data}`) — assignments ki
  /// list-shape se alag, ye backend ka confirmed contract hai (§12.5).
  /// Files sirf `text` questions pe allowed hain aur flat
  /// `answer_<question_id>` key pe jaati hain; multipart me `answers` ek
  /// JSON **string** ban jaata hai, kyunki multipart me nested structure
  /// ka koi syntax hi nahi hota. Jo file disk se gayab ho chuki hai wo
  /// silently skip hoti hai (camera temp files OS saaf kar sakta hai).
  static Future<TestAttemptModel> submitAttempt({
    required String attemptId,
    required Map<String, dynamic> answers,
    Map<String, File> filesByQuestionId = const {},
  }) async {
    final url = Uri.parse('$_attempts/$attemptId/submit/');
    final files = {
      for (final e in filesByQuestionId.entries)
        if (e.value.existsSync()) e.key: e.value,
    };

    if (files.isEmpty) {
      final r = await _postJson(url, {'answers': answers}, timeout: TsConfig.submitTimeout);
      if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
      return TestAttemptModel.fromJson(_asMap(r));
    }

    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) {
      throw TestSeriesApiException('NOT_AUTHENTICATED', kind: TsErrorKind.unauthorized);
    }

    try {
      final req = http.MultipartRequest('POST', url)..headers['Authorization'] = 'Bearer $token';
      req.fields['answers'] = jsonEncode(answers);
      for (final e in files.entries) {
        req.files.add(await http.MultipartFile.fromPath('answer_${e.key}', e.value.path));
      }
      final streamed = await _client.send(req).timeout(TsConfig.uploadTimeout);
      final r = await http.Response.fromStream(streamed);
      _syncClock(r);
      if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
      return TestAttemptModel.fromJson(_asMap(r));
    } on TestSeriesApiException {
      rethrow;
    } catch (e) {
      final kind = _transientKind(e);
      if (kind == null) rethrow;
      throw TestSeriesApiException(kind == TsErrorKind.timeout ? 'TIMEOUT' : 'NETWORK', kind: kind);
    }
  }

  // ---------------- reviews ----------------

  static Future<List<TestSeriesReview>> listReviews(String seriesId) =>
      _getAll<TestSeriesReview>(Uri.parse('$_series/$seriesId/reviews/'), TestSeriesReview.fromJson);

  /// 400 aata hai agar attempt abhi `checked` nahi hua, ya pehle se review
  /// de chuke ho — dono cases me backend ka message user ko dikhane layak
  /// hota hai, isliye `_fail()` usko parse karke exception me daal deta hai.
  static Future<TestSeriesReview> createReview({
    required String seriesId,
    required int rating,
    String comment = '',
  }) async {
    final r = await _postJson(
      Uri.parse('$_series/$seriesId/reviews/'),
      {'rating': rating, 'comment': comment},
    );
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return TestSeriesReview.fromJson(_asMap(r));
  }

  // ---------------- creator: publish / answer key / questions ----------------

  /// `POST {mount}/testseries/{id}/publish/` — draft → published (creator
  /// only; refuses if answer key incomplete or has no questions).
  static Future<TestSeriesModel> publishSeries(String seriesId) async {
    final r = await _postJson(Uri.parse('$_series/$seriesId/publish/'), {});
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return TestSeriesModel.fromJson(_asMap(r));
  }

  /// `GET {mount}/testseries/{id}/answer-key/` — `{complete, missing_question_orders}`.
  static Future<Map<String, dynamic>> answerKeyStatus(String seriesId) async {
    final r = await _get(Uri.parse('$_series/$seriesId/answer-key/'));
    if (r.statusCode != 200) _fail(r);
    return _asMap(r);
  }

  /// `POST {mount}/testseries/{id}/questions-bulk/` — draft-only, all-or-nothing.
  static Future<List<TsQuestion>> questionsBulk(String seriesId, List<Map<String, dynamic>> questions) async {
    final r = await _postJson(Uri.parse('$_series/$seriesId/questions-bulk/'), {'questions': questions});
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    final body = _asMap(r);
    final list = body['questions'];
    return list is List
        ? list.whereType<Map>().map((e) => TsQuestion.fromJson(Map<String, dynamic>.from(e))).toList()
        : const [];
  }

  /// `POST {mount}/testseries/{id}/questions-import/` — CSV with the answer key.
  static Future<Map<String, dynamic>> questionsImportCsv(String seriesId, File csv) async {
    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) {
      throw TestSeriesApiException('NOT_AUTHENTICATED', kind: TsErrorKind.unauthorized);
    }
    final req = http.MultipartRequest('POST', Uri.parse('$_series/$seriesId/questions-import/'))
      ..headers['Authorization'] = 'Bearer $token';
    req.files.add(await http.MultipartFile.fromPath('file', csv.path));
    final streamed = await _client.send(req).timeout(TsConfig.uploadTimeout);
    final r = await http.Response.fromStream(streamed);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return _asMap(r);
  }

  // ---------------- creator: results / leaderboard / certificates ----------------

  /// `POST {mount}/testseries/{id}/release-results/`.
  static Future<DateTime?> releaseResults(String seriesId) async {
    final r = await _postJson(Uri.parse('$_series/$seriesId/release-results/'), {});
    if (r.statusCode != 200) _fail(r);
    return DateTime.tryParse(_asMap(r)['results_released_at']?.toString() ?? '');
  }

  /// `GET {mount}/testseries/{id}/leaderboard/?limit=`.
  static Future<List<TsLeaderboardRow>> leaderboard(String seriesId, {int limit = 20}) async {
    final r = await _get(Uri.parse('$_series/$seriesId/leaderboard/').replace(
      queryParameters: {'limit': '$limit'},
    ));
    if (r.statusCode != 200) _fail(r);
    final rows = _asMap(r)['results'];
    return rows is List
        ? rows.whereType<Map>().map((e) => TsLeaderboardRow.fromJson(Map<String, dynamic>.from(e))).toList()
        : const [];
  }

  /// `GET {mount}/testseries/{id}/certificates/` — creator: every certificate issued.
  static Future<List<TsCertificate>> seriesCertificates(String seriesId) async {
    final r = await _get(Uri.parse('$_series/$seriesId/certificates/'));
    if (r.statusCode != 200) _fail(r);
    final decoded = _decode(r);
    return decoded is List
        ? decoded.whereType<Map>().map((e) => TsCertificate.fromJson(Map<String, dynamic>.from(e))).toList()
        : const [];
  }

  /// `POST {mount}/testseries/{id}/revoke-certificate/` — body: `{code, reason}`.
  static Future<TsCertificate> revokeCertificate(String seriesId, {required String code, String reason = ''}) async {
    final r = await _postJson(
      Uri.parse('$_series/$seriesId/revoke-certificate/'),
      {'code': code, 'reason': reason},
    );
    if (r.statusCode != 200) _fail(r);
    return TsCertificate.fromJson(_asMap(r));
  }

  /// `GET {mount}/testseries/certificates/mine/` — my own certificates.
  static Future<List<TsCertificate>> myCertificates() async {
    final r = await _get(Uri.parse('$_root/testseries/certificates/mine/'));
    if (r.statusCode != 200) _fail(r);
    final decoded = _decode(r);
    final list = decoded is Map ? decoded['results'] : decoded;
    return list is List
        ? list.whereType<Map>().map((e) => TsCertificate.fromJson(Map<String, dynamic>.from(e))).toList()
        : const [];
  }

  /// `GET {mount}/testseries/certificates/verify/{code}/` — public,
  /// unauthenticated (an employer verifying a printed certificate); does
  /// NOT go through `_get()` since that requires a bearer token.
  static Future<Map<String, dynamic>> verifyCertificate(String code) async {
    final r = await _client
        .get(Uri.parse('$_root/testseries/certificates/verify/$code/'))
        .timeout(TsConfig.getTimeout);
    if (r.statusCode != 200) _fail(r);
    return _asMap(r);
  }

  /// `GET {mount}/testseries/public/{slug}/` — public share-link preview
  /// (marketing card only, no questions). Also unauthenticated.
  static Future<TestSeriesModel> publicSeries(String slug) async {
    final r = await _client.get(Uri.parse('$_root/testseries/public/$slug/')).timeout(TsConfig.getTimeout);
    if (r.statusCode != 200) _fail(r);
    return TestSeriesModel.fromJson(_asMap(r));
  }

  // ---------------- creator: live class ----------------

  /// `POST {mount}/testseries/{id}/live-start/` — host goes live.
  static Future<TsLiveToken> liveStart(String seriesId) async {
    final r = await _postJson(Uri.parse('$_series/$seriesId/live-start/'), {});
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return TsLiveToken.fromJson(_asMap(r));
  }

  /// `POST {mount}/testseries/{id}/live-end/`.
  static Future<void> liveEnd(String seriesId) async {
    final r = await _postJson(Uri.parse('$_series/$seriesId/live-end/'), {});
    if (r.statusCode != 200) _fail(r);
  }

  /// `POST {mount}/testseries/{id}/live-token/` — fresh host token.
  static Future<TsLiveToken> seriesLiveToken(String seriesId) async {
    final r = await _postJson(Uri.parse('$_series/$seriesId/live-token/'), {});
    if (r.statusCode != 200) _fail(r);
    return TsLiveToken.fromJson(_asMap(r));
  }

  /// `GET {mount}/testseries/{id}/recordings/`.
  static Future<List<TsRecording>> recordings(String seriesId) async {
    final r = await _get(Uri.parse('$_series/$seriesId/recordings/'));
    if (r.statusCode != 200) _fail(r);
    final decoded = _decode(r);
    return decoded is List
        ? decoded.whereType<Map>().map((e) => TsRecording.fromJson(Map<String, dynamic>.from(e))).toList()
        : const [];
  }

  // ---------------- attempt: autosave / solutions / analytics ----------------

  /// `PATCH {mount}/attempts/{id}/save/` — server-side autosave. Call this
  /// periodically while a student is attempting; independent of `submit`.
  static Future<void> saveProgress({required String attemptId, required Map<String, dynamic> answers}) async {
    final r = await _postJson(Uri.parse('$_attempts/$attemptId/save/'), {'answers': answers});
    if (r.statusCode != 200) _fail(r);
  }

  /// `GET {mount}/attempts/{id}/solutions/` — full answer key + explanation,
  /// only once results are visible (or the creator has `show_solutions` on).
  static Future<List<Map<String, dynamic>>> solutions(String attemptId) async {
    final r = await _get(Uri.parse('$_attempts/$attemptId/solutions/'));
    if (r.statusCode != 200) _fail(r);
    final results = _asMap(r)['results'];
    return results is List ? results.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList() : const [];
  }

  /// `GET {mount}/attempts/{id}/analytics/` — rank / percentile / per-topic
  /// accuracy / time. Raw map (shape is rich + still settling), same
  /// pattern as `answerKeyStatus`.
  static Future<Map<String, dynamic>> analytics(String attemptId) async {
    final r = await _get(Uri.parse('$_attempts/$attemptId/analytics/'));
    if (r.statusCode != 200) _fail(r);
    return _asMap(r);
  }

  // ---------------- attempt: certificate ----------------

  static Future<TsCertificate> attemptCertificate(String attemptId) async {
    final r = await _get(Uri.parse('$_attempts/$attemptId/certificate/'));
    if (r.statusCode != 200) _fail(r);
    return TsCertificate.fromJson(_asMap(r));
  }

  /// `GET {mount}/attempts/{id}/certificate-pdf/` — the URL itself (used
  /// directly by the PDF viewer / download; auth header still required, so
  /// screens should fetch via `downloadCertificatePdf` instead of a bare
  /// `Image.network`-style load).
  static String certificatePdfUrl(String attemptId) => '$_attempts/$attemptId/certificate-pdf/';

  /// Downloads the certificate PDF bytes (auth header attached).
  static Future<List<int>> downloadCertificatePdf(String attemptId) async {
    final headers = await _headers(json: false);
    final r = await _client
        .get(Uri.parse(certificatePdfUrl(attemptId)), headers: headers)
        .timeout(TsConfig.getTimeout);
    if (r.statusCode != 200) _fail(r);
    return r.bodyBytes;
  }

  // ---------------- attempt: live / proctoring ----------------

  /// `POST {mount}/attempts/{id}/live-token/` — student's own join token
  /// (live-class subscribe token and/or proctor publish token).
  static Future<Map<String, TsLiveToken>> attemptLiveToken(String attemptId) async {
    final r = await _postJson(Uri.parse('$_attempts/$attemptId/live-token/'), {});
    if (r.statusCode != 200) _fail(r);
    final body = _asMap(r);
    final out = <String, TsLiveToken>{};
    for (final key in const ['live', 'proctor']) {
      final v = body[key];
      if (v is Map) out[key] = TsLiveToken.fromJson(Map<String, dynamic>.from(v));
    }
    return out;
  }

  /// `POST {mount}/attempts/{id}/proctor-events/` — client-reported
  /// integrity signal (tab switch, face missing, ...). Advisory only.
  static Future<void> reportProctorEvent(String attemptId, TsProctorEvent event) async {
    final r = await _postJson(Uri.parse('$_attempts/$attemptId/proctor-events/'), event.toJson());
    if (r.statusCode != 200 && r.statusCode != 201 && r.statusCode != 204) _fail(r);
  }

  /// `GET {mount}/attempts/{id}/integrity/` — creator/reviewer: flags + events + recordings.
  static Future<Map<String, dynamic>> integrity(String attemptId) async {
    final r = await _get(Uri.parse('$_attempts/$attemptId/integrity/'));
    if (r.statusCode != 200) _fail(r);
    return _asMap(r);
  }

  /// `POST {mount}/attempts/{id}/proctor-watch/` — creator/reviewer: token to watch this candidate live.
  static Future<TsLiveToken> proctorWatch(String attemptId) async {
    final r = await _postJson(Uri.parse('$_attempts/$attemptId/proctor-watch/'), {});
    if (r.statusCode != 200) _fail(r);
    return TsLiveToken.fromJson(_asMap(r));
  }

  // ---------------- doubts ----------------

  /// Sirf `checked` attempt pe allowed (backend rule) — warna 400.
  static Future<void> askQuery({
    required String attemptId,
    required String text,
    bool isAnonymous = false,
  }) async {
    final r = await _postJson(
      Uri.parse('$_attempts/$attemptId/ask-query/'),
      {'text': text, 'is_anonymous': isAnonymous},
    );
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
  }

  // ---------------- answer_data shapes ----------------
  //
  // §4.3 ki shape table se (answer_data ka shape wahi hai jo us type ke
  // `correct_answer` ka hai), aur §12.5 ke example payloads se.
  // Ek hi jagah rakhi hain taaki backend shape badle to screens untouched
  // rahein.

  static Map<String, dynamic> mcqAnswer(String optionId) => {'option_id': optionId};
  static Map<String, dynamic> msqAnswer(List<String> optionIds) => {'option_ids': optionIds};
  static Map<String, dynamic> orderAnswer(List<String> sequence) =>
      {'list_mode': 'order', 'sequence': sequence};
  static Map<String, dynamic> matchAnswer(Map<String, String> pairs) =>
      {'list_mode': 'match', 'pairs': pairs};
  static Map<String, dynamic> textAnswer(String text) => {'text': text};
}
