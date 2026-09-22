import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';
import 'section_detail_screen.dart';

// ============================================================
// CAMPUS — SECTIONS (per class)
//
// `Section` ka apna `campus` field nahi hai — sirf `school_class` (§19),
// isliye ye screen hamesha ek specific `SchoolClass` ke context me khulti
// hai (`CampusSetupScreen`'s Classes card se). `POST /sections/` response
// `bridge.create_section_group` fire-and-forget trigger karta hai — us par
// yahan koi UI depend nahi karti, bas list refresh hoti hai.
// ============================================================

class ClassSectionsSetupScreen extends StatefulWidget {
  final SchoolClass schoolClass;
  final CampusAccess access;
  const ClassSectionsSetupScreen({super.key, required this.schoolClass, required this.access});

  @override
  State<ClassSectionsSetupScreen> createState() => _ClassSectionsSetupScreenState();
}

class _ClassSectionsSetupScreenState extends State<ClassSectionsSetupScreen> {
  bool _loading = true;
  String? _error;
  List<Section> _sections = const [];

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
      final rows = await CampusService.sections(schoolClassId: widget.schoolClass.id);
      if (!mounted) return;
      rows.sort((a, b) => a.name.compareTo(b.name));
      setState(() {
        _sections = rows;
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

  Future<void> _add() async {
    final created = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _AddSectionSheet(schoolClassId: widget.schoolClass.id),
    );
    if (created == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: lsAppBar(context, title: l10n.setupSectionsTitle(widget.schoolClass.name)),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _add,
        icon: const Icon(Icons.add_rounded),
        label: Text(l10n.setupSectionsAdd),
      ),
      body: RefreshIndicator(onRefresh: _load, child: _body(l10n)),
    );
  }

  Widget _body(AppLocalizations l10n) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 56),
        SizedBox(height: 10),
        LsSkeletonBox(height: 56),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(
          title: l10n.setupLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _load,
        ),
      ]);
    }
    if (_sections.isEmpty) {
      return EmptyStateWidget(
        icon: Icons.groups_2_outlined,
        title: l10n.setupSectionsEmpty,
        actionLabel: l10n.setupSectionsAdd,
        onAction: _add,
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 90),
      itemCount: _sections.length,
      itemBuilder: (_, i) {
        final s = _sections[i];
        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: LsCard(
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => SectionDetailScreen(
                  schoolClass: widget.schoolClass,
                  section: s,
                  access: widget.access,
                ),
              ),
            ),
            child: Row(children: [
              Icon(Icons.groups_2_outlined, size: 18, color: Theme.of(context).colorScheme.onSurfaceVariant),
              const SizedBox(width: 10),
              Expanded(child: Text(s.name, style: LsType.head(context, size: 14))),
              const Icon(Icons.chevron_right_rounded, size: 18),
            ]),
          ),
        );
      },
    );
  }
}

class _AddSectionSheet extends StatefulWidget {
  final String schoolClassId;
  const _AddSectionSheet({required this.schoolClassId});

  @override
  State<_AddSectionSheet> createState() => _AddSectionSheetState();
}

class _AddSectionSheetState extends State<_AddSectionSheet> {
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
      await CampusService.createSection(schoolClassId: widget.schoolClassId, name: _name.text.trim());
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
          Align(
            alignment: Alignment.centerLeft,
            child: Text(l10n.setupSectionsAdd, style: LsType.head(context, size: 16)),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _name,
            enabled: !_saving,
            textCapitalization: TextCapitalization.characters,
            decoration: InputDecoration(
              labelText: l10n.sectionNameLabel,
              hintText: l10n.sectionNameHint,
              border: const OutlineInputBorder(),
            ),
            onSubmitted: (_) => _submit(),
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerLeft,
              child: Text(_error!, style: TextStyle(color: cs.error, fontSize: 12.5)),
            ),
          ],
          const SizedBox(height: 14),
          LsPrimaryButton(label: l10n.save, loading: _saving, onPressed: _saving ? null : _submit),
        ]),
      ),
    );
  }
}
