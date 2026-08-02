import 'dart:async';
import 'dart:convert'; // 🔥 NAYA — hold signal peer ko bhejne/samajhne ke liye
import 'dart:developer' as developer;
import 'package:flutter/foundation.dart';
import 'package:livekit_client/livekit_client.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
import 'package:audioplayers/audioplayers.dart';
import 'package:flutter_background/flutter_background.dart';
import 'package:permission_handler/permission_handler.dart';
import 'call_api_service.dart';

/// 🔥 NAYA — local video preview par lagne wale color filters (sirf
/// isi device pe render, peer ko normal video jaata hai — dekho
/// CallManager.localFilter/localSoftBlur ke comments).
enum VideoFilterType { none, blackAndWhite, warm, cool, vintage, sepia }

/// Global singleton that owns the lifecycle of an in-progress call
/// (audio or video), independent of whatever screen is currently on
/// top (StudyRoomScreen, ChatScreen, etc). This lets the whiteboard
/// stay visible with a small status bar instead of navigating away.
///
/// 🔥 NOTE: This no longer starts a `flutter_background` foreground
/// service — that was showing users a "disable battery optimization"
/// system popup on every call, which was removed on request (see the
/// comment above `_enableBackgroundExecution()` call-site in
/// `_initCall`). Calls now behave like a normal foreground activity:
/// no extra permission popup, but no extra background-survival
/// guarantee either. `_enableBackgroundExecution()` /
/// `_disableBackgroundExecution()` are now used ONLY around screen
/// share (see `toggleScreenShare()`) — Android requires an active
/// foreground service while MediaProjection (screen capture) is
/// running, so that's the one case this still needs to turn on.
/// AndroidManifest.xml's `IsolateHolderService` MUST declare
/// `mediaProjection` in its `foregroundServiceType`, and
/// `FOREGROUND_SERVICE_MEDIA_PROJECTION` must be in the permissions
/// list — without both, screen share will still crash even with this
/// fix.
class CallManager extends ChangeNotifier {
  CallManager._();
  static final CallManager instance = CallManager._();

  // ============================================================
  // CALL IDENTITY / METADATA
  // ============================================================
  String? callId;
  String? conversationId;
  bool isVideo = false;
  bool isCaller = false;
  String? peerName;
  String? peerAvatar;

  String _livekitUrl = '';
  String _livekitToken = '';

  // ============================================================
  // UI / LIFECYCLE STATE
  // ============================================================
  bool isActive = false;
  bool isMinimized = false;

  // ============================================================
  // LIVEKIT STATE
  // ============================================================
  Room? room;
  EventsListener<RoomEvent>? _listener;

  VideoTrack? remoteVideoTrack;
  LocalVideoTrack? localVideoTrack;
  VideoTrack? remoteScreenTrack;
  LocalVideoTrack? localScreenTrack;

  // ============================================================
  // 🔥 NAYA — GROUP CALL: multiple remote participants (WhatsApp jaisa).
  // Purane singular fields (remoteVideoTrack/remoteConnected/peerName)
  // ABHI BHI maintained hain — 1-on-1 call ka existing UI unhi par
  // depend karta hai aur bina tootey chalta rahega (wo hamesha "first"
  // remote participant ko reflect karte hain). Jab 2+ log call me hon,
  // UI is `remoteTiles` map ko use karke grid banaye.
  // Key = LiveKit participant.identity (backend-assigned unique user id).
  // ============================================================
  final Map<String, RemoteParticipant> remoteTiles = <String, RemoteParticipant>{};
  final Map<String, VideoTrack> remoteTileVideoTracks = <String, VideoTrack>{};

  bool get isGroupCall => remoteTiles.length > 1;
  List<RemoteParticipant> get remoteParticipantsList => remoteTiles.values.toList(growable: false);

  bool muted = false;
  bool videoOff = false;
  bool speakerOn = true;

  // 🔥 NAYA — Hold: apna mic+camera band, dusri taraf ka audio bhi mute,
  // aur peer ko data-channel se batao taaki unki screen pe "On hold"
  // dikhe. `peerOnHold` — jab DUSRI taraf ne mujhe hold pe daala ho.
  bool onHold = false;
  bool peerOnHold = false;
  bool _micWasMutedBeforeHold = false;
  bool _cameraWasOffBeforeHold = false;

  // 🔥 NAYA — front/back camera switch ke liye current position track karo.
  bool isFrontCamera = true;

  // ============================================================
  // 🔥 NAYA — LOCAL VIDEO EFFECTS (color filters + soft blur).
  // SCOPE NOTE: LiveKit ka Flutter SDK abhi background segmentation /
  // virtual-background natively support NAHI karta (sirf LiveKit ka
  // JS/web SDK me hai — dekho: livekit/client-sdk-flutter GitHub issue
  // #479, jo abhi bhi open hai). Isliye:
  //  - Filters aur blur DONO isi device pe render hone wale pixels par
  //    apply hote hain (ImageFiltered/ColorFiltered) — jo peer ko
  //    bheja jaata hai wo track hamesha unprocessed/normal rehta hai.
  //  - "Soft blur" PURA frame blur karta hai (person + background dono)
  //    — sirf background blur/replace ke liye person ko ML se background
  //    se alag karna padta, jo yahan available nahi.
  // ============================================================
  VideoFilterType localFilter = VideoFilterType.none;
  bool localSoftBlur = false;

  void setLocalFilter(VideoFilterType filter) {
    localFilter = filter;
    notifyListeners();
  }

  void toggleLocalSoftBlur() {
    localSoftBlur = !localSoftBlur;
    notifyListeners();
  }

  // ============================================================
  // 🔥 NAYA — CALL WAITING. Jab ek call already chal rahi ho aur usi
  // dauraan koi DOOSRA banda call kare, to iske liye poori (already
  // active call ko ROKNE wali) IncomingCallScreen nahi khulti — bas ek
  // chhota banner CallScreen ke upar dikhta hai. NOTE: LiveKit ke 2
  // Room simultaneously manage karna bahut bada architecture change
  // hota, isliye "Accept" karne par purani call END karke nayi call
  // start hoti hai (WhatsApp jaisa asli hold-and-switch nahi, ek
  // simple aur reliable swap hai) — call_screen.dart me banner pe
  // isi hisaab se label rakha hai.
  // ============================================================
  String? waitingCallId;
  String? waitingCallerName;
  String? waitingCallType; // "audio" | "video"
  String? waitingConversationId;
  String? waitingCallerAvatar;

  void setWaitingCall({
    required String callId,
    required String callerName,
    required String callType,
    required String conversationId,
    String? callerAvatar,
  }) {
    if (!isActive) return; // koi call chal hi nahi rahi to "waiting" ka matlab nahi
    if (waitingCallId == callId) return; // already track ho raha hai
    waitingCallId = callId;
    waitingCallerName = callerName;
    waitingCallType = callType;
    waitingConversationId = conversationId;
    waitingCallerAvatar = callerAvatar;
    notifyListeners();
  }

  void clearWaitingCall() {
    if (waitingCallId == null) return;
    waitingCallId = null;
    waitingCallerName = null;
    waitingCallType = null;
    waitingConversationId = null;
    waitingCallerAvatar = null;
    notifyListeners();
  }

  Future<void> declineWaitingCall() async {
    final id = waitingCallId;
    if (id == null) return;
    clearWaitingCall();
    try {
      await CallApiService.callAction(id, 'reject');
    } catch (e) {
      developer.log("declineWaitingCall failed: $e");
    }
  }
  bool remoteConnected = false;
  bool isReconnecting = false;
  bool isScreenSharing = false;

  Duration connectedDuration = Duration.zero;
  int reconnectSecondsLeft = 2;

  String status = "Connecting...";
  String? error;
  bool needsSettingsRedirect = false;

  Timer? _callTimer;
  Timer? _reconnectTimer;
  DateTime? _connectedAt;

  // 🔥 NAYA — agar caller ki call 30 second tak koi answer na kare (ring
  // bajti rahe, remoteConnected kabhi true na ho), to call apne aap cut
  // ho jaati hai — jaise WhatsApp/Instagram me hota hai.
  Timer? _noAnswerTimer;

  bool _ringtonePlaying = false;
  String ringtoneDebugStatus = "Ringtone: not started";

  bool _bgExecEnabled = false;
  AudioPlayer? _ringPlayer;

  // 🔥 FIX — caller's mic is enabled only once the callee actually joins
  // (see _initCall / ParticipantConnectedEvent below). Enabling it
  // immediately made LiveKit's WebRTC engine switch Android's audio
  // mode to MODE_IN_COMMUNICATION while still ringing, which silently
  // ducked/muted the outgoing ringtone (a separate audio stream).
  bool _micPendingForCaller = false;

  // ============================================================
  // PUBLIC ENTRY POINT
  // ============================================================
  Future<void> startCallIfNeeded({
    required String callId,
    required String conversationId,
    required bool isVideo,
    required bool isCaller,
    required String livekitUrl,
    required String livekitToken,
    String? peerName,
    String? peerAvatar,
  }) async {
    // Already on this exact call — just bring the UI back, don't restart.
    if (isActive && this.callId == callId) {
      isMinimized = false;
      notifyListeners();
      return;
    }

    // Switching calls mid-flight — clean up the old one first.
    if (isActive) await _cleanup();

    this.callId = callId;
    this.conversationId = conversationId;
    this.isVideo = isVideo;
    this.isCaller = isCaller;
    _livekitUrl = livekitUrl;
    _livekitToken = livekitToken;
    this.peerName = peerName;
    this.peerAvatar = peerAvatar;

    videoOff = !isVideo;
    muted = false;
    speakerOn = true;
    remoteConnected = false;
    isReconnecting = false;
    isScreenSharing = false;
    status = "Connecting...";
    error = null;
    needsSettingsRedirect = false;
    isActive = true;
    isMinimized = false;
    notifyListeners();

    await _initCall();
  }

  void minimize() {
    isMinimized = true;
    notifyListeners();
  }

  void unminimize() {
    if (isActive) {
      isMinimized = false;
      notifyListeners();
    }
  }

  // ============================================================
  // RINGTONE
  // ============================================================
  Future<void> _playRingtone() async {
    if (_ringtonePlaying) await _stopRingtone();
    _ringtonePlaying = true;

    try {
      await _ringPlayer?.stop();
      await _ringPlayer?.dispose();
      _ringPlayer = null;

      final player = AudioPlayer();
      _ringPlayer = player;

      await player.setAudioContext(
        AudioContext(
          android: AudioContextAndroid(
            isSpeakerphoneOn: true,
            stayAwake: true,
            contentType: AndroidContentType.sonification,
            usageType: AndroidUsageType.alarm,
            audioFocus: AndroidAudioFocus.gainTransient,
          ),
          iOS: AudioContextIOS(
            // `defaultToSpeaker` is only valid alongside `playAndRecord` —
            // pairing it with `playback` throws at construction time on
            // BOTH platforms (this object is shared), which silently
            // failed the ringtone before this fix.
            category: AVAudioSessionCategory.playAndRecord,
            options: {
              AVAudioSessionOptions.defaultToSpeaker,
              AVAudioSessionOptions.mixWithOthers,
            },
          ),
        ),
      );

      await player.setReleaseMode(ReleaseMode.loop);
      await player.setVolume(1.0);

      final asset = isCaller ? 'sounds/outgoing_ring.mp3' : 'sounds/incoming_ring.mp3';
      ringtoneDebugStatus = "Ringtone: trying $asset";
      notifyListeners();

      await player.play(AssetSource(asset));
      developer.log("Ringtone started OK: $asset");
      ringtoneDebugStatus = "Ringtone: playing ($asset)";
      notifyListeners();
    } catch (e, s) {
      _ringtonePlaying = false;
      developer.log("Ringtone play error: $e", stackTrace: s);
      ringtoneDebugStatus = "Ringtone FAILED: $e";
      notifyListeners();
      // Non-fatal — a failed ringtone should never take the call down.
    }
  }

  Future<void> _stopRingtone() async {
    if (!_ringtonePlaying && _ringPlayer == null) return;
    _ringtonePlaying = false;
    try {
      await _ringPlayer?.stop();
      await _ringPlayer?.dispose();
    } catch (_) {
      // Best-effort cleanup — a stop/dispose failure isn't actionable.
    }
    _ringPlayer = null;
    ringtoneDebugStatus = "Ringtone: stopped";
    notifyListeners();
  }

  // ============================================================
  // BACKGROUND EXECUTION (foreground service)
  // ------------------------------------------------------------
  // This is best-effort: if it fails, the call still works while the
  // app is in the foreground — it just won't survive being
  // backgrounded as gracefully. It must NEVER be allowed to crash the
  // process, so every failure path here is caught and logged, never
  // rethrown.
  // ============================================================
  Future<void> _enableBackgroundExecution() async {
    if (_bgExecEnabled) return;
    try {
      final ok = await FlutterBackground.initialize(
        androidConfig: FlutterBackgroundAndroidConfig(
          notificationTitle: peerName ?? "Ongoing call",
          notificationText: "Tap to return to the call",
          notificationImportance: AndroidNotificationImportance.high,
          // Explicit icon resource — omitting this crashes the
          // notification build on some OEM devices.
          notificationIcon: const AndroidResource(
            name: 'ic_launcher',
            defType: 'mipmap',
          ),
        ),
      );
      if (ok) {
        await FlutterBackground.enableBackgroundExecution();
        _bgExecEnabled = true;
      } else {
        developer.log("FlutterBackground.initialize returned false — continuing without it");
      }
    } catch (e, s) {
      // Must match AndroidManifest.xml's declared foregroundServiceType
      // (phoneCall|microphone|camera). A mismatch here throws a
      // SecurityException at the OS level that this catch cannot
      // fully suppress on some Android versions — if you still see
      // crashes after this fix, double check the manifest first.
      developer.log("Background execution failed (non-fatal, call continues): $e", stackTrace: s);
    }
  }

  Future<void> _disableBackgroundExecution() async {
    if (!_bgExecEnabled) return;
    try {
      if (FlutterBackground.isBackgroundExecutionEnabled) {
        await FlutterBackground.disableBackgroundExecution();
      }
    } catch (e) {
      developer.log("Background execution teardown failed (non-fatal): $e");
    }
    _bgExecEnabled = false;
  }

  // ============================================================
  // PERMISSIONS
  // ============================================================
  Future<bool> _requestPermissions() async {
    final micStatus = await Permission.microphone.request();
    if (!micStatus.isGranted) {
      needsSettingsRedirect = micStatus.isPermanentlyDenied;
      error = needsSettingsRedirect
          ? "Mic permission denied. Settings me jaake allow karo."
          : "Mic permission denied";
      notifyListeners();
      return false;
    }

    if (isVideo) {
      final camStatus = await Permission.camera.request();
      if (!camStatus.isGranted) {
        needsSettingsRedirect = camStatus.isPermanentlyDenied;
        error = needsSettingsRedirect
            ? "Camera permission denied. Settings me jaake allow karo."
            : "Camera permission denied";
        notifyListeners();
        return false;
      }
    }

    return true;
  }

  // ============================================================
  // CALL INITIALIZATION
  // ============================================================
  Future<void> _initCall() async {
    try {
      if (!await _requestPermissions()) return;

      // 🔥 FIX — study room call se ab "battery optimization band karo"
      // wala system popup NAHI aayega. Ye popup `FlutterBackground.initialize()`
      // se aata tha (foreground-service + battery-optimization-exemption
      // request). User ne explicitly ye band karne ko bola hai, isliye
      // `_enableBackgroundExecution()` yahan se hata diya.
      //
      // ⚠️ TRADEOFF samajh lo: iske bina agar app background me chali
      // jaaye (home button dabao ya screen off ho) to kuch Android phones
      // (khaaskar Xiaomi/Oppo/Vivo jaise aggressive battery-saver wale)
      // OS ki taraf se mic/call ko kuch second/minute baad suspend kar
      // sakte hain. App foreground me rahe to koi farak nahi padta.
      // Agar future me firse chahiye ho to `_enableBackgroundExecution()`
      // method neeche waisa hi maujood hai — bas yahan call karna hoga.

      WakelockPlus.enable();
      status = "Connecting...";
      notifyListeners();

      await Hardware.instance.setSpeakerphoneOn(true);

      if (isCaller) {
        await _playRingtone();
        _startNoAnswerTimer();
      } else {
        ringtoneDebugStatus = "Ringtone: skipped (callee — CallKit native popup rings)";
        notifyListeners();
      }

      room = Room(roomOptions: const RoomOptions(adaptiveStream: true, dynacast: true));
      _listener = room!.createListener();

      _listener!
        ..on<TrackSubscribedEvent>(_onTrackSubscribed)
        ..on<TrackUnsubscribedEvent>(_onTrackUnsubscribed)
        ..on<ParticipantConnectedEvent>((event) async {
          status = "Joined";
          // 🔥 NAYA — group call tile map me naya banda add karo.
          remoteTiles[event.participant.identity] = event.participant;
          // Call answered — no more risk of a silent timeout.
          _noAnswerTimer?.cancel();
          _noAnswerTimer = null;
          // Callee has now actually joined the room — this is the
          // "answered" moment for the caller. Only now switch on the
          // mic, which is what hands Android's audio mode over to
          // LiveKit's WebRTC engine. Doing this earlier (while still
          // ringing) was what silenced the outgoing ringtone.
          if (_micPendingForCaller) {
            _micPendingForCaller = false;
            try {
              await room?.localParticipant?.setMicrophoneEnabled(true);
            } catch (e) {
              developer.log("Deferred mic enable failed: $e");
            }
          }
          notifyListeners();
        })
        ..on<ParticipantDisconnectedEvent>((event) {
          // 🔥 NAYA — group call tile map se ye banda hata do.
          remoteTiles.remove(event.participant.identity);
          remoteTileVideoTracks.remove(event.participant.identity);

          // Purana singular behavior sirf tab trigger karo jab koi bhi
          // remote participant baaki na bacha ho (1-on-1 call ke liye
          // yehi hamesha true hota tha; group call me ek banda chhodne
          // se poori call "disconnected" nahi maani jaani chahiye).
          if (remoteTiles.isEmpty) {
            remoteConnected = false;
            remoteVideoTrack = null;
            _stopCallTimer();
            _startReconnectCountdown();
          }
          notifyListeners();
        })
        ..on<RoomDisconnectedEvent>((_) {
          _stopRingtone();
          if (isReconnecting) return;
          _stopCallTimer();
          _cleanup();
        })
        ..on<RoomReconnectingEvent>((_) => _startReconnectCountdown())
        ..on<RoomReconnectedEvent>((_) {
          isReconnecting = false;
          remoteConnected = true;
          status = "Connected";
          _reconnectTimer?.cancel();
          _startCallTimer();
          notifyListeners();
        })
        ..on<RoomAttemptReconnectEvent>((event) {
          developer.log("Attempt reconnect: ${event.attempt}");
          _startReconnectCountdown();
        })
        // 🔥 NAYA — dusri taraf ne hold/resume kiya to unka data-channel
        // message yahan aata hai, taaki hamari screen pe bhi "On hold"
        // dikha sakein.
        ..on<DataReceivedEvent>((event) {
          try {
            final decoded = jsonDecode(utf8.decode(event.data)) as Map<String, dynamic>;
            if (decoded['type'] == 'call_hold') {
              peerOnHold = decoded['hold'] == true;
              notifyListeners();
            }
          } catch (e) {
            developer.log("DataReceivedEvent parse failed: $e");
          }
        });

      await room!.connect(_livekitUrl, _livekitToken);
      await Hardware.instance.setSpeakerphoneOn(true);

      // 🔥 NAYA — agar hum ek aisi group call me connect ho rahe hain
      // jisme humse pehle se hi doosre log maujood hain (e.g. koi humein
      // mid-call add kare), to unke liye ParticipantConnectedEvent
      // dobara fire nahi hota — is room ka snapshot khud le lo.
      for (final p in room!.remoteParticipants.values) {
        remoteTiles[p.identity] = p;
      }

      // 🔥 FIX: callee has already tapped "Accept" by the time this runs,
      // so their mic should go live immediately. The caller, however, is
      // still ringing at this point — enabling the mic now would switch
      // Android's audio mode and mute the outgoing ringtone. For the
      // caller, this is deferred to ParticipantConnectedEvent (above),
      // i.e. the moment the callee actually joins.
      if (!isCaller) {
        await room!.localParticipant?.setMicrophoneEnabled(true);
      } else {
        _micPendingForCaller = true;
      }

      if (isVideo) {
        await room!.localParticipant?.setCameraEnabled(true);
        final pub = room!.localParticipant?.videoTrackPublications.firstOrNull;
        if (pub?.track != null) localVideoTrack = pub!.track as LocalVideoTrack;
      }

      status = isCaller ? "Ringing..." : "Connecting...";
      notifyListeners();
    } catch (e, s) {
      developer.log("Call init failed: $e", stackTrace: s);
      await _stopRingtone();
      error = "Failed: $e";
      notifyListeners();
    }
  }

  void _onTrackSubscribed(TrackSubscribedEvent event) {
    _noAnswerTimer?.cancel();
    _noAnswerTimer = null;
    // 🔥 NAYA — is participant ko tile map me hona hi chahiye (safety net
    // agar kisi wajah se ParticipantConnectedEvent miss ho gaya ho).
    remoteTiles[event.participant.identity] = event.participant;
    if (event.track is VideoTrack) {
      final isScreen = event.publication.source == TrackSource.screenShareVideo;
      if (isScreen) {
        remoteScreenTrack = event.track as VideoTrack;
      } else {
        remoteVideoTrack = event.track as VideoTrack;
        // 🔥 NAYA — group grid ke liye per-participant camera track.
        remoteTileVideoTracks[event.participant.identity] = event.track as VideoTrack;
      }
      remoteConnected = true;
      status = "Connected";
      isReconnecting = false;
      _stopRingtone();
      _startCallTimer();
      _reconnectTimer?.cancel();
      notifyListeners();
    } else if (event.track is AudioTrack) {
      remoteConnected = true;
      status = "Connected";
      isReconnecting = false;
      _stopRingtone();
      _startCallTimer();
      _reconnectTimer?.cancel();
      Hardware.instance.setSpeakerphoneOn(true);
      notifyListeners();
    }
  }

  void _onTrackUnsubscribed(TrackUnsubscribedEvent event) {
    if (event.track is VideoTrack) {
      if (event.publication.source == TrackSource.screenShareVideo) {
        remoteScreenTrack = null;
      } else {
        remoteVideoTrack = null;
        remoteTileVideoTracks.remove(event.participant.identity);
      }
      notifyListeners();
    }
  }

  // ============================================================
  // TIMERS
  // ============================================================
  void _startCallTimer() {
    if (_callTimer != null) return;
    _connectedAt = DateTime.now();
    _callTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (_connectedAt != null) {
        connectedDuration = DateTime.now().difference(_connectedAt!);
        notifyListeners();
      }
    });
  }

  void _stopCallTimer() {
    _callTimer?.cancel();
    _callTimer = null;
  }

  void _startReconnectCountdown() {
    if (_reconnectTimer?.isActive == true) return;
    reconnectSecondsLeft = 2;
    isReconnecting = true;
    notifyListeners();

    _reconnectTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      reconnectSecondsLeft--;
      status = "Reconnecting... ${reconnectSecondsLeft}s";
      notifyListeners();
      if (reconnectSecondsLeft <= 0) {
        timer.cancel();
        _stopCallTimer();
        _cleanup();
      }
    });
  }

  // 🔥 NAYA — caller ki taraf se: agar 30 second tak dusra banda call
  // receive na kare (remoteConnected kabhi true na ho), to call apne aap
  // cut ho jaati hai. `endCall()` reuse karte hain taaki backend ko bhi
  // "end" action ki normal notification chali jaaye, ringtone ruke, aur
  // saari state consistent tarike se clean ho.
  void _startNoAnswerTimer() {
    _noAnswerTimer?.cancel();
    _noAnswerTimer = Timer(const Duration(seconds: 30), () {
      if (!isActive || remoteConnected) return; // already answered/ended
      developer.log("No answer within 30s — auto-ending call $callId");
      status = "No answer";
      notifyListeners();
      endCall();
    });
  }

  // ============================================================
  // IN-CALL CONTROLS
  // ============================================================
  Future<void> toggleMic() async {
    muted = !muted;
    notifyListeners();
    try {
      await room?.localParticipant?.setMicrophoneEnabled(!muted);
    } catch (e) {
      developer.log("toggleMic failed: $e");
    }
  }

  Future<void> toggleCamera() async {
    if (!isVideo) return;
    videoOff = !videoOff;
    notifyListeners();
    try {
      await room?.localParticipant?.setCameraEnabled(!videoOff);
    } catch (e) {
      developer.log("toggleCamera failed: $e");
    }
  }

  Future<void> toggleSpeaker() async {
    speakerOn = !speakerOn;
    notifyListeners();
    try {
      await Hardware.instance.setSpeakerphoneOn(speakerOn);
    } catch (e) {
      developer.log("toggleSpeaker failed: $e");
    }
  }

  // ============================================================
  // 🔥 NAYA — HOLD. WebRTC/LiveKit me SIP jaisa built-in "hold" nahi hota,
  // isliye ise khud simulate karte hain: apna mic+camera band karo (state
  // yaad rakhke, taaki resume par exactly wahi wapas aaye jo pehle tha),
  // dusri taraf ka audio bhi apni taraf se mute kar do (taaki hold ke
  // dauraan kuch sunayi na de), aur ek chhota data-channel message bhejo
  // taaki peer ki screen pe bhi "X ne aapko hold pe daala" dikh sake.
  // ============================================================
  Future<void> toggleHold() async {
    final next = !onHold;
    try {
      if (next) {
        _micWasMutedBeforeHold = muted;
        _cameraWasOffBeforeHold = videoOff;

        if (!muted) {
          await room?.localParticipant?.setMicrophoneEnabled(false);
        }
        if (isVideo && !videoOff) {
          await room?.localParticipant?.setCameraEnabled(false);
        }
        muted = true;
        videoOff = isVideo ? true : videoOff;

        for (final p in room?.remoteParticipants.values ?? const <RemoteParticipant>[]) {
          for (final pub in p.audioTrackPublications) {
            try {
              pub.track?.mediaStreamTrack.enabled = false;
            } catch (_) {}
          }
        }
      } else {
        if (!_micWasMutedBeforeHold) {
          await room?.localParticipant?.setMicrophoneEnabled(true);
          muted = false;
        }
        if (isVideo && !_cameraWasOffBeforeHold) {
          await room?.localParticipant?.setCameraEnabled(true);
          videoOff = false;
        }

        for (final p in room?.remoteParticipants.values ?? const <RemoteParticipant>[]) {
          for (final pub in p.audioTrackPublications) {
            try {
              pub.track?.mediaStreamTrack.enabled = true;
            } catch (_) {}
          }
        }
      }

      onHold = next;
      notifyListeners();

      try {
        await room?.localParticipant?.publishData(
          utf8.encode(jsonEncode({'type': 'call_hold', 'hold': next})),
          reliable: true,
        );
      } catch (e) {
        developer.log("publishData(call_hold) failed: $e");
      }
    } catch (e) {
      developer.log("toggleHold failed: $e");
      error = "Hold failed: $e";
      notifyListeners();
    }
  }

  // ============================================================
  // 🔥 NAYA — GROUP CALL: naye banda ko chalti hui call me add karo.
  // LiveKit room already multi-party hai, isliye yahan sirf backend ko
  // batana hota hai — wo naye user ko normal incoming-call invite bhej
  // dega (usi call_id ke saath). Accept karne par woh khud room me aa
  // jaayega aur ParticipantConnectedEvent + TrackSubscribedEvent se
  // apne aap `remoteTiles` grid me dikhne lagega — is method ko khud
  // room state chhedne ki zaroorat nahi.
  // ============================================================
  bool addingParticipant = false;
  String? addParticipantError;

  Future<bool> addParticipant(String userId) async {
    final id = callId;
    if (id == null) return false;
    addingParticipant = true;
    addParticipantError = null;
    notifyListeners();
    try {
      await CallApiService.addParticipant(id, userId);
      return true;
    } catch (e) {
      developer.log("addParticipant failed: $e");
      addParticipantError = "Add participant failed: $e";
      return false;
    } finally {
      addingParticipant = false;
      notifyListeners();
    }
  }

  // ============================================================
  // 🔥 NAYA — Front/back CAMERA SWITCH (video call ke dauraan). Screen
  // share ya audio-only call me disabled rakho (button khud UI me hide
  // hoga — call_screen.dart dekho).
  // ============================================================
  Future<void> switchCamera() async {
    if (!isVideo || videoOff) return;
    try {
      final pub = room?.localParticipant?.videoTrackPublications
          .where((p) => p.source == TrackSource.camera)
          .firstOrNull;
      final track = pub?.track;
      if (track == null || track is! LocalVideoTrack) return;

      final newPosition = isFrontCamera ? CameraPosition.back : CameraPosition.front;
      await track.setCameraPosition(newPosition);
      isFrontCamera = !isFrontCamera;
      notifyListeners();
    } catch (e) {
      developer.log("switchCamera failed: $e");
      // Kuch devices/versions pe setCameraPosition available na ho to bhi
      // call crash nahi honi chahiye — button bas kaam nahi karega.
    }
  }


  // ko chalu hote hi ek RUNNING foreground service chahiye — warna OS
  // turant process crash kar deta hai (Android 14+ pe to
  // `mediaProjection` foregroundServiceType declare karna literally
  // mandatory hai). Poori call ke liye ye service hum jaan-bujh kar band
  // rakhte hain (battery-optimization popup na aaye isliye — upar
  // `_initCall` ka comment dekho), isliye ab isse SIRF screen-share ke
  // duration ke liye chalu/band karte hain: on karte waqt start, off
  // karte waqt (ya call khatam hote waqt _cleanup se) turant stop.
  Future<void> toggleScreenShare() async {
    final next = !isScreenSharing;
    try {
      if (next) {
        // Screen capture start hone se PEHLE service running honi
        // chahiye — order yahan matter karta hai.
        await _enableBackgroundExecution();
      }

      await room?.localParticipant?.setScreenShareEnabled(next);

      if (next) {
        await Future.delayed(const Duration(milliseconds: 300));
        final pub = room?.localParticipant?.videoTrackPublications
            .where((p) => p.source == TrackSource.screenShareVideo)
            .firstOrNull;
        isScreenSharing = true;
        localScreenTrack = pub?.track as LocalVideoTrack?;
      } else {
        isScreenSharing = false;
        localScreenTrack = null;
        // Ab foreground service ki zaroorat nahi — band kar do taaki
        // baaki call normal (bina service ke) chalti rahe.
        await _disableBackgroundExecution();
      }
      notifyListeners();
    } catch (e) {
      developer.log("Screen share failed: $e");
      error = "Screen share failed: $e";
      isScreenSharing = false;
      localScreenTrack = null;
      // Fail hua to bhi service chalu chhod ke mat rakho.
      await _disableBackgroundExecution();
      notifyListeners();
    }
  }

  // ============================================================
  // END / CLEANUP
  // ============================================================
  Future<void> endCall() async {
    _stopCallTimer();
    _reconnectTimer?.cancel();
    _noAnswerTimer?.cancel();
    _noAnswerTimer = null;
    await _stopRingtone();

    final id = callId;
    if (id != null) {
      try {
        await CallApiService.callAction(id, 'end');
      } catch (e) {
        developer.log("Backend end-call notification failed (non-fatal): $e");
      }
    }
    await _cleanup();
  }

  Future<void> _cleanup() async {
    isActive = false;
    isMinimized = false;
    remoteConnected = false;
    remoteVideoTrack = null;
    localVideoTrack = null;
    remoteScreenTrack = null;
    localScreenTrack = null;
    remoteTiles.clear();
    remoteTileVideoTracks.clear();
    isScreenSharing = false;
    status = "Connecting...";
    error = null;
    needsSettingsRedirect = false;
    callId = null;
    _micPendingForCaller = false;
    _noAnswerTimer?.cancel();
    _noAnswerTimer = null;

    WakelockPlus.disable();
    await _disableBackgroundExecution();

    try {
      await _listener?.dispose();
      await room?.disconnect();
      await room?.dispose();
    } catch (e) {
      developer.log("Room teardown failed (non-fatal): $e");
    }

    _listener = null;
    room = null;
    notifyListeners();
  }
}