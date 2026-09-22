import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// ANALYTICS — read-only, Celery-computed
//
// `CampusAnalyticsSnapshot` rows sirf `refresh_analytics_snapshot` task se
// banti hain (§12) — koi "refresh now" button yahan nahi ho sakta, is app
// se koi write path hai hi nahi. Naya campus jiska task abhi chala nahi,
// wahan `404` normal hai, error nahi — empty state se dikhaya hai.
// ============================================================

class AnalyticsScreen extends StatefulWidget {
  final String campusId;
  const AnalyticsScreen({super.key, required this.campusId});

  @override
  State<AnalyticsScreen> createState() => _AnalyticsScreenState();
}

class _AnalyticsScreenState extends State<AnalyticsScreen> {
  bool _loading = true;
  String? _error;
  CampusAnalyticsSnapshot? _snapshot;

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
      final snap = await CampusService.analyticsLatest(widget.campusId);
      if (!mounted) return;
      setState(() {
        _snapshot = snap;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  static String _fmt(DateTime d) =>
      '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')}/${d.year}';

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    final snapshot = _snapshot;
    return Scaffold(
      appBar: lsAppBar(context, title: l10n.analyticsTitle),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? ListView(padding: const EdgeInsets.all(14), children: const [LsSkeletonBox(height: 200)])
            : _error != null
                ? ListView(children: [
                    const SizedBox(height: 60),
                    ErrorStateWidget(
                      title: l10n.setupLoadFailed,
                      subtitle: _error,
                      retryLabel: l10n.retry,
                      onRetry: _load,
                    ),
                  ])
                : snapshot == null
                    ? ListView(children: [
                        const SizedBox(height: 60),
                        EmptyStateWidget(
                          icon: Icons.insights_outlined,
                          title: l10n.analyticsNoSnapshot,
                          subtitle: l10n.analyticsNoSnapshotHint,
                        ),
                      ])
                    : ListView(
                        padding: const EdgeInsets.all(14),
                        children: [
                          Text(l10n.analyticsComputedAt(_fmt(snapshot.computedAt ?? DateTime.now())),
                              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                          const SizedBox(height: 14),
                          Row(children: [
                            Expanded(
                              child: LsScoreTile(
                                value: '${snapshot.avgAttendancePercent.toStringAsFixed(1)}%',
                                label: l10n.analyticsAvgAttendance,
                                color: cs.primary,
                              ),
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: LsScoreTile(
                                value: snapshot.avgMarksObtained.toStringAsFixed(1),
                                label: l10n.analyticsAvgMarks,
                                color: cs.secondary,
                              ),
                            ),
                          ]),
                          const SizedBox(height: 10),
                          Row(children: [
                            Expanded(
                              child: LsScoreTile(
                                value: '${snapshot.syllabusCompletionPercent.toStringAsFixed(1)}%',
                                label: l10n.analyticsSyllabusCompletion,
                                color: Colors.green,
                              ),
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: LsScoreTile(
                                value: '${snapshot.activeEnrollments}',
                                label: l10n.analyticsActiveEnrollments,
                                color: cs.tertiary,
                              ),
                            ),
                          ]),
                        ],
                      ),
      ),
    );
  }
}
