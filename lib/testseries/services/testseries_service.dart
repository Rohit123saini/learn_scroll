import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../../utils/api.dart';
import '../../services/auth_service.dart';
import 'testseries_models.dart';

// ============================================================
// TEST SERIES — API SERVICE
//
// Endpoints (testseries/urls.py + reference §12):
//   GET  {mount}/testseries/                         list (?source=)
//   GET  {mount}/testseries/{id}/                    retrieve
//   GET  {mount}/testseries/{id}/questions/          questions
//   GET  {mount}/attempts/                           my attempts
//   GET  {mount}/attempts/{id}/                      one attempt + responses
//   POST {mount}/attempts/start/{series_id}/         start (idempotent, 402 = coins kam)
//   POST {mount}/attempts/{id}/submit/               submit
//   GET  {mount}/testseries/{id}/reviews/            reviews (public read)
//   POST {mount}/testseries/{id}/reviews/            review likho
//   POST {mount}/attempts/{id}/ask-query/            checked attempt pe doubt
// ============================================================

/// 🔧 CONFIRM WITH BACKEND — `testseries.urls` root URLconf me kahan mount
/// hai. Dhyan do: `attempts` router pe registered hai, isliye uska path
/// `{mount}/attempts/` hai — `{mount}/testseries/attempts/` NAHI.
/// Reference doc kehta hai "Base path: whatever testseries.urls is mounted
/// at (example: /api/)". Baaki app bina prefix ke chalti hai, isliye
/// default khaali rakha hai.
const String kTestSeriesMount = '';

class TestSeriesApiException implements Exception {
  final String message;
  final int? statusCode;
  TestSeriesApiException(this.message, {this.statusCode});

  /// 402 = "coins kam hain" (purchase_and_start_attempt ka ValueError).
  bool get isInsufficientCoins => statusCode == 402;
  @override
  String toString() => message;
}

class TestSeriesService {
  TestSeriesService._();

  static const Duration _timeout = Duration(seconds: 15);

  static String get _root => '${Api.baseUrl}$kTestSeriesMount';
  static String get _series => '$_root/testseries';
  static String get _attempts => '$_root/attempts';

  static Future<Map<String, String>> _headers({bool json = true}) async {
    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) {
      throw TestSeriesApiException('NOT_AUTHENTICATED');
    }
    return {
      'Authorization': 'Bearer $token',
      if (json) 'Content-Type': 'application/json',
    };
  }

  static List<dynamic> _asList(dynamic decoded) {
    if (decoded is List) return decoded;
    if (decoded is Map && decoded['results'] is List) return decoded['results'] as List;
    return const [];
  }

  static Map<String, dynamic> _asMap(http.Response r) =>
      Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map);

  static Never _fail(http.Response r) {
    String message = 'Request failed (${r.statusCode})';
    try {
      final body = jsonDecode(utf8.decode(r.bodyBytes));
      if (body is Map) {
        // DRF ka error shape: {"detail": "..."} ya {"field": ["..."]}
        final detail = body['detail'];
        if (detail != null) {
          message = detail.toString();
        } else if (body.isNotEmpty) {
          final first = body.values.first;
          message = first is List && first.isNotEmpty ? first.first.toString() : first.toString();
        }
      }
    } catch (_) {}
    throw TestSeriesApiException(message, statusCode: r.statusCode);
  }

  // ---------------- series ----------------

  static Future<List<TestSeriesModel>> listSeries({String? source}) async {
    final uri = Uri.parse('$_series/').replace(queryParameters: source == null ? null : {'source': source});
    final r = await http.get(uri, headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return _asList(jsonDecode(utf8.decode(r.bodyBytes)))
        .map((e) => TestSeriesModel.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  static Future<TestSeriesModel> getSeries(String id) async {
    final r = await http.get(Uri.parse('$_series/$id/'), headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return TestSeriesModel.fromJson(_asMap(r));
  }

  static Future<List<TsQuestion>> getQuestions(String seriesId) async {
    final r =
        await http.get(Uri.parse('$_series/$seriesId/questions/'), headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    final list = _asList(jsonDecode(utf8.decode(r.bodyBytes)))
        .map((e) => TsQuestion.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
    list.sort((a, b) => a.order.compareTo(b.order));
    return list;
  }

  // ---------------- attempts ----------------

  static Future<List<TestAttemptModel>> listMyAttempts() async {
    final r = await http.get(Uri.parse('$_attempts/'), headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return _asList(jsonDecode(utf8.decode(r.bodyBytes)))
        .map((e) => TestAttemptModel.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  static Future<TestAttemptModel> getAttempt(String id) async {
    final r = await http.get(Uri.parse('$_attempts/$id/'), headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return TestAttemptModel.fromJson(_asMap(r));
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

    final r = await http
        .post(Uri.parse('$_attempts/start/$seriesId/'), headers: await _headers(), body: jsonEncode(body))
        .timeout(_timeout);
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
  /// ka koi syntax hi nahi hota.
  static Future<TestAttemptModel> submitAttempt({
    required String attemptId,
    required Map<String, dynamic> answers,
    Map<String, File> filesByQuestionId = const {},
  }) async {
    final url = Uri.parse('$_attempts/$attemptId/submit/');

    if (filesByQuestionId.isEmpty) {
      final r = await http
          .post(url, headers: await _headers(), body: jsonEncode({'answers': answers}))
          .timeout(const Duration(seconds: 30));
      if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
      return TestAttemptModel.fromJson(_asMap(r));
    }

    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) throw TestSeriesApiException('NOT_AUTHENTICATED');

    final req = http.MultipartRequest('POST', url)..headers['Authorization'] = 'Bearer $token';
    req.fields['answers'] = jsonEncode(answers);
    for (final e in filesByQuestionId.entries) {
      req.files.add(await http.MultipartFile.fromPath('answer_${e.key}', e.value.path));
    }
    final streamed = await req.send().timeout(const Duration(seconds: 180));
    final r = await http.Response.fromStream(streamed);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return TestAttemptModel.fromJson(_asMap(r));
  }

  // ---------------- reviews ----------------

  static Future<List<TestSeriesReview>> listReviews(String seriesId) async {
    final r =
        await http.get(Uri.parse('$_series/$seriesId/reviews/'), headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return _asList(jsonDecode(utf8.decode(r.bodyBytes)))
        .map((e) => TestSeriesReview.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// 400 aata hai agar attempt abhi `checked` nahi hua, ya pehle se review
  /// de chuke ho — dono cases me backend ka message user ko dikhane layak
  /// hota hai, isliye `_fail()` usko parse karke exception me daal deta hai.
  static Future<TestSeriesReview> createReview({
    required String seriesId,
    required int rating,
    String comment = '',
  }) async {
    final r = await http
        .post(Uri.parse('$_series/$seriesId/reviews/'),
            headers: await _headers(), body: jsonEncode({'rating': rating, 'comment': comment}))
        .timeout(_timeout);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return TestSeriesReview.fromJson(_asMap(r));
  }

  // ---------------- doubts ----------------

  /// Sirf `checked` attempt pe allowed (backend rule) — warna 400.
  static Future<void> askQuery({
    required String attemptId,
    required String text,
    bool isAnonymous = false,
  }) async {
    final r = await http
        .post(Uri.parse('$_attempts/$attemptId/ask-query/'),
            headers: await _headers(), body: jsonEncode({'text': text, 'is_anonymous': isAnonymous}))
        .timeout(_timeout);
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
