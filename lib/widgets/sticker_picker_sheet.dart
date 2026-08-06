import 'package:flutter/material.dart';

import 'own_stickers.dart';

/// ============================================================
/// STICKER PICKER SHEET — TUMHARE APNE PNG STICKERS (own_stickers.dart)
/// ============================================================
/// Pehle ye Rive/Lottie animated stickers dikhata tha (jinke liye
/// alag .riv/.json asset files download karni padti thi). Ab wo
/// poora hata diya gaya hai — ab ye seedha tumhare "assets/stickers/"
/// folder wale 100 PNG stickers dikhata hai, category-wise tabs me
/// (Hi, Bye, Morning, Night, Laugh, Okay, No, Sorry, Thanks, Wassup).
///
/// Koi extra package nahi chahiye — sirf normal Image.asset().
///
/// USAGE — chat ya comments, kahin se bhi:
///
///   showStickerPicker(
///     context,
///     onSelected: (assetPath) {
///       // assetPath jaisे "assets/stickers/hi_frog.png" milega —
///       // ise normal image message ki tarah bhejo (apna existing
///       // upload/send logic use karke).
///     },
///   );
///
/// Sirf UI embed karna ho (bottom sheet ke bina):
///   StickerPickerSheet(onSelected: (assetPath) { ... })
/// ============================================================

class StickerPickerSheet extends StatefulWidget {
  final void Function(String assetPath) onSelected;
  final double stickerSize;

  const StickerPickerSheet({
    super.key,
    required this.onSelected,
    this.stickerSize = 72,
  });

  @override
  State<StickerPickerSheet> createState() => _StickerPickerSheetState();
}

class _StickerPickerSheetState extends State<StickerPickerSheet>
    with SingleTickerProviderStateMixin {
  late final TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: stickerCategories.length, vsync: this);
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  Widget _grid(List<StickerItem> stickers) {
    return GridView.builder(
      padding: const EdgeInsets.all(14),
      itemCount: stickers.length,
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 4,
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
        childAspectRatio: 1,
      ),
      itemBuilder: (context, index) {
        final sticker = stickers[index];
        return GestureDetector(
          onTap: () => widget.onSelected(sticker.assetPath),
          child: Container(
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              color: const Color(0xFFF5F6F8),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Image.asset(
              sticker.assetPath,
              fit: BoxFit.contain,
              errorBuilder: (context, error, stackTrace) {
                // PNG abhi assets me nahi hai ya pubspec.yaml me
                // register nahi hua — placeholder, crash nahi.
                return const Icon(Icons.image_not_supported_outlined, color: Colors.black26);
              },
            ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SizedBox(
        height: 420,
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
            const SizedBox(height: 4),
            TabBar(
              controller: _tabController,
              isScrollable: true,
              labelColor: const Color(0xFF030F27),
              unselectedLabelColor: Colors.black38,
              indicatorColor: const Color(0xFF030F27),
              tabs: stickerCategories
                  .map((c) => Tab(text: '${c.tabEmoji}  ${c.label}'))
                  .toList(),
            ),
            const Divider(height: 1),
            Expanded(
              child: TabBarView(
                controller: _tabController,
                children: stickerCategories.map((c) => _grid(c.stickers)).toList(),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Sabse aasan tarika — kahin se bhi ek line me sticker picker
/// bottom sheet khol do. `onSelected` me chosen sticker ka asset
/// path milega (jaise "assets/stickers/hi_frog.png").
Future<void> showStickerPicker(
  BuildContext context, {
  required void Function(String assetPath) onSelected,
}) {
  return showModalBottomSheet(
    context: context,
    backgroundColor: Colors.white,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
    ),
    builder: (_) => StickerPickerSheet(
      onSelected: (assetPath) {
        Navigator.pop(context);
        onSelected(assetPath);
      },
    ),
  );
}