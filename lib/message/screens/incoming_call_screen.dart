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
//       - assets/sounds/incoming_ring.wav

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
    with TickerProviderStateMixin {
  // ---------- outgoing call screen jaisa hi pulsing ring animation ----------
  late final AnimationController _pulseController;
  final AudioPlayer _ringtonePlayer = AudioPlayer();
  bool _busy = false;

  // ============================================================
  // 🔥 NAYA — SWIPE TO ACCEPT/DECLINE. Center knob ko left (decline) ya
  // right (accept) drag karo — jaise Google Phone/Pixel ka "slide to
  // answer". `_dragX` (pixels, track-center se relative) drag ke dauraan
  // live update hota hai; threshold paar hote hi haptic + action fire
  // hoti hai, warna knob spring-back ho ke center wapas aa jaata hai.
  // ============================================================
  late final AnimationController _hintController; // idle "swipe" hint pulse
  late final AnimationController _knobSnapController; // release ke baad snap/complete animation
  Animation<double>? _knobSnapAnimation;
  double _dragX = 0;
  bool _swipeSettled = false; // true jab knob edge tak pahunch chuka ho (action fire ho chuki)

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1600),
    )..repeat();
    _hintController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1100),
    )..repeat(reverse: true);
    _knobSnapController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 260),
    );
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
      await _ringtonePlayer.play(AssetSource('sounds/incoming_ring.wav'));
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
    _hintController.dispose();
    _knobSnapController.dispose();
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

  // ---------- swipe-to-answer drag handling ----------

  void _onDragUpdate(DragUpdateDetails details, double maxDrag) {
    if (_busy || _swipeSettled) return;
    setState(() {
      _dragX = (_dragX + details.delta.dx).clamp(-maxDrag, maxDrag);
    });
  }

  void _onDragEnd(DragEndDetails details, double maxDrag, double threshold) {
    if (_busy || _swipeSettled) return;
    if (_dragX <= -threshold) {
      _completeSwipe(isAccept: false, maxDrag: maxDrag);
    } else if (_dragX >= threshold) {
      _completeSwipe(isAccept: true, maxDrag: maxDrag);
    } else {
      _springBack();
    }
  }

  void _springBack() {
    _knobSnapAnimation = Tween<double>(begin: _dragX, end: 0).animate(
      CurvedAnimation(parent: _knobSnapController, curve: Curves.elasticOut),
    )..addListener(() {
        if (mounted) setState(() => _dragX = _knobSnapAnimation!.value);
      });
    _knobSnapController.forward(from: 0);
  }

  Future<void> _completeSwipe({required bool isAccept, required double maxDrag}) async {
    setState(() => _swipeSettled = true);
    try {
      HapticFeedback.mediumImpact();
    } catch (_) {}
    _knobSnapAnimation = Tween<double>(begin: _dragX, end: isAccept ? maxDrag : -maxDrag).animate(
      CurvedAnimation(parent: _knobSnapController, curve: Curves.easeOut),
    )..addListener(() {
        if (mounted) setState(() => _dragX = _knobSnapAnimation!.value);
      });
    await _knobSnapController.forward(from: 0);
    if (isAccept) {
      await _accept();
    } else {
      await _reject();
    }
    // Agar accept fail ho gaya (abhi bhi isi screen pe hain), knob ko
    // wapas center laao taaki dobara try kiya ja sake.
    if (mounted && !_busy) {
      setState(() => _swipeSettled = false);
      _springBack();
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
                  width: 176,
                  height: 176,
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      // 🔥 Do staggered pulsing rings (ek dusre se ~35% phase
                      // shifted) — pehle sirf ek ring thi, ab WhatsApp jaisa
                      // continuous "breathing" wave effect dikhta hai.
                      AnimatedBuilder(
                        animation: _pulseController,
                        builder: (context, child) {
                          final t = _pulseController.value;
                          return Opacity(
                            opacity: (1 - t) * 0.35,
                            child: Transform.scale(
                              scale: 1 + t * 0.4,
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
                      AnimatedBuilder(
                        animation: _pulseController,
                        builder: (context, child) {
                          final t = (_pulseController.value + 0.35) % 1.0;
                          return Opacity(
                            opacity: (1 - t) * 0.22,
                            child: Transform.scale(
                              scale: 1 + t * 0.55,
                              child: Container(
                                width: 140,
                                height: 140,
                                decoration: const BoxDecoration(
                                  shape: BoxShape.circle,
                                  color: Color(0xFF25D366),
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
                  padding: const EdgeInsets.symmetric(horizontal: 28),
                  child: _buildSwipeTrack(isVideo),
                ),
                const SizedBox(height: 10),
                AnimatedOpacity(
                  opacity: _swipeSettled ? 0 : 1,
                  duration: const Duration(milliseconds: 200),
                  child: Text(
                    "Slide to decline or accept",
                    style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 12.5),
                  ),
                ),
                const SizedBox(height: 40),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // ============================================================
  // SWIPE-TO-ANSWER TRACK — center knob ko drag karke Accept (right,
  // green) ya Decline (left, red) tak le jaao. Threshold paar hote hi
  // knob khud edge tak snap ho jaata hai (haptic ke saath) aur action
  // fire hoti hai; beech me chhod dene par elastic spring-back hota hai.
  // Track ke dono ends khud bhi tappable hain — jisko drag pasand na ho
  // wo seedha tap kar sakta hai, koi functionality lock nahi hoti.
  // ============================================================
  Widget _buildSwipeTrack(bool isVideo) {
    const double trackHeight = 76;
    const double knobSize = 62;
    final trackWidth = MediaQuery.of(context).size.width - 56;
    final maxDrag = (trackWidth - knobSize) / 2 - 6;
    final threshold = maxDrag * 0.6;
    // -1 (full decline) .. 0 (center) .. +1 (full accept)
    final progress = maxDrag == 0 ? 0.0 : (_dragX / maxDrag).clamp(-1.0, 1.0);
    final declineStrength = (-progress).clamp(0.0, 1.0);
    final acceptStrength = progress.clamp(0.0, 1.0);

    const declineColor = Color(0xFFE53E3E);
    const acceptColor = Color(0xFF25D366);
    final trackTint = progress < 0
        ? Color.lerp(const Color(0xFF17171F), declineColor.withOpacity(0.55), declineStrength)!
        : Color.lerp(const Color(0xFF17171F), acceptColor.withOpacity(0.55), acceptStrength)!;
    final knobColor = progress < 0
        ? Color.lerp(Colors.white, declineColor, declineStrength)!
        : Color.lerp(Colors.white, acceptColor, acceptStrength)!;
    final knobIconColor = progress.abs() > 0.08 ? Colors.white : const Color(0xFF0B0B0D);

    return GestureDetector(
      onHorizontalDragUpdate: (d) => _onDragUpdate(d, maxDrag),
      onHorizontalDragEnd: (d) => _onDragEnd(d, maxDrag, threshold),
      child: Container(
        width: trackWidth,
        height: trackHeight,
        padding: const EdgeInsets.symmetric(horizontal: 6),
        decoration: BoxDecoration(
          color: trackTint,
          borderRadius: BorderRadius.circular(trackHeight / 2),
          border: Border.all(color: Colors.white.withOpacity(0.1)),
          boxShadow: [
            BoxShadow(color: Colors.black.withOpacity(0.35), blurRadius: 20, offset: const Offset(0, 8)),
          ],
        ),
        child: Stack(
          alignment: Alignment.center,
          children: [
            // Decline label — left end, tap-fallback bhi hai.
            Align(
              alignment: Alignment.centerLeft,
              child: GestureDetector(
                onTap: _busy ? null : () => _completeSwipe(isAccept: false, maxDrag: maxDrag),
                child: Padding(
                  padding: const EdgeInsets.only(left: 14),
                  child: Opacity(
                    opacity: 0.5 + declineStrength * 0.5,
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.call_end_rounded, color: declineColor, size: 20),
                        const SizedBox(width: 6),
                        Text("Decline",
                            style: TextStyle(
                                color: declineColor, fontSize: 12.5, fontWeight: FontWeight.w600)),
                      ],
                    ),
                  ),
                ),
              ),
            ),
            // Accept label — right end, tap-fallback bhi hai.
            Align(
              alignment: Alignment.centerRight,
              child: GestureDetector(
                onTap: _busy ? null : () => _completeSwipe(isAccept: true, maxDrag: maxDrag),
                child: Padding(
                  padding: const EdgeInsets.only(right: 14),
                  child: Opacity(
                    opacity: 0.5 + acceptStrength * 0.5,
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text("Accept",
                            style: TextStyle(
                                color: acceptColor, fontSize: 12.5, fontWeight: FontWeight.w600)),
                        const SizedBox(width: 6),
                        Icon(isVideo ? Icons.videocam_rounded : Icons.call_rounded,
                            color: acceptColor, size: 20),
                      ],
                    ),
                  ),
                ),
              ),
            ),
            // Idle hint chevrons — dono taraf halka sa pulse karte hain jab
            // tak koi drag shuru na ho, taaki gesture discover ho sake.
            if (progress == 0 && !_swipeSettled)
              AnimatedBuilder(
                animation: _hintController,
                builder: (context, child) {
                  final v = _hintController.value;
                  return Opacity(
                    opacity: 0.15 + v * 0.2,
                    child: child,
                  );
                },
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: const [
                    Icon(Icons.chevron_left_rounded, color: Colors.white, size: 18),
                    SizedBox(width: 26),
                    Icon(Icons.chevron_right_rounded, color: Colors.white, size: 18),
                  ],
                ),
              ),
            // Draggable knob — poore track pe left/right transform hota hai.
            Transform.translate(
              offset: Offset(_dragX, 0),
              child: Container(
                width: knobSize,
                height: knobSize,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: knobColor,
                  boxShadow: [
                    BoxShadow(
                      color: (progress < 0 ? declineColor : acceptColor)
                          .withOpacity(0.25 + progress.abs() * 0.35),
                      blurRadius: 16,
                      spreadRadius: 1,
                    ),
                  ],
                ),
                alignment: Alignment.center,
                child: _busy
                    ? SizedBox(
                        width: 22,
                        height: 22,
                        child: CircularProgressIndicator(strokeWidth: 2, color: knobIconColor),
                      )
                    : Transform.rotate(
                        angle: progress * -0.5,
                        child: Icon(
                          progress < -0.2
                              ? Icons.call_end_rounded
                              : (isVideo ? Icons.videocam_rounded : Icons.call_rounded),
                          color: knobIconColor,
                          size: 26,
                        ),
                      ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}