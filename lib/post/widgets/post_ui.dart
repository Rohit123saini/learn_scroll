// lib/post/widgets/post_ui.dart
//
// UI-POLISH PASS — small, purely presentational widgets shared between
// the feed card (home.dart's `_buildPostCard`), the single-post detail
// screen (singlepost.dart) and the saved/explore/hashtag grid
// (post_list_screen.dart), so all three keep the exact same look instead
// of drifting apart. None of these touch data/services — callers own all
// state and behaviour, these just render it consistently.

import 'package:flutter/material.dart';

// FIX (Task 1 — feed card corners consistency): in dono widgets pehle apna
// hardcoded `BorderRadius.circular(20)` use karte the, jabki feed card khud
// `home.dart` me shared `kLsRadius` (=18, `widgets/ls_ui.dart`) se banta
// hai. Ab dono ek hi radius-token use karte hain taaki card aur uske andar
// ke chips/pills ka corner-radius kabhi drift na kare.
import '../../widgets/ls_ui.dart' show kLsRadius;

/// Pill chip for hashtags / category / subcategory labels. Used by the
/// feed card's hashtag row and the single-post screen's category chips —
/// previously two separate, slightly different-looking implementations.
///
/// Pass [hashtag]: true to have the label prefixed with '#' (feed
/// hashtags); leave it false for a plain label (category/subcategory
/// chips, which already carry their own display text).
class PostTagChip extends StatelessWidget {
  final String label;
  final bool emphasized;
  final bool hashtag;
  final VoidCallback? onTap;
  const PostTagChip({
    super.key,
    required this.label,
    this.emphasized = false,
    this.hashtag = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final chip = Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: emphasized ? cs.primary.withOpacity(.12) : cs.surfaceVariant,
        borderRadius: BorderRadius.circular(kLsRadius),
      ),
      child: Text(
        hashtag ? '#$label' : label,
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w700,
          color: emphasized ? cs.primary : cs.onSurfaceVariant,
        ),
      ),
    );
    if (onTap == null) return chip;
    return InkWell(borderRadius: BorderRadius.circular(kLsRadius), onTap: onTap, child: chip);
  }
}

/// Carousel page-dot indicator — one look shared by the feed card's media
/// carousel and the single-post detail carousel (both used slightly
/// different hand-rolled versions before).
class PostCarouselDots extends StatelessWidget {
  final int count;
  final int activeIndex;

  /// `true` — dark rounded pill behind the dots (feed card, auto-hides).
  /// `false` — bare dots straight over the media (detail screen header).
  final bool pill;

  const PostCarouselDots({super.key, required this.count, required this.activeIndex, this.pill = true});

  @override
  Widget build(BuildContext context) {
    final dots = Row(
      mainAxisSize: MainAxisSize.min,
      children: List.generate(count, (i) {
        final active = i == activeIndex;
        return AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          margin: const EdgeInsets.symmetric(horizontal: 2.5),
          width: active ? 16 : 6,
          height: 6,
          decoration: BoxDecoration(
            color: active ? Colors.white : Colors.white54,
            borderRadius: BorderRadius.circular(4),
          ),
        );
      }),
    );
    if (!pill) return dots;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(kLsRadius)),
      child: dots,
    );
  }
}

/// Instagram/Threads-style double-tap-to-like: wrap a post's media with
/// this and a double tap plays a big heart burst and fires
/// [onDoubleTapLike]. Purely a gesture + animation layer — it never
/// decides what "like" means, so the caller is expected to only actually
/// mutate like-state when the post isn't already liked (a double tap on
/// an already-liked post should just replay the burst, not unlike it).
class DoubleTapLikeOverlay extends StatefulWidget {
  final Widget child;
  final VoidCallback onDoubleTapLike;
  const DoubleTapLikeOverlay({super.key, required this.child, required this.onDoubleTapLike});

  @override
  State<DoubleTapLikeOverlay> createState() => _DoubleTapLikeOverlayState();
}

class _DoubleTapLikeOverlayState extends State<DoubleTapLikeOverlay> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 650));
  late final Animation<double> _scale = TweenSequence<double>([
    TweenSequenceItem(tween: Tween(begin: 0.0, end: 1.15).chain(CurveTween(curve: Curves.easeOutBack)), weight: 45),
    TweenSequenceItem(tween: Tween(begin: 1.15, end: 1.0), weight: 15),
    TweenSequenceItem(tween: ConstantTween(1.0), weight: 20),
    TweenSequenceItem(tween: Tween(begin: 1.0, end: 0.7).chain(CurveTween(curve: Curves.easeIn)), weight: 20),
  ]).animate(_c);
  late final Animation<double> _opacity = TweenSequence<double>([
    TweenSequenceItem(tween: Tween(begin: 0.0, end: 1.0), weight: 12),
    TweenSequenceItem(tween: ConstantTween(1.0), weight: 58),
    TweenSequenceItem(tween: Tween(begin: 1.0, end: 0.0), weight: 30),
  ]).animate(_c);

  void _burst() {
    widget.onDoubleTapLike();
    _c.forward(from: 0);
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onDoubleTap: _burst,
      child: Stack(alignment: Alignment.center, children: [
        widget.child,
        IgnorePointer(
          child: FadeTransition(
            opacity: _opacity,
            child: ScaleTransition(
              scale: _scale,
              child: const Icon(
                Icons.favorite_rounded,
                color: Colors.white,
                size: 92,
                shadows: [Shadow(color: Colors.black45, blurRadius: 18)],
              ),
            ),
          ),
        ),
      ]),
    );
  }
}