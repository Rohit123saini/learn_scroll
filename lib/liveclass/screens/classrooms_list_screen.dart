// ============================================================
// LIVECLASS — CLASSROOMS LIST / BROWSE SCREEN
//
// Backend surface used: GET /classrooms/?search=&language=&mine=
// and GET /classrooms/recommended/?limit=.
//
// Follows the same reuse pattern the shared kit already establishes:
// LsCard for the surface, LsFilterChips for language/mine filters,
// LsPostCardSkeleton while loading, ErrorStateWidget/EmptyStateWidget
// for the two failure/empty cases (Task 11.1) instead of a bare
// spinner or a blank screen.
// ============================================================

import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';
import '../models/liveclass_models.dart';
import 'classroom_detail_screen.dart';

class ClassroomsListScreen extends StatefulWidget {
  final LiveClassApi api;
  const ClassroomsListScreen({super.key, required this.api});

  @override
  State<ClassroomsListScreen> createState() => _ClassroomsListScreenState();
}

class _ClassroomsListScreenState extends State<ClassroomsListScreen> {
  final _searchCtrl = TextEditingController();
  int _filterIndex = 0; // 0 = all, 1 = mine, 2..N = a language
  List<String> _languages = const ['hi', 'en'];

  List<Classroom>? _classrooms;
  Object? _error;
  bool _loading = true;

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
      final mine = _filterIndex == 1 ? true : null;
      final language = _filterIndex >= 2 ? _languages[_filterIndex - 2] : null;
      final raw = await widget.api.classrooms(
        search: _searchCtrl.text.trim().isEmpty ? null : _searchCtrl.text.trim(),
        mine: mine,
        language: language,
      );
      setState(() {
        _classrooms = raw.map((e) => Classroom.fromJson(e as Map<String, dynamic>)).toList();
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.liveClassesTitle),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 10),
              child: TextField(
                controller: _searchCtrl,
                onSubmitted: (_) => _load(),
                decoration: InputDecoration(
                  hintText: t.searchClassroomsHint,
                  prefixIcon: const Icon(Icons.search_rounded, size: 20),
                  filled: true,
                  fillColor: cs.surfaceVariant,
                  contentPadding: const EdgeInsets.symmetric(vertical: 0),
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
                ),
              ),
            ),
            LsFilterChips(
              labels: [t.filterAll, t.filterMine, ..._languages.map((l) => l.toUpperCase())],
              selectedIndex: _filterIndex,
              onSelected: (i) {
                setState(() => _filterIndex = i);
                _load();
              },
            ),
            const SizedBox(height: 10),
            if (_loading)
              Column(children: List.generate(3, (_) => const LsPostCardSkeleton()))
            else if (_error != null)
              ErrorStateWidget(
                title: t.couldNotLoadClassrooms,
                subtitle: t.checkConnectionRetry,
                retryLabel: t.retry,
                onRetry: _load,
              )
            else if (_classrooms!.isEmpty)
              EmptyStateWidget(
                title: t.noClassroomsFound,
                subtitle: t.tryDifferentSearch,
                icon: Icons.school_outlined,
              )
            else
              ..._classrooms!.map((c) => _ClassroomCard(
                    classroom: c,
                    onTap: () => Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => ClassroomDetailScreen(api: widget.api, classroomId: c.id),
                    )),
                  )),
            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }
}

class _ClassroomCard extends StatelessWidget {
  final Classroom classroom;
  final VoidCallback onTap;
  const _ClassroomCard({required this.classroom, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      onTap: onTap,
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(12),
          child: classroom.coverImageUrl != null
              ? Image.network(classroom.coverImageUrl!, width: 68, height: 68, fit: BoxFit.cover)
              : Container(width: 68, height: 68, color: cs.surfaceVariant, child: Icon(Icons.school_rounded, color: cs.onSurfaceVariant)),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(classroom.title, maxLines: 1, overflow: TextOverflow.ellipsis, style: LsType.head(context)),
            const SizedBox(height: 3),
            Text(classroom.teacherName,
                maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
            const SizedBox(height: 6),
            Row(children: [
              LsStatusChip(label: classroom.language.toUpperCase(), color: cs.primary),
              const SizedBox(width: 6),
              if (classroom.ratingCount > 0)
                LsStatusChip(
                  label: '${classroom.ratingAvg.toStringAsFixed(1)} (${classroom.ratingCount})',
                  color: Colors.amber.shade700,
                  icon: Icons.star_rounded,
                ),
              const Spacer(),
              Text(t.enrolledCountLabel(classroom.enrolledCount),
                  style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
            ]),
          ]),
        ),
      ]),
    );
  }
}
