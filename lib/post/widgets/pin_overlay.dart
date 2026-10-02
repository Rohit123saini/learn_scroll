// lib/post/widgets/pin_overlay.dart
//
// P3-FE — profile-grid helpers for pinned posts.
//   * PinnedTileOverlay : wraps any grid tile; draws the 📌 badge when
//     `pinned`, and (own profile only) opens the menu on long-press.
//   * showPinMenuSheet  : bottom sheet with "Pin to profile" / "Unpin from profile".

import 'package:flutter/material.dart';

class PinnedTileOverlay extends StatelessWidget {
  final bool pinned;
  final VoidCallback? onLongPress; // null => read-only (someone else's profile)
  final bool alignLeft; // true when the tile already has a badge at top-right (Reposts tab)
  final Widget child;

  const PinnedTileOverlay({super.key, required this.pinned, required this.child, this.onLongPress, this.alignLeft = false});

  @override
  Widget build(BuildContext context) {
    if (!pinned && onLongPress == null) return child;
    final content = Stack(
      fit: StackFit.expand,
      children: [
        child,
        if (pinned)
          Positioned(
            top: 6,
            left: alignLeft ? 6 : null,
            right: alignLeft ? null : 6,
            child: Semantics(
              label: 'Pinned post',
              child: Container(
                padding: const EdgeInsets.all(4),
                decoration: const BoxDecoration(color: Colors.black54, shape: BoxShape.circle),
                child: const Icon(Icons.push_pin_rounded, size: 13, color: Colors.white),
              ),
            ),
          ),
      ],
    );
    if (onLongPress == null) return content;
    return GestureDetector(onLongPress: onLongPress, child: content);
  }
}

/// [onToggle] does the API call + refresh; the sheet closes first so the
/// caller can show a snackbar on the screen underneath.
Future<void> showPinMenuSheet(
  BuildContext context, {
  required bool isPinned,
  required VoidCallback onToggle,
}) {
  return showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    builder: (sheetContext) => SafeArea(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        ListTile(
          leading: Icon(isPinned ? Icons.push_pin_outlined : Icons.push_pin_rounded),
          title: Text(isPinned ? 'Unpin from profile' : 'Pin to profile'),
          onTap: () {
            Navigator.pop(sheetContext);
            onToggle();
          },
        ),
      ]),
    ),
  );
}
