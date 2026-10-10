import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../services/campus_service.dart';

// ============================================================
// [T4 §B] CAMPUS CONTROL PANEL (admin / principal)
//
// 4 tabs: Overview (counts + setup progress + "needs attention"),
// Assignments (teacher x section/subject matrix + direct assign with
// preview), Bulk import (CSV, dry-run first) and Audit log.
// Sab data backend se (campus/panel.py) — yahan koi business rule nahi.
// ============================================================

class CampusControlPanelScreen extends StatelessWidget {
  final String campusId;
  const CampusControlPanelScreen({super.key, required this.campusId});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return DefaultTabController(
      length: 4,
      child: Scaffold(
        appBar: AppBar(
          title: Text(l10n.campusControlPanelTitle, style: LsType.head(context, size: 15)),
          bottom: TabBar(isScrollable: true, tabs: [
            Tab(text: l10n.campusPanelOverview),
            Tab(text: l10n.campusPanelAssignments),
            Tab(text: l10n.campusPanelImport),
            Tab(text: l10n.campusPanelAudit),
          ]),
        ),
        body: TabBarView(children: [
          _OverviewTab(campusId: campusId),
          _AssignmentsTab(campusId: campusId),
          _ImportTab(campusId: campusId),
          _AuditTab(campusId: campusId),
        ]),
      ),
    );
  }
}

/// Small helper: load a Map once, show skeleton / error / content.
class _Loader extends StatefulWidget {
  final Future<Map<String, dynamic>> Function() load;
  final Widget Function(BuildContext, Map<String, dynamic>, Future<void> Function()) builder;
  const _Loader({required this.load, required this.builder});

  @override
  State<_Loader> createState() => _LoaderState();
}

class _LoaderState extends State<_Loader> {
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _data;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final d = await widget.load();
      if (!mounted) return;
      setState(() {
        _data = d;
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
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 90),
        SizedBox(height: 8),
        LsSkeletonBox(height: 70),
        SizedBox(height: 8),
        LsSkeletonBox(height: 70),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(title: l10n.campusPanelLoadFailed, subtitle: _error, retryLabel: l10n.retry, onRetry: _reload),
      ]);
    }
    return RefreshIndicator(onRefresh: _reload, child: widget.builder(context, _data!, _reload));
  }
}

// ------------------------------------------------------------ Overview
class _OverviewTab extends StatelessWidget {
  final String campusId;
  const _OverviewTab({required this.campusId});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return _Loader(
      load: () => CampusService.controlPanelOverview(campusId),
      builder: (ctx, d, _) {
        final setup = Map<String, dynamic>.from(d['setup'] as Map);
        final steps = (setup['steps'] as List).map((e) => Map<String, dynamic>.from(e as Map)).toList();
        final done = (setup['completed'] ?? 0) as int;
        final total = (setup['total'] ?? 1) as int;
        final att = Map<String, dynamic>.from(d['attention'] as Map);
        final counts = Map<String, dynamic>.from(d['counts'] as Map);
        final staff = Map<String, dynamic>.from(counts['staff'] as Map);
        final staffTotal = staff.values.fold<int>(0, (a, b) => a + (b as int));
        Widget stat(String label, Object v) => Expanded(
              child: LsCard(
                padding: const EdgeInsets.all(12),
                child: Column(children: [
                  Text('$v', style: LsType.head(ctx, size: 18)),
                  const SizedBox(height: 2),
                  Text(label, textAlign: TextAlign.center, style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
                ]),
              ),
            );
        return ListView(padding: const EdgeInsets.fromLTRB(14, 12, 14, 24), children: [
          Row(children: [
            stat(l10n.campusCategoryStudent, counts['students'] ?? 0),
            const SizedBox(width: 8),
            stat(l10n.campusCategoryParent, counts['parents'] ?? 0),
            const SizedBox(width: 8),
            stat(l10n.campusParticipantsTitle, staffTotal),
          ]),
          const SizedBox(height: 12),
          LsCard(
            padding: const EdgeInsets.all(14),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Text(l10n.campusPanelSetupProgress, style: LsType.head(ctx, size: 13)),
                const Spacer(),
                Text('$done / $total', style: LsType.head(ctx, size: 13)),
              ]),
              const SizedBox(height: 8),
              LinearProgressIndicator(value: total == 0 ? 0 : (done / total).clamp(0.0, 1.0).toDouble()),
              const SizedBox(height: 8),
              for (final s in steps)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Row(children: [
                    Icon(s['done'] == true ? Icons.check_circle : Icons.radio_button_unchecked,
                        size: 16, color: s['done'] == true ? cs.primary : cs.onSurfaceVariant),
                    const SizedBox(width: 8),
                    Expanded(child: Text('${s['key']}', style: const TextStyle(fontSize: 12.5))),
                    Text('${s['count']}', style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                    if (setup['next_step'] == s['key'])
                      Padding(
                        padding: const EdgeInsets.only(left: 6),
                        child: LsStatusChip(label: l10n.campusPanelNextStep, color: cs.tertiary),
                      ),
                  ]),
                ),
            ]),
          ),
          const SizedBox(height: 12),
          LsSectionHead(title: l10n.campusPanelAttention),
          _attention(l10n.campusPanelPendingRequests, att['pending_subject_requests'], cs),
          _attention(l10n.campusPanelNoClassTeacher, att['sections_without_class_teacher'], cs),
          _attention(l10n.campusPanelFullSections, att['sections_full'], cs),
          _attention(l10n.campusPanelNearCapacity, att['sections_near_capacity'], cs),
        ]);
      },
    );
  }

  Widget _attention(String label, Object? n, ColorScheme cs) {
    final v = (n ?? 0) as int;
    return ListTile(
      dense: true,
      leading: Icon(v > 0 ? Icons.warning_amber_rounded : Icons.check_circle_outline,
          size: 18, color: v > 0 ? cs.error : cs.primary),
      title: Text(label, style: const TextStyle(fontSize: 13)),
      trailing: Text('$v', style: const TextStyle(fontWeight: FontWeight.w700)),
    );
  }
}

// ------------------------------------------------------------ Assignments
class _AssignmentsTab extends StatelessWidget {
  final String campusId;
  const _AssignmentsTab({required this.campusId});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return _Loader(
      load: () => CampusService.assignmentMatrix(campusId),
      builder: (ctx, d, reload) {
        final rows = (d['rows'] as List).map((e) => Map<String, dynamic>.from(e as Map)).toList();
        final cols = Map<String, dynamic>.from(d['columns'] as Map);
        return Stack(children: [
          ListView(padding: const EdgeInsets.fromLTRB(14, 12, 14, 90), children: [
            for (final r in rows)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: LsCard(
                  padding: const EdgeInsets.all(12),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Row(children: [
                      Expanded(
                        child: Text(
                          '${r['user']['first_name'] ?? ''} ${r['user']['last_name'] ?? ''}'.trim().isEmpty
                              ? '@${r['user']['username']}'
                              : '${r['user']['first_name']} ${r['user']['last_name']}'.trim(),
                          style: LsType.head(ctx, size: 13.5),
                        ),
                      ),
                      LsStatusChip(label: '${r['role']}'.replaceAll('_', ' '), color: cs.secondary),
                    ]),
                    const SizedBox(height: 6),
                    Text(
                      '${l10n.campusMatrixClassTeacher}: ${(r['class_teacher_of'] as List).length}   ·   '
                      '${l10n.campusMatrixSubject}: ${(r['subjects'] as List).length}   ·   '
                      '${l10n.campusMatrixSection}: ${r['load']['sections']}',
                      style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
                    ),
                    if ((r['warnings'] as List).isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 6),
                        child: Wrap(spacing: 6, children: [
                          for (final w in (r['warnings'] as List))
                            LsStatusChip(
                              label: w == 'heavy_load' ? l10n.campusMatrixHeavyLoad : l10n.campusMatrixMultiSection,
                              color: cs.error,
                            ),
                        ]),
                      ),
                  ]),
                ),
              ),
          ]),
          Positioned(
            right: 16,
            bottom: 16,
            child: FloatingActionButton.extended(
              icon: const Icon(Icons.person_add_alt_1_rounded),
              label: Text(l10n.campusMatrixDirectAssign),
              onPressed: () async {
                final changed = await showModalBottomSheet<bool>(
                  context: ctx,
                  isScrollControlled: true,
                  builder: (_) => _AssignSheet(campusId: campusId, rows: rows, columns: cols),
                );
                if (changed == true) reload();
              },
            ),
          ),
        ]);
      },
    );
  }
}

class _AssignSheet extends StatefulWidget {
  final String campusId;
  final List<Map<String, dynamic>> rows;
  final Map<String, dynamic> columns;
  const _AssignSheet({required this.campusId, required this.rows, required this.columns});

  @override
  State<_AssignSheet> createState() => _AssignSheetState();
}

class _AssignSheetState extends State<_AssignSheet> {
  String? _staff, _section, _subject;
  String _kind = 'subject';
  bool _replace = false;
  bool _busy = false;
  Map<String, dynamic>? _result;
  String? _error;

  List<DropdownMenuItem<String>> get _sectionItems => [
        for (final c in (widget.columns['classes'] as List))
          for (final s in (c['sections'] as List))
            DropdownMenuItem(value: s['id'].toString(), child: Text('${c['name']} ${s['name']}')),
      ];

  Future<void> _run(bool dry) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final r = await CampusService.bulkAssign(
        widget.campusId,
        [
          {
            'staff': _staff,
            'section': _section,
            'kind': _kind,
            if (_kind == 'subject') 'subject': _subject,
          }
        ],
        dryRun: dry,
        replace: _replace,
      );
      if (!mounted) return;
      setState(() => _result = r);
      final status = (r['results'] as List).first['status'];
      if (!dry && (status == 'created' || status == 'updated' || status == 'unchanged')) {
        Navigator.pop(context, true);
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final ready = _staff != null && _section != null && (_kind == 'class_teacher' || _subject != null);
    final res = (_result?['results'] as List?)?.first as Map?;
    return Padding(
      padding: EdgeInsets.fromLTRB(16, 16, 16, 16 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(l10n.campusMatrixDirectAssign, style: LsType.head(context, size: 15)),
          const SizedBox(height: 10),
          SegmentedButton<String>(
            segments: [
              ButtonSegment(value: 'subject', label: Text(l10n.campusMatrixSubject)),
              ButtonSegment(value: 'class_teacher', label: Text(l10n.campusMatrixClassTeacher)),
            ],
            selected: {_kind},
            onSelectionChanged: (s) => setState(() {
              _kind = s.first;
              _result = null;
            }),
          ),
          const SizedBox(height: 10),
          DropdownButtonFormField<String>(
            value: _staff,
            decoration: InputDecoration(labelText: l10n.campusMatrixTeacher, border: const OutlineInputBorder()),
            items: [
              for (final r in widget.rows)
                DropdownMenuItem(value: r['staff'].toString(), child: Text('@${r['user']['username']} · ${r['role']}')),
            ],
            onChanged: (v) => setState(() => _staff = v),
          ),
          const SizedBox(height: 10),
          DropdownButtonFormField<String>(
            value: _section,
            decoration: InputDecoration(labelText: l10n.campusMatrixSection, border: const OutlineInputBorder()),
            items: _sectionItems,
            onChanged: (v) => setState(() => _section = v),
          ),
          if (_kind == 'subject') ...[
            const SizedBox(height: 10),
            DropdownButtonFormField<String>(
              value: _subject,
              decoration: InputDecoration(labelText: l10n.campusMatrixSubject, border: const OutlineInputBorder()),
              items: [
                for (final s in (widget.columns['subjects'] as List))
                  DropdownMenuItem(value: s['id'].toString(), child: Text('${s['name']}')),
              ],
              onChanged: (v) => setState(() => _subject = v),
            ),
          ],
          SwitchListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            title: Text(l10n.campusMatrixReplace, style: const TextStyle(fontSize: 13)),
            value: _replace,
            onChanged: (v) => setState(() => _replace = v),
          ),
          if (res != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                '${res['status'] == 'conflict' ? '${l10n.campusMatrixConflict}: ' : ''}${res['status']}'
                '${(res['detail'] ?? '').toString().isEmpty ? '' : ' — ${res['detail']}'}'
                '${(res['warnings'] as List).isEmpty ? '' : '  ⚠ ${(res['warnings'] as List).join(', ')}'}',
                style: TextStyle(
                    fontSize: 12.5,
                    color: res['status'] == 'conflict' || res['status'] == 'error'
                        ? Theme.of(context).colorScheme.error
                        : null),
              ),
            ),
          if (_error != null) Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          Row(children: [
            Expanded(
              child: OutlinedButton(
                  onPressed: ready && !_busy ? () => _run(true) : null, child: Text(l10n.campusMatrixPreview)),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: FilledButton(
                  onPressed: ready && !_busy ? () => _run(false) : null, child: Text(l10n.campusMatrixApply)),
            ),
          ]),
        ]),
      ),
    );
  }
}

// ------------------------------------------------------------ Bulk import
class _ImportTab extends StatefulWidget {
  final String campusId;
  const _ImportTab({required this.campusId});

  @override
  State<_ImportTab> createState() => _ImportTabState();
}

class _ImportTabState extends State<_ImportTab> {
  static const _kinds = {
    'students': 'username, class, section, roll_number, enrollment_no',
    'staff': 'username, role, department',
    'subjects': 'name, code, department',
    'enrollments': 'username, class, section',
  };
  String _kind = 'students';
  bool _dry = true;
  bool _busy = false;
  String? _error;
  Map<String, dynamic>? _report;

  Future<void> _pickAndRun() async {
    final res = await FilePicker.platform.pickFiles(type: FileType.custom, allowedExtensions: ['csv'], withData: true);
    final f = res?.files.single;
    if (f == null || f.bytes == null) return;
    setState(() {
      _busy = true;
      _error = null;
      _report = null;
    });
    try {
      final r = await CampusService.bulkImport(
        widget.campusId,
        kind: _kind,
        bytes: f.bytes!,
        filename: f.name,
        dryRun: _dry,
      );
      if (mounted) setState(() => _report = r);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final errs = ((_report?['errors'] as List?) ?? const []).map((e) => Map<String, dynamic>.from(e as Map)).toList();
    return ListView(padding: const EdgeInsets.all(14), children: [
      DropdownButtonFormField<String>(
        value: _kind,
        decoration: InputDecoration(labelText: l10n.campusImportKind, border: const OutlineInputBorder()),
        items: [for (final k in _kinds.keys) DropdownMenuItem(value: k, child: Text(k))],
        onChanged: (v) => setState(() => _kind = v ?? _kind),
      ),
      const SizedBox(height: 6),
      Text('CSV: ${_kinds[_kind]}', style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
      SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: Text(l10n.campusImportDryRun, style: const TextStyle(fontSize: 13)),
        value: _dry,
        onChanged: (v) => setState(() => _dry = v),
      ),
      FilledButton.icon(
        icon: const Icon(Icons.upload_file_rounded),
        label: Text(_dry ? l10n.campusImportPickFile : l10n.campusImportRun),
        onPressed: _busy ? null : _pickAndRun,
      ),
      if (_busy) const Padding(padding: EdgeInsets.all(16), child: Center(child: CircularProgressIndicator())),
      if (_error != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(_error!, style: TextStyle(color: cs.error))),
      if (_report != null) ...[
        const SizedBox(height: 14),
        LsCard(
          padding: const EdgeInsets.all(14),
          child: Text(
            '${_report!['total']} rows  ·  ✔ ${_report!['created']}  ·  ↷ ${_report!['skipped']}  ·  ✖ ${_report!['error_count']}',
            style: LsType.head(context, size: 13),
          ),
        ),
        if (errs.isNotEmpty) ...[
          LsSectionHead(title: l10n.campusImportErrors),
          for (final e in errs)
            ListTile(dense: true, leading: Text('#${e['row']}'), title: Text('${e['error']}', style: const TextStyle(fontSize: 12.5))),
        ],
      ],
    ]);
  }
}

// ------------------------------------------------------------ Audit
class _AuditTab extends StatefulWidget {
  final String campusId;
  const _AuditTab({required this.campusId});

  @override
  State<_AuditTab> createState() => _AuditTabState();
}

class _AuditTabState extends State<_AuditTab> {
  final List<Map<String, dynamic>> _rows = [];
  int _page = 1;
  bool _hasNext = false;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load(reset: true);
  }

  Future<void> _load({bool reset = false}) async {
    if (reset) {
      _page = 1;
      _rows.clear();
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final d = await CampusService.auditLog(widget.campusId, page: _page);
      if (!mounted) return;
      setState(() {
        _rows.addAll((d['results'] as List).map((e) => Map<String, dynamic>.from(e as Map)));
        _hasNext = d['next'] != null;
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
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    if (_error != null && _rows.isEmpty) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(title: l10n.campusPanelLoadFailed, subtitle: _error, retryLabel: l10n.retry, onRetry: () => _load(reset: true)),
      ]);
    }
    if (_loading && _rows.isEmpty) return const Center(child: CircularProgressIndicator());
    if (_rows.isEmpty) {
      return EmptyStateWidget(icon: Icons.history_rounded, title: l10n.campusAuditEmpty);
    }
    return RefreshIndicator(
      onRefresh: () => _load(reset: true),
      child: ListView.builder(
        padding: const EdgeInsets.fromLTRB(14, 8, 14, 20),
        itemCount: _rows.length + (_hasNext ? 1 : 0),
        itemBuilder: (_, i) {
          if (i == _rows.length) {
            return TextButton(
              onPressed: _loading
                  ? null
                  : () {
                      _page += 1;
                      _load();
                    },
              child: const Text('…'),
            );
          }
          final r = _rows[i];
          final actor = r['actor'] is Map ? '@${r['actor']['username']}' : '—';
          return ListTile(
            dense: true,
            leading: const Icon(Icons.history_rounded, size: 18),
            title: Text('${r['summary'] ?? r['action']}', style: const TextStyle(fontSize: 12.5)),
            subtitle: Text('$actor · ${r['action']} · ${'${r['created_at']}'.split('T').first}',
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
          );
        },
      ),
    );
  }
}
