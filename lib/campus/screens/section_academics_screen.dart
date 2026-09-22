import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';
import 'campus_setup_screen.dart' show SetupAccordionSection, SetupAddSheetShell;
import 'assignment_submissions_screen.dart';
import 'results_entry_screen.dart';
import 'report_card_screen.dart';

// ============================================================
// SECTION ACADEMICS (Task: campus setup, Phase 3)
//
// Ek section ke academics — assignments, syllabus coverage, results —
// sab yahan se. Do bilkul alag mode:
//   • `myEnrollment` diya gaya (student apni hi section se aaya) → sab
//     kuch read/submit-only: assignment submit karo, syllabus coverage
//     dekho, apna report card dekho.
//   • `myEnrollment` null (staff/management teacher/admin card se aaya) →
//     poora control: assignment post karo, syllabus unit banao/cover
//     mark karo, results entry screen kholo.
//
// Permission ek hi jagah se aata hai: `access.canMarkAttendance(sectionId,
// subjectId)` — backend ka `can_manage_section_subject` isi shape ka hai
// (CT apne section ke kisi bhi subject ke liye, approved ST sirf apne
// subject ke liye, A/P hamesha) — attendance/assigments/syllabus/results
// chaaron isi ek permission function ke peeche hain (§20), isliye naya
// capability banane ki zaroorat nahi padi.
// ============================================================

class SectionAcademicsScreen extends StatefulWidget {
  final String sectionId;
  final String sectionLabel;
  final List<Subject> subjects;
  final CampusAccess access;

  /// Sirf student-flow me set hota hai — campus_screen.dart ke apne
  /// enrollment card se aata hai.
  final StudentEnrollment? myEnrollment;

  const SectionAcademicsScreen({
    super.key,
    required this.sectionId,
    required this.sectionLabel,
    required this.subjects,
    required this.access,
    this.myEnrollment,
  });

  bool get isStudentView => myEnrollment != null;

  @override
  State<SectionAcademicsScreen> createState() => _SectionAcademicsScreenState();
}

class _SectionAcademicsScreenState extends State<SectionAcademicsScreen> {
  String? _syllabusSubjectId;

  /// Ye section ke liye "kuch na kuch" sikha sakte hain — assignment/
  /// syllabus create karne layak subjects (dropdown me sirf inhi ko dikhana
  /// hai, warna backend 403 dega aur user confuse hoga).
  List<Subject> get _teachableSubjects => widget.subjects
      .where((s) => widget.access.canMarkAttendance(widget.sectionId, s.id))
      .toList();

  @override
  void initState() {
    super.initState();
    final subjects = widget.subjects;
    _syllabusSubjectId = subjects.isNotEmpty ? subjects.first.id : null;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    final teachable = _teachableSubjects;

    return Scaffold(
      appBar: lsAppBar(context, title: widget.sectionLabel),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(14, 14, 14, 40),
        children: [
          // ---- Assignments ----
          SetupAccordionSection<CampusAssignment>(
            icon: Icons.assignment_outlined,
            title: l10n.assignmentsTitle,
            emptyText: l10n.assignmentsEmpty,
            addLabel: l10n.assignmentsAdd,
            loader: () => CampusService.assignments(widget.sectionId),
            tileBuilder: (a) => _AssignmentTile(
              assignment: a,
              subjectName: widget.subjects.where((s) => s.id == a.subjectId).map((s) => s.label).firstOrNull,
              onTap: () {
                if (widget.isStudentView) {
                  showModalBottomSheet<void>(
                    context: context,
                    isScrollControlled: true,
                    useSafeArea: true,
                    builder: (_) => _StudentSubmissionSheet(assignment: a),
                  );
                } else {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => AssignmentSubmissionsScreen(
                        assignment: a,
                        canGrade: widget.access.canMarkAttendance(a.sectionId, a.subjectId),
                      ),
                    ),
                  );
                }
              },
            ),
            onAdd: widget.isStudentView || teachable.isEmpty
                ? null
                : (ctx) => showModalBottomSheet<bool>(
                      context: ctx,
                      isScrollControlled: true,
                      useSafeArea: true,
                      builder: (_) => _AddAssignmentSheet(
                        sectionId: widget.sectionId,
                        sessionId: widget.access.currentSession?.id ?? '',
                        subjects: teachable,
                      ),
                    ),
          ),

          // ---- Syllabus ----
          if (widget.subjects.isNotEmpty) ...[
            if (widget.subjects.length > 1)
              Padding(
                padding: const EdgeInsets.fromLTRB(4, 4, 4, 6),
                child: Row(mainAxisAlignment: MainAxisAlignment.end, children: [
                  Text(l10n.subjectPickLabel, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                  const SizedBox(width: 8),
                  DropdownButton<String>(
                    value: _syllabusSubjectId,
                    underline: const SizedBox.shrink(),
                    items: widget.subjects
                        .map((s) => DropdownMenuItem(value: s.id, child: Text(s.label)))
                        .toList(),
                    onChanged: (v) => setState(() => _syllabusSubjectId = v),
                  ),
                ]),
              ),
            if (_syllabusSubjectId != null)
              SetupAccordionSection<_SyllabusRow>(
                key: ValueKey('syllabus-$_syllabusSubjectId'),
                icon: Icons.checklist_rtl_rounded,
                title: l10n.syllabusTitle,
                emptyText: l10n.syllabusEmpty,
                addLabel: l10n.syllabusAdd,
                loader: () => _loadSyllabus(_syllabusSubjectId!),
                tileBuilder: (row) => _SyllabusTile(
                  row: row,
                  canMark: !widget.isStudentView &&
                      widget.access.canMarkAttendance(widget.sectionId, _syllabusSubjectId!),
                  onMarked: () => setState(() {}),
                ),
                onAdd: widget.isStudentView ||
                        !widget.access.canMarkAttendance(widget.sectionId, _syllabusSubjectId!)
                    ? null
                    : (ctx) => showModalBottomSheet<bool>(
                          context: ctx,
                          isScrollControlled: true,
                          useSafeArea: true,
                          builder: (_) => _AddSyllabusUnitSheet(
                            subjectId: _syllabusSubjectId!,
                            sectionId: widget.sectionId,
                            sessionId: widget.access.currentSession?.id ?? '',
                          ),
                        ),
              ),
          ],

          // ---- Results ----
          const SizedBox(height: 4),
          LsCard(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Icon(Icons.grading_outlined, size: 17, color: cs.primary),
                const SizedBox(width: 8),
                Text(widget.isStudentView ? l10n.reportCardTitle : l10n.resultsTitle,
                    style: LsType.head(context, size: 14)),
              ]),
              const SizedBox(height: 10),
              if (widget.isStudentView)
                LsOutlineButton(
                  label: l10n.reportCardView,
                  icon: Icons.description_outlined,
                  onPressed: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => ReportCardScreen(
                        enrollment: widget.myEnrollment!,
                        subjects: widget.subjects,
                        sessionId: widget.access.currentSession?.id,
                      ),
                    ),
                  ),
                )
              else
                LsOutlineButton(
                  label: l10n.resultsManage,
                  icon: Icons.edit_note_rounded,
                  onPressed: teachable.isEmpty
                      ? null
                      : () => Navigator.push(
                            context,
                            MaterialPageRoute(
                              builder: (_) => ResultsEntryScreen(
                                sectionId: widget.sectionId,
                                sectionLabel: widget.sectionLabel,
                                subjects: teachable,
                                access: widget.access,
                              ),
                            ),
                          ),
                ),
            ]),
          ),
        ],
      ),
    );
  }

  Future<List<_SyllabusRow>> _loadSyllabus(String subjectId) async {
    final units = await CampusService.syllabusUnits(subjectId: subjectId, sectionId: widget.sectionId);
    final progressLists = await Future.wait(units.map((u) => CampusService.syllabusProgressFor(u.id)));
    return [
      for (var i = 0; i < units.length; i++)
        _SyllabusRow(unit: units[i], progress: progressLists[i].isNotEmpty ? progressLists[i].first : null),
    ];
  }
}

extension<T> on Iterable<T> {
  T? get firstOrNull => isEmpty ? null : first;
}

class _SyllabusRow {
  final SyllabusUnit unit;
  final SyllabusProgress? progress;
  const _SyllabusRow({required this.unit, this.progress});
}

// ============================================================
// Row widgets
// ============================================================

class _AssignmentTile extends StatelessWidget {
  final CampusAssignment assignment;
  final String? subjectName;
  final VoidCallback onTap;
  const _AssignmentTile({required this.assignment, required this.onTap, this.subjectName});

  static String _fmt(DateTime d) =>
      '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')}/${d.year}';

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(10),
      child: Row(children: [
        Icon(Icons.assignment_outlined, size: 17, color: cs.onSurfaceVariant),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(assignment.title,
                maxLines: 1, overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
            Text(
              [
                if (subjectName != null) subjectName!,
                if (assignment.dueDate != null) _fmt(assignment.dueDate!),
              ].join(' · '),
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
            ),
          ]),
        ),
        const Icon(Icons.chevron_right_rounded, size: 18),
      ]),
    );
  }
}

class _SyllabusTile extends StatefulWidget {
  final _SyllabusRow row;
  final bool canMark;
  final VoidCallback onMarked;
  const _SyllabusTile({required this.row, required this.canMark, required this.onMarked});

  @override
  State<_SyllabusTile> createState() => _SyllabusTileState();
}

class _SyllabusTileState extends State<_SyllabusTile> {
  bool _marking = false;

  static String _fmt(DateTime d) =>
      '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')}/${d.year}';

  Future<void> _markCovered() async {
    final progress = widget.row.progress;
    if (progress == null) return;
    setState(() => _marking = true);
    try {
      await CampusService.markSyllabusCovered(progress.id);
      widget.onMarked();
    } on CampusApiException catch (e) {
      if (mounted) lsSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _marking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final covered = widget.row.progress?.isCovered ?? false;
    return Row(children: [
      Icon(covered ? Icons.check_circle_rounded : Icons.radio_button_unchecked_rounded,
          size: 17, color: covered ? Colors.green : cs.onSurfaceVariant),
      const SizedBox(width: 10),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(widget.row.unit.title, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
          if (covered && widget.row.progress?.coveredOn != null)
            Text(l10n.syllabusCoveredOn(_fmt(widget.row.progress!.coveredOn!)),
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
        ]),
      ),
      if (!covered && widget.canMark)
        _marking
            ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
            : TextButton(onPressed: _markCovered, child: Text(l10n.syllabusMarkCovered)),
    ]);
  }
}

// ============================================================
// Sheets
// ============================================================

class _AddAssignmentSheet extends StatefulWidget {
  final String sectionId;
  final String sessionId;
  final List<Subject> subjects;
  const _AddAssignmentSheet({required this.sectionId, required this.sessionId, required this.subjects});

  @override
  State<_AddAssignmentSheet> createState() => _AddAssignmentSheetState();
}

class _AddAssignmentSheetState extends State<_AddAssignmentSheet> {
  final _title = TextEditingController();
  final _description = TextEditingController();
  String? _subjectId;
  DateTime? _dueDate;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _subjectId = widget.subjects.isNotEmpty ? widget.subjects.first.id : null;
  }

  @override
  void dispose() {
    _title.dispose();
    _description.dispose();
    super.dispose();
  }

  Future<void> _pickDueDate() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: _dueDate ?? now,
      firstDate: DateTime(now.year - 1),
      lastDate: DateTime(now.year + 5),
    );
    if (picked != null) setState(() => _dueDate = picked);
  }

  Future<void> _submit() async {
    if (_title.text.trim().isEmpty || _subjectId == null) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.createAssignment(
        sectionId: widget.sectionId,
        subjectId: _subjectId!,
        sessionId: widget.sessionId,
        title: _title.text.trim(),
        description: _description.text.trim(),
        dueDate: _dueDate,
      );
      if (mounted) Navigator.pop(context, true);
    } on CampusApiException catch (e) {
      if (mounted) setState(() {
        _saving = false;
        _error = e.message;
      });
    }
  }

  static String _fmt(DateTime d) =>
      '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')}/${d.year}';

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return SetupAddSheetShell(
      title: l10n.assignmentsAdd,
      child: Column(children: [
        DropdownButtonFormField<String>(
          value: _subjectId,
          isExpanded: true,
          items: widget.subjects.map((s) => DropdownMenuItem(value: s.id, child: Text(s.label))).toList(),
          decoration: InputDecoration(labelText: l10n.subjectPickLabel, border: const OutlineInputBorder()),
          onChanged: _saving ? null : (v) => setState(() => _subjectId = v),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _title,
          enabled: !_saving,
          decoration:
              InputDecoration(labelText: l10n.assignmentTitleLabel, border: const OutlineInputBorder()),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _description,
          enabled: !_saving,
          maxLines: 3,
          decoration: InputDecoration(
              labelText: l10n.assignmentDescriptionLabel, border: const OutlineInputBorder()),
        ),
        const SizedBox(height: 12),
        OutlinedButton.icon(
          onPressed: _saving ? null : _pickDueDate,
          icon: const Icon(Icons.event_outlined, size: 16),
          label: Text(_dueDate == null ? l10n.assignmentDueDateLabel : _fmt(_dueDate!)),
        ),
        // Attachment upload abhi is app se wire nahi hai — backend field
        // (`attachment: file|null`) optional hai, isliye text-only bhi
        // 201 deta hai. File-picker aane par yahin ek field jodna hai.
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
    );
  }
}

class _AddSyllabusUnitSheet extends StatefulWidget {
  final String subjectId;
  final String sectionId;
  final String sessionId;
  const _AddSyllabusUnitSheet({required this.subjectId, required this.sectionId, required this.sessionId});

  @override
  State<_AddSyllabusUnitSheet> createState() => _AddSyllabusUnitSheetState();
}

class _AddSyllabusUnitSheetState extends State<_AddSyllabusUnitSheet> {
  final _title = TextEditingController();
  final _order = TextEditingController(text: '0');
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _title.dispose();
    _order.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_title.text.trim().isEmpty) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.createSyllabusUnit(
        subjectId: widget.subjectId,
        sectionId: widget.sectionId,
        sessionId: widget.sessionId,
        title: _title.text.trim(),
        order: int.tryParse(_order.text.trim()) ?? 0,
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
    return SetupAddSheetShell(
      title: l10n.syllabusAdd,
      child: Column(children: [
        TextField(
          controller: _title,
          enabled: !_saving,
          decoration:
              InputDecoration(labelText: l10n.syllabusUnitTitleLabel, border: const OutlineInputBorder()),
          onSubmitted: (_) => _submit(),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _order,
          enabled: !_saving,
          keyboardType: TextInputType.number,
          decoration:
              InputDecoration(labelText: l10n.syllabusUnitOrderLabel, border: const OutlineInputBorder()),
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
    );
  }
}

/// Student ka apna submission — status dekhna aur (agar abhi tak nahi kiya)
/// submit karna. File upload abhi wire nahi (§ note upar) — "submit" khaali
/// body ke saath bhi valid hai (backend `submitted_at`/`status` khud set
/// karta hai).
class _StudentSubmissionSheet extends StatefulWidget {
  final CampusAssignment assignment;
  const _StudentSubmissionSheet({required this.assignment});

  @override
  State<_StudentSubmissionSheet> createState() => _StudentSubmissionSheetState();
}

class _StudentSubmissionSheetState extends State<_StudentSubmissionSheet> {
  bool _loading = true;
  String? _error;
  CampusAssignmentSubmission? _mine;
  bool _submitting = false;

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
      // Apni row dhoondo — nahi mili to edge-case create karo (§19: student
      // assignment post hone ke baad enroll hua ho to uski row missing
      // ho sakti hai).
      CampusAssignmentSubmission? mine = rows.isNotEmpty ? rows.first : null;
      mine ??= await CampusService.ensureOwnSubmission(widget.assignment.id);
      if (!mounted) return;
      setState(() {
        _mine = mine;
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

  Future<void> _submit() async {
    final mine = _mine;
    if (mine == null) return;
    setState(() => _submitting = true);
    try {
      final updated = await CampusService.submitAssignment(mine.id);
      if (mounted) {
        setState(() {
          _mine = updated;
          _submitting = false;
        });
        lsSnack(context, AppLocalizations.of(context)!.assignmentSubmitSuccess);
      }
    } on CampusApiException catch (e) {
      if (mounted) {
        setState(() => _submitting = false);
        lsSnack(context, e.message, error: true);
      }
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
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Container(
          width: 36,
          height: 4,
          decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
        ),
        const SizedBox(height: 16),
        Align(
          alignment: Alignment.centerLeft,
          child: Text(widget.assignment.title, style: LsType.head(context, size: 16)),
        ),
        if (widget.assignment.description.isNotEmpty) ...[
          const SizedBox(height: 6),
          Align(
            alignment: Alignment.centerLeft,
            child: Text(widget.assignment.description,
                style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant, height: 1.4)),
          ),
        ],
        const SizedBox(height: 16),
        if (_loading)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 20),
            child: CircularProgressIndicator(),
          )
        else if (_error != null)
          Text(_error!, style: TextStyle(color: cs.error, fontSize: 12.5))
        else if (_mine != null) ...[
          LsStatusChip(
            label: _mine!.isSubmitted ? _mine!.status : l10n.assignmentNotSubmitted,
            color: _mine!.isSubmitted ? Colors.green : Colors.orange,
          ),
          if (_mine!.isGraded) ...[
            const SizedBox(height: 10),
            LsMetaRow(icon: Icons.grade_outlined, label: l10n.assignmentGradeLabel, value: _mine!.grade!),
            if (_mine!.feedback.isNotEmpty) ...[
              const SizedBox(height: 6),
              Align(
                alignment: Alignment.centerLeft,
                child: Text(_mine!.feedback, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
              ),
            ],
          ],
          if (!_mine!.isSubmitted) ...[
            const SizedBox(height: 14),
            LsPrimaryButton(
              label: l10n.assignmentSubmitCta,
              icon: Icons.check_rounded,
              loading: _submitting,
              onPressed: _submitting ? null : _submit,
            ),
          ],
        ],
      ]),
    );
  }
}
