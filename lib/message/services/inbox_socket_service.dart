// message/services/inbox_socket_service.dart
//
// 🔥 NAYA — Tere naye `InboxConsumer` (consumers.py) se connect karta hai:
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
      final token = await AuthService.getToken();
      if (token == null || token.isEmpty) {
        _isConnecting = false;
        return; // login nahi hua abhi, baad me retry karo
      }

      final uri = Uri.parse("${_wsBaseUrl()}/ws/inbox/?token=$token");
      _channel = WebSocketChannel.connect(uri);
      _isConnected = true;
      _isConnecting = false;

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

  Timer? _reconnectTimer;
  void _scheduleReconnect() {
    // Poori app session me alive rehna hai — connection drop (network
    // blip, server restart) hone par khud reconnect kar le.
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 4), connect);
  }

  /// Sirf logout pe call karo — normal screen navigation pe NAHI (ye
  /// jaan-boojh kar app-wide/global hai, kisi ek screen se bandha nahi).
  void disconnect() {
    _reconnectTimer?.cancel();
    _isConnected = false;
    _isConnecting = false;
    _sub?.cancel();
    _channel?.sink.close();
  }
}