// message/services/chat_socket_service.dart
//
// Tere `ChatConsumer` (consumers.py) se connect karta hai:
//   ws/chat/<conversation_id>/?token=<JWT>
//
// Client -> Server events: message, typing, read, delete, reaction
// Server -> Client events: message(chat_message), typing, read, delete,
//                           reaction, presence, error
//
// pubspec.yaml me ye dependency chahiye:
//   web_socket_channel: ^2.4.0

import 'dart:async';
import 'dart:convert';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../utils/api.dart';
import '../../services/auth_service.dart';

class ChatSocketService {
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  final _eventController = StreamController<Map<String, dynamic>>.broadcast();
  bool _isConnected = false;

  /// Har incoming server event yahan se milta hai — {"type": "...", ...}
  Stream<Map<String, dynamic>> get events => _eventController.stream;
  bool get isConnected => _isConnected;

  /// `Api.baseUrl` http(s):// hai, WebSocket ke liye ws(s):// chahiye.
  String _wsBaseUrl() {
    final base = Api.baseUrl;
    if (base.startsWith("https://")) return base.replaceFirst("https://", "wss://");
    if (base.startsWith("http://")) return base.replaceFirst("http://", "ws://");
    return base;
  }

  Future<void> connect(String conversationId) async {
    final token = await AuthService.getToken();
    final uri = Uri.parse(
        "${_wsBaseUrl()}/ws/chat/$conversationId/?token=${token ?? ''}");

    _channel = WebSocketChannel.connect(uri);
    _isConnected = true;

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
      },
      onError: (e) {
        _isConnected = false;
        _eventController.add({'type': 'error', 'code': 'socket_error', 'message': e.toString()});
      },
    );
  }

  void _send(Map<String, dynamic> payload) {
    if (_channel == null || !_isConnected) return;
    _channel!.sink.add(jsonEncode(payload));
  }

  /// Naya message bhejo. `clientId` offline-retry idempotency ke liye —
  /// har naye message ke liye unique id do (e.g. uuid ya timestamp).
  ///
  /// 🔧 FIX (backend mismatch) — pehle sirf plain-text jaata tha, isliye
  /// media messages ko REST fallback pe bhejna padta tha. Backend ka
  /// `ChatConsumer.handle_new_message`/`save_message` ab `file_url`,
  /// `file_urls`, `thumbnail_url`, `meta` bhi read karta hai (WS media
  /// send fix, backend doc §8) — isliye ye 4 optional params add kiye.
  /// Media `message_type` ke liye backend `file_url`/`file_urls` me se
  /// kam-se-kam ek ke bina 400 dega (REST `MessageCreateSerializer` jaisa
  /// hi validation) — pehle Dio se upload karke URL le lo, phir yahan bhejo.
  void sendMessage({
    required String text,
    String messageType = 'text',
    required String clientId,
    String? replyTo,
    String? fileUrl,
    List<String>? fileUrls,
    String? thumbnailUrl,
    Map<String, dynamic>? meta,
  }) {
    _send({
      'type': 'message',
      'client_id': clientId,
      'message_type': messageType,
      'text': text,
      'reply_to': replyTo,
      if (fileUrl != null) 'file_url': fileUrl,
      if (fileUrls != null) 'file_urls': fileUrls,
      if (thumbnailUrl != null) 'thumbnail_url': thumbnailUrl,
      if (meta != null) 'meta': meta,
    });
  }

  /// 🔧 NAYA (backend mismatch fix) — backend `ChatConsumer` client→server
  /// `pin` event accept karta hai (`{message_id, pin: bool}`), frontend me
  /// pehle sirf REST se pin/unpin hota tha. Ab socket se bhi bhej sakte ho
  /// (optional — REST call already kaam karta hai, isko use karna zaroori
  /// nahi, par server → `pin_event` broadcast dono se hi trigger hota hai).
  void sendPin(String messageId, bool pin) {
    _send({'type': 'pin', 'message_id': messageId, 'pin': pin});
  }

  void sendTyping(bool isTyping) {
    _send({'type': 'typing', 'is_typing': isTyping});
  }

  void sendReadReceipt(String messageId) {
    _send({'type': 'read', 'message_id': messageId});
  }

  void sendDelete(String messageId, {bool forEveryone = false}) {
    _send({'type': 'delete', 'message_id': messageId, 'for_everyone': forEveryone});
  }

  void sendReaction(String messageId, String emoji) {
    _send({'type': 'reaction', 'message_id': messageId, 'emoji': emoji});
  }

  // 🔥 NAYA — Study Room ke saare realtime events (whiteboard strokes,
  // shapes, text, sticky notes, floating windows, timer sync) isi ek
  // generic passthrough se jaate hain. Backend ke `ChatConsumer` me
  // handling add karni hogi: jab bhi `{"type": "study_room_event", ...}`
  // aaye, usko as-is (action + data samet) baaki sab connected
  // participants ko broadcast kar do — bilkul jaise `typing`/`reaction`
  // events already broadcast hote hain.
  void sendStudyRoomEvent(String action, Map<String, dynamic> data) {
    _send({'type': 'study_room_event', 'action': action, 'data': data});
  }

  void disconnect() {
    _isConnected = false;
    _sub?.cancel();
    _channel?.sink.close();
  }

  void dispose() {
    disconnect();
    _eventController.close();
  }
}