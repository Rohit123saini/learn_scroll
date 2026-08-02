import 'package:flutter/material.dart';
import 'package:rive/rive.dart';

/// ============================================================
/// FUNNY / MEMES - INTERACTIVE CARTOON STICKERS (Rive)
/// ============================================================
/// Sabse zyada use hone wale funny/meme-style reaction stickers,
/// Rive engine se banaye gaye — tap karne pe interactively
/// animate hote hain (state machine ke through).
///
/// SETUP (agar Emotions/Reactions wala setup pehle se kar chuke
/// ho to sirf naye .riv files add karne hain, package dubara
/// add karne ki zarurat nahi):
///
/// 1) pubspec.yaml me dependency (agar pehle se nahi hai — naya
///    0.14+ API):
///
///      dependencies:
///        rive: ^0.14.4
///
///      flutter:
///        assets:
///          - assets/rive/
///
/// 2) rive.app/community (free) se in stickers ko download
///    karke "assets/rive/" me isi naam se rakho:
///
///      assets/rive/lol.riv        -> rolling-on-floor laughing
///      assets/rive/facepalm.riv   -> facepalm / disappointed
///      assets/rive/mindblown.riv  -> mind blown / shocked
///      assets/rive/troll.riv      -> troll face / mischievous grin
///      assets/rive/wink.riv       -> winking face
///      assets/rive/tongue.riv     -> tongue-out / silly face
///      assets/rive/savage.riv     -> cool sunglasses / savage
///      assets/rive/dizzy.riv      -> dizzy / confused swirl eyes
///
///    Community pe search karo: "funny emoji", "meme sticker",
///    "laughing", "mind blown", "troll face", "cool sunglasses",
///    "dizzy face" -> Free filter -> download .riv
///
///    NOTE: Har .riv file ka State Machine name check kar lena
///    (Rive editor me dikhता है). Default yahan "State Machine 1"
///    maana gaya hai — agar alag ho to niche stateMachineName
///    value badal dena.
///
/// 3) 🔥 IMPORTANT (naya rive 0.14+ API): apne main.dart me app start
///    hone se pehle Rive native runtime init karo (agar
///    sticker_emotion_rive.dart wale setup me pehle hi kar chuke ho
///    to dobara karne ki zarurat nahi):
///
///      import 'package:rive/rive.dart';
///      Future<void> main() async {
///        WidgetsFlutterBinding.ensureInitialized();
///        await RiveNative.init();
///        runApp(const MyApp());
///      }
///
/// USAGE — sirf class ko call karo:
///   LOLSticker()
///   TrollSticker(size: 90)
///   MindBlownSticker(size: 70, onReact: () => print("mind blown tapped"))
///
/// Available Classes:
///   1. LOLSticker         -> 🤣 rolling-on-floor laughing
///   2. FacepalmSticker    -> 🤦 facepalm
///   3. MindBlownSticker   -> 🤯 mind blown
///   4. TrollSticker       -> 😏 troll / mischievous grin
///   5. WinkSticker        -> 😉 wink
///   6. TongueOutSticker   -> 😜 tongue-out silly face
///   7. SavageSticker      -> 😎 cool / savage sunglasses
///   8. DizzySticker       -> 😵 dizzy / confused
/// ============================================================

/// Internal base widget — saare funny stickers isi se ban rahe
/// hain. Directly isko call karne ki zarurat nahi hai.
///
/// 🔥 UPDATED — Rive Flutter 0.14+ ke naye API pe migrate kiya gaya hai
/// (purana StateMachineController / SMITrigger / RiveAnimation.asset
/// naye rive package me hata diya gaya hai). Ab FileLoader +
/// RiveWidgetBuilder + RiveWidgetController use hota hai.
class _RiveStickerBase extends StatefulWidget {
  final String assetPath;
  final String stateMachineName;
  final String triggerInputName;
  final double size;
  final VoidCallback? onReact;

  const _RiveStickerBase({
    required this.assetPath,
    required this.size,
    this.stateMachineName = 'State Machine 1',
    this.triggerInputName = 'Tap',
    this.onReact,
  });

  @override
  State<_RiveStickerBase> createState() => _RiveStickerBaseState();
}

class _RiveStickerBaseState extends State<_RiveStickerBase> {
  late final FileLoader _fileLoader = FileLoader.fromAsset(
    widget.assetPath,
    riveFactory: Factory.rive,
  );

  void _handleTap(RiveWidgetController controller) {
    // Naye API me trigger input seedha naam se fire hota hai.
    controller.stateMachine.trigger(widget.triggerInputName)?.fire();
    widget.onReact?.call();
  }

  @override
  void dispose() {
    _fileLoader.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: widget.size,
      height: widget.size,
      child: RiveWidgetBuilder(
        fileLoader: _fileLoader,
        stateMachineSelector: StateMachineSelector.byName(widget.stateMachineName),
        builder: (context, state) => switch (state) {
          RiveLoading() => const Center(
              child: SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
            ),
          RiveFailed() => GestureDetector(
              onTap: widget.onReact,
              child: Container(
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: Colors.grey.shade200,
                  shape: BoxShape.circle,
                ),
                child: const Icon(Icons.image_not_supported_outlined),
              ),
            ),
          RiveLoaded() => GestureDetector(
              onTap: () => _handleTap(state.controller),
              child: RiveWidget(
                controller: state.controller,
                fit: Fit.contain,
              ),
            ),
        },
      ),
    );
  }
}

/// 🤣 LOLSticker — call this to show an interactive rolling-laughing sticker.
/// Needs: assets/rive/lol.riv
class LOLSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const LOLSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/lol.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 🤦 FacepalmSticker — call this to show an interactive facepalm sticker.
/// Needs: assets/rive/facepalm.riv
class FacepalmSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const FacepalmSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/facepalm.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 🤯 MindBlownSticker — call this to show an interactive mind-blown sticker.
/// Needs: assets/rive/mindblown.riv
class MindBlownSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const MindBlownSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/mindblown.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 😏 TrollSticker — call this to show an interactive troll-face sticker.
/// Needs: assets/rive/troll.riv
class TrollSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const TrollSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/troll.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 😉 WinkSticker — call this to show an interactive wink sticker.
/// Needs: assets/rive/wink.riv
class WinkSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const WinkSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/wink.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 😜 TongueOutSticker — call this to show an interactive silly/tongue-out sticker.
/// Needs: assets/rive/tongue.riv
class TongueOutSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const TongueOutSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/tongue.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 😎 SavageSticker — call this to show an interactive cool/savage sticker.
/// Needs: assets/rive/savage.riv
class SavageSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const SavageSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/savage.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 😵 DizzySticker — call this to show an interactive dizzy/confused sticker.
/// Needs: assets/rive/dizzy.riv
class DizzySticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const DizzySticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/dizzy.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// ------------------------------------------------------------
/// BONUS: FunnyMemePicker
/// Chat/comment ke niche ek row me saare funny stickers dikhane
/// ke liye ye ready-made picker call kar sakte ho.
/// Example: FunnyMemePicker(onSelected: (name) => print(name))
/// ------------------------------------------------------------
class FunnyMemePicker extends StatelessWidget {
  final void Function(String reactionName)? onSelected;
  final double stickerSize;

  const FunnyMemePicker({
    super.key,
    this.onSelected,
    this.stickerSize = 48,
  });

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          LOLSticker(size: stickerSize, onReact: () => onSelected?.call('LOL')),
          FacepalmSticker(size: stickerSize, onReact: () => onSelected?.call('Facepalm')),
          MindBlownSticker(size: stickerSize, onReact: () => onSelected?.call('MindBlown')),
          TrollSticker(size: stickerSize, onReact: () => onSelected?.call('Troll')),
          WinkSticker(size: stickerSize, onReact: () => onSelected?.call('Wink')),
          TongueOutSticker(size: stickerSize, onReact: () => onSelected?.call('TongueOut')),
          SavageSticker(size: stickerSize, onReact: () => onSelected?.call('Savage')),
          DizzySticker(size: stickerSize, onReact: () => onSelected?.call('Dizzy')),
        ],
      ),
    );
  }
}