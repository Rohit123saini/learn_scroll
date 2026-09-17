import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// ATTENDANCE — MARK SCREEN
//
// Teacher ka rozana ka kaam. Isliye teen cheezon pe design tika hai:
//
//  1. DEFAULT SAB PRESENT. Aam din pe 2-3 bachche absent hote hain, 58
//     present. Sab ko khaali chhod ke ek-ek tap karwana 58 extra taps hai.
//     Sab present se shuru, teacher sirf absent wale tap karta hai.
//
//  2. SAVE HONE KE BAAD SCREEN BAND NAHI HOTI JAB TAK CONFIRM NA HO.
//     Backend pe bulk endpoint nahi hai (`AttendanceViewSet` plain
//     ModelViewSet hai) — 60 students = 60 POSTs. Beech me kuch fail ho
//     sakta hai. Isliye failures wapas dikhte hain aur sirf unko retry
//     kiya ja sakta hai.
//
//  3. DOBARA MARK KARNA. Model pe `unique_attendance_per_day_subject`
//     constraint hai, to usi din ka dobara POST 400 dega. Screen pehle
//     us din ka existing attendance load karti hai aur agar mila to
//     "already marked" mode me jaati hai.
// ============================================================

class AttendanceMarkScreen extends StatefulWidget {
  final String sectionId;
  final String sectionLabel;
  final String? sessionId;

  /// Subject teacher ke case me subject pehle se fix hota hai — wo doosre
  /// subject ka attendance mark kar hi nahi sakta. Class teacher ke case
  /// me null aata hai aur wo choose karta hai.
  final String? fixedSubjectId;

  final List<Subject> subjects;
  final CampusAccess access;

  const AttendanceMarkScreen({
    super.key,
    required this.sectionId,
    required this.sectionLabel,
    required this.subjects,
    required this.access,
    this.sessionId,
    this.fixedSubjectId,
  });

  @override
  State<AttendanceMarkScreen> createState() => _AttendanceMarkScreenState();
}

class _AttendanceMarkScreenState extends State<AttendanceMarkScreen> {
  bool _loading = true;
  String? _error;

  List<StudentEnrollment> _roster = const [];

  /// enrollmentId -> status. Default sab `present` (upar wali baat #1).
  final Map<String, String> _status = {};

  DateTime _date = DateTime.now();
  String? _subjectId;

  bool _saving = false;
  int _savedCount = 0;
  List<String> _failedIds = const [];

  /// Us din ka attendance already mark ho chuka hai — sirf padhne ke liye.
  bool _alreadyMarked = false;

  @override
  void initState() {
    super.initState();
    _subjectId = widget.fixedSubjectId;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
      _alreadyMarked = false;
    });
    try {
      final roster = await CampusService.roster(widget.sectionId, sessionId: widget.sessionId);
      if (!mounted) return;

      _status
        ..clear()
        ..addEntries(roster.map((e) => MapEntry(e.id, AttendanceStatus.present)));

      setState(() {
        _roster = roster;
        _loading = false;
      });

      await _checkExisting();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  /// Is date + subject pe pehle se kuch mark hai?
  ///
  /// Pehle student ka check kaafi hai — attendance poore roster ka ek saath
  /// save hota hai, isliye agar ek row hai to poora din mark ho chuka hai.
  /// 60 alag queries karne ka koi fayda nahi.
  Future<void> _checkExisting() async {
    if (_roster.isEmpty) return;
    try {
      final existing = await CampusService.attendance(
        enrollmentId: _roster.first.id,
        subjectId: _subjectId,
        date: _date,
      );
      if (!mounted) return;
      if (existing.isNotEmpty) {
        // Poore roster ka existing status laao taaki teacher dekh sake
        // kya mark hua tha.
        final all = <String, String>{};
        for (final e in _roster) {
          final rows = await CampusService.attendance(
            enrollmentId: e.id,
            subjectId: _subjectId,
            date: _date,
          );
          if (rows.isNotEmpty) all[e.id] = rows.first.status;
        }
        if (!mounted) return;
        setState(() {
          _status
            ..clear()
            ..addAll(all);
          _alreadyMarked = true;
        });
      }
    } catch (_) {
      // Check fail hua to maan lo mark nahi hua — save pe backend waise
      // bhi 400 dega aur wo failure list me dikh jaayega. Yahan silently
      // aage badhna theek hai, kyunki ye sirf ek optimisation hai.
    }
  }

  int get _presentCount =>
      _status.values.where((s) => s == AttendanceStatus.present).length;
  int get _absentCount =>
      _status.values.where((s) => s == AttendanceStatus.absent).length;

  bool get _canMark =>
      widget.access.canMarkAttendance(widget.sectionId, _subjectId);

  Future<void> _pickDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _date,
      // Aage ki date ka attendance mark karna galat hai — class hui hi
      // nahi. Peeche 60 din, taaki bhoola hua din bhara ja sake.
      firstDate: DateTime.now().subtract(const Duration(days: 60)),
      lastDate: DateTime.now(),
    );
    if (picked == null || !mounted) return;
    setState(() => _date = picked);
    await _checkExisting();
  }

  Future<void> _save() async {
    if (_saving) return;
    HapticFeedback.mediumImpact();
    setState(() {
      _saving = true;
      _savedCount = 0;
      _failedIds = const [];
    });

    final failed = await CampusService.markAttendance(
      statusByEnrollmentId: _status,
      date: _date,
      subjectId: _subjectId,
      onProgress: (done, _) {
        if (mounted) setState(() => _savedCount = done);
      },
    );

    if (!mounted) return;
    setState(() {
      _saving = false;
      _failedIds = failed;
    });

    final l10n = AppLocalizations.of(context)!;
    if (failed.isEmpty) {
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(
          content: Text(l10n.attendanceSavedAll(_status.length)),
          behavior: SnackBarBehavior.floating,
        ));
      Navigator.pop(context, true);
    } else {
      // Partial failure — screen band NAHI karo. Teacher ko dikhna chahiye
      // kiska nahi bacha, aur sirf unhe retry karne ka option mile.
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(
          content: Text(l10n.attendanceSavedPartial(_status.length - failed.length, failed.length)),
          backgroundColor: Theme.of(context).colorScheme.error,
          behavior: SnackBarBehavior.floating,
        ));
    }
  }

  Future<void> _retryFailed() async {
    final retryMap = {
      for (final id in _failedIds) id: _status[id] ?? AttendanceStatus.present,
    };
    setState(() {
      _saving = true;
      _savedCount = 0;
    });
    final failed = await CampusService.markAttendance(
      statusByEnrollmentId: retryMap,
      date: _date,
      subjectId: _subjectId,
      onProgress: (done, _) {
        if (mounted) setState(() => _savedCount = done);
      },
    );
    if (!mounted) return;
    setState(() {
      _saving = false;
      _failedIds = failed;
    });
    if (failed.isEmpty && mounted) Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      appBar: AppBar(
        title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l10n.campusMarkAttendance, style: LsType.head(context, size: 15)),
          Text(widget.sectionLabel,
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ]),
        actions: [
          TextButton.icon(
            onPressed: _saving ? null : _pickDate,
            icon: const Icon(Icons.calendar_today_rounded, size: 16),
            label: Text(_prettyDate(_date)),
          ),
        ],
      ),
      body: _buildBody(cs, l10n),
      bottomNavigationBar: _buildBottomBar(cs, l10n),
    );
  }

  Widget _buildBody(ColorScheme cs, AppLocalizations l10n) {
    if (_loading) {
      return ListView(
        padding: const EdgeInsets.all(14),
        children: const [
          LsSkeletonBox(height: 56),
          SizedBox(height: 12),
          LsSkeletonBox(height: 60),
          SizedBox(height: 8),
          LsSkeletonBox(height: 60),
          SizedBox(height: 8),
          LsSkeletonBox(height: 60),
        ],
      );
    }

    if (_error != null) {
      return ErrorStateWidget(
        title: l10n.campusRosterLoadFailed,
        subtitle: _error,
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    }

    if (_roster.isEmpty) {
      return EmptyStateWidget(
        icon: Icons.groups_2_outlined,
        title: l10n.campusRosterEmptyTitle,
        subtitle: l10n.campusRosterEmptySubtitle,
      );
    }

    // Permission nahi hai — backend waise bhi 403 dega, par usse pehle
    // batana behtar hai. 60 students mark karke 403 khaana bura UX hai.
    if (!_canMark) {
      return EmptyStateWidget(
        icon: Icons.lock_outline_rounded,
        title: l10n.campusNoAttendancePermissionTitle,
        subtitle: l10n.campusNoAttendancePermissionBody,
      );
    }

    return Column(children: [
      if (widget.fixedSubjectId == null) _subjectPicker(cs, l10n),
      if (_alreadyMarked) _alreadyMarkedBanner(cs, l10n),
      if (_failedIds.isNotEmpty) _failureBanner(cs, l10n),
      _summaryStrip(cs, l10n),
      Expanded(
        child: ListView.builder(
          padding: const EdgeInsets.fromLTRB(14, 4, 14, 14),
          itemCount: _roster.length,
          itemBuilder: (_, i) {
            final e = _roster[i];
            return _StudentRow(
              enrollment: e,
              status: _status[e.id] ?? AttendanceStatus.present,
              failed: _failedIds.contains(e.id),
              enabled: !_saving && !_alreadyMarked,
              onChanged: (s) {
                HapticFeedback.selectionClick();
                setState(() => _status[e.id] = s);
              },
            );
          },
        ),
      ),
    ]);
  }

  Widget _subjectPicker(ColorScheme cs, AppLocalizations l10n) {
    // Class teacher subject choose kar sakta hai — ya khaali chhod de,
    // jo "poore din ka attendance" hota hai (backend me subject nullable
    // hai, aur unique constraint me subject shaamil hai).
    final items = <DropdownMenuItem<String?>>[
      DropdownMenuItem(value: null, child: Text(l10n.attendanceWholeDay)),
      ...widget.subjects.map((s) => DropdownMenuItem(value: s.id, child: Text(s.label))),
    ];
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 4),
      child: DropdownButtonFormField<String?>(
        value: _subjectId,
        items: items,
        isExpanded: true,
        decoration: InputDecoration(
          labelText: l10n.attendanceSubjectLabel,
          border: const OutlineInputBorder(),
          contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        ),
        onChanged: _saving
            ? null
            : (v) async {
                setState(() => _subjectId = v);
                await _checkExisting();
              },
      ),
    );
  }

  Widget _alreadyMarkedBanner(ColorScheme cs, AppLocalizations l10n) => Padding(
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 0),
        child: LsCard(
          borderColor: cs.primary,
          child: Row(children: [
            Icon(Icons.check_circle_outline_rounded, size: 18, color: cs.primary),
            const SizedBox(width: 10),
            Expanded(
              child: Text(l10n.attendanceAlreadyMarked,
                  style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant, height: 1.35)),
            ),
          ]),
        ),
      );

  Widget _failureBanner(ColorScheme cs, AppLocalizations l10n) => Padding(
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 0),
        child: LsCard(
          borderColor: cs.error,
          child: Row(children: [
            Icon(Icons.error_outline_rounded, size: 18, color: cs.error),
            const SizedBox(width: 10),
            Expanded(
              child: Text(l10n.attendanceFailedCount(_failedIds.length),
                  style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant, height: 1.35)),
            ),
            TextButton(
              onPressed: _saving ? null : _retryFailed,
              child: Text(l10n.retry),
            ),
          ]),
        ),
      );

  Widget _summaryStrip(ColorScheme cs, AppLocalizations l10n) => Padding(
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 8),
        child: Row(children: [
          Expanded(
            child: LsScoreTile(
              value: '$_presentCount',
              label: l10n.attendancePresent,
              color: Colors.green,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: LsScoreTile(
              value: '$_absentCount',
              label: l10n.attendanceAbsent,
              color: cs.error,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: LsScoreTile(
              value: '${_roster.length}',
              label: l10n.attendanceTotal,
              color: cs.primary,
            ),
          ),
        ]),
      );

  Widget? _buildBottomBar(ColorScheme cs, AppLocalizations l10n) {
    if (_loading || _error != null || _roster.isEmpty || !_canMark || _alreadyMarked) {
      return null;
    }
    return SafeArea(
      minimum: const EdgeInsets.fromLTRB(14, 8, 14, 12),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        if (_saving) ...[
          // Progress dikhana zaroori hai — 60 sequential POSTs me 10-15
          // second lag sakte hain, aur bina progress ke app hang laga hai.
          LsProgressBar(value: _roster.isEmpty ? 0 : _savedCount / _roster.length),
          const SizedBox(height: 8),
          Text(l10n.attendanceSaving(_savedCount, _roster.length),
              style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
          const SizedBox(height: 8),
        ],
        LsPrimaryButton(
          label: l10n.attendanceSaveLabel(_roster.length),
          icon: Icons.check_rounded,
          loading: _saving,
          onPressed: _saving ? null : _save,
        ),
      ]),
    );
  }

  static String _prettyDate(DateTime d) =>
      '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')}';
}

/// Ek student ki row — naam + roll + 4 status buttons.
///
/// Segmented control isliye nahi (jo pehla instinct hota hai): 4 options
/// × 60 rows me segmented control bahut jagah khaata hai aur scroll lamba
/// ho jaata hai. Compact icon toggles se poora roster kam scroll me aata
/// hai, jo teacher ke liye tez hai.
class _StudentRow extends StatelessWidget {
  final StudentEnrollment enrollment;
  final String status;
  final bool failed;
  final bool enabled;
  final ValueChanged<String> onChanged;

  const _StudentRow({
    required this.enrollment,
    required this.status,
    required this.failed,
    required this.enabled,
    required this.onChanged,
  });

  static const _icons = {
    AttendanceStatus.present: Icons.check_rounded,
    AttendanceStatus.absent: Icons.close_rounded,
    AttendanceStatus.late: Icons.schedule_rounded,
    AttendanceStatus.excused: Icons.verified_user_outlined,
  };

  Color _colorFor(String s, ColorScheme cs) => switch (s) {
        AttendanceStatus.present => Colors.green,
        AttendanceStatus.absent => cs.error,
        AttendanceStatus.late => Colors.orange,
        AttendanceStatus.excused => cs.tertiary,
        _ => cs.outline,
      };

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final name = enrollment.student?.displayName ?? enrollment.studentId;

    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: LsCard(
        padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
        borderColor: failed ? cs.error : null,
        child: Row(children: [
          CircleAvatar(
            radius: 16,
            backgroundColor: cs.surfaceContainerHighest,
            child: Text(enrollment.student?.initials ?? '?',
                style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: cs.onSurface)),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: LsType.head(context, size: 13.5)),
              if (enrollment.rollNumber.isNotEmpty)
                Text('#${enrollment.rollNumber}',
                    style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
            ]),
          ),
          for (final s in AttendanceStatus.all)
            _StatusDot(
              icon: _icons[s]!,
              color: _colorFor(s, cs),
              selected: status == s,
              enabled: enabled,
              onTap: () => onChanged(s),
            ),
        ]),
      ),
    );
  }
}

class _StatusDot extends StatelessWidget {
  final IconData icon;
  final Color color;
  final bool selected;
  final bool enabled;
  final VoidCallback onTap;

  const _StatusDot({
    required this.icon,
    required this.color,
    required this.selected,
    required this.enabled,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Semantics(
      selected: selected,
      button: true,
      child: InkWell(
        onTap: enabled ? onTap : null,
        borderRadius: BorderRadius.circular(9),
        child: Container(
          width: 32,
          height: 32,
          margin: const EdgeInsets.only(left: 3),
          decoration: BoxDecoration(
            color: selected ? color.withValues(alpha: .16) : Colors.transparent,
            border: Border.all(color: selected ? color : cs.outlineVariant, width: selected ? 1.4 : 1),
            borderRadius: BorderRadius.circular(9),
          ),
          child: Icon(icon, size: 16, color: selected ? color : cs.outline),
        ),
      ),
    );
  }
}
