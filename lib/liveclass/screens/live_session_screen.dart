// lib/liveclass/screens/live_session_screen.dart
//
// Screen 6 — Live Session Room (see LIVECLASS_SCREEN_ARCHITECTURE.md §6).
//
// Self-contained: pass just a [sessionId] and it does the whole
// `POST sessions/{id}/join/` dance itself — 200 -> render the room, 403 ->
// "pass required" state, 202 -> "waitlisted" state. If the caller already
// joined (e.g. Classroom Detail's "Enter Class" flow), pass [initialResult]
// to skip straight to the room, or [initialResult] with a null
// participantId (from `token()`, a reconnect) which just skips the "leave"
// participant-row cleanup on exit.
//
// LiveKit video/audio is wired in below: [livekitUrl]/[livekitToken] from
// [_joinResult] connect an `lk.Room` in [_connectLiveKit], local mic/cam
// toggles call the real LiveKit APIs, and camera thumbnails render via
// `lk.VideoTrackRenderer`. Chat, polls, participants, end/kick, leave were
// already wired to the real API per urls.py and are unchanged.
//
// ⚠ SETUP — same spirit as `message/services/call_kit_service.dart`'s
// version-pin notes:
//   1. pubspec.yaml:
//        dependencies:
//          livekit_client: ^2.3.5   # check the current 2.x when you add this
//          permission_handler: ^11.3.0  # pre-join green room device checks
//          wakelock_plus: ^1.5.2  # keep the screen on for the duration of the live class
//          connectivity_plus: ^7.3.1  # jump-start reconnect the instant the network actually comes back
//          battery_plus: ^7.1.1  # camera-off suggestion when the battery actually gets low
//          gal: ^2.3.2  # save the whiteboard to the photo gallery
//   2. Permissions (additive to whatever call_kit_service.dart already
//      needs for voice/video calling — likely already present if calling
//      works):
//        iOS Info.plist:   NSCameraUsageDescription, NSMicrophoneUsageDescription
//        Android manifest: CAMERA, RECORD_AUDIO, (BLUETOOTH_CONNECT on
//                           Android 12+ if you support BT headsets)
//        Android minSdkVersion 24+ (flutter_webrtc requirement).
//   3. `lk.VideoTrackRenderer` / track-publication field names below match
//      `livekit_client` ~2.3.x — if `flutter pub get` resolves a different
//      major version, check its CHANGELOG for renamed classes/getters
//      before trusting this file verbatim (same caution livekit_utils.py
//      gives for the Python server SDK).
//
// FEATURE: pre-join "green room". Previously this screen dropped the user
// straight into a live LiveKit room before they'd ever seen their own
// camera or confirmed mic/cam permissions — a permission prompt (or a
// denial) used to surface AFTER they were already "in class", which is
// both jarring and means a denied permission is discovered by the teacher
// and class before the student even knows something's wrong. The green
// room now runs first (skipped only when the caller already completed
// their own join — see [initialResult] doc above): it requests camera/mic
// permission up front, previews the real local camera feed, lets the user
// pre-set their mic/cam toggle state, and only calls the actual
// sessions/{id}/join/ + LiveKit connect once they tap "Join Class".
//
// FEATURE (advanced batch): live captions, in-app mini-view, and breakout
// rooms — see each block's own FEATURE comment below for detail. New
// pubspec.yaml dependency needed for captions:
//   speech_to_text: ^7.0.0   # on-device STT, no server round-trip
// Android: RECORD_AUDIO permission (already present for calling) plus, on
// Android 13+, the OS speech-recognition service must be present (it is on
// virtually all real devices; some bare AOSP emulators lack it — the
// _captionsUnavailable path below handles that gracefully instead of
// crashing). iOS: add NSSpeechRecognitionUsageDescription to Info.plist
// alongside the existing NSMicrophoneUsageDescription.
//
// Breakout rooms — backend done. `LiveClassApi.breakoutRooms` (in
// liveclass_api_service.dart) now hits real endpoints on
// ClassSessionViewSet (see urls.py/views.py):
//   POST sessions/{id}/breakout/          {"room_count": int} -> host creates rooms
//   POST sessions/{id}/breakout/assign/   {"participant_id": int, "room": int|null}
//   GET  sessions/{id}/breakout/          -> [{"room": int, "participant_ids": [...]}]
//   POST sessions/{id}/breakout/close/    -> host ends all rooms
// `BreakoutRoom` is a public model in liveclass_models.dart, shared between
// the API service and this screen. NOTE: a Django migration for the new
// `BreakoutRoom` model + `SessionParticipant.breakout_room` field still
// needs to be generated and applied (`manage.py makemigrations && migrate`)
// before this goes live against a real database.
//
// NOT included in this batch — virtual/blurred background. This screen
// already depends on `google_mlkit_selfie_segmentation` (see pubspec.yaml)
// but that package segments single still frames (an InputImage), while
// `livekit_client`'s LocalVideoTrack publishes camera frames straight from
// the platform's native WebRTC capturer with no per-frame Dart hook to
// intercept, blur, and re-inject before they're sent — doing this for real
// needs a custom VideoProcessor/native video source at the platform-channel
// level, not a change this single Dart file can make safely. Flagging this
// honestly rather than shipping a blur toggle that silently does nothing
// once actually in a call.

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data' show Uint8List;
import 'dart:ui' as ui;
// `hide MaterialType` — this screen's own `MaterialType` (course material
// kind: pdf/ppt/doc/image/video/link, from liveclass_models.dart) collided
// with Flutter's own `MaterialType` enum (used by the `Material` widget's
// `type:` param, which this file never sets explicitly).
import 'package:flutter/material.dart' hide MaterialType;
import 'package:flutter/rendering.dart' show RenderRepaintBoundary;
import 'package:flutter/services.dart' show Clipboard, ClipboardData, HapticFeedback;
import 'package:livekit_client/livekit_client.dart' as lk;
import 'package:permission_handler/permission_handler.dart' as ph;
import 'package:wakelock_plus/wakelock_plus.dart';
import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;
import 'package:printing/printing.dart';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:battery_plus/battery_plus.dart';
import 'package:gal/gal.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;
import 'package:speech_to_text/speech_recognition_error.dart' as stt;
import 'package:intl/intl.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

// FIX (design-system drift): aliased to the shared tokens instead of
// locally-duplicated literals — see the matching note in
// waitlist_screen.dart. Zero-risk: same values, single source of truth.
const _kNavy = LiveClassColors.navy;
const _kGradient = LiveClassColors.gradient;

// ===========================================================================
// SCREEN
// ===========================================================================
class LiveSessionScreen extends StatefulWidget {
  final int sessionId;
  /// Optional — for instant header info (classroom title, scheduled time).
  final ClassSession? session;
  /// Optional — pass this if the caller already called join()/token()
  /// (e.g. Classroom Detail's "Enter Class" button). If null, this screen
  /// performs the join itself on open.
  final SessionJoinResult? initialResult;

  const LiveSessionScreen({super.key, required this.sessionId, this.session, this.initialResult});

  @override
  State<LiveSessionScreen> createState() => _LiveSessionScreenState();
}

enum _RoomState { greenRoom, joining, inRoom, passRequired, waitlisted, error, ended }

/// Camera/mic permission outcome for the green room. [unknown] is the
/// initial "still checking" state so the UI can show a spinner instead of
/// flashing a denied message before the actual system check resolves.
enum _DevicePermState { unknown, granted, denied, permanentlyDenied }

class _LiveSessionScreenState extends State<LiveSessionScreen> with WidgetsBindingObserver {
  _RoomState _state = _RoomState.joining;
  String? _errorMessage;

  SessionJoinResult? _joinResult;
  ClassSession? _session;

  bool get _isHost => _joinResult?.role == 'host' || _joinResult?.livekitRole == 'host';

  // Button state for mic/cam/screen-share -- mirrored into the real
  // LiveKit publish calls in _toggleMic/_toggleCam/_toggleScreenShare below
  // (see file header: LiveKit is fully wired in this file).
  bool _micOn = true;
  bool _camOn = true;
  bool _screenSharing = false;

  // Tracks which physical camera is currently live for the front/back flip
  // buttons (_flipCamera/_flipGreenRoomCamera) — `switchCamera()` on newer
  // livekit_client needs an explicit device id/position rather than just
  // toggling, so this is the source of truth for "which one is next".
  bool _isFrontCameraInRoom = true;
  bool _isFrontCameraGreenRoom = true;

  // FEATURE: full participant grid, toggled from the header — separate from
  // the host-only "Participants" management panel (kick, counts) below.
  // Anyone (host or student) can flip to grid to actually SEE everyone at
  // once, not just the 5-tile thumbnail strip.
  bool _gridView = false;

  // FEATURE: app-lifecycle handling. Backgrounding a live class used to
  // leave the camera pointed at whatever's in front of the phone with
  // nobody able to see the screen — same behavior real call apps avoid.
  // Camera is turned off on background and restored to whatever the user
  // had it set to on return; mic is left alone so audio-only listening
  // keeps working in the background.
  bool _camOnBeforeBackground = true;

  // Side panel
  _PanelTab? _openPanel;

  // Chat
  final List<ChatMessage> _chatMessages = [];
  final TextEditingController _chatCtrl = TextEditingController();
  bool _chatLoading = false;
  bool _sendingChat = false;

  // Polls
  List<LivePoll> _polls = [];
  bool _pollsLoading = false;
  final Map<int, int> _myVotes = {}; // pollId -> chosen option index (local-only tracking)

  // Participants
  List<SessionParticipant> _participants = [];
  bool _participantsLoading = false;

  bool _actionBusy = false; // end / leave / kick in flight

  // FEATURE: hand-raise. `_handRaised` mirrors OUR OWN
  // SessionParticipant.hand_raised_at (see sessions/{id}/hand/ in
  // urls.py) — kept in local state so the control-bar button responds
  // instantly on tap, and re-synced from the server truth on every
  // participants refresh (see _syncOwnHandState, called from
  // _loadParticipants) so a stale tap from a flaky network never leaves
  // the button showing the wrong state for long.
  bool _handRaised = false;
  bool _handBusy = false; // guards the hand-raise tap while a request is in flight

  // FEATURE: recording. `_isRecording` mirrors ClassSession.is_recording
  // (true while `egress_id` is set — see models.py). Seeded from
  // [_session] on join and kept fresh by [_sessionPollTimer] below so
  // every participant (not just whoever tapped the button) sees the
  // "REC" indicator update, even if the host started it from a
  // different device.
  bool _isRecording = false;
  bool _recordingBusy = false; // guards start/stop recording while in flight

  // FEATURE: materials tab — this session's/classroom's shared notes,
  // PDFs, PPTs, videos, links (see ClassMaterial in models.py). Read-only
  // here: uploading stays on the Classroom Detail screen, this is just
  // "let me grab today's notes without leaving the live room".
  List<ClassMaterial> _materials = [];
  bool _materialsLoading = false;

  // FEATURE: doubts/queries tab — the async "ask a question, get it
  // answered when the teacher has a moment" channel (ClassQuery in
  // models.py), now reachable from inside the live room instead of only
  // from Classroom Detail. Anyone can ask; only the host can answer.
  List<ClassQuery> _queries = [];
  bool _queriesLoading = false;
  final TextEditingController _queryCtrl = TextEditingController();
  bool _askingQuery = false;

  // FEATURE: waitlist tab (host only) — sessions/{id} can fill up and
  // start silently waitlisting new joiners (see SessionWaitlist in
  // models.py); previously the host had no way to see that queue or
  // promote anyone from inside the live room itself.
  List<SessionWaitlistEntry> _waitlist = [];
  bool _waitlistLoading = false;

  // FEATURE: pinned notice banner — a teacher's pinned/urgent classroom
  // notice (Notice.is_pinned in models.py) previously only surfaced on
  // Classroom Detail, easy to miss once class has already started.
  // Shown as a dismissible strip above the video; dismissal is
  // local-only (per device, per open of this screen) — it doesn't
  // unpin/delete the notice for anyone else.
  List<Notice> _notices = [];
  final Set<int> _dismissedNoticeIds = {};

  // FEATURE: spotlight/pin — host can pin one participant's video large for
  // everyone (e.g. whoever is currently answering), instead of everyone
  // being stuck with their own focus tile or a plain gallery grid. This is
  // a pure LiveKit data-channel broadcast (see _sendSignal/_handleSignal
  // below) — there's no backend field for it, so it's session-local: a
  // fresh join always starts with nothing spotlighted, and it's invisible
  // to whoever wasn't in the room when it was set.
  String? _spotlightIdentity; // LiveKit identity (str(user_id)) of the pinned tile, or null = no spotlight

  // FEATURE: "ask to unmute" — previously a muted student had no way to ask
  // the host for the mic besides raising their hand and hoping the host
  // guesses why, or the host could only force-MUTE (never force-unmute,
  // which LiveKit deliberately doesn't allow one participant to do to
  // another's mic — audio consent has to come from the device owner).
  // Student side sends a request over the data channel; host sees a queue
  // (surfaced next to the mic icon in Participants) and can "approve",
  // which pings that one student back to flip their OWN mic on.
  final Set<String> _pendingUnmuteRequests = {}; // identities waiting on host approval (host-side)
  final Map<String, String> _pendingUnmuteNames = {}; // identity -> display name, for the host's queue
  DateTime? _lastUnmuteRequestSentAt; // student-side cooldown so a nervous tap-spam doesn't flood the host

  // FEATURE: low-bandwidth / audio-only mode. Local-only choice — turns off
  // the local camera and unsubscribes from every remote camera track so
  // this device stops encoding/decoding video altogether, keeping just
  // audio (+ data channel) flowing on a weak connection. Independent of the
  // green room's mic/cam picks; doesn't broadcast anything to the room —
  // every participant chooses this for their own device only.
  bool _audioOnlyMode = false;
  bool _camOnBeforeAudioOnly = true;

  // FEATURE: collaborative whiteboard — everyone's strokes synced live over
  // the LiveKit data channel (see _WhiteboardStroke near the bottom of this
  // file), no backend persistence. `Classroom.whiteboard_snapshot` (see
  // models.py) is never read or written here — a participant who opens the
  // board after drawing has already happened gets caught up via a
  // peer-to-peer "does anyone have the current board" request/response
  // (_wbRequestSync / 'wb_full_state' below) instead of a server round-trip,
  // so the board is empty again once every participant has left.
  bool _whiteboardOpen = false;
  final Map<String, _WhiteboardStroke> _wbStrokes = {}; // strokeId -> stroke
  final List<String> _wbStrokeOrder = []; // insertion order — paint order + undo (pop own last id)
  Color _wbColor = const Color(0xFFEE0979);
  double _wbWidth = 4.0;
  bool _wbErasing = false;
  bool _wbExporting = false; // guards _exportWhiteboardPdf against double-tap while a snapshot/PDF build is in flight
  String? _wbActiveStrokeId; // stroke currently being drawn by ME, if any
  Size _wbCanvasSize = Size.zero; // last-known canvas box size, for normalizing/denormalizing points
  final GlobalKey _wbRepaintKey = GlobalKey(); // wraps the whiteboard's CustomPaint -- see _exportWhiteboardPdf

  // LiveKit
  lk.Room? _lkRoom;
  lk.EventsListener<lk.RoomEvent>? _lkListener;
  bool _lkConnecting = false;
  bool _lkReconnecting = false;
  String? _lkError; // best-effort banner text; never blocks chat/polls/etc.

  // FEATURE: active speaker highlight — LiveKit's own activeSpeakers signal
  // wasn't used anywhere in this file before. Lets anyone glancing at grid
  // view see who's actually talking right now instead of guessing off a
  // static wall of tiles, same as any real video-call app.
  Set<String> _activeSpeakerIdentities = {};

  // FEATURE: connection quality — LiveKit's own per-participant quality
  // signal, surfaced for OUR OWN connection only (small icon in the header)
  // so a student on a bad connection understands why their video/audio
  // might be choppy, instead of it just degrading with no explanation —
  // ties into the same "weak connection" story as audio-only mode and
  // auto-reconnect above.
  lk.ConnectionQuality _myConnectionQuality = lk.ConnectionQuality.unknown;
  bool _suggestedAudioOnly = false; // only nudge once per session -- see the ParticipantConnectionQualityUpdatedEvent handler
  bool _suggestedCameraOffForBattery = false; // only nudge once per session -- see _checkBatteryForCameraSuggestion
  final Battery _battery = Battery();

  // FEATURE: emoji reactions — quick, low-friction feedback (👍❤️😂👏🎉🙌)
  // broadcast over the same data channel as spotlight/whiteboard/unmute
  // signaling (see _kSignalTopic below). Purely ephemeral and session-local,
  // same spirit as spotlight/whiteboard: no backend field for it, nothing
  // to persist — a fresh join simply doesn't see reactions sent before it
  // joined, and the floating emojis clear themselves a few seconds later.
  final List<_FloatingReaction> _activeReactions = [];
  int _reactionSeq = 0;
  int _reactionTotalCount = 0; // running total this session -- shown as a badge on the reaction button

  // FEATURE: session elapsed timer — "how long have I been in this call",
  // shown in the header (see _roomHeader / _ElapsedTimerText). Set once on
  // first successful connect (see _connectLiveKit); the ticking itself
  // lives in the small dedicated _ElapsedTimerText widget below so a
  // once-a-second update doesn't rebuild this whole screen (video tiles
  // included) every second.
  DateTime? _lkFirstConnectedAt;

  // FEATURE: auto-reconnect with backoff. A single dropped frame of network
  // used to dump the user straight into a dead-end error banner — now it
  // retries quietly a few times on its own before ever bothering the user
  // with a manual "Retry" button.
  static const int _maxAutoRetries = 3;
  int _lkAutoRetryCount = 0;
  // FEATURE (advanced): connectivity-aware reconnect -- see the listener
  // set up in initState / torn down in dispose. Skips the rest of
  // _scheduleAutoReconnect's exponential backoff wait the moment the OS
  // reports the network is actually back, instead of always waiting out
  // the full 2s/4s/8s schedule or for LiveKit's own (slower) detection.
  StreamSubscription<List<ConnectivityResult>>? _connectivitySub;
  Timer? _lkRetryTimer;

  // FEATURE: pre-join green room — local-only preview track + device
  // permission state, all separate from the real in-room `_lkRoom` above.
  // This never touches the actual session join or the real LiveKit room;
  // it's disposed the moment the user taps "Join Class" (or leaves the
  // screen from the green room without joining).
  lk.LocalVideoTrack? _previewTrack;
  bool _greenRoomMicOn = true;
  bool _greenRoomCamOn = true;
  _DevicePermState _camPermState = _DevicePermState.unknown;
  _DevicePermState _micPermState = _DevicePermState.unknown;
  bool _greenRoomBusy = false; // true while the initial permission/preview setup runs

  // FEATURE: live captions — on-device speech-to-text on OUR OWN mic audio
  // only (see file header for why: there's no easy tap into a remote
  // participant's decoded audio in Flutter/LiveKit, but every device can
  // run STT on its own microphone). Each device recognizes its own speaker
  // locally and broadcasts the finished line over the existing signal data
  // channel (same _kSignalTopic as reactions/spotlight/whiteboard) tagged
  // with the speaker's name, so everyone's client renders one shared
  // caption strip. Nothing is sent to any server and nothing persists —
  // captions vanish a few seconds after each line, same spirit as
  // reactions above.
  stt.SpeechToText? _speech;
  bool _captionsOn = false;
  bool _captionsUnavailable = false; // true once initialize() reports no STT engine on this device
  bool _captionsListening = false; // actually mid-recognition right now (STT auto-restarts in short bursts)
  String _myPartialCaption = ''; // our own in-progress (not yet finalized) line, shown locally only
  final List<_CaptionLine> _captionFeed = []; // finalized lines from everyone, newest last
  Timer? _captionLineExpiryTimer;

  // FEATURE: in-app "mini view" (Picture-in-Picture-style floating tile).
  // True OS-level PiP (visible after backgrounding the whole app) needs
  // native platform code this Dart file can't add on its own — see file
  // header. What IS fully doable here: a small draggable floating video
  // tile that stays on top while the user opens chat/whiteboard/materials
  // — panels that would otherwise fully cover the video — so they don't
  // lose sight of the class while reading/typing. Tapping it jumps back
  // to the full room view (closes whatever panel is open).
  bool _miniViewOn = false;
  Offset _miniViewOffset = const Offset(14, 90); // top-left corner, screen coords; dragged live in _MiniViewTile

  // FEATURE: breakout rooms (host-only creation/assignment; everyone else
  // just sees which room they've been put in). See file header for the
  // backend endpoints this assumes — genuinely session-scoped, so (like
  // waitlist/participants) it's REST + polling rather than the data
  // channel: a student who briefly loses connection and rejoins should
  // still see their assigned room from the server, not lose it because a
  // peer-to-peer signal was missed.
  List<BreakoutRoom> _breakoutRooms = []; // empty = no breakout in progress
  bool _breakoutLoading = false;
  bool _breakoutBusy = false; // guards create/assign/close while a request is in flight
  Timer? _breakoutPollTimer;
  int? get _myBreakoutRoom {
    final myIdentity = _localIdentity();
    if (myIdentity == null) return null;
    for (final room in _breakoutRooms) {
      if (room.participantIdentities.contains(myIdentity)) return room.roomNumber;
    }
    return null;
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _session = widget.session;
    // FEATURE (advanced): see _connectivitySub's own comment. Set up
    // unconditionally here (not gated on _state) since it only ever acts
    // while _state == inRoom anyway -- the handler checks that itself.
    _connectivitySub = Connectivity().onConnectivityChanged.listen((results) {
      if (!mounted || _state != _RoomState.inRoom) return;
      final hasNetwork = results.isNotEmpty && !results.contains(ConnectivityResult.none);
      if (!hasNetwork) return;
      // Network just came back while we were either sitting in the
      // persistent error banner or mid-way through a quiet backoff wait —
      // don't make the user sit through the rest of that delay.
      if (_lkError != null || _lkRetryTimer != null) {
        _lkRetryTimer?.cancel();
        _lkRetryTimer = null;
        if (!_lkReconnecting) _reconnectLiveKit(auto: true);
      }
    });
    if (widget.initialResult != null) {
      // Caller (e.g. Classroom Detail's "Enter Class") already did its own
      // join — no green room here, or we'd be asking the user to confirm
      // camera/mic a second time for a room they're already committed to.
      _joinResult = widget.initialResult;
      _state = _RoomState.inRoom;
      unawaited(_afterJoined());
    } else {
      _state = _RoomState.greenRoom;
      _initGreenRoom();
    }
  }

  // -------------------------------------------------------------------
  // Green room — permission check + local camera preview.
  // -------------------------------------------------------------------
  Future<void> _initGreenRoom() async {
    setState(() => _greenRoomBusy = true);

    final camStatus = await ph.Permission.camera.request();
    final micStatus = await ph.Permission.microphone.request();
    if (!mounted) return;

    _camPermState = _mapPermStatus(camStatus);
    _micPermState = _mapPermStatus(micStatus);
    _greenRoomCamOn = _camPermState == _DevicePermState.granted;
    _greenRoomMicOn = _micPermState == _DevicePermState.granted;

    if (_camPermState == _DevicePermState.granted) {
      try {
        _previewTrack = await lk.LocalVideoTrack.createCameraTrack();
      } catch (e) {
        // Permission said yes but the OS/hardware still refused (camera in
        // use elsewhere, simulator with no camera, etc.) — fall back to the
        // avatar placeholder rather than crashing the green room over it.
        debugPrint('Green room camera preview failed: $e');
        _previewTrack = null;
      }
    }

    if (!mounted) return;
    setState(() => _greenRoomBusy = false);
  }

  _DevicePermState _mapPermStatus(ph.PermissionStatus s) {
    if (s.isGranted || s.isLimited) return _DevicePermState.granted;
    if (s.isPermanentlyDenied) return _DevicePermState.permanentlyDenied;
    return _DevicePermState.denied;
  }

  Future<void> _toggleGreenRoomCam() async {
    if (_camPermState != _DevicePermState.granted) return;
    final next = !_greenRoomCamOn;
    if (next && _previewTrack == null) {
      try {
        _previewTrack = await lk.LocalVideoTrack.createCameraTrack();
      } catch (e) {
        debugPrint('Green room camera re-enable failed: $e');
        return;
      }
    }
    setState(() => _greenRoomCamOn = next);
  }

  void _toggleGreenRoomMic() {
    if (_micPermState != _DevicePermState.granted) return;
    setState(() => _greenRoomMicOn = !_greenRoomMicOn);
  }

  Future<void> _disposeGreenRoomPreview() async {
    try {
      await _previewTrack?.stop();
      await _previewTrack?.dispose();
    } catch (e) {
      debugPrint('Green room preview teardown error: $e');
    } finally {
      _previewTrack = null;
    }
  }

  /// Carries the green room's mic/cam choice into the real room, then runs
  /// the actual join. Camera permission being denied doesn't block joining
  /// — audio-only participation is still useful in a live class — but mic
  /// permission denial still lets them in muted; they can retry the OS
  /// permission from the control bar's mic button later same as before.
  Future<void> _joinFromGreenRoom() async {
    _micOn = _greenRoomMicOn;
    _camOn = _greenRoomCamOn;
    await _disposeGreenRoomPreview();
    if (!mounted) return;
    setState(() => _state = _RoomState.joining);
    await _join();
  }

  // -------------------------------------------------------------------
  // App lifecycle — camera off in background, restored on resume.
  // -------------------------------------------------------------------
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (_state != _RoomState.inRoom || _lkRoom == null) return;
    switch (state) {
      case AppLifecycleState.paused:
      case AppLifecycleState.inactive:
      case AppLifecycleState.hidden:
        if (_camOn) {
          _camOnBeforeBackground = true;
          unawaited(_lkRoom?.localParticipant?.setCameraEnabled(false));
          if (mounted) setState(() => _camOn = false);
        } else {
          _camOnBeforeBackground = false;
        }
        break;
      case AppLifecycleState.resumed:
        if (_camOnBeforeBackground) {
          unawaited(_lkRoom?.localParticipant?.setCameraEnabled(true).then((_) {
            if (mounted) setState(() => _camOn = true);
          }));
        }
        break;
      case AppLifecycleState.detached:
        break;
    }
  }

  // Lightweight polling — there's no websocket/pubsub wired up (see file
  // header), so this is how chat/polls/participants stay "live" without one.
  // Only the panel that's actually open gets polled, and only while the
  // widget is mounted and still in the room.
  Timer? _chatPollTimer;
  Timer? _pollPollTimer;
  Timer? _participantsPollTimer;
  Timer? _sessionPollTimer;
  Timer? _noticePollTimer;
  Timer? _batteryCheckTimer;

  void _startPolling() {
    _chatPollTimer?.cancel();
    _pollPollTimer?.cancel();
    _participantsPollTimer?.cancel();
    _sessionPollTimer?.cancel();
    _noticePollTimer?.cancel();
    _batteryCheckTimer?.cancel();
    _chatPollTimer = Timer.periodic(const Duration(seconds: 4), (_) {
      if (_state == _RoomState.inRoom && _openPanel == _PanelTab.chat) _loadChat(silent: true);
    });
    _pollPollTimer = Timer.periodic(const Duration(seconds: 6), (_) {
      if (_state == _RoomState.inRoom && _openPanel == _PanelTab.polls) _loadPolls(silent: true);
    });
    // FEATURE (grid view): participants now need to stay fresh for EVERY
    // role when the grid is open, not just for the host's management panel
    // — previously this whole timer only existed for _isHost. Same timer
    // now also refreshes the hand-raise queue (see _participantsPanel)
    // whenever that panel is open, since raised hands live on this same
    // participant list.
    _participantsPollTimer = Timer.periodic(const Duration(seconds: 8), (_) {
      if (_state == _RoomState.inRoom && (_openPanel == _PanelTab.participants || _gridView)) {
        _loadParticipants(silent: true);
      }
    });
    // FEATURE (recording): the REC indicator in the header (see
    // _roomHeader) has to be visible to EVERY participant, not just
    // whoever tapped start/stop — this is how a student's client learns
    // the host started/stopped recording from their own device.
    _sessionPollTimer = Timer.periodic(const Duration(seconds: 10), (_) {
      if (_state == _RoomState.inRoom) _refreshSessionRecordingState();
    });
    // FEATURE (notice banner): a newly-pinned/urgent notice should surface
    // without the student having to leave the room to notice it — but
    // notices don't change nearly as often as chat/polls, so a slower
    // 30s interval is plenty.
    _noticePollTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      if (_state == _RoomState.inRoom) _loadNotices();
    });
    // FEATURE (advanced/comfort): battery-aware camera-off suggestion.
    // Long live classes with the camera on are a real battery drain; a
    // 2-minute interval is plenty for something that only matters once the
    // level actually gets low, and matches this file's other slow-changing
    // background checks (notices above) rather than needing its own
    // battery-level stream.
    _batteryCheckTimer = Timer.periodic(const Duration(minutes: 2), (_) {
      if (_state == _RoomState.inRoom) _checkBatteryForCameraSuggestion();
    });
    // FEATURE (breakout rooms): a 6s poll while the panel is open (or, for
    // the host, always while breakout is active — so they can watch the
    // room list without keeping that panel open) mirrors the participants
    // poll's cadence above, since it's the same "who's where" kind of data.
    _breakoutPollTimer = Timer.periodic(const Duration(seconds: 6), (_) {
      if (_state != _RoomState.inRoom) return;
      if (_openPanel == _PanelTab.breakout || (_isHost && _breakoutRooms.isNotEmpty)) {
        _loadBreakoutRooms(silent: true);
      }
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _chatCtrl.dispose();
    _queryCtrl.dispose();
    _chatPollTimer?.cancel();
    _pollPollTimer?.cancel();
    _participantsPollTimer?.cancel();
    _sessionPollTimer?.cancel();
    _noticePollTimer?.cancel();
    _lkRetryTimer?.cancel();
    _batteryCheckTimer?.cancel();
    _breakoutPollTimer?.cancel();
    _captionLineExpiryTimer?.cancel();
    unawaited(_speech?.stop());
    unawaited(_connectivitySub?.cancel());
    // Best-effort, fire-and-forget — same pattern as _leave()'s participant
    // cleanup: never let a slow/failed teardown block screen disposal.
    unawaited(_disconnectLiveKit());
    unawaited(_disposeGreenRoomPreview());
    // FEATURE (comfort): release the wake lock this screen took out in
    // _connectLiveKit — otherwise the device would stay awake indefinitely
    // after leaving the class, well past this screen's own lifetime.
    unawaited(WakelockPlus.disable());
    super.dispose();
  }

  // -------------------------------------------------------------------
  // Join
  // -------------------------------------------------------------------
  Future<void> _join() async {
    setState(() {
      _state = _RoomState.joining;
      _errorMessage = null;
    });
    try {
      final result = await LiveClassApi.sessions.join(widget.sessionId);
      if (!mounted) return;
      if (result.waitlisted) {
        setState(() => _state = _RoomState.waitlisted);
        return;
      }
      setState(() {
        _joinResult = result;
        _state = _RoomState.inRoom;
      });
      unawaited(_afterJoined());
    } on LiveClassApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _state = e.statusCode == 403 ? _RoomState.passRequired : _RoomState.error;
        _errorMessage = e.message;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _state = _RoomState.error;
        _errorMessage = 'Could not join the session.';
      });
    }
  }

  Future<void> _afterJoined() async {
    _loadChat();
    _loadPolls();
    // FEATURE (grid view): loaded for everyone now, not just the host — the
    // grid needs names/roles for every tile regardless of who's watching.
    _loadParticipants();
    // FEATURE (recording): seed the REC indicator from whatever
    // [_session] we already had (may be stale/absent).
    _isRecording = _session?.isRecording ?? false;
    if (_session == null) {
      // Screen was opened with just a sessionId (no pre-fetched
      // ClassSession) — the materials/doubts/notices loaders just below
      // need classroom_id, which only comes from the session detail
      // fetch, so wait for that one call before firing them off.
      await _refreshSessionRecordingState();
    } else {
      unawaited(_refreshSessionRecordingState());
    }
    // FEATURE: materials/doubts/notices are all useful the moment someone
    // is in the room, not just when they open that panel — loaded eagerly
    // (like chat/polls above) so the "more" sheet's badges are accurate
    // from the first frame instead of showing 0 until tapped once.
    _loadMaterials();
    _loadQueries();
    _loadNotices();
    if (_isHost) _loadWaitlist();
    // FEATURE (breakout rooms): everyone checks once on join (not just the
    // host) so a student who joins mid-class while breakout is already
    // running lands straight in their assigned room's banner instead of
    // only finding out once they happen to open the panel.
    _loadBreakoutRooms(silent: true);
    _startPolling();
    unawaited(_connectLiveKit());
  }

  /// classroom_id for the classroom-scoped panels (materials/doubts/
  /// notices) below — from whichever ClassSession we have, pre-fetched
  /// or freshly loaded by [_refreshSessionRecordingState].
  int? get _classroomId => widget.session?.classroomId ?? _session?.classroomId;

  // -------------------------------------------------------------------
  // Recording — start/stop the LiveKit egress job and keep the REC
  // indicator in sync for everyone in the room.
  // -------------------------------------------------------------------
  /// Silent background refresh of [_isRecording] from the server (see
  /// sessions/{id}/ -> is_recording in serializers.py). Never surfaces an
  /// error to the user — this is just a polling heartbeat, same spirit as
  /// _loadChat/_loadPolls/_loadParticipants(silent: true).
  Future<void> _refreshSessionRecordingState() async {
    try {
      final fresh = await LiveClassApi.sessions.detail(widget.sessionId);
      if (!mounted) return;
      _session = fresh;
      if (fresh.isRecording != _isRecording) {
        setState(() => _isRecording = fresh.isRecording);
      }
    } catch (_) {
      // best-effort — keep showing whatever we last knew
    }
  }

  /// Host/co-teacher/moderator only: starts a LiveKit Egress recording of
  /// this session (see sessions/{id}/start-recording/ in urls.py). Whether
  /// recording is even allowed is decided by the classroom's teacher/
  /// organiser via Classroom.recording_enabled (a classroom setting, not
  /// something this screen controls) — the server enforces that and this
  /// just surfaces whatever it says.
  Future<void> _startRecording() async {
    if (_recordingBusy) return;
    setState(() => _recordingBusy = true);
    try {
      await LiveClassApi.sessions.startRecording(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _isRecording = true;
        _recordingBusy = false;
      });
      _snack('Recording started.');
    } catch (e) {
      if (!mounted) return;
      setState(() => _recordingBusy = false);
      _snack(e is LiveClassApiException ? e.message : 'Recording could not be started.');
    }
  }

  Future<void> _stopRecording() async {
    if (_recordingBusy) return;
    setState(() => _recordingBusy = true);
    try {
      await LiveClassApi.sessions.stopRecording(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _isRecording = false;
        _recordingBusy = false;
      });
      // The actual file (recording_url) still fills in asynchronously —
      // see LiveKitWebhookView in views.py — so don't promise it's ready.
      _snack('Recording stopped. The file will appear once it finishes processing.');
    } catch (e) {
      if (!mounted) return;
      setState(() => _recordingBusy = false);
      _snack(e is LiveClassApiException ? e.message : 'Error while stopping recording.');
    }
  }

  // -------------------------------------------------------------------
  // Hand raise — student's own "wait, let me ask" gesture, and the
  // host's queue of who's currently raised.
  // -------------------------------------------------------------------
  /// Toggles OUR OWN hand (see sessions/{id}/hand/ in urls.py). Local
  /// state flips immediately for a snappy button; [_loadParticipants]'s
  /// next refresh (via [_syncOwnHandState]) reconciles it with the server
  /// if the request ends up failing after the optimistic flip.
  Future<void> _toggleHandRaise() async {
    if (_handBusy) return;
    final next = !_handRaised;
    setState(() {
      _handRaised = next;
      _handBusy = true;
    });
    try {
      final serverState = await LiveClassApi.sessions.setHandRaised(widget.sessionId, raised: next);
      if (!mounted) return;
      setState(() {
        _handRaised = serverState;
        _handBusy = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _handRaised = !next; // revert the optimistic flip
        _handBusy = false;
      });
      _snack(e is LiveClassApiException ? e.message : 'Could not raise hand.');
    }
  }

  /// Host control: lower someone ELSE's raised hand after acknowledging
  /// them (see sessions/{id}/hand/{user_id}/lower/ in urls.py).
  Future<void> _lowerHand(SessionParticipant p) async {
    try {
      await LiveClassApi.sessions.lowerHand(widget.sessionId, p.user.id);
      _loadParticipants();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not lower hand.');
    }
  }

  /// Keeps [_handRaised] (our own control-bar button state) honest against
  /// the server truth on every participants refresh — matches ourselves by
  /// [SessionJoinResult.participantId], same identifier [_leave] already
  /// uses for "which row is me".
  void _syncOwnHandState(List<SessionParticipant> participants) {
    final myId = _joinResult?.participantId;
    if (myId == null || _handBusy) return; // don't fight an in-flight tap
    for (final p in participants) {
      if (p.id == myId) {
        if (p.handRaised != _handRaised) _handRaised = p.handRaised;
        break;
      }
    }
  }

  /// Our own place in the hand-raise queue (1-based), using the same
  /// handRaisedAt ordering as [_participantsPanel]'s host-facing list — so
  /// what a student sees on their own control button ("you're #2") always
  /// matches what the host sees at the top of their Participants panel.
  /// Null when our hand isn't actually raised or we can't identify
  /// ourselves in [_participants] yet.
  int? get _myHandQueuePosition {
    final myId = _joinResult?.participantId;
    if (myId == null || !_handRaised) return null;
    final raised = _participants.where((p) => p.handRaised).toList()
      ..sort((a, b) => (a.handRaisedAt ?? a.joinedAt).compareTo(b.handRaisedAt ?? b.joinedAt));
    final idx = raised.indexWhere((p) => p.id == myId);
    return idx == -1 ? null : idx + 1;
  }

  // -------------------------------------------------------------------
  // LiveKit — connect / events / teardown
  // -------------------------------------------------------------------
  /// Does the actual `lk.Room` construction + connect + local-track publish.
  /// Never touches `_lkError`/`_lkConnecting`/`_lkReconnecting` or schedules
  /// retries itself — the two callers below (first connect vs. reconnect)
  /// know their own context and own what the UI should show; this just
  /// reports success/failure.
  Future<bool> _doLiveKitConnect() async {
    final url = _joinResult?.livekitUrl;
    final token = _joinResult?.livekitToken;
    if (url == null || url.isEmpty || token == null || token.isEmpty) {
      // Shouldn't happen once past `joining` (join()/token() always
      // returns both when not waitlisted) — but never crash the room over
      // it; chat/polls/participants still work without video.
      return false;
    }

    final room = lk.Room(
      roomOptions: const lk.RoomOptions(
        adaptiveStream: true, // downscale subscribed tracks to renderer size
        dynacast: true, // simulcast layer switching server-side
      ),
    );
    _lkRoom = room;
    _lkListener = room.createListener();
    _wireLiveKitEvents();

    try {
      await room.connect(url, token);
      // Publish local tracks matching whatever the mic/cam toggle state
      // was before connect finished (user may have tapped mute while
      // still connecting).
      await room.localParticipant?.setMicrophoneEnabled(_micOn);
      await room.localParticipant?.setCameraEnabled(_camOn);
      return true;
    } catch (e) {
      debugPrint('LiveKit connect failed: $e');
      return false;
    }
  }

  Future<void> _connectLiveKit() async {
    if (_joinResult?.livekitUrl == null || _joinResult?.livekitUrl == '') return;
    setState(() {
      _lkConnecting = true;
      _lkError = null;
    });
    final ok = await _doLiveKitConnect();
    if (!mounted) return;
    setState(() => _lkConnecting = false);
    if (ok) {
      // Connected clean — auto-retry counter resets so the NEXT drop gets
      // its own full run of quiet retries instead of inheriting this one's.
      _lkAutoRetryCount = 0;
      // FEATURE: session elapsed timer (see _roomHeader) — starts counting
      // from this device's own first successful connect, not from
      // whatever _session.startedAt the server might report (that field
      // isn't threaded through this screen, and "how long have I been in
      // this call" is the more useful number for the person looking at
      // their own header anyway). Only set once — a later reconnect
      // shouldn't restart the clock.
      _lkFirstConnectedAt ??= DateTime.now();
      // FEATURE (comfort): keep the screen awake for the duration of the
      // live class — without this, the device's normal auto-lock timeout
      // (often as short as 30s-1min) dims and locks the screen mid-lecture,
      // which is a genuinely frustrating way to silently drop out of a
      // class you're still actively in. Enabled once per successful
      // connect; WakelockPlus.enable() is itself idempotent, so a
      // reconnect calling this again is harmless. Disabled in dispose().
      unawaited(WakelockPlus.enable());
    } else {
      _scheduleAutoReconnect();
    }
  }

  /// FIX: initial `_connectLiveKit()` failure and `RoomDisconnectedEvent`
  /// (token expiry, network blip, host restarting the room, etc.) both used
  /// to just leave `_lkError` sitting there forever with no way out short of
  /// backing all the way out of the screen and re-entering — the single
  /// biggest source of "joined but video/audio never came through"
  /// reports. `sessions/{id}/token/` exists exactly for this (fresh LiveKit
  /// credentials, no new participant row) but nothing ever called it.
  /// Re-fetches a token and reconnects without leaving the room or losing
  /// chat/polls/participants state.
  ///
  /// [auto] retries stay quiet (small "Reconnecting…" text, no alarming
  /// orange banner) since these are expected transient blips the user
  /// shouldn't have to think about. A manual tap (the Retry button, [auto]
  /// = false) always resets the auto-retry budget — the user asking for it
  /// again is a fresh attempt, not a continuation of the automatic ones.
  Future<void> _reconnectLiveKit({bool auto = false}) async {
    if (_lkReconnecting) return;
    if (!auto) _lkAutoRetryCount = 0;
    setState(() {
      _lkReconnecting = true;
      _lkError = null;
    });

    bool ok = false;
    String? failureMessage;
    try {
      final fresh = await LiveClassApi.sessions.freshToken(widget.sessionId);
      if (!mounted) return;
      // `token()` intentionally returns participantId=null (see file header
      // + SessionApi.freshToken doc) — keep the ORIGINAL participantId from
      // the real join() so `_leave()`'s participant-row cleanup still fires
      // for the right row.
      _joinResult = SessionJoinResult(
        roomId: fresh.roomId,
        participantId: _joinResult?.participantId,
        role: fresh.role,
        livekitRole: fresh.livekitRole,
        livekitUrl: fresh.livekitUrl,
        livekitToken: fresh.livekitToken,
      );
      await _disconnectLiveKit();
      if (!mounted) return;
      ok = await _doLiveKitConnect();
    } on LiveClassApiException catch (e) {
      failureMessage = e.message;
    } catch (_) {
      failureMessage = 'Could not reconnect — please try again.';
    }

    if (!mounted) return;
    // NOTE: deliberately not a try/finally — `_scheduleAutoReconnect()`
    // below may flip `_lkReconnecting` straight back to true for the next
    // quiet wait, and a blanket finally-reset used to stomp on that a
    // frame later, making the "Reconnecting…" text flicker off and on
    // between retries.
    setState(() => _lkReconnecting = false);
    if (ok) {
      _lkAutoRetryCount = 0;
    } else if (auto) {
      _scheduleAutoReconnect();
    } else {
      setState(() => _lkError = failureMessage ?? 'Video/audio could not connect — everything else is still running.');
    }
  }

  /// Backoff schedule: 2s, 4s, 8s. After [_maxAutoRetries] silent attempts,
  /// gives up and shows the persistent banner + manual Retry button.
  void _scheduleAutoReconnect() {
    if (!mounted || _state != _RoomState.inRoom) return;
    _lkRetryTimer?.cancel();
    if (_lkAutoRetryCount >= _maxAutoRetries) {
      setState(() {
        _lkReconnecting = false;
        _lkError = 'Video/audio could not connect — everything else is still running.';
      });
      return;
    }
    _lkAutoRetryCount++;
    final delay = Duration(seconds: 2 * (1 << (_lkAutoRetryCount - 1))); // 2s, 4s, 8s
    // Quiet wait — small "Reconnecting…" text via _lkReconnecting, no
    // alarming orange banner for what's meant to be an invisible retry.
    setState(() {
      _lkReconnecting = true;
      _lkError = null;
    });
    _lkRetryTimer = Timer(delay, () {
      if (!mounted || _state != _RoomState.inRoom) return;
      _reconnectLiveKit(auto: true);
    });
  }

  void _wireLiveKitEvents() {
    // Any of these firing just means "something about tracks/participants
    // changed" — re-render is enough, no per-event state to track since
    // `_localCameraVideoTrack()` / `_remoteCameraTrack()` read straight
    // off `_lkRoom` each build.
    _lkListener
      ?..on<lk.TrackSubscribedEvent>((event) {
        // FEATURE (audio-only mode): a track that gets subscribed AFTER we
        // already switched to audio-only (e.g. someone turns their camera
        // on mid-class, starts screen sharing, or a new participant joins)
        // would otherwise start decoding video again behind our back —
        // LiveKit auto-subscribes new publications by default. Immediately
        // drop it back down; covers screen-share too, not just camera,
        // matching _toggleAudioOnly's own unsubscribe loop below. Uses the
        // same `pub.source` check this screen already relies on elsewhere
        // (see _localCameraVideoTrack/_remoteCameraTrack/_isRemoteMicMuted)
        // rather than a track-kind API this file hasn't otherwise touched.
        // NOTE: `TrackSource.screenShareVideo` is the expected member name
        // on livekit_client ~2.3.x — recheck against the resolved
        // version's CHANGELOG if this doesn't compile (see file header's
        // version-pin caution).
        //
        // `RemoteTrackPublication.setSubscribed(bool)` was removed as of
        // livekit_client 2.0.0 — use the separate subscribe()/unsubscribe()
        // methods instead (see CHANGELOG's "Removal of previously
        // deprecated APIs" for 2.0.0).
        if (_audioOnlyMode &&
            (event.publication.source == lk.TrackSource.camera ||
                event.publication.source == lk.TrackSource.screenShareVideo)) {
          unawaited(event.publication.unsubscribe());
        }
        _lkRefresh();
      })
      ..on<lk.TrackUnsubscribedEvent>((_) => _lkRefresh())
      ..on<lk.LocalTrackPublishedEvent>((_) => _lkRefresh())
      ..on<lk.LocalTrackUnpublishedEvent>((_) => _lkRefresh())
      ..on<lk.ParticipantConnectedEvent>((_) => _lkRefresh())
      ..on<lk.ParticipantDisconnectedEvent>((event) {
        // FEATURE: spotlight/ask-to-unmute cleanup — a pinned tile or a
        // pending "please unmute" request for someone who just left would
        // otherwise linger: the spotlight would freeze on an empty tile,
        // and the host's queue would show a request nobody can act on.
        final identity = event.participant.identity;
        if (_spotlightIdentity == identity) {
          setState(() => _spotlightIdentity = null);
          if (_isHost) _sendSignal({'t': 'spotlight', 'id': null});
        }
        if (_pendingUnmuteRequests.remove(identity)) {
          _pendingUnmuteNames.remove(identity);
          if (mounted) setState(() {});
        }
        _lkRefresh();
      })
      // FEATURE: spotlight / ask-to-unmute / whiteboard sync — all three
      // ride the same LiveKit data channel rather than any new backend
      // endpoint. See _handleDataReceived for the payload dispatch.
      ..on<lk.DataReceivedEvent>(_handleDataReceived)
      // FEATURE (host mute control): needed so the participants panel's
      // mic icon and the grid view's muted badge update the instant the
      // server-side mute/unmute lands, instead of waiting for the next
      // 8-second participants poll tick.
      ..on<lk.TrackMutedEvent>((_) => _lkRefresh())
      ..on<lk.TrackUnmutedEvent>((_) => _lkRefresh())
      // FEATURE: active speaker highlight. NOTE: `ActiveSpeakersChangedEvent`
      // / its `.speakers` field are the livekit_client ~2.3.x names for this
      // — recheck against the resolved version's CHANGELOG if this doesn't
      // compile (same version-pin caution as this file's other LiveKit
      // calls, see file header).
      ..on<lk.ActiveSpeakersChangedEvent>((event) {
        if (!mounted) return;
        setState(() => _activeSpeakerIdentities = event.speakers.map((p) => p.identity).toSet());
      })
      // FEATURE: connection quality indicator — only OUR OWN quality drives
      // any UI here (see _myConnectionQuality's own comment above); other
      // participants' quality isn't surfaced anywhere in this screen.
      //
      // NOTE: the current livekit_client class for this is
      // `ParticipantConnectionQualityUpdatedEvent` (the `participant` and
      // `connectionQuality` fields are unchanged) — `ConnectionQualityChangedEvent`
      // was a livekit_client ~2.3.x name that no longer exists.
      ..on<lk.ParticipantConnectionQualityUpdatedEvent>((event) {
        if (!mounted || event.participant is! lk.LocalParticipant) return;
        setState(() => _myConnectionQuality = event.connectionQuality);
        // FEATURE (comfort): the header icon alone is passive — the
        // student still has to notice it and know what to do about it.
        // The first time our own connection actually drops to poor/lost,
        // proactively offer the one thing that reliably helps (audio-only
        // mode, see _toggleAudioOnly) instead of leaving them to figure
        // that out on their own. Only ever offered once per session so it
        // doesn't nag on every subsequent dip.
        if (!_audioOnlyMode &&
            !_suggestedAudioOnly &&
            (event.connectionQuality == lk.ConnectionQuality.poor || event.connectionQuality == lk.ConnectionQuality.lost)) {
          _suggestedAudioOnly = true;
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: const Text('Connection looks weak — try audio-only mode?'),
              action: SnackBarAction(label: 'Turn On', onPressed: _toggleAudioOnly),
              duration: const Duration(seconds: 8),
            ),
          );
        }
      })
      ..on<lk.RoomDisconnectedEvent>((event) {
        if (!mounted || _state != _RoomState.inRoom) return;
        // Room-service side already ended/removed us (host ended the
        // session, or we got kicked) — the sessions/{id}/end or kick
        // endpoint is the source of truth; this just reflects it in the
        // video area. FIX: this used to just sit there — now it feeds the
        // same quiet auto-retry-then-manual-button path as a failed
        // initial connect.
        _scheduleAutoReconnect();
      });
  }

  void _lkRefresh() {
    if (mounted) setState(() {});
  }

  // -------------------------------------------------------------------
  // Data-channel signaling — shared plumbing for spotlight, ask-to-unmute,
  // and whiteboard sync (see the FEATURE blocks in the state fields above).
  // A single JSON "topic" keeps this from colliding with any other data
  // messages a future feature might publish on this room.
  // -------------------------------------------------------------------
  static const String _kSignalTopic = 'liveclass_signal';

  /// [to] = specific LiveKit identities (e.g. approving one student's
  /// unmute request, or answering one late-joiner's whiteboard sync
  /// request); omit for a broadcast to everyone in the room.
  void _sendSignal(Map<String, dynamic> payload, {List<String>? to}) {
    final room = _lkRoom;
    // Read into a local so the null check below actually promotes it —
    // `room?.localParticipant` alone doesn't let Dart treat `room` (or a
    // getter read off it) as non-null afterwards.
    final localParticipant = room?.localParticipant;
    if (localParticipant == null) return; // not connected yet — signal is dropped, same as any other realtime event pre-connect
    try {
      final bytes = utf8.encode(jsonEncode(payload));
      // NOTE: `publishData` signature matches livekit_client ~2.3.x
      // (reliable + destinationIdentities + topic) — recheck against the
      // resolved version's CHANGELOG if this doesn't compile (see file
      // header's version-pin caution, same spirit as the LiveKit calls
      // elsewhere in this screen).
      localParticipant.publishData(
        bytes,
        reliable: true,
        destinationIdentities: to,
        topic: _kSignalTopic,
      );
    } catch (e) {
      debugPrint('Signal send failed ($payload): $e');
    }
  }

  void _handleDataReceived(lk.DataReceivedEvent event) {
    if (event.topic != null && event.topic != _kSignalTopic) return;
    Map<String, dynamic> payload;
    try {
      payload = jsonDecode(utf8.decode(event.data)) as Map<String, dynamic>;
    } catch (e) {
      debugPrint('Signal decode failed: $e');
      return;
    }
    final fromIdentity = event.participant?.identity;
    switch (payload['t']) {
      case 'spotlight':
        if (mounted) setState(() => _spotlightIdentity = payload['id'] as String?);
        break;
      case 'unmute_request':
        if (!_isHost || fromIdentity == null) return;
        setState(() {
          _pendingUnmuteRequests.add(fromIdentity);
          _pendingUnmuteNames[fromIdentity] = (payload['name'] as String?) ?? 'Student';
        });
        _snack('${_pendingUnmuteNames[fromIdentity]} has requested to unmute.');
        break;
      case 'unmute_approved':
        _showUnmuteApprovedPrompt();
        break;
      case 'reaction':
        {
          final emoji = payload['emoji'] as String?;
          if (emoji != null) _addFloatingReaction(emoji);
        }
        break;
      case 'caption':
        {
          final text = payload['text'] as String?;
          final name = (payload['name'] as String?) ?? 'Someone';
          if (text != null && text.trim().isNotEmpty) _addCaptionLine(name, text.trim());
        }
        break;
      case 'wb_point':
        _wbHandlePoint(payload);
        break;
      case 'wb_stroke_end':
        // No local state change needed — the stroke's points already
        // arrived one-by-one via 'wb_point'. Kept as its own message (vs.
        // inferring "done" from a gap in points) so a future feature like
        // per-stroke undo-by-remote-author has a clean end marker to key
        // off of.
        break;
      case 'wb_undo':
        {
          final sid = payload['sid'] as String?;
          if (sid == null) return;
          setState(() {
            _wbStrokes.remove(sid);
            _wbStrokeOrder.remove(sid);
          });
        }
        break;
      case 'wb_clear':
        setState(() {
          _wbStrokes.clear();
          _wbStrokeOrder.clear();
        });
        break;
      case 'wb_request_sync':
        if (fromIdentity == null || _wbStrokes.isEmpty) return;
        _sendSignal({
          't': 'wb_full_state',
          'strokes': _wbStrokeOrder.map((id) => _wbStrokes[id]!.toJson()).toList(),
        }, to: [fromIdentity]);
        break;
      case 'wb_full_state':
        {
          final incoming = (payload['strokes'] as List? ?? []);
          setState(() {
            for (final raw in incoming) {
              final stroke = _WhiteboardStroke.fromJson(raw as Map<String, dynamic>);
              if (!_wbStrokes.containsKey(stroke.id)) {
                _wbStrokes[stroke.id] = stroke;
                _wbStrokeOrder.add(stroke.id);
              }
            }
          });
        }
        break;
    }
  }

  // -------------------------------------------------------------------
  // Spotlight / pin — host-only control; broadcast + local mirror.
  // -------------------------------------------------------------------
  void _setSpotlight(String? identity) {
    if (!_isHost) return;
    final next = _spotlightIdentity == identity ? null : identity; // tapping the same tile again un-spotlights it
    setState(() => _spotlightIdentity = next);
    _sendSignal({'t': 'spotlight', 'id': next});
  }

  // -------------------------------------------------------------------
  // Ask to unmute
  // -------------------------------------------------------------------
  Future<void> _requestUnmute() async {
    final now = DateTime.now();
    if (_lastUnmuteRequestSentAt != null && now.difference(_lastUnmuteRequestSentAt!) < const Duration(seconds: 15)) {
      _snack('Request already sent — please wait a moment.');
      return;
    }
    final myIdentity = _localIdentity();
    if (myIdentity == null) return;
    _lastUnmuteRequestSentAt = now;
    final myName = _participants.firstWhere(
      (p) => p.user.id.toString() == myIdentity,
      orElse: () => SessionParticipant(id: 0, sessionId: widget.sessionId, user: UserMini(id: 0, username: '', fullName: 'Student'), role: ParticipantRole.student, joinedAt: DateTime.now()),
    ).user.fullName;
    _sendSignal({'t': 'unmute_request', 'name': myName});
    _snack('Unmute request sent to the host.');
  }

  /// Host approving one student's queued request (see Participants panel).
  /// Never flips the student's mic ourselves — LiveKit doesn't expose that,
  /// and it shouldn't: the student always makes the final call on their own
  /// mic. This just tells their device the host said yes.
  void _approveUnmute(String identity) {
    setState(() {
      _pendingUnmuteRequests.remove(identity);
      _pendingUnmuteNames.remove(identity);
    });
    _sendSignal({'t': 'unmute_approved'}, to: [identity]);
  }

  void _showUnmuteApprovedPrompt() {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: const Text('The host has given you permission to unmute.'),
        action: SnackBarAction(label: 'Unmute', onPressed: _toggleMic),
        duration: const Duration(seconds: 8),
      ),
    );
  }

  // -------------------------------------------------------------------
  // Emoji reactions — quick broadcast + local floating-emoji display.
  // See _activeReactions' own comment above for why this is data-channel
  // only, same as spotlight/whiteboard.
  // -------------------------------------------------------------------
  void _sendReaction(String emoji) {
    _addFloatingReaction(emoji);
    _sendSignal({'t': 'reaction', 'emoji': emoji});
  }

  void _addFloatingReaction(String emoji) {
    if (!mounted) return;
    final id = _reactionSeq++;
    // Spread horizontally a bit so a burst of the same emoji from several
    // people doesn't just stack into one unreadable column.
    final dx = (id % 4) / 4.0;
    setState(() {
      _activeReactions.add(_FloatingReaction(id: id, emoji: emoji, dx: dx));
      _reactionTotalCount++;
    });
    // Self-clears after the float-up animation finishes — nothing to undo
    // server-side, this was never persisted anywhere (see field comment).
    Timer(const Duration(milliseconds: 2600), () {
      if (!mounted) return;
      setState(() => _activeReactions.removeWhere((r) => r.id == id));
    });
  }

  Future<void> _showReactionPicker() async {
    const emojis = ['👍', '❤️', '😂', '👏', '🎉', '🙌'];
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: const Color(0xFF15171C),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 18, horizontal: 8),
          child: Wrap(
            alignment: WrapAlignment.center,
            spacing: 14,
            children: emojis
                .map((e) => InkWell(
                      borderRadius: BorderRadius.circular(28),
                      onTap: () {
                        Navigator.pop(context);
                        _sendReaction(e);
                      },
                      child: Padding(
                        padding: const EdgeInsets.all(8),
                        child: Text(e, style: const TextStyle(fontSize: 32)),
                      ),
                    ))
                .toList(),
          ),
        ),
      ),
    );
  }

  // -------------------------------------------------------------------
  // Live captions — on-device STT on our own mic, broadcast as finished
  // lines over the signal data channel (see field comment above).
  // -------------------------------------------------------------------
  Future<void> _toggleCaptions() async {
    if (_captionsOn) {
      setState(() => _captionsOn = false);
      await _speech?.stop();
      if (!mounted) return;
      setState(() {
        _captionsListening = false;
        _myPartialCaption = '';
      });
      return;
    }
    _speech ??= stt.SpeechToText();
    if (!_captionsUnavailable) {
      final ok = await _speech!.initialize(
        onError: (stt.SpeechRecognitionError e) {
          if (!mounted) return;
          setState(() => _captionsListening = false);
          // A transient no-speech/timeout error just means the current
          // listen() burst ended with silence — restart it rather than
          // treating it as a hard failure, as long as captions are still
          // meant to be on.
          if (_captionsOn) _startCaptionListenBurst();
        },
        onStatus: (status) {
          if (!mounted) return;
          if (status == 'done' || status == 'notListening') {
            setState(() => _captionsListening = false);
            // speech_to_text stops itself after each pause in speech —
            // immediately kick off another burst so captions feel
            // continuous instead of a single one-shot recognition.
            if (_captionsOn) _startCaptionListenBurst();
          }
        },
      );
      if (!ok) {
        setState(() => _captionsUnavailable = true);
        _snack('Speech recognition is not available on this device.');
        return;
      }
    }
    setState(() => _captionsOn = true);
    _startCaptionListenBurst();
  }

  void _startCaptionListenBurst() {
    final speech = _speech;
    if (speech == null || !_captionsOn || !mounted) return;
    setState(() => _captionsListening = true);
    speech.listen(
      onResult: (result) {
        if (!mounted) return;
        if (result.finalResult) {
          final text = result.recognizedWords.trim();
          setState(() => _myPartialCaption = '');
          if (text.isNotEmpty) {
            _addCaptionLine('You', text);
            final myName = _participants
                .firstWhere(
                  (p) => p.user.id.toString() == _localIdentity(),
                  orElse: () => SessionParticipant(id: 0, sessionId: widget.sessionId, user: UserMini(id: 0, username: '', fullName: 'You'), role: ParticipantRole.student, joinedAt: DateTime.now()),
                )
                .user
                .fullName;
            _sendSignal({'t': 'caption', 'text': text, 'name': myName});
          }
        } else {
          setState(() => _myPartialCaption = result.recognizedWords);
        }
      },
      listenFor: const Duration(seconds: 30),
      pauseFor: const Duration(seconds: 3),
      partialResults: true,
      cancelOnError: false,
    );
  }

  void _addCaptionLine(String speaker, String text) {
    if (!mounted) return;
    setState(() {
      _captionFeed.add(_CaptionLine(speaker: speaker, text: text, at: DateTime.now()));
      // Keep the feed short — this is a live strip, not a transcript log.
      if (_captionFeed.length > 3) _captionFeed.removeAt(0);
    });
    _captionLineExpiryTimer?.cancel();
    _captionLineExpiryTimer = Timer(const Duration(seconds: 6), () {
      if (!mounted) return;
      setState(() {
        if (_captionFeed.isNotEmpty) _captionFeed.removeAt(0);
      });
    });
  }

  // -------------------------------------------------------------------
  // Breakout rooms (host creates/assigns/closes; everyone else just reads
  // their own assignment). See file header for the assumed REST shape.
  // -------------------------------------------------------------------
  Future<void> _loadBreakoutRooms({bool silent = false}) async {
    if (!silent) setState(() => _breakoutLoading = true);
    try {
      final rooms = await LiveClassApi.breakoutRooms.list(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _breakoutRooms = rooms;
        _breakoutLoading = false;
      });
    } catch (e) {
      // Silent by design (mirrors _loadMaterials/_loadNotices' own
      // silent-poll failure handling) — the backend endpoint may simply
      // not exist yet (see file header), and a background poll shouldn't
      // spam a snackbar every 6 seconds while that's the case.
      if (!silent && mounted) setState(() => _breakoutLoading = false);
    }
  }

  Future<void> _createBreakoutRooms(int roomCount) async {
    if (!_isHost || _breakoutBusy) return;
    setState(() => _breakoutBusy = true);
    try {
      final rooms = await LiveClassApi.breakoutRooms.create(sessionId: widget.sessionId, roomCount: roomCount);
      if (!mounted) return;
      setState(() {
        _breakoutRooms = rooms;
        _breakoutBusy = false;
      });
      _snack('$roomCount breakout rooms created.');
    } on LiveClassApiException catch (e) {
      if (!mounted) return;
      setState(() => _breakoutBusy = false);
      _snack(e.message);
    } catch (_) {
      if (!mounted) return;
      setState(() => _breakoutBusy = false);
      _snack('Could not create breakout rooms.');
    }
  }

  Future<void> _assignToBreakoutRoom(SessionParticipant participant, int? roomNumber) async {
    if (!_isHost || _breakoutBusy) return;
    setState(() => _breakoutBusy = true);
    try {
      final rooms = await LiveClassApi.breakoutRooms.assign(
        sessionId: widget.sessionId,
        participantId: participant.id,
        roomNumber: roomNumber,
      );
      if (!mounted) return;
      setState(() {
        _breakoutRooms = rooms;
        _breakoutBusy = false;
      });
    } on LiveClassApiException catch (e) {
      if (!mounted) return;
      setState(() => _breakoutBusy = false);
      _snack(e.message);
    } catch (_) {
      if (!mounted) return;
      setState(() => _breakoutBusy = false);
      _snack('Could not assign.');
    }
  }

  Future<void> _closeBreakoutRooms() async {
    if (!_isHost || _breakoutBusy) return;
    setState(() => _breakoutBusy = true);
    try {
      await LiveClassApi.breakoutRooms.close(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _breakoutRooms = [];
        _breakoutBusy = false;
      });
      _snack('Everyone is back in the main room.');
    } on LiveClassApiException catch (e) {
      if (!mounted) return;
      setState(() => _breakoutBusy = false);
      _snack(e.message);
    } catch (_) {
      if (!mounted) return;
      setState(() => _breakoutBusy = false);
      _snack('Could not close breakout rooms.');
    }
  }

  /// Host-only management sheet: create N rooms (if none running), assign
  /// each participant to a room via a simple picker, or close everything
  /// back to the main room. Uses a StatefulBuilder so the sheet re-renders
  /// on every assign without tearing down/rebuilding the whole screen
  /// behind it (same trick the participants panel doesn't need since it's
  /// already part of the main setState tree, but a modal sheet is not).
  Future<void> _openBreakoutSheet() async {
    if (!_isHost) return;
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF15171C),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (sheetContext) => StatefulBuilder(
        builder: (sheetContext, setSheetState) {
          Future<void> refresh() async {
            await _loadBreakoutRooms();
            setSheetState(() {});
          }

          return SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Breakout Rooms', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 16)),
                  const SizedBox(height: 14),
                  if (_breakoutRooms.isEmpty) ...[
                    const Text('No breakout room is active right now.', style: TextStyle(color: Colors.white54, fontSize: 13)),
                    const SizedBox(height: 14),
                    Row(
                      children: [2, 3, 4, 5]
                          .map((n) => Padding(
                                padding: const EdgeInsets.only(right: 8),
                                child: OutlinedButton(
                                  onPressed: _breakoutBusy
                                      ? null
                                      : () async {
                                          await _createBreakoutRooms(n);
                                          await refresh();
                                        },
                                  child: Text('$n rooms'),
                                ),
                              ))
                          .toList(),
                    ),
                  ] else ...[
                    SizedBox(
                      height: 280,
                      child: ListView(
                        children: _participants.where((p) => p.role != ParticipantRole.host).map((p) {
                          final identity = p.user.id.toString();
                          int? currentRoom;
                          for (final room in _breakoutRooms) {
                            if (room.participantIdentities.contains(identity)) currentRoom = room.roomNumber;
                          }
                          return ListTile(
                            dense: true,
                            title: Text(p.user.fullName, style: const TextStyle(color: Colors.white, fontSize: 13.5)),
                            trailing: DropdownButton<int?>(
                              value: currentRoom,
                              dropdownColor: const Color(0xFF1F2229),
                              hint: const Text('Unassigned', style: TextStyle(color: Colors.white38, fontSize: 12)),
                              style: const TextStyle(color: Colors.white, fontSize: 12.5),
                              items: [
                                const DropdownMenuItem<int?>(value: null, child: Text('Unassigned')),
                                for (final room in _breakoutRooms)
                                  DropdownMenuItem<int?>(value: room.roomNumber, child: Text('Room ${room.roomNumber}')),
                              ],
                              onChanged: _breakoutBusy
                                  ? null
                                  : (roomNumber) async {
                                      await _assignToBreakoutRoom(p, roomNumber);
                                      setSheetState(() {});
                                    },
                            ),
                          );
                        }).toList(),
                      ),
                    ),
                    const SizedBox(height: 14),
                    SizedBox(
                      width: double.infinity,
                      child: OutlinedButton.icon(
                        icon: const Icon(Icons.close_rounded, size: 18),
                        label: const Text('Close All Rooms'),
                        onPressed: _breakoutBusy
                            ? null
                            : () async {
                                await _closeBreakoutRooms();
                                if (sheetContext.mounted) Navigator.pop(sheetContext);
                              },
                      ),
                    ),
                  ],
                ],
              ),
            ),
          );
        },
      ),
    );
  }

  // -------------------------------------------------------------------
  // Camera flip (front/back) — missing from the control bar before; every
  // other real video-call app lets you switch which camera is live
  // mid-call, not just at the moment you first grant permission.
  // -------------------------------------------------------------------
  Future<void> _flipCamera() async {
    final track = _localCameraVideoTrack();
    if (track is! lk.LocalVideoTrack) {
      _snack('Camera is not active right now.');
      return;
    }
    try {
      // NOTE: on current livekit_client, `LocalVideoTrackExt.switchCamera`
      // takes a required `deviceId` (no bare front/back toggle overload
      // any more — that was a livekit_client ~2.3.x signature). Use the
      // `setCameraPosition` extension instead, which does take a plain
      // front/back `CameraPosition` and is exactly the toggle this needs.
      _isFrontCameraInRoom = !_isFrontCameraInRoom;
      await track.setCameraPosition(_isFrontCameraInRoom ? lk.CameraPosition.front : lk.CameraPosition.back);
    } catch (e) {
      debugPrint('Camera flip failed: $e');
      _snack('Could not switch camera.');
    }
  }

  /// Same flip, but for the green room's local-only preview track — kept
  /// separate from [_flipCamera] since the preview track and the real
  /// in-room track are two different `LocalVideoTrack` instances (see file
  /// header: green room "never touches the actual session join").
  Future<void> _flipGreenRoomCamera() async {
    final track = _previewTrack;
    if (track == null) return;
    try {
      // See _flipCamera's note — `setCameraPosition` is the current
      // front/back toggle API.
      _isFrontCameraGreenRoom = !_isFrontCameraGreenRoom;
      await track.setCameraPosition(_isFrontCameraGreenRoom ? lk.CameraPosition.front : lk.CameraPosition.back);
      if (mounted) setState(() {});
    } catch (e) {
      debugPrint('Green room camera flip failed: $e');
      _snack('Could not switch camera.');
    }
  }

  // -------------------------------------------------------------------
  // Battery-aware camera-off suggestion (see _startPolling's 2-minute
  // check). Same "passive indicator becomes an actionable nudge" pattern
  // as the connection-quality -> audio-only suggestion above — camera is
  // by far the biggest battery draw on this screen, so once per session,
  // when the battery is actually low and not charging, offer the one
  // thing that reliably helps.
  // -------------------------------------------------------------------
  Future<void> _checkBatteryForCameraSuggestion() async {
    if (_suggestedCameraOffForBattery || !_camOn) return;
    try {
      final state = await _battery.batteryState;
      if (state == BatteryState.charging || state == BatteryState.full) return;
      final level = await _battery.batteryLevel;
      if (!mounted || level > 20 || !_camOn) return;
      _suggestedCameraOffForBattery = true;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Battery at $level% — turn off camera to save battery?'),
          action: SnackBarAction(label: 'Camera Off', onPressed: _toggleCam),
          duration: const Duration(seconds: 8),
        ),
      );
    } catch (e) {
      debugPrint('Battery check failed: $e');
    }
  }

  // -------------------------------------------------------------------
  // Low-bandwidth / audio-only mode
  // -------------------------------------------------------------------
  // NOTE: uses `RemoteTrackPublication.unsubscribe()`/`.subscribe()` — the
  // `setSubscribed(bool)` method this was originally written against
  // (livekit_client ~2.3.x assumption, same as the TrackSubscribedEvent
  // guard above) was removed as of livekit_client 2.0.0.
  Future<void> _toggleAudioOnly() async {
    final next = !_audioOnlyMode;
    setState(() => _audioOnlyMode = next);
    final room = _lkRoom;
    if (next) {
      _camOnBeforeAudioOnly = _camOn;
      if (_camOn) await _toggleCam();
      if (room != null) {
        for (final rp in room.remoteParticipants.values) {
          for (final pub in rp.videoTrackPublications) {
            try {
              await pub.unsubscribe();
            } catch (e) {
              debugPrint('Audio-only unsubscribe failed: $e');
            }
          }
        }
      }
      _snack('Audio-only mode on — everyone\'s video is off, only audio will run.');
    } else {
      if (room != null) {
        for (final rp in room.remoteParticipants.values) {
          for (final pub in rp.videoTrackPublications) {
            try {
              await pub.subscribe();
            } catch (e) {
              debugPrint('Audio-only resubscribe failed: $e');
            }
          }
        }
      }
      if (_camOnBeforeAudioOnly && !_camOn) await _toggleCam();
      _snack('Audio-only mode off — video is back on.');
    }
    if (mounted) _lkRefresh();
  }

  // -------------------------------------------------------------------
  // Whiteboard — drawing capture, local paint state, and the data-channel
  // sync helpers _handleDataReceived dispatches into above.
  // -------------------------------------------------------------------
  void _openWhiteboard() {
    setState(() => _whiteboardOpen = true);
    if (_wbStrokes.isEmpty) {
      // Catch up on whatever's already been drawn — see the FEATURE note
      // on _whiteboardOpen above for why this is peer-to-peer, not a
      // server fetch.
      _sendSignal({'t': 'wb_request_sync'});
    }
  }

  void _wbStartStroke(Offset localPos) {
    if (_wbCanvasSize.width == 0 || _wbCanvasSize.height == 0) return;
    final myIdentity = _localIdentity() ?? 'me';
    final id = '${myIdentity}_${DateTime.now().microsecondsSinceEpoch}';
    final color = _wbErasing ? Colors.white : _wbColor;
    final width = _wbErasing ? 22.0 : _wbWidth;
    final norm = Offset(localPos.dx / _wbCanvasSize.width, localPos.dy / _wbCanvasSize.height);
    final stroke = _WhiteboardStroke(id: id, authorIdentity: myIdentity, colorValue: color.value, width: width, points: [norm]);
    setState(() {
      _wbStrokes[id] = stroke;
      _wbStrokeOrder.add(id);
      _wbActiveStrokeId = id;
    });
    _sendSignal({'t': 'wb_point', 'sid': id, 'c': color.value, 'w': width, 'x': norm.dx, 'y': norm.dy, 'new': true});
  }

  void _wbAppendPoint(Offset localPos) {
    final id = _wbActiveStrokeId;
    if (id == null || _wbCanvasSize.width == 0 || _wbCanvasSize.height == 0) return;
    final norm = Offset(localPos.dx / _wbCanvasSize.width, localPos.dy / _wbCanvasSize.height);
    setState(() => _wbStrokes[id]?.points.add(norm));
    _sendSignal({'t': 'wb_point', 'sid': id, 'x': norm.dx, 'y': norm.dy});
  }

  void _wbEndStroke() {
    final id = _wbActiveStrokeId;
    if (id == null) return;
    _wbActiveStrokeId = null;
    _sendSignal({'t': 'wb_stroke_end', 'sid': id});
  }

  /// Applies an incoming 'wb_point' payload from _handleDataReceived —
  /// creates the stroke on first sight (`new: true`, carries color/width)
  /// or just appends to an already-known one.
  void _wbHandlePoint(Map<String, dynamic> payload) {
    final sid = payload['sid'] as String?;
    final x = (payload['x'] as num?)?.toDouble();
    final y = (payload['y'] as num?)?.toDouble();
    if (sid == null || x == null || y == null) return;
    setState(() {
      var stroke = _wbStrokes[sid];
      if (stroke == null) {
        stroke = _WhiteboardStroke(
          id: sid,
          authorIdentity: sid.split('_').first,
          colorValue: (payload['c'] as num?)?.toInt() ?? Colors.black.value,
          width: (payload['w'] as num?)?.toDouble() ?? 4.0,
          points: [],
        );
        _wbStrokes[sid] = stroke;
        _wbStrokeOrder.add(sid);
      }
      stroke.points.add(Offset(x, y));
    });
  }

  void _wbClear() {
    if (!_isHost) return;
    setState(() {
      _wbStrokes.clear();
      _wbStrokeOrder.clear();
    });
    _sendSignal({'t': 'wb_clear'});
  }

  /// Snapshots the on-screen board via [_wbRepaintKey]'s RepaintBoundary —
  /// exactly what's currently drawn, including anyone else's strokes synced
  /// in over the data channel — into raw PNG bytes. Shared by
  /// [_exportWhiteboardPdf] and [_saveWhiteboardToGallery] so both export
  /// paths capture the identical snapshot logic.
  Future<Uint8List?> _captureWhiteboardPng() async {
    final boundary = _wbRepaintKey.currentContext?.findRenderObject() as RenderRepaintBoundary?;
    if (boundary == null) return null;
    // pixelRatio 2.0 for a reasonably crisp export without the file
    // getting huge — this is a hand-drawn scribble board, not a photo.
    final image = await boundary.toImage(pixelRatio: 2.0);
    final byteData = await image.toByteData(format: ui.ImageByteFormat.png);
    return byteData?.buffer.asUint8List();
  }

  /// Exports the board to a PDF and hands it to the system share sheet so
  /// the student can save/share/print it. A local-only action — nothing
  /// about this is broadcast, same as [_wbUndo] only ever touching one's
  /// own strokes.
  Future<void> _exportWhiteboardPdf() async {
    if (_wbExporting) return;
    setState(() => _wbExporting = true);
    try {
      final pngBytes = await _captureWhiteboardPng();
      if (pngBytes == null) {
        _snack('Could not export whiteboard.');
        return;
      }

      final doc = pw.Document();
      final pdfImage = pw.MemoryImage(pngBytes);
      doc.addPage(
        pw.Page(
          pageFormat: PdfPageFormat.a4,
          build: (context) => pw.Center(child: pw.Image(pdfImage, fit: pw.BoxFit.contain)),
        ),
      );
      final bytes = await doc.save();
      final title = _session?.classroomTitle.isNotEmpty == true ? _session!.classroomTitle : 'live_class';
      final safeTitle = title.replaceAll(RegExp(r'[^A-Za-z0-9_-]'), '_');
      await Printing.sharePdf(bytes: bytes, filename: '${safeTitle}_whiteboard.pdf');
    } catch (e) {
      debugPrint('Whiteboard PDF export failed: $e');
      if (mounted) _snack('Could not export whiteboard.');
    } finally {
      if (mounted) setState(() => _wbExporting = false);
    }
  }

  /// Saves the board straight to the device's photo gallery as a PNG —
  /// quicker than the PDF share-sheet round-trip when a student just wants
  /// the picture, not a document. Same snapshot, different destination.
  /// NOTE: `Gal.putImageBytes` / `GalException.type` / `GalExceptionType`
  /// are the `gal` ~2.3.x API for this — recheck against the resolved
  /// version's CHANGELOG if this doesn't compile (same version-pin caution
  /// as this file's other third-party-package calls).
  Future<void> _saveWhiteboardToGallery() async {
    if (_wbExporting) return;
    setState(() => _wbExporting = true);
    try {
      final pngBytes = await _captureWhiteboardPng();
      if (pngBytes == null) {
        _snack('Could not save whiteboard.');
        return;
      }
      final title = _session?.classroomTitle.isNotEmpty == true ? _session!.classroomTitle : 'live_class';
      final safeTitle = title.replaceAll(RegExp(r'[^A-Za-z0-9_-]'), '_');
      await Gal.putImageBytes(pngBytes, name: '${safeTitle}_whiteboard');
      if (mounted) _snack('Whiteboard saved to gallery.');
    } on GalException catch (e) {
      debugPrint('Whiteboard gallery save failed: ${e.type}');
      if (mounted) {
        _snack(e.type == GalExceptionType.accessDenied
            ? 'Please allow gallery access in settings.'
            : 'Could not save whiteboard.');
      }
    } catch (e) {
      debugPrint('Whiteboard gallery save failed: $e');
      if (mounted) _snack('Could not save whiteboard.');
    } finally {
      if (mounted) setState(() => _wbExporting = false);
    }
  }

  /// Undo — only ever removes MY OWN last stroke, never someone else's;
  /// keeps this a personal "oops" button rather than a shared edit-war
  /// tool. Host still has the separate hard "Clear" for wiping everything.
  void _wbUndo() {
    final myIdentity = _localIdentity();
    if (myIdentity == null) return;
    for (var i = _wbStrokeOrder.length - 1; i >= 0; i--) {
      final id = _wbStrokeOrder[i];
      if (_wbStrokes[id]?.authorIdentity == myIdentity) {
        setState(() {
          _wbStrokes.remove(id);
          _wbStrokeOrder.removeAt(i);
        });
        _sendSignal({'t': 'wb_undo', 'sid': id});
        return;
      }
    }
  }

  Future<void> _disconnectLiveKit() async {
    try {
      await _lkListener?.dispose();
      await _lkRoom?.disconnect();
      await _lkRoom?.dispose();
    } catch (e) {
      debugPrint('LiveKit teardown error: $e');
    } finally {
      _lkListener = null;
      _lkRoom = null;
    }
  }

  /// Live truth for OUR OWN mic — mirrors [_isRemoteMicMuted] but for the
  /// local participant. Needed because `_micOn` only tracks OUR OWN taps on
  /// the control-bar mic button; when the host force-mutes us via
  /// [_toggleParticipantMute]'s server-side call (see that method's own
  /// comment), LiveKit mutes our published track directly without us ever
  /// touching `_micOn` — so anything that needs to know "am I actually
  /// muted right now" (e.g. showing the ask-to-unmute button) has to read
  /// the track, not the button's optimistic flag.
  bool _isMyMicMuted() {
    final pubs = _lkRoom?.localParticipant?.audioTrackPublications;
    if (pubs == null) return !_micOn; // not connected to LiveKit yet — fall back to the toggle's own state
    for (final pub in pubs) {
      if (pub.source == lk.TrackSource.microphone) return pub.muted;
    }
    return !_micOn; // no mic publication at all yet
  }

  lk.VideoTrack? _localCameraVideoTrack() {
    final pubs = _lkRoom?.localParticipant?.videoTrackPublications;
    if (pubs == null) return null;
    for (final pub in pubs) {
      if (pub.source == lk.TrackSource.camera) {
        final t = pub.track;
        if (t is lk.VideoTrack) return t;
      }
    }
    return null;
  }

  /// [identity] must match the LiveKit token identity — the server issues
  /// tokens with `identity = str(user_id)` (see livekit_utils.py), so we
  /// look remote participants up by `user.id.toString()`.
  lk.VideoTrack? _remoteCameraTrack(String identity) {
    final room = _lkRoom;
    if (room == null) return null;
    for (final rp in room.remoteParticipants.values) {
      if (rp.identity != identity) continue;
      for (final pub in rp.videoTrackPublications) {
        if (pub.source == lk.TrackSource.camera && pub.subscribed) {
          final t = pub.track;
          if (t is lk.VideoTrack) return t;
        }
      }
    }
    return null;
  }

  // -------------------------------------------------------------------
  // LiveKit — local mic/cam/screen-share toggles
  // -------------------------------------------------------------------
  Future<void> _toggleMic() async {
    final next = !_micOn;
    setState(() => _micOn = next); // optimistic — instant button feedback
    try {
      await _lkRoom?.localParticipant?.setMicrophoneEnabled(next);
    } catch (e) {
      if (!mounted) return;
      setState(() => _micOn = !next); // revert on failure (e.g. permission denied)
      _snack('Could not toggle mic — please check permissions.');
    }
  }

  Future<void> _toggleCam() async {
    final next = !_camOn;
    setState(() => _camOn = next);
    try {
      await _lkRoom?.localParticipant?.setCameraEnabled(next);
    } catch (e) {
      if (!mounted) return;
      setState(() => _camOn = !next);
      _snack('Could not toggle camera — please check permissions.');
    }
  }

  Future<void> _toggleScreenShare() async {
    final next = !_screenSharing;
    try {
      // Android: needs a foreground-service + MediaProjection prompt under
      // the hood, handled by livekit_client/flutter_webrtc — just await it.
      await _lkRoom?.localParticipant?.setScreenShareEnabled(next);
      if (!mounted) return;
      setState(() => _screenSharing = next);
    } catch (e) {
      debugPrint('Screen share toggle failed: $e');
      _snack('Could not start screen share.');
    }
  }

  // -------------------------------------------------------------------
  // Chat
  // -------------------------------------------------------------------
  Future<void> _loadChat({bool silent = false}) async {
    if (!silent) setState(() => _chatLoading = true);
    try {
      final page = await LiveClassApi.chatMessages.list(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _chatMessages
          ..clear()
          ..addAll(page.results.where((m) => !m.isDeleted));
        _chatLoading = false;
      });
    } catch (_) {
      // A silent background refresh failing is not worth interrupting the
      // user over — just try again on the next tick.
      if (mounted && !silent) setState(() => _chatLoading = false);
    }
  }

  Future<void> _sendChat() async {
    final text = _chatCtrl.text.trim();
    if (text.isEmpty || _sendingChat) return;
    setState(() => _sendingChat = true);
    try {
      final msg = await LiveClassApi.chatMessages.send(sessionId: widget.sessionId, message: text);
      if (!mounted) return;
      setState(() {
        _chatMessages.add(msg);
        _chatCtrl.clear();
        _sendingChat = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _sendingChat = false);
      _snack(e is LiveClassApiException ? e.message : 'Could not send message.');
    }
  }

  Future<void> _deleteChat(ChatMessage msg) async {
    try {
      await LiveClassApi.chatMessages.delete(msg.id);
      if (!mounted) return;
      setState(() => _chatMessages.removeWhere((m) => m.id == msg.id));
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not delete.');
    }
  }

  // -------------------------------------------------------------------
  // Polls
  // -------------------------------------------------------------------
  Future<void> _loadPolls({bool silent = false}) async {
    if (!silent) setState(() => _pollsLoading = true);
    try {
      final page = await LiveClassApi.polls.list(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _polls = page.results;
        _pollsLoading = false;
      });
    } catch (_) {
      if (mounted && !silent) setState(() => _pollsLoading = false);
    }
  }

  Future<void> _vote(LivePoll poll, int optionIndex) async {
    if (_myVotes.containsKey(poll.id)) return;
    setState(() => _myVotes[poll.id] = optionIndex);
    try {
      await LiveClassApi.polls.vote(poll.id, optionIndex);
      _loadPolls();
    } catch (e) {
      if (!mounted) return;
      setState(() => _myVotes.remove(poll.id));
      _snack(e is LiveClassApiException ? e.message : 'Could not submit vote.');
    }
  }

  Future<void> _closePoll(LivePoll poll) async {
    try {
      await LiveClassApi.polls.close(poll.id);
      _loadPolls();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not close poll.');
    }
  }

  Future<void> _createPoll() async {
    final created = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => _CreatePollSheet(sessionId: widget.sessionId),
    );
    if (created == true) _loadPolls();
  }

  // -------------------------------------------------------------------
  // Materials — read-only inside the live room; uploading stays on
  // Classroom Detail (host manages materials there, same as before).
  // -------------------------------------------------------------------
  Future<void> _loadMaterials({bool silent = false}) async {
    final classroomId = _classroomId;
    if (classroomId == null) return;
    if (!silent) setState(() => _materialsLoading = true);
    try {
      final page = await LiveClassApi.materials.list(classroomId: classroomId);
      if (!mounted) return;
      setState(() {
        _materials = page.results;
        _materialsLoading = false;
      });
    } catch (_) {
      if (mounted && !silent) setState(() => _materialsLoading = false);
    }
  }

  /// A material can be an uploaded file OR an external link — whichever
  /// it is, the readable URL just gets copied to the clipboard (no
  /// url_launcher wired up in this file — see the SETUP notes at the top
  /// for the package list this screen already depends on) so the user can
  /// paste it into a browser/downloads app themselves.
  void _copyMaterialLink(ClassMaterial m) {
    final url = m.materialType == MaterialType.link ? m.externalLink : (m.file ?? '');
    if (url.isEmpty) {
      _snack('No link/file found for this material.');
      return;
    }
    Clipboard.setData(ClipboardData(text: url));
    _snack('Link copied — paste it into a browser to open.');
  }

  // -------------------------------------------------------------------
  // Doubts / Queries — the async "ask now, get answered when the host
  // has a moment" channel, now reachable without leaving the live room.
  // -------------------------------------------------------------------
  Future<void> _loadQueries({bool silent = false}) async {
    final classroomId = _classroomId;
    if (classroomId == null) return;
    if (!silent) setState(() => _queriesLoading = true);
    try {
      final page = await LiveClassApi.queries.list(classroomId);
      if (!mounted) return;
      setState(() {
        // Open doubts first (most actionable for the host), newest of
        // each group first.
        _queries = [...page.results]
          ..sort((a, b) {
            if (a.status != b.status) return a.status == QueryStatus.open ? -1 : 1;
            return b.createdAt.compareTo(a.createdAt);
          });
        _queriesLoading = false;
      });
    } catch (_) {
      if (mounted && !silent) setState(() => _queriesLoading = false);
    }
  }

  Future<void> _askQuery() async {
    final text = _queryCtrl.text.trim();
    final classroomId = _classroomId;
    if (text.isEmpty || classroomId == null || _askingQuery) return;
    setState(() => _askingQuery = true);
    try {
      await LiveClassApi.queries.ask(ClassQuery(
        id: 0,
        classroomId: classroomId,
        sessionId: widget.sessionId,
        askedBy: UserMini(id: 0, username: '', fullName: ''), // ignored by toJson() — server fills the real asker in
        question: text,
        createdAt: DateTime.now(),
      ));
      _queryCtrl.clear();
      await _loadQueries();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not post the doubt.');
    } finally {
      if (mounted) setState(() => _askingQuery = false);
    }
  }

  /// Host/co-teacher/moderator only.
  Future<void> _answerQuery(ClassQuery q) async {
    final ctrl = TextEditingController();
    final answer = await showDialog<String>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Answer the doubt'),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          minLines: 2,
          maxLines: 5,
          decoration: const InputDecoration(hintText: 'Write your answer…'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, ctrl.text.trim()),
            child: const Text('Send'),
          ),
        ],
      ),
    );
    if (answer == null || answer.isEmpty) return;
    try {
      await LiveClassApi.queries.answer(q.id, answer);
      _loadQueries();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Error while sending the answer.');
    }
  }

  // -------------------------------------------------------------------
  // Waitlist (host only) — who's stuck outside a full session, and
  // promoting a seat open for them without leaving the live room.
  // -------------------------------------------------------------------
  Future<void> _loadWaitlist({bool silent = false}) async {
    if (!silent) setState(() => _waitlistLoading = true);
    try {
      final page = await LiveClassApi.waitlist.forSession(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _waitlist = page.results;
        _waitlistLoading = false;
      });
    } catch (_) {
      if (mounted && !silent) setState(() => _waitlistLoading = false);
    }
  }

  Future<void> _promoteFromWaitlist(SessionWaitlistEntry entry) async {
    try {
      await LiveClassApi.waitlist.promote(entry.id);
      _snack('${entry.student.fullName} has been promoted into the session.');
      _loadWaitlist();
      _loadParticipants();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not promote.');
    }
  }

  // -------------------------------------------------------------------
  // Pinned/urgent notice banner
  // -------------------------------------------------------------------
  Future<void> _loadNotices() async {
    final classroomId = _classroomId;
    if (classroomId == null) return;
    try {
      final page = await LiveClassApi.notices.list(classroomId);
      if (!mounted) return;
      final relevant = page.results.where((n) => n.isPinned && !n.isExpired).toList()
        ..sort((a, b) {
          // Urgent first, then newest.
          if (a.priority != b.priority) {
            if (a.priority == NoticePriority.urgent) return -1;
            if (b.priority == NoticePriority.urgent) return 1;
          }
          return b.createdAt.compareTo(a.createdAt);
        });
      setState(() => _notices = relevant);
    } catch (_) {
      // best-effort — a missed notice refresh isn't worth bothering the user
    }
  }

  // -------------------------------------------------------------------
  // Participants / host controls
  // -------------------------------------------------------------------
  Future<void> _loadParticipants({bool silent = false}) async {
    if (!silent) setState(() => _participantsLoading = true);
    try {
      final page = await LiveClassApi.participants.list(sessionId: widget.sessionId);
      if (!mounted) return;
      final active = page.results.where((p) => p.leftAt == null).toList();
      setState(() {
        _participants = active;
        _participantsLoading = false;
        _syncOwnHandState(active);
      });
    } catch (_) {
      if (mounted && !silent) setState(() => _participantsLoading = false);
    }
  }

  /// Host control: force-mute (or release the mute on) one participant's
  /// mic without removing them — see sessions/{id}/mute/{user_id}/ in
  /// urls.py. No confirmation dialog (unlike _kick) since this is
  /// reversible in one more tap and low-stakes compared to removal.
  Future<void> _toggleParticipantMute(SessionParticipant p) async {
    final identity = p.user.id.toString();
    final currentlyMuted = _isRemoteMicMuted(identity);
    try {
      await LiveClassApi.sessions.muteParticipant(widget.sessionId, p.user.id, muted: !currentlyMuted);
      // LiveKit will push the TrackMutedEvent down and _lkRefresh() will
      // pick up the new state on the next room event — no local state to
      // flip here since _isRemoteMicMuted always reads live truth.
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not mute/unmute mic.');
    }
  }

  /// Host bulk action: mute every currently-unmuted student in one tap
  /// (see the header button in _sidePanel). Reuses the same per-participant
  /// endpoint as [_toggleParticipantMute] rather than a dedicated bulk
  /// route — there wasn't one, and this keeps the same server-side
  /// mute/LiveKit-push behavior per person instead of assuming a bulk
  /// endpoint exists.
  bool _muteAllBusy = false;
  Future<void> _muteAllParticipants() async {
    final targets = _participants.where((p) => p.role != ParticipantRole.host && !_isRemoteMicMuted(p.user.id.toString())).toList();
    if (targets.isEmpty) return;
    setState(() => _muteAllBusy = true);
    var failures = 0;
    for (final p in targets) {
      try {
        await LiveClassApi.sessions.muteParticipant(widget.sessionId, p.user.id, muted: true);
      } catch (_) {
        failures++;
      }
    }
    if (!mounted) return;
    setState(() => _muteAllBusy = false);
    _snack(failures == 0 ? 'Everyone has been muted.' : '${targets.length - failures}/${targets.length} muted.');
  }

  Future<void> _kick(SessionParticipant p) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Remove Participant?'),
        content: Text('${p.user.fullName} will be removed from the session.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Remove', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await LiveClassApi.sessions.kick(widget.sessionId, p.user.id);
      final identity = p.user.id.toString();
      if (_spotlightIdentity == identity) _setSpotlight(null);
      _pendingUnmuteRequests.remove(identity);
      _pendingUnmuteNames.remove(identity);
      _snack('${p.user.fullName} has been removed.');
      _loadParticipants();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not remove.');
    }
  }

  // -------------------------------------------------------------------
  // End / Leave
  // -------------------------------------------------------------------
  Future<void> _endSession() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('End Session?'),
        content: const Text('This session will end for everyone.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('End Session', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _actionBusy = true);
    try {
      await LiveClassApi.sessions.end(widget.sessionId);
      await _disconnectLiveKit();
      if (!mounted) return;
      setState(() => _state = _RoomState.ended);
    } catch (e) {
      if (!mounted) return;
      setState(() => _actionBusy = false);
      _snack(e is LiveClassApiException ? e.message : 'Could not end the session.');
    }
  }

  /// FEATURE (comfort): a confirmation before actually leaving — this used
  /// to disconnect immediately on a single tap, the only "leave the room"
  /// action in this screen without one (compare [_endSession] and [_kick],
  /// both of which confirm). A student who fat-fingers the X button or the
  /// bottom call-end button, or a device back-gesture that lands here (see
  /// [_buildRoom]'s PopScope), shouldn't lose their spot in a live class
  /// over it.
  Future<void> _leave() async {
    if (_actionBusy) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Leave Class?'),
        content: const Text('You will leave this live class. You can rejoin later if the class is still running.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Leave', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _actionBusy = true);
    try {
      final pid = _joinResult?.participantId;
      if (pid != null) {
        await LiveClassApi.participants.leave(pid);
      }
    } catch (_) {
      // leaving is best-effort — never block the user from exiting the room
    } finally {
      await _disconnectLiveKit(); // also best-effort, same reasoning
      if (mounted) Navigator.pop(context);
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  // ===========================================================================
  // BUILD
  // ===========================================================================
  @override
  Widget build(BuildContext context) {
    switch (_state) {
      case _RoomState.greenRoom:
        return _buildGreenRoom();
      case _RoomState.joining:
        return const _CenteredState(
          background: Colors.black,
          icon: null,
          spinner: true,
          message: 'Joining the session…',
          textColor: Colors.white70,
        );
      case _RoomState.passRequired:
        return _CenteredState(
          background: Colors.black,
          icon: Icons.lock_outline_rounded,
          message: _errorMessage ?? 'You need a valid pass to enter this class.',
          textColor: Colors.white70,
          actionLabel: 'Go Back',
          onAction: () => Navigator.pop(context),
        );
      case _RoomState.waitlisted:
        return _CenteredState(
          background: Colors.black,
          icon: Icons.hourglass_top_rounded,
          message: 'Session is full — you\'ve been added to the waitlist. You\'ll be notified as soon as a seat opens up.',
          textColor: Colors.white70,
          actionLabel: 'OK',
          onAction: () => Navigator.pop(context),
        );
      case _RoomState.ended:
        return _CenteredState(
          background: Colors.black,
          icon: Icons.check_circle_outline_rounded,
          message: 'Session has ended.',
          textColor: Colors.white70,
          actionLabel: 'Close',
          onAction: () => Navigator.pop(context),
        );
      case _RoomState.error:
        return _CenteredState(
          background: Colors.black,
          icon: Icons.error_outline_rounded,
          message: _errorMessage ?? 'Something went wrong.',
          textColor: Colors.white70,
          actionLabel: 'Try Again',
          onAction: _join,
        );
      case _RoomState.inRoom:
        return _buildRoom();
    }
  }

  // -------------------------------------------------------------------
  // Green room — camera preview + mic/cam pre-check before joining.
  // -------------------------------------------------------------------
  Widget _buildGreenRoom() {
    final title = _session?.classroomTitle.isNotEmpty == true ? _session!.classroomTitle : 'Live Class';
    final camDenied = _camPermState == _DevicePermState.denied || _camPermState == _DevicePermState.permanentlyDenied;
    final micDenied = _micPermState == _DevicePermState.denied || _micPermState == _DevicePermState.permanentlyDenied;

    return Scaffold(
      backgroundColor: _kNavy,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
          child: Column(
            children: [
              Row(
                children: [
                  IconButton(
                    icon: const Icon(Icons.arrow_back_rounded, color: Colors.white70),
                    onPressed: () => Navigator.pop(context),
                  ),
                  Expanded(
                    child: Text(title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600, fontSize: 15)),
                  ),
                  const SizedBox(width: 48), // balances the back button so title stays centered
                ],
              ),
              const SizedBox(height: 8),
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(20),
                  child: Container(
                    width: double.infinity,
                    color: const Color(0xFF111318),
                    child: _greenRoomBusy
                        ? const Center(child: CircularProgressIndicator(color: Colors.white38))
                        : (_greenRoomCamOn && _previewTrack != null)
                            ? lk.VideoTrackRenderer(_previewTrack!)
                            : _greenRoomPlaceholder(camDenied: camDenied),
                  ),
                ),
              ),
              const SizedBox(height: 18),
              if (camDenied || micDenied) ...[
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(color: Colors.white.withOpacity(0.06), borderRadius: BorderRadius.circular(12)),
                  child: Row(
                    children: [
                      const Icon(Icons.warning_amber_rounded, color: Colors.orangeAccent, size: 20),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Text(
                          camDenied && micDenied
                              ? 'Camera and microphone permission is off — you can join the class, but without video/audio.'
                              : camDenied
                                  ? 'Camera permission is off — you will join with audio only.'
                                  : 'Microphone permission is off — you will join with video only (muted).',
                          style: const TextStyle(color: Colors.white70, fontSize: 12.5),
                        ),
                      ),
                      if (_camPermState == _DevicePermState.permanentlyDenied ||
                          _micPermState == _DevicePermState.permanentlyDenied)
                        TextButton(
                          onPressed: ph.openAppSettings,
                          child: const Text('Settings', style: TextStyle(fontSize: 12.5)),
                        ),
                    ],
                  ),
                ),
                const SizedBox(height: 14),
              ],
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  _greenRoomToggle(
                    icon: _greenRoomMicOn ? Icons.mic_rounded : Icons.mic_off_rounded,
                    active: _greenRoomMicOn,
                    enabled: _micPermState == _DevicePermState.granted,
                    onTap: _toggleGreenRoomMic,
                  ),
                  const SizedBox(width: 18),
                  _greenRoomToggle(
                    icon: _greenRoomCamOn ? Icons.videocam_rounded : Icons.videocam_off_rounded,
                    active: _greenRoomCamOn,
                    enabled: _camPermState == _DevicePermState.granted,
                    onTap: _toggleGreenRoomCam,
                  ),
                  if (_greenRoomCamOn && _previewTrack != null) ...[
                    const SizedBox(width: 18),
                    _greenRoomToggle(
                      icon: Icons.cameraswitch_rounded,
                      active: false,
                      enabled: true,
                      onTap: _flipGreenRoomCamera,
                    ),
                  ],
                ],
              ),
              const SizedBox(height: 20),
              SizedBox(
                width: double.infinity,
                child: DecoratedBox(
                  decoration: BoxDecoration(gradient: _kGradient, borderRadius: BorderRadius.circular(12)),
                  child: Material(
                    color: Colors.transparent,
                    child: InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: _greenRoomBusy ? null : _joinFromGreenRoom,
                      child: const Padding(
                        padding: EdgeInsets.symmetric(vertical: 14),
                        child: Center(
                          child: Text('Join Class', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15)),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _greenRoomPlaceholder({required bool camDenied}) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(camDenied ? Icons.videocam_off_rounded : Icons.person_rounded, size: 56, color: Colors.white24),
          const SizedBox(height: 10),
          Text(camDenied ? 'Camera off' : 'Preview unavailable', style: const TextStyle(color: Colors.white38, fontSize: 12.5)),
        ],
      ),
    );
  }

  Widget _greenRoomToggle({required IconData icon, required bool active, required bool enabled, required VoidCallback onTap}) {
    return InkWell(
      onTap: enabled ? onTap : null,
      borderRadius: BorderRadius.circular(30),
      child: Container(
        width: 54,
        height: 54,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: !enabled ? Colors.white10 : (active ? Colors.white24 : Colors.redAccent.withOpacity(0.85)),
        ),
        child: Icon(icon, color: enabled ? Colors.white : Colors.white24, size: 24),
      ),
    );
  }

  Widget _buildRoom() {
    return PopScope(
      canPop: false,
      onPopInvoked: (didPop) async {
        if (didPop || _actionBusy) return;
        if (_isHost) {
          await _endSession();
        } else {
          await _leave();
        }
      },
      child: Scaffold(
        backgroundColor: Colors.black,
        body: SafeArea(
          child: Stack(
            children: [
              Column(
                children: [
                  _roomHeader(),
                  // FEATURE (breakout rooms): a thin banner instead of
                  // making everyone dig into a panel to find out where
                  // they've been put — same "surface it, don't hide it"
                  // spirit as the pinned-notice banner elsewhere in this
                  // file.
                  if (_myBreakoutRoom != null) _breakoutRoomBanner(),
                  Expanded(child: _videoArea()),
                  // FEATURE: live captions -- sits just above the control
                  // bar so it never covers the video itself.
                  if (_captionsOn || _captionFeed.isNotEmpty)
                    _CaptionOverlay(lines: _captionFeed, partial: _captionsOn ? _myPartialCaption : ''),
                  _controlBar(),
                ],
              ),
              if (_openPanel != null) _sidePanel(),
              // FEATURE: collaborative whiteboard. A full overlay rather
              // than another _PanelTab/_sidePanel entry — a 320-wide side
              // strip is unusable for actually drawing on, so this takes
              // the whole room area instead, same footprint as grid view.
              if (_whiteboardOpen) _whiteboardOverlay(),
              // FEATURE: emoji reactions -- floating overlay, sits above
              // everything else (including the whiteboard) same as any
              // real video-call app's reaction burst.
              if (_activeReactions.isNotEmpty) _reactionsOverlay(),
              // FEATURE: in-app mini-view (PiP-style). Only worth showing
              // once something is actually covering the real video (a
              // panel or the whiteboard) — otherwise it would just be a
              // redundant second copy of the same tile sitting on top of
              // itself.
              if (_miniViewOn && (_openPanel != null || _whiteboardOpen))
                _MiniViewTile(
                  offset: _miniViewOffset,
                  onDrag: (next) => setState(() => _miniViewOffset = next),
                  onTapExpand: () => setState(() {
                    _openPanel = null;
                    _whiteboardOpen = false;
                  }),
                  videoChild: _miniViewContent(),
                ),
            ],
          ),
        ),
      ),
    );
  }

  /// What the floating mini-view tile actually shows: whoever's
  /// spotlighted, else our own camera, else a plain placeholder — same
  /// priority order the main stage uses, just for a 120x160 tile instead
  /// of the full screen.
  Widget _miniViewContent() {
    final spotlightTrack = _spotlightIdentity != null ? _remoteCameraTrack(_spotlightIdentity!) : null;
    if (spotlightTrack != null) {
      return ColoredBox(color: Colors.black, child: lk.VideoTrackRenderer(spotlightTrack));
    }
    final myTrack = _localCameraVideoTrack();
    if (_camOn && myTrack != null) {
      return ColoredBox(color: Colors.black, child: lk.VideoTrackRenderer(myTrack));
    }
    return const ColoredBox(
      color: Color(0xFF1B1E26),
      child: Center(child: Icon(Icons.videocam_off_rounded, color: Colors.white38, size: 28)),
    );
  }

  /// FEATURE (breakout rooms): dismissible-in-spirit-only strip (it
  /// reappears on the next room-list refresh, same as the pinned-notice
  /// banner never permanently unpinning) telling a student which room
  /// they're currently assigned to.
  Widget _breakoutRoomBanner() {
    return Container(
      width: double.infinity,
      color: const Color(0xFFFF6A00).withOpacity(0.16),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      child: Row(
        children: [
          const Icon(Icons.meeting_room_rounded, size: 16, color: Color(0xFFFF6A00)),
          const SizedBox(width: 8),
          Expanded(
            child: Text('You are in Breakout Room $_myBreakoutRoom', style: const TextStyle(color: Colors.white, fontSize: 12.5)),
          ),
        ],
      ),
    );
  }

  // -------------------------------------------------------------------
  // Header
  // -------------------------------------------------------------------
  Widget _roomHeader() {
    final title = _session?.classroomTitle.isNotEmpty == true ? _session!.classroomTitle : 'Live Class';
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 8, 8, 8),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(color: Colors.red, borderRadius: BorderRadius.circular(6)),
            child: const Text('LIVE', style: TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.bold)),
          ),
          // FEATURE: session elapsed timer -- own small self-ticking
          // widget (see its class comment) so this doesn't rebuild video
          // tiles every second. Hidden until the first successful connect
          // actually lands (see _lkFirstConnectedAt).
          if (_lkFirstConnectedAt != null) ...[
            const SizedBox(width: 6),
            _ElapsedTimerText(since: _lkFirstConnectedAt!),
          ],
          // FEATURE: recording indicator — visible to EVERYONE in the
          // room (not just the host who can start/stop it), same as any
          // real video-call app: people have a right to know they're
          // being recorded.
          if (_isRecording) ...[
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(
                color: Colors.black45,
                borderRadius: BorderRadius.circular(6),
                border: Border.all(color: Colors.red, width: 1),
              ),
              child: const Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.fiber_manual_record_rounded, color: Colors.red, size: 10),
                  SizedBox(width: 3),
                  Text('REC', style: TextStyle(color: Colors.white, fontSize: 10, fontWeight: FontWeight.bold)),
                ],
              ),
            ),
          ],
          const SizedBox(width: 10),
          Expanded(
            child: Text(title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600, fontSize: 14)),
          ),
          // FEATURE: connection quality -- small heads-up for OUR OWN
          // connection (see _myConnectionQuality's own comment); silent
          // when good/unknown so it doesn't clutter the header the vast
          // majority of the time.
          if (_myConnectionQuality == lk.ConnectionQuality.poor)
            const Padding(
              padding: EdgeInsets.only(right: 6),
              child: Icon(Icons.signal_wifi_statusbar_connected_no_internet_4_rounded, color: Colors.orangeAccent, size: 18),
            )
          else if (_myConnectionQuality == lk.ConnectionQuality.lost)
            const Padding(
              padding: EdgeInsets.only(right: 6),
              child: Icon(Icons.signal_wifi_off_rounded, color: Colors.redAccent, size: 18),
            ),
          // FEATURE: grid view toggle — available to everyone (host or
          // student), not gated on canManage. Only shown once there's
          // actually more than one person in the room; with just the local
          // user, grid and focus mode look identical so the button would be
          // dead weight.
          if (_participants.length > 1)
            IconButton(
              icon: Icon(_gridView ? Icons.crop_square_rounded : Icons.grid_view_rounded, color: Colors.white70, size: 20),
              tooltip: _gridView ? 'Focus View' : 'Grid View',
              onPressed: () => setState(() => _gridView = !_gridView),
            ),
          if (_isHost)
            IconButton(
              icon: const Icon(Icons.call_end_rounded, color: Colors.red),
              tooltip: 'End Session',
              onPressed: _actionBusy ? null : _endSession,
            )
          else
            IconButton(
              icon: const Icon(Icons.close_rounded, color: Colors.white70),
              tooltip: 'Leave',
              onPressed: _actionBusy ? null : _leave,
            ),
        ],
      ),
    );
  }

  // -------------------------------------------------------------------
  // Video area — local camera preview, falling back to an avatar/status
  // placeholder while connecting, camera-off, or on LiveKit failure.
  // -------------------------------------------------------------------
  Widget _localVideoArea() {
    final track = _localCameraVideoTrack();
    if (_camOn && track != null) {
      return lk.VideoTrackRenderer(track);
    }
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (_lkConnecting)
            const CircularProgressIndicator(color: Colors.white38, strokeWidth: 2)
          else
            Icon(_camOn ? Icons.videocam_rounded : Icons.videocam_off_rounded, size: 56, color: Colors.white24),
          const SizedBox(height: 10),
          Text(_isHost ? 'You (Host)' : 'You',
              style: const TextStyle(color: Colors.white54, fontWeight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text(
            _lkConnecting
                ? 'Connecting…'
                : (_screenSharing ? 'Screen sharing…' : (_camOn ? 'Camera on' : 'Camera off')),
            style: const TextStyle(color: Colors.white24, fontSize: 12),
          ),
          if (_lkReconnecting) ...[
            const SizedBox(height: 6),
            const Text('Reconnecting…', style: TextStyle(color: Colors.white38, fontSize: 11.5)),
          ] else if (_lkError != null) ...[
            const SizedBox(height: 6),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              child: Text(_lkError!,
                  textAlign: TextAlign.center, style: const TextStyle(color: Colors.orangeAccent, fontSize: 11.5)),
            ),
            const SizedBox(height: 4),
            TextButton(
              onPressed: _reconnectLiveKit,
              style: TextButton.styleFrom(
                minimumSize: Size.zero,
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                foregroundColor: Colors.white,
                backgroundColor: Colors.white12,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
              ),
              child: const Text('Retry', style: TextStyle(fontSize: 12)),
            ),
          ],
        ],
      ),
    );
  }

  Widget _videoArea() {
    // FEATURE: spotlight/pin. Whoever the host pinned takes over the main
    // tile for EVERYONE, overriding both the default focus layout and grid
    // view — that's the point of a spotlight, it's not just "my own
    // preference for how I view the room" like _gridView is.
    if (_spotlightIdentity != null) return _buildSpotlightVideoArea();

    // FEATURE: grid view. Previously `_gridView` was a dead toggle with no
    // widget actually reading it — flipping it changed nothing on screen.
    // This branches the whole video area between the original single
    // focus-tile + 5-thumbnail-strip layout and a real grid that lays out
    // EVERY participant (no 5-tile cap) at once.
    if (_gridView) return _buildGridVideoArea();

    final others = _participants.where((p) => p.role != ParticipantRole.host).take(5).toList();
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      child: Column(
        children: [
          Expanded(
            child: Container(
              width: double.infinity,
              clipBehavior: Clip.hardEdge,
              decoration: BoxDecoration(color: const Color(0xFF111318), borderRadius: BorderRadius.circular(16)),
              // FEATURE: double-tap-to-react -- the Instagram/TikTok Live
              // "double-tap sends a heart" gesture, applied to the main
              // video tile. A quick, no-menu-needed way to react that
              // people already know from every other live app; the full
              // picker (_showReactionPicker) still covers everything else.
              child: GestureDetector(
                onDoubleTap: () {
                  HapticFeedback.mediumImpact();
                  _sendReaction('❤️');
                },
                child: _localVideoArea(),
              ),
            ),
          ),
          if (others.isNotEmpty) ...[
            const SizedBox(height: 10),
            SizedBox(
              height: 64,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: others.length,
                separatorBuilder: (_, __) => const SizedBox(width: 8),
                itemBuilder: (_, i) {
                  final p = others[i];
                  final track = _remoteCameraTrack(p.user.id.toString());
                  return Container(
                    width: 64,
                    clipBehavior: Clip.hardEdge,
                    decoration: BoxDecoration(color: const Color(0xFF111318), borderRadius: BorderRadius.circular(10)),
                    child: track != null
                        ? lk.VideoTrackRenderer(track)
                        : Center(
                            child: Text(
                              p.user.fullName.isNotEmpty ? p.user.fullName.substring(0, 1).toUpperCase() : '?',
                              style: const TextStyle(color: Colors.white54, fontWeight: FontWeight.bold, fontSize: 18),
                            ),
                          ),
                  );
                },
              ),
            ),
            const SizedBox(height: 10),
          ] else
            const SizedBox(height: 12),
        ],
      ),
    );
  }

  // -------------------------------------------------------------------
  // Spotlight view — the pinned participant fills the main tile; everyone
  // else (including the local user, if they're not the one pinned) shrinks
  // to the same horizontal thumbnail strip the default focus layout uses.
  // -------------------------------------------------------------------
  Widget _buildSpotlightVideoArea() {
    final pinnedIdentity = _spotlightIdentity!;
    final amIPinned = pinnedIdentity == _localIdentity();
    SessionParticipant? pinnedParticipant;
    if (!amIPinned) {
      for (final p in _participants) {
        if (p.user.id.toString() == pinnedIdentity) {
          pinnedParticipant = p;
          break;
        }
      }
    }
    final pinnedName = amIPinned ? (_isHost ? 'You (Host)' : 'You') : (pinnedParticipant?.user.fullName ?? 'Pinned');

    final others = _participants.where((p) => p.user.id.toString() != pinnedIdentity).toList();

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      child: Column(
        children: [
          Row(
            children: [
              const Icon(Icons.push_pin_rounded, color: Color(0xFFFF6A00), size: 14),
              const SizedBox(width: 4),
              Expanded(
                child: Text('Spotlight: $pinnedName',
                    maxLines: 1, overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: Colors.white54, fontSize: 12, fontWeight: FontWeight.w600)),
              ),
              if (_isHost)
                TextButton(
                  onPressed: () => _setSpotlight(null),
                  style: TextButton.styleFrom(minimumSize: Size.zero, padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4)),
                  child: const Text('Clear', style: TextStyle(color: Colors.white54, fontSize: 12)),
                ),
            ],
          ),
          const SizedBox(height: 6),
          Expanded(
            child: Container(
              width: double.infinity,
              clipBehavior: Clip.hardEdge,
              decoration: BoxDecoration(color: const Color(0xFF111318), borderRadius: BorderRadius.circular(16)),
              child: amIPinned ? _localVideoArea() : _remoteTileContent(pinnedIdentity, pinnedName),
            ),
          ),
          if (others.isNotEmpty) ...[
            const SizedBox(height: 10),
            SizedBox(
              height: 64,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: others.length,
                separatorBuilder: (_, __) => const SizedBox(width: 8),
                itemBuilder: (_, i) {
                  final p = others[i];
                  final isMe = p.user.id.toString() == _localIdentity();
                  final track = isMe ? null : _remoteCameraTrack(p.user.id.toString());
                  return Container(
                    width: 64,
                    clipBehavior: Clip.hardEdge,
                    decoration: BoxDecoration(color: const Color(0xFF111318), borderRadius: BorderRadius.circular(10)),
                    child: (isMe && _camOn)
                        ? (_localCameraVideoTrack() != null ? lk.VideoTrackRenderer(_localCameraVideoTrack()!) : _gridAvatarFallback(p.user.fullName))
                        : (track != null ? lk.VideoTrackRenderer(track) : _gridAvatarFallback(p.user.fullName)),
                  );
                },
              ),
            ),
            const SizedBox(height: 10),
          ] else
            const SizedBox(height: 12),
        ],
      ),
    );
  }

  /// Renders a remote participant's camera feed (or an avatar fallback) by
  /// [identity] alone — used by the spotlight tile, which may be showing
  /// someone who isn't in the default 5-thumbnail strip.
  Widget _remoteTileContent(String identity, String fallbackName) {
    final track = _remoteCameraTrack(identity);
    if (track != null) return lk.VideoTrackRenderer(track);
    return Center(
      child: Text(
        fallbackName.isNotEmpty ? fallbackName.substring(0, 1).toUpperCase() : '?',
        style: const TextStyle(color: Colors.white54, fontWeight: FontWeight.bold, fontSize: 40),
      ),
    );
  }

  // -------------------------------------------------------------------
  // Grid view — every participant (local + all remote) in one screen at
  // once, column count adapting to how many people are actually in the
  // room instead of a fixed layout that wastes space with 2 people or
  // overflows with 12.
  // -------------------------------------------------------------------
  Widget _buildGridVideoArea() {
    // Local tile always first; every other current participant (host and
    // students alike, minus ourselves — the local tile already covers us)
    // follows. No take(5) cap — this is exactly the "see everyone at once"
    // gap the 5-thumbnail strip couldn't cover.
    final remoteTiles = _participants.where((p) => p.user.id.toString() != _localIdentity()).toList();
    final tileCount = 1 + remoteTiles.length;
    final crossAxisCount = tileCount <= 1
        ? 1
        : tileCount <= 4
            ? 2
            : tileCount <= 9
                ? 3
                : 4;

    return Padding(
      padding: const EdgeInsets.all(12),
      child: GridView.builder(
        itemCount: tileCount,
        gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: crossAxisCount,
          crossAxisSpacing: 8,
          mainAxisSpacing: 8,
          childAspectRatio: 3 / 4,
        ),
        itemBuilder: (_, i) {
          if (i == 0) {
            final myIdentity = _localIdentity();
            return _gridTile(
              name: _isHost ? 'You (Host)' : 'You',
              videoWidget: _localVideoArea(),
              micOn: _micOn,
              speaking: myIdentity != null && _activeSpeakerIdentities.contains(myIdentity),
              onSpotlightTap: _isHost ? () => _setSpotlight(myIdentity) : null,
            );
          }
          final p = remoteTiles[i - 1];
          final identity = p.user.id.toString();
          final track = _remoteCameraTrack(identity);
          return _gridTile(
            name: p.user.fullName,
            videoWidget: track != null
                ? lk.VideoTrackRenderer(track)
                : _gridAvatarFallback(p.user.fullName),
            micOn: !_isRemoteMicMuted(identity),
            speaking: _activeSpeakerIdentities.contains(identity),
            onSpotlightTap: _isHost ? () => _setSpotlight(identity) : null,
          );
        },
      ),
    );
  }

  Widget _gridTile({
    required String name,
    required Widget videoWidget,
    required bool micOn,
    bool speaking = false,
    VoidCallback? onSpotlightTap,
  }) {
    return Container(
      clipBehavior: Clip.hardEdge,
      decoration: BoxDecoration(
        color: const Color(0xFF111318),
        borderRadius: BorderRadius.circular(12),
        // FEATURE: active speaker highlight -- a thin accent border on
        // whoever LiveKit currently reports as speaking, so grid view
        // shows who's talking without anyone having to guess off a wall
        // of static tiles.
        border: speaking ? Border.all(color: const Color(0xFF2ECC71), width: 2) : null,
      ),
      child: Stack(
        fit: StackFit.expand,
        children: [
          // FEATURE: spotlight — host long-presses a grid tile to pin it
          // for everyone, a quicker path than opening Participants when
          // the grid is already open (short-press stays free for a future
          // "tap to focus for just me" if that's ever wanted).
          onSpotlightTap != null ? GestureDetector(onLongPress: onSpotlightTap, child: videoWidget) : videoWidget,
          Positioned(
            left: 6,
            bottom: 6,
            right: 6,
            child: Row(
              children: [
                Expanded(
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(6)),
                    child: Text(name, maxLines: 1, overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: Colors.white, fontSize: 10.5, fontWeight: FontWeight.w600)),
                  ),
                ),
                if (!micOn) ...[
                  const SizedBox(width: 4),
                  Container(
                    padding: const EdgeInsets.all(3),
                    decoration: const BoxDecoration(color: Colors.black54, shape: BoxShape.circle),
                    child: const Icon(Icons.mic_off_rounded, color: Colors.redAccent, size: 12),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _gridAvatarFallback(String fullName) {
    return Center(
      child: Text(
        fullName.isNotEmpty ? fullName.substring(0, 1).toUpperCase() : '?',
        style: const TextStyle(color: Colors.white54, fontWeight: FontWeight.bold, fontSize: 20),
      ),
    );
  }

  /// This client's own LiveKit identity — matches `str(user_id)` the server
  /// issues tokens with (see livekit_utils.py), read straight off the
  /// connected room's local participant so the grid can exclude "ourselves"
  /// from the remote-tiles list without needing a separate "my user id"
  /// field threaded through from the join response.
  String? _localIdentity() => _lkRoom?.localParticipant?.identity;

  /// Reads live mute truth off the actual LiveKit audio track publication
  /// rather than any local record — a remote participant with no published
  /// microphone track (mic off, or never turned on) is treated the same as
  /// an explicitly server-muted one for display purposes: either way, they
  /// currently aren't sending audio.
  bool _isRemoteMicMuted(String identity) {
    final room = _lkRoom;
    if (room == null) return true;
    for (final rp in room.remoteParticipants.values) {
      if (rp.identity != identity) continue;
      for (final pub in rp.audioTrackPublications) {
        if (pub.source == lk.TrackSource.microphone) return pub.muted;
      }
      return true; // no mic publication at all — nothing being sent
    }
    return true;
  }

  // -------------------------------------------------------------------
  // Collaborative whiteboard
  // -------------------------------------------------------------------
  static const List<Color> _wbSwatches = [
    Color(0xFFEE0979),
    Color(0xFFFF6A00),
    Color(0xFF2ECC71),
    Color(0xFF3498DB),
    Colors.black,
  ];

  Widget _whiteboardOverlay() {
    return Positioned.fill(
      child: Material(
        color: Colors.white,
        child: Column(
          children: [
            Container(
              color: const Color(0xFF15171C),
              padding: const EdgeInsets.fromLTRB(14, 10, 8, 10),
              child: Row(
                children: [
                  const Icon(Icons.draw_rounded, color: Colors.white70, size: 18),
                  const SizedBox(width: 8),
                  const Expanded(
                    child: Text('Whiteboard', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w600, fontSize: 14)),
                  ),
                  IconButton(
                    icon: const Icon(Icons.undo_rounded, color: Colors.white70, size: 20),
                    tooltip: 'Undo my last stroke',
                    onPressed: _wbUndo,
                  ),
                  // FEATURE (advanced): export the board to a PDF and
                  // share/save/print it via the system share sheet --
                  // turns the whiteboard from a purely ephemeral, in-call
                  // scribble pad (nothing survives once the class ends,
                  // see _activeReactions'/whiteboard's own "nothing to
                  // persist" comments elsewhere in this file) into
                  // something a student can actually keep as notes.
                  IconButton(
                    icon: _wbExporting
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white70))
                        : const Icon(Icons.picture_as_pdf_outlined, color: Colors.white70, size: 20),
                    tooltip: 'Save/share as PDF',
                    onPressed: (_wbExporting || _wbStrokes.isEmpty) ? null : _exportWhiteboardPdf,
                  ),
                  // FEATURE (advanced): straight-to-gallery save -- same
                  // snapshot as the PDF export above (see
                  // _captureWhiteboardPng), just skipping the share-sheet
                  // round-trip for when a student just wants the picture.
                  IconButton(
                    icon: const Icon(Icons.image_outlined, color: Colors.white70, size: 20),
                    tooltip: 'Save to gallery',
                    onPressed: (_wbExporting || _wbStrokes.isEmpty) ? null : _saveWhiteboardToGallery,
                  ),
                  if (_isHost)
                    IconButton(
                      icon: const Icon(Icons.delete_sweep_outlined, color: Colors.white70, size: 20),
                      tooltip: 'Clear everyone\'s board',
                      onPressed: _wbClear,
                    ),
                  IconButton(
                    icon: const Icon(Icons.close_rounded, color: Colors.white70, size: 20),
                    onPressed: () => setState(() => _whiteboardOpen = false),
                  ),
                ],
              ),
            ),
            Expanded(
              child: LayoutBuilder(
                builder: (context, constraints) {
                  _wbCanvasSize = constraints.biggest;
                  return GestureDetector(
                    onPanStart: (d) => _wbStartStroke(d.localPosition),
                    onPanUpdate: (d) => _wbAppendPoint(d.localPosition),
                    onPanEnd: (_) => _wbEndStroke(),
                    // FEATURE (advanced): RepaintBoundary here is what lets
                    // _exportWhiteboardPdf snapshot exactly what's on
                    // screen into a PNG for the PDF -- see that method.
                    child: RepaintBoundary(
                      key: _wbRepaintKey,
                      child: CustomPaint(
                        size: constraints.biggest,
                        painter: _WhiteboardPainter(strokes: _wbStrokeOrder.map((id) => _wbStrokes[id]!).toList()),
                      ),
                    ),
                  );
                },
              ),
            ),
            Container(
              color: const Color(0xFF15171C),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              child: Row(
                children: [
                  ..._wbSwatches.map((c) => Padding(
                        padding: const EdgeInsets.only(right: 8),
                        child: InkWell(
                          borderRadius: BorderRadius.circular(16),
                          onTap: () => setState(() {
                            _wbColor = c;
                            _wbErasing = false;
                          }),
                          child: Container(
                            width: 28,
                            height: 28,
                            decoration: BoxDecoration(
                              color: c,
                              shape: BoxShape.circle,
                              border: Border.all(
                                color: (!_wbErasing && _wbColor.value == c.value) ? Colors.white : Colors.transparent,
                                width: 2,
                              ),
                            ),
                          ),
                        ),
                      )),
                  const Spacer(),
                  IconButton(
                    icon: Icon(Icons.backspace_rounded, color: _wbErasing ? const Color(0xFFFF6A00) : Colors.white54, size: 20),
                    tooltip: 'Eraser',
                    onPressed: () => setState(() => _wbErasing = !_wbErasing),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  // -------------------------------------------------------------------
  // Emoji reactions overlay -- floating bursts that rise and fade, driven
  // by [_activeReactions] (see its own comment above).
  // -------------------------------------------------------------------
  Widget _reactionsOverlay() {
    return Positioned(
      right: 6,
      bottom: 88, // sits just above the control bar
      child: IgnorePointer(
        child: SizedBox(
          width: 70,
          height: 220,
          child: Stack(
            children: _activeReactions
                .map((r) => _FloatingReactionWidget(key: ValueKey(r.id), reaction: r))
                .toList(),
          ),
        ),
      ),
    );
  }

  // -------------------------------------------------------------------
  // Bottom control bar
  // -------------------------------------------------------------------
  Widget _controlBar() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 12),
      // FEATURE: with hand-raise/recording/"more" added on top of the
      // original 7 buttons, this row can now run wider than a narrow
      // phone screen (host view alone is up to 9 fixed 48dp circles) —
      // wrapped in a horizontal scroll so it degrades to a swipe instead
      // of a RenderFlex overflow error. The ConstrainedBox pins the Row
      // to at least the screen width so mainAxisAlignment.spaceEvenly
      // still spaces things out the same as before whenever everything
      // already fits.
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: ConstrainedBox(
          constraints: BoxConstraints(minWidth: MediaQuery.of(context).size.width - 8),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: [
              _controlButton(
                icon: _micOn ? Icons.mic_rounded : Icons.mic_off_rounded,
                active: _micOn,
                onTap: _toggleMic,
              ),
              _controlButton(
                icon: _camOn ? Icons.videocam_rounded : Icons.videocam_off_rounded,
                active: _camOn,
                onTap: _toggleCam,
              ),
              _controlButton(
                icon: Icons.screen_share_rounded,
                active: _screenSharing,
                onTap: _toggleScreenShare,
              ),
              // FEATURE: camera flip -- front/back switch mid-call, missing
              // from the control bar before (see _flipCamera's own
              // comment). Only meaningful while the camera is actually on.
              if (_camOn)
                _controlButton(
                  icon: Icons.cameraswitch_rounded,
                  active: false,
                  tooltip: 'Switch camera',
                  onTap: _flipCamera,
                ),
              // FEATURE: emoji reactions -- quick low-friction feedback
              // (see _sendReaction's own comment), available to host and
              // student alike. Badge is the running session total, same
              // "little number that goes up" feedback loop live-streaming
              // apps use to make a room feel alive.
              _controlButton(
                icon: Icons.emoji_emotions_outlined,
                active: false,
                badge: _reactionTotalCount > 0 ? _reactionTotalCount : null,
                tooltip: 'Send reaction',
                onTap: _showReactionPicker,
              ),
              // FEATURE: hand-raise — students' own "wait, let me ask" gesture.
              // Not shown to the host: the host doesn't raise a hand at their
              // own class, they see everyone else's raised hands in the
              // Participants panel instead (see _participantsPanel).
              if (!_isHost)
                _controlButton(
                  icon: _handRaised ? Icons.back_hand_rounded : Icons.back_hand_outlined,
                  active: _handRaised,
                  // FEATURE: hand-raise queue position -- shows the
                  // student their own place in line ("you're #2") instead
                  // of just a raised/lowered toggle with no sense of how
                  // long the wait might be. Only meaningful once the hand
                  // is actually up.
                  badge: _handRaised ? _myHandQueuePosition : null,
                  tooltip: _handRaised && _myHandQueuePosition != null ? 'Queue position #$_myHandQueuePosition' : null,
                  onTap: _toggleHandRaise,
                ),
              // NOTE: the four `Icons.*` constants introduced below
              // (record_voice_over_rounded, hearing_rounded, draw_rounded,
              // push_pin_rounded, backspace_rounded, undo_rounded,
              // delete_sweep_outlined) are standard Material Symbols and
              // should resolve on any reasonably current Flutter SDK — but
              // exact icon-name availability has shifted across Flutter
              // versions before, so if any of these fail to resolve, swap
              // in whatever equivalent your pinned SDK's Icons class offers
              // (same spirit as this file's other version-pin cautions).
              // FEATURE: ask-to-unmute — only worth showing to a student
              // who's actually muted right now; once they unmute themselves
              // (or get approved and do so), the button just disappears
              // rather than sitting there doing nothing.
              if (!_isHost && !_micOn)
                _controlButton(
                  icon: Icons.record_voice_over_rounded,
                  active: false,
                  tooltip: 'Send unmute request',
                  onTap: _requestUnmute,
                ),
              // FEATURE: low-bandwidth / audio-only mode — local-only, so
              // available to host and student alike.
              _controlButton(
                icon: _audioOnlyMode ? Icons.wifi_off_rounded : Icons.hearing_rounded,
                active: _audioOnlyMode,
                tooltip: _audioOnlyMode ? 'Turn off audio-only' : 'Audio-only mode (lower bandwidth)',
                onTap: _toggleAudioOnly,
              ),
              // FEATURE: collaborative whiteboard.
              _controlButton(
                icon: Icons.draw_rounded,
                active: _whiteboardOpen,
                tooltip: 'Whiteboard',
                onTap: () => _whiteboardOpen ? setState(() => _whiteboardOpen = false) : _openWhiteboard(),
              ),
              // FEATURE: live captions -- on-device STT on our own mic,
              // see _toggleCaptions' own comment. Disabled (not hidden) on
              // a device with no speech-recognition engine, so it's clear
              // the button did register the tap rather than looking dead.
              _controlButton(
                icon: _captionsOn ? Icons.closed_caption_rounded : Icons.closed_caption_off_outlined,
                active: _captionsOn,
                tooltip: _captionsUnavailable ? 'Not available on this device' : 'Live captions',
                onTap: _captionsUnavailable ? null : _toggleCaptions,
              ),
              // FEATURE: in-app mini-view (PiP-style floating tile) -- see
              // _MiniViewTile's own comment for why this is in-app only,
              // not real OS-level Picture-in-Picture.
              _controlButton(
                icon: _miniViewOn ? Icons.picture_in_picture_rounded : Icons.picture_in_picture_outlined,
                active: _miniViewOn,
                tooltip: 'Mini view',
                onTap: () => setState(() => _miniViewOn = !_miniViewOn),
              ),
              // FEATURE: breakout rooms -- host manages from here; a
              // student with no breakout running doesn't get the button at
              // all (nothing for them to do until the host starts one, at
              // which point the banner above the video area already tells
              // them their room).
              if (_isHost)
                _controlButton(
                  icon: Icons.meeting_room_outlined,
                  active: _breakoutRooms.isNotEmpty,
                  badge: _breakoutRooms.isNotEmpty ? _breakoutRooms.length : null,
                  tooltip: 'Breakout rooms',
                  onTap: _breakoutBusy ? null : _openBreakoutSheet,
                ),
              // FEATURE: recording — host/co-teacher/moderator only. Whether
              // this is even allowed is a classroom-level setting the teacher/
              // organiser controls elsewhere (Classroom.recording_enabled); if
              // it's off, the tap still goes through and the server's error
              // ("Recording is turned off for this classroom.") shows as a
              // snackbar rather than this screen trying to guess/duplicate
              // that setting.
              if (_isHost)
                _controlButton(
                  icon: _isRecording ? Icons.stop_circle_rounded : Icons.fiber_manual_record_rounded,
                  active: _isRecording,
                  danger: _isRecording,
                  onTap: _recordingBusy ? null : (_isRecording ? _stopRecording : _startRecording),
                ),
              _controlButton(
                icon: Icons.chat_bubble_outline_rounded,
                active: _openPanel == _PanelTab.chat,
                badge: _chatMessages.isNotEmpty ? _chatMessages.length : null,
                onTap: () => setState(() => _openPanel = _openPanel == _PanelTab.chat ? null : _PanelTab.chat),
              ),
              _controlButton(
                icon: Icons.bar_chart_rounded,
                active: _openPanel == _PanelTab.polls,
                onTap: () => setState(() => _openPanel = _openPanel == _PanelTab.polls ? null : _PanelTab.polls),
              ),
              if (_isHost)
                _controlButton(
                  icon: Icons.groups_rounded,
                  active: _openPanel == _PanelTab.participants,
                  // FEATURE (hand-raise): a raised hand is more urgent than
                  // the plain headcount, so it takes over the badge whenever
                  // at least one hand is up — the host should notice a raised
                  // hand even without the panel open.
                  badge: (_participants.any((p) => p.handRaised) || _pendingUnmuteRequests.isNotEmpty)
                      ? _participants.where((p) => p.handRaised).length + _pendingUnmuteRequests.length
                      : (_participants.isNotEmpty ? _participants.length : null),
                  onTap: () {
                    setState(() => _openPanel = _openPanel == _PanelTab.participants ? null : _PanelTab.participants);
                    if (_openPanel == _PanelTab.participants) _loadParticipants();
                  },
                ),
              // FEATURE: "more" — materials/doubts/(host-only) waitlist all
              // live behind one button instead of three more circles
              // permanently crowding this bar. Badge is open-doubts count
              // plus (for the host) how many are stuck on the waitlist —
              // the two "something needs your attention" numbers.
              _controlButton(
                icon: Icons.more_horiz_rounded,
                active: _openPanel == _PanelTab.materials ||
                    _openPanel == _PanelTab.queries ||
                    _openPanel == _PanelTab.waitlist,
                badge: _moreBadgeCount > 0 ? _moreBadgeCount : null,
                onTap: _openMoreSheet,
              ),
              _controlButton(
                icon: Icons.call_end_rounded,
                active: false,
                danger: true,
                onTap: _actionBusy ? null : (_isHost ? _endSession : _leave),
              ),
            ],
          ),
        ),
      ),
    );
  }

  int get _moreBadgeCount {
    final openDoubts = _queries.where((q) => q.status == QueryStatus.open).length;
    return openDoubts + (_isHost ? _waitlist.length : 0);
  }

  /// Bottom sheet listing the panels that don't get a permanent spot in
  /// the control bar (see the "more" button above).
  Future<void> _openMoreSheet() async {
    final tab = await showModalBottomSheet<_PanelTab>(
      context: context,
      backgroundColor: const Color(0xFF15171C),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const SizedBox(height: 10),
            Container(width: 36, height: 4, decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2))),
            const SizedBox(height: 10),
            ListTile(
              leading: const Icon(Icons.folder_outlined, color: Colors.white70),
              title: const Text('Materials', style: TextStyle(color: Colors.white)),
              subtitle: Text('${_materials.length} shared', style: const TextStyle(color: Colors.white38, fontSize: 12)),
              onTap: () => Navigator.pop(context, _PanelTab.materials),
            ),
            ListTile(
              leading: const Icon(Icons.help_outline_rounded, color: Colors.white70),
              title: const Text('Doubts / Questions', style: TextStyle(color: Colors.white)),
              subtitle: Text(
                '${_queries.where((q) => q.status == QueryStatus.open).length} open',
                style: const TextStyle(color: Colors.white38, fontSize: 12),
              ),
              onTap: () => Navigator.pop(context, _PanelTab.queries),
            ),
            if (_isHost)
              ListTile(
                leading: const Icon(Icons.hourglass_top_rounded, color: Colors.white70),
                title: const Text('Waitlist', style: TextStyle(color: Colors.white)),
                subtitle: Text('${_waitlist.length} waiting', style: const TextStyle(color: Colors.white38, fontSize: 12)),
                onTap: () => Navigator.pop(context, _PanelTab.waitlist),
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
    if (tab == null || !mounted) return;
    setState(() => _openPanel = tab);
    switch (tab) {
      case _PanelTab.materials:
        _loadMaterials();
        break;
      case _PanelTab.queries:
        _loadQueries();
        break;
      case _PanelTab.waitlist:
        _loadWaitlist();
        break;
      default:
        break;
    }
  }

  Widget _controlButton({
    required IconData icon,
    required bool active,
    VoidCallback? onTap,
    bool danger = false,
    int? badge,
    String? tooltip,
  }) {
    final button = InkWell(
      // FEATURE (comfort): a light haptic tick on every control-bar tap —
      // small thing, but it's the difference between this row feeling like
      // a webpage and feeling like a real phone control. Skipped when
      // onTap itself is null (disabled buttons, e.g. mid-action-busy)
      // since there's no action to actually confirm.
      onTap: onTap == null
          ? null
          : () {
              HapticFeedback.lightImpact();
              onTap();
            },
      borderRadius: BorderRadius.circular(28),
      child: Container(
        width: 48,
        height: 48,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: danger ? Colors.red : (active ? Colors.white24 : Colors.white10),
        ),
        child: Icon(icon, color: Colors.white, size: 22),
      ),
    );
    return Stack(
      clipBehavior: Clip.none,
      children: [
        tooltip != null ? Tooltip(message: tooltip, child: button) : button,
        if (badge != null && badge > 0)
          Positioned(
            right: -2,
            top: -2,
            child: Container(
              padding: const EdgeInsets.all(3),
              decoration: const BoxDecoration(color: Color(0xFFEE0979), shape: BoxShape.circle),
              constraints: const BoxConstraints(minWidth: 16, minHeight: 16),
              child: Text('$badge',
                  textAlign: TextAlign.center, style: const TextStyle(color: Colors.white, fontSize: 9, fontWeight: FontWeight.bold)),
            ),
          ),
      ],
    );
  }

  // -------------------------------------------------------------------
  // Side panel (chat / polls / participants)
  // -------------------------------------------------------------------
  Widget _sidePanel() {
    return Positioned(
      right: 0,
      top: 0,
      bottom: 0,
      width: 320,
      child: Material(
        color: const Color(0xFF15171C),
        child: Column(
          children: [
            Container(
              padding: const EdgeInsets.fromLTRB(16, 14, 8, 14),
              decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: Colors.white12))),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      switch (_openPanel!) {
                        _PanelTab.chat => 'Chat',
                        _PanelTab.polls => 'Polls',
                        _PanelTab.participants => 'Participants',
                        _PanelTab.materials => 'Materials',
                        _PanelTab.queries => 'Doubts / Questions',
                        _PanelTab.waitlist => 'Waitlist',
                        _PanelTab.breakout => 'Breakout Rooms',
                      },
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15),
                    ),
                  ),
                  if (_openPanel == _PanelTab.polls && _isHost)
                    IconButton(
                      icon: const Icon(Icons.add_circle_outline_rounded, color: Colors.white70, size: 20),
                      onPressed: _createPoll,
                      tooltip: 'New Poll',
                    ),
                  // FEATURE: mute-all -- host bulk action, missing before
                  // (every mute was one participant at a time). Only shown
                  // when there's actually at least one unmuted student to
                  // act on.
                  if (_openPanel == _PanelTab.participants &&
                      _isHost &&
                      _participants.any((p) => p.role != ParticipantRole.host && !_isRemoteMicMuted(p.user.id.toString())))
                    IconButton(
                      icon: const Icon(Icons.mic_off_rounded, color: Colors.white70, size: 20),
                      tooltip: 'Mute everyone',
                      onPressed: _muteAllBusy ? null : _muteAllParticipants,
                    ),
                  IconButton(
                    icon: const Icon(Icons.close_rounded, color: Colors.white54, size: 20),
                    onPressed: () => setState(() => _openPanel = null),
                  ),
                ],
              ),
            ),
            Expanded(
              child: switch (_openPanel!) {
                _PanelTab.chat => _chatPanel(),
                _PanelTab.polls => _pollsPanel(),
                _PanelTab.participants => _participantsPanel(),
                _PanelTab.materials => _materialsPanel(),
                _PanelTab.queries => _queriesPanel(),
                _PanelTab.waitlist => _waitlistPanel(),
                _PanelTab.breakout => _breakoutPanel(),
              },
            ),
          ],
        ),
      ),
    );
  }

  Widget _chatPanel() {
    return Column(
      children: [
        Expanded(
          child: _chatLoading
              ? const Center(child: CircularProgressIndicator(color: Colors.white54))
              : _chatMessages.isEmpty
                  ? const Center(child: Text('No messages yet.', style: TextStyle(color: Colors.white38)))
                  : ListView.builder(
                      padding: const EdgeInsets.all(12),
                      itemCount: _chatMessages.length,
                      itemBuilder: (_, i) {
                        final m = _chatMessages[i];
                        return Padding(
                          padding: const EdgeInsets.only(bottom: 10),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(m.sender.fullName,
                                        style: const TextStyle(color: Colors.white54, fontSize: 11.5, fontWeight: FontWeight.w600)),
                                    const SizedBox(height: 2),
                                    Text(m.message, style: const TextStyle(color: Colors.white, fontSize: 13.5)),
                                  ],
                                ),
                              ),
                              if (_isHost)
                                InkWell(
                                  onTap: () => _deleteChat(m),
                                  child: const Padding(
                                    padding: EdgeInsets.only(left: 6, top: 2),
                                    child: Icon(Icons.delete_outline_rounded, size: 16, color: Colors.white24),
                                  ),
                                ),
                            ],
                          ),
                        );
                      },
                    ),
        ),
        Container(
          padding: const EdgeInsets.all(10),
          decoration: const BoxDecoration(border: Border(top: BorderSide(color: Colors.white12))),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _chatCtrl,
                  style: const TextStyle(color: Colors.white, fontSize: 13.5),
                  decoration: InputDecoration(
                    hintText: 'Write a message…',
                    hintStyle: const TextStyle(color: Colors.white30),
                    filled: true,
                    fillColor: Colors.white10,
                    contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                    border: OutlineInputBorder(borderRadius: BorderRadius.circular(20), borderSide: BorderSide.none),
                  ),
                  onSubmitted: (_) => _sendChat(),
                ),
              ),
              IconButton(
                icon: _sendingChat
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white54))
                    : const Icon(Icons.send_rounded, color: Colors.white),
                onPressed: _sendingChat ? null : _sendChat,
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _pollsPanel() {
    if (_pollsLoading) return const Center(child: CircularProgressIndicator(color: Colors.white54));
    if (_polls.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Text(
            _isHost ? 'No polls yet — tap + to create one.' : 'No poll is active right now.',
            textAlign: TextAlign.center,
            style: const TextStyle(color: Colors.white38),
          ),
        ),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: _polls.length,
      itemBuilder: (_, i) => _pollCard(_polls[i]),
    );
  }

  Widget _pollCard(LivePoll poll) {
    final totalVotes = poll.resultCounts.values.fold<int>(0, (a, b) => a + b);
    final voted = _myVotes.containsKey(poll.id);
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: Colors.white.withOpacity(0.05), borderRadius: BorderRadius.circular(12)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(poll.question, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600, fontSize: 13.5)),
              ),
              if (!poll.isActive)
                const Padding(
                  padding: EdgeInsets.only(left: 6),
                  child: Text('Closed', style: TextStyle(color: Colors.white38, fontSize: 10.5)),
                ),
            ],
          ),
          const SizedBox(height: 10),
          ...List.generate(poll.options.length, (idx) {
            final count = poll.resultCounts[idx] ?? 0;
            final pct = totalVotes == 0 ? 0.0 : count / totalVotes;
            final showResults = voted || !poll.isActive || _isHost;
            return Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: InkWell(
                onTap: (poll.isActive && !voted && !_isHost) ? () => _vote(poll, idx) : null,
                borderRadius: BorderRadius.circular(8),
                child: Stack(
                  children: [
                    if (showResults)
                      FractionallySizedBox(
                        widthFactor: pct.clamp(0, 1),
                        child: Container(
                          height: 32,
                          decoration: BoxDecoration(color: Colors.white12, borderRadius: BorderRadius.circular(8)),
                        ),
                      ),
                    Container(
                      width: double.infinity,
                      height: 32,
                      padding: const EdgeInsets.symmetric(horizontal: 10),
                      decoration: BoxDecoration(border: Border.all(color: Colors.white24), borderRadius: BorderRadius.circular(8)),
                      alignment: Alignment.centerLeft,
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(poll.options[idx],
                                style: const TextStyle(color: Colors.white, fontSize: 12.5), overflow: TextOverflow.ellipsis),
                          ),
                          if (showResults)
                            Text('$count', style: const TextStyle(color: Colors.white54, fontSize: 11.5)),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            );
          }),
          if (_isHost && poll.isActive)
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: () => _closePoll(poll),
                child: const Text('Close Poll', style: TextStyle(color: Colors.white54, fontSize: 12)),
              ),
            ),
        ],
      ),
    );
  }

  Widget _participantsPanel() {
    if (_participantsLoading) return const Center(child: CircularProgressIndicator(color: Colors.white54));
    if (_participants.isEmpty) {
      return const Center(child: Text('No participants.', style: TextStyle(color: Colors.white38)));
    }

    // FEATURE: hand-raise queue. Raised hands are surfaced first —
    // longest-raised first, using hand_raised_at as the natural queue
    // order (see the model's note in models.py) — so the host doesn't
    // have to scroll to find who's waiting. Built by concatenating two
    // already-ordered groups rather than one combined List.sort() call,
    // since Dart's sort isn't guaranteed stable and that could otherwise
    // shuffle same-tier participants around on every rebuild.
    final raised = _participants.where((p) => p.handRaised).toList()
      ..sort((a, b) => (a.handRaisedAt ?? a.joinedAt).compareTo(b.handRaisedAt ?? b.joinedAt));
    final others = _participants.where((p) => !p.handRaised).toList();
    final ordered = [...raised, ...others];

    return ListView.builder(
      padding: const EdgeInsets.all(8),
      itemCount: ordered.length,
      itemBuilder: (_, i) {
        final p = ordered[i];
        final isHostRow = p.role == ParticipantRole.host;
        return ListTile(
          leading: Stack(
            clipBehavior: Clip.none,
            children: [
              CircleAvatar(
                backgroundColor: Colors.white12,
                child: Text(p.user.fullName.isNotEmpty ? p.user.fullName.substring(0, 1).toUpperCase() : '?',
                    style: const TextStyle(color: Colors.white70)),
              ),
              if (p.handRaised)
                Positioned(
                  right: -2,
                  bottom: -2,
                  child: Container(
                    padding: const EdgeInsets.all(2),
                    decoration: const BoxDecoration(color: Color(0xFFFF6A00), shape: BoxShape.circle),
                    child: const Icon(Icons.back_hand_rounded, size: 10, color: Colors.white),
                  ),
                ),
            ],
          ),
          title: Text(p.user.fullName, style: const TextStyle(color: Colors.white, fontSize: 13.5)),
          subtitle: Text(
            isHostRow ? 'Host' : (p.handRaised ? 'Hand raised' : 'Student'),
            style: TextStyle(
              color: p.handRaised ? const Color(0xFFFF9A3D) : Colors.white38,
              fontSize: 11.5,
              fontWeight: p.handRaised ? FontWeight.w600 : FontWeight.normal,
            ),
          ),
          // FEATURE: mute-without-removing. "Remove" used to be the only
          // host control here — every "please mute yourself" moment forced
          // a full kick (drops their connection, blocks rejoining this
          // session) just to quiet one mic. Mute icon reflects live
          // LiveKit truth (see _isRemoteMicMuted) so it stays correct even
          // if the student mutes/unmutes themselves in between taps.
          trailing: (!isHostRow)
              ? Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (p.handRaised)
                      IconButton(
                        icon: const Icon(Icons.back_hand_rounded, color: Color(0xFFFF6A00), size: 18),
                        tooltip: 'Lower hand',
                        onPressed: () => _lowerHand(p),
                      ),
                    // FEATURE: ask-to-unmute queue — a green check appears
                    // only for students who've actually asked, so this
                    // never clutters the normal row.
                    if (_pendingUnmuteRequests.contains(p.user.id.toString()))
                      IconButton(
                        icon: const Icon(Icons.check_circle_outline_rounded, color: Colors.greenAccent, size: 18),
                        tooltip: 'Approve unmute request',
                        onPressed: () => _approveUnmute(p.user.id.toString()),
                      ),
                    // FEATURE: spotlight/pin — highlighted when this is the
                    // currently-pinned tile; tapping again un-pins.
                    IconButton(
                      icon: Icon(
                        Icons.push_pin_rounded,
                        color: _spotlightIdentity == p.user.id.toString() ? const Color(0xFFFF6A00) : Colors.white38,
                        size: 18,
                      ),
                      tooltip: _spotlightIdentity == p.user.id.toString() ? 'Un-spotlight' : 'Spotlight',
                      onPressed: () => _setSpotlight(p.user.id.toString()),
                    ),
                    IconButton(
                      icon: Icon(
                        _isRemoteMicMuted(p.user.id.toString()) ? Icons.mic_off_rounded : Icons.mic_rounded,
                        color: _isRemoteMicMuted(p.user.id.toString()) ? Colors.white38 : Colors.white70,
                        size: 18,
                      ),
                      tooltip: _isRemoteMicMuted(p.user.id.toString()) ? 'Unmute' : 'Mute',
                      onPressed: () => _toggleParticipantMute(p),
                    ),
                    IconButton(
                      icon: const Icon(Icons.person_remove_alt_1_rounded, color: Colors.redAccent, size: 18),
                      tooltip: 'Remove',
                      onPressed: () => _kick(p),
                    ),
                  ],
                )
              : null,
        );
      },
    );
  }

  // -------------------------------------------------------------------
  // Materials panel — read-only list; tap copies the file/link URL.
  // -------------------------------------------------------------------
  Widget _materialsPanel() {
    if (_materialsLoading) return const Center(child: CircularProgressIndicator(color: Colors.white54));
    if (_materials.isEmpty) {
      return const Center(child: Text('No material has been shared.', style: TextStyle(color: Colors.white38)));
    }
    return ListView.builder(
      padding: const EdgeInsets.all(8),
      itemCount: _materials.length,
      itemBuilder: (_, i) {
        final m = _materials[i];
        final icon = switch (m.materialType) {
          MaterialType.pdf => Icons.picture_as_pdf_rounded,
          MaterialType.ppt => Icons.slideshow_rounded,
          MaterialType.doc => Icons.description_rounded,
          MaterialType.image => Icons.image_rounded,
          MaterialType.video => Icons.play_circle_outline_rounded,
          MaterialType.link => Icons.link_rounded,
          _ => Icons.insert_drive_file_rounded,
        };
        return ListTile(
          leading: Icon(icon, color: Colors.white70),
          title: Text(m.title, style: const TextStyle(color: Colors.white, fontSize: 13.5)),
          subtitle: Text(
            '${m.uploadedBy.fullName} • ${_shortDate(m.uploadedAt)}',
            style: const TextStyle(color: Colors.white38, fontSize: 11.5),
          ),
          trailing: const Icon(Icons.copy_rounded, color: Colors.white38, size: 18),
          onTap: () => _copyMaterialLink(m),
        );
      },
    );
  }

  // -------------------------------------------------------------------
  // Doubts / Questions panel — ask (everyone) + answer (host).
  // -------------------------------------------------------------------
  Widget _queriesPanel() {
    return Column(
      children: [
        Expanded(
          child: _queriesLoading
              ? const Center(child: CircularProgressIndicator(color: Colors.white54))
              : _queries.isEmpty
                  ? const Center(child: Text('No doubts yet.', style: TextStyle(color: Colors.white38)))
                  : ListView.builder(
                      padding: const EdgeInsets.all(12),
                      itemCount: _queries.length,
                      itemBuilder: (_, i) {
                        final q = _queries[i];
                        final answered = q.status == QueryStatus.answered;
                        return Container(
                          margin: const EdgeInsets.only(bottom: 10),
                          padding: const EdgeInsets.all(10),
                          decoration: BoxDecoration(
                            color: Colors.white.withOpacity(0.04),
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(color: answered ? Colors.white12 : const Color(0xFFFF6A00).withOpacity(0.4)),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(q.askedBy.fullName,
                                  style: const TextStyle(color: Colors.white54, fontSize: 11.5, fontWeight: FontWeight.w600)),
                              const SizedBox(height: 3),
                              Text(q.question, style: const TextStyle(color: Colors.white, fontSize: 13.5)),
                              if (answered) ...[
                                const SizedBox(height: 8),
                                Container(
                                  padding: const EdgeInsets.all(8),
                                  decoration: BoxDecoration(color: Colors.white10, borderRadius: BorderRadius.circular(8)),
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        q.answeredBy?.fullName ?? 'Host',
                                        style: const TextStyle(color: Color(0xFF6FE38B), fontSize: 11, fontWeight: FontWeight.w600),
                                      ),
                                      const SizedBox(height: 2),
                                      Text(q.answer, style: const TextStyle(color: Colors.white70, fontSize: 13)),
                                    ],
                                  ),
                                ),
                              ] else if (_isHost) ...[
                                const SizedBox(height: 8),
                                Align(
                                  alignment: Alignment.centerRight,
                                  child: TextButton(
                                    onPressed: () => _answerQuery(q),
                                    style: TextButton.styleFrom(
                                      minimumSize: Size.zero,
                                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                                    ),
                                    child: const Text('Answer', style: TextStyle(fontSize: 12)),
                                  ),
                                ),
                              ],
                            ],
                          ),
                        );
                      },
                    ),
        ),
        Container(
          padding: const EdgeInsets.all(10),
          decoration: const BoxDecoration(border: Border(top: BorderSide(color: Colors.white12))),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _queryCtrl,
                  style: const TextStyle(color: Colors.white, fontSize: 13.5),
                  decoration: InputDecoration(
                    hintText: 'Write your doubt…',
                    hintStyle: const TextStyle(color: Colors.white30),
                    filled: true,
                    fillColor: Colors.white10,
                    contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                    border: OutlineInputBorder(borderRadius: BorderRadius.circular(20), borderSide: BorderSide.none),
                  ),
                  onSubmitted: (_) => _askQuery(),
                ),
              ),
              IconButton(
                icon: _askingQuery
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white54))
                    : const Icon(Icons.send_rounded, color: Colors.white),
                onPressed: _askingQuery ? null : _askQuery,
              ),
            ],
          ),
        ),
      ],
    );
  }

  // -------------------------------------------------------------------
  // Waitlist panel (host only).
  // -------------------------------------------------------------------
  Widget _waitlistPanel() {
    if (_waitlistLoading) return const Center(child: CircularProgressIndicator(color: Colors.white54));
    if (_waitlist.isEmpty) {
      return const Center(child: Text('Waitlist is empty.', style: TextStyle(color: Colors.white38)));
    }
    return ListView.builder(
      padding: const EdgeInsets.all(8),
      itemCount: _waitlist.length,
      itemBuilder: (_, i) {
        final entry = _waitlist[i];
        return ListTile(
          leading: CircleAvatar(
            backgroundColor: Colors.white12,
            child: Text(
              entry.student.fullName.isNotEmpty ? entry.student.fullName.substring(0, 1).toUpperCase() : '?',
              style: const TextStyle(color: Colors.white70),
            ),
          ),
          title: Text(entry.student.fullName, style: const TextStyle(color: Colors.white, fontSize: 13.5)),
          subtitle: Text('Waiting since ${_shortDate(entry.joinedAt)}', style: const TextStyle(color: Colors.white38, fontSize: 11.5)),
          trailing: TextButton(
            onPressed: () => _promoteFromWaitlist(entry),
            style: TextButton.styleFrom(
              minimumSize: Size.zero,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              backgroundColor: Colors.white10,
            ),
            child: const Text('Promote', style: TextStyle(fontSize: 12, color: Colors.white)),
          ),
        );
      },
    );
  }

  // FIX (i18n / timezone audit — see utils/liveclass_datetime.dart): was a
  // hardcoded English month array with no `.toLocal()` call before
  // reading `.day`/`.month` off `entry.joinedAt` (UTC-parsed from the
  // API) — the waitlist's "Waiting since ..." label rendered in English
  // and in UTC regardless of the viewer's own locale/timezone.
  String _shortDate(DateTime dt) {
    final locale = Localizations.maybeLocaleOf(context)?.toString();
    return DateFormat.MMMd(locale).format(dt.toLocal());
  }

  // FEATURE: side-panel view for breakout rooms — added so `_PanelTab.breakout`
  // (already referenced by the poll timer in initState, see that block's own
  // FEATURE comment) is actually reachable/renderable instead of only
  // existing as an enum value. Host gets create/assign/close controls
  // (mirrors the dedicated management bottom sheet elsewhere in this file);
  // everyone else just sees the current room roster read-only.
  Widget _breakoutPanel() {
    if (_breakoutLoading) return const Center(child: CircularProgressIndicator(color: Colors.white54));
    if (_breakoutRooms.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Text(
            _isHost ? 'No breakout room is active right now.' : 'The host hasn\'t started any breakout rooms yet.',
            textAlign: TextAlign.center,
            style: const TextStyle(color: Colors.white38, fontSize: 13),
          ),
        ),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.all(8),
      itemCount: _breakoutRooms.length,
      itemBuilder: (_, i) {
        final room = _breakoutRooms[i];
        final names = _participants
            .where((p) => room.participantIdentities.contains(p.user.id.toString()))
            .map((p) => p.user.fullName)
            .join(', ');
        return ListTile(
          dense: true,
          leading: const Icon(Icons.meeting_room_rounded, color: Colors.white70, size: 20),
          title: Text('Room ${room.roomNumber}', style: const TextStyle(color: Colors.white, fontSize: 13.5)),
          subtitle: Text(
            names.isEmpty ? 'None assigned' : names,
            style: const TextStyle(color: Colors.white38, fontSize: 11.5),
          ),
          trailing: _isHost
              ? IconButton(
                  icon: const Icon(Icons.close_rounded, color: Colors.white38, size: 18),
                  tooltip: 'Close all breakout rooms',
                  onPressed: _breakoutBusy ? null : _closeBreakoutRooms,
                )
              : null,
        );
      },
    );
  }
}

enum _PanelTab { chat, polls, participants, materials, queries, waitlist, breakout }

// ===========================================================================
// Whiteboard stroke model + painter
//
// Points are stored NORMALIZED (0..1 on both axes, relative to the drawing
// canvas) rather than raw pixels — different devices/orientations have
// different canvas sizes, so raw pixel coordinates from a phone would land
// in the wrong place on a tablet. _wbCanvasSize (captured from the
// LayoutBuilder in _whiteboardOverlay) is what converts local taps to/from
// this normalized space.
// ===========================================================================
class _WhiteboardStroke {
  final String id;
  final String authorIdentity;
  final int colorValue;
  final double width;
  final List<Offset> points; // normalized 0..1

  _WhiteboardStroke({required this.id, required this.authorIdentity, required this.colorValue, required this.width, required this.points});

  Map<String, dynamic> toJson() => {
        'sid': id,
        'aid': authorIdentity,
        'c': colorValue,
        'w': width,
        'pts': points.map((p) => [p.dx, p.dy]).toList(),
      };

  factory _WhiteboardStroke.fromJson(Map<String, dynamic> j) => _WhiteboardStroke(
        id: j['sid'] as String,
        authorIdentity: (j['aid'] as String?) ?? (j['sid'] as String).split('_').first,
        colorValue: (j['c'] as num?)?.toInt() ?? Colors.black.value,
        width: (j['w'] as num?)?.toDouble() ?? 4.0,
        points: ((j['pts'] as List?) ?? []).map((p) => Offset((p[0] as num).toDouble(), (p[1] as num).toDouble())).toList(),
      );
}

class _WhiteboardPainter extends CustomPainter {
  final List<_WhiteboardStroke> strokes;
  _WhiteboardPainter({required this.strokes});

  @override
  void paint(Canvas canvas, Size size) {
    for (final stroke in strokes) {
      if (stroke.points.isEmpty) continue;
      final paint = Paint()
        ..color = Color(stroke.colorValue)
        ..strokeWidth = stroke.width
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round
        ..style = PaintingStyle.stroke;
      if (stroke.points.length == 1) {
        // A tap with no drag — draw a dot so it's not invisible.
        final p = Offset(stroke.points.first.dx * size.width, stroke.points.first.dy * size.height);
        canvas.drawCircle(p, stroke.width / 2, paint..style = PaintingStyle.fill);
        continue;
      }
      final path = Path();
      final first = stroke.points.first;
      path.moveTo(first.dx * size.width, first.dy * size.height);
      for (final p in stroke.points.skip(1)) {
        path.lineTo(p.dx * size.width, p.dy * size.height);
      }
      canvas.drawPath(path, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _WhiteboardPainter oldDelegate) => true; // strokes mutate in place while drawing — always repaint
}

// ===========================================================================
// Session elapsed timer -- owns its own 1-second Timer and setState so the
// once-a-second tick never rebuilds the full LiveSessionScreen (video tiles
// included). See _lkFirstConnectedAt / _roomHeader for how it's used.
// ===========================================================================
class _ElapsedTimerText extends StatefulWidget {
  final DateTime since;
  const _ElapsedTimerText({required this.since});

  @override
  State<_ElapsedTimerText> createState() => _ElapsedTimerTextState();
}

class _ElapsedTimerTextState extends State<_ElapsedTimerText> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final d = DateTime.now().difference(widget.since);
    final h = d.inHours;
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    final text = h > 0 ? '$h:$m:$s' : '$m:$s';
    return Text(text, style: const TextStyle(color: Colors.white54, fontSize: 11.5));
  }
}

// ===========================================================================
// Emoji reaction floating bubble (see _activeReactions / _sendReaction in
// _LiveSessionScreenState for how these get created and broadcast).
// ===========================================================================
class _FloatingReaction {
  final int id;
  final String emoji;
  final double dx; // 0..1 horizontal jitter within the overlay column
  _FloatingReaction({required this.id, required this.emoji, required this.dx});
}

class _FloatingReactionWidget extends StatelessWidget {
  final _FloatingReaction reaction;
  const _FloatingReactionWidget({super.key, required this.reaction});

  @override
  Widget build(BuildContext context) {
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: 1),
      duration: const Duration(milliseconds: 2600),
      curve: Curves.easeOut,
      builder: (context, t, child) {
        return Positioned(
          right: reaction.dx * 40,
          bottom: t * 190,
          child: Opacity(opacity: (1 - t).clamp(0.0, 1.0), child: child),
        );
      },
      child: Text(reaction.emoji, style: const TextStyle(fontSize: 30)),
    );
  }
}

// ===========================================================================
// Live captions — one finalized line (see _captionFeed / _addCaptionLine).
// ===========================================================================
class _CaptionLine {
  final String speaker;
  final String text;
  final DateTime at;
  _CaptionLine({required this.speaker, required this.text, required this.at});
}

/// Bottom-of-screen caption strip — shows up to the last few finalized
/// lines plus our own in-progress (not-yet-final) words underneath, so the
/// user sees their own speech tracking live the way real captioning does.
class _CaptionOverlay extends StatelessWidget {
  final List<_CaptionLine> lines;
  final String partial;
  const _CaptionOverlay({required this.lines, required this.partial});

  @override
  Widget build(BuildContext context) {
    if (lines.isEmpty && partial.isEmpty) return const SizedBox.shrink();
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(color: Colors.black.withOpacity(0.62), borderRadius: BorderRadius.circular(10)),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final line in lines)
            Padding(
              padding: const EdgeInsets.only(bottom: 2),
              child: RichText(
                text: TextSpan(
                  style: const TextStyle(fontSize: 13.5, color: Colors.white, height: 1.35),
                  children: [
                    TextSpan(text: '${line.speaker}: ', style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.white70)),
                    TextSpan(text: line.text),
                  ],
                ),
              ),
            ),
          if (partial.isNotEmpty)
            Text(partial, style: const TextStyle(fontSize: 13.5, color: Colors.white54, fontStyle: FontStyle.italic)),
        ],
      ),
    );
  }
}

// ===========================================================================
// In-app mini-view (PiP-style floating tile) — draggable, always on top of
// whichever panel is open (see _miniViewOn in _LiveSessionScreenState).
// ===========================================================================
class _MiniViewTile extends StatelessWidget {
  final Offset offset;
  final ValueChanged<Offset> onDrag;
  final VoidCallback onTapExpand;
  final Widget videoChild; // whatever tile _LiveSessionScreenState is already rendering full-size

  const _MiniViewTile({
    required this.offset,
    required this.onDrag,
    required this.onTapExpand,
    required this.videoChild,
  });

  @override
  Widget build(BuildContext context) {
    const size = Size(120, 160);
    return Positioned(
      left: offset.dx,
      top: offset.dy,
      child: GestureDetector(
        onPanUpdate: (d) => onDrag(offset + d.delta),
        onTap: onTapExpand,
        child: Material(
          elevation: 8,
          borderRadius: BorderRadius.circular(12),
          clipBehavior: Clip.antiAlias,
          child: SizedBox(
            width: size.width,
            height: size.height,
            child: Stack(
              fit: StackFit.expand,
              children: [
                videoChild,
                Positioned(
                  right: 4,
                  top: 4,
                  child: Icon(Icons.open_in_full_rounded, size: 16, color: Colors.white.withOpacity(0.85)),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ===========================================================================
// Breakout rooms — `BreakoutRoom` (roster per room) now lives in
// liveclass_models.dart as a public class, shared with liveclass_api_service.dart
// — see that file for the model + the file header above for the backend
// endpoints it maps onto.
// ===========================================================================
// Create Poll bottom sheet (host only)
// ===========================================================================
class _CreatePollSheet extends StatefulWidget {
  final int sessionId;
  const _CreatePollSheet({required this.sessionId});

  @override
  State<_CreatePollSheet> createState() => _CreatePollSheetState();
}

class _CreatePollSheetState extends State<_CreatePollSheet> {
  final TextEditingController _questionCtrl = TextEditingController();
  final List<TextEditingController> _optionCtrls = [TextEditingController(), TextEditingController()];
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _questionCtrl.dispose();
    for (final c in _optionCtrls) {
      c.dispose();
    }
    super.dispose();
  }

  void _addOption() {
    if (_optionCtrls.length >= 6) return;
    setState(() => _optionCtrls.add(TextEditingController()));
  }

  Future<void> _submit() async {
    final question = _questionCtrl.text.trim();
    final options = _optionCtrls.map((c) => c.text.trim()).where((s) => s.isNotEmpty).toList();
    if (question.isEmpty) {
      setState(() => _error = 'Write a question.');
      return;
    }
    if (options.length < 2) {
      setState(() => _error = 'At least 2 options are required.');
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await LiveClassApi.polls.create(sessionId: widget.sessionId, question: question, options: options);
      if (!mounted) return;
      Navigator.pop(context, true);
    } on LiveClassApiException catch (e) {
      setState(() {
        _submitting = false;
        _error = e.message;
      });
    } catch (_) {
      setState(() {
        _submitting = false;
        _error = 'Could not create the poll.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('New Poll', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
            const SizedBox(height: 14),
            TextField(
              controller: _questionCtrl,
              decoration: const InputDecoration(hintText: 'Poll question', border: OutlineInputBorder()),
            ),
            const SizedBox(height: 12),
            ...List.generate(
              _optionCtrls.length,
              (i) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: TextField(
                  controller: _optionCtrls[i],
                  decoration: InputDecoration(hintText: 'Option ${i + 1}', border: const OutlineInputBorder()),
                ),
              ),
            ),
            if (_optionCtrls.length < 6)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: _addOption,
                  icon: const Icon(Icons.add_rounded, size: 18),
                  label: const Text('Add Option'),
                ),
              ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 4, bottom: 4),
                child: Text(_error!, style: const TextStyle(color: Colors.red, fontSize: 12.5)),
              ),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white, padding: const EdgeInsets.symmetric(vertical: 13)),
                onPressed: _submitting ? null : _submit,
                child: _submitting
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : const Text('Launch Poll'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ===========================================================================
// Shared full-screen state widget (joining / pass-required / waitlisted / etc.)
// ===========================================================================
class _CenteredState extends StatelessWidget {
  final Color background;
  final IconData? icon;
  final bool spinner;
  final String message;
  final Color textColor;
  final String? actionLabel;
  final VoidCallback? onAction;

  const _CenteredState({
    required this.background,
    required this.icon,
    this.spinner = false,
    required this.message,
    required this.textColor,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: background,
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (spinner)
                const CircularProgressIndicator(color: Colors.white70)
              else if (icon != null)
                Icon(icon, size: 48, color: Colors.white38),
              const SizedBox(height: 16),
              Text(message, textAlign: TextAlign.center, style: TextStyle(color: textColor, fontSize: 14.5)),
              if (actionLabel != null && onAction != null) ...[
                const SizedBox(height: 22),
                SizedBox(
                  width: double.infinity,
                  child: DecoratedBox(
                    decoration: BoxDecoration(gradient: _kGradient, borderRadius: BorderRadius.circular(12)),
                    child: Material(
                      color: Colors.transparent,
                      child: InkWell(
                        borderRadius: BorderRadius.circular(12),
                        onTap: onAction,
                        child: Padding(
                          padding: const EdgeInsets.symmetric(vertical: 13),
                          child: Center(
                            child: Text(actionLabel!,
                                style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 14.5)),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}