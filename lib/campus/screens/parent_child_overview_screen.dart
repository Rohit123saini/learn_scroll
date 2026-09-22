import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';
import 'attendance_summary_screen.dart';
import 'report_card_screen.dart';

// ============================================================
// PARENT — CHILD OVERVIEW  [Task 4 gap-fix]
//
// Pehle `ParentLinkScreen` (parent_link_screen.dart) me linked children
// sirf ek dead-end list the — `_LinkTile` ka koi `onTap` hi nahi tha,
// tap karne par kuch nahi khulta tha. Backend (attendance/summary,
// results/report-card, notices) sab pehle se `is_linked_parent_of_student`
// check karke parent ko padhne dete the — bas iske liye koi screen nahi
// thi. `AttendanceSummaryScreen`/`ReportCardScreen` dono apne hi header
// comment me "student / parent / staff view" likhte hain — wahi do
// screens yahan reuse ho rahi hain, koi naya attendance/report-card UI
// nahi likha.
//
// `CampusParentLinkSerializer.active_enrollment` (naya, backend FIX) se
// mila `enrollment` id/section/class/department seedha `StudentEnrollment`
// bana dete hain — is screen ko khud koi extra "enrollment resolve karo"
// call nahi karni padti.
// ============================================================

class ParentChildOverviewScreen extends StatefulWidget {
  final CampusParentLink link;
  const ParentChildOverviewScreen({super.key, required this.link});

  @override
  State<ParentChildOverviewScreen> createState() => _ParentChildOverviewScreenState();
}

class _ParentChildOverviewScreenState extends State<ParentChildOverviewScreen> {
  bool _loading = true;
  String? _error;
  List<Subject> _subjects = const [];
  List<Notice> _notices = const [];

  ParentChildEnrollment? get _enr => widget.link.activeEnrollment;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final enr = _enr;
    if (enr == null) {
      // Bachcha is campus me abhi kisi active section me enroll nahi hai
      // (transfer/graduate ho chuka, ya enroll hi nahi hua) — attendance/
      // report-card/notices me se koi bhi call karne ka matlab nahi,
      // seedha empty state.
      setState(() => _loading = false);
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        CampusService.subjects(widget.link.campusId),
        CampusService.notices(widget.link.campusId),
      ]);
      if (!mounted) return;
      final subjects = results[0] as List<Subject>;
      final allNotices = results[1] as List<Notice>;
      // Whole-campus notices (scope fields sab null) + jo iske apne
      // section/class/department ke liye hain — baaki campus ke doosre
      // classes/sections ke notices is bachche se related nahi hain.
      final notices = allNotices.where((n) {
        final wholeCampus = n.sectionId == null && n.schoolClassId == null && n.departmentId == null;
        return wholeCampus ||
            n.sectionId == enr.sectionId ||
            (enr.schoolClassId != null && n.schoolClassId == enr.schoolClassId) ||
            (enr.departmentId != null && n.departmentId == enr.departmentId);
      }).toList()
        ..sort((a, b) => (b.createdAt ?? DateTime(0)).compareTo(a.createdAt ?? DateTime(0)));
      setState(() {
        _subjects = subjects;
        _notices = notices;
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

  StudentEnrollment _asStudentEnrollment(ParentChildEnrollment enr) => StudentEnrollment(
        id: enr.id,
        studentId: widget.link.studentId,
        student: widget.link.student,
        sectionId: enr.sectionId,
        sessionId: enr.sessionId,
        status: 'active',
      );

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    final childName = widget.link.student?.displayName ?? widget.link.studentId;

    return Scaffold(
      appBar: AppBar(title: Text(childName, style: LsType.head(context, size: 15))),
      body: RefreshIndicator(onRefresh: _load, child: _body(context, l10n, cs)),
    );
  }

  Widget _body(BuildContext context, AppLocalizations l10n, ColorScheme cs) {
    final enr = _enr;

    if (enr == null) {
      return ListView(children: [
        const SizedBox(height: 60),
        EmptyStateWidget(
          icon: Icons.school_outlined,
          title: l10n.parentOverviewNoEnrollment,
        ),
      ]);
    }

    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 72),
        SizedBox(height: 10),
        LsSkeletonBox(height: 72),
        SizedBox(height: 10),
        LsSkeletonBox(height: 92),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(
          title: l10n.parentOverviewLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _load,
        ),
      ]);
    }

    final enrollment = _asStudentEnrollment(enr);

    return ListView(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 90),
      children: [
        LsCard(
          child: Row(children: [
            CircleAvatar(
              radius: 20,
              backgroundColor: cs.primaryContainer,
              child: Text(widget.link.student?.initials ?? '?',
                  style: TextStyle(fontWeight: FontWeight.w700, color: cs.onPrimaryContainer)),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(widget.link.student?.displayName ?? widget.link.studentId,
                    style: LsType.head(context, size: 15)),
                Text(enr.label, style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
              ]),
            ),
          ]),
        ),
        const SizedBox(height: 14),
        LsCard(
          onTap: () => Navigator.push(
            context,
            MaterialPageRoute(
              builder: (_) => AttendanceSummaryScreen(
                enrollment: enrollment,
                sectionLabel: enr.label,
                subjects: _subjects,
              ),
            ),
          ),
          child: Row(children: [
            Icon(Icons.fact_check_outlined, size: 20, color: cs.primary),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(l10n.campusAttendanceLabel, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5)),
                Text(l10n.campusTapToView, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
              ]),
            ),
            Icon(Icons.chevron_right_rounded, color: cs.outline),
          ]),
        ),
        const SizedBox(height: 10),
        LsCard(
          onTap: () => Navigator.push(
            context,
            MaterialPageRoute(
              builder: (_) => ReportCardScreen(
                enrollment: enrollment,
                subjects: _subjects,
                sessionId: enr.sessionId,
              ),
            ),
          ),
          child: Row(children: [
            Icon(Icons.assessment_outlined, size: 20, color: cs.primary),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(l10n.parentOverviewReportCard, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5)),
                Text(l10n.campusTapToView, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
              ]),
            ),
            Icon(Icons.chevron_right_rounded, color: cs.outline),
          ]),
        ),
        const SizedBox(height: 18),
        Padding(
          padding: const EdgeInsets.only(left: 4, bottom: 8),
          child: Text(l10n.campusNoticesTitle, style: LsType.head(context, size: 14)),
        ),
        if (_notices.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 10),
            child: Text(l10n.parentOverviewNoNotices,
                style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
          )
        else
          for (final n in _notices.take(10))
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: LsCard(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(n.title, style: LsType.head(context, size: 13.5)),
                  const SizedBox(height: 6),
                  Text(n.body,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 12.5, height: 1.4, color: cs.onSurfaceVariant)),
                ]),
              ),
            ),
      ],
    );
  }
}
