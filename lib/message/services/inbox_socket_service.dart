// message/services/inbox_socket_service.dart
//
// Tere `InboxConsumer` (consumers.py) se connect karta hai:
//   ws/inbox/?token=<JWT>
//
// `ChatSocketService` se ALAG hai: wo per-conversation hai (sirf tab
// connect hota hai jab tu kisi specific chat ke andar jaata hai). Ye
// GLOBAL hai — singleton, poori app session me ek hi baar connect hota
// hai aur connected rehta hai chahe koi bhi screen khuli ho. Isi se
// `ConversationsScreen` ko pata chalta hai ki kisi bhi conversation me
// naya message aaya, bina us chat ke andar gaye.
//
// Best jagah connect() call karne ki: login success hone ke turant baad
// (jahan bhi tera auth flow hai) — abhi ke liye ConversationsScreen bhi
// `initState()` me isse connect kar deti hai (idempotent hai, dobara call
// karne se dusra socket nahi khulta), taaki kam se kam wahan turant kaam
// karne lage.
//
// pubspec.yaml me ye dependency chahiye (ChatSocketService jaisi hi):
//   web_socket_channel: ^2.4.0
//
// 🔧 FIX (yeh session) — pehle `getToken()` (stale/expired ho sakta tha)
// use karta tha aur reconnect FIXED 4s pe hota tha. Agar token expire ho
// chuka ho: server 4001 de ke turant close karega -> onDone -> phir 4s
// baad wahi expired token -> phir 4001 -> infinite tight loop, silently,
// forever — UI ko kabhi pata nahi chalta ki reconnect fail ho raha hai.
// Ab `AuthService.getValidToken()` (expiry-check + auto-refresh) use
// karta hai, aur backoff bhi capped hai taaki persistent-failure case me
// tight loop na bane.

import 'dart:async';
import 'dart:convert';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../utils/api.dart';
import '../../services/auth_service.dart';

class InboxSocketService {
  InboxSocketService._internal();
  static final InboxSocketService instance = InboxSocketService._internal();

  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  final _eventController = StreamController<Map<String, dynamic>>.broadcast();
  bool _isConnected = false;
  bool _isConnecting = false;

  // 🔥 NAYA — backoff state
  Timer? _reconnectTimer;
  int _reconnectAttempts = 0;

  /// Har `inbox_update` event yahan se milta hai.
  Stream<Map<String, dynamic>> get events => _eventController.stream;
  bool get isConnected => _isConnected;

  String _wsBaseUrl() {
    final base = Api.baseUrl;
    if (base.startsWith("https://")) return base.replaceFirst("https://", "wss://");
    if (base.startsWith("http://")) return base.replaceFirst("http://", "ws://");
    return base;
  }

  /// Idempotent — already connected/connecting ho to kuch nahi karta.
  /// Login ke turant baad ek baar call kar do (best), ya jahan bhi
  /// convenient ho — safe hai bar-bar call karna.
  Future<void> connect() async {
    if (_isConnected || _isConnecting) return;
    _isConnecting = true;

    try {
      // 🔧 CHANGED — getToken() ki jagah getValidToken(): expiry check
      // karta hai, zaroorat pade to refresh bhi karta hai.
      final token = await AuthService.getValidToken();
      if (token == null || token.isEmpty) {
        _isConnecting = false;
        // Login hua hi nahi ho abhi (getToken null), YA refresh fail hua.
        // Dono case mein thoda ruk ke retry karna theek hai — agar
        // refresh-token khud invalid tha to `AuthService` already
        // `onForceLogout` fire kar chuka hoga, aur is service ko
        // `disconnect()` se rok diya jayega (logout flow mein).
        _scheduleReconnect();
        return;
      }

      final uri = Uri.parse("${_wsBaseUrl()}/ws/inbox/?token=$token");
      _channel = WebSocketChannel.connect(uri);
      _isConnected = true;
      _isConnecting = false;
      _reconnectAttempts = 0; // successful connect — backoff reset

      _sub = _channel!.stream.listen(
        (raw) {
          try {
            final data = jsonDecode(raw) as Map<String, dynamic>;
            _eventController.add(data);
          } catch (_) {
            // malformed frame, ignore
          }
        },
        onDone: () {
          _isConnected = false;
          _scheduleReconnect();
        },
        onError: (e) {
          _isConnected = false;
          _scheduleReconnect();
        },
      );
    } catch (_) {
      _isConnecting = false;
      _isConnected = false;
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    // Poori app session me alive rehna hai — connection drop (network
    // blip, server restart, ab expired-token-refresh-fail bhi) hone par
    // khud reconnect kare, lekin ab CAPPED backoff ke saath (pehle fixed
    // 4s tha — persistent-failure case mein tight infinite loop ban
    // jaata tha).
    _reconnectTimer?.cancel();
    _reconnectAttempts++;
    final delaySeconds = (4 * _reconnectAttempts).clamp(4, 60);
    _reconnectTimer = Timer(Duration(seconds: delaySeconds), connect);
  }

  /// Sirf logout pe call karo — normal screen navigation pe NAHI (ye
  /// jaan-boojh kar app-wide/global hai, kisi ek screen se bandha nahi).
  void disconnect() {
    _reconnectTimer?.cancel();
    _reconnectAttempts = 0;
    _isConnected = false;
    _isConnecting = false;
    _sub?.cancel();
    _channel?.sink.close();
  }
}