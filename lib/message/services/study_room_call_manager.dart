// message/services/study_room_call_manager.dart
//
// Google Meet jaisa study-room media manager — CallManager (call_manager.dart)
// se BILKUL ALAG hai:
//   - CallManager: 1:1 "phone call" — ring karta hai, accept/reject hota
//     hai, no-answer timeout hai, sirf EK remote peer hota hai.
//   - StudyRoomCallManager (ye file): koi ringing nahi. Screen khulte hi
//     seedha LiveKit room se connect ho jaata hai (jaise Meet link kholte
//     hi ho jaata hai), camera/mic DEFAULT OFF rehte hain, user khud
//     apni marzi se on/off karta hai. Kitne bhi participants ho sakte
//     hain — har ek ka apna alag video track userId se track hota hai.
//
// Har StudyRoomScreen apna khud ka instance banaye (global singleton
// nahi) — jaise: `final _roomCall = StudyRoomCallManager();` — aur
// screen dispose hote hi `_roomCall.leaveRoom()` call karo.
import 'dart:async';
import 'dart:developer' as developer;
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:livekit_client/livekit_client.dart';
import 'package:permission_handler/permission_handler.dart';
// 🔥 NAYA — Feature 3 (class transcript): har participant apna mic
// locally chunk-record karta hai (existing `record` package, chat
// voice-note ki tarah — LiveKit server-side room recording/egress abhi
// is stack me wired nahi hai, isliye "poori class ki ek continuous
// recording" abhi possible nahi, per-participant chunked capture hi
// buildable tha).
import 'package:record/record.dart';
import 'package:path_provider/path_provider.dart';
import 'message_api_service.dart'; // same services/ folder
import 'ai_study_service.dart'; // same services/ folder
// 🔥 NAYA — screen share ke liye. `Helper.requestCapturePermission()`
// `package:flutter_webrtc` se aata hai (Android par capture permission
// maangne ke liye). `flutter_background` alag package hai, Android par
// media-projection foreground service chalane ke liye zaroori —
// pubspec.yaml me `flutter_background: ^1.3.0` add karna hoga.
// iOS ke liye koi extra Dart import nahi chahiye — Broadcast Extension
// (Xcode side, alag se banani hogi) `setScreenShareEnabled(...)` call ka
// khud jawab deti hai.
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:flutter_background/flutter_background.dart';

class StudyRoomCallManager extends ChangeNotifier {
  Room? room;
  EventsListener<RoomEvent>? _listener;

  bool isConnected = false;
  bool isConnecting = false;
  String? error;
  // 🔥 FIX — jab permission "Deny & don't ask again" ho chuki ho, request()
  // dubara popup nahi dikhayega, seedha denied laut aayega. Screen isko
  // dekh kar user ko "Settings me jaake allow karo" bata sakti hai.
  bool needsSettingsRedirect = false;

  // 🔥 NAYA — screen ko batata hai ki KAUN sa participant LiveKit room se
  // disconnect hua (sirf `notifyListeners()` se "kuch badla hai" pata
  // chalta tha, "kisko hataana hai" pata nahi chalta tha). StudyRoomScreen
  // isse apni `_windows` list se us user ka floating profile/camera
  // window hata sakta hai.
  void Function(String identity)? onParticipantLeft;

  // ---- Apni (local) state ----
  bool micOn = false;
  bool cameraOn = false;
  LocalVideoTrack? localVideoTrack;

  // ---- Doosre participants ki state — userId (LiveKit identity) se keyed ----
  final Map<String, VideoTrack> remoteVideoTracks = {};
  final Map<String, bool> remoteMicOn = {};

  // ---- Screen share state ----
  // 🔥 NAYA — Google Meet-style "present": koi bhi participant apni screen
  // share kar sakta hai, aur har koi (presenter samet) usi pen/marker se
  // uske upar draw kar sakta hai (drawing khud whiteboard page ke strokes
  // se hi handle hoti hai — ye sirf background video track manage karta
  // hai). Ek time par ek hi "active" presentation track dikhaya jaata hai.
  bool isScreenSharing = false;
  LocalVideoTrack? _localScreenTrack;
  final Map<String, VideoTrack> screenShareTracks = {};
  bool _androidBackgroundEnabled = false;

  // ---- 🔥 NAYA — class transcript: chunked local mic recording ----
  static const Duration _transcriptChunkDuration = Duration(seconds: 45);
  final AudioRecorder _transcriptRecorder = AudioRecorder();
  Timer? _transcriptChunkTimer;
  String? _transcriptConversationId;
  String? _transcriptSessionId;
  DateTime? _transcriptSessionStart; // session-relative offsets isi se calculate hote hain
  Duration _transcriptElapsedBeforeCurrentChunk = Duration.zero;
  bool isRecordingTranscript = false;

  /// Jo bhi is waqt present kar raha hai uska video track — pehle apna
  /// (agar main present kar raha hoon), warna jo bhi remote presenter mila.
  VideoTrack? get activePresentationTrack {
    if (isScreenSharing && _localScreenTrack != null) return _localScreenTrack;
    if (screenShareTracks.isNotEmpty) return screenShareTracks.values.first;
    return null;
  }

  /// Active presenter ki LiveKit identity (userId) — null agar koi present
  /// nahi kar raha.
  String? get activePresenterId {
    if (isScreenSharing) return room?.localParticipant?.identity;
    if (screenShareTracks.isNotEmpty) return screenShareTracks.keys.first;
    return null;
  }

  // ============================================================
  // JOIN — ringing/accept kuch nahi, seedha connect. Backend se
  // `livekitUrl`/`livekitToken` StudyRoomScreen already fetch karke
  // yahan pass karega (naya `CallApiService.joinStudyRoom()` endpoint).
  // ============================================================
  Future<void> joinRoom({
    required String livekitUrl,
    required String livekitToken,
  }) async {
    if (isConnected || isConnecting) return;
    isConnecting = true;
    error = null;
    notifyListeners();

    try {
      room = Room(roomOptions: const RoomOptions(adaptiveStream: true, dynacast: true));
      _listener = room!.createListener();

      _listener!
        ..on<TrackSubscribedEvent>(_onTrackSubscribed)
        ..on<TrackUnsubscribedEvent>(_onTrackUnsubscribed)
        ..on<TrackMutedEvent>((e) {
          remoteMicOn[e.participant.identity] = false;
          notifyListeners();
        })
        ..on<TrackUnmutedEvent>((e) {
          remoteMicOn[e.participant.identity] = true;
          notifyListeners();
        })
        ..on<ParticipantConnectedEvent>((_) => notifyListeners())
        ..on<ParticipantDisconnectedEvent>((event) {
          final identity = event.participant.identity;
          remoteVideoTracks.remove(identity);
          remoteMicOn.remove(identity);
          screenShareTracks.remove(identity);
          onParticipantLeft?.call(identity);
          notifyListeners();
        })
        ..on<RoomDisconnectedEvent>((_) {
          isConnected = false;
          notifyListeners();
        });

      await room!.connect(livekitUrl, livekitToken);

      // 🔥 CHANGED — pehle Google Meet default tha: camera/mic dono OFF
      // jab tak user khud on na kare. User ne ab explicitly ye badalne
      // ko bola hai: join hote hi seedha dono ON ho jaayein (OS ka
      // permission popup ek baar aayega — wo koi bhi app skip nahi kar
      // sakta, Android/iOS ki security requirement hai — lekin uske
      // baad user ko khud kuch dabana nahi padega, camera/mic turant
      // chalu mil jaayenge). Jisko na chahiye wo neeche diye mic/camera
      // buttons se khud OFF kar sakta hai.
      final micGranted = await _requestMicPermission();
      if (micGranted) {
        try {
          await room!.localParticipant?.setMicrophoneEnabled(true);
          micOn = true;
        } catch (e) {
          developer.log("StudyRoom auto-enable mic failed: $e");
          micOn = false;
        }
      }

      final camGranted = await _requestCameraPermission();
      if (camGranted) {
        try {
          await room!.localParticipant?.setCameraEnabled(true);
          cameraOn = true;
          final pub = room?.localParticipant?.videoTrackPublications
              .where((p) => p.source == TrackSource.camera)
              .firstOrNull;
          localVideoTrack = pub?.track as LocalVideoTrack?;
        } catch (e) {
          developer.log("StudyRoom auto-enable camera failed: $e");
          cameraOn = false;
        }
      }

      // Agar permission denied ho gayi (ya permanently denied), error/
      // needsSettingsRedirect already `_requestMicPermission`/
      // `_requestCameraPermission` ke andar set ho chuka hai — connect
      // hona nahi rukta, bas us particular track ke bina room join hota
      // hai aur user baad me settings se allow karke toggle button se
      // try kar sakta hai.

      isConnected = true;
      isConnecting = false;
      notifyListeners();
    } catch (e, s) {
      developer.log("StudyRoomCallManager join failed: $e", stackTrace: s);
      isConnecting = false;
      error = "Media connect failed: $e";
      notifyListeners();
    }
  }

  void _onTrackSubscribed(TrackSubscribedEvent event) {
    if (event.track is VideoTrack && event.publication.source == TrackSource.camera) {
      remoteVideoTracks[event.participant.identity] = event.track as VideoTrack;
      notifyListeners();
    } else if (event.track is VideoTrack && event.publication.source == TrackSource.screenShareVideo) {
      // 🔥 NAYA — remote screen share track alag map me, camera se mix nahi
      // hota (ek participant ka camera AUR screen share dono ek saath
      // active ho sakte hain).
      screenShareTracks[event.participant.identity] = event.track as VideoTrack;
      notifyListeners();
    } else if (event.track is AudioTrack) {
      remoteMicOn[event.participant.identity] = true;
      notifyListeners();
    }
  }

  void _onTrackUnsubscribed(TrackUnsubscribedEvent event) {
    if (event.track is VideoTrack && event.publication.source == TrackSource.camera) {
      remoteVideoTracks.remove(event.participant.identity);
      notifyListeners();
    } else if (event.track is VideoTrack && event.publication.source == TrackSource.screenShareVideo) {
      screenShareTracks.remove(event.participant.identity);
      notifyListeners();
    }
  }

  // ============================================================
  // SCREEN SHARE — har participant apni screen present kar sakta hai.
  // Android: media-projection foreground service zaroori hai (verna OS
  // capture ko turant kill kar deta hai jaise hi app background jaata
  // hai) — isliye `flutter_background` se ek foreground service chalate
  // hain jab tak sharing chalu hai.
  // iOS: Broadcast Upload Extension zaroori hai (Xcode target — is file
  // se nahi banaya ja sakta, alag se add karna hoga, neeche notes dekho).
  // ============================================================
  Future<bool> startScreenShare() async {
    if (room == null || isScreenSharing) return false;
    error = null;
    try {
      if (defaultTargetPlatform == TargetPlatform.android) {
        final hasCapturePermission = await Helper.requestCapturePermission();
        if (!hasCapturePermission) {
          error = "Screen record permission denied";
          notifyListeners();
          return false;
        }
        if (!_androidBackgroundEnabled) {
          await FlutterBackground.initialize(
            androidConfig: const FlutterBackgroundAndroidConfig(
              notificationTitle: "Study Room",
              notificationText: "Screen sharing is active",
              notificationImportance: AndroidNotificationImportance.normal,
            ),
          );
          _androidBackgroundEnabled = await FlutterBackground.enableBackgroundExecution();
        }
      }
      // iOS: yahan Dart side se extra kuch call karne ki zaroorat nahi.
      // Broadcast Extension (alag Xcode target, native side pe banani
      // hogi) khud hi neeche wali `setScreenShareEnabled(...)` call ke
      // jawab me system ka ReplayKit broadcast picker khol deti hai —
      // bas `useiOSBroadcastExtension: true` set hona chahiye, jo neeche
      // pehle se hai.

      await room!.localParticipant?.setScreenShareEnabled(
        true,
        captureScreenAudio: false,
        screenShareCaptureOptions: const ScreenShareCaptureOptions(
          useiOSBroadcastExtension: true,
        ),
      );

      // Jo track abhi publish hua, usi ko local render ke liye pakad lo
      // (`.firstOrNull` jaisi extension par depend nahi karte — koi
      // extra package import ki zaroorat nahi).
      dynamic screenPub;
      for (final p in room?.localParticipant?.videoTrackPublications ?? const []) {
        if (p.source == TrackSource.screenShareVideo) {
          screenPub = p;
          break;
        }
      }
      _localScreenTrack = screenPub?.track as LocalVideoTrack?;

      isScreenSharing = true;
      notifyListeners();
      return true;
    } catch (e, s) {
      developer.log("startScreenShare failed: $e", stackTrace: s);
      error = "Screen share failed: $e";
      notifyListeners();
      return false;
    }
  }

  Future<void> stopScreenShare() async {
    if (room == null || !isScreenSharing) return;
    try {
      await room!.localParticipant?.setScreenShareEnabled(false);
    } catch (e) {
      developer.log("stopScreenShare failed (non-fatal): $e");
    }
    if (defaultTargetPlatform == TargetPlatform.android && _androidBackgroundEnabled) {
      try {
        await FlutterBackground.disableBackgroundExecution();
      } catch (_) {}
      _androidBackgroundEnabled = false;
    }
    _localScreenTrack = null;
    isScreenSharing = false;
    notifyListeners();
  }

  // ============================================================
  // 🔥 NAYA — CLASS TRANSCRIPT: chunked local mic recording
  // ------------------------------------------------------------
  // StudyRoomScreen ye call kare `_joinStudyRoomMedia()` ke turant baad
  // (jab tak room join ho chuka hai — mic permission already granted hai
  // us waqt tak, isliye alag se permission nahi maangte yahan). `sessionId`
  // backend ke `joinStudyRoom` response se aata hai (naya field — backend
  // ko `StudyRoomJoinView` me har session ke liye ek stable id return
  // karna hoga, taaki naya-session-per-open ka transcript purani session
  // se mix na ho).
  //
  // Recording continuous nahi hai — har ~45s pe purana chunk stop hoke
  // upload hota hai, naya turant shuru ho jaata hai. Isse:
  //   1. Transcript progressively aata rehta hai (poori class khatam hone
  //      ka wait nahi karna padta result dekhne ke liye)
  //   2. App crash/force-close ho jaaye to sirf last ~45s ka transcript
  //      miss hota hai, poori class ka nahi
  // ============================================================
  Future<void> startTranscriptRecording({
    required String conversationId,
    required String sessionId,
  }) async {
    if (isRecordingTranscript) return;
    _transcriptConversationId = conversationId;
    _transcriptSessionId = sessionId;
    _transcriptSessionStart = DateTime.now();
    _transcriptElapsedBeforeCurrentChunk = Duration.zero;
    isRecordingTranscript = true;

    await _beginNextTranscriptChunk();
    _transcriptChunkTimer = Timer.periodic(_transcriptChunkDuration, (_) => _rotateTranscriptChunk());
  }

  Future<void> _beginNextTranscriptChunk() async {
    try {
      final hasPermission = await _transcriptRecorder.hasPermission();
      if (!hasPermission) return; // mic permission na ho to transcript feature bas silently skip ho jaaye, class join block nahi hona chahiye
      final tempDir = await getTemporaryDirectory();
      final path = "${tempDir.path}/class_chunk_${DateTime.now().millisecondsSinceEpoch}.m4a";
      await _transcriptRecorder.start(const RecordConfig(encoder: AudioEncoder.aacLc), path: path);
    } catch (e) {
      developer.log("startTranscriptRecording chunk start failed: $e");
    }
  }

  Future<void> _rotateTranscriptChunk() async {
    if (!isRecordingTranscript) return;
    final startOffset = _transcriptElapsedBeforeCurrentChunk;
    final endOffset = startOffset + _transcriptChunkDuration;
    _transcriptElapsedBeforeCurrentChunk = endOffset;

    String? path;
    try {
      path = await _transcriptRecorder.stop();
    } catch (e) {
      developer.log("rotateTranscriptChunk stop failed: $e");
    }

    // Agla chunk turant shuru karo — upload background me chalta rahega,
    // isse transcript me gap nahi aata.
    if (isRecordingTranscript) unawaited(_beginNextTranscriptChunk());

    if (path == null) return;
    unawaited(_uploadTranscriptChunk(path, startOffset, endOffset));
  }

  Future<void> _uploadTranscriptChunk(String path, Duration startOffset, Duration endOffset) async {
    final conversationId = _transcriptConversationId;
    final sessionId = _transcriptSessionId;
    if (conversationId == null || sessionId == null) return;

    try {
      // Bahut chhota chunk (koi awaz hi nahi aayi shayad) — transcribe
      // karwane ka koi fayda nahi, storage/AI quota bachao.
      final file = File(path);
      final size = await file.exists() ? await file.length() : 0;
      if (size < 2000) {
        try { await file.delete(); } catch (_) {}
        return;
      }

      final uploaded = await MessageApiService.uploadFile(file);
      await AiStudyService.registerTranscriptChunk(
        conversationId: conversationId,
        sessionId: sessionId,
        audioFileUrl: uploaded.fileUrl,
        startOffset: startOffset,
        endOffset: endOffset,
      );
    } catch (e) {
      // Best-effort — ek chunk fail ho jaaye to poori class transcript
      // sirf usi chunk jitna incomplete rahega, kuch aur break nahi hota.
      developer.log("uploadTranscriptChunk failed: $e");
    } finally {
      try { await File(path).delete(); } catch (_) {}
    }
  }

  Future<void> stopTranscriptRecording() async {
    if (!isRecordingTranscript) return;
    isRecordingTranscript = false;
    _transcriptChunkTimer?.cancel();
    _transcriptChunkTimer = null;

    final startOffset = _transcriptElapsedBeforeCurrentChunk;
    final endOffset = startOffset + (DateTime.now().difference(_transcriptSessionStart ?? DateTime.now()) - startOffset);
    String? path;
    try {
      path = await _transcriptRecorder.stop();
    } catch (e) {
      developer.log("stopTranscriptRecording stop failed: $e");
    }
    if (path != null) {
      unawaited(_uploadTranscriptChunk(path, startOffset, endOffset));
    }
    _transcriptConversationId = null;
    _transcriptSessionId = null;
    _transcriptSessionStart = null;
  }

  // ============================================================
  // PERMISSIONS
  // ------------------------------------------------------------
  // 🔥 FIX — ye method pehle poori file me kahin nahi tha. CallManager
  // (1:1 calls) me `_requestPermissions()` explicitly `Permission.
  // microphone.request()` / `Permission.camera.request()` call karta
  // hai, JOIN se pehle. Study room me ye step missing tha — seedha
  // `setMicrophoneEnabled(true)` / `setCameraEnabled(true)` call ho raha
  // tha bina runtime permission maange. LiveKit/WebRTC kabhi-kabhi khud
  // OS permission dialog trigger kar deta hai, lekin bahut saare Android
  // OEMs (Xiaomi/Oppo/Vivo) aur kai iOS cases me ye silently fail ho
  // jaata hai agar permission_handler se explicitly pehle maanga na
  // gaya ho — yahi wajah thi ki call screen pe kaam karta tha (wahan
  // CallManager permission maangta hai) lekin study room me nahi.
  // ============================================================
  Future<bool> _requestMicPermission() async {
    final status = await Permission.microphone.request();
    if (!status.isGranted) {
      needsSettingsRedirect = status.isPermanentlyDenied;
      error = needsSettingsRedirect
          ? "Mic permission denied. Settings me jaake allow karo."
          : "Mic permission denied";
      notifyListeners();
      return false;
    }
    return true;
  }

  Future<bool> _requestCameraPermission() async {
    final status = await Permission.camera.request();
    if (!status.isGranted) {
      needsSettingsRedirect = status.isPermanentlyDenied;
      error = needsSettingsRedirect
          ? "Camera permission denied. Settings me jaake allow karo."
          : "Camera permission denied";
      notifyListeners();
      return false;
    }
    return true;
  }

  // ============================================================
  // SELF CONTROLS — jisko jo chahiye wo khud dabaye
  // ============================================================
  Future<void> toggleMic() async {
    if (!isConnected) return;

    // Turn ON karte waqt hi permission chahiye — turn OFF karne ke liye
    // permission maangne ki zaroorat nahi.
    if (!micOn) {
      final granted = await _requestMicPermission();
      if (!granted) return; // error/needsSettingsRedirect already set
    }

    micOn = !micOn;
    notifyListeners();
    try {
      await room?.localParticipant?.setMicrophoneEnabled(micOn);
    } catch (e) {
      developer.log("StudyRoom toggleMic failed: $e");
      micOn = !micOn; // revert on failure
      error = "Mic enable failed: $e";
      notifyListeners();
    }
  }

  Future<void> toggleCamera() async {
    if (!isConnected) return;

    if (!cameraOn) {
      final granted = await _requestCameraPermission();
      if (!granted) return;
    }

    cameraOn = !cameraOn;
    notifyListeners();
    try {
      await room?.localParticipant?.setCameraEnabled(cameraOn);
      if (cameraOn) {
        final pub = room?.localParticipant?.videoTrackPublications
            .where((p) => p.source == TrackSource.camera)
            .firstOrNull;
        localVideoTrack = pub?.track as LocalVideoTrack?;
      } else {
        localVideoTrack = null;
      }
      notifyListeners();
    } catch (e) {
      developer.log("StudyRoom toggleCamera failed: $e");
      cameraOn = !cameraOn; // revert on failure
      error = "Camera enable failed: $e";
      notifyListeners();
    }
  }

  // ============================================================
  // LEAVE — StudyRoomScreen.dispose() se call karo
  // ============================================================
  Future<void> leaveRoom() async {
    await stopTranscriptRecording();
    try {
      await _listener?.dispose();
      await room?.disconnect();
      await room?.dispose();
    } catch (e) {
      developer.log("StudyRoomCallManager leaveRoom teardown failed (non-fatal): $e");
    }
    _listener = null;
    room = null;
    isConnected = false;
    isConnecting = false;
    micOn = false;
    cameraOn = false;
    localVideoTrack = null;
    remoteVideoTracks.clear();
    remoteMicOn.clear();
    if (defaultTargetPlatform == TargetPlatform.android && _androidBackgroundEnabled) {
      try {
        await FlutterBackground.disableBackgroundExecution();
      } catch (_) {}
      _androidBackgroundEnabled = false;
    }
    isScreenSharing = false;
    _localScreenTrack = null;
    screenShareTracks.clear();
  }
}