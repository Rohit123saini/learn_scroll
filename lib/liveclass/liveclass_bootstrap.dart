// ============================================================
// LIVECLASS — APP-WIDE WIRING
//
// Every liveclass screen takes a `LiveClassApi` (and the shell a settings
// controller) as constructor arguments; before this file nothing in the real
// app ever built one — only `main_example.dart` did — so `home.dart` could not
// open a classroom. This is the single place the app-wide instances live:
//
//   * `LiveClass.api`      → base `${Api.baseUrl}/liveclass` (matches the
//                            backend's `path("liveclass/", …)` mount) with the
//                            same auto-refreshing JWT the rest of the app uses.
//   * `LiveClass.settings` → bridge to LanguageService / ThemeService.
//   * `LiveClass.userSocket()` → per-user realtime channel (join-request
//                            decisions, staff promotion).
// ============================================================

import 'api/liveclass_api.dart';
import 'api/liveclass_socket.dart';
import 'state/liveclass_settings.dart';
import '../services/auth_service.dart';
import '../utils/api.dart';

class LiveClass {
  LiveClass._();

  static final LiveClassApi api = LiveClassApi(
    baseUrl: '${Api.baseUrl}/liveclass',
    getToken: AuthService.getValidToken,
  );

  static final LiveClassSettingsController settings = LiveClassSettingsController();

  static LiveClassSocket? _user;

  /// Shared `ws/liveclass/user/` socket (created + connected on first use).
  /// Callers only `listen` to `.events`; they must NOT `close()` it.
  static LiveClassSocket userSocket() {
    final s = _user ??= LiveClassSocket.user();
    if (!s.connected) s.connect();
    return s;
  }

  /// Call on logout so the next account doesn't inherit the socket.
  static Future<void> reset() async {
    final s = _user;
    _user = null;
    await s?.close();
  }
}
