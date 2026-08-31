// lib/liveclass/screens/poll_templates_screen.dart
//
// NEW screen (frontend integration architecture v3, §1.11, Pass 13).
// Classroom-scoped CRUD for `PollTemplate` — owner/admin only, same
// `canManage` threading convention used elsewhere in this module
// (coupons_screen.dart / staff_management_screen.dart per the doc).
//
// Entry point: classroom_detail_screen.dart's manage sheet (`_openManageSheet`
// / `_manageTile`) should get a "Poll templates" tile pushing this screen
// with the classroom id — see doc §1.11 "Entry point for the new screen".
// Add, right after the existing Certificates tile:
//   _manageTile(ctx, Icons.quiz_outlined, 'Poll Templates', _openPollTemplates),
// with:
//   void _openPollTemplates() {
//     Navigator.push(context, MaterialPageRoute(
//       builder: (_) => PollTemplatesScreen(
//         classroomId: widget.classroomId,
//         classroomTitle: _classroom?.title ?? '',
//       ),
//     ));
//   }
//
// Backend contract used here (`LiveClassApi.pollTemplates`, `PollTemplate`)
// already exists in liveclass_api_service.dart / liveclass_models.dart —
// Pass 13 fields are confirmed, not a skeleton.

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

class PollTemplatesScreen extends StatefulWidget {
  final int classroomId;
  final String classroomTitle;

  const PollTemplatesScreen({
    super.key,
    required this.classroomId,
    this.classroomTitle = '',
  });

  @override
  State<PollTemplatesScreen> createState() => _PollTemplatesScreenState();
}

class _PollTemplatesScreenState extends State<PollTemplatesScreen> {
  bool _loading = true;
  String? _error;
  List<PollTemplate> _templates = [];

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
      final page = await LiveClassApi.pollTemplates.list(widget.classroomId);
      if (!mounted) return;
      setState(() {
        _templates = page.results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load poll templates.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _openEditor({PollTemplate? existing}) async {
    final saved = await showModalBottomSheet<PollTemplate>(
      context: context,
      backgroundColor: Colors.white,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(LiveClassRadius.sheet))),
      builder: (_) => _PollTemplateEditorSheet(classroomId: widget.classroomId, existing: existing),
    );
    if (saved != null) {
      setState(() {
        if (existing == null) {
          _templates = [saved, ..._templates];
        } else {
          _templates = _templates.map((t) => t.id == saved.id ? saved : t).toList();
        }
      });
    }
  }

  Future<void> _delete(PollTemplate t) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete template?'),
        content: Text('"${t.question}" will be removed permanently.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete', style: TextStyle(color: LiveClassColors.danger)),
          ),
        ],
      ),
    );
    if (confirm != true) return;

    final previous = _templates;
    setState(() => _templates = _templates.where((x) => x.id != t.id).toList());
    try {
      await LiveClassApi.pollTemplates.delete(t.id);
    } catch (e) {
      setState(() => _templates = previous);
      _snack(e is LiveClassApiException ? e.message : 'Could not delete template.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(
        widget.classroomTitle.isEmpty ? 'Poll Templates' : 'Poll Templates · ${widget.classroomTitle}',
      ),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: LiveClassColors.navy,
        onPressed: () => _openEditor(),
        icon: const Icon(Icons.add),
        label: const Text('New template'),
      ),
      body: RefreshIndicator(onRefresh: _load, child: _buildBody()),
    );
  }

  Widget _buildBody() {
    if (_loading) return const LiveClassLoading();
    if (_error != null) return LiveClassErrorState(message: _error!, onRetry: _load);
    if (_templates.isEmpty) {
      return const LiveClassEmptyState(
        icon: Icons.quiz_outlined,
        title: 'No poll templates yet',
        subtitle: 'Save a question you ask often so you can fire it into a live session in one tap.',
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(LiveClassSpacing.lg, LiveClassSpacing.lg, LiveClassSpacing.lg, 88),
      itemCount: _templates.length,
      itemBuilder: (context, i) {
        final t = _templates[i];
        return LiveClassCard(
          onTap: () => _openEditor(existing: t),
          child: Row(
            children: [
              const LiveClassIconBadge(icon: Icons.quiz_outlined, size: 38),
              const SizedBox(width: LiveClassSpacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      t.question,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      '${t.options.length} options · ${t.options.take(3).join(", ")}${t.options.length > 3 ? "…" : ""}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                    ),
                  ],
                ),
              ),
              PopupMenuButton<String>(
                onSelected: (v) {
                  if (v == 'edit') _openEditor(existing: t);
                  if (v == 'delete') _delete(t);
                },
                itemBuilder: (_) => const [
                  PopupMenuItem(value: 'edit', child: Text('Edit')),
                  PopupMenuItem(value: 'delete', child: Text('Delete')),
                ],
              ),
            ],
          ),
        );
      },
    );
  }
}

/// Create/edit form — mirrors `_CreatePollSheet`'s option-list UI pattern
/// inside `live_session_screen.dart` (per doc §1.11) so both flows feel the
/// same, without depending on that file directly.
class _PollTemplateEditorSheet extends StatefulWidget {
  final int classroomId;
  final PollTemplate? existing;

  const _PollTemplateEditorSheet({required this.classroomId, this.existing});

  @override
  State<_PollTemplateEditorSheet> createState() => _PollTemplateEditorSheetState();
}

class _PollTemplateEditorSheetState extends State<_PollTemplateEditorSheet> {
  late final TextEditingController _questionCtrl;
  late List<TextEditingController> _optionCtrls;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _questionCtrl = TextEditingController(text: widget.existing?.question ?? '');
    final initialOptions = widget.existing?.options ?? const ['', ''];
    _optionCtrls = initialOptions.map((o) => TextEditingController(text: o)).toList();
    if (_optionCtrls.length < 2) {
      _optionCtrls.addAll(List.generate(2 - _optionCtrls.length, (_) => TextEditingController()));
    }
  }

  @override
  void dispose() {
    _questionCtrl.dispose();
    for (final c in _optionCtrls) {
      c.dispose();
    }
    super.dispose();
  }

  void _addOption() {
    if (_optionCtrls.length >= 6) return;
    setState(() => _optionCtrls.add(TextEditingController()));
  }

  void _removeOption(int i) {
    if (_optionCtrls.length <= 2) return;
    setState(() {
      _optionCtrls[i].dispose();
      _optionCtrls.removeAt(i);
    });
  }

  Future<void> _save() async {
    final question = _questionCtrl.text.trim();
    final options = _optionCtrls.map((c) => c.text.trim()).where((o) => o.isNotEmpty).toList();

    if (question.isEmpty) {
      setState(() => _error = 'Question is required.');
      return;
    }
    if (options.length < 2) {
      setState(() => _error = 'Add at least 2 options.');
      return;
    }

    setState(() {
      _saving = true;
      _error = null;
    });

    final draft = PollTemplate(
      id: widget.existing?.id ?? 0,
      classroomId: widget.classroomId,
      question: question,
      options: options,
    );

    try {
      final saved = widget.existing == null
          ? await LiveClassApi.pollTemplates.create(draft)
          : await LiveClassApi.pollTemplates.update(widget.existing!.id, draft);
      if (mounted) Navigator.pop(context, saved);
    } catch (e) {
      setState(() {
        _error = e is LiveClassApiException ? e.message : 'Could not save the template.';
        _saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(
        left: 20,
        right: 20,
        top: 16,
        bottom: MediaQuery.of(context).viewInsets.bottom + 16,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              widget.existing == null ? 'New Poll Template' : 'Edit Poll Template',
              style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
            ),
            const SizedBox(height: LiveClassSpacing.lg),
            TextField(
              controller: _questionCtrl,
              decoration: liveClassInputDecoration('Question', label: 'Question'),
              maxLines: 2,
            ),
            const SizedBox(height: LiveClassSpacing.lg),
            const Text('Options', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
            const SizedBox(height: LiveClassSpacing.sm),
            for (int i = 0; i < _optionCtrls.length; i++)
              Padding(
                padding: const EdgeInsets.only(bottom: LiveClassSpacing.sm),
                child: Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _optionCtrls[i],
                        decoration: liveClassInputDecoration('Option ${i + 1}'),
                      ),
                    ),
                    if (_optionCtrls.length > 2)
                      IconButton(
                        icon: const Icon(Icons.remove_circle_outline, color: LiveClassColors.danger),
                        onPressed: () => _removeOption(i),
                      ),
                  ],
                ),
              ),
            if (_optionCtrls.length < 6)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: _addOption,
                  icon: const Icon(Icons.add),
                  label: const Text('Add option'),
                ),
              ),
            if (_error != null) ...[
              const SizedBox(height: LiveClassSpacing.sm),
              Text(_error!, style: const TextStyle(color: LiveClassColors.danger, fontSize: 12.5)),
            ],
            const SizedBox(height: LiveClassSpacing.lg),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                style: ElevatedButton.styleFrom(backgroundColor: LiveClassColors.navy, foregroundColor: Colors.white),
                onPressed: _saving ? null : _save,
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: _saving
                      ? const SizedBox(
                          height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : Text(widget.existing == null ? 'Create Template' : 'Save Changes'),
                ),
              ),
            ),
            const SizedBox(height: LiveClassSpacing.sm),
          ],
        ),
      ),
    );
  }
}