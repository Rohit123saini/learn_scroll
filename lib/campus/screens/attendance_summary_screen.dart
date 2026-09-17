import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// ATTENDANCE — SUMMARY (student / parent / staff view)
//
// `/attendance/summary/?enrollment=<id>[&subject=<id>]`
//
// Backend ise store nahi karta, har call pe compute karta hai. Isliye
// yahan bhi koi cache nahi — pull-to-refresh hi sach hai.
//
// Subject-wise breakdown ke liye har subject ka alag call jaata hai
// (backend pe koi "sab subject ek saath" endpoint nahi hai). Isliye
// overall pehle dikhta hai aur subject rows baad me bharte hain — student
// ko 6 calls ka wait nahi karna padta.
// ============================================================

class AttendanceSummaryScreen extends StatefulWidget {
  final StudentEnrollment enrollment;
  final String sectionLabel;
  final List<Subject> subjects;
  final int thresholdPercent;

  const AttendanceSummaryScreen({
    super.key,
    required this.enrollment,
    required this.sectionLabel,
    required this.subjects,
    this.thresholdPercent = 75,
  });

  @override
  State<AttendanceSummaryScreen> createState() => _AttendanceSummaryScreenState();
}

class _AttendanceSummaryScreenState extends State<AttendanceSummaryScreen> {
  bool _loading = true;
  String? _error;
  AttendanceSummary? _overall;

  /// subjectId -> summary. Dheere-dheere bharta hai.
  final Map<String, AttendanceSummary> _bySubject = {};
  bool _loadingSubjects = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
      _bySubject.clear();
    });
    try {
      final overall = await CampusService.attendanceSummary(enrollmentId: widget.enrollment.id);
      if (!mounted) return;
      setState(() {
        _overall = overall;
        _loading = false;
      });
      _loadSubjects();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _loadSubjects() async {
    if (widget.subjects.isEmpty) return;
    setState(() => _loadingSubjects = true);
    for (final s in widget.subjects) {
      try {
        final summary = await CampusService.attendanceSummary(
          enrollmentId: widget.enrollment.id,
          subjectId: s.id,
        );
        if (!mounted) return;
        // Jis subject ki ek bhi class nahi hui, usko list me mat dikhao —
        // "0/0 — 0%" dekhkar student ghabra jaata hai.
        if (summary.total > 0) setState(() => _bySubject[s.id] = summary);
      } catch (_) {
        // Ek subject fail hua to baaki mat roko.
      }
    }
    if (mounted) setState(() => _loadingSubjects = false);
  }

  Color _colorFor(double percent, ColorScheme cs) {
    if (percent >= widget.thresholdPercent) return Colors.green;
    if (percent >= widget.thresholdPercent - 10) return Colors.orange;
    return cs.error;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      appBar: AppBar(
        title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l10n.campusAttendanceLabel, style: LsType.head(context, size: 15)),
          Text(widget.sectionLabel,
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ]),
      ),
      body: RefreshIndicator(onRefresh: _load, child: _body(cs, l10n)),
    );
  }

  Widget _body(ColorScheme cs, AppLocalizations l10n) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 140),
        SizedBox(height: 14),
        LsSkeletonBox(height: 56),
        SizedBox(height: 8),
        LsSkeletonBox(height: 56),
      ]);
    }

    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(
          title: l10n.attendanceSummaryFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _load,
        ),
      ]);
    }

    final overall = _overall!;
    final color = _colorFor(overall.percent, cs);

    return ListView(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 28),
      children: [
        LsCard(
          child: Column(children: [
            Text('${overall.percent.toStringAsFixed(1)}%',
                style: TextStyle(
                    fontSize: 42, fontWeight: FontWeight.w800, color: color, height: 1.05)),
            const SizedBox(height: 4),
            Text(l10n.attendanceOverall,
                style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
            const SizedBox(height: 14),
            LsProgressBar(value: (overall.percent / 100).clamp(0, 1), color: color, height: 8),
            const SizedBox(height: 14),
            Row(children: [
              Expanded(
                child: LsScoreTile(
                    value: '${overall.present}', label: l10n.attendancePresent, color: Colors.green),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: LsScoreTile(
                    value: '${overall.absent}', label: l10n.attendanceAbsent, color: cs.error),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: LsScoreTile(
                    value: '${overall.total}', label: l10n.attendanceTotal, color: cs.primary),
              ),
            ]),
          ]),
        ),

        // Threshold warning — campus ka apna threshold
        // (`Campus.attendance_alert_threshold_percent`, default 75) use
        // hota hai, hardcoded 75 nahi. Har campus ka rule alag ho sakta hai.
        if (overall.isBelow(widget.thresholdPercent)) ...[
          const SizedBox(height: 12),
          LsCard(
            borderColor: cs.error,
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Icon(Icons.warning_amber_rounded, size: 18, color: cs.error),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  l10n.attendanceBelowThreshold(widget.thresholdPercent),
                  style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant, height: 1.35),
                ),
              ),
            ]),
          ),
        ],

        const SizedBox(height: 18),
        LsSectionHead(
          title: l10n.attendanceBySubject,
          padding: const EdgeInsets.only(bottom: 10),
        ),

        if (_bySubject.isEmpty && !_loadingSubjects)
          LsCard(
            tinted: true,
            child: Text(l10n.attendanceNoSubjectData,
                style: TextStyle(color: cs.onSurfaceVariant)),
          ),

        for (final entry in _bySubject.entries)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: _SubjectRow(
              name: widget.subjects.firstWhere((s) => s.id == entry.key).label,
              summary: entry.value,
              color: _colorFor(entry.value.percent, cs),
            ),
          ),

        if (_loadingSubjects) const LsSkeletonBox(height: 56),
      ],
    );
  }
}

class _SubjectRow extends StatelessWidget {
  final String name;
  final AttendanceSummary summary;
  final Color color;

  const _SubjectRow({required this.name, required this.summary, required this.color});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return LsCard(
      padding: const EdgeInsets.fromLTRB(12, 11, 12, 11),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(
            child: Text(name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: LsType.head(context, size: 13.5)),
          ),
          Text('${summary.percent.toStringAsFixed(0)}%',
              style: TextStyle(fontSize: 14, fontWeight: FontWeight.w800, color: color)),
        ]),
        const SizedBox(height: 7),
        LsProgressBar(value: (summary.percent / 100).clamp(0, 1), color: color, height: 5),
        const SizedBox(height: 6),
        Text('${summary.present} / ${summary.total}',
            style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
      ]),
    );
  }
}
