import 'dart:async';
import 'dart:ui'; // 🔥 NAYA — local video effects (blur) ke liye
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:livekit_client/livekit_client.dart';
import 'package:permission_handler/permission_handler.dart';
import '../services/call_manager.dart';
import '../services/call_api_service.dart'; // 🔥 NAYA — call waiting accept ke liye

class CallScreen extends StatefulWidget {
  final String callId;
  final String conversationId;
  final bool isVideo;
  final bool isCaller;
  final String livekitUrl;
  final String livekitToken;
  final String? peerName;
  final String? peerAvatar;

  const CallScreen({
    super.key,
    required this.callId,
    required this.conversationId,
    required this.isVideo,
    required this.isCaller,
    required this.livekitUrl,
    required this.livekitToken,
    this.peerName,
    this.peerAvatar,
  });

  @override
  State<CallScreen> createState() => _CallScreenState();
}

class _CallScreenState extends State<CallScreen> with SingleTickerProviderStateMixin {
  final CallManager _cm = CallManager.instance;

  // ---------- Floating / PiP video (purely visual, isliye screen-level
  // State me rehta hai, CallManager me nahi) ----------
  Offset? _pipOffset;
  bool _mainIsLocal = false; // false => remote fullscreen, local floats

  // ---------- WhatsApp-style pulsing ring around avatar while
  // connecting/ringing (band ho jaata hai jaise hi call connect hoti hai) ----------
  late final AnimationController _pulseController;

  // 🔥 NAYA — control bar (mute/speaker/etc icons) 3 second baad apne aap
  // fade ho jaata hai; screen pe kahin bhi tap karne se wapas dikh jaata
  // hai aur timer reset ho jaata hai.
  bool _controlsVisible = true;
  Timer? _hideControlsTimer;

  void _resetHideControlsTimer() {
    _hideControlsTimer?.cancel();
    if (!_controlsVisible) {
      setState(() => _controlsVisible = true);
    }
    _hideControlsTimer = Timer(const Duration(seconds: 3), () {
      if (mounted) setState(() => _controlsVisible = false);
    });
  }

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1600),
    )..repeat();

    _resetHideControlsTimer();

    _cm.addListener(_onManagerChanged);
    // Agar ye call already CallManager me chal rahi hai (floating bar se
    // wapas khola gaya), to dobara connect nahi hoga — bas UI attach ho
    // jayega. Nayi call ke liye normal tarike se connect karega.
    _cm.startCallIfNeeded(
      callId: widget.callId,
      conversationId: widget.conversationId,
      isVideo: widget.isVideo,
      isCaller: widget.isCaller,
      livekitUrl: widget.livekitUrl,
      livekitToken: widget.livekitToken,
      peerName: widget.peerName,
      peerAvatar: widget.peerAvatar,
    );
  }

  void _onManagerChanged() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _cm.removeListener(_onManagerChanged);
    _pulseController.dispose();
    _hideControlsTimer?.cancel();
    super.dispose();
  }

  String _formatDuration(Duration d) {
    String twoDigits(int n) => n.toString().padLeft(2, '0');
    final h = d.inHours;
    final m = twoDigits(d.inMinutes.remainder(60));
    final s = twoDigits(d.inSeconds.remainder(60));
    return h > 0 ? "$h:$m:$s" : "${d.inMinutes}:$s";
  }

  /// Back gesture / minimize button dabane pe call KHATAM nahi hoti —
  /// bas UI hat jaata hai, floating bar dikhne lagta hai.
  void _minimizeAndPop() {
    _cm.minimize();
    if (Navigator.canPop(context)) Navigator.pop(context);
  }

  Future<void> _endCall() async {
    await _cm.endCall();
    if (mounted && Navigator.canPop(context)) Navigator.pop(context);
  }

  // 🔥 NAYA — GROUP CALL: participant-picker bottom sheet kholo. Chalti
  // hui call ke conversation ke un members ki list backend se maangte
  // hain jo abhi call me nahi hain (`addable-participants`), aur tap
  // karne par unhein invite kar dete hain.
  void _showAddParticipantSheet() {
    final callId = _cm.callId;
    if (callId == null) return;
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF15243D),
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => _AddParticipantSheet(
        callId: callId,
        onAdd: (userId) async {
          final ok = await _cm.addParticipant(userId);
          if (!ok && mounted && _cm.addParticipantError != null) {
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text(_cm.addParticipantError!)),
            );
          }
          return ok;
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    // Call kisi wajah se (dusri jagah se) khatam ho chuki ho to screen
    // khud band ho jaaye.
    if (!_cm.isActive) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && Navigator.canPop(context)) Navigator.pop(context);
      });
    }

    final hasFullscreenVideo = (_cm.isVideo && !_cm.videoOff && _cm.remoteConnected) ||
        _cm.remoteScreenTrack != null ||
        _cm.localScreenTrack != null;

    return PopScope(
      canPop: true,
      onPopInvokedWithResult: (didPop, _) {
        if (didPop) _cm.minimize();
      },
      child: Scaffold(
        backgroundColor: const Color(0xFF0B0B0D),
        body: GestureDetector(
          behavior: HitTestBehavior.translucent,
          onTap: _resetHideControlsTimer,
          child: Stack(
          fit: StackFit.expand,
          children: [
            // ---- Background: video fullscreen ya audio-call gradient ----
            // 🔥 NAYA — 2+ remote participants ho to WhatsApp jaisa grid,
            // warna purana 1-on-1 fullscreen/PiP view (bina toote hue).
            _cm.isGroupCall ? _buildGroupGrid() : _buildVideoStack(),

            // Audio calls ke liye subtle vignette taaki text upar/niche
            // hamesha readable rahe, video call me bhi thoda so darkening.
            IgnorePointer(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      Colors.black.withOpacity(hasFullscreenVideo ? 0.45 : 0.0),
                      Colors.transparent,
                      Colors.transparent,
                      Colors.black.withOpacity(hasFullscreenVideo ? 0.55 : 0.35),
                    ],
                    stops: const [0.0, 0.25, 0.6, 1.0],
                  ),
                ),
              ),
            ),

            SafeArea(
              child: Column(
                children: [
                  _buildTopBar(),
                  if (_cm.peerOnHold) _buildHoldBanner(),
                  if (_cm.waitingCallId != null) _buildCallWaitingBanner(),
                  const Spacer(),
                  _buildControlBar(),
                  const SizedBox(height: 8),
                ],
              ),
            ),
          ],
        ),
        ),
      ),
    );
  }

  // ============================================================
  // Top bar — peer name + call status pill (WhatsApp-style: naam bada,
  // status chhota aur muted, error/reconnect hi highlight hota hai)
  // ============================================================
  Widget _buildTopBar() {
    final isError = _cm.error != null;
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 8, 16, 0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          IconButton(
            icon: const Icon(Icons.keyboard_arrow_down, color: Colors.white, size: 30),
            onPressed: _minimizeAndPop,
            tooltip: "Minimize",
          ),
          const SizedBox(width: 4),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _cm.isGroupCall
                      ? "${_cm.peerName ?? 'User'} +${_cm.remoteTiles.length - 1}"
                      : (_cm.peerName ?? "User"),
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 21,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.1,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 3),
                AnimatedSwitcher(
                  duration: const Duration(milliseconds: 250),
                  child: Row(
                    key: ValueKey(_cm.error ?? _cm.status),
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Flexible(
                        child: Text(
                          isError
                              ? _cm.error!
                              : (_cm.onHold
                                  ? "On hold"
                                  : (_cm.remoteConnected
                                      ? _formatDuration(_cm.connectedDuration)
                                      // 🔥 FIX — reconnecting UI hidden: agar
                                      // status "Reconnecting..." ho to bhi
                                      // user ko generic "Connecting..." dikhe.
                                      : (_cm.isReconnecting ? "Connecting..." : _cm.status))),
                          style: TextStyle(
                            color: isError
                                ? Colors.redAccent.shade100
                                : (_cm.onHold ? Colors.orangeAccent : Colors.white.withOpacity(0.75)),
                            fontSize: 14,
                            fontWeight: FontWeight.w400,
                          ),
                        ),
                      ),
                      if (_cm.needsSettingsRedirect) ...[
                        const SizedBox(width: 8),
                        GestureDetector(
                          onTap: openAppSettings,
                          child: const Text(
                            "Open settings",
                            style: TextStyle(
                              color: Colors.orangeAccent,
                              fontSize: 13,
                              decoration: TextDecoration.underline,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
          if (_cm.isScreenSharing)
            Container(
              margin: const EdgeInsets.only(top: 6),
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
              decoration: BoxDecoration(
                color: Colors.blueAccent.withOpacity(0.85),
                borderRadius: BorderRadius.circular(20),
              ),
              child: const Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.screen_share, color: Colors.white, size: 14),
                  SizedBox(width: 5),
                  Text("Sharing", style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w500)),
                ],
              ),
            ),
          // 🔥 NAYA — GROUP CALL: naya banda add karo (WhatsApp jaisa +
          // icon). Hold pe hote hue bhi allowed hai — add karna apne
          // audio/video se kuch lena-dena nahi rakhta.
          IconButton(
            icon: const Icon(Icons.person_add_alt_1_rounded, color: Colors.white, size: 22),
            onPressed: _showAddParticipantSheet,
            tooltip: "Add participant",
          ),
        ],
      ),
    );
  }

  // ============================================================
  // WhatsApp-style bottom control bar — circular buttons with small
  // labels underneath, end-call button bigger & centered on its own row.
  // ============================================================
  // 🔥 NAYA — jab DUSRI taraf ne call hold pe daali ho.
  Widget _buildHoldBanner() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 14),
        decoration: BoxDecoration(
          color: Colors.orangeAccent.withOpacity(0.15),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: Colors.orangeAccent.withOpacity(0.4)),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.pause_circle_filled_rounded, color: Colors.orangeAccent, size: 18),
            const SizedBox(width: 8),
            Text(
              "${_cm.peerName ?? 'They'} put you on hold",
              style: const TextStyle(color: Colors.orangeAccent, fontSize: 13, fontWeight: FontWeight.w500),
            ),
          ],
        ),
      ),
    );
  }

  // 🔥 NAYA — CALL WAITING: doosri incoming call ka chhota banner (poori
  // screen le lene wali IncomingCallScreen nahi). Accept karne par purani
  // call end hoke nayi start hoti hai — isliye label me "End & Accept"
  // saaf-saaf likha hai, koi confusion nahi.
  bool _waitingCallBusy = false;

  Future<void> _declineWaitingCall() async {
    if (_waitingCallBusy) return;
    setState(() => _waitingCallBusy = true);
    await _cm.declineWaitingCall();
    if (mounted) setState(() => _waitingCallBusy = false);
  }

  Future<void> _acceptWaitingCall() async {
    if (_waitingCallBusy) return;
    setState(() => _waitingCallBusy = true);

    final waitingId = _cm.waitingCallId;
    final waitingName = _cm.waitingCallerName;
    final waitingType = _cm.waitingCallType ?? 'audio';
    final waitingConvId = _cm.waitingConversationId ?? '';
    final waitingAvatar = _cm.waitingCallerAvatar;
    if (waitingId == null) {
      setState(() => _waitingCallBusy = false);
      return;
    }

    try {
      final actionData = await CallApiService.callAction(waitingId, 'accept');
      final livekitUrl = actionData['livekit_url']?.toString();
      final livekitToken = actionData['livekit_token']?.toString();
      if (livekitUrl == null || livekitToken == null) {
        throw Exception("LiveKit credentials not received from server");
      }

      _cm.clearWaitingCall();
      // Purani call khatam karo — 2 calls ek saath (true hold-and-switch)
      // is version me support nahi hai.
      await _cm.endCall();

      if (!mounted) return;
      Navigator.of(context).pushReplacement(MaterialPageRoute(
        builder: (_) => CallScreen(
          callId: waitingId,
          conversationId: waitingConvId,
          isVideo: waitingType == 'video',
          isCaller: false,
          livekitUrl: livekitUrl,
          livekitToken: livekitToken,
          peerName: waitingName,
          peerAvatar: waitingAvatar,
        ),
      ));
    } catch (e) {
      if (!mounted) return;
      setState(() => _waitingCallBusy = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Failed to accept: $e")),
      );
    }
  }

  Widget _buildCallWaitingBanner() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 14),
        decoration: BoxDecoration(
          color: Colors.white.withOpacity(0.08),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: Colors.white.withOpacity(0.15)),
        ),
        child: Row(
          children: [
            const Icon(Icons.phone_in_talk_rounded, color: Colors.white70, size: 20),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    _cm.waitingCallerName ?? "Someone",
                    style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w600),
                    overflow: TextOverflow.ellipsis,
                  ),
                  const Text(
                    "is calling — accept karne par current call end hogi",
                    style: TextStyle(color: Colors.white54, fontSize: 11.5),
                  ),
                ],
              ),
            ),
            if (_waitingCallBusy)
              const Padding(
                padding: EdgeInsets.all(8),
                child: SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                ),
              )
            else ...[
              GestureDetector(
                onTap: _declineWaitingCall,
                child: Container(
                  margin: const EdgeInsets.only(left: 6),
                  padding: const EdgeInsets.all(8),
                  decoration: const BoxDecoration(shape: BoxShape.circle, color: Color(0xFFE53E3E)),
                  child: const Icon(Icons.call_end_rounded, color: Colors.white, size: 18),
                ),
              ),
              GestureDetector(
                onTap: _acceptWaitingCall,
                child: Container(
                  margin: const EdgeInsets.only(left: 6),
                  padding: const EdgeInsets.all(8),
                  decoration: const BoxDecoration(shape: BoxShape.circle, color: Color(0xFF25D366)),
                  child: const Icon(Icons.call_rounded, color: Colors.white, size: 18),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildControlBar() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // 🔥 NAYA — mute/speaker/etc icons ka block 3 sec inactivity ke
          // baad fade ho jaata hai; screen pe tap karte hi wapas aa jaata
          // hai (dekho _resetHideControlsTimer).
          AnimatedOpacity(
            duration: const Duration(milliseconds: 250),
            opacity: _controlsVisible ? 1.0 : 0.0,
            child: IgnorePointer(
              ignoring: !_controlsVisible,
              child: Container(
                padding: const EdgeInsets.symmetric(vertical: 18, horizontal: 10),
                decoration: BoxDecoration(
                  color: Colors.black.withOpacity(0.45),
                  borderRadius: BorderRadius.circular(28),
                  border: Border.all(color: Colors.white.withOpacity(0.08)),
                ),
                child: Wrap(
                  alignment: WrapAlignment.spaceEvenly,
                  runSpacing: 14,
                  spacing: 4,
                  children: [
                    _ctrl(
                      icon: _cm.muted ? Icons.mic_off_rounded : Icons.mic_rounded,
                      label: "Mute",
                      active: _cm.muted,
                      onTap: _cm.onHold ? null : _cm.toggleMic,
                    ),
                    if (_cm.isVideo)
                      _ctrl(
                        icon: _cm.videoOff ? Icons.videocam_off_rounded : Icons.videocam_rounded,
                        label: "Video",
                        active: _cm.videoOff,
                        onTap: _cm.onHold ? null : _cm.toggleCamera,
                      ),
                    if (_cm.isVideo && !_cm.videoOff)
                      _ctrl(
                        icon: Icons.cameraswitch_rounded,
                        label: "Flip",
                        active: false,
                        onTap: _cm.onHold ? null : _cm.switchCamera,
                      ),
                    // 🔥 NAYA — filters + soft-blur bottom sheet (local
                    // preview only).
                    if (_cm.isVideo)
                      _ctrl(
                        icon: Icons.auto_awesome_rounded,
                        label: "Effects",
                        active: _cm.localFilter != VideoFilterType.none || _cm.localSoftBlur,
                        activeColor: Colors.pinkAccent,
                        onTap: _showEffectsSheet,
                      ),
                    _ctrl(
                      icon: _cm.speakerOn ? Icons.volume_up_rounded : Icons.hearing_rounded,
                      label: "Speaker",
                      active: _cm.speakerOn,
                      activeColor: Colors.white24,
                      onTap: _cm.toggleSpeaker,
                    ),
                    _ctrl(
                      icon: _cm.isScreenSharing ? Icons.stop_screen_share_rounded : Icons.screen_share_rounded,
                      label: "Share",
                      active: _cm.isScreenSharing,
                      activeColor: Colors.blueAccent,
                      onTap: _cm.onHold ? null : _cm.toggleScreenShare,
                    ),
                    // Hold/Resume — icon + label dono state ke saath badalte
                    // hain, jaise WhatsApp/phone dialer me hota hai.
                    _ctrl(
                      icon: _cm.onHold ? Icons.play_circle_fill_rounded : Icons.pause_circle_filled_rounded,
                      label: _cm.onHold ? "Resume" : "Hold",
                      active: _cm.onHold,
                      activeColor: Colors.orangeAccent,
                      onTap: _cm.toggleHold,
                    ),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(height: 22),
          GestureDetector(
            onTap: _endCall,
            child: Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: const Color(0xFFE53E3E),
                boxShadow: [
                  BoxShadow(
                    color: const Color(0xFFE53E3E).withOpacity(0.45),
                    blurRadius: 18,
                    spreadRadius: 1,
                  ),
                ],
              ),
              child: const Icon(Icons.call_end_rounded, color: Colors.white, size: 28),
            ),
          ),
        ],
      ),
    );
  }

  Widget _ctrl({
    required IconData icon,
    required String label,
    required VoidCallback? onTap,
    bool active = false,
    Color activeColor = Colors.white,
  }) {
    final disabled = onTap == null;
    final bg = active ? activeColor : Colors.white.withOpacity(0.14);
    final fg = active && activeColor == Colors.white ? Colors.black87 : Colors.white;
    return Opacity(
      opacity: disabled ? 0.4 : 1.0,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Material(
            color: bg,
            shape: const CircleBorder(),
            child: InkWell(
              customBorder: const CircleBorder(),
              onTap: onTap,
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Icon(icon, color: fg, size: 22),
              ),
            ),
          ),
          const SizedBox(height: 6),
          Text(label, style: TextStyle(color: Colors.white.withOpacity(0.85), fontSize: 11.5)),
        ],
      ),
    );
  }

  // ============================================================
  // 🔥 NAYA — GROUP CALL GRID: WhatsApp jaisa hi — 2 log ho to 2 tiles
  // pane, 3-4 ho to 2x2, usse zyada ho to scroll-able grid. Apna local
  // tile bhi isi grid me ek chhoti tile ki tarah shaamil hota hai
  // (bottom-most, taaki baaki sab peers upar dikhein).
  // ============================================================
  Widget _buildGroupGrid() {
    final participants = _cm.remoteParticipantsList;
    final showLocalVideo = _cm.isVideo && !_cm.videoOff;
    final crossAxisCount = (participants.length + 1) <= 4 ? 2 : 3;

    return DecoratedBox(
      decoration: const BoxDecoration(color: Color(0xFF0B0B0D)),
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(6, 90, 6, 130),
          child: GridView.builder(
            padding: EdgeInsets.zero,
            physics: const NeverScrollableScrollPhysics(),
            itemCount: participants.length + 1, // +1 = apna local tile
            gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: crossAxisCount,
              mainAxisSpacing: 6,
              crossAxisSpacing: 6,
              childAspectRatio: 0.78,
            ),
            itemBuilder: (context, index) {
              if (index == participants.length) {
                // Apna khud ka tile — hamesha last.
                return _groupTile(
                  name: "You",
                  videoTrack: showLocalVideo ? _cm.localVideoTrack : null,
                  isMuted: _cm.muted,
                  isLocal: true,
                );
              }
              final p = participants[index];
              final track = _cm.remoteTileVideoTracks[p.identity];
              return _groupTile(
                name: p.name.isNotEmpty ? p.name : "User",
                videoTrack: track,
                isMuted: _isParticipantMuted(p),
                isLocal: false,
              );
            },
          ),
        ),
      ),
    );
  }

  // Publication-level `.muted` check — same source of truth
  // call_manager.dart already reads from for the hold feature, so this
  // stays consistent with the rest of the codebase instead of guessing
  // at a different Participant-level API.
  bool _isParticipantMuted(RemoteParticipant p) {
    if (p.audioTrackPublications.isEmpty) return true;
    return p.audioTrackPublications.every((pub) => pub.muted);
  }

  Widget _groupTile({
    required String name,
    VideoTrack? videoTrack,
    bool isMuted = false,
    bool isLocal = false,
  }) {
    final initial = (name.trim().isNotEmpty ? name.trim()[0] : "U").toUpperCase();
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        color: const Color(0xFF15243D),
        border: Border.all(color: Colors.white.withOpacity(0.08)),
      ),
      clipBehavior: Clip.antiAlias,
      child: Stack(
        fit: StackFit.expand,
        children: [
          if (videoTrack != null)
            _renderVideo(videoTrack)
          else
            Center(
              child: Container(
                width: 64,
                height: 64,
                decoration: const BoxDecoration(shape: BoxShape.circle, color: Color(0xFF2E4E82)),
                alignment: Alignment.center,
                child: Text(initial, style: const TextStyle(fontSize: 26, color: Colors.white, fontWeight: FontWeight.w500)),
              ),
            ),
          Positioned(
            left: 8,
            bottom: 8,
            right: 8,
            child: Row(
              children: [
                if (isMuted)
                  Container(
                    margin: const EdgeInsets.only(right: 6),
                    padding: const EdgeInsets.all(4),
                    decoration: const BoxDecoration(shape: BoxShape.circle, color: Colors.black54),
                    child: const Icon(Icons.mic_off_rounded, color: Colors.white, size: 12),
                  ),
                Flexible(
                  child: Text(
                    isLocal ? "You" : name,
                    style: const TextStyle(color: Colors.white, fontSize: 12.5, fontWeight: FontWeight.w500, shadows: [
                      Shadow(color: Colors.black87, blurRadius: 4),
                    ]),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  // ============================================================
  // Floating / draggable / swappable video (chota-bada dono)
  // ============================================================
  Widget _buildVideoStack() {
    final showVideo = _cm.isVideo && !_cm.videoOff;
    final screenSize = MediaQuery.of(context).size;
    _pipOffset ??= Offset(screenSize.width - 130, 100);

    final activeScreenTrack = _cm.remoteScreenTrack ?? _cm.localScreenTrack;
    VideoTrack? mainTrack;
    VideoTrack? pipTrack;

    if (activeScreenTrack != null) {
      mainTrack = activeScreenTrack;
      pipTrack = _mainIsLocal
          ? (showVideo ? _cm.localVideoTrack : null)
          : (_cm.remoteConnected ? _cm.remoteVideoTrack : null);
    } else {
      mainTrack = _mainIsLocal ? (showVideo ? _cm.localVideoTrack : null) : (_cm.remoteConnected ? _cm.remoteVideoTrack : null);
      pipTrack = _mainIsLocal ? (_cm.remoteConnected ? _cm.remoteVideoTrack : null) : (showVideo ? _cm.localVideoTrack : null);
    }

    return Stack(
      children: [
        Positioned.fill(
          child: mainTrack != null
              ? _renderVideo(mainTrack)
              : _buildAudioView(),
        ),
        if (pipTrack != null)
          AnimatedPositioned(
            duration: const Duration(milliseconds: 120),
            curve: Curves.easeOut,
            left: _pipOffset!.dx,
            top: _pipOffset!.dy,
            child: GestureDetector(
              onPanUpdate: (details) {
                setState(() {
                  final newDx = (_pipOffset!.dx + details.delta.dx).clamp(0.0, screenSize.width - 112);
                  final newDy = (_pipOffset!.dy + details.delta.dy).clamp(0.0, screenSize.height - 170);
                  _pipOffset = Offset(newDx, newDy);
                });
              },
              onTap: () => setState(() => _mainIsLocal = !_mainIsLocal), // tap => swap big/small
              child: Container(
                width: 108,
                height: 156,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: Colors.white.withOpacity(0.25), width: 1.5),
                  color: Colors.black,
                  boxShadow: [
                    BoxShadow(
                      color: Colors.black.withOpacity(0.5),
                      blurRadius: 14,
                      offset: const Offset(0, 6),
                    ),
                  ],
                ),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(16.5),
                  child: _renderVideo(pipTrack),
                ),
              ),
            ),
          ),
      ],
    );
  }

  // 🔥 FIX — pehle default fit "contain" tha, jisse portrait camera video
  // apne container (PiP box / group tile) ki poori WIDTH cover nahi karta
  // tha (dono taraf black bars aa jaate the — video ki width container se
  // kam dikhti thi). "cover" fit video ko crop karke poora container
  // (width + height dono) bhar deta hai, jaise WhatsApp/Instagram calls me.
  //
  // 🔥 NAYA — agar ye LOCAL preview hai to filter/soft-blur bhi yahin
  // apply hote hain (sirf render pe — jo peer ko jaata hai wo unaffected
  // rehta hai, upar CallManager.localFilter/localSoftBlur comment dekho).
  Widget _renderVideo(VideoTrack track) {
    Widget renderer = VideoTrackRenderer(track, fit: VideoViewFit.cover);
    final isLocalPreview = identical(track, _cm.localVideoTrack);
    if (isLocalPreview) {
      if (_cm.localSoftBlur) {
        renderer = ImageFiltered(
          imageFilter: ImageFilter.blur(sigmaX: 14, sigmaY: 14, tileMode: TileMode.decal),
          child: renderer,
        );
      }
      final matrix = _filterMatrix(_cm.localFilter);
      if (matrix != null) {
        renderer = ColorFiltered(
          colorFilter: ColorFilter.matrix(matrix),
          child: renderer,
        );
      }
    }
    return renderer;
  }

  String _filterLabel(VideoFilterType f) {
    switch (f) {
      case VideoFilterType.none:
        return "None";
      case VideoFilterType.blackAndWhite:
        return "B&W";
      case VideoFilterType.warm:
        return "Warm";
      case VideoFilterType.cool:
        return "Cool";
      case VideoFilterType.vintage:
        return "Vintage";
      case VideoFilterType.sepia:
        return "Sepia";
    }
  }

  // 4x5 color matrices for ColorFilter.matrix — null = no filter.
  List<double>? _filterMatrix(VideoFilterType f) {
    switch (f) {
      case VideoFilterType.none:
        return null;
      case VideoFilterType.blackAndWhite:
        return const [
          0.2126, 0.7152, 0.0722, 0, 0,
          0.2126, 0.7152, 0.0722, 0, 0,
          0.2126, 0.7152, 0.0722, 0, 0,
          0, 0, 0, 1, 0,
        ];
      case VideoFilterType.warm:
        return const [
          1.15, 0, 0, 0, 8,
          0, 1.05, 0, 0, 4,
          0, 0, 0.85, 0, -8,
          0, 0, 0, 1, 0,
        ];
      case VideoFilterType.cool:
        return const [
          0.85, 0, 0, 0, -6,
          0, 1.0, 0, 0, 0,
          0, 0, 1.2, 0, 12,
          0, 0, 0, 1, 0,
        ];
      case VideoFilterType.vintage:
        return const [
          0.9, 0.5, 0.1, 0, 10,
          0.3, 0.8, 0.1, 0, 10,
          0.2, 0.3, 0.5, 0, 10,
          0, 0, 0, 1, 0,
        ];
      case VideoFilterType.sepia:
        return const [
          0.393, 0.769, 0.189, 0, 0,
          0.349, 0.686, 0.168, 0, 0,
          0.272, 0.534, 0.131, 0, 0,
          0, 0, 0, 1, 0,
        ];
    }
  }

  // 🔥 NAYA — filters + soft-blur choose karne ke liye bottom sheet.
  // AnimatedBuilder se wrap kiya hai taaki CallManager.notifyListeners()
  // par sheet turant update ho (selected chip highlight ho jaaye).
  void _showEffectsSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF15243D),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => AnimatedBuilder(
        animation: _cm,
        builder: (context, __) => SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 14, 20, 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Center(
                  child: Container(
                    width: 36,
                    height: 4,
                    margin: const EdgeInsets.only(bottom: 16),
                    decoration: BoxDecoration(
                      color: Colors.white24,
                      borderRadius: BorderRadius.circular(4),
                    ),
                  ),
                ),
                const Text(
                  "Video effects",
                  style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 4),
                const Text(
                  "Sirf aapki screen pe dikhega — doosri taraf normal video hi jaayega.",
                  style: TextStyle(color: Colors.white54, fontSize: 12.5),
                ),
                const SizedBox(height: 16),
                Row(
                  children: [
                    const Icon(Icons.blur_on_rounded, color: Colors.white70, size: 20),
                    const SizedBox(width: 10),
                    const Expanded(
                      child: Text("Soft blur", style: TextStyle(color: Colors.white, fontSize: 14.5)),
                    ),
                    Switch(
                      value: _cm.localSoftBlur,
                      activeColor: Colors.pinkAccent,
                      onChanged: (_) => _cm.toggleLocalSoftBlur(),
                    ),
                  ],
                ),
                const SizedBox(height: 14),
                const Text(
                  "Filter",
                  style: TextStyle(color: Colors.white70, fontSize: 13, fontWeight: FontWeight.w500),
                ),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 10,
                  runSpacing: 10,
                  children: VideoFilterType.values.map((f) {
                    final selected = _cm.localFilter == f;
                    return GestureDetector(
                      onTap: () => _cm.setLocalFilter(f),
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        decoration: BoxDecoration(
                          color: selected ? Colors.pinkAccent.withOpacity(0.9) : Colors.white.withOpacity(0.08),
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(
                            color: selected ? Colors.pinkAccent : Colors.white.withOpacity(0.12),
                          ),
                        ),
                        child: Text(
                          _filterLabel(f),
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 13,
                            fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                          ),
                        ),
                      ),
                    );
                  }).toList(),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // ============================================================
  // Audio-only call view — WhatsApp-style large avatar with a soft
  // pulsing ring while ringing/connecting, radial background glow.
  // ============================================================
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

  Widget _buildAudioView() {
    final initial = (_cm.peerName?.trim().isNotEmpty == true ? _cm.peerName![0] : "U").toUpperCase();
    final isRinging = !_cm.remoteConnected && !_cm.isReconnecting;

    return DecoratedBox(
      decoration: const BoxDecoration(
        gradient: RadialGradient(
          center: Alignment(0, -0.15),
          radius: 1.1,
          colors: [Color(0xFF15243D), Color(0xFF0B0B0D)],
          stops: [0.0, 0.85],
        ),
      ),
      child: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            SizedBox(
              width: 168,
              height: 168,
              child: Stack(
                alignment: Alignment.center,
                children: [
                  if (isRinging)
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
                        BoxShadow(color: Colors.black.withOpacity(0.4), blurRadius: 24, offset: const Offset(0, 10)),
                      ],
                    ),
                    child: ClipOval(
                      child: (_cm.peerAvatar != null && _cm.peerAvatar!.isNotEmpty)
                          ? CachedNetworkImage(
                              imageUrl: _cm.peerAvatar!,
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
              _cm.peerName ?? "Calling...",
              style: const TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 10),
            if (_cm.remoteConnected)
              Text(
                _formatDuration(_cm.connectedDuration),
                style: TextStyle(color: Colors.white.withOpacity(0.7), fontSize: 16),
              )
            else
              Text(
                // 🔥 FIX — reconnecting UI hidden: "Reconnecting... Xs" ki
                // jagah generic status dikhao.
                _cm.isReconnecting ? "Connecting..." : _cm.status,
                style: TextStyle(color: Colors.white.withOpacity(0.55), fontSize: 16),
              ),
          ],
        ),
      ),
    );
  }
}

// ============================================================
// 🔥 NAYA — GROUP CALL: "Add participant" bottom sheet. Conversation ke
// un members ki list dikhata hai jo abhi call me nahi hain, tap karne
// par unhein invite karta hai (spinner -> green check on success).
// ============================================================
class _AddParticipantSheet extends StatefulWidget {
  final String callId;
  final Future<bool> Function(String userId) onAdd;

  const _AddParticipantSheet({required this.callId, required this.onAdd});

  @override
  State<_AddParticipantSheet> createState() => _AddParticipantSheetState();
}

class _AddParticipantSheetState extends State<_AddParticipantSheet> {
  List<Map<String, dynamic>> _members = [];
  bool _loading = true;
  String? _error;
  final Set<String> _addingIds = {};
  final Set<String> _addedIds = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final list = await CallApiService.getAddableParticipants(widget.callId);
      if (mounted) setState(() { _members = list; _loading = false; });
    } catch (e) {
      if (mounted) setState(() { _error = "$e"; _loading = false; });
    }
  }

  Future<void> _handleAdd(String userId) async {
    setState(() => _addingIds.add(userId));
    final ok = await widget.onAdd(userId);
    if (!mounted) return;
    setState(() {
      _addingIds.remove(userId);
      if (ok) _addedIds.add(userId);
    });
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 14, 20, 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 36,
                height: 4,
                margin: const EdgeInsets.only(bottom: 16),
                decoration: BoxDecoration(
                  color: Colors.white24,
                  borderRadius: BorderRadius.circular(4),
                ),
              ),
            ),
            const Text(
              "Add participant",
              style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 14),
            if (_loading)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 30),
                child: Center(child: CircularProgressIndicator(color: Colors.white70)),
              )
            else if (_error != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 16),
                child: Text("Couldn't load members: $_error",
                    style: const TextStyle(color: Colors.redAccent, fontSize: 13)),
              )
            else if (_members.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 20),
                child: Text(
                  "Everyone in this chat is already on the call",
                  style: TextStyle(color: Colors.white54, fontSize: 13.5),
                ),
              )
            else
              Flexible(
                child: ListView.separated(
                  shrinkWrap: true,
                  itemCount: _members.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 2),
                  itemBuilder: (_, i) {
                    final m = _members[i];
                    final id = (m['id'] ?? '').toString();
                    final name = (m['display_name'] ?? 'User').toString();
                    final avatar = m['avatar']?.toString();
                    final isAdding = _addingIds.contains(id);
                    final isAdded = _addedIds.contains(id);
                    return ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: CircleAvatar(
                        radius: 22,
                        backgroundColor: const Color(0xFF2E4E82),
                        backgroundImage: (avatar != null && avatar.isNotEmpty)
                            ? CachedNetworkImageProvider(avatar)
                            : null,
                        child: (avatar == null || avatar.isEmpty)
                            ? Text(
                                name.isNotEmpty ? name[0].toUpperCase() : "U",
                                style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w500),
                              )
                            : null,
                      ),
                      title: Text(name, style: const TextStyle(color: Colors.white, fontSize: 15)),
                      trailing: isAdding
                          ? const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white70),
                            )
                          : isAdded
                              ? const Icon(Icons.check_circle, color: Color(0xFF25D366))
                              : IconButton(
                                  icon: const Icon(Icons.add_circle_outline, color: Colors.white70),
                                  onPressed: () => _handleAdd(id),
                                ),
                    );
                  },
                ),
              ),
          ],
        ),
      ),
    );
  }
}