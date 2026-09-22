import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';
import 'class_sections_setup_screen.dart';

// ============================================================
// CAMPUS — STRUCTURAL SETUP (Task: campus setup screens, Phase 1)
//
// Admin/principal ka "build the campus" screen — sessions, departments,
// classes, subjects, rooms, staff. Har cheez `access.canManageCampusSetup`
// (== isManagement) ke peeche hai, jo caller (campus_screen.dart) already
// check karta hai is screen pe push karne se pehle — par yahan bhi ek
// safety net rakha hai (`_Locked` widget) agar koi is screen pe seedha
// deep-link kar de.
//
// Design: ek scroll-able list of accordion cards, TabBar nahi — kyunki har
// section ka apna alag "add" form hai aur ek saath sab tabs preload karna
// (7 API calls upfront) hub screen ko slow bana deta. ExpansionTile se har
// section apni marzi se load hoti hai, aur zyada natural "setup checklist"
// jaisa feel deta hai.
//
// Sections ka apna hierarchy hai (school_class ke andar) isliye wo yahan
// nahi — Classes card ke har row se `ClassSectionsSetupScreen` khulti hai.
// ============================================================

class CampusSetupScreen extends StatefulWidget {
  final CampusAccess access;
  const CampusSetupScreen({super.key, required this.access});

  @override
  State<CampusSetupScreen> createState() => _CampusSetupScreenState();
}

class _CampusSetupScreenState extends State<CampusSetupScreen> {
  /// Classes tab ko Sessions/Departments dono chahiye (dropdowns ke liye),
  /// aur Subjects tab ko Departments chahiye. In teeno ko yahan ek baar
  /// load karke bachche widgets ko pass karna — har card apna khud ka
  /// `SetupAccordionSection` state rakhta hai (list/add/reload), par ye teen lists
  /// shared hain taaki "class add karo" ke baad "session dropdown" turant
  /// naya session dikhaye bina refetch kiye.
  List<AcademicSession> _sessions = const [];
  List<Department> _departments = const [];
  bool _loadingShared = true;

  /// Exam terms `session` FK ke saath scoped hain — Sessions ki tarah campus-
  /// wide nahi, ek session ke andar. Isliye alag dropdown se select karna
  /// padta hai (default: current session).
  String? _examTermSessionId;

  @override
  void initState() {
    super.initState();
    _loadShared();
  }

  Future<void> _loadShared() async {
    setState(() => _loadingShared = true);
    try {
      final results = await Future.wait([
        CampusService.sessions(widget.access.campus.id),
        CampusService.departments(widget.access.campus.id),
      ]);
      if (!mounted) return;
      setState(() {
        _sessions = results[0] as List<AcademicSession>;
        _departments = results[1] as List<Department>;
        _examTermSessionId ??= widget.access.currentSession?.id ??
            (_sessions.isNotEmpty ? _sessions.first.id : null);
        _loadingShared = false;
      });
    } catch (_) {
      // Sessions/Departments cards apna khud ka error state dikhayenge —
      // ye sirf dropdowns ke liye cache hai, is fail hone se poori screen
      // block nahi honi chahiye.
      if (mounted) setState(() => _loadingShared = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    final access = widget.access;

    if (!access.canManageCampusSetup) {
      return Scaffold(
        appBar: lsAppBar(context, title: l10n.campusSetupTitle),
        body: EmptyStateWidget(
          icon: Icons.lock_outline_rounded,
          title: l10n.campusNoAttendancePermissionTitle,
        ),
      );
    }

    final campusId = access.campus.id;

    return Scaffold(
      appBar: lsAppBar(context, title: l10n.campusSetupTitle),
      body: RefreshIndicator(
        onRefresh: _loadShared,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(14, 14, 14, 40),
          children: [
            if (!access.campus.isApproved)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: LsCard(
                  borderColor: Colors.orange,
                  child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Icon(Icons.hourglass_top_rounded, size: 18, color: Colors.orange),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(l10n.setupStaffPendingApproval,
                          style: TextStyle(
                              fontSize: 12.5,
                              height: 1.4,
                              color: Theme.of(context).colorScheme.onSurfaceVariant)),
                    ),
                  ]),
                ),
              ),

            // ---- Sessions ----
            SetupAccordionSection<AcademicSession>(
              key: const ValueKey('sessions'),
              icon: Icons.event_note_rounded,
              title: l10n.setupSessionsTitle,
              emptyText: l10n.setupSessionsEmpty,
              addLabel: l10n.setupSessionsAdd,
              loader: () => CampusService.sessions(campusId),
              onLoaded: (rows) => setState(() => _sessions = rows),
              tileBuilder: (s) => _SessionTile(session: s, onChanged: () => setState(() {})),
              onAdd: (ctx) => showModalBottomSheet<bool>(
                context: ctx,
                isScrollControlled: true,
                useSafeArea: true,
                builder: (_) => _AddSessionSheet(campusId: campusId),
              ),
            ),

            // ---- Departments ----
            SetupAccordionSection<Department>(
              key: const ValueKey('departments'),
              icon: Icons.account_tree_outlined,
              title: l10n.setupDepartmentsTitle,
              emptyText: l10n.setupDepartmentsEmpty,
              addLabel: l10n.setupDepartmentsAdd,
              loader: () => CampusService.departments(campusId),
              onLoaded: (rows) => setState(() => _departments = rows),
              tileBuilder: (d) => _NameTile(icon: Icons.account_tree_outlined, name: d.name),
              onAdd: (ctx) => showModalBottomSheet<bool>(
                context: ctx,
                isScrollControlled: true,
                useSafeArea: true,
                builder: (_) => _AddDepartmentSheet(campusId: campusId),
              ),
            ),

            // ---- Classes ----
            SetupAccordionSection<SchoolClass>(
              key: const ValueKey('classes'),
              icon: Icons.class_outlined,
              title: l10n.setupClassesTitle,
              emptyText: l10n.setupClassesEmpty,
              addLabel: l10n.setupClassesAdd,
              loader: () => CampusService.classes(campusId),
              tileBuilder: (c) => _ClassTile(
                schoolClass: c,
                departmentName: _departments
                    .where((d) => d.id == c.departmentId)
                    .map((d) => d.name)
                    .firstOrNull,
                sessionName:
                    _sessions.where((s) => s.id == c.sessionId).map((s) => s.name).firstOrNull,
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => ClassSectionsSetupScreen(schoolClass: c, access: access),
                  ),
                ),
              ),
              onAdd: (ctx) {
                if (_sessions.isEmpty) {
                  lsSnack(ctx, l10n.classNoSessionsError, error: true);
                  return Future.value(null);
                }
                return showModalBottomSheet<bool>(
                  context: ctx,
                  isScrollControlled: true,
                  useSafeArea: true,
                  builder: (_) => _AddClassSheet(
                    campusId: campusId,
                    sessions: _sessions,
                    departments: _departments,
                    defaultSessionId: access.currentSession?.id,
                  ),
                );
              },
            ),

            // ---- Subjects ----
            SetupAccordionSection<Subject>(
              key: const ValueKey('subjects'),
              icon: Icons.menu_book_outlined,
              title: l10n.setupSubjectsTitle,
              emptyText: l10n.setupSubjectsEmpty,
              addLabel: l10n.setupSubjectsAdd,
              loader: () => CampusService.subjects(campusId),
              tileBuilder: (s) => _NameTile(
                icon: Icons.menu_book_outlined,
                name: s.label,
                subtitle: _departments
                    .where((d) => d.id == s.departmentId)
                    .map((d) => d.name)
                    .firstOrNull,
              ),
              onAdd: (ctx) => showModalBottomSheet<bool>(
                context: ctx,
                isScrollControlled: true,
                useSafeArea: true,
                builder: (_) => _AddSubjectSheet(campusId: campusId, departments: _departments),
              ),
            ),

            // ---- Rooms ----
            SetupAccordionSection<Room>(
              key: const ValueKey('rooms'),
              icon: Icons.meeting_room_outlined,
              title: l10n.setupRoomsTitle,
              emptyText: l10n.setupRoomsEmpty,
              addLabel: l10n.setupRoomsAdd,
              loader: () => CampusService.rooms(campusId),
              tileBuilder: (r) => _NameTile(
                icon: r.isVirtual ? Icons.videocam_outlined : Icons.meeting_room_outlined,
                name: r.name,
                subtitle: r.isVirtual ? l10n.roomVirtualLabel : null,
              ),
              onAdd: (ctx) => showModalBottomSheet<bool>(
                context: ctx,
                isScrollControlled: true,
                useSafeArea: true,
                builder: (_) => _AddRoomSheet(campusId: campusId),
              ),
            ),

            // ---- Staff ----
            SetupAccordionSection<StaffProfile>(
              key: const ValueKey('staff'),
              icon: Icons.badge_outlined,
              title: l10n.setupStaffTitle,
              emptyText: l10n.setupStaffEmpty,
              addLabel: l10n.setupStaffAdd,
              loader: () => CampusService.staff(campusId),
              disabledHint: access.campus.isApproved ? null : l10n.setupStaffPendingApproval,
              tileBuilder: (s) => _StaffTile(staff: s),
              onAdd: !access.campus.isApproved
                  ? null
                  : (ctx) => showModalBottomSheet<bool>(
                        context: ctx,
                        isScrollControlled: true,
                        useSafeArea: true,
                        builder: (_) => _AddStaffSheet(campusId: campusId),
                      ),
            ),

            // ---- Exam terms (session-scoped) ----
            if (_sessions.length > 1)
              Padding(
                padding: const EdgeInsets.fromLTRB(4, 4, 4, 6),
                child: Row(mainAxisAlignment: MainAxisAlignment.end, children: [
                  Text(l10n.classSessionLabel, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                  const SizedBox(width: 8),
                  DropdownButton<String>(
                    value: _examTermSessionId,
                    underline: const SizedBox.shrink(),
                    items: _sessions.map((s) => DropdownMenuItem(value: s.id, child: Text(s.name))).toList(),
                    onChanged: (v) => setState(() => _examTermSessionId = v),
                  ),
                ]),
              ),
            if (_examTermSessionId != null)
              SetupAccordionSection<ExamTerm>(
                // Session badalte hi accordion ko naya widget instance dena
                // hai taaki purani list dobara load ho — generic widget khud
                // subscribe/refresh nahi karta jab bahar se koi dependency
                // badle.
                key: ValueKey('exam-terms-$_examTermSessionId'),
                icon: Icons.fact_check_outlined,
                title: l10n.setupExamTermsTitle,
                emptyText: l10n.setupExamTermsEmpty,
                addLabel: l10n.setupExamTermsAdd,
                loader: () => CampusService.examTerms(_examTermSessionId!),
                tileBuilder: (t) => _ExamTermTile(term: t),
                onAdd: (ctx) => showModalBottomSheet<bool>(
                  context: ctx,
                  isScrollControlled: true,
                  useSafeArea: true,
                  builder: (_) => _AddExamTermSheet(sessionId: _examTermSessionId!),
                ),
              ),

            // ---- Fee structures (opt-in — §8) ----
            const SizedBox(height: 10),
            if (!access.campus.feeModuleEnabled)
              LsCard(
                tinted: true,
                child: Row(children: [
                  Icon(Icons.toll_outlined, size: 17, color: cs.onSurfaceVariant),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(l10n.feeModuleDisabledNote,
                        style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
                  ),
                ]),
              )
            else
              SetupAccordionSection<FeeStructure>(
                icon: Icons.toll_outlined,
                title: l10n.setupFeeStructuresTitle,
                emptyText: l10n.setupFeeStructuresEmpty,
                addLabel: l10n.setupFeeStructuresAdd,
                loader: () => CampusService.feeStructures(campusId),
                tileBuilder: (fs) => _FeeStructureTile(structure: fs),
                onAdd: _examTermSessionId == null
                    ? null
                    : (ctx) => showModalBottomSheet<bool>(
                          context: ctx,
                          isScrollControlled: true,
                          useSafeArea: true,
                          builder: (_) => _AddFeeStructureSheet(
                            campusId: campusId,
                            sessionId: _examTermSessionId!,
                          ),
                        ),
              ),
          ],
        ),
      ),
    );
  }
}

extension<T> on Iterable<T> {
  T? get firstOrNull => isEmpty ? null : first;
}

// ============================================================
// Generic accordion section — load / error / empty / list / add
// ============================================================

class SetupAccordionSection<T> extends StatefulWidget {
  final IconData icon;
  final String title;
  final String emptyText;
  final String addLabel;
  final Future<List<T>> Function() loader;
  final Widget Function(T item) tileBuilder;
  final Future<bool?> Function(BuildContext context)? onAdd;
  final void Function(List<T> rows)? onLoaded;

  /// Non-null: "add" button ki jagah ye hint dikhta hai (e.g. staff jab tak
  /// campus approve nahi hota).
  final String? disabledHint;

  const SetupAccordionSection({
    super.key,
    required this.icon,
    required this.title,
    required this.emptyText,
    required this.addLabel,
    required this.loader,
    required this.tileBuilder,
    this.onAdd,
    this.onLoaded,
    this.disabledHint,
  });

  @override
  State<SetupAccordionSection<T>> createState() => _SetupAccordionSectionState<T>();
}

class _SetupAccordionSectionState<T> extends State<SetupAccordionSection<T>> {
  List<T>? _items;
  bool _loading = false;
  String? _error;
  bool _expanded = false;

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final rows = await widget.loader();
      if (!mounted) return;
      setState(() {
        _items = rows;
        _loading = false;
      });
      widget.onLoaded?.call(rows);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _add() async {
    final onAdd = widget.onAdd;
    if (onAdd == null) return;
    final created = await onAdd(context);
    if (created == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: LsCard(
        padding: EdgeInsets.zero,
        child: Theme(
          data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
          child: ExpansionTile(
            leading: Icon(widget.icon, color: cs.primary),
            title: Text(widget.title, style: LsType.head(context, size: 14)),
            trailing: _items != null
                ? LsStatusChip(label: '${_items!.length}', color: cs.secondary)
                : const Icon(Icons.expand_more_rounded),
            initiallyExpanded: _expanded,
            onExpansionChanged: (v) {
              setState(() => _expanded = v);
              if (v && _items == null && !_loading) _load();
            },
            childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
            children: [
              if (_loading)
                const Padding(
                  padding: EdgeInsets.only(bottom: 8),
                  child: Column(children: [
                    LsSkeletonBox(height: 48),
                    SizedBox(height: 8),
                    LsSkeletonBox(height: 48),
                  ]),
                )
              else if (_error != null)
                ErrorStateWidget(
                  compact: true,
                  title: l10n.setupLoadFailed,
                  subtitle: _error,
                  retryLabel: l10n.retry,
                  onRetry: _load,
                )
              else if ((_items ?? const []).isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 6),
                  child: Text(widget.emptyText,
                      style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
                )
              else
                ...(_items!.map((item) => Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: widget.tileBuilder(item),
                    ))),
              const SizedBox(height: 4),
              if (widget.disabledHint != null)
                Text(widget.disabledHint!,
                    style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant, height: 1.4))
              else if (widget.onAdd != null)
                LsOutlineButton(
                  label: widget.addLabel,
                  icon: Icons.add_rounded,
                  onPressed: _add,
                ),
            ],
          ),
        ),
      ),
    );
  }
}

// ============================================================
// Row widgets
// ============================================================

class _NameTile extends StatelessWidget {
  final IconData icon;
  final String name;
  final String? subtitle;
  const _NameTile({required this.icon, required this.name, this.subtitle});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(children: [
      Icon(icon, size: 17, color: cs.onSurfaceVariant),
      const SizedBox(width: 10),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(name, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
          if (subtitle != null && subtitle!.isNotEmpty)
            Text(subtitle!, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ]),
      ),
    ]);
  }
}

class _SessionTile extends StatefulWidget {
  final AcademicSession session;
  final VoidCallback onChanged;
  const _SessionTile({required this.session, required this.onChanged});

  @override
  State<_SessionTile> createState() => _SessionTileState();
}

class _SessionTileState extends State<_SessionTile> {
  bool _setting = false;

  Future<void> _setCurrent() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _setting = true);
    try {
      final updated = await CampusService.setCurrentSession(widget.session.id);
      if (!mounted) return;
      lsSnack(context, l10n.sessionSetCurrentSuccess(updated.name));
      widget.onChanged();
    } on CampusApiException catch (e) {
      if (mounted) lsSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _setting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final s = widget.session;
    return Row(children: [
      Icon(Icons.event_note_rounded, size: 17, color: cs.onSurfaceVariant),
      const SizedBox(width: 10),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(s.name, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
            if (s.isCurrent) ...[
              const SizedBox(width: 6),
              LsStatusChip(label: l10n.sessionCurrentBadge, color: cs.primary, solid: true),
            ],
          ]),
          if (s.startDate != null && s.endDate != null)
            Text('${_fmt(s.startDate!)} — ${_fmt(s.endDate!)}',
                style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ]),
      ),
      if (!s.isCurrent)
        _setting
            ? const SizedBox(
                width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
            : TextButton(onPressed: _setCurrent, child: Text(l10n.sessionSetCurrentLabel)),
    ]);
  }

  static String _fmt(DateTime d) =>
      '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')}/${d.year}';
}

class _ClassTile extends StatelessWidget {
  final SchoolClass schoolClass;
  final String? departmentName;
  final String? sessionName;
  final VoidCallback onTap;
  const _ClassTile({
    required this.schoolClass,
    required this.onTap,
    this.departmentName,
    this.sessionName,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final bits = [if (sessionName != null) sessionName!, if (departmentName != null) departmentName!];
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(10),
      child: Row(children: [
        Icon(Icons.class_outlined, size: 17, color: cs.onSurfaceVariant),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(schoolClass.name, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
            if (bits.isNotEmpty)
              Text(bits.join(' · '), style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
          ]),
        ),
        TextButton.icon(
          onPressed: onTap,
          icon: const Icon(Icons.chevron_right_rounded, size: 16),
          label: Text(l10n.classSectionsCta, style: const TextStyle(fontSize: 12)),
        ),
      ]),
    );
  }
}

class _FeeStructureTile extends StatefulWidget {
  final FeeStructure structure;
  const _FeeStructureTile({required this.structure});

  @override
  State<_FeeStructureTile> createState() => _FeeStructureTileState();
}

class _FeeStructureTileState extends State<_FeeStructureTile> {
  bool _generating = false;

  Future<void> _generate() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _generating = true);
    try {
      final result = await CampusService.generateInvoices(widget.structure.id);
      if (mounted) {
        lsSnack(context, l10n.feeInvoicesGenerated(result['invoices_created'] ?? 0, result['already_existed'] ?? 0));
      }
    } on CampusApiException catch (e) {
      if (mounted) lsSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _generating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Row(children: [
      Icon(Icons.toll_outlined, size: 17, color: cs.onSurfaceVariant),
      const SizedBox(width: 10),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(widget.structure.title, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
          Text('₹${widget.structure.amount.toStringAsFixed(0)}',
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ]),
      ),
      _generating
          ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
          : TextButton(onPressed: _generate, child: Text(l10n.feeGenerateInvoices)),
    ]);
  }
}

class _ExamTermTile extends StatelessWidget {
  final ExamTerm term;
  const _ExamTermTile({required this.term});

  static String _fmt(DateTime d) =>
      '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')}/${d.year}';

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(children: [
      Icon(Icons.fact_check_outlined, size: 17, color: cs.onSurfaceVariant),
      const SizedBox(width: 10),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(term.name, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
          if (term.startDate != null && term.endDate != null)
            Text('${_fmt(term.startDate!)} — ${_fmt(term.endDate!)}',
                style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ]),
      ),
    ]);
  }
}

class _StaffTile extends StatelessWidget {
  final StaffProfile staff;
  const _StaffTile({required this.staff});

  String _roleLabel(AppLocalizations l10n) => switch (staff.role) {
        CampusRole.admin => l10n.roleAdmin,
        CampusRole.principalHod => l10n.rolePrincipalHod,
        CampusRole.classTeacher => l10n.roleClassTeacher,
        CampusRole.subjectTeacher => l10n.roleSubjectTeacher,
        CampusRole.nonTeaching => l10n.roleNonTeaching,
        CampusRole.student => l10n.roleStudent,
        CampusRole.parent => l10n.roleParent,
        CampusRole.none => l10n.roleNone,
      };

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Row(children: [
      CircleAvatar(
        radius: 15,
        backgroundColor: cs.primaryContainer,
        child: Text(staff.user?.initials ?? '?',
            style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: cs.onPrimaryContainer)),
      ),
      const SizedBox(width: 10),
      Expanded(
        child: Text(staff.user?.displayName ?? staff.userId,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
      ),
      LsStatusChip(
        label: _roleLabel(l10n),
        color: staff.role.isManagement ? cs.primary : cs.secondary,
      ),
      if (!staff.isActive) ...[
        const SizedBox(width: 6),
        Icon(Icons.pause_circle_outline_rounded, size: 15, color: cs.outline),
      ],
    ]);
  }
}

// ============================================================
// Add sheets
// ============================================================

/// Shared shell — drag handle + title + scroll, jaisa `NoticesScreen`'s
/// `_ComposeSheet` me hai.
class SetupAddSheetShell extends StatelessWidget {
  final String title;
  final Widget child;
  const SetupAddSheetShell({required this.title, required this.child});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
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
            decoration:
                BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
          ),
          const SizedBox(height: 16),
          Align(alignment: Alignment.centerLeft, child: Text(title, style: LsType.head(context, size: 16))),
          const SizedBox(height: 16),
          child,
        ]),
      ),
    );
  }
}

class _AddSessionSheet extends StatefulWidget {
  final String campusId;
  const _AddSessionSheet({required this.campusId});

  @override
  State<_AddSessionSheet> createState() => _AddSessionSheetState();
}

class _AddSessionSheetState extends State<_AddSessionSheet> {
  final _name = TextEditingController();
  DateTime? _start;
  DateTime? _end;
  bool _current = false;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _pickDate({required bool isStart}) async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: (isStart ? _start : _end) ?? now,
      firstDate: DateTime(now.year - 5),
      lastDate: DateTime(now.year + 5),
    );
    if (picked != null) {
      setState(() => isStart ? _start = picked : _end = picked);
    }
  }

  Future<void> _submit() async {
    final l10n = AppLocalizations.of(context)!;
    if (_name.text.trim().isEmpty || _start == null || _end == null) {
      setState(() => _error = l10n.sessionDatesRequiredError);
      return;
    }
    if (!_end!.isAfter(_start!)) {
      setState(() => _error = l10n.sessionDateOrderError);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.createSession(
        campusId: widget.campusId,
        name: _name.text.trim(),
        startDate: _start!,
        endDate: _end!,
        isCurrent: _current,
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
      title: l10n.setupSessionsAdd,
      child: Column(children: [
        TextField(
          controller: _name,
          enabled: !_saving,
          decoration: InputDecoration(
            labelText: l10n.sessionNameLabel,
            hintText: l10n.sessionNameHint,
            border: const OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(
            child: OutlinedButton(
              onPressed: _saving ? null : () => _pickDate(isStart: true),
              child: Text(_start == null ? l10n.sessionStartDateLabel : _SessionTileState._fmt(_start!)),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: OutlinedButton(
              onPressed: _saving ? null : () => _pickDate(isStart: false),
              child: Text(_end == null ? l10n.sessionEndDateLabel : _SessionTileState._fmt(_end!)),
            ),
          ),
        ]),
        SwitchListTile(
          value: _current,
          onChanged: _saving ? null : (v) => setState(() => _current = v),
          contentPadding: EdgeInsets.zero,
          title: Text(l10n.sessionSetCurrentLabel, style: const TextStyle(fontSize: 13.5)),
        ),
        if (_error != null) ...[
          Align(
            alignment: Alignment.centerLeft,
            child: Text(_error!, style: TextStyle(color: cs.error, fontSize: 12.5)),
          ),
          const SizedBox(height: 6),
        ],
        const SizedBox(height: 8),
        LsPrimaryButton(label: l10n.save, loading: _saving, onPressed: _saving ? null : _submit),
      ]),
    );
  }
}

class _AddDepartmentSheet extends StatefulWidget {
  final String campusId;
  const _AddDepartmentSheet({required this.campusId});

  @override
  State<_AddDepartmentSheet> createState() => _AddDepartmentSheetState();
}

class _AddDepartmentSheetState extends State<_AddDepartmentSheet> {
  final _name = TextEditingController();
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_name.text.trim().isEmpty) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.createDepartment(campusId: widget.campusId, name: _name.text.trim());
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
      title: l10n.setupDepartmentsAdd,
      child: Column(children: [
        TextField(
          controller: _name,
          enabled: !_saving,
          textCapitalization: TextCapitalization.words,
          decoration:
              InputDecoration(labelText: l10n.departmentNameLabel, border: const OutlineInputBorder()),
          onSubmitted: (_) => _submit(),
        ),
        if (_error != null) ...[
          const SizedBox(height: 6),
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

class _AddClassSheet extends StatefulWidget {
  final String campusId;
  final List<AcademicSession> sessions;
  final List<Department> departments;
  final String? defaultSessionId;

  const _AddClassSheet({
    required this.campusId,
    required this.sessions,
    required this.departments,
    this.defaultSessionId,
  });

  @override
  State<_AddClassSheet> createState() => _AddClassSheetState();
}

class _AddClassSheetState extends State<_AddClassSheet> {
  final _name = TextEditingController();
  String? _sessionId;
  String? _departmentId;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _sessionId = widget.defaultSessionId ?? (widget.sessions.isNotEmpty ? widget.sessions.first.id : null);
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_name.text.trim().isEmpty || _sessionId == null) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.createClass(
        campusId: widget.campusId,
        sessionId: _sessionId!,
        name: _name.text.trim(),
        departmentId: _departmentId,
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
      title: l10n.setupClassesAdd,
      child: Column(children: [
        TextField(
          controller: _name,
          enabled: !_saving,
          textCapitalization: TextCapitalization.words,
          decoration: InputDecoration(
            labelText: l10n.classNameLabel,
            hintText: l10n.classNameHint,
            border: const OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
          value: _sessionId,
          isExpanded: true,
          items: widget.sessions
              .map((s) => DropdownMenuItem(value: s.id, child: Text(s.name)))
              .toList(),
          decoration:
              InputDecoration(labelText: l10n.classSessionLabel, border: const OutlineInputBorder()),
          onChanged: _saving ? null : (v) => setState(() => _sessionId = v),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<String?>(
          value: _departmentId,
          isExpanded: true,
          items: [
            DropdownMenuItem<String?>(value: null, child: Text(l10n.classDepartmentNone)),
            ...widget.departments.map((d) => DropdownMenuItem<String?>(value: d.id, child: Text(d.name))),
          ],
          decoration:
              InputDecoration(labelText: l10n.classDepartmentLabel, border: const OutlineInputBorder()),
          onChanged: _saving ? null : (v) => setState(() => _departmentId = v),
        ),
        if (_error != null) ...[
          const SizedBox(height: 6),
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

class _AddSubjectSheet extends StatefulWidget {
  final String campusId;
  final List<Department> departments;
  const _AddSubjectSheet({required this.campusId, required this.departments});

  @override
  State<_AddSubjectSheet> createState() => _AddSubjectSheetState();
}

class _AddSubjectSheetState extends State<_AddSubjectSheet> {
  final _name = TextEditingController();
  final _code = TextEditingController();
  String? _departmentId;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _code.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_name.text.trim().isEmpty) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.createSubject(
        campusId: widget.campusId,
        name: _name.text.trim(),
        code: _code.text.trim(),
        departmentId: _departmentId,
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
      title: l10n.setupSubjectsAdd,
      child: Column(children: [
        TextField(
          controller: _name,
          enabled: !_saving,
          textCapitalization: TextCapitalization.words,
          decoration: InputDecoration(labelText: l10n.subjectNameLabel, border: const OutlineInputBorder()),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _code,
          enabled: !_saving,
          textCapitalization: TextCapitalization.characters,
          decoration: InputDecoration(labelText: l10n.subjectCodeLabel, border: const OutlineInputBorder()),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<String?>(
          value: _departmentId,
          isExpanded: true,
          items: [
            DropdownMenuItem<String?>(value: null, child: Text(l10n.classDepartmentNone)),
            ...widget.departments.map((d) => DropdownMenuItem<String?>(value: d.id, child: Text(d.name))),
          ],
          decoration:
              InputDecoration(labelText: l10n.subjectDepartmentLabel, border: const OutlineInputBorder()),
          onChanged: _saving ? null : (v) => setState(() => _departmentId = v),
        ),
        if (_error != null) ...[
          const SizedBox(height: 6),
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

class _AddRoomSheet extends StatefulWidget {
  final String campusId;
  const _AddRoomSheet({required this.campusId});

  @override
  State<_AddRoomSheet> createState() => _AddRoomSheetState();
}

class _AddRoomSheetState extends State<_AddRoomSheet> {
  final _name = TextEditingController();
  bool _virtual = false;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_name.text.trim().isEmpty) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.createRoom(
        campusId: widget.campusId,
        name: _name.text.trim(),
        isVirtual: _virtual,
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
      title: l10n.setupRoomsAdd,
      child: Column(children: [
        TextField(
          controller: _name,
          enabled: !_saving,
          textCapitalization: TextCapitalization.words,
          decoration: InputDecoration(labelText: l10n.roomNameLabel, border: const OutlineInputBorder()),
          onSubmitted: (_) => _submit(),
        ),
        SwitchListTile(
          value: _virtual,
          onChanged: _saving ? null : (v) => setState(() => _virtual = v),
          contentPadding: EdgeInsets.zero,
          title: Text(l10n.roomVirtualLabel, style: const TextStyle(fontSize: 13.5)),
          subtitle: Text(l10n.roomVirtualHint,
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ),
        if (_error != null) ...[
          Align(
            alignment: Alignment.centerLeft,
            child: Text(_error!, style: TextStyle(color: cs.error, fontSize: 12.5)),
          ),
          const SizedBox(height: 6),
        ],
        LsPrimaryButton(label: l10n.save, loading: _saving, onPressed: _saving ? null : _submit),
      ]),
    );
  }
}

class _AddStaffSheet extends StatefulWidget {
  final String campusId;
  const _AddStaffSheet({required this.campusId});

  @override
  State<_AddStaffSheet> createState() => _AddStaffSheetState();
}

class _AddStaffSheetState extends State<_AddStaffSheet> {
  final _userId = TextEditingController();
  String _role = CampusRole.subjectTeacher.value;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _userId.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_userId.text.trim().isEmpty) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.createStaff(
        campusId: widget.campusId,
        userId: _userId.text.trim(),
        role: _role,
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
    final roleOptions = <CampusRole>[
      CampusRole.admin,
      CampusRole.principalHod,
      CampusRole.classTeacher,
      CampusRole.subjectTeacher,
      CampusRole.nonTeaching,
    ];
    String roleLabel(CampusRole r) => switch (r) {
          CampusRole.admin => l10n.roleAdmin,
          CampusRole.principalHod => l10n.rolePrincipalHod,
          CampusRole.classTeacher => l10n.roleClassTeacher,
          CampusRole.subjectTeacher => l10n.roleSubjectTeacher,
          CampusRole.nonTeaching => l10n.roleNonTeaching,
          _ => l10n.roleNone,
        };

    return SetupAddSheetShell(
      title: l10n.setupStaffAdd,
      child: Column(children: [
        TextField(
          controller: _userId,
          enabled: !_saving,
          decoration: InputDecoration(
            labelText: l10n.staffUserIdLabel,
            hintText: l10n.staffUserIdHint,
            border: const OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
          value: _role,
          isExpanded: true,
          items: roleOptions
              .map((r) => DropdownMenuItem(value: r.value, child: Text(roleLabel(r))))
              .toList(),
          decoration: InputDecoration(labelText: l10n.staffRoleLabel, border: const OutlineInputBorder()),
          onChanged: _saving ? null : (v) => setState(() => _role = v ?? _role),
        ),
        if (_error != null) ...[
          const SizedBox(height: 6),
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

class _AddExamTermSheet extends StatefulWidget {
  final String sessionId;
  const _AddExamTermSheet({required this.sessionId});

  @override
  State<_AddExamTermSheet> createState() => _AddExamTermSheetState();
}

class _AddExamTermSheetState extends State<_AddExamTermSheet> {
  final _name = TextEditingController();
  DateTime? _start;
  DateTime? _end;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _pickDate({required bool isStart}) async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: (isStart ? _start : _end) ?? now,
      firstDate: DateTime(now.year - 5),
      lastDate: DateTime(now.year + 5),
    );
    if (picked != null) setState(() => isStart ? _start = picked : _end = picked);
  }

  Future<void> _submit() async {
    final l10n = AppLocalizations.of(context)!;
    if (_name.text.trim().isEmpty || _start == null || _end == null) {
      setState(() => _error = l10n.sessionDatesRequiredError);
      return;
    }
    if (!_end!.isAfter(_start!)) {
      setState(() => _error = l10n.sessionDateOrderError);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.createExamTerm(
        sessionId: widget.sessionId,
        name: _name.text.trim(),
        startDate: _start!,
        endDate: _end!,
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
      title: l10n.setupExamTermsAdd,
      child: Column(children: [
        TextField(
          controller: _name,
          enabled: !_saving,
          decoration: InputDecoration(labelText: l10n.examTermNameLabel, border: const OutlineInputBorder()),
        ),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(
            child: OutlinedButton(
              onPressed: _saving ? null : () => _pickDate(isStart: true),
              child: Text(_start == null
                  ? l10n.sessionStartDateLabel
                  : _ExamTermTile._fmt(_start!)),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: OutlinedButton(
              onPressed: _saving ? null : () => _pickDate(isStart: false),
              child: Text(_end == null ? l10n.sessionEndDateLabel : _ExamTermTile._fmt(_end!)),
            ),
          ),
        ]),
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

class _AddFeeStructureSheet extends StatefulWidget {
  final String campusId;
  final String sessionId;
  const _AddFeeStructureSheet({required this.campusId, required this.sessionId});

  @override
  State<_AddFeeStructureSheet> createState() => _AddFeeStructureSheetState();
}

class _AddFeeStructureSheetState extends State<_AddFeeStructureSheet> {
  final _title = TextEditingController();
  final _amount = TextEditingController();
  DateTime? _dueDate;
  List<SchoolClass> _classes = const [];
  bool _loadingClasses = true;
  String? _schoolClassId;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadClasses();
  }

  Future<void> _loadClasses() async {
    try {
      final rows = await CampusService.classes(widget.campusId, sessionId: widget.sessionId);
      if (mounted) setState(() {
        _classes = rows;
        _loadingClasses = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loadingClasses = false);
    }
  }

  @override
  void dispose() {
    _title.dispose();
    _amount.dispose();
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
    final l10n = AppLocalizations.of(context)!;
    final amount = double.tryParse(_amount.text.trim());
    if (_title.text.trim().isEmpty || amount == null || _dueDate == null) {
      setState(() => _error = l10n.resultMarksInvalid);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.createFeeStructure(
        campusId: widget.campusId,
        sessionId: widget.sessionId,
        title: _title.text.trim(),
        amount: amount,
        dueDate: _dueDate!,
        schoolClassId: _schoolClassId,
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
      title: l10n.setupFeeStructuresAdd,
      child: Column(children: [
        TextField(
          controller: _title,
          enabled: !_saving,
          decoration: InputDecoration(labelText: l10n.feeTitleLabel, border: const OutlineInputBorder()),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _amount,
          enabled: !_saving,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: InputDecoration(labelText: l10n.feeAmountLabel, border: const OutlineInputBorder()),
        ),
        const SizedBox(height: 12),
        OutlinedButton.icon(
          onPressed: _saving ? null : _pickDueDate,
          icon: const Icon(Icons.event_outlined, size: 16),
          label: Text(_dueDate == null ? l10n.sessionEndDateLabel : _fmt(_dueDate!)),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<String?>(
          value: _schoolClassId,
          isExpanded: true,
          items: [
            DropdownMenuItem<String?>(value: null, child: Text(l10n.feeCampusWide)),
            ..._classes.map((c) => DropdownMenuItem<String?>(value: c.id, child: Text(c.name))),
          ],
          decoration: InputDecoration(labelText: l10n.classNameLabel, border: const OutlineInputBorder()),
          onChanged: (_saving || _loadingClasses) ? null : (v) => setState(() => _schoolClassId = v),
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
