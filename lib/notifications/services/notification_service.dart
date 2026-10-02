// lib/notifications/services/notification_service.dart
//
// Backend endpoints (`core` app — already production-ready, koi naya backend
// kaam nahi chahiye):
//   GET  core/notifications/?limit=&offset=&source=&category=  -> list (paginated)
//   GET  core/notifications/unread-count/?source=&category=    -> {"unread_count": N}
//   POST core/notifications/{id}/mark-read/
//   POST core/notifications/mark-all-read/                    -> {"marked_read": N}
//   GET  core/notification-preferences/me/
//   PATCH core/notification-preferences/me/
//   DELETE core/notifications/{id}/                           (N6)
//   POST/DELETE core/notification-mutes/{user_id}/            (N6)
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

  /// GET core/notifications/unread-count/?source=message|tuitionclass
  /// Bell-icon badge (home.dart) ke liye `source: 'tuitionclass'` bhejo — message
  /// notifications ab bell me nahi, Chats tab ke badge me dikhti hain (isliye
  /// yahan `source: 'message'` se wahi count Chats tab ke liye bhi milta hai).
  /// `source` omit karo to purana combined total milta hai.
  ///
  /// N4-FE — optional `category` (mentions | follows | classroom | tests | other),
  /// same filter as the list endpoint; stacks with `source`.
  Future<int> getUnreadCount({String? source, String? category}) async {
    final qp = <String, String>{
      if (source != null) 'source': source,
      if (category != null) 'category': category,
    };
    final uri = Uri.parse('${Api.baseUrl}/core/notifications/unread-count/').replace(
      queryParameters: qp.isEmpty ? null : qp,
    );
    final res = await http
        .get(uri, headers: await _authHeaders())
        .timeout(kApiTimeout);

    if (res.statusCode != 200) {
      throw NotificationApiException('failed_to_load_unread_count', res.statusCode);
    }
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    return body['unread_count'] as int? ?? 0;
  }

  /// GET core/notifications/?limit=&offset=&source=&category=
  /// Infinite-scroll list ke liye. `source` optional — "message" ya "tuitionclass" se filter.
  /// N4-FE — `category` optional — "mentions" | "follows" | "classroom" | "tests" | "other"
  /// (null = All). Backend unknown value ko ignore karta hai, 400 nahi deta.
  Future<NotificationListResponse> getNotifications({
    int limit = 30,
    int offset = 0,
    String? source,
    String? category,
  }) async {
    final qp = <String, String>{
      'limit': '$limit',
      'offset': '$offset',
      if (source != null) 'source': source,
      if (category != null) 'category': category,
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
  // ------------------------------------------------------------------
  // N6-FE — delete one notification / mute an account / mute a type.
  // ------------------------------------------------------------------

  /// DELETE core/notifications/{id}/ — own rows only (backend scopes the
  /// queryset to the caller). 404 is treated as success: the row is
  /// already gone (e.g. deleted from another device), which is exactly
  /// the state the caller wanted.
  Future<void> deleteNotification(int notificationId) async {
    final uri = Uri.parse('${Api.baseUrl}/core/notifications/$notificationId/');
    final res = await http.delete(uri, headers: await _authHeaders()).timeout(kApiTimeout);
    if (res.statusCode != 204 && res.statusCode != 200 && res.statusCode != 404) {
      throw NotificationApiException('failed_to_delete_notification', res.statusCode);
    }
  }

  /// POST core/notification-mutes/{user_id}/ — 201 (new) / 200 (already
  /// muted), idempotent. From now on that user's actions create no
  /// notification row for the caller. Existing rows are NOT removed.
  Future<void> muteUser(int userId) async {
    final uri = Uri.parse('${Api.baseUrl}/core/notification-mutes/$userId/');
    final res = await http.post(uri, headers: await _authHeaders()).timeout(kApiTimeout);
    if (res.statusCode != 200 && res.statusCode != 201) {
      throw NotificationApiException('failed_to_mute_user', res.statusCode);
    }
  }

  /// DELETE core/notification-mutes/{user_id}/ — 204, idempotent.
  Future<void> unmuteUser(int userId) async {
    final uri = Uri.parse('${Api.baseUrl}/core/notification-mutes/$userId/');
    final res = await http.delete(uri, headers: await _authHeaders()).timeout(kApiTimeout);
    if (res.statusCode != 204 && res.statusCode != 200) {
      throw NotificationApiException('failed_to_unmute_user', res.statusCode);
    }
  }

  /// "Turn off this type" — reuses the existing `muted_types` PATCH.
  /// `muted_types` is a whole-list replace on the backend, so this reads
  /// the current list first and adds/removes just [notifType] (a blind
  /// PATCH with one item would wipe the user's other muted types).
  /// Reads the raw JSON on purpose, so it does not depend on which
  /// fields NotificationPreferences exposes.
  Future<void> setTypeMuted(String notifType, {required bool muted}) async {
    final uri = Uri.parse('${Api.baseUrl}/core/notification-preferences/me/');
    final res = await http.get(uri, headers: await _authHeaders()).timeout(kApiTimeout);
    if (res.statusCode != 200) {
      throw NotificationApiException('failed_to_load_preferences', res.statusCode);
    }
    final raw = (jsonDecode(res.body) as Map<String, dynamic>)['muted_types'];
    final types = <String>{
      if (raw is List) ...raw.map((e) => e.toString()),
    };
    if (muted) {
      types.add(notifType);
    } else {
      types.remove(notifType);
    }
    await updatePreferences({'muted_types': types.toList()});
  }
}

class NotificationApiException implements Exception {
  final String code;
  final int statusCode;
  NotificationApiException(this.code, this.statusCode);

  @override
  String toString() => 'NotificationApiException($code, status=$statusCode)';
}