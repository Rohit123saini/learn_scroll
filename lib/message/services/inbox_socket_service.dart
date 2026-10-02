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
import 'package:flutter/foundation.dart'; // ValueNotifier — requests badge
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../utils/api.dart';
import '../../services/auth_service.dart';
import 'message_api_service.dart'; // 🔥 NAYA (M1-FE) — requests count sync

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
  DateTime? _connectedAt; // N10-FE — connection kitni der zinda raha (backoff reset ke liye)

  // 🔥 NAYA (N10-FE) — live bell badge. Backend `notification_badge`
  // event ({"type": "notification_badge", "unread_count": N}) har
  // Notification create/read/delete pe aata hai. Ye stream use SIGNAL ki
  // tarah emit karta hai: listener (home bell, NotificationsScreen) apna
  // REST refetch kare. `unread_count` payload TOTAL hai (sab sources), jabki
  // bell sirf `source=tuitionclass` dikhata hai aur Chats badge
  // `source=message` — isliye payload ko seedha bell me mat daalo.
  //
  // Har successful (re)connect pe ek synthetic event (`synthetic: true`)
  // bhi aata hai: disconnect ke beech chhute events ka catch-up.
  final _notificationSync = StreamController<Map<String, dynamic>>.broadcast();
  Stream<Map<String, dynamic>> get notificationSync => _notificationSync.stream;

  // 🔥 NAYA (M1-FE) — "Message requests (N)" ka live count. Singleton me
  // rakha hai taaki ConversationsScreen / MessageRequestsScreen dono ek hi
  // source dekhein. Backend `message_request` event HAR naye message pe aata
  // hai (sirf pehle pe nahi), isliye conversation_id se dedupe karte hain.
  final ValueNotifier<int> pendingRequestCount = ValueNotifier<int>(0);
  final Set<String> _pendingRequestIds = {};
  // true = `_pendingRequestIds` me SAARI pending requests hain (page 1 me
  // sab aa gayi). false = aur bhi hain jo yahan nahi -> unknown id pe
  // andaza lagane ki jagah API se dobara sync karo.
  bool _requestIdsComplete = false;

  /// API se page 1 laake count + ids sync karo. Fail ho to chup-chaap ignore.
  Future<void> refreshRequestCount() async {
    try {
      applyRequestsPage(await MessageApiService.getMessageRequests());
    } catch (_) {}
  }

  /// `MessageRequestsScreen` apna page 1 load karke yahi call karti hai —
  /// dobara network call ki zaroorat nahi.
  void applyRequestsPage(MessageRequestsPage page) {
    _pendingRequestIds
      ..clear()
      ..addAll(page.items.map((c) => c.id));
    _requestIdsComplete = !page.hasMore;
    pendingRequestCount.value = page.total;
  }

  void _trackRequestEvent(Map<String, dynamic> data) {
    final type = data['type'];
    if (type != 'message_request' && type != 'message_request_resolved') return;
    final id = data['conversation_id']?.toString();
    if (id == null) return;

    if (type == 'message_request') {
      if (_pendingRequestIds.contains(id)) return; // isi request ka dusra message
      if (_requestIdsComplete) {
        _pendingRequestIds.add(id);
        pendingRequestCount.value = pendingRequestCount.value + 1;
      } else {
        refreshRequestCount();
      }
    } else {
      if (_pendingRequestIds.remove(id)) {
        final next = pendingRequestCount.value - 1;
        pendingRequestCount.value = next < 0 ? 0 : next;
      } else if (!_requestIdsComplete) {
        refreshRequestCount();
      }
    }
  }

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
      // N10-FE — backoff reset ab yahan NAHI: server auth fail pe accept ke
      // turant baad 4001 se close kar deta hai, to yahan reset karne se
      // expired-token loop hamesha 4s pe wapas aa jaata tha. Reset tabhi
      // hota hai jab connection >= 10s zinda rahe (`_noteDisconnect`).
      _connectedAt = DateTime.now();
      _notificationSync.add({'type': 'notification_badge', 'synthetic': true});
      refreshRequestCount(); // 🔥 NAYA (M1-FE) — disconnect ke beech chhute events ka catch-up

      _sub = _channel!.stream.listen(
        (raw) {
          try {
            final data = jsonDecode(raw) as Map<String, dynamic>;
            _trackRequestEvent(data); // 🔥 NAYA (M1-FE)
            if (data['type'] == 'notification_badge') {
              _notificationSync.add(data); // 🔥 NAYA (N10-FE)
            }
            _eventController.add(data);
          } catch (_) {
            // malformed frame, ignore
          }
        },
        onDone: () {
          _isConnected = false;
          _noteDisconnect();
          _scheduleReconnect();
        },
        onError: (e) {
          _isConnected = false;
          _noteDisconnect();
          _scheduleReconnect();
        },
      );
    } catch (_) {
      _isConnecting = false;
      _isConnected = false;
      _scheduleReconnect();
    }
  }

  // N10-FE — stable connection (>=10s) ke baad hi backoff reset.
  void _noteDisconnect() {
    final at = _connectedAt;
    _connectedAt = null;
    if (at != null && DateTime.now().difference(at) >= const Duration(seconds: 10)) {
      _reconnectAttempts = 0;
    }
  }

  /// N10-FE — app resume pe: OS ne background me socket maar diya ho sakta
  /// hai (aur `_isConnected` abhi bhi true dikhe), ya reconnect backoff ke
  /// intezaar me ho. Dead/waiting ho to turant reconnect; connected ho to
  /// no-op. Logged-out (disconnect() ke baad) me bhi connect() token na
  /// milne par khud retry-loop me jaata hai, isliye caller ko login-state
  /// check karke hi call karna chahiye.
  void reconnectNow() {
    if (_isConnected || _isConnecting) return;
    _reconnectTimer?.cancel();
    _reconnectAttempts = 0;
    connect();
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
    _pendingRequestIds.clear(); // 🔥 NAYA (M1-FE) — logout pe badge reset
    _requestIdsComplete = false;
    pendingRequestCount.value = 0;
    _sub?.cancel();
    _channel?.sink.close();
  }
}