// message/widgets/minimized_call_bar.dart
//
// WhatsApp/Messenger jaisa chhota floating pill jo call minimize karne ke
// baad app ke upar hamesha dikhta hai (jis bhi screen pe user ho) — tap
// karke wapas CallScreen khulta hai, red button se seedha call end.
//
// WIRING (main.dart me, MaterialApp ke andar):
//
//   return MaterialApp(
//     navigatorKey: navigatorKey,
//     builder: (context, child) {
//       return Stack(
//         children: [
//           if (child != null) child,
//           const MinimizedCallBar(),
//         ],
//       );
//     },
//     ...
//   );
//
// MinimizedCallBar khud CallManager.instance ko listen karta hai, isliye
// kahin aur se manually show/hide karne ki zaroorat nahi.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../services/call_manager.dart';
import '../services/call_kit_service.dart';
import '../screens/call_screen.dart';

class MinimizedCallBar extends StatelessWidget {
  const MinimizedCallBar({super.key});

  String _formatDuration(Duration d) {
    String two(int n) => n.toString().padLeft(2, '0');
    return "${d.inMinutes}:${two(d.inSeconds.remainder(60))}";
  }

  // 🔥 FIX — "back karne ke baad fullscreen wapas nahi aati" bug ka root
  // cause: main.dart me ye widget MaterialApp.builder ke Stack me `child`
  // ke SIBLING ke taur pe lagaya jaata hai (jaisa is file ke header
  // comment me hi likha hai) — matlab iska BuildContext Navigator ke
  // ANDAR nahi, uske BAHAR hota hai. Isliye pehle wala
  // `Navigator.of(context, rootNavigator: true)` kaam nahi karta tha
  // (no Navigator found in context) aur tap silently fail ho jaata tha.
  //
  // Fix: local context ke bharose na rehke wahi global `navigatorKey`
  // reuse karo jo already CallKitService me set hota hai (main.dart me
  // MaterialApp ke `navigatorKey:` field ke saath) — ye poori app me
  // hamesha reliably kaam karta hai, chahe widget kahin bhi mounted ho.
  void _expand(CallManager cm) {
    cm.unminimize();
    final navigator = CallKitService.navigatorKey?.currentState;
    if (navigator == null) return; // main.dart me navigatorKey wire nahi hua — neeche wiring note dekho
    navigator.push(MaterialPageRoute(
      builder: (_) => CallScreen(
        callId: cm.callId ?? '',
        conversationId: cm.conversationId ?? '',
        isVideo: cm.isVideo,
        isCaller: cm.isCaller,
        // Call already CallManager me chal rahi hai (room connected), isliye
        // yahan livekitUrl/token dobara connect ke liye nahi chahiye —
        // CallScreen.startCallIfNeeded() same callId dekh ke reuse karega.
        livekitUrl: '',
        livekitToken: '',
        peerName: cm.peerName,
        peerAvatar: cm.peerAvatar,
      ),
    ));
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: CallManager.instance,
      builder: (context, _) {
        final cm = CallManager.instance;
        final visible = cm.isActive && cm.isMinimized;

        return AnimatedSlide(
          duration: const Duration(milliseconds: 220),
          curve: Curves.easeOut,
          offset: visible ? Offset.zero : const Offset(0, -1.4),
          child: AnimatedOpacity(
            duration: const Duration(milliseconds: 180),
            opacity: visible ? 1 : 0,
            child: visible ? _buildBar(context, cm) : const SizedBox.shrink(),
          ),
        );
      },
    );
  }

  Widget _buildBar(BuildContext context, CallManager cm) {
    final initial = (cm.peerName?.trim().isNotEmpty == true ? cm.peerName![0] : "U").toUpperCase();

    return SafeArea(
      child: Align(
        alignment: Alignment.topCenter,
        child: Padding(
          padding: const EdgeInsets.only(top: 8, left: 12, right: 12),
          child: Material(
            color: const Color(0xFF1C1C1E),
            elevation: 8,
            shadowColor: Colors.black54,
            borderRadius: BorderRadius.circular(30),
            child: InkWell(
              borderRadius: BorderRadius.circular(30),
              onTap: () => _expand(cm),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    CircleAvatar(
                      radius: 16,
                      backgroundColor: const Color(0xFF2E4E82),
                      backgroundImage: (cm.peerAvatar != null && cm.peerAvatar!.isNotEmpty)
                          ? CachedNetworkImageProvider(cm.peerAvatar!)
                          : null,
                      child: (cm.peerAvatar == null || cm.peerAvatar!.isEmpty)
                          ? Text(initial, style: const TextStyle(color: Colors.white, fontSize: 13))
                          : null,
                    ),
                    const SizedBox(width: 10),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          cm.peerName ?? "Call",
                          style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                        ),
                        Text(
                          cm.remoteConnected ? _formatDuration(cm.connectedDuration) : cm.status,
                          style: TextStyle(color: Colors.white.withOpacity(0.65), fontSize: 11.5),
                        ),
                      ],
                    ),
                    const SizedBox(width: 14),
                    GestureDetector(
                      onTap: () => cm.endCall(),
                      child: Container(
                        width: 30,
                        height: 30,
                        decoration: const BoxDecoration(shape: BoxShape.circle, color: Color(0xFFE53E3E)),
                        child: const Icon(Icons.call_end_rounded, color: Colors.white, size: 16),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}