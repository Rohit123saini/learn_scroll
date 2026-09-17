import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';
import 'attendance_mark_screen.dart';
import 'attendance_summary_screen.dart';
import 'notices_screen.dart';
import 'section_students_screen.dart';
import 'timetable_screen.dart';

// ============================================================
// CAMPUS — HUB SCREEN
//
// Bottom nav ke "Campus" tab ka destination. Pehle ye
// `featureComingSoon` snackbar dikhata tha (home.dart:1928).
//
// Ek hi screen teen bilkul alag logon ko serve karti hai:
//   • Management (admin / principal) — poore campus ka view
//   • Teacher (class / subject) — sirf apni sections
//   • Student — apni ek section, apna attendance
//
// Teen alag screens isliye nahi banayin ki tab "role badla to kaunsi
// screen" ka sawaal har navigation pe aata. Ek screen, aur `CampusAccess`
// decide karta hai ki kaunse cards banenge. Role add hone pe sirf ek
// builder method add hoti hai.
// ============================================================

class CampusScreen extends StatefulWidget {
  const CampusScreen({super.key});

  @override
  State<CampusScreen> createState() => _CampusScreenState();
}

class _CampusScreenState extends State<CampusScreen> {
  bool _loading = true;
  String? _error;

  List<Campus> _campuses = const [];
  Campus? _selected;
  CampusAccess? _access;

  /// Section id -> label ("Class 10 — A"). Hub pe har jagah section ka
  /// naam chahiye hota hai par `Section.name` sirf "A" hai — class ka naam
  /// uske parent me hai. Isliye ek baar resolve karke yahan rakh liya.
  Map<String, String> _sectionLabels = const {};
  Map<String, Section> _sectionsById = const {};
  Map<String, Subject> _subjectsById = const {};

  @override
  void initState() {
    super.initState();
    _bootstrap();
  }

  Future<void> _bootstrap() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final campuses = await CampusService.myCampuses();
      if (!mounted) return;

      if (campuses.isEmpty) {
        setState(() {
          _campuses = const [];
          _selected = null;
          _access = null;
          _loading = false;
        });
        return;
      }

      // Pehla approved campus default. Agar koi approved nahi hai to
      // pehla hi le lo — pending banner usi screen pe dikh jaayega.
      final approved = campuses.where((c) => c.isApproved).toList();
      final pick = approved.isNotEmpty ? approved.first : campuses.first;

      setState(() => _campuses = campuses);
      await _selectCampus(pick);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _selectCampus(Campus campus) async {
    setState(() {
      _selected = campus;
      _loading = true;
      _error = null;
    });
    try {
      final access = await CampusService.loadAccess(campus);
      if (!mounted) return;

      // Sections + subjects ek saath — labels banane ke liye dono chahiye.
      final classes = await CampusService.classes(campus.id,
          sessionId: access.currentSession?.id);
      final classNames = {for (final c in classes) c.id: c.name};

      final allSections = <Section>[];
      for (final c in classes) {
        allSections.addAll(await CampusService.sections(schoolClassId: c.id));
      }

      final subjects = await CampusService.subjects(campus.id);

      if (!mounted) return;
      setState(() {
        _access = access;
        _sectionsById = {for (final s in allSections) s.id: s};
        _sectionLabels = {
          for (final s in allSections)
            s.id: '${classNames[s.schoolClassId] ?? 'Class'} — ${s.name}',
        };
        _subjectsById = {for (final s in subjects) s.id: s};
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

  String _labelFor(String sectionId) => _sectionLabels[sectionId] ?? 'Section';

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.campusTab, style: LsType.head(context, size: 16)),
        actions: [
          if (_campuses.length > 1)
            PopupMenuButton<Campus>(
              tooltip: l10n.campusSwitchTooltip,
              icon: const Icon(Icons.swap_horiz_rounded),
              onSelected: _selectCampus,
              itemBuilder: (_) => _campuses
                  .map((c) => PopupMenuItem<Campus>(
                        value: c,
                        child: Row(children: [
                          Expanded(child: Text(c.name, overflow: TextOverflow.ellipsis)),
                          if (c.id == _selected?.id)
                            Icon(Icons.check, size: 16, color: cs.primary),
                        ]),
                      ))
                  .toList(),
            ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _bootstrap,
        child: _buildBody(cs, l10n),
      ),
    );
  }

  Widget _buildBody(ColorScheme cs, AppLocalizations l10n) {
    if (_loading) return const _CampusSkeleton();

    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(
          title: l10n.campusLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _bootstrap,
        ),
      ]);
    }

    if (_campuses.isEmpty) {
      return ListView(children: [
        const SizedBox(height: 40),
        EmptyStateWidget(
          icon: Icons.school_outlined,
          title: l10n.campusNoneTitle,
          subtitle: l10n.campusNoneSubtitle,
        ),
      ]);
    }

    final access = _access;
    if (access == null) return const _CampusSkeleton();

    return ListView(
      padding: const EdgeInsets.only(bottom: 28),
      children: [
        _CampusHeader(campus: access.campus, access: access),

        // Campus abhi approve nahi hua — ye sabse upar dikhna chahiye,
        // warna creator ghante bhar samajhta rahega ki kuch kaam kyun
        // nahi kar raha.
        if (!access.campus.isApproved) _PendingBanner(campus: access.campus),

        if (access.isStudent) ..._studentCards(access, l10n),
        if (access.role.canTeach) ..._teacherCards(access, l10n),
        if (access.isManagement) ..._managementCards(access, l10n),

        LsSectionHead(title: l10n.campusNoticesTitle, actionLabel: l10n.viewAll, onAction: _openNotices),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14),
          child: _NoticePreview(campusId: access.campus.id, onOpen: _openNotices),
        ),
      ],
    );
  }

  // ---------------- student ----------------

  List<Widget> _studentCards(CampusAccess access, AppLocalizations l10n) {
    return [
      LsSectionHead(title: l10n.campusMyClassTitle),
      for (final enrollment in access.myEnrollments)
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
          child: LsCard(
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => AttendanceSummaryScreen(
                  enrollment: enrollment,
                  sectionLabel: _labelFor(enrollment.sectionId),
                  subjects: _subjectsById.values.toList(),
                  thresholdPercent: access.campus.attendanceAlertThresholdPercent,
                ),
              ),
            ),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(
                  child: Text(_labelFor(enrollment.sectionId),
                      style: LsType.head(context, size: 15)),
                ),
                if (enrollment.rollNumber.isNotEmpty)
                  LsStatusChip(
                    label: '#${enrollment.rollNumber}',
                    color: Theme.of(context).colorScheme.primary,
                  ),
              ]),
              const SizedBox(height: 10),
              LsMetaRow(
                icon: Icons.fact_check_outlined,
                label: l10n.campusAttendanceLabel,
                value: l10n.campusTapToView,
              ),
            ]),
          ),
        ),
      Padding(
        padding: const EdgeInsets.fromLTRB(14, 0, 14, 12),
        child: LsOutlineButton(
          label: l10n.campusTimetableTitle,
          icon: Icons.calendar_view_week_rounded,
          onPressed: () => _openTimetable(access.myEnrollments.first.sectionId),
        ),
      ),
    ];
  }

  // ---------------- teacher ----------------

  List<Widget> _teacherCards(CampusAccess access, AppLocalizations l10n) {
    // Class teacher wali sections pehle — wahan poora access hai.
    // Subject-only sections uske baad.
    final classTeacherOf = access.classTeacherSectionIds.toList();
    final subjectOnly = access.subjectTeacherKeys
        .map((k) => k.split('::'))
        .where((p) => !access.classTeacherSectionIds.contains(p.first))
        .toList();

    return [
      LsSectionHead(title: l10n.campusMySectionsTitle),
      for (final sectionId in classTeacherOf)
        _SectionCard(
          label: _labelFor(sectionId),
          roleLabel: l10n.roleClassTeacher,
          roleColor: Theme.of(context).colorScheme.primary,
          onMarkAttendance: () => _openMarkAttendance(access, sectionId, null),
          onRoster: () => _openRoster(sectionId),
          onTimetable: () => _openTimetable(sectionId),
          markLabel: l10n.campusMarkAttendance,
          rosterLabel: l10n.campusStudents,
        ),
      for (final pair in subjectOnly)
        _SectionCard(
          label: '${_labelFor(pair.first)} · ${_subjectsById[pair.last]?.name ?? ''}',
          roleLabel: l10n.roleSubjectTeacher,
          roleColor: Theme.of(context).colorScheme.secondary,
          onMarkAttendance: () => _openMarkAttendance(access, pair.first, pair.last),
          onRoster: () => _openRoster(pair.first),
          onTimetable: () => _openTimetable(pair.first),
          markLabel: l10n.campusMarkAttendance,
          rosterLabel: l10n.campusStudents,
        ),
      if (classTeacherOf.isEmpty && subjectOnly.isEmpty)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14),
          child: LsCard(
            tinted: true,
            child: Text(l10n.campusNoSectionsAssigned,
                style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant)),
          ),
        ),
    ];
  }

  // ---------------- management ----------------

  List<Widget> _managementCards(CampusAccess access, AppLocalizations l10n) {
    final allSections = _sectionsById.keys.toList();
    return [
      LsSectionHead(title: l10n.campusManagementTitle),
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14),
        child: LsCard(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            LsMetaRow(
              icon: Icons.groups_2_outlined,
              label: l10n.campusSectionsCount,
              value: '${allSections.length}',
            ),
            const SizedBox(height: 8),
            LsMetaRow(
              icon: Icons.menu_book_outlined,
              label: l10n.campusSubjectsCount,
              value: '${_subjectsById.length}',
            ),
            const SizedBox(height: 12),
            LsPrimaryButton(
              label: l10n.campusPostNotice,
              icon: Icons.campaign_outlined,
              onPressed: _openNotices,
            ),
          ]),
        ),
      ),
      const SizedBox(height: 4),
      // Management ko har section dikhta hai — yahi `canManageSection()`
      // ka campus-wide scope practice me kaisa lagta hai.
      LsSectionHead(title: l10n.campusAllSectionsTitle),
      for (final sectionId in allSections)
        _SectionCard(
          label: _labelFor(sectionId),
          roleLabel: l10n.roleManagement,
          roleColor: Theme.of(context).colorScheme.tertiary,
          onMarkAttendance: () => _openMarkAttendance(access, sectionId, null),
          onRoster: () => _openRoster(sectionId),
          onTimetable: () => _openTimetable(sectionId),
          markLabel: l10n.campusMarkAttendance,
          rosterLabel: l10n.campusStudents,
        ),
    ];
  }

  // ---------------- navigation ----------------

  void _openMarkAttendance(CampusAccess access, String sectionId, String? subjectId) {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => AttendanceMarkScreen(
          sectionId: sectionId,
          sectionLabel: _labelFor(sectionId),
          sessionId: access.currentSession?.id,
          fixedSubjectId: subjectId,
          subjects: _subjectsById.values.toList(),
          access: access,
        ),
      ),
    );
  }

  void _openRoster(String sectionId) => Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => SectionStudentsScreen(
            sectionId: sectionId,
            sectionLabel: _labelFor(sectionId),
          ),
        ),
      );

  void _openTimetable(String sectionId) => Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => TimetableScreen(
            sectionId: sectionId,
            sectionLabel: _labelFor(sectionId),
            campusId: _selected!.id,
            subjects: _subjectsById,
          ),
        ),
      );

  void _openNotices() {
    final access = _access;
    if (access == null) return;
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => NoticesScreen(
          access: access,
          sectionLabels: _sectionLabels,
        ),
      ),
    );
  }
}

// ============================================================
// pieces
// ============================================================

class _CampusHeader extends StatelessWidget {
  final Campus campus;
  final CampusAccess access;
  const _CampusHeader({required this.campus, required this.access});

  String _roleLabel(AppLocalizations l10n) => switch (access.role) {
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

    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
      child: LsCard(
        tinted: true,
        child: Row(children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              gradient: LinearGradient(colors: [cs.primary, cs.secondary]),
              borderRadius: BorderRadius.circular(12),
            ),
            child: const Icon(Icons.school_rounded, color: Colors.white, size: 22),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(campus.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: LsType.head(context, size: 15.5)),
              const SizedBox(height: 3),
              Text(_roleLabel(l10n),
                  style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
            ]),
          ),
          if (access.currentSession != null)
            LsStatusChip(label: access.currentSession!.name, color: cs.primary),
        ]),
      ),
    );
  }
}

class _PendingBanner extends StatelessWidget {
  final Campus campus;
  const _PendingBanner({required this.campus});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final rejected = campus.isRejected;

    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 0, 14, 12),
      child: LsCard(
        borderColor: rejected ? cs.error : Colors.orange,
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(rejected ? Icons.block_rounded : Icons.hourglass_top_rounded,
              size: 18, color: rejected ? cs.error : Colors.orange),
          const SizedBox(width: 10),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(rejected ? l10n.campusRejectedTitle : l10n.campusPendingTitle,
                  style: LsType.head(context, size: 13.5)),
              const SizedBox(height: 3),
              Text(rejected ? l10n.campusRejectedBody : l10n.campusPendingBody,
                  style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant, height: 1.35)),
            ]),
          ),
        ]),
      ),
    );
  }
}

class _SectionCard extends StatelessWidget {
  final String label;
  final String roleLabel;
  final Color roleColor;
  final String markLabel;
  final String rosterLabel;
  final VoidCallback onMarkAttendance;
  final VoidCallback onRoster;
  final VoidCallback onTimetable;

  const _SectionCard({
    required this.label,
    required this.roleLabel,
    required this.roleColor,
    required this.markLabel,
    required this.rosterLabel,
    required this.onMarkAttendance,
    required this.onRoster,
    required this.onTimetable,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
      child: LsCard(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(
              child: Text(label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: LsType.head(context, size: 14.5)),
            ),
            LsStatusChip(label: roleLabel, color: roleColor),
          ]),
          const SizedBox(height: 12),
          Row(children: [
            Expanded(
              child: LsPrimaryButton(
                label: markLabel,
                icon: Icons.how_to_reg_rounded,
                onPressed: onMarkAttendance,
              ),
            ),
            const SizedBox(width: 8),
            IconButton.filledTonal(
              tooltip: rosterLabel,
              onPressed: onRoster,
              icon: const Icon(Icons.groups_2_outlined, size: 20),
            ),
            IconButton.filledTonal(
              onPressed: onTimetable,
              icon: const Icon(Icons.calendar_view_week_rounded, size: 20),
            ),
          ]),
        ]),
      ),
    );
  }
}

/// Hub pe sirf do latest notices. Poori list alag screen pe.
class _NoticePreview extends StatefulWidget {
  final String campusId;
  final VoidCallback onOpen;
  const _NoticePreview({required this.campusId, required this.onOpen});

  @override
  State<_NoticePreview> createState() => _NoticePreviewState();
}

class _NoticePreviewState extends State<_NoticePreview> {
  List<Notice>? _notices;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final rows = await CampusService.notices(widget.campusId);
      if (!mounted) return;
      // Pinned pehle, phir naye se purane.
      rows.sort((a, b) {
        if (a.isPinned != b.isPinned) return a.isPinned ? -1 : 1;
        return (b.createdAt ?? DateTime(0)).compareTo(a.createdAt ?? DateTime(0));
      });
      setState(() => _notices = rows.take(2).toList());
    } catch (_) {
      if (mounted) setState(() => _failed = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    if (_failed) {
      return ErrorStateWidget(
        compact: true,
        title: l10n.campusNoticesLoadFailed,
        retryLabel: l10n.retry,
        onRetry: () {
          setState(() => _failed = false);
          _load();
        },
      );
    }
    final notices = _notices;
    if (notices == null) {
      return const Column(children: [
        LsSkeletonBox(height: 62),
        SizedBox(height: 8),
        LsSkeletonBox(height: 62),
      ]);
    }
    if (notices.isEmpty) {
      return LsCard(
        tinted: true,
        child: Text(l10n.campusNoNotices,
            style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant)),
      );
    }
    return Column(
      children: notices
          .map((n) => Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: LsCard(
                  onTap: widget.onOpen,
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Row(children: [
                      if (n.isPinned) ...[
                        const Icon(Icons.push_pin_rounded, size: 14),
                        const SizedBox(width: 6),
                      ],
                      Expanded(
                        child: Text(n.title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: LsType.head(context, size: 13.5)),
                      ),
                    ]),
                    const SizedBox(height: 4),
                    Text(n.body,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                            fontSize: 12.5,
                            height: 1.35,
                            color: Theme.of(context).colorScheme.onSurfaceVariant)),
                  ]),
                ),
              ))
          .toList(),
    );
  }
}

class _CampusSkeleton extends StatelessWidget {
  const _CampusSkeleton();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(14),
      children: const [
        LsSkeletonBox(height: 72),
        SizedBox(height: 16),
        LsSkeletonBox(height: 18, width: 120),
        SizedBox(height: 12),
        LsSkeletonBox(height: 104),
        SizedBox(height: 10),
        LsSkeletonBox(height: 104),
      ],
    );
  }
}
