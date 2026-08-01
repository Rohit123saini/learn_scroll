// message/screens/incoming_call_screen.dart
//
// Full-screen incoming-call UI — outgoing call screen (call_screen.dart)
// jaisa hi look (radial gradient, pulsing-ring avatar), WhatsApp-style
// Accept/Decline buttons, aur khud ka ringtone + vibration jab tak app
// FOREGROUND me ho (kisi bhi screen pe — is screen ko `showIfNeeded` se
// global navigatorKey ke through push kiya jaata hai, isliye ChatScreen
// khuli hone ki zaroorat nahi).
//
// App BACKGROUND/KILLED ho tab ye screen involve nahi hoti — us state me
// native CallKit (call_kit_service.dart) apna khud ka system-level
// full-screen popup + ringtone dikhata hai.
//
// pubspec.yaml me ringtone asset declare karna zaroori hai:
//   flutter:
//     assets:
//       - assets/sounds/ringtone.wav

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show HapticFeedback;
import 'package:audioplayers/audioplayers.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../services/call_api_service.dart';
import 'call_screen.dart';

class IncomingCallScreen extends StatefulWidget {
  final String callId;
  final String callType; // "audio" | "video"
  final String callerName;
  final String? callerAvatar;
  final String conversationId;
  final bool isGroup;
  final String? groupTitle;

  const IncomingCallScreen({
    super.key,
    required this.callId,
    required this.callType,
    required this.callerName,
    required this.conversationId,
    this.callerAvatar,
    this.isGroup = false,
    this.groupTitle,
  });

  @override
  State<IncomingCallScreen> createState() => _IncomingCallScreenState();

  // ---------------------------------------------------------------------
  // 🔥 Global entry point. Do jagah se is screen ko trigger kiya ja sakta
  // hai (ek hi call ke liye dono ek saath bhi fire ho sakte hain):
  //   1) push_notification_service.dart — foreground FCM listener, app
  //      khuli ho kisi bhi screen pe (global navigatorKey se push).
  //   2) chat_screen.dart — WebSocket fallback (ChatScreen khuli ho).
  // Dono jagah SEEDHA Navigator.push mat karo — hamesha yahi static method
  // call karo, warna same call ke liye screen 2 baar stack ho sakti hai.
  // ---------------------------------------------------------------------
  static final Set<String> _activeCallIds = <String>{};

  static void showIfNeeded(
    NavigatorState? navigator, {
    required String callId,
    required String callType,
    required String callerName,
    required String conversationId,
    String? callerAvatar,
    bool isGroup = false,
    String? groupTitle,
  }) {
    if (navigator == null || callId.isEmpty) return;
    if (_activeCallIds.contains(callId)) return; // isi call ka screen already dikh raha hai
    _activeCallIds.add(callId);

    navigator
        .push(MaterialPageRoute(
          builder: (_) => IncomingCallScreen(
            callId: callId,
            callType: callType,
            callerName: callerName,
            conversationId: conversationId,
            callerAvatar: callerAvatar,
            isGroup: isGroup,
            groupTitle: groupTitle,
          ),
          fullscreenDialog: true,
        ))
        .whenComplete(() => _activeCallIds.remove(callId));
  }

  /// Backend se call_ended/cancelled event aaye to isse UI turant band kar do
  /// (agar is call ka incoming screen abhi bhi dikh raha ho).
  static void dismissIfShowing(NavigatorState? navigator, String callId) {
    if (!_activeCallIds.contains(callId)) return;
    if (navigator?.canPop() == true) navigator!.pop();
  }
}

class _IncomingCallScreenState extends State<IncomingCallScreen>
    with SingleTickerProviderStateMixin {
  // ---------- outgoing call screen jaisa hi pulsing ring animation ----------
  late final AnimationController _pulseController;
  final AudioPlayer _ringtonePlayer = AudioPlayer();
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1600),
    )..repeat();
    _startRinging();
  }

  // WhatsApp jaisa hi — ring shuru hote hi ek chhota buzz, phir ringtone
  // loop me bajta rehta hai jab tak accept/decline/dismiss na ho.
  Future<void> _startRinging() async {
    try {
      HapticFeedback.vibrate();
    } catch (_) {}
    try {
      await _ringtonePlayer.setReleaseMode(ReleaseMode.loop);
      await _ringtonePlayer.play(AssetSource('sounds/ringtone.wav'));
    } catch (_) {
      // Ringtone asset na mile ya audio init fail ho to bhi call popup
      // normally kaam karta rahe — silent fail.
    }
  }

  Future<void> _stopRinging() async {
    try {
      await _ringtonePlayer.stop();
    } catch (_) {}
  }

  @override
  void dispose() {
    _stopRinging();
    _ringtonePlayer.dispose();
    _pulseController.dispose();
    super.dispose();
  }

  Widget _initialsAvatar(String initial) => Container(
        width: 124,
        height: 124,
        color: const Color(0xFF2E4E82),
        alignment: Alignment.center,
        child: Text(
          initial,
          style: const TextStyle(fontSize: 50, color: Colors.white, fontWeight: FontWeight.w500),
        ),
      );

  Future<void> _reject() async {
    if (_busy) return;
    setState(() => _busy = true);
    _stopRinging();
    try {
      await CallApiService.callAction(widget.callId, 'reject');
    } catch (_) {
      // reject me server error bhi ho to bhi screen band ho jaani chahiye
    }
    if (mounted && Navigator.canPop(context)) Navigator.pop(context);
  }

  Future<void> _accept() async {
    if (_busy) return;
    setState(() => _busy = true);
    _stopRinging();
    try {
      final actionData = await CallApiService.callAction(widget.callId, 'accept');
      final livekitUrl = actionData['livekit_url']?.toString();
      final livekitToken = actionData['livekit_token']?.toString();
      if (livekitUrl == null || livekitToken == null) {
        throw Exception("LiveKit credentials not received from server");
      }
      if (!mounted) return;
      // pushReplacement — is screen ko back-stack me nahi rakhna, warna
      // call ke andar se back dabane par wapas "incoming call" dikh jaata.
      Navigator.of(context).pushReplacement(MaterialPageRoute(
        builder: (_) => CallScreen(
          callId: widget.callId,
          conversationId: widget.conversationId,
          isVideo: widget.callType == 'video',
          isCaller: false,
          livekitUrl: livekitUrl,
          livekitToken: livekitToken,
          peerName: widget.callerName,
          peerAvatar: widget.callerAvatar,
        ),
      ));
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Failed to accept: $e")),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final initial =
        (widget.callerName.trim().isNotEmpty ? widget.callerName[0] : "U").toUpperCase();
    final isVideo = widget.callType == 'video';

    return PopScope(
      // back gesture se incoming call silently dismiss nahi honi chahiye —
      // user ko explicitly Accept/Decline dabana hoga.
      canPop: false,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B0B0D),
        body: DecoratedBox(
          // 🔥 outgoing/active call screen (_buildAudioView) jaisa hi
          // radial gradient background — dono screens ka look consistent.
          decoration: const BoxDecoration(
            gradient: RadialGradient(
              center: Alignment(0, -0.15),
              radius: 1.1,
              colors: [Color(0xFF15243D), Color(0xFF0B0B0D)],
              stops: [0.0, 0.85],
            ),
          ),
          child: SafeArea(
            child: Column(
              children: [
                const SizedBox(height: 36),
                Text(
                  isVideo ? "Incoming video call" : "Incoming voice call",
                  style: TextStyle(
                    color: Colors.white.withOpacity(0.6),
                    fontSize: 15,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                const Spacer(),
                SizedBox(
                  width: 168,
                  height: 168,
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      AnimatedBuilder(
                        animation: _pulseController,
                        builder: (context, child) {
                          final t = _pulseController.value;
                          return Opacity(
                            opacity: (1 - t) * 0.35,
                            child: Transform.scale(
                              scale: 1 + t * 0.35,
                              child: Container(
                                width: 140,
                                height: 140,
                                decoration: const BoxDecoration(
                                  shape: BoxShape.circle,
                                  color: Color(0xFF2E7CF6),
                                ),
                              ),
                            ),
                          );
                        },
                      ),
                      Container(
                        width: 128,
                        height: 128,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          border: Border.all(color: Colors.white.withOpacity(0.12), width: 2),
                          boxShadow: [
                            BoxShadow(
                              color: Colors.black.withOpacity(0.4),
                              blurRadius: 24,
                              offset: const Offset(0, 10),
                            ),
                          ],
                        ),
                        child: ClipOval(
                          child: (widget.callerAvatar != null && widget.callerAvatar!.isNotEmpty)
                              ? CachedNetworkImage(
                                  imageUrl: widget.callerAvatar!,
                                  width: 124,
                                  height: 124,
                                  fit: BoxFit.cover,
                                  placeholder: (_, __) => _initialsAvatar(initial),
                                  errorWidget: (_, __, ___) => _initialsAvatar(initial),
                                )
                              : _initialsAvatar(initial),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 26),
                Text(
                  widget.callerName,
                  style: const TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.w600),
                  textAlign: TextAlign.center,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 10),
                Text(
                  (widget.isGroup && widget.groupTitle != null)
                      ? "${isVideo ? 'Video' : 'Voice'} call in ${widget.groupTitle}"
                      : "${isVideo ? 'Video' : 'Voice'} call",
                  style: TextStyle(color: Colors.white.withOpacity(0.55), fontSize: 16),
                ),
                const Spacer(),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 44),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      _actionButton(
                        icon: Icons.call_end_rounded,
                        color: const Color(0xFFE53E3E),
                        label: "Decline",
                        onTap: _reject,
                      ),
                      _actionButton(
                        icon: isVideo ? Icons.videocam_rounded : Icons.call_rounded,
                        color: const Color(0xFF25D366), // WhatsApp-style accept green
                        label: "Accept",
                        onTap: _accept,
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 48),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _actionButton({
    required IconData icon,
    required Color color,
    required String label,
    required VoidCallback onTap,
  }) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        GestureDetector(
          onTap: _busy ? null : onTap,
          child: Container(
            width: 68,
            height: 68,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: color,
              boxShadow: [
                BoxShadow(color: color.withOpacity(0.45), blurRadius: 18, spreadRadius: 1),
              ],
            ),
            child: _busy
                ? const Padding(
                    padding: EdgeInsets.all(20),
                    child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                  )
                : Icon(icon, color: Colors.white, size: 30),
          ),
        ),
        const SizedBox(height: 8),
        Text(label, style: TextStyle(color: Colors.white.withOpacity(0.85), fontSize: 13)),
      ],
    );
  }
}