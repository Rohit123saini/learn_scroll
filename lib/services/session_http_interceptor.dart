import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'session_service.dart';

// ============================================================
// SLIDING-EXPIRY SESSION — GLOBAL 401 INTERCEPTOR
//
// Backend (login/authentication.py — SlidingSessionAuthentication) ab
// koi bhi authenticated request 401 de sakta hai body
// {"detail": "...", "code": "TOKEN_EXPIRED"} ke saath, jab session
// 10 din inactivity ke baad expire ho chuki ho. Isko catch karne ke
// liye HAR http call site (har service/screen) me alag-alag check
// likhna duplicate + error-prone hota — is file ka poora point yehi
// hai ki yeh check EK HI jagah ho.
//
// `http` package top-level functions (http.get/http.post/...) internally
// ek default Client use karte hain jo `runWithClient()` (main.dart me
// wire kiya gaya) se override ho jaata hai — matlab is poori app ke
// EXISTING har http.get/post call (login ke alawa doosre services —
// message, post, testseries, wagera — jo already raw http.* use karte
// hain) BINA kisi call-site badle is interceptor se guzrenge.
//
// Successful (non-401) responses ko yeh jaan-boojh kar bilkul chhoo nahi
// raha — backend khud "activity = renew" handle kar raha hai
// (AuthToken.touch() server-side, har authenticated request par), isliye
// client ko kuch track/update karne ki zaroorat nahi.
// ============================================================

class SessionAwareHttpClient extends http.BaseClient {
  SessionAwareHttpClient(this._inner);

  final http.Client _inner;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final streamed = await _inner.send(request);

    if (streamed.statusCode != 401) return streamed;

    // Body ek hi baar padhi ja sakti hai — buffer karke check karo, phir
    // caller (http.get/post/... ya koi bhi raw http.Client user) ke liye
    // equivalent StreamedResponse wapas bana do taaki unka normal
    // response-reading code (jo already 401 pe apna Exception(...) throw
    // karta hai — jaisa api_service.dart me sab jagah hai) waise hi kaam
    // karta rahe. Yeh interceptor sirf EXTRA signal nikalta hai, kisi ka
    // existing error-handling nahi todta.
    final response = await http.Response.fromStream(streamed);
    _checkForExpiredSession(response);

    return http.StreamedResponse(
      Stream.value(response.bodyBytes),
      response.statusCode,
      contentLength: response.bodyBytes.length,
      request: streamed.request,
      headers: streamed.headers,
      isRedirect: streamed.isRedirect,
      persistentConnection: streamed.persistentConnection,
      reasonPhrase: streamed.reasonPhrase,
    );
  }

  void _checkForExpiredSession(http.Response response) {
    try {
      final body = jsonDecode(response.body);
      if (body is Map && body['code'] == 'TOKEN_EXPIRED') {
        // home.dart ka existing "Task 8" flow (_runSessionExpiryCountdown)
        // yahi se pick karta hai: 3 sec loader/snackbar -> AuthService.
        // logout() (local session clear) -> '/login' pe redirect. Yahan
        // seedha navigate NAHI karna — koi BuildContext nahi hai is layer
        // pe, aur spec ke hisaab se turant redirect bhi nahi karna.
        SessionService.markExpired();
      }
    } catch (_) {
      // 401 ka body JSON nahi tha (proxy/gateway error page, khaali body,
      // etc.) — koi "TOKEN_EXPIRED" signal nahi mila, chup-chaap ignore.
      // Plain "invalid/missing token" wala normal 401 abhi bhi waise hi
      // upar caller tak jaata hai jaisa pehle jaata tha.
    }
  }

  @override
  void close() => _inner.close();
}

/// main.dart me ek baar call karo, poori app ke around:
///
///   void main() {
///     runAppWithSessionAwareClient(() async {
///       WidgetsFlutterBinding.ensureInitialized();
///       ... existing async setup ...
///       runApp(const MyApp());
///     });
///   }
///
/// Isse existing main() ka poora async body isi zone ke andar chalta hai,
/// isliye us body ke andar hone wala HAR http.get/post/... call
/// (AuthService.checkSessionAlive() samet) automatically is client se
/// guzarta hai — main() restructure karne ke alawa kahin aur kuch badalne
/// ki zaroorat nahi.
void runAppWithSessionAwareClient(FutureOr<void> Function() body) {
  http.runWithClient(body, () => SessionAwareHttpClient(http.Client()));
}