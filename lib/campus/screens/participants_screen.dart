import 'dart:async';

import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_t4_models.dart';
import '../services/campus_service.dart';

// ============================================================
// [T4 §A] PARTICIPANTS
//
// Ek hi list me staff (role-wise) / students / parents. Backend scope
// khud lagata hai (admin = poora campus, principal/HOD = apna department,
// class teacher = apna section; baaki ko 403) — yahan sirf filter chips,
// search aur "load more" hai. Section/class se khola jaye to `sectionId` do.
// ============================================================

String participantCategoryLabel(AppLocalizations l10n, String key) => switch (key) {
      'admin' => l10n.campusCategoryAdmin,
      'principal_hod' => l10n.campusCategoryPrincipal,
      'moderator' => l10n.campusCategoryModerator,
      'class_teacher' => l10n.campusCategoryClassTeacher,
      'subject_teacher' => l10n.campusCategorySubjectTeacher,
      'non_teaching' => l10n.campusCategoryNonTeaching,
      'student' => l10n.campusCategoryStudent,
      'parent' => l10n.campusCategoryParent,
      _ => l10n.campusCategoryAll,
    };

class ParticipantsScreen extends StatefulWidget {
  final String campusId;
  final String? sectionId;
  final String? title;

  const ParticipantsScreen({super.key, required this.campusId, this.sectionId, this.title});

  @override
  State<ParticipantsScreen> createState() => _ParticipantsScreenState();
}

class _ParticipantsScreenState extends State<ParticipantsScreen> {
  final _scroll = ScrollController();
  Timer? _debounce;

  String _category = ''; // '' = all
  String _query = '';
  int _page = 1;
  int _count = 0;
  bool _hasNext = false;
  bool _loading = true;
  bool _loadingMore = false;
  String? _error;
  List<ParticipantRow> _rows = const [];
  Map<String, dynamic>? _summary;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(() {
      if (_scroll.position.pixels > _scroll.position.maxScrollExtent - 300) _loadMore();
    });
    _load();
    _loadSummary();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _loadSummary() async {
    try {
      final s = await CampusService.participantsSummary(widget.campusId, sectionId: widget.sectionId);
      if (mounted) setState(() => _summary = s);
    } catch (_) {
      // counts on the chips are a nicety — the list works without them
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
      _page = 1;
    });
    try {
      final p = await CampusService.participants(
        widget.campusId,
        category: _category,
        q: _query,
        sectionId: widget.sectionId,
      );
      if (!mounted) return;
      setState(() {
        _rows = p.rows;
        _count = p.count;
        _hasNext = p.hasNext;
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

  Future<void> _loadMore() async {
    if (_loading || _loadingMore || !_hasNext) return;
    setState(() => _loadingMore = true);
    try {
      final p = await CampusService.participants(
        widget.campusId,
        category: _category,
        q: _query,
        sectionId: widget.sectionId,
        page: _page + 1,
      );
      if (!mounted) return;
      setState(() {
        _page += 1;
        _rows = [..._rows, ...p.rows];
        _hasNext = p.hasNext;
        _loadingMore = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  int? _countFor(String key) {
    final c = (_summary?['categories'] as Map?)?[key];
    return c is int ? c : null;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(widget.title ?? l10n.campusParticipantsTitle, style: LsType.head(context, size: 15))),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 4),
          child: TextField(
            onChanged: (v) {
              _debounce?.cancel();
              _debounce = Timer(const Duration(milliseconds: 400), () {
                _query = v;
                _load();
              });
            },
            decoration: InputDecoration(
              hintText: l10n.campusParticipantsSearch,
              prefixIcon: const Icon(Icons.search_rounded, size: 20),
              isDense: true,
              border: const OutlineInputBorder(),
            ),
          ),
        ),
        SizedBox(
          height: 46,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            children: [
              for (final key in ['', ...kParticipantCategories])
                if (key.isEmpty || (_countFor(key) ?? 1) > 0)
                  Padding(
                    padding: const EdgeInsets.only(right: 6),
                    child: ChoiceChip(
                      selected: _category == key,
                      label: Text(
                        key.isEmpty
                            ? l10n.campusCategoryAll
                            : '${participantCategoryLabel(l10n, key)}'
                                '${_countFor(key) != null ? ' (${_countFor(key)})' : ''}',
                        style: const TextStyle(fontSize: 12),
                      ),
                      onSelected: (_) {
                        setState(() => _category = key);
                        _load();
                      },
                    ),
                  ),
            ],
          ),
        ),
        Expanded(child: RefreshIndicator(onRefresh: _load, child: _body(cs, l10n))),
      ]),
    );
  }

  Widget _body(ColorScheme cs, AppLocalizations l10n) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 58),
        SizedBox(height: 8),
        LsSkeletonBox(height: 58),
        SizedBox(height: 8),
        LsSkeletonBox(height: 58),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(
          title: l10n.campusParticipantsLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _load,
        ),
      ]);
    }
    if (_rows.isEmpty) {
      return ListView(children: [
        const SizedBox(height: 60),
        EmptyStateWidget(icon: Icons.groups_2_outlined, title: l10n.campusParticipantsEmpty),
      ]);
    }
    return ListView.builder(
      controller: _scroll,
      padding: const EdgeInsets.fromLTRB(14, 4, 14, 20),
      itemCount: _rows.length + 1,
      itemBuilder: (_, i) {
        if (i == _rows.length) {
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 12),
            child: Center(
              child: _loadingMore
                  ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                  : Text('$_count', style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
            ),
          );
        }
        return _ParticipantTile(row: _rows[i], l10n: l10n);
      },
    );
  }
}

class _ParticipantTile extends StatelessWidget {
  final ParticipantRow row;
  final AppLocalizations l10n;
  const _ParticipantTile({required this.row, required this.l10n});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final parts = <String>[
      if (row.className != null) row.className!,
      if (row.sectionName != null) row.sectionName!,
      if (row.departmentName != null && row.category != 'student') row.departmentName!,
      if (row.childUsername != null) '@${row.childUsername}',
    ];
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: LsCard(
        padding: const EdgeInsets.fromLTRB(12, 11, 12, 11),
        child: Row(children: [
          CircleAvatar(
            radius: 17,
            backgroundColor: cs.surfaceContainerHighest,
            child: Text(
              row.name.isEmpty ? '?' : row.name.substring(0, 1).toUpperCase(),
              style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.onSurface),
            ),
          ),
          const SizedBox(width: 11),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(row.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: LsType.head(context, size: 13.5)),
              Text(
                ['@${row.username}', ...parts].join(' · '),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant),
              ),
            ]),
          ),
          if (row.rollNumber.isNotEmpty) LsStatusChip(label: '#${row.rollNumber}', color: cs.primary),
          if (row.rollNumber.isEmpty)
            LsStatusChip(label: participantCategoryLabel(l10n, row.category), color: cs.secondary),
        ]),
      ),
    );
  }
}
