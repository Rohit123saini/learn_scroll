// lib/notifications/services/notification_service.dart
//
// Backend endpoints (`core` app — already production-ready, koi naya backend
// kaam nahi chahiye):
//   GET  core/notifications/?limit=&offset=&source=          -> list (paginated)
//   GET  core/notifications/unread-count/                     -> {"unread_count": N}
//   POST core/notifications/{id}/mark-read/
//   POST core/notifications/mark-all-read/                    -> {"marked_read": N}
//   GET  core/notification-preferences/me/
//   PATCH core/notification-preferences/me/
//
// Base URL + token convention yahan wahi hai jo `home_api_model_service.dart`
// (`Api.baseUrl`, `AuthService.getValidToken()`) already use karta hai — koi
// naya/duplicate pattern nahi banaya. `getValidToken()` khud hi expiry-aware
// hai aur refresh-token bhi mar chuka ho to `AuthService.onForceLogout()` fire
// kar deta hai (jo `session_service.dart` ka notifier set karta hai) — isliye
// yahan alag se 401 → session-expiry wiring karne ki zaroorat nahi hai.

import 'dart:convert';
import 'package:http/http.dart' as http;

import '../models/notification_model.dart';
import '../../utils/api.dart';
import '../../services/auth_service.dart';
const Duration kApiTimeout = Duration(seconds: 15); // home_api_model_service.dart wala hi convention

class NotificationService {
  NotificationService._();
  static final NotificationService instance = NotificationService._();

  Future<Map<String, String>> _authHeaders() async {
    // Task 8 convention: plain getToken() nahi — getValidToken() use karo.
    final token = await AuthService.getValidToken();
    if (token == null) throw NotificationApiException('unauthorized', 401);
    return {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer $token',
    };
  }

  /// GET core/notifications/unread-count/
  /// Home screen ke bell-icon badge ke liye — isi ko `_loadUnreadCount()` se call karo.
  Future<int> getUnreadCount() async {
    final uri = Uri.parse('${Api.baseUrl}/core/notifications/unread-count/');
    final res = await http
        .get(uri, headers: await _authHeaders())
        .timeout(kApiTimeout);

    if (res.statusCode != 200) {
      throw NotificationApiException('failed_to_load_unread_count', res.statusCode);
    }
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    return body['unread_count'] as int? ?? 0;
  }

  /// GET core/notifications/?limit=&offset=&source=
  /// Infinite-scroll list ke liye. `source` optional — "message" ya "liveclass" se filter.
  Future<NotificationListResponse> getNotifications({
    int limit = 30,
    int offset = 0,
    String? source,
  }) async {
    final qp = <String, String>{
      'limit': '$limit',
      'offset': '$offset',
      if (source != null) 'source': source,
    };
    final uri = Uri.parse('${Api.baseUrl}/core/notifications/')
        .replace(queryParameters: qp);

    final res = await http
        .get(uri, headers: await _authHeaders())
        .timeout(kApiTimeout);

    if (res.statusCode != 200) {
      throw NotificationApiException('failed_to_load_notifications', res.statusCode);
    }
    return NotificationListResponse.fromJson(
      jsonDecode(res.body) as Map<String, dynamic>,
    );
  }

  /// POST core/notifications/{id}/mark-read/
  /// Fire-and-forget style call karo (UI already optimistically update ho chuki hogi).
  Future<void> markRead(int notificationId) async {
    final uri =
        Uri.parse('${Api.baseUrl}/core/notifications/$notificationId/mark-read/');
    final res = await http
        .post(uri, headers: await _authHeaders())
        .timeout(kApiTimeout);
    if (res.statusCode != 200 && res.statusCode != 204) {
      throw NotificationApiException('failed_to_mark_read', res.statusCode);
    }
  }

  /// POST core/notifications/mark-all-read/ -> {"marked_read": N}
  Future<int> markAllRead() async {
    final uri = Uri.parse('${Api.baseUrl}/core/notifications/mark-all-read/');
    final res = await http
        .post(uri, headers: await _authHeaders())
        .timeout(kApiTimeout);
    if (res.statusCode != 200) {
      throw NotificationApiException('failed_to_mark_all_read', res.statusCode);
    }
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    return body['marked_read'] as int? ?? 0;
  }

  /// GET core/notification-preferences/me/
  /// [Task 5] — pehle sirf header comment me documented tha, kabhi call
  /// nahi hota tha. Always the caller's own row (backend `get_or_create`
  /// karta hai — pehli baar hit karne pe bhi 404 nahi aata).
  Future<NotificationPreferences> getPreferences() async {
    final uri = Uri.parse('${Api.baseUrl}/core/notification-preferences/me/');
    final res = await http.get(uri, headers: await _authHeaders()).timeout(kApiTimeout);
    if (res.statusCode != 200) {
      throw NotificationApiException('failed_to_load_preferences', res.statusCode);
    }
    return NotificationPreferences.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  /// PATCH core/notification-preferences/me/ — partial update, sirf jo
  /// fields pass kiye wahi badalte hain. Poora updated object wapas
  /// aata hai, taaki UI ek hi call se refresh ho jaye.
  Future<NotificationPreferences> updatePreferences(Map<String, dynamic> patch) async {
    final uri = Uri.parse('${Api.baseUrl}/core/notification-preferences/me/');
    final res = await http
        .patch(uri, headers: await _authHeaders(), body: jsonEncode(patch))
        .timeout(kApiTimeout);
    if (res.statusCode != 200) {
      throw NotificationApiException('failed_to_update_preferences', res.statusCode);
    }
    return NotificationPreferences.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }
}

class NotificationApiException implements Exception {
  final String code;
  final int statusCode;
  NotificationApiException(this.code, this.statusCode);

  @override
  String toString() => 'NotificationApiException($code, status=$statusCode)';
}