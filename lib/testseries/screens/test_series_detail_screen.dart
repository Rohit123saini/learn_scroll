import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../config/testseries_config.dart';
import '../services/attempt_draft_store.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import '../utils/ts_error_text.dart';
import '../widgets/ts_review_tile.dart';
import '../widgets/ts_series_card.dart' show TsRatingBit;
import '../widgets/ts_status.dart';
import 'test_attempt_screen.dart';
import 'test_result_screen.dart';
import 'test_series_reviews_screen.dart';

// ============================================================
// TEST SERIES — DETAIL
//
// Ek hi screen se raaste, attempt ke status + attempts-left ke hisaab se:
//   koi attempt nahi                → Start (paid ho to coins ka confirm pehle)
//   in_progress                     → Resume (wahi attempt, dobara charge nahi
//                                     hota — backend ka start endpoint idempotent hai)
//   finished + attempts baaki       → "Attempt again" + "View result"
//   finished + attempts khatam      → View result
//
// Saari attempts ki history yahin dikhti hai (pehle sirf latest dikhti thi
// aur multi-attempt series me dobara attempt ka rasta hi nahi tha).
// ============================================================

class TestSeriesDetailScreen extends StatefulWidget {
  final String seriesId;
  final TestSeriesModel? initialSeries;

  const TestSeriesDetailScreen({super.key, required this.seriesId, this.initialSeries});

  @override
  State<TestSeriesDetailScreen> createState() => _TestSeriesDetailScreenState();
}

class _TestSeriesDetailScreenState extends State<TestSeriesDetailScreen> {
  TestSeriesModel? _series;
  List<TestAttemptModel> _attempts = []; // newest attempt_number first
  List<TestSeriesReview> _reviews = [];
  int _questionCount = 0;
  bool _hasPendingSubmit = false;

  bool _loading = true;
  bool _failed = false;
  Object? _error;
  bool _starting = false;
  bool _flushing = false;

  @override
  void initState() {
    super.initState();
    _series = widget.initialSeries;
    _loading = _series == null;
    _load();
  }

  TestAttemptModel? get _latest => _attempts.isEmpty ? null : _attempts.first;

  bool get _canAttemptAgain {
    final s = _series;
    if (s == null || s.isArchived) return false;
    return s.unlimitedAttempts || _attempts.length < s.attemptsAllowed;
  }

  Future<void> _load() async {
    try {
      final series = await TestSeriesService.getSeries(widget.seriesId);

      // Baaki teen calls optional hain — inme se koi fail ho to screen
      // phir bhi kaam karni chahiye (start button sabse zaroori cheez hai).
      final results = await Future.wait<Object?>([
        TestSeriesService.listMyAttempts().catchError((_) => <TestAttemptModel>[]),
        TestSeriesService.listReviews(widget.seriesId).catchError((_) => <TestSeriesReview>[]),
        TestSeriesService.getQuestions(widget.seriesId).catchError((_) => <TsQuestion>[]),
      ]);

      final attempts = (results[0] as List<TestAttemptModel>)
          .where((a) => a.seriesId == widget.seriesId)
          .toList()
        ..sort((a, b) => b.attemptNumber.compareTo(a.attemptNumber));

      var pending = false;
      final latest = attempts.isEmpty ? null : attempts.first;
      if (latest != null && latest.isInProgress) {
        pending = (await TsDraftStore.load(latest.id))?.pending != null;
      }

      if (!mounted) return;
      setState(() {
        _series = series;
        _attempts = attempts;
        _reviews = results[1] as List<TestSeriesReview>;
        _questionCount = (results[2] as List<TsQuestion>).length;
        _hasPendingSubmit = pending;
        _loading = false;
        _failed = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e;
        _failed = _series == null;
      });
    }
  }

  // ---------------- actions ----------------

  Future<void> _primaryAction() async {
    final s = _series;
    if (s == null || _starting) return;
    final a = _latest;

    if (a != null && a.isInProgress) {
      await _openAttempt(a);
      return;
    }
    if (a != null && a.isFinished && !_canAttemptAgain) {
      await _openResult(a);
      return;
    }
    await _start(s);
  }

  Future<void> _start(TestSeriesModel s) async {
    final l10n = AppLocalizations.of(context)!;

    // Paid series — coins kharch hone se pehle explicit confirm. Backend
    // idempotent hai, par user ko surprise nahi hona chahiye.
    if (s.isPaid && s.priceCoins > 0) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(l10n.testSeriesPayTitle),
          content: Text(l10n.testSeriesPayBody(s.priceCoins)),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
            TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.confirm)),
          ],
        ),
      );
      if (ok != true || !mounted) return;
    }

    setState(() => _starting = true);
    try {
      final attempt = await TestSeriesService.startAttempt(s.id);
      if (!mounted) return;
      setState(() => _starting = false);
      HapticFeedback.mediumImpact();
      if (attempt.isFinished) {
        await _openResult(attempt);
      } else {
        await _openAttempt(attempt);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _starting = false);
      lsSnack(context, tsErrorMessage(l10n, e), error: true);
    }
  }

  Future<void> _openAttempt(TestAttemptModel a) async {
    final s = _series!;
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => TestAttemptScreen(attemptId: a.id, series: s)),
    );
    if (mounted) _load();
  }

  Future<void> _openResult(TestAttemptModel a) async {
    final s = _series!;
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => TestResultScreen(attemptId: a.id, series: s)),
    );
    if (mounted) _load();
  }

  Future<void> _retryPending() async {
    if (_flushing) return;
    setState(() => _flushing = true);
    final done = await TsSubmissionQueue.flush();
    if (!mounted) return;
    setState(() => _flushing = false);
    final l10n = AppLocalizations.of(context)!;
    lsSnack(context, done.isEmpty ? l10n.tsSubmitPendingBody : l10n.tsPendingSynced, error: done.isEmpty);
    if (done.isNotEmpty) _load();
  }

  // ---------------- build ----------------

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: l10n.testSeries),
      body: _buildBody(cs, l10n),
      bottomNavigationBar: _series == null ? null : _buildCtaBar(cs, l10n),
    );
  }

  Widget _buildBody(ColorScheme cs, AppLocalizations l10n) {
    if (_loading && _series == null) return const Center(child: CircularProgressIndicator());
    if (_failed && _series == null) {
      return ErrorStateWidget(
        title: l10n.testSeriesErrorTitle,
        subtitle: _error == null ? l10n.feedErrorSubtitle : tsErrorMessage(l10n, _error!),
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    }

    final s = _series!;
    final t = lsTokens(context);
    final source = s.sourceType;
    final latest = _latest;
    final canStartNew = latest == null || _canAttemptAgain || latest.isInProgress;

    return RefreshIndicator(
      color: cs.primary,
      backgroundColor: cs.surface,
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(top: 6, bottom: 28),
        children: [
          LsCard(
            margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(child: Text(s.title, style: LsType.head(context, size: 16))),
                const SizedBox(width: 10),
                if (s.isPaid)
                  LsStatusChip(
                      label: l10n.testSeriesCoins(s.priceCoins), color: t.warning, icon: Icons.toll_rounded)
                else
                  LsStatusChip(label: l10n.testSeriesFree, color: t.success),
              ]),
              if (s.creator.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(l10n.testSeriesBy(s.creator), style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
              ],
              const SizedBox(height: 8),
              Wrap(spacing: 6, runSpacing: 6, crossAxisAlignment: WrapCrossAlignment.center, children: [
                if (source != TsSource.unknown)
                  LsStatusChip(label: tsSourceLabel(l10n, source), color: tsSourceColor(context, source)),
                TsRatingBit(series: s),
              ]),
              if (s.description.isNotEmpty) ...[
                const SizedBox(height: 12),
                Text(s.description,
                    style: TextStyle(fontSize: 13, height: 1.5, color: cs.onSurface.withOpacity(.92))),
              ],
              const SizedBox(height: 12),
              Divider(height: 1, color: cs.outlineVariant),
              const SizedBox(height: 8),
              if (_questionCount > 0)
                LsMetaRow(
                    icon: Icons.help_outline_rounded,
                    label: l10n.testSeriesQuestionsLabel,
                    value: '$_questionCount'),
              LsMetaRow(
                  icon: Icons.workspace_premium_outlined,
                  label: l10n.assignmentTotalMarksLabel,
                  value: '${s.totalMarks}'),
              LsMetaRow(
                icon: Icons.timer_outlined,
                label: l10n.testSeriesDurationLabel,
                value: s.durationMinutes == null
                    ? l10n.testSeriesNoTimeLimit
                    : l10n.testSeriesDuration(s.durationMinutes!),
              ),
              LsMetaRow(
                icon: Icons.replay_rounded,
                label: l10n.testSeriesAttemptsLabel,
                value: s.unlimitedAttempts
                    ? l10n.tsUnlimited
                    : l10n.tsAttemptsUsedOf(_attempts.length, s.attemptsAllowed),
              ),
              if (latest != null)
                LsMetaRow(
                  icon: Icons.flag_outlined,
                  label: l10n.testSeriesYourAttempt,
                  value: tsStatusLabel(l10n, latest.status),
                  valueColor: tsStatusColor(context, latest.status),
                ),
            ]),
          ),

          // ---- atka hua submit ----
          if (_hasPendingSubmit)
            _Banner(
              color: t.danger,
              icon: Icons.cloud_off_rounded,
              text: l10n.tsSubmitPendingBody,
              actionLabel: l10n.tsRetrySubmit,
              busy: _flushing,
              onAction: _retryPending,
            ),

          // ---- archived ----
          if (s.isArchived && latest == null)
            _Banner(color: t.warning, icon: Icons.archive_outlined, text: l10n.tsSeriesArchived),

          // ---- timer warning ----
          if (s.durationMinutes != null && canStartNew && !s.isArchived)
            _Banner(color: t.info, icon: Icons.info_outline_rounded, text: l10n.testSeriesTimerNotice(s.durationMinutes!)),

          // ---- attempt history ----
          if (_attempts.isNotEmpty) ...[
            LsSectionHead(
              title: l10n.tsAttemptHistory,
              padding: const EdgeInsets.fromLTRB(kLsPad, 6, kLsPad, 10),
            ),
            for (final a in _attempts) _AttemptRow(attempt: a, series: s, onTap: () => _openAny(a)),
          ],

          // ---- reviews ----
          LsSectionHead(
            title: l10n.testSeriesReviews,
            padding: const EdgeInsets.fromLTRB(kLsPad, 6, kLsPad, 10),
          ),
          if (_reviews.isEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
              child: Text(l10n.testSeriesNoReviews, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
            )
          else ...[
            ..._reviews.take(TsConfig.reviewsPreviewCount).map((r) => TsReviewTile(review: r)),
            if (_reviews.length > TsConfig.reviewsPreviewCount)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: kLsPad),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton(
                    onPressed: () => Navigator.push(
                      context,
                      MaterialPageRoute(builder: (_) => TestSeriesReviewsScreen(series: s)),
                    ),
                    child: Text(l10n.tsViewAllReviews(_reviews.length)),
                  ),
                ),
              ),
          ],
        ],
      ),
    );
  }

  Future<void> _openAny(TestAttemptModel a) => a.isInProgress ? _openAttempt(a) : _openResult(a);

  Widget _buildCtaBar(ColorScheme cs, AppLocalizations l10n) {
    final a = _latest;
    final s = _series!;

    final String primaryLabel;
    final IconData primaryIcon;
    bool showViewResult = false;
    bool disabled = false;

    if (a == null) {
      primaryLabel = l10n.testSeriesStart;
      primaryIcon = Icons.play_arrow_rounded;
      disabled = s.isArchived || s.isDraft;
    } else if (a.isInProgress) {
      primaryLabel = l10n.testSeriesResume;
      primaryIcon = Icons.play_arrow_rounded;
    } else if (_canAttemptAgain) {
      primaryLabel = l10n.tsAttemptAgain;
      primaryIcon = Icons.replay_rounded;
      showViewResult = true;
    } else {
      primaryLabel = l10n.testSeriesViewResult;
      primaryIcon = Icons.bar_chart_rounded;
    }

    return Container(
      decoration: BoxDecoration(
        color: cs.surface,
        border: Border(top: BorderSide(color: cs.outlineVariant)),
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 10),
          child: Row(children: [
            if (showViewResult) ...[
              Expanded(
                child: LsOutlineButton(
                  label: l10n.testSeriesViewResult,
                  icon: Icons.bar_chart_rounded,
                  onPressed: () => _openResult(a!),
                ),
              ),
              const SizedBox(width: 10),
            ],
            Expanded(
              child: LsPrimaryButton(
                label: primaryLabel,
                icon: primaryIcon,
                loading: _starting,
                onPressed: _starting || disabled ? null : _primaryAction,
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

class _Banner extends StatelessWidget {
  final Color color;
  final IconData icon;
  final String text;
  final String? actionLabel;
  final bool busy;
  final VoidCallback? onAction;

  const _Banner({
    required this.color,
    required this.icon,
    required this.text,
    this.actionLabel,
    this.busy = false,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 11),
      decoration: BoxDecoration(
        color: color.withOpacity(dark ? .18 : .10),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(children: [
        Icon(icon, size: 17, color: color),
        const SizedBox(width: 9),
        Expanded(child: Text(text, style: TextStyle(fontSize: 11.5, height: 1.4, color: cs.onSurface))),
        if (actionLabel != null) ...[
          const SizedBox(width: 6),
          busy
              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
              : TextButton(onPressed: onAction, child: Text(actionLabel!)),
        ],
      ]),
    );
  }
}

class _AttemptRow extends StatelessWidget {
  final TestAttemptModel attempt;
  final TestSeriesModel series;
  final VoidCallback onTap;

  const _AttemptRow({required this.attempt, required this.series, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final locale = Localizations.localeOf(context).toString();
    final when = attempt.submittedAt ?? attempt.startedAt;

    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
      padding: const EdgeInsets.all(12),
      onTap: onTap,
      child: Row(children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.tsAttemptRow(attempt.attemptNumber),
                style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurface)),
            if (when != null) ...[
              const SizedBox(height: 3),
              Text(DateFormat.yMMMd(locale).add_jm().format(when.toLocal()),
                  style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
            ],
          ]),
        ),
        if (!attempt.isInProgress)
          Padding(
            padding: const EdgeInsets.only(right: 10),
            child: Text('${attempt.displayScore}/${series.totalMarks}',
                style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurface)),
          ),
        LsStatusChip(label: tsStatusLabel(l10n, attempt.status), color: tsStatusColor(context, attempt.status)),
        const SizedBox(width: 4),
        Icon(Icons.chevron_right_rounded, size: 18, color: cs.onSurfaceVariant),
      ]),
    );
  }
}
