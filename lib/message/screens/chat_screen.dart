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
import 'dart:convert'; // 🔥 NAYA — JWT se username decode karne ke liye (profile navigation)
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart'; // 🔥 NAYA — video fullscreen (landscape) rotation ke liye
import 'package:flutter/gestures.dart'; // 🔥 NAYA — chat text ke andar clickable links ke liye (TapGestureRecognizer)
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
import '../../profile/screens/target_profile.dart'; // 🔥 NAYA — user profile pe navigate karne ke liye
import '../../profile/api_service.dart' as ProfileApi; // 🔥 NAYA
import '../../home.dart'; // 🔥 NAYA — apni khud ki profile pe tap karne par Home ke Profile tab pe bhejne ke liye
import 'call_screen.dart';
import 'incoming_call_screen.dart'; // 🔥 NAYA — full-screen incoming call UI (outgoing call jaisa look)
import 'study_room_screen.dart';
import 'forward_message_screen.dart'; // NEW — pick chat(s) to forward selected message(s) to
import 'group_profile_screen.dart'; // 🔥 NAYA — Group info screen (public/private, members, admin roles, invite link)
import 'media_viewer_screen.dart'; // 🔥 NAYA — fullscreen swipeable image viewer (zoom + auto-hide thumbnail strip)
import '../../widgets/sticker_picker_sheet.dart'; // 🔥 NAYA — apne PNG stickers ka picker (assets/stickers/), chat & comments dono me reusable

const _kEmojis = ['👍', '❤', '😂', '😮', '😢', '🙏'];

// 🔥 NAYA — chat text ke andar URL (http://, https://, ya www. se shuru)
// detect karne ke liye regex. `_LinkifiedText` widget isse use karta hai.
final RegExp _urlRegex = RegExp(
  r'((https?:\/\/)|(www\.))[^\s]+',
  caseSensitive: false,
);

// 🔥 NAYA — WhatsApp/Telegram jaisa hi: message text ke andar jahan bhi
// koi URL mile, use blue + underline dikhata hai aur tap karne par
// device ke default browser (ya us link ko handle karne wali app) me
// khol deta hai. Agar text me koi URL nahi hai to normal plain Text
// jaisa hi render hota hai (koi extra cost/behaviour change nahi).
class _LinkifiedText extends StatelessWidget {
  final String text;
  final Color color;
  final double fontSize;
  const _LinkifiedText({required this.text, required this.color, this.fontSize = 14.5});

  Future<void> _openLink(String raw) async {
    var url = raw.trim();
    // Sentence ke end me aane wala trailing punctuation (., ,, )) etc.)
    // link ka hissa nahi hota — usse hata dete hain taaki galat URL na khule.
    url = url.replaceAll(RegExp(r'[\.,\)\]]+$'), '');
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      url = 'https://$url';
    }
    final uri = Uri.tryParse(url);
    if (uri == null) return;
    try {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    } catch (_) {
      // link open nahi ho paaya (invalid URL, ya koi app handle nahi kar
      // paayi) — silently ignore, chat UI break nahi hona chahiye.
    }
  }

  @override
  Widget build(BuildContext context) {
    final matches = _urlRegex.allMatches(text);
    if (matches.isEmpty) {
      // Koi URL nahi mila — plain Text hi kaafi hai, RichText ki zaroorat nahi.
      return Text(text, style: TextStyle(color: color, fontSize: fontSize));
    }

    final spans = <InlineSpan>[];
    int last = 0;
    for (final m in matches) {
      if (m.start > last) {
        spans.add(TextSpan(text: text.substring(last, m.start)));
      }
      final linkText = text.substring(m.start, m.end);
      spans.add(TextSpan(
        text: linkText,
        style: TextStyle(
          // Sent (dark) bubble pe halka light-blue, received (white)
          // bubble pe standard link-blue — dono jagah readable rahega.
          color: color == Colors.white ? const Color(0xFFB3E5FC) : const Color(0xFF039BE5),
          decoration: TextDecoration.underline,
        ),
        recognizer: TapGestureRecognizer()..onTap = () => _openLink(linkText),
      ));
      last = m.end;
    }
    if (last < text.length) {
      spans.add(TextSpan(text: text.substring(last)));
    }

    return RichText(
      text: TextSpan(
        style: TextStyle(color: color, fontSize: fontSize),
        children: spans,
      ),
    );
  }
}

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

  // 🔥 NAYA — pagination: initially sirf pehla page (20 messages) load hota
  // hai, baaki purane messages tab load hote hain jab user list ke top tak
  // scroll karta hai (WhatsApp/Telegram jaisa "load more on scroll up").
  static const int _kPageSize = 20; // ek page me kitne messages
  int _currentPage = 1;
  bool _hasMoreMessages = true; // false ho jaata hai jab backend se koi purana message na aaye
  bool _isLoadingMore = false; // top pe "loading older messages" spinner ke liye
  bool _isSocketConnected = false;
  bool _otherTyping = false;
  String? _myUserId;
  String? _myUsername; // 🔥 NAYA — profile navigation ke liye (isMe check)
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

  // 🔥 NAYA — appbar ke 3-dot menu me mute/unmute notification toggle.
  // Private chat ho ya group, dono ke liye same hi flag hai (per-user
  // ConversationParticipant.is_muted), isliye alag logic nahi chahiye.
  bool _isMuted = false;

  // 🔥 NAYA — private chat me doosre user ko block/unblock karne ke liye.
  // Sirf 1-to-1 chat me relevant hai (group me nahi dikhta).
  bool _isBlocked = false;

  // 🔥 NAYA — chat filter (3-dot menu se): 'all' | 'text' | 'media' | 'docs' | 'links'
  // 'all' matlab koi filter nahi, poori chat normal dikhti hai.
  String _chatFilter = 'all';

  // 🔥 NAYA — Temporary chat (disappearing messages) ka current duration:
  // 'none' | '1_month' | '6_months' | '1_year'. Backend default '6_months'
  // rakhta hai, isliye yahan bhi wahi default rakha hai jab tak asli value
  // load na ho jaaye (taaki menu me galat "Off" na flash ho ek pal ke liye).
  String _disappearingDuration = '6_months';

  // 🔥 NAYA — "Delete group" option sirf group ke ADMIN (moderator/member
  // nahi) ko dikhane ke liye — apni role group detail se load karte hain.
  bool _isGroupAdmin = false;

  // NEW — multi-select mode for forwarding messages. When active, the
  // normal AppBar is swapped for a selection bar (count + forward icon),
  // tapping a message toggles it in/out of `_selectedMessageIds` instead
  // of opening it, and long-press/media-tap gestures are absorbed.
  bool _selectionMode = false;
  final Set<String> _selectedMessageIds = {};

  // 🔥 NAYA — ek message diye gaye filter category me aata hai ya nahi, ye check karta hai.
  //  text  -> sirf plain text message (jisme koi URL na ho)
  //  media -> image ya video
  //  docs  -> file ya presentation (document type attachments)
  //  links -> text message jiske andar koi http(s)/www link ho
  bool _matchesFilter(MessageModel msg) {
    switch (_chatFilter) {
      case 'text':
        return msg.type == MessageType.text && !_urlRegex.hasMatch(msg.text ?? '');
      case 'media':
        return msg.type == MessageType.image || msg.type == MessageType.video;
      case 'docs':
        return msg.type == MessageType.file || msg.type == MessageType.presentation;
      case 'links':
        return msg.type == MessageType.text && _urlRegex.hasMatch(msg.text ?? '');
      case 'all':
      default:
        return true;
    }
  }

  String _filterLabel(String value) {
    switch (value) {
      case 'text': return "Text";
      case 'media': return "Media";
      case 'docs': return "Docs";
      case 'links': return "Links";
      default: return "All";
    }
  }

  // 🔥 NAYA — 3-dot menu ke "Filter messages" tap hone par ye bottom sheet
  // khulti hai jisme 5 options hote hain: All, Text, Media, Docs, Links.
  void _showFilterSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (_) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 14, 16, 4),
            child: Align(alignment: Alignment.centerLeft, child: Text("Filter messages", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16))),
          ),
          for (final entry in const [
            {'value': 'all', 'label': 'All messages', 'icon': Icons.forum_outlined},
            {'value': 'text', 'label': 'Text', 'icon': Icons.short_text},
            {'value': 'media', 'label': 'Image / Video', 'icon': Icons.perm_media_outlined},
            {'value': 'docs', 'label': 'Docs / Files', 'icon': Icons.insert_drive_file_outlined},
            {'value': 'links', 'label': 'URL / Links', 'icon': Icons.link},
          ])
            ListTile(
              leading: Icon(entry['icon'] as IconData, color: _chatFilter == entry['value'] ? const Color(0xFF3D7EFF) : Colors.black87),
              title: Text(entry['label'] as String, style: TextStyle(color: _chatFilter == entry['value'] ? const Color(0xFF3D7EFF) : Colors.black87, fontWeight: _chatFilter == entry['value'] ? FontWeight.bold : FontWeight.normal)),
              trailing: _chatFilter == entry['value'] ? const Icon(Icons.check, color: Color(0xFF3D7EFF)) : null,
              onTap: () {
                Navigator.pop(context);
                setState(() => _chatFilter = entry['value'] as String);
              },
            ),
        ]),
      ),
    );
  }

  @override
  void initState() {
    super.initState();
    // 🔥 NAYA: is chat ka id "currently open" mark karo — taaki isi chat
    // ka naya message aane par duplicate push notification popup na dikhe
    // (PushNotificationService.init() me ye check hota hai).
    PushNotificationService.currentOpenConversationId = widget.conversation.id;
    _scrollController.addListener(_onScroll); // 🔥 NAYA — top tak scroll hone par purane messages load karne ke liye
    _init();
    _loadMuteStatus(); // 🔥 NAYA
    _loadBlockStatus(); // 🔥 NAYA
    _loadDisappearingStatus(); // 🔥 NAYA
  }

  // 🔥 NAYA — jab user list ko top ke paas scroll kare (chat me sabse
  // upar, list ka index 0 = sabse purana message), to agla page (aur
  // purane messages) load karo. Bottom (naye messages) se koi lena-dena
  // nahi — waha to naye realtime messages seedha add ho jaate hain.
  void _onScroll() {
    if (!_scrollController.hasClients) return;
    if (_isLoadingMore || !_hasMoreMessages || _isLoading) return;
    // 300px ka buffer rakha hai taaki user ke top pe pahochne se THODA
    // pehle hi loading shuru ho jaaye — list "jump" hote hue nahi dikhega.
    if (_scrollController.offset <= 300) {
      _loadMoreMessages();
    }
  }

  Future<void> _init() async {
    _myUserId = await AuthService.getUserId();
    _loadMyUsername(); // 🔥 NAYA — fire-and-forget, profile tap se pehle usually ready ho jaayega
    await _loadHistory();
    await _connectSocket();
    await MessageApiService.readAll(widget.conversation.id);
    _loadGroupRole(); // 🔥 NAYA — "Delete group" ke liye apni admin-status pata karo
  }

  // 🔥 NAYA — bilkul home.dart ke _loadMyUsername jaisa: pehle profile API
  // try karo, fail ho to JWT access token decode karke username nikaal lo.
  Future<void> _loadMyUsername() async {
    try {
      final d = await ProfileApi.ApiService.getProfile();
      _myUsername = d.username;
    } catch (_) {
      try {
        final t = await AuthService.getToken();
        if (t != null) {
          String p = base64.normalize(t.split('.')[1]);
          _myUsername = jsonDecode(utf8.decode(base64Url.decode(p)))['username']?.toString();
        }
      } catch (_) {}
    }
  }

  // 🔥 NAYA — bilkul home.dart ke _goToProfile jaisa hi logic: apni khud ki
  // profile pe tap kiya to Home ke Profile tab pe bhej do, kisi aur ki
  // profile pe tap kiya to seedha TargetProfilePage khol do.
  Future<void> _goToProfile(String username) async {
    if (username.trim().isEmpty) return;
    if (_myUsername == null) await _loadMyUsername();
    if (!mounted) return;
    final isMe = _myUsername != null &&
        _myUsername!.toLowerCase().trim() == username.toLowerCase().trim();
    if (isMe) {
      Navigator.of(context).pushAndRemoveUntil(
        MaterialPageRoute(builder: (_) => const HomeScreen(initialIndex: 2)),
        (route) => false,
      );
    } else {
      Navigator.push(
        context,
        MaterialPageRoute(builder: (_) => TargetProfilePage(username: username)),
      );
    }
  }

  // 🔥 NAYA — group chat me apni role (admin/moderator/member) group
  // detail se nikaal ke check karte hain ki "Delete group" (admin-only)
  // option dikhana hai ya nahi. Private chat me ye no-op hi rehta hai.
  // Backend response ka exact shape pata nahi (serializers.py yahan
  // nahi hai) isliye members list ke liye common possible key-names
  // (`members` / `group_members`) aur har member ke andar
  // (`user.id` / `user_id`) dono format defensively handle kiye hain —
  // kuch bhi match na ho to chup-chaap admin=false hi maan lo (worst
  // case sirf button nahi dikhega, backend to permission already
  // enforce karta hi hai).
  Future<void> _loadGroupRole() async {
    if (!widget.conversation.isGroup) return;
    final groupId = widget.conversation.group?.id;
    if (groupId == null || groupId.isEmpty || _myUserId == null) return;
    try {
      final data = await MessageApiService.getGroup(groupId);
      final membersRaw = data['members'] ?? data['group_members'] ?? [];
      if (membersRaw is List) {
        for (final m in membersRaw) {
          if (m is Map) {
            final userField = m['user'];
            final memberUserId = (userField is Map ? userField['id'] : (m['user_id'] ?? m['id']))?.toString();
            if (memberUserId == _myUserId) {
              final role = m['role']?.toString();
              if (mounted) setState(() => _isGroupAdmin = role == 'admin');
              break;
            }
          }
        }
      }
    } catch (_) {}
  }

  // 🔥 NAYA — abhi ka mute status backend se le aao taaki menu me sahi
  // label ("Mute" ya "Unmute") dikhe. Fail ho jaaye to chup-chaap
  // default false (unmuted) maan lo — koi crash/blocking error nahi.
  Future<void> _loadMuteStatus() async {
    try {
      final muted =
          await MessageApiService.isConversationMuted(widget.conversation.id);
      if (mounted) setState(() => _isMuted = muted);
    } catch (_) {}
  }

  // 🔥 NAYA — optimistic toggle: pehle UI turant update, phir backend
  // call; fail ho jaaye to purani value pe wapas revert kar do.
  Future<void> _toggleMuteNotifications() async {
    final newValue = !_isMuted;
    setState(() => _isMuted = newValue);
    try {
      await MessageApiService.updateSettings(widget.conversation.id,
          isMuted: newValue);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content:
              Text(newValue ? "Notifications muted" : "Notifications unmuted"),
        ));
      }
    } catch (e) {
      if (mounted) setState(() => _isMuted = !newValue); // revert
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text("Failed to update: $e")));
      }
    }
  }

  // 🔥 NAYA — block/unblock ke liye current status backend se le aao.
  // Group chat me ye sawal hi nahi uthta, aur agar `otherParticipant`
  // kisi wajah se null ho (data abhi load nahi hua) to bhi silently
  // skip kar do — koi crash nahi.
  Future<void> _loadBlockStatus() async {
    if (widget.conversation.isGroup) return;
    final otherId = widget.conversation.otherParticipant?.id;
    if (otherId == null || otherId.isEmpty) return;
    try {
      final blocked = await MessageApiService.isUserBlocked(otherId);
      if (mounted) setState(() => _isBlocked = blocked);
    } catch (_) {}
  }

  // 🔥 NAYA — block karne se pehle confirm dialog dikhata hai (WhatsApp
  // jaisa), unblock seedha ho jaata hai (koi confirm ki zaroorat nahi).
  // Success/fail dono cases me user ko snackbar se pata chal jaata hai.
  Future<void> _toggleBlockUser() async {
    final otherId = widget.conversation.otherParticipant?.id;
    if (otherId == null || otherId.isEmpty) return;

    if (!_isBlocked) {
      final otherName = widget.conversation.otherParticipant?.displayName ?? 'this user';
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          title: const Text("Block user?"),
          content: Text(
              "$otherName won't be able to call or message you, and you won't see their messages either."),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text("Cancel")),
            TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text("Block", style: TextStyle(color: Colors.red)),
            ),
          ],
        ),
      );
      if (confirmed != true) return;

      try {
        await MessageApiService.blockUser(otherId);
        if (!mounted) return;
        setState(() => _isBlocked = true);
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("User blocked.")));
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Block failed: $e")));
        }
      }
    } else {
      try {
        await MessageApiService.unblockUser(otherId);
        if (!mounted) return;
        setState(() => _isBlocked = false);
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("User unblocked.")));
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Unblock failed: $e")));
        }
      }
    }
  }

  // 🔥 NAYA — chat khulte hi current disappearing-messages duration
  // backend se le aao (menu me sahi option pe checkmark dikhane ke liye).
  Future<void> _loadDisappearingStatus() async {
    try {
      final duration =
          await MessageApiService.getDisappearingDuration(widget.conversation.id);
      if (mounted) setState(() => _disappearingDuration = duration);
    } catch (_) {}
  }

  String _disappearingLabel(String value) {
    switch (value) {
      case '1_month': return "1 Month";
      case '6_months': return "6 Months";
      case '1_year': return "1 Year";
      case 'none':
      default: return "Off";
    }
  }

  // 🔥 NAYA — optimistic update: pehle UI turant naya duration dikhata hai,
  // phir backend call; fail ho jaaye (e.g. group me non-admin) to purani
  // value pe wapas revert kar do aur error dikhao.
  Future<void> _setDisappearingDuration(String duration) async {
    final previous = _disappearingDuration;
    if (duration == previous) return;
    setState(() => _disappearingDuration = duration);
    try {
      await MessageApiService.setDisappearingMessages(widget.conversation.id, duration);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(duration == 'none'
              ? "Disappearing messages turned off"
              : "New messages will disappear after ${_disappearingLabel(duration)}"),
        ));
      }
    } catch (e) {
      if (mounted) setState(() => _disappearingDuration = previous); // revert
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text("Failed to update: $e")));
      }
    }
  }

  // 🔥 NAYA — 3-dot menu ke "Disappearing messages" tap hone par ye bottom
  // sheet khulti hai — WhatsApp jaisa hi 4 options: Off, 1 Month, 6 Months, 1 Year.
  void _showDisappearingMessagesSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (_) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 14, 16, 4),
            child: Align(alignment: Alignment.centerLeft, child: Text("Disappearing messages", style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16))),
          ),
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text(
                "Naye messages chune gaye time ke baad chat se apne aap gayab ho jaayenge.",
                style: TextStyle(fontSize: 12.5, color: Colors.black54),
              ),
            ),
          ),
          for (final entry in const [
            {'value': 'none', 'label': 'Off'},
            {'value': '1_month', 'label': '1 Month'},
            {'value': '6_months', 'label': '6 Months'},
            {'value': '1_year', 'label': '1 Year'},
          ])
            ListTile(
              leading: Icon(
                entry['value'] == 'none' ? Icons.timer_off_outlined : Icons.timer_outlined,
                color: _disappearingDuration == entry['value'] ? const Color(0xFF3D7EFF) : Colors.black87,
              ),
              title: Text(
                entry['label']!,
                style: TextStyle(
                  color: _disappearingDuration == entry['value'] ? const Color(0xFF3D7EFF) : Colors.black87,
                  fontWeight: _disappearingDuration == entry['value'] ? FontWeight.bold : FontWeight.normal,
                ),
              ),
              trailing: _disappearingDuration == entry['value'] ? const Icon(Icons.check, color: Color(0xFF3D7EFF)) : null,
              onTap: () {
                Navigator.pop(context);
                _setDisappearingDuration(entry['value']!);
              },
            ),
        ]),
      ),
    );
  }

  // 🔥 NAYA — group chat se khud nikalne ke liye. Backend ka
  // `GroupViewSet.update_member` (DELETE) already self-leave allow karta
  // hai (admin check sirf tab lagta hai jab koi AUR member ko remove kiya
  // ja raha ho) — isliye yahan seedha apni hi `_myUserId` bhej dete hain.
  // Group ka id `widget.conversation.group?.id` se aata hai — ye
  // `Conversation.id` se ALAG hota hai (Group aur uski Conversation dono
  // ke apne-apne UUID hote hain).
  // 🔥 NAYA — "Group info" screen (naam/photo edit, members list, admin
  // role management, invite link, public/private) khud group ke saare
  // members-related actions khud handle karti hai. Wahan se "Leave group"
  // ya "Delete group" hone par `true` return hota hai — us case me ye
  // chat screen bhi khud ko band kar leti hai (jaisa `_deleteGroup` /
  // `_leaveGroup` already niche karte hain), taaki user wapas ek aisi
  // chat me na reh jaaye jiska ab wo member hi nahi hai.
  Future<void> _openGroupProfile() async {
    final groupId = widget.conversation.group?.id;
    if (groupId == null || groupId.isEmpty) return;
    final result = await Navigator.push<bool>(
      context,
      MaterialPageRoute(builder: (_) => GroupProfileScreen(groupId: groupId)),
    );
    if (result == true && mounted) {
      Navigator.of(context).pop(true);
    }
  }

  Future<void> _leaveGroup() async {
    final groupId = widget.conversation.group?.id;
    if (groupId == null || groupId.isEmpty || _myUserId == null) return;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text("Leave group?"),
        content: Text(
            "You'll no longer receive messages from \"${widget.conversation.displayTitle}\"."),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text("Cancel")),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text("Leave", style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    try {
      await MessageApiService.removeGroupMember(groupId, _myUserId!);
      if (!mounted) return;
      // Chat screen se conversations list pe wapas — `true` return karte
      // hain taaki caller (conversations screen) chahe to list refresh
      // kar le (ye group ab uski list me nahi dikhna chahiye).
      Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Leave failed: $e")));
      }
    }
  }

  // 🔥 NAYA — poora group permanently delete karne ke liye — SIRF ADMIN
  // (backend `GroupViewSet.destroy` me strictly `role == 'admin'` check
  // karta hai, moderator ko bhi allow nahi). `_leaveGroup` se ALAG hai:
  // wahan sirf khud nikalte ho, yahan poora group sabke liye (saare
  // members, messages, media sab) hamesha ke liye delete ho jaata hai.
  Future<void> _deleteGroup() async {
    final groupId = widget.conversation.group?.id;
    if (groupId == null || groupId.isEmpty) return;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text("Delete group?"),
        content: Text(
            "\"${widget.conversation.displayTitle}\" hamesha ke liye delete ho jaayega — saare members ke liye, saare messages/media ke saath. Ye undo nahi ho sakta."),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text("Cancel")),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text("Delete", style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    try {
      await MessageApiService.deleteGroup(groupId);
      if (!mounted) return;
      // Baaki members ko is delete ka pata `group_deleted` socket event se
      // chal jaata hai (backend delete se PEHLE hi broadcast kar deta hai)
      // — humein khud yahan seedha conversations list pe wapas jaana hai.
      Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Delete failed: $e")));
      }
    }
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
      // 🔥 NAYA — pehli baar sirf `_kPageSize` (20) messages mangwao, poori
      // history nahi. Baaki purane messages user ke top tak scroll karne
      // par `_loadMoreMessages()` se load honge.
      final data = await MessageApiService.getMessages(
        widget.conversation.id,
        page: 1,
        pageSize: _kPageSize,
      );
      if (mounted) {
        setState(() {
          _messages = data.reversed.toList();
          _isLoading = false;
          _currentPage = 1;
          // Agar backend se ek page se kam messages aaye, matlab aur purane
          // messages hain hi nahi — "load more" trigger karne ki zaroorat nahi.
          _hasMoreMessages = data.length >= _kPageSize;
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

  // 🔥 NAYA — user list ke top ke paas pahochte hi agla (purana) page
  // fetch karke `_messages` list ke SHURU me insert karta hai. Scroll
  // position ko manually adjust karte hain taaki naye messages upar add
  // hone ke baad bhi user ki current screen "jump" na kare — bilkul
  // WhatsApp/Telegram jaisa smooth "load older messages" feel.
  Future<void> _loadMoreMessages() async {
    if (_isLoadingMore || !_hasMoreMessages) return;
    setState(() => _isLoadingMore = true);

    final nextPage = _currentPage + 1;
    try {
      final older = await MessageApiService.getMessages(
        widget.conversation.id,
        page: nextPage,
        pageSize: _kPageSize,
      );
      if (!mounted) return;

      if (older.isEmpty) {
        setState(() {
          _hasMoreMessages = false;
          _isLoadingMore = false;
        });
        return;
      }

      // Insertion se PEHLE ka scroll offset/extent yaad rakho — insertion ke
      // baad isi diff se jumpTo() karenge taaki user jahan dekh raha tha
      // wahi content usi jagah dikhta rahe (visual jump na ho).
      final prevMaxExtent =
          _scrollController.hasClients ? _scrollController.position.maxScrollExtent : 0.0;
      final prevOffset = _scrollController.hasClients ? _scrollController.offset : 0.0;

      setState(() {
        _messages.insertAll(0, older.reversed.toList());
        _currentPage = nextPage;
        _isLoadingMore = false;
        if (older.length < _kPageSize) _hasMoreMessages = false;
      });
      _scanAlreadyDownloaded(); // 🔥 NAYA — abhi load hue purane messages ke media pe bhi "Open" status dikhao

      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted || !_scrollController.hasClients) return;
        final newMaxExtent = _scrollController.position.maxScrollExtent;
        final diff = newMaxExtent - prevMaxExtent;
        if (diff > 0) {
          _scrollController.jumpTo(prevOffset + diff);
        }
      });
    } catch (e) {
      if (mounted) {
        setState(() => _isLoadingMore = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Failed to load older messages: $e")),
        );
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
      // 🔥 NAYA — Temporary chat: dusre participant/admin ne disappearing
      // messages ki setting change ki to yahan bhi turant sync ho jaaye.
      case 'disappearing_messages_updated':
        final duration = event['duration']?.toString();
        if (duration != null && mounted) {
          setState(() => _disappearingDuration = duration);
          if (event['updated_by']?.toString() != _myUserId) {
            ScaffoldMessenger.of(context).showSnackBar(SnackBar(
              content: Text(duration == 'none'
                  ? "Disappearing messages turned off"
                  : "Disappearing messages set to ${_disappearingLabel(duration)}"),
            ));
          }
        }
        break;
      // 🔥 NAYA — admin ne poora group delete kar diya — sabhi (khud
      // delete karne wale admin ko chhod ke, uski app pehle hi
      // `_deleteGroup()` ke andar seedha pop kar chuki hoti hai) members
      // ki chat screen turant band karke conversations list pe bhej do.
      case 'group_deleted':
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text("This group was deleted by the admin.")),
          );
          Navigator.of(context).pop(true);
        }
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
    // 1) {type: call_event, event: incoming_call, call_id:..., call_type:..., caller_name:..., caller_photo:...}
    // 2) {type: incoming_call, call_id:...,...}
    final eventName = (event['event'] ?? event['type']).toString();
    final callId = (event['call_id'] ?? event['id'])?.toString();
    final callType = (event['call_type'] ?? event['type'] ?? 'audio').toString();
    final callerName = (event['caller_name'] ?? 'Someone').toString();
    // 🔥 NAYA — backend ab `caller_photo` bhi bhejta hai (CallInitiateView),
    // taaki incoming-call popup me caller ki asli photo dikhe, sirf
    // initials wala fallback avatar nahi.
    final callerPhoto = event['caller_photo']?.toString();
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
        callerAvatar: callerPhoto,
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
      showDialog(context: context, barrierDismissible: false, builder: (_) => const Center(child: CircularProgressIndicator(color: Color(0xFF030F27))));
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
    // 🔥 FIX — pehle yahan bhi normal `_enterStudyRoom()` hi call hota tha,
    // isliye har baar icon tap karne par purani persistent room/whiteboard
    // hi reuse hoti thi. Ab icon se start karna hamesha ek BILKUL NAYI
    // session banata hai (`startNewSession: true`) — invite card pe tap
    // karke JOIN karne wala flow (neeche `onJoinStudyRoom: _enterStudyRoom`)
    // isse alag hai aur wahi purani/active session me le jaata hai.
    _enterStudyRoom(startNewSession: true);
  }

  // Card pe tap karke (khud bheja ho ya doosre ka receive kiya ho) —
  // dono jagah se yehi ek function room me le jaata hai, taaki tap karne
  // par dobara invite na bhej jaaye. `startNewSession` sirf `_openStudyRoom`
  // (icon se fresh start) se true aata hai — card tap se JOIN karne me
  // hamesha false rehta hai taaki chal rahi session me hi entry ho.
  void _enterStudyRoom({bool startNewSession = false}) {
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
          startNewSession: startNewSession,
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

  // 🔥 NAYA — apna PNG sticker bhejna: sticker_picker_sheet se ek
  // asset path milta hai (jaise "assets/stickers/hi_frog.png"). Usko
  // app bundle se bytes ki tarah padh ke ek real temp file bana dete
  // hain, phir wahi normal `_uploadAndSendFile` pipeline use karte
  // hain jo photo bhejne me use hota hai — isliye upload %, read-tick,
  // download, sab already-existing image logic apne aap kaam karta
  // hai. `is_sticker: true` meta se bubble ko pata chal jaata hai ki
  // ye ek sticker hai (normal colored chat-bubble nahi, WhatsApp
  // jaisa transparent bada sticker dikhana hai).
  Future<void> _sendSticker(String assetPath) async {
    try {
      final data = await rootBundle.load(assetPath);
      final bytes = data.buffer.asUint8List();
      final tempDir = await getTemporaryDirectory();
      final fileName = assetPath.split('/').last;
      final tempPath = "${tempDir.path}/sticker_${DateTime.now().microsecondsSinceEpoch}_$fileName";
      final file = await File(tempPath).writeAsBytes(bytes);
      await _uploadAndSendFile(file, MessageType.image, fileName, extraMeta: {'is_sticker': true});
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Sticker bhejne me error: $e")));
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
      // 🔥 NAYA — Photo aur Video gallery ab do ALAG buttons hain — Photo
      // Gallery sirf images ka native picker kholta hai, Video Gallery
      // sirf videos ka. Pehle ek hi "Gallery" button tha jo pehle mixed
      // picker try karta, na mile to images-only, na mile to ek video —
      // isliye kabhi photo aati thi kabhi video, mixed/unpredictable tha.
      _attachmentTile(Icons.photo_library, "Photo Gallery", const Color(0xFF9C27B0), _pickAndSendPhotoGallery),
      _attachmentTile(Icons.video_library, "Video Gallery", const Color(0xFFE53935), _pickAndSendVideoGallery),
      _attachmentTile(Icons.mic, "Audio", const Color(0xFFFF9800), () => _pickAndSendAttachment(MessageType.audio)),
      _attachmentTile(Icons.insert_drive_file, "File", const Color(0xFF3F51B5), () => _pickAndSendAttachment(MessageType.file)),
      _attachmentTile(Icons.slideshow, "Presentation", const Color(0xFF00897B), () => _pickAndSendAttachment(MessageType.presentation)),
      _attachmentTile(Icons.location_on, "Location", const Color(0xFF4CAF50), _sendLocation),
    ])));
  }

  // 🔥 NAYA — shared helper: kahin se bhi (Photo Gallery ya Video Gallery
  // button se) raw XFiles aa jaayein, ye unhe real files banata hai
  // (_ensureRealFile — content:// URI wale sources ke liye), WhatsApp
  // jaisa review/preview screen dikhata hai (caption, item remove/add),
  // aur "send" hone par images/videos ko split karke correct pipeline
  // (_uploadAndSendMultipleImages / _uploadAndSendFile) se bhejta hai.
  Future<void> _pickAndSendMedia(List<XFile> rawPicked) async {
    if (rawPicked.isEmpty) return;
    // 🔥 WhatsApp/doosre app ke media folder se pick kiya gaya item
    // content:// URI ke roop me aa sakta hai; yahin sabse pehle real
    // file bana lete hain taaki preview screen aur upload dono sahi se
    // kaam karein (_ensureRealFile ka comment upar dekho).
    final picked = await _ensureRealFiles(rawPicked);

    // 🔥 Seedha upload karne ke bajaye pehle WhatsApp jaisa review/preview
    // screen dikhao — user yahan se koi item hata sakta hai, "+" se aur
    // media add kar sakta hai, aur caption likh sakta hai.
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

  // 🔥 NAYA — "Photo Gallery" button: sirf-images multi-select picker.
  // `pickMultiImage()` bahut purana, stable, sirf-images API hai jo
  // hamesha device ki asli Photos/Gallery app kholta hai, kabhi generic
  // Downloads/file-manager par fallback nahi karta — isliye ab koi
  // "Word/Excel/PDF bhi dikhne lagte hain" wala confusion nahi rahega,
  // aur na hi koi video is grid me dikhegi.
  Future<void> _pickAndSendPhotoGallery() async {
    final rawPicked = await ImagePicker().pickMultiImage(imageQuality: 85);
    await _pickAndSendMedia(rawPicked);
  }

  // 🔥 NAYA — "Video Gallery" button: sirf-videos multi-select picker.
  // image_picker me multiple videos ek saath select karne ka koi
  // built-in tarika nahi hai, isliye file_selector ka `openFiles`
  // video-extensions ke saath use karte hain — OS ka video-filtered
  // picker khulta hai (generic "any file" browser nahi), aur user ek
  // saath kai videos select kar sakta hai.
  Future<void> _pickAndSendVideoGallery() async {
    const videoGroup = XTypeGroup(
      label: 'video',
      extensions: ['mp4', 'mov', 'mkv', '3gp', 'webm', 'avi', 'm4v'],
    );
    final rawPicked = await openFiles(acceptedTypeGroups: [videoGroup]);
    await _pickAndSendMedia(rawPicked);
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
    // Reaction bar plain emoji dikhata hai (WhatsApp jaisa asli reaction
    // bar) — backend me wahi purana emoji string save/bheja jaata hai.
    showModalBottomSheet(context: context, backgroundColor: Colors.white, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))), builder: (_) => SafeArea(child: Padding(padding: const EdgeInsets.symmetric(vertical: 18), child: Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: _kEmojis.map((emoji) { final selected = emoji == myCurrent; return GestureDetector(onTap: () { Navigator.pop(context); _toggleReaction(msg, emoji); }, child: Container(padding: const EdgeInsets.all(6), decoration: BoxDecoration(color: selected ? const Color(0xFFEEF1FF) : null, shape: BoxShape.circle), child: Text(emoji, style: const TextStyle(fontSize: 30)))); }).toList()))));
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
      // NEW — forward just this one message straight to a picker.
      ListTile(leading: const Icon(Icons.forward), title: const Text("Forward"), onTap: () { Navigator.pop(context); _forwardOne(msg); }),
      ListTile(leading: const Icon(Icons.emoji_emotions_outlined), title: const Text("React"), onTap: () { Navigator.pop(context); _showReactionPicker(msg); }),
      // NEW — enter multi-select mode (starting with this message already
      // checked) so several messages can be picked and forwarded together.
      ListTile(leading: const Icon(Icons.check_circle_outline), title: const Text("Select"), onTap: () { Navigator.pop(context); _enterSelectionMode(msg); }),
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
      ListTile(leading: const Icon(Icons.delete_outline, color: Colors.red), title: const Text("Delete for me", style: TextStyle(color: Colors.red)), onTap: () { Navigator.pop(context); _deleteMessage(msg, forEveryone: false); }),
      if (isMe) ListTile(leading: const Icon(Icons.delete_forever_outlined, color: Colors.red), title: const Text("Delete for everyone", style: TextStyle(color: Colors.red)), onTap: () { Navigator.pop(context); _deleteMessage(msg, forEveryone: true); }),
    ])));
  }

  // ============================================================
  // NEW — FORWARD (single message, or multi-select forward)
  // ============================================================

  void _enterSelectionMode(MessageModel msg) {
    setState(() {
      _selectionMode = true;
      _selectedMessageIds
        ..clear()
        ..add(msg.id);
    });
  }

  void _exitSelectionMode() {
    setState(() {
      _selectionMode = false;
      _selectedMessageIds.clear();
    });
  }

  void _toggleMessageSelected(MessageModel msg) {
    setState(() {
      if (_selectedMessageIds.contains(msg.id)) {
        _selectedMessageIds.remove(msg.id);
        if (_selectedMessageIds.isEmpty) _selectionMode = false;
      } else {
        _selectedMessageIds.add(msg.id);
      }
    });
  }

  Future<void> _forwardOne(MessageModel msg) => _openForwardPicker([msg.id]);

  Future<void> _forwardSelected() async {
    final ids = _selectedMessageIds.toList();
    _exitSelectionMode();
    await _openForwardPicker(ids);
  }

  Future<void> _openForwardPicker(List<String> messageIds) async {
    if (messageIds.isEmpty) return;
    final ok = await Navigator.of(context).push<bool>(
      MaterialPageRoute(builder: (_) => ForwardMessageScreen(messageIds: messageIds)),
    );
    if (ok == true && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(messageIds.length == 1 ? "Message forwarded" : "${messageIds.length} messages forwarded"),
      ));
    }
  }

  // 🔧 FIX — delete pehle SIRF socket ke bharose tha; socket disconnect
  // hote hi silently kuch nahi hota tha. Ab turant local UI update
  // (optimistic) + reliable REST call (`MessageApiService.deleteMessage`)
  // — socket ki state pe depend nahi karta. Fail hone par revert + error.
  Future<void> _deleteMessage(MessageModel msg, {required bool forEveryone}) async {
    final idx = _messages.indexWhere((m) => m.id == msg.id);
    if (idx == -1) return;

    final prevDeletedForMe = _messages[idx].deletedForMe;
    final prevDeletedForEveryone = _messages[idx].deletedForEveryone;
    final prevText = _messages[idx].text;

    setState(() {
      if (forEveryone) {
        _messages[idx].deletedForEveryone = true;
        _messages[idx].text = '';
      } else {
        _messages[idx].deletedForMe = true;
      }
    });

    _socket.sendDelete(msg.id, forEveryone: forEveryone);

    try {
      await MessageApiService.deleteMessage(msg.id, forEveryone: forEveryone);
    } catch (e) {
      if (mounted) {
        setState(() {
          _messages[idx].deletedForMe = prevDeletedForMe;
          _messages[idx].deletedForEveryone = prevDeletedForEveryone;
          _messages[idx].text = prevText;
        });
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Delete failed: $e")));
      }
    }
  }

  void _showEditDialog(MessageModel msg) {
    final controller = TextEditingController(text: msg.text ?? '');
    showDialog(context: context, builder: (_) => AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),title: const Text("Edit message"), content: TextField(controller: controller, maxLines: 4, autofocus: true), actions: [
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
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
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
      backgroundColor: const Color(0xFFF3F5FA), // 🔥 NAYA — soft, professional cool-grey chat background (WhatsApp-beige ki jagah)
      appBar: _selectionMode ? _buildSelectionAppBar() : AppBar(
        backgroundColor: const Color(0xFF030F27),
        elevation: 3, // 🔥 NAYA — subtle depth, flat/dated na lage
        shadowColor: Colors.black45,
        iconTheme: const IconThemeData(color: Colors.white),
        titleSpacing: 0,
        title: InkWell(
          // 🔥 NAYA — header (avatar + naam) pe tap karke: private chat me
          // seedha otherParticipant.username se profile khulti hai; group
          // chat me ab "Group info" screen khulti hai (members, roles,
          // invite link — jaisa WhatsApp/Telegram me hota hai).
          onTap: widget.conversation.isGroup
              ? _openGroupProfile
              : () => _goToProfile(widget.conversation.otherParticipant?.username ?? ''),
          borderRadius: BorderRadius.circular(8),
          child: Row(children: [
          Stack(clipBehavior: Clip.none, children: [
            CircleAvatar(
              radius: 19,
              backgroundColor: Colors.white24, // 🔥 NAYA — halka ring jaisa fallback background
              child: CircleAvatar(radius: 18, backgroundColor: Colors.grey[300], backgroundImage: widget.conversation.displayPhoto != null && widget.conversation.displayPhoto!.isNotEmpty ? CachedNetworkImageProvider(widget.conversation.displayPhoto!) : null, child: widget.conversation.displayPhoto == null || widget.conversation.displayPhoto!.isEmpty ? Icon(widget.conversation.isGroup ? Icons.group : Icons.person, color: Colors.grey[600], size: 18) : null),
            ),
            // 🔥 NAYA — online hone par avatar pe accent-blue dot
            if (!widget.conversation.isGroup && _otherOnline)
              Positioned(right: -1, bottom: -1, child: Container(width: 11, height: 11, decoration: BoxDecoration(color: const Color(0xFF3D7EFF), shape: BoxShape.circle, border: Border.all(color: const Color(0xFF030F27), width: 2)))),
          ]),
          const SizedBox(width: 12),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            Text(widget.conversation.displayTitle, style: const TextStyle(color: Colors.white, fontSize: 15.5, fontWeight: FontWeight.w600, letterSpacing: 0.1)),
            if (subtitle != null) Padding(padding: const EdgeInsets.only(top: 1), child: Text(subtitle, style: TextStyle(color: _otherTyping ? const Color(0xFF3D7EFF) : Colors.white60, fontSize: 11.5))),
          ])),
        ]),
        ),
        actions: [
          IconButton(icon: const Icon(Icons.cast_for_education, color: Colors.white), tooltip: "Study Room", onPressed: _openStudyRoom),
          IconButton(icon: const Icon(Icons.call, color: Colors.white), tooltip: "Audio Call", onPressed: () => _startCall('audio')),
          IconButton(icon: const Icon(Icons.videocam, color: Colors.white), tooltip: "Video Call", onPressed: () => _startCall('video')),
          // 🔥 NAYA — 3-dot overflow menu: mute/unmute notification
          // (private chat ho ya group, dono ke liye kaam karta hai).
          PopupMenuButton<String>(
            icon: const Icon(Icons.more_vert, color: Colors.white),
            onSelected: (value) {
              if (value == 'toggle_mute') _toggleMuteNotifications();
              if (value == 'filter') _showFilterSheet();
              if (value == 'toggle_block') _toggleBlockUser();
              if (value == 'disappearing_messages') _showDisappearingMessagesSheet();
              if (value == 'group_info') _openGroupProfile();
              if (value == 'leave_group') _leaveGroup();
              if (value == 'delete_group') _deleteGroup();
            },
            itemBuilder: (context) => [
              PopupMenuItem<String>(
                value: 'toggle_mute',
                child: Row(children: [
                  Icon(
                    _isMuted ? Icons.notifications_active_outlined : Icons.notifications_off_outlined,
                    color: Colors.black87,
                    size: 20,
                  ),
                  const SizedBox(width: 10),
                  Text(_isMuted
                      ? (widget.conversation.isGroup ? "Unmute group" : "Unmute notifications")
                      : (widget.conversation.isGroup ? "Mute group" : "Mute notifications")),
                ]),
              ),
              // 🔥 NAYA — Filter messages: Text / Media / Docs / Links
              PopupMenuItem<String>(
                value: 'filter',
                child: Row(children: [
                  Icon(Icons.filter_list, color: _chatFilter != 'all' ? const Color(0xFF3D7EFF) : Colors.black87, size: 20),
                  const SizedBox(width: 10),
                  Text(_chatFilter == 'all' ? "Filter messages" : "Filter: ${_filterLabel(_chatFilter)}"),
                ]),
              ),
              // 🔥 NAYA — Block / Unblock user (sirf 1-to-1 chat me dikhta
              // hai, group me user-level block ka concept hi nahi hai).
              if (!widget.conversation.isGroup && widget.conversation.otherParticipant != null)
                PopupMenuItem<String>(
                  value: 'toggle_block',
                  child: Row(children: [
                    Icon(
                      _isBlocked ? Icons.person_add_alt_1_outlined : Icons.block,
                      color: _isBlocked ? Colors.black87 : Colors.red,
                      size: 20,
                    ),
                    const SizedBox(width: 10),
                    Text(
                      _isBlocked ? "Unblock user" : "Block user",
                      style: TextStyle(color: _isBlocked ? Colors.black87 : Colors.red),
                    ),
                  ]),
                ),
              // 🔥 NAYA — Temporary chat: disappearing messages on/off ya
              // duration change karne ke liye. Private + group dono chat
              // me dikhta hai (group me backend admin/moderator check
              // karega, yahan sirf UI hai).
              PopupMenuItem<String>(
                value: 'disappearing_messages',
                child: Row(children: [
                  Icon(
                    _disappearingDuration == 'none' ? Icons.timer_off_outlined : Icons.timer_outlined,
                    color: _disappearingDuration != 'none' ? const Color(0xFF3D7EFF) : Colors.black87,
                    size: 20,
                  ),
                  const SizedBox(width: 10),
                  Text(_disappearingDuration == 'none'
                      ? "Disappearing messages"
                      : "Disappearing: ${_disappearingLabel(_disappearingDuration)}"),
                ]),
              ),
              // 🔥 NAYA — Group info: members, roles (admin/moderator),
              // invite link, public/private, add/remove members — sab
              // ek jagah (sirf group chat me dikhta hai).
              if (widget.conversation.isGroup)
                const PopupMenuItem<String>(
                  value: 'group_info',
                  child: Row(children: [
                    Icon(Icons.info_outline_rounded, color: Colors.black87, size: 20),
                    SizedBox(width: 10),
                    Text("Group info"),
                  ]),
                ),
              // 🔥 NAYA — Leave group (sirf group chat me dikhta hai).
              if (widget.conversation.isGroup)
                const PopupMenuItem<String>(
                  value: 'leave_group',
                  child: Row(children: [
                    Icon(Icons.exit_to_app, color: Colors.red, size: 20),
                    SizedBox(width: 10),
                    Text("Leave group", style: TextStyle(color: Colors.red)),
                  ]),
                ),
              // 🔥 NAYA — Delete group (ADMIN ONLY — moderator ko bhi
              // nahi dikhta, backend bhi strictly admin role hi allow
              // karta hai).
              if (widget.conversation.isGroup && _isGroupAdmin)
                const PopupMenuItem<String>(
                  value: 'delete_group',
                  child: Row(children: [
                    Icon(Icons.delete_forever, color: Colors.red, size: 20),
                    SizedBox(width: 10),
                    Text("Delete group", style: TextStyle(color: Colors.red)),
                  ]),
                ),
            ],
          ),
          const SizedBox(width: 6),
        ],
      ),
      // 🔥 NAYA — halka doodle-pattern wallpaper, WhatsApp jaisa flat rang nahi
      body: Stack(children: [
        Positioned.fill(child: CustomPaint(painter: _ChatWallpaperPainter())),
        Column(children: [
          Expanded(child: _buildMessageList()),
          _buildReplyPreview(),
          _isBlocked ? _buildBlockedBanner() : _buildInputBar(),
        ]),
      ]),
    );
  }

  // NEW — replaces the normal AppBar while multi-select is active:
  // close (X) to cancel, live count, and a forward icon that opens the
  // conversation picker for every currently-checked message.
  PreferredSizeWidget _buildSelectionAppBar() {
    return AppBar(
      backgroundColor: const Color(0xFF030F27),
      elevation: 3,
      iconTheme: const IconThemeData(color: Colors.white),
      leading: IconButton(icon: const Icon(Icons.close), onPressed: _exitSelectionMode),
      title: Text(
        "${_selectedMessageIds.length} selected",
        style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w600),
      ),
      actions: [
        IconButton(
          icon: const Icon(Icons.forward),
          tooltip: "Forward",
          onPressed: _selectedMessageIds.isEmpty ? null : _forwardSelected,
        ),
        const SizedBox(width: 4),
      ],
    );
  }

  // 🔥 NAYA — jab humne is user ko block kiya hua hai, to normal input
  // bar ki jagah ye banner dikhta hai — na message bheja ja sakta hai,
  // na attachment/mic — sirf ek tap se seedha unblock karne ka option.
  Widget _buildBlockedBanner() {
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: Colors.white,
          border: Border(top: BorderSide(color: Colors.grey[300]!)),
        ),
        child: Row(children: [
          const Icon(Icons.block, color: Colors.red, size: 18),
          const SizedBox(width: 8),
          const Expanded(
            child: Text(
              "You've blocked this user. Unblock to send messages.",
              style: TextStyle(fontSize: 13, color: Colors.black54),
            ),
          ),
          TextButton(onPressed: _toggleBlockUser, child: const Text("Unblock")),
        ]),
      ),
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
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8), border: const Border(left: BorderSide(color: Color(0xFF3D7EFF), width: 4))),
      child: Row(children: [
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          Text(isMe ? "You" : (msg.sender?.displayName ?? ''), style: const TextStyle(color: Color(0xFF3D7EFF), fontWeight: FontWeight.bold, fontSize: 12.5)),
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
    if (_isLoading) return const Center(child: CircularProgressIndicator(color: Color(0xFF030F27)));
    // 🔥 POLISH — plain "Say hi" text ki jagah ab ek proper empty-state
    // card hai (icon + heading + subtext), baaki screens ke empty states
    // jaisa consistent look.
    if (_messages.isEmpty) {
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 84, height: 84,
            decoration: BoxDecoration(color: const Color(0xFF030F27).withOpacity(0.06), shape: BoxShape.circle),
            child: const Icon(Icons.waving_hand_rounded, size: 36, color: Color(0xFF030F27)),
          ),
          const SizedBox(height: 14),
          const Text("Say hi 👋", style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: Colors.black87)),
          const SizedBox(height: 4),
          Text("Send a message to start the conversation", style: TextStyle(fontSize: 12.5, color: Colors.grey[500])),
        ]),
      );
    }

    // 🔥 NAYA — active filter ke hisaab se sirf matching messages dikhao.
    // Date-separator/grouping logic isi filtered list ke andar-andar chalta
    // hai (poore _messages list se nahi), taaki filtered view khud ek
    // consistent chat jaisi dikhe.
    final filtered = _chatFilter == 'all' ? _messages : _messages.where(_matchesFilter).toList();

    return Column(children: [
      if (_chatFilter != 'all') _buildFilterBanner(filtered.length),
      Expanded(
        child: filtered.isEmpty
            ? Center(
                child: Text(
                  "No ${_filterLabel(_chatFilter).toLowerCase()} messages in this chat",
                  style: const TextStyle(color: Colors.black45),
                ),
              )
            : Builder(builder: (context) {
                // typing indicator ko list ke end me ek extra "item" ki tarah treat karte hain
                // (sirf tab jab koi filter active na ho, warna filtered view me ajeeb lagega)
                final showTyping = _otherTyping && _chatFilter == 'all';
                // 🔥 NAYA — "load more" spinner ko list ke SHURU me ek extra
                // "item" ki tarah treat karte hain (sirf jab hum purane
                // messages fetch kar rahe hon). Filter active hone par bhi
                // dikhana theek hai kyunki pagination poori `_messages` list
                // par chalta hai, filtered view par nahi.
                final showLoadingMore = _isLoadingMore;
                final itemCount = filtered.length + (showTyping ? 1 : 0) + (showLoadingMore ? 1 : 0);
                return ListView.builder(
                  controller: _scrollController,
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
                  itemCount: itemCount,
                  itemBuilder: (context, index) {
                    if (showLoadingMore && index == 0) {
                      // 🔥 NAYA — top pe chhota spinner: "purane messages load ho rahe hain"
                      return const Padding(
                        padding: EdgeInsets.symmetric(vertical: 14),
                        child: Center(
                          child: SizedBox(
                            width: 22,
                            height: 22,
                            child: CircularProgressIndicator(strokeWidth: 2.2, color: Color(0xFF030F27)),
                          ),
                        ),
                      );
                    }
                    final adjustedIndex = showLoadingMore ? index - 1 : index;
                    if (showTyping && adjustedIndex == filtered.length) {
                      return const _TypingBubble(); // 🔥 NAYA — animated 3-dot bubble
                    }
                    final msg = filtered[adjustedIndex];
                    final isMe = msg.sender?.id == _myUserId;

                    // 🔥 NAYA — date separator: pichle message se din badal gaya to divider dikhao
                    final prev = adjustedIndex > 0 ? filtered[adjustedIndex - 1] : null;
                    final showDateSeparator = prev == null || !_isSameDay(prev.createdAt, msg.createdAt);

                    // 🔥 NAYA — consecutive grouping (bubble tail sirf group ke last message pe)
                    final next = adjustedIndex < filtered.length - 1 ? filtered[adjustedIndex + 1] : null;
                    final isLastInGroup = next == null || next.sender?.id != msg.sender?.id || !_isSameDay(next.createdAt, msg.createdAt);
                    final isFirstInGroup = prev == null || prev.sender?.id != msg.sender?.id || showDateSeparator;

                    final replyPreview = _findMessageById(msg.replyTo);

                    final isSelected = _selectedMessageIds.contains(msg.id);
                    final bubble = _SwipeToReply(
                      isMe: isMe,
                      onReply: () => _startReply(msg),
                      child: _MessageBubble(
                        message: msg,
                        isMe: isMe,
                        isGroup: widget.conversation.isGroup, // 🔥 NAYA — group me sender ka naam bubble ke upar dikhane ke liye
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
                    );

                    return Column(children: [
                      if (showDateSeparator) _DateSeparator(date: msg.createdAt),
                      // NEW — during multi-select, tapping anywhere on the
                      // row toggles the checkbox instead of the message's
                      // normal tap behaviour (media viewer, link open,
                      // etc.), which is why the bubble itself is wrapped
                      // in AbsorbPointer while selection mode is active.
                      GestureDetector(
                        behavior: HitTestBehavior.translucent,
                        onTap: _selectionMode ? () => _toggleMessageSelected(msg) : null,
                        child: Container(
                          color: isSelected ? const Color(0xFF3D7EFF).withOpacity(0.12) : null,
                          child: Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
                            if (_selectionMode)
                              Padding(
                                padding: const EdgeInsets.only(left: 6, right: 2),
                                child: Icon(
                                  isSelected ? Icons.check_circle : Icons.radio_button_unchecked,
                                  size: 20,
                                  color: isSelected ? const Color(0xFF3D7EFF) : Colors.grey,
                                ),
                              ),
                            Expanded(
                              child: AbsorbPointer(absorbing: _selectionMode, child: bubble),
                            ),
                          ]),
                        ),
                      ),
                    ]);
                  },
                );
              }),
      ),
    ]);
  }

  // 🔥 NAYA — filter active hone par top pe ek chhota banner: kaunsa
  // filter laga hai + kitne messages mile + ek tap me clear karne ka option.
  Widget _buildFilterBanner(int count) {
    return Container(
      width: double.infinity,
      color: const Color(0xFFE7EEFC),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      child: Row(children: [
        const Icon(Icons.filter_list, size: 16, color: Color(0xFF2457C5)),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            "${_filterLabel(_chatFilter)} • $count message${count == 1 ? '' : 's'}",
            style: const TextStyle(color: Color(0xFF2457C5), fontSize: 12.5, fontWeight: FontWeight.w600),
          ),
        ),
        GestureDetector(
          onTap: () => setState(() => _chatFilter = 'all'),
          child: const Padding(
            padding: EdgeInsets.symmetric(horizontal: 4, vertical: 2),
            child: Text("Clear", style: TextStyle(color: Color(0xFF2457C5), fontSize: 12.5, fontWeight: FontWeight.bold)),
          ),
        ),
      ]),
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
      return SafeArea(child: Padding(padding: const EdgeInsets.fromLTRB(10, 6, 10, 10), child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(28),
          boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.08), blurRadius: 10, offset: const Offset(0, 3))],
        ),
        child: Row(children: [
          IconButton(icon: const Icon(Icons.delete_outline, color: Colors.red), onPressed: _cancelRecording),
          Expanded(child: Row(children: [
            const Icon(Icons.fiber_manual_record, color: Colors.red, size: 14),
            const SizedBox(width: 8),
            Text(_fmtRecordDuration(_recordDuration), style: const TextStyle(fontSize: 15, color: Color(0xFF030F27), fontWeight: FontWeight.w600)),
            const SizedBox(width: 8),
            const Text("Recording...", style: TextStyle(color: Colors.black45, fontSize: 13)),
          ])),
          const SizedBox(width: 8),
          Container(
            decoration: BoxDecoration(shape: BoxShape.circle, boxShadow: [BoxShadow(color: const Color(0xFF030F27).withOpacity(0.35), blurRadius: 8, offset: const Offset(0, 2))]),
            child: CircleAvatar(backgroundColor: const Color(0xFF030F27), child: IconButton(icon: const Icon(Icons.send, color: Colors.white), onPressed: _stopRecordingAndSend)),
          ),
        ]),
      )));
    }

    // 🔥 NAYA — attach + emoji + text field ab ek hi floating white "card"
    // ke andar hain (subtle shadow, fully rounded) — flat/dated bar ki
    // jagah modern messaging-app jaisa look. Send/mic button bahar,
    // apna elevated circle, taaki primary action visually stand-out kare.
    return SafeArea(child: Padding(padding: const EdgeInsets.fromLTRB(10, 6, 10, 10), child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
      Expanded(
        child: Container(
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(26),
            boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 8, offset: const Offset(0, 2))],
          ),
          child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
            IconButton(icon: const Icon(Icons.attach_file, color: Color(0xFF030F27)), onPressed: _showAttachmentSheet),
            // 🔥 NAYA — apna sticker picker (assets/stickers/). Tap karte hi
            // chosen sticker seedha ek image message ki tarah bhej diya jaata
            // hai (WhatsApp jaisa — koi text nahi banta).
            IconButton(
              icon: const Icon(Icons.emoji_emotions_outlined, color: Color(0xFF030F27)),
              onPressed: () => showStickerPicker(
                context,
                onSelected: (assetPath) => _sendSticker(assetPath),
              ),
            ),
            Expanded(child: TextField(controller: _textController, onChanged: _onTypingChanged, minLines: 1, maxLines: 4, style: const TextStyle(fontSize: 14.5), decoration: const InputDecoration(hintText: "Message...", hintStyle: TextStyle(color: Colors.black38), filled: false, contentPadding: EdgeInsets.symmetric(horizontal: 4, vertical: 12), border: InputBorder.none))),
            const SizedBox(width: 4),
          ]),
        ),
      ),
      const SizedBox(width: 8),
      // 🔥 NAYA — WhatsApp jaisa hi: text khaali ho to "mic" (voice note),
      // kuch type kiya ho to "send" — ValueListenableBuilder se text
      // controller change hote hi ye button khud switch ho jaata hai.
      ValueListenableBuilder<TextEditingValue>(
        valueListenable: _textController,
        builder: (_, value, __) {
          final hasText = value.text.trim().isNotEmpty;
          return Container(
            decoration: BoxDecoration(shape: BoxShape.circle, boxShadow: [BoxShadow(color: const Color(0xFF030F27).withOpacity(0.32), blurRadius: 8, offset: const Offset(0, 2))]),
            child: CircleAvatar(
              radius: 23,
              backgroundColor: const Color(0xFF030F27),
              child: IconButton(
                icon: Icon(hasText ? Icons.send : Icons.mic, color: Colors.white),
                onPressed: hasText ? _sendMessage : _startRecording,
              ),
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
        decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(14), boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.07), blurRadius: 6, offset: const Offset(0, 2))]),
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
    final paint = Paint()..color = const Color(0xFFDCE3F0).withOpacity(0.35)..style = PaintingStyle.fill;
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
  final bool isGroup; // 🔥 NAYA — group chat me sender ka naam bubble ke upar dikhane ke liye
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
    this.isGroup = false,
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

    // 🔥 NAYA — STICKER message: WhatsApp/Telegram jaisa hi, koi colored
    // chat-bubble background nahi — bada transparent sticker + chhota
    // timestamp overlay uske bottom-right corner pe.
    if (message.type == MessageType.image && message.meta?['is_sticker'] == true) {
      return _buildStickerMessage(context);
    }

    final bubbleColor = isMe ? const Color(0xFF16325C) : Colors.white; // 🔥 WhatsApp jaisa dark-teal sent bubble
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
            decoration: BoxDecoration(color: bubbleColor, borderRadius: radius, boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.07), blurRadius: 6, offset: const Offset(0, 2))]),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
              // 🔥 NAYA — group chat me, apne khud ke message ko chhod ke,
              // har naye sender-block ke pehle bubble ke upar naam dikhao
              // (WhatsApp jaisa) — taaki pata chale kisne msg bheja.
              if (isGroup && !isMe && isFirstInGroup)
                Padding(
                  padding: const EdgeInsets.only(left: 4, bottom: 2),
                  child: Text(
                    message.sender?.displayName ?? 'Unknown',
                    style: TextStyle(color: _senderColor(message.sender?.id), fontWeight: FontWeight.bold, fontSize: 12.5),
                  ),
                ),
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

  // 🔥 NAYA — STICKER bubble: normal image message pipeline (upload,
  // download, tick, fullscreen-view) hi reuse karta hai, bas dikhta hai
  // WhatsApp jaisa — koi colored container/background nahi, sirf bada
  // sticker + niche-right corner me chhota semi-transparent timestamp.
  Widget _buildStickerMessage(BuildContext context) {
    const double size = 128;
    final url = (message.fileUrl != null && message.fileUrl!.isNotEmpty)
        ? message.fileUrl
        : ((message.fileUrls != null && message.fileUrls!.isNotEmpty) ? message.fileUrls!.first.toString() : null);
    final localPath = message.localFilePath;

    Widget image;
    if (localPath != null && message.isSending) {
      image = Image.file(File(localPath), width: size, height: size, fit: BoxFit.contain);
    } else if (url != null && url.isNotEmpty) {
      image = CachedNetworkImage(
        imageUrl: url,
        width: size,
        height: size,
        fit: BoxFit.contain,
        placeholder: (_, __) => const SizedBox(width: size, height: size, child: Center(child: CircularProgressIndicator(strokeWidth: 2))),
        errorWidget: (_, __, ___) => const SizedBox(width: size, height: size, child: Icon(Icons.broken_image, color: Colors.grey)),
      );
    } else {
      image = const SizedBox(width: size, height: size, child: Icon(Icons.image, color: Colors.grey));
    }

    return GestureDetector(
      onLongPress: message.deletedForEveryone ? null : onLongPress,
      child: Align(
        alignment: isMe ? Alignment.centerRight : Alignment.centerLeft,
        child: Column(crossAxisAlignment: isMe ? CrossAxisAlignment.end : CrossAxisAlignment.start, children: [
          if (isGroup && !isMe && isFirstInGroup)
            Padding(
              padding: const EdgeInsets.only(left: 4, bottom: 2),
              child: Text(
                message.sender?.displayName ?? 'Unknown',
                style: TextStyle(color: _senderColor(message.sender?.id), fontWeight: FontWeight.bold, fontSize: 12.5),
              ),
            ),
          Container(
            margin: EdgeInsets.only(top: isFirstInGroup ? 6 : 2),
            child: SizedBox(
              width: size,
              height: size,
              child: Stack(alignment: Alignment.bottomRight, children: [
                image,
                if (message.isSending)
                  SizedBox(
                    width: size,
                    height: size,
                    child: Center(
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        value: (message.uploadProgress != null && message.uploadProgress! > 0) ? message.uploadProgress : null,
                      ),
                    ),
                  ),
                Padding(
                  padding: const EdgeInsets.only(right: 4, bottom: 3),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1.5),
                    decoration: BoxDecoration(color: Colors.black.withOpacity(0.35), borderRadius: BorderRadius.circular(8)),
                    child: Row(mainAxisSize: MainAxisSize.min, children: [
                      Text(
                        "${message.createdAt.hour.toString().padLeft(2, '0')}:${message.createdAt.minute.toString().padLeft(2, '0')}",
                        style: const TextStyle(fontSize: 10, color: Colors.white),
                      ),
                      if (isMe) ...[const SizedBox(width: 3), _buildTick()],
                    ]),
                  ),
                ),
              ]),
            ),
          ),
          if (message.reactions.isNotEmpty) _buildReactionRow(),
        ]),
      ),
    );
  }

  // 🔥 NAYA — group chat me har sender ko ek consistent (fixed) color
  // milta hai — jaise WhatsApp me har member ka naam alag color me
  // dikhta hai. userId ka hash use karte hain taaki wahi user hamesha
  // wahi color paaye (chahe list kitni bhi baar rebuild ho).
  static const List<Color> _senderPalette = [
    Color(0xFFE53935), Color(0xFF00897B), Color(0xFF3D7EFF), Color(0xFF8E24AA),
    Color(0xFFF4511E), Color(0xFF43A047), Color(0xFF6D4C41), Color(0xFFD81B60),
  ];
  Color _senderColor(String? userId) {
    if (userId == null || userId.isEmpty) return _senderPalette[0];
    final hash = userId.codeUnits.fold<int>(0, (acc, c) => acc + c);
    return _senderPalette[hash % _senderPalette.length];
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
        decoration: BoxDecoration(color: textColor == Colors.white ? Colors.white.withOpacity(0.12) : Colors.black.withOpacity(0.05), borderRadius: BorderRadius.circular(6), border: const Border(left: BorderSide(color: Color(0xFF3D7EFF), width: 3))),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          Text(r.sender?.displayName ?? '', style: const TextStyle(color: Color(0xFF3D7EFF), fontWeight: FontWeight.bold, fontSize: 11.5)),
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
      // 🔥 NAYA — plain text ab _LinkifiedText se render hota hai, taaki
      // agar message me koi URL (http/https/www.) ho to wo clickable
      // link ki tarah dikhe (blue + underline) aur tap karne par khul
      // jaaye. URL na ho to ye bilkul normal Text jaisa hi behave karta hai.
      default: return _LinkifiedText(text: message.text ?? '', color: textColor);
    }

    // 🔥 NAYA: media ke saath caption ho (gallery-preview screen se) to
    // WhatsApp jaisa hi media ke neeche caption text dikhta hai — caption
    // me bhi URL ho to clickable link banega.
    final caption = message.text?.trim();
    if (caption == null || caption.isEmpty) return media;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
      media,
      Padding(
        padding: const EdgeInsets.only(top: 5, left: 2, right: 2),
        child: _LinkifiedText(text: caption, color: textColor),
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
          // 🔥 NAYA: tap se ab poori screen pe swipeable + zoomable
          // MediaViewerScreen khulti hai (single image ho to bhi ek-item
          // wali list bhej dete hain — viewer khud handle karta hai).
          onTap: url != null && !message.isSending
              ? () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => MediaViewerScreen(
                        urls: [url],
                        initialIndex: 0,
                        onDownload: (u) => onDownloadUrl != null ? onDownloadUrl!(u) : onDownload(),
                        isDownloaded: (u) => isUrlDownloaded != null ? isUrlDownloaded!(u) : isDownloaded,
                      ),
                    ),
                  )
              : null,
          onLongPress: url != null && !message.isSending ? onDownload : null,
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
  // khulta hai (thumbnail strip ke saath); long-press us specific photo
  // ko download karta hai.
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
        // 🔥 NAYA: is photo se shuru hoke poore album ka fullscreen
        // swipeable viewer khulta hai — MediaViewerScreen ko poori
        // urls list + initialIndex (yahi tap ki hui photo) pass karte hain.
        onTap: canInteract
            ? () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => MediaViewerScreen(
                      urls: urls,
                      initialIndex: i,
                      onDownload: (u) => onDownloadUrl?.call(u),
                      isDownloaded: (u) => isUrlDownloaded?.call(u) ?? false,
                    ),
                  ),
                )
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
    // 🔥 NAYA: preview screen ke andar "+" se add karne ke liye — agar
    // ismein already sirf-images hain to images-only picker, agar
    // videos hain to sirf-videos picker; agar mixed hain (kabhi purana
    // album ho) to dono allow karne wala mixed picker try karte hain.
    final hasVideo = _files.any(_isVideo);
    final hasImage = _files.any((f) => !_isVideo(f));
    List<XFile> raw;
    if (hasVideo && !hasImage) {
      const videoGroup = XTypeGroup(
        label: 'video',
        extensions: ['mp4', 'mov', 'mkv', '3gp', 'webm', 'avi', 'm4v'],
      );
      raw = await openFiles(acceptedTypeGroups: [videoGroup]);
    } else if (hasImage && !hasVideo) {
      raw = await ImagePicker().pickMultiImage(imageQuality: 85);
    } else {
      try {
        raw = await ImagePicker().pickMultipleMedia(imageQuality: 85);
      } catch (_) {
        raw = await ImagePicker().pickMultiImage(imageQuality: 85);
      }
    }
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
    _player.setAudioContext(AudioContext(
      android: const AudioContextAndroid(
        isSpeakerphoneOn: true,
        stayAwake: false,
        contentType: AndroidContentType.music,
        usageType: AndroidUsageType.media,
        audioFocus: AndroidAudioFocus.gain,
      ),
      iOS: AudioContextIOS(
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
                    if (_controller.value.isBuffering)
                      const Center(child: CircularProgressIndicator(color: Colors.white)),
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