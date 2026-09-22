import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../config/testseries_config.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import '../utils/ts_error_text.dart';
import '../widgets/ts_response_card.dart';
import '../widgets/ts_sheets.dart';
import '../widgets/ts_status.dart';

// ============================================================
// TEST RESULT
//
// Ek attempt ka poora natija: score, summary (sahi / galat / chhode /
// review baaki), per-question breakdown, aur do actions jo sirf `checked`
// attempt pe khulte hain (backend ka rule, UI bhi wahi enforce karta hai
// taaki user ko 400 na khana pade):
//   • series ko rate karna
//   • creator se doubt poochhna
//
// `partially_checked` ka matlab: auto-graded questions ho gaye, text wale
// abhi teacher ke paas hain — isliye final score abhi null hota hai aur
// hum auto score dikhate hain, ek saaf "abhi check hona baaki hai" note ke
// saath. Pull-to-refresh se naya status aata hai.
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
  Object? _error;
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
      final results = await Future.wait<Object?>([
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
        _error = e;
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

  Future<void> _rate() async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showTsReviewSheet(context, seriesId: widget.series.id);
    if (!ok || !mounted) return;
    setState(() => _reviewed = true);
    lsSnack(context, l10n.testReviewThanks);
  }

  Future<void> _ask() async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showTsQuerySheet(context, attemptId: widget.attemptId);
    if (ok && mounted) lsSnack(context, l10n.testQuerySent);
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
        subtitle: _error == null ? l10n.feedErrorSubtitle : tsErrorMessage(l10n, _error!),
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    }

    final a = _attempt!;
    final t = lsTokens(context);
    final total = widget.series.totalMarks;
    final score = a.displayScore;
    // Negative marking ya total=0 me bhi progress bar ko 0..1 me rakho.
    final pct = total <= 0 ? 0.0 : (score / total).clamp(0.0, 1.0).toDouble();
    final passed = pct >= TsConfig.passFraction;
    final locale = Localizations.localeOf(context).toString();

    final answered = a.responses.where((r) => r.wasAnswered).length;
    final correct = a.responses.where((r) => r.isCorrect == true).length;
    final incorrect = a.responses.where((r) => r.isCorrect == false && r.wasAnswered).length;
    final skipped = a.responses.length - answered;
    final awaiting = a.responses.where((r) => r.awaitingReview && r.wasAnswered).length;

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
                    color: passed ? t.success : t.danger,
                  ),
                ),
              ]),
              const SizedBox(height: 14),
              LsProgressBar(value: pct, color: passed ? t.success : t.danger, height: 8),
              if (a.status == TsAttemptStatus.partiallyChecked || a.status == TsAttemptStatus.submitted) ...[
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
              if (a.responses.isNotEmpty) ...[
                const SizedBox(height: 12),
                Wrap(spacing: 6, runSpacing: 6, children: [
                  LsStatusChip(label: l10n.tsSummaryCorrect(correct), color: t.success),
                  LsStatusChip(label: l10n.tsSummaryIncorrect(incorrect), color: t.danger),
                  LsStatusChip(label: l10n.tsSummarySkipped(skipped), color: cs.onSurfaceVariant),
                  if (awaiting > 0) LsStatusChip(label: l10n.tsSummaryAwaiting(awaiting), color: t.info),
                ]),
              ],
              const SizedBox(height: 10),
              if (a.submittedAt != null)
                LsMetaRow(
                  icon: Icons.upload_rounded,
                  label: l10n.testSubmittedOn,
                  value: DateFormat.yMMMd(locale).add_jm().format(a.submittedAt!.toLocal()),
                ),
              if (a.checkedAt != null)
                LsMetaRow(
                  icon: Icons.verified_outlined,
                  label: l10n.testCheckedOn,
                  value: DateFormat.yMMMd(locale).add_jm().format(a.checkedAt!.toLocal()),
                ),
              if (widget.series.attemptsAllowed != 1)
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
                    onPressed: _rate,
                  )),
                if (!_reviewed) const SizedBox(width: 10),
                Expanded(
                  child: LsOutlineButton(
                    label: l10n.testAskQuery,
                    icon: Icons.help_outline_rounded,
                    onPressed: _ask,
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
              TsResponseCard(
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
