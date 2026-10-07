import 'package:flutter/material.dart';
import '../post/widgets/post_media_ratio.dart';

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

/// TASK G16 — generic list-row placeholder: avatar + title line + subtitle
/// line, trailing optional. Feed/campus/testseries already had their own
/// bespoke skeleton shapes before this task; the modules that were still
/// falling back to a bare `CircularProgressIndicator` on cold start
/// (conversations list, notifications, study-groups discovery, leaderboard
/// rows, etc.) were doing so because there was no reusable *generic* row
/// shape in this file to reach for — every existing shape here is
/// purpose-built for one specific card. This one and `LsGridCardSkeleton`
/// below are meant to be the default reach-for-it shapes for any new
/// "avatar + two lines" or "square card in a grid" list, so the next
/// screen that needs a skeleton doesn't reinvent one.
class LsListRowSkeleton extends StatelessWidget {
  final double sidePad;
  final double avatarSize;
  final bool trailingChip;
  const LsListRowSkeleton({super.key, this.sidePad = 18, this.avatarSize = 44, this.trailingChip = false});

  @override
  Widget build(BuildContext context) {
    return LsShimmer(
      child: Padding(
        padding: EdgeInsets.symmetric(horizontal: sidePad, vertical: 8),
        child: Row(children: [
          LsSkeletonBox(width: avatarSize, height: avatarSize, radius: avatarSize / 2),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              LsSkeletonBox(width: 150, height: 11, radius: 5),
              const SizedBox(height: 8),
              LsSkeletonBox(width: 100, height: 9, radius: 4),
            ]),
          ),
          if (trailingChip) ...[
            const SizedBox(width: 10),
            const LsSkeletonBox(width: 44, height: 20, radius: 10),
          ],
        ]),
      ),
    );
  }
}

/// Convenience: N rows of [LsListRowSkeleton] stacked — the shape almost
/// every cold-start "list of things" screen wants (conversations,
/// notifications, discovery tabs, leaderboards).
class LsListSkeleton extends StatelessWidget {
  final int count;
  final double sidePad;
  final bool trailingChip;
  const LsListSkeleton({super.key, this.count = 6, this.sidePad = 18, this.trailingChip = false});

  @override
  Widget build(BuildContext context) {
    return Column(
      children: List.generate(
        count,
        (_) => LsListRowSkeleton(sidePad: sidePad, trailingChip: trailingChip),
      ),
    );
  }
}

/// Generic square-ish card for a grid (wallet offers, campus section tiles,
/// leaderboard podium cards) — same reasoning as `LsListRowSkeleton` above.
class LsGridCardSkeleton extends StatelessWidget {
  final double height;
  const LsGridCardSkeleton({super.key, this.height = 96});

  @override
  Widget build(BuildContext context) {
    return LsShimmer(
      child: LsSkeletonBox(height: height, radius: 14),
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
            // 1.2-FE — was a fixed 160px box; now the same default ratio the real
            // frame uses, so the card doesn't jump when the post loads.
            AspectRatio(
              aspectRatio: kPostMediaDefaultRatio,
              child: Container(
                decoration: BoxDecoration(
                  color: cs.surfaceVariant,
                  borderRadius: BorderRadius.circular(12),
                ),
              ),
            ),
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

// ============================================================
// TASK C3 — shared skeletons for profile / grid screens.
//
// Naya design nahi banaya: sab kuch upar wale LsShimmer + LsSkeletonBox pe
// bana hai (same palette, same reduced-motion handling). List-tile ke liye
// alag widget JAAN-BOOJH kar nahi add kiya — `LsListRowSkeleton` /
// `LsListSkeleton` (avatar + 2 lines + optional trailing chip) wahi kaam
// already karta hai; dobara banana duplicate hota.
// ============================================================

/// Profile screen / grid ke liye post-thumbnail grid placeholder.
///
/// Poora grid EK hi [LsShimmer] ke andar hai, to ek hi AnimationController
/// chalta hai (har tile ka alag controller nahi). `shrinkWrap` + non-scroll
/// physics hai, isliye ise kisi bhi scroll view (NestedScrollView, Column
/// in SingleChildScrollView, sliver via SliverToBoxAdapter) me rakh sakte ho.
class LsPostGridSkeleton extends StatelessWidget {
  final int count;
  final int crossAxisCount;
  final double spacing;
  final double sidePad;
  final double aspectRatio;
  const LsPostGridSkeleton({
    super.key,
    this.count = 12,
    this.crossAxisCount = 3,
    this.spacing = 2,
    this.sidePad = 0,
    this.aspectRatio = 1,
  });

  @override
  Widget build(BuildContext context) {
    return LsShimmer(
      child: GridView.builder(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        padding: EdgeInsets.symmetric(horizontal: sidePad),
        itemCount: count,
        gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: crossAxisCount,
          mainAxisSpacing: spacing,
          crossAxisSpacing: spacing,
          childAspectRatio: aspectRatio,
        ),
        itemBuilder: (_, __) => const LsSkeletonBox(height: double.infinity, radius: 2),
      ),
    );
  }
}

/// Profile header placeholder: avatar + 3 stat columns, name, bio lines,
/// action button. Dimensions real header ke nazdeek rakhe hain (avatar 86);
/// agar real profile header ka avatar/padding alag ho to sirf
/// [avatarSize] / [sidePad] pass karke match kar lo.
class LsProfileHeaderSkeleton extends StatelessWidget {
  final double sidePad;
  final double avatarSize;
  final bool showActionButton;
  const LsProfileHeaderSkeleton({
    super.key,
    this.sidePad = 18,
    this.avatarSize = 86,
    this.showActionButton = true,
  });

  @override
  Widget build(BuildContext context) {
    return LsShimmer(
      child: Padding(
        padding: EdgeInsets.fromLTRB(sidePad, 12, sidePad, 16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            LsSkeletonBox(width: avatarSize, height: avatarSize, radius: avatarSize / 2),
            const SizedBox(width: 20),
            const Expanded(
              child: Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
                _StatSkeleton(),
                _StatSkeleton(),
                _StatSkeleton(),
              ]),
            ),
          ]),
          const SizedBox(height: 14),
          const LsSkeletonBox(width: 130, height: 12, radius: 6),
          const SizedBox(height: 8),
          const LsSkeletonBox(height: 9, radius: 5),
          const SizedBox(height: 6),
          const LsSkeletonBox(width: 210, height: 9, radius: 5),
          if (showActionButton) ...[
            const SizedBox(height: 14),
            const LsSkeletonBox(height: 36, radius: 10),
          ],
        ]),
      ),
    );
  }
}

class _StatSkeleton extends StatelessWidget {
  const _StatSkeleton();
  @override
  Widget build(BuildContext context) => const Column(children: [
        LsSkeletonBox(width: 34, height: 14, radius: 6),
        SizedBox(height: 6),
        LsSkeletonBox(width: 48, height: 9, radius: 4),
      ]);
}