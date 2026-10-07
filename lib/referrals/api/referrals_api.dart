// lib/referrals/api/referrals_api.dart
//
// ============================================================
// REFERRALS — APP-WIDE API SERVICE  (Task G12, growth_and_feature_tasks.md)
//
// Backend surface: GET /referrals/my-code/, POST /referrals/redeem/,
// GET /referrals/class-referral-summary/, GET /referrals/ (own ledger).
// These are the SAME endpoints `tuitionclass/api/tuitionclass_api.dart` already
// calls at `/tuitionclass/referrals/...` — see backend/tuitionclass/referral_urls.py
// for why they're now also mounted at the project root. This service hits
// the root path so referrals aren't tied to importing the tuitionclass module
// from Profile/Settings/anywhere else non-Tuition-Class.
//
// Response shape follows tuitionclass's plain-DRF convention (not
// user_profile's {"status","message","data"} envelope) — errors arrive as
// `{"error": {"code", "message"}}` (`tuitionclass/exceptions.py`'s handler is
// wired globally in REST_FRAMEWORK settings, so it applies here too even
// though the route isn't under /tuitionclass/).
// ============================================================

import 'dart:convert';
import 'package:http/http.dart' as http;

import '../../services/auth_service.dart';
import '../../utils/api.dart';

const Duration _kReferralsApiTimeout = Duration(seconds: 15);

class ReferralApiException implements Exception {
  final int statusCode;
  final String code;
  final String message;
  ReferralApiException(this.statusCode, this.code, this.message);
  @override
  String toString() => message;
}

class ReferralsApi {
  ReferralsApi._();
  static final ReferralsApi instance = ReferralsApi._();

  static String get _base => '${Api.baseUrl}/referrals';

  Future<Map<String, String>> _headers({bool json = true}) async {
    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) {
      throw ReferralApiException(401, 'not_authenticated', 'Please log in again.');
    }
    return {
      if (json) 'Content-Type': 'application/json',
      'Accept': 'application/json',
      'Authorization': 'Bearer $token',
    };
  }

  dynamic _decode(http.Response r) {
    final body = r.body.isEmpty ? null : jsonDecode(utf8.decode(r.bodyBytes));
    if (r.statusCode >= 200 && r.statusCode < 300) return body;
    final err = (body is Map ? body['error'] : null) as Map?;
    throw ReferralApiException(
      r.statusCode,
      err?['code']?.toString() ?? 'unknown_error',
      err?['message']?.toString() ??
          (body is Map ? (body['detail'] ?? body['error'])?.toString() : null) ??
          r.reasonPhrase ??
          'Request failed',
    );
  }

  /// List endpoints are DRF-paginated ({count, next, previous, results});
  /// screens here just want a flat List, same unwrap `TuitionClassApi._get` does.
  dynamic _unwrapPage(dynamic body) {
    if (body is Map && body['results'] is List && body.containsKey('count')) {
      return body['results'];
    }
    return body;
  }

  Future<dynamic> _get(String path) async {
    final res = await http
        .get(Uri.parse('$_base$path'), headers: await _headers(json: false))
        .timeout(_kReferralsApiTimeout);
    return _unwrapPage(_decode(res));
  }

  Future<dynamic> _post(String path, {Map<String, dynamic>? body}) async {
    final res = await http
        .post(Uri.parse('$_base$path'),
            headers: await _headers(), body: body == null ? null : jsonEncode(body))
        .timeout(_kReferralsApiTimeout);
    return _decode(res);
  }

  // ---------------- Endpoints ----------------

  /// Own referral code + running tally of redemptions/coins earned.
  Future<Map<String, dynamic>> myReferralCode() async =>
      Map<String, dynamic>.from(await _get('/my-code/') as Map);

  /// People the caller has successfully referred (own ledger).
  Future<List<dynamic>> myReferrals() async => List<dynamic>.from(await _get('/') as List);

  /// Redeem someone else's referral code — one-time, new-account-only.
  Future<Map<String, dynamic>> redeem(String code) async =>
      Map<String, dynamic>.from(await _post('/redeem/', body: {'code': code}) as Map);

  /// Tuition-Class commission summary — the class-level referral program
  /// (per-classroom commission on top of the flat signup bonus above).
  /// Shown as its own section on the shared screen since it's real earned
  /// coin, not because this API is Tuition-Class-only.
  Future<Map<String, dynamic>> classReferralSummary() async =>
      Map<String, dynamic>.from(await _get('/class-referral-summary/') as Map);

  /// TASK 12 — link attribution: tell the backend "I arrived through this
  /// referral code" (first touch wins, expires server-side). Expected refusals
  /// (bad code, existing customer, ...) come back as 200 with
  /// `attributed: false`, so callers never need to show an error for them.
  Future<Map<String, dynamic>> attribute(
    String code, {
    String sourceType = 'app',
    String sourceId = '',
  }) async =>
      Map<String, dynamic>.from(await _post('/attribute/', body: {
        'code': code,
        'source_type': sourceType,
        'source_id': sourceId,
      }) as Map);

  /// TASK 12 — commission totals (test series + classroom) and the most
  /// recent PAID commissions.
  Future<Map<String, dynamic>> earnings() async =>
      Map<String, dynamic>.from(await _get('/earnings/') as Map);
}
