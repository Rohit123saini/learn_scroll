import 'package:flutter/material.dart';

import 'sticker_emotion_rive.dart' as emo;
import 'sticker_funny_rive.dart' as funny;

/// ============================================================
/// STICKER PICKER SHEET — reusable in CHAT and COMMENTS both
/// ============================================================
/// Ye ek common bottom-sheet widget hai jo Emotions/Reactions
/// aur Funny/Memes — dono Rive sticker categories ko ek sath
/// dikhata hai, tabs ke through switch karke.
///
/// Kahin bhi (chat screen ho ya comment box) sticker bhejwana ho,
/// bas ye function call karo:
///
///   showStickerPicker(
///     context,
///     onSelected: (emoji) {
///       // apna send-message / post-comment logic yahan likho
///       // emoji ek normal string hai (jaise "👍", "😂") jo
///       // backend me bhi normal emoji ki tarah save ho sakta hai
///     },
///   );
///
/// Agar sirf UI chahiye (embed karna hai kisi screen me, bottom
/// sheet ke bina) to seedha StickerPickerSheet widget bhi use
/// kar sakte ho:
///
///   StickerPickerSheet(onSelected: (emoji) { ... })
/// ============================================================

/// Ek sticker entry: uska animated widget builder + uske sath
/// jo emoji value chat/comment me actually save/bheji jayegi.
class _StickerEntry {
  final Widget Function({required double size, VoidCallback? onReact})
      builder;
  final String emoji;
  final String label;
  const _StickerEntry(this.builder, this.emoji, this.label);
}

final List<_StickerEntry> _emotionEntries = [
  _StickerEntry(
    ({required size, onReact}) =>
        emo.LikeSticker(size: size, onReact: onReact),
    '👍',
    'Like',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        emo.LoveSticker(size: size, onReact: onReact),
    '❤', // NOTE: variation-selector ke bina, taaki chat_screen.dart ke
        // _kEmojis list (['👍','❤','😂','😮','😢','🙏']) se exact match ho
    'Love',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        emo.HahaSticker(size: size, onReact: onReact),
    '😂',
    'Haha',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        emo.WowSticker(size: size, onReact: onReact),
    '😮',
    'Wow',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        emo.SadSticker(size: size, onReact: onReact),
    '😢',
    'Sad',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        emo.AngrySticker(size: size, onReact: onReact),
    '😡',
    'Angry',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        emo.ClapSticker(size: size, onReact: onReact),
    '👏',
    'Clap',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        emo.FireSticker(size: size, onReact: onReact),
    '🔥',
    'Fire',
  ),
];

final List<_StickerEntry> _funnyEntries = [
  _StickerEntry(
    ({required size, onReact}) =>
        funny.LOLSticker(size: size, onReact: onReact),
    '🤣',
    'LOL',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        funny.FacepalmSticker(size: size, onReact: onReact),
    '🤦',
    'Facepalm',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        funny.MindBlownSticker(size: size, onReact: onReact),
    '🤯',
    'Mind Blown',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        funny.TrollSticker(size: size, onReact: onReact),
    '😏',
    'Troll',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        funny.WinkSticker(size: size, onReact: onReact),
    '😉',
    'Wink',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        funny.TongueOutSticker(size: size, onReact: onReact),
    '😜',
    'Tongue Out',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        funny.SavageSticker(size: size, onReact: onReact),
    '😎',
    'Savage',
  ),
  _StickerEntry(
    ({required size, onReact}) =>
        funny.DizzySticker(size: size, onReact: onReact),
    '😵',
    'Dizzy',
  ),
];

/// Chat ke reaction-picker jaisi jagah ke liye chhota helper —
/// agar tumhare paas already koi emoji hai aur uska matching
/// animated sticker widget dikhana hai, to ye function use karo.
/// Match na mile to plain emoji Text fallback degi (crash nahi hoga).
Widget stickerForEmoji(
  String emoji, {
  double size = 44,
  VoidCallback? onReact,
}) {
  final all = [..._emotionEntries, ..._funnyEntries];
  for (final entry in all) {
    if (entry.emoji == emoji) {
      return entry.builder(size: size, onReact: onReact);
    }
  }
  // Fallback — koi matching animated sticker nahi mila
  return Text(emoji, style: TextStyle(fontSize: size * 0.55));
}

/// Poora tabbed sticker-picker sheet — CHAT aur COMMENTS dono
/// jagah embed/call karne ke liye.
class StickerPickerSheet extends StatelessWidget {
  final void Function(String emoji) onSelected;
  final double stickerSize;

  const StickerPickerSheet({
    super.key,
    required this.onSelected,
    this.stickerSize = 64,
  });

  Widget _grid(BuildContext context, List<_StickerEntry> entries) {
    return GridView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: entries.length,
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 4,
        mainAxisSpacing: 12,
        crossAxisSpacing: 12,
        childAspectRatio: 0.85,
      ),
      itemBuilder: (context, index) {
        final entry = entries[index];
        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            entry.builder(
              size: stickerSize,
              onReact: () => onSelected(entry.emoji),
            ),
            const SizedBox(height: 4),
            Text(
              entry.label,
              style: const TextStyle(fontSize: 11, color: Colors.black54),
              overflow: TextOverflow.ellipsis,
            ),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: SafeArea(
        child: SizedBox(
          height: 380,
          child: Column(
            children: [
              const SizedBox(height: 8),
              Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: Colors.black12,
                  borderRadius: BorderRadius.circular(4),
                ),
              ),
              const TabBar(
                labelColor: Color(0xFF030F27),
                unselectedLabelColor: Colors.black38,
                indicatorColor: Color(0xFF030F27),
                tabs: [
                  Tab(text: 'Emotions'),
                  Tab(text: 'Funny'),
                ],
              ),
              Expanded(
                child: TabBarView(
                  children: [
                    _grid(context, _emotionEntries),
                    _grid(context, _funnyEntries),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Sabse aasan tarika — kahin se bhi ek line me sticker picker
/// bottom sheet khol do. `onSelected` me chosen sticker ka emoji
/// milega, jise tum message/comment ki tarah bhej sakte ho.
Future<void> showStickerPicker(
  BuildContext context, {
  required void Function(String emoji) onSelected,
}) {
  return showModalBottomSheet(
    context: context,
    backgroundColor: Colors.white,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
    ),
    builder: (_) => StickerPickerSheet(
      onSelected: (emoji) {
        Navigator.pop(context);
        onSelected(emoji);
      },
    ),
  );
}