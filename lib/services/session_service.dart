import 'package:flutter/material.dart';

// ============================================================
// TASK 8 — SESSION SERVICE
//
// Ek chhota global "session expire ho gaya" signal. Yahan koi navigation
// logic NAHI hai — sirf state. Kyun:
//   • AuthService._doRefresh() jab dekhta hai ki refresh-token khud
//     invalid/expire ho chuka hai, to wo AuthService.onForceLogout() call
//     karta hai. Wo callback ek pure-Dart service ke andar chalta hai —
//     uske paas koi BuildContext nahi hota, isliye wo seedha navigate nahi
//     kar sakta (aur spec ke hisaab se karna bhi nahi chahiye: home screen
//     ko 3 second dikhna hai pehle).
//   • Isliye onForceLogout sirf `homeSessionExpiredNotifier.value = true`
//     set karta hai (main.dart, Task 8.2), aur HomeScreen us flag ko sunke
//     banner + 3 sec delay + redirect handle karta hai (home.dart, Task 8.3).
//
// Ye file jaan-boojh kar sirf flutter/material pe depend karti hai — na
// AuthService pe, na kisi screen pe — taaki main.dart / home.dart /
// auth_service.dart teeno ise bina circular import ke import kar sakein.
// ============================================================

/// App-wide navigator key.
///
/// 🔧 IMPORTANT (main.dart): agar aapke main.dart me pehle se ek
/// `navigatorKey` global declare hai, to usko HATA do aur yahan wali ko
/// import karo — ya phir home.dart ka import badal ke apne wale pe point
/// kar do. DO alag-alag GlobalKey banane par `navigatorKey.currentState`
/// null aayega (kyunki MaterialApp sirf ek key se attach hoga) aur redirect
/// chupchaap fail ho jaayega.
///
/// MaterialApp me isko aise pass karna zaroori hai:
///   MaterialApp(
///     navigatorKey: navigatorKey,
///     routes: { '/login': (_) => const LoginScreen(), ... },
///   )
final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

/// `true` ho jaata hai jab session force-logout ho chuka hai (refresh token
/// mar gaya). HomeScreen isko sunta hai — dekho `_HomeScreenState.
/// _onSessionExpired()` in home.dart.
///
/// Logout + redirect complete hone ke baad wapas `false` set kar diya jaata
/// hai, taaki agle login ke baad ye flag purani state leke na baithe rahe.
final ValueNotifier<bool> homeSessionExpiredNotifier = ValueNotifier<bool>(false);

/// Chhote helpers — taaki call-sites pe `.value = true` likhna na pade aur
/// intent saaf rahe.
class SessionService {
  SessionService._();

  /// AuthService.onForceLogout se call hota hai (main.dart me subscribe).
  /// Idempotent hai: pehle se `true` ho to dobara notify nahi karta, warna
  /// HomeScreen ka listener multiple baar fire ho sakta hai.
  static void markExpired() {
    if (homeSessionExpiredNotifier.value) return;
    homeSessionExpiredNotifier.value = true;
  }

  /// Redirect ke baad (ya fresh login ke baad) flag clear karne ke liye.
  static void reset() {
    homeSessionExpiredNotifier.value = false;
  }
}