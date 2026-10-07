import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../../l10n/app_localizations.dart';
import '../../leaderboard/models/leaderboard_models.dart';
import '../../leaderboard/screens/leaderboard_screen.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../config/testseries_config.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import '../utils/ts_error_text.dart';
import '../widgets/ts_response_card.dart';
import '../widgets/ts_sheets.dart';
import '../widgets/ts_status.dart';
import 'test_attempt_screen.dart';

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

  // TASK G10 (growth_and_feature_tasks.md — certificates as a share loop).
  TsCertificate? _certificate;
  bool _sharingCertificate = false;

  // TASK G8 (growth_and_feature_tasks.md — AI-generated practice tests
  // from weak areas). Only a loading flag is needed here — success
  // navigates straight into the new attempt, and "nothing to revise"
  // is a one-off snackbar, so there's nothing to keep in state.
  bool _generatingPractice = false;

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
      _loadCertificateIfAny();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e;
        _failed = _attempt == null;
      });
    }
  }

  /// Certificate only exists once the attempt is checked AND the series
  /// has certificates on — every other combination 404s on the backend
  /// (`_certificate_for`), so a failed/absent fetch here just means "no
  /// certificate", never an error worth surfacing (same null-is-fine
  /// contract `downloadCertificateShareCard` documents).
  Future<void> _loadCertificateIfAny() async {
    final a = _attempt;
    if (a == null || !a.isChecked || !widget.series.certificateEnabled) return;
    try {
      final cert = await TestSeriesService.attemptCertificate(widget.attemptId);
      if (!mounted) return;
      setState(() => _certificate = cert);
    } catch (_) {
      // No certificate for this attempt (didn't pass, or not yet issued) —
      // leave _certificate null, the card just stays hidden.
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

  // TASK G7 (growth_and_feature_tasks.md — Leaderboards) — a checked
  // attempt has a settled final_score, so it's exactly when "see how you
  // rank against everyone else on this series" is meaningful.
  void _openLeaderboard() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => LeaderboardScreen(
          scope: LeaderboardScope.testSeries,
          scopeId: widget.series.id,
          title: widget.series.title,
        ),
      ),
    );
  }

  /// TASK G10 — downloads the backend-rendered PNG share card and hands it
  /// to `share_plus`, same "download-to-temp-file, then `Share.shareXFiles`"
  /// flow `weekly_recap_screen.dart`'s `_share()` already uses for the
  /// weekly recap card — just a certificate instead of a stats card.
  Future<void> _shareCertificate() async {
    if (_sharingCertificate) return;
    setState(() => _sharingCertificate = true);
    try {
      final bytes = await TestSeriesService.downloadCertificateShareCard(widget.attemptId);
      if (!mounted) return;
      if (bytes == null) {
        lsSnack(context, "Couldn't prepare your certificate card — try again in a bit.");
        return;
      }
      final dir = await getTemporaryDirectory();
      final code = _certificate?.code ?? widget.attemptId;
      final file = File('${dir.path}/learnscroll_certificate_$code.png');
      await file.writeAsBytes(bytes, flush: true);
      await Share.shareXFiles(
        [XFile(file.path, mimeType: 'image/png')],
        text: 'I just earned a certificate on LearnScroll \u{1F3C6}',
      );
    } finally {
      if (mounted) setState(() => _sharingCertificate = false);
    }
  }

  /// TASK G8 — "Practice weak areas" CTA. Generates a revision test from
  /// this attempt's wrong answers (weakest topics first) and pushes
  /// straight into `TestAttemptScreen` on it — same "start → open
  /// attempt" flow `test_series_detail_screen.dart`'s `_start()` uses,
  /// just triggered from the result screen instead of the series page.
  /// `getSeries` is needed because the endpoint only returns the new
  /// series' id/question-count/total-marks, not the full
  /// `TestSeriesModel` the attempt screen renders from.
  Future<void> _practiceWeakAreas() async {
    if (_generatingPractice) return;
    setState(() => _generatingPractice = true);
    try {
      final practice = await TestSeriesService.practiceWeakAreas(widget.attemptId);
      if (!mounted) return;
      if (practice == null) {
        lsSnack(context, "No weak areas to practice — everything you've answered so far is correct \u{1F389}");
        return;
      }
      final practiceSeries = await TestSeriesService.getSeries(practice.practiceSeriesId);
      if (!mounted) return;
      HapticFeedback.mediumImpact();
      await Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => TestAttemptScreen(attemptId: practice.attempt.id, series: practiceSeries),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      final l10n = AppLocalizations.of(context)!;
      lsSnack(context, tsErrorMessage(l10n, e), error: true);
    } finally {
      if (mounted) setState(() => _generatingPractice = false);
    }
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
    final penaltyTotal = a.responses.fold<int>(0, (sum, r) => sum + r.penalty);

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
                  if (penaltyTotal > 0) LsStatusChip(label: l10n.tsSummaryPenalty(penaltyTotal), color: t.danger),
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

          // ---- practice weak areas (Task G8) ----
          // Same gate the backend uses (`_finished` in views_advanced.py):
          // any status other than in_progress, not just fully `checked` —
          // partially-checked attempts already have enough auto-graded
          // wrong answers to revise from.
          if (a.isFinished)
            LsCard(
              margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
              child: Row(children: [
                Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: t.info.withOpacity(0.12),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Icon(Icons.fitness_center_rounded, color: t.info),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('Practice your weak areas', style: LsType.head(context, size: 14)),
                    const SizedBox(height: 2),
                    Text(
                      'A short revision test from the questions you got wrong.',
                      style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
                    ),
                  ]),
                ),
                const SizedBox(width: 8),
                LsOutlineButton(
                  label: _generatingPractice ? 'Preparing\u2026' : 'Practice',
                  icon: Icons.bolt_rounded,
                  onPressed: _generatingPractice ? null : _practiceWeakAreas,
                ),
              ]),
            ),

          // ---- certificate share card (Task G10) ----
          if (_certificate != null && _certificate!.isValid)
            LsCard(
              margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
              child: Row(children: [
                Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: cs.primary.withOpacity(0.12),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Icon(Icons.workspace_premium_rounded, color: cs.primary),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('Certificate earned', style: LsType.head(context, size: 14)),
                    const SizedBox(height: 2),
                    Text(
                      _certificate!.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
                    ),
                  ]),
                ),
                const SizedBox(width: 8),
                LsOutlineButton(
                  label: _sharingCertificate ? 'Preparing\u2026' : 'Share',
                  icon: Icons.ios_share_rounded,
                  onPressed: _sharingCertificate ? null : _shareCertificate,
                ),
              ]),
            ),

          // ---- leaderboard (Task G7) ----
          // Own card (not squeezed into the rate/ask row below) since
          // it's relevant to every checked attempt, not just the first
          // one the reviewer hasn't rated yet — same "own row" treatment
          // as the practice/certificate cards above it.
          if (a.isChecked)
            LsCard(
              margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
              child: Row(children: [
                Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: cs.primary.withOpacity(0.12),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Icon(Icons.leaderboard_rounded, color: cs.primary),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Text('See where you rank', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                    const SizedBox(height: 2),
                    Text(
                      'Compare your score with everyone on this series.',
                      style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
                    ),
                  ]),
                ),
                const SizedBox(width: 8),
                LsOutlineButton(
                  label: 'View',
                  icon: Icons.emoji_events_outlined,
                  onPressed: _openLeaderboard,
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
