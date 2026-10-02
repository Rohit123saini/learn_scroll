import 'package:flutter/material.dart';

import '../../assignments/services/assignment_models.dart';
import '../../assignments/services/assignment_service.dart';
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

class _SubmissionRow extends StatefulWidget {
  final CampusAssignmentSubmission submission;
  final bool canGrade;
  final VoidCallback onGraded;
  const _SubmissionRow({required this.submission, required this.canGrade, required this.onGraded});

  @override
  State<_SubmissionRow> createState() => _SubmissionRowState();
}

class _SubmissionRowState extends State<_SubmissionRow> {
  // Task 6 Part C — duplicate/plagiarism badge. `submission.id` here is
  // the same `assigmentsSubmission.id` the `assigments` app's own
  // `similarity-flags` action reads (`bridge.py` pre-creates one row per
  // roster entry against that shared model — see `assignment_service.dart`'s
  // module docstring) — so this campus-scoped roster screen can call the
  // `assigments` app's endpoint directly, no separate campus-side API needed.
  List<SimilarityFlag> _flags = const [];

  @override
  void initState() {
    super.initState();
    // Only fetched for graders — `IsassigmentsStaffOrOwner` 403s this
    // endpoint for anyone else, and a `missing` submission was never
    // compared against anything (`plagiarism.py` only runs on submit), so
    // there's nothing to fetch for those rows either.
    if (widget.canGrade && widget.submission.status != 'missing') {
      _loadFlags();
    }
  }

  Future<void> _loadFlags() async {
    try {
      final flags = await AssignmentService.getSimilarityFlags(widget.submission.id);
      if (!mounted) return;
      setState(() => _flags = flags);
    } catch (_) {
      // Silent, same posture the backend itself already takes around this
      // feature (`views.py`'s submit actions wrap the similarity check in
      // try/except so a scan issue never costs a student their submit) —
      // a missing badge on one row just means "nothing to show yet", not
      // an error worth surfacing on every row of a roster.
    }
  }

  int get _pendingCount => _flags.where((f) => f.isPending).length;
  bool get _hasConfirmed => _flags.any((f) => f.status == SimilarityFlagStatus.confirmed);

  Future<void> _openFlags() async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _SimilarityFlagsSheet(
        submissionId: widget.submission.id,
        studentName: widget.submission.student?.displayName ?? widget.submission.studentId,
        initialFlags: _flags,
      ),
    );
    if (changed == true) _loadFlags();
  }

  Color _statusColor(String status, ColorScheme cs) => switch (status) {
        'missing' => cs.error,
        'late' => Colors.orange,
        _ => Colors.green,
      };

  Widget _flagBadge({required String label, required Color color, required IconData icon}) {
    return Material(
      color: Colors.transparent,
      borderRadius: BorderRadius.circular(kRadiusChip),
      child: InkWell(
        borderRadius: BorderRadius.circular(kRadiusChip),
        onTap: _openFlags,
        child: LsStatusChip(label: label, color: color, icon: icon),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final submission = widget.submission;

    Widget? flagBadge;
    if (_pendingCount > 0) {
      flagBadge = _flagBadge(
        label: _pendingCount == 1
            ? l10n.assignmentPossibleDuplicate
            : '${l10n.assignmentPossibleDuplicate} ($_pendingCount)',
        color: Colors.red,
        icon: Icons.report_gmailerrorred_rounded,
      );
    } else if (_hasConfirmed) {
      flagBadge = _flagBadge(
        label: l10n.assignmentFlagStatusConfirmed,
        color: Colors.deepOrange,
        icon: Icons.flag_rounded,
      );
    }

    return LsCard(
      onTap: widget.canGrade
          ? () async {
              final graded = await showModalBottomSheet<bool>(
                context: context,
                isScrollControlled: true,
                useSafeArea: true,
                builder: (_) => _GradeSheet(submission: submission),
              );
              if (graded == true) widget.onGraded();
            }
          : null,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
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
        if (flagBadge != null) ...[
          const SizedBox(height: 8),
          Align(alignment: Alignment.centerLeft, child: flagBadge),
        ],
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

// ============================================================
// SIMILARITY / PLAGIARISM FLAGS — Task 6 Part C
//
// `_SimilarityFlagsSheet` lists every possible-duplicate flag involving one
// submission (`GET .../submissions/{id}/similarity-flags/`); `_FlagTile`
// renders one such flag with a Confirm/Dismiss pair for anything still
// `pending`. Both sides of a flag are shown side by side — "this
// submission"'s excerpt vs the matching one's — with the other student's
// name, so a teacher never has to open two separate submissions to judge a
// match. Reviewed flags (`confirmed`/`dismissed`) show their resolved
// status instead of the action buttons — `SubmissionSimilarityFlag.review()`
// on the backend is a one-way transition, there's nothing to undo here.
// ============================================================

class _SimilarityFlagsSheet extends StatefulWidget {
  final String submissionId;
  final String studentName;
  final List<SimilarityFlag> initialFlags;
  const _SimilarityFlagsSheet({
    required this.submissionId,
    required this.studentName,
    required this.initialFlags,
  });

  @override
  State<_SimilarityFlagsSheet> createState() => _SimilarityFlagsSheetState();
}

class _SimilarityFlagsSheetState extends State<_SimilarityFlagsSheet> {
  late List<SimilarityFlag> _flags = widget.initialFlags;
  bool _loading = false;
  String? _error;
  // Row tap already gave us `initialFlags`, so the first paint never shows
  // a loading spinner over data we already have — but a teacher opening
  // this straight after `_SubmissionRow`'s own fetch might still be
  // looking at a slightly stale list (another tab, another reviewer), so
  // silently refresh once in the background.
  bool _changed = false;
  final Set<String> _busyFlagIds = {};

  @override
  void initState() {
    super.initState();
    _refresh(silent: true);
  }

  Future<void> _refresh({bool silent = false}) async {
    if (!silent) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final flags = await AssignmentService.getSimilarityFlags(widget.submissionId);
      if (!mounted) return;
      setState(() {
        _flags = flags;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      // A silent background refresh failing is fine — the row's own
      // `initialFlags` are still shown; only a manual retry tap surfaces
      // the error state.
      if (silent) return;
      setState(() {
        _loading = false;
        _error = e.toString();
      });
    }
  }

  Future<void> _review(SimilarityFlag flag, String status) async {
    setState(() => _busyFlagIds.add(flag.id));
    try {
      final updated = await AssignmentService.reviewSimilarityFlag(
        submissionId: widget.submissionId,
        flagId: flag.id,
        status: status,
      );
      if (!mounted) return;
      setState(() {
        _flags = _flags.map((f) => f.id == updated.id ? updated : f).toList();
        _busyFlagIds.remove(flag.id);
        _changed = true;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _busyFlagIds.remove(flag.id));
      lsSnack(context, e.toString(), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return SizedBox(
      height: MediaQuery.of(context).size.height * 0.78,
      child: Padding(
        padding: EdgeInsets.only(
          left: 16,
          right: 16,
          top: 18,
          bottom: MediaQuery.of(context).viewInsets.bottom + 18,
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Container(
            width: 36,
            height: 4,
            decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
          ),
          const SizedBox(height: 16),
          Row(children: [
            Expanded(
              child: Text(l10n.assignmentSimilarityFlagsTitle, style: LsType.head(context, size: 16)),
            ),
            IconButton(
              onPressed: () => Navigator.pop(context, _changed),
              icon: const Icon(Icons.close_rounded),
            ),
          ]),
          Text(widget.studentName, style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
          const SizedBox(height: 12),
          Expanded(child: _body(l10n)),
        ]),
      ),
    );
  }

  Widget _body(AppLocalizations l10n) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 40),
        ErrorStateWidget(
          title: l10n.assignmentSimilarityFlagsLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: () => _refresh(),
        ),
      ]);
    }
    if (_flags.isEmpty) {
      return EmptyStateWidget(icon: Icons.verified_outlined, title: l10n.assignmentNoSimilarityFlags);
    }
    return ListView.builder(
      itemCount: _flags.length,
      itemBuilder: (_, i) => Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: _FlagTile(
          flag: _flags[i],
          busy: _busyFlagIds.contains(_flags[i].id),
          onConfirm: () => _review(_flags[i], 'confirmed'),
          onDismiss: () => _review(_flags[i], 'dismissed'),
        ),
      ),
    );
  }
}

class _FlagTile extends StatelessWidget {
  final SimilarityFlag flag;
  final bool busy;
  final VoidCallback onConfirm;
  final VoidCallback onDismiss;
  const _FlagTile({required this.flag, required this.busy, required this.onConfirm, required this.onDismiss});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final pct = '${(flag.similarityScore * 100).round()}%';

    return LsCard(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(
            child: Text.rich(
              TextSpan(children: [
                TextSpan(
                  text: '${l10n.assignmentMatchesWith} ',
                  style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant),
                ),
                TextSpan(
                  text: flag.otherStudent,
                  style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
                ),
              ]),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          LsStatusChip(
            label: '${l10n.assignmentSimilarityScoreLabel} $pct',
            color: flag.similarityScore >= 0.9 ? Colors.red : Colors.orange,
          ),
        ]),
        if (!flag.isFreeform && flag.questionText.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(flag.questionText,
              maxLines: 2, overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ],
        const SizedBox(height: 10),
        _excerptBlock(context, label: l10n.assignmentThisStudentAnswer, text: flag.myExcerpt),
        const SizedBox(height: 8),
        _excerptBlock(context, label: l10n.assignmentOtherStudentAnswer, text: flag.otherExcerpt),
        const SizedBox(height: 12),
        if (flag.isPending)
          Row(children: [
            Expanded(
              child: LsOutlineButton(
                label: busy ? '…' : l10n.assignmentDismissFlag,
                onPressed: busy ? null : onDismiss,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: LsPrimaryButton(
                label: l10n.assignmentConfirmDuplicate,
                color: cs.error,
                loading: busy,
                onPressed: busy ? null : onConfirm,
              ),
            ),
          ])
        else
          LsStatusChip(
            label: flag.status == SimilarityFlagStatus.confirmed
                ? l10n.assignmentFlagStatusConfirmed
                : l10n.assignmentFlagStatusDismissed,
            color: flag.status == SimilarityFlagStatus.confirmed ? Colors.deepOrange : Colors.grey,
          ),
      ]),
    );
  }

  Widget _excerptBlock(BuildContext context, {required String label, required String text}) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: cs.surfaceVariant.withOpacity(.5),
        borderRadius: BorderRadius.circular(kRadiusMd),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant)),
        const SizedBox(height: 4),
        Text(text.isEmpty ? '—' : text, maxLines: 4, overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 12, color: cs.onSurface)),
      ]),
    );
  }
}
