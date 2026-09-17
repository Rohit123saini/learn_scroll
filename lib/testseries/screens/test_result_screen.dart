import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import 'test_series_screen.dart' show tsStatusColor, tsStatusLabel;

// ============================================================
// TEST RESULT
//
// Ek attempt ka poora natija: score, per-question breakdown, aur do
// actions jo sirf `checked` attempt pe khulte hain (backend ka rule, UI
// bhi wahi enforce karta hai taaki user ko 400 na khana pade):
//   • series ko rate karna
//   • creator se doubt poochhna
//
// `partially_checked` ka matlab: auto-graded questions ho gaye, text wale
// abhi teacher ke paas hain — isliye final score abhi null hota hai aur
// hum auto score dikhate hain, ek saaf "abhi check hona baaki hai" note ke
// saath.
// ============================================================

class TestResultScreen extends StatefulWidget {
  final String attemptId;
  final TestSeriesModel series;
  final TestAttemptModel? initialAttempt;

  const TestResultScreen({
    super.key,
    required this.attemptId,
    required this.series,
    this.initialAttempt,
  });

  @override
  State<TestResultScreen> createState() => _TestResultScreenState();
}

class _TestResultScreenState extends State<TestResultScreen> {
  TestAttemptModel? _attempt;
  List<TsQuestion> _questions = [];
  bool _loading = true;
  bool _failed = false;
  bool _reviewed = false;

  @override
  void initState() {
    super.initState();
    _attempt = widget.initialAttempt;
    _loading = _attempt == null;
    _load();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([
        TestSeriesService.getAttempt(widget.attemptId),
        TestSeriesService.getQuestions(widget.series.id).catchError((_) => <TsQuestion>[]),
      ]);
      if (!mounted) return;
      setState(() {
        _attempt = results[0] as TestAttemptModel;
        _questions = results[1] as List<TsQuestion>;
        _loading = false;
        _failed = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _failed = _attempt == null;
      });
    }
  }

  TsQuestion? _questionById(String id) {
    for (final q in _questions) {
      if (q.id == id) return q;
    }
    return null;
  }

  // ---------------- review ----------------

  Future<void> _openReviewSheet() async {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    int rating = 5;
    final commentCtrl = TextEditingController();
    bool sending = false;

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: cs.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) => Padding(
          padding: EdgeInsets.only(
            left: kLsPad,
            right: kLsPad,
            top: 18,
            bottom: MediaQuery.of(ctx).viewInsets.bottom + 18,
          ),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.testRateTitle, style: LsType.head(context, size: 15)),
            const SizedBox(height: 4),
            Text(l10n.testRateSubtitle, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
            const SizedBox(height: 14),
            Row(
              children: List.generate(5, (i) {
                final on = i < rating;
                return IconButton(
                  onPressed: () {
                    HapticFeedback.selectionClick();
                    setSheet(() => rating = i + 1);
                  },
                  icon: Icon(on ? Icons.star_rounded : Icons.star_outline_rounded,
                      size: 30, color: lsTokens(context).warning),
                );
              }),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: commentCtrl,
              maxLines: 4,
              minLines: 2,
              textCapitalization: TextCapitalization.sentences,
              style: TextStyle(fontSize: 13, color: cs.onSurface),
              decoration: InputDecoration(hintText: l10n.testReviewHint),
            ),
            const SizedBox(height: 14),
            LsPrimaryButton(
              label: l10n.testSubmitReview,
              icon: Icons.send_rounded,
              loading: sending,
              onPressed: sending
                  ? null
                  : () async {
                      setSheet(() => sending = true);
                      try {
                        await TestSeriesService.createReview(
                          seriesId: widget.series.id,
                          rating: rating,
                          comment: commentCtrl.text.trim(),
                        );
                        if (!ctx.mounted) return;
                        Navigator.pop(ctx);
                        if (!mounted) return;
                        setState(() => _reviewed = true);
                        lsSnack(context, l10n.testReviewThanks);
                      } on TestSeriesApiException catch (e) {
                        setSheet(() => sending = false);
                        // Backend ka message (e.g. "already reviewed" /
                        // "attempt checked nahi hua") user ko dikhane layak
                        // hota hai, isliye usko hi dikhate hain.
                        if (ctx.mounted) lsSnack(ctx, e.message, error: true);
                      } catch (e) {
                        setSheet(() => sending = false);
                        if (ctx.mounted) lsSnack(ctx, l10n.somethingWentWrong, error: true);
                      }
                    },
            ),
          ]),
        ),
      ),
    );
    commentCtrl.dispose();
  }

  // ---------------- doubt ----------------

  Future<void> _openQuerySheet() async {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final ctrl = TextEditingController();
    bool anonymous = false;
    bool sending = false;

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: cs.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) => Padding(
          padding: EdgeInsets.only(
            left: kLsPad,
            right: kLsPad,
            top: 18,
            bottom: MediaQuery.of(ctx).viewInsets.bottom + 18,
          ),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.testAskQueryTitle, style: LsType.head(context, size: 15)),
            const SizedBox(height: 4),
            Text(l10n.testAskQuerySubtitle, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
            const SizedBox(height: 14),
            TextField(
              controller: ctrl,
              maxLines: 5,
              minLines: 3,
              textCapitalization: TextCapitalization.sentences,
              style: TextStyle(fontSize: 13, color: cs.onSurface),
              decoration: InputDecoration(hintText: l10n.testQueryHint),
            ),
            const SizedBox(height: 6),
            Row(children: [
              Checkbox(
                value: anonymous,
                onChanged: (v) => setSheet(() => anonymous = v ?? false),
              ),
              Expanded(
                child: Text(l10n.testQueryAnonymous, style: TextStyle(fontSize: 12, color: cs.onSurface)),
              ),
            ]),
            const SizedBox(height: 8),
            LsPrimaryButton(
              label: l10n.testSendQuery,
              icon: Icons.send_rounded,
              loading: sending,
              onPressed: sending
                  ? null
                  : () async {
                      if (ctrl.text.trim().isEmpty) {
                        lsSnack(ctx, l10n.testQueryEmpty, error: true);
                        return;
                      }
                      setSheet(() => sending = true);
                      try {
                        await TestSeriesService.askQuery(
                          attemptId: widget.attemptId,
                          text: ctrl.text.trim(),
                          isAnonymous: anonymous,
                        );
                        if (!ctx.mounted) return;
                        Navigator.pop(ctx);
                        if (mounted) lsSnack(context, l10n.testQuerySent);
                      } on TestSeriesApiException catch (e) {
                        setSheet(() => sending = false);
                        if (ctx.mounted) lsSnack(ctx, e.message, error: true);
                      } catch (e) {
                        setSheet(() => sending = false);
                        if (ctx.mounted) lsSnack(ctx, l10n.somethingWentWrong, error: true);
                      }
                    },
            ),
          ]),
        ),
      ),
    );
    ctrl.dispose();
  }

  // ---------------- build ----------------

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: l10n.testResultTitle),
      body: _buildBody(cs, l10n),
    );
  }

  Widget _buildBody(ColorScheme cs, AppLocalizations l10n) {
    if (_loading && _attempt == null) return const Center(child: CircularProgressIndicator());
    if (_failed && _attempt == null) {
      return ErrorStateWidget(
        title: l10n.testSeriesErrorTitle,
        subtitle: l10n.feedErrorSubtitle,
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    }

    final a = _attempt!;
    final t = lsTokens(context);
    final total = widget.series.totalMarks;
    final score = a.displayScore;
    final pct = total == 0 ? 0.0 : score / total;

    return RefreshIndicator(
      color: cs.primary,
      backgroundColor: cs.surface,
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(top: 6, bottom: 32),
        children: [
          // ---- score card ----
          LsCard(
            margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(child: Text(widget.series.title, style: LsType.head(context, size: 15))),
                const SizedBox(width: 10),
                LsStatusChip(label: tsStatusLabel(l10n, a.status), color: tsStatusColor(context, a.status)),
              ]),
              const SizedBox(height: 14),
              Row(children: [
                Expanded(
                  child: LsScoreTile(
                    value: '$score',
                    label: a.finalScore != null ? l10n.testFinalScore : l10n.testAutoScore,
                    color: a.finalScore != null ? t.success : cs.primary,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(child: LsScoreTile(value: '$total', label: l10n.testTotalMarks, color: cs.primary)),
                const SizedBox(width: 10),
                Expanded(
                  child: LsScoreTile(
                    value: '${(pct * 100).round()}%',
                    label: l10n.testPercentage,
                    color: pct >= .4 ? t.success : t.danger,
                  ),
                ),
              ]),
              const SizedBox(height: 14),
              LsProgressBar(value: pct, color: pct >= .4 ? t.success : t.danger, height: 8),
              if (a.status == TsAttemptStatus.partiallyChecked ||
                  a.status == TsAttemptStatus.submitted) ...[
                const SizedBox(height: 12),
                Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Icon(Icons.hourglass_bottom_rounded, size: 15, color: t.info),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(l10n.testAwaitingCheckNote,
                        style: TextStyle(fontSize: 11.5, height: 1.4, color: cs.onSurfaceVariant)),
                  ),
                ]),
              ],
              const SizedBox(height: 10),
              if (a.submittedAt != null)
                LsMetaRow(
                  icon: Icons.upload_rounded,
                  label: l10n.testSubmittedOn,
                  value: DateFormat.yMMMd(Localizations.localeOf(context).toString())
                      .add_jm()
                      .format(a.submittedAt!.toLocal()),
                ),
              if (a.checkedAt != null)
                LsMetaRow(
                  icon: Icons.verified_outlined,
                  label: l10n.testCheckedOn,
                  value: DateFormat.yMMMd(Localizations.localeOf(context).toString())
                      .add_jm()
                      .format(a.checkedAt!.toLocal()),
                ),
              if (widget.series.attemptsAllowed > 1)
                LsMetaRow(
                  icon: Icons.replay_rounded,
                  label: l10n.testAttemptNumber,
                  value: '${a.attemptNumber}',
                ),
            ]),
          ),

          // ---- checked-only actions ----
          if (a.isChecked)
            Padding(
              padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 16),
              child: Row(children: [
                if (!_reviewed)
                  Expanded(
                      child: LsPrimaryButton(
                    label: l10n.testRateSeries,
                    icon: Icons.star_rounded,
                    onPressed: _openReviewSheet,
                  )),
                if (!_reviewed) const SizedBox(width: 10),
                Expanded(
                  child: LsOutlineButton(
                    label: l10n.testAskQuery,
                    icon: Icons.help_outline_rounded,
                    onPressed: _openQuerySheet,
                  ),
                ),
              ]),
            ),

          // ---- per-question breakdown ----
          if (a.responses.isNotEmpty) ...[
            LsSectionHead(
              title: l10n.testBreakdown,
              padding: const EdgeInsets.fromLTRB(kLsPad, 4, kLsPad, 10),
            ),
            for (int i = 0; i < a.responses.length; i++)
              _ResponseCard(
                index: i,
                response: a.responses[i],
                question: _questionById(a.responses[i].questionId),
              ),
          ],
        ],
      ),
    );
  }
}

class _ResponseCard extends StatelessWidget {
  final int index;
  final TsResponse response;
  final TsQuestion? question;

  const _ResponseCard({required this.index, required this.response, required this.question});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = lsTokens(context);
    final l10n = AppLocalizations.of(context)!;

    final Color statusColor;
    final String statusLabel;
    if (response.awaitingReview) {
      statusColor = t.info;
      statusLabel = l10n.testAwaitingReview;
    } else if (response.isCorrect == true) {
      statusColor = t.success;
      statusLabel = l10n.answerCorrect;
    } else if (response.isCorrect == false) {
      statusColor = t.danger;
      statusLabel = l10n.answerIncorrect;
    } else {
      statusColor = cs.primary;
      statusLabel = l10n.assignmentReviewed;
    }

    final maxMarks = question?.marks ?? 0;

    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(l10n.questionShort(index + 1),
              style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: cs.primary)),
          const Spacer(),
          LsStatusChip(label: statusLabel, color: statusColor),
          const SizedBox(width: 8),
          Text(
            l10n.assignmentMarksOf(response.marksAwarded ?? 0, maxMarks),
            style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: cs.onSurface),
          ),
        ]),
        if (question != null) ...[
          const SizedBox(height: 9),
          Text(question!.text, style: TextStyle(fontSize: 13, height: 1.45, color: cs.onSurface)),
        ],
        const SizedBox(height: 11),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(11),
          decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(12)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.testYourAnswer,
                style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant)),
            const SizedBox(height: 5),
            Text(_readable(l10n), style: TextStyle(fontSize: 12.5, height: 1.45, color: cs.onSurface)),
          ]),
        ),
        if (response.reviewerFeedback.isNotEmpty) ...[
          const SizedBox(height: 10),
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(Icons.rate_review_outlined, size: 15, color: cs.onSurfaceVariant),
            const SizedBox(width: 8),
            Expanded(
              child: Text(response.reviewerFeedback,
                  style: TextStyle(fontSize: 12, height: 1.45, color: cs.onSurfaceVariant)),
            ),
          ]),
        ],
      ]),
    );
  }

  /// `answer_data` ko padhne layak text me badalta hai. Shape question type
  /// ke hisaab se badalti hai (§4.3), aur question object na mile to option
  /// ids hi dikhti hain — crash kabhi nahi.
  String _readable(AppLocalizations l10n) {
    final data = response.answerData;
    if (data == null) return l10n.answerNotAnswered;

    String label(String id) {
      final q = question;
      if (q == null) return id;
      for (final o in [...q.choices, ...q.matchLeft, ...q.matchRight]) {
        if (o.id == id) return o.text;
      }
      return id;
    }

    if (data is String) return data.trim().isEmpty ? l10n.answerNotAnswered : data;
    if (data is Map) {
      if (data['option_id'] != null) return label(data['option_id'].toString());
      final ids = data['option_ids'];
      if (ids is List) {
        return ids.isEmpty ? l10n.answerNotAnswered : ids.map((e) => label(e.toString())).join(', ');
      }
      final seq = data['sequence'];
      if (seq is List) {
        return seq.isEmpty
            ? l10n.answerNotAnswered
            : seq.asMap().entries.map((e) => '${e.key + 1}. ${label(e.value.toString())}').join('\n');
      }
      final pairs = data['pairs'];
      if (pairs is Map) {
        return pairs.isEmpty
            ? l10n.answerNotAnswered
            : pairs.entries.map((e) => '${label(e.key.toString())} → ${label(e.value.toString())}').join('\n');
      }
      final text = data['text'];
      if (text != null) {
        return text.toString().trim().isEmpty ? l10n.answerNotAnswered : text.toString();
      }
      if (data.isEmpty) return l10n.answerNotAnswered;
      return data.toString();
    }
    if (data is List) {
      return data.isEmpty ? l10n.answerNotAnswered : data.map((e) => label(e.toString())).join(', ');
    }
    return data.toString();
  }
}
