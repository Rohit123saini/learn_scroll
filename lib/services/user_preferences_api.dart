// lib/services/user_preferences_api.dart
//
// ============================================================
// SETTINGS-PERSISTENCE BUG FIX — root cause #1.
//
// Backend `user_profile.UserPreference` (model + serializer + view) was
// already fully production-ready — `GET/PATCH /profile/preferences/me/`
// (`UserPreferenceView`, `user_profile/urls.py`) returns/accepts
// `{"theme": "light"|"dark"|"system", "language": "en"|"hi"|...}` and
// get-or-creates the row so it never 404s. Nothing on the backend needed
// to change.
//
// The actual bug was entirely on this side: `theme_service.dart` and
// `language_service.dart` only ever read/wrote `SharedPreferences` — a
// purely on-device value. That survives a normal app restart just fine
// (which is why it could look "half-working" in casual testing), but it
// silently resets to the device default on a reinstall or a new device,
// and two devices on the same account never agree — which is what the
// user report actually meant by "settings don't persist".
//
// This file is the one shared place both services call into (rather than
// each hand-rolling its own identical http.get/patch — same
// don't-duplicate-identical-logic reasoning `user_display.dart`-style
// shared helpers use elsewhere in this app) so `theme_service.dart` and
// `language_service.dart` stay in sync with exactly one backend contract.
//
// Deliberately fails silent everywhere: a cross-device sync hiccup should
// never block, delay, or revert a purely local theme/language change —
// the tap already took effect on THIS device the instant it happened,
// regardless of what the network says.
// ============================================================

import 'dart:convert';

import 'package:http/http.dart' as http;

import '../utils/api.dart';
import 'auth_service.dart';

class UserPreferencesApi {
  UserPreferencesApi._();

  static const Duration _timeout = Duration(seconds: 10);
  static const String _endpoint = '/profile/preferences/me/';

  /// Returns `{"theme": "...", "language": "...", "updated_at": "..."}` —
  /// or `null` for absolutely any reason it couldn't (logged out, offline,
  /// server error, unexpected shape). Callers treat `null` as "nothing to
  /// sync right now", never as something to surface to the user — the
  /// on-device value they already have keeps working either way.
  static Future<Map<String, dynamic>?> fetch() async {
    try {
      final token = await AuthService.getValidToken();
      if (token == null) return null; // logged out — nothing to sync yet

      final res = await http.get(
        Uri.parse('${Api.baseUrl}$_endpoint'),
        headers: {'Authorization': 'Bearer $token'},
      ).timeout(_timeout);

      if (res.statusCode != 200) return null;
      final body = jsonDecode(res.body);
      if (body is! Map<String, dynamic>) return null;
      final data = body['data'];
      return data is Map<String, dynamic> ? data : null;
    } catch (_) {
      return null;
    }
  }

  /// Fire-and-forget by design — `theme_service.dart`/`language_service.dart`
  /// already apply the change locally (and paint it) before this is ever
  /// called, so callers don't need to `await` this to stay responsive.
  /// Partial body — only send the field(s) actually changing.
  static Future<void> update(Map<String, dynamic> patch) async {
    try {
      final token = await AuthService.getValidToken();
      if (token == null) return;

      await http.patch(
        Uri.parse('${Api.baseUrl}$_endpoint'),
        headers: {
          'Authorization': 'Bearer $token',
          'Content-Type': 'application/json',
        },
        body: jsonEncode(patch),
      ).timeout(_timeout);
    } catch (_) {
      // Best-effort only — see file header. Local state is already correct.
    }
  }
}
