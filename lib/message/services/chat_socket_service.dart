// // message/screens/chat_screen.dart
// import 'package:flutter/material.dart';
// import 'package:cached_network_image/cached_network_image.dart';

// import '../models/message_models.dart';
// import '../services/message_api_service.dart';
// import '../services/chat_socket_service.dart';
// import '../../services/auth_service.dart';

// class ChatScreen extends StatefulWidget {
//   final ConversationModel conversation;
//   const ChatScreen({super.key, required this.conversation});

//   @override
//   State<ChatScreen> createState() => _ChatScreenState();
// }

// class _ChatScreenState extends State<ChatScreen> {
//   final ChatSocketService _socket = ChatSocketService();
//   final TextEditingController _textController = TextEditingController();
//   final ScrollController _scrollController = ScrollController();

//   List<MessageModel> _messages = []; // index 0 = sabse purana (chat bottom pe latest)
//   bool _isLoading = true;
//   bool _isSocketConnected = false;
//   bool _otherTyping = false;
//   String? _myUserId;
//   int _clientIdCounter = 0;

//   @override
//   void initState() {
//     super.initState();
//     _init();
//   }

//   Future<void> _init() async {
//     _myUserId = await AuthService.getUserId();
//     await _loadHistory();
//     await _connectSocket();
//     await MessageApiService.readAll(widget.conversation.id);
//   }

//   Future<void> _loadHistory() async {
//     try {
//       final data = await MessageApiService.getMessages(widget.conversation.id, page: 1);
//       // Backend `-created_at` order me deta hai (BaseModel.Meta.ordering) —
//       // UI ke liye reverse karke oldest-first bana lo.
//       if (mounted) {
//         setState(() {
//           _messages = data.reversed.toList();
//           _isLoading = false;
//         });
//         _scrollToBottom();
//       }
//     } catch (e) {
//       if (mounted) {
//         setState(() => _isLoading = false);
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(content: Text("Failed to load messages: $e")),
//         );
//       }
//     }
//   }

//   Future<void> _connectSocket() async {
//     await _socket.connect(widget.conversation.id);
//     setState(() => _isSocketConnected = true);
//     _socket.events.listen(_handleSocketEvent);
//   }

//   void _handleSocketEvent(Map<String, dynamic> event) {
//     final type = event['type'];
//     switch (type) {
//       case 'chat_message':
//         _onIncomingMessage(event);
//         break;
//       case 'typing':
//         if (event['user_id']?.toString() != _myUserId) {
//           setState(() => _otherTyping = event['is_typing'] == true);
//         }
//         break;
//       case 'read':
//         // sender ke liye "seen" tick update karna ho to yahan handle karo
//         break;
//       case 'delete':
//         _onDeleteEvent(event);
//         break;
//       case 'reaction':
//         _onReactionEvent(event);
//         break;
//       case 'presence':
//         // online/offline indicator update yahan
//         break;
//       case 'error':
//         if (mounted) {
//           ScaffoldMessenger.of(context).showSnackBar(
//             SnackBar(content: Text(event['message']?.toString() ?? 'Error')),
//           );
//         }
//         break;
//     }
//   }

//   void _onIncomingMessage(Map<String, dynamic> event) {
//     final incoming = MessageModel.fromSocketEvent(event);

//     setState(() {
//       // Agar ye humara hi optimistic message hai (client_id match), replace karo
//       final idx = _messages.indexWhere((m) => m.clientId != null && m.clientId == incoming.clientId);
//       if (idx != -1) {
//         _messages[idx] = incoming;
//       } else {
//         _messages.add(incoming);
//       }
//     });
//     _scrollToBottom();

//     // Doosre ka message hai to turant read receipt bhej do (chat khuli hui hai)
//     if (incoming.sender?.id != _myUserId) {
//       _socket.sendReadReceipt(incoming.id);
//     }
//   }

//   void _onDeleteEvent(Map<String, dynamic> event) {
//     final messageId = event['message_id']?.toString();
//     final forEveryone = event['for_everyone'] == true;
//     setState(() {
//       final idx = _messages.indexWhere((m) => m.id == messageId);
//       if (idx == -1) return;
//       if (forEveryone) {
//         _messages[idx].deletedForEveryone = true;
//         _messages[idx].text = '';
//       } else if (event['deleted_by']?.toString() == _myUserId) {
//         _messages[idx].deletedForMe = true;
//       }
//     });
//   }

//   void _onReactionEvent(Map<String, dynamic> event) {
//     // Simple approach: is message ka reaction list stale ho gaya,
//     // production me yahan history-refresh ya reaction merge kar sakte ho.
//   }

//   void _scrollToBottom() {
//     WidgetsBinding.instance.addPostFrameCallback((_) {
//       if (_scrollController.hasClients) {
//         _scrollController.animateTo(
//           _scrollController.position.maxScrollExtent,
//           duration: const Duration(milliseconds: 250),
//           curve: Curves.easeOut,
//         );
//       }
//     });
//   }

//   void _sendMessage() {
//     final text = _textController.text.trim();
//     if (text.isEmpty) return;

//     final clientId = "${_myUserId}_${DateTime.now().millisecondsSinceEpoch}_${_clientIdCounter++}";

//     // Optimistic UI — turant list me daal do, socket confirm karega
//     final optimistic = MessageModel(
//       id: clientId, // temp id, socket response se replace hoga
//       conversationId: widget.conversation.id,
//       sender: UserMini(id: _myUserId ?? '', displayName: 'You'),
//       type: 'text',
//       text: text,
//       clientId: clientId,
//       createdAt: DateTime.now(),
//       isSending: true,
//     );

//     setState(() {
//       _messages.add(optimistic);
//       _textController.clear();
//     });
//     _scrollToBottom();

//     if (_isSocketConnected) {
//       _socket.sendMessage(text: text, clientId: clientId);
//     } else {
//       // Fallback: socket down hai to REST se bhejo
//       MessageApiService.sendMessageRest(
//         widget.conversation.id,
//         type: 'text',
//         text: text,
//         clientId: clientId,
//       ).then((sent) {
//         if (mounted) {
//           setState(() {
//             final idx = _messages.indexWhere((m) => m.clientId == clientId);
//             if (idx != -1) _messages[idx] = sent;
//           });
//         }
//       }).catchError((e) {
//         if (mounted) {
//           setState(() {
//             final idx = _messages.indexWhere((m) => m.clientId == clientId);
//             if (idx != -1) _messages[idx].sendFailed = true;
//           });
//         }
//       });
//     }
//   }

//   void _onTypingChanged(String value) {
//     _socket.sendTyping(value.isNotEmpty);
//   }

//   @override
//   void dispose() {
//     _socket.dispose();
//     _textController.dispose();
//     _scrollController.dispose();
//     super.dispose();
//   }

//   @override
//   Widget build(BuildContext context) {
//     return Scaffold(
//       backgroundColor: const Color(0xFFF0F2F5),
//       appBar: AppBar(
//         backgroundColor: const Color(0xFF030F27),
//         iconTheme: const IconThemeData(color: Colors.white),
//         titleSpacing: 0,
//         title: Row(
//           children: [
//             CircleAvatar(
//               radius: 18,
//               backgroundColor: Colors.grey[300],
//               backgroundImage: widget.conversation.displayPhoto != null && widget.conversation.displayPhoto!.isNotEmpty
//                   ? CachedNetworkImageProvider(widget.conversation.displayPhoto!)
//                   : null,
//               child: widget.conversation.displayPhoto == null || widget.conversation.displayPhoto!.isEmpty
//                   ? Icon(widget.conversation.isGroup ? Icons.group : Icons.person, color: Colors.grey[600], size: 18)
//                   : null,
//             ),
//             const SizedBox(width: 10),
//             Expanded(
//               child: Column(
//                 crossAxisAlignment: CrossAxisAlignment.start,
//                 mainAxisSize: MainAxisSize.min,
//                 children: [
//                   Text(widget.conversation.displayTitle,
//                       style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.bold)),
//                   if (_otherTyping)
//                     const Text("typing...", style: TextStyle(color: Colors.white70, fontSize: 11)),
//                 ],
//               ),
//             ),
//           ],
//         ),
//       ),
//       body: Column(
//         children: [
//           Expanded(child: _buildMessageList()),
//           _buildInputBar(),
//         ],
//       ),
//     );
//   }

//   Widget _buildMessageList() {
//     if (_isLoading) return const Center(child: CircularProgressIndicator());
//     if (_messages.isEmpty) {
//       return const Center(child: Text("Say hi 👋", style: TextStyle(color: Colors.grey)));
//     }
//     return ListView.builder(
//       controller: _scrollController,
//       padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
//       itemCount: _messages.length,
//       itemBuilder: (context, index) {
//         final msg = _messages[index];
//         final isMe = msg.sender?.id == _myUserId;
//         return _MessageBubble(message: msg, isMe: isMe);
//       },
//     );
//   }

//   Widget _buildInputBar() {
//     return SafeArea(
//       child: Padding(
//         padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
//         child: Row(
//           children: [
//             Expanded(
//               child: TextField(
//                 controller: _textController,
//                 onChanged: _onTypingChanged,
//                 minLines: 1,
//                 maxLines: 4,
//                 decoration: InputDecoration(
//                   hintText: "Message...",
//                   filled: true,
//                   fillColor: Colors.white,
//                   contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
//                   border: OutlineInputBorder(borderRadius: BorderRadius.circular(25), borderSide: BorderSide.none),
//                 ),
//               ),
//             ),
//             const SizedBox(width: 8),
//             CircleAvatar(
//               backgroundColor: const Color(0xFF030F27),
//               child: IconButton(
//                 icon: const Icon(Icons.send, color: Colors.white),
//                 onPressed: _sendMessage,
//               ),
//             ),
//           ],
//         ),
//       ),
//     );
//   }
// }

// class _MessageBubble extends StatelessWidget {
//   final MessageModel message;
//   final bool isMe;
//   const _MessageBubble({required this.message, required this.isMe});

//   @override
//   Widget build(BuildContext context) {
//     if (message.deletedForMe) return const SizedBox.shrink();

//     final bubbleColor = isMe ? const Color(0xFF030F27) : Colors.white;
//     final textColor = isMe ? Colors.white : Colors.black87;
//     final displayText = message.deletedForEveryone ? "This message was deleted" : (message.text ?? '');

//     return Align(
//       alignment: isMe ? Alignment.centerRight : Alignment.centerLeft,
//       child: Container(
//         margin: const EdgeInsets.symmetric(vertical: 3),
//         padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
//         constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.72),
//         decoration: BoxDecoration(
//           color: bubbleColor,
//           borderRadius: BorderRadius.circular(16),
//         ),
//         child: Column(
//           crossAxisAlignment: CrossAxisAlignment.start,
//           mainAxisSize: MainAxisSize.min,
//           children: [
//             Text(
//               displayText,
//               style: TextStyle(
//                 color: message.deletedForEveryone ? Colors.grey : textColor,
//                 fontStyle: message.deletedForEveryone ? FontStyle.italic : FontStyle.normal,
//                 fontSize: 14.5,
//               ),
//             ),
//             const SizedBox(height: 3),
//             Row(
//               mainAxisSize: MainAxisSize.min,
//               children: [
//                 if (message.isEdited)
//                   Text("edited  ", style: TextStyle(fontSize: 10, color: isMe ? Colors.white60 : Colors.grey)),
//                 Text(
//                   "${message.createdAt.hour.toString().padLeft(2, '0')}:${message.createdAt.minute.toString().padLeft(2, '0')}",
//                   style: TextStyle(fontSize: 10, color: isMe ? Colors.white60 : Colors.grey),
//                 ),
//                 if (isMe) ...[
//                   const SizedBox(width: 4),
//                   Icon(
//                     message.sendFailed
//                         ? Icons.error_outline
//                         : message.isSending
//                             ? Icons.access_time
//                             : Icons.done_all,
//                     size: 13,
//                     color: message.sendFailed ? Colors.red : Colors.white60,
//                   ),
//                 ],
//               ],
//             ),
//           ],
//         ),
//       ),
//     );
//   }
// }

































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
      onDone: () => _isConnected = false,
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
  void sendMessage({
    required String text,
    String messageType = 'text',
    required String clientId,
    String? replyTo,
  }) {
    _send({
      'type': 'message',
      'client_id': clientId,
      'message_type': messageType,
      'text': text,
      'reply_to': replyTo,
    });
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