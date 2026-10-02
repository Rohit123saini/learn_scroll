// lib/post/widgets/story_sticker_widgets.dart
//
// Stories upgrade, Part 2 — the ONE renderer for story overlays (mention /
// link / poll / question). The composer preview (`story_caption_sheet.dart`)
// and the viewer (`story_viewer_screen.dart`) both draw stickers through
// [StoryStickerView] + [StickerPlacement], so what the author sees while
// placing a sticker is exactly what viewers get.
//
// COORDINATES — backend stores x/y as the sticker CENTRE, 0..1 of the story
// canvas (`StorySticker` in story_model.dart). The canvas is the largest 9:16
// rectangle that fits the available area, centred ([StoryCanvas]). A sticker
// keeps its natural size at a 360-logical-px wide canvas and scales with the
// canvas width, so it looks the same on every screen size.

import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../models/story_model.dart';

/// Story canvas shape (width / height).
const double kStoryCanvasAspect = 9 / 16;

/// Canvas width at which stickers render at scale 1.0 / natural size.
const double kStoryCanvasBaseWidth = 360;

const Color kStickerAccent = Color(0xFF8B7CFF); // LearnScroll brand purple
const Color _kInk = Color(0xFF1C1C1E);
const Color _kSoftFill = Color(0xFFF1EEFF);

/// Largest 9:16 rectangle that fits inside [available].
Size storyCanvasSize(Size available) {
  final w = available.width;
  final h = available.height;
  if (w <= 0 || h <= 0) return Size.zero;
  if (w / h > kStoryCanvasAspect) return Size(h * kStoryCanvasAspect, h);
  return Size(w, w / kStoryCanvasAspect);
}

/// Centres a 9:16 box inside whatever space it is given and hands its size to
/// [builder]. Needs bounded constraints (always true inside a full-screen
/// `Stack(fit: StackFit.expand)`).
class StoryCanvas extends StatelessWidget {
  final Widget Function(BuildContext context, Size canvas) builder;
  const StoryCanvas({super.key, required this.builder});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, constraints) {
      final size = storyCanvasSize(Size(constraints.maxWidth, constraints.maxHeight));
      return Center(
        child: SizedBox(width: size.width, height: size.height, child: builder(context, size)),
      );
    });
  }
}

/// Puts [child] on the canvas: centre at (x, y) as 0..1 fractions, then
/// rotated (degrees) and scaled about that centre. Returns a [Positioned], so
/// it must be a direct child of a `Stack` that is exactly [canvas] sized.
class StickerPlacement extends StatelessWidget {
  final double x;
  final double y;
  final double rotation;
  final double scale;
  final Size canvas;
  final Widget child;

  const StickerPlacement({
    super.key,
    required this.x,
    required this.y,
    required this.rotation,
    required this.scale,
    required this.canvas,
    required this.child,
  });

  @override
  Widget build(BuildContext context) {
    final unit = canvas.width / kStoryCanvasBaseWidth;
    return Positioned(
      left: x * canvas.width,
      top: y * canvas.height,
      child: FractionalTranslation(
        translation: const Offset(-0.5, -0.5),
        child: Transform.rotate(
          angle: rotation * math.pi / 180,
          child: Transform.scale(scale: scale * unit, child: child),
        ),
      ),
    );
  }
}

/// Draws one [StorySticker] at its natural size. Pure presentation — every
/// interaction is a callback, and a callback left null means "not tappable"
/// (that is how the composer preview stays inert).
class StoryStickerView extends StatelessWidget {
  final StorySticker sticker;

  /// The signed-in user owns this story: polls always show results, and
  /// poll / question tap opens the responses list ([onResponsesTap]).
  final bool isOwner;

  /// A vote / answer request is in flight for this sticker.
  final bool busy;

  final ValueChanged<StorySticker>? onMentionTap;
  final ValueChanged<StorySticker>? onLinkTap;
  final void Function(StorySticker sticker, int option)? onVote;
  final ValueChanged<StorySticker>? onQuestionTap;
  final ValueChanged<StorySticker>? onResponsesTap;

  const StoryStickerView({
    super.key,
    required this.sticker,
    this.isOwner = false,
    this.busy = false,
    this.onMentionTap,
    this.onLinkTap,
    this.onVote,
    this.onQuestionTap,
    this.onResponsesTap,
  });

  static const List<BoxShadow> _shadow = [BoxShadow(color: Colors.black26, blurRadius: 8, offset: Offset(0, 2))];

  @override
  Widget build(BuildContext context) {
    switch (sticker.kind) {
      case StorySticker.kMention:
        return _tappable(_buildMention(), onMentionTap);
      case StorySticker.kLink:
        return _tappable(_buildLink(), onLinkTap);
      case StorySticker.kPoll:
        return isOwner ? _tappable(_buildPoll(context), onResponsesTap) : _buildPoll(context);
      case StorySticker.kQuestion:
        return _tappable(_buildQuestion(context), isOwner ? onResponsesTap : onQuestionTap);
      default:
        return const SizedBox.shrink();
    }
  }

  Widget _tappable(Widget child, ValueChanged<StorySticker>? onTap) {
    if (onTap == null) return child;
    return GestureDetector(behavior: HitTestBehavior.opaque, onTap: () => onTap(sticker), child: child);
  }

  // ---- mention ----
  Widget _buildMention() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(10), boxShadow: _shadow),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 240),
        child: Text(
          '@${sticker.mentionUsername ?? ''}',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(color: _kInk, fontSize: 18, fontWeight: FontWeight.w800),
        ),
      ),
    );
  }

  // ---- link ----
  Widget _buildLink() {
    final text = sticker.label.isNotEmpty ? sticker.label : sticker.host;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12), boxShadow: _shadow),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 260),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.link_rounded, size: 20, color: kStickerAccent),
            const SizedBox(width: 6),
            Flexible(
              child: Text(
                text,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: _kInk, fontSize: 15, fontWeight: FontWeight.w700),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // ---- poll ----
  Widget _buildPoll(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final options = sticker.pollOptions;
    final counts = sticker.pollCounts;
    final showResults = counts != null;
    final total = sticker.pollTotal;
    final mine = sticker.myVote;

    return Container(
      width: 250,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(16), boxShadow: _shadow),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            sticker.pollQuestion,
            textAlign: TextAlign.center,
            style: const TextStyle(color: _kInk, fontSize: 15, fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 10),
          for (int i = 0; i < options.length; i++)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: counts != null
                  ? _resultRow(options[i], i < counts.length ? counts[i] : 0, total, mine == i)
                  : _voteRow(i, options[i]),
            ),
          if (showResults)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(
                isOwner && onResponsesTap != null
                    ? '${l10n.stickerPollVotes(total)} · ${l10n.stickerOwnerTapToView}'
                    : l10n.stickerPollVotes(total),
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.black54, fontSize: 11.5, fontWeight: FontWeight.w600),
              ),
            ),
        ],
      ),
    );
  }

  Widget _voteRow(int index, String label) {
    final enabled = onVote != null && !busy;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: enabled ? () => onVote!(sticker, index) : null,
      child: Opacity(
        opacity: busy ? 0.6 : 1,
        child: Container(
          height: 38,
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: 10),
          decoration: BoxDecoration(
            color: _kSoftFill,
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: kStickerAccent.withOpacity(0.5)),
          ),
          child: Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(color: _kInk, fontSize: 13.5, fontWeight: FontWeight.w700),
          ),
        ),
      ),
    );
  }

  Widget _resultRow(String label, int count, int total, bool isMine) {
    final fraction = total > 0 ? (count / total).clamp(0.0, 1.0).toDouble() : 0.0;
    return ClipRRect(
      borderRadius: BorderRadius.circular(10),
      child: SizedBox(
        height: 38,
        child: DecoratedBox(
          decoration: const BoxDecoration(color: _kSoftFill),
          child: Stack(
            children: [
              Positioned.fill(
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: FractionallySizedBox(
                    widthFactor: fraction,
                    heightFactor: 1,
                    child: ColoredBox(color: kStickerAccent.withOpacity(isMine ? 0.55 : 0.25)),
                  ),
                ),
              ),
              Positioned.fill(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 10),
                  child: Row(
                    children: [
                      if (isMine) ...[
                        const Icon(Icons.check_circle_rounded, size: 15, color: _kInk),
                        const SizedBox(width: 4),
                      ],
                      Expanded(
                        child: Text(
                          label,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: _kInk, fontSize: 13.5, fontWeight: FontWeight.w700),
                        ),
                      ),
                      Text(
                        '${(fraction * 100).round()}%',
                        style: const TextStyle(color: _kInk, fontSize: 12.5, fontWeight: FontWeight.w800),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ---- question ----
  Widget _buildQuestion(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final String sub;
    final IconData icon;
    if (isOwner) {
      final c = sticker.answersCount;
      sub = c == null ? l10n.stickerOwnerTapToView : l10n.stickerQuestionAnswersCount(c);
      icon = Icons.forum_outlined;
    } else if (sticker.myAnswered) {
      sub = l10n.stickerQuestionAnswered;
      icon = Icons.check_circle_rounded;
    } else {
      sub = l10n.stickerQuestionTapToAnswer;
      icon = Icons.edit_outlined;
    }

    return Container(
      width: 250,
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(16), boxShadow: _shadow),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(
              color: kStickerAccent,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
              child: Text(
                sticker.prompt,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w800),
              ),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(icon, size: 16, color: Colors.black54),
                  const SizedBox(width: 6),
                  Flexible(
                    child: Text(
                      sub,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: Colors.black54, fontSize: 13, fontWeight: FontWeight.w600),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
