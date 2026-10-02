// lib/testseries/screens/series_analytics_screen.dart
//
// ============================================================
// TEST SERIES — CREATOR PERFORMANCE ANALYTICS  (Task 4)
//
// Backend: GET {mount}/testseries/{id}/analytics/ (creator-only, see
// testseries/views_advanced.py, SeriesAdvancedActionsMixin.analytics).
// Distinct from `test_result_screen.dart`'s per-attempt analytics, which
// is a student's own rank/percentile view of ONE attempt — this screen
// aggregates every attempt anyone has ever made on the whole series:
// average score/%, pass rate, which questions trip students up the
// most, and attempt volume over time.
//
// Entry point: `TestSeriesDetailScreen` app bar (bar-chart icon) — shown
// for every series, same "shown to everyone, backend gates it" pattern
// this app already uses elsewhere (e.g. Classroom.refer_link in the
// tuitionclass module). A non-creator tapping it simply sees the normal
// 403 error state below instead of a raw crash.
// ============================================================

import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import '../utils/ts_error_text.dart';

class SeriesAnalyticsScreen extends StatefulWidget {
  final String seriesId;
  final String? seriesTitle;

  const SeriesAnalyticsScreen({super.key, required this.seriesId, this.seriesTitle});

  @override
  State<SeriesAnalyticsScreen> createState() => _SeriesAnalyticsScreenState();
}

class _SeriesAnalyticsScreenState extends State<SeriesAnalyticsScreen> {
  TsSeriesAnalytics? _data;
  bool _loading = true;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await TestSeriesService.seriesAnalytics(widget.seriesId);
      if (!mounted) return;
      setState(() {
        _data = data;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: widget.seriesTitle ?? 'Series analytics'),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? ListView(children: [
                    Padding(
                      padding: const EdgeInsets.only(top: 80),
                      child: ErrorStateWidget(
                        title: tsErrorMessage(l10n, _error!),
                        retryLabel: l10n.retry,
                        onRetry: _load,
                      ),
                    ),
                  ])
                : _buildBody(context),
      ),
    );
  }

  Widget _buildBody(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final d = _data!;

    if (d.totalAttempts == 0) {
      return ListView(children: const [
        Padding(
          padding: EdgeInsets.only(top: 80),
          child: EmptyStateWidget(
            title: 'No attempts yet',
            subtitle: 'Analytics will show up once students start attempting this series.',
            icon: Icons.bar_chart_rounded,
          ),
        ),
      ]);
    }

    return ListView(
      padding: const EdgeInsets.all(kLsPad),
      children: [
        // ---- overview totals ----
        LsCard(
          child: Column(children: [
            Row(children: [
              Expanded(child: _StatBlock(label: 'Attempts', value: '${d.totalAttempts}')),
              Expanded(child: _StatBlock(label: 'Students', value: '${d.studentsAttempted}')),
              Expanded(child: _StatBlock(label: 'Checked', value: '${d.checkedCount}')),
            ]),
          ]),
        ),
        const SizedBox(height: 16),
        LsCard(
          child: Column(children: [
            LsMetaRow(
              icon: Icons.equalizer_rounded,
              label: 'Average score',
              value: d.averageScore != null
                  ? '${_fmtNum(d.averageScore!)} / ${d.totalMarks}'
                  : '-',
            ),
            const SizedBox(height: 8),
            LsMetaRow(
              icon: Icons.percent_rounded,
              label: 'Average percentage',
              value: d.averagePercentage != null ? '${_fmtNum(d.averagePercentage!)}%' : '-',
            ),
            const SizedBox(height: 8),
            LsMetaRow(
              icon: Icons.verified_rounded,
              label: 'Pass rate'
                  '${d.passPercentageThreshold > 0 ? " (\u2265${d.passPercentageThreshold}%)" : ""}',
              value: d.passRate != null ? '${_fmtNum(d.passRate!)}%' : '-',
              valueColor: d.passRate == null
                  ? null
                  : (d.passRate! >= 50 ? Theme.of(context).colorScheme.tertiary : Theme.of(context).colorScheme.error),
            ),
          ]),
        ),

        // ---- most missed questions ----
        if (d.mostMissedQuestions.isNotEmpty) ...[
          const SizedBox(height: 20),
          const LsSectionHead(title: 'Most missed questions', padding: EdgeInsets.zero),
          ...d.mostMissedQuestions.map((q) => LsCard(
                margin: const EdgeInsets.only(top: 8),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                      decoration: BoxDecoration(
                        color: cs.errorContainer,
                        borderRadius: BorderRadius.circular(20),
                      ),
                      child: Text('Q${q.order}',
                          style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: cs.onErrorContainer)),
                    ),
                    const SizedBox(width: 8),
                    if (q.topic.isNotEmpty)
                      Text(q.topic, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                  ]),
                  const SizedBox(height: 6),
                  Text(q.text, style: LsType.head(context, size: 13.5), maxLines: 2, overflow: TextOverflow.ellipsis),
                  const SizedBox(height: 8),
                  LsMetaRow(
                    icon: Icons.close_rounded,
                    label: 'Wrong rate',
                    value: '${_fmtNum(q.wrongRate)}%  (${q.wrongCount}/${q.attempts})',
                    valueColor: cs.error,
                  ),
                ]),
              )),
        ],

        // ---- attempts over time ----
        if (d.attemptsOverTime.isNotEmpty) ...[
          const SizedBox(height: 20),
          const LsSectionHead(title: 'Attempts over time', padding: EdgeInsets.zero),
          LsCard(
            child: _AttemptsBarChart(rows: d.attemptsOverTime),
          ),
        ],
        const SizedBox(height: 24),
      ],
    );
  }

  static String _fmtNum(double v) => v == v.roundToDouble() ? v.toStringAsFixed(0) : v.toStringAsFixed(1);
}

class _StatBlock extends StatelessWidget {
  final String label;
  final String value;
  const _StatBlock({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Column(children: [
      Text(value, style: LsType.head(context, size: 20)),
      const SizedBox(height: 4),
      Text(label, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
    ]);
  }
}

/// Minimal dependency-free bar chart — one bar per day, tallest day at
/// full height. Kept intentionally simple (no charting package in this
/// module's pubspec) rather than pulling in a new dependency for one
/// screen.
class _AttemptsBarChart extends StatelessWidget {
  final List<TsAttemptsByDay> rows;
  const _AttemptsBarChart({required this.rows});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final maxCount = rows.fold<int>(1, (m, r) => r.count > m ? r.count : m);
    const chartHeight = 120.0;

    return SizedBox(
      height: chartHeight + 28,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: rows.length,
        separatorBuilder: (_, __) => const SizedBox(width: 10),
        itemBuilder: (context, i) {
          final row = rows[i];
          final barHeight = chartHeight * (row.count / maxCount).clamp(0.05, 1.0);
          return SizedBox(
            width: 34,
            child: Column(mainAxisAlignment: MainAxisAlignment.end, children: [
              Text('${row.count}', style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
              const SizedBox(height: 4),
              Container(
                height: barHeight,
                decoration: BoxDecoration(
                  color: cs.primary,
                  borderRadius: const BorderRadius.vertical(top: Radius.circular(6)),
                ),
              ),
              const SizedBox(height: 6),
              Text(
                row.date != null ? '${row.date!.day}/${row.date!.month}' : '-',
                style: TextStyle(fontSize: 9.5, color: cs.onSurfaceVariant),
              ),
            ]),
          );
        },
      ),
    );
  }
}
