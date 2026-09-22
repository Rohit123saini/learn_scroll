// ============================================================
// LIVECLASS — ASSIGNMENTS SCREEN (classroom scope)
//
// Since the assignment merge, an assignment is ONE unified record
// (`assigments` app, UUID id) whether it was posted personally, by a campus
// section or by a live classroom. This screen is therefore only the
// *classroom-scoped entry point*; the actual assignment experience
// (view, submit free-form / structured, grade, review, publish) is the
// unified `AssignmentDetailScreen`, so both places behave identically.
//
// Backend surface used here:
//   GET  /liveclass/assigmentss/?classroom=<id>      → unified assignments (list)
//   POST /liveclass/assigmentss/  {classroom,title,description,due_date}
//                                                     → post one (manager only)
//   GET  /liveclass/submissions/?classroom=<id>&assigments=<uuid>
//                                                     → grading queue / own rows
// (`assigmentss` is the backend's own spelling — kept as-is.)
//
// Was: ids were parsed as `int` (they are UUIDs → crashed on tap), submissions
// were POSTed to `/liveclass/submissions/` and graded via a POST `…/grade/`
// (both removed server-side in the merge).
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../assignments/screens/assignment_detail_screen.dart';
import '../../assignments/services/assignment_models.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';

class AssignmentsScreen extends StatefulWidget {
  final LiveClassApi api;
  final int classroomId;
  final bool isTeacher;
  const AssignmentsScreen({super.key, required this.api, required this.classroomId, this.isTeacher = false});

  @override
  State<AssignmentsScreen> createState() => _AssignmentsScreenState();
}

class _AssignmentsScreenState extends State<AssignmentsScreen> {
  List<Map<String, dynamic>> _assignments = const [];
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
      final data = await widget.api.assignments(classroomId: widget.classroomId);
      if (!mounted) return;
      setState(() {
        _assignments = data.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList();
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

  Future<void> _open(Map<String, dynamic> m) async {
    final id = m['id']?.toString() ?? '';
    if (id.isEmpty) return;
    if (widget.isTeacher) {
      await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => _SubmissionsScreen(
          api: widget.api,
          classroomId: widget.classroomId,
          assignmentId: id,
          title: m['title']?.toString() ?? '',
        ),
      ));
    } else {
      await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => AssignmentDetailScreen(assignmentId: id),
      ));
    }
    if (mounted) _load();
  }

  Future<void> _create() async {
    final t = AppLocalizations.of(context)!;
    final titleCtrl = TextEditingController();
    final descCtrl = TextEditingController();
    DateTime? due;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setD) => AlertDialog(
          title: Text(t.createCta),
          content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            TextField(controller: titleCtrl, decoration: InputDecoration(labelText: t.assignmentTitleLabel)),
            const SizedBox(height: 8),
            TextField(controller: descCtrl, maxLines: 3),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              icon: const Icon(Icons.event_rounded, size: 16),
              label: Text(due == null ? t.assignmentNoDueDate : '${t.assignmentDueLabel}: ${due!.toIso8601String().substring(0, 10)}'),
              onPressed: () async {
                final now = DateTime.now();
                final d = await showDatePicker(
                  context: ctx,
                  initialDate: now.add(const Duration(days: 7)),
                  firstDate: now,
                  lastDate: now.add(const Duration(days: 365 * 2)),
                );
                if (d != null) setD(() => due = d);
              },
            ),
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(t.cancelCta)),
            TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(t.createCta)),
          ],
        ),
      ),
    );
    if (ok != true || titleCtrl.text.trim().isEmpty) return;
    try {
      await widget.api.createAssignment({
        'classroom': widget.classroomId,
        'title': titleCtrl.text.trim(),
        'description': descCtrl.text.trim(),
        if (due != null) 'due_date': due!.toIso8601String().substring(0, 10),
      });
      _load();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.assignmentsTitle),
      floatingActionButton: widget.isTeacher
          ? FloatingActionButton(onPressed: _create, child: const Icon(Icons.add_rounded))
          : null,
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? ErrorStateWidget(title: t.couldNotLoadAssignments, retryLabel: t.retry, onRetry: _load)
                : _assignments.isEmpty
                    ? EmptyStateWidget(title: t.noAssignmentsYet, icon: Icons.assignment_outlined)
                    : ListView(
                        children: _assignments.map((m) {
                          final due = m['due_date']?.toString();
                          return LsCard(
                            margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                            onTap: () => _open(m),
                            child: Row(children: [
                              Expanded(
                                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                  Text(m['title']?.toString() ?? '', style: LsType.head(context, size: 13.5)),
                                  if (due != null && due.isNotEmpty)
                                    Padding(
                                      padding: const EdgeInsets.only(top: 3),
                                      child: Text(
                                        '${t.assignmentDueLabel}: ${due.length >= 10 ? due.substring(0, 10) : due}',
                                        style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
                                      ),
                                    ),
                                ]),
                              ),
                              Icon(Icons.chevron_right_rounded, color: cs.onSurfaceVariant),
                            ]),
                          );
                        }).toList(),
                      ),
      ),
    );
  }
}

/// Teacher view: every student's submission for one assignment. Tapping a row
/// opens the unified detail screen on that submission, which is where the
/// server-enforced grading / review controls live.
class _SubmissionsScreen extends StatefulWidget {
  final LiveClassApi api;
  final int classroomId;
  final String assignmentId;
  final String title;
  const _SubmissionsScreen({required this.api, required this.classroomId, required this.assignmentId, required this.title});

  @override
  State<_SubmissionsScreen> createState() => _SubmissionsScreenState();
}

class _SubmissionsScreenState extends State<_SubmissionsScreen> {
  List<Map<String, dynamic>> _rows = const [];
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
      final data = await widget.api.submissions(classroomId: widget.classroomId, assignmentId: widget.assignmentId);
      if (!mounted) return;
      setState(() {
        _rows = data.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList();
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

  String _statusLabel(AppLocalizations t, String s) => switch (s) {
        'checked' => t.assignmentStatusChecked,
        'partially_checked' => t.assignmentStatusPartiallyChecked,
        'submitted' => t.assignmentStatusSubmitted,
        'late' => t.assignmentStatusLate,
        _ => t.assignmentStatusMissing,
      };

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: widget.title),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? ErrorStateWidget(title: t.assignmentsErrorTitle, retryLabel: t.retry, onRetry: _load)
                : _rows.isEmpty
                    ? EmptyStateWidget(title: t.assignmentsEmptyTitle, icon: Icons.inbox_outlined)
                    : ListView(
                        children: _rows.map((r) {
                          final roll = r['roll_number']?.toString() ?? '';
                          final enrol = r['enrollment_no']?.toString() ?? '';
                          final who = roll.isNotEmpty ? roll : (enrol.isNotEmpty ? enrol : '#${r['student'] ?? ''}');
                          final marks = r['total_marks_awarded'];
                          return LsCard(
                            margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                            onTap: () async {
                              await Navigator.of(context).push(MaterialPageRoute(
                                builder: (_) => AssignmentDetailScreen(
                                  assignmentId: widget.assignmentId,
                                  initialSubmission: AssignmentSubmission.fromJson(r),
                                ),
                              ));
                              if (mounted) _load();
                            },
                            child: Row(children: [
                              Expanded(child: Text(who, style: LsType.head(context, size: 13.5))),
                              Text(
                                marks != null ? '${_statusLabel(t, r['status']?.toString() ?? '')} · $marks' : _statusLabel(t, r['status']?.toString() ?? ''),
                                style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
                              ),
                              Icon(Icons.chevron_right_rounded, color: cs.onSurfaceVariant),
                            ]),
                          );
                        }).toList(),
                      ),
      ),
    );
  }
}
