// ============================================================
// LIVECLASS — LIVEKIT STAGE
//
// The actual audio/video for a live class. `LiveSessionScreen` used to leave
// a placeholder box here; the backend's `sessions/<id>/join/` (and
// `classrooms/<id>/start-or-join/`) already return `livekit_url` +
// `livekit_token`, so this connects a `Room` with them and draws:
//   * a screen-share track if anyone is sharing (spotlight),
//   * otherwise the first remote camera (teacher),
//   * a small self-view (picture-in-picture) when our own camera is on.
//
// Same `livekit_client` API surface the message module already uses
// (Room / createListener / setMicrophoneEnabled / setCameraEnabled /
// VideoTrackRenderer), so no new package or version is needed.
//
// Publishing (mic/camera) is host-driven: the host/co-host auto-enables both
// on join; students join listen/watch-only and can turn mic/camera on with
// the controls (the backend token allows students to publish; a parent
// observer's token does not — publish attempts just fail quietly).
// ============================================================

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:livekit_client/livekit_client.dart';
import 'package:permission_handler/permission_handler.dart';

class LiveKitStageController extends ChangeNotifier {
  Room? _room;
  EventsListener<RoomEvent>? _listener;

  bool connecting = false;
  bool connected = false;
  bool micOn = false;
  bool camOn = false;
  String? error;

  Room? get room => _room;

  Future<void> connect({
    required String url,
    required String token,
    bool autoPublish = false,
  }) async {
    if (connected || connecting) return;
    connecting = true;
    error = null;
    notifyListeners();
    try {
      final room = Room(roomOptions: const RoomOptions(adaptiveStream: true, dynacast: true));
      _room = room;
      room.addListener(_onRoomChanged);
      _listener = room.createListener();
      _listener!.on<RoomDisconnectedEvent>((_) {
        connected = false;
        micOn = false;
        camOn = false;
        notifyListeners();
      });
      await room.connect(url, token);
      connected = true;
      connecting = false;
      notifyListeners();
      if (autoPublish) {
        await setMic(true);
        await setCam(true);
      }
    } catch (e) {
      error = e.toString();
      connecting = false;
      connected = false;
      notifyListeners();
    }
  }

  void _onRoomChanged() => notifyListeners();

  Future<void> setMic(bool on) async {
    final lp = _room?.localParticipant;
    if (lp == null) return;
    try {
      if (on && !(await Permission.microphone.request()).isGranted) return;
      await lp.setMicrophoneEnabled(on);
      micOn = on;
    } catch (_) {
      micOn = false;
    }
    notifyListeners();
  }

  Future<void> setCam(bool on) async {
    final lp = _room?.localParticipant;
    if (lp == null) return;
    try {
      if (on && !(await Permission.camera.request()).isGranted) return;
      await lp.setCameraEnabled(on);
      camOn = on;
    } catch (_) {
      camOn = false;
    }
    notifyListeners();
  }

  // ---- track lookup ---------------------------------------------------

  VideoTrack? _videoOf(Participant p, TrackSource source) {
    for (final pub in p.videoTrackPublications) {
      if (pub.source != source || pub.muted) continue;
      final t = pub.track;
      if (t is VideoTrack) return t;
    }
    return null;
  }

  /// Spotlight: any participant's screen share, else the first remote camera.
  VideoTrack? get mainTrack {
    final room = _room;
    if (room == null) return null;
    for (final p in room.remoteParticipants.values) {
      final s = _videoOf(p, TrackSource.screenShareVideo);
      if (s != null) return s;
    }
    for (final p in room.remoteParticipants.values) {
      final c = _videoOf(p, TrackSource.camera);
      if (c != null) return c;
    }
    return null;
  }

  VideoTrack? get selfTrack {
    final lp = _room?.localParticipant;
    return lp == null ? null : _videoOf(lp, TrackSource.camera);
  }

  int get remoteCount => _room?.remoteParticipants.length ?? 0;

  Future<void> disconnect() async {
    final room = _room;
    _room = null;
    connected = false;
    connecting = false;
    try {
      room?.removeListener(_onRoomChanged);
      await _listener?.dispose();
      await room?.disconnect();
      await room?.dispose();
    } catch (_) {}
    _listener = null;
  }

  @override
  void dispose() {
    unawaited(disconnect());
    super.dispose();
  }
}

class LiveKitStage extends StatelessWidget {
  final LiveKitStageController controller;
  final bool showControls;
  final String? waitingLabel;
  const LiveKitStage({
    super.key,
    required this.controller,
    this.showControls = true,
    this.waitingLabel,
  });

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        final main = controller.mainTrack;
        final self = controller.selfTrack;
        return ClipRRect(
          borderRadius: BorderRadius.circular(16),
          child: Container(
            color: const Color(0xFF15121D),
            child: Stack(fit: StackFit.expand, children: [
              if (controller.connecting)
                const Center(child: CircularProgressIndicator(color: Colors.white54))
              else if (main != null)
                VideoTrackRenderer(main, fit: VideoViewFit.contain)
              else
                Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    const Icon(Icons.videocam_off_rounded, color: Colors.white24, size: 44),
                    if ((controller.error ?? waitingLabel) != null) ...[
                      const SizedBox(height: 8),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        child: Text(
                          controller.error ?? waitingLabel!,
                          textAlign: TextAlign.center,
                          maxLines: 3,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: Colors.white54, fontSize: 12),
                        ),
                      ),
                    ],
                  ]),
                ),
              if (self != null)
                Positioned(
                  right: 8,
                  bottom: showControls ? 52 : 8,
                  width: 96,
                  height: 128,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(10),
                    child: VideoTrackRenderer(self, fit: VideoViewFit.cover),
                  ),
                ),
              if (showControls && controller.connected)
                Positioned(
                  left: 0,
                  right: 0,
                  bottom: 6,
                  child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                    _StageBtn(
                      icon: controller.micOn ? Icons.mic_rounded : Icons.mic_off_rounded,
                      active: controller.micOn,
                      onTap: () => controller.setMic(!controller.micOn),
                    ),
                    const SizedBox(width: 12),
                    _StageBtn(
                      icon: controller.camOn ? Icons.videocam_rounded : Icons.videocam_off_rounded,
                      active: controller.camOn,
                      onTap: () => controller.setCam(!controller.camOn),
                    ),
                  ]),
                ),
            ]),
          ),
        );
      },
    );
  }
}

class _StageBtn extends StatelessWidget {
  final IconData icon;
  final bool active;
  final VoidCallback onTap;
  const _StageBtn({required this.icon, required this.active, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      customBorder: const CircleBorder(),
      child: Container(
        width: 38,
        height: 38,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: active ? Colors.white : Colors.black54,
        ),
        child: Icon(icon, size: 19, color: active ? Colors.black87 : Colors.white),
      ),
    );
  }
}
