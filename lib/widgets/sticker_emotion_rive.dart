import 'package:flutter/material.dart';
import 'package:rive/rive.dart';

/// ============================================================
/// EMOTIONS / REACTIONS - INTERACTIVE CARTOON STICKERS (Rive)
/// ============================================================
/// Ye sabse zyada currently-preferred tarika hai animated
/// cartoon stickers banane ka — Rive engine use karta hai,
/// jisme tap karne pe sticker khud se react karta hai
/// (state machine ke through), sirf play-once animation nahi.
///
/// SETUP (ek baar karna hai):
/// 1) pubspec.yaml me dependency add karo (naya 0.14+ API):
///
///      dependencies:
///        rive: ^0.14.4
///
///    aur assets register karo:
///
///      flutter:
///        assets:
///          - assets/rive/
///
/// 2) rive.app (free editor, sign up free hai) pe jaake ya
///    rive.app/community (free community files) se in
///    stickers ko download/banake "assets/rive/" me rakho:
///
///      assets/rive/like.riv     -> thumbs up (State Machine: "State Machine 1")
///      assets/rive/love.riv     -> heart/love
///      assets/rive/haha.riv     -> laughing face
///      assets/rive/wow.riv      -> surprised face
///      assets/rive/sad.riv      -> crying face
///      assets/rive/angry.riv    -> angry face
///      assets/rive/clap.riv     -> clapping hands
///      assets/rive/fire.riv     -> fire / lit
///
///    Community pe search karo: "reaction", "emoji", "sticker pack",
///    "like button", "heart animation" -> free filter -> download .riv
///
///    NOTE: Har .riv file ke andar State Machine ka naam check karo
///    (Rive editor me dikhता है). Agar tumhara naam "State Machine 1"
///    se alag hai to niche _stateMachineName value badal dena.
///
/// 3) 🔥 IMPORTANT (naya rive 0.14+ API): apne main.dart me app start
///    hone se pehle Rive native runtime init karo:
///
///      import 'package:rive/rive.dart';
///      Future<void> main() async {
///        WidgetsFlutterBinding.ensureInitialized();
///        await RiveNative.init();
///        runApp(const MyApp());
///      }
///
/// USAGE — sirf class ko call karo, tap karne pe animation trigger
/// hoga:
///   LikeSticker()
///   LoveSticker(size: 90)
///   HahaSticker(size: 70, onReact: () => print("haha tapped"))
///
/// Available Classes:
///   1. LikeSticker    -> 👍 thumbs up interactive sticker
///   2. LoveSticker    -> ❤️ love/heart interactive sticker
///   3. HahaSticker    -> 😂 laughing interactive sticker
///   4. WowSticker     -> 😮 surprised interactive sticker
///   5. SadSticker     -> 😢 crying interactive sticker
///   6. AngrySticker   -> 😡 angry interactive sticker
///   7. ClapSticker    -> 👏 clapping interactive sticker
///   8. FireSticker    -> 🔥 fire interactive sticker
/// ============================================================

/// Internal base widget — saare interactive stickers isi se ban rahe
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
          // Agar .riv file abhi assets me nahi hai (ya load fail ho
          // jaaye) to ye placeholder dikhega, taaki app crash na ho.
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

/// 👍 LikeSticker — call this to show an interactive Like/Thumbs-up sticker.
/// Needs: assets/rive/like.riv
class LikeSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const LikeSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/like.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// ❤️ LoveSticker — call this to show an interactive Love/Heart sticker.
/// Needs: assets/rive/love.riv
class LoveSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const LoveSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/love.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 😂 HahaSticker — call this to show an interactive Haha/Laughing sticker.
/// Needs: assets/rive/haha.riv
class HahaSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const HahaSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/haha.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 😮 WowSticker — call this to show an interactive Wow/Surprised sticker.
/// Needs: assets/rive/wow.riv
class WowSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const WowSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/wow.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 😢 SadSticker — call this to show an interactive Sad/Crying sticker.
/// Needs: assets/rive/sad.riv
class SadSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const SadSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/sad.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 😡 AngrySticker — call this to show an interactive Angry sticker.
/// Needs: assets/rive/angry.riv
class AngrySticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const AngrySticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/angry.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 👏 ClapSticker — call this to show an interactive Clap sticker.
/// Needs: assets/rive/clap.riv
class ClapSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const ClapSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/clap.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// 🔥 FireSticker — call this to show an interactive Fire sticker.
/// Needs: assets/rive/fire.riv
class FireSticker extends StatelessWidget {
  final double size;
  final VoidCallback? onReact;
  const FireSticker({super.key, this.size = 80, this.onReact});

  @override
  Widget build(BuildContext context) {
    return _RiveStickerBase(
      assetPath: 'assets/rive/fire.riv',
      size: size,
      onReact: onReact,
    );
  }
}

/// ------------------------------------------------------------
/// BONUS: EmotionReactionPicker
/// Chat/comment ke niche ek row me saare interactive cartoon
/// reactions dikhane ke liye ye ready-made picker call kar
/// sakte ho.
/// Example: EmotionReactionPicker(onSelected: (name) => print(name))
/// ------------------------------------------------------------
class EmotionReactionPicker extends StatelessWidget {
  final void Function(String reactionName)? onSelected;
  final double stickerSize;

  const EmotionReactionPicker({
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
          LikeSticker(size: stickerSize, onReact: () => onSelected?.call('Like')),
          LoveSticker(size: stickerSize, onReact: () => onSelected?.call('Love')),
          HahaSticker(size: stickerSize, onReact: () => onSelected?.call('Haha')),
          WowSticker(size: stickerSize, onReact: () => onSelected?.call('Wow')),
          SadSticker(size: stickerSize, onReact: () => onSelected?.call('Sad')),
          AngrySticker(size: stickerSize, onReact: () => onSelected?.call('Angry')),
          ClapSticker(size: stickerSize, onReact: () => onSelected?.call('Clap')),
          FireSticker(size: stickerSize, onReact: () => onSelected?.call('Fire')),
        ],
      ),
    );
  }
}