import 'package:flutter/material.dart';

// ============================================================
// SKELETON / SHIMMER PLACEHOLDERS  — Task 10.1
//
// Jaan-boojh kar `shimmer` package use NAHI kiya:
//   • ye ~60 lines ka effect hai, ek aur dependency + uske Flutter-version
//     constraints add karne layak nahi;
//   • aur package wala shimmer apna fixed grey palette leke aata hai, jo
//     dark mode me theme se match nahi karta. Yahan base/highlight dono
//     ColorScheme se aate hain, to light aur dark dono me sahi lagta hai.
//
// Sab shapes real widgets ke exact dimensions match karti hain (classroom
// chip 40h, story ring 56, live card 200x172, post card) — warna data aane
// par layout jump karta hai (CLS), jo sabse zyada "sasta" feel deta hai.
// ============================================================

/// Shimmer sweep — apne children ke upar ek moving highlight gradient
/// paint karta hai. Children ko `LsSkeletonBox` hona chahiye (ya koi bhi
/// opaque shape), kyunki mask `srcATop` se lagta hai.
class LsShimmer extends StatefulWidget {
  final Widget child;
  const LsShimmer({super.key, required this.child});
  @override
  State<LsShimmer> createState() => _LsShimmerState();
}

class _LsShimmerState extends State<LsShimmer> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 1250))..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final base = cs.surfaceVariant;
    final highlight = Color.alphaBlend(cs.onSurface.withOpacity(.07), base);

    // Task 15.4 — reduced-motion respect. Animation off ho to static grey
    // blocks dikha do, sweep nahi.
    final reduceMotion = MediaQuery.maybeOf(context)?.disableAnimations ?? false;
    if (reduceMotion) return widget.child;

    return AnimatedBuilder(
      animation: _c,
      child: widget.child,
      builder: (context, child) => ShaderMask(
        blendMode: BlendMode.srcATop,
        shaderCallback: (bounds) => LinearGradient(
          begin: Alignment.centerLeft,
          end: Alignment.centerRight,
          colors: [base, highlight, base],
          stops: const [0.25, 0.5, 0.75],
          transform: _SweepTransform(_c.value),
        ).createShader(bounds),
        child: child,
      ),
    );
  }
}

class _SweepTransform extends GradientTransform {
  final double t;
  const _SweepTransform(this.t);
  @override
  Matrix4? transform(Rect bounds, {TextDirection? textDirection}) =>
      Matrix4.translationValues(bounds.width * (t * 2 - 1), 0, 0);
}

/// Ek plain rounded block. Color hamesha `surfaceVariant` — LsShimmer iske
/// upar sweep paint karta hai.
class LsSkeletonBox extends StatelessWidget {
  final double? width;
  final double height;
  final double radius;
  final EdgeInsetsGeometry? margin;
  const LsSkeletonBox({super.key, this.width, required this.height, this.radius = 8, this.margin});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: width,
      height: height,
      margin: margin,
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceVariant,
        borderRadius: BorderRadius.circular(radius),
      ),
    );
  }
}

// ---------- Section-specific shapes ----------

/// `.classroom-row` ka placeholder — 40px height, real chips jitni.
class LsClassroomRowSkeleton extends StatelessWidget {
  final double sidePad;
  const LsClassroomRowSkeleton({super.key, this.sidePad = 18});
  @override
  Widget build(BuildContext context) {
    const widths = [104.0, 118.0, 92.0, 78.0];
    return LsShimmer(
      child: SizedBox(
        height: 40,
        child: ListView(
          scrollDirection: Axis.horizontal,
          physics: const NeverScrollableScrollPhysics(),
          padding: EdgeInsets.symmetric(horizontal: sidePad),
          children: [
            for (final w in widths)
              LsSkeletonBox(width: w, height: 34, radius: 20, margin: const EdgeInsets.only(right: 8, top: 3)),
          ],
        ),
      ),
    );
  }
}

/// `.stories` ka placeholder — ring + label.
class LsStoriesSkeleton extends StatelessWidget {
  final double sidePad;
  const LsStoriesSkeleton({super.key, this.sidePad = 18});
  @override
  Widget build(BuildContext context) {
    return LsShimmer(
      child: SizedBox(
        height: 100,
        child: ListView.builder(
          scrollDirection: Axis.horizontal,
          physics: const NeverScrollableScrollPhysics(),
          padding: EdgeInsets.fromLTRB(sidePad, 2, sidePad, 4),
          itemCount: 5,
          itemBuilder: (_, __) => Container(
            width: 74,
            margin: const EdgeInsets.only(right: 14),
            child: const Column(children: [
              LsSkeletonBox(width: 72, height: 72, radius: 36),
              SizedBox(height: 7),
              LsSkeletonBox(width: 46, height: 8, radius: 4),
            ]),
          ),
        ),
      ),
    );
  }
}

/// `.live-row` ka placeholder — 200x172 cards.
class LsLiveRowSkeleton extends StatelessWidget {
  final double sidePad;
  const LsLiveRowSkeleton({super.key, this.sidePad = 18});
  @override
  Widget build(BuildContext context) {
    return LsShimmer(
      child: SizedBox(
        height: 216,
        child: ListView.builder(
          scrollDirection: Axis.horizontal,
          physics: const NeverScrollableScrollPhysics(),
          padding: EdgeInsets.symmetric(horizontal: sidePad),
          itemCount: 3,
          itemBuilder: (_, __) => const Padding(
            padding: EdgeInsets.only(right: 10),
            child: LsSkeletonBox(width: 200, height: 216, radius: 16),
          ),
        ),
      ),
    );
  }
}

/// `.invite-strip` ka placeholder.
class LsInviteStripSkeleton extends StatelessWidget {
  final double sidePad;
  const LsInviteStripSkeleton({super.key, this.sidePad = 18});
  @override
  Widget build(BuildContext context) {
    return LsShimmer(
      child: Padding(
        padding: EdgeInsets.fromLTRB(sidePad, 0, sidePad, 16),
        child: const LsSkeletonBox(height: 62, radius: 16),
      ),
    );
  }
}

/// `.post-card` ka placeholder — feed ke pehle load pe
/// CircularProgressIndicator ki jagah (Task 10.1).
class LsPostCardSkeleton extends StatelessWidget {
  final double sidePad;
  final bool withMedia;
  const LsPostCardSkeleton({super.key, this.sidePad = 18, this.withMedia = false});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return LsShimmer(
      child: Container(
        margin: EdgeInsets.fromLTRB(sidePad, 0, sidePad, 14),
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
        decoration: BoxDecoration(
          color: cs.surface,
          border: Border.all(color: cs.outlineVariant),
          borderRadius: BorderRadius.circular(18),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: const [
            LsSkeletonBox(width: 32, height: 32, radius: 16),
            SizedBox(width: 9),
            Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              LsSkeletonBox(width: 96, height: 10, radius: 5),
              SizedBox(height: 6),
              LsSkeletonBox(width: 62, height: 8, radius: 4),
            ]),
          ]),
          const SizedBox(height: 14),
          const LsSkeletonBox(height: 9, radius: 5),
          const SizedBox(height: 7),
          const LsSkeletonBox(height: 9, radius: 5),
          const SizedBox(height: 7),
          const LsSkeletonBox(width: 180, height: 9, radius: 5),
          if (withMedia) ...[
            const SizedBox(height: 12),
            const LsSkeletonBox(height: 160, radius: 12),
          ],
          const SizedBox(height: 14),
          Row(children: const [
            LsSkeletonBox(width: 54, height: 10, radius: 5),
            SizedBox(width: 16),
            LsSkeletonBox(width: 54, height: 10, radius: 5),
            SizedBox(width: 16),
            LsSkeletonBox(width: 54, height: 10, radius: 5),
          ]),
        ]),
      ),
    );
  }
}