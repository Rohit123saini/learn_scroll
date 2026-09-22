import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// ASSIGNMENT SUBMISSIONS — teacher roster + grading
//
// `GET /assigments-submissions/?assigments=<id>` confirmed roster contract
// (§19). `canGrade` caller se aata hai (`SectionAcademicsScreen` already
// `access.canMarkAttendance(sectionId, subjectId)` check kar chuki hoti
// hai) — yahan dobara derive karne ki zaroorat nahi, bas respect karna hai.
// ============================================================

class AssignmentSubmissionsScreen extends StatefulWidget {
  final CampusAssignment assignment;
  final bool canGrade;
  const AssignmentSubmissionsScreen({super.key, required this.assignment, required this.canGrade});

  @override
  State<AssignmentSubmissionsScreen> createState() => _AssignmentSubmissionsScreenState();
}

class _AssignmentSubmissionsScreenState extends State<AssignmentSubmissionsScreen> {
  bool _loading = true;
  String? _error;
  List<CampusAssignmentSubmission> _rows = const [];

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
      final rows = await CampusService.assignmentSubmissions(widget.assignment.id);
      if (!mounted) return;
      setState(() {
        _rows = rows;
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: lsAppBar(context, title: widget.assignment.title),
      body: RefreshIndicator(onRefresh: _load, child: _body(l10n)),
    );
  }

  Widget _body(AppLocalizations l10n) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 56),
        SizedBox(height: 10),
        LsSkeletonBox(height: 56),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(
          title: l10n.setupLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _load,
        ),
      ]);
    }
    if (_rows.isEmpty) {
      return EmptyStateWidget(icon: Icons.people_outline_rounded, title: l10n.assignmentNoSubmissions);
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 40),
      itemCount: _rows.length,
      itemBuilder: (_, i) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: _SubmissionRow(
          submission: _rows[i],
          canGrade: widget.canGrade,
          onGraded: _load,
        ),
      ),
    );
  }
}

class _SubmissionRow extends StatelessWidget {
  final CampusAssignmentSubmission submission;
  final bool canGrade;
  final VoidCallback onGraded;
  const _SubmissionRow({required this.submission, required this.canGrade, required this.onGraded});

  Color _statusColor(String status, ColorScheme cs) => switch (status) {
        'missing' => cs.error,
        'late' => Colors.orange,
        _ => Colors.green,
      };

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return LsCard(
      onTap: canGrade
          ? () async {
              final graded = await showModalBottomSheet<bool>(
                context: context,
                isScrollControlled: true,
                useSafeArea: true,
                builder: (_) => _GradeSheet(submission: submission),
              );
              if (graded == true) onGraded();
            }
          : null,
      child: Row(children: [
        CircleAvatar(
          radius: 16,
          backgroundColor: cs.primaryContainer,
          child: Text(submission.student?.initials ?? '?',
              style: TextStyle(fontWeight: FontWeight.w700, color: cs.onPrimaryContainer)),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(submission.student?.displayName ?? submission.studentId,
                maxLines: 1, overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
            if (submission.isGraded)
              Text('${l10n.assignmentGradeLabel}: ${submission.grade}',
                  style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
          ]),
        ),
        LsStatusChip(label: submission.status, color: _statusColor(submission.status, cs)),
      ]),
    );
  }
}

class _GradeSheet extends StatefulWidget {
  final CampusAssignmentSubmission submission;
  const _GradeSheet({required this.submission});

  @override
  State<_GradeSheet> createState() => _GradeSheetState();
}

class _GradeSheetState extends State<_GradeSheet> {
  late final _grade = TextEditingController(text: widget.submission.grade ?? '');
  late final _feedback = TextEditingController(text: widget.submission.feedback);
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _grade.dispose();
    _feedback.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_grade.text.trim().isEmpty) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.gradeSubmission(
        submissionId: widget.submission.id,
        grade: _grade.text.trim(),
        feedback: _feedback.text.trim(),
      );
      if (mounted) Navigator.pop(context, true);
    } on CampusApiException catch (e) {
      if (mounted) setState(() {
        _saving = false;
        _error = e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        top: 18,
        bottom: MediaQuery.of(context).viewInsets.bottom + 18,
      ),
      child: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 36,
            height: 4,
            decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
          ),
          const SizedBox(height: 16),
          Align(
            alignment: Alignment.centerLeft,
            child: Text(
                widget.submission.student?.displayName ?? widget.submission.studentId,
                style: LsType.head(context, size: 16)),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _grade,
            enabled: !_saving,
            decoration:
                InputDecoration(labelText: l10n.assignmentGradeLabel, border: const OutlineInputBorder()),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _feedback,
            enabled: !_saving,
            maxLines: 3,
            decoration: InputDecoration(
                labelText: l10n.assignmentFeedbackLabel, border: const OutlineInputBorder()),
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerLeft,
              child: Text(_error!, style: TextStyle(color: cs.error, fontSize: 12.5)),
            ),
          ],
          const SizedBox(height: 12),
          LsPrimaryButton(label: l10n.save, loading: _saving, onPressed: _saving ? null : _submit),
        ]),
      ),
    );
  }
}
