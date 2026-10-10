// ============================================================
// REELS — overlay widgets (P13).
//
//   ReelActionRail      right rail: like, comment, share, save, more
//   ReelInfoOverlay     bottom: author + Follow, expandable caption, hashtags
//   showReelMoreSheet   "more" menu: Not interested / Show fewer like this / Why am I seeing this
//   showReelWhySheet    "Why am I seeing this" (FeedFeedbackService.why)
//
// These widgets are dumb: they only render a [Reel] and call [ReelActions]. All
// state changes (optimistic like / save / follow, removal, ...) live in
// reels_screen.dart.
// ============================================================

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../services/feed_feedback_service.dart';
import '../services/reels_service.dart';

const List<Shadow> _kShadow = [Shadow(blurRadius: 6, color: Colors.black54)];
const Color _kLikeRed = Color(0xFFFF3B5C);

/// 999 -> "999", 1200 -> "1.2K", 15300 -> "15K", 2400000 -> "2.4M".
String reelCompactCount(int n) {
  if (n < 1000) return '$n';
  String trim(double v, int digits) => v.toStringAsFixed(digits).replaceAll(RegExp(r'\.0$'), '');
  if (n < 999500) {
    final v = n / 1000;
    return '${trim(v, v >= 100 ? 0 : 1)}K';
  }
  return '${trim(n / 1000000, 1)}M';
}

/// Everything the overlays can trigger. Built by the screen for one reel.
class ReelActions {
  final VoidCallback onLike;
  final VoidCallback onComment;
  final VoidCallback onShare;
  final VoidCallback onSave;
  final VoidCallback onMore;
  final VoidCallback onAuthorTap;
  final VoidCallback onFollow;
  final ValueChanged<String> onHashtag; // tag without '#'
  const ReelActions({
    required this.onLike,
    required this.onComment,
    required this.onShare,
    required this.onSave,
    required this.onMore,
    required this.onAuthorTap,
    required this.onFollow,
    required this.onHashtag,
  });
}

// ============================================================ right rail

class ReelActionRail extends StatelessWidget {
  final Reel reel;
  final ReelActions actions;
  const ReelActionRail({super.key, required this.reel, required this.actions});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final liked = reel.isLiked;
    return Column(mainAxisSize: MainAxisSize.min, children: [
      _RailButton(
        label: l10n.like,
        count: reel.counts.likes,
        onTap: actions.onLike,
        icon: AnimatedSwitcher(
          duration: const Duration(milliseconds: 160),
          transitionBuilder: (child, anim) => ScaleTransition(scale: anim, child: child),
          child: Icon(
            liked ? Icons.favorite_rounded : Icons.favorite_border_rounded,
            key: ValueKey<bool>(liked),
            color: liked ? _kLikeRed : Colors.white,
            size: 32,
            shadows: _kShadow,
          ),
        ),
      ),
      _RailButton(
        label: l10n.comment,
        count: reel.counts.comments,
        onTap: actions.onComment,
        icon: const Icon(Icons.mode_comment_outlined, color: Colors.white, size: 30, shadows: _kShadow),
      ),
      _RailButton(
        label: l10n.share,
        count: reel.counts.shares,
        onTap: actions.onShare,
        icon: const Icon(Icons.share_rounded, color: Colors.white, size: 29, shadows: _kShadow),
      ),
      _RailButton(
        label: l10n.save,
        count: reel.counts.saves,
        onTap: actions.onSave,
        icon: Icon(
          reel.isSaved ? Icons.bookmark_rounded : Icons.bookmark_border_rounded,
          color: Colors.white,
          size: 31,
          shadows: _kShadow,
        ),
      ),
      _RailButton(
        label: l10n.moreOptions,
        count: 0,
        onTap: actions.onMore,
        icon: const Icon(Icons.more_horiz_rounded, color: Colors.white, size: 30, shadows: _kShadow),
      ),
    ]);
  }
}

class _RailButton extends StatelessWidget {
  final Widget icon;
  final int count;
  final String label;
  final VoidCallback onTap;
  const _RailButton({required this.icon, required this.count, required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: label,
      child: InkResponse(
        onTap: onTap,
        radius: 30,
        child: ConstrainedBox(
          constraints: const BoxConstraints(minWidth: 52, minHeight: 52),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              icon,
              if (count > 0) ...[
                const SizedBox(height: 2),
                Text(
                  reelCompactCount(count),
                  style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600, shadows: _kShadow),
                ),
              ],
            ]),
          ),
        ),
      ),
    );
  }
}

// ============================================================ bottom info

class ReelInfoOverlay extends StatefulWidget {
  final Reel reel;

  /// Hide the Follow button (own reel).
  final bool showFollow;

  /// A follow request to a private account is waiting -> "Requested".
  final bool followPending;
  final ReelActions actions;
  const ReelInfoOverlay({
    super.key,
    required this.reel,
    required this.showFollow,
    required this.followPending,
    required this.actions,
  });

  @override
  State<ReelInfoOverlay> createState() => _ReelInfoOverlayState();
}

class _ReelInfoOverlayState extends State<ReelInfoOverlay> {
  static const int _collapsedLines = 2;
  static const int _collapsedTags = 3;
  static const TextStyle _captionStyle =
      TextStyle(color: Colors.white, fontSize: 14, height: 1.3, shadows: _kShadow);

  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final reel = widget.reel;
    final a = reel.author;
    final photo = a.profilePhoto;
    final tags = reel.hashtags.where((t) => t.isNotEmpty).toList();
    final shownTags = _expanded ? tags : tags.take(_collapsedTags).toList();
    final maxCaptionHeight = MediaQuery.of(context).size.height * 0.32;

    return Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
      // ---- author + Follow
      Row(children: [
        Flexible(
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: widget.actions.onAuthorTap,
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              CircleAvatar(
                radius: 17,
                backgroundColor: Colors.white24,
                backgroundImage: (photo != null && photo.isNotEmpty) ? CachedNetworkImageProvider(photo) : null,
                child: (photo == null || photo.isEmpty)
                    ? Text(a.displayName.isNotEmpty ? a.displayName[0].toUpperCase() : '?',
                        style: const TextStyle(color: Colors.white, fontSize: 14))
                    : null,
              ),
              const SizedBox(width: 10),
              Flexible(
                child: Text(
                  a.displayName,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w700, shadows: _kShadow),
                ),
              ),
            ]),
          ),
        ),
        if (widget.showFollow) ...[
          const SizedBox(width: 10),
          _FollowButton(
            label: widget.followPending ? l10n.requestedLabel : (a.isFollowing ? l10n.following : l10n.follow),
            filled: !a.isFollowing && !widget.followPending,
            onTap: widget.actions.onFollow,
          ),
        ],
      ]),

      // ---- caption (2 lines, expandable)
      if (reel.caption.trim().isNotEmpty) ...[
        const SizedBox(height: 8),
        LayoutBuilder(builder: (context, cons) {
          final painter = TextPainter(
            text: TextSpan(text: reel.caption, style: _captionStyle),
            maxLines: _collapsedLines,
            textDirection: Directionality.of(context),
            textScaler: MediaQuery.textScalerOf(context),
          )..layout(maxWidth: cons.maxWidth);
          final overflows = painter.didExceedMaxLines;
          final text = Text(
            reel.caption,
            maxLines: _expanded ? null : _collapsedLines,
            overflow: _expanded ? TextOverflow.visible : TextOverflow.ellipsis,
            style: _captionStyle,
          );
          return GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: overflows ? () => setState(() => _expanded = !_expanded) : null,
            child: AnimatedSize(
              duration: const Duration(milliseconds: 180),
              alignment: Alignment.topLeft,
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                _expanded
                    ? ConstrainedBox(constraints: BoxConstraints(maxHeight: maxCaptionHeight), child: SingleChildScrollView(child: text))
                    : text,
                if (overflows)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      _expanded ? l10n.reelCaptionLess : l10n.reelCaptionMore,
                      style: const TextStyle(color: Colors.white70, fontSize: 13, fontWeight: FontWeight.w600, shadows: _kShadow),
                    ),
                  ),
              ]),
            ),
          );
        }),
      ],

      // ---- hashtags -> hashtag feed
      if (shownTags.isNotEmpty) ...[
        const SizedBox(height: 2),
        Wrap(spacing: 10, children: [
          for (final t in shownTags)
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => widget.actions.onHashtag(t),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Text('#$t', style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600, shadows: _kShadow)),
              ),
            ),
          if (!_expanded && tags.length > _collapsedTags)
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => setState(() => _expanded = true),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Text('+${tags.length - _collapsedTags}', style: const TextStyle(color: Colors.white70, fontSize: 13, shadows: _kShadow)),
              ),
            ),
        ]),
      ],
    ]);
  }
}

class _FollowButton extends StatelessWidget {
  final String label;
  final bool filled;
  final VoidCallback onTap;
  const _FollowButton({required this.label, required this.filled, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: label,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
          decoration: BoxDecoration(
            color: filled ? Colors.white : Colors.transparent,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: Colors.white, width: 1.2),
          ),
          child: Text(
            label,
            style: TextStyle(color: filled ? Colors.black : Colors.white, fontSize: 13, fontWeight: FontWeight.w700),
          ),
        ),
      ),
    );
  }
}

// ============================================================ more menu

enum ReelMoreAction { notInterested, showFewer, why }

/// [canHide] = false for the user's own reel (server rejects Not interested / Show fewer for it).
Future<ReelMoreAction?> showReelMoreSheet(BuildContext context, {required bool canHide}) {
  final l10n = AppLocalizations.of(context)!;
  return showModalBottomSheet<ReelMoreAction>(
    context: context,
    showDragHandle: true,
    useSafeArea: true,
    builder: (ctx) => SafeArea(
      top: false,
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        if (canHide) ...[
          ListTile(
            leading: const Icon(Icons.visibility_off_outlined),
            title: Text(l10n.reelNotInterested),
            onTap: () => Navigator.pop(ctx, ReelMoreAction.notInterested),
          ),
          ListTile(
            leading: const Icon(Icons.thumb_down_alt_outlined),
            title: Text(l10n.reelShowFewer),
            onTap: () => Navigator.pop(ctx, ReelMoreAction.showFewer),
          ),
        ],
        ListTile(
          leading: const Icon(Icons.help_outline_rounded),
          title: Text(l10n.reelWhySeeing),
          onTap: () => Navigator.pop(ctx, ReelMoreAction.why),
        ),
        const SizedBox(height: 8),
      ]),
    ),
  );
}

// ============================================================ why sheet

Future<void> showReelWhySheet(BuildContext context, String postId) {
  return showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    useSafeArea: true,
    isScrollControlled: true,
    builder: (_) => _WhySheet(postId: postId),
  );
}

class _WhySheet extends StatefulWidget {
  final String postId;
  const _WhySheet({required this.postId});

  @override
  State<_WhySheet> createState() => _WhySheetState();
}

class _WhySheetState extends State<_WhySheet> {
  late Future<WhyResult> _future = FeedFeedbackService.why(widget.postId);

  static IconData _icon(String code) {
    switch (code) {
      case 'following':
        return Icons.person_add_alt_1_outlined;
      case 'trending':
        return Icons.trending_up_rounded;
      case 'interest_category':
        return Icons.category_outlined;
      case 'liked_category':
        return Icons.favorite_border_rounded;
      case 'friend_of_follow':
        return Icons.group_outlined;
      case 'popular':
        return Icons.local_fire_department_outlined;
      // T1 Parts 3-4 — exploration, behaviour and the educational lens
      case 'new_creator':
        return Icons.auto_awesome_outlined;
      case 'educational_topic':
      case 'study_time':
        return Icons.school_outlined;
      case 'campus_context':
      case 'class_context':
        return Icons.apartment_outlined;
      case 'engaged_author':
      case 'author_affinity':
        return Icons.timer_outlined;
      case 'own_post':
        return Icons.person_outline_rounded;
      default:
        return Icons.info_outline_rounded;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return SafeArea(
      top: false,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.6),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.reelWhySeeing, style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
            const SizedBox(height: 12),
            Flexible(
              child: FutureBuilder<WhyResult>(
                future: _future,
                builder: (context, snap) {
                  if (snap.connectionState != ConnectionState.done) {
                    return const Padding(
                      padding: EdgeInsets.symmetric(vertical: 28),
                      child: Center(child: SizedBox(width: 26, height: 26, child: CircularProgressIndicator(strokeWidth: 2.4))),
                    );
                  }
                  if (snap.hasError || snap.data == null) {
                    return Padding(
                      padding: const EdgeInsets.symmetric(vertical: 16),
                      child: Column(mainAxisSize: MainAxisSize.min, children: [
                        Text(l10n.somethingWentWrong, style: TextStyle(color: cs.onSurfaceVariant)),
                        const SizedBox(height: 8),
                        OutlinedButton(
                          onPressed: () => setState(() => _future = FeedFeedbackService.why(widget.postId)),
                          child: Text(l10n.retry),
                        ),
                      ]),
                    );
                  }
                  final reasons = snap.data!.reasons;
                  if (reasons.isEmpty) return const SizedBox.shrink();
                  return ListView.separated(
                    shrinkWrap: true,
                    itemCount: reasons.length,
                    separatorBuilder: (_, __) => const SizedBox(height: 14),
                    itemBuilder: (_, i) => Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Icon(_icon(reasons[i].code), size: 22, color: cs.primary),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          reasons[i].text,
                          style: TextStyle(fontSize: 14.5, height: 1.35, fontWeight: i == 0 ? FontWeight.w600 : FontWeight.w400),
                        ),
                      ),
                    ]),
                  );
                },
              ),
            ),
          ]),
        ),
      ),
    );
  }
}
