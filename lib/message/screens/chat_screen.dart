// message/screens/chat_screen.dart
//
// 🌐 LANGUAGE + 🎨 THEME PASS: every user-visible string now comes from AppLocalizations
// (lib/l10n/app_en.arb / app_hi.arb, keys `chat*`), and the input bar / banners / dialogs /
// date chips use the theme (ColorScheme + AppThemeTokens) so light and dark mode both work.
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
import '../services/ai_study_service.dart'; // 🔥 NAYA — manual voice-note transcribe button
import '../../services/auth_service.dart';
import 'doubts_screen.dart'; // 🔥 NAYA — "Doubts" tab (persistent upvotable question board + anonymous asking)
import '../../profile/screens/target_profile.dart'; // 🔥 NAYA — user profile pe navigate karne ke liye
import '../../profile/api_service.dart' as ProfileApi; // 🔥 NAYA
import '../../home.dart'; // 🔥 NAYA — apni khud ki profile pe tap karne par Home ke Profile tab pe bhejne ke liye
import 'call_screen.dart';
import 'incoming_call_screen.dart'; // 🔥 NAYA — full-screen incoming call UI (outgoing call jaisa look)
import 'study_room_screen.dart';
import '../models/study_room_models.dart'; // 🔧 FIX — UserProfileWindowModel chahiye initialParticipants banane ke liye
import 'forward_message_screen.dart'; // NEW — pick chat(s) to forward selected message(s) to
import 'group_profile_screen.dart'; // 🔥 NAYA — Group info screen (public/private, members, admin roles, invite link)
import 'media_viewer_screen.dart'; // 🔥 NAYA — fullscreen swipeable image viewer (zoom + auto-hide thumbnail strip)
import '../../widgets/sticker_picker_sheet.dart'; // 🔥 NAYA — apne PNG stickers ka picker (assets/stickers/), chat & comments dono me reusable
import '../widgets/translatable_message_widgets.dart'; // 🔥 NAYA — Features 9/10 (Listen + Translate), ab actually wired
import '../services/translate_service.dart'; // 🔥 NAYA — Task 6: Translate ab 3-dot menu se on/off hone wala permission hai
import '../../theme_service.dart'; // 🎨 THEME FIX — AppThemeTokens
import 'message_search_screen.dart'; // 🔥 NAYA (Phase 4, §2.1) — in-chat message search
import 'message_info_screen.dart'; // 🔥 NAYA — "Seen by" / message-info (long-press → Info)
import '../widgets/mention_suggestions_overlay.dart'; // 🔥 NAYA (Phase 3, §2.2) — @mention autocomplete
import '../../l10n/app_localizations.dart'; // 🌐 LANGUAGE FIX — chat text now comes from the ARB files (en/hi)
import 'package:intl/intl.dart' show DateFormat; // localized date labels (`show` avoids intl's TextDirection clash)

/// 🌐 LANGUAGE — gives every State in this file a `_l10n` getter.
/// The last-resolved [AppLocalizations] is cached, so strings can still be looked up
/// safely after an `await` when the State may already be unmounted (no
/// "deactivated widget's ancestor" crash), while a language switch is still picked up
/// on the next access while mounted.
mixin _L10nCache<T extends StatefulWidget> on State<T> {
  AppLocalizations? _l10nCache;

  AppLocalizations get _l10n {
    if (mounted) _l10nCache = AppLocalizations.of(context)!;
    return _l10nCache!;
  }
}

const _kEmojis = ['👍', '❤', '😂', '😮', '😢', '🙏'];

// M5-FE — double-tap reaction. `_kEmojis[1]` wala hi string use karte hain
// (variation selector ke bina) taaki purane aur naye ❤ reactions ek hi chip
// me group hon.
const _kDoubleTapEmoji = '❤';

// M5-FE — strip ke "+" button ka extended grid (koi emoji package nahi hai
// pubspec me, isliye chhoti in-house list).
const _kMoreEmojis = [
  '👍', '👎', '❤', '😂', '😮', '😢', '🙏', '🔥',
  '🎉', '👏', '😍', '🤔', '😅', '😭', '😡', '🥳',
  '💯', '✅', '❌', '👀', '🙌', '💪', '😎', '🤝',
  '😊', '😁', '🥹', '😴', '🤯', '😇', '🙄', '😬',
  '👌', '✌', '🤞', '👋', '🫡', '🤗', '😘', '💔',
];

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
          // 🎨 THEME FIX — was `color == Colors.white ? … : …`, which only held in
          // light mode. Sent bubble (text == onPrimary): light-blue in light mode, ink
          // in dark mode (bubble is light violet there). Received bubble: theme info blue.
          color: color == Theme.of(context).colorScheme.onPrimary
              ? (Theme.of(context).brightness == Brightness.light ? const Color(0xFFB3E5FC) : color)
              : AppThemeTokens.of(context).info,
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

// 🔥 NAYA (Phase 2, §7.5/§4.1) — link-preview card jo text message ke
// neeche render hota hai jab backend ne message.linkPreview generate ki
// ho (`meta['link_preview']` → `LinkPreviewModel`, message_models.dart).
// Tap karne pe wahi URL externally khulta hai (jaisa `_LinkifiedText` me
// hota hai). Image na ho ya load na ho paaye to bhi title/description
// dikhte rehte hain — card kabhi crash/blank nahi hota.
class _LinkPreviewCard extends StatelessWidget {
  final LinkPreviewModel preview;
  final Color textColor;
  const _LinkPreviewCard({required this.preview, required this.textColor});

  Future<void> _open() async {
    final uri = Uri.tryParse(preview.url);
    if (uri == null) return;
    try {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    } catch (_) {
      // link open nahi ho paaya — chat UI break nahi hona chahiye.
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final onSent = textColor == cs.onPrimary; // sent bubble (was `== Colors.white`, light-mode only)
    final hasImage = preview.image != null && preview.image!.isNotEmpty;
    return GestureDetector(
      onTap: _open,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 240),
        decoration: BoxDecoration(
          color: onSent ? textColor.withOpacity(0.10) : AppThemeTokens.of(context).surface2,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: onSent ? textColor.withOpacity(0.24) : cs.outlineVariant),
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            if (hasImage)
              CachedNetworkImage(
                imageUrl: preview.image!,
                height: 110,
                width: double.infinity,
                fit: BoxFit.cover,
                placeholder: (_, __) => const SizedBox(height: 110, child: Center(child: CircularProgressIndicator(strokeWidth: 2))),
                errorWidget: (_, __, ___) => const SizedBox.shrink(),
              ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (preview.title != null && preview.title!.isNotEmpty)
                    Text(
                      preview.title!,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(color: textColor, fontWeight: FontWeight.w600, fontSize: 12.5),
                    ),
                  if (preview.description != null && preview.description!.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 2),
                      child: Text(
                        preview.description!,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(color: textColor.withOpacity(0.7), fontSize: 11.5),
                      ),
                    ),
                  Padding(
                    padding: const EdgeInsets.only(top: 3),
                    child: Text(
                      preview.url,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(color: textColor.withOpacity(0.5), fontSize: 10.5),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
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
  // 🔥 NAYA (Phase 4, §2.1) — MessageSearchScreen se ek specific message
  // pe seedha jump karke aana ho to iska id diya jaata hai. Screen khulte
  // hi history load hoke, zaroorat pade to purana pagination bhi chalke,
  // us message tak scroll + flash-highlight karega.
  final String? jumpToMessageId;
  // 🔥 NAYA (M1-FE) — MessageRequestsScreen se khulne par true: neeche
  // Accept / Delete / Block bar dikhta hai aur reply input band rehta hai
  // jab tak accept na ho. Kisi aur raste se (push/search) khulne par ye
  // false hota hai, tab `_loadRequestStatus()` server se status dekh leta hai.
  final bool isMessageRequest;
  const ChatScreen({super.key, required this.conversation, this.jumpToMessageId, this.isMessageRequest = false});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> with _L10nCache<ChatScreen> {
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
  // M6-FE — socket se bheje text/study-room ka koi ack nahi hota; echo na aaye to
  // ye timer message ko "failed" mark karta hai (warna clock forever ghumti).
  final Map<String, Timer> _sendWatchdogs = {};
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
  // 🔥 NAYA (M4a) — waveform: recording ke dauran `record` ke amplitude stream
  // (har 100ms) se normalized (0..1) samples jama hote hain; send par 40 bars
  // me downsample hoke `meta['waveform']` me jaate hain.
  StreamSubscription<Amplitude>? _ampSub;
  final List<double> _ampSamples = [];

  // 🔥 NAYA (M4c) — lock-to-record + slide-to-cancel. Gesture raw pointer events
  // (`Listener`) se handle hota hai, GestureDetector se nahi: recording shuru
  // hote hi input bar ka UI badal jaata hai (mic button tree se hat jaata hai),
  // aur widget-level gesture recognizer dispose hote hi release event kho deta.
  // Stable ancestor `Listener` (`_buildInputBar`) poore gesture ko track karta hai.
  bool _recordLocked = false;   // upar swipe se lock: haath hata sakte ho
  bool _recordPaused = false;   // lock mode me pause/resume
  bool _micStarting = false;    // recorder.start() chal raha hai
  bool _micReleased = false;    // start ke dauran haath utha liya
  int? _micPointer;
  Offset _micStart = Offset.zero;
  Timer? _micHoldTimer;
  double _slideDx = 0, _slideDy = 0; // UI feedback (<=0)
  DateTime _lastMicPointerAt = DateTime.fromMillisecondsSinceEpoch(0); // vanish-swipe se conflict rokne ke liye
  static const double _kLockDistance = 70;   // itna upar -> lock
  static const double _kCancelDistance = 110; // itna left -> cancel

  // 🔥 NAYA — appbar ke 3-dot menu me mute/unmute notification toggle.
  // Private chat ho ya group, dono ke liye same hi flag hai (per-user
  // ConversationParticipant.is_muted), isliye alag logic nahi chahiye.
  bool _isMuted = false;

  // 🔥 NAYA — private chat me doosre user ko block/unblock karne ke liye.
  // Sirf 1-to-1 chat me relevant hai (group me nahi dikhta).
  bool _isBlocked = false;

  // 🔥 NAYA (M1-FE) — ye chat mere liye abhi "message request" hai (pending).
  // True rehte tak input bar ki jagah Accept/Delete/Block bar dikhta hai.
  bool _isPendingRequest = false;
  bool _requestBusy = false; // accept/delete/block call chal rahi hai

  // 🔥 NAYA — chat filter (3-dot menu se): 'all' | 'text' | 'media' | 'docs' | 'links'
  // 'all' matlab koi filter nahi, poori chat normal dikhti hai.
  String _chatFilter = 'all';

  // 🔥 NAYA — Temporary chat (disappearing messages) ka current duration:
  // 'none' | '1_month' | '6_months' | '1_year'. Backend default '6_months'
  // rakhta hai, isliye yahan bhi wahi default rakha hai jab tak asli value
  // load na ho jaaye (taaki menu me galat "Off" na flash ho ek pal ke liye).
  String _disappearingDuration = '6_months';

  // 🔥 NAYA (M3a-FE) — Vanish mode quick toggle (swipe-up). Backend ke
  // 🔥 M3b — private chat me vanish mode = BE ka 'after_seen' (padhne + chat
  // band hone par delete). Group me 'after_seen' allowed nahi (BE reject karta
  // hai), isliye wahan purana fallback: sabse chhota time-based duration '1_month'.
  // `_vanishPrevDuration` swipe-off par wapas restore karne ke liye.
  String get _kVanishDuration => widget.conversation.isGroup ? '1_month' : 'after_seen';
  bool _vanishMode = false;
  String? _vanishPrevDuration;
  bool _vanishBusy = false; // double-swipe pe parallel API calls rokne ke liye

  // 🔥 NAYA — "Delete group" option sirf group ke ADMIN (moderator/member
  // nahi) ko dikhane ke liye — apni role group detail se load karte hain.
  bool _isGroupAdmin = false;

  // 🔥 NAYA — ACCESS CONTROL SYSTEM: teen alag roles (admin/moderator/
  // member), har ek ki alag capability:
  //   - admin: sab kuch (delete group, message-permission/limit set karna,
  //     photo change, join-requests approve/reject)
  //   - moderator: photo change + message-permission/limit set karna +
  //     join-requests handle karna (delete group NAHI — sirf admin)
  //   - member: sirf group ki current `message_permission` policy follow
  //     karta hai (agar "admins_mods" set hai to bilkul message nahi bhej
  //     sakta) + `daily_message_limit` se bandha hota hai
  // Sab enforcement backend (`check_group_send_permission`) pe already
  // hoti hai — ye flags sirf UI ko sahi buttons/banners dikhane ke liye
  // hain, security ka source-of-truth backend hi hai.
  bool _isGroupModerator = false;
  // FE canonical values: 'everyone' | 'admins_mods'. Backend model value
  // 'admins_only' (M9a) aur FE alias 'admins_mods' dono same maane jaate hain —
  // `_normalizeGroupPermission` har source (REST/WS) pe 'admins_mods' bana deta hai.
  String _groupMessagePermission = 'everyone';
  int? _groupDailyLimit; // null = no limit
  int _pendingJoinRequestsCount = 0; // admin/mod badge (private group only)

  // 🔥 NAYA — session-local "aaj kitne message bheje" counter. Backend hi
  // asli limit enforce karta hai (403 + code milega limit cross hone pe),
  // ye sirf best-effort local estimate hai taaki member ko pehle se hi
  // andaza mil jaaye ki kitne bache hain — app restart pe reset ho jaata
  // hai, isliye kabhi bhi security check ke liye trust mat karna.
  int _myMessagesSentToday = 0;
  DateTime _messagesCounterDay = DateTime.now();

  bool get _isGroupAdminOrMod => _isGroupAdmin || _isGroupModerator;

  // M9a-FE — backend 'admins_only' / FE 'admins_mods' -> 'admins_mods'.
  static String _normalizeGroupPermission(Object? v) {
    final s = v?.toString() ?? 'everyone';
    return (s == 'admins_only' || s == 'admins_mods') ? 'admins_mods' : s;
  }

  // group ne "sirf admin/moderator hi bhej sakte hain" set kiya hua hai
  // aur main sirf ek normal member hoon -> composer band, banner dikhao.
  bool get _isMessagingRestrictedForMe =>
      widget.conversation.isGroup &&
      _groupMessagePermission == 'admins_mods' &&
      !_isGroupAdminOrMod;

  // NEW — multi-select mode for forwarding messages. When active, the
  // normal AppBar is swapped for a selection bar (count + forward icon),
  // tapping a message toggles it in/out of `_selectedMessageIds` instead
  // of opening it, and long-press/media-tap gestures are absorbed.
  bool _selectionMode = false;
  final Set<String> _selectedMessageIds = {};

  // 🔥 NAYA — WHOLE CHAT-SCREEN WALLPAPER (WhatsApp jaisa)
  // Poori chat screen ka background image (message bubbles ke peeche,
  // saara chat screen), ek message ki bubble ka background NAHI. Backend
  // me `ConversationParticipant.wallpaper_url` field + GET/PATCH
  // /message/conversations/<id>/wallpaper/ endpoint hai (views.py
  // `ConversationViewSet.wallpaper`) — per-user setting hai (sirf apne
  // account ke liye, dusre participant/group members ko nahi dikhega).
  // `null` matlab koi custom wallpaper nahi hai, default doodle-pattern
  // (`_ChatWallpaperPainter`) dikhega.
  String? _wallpaperUrl;
  bool _wallpaperUploading = false;

  // 🔥 NAYA — PINNED MESSAGES: is chat ke pinned messages (max 3, backend
  // cap). Top banner isi list se render hota hai — dekho _buildPinnedBanner().
  List<PinnedMessageModel> _pinnedMessages = [];

  // 🔥 NAYA (Phase 4, §2.1) — MessageSearchScreen se "jump to message" karke
  // aane par, ya reply-quote pe tap karne par, us message ko thodi der ke
  // liye flash-highlight karna. `_highlightTimer` purana highlight clear
  // karta hai jab naya trigger ho ya duration khatam ho jaaye.
  String? _highlightedMessageId;
  Timer? _highlightTimer;

  // 🔥 NAYA (Phase 3, §2.2) — @Mention autocomplete: group ke active
  // members (sirf group chat me load hote hain, private chat me hamesha
  // khaali rehti hai — mention private chat me possible hi nahi hai).
  // `_mentionQuery` non-null hote hi overlay render hota hai (empty string
  // = "@" abhi-abhi type hua hai, sab members dikhao).
  List<UserMini> _groupMembers = [];
  String? _mentionQuery;

  // 🔥 NAYA (Phase 3, §7.10) — server-side draft autosave: text change
  // hone par debounce (1-2s) karke PATCH karta hai. `_lastSavedDraft` se
  // compare karte hain taaki same text baar-baar save na ho (harmless hai
  // par unnecessary API calls bachate hain).
  Timer? _draftSaveTimer;
  String? _lastSavedDraft;

  // 🔥 NAYA (Phase 3, §1 #11) — Smart-reply suggestion chips. Sirf tab
  // fetch hota hai jab last message requester ka apna na ho (matlab
  // dusre ne bheja ho) — throttle scope `ai_smart_reply` 30/min hai,
  // isliye client bhi `_kSmartReplyCooldown` ka reasonable cooldown
  // rakhta hai taaki har naye incoming message pe call na ho.
  List<String> _smartReplies = [];
  DateTime? _lastSmartReplyFetch;
  static const Duration _kSmartReplyCooldown = Duration(seconds: 15);

  Future<void> _loadPinnedMessages() async {
    try {
      final pins = await MessageApiService.getPinnedMessages(widget.conversation.id);
      if (!mounted) return;
      setState(() => _pinnedMessages = pins);
    } catch (_) {
      // silent — pinned banner bas nahi dikhega, chat load hona nahi rukna chahiye
    }
  }

  // 🔥 NAYA (Phase 3, §2.2) — group hi ho to active members load karo
  // (mention suggestion list ke liye). Private chat me no-op.
  Future<void> _loadGroupMembers() async {
    if (!widget.conversation.isGroup) return;
    final groupId = widget.conversation.group?.id;
    if (groupId == null || groupId.isEmpty) return;
    try {
      final members = await MessageApiService.getGroupActiveMembers(groupId);
      if (mounted) setState(() => _groupMembers = members);
    } catch (_) {
      // silent — overlay bas nahi dikhega, "@" typing normal text jaisa hi rahega
    }
  }

  // 🔥 NAYA (Phase 4, §2.1) — MessageSearchScreen se select hue message tak
  // pahochne ke liye: pehle jo already-loaded `_messages` me maujood hai
  // wahi scroll+highlight karo. Agar nahi mila (purana message, abhi tak
  // pagination se load nahi hua) to `_loadMoreMessages()` baar-baar call
  // karke aur purana history laate raho (max 25 pages tak — safety cap,
  // taaki koi corrupt/missing id infinite loop na bana de).
  Future<void> _tryJumpToInitialMessage() async {
    final targetId = widget.jumpToMessageId;
    if (targetId == null) return;
    await _tryJumpToMessageId(targetId);
  }

  // 🔥 NAYA (Phase 4, §2.1) — reusable: pehle jo already-loaded `_messages`
  // me maujood hai wahi scroll+highlight karo. Agar nahi mila (purana
  // message, abhi tak pagination se load nahi hua) to `_loadMoreMessages()`
  // baar-baar call karke aur purana history laate raho (max 25 pages tak —
  // safety cap, taaki koi corrupt/missing id infinite loop na bana de).
  Future<void> _tryJumpToMessageId(String targetId) async {
    int attempts = 0;
    while (mounted && attempts < 25) {
      if (_messages.any((m) => m.id == targetId)) {
        // ListView ko ek frame build hone do taaki naye-load hue purane
        // items ka layout ready ho, warna scroll offset galat calculate hoga.
        await Future.delayed(const Duration(milliseconds: 150));
        if (mounted) _scrollToMessage(targetId, highlight: true);
        return;
      }
      if (!_hasMoreMessages) break;
      await _loadMoreMessages();
      attempts++;
    }
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(_l10n.chatScrollToMessageFailed)),
      );
    }
  }

  // 🔥 NAYA (Phase 3, §1 #11) — throttle scope respect karte hue smart-reply
  // suggestions fetch karo. Sirf tab call karo jab: (a) last message current
  // user ka na ho, (b) cooldown khatam ho chuka ho.
  Future<void> _maybeLoadSmartReplies() async {
    if (_messages.isEmpty) return;
    final last = _messages.last;
    if (last.sender?.id == _myUserId) {
      if (mounted && _smartReplies.isNotEmpty) setState(() => _smartReplies = []);
      return;
    }
    final now = DateTime.now();
    if (_lastSmartReplyFetch != null && now.difference(_lastSmartReplyFetch!) < _kSmartReplyCooldown) {
      return;
    }
    _lastSmartReplyFetch = now;
    try {
      final result = await MessageApiService.getSmartReplies(widget.conversation.id);
      if (mounted) setState(() => _smartReplies = result.suggestions);
    } catch (_) {
      // silent — chips bas nahi dikhenge
    }
  }

  // 🔥 NAYA (Phase 3, §7.10) — debounce (1.5s) karke server pe draft save
  // karta hai. Text khaali ho gaya (message send ho gaya ya user ne clear
  // kar diya) to bhi call hota hai taaki server-side draft bhi clear ho
  // jaaye — warna purana draft list-preview me atka reh jaayega.
  void _scheduleDraftSave(String text) {
    _draftSaveTimer?.cancel();
    _draftSaveTimer = Timer(const Duration(milliseconds: 1500), () {
      if (!mounted || text == _lastSavedDraft) return;
      _lastSavedDraft = text;
      MessageApiService.updateSettings(widget.conversation.id, draftText: text).catchError((_) {
        // silent — draft save fail hone se chat use karna nahi rukna chahiye
        return ConversationSettings();
      });
    });
  }

  // 🔧 FIX (backend mismatch) — pin/unpin ab message-level endpoint hai,
  // `conversationId` pass karne ki zaroorat nahi (§0 backend doc).
  Future<void> _pinMessage(MessageModel msg) async {
    try {
      final pin = await MessageApiService.pinMessage(msg.id);
      if (!mounted) return;
      setState(() => _pinnedMessages = [pin, ..._pinnedMessages]);
    } on MessageApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  Future<void> _unpinMessage(String messageId) async {
    final prev = _pinnedMessages;
    setState(() => _pinnedMessages = _pinnedMessages.where((p) => p.message.id != messageId).toList());
    try {
      await MessageApiService.unpinMessage(messageId);
    } catch (_) {
      if (mounted) setState(() => _pinnedMessages = prev); // rollback on failure
    }
  }

  Widget _buildPinnedBanner() {
    final latest = _pinnedMessages.first;
    return GestureDetector(
      onTap: _showPinnedMessagesSheet,
      child: Container(
        width: double.infinity,
        color: AppThemeTokens.of(context).warning.withOpacity(0.15),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        child: Row(children: [
          Icon(Icons.push_pin, size: 16, color: AppThemeTokens.of(context).warning),
          const SizedBox(width: 8),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(
                _l10n.chatPinnedBanner(_pinnedMessages.length),
                style: TextStyle(color: AppThemeTokens.of(context).warning, fontSize: 11, fontWeight: FontWeight.bold),
              ),
              Text(
                latest.message.text?.isNotEmpty == true ? latest.message.text! : _l10n.chatAttachment,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: Theme.of(context).colorScheme.onSurface, fontSize: 12.5),
              ),
            ]),
          ),
          Icon(Icons.chevron_right, size: 18, color: AppThemeTokens.of(context).warning),
        ]),
      ),
    );
  }

  void _showPinnedMessagesSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (sheetCtx) => StatefulBuilder(builder: (sheetCtx, setSheetState) {
        return SafeArea(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Padding(
              padding: EdgeInsets.all(14),
              child: Text(_l10n.chatPinnedMessagesTitle, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
            ),
            if (_pinnedMessages.isEmpty)
              Padding(padding: EdgeInsets.all(20), child: Text(_l10n.chatNoPinned)),
            ..._pinnedMessages.map((p) => ListTile(
                  leading: const Icon(Icons.push_pin_outlined),
                  title: Text(
                    p.message.text?.isNotEmpty == true ? p.message.text! : _l10n.chatAttachment,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  subtitle: Text(_l10n.chatPinnedBy(p.pinnedBy.displayName)),
                  trailing: IconButton(tooltip: 'Close', 
                    icon: const Icon(Icons.close, size: 18),
                    onPressed: () async {
                      await _unpinMessage(p.message.id);
                      setSheetState(() {});
                      if (_pinnedMessages.isEmpty && mounted) Navigator.pop(sheetCtx);
                    },
                  ),
                  onTap: () {
                    Navigator.pop(sheetCtx);
                    _scrollToMessage(p.message.id);
                  },
                )),
          ]),
        );
      }),
    );
  }

  Future<void> _loadWallpaper() async {
    final url = await MessageApiService.getConversationWallpaper(widget.conversation.id);
    if (!mounted) return;
    setState(() => _wallpaperUrl = url);
  }

  Future<void> _pickChatWallpaper() async {
    // 🔥 NAYA — pehle se ek upload chal raha ho to dobara tap ignore karo
    // (warna do parallel upload+set requests race kar sakti hain).
    if (_wallpaperUploading) return;
    try {
      final picked = await ImagePicker().pickImage(source: ImageSource.gallery, imageQuality: 85);
      if (picked == null || !mounted) return;

      setState(() => _wallpaperUploading = true);

      // Step 1: file upload -> file_url milta hai.
      final uploaded = await MessageApiService.uploadFile(File(picked.path));
      // Step 2: wahi file_url is poori chat ke wallpaper ke roop me set karo.
      final saved = await MessageApiService.setConversationWallpaper(
        widget.conversation.id,
        uploaded.fileUrl,
      );

      if (!mounted) return;
      setState(() {
        _wallpaperUrl = saved ?? uploaded.fileUrl;
        _wallpaperUploading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _wallpaperUploading = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(_l10n.chatWallpaperSetFailed(e.toString()))),
      );
    }
  }

  Future<void> _removeChatWallpaper() async {
    // 🔥 NAYA — accidental tap se turant wallpaper na hat jaaye, ek chhota
    // confirm dialog dikha dete hain.
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: Text(_l10n.chatWallpaperRemoveTitle),
        content: Text(_l10n.chatWallpaperRemoveBody),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: Text(_l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(context, true), child: Text(_l10n.chatRemove)),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    // Optimistic update — turant hata do, fail hone par wapas laga denge.
    final previousUrl = _wallpaperUrl;
    setState(() => _wallpaperUrl = null);
    try {
      await MessageApiService.removeConversationWallpaper(widget.conversation.id);
    } catch (e) {
      if (!mounted) return;
      setState(() => _wallpaperUrl = previousUrl);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(_l10n.chatWallpaperRemoveFailed(e.toString()))),
      );
    }
  }

  // 🔥 NAYA — 3-dot menu se "Chat wallpaper" option: change ya remove
  // (agar already set hai) dikhane wala chhota bottom sheet.
  void _showWallpaperSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (_) => SafeArea(
        child: Wrap(children: [
          ListTile(
            leading: const Icon(Icons.wallpaper),
            title: Text(_wallpaperUrl != null ? _l10n.chatChangeWallpaper : _l10n.chatSetWallpaper),
            enabled: !_wallpaperUploading,
            onTap: () {
              Navigator.pop(context);
              _pickChatWallpaper();
            },
          ),
          if (_wallpaperUrl != null)
            ListTile(
              leading: const Icon(Icons.image_not_supported_outlined),
              title: Text(_l10n.chatRemoveWallpaper),
              onTap: () {
                Navigator.pop(context);
                _removeChatWallpaper();
              },
            ),
        ]),
      ),
    );
  }

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
      case 'text': return _l10n.chatFilterText;
      case 'media': return _l10n.chatFilterMedia;
      case 'docs': return _l10n.chatFilterDocs;
      case 'links': return _l10n.chatFilterLinks;
      default: return _l10n.filterAll;
    }
  }

  // 🔥 NAYA — 3-dot menu ke "Filter messages" tap hone par ye bottom sheet
  // khulti hai jisme 5 options hote hain: All, Text, Media, Docs, Links.
  void _showFilterSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (_) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Padding(
            padding: EdgeInsets.fromLTRB(16, 14, 16, 4),
            child: Align(alignment: Alignment.centerLeft, child: Text(_l10n.chatFilterTitle, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16))),
          ),
          for (final entry in [
            {'value': 'all', 'label': _l10n.chatFilterAllMessages, 'icon': Icons.forum_outlined},
            {'value': 'text', 'label': _l10n.chatFilterText, 'icon': Icons.short_text},
            {'value': 'media', 'label': _l10n.chatFilterImageVideo, 'icon': Icons.perm_media_outlined},
            {'value': 'docs', 'label': _l10n.chatFilterDocsFiles, 'icon': Icons.insert_drive_file_outlined},
            {'value': 'links', 'label': _l10n.chatFilterUrlLinks, 'icon': Icons.link},
          ])
            ListTile(
              leading: Icon(entry['icon'] as IconData, color: _chatFilter == entry['value'] ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurface),
              title: Text(entry['label'] as String, style: TextStyle(color: _chatFilter == entry['value'] ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurface, fontWeight: _chatFilter == entry['value'] ? FontWeight.bold : FontWeight.normal)),
              trailing: _chatFilter == entry['value'] ? Icon(Icons.check, color: Theme.of(context).colorScheme.primary) : null,
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
    // 🔥 NAYA (Phase 3, §7.10) — screen open hote hi agar server pe koi
    // saved draft hai to compose box usi se prefill ho jaaye.
    final savedDraft = widget.conversation.mySettings.draftText;
    if (savedDraft != null && savedDraft.trim().isNotEmpty) {
      _textController.text = savedDraft;
      _lastSavedDraft = savedDraft;
    }
    _scrollController.addListener(_onScroll); // 🔥 NAYA — top tak scroll hone par purane messages load karne ke liye
    _init();
    _loadMuteStatus(); // 🔥 NAYA
    TranslateService.instance.loadTranslatePermission(); // 🔥 NAYA — Task 6: translate on/off switch ki saved value load karo
    _loadBlockStatus(); // 🔥 NAYA
    _isPendingRequest = widget.isMessageRequest; // 🔥 NAYA (M1-FE)
    _loadRequestStatus(); // 🔥 NAYA (M1-FE)
    _loadDisappearingStatus(); // 🔥 NAYA
    _loadWallpaper(); // 🔥 NAYA — poori chat screen ka background image (agar set hai)
    _loadPinnedMessages(); // 🔥 NAYA — pinned messages banner
    _loadGroupMembers(); // 🔥 NAYA (Phase 3, §2.2) — @mention suggestion list ke liye
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
    // 🔥 NAYA (Phase 4, §2.1) — search se aaye ho to us message tak jump karo.
    if (widget.jumpToMessageId != null) _tryJumpToInitialMessage();
    // 🔥 NAYA (Phase 3, §1 #11) — history load hote hi, agar last message
    // dusre ka hai, smart-reply chips fetch kar lo.
    _maybeLoadSmartReplies();
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

      // 🔥 NAYA — access-control fields (message_permission /
      // daily_message_limit) — model field names ke hi hisaab se, defensive
      // fallback ke saath (agar backend response me na ho to defaults).
      final permission = _normalizeGroupPermission(data['message_permission']);
      final rawLimit = data['daily_message_limit'];
      final limit = rawLimit is int ? rawLimit : int.tryParse(rawLimit?.toString() ?? '');

      String? myRole;
      final membersRaw = data['members'] ?? data['group_members'] ?? [];
      if (membersRaw is List) {
        for (final m in membersRaw) {
          if (m is Map) {
            final userField = m['user'];
            final memberUserId = (userField is Map ? userField['id'] : (m['user_id'] ?? m['id']))?.toString();
            if (memberUserId == _myUserId) {
              myRole = m['role']?.toString();
              break;
            }
          }
        }
      }

      if (mounted) {
        setState(() {
          _isGroupAdmin = myRole == 'admin';
          _isGroupModerator = myRole == 'moderator';
          _groupMessagePermission = permission;
          _groupDailyLimit = limit;
        });
      }

      // 🔥 NAYA — admin/moderator ho aur group private ho, to pending
      // "join requests" ka badge count bhi load karo (approve/reject UI
      // ke liye).
      if ((_isGroupAdmin || _isGroupModerator) && (data['is_private'] == true)) {
        _loadPendingJoinRequestsCount();
      }
    } catch (_) {}
  }

  // 🔥 NAYA — sirf count chahiye (badge ke liye), list `_showJoinRequestsSheet`
  // khulne pe fresh fetch hoti hai.
  Future<void> _loadPendingJoinRequestsCount() async {
    final groupId = widget.conversation.group?.id;
    if (groupId == null) return;
    try {
      final list = await MessageApiService.getJoinRequests(groupId);
      if (mounted) setState(() => _pendingJoinRequestsCount = list.length);
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
              Text(newValue ? _l10n.chatNotificationsMuted : _l10n.chatNotificationsUnmuted),
        ));
      }
    } catch (e) {
      if (mounted) setState(() => _isMuted = !newValue); // revert
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(_l10n.chatUpdateFailed(e.toString()))));
      }
    }
  }

  // 🔥 NAYA — Task 6: "Translate" ab per-message inline button nahi,
  // 3-dot menu me ek on/off permission hai. Ye per-device preference
  // hai (SharedPreferences, `TranslateService.instance` — same jagah
  // `getPreferredTranslateLang()` apni value store karta hai), poori
  // app me sab chat ke liye ek hi switch — jaisa hi flip hota hai,
  // `TranslateToggle` (jo iske `translateEnabled` notifier ko sun raha
  // hai) turant hide/show ho jaata hai, kisi screen-rebuild ki zaroorat
  // nahi.
  Future<void> _toggleTranslatePermission() async {
    final newValue = !TranslateService.instance.translateEnabled.value;
    await TranslateService.instance.setTranslateEnabled(newValue);
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(newValue ? _l10n.chatTranslateEnabled : _l10n.chatTranslateDisabled),
      ));
    }
  }

  // 🔥 NAYA — Task 7.1: Listen aur Transcribe bhi Translate jaise hi
  // 3-dot menu toggles hain (default OFF, per-device persist).
  Future<void> _toggleListenPermission() async {
    final newValue = !TranslateService.instance.listenEnabled.value;
    await TranslateService.instance.setListenEnabled(newValue);
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(newValue ? _l10n.chatListenEnabled : _l10n.chatListenDisabled),
      ));
    }
  }

  Future<void> _toggleTranscribePermission() async {
    final newValue = !TranslateService.instance.transcribeEnabled.value;
    await TranslateService.instance.setTranscribeEnabled(newValue);
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(newValue ? _l10n.chatTranscribeEnabled : _l10n.chatTranscribeDisabled),
      ));
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
      final otherName = widget.conversation.otherParticipant?.displayName ?? _l10n.chatThisUser;
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          title: Text(_l10n.chatBlockTitle),
          content: Text(
              _l10n.chatBlockBody(otherName)),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(_l10n.cancel)),
            TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(_l10n.chatBlock, style: TextStyle(color: Colors.red)),
            ),
          ],
        ),
      );
      if (confirmed != true) return;

      try {
        await MessageApiService.blockUser(otherId);
        if (!mounted) return;
        setState(() => _isBlocked = true);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatUserBlocked)));
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatBlockFailed(e.toString()))));
        }
      }
    } else {
      try {
        await MessageApiService.unblockUser(otherId);
        if (!mounted) return;
        setState(() => _isBlocked = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatUserUnblocked)));
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatUnblockFailed(e.toString()))));
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
      case '1_month': return _l10n.chatDisappear1Month;
      case '6_months': return _l10n.chatDisappear6Months;
      case '1_year': return _l10n.chatDisappear1Year;
      case 'after_seen': return 'After seen'; // TODO(l10n): arb me `chatDisappearAfterSeen`
      case 'none':
      default: return _l10n.chatDisappearingOff;
    }
  }

  // 🔥 NAYA — optimistic update: pehle UI turant naya duration dikhata hai,
  // phir backend call; fail ho jaaye (e.g. group me non-admin) to purani
  // value pe wapas revert kar do aur error dikhao.
  Future<bool> _setDisappearingDuration(String duration) async {
    final previous = _disappearingDuration;
    if (duration == previous) return true;
    setState(() => _disappearingDuration = duration);
    try {
      await MessageApiService.setDisappearingMessages(widget.conversation.id, duration);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(duration == 'none'
              ? _l10n.chatDisappearingTurnedOff
              : _l10n.chatDisappearingNewMessages(_disappearingLabel(duration))),
        ));
      }
      return true;
    } catch (e) {
      if (mounted) setState(() => _disappearingDuration = previous); // revert
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(_l10n.chatUpdateFailed(e.toString()))));
      }
      return false;
    }
  }

  // 🔥 NAYA (M3a-FE) — swipe-up par vanish mode on/off. Existing
  // `_setDisappearingDuration` (optimistic + revert on fail, group me
  // non-admin ko 403) hi reuse hota hai; flag sirf API success par badalta hai.
  Future<void> _toggleVanishMode() async {
    if (_vanishBusy) return;
    _vanishBusy = true;
    try {
      if (!_vanishMode) {
        final prev = _disappearingDuration;
        if (prev != _kVanishDuration) {
          final ok = await _setDisappearingDuration(_kVanishDuration);
          if (!ok || !mounted) return;
          _vanishPrevDuration = prev;
        } else {
          _vanishPrevDuration = null; // pehle se sabse chhota duration tha
        }
        HapticFeedback.mediumImpact();
        if (mounted) setState(() => _vanishMode = true);
      } else {
        final restore = _vanishPrevDuration;
        if (restore != null && _disappearingDuration == _kVanishDuration) {
          final ok = await _setDisappearingDuration(restore);
          if (!ok || !mounted) return;
        }
        HapticFeedback.lightImpact();
        if (mounted) setState(() { _vanishMode = false; _vanishPrevDuration = null; });
      }
    } finally {
      _vanishBusy = false;
    }
  }

  Widget _buildVanishBanner() {
    final cs = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      color: cs.inverseSurface,
      child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
        Icon(Icons.visibility_off_outlined, size: 16, color: cs.onInverseSurface),
        const SizedBox(width: 8),
        // TODO(l10n): arb me `chatVanishModeOn` / `chatVanishModeHint` add karke _l10n se replace karo
        Text('Vanish mode on', style: TextStyle(color: cs.onInverseSurface, fontWeight: FontWeight.w600, fontSize: 13)),
        const SizedBox(width: 8),
        Text('· swipe up to turn off', style: TextStyle(color: cs.onInverseSurface.withOpacity(0.7), fontSize: 12)),
      ]),
    );
  }

  // 🔥 NAYA — 3-dot menu ke "Disappearing messages" tap hone par ye bottom
  // sheet khulti hai — WhatsApp jaisa hi 4 options: Off, 1 Month, 6 Months, 1 Year.
  void _showDisappearingMessagesSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (_) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Padding(
            padding: EdgeInsets.fromLTRB(16, 14, 16, 4),
            child: Align(alignment: Alignment.centerLeft, child: Text(_l10n.chatDisappearingTitle, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16))),
          ),
          Padding(
            padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text(
                _l10n.chatDisappearingSubtitle,
                style: TextStyle(fontSize: 12.5, color: Theme.of(context).colorScheme.onSurfaceVariant),
              ),
            ),
          ),
          for (final entry in [
            {'value': 'none', 'label': _l10n.chatDisappearingOff},
            {'value': '1_month', 'label': _l10n.chatDisappear1Month},
            {'value': '6_months', 'label': _l10n.chatDisappear6Months},
            {'value': '1_year', 'label': _l10n.chatDisappear1Year},
            // 🔥 M3b — sirf private chat (group me BE 'after_seen' reject karta hai)
            if (!widget.conversation.isGroup) {'value': 'after_seen', 'label': 'After seen'}, // TODO(l10n)
          ])
            ListTile(
              leading: Icon(
                entry['value'] == 'none' ? Icons.timer_off_outlined : Icons.timer_outlined,
                color: _disappearingDuration == entry['value'] ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurface,
              ),
              title: Text(
                entry['label']!,
                style: TextStyle(
                  color: _disappearingDuration == entry['value'] ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurface,
                  fontWeight: _disappearingDuration == entry['value'] ? FontWeight.bold : FontWeight.normal,
                ),
              ),
              trailing: _disappearingDuration == entry['value'] ? Icon(Icons.check, color: Theme.of(context).colorScheme.primary) : null,
              onTap: () {
                Navigator.pop(context);
                _setDisappearingDuration(entry['value']!);
              },
            ),
        ]),
      ),
    );
  }

  // ================================================================
  // 🔥 NAYA — FEATURE 1: ACCESS CONTROL — "Kaun message bhej sakta hai"
  // (Everyone / sirf Admins & Moderators) aur "Daily message limit"
  // (normal members ke liye, e.g. 4/din) — dono admin/moderator hi
  // set kar sakte hain (`IsGroupAdminOrModerator` — views.py). Backend
  // hi asal me enforce karta hai (`group_rules.check_group_send_
  // permission`), ye sheet sirf un dono flags ko `Group.message_
  // permission` / `Group.daily_message_limit` pe likhti hai.
  // ================================================================
  void _showAccessControlSheet() {
    String selectedPermission = _groupMessagePermission;
    final limitController = TextEditingController(
      text: _groupDailyLimit == null ? '' : _groupDailyLimit.toString(),
    );
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (sheetCtx) => StatefulBuilder(
        builder: (sheetCtx, setSheetState) => Padding(
          padding: EdgeInsets.only(bottom: MediaQuery.of(sheetCtx).viewInsets.bottom),
          child: SafeArea(
            child: SingleChildScrollView(
              child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
                Padding(
                  padding: EdgeInsets.fromLTRB(16, 14, 16, 4),
                  child: Text(_l10n.chatPermissionsTitle, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                ),
                Padding(
                  padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
                  child: Text(
                    _l10n.chatPermissionsSubtitle,
                    style: TextStyle(fontSize: 12.5, color: Theme.of(sheetCtx).colorScheme.onSurfaceVariant),
                  ),
                ),
                RadioListTile<String>(
                  value: 'everyone',
                  groupValue: selectedPermission,
                  activeColor: Theme.of(sheetCtx).colorScheme.primary,
                  title: Text(_l10n.chatPermEveryone),
                  subtitle: Text(_l10n.chatPermEveryoneSub, style: TextStyle(fontSize: 12)),
                  onChanged: (v) => setSheetState(() => selectedPermission = v!),
                ),
                RadioListTile<String>(
                  value: 'admins_mods',
                  groupValue: selectedPermission,
                  activeColor: Theme.of(sheetCtx).colorScheme.primary,
                  title: Text(_l10n.chatPermAdminsOnly),
                  subtitle: Text(_l10n.chatPermAdminsOnlySub, style: TextStyle(fontSize: 12)),
                  onChanged: (v) => setSheetState(() => selectedPermission = v!),
                ),
                const Divider(height: 24),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Text(_l10n.chatDailyLimitTitle, style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5, color: Theme.of(context).colorScheme.onSurface)),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 4),
                  child: Text(
                    _l10n.chatDailyLimitHelp,
                    style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: TextField(
                    controller: limitController,
                    keyboardType: TextInputType.number,
                    decoration: InputDecoration(
                      hintText: _l10n.chatDailyLimitHint,
                      isDense: true,
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
                      suffixText: _l10n.chatDailyLimitSuffix,
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                  child: SizedBox(
                    width: double.infinity,
                    child: ElevatedButton(
                      style: ElevatedButton.styleFrom(backgroundColor: Theme.of(context).colorScheme.primary, foregroundColor: Theme.of(context).colorScheme.onPrimary, padding: const EdgeInsets.symmetric(vertical: 13)),
                      onPressed: () {
                        final raw = limitController.text.trim();
                        final newLimit = raw.isEmpty ? null : int.tryParse(raw);
                        Navigator.pop(sheetCtx);
                        _saveAccessControl(selectedPermission, newLimit);
                      },
                      child: Text(_l10n.save),
                    ),
                  ),
                ),
              ]),
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _saveAccessControl(String permission, int? dailyLimit) async {
    final groupId = widget.conversation.group?.id;
    if (groupId == null) return;
    final prevPermission = _groupMessagePermission;
    final prevLimit = _groupDailyLimit;
    setState(() {
      _groupMessagePermission = permission;
      _groupDailyLimit = dailyLimit;
    });
    try {
      await MessageApiService.updateGroup(groupId, {
        'message_permission': permission,
        'daily_message_limit': dailyLimit,
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(
          permission == 'admins_mods'
              ? _l10n.chatPermUpdatedAdminsOnly
              : _l10n.chatPermUpdatedEveryone,
        )));
      }
    } catch (e) {
      if (mounted) {
        setState(() { _groupMessagePermission = prevPermission; _groupDailyLimit = prevLimit; });
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatUpdateFailedDetail(e.toString()))));
      }
    }
  }

  // ================================================================
  // 🔥 NAYA — FEATURE 2: CHANGE GROUP PROFILE PHOTO seedha chat screen
  // se — pehle sirf `group_profile_screen.dart` se hota tha. Admin/
  // moderator dono allowed hain (backend `IsGroupAdminOrModerator`).
  // ================================================================
  Future<void> _changeGroupPhoto() async {
    final groupId = widget.conversation.group?.id;
    if (groupId == null) return;
    final picked = await ImagePicker().pickImage(source: ImageSource.gallery, imageQuality: 85);
    if (picked == null) return;
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatUploadingPhoto)));
    try {
      final uploaded = await MessageApiService.uploadFile(File(picked.path));
      await MessageApiService.updateGroup(groupId, {'photo_url': uploaded.fileUrl});
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatGroupPhotoUpdated)));
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatPhotoUpdateFailed(e.toString()))));
    }
  }

  // ================================================================
  // 🔥 NAYA — FEATURE 3: JOIN REQUESTS — private group me naye members
  // pehle `GroupJoinRequest` (PENDING) banate hain, admin/moderator yahan
  // se hi approve/reject kar sakte hain (`GroupMember` turant ban jaata
  // hai approve pe).
  // ================================================================
  void _showJoinRequestsSheet() {
    final groupId = widget.conversation.group?.id;
    if (groupId == null) return;

    // 🔥 FIX — ye state `StatefulBuilder`'s OUTER method scope me honi
    // chahiye, uske andar wale `builder:` callback me NAHI — wo callback
    // har `setSheetState()` call pe dobara chalta hai, aur agar
    // `requests`/`error` uske andar declare kiye jaate to har rebuild pe
    // wapas `null` ho jaate (fresh local variables), jisse `load()`
    // baar-baar (infinite loop) call hota aur list kabhi dikhti hi nahi —
    // hamesha loading spinner pe atki rehti.
    List<dynamic>? requests;
    String? error;

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (sheetCtx) => StatefulBuilder(
        builder: (sheetCtx, setSheetState) {
          Future<void> load() async {
            try {
              final list = await MessageApiService.getJoinRequests(groupId);
              if (sheetCtx.mounted) setSheetState(() { requests = list; error = null; });
            } catch (e) {
              if (sheetCtx.mounted) setSheetState(() => error = e.toString());
            }
          }
          if (requests == null && error == null) load();

          return DraggableScrollableSheet(
            initialChildSize: 0.55,
            minChildSize: 0.3,
            maxChildSize: 0.85,
            expand: false,
            builder: (_, scrollCtl) => SafeArea(
              child: Column(children: [
                Padding(
                  padding: EdgeInsets.fromLTRB(16, 14, 16, 8),
                  child: Align(alignment: Alignment.centerLeft, child: Text(_l10n.chatJoinRequestsTitle, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16))),
                ),
                if (requests == null && error == null)
                  const Expanded(child: Center(child: CircularProgressIndicator()))
                else if (error != null)
                  Expanded(child: Center(child: Text(_l10n.chatLoadFailed(error.toString()))))
                else if (requests!.isEmpty)
                  Expanded(child: Center(child: Text(_l10n.chatNoPendingRequests, style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant))))
                else
                  Expanded(
                    child: ListView.builder(
                      controller: scrollCtl,
                      itemCount: requests!.length,
                      itemBuilder: (_, i) {
                        final r = requests![i];
                        final user = r is Map ? (r['user'] ?? r) : {};
                        final requestId = (r is Map ? (r['id'] ?? r['request_id']) : null)?.toString() ?? '';
                        final username = (user is Map ? (user['username'] ?? user['name']) : null)?.toString() ?? _l10n.chatUnknown;
                        final photoUrl = (user is Map ? user['profile_pic'] : null)?.toString();
                        return ListTile(
                          leading: CircleAvatar(
                            backgroundImage: (photoUrl != null && photoUrl.isNotEmpty) ? CachedNetworkImageProvider(photoUrl) : null,
                            child: (photoUrl == null || photoUrl.isEmpty) ? Text(username.isNotEmpty ? username[0].toUpperCase() : '?') : null,
                          ),
                          title: Text(username),
                          trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                            IconButton(
                              icon: const Icon(Icons.check_circle, color: Colors.green),
                              tooltip: _l10n.chatApprove,
                              onPressed: () async {
                                try {
                                  await MessageApiService.approveJoinRequest(groupId, requestId);
                                  setSheetState(() => requests!.removeAt(i));
                                  if (mounted) setState(() => _pendingJoinRequestsCount = requests!.length);
                                } catch (e) {
                                  if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatApproveFailed(e.toString()))));
                                }
                              },
                            ),
                            IconButton(
                              icon: const Icon(Icons.cancel, color: Colors.red),
                              tooltip: _l10n.chatReject,
                              onPressed: () async {
                                try {
                                  await MessageApiService.rejectJoinRequest(groupId, requestId);
                                  setSheetState(() => requests!.removeAt(i));
                                  if (mounted) setState(() => _pendingJoinRequestsCount = requests!.length);
                                } catch (e) {
                                  if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatRejectFailed(e.toString()))));
                                }
                              },
                            ),
                          ]),
                        );
                      },
                    ),
                  ),
              ]),
            ),
          );
        },
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
    } else if (mounted) {
      _loadGroupRole(); // M9a-FE — profile me permission/role badla ho to composer sync
    }
  }

  Future<void> _leaveGroup() async {
    final groupId = widget.conversation.group?.id;
    if (groupId == null || groupId.isEmpty || _myUserId == null) return;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text(_l10n.chatLeaveGroupTitle),
        content: Text(
            _l10n.chatLeaveGroupBody(widget.conversation.displayTitle)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(_l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(_l10n.chatLeave, style: TextStyle(color: Colors.red)),
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
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatLeaveFailed(e.toString()))));
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
        title: Text(_l10n.chatDeleteGroupTitle),
        content: Text(
            _l10n.chatDeleteGroupBody(widget.conversation.displayTitle)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(_l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(_l10n.delete, style: TextStyle(color: Colors.red)),
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
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatDeleteFailed(e.toString()))));
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
          // M6-FE — history load ke dauran bheje gaye (abhi server list me nahi) bubbles overwrite na hon.
          final serverClientIds = data.map((m) => m.clientId).whereType<String>().toSet();
          final unsent = _messages.where((m) => (m.isSending || m.sendFailed) && m.clientId != null && !serverClientIds.contains(m.clientId)).toList();
          _messages = data.reversed.toList()..addAll(unsent);
          _isLoading = false;
          _currentPage = 1;
          // Agar backend se ek page se kam messages aaye, matlab aur purane
          // messages hain hi nahi — "load more" trigger karne ki zaroorat nahi.
          _hasMoreMessages = data.length >= _kPageSize;
        });
        _scrollToBottom();
        _scanAlreadyDownloaded(); // 🔥 NAYA — WhatsApp jaisa: purane downloaded files pe "Open" dikhao
        _restorePending(data); // M6-FE — outbox ke unsent messages wapas + auto-retry
      }
      MessageCacheService.saveMessages(widget.conversation.id, data); // fire-and-forget, 1 week tak valid
    } catch (e) {
      if (mounted) {
        setState(() => _isLoading = false);
        // Cache se messages already dikh rahe hon to error se use mat dabao.
        if (cached.isEmpty) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(_l10n.chatLoadMessagesFailed(e.toString()))),
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
    } on MessageApiException catch (e) {
      // 🔥 FIX — DRF `PageNumberPagination` out-of-range page pe hamesha 404
      // deta hai. Ye asal error nahi hai, matlab bas "aur purane messages
      // hain hi nahi" (isse pehle exactly `_kPageSize` messages waale page
      // ke baad `_hasMoreMessages` galat `true` reh jaata tha aur wahi
      // failing request baar-baar retry hoti thi — yahi "purani stickers
      // load nahi hote" wale case ka asli bug tha).
      if (mounted) {
        setState(() {
          _isLoadingMore = false;
          if (e.statusCode == 404) {
            _hasMoreMessages = false;
          }
        });
        if (e.statusCode != 404) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(_l10n.chatLoadOlderFailed(e.toString()))),
          );
        }
      }
    } catch (e) {
      if (mounted) {
        setState(() => _isLoadingMore = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(_l10n.chatLoadOlderFailed(e.toString()))),
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
      // 🔧 FIX (backend mismatch) — backend `poll_created`/`poll_voted`
      // naam se KUCH nahi bhejta. Naya poll ek normal `chat_message` event
      // se aata hai (poll field nested hoti hai — `_onIncomingMessage` /
      // `MessageModel.fromSocketEvent` isko already handle karta hai, agar
      // model me `poll` field parse ho rahi hai to alag se kuch nahi
      // karna). Vote/close dono ek hi event se aate hain: `poll_update`.
      case 'poll_update':
        _onPollUpdateEvent(event);
        break;
      // 🔥 NAYA (Phase 2, §7.7) — link preview aur voice transcript dono
      // isi event se live aate hain (backend background job se generate
      // hoke baad me attach hote hain — message pehle bina inke insert
      // hota hai). Payload: `{message_id, meta: {...}}` — us message ka
      // `.meta` poora replace karo (poori list reload nahi), `linkPreview`/
      // `transcript` getters (message_models.dart) khud-ba-khud naye
      // `meta` se re-derive ho jaate hain.
      case 'meta_update':
        _onMetaUpdateEvent(event);
        break;
      // 🔧 FIX (backend mismatch) — backend `message_pinned`/
      // `message_unpinned` naam se nahi, single `pin_event` bhejta hai:
      // `{event: "pinned"|"unpinned", message_id, conversation_id, actor_id}`.
      // Poori list refresh karna hi simplest/consistent tarika hai (list
      // chhoti hoti hai, max 3).
      case 'pin_event':
        _loadPinnedMessages();
        break;
      // 🔥 NAYA — khud apne doosre connected device se chat wallpaper
      // set/remove hone par yahan turant sync ho jaaye.
      case 'conversation_wallpaper_updated':
        _onWallpaperEvent(event);
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
          setState(() {
            _disappearingDuration = duration;
            if (duration != _kVanishDuration) { _vanishMode = false; _vanishPrevDuration = null; }
          });
          if (event['updated_by']?.toString() != _myUserId) {
            ScaffoldMessenger.of(context).showSnackBar(SnackBar(
              content: Text(duration == 'none'
                  ? _l10n.chatDisappearingTurnedOff
                  : _l10n.chatDisappearingSetTo(_disappearingLabel(duration))),
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
            SnackBar(content: Text(_l10n.chatGroupDeletedByAdmin)),
          );
          Navigator.of(context).pop(true);
        }
        break;
      // Block / unblock between the two people of this chat (server sends
      // the same event to both and never says who did it). Re-check
      // my block state so the menu / banner follow immediately.
      case 'block_changed':
        _loadBlockStatus();
        break;
      // 🔥 CALL EVENTS — backend se call_event type me aate hain
      case 'call_event':
      case 'incoming_call':
        _handleCallEvent(event);
        break;
      case 'error':
        // M9a-FE — socket se bheja message admin-only ki wajah se reject hua.
        if (event['code']?.toString() == 'admins_only') {
          _onAdminOnlyBlocked(event['message']?.toString());
          break;
        }
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(event['message']?.toString() ?? _l10n.chatGenericError)),
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
    final callerName = (event['caller_name'] ?? _l10n.chatSomeone).toString();
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
      showDialog(context: context, barrierDismissible: false, builder: (_) => const Center(child: CircularProgressIndicator()));
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
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatCallFailed(e.toString()))));
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
          // 🔧 FIX — pehle yahan hamesha `const []` jaata tha (TODO tha:
          // "widget.conversation se actual participants map karke banao,
          // agar group participants list available ho"). `ConversationModel`
          // khud group members list carry nahi karta, lekin `_groupMembers`
          // (mention-autocomplete ke liye already `_loadGroupMembers()` se
          // load/cached `List<UserMini>`) exactly wahi data hai — usi se
          // banaya, koi extra API call nahi chahiye. Windows ko ek simple
          // horizontal cascade me place kar diya taaki overlap na ho.
          initialParticipants: widget.conversation.isGroup
              ? List.generate(_groupMembers.length, (i) {
                  final m = _groupMembers[i];
                  return UserProfileWindowModel(
                    userId: m.id,
                    displayName: m.displayName,
                    avatarUrl: m.profilePhoto,
                    position: Offset(24.0 + (i % 4) * 90, 24.0 + (i ~/ 4) * 90),
                    size: const Size(80, 80),
                    zIndex: i,
                  );
                })
              : const [],
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
      sender: UserMini(id: _myUserId ?? '', displayName: _l10n.chatYou),
      type: MessageType.studyRoom,
      text: _l10n.chatStudyRoom,
      clientId: clientId,
      createdAt: DateTime.now(),
      isSending: true,
    );
    setState(() => _messages.add(optimistic));
    _scrollToBottom();
    _dispatch(
      PendingMessage(clientId: clientId, conversationId: widget.conversation.id, type: MessageType.studyRoom, text: _l10n.chatStudyRoom, createdAt: optimistic.createdAt),
      viaSocket: _isSocketConnected,
    );
  }

  // ============================================================
  // MESSAGE HANDLERS
  // ============================================================
  // M9a-FE — admin ne `message_permission` badli: backend system message
  // (`meta.system_event == 'message_permission_changed'`) group_send karta hai
  // jisme `message_permission` ('admins_only' | 'everyone') bhi hota hai.
  // Isse member ka input bar / banner bina reload ke turant badal jaata hai.
  void _applyPermissionFromEvent(Map<String, dynamic> event) {
    if (!widget.conversation.isGroup) return;
    final convId = event['conversation_id']?.toString();
    if (convId != null && convId != widget.conversation.id.toString()) return;
    final meta = event['meta'];
    if (meta is! Map || meta['system_event'] != 'message_permission_changed') return;
    final raw = event['message_permission'] ?? meta['message_permission'];
    if (raw == null || !mounted) return;
    final next = _normalizeGroupPermission(raw);
    if (next == _groupMessagePermission) return;
    setState(() => _groupMessagePermission = next);
    // Composer lock ho gaya (member ke liye) — keyboard band karo.
    if (_isMessagingRestrictedForMe) FocusManager.instance.primaryFocus?.unfocus();
  }

  // M9a-FE — send 403/`admins_only` (REST) ya socket error `admins_only`:
  // local setting stale thi. Banner dikhao, in-flight message hatao (outbox se
  // bhi, warna flush/reopen pe baar-baar retry hoga), aur role/setting
  // server se refresh karo (agar admin/mod ko ye aaya to role stale tha).
  void _onAdminOnlyBlocked(String? reason, {String? clientId}) {
    if (!mounted || !widget.conversation.isGroup) return;
    final removedIds = <String>[];
    setState(() {
      if (!_isGroupAdminOrMod) _groupMessagePermission = 'admins_mods';
      _messages.removeWhere((m) {
        final inFlight = m.isSending && !m.sendFailed;
        final match = clientId != null
            ? m.clientId == clientId
            : (inFlight && m.sender?.id == _myUserId);
        if (match && m.clientId != null) removedIds.add(m.clientId!);
        return match;
      });
    });
    for (final id in removedIds) {
      _sendWatchdogs.remove(id)?.cancel();
      MessageCacheService.removePending(id);
    }
    FocusManager.instance.primaryFocus?.unfocus();
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text((reason != null && reason.isNotEmpty) ? reason : _l10n.chatAdminsOnlyBanner),
    ));
    _loadGroupRole();
  }

  void _onIncomingMessage(Map<String, dynamic> event) {
    _applyPermissionFromEvent(event); // M9a-FE
    final incoming = MessageModel.fromSocketEvent(event);
    // M6-FE — apna bheja message server se echo ho gaya: watchdog band, outbox se hatao.
    final inCid = incoming.clientId;
    if (inCid != null && inCid.isNotEmpty) {
      _sendWatchdogs.remove(inCid)?.cancel();
      if (_messages.any((m) => m.clientId == inCid && (m.isSending || m.sendFailed))) {
        MessageCacheService.removePending(inCid);
      }
    }
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
      // 🔥 NAYA (Phase 3, §1 #11) — naya incoming message dusre ka hai,
      // smart-reply chips refresh karo (cooldown internally respect hota hai).
      _maybeLoadSmartReplies();
    } else if (_smartReplies.isNotEmpty) {
      // Maine khud reply bhej diya (kisi aur device se ho sakta hai) — chips hata do.
      setState(() => _smartReplies = []);
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
      final reactor = isMe ? UserMini(id: userId, displayName: _l10n.chatYou) : (existingIdx != -1 ? reactions[existingIdx].user : UserMini(id: userId, displayName: ''));
      final updated = MessageReactionModel(id: existingIdx != -1 ? reactions[existingIdx].id : '$messageId-$userId', user: reactor, emoji: emoji, createdAt: DateTime.now());
      if (existingIdx != -1) reactions[existingIdx] = updated; else reactions.add(updated);
    });
  }

  // 🔥 NAYA — POLLS
  // ============================================================

  // 🔧 FIX (backend mismatch) — `poll_created` naam ka koi event backend
  // nahi bhejta (isliye purana `_onPollCreatedEvent` yahan se hata diya).
  // Naya poll ek normal `chat_message` event se hi aata hai — agar
  // `MessageModel.fromSocketEvent` (message_models.dart) `event['poll']`
  // ko parse karke `MessageModel.poll`/`meta['poll']` set karta hai to
  // `_onIncomingMessage()` already sahi se handle kar raha hai, kuch alag
  // se karne ki zaroorat nahi. (Ye confirm kar lena model file me.)

  // 🔧 FIX (backend mismatch) — `poll_voted` nahi, backend `poll_update`
  // bhejta hai (vote AUR close dono ke liye same event), payload
  // `{message_id, poll: {...full updated Poll...}, voted_by | closed_by}`.
  // Standalone `GET /polls/<id>/` endpoint exist hi nahi karta (purana
  // `getPoll()` call isiliye hata diya gaya — §0 backend doc), poori
  // updated poll object seedha isi event ke payload me mil jaati hai,
  // extra REST call ki zaroorat nahi.
  //
  // 🔧 FIX (Phase 1 model fix) — poll ab `MessageModel.meta['poll']` me
  // nahi, TOP-LEVEL `.poll` field me store hota hai (`message_models.dart`
  // §3 fix) — is handler ko bhi usi ke hisaab se update kiya, purana
  // `meta['poll']` merge hata diya (poora object replace karo, partial
  // merge mat karo — jaisa doc kehta hai).
  void _onPollUpdateEvent(Map<String, dynamic> event) {
    final messageId = event['message_id']?.toString();
    final pollJson = event['poll'] as Map<String, dynamic>?;
    if (messageId == null || pollJson == null) return;
    final idx = _messages.indexWhere((m) => m.id == messageId);
    if (idx == -1) return;
    setState(() {
      _messages[idx].poll = PollModel.fromJson(pollJson);
    });
  }

  // 🔥 NAYA (Phase 2, §7.7) — link preview + transcript live update.
  // `.meta` poora replace karte hain (backend jo bhi naya `meta` bhejta
  // hai wahi source of truth hai) — in-place update, poori list reload
  // nahi, taaki scroll position/keyboard focus disturb na ho.
  void _onMetaUpdateEvent(Map<String, dynamic> event) {
    final messageId = event['message_id']?.toString();
    final metaJson = event['meta'] as Map<String, dynamic>?;
    if (messageId == null || metaJson == null) return;
    final idx = _messages.indexWhere((m) => m.id == messageId);
    if (idx == -1) return;
    setState(() {
      _messages[idx].meta = metaJson;
    });
  }

  // Poll REST se bana (hume apna khud ka response mil gaya) — turant
  // insert karo. Backend group ke baaki members ko normal `chat_message`
  // event se hi ye poll bhejega (`poll` field nested hoga) — us event ka
  // `_onIncomingMessage` clientId/id based dedup already sambhal leta hai,
  // yahan alag se kuch nahi karna.
  void _insertPollMessage(PollModel poll) {
    if (_messages.any((m) => m.id == poll.messageId)) return;
    final msg = MessageModel(
      id: poll.messageId,
      conversationId: widget.conversation.id,
      sender: UserMini(id: _myUserId ?? '', displayName: _l10n.chatYou),
      type: MessageType.poll,
      text: poll.question,
      poll: poll,
      createdAt: DateTime.now(),
    );
    setState(() => _messages.add(msg));
    _scrollToBottom();
  }

  // 🔧 FIX (backend mismatch) — vote/close backend me POLL id se nahi,
  // us poll ke underlying MESSAGE id se hote hain
  // (`POST /messages/<message_id>/poll/vote/`). Pehle `pollJson['id']`
  // bheja jaa raha tha, jo backend `Message` id expect karta hai — 404
  // deta raha hoga. Ab seedha `msg.id` bhejo.
  Future<void> _votePoll(MessageModel msg, List<String> optionIds) async {
    try {
      final updated = await MessageApiService.votePoll(msg.id, optionIds);
      if (!mounted) return;
      setState(() {
        final idx = _messages.indexWhere((m) => m.id == msg.id);
        if (idx != -1) _messages[idx].poll = updated;
      });
    } on MessageApiException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  void _showCreatePollSheet() {
    final questionCtrl = TextEditingController();
    final optionCtrls = <TextEditingController>[TextEditingController(), TextEditingController()];
    bool allowsMultiple = false;
    bool isSubmitting = false;

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (sheetCtx) => StatefulBuilder(builder: (sheetCtx, setSheetState) {
        return Padding(
          padding: EdgeInsets.only(
            left: 16, right: 16, top: 16,
            bottom: MediaQuery.of(sheetCtx).viewInsets.bottom + 16,
          ),
          child: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(_l10n.chatCreatePoll, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
              const SizedBox(height: 12),
              TextField(
                controller: questionCtrl,
                decoration: InputDecoration(hintText: _l10n.chatPollQuestion, border: OutlineInputBorder()),
                maxLength: 300,
              ),
              const SizedBox(height: 8),
              ...List.generate(optionCtrls.length, (i) => Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Row(children: [
                      Expanded(
                        child: TextField(
                          controller: optionCtrls[i],
                          decoration: InputDecoration(hintText: _l10n.pollOptionNumber(i + 1), border: const OutlineInputBorder()),
                          maxLength: 200,
                        ),
                      ),
                      if (optionCtrls.length > 2)
                        IconButton(tooltip: 'Remove', 
                          icon: const Icon(Icons.remove_circle_outline),
                          onPressed: () => setSheetState(() => optionCtrls.removeAt(i)),
                        ),
                    ]),
                  )),
              if (optionCtrls.length < 12)
                TextButton.icon(
                  onPressed: () => setSheetState(() => optionCtrls.add(TextEditingController())),
                  icon: const Icon(Icons.add),
                  label: Text(_l10n.addOptionLabel),
                ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                value: allowsMultiple,
                title: Text(_l10n.chatAllowMultipleAnswers),
                onChanged: (v) => setSheetState(() => allowsMultiple = v),
              ),
              // 🔧 FIX (backend mismatch) — "Anonymous voting" switch hata
              // diya: backend `Poll` model me `is_anonymous` field hai hi
              // nahi (sirf allow_multiple_answers/is_closed/closed_at/
              // closed_by hain) — pehle ye silently ignore ho raha tha,
              // user ko galat expectation deta tha ki votes anonymous hain.
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: isSubmitting ? null : () async {
                    final question = questionCtrl.text.trim();
                    final options = optionCtrls.map((c) => c.text.trim()).where((t) => t.isNotEmpty).toList();
                    if (question.isEmpty || options.length < 2) {
                      ScaffoldMessenger.of(sheetCtx).showSnackBar(
                        SnackBar(content: Text(_l10n.chatPollNeedsQuestionAndTwo)),
                      );
                      return;
                    }
                    setSheetState(() => isSubmitting = true);
                    try {
                      final poll = await MessageApiService.createPoll(
                        widget.conversation.id,
                        question: question,
                        options: options,
                        allowsMultipleAnswers: allowsMultiple,
                      );
                      _insertPollMessage(poll);
                      if (mounted) Navigator.pop(sheetCtx);
                    } on MessageApiException catch (e) {
                      setSheetState(() => isSubmitting = false);
                      ScaffoldMessenger.of(sheetCtx).showSnackBar(SnackBar(content: Text(e.message)));
                    }
                  },
                  child: isSubmitting
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : Text(_l10n.chatSendPoll),
                ),
              ),
            ]),
          ),
        );
      }),
    );
  }

  // ============================================================
  // 🔥 NAYA — SCHEDULED MESSAGES
  // ============================================================

  void _showScheduleMessageSheet() {
    final textCtrl = TextEditingController(text: _textController.text);
    DateTime selected = DateTime.now().add(const Duration(hours: 1));
    bool isSubmitting = false;

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (sheetCtx) => StatefulBuilder(builder: (sheetCtx, setSheetState) {
        return Padding(
          padding: EdgeInsets.only(
            left: 16, right: 16, top: 16,
            bottom: MediaQuery.of(sheetCtx).viewInsets.bottom + 16,
          ),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(_l10n.chatScheduleMessageTitle, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
            const SizedBox(height: 12),
            TextField(
              controller: textCtrl,
              minLines: 1, maxLines: 4,
              decoration: InputDecoration(hintText: _l10n.chatMessageFieldLabel, border: OutlineInputBorder()),
            ),
            const SizedBox(height: 12),
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.event),
              title: Text("${selected.day}/${selected.month}/${selected.year} • "
                  "${selected.hour.toString().padLeft(2, '0')}:${selected.minute.toString().padLeft(2, '0')}"),
              trailing: const Icon(Icons.edit_calendar_outlined),
              onTap: () async {
                final date = await showDatePicker(
                  context: sheetCtx,
                  initialDate: selected,
                  firstDate: DateTime.now(),
                  lastDate: DateTime.now().add(const Duration(days: 365)),
                );
                if (date == null) return;
                final time = await showTimePicker(context: sheetCtx, initialTime: TimeOfDay.fromDateTime(selected));
                if (time == null) return;
                setSheetState(() => selected = DateTime(date.year, date.month, date.day, time.hour, time.minute));
              },
            ),
            const SizedBox(height: 8),
            Row(children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: () {
                    Navigator.pop(sheetCtx);
                    _showManageScheduledSheet();
                  },
                  child: Text(_l10n.chatViewScheduled),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: ElevatedButton(
                  onPressed: isSubmitting ? null : () async {
                    if (textCtrl.text.trim().isEmpty) return;
                    if (selected.isBefore(DateTime.now())) {
                      ScaffoldMessenger.of(sheetCtx).showSnackBar(
                        SnackBar(content: Text(_l10n.chatPickFutureTime)),
                      );
                      return;
                    }
                    setSheetState(() => isSubmitting = true);
                    try {
                      await MessageApiService.scheduleMessage(
                        widget.conversation.id,
                        text: textCtrl.text.trim(),
                        scheduledFor: selected,
                      );
                      if (mounted) {
                        Navigator.pop(sheetCtx);
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: Text(_l10n.chatMessageScheduled)),
                        );
                      }
                    } on MessageApiException catch (e) {
                      setSheetState(() => isSubmitting = false);
                      ScaffoldMessenger.of(sheetCtx).showSnackBar(SnackBar(content: Text(e.message)));
                    }
                  },
                  child: isSubmitting
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : Text(_l10n.scheduleLabel),
                ),
              ),
            ]),
          ]),
        );
      }),
    );
  }

  void _showManageScheduledSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (sheetCtx) => FutureBuilder<List<ScheduledMessageModel>>(
        future: MessageApiService.getScheduledMessages(widget.conversation.id),
        builder: (context, snap) {
          if (!snap.hasData) {
            return const SizedBox(height: 120, child: Center(child: CircularProgressIndicator()));
          }
          final items = snap.data!;
          return StatefulBuilder(builder: (sheetCtx, setSheetState) {
            return SafeArea(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Padding(
                  padding: EdgeInsets.all(14),
                  child: Text(_l10n.chatScheduledMessagesTitle, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                ),
                if (items.isEmpty)
                  Padding(padding: EdgeInsets.all(20), child: Text(_l10n.chatNoScheduled)),
                ...items.map((s) => ListTile(
                      leading: const Icon(Icons.schedule_send_outlined),
                      title: Text(s.text ?? '', maxLines: 2, overflow: TextOverflow.ellipsis),
                      subtitle: Text(
                        "${s.scheduledFor.day}/${s.scheduledFor.month} • "
                        "${s.scheduledFor.hour.toString().padLeft(2, '0')}:${s.scheduledFor.minute.toString().padLeft(2, '0')}",
                      ),
                      trailing: IconButton(tooltip: 'Delete', 
                        icon: const Icon(Icons.delete_outline, color: Colors.red),
                        onPressed: () async {
                          try {
                            await MessageApiService.cancelScheduledMessage(s.id);
                            items.removeWhere((e) => e.id == s.id);
                            setSheetState(() {});
                          } catch (_) {}
                        },
                      ),
                    )),
              ]),
            );
          });
        },
      ),
    );
  }

  // 🔥 NAYA — backend `conversation_wallpaper_updated` group_send event
  // (khud apne user-channel pe aata hai, taaki wallpaper doosre connected
  // device pe bhi turant sync ho jaaye).
  void _onWallpaperEvent(Map<String, dynamic> event) {
    final conversationId = event['conversation_id']?.toString();
    if (conversationId != widget.conversation.id) return;
    final wallpaperUrl = event['wallpaper_url']?.toString();
    setState(() => _wallpaperUrl = (wallpaperUrl != null && wallpaperUrl.isNotEmpty) ? wallpaperUrl : null);
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
    if (_otherTyping) return _l10n.chatTyping;
    if (_otherOnline) return _l10n.chatOnline;
    if (_otherLastSeen != null) return _l10n.chatLastSeen(_formatLastSeen(_otherLastSeen!));
    return null;
  }

  String _formatLastSeen(DateTime dt) {
    final now = DateTime.now();
    final local = dt.toLocal();
    final isToday = now.year == local.year && now.month == local.month && now.day == local.day;
    final yesterday = now.subtract(const Duration(days: 1));
    final isYesterday = yesterday.year == local.year && yesterday.month == local.month && yesterday.day == local.day;
    final time = "${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}";
    if (isToday) return _l10n.chatLastSeenToday(time);
    if (isYesterday) return _l10n.chatLastSeenYesterday(time);
    return _l10n.chatLastSeenOn("${local.day}/${local.month}/${local.year}");
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
    final optimistic = MessageModel(id: clientId, conversationId: widget.conversation.id, sender: UserMini(id: _myUserId ?? '', displayName: _l10n.chatYou), type: MessageType.text, text: text, replyTo: replyToId, clientId: clientId, createdAt: DateTime.now(), isSending: true);
    setState(() {
      _messages.add(optimistic);
      _textController.clear();
      _replyingTo = null;
      _mentionQuery = null; // 🔥 NAYA (Phase 3, §2.2) — send hote hi overlay band
      _smartReplies = []; // 🔥 NAYA (Phase 3, §1 #11) — apna hi reply bhej diya, chips hata do
    });
    // 🔥 NAYA (Phase 3, §7.10) — send ho gaya, server-side draft turant clear
    // karo (1.5s debounce ka intezaar mat karo — warna send + turant-band-
    // karna ki race me purana draft list-preview me reh sakta hai).
    _draftSaveTimer?.cancel();
    _lastSavedDraft = '';
    MessageApiService.updateSettings(widget.conversation.id, draftText: '').catchError((_) {
      return ConversationSettings();
    });
    _scrollToBottom();
    _dispatch(
      PendingMessage(clientId: clientId, conversationId: widget.conversation.id, type: MessageType.text, text: text, replyTo: replyToId, createdAt: optimistic.createdAt),
      viaSocket: _isSocketConnected,
    );
  }

  // ============================================================
  // 🔥 NAYA (M6-FE) — send state: sending (clock) → sent → failed (red "!")
  // ============================================================
  //
  // State existing MessageModel flags se hi nikalti hai (model change nahi):
  //   sending = isSending && !sendFailed   (clock / spinner)
  //   sent    = isSending == false          (server ka message)
  //   failed  = sendFailed                  (red "!", tap = Resend / Delete)
  // `isSending` failure me bhi true rehta hai taaki local preview dikhta rahe;
  // bubble `_inFlight` se spinner decide karta hai.
  //
  // Har send pehle outbox (MessageCacheService) me persist hota hai, success
  // par hata diya jaata hai. App kill / network drop par message bachta hai
  // aur chat reopen par (ya `MessageApiService.flushOutbox()` se) auto-retry hota hai.

  int _msgIndex(String clientId) => _messages.indexWhere((m) => m.clientId == clientId);

  /// Outbox me daalo, phir bhejo. Kabhi throw nahi karta.
  Future<void> _dispatch(PendingMessage p, {bool viaSocket = false}) async {
    await MessageCacheService.upsertPending(p);
    if (!mounted) return; // screen band — outbox me hai, flush/reopen retry karega
    if (viaSocket) {
      // Socket fire-and-forget hai (ack nahi). Echo (`chat_message` with same
      // clientId) aate hi bubble "sent" ho jaata hai; na aaye to watchdog failed karega.
      if (p.type == MessageType.text) {
        _socket.sendMessage(text: p.text ?? '', clientId: p.clientId, replyTo: p.replyTo);
      } else {
        _socket.sendMessage(text: p.text ?? '', messageType: p.type, clientId: p.clientId);
      }
      _armSendWatchdog(p.clientId);
      return;
    }
    await _runSend(p);
  }

  Future<void> _runSend(PendingMessage p) async {
    try {
      final sent = await MessageApiService.sendPending(p, onProgress: (v) {
        if (!mounted) return;
        setState(() { final i = _msgIndex(p.clientId); if (i != -1) _messages[i].uploadProgress = v; });
      });
      _sendWatchdogs.remove(p.clientId)?.cancel();
      if (!mounted) return;
      setState(() { final i = _msgIndex(p.clientId); if (i != -1) _messages[i] = sent; });
      if (p.type == MessageType.text) _bumpSentTodayCounter();
    } catch (e) {
      if (!mounted) return;
      // M9a-FE — admin-only 403: failed-tick/dialog nahi, composer banner mode me jao.
      if (widget.conversation.isGroup && e is MessageApiException && e.code == 'admins_only') {
        _sendWatchdogs.remove(p.clientId)?.cancel();
        _onAdminOnlyBlocked(e.message, clientId: p.clientId);
        return;
      }
      setState(() { final i = _msgIndex(p.clientId); if (i != -1) _messages[i].sendFailed = true; });
      _maybeShowGroupSendBlockedDialog(e);
      if (p.localPaths.isNotEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatUploadFailed(e.toString()))));
      }
    }
  }

  void _armSendWatchdog(String clientId) {
    _sendWatchdogs.remove(clientId)?.cancel();
    _sendWatchdogs[clientId] = Timer(const Duration(seconds: 15), () {
      _sendWatchdogs.remove(clientId);
      if (!mounted) return;
      final i = _msgIndex(clientId);
      if (i == -1 || !_messages[i].isSending || _messages[i].sendFailed) return;
      setState(() => _messages[i].sendFailed = true);
    });
  }

  // Failed bubble tap / long-press: Resend ya Delete.
  // TODO(l10n): 'Resend' / 'Delete' ke liye chatResend / chatDiscard keys .arb me add karo.
  void _showFailedMessageSheet(MessageModel msg) {
    showModalBottomSheet(
      context: context,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (_) => SafeArea(child: Column(mainAxisSize: MainAxisSize.min, children: [
        ListTile(leading: const Icon(Icons.refresh), title: const Text('Resend'), onTap: () { Navigator.pop(context); _resendMessage(msg); }),
        ListTile(leading: const Icon(Icons.delete_outline, color: Colors.red), title: const Text('Delete', style: TextStyle(color: Colors.red)), onTap: () { Navigator.pop(context); _discardFailed(msg); }),
      ])),
    );
  }

  Future<void> _resendMessage(MessageModel msg) async {
    final cid = msg.clientId;
    if (cid == null) return;
    PendingMessage? p;
    for (final x in await MessageCacheService.getPending(widget.conversation.id)) {
      if (x.clientId == cid) p = x;
    }
    // Outbox write fail hua ho to bubble se hi rebuild (upload dobara hoga).
    p ??= PendingMessage(
      clientId: cid,
      conversationId: widget.conversation.id,
      type: msg.type,
      text: msg.text,
      replyTo: msg.replyTo,
      meta: msg.meta,
      localPaths: msg.localFilePaths ?? (msg.localFilePath != null ? [msg.localFilePath!] : const []),
      createdAt: msg.createdAt,
    );
    p = p.copyWith(permanentFailure: false);
    await MessageCacheService.upsertPending(p);
    if (!mounted) return;
    setState(() {
      final i = _msgIndex(cid);
      if (i != -1) { _messages[i].sendFailed = false; _messages[i].uploadProgress = 0.0; }
    });
    // Resend hamesha REST se (socket ack nahi deta, failure pata nahi chalta).
    await _runSend(p);
  }

  Future<void> _discardFailed(MessageModel msg) async {
    final cid = msg.clientId;
    if (cid != null) _sendWatchdogs.remove(cid)?.cancel();
    setState(() => _messages.removeWhere((m) => identical(m, msg) || (cid != null && m.clientId == cid)));
    if (cid != null) await MessageCacheService.removePending(cid);
  }

  MessageModel _optimisticFromPending(PendingMessage p) {
    final multi = p.localPaths.length > 1 || (p.meta?.containsKey('count') ?? false);
    return MessageModel(
      id: p.clientId,
      conversationId: p.conversationId,
      sender: UserMini(id: _myUserId ?? '', displayName: _l10n.chatYou),
      type: p.type,
      text: p.text ?? '',
      replyTo: p.replyTo,
      meta: p.meta,
      clientId: p.clientId,
      createdAt: p.createdAt,
      isSending: true,
      uploadProgress: p.localPaths.isNotEmpty ? 0.0 : null,
      localFilePath: (!multi && p.localPaths.length == 1) ? p.localPaths.first : null,
      localFilePaths: multi ? p.localPaths : null,
    );
  }

  /// Chat khulte hi: outbox ke is conversation ke messages wapas bubble me
  /// laao aur retry-eligible ko auto-retry karo. Jo server pe already hain
  /// (app kill se pehle pahunch gaye the) unhe outbox se hata do.
  Future<void> _restorePending(List<MessageModel> serverMessages) async {
    final serverIds = serverMessages.map((m) => m.clientId).whereType<String>().toSet();
    final pending = await MessageCacheService.getPending(widget.conversation.id);
    if (pending.isEmpty || !mounted) return;
    final added = <MessageModel>[];
    final needFlush = <String>{};
    final joining = <PendingMessage>[];
    for (final p in pending) {
      if (serverIds.contains(p.clientId)) { await MessageCacheService.removePending(p.clientId); continue; }
      if (_msgIndex(p.clientId) != -1) continue; // isi session ka message, screen pe hai
      final m = _optimisticFromPending(p);
      if (!p.canAutoRetry) {
        m.sendFailed = true;
      } else if (MessageApiService.isSending(p.clientId)) {
        joining.add(p); // pehle se chal rahi send (flush) — uska result sunna hai
      } else {
        needFlush.add(p.clientId);
      }
      added.add(m);
    }
    if (!mounted || added.isEmpty) return;
    setState(() => _messages.addAll(added));
    _scrollToBottom();
    for (final p in joining) { _runSend(p); }
    if (needFlush.isEmpty) return;
    await MessageApiService.flushOutbox(); // sequential, server-reconcile ke saath
    await _reconcileRestored(needFlush);
  }

  /// Flush ke baad restored bubbles ko sach se match karo: server pe mila =
  /// sent, outbox me abhi bhi hai = failed.
  Future<void> _reconcileRestored(Set<String> clientIds) async {
    if (!mounted) return;
    List<MessageModel> server = const [];
    try {
      server = await MessageApiService.getMessages(widget.conversation.id, page: 1, pageSize: _kPageSize + clientIds.length);
    } catch (_) {}
    if (!mounted) return;
    setState(() {
      for (final cid in clientIds) {
        final i = _msgIndex(cid);
        if (i == -1 || !_messages[i].isSending) continue;
        MessageModel? match;
        for (final m in server) { if (m.clientId == cid) match = m; }
        if (match != null) { _messages[i] = match; } else { _messages[i].sendFailed = true; }
      }
    });
  }

  // 🔥 NAYA — day rollover pe local "aaj bheje messages" counter reset.
  void _bumpSentTodayCounter() {
    final now = DateTime.now();
    if (now.day != _messagesCounterDay.day || now.month != _messagesCounterDay.month || now.year != _messagesCounterDay.year) {
      _myMessagesSentToday = 0;
      _messagesCounterDay = now;
    }
    _myMessagesSentToday++;
  }

  // 🔥 NAYA — group send REST se fail hua (403 + `code` — exact values
  // `group_rules.check_group_send_permission()` se: 'admins_only',
  // 'daily_limit_reached', 'not_a_member') to generic "sendFailed" tick ke
  // bajaye ek clear dialog dikhao: kyun nahi gaya, aur agla step kya hai.
  // Non-group ya non-permission errors (network, validation, etc.) me
  // chup-chaap sirf failed-tick hi dikhta hai jaisa pehle tha — dialog
  // spam nahi hota.
  void _maybeShowGroupSendBlockedDialog(Object e) {
    if (!widget.conversation.isGroup || !mounted) return;
    if (e is! MessageApiException) return;
    if (e.code != 'admins_only' && e.code != 'daily_limit_reached' && e.code != 'not_a_member') return;

    final isLimitBlock = e.code == 'daily_limit_reached';
    final isRemoved = e.code == 'not_a_member';

    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        icon: Icon(
          isLimitBlock ? Icons.hourglass_bottom : (isRemoved ? Icons.person_off_outlined : Icons.lock_outline),
          color: Theme.of(context).colorScheme.primary,
        ),
        title: Text(isLimitBlock ? _l10n.chatDailyLimitReached : (isRemoved ? _l10n.chatNoLongerMember : _l10n.chatMessageNotAllowed)),
        content: Text(e.message),
        actions: [TextButton(onPressed: () => Navigator.pop(context), child: Text(_l10n.ok))],
      ),
    );
    // 🔥 NAYA — agar admin ne beech me hi hata diya (`not_a_member`), to
    // apni local admin/moderator flags bhi reset kar do — warna stale
    // "Message permissions" jaisa admin-only menu galti se dikhta rahega.
    if (isRemoved && mounted) {
      setState(() { _isGroupAdmin = false; _isGroupModerator = false; });
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
    final optimistic = MessageModel(id: clientId, conversationId: widget.conversation.id, sender: UserMini(id: _myUserId ?? '', displayName: _l10n.chatYou), type: messageType, text: text ?? '', meta: {'file_name': fileName, ...?extraMeta}, clientId: clientId, createdAt: DateTime.now(), isSending: true, uploadProgress: 0.0, localFilePath: path);
    setState(() => _messages.add(optimistic)); _scrollToBottom();
    // M6-FE — upload + send + failure/retry ab outbox pipeline (`_dispatch`) se.
    await _dispatch(PendingMessage(
      clientId: clientId,
      conversationId: widget.conversation.id,
      type: messageType,
      text: text,
      meta: {'file_name': fileName, ...?extraMeta},
      localPaths: [path],
      createdAt: optimistic.createdAt,
    ));
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
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatStickerFailed(e.toString()))));
    }
  }

  // ============================================================
  // 🔥 NAYA: VOICE NOTE RECORDING — seedha chat input bar ke mic se
  // ============================================================
  Future<void> _startRecording({bool locked = false}) async {
    if (_isRecording || _micStarting) return;
    _micStarting = true;
    try {
      await _startRecordingInner(locked: locked);
    } finally {
      _micStarting = false;
    }
  }

  Future<void> _startRecordingInner({required bool locked}) async {
    var micStatus = await Permission.microphone.status;
    if (!micStatus.isGranted) micStatus = await Permission.microphone.request();
    if (!micStatus.isGranted) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatMicPermission)));
      return;
    }
    final tempDir = await getTemporaryDirectory();
    final path = "${tempDir.path}/voice_${DateTime.now().millisecondsSinceEpoch}.m4a";
    await _recorder.start(const RecordConfig(encoder: AudioEncoder.aacLc), path: path);
    _recordPath = path;
    // 🔥 M4a — amplitude sampling (dBFS: -160..0; -60 se neeche ko silence maante hain)
    _ampSamples.clear();
    await _ampSub?.cancel();
    _ampSub = _recorder
        .onAmplitudeChanged(const Duration(milliseconds: 100))
        .listen((a) { if (!_recordPaused) _ampSamples.add(((a.current + 60) / 60).clamp(0.0, 1.0)); });
    _recordDuration = Duration.zero;
    _recordTimer?.cancel();
    _recordTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && !_recordPaused) setState(() => _recordDuration += const Duration(seconds: 1));
    });
    _recordLocked = locked;
    _recordPaused = false;
    _slideDx = 0; _slideDy = 0;
    if (mounted) setState(() => _isRecording = true);
  }

  // 🔥 M4c — lock mode: pause / resume
  Future<void> _togglePauseRecording() async {
    if (!_isRecording) return;
    try {
      if (_recordPaused) {
        await _recorder.resume();
      } else {
        await _recorder.pause();
      }
      if (mounted) setState(() => _recordPaused = !_recordPaused);
    } catch (_) {}
  }

  void _resetRecordGestureState() {
    _recordLocked = false;
    _recordPaused = false;
    _slideDx = 0; _slideDy = 0;
    _micPointer = null;
    _micReleased = false;
    _micHoldTimer?.cancel();
  }

  // ---- raw pointer handlers (mic button: down | stable ancestor: move/up/cancel) ----
  void _onMicDown(PointerDownEvent e) {
    if (_isRecording || _micStarting) return;
    _micPointer = e.pointer;
    _micStart = e.position;
    _micReleased = false;
    _lastMicPointerAt = DateTime.now();
    _micHoldTimer?.cancel();
    // 200ms hold = "hold-to-record"; usse pehle haath utha liya = tap (neeche _onMicUp)
    _micHoldTimer = Timer(const Duration(milliseconds: 200), () async {
      _micHoldTimer = null;
      HapticFeedback.selectionClick();
      await _startRecording();
      if (!mounted) return;
      if (!_isRecording) { _resetRecordGestureState(); return; } // permission denied etc.
      if (_micReleased && !_recordLocked) {
        // start hote-hote haath utha liya
        _resetRecordGestureState();
        await _stopRecordingAndSend();
      }
    });
  }

  void _onMicMove(PointerMoveEvent e) {
    if (e.pointer != _micPointer) return;
    _lastMicPointerAt = DateTime.now();
    final d = e.position - _micStart;
    if (_micHoldTimer != null) {
      // hold se pehle zyada hila diya = scroll/accidental, recording nahi
      if (d.distance > 24) { _micHoldTimer?.cancel(); _micHoldTimer = null; _micPointer = null; }
      return;
    }
    if (!_isRecording || _recordLocked) return;
    setState(() {
      _slideDx = d.dx.clamp(-_kCancelDistance * 1.5, 0.0);
      _slideDy = d.dy.clamp(-_kLockDistance * 1.5, 0.0);
    });
    if (d.dy < -_kLockDistance && d.dy.abs() > d.dx.abs()) {
      HapticFeedback.mediumImpact();
      setState(() { _recordLocked = true; _slideDx = 0; _slideDy = 0; });
      _micPointer = null;
    } else if (d.dx < -_kCancelDistance) {
      HapticFeedback.mediumImpact();
      _micPointer = null;
      _resetRecordGestureState();
      _cancelRecording();
    }
  }

  void _onMicUp(PointerUpEvent e) {
    if (e.pointer != _micPointer) return;
    _micPointer = null;
    _lastMicPointerAt = DateTime.now();
    if (_micHoldTimer != null) {
      // TAP (hold nahi): purane behaviour jaisa — recording shuru, lekin LOCKED
      // (hands-free), taaki delete / pause / send buttons se control ho.
      _micHoldTimer?.cancel();
      _micHoldTimer = null;
      _startRecording(locked: true);
      return;
    }
    if (_micStarting) { _micReleased = true; return; }
    if (_isRecording && !_recordLocked) {
      _resetRecordGestureState();
      _stopRecordingAndSend(); // hold-release = send
    }
  }

  void _onMicCancel(PointerCancelEvent e) {
    if (e.pointer != _micPointer) return;
    _micPointer = null;
    _micHoldTimer?.cancel();
    _micHoldTimer = null;
    // System ne gesture cheen li (call/notification): recording DELETE mat karo,
    // lock kar do — user khud send/delete chune.
    if (_isRecording && !_recordLocked) setState(() { _recordLocked = true; _slideDx = 0; _slideDy = 0; });
  }

  Future<void> _stopRecordingAndSend() async {
    _recordTimer?.cancel();
    await _ampSub?.cancel();
    _ampSub = null;
    final waveform = _buildWaveform(_ampSamples); // 🔥 M4a — ~40 bars, 0..100
    _ampSamples.clear();
    final path = await _recorder.stop();
    final duration = _recordDuration;
    _resetRecordGestureState();
    if (mounted) setState(() { _isRecording = false; _recordDuration = Duration.zero; });
    if (path == null) return;
    // bahut chhota (accidental tap) recording ho to mat bhejo
    if (duration.inMilliseconds < 800) {
      try { await File(path).delete(); } catch (_) {}
      return;
    }
    final fileName = "voice_note_${DateTime.now().millisecondsSinceEpoch}.m4a";
    await _uploadAndSendFile(File(path), MessageType.audio, fileName, extraMeta: {
      'duration_seconds': duration.inSeconds,
      if (waveform.isNotEmpty) 'waveform': waveform, // 🔥 M4a — BE max 64 ints sanitize karta hai
    });
  }

  Future<void> _cancelRecording() async {
    _recordTimer?.cancel();
    await _ampSub?.cancel();
    _ampSub = null;
    _ampSamples.clear();
    final path = await _recorder.stop();
    _resetRecordGestureState();
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
        if (requested == LocationPermission.denied || requested == LocationPermission.deniedForever) throw Exception(_l10n.chatLocationPermissionDenied);
      }
      final pos = await Geolocator.getCurrentPosition(); final clientId = _newClientId();
      final optimistic = MessageModel(id: clientId, conversationId: widget.conversation.id, sender: UserMini(id: _myUserId ?? '', displayName: _l10n.chatYou), type: MessageType.location, text: _l10n.locationLabel, meta: {'lat': pos.latitude, 'lng': pos.longitude}, clientId: clientId, createdAt: DateTime.now(), isSending: true);
      setState(() => _messages.add(optimistic)); _scrollToBottom();
      await _dispatch(PendingMessage(clientId: clientId, conversationId: widget.conversation.id, type: MessageType.location, text: _l10n.locationLabel, meta: {'lat': pos.latitude, 'lng': pos.longitude}, createdAt: optimistic.createdAt));
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatLocationShareFailed(e.toString()))));
    }
  }

  void _showAttachmentSheet() {
    showModalBottomSheet(context: context, backgroundColor: Theme.of(context).colorScheme.surface, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))), builder: (_) => SafeArea(child: Wrap(children: [
      // 🔥 NAYA — seedha camera se photo/video khinch ke bhejo
      _attachmentTile(Icons.camera_alt, _l10n.camera, const Color(0xFF00BCD4), _showCameraChooser),
      // 🔥 NAYA — Photo aur Video gallery ab do ALAG buttons hain — Photo
      // Gallery sirf images ka native picker kholta hai, Video Gallery
      // sirf videos ka. Pehle ek hi "Gallery" button tha jo pehle mixed
      // picker try karta, na mile to images-only, na mile to ek video —
      // isliye kabhi photo aati thi kabhi video, mixed/unpredictable tha.
      _attachmentTile(Icons.photo_library, _l10n.chatPhotoGallery, const Color(0xFF9C27B0), _pickAndSendPhotoGallery),
      _attachmentTile(Icons.video_library, _l10n.chatVideoGallery, const Color(0xFFE53935), _pickAndSendVideoGallery),
      _attachmentTile(Icons.mic, _l10n.chatAudio, const Color(0xFFFF9800), () => _pickAndSendAttachment(MessageType.audio)),
      _attachmentTile(Icons.insert_drive_file, _l10n.chatFile, const Color(0xFF3F51B5), () => _pickAndSendAttachment(MessageType.file)),
      _attachmentTile(Icons.slideshow, _l10n.chatPresentation, const Color(0xFF00897B), () => _pickAndSendAttachment(MessageType.presentation)),
      _attachmentTile(Icons.location_on, _l10n.locationLabel, const Color(0xFF4CAF50), _sendLocation),
      // 🔥 NAYA
      _attachmentTile(Icons.poll, _l10n.pollLabel, const Color(0xFF6A4CE0), _showCreatePollSheet),
      _attachmentTile(Icons.schedule_send, _l10n.chatScheduleMessageTitle, const Color(0xFF2E7D32), _showScheduleMessageSheet),
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
      sender: UserMini(id: _myUserId ?? '', displayName: _l10n.chatYou),
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

    await _dispatch(PendingMessage(
      clientId: clientId,
      conversationId: widget.conversation.id,
      type: MessageType.image,
      text: caption,
      meta: {'count': files.length},
      localPaths: localPaths,
      createdAt: optimistic.createdAt,
    ));
  }

  // 🔥 NAYA — "Camera" tap karte hi Photo ya Video khinchne ka chhota chooser
  void _showCameraChooser() {
    showModalBottomSheet(context: context, backgroundColor: Theme.of(context).colorScheme.surface, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))), builder: (_) => SafeArea(child: Wrap(children: [
      _attachmentTile(Icons.camera_alt, _l10n.takePhotoLabel, const Color(0xFF9C27B0), () => _pickAndSendAttachment(MessageType.image, source: ImageSource.camera)),
      _attachmentTile(Icons.videocam, _l10n.recordVideo, const Color(0xFFE53935), () => _pickAndSendAttachment(MessageType.video, source: ImageSource.camera)),
    ])));
  }

  Widget _attachmentTile(IconData icon, String label, Color color, VoidCallback onTap) {
    return ListTile(leading: CircleAvatar(backgroundColor: color, child: Icon(icon, color: Colors.white)), title: Text(label), onTap: () { Navigator.pop(context); onTap(); });
  }

  // 🔥 EXTENDED (Phase 3, §2.2/§7.10) — typing indicator ke saath-saath ab
  // ye 2 aur cheezein bhi karta hai: (1) `@` mention query detect karke
  // overlay show/hide karna, (2) debounced server-side draft autosave.
  void _onTypingChanged(String value) {
    _socket.sendTyping(value.isNotEmpty);

    // 🔥 NAYA (Phase 3, §2.2) — group chat me hi mention possible hai.
    if (widget.conversation.isGroup) {
      final cursor = _textController.selection.baseOffset;
      final query = extractMentionQuery(value, cursor < 0 ? value.length : cursor);
      if (query != _mentionQuery) {
        setState(() => _mentionQuery = query);
      }
    }

    // 🔥 NAYA (Phase 3, §7.10) — debounced draft autosave.
    _scheduleDraftSave(value);
  }

  // 🔥 NAYA (Phase 3, §2.2) — suggestion list se ek member select kiya —
  // text me `@username ` insert karo aur overlay band karo.
  void _onMentionSelected(UserMini user) {
    final updated = insertMention(_textController.value, user);
    _textController.value = updated;
    setState(() => _mentionQuery = null);
  }

  // M5-FE — strip ke "+" se khulta hai: extended emoji grid. Pehle yahi
  // 6-emoji ka bottom sheet tha (long-press -> React tile -> sheet).
  void _showReactionPicker(MessageModel msg) {
    final cs = Theme.of(context).colorScheme;
    final myCurrent = msg.myReaction(_myUserId ?? '');
    showModalBottomSheet(
      context: context,
      backgroundColor: cs.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (_) => SafeArea(
        child: ConstrainedBox(
          constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.5),
          child: GridView.count(
            shrinkWrap: true,
            crossAxisCount: 8,
            padding: const EdgeInsets.fromLTRB(12, 16, 12, 16),
            children: _kMoreEmojis.map((emoji) {
              final selected = emoji == myCurrent;
              return GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () { Navigator.pop(context); _toggleReaction(msg, emoji); },
                child: Container(
                  alignment: Alignment.center,
                  decoration: BoxDecoration(color: selected ? cs.primary.withOpacity(0.12) : null, shape: BoxShape.circle),
                  child: Text(emoji, style: const TextStyle(fontSize: 26)),
                ),
              );
            }).toList(),
          ),
        ),
      ),
    );
  }

  // M5-FE — double-tap = ❤. Agar pehle se ❤ laga hai to kuch nahi karte
  // (accidental double-tap se reaction hat jaana bura UX hoga); hatane ke
  // liye chip tap karo.
  void _onDoubleTapReact(MessageModel msg) {
    if (msg.deletedForEveryone || msg.deletedForMe) return;
    if (msg.myReaction(_myUserId ?? '') == _kDoubleTapEmoji) return;
    HapticFeedback.lightImpact();
    _toggleReaction(msg, _kDoubleTapEmoji);
  }

  void _toggleReaction(MessageModel msg, String emoji) async {
    final myCurrent = msg.myReaction(_myUserId ?? '');
    if (myCurrent == emoji) { setState(() { msg.reactions.removeWhere((r) => r.user.id == _myUserId); }); try { await MessageApiService.removeReaction(msg.id); } catch (_) {} }
    else { setState(() { final idx = msg.reactions.indexWhere((r) => r.user.id == _myUserId); final mine = MessageReactionModel(id: idx != -1 ? msg.reactions[idx].id : '${msg.id}-me', user: UserMini(id: _myUserId ?? '', displayName: _l10n.chatYou), emoji: emoji, createdAt: DateTime.now()); if (idx != -1) msg.reactions[idx] = mine; else msg.reactions.add(mine); }); _socket.sendReaction(msg.id, emoji); }
  }

  // M5-FE — long-press: bubble ke upar emoji strip (6 emoji + "+"), neeche
  // alag bottom-sheet me baaki menu (reply/copy/forward/...). Dono ek hi
  // dialog route me hain, taaki barrier-tap se saath me band hon.
  void _showMessageActions(MessageModel msg, bool isMe, {Rect? anchor}) {
    if (msg.deletedForEveryone || msg.deletedForMe) return;
    // M6-FE — failed: Resend/Delete sheet. Sending: abhi real message id nahi
    // (id == clientId), isliye react/reply/forward/delete server pe bhejna galat hoga.
    if (msg.sendFailed) { _showFailedMessageSheet(msg); return; }
    if (msg.isSending) return;
    HapticFeedback.mediumImpact();
    final size = MediaQuery.of(context).size;
    final menuTiles = _messageActionTiles(msg, isMe);
    final myCurrent = msg.myReaction(_myUserId ?? '');
    showGeneralDialog(
      context: context,
      useRootNavigator: false, // tiles `Navigator.pop(context)` State-context se karte hain
      barrierDismissible: true,
      barrierLabel: MaterialLocalizations.of(context).modalBarrierDismissLabel,
      barrierColor: Colors.black26,
      transitionDuration: const Duration(milliseconds: 140),
      transitionBuilder: (_, anim, __, child) => FadeTransition(opacity: anim, child: child),
      pageBuilder: (dialogCtx, _, __) => _ReactionMenuOverlay(
        anchor: anchor ?? Rect.fromLTWH(0, size.height * 0.4, size.width, 0),
        isMe: isMe,
        myReaction: myCurrent,
        menuTiles: menuTiles,
        onPick: (emoji) { Navigator.pop(dialogCtx); _toggleReaction(msg, emoji); },
        onMore: () { Navigator.pop(dialogCtx); _showReactionPicker(msg); },
      ),
    );
  }

  List<Widget> _messageActionTiles(MessageModel msg, bool isMe) {
    return [
      ListTile(leading: const Icon(Icons.reply), title: Text(_l10n.reply), onTap: () { Navigator.pop(context); _startReply(msg); }),
      // NEW — forward just this one message straight to a picker.
      // 🔧 FIX (Phase 3, §4.3) — poll messages forward nahi ho sakte
      // (backend silently drop karta hai) — is item ko hi hide kar do,
      // UI me pehle hi rok dena better UX hai.
      if (msg.type != MessageType.poll)
        ListTile(leading: const Icon(Icons.forward), title: Text(_l10n.chatForward), onTap: () { Navigator.pop(context); _forwardOne(msg); }),
      // M5-FE — Copy: pehle menu me tha hi nahi. TODO(l10n): 'Copy'/'Copied'
      // ke liye chatCopy/chatCopied keys .arb me add karke yahan lagao.
      if (msg.text != null && msg.text!.trim().isNotEmpty)
        ListTile(
          leading: const Icon(Icons.copy_outlined),
          title: const Text('Copy'),
          onTap: () {
            Navigator.pop(context);
            Clipboard.setData(ClipboardData(text: msg.text!));
            ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Copied'), duration: Duration(seconds: 1)));
          },
        ),
      // 🔥 NAYA — "Seen by" / message-info (WhatsApp-style). Sirf apne
      // bheje hue messages pe — dusre ka message "kisne dekha" tum nahi
      // pooch sakte. Preview line ke liye plain text ya type-label bhejte
      // hain, screen khud `getReadStatus` call karke poori list laati hai.
      if (isMe)
        ListTile(
          leading: const Icon(Icons.info_outline_rounded),
          title: Text(_l10n.chatInfo),
          onTap: () {
            Navigator.pop(context);
            Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => MessageInfoScreen(
                  messageId: msg.id,
                  messagePreview: (msg.text != null && msg.text!.trim().isNotEmpty) ? msg.text : null,
                ),
              ),
            );
          },
        ),
      // 🔥 NAYA — Pin/Unpin (backend max 3 pinned/conversation — limit
      // cross hone par _pinMessage() snackbar me error dikha dega).
      ListTile(
        leading: Icon(_pinnedMessages.any((p) => p.message.id == msg.id) ? Icons.push_pin : Icons.push_pin_outlined),
        title: Text(_pinnedMessages.any((p) => p.message.id == msg.id) ? _l10n.chatUnpin : _l10n.chatPin),
        onTap: () {
          Navigator.pop(context);
          final alreadyPinned = _pinnedMessages.any((p) => p.message.id == msg.id);
          if (alreadyPinned) {
            _unpinMessage(msg.id);
          } else {
            _pinMessage(msg);
          }
        },
      ),
      // NEW — enter multi-select mode (starting with this message already
      // checked) so several messages can be picked and forwarded together.
      ListTile(leading: const Icon(Icons.check_circle_outline), title: Text(_l10n.chatSelect), onTap: () { Navigator.pop(context); _enterSelectionMode(msg); }),
      if (isMe && msg.type == MessageType.text) ListTile(leading: const Icon(Icons.edit_outlined), title: Text(_l10n.editLabel), onTap: () { Navigator.pop(context); _showEditDialog(msg); }),
      // 🔥 NAYA: media messages ke liye "Save to device" action bhi —
      // multi-image (album) message ho to sab photos ek-ek karke save hoti hain.
      if ((msg.fileUrl != null && msg.fileUrl!.isNotEmpty) || (msg.fileUrls != null && msg.fileUrls!.isNotEmpty))
        ListTile(leading: const Icon(Icons.download_outlined), title: Text(_l10n.chatSaveToDevice), onTap: () async {
          Navigator.pop(context);
          if (msg.fileUrls != null && msg.fileUrls!.length > 1) {
            for (final u in msg.fileUrls!) {
              await _downloadMediaUrl(context, msg, u.toString());
            }
          } else {
            _downloadMedia(context, msg);
          }
        }),
      ListTile(leading: const Icon(Icons.delete_outline, color: Colors.red), title: Text(_l10n.chatDeleteForMe, style: TextStyle(color: Colors.red)), onTap: () { Navigator.pop(context); _deleteMessage(msg, forEveryone: false); }),
      if (isMe) ListTile(leading: const Icon(Icons.delete_forever_outlined, color: Colors.red), title: Text(_l10n.chatDeleteForEveryone, style: TextStyle(color: Colors.red)), onTap: () { Navigator.pop(context); _deleteMessage(msg, forEveryone: true); }),
    ];
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

  Future<void> _forwardOne(MessageModel msg) => _openForwardPicker([msg]);

  Future<void> _forwardSelected() async {
    final selected = _messages.where((m) => _selectedMessageIds.contains(m.id)).toList();
    _exitSelectionMode();
    await _openForwardPicker(selected);
  }

  // 🔧 FIX (Phase 3, §4.3) — pehle sirf message ids pass hote the, isliye
  // `ForwardMessageScreen` ko pata hi nahi chalta tha ki selection me
  // koi text-message hai ya poll — caption field ka "text-only hide"
  // aur "polls excluded" wala UI logic (§4.3) implement hi nahi ho pa
  // raha tha. Ab poore `MessageModel` bhejte hain.
  Future<void> _openForwardPicker(List<MessageModel> messages) async {
    if (messages.isEmpty) return;
    final ok = await Navigator.of(context).push<bool>(
      MaterialPageRoute(builder: (_) => ForwardMessageScreen(messages: messages)),
    );
    if (ok == true && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(_l10n.chatMessageForwarded(messages.length)),
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
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatDeleteFailed(e.toString()))));
      }
    }
  }

  void _showEditDialog(MessageModel msg) {
    final controller = TextEditingController(text: msg.text ?? '');
    showDialog(context: context, builder: (_) => AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),title: Text(_l10n.chatEditMessageTitle), content: TextField(controller: controller, maxLines: 4, autofocus: true), actions: [
      TextButton(onPressed: () => Navigator.pop(context), child: Text(_l10n.cancel)),
      TextButton(onPressed: () async { final newText = controller.text.trim(); Navigator.pop(context); if (newText.isEmpty || newText == msg.text) return; try { final updated = await MessageApiService.editMessage(msg.id, newText); if (mounted) setState(() { final idx = _messages.indexWhere((m) => m.id == msg.id); if (idx != -1) _messages[idx] = updated; }); } catch (e) { if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatEditFailed(e.toString())))); } }, child: Text(_l10n.save)),
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
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatAlreadySaved)));
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
            Text(_l10n.chatDownloading),
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
          content: Text(isGalleryMedia ? _l10n.chatSavedToGallery : _l10n.chatDownloadedToFolder),
          action: isGalleryMedia ? null : SnackBarAction(label: _l10n.open, onPressed: () => MediaDownloadService.openFile(path)),
        ));
      }
    } catch (e) {
      if (context.mounted) Navigator.pop(context);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatDownloadFailed(e.toString()))));
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
    // 🔥 NAYA (M3b) — vanish ('after_seen') chat se nikalte hi local cache saaf:
    // warna 1-week MessageCacheService purane (BE se delete ho chuke) messages
    // agli baar turant dikha deta. Cache sirf cache hai — server source of truth,
    // isliye saaf karna safe hai. Fire-and-forget (dispose async nahi ho sakta).
    if (_disappearingDuration == 'after_seen') {
      MessageCacheService.saveMessages(widget.conversation.id, []);
    }
    for (final t in _sendWatchdogs.values) { t.cancel(); } // M6-FE
    _sendWatchdogs.clear();
    _socket.dispose();
    _textController.dispose();
    _scrollController.dispose();
    _recordTimer?.cancel();
    _ampSub?.cancel(); // 🔥 M4a
    _micHoldTimer?.cancel(); // 🔥 M4c
    _recorder.dispose(); // 🔥 NAYA
    _highlightTimer?.cancel(); // 🔥 NAYA (Phase 4, §2.1)
    _draftSaveTimer?.cancel(); // 🔥 NAYA (Phase 3, §7.10)
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final subtitle = _presenceSubtitle();
    final cs = Theme.of(context).colorScheme;
    // 🎨 THEME FIX — bg/AppBar/icons ab theme se aate hain (pehle hardcoded
    // navy header + off-white bg, dark mode me bhi same rehta tha).
    return Scaffold(
      appBar: _selectionMode ? _buildSelectionAppBar() : AppBar(
        backgroundColor: cs.primary,
        elevation: 3, // 🔥 NAYA — subtle depth, flat/dated na lage
        shadowColor: Colors.black45,
        iconTheme: IconThemeData(color: cs.onPrimary),
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
              backgroundColor: cs.onPrimary.withOpacity(0.24), // 🔥 NAYA — halka ring jaisa fallback background
              child: CircleAvatar(radius: 18, backgroundColor: AppThemeTokens.of(context).surface2, backgroundImage: widget.conversation.displayPhoto != null && widget.conversation.displayPhoto!.isNotEmpty ? CachedNetworkImageProvider(widget.conversation.displayPhoto!) : null, child: widget.conversation.displayPhoto == null || widget.conversation.displayPhoto!.isEmpty ? Icon(widget.conversation.isGroup ? Icons.group : Icons.person, color: cs.onSurfaceVariant, size: 18) : null),
            ),
            // 🔥 NAYA — online hone par avatar pe accent-blue dot
            if (!widget.conversation.isGroup && _otherOnline)
              Positioned(right: -1, bottom: -1, child: Container(width: 11, height: 11, decoration: BoxDecoration(color: AppThemeTokens.of(context).success, shape: BoxShape.circle, border: Border.all(color: cs.primary, width: 2)))),
          ]),
          const SizedBox(width: 12),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            Text(widget.conversation.displayTitle, style: TextStyle(color: cs.onPrimary, fontSize: 15.5, fontWeight: FontWeight.w600, letterSpacing: 0.1)),
            if (subtitle != null) Padding(padding: const EdgeInsets.only(top: 1), child: Text(subtitle, style: TextStyle(color: _otherTyping ? AppThemeTokens.of(context).success : cs.onPrimary.withOpacity(0.6), fontSize: 11.5))),
          ])),
        ]),
        ),
        actions: [
          // 🔥 NAYA (Phase 4, §2.1) — is conversation ke andar search.
          // Result tap karne pe MessageSearchScreen us message ka id le kar
          // pop hota hai — jise pakad ke seedha wahan scroll+highlight karo.
          IconButton(
            icon: Icon(Icons.search, color: cs.onPrimary),
            tooltip: _l10n.chatSearchInChat,
            onPressed: () async {
              final selectedId = await Navigator.push<String>(
                context,
                MaterialPageRoute(
                  builder: (_) => MessageSearchScreen(conversationId: widget.conversation.id),
                ),
              );
              if (selectedId != null && mounted) {
                await _tryJumpToMessageId(selectedId);
              }
            },
          ),
          IconButton(icon: Icon(Icons.cast_for_education, color: cs.onPrimary), tooltip: _l10n.chatStudyRoom, onPressed: _openStudyRoom),
          // 🔥 NAYA — "Doubts" tab entry point (persistent upvotable
          // question board + anonymous asking). Sirf group chats me
          // dikhta hai — private 1:1 chat me "classroom" concept hi nahi
          // hota. `widget.conversation.group!.id` (GroupMini.id) hi group
          // ka id hai; conversationId isi conversation ka id hai (jo
          // DoubtsScreen apna khud ka WS connect karne ke liye use karta
          // hai realtime doubt/upvote/answer updates ke liye).
          if (widget.conversation.isGroup)
            IconButton(
              icon: Icon(Icons.help_outline_rounded, color: cs.onPrimary),
              tooltip: _l10n.doubtsTab,
              onPressed: () {
                final group = widget.conversation.group;
                if (group == null) return;
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => DoubtsScreen(
                      groupId: group.id,
                      conversationId: widget.conversation.id,
                      groupName: group.name,
                    ),
                  ),
                );
              },
            ),
          IconButton(icon: Icon(Icons.call, color: cs.onPrimary), tooltip: _l10n.chatAudioCall, onPressed: () => _startCall('audio')),
          IconButton(icon: Icon(Icons.videocam, color: cs.onPrimary), tooltip: _l10n.chatVideoCall, onPressed: () => _startCall('video')),
          // 🔥 NAYA — 3-dot overflow menu: mute/unmute notification
          // (private chat ho ya group, dono ke liye kaam karta hai).
          PopupMenuButton<String>(
            icon: Icon(Icons.more_vert, color: cs.onPrimary),
            onSelected: (value) {
              if (value == 'toggle_mute') _toggleMuteNotifications();
              if (value == 'toggle_translate') _toggleTranslatePermission(); // 🔥 NAYA — Task 6
              if (value == 'toggle_listen') _toggleListenPermission(); // 🔥 NAYA — Task 7.1
              if (value == 'toggle_transcribe') _toggleTranscribePermission(); // 🔥 NAYA — Task 7.1
              if (value == 'filter') _showFilterSheet();
              if (value == 'wallpaper') _showWallpaperSheet();
              if (value == 'toggle_block') _toggleBlockUser();
              if (value == 'disappearing_messages') _showDisappearingMessagesSheet();
              if (value == 'group_info') _openGroupProfile();
              if (value == 'access_control') _showAccessControlSheet();
              if (value == 'change_group_photo') _changeGroupPhoto();
              if (value == 'join_requests') _showJoinRequestsSheet();
              if (value == 'leave_group') _leaveGroup();
              if (value == 'delete_group') _deleteGroup();
              if (value == 'scheduled_messages') _showManageScheduledSheet(); // 🔥 NAYA
            },
            itemBuilder: (context) => [
              PopupMenuItem<String>(
                value: 'toggle_mute',
                child: Row(children: [
                  Icon(
                    _isMuted ? Icons.notifications_active_outlined : Icons.notifications_off_outlined,
                    color: Theme.of(context).colorScheme.onSurface,
                    size: 20,
                  ),
                  const SizedBox(width: 10),
                  Text(_isMuted
                      ? (widget.conversation.isGroup ? _l10n.chatUnmuteGroup : _l10n.chatUnmuteNotifications)
                      : (widget.conversation.isGroup ? _l10n.chatMuteGroup : _l10n.chatMuteNotifications)),
                ]),
              ),
              // 🔥 NAYA — Task 6: Translate ab per-message inline button
              // nahi, ek on/off permission hai (poori app me ek hi
              // switch — per-device, SharedPreferences me store hota
              // hai). ON hone par hi `TranslateToggle` kisi bhi message
              // ke niche dikhega.
              PopupMenuItem<String>(
                value: 'toggle_translate',
                child: ValueListenableBuilder<bool>(
                  valueListenable: TranslateService.instance.translateEnabled,
                  builder: (context, translateOn, _) {
                    return Row(children: [
                      Icon(
                        Icons.translate,
                        color: translateOn ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurface,
                        size: 20,
                      ),
                      const SizedBox(width: 10),
                      Text(translateOn ? _l10n.chatDisableTranslate : _l10n.chatEnableTranslate),
                    ]);
                  },
                ),
              ),
              // 🔥 NAYA — Task 7.1: Listen + Transcribe toggles (default OFF)
              PopupMenuItem<String>(
                value: 'toggle_listen',
                child: ValueListenableBuilder<bool>(
                  valueListenable: TranslateService.instance.listenEnabled,
                  builder: (context, isOn, _) {
                    return Row(children: [
                      Icon(
                        Icons.volume_up_outlined,
                        color: isOn ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurface,
                        size: 20,
                      ),
                      const SizedBox(width: 10),
                      Text(isOn ? _l10n.chatDisableListen : _l10n.chatEnableListen),
                    ]);
                  },
                ),
              ),
              PopupMenuItem<String>(
                value: 'toggle_transcribe',
                child: ValueListenableBuilder<bool>(
                  valueListenable: TranslateService.instance.transcribeEnabled,
                  builder: (context, isOn, _) {
                    return Row(children: [
                      Icon(
                        Icons.subtitles_outlined,
                        color: isOn ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurface,
                        size: 20,
                      ),
                      const SizedBox(width: 10),
                      Text(isOn ? _l10n.chatDisableTranscribe : _l10n.chatEnableTranscribe),
                    ]);
                  },
                ),
              ),
              // 🔥 NAYA — Filter messages: Text / Media / Docs / Links
              PopupMenuItem<String>(
                value: 'filter',
                child: Row(children: [
                  Icon(Icons.filter_list, color: _chatFilter != 'all' ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurface, size: 20),
                  const SizedBox(width: 10),
                  Text(_chatFilter == 'all' ? _l10n.chatFilterTitle : _l10n.chatFilterActive(_filterLabel(_chatFilter))),
                ]),
              ),
              // 🔥 NAYA — poori chat screen ka wallpaper (WhatsApp jaisa),
              // sirf apne account ke liye — dusre participant/group
              // members ko nahi dikhega.
              PopupMenuItem<String>(
                value: 'wallpaper',
                child: Row(children: [
                  Icon(Icons.wallpaper, color: _wallpaperUrl != null ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurface, size: 20),
                  const SizedBox(width: 10),
                  Text(_wallpaperUrl != null ? _l10n.chatChangeWallpaper : _l10n.chatWallpaperMenu),
                ]),
              ),
              // 🔥 NAYA — apne scheduled (abhi bheje nahi gaye) messages
              // dekho / reschedule / cancel karo.
              PopupMenuItem<String>(
                value: 'scheduled_messages',
                child: Row(children: [
                  Icon(Icons.schedule_send_outlined, color: Theme.of(context).colorScheme.onSurface, size: 20),
                  const SizedBox(width: 10),
                  Text(_l10n.chatScheduledMessagesTitle),
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
                      color: _isBlocked ? Theme.of(context).colorScheme.onSurface : Theme.of(context).colorScheme.error,
                      size: 20,
                    ),
                    const SizedBox(width: 10),
                    Text(
                      _isBlocked ? _l10n.chatUnblockUser : _l10n.chatBlockUser,
                      style: TextStyle(color: _isBlocked ? Theme.of(context).colorScheme.onSurface : Theme.of(context).colorScheme.error),
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
                    color: _disappearingDuration != 'none' ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurface,
                    size: 20,
                  ),
                  const SizedBox(width: 10),
                  Text(_disappearingDuration == 'none'
                      ? _l10n.chatDisappearingTitle
                      : _l10n.chatDisappearingMenu(_disappearingLabel(_disappearingDuration))),
                ]),
              ),
              // 🔥 NAYA — Group info: members, roles (admin/moderator),
              // invite link, public/private, add/remove members — sab
              // ek jagah (sirf group chat me dikhta hai).
              if (widget.conversation.isGroup)
                PopupMenuItem<String>(
                  value: 'group_info',
                  child: Row(children: [
                    Icon(Icons.info_outline_rounded, color: Theme.of(context).colorScheme.onSurface, size: 20),
                    const SizedBox(width: 10),
                    Text(_l10n.chatGroupInfo),
                  ]),
                ),
              // 🔥 NAYA — ACCESS CONTROL: "kaun message bhej sakta hai" +
              // daily limit — sirf admin/moderator ko dikhta hai (backend
              // `IsGroupAdminOrModerator` bhi wahi enforce karta hai).
              if (widget.conversation.isGroup && _isGroupAdminOrMod)
                PopupMenuItem<String>(
                  value: 'access_control',
                  child: Row(children: [
                    Icon(
                      _groupMessagePermission == 'admins_mods' ? Icons.admin_panel_settings : Icons.groups_outlined,
                      color: _groupMessagePermission == 'admins_mods' ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurface,
                      size: 20,
                    ),
                    const SizedBox(width: 10),
                    Text(_l10n.chatPermissionsTitle),
                  ]),
                ),
              // 🔥 NAYA — Change group photo seedha yahin se, bina group
              // info screen khole (admin/moderator only).
              if (widget.conversation.isGroup && _isGroupAdminOrMod)
                PopupMenuItem<String>(
                  value: 'change_group_photo',
                  child: Row(children: [
                    Icon(Icons.add_a_photo_outlined, color: Theme.of(context).colorScheme.onSurface, size: 20),
                    const SizedBox(width: 10),
                    Text(_l10n.chatChangeGroupPhoto),
                  ]),
                ),
              // 🔥 NAYA — Pending join requests (private group, admin/mod
              // only) — badge count ke saath.
              if (widget.conversation.isGroup && _isGroupAdminOrMod)
                PopupMenuItem<String>(
                  value: 'join_requests',
                  child: Row(children: [
                    Icon(Icons.person_add_alt_1_outlined, color: Theme.of(context).colorScheme.onSurface, size: 20),
                    const SizedBox(width: 10),
                    Text(_pendingJoinRequestsCount > 0 ? _l10n.chatJoinRequestsCount(_pendingJoinRequestsCount) : _l10n.chatJoinRequestsTitle),
                  ]),
                ),
              // 🔥 NAYA — Leave group (sirf group chat me dikhta hai).
              if (widget.conversation.isGroup)
                PopupMenuItem<String>(
                  value: 'leave_group',
                  child: Row(children: [
                    Icon(Icons.exit_to_app, color: Colors.red, size: 20),
                    SizedBox(width: 10),
                    Text(_l10n.chatLeaveGroup, style: TextStyle(color: Colors.red)),
                  ]),
                ),
              // 🔥 NAYA — Delete group (ADMIN ONLY — moderator ko bhi
              // nahi dikhta, backend bhi strictly admin role hi allow
              // karta hai).
              if (widget.conversation.isGroup && _isGroupAdmin)
                PopupMenuItem<String>(
                  value: 'delete_group',
                  child: Row(children: [
                    Icon(Icons.delete_forever, color: Colors.red, size: 20),
                    SizedBox(width: 10),
                    Text(_l10n.chatDeleteGroup, style: TextStyle(color: Colors.red)),
                  ]),
                ),
            ],
          ),
          const SizedBox(width: 6),
        ],
      ),
      // 🔥 NAYA — poori chat screen ka background: agar user ne apna custom
      // wallpaper set kiya hai (`_wallpaperUrl`) to wahi image poori screen
      // pe (message bubbles ke peeche) dikhta hai — WhatsApp jaisa. Nahi to
      // default halka doodle-pattern (`_ChatWallpaperPainter`) dikhta hai.
      // `CachedNetworkImage` use kiya hai (raw Image nahi) taaki agar URL
      // expire/404 ho jaaye (broken link), to crash/blank screen ki jagah
      // wapas default pattern par gracefully fallback ho jaaye.
      body: Stack(children: [
        Positioned.fill(
          child: _wallpaperUrl != null
              ? CachedNetworkImage(
                  imageUrl: _wallpaperUrl!,
                  fit: BoxFit.cover,
                  placeholder: (_, __) => CustomPaint(painter: _ChatWallpaperPainter(color: cs.outlineVariant.withOpacity(0.6))),
                  errorWidget: (_, __, ___) => CustomPaint(painter: _ChatWallpaperPainter(color: cs.outlineVariant.withOpacity(0.6))),
                )
              : CustomPaint(painter: _ChatWallpaperPainter(color: cs.outlineVariant.withOpacity(0.6))),
        ),
        Column(children: [
          if (_vanishMode && _disappearingDuration == _kVanishDuration) _buildVanishBanner(), // 🔥 NAYA (M3a-FE)
          if (_pinnedMessages.isNotEmpty) _buildPinnedBanner(), // 🔥 NAYA
          Expanded(child: _buildMessageList()),
          _buildReplyPreview(),
          // 🔥 NAYA (Phase 3, §2.2) — @mention suggestion overlay, compose
          // box ke bilkul upar. Sirf group chat me aur jab `@query` active
          // ho tab dikhta hai; text field ke upar "floating card" jaisa.
          if (!_isBlocked && !_isPendingRequest && !_isMessagingRestrictedForMe && _mentionQuery != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: MentionSuggestionsOverlay(
                members: _groupMembers,
                query: _mentionQuery!,
                onSelected: _onMentionSelected,
              ),
            ),
          // 🔥 NAYA (Phase 3, §1 #11) — smart-reply suggestion chips, tap
          // karne se chip ka text seedha compose box me daal deta hai
          // (send NAHI hota — WhatsApp/Gmail jaisa hi, user chahe to edit
          // kar sakta hai bhejne se pehle).
          if (!_isBlocked && !_isPendingRequest && !_isMessagingRestrictedForMe && _smartReplies.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(left: 10, right: 10, bottom: 6),
              child: SizedBox(
                height: 34,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: _smartReplies.length,
                  separatorBuilder: (_, __) => const SizedBox(width: 8),
                  itemBuilder: (context, i) {
                    final suggestion = _smartReplies[i];
                    return ActionChip(
                      label: Text(suggestion, style: const TextStyle(fontSize: 12.5)),
                      backgroundColor: Theme.of(context).colorScheme.surface,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20), side: BorderSide(color: Theme.of(context).colorScheme.outlineVariant)),
                      onPressed: () {
                        _textController.text = suggestion;
                        _textController.selection = TextSelection.collapsed(offset: suggestion.length);
                        setState(() => _smartReplies = []);
                      },
                    );
                  },
                ),
              ),
            ),
          // 🔥 NAYA (M3a-FE) — bottom bar pe swipe-up = vanish mode toggle.
          // Gesture sirf neeche ke bar pe hai (message ListView pe nahi) taaki
          // chat scroll se conflict na ho. Blocked/pending/restricted me
          // vanish mode nahi — wahan kuch bhejna hi nahi hota.
          GestureDetector(
            behavior: HitTestBehavior.translucent,
            onVerticalDragEnd: (_isBlocked || _isPendingRequest || _isMessagingRestrictedForMe)
                ? null
                : (d) {
                    // 🔥 M4c — mic ke lock-swipe (upar) se vanish toggle na ho
                    if (_isRecording || DateTime.now().difference(_lastMicPointerAt) < const Duration(milliseconds: 900)) return;
                    if ((d.primaryVelocity ?? 0) < -400) _toggleVanishMode();
                  },
            child: _isBlocked
                ? _buildBlockedBanner()
                : (_isPendingRequest
                    ? _buildRequestBar() // 🔥 NAYA (M1-FE) — accept hone tak reply band
                    : (_isMessagingRestrictedForMe ? _buildRestrictedBanner() : _buildInputBar())),
          ),
        ]),
        // 🔥 NAYA — wallpaper upload chalte waqt chhota top banner
        if (_wallpaperUploading)
          Positioned(
            top: 10, left: 0, right: 0,
            child: Center(
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                decoration: BoxDecoration(color: Colors.black87, borderRadius: BorderRadius.circular(20)),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white)),
                  SizedBox(width: 10),
                  Text(_l10n.chatSettingWallpaper, style: TextStyle(color: Colors.white, fontSize: 12.5)),
                ]),
              ),
            ),
          ),
      ]),
    );
  }

  // NEW — replaces the normal AppBar while multi-select is active:
  // close (X) to cancel, live count, and a forward icon that opens the
  // conversation picker for every currently-checked message.
  PreferredSizeWidget _buildSelectionAppBar() {
    final cs = Theme.of(context).colorScheme;
    return AppBar(
      backgroundColor: cs.primary,
      elevation: 3,
      iconTheme: IconThemeData(color: cs.onPrimary),
      leading: IconButton(tooltip: 'Close', icon: const Icon(Icons.close), onPressed: _exitSelectionMode),
      title: Text(
        _l10n.chatSelectedCount(_selectedMessageIds.length),
        style: TextStyle(color: cs.onPrimary, fontSize: 16, fontWeight: FontWeight.w600),
      ),
      actions: [
        IconButton(
          icon: const Icon(Icons.forward),
          tooltip: _l10n.chatForward,
          onPressed: _selectedMessageIds.isEmpty ? null : _forwardSelected,
        ),
        const SizedBox(width: 4),
      ],
    );
  }

  // ------------------------------------------------------------------
  // 🔥 NAYA (M1-FE) — MESSAGE REQUEST bottom bar
  // ------------------------------------------------------------------

  // Request-list se na khuli ho (push tap, search...) to bhi server se
  // pending status dekh lo. Group me request hoti hi nahi. Error = normal chat.
  Future<void> _loadRequestStatus() async {
    if (widget.conversation.isGroup || widget.isMessageRequest) return;
    final status = await MessageApiService.getConversationRequestStatus(widget.conversation.id);
    if (mounted && status == 'pending') setState(() => _isPendingRequest = true);
  }

  Future<void> _acceptRequest() async {
    if (_requestBusy) return;
    setState(() => _requestBusy = true);
    try {
      await MessageApiService.acceptMessageRequest(widget.conversation.id);
      if (!mounted) return;
      // Input bar wapas aa jaata hai — ab reply de sakte ho.
      setState(() {
        _isPendingRequest = false;
        _requestBusy = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _requestBusy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Accept nahi ho paya: $e')));
    }
  }

  Future<void> _deleteRequest() async {
    if (_requestBusy) return;
    final otherName = widget.conversation.otherParticipant?.displayName ?? _l10n.chatThisUser;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Delete request?'),
        content: Text('Ye request hat jaayegi. $otherName ko iske baare me kuch nahi pata chalega.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(_l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text('Delete', style: TextStyle(color: Theme.of(ctx).colorScheme.error)),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _requestBusy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await MessageApiService.declineMessageRequest(widget.conversation.id);
      if (!mounted) return;
      Navigator.pop(context, 'declined');
      messenger.showSnackBar(const SnackBar(content: Text('Request delete ho gayi')));
    } catch (e) {
      if (!mounted) return;
      setState(() => _requestBusy = false);
      messenger.showSnackBar(SnackBar(content: Text('Delete nahi ho paya: $e')));
    }
  }

  Future<void> _blockRequest() async {
    if (_requestBusy) return;
    final otherId = widget.conversation.otherParticipant?.id;
    if (otherId == null || otherId.isEmpty) return;
    final otherName = widget.conversation.otherParticipant?.displayName ?? _l10n.chatThisUser;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text(_l10n.chatBlockTitle),
        content: Text(_l10n.chatBlockBody(otherName)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(_l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(_l10n.chatBlock, style: const TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _requestBusy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await MessageApiService.blockUser(otherId);
    } catch (e) {
      if (!mounted) return;
      setState(() => _requestBusy = false);
      messenger.showSnackBar(SnackBar(content: Text(_l10n.chatBlockFailed(e.toString()))));
      return;
    }
    // Block ho gaya — ab request ko bhi list se hata do. Ye fail ho jaye
    // (e.g. already declined) to bhi block ka kaam ho chuka, isliye ignore.
    try {
      await MessageApiService.declineMessageRequest(widget.conversation.id);
    } catch (_) {}
    if (!mounted) return;
    Navigator.pop(context, 'blocked');
    messenger.showSnackBar(SnackBar(content: Text(_l10n.chatUserBlocked)));
  }

  // Reply input band; sirf Accept / Delete / Block. Accept ke baad
  // `_isPendingRequest = false` hota hai aur normal `_buildInputBar()` aa jaata hai.
  Widget _buildRequestBar() {
    final cs = Theme.of(context).colorScheme;
    final otherName = widget.conversation.otherParticipant?.displayName ?? _l10n.chatThisUser;
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
        decoration: BoxDecoration(
          color: cs.surface,
          border: Border(top: BorderSide(color: cs.outlineVariant)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              '$otherName aapko message bhejna chahta hai. Accept karne par ye chat aapke inbox me aa jaayegi aur aap reply kar paoge.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant),
            ),
            const SizedBox(height: 10),
            Row(children: [
              Expanded(
                child: ElevatedButton(
                  onPressed: _requestBusy ? null : _acceptRequest,
                  style: ElevatedButton.styleFrom(backgroundColor: cs.primary, foregroundColor: cs.onPrimary),
                  child: _requestBusy
                      ? SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: cs.onPrimary))
                      : const Text('Accept'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton(
                  onPressed: _requestBusy ? null : _deleteRequest,
                  child: const Text('Delete'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton(
                  onPressed: _requestBusy ? null : _blockRequest,
                  style: OutlinedButton.styleFrom(
                    foregroundColor: cs.error,
                    side: BorderSide(color: cs.error.withOpacity(0.5)),
                  ),
                  child: const Text('Block'),
                ),
              ),
            ]),
          ],
        ),
      ),
    );
  }

  // 🔥 NAYA — jab humne is user ko block kiya hua hai, to normal input
  // bar ki jagah ye banner dikhta hai — na message bheja ja sakta hai,
  // na attachment/mic — sirf ek tap se seedha unblock karne ka option.
  Widget _buildBlockedBanner() {
    final cs = Theme.of(context).colorScheme;
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: cs.surface,
          border: Border(top: BorderSide(color: cs.outlineVariant)),
        ),
        child: Row(children: [
          Icon(Icons.block, color: cs.error, size: 18),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              _l10n.chatBlockedBanner,
              style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
            ),
          ),
          TextButton(onPressed: _toggleBlockUser, child: Text(_l10n.chatUnblock)),
        ]),
      ),
    );
  }

  // 🔥 NAYA — jab group ka `message_permission` "admins_mods" ho aur main
  // sirf ek normal member hoon, to composer ki jagah ye locked banner
  // dikhta hai — WhatsApp announcement-group jaisa. Real block backend pe
  // (`check_group_send_permission`) already hai, ye sirf UI-level clarity
  // hai taaki member confuse na ho ki uska message kyun nahi ja raha.
  Widget _buildRestrictedBanner() {
    final cs = Theme.of(context).colorScheme;
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        decoration: BoxDecoration(
          color: cs.surface,
          border: Border(top: BorderSide(color: cs.outlineVariant)),
        ),
        child: Row(children: [
          Icon(Icons.lock_outline, color: cs.onSurfaceVariant, size: 18),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              _l10n.chatAdminsOnlyBanner,
              style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
            ),
          ),
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
      decoration: BoxDecoration(color: Theme.of(context).colorScheme.surface, borderRadius: BorderRadius.circular(8), border: Border(left: BorderSide(color: Theme.of(context).colorScheme.primary, width: 4))),
      child: Row(children: [
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          Text(isMe ? _l10n.chatYou : (msg.sender?.displayName ?? ''), style: TextStyle(color: Theme.of(context).colorScheme.primary, fontWeight: FontWeight.bold, fontSize: 12.5)),
          Text(_replyPreviewText(msg), maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant, fontSize: 12.5)),
        ])),
        IconButton(tooltip: 'Close', icon: Icon(Icons.close, size: 18, color: Theme.of(context).colorScheme.onSurfaceVariant), onPressed: _cancelReply, padding: EdgeInsets.zero, constraints: const BoxConstraints()),
      ]),
    );
  }

  String _replyPreviewText(MessageModel msg) {
    switch (msg.type) {
      case MessageType.image: return _l10n.chatPreviewPhoto;
      case MessageType.video: return _l10n.chatPreviewVideo;
      case MessageType.audio: return _l10n.chatPreviewAudio;
      case MessageType.file: return _l10n.chatPreviewFile;
      case MessageType.presentation: return _l10n.chatPreviewPresentation;
      case MessageType.location: return _l10n.chatPreviewLocation;
      case MessageType.studyRoom: return _l10n.chatPreviewStudyRoom;
      case MessageType.poll: return _l10n.chatPreviewPoll; // 🔥 NAYA
      case MessageType.storyReply: return _l10n.chatPreviewStoryReply; // 🔥 NAYA
      default: return msg.text ?? '';
    }
  }

  Widget _buildMessageList() {
    if (_isLoading) return const Center(child: CircularProgressIndicator());
    // 🔥 POLISH — plain "Say hi" text ki jagah ab ek proper empty-state
    // card hai (icon + heading + subtext), baaki screens ke empty states
    // jaisa consistent look.
    if (_messages.isEmpty) {
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 84, height: 84,
            decoration: BoxDecoration(color: Theme.of(context).colorScheme.primary.withOpacity(0.06), shape: BoxShape.circle),
            child: Icon(Icons.waving_hand_rounded, size: 36, color: Theme.of(context).colorScheme.primary),
          ),
          const SizedBox(height: 14),
          Text(_l10n.chatSayHi, style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: Theme.of(context).colorScheme.onSurface)),
          const SizedBox(height: 4),
          Text(_l10n.chatSendToStart, style: TextStyle(fontSize: 12.5, color: Theme.of(context).colorScheme.onSurfaceVariant)),
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
                  _l10n.chatNoFilteredMessages(_filterLabel(_chatFilter).toLowerCase()),
                  style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant),
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
                        key: ValueKey('__loading_more_spinner__'), // 🔧 FIX — see message-row key note below
                        padding: EdgeInsets.symmetric(vertical: 14),
                        child: Center(
                          child: SizedBox(
                            width: 22,
                            height: 22,
                            child: CircularProgressIndicator(strokeWidth: 2.2),
                          ),
                        ),
                      );
                    }
                    final adjustedIndex = showLoadingMore ? index - 1 : index;
                    if (showTyping && adjustedIndex == filtered.length) {
                      return const _TypingBubble(key: ValueKey('__typing_bubble__')); // 🔥 NAYA — animated 3-dot bubble
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
                        onLongPress: (anchor) => _showMessageActions(msg, isMe, anchor: anchor),
                        onDoubleTap: () => _onDoubleTapReact(msg), // M5-FE
                        onFailedTap: () => _showFailedMessageSheet(msg), // M6-FE — failed bubble tap = Resend / Delete
                        onReactionTap: (emoji) => _toggleReaction(msg, emoji), // M5-FE — chip tap = toggle
                        myReaction: msg.myReaction(_myUserId ?? ''), // M5-FE — apna chip highlight
                        onDownload: () => _downloadMedia(context, msg),
                        onDownloadUrl: (url) => _downloadMediaUrl(context, msg, url), // 🔥 NAYA — multi-image grid ke ek specific photo ke liye
                        isUrlDownloaded: (url) => _downloadedIds.contains('${msg.id}::$url'), // 🔥 NAYA
                        onReplyTap: replyPreview != null ? () => _scrollToMessage(replyPreview.id, highlight: true) : null,
                        onJoinStudyRoom: _enterStudyRoom, // 🔥 NAYA — card pe tap = seedha room me entry, dobara invite nahi
                        onVotePoll: _votePoll, // 🔥 NAYA
                        // 🔥 NAYA (Phase 2, §7.3) — current user mentioned hai to highlight
                        isMentioned: _myUserId != null && msg.mentionedUsers.any((u) => u.id == _myUserId),
                        // 🔥 NAYA (Feature 11) — teacher/staff ka message, backend `is_announcement`
                        // flag se (MessageModel me field add karna hoga — message_models_patch.md).
                        isAnnouncement: msg.isAnnouncement,
                        // 🔥 NAYA (Phase 4, §2.1) — search-jump/reply-tap flash highlight
                        isJumpHighlighted: _highlightedMessageId == msg.id,
                      ),
                    );

                    return Column(
                      // 🔧 FIX (scroll jank on pagination) — pehle is row ka
                      // koi `key` nahi tha. Jab `_loadMoreMessages()` purane
                      // messages `_messages` list ke SHURU me insert karta
                      // hai (ya jab loading-spinner item show/hide hota hai),
                      // to har already-visible item ka LIST INDEX shift ho
                      // jaata hai. Bina key ke, Flutter naye/purane widgets ko
                      // POSITION se match karta hai — matlab jo bubble abhi
                      // dikh raha tha wahi Element ab ek DIFFERENT message ke
                      // liye reuse hota hai, poora subtree (image/video/audio
                      // player/poll) force-rebuild hota hai, chahe wo message
                      // khud change hi na hua ho. `msg.id` se stable key dene
                      // par Flutter Elements ko IDENTITY se match karta hai —
                      // sirf naye (abhi-load-hue) messages ke liye naye
                      // widgets banenge, baaki sab as-is reuse honge — load
                      // karte waqt visible jank/flicker khatam ho jaata hai.
                      key: ValueKey(msg.id),
                      children: [
                        if (showDateSeparator) _DateSeparator(date: msg.createdAt),
                        // NEW — during multi-select, tapping anywhere on the
                        // row toggles the checkbox instead of the message's
                        // normal tap behaviour (media viewer, link open,
                        // etc.), which is why the bubble itself is wrapped
                        // in AbsorbPointer while selection mode is active.
                        GestureDetector(
                          behavior: HitTestBehavior.translucent,
                          // 🔧 FIX (Phase 3, §4.3) — poll messages ko forward
                          // selection se exclude karo, checkbox tap disabled.
                          onTap: (_selectionMode && msg.type != MessageType.poll) ? () => _toggleMessageSelected(msg) : null,
                          child: Container(
                            color: isSelected ? Theme.of(context).colorScheme.primary.withOpacity(0.12) : null,
                            child: Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
                              if (_selectionMode)
                                Padding(
                                  padding: const EdgeInsets.only(left: 6, right: 2),
                                  child: msg.type == MessageType.poll
                                      ? Icon(Icons.block, size: 18, color: Theme.of(context).colorScheme.outlineVariant)
                                      : Icon(
                                          isSelected ? Icons.check_circle : Icons.radio_button_unchecked,
                                          size: 20,
                                          color: isSelected ? Theme.of(context).colorScheme.primary : Theme.of(context).colorScheme.onSurfaceVariant,
                                        ),
                                ),
                              Expanded(
                                child: AbsorbPointer(absorbing: _selectionMode, child: bubble),
                              ),
                            ]),
                          ),
                        ),
                      ],
                    );
                  },
                );
              }),
      ),
    ]);
  }

  // 🔥 NAYA — filter active hone par top pe ek chhota banner: kaunsa
  // filter laga hai + kitne messages mile + ek tap me clear karne ka option.
  Widget _buildFilterBanner(int count) {
    final tokens = AppThemeTokens.of(context);
    return Container(
      width: double.infinity,
      color: tokens.info.withOpacity(0.12),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      child: Row(children: [
        Icon(Icons.filter_list, size: 16, color: tokens.info),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            _l10n.chatFilterResultCount(_filterLabel(_chatFilter), count),
            style: TextStyle(color: tokens.info, fontSize: 12.5, fontWeight: FontWeight.w600),
          ),
        ),
        GestureDetector(
          onTap: () => setState(() => _chatFilter = 'all'),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
            child: Text(_l10n.clearButton, style: TextStyle(color: tokens.info, fontSize: 12.5, fontWeight: FontWeight.bold)),
          ),
        ),
      ]),
    );
  }

  bool _isSameDay(DateTime a, DateTime b) => a.year == b.year && a.month == b.month && a.day == b.day;

  // 🔥 NAYA — quoted reply pe tap karke us original message tak scroll/highlight
  // 🔥 NAYA (Phase 4, §2.1) — ab `highlight: true` dene par message ko
  // thodi der ke liye amber flash bhi karta hai (search-jump aur reply-tap
  // dono isi ek method ko reuse karte hain).
  void _scrollToMessage(String id, {bool highlight = false}) {
    final idx = _messages.indexWhere((m) => m.id == id);
    if (idx == -1 || !_scrollController.hasClients) return;
    // approx: har message ~70px, list top se offset nikaal ke scroll karo
    final approxOffset = (idx * 70.0).clamp(0.0, _scrollController.position.maxScrollExtent);
    _scrollController.animateTo(approxOffset, duration: const Duration(milliseconds: 300), curve: Curves.easeOut);
    if (highlight) {
      _highlightTimer?.cancel();
      setState(() => _highlightedMessageId = id);
      _highlightTimer = Timer(const Duration(milliseconds: 1300), () {
        if (mounted) setState(() => _highlightedMessageId = null);
      });
    }
  }

  String _fmtRecordDuration(Duration d) {
    final m = d.inMinutes.toString().padLeft(2, '0');
    final s = (d.inSeconds % 60).toString().padLeft(2, '0');
    return "$m:$s";
  }

  // 🔥 NAYA (M4c) — recording bar. Do mode:
  //  - HOLD (unlocked): timer + "slide to cancel" (slide ke saath fade) + mic circle
  //    jahan abhi finger hai; upar "lock" hint jo upar swipe ke saath bada hota hai.
  //  - LOCKED: delete | timer + status | pause/resume | send — haath hata sakte ho.
  Widget _buildRecordingBar(ColorScheme cs) {
    final locked = _recordLocked;
    final cancelProgress = (-_slideDx / _kCancelDistance).clamp(0.0, 1.0);
    final lockProgress = (-_slideDy / _kLockDistance).clamp(0.0, 1.0);
    final timer = Text(_fmtRecordDuration(_recordDuration), style: TextStyle(fontSize: 15, color: cs.onSurface, fontWeight: FontWeight.w600));
    final dot = Icon(Icons.fiber_manual_record, color: _recordPaused ? cs.onSurfaceVariant : cs.error, size: 14);

    final bar = Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(28),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.08), blurRadius: 10, offset: const Offset(0, 3))],
      ),
      child: locked
          ? Row(children: [
              IconButton(tooltip: 'Delete', icon: Icon(Icons.delete_outline, color: cs.error), onPressed: _cancelRecording),
              Expanded(child: Row(children: [
                dot,
                const SizedBox(width: 8),
                timer,
                const SizedBox(width: 8),
                // TODO(l10n): arb me `chatRecordingPaused` add karo
                Text(_recordPaused ? 'Paused' : _l10n.chatRecording, style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13)),
              ])),
              IconButton(
                tooltip: _recordPaused ? 'Resume recording' : 'Pause recording',
                icon: Icon(_recordPaused ? Icons.mic : Icons.pause_circle_outline, color: cs.primary),
                onPressed: _togglePauseRecording,
              ),
              const SizedBox(width: 4),
              Container(
                decoration: BoxDecoration(shape: BoxShape.circle, boxShadow: [BoxShadow(color: cs.primary.withOpacity(0.35), blurRadius: 8, offset: const Offset(0, 2))]),
                child: CircleAvatar(backgroundColor: cs.primary, child: IconButton(tooltip: 'Send', icon: Icon(Icons.send, color: cs.onPrimary), onPressed: _stopRecordingAndSend)),
              ),
            ])
          : Row(children: [
              dot,
              const SizedBox(width: 8),
              timer,
              Expanded(
                child: Opacity(
                  opacity: (1 - cancelProgress).clamp(0.0, 1.0),
                  child: Transform.translate(
                    offset: Offset(_slideDx * 0.5, 0),
                    child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                      Icon(Icons.chevron_left, size: 18, color: cs.onSurfaceVariant),
                      // TODO(l10n): arb me `chatSlideToCancel` add karo
                      Text('Slide to cancel', style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13)),
                    ]),
                  ),
                ),
              ),
              // mic circle: finger isi jagah (right) hai; slide ke saath thoda saath chalta hai
              Transform.translate(
                offset: Offset(_slideDx * 0.6, _slideDy * 0.4),
                child: CircleAvatar(
                  radius: 23 + 4 * lockProgress,
                  backgroundColor: cs.error,
                  child: Icon(Icons.mic, color: cs.onError),
                ),
              ),
            ]),
    );

    return SafeArea(child: Padding(
      padding: const EdgeInsets.fromLTRB(10, 6, 10, 10),
      child: Stack(clipBehavior: Clip.none, children: [
        bar,
        if (!locked)
          // lock hint: bar ke upar floating (layout nahi badalta); upar swipe ke saath gadha/bada
          Positioned(
            right: 8,
            top: -78,
            child: IgnorePointer(
              child: Opacity(
                opacity: 0.55 + 0.45 * lockProgress,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
                  decoration: BoxDecoration(
                    color: cs.surface,
                    borderRadius: BorderRadius.circular(22),
                    boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.12), blurRadius: 8)],
                  ),
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Icon(Icons.lock_outline, size: 18 + 4 * lockProgress, color: cs.onSurface),
                    const SizedBox(height: 2),
                    Icon(Icons.keyboard_arrow_up, size: 18, color: cs.onSurfaceVariant),
                  ]),
                ),
              ),
            ),
          ),
      ]),
    ));
  }

  // 🔥 M4c — stable `Listener` ancestor: mic gesture (move/up/cancel) yahin track
  // hota hai, chahe neeche ka bar normal <-> recording UI me badal jaaye.
  Widget _buildInputBar() {
    return Listener(
      onPointerMove: _onMicMove,
      onPointerUp: _onMicUp,
      onPointerCancel: _onMicCancel,
      child: _buildInputBarContent(),
    );
  }

  Widget _buildInputBarContent() {
    final cs = Theme.of(context).colorScheme;
    // 🔥 NAYA — recording chal rahi ho to poora bar ek "Slide to cancel"
    // jaisa recording indicator ban jaata hai (WhatsApp jaisa).
    if (_isRecording) {
      return _buildRecordingBar(cs);
    }
    // 🔥 NAYA — attach + emoji + text field ab ek hi floating white "card"
    // ke andar hain (subtle shadow, fully rounded) — flat/dated bar ki
    // jagah modern messaging-app jaisa look. Send/mic button bahar,
    // apna elevated circle, taaki primary action visually stand-out kare.
    return SafeArea(child: Padding(padding: const EdgeInsets.fromLTRB(10, 6, 10, 10), child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
      Expanded(
        child: Container(
          decoration: BoxDecoration(
            color: cs.surface,
            borderRadius: BorderRadius.circular(26),
            boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 8, offset: const Offset(0, 2))],
          ),
          child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
            IconButton(tooltip: 'Attach file', icon: Icon(Icons.attach_file, color: cs.primary), onPressed: _showAttachmentSheet),
            // 🔥 NAYA — apna sticker picker (assets/stickers/). Tap karte hi
            // chosen sticker seedha ek image message ki tarah bhej diya jaata
            // hai (WhatsApp jaisa — koi text nahi banta).
            IconButton(tooltip: 'Emoji', 
              icon: Icon(Icons.emoji_emotions_outlined, color: cs.primary),
              onPressed: () => showStickerPicker(
                context,
                onSelected: (assetPath) => _sendSticker(assetPath),
              ),
            ),
            Expanded(child: TextField(controller: _textController, onChanged: _onTypingChanged, minLines: 1, maxLines: 4, style: TextStyle(fontSize: 14.5, color: cs.onSurface), decoration: InputDecoration(hintText: _l10n.chatMessageHint, hintStyle: TextStyle(color: cs.onSurfaceVariant), filled: false, contentPadding: const EdgeInsets.symmetric(horizontal: 4, vertical: 12), border: InputBorder.none))),
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
            decoration: BoxDecoration(shape: BoxShape.circle, boxShadow: [BoxShadow(color: cs.primary.withOpacity(0.32), blurRadius: 8, offset: const Offset(0, 2))]),
            child: CircleAvatar(
              radius: 23,
              backgroundColor: cs.primary,
              child: hasText
                  ? IconButton(tooltip: 'Send', icon: Icon(Icons.send, color: cs.onPrimary), onPressed: _sendMessage)
                  // 🔥 M4c — mic: hold = record (upar swipe = lock, left swipe = cancel,
                  // haath hatao = send); tap = locked recording. Move/up `_buildInputBar` ka Listener sambhalta hai.
                  : Listener(
                      behavior: HitTestBehavior.opaque,
                      onPointerDown: _onMicDown,
                      child: SizedBox(width: 48, height: 48, child: Icon(Icons.mic, color: cs.onPrimary)),
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
            child: Padding(padding: const EdgeInsets.symmetric(horizontal: 6), child: Icon(Icons.reply, color: Theme.of(context).colorScheme.onSurfaceVariant, size: 20)),
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

  String _label(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final now = DateTime.now();
    final d = date.toLocal();
    final today = DateTime(now.year, now.month, now.day);
    final that = DateTime(d.year, d.month, d.day);
    final diff = today.difference(that).inDays;
    if (diff == 0) return l10n.chatToday;
    if (diff == 1) return l10n.chatYesterday;
    // 🌐 LANGUAGE FIX — month names were a hardcoded English list ("Jan".."Dec");
    // now formatted with the app's active locale (Hindi month names in hi).
    return DateFormat('d MMM y', Localizations.localeOf(context).toString()).format(d);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Center(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
          decoration: BoxDecoration(color: cs.surface, borderRadius: BorderRadius.circular(8), boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 2)]),
          child: Text(_label(context), style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant, fontWeight: FontWeight.w500)),
        ),
      ),
    );
  }
}

// 🔥 NAYA — animated 3-dot "typing..." bubble jaisa WhatsApp/Messenger me hota hai
class _TypingBubble extends StatefulWidget {
  const _TypingBubble({super.key});
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
    final cs = Theme.of(context).colorScheme;
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(top: 4, bottom: 4),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(color: cs.surface, borderRadius: BorderRadius.circular(14), boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.07), blurRadius: 6, offset: const Offset(0, 2))]),
        child: AnimatedBuilder(
          animation: _controller,
          builder: (_, __) => Row(mainAxisSize: MainAxisSize.min, children: List.generate(3, (i) {
            final t = ((_controller.value - i * 0.2) % 1.0);
            final scale = t < 0.5 ? 0.6 + t : 1.1 - t;
            return Padding(padding: const EdgeInsets.symmetric(horizontal: 2), child: Transform.scale(scale: scale.clamp(0.6, 1.0), child: Container(width: 7, height: 7, decoration: BoxDecoration(color: cs.onSurfaceVariant.withOpacity(0.6), shape: BoxShape.circle))));
          })),
        ),
      ),
    );
  }
}

// 🔥 NAYA — halka repeating doodle pattern, flat color se zyada "app jaisa" feel
class _ChatWallpaperPainter extends CustomPainter {
  final Color color;
  const _ChatWallpaperPainter({required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..color = color..style = PaintingStyle.fill;
    const step = 44.0;
    for (double y = 0; y < size.height; y += step) {
      for (double x = 0; x < size.width; x += step) {
        final offsetX = (y ~/ step) % 2 == 0 ? 0.0 : step / 2;
        canvas.drawCircle(Offset(x + offsetX + 6, y + 6), 1.6, paint);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _ChatWallpaperPainter oldDelegate) => oldDelegate.color != color;
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
  final ValueChanged<Rect?> onLongPress; // M5-FE — bubble ka global rect (strip anchor ke liye)
  final VoidCallback? onDoubleTap; // M5-FE — double-tap = ❤
  final VoidCallback? onFailedTap; // M6-FE — failed message tap = Resend / Delete
  final ValueChanged<String> onReactionTap; // M5-FE — chip tap = toggle us emoji ko
  final String? myReaction; // M5-FE — current user ka reaction (chip highlight)
  final VoidCallback onDownload;
  final void Function(String url)? onDownloadUrl; // 🔥 NAYA — multi-image message me ek specific photo download karne ke liye
  final bool Function(String url)? isUrlDownloaded; // 🔥 NAYA
  final VoidCallback? onReplyTap; // 🔥 NAYA
  final VoidCallback? onJoinStudyRoom; // 🔥 NAYA — study room invite card ke "Tap to Join" ke liye
  final void Function(MessageModel msg, List<String> optionIds)? onVotePoll; // 🔥 NAYA — poll option tap
  // 🔥 NAYA (Phase 2, §7.3/§4.1) — true jab current user
  // `message.mentionedUsers` me ho, taaki bubble WhatsApp jaisa subtle
  // highlight kare.
  final bool isMentioned;
  // 🔥 NAYA (Phase 4, §2.1) — "search se jump karke aaya" ya "reply-tap se
  // scroll hua" message ko ek pal ke liye flash-highlight karne ke liye.
  // One-shot fade-out animation (TweenAnimationBuilder) — jaise hi parent
  // ye flag reset karega (ek chhoti Timer ke baad), bubble apni normal
  // background pe wapas aa jaayega.
  final bool isJumpHighlighted;
  // 🔥 NAYA (Feature 11) — teacher/staff message: alag border/badge/tint,
  // taaki "important" lane feel bubble level pe bhi carry ho, sirf chat
  // list me hi nahi. `isMentioned` jaisa hi pattern follow kiya hai.
  final bool isAnnouncement;
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
    this.onDoubleTap,
    this.onFailedTap,
    required this.onReactionTap,
    this.myReaction,
    required this.onDownload,
    this.onDownloadUrl,
    this.isUrlDownloaded,
    this.onReplyTap,
    this.onJoinStudyRoom,
    this.onVotePoll,
    this.isMentioned = false,
    this.isJumpHighlighted = false,
    this.isAnnouncement = false,
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

    final cs = Theme.of(context).colorScheme;
    // 🎨 THEME FIX — pehle bubbleColor/textColor/timeColor hardcoded the
    // (dark-navy sent bubble + Colors.white receive bubble hamesha, kisi
    // bhi theme mode me same) — dark mode me received bubble literally
    // safed hi rehta, ink bhi kabhi nahi badalta. Ab dono theme se aate
    // hain, home.dart jaisa hi.
    final bubbleColor = isMe ? cs.primary : cs.surface;
    final textColor = isMe ? cs.onPrimary : cs.onSurface;
    final timeColor = isMe ? cs.onPrimary.withOpacity(0.7) : cs.onSurfaceVariant;

    // 🔥 NAYA — bubble tail: last-in-group bubble ka ek corner chhota
    // (~4px) rehta hai, jaisa WhatsApp me "pointer" hota hai.
    final radius = BorderRadius.only(
      topLeft: const Radius.circular(14),
      topRight: const Radius.circular(14),
      bottomLeft: Radius.circular(isMe || !isLastInGroup ? 14 : 3),
      bottomRight: Radius.circular(isMe && isLastInGroup ? 3 : 14),
    );

    // 🔥 NAYA (Phase 4, §2.1) — search-jump / reply-tap flash highlight:
    // one-shot tween se amber se wapas normal bubble-color pe fade hota
    // hai. `isJumpHighlighted=false` ho to bilkul normal (koi extra
    // rebuild/cost nahi) — tween sirf tab chalta hai jab parent ye flag
    // thodi der ke liye true karta hai.
    final Color highlightStart = AppThemeTokens.of(context).warning.withOpacity(0.55);

    return GestureDetector(
      onLongPress: message.deletedForEveryone ? null : () => _handleLongPress(context),
      onTap: message.sendFailed ? onFailedTap : null, // M6-FE
      onDoubleTap: (message.deletedForEveryone || message.isSending) ? null : onDoubleTap, // M6-FE — unsent pe react nahi
      child: Align(
        alignment: isMe ? Alignment.centerRight : Alignment.centerLeft,
        child: Column(crossAxisAlignment: isMe ? CrossAxisAlignment.end : CrossAxisAlignment.start, children: [
          TweenAnimationBuilder<Color?>(
            tween: ColorTween(
              begin: isJumpHighlighted ? highlightStart : bubbleColor,
              end: bubbleColor,
            ),
            duration: const Duration(milliseconds: 1200),
            curve: Curves.easeOut,
            builder: (context, animatedColor, child) => Container(
            margin: EdgeInsets.only(top: isFirstInGroup ? 6 : 2),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
            constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.75),
            decoration: BoxDecoration(
              color: animatedColor ?? bubbleColor,
              borderRadius: radius,
              // 🔥 NAYA (Phase 2, §7.3) — @mention highlight: agar current
              // user is message me mentioned hai, subtle amber border +
              // thoda alag shadow (WhatsApp jaisa "you were mentioned" look).
              // 🔥 NAYA (Feature 11) — announcement border sabse zyada priority
              // (teacher ka message hai to mention-highlight se bhi zyada
              // noticeable hona chahiye), phir mention, phir normal.
              border: isAnnouncement
                  ? Border.all(color: AppThemeTokens.of(context).warning, width: 1.6)
                  : (isMentioned ? Border.all(color: AppThemeTokens.of(context).warning.withOpacity(0.75), width: 1.4) : null),
              boxShadow: [
                BoxShadow(
                  color: (isAnnouncement || isMentioned ? AppThemeTokens.of(context).warning : Colors.black)
                      .withOpacity(isAnnouncement ? 0.22 : (isMentioned ? 0.18 : 0.07)),
                  blurRadius: 6,
                  offset: const Offset(0, 2),
                ),
              ],
            ),
            child: Stack(children: [
              Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
              // 🔥 NAYA (Feature 11) — "📢 Announcement" chip, group-sender
              // naam se bhi upar, taaki chit-chat se ek nazar me alag lage.
              if (isAnnouncement)
                Padding(
                  padding: const EdgeInsets.only(left: 4, bottom: 3),
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Icon(Icons.campaign_rounded, size: 13, color: AppThemeTokens.of(context).warning),
                    const SizedBox(width: 4),
                    Text(AppLocalizations.of(context)!.chatAnnouncement, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: AppThemeTokens.of(context).warning)),
                  ]),
                ),
              // 🔥 NAYA — group chat me, apne khud ke message ko chhod ke,
              // har naye sender-block ke pehle bubble ke upar naam dikhao
              // (WhatsApp jaisa) — taaki pata chale kisne msg bheja.
              if (isGroup && !isMe && isFirstInGroup)
                Padding(
                  padding: const EdgeInsets.only(left: 4, bottom: 2),
                  child: Text(
                    message.sender?.displayName ?? AppLocalizations.of(context)!.chatUnknown,
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
                  if (message.isEdited) Text("${AppLocalizations.of(context)!.edited} ", style: TextStyle(fontSize: 10, color: timeColor)),
                  Text("${message.createdAt.hour.toString().padLeft(2, '0')}:${message.createdAt.minute.toString().padLeft(2, '0')}", style: TextStyle(fontSize: 10.5, color: timeColor)),
                  if (isMe) ...[const SizedBox(width: 3), _buildTick(context)],
                ]),
              ),
            ]),
            ]),
          ),
          ),
          if (message.reactions.isNotEmpty) _buildReactionRow(context),
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
      onLongPress: message.deletedForEveryone ? null : () => _handleLongPress(context),
      onTap: message.sendFailed ? onFailedTap : null, // M6-FE
      onDoubleTap: (message.deletedForEveryone || message.isSending) ? null : onDoubleTap, // M6-FE — unsent pe react nahi
      child: Align(
        alignment: isMe ? Alignment.centerRight : Alignment.centerLeft,
        child: Column(crossAxisAlignment: isMe ? CrossAxisAlignment.end : CrossAxisAlignment.start, children: [
          if (isGroup && !isMe && isFirstInGroup)
            Padding(
              padding: const EdgeInsets.only(left: 4, bottom: 2),
              child: Text(
                message.sender?.displayName ?? AppLocalizations.of(context)!.chatUnknown,
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
                if (_inFlight)
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
                      if (isMe) ...[const SizedBox(width: 3), _buildTick(context)],
                    ]),
                  ),
                ),
              ]),
            ),
          ),
          if (message.reactions.isNotEmpty) _buildReactionRow(context),
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
  Widget _buildTick(BuildContext context) {
    if (message.sendFailed) return const Icon(Icons.error, size: 15, color: Colors.redAccent); // M6-FE — laal "!"
    final onBubble = Theme.of(context).colorScheme.onPrimary.withOpacity(0.7);
    if (message.isSending) return Icon(Icons.access_time, size: 12, color: onBubble);
    if (isReadByOther) return const Icon(Icons.done_all, size: 15, color: Color(0xFF34B7F1)); // blue double tick — WhatsApp-jaisa universal "read" indicator, jaan-boojh kar fixed
    return Icon(Icons.done, size: 14, color: onBubble); // single grey tick = sent
  }

  // 🔥 NAYA — reply karte hue jis message ko quote kiya, uska preview
  Widget _buildReplyQuote(BuildContext context, Color textColor) {
    final r = replyPreview!;
    String preview;
    switch (r.type) {
      case MessageType.image: preview = AppLocalizations.of(context)!.chatPreviewPhoto; break;
      case MessageType.video: preview = AppLocalizations.of(context)!.chatPreviewVideo; break;
      case MessageType.audio: preview = AppLocalizations.of(context)!.chatPreviewAudio; break;
      case MessageType.file: preview = AppLocalizations.of(context)!.chatPreviewFile; break;
      case MessageType.presentation: preview = AppLocalizations.of(context)!.chatPreviewPresentation; break;
      case MessageType.location: preview = AppLocalizations.of(context)!.chatPreviewLocation; break;
      case MessageType.studyRoom: preview = AppLocalizations.of(context)!.chatPreviewStudyRoom; break;
      case MessageType.poll: preview = AppLocalizations.of(context)!.chatPreviewPoll; break; // 🔥 NAYA
      case MessageType.storyReply: preview = AppLocalizations.of(context)!.chatPreviewStoryReply; break; // 🔥 NAYA
      default: preview = r.text ?? '';
    }
    return GestureDetector(
      onTap: onReplyTap,
      child: Container(
        margin: const EdgeInsets.only(bottom: 5),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        // 🎨 THEME FIX — pehle `textColor == Colors.white` se sent/received
        // decide hota tha, jo naye theme-driven textColor ke saath kabhi
        // match nahi karega (ab wo literal Colors.white nahi, cs.onPrimary
        // hai). Seedha `isMe` field use kar rahe hain — zyada robust.
        decoration: BoxDecoration(
          color: isMe ? Theme.of(context).colorScheme.onPrimary.withOpacity(0.15) : Theme.of(context).colorScheme.onSurface.withOpacity(0.06),
          borderRadius: BorderRadius.circular(6),
          border: Border(left: BorderSide(color: Theme.of(context).colorScheme.primary, width: 3)),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          Text(r.sender?.displayName ?? '', style: TextStyle(color: Theme.of(context).colorScheme.primary, fontWeight: FontWeight.bold, fontSize: 11.5)),
          Text(preview, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: textColor.withOpacity(0.75), fontSize: 12)),
        ]),
      ),
    );
  }

  // M6-FE — sending (spinner) = isSending && !sendFailed. Failed me local preview rehta hai, spinner nahi.
  bool get _inFlight => message.isSending && !message.sendFailed;

  // M5-FE — long-press par bubble ka global rect nikalke parent ko do.
  void _handleLongPress(BuildContext context) {
    final box = context.findRenderObject();
    Rect? rect;
    if (box is RenderBox && box.hasSize) {
      rect = box.localToGlobal(Offset.zero) & box.size;
    }
    onLongPress(rect);
  }

  // M5-FE — bubble ke neeche per-emoji chips (emoji + count). Chip tap =
  // us emoji ko toggle. Apna reaction primary-tint se highlight.
  Widget _buildReactionRow(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final counts = <String, int>{};
    for (final r in message.reactions) {
      counts[r.emoji] = (counts[r.emoji] ?? 0) + 1;
    }
    return Padding(
      padding: const EdgeInsets.only(top: 2),
      child: Wrap(
        spacing: 4,
        runSpacing: 4,
        alignment: isMe ? WrapAlignment.end : WrapAlignment.start,
        children: counts.entries.map((e) {
          final mine = e.key == myReaction;
          return GestureDetector(
            onTap: () => onReactionTap(e.key),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(
                color: mine ? cs.primary.withOpacity(0.14) : cs.surface,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: mine ? cs.primary : cs.outlineVariant, width: mine ? 1.2 : 0.8),
                boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 3)],
              ),
              child: Text('${e.key} ${e.value}', style: TextStyle(fontSize: 12, color: cs.onSurface)),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildContent(BuildContext context, Color textColor) {
    if (message.deletedForEveryone) return Text(AppLocalizations.of(context)!.chatMessageDeleted, style: TextStyle(color: Colors.grey, fontStyle: FontStyle.italic, fontSize: 14.5));

    Widget media;
    switch (message.type) {
      case MessageType.image: media = _imageContent(context, textColor); break;
      case MessageType.video: media = _videoContent(context, textColor); break;
      case MessageType.audio: return _AudioBubble(message: message, textColor: textColor, onDownload: onDownload);
      case MessageType.presentation: media = _fileLikeContent(context, textColor, Icons.slideshow, AppLocalizations.of(context)!.chatPresentation); break;
      case MessageType.file: media = _fileLikeContent(context, textColor, Icons.insert_drive_file, AppLocalizations.of(context)!.chatFile); break;
      case MessageType.location: return _locationContent(context, textColor);
      case MessageType.studyRoom: return _studyRoomCard(context); // 🔥 NAYA
      case MessageType.poll: return _pollContent(context, textColor); // 🔥 NAYA
      case MessageType.storyReply: return _storyReplyContent(context, textColor); // 🔥 NAYA
      // 🔥 NAYA — plain text ab _LinkifiedText se render hota hai, taaki
      // agar message me koi URL (http/https/www.) ho to wo clickable
      // link ki tarah dikhe (blue + underline) aur tap karne par khul
      // jaaye. URL na ho to ye bilkul normal Text jaisa hi behave karta hai.
      // 🔥 NAYA (Phase 2, §7.5) — agar backend ne is text me se URL ke
      // liye link-preview generate kar di hai (`message.linkPreview`,
      // `meta['link_preview']` se derive hota hai — turant ho sakta hai
      // ya thodi der baad `meta_update` event se live aaye), text ke
      // neeche ek preview card bhi dikhao.
      default:
        // 🔥 NAYA — Features 9/10 (Translate + Listen) ab yahan wire ho
        // gaye hain. Dono widgets (`translatable_message_widgets.dart`)
        // pehle se bane the lekin kahin call nahi ho rahe the — is chat
        // bubble ke text render path me hi missing piece the, in par koi
        // dependency nahi thi.
        final txt = message.text ?? '';
        // Task 7.1 — Listen/Translate dono off ho to row bilkul nahi
        // dikhti (extra padding bhi nahi). Dono widgets khud bhi apne
        // notifier se gated hain.
        final actionRow = txt.trim().isEmpty
            ? null
            : AnimatedBuilder(
                animation: Listenable.merge([
                  TranslateService.instance.listenEnabled,
                  TranslateService.instance.translateEnabled,
                ]),
                builder: (context, _) {
                  if (!TranslateService.instance.listenEnabled.value &&
                      !TranslateService.instance.translateEnabled.value) {
                    return const SizedBox.shrink();
                  }
                  return Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        ListenButton(messageId: message.id, text: txt),
                        const SizedBox(width: 4),
                        TranslateToggle(messageId: message.id, text: txt),
                      ],
                    ),
                  );
                },
              );
        if (message.linkPreview != null) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              _LinkifiedText(text: message.text ?? '', color: textColor),
              const SizedBox(height: 6),
              _LinkPreviewCard(preview: message.linkPreview!, textColor: textColor),
              if (actionRow != null) actionRow,
            ],
          );
        }
        if (actionRow == null) return _LinkifiedText(text: message.text ?? '', color: textColor);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            _LinkifiedText(text: message.text ?? '', color: textColor),
            actionRow,
          ],
        );
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
              children: [
                Icon(Icons.school, color: Colors.white, size: 22),
                SizedBox(width: 8),
                Expanded(
                  child: Text(
                    AppLocalizations.of(context)!.chatStudyRoom,
                    style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              AppLocalizations.of(context)!.chatStudyRoomCardSubtitle,
              style: TextStyle(color: Colors.white70, fontSize: 12),
            ),
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 8),
              decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(20)),
              alignment: Alignment.center,
              child: Text(
                AppLocalizations.of(context)!.chatTapToJoin,
                style: TextStyle(color: Color(0xFF4E54C8), fontWeight: FontWeight.bold, fontSize: 13),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // 🔥 NAYA — STORY REPLY bubble. Instagram-style: small square story
  // thumbnail (from `message.storyReply.snapshotUrl` — survives the story
  // itself expiring, see StoryReplyInfo's doc comment) + the reply text
  // underneath, in a bordered "quoted" card rather than a plain bubble so
  // it visually reads as "this message is about that story", same idea
  // `_buildReplyPreview` already uses for quoted replies elsewhere in
  // this file.
  Widget _storyReplyContent(BuildContext context, Color textColor) {
    final story = message.storyReply;
    final snapshot = story?.snapshotUrl;
    final l10n = AppLocalizations.of(context)!;
    return Container(
      width: 220,
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: (isMe ? Theme.of(context).colorScheme.onPrimary : Theme.of(context).colorScheme.onSurface)
            .withOpacity(0.06),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Icon(Icons.reply_rounded, size: 14, color: textColor.withOpacity(0.6)),
              const SizedBox(width: 4),
              Text(
                l10n.chatRepliedToYourStory,
                style: TextStyle(fontSize: 11.5, color: textColor.withOpacity(0.6), fontWeight: FontWeight.w600),
              ),
            ],
          ),
          const SizedBox(height: 6),
          GestureDetector(
            onTap: snapshot == null
                ? null
                : () => Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => MediaViewerScreen(urls: [snapshot], initialIndex: 0, onDownload: (_) => onDownload()),
                      ),
                    ),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: snapshot != null
                  ? CachedNetworkImage(
                      imageUrl: snapshot,
                      width: 70,
                      height: 90,
                      fit: BoxFit.cover,
                      placeholder: (_, __) => const SizedBox(
                        width: 70,
                        height: 90,
                        child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
                      ),
                      errorWidget: (_, __, ___) =>
                          const SizedBox(width: 70, height: 90, child: Icon(Icons.broken_image, color: Colors.grey)),
                    )
                  : Container(
                      width: 70,
                      height: 90,
                      color: textColor.withOpacity(0.08),
                      child: Icon(Icons.image_not_supported_outlined, color: textColor.withOpacity(0.4)),
                    ),
            ),
          ),
          if ((message.text ?? '').trim().isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(message.text!.trim(), style: TextStyle(color: textColor, fontSize: 14.5)),
          ],
        ],
      ),
    );
  }

  // 🔥 NAYA — POLL bubble
  // 🔧 FIX (Phase 1 model fix) — `message.poll` ab top-level field hai
  // (§3), `message.meta?['poll']` nahi. Purana poll (jo history se scroll
  // karke load hua) abhi bhi `poll == null` ho sakta hai agar backend
  // list/detail response me poll data nahi bhejta — wahi read-only
  // fallback neeche as-is rakha hai.
  Widget _pollContent(BuildContext context, Color textColor) {
    final poll = message.poll;
    if (poll == null) {
      // Purana poll, jiska poora data history API me nahi aata (dekho
      // model file ka note) — question-only, read-only fallback.
      return Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(Icons.poll_outlined, color: textColor, size: 18),
        const SizedBox(width: 6),
        Flexible(child: Text(message.text ?? AppLocalizations.of(context)!.pollLabel, style: TextStyle(color: textColor, fontWeight: FontWeight.w600))),
      ]);
    }
    return _PollBubbleContent(
      poll: poll,
      textColor: textColor,
      onVote: (optionIds) => onVotePoll?.call(message, optionIds),
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
        if (_inFlight) Container(width: 200, height: 200, color: Colors.black26, child: Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
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
          if (_inFlight)
            Positioned.fill(
              child: Container(
                color: Colors.black26,
                child: Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
                  CircularProgressIndicator(color: Colors.white, value: (message.uploadProgress != null && message.uploadProgress! > 0) ? message.uploadProgress : null),
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text(
                      message.uploadProgress != null && message.uploadProgress! > 0
                          ? AppLocalizations.of(context)!.chatUploadingPhotosPercent((message.uploadProgress! * 100).round(), count)
                          : AppLocalizations.of(context)!.chatPhotosCount(count),
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
        Container(padding: const EdgeInsets.all(8), decoration: BoxDecoration(color: textColor.withOpacity(0.18), shape: BoxShape.circle), child: _inFlight ? SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: textColor, value: (message.uploadProgress != null && message.uploadProgress! > 0) ? message.uploadProgress : null)) : Icon(icon, color: textColor)),
        const SizedBox(width: 8),
        Flexible(child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          Text(fileName, style: TextStyle(color: textColor, fontSize: 14), overflow: TextOverflow.ellipsis),
          // 🔥 NAYA: sending ke dauraan "42% uploading..." dikhega
          if (_inFlight && message.uploadProgress != null && message.uploadProgress! > 0)
            Text(AppLocalizations.of(context)!.chatUploadingPercent((message.uploadProgress! * 100).round()), style: TextStyle(color: textColor.withOpacity(0.7), fontSize: 11)),
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
    if (_inFlight) {
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
    return GestureDetector(onTap: (lat != null && lng != null) ? () => launchUrl(Uri.parse("https://maps.google.com/?q=$lat,$lng"), mode: LaunchMode.externalApplication) : null, child: Row(mainAxisSize: MainAxisSize.min, children: [Icon(Icons.location_on, color: textColor), const SizedBox(width: 6), Text(AppLocalizations.of(context)!.chatLocationShared, style: TextStyle(color: textColor, fontSize: 14))]));
  }
}

// ============================================================
// 🔥 NAYA — POLL BUBBLE (options + live vote bars + tap to vote)
// ============================================================
class _PollBubbleContent extends StatefulWidget {
  final PollModel poll;
  final Color textColor;
  final void Function(List<String> optionIds) onVote;

  const _PollBubbleContent({required this.poll, required this.textColor, required this.onVote});

  @override
  State<_PollBubbleContent> createState() => _PollBubbleContentState();
}

class _PollBubbleContentState extends State<_PollBubbleContent> with _L10nCache<_PollBubbleContent> {
  final Set<String> _selected = {};

  @override
  void initState() {
    super.initState();
    _selected.addAll(widget.poll.options.where((o) => o.votedByMe).map((o) => o.id));
  }

  @override
  void didUpdateWidget(covariant _PollBubbleContent oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 🔥 NAYA — dusra live poll update (socket `poll_update`) aane par
    // apna hi selection state bhi fresh server data se resync karo.
    if (oldWidget.poll.id != widget.poll.id || oldWidget.poll.totalVotes != widget.poll.totalVotes) {
      _selected
        ..clear()
        ..addAll(widget.poll.options.where((o) => o.votedByMe).map((o) => o.id));
    }
  }

  @override
  Widget build(BuildContext context) {
    final poll = widget.poll;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
      Row(children: [
        Icon(Icons.poll, size: 16, color: widget.textColor),
        const SizedBox(width: 6),
        Flexible(child: Text(poll.question, style: TextStyle(color: widget.textColor, fontWeight: FontWeight.bold, fontSize: 14))),
      ]),
      const SizedBox(height: 6),
      ...poll.options.map((opt) {
        final selected = _selected.contains(opt.id);
        final pct = poll.totalVotes == 0 ? 0.0 : opt.voteCount / poll.totalVotes;
        return GestureDetector(
          onTap: poll.isClosed ? null : () {
            setState(() {
              if (poll.allowsMultipleAnswers) {
                selected ? _selected.remove(opt.id) : _selected.add(opt.id);
              } else {
                _selected
                  ..clear()
                  ..add(opt.id);
              }
            });
            widget.onVote(_selected.toList());
          },
          child: Container(
            margin: const EdgeInsets.only(bottom: 5),
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
            decoration: BoxDecoration(
              border: Border.all(color: widget.textColor.withOpacity(0.25)),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Stack(children: [
              if (poll.totalVotes > 0)
                Positioned.fill(
                  child: FractionallySizedBox(
                    alignment: Alignment.centerLeft,
                    widthFactor: pct.clamp(0.0, 1.0),
                    child: Container(
                      decoration: BoxDecoration(
                        color: widget.textColor.withOpacity(0.12),
                        borderRadius: BorderRadius.circular(8),
                      ),
                    ),
                  ),
                ),
              Row(children: [
                Icon(selected ? Icons.check_circle : Icons.circle_outlined, size: 16, color: widget.textColor),
                const SizedBox(width: 6),
                Expanded(child: Text(opt.text, style: TextStyle(color: widget.textColor, fontSize: 13))),
                if (!poll.isAnonymous || poll.totalVotes > 0)
                  Text("${opt.voteCount}", style: TextStyle(color: widget.textColor.withOpacity(0.7), fontSize: 12)),
              ]),
            ]),
          ),
        );
      }),
      Text(
        poll.isClosed ? _l10n.chatPollVotesClosed(poll.totalVotes) : _l10n.chatPollVotes(poll.totalVotes),
        style: TextStyle(color: widget.textColor.withOpacity(0.6), fontSize: 11),
      ),
    ]);
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

class _MediaPreviewScreenState extends State<_MediaPreviewScreen> with _L10nCache<_MediaPreviewScreen> {
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
              IconButton(tooltip: 'Close', icon: const Icon(Icons.close, color: Colors.white), onPressed: () => Navigator.pop(context)),
              const Spacer(),
              Text(
                _l10n.chatItemsCount(_files.length),
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
                    decoration: InputDecoration(hintText: _l10n.addCaptionHint, hintStyle: TextStyle(color: Colors.white38), border: InputBorder.none),
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
// 🔥 NAYA (M4a) — recording ke amplitude samples (0..1) ko `bars` (default 40)
// buckets me downsample karta hai (har bucket ka peak), phir loudest bar ko 100
// maan ke scale karta hai taaki dheemi recording bhi shape dikhaye. Har bar
// minimum 6 (chhoti si line, silence bhi dikhe). Khali input -> khali list.
List<int> _buildWaveform(List<double> samples, {int bars = 40}) {
  if (samples.isEmpty) return const [];
  final out = <double>[];
  for (var i = 0; i < bars; i++) {
    final from = (i * samples.length / bars).floor();
    var to = ((i + 1) * samples.length / bars).ceil();
    if (to <= from) to = from + 1;
    if (from >= samples.length) { out.add(out.isEmpty ? 0 : out.last); continue; }
    var peak = 0.0;
    for (var j = from; j < to && j < samples.length; j++) {
      if (samples[j] > peak) peak = samples[j];
    }
    out.add(peak);
  }
  final maxV = out.reduce((a, b) => a > b ? a : b);
  if (maxV <= 0) return List<int>.filled(bars, 6);
  return out.map((v) => ((v / maxV) * 100).round().clamp(6, 100)).toList();
}

// 🔥 NAYA (M4a) — voice-note waveform: played bars `color`, baaki halka.
// `bars == null` => purana voice note, flat (barabar height) bars.
class _VoiceWaveform extends StatelessWidget {
  final List<int>? bars;
  final double progress; // 0..1
  final Color color;
  final bool enabled;
  final ValueChanged<double> onSeek;
  const _VoiceWaveform({required this.bars, required this.progress, required this.color, required this.enabled, required this.onSeek});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, c) {
      void seekAt(double dx) {
        if (!enabled || c.maxWidth <= 0) return;
        onSeek((dx / c.maxWidth).clamp(0.0, 1.0));
      }
      return GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: (d) => seekAt(d.localPosition.dx),
        onHorizontalDragUpdate: (d) => seekAt(d.localPosition.dx),
        child: SizedBox(
          height: 30,
          width: double.infinity,
          child: CustomPaint(painter: _WaveformPainter(bars: bars, progress: progress, color: color)),
        ),
      );
    });
  }
}

class _WaveformPainter extends CustomPainter {
  final List<int>? bars;
  final double progress;
  final Color color;
  _WaveformPainter({required this.bars, required this.progress, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final values = (bars == null || bars!.isEmpty) ? List<int>.filled(40, 20) : bars!;
    final n = values.length;
    final slot = size.width / n;
    final barW = (slot * 0.6).clamp(1.5, 4.0);
    final played = Paint()..color = color;
    final rest = Paint()..color = color.withOpacity(0.35);
    for (var i = 0; i < n; i++) {
      final h = (values[i] / 100 * size.height).clamp(3.0, size.height);
      final x = i * slot + (slot - barW) / 2;
      final rect = RRect.fromRectAndRadius(
        Rect.fromLTWH(x, (size.height - h) / 2, barW, h),
        Radius.circular(barW / 2),
      );
      canvas.drawRRect(rect, (i + 0.5) / n <= progress ? played : rest);
    }
  }

  @override
  bool shouldRepaint(_WaveformPainter old) =>
      old.progress != progress || old.color != color || old.bars != bars;
}

class _AudioBubble extends StatefulWidget {
  final MessageModel message;
  final Color textColor;
  final VoidCallback onDownload;
  const _AudioBubble({required this.message, required this.textColor, required this.onDownload});

  @override
  State<_AudioBubble> createState() => _AudioBubbleState();
}

class _AudioBubbleState extends State<_AudioBubble> with _L10nCache<_AudioBubble> {
  final AudioPlayer _player = AudioPlayer();
  PlayerState _state = PlayerState.stopped;
  Duration _position = Duration.zero;
  Duration _duration = Duration.zero;
  bool _loading = false;
  bool _transcribing = false; // 🔥 NAYA — manual "Transcribe" tap ke liye

  // 🔥 NAYA (M4b) — playback speed 1x -> 1.5x -> 2x -> 1x. `static` hai taaki
  // choice app session me saare voice-note bubbles me yaad rahe (naya bubble
  // bhi last chuni speed se shuru hota hai); app restart pe 1x pe reset.
  static double _sessionRate = 1.0;
  static const List<double> _kRates = [1.0, 1.5, 2.0];
  double _rate = _sessionRate;

  String _rateLabel(double r) => r == r.roundToDouble() ? '${r.toInt()}x' : '${r}x';

  Future<void> _cycleRate() async {
    final next = _kRates[(_kRates.indexOf(_rate) + 1) % _kRates.length];
    setState(() { _rate = next; _sessionRate = next; });
    try { await _player.setPlaybackRate(next); } catch (_) {}
  }

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
      // 🔥 M4b — session speed (play ke baad set karna sab platforms pe reliable hai)
      if (_rate != 1.0) await _player.setPlaybackRate(_rate);
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(_l10n.chatAudioPlayFailed(e.toString()))));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _fmt(Duration d) {
    final m = d.inMinutes.toString().padLeft(1, '0');
    final s = (d.inSeconds % 60).toString().padLeft(2, '0');
    return "$m:$s";
  }

  // 🔥 M4a — meta['waveform'] (List<num>) -> 0..100 ints; nahi/invalid ho to null (flat fallback)
  List<int>? _waveformOf(MessageModel m) {
    final raw = m.meta?['waveform'];
    if (raw is! List || raw.isEmpty) return null;
    return raw.map((e) => e is num ? e.toInt().clamp(0, 100) : 0).toList();
  }

  // 🔥 NAYA — manual fallback transcribe (auto-transcription §7.6 already
  // hoti hai backend me, ye sirf tab kaam aata hai jab wo kisi wajah se
  // nahi aaya — purana voice note, ya us waqt AI_ENABLED false tha).
  // `widget.message` ka `meta` seedha mutate karte hain (same object jo
  // parent `_messages` list me hai) taaki `meta_update` WS event jaisa
  // hi behave ho — koi extra callback/state-lifting nahi chahiye.
  Future<void> _transcribe() async {
    final msg = widget.message;
    final url = msg.fileUrl;
    if (url == null || url.isEmpty || _transcribing) return;
    setState(() => _transcribing = true);
    try {
      final transcript = await AiStudyService.transcribe(fileUrl: url);
      if (!mounted) return;
      setState(() {
        msg.meta = {...?msg.meta, 'transcript': transcript};
      });
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(_l10n.chatTranscribeFailed(e.toString()))),
        );
      }
    } finally {
      if (mounted) setState(() => _transcribing = false);
    }
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
    if (msg.isSending && !msg.sendFailed) {
      return Row(mainAxisSize: MainAxisSize.min, children: [
        SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: textColor, value: (msg.uploadProgress != null && msg.uploadProgress! > 0) ? msg.uploadProgress : null)),
        const SizedBox(width: 8),
        Text(msg.uploadProgress != null && msg.uploadProgress! > 0 ? _l10n.chatUploadingPercent((msg.uploadProgress! * 100).round()) : _l10n.chatSendingAudio, style: TextStyle(color: textColor, fontSize: 13)),
      ]);
    }
    final url = msg.fileUrl;
    final total = _duration.inMilliseconds > 0 ? _duration : Duration(seconds: (msg.meta?['duration_seconds'] as num?)?.toInt() ?? 0);
    final progress = total.inMilliseconds > 0 ? (_position.inMilliseconds / total.inMilliseconds).clamp(0.0, 1.0) : 0.0;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox(
          width: 220,
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            GestureDetector(
              onTap: (url != null && url.isNotEmpty) ? _togglePlay : null,
              onLongPress: (url != null && url.isNotEmpty) ? widget.onDownload : null,
              child: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(color: textColor.withOpacity(0.18), shape: BoxShape.circle),
                child: _loading
                    ? SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: textColor))
                    : Icon(_state == PlayerState.playing ? Icons.pause : Icons.play_arrow, color: textColor),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                // 🔥 NAYA (M4a) — waveform bars + progress colour. Purane voice notes
                // (meta me waveform nahi) ke liye flat bars fallback. Tap/drag se seek.
                _VoiceWaveform(
                  bars: _waveformOf(msg),
                  progress: progress,
                  color: textColor,
                  enabled: !(url == null || url.isEmpty || total.inMilliseconds == 0),
                  onSeek: (f) => _player.seek(Duration(milliseconds: (f * total.inMilliseconds).round())),
                ),
                Padding(
                  padding: const EdgeInsets.only(left: 4),
                  child: Row(children: [
                    Expanded(
                      child: Text(
                        total.inMilliseconds > 0 ? "${_fmt(_position)} / ${_fmt(total)}" : _l10n.chatAudioMessage,
                        style: TextStyle(color: textColor.withOpacity(0.8), fontSize: 11),
                      ),
                    ),
                    // 🔥 NAYA (M4b) — speed chip: tap karke 1x -> 1.5x -> 2x
                    if (url != null && url.isNotEmpty)
                      GestureDetector(
                        onTap: _cycleRate,
                        behavior: HitTestBehavior.opaque,
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                          decoration: BoxDecoration(
                            color: textColor.withOpacity(_rate == 1.0 ? 0.14 : 0.28),
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: Text(_rateLabel(_rate), style: TextStyle(color: textColor, fontSize: 11, fontWeight: FontWeight.w700)),
                        ),
                      ),
                  ]),
                ),
              ]),
            ),
          ]),
        ),
        // 🔥 NAYA (Phase 2, §7.6) — auto voice transcription. Backend
        // ab transcript background me generate karke `meta['transcript']`
        // me daal deta hai (`_onMetaUpdateEvent` se live update hota hai
        // agar screen already open hai). Jab tak transcript nahi aaya,
        // manual "Transcribe" button dikhta hai (fallback — POST
        // `/message/ai/transcribe/` via `AiStudyService.transcribe`,
        // §17.4) taaki purane voice notes ya auto-transcription miss
        // hone ki soorat me bhi user transcript pa sake.
        // 🔥 Task 7.1 — transcript + manual Transcribe button ab 3-dot
        // menu ke "transcribe" toggle ke peeche hain (default OFF).
        ValueListenableBuilder<bool>(
          valueListenable: TranslateService.instance.transcribeEnabled,
          builder: (context, transcribeOn, _) {
            if (!transcribeOn) return const SizedBox.shrink();
            if (msg.transcript != null && msg.transcript!.trim().isNotEmpty) {
              return Padding(
                padding: const EdgeInsets.only(top: 4),
                child: _TranscriptText(text: msg.transcript!.trim(), textColor: textColor),
              );
            }
            if (url != null && url.isNotEmpty && !msg.isSending) {
              return Padding(
            padding: const EdgeInsets.only(top: 4, left: 4),
            child: GestureDetector(
              onTap: _transcribing ? null : _transcribe,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (_transcribing)
                    SizedBox(
                      width: 12,
                      height: 12,
                      child: CircularProgressIndicator(strokeWidth: 1.5, color: textColor.withOpacity(0.7)),
                    )
                  else
                    Icon(Icons.subtitles_outlined, size: 13, color: textColor.withOpacity(0.7)),
                  const SizedBox(width: 4),
                  Text(
                    _transcribing ? _l10n.chatTranscribing : _l10n.chatTranscribe,
                    style: TextStyle(
                      color: textColor.withOpacity(0.7),
                      fontSize: 11,
                      fontWeight: FontWeight.w600,
                      decoration: _transcribing ? TextDecoration.none : TextDecoration.underline,
                    ),
                  ),
                ],
              ),
            ),
          );
            }
            return const SizedBox.shrink();
          },
        ),
      ],
    );
  }
}

// 🔥 NAYA (Phase 2, §7.6) — collapsible "Transcript" text neeche audio
// bubble ke, WhatsApp/Telegram jaisa. Chhoti transcript ek line me hi
// dikh jaati hai; lambi ho to "View transcript" tap karke poori khulti hai.
class _TranscriptText extends StatefulWidget {
  final String text;
  final Color textColor;
  const _TranscriptText({required this.text, required this.textColor});

  @override
  State<_TranscriptText> createState() => _TranscriptTextState();
}

class _TranscriptTextState extends State<_TranscriptText> with _L10nCache<_TranscriptText> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final color = widget.textColor;
    return GestureDetector(
      onTap: () => setState(() => _expanded = !_expanded),
      child: Container(
        constraints: const BoxConstraints(maxWidth: 220),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        decoration: BoxDecoration(
          color: color.withOpacity(0.1),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(children: [
              Icon(Icons.subtitles_outlined, size: 13, color: color.withOpacity(0.7)),
              const SizedBox(width: 4),
              Text(_l10n.chatTranscript, style: TextStyle(color: color.withOpacity(0.7), fontSize: 10.5, fontWeight: FontWeight.w600)),
            ]),
            const SizedBox(height: 2),
            Text(
              widget.text,
              maxLines: _expanded ? null : 2,
              overflow: _expanded ? TextOverflow.visible : TextOverflow.ellipsis,
              style: TextStyle(color: color.withOpacity(0.85), fontSize: 12),
            ),
          ],
        ),
      ),
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

class _VideoPlayerScreenState extends State<_VideoPlayerScreen> with _L10nCache<_VideoPlayerScreen> {
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
        setState(() => _error = _l10n.chatVideoLoadFailed(e.toString()));
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
                                  IconButton(tooltip: 'Back', icon: const Icon(Icons.arrow_back, color: Colors.white), onPressed: () => Navigator.pop(context)),
                                  const Spacer(),
                                  IconButton(tooltip: _isLandscape ? 'Lock portrait' : 'Rotate screen', 
                                    icon: Icon(_isLandscape ? Icons.screen_lock_portrait : Icons.screen_rotation, color: Colors.white),
                                    onPressed: _toggleOrientation,
                                  ),
                                ]),
                              ),
                              const Spacer(),
                              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                                IconButton(tooltip: 'Back 10 seconds', iconSize: 32, icon: const Icon(Icons.replay_10, color: Colors.white), onPressed: () => _seekBy(-10)),
                                const SizedBox(width: 26),
                                Container(
                                  decoration: const BoxDecoration(color: Colors.white24, shape: BoxShape.circle),
                                  child: IconButton(tooltip: _controller.value.isPlaying ? 'Pause' : 'Play', 
                                    iconSize: 42,
                                    icon: Icon(_controller.value.isPlaying ? Icons.pause : Icons.play_arrow, color: Colors.white),
                                    onPressed: _togglePlay,
                                  ),
                                ),
                                const SizedBox(width: 26),
                                IconButton(tooltip: 'Forward 10 seconds', iconSize: 32, icon: const Icon(Icons.forward_10, color: Colors.white), onPressed: () => _seekBy(10)),
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

// ============================================================
// M5-FE — long-press overlay: emoji strip (bubble ke upar) + menu sheet (neeche)
// ============================================================

enum _OverlaySlot { strip, menu }

class _ReactionMenuOverlay extends StatelessWidget {
  final Rect anchor;
  final bool isMe;
  final String? myReaction;
  final List<Widget> menuTiles;
  final ValueChanged<String> onPick;
  final VoidCallback onMore;

  const _ReactionMenuOverlay({
    required this.anchor,
    required this.isMe,
    required this.myReaction,
    required this.menuTiles,
    required this.onPick,
    required this.onMore,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return CustomMultiChildLayout(
      delegate: _OverlayLayoutDelegate(anchor: anchor, isMe: isMe, topInset: MediaQuery.of(context).padding.top),
      children: [
        LayoutId(
          id: _OverlaySlot.menu,
          child: Material(
            color: cs.surface,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(18)),
            clipBehavior: Clip.antiAlias,
            child: SafeArea(
              top: false,
              child: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min, children: menuTiles)),
            ),
          ),
        ),
        LayoutId(
          id: _OverlaySlot.strip,
          child: Material(
            color: cs.surface,
            elevation: 6,
            borderRadius: BorderRadius.circular(28),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 5),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                for (final emoji in _kEmojis)
                  GestureDetector(
                    onTap: () => onPick(emoji),
                    child: Container(
                      padding: const EdgeInsets.all(5),
                      decoration: BoxDecoration(
                        color: emoji == myReaction ? cs.primary.withOpacity(0.15) : null,
                        shape: BoxShape.circle,
                      ),
                      child: Text(emoji, style: const TextStyle(fontSize: 26)),
                    ),
                  ),
                GestureDetector(
                  onTap: onMore,
                  child: Container(
                    margin: const EdgeInsets.only(left: 2),
                    width: 34,
                    height: 34,
                    decoration: BoxDecoration(color: cs.onSurface.withOpacity(0.08), shape: BoxShape.circle),
                    child: Icon(Icons.add, size: 20, color: cs.onSurface),
                  ),
                ),
              ]),
            ),
          ),
        ),
      ],
    );
  }
}

class _OverlayLayoutDelegate extends MultiChildLayoutDelegate {
  final Rect anchor;
  final bool isMe;
  final double topInset;
  _OverlayLayoutDelegate({required this.anchor, required this.isMe, required this.topInset});

  @override
  void performLayout(Size size) {
    // 1) menu sheet neeche, max 55% height
    final menuSize = layoutChild(
      _OverlaySlot.menu,
      BoxConstraints(minWidth: size.width, maxWidth: size.width, maxHeight: size.height * 0.55),
    );
    final menuTop = size.height - menuSize.height;
    positionChild(_OverlaySlot.menu, Offset(0, menuTop));

    // 2) strip bubble ke upar; screen ke top aur menu ke top ke beech clamp
    final stripSize = layoutChild(_OverlaySlot.strip, BoxConstraints.loose(Size(size.width - 16, 60)));
    final minY = topInset + 6;
    final maxY = (menuTop - stripSize.height - 6).clamp(minY, double.infinity).toDouble();
    final y = (anchor.top - stripSize.height - 6).clamp(minY, maxY).toDouble();
    final x = isMe ? size.width - stripSize.width - 12 : 12.0;
    positionChild(_OverlaySlot.strip, Offset(x, y));
  }

  @override
  bool shouldRelayout(_OverlayLayoutDelegate old) =>
      old.anchor != anchor || old.isMe != isMe || old.topInset != topInset;
}
