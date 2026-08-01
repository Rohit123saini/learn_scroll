// lib/message/widgets/floating_call_bar.dart
//
// WhatsApp/Instagram jaisa chhota call-bar — jab call minimize ho (CallScreen
// band ho par call chal rahi ho), ye poore app me har screen ke upar dikhta
// hai. Tap karne se wapas CallScreen khul jaata hai. Call end hote hi
// (CallManager.isActive == false) ye khud gayab ho jaata hai.
//
// main.dart me MaterialApp ke `builder:` me isko Stack se wrap karke lagao.

import 'package:flutter/material.dart';
import '../services/call_manager.dart';
import '../screens/call_screen.dart';

class FloatingCallBar extends StatefulWidget {
  final GlobalKey<NavigatorState> navigatorKey;
  const FloatingCallBar({super.key, required this.navigatorKey});

  @override
  State<FloatingCallBar> createState() => _FloatingCallBarState();
}

class _FloatingCallBarState extends State<FloatingCallBar> {
  final CallManager _cm = CallManager.instance;

  @override
  void initState() {
    super.initState();
    _cm.addListener(_onChanged);
  }

  @override
  void dispose() {
    _cm.removeListener(_onChanged);
    super.dispose();
  }

  void _onChanged() {
    if (mounted) setState(() {});
  }

  String _formatDuration(Duration d) {
    String two(int n) => n.toString().padLeft(2, '0');
    return "${two(d.inMinutes)}:${two(d.inSeconds.remainder(60))}";
  }

  void _reopenCallScreen() {
    _cm.unminimize();
    widget.navigatorKey.currentState?.push(
      MaterialPageRoute(
        builder: (_) => CallScreen(
          callId: _cm.callId ?? '',
          conversationId: _cm.conversationId ?? '',
          isVideo: _cm.isVideo,
          isCaller: _cm.isCaller,
          livekitUrl: '', // 🔥 CallManager already connected hai, ye reuse hoga
          livekitToken: '',
          peerName: _cm.peerName,
          peerAvatar: _cm.peerAvatar,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    // Call chal hi nahi rahi, ya CallScreen khud khula hua hai (minimized
    // nahi hai) to bar dikhane ki zarurat nahi.
    if (!_cm.isActive || !_cm.isMinimized) return const SizedBox.shrink();

    return Positioned(
      top: MediaQuery.of(context).padding.top + 8,
      left: 12,
      right: 12,
      child: SafeArea(
        bottom: false,
        child: Material(
          color: Colors.transparent,
          child: GestureDetector(
            onTap: _reopenCallScreen,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: const Color(0xFF1C1C1E),
                borderRadius: BorderRadius.circular(30),
                boxShadow: const [BoxShadow(color: Colors.black45, blurRadius: 10, offset: Offset(0, 3))],
              ),
              child: Row(
                children: [
                  const Icon(Icons.call, color: Colors.greenAccent, size: 18),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      _cm.remoteConnected
                          ? "${_cm.peerName ?? 'Call'} • ${_formatDuration(_cm.connectedDuration)}"
                          : "${_cm.peerName ?? 'Call'} • ${_cm.status}",
                      style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const SizedBox(width: 6),
                  GestureDetector(
                    onTap: () => _cm.endCall(),
                    child: const CircleAvatar(
                      radius: 14,
                      backgroundColor: Colors.redAccent,
                      child: Icon(Icons.call_end, color: Colors.white, size: 15),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}