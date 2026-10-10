import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../services/campus_service.dart';

// ============================================================
// [T4 §D] SECTION DASHBOARD (class teacher / admin)
//
// Roster + enrolled/capacity + aaj ki hazri + pending subject requests.
// Student ko dusre section me bhejna / hatana yahin se. Section full ho
// to backend 400 `section_full` deta hai — admin ko "phir bhi jodein"
// (override) ka option milta hai, class teacher ko nahi.
// ============================================================

class SectionDashboardScreen extends StatefulWidget {
  final String sectionId;
  final String sectionLabel;
  final bool isAdmin; // admin/principal => capacity override allowed

  const SectionDashboardScreen({
    super.key,
    required this.sectionId,
    required this.sectionLabel,
    this.isAdmin = false,
  });

  @override
  State<SectionDashboardScreen> createState() => _SectionDashboardScreenState();
}

class _SectionDashboardScreenState extends State<SectionDashboardScreen> {
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _d;

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
      final d = await CampusService.sectionDashboard(widget.sectionId);
      if (!mounted) return;
      setState(() {
        _d = d;
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

  void _snack(String m) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));

  Future<void> _remove(Map<String, dynamic> s) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        content: Text(l10n.campusRosterRemoveConfirm),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(MaterialLocalizations.of(ctx).cancelButtonLabel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.campusRosterRemove)),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await CampusService.removeEnrollment(s['enrollment'].toString());
      _load();
    } catch (e) {
      _snack(e.toString());
    }
  }

  Future<void> _transfer(Map<String, dynamic> s) async {
    final l10n = AppLocalizations.of(context)!;
    final campusId = _d?['campus']?.toString();
    if (campusId == null) return;
    List<MapEntry<String, String>> targets;
    try {
      final classes = await CampusService.classes(campusId);
      final sections = await CampusService.sections();
      final names = {for (final c in classes) c.id: c.name};
      targets = [
        for (final sec in sections)
          if (sec.id != widget.sectionId) MapEntry(sec.id, '${names[sec.schoolClassId] ?? ''} ${sec.name}'.trim()),
      ];
    } catch (e) {
      _snack(e.toString());
      return;
    }
    if (!mounted) return;
    final target = await showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => SafeArea(
        child: ListView(shrinkWrap: true, children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(l10n.campusRosterTransfer, style: LsType.head(ctx, size: 14)),
          ),
          for (final t in targets)
            ListTile(title: Text(t.value), onTap: () => Navigator.pop(ctx, t.key)),
        ]),
      ),
    );
    if (target == null) return;
    await _doTransfer(s, target, false);
  }

  Future<void> _doTransfer(Map<String, dynamic> s, String target, bool override) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      await CampusService.transferEnrollment(s['enrollment'].toString(), target, overrideCapacity: override);
      _load();
    } on CampusApiException catch (e) {
      if (e.isSectionFull && widget.isAdmin && !override) {
        if (!mounted) return;
        final go = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: Text(l10n.campusSectionFull),
            content: Text(e.message),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(ctx, false),
                  child: Text(MaterialLocalizations.of(ctx).cancelButtonLabel)),
              TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.campusSectionOverride)),
            ],
          ),
        );
        if (go == true) await _doTransfer(s, target, true);
      } else {
        _snack(e.message);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l10n.campusDashboardTitle, style: LsType.head(context, size: 15)),
          Text(widget.sectionLabel, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ]),
      ),
      body: RefreshIndicator(onRefresh: _load, child: _body(cs, l10n)),
    );
  }

  Widget _body(ColorScheme cs, AppLocalizations l10n) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 90),
        SizedBox(height: 8),
        LsSkeletonBox(height: 58),
        SizedBox(height: 8),
        LsSkeletonBox(height: 58),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(title: l10n.campusPanelLoadFailed, subtitle: _error, retryLabel: l10n.retry, onRetry: _load),
      ]);
    }
    final d = _d!;
    final count = Map<String, dynamic>.from(d['count'] as Map);
    final enrolled = (count['enrolled'] ?? 0) as int;
    final cap = count['capacity'] as int?;
    final att = Map<String, dynamic>.from(d['attendance'] as Map);
    final today = Map<String, dynamic>.from(att['today'] as Map);
    final pct = att['last_30_days_percent'];
    final students = (d['students'] as List).map((e) => Map<String, dynamic>.from(e as Map)).toList();
    final pending = (d['pending_subject_requests'] as List).map((e) => Map<String, dynamic>.from(e as Map)).toList();

    return ListView(padding: const EdgeInsets.fromLTRB(14, 12, 14, 24), children: [
      LsCard(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(l10n.campusSectionCapacity, style: LsType.head(context, size: 13)),
            const Spacer(),
            Text(cap == null ? '$enrolled' : '$enrolled / $cap', style: LsType.head(context, size: 15)),
          ]),
          if (cap != null) ...[
            const SizedBox(height: 8),
            LinearProgressIndicator(value: cap == 0 ? 0 : (enrolled / cap).clamp(0.0, 1.0).toDouble()),
            const SizedBox(height: 6),
            Text(
              count['is_full'] == true
                  ? l10n.campusSectionFull
                  : '${l10n.campusRosterSeatsLeft}: ${count['seats_left']}',
              style: TextStyle(fontSize: 11.5, color: count['is_full'] == true ? cs.error : cs.onSurfaceVariant),
            ),
          ],
        ]),
      ),
      const SizedBox(height: 10),
      LsCard(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l10n.campusDashboardAttendanceToday, style: LsType.head(context, size: 13)),
          const SizedBox(height: 6),
          Text(
            'P ${today['present'] ?? 0}  ·  L ${today['late'] ?? 0}  ·  A ${today['absent'] ?? 0}  ·  Leave ${today['leave'] ?? 0}',
            style: const TextStyle(fontSize: 13),
          ),
          const SizedBox(height: 6),
          Text('${l10n.campusDashboardAttendance30}: ${pct == null ? '—' : '$pct%'}',
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ]),
      ),
      if (pending.isNotEmpty) ...[
        const SizedBox(height: 10),
        LsSectionHead(title: l10n.campusDashboardPending),
        for (final p in pending)
          ListTile(
            dense: true,
            leading: const Icon(Icons.hourglass_top_rounded, size: 18),
            title: Text('${p['subject']} — @${p['staff']}'),
          ),
      ],
      const SizedBox(height: 6),
      Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Text(l10n.campusDashboardDoubtsSoon, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
      ),
      LsSectionHead(title: l10n.campusStudents),
      for (final s in students)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: LsCard(
            padding: const EdgeInsets.fromLTRB(12, 6, 4, 6),
            child: Row(children: [
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(
                    '${s['first_name'] ?? ''} ${s['last_name'] ?? ''}'.trim().isEmpty
                        ? '${s['username']}'
                        : '${s['first_name'] ?? ''} ${s['last_name'] ?? ''}'.trim(),
                    style: LsType.head(context, size: 13.5),
                  ),
                  Text('@${s['username']}', style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
                ]),
              ),
              if ('${s['roll_number'] ?? ''}'.isNotEmpty) LsStatusChip(label: '#${s['roll_number']}', color: cs.primary),
              PopupMenuButton<String>(
                onSelected: (v) => v == 'transfer' ? _transfer(s) : _remove(s),
                itemBuilder: (_) => [
                  PopupMenuItem(value: 'transfer', child: Text(l10n.campusRosterTransfer)),
                  PopupMenuItem(value: 'remove', child: Text(l10n.campusRosterRemove)),
                ],
              ),
            ]),
          ),
        ),
      if (d['students_truncated'] == true)
        Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Text('…', style: TextStyle(color: cs.onSurfaceVariant)),
        ),
    ]);
  }
}
