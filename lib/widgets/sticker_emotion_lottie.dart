import 'package:flutter/material.dart';
import 'package:lottie/lottie.dart';

/// ============================================================
/// EMOTIONS / REACTIONS - CARTOON ANIMATED STICKERS (Lottie)
/// ============================================================
/// Ye real cartoon-style animated stickers hain (jaise WhatsApp/
/// Telegram/Instagram me hote hain), Lottie animation engine use
/// karke banaye gaye hain.
///
/// SETUP (ek baar karna hai):
/// 1) pubspec.yaml me dependency add karo:
///
///      dependencies:
///        lottie: ^3.1.2
///
///    aur assets register karo:
///
///      flutter:
///        assets:
///          - assets/lottie/
///
/// 2) LottieFiles.com (free, bina login ke bhi milte hain) se
///    neeche di hui category ke stickers download karke
///    "assets/lottie/" folder me isi naam se rakho:
///
///      assets/lottie/like.json     -> thumbs up animation
///      assets/lottie/love.json     -> heart/love animation
///      assets/lottie/haha.json     -> laughing face animation
///      assets/lottie/wow.json      -> surprised face animation
///      assets/lottie/sad.json      -> crying face animation
///      assets/lottie/angry.json    -> angry face animation
///      assets/lottie/clap.json     -> clapping hands animation
///      assets/lottie/fire.json     -> fire/lit animation
///
///    (Search on lottiefiles.com: "thumbs up", "heart love",
///     "laughing emoji", "wow emoji", "crying emoji",
///     "angry emoji", "clapping hands", "fire" -> Free filter -> Download JSON)
///
/// USAGE — sirf class ko call karo:
///   LikeStickerLottie()
///   LoveStickerLottie(size: 90)
///   HahaStickerLottie(size: 70, repeat: true)
///
/// Available Classes:
///   1. LikeStickerLottie    -> 👍 thumbs up cartoon animation
///   2. LoveStickerLottie    -> ❤️ love/heart cartoon animation
///   3. HahaStickerLottie    -> 😂 laughing cartoon animation
///   4. WowStickerLottie     -> 😮 surprised cartoon animation
///   5. SadStickerLottie     -> 😢 crying cartoon animation
///   6. AngryStickerLottie   -> 😡 angry cartoon animation
///   7. ClapStickerLottie    -> 👏 clapping cartoon animation
///   8. FireStickerLottie    -> 🔥 fire cartoon animation
/// ============================================================

/// Internal base widget — saare cartoon stickers isi se ban rahe hain.
/// Directly isko call karne ki zarurat nahi hai.
class _LottieStickerBase extends StatelessWidget {
  final String assetPath;
  final double size;
  final bool repeat;
  final VoidCallback? onTap;

  const _LottieStickerBase({
    required this.assetPath,
    required this.size,
    this.repeat = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
        width: size,
        height: size,
        child: Lottie.asset(
          assetPath,
          repeat: repeat,
          fit: BoxFit.contain,
          errorBuilder: (context, error, stackTrace) {
            // Agar json file abhi assets me nahi daali hai to
            // ye placeholder dikhega, taaki app crash na ho.
            return Container(
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: Colors.grey.shade200,
                shape: BoxShape.circle,
              ),
              child: const Icon(Icons.image_not_supported_outlined),
            );
          },
        ),
      ),
    );
  }
}

/// 👍 LikeStickerLottie — call this to show a cartoon Like/Thumbs-up sticker.
/// Needs: assets/lottie/like.json
class LikeStickerLottie extends StatelessWidget {
  final double size;
  final bool repeat;
  final VoidCallback? onTap;
  const LikeStickerLottie({
    super.key,
    this.size = 80,
    this.repeat = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return _LottieStickerBase(
      assetPath: 'assets/lottie/like.json',
      size: size,
      repeat: repeat,
      onTap: onTap,
    );
  }
}

/// ❤️ LoveStickerLottie — call this to show a cartoon Love/Heart sticker.
/// Needs: assets/lottie/love.json
class LoveStickerLottie extends StatelessWidget {
  final double size;
  final bool repeat;
  final VoidCallback? onTap;
  const LoveStickerLottie({
    super.key,
    this.size = 80,
    this.repeat = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return _LottieStickerBase(
      assetPath: 'assets/lottie/love.json',
      size: size,
      repeat: repeat,
      onTap: onTap,
    );
  }
}

/// 😂 HahaStickerLottie — call this to show a cartoon Haha/Laughing sticker.
/// Needs: assets/lottie/haha.json
class HahaStickerLottie extends StatelessWidget {
  final double size;
  final bool repeat;
  final VoidCallback? onTap;
  const HahaStickerLottie({
    super.key,
    this.size = 80,
    this.repeat = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return _LottieStickerBase(
      assetPath: 'assets/lottie/haha.json',
      size: size,
      repeat: repeat,
      onTap: onTap,
    );
  }
}

/// 😮 WowStickerLottie — call this to show a cartoon Wow/Surprised sticker.
/// Needs: assets/lottie/wow.json
class WowStickerLottie extends StatelessWidget {
  final double size;
  final bool repeat;
  final VoidCallback? onTap;
  const WowStickerLottie({
    super.key,
    this.size = 80,
    this.repeat = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return _LottieStickerBase(
      assetPath: 'assets/lottie/wow.json',
      size: size,
      repeat: repeat,
      onTap: onTap,
    );
  }
}

/// 😢 SadStickerLottie — call this to show a cartoon Sad/Crying sticker.
/// Needs: assets/lottie/sad.json
class SadStickerLottie extends StatelessWidget {
  final double size;
  final bool repeat;
  final VoidCallback? onTap;
  const SadStickerLottie({
    super.key,
    this.size = 80,
    this.repeat = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return _LottieStickerBase(
      assetPath: 'assets/lottie/sad.json',
      size: size,
      repeat: repeat,
      onTap: onTap,
    );
  }
}

/// 😡 AngryStickerLottie — call this to show a cartoon Angry sticker.
/// Needs: assets/lottie/angry.json
class AngryStickerLottie extends StatelessWidget {
  final double size;
  final bool repeat;
  final VoidCallback? onTap;
  const AngryStickerLottie({
    super.key,
    this.size = 80,
    this.repeat = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return _LottieStickerBase(
      assetPath: 'assets/lottie/angry.json',
      size: size,
      repeat: repeat,
      onTap: onTap,
    );
  }
}

/// 👏 ClapStickerLottie — call this to show a cartoon Clap sticker.
/// Needs: assets/lottie/clap.json
class ClapStickerLottie extends StatelessWidget {
  final double size;
  final bool repeat;
  final VoidCallback? onTap;
  const ClapStickerLottie({
    super.key,
    this.size = 80,
    this.repeat = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return _LottieStickerBase(
      assetPath: 'assets/lottie/clap.json',
      size: size,
      repeat: repeat,
      onTap: onTap,
    );
  }
}

/// 🔥 FireStickerLottie — call this to show a cartoon Fire sticker.
/// Needs: assets/lottie/fire.json
class FireStickerLottie extends StatelessWidget {
  final double size;
  final bool repeat;
  final VoidCallback? onTap;
  const FireStickerLottie({
    super.key,
    this.size = 80,
    this.repeat = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return _LottieStickerBase(
      assetPath: 'assets/lottie/fire.json',
      size: size,
      repeat: repeat,
      onTap: onTap,
    );
  }
}

/// ------------------------------------------------------------
/// BONUS: EmotionReactionPickerLottie
/// Chat/comment ke niche ek row me saare cartoon reactions
/// dikhane ke liye ye ready-made picker call kar sakte ho.
/// Example: EmotionReactionPickerLottie(onSelected: (name) => print(name))
/// ------------------------------------------------------------
class EmotionReactionPickerLottie extends StatelessWidget {
  final void Function(String reactionName)? onSelected;
  final double stickerSize;

  const EmotionReactionPickerLottie({
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
          LikeStickerLottie(size: stickerSize, onTap: () => onSelected?.call('Like')),
          LoveStickerLottie(size: stickerSize, onTap: () => onSelected?.call('Love')),
          HahaStickerLottie(size: stickerSize, onTap: () => onSelected?.call('Haha')),
          WowStickerLottie(size: stickerSize, onTap: () => onSelected?.call('Wow')),
          SadStickerLottie(size: stickerSize, onTap: () => onSelected?.call('Sad')),
          AngryStickerLottie(size: stickerSize, onTap: () => onSelected?.call('Angry')),
          ClapStickerLottie(size: stickerSize, onTap: () => onSelected?.call('Clap')),
          FireStickerLottie(size: stickerSize, onTap: () => onSelected?.call('Fire')),
        ],
      ),
    );
  }
}