import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// SECTION — STUDENT ROSTER
//
// Simple list, par ek cheez deliberate hai: search bar tabhi dikhta hai
// jab 15+ students hon. 8 bachchon ki class me search bar sirf jagah
// khaata hai aur teacher ko lagta hai kuch chhupa hua hai.
// ============================================================

class SectionStudentsScreen extends StatefulWidget {
  final String sectionId;
  final String sectionLabel;

  const SectionStudentsScreen({
    super.key,
    required this.sectionId,
    required this.sectionLabel,
  });

  @override
  State<SectionStudentsScreen> createState() => _SectionStudentsScreenState();
}

class _SectionStudentsScreenState extends State<SectionStudentsScreen> {
  bool _loading = true;
  String? _error;
  List<StudentEnrollment> _roster = const [];
  String _query = '';

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
      final rows = await CampusService.roster(widget.sectionId);
      if (!mounted) return;
      setState(() {
        _roster = rows;
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

  List<StudentEnrollment> get _filtered {
    if (_query.trim().isEmpty) return _roster;
    final q = _query.toLowerCase();
    return _roster.where((e) {
      final name = (e.student?.displayName ?? '').toLowerCase();
      return name.contains(q) || e.rollNumber.contains(q);
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      appBar: AppBar(
        title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l10n.campusStudents, style: LsType.head(context, size: 15)),
          Text(widget.sectionLabel,
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ]),
      ),
      body: RefreshIndicator(onRefresh: _load, child: _body(cs, l10n)),
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
          title: l10n.campusRosterLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _load,
        ),
      ]);
    }
    if (_roster.isEmpty) {
      return EmptyStateWidget(
        icon: Icons.groups_2_outlined,
        title: l10n.campusRosterEmptyTitle,
        subtitle: l10n.campusRosterEmptySubtitle,
      );
    }

    final rows = _filtered;
    return Column(children: [
      if (_roster.length >= 15)
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 4),
          child: TextField(
            onChanged: (v) => setState(() => _query = v),
            decoration: InputDecoration(
              hintText: l10n.campusSearchStudents,
              prefixIcon: const Icon(Icons.search_rounded, size: 20),
              isDense: true,
              border: const OutlineInputBorder(),
            ),
          ),
        ),
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
        child: Row(children: [
          Text(l10n.campusStudentCount(rows.length),
              style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
        ]),
      ),
      Expanded(
        child: rows.isEmpty
            ? EmptyStateWidget(icon: Icons.search_off_rounded, title: l10n.campusNoMatches)
            : ListView.builder(
                padding: const EdgeInsets.fromLTRB(14, 4, 14, 20),
                itemCount: rows.length,
                itemBuilder: (_, i) {
                  final e = rows[i];
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: LsCard(
                      padding: const EdgeInsets.fromLTRB(12, 11, 12, 11),
                      child: Row(children: [
                        CircleAvatar(
                          radius: 17,
                          backgroundColor: cs.surfaceContainerHighest,
                          child: Text(e.student?.initials ?? '?',
                              style: TextStyle(
                                  fontSize: 12,
                                  fontWeight: FontWeight.w700,
                                  color: cs.onSurface)),
                        ),
                        const SizedBox(width: 11),
                        Expanded(
                          child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(e.student?.displayName ?? e.studentId,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: LsType.head(context, size: 13.5)),
                                if (e.student != null)
                                  Text('@${e.student!.username}',
                                      style: TextStyle(
                                          fontSize: 11, color: cs.onSurfaceVariant)),
                              ]),
                        ),
                        if (e.rollNumber.isNotEmpty)
                          LsStatusChip(label: '#${e.rollNumber}', color: cs.primary),
                      ]),
                    ),
                  );
                },
              ),
      ),
    ]);
  }
}
