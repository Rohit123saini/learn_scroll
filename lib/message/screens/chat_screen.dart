// message/screens/chat_screen.dart
//
// Tere pubspec.yaml me ye sab already maujood hain:
// image_picker -> photo/video pick
// file_selector -> audio/any-file pick
// url_launcher -> location tap pe externally open (Maps)
// geolocator -> current location share
// flutter_webrtc, permission_handler -> call
//
// NAYI dependencies (media download + notifications ke liye):
//   dio, gal, path_provider, open_filex   (media_download_service.dart)
//   firebase_core, firebase_messaging, flutter_local_notifications (push_notification_service.dart)

import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart'; // 🔥 NAYA — video fullscreen (landscape) rotation ke liye
import 'package:cached_network_image/cached_network_image.dart';
import 'package:image_picker/image_picker.dart';
import 'package:file_selector/file_selector.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:geolocator/geolocator.dart';
import 'package:permission_handler/permission_handler.dart'; // 🔥 NAYA — camera/mic runtime permission
import 'package:path_provider/path_provider.dart'; // 🔥 NAYA — voice note temp file
import 'package:record/record.dart'; // 🔥 NAYA — WhatsApp jaisa voice-note recording (pubspec: record: ^5.1.2)
import 'package:video_player/video_player.dart'; // 🔥 NAYA — video ab tap pe play hoga, sirf download nahi
import 'package:audioplayers/audioplayers.dart'; // 🔥 NAYA — audio ab inline play hoga, WhatsApp voice-note jaisa

import '../models/message_models.dart';
import '../services/message_api_service.dart';
import '../services/message_cache_service.dart'; // 🔥 NAYA — 1-week local message cache
import '../services/chat_socket_service.dart';
import '../services/call_api_service.dart';
import '../services/media_download_service.dart'; // 🔥 NAYA — media download
import '../services/push_notification_service.dart'; // 🔥 NAYA — notification suppress
import '../services/call_kit_service.dart'; // 🔥 NAYA — native call popup dismiss
import '../services/call_manager.dart'; // 🔥 NAYA — call waiting check ke liye
import '../../services/auth_service.dart';
import 'call_screen.dart';
import 'incoming_call_screen.dart'; // 🔥 NAYA — full-screen incoming call UI (outgoing call jaisa look)
import 'study_room_screen.dart';
import '../../widgets/sticker_picker_sheet.dart'; // 🔥 NAYA — animated Rive stickers (Emotions + Funny), chat & comments dono me reusable

const _kEmojis = ['👍', '❤', '😂', '😮', '😢', '🙏'];

// 🔥 NAYA — FIX: gallery se pick kiya gaya media hamesha ek normal file
// path nahi hota. Kuch sources — jaise DOOSRE app ka media (misaal:
// WhatsApp ke apne "WhatsApp Images/Video" folder se koi photo/video
// select karo) — Android ke system picker se `content://` URI ke roop
// me aata hai. `dart:io` ka `File()` is URI ko seedha padh nahi paata,
// isliye:
//   • preview me kuch dikhta nahi tha (Image.file blank reh jaata)
//   • upload silently fail ho jaata tha (File nahi khulti), isliye
//     chat me photo bheji hi nahi jaati thi
//   • filename na milne ki wajah se image/video ka extension-check
//     bhi kabhi-kabhi galat ho jaata tha
// `XFile.readAsBytes()` HAR source ke liye kaam karta hai (real path ho
// ya content URI), isliye usse bytes nikaal ke ek REAL temp file bana
// dete hain — us par aage sab kuch (preview, extension-check, upload)
// normal file ki tarah kaam karta hai. Top-level rakha hai taaki
// ChatScreen aur niche wali _MediaPreviewScreen dono use kar sakein.
Future<XFile> _ensureRealFile(XFile f) async {
  try {
    if (!f.path.contains('content://') && await File(f.path).exists()) {
      return f; // already ek normal, readable file path hai
    }
  } catch (_) {
    // File(f.path) khud crash kar sakta hai agar path valid hi na ho
    // (content URI pe) — is case me neeche wala fallback chalega.
  }
  final bytes = await f.readAsBytes();
  final tempDir = await getTemporaryDirectory();
  final safeName = f.name.trim().isNotEmpty
      ? f.name.trim()
      : 'media_${DateTime.now().millisecondsSinceEpoch}';
  final tempPath = "${tempDir.path}/picked_${DateTime.now().microsecondsSinceEpoch}_$safeName";
  final tempFile = await File(tempPath).writeAsBytes(bytes);
  return XFile(tempFile.path, name: f.name, mimeType: f.mimeType);
}

Future<List<XFile>> _ensureRealFiles(List<XFile> files) =>
    Future.wait(files.map(_ensureRealFile));

class ChatScreen extends StatefulWidget {
  final ConversationModel conversation;
  const ChatScreen({super.key, required this.conversation});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final ChatSocketService _socket = ChatSocketService();
  final TextEditingController _textController = TextEditingController();
  final ScrollController _scrollController = ScrollController();

  List<MessageModel> _messages = []; // index 0 = sabse purana (chat bottom pe latest)
  bool _isLoading = true;
  bool _isSocketConnected = false;
  bool _otherTyping = false;
  String? _myUserId;
  int _clientIdCounter = 0;
  // (call-in-progress guard ab IncomingCallScreen ke andar callId-based
  // hai — IncomingCallScreen._activeCallIds — isliye ye local flag hata di.)

  // 🔥 NAYA — WhatsApp-level upgrades ke liye state:
  MessageModel? _replyingTo; // reply-compose mode
  bool _otherOnline = false;
  DateTime? _otherLastSeen;
  final Set<String> _readByOtherIds = {}; // mere bheje messages jo dusre ne read kar liye

  // 🔥 NAYA — WhatsApp jaisa "Download" -> "Open" state. Doc/file/
  // presentation ke liye actual local path bhi yaad rakhte hain taaki
  // dobara tap pe seedha khul jaaye, dobara download na ho.
  final Set<String> _downloadedIds = {}; // sab downloaded message ids (docs + gallery dono)
  final Map<String, String> _downloadedPaths = {}; // sirf doc-type: msg.id -> local path
  final Set<String> _downloadCheckedIds = {}; // duplicate "already downloaded?" check na ho isliye

  // 🔥 NAYA — voice note recording (mic seedha input bar pe)
  final AudioRecorder _recorder = AudioRecorder();
  bool _isRecording = false;
  Duration _recordDuration = Duration.zero;
  Timer? _recordTimer;
  String? _recordPath;

  @override
  void initState() {
    super.initState();
    // 🔥 NAYA: is chat ka id "currently open" mark karo — taaki isi chat
    // ka naya message aane par duplicate push notification popup na dikhe
    // (PushNotificationService.init() me ye check hota hai).
    PushNotificationService.currentOpenConversationId = widget.conversation.id;
    _init();
  }

  Future<void> _init() async {
    _myUserId = await AuthService.getUserId();
    await _loadHistory();
    await _connectSocket();
    await MessageApiService.readAll(widget.conversation.id);
  }

  Future<void> _loadHistory() async {
    // Pehle cache se turant dikhao (agar hai, 1 week ke andar ka) — chat
    // kholte hi purane messages dikh jaate hain, network slow ho ya na ho.
    final cached = await MessageCacheService.getCachedMessages(widget.conversation.id);
    if (cached.isNotEmpty && mounted) {
      setState(() {
        _messages = cached.reversed.toList();
        _isLoading = false;
      });
      _scrollToBottom();
      _scanAlreadyDownloaded();
    }
    try {
      final data = await MessageApiService.getMessages(widget.conversation.id, page: 1);
      if (mounted) {
        setState(() {
          _messages = data.reversed.toList();
          _isLoading = false;
        });
        _scrollToBottom();
        _scanAlreadyDownloaded(); // 🔥 NAYA — WhatsApp jaisa: purane downloaded files pe "Open" dikhao
      }
      MessageCacheService.saveMessages(widget.conversation.id, data); // fire-and-forget, 1 week tak valid
    } catch (e) {
      if (mounted) {
        setState(() => _isLoading = false);
        // Cache se messages already dikh rahe hon to error se use mat dabao.
        if (cached.isEmpty) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text("Failed to load messages: $e")),
          );
        }
      }
    }
  }

  Future<void> _connectSocket() async {
    await _socket.connect(widget.conversation.id);
    setState(() => _isSocketConnected = true);
    _socket.events.listen(_handleSocketEvent);
  }

  void _handleSocketEvent(Map<String, dynamic> event) {
    final type = event['type'];
    switch (type) {
      case 'chat_message':
        _onIncomingMessage(event);
        break;
      case 'typing':
        if (event['user_id']?.toString() != _myUserId) {
          setState(() => _otherTyping = event['is_typing'] == true);
        }
        break;
      case 'read':
        // 🔥 NAYA: pehle ye event bilkul ignore hota tha — isliye tick
        // kabhi blue (read) nahi hota tha. Ab jab dusra user read karta
        // hai, us message TAK ke saare mere-bheje messages blue tick ho
        // jaate hain (WhatsApp jaisa hi — read ek point tak sequential hota hai).
        _onReadEvent(event);
        break;
      case 'delete':
        _onDeleteEvent(event);
        break;
      case 'reaction':
        _onReactionEvent(event);
        break;
      case 'presence':
        // 🔥 NAYA: online/last-seen status ab AppBar me dikhega
        _onPresenceEvent(event);
        break;
      // 🔥 CALL EVENTS — backend se call_event type me aate hain
      case 'call_event':
      case 'incoming_call':
        _handleCallEvent(event);
        break;
      case 'error':
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(event['message']?.toString() ?? 'Error')),
          );
        }
        break;
      default:
        // kuch backends event ko directly 'incoming_call' type me bhejte hain
        if (type.toString().contains('call')) {
          _handleCallEvent(event);
        }
        break;
    }
  }

  // ============================================================
  // CALL LOGIC
  // ============================================================
  void _handleCallEvent(Map<String, dynamic> event) {
    // backend 2 format bhej sakta hai:
    // 1) {type: call_event, event: incoming_call, call_id:..., call_type:..., caller_name:...}
    // 2) {type: incoming_call, call_id:...,...}
    final eventName = (event['event'] ?? event['type']).toString();
    final callId = (event['call_id'] ?? event['id'])?.toString();
    final callType = (event['call_type'] ?? event['type'] ?? 'audio').toString();
    final callerName = (event['caller_name'] ?? 'Someone').toString();
    final convId = (event['conversation_id'] ?? widget.conversation.id).toString();

    if (convId != widget.conversation.id) return; // dusri chat ka call ignore
    if (callId == null) return;
    if (eventName == 'incoming_call' && event['caller_id']?.toString() == _myUserId) return;

    if (eventName == 'incoming_call') {
      // 🔥 NAYA — CALL WAITING: pehle se ek call chal rahi ho to poori
      // IncomingCallScreen mat kholo, bas CallManager ko batao (chhota
      // banner CallScreen khud dikha dega).
      if (CallManager.instance.isActive) {
        CallManager.instance.setWaitingCall(
          callId: callId,
          callerName: callerName,
          callType: callType,
          conversationId: convId,
        );
        return;
      }

      // Primary popup ab foreground FCM listener se app-wide push hota hai
      // (push_notification_service.dart -> IncomingCallScreen.showIfNeeded).
      // Ye WebSocket wala sirf fallback hai (jab ChatScreen khuli ho) — same
      // static entry point use karta hai, isliye agar dono fire ho jaayein
      // to bhi screen sirf EK baar khulegi (callId-based guard).
      IncomingCallScreen.showIfNeeded(
        Navigator.of(context),
        callId: callId,
        callType: callType,
        callerName: callerName,
        conversationId: convId,
        isGroup: widget.conversation.isGroup,
        groupTitle: widget.conversation.isGroup ? widget.conversation.displayTitle : null,
      );
    } else if (eventName == 'call_ended' || eventName == 'call_rejected' || eventName == 'user_left') {
      IncomingCallScreen.dismissIfShowing(Navigator.of(context), callId);
      // Agar ye waiting-call hi cancel/khatam hui ho (dusra banda hang up
      // kar de call connect hone se pehle), waiting banner bhi hata do.
      if (CallManager.instance.waitingCallId == callId) {
        CallManager.instance.clearWaitingCall();
      }
      // Dusri taraf se call cut/reject hui to native CallKit popup bhi
      // turant hata do, warna woh screen pe atka reh jaayega.
      CallKitService.endCallUiByCallId(callId);
    }
  }

  Future<void> _startCall(String type) async {
    try {
      // loader
      showDialog(context: context, barrierDismissible: false, builder: (_) => Center(child: CircularProgressIndicator()));
      final data = await CallApiService.initiateCall(widget.conversation.id, type);
      if (!mounted) return;
      Navigator.pop(context); // loader close

      final callId = data['call_id']?.toString() ?? data['id']?.toString();
      if (callId == null) throw Exception("call_id not returned");

      // 🔥 FIX: initiateCall ke response me livekit_url + livekit_token
      // pehle se aa raha tha lekin CallScreen ko pass hi nahi kiya ja
      // raha tha — required params hone ki wajah se ye compile/run hi
      // nahi hota tha.
      final livekitUrl = data['livekit_url']?.toString();
      final livekitToken = data['livekit_token']?.toString();
      if (livekitUrl == null || livekitToken == null) {
        throw Exception("LiveKit credentials not received from server");
      }

      Navigator.push(context, MaterialPageRoute(builder: (_) => CallScreen(
        callId: callId,
        conversationId: widget.conversation.id,
        isVideo: type == 'video',
        isCaller: true,
        livekitUrl: livekitUrl,
        livekitToken: livekitToken,
      )));
    } catch (e) {
      if (mounted) {
        Navigator.pop(context);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Call failed: $e")));
      }
    }
  }

  // ============================================================
  // 🔥 NAYA: GROUP STUDY ROOM — whiteboard + shared PDF/image +
  // floating participant windows, same conversation ke socket
  // channel ka reuse karke.
  // ============================================================
  void _openStudyRoom() {
    // 🔥 NAYA — jab MAIN study room start karta hoon, samne wale ki chat
    // me turant ek "Study Room" card bhej do (jaisa call/media messages
    // bhejte hain) — usko tap karke wo seedha isi room me enter ho
    // jaayega, bina alag se link poochhe.
    _sendStudyRoomInvite();
    _enterStudyRoom();
  }

  // Card pe tap karke (khud bheja ho ya doosre ka receive kiya ho) —
  // dono jagah se yehi ek function room me le jaata hai, taaki tap karne
  // par dobara invite na bhej jaaye.
  void _enterStudyRoom() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => StudyRoomScreen(
          conversationId: widget.conversation.id,
          currentUserId: _myUserId ?? '',
          initialParticipants: const [], // TODO: widget.conversation se actual participants map karke UserProfileWindowModel list banao, agar group participants list available ho
          // 🔥 NAYA — study room ke andar hi call button aur AppBar title ke
          // liye us user ka naam/photo chahiye jiske saath one-to-one chat
          // chal rahi hai (group ho to group ka naam/photo).
          peerName: widget.conversation.isGroup
              ? widget.conversation.displayTitle
              : widget.conversation.otherParticipant?.displayName,
          peerAvatar: widget.conversation.displayPhoto,
        ),
      ),
    );
  }

  void _sendStudyRoomInvite() {
    final clientId = _newClientId();
    final optimistic = MessageModel(
      id: clientId,
      conversationId: widget.conversation.id,
      sender: UserMini(id: _myUserId ?? '', displayName: 'You'),
      type: MessageType.studyRoom,
      text: 'Study Room',
      clientId: clientId,
      createdAt: DateTime.now(),
      isSending: true,
    );
    setState(() => _messages.add(optimistic));
    _scrollToBottom();
    if (_isSocketConnected) {
      _socket.sendMessage(text: 'Study Room', messageType: MessageType.studyRoom, clientId: clientId);
    } else {
      MessageApiService.sendMessageRest(
        widget.conversation.id,
        type: MessageType.studyRoom,
        text: 'Study Room',
        clientId: clientId,
      ).then((sent) {
        if (mounted) {
          setState(() {
            final idx = _messages.indexWhere((m) => m.clientId == clientId);
            if (idx != -1) _messages[idx] = sent;
          });
        }
      }).catchError((e) {
        if (mounted) {
          setState(() {
            final idx = _messages.indexWhere((m) => m.clientId == clientId);
            if (idx != -1) _messages[idx].sendFailed = true;
          });
        }
      });
    }
  }

  // ============================================================
  // MESSAGE HANDLERS
  // ============================================================
  void _onIncomingMessage(Map<String, dynamic> event) {
    final incoming = MessageModel.fromSocketEvent(event);
    setState(() {
      final idx = _messages.indexWhere((m) => m.clientId != null && m.clientId == incoming.clientId);
      if (idx != -1) {
        _messages[idx] = incoming;
      } else {
        _messages.add(incoming);
      }
    });
    _scrollToBottom();
    if (incoming.sender?.id != _myUserId) {
      _socket.sendReadReceipt(incoming.id);
    }
  }

  void _onDeleteEvent(Map<String, dynamic> event) {
    final messageId = event['message_id']?.toString();
    final forEveryone = event['for_everyone'] == true;
    setState(() {
      final idx = _messages.indexWhere((m) => m.id == messageId);
      if (idx == -1) return;
      if (forEveryone) {
        _messages[idx].deletedForEveryone = true;
        _messages[idx].text = '';
      } else if (event['deleted_by']?.toString() == _myUserId) {
        _messages[idx].deletedForMe = true;
      }
    });
  }

  void _onReactionEvent(Map<String, dynamic> event) {
    final messageId = event['message_id']?.toString();
    final userId = event['user_id']?.toString();
    final emoji = event['emoji']?.toString();
    if (messageId == null || userId == null || emoji == null) return;
    setState(() {
      final idx = _messages.indexWhere((m) => m.id == messageId);
      if (idx == -1) return;
      final reactions = _messages[idx].reactions;
      final existingIdx = reactions.indexWhere((r) => r.user.id == userId);
      final isMe = userId == _myUserId;
      final reactor = isMe ? UserMini(id: userId, displayName: 'You') : (existingIdx != -1 ? reactions[existingIdx].user : UserMini(id: userId, displayName: ''));
      final updated = MessageReactionModel(id: existingIdx != -1 ? reactions[existingIdx].id : '$messageId-$userId', user: reactor, emoji: emoji, createdAt: DateTime.now());
      if (existingIdx != -1) reactions[existingIdx] = updated; else reactions.add(updated);
    });
  }

  // 🔥 NAYA
  void _onReadEvent(Map<String, dynamic> event) {
    final userId = event['user_id']?.toString();
    final messageId = event['message_id']?.toString();
    if (userId == null || messageId == null) return;
    if (userId == _myUserId) return; // apna hi read receipt, ignore
    final idx = _messages.indexWhere((m) => m.id == messageId);
    if (idx == -1) return;
    setState(() {
      // WhatsApp jaisa: is point tak ke saare mere bheje messages read maano
      for (int i = 0; i <= idx; i++) {
        final m = _messages[i];
        if (m.sender?.id == _myUserId) {
          _readByOtherIds.add(m.id);
        }
      }
    });
  }

  // 🔥 NAYA
  void _onPresenceEvent(Map<String, dynamic> event) {
    if (widget.conversation.isGroup) return; // group me per-user presence dikhana simple nahi, skip
    final userId = event['user_id']?.toString();
    if (userId == null || userId == _myUserId) return;
    final otherId = widget.conversation.otherParticipant?.id;
    if (otherId != null && userId != otherId) return;
    setState(() {
      _otherOnline = event['is_online'] == true;
      final lastSeen = event['last_seen_at']?.toString();
      if (lastSeen != null) _otherLastSeen = DateTime.tryParse(lastSeen);
    });
  }

  String? _presenceSubtitle() {
    if (widget.conversation.isGroup) return null;
    if (_otherTyping) return 'typing...';
    if (_otherOnline) return 'online';
    if (_otherLastSeen != null) return 'last seen ${_formatLastSeen(_otherLastSeen!)}';
    return null;
  }

  String _formatLastSeen(DateTime dt) {
    final now = DateTime.now();
    final local = dt.toLocal();
    final isToday = now.year == local.year && now.month == local.month && now.day == local.day;
    final yesterday = now.subtract(const Duration(days: 1));
    final isYesterday = yesterday.year == local.year && yesterday.month == local.month && yesterday.day == local.day;
    final time = "${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}";
    if (isToday) return "today at $time";
    if (isYesterday) return "yesterday at $time";
    return "on ${local.day}/${local.month}/${local.year}";
  }

  // 🔥 NAYA — reply-compose mode
  void _startReply(MessageModel msg) {
    setState(() => _replyingTo = msg);
  }

  void _cancelReply() => setState(() => _replyingTo = null);

  MessageModel? _findMessageById(String? id) {
    if (id == null) return null;
    for (final m in _messages) {
      if (m.id == id) return m;
    }
    return null;
  }
  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(_scrollController.position.maxScrollExtent, duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      }
    });
  }

  String _newClientId() => "${_myUserId}_${DateTime.now().millisecondsSinceEpoch}_${_clientIdCounter++}";

  void _sendMessage() {
    final text = _textController.text.trim();
    if (text.isEmpty) return;
    final clientId = _newClientId();
    final replyToId = _replyingTo?.id; // 🔥 NAYA
    final optimistic = MessageModel(id: clientId, conversationId: widget.conversation.id, sender: UserMini(id: _myUserId ?? '', displayName: 'You'), type: MessageType.text, text: text, replyTo: replyToId, clientId: clientId, createdAt: DateTime.now(), isSending: true);
    setState(() { _messages.add(optimistic); _textController.clear(); _replyingTo = null; });
    _scrollToBottom();
    if (_isSocketConnected) {
      _socket.sendMessage(text: text, clientId: clientId, replyTo: replyToId);
    } else {
      MessageApiService.sendMessageRest(widget.conversation.id, type: MessageType.text, text: text, replyTo: replyToId, clientId: clientId).then((sent) {
        if (mounted) setState(() { final idx = _messages.indexWhere((m) => m.clientId == clientId); if (idx != -1) _messages[idx] = sent; });
      }).catchError((e) {
        if (mounted) setState(() { final idx = _messages.indexWhere((m) => m.clientId == clientId); if (idx != -1) _messages[idx].sendFailed = true; });
      });
    }
  }

  // 🔥 NAYA: camera se seedha photo/video khinch ke bhejne ke liye —
  // pehle sirf gallery se pick hota tha. RECORD_AUDIO permission video
  // recording ke liye zaroori hai (audio track ke saath).
  Future<bool> _ensureCameraPermission({bool withMic = false}) async {
    var camStatus = await Permission.camera.status;
    if (!camStatus.isGranted) camStatus = await Permission.camera.request();
    if (!camStatus.isGranted) return false;
    if (withMic) {
      var micStatus = await Permission.microphone.status;
      if (!micStatus.isGranted) micStatus = await Permission.microphone.request();
      if (!micStatus.isGranted) return false;
    }
    return true;
  }

  Future<XFile?> _pickAttachmentFile(String messageType, {ImageSource source = ImageSource.gallery}) async {
    switch (messageType) {
      case MessageType.image:
        if (source == ImageSource.camera && !await _ensureCameraPermission()) return null;
        return ImagePicker().pickImage(source: source, imageQuality: 85);
      case MessageType.video:
        if (source == ImageSource.camera && !await _ensureCameraPermission(withMic: true)) return null;
        return ImagePicker().pickVideo(source: source);
      case MessageType.audio: const audioGroup = XTypeGroup(label: 'audio', extensions: ['mp3', 'wav', 'm4a', 'ogg', 'aac', 'opus']); return openFile(acceptedTypeGroups: [audioGroup]);
      case MessageType.presentation: const presentationGroup = XTypeGroup(label: 'presentation', extensions: ['ppt', 'pptx', 'key', 'odp', 'pdf']); return openFile(acceptedTypeGroups: [presentationGroup]);
      default: return openFile();
    }
  }

  Future<void> _pickAndSendAttachment(String messageType, {ImageSource source = ImageSource.gallery}) async {
    var picked = await _pickAttachmentFile(messageType, source: source);
    if (picked == null || picked.path.isEmpty) return;
    if (messageType == MessageType.image || messageType == MessageType.video) {
      picked = await _ensureRealFile(picked);
    }
    await _uploadAndSendFile(File(picked.path), messageType, picked.name);
  }

  /// 🔥 NAYA: picked attachment aur recorded voice note dono isi ek jagah
  /// se upload + send hote hain — code duplicate nahi.
  Future<void> _uploadAndSendFile(File file, String messageType, String fileName, {Map<String, dynamic>? extraMeta, String? text}) async {
    final path = file.path;
    final clientId = _newClientId();
    final optimistic = MessageModel(id: clientId, conversationId: widget.conversation.id, sender: UserMini(id: _myUserId ?? '', displayName: 'You'), type: messageType, text: text ?? '', meta: {'file_name': fileName, ...?extraMeta}, clientId: clientId, createdAt: DateTime.now(), isSending: true, uploadProgress: 0.0, localFilePath: path);
    setState(() => _messages.add(optimistic)); _scrollToBottom();
    try {
      // 🔥 NAYA: onProgress se optimistic message ka uploadProgress
      // live update hota hai — bubble me actual % dikhta hai jab tak
      // file backend tak pura upload nahi ho jaata.
      final uploaded = await MessageApiService.uploadFile(
        file,
        onProgress: (p) {
          if (!mounted) return;
          setState(() {
            final idx = _messages.indexWhere((m) => m.clientId == clientId);
            if (idx != -1) _messages[idx].uploadProgress = p;
          });
        },
      );
      final sent = await MessageApiService.sendMessageRest(widget.conversation.id, type: messageType, text: text, fileUrl: uploaded.fileUrl, meta: {'file_name': uploaded.fileName, 'size': uploaded.fileSize, 'mime_type': uploaded.mimeType, ...?extraMeta}, clientId: clientId);
      if (mounted) setState(() { final idx = _messages.indexWhere((m) => m.clientId == clientId); if (idx != -1) _messages[idx] = sent; });
    } catch (e) {
      if (mounted) { setState(() { final idx = _messages.indexWhere((m) => m.clientId == clientId); if (idx != -1) _messages[idx].sendFailed = true; }); ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Upload failed: $e"))); }
    }
  }

  // ============================================================
  // 🔥 NAYA: VOICE NOTE RECORDING — seedha chat input bar ke mic se
  // ============================================================
  Future<void> _startRecording() async {
    var micStatus = await Permission.microphone.status;
    if (!micStatus.isGranted) micStatus = await Permission.microphone.request();
    if (!micStatus.isGranted) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Microphone permission chahiye voice note bhejne ke liye")));
      return;
    }
    final tempDir = await getTemporaryDirectory();
    final path = "${tempDir.path}/voice_${DateTime.now().millisecondsSinceEpoch}.m4a";
    await _recorder.start(const RecordConfig(encoder: AudioEncoder.aacLc), path: path);
    _recordPath = path;
    _recordDuration = Duration.zero;
    _recordTimer?.cancel();
    _recordTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() => _recordDuration += const Duration(seconds: 1));
    });
    if (mounted) setState(() => _isRecording = true);
  }

  Future<void> _stopRecordingAndSend() async {
    _recordTimer?.cancel();
    final path = await _recorder.stop();
    final duration = _recordDuration;
    if (mounted) setState(() { _isRecording = false; _recordDuration = Duration.zero; });
    if (path == null) return;
    // bahut chhota (accidental tap) recording ho to mat bhejo
    if (duration.inMilliseconds < 800) {
      try { await File(path).delete(); } catch (_) {}
      return;
    }
    final fileName = "voice_note_${DateTime.now().millisecondsSinceEpoch}.m4a";
    await _uploadAndSendFile(File(path), MessageType.audio, fileName, extraMeta: {'duration_seconds': duration.inSeconds});
  }

  Future<void> _cancelRecording() async {
    _recordTimer?.cancel();
    final path = await _recorder.stop();
    if (mounted) setState(() { _isRecording = false; _recordDuration = Duration.zero; });
    if (path != null) {
      try { await File(path).delete(); } catch (_) {}
    }
  }

  Future<void> _sendLocation() async {
    try {
      final permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        final requested = await Geolocator.requestPermission();
        if (requested == LocationPermission.denied || requested == LocationPermission.deniedForever) throw Exception("Location permission denied");
      }
      final pos = await Geolocator.getCurrentPosition(); final clientId = _newClientId();
      final optimistic = MessageModel(id: clientId, conversationId: widget.conversation.id, sender: UserMini(id: _myUserId ?? '', displayName: 'You'), type: MessageType.location, text: 'Location', meta: {'lat': pos.latitude, 'lng': pos.longitude}, clientId: clientId, createdAt: DateTime.now(), isSending: true);
      setState(() => _messages.add(optimistic)); _scrollToBottom();
      final sent = await MessageApiService.sendMessageRest(widget.conversation.id, type: MessageType.location, text: 'Location', meta: {'lat': pos.latitude, 'lng': pos.longitude}, clientId: clientId);
      if (mounted) setState(() { final idx = _messages.indexWhere((m) => m.clientId == clientId); if (idx != -1) _messages[idx] = sent; });
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Location share failed: $e")));
    }
  }

  void _showAttachmentSheet() {
    showModalBottomSheet(context: context, backgroundColor: Colors.white, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))), builder: (_) => SafeArea(child: Wrap(children: [
      // 🔥 NAYA — seedha camera se photo/video khinch ke bhejo
      _attachmentTile(Icons.camera_alt, "Camera", const Color(0xFF00BCD4), _showCameraChooser),
      // 🔥 NAYA — gallery se ek saath MULTIPLE photos/videos select karke bhejo (WhatsApp jaisa)
      _attachmentTile(Icons.photo_library, "Gallery", const Color(0xFF9C27B0), _pickAndSendMultipleMedia),
      _attachmentTile(Icons.mic, "Audio", const Color(0xFFFF9800), () => _pickAndSendAttachment(MessageType.audio)),
      _attachmentTile(Icons.insert_drive_file, "File", const Color(0xFF3F51B5), () => _pickAndSendAttachment(MessageType.file)),
      _attachmentTile(Icons.slideshow, "Presentation", const Color(0xFF00897B), () => _pickAndSendAttachment(MessageType.presentation)),
      _attachmentTile(Icons.location_on, "Location", const Color(0xFF4CAF50), _sendLocation),
    ])));
  }

  // 🔥 NAYA — WhatsApp jaisa "album": gallery kholte hi user ek saath kai
  // photos select kar sakta hai, aur wo sab EK HI message ke andar
  // (file_urls[]) bandle hoke jaate hain — pehle har photo ka apna alag
  // message ban jaata tha, ab ek hi bubble me sab dikhte hain.
  // Videos abhi bhi apne-apne alag message me jaate hain (ek message = ek
  // video — WhatsApp bhi isi tarah karta hai jab video ho).
  // NOTE: `pickMultipleMedia()` image_picker >= 1.0.0 me available hai.
  // Agar tumhara image_picker version purana hai to pubspec.yaml me
  // `image_picker: ^1.1.2` kar ke `flutter pub get` chala lena.
  Future<void> _pickAndSendMultipleMedia() async {
    final List<XFile> rawPicked = await ImagePicker().pickMultipleMedia(imageQuality: 85);
    if (rawPicked.isEmpty) return;
    // 🔥 FIX — WhatsApp/doosre app ke media folder se pick kiya gaya
    // item content:// URI ke roop me aa sakta hai; yahin sabse pehle
    // real file bana lete hain taaki preview screen aur upload dono
    // sahi se kaam karein (_ensureRealFile ka comment dekho).
    final picked = await _ensureRealFiles(rawPicked);

    // 🔥 NAYA: seedha upload karne ke bajaye pehle WhatsApp jaisa
    // review/preview screen dikhao — user yahan se koi item hata sakta
    // hai, "+" se aur media add kar sakta hai, aur caption likh sakta hai.
    // Tap-to-send hone se pehle poora album dekh sakte ho.
    final result = await Navigator.push<_MediaPreviewResult>(
      context,
      MaterialPageRoute(builder: (_) => _MediaPreviewScreen(files: picked)),
    );
    if (result == null || result.files.isEmpty) return;

    const videoExtensions = {'mp4', 'mov', 'mkv', '3gp', 'webm', 'avi', 'm4v'};
    final images = <XFile>[];
    final videos = <XFile>[];
    for (final file in result.files) {
      final ext = (file.name.isNotEmpty ? file.name : file.path).split('.').last.toLowerCase();
      (videoExtensions.contains(ext) ? videos : images).add(file);
    }
    final caption = result.caption.isNotEmpty ? result.caption : null;

    // Saare images ek hi message me bandle karke bhejo.
    if (images.isNotEmpty) {
      _uploadAndSendMultipleImages(images, caption: caption);
    }
    // Videos: har ek apna alag message (fire-and-forget, parallel).
    // Caption sirf pehli video pe lagta hai (WhatsApp bhi mixed-album me
    // caption ko pehle item se associate karta hai).
    for (var i = 0; i < videos.length; i++) {
      _uploadAndSendFile(
        File(videos[i].path),
        MessageType.video,
        videos[i].name,
        text: (i == 0 && images.isEmpty) ? caption : null,
      );
    }
  }

  /// 🔥 NAYA: multiple images ko ek hi message me bhejta hai — sab files
  /// parallel upload hoti hain, aur upload complete hote hi sirf EK REST
  /// call (`file_urls: [...]`) se message jaata hai. Backend `Message`
  /// model me `file_urls` (JSONField list) pehle se hi maujood hai, bas
  /// frontend abhi tak use nahi kar raha tha.
  Future<void> _uploadAndSendMultipleImages(List<XFile> files, {String? caption}) async {
    final clientId = _newClientId();
    final localPaths = files.map((f) => f.path).toList();
    final optimistic = MessageModel(
      id: clientId,
      conversationId: widget.conversation.id,
      sender: UserMini(id: _myUserId ?? '', displayName: 'You'),
      type: MessageType.image,
      text: caption ?? '',
      meta: {'count': files.length},
      clientId: clientId,
      createdAt: DateTime.now(),
      isSending: true,
      uploadProgress: 0.0,
      localFilePaths: localPaths,
    );
    setState(() => _messages.add(optimistic));
    _scrollToBottom();

    final progressPerFile = List<double>.filled(files.length, 0.0);
    void updateOverallProgress() {
      if (!mounted) return;
      final avg = progressPerFile.reduce((a, b) => a + b) / progressPerFile.length;
      setState(() {
        final idx = _messages.indexWhere((m) => m.clientId == clientId);
        if (idx != -1) _messages[idx].uploadProgress = avg;
      });
    }

    try {
      // Sab files EK SAATH (parallel) upload hoti hain — ek ke liye ruk
      // ke doosri ka wait nahi karna padta.
      final uploaded = await Future.wait(List.generate(files.length, (i) {
        return MessageApiService.uploadFile(
          File(files[i].path),
          onProgress: (p) {
            progressPerFile[i] = p;
            updateOverallProgress();
          },
        );
      }));

      final urls = uploaded.map((u) => u.fileUrl).toList();
      final sent = await MessageApiService.sendMessageRest(
        widget.conversation.id,
        type: MessageType.image,
        text: caption,
        fileUrls: urls,
        meta: {
          'count': urls.length,
          'items': uploaded.map((u) => {'file_name': u.fileName, 'size': u.fileSize, 'mime_type': u.mimeType}).toList(),
        },
        clientId: clientId,
      );
      if (mounted) {
        setState(() {
          final idx = _messages.indexWhere((m) => m.clientId == clientId);
          if (idx != -1) _messages[idx] = sent;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          final idx = _messages.indexWhere((m) => m.clientId == clientId);
          if (idx != -1) _messages[idx].sendFailed = true;
        });
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Upload failed: $e")));
      }
    }
  }

  // 🔥 NAYA — "Camera" tap karte hi Photo ya Video khinchne ka chhota chooser
  void _showCameraChooser() {
    showModalBottomSheet(context: context, backgroundColor: Colors.white, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))), builder: (_) => SafeArea(child: Wrap(children: [
      _attachmentTile(Icons.camera_alt, "Take Photo", const Color(0xFF9C27B0), () => _pickAndSendAttachment(MessageType.image, source: ImageSource.camera)),
      _attachmentTile(Icons.videocam, "Record Video", const Color(0xFFE53935), () => _pickAndSendAttachment(MessageType.video, source: ImageSource.camera)),
    ])));
  }

  Widget _attachmentTile(IconData icon, String label, Color color, VoidCallback onTap) {
    return ListTile(leading: CircleAvatar(backgroundColor: color, child: Icon(icon, color: Colors.white)), title: Text(label), onTap: () { Navigator.pop(context); onTap(); });
  }

  void _onTypingChanged(String value) => _socket.sendTyping(value.isNotEmpty);

  void _showReactionPicker(MessageModel msg) {
    final myCurrent = msg.myReaction(_myUserId ?? '');
    // 🔥 NAYA — plain emoji ki jagah ab animated Rive sticker dikhta hai
    // (stickerForEmoji helper se), lekin backend me wahi purana emoji
    // string save/bheja jaata hai — koi data-format change nahi hua.
    showModalBottomSheet(context: context, backgroundColor: Colors.white, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))), builder: (_) => SafeArea(child: Padding(padding: const EdgeInsets.symmetric(vertical: 18), child: Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: _kEmojis.map((emoji) { final selected = emoji == myCurrent; return GestureDetector(onTap: () { Navigator.pop(context); _toggleReaction(msg, emoji); }, child: Container(padding: const EdgeInsets.all(6), decoration: BoxDecoration(color: selected ? const Color(0xFFEEF1FF) : null, shape: BoxShape.circle), child: stickerForEmoji(emoji, size: 40))); }).toList()))));
  }

  void _toggleReaction(MessageModel msg, String emoji) async {
    final myCurrent = msg.myReaction(_myUserId ?? '');
    if (myCurrent == emoji) { setState(() { msg.reactions.removeWhere((r) => r.user.id == _myUserId); }); try { await MessageApiService.removeReaction(msg.id); } catch (_) {} }
    else { setState(() { final idx = msg.reactions.indexWhere((r) => r.user.id == _myUserId); final mine = MessageReactionModel(id: idx != -1 ? msg.reactions[idx].id : '${msg.id}-me', user: UserMini(id: _myUserId ?? '', displayName: 'You'), emoji: emoji, createdAt: DateTime.now()); if (idx != -1) msg.reactions[idx] = mine; else msg.reactions.add(mine); }); _socket.sendReaction(msg.id, emoji); }
  }

  void _showMessageActions(MessageModel msg, bool isMe) {
    if (msg.deletedForEveryone || msg.deletedForMe) return;
    showModalBottomSheet(context: context, backgroundColor: Colors.white, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))), builder: (_) => SafeArea(child: Column(mainAxisSize: MainAxisSize.min, children: [
      ListTile(leading: const Icon(Icons.reply), title: const Text("Reply"), onTap: () { Navigator.pop(context); _startReply(msg); }),
      ListTile(leading: const Icon(Icons.emoji_emotions_outlined), title: const Text("React"), onTap: () { Navigator.pop(context); _showReactionPicker(msg); }),
      if (isMe && msg.type == MessageType.text) ListTile(leading: const Icon(Icons.edit_outlined), title: const Text("Edit"), onTap: () { Navigator.pop(context); _showEditDialog(msg); }),
      // 🔥 NAYA: media messages ke liye "Save to device" action bhi —
      // multi-image (album) message ho to sab photos ek-ek karke save hoti hain.
      if ((msg.fileUrl != null && msg.fileUrl!.isNotEmpty) || (msg.fileUrls != null && msg.fileUrls!.isNotEmpty))
        ListTile(leading: const Icon(Icons.download_outlined), title: const Text("Save to device"), onTap: () async {
          Navigator.pop(context);
          if (msg.fileUrls != null && msg.fileUrls!.length > 1) {
            for (final u in msg.fileUrls!) {
              await _downloadMediaUrl(context, msg, u.toString());
            }
          } else {
            _downloadMedia(context, msg);
          }
        }),
      ListTile(leading: const Icon(Icons.delete_outline, color: Colors.red), title: const Text("Delete for me", style: TextStyle(color: Colors.red)), onTap: () { Navigator.pop(context); _socket.sendDelete(msg.id, forEveryone: false); }),
      if (isMe) ListTile(leading: const Icon(Icons.delete_forever_outlined, color: Colors.red), title: const Text("Delete for everyone", style: TextStyle(color: Colors.red)), onTap: () { Navigator.pop(context); _socket.sendDelete(msg.id, forEveryone: true); }),
    ])));
  }

  void _showEditDialog(MessageModel msg) {
    final controller = TextEditingController(text: msg.text ?? '');
    showDialog(context: context, builder: (_) => AlertDialog(title: const Text("Edit message"), content: TextField(controller: controller, maxLines: 4, autofocus: true), actions: [
      TextButton(onPressed: () => Navigator.pop(context), child: const Text("Cancel")),
      TextButton(onPressed: () async { final newText = controller.text.trim(); Navigator.pop(context); if (newText.isEmpty || newText == msg.text) return; try { final updated = await MessageApiService.editMessage(msg.id, newText); if (mounted) setState(() { final idx = _messages.indexWhere((m) => m.id == msg.id); if (idx != -1) _messages[idx] = updated; }); } catch (e) { if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Edit failed: $e"))); } }, child: const Text("Save")),
    ]));
  }

  // ============================================================
  // 🔥 NAYA: MEDIA DOWNLOAD (with WhatsApp-jaisa "Open" state)
  // ============================================================
  bool _isDownloaded(MessageModel msg) => _downloadedIds.contains(msg.id);

  DownloadKind _kindOf(MessageModel msg) => msg.type == MessageType.image
      ? DownloadKind.image
      : msg.type == MessageType.video
          ? DownloadKind.video
          : DownloadKind.other;

  /// App khulte hi ek baar, chat history ke saare doc/file/presentation
  /// messages check karta hai ki wo pehle se device pe maujood hain kya —
  /// agar haan, to bina dobara download kiye seedha "Open" dikhega.
  Future<void> _scanAlreadyDownloaded() async {
    for (final msg in _messages) {
      final url = msg.fileUrl;
      if (url == null || url.isEmpty) continue;
      if (_downloadCheckedIds.contains(msg.id)) continue;
      if (_kindOf(msg) != DownloadKind.other) continue; // image/video ke liye session-only tracking
      _downloadCheckedIds.add(msg.id);
      final fileName = msg.meta?['file_name']?.toString() ?? url.split('/').last.split('?').first;
      final existing = await MediaDownloadService.alreadyDownloadedPath(fileName);
      if (existing != null && mounted) {
        setState(() {
          _downloadedIds.add(msg.id);
          _downloadedPaths[msg.id] = existing;
        });
      }
    }
  }

  /// Image -> gallery. Video -> gallery. File/audio/presentation ->
  /// device ke public "Download/LearnScroll" folder me.
  /// Pehle se downloaded hai to dobara download NAHI hota — seedha "Open"
  /// ho jaata hai, bilkul WhatsApp jaisa.
  Future<void> _downloadMedia(BuildContext context, MessageModel msg) async {
    final url = msg.fileUrl;
    if (url == null || url.isEmpty) return;
    await _downloadMediaUrl(context, msg, url);
  }

  /// 🔥 NAYA: `_downloadMedia` jaisa hi, lekin ek specific URL ke liye —
  /// multi-image message (`file_urls[]`) me se koi bhi EK photo download
  /// karne ke kaam aata hai (poore message ka ek hi `fileUrl` nahi hota).
  Future<void> _downloadMediaUrl(BuildContext context, MessageModel msg, String url) async {
    if (url.isEmpty) return;

    // 🔥 NAYA: multi-image message me sab photos ka apna alag downloaded
    // state hona chahiye, isliye single-image message me `msg.id` aur
    // multi-image message me `msg.id + url` — dono cases me correct key.
    final trackingId = (msg.fileUrls != null && msg.fileUrls!.length > 1) ? '${msg.id}::$url' : msg.id;

    final kind = _kindOf(msg);
    final fileName = msg.meta?['file_name']?.toString() ?? url.split('/').last.split('?').first;

    // 🔥 Pehle se downloaded? seedha open karo, progress dialog bhi mat dikhao.
    if (kind != DownloadKind.image && kind != DownloadKind.video) {
      final existing = _downloadedPaths[trackingId] ?? await MediaDownloadService.alreadyDownloadedPath(fileName);
      if (existing != null) {
        if (mounted && !_downloadedIds.contains(trackingId)) {
          setState(() {
            _downloadedIds.add(trackingId);
            _downloadedPaths[trackingId] = existing;
          });
        }
        await MediaDownloadService.openFile(existing);
        return;
      }
    } else if (_downloadedIds.contains(trackingId)) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Pehle se gallery me save hai ✅")));
      }
      return;
    }

    final progressNotifier = ValueNotifier<double>(0);
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (_) => AlertDialog(
        content: ValueListenableBuilder<double>(
          valueListenable: progressNotifier,
          builder: (_, value, __) => Row(children: [
            CircularProgressIndicator(value: value > 0 ? value : null),
            const SizedBox(width: 16),
            const Text("Downloading..."),
          ]),
        ),
      ),
    );

    try {
      final path = await MediaDownloadService.download(
        url: url,
        kind: kind,
        fileName: fileName,
        onProgress: (p) => progressNotifier.value = p,
      );
      if (context.mounted) Navigator.pop(context); // progress dialog band

      final isGalleryMedia = kind == DownloadKind.image || kind == DownloadKind.video;
      if (mounted) {
        setState(() {
          _downloadedIds.add(trackingId);
          if (!isGalleryMedia) _downloadedPaths[trackingId] = path;
        });
      }
      // 🔥 HATAYA — pehle yahan se `PushNotificationService.showDownloadCompleteNotification()`
      // call hoti thi taaki download poora hone par system notification
      // bhi dikhe. Ab app-wide notification pehle se hi laga di gayi
      // hai (kisi aur jagah se fire hoti hai), isliye yahan se duplicate
      // trigger hata diya — warna ek hi download pe 2 notification aate.
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(isGalleryMedia ? "Gallery me save ho gaya ✅" : "Downloaded ✅ — Download/LearnScroll folder me"),
          action: isGalleryMedia ? null : SnackBarAction(label: "Open", onPressed: () => MediaDownloadService.openFile(path)),
        ));
      }
    } catch (e) {
      if (context.mounted) Navigator.pop(context);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Download failed: $e")));
      }
    }
  }

  @override
  void dispose() {
    // 🔥 NAYA: chat band ho rahi hai to "currently open" flag hatao —
    // taaki ab is conversation ke naye message pe push notification popup aaye.
    if (PushNotificationService.currentOpenConversationId == widget.conversation.id) {
      PushNotificationService.currentOpenConversationId = null;
    }
    _socket.dispose();
    _textController.dispose();
    _scrollController.dispose();
    _recordTimer?.cancel();
    _recorder.dispose(); // 🔥 NAYA
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final subtitle = _presenceSubtitle();
    return Scaffold(
      backgroundColor: const Color(0xFFECE5DD), // 🔥 NAYA — WhatsApp jaisa warm chat background base
      appBar: AppBar(
        backgroundColor: const Color(0xFF030F27),
        iconTheme: const IconThemeData(color: Colors.white),
        titleSpacing: 0,
        title: Row(children: [
          Stack(clipBehavior: Clip.none, children: [
            CircleAvatar(radius: 18, backgroundColor: Colors.grey[300], backgroundImage: widget.conversation.displayPhoto != null && widget.conversation.displayPhoto!.isNotEmpty ? CachedNetworkImageProvider(widget.conversation.displayPhoto!) : null, child: widget.conversation.displayPhoto == null || widget.conversation.displayPhoto!.isEmpty ? Icon(widget.conversation.isGroup ? Icons.group : Icons.person, color: Colors.grey[600], size: 18) : null),
            // 🔥 NAYA — online hone par avatar pe green dot
            if (!widget.conversation.isGroup && _otherOnline)
              Positioned(right: -1, bottom: -1, child: Container(width: 11, height: 11, decoration: BoxDecoration(color: const Color(0xFF25D366), shape: BoxShape.circle, border: Border.all(color: const Color(0xFF030F27), width: 2)))),
          ]),
          const SizedBox(width: 10),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            Text(widget.conversation.displayTitle, style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.bold)),
            if (subtitle != null) Text(subtitle, style: TextStyle(color: _otherTyping ? const Color(0xFF25D366) : Colors.white70, fontSize: 11.5)),
          ])),
        ]),
        actions: [
          IconButton(icon: const Icon(Icons.cast_for_education, color: Colors.white), tooltip: "Study Room", onPressed: _openStudyRoom),
          IconButton(icon: const Icon(Icons.call, color: Colors.white), tooltip: "Audio Call", onPressed: () => _startCall('audio')),
          IconButton(icon: const Icon(Icons.videocam, color: Colors.white), tooltip: "Video Call", onPressed: () => _startCall('video')),
          const SizedBox(width: 6),
        ],
      ),
      // 🔥 NAYA — halka doodle-pattern wallpaper, WhatsApp jaisa flat rang nahi
      body: Stack(children: [
        Positioned.fill(child: CustomPaint(painter: _ChatWallpaperPainter())),
        Column(children: [Expanded(child: _buildMessageList()), _buildReplyPreview(), _buildInputBar()]),
      ]),
    );
  }

  // 🔥 NAYA — reply-compose preview jo input bar ke upar dikhta hai
  Widget _buildReplyPreview() {
    if (_replyingTo == null) return const SizedBox.shrink();
    final msg = _replyingTo!;
    final isMe = msg.sender?.id == _myUserId;
    return Container(
      margin: const EdgeInsets.fromLTRB(10, 6, 10, 0),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8), border: const Border(left: BorderSide(color: Color(0xFF25D366), width: 4))),
      child: Row(children: [
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          Text(isMe ? "You" : (msg.sender?.displayName ?? ''), style: const TextStyle(color: Color(0xFF25D366), fontWeight: FontWeight.bold, fontSize: 12.5)),
          Text(_replyPreviewText(msg), maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: Colors.black54, fontSize: 12.5)),
        ])),
        IconButton(icon: const Icon(Icons.close, size: 18, color: Colors.black45), onPressed: _cancelReply, padding: EdgeInsets.zero, constraints: const BoxConstraints()),
      ]),
    );
  }

  String _replyPreviewText(MessageModel msg) {
    switch (msg.type) {
      case MessageType.image: return "📷 Photo";
      case MessageType.video: return "🎥 Video";
      case MessageType.audio: return "🎵 Audio";
      case MessageType.file: return "📄 File";
      case MessageType.presentation: return "📊 Presentation";
      case MessageType.location: return "📍 Location";
      case MessageType.studyRoom: return "🧑‍🎓 Study Room";
      default: return msg.text ?? '';
    }
  }

  Widget _buildMessageList() {
    if (_isLoading) return const Center(child: CircularProgressIndicator());
    if (_messages.isEmpty) return const Center(child: Text("Say hi 👋", style: TextStyle(color: Colors.black45)));
    // typing indicator ko list ke end me ek extra "item" ki tarah treat karte hain
    final itemCount = _messages.length + (_otherTyping ? 1 : 0);
    return ListView.builder(
      controller: _scrollController,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
      itemCount: itemCount,
      itemBuilder: (context, index) {
        if (_otherTyping && index == _messages.length) {
          return const _TypingBubble(); // 🔥 NAYA — animated 3-dot bubble
        }
        final msg = _messages[index];
        final isMe = msg.sender?.id == _myUserId;

        // 🔥 NAYA — date separator: pichle message se din badal gaya to divider dikhao
        final prev = index > 0 ? _messages[index - 1] : null;
        final showDateSeparator = prev == null || !_isSameDay(prev.createdAt, msg.createdAt);

        // 🔥 NAYA — consecutive grouping (bubble tail sirf group ke last message pe)
        final next = index < _messages.length - 1 ? _messages[index + 1] : null;
        final isLastInGroup = next == null || next.sender?.id != msg.sender?.id || !_isSameDay(next.createdAt, msg.createdAt);
        final isFirstInGroup = prev == null || prev.sender?.id != msg.sender?.id || showDateSeparator;

        final replyPreview = _findMessageById(msg.replyTo);

        return Column(children: [
          if (showDateSeparator) _DateSeparator(date: msg.createdAt),
          _SwipeToReply(
            isMe: isMe,
            onReply: () => _startReply(msg),
            child: _MessageBubble(
              message: msg,
              isMe: isMe,
              isLastInGroup: isLastInGroup,
              isFirstInGroup: isFirstInGroup,
              replyPreview: replyPreview,
              isReadByOther: _readByOtherIds.contains(msg.id),
              isDownloaded: _isDownloaded(msg), // 🔥 NAYA
              onLongPress: () => _showMessageActions(msg, isMe),
              onReactionTap: () => _showReactionPicker(msg),
              onDownload: () => _downloadMedia(context, msg),
              onDownloadUrl: (url) => _downloadMediaUrl(context, msg, url), // 🔥 NAYA — multi-image grid ke ek specific photo ke liye
              isUrlDownloaded: (url) => _downloadedIds.contains('${msg.id}::$url'), // 🔥 NAYA
              onReplyTap: replyPreview != null ? () => _scrollToMessage(replyPreview.id) : null,
              onJoinStudyRoom: _enterStudyRoom, // 🔥 NAYA — card pe tap = seedha room me entry, dobara invite nahi
            ),
          ),
        ]);
      },
    );
  }

  bool _isSameDay(DateTime a, DateTime b) => a.year == b.year && a.month == b.month && a.day == b.day;

  // 🔥 NAYA — quoted reply pe tap karke us original message tak scroll/highlight
  void _scrollToMessage(String id) {
    final idx = _messages.indexWhere((m) => m.id == id);
    if (idx == -1 || !_scrollController.hasClients) return;
    // approx: har message ~70px, list top se offset nikaal ke scroll karo
    final approxOffset = (idx * 70.0).clamp(0.0, _scrollController.position.maxScrollExtent);
    _scrollController.animateTo(approxOffset, duration: const Duration(milliseconds: 300), curve: Curves.easeOut);
  }

  String _fmtRecordDuration(Duration d) {
    final m = d.inMinutes.toString().padLeft(2, '0');
    final s = (d.inSeconds % 60).toString().padLeft(2, '0');
    return "$m:$s";
  }

  Widget _buildInputBar() {
    // 🔥 NAYA — recording chal rahi ho to poora bar ek "Slide to cancel"
    // jaisa recording indicator ban jaata hai (WhatsApp jaisa).
    if (_isRecording) {
      return SafeArea(child: Padding(padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8), child: Row(children: [
        IconButton(icon: const Icon(Icons.delete_outline, color: Colors.red), onPressed: _cancelRecording),
        Expanded(child: Row(children: [
          const Icon(Icons.fiber_manual_record, color: Colors.red, size: 14),
          const SizedBox(width: 8),
          Text(_fmtRecordDuration(_recordDuration), style: const TextStyle(fontSize: 15, color: Color(0xFF030F27))),
          const SizedBox(width: 8),
          const Text("Recording...", style: TextStyle(color: Colors.black45, fontSize: 13)),
        ])),
        const SizedBox(width: 8),
        CircleAvatar(backgroundColor: const Color(0xFF030F27), child: IconButton(icon: const Icon(Icons.send, color: Colors.white), onPressed: _stopRecordingAndSend)),
      ])));
    }

    return SafeArea(child: Padding(padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8), child: Row(children: [
      IconButton(icon: const Icon(Icons.attach_file, color: Color(0xFF030F27)), onPressed: _showAttachmentSheet),
      // 🔥 NAYA — animated sticker picker (Emotions + Funny). Tap karte hi
      // chosen sticker ka emoji seedha message ki tarah bhej diya jaata hai.
      IconButton(
        icon: const Icon(Icons.emoji_emotions_outlined, color: Color(0xFF030F27)),
        onPressed: () => showStickerPicker(
          context,
          onSelected: (emoji) {
            setState(() => _textController.text = emoji);
            _sendMessage();
          },
        ),
      ),
      Expanded(child: TextField(controller: _textController, onChanged: _onTypingChanged, minLines: 1, maxLines: 4, decoration: InputDecoration(hintText: "Message...", filled: true, fillColor: Colors.white, contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10), border: OutlineInputBorder(borderRadius: BorderRadius.circular(25), borderSide: BorderSide.none)))),
      const SizedBox(width: 8),
      // 🔥 NAYA — WhatsApp jaisa hi: text khaali ho to "mic" (voice note),
      // kuch type kiya ho to "send" — ValueListenableBuilder se text
      // controller change hote hi ye button khud switch ho jaata hai.
      ValueListenableBuilder<TextEditingValue>(
        valueListenable: _textController,
        builder: (_, value, __) {
          final hasText = value.text.trim().isNotEmpty;
          return CircleAvatar(
            backgroundColor: const Color(0xFF030F27),
            child: IconButton(
              icon: Icon(hasText ? Icons.send : Icons.mic, color: Colors.white),
              onPressed: hasText ? _sendMessage : _startRecording,
            ),
          );
        },
      ),
    ])));
  }
}

// 🔥 NAYA — WhatsApp jaisa swipe-to-reply: bubble ko thoda drag karo,
// reply icon reveal hota hai, threshold cross karte hi reply mode trigger.
class _SwipeToReply extends StatefulWidget {
  final Widget child;
  final bool isMe;
  final VoidCallback onReply;
  const _SwipeToReply({required this.child, required this.isMe, required this.onReply});

  @override
  State<_SwipeToReply> createState() => _SwipeToReplyState();
}

class _SwipeToReplyState extends State<_SwipeToReply> with SingleTickerProviderStateMixin {
  double _dragX = 0;
  static const double _maxDrag = 56;
  bool _triggered = false;

  void _onDragUpdate(DragUpdateDetails d) {
    setState(() {
      // isMe (outgoing) bubble ko left ki taraf swipe karo, incoming ko right
      final delta = widget.isMe ? -d.delta.dx : d.delta.dx;
      _dragX = (_dragX + delta).clamp(0.0, _maxDrag + 20);
      if (_dragX >= _maxDrag && !_triggered) {
        _triggered = true;
      }
    });
  }

  void _onDragEnd(DragEndDetails d) {
    if (_dragX >= _maxDrag) widget.onReply();
    setState(() { _dragX = 0; _triggered = false; });
  }

  @override
  Widget build(BuildContext context) {
    final offset = widget.isMe ? -_dragX : _dragX;
    return GestureDetector(
      onHorizontalDragUpdate: _onDragUpdate,
      onHorizontalDragEnd: _onDragEnd,
      child: Stack(alignment: widget.isMe ? Alignment.centerRight : Alignment.centerLeft, children: [
        if (_dragX > 4)
          Opacity(
            opacity: (_dragX / _maxDrag).clamp(0.0, 1.0),
            child: const Padding(padding: EdgeInsets.symmetric(horizontal: 6), child: Icon(Icons.reply, color: Colors.black38, size: 20)),
          ),
        Transform.translate(offset: Offset(offset, 0), child: widget.child),
      ]),
    );
  }
}

// 🔥 NAYA — "Today / Yesterday / dd Mon yyyy" divider between message groups
class _DateSeparator extends StatelessWidget {
  final DateTime date;
  const _DateSeparator({required this.date});

  String _label() {
    final now = DateTime.now();
    final d = date.toLocal();
    final today = DateTime(now.year, now.month, now.day);
    final that = DateTime(d.year, d.month, d.day);
    final diff = today.difference(that).inDays;
    if (diff == 0) return "Today";
    if (diff == 1) return "Yesterday";
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return "${d.day} ${months[d.month - 1]} ${d.year}";
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Center(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
          decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8), boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 2)]),
          child: Text(_label(), style: const TextStyle(fontSize: 12, color: Colors.black54, fontWeight: FontWeight.w500)),
        ),
      ),
    );
  }
}

// 🔥 NAYA — animated 3-dot "typing..." bubble jaisa WhatsApp/Messenger me hota hai
class _TypingBubble extends StatefulWidget {
  const _TypingBubble();
  @override
  State<_TypingBubble> createState() => _TypingBubbleState();
}

class _TypingBubbleState extends State<_TypingBubble> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 1000))..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(top: 4, bottom: 4),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(14), boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 1.5, offset: const Offset(0, 1))]),
        child: AnimatedBuilder(
          animation: _controller,
          builder: (_, __) => Row(mainAxisSize: MainAxisSize.min, children: List.generate(3, (i) {
            final t = ((_controller.value - i * 0.2) % 1.0);
            final scale = t < 0.5 ? 0.6 + t : 1.1 - t;
            return Padding(padding: const EdgeInsets.symmetric(horizontal: 2), child: Transform.scale(scale: scale.clamp(0.6, 1.0), child: Container(width: 7, height: 7, decoration: const BoxDecoration(color: Colors.black38, shape: BoxShape.circle))));
          })),
        ),
      ),
    );
  }
}

// 🔥 NAYA — halka repeating doodle pattern, flat color se zyada "app jaisa" feel
class _ChatWallpaperPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..color = const Color(0xFFD9D0C7).withOpacity(0.35)..style = PaintingStyle.fill;
    const step = 44.0;
    for (double y = 0; y < size.height; y += step) {
      for (double x = 0; x < size.width; x += step) {
        final offsetX = (y ~/ step) % 2 == 0 ? 0.0 : step / 2;
        canvas.drawCircle(Offset(x + offsetX + 6, y + 6), 1.6, paint);
      }
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class _MessageBubble extends StatelessWidget {
  final MessageModel message;
  final bool isMe;
  final bool isLastInGroup; // 🔥 NAYA — tail sirf group ke aakhri bubble pe
  final bool isFirstInGroup; // 🔥 NAYA — group ke pehle bubble pe thoda zyada top margin
  final MessageModel? replyPreview; // 🔥 NAYA — jis message ka reply hai, uska data
  final bool isReadByOther; // 🔥 NAYA — blue tick ke liye
  final bool isDownloaded; // 🔥 NAYA — true ho to icon/label "Open" dikhayenge, dobara "Download" nahi
  final VoidCallback onLongPress;
  final VoidCallback onReactionTap;
  final VoidCallback onDownload;
  final void Function(String url)? onDownloadUrl; // 🔥 NAYA — multi-image message me ek specific photo download karne ke liye
  final bool Function(String url)? isUrlDownloaded; // 🔥 NAYA
  final VoidCallback? onReplyTap; // 🔥 NAYA
  final VoidCallback? onJoinStudyRoom; // 🔥 NAYA — study room invite card ke "Tap to Join" ke liye
  const _MessageBubble({
    required this.message,
    required this.isMe,
    this.isLastInGroup = true,
    this.isFirstInGroup = true,
    this.replyPreview,
    this.isReadByOther = false,
    this.isDownloaded = false,
    required this.onLongPress,
    required this.onReactionTap,
    required this.onDownload,
    this.onDownloadUrl,
    this.isUrlDownloaded,
    this.onReplyTap,
    this.onJoinStudyRoom,
  });

  @override
  Widget build(BuildContext context) {
    if (message.deletedForMe) return const SizedBox.shrink();
    final bubbleColor = isMe ? const Color(0xFF075E54) : Colors.white; // 🔥 WhatsApp jaisa dark-teal sent bubble
    final textColor = isMe ? Colors.white : Colors.black87;

    // 🔥 NAYA — bubble tail: last-in-group bubble ka ek corner chhota
    // (~4px) rehta hai, jaisa WhatsApp me "pointer" hota hai.
    final radius = BorderRadius.only(
      topLeft: const Radius.circular(14),
      topRight: const Radius.circular(14),
      bottomLeft: Radius.circular(isMe || !isLastInGroup ? 14 : 3),
      bottomRight: Radius.circular(isMe && isLastInGroup ? 3 : 14),
    );

    return GestureDetector(
      onLongPress: message.deletedForEveryone ? null : onLongPress,
      child: Align(
        alignment: isMe ? Alignment.centerRight : Alignment.centerLeft,
        child: Column(crossAxisAlignment: isMe ? CrossAxisAlignment.end : CrossAxisAlignment.start, children: [
          Container(
            margin: EdgeInsets.only(top: isFirstInGroup ? 6 : 2),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
            constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.75),
            decoration: BoxDecoration(color: bubbleColor, borderRadius: radius, boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 1.5, offset: const Offset(0, 1))]),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
              // 🔥 NAYA — quoted reply preview, tap karke original tak jump
              if (replyPreview != null) _buildReplyQuote(context, textColor),
              Padding(padding: const EdgeInsets.symmetric(horizontal: 4), child: _buildContent(context, textColor)),
              const SizedBox(height: 2),
              Padding(
                padding: const EdgeInsets.only(right: 2, left: 4),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  if (message.isEdited) Text("edited ", style: TextStyle(fontSize: 10, color: isMe ? Colors.white60 : Colors.grey)),
                  Text("${message.createdAt.hour.toString().padLeft(2, '0')}:${message.createdAt.minute.toString().padLeft(2, '0')}", style: TextStyle(fontSize: 10.5, color: isMe ? Colors.white60 : Colors.grey)),
                  if (isMe) ...[const SizedBox(width: 3), _buildTick()],
                ]),
              ),
            ]),
          ),
          if (message.reactions.isNotEmpty) _buildReactionRow(),
        ]),
      ),
    );
  }

  // 🔥 NAYA — WhatsApp jaisa tick logic: clock = sending, ek grey tick =
  // sent, 2 blue tick = read. Failed pe red "!" icon.
  Widget _buildTick() {
    if (message.sendFailed) return const Icon(Icons.error_outline, size: 13, color: Colors.redAccent);
    if (message.isSending) return const Icon(Icons.access_time, size: 12, color: Colors.white60);
    if (isReadByOther) return const Icon(Icons.done_all, size: 15, color: Color(0xFF34B7F1)); // blue double tick
    return const Icon(Icons.done, size: 14, color: Colors.white60); // single grey tick = sent
  }

  // 🔥 NAYA — reply karte hue jis message ko quote kiya, uska preview
  Widget _buildReplyQuote(BuildContext context, Color textColor) {
    final r = replyPreview!;
    String preview;
    switch (r.type) {
      case MessageType.image: preview = "📷 Photo"; break;
      case MessageType.video: preview = "🎥 Video"; break;
      case MessageType.audio: preview = "🎵 Audio"; break;
      case MessageType.file: preview = "📄 File"; break;
      case MessageType.presentation: preview = "📊 Presentation"; break;
      case MessageType.location: preview = "📍 Location"; break;
      case MessageType.studyRoom: preview = "🧑‍🎓 Study Room"; break;
      default: preview = r.text ?? '';
    }
    return GestureDetector(
      onTap: onReplyTap,
      child: Container(
        margin: const EdgeInsets.only(bottom: 5),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        decoration: BoxDecoration(color: textColor == Colors.white ? Colors.white.withOpacity(0.12) : Colors.black.withOpacity(0.05), borderRadius: BorderRadius.circular(6), border: const Border(left: BorderSide(color: Color(0xFF25D366), width: 3))),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          Text(r.sender?.displayName ?? '', style: const TextStyle(color: Color(0xFF25D366), fontWeight: FontWeight.bold, fontSize: 11.5)),
          Text(preview, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: textColor.withOpacity(0.75), fontSize: 12)),
        ]),
      ),
    );
  }

  Widget _buildReactionRow() { final counts = <String, int>{}; for (final r in message.reactions) { counts[r.emoji] = (counts[r.emoji] ?? 0) + 1; } return GestureDetector(onTap: onReactionTap, child: Container(margin: const EdgeInsets.only(top: 2), padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3), decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12), boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 3)]), child: Row(mainAxisSize: MainAxisSize.min, children: counts.entries.map((e) => Padding(padding: const EdgeInsets.symmetric(horizontal: 2), child: Text("${e.key} ${e.value > 1 ? e.value : ''}", style: const TextStyle(fontSize: 12)))).toList()))); }

  Widget _buildContent(BuildContext context, Color textColor) {
    if (message.deletedForEveryone) return Text("This message was deleted", style: TextStyle(color: Colors.grey, fontStyle: FontStyle.italic, fontSize: 14.5));

    Widget media;
    switch (message.type) {
      case MessageType.image: media = _imageContent(context, textColor); break;
      case MessageType.video: media = _videoContent(context, textColor); break;
      case MessageType.audio: return _AudioBubble(message: message, textColor: textColor, onDownload: onDownload);
      case MessageType.presentation: media = _fileLikeContent(context, textColor, Icons.slideshow, "Presentation"); break;
      case MessageType.file: media = _fileLikeContent(context, textColor, Icons.insert_drive_file, "File"); break;
      case MessageType.location: return _locationContent(context, textColor);
      case MessageType.studyRoom: return _studyRoomCard(context); // 🔥 NAYA
      default: return Text(message.text ?? '', style: TextStyle(color: textColor, fontSize: 14.5));
    }

    // 🔥 NAYA: media ke saath caption ho (gallery-preview screen se) to
    // WhatsApp jaisa hi media ke neeche caption text dikhta hai.
    final caption = message.text?.trim();
    if (caption == null || caption.isEmpty) return media;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
      media,
      Padding(
        padding: const EdgeInsets.only(top: 5, left: 2, right: 2),
        child: Text(caption, style: TextStyle(color: textColor, fontSize: 14.5)),
      ),
    ]);
  }

  // 🔥 NAYA — chat me dikhne wala clickable "Study Room" invite block.
  // Design: gradient card + "Tap to Join" pill, WhatsApp/Discord ke
  // "activity invite" cards jaisa. Tap se seedha room me entry.
  Widget _studyRoomCard(BuildContext context) {
    return GestureDetector(
      onTap: onJoinStudyRoom,
      child: Container(
        width: 220,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            colors: [Color(0xFF4E54C8), Color(0xFF8F94FB)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
          borderRadius: BorderRadius.circular(14),
          boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.15), blurRadius: 6, offset: const Offset(0, 3))],
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: const [
                Icon(Icons.school, color: Colors.white, size: 22),
                SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'Study Room',
                    style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            const Text(
              'Whiteboard, timer aur live call — sab ek jagah.',
              style: TextStyle(color: Colors.white70, fontSize: 12),
            ),
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 8),
              decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(20)),
              alignment: Alignment.center,
              child: const Text(
                'Tap to Join',
                style: TextStyle(color: Color(0xFF4E54C8), fontWeight: FontWeight.bold, fontSize: 13),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // 🔥 NAYA: image ab tap pe fullscreen preview + long-press pe download.
  // Agar message me EK se zyada photos hain (`file_urls[]` — user ne
  // gallery se multiple select karke ek saath bheji thi), to WhatsApp
  // jaisa hi album-grid dikhta hai, ek single image ke bajaye.
  Widget _imageContent(BuildContext context, Color textColor) {
    final urls = (message.fileUrls != null && message.fileUrls!.isNotEmpty)
        ? message.fileUrls!.map((e) => e.toString()).toList()
        : <String>[];
    final localPaths = message.localFilePaths ?? const <String>[];

    if (urls.length > 1 || localPaths.length > 1) {
      return _multiImageGrid(context, urls, localPaths);
    }

    final localPath = message.localFilePath;
    // 🔧 FIX — ROOT CAUSE of the white/blank thumbnail box: gallery se
    // (chahe EK hi photo kyun na ho) `_pickAndSendMultipleMedia()` se
    // hokar jaata hai, jo sirf `fileUrls` (list) set karta hai, singular
    // `fileUrl` kabhi nahi. Neeche wala single-image path pehle sirf
    // `message.fileUrl` dekhta tha — list-only message ke liye wo hamesha
    // null milta tha, isliye URL hi nahi milta tha aur placeholder icon
    // (safed box) dikh jaata tha. Ab agar singular `fileUrl` na ho to
    // `fileUrls` ke pehle item pe fallback karte hain.
    final url = (message.fileUrl != null && message.fileUrl!.isNotEmpty)
        ? message.fileUrl
        : (urls.isNotEmpty ? urls.first : null);
    Widget image;
    if (localPath != null && message.isSending) { image = Image.file(File(localPath), width: 200, height: 200, fit: BoxFit.cover); }
    else if (url != null && url.isNotEmpty) { image = CachedNetworkImage(imageUrl: url, width: 200, height: 200, fit: BoxFit.cover, placeholder: (_, __) => const SizedBox(width: 200, height: 200, child: Center(child: CircularProgressIndicator(strokeWidth: 2))), errorWidget: (_, __, ___) => const SizedBox(width: 200, height: 200, child: Icon(Icons.broken_image, color: Colors.grey))); }
    else { image = const SizedBox(width: 200, height: 200, child: Icon(Icons.image, color: Colors.grey)); }
    return ClipRRect(
      borderRadius: BorderRadius.circular(10),
      child: Stack(alignment: Alignment.center, children: [
        GestureDetector(
          // 🔧 FIX: pehle yahan Hero(tag: url) tha jo chat ke scrolling
          // ListView ke andar tha — jab source thumbnail off-screen ho
          // jaata (list scroll hone se lazily dispose ho jaata) ya same
          // url do jagah dikhta, Hero flight ka koi valid start point
          // nahi milta aur Flutter ise chhota karke screen ke top-left
          // corner se animate kar deta tha (wahi "chhoti hoke upar bhaag
          // jaana" wala bug). Ab Hero hata ke ek reliable fade+scale
          // transition use kar rahe hain jo hamesha sahi dikhta hai.
          onTap: url != null && !message.isSending ? () => Navigator.push(context, _fadeScaleRoute(_ImageViewerScreen(url: url, onDownload: onDownload))) : null,
          onLongPress: url != null && !message.isSending ? onDownload : null, // 🔥 NAYA
          child: image,
        ),
        // 🔥 FIX: pehle sirf indeterminate spinner dikhta tha — ab actual
        // upload % (agar available hai) dikhta hai, WhatsApp jaisa.
        if (message.isSending) Container(width: 200, height: 200, color: Colors.black26, child: Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
          CircularProgressIndicator(color: Colors.white, value: (message.uploadProgress != null && message.uploadProgress! > 0) ? message.uploadProgress : null),
          if (message.uploadProgress != null && message.uploadProgress! > 0) Padding(padding: const EdgeInsets.only(top: 6), child: Text("${(message.uploadProgress! * 100).toStringAsFixed(0)}%", style: const TextStyle(color: Colors.white, fontSize: 12))),
        ]))),
        // 🔥 NAYA: chhota download icon corner me — pehle se saved hai to
        // checkmark dikhega (WhatsApp jaisa), dobara download trigger nahi hota.
        if (url != null && !message.isSending)
          Positioned(
            bottom: 6, right: 6,
            child: GestureDetector(
              onTap: isDownloaded ? null : onDownload,
              child: Container(
                padding: const EdgeInsets.all(5),
                decoration: BoxDecoration(color: Colors.black54, shape: BoxShape.circle),
                child: Icon(isDownloaded ? Icons.check : Icons.download, color: Colors.white, size: 16),
              ),
            ),
          ),
      ]),
    );
  }

  // 🔥 NAYA: ek message ke andar bandled multiple photos — WhatsApp jaisa
  // 2x2 grid, 4 se zyada hone par 4th tile pe "+N" overlay. Tap karne pe
  // us photo se shuru hoke poore album ka fullscreen swipeable viewer
  // khulta hai; long-press us specific photo ko download karta hai.
  Widget _multiImageGrid(BuildContext context, List<String> urls, List<String> localPaths) {
    final count = urls.isNotEmpty ? urls.length : localPaths.length;
    const size = 200.0;
    const gap = 2.0;
    final tilesToShow = count > 4 ? 4 : count;

    Widget tileFor(int i) {
      Widget img;
      if (message.isSending && i < localPaths.length) {
        img = Image.file(File(localPaths[i]), fit: BoxFit.cover);
      } else if (i < urls.length) {
        img = CachedNetworkImage(
          imageUrl: urls[i],
          fit: BoxFit.cover,
          placeholder: (_, __) => Container(color: Colors.black12),
          errorWidget: (_, __, ___) => const Icon(Icons.broken_image, color: Colors.grey),
        );
      } else {
        img = Container(color: Colors.black12);
      }

      final isOverflowTile = i == 3 && count > 4;
      final canInteract = !message.isSending && urls.isNotEmpty;

      return GestureDetector(
        onTap: canInteract
            ? () => Navigator.push(context, _fadeScaleRoute(_MultiImageViewerScreen(
                  urls: urls,
                  initialIndex: i,
                  isDownloaded: isUrlDownloaded,
                  onDownload: onDownloadUrl,
                )))
            : null,
        onLongPress: canInteract && i < urls.length ? () => onDownloadUrl?.call(urls[i]) : null,
        child: Stack(fit: StackFit.expand, children: [
          img,
          if (isOverflowTile)
            Container(
              color: Colors.black54,
              alignment: Alignment.center,
              child: Text("+${count - 4}", style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.bold)),
            ),
        ]),
      );
    }

    return ClipRRect(
      borderRadius: BorderRadius.circular(10),
      child: SizedBox(
        width: size,
        height: size,
        child: Stack(children: [
          GridView.builder(
            physics: const NeverScrollableScrollPhysics(),
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 2, crossAxisSpacing: gap, mainAxisSpacing: gap,
            ),
            itemCount: tilesToShow,
            itemBuilder: (_, i) => tileFor(i),
          ),
          if (message.isSending)
            Positioned.fill(
              child: Container(
                color: Colors.black26,
                child: Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
                  CircularProgressIndicator(color: Colors.white, value: (message.uploadProgress != null && message.uploadProgress! > 0) ? message.uploadProgress : null),
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text(
                      message.uploadProgress != null && message.uploadProgress! > 0
                          ? "${(message.uploadProgress! * 100).toStringAsFixed(0)}% • $count photos"
                          : "$count photos",
                      style: const TextStyle(color: Colors.white, fontSize: 12),
                    ),
                  ),
                ])),
              ),
            ),
        ]),
      ),
    );
  }

  // 🔥 NAYA: tap ab direct download karta hai (external browser open karne
  // ke bajaye), download icon bhi dikhega
  Widget _fileLikeContent(BuildContext context, Color textColor, IconData icon, String label) {
    final fileName = message.meta?['file_name']?.toString() ?? label; final url = message.fileUrl;
    return GestureDetector(
      onTap: (url != null && url.isNotEmpty && !message.isSending) ? onDownload : null,
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Container(padding: const EdgeInsets.all(8), decoration: BoxDecoration(color: textColor == Colors.white ? Colors.white24 : Colors.grey[200], shape: BoxShape.circle), child: message.isSending ? SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: textColor, value: (message.uploadProgress != null && message.uploadProgress! > 0) ? message.uploadProgress : null)) : Icon(icon, color: textColor)),
        const SizedBox(width: 8),
        Flexible(child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          Text(fileName, style: TextStyle(color: textColor, fontSize: 14), overflow: TextOverflow.ellipsis),
          // 🔥 NAYA: sending ke dauraan "42% uploading..." dikhega
          if (message.isSending && message.uploadProgress != null && message.uploadProgress! > 0)
            Text("${(message.uploadProgress! * 100).toStringAsFixed(0)}% uploading...", style: TextStyle(color: textColor.withOpacity(0.7), fontSize: 11)),
        ])),
        if (!message.isSending && url != null && url.isNotEmpty) ...[
          const SizedBox(width: 6),
          Icon(isDownloaded ? Icons.open_in_new : Icons.download, size: 16, color: textColor.withOpacity(0.6)),
        ],
      ]),
    );
  }

  // 🔥 NAYA: video ab WhatsApp jaisa hi — thumbnail pe play button, tap
  // karte hi seedha app ke andar hi chalta hai (download hone ka wait
  // nahi karna padta). Long-press se device pe save kar sakte ho.
  Widget _videoContent(BuildContext context, Color textColor) {
    final url = message.fileUrl;
    final thumb = message.thumbnailUrl;
    if (message.isSending) {
      return SizedBox(
        width: 200, height: 130,
        child: Stack(alignment: Alignment.center, children: [
          Container(color: Colors.black26),
          Column(mainAxisSize: MainAxisSize.min, children: [
            CircularProgressIndicator(color: Colors.white, value: (message.uploadProgress != null && message.uploadProgress! > 0) ? message.uploadProgress : null),
            if (message.uploadProgress != null && message.uploadProgress! > 0) Padding(padding: const EdgeInsets.only(top: 6), child: Text("${(message.uploadProgress! * 100).toStringAsFixed(0)}%", style: const TextStyle(color: Colors.white, fontSize: 12))),
          ]),
        ]),
      );
    }
    if (url == null || url.isEmpty) {
      return const SizedBox(width: 200, height: 130, child: Icon(Icons.videocam, color: Colors.grey));
    }
    return ClipRRect(
      borderRadius: BorderRadius.circular(10),
      child: GestureDetector(
        onTap: () => Navigator.push(context, _fadeScaleRoute(_VideoPlayerScreen(url: url))),
        onLongPress: onDownload,
        child: SizedBox(
          width: 200, height: 130,
          child: Stack(alignment: Alignment.center, fit: StackFit.expand, children: [
            thumb != null && thumb.isNotEmpty
                ? CachedNetworkImage(imageUrl: thumb, fit: BoxFit.cover, errorWidget: (_, __, ___) => Container(color: Colors.black87))
                : Container(color: Colors.black87),
            Container(color: Colors.black26),
            Container(padding: const EdgeInsets.all(10), decoration: const BoxDecoration(color: Colors.black45, shape: BoxShape.circle), child: const Icon(Icons.play_arrow, color: Colors.white, size: 30)),
          ]),
        ),
      ),
    );
  }

  Widget _locationContent(BuildContext context, Color textColor) {
    final lat = message.meta?['lat']; final lng = message.meta?['lng'];
    return GestureDetector(onTap: (lat != null && lng != null) ? () => launchUrl(Uri.parse("https://maps.google.com/?q=$lat,$lng"), mode: LaunchMode.externalApplication) : null, child: Row(mainAxisSize: MainAxisSize.min, children: [Icon(Icons.location_on, color: textColor), const SizedBox(width: 6), Text("Location shared", style: TextStyle(color: textColor, fontSize: 14))]));
  }
}

// ============================================================
// 🔥 NAYA: GALLERY MULTI-SELECT — REVIEW/PREVIEW SCREEN
// WhatsApp jaisa hi: gallery se ek saath kai photos/videos select karne ke
// baad seedha upload nahi hota — pehle ek dedicated "review" screen khulti
// hai jahan user:
//   • poore album ko swipe karke dekh sakta hai
//   • kisi bhi item ko cross (x) se list se hata sakta hai
//   • "+" tile se aur media add kar sakta hai
//   • ek caption likh sakta hai jo poore album ke saath jaata hai
//   • sirf "send" (➤) dabane par hi actual upload shuru hota hai
// ============================================================
class _MediaPreviewResult {
  final List<XFile> files;
  final String caption;
  _MediaPreviewResult(this.files, this.caption);
}

class _MediaPreviewScreen extends StatefulWidget {
  final List<XFile> files;
  const _MediaPreviewScreen({required this.files});

  @override
  State<_MediaPreviewScreen> createState() => _MediaPreviewScreenState();
}

class _MediaPreviewScreenState extends State<_MediaPreviewScreen> {
  static const _videoExtensions = {'mp4', 'mov', 'mkv', '3gp', 'webm', 'avi', 'm4v'};

  late List<XFile> _files;
  late PageController _pageController;
  int _index = 0;
  final TextEditingController _captionController = TextEditingController();

  bool _isVideo(XFile f) => _videoExtensions.contains(
      (f.name.isNotEmpty ? f.name : f.path).split('.').last.toLowerCase());

  @override
  void initState() {
    super.initState();
    _files = List.of(widget.files);
    _pageController = PageController();
  }

  @override
  void dispose() {
    _pageController.dispose();
    _captionController.dispose();
    super.dispose();
  }

  void _removeAt(int i) {
    if (_files.length == 1) {
      // Aakhri item hata diya to poori screen band karo — album khaali
      // nahi ho sakta.
      Navigator.pop(context);
      return;
    }
    setState(() {
      _files.removeAt(i);
      if (_index >= _files.length) _index = _files.length - 1;
      _pageController.jumpToPage(_index);
    });
  }

  Future<void> _addMore() async {
    final raw = await ImagePicker().pickMultipleMedia(imageQuality: 85);
    if (raw.isEmpty || !mounted) return;
    final more = await _ensureRealFiles(raw);
    if (!mounted) return;
    setState(() => _files.addAll(more));
  }

  void _send() {
    Navigator.pop(context, _MediaPreviewResult(_files, _captionController.text.trim()));
  }

  @override
  Widget build(BuildContext context) {
    if (_files.isEmpty) return const SizedBox.shrink();
    return Scaffold(
      backgroundColor: Colors.black,
      body: SafeArea(
        child: Column(children: [
          // ---- Top bar ----
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
            child: Row(children: [
              IconButton(icon: const Icon(Icons.close, color: Colors.white), onPressed: () => Navigator.pop(context)),
              const Spacer(),
              Text(
                _files.length == 1 ? "1 item" : "${_files.length} items",
                style: const TextStyle(color: Colors.white70, fontSize: 13, fontWeight: FontWeight.w500),
              ),
              const SizedBox(width: 14),
            ]),
          ),
          // ---- Main preview pane ----
          Expanded(
            child: PageView.builder(
              controller: _pageController,
              itemCount: _files.length,
              onPageChanged: (i) => setState(() => _index = i),
              itemBuilder: (_, i) {
                final f = _files[i];
                if (_isVideo(f)) {
                  return Center(
                    child: Stack(alignment: Alignment.center, children: [
                      Icon(Icons.videocam_rounded, color: Colors.white.withOpacity(0.15), size: 100),
                      Container(
                        padding: const EdgeInsets.all(16),
                        decoration: const BoxDecoration(color: Colors.black45, shape: BoxShape.circle),
                        child: const Icon(Icons.play_arrow, color: Colors.white, size: 40),
                      ),
                    ]),
                  );
                }
                return InteractiveViewer(
                  minScale: 1,
                  maxScale: 4,
                  // 🔧 FIX: SizedBox.expand — same reason as fullscreen
                  // viewers, taaki image poore available space me contain
                  // ho, chota block na dikhe.
                  child: SizedBox.expand(child: Image.file(File(f.path), fit: BoxFit.contain)),
                );
              },
            ),
          ),
          // ---- Thumbnail strip ----
          SizedBox(
            height: 72,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 10),
              itemCount: _files.length + 1,
              separatorBuilder: (_, __) => const SizedBox(width: 8),
              itemBuilder: (_, i) {
                if (i == _files.length) {
                  return GestureDetector(
                    onTap: _addMore,
                    child: Container(
                      width: 56,
                      height: 56,
                      decoration: BoxDecoration(color: Colors.white12, borderRadius: BorderRadius.circular(8), border: Border.all(color: Colors.white24)),
                      child: const Icon(Icons.add, color: Colors.white70),
                    ),
                  );
                }
                final f = _files[i];
                final selected = i == _index;
                return GestureDetector(
                  onTap: () => _pageController.animateToPage(i, duration: const Duration(milliseconds: 200), curve: Curves.easeOut),
                  child: SizedBox(
                    width: 56,
                    height: 56,
                    child: Stack(clipBehavior: Clip.none, children: [
                      Container(
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: selected ? const Color(0xFF9C27B0) : Colors.transparent, width: 2),
                        ),
                        clipBehavior: Clip.antiAlias,
                        child: _isVideo(f)
                            ? Container(color: Colors.grey[900], alignment: Alignment.center, child: const Icon(Icons.videocam, color: Colors.white54, size: 20))
                            : Image.file(File(f.path), fit: BoxFit.cover, width: 56, height: 56),
                      ),
                      Positioned(
                        top: -6,
                        right: -6,
                        child: GestureDetector(
                          onTap: () => _removeAt(i),
                          child: Container(
                            padding: const EdgeInsets.all(2),
                            decoration: const BoxDecoration(color: Colors.black87, shape: BoxShape.circle),
                            child: const Icon(Icons.close, color: Colors.white, size: 13),
                          ),
                        ),
                      ),
                    ]),
                  ),
                );
              },
            ),
          ),
          // ---- Caption + send ----
          Padding(
            padding: const EdgeInsets.fromLTRB(10, 8, 10, 10),
            child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
              Expanded(
                child: Container(
                  constraints: const BoxConstraints(minHeight: 44, maxHeight: 100),
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                  decoration: BoxDecoration(color: Colors.white12, borderRadius: BorderRadius.circular(22)),
                  child: TextField(
                    controller: _captionController,
                    minLines: 1,
                    maxLines: 4,
                    style: const TextStyle(color: Colors.white),
                    decoration: const InputDecoration(hintText: "Add a caption…", hintStyle: TextStyle(color: Colors.white38), border: InputBorder.none),
                  ),
                ),
              ),
              const SizedBox(width: 10),
              GestureDetector(
                onTap: _send,
                child: Container(
                  padding: const EdgeInsets.all(13),
                  decoration: const BoxDecoration(color: Color(0xFF9C27B0), shape: BoxShape.circle),
                  child: const Icon(Icons.send, color: Colors.white, size: 20),
                ),
              ),
            ]),
          ),
        ]),
      ),
    );
  }
}

// 🔥 NAYA: WhatsApp jaisa full-screen image viewer — pehle ye ek chhota
// Dialog tha (rounded corners, status bar area cover nahi karta tha, koi
// close/download button nahi tha). Ab poori screen black background ke
// saath, pinch-to-zoom, aur top pe close + download button.
// 🔧 FIX: Hero-based navigation ko yahan se replace kiya — ye ek chhota,
// self-contained fade+scale transition hai jo kabhi bhi "wrong corner se
// fly" nahi karta kyunki ye kisi doosre widget ki position/size pe depend
// nahi karta, bas simple fade-in + slight scale-up animation hai. Feel
// still smooth/native lagta hai but 100% reliable rehta hai chahe source
// thumbnail list me kahin bhi ho ya scroll ho chuka ho.
Route<T> _fadeScaleRoute<T>(Widget page) {
  return PageRouteBuilder<T>(
    opaque: true,
    barrierColor: Colors.black,
    transitionDuration: const Duration(milliseconds: 220),
    reverseTransitionDuration: const Duration(milliseconds: 180),
    pageBuilder: (_, __, ___) => page,
    transitionsBuilder: (_, animation, __, child) {
      final curved = CurvedAnimation(parent: animation, curve: Curves.easeOutCubic, reverseCurve: Curves.easeInCubic);
      return FadeTransition(
        opacity: curved,
        child: ScaleTransition(
          scale: Tween<double>(begin: 0.92, end: 1.0).animate(curved),
          child: child,
        ),
      );
    },
  );
}

class _ImageViewerScreen extends StatefulWidget {
  final String url;
  final VoidCallback onDownload;
  const _ImageViewerScreen({required this.url, required this.onDownload});

  @override
  State<_ImageViewerScreen> createState() => _ImageViewerScreenState();
}

class _ImageViewerScreenState extends State<_ImageViewerScreen> {
  final TransformationController _transform = TransformationController();
  double _dragOffset = 0;
  double _bgOpacity = 1;
  bool _zoomed = false;

  // 🔥 NAYA: double-tap se WhatsApp/Instagram jaisa zoom-in/zoom-out.
  void _onDoubleTapDown(TapDownDetails details, BuildContext ctx) {
    final tapPos = details.localPosition;
    if (_transform.value != Matrix4.identity()) {
      _transform.value = Matrix4.identity();
    } else {
      _transform.value = Matrix4.identity()
        ..translate(-tapPos.dx * 2, -tapPos.dy * 2)
        ..scale(3.0);
    }
  }

  @override
  void dispose() {
    _transform.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black.withOpacity(_bgOpacity),
      body: Stack(children: [
        // 🔧 FIX — ROOT CAUSE of "image chhoti si upar dikhti hai, baaki
        // black": pehle Positioned.fill > GestureDetector > Transform.translate
        // > InteractiveViewer > SizedBox.expand chain me size constraints
        // reliably poori screen tak nahi pahunch rahe the — InteractiveViewer
        // apne chhote/"natural" size par hi render ho raha tha. LayoutBuilder
        // se ab explicitly screen ke poore available width/height ko ek
        // tight SizedBox me le kar seedha InteractiveViewer ko diya jaata
        // hai — ab size ambiguity ki koi gunjaish nahi.
        Positioned.fill(
          child: LayoutBuilder(
            builder: (context, constraints) {
              return GestureDetector(
                onVerticalDragUpdate: _zoomed
                    ? null
                    : (details) {
                        setState(() {
                          _dragOffset += details.delta.dy;
                          _bgOpacity = (1 - (_dragOffset.abs() / 300)).clamp(0.15, 1.0);
                        });
                      },
                onVerticalDragEnd: _zoomed
                    ? null
                    : (details) {
                        if (_dragOffset.abs() > 120) {
                          Navigator.pop(context);
                        } else {
                          setState(() {
                            _dragOffset = 0;
                            _bgOpacity = 1;
                          });
                        }
                      },
                onDoubleTapDown: (d) => _onDoubleTapDown(d, context),
                onDoubleTap: () => setState(() {}),
                child: Transform.translate(
                  offset: Offset(0, _dragOffset),
                  child: SizedBox(
                    width: constraints.maxWidth,
                    height: constraints.maxHeight,
                    child: InteractiveViewer(
                      transformationController: _transform,
                      minScale: 1,
                      maxScale: 6,
                      boundaryMargin: const EdgeInsets.all(double.infinity),
                      onInteractionEnd: (_) => setState(() => _zoomed = _transform.value.getMaxScaleOnAxis() > 1.01),
                      child: SizedBox(
                        width: constraints.maxWidth,
                        height: constraints.maxHeight,
                        child: CachedNetworkImage(
                          imageUrl: widget.url,
                          fit: BoxFit.contain,
                          width: constraints.maxWidth,
                          height: constraints.maxHeight,
                          placeholder: (_, __) => const Center(child: CircularProgressIndicator(color: Colors.white)),
                          errorWidget: (_, __, ___) => const Center(child: Icon(Icons.broken_image, color: Colors.white38, size: 60)),
                        ),
                      ),
                    ),
                  ),
                ),
              );
            },
          ),
        ),
        SafeArea(
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              IconButton(icon: const Icon(Icons.close, color: Colors.white, size: 28), onPressed: () => Navigator.pop(context)),
              IconButton(icon: const Icon(Icons.download, color: Colors.white, size: 24), onPressed: widget.onDownload),
            ],
          ),
        ),
      ]),
    );
  }
}

// 🔥 NAYA: ek message me bandled multiple photos ke liye fullscreen
// swipeable viewer — WhatsApp jaisa hi "1/5" counter, pinch-to-zoom, aur
// har photo ka apna download button (kyunki poore album ka ek hi URL
// nahi hota, har photo alag download hoti hai).
class _MultiImageViewerScreen extends StatefulWidget {
  final List<String> urls;
  final int initialIndex;
  final void Function(String url)? onDownload;
  final bool Function(String url)? isDownloaded;
  const _MultiImageViewerScreen({
    required this.urls,
    required this.initialIndex,
    this.onDownload,
    this.isDownloaded,
  });

  @override
  State<_MultiImageViewerScreen> createState() => _MultiImageViewerScreenState();
}

class _MultiImageViewerScreenState extends State<_MultiImageViewerScreen> {
  late final PageController _controller;
  late int _index;

  @override
  void initState() {
    super.initState();
    _index = widget.initialIndex;
    _controller = PageController(initialPage: widget.initialIndex);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final currentUrl = widget.urls[_index];
    final downloaded = widget.isDownloaded?.call(currentUrl) ?? false;
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(children: [
        Positioned.fill(
          child: PageView.builder(
            controller: _controller,
            itemCount: widget.urls.length,
            onPageChanged: (i) => setState(() => _index = i),
            itemBuilder: (_, i) => _ZoomableImage(url: widget.urls[i]),
          ),
        ),
        SafeArea(
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              IconButton(icon: const Icon(Icons.close, color: Colors.white, size: 28), onPressed: () => Navigator.pop(context)),
              Text("${_index + 1} / ${widget.urls.length}", style: const TextStyle(color: Colors.white, fontSize: 15)),
              IconButton(
                icon: Icon(downloaded ? Icons.check : Icons.download, color: Colors.white, size: 24),
                onPressed: downloaded ? null : () => widget.onDownload?.call(currentUrl),
              ),
            ],
          ),
        ),
      ]),
    );
  }
}

// 🔥 NAYA: album-viewer ke har page ke liye — double-tap se zoom-in/out
// (Instagram/WhatsApp jaisa), pinch-zoom bhi supported.
class _ZoomableImage extends StatefulWidget {
  final String url;
  const _ZoomableImage({required this.url});

  @override
  State<_ZoomableImage> createState() => _ZoomableImageState();
}

class _ZoomableImageState extends State<_ZoomableImage> {
  final TransformationController _transform = TransformationController();

  void _onDoubleTapDown(TapDownDetails details) {
    final tapPos = details.localPosition;
    if (_transform.value != Matrix4.identity()) {
      _transform.value = Matrix4.identity();
    } else {
      _transform.value = Matrix4.identity()
        ..translate(-tapPos.dx * 2, -tapPos.dy * 2)
        ..scale(3.0);
    }
  }

  @override
  void dispose() {
    _transform.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        return GestureDetector(
          onDoubleTapDown: _onDoubleTapDown,
          onDoubleTap: () => setState(() {}),
          child: SizedBox(
            width: constraints.maxWidth,
            height: constraints.maxHeight,
            child: InteractiveViewer(
              transformationController: _transform,
              minScale: 1,
              maxScale: 6,
              boundaryMargin: const EdgeInsets.all(double.infinity),
              child: SizedBox(
                width: constraints.maxWidth,
                height: constraints.maxHeight,
                child: CachedNetworkImage(
                  imageUrl: widget.url,
                  fit: BoxFit.contain,
                  width: constraints.maxWidth,
                  height: constraints.maxHeight,
                  placeholder: (_, __) => const Center(child: CircularProgressIndicator(color: Colors.white)),
                  errorWidget: (_, __, ___) => const Center(child: Icon(Icons.broken_image, color: Colors.white38, size: 60)),
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}

// 🔥 NAYA: WhatsApp jaisa inline voice-note player — play/pause button,
// seekable progress bar, aur live time. Seedha URL se stream karta hai,
// download ka wait nahi karna padta. Sending state me upload % dikhta hai.
class _AudioBubble extends StatefulWidget {
  final MessageModel message;
  final Color textColor;
  final VoidCallback onDownload;
  const _AudioBubble({required this.message, required this.textColor, required this.onDownload});

  @override
  State<_AudioBubble> createState() => _AudioBubbleState();
}

class _AudioBubbleState extends State<_AudioBubble> {
  final AudioPlayer _player = AudioPlayer();
  PlayerState _state = PlayerState.stopped;
  Duration _position = Duration.zero;
  Duration _duration = Duration.zero;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    // 🔥 NAYA — FIX: audio hamesha LOUDSPEAKER (media/speaker) se play ho,
    // call/earpiece speaker se nahi. Bina isko set kiye, Android kabhi-kabhi
    // audio route ko earpiece pe bhej deta hai (khaaskar jab pehle koi call
    // ya WebRTC session chal chuki ho aur audio focus/route already earpiece
    // pe set ho). Ye explicitly speakerphone + media usage force karta hai.
    _player.setAudioContext(AudioContext(
      android: const AudioContextAndroid(
        isSpeakerphoneOn: true,
        stayAwake: false,
        contentType: AndroidContentType.music,
        usageType: AndroidUsageType.media,
        audioFocus: AndroidAudioFocus.gain,
      ),
      iOS: AudioContextIOS(
        // 🔧 FIX: `defaultToSpeaker` option sirf `playAndRecord` category
        // ke saath allowed hai — `playback` ke saath ye assertion fail
        // karke red error chat me dikha raha tha. `playback` category
        // already default speaker se play karta hai (earpiece se nahi),
        // isliye option ki zaroorat hi nahi thi.
        category: AVAudioSessionCategory.playback,
        options: const {},
      ),
    ));
    _player.onPlayerStateChanged.listen((s) {
      if (mounted) setState(() => _state = s);
    });
    _player.onPositionChanged.listen((p) {
      if (mounted) setState(() => _position = p);
    });
    _player.onDurationChanged.listen((d) {
      if (mounted) setState(() => _duration = d);
    });
    _player.onPlayerComplete.listen((_) {
      if (mounted) setState(() { _state = PlayerState.stopped; _position = Duration.zero; });
    });
  }

  Future<void> _togglePlay() async {
    final url = widget.message.fileUrl;
    if (url == null || url.isEmpty) return;
    if (_state == PlayerState.playing) {
      await _player.pause();
      return;
    }
    setState(() => _loading = true);
    try {
      if (_state == PlayerState.paused) {
        await _player.resume();
      } else {
        await _player.play(UrlSource(url));
      }
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Audio play nahi ho payi: $e")));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _fmt(Duration d) {
    final m = d.inMinutes.toString().padLeft(1, '0');
    final s = (d.inSeconds % 60).toString().padLeft(2, '0');
    return "$m:$s";
  }

  @override
  void dispose() {
    _player.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final msg = widget.message;
    final textColor = widget.textColor;
    if (msg.isSending) {
      return Row(mainAxisSize: MainAxisSize.min, children: [
        SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: textColor, value: (msg.uploadProgress != null && msg.uploadProgress! > 0) ? msg.uploadProgress : null)),
        const SizedBox(width: 8),
        Text(msg.uploadProgress != null && msg.uploadProgress! > 0 ? "${(msg.uploadProgress! * 100).toStringAsFixed(0)}% uploading..." : "Sending audio...", style: TextStyle(color: textColor, fontSize: 13)),
      ]);
    }
    final url = msg.fileUrl;
    final total = _duration.inMilliseconds > 0 ? _duration : Duration(seconds: (msg.meta?['duration_seconds'] as num?)?.toInt() ?? 0);
    final progress = total.inMilliseconds > 0 ? (_position.inMilliseconds / total.inMilliseconds).clamp(0.0, 1.0) : 0.0;

    return SizedBox(
      width: 220,
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        GestureDetector(
          onTap: (url != null && url.isNotEmpty) ? _togglePlay : null,
          onLongPress: (url != null && url.isNotEmpty) ? widget.onDownload : null,
          child: Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(color: textColor == Colors.white ? Colors.white24 : Colors.grey[200], shape: BoxShape.circle),
            child: _loading
                ? SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: textColor))
                : Icon(_state == PlayerState.playing ? Icons.pause : Icons.play_arrow, color: textColor),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            SliderTheme(
              data: SliderTheme.of(context).copyWith(
                trackHeight: 2.5,
                thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 5),
                overlayShape: SliderComponentShape.noOverlay,
                activeTrackColor: textColor,
                inactiveTrackColor: textColor.withOpacity(0.25),
                thumbColor: textColor,
              ),
              child: Slider(
                value: progress,
                onChanged: (url == null || url.isEmpty || total.inMilliseconds == 0) ? null : (v) {
                  final seekTo = Duration(milliseconds: (v * total.inMilliseconds).round());
                  _player.seek(seekTo);
                },
              ),
            ),
            Padding(
              padding: const EdgeInsets.only(left: 4),
              child: Text(
                total.inMilliseconds > 0 ? "${_fmt(_position)} / ${_fmt(total)}" : "Audio message",
                style: TextStyle(color: textColor.withOpacity(0.8), fontSize: 11),
              ),
            ),
          ]),
        ),
      ]),
    );
  }
}

// ============================================================
// 🔥 NAYA: MX Player jaisa full-screen video player.
// Pehle ye sirf ek basic play/pause tha (tap se toggle, progress bar).
// Ab production-level controls hain:
//   • center play/pause + ⏪10s / ⏩10s buttons
//   • bottom pe seekbar with live current/total time
//   • left/right screen pe double-tap karke bhi 10s seek (MX Player jaisa)
//     — saath me ek chhota "-10s"/"+10s" flash overlay
//   • single tap se controls show/hide, 3 second baad auto-hide
//   • buffering spinner
//   • rotate icon se landscape fullscreen toggle
// ============================================================
class _VideoPlayerScreen extends StatefulWidget {
  final String url;
  const _VideoPlayerScreen({required this.url});

  @override
  State<_VideoPlayerScreen> createState() => _VideoPlayerScreenState();
}

class _VideoPlayerScreenState extends State<_VideoPlayerScreen> {
  late VideoPlayerController _controller;
  bool _isReady = false;
  String? _error;
  bool _controlsVisible = true;
  bool _isLandscape = false;
  Timer? _hideTimer;

  // Double-tap seek flash feedback (left = rewind, right = forward)
  String? _seekFlashSide; // 'left' | 'right' | null
  Timer? _seekFlashTimer;

  @override
  void initState() {
    super.initState();
    _controller = VideoPlayerController.networkUrl(Uri.parse(widget.url))
      ..initialize().then((_) {
        if (!mounted) return;
        setState(() => _isReady = true);
        _controller.play();
        _restartHideTimer();
      }).catchError((e) {
        if (!mounted) return;
        setState(() => _error = "Video load nahi ho payi: $e");
      });
    _controller.addListener(_onTick);
  }

  void _onTick() {
    if (mounted) setState(() {});
  }

  void _restartHideTimer() {
    _hideTimer?.cancel();
    _hideTimer = Timer(const Duration(seconds: 3), () {
      if (mounted && _controller.value.isPlaying) setState(() => _controlsVisible = false);
    });
  }

  void _toggleControls() {
    setState(() => _controlsVisible = !_controlsVisible);
    if (_controlsVisible) _restartHideTimer();
  }

  void _togglePlay() {
    setState(() {
      if (_controller.value.isPlaying) {
        _controller.pause();
        _hideTimer?.cancel();
        _controlsVisible = true;
      } else {
        _controller.play();
        _restartHideTimer();
      }
    });
  }

  void _seekBy(int seconds) {
    final duration = _controller.value.duration;
    var target = _controller.value.position + Duration(seconds: seconds);
    if (target < Duration.zero) target = Duration.zero;
    if (target > duration) target = duration;
    _controller.seekTo(target);
    if (_controller.value.isPlaying) _restartHideTimer();
  }

  // MX Player jaisa: screen ke left/right hisse pe double-tap se -10/+10
  void _onDoubleTapSeek(bool forward) {
    _seekBy(forward ? 10 : -10);
    setState(() => _seekFlashSide = forward ? 'right' : 'left');
    _seekFlashTimer?.cancel();
    _seekFlashTimer = Timer(const Duration(milliseconds: 500), () {
      if (mounted) setState(() => _seekFlashSide = null);
    });
  }

  String _fmt(Duration d) {
    final h = d.inHours;
    final m = (d.inMinutes % 60).toString().padLeft(2, '0');
    final s = (d.inSeconds % 60).toString().padLeft(2, '0');
    return h > 0 ? "$h:$m:$s" : "$m:$s";
  }

  void _toggleOrientation() {
    setState(() => _isLandscape = !_isLandscape);
    if (_isLandscape) {
      SystemChrome.setPreferredOrientations([DeviceOrientation.landscapeLeft, DeviceOrientation.landscapeRight]);
      SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
    } else {
      SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp, DeviceOrientation.portraitDown]);
      SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
    }
  }

  @override
  void dispose() {
    _hideTimer?.cancel();
    _seekFlashTimer?.cancel();
    _controller.removeListener(_onTick);
    _controller.dispose();
    // Video screen se nikalte hi orientation/system UI wapas normal.
    SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp, DeviceOrientation.portraitDown]);
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: _error != null
          ? Center(child: Padding(padding: const EdgeInsets.all(20), child: Text(_error!, style: const TextStyle(color: Colors.white))))
          : !_isReady
              ? const Center(child: CircularProgressIndicator(color: Colors.white))
              : GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: _toggleControls,
                  child: Stack(children: [
                    Center(
                      child: AspectRatio(
                        aspectRatio: _controller.value.aspectRatio,
                        child: VideoPlayer(_controller),
                      ),
                    ),

                    // ---- Double-tap seek zones (left = -10s, right = +10s) ----
                    Positioned.fill(
                      child: Row(children: [
                        Expanded(
                          child: GestureDetector(
                            behavior: HitTestBehavior.translucent,
                            onDoubleTap: () => _onDoubleTapSeek(false),
                          ),
                        ),
                        Expanded(
                          child: GestureDetector(
                            behavior: HitTestBehavior.translucent,
                            onDoubleTap: () => _onDoubleTapSeek(true),
                          ),
                        ),
                      ]),
                    ),

                    // ---- Seek flash feedback ----
                    if (_seekFlashSide != null)
                      Align(
                        alignment: _seekFlashSide == 'left' ? Alignment.centerLeft : Alignment.centerRight,
                        child: Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 36),
                          child: Container(
                            padding: const EdgeInsets.all(14),
                            decoration: const BoxDecoration(color: Colors.black45, shape: BoxShape.circle),
                            child: Column(mainAxisSize: MainAxisSize.min, children: [
                              Icon(_seekFlashSide == 'left' ? Icons.replay_10 : Icons.forward_10, color: Colors.white, size: 30),
                            ]),
                          ),
                        ),
                      ),

                    // ---- Buffering ----
                    if (_controller.value.isBuffering)
                      const Center(child: CircularProgressIndicator(color: Colors.white)),

                    // ---- Controls overlay ----
                    AnimatedOpacity(
                      opacity: _controlsVisible ? 1 : 0,
                      duration: const Duration(milliseconds: 200),
                      child: IgnorePointer(
                        ignoring: !_controlsVisible,
                        child: Container(
                          decoration: const BoxDecoration(
                            gradient: LinearGradient(
                              begin: Alignment.topCenter,
                              end: Alignment.bottomCenter,
                              colors: [Colors.black54, Colors.transparent, Colors.transparent, Colors.black87],
                              stops: [0, 0.22, 0.68, 1],
                            ),
                          ),
                          child: SafeArea(
                            child: Column(children: [
                              // Top bar: back + rotate
                              Padding(
                                padding: const EdgeInsets.symmetric(horizontal: 4),
                                child: Row(children: [
                                  IconButton(icon: const Icon(Icons.arrow_back, color: Colors.white), onPressed: () => Navigator.pop(context)),
                                  const Spacer(),
                                  IconButton(
                                    icon: Icon(_isLandscape ? Icons.screen_lock_portrait : Icons.screen_rotation, color: Colors.white),
                                    onPressed: _toggleOrientation,
                                  ),
                                ]),
                              ),
                              const Spacer(),
                              // Center: -10s | play/pause | +10s
                              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                                IconButton(iconSize: 32, icon: const Icon(Icons.replay_10, color: Colors.white), onPressed: () => _seekBy(-10)),
                                const SizedBox(width: 26),
                                Container(
                                  decoration: const BoxDecoration(color: Colors.white24, shape: BoxShape.circle),
                                  child: IconButton(
                                    iconSize: 42,
                                    icon: Icon(_controller.value.isPlaying ? Icons.pause : Icons.play_arrow, color: Colors.white),
                                    onPressed: _togglePlay,
                                  ),
                                ),
                                const SizedBox(width: 26),
                                IconButton(iconSize: 32, icon: const Icon(Icons.forward_10, color: Colors.white), onPressed: () => _seekBy(10)),
                              ]),
                              const Spacer(),
                              // Bottom: seekbar + times
                              Padding(
                                padding: const EdgeInsets.symmetric(horizontal: 14),
                                child: Row(children: [
                                  Text(_fmt(_controller.value.position), style: const TextStyle(color: Colors.white, fontSize: 12)),
                                  Expanded(
                                    child: SliderTheme(
                                      data: SliderTheme.of(context).copyWith(
                                        trackHeight: 2.5,
                                        thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 6),
                                        overlayShape: SliderComponentShape.noOverlay,
                                        activeTrackColor: const Color(0xFFE53935),
                                        inactiveTrackColor: Colors.white30,
                                        thumbColor: const Color(0xFFE53935),
                                      ),
                                      child: Slider(
                                        value: _controller.value.duration.inMilliseconds > 0
                                            ? _controller.value.position.inMilliseconds
                                                .clamp(0, _controller.value.duration.inMilliseconds)
                                                .toDouble()
                                            : 0,
                                        min: 0,
                                        max: _controller.value.duration.inMilliseconds > 0
                                            ? _controller.value.duration.inMilliseconds.toDouble()
                                            : 1,
                                        onChangeStart: (_) => _hideTimer?.cancel(),
                                        onChanged: (v) => setState(() => _controller.seekTo(Duration(milliseconds: v.round()))),
                                        onChangeEnd: (_) {
                                          if (_controller.value.isPlaying) _restartHideTimer();
                                        },
                                      ),
                                    ),
                                  ),
                                  Text(_fmt(_controller.value.duration), style: const TextStyle(color: Colors.white, fontSize: 12)),
                                ]),
                              ),
                              const SizedBox(height: 8),
                            ]),
                          ),
                        ),
                      ),
                    ),
                  ]),
                ),
    );
  }
}