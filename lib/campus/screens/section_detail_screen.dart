import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';
import 'campus_setup_screen.dart' show SetupAccordionSection, SetupAddSheetShell;

// ============================================================
// SECTION DETAIL — staffing + enrollment (Task: campus setup, Phase 2)
//
// Ek section ke andar teen alag cheezein: kaun class-teacher hai, kaun-kaun
// subject teachers hain (pending/approved/rejected), aur kaun-kaun students
// enrolled hain. Teenon apne-apne independent backend endpoints hain —
// `class-teacher-assigmentss`, `subject-teacher-assigmentss`, `enrollments`
// — is screen pe ek jagah la ke dikhaya hai kyunki "ye section staff/
// students se bhara hai" ek hi mental model hai admin/CT ke liye.
//
// `SetupAccordionSection`/`SetupAddSheetShell` — campus_setup_screen.dart
// se reuse — wahi accordion-card pattern jo structural setup me use hua
// tha, taaki poori app me "list + add" ka look same rahe.
// ============================================================

class SectionDetailScreen extends StatefulWidget {
  final SchoolClass schoolClass;
  final Section section;
  final CampusAccess access;

  const SectionDetailScreen({
    super.key,
    required this.schoolClass,
    required this.section,
    required this.access,
  });

  @override
  State<SectionDetailScreen> createState() => _SectionDetailScreenState();
}

class _SectionDetailScreenState extends State<SectionDetailScreen> {
  /// Dropdowns ke liye shared reference data — sirf ek baar load hota hai.
  List<StaffProfile> _staff = const [];
  List<Subject> _subjects = const [];
  bool _loadingRefData = true;

  /// Class teacher apna khud ka accordion nahi hai (list ki jagah ek row),
  /// isliye apna alag state.
  ClassTeacherAssignment? _classTeacher;
  bool _loadingClassTeacher = true;

  @override
  void initState() {
    super.initState();
    _loadRefData();
    _loadClassTeacher();
  }

  Future<void> _loadRefData() async {
    try {
      final results = await Future.wait([
        CampusService.staff(widget.access.campus.id),
        CampusService.subjects(widget.access.campus.id),
      ]);
      if (!mounted) return;
      setState(() {
        _staff = (results[0] as List<StaffProfile>).where((s) => s.isActive).toList();
        _subjects = results[1] as List<Subject>;
        _loadingRefData = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loadingRefData = false);
    }
  }

  Future<void> _loadClassTeacher() async {
    setState(() => _loadingClassTeacher = true);
    try {
      final all = await CampusService.classTeachers();
      if (!mounted) return;
      setState(() {
        _classTeacher = all.where((c) => c.sectionId == widget.section.id).firstOrNull;
        _loadingClassTeacher = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loadingClassTeacher = false);
    }
  }

  bool get _canManageThisSection => widget.access.canManageSection(widget.section.id);

  Future<void> _assignClassTeacher() async {
    final l10n = AppLocalizations.of(context)!;
    final chosen = await showModalBottomSheet<StaffProfile>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _PickStaffSheet(title: l10n.classTeacherAssign, staff: _staff),
    );
    if (chosen == null) return;
    try {
      await CampusService.assignClassTeacher(sectionId: widget.section.id, staffId: chosen.id);
      if (!mounted) return;
      lsSnack(context, l10n.classTeacherAssignedSuccess);
      _loadClassTeacher();
    } on CampusApiException catch (e) {
      if (mounted) lsSnack(context, e.message, error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: lsAppBar(
        context,
        title: l10n.sectionDetailTitle(widget.schoolClass.name, widget.section.name),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(14, 14, 14, 40),
        children: [
          // ---- Class teacher ----
          LsCard(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Icon(Icons.school_outlined, size: 17, color: cs.primary),
                const SizedBox(width: 8),
                Text(l10n.classTeacherTitle, style: LsType.head(context, size: 14)),
              ]),
              const SizedBox(height: 10),
              if (_loadingClassTeacher)
                const LsSkeletonBoxInline()
              else if (_classTeacher != null)
                _StaffRow(staff: _classTeacher!.staff)
              else ...[
                Text(l10n.classTeacherNotAssigned,
                    style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
                if (_canManageThisSection) ...[
                  const SizedBox(height: 10),
                  LsOutlineButton(
                    label: l10n.classTeacherAssign,
                    icon: Icons.person_add_alt_1_rounded,
                    onPressed: _loadingRefData ? null : _assignClassTeacher,
                  ),
                ],
              ],
            ]),
          ),
          const SizedBox(height: 10),

          // ---- Subject teachers ----
          SetupAccordionSection<SubjectTeacherAssignment>(
            icon: Icons.menu_book_outlined,
            title: l10n.subjectTeachersTitle,
            emptyText: l10n.subjectTeachersEmpty,
            addLabel:
                _canManageThisSection ? l10n.subjectTeacherAssign : l10n.subjectTeacherRequest,
            loader: () async {
              final all = await CampusService.subjectTeachers();
              return all.where((r) => r.sectionId == widget.section.id).toList();
            },
            tileBuilder: (row) => _SubjectTeacherTile(
              assignment: row,
              subjectName: _subjects.where((s) => s.id == row.subjectId).map((s) => s.label).firstOrNull,
              canDecide: _canManageThisSection && row.status == 'pending',
              onDecided: () => setState(() {}),
            ),
            onAdd: (_loadingRefData || (!_canManageThisSection && widget.access.staff == null))
                ? null
                : (ctx) => showModalBottomSheet<bool>(
                      context: ctx,
                      isScrollControlled: true,
                      useSafeArea: true,
                      builder: (_) => _AssignSubjectTeacherSheet(
                        sectionId: widget.section.id,
                        subjects: _subjects,
                        staff: _staff,
                        // Management/CT dono ke paas is section pe poora access
                        // hai — unhe seedha "assign" (aur turant approve) karne
                        // dete hain. Baaki koi bhi staff sirf apne liye request
                        // bhej sakta hai (§20 permission matrix).
                        canAssignForOthers: _canManageThisSection,
                        selfStaffId: widget.access.staff?.id,
                      ),
                    ),
          ),

          // ---- Students ----
          SetupAccordionSection<StudentEnrollment>(
            icon: Icons.groups_2_outlined,
            title: l10n.enrollmentsTitle,
            emptyText: l10n.enrollmentsEmpty,
            addLabel: l10n.enrollmentsAdd,
            loader: () => CampusService.enrollments(sectionId: widget.section.id),
            tileBuilder: (e) => _EnrollmentTile(enrollment: e),
            disabledHint: widget.access.canGrowMembership ? null : l10n.setupStaffPendingApproval,
            onAdd: !widget.access.canGrowMembership || widget.access.currentSession == null
                ? null
                : (ctx) => showModalBottomSheet<bool>(
                      context: ctx,
                      isScrollControlled: true,
                      useSafeArea: true,
                      builder: (_) => _EnrollStudentSheet(
                        sectionId: widget.section.id,
                        sessionId: widget.access.currentSession!.id,
                      ),
                    ),
          ),
        ],
      ),
    );
  }
}

extension<T> on Iterable<T> {
  T? get firstOrNull => isEmpty ? null : first;
}

/// `LsSkeletonBox` height ke liye specific import chahiye — yahan sirf ek
/// chhoti height chahiye thi, isliye local alias taaki `skeletons.dart`
/// import na karna pade sirf ek line ke liye.
class LsSkeletonBoxInline extends StatelessWidget {
  const LsSkeletonBoxInline({super.key});
  @override
  Widget build(BuildContext context) => Container(
        height: 40,
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.surfaceVariant,
          borderRadius: BorderRadius.circular(8),
        ),
      );
}

// ============================================================
// Row widgets
// ============================================================

class _StaffRow extends StatelessWidget {
  final StaffProfile? staff;
  const _StaffRow({this.staff});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(children: [
      CircleAvatar(
        radius: 15,
        backgroundColor: cs.primaryContainer,
        child: Text(staff?.user?.initials ?? '?',
            style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: cs.onPrimaryContainer)),
      ),
      const SizedBox(width: 10),
      Text(staff?.user?.displayName ?? staff?.userId ?? '—',
          style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
    ]);
  }
}

class _SubjectTeacherTile extends StatefulWidget {
  final SubjectTeacherAssignment assignment;
  final String? subjectName;
  final bool canDecide;
  final VoidCallback onDecided;

  const _SubjectTeacherTile({
    required this.assignment,
    required this.canDecide,
    required this.onDecided,
    this.subjectName,
  });

  @override
  State<_SubjectTeacherTile> createState() => _SubjectTeacherTileState();
}

class _SubjectTeacherTileState extends State<_SubjectTeacherTile> {
  bool _busy = false;

  Future<void> _decide(bool approve) async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _busy = true);
    try {
      if (approve) {
        await CampusService.approveSubjectTeacher(widget.assignment.id);
        if (mounted) lsSnack(context, l10n.subjectTeacherApproved);
      } else {
        await CampusService.rejectSubjectTeacher(widget.assignment.id);
        if (mounted) lsSnack(context, l10n.subjectTeacherRejected);
      }
      widget.onDecided();
    } on CampusApiException catch (e) {
      if (mounted) lsSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Color _statusColor(ColorScheme cs) => switch (widget.assignment.status) {
        'approved' => Colors.green,
        'rejected' => cs.error,
        _ => Colors.orange,
      };

  String _statusLabel(AppLocalizations l10n) => switch (widget.assignment.status) {
        'approved' => l10n.subjectTeacherStatusApproved,
        'rejected' => l10n.subjectTeacherStatusRejected,
        _ => l10n.subjectTeacherStatusPending,
      };

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final staff = widget.assignment.staff;

    return Row(children: [
      CircleAvatar(
        radius: 14,
        backgroundColor: cs.secondaryContainer,
        child: Text(staff?.user?.initials ?? '?', style: const TextStyle(fontSize: 10, fontWeight: FontWeight.w700)),
      ),
      const SizedBox(width: 9),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(staff?.user?.displayName ?? staff?.userId ?? '—',
              maxLines: 1, overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
          Text(widget.subjectName ?? '—', style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
        ]),
      ),
      if (_busy)
        const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
      else if (widget.canDecide) ...[
        IconButton(
          tooltip: l10n.subjectTeacherApprove,
          icon: const Icon(Icons.check_circle_outline_rounded, size: 20, color: Colors.green),
          onPressed: () => _decide(true),
        ),
        IconButton(
          tooltip: l10n.subjectTeacherReject,
          icon: Icon(Icons.cancel_outlined, size: 20, color: cs.error),
          onPressed: () => _decide(false),
        ),
      ] else
        LsStatusChip(label: _statusLabel(l10n), color: _statusColor(cs)),
    ]);
  }
}

class _EnrollmentTile extends StatelessWidget {
  final StudentEnrollment enrollment;
  const _EnrollmentTile({required this.enrollment});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(children: [
      CircleAvatar(
        radius: 14,
        backgroundColor: cs.tertiaryContainer,
        child: Text(enrollment.student?.initials ?? '?', style: const TextStyle(fontSize: 10, fontWeight: FontWeight.w700)),
      ),
      const SizedBox(width: 9),
      Expanded(
        child: Text(enrollment.student?.displayName ?? enrollment.studentId,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
      ),
      if (enrollment.rollNumber.isNotEmpty) ...[
        LsStatusChip(label: '#${enrollment.rollNumber}', color: cs.primary),
        const SizedBox(width: 6),
      ],
      if (!enrollment.isActive)
        LsStatusChip(label: enrollment.status, color: cs.outline),
    ]);
  }
}

// ============================================================
// Sheets
// ============================================================

class _PickStaffSheet extends StatelessWidget {
  final String title;
  final List<StaffProfile> staff;
  const _PickStaffSheet({required this.title, required this.staff});

  @override
  Widget build(BuildContext context) {
    return SetupAddSheetShell(
      title: title,
      child: Column(
        children: staff
            .map((s) => ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: CircleAvatar(radius: 14, child: Text(s.user?.initials ?? '?', style: const TextStyle(fontSize: 10))),
                  title: Text(s.user?.displayName ?? s.userId),
                  onTap: () => Navigator.pop(context, s),
                ))
            .toList(),
      ),
    );
  }
}

class _AssignSubjectTeacherSheet extends StatefulWidget {
  final String sectionId;
  final List<Subject> subjects;
  final List<StaffProfile> staff;
  final bool canAssignForOthers;
  final String? selfStaffId;

  const _AssignSubjectTeacherSheet({
    required this.sectionId,
    required this.subjects,
    required this.staff,
    required this.canAssignForOthers,
    this.selfStaffId,
  });

  @override
  State<_AssignSubjectTeacherSheet> createState() => _AssignSubjectTeacherSheetState();
}

class _AssignSubjectTeacherSheetState extends State<_AssignSubjectTeacherSheet> {
  String? _subjectId;
  String? _staffId;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _staffId = widget.canAssignForOthers
        ? (widget.staff.isNotEmpty ? widget.staff.first.id : null)
        : widget.selfStaffId;
  }

  Future<void> _submit() async {
    final l10n = AppLocalizations.of(context)!;
    if (_subjectId == null || _staffId == null) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final created = await CampusService.requestSubjectTeacher(
        sectionId: widget.sectionId,
        subjectId: _subjectId!,
        staffId: _staffId!,
      );
      // Management/CT ke paas is section pe already poora access hai —
      // unke banaye row ko turant approve kar dena zyada natural hai;
      // warna wahi banda dobara "Subject teachers" accordion khol ke
      // apna hi request approve karega, jo confusing UX hoga.
      if (widget.canAssignForOthers) {
        await CampusService.approveSubjectTeacher(created.id);
      }
      if (mounted) {
        Navigator.pop(context, true);
        lsSnack(context, widget.canAssignForOthers ? l10n.subjectTeacherApproved : l10n.subjectTeacherRequested);
      }
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
      title: widget.canAssignForOthers ? l10n.subjectTeacherAssign : l10n.subjectTeacherRequest,
      child: Column(children: [
        DropdownButtonFormField<String>(
          value: _subjectId,
          isExpanded: true,
          items: widget.subjects.map((s) => DropdownMenuItem(value: s.id, child: Text(s.label))).toList(),
          decoration: InputDecoration(labelText: l10n.subjectPickLabel, border: const OutlineInputBorder()),
          onChanged: _saving ? null : (v) => setState(() => _subjectId = v),
        ),
        if (widget.canAssignForOthers) ...[
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            value: _staffId,
            isExpanded: true,
            items: widget.staff
                .map((s) => DropdownMenuItem(value: s.id, child: Text(s.user?.displayName ?? s.userId)))
                .toList(),
            decoration: InputDecoration(labelText: l10n.staffPickLabel, border: const OutlineInputBorder()),
            onChanged: _saving ? null : (v) => setState(() => _staffId = v),
          ),
        ],
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

class _EnrollStudentSheet extends StatefulWidget {
  final String sectionId;
  final String sessionId;
  const _EnrollStudentSheet({required this.sectionId, required this.sessionId});

  @override
  State<_EnrollStudentSheet> createState() => _EnrollStudentSheetState();
}

class _EnrollStudentSheetState extends State<_EnrollStudentSheet> {
  final _studentId = TextEditingController();
  final _rollNumber = TextEditingController();
  String _status = 'active';
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _studentId.dispose();
    _rollNumber.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_studentId.text.trim().isEmpty) return;
    final l10n = AppLocalizations.of(context)!;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.enrollStudent(
        studentId: _studentId.text.trim(),
        sectionId: widget.sectionId,
        sessionId: widget.sessionId,
        rollNumber: _rollNumber.text.trim(),
        status: _status,
      );
      if (mounted) {
        Navigator.pop(context, true);
        lsSnack(context, l10n.enrollmentAddedSuccess);
      }
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
      title: l10n.enrollmentsAdd,
      child: Column(children: [
        TextField(
          controller: _studentId,
          enabled: !_saving,
          decoration: InputDecoration(
            labelText: l10n.enrollStudentIdLabel,
            hintText: l10n.enrollStudentIdHint,
            border: const OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _rollNumber,
          enabled: !_saving,
          decoration:
              InputDecoration(labelText: l10n.enrollRollNumberLabel, border: const OutlineInputBorder()),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
          value: _status,
          isExpanded: true,
          items: [
            DropdownMenuItem(value: 'active', child: Text(l10n.enrollStatusActive)),
            DropdownMenuItem(value: 'transferred', child: Text(l10n.enrollStatusTransferred)),
            DropdownMenuItem(value: 'graduated', child: Text(l10n.enrollStatusGraduated)),
          ],
          decoration: InputDecoration(labelText: l10n.enrollStatusLabel, border: const OutlineInputBorder()),
          onChanged: _saving ? null : (v) => setState(() => _status = v ?? _status),
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
