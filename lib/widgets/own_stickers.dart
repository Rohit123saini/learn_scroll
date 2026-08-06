// message/widgets/own_stickers.dart
//
// ============================================================
// TUMHARE APNE STICKERS — asset PNG catalog (Rive/Lottie NAHI)
// ============================================================
// Ye file sirf ek CATALOG hai — tumhare `assets/stickers/` folder
// me jo 100 PNG files pehle se maujood hain (bye_*, gm_*, gn_*,
// hi_*, laugh_*, no_*, okdone_*, sorry_*, ty_*, wup_*), unko
// category-wise groups me organize karti hai taaki sticker picker
// unhe tabs me dikha sake.
//
// ⚠️ IMPORTANT — SIRF EK HI SETUP STEP BACHA HAI:
// Apne pubspec.yaml me ye add karo (agar already nahi hai):
//
//   flutter:
//     assets:
//       - assets/stickers/
//
// Bas. Koi Rive nahi, koi Lottie nahi, koi extra package nahi —
// ye sirf normal Image.asset() use karta hai, jo tumhare pubspec
// me already declared PNG folder se seedha load hoga.
//
// Agar kal ko koi naya sticker PNG "assets/stickers/" me daalo,
// bas neeche uski category ki list me uska naam add kar dena —
// naam MUST match: "<prefix>_<name>.png" wahi jo file ka actual
// naam hai.
// ============================================================

class StickerItem {
  final String assetPath;
  final String name; // display label (fallback tooltip)
  const StickerItem(this.assetPath, this.name);
}

class StickerCategory {
  final String id;
  final String label; // tab label
  final String tabEmoji; // chhota emoji jo tab icon ki tarah dikhta hai
  final List<StickerItem> stickers;
  const StickerCategory({
    required this.id,
    required this.label,
    required this.tabEmoji,
    required this.stickers,
  });
}

const String _kBase = 'assets/stickers/';

List<StickerItem> _pack(String prefix, List<String> names) {
  return names
      .map((n) => StickerItem('$_kBase${prefix}_$n.png', n))
      .toList(growable: false);
}

/// Poora catalog — WhatsApp/Telegram jaisa tab-wise organized.
/// Order yahi order hai jisme tabs dikhenge.
final List<StickerCategory> stickerCategories = [
  StickerCategory(
    id: 'hi',
    label: 'Hi',
    tabEmoji: '👋',
    stickers: _pack('hi', [
      'astronaut', 'cactus', 'catpaw', 'cloud', 'frog',
      'ghost', 'monster', 'retro', 'smiley', 'toast',
    ]),
  ),
  StickerCategory(
    id: 'bye',
    label: 'Bye',
    tabEmoji: '🙋',
    stickers: _pack('bye', [
      'bear', 'bunny', 'cat', 'cloud', 'frog',
      'ghost', 'hand', 'retro', 'skull', 'toast',
    ]),
  ),
  StickerCategory(
    id: 'gm',
    label: 'Morning',
    tabEmoji: '☀️',
    stickers: _pack('gm', [
      'bear', 'cat', 'cloud', 'coffee', 'flower',
      'frog', 'ghost', 'retro', 'sun', 'toast',
    ]),
  ),
  StickerCategory(
    id: 'gn',
    label: 'Night',
    tabEmoji: '🌙',
    stickers: _pack('gn', [
      'bear', 'bunny', 'cat', 'cloud', 'frog',
      'ghost', 'milk', 'moon', 'retro', 'stars',
    ]),
  ),
  StickerCategory(
    id: 'laugh',
    label: 'Laugh',
    tabEmoji: '😂',
    stickers: _pack('laugh', [
      'cat', 'cloud', 'emoji', 'frog', 'ghost',
      'hamster', 'me', 'skull', 'toast', 'villain',
    ]),
  ),
  StickerCategory(
    id: 'okdone',
    label: 'Okay',
    tabEmoji: '✅',
    stickers: _pack('okdone', [
      'bear', 'bunny', 'cat', 'cloud', 'frog',
      'ghost', 'hands', 'retro', 'tick', 'toast',
    ]),
  ),
  StickerCategory(
    id: 'no',
    label: 'No',
    tabEmoji: '🙅',
    stickers: _pack('no', [
      'bear', 'bunny', 'cat', 'cloud', 'frog',
      'ghost', 'hand', 'retro', 'skull', 'toast',
    ]),
  ),
  StickerCategory(
    id: 'sorry',
    label: 'Sorry',
    tabEmoji: '🙏',
    stickers: _pack('sorry', [
      'bear', 'cat1', 'cloud', 'cookie', 'frog',
      'ghost', 'hands', 'heart', 'puppy', 'retro',
    ]),
  ),
  StickerCategory(
    id: 'ty',
    label: 'Thanks',
    tabEmoji: '🙌',
    stickers: _pack('ty', [
      'bear', 'bunny', 'cat', 'cloud', 'flower',
      'frog', 'ghost', 'hands', 'retro', 'toast',
    ]),
  ),
  StickerCategory(
    id: 'wup',
    label: 'Wassup',
    tabEmoji: '🤙',
    stickers: _pack('wup', [
      'alien', 'bear', 'bunny', 'cat', 'cloud',
      'frog', 'ghost', 'retro', 'skull', 'toast',
    ]),
  ),
];