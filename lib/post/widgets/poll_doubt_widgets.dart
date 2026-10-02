// lib/post/widgets/poll_doubt_widgets.dart
//
// TASK G6 (growth_and_feature_tasks.md) — "Polls & Q&A post types".
//
// Poll CREATION already existed (new_post.dart's poll toolbar button +
// PostCreateSerializer's `poll_options`), but nothing rendered a poll in
// the feed or let a viewer vote — `PostPollVoteAPIView` (backend) is the
// endpoint this widget calls. "Ask a doubt" is entirely new end-to-end:
// question = the post's own `content` (rendered by the feed card as
// normal), this file adds the "N answers" / best-answer summary on the
// card plus the full answer list + best-answer pin in a bottom sheet.

import 'package:flutter/material.dart';
import 'package:timeago/timeago.dart' as timeago;
import '../../services/home_api_model_service.dart';

/// Inline poll card — shown under a post's text/media whenever
/// `post.poll != null` (works on any post_type, matching how a poll can
/// ride along on a text/image/video post per the composer).
class PollCardWidget extends StatefulWidget {
  final String postId;
  final PostPollModel poll;
  final ValueChanged<PostPollModel>? onVoted;
  const PollCardWidget({super.key, required this.postId, required this.poll, this.onVoted});

  @override
  State<PollCardWidget> createState() => _PollCardWidgetState();
}

class _PollCardWidgetState extends State<PollCardWidget> {
  late PostPollModel _poll;
  bool _voting = false;

  @override
  void initState() {
    super.initState();
    _poll = widget.poll;
  }

  @override
  void didUpdateWidget(covariant PollCardWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    // A feed refresh may hand us a fresh PostModel — pick up its poll
    // state unless we're mid-vote (avoid a race where a background
    // refresh clobbers an optimistic UI update).
    if (!_voting && oldWidget.poll.id == widget.poll.id) {
      _poll = widget.poll;
    }
  }

  Future<void> _vote(String optionId) async {
    if (_voting || _poll.isExpired) return;
    final previous = _poll;
    setState(() => _voting = true);
    try {
      final updated = await HomeFeedService.votePoll(widget.postId, optionId);
      if (!mounted) return;
      setState(() => _poll = updated);
      widget.onVoted?.call(updated);
    } catch (_) {
      if (!mounted) return;
      setState(() => _poll = previous);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Couldn't submit your vote. Try again.")),
      );
    } finally {
      if (mounted) setState(() => _voting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final hasVoted = _poll.myVoteOptionId != null;
    final total = _poll.totalVotesCount;

    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ..._poll.options.map((opt) {
            final pct = total == 0 ? 0.0 : opt.votesCount / total;
            final isMine = _poll.myVoteOptionId == opt.id;
            return Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: GestureDetector(
                onTap: () => _vote(opt.id),
                child: Stack(
                  children: [
                    Container(
                      height: 40,
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(color: isMine ? cs.primary : cs.outlineVariant),
                        color: cs.surfaceVariant.withOpacity(.35),
                      ),
                      child: hasVoted
                          ? FractionallySizedBox(
                              alignment: Alignment.centerLeft,
                              widthFactor: pct.clamp(0.0, 1.0),
                              child: Container(
                                decoration: BoxDecoration(
                                  borderRadius: BorderRadius.circular(10),
                                  color: (isMine ? cs.primary : cs.primary.withOpacity(.35))
                                      .withOpacity(isMine ? .22 : .18),
                                ),
                              ),
                            )
                          : null,
                    ),
                    Positioned.fill(
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        child: Row(
                          children: [
                            if (isMine)
                              Padding(
                                padding: const EdgeInsets.only(right: 6),
                                child: Icon(Icons.check_circle, size: 15, color: cs.primary),
                              ),
                            Expanded(
                              child: Text(
                                opt.text,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                  fontSize: 12.5,
                                  fontWeight: isMine ? FontWeight.w800 : FontWeight.w600,
                                  color: cs.onSurface,
                                ),
                              ),
                            ),
                            if (hasVoted)
                              Text(
                                '${(pct * 100).round()}%',
                                style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant),
                              ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            );
          }),
          Text(
            _poll.isExpired
                ? 'Poll ended · $total ${total == 1 ? 'vote' : 'votes'}'
                : '$total ${total == 1 ? 'vote' : 'votes'}${hasVoted ? '' : ' · tap to vote'}',
            style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant),
          ),
        ],
      ),
    );
  }
}

/// Compact summary shown on a doubt-post's feed card: question is already
/// rendered as the post's normal text — this just adds the "N answers" /
/// best-answer strip + a button that opens the full answer sheet.
class DoubtSummaryWidget extends StatelessWidget {
  final int answersCount;
  final PostAnswerModel? bestAnswer;
  final VoidCallback onTap;
  const DoubtSummaryWidget({super.key, required this.answersCount, this.bestAnswer, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: cs.tertiaryContainer.withOpacity(.25),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: cs.outlineVariant),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(Icons.help_outline_rounded, size: 15, color: cs.tertiary),
                  const SizedBox(width: 6),
                  Text('Doubt', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, color: cs.tertiary)),
                  const Spacer(),
                  Text(
                    answersCount == 0 ? 'No answers yet' : '$answersCount ${answersCount == 1 ? 'answer' : 'answers'}',
                    style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant),
                  ),
                  const SizedBox(width: 4),
                  Icon(Icons.chevron_right_rounded, size: 16, color: cs.onSurfaceVariant),
                ],
              ),
              if (bestAnswer != null) ...[
                const SizedBox(height: 6),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(Icons.verified_rounded, size: 14, color: Colors.green.shade600),
                    const SizedBox(width: 5),
                    Expanded(
                      child: Text(
                        bestAnswer!.content,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(fontSize: 12, color: cs.onSurface.withOpacity(.85)),
                      ),
                    ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// Full "View & Answer" bottom sheet for a doubt post — lists answers
/// (best-pinned first, per PostAnswer.Meta.ordering), lets any signed-in
/// user add an answer, and lets the doubt's own author pin/unpin the
/// best answer.
class DoubtAnswersSheet extends StatefulWidget {
  final String postId;
  final bool isOwnPost; // can this viewer pin a best answer?
  final ValueChanged<int>? onAnswersCountChanged;
  const DoubtAnswersSheet({
    super.key,
    required this.postId,
    required this.isOwnPost,
    this.onAnswersCountChanged,
  });

  static Future<void> open(
    BuildContext context, {
    required String postId,
    required bool isOwnPost,
    ValueChanged<int>? onAnswersCountChanged,
  }) {
    return showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => DraggableScrollableSheet(
        initialChildSize: 0.75,
        minChildSize: 0.5,
        maxChildSize: 0.95,
        expand: false,
        builder: (context, scrollController) => DoubtAnswersSheet(
          postId: postId,
          isOwnPost: isOwnPost,
          onAnswersCountChanged: onAnswersCountChanged,
        ),
      ),
    );
  }

  @override
  State<DoubtAnswersSheet> createState() => _DoubtAnswersSheetState();
}

class _DoubtAnswersSheetState extends State<DoubtAnswersSheet> {
  final _controller = TextEditingController();
  List<PostAnswerModel>? _answers;
  bool _loading = true;
  bool _submitting = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final answers = await HomeFeedService.getAnswers(widget.postId);
      if (!mounted) return;
      setState(() {
        _answers = answers;
        _loading = false;
      });
      widget.onAnswersCountChanged?.call(answers.length);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = "Couldn't load answers.";
        _loading = false;
      });
    }
  }

  Future<void> _submit() async {
    final text = _controller.text.trim();
    if (text.isEmpty || _submitting) return;
    setState(() => _submitting = true);
    try {
      final answer = await HomeFeedService.postAnswer(widget.postId, text);
      if (!mounted) return;
      setState(() {
        _answers = [...?_answers, answer];
        _controller.clear();
      });
      widget.onAnswersCountChanged?.call(_answers!.length);
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Couldn't post your answer. Try again.")),
      );
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  Future<void> _toggleBest(PostAnswerModel answer) async {
    try {
      await HomeFeedService.markBestAnswer(answer.id);
      if (!mounted) return;
      setState(() {
        final wasBest = answer.isBestAnswer;
        for (final a in _answers ?? <PostAnswerModel>[]) {
          a.isBestAnswer = false;
        }
        answer.isBestAnswer = !wasBest;
        // Best answer floats to the top, matching the backend's ordering.
        _answers = [
          ...(_answers ?? []).where((a) => a.isBestAnswer),
          ...(_answers ?? []).where((a) => !a.isBestAnswer),
        ];
      });
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Couldn't update the best answer.")),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(18)),
      ),
      child: Column(
        children: [
          const SizedBox(height: 10),
          Container(width: 36, height: 4, decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2))),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
            child: Row(
              children: [
                Icon(Icons.help_outline_rounded, size: 18, color: cs.tertiary),
                const SizedBox(width: 8),
                Text('Answers', style: TextStyle(fontSize: 15, fontWeight: FontWeight.w800, color: cs.onSurface)),
              ],
            ),
          ),
          const Divider(height: 1),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                    ? Center(child: Text(_error!, style: TextStyle(color: cs.onSurfaceVariant)))
                    : (_answers == null || _answers!.isEmpty)
                        ? Center(
                            child: Text(
                              'No answers yet — be the first to help out.',
                              style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13),
                            ),
                          )
                        : ListView.separated(
                            padding: const EdgeInsets.all(14),
                            itemCount: _answers!.length,
                            separatorBuilder: (_, __) => const SizedBox(height: 10),
                            itemBuilder: (context, i) {
                              final a = _answers![i];
                              return Container(
                                padding: const EdgeInsets.all(12),
                                decoration: BoxDecoration(
                                  color: a.isBestAnswer ? Colors.green.withOpacity(.07) : cs.surfaceVariant.withOpacity(.3),
                                  borderRadius: BorderRadius.circular(12),
                                  border: a.isBestAnswer ? Border.all(color: Colors.green.shade400) : null,
                                ),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Row(
                                      children: [
                                        Expanded(
                                          child: Text(
                                            a.user.username,
                                            style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurface),
                                          ),
                                        ),
                                        if (a.isBestAnswer)
                                          Row(children: [
                                            Icon(Icons.verified_rounded, size: 14, color: Colors.green.shade600),
                                            const SizedBox(width: 3),
                                            Text('Best answer', style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: Colors.green.shade700)),
                                          ]),
                                        const SizedBox(width: 6),
                                        Text(timeago.format(a.createdAt), style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
                                      ],
                                    ),
                                    const SizedBox(height: 4),
                                    Text(a.content, style: TextStyle(fontSize: 13, height: 1.4, color: cs.onSurface.withOpacity(.92))),
                                    if (widget.isOwnPost) ...[
                                      const SizedBox(height: 6),
                                      Align(
                                        alignment: Alignment.centerRight,
                                        child: TextButton.icon(
                                          onPressed: () => _toggleBest(a),
                                          icon: Icon(
                                            a.isBestAnswer ? Icons.close_rounded : Icons.check_circle_outline_rounded,
                                            size: 15,
                                          ),
                                          label: Text(a.isBestAnswer ? 'Unpin' : 'Mark best answer', style: const TextStyle(fontSize: 11.5)),
                                          style: TextButton.styleFrom(visualDensity: VisualDensity.compact, padding: const EdgeInsets.symmetric(horizontal: 6)),
                                        ),
                                      ),
                                    ],
                                  ],
                                ),
                              );
                            },
                          ),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _controller,
                      minLines: 1,
                      maxLines: 4,
                      decoration: InputDecoration(
                        hintText: 'Write an answer…',
                        isDense: true,
                        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(20)),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  _submitting
                      ? const SizedBox(width: 36, height: 36, child: Padding(padding: EdgeInsets.all(8), child: CircularProgressIndicator(strokeWidth: 2)))
                      : IconButton(
                          onPressed: _submit,
                          icon: Icon(Icons.send_rounded, color: cs.primary),
                        ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
