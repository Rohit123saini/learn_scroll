import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import 'test_attempt_screen.dart';
import 'test_result_screen.dart';
import 'test_series_screen.dart' show tsStatusColor, tsStatusLabel;

// ============================================================
// TEST SERIES — DETAIL
//
// Ek hi screen se teen raste nikalte hain, attempt ke status ke hisaab se:
//   koi attempt nahi  → Start (paid ho to coins ka confirm pehle)
//   in_progress       → Resume (wahi attempt, dobara charge nahi hota —
//                       backend ka start endpoint idempotent hai)
//   submitted/checked → View result
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
  TestAttemptModel? _attempt;
  List<TestSeriesReview> _reviews = [];
  int _questionCount = 0;

  bool _loading = true;
  bool _failed = false;
  bool _starting = false;

  @override
  void initState() {
    super.initState();
    _series = widget.initialSeries;
    _loading = _series == null;
    _load();
  }

  Future<void> _load() async {
    try {
      final series = await TestSeriesService.getSeries(widget.seriesId);

      // Baaki teen calls optional hain — inme se koi fail ho to screen
      // phir bhi kaam karni chahiye (start button sabse zaroori cheez hai).
      final results = await Future.wait([
        TestSeriesService.listMyAttempts().catchError((_) => <TestAttemptModel>[]),
        TestSeriesService.listReviews(widget.seriesId).catchError((_) => <TestSeriesReview>[]),
        TestSeriesService.getQuestions(widget.seriesId).catchError((_) => <TsQuestion>[]),
      ]);

      final attempts = (results[0] as List<TestAttemptModel>)
          .where((a) => a.seriesId == widget.seriesId)
          .toList()
        ..sort((a, b) => b.attemptNumber.compareTo(a.attemptNumber));

      if (!mounted) return;
      setState(() {
        _series = series;
        _attempt = attempts.isEmpty ? null : attempts.first;
        _reviews = results[1] as List<TestSeriesReview>;
        _questionCount = (results[2] as List<TsQuestion>).length;
        _loading = false;
        _failed = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _failed = _series == null;
      });
    }
  }

  // ---------------- actions ----------------

  Future<void> _primaryAction() async {
    final s = _series;
    if (s == null || _starting) return;
    final a = _attempt;

    if (a != null && a.isFinished) {
      await _openResult(a);
      return;
    }
    if (a != null && a.isInProgress) {
      await _openAttempt(a);
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
      if (ok != true) return;
    }

    setState(() => _starting = true);
    try {
      final attempt = await TestSeriesService.startAttempt(s.id);
      if (!mounted) return;
      setState(() {
        _attempt = attempt;
        _starting = false;
      });
      HapticFeedback.mediumImpact();
      if (attempt.isFinished) {
        await _openResult(attempt);
      } else {
        await _openAttempt(attempt);
      }
    } on TestSeriesApiException catch (e) {
      if (!mounted) return;
      setState(() => _starting = false);
      lsSnack(context, e.isInsufficientCoins ? l10n.testSeriesNotEnoughCoins : e.message, error: true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _starting = false);
      lsSnack(context, l10n.somethingWentWrong, error: true);
    }
  }

  Future<void> _openAttempt(TestAttemptModel a) async {
    final s = _series!;
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => TestAttemptScreen(attemptId: a.id, series: s),
      ),
    );
    if (mounted) _load();
  }

  Future<void> _openResult(TestAttemptModel a) async {
    final s = _series!;
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => TestResultScreen(attemptId: a.id, series: s),
      ),
    );
    if (mounted) _load();
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
        subtitle: l10n.feedErrorSubtitle,
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    }

    final s = _series!;
    final t = lsTokens(context);

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
                Text(l10n.testSeriesBy(s.creator),
                    style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
              ],
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
                  value: '${s.attemptsAllowed}'),
              if (_attempt != null)
                LsMetaRow(
                  icon: Icons.flag_outlined,
                  label: l10n.testSeriesYourAttempt,
                  value: tsStatusLabel(l10n, _attempt!.status),
                  valueColor: tsStatusColor(context, _attempt!.status),
                ),
            ]),
          ),

          // ---- timer warning ----
          if (s.durationMinutes != null && (_attempt == null || _attempt!.isInProgress))
            Container(
              margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
              padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 11),
              decoration: BoxDecoration(
                color: t.info.withOpacity(Theme.of(context).brightness == Brightness.dark ? .18 : .10),
                borderRadius: BorderRadius.circular(14),
              ),
              child: Row(children: [
                Icon(Icons.info_outline_rounded, size: 17, color: t.info),
                const SizedBox(width: 9),
                Expanded(
                  child: Text(l10n.testSeriesTimerNotice(s.durationMinutes!),
                      style: TextStyle(fontSize: 11.5, height: 1.4, color: cs.onSurface)),
                ),
              ]),
            ),

          // ---- reviews ----
          LsSectionHead(
            title: l10n.testSeriesReviews,
            padding: const EdgeInsets.fromLTRB(kLsPad, 6, kLsPad, 10),
          ),
          if (_reviews.isEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
              child: Text(l10n.testSeriesNoReviews,
                  style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
            )
          else
            ..._reviews.take(5).map((r) => _ReviewTile(review: r)),
        ],
      ),
    );
  }

  Widget _buildCtaBar(ColorScheme cs, AppLocalizations l10n) {
    final a = _attempt;
    final String label;
    final IconData icon;
    if (a == null) {
      label = l10n.testSeriesStart;
      icon = Icons.play_arrow_rounded;
    } else if (a.isInProgress) {
      label = l10n.testSeriesResume;
      icon = Icons.play_arrow_rounded;
    } else {
      label = l10n.testSeriesViewResult;
      icon = Icons.bar_chart_rounded;
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
          child: LsPrimaryButton(
            label: label,
            icon: icon,
            loading: _starting,
            onPressed: _starting ? null : _primaryAction,
          ),
        ),
      ),
    );
  }
}

class _ReviewTile extends StatelessWidget {
  final TestSeriesReview review;
  const _ReviewTile({required this.review});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = lsTokens(context);
    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
      padding: const EdgeInsets.all(12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(
            child: Text(review.student,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.onSurface)),
          ),
          Row(
            mainAxisSize: MainAxisSize.min,
            children: List.generate(
              5,
              (i) => Icon(
                i < review.rating ? Icons.star_rounded : Icons.star_outline_rounded,
                size: 13,
                color: t.warning,
              ),
            ),
          ),
        ]),
        if (review.comment.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(review.comment, style: TextStyle(fontSize: 12, height: 1.45, color: cs.onSurfaceVariant)),
        ],
        if (review.createdAt != null) ...[
          const SizedBox(height: 6),
          Text(
            DateFormat.yMMMd(Localizations.localeOf(context).toString()).format(review.createdAt!.toLocal()),
            style: TextStyle(fontSize: 10, color: cs.onSurfaceVariant),
          ),
        ],
      ]),
    );
  }
}
