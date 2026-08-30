import 'dart:developer' as developer;
import 'dart:io';
import 'dart:async';
import 'dart:math' as math; // 🔥 NAYA — sticker bubbles ki random horizontal position ke liye
import 'dart:typed_data';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:file_selector/file_selector.dart';
import 'package:image_picker/image_picker.dart'; // 🔥 NAYA — camera se seedha photo capture karne ke liye
import 'package:path_provider/path_provider.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:printing/printing.dart'; // 🔥 NAYA — PDF ke har page ko image me rasterize karke multi-page split karne ke liye (pubspec.yaml: printing: ^5.12.0)
import 'package:pdf/pdf.dart'; // 🔥 NAYA — "Download All Pages (PDF)" ke liye PdfPageFormat
import 'package:pdf/widgets.dart' as pw; // 🔥 NAYA — "Download All Pages (PDF)" ke liye naya multi-page PDF document banane ke liye (pubspec.yaml: pdf: ^3.10.7 — printing package ke saath already transitively aata hai, lekin direct use karne ke liye seedha dependency me bhi add kar lena)
import 'package:livekit_client/livekit_client.dart';
import 'package:uuid/uuid.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:http/http.dart' as http;
// 🔥 NAYA — study room khula rehte tak phone ki screen auto-lock/sleep na
// ho (jaise Meet/Zoom me hota hai). pubspec.yaml me add karna hoga:
//   wakelock_plus: ^1.2.10
import 'package:wakelock_plus/wakelock_plus.dart';

import '../models/study_room_models.dart';
import '../services/chat_socket_service.dart';
import '../services/call_api_service.dart';
import '../services/call_manager.dart';
import '../services/study_room_call_manager.dart';
import '../services/message_api_service.dart';
import '../services/ai_study_service.dart';
import '../services/push_notification_service.dart'; // 🔥 NAYA — file downloads (annotated snapshot/all-pages PDF/original file) complete hone par bhi chat_screen jaisa hi system notification dikhane ke liye
import '../widgets/whiteboard_painter.dart';

// ============================================================
// TEMPORARY IN-ROOM CHAT MESSAGE
// ------------------------------------------------------------
// Ye study room ki "quick chat" ke liye hai — Google Meet/Zoom ki
// in-call chat jaisi: sirf is session me jab tak room khula hai
// tab tak zinda rehti hai. Ye asli 1:1/group conversation (jo
// MessageApiService/MessageModel se backend me save hoti hai) se
// bilkul alag hai — na to backend se load hoti hai, na wahan save
// hoti hai. Room band hote hi (ya screen se bahar jaate hi) ye
// messages hamesha ke liye gayab ho jaate hain.
// ============================================================
// ============================================================
// GENERIC ACTION HISTORY (for the "Undo Last Action" button)
// ------------------------------------------------------------
// Pehle sirf freehand strokes undo ho paate the. Ab jo bhi last
// action user ne kiya ho — line/shape draw ki ho, text likha ho, ya
// sticky note lagayi ho — wo bhi ek hi "Undo" button se reverse ho
// jaata hai. Sirf khud ke actions undo hote hain (jaisa pehle bhi
// stroke undo sirf apna hota tha), doosre participants ke kaam se
// chhed-chhaad nahi hoti.
// ============================================================
enum _BoardActionType { stroke, shape, text, stickyNote }

class _BoardAction {
  final _BoardActionType type;
  final String pageId;
  // shape/text/stickyNote ke liye unka unique id (id-based removal —
  // safe hai chahe beech me kuch aur add/remove ho jaye). Stroke ke
  // liye zaroorat nahi, wo already apne alag index-stack se undo hota h.
  final String? refId;

  _BoardAction({required this.type, required this.pageId, this.refId});
}

class _RoomChatMessage {
  final String senderId;
  final String senderName;
  final String text;
  final DateTime sentAt;

  _RoomChatMessage({
    required this.senderId,
    required this.senderName,
    required this.text,
    required this.sentAt,
  });

  Map<String, dynamic> toJson() => {
        'senderId': senderId,
        'senderName': senderName,
        'text': text,
        'sentAt': sentAt.toIso8601String(),
      };

  factory _RoomChatMessage.fromJson(Map<String, dynamic> json) {
    return _RoomChatMessage(
      senderId: json['senderId']?.toString() ?? '',
      senderName: json['senderName']?.toString() ?? 'Participant',
      text: json['text']?.toString() ?? '',
      sentAt: DateTime.tryParse(json['sentAt']?.toString() ?? '') ?? DateTime.now(),
    );
  }
}

// ============================================================
// STICKER REACTIONS
// ------------------------------------------------------------
// Google Meet/Zoom jaisa quick reaction burst: koi bhi sticker tap
// karke bhejo, wo screen ke bottom se float hoke upar jaata hai aur
// ~3 second me fade-out ho jaata hai — sender aur baaki sab dono ko
// dikhta hai. Quick-chat jaisa hi temporary hai: kahin persist/save
// nahi hota, bas is live moment ke liye hai.
// ============================================================
class _StickerEvent {
  final String id;
  final String emoji;
  final String senderId;
  final String senderName;
  // 0.0–1.0 ke beech random horizontal position — ek hi baar
  // generate hoti hai (creation ke time) taaki rebuild pe udhar-idhar
  // "jump" na kare, aur ek saath bheje gaye multiple stickers
  // ek-doosre ke upar overlap na karein.
  final double dx;

  _StickerEvent({
    required this.id,
    required this.emoji,
    required this.senderId,
    required this.senderName,
    required this.dx,
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'emoji': emoji,
        'senderId': senderId,
        'senderName': senderName,
      };

  factory _StickerEvent.fromJson(Map<String, dynamic> json) {
    return _StickerEvent(
      id: json['id']?.toString() ?? UniqueKey().toString(),
      emoji: json['emoji']?.toString() ?? '👍',
      senderId: json['senderId']?.toString() ?? '',
      senderName: json['senderName']?.toString() ?? 'Participant',
      // Har device apna khud ka random dx generate karta hai — isse
      // network me extra field nahi bhejna padta aur position phir
      // bhi natural random dikhti hai.
      dx: math.Random().nextDouble(),
    );
  }
}

class StudyRoomScreen extends StatefulWidget {
  final String conversationId;
  final String currentUserId;
  final List<UserProfileWindowModel> initialParticipants;
  final Room? livekitRoom;
  // Jis user/group ke saath ye conversation hai uska naam/photo — call
  // button aur AppBar title ke liye.
  final String? peerName;
  final String? peerAvatar;
  // 🔥 NAYA — khud (current user) ka naam/photo. Isse study room khud
  // apna video/avatar window bana sakta hai, chahe caller
  // `initialParticipants` me self ko shaamil na kare.
  final String? currentUserName;
  final String? currentUserAvatar;
  // 🔥 NAYA — true jab chat_screen ke "Study Room" icon se FRESH session
  // start ki jaa rahi ho (na ki kisi purane invite-card/pehle se chal rahi
  // session me JOIN ki jaa rahi ho). true hone par purana whiteboard state
  // restore NAHI hota — bilkul khaali page se shuru hota hai, jaise koi
  // naya Google Meet link banaya ho. Invite-card tap flow (`_enterStudyRoom`
  // bina is flag ke) isko false hi rakhta hai, taaki jo already-chal-rahi
  // session me tap karke aaye use purana board/state mile, naya nahi.
  final bool startNewSession;

  const StudyRoomScreen({
    super.key,
    required this.conversationId,
    required this.currentUserId,
    required this.initialParticipants,
    this.livekitRoom,
    this.peerName,
    this.peerAvatar,
    this.currentUserName,
    this.currentUserAvatar,
    this.startNewSession = false,
  });

  @override
  State<StudyRoomScreen> createState() => _StudyRoomScreenState();
}

class _StudyRoomScreenState extends State<StudyRoomScreen> {
  // 🔥 NAYA — screen-share ke liye ek fixed, well-known page id. Presenter
  // aur baaki sab isi id se ek hi page identify karte hain (existing
  // `add_page`/`WhiteboardPage` sync mechanism dobara use hota hai —
  // koi naya sync engine nahi likhna pada).
  static const String _presentationPageId = 'presentation';

  final GlobalKey _globalKey = GlobalKey();
  final GlobalKey<ScaffoldState> _scaffoldKey = GlobalKey<ScaffoldState>();
  final ChatSocketService _socket = ChatSocketService();
  final TransformationController _transformCtrl = TransformationController();

  // ============================================================
  // MULTI-PAGE WHITEBOARD STATE
  // ============================================================
  List<WhiteboardPage> _pages = [WhiteboardPage(id: 'page_1')];
  int _currentPageIndex = 0;
  WhiteboardPage get _page => _pages[_currentPageIndex];
  // 🔥 NAYA — jo pages abhi banaye/receive hue hain lekin user ne khud
  // navigate karke dekhe nahi hain (PDF-split ke extra pages, ya koi
  // doosra participant jo naya page add kare) — inpe blue "unread" dot
  // dikhta hai, jab tak user tab pe tap na kare.
  final Set<String> _unvisitedPageIds = {};

  // Drawing Config State
  ToolType _activeTool = ToolType.marker;
  Color _selectedColor = Colors.black;
  double _strokeWidth = 3.0;
  List<DrawingPoint> _currentStroke = [];

  // Shape drag-in-progress
  Offset? _shapeDragStart;
  Offset? _shapeDragCurrent;

  // Zoom / Pan
  bool _panZoomMode = false;
  int _activePointers = 0;

  // Media & Sticky Notes State
  // ------------------------------------------------------------
  // 🔥 FIX — pehle _loadedPdfPath/_loadedImagePath screen-level single
  // fields the, isliye ek page pe load ki gayi file *sabhi* pages pe
  // dikhti thi. Ab har page ka apna alag media path maintained hai
  // (page id -> path), aur _loadedPdfPath/_loadedImagePath sirf
  // "currently visible page" ke liye getters hain — baaki sab code
  // (build, download, export) bina badlaav ke chalta rehta hai.
  // ------------------------------------------------------------
  final Map<String, String> _pagePdfPaths = {};
  final Map<String, String> _pageImagePaths = {};

  // 🔥 NAYA — RULED PAPER LINES
  // ------------------------------------------------------------
  // Notebook/copy jaisi ruled lines background — sirf un pages ke liye
  // jinme koi PDF/image load nahi hai (khaali whiteboard page). Har page
  // apna alag on/off state rakhta hai (jaise `_pagePdfPaths` etc. bhi page
  // id se keyed hain), aur doosre participants ko bhi turant dikhe isliye
  // socket se sync hota hai ('ruled_lines' room event — neeche
  // `_toggleRuledLines`/`_setRuledLineStyle` aur socket switch case dono
  // me hai). NOTE: ye sirf is live session ke liye hai — WhiteboardPage
  // model me is field ke liye jagah nahi hai isliye reopen/restore pe
  // reset ho jaata hai (agar permanently save karna ho to
  // WhiteboardPage.toJson()/fromJson() me `ruledLines` field add karna hoga).
  //
  // 🔥 NAYA v2 — pehle sirf on/off tha, fixed 40px horizontal lines.
  // Ab har page apni alag style rakh sakta hai: orientation (horizontal
  // jaisa notebook / vertical jaisa graph paper columns / grid dono) aur
  // spacing (lines choti-badi — paas-paas ya door-door).
  final Map<String, _RuledLineStyle> _ruledPages = {};
  bool get _showRuledLines => _ruledPages.containsKey(_page.id);
  _RuledLineStyle get _currentRuledStyle => _ruledPages[_page.id] ?? const _RuledLineStyle();

  void _toggleRuledLines() {
    final pageId = _page.id;
    setState(() {
      if (_ruledPages.containsKey(pageId)) {
        _ruledPages.remove(pageId);
      } else {
        _ruledPages[pageId] = const _RuledLineStyle();
      }
    });
    _isDirty = true;
    final style = _ruledPages[pageId];
    _sendRoomEvent('ruled_lines', {
      'pageId': pageId,
      'enabled': style != null,
      if (style != null) ...style.toJson(),
    });
  }

  // 🔥 NAYA — spacing chhoti-badi karna aur orientation (horizontal/
  // vertical/grid) badalna, dono isi ek function se hota hai taaki
  // sirf ek hi socket event bhejna pade.
  void _setRuledLineStyle({double? spacing, _RuledLineOrientation? orientation}) {
    final pageId = _page.id;
    final current = _currentRuledStyle;
    final updated = _RuledLineStyle(
      spacing: spacing ?? current.spacing,
      orientation: orientation ?? current.orientation,
    );
    setState(() => _ruledPages[pageId] = updated);
    _isDirty = true;
    _sendRoomEvent('ruled_lines', {'pageId': pageId, 'enabled': true, ...updated.toJson()});
  }

  // 🔥 NAYA — "Line Style" bottom sheet: yahan se lines ki spacing
  // (chhoti/darmiyani/badi) aur orientation (horizontal jaisa notebook /
  // vertical jaisa graph-paper columns / grid dono) choose kar sakte ho.
  // Tap karte hi turant apply + doosre participants ko sync ho jaata hai.
  void _showRuledLineStyleSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF262636),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (sheetContext) {
        return StatefulBuilder(
          builder: (sheetContext, setSheetState) {
            final style = _currentRuledStyle;
            Widget sectionTitle(String text) => Padding(
                  padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
                  child: Text(text, style: const TextStyle(color: Colors.white54, fontSize: 12, fontWeight: FontWeight.w600)),
                );
            Widget chip(String label, bool selected, VoidCallback onTap) => Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    label: Text(label),
                    selected: selected,
                    onSelected: (_) {
                      onTap();
                      setSheetState(() {});
                    },
                    labelStyle: TextStyle(color: selected ? Colors.black : Colors.white70),
                    selectedColor: Colors.tealAccent,
                    backgroundColor: Colors.white10,
                  ),
                );
            return SafeArea(
              child: Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                      child: Row(children: const [
                        Icon(Icons.tune, color: Colors.white),
                        SizedBox(width: 8),
                        Text('Line Style', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                      ]),
                    ),
                    sectionTitle('DIRECTION'),
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Row(children: [
                        chip('Horizontal', style.orientation == _RuledLineOrientation.horizontal,
                            () => _setRuledLineStyle(orientation: _RuledLineOrientation.horizontal)),
                        chip('Vertical', style.orientation == _RuledLineOrientation.vertical,
                            () => _setRuledLineStyle(orientation: _RuledLineOrientation.vertical)),
                        chip('Grid (both)', style.orientation == _RuledLineOrientation.grid,
                            () => _setRuledLineStyle(orientation: _RuledLineOrientation.grid)),
                      ]),
                    ),
                    sectionTitle('LINE SIZE (spacing)'),
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Row(children: [
                        chip('Small', style.spacing == _RuledLineStyle.smallSpacing,
                            () => _setRuledLineStyle(spacing: _RuledLineStyle.smallSpacing)),
                        chip('Medium', style.spacing == _RuledLineStyle.mediumSpacing,
                            () => _setRuledLineStyle(spacing: _RuledLineStyle.mediumSpacing)),
                        chip('Large', style.spacing == _RuledLineStyle.largeSpacing,
                            () => _setRuledLineStyle(spacing: _RuledLineStyle.largeSpacing)),
                      ]),
                    ),
                    const SizedBox(height: 8),
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }

  // 🔥 NAYA — SHARED FILE SYNC
  // ------------------------------------------------------------
  // `_pagePdfPaths`/`_pageImagePaths` upar sirf LOCAL device paths hain
  // (kisi doosre participant ke phone pe kaam nahi karte). Jab koi file
  // load karta hai, wo pehle backend pe upload hoti hai aur uska URL
  // page ke `fileUrl` field me store + broadcast hota hai. `_pageFileSourceUrl`
  // track karta hai ki har page ke liye abhi kaunsa URL download/set ho
  // chuka hai (duplicate re-download rokne ke liye). `_pendingFileLoads`
  // ek queue hai — socket event `setState` ke andar aata hai, lekin
  // download khud async hai isliye use `setState` ke BAHAR, switch ke
  // baad process karte hain (jaise `_pendingPresentationSwitch` pattern).
  // ------------------------------------------------------------
  final Map<String, String> _pageFileSourceUrl = {};
  final List<Map<String, String>> _pendingFileLoads = [];

  // Generic undo history — sirf current user ke actions, page id se keyed.
  final Map<String, List<_BoardAction>> _myActionsByPage = {};

  String? get _loadedPdfPath => _pagePdfPaths[_page.id];
  String? get _loadedImagePath => _pageImagePaths[_page.id];

  // Floating Window State
  List<UserProfileWindowModel> _windows = [];
  int _topZIndex = 1;
  // 🔥 NAYA — 'user_joined' discovery handshake ke liye: setState() ke
  // andar socket send karna theek nahi (side-effect ko build/rebuild se
  // alag rakhna chahiye), isliye is flag se track karte hain aur switch
  // khatam hone ke baad, setState() ke bahar, reply bhejte hain.
  bool _pendingSelfAnnounceReply = false;
  bool _pendingPresentationSwitch = false;

  bool _startingCall = false;

  // PARTICIPANTS PANEL (compact button in the bottom control bar <->
  // full-screen grid)
  // ------------------------------------------------------------
  // This is a SINGLE consolidated container — whoever has their video ON
  // gets a spot inside this container (instead of separate floating
  // windows per participant). It lives as a small icon button in the
  // bottom call control bar (next to mic/camera/screen-share) so it
  // doesn't compete for space on the whiteboard. Tapping it expands to a
  // full-screen grid (live video for anyone with camera ON, profile
  // picture for anyone with it OFF).
  //
  // `_participantsExpanded` is LOCAL UI state only — no socket event
  // sends it anywhere. So:
  //   - If I expand the grid, only I see it — it has no effect on other
  //     participants' screens.
  //   - Every participant has the same collapse/expand access (everyone
  //     always sees the same compact button, so "who's online" is
  //     always one tap away, even if nobody has opened their grid).
  bool _participantsExpanded = false;

  // When set, the participants overlay shows a "spotlight" layout instead
  // of the grid: this participant large (~70% of the overlay height) with
  // everyone else in a scrollable strip below (~30%). Tapping any tile —
  // in the grid or in the strip — focuses that participant; tapping
  // "back to grid" or closing the overlay clears it. Purely local UI
  // state, same as `_participantsExpanded`.
  String? _focusedParticipantId;

  UserProfileWindowModel? _focusedWindowOrNull() {
    if (_focusedParticipantId == null) return null;
    for (final w in _windows) {
      if (w.userId == _focusedParticipantId) return w;
    }
    return null; // focused participant left the room — falls back to grid
  }

  // Google Meet-style auto-join media (camera/mic). This is SEPARATE
  // from the older `CallManager.instance` above — that one is still used
  // for regular 1:1 ringing calls (in case a real 1:1 call is ever
  // received while inside the study room). This new instance is only
  // for this study room
  // session ke liye hai, koi ringing nahi karta.
  final StudyRoomCallManager _roomCall = StudyRoomCallManager();

  // Study Timer
  StudyTimerState _timer = StudyTimerState();
  Duration _timerRemaining = const Duration(minutes: 25);
  Timer? _timerTicker;

  // In-room Quick Chat — temporary, session-only (like Google Meet's
  // in-call chat). Never loaded from or saved to the real conversation.
  final List<_RoomChatMessage> _quickChatMessages = [];
  // 🔥 NAYA — jab tak user khud chat drawer na khole, incoming message ka
  // pata chalta rahe isliye chat icon ke upar WhatsApp jaisa blue "unread"
  // dot + jisne bheja uska naam (tooltip me) track karte hain.
  bool _hasUnreadChat = false;
  String? _lastChatSenderName;
  final TextEditingController _chatInputController = TextEditingController();

  // Sticker Reactions — study room ke liye sabse useful 10 quick
  // reactions. Har ek ka apna context: 👍 agree/ok, ❤️ thanks/love,
  // 🎉 done/celebrate, 👏 good job, 🤔 doubt/thinking, ✋ raise
  // hand/question, 🔥 on a streak, 💯 nailed it, 😴 need a break,
  // ✅ understood/marked complete.
  static const List<String> _stickerPack = [
    '👍', '❤️', '🎉', '👏', '🤔', '✋', '🔥', '💯', '😴', '✅',
  ];
  final List<_StickerEvent> _activeStickers = [];
  final Map<String, Timer> _stickerTimers = {};

  // Auto-save
  Timer? _autoSaveTimer;
  bool _isDirty = false;

  // Session lifecycle
  bool _sessionEnded = false;

  // ============================================================
  // SYSTEM NAV BAR (phone ka back/home/recents bar) AUTO-HIDE
  // ------------------------------------------------------------
  // Default me system nav bar hidden rehta hai taaki whiteboard ka
  // pura area mile aur wo neeche wale pen/pencil toolbar ke upar
  // aake overlap na kare. Jab user screen ke bilkul neeche edge se
  // touch/swipe kare, bar 3 second ke liye dikhta hai, phir apne aap
  // wapas hide ho jaata hai.
  // ============================================================
  Timer? _navBarHideTimer;

  void _showSystemNavTemporarily() {
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.manual, overlays: SystemUiOverlay.values);
    _navBarHideTimer?.cancel();
    _navBarHideTimer = Timer(const Duration(seconds: 3), () {
      if (!mounted) return;
      SystemChrome.setEnabledSystemUIMode(SystemUiMode.manual, overlays: []);
    });
  }

  @override
  void initState() {
    super.initState();
    _windows = List.from(widget.initialParticipants);
    // 🔥 FIX — pehle agar caller `initialParticipants` me current user ko
    // shaamil nahi karta tha (jaise ab tak chat_screen se hamesha `[]`
    // pass ho raha tha), to apna khud ka video/avatar block KABHI nahi
    // banta tha, aur `_announceSelfJoined()` bhi silently kuch nahi karta
    // tha (self hi `_windows` me nahi milta tha) — isliye doosre
    // participants ko bhi ye user room me hai ye pata hi nahi chalta tha.
    // Ab yahan defensively apna window guaranteed bana dete hain.
    _ensureSelfWindow();
    _timerRemaining = _timer.totalDuration;
    // 🔥 FIX — pehle yahan hamesha unconditionally purana whiteboard state
    // restore ho jaata tha, chahe user "Study Room" icon se bilkul NAYI
    // session start kar raha ho ya kisi purani/active session me JOIN kar
    // raha ho — dono cases me same persistent room + purana board dikhta
    // tha. Ab `startNewSession` true ho (fresh start) to restore skip karke
    // khaali page se shuru karte hain, aur purana backend state bhi khaali
    // pages se overwrite kar dete hain taaki agli baar (join flow se) koi
    // aur galti se purana content na dekhe.
    if (widget.startNewSession) {
      _isDirty = true;
      _saveBoardState();
    } else {
      _restoreBoardState();
    }
    _connectSocket();
    _startAutoSave();
    // Study room khulte hi system nav bar hide — sirf whiteboard/toolbar
    // ke liye jagah, bina kisi overlap ke.
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.manual, overlays: []);
    // 🔥 NAYA — CallManager global hai; jab bhi iski state change ho
    // (mic toggle, call connect/end waghera) is screen ko bhi rebuild karo
    // taaki upar wala mini audio-status bar sync rahe.
    CallManager.instance.addListener(_onCallManagerChanged);

    // 🔥 NAYA — Google Meet jaisa: koi ring/accept nahi, screen khulte
    // hi seedha media room se connect (camera/mic dono OFF default me,
    // user khud on karega). `_roomCall` ka apna listener bhi isi
    // `_onCallManagerChanged` se rebuild trigger karta hai taaki jaise
    // hi koi remote participant camera/mic on kare, UI turant update ho.
    _roomCall.addListener(_onCallManagerChanged);
    // 🔥 NAYA — jab koi remote participant LiveKit room chhod de, uska
    // floating profile/camera window bhi hata do (pehle stale window
    // hamesha ke liye "camera off" state me pada reh jaata tha).
    _roomCall.onParticipantLeft = _onRemoteParticipantLeft;
    _joinStudyRoomMedia();

    // 🔥 NAYA — jab tak study room screen khuli hai, phone khud-ba-khud
    // lock/sleep nahi hona chahiye (whiteboard use karte waqt beech me
    // screen band ho jaana bahut irritating hai). Screen band karna
    // (ya app minimize karna) user apni marzi se kar sakta hai — ye sirf
    // AUTO screen-timeout ko rokta hai.
    WakelockPlus.enable();
  }

  void _onRemoteParticipantLeft(String identity) {
    if (!mounted) return;
    setState(() {
      _windows.removeWhere((w) => w.userId == identity);
    });
  }

  // ============================================================
  // PARTICIPANT-JOINED TOAST — jab bhi koi naya participant room me
  // aata hai, uska naam chhote se floating pill me top se andar aakar
  // ~2.5s dikhta hai, phir khud fade/slide-out ho jaata hai. Isse
  // participants grid khole bina hi turant pata chal jaata hai ki
  // "kaun-kaun newly join hua" — bilkul Meet/Zoom ke "X joined the
  // meeting" toast jaisa.
  // ============================================================
  void _announceParticipantJoined(String name) {
    if (!mounted) return;
    final overlay = Overlay.of(context);
    late OverlayEntry entry;
    entry = OverlayEntry(
      builder: (overlayContext) => _JoinToast(
        name: name,
        onDone: () => entry.remove(),
      ),
    );
    overlay.insert(entry);
  }

  // 🔥 NAYA — apna window guaranteed banata hai agar pehle se `_windows`
  // me nahi hai (e.g. caller ne `initialParticipants` me self shaamil
  // nahi kiya). Bina isske apna video/avatar block kabhi visible nahi
  // hota, aur discovery handshake (`_announceSelfJoined`) bhi chal nahi
  // paata.
  void _ensureSelfWindow() {
    final alreadyThere = _windows.any((w) => w.userId == widget.currentUserId);
    if (alreadyThere) return;
    final name = widget.currentUserName?.trim();
    _windows.add(UserProfileWindowModel(
      userId: widget.currentUserId,
      displayName: (name != null && name.isNotEmpty) ? name : 'Participant',
      avatarUrl: widget.currentUserAvatar,
      position: const Offset(16, 90),
      size: const Size(120, 160),
      zIndex: ++_topZIndex,
    ));
  }

  // 🔥 NAYA — StudyRoomJoinView ab conversation ke saare members ka
  // `user_id`/`display_name`/`avatar_url` bhejta hai. Jo bhi userId
  // `_windows` me pehle se nahi hai (self ko chhodkar) uske liye ek naya
  // profile window bana dete hain — isse room khulte hi sabka naam/photo
  // dikhta hai, chahe wo abhi LiveKit me connect na hua ho (camera-off
  // hone par bhi avatar placeholder dikhta rahega).
  void _applyServerParticipants(List<dynamic> list) {
    if (!mounted) return;
    setState(() {
      for (final raw in list) {
        if (raw is! Map) continue;
        final p = raw.cast<String, dynamic>();
        final userId = p['user_id']?.toString();
        if (userId == null || userId.isEmpty || userId == widget.currentUserId) continue;
        if (_windows.any((w) => w.userId == userId)) continue;

        final name = p['display_name']?.toString();
        final offset = _windows.length * 24.0;
        _windows.add(UserProfileWindowModel(
          userId: userId,
          displayName: (name != null && name.isNotEmpty) ? name : 'Participant',
          avatarUrl: p['avatar_url']?.toString(),
          position: Offset(16 + offset, 90 + offset),
          size: const Size(120, 160),
          zIndex: ++_topZIndex,
        ));
      }
    });
  }

  // 🔥 NAYA — apna profile window (agar `initialParticipants` me pehle se
  // maujood hai) baaki sab ko broadcast karta hai, taaki jo bhi room me
  // pehle se hai wo mujhe turant add kar le. Ye "discovery handshake" hai:
  // koi central "kaun-kaun room me hai" backend endpoint nahi hai, isliye
  // socket ke through hi sab ek doosre ko batate hain.
  void _announceSelfJoined() {
    UserProfileWindowModel? selfWindow;
    for (final w in _windows) {
      if (w.userId == widget.currentUserId) {
        selfWindow = w;
        break;
      }
    }
    // Agar self hi `_windows` me nahi hai to broadcast karne ke liye
    // profile data hi nahi hai — is case me apni khud ki tile bhi nahi
    // dikhti (pehle se yahi assumption thi), caller screen ko
    // `initialParticipants` me hamesha current user ko shaamil karna hoga.
    if (selfWindow == null) return;
    _sendRoomEvent('user_joined', selfWindow.toJson());
  }

  Future<void> _joinStudyRoomMedia() async {
    try {
      final data = await CallApiService.joinStudyRoom(widget.conversationId, newSession: widget.startNewSession);
      final livekitUrl = data['livekit_url']?.toString();
      final livekitToken = data['livekit_token']?.toString();
      if (livekitUrl == null || livekitToken == null) {
        throw Exception("Study room media credentials server se nahi mile");
      }
      // 🔥 NAYA — backend ab conversation ke saare members ka naam/photo
      // bhi bhejta hai. Isse turant (socket handshake ka wait kiye bina)
      // har participant ka profile photo + naam room ke andar dikh jaata
      // hai. Socket 'user_joined'/'update_window' events isi list ko
      // upar hi refresh karte rehte hain — kuch conflict nahi hota.
      final participantsData = data['participants'];
      if (participantsData is List) {
        _applyServerParticipants(participantsData);
      }

      await _roomCall.joinRoom(livekitUrl: livekitUrl, livekitToken: livekitToken);
      _announceSelfJoined();
    } catch (e) {
      developer.log("Study room auto-join failed: $e");
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Camera/mic connect failed: $e")),
        );
      }
    }
  }

  void _onCallManagerChanged() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _timerTicker?.cancel();
    _autoSaveTimer?.cancel();
    _navBarHideTimer?.cancel();
    for (final t in _stickerTimers.values) {
      t.cancel();
    }
    _stickerTimers.clear();
    _chatInputController.dispose();
    _socket.dispose();
    CallManager.instance.removeListener(_onCallManagerChanged);
    _roomCall.removeListener(_onCallManagerChanged);
    _roomCall.leaveRoom();
    // Screen se bahar jaate hi phone ka normal system nav bar wapas la do —
    // baaki app is hidden state me stuck na rahe.
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
    // 🔥 NAYA — study room band karte hi auto-lock wapas normal (taaki
    // baaki poori app me phone hamesha ke liye jaagta na reh jaaye).
    WakelockPlus.disable();
    super.dispose();
  }

  Future<void> _connectSocket() async {
    await _socket.connect(widget.conversationId);
    _socket.events.listen(_handleRoomEvent);
  }

  // ============================================================
  // INCOMING REALTIME EVENTS
  // ============================================================
  void _handleRoomEvent(Map<String, dynamic> event) {
    developer.log("Study room event: $event");
    final type = event['type'];

    // 🔥 FIX — quick chat ab ek study_room_event ('quick_chat' action) ke
    // through chalti hai, isliye asli persisted chat_message/message
    // events ko is room ke andar handle hi nahi karte — wo apni normal
    // inbox screen ke liye hain, is temporary room chat ke liye nahi.
    if (type != 'study_room_event') return;

    final action = event['action'];
    final data = (event['data'] as Map?)?.cast<String, dynamic>() ?? {};

    // 'end_session' room ko turant band kar deta hai (sabko bahar nikal
    // deta hai), isliye ise setState/switch se pehle, alag se handle
    // karte hain.
    if (action == 'end_session') {
      _handleSessionEndedByRemote();
      return;
    }

    setState(() {
      switch (action) {
        case 'draw_point':
          {
            final page = _findPage(data['pageId']);
            if (page == null) return;
            final point = DrawingPoint.fromJson(data['point']);
            final isNew = data['isNew'] == true;
            if (isNew || page.strokes.isEmpty) {
              page.strokes.add([point]);
            } else {
              page.strokes.last.add(point);
            }
            break;
          }

        case 'undo_user_stroke':
          {
            final page = _findPage(data['pageId']);
            if (page == null) return;
            _undoUserLastStroke(data['userId'], page);
            break;
          }

        case 'clear_board':
          {
            final page = _findPage(data['pageId']);
            if (page == null) return;
            page.strokes.clear();
            page.shapes.clear();
            page.texts.clear();
            page.stickyNotes.clear();
            page.userStrokeIndices.clear();
            _myActionsByPage.remove(page.id);
            break;
          }

        case 'clear_board_keep_text':
          {
            final page = _findPage(data['pageId']);
            if (page == null) return;
            page.strokes.clear();
            page.shapes.clear();
            page.stickyNotes.clear();
            page.userStrokeIndices.clear();
            _myActionsByPage[page.id]?.removeWhere((a) => a.type != _BoardActionType.text);
            break;
          }

        case 'undo_user_shape':
          {
            final page = _findPage(data['pageId']);
            if (page == null) return;
            page.shapes.removeWhere((s) => s.id == data['shapeId']);
            break;
          }

        case 'undo_user_text':
          {
            final page = _findPage(data['pageId']);
            if (page == null) return;
            page.texts.removeWhere((t) => t.id == data['textId']);
            break;
          }

        case 'undo_user_sticky':
          {
            final page = _findPage(data['pageId']);
            if (page == null) return;
            page.stickyNotes.removeWhere((n) => n.id == data['noteId']);
            break;
          }

        case 'add_sticky_note':
          {
            final page = _findPage(data['pageId']);
            if (page == null) return;
            final incoming = StickyNoteModel.fromJson(data['note'] ?? data);
            final idx = page.stickyNotes.indexWhere((n) => n.id == incoming.id);
            if (idx != -1) {
              page.stickyNotes[idx] = incoming;
            } else {
              page.stickyNotes.add(incoming);
            }
            break;
          }

        case 'update_window':
          {
            final idx = _windows.indexWhere((w) => w.userId == data['userId']);
            if (idx != -1) _windows[idx] = UserProfileWindowModel.fromJson(data);
            break;
          }

        // 🔥 NAYA — kisi participant ne ruled-paper-lines background
        // on/off ki (ya spacing/orientation badla), sabki screen turant
        // sync ho jaani chahiye.
        case 'ruled_lines':
          {
            final pageId = data['pageId']?.toString();
            if (pageId == null) return;
            final enabled = data['enabled'] == true;
            if (enabled) {
              _ruledPages[pageId] = _RuledLineStyle.fromJson(data);
            } else {
              _ruledPages.remove(pageId);
            }
            break;
          }

        // 🔥 NAYA — koi naya user room me aaya (ya humein khud ko announce
        // kar raha hai). `widget.currentUserId` ka apna hi echo ignore karo.
        // Agar ye userId pehli baar dikha hai, tabhi apna khud ka
        // 'user_joined' wapas bhejo — isse naya joiner un sab logo ko bhi
        // discover kar leta hai jo already room me the, aur reply sirf
        // ek baar hoti hai (loop nahi banti, kyunki dusri baar wahi userId
        // dobara aaye to already known hoga).
        case 'user_joined':
          {
            final userId = data['userId']?.toString();
            if (userId == null || userId == widget.currentUserId) return;
            final idx = _windows.indexWhere((w) => w.userId == userId);
            if (idx == -1) {
              final joined = UserProfileWindowModel.fromJson(data);
              _windows.add(joined);
              _pendingSelfAnnounceReply = true;
              // 🔥 NAYA — pehle naya participant sirf silently _windows
              // list me add ho jaata tha; uska naam kahin turant dikhta
              // nahi tha jab tak koi khud participants grid na kholta.
              // Ab room khulte hi ek chhota toast dikhta hai jisme uska
              // naam saaf nazar aata hai (jaise Meet/Zoom "X joined").
              _announceParticipantJoined(joined.displayName);
            } else {
              _windows[idx] = UserProfileWindowModel.fromJson(data);
            }
            break;
          }

        case 'add_shape':
          {
            final page = _findPage(data['pageId']);
            if (page == null) return;
            page.shapes.add(ShapeElement.fromJson(data['shape']));
            break;
          }

        case 'add_text':
          {
            final page = _findPage(data['pageId']);
            if (page == null) return;
            final incoming = TextElement.fromJson(data['text']);
            final idx = page.texts.indexWhere((t) => t.id == incoming.id);
            if (idx != -1) {
              page.texts[idx] = incoming;
            } else {
              page.texts.add(incoming);
            }
            break;
          }

        case 'add_page':
          {
            final newPage = WhiteboardPage.fromJson(data['page']);
            if (!_pages.any((p) => p.id == newPage.id)) {
              _pages.add(newPage);
              // 🔥 NAYA — koi doosra participant (ya PDF-split) naya page
              // laaya — jab tak main khud us page pe navigate nahi karta,
              // blue dot dikhana hai.
              _unvisitedPageIds.add(newPage.id);
            }
            if ((newPage.fileUrl ?? '').isNotEmpty) {
              _pendingFileLoads.add({
                'pageId': newPage.id,
                'fileUrl': newPage.fileUrl!,
                'fileType': newPage.fileType ?? 'file',
              });
            }
            break;
          }

        // 🔥 NAYA — kisi bhi participant ne PDF/image load/share ki. Sirf
        // URL yahan store karte hain (setState ke andar) — asli download
        // async hai isliye setState ke bahar `_pendingFileLoads` process
        // hone ke baad shuru hoga.
        case 'load_page_file':
          {
            final pageId = data['pageId']?.toString();
            final fileUrl = data['fileUrl']?.toString();
            final fileType = data['fileType']?.toString();
            if (pageId == null || fileUrl == null || fileUrl.isEmpty) return;
            final page = _findPage(pageId);
            if (page != null) {
              page.fileUrl = fileUrl;
              page.fileType = fileType;
            }
            _pendingFileLoads.add({
              'pageId': pageId,
              'fileUrl': fileUrl,
              'fileType': fileType ?? 'file',
            });
            break;
          }

        // 🔥 NAYA — koi presenting shuru kare to baaki sab bhi turant usi
        // presentation page pe switch ho jaate hain (Meet jaisa "auto
        // focus on presenter"). Presenter khud already apni taraf se
        // switch kar chuka hota hai (_toggleScreenShare me), isliye yahan
        // sirf doosron ke liye hai.
        case 'presentation_started':
          {
            final presenterId = data['presenterId']?.toString();
            if (presenterId == null || presenterId == widget.currentUserId) return;
            _pendingPresentationSwitch = true;
            break;
          }

        // Presentation ruk jaane par forcefully kisi ko wapas kisi doosre
        // page pe nahi le jaate — jo bhi drawing/annotations us page pe
        // ban chuki thi wo bani rehti hai (jaise PDF/image page ki bhi
        // rehti hai), user khud jab chahe doosre page pe switch kar sakta
        // hai. Ye case sirf yahan hai taaki future me koi UI indicator
        // ("presentation ended") isse hook kar sake.
        case 'presentation_stopped':
          break;

        case 'remove_page':
          {
            if (_pages.length <= 1) return;
            final removedId = data['pageId']?.toString();
            _pages.removeWhere((p) => p.id == data['pageId']);
            _currentPageIndex = _currentPageIndex.clamp(0, _pages.length - 1);
            if (removedId != null) {
              _pagePdfPaths.remove(removedId);
              _pageImagePaths.remove(removedId);
              _myActionsByPage.remove(removedId);
              _pageFileSourceUrl.remove(removedId);
              _unvisitedPageIds.remove(removedId);
            }
            break;
          }

        case 'timer_update':
          {
            _timer = StudyTimerState.fromJson(data);
            _restartTimerTicker();
            break;
          }

        case 'quick_chat':
          {
            // Sender ne apna message pehle hi optimistically add kar liya
            // hota hai (_sendQuickChat me), isliye yahan sirf doosre
            // participants ke messages add karte hain — duplicate na ho.
            final incoming = _RoomChatMessage.fromJson(data);
            if (incoming.senderId != widget.currentUserId) {
              _quickChatMessages.add(incoming);
              // 🔥 NAYA — chat drawer band hai to unread badge on karo,
              // saath hi sender ka naam yaad rakho (chat icon tooltip me
              // dikhane ke liye).
              _hasUnreadChat = true;
              _lastChatSenderName = incoming.senderName;
            }
            break;
          }

        // 🔥 NAYA — koi sticker/reaction bheji gayi. Sender ne apna wala
        // pehle hi optimistically add kar liya hota hai (_sendSticker
        // me), isliye yahan bhi quick_chat jaisa hi — sirf doosre
        // participants ki sticker add karte hain, duplicate na ho.
        case 'sticker':
          {
            final incoming = _StickerEvent.fromJson(data);
            if (incoming.senderId != widget.currentUserId) {
              _activeStickers.add(incoming);
              _scheduleStickerRemoval(incoming.id);
            }
            break;
          }
      }
    });

    if (_pendingSelfAnnounceReply) {
      _pendingSelfAnnounceReply = false;
      _announceSelfJoined();
    }
    if (_pendingPresentationSwitch) {
      _pendingPresentationSwitch = false;
      _switchToPresentationPage(createIfMissing: true);
    }
    if (_pendingFileLoads.isNotEmpty) {
      final loads = List<Map<String, String>>.from(_pendingFileLoads);
      _pendingFileLoads.clear();
      for (final item in loads) {
        _downloadAndCachePageFile(item['pageId']!, item['fileUrl']!, item['fileType'] ?? 'file');
      }
    }
  }

  // ============================================================
  // LEAVE SESSION — sirf MAIN room se bahar nikalta hoon. Baaki sab
  // participants ke liye room chalu rehta hai, aur main baad me isi
  // conversation se dobara "Study Room" khol ke wapas join kar sakta
  // hoon (whiteboard state backend me save hoti hai, `_restoreBoardState`
  // se wapas load ho jaati hai). "End Session" se bilkul alag — usme
  // room hamesha ke liye sabke liye band ho jaata hai.
  // ============================================================
  Future<void> _leaveSession() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Leave Session?'),
        content: const Text(
          'You will leave this study room, but it stays open for everyone else — you can rejoin anytime. '
          'To close it for everyone instead, use "End Session for Everyone".',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          ElevatedButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Leave'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    // 🔥 NAYA — `end_session` room-event yahan BILKUL nahi bhejte (wo
    // sirf `_endSessionForEveryone` bhejta hai) — isliye baaki sab
    // participants ki screen pe koi asar nahi padta, sirf mera khud
    // ka connection/screen band hota hai. Cleanup (`_roomCall.leaveRoom()`,
    // `_socket.dispose()`, timers cancel) already `dispose()` me hota
    // hai jab ye route pop hota hai.
    Navigator.of(context).pop();
  }

  // ============================================================
  // END SESSION — jab koi bhi participant room khatam karta hai,
  // sabko turant bahar nikaal dete hain (Google Meet ki tarah "end for
  // everyone"). Chahe main khud end karu ya koi aur — dono jagah call
  // stop hoti hai, timer ruk jaata hai, aur screen band ho jaati hai.
  // ============================================================
  void _handleSessionEndedByRemote() {
    if (_sessionEnded || !mounted) return;
    _leaveRoomAfterSessionEnd(showMessage: 'The host ended this study session.');
  }

  Future<void> _endSessionForEveryone() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('End Session for Everyone?'),
        content: const Text(
          'This will immediately close the study room for every participant. This cannot be undone.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: Colors.redAccent),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('End Session'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    // Auto-save timer ko pehle hi cancel karte hain — warna wo state-delete
    // ke baad bhi ek aakhri baar purana board wapas save kar sakta hai
    // (race condition), aur "fresh" hone ka poora maksad hi fail ho jaayega.
    _autoSaveTimer?.cancel();
    _sendRoomEvent('end_session', {'endedBy': widget.currentUserId});
    // 🔥 NAYA — sirf "End Session for Everyone" par saved whiteboard state
    // permanently delete karte hain (fire-and-forget — iske liye user ko
    // ruk kar wait nahi karna, screen turant band ho jaani chahiye jaise
    // upar `_leaveRoomAfterSessionEnd` karta hai).
    unawaited(MessageApiService.endStudyRoomState(widget.conversationId).catchError((e) {
      developer.log('Failed to clear study room state: $e');
    }));
    _leaveRoomAfterSessionEnd(showMessage: 'Study session ended.');
  }

  void _leaveRoomAfterSessionEnd({required String showMessage}) {
    if (_sessionEnded) return;
    _sessionEnded = true;
    _timerTicker?.cancel();
    _autoSaveTimer?.cancel();
    if (CallManager.instance.isActive) {
      CallManager.instance.endCall();
    }
    if (!mounted) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(showMessage)));
      // 🔧 FIX — pehle sirf ek `.pop()` hota tha, jisse agar study room
      // screen ke upar koi aur route (jaise call/connecting screen) stacked
      // tha to user room se bahar nikal kar bhi kisi beech ke screen pe
      // atak jaata tha — poori tarah room se "left" nahi hota tha. Ab
      // stack ke sabse pehle (root/home) route tak seedha pop karte hain,
      // taaki end-session/leave dono cases me user ek hi tap me poori
      // tarah room ke bahar aa jaaye.
      final navigator = Navigator.of(context);
      if (navigator.canPop()) {
        navigator.popUntil((route) => route.isFirst);
      }
    });
  }

  WhiteboardPage? _findPage(dynamic pageId) {
    if (pageId == null) return _pages.isNotEmpty ? _pages.first : null;
    try {
      return _pages.firstWhere((p) => p.id == pageId.toString());
    } catch (_) {
      return null;
    }
  }

  void _undoUserLastStroke(String userId, WhiteboardPage page) {
    if (!page.userStrokeIndices.containsKey(userId) || page.userStrokeIndices[userId]!.isEmpty) return;
    final lastIdx = page.userStrokeIndices[userId]!.removeLast();
    if (lastIdx < page.strokes.length) {
      page.strokes.removeAt(lastIdx);
    }
  }

  void _recordMyAction(_BoardActionType type, {String? refId}) {
    _myActionsByPage.putIfAbsent(_page.id, () => []).add(
          _BoardAction(type: type, pageId: _page.id, refId: refId),
        );
  }

  // Jo bhi maine (current user ne) is page pe last kiya tha — chahe
  // stroke ho, shape ho, text ho ya sticky note — usi ko reverse karta
  // hai, aur dusre participants ko bhi sync kar deta hai.
  void _undoLastAction() {
    final history = _myActionsByPage[_page.id];
    if (history == null || history.isEmpty) return;
    final action = history.removeLast();

    setState(() {
      switch (action.type) {
        case _BoardActionType.stroke:
          _undoUserLastStroke(widget.currentUserId, _page);
          _sendRoomEvent('undo_user_stroke', {'userId': widget.currentUserId, 'pageId': _page.id});
          break;
        case _BoardActionType.shape:
          _page.shapes.removeWhere((s) => s.id == action.refId);
          _sendRoomEvent('undo_user_shape', {'userId': widget.currentUserId, 'pageId': _page.id, 'shapeId': action.refId});
          break;
        case _BoardActionType.text:
          _page.texts.removeWhere((t) => t.id == action.refId);
          _sendRoomEvent('undo_user_text', {'userId': widget.currentUserId, 'pageId': _page.id, 'textId': action.refId});
          break;
        case _BoardActionType.stickyNote:
          _page.stickyNotes.removeWhere((n) => n.id == action.refId);
          _sendRoomEvent('undo_user_sticky', {'userId': widget.currentUserId, 'pageId': _page.id, 'noteId': action.refId});
          break;
      }
      _isDirty = true;
    });
  }

  void _sendRoomEvent(String action, Map<String, dynamic> data) {
    _socket.sendStudyRoomEvent(action, data);
  }

  // ============================================================
  // WHITEBOARD — FREEHAND (marker/paint/highlighter/eraser)
  // ============================================================
  void _onPanStart(DragStartDetails details) {
    final pos = details.localPosition;

    if (isShapeTool(_activeTool)) {
      setState(() {
        _shapeDragStart = pos;
        _shapeDragCurrent = pos;
      });
      return;
    }

    final point = DrawingPoint(
      offset: pos,
      paint: Paint()
        ..color = _selectedColor
        ..strokeWidth = _strokeWidth
        ..strokeCap = StrokeCap.round,
      toolType: _activeTool,
    );

    setState(() {
      _currentStroke = [point];
      _page.strokes.add(_currentStroke);
      _page.userStrokeIndices.putIfAbsent(widget.currentUserId, () => []).add(_page.strokes.length - 1);
      _recordMyAction(_BoardActionType.stroke);
      _isDirty = true;
    });

    _sendRoomEvent('draw_point', {'point': point.toJson(), 'isNew': true, 'pageId': _page.id});
  }

  void _onPanUpdate(DragUpdateDetails details) {
    final pos = details.localPosition;

    if (isShapeTool(_activeTool)) {
      setState(() => _shapeDragCurrent = pos);
      return;
    }

    final point = DrawingPoint(
      offset: pos,
      paint: Paint()
        ..color = _selectedColor
        ..strokeWidth = _strokeWidth
        ..strokeCap = StrokeCap.round,
      toolType: _activeTool,
    );

    setState(() {
      _currentStroke.add(point);
      _isDirty = true;
    });

    _sendRoomEvent('draw_point', {'point': point.toJson(), 'isNew': false, 'pageId': _page.id});
  }

  void _onPanEnd(DragEndDetails details) {
    if (isShapeTool(_activeTool) && _shapeDragStart != null && _shapeDragCurrent != null) {
      final shape = ShapeElement(
        id: const Uuid().v4(),
        userId: widget.currentUserId,
        start: _shapeDragStart!,
        end: _shapeDragCurrent!,
        tool: _activeTool,
        color: _selectedColor,
        strokeWidth: _strokeWidth,
      );
      setState(() {
        _page.shapes.add(shape);
        _shapeDragStart = null;
        _shapeDragCurrent = null;
        _recordMyAction(_BoardActionType.shape, refId: shape.id);
        _isDirty = true;
      });
      _sendRoomEvent('add_shape', {'shape': shape.toJson(), 'pageId': _page.id});
    }
  }

  ShapeElement? _previewShape() {
    if (isShapeTool(_activeTool) && _shapeDragStart != null && _shapeDragCurrent != null) {
      return ShapeElement(
        id: '_preview',
        userId: widget.currentUserId,
        start: _shapeDragStart!,
        end: _shapeDragCurrent!,
        tool: _activeTool,
        color: _selectedColor,
        strokeWidth: _strokeWidth,
      );
    }
    return null;
  }

  void _clearCompleteBoard() {
    setState(() {
      _page.strokes.clear();
      _page.shapes.clear();
      _page.texts.clear();
      _page.stickyNotes.clear();
      _page.userStrokeIndices.clear();
      _myActionsByPage.remove(_page.id);
      _isDirty = true;
    });
    _sendRoomEvent('clear_board', {'pageId': _page.id});
  }

  // Sab kuch hata do — drawing, shapes, sticky notes — sirf jo maine
  // (ya kisi ne) likha hua text hai wahi board pe reh jaaye.
  void _clearBoardKeepText() {
    setState(() {
      _page.strokes.clear();
      _page.shapes.clear();
      _page.stickyNotes.clear();
      _page.userStrokeIndices.clear();
      _myActionsByPage[_page.id]?.removeWhere((a) => a.type != _BoardActionType.text);
      _isDirty = true;
    });
    _sendRoomEvent('clear_board_keep_text', {'pageId': _page.id});
  }

  // ============================================================
  // TEXT TOOL
  // ============================================================
  Future<void> _onCanvasTapForText(TapUpDetails details) async {
    final controller = TextEditingController();
    final result = await showDialog<String>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Add Text'),
        content: TextField(controller: controller, autofocus: true, maxLines: 3),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          ElevatedButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('Add'),
          ),
        ],
      ),
    );
    if (result == null || result.isEmpty) return;

    final textEl = TextElement(
      id: const Uuid().v4(),
      userId: widget.currentUserId,
      text: result,
      position: details.localPosition,
      color: _selectedColor,
      fontSize: (_strokeWidth * 4).clamp(14.0, 40.0),
    );
    setState(() {
      _page.texts.add(textEl);
      _recordMyAction(_BoardActionType.text, refId: textEl.id);
      _isDirty = true;
    });
    _sendRoomEvent('add_text', {'text': textEl.toJson(), 'pageId': _page.id});
  }

  Future<void> _editTextElement(TextElement t) async {
    final controller = TextEditingController(text: t.text);
    final result = await showDialog<String>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Edit Text'),
        content: TextField(controller: controller, autofocus: true, maxLines: 3),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          ElevatedButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    if (result == null) return;
    setState(() {
      t.text = result;
      _isDirty = true;
    });
    _sendRoomEvent('add_text', {'text': t.toJson(), 'pageId': _page.id});
  }

  // ============================================================
  // MULTI-PAGE MANAGEMENT
  // ============================================================
  void _addPage() {
    final newPage = WhiteboardPage(id: const Uuid().v4());
    setState(() {
      _pages.add(newPage);
      _currentPageIndex = _pages.length - 1;
      _isDirty = true;
    });
    _sendRoomEvent('add_page', {'page': newPage.toJson()});
  }

  void _removeCurrentPage() {
    if (_pages.length <= 1) return;
    final removedId = _page.id;
    setState(() {
      _pages.removeAt(_currentPageIndex);
      _currentPageIndex = _currentPageIndex.clamp(0, _pages.length - 1);
      _pagePdfPaths.remove(removedId);
      _pageImagePaths.remove(removedId);
      _pageFileSourceUrl.remove(removedId);
      _unvisitedPageIds.remove(removedId);
      _isDirty = true;
    });
    _sendRoomEvent('remove_page', {'pageId': removedId});
  }

  // ============================================================
  // SCREEN SHARE / PRESENTATION
  // ------------------------------------------------------------
  // Google Meet-style: screen share ek naye page ka background ban jaata
  // hai (existing multi-page whiteboard infra dobara use hoti hai),
  // isliye pen/marker/shapes/color — sab kuch already usi tarah kaam karta
  // hai jaise kisi PDF/image ke upar karta hai. Koi alag drawing engine
  // nahi likhna pada.
  // ============================================================
  bool get _onPresentationPage => _page.id == _presentationPageId;

  void _switchToPresentationPage({required bool createIfMissing}) {
    final existingIdx = _pages.indexWhere((p) => p.id == _presentationPageId);
    if (existingIdx != -1) {
      setState(() => _currentPageIndex = existingIdx);
      return;
    }
    if (!createIfMissing) return;
    final newPage = WhiteboardPage(id: _presentationPageId);
    setState(() {
      _pages.add(newPage);
      _currentPageIndex = _pages.length - 1;
      _isDirty = true;
    });
    // Existing 'add_page' handler already sabke liye idempotently sync
    // kar deta hai (id se duplicate-check karta hai) — koi naya event
    // type nahi chahiye is hisse ke liye.
    _sendRoomEvent('add_page', {'page': newPage.toJson()});
  }

  Future<void> _toggleScreenShare() async {
    if (_roomCall.isScreenSharing) {
      await _roomCall.stopScreenShare();
      _sendRoomEvent('presentation_stopped', {'presenterId': widget.currentUserId});
      if (mounted) setState(() {});
      return;
    }

    final started = await _roomCall.startScreenShare();
    if (!started) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(_roomCall.error ?? "Could not start screen share")),
        );
      }
      return;
    }

    _switchToPresentationPage(createIfMissing: true);
    _sendRoomEvent('presentation_started', {
      'presenterId': widget.currentUserId,
      'presenterName': _myDisplayName,
    });
  }

  // Flips the local camera between front and back. Only affects the
  // local user's own outgoing video — available from the bottom call
  // control bar (while camera is on) and from the spotlight view when
  // viewing yourself large in the participants overlay.
  Future<void> _switchCamera() async {
    final track = _roomCall.localVideoTrack;
    if (track == null) return;
    try {
      final devices = await Hardware.instance.videoInputs();
      if (devices.isEmpty) return;

      final currentDeviceId = track.currentOptions is CameraCaptureOptions
          ? (track.currentOptions as CameraCaptureOptions).deviceId
          : null;

      final nextDevice = devices.firstWhere(
        (d) => d.deviceId != currentDeviceId,
        orElse: () => devices.first,
      );

      await track.switchCamera(nextDevice.deviceId);
      if (mounted) setState(() {});
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not switch camera: $e')),
        );
      }
    }
  }

  // ============================================================
  // STUDY TIMER (Pomodoro)
  // ============================================================
  void _restartTimerTicker() {
    _timerTicker?.cancel();
    if (!_timer.isRunning || _timer.endAt == null) {
      _timerRemaining = _timer.totalDuration;
      if (mounted) setState(() {});
      return;
    }
    _timerTicker = Timer.periodic(const Duration(seconds: 1), (_) => _tickTimer());
    _tickTimer();
  }

  void _tickTimer() {
    if (_timer.endAt == null) return;
    final remaining = _timer.endAt!.difference(DateTime.now());

    if (remaining.isNegative) {
      _timerTicker?.cancel();
      final nowBreak = !_timer.isBreak;
      setState(() {
        _timer.isBreak = nowBreak;
        _timer.isRunning = true;
        _timer.endAt = DateTime.now().add(_timer.totalDuration);
        _timerRemaining = _timer.totalDuration;
      });
      _broadcastTimer();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(_timer.isBreak ? "Break time! ☕" : "Back to focus! 📚")),
        );
      }
      _restartTimerTicker();
      return;
    }

    if (mounted) setState(() => _timerRemaining = remaining);
  }

  void _broadcastTimer() => _sendRoomEvent('timer_update', _timer.toJson());

  void _toggleTimer() {
    setState(() {
      _timer.isRunning = !_timer.isRunning;
      _timer.endAt = _timer.isRunning ? DateTime.now().add(_timerRemaining) : null;
    });
    _broadcastTimer();
    _restartTimerTicker();
  }

  void _resetTimer() {
    _timerTicker?.cancel();
    setState(() {
      _timer.isRunning = false;
      _timer.isBreak = false;
      _timer.endAt = null;
      _timerRemaining = _timer.totalDuration;
    });
    _broadcastTimer();
  }

  Future<void> _configureTimer() async {
    final focusCtrl = TextEditingController(text: _timer.focusMinutes.toString());
    final breakCtrl = TextEditingController(text: _timer.breakMinutes.toString());
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Study Timer Settings'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: focusCtrl,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: 'Focus minutes'),
            ),
            TextField(
              controller: breakCtrl,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: 'Break minutes'),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          ElevatedButton(onPressed: () => Navigator.pop(context, true), child: const Text('Save')),
        ],
      ),
    );
    if (ok != true) return;

    setState(() {
      _timer.focusMinutes = int.tryParse(focusCtrl.text) ?? _timer.focusMinutes;
      _timer.breakMinutes = int.tryParse(breakCtrl.text) ?? _timer.breakMinutes;
      if (!_timer.isRunning) _timerRemaining = _timer.totalDuration;
    });
    _broadcastTimer();
  }

  // ============================================================
  // IN-ROOM QUICK CHAT
  // ============================================================
  // 🔥 FIX — quick chat ab poori tarah temporary hai (Google Meet ki
  // in-call chat jaisi): na ye backend ki asli conversation me save
  // hoti hai, na wahan se load hoti hai. Ye sirf study_room_event ke
  // 'quick_chat' action se doosre participants tak socket ke through
  // pahunchti hai aur sirf is session/screen ke jeevan tak zinda rehti
  // hai — screen band hote hi hamesha ke liye gayab.
  String get _myDisplayName {
    for (final w in _windows) {
      if (w.userId == widget.currentUserId) return w.displayName;
    }
    return 'You';
  }

  void _sendQuickChat() {
    final text = _chatInputController.text.trim();
    if (text.isEmpty) return;

    final msg = _RoomChatMessage(
      senderId: widget.currentUserId,
      senderName: _myDisplayName,
      text: text,
      sentAt: DateTime.now(),
    );

    setState(() => _quickChatMessages.add(msg));
    _sendRoomEvent('quick_chat', msg.toJson());
    _chatInputController.clear();
  }

  // ============================================================
  // STICKER REACTIONS — send + auto-dismiss
  // ============================================================
  void _sendSticker(String emoji) {
    final sticker = _StickerEvent(
      id: const Uuid().v4(),
      emoji: emoji,
      senderId: widget.currentUserId,
      senderName: _myDisplayName,
      dx: math.Random().nextDouble(),
    );

    setState(() => _activeStickers.add(sticker));
    _scheduleStickerRemoval(sticker.id);
    _sendRoomEvent('sticker', sticker.toJson());

    // Picker khud bandh nahi hota — user chahe to jaldi-jaldi kai
    // stickers bhej sake (jaise ek hi reaction baar-baar).
  }

  // 3 second baad sticker apne aap list se hat jaati hai (bas ye ek
  // sticker, baaki jo bhi is dauraan aayi hain wo apne timer pe
  // independently hatengi). Timer ko id se track karte hain taaki
  // dispose ke waqt sab cancel ho sake.
  void _scheduleStickerRemoval(String id) {
    _stickerTimers[id]?.cancel();
    _stickerTimers[id] = Timer(const Duration(seconds: 3), () {
      _stickerTimers.remove(id);
      if (!mounted) return;
      setState(() => _activeStickers.removeWhere((s) => s.id == id));
    });
  }

  // ============================================================
  // CALL FROM STUDY ROOM
  // ============================================================
  // 🔥 NAYA — ab call ke liye alag CallScreen par Navigator.push nahi karte
  // (jo whiteboard hata deta tha). CallManager ek global singleton hai jo
  // call ko background me zinda rakhta hai — bas usko start karo, whiteboard
  // screen pe hi raho, aur upar ek chhota audio/video status bar dikha do
  // (_buildCallStatusBar, Stack me build() ke andar).
  Future<void> _startRoomCall(String type) async {
    if (_startingCall) return;
    setState(() => _startingCall = true);
    try {
      final data = await CallApiService.initiateCall(widget.conversationId, type);

      final callId = data['call_id']?.toString() ?? data['id']?.toString();
      final livekitUrl = data['livekit_url']?.toString();
      final livekitToken = data['livekit_token']?.toString();
      if (callId == null || livekitUrl == null || livekitToken == null) {
        throw Exception("Call credentials server se nahi mile");
      }

      await CallManager.instance.startCallIfNeeded(
        callId: callId,
        conversationId: widget.conversationId,
        isVideo: type == 'video',
        isCaller: true,
        livekitUrl: livekitUrl,
        livekitToken: livekitToken,
        peerName: widget.peerName,
        peerAvatar: widget.peerAvatar,
      );
      // Navigator.push nahi — whiteboard screen pe hi raho.
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Call failed: $e")));
      }
    } finally {
      if (mounted) setState(() => _startingCall = false);
    }
  }

  // ============================================================
  // MINI CALL STATUS BAR — jab CallManager.instance.isActive ho, ek
  // chhota bar whiteboard ke upar dikhao (mic mute/unmute + duration +
  // end call). Poori CallScreen kholne ki zaroorat nahi — call background
  // me connected rehti hai, whiteboard visible rehta hai.
  // ============================================================
  Widget _buildCallStatusBar() {
    final cm = CallManager.instance;
    if (!cm.isActive) return const SizedBox.shrink();

    String _fmtDuration(Duration d) {
      final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
      final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
      return "$m:$s";
    }

    return SafeArea(
      child: Align(
        alignment: Alignment.topCenter,
        child: Container(
          margin: const EdgeInsets.only(top: 8),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          decoration: BoxDecoration(
            color: const Color(0xFF0D0D15).withOpacity(0.95),
            borderRadius: BorderRadius.circular(24),
            boxShadow: const [BoxShadow(color: Colors.black45, blurRadius: 8)],
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                cm.remoteConnected ? Icons.call : Icons.phone_in_talk,
                color: Colors.greenAccent,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                cm.remoteConnected ? _fmtDuration(cm.connectedDuration) : cm.status,
                style: const TextStyle(color: Colors.white, fontSize: 13),
              ),
              const SizedBox(width: 10),
              IconButton(
                constraints: const BoxConstraints(),
                padding: const EdgeInsets.all(6),
                icon: Icon(cm.muted ? Icons.mic_off : Icons.mic, color: Colors.white, size: 18),
                onPressed: () => cm.toggleMic(),
              ),
              IconButton(
                constraints: const BoxConstraints(),
                padding: const EdgeInsets.all(6),
                icon: const Icon(Icons.call_end, color: Colors.redAccent, size: 18),
                onPressed: () => cm.endCall(),
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ============================================================
  // BOTTOM CALL CONTROL BAR — mic / camera / screen-share / participants
  // / Ask AI, all in one place. Styled like Meet/Zoom's bottom bar: big
  // circular buttons, floating just above the phone's system nav area,
  // always within thumb reach. Participants and Ask AI used to be
  // separate floating pills scattered around the screen — they now live
  // here alongside the call controls so everything actionable is in one
  // predictable spot.
  // ============================================================
  Widget _buildBottomCallControlBar() {
    final bottomSafeInset = MediaQuery.of(context).padding.bottom;

    Widget circleButton({
      required IconData icon,
      required Color background,
      required Color iconColor,
      VoidCallback? onPressed,
      required String tooltip,
      int? badgeCount,
      Color badgeColor = Colors.tealAccent,
    }) {
      final button = Material(
        color: background,
        shape: const CircleBorder(),
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: onPressed,
          child: Container(
            width: 42,
            height: 42,
            alignment: Alignment.center,
            child: Icon(icon, color: iconColor, size: 19),
          ),
        ),
      );

      return Padding(
        padding: const EdgeInsets.symmetric(horizontal: 5),
        child: Tooltip(
          message: tooltip,
          child: badgeCount == null || badgeCount <= 0
              ? button
              : Stack(
                  clipBehavior: Clip.none,
                  children: [
                    button,
                    Positioned(
                      top: -2,
                      right: -2,
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
                        constraints: const BoxConstraints(minWidth: 18),
                        decoration: BoxDecoration(
                          color: badgeColor,
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(color: const Color(0xFF0D0D15), width: 1.5),
                        ),
                        child: Text(
                          '$badgeCount',
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            color: Colors.black,
                            fontSize: 10,
                            fontWeight: FontWeight.w800,
                            height: 1.2,
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
        ),
      );
    }

    final anyCameraOn = _windows.any((w) {
      final isSelf = w.userId == widget.currentUserId;
      final track = isSelf ? _roomCall.localVideoTrack : _roomCall.remoteVideoTracks[w.userId];
      return isSelf ? (_roomCall.cameraOn && track != null) : track != null;
    });

    return Align(
      alignment: Alignment.bottomCenter,
      child: Container(
        // Stacked directly above the drawing tool palette (which has its
        // own bottom margin below this), so the two floating bars sit one
        // above the other instead of overlapping.
        margin: EdgeInsets.only(bottom: _bottomCallBarBottomMargin(bottomSafeInset)),
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
        decoration: BoxDecoration(
          color: const Color(0xFF0D0D15).withOpacity(0.92),
          borderRadius: BorderRadius.circular(40),
          boxShadow: const [BoxShadow(color: Colors.black45, blurRadius: 10)],
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            circleButton(
              icon: anyCameraOn ? Icons.videocam : Icons.groups_rounded,
              background: Colors.white12,
              iconColor: anyCameraOn ? Colors.greenAccent : Colors.white,
              onPressed: () => setState(() => _participantsExpanded = true),
              tooltip: 'Participants (${_windows.length})',
              badgeCount: _windows.length,
              badgeColor: Colors.white,
            ),
            circleButton(
              icon: _roomCall.micOn ? Icons.mic : Icons.mic_off,
              background: _roomCall.micOn ? Colors.white12 : Colors.redAccent,
              iconColor: Colors.white,
              onPressed: _roomCall.isConnected ? () => _roomCall.toggleMic() : null,
              tooltip: _roomCall.micOn ? 'Mute mic' : 'Unmute mic',
            ),
            circleButton(
              icon: _roomCall.cameraOn ? Icons.videocam : Icons.videocam_off,
              background: _roomCall.cameraOn ? Colors.white12 : Colors.white24,
              iconColor: _roomCall.cameraOn ? Colors.white : Colors.white54,
              onPressed: _roomCall.isConnected ? () => _roomCall.toggleCamera() : null,
              tooltip: _roomCall.cameraOn ? 'Turn off camera' : 'Turn on camera',
            ),
            if (_roomCall.cameraOn)
              circleButton(
                icon: Icons.cameraswitch_rounded,
                background: Colors.white12,
                iconColor: Colors.white,
                onPressed: _roomCall.isConnected ? _switchCamera : null,
                tooltip: 'Switch camera',
              ),
            circleButton(
              icon: _roomCall.isScreenSharing ? Icons.stop_screen_share : Icons.screen_share_outlined,
              background: _roomCall.isScreenSharing ? Colors.tealAccent : Colors.white12,
              iconColor: _roomCall.isScreenSharing ? Colors.black : Colors.white,
              onPressed: _roomCall.isConnected ? _toggleScreenShare : null,
              tooltip: _roomCall.isScreenSharing
                  ? 'Stop presenting'
                  : (_roomCall.activePresenterId != null ? 'Someone else is presenting' : 'Present screen'),
            ),
            circleButton(
              icon: Icons.auto_awesome,
              background: Colors.tealAccent,
              iconColor: Colors.black,
              onPressed: _openAiToolsSheet,
              tooltip: 'Ask AI',
            ),
          ],
        ),
      ),
    );
  }

  // Shared height/spacing constants so the call control bar and the
  // drawing tool palette stack cleanly instead of overlapping.
  static const double _callBarApproxHeight = 64;
  static const double _barStackGap = 10;

  double _bottomCallBarBottomMargin(double bottomSafeInset) => 16 + bottomSafeInset;

  double _toolPaletteBottomMargin(double bottomSafeInset) =>
      _bottomCallBarBottomMargin(bottomSafeInset) + _callBarApproxHeight + _barStackGap;

  // ============================================================
  // ROOM LINK SHARE
  // ============================================================
  String get _roomShareLink => "https://learnscroll.app/study-room/${widget.conversationId}";

  void _shareRoomLink() {
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text("Share Study Room"),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text("Ye link bhejo taaki koi aur bhi is study room me join kar sake:"),
            const SizedBox(height: 12),
            SelectableText(_roomShareLink, style: const TextStyle(fontWeight: FontWeight.bold)),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text("Close")),
          ElevatedButton.icon(
            icon: const Icon(Icons.copy, size: 18),
            label: const Text("Copy Link"),
            onPressed: () {
              Clipboard.setData(ClipboardData(text: _roomShareLink));
              Navigator.pop(context);
              ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Link copied!")));
            },
          ),
        ],
      ),
    );
  }

  // ============================================================
  // ADD USER
  // ============================================================
  void _showAddUserDialog() {
    final controller = TextEditingController();
    showDialog(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text("Add User to Study Room"),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(labelText: "User ID", hintText: "e.g. 42"),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text("Cancel")),
          ElevatedButton(
            onPressed: () async {
              final userId = controller.text.trim();
              if (userId.isEmpty) return;
              Navigator.pop(dialogContext);
              await _addUser(userId);
            },
            child: const Text("Add"),
          ),
        ],
      ),
    );
  }

  Future<void> _addUser(String userId) async {
    try {
      await MessageApiService.addParticipantToConversation(widget.conversationId, userId);
      _sendRoomEvent('participant_added', {'userId': userId});
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("User added to the study room")));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Failed to add user: $e")));
      }
    }
  }

  // ============================================================
  // MEDIA FILE ATTACHMENTS
  // ============================================================
  Future<void> _pickAndLoadFile() async {
    // PPT/PPTX support hata diya gaya hai (koi native widget PPTX ko
    // render nahi kar sakta aur PDF-conversion backend abhi nahi hai),
    // isliye file picker me sirf PDF/image hi allowed hain.
    const typeGroup = XTypeGroup(
      label: 'documents_images',
      extensions: ['pdf', 'png', 'jpg', 'jpeg'],
    );
    final file = await openFile(acceptedTypeGroups: [typeGroup]);
    if (file == null) return;
    await _loadPickedOrCapturedFile(File(file.path));
  }

  // 🔥 NAYA — camera se seedha photo khichkar usi page pe daalne ke liye.
  // Baaki sab (upload, sync, pan/zoom) file-picker wale image jaisa hi
  // hota hai, isliye same shared helper (`_loadPickedOrCapturedFile`) use
  // hota hai — code duplicate nahi karna pada.
  Future<void> _captureFromCamera() async {
    final picked = await ImagePicker().pickImage(
      source: ImageSource.camera,
      imageQuality: 90,
    );
    if (picked == null) return;
    await _loadPickedOrCapturedFile(File(picked.path));
  }

  // 🔥 NAYA — file-picker aur camera-capture dono isi ek jagah aakar
  // milte hain, taaki upload/sync/pan-zoom logic sirf ek hi jagah maintain
  // karni pade.
  Future<void> _loadPickedOrCapturedFile(File pickedFile) async {
    final path = pickedFile.path;
    final lowerPath = path.toLowerCase();
    final isPdf = lowerPath.endsWith('.pdf');
    final isPpt = lowerPath.endsWith('.ppt') || lowerPath.endsWith('.pptx');

    if (isPpt) {
      // PPT-to-PDF backend conversion abhi implement nahi hai, isliye PPT
      // support hata diya — user ko seedha PDF/image select karne ko bola
      // jaata hai.
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('PPT files are not supported — please select a PDF or image instead.'),
            duration: Duration(seconds: 3),
          ),
        );
      }
      return;
    }

    // 🔧 FIX — pehle poori PDF ek hi whiteboard page ke andar SfPdfViewer
    // me (scrollable) daal di jaati thi, jisse poora document properly
    // padhna mushkil ho jaata tha. Ab har PDF page ko rasterize karke
    // apna khud ka WhiteboardPage banate hain — existing multi-page
    // system (page tabs) se hi user ek-ek page karke poora, clearly
    // padh sakta hai, jaise normal image pages me hota hai.
    if (isPdf) {
      await _loadPdfAsPages(pickedFile);
      return;
    }

    final pageId = _page.id;

    // Apne liye turant local preview (upload ka wait nahi karna) + Pan/Zoom
    // on, taaki poori file/document turant scroll ho sake. Annotate karne
    // ke liye user ab toolbar wale seedhe "hand" button se draw mode me
    // aa sakta hai.
    setState(() {
      _pageImagePaths[pageId] = path;
      _pagePdfPaths.remove(pageId);
      _panZoomMode = true;
    });

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Sharing file with everyone in the room...'), duration: Duration(seconds: 2)),
      );
    }

    // 🔥 NAYA — sirf local path set karna kaafi nahi hai, wo sirf isi
    // device pe kaam karta hai. Baaki sab participants (aur baad me join
    // karne wale) ko POORI file dikhane ke liye pehle backend pe upload
    // karo, phir uska URL page ke saath sync karo — existing chat-upload
    // endpoint (`MessageApiService.uploadFile`) hi reuse ho raha hai, koi
    // naya backend route nahi banana pada.
    try {
      final uploaded = await MessageApiService.uploadFile(File(path));
      final page = _findPage(pageId);
      if (page != null) {
        page.fileUrl = uploaded.fileUrl;
        page.fileType = 'image';
      }
      // Apna khud ka upload hai — dobara download karke overwrite karne ki
      // zaroorat nahi, already best-quality local copy maujood hai.
      _pageFileSourceUrl[pageId] = uploaded.fileUrl;
      _isDirty = true;

      _sendRoomEvent('load_page_file', {
        'pageId': pageId,
        'fileUrl': uploaded.fileUrl,
        'fileType': 'image',
      });

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('File shared — everyone can see it now. Tap the hand icon to draw on it.'),
            duration: Duration(seconds: 3),
          ),
        );
      }
    } catch (e) {
      developer.log('Study room file upload failed: $e');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Upload failed — only visible on your screen: $e')),
        );
      }
    }
  }

  // 🔥 NAYA — PDF ke har page ko ek image me rasterize karke, har page ko
  // apna khud ka WhiteboardPage bana dete hain. Pehla PDF page currently
  // khuli hui page me hi load hota hai; baaki har page ke liye naya page
  // banta hai (jaise "+" button se manually banate ho), taaki page-tabs
  // se navigate karke har page poora, screen-fit clearly padha ja sake —
  // ek hi lambi scrollable file me nahi.
  Future<void> _loadPdfAsPages(File pdfFile) async {
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Splitting PDF into pages...'), duration: Duration(seconds: 2)),
      );
    }

    List<int> pdfBytes;
    try {
      pdfBytes = await pdfFile.readAsBytes();
    } catch (e) {
      developer.log('Study room PDF read failed: $e');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not read the PDF: $e')),
        );
      }
      return;
    }

    final tempDir = await getTemporaryDirectory();
    final WhiteboardPage firstPage = _page;
    int pageNumber = 0;

    try {
      await for (final rasterPage in Printing.raster(Uint8List.fromList(pdfBytes), dpi: 150)) {
        pageNumber++;
        final pngBytes = await rasterPage.toPng();
        final imgPath =
            "${tempDir.path}/pdfpage_${DateTime.now().microsecondsSinceEpoch}_$pageNumber.png";
        await File(imgPath).writeAsBytes(pngBytes);

        final bool isFirst = pageNumber == 1;
        final WhiteboardPage targetPage =
            isFirst ? firstPage : WhiteboardPage(id: '${firstPage.id}_pdf_$pageNumber');

        setState(() {
          _pageImagePaths[targetPage.id] = imgPath;
          _pagePdfPaths.remove(targetPage.id);
          if (!isFirst && !_pages.any((p) => p.id == targetPage.id)) {
            _pages.add(targetPage);
            // Sirf jis page pe user abhi hai wahi "visited" mana jaata
            // hai — baaki naye bane PDF pages "unvisited" rehte hain jab
            // tak user khud unke tab pe tap na kare (blue dot dikhega).
            _unvisitedPageIds.add(targetPage.id);
          }
          _panZoomMode = true;
        });

        // Har page ka apna alag image upload + sync karo, taaki baaki sab
        // participants (aur baad me join karne wale) ko bhi poora
        // multi-page split dikhe, sirf ek hi device pe nahi.
        try {
          final uploaded = await MessageApiService.uploadFile(File(imgPath));
          setState(() {
            targetPage.fileUrl = uploaded.fileUrl;
            targetPage.fileType = 'image';
          });
          _pageFileSourceUrl[targetPage.id] = uploaded.fileUrl;
          if (isFirst) {
            _sendRoomEvent('load_page_file', {
              'pageId': targetPage.id,
              'fileUrl': uploaded.fileUrl,
              'fileType': 'image',
            });
          } else {
            _sendRoomEvent('add_page', {'page': targetPage.toJson()});
          }
        } catch (e) {
          developer.log('Study room PDF page $pageNumber upload failed: $e');
        }
      }
    } catch (e) {
      developer.log('Study room PDF rasterize failed: $e');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not split the PDF: $e')),
        );
      }
      return;
    }

    _isDirty = true;
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            pageNumber > 1
                ? 'PDF split into $pageNumber pages — use the page tabs above to read each one fully.'
                : 'PDF shared — everyone can see it now. Tap the hand icon to draw on it.',
          ),
          duration: const Duration(seconds: 3),
        ),
      );
    }
  }


  // 🔥 NAYA — koi doosra participant (ya restore-on-reopen) ke through
  // mila file URL yahan poora download hota hai aur local disk pe save
  // hota hai. Poore bytes ek hi baar me `writeAsBytes` hote hain, isliye
  // `SfPdfViewer.file()`/`Image.file()` POORA document render karta hai —
  // sirf pehla page ya ek screenshot nahi.
  Future<void> _downloadAndCachePageFile(String pageId, String fileUrl, String fileType) async {
    if (fileUrl.isEmpty) return;
    // Ye hi URL pehle se download ho chuka/ho raha hai to dobara kaam mat
    // karo (baar-baar 'load_page_file' aane par bhi duplicate download na
    // ho).
    if (_pageFileSourceUrl[pageId] == fileUrl) return;
    _pageFileSourceUrl[pageId] = fileUrl;

    try {
      final res = await http.get(Uri.parse(fileUrl));
      if (res.statusCode < 200 || res.statusCode >= 300) {
        throw Exception('HTTP ${res.statusCode}');
      }

      final isPdf = fileType == 'pdf' || fileUrl.toLowerCase().contains('.pdf');
      final ext = isPdf
          ? 'pdf'
          : (fileUrl.toLowerCase().contains('.png') ? 'png' : 'jpg');
      final dir = await getTemporaryDirectory();
      final localFile = File(
        '${dir.path}/study_room_${pageId}_${DateTime.now().millisecondsSinceEpoch}.$ext',
      );
      await localFile.writeAsBytes(res.bodyBytes);

      if (!mounted) return;
      setState(() {
        if (isPdf) {
          _pagePdfPaths[pageId] = localFile.path;
          _pageImagePaths.remove(pageId);
        } else {
          _pageImagePaths[pageId] = localFile.path;
          _pagePdfPaths.remove(pageId);
        }
      });
    } catch (e) {
      developer.log('Study room shared-file download failed: $e');
      // Retry allowed next time (add_page/restore/reconnect).
      _pageFileSourceUrl.remove(pageId);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not load the shared file: $e')),
        );
      }
    }
  }

  // 🔥 FIX — pehle sirf temp directory me save hota tha (getTemporaryDirectory),
  // jo phone ke Files/Downloads app se nahi dikhta. Ab media_download_service.dart
  // wala hi pattern: Android 11+ (API 30+) pe MANAGE_EXTERNAL_STORAGE, uske
  // niche normal storage permission, aur public Download/LearnScroll folder
  // me save karo. Permission na mile to app-sandboxed folder pe fallback,
  // taaki export kabhi crash na ho.
  Future<void> _exportAndDownloadAnnotatedFile() async {
    try {
      RenderRepaintBoundary boundary = _globalKey.currentContext!.findRenderObject() as RenderRepaintBoundary;
      ui.Image image = await boundary.toImage(pixelRatio: 3.0);
      ByteData? byteData = await image.toByteData(format: ui.ImageByteFormat.png);
      Uint8List pngBytes = byteData!.buffer.asUint8List();

      Directory targetDir;
      if (Platform.isAndroid) {
        final sdkInt = (await DeviceInfoPlugin().androidInfo).version.sdkInt;
        var granted = false;
        if (sdkInt >= 30) {
          var status = await Permission.manageExternalStorage.status;
          if (!status.isGranted) status = await Permission.manageExternalStorage.request();
          granted = status.isGranted;
        } else {
          var status = await Permission.storage.status;
          if (!status.isGranted) status = await Permission.storage.request();
          granted = status.isGranted;
        }
        if (granted) {
          targetDir = Directory("/storage/emulated/0/Download/flutter");
          if (!await targetDir.exists()) await targetDir.create(recursive: true);
        } else {
          targetDir = await getApplicationDocumentsDirectory();
        }
      } else {
        targetDir = await getApplicationDocumentsDirectory();
      }

      final file = File('${targetDir.path}/StudyRoom_Annotated_${DateTime.now().millisecondsSinceEpoch}.png');
      await file.writeAsBytes(pngBytes);

      // 🔥 NAYA — chat_screen ke media download jaisa hi, yahan bhi download
      // poora hone par app-wide system notification aani chahiye (study room
      // ke downloads MediaDownloadService se nahi guzarte, isliye wo
      // centralized notification yahan trigger nahi hoti — isliye explicitly
      // call karna zaroori hai).
      PushNotificationService.instance.showDownloadCompleteNotification(
        fileName: file.path.split('/').last,
        filePath: file.path,
      );

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Saved to Downloads/flutter')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Export Failed: $e')));
      }
    }
  }

  // ============================================================
  // 🔥 NAYA — "Download All Pages (PDF)": upar wala _exportAndDownloadAnnotatedFile
  // sirf ABHI khuli hui EK page ka PNG deta hai ("single page" download).
  // Ye naya wala pura session — Page 1 se lekar aakhri page tak, sab kuch
  // — ek hi multi-page PDF me deta hai. Kaam karne ka tarika: har page pe
  // ek-ek karke (turant) switch karta hai taaki wahi RepaintBoundary
  // (_globalKey) us page ka sahi content capture kare, uska PNG snapshot
  // leta hai, aur sabko ek `pw.Document()` me jodta hai. Aakhri me user
  // jis page pe tha, wahin wapas laa deta hai.
  // ============================================================
  Future<void> _exportAllPagesAsPdf() async {
    if (_pages.isEmpty || !mounted) return;
    final originalIndex = _currentPageIndex;

    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (_) => AlertDialog(
        backgroundColor: const Color(0xFF1E1E2C),
        content: Row(children: [
          const CircularProgressIndicator(color: Colors.blueAccent),
          const SizedBox(width: 16),
          Expanded(child: Text('Preparing PDF — 0/${_pages.length} pages...', style: const TextStyle(color: Colors.white))),
        ]),
      ),
    );

    try {
      final pdf = pw.Document();
      for (int i = 0; i < _pages.length; i++) {
        // 🔧 Page switch karke ek frame + thoda extra wait — taaki naya
        // page poora paint ho chuke (images/text/strokes sab render ho
        // chuke) tab hi RepaintBoundary se snapshot liya jaaye, warna
        // beech ka adhoora frame capture ho sakta hai.
        setState(() => _currentPageIndex = i);
        await WidgetsBinding.instance.endOfFrame;
        await Future.delayed(const Duration(milliseconds: 80));

        final boundary = _globalKey.currentContext!.findRenderObject() as RenderRepaintBoundary;
        final image = await boundary.toImage(pixelRatio: 2.5);
        final byteData = await image.toByteData(format: ui.ImageByteFormat.png);
        final pngBytes = byteData!.buffer.asUint8List();
        final pdfImage = pw.MemoryImage(pngBytes);

        pdf.addPage(
          pw.Page(
            pageFormat: PdfPageFormat.a4,
            build: (pwContext) => pw.Center(child: pw.Image(pdfImage, fit: pw.BoxFit.contain)),
          ),
        );
      }

      Directory targetDir;
      if (Platform.isAndroid) {
        final sdkInt = (await DeviceInfoPlugin().androidInfo).version.sdkInt;
        var granted = false;
        if (sdkInt >= 30) {
          var status = await Permission.manageExternalStorage.status;
          if (!status.isGranted) status = await Permission.manageExternalStorage.request();
          granted = status.isGranted;
        } else {
          var status = await Permission.storage.status;
          if (!status.isGranted) status = await Permission.storage.request();
          granted = status.isGranted;
        }
        if (granted) {
          targetDir = Directory("/storage/emulated/0/Download/flutter");
          if (!await targetDir.exists()) await targetDir.create(recursive: true);
        } else {
          targetDir = await getApplicationDocumentsDirectory();
        }
      } else {
        targetDir = await getApplicationDocumentsDirectory();
      }

      final file = File('${targetDir.path}/StudyRoom_AllPages_${DateTime.now().millisecondsSinceEpoch}.pdf');
      await file.writeAsBytes(await pdf.save());

      // 🔥 NAYA — chat_screen jaisa hi download-complete system notification.
      PushNotificationService.instance.showDownloadCompleteNotification(
        fileName: file.path.split('/').last,
        filePath: file.path,
      );

      if (mounted) setState(() => _currentPageIndex = originalIndex);
      if (mounted) {
        Navigator.pop(context); // loading dialog band karo
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Saved all ${_pages.length} pages as one PDF to Downloads/flutter')),
        );
      }
    } catch (e) {
      if (mounted) setState(() => _currentPageIndex = originalIndex);
      if (mounted) {
        Navigator.pop(context); // loading dialog band karo
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('PDF export failed: $e')));
      }
    }
  }

  // 🔥 NAYA — "Download Annotated Snapshot" hamesha ek flattened PNG
  // banata hai (chahe original PDF ho ya image), jo original file type ko
  // preserve nahi karta. Ye function alag se original file (jo load kiya
  // gaya tha) ko uske asli type me hi (.pdf ya .png/.jpg) Downloads me
  // copy kar deta hai — bina kisi cropping/annotation ke, poori file.
  Future<void> _downloadOriginalFile() async {
    final sourcePath = _loadedPdfPath ?? _loadedImagePath;
    if (sourcePath == null) return;

    try {
      Directory targetDir;
      if (Platform.isAndroid) {
        final sdkInt = (await DeviceInfoPlugin().androidInfo).version.sdkInt;
        var granted = false;
        if (sdkInt >= 30) {
          var status = await Permission.manageExternalStorage.status;
          if (!status.isGranted) status = await Permission.manageExternalStorage.request();
          granted = status.isGranted;
        } else {
          var status = await Permission.storage.status;
          if (!status.isGranted) status = await Permission.storage.request();
          granted = status.isGranted;
        }
        if (granted) {
          targetDir = Directory("/storage/emulated/0/Download/LearnScroll");
          if (!await targetDir.exists()) await targetDir.create(recursive: true);
        } else {
          targetDir = await getApplicationDocumentsDirectory();
        }
      } else {
        targetDir = await getApplicationDocumentsDirectory();
      }

      final originalName = sourcePath.split('/').last;
      final destPath = '${targetDir.path}/StudyRoom_${DateTime.now().millisecondsSinceEpoch}_$originalName';
      await File(sourcePath).copy(destPath);

      // 🔥 NAYA — chat_screen jaisa hi download-complete system notification.
      PushNotificationService.instance.showDownloadCompleteNotification(
        fileName: originalName,
        filePath: destPath,
      );

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Saved $originalName to Downloads/LearnScroll')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Download failed: $e')));
      }
    }
  }

  // ============================================================
  // AUTO-SAVE
  // ============================================================
  void _startAutoSave() {
    _autoSaveTimer = Timer.periodic(const Duration(seconds: 20), (_) {
      if (_isDirty) _saveBoardState();
    });
  }

  Future<void> _saveBoardState() async {
    _isDirty = false;
    try {
      await MessageApiService.saveStudyRoomState(
        widget.conversationId,
        {'pages': _pages.map((p) => p.toJson()).toList()},
      );
    } catch (e) {
      developer.log('Study room auto-save failed: $e');
    }
  }

  Future<void> _restoreBoardState() async {
    try {
      final data = await MessageApiService.getStudyRoomState(widget.conversationId);
      if (data == null) return;
      final pagesJson = data['pages'] as List?;
      if (pagesJson == null || pagesJson.isEmpty) return;
      if (!mounted) return;
      setState(() {
        _pages = pagesJson.map((p) => WhiteboardPage.fromJson(p)).toList();
        _currentPageIndex = 0;
      });

      // 🔥 NAYA — restored pages me se jinke paas already fileUrl hai
      // (koi presentation/PDF/image pehle se share ho chuki thi), unko
      // turant download karo — taaki reopen karne wale ya late-join karne
      // wale ko bhi POORI file mile, sirf naye events ka wait na karna
      // pade.
      for (final p in _pages) {
        if ((p.fileUrl ?? '').isNotEmpty) {
          _downloadAndCachePageFile(p.id, p.fileUrl!, p.fileType ?? 'file');
        }
      }
    } catch (e) {
      developer.log('Study room restore failed (endpoint might not exist yet): $e');
    }
  }

  // ============================================================
  // BUILD
  // ============================================================
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      key: _scaffoldKey,
      backgroundColor: const Color(0xFF1E1E2C),
      endDrawer: _buildChatDrawer(),
      // 🔥 NAYA — end drawer khulte hi (chahe icon-tap se ho ya edge-swipe
      // se) unread badge turant clear ho jaata hai.
      onEndDrawerChanged: (isOpen) {
        if (isOpen && _hasUnreadChat) {
          setState(() => _hasUnreadChat = false);
        }
      },
      appBar: AppBar(
        title: Text(widget.peerName ?? 'Group Study Room', style: const TextStyle(color: Colors.white)),
        backgroundColor: const Color(0xFF0D0D15),
        actions: [
          // Kept intentionally minimal: only "secondary/social" actions
          // live in the AppBar now (chat + sticker) plus the overflow
          // menu. Everything call-critical (mic / camera / screen-share)
          // has moved to the floating bottom call control bar — see
          // _buildBottomCallControlBar — Meet/Zoom style, big circular
          // buttons within thumb reach instead of cramped into the top bar.
          // 🔥 NAYA — WhatsApp jaisa hi unread blue dot: jab tak drawer nahi
          // khola, naya message aane par icon ke upar-right corner me dot
          // dikhta hai, aur tooltip me bhejne wale ka naam bhi show hota hai.
          Stack(
            clipBehavior: Clip.none,
            children: [
              IconButton(
                icon: const Icon(Icons.chat_bubble_outline, color: Colors.white),
                onPressed: () {
                  if (_hasUnreadChat) setState(() => _hasUnreadChat = false);
                  _scaffoldKey.currentState?.openEndDrawer();
                },
                tooltip: (_hasUnreadChat && _lastChatSenderName != null)
                    ? 'New message from $_lastChatSenderName'
                    : 'Quick Chat',
              ),
              if (_hasUnreadChat)
                Positioned(
                  top: 8,
                  right: 8,
                  child: IgnorePointer(
                    child: Container(
                      width: 10,
                      height: 10,
                      decoration: BoxDecoration(
                        color: Colors.blueAccent,
                        shape: BoxShape.circle,
                        border: Border.all(color: const Color(0xFF0D0D15), width: 1.5),
                      ),
                    ),
                  ),
                ),
            ],
          ),
          // 🔥 NAYA — quick sticker/reaction picker. Tap karke ek chota
          // grid khulta hai, jahan se sticker bhejte hi wo turant sabki
          // screen pe 3 second ke liye pop-up hoti hai.
          IconButton(
            icon: const Icon(Icons.emoji_emotions_outlined, color: Colors.white),
            onPressed: _openStickerPicker,
            tooltip: 'Send Sticker',
          ),
          PopupMenuButton<String>(
            icon: const Icon(Icons.more_vert, color: Colors.white),
            onSelected: (value) {
              switch (value) {
                case 'share':
                  _shareRoomLink();
                  break;
                case 'add_user':
                  _showAddUserDialog();
                  break;
                case 'attach':
                  _pickAndLoadFile();
                  break;
                case 'camera':
                  _captureFromCamera();
                  break;
                case 'download':
                  _exportAndDownloadAnnotatedFile();
                  break;
                case 'download_all_pdf':
                  _exportAllPagesAsPdf();
                  break;
                case 'download_original':
                  _downloadOriginalFile();
                  break;
                case 'timer':
                  _showTimerSheet();
                  break;
                case 'ruled_lines':
                  _toggleRuledLines();
                  break;
                case 'ruled_lines_style':
                  _showRuledLineStyleSheet();
                  break;
                case 'leave_session':
                  _leaveSession();
                  break;
                case 'end_session':
                  _endSessionForEveryone();
                  break;
              }
            },
            itemBuilder: (_) => [
              const PopupMenuItem(
                value: 'share',
                child: Row(children: [Icon(Icons.link), SizedBox(width: 8), Text('Share Room Link')]),
              ),
              const PopupMenuItem(
                value: 'add_user',
                child: Row(children: [Icon(Icons.person_add), SizedBox(width: 8), Text('Add User')]),
              ),
              const PopupMenuItem(
                value: 'attach',
                child: Row(children: [Icon(Icons.attach_file), SizedBox(width: 8), Text('Load PDF/PPT/Image')]),
              ),
              // 🔥 NAYA — seedha camera se photo khींचकर page pe daalne ke
              // liye. Gallery/file-picker se alag — turant capture karke
              // usi image-upload pipeline se sabko sync ho jaata hai.
              const PopupMenuItem(
                value: 'camera',
                child: Row(children: [Icon(Icons.camera_alt_outlined), SizedBox(width: 8), Text('Take Photo (Camera)')]),
              ),
              if (_loadedPdfPath != null || _loadedImagePath != null)
                const PopupMenuItem(
                  value: 'download_original',
                  child: Row(children: [
                    Icon(Icons.file_download_outlined),
                    SizedBox(width: 8),
                    Text('Download Original File'),
                  ]),
                ),
              const PopupMenuItem(
                value: 'download',
                child: Row(children: [Icon(Icons.download), SizedBox(width: 8), Text('Download This Page (Image)')]),
              ),
              // 🔥 NAYA — Page 1 se lekar aakhri page tak, SAB pages ek
              // hi PDF file me — upar wale se alag, jo sirf ABHI wali
              // ek page deta hai.
              const PopupMenuItem(
                value: 'download_all_pdf',
                child: Row(children: [Icon(Icons.picture_as_pdf_outlined), SizedBox(width: 8), Text('Download All Pages (PDF)')]),
              ),
              // 🔥 NAYA — notebook/copy jaisi ruled lines background, sirf
              // khaali page (koi PDF/image load na ho) pe hi maayne rakhta
              // hai, isliye sirf tabhi menu me dikhta hai.
              if (_loadedPdfPath == null && _loadedImagePath == null)
                PopupMenuItem(
                  value: 'ruled_lines',
                  child: Row(children: [
                    Icon(_showRuledLines ? Icons.check_box : Icons.check_box_outline_blank),
                    const SizedBox(width: 8),
                    const Text('Ruled Paper Lines'),
                  ]),
                ),
              // 🔥 NAYA — lines choti-badi (spacing) aur horizontal/
              // vertical/grid orientation badalne ke liye — sirf tab
              // dikhta hai jab ruled lines already ON hon.
              if (_loadedPdfPath == null && _loadedImagePath == null && _showRuledLines)
                const PopupMenuItem(
                  value: 'ruled_lines_style',
                  child: Row(children: [
                    Icon(Icons.tune),
                    SizedBox(width: 8),
                    Text('Line Style (size / direction)'),
                  ]),
                ),
              PopupMenuItem(
                value: 'timer',
                child: Row(children: [
                  Icon(_timer.isBreak ? Icons.local_cafe : Icons.timer),
                  const SizedBox(width: 8),
                  Text('Study Timer  ${_timerRemaining.inMinutes.remainder(60).toString().padLeft(2, '0')}:${_timerRemaining.inSeconds.remainder(60).toString().padLeft(2, '0')}'),
                ]),
              ),
              const PopupMenuDivider(),
              const PopupMenuItem(
                value: 'leave_session',
                child: Row(children: [
                  Icon(Icons.logout, color: Colors.white70),
                  SizedBox(width: 8),
                  Text('Leave Session'),
                ]),
              ),
              const PopupMenuItem(
                value: 'end_session',
                child: Row(children: [
                  Icon(Icons.exit_to_app, color: Colors.redAccent),
                  SizedBox(width: 8),
                  Text('End Session for Everyone', style: TextStyle(color: Colors.redAccent)),
                ]),
              ),
            ],
          ),
        ],
      ),
      body: Stack(
        children: [
          Column(
            children: [
              _buildPageTabs(),
              Expanded(child: _buildWhiteboardArea()),
            ],
          ),
          // Individual participant video windows used to float loose on
          // top of the whiteboard, which made board content hard to see.
          // Now no per-participant tile sits on the whiteboard — the
          // bottom call control bar has a single "Participants" button
          // (with an online-count badge) that opens everyone into a
          // full-screen grid on tap.
          _buildToolPalette(),
          // Meet/Zoom-style floating call control bar: mic, camera,
          // screen-share, Participants, and Ask AI all live here (thumb
          // reach, large circular buttons), not scattered across the
          // screen and not in the AppBar. It always sits directly below
          // the drawing tool palette so the two floating bars stack
          // cleanly instead of overlapping.
          _buildBottomCallControlBar(),
          _buildCallStatusBar(),
          // Opens full-screen when the Participants button in the
          // bottom bar is tapped. Local state only (`_participantsExpanded`)
          // — this overlay never opens on anyone else's screen.
          if (_participantsExpanded) _buildParticipantsExpandedOverlay(),
          // 🔥 NAYA — floating sticker/reaction bubbles. Body Stack ke
          // sabse upar wale layers me se ek hai taaki whiteboard, video
          // tiles, ya screen-share — sabke upar dikhe. Har bubble apne
          // khud ke 3-second lifecycle pe independently animate + hatti
          // hai (see _StickerBubble).
          if (_activeStickers.isNotEmpty) _buildStickerOverlay(),
          // Screen ke bilkul neeche ek patli si touch strip — yahan
          // haath rakhne/swipe karne par phone ka system nav bar 3
          // second ke liye dikh jaata hai, phir apne aap hide ho jaata
          // hai. Pen/pencil toolbar ke upar nahi aata (bahut patla hai).
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            height: 14,
            child: GestureDetector(
              behavior: HitTestBehavior.translucent,
              onVerticalDragStart: (_) => _showSystemNavTemporarily(),
              onTap: _showSystemNavTemporarily,
            ),
          ),
        ],
      ),
    );
  }

  // --- WHITEBOARD AREA (pan/zoom + drawing + shapes + text + notes) ---
  Widget _buildWhiteboardArea() {
    final isTextTool = _activeTool == ToolType.text;

    // 🔥 FIX — pehle `MediaQuery.of(context).size` (poore device screen ka
    // size, AppBar/page-tabs bar ka height minus kiye bina) use karke
    // canvas size fix kiya jaata tha. Isse actual available area se canvas
    // bada/chota ho jaata tha aur content crop ho jaata tha. Ab
    // LayoutBuilder se jo asli constraints is Expanded area ko mile hain
    // wahi use karte hain.
    return LayoutBuilder(
      builder: (context, constraints) {
        final areaSize = Size(constraints.maxWidth, constraints.maxHeight);
        return Listener(
          onPointerDown: (_) => setState(() => _activePointers++),
          onPointerUp: (_) => setState(() => _activePointers = (_activePointers - 1).clamp(0, 10)),
          onPointerCancel: (_) => setState(() => _activePointers = (_activePointers - 1).clamp(0, 10)),
          child: InteractiveViewer(
            transformationController: _transformCtrl,
            minScale: 0.5,
            maxScale: 4,
            // 🔥 FIX — boundaryMargin pehle set hi nahi tha, jiski wajah se
            // default EdgeInsets.zero use ho raha tha — matlab content
            // apne hi bounds ke andar "trapped" tha, edges tak pan/scroll
            // nahi ho pata tha. Bina boundary ke poora file/canvas
            // freely pan/scroll/zoom ho sake, isliye ab infinite margin.
            boundaryMargin: const EdgeInsets.all(double.infinity),
            // 🔥 FIX — PDF load hone par pehle ye outer InteractiveViewer
            // bhi pan/scale enabled rehta tha, jo SfPdfViewer ke apne
            // internal vertical page-scroll se gesture-arena me takra
            // jaata tha — result: sirf page 1 static dikhta tha, neeche
            // scroll hi nahi hota tha (touch outer canvas le leta tha,
            // PDF viewer tak pahunchta hi nahi tha). Ab jab PDF loaded ho,
            // outer canvas ka pan/zoom poori tarah band — SfPdfViewer khud
            // apna scroll + pinch-zoom sambhalta hai (uska apna built-in
            // hai), taaki poori multi-page file properly scroll ho sake.
            panEnabled: _loadedPdfPath == null && (_panZoomMode || _activePointers >= 2),
            scaleEnabled: _loadedPdfPath == null && (_panZoomMode || _activePointers >= 2),
            child: SizedBox(
              width: areaSize.width,
              height: areaSize.height,
              child: RepaintBoundary(
                key: _globalKey,
                child: Stack(
                  children: [
                    if (_onPresentationPage && _roomCall.activePresentationTrack != null)
                      // 🔥 NAYA — live shared screen background ke roop
                      // me. Neeche wala CustomPaint/GestureDetector layer
                      // bilkul waisa hi hai jaisa PDF/image ke upar hota
                      // hai, isliye pen/marker/shapes/color sab already
                      // isi ke upar kaam karte hain — koi alag drawing
                      // path nahi likhna pada.
                      Positioned.fill(
                        child: VideoTrackRenderer(_roomCall.activePresentationTrack!),
                      )
                    else if (_onPresentationPage)
                      // Presentation page khuli hai lekin abhi koi live
                      // track nahi (presenter ne rok diya) — annotations
                      // dikhte rehte hain, bas background plain reh jaata
                      // hai (jaise koi background hi na ho).
                      Container(color: Colors.black87)
                    else if (_loadedPdfPath != null)
                      // SfPdfViewer apna khud ka internal vertical scroll
                      // sambhalta hai (multi-page). Poori width/height de
                      // rahe hain taaki poori file open ho, kahin se crop
                      // na ho.
                      // 🔧 FIX — pehle SfPdfViewer seedha yahan render hota
                      // tha bina kisi background ke, isliye jab PDF load ho
                      // raha ho ya page ke around jagah bache to peeche ka
                      // (dark) Scaffold background jhalak jaata tha — black
                      // background dikhta tha. Ab ek explicit white
                      // Container ke andar wrap kiya, jaise doc/paper hota
                      // hai wapar hamesha safed rahega.
                      Container(
                        color: Colors.white,
                        child: SfPdfViewer.file(
                          File(_loadedPdfPath!),
                          canShowScrollHead: true,
                          canShowScrollStatus: true,
                          enableDoubleTapZooming: true,
                        ),
                      )
                    else if (_loadedImagePath != null)
                      // 🔧 FIX — ye asli culprit thi "PDF/file ka background
                      // black ho jaata hai" wale issue ki: PDF pages
                      // `_loadPdfAsPages` me rasterize hoke isi
                      // `_loadedImagePath` path se yahan dikhte hain, aur
                      // unke PNG me transparent alpha hota hai un jagah jahan
                      // PDF page pe kuch draw nahi hua tha. Pehle seedha
                      // `Image.file` ek plain `Positioned.fill` ke andar tha —
                      // koi background hi nahi tha, isliye transparent hissa
                      // peeche wale dark Scaffold background (0xFF1E1E2C) se
                      // hoke black/dark dikhta tha. Ab SfPdfViewer wale case
                      // jaisa hi ek explicit white Container wrap kar diya,
                      // taaki transparent areas hamesha ek asli page/paper ki
                      // tarah white dikhein, chahe PDF ho ya koi transparent
                      // PNG/sticker.
                      Positioned.fill(
                        child: Container(
                          color: Colors.white,
                          child: Image.file(File(_loadedImagePath!), fit: BoxFit.contain),
                        ),
                      )
                    else
                      // 🔥 NAYA — khaali page ka default background ab ya to
                      // plain white hai (jaisa pehle tha) ya, toggle on karne
                      // par, notebook jaisi ruled lines wala paper.
                      Container(
                        color: Colors.white,
                        child: _showRuledLines
                            ? CustomPaint(painter: _RuledPaperPainter(_currentRuledStyle), size: Size.infinite)
                            : null,
                      ),

                GestureDetector(
                  behavior: HitTestBehavior.translucent,
                  onPanStart: (_panZoomMode || isTextTool) ? null : _onPanStart,
                  onPanUpdate: (_panZoomMode || isTextTool) ? null : _onPanUpdate,
                  onPanEnd: (_panZoomMode || isTextTool) ? null : _onPanEnd,
                  onTapUp: (_panZoomMode || !isTextTool) ? null : _onCanvasTapForText,
                  child: CustomPaint(
                    painter: WhiteboardPainter(
                      strokes: _page.strokes,
                      shapes: _page.shapes,
                      previewShape: _previewShape(),
                    ),
                    size: Size.infinite,
                  ),
                ),

                ..._page.texts.map((t) => _buildTextWidget(t)),
                ..._page.stickyNotes.map((note) => _buildStickyNoteWidget(note)),
              ],
            ),
          ),
        ),
      ),
    );
      },
    );
  }

  // --- PAGE TABS ---
  Widget _buildPageTabs() {
    final hasUnvisitedPages = _unvisitedPageIds.isNotEmpty;
    return Container(
      height: 44,
      color: const Color(0xFF0D0D15),
      child: Row(
        children: [
          Expanded(
            child: ListView.builder(
              scrollDirection: Axis.horizontal,
              itemCount: _pages.length,
              itemBuilder: (context, index) {
                final selected = index == _currentPageIndex;
                final isUnvisited = _unvisitedPageIds.contains(_pages[index].id);
                return GestureDetector(
                  onTap: () => setState(() {
                    _currentPageIndex = index;
                    // 🔥 NAYA — tap karte hi ye page "visited" ho jaata hai,
                    // uska blue dot hat jaata hai.
                    _unvisitedPageIds.remove(_pages[index].id);
                  }),
                  child: Container(
                    margin: const EdgeInsets.symmetric(horizontal: 4, vertical: 6),
                    child: Stack(
                      clipBehavior: Clip.none,
                      children: [
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                          decoration: BoxDecoration(
                            color: selected ? Colors.blueAccent : Colors.white10,
                            borderRadius: BorderRadius.circular(16),
                          ),
                          child: Text(
                            'Page ${index + 1}',
                            style: TextStyle(
                              color: Colors.white,
                              fontWeight: selected ? FontWeight.bold : FontWeight.normal,
                              fontSize: 12,
                            ),
                          ),
                        ),
                        // 🔥 NAYA — is page pe abhi tak navigate nahi kiya —
                        // WhatsApp jaisa hi unread blue dot.
                        if (isUnvisited)
                          Positioned(
                            top: -2,
                            right: -2,
                            child: Container(
                              width: 9,
                              height: 9,
                              decoration: BoxDecoration(
                                color: Colors.blueAccent,
                                shape: BoxShape.circle,
                                border: Border.all(color: const Color(0xFF0D0D15), width: 1.5),
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
          // 🔥 NAYA — jis icon se page add hota hai usi pe blue dot: batata
          // hai ki koi naya page bana hai jo abhi tak dekha nahi gaya
          // (jaise chat list me unread message ka blue dot).
          Stack(
            clipBehavior: Clip.none,
            children: [
              IconButton(
                icon: const Icon(Icons.add_box_outlined, color: Colors.white),
                tooltip: 'Add Page',
                onPressed: _addPage,
              ),
              if (hasUnvisitedPages)
                Positioned(
                  top: 6,
                  right: 6,
                  child: IgnorePointer(
                    child: Container(
                      width: 10,
                      height: 10,
                      decoration: BoxDecoration(
                        color: Colors.blueAccent,
                        shape: BoxShape.circle,
                        border: Border.all(color: const Color(0xFF0D0D15), width: 1.5),
                      ),
                    ),
                  ),
                ),
            ],
          ),
          IconButton(
            icon: const Icon(Icons.delete_outline, color: Colors.redAccent),
            tooltip: 'Delete Page',
            onPressed: _pages.length > 1 ? _removeCurrentPage : null,
          ),
        ],
      ),
    );
  }

  // --- TIMER SHEET (3-dot menu -> Study Timer) ---
  // 🔥 NAYA — timer ab screen pe hamesha nahi dikhta, sirf 3-dot menu se
  // khulta hai. Sheet apna khud ka 1-second refresh rakhta hai taaki
  // countdown live update ho (parent widget bina open kiye bhi timer
  // background me chalta rehta hai — sirf display yahan hota hai).
  void _showTimerSheet() {
    // Ye Timer sheet ke bahar declare hai (StatefulBuilder ke andar
    // nahi), taaki har rebuild pe naya periodic Timer na bane — sirf ek
    // hi ticker chale jab tak sheet khula hai, aur band hote hi cancel ho.
    Timer? refreshTicker;
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF1C1C2E),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (sheetContext) {
        return StatefulBuilder(
          builder: (context, setSheetState) {
            refreshTicker ??= Timer.periodic(const Duration(seconds: 1), (_) {
              setSheetState(() {});
            });

            final mins = _timerRemaining.inMinutes.remainder(60).toString().padLeft(2, '0');
            final secs = _timerRemaining.inSeconds.remainder(60).toString().padLeft(2, '0');

            return Padding(
              padding: const EdgeInsets.fromLTRB(20, 20, 20, 32),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(_timer.isBreak ? Icons.local_cafe : Icons.timer, color: Colors.white, size: 22),
                      const SizedBox(width: 8),
                      Text(
                        _timer.isBreak ? 'Break' : 'Focus',
                        style: const TextStyle(color: Colors.white70, fontSize: 14),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '$mins:$secs',
                    style: const TextStyle(color: Colors.white, fontSize: 44, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 16),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      IconButton(
                        icon: Icon(_timer.isRunning ? Icons.pause_circle : Icons.play_circle,
                            color: Colors.white, size: 40),
                        onPressed: () {
                          _toggleTimer();
                          setSheetState(() {});
                        },
                      ),
                      const SizedBox(width: 12),
                      IconButton(
                        icon: const Icon(Icons.replay_circle_filled, color: Colors.white70, size: 40),
                        onPressed: () {
                          _resetTimer();
                          setSheetState(() {});
                        },
                      ),
                      const SizedBox(width: 12),
                      IconButton(
                        icon: const Icon(Icons.settings, color: Colors.white70, size: 32),
                        onPressed: () async {
                          await _configureTimer();
                          setSheetState(() {});
                        },
                      ),
                    ],
                  ),
                ],
              ),
            );
          },
        );
      },
    ).whenComplete(() {
      // Sheet band ho -> apna local refresh-ticker cancel karo. Asli
      // countdown (_timerTicker) parent-level pe independently chalta
      // rehta hai — sheet sirf display karta hai, timer ko control nahi.
      refreshTicker?.cancel();
    });
  }

  // --- QUICK CHAT DRAWER ---
  Widget _buildChatDrawer() {
    return Drawer(
      backgroundColor: const Color(0xFF1E1E2C),
      child: SafeArea(
        child: Column(
          children: [
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(16),
              color: const Color(0xFF0D0D15),
              child: const Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(Icons.chat_bubble, color: Colors.white),
                      SizedBox(width: 8),
                      Text('Quick Chat', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
                    ],
                  ),
                  SizedBox(height: 2),
                  Text(
                    'Temporary — cleared when this room closes',
                    style: TextStyle(color: Colors.white54, fontSize: 11),
                  ),
                ],
              ),
            ),
            Expanded(
              child: ListView.builder(
                padding: const EdgeInsets.all(12),
                itemCount: _quickChatMessages.length,
                itemBuilder: (context, index) {
                  final m = _quickChatMessages[index];
                  final isMe = m.senderId == widget.currentUserId;
                  return Align(
                    alignment: isMe ? Alignment.centerRight : Alignment.centerLeft,
                    child: Container(
                      margin: const EdgeInsets.symmetric(vertical: 4),
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.6),
                      decoration: BoxDecoration(
                        color: isMe ? Colors.blueAccent : Colors.white24,
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          if (!isMe)
                            Padding(
                              padding: const EdgeInsets.only(bottom: 2),
                              child: Text(
                                m.senderName,
                                style: const TextStyle(color: Colors.white70, fontSize: 10, fontWeight: FontWeight.bold),
                              ),
                            ),
                          Text(m.text, style: const TextStyle(color: Colors.white)),
                        ],
                      ),
                    ),
                  );
                },
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(8),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _chatInputController,
                      style: const TextStyle(color: Colors.white),
                      decoration: const InputDecoration(
                        hintText: 'Message...',
                        hintStyle: TextStyle(color: Colors.white38),
                        border: OutlineInputBorder(),
                      ),
                      onSubmitted: (_) => _sendQuickChat(),
                    ),
                  ),
                  IconButton(icon: const Icon(Icons.send, color: Colors.blueAccent), onPressed: _sendQuickChat),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  // --- PARTICIPANTS: FULL-SCREEN EXPANDED GRID ---
  // Opens when the Participants button in the bottom bar is tapped.
  // Only on this user's screen (local `_participantsExpanded` state) —
  // no socket broadcast happens, so it has no effect on anyone else's
  // screen. Each joined participant gets a tile — live video for anyone
  // with camera ON, profile picture for anyone with it OFF.
  Widget _buildParticipantsExpandedOverlay() {
    final focused = _focusedWindowOrNull();
    return Positioned.fill(
      child: Material(
        color: const Color(0xFF0F0F11).withOpacity(0.97),
        child: SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                child: Row(
                  children: [
                    const Icon(Icons.groups_rounded, color: Colors.white70),
                    const SizedBox(width: 8),
                    Text(
                      'Participants (${_windows.length})',
                      style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
                    ),
                    const Spacer(),
                    if (focused != null)
                      IconButton(
                        tooltip: 'Back to grid',
                        icon: const Icon(Icons.grid_view_rounded, color: Colors.white70),
                        onPressed: () => setState(() => _focusedParticipantId = null),
                      ),
                    IconButton(
                      tooltip: 'Close',
                      icon: const Icon(Icons.close, color: Colors.white70),
                      // Only collapses the overlay — whiteboard and call
                      // both keep running, nothing disconnects.
                      onPressed: () => setState(() {
                        _participantsExpanded = false;
                        _focusedParticipantId = null;
                      }),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: focused != null
                    ? _buildSpotlightView(focused)
                    : Padding(
                        // Grid spacing kept tight, especially on the
                        // 2-column layout (smaller phones) — tiles sit
                        // close together with minimal wasted space.
                        padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
                        child: LayoutBuilder(
                          builder: (context, constraints) {
                            final crossAxisCount = constraints.maxWidth > 900
                                ? 4
                                : constraints.maxWidth > 600
                                    ? 3
                                    : 2;
                            return GridView.builder(
                              gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                                crossAxisCount: crossAxisCount,
                                crossAxisSpacing: 6,
                                mainAxisSpacing: 6,
                                childAspectRatio: 1.05,
                              ),
                              itemCount: _windows.length,
                              itemBuilder: (context, index) => _buildParticipantGridTile(_windows[index]),
                            );
                          },
                        ),
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  // --- PARTICIPANTS: SPOTLIGHT VIEW ---
  // One participant large (~70% of the overlay height) so their video is
  // actually watchable, with everyone else in a horizontally scrollable
  // strip underneath (~30%) — tap any of them to bring them into focus
  // instead. The strip scrolls rather than growing, so it stays out of
  // the way even with a large room. When the focused tile is yourself,
  // mic/camera/switch-camera controls sit on the video itself so you
  // don't have to back out to the whiteboard's bottom bar to use them.
  Widget _buildSpotlightView(UserProfileWindowModel focused) {
    final isSelf = focused.userId == widget.currentUserId;
    final videoTrack = isSelf ? _roomCall.localVideoTrack : _roomCall.remoteVideoTracks[focused.userId];
    final cameraIsOn = isSelf ? (_roomCall.cameraOn && videoTrack != null) : videoTrack != null;
    final micIsOn = isSelf ? _roomCall.micOn : (_roomCall.remoteMicOn[focused.userId] ?? false);
    final others = _windows.where((w) => w.userId != focused.userId).toList();

    return Column(
      children: [
        Expanded(
          flex: others.isEmpty ? 10 : 7,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(10, 4, 10, 6),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(16),
              child: Container(
                color: const Color(0xFF2A2A3C),
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    cameraIsOn
                        ? VideoTrackRenderer(videoTrack!)
                        : Center(
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                CircleAvatar(
                                  radius: 48,
                                  backgroundColor: Colors.white24,
                                  backgroundImage: focused.avatarUrl != null ? NetworkImage(focused.avatarUrl!) : null,
                                  child: focused.avatarUrl == null
                                      ? const Icon(Icons.person, size: 44, color: Colors.white)
                                      : null,
                                ),
                                const SizedBox(height: 12),
                                Text(
                                  focused.displayName,
                                  style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
                                ),
                              ],
                            ),
                          ),
                    Positioned(
                      left: 14,
                      bottom: 14,
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                        decoration: BoxDecoration(
                          color: Colors.black.withOpacity(0.55),
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(micIsOn ? Icons.mic : Icons.mic_off,
                                size: 14, color: micIsOn ? Colors.white : Colors.redAccent),
                            const SizedBox(width: 6),
                            Text(
                              isSelf ? '${focused.displayName} (You)' : focused.displayName,
                              style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                            ),
                          ],
                        ),
                      ),
                    ),
                    Positioned(top: 12, right: 12, child: _buildActiveDot(isSelf: isSelf)),
                    // Self-only controls — you can only toggle your own
                    // mic/camera, never another participant's.
                    if (isSelf)
                      Positioned(
                        right: 12,
                        bottom: 12,
                        child: Row(
                          children: [
                            _buildSpotlightControlButton(
                              icon: micIsOn ? Icons.mic : Icons.mic_off,
                              active: micIsOn,
                              tooltip: micIsOn ? 'Mute mic' : 'Unmute mic',
                              onTap: _roomCall.isConnected ? () => _roomCall.toggleMic() : null,
                            ),
                            const SizedBox(width: 8),
                            _buildSpotlightControlButton(
                              icon: cameraIsOn ? Icons.videocam : Icons.videocam_off,
                              active: cameraIsOn,
                              tooltip: cameraIsOn ? 'Turn off camera' : 'Turn on camera',
                              onTap: _roomCall.isConnected ? () => _roomCall.toggleCamera() : null,
                            ),
                            if (cameraIsOn) ...[
                              const SizedBox(width: 8),
                              _buildSpotlightControlButton(
                                icon: Icons.cameraswitch_rounded,
                                active: true,
                                tooltip: 'Switch camera',
                                onTap: _switchCamera,
                              ),
                            ],
                          ],
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ),
        if (others.isNotEmpty)
          Expanded(
            flex: 3,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(10, 0, 10, 10),
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: others.length,
                separatorBuilder: (_, __) => const SizedBox(width: 8),
                itemBuilder: (context, index) => SizedBox(
                  width: 110,
                  child: _buildParticipantGridTile(others[index]),
                ),
              ),
            ),
          ),
      ],
    );
  }

  // Small circular control button used on the spotlight video itself
  // (mic / camera / switch-camera) — same visual language as the bottom
  // call control bar's circleButton, just sized for sitting on a video
  // tile instead of floating on its own.
  Widget _buildSpotlightControlButton({
    required IconData icon,
    required bool active,
    required String tooltip,
    required VoidCallback? onTap,
  }) {
    return Tooltip(
      message: tooltip,
      child: Material(
        color: active ? Colors.white24 : Colors.redAccent,
        shape: const CircleBorder(),
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: onTap,
          child: Container(
            width: 40,
            height: 40,
            alignment: Alignment.center,
            child: Icon(icon, color: Colors.white, size: 20),
          ),
        ),
      ),
    );
  }

  // --- PARTICIPANTS: SINGLE GRID/STRIP TILE ---
  // Reuses the same video/avatar rendering in both the grid and the
  // spotlight strip. Tapping any tile focuses that participant into the
  // spotlight view (see `_buildSpotlightView`).
  Widget _buildParticipantGridTile(UserProfileWindowModel win) {
    final isSelf = win.userId == widget.currentUserId;
    final videoTrack = isSelf ? _roomCall.localVideoTrack : _roomCall.remoteVideoTracks[win.userId];
    final cameraIsOn = isSelf ? (_roomCall.cameraOn && videoTrack != null) : videoTrack != null;
    final micIsOn = isSelf ? _roomCall.micOn : (_roomCall.remoteMicOn[win.userId] ?? false);

    return InkWell(
      borderRadius: BorderRadius.circular(12),
      onTap: () => setState(() => _focusedParticipantId = win.userId),
      child: Container(
        clipBehavior: Clip.antiAlias,
        decoration: BoxDecoration(
          color: const Color(0xFF2A2A3C),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: cameraIsOn ? Colors.greenAccent : Colors.white24, width: 1.5),
        ),
        child: Stack(
          fit: StackFit.expand,
          children: [
            cameraIsOn
                ? VideoTrackRenderer(videoTrack!)
                : Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        CircleAvatar(
                          radius: 28,
                          backgroundColor: Colors.white24,
                          backgroundImage: win.avatarUrl != null ? NetworkImage(win.avatarUrl!) : null,
                          child: win.avatarUrl == null ? const Icon(Icons.person, color: Colors.white) : null,
                        ),
                        const SizedBox(height: 6),
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 6),
                          child: Text(
                            win.displayName,
                            textAlign: TextAlign.center,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.bold),
                          ),
                        ),
                      ],
                    ),
                  ),
            Positioned(
              left: 6,
              bottom: 6,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: Colors.black.withOpacity(0.5),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(micIsOn ? Icons.mic : Icons.mic_off,
                        size: 11, color: micIsOn ? Colors.white : Colors.redAccent),
                    if (cameraIsOn) ...[
                      const SizedBox(width: 3),
                      Flexible(
                        child: Text(
                          win.displayName,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.w600),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
            // Top-right green dot: this participant is actively connected
            // right now (self uses `_roomCall`'s real connection state,
            // others stay in `_windows` only while actively in the LiveKit
            // room).
            Positioned(top: 6, right: 6, child: _buildActiveDot(isSelf: isSelf)),
          ],
        ),
      ),
    );
  }

  // 🔥 NAYA — chota "active in room" status dot. Self ke liye asli connection
  // state (connecting = amber, connected = green, error/disconnected = grey)
  // dikhata hai; doosre participants ke liye green — kyunki unka window
  // sirf tab tak `_windows` me rehta hai jab tak wo LiveKit room me
  // actively joined hain (leave hote hi `_onRemoteParticipantLeft` window
  // hata deta hai).
  Widget _buildActiveDot({required bool isSelf}) {
    Color color;
    if (isSelf) {
      color = _roomCall.isConnected
          ? Colors.greenAccent
          : _roomCall.isConnecting
              ? Colors.amber
              : Colors.white38;
    } else {
      color = Colors.greenAccent;
    }
    return Container(
      width: 12,
      height: 12,
      decoration: BoxDecoration(
        color: color,
        shape: BoxShape.circle,
        border: Border.all(color: const Color(0xFF2A2A3C), width: 2),
      ),
    );
  }

  // --- FLOATING WINDOW COMPONENT ---
  // 🔥 NAYA — Ye ab sirf ek static avatar box nahi raha. Google Meet
  // jaisa: jis participant (khud ya koi doosra) ka camera ON hai uska
  // LIVE video feed yahan dikhta hai; jiska OFF hai uska profile
  // picture (avatar) dikhta hai — bilkul Meet tile jaisa.
  Widget _buildFloatingWindowWidget(UserProfileWindowModel win) {
    final isSelf = win.userId == widget.currentUserId;
    final videoTrack = isSelf ? _roomCall.localVideoTrack : _roomCall.remoteVideoTracks[win.userId];
    final cameraIsOn = isSelf ? (_roomCall.cameraOn && videoTrack != null) : videoTrack != null;
    final micIsOn = isSelf ? _roomCall.micOn : (_roomCall.remoteMicOn[win.userId] ?? false);

    return Positioned(
      left: win.position.dx,
      top: win.position.dy,
      child: GestureDetector(
        onTap: () => setState(() => win.zIndex = ++_topZIndex),
        onPanUpdate: (details) {
          setState(() => win.position += details.delta);
          _sendRoomEvent('update_window', win.toJson());
        },
        child: Material(
          elevation: win.zIndex.toDouble(),
          borderRadius: BorderRadius.circular(12),
          child: Container(
            width: win.size.width,
            height: win.size.height,
            decoration: BoxDecoration(
              color: const Color(0xFF2A2A3C),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: cameraIsOn ? Colors.greenAccent : Colors.blueAccent,
                width: 1.5,
              ),
            ),
            child: Stack(
              children: [
                Positioned.fill(
                  child: cameraIsOn
                      ? ClipRRect(
                          borderRadius: BorderRadius.circular(10.5),
                          child: VideoTrackRenderer(videoTrack!),
                        )
                      : Center(
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              CircleAvatar(
                                radius: win.size.width * 0.2,
                                backgroundImage: win.avatarUrl != null ? NetworkImage(win.avatarUrl!) : null,
                                child: win.avatarUrl == null ? const Icon(Icons.person, color: Colors.white) : null,
                              ),
                              const SizedBox(height: 6),
                              Text(
                                win.displayName,
                                style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.bold),
                              ),
                            ],
                          ),
                        ),
                ),
                // Camera ON hote hue bhi naam + mic-status chhota sa
                // bottom-left overlay me dikhta hai (jaise Meet tiles pe).
                if (cameraIsOn)
                  Positioned(
                    left: 6,
                    bottom: 6,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(
                        color: Colors.black.withOpacity(0.5),
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(micIsOn ? Icons.mic : Icons.mic_off,
                              size: 11, color: micIsOn ? Colors.white : Colors.redAccent),
                          const SizedBox(width: 3),
                          Text(win.displayName,
                              style: const TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.w600)),
                        ],
                      ),
                    ),
                  )
                else
                  Positioned(
                    top: 6,
                    left: 6,
                    child: Icon(micIsOn ? Icons.mic : Icons.mic_off,
                        size: 14, color: micIsOn ? Colors.white70 : Colors.redAccent),
                  ),
                // 🔥 NAYA — active-in-room green/amber dot, jaisa grid tile me.
                Positioned(top: 6, right: 6, child: _buildActiveDot(isSelf: isSelf)),
                Positioned(
                  right: 0,
                  bottom: 0,
                  child: GestureDetector(
                    onPanUpdate: (details) {
                      setState(() {
                        win.size = Size(
                          (win.size.width + details.delta.dx).clamp(100.0, 400.0),
                          (win.size.height + details.delta.dy).clamp(100.0, 400.0),
                        );
                      });
                      _sendRoomEvent('update_window', win.toJson());
                    },
                    child: const Padding(
                      padding: EdgeInsets.all(4.0),
                      child: Icon(Icons.open_in_full, size: 14, color: Colors.white70),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // --- TEXT ELEMENT WIDGET ---
  Widget _buildTextWidget(TextElement t) {
    return Positioned(
      left: t.position.dx,
      top: t.position.dy,
      child: GestureDetector(
        onPanUpdate: (details) {
          setState(() {
            t.position += details.delta;
            _isDirty = true;
          });
          _sendRoomEvent('add_text', {'text': t.toJson(), 'pageId': _page.id});
        },
        onDoubleTap: () => _editTextElement(t),
        child: Text(
          t.text,
          style: TextStyle(color: t.color, fontSize: t.fontSize, fontWeight: FontWeight.w600),
        ),
      ),
    );
  }

  // --- STICKY NOTE WIDGET ---
  Widget _buildStickyNoteWidget(StickyNoteModel note) {
    return Positioned(
      left: note.position.dx,
      top: note.position.dy,
      child: GestureDetector(
        onPanUpdate: (details) {
          setState(() {
            note.position += details.delta;
            _isDirty = true;
          });
          _sendRoomEvent('add_sticky_note', {'note': note.toJson(), 'pageId': _page.id});
        },
        child: Container(
          width: 140,
          height: 140,
          padding: const EdgeInsets.all(8),
          decoration: BoxDecoration(
            color: note.color,
            borderRadius: BorderRadius.circular(8),
            boxShadow: const [BoxShadow(color: Colors.black26, blurRadius: 4)],
          ),
          child: Focus(
            onFocusChange: (hasFocus) {
              if (!hasFocus) {
                _isDirty = true;
                _sendRoomEvent('add_sticky_note', {'note': note.toJson(), 'pageId': _page.id});
              }
            },
            child: TextField(
              controller: TextEditingController(text: note.text),
              maxLines: null,
              style: const TextStyle(fontSize: 12, color: Colors.black),
              decoration: const InputDecoration(border: InputBorder.none, hintText: 'Type note...'),
              onChanged: (val) => note.text = val,
            ),
          ),
        ),
      ),
    );
  }

  void _addStickyNote() {
    final note = StickyNoteModel(
      id: const Uuid().v4(),
      userId: widget.currentUserId,
      text: 'New Note',
      position: const Offset(100, 100),
      color: Colors.yellow.shade200,
    );
    setState(() {
      _page.stickyNotes.add(note);
      _recordMyAction(_BoardActionType.stickyNote, refId: note.id);
      _isDirty = true;
    });
    _sendRoomEvent('add_sticky_note', {'note': note.toJson(), 'pageId': _page.id});
  }

  // ============================================================
  // AI SUMMARY NOTES + QUIZ
  // ------------------------------------------------------------
  // Board pe (sabhi pages ke) text elements aur sticky notes ke text
  // ko ikattha karke AI ko bhejte hain — wahi "jo maine likha hai" wala
  // content. AiStudyService (services/ai_study_service.dart) usse ek
  // summary ya ek chhota quiz bana kar wapas bhejta hai. NOTE: usme
  // backend endpoint abhi ek naya route hai (/message/study-room/ai-tools/)
  // jo tumhare backend me add karna hoga — CallApiService jaisa hi
  // baseUrl aur auth-token pattern use karta hai.
  // ============================================================
  String _collectBoardTextContent() {
    final buffer = StringBuffer();
    for (final page in _pages) {
      for (final t in page.texts) {
        if (t.text.trim().isNotEmpty) buffer.writeln(t.text.trim());
      }
      for (final n in page.stickyNotes) {
        if (n.text.trim().isNotEmpty) buffer.writeln(n.text.trim());
      }
    }
    return buffer.toString().trim();
  }

  void _openAiToolsSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF1E1E2C),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => SafeArea(
        child: Wrap(
          children: [
            ListTile(
              leading: const Icon(Icons.notes, color: Colors.tealAccent),
              title: const Text('Generate Summary Notes', style: TextStyle(color: Colors.white)),
              subtitle: const Text('Board pe likhe hue se short summary banao', style: TextStyle(color: Colors.white54)),
              onTap: () {
                Navigator.pop(context);
                _runAiGeneration('summary');
              },
            ),
            ListTile(
              leading: const Icon(Icons.quiz_outlined, color: Colors.tealAccent),
              title: const Text('Generate Quiz', style: TextStyle(color: Colors.white)),
              subtitle: const Text('Board pe likhe hue se practice quiz banao', style: TextStyle(color: Colors.white54)),
              onTap: () {
                Navigator.pop(context);
                _runAiGeneration('quiz');
              },
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _runAiGeneration(String mode) async {
    final content = _collectBoardTextContent();
    if (content.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Pehle board pe kuch likho ya sticky note lagao, tabhi AI usse banayega.')),
      );
      return;
    }

    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (_) => const AlertDialog(
        backgroundColor: Color(0xFF1E1E2C),
        content: Row(
          children: [
            CircularProgressIndicator(color: Colors.tealAccent),
            SizedBox(width: 20),
            Expanded(child: Text('Generating…', style: TextStyle(color: Colors.white))),
          ],
        ),
      ),
    );

    try {
      final result = await AiStudyService.generate(mode: mode, content: content);
      if (!mounted) return;
      Navigator.pop(context); // close loading dialog
      _showAiResultSheet(mode, result);
    } catch (e) {
      if (!mounted) return;
      Navigator.pop(context); // close loading dialog
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('AI se generate nahi ho paya: $e')),
      );
    }
  }

  void _showAiResultSheet(String mode, Map<String, dynamic> result) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF1E1E2C),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (sheetContext) {
        return DraggableScrollableSheet(
          initialChildSize: 0.6,
          minChildSize: 0.3,
          maxChildSize: 0.9,
          expand: false,
          builder: (_, scrollController) {
            if (mode == 'summary') {
              final summaryText = (result['summary'] ?? '').toString();
              return Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('AI Summary Notes', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                    const SizedBox(height: 12),
                    Expanded(
                      child: SingleChildScrollView(
                        controller: scrollController,
                        child: Text(summaryText, style: const TextStyle(color: Colors.white70, height: 1.4)),
                      ),
                    ),
                    const SizedBox(height: 12),
                    Row(
                      children: [
                        Expanded(
                          child: OutlinedButton.icon(
                            icon: const Icon(Icons.note_add, color: Colors.amber),
                            label: const Text('Save as Sticky Note', style: TextStyle(color: Colors.amber)),
                            onPressed: () {
                              Navigator.pop(sheetContext);
                              _saveTextAsStickyNote(summaryText);
                            },
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: ElevatedButton.icon(
                            icon: const Icon(Icons.refresh),
                            label: const Text('Regenerate'),
                            onPressed: () {
                              Navigator.pop(sheetContext);
                              _runAiGeneration('summary');
                            },
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              );
            }

            // Quiz mode
            final questions = (result['questions'] as List?) ?? [];
            return Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('AI Quiz', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
                  const SizedBox(height: 12),
                  Expanded(
                    child: ListView.builder(
                      controller: scrollController,
                      itemCount: questions.length,
                      itemBuilder: (_, i) {
                        final q = (questions[i] as Map).cast<String, dynamic>();
                        return _QuizQuestionCard(question: q, index: i);
                      },
                    ),
                  ),
                  const SizedBox(height: 12),
                  ElevatedButton.icon(
                    icon: const Icon(Icons.refresh),
                    label: const Text('Regenerate Quiz'),
                    onPressed: () {
                      Navigator.pop(sheetContext);
                      _runAiGeneration('quiz');
                    },
                  ),
                ],
              ),
            );
          },
        );
      },
    );
  }

  void _saveTextAsStickyNote(String text) {
    if (text.trim().isEmpty) return;
    final note = StickyNoteModel(
      id: const Uuid().v4(),
      userId: widget.currentUserId,
      text: text.trim(),
      position: const Offset(100, 100),
      color: Colors.tealAccent.shade100,
    );
    setState(() {
      _page.stickyNotes.add(note);
      _recordMyAction(_BoardActionType.stickyNote, refId: note.id);
      _isDirty = true;
    });
    _sendRoomEvent('add_sticky_note', {'note': note.toJson(), 'pageId': _page.id});
  }

  // ============================================================
  // TOOLBAR
  // ============================================================
  static const List<Color> _colorPalette = [
    Colors.black,
    Colors.white,
    Colors.redAccent,
    Colors.orange,
    Colors.amber,
    Colors.green,
    Colors.blueAccent,
    Colors.purpleAccent,
    Colors.pinkAccent,
    Colors.brown,
  ];

  static const List<double> _strokeSizes = [2.0, 4.0, 6.0, 10.0, 16.0];

  Widget _buildToolPalette() {
    // 🔥 FIX — pehle margin fixed 16px tha, isliye jab phone ka system
    // nav bar (back/home/recents) dikhta tha to wo is toolbar ke upar
    // aa jaata tha. Ab device ke bottom safe-area inset ko bhi margin
    // me jod dete hain taaki toolbar hamesha nav bar se upar, clear
    // space me rahe. Additionally, ab is margin me call control bar ka
    // height + gap bhi shaamil hai, taaki dono floating bars stack ho
    // jayein (ek doosre ke upar) aur overlap na karein.
    final bottomSafeInset = MediaQuery.of(context).padding.bottom;
    return Align(
      alignment: Alignment.bottomCenter,
      child: Container(
        margin: EdgeInsets.only(bottom: _toolPaletteBottomMargin(bottomSafeInset)),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: const Color(0xFF0D0D15).withOpacity(0.92),
          borderRadius: BorderRadius.circular(30),
        ),
        child: SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(
            children: [
              // 🔧 MOVED — Pan/Zoom (Hand) toggle pehle AppBar me tha; ye
              // ek canvas navigation tool hai (call control nahi), isliye
              // ab yahan baaki drawing tools ke saath hai — same jagah jahan
              // se log naturally is tarah ke tools dhoondte hain.
              IconButton(
                icon: Icon(
                  _panZoomMode ? Icons.pan_tool : Icons.pan_tool_outlined,
                  color: _panZoomMode ? Colors.blueAccent : Colors.white54,
                ),
                onPressed: () => setState(() => _panZoomMode = !_panZoomMode),
                tooltip: _panZoomMode ? 'Disable Pan/Zoom (Hand)' : 'Enable Pan/Zoom (Hand)',
              ),
              const SizedBox(width: 4),
              _toolIcon(Icons.edit, ToolType.marker),
              _toolIcon(Icons.brush, ToolType.paint),
              _toolIcon(Icons.highlight, ToolType.highlighter),
              _toolIcon(Icons.cleaning_services, ToolType.eraser),
              _shapeMenuButton(),
              _toolIcon(Icons.text_fields, ToolType.text),
              const SizedBox(width: 6),
              GestureDetector(
                onTap: _openColorAndSizePicker,
                child: Container(
                  margin: const EdgeInsets.symmetric(horizontal: 4),
                  width: 26,
                  height: 26,
                  decoration: BoxDecoration(
                    color: _selectedColor,
                    shape: BoxShape.circle,
                    border: Border.all(color: Colors.white54, width: 1.5),
                  ),
                ),
              ),
              const SizedBox(width: 6),
              IconButton(
                icon: const Icon(Icons.undo, color: Colors.white),
                onPressed: _undoLastAction,
                tooltip: 'Undo Last Action',
              ),
              IconButton(
                icon: const Icon(Icons.delete_forever, color: Colors.redAccent),
                onPressed: _clearCompleteBoard,
                tooltip: 'Clear This Page',
              ),
              IconButton(
                icon: const Icon(Icons.text_snippet_outlined, color: Colors.orangeAccent),
                onPressed: _clearBoardKeepText,
                tooltip: 'Clear All (Keep Text)',
              ),
              IconButton(
                icon: const Icon(Icons.note_add, color: Colors.amber),
                onPressed: _addStickyNote,
                tooltip: 'Add Sticky Note',
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _toolIcon(IconData icon, ToolType type) {
    final active = _activeTool == type;
    return IconButton(
      icon: Icon(icon, color: active ? Colors.blueAccent : Colors.white54),
      onPressed: () => setState(() => _activeTool = type),
    );
  }

  Widget _shapeMenuButton() {
    final isActive = isShapeTool(_activeTool);
    return PopupMenuButton<ToolType>(
      icon: Icon(Icons.category_outlined, color: isActive ? Colors.blueAccent : Colors.white54),
      tooltip: 'Shapes',
      onSelected: (tool) => setState(() => _activeTool = tool),
      itemBuilder: (_) => const [
        PopupMenuItem(
          value: ToolType.rectangle,
          child: Row(children: [Icon(Icons.crop_square), SizedBox(width: 8), Text('Rectangle')]),
        ),
        PopupMenuItem(
          value: ToolType.circle,
          child: Row(children: [Icon(Icons.circle_outlined), SizedBox(width: 8), Text('Circle')]),
        ),
        PopupMenuItem(
          value: ToolType.line,
          child: Row(children: [Icon(Icons.horizontal_rule), SizedBox(width: 8), Text('Line')]),
        ),
        PopupMenuItem(
          value: ToolType.arrowLine,
          child: Row(children: [Icon(Icons.arrow_forward), SizedBox(width: 8), Text('Arrow')]),
        ),
      ],
    );
  }

  void _openColorAndSizePicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF1E1E2C),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (sheetContext) {
        return StatefulBuilder(
          builder: (sheetContext, sheetSetState) {
            return Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Color', style: TextStyle(color: Colors.white70, fontSize: 13, fontWeight: FontWeight.bold)),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 12,
                    runSpacing: 12,
                    children: _colorPalette.map((color) {
                      final isSelected = _selectedColor.value == color.value;
                      return GestureDetector(
                        onTap: () {
                          setState(() => _selectedColor = color);
                          sheetSetState(() {});
                        },
                        child: Container(
                          width: 34,
                          height: 34,
                          decoration: BoxDecoration(
                            color: color,
                            shape: BoxShape.circle,
                            border: Border.all(
                              color: isSelected ? Colors.blueAccent : Colors.white24,
                              width: isSelected ? 3 : 1,
                            ),
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                  const SizedBox(height: 20),
                  const Text('Stroke Size', style: TextStyle(color: Colors.white70, fontSize: 13, fontWeight: FontWeight.bold)),
                  const SizedBox(height: 10),
                  Row(
                    children: _strokeSizes.map((size) {
                      final isSelected = _strokeWidth == size;
                      return Expanded(
                        child: GestureDetector(
                          onTap: () {
                            setState(() => _strokeWidth = size);
                            sheetSetState(() {});
                          },
                          child: Container(
                            margin: const EdgeInsets.symmetric(horizontal: 4),
                            padding: const EdgeInsets.symmetric(vertical: 10),
                            decoration: BoxDecoration(
                              color: isSelected ? Colors.blueAccent.withOpacity(0.25) : Colors.white10,
                              borderRadius: BorderRadius.circular(12),
                              border: Border.all(color: isSelected ? Colors.blueAccent : Colors.transparent),
                            ),
                            child: Center(
                              child: Container(
                                width: size.clamp(2.0, 22.0),
                                height: size.clamp(2.0, 22.0),
                                decoration: BoxDecoration(color: _selectedColor, shape: BoxShape.circle),
                              ),
                            ),
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                  const SizedBox(height: 8),
                  Slider(
                    value: _strokeWidth.clamp(1.0, 20.0),
                    min: 1.0,
                    max: 20.0,
                    activeColor: Colors.blueAccent,
                    onChanged: (v) {
                      setState(() => _strokeWidth = v);
                      sheetSetState(() {});
                    },
                  ),
                ],
              ),
            );
          },
        );
      },
    );
  }

  // ============================================================
  // STICKER PICKER — chota grid, tap karte hi sticker bhej deta hai
  // (sheet khud band nahi hoti, taaki user chahe to jaldi-jaldi kai
  // reactions bhej sake; wapas jaane ke liye khud swipe-down/back karo).
  // ============================================================
  void _openStickerPicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF1E1E2C),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (sheetContext) {
        return Padding(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Send a Sticker', style: TextStyle(color: Colors.white70, fontSize: 13, fontWeight: FontWeight.bold)),
              const SizedBox(height: 14),
              Wrap(
                spacing: 14,
                runSpacing: 14,
                children: _stickerPack.map((emoji) {
                  return GestureDetector(
                    onTap: () => _sendSticker(emoji),
                    child: Container(
                      width: 52,
                      height: 52,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        color: Colors.white10,
                        borderRadius: BorderRadius.circular(14),
                      ),
                      child: Text(emoji, style: const TextStyle(fontSize: 26)),
                    ),
                  );
                }).toList(),
              ),
            ],
          ),
        );
      },
    );
  }

  // ============================================================
  // STICKER OVERLAY — active stickers ko screen ke bottom-half me
  // random horizontal spots par IgnorePointer ke saath render karta
  // hai (taaki whiteboard/drawing touches ke beech me na aayein).
  // ============================================================
  Widget _buildStickerOverlay() {
    return Positioned.fill(
      child: IgnorePointer(
        child: LayoutBuilder(
          builder: (context, constraints) {
            return Stack(
              children: _activeStickers.map((sticker) {
                final usableWidth = math.max(0.0, constraints.maxWidth - 88);
                final left = 24 + sticker.dx * usableWidth;
                return Positioned(
                  key: ValueKey(sticker.id),
                  left: left,
                  bottom: 90,
                  child: _StickerBubble(sticker: sticker),
                );
              }).toList(),
            );
          },
        ),
      ),
    );
  }
}

// ============================================================
// RULED LINE STYLE — orientation (horizontal jaisa notebook / vertical
// jaisa graph-paper columns / grid dono) + spacing (lines choti-badi).
// Socket ke through JSON me sync hota hai taaki sab participants ko
// same style dikhe.
// ============================================================
enum _RuledLineOrientation { horizontal, vertical, grid }

class _RuledLineStyle {
  static const double smallSpacing = 24;
  static const double mediumSpacing = 40;
  static const double largeSpacing = 64;

  final double spacing;
  final _RuledLineOrientation orientation;

  const _RuledLineStyle({this.spacing = mediumSpacing, this.orientation = _RuledLineOrientation.horizontal});

  Map<String, dynamic> toJson() => {
        'spacing': spacing,
        'orientation': orientation.name,
      };

  factory _RuledLineStyle.fromJson(Map<String, dynamic> json) {
    final orientationStr = json['orientation']?.toString();
    final orientation = _RuledLineOrientation.values.firstWhere(
      (o) => o.name == orientationStr,
      orElse: () => _RuledLineOrientation.horizontal,
    );
    final spacing = (json['spacing'] is num) ? (json['spacing'] as num).toDouble() : mediumSpacing;
    return _RuledLineStyle(spacing: spacing, orientation: orientation);
  }
}

// ============================================================
// RULED PAPER PAINTER — notebook/copy jaisi ruled lines. Ab spacing
// (chhoti/badi) aur orientation (horizontal/vertical/grid) style se
// aate hain — pehle fixed 40px horizontal-only tha. Left margin ki red
// line sirf tab dikhti hai jab horizontal lines maujood hon (notebook
// jaisa look), pure-vertical graph-paper style me nahi dikhti. Sirf
// khaali whiteboard page ke background pe draw hota hai (annotations/
// strokes upar alag layer me render hote hain, isse koi interference
// nahi).
// ============================================================
class _RuledPaperPainter extends CustomPainter {
  final _RuledLineStyle style;
  const _RuledPaperPainter(this.style);

  static const double _marginLeft = 56;

  @override
  void paint(Canvas canvas, Size size) {
    final linePaint = Paint()
      ..color = Colors.blue.withOpacity(0.25)
      ..strokeWidth = 1;

    final drawHorizontal = style.orientation == _RuledLineOrientation.horizontal || style.orientation == _RuledLineOrientation.grid;
    final drawVertical = style.orientation == _RuledLineOrientation.vertical || style.orientation == _RuledLineOrientation.grid;

    if (drawHorizontal) {
      for (double y = style.spacing; y < size.height; y += style.spacing) {
        canvas.drawLine(Offset(0, y), Offset(size.width, y), linePaint);
      }
    }
    if (drawVertical) {
      for (double x = style.spacing; x < size.width; x += style.spacing) {
        canvas.drawLine(Offset(x, 0), Offset(x, size.height), linePaint);
      }
    }

    // notebook jaisa red margin — sirf horizontal-wale styles (horizontal
    // ya grid) me maayne rakhta hai.
    if (drawHorizontal) {
      final marginPaint = Paint()
        ..color = Colors.redAccent.withOpacity(0.4)
        ..strokeWidth = 1.5;
      canvas.drawLine(Offset(_marginLeft, 0), Offset(_marginLeft, size.height), marginPaint);
    }
  }

  @override
  bool shouldRepaint(covariant _RuledPaperPainter oldDelegate) =>
      oldDelegate.style.spacing != style.spacing || oldDelegate.style.orientation != style.orientation;
}

// ============================================================
// STICKER BUBBLE — ek single reaction ka pop-up + float-up + fade
// animation. 3 second ke total lifecycle ke saath sync rehta hai
// (parent hi 3s baad ise list se hata deta hai — ye widget khud sirf
// visual animation dikhata hai).
// ============================================================
class _StickerBubble extends StatelessWidget {
  final _StickerEvent sticker;

  const _StickerBubble({required this.sticker});

  @override
  Widget build(BuildContext context) {
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0.0, end: 1.0),
      duration: const Duration(milliseconds: 3000),
      curve: Curves.easeOut,
      builder: (context, t, child) {
        // Pehle 15% me pop-in (scale 0 -> 1.15 -> 1), aakhri 30% me fade-out.
        final scale = t < 0.15 ? Curves.elasticOut.transform(t / 0.15) : 1.0;
        final opacity = t > 0.7 ? (1 - (t - 0.7) / 0.3).clamp(0.0, 1.0) : 1.0;
        final floatUp = t * 70; // upar float hote jaata hai
        return Opacity(
          opacity: opacity,
          child: Transform.translate(
            offset: Offset(0, -floatUp),
            child: Transform.scale(scale: scale, child: child),
          ),
        );
      },
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // 🔥 Naya arrangement: sender chip ab UPAR (avatar-initial ke saath),
          // emoji NEECHE ek soft glow-disc ke andar — pehle ye ulta tha
          // (emoji upar, plain name-pill neeche). Feels zyada "live reaction
          // card" jaisa, kam "floating label" jaisa.
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
            margin: const EdgeInsets.only(bottom: 6),
            decoration: BoxDecoration(
              color: Colors.black.withOpacity(0.55),
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: Colors.white.withOpacity(0.1)),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                CircleAvatar(
                  radius: 7,
                  backgroundColor: Colors.white24,
                  child: Text(
                    sticker.senderName.isNotEmpty ? sticker.senderName[0].toUpperCase() : '?',
                    style: const TextStyle(color: Colors.white, fontSize: 8, fontWeight: FontWeight.w700),
                  ),
                ),
                const SizedBox(width: 5),
                Text(
                  sticker.senderName,
                  style: const TextStyle(color: Colors.white, fontSize: 10.5, fontWeight: FontWeight.w600),
                ),
              ],
            ),
          ),
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: Colors.white.withOpacity(0.06),
              boxShadow: [
                BoxShadow(color: Colors.black.withOpacity(0.25), blurRadius: 18, spreadRadius: 2),
              ],
            ),
            child: Text(sticker.emoji, style: const TextStyle(fontSize: 40)),
          ),
        ],
      ),
    );
  }
}

// ============================================================
// JOIN TOAST — ek chhota, professional floating pill jo top se slide
// + fade-in hoke ~2.5s dikhta hai (naya participant ka naam ke saath),
// phir khud slide + fade-out hoke Overlay se hat jaata hai. Screen
// reader / layout ko touch nahi karta (Overlay ka standalone entry
// hai), isliye kisi bhi existing bar ke saath overlap/collision nahi
// hoti — bas thodi der ke liye sabse upar float karta hai.
// ============================================================
class _JoinToast extends StatefulWidget {
  final String name;
  final VoidCallback onDone;

  const _JoinToast({required this.name, required this.onDone});

  @override
  State<_JoinToast> createState() => _JoinToastState();
}

class _JoinToastState extends State<_JoinToast> with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late final Animation<double> _fade;
  late final Animation<Offset> _slide;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 380));
    _fade = CurvedAnimation(parent: _controller, curve: Curves.easeOut);
    // 🔥 Naya layout: ab top-center pill ki jagah top-right se slide-in
    // hota hai (Slack/Zoom notification-card jaisa) — center screen
    // clutter nahi hota aur video tiles ke saath overlap kam hota hai.
    _slide = Tween<Offset>(begin: const Offset(0.35, 0), end: Offset.zero)
        .animate(CurvedAnimation(parent: _controller, curve: Curves.easeOutCubic));
    _controller.forward();

    // ~2.5s visible, phir reverse (fade + slide-out) karke khatam.
    Future.delayed(const Duration(milliseconds: 2500), () async {
      if (!mounted) return;
      await _controller.reverse();
      widget.onDone();
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Positioned(
      top: MediaQuery.of(context).padding.top + 16,
      right: 16,
      child: IgnorePointer(
        child: FadeTransition(
          opacity: _fade,
          child: SlideTransition(
            position: _slide,
            child: Material(
              color: Colors.transparent,
              child: Container(
                constraints: const BoxConstraints(maxWidth: 260),
                decoration: BoxDecoration(
                  color: const Color(0xFF12121C).withOpacity(0.96),
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(color: Colors.white.withOpacity(0.08)),
                  boxShadow: const [BoxShadow(color: Colors.black54, blurRadius: 16, offset: Offset(0, 6))],
                ),
                child: IntrinsicHeight(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      // Left accent bar — ek nazar me "someone joined" signal.
                      Container(
                        width: 4,
                        decoration: const BoxDecoration(
                          gradient: LinearGradient(
                            begin: Alignment.topCenter,
                            end: Alignment.bottomCenter,
                            colors: [Color(0xFF34D399), Color(0xFF10B981)],
                          ),
                          borderRadius: BorderRadius.horizontal(left: Radius.circular(14)),
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Stack(
                              clipBehavior: Clip.none,
                              children: [
                                CircleAvatar(
                                  radius: 15,
                                  backgroundColor: Colors.white12,
                                  child: Text(
                                    widget.name.isNotEmpty ? widget.name[0].toUpperCase() : '?',
                                    style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 13),
                                  ),
                                ),
                                Positioned(
                                  right: -1,
                                  bottom: -1,
                                  child: Container(
                                    width: 10,
                                    height: 10,
                                    decoration: BoxDecoration(
                                      color: const Color(0xFF34D399),
                                      shape: BoxShape.circle,
                                      border: Border.all(color: const Color(0xFF12121C), width: 2),
                                    ),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(width: 10),
                            Flexible(
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    widget.name,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 13),
                                  ),
                                  const Text(
                                    'joined the study room',
                                    style: TextStyle(color: Colors.white54, fontSize: 11),
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// ============================================================
// QUIZ QUESTION CARD — ek question dikhata hai, tap karne pe answer
// reveal ho jaata hai (MCQ options ho to un-highlighted rehte hain,
// answer text hamesha bata diya jaata hai reveal ke baad).
// ============================================================
class _QuizQuestionCard extends StatefulWidget {
  final Map<String, dynamic> question;
  final int index;

  const _QuizQuestionCard({required this.question, required this.index});

  @override
  State<_QuizQuestionCard> createState() => _QuizQuestionCardState();
}

class _QuizQuestionCardState extends State<_QuizQuestionCard> {
  bool _revealed = false;

  static const List<String> _labels = ['A', 'B', 'C', 'D', 'E', 'F'];

  @override
  Widget build(BuildContext context) {
    final questionText = (widget.question['question'] ?? '').toString();
    final answerText = (widget.question['answer'] ?? '').toString();
    final options = (widget.question['options'] as List?)?.map((e) => e.toString()).toList() ?? [];

    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.045),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withOpacity(0.06)),
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header row — gradient index badge + question text, ab plain
          // "Q1." prefix ki jagah proper card-title jaisa dikhta hai.
          Padding(
            padding: EdgeInsets.fromLTRB(14, 14, 14, options.isEmpty ? 4 : 10),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 26,
                  height: 26,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    gradient: const LinearGradient(colors: [Color(0xFF6366F1), Color(0xFF8B5CF6)]),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Text(
                    '${widget.index + 1}',
                    style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 13),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    questionText,
                    style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600, fontSize: 14, height: 1.35),
                  ),
                ),
              ],
            ),
          ),
          // Options ab bullet-list ki jagah A/B/C labelled wrap-chips me —
          // do-column jaisa flexible grid, chhoti screen pe khud wrap hota hai.
          if (options.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
              child: Wrap(
                spacing: 8,
                runSpacing: 8,
                children: List.generate(options.length, (i) {
                  final label = i < _labels.length ? _labels[i] : '${i + 1}';
                  return Container(
                    constraints: BoxConstraints(minWidth: (MediaQuery.of(context).size.width - 60) / 2),
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    decoration: BoxDecoration(
                      color: Colors.white.withOpacity(0.04),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: Colors.white.withOpacity(0.07)),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        CircleAvatar(
                          radius: 9,
                          backgroundColor: Colors.white10,
                          child: Text(label, style: const TextStyle(color: Colors.white70, fontSize: 10, fontWeight: FontWeight.w700)),
                        ),
                        const SizedBox(width: 8),
                        Flexible(
                          child: Text(options[i], style: const TextStyle(color: Colors.white70, fontSize: 12.5)),
                        ),
                      ],
                    ),
                  );
                }),
              ),
            ),
          // Reveal button -> animated highlighted answer panel (checkmark +
          // green tint) ki jagah pehle sirf plain text tha.
          AnimatedSize(
            duration: const Duration(milliseconds: 220),
            curve: Curves.easeOut,
            child: _revealed
                ? Container(
                    width: double.infinity,
                    margin: const EdgeInsets.fromLTRB(14, 0, 14, 14),
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                    decoration: BoxDecoration(
                      color: const Color(0xFF10B981).withOpacity(0.12),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: const Color(0xFF10B981).withOpacity(0.35)),
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Icon(Icons.check_circle_rounded, color: Color(0xFF34D399), size: 18),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            answerText,
                            style: const TextStyle(color: Color(0xFFD1FAE5), fontWeight: FontWeight.w600, fontSize: 13),
                          ),
                        ),
                      ],
                    ),
                  )
                : Padding(
                    padding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
                    child: InkWell(
                      borderRadius: BorderRadius.circular(10),
                      onTap: () => setState(() => _revealed = true),
                      child: Container(
                        width: double.infinity,
                        padding: const EdgeInsets.symmetric(vertical: 9),
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(color: Colors.tealAccent.withOpacity(0.4)),
                        ),
                        child: const Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.visibility_rounded, size: 15, color: Colors.tealAccent),
                            SizedBox(width: 6),
                            Text('Reveal Answer', style: TextStyle(color: Colors.tealAccent, fontWeight: FontWeight.w600, fontSize: 12.5)),
                          ],
                        ),
                      ),
                    ),
                  ),
          ),
        ],
      ),
    );
  }
}