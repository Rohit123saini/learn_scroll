import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../../utils/api.dart';

class AuthService {
  // 🔧 CONFIRMED (login app ka urls.py + views.py dekh liye) — path ab
  // `TokenRefreshView` se match karta hai jo `login/urls.py` mein add kiya
  // gaya. ⚠️ Ek cheez abhi bhi confirm karni hai: top-level project
  // `urls.py` mein yeh `login` app kis prefix ke peeche `include()` hua
  // hai. Agar prefix `/login/` hai to yeh path sahi hai; koi aur prefix
  // (ya bilkul nahi) ho to sirf yeh ek constant update karna hoga.
  static const String _refreshEndpoint = '/login/auth/token/refresh/';

  /// Jab refresh-token khud expire/invalid nikle (user ko wapas login
  /// screen bhejna hai) — app root/main.dart isko ek baar subscribe kare,
  /// e.g.:
  ///   AuthService.onForceLogout = () {
  ///     navigatorKey.currentState?.pushNamedAndRemoveUntil('/login', (_) => false);
  ///   };
  static void Function()? onForceLogout;

  // ---- existing storage (unchanged behavior) ----

  static Future<String?> getToken() async {
    final prefs = await SharedPreferences.getInstance();
    String? token = prefs.getString("access");
    token ??= prefs.getString("access_token"); // fallback
    return token;
  }

  static Future<String?> getRefreshToken() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString("refresh");
  }

  static Future<String?> getUserId() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString("user_id");
  }

  /// 🔧 CHANGED — ab optional `refreshToken` bhi accept karta hai.
  /// Login screen (abhi tak unshared) ko yahan se refresh token bhi pass
  /// karna hoga — login response mein jo bhi field name ho (aksar
  /// `refresh`), wahi yahan `refreshToken:` mein daalo. Purana call
  /// (`saveToken(accessToken)`, bina refresh ke) bhi bilkul waise hi kaam
  /// karega — sirf refresh-token wala hissa skip ho jayega.
  static Future<void> saveToken(String token, {String? refreshToken}) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString("access", token);
    await prefs.setString("access_token", token); // double save, jaisa pehle tha
    if (refreshToken != null && refreshToken.isNotEmpty) {
      await prefs.setString("refresh", refreshToken);
    }
  }

  /// 🔥 NAYA — `login` app ka `views.py` dekhne ke baad pata chala: `Login`,
  /// `Signup`, aur `GoogleAuthView` teeno response NESTED shape mein tokens
  /// dete hain (`{"token": {"access": ..., "refresh": ...}}`), lekin
  /// `VerifyOTPView` (OTP se login) FLAT shape mein deta hai
  /// (`{"access": ..., "refresh": ...}`, koi "token" wrapper nahi).
  ///
  /// Har login/signup/otp/google screen mein alag-alag parsing likhne ke
  /// bajaye, bas jo bhi raw decoded JSON response mile wahi seedha yahan
  /// pass kar do — yeh khud dono shape try karke sahi wala nikaal lega:
  ///
  ///   final body = jsonDecode(response.body);
  ///   await AuthService.saveAuthResponse(body);
  ///
  /// Access token na mile (unexpected/error response) to kuch save nahi
  /// karta, chup-chaap return ho jaata hai — caller apna error-handling
  /// (jo status code pe already based hai) waise hi rakh sakta hai.
  static Future<void> saveAuthResponse(Map<String, dynamic> body) async {
    final tokenBlock = body['token'];
    String? access;
    String? refresh;

    if (tokenBlock is Map) {
      // Login / Signup / GoogleAuthView shape
      access = tokenBlock['access'] as String?;
      refresh = tokenBlock['refresh'] as String?;
    } else {
      // VerifyOTPView shape (flat)
      access = body['access'] as String?;
      refresh = body['refresh'] as String?;
    }

    if (access != null && access.isNotEmpty) {
      await saveToken(access, refreshToken: refresh);
    }
  }

  static Future<void> saveUserId(String id) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString("user_id", id);
  }

  static Future<void> logout() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.clear();
  }

  // ---- NAYA: expiry-aware token + refresh ----

  /// JWT ke 2nd segment (payload) ko base64url-decode karke `exp` (unix
  /// seconds) nikalta hai. Koi extra package (jwt_decode etc.) add nahi
  /// karna pada — manual decode kaafi hai sirf `exp` padhne ke liye.
  static int? _getExpiry(String token) {
    try {
      final parts = token.split('.');
      if (parts.length != 3) return null;
      String payload = parts[1].replaceAll('-', '+').replaceAll('_', '/');
      switch (payload.length % 4) {
        case 2:
          payload += '==';
          break;
        case 3:
          payload += '=';
          break;
      }
      final decoded = utf8.decode(base64.decode(payload));
      final map = jsonDecode(decoded) as Map<String, dynamic>;
      return map['exp'] as int?;
    } catch (_) {
      return null;
    }
  }

  static bool _isExpiredOrNear(String token, {int bufferSeconds = 30}) {
    final exp = _getExpiry(token);
    // decode fail ho jaye (malformed token) to purana behavior maano
    // (valid) — warna yahan crash-loop create ho sakta hai reconnect ke
    // saath milke.
    if (exp == null) return false;
    final nowSec = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    return nowSec >= (exp - bufferSeconds);
  }

  // Ek time pe ek hi refresh network-call chale. `chat_socket_service` aur
  // `inbox_socket_service` dono ek saath expire hue token pe reconnect try
  // kar sakte hain — duplicate refresh calls (aur possibly do alag naye
  // access tokens race karke) rokne ke liye single in-flight future share
  // karte hain.
  static Future<String?>? _refreshInFlight;

  /// 🔥 YEH method use karo har jagah (REST calls + WS reconnect donon
  /// jagah) — `getToken()` nahi. Expiry check karta hai, zaroorat pade to
  /// khud refresh kar leta hai, aur naya valid access token return karta
  /// hai. Refresh fail ho (refresh-token khud invalid) to `null` return
  /// karega AUR `onForceLogout` bhi trigger karega.
  static Future<String?> getValidToken() async {
    final current = await getToken();
    if (current == null) return null;
    if (!_isExpiredOrNear(current)) return current;

    if (_refreshInFlight != null) return _refreshInFlight;
    _refreshInFlight = _doRefresh();
    final result = await _refreshInFlight;
    _refreshInFlight = null;
    return result;
  }

  static Future<String?> _doRefresh() async {
    final refresh = await getRefreshToken();
    if (refresh == null || refresh.isEmpty) {
      // refresh token kabhi save hi nahi hua (purana login flow) — force
      // logout, kyunki access token refresh karne ka koi tareeka nahi hai.
      onForceLogout?.call();
      return null;
    }

    try {
      final res = await http.post(
        Uri.parse('${Api.baseUrl}$_refreshEndpoint'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'refresh': refresh}),
      );

      if (res.statusCode == 200) {
        final body = jsonDecode(res.body) as Map<String, dynamic>;
        final newAccess = body['access'] as String?;
        if (newAccess == null) return null;
        await saveToken(newAccess); // refresh token same rehta hai (rotation on ho to yahan update bhi karna hoga)
        return newAccess;
      }

      if (res.statusCode == 401 || res.statusCode == 403) {
        // refresh token khud expire/blacklisted/invalid — ab retry se
        // kuch nahi hoga, seedha login pe bhejo.
        onForceLogout?.call();
        return null;
      }

      // koi aur server error (500 etc.) — force-logout mat karo, ho
      // sakta hai temporary ho; caller (socket reconnect loop) khud retry
      // karega apne backoff ke saath.
      return null;
    } catch (_) {
      // network error — same, temporary maano, force-logout mat karo.
      return null;
    }
  }
}