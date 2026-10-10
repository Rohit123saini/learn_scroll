import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import '../utils/ts_error_text.dart';
import 'question_editor_sheet.dart';

// ============================================================
// TASK T2 — "Questions" manager for an EXISTING series.
//
// Shown on the series detail screen to whoever the server says may edit
// (`TestSeriesModel.canEdit`: the creator, or — for campus / class series —
// the teaching staff). While the series is a DRAFT you can add, edit, delete
// and drag-reorder questions; once published the list is read-only (the
// backend refuses question changes after publish, so attempts stay valid).
//
// Same editor sheet as the create flow (`QuestionEditorSheet`). Option images
// are uploaded first (the series already exists, so the endpoint can be
// called straight away) and only then is the question saved — a failed upload
// never leaves a half-saved question behind.
//
// Types the editor cannot author (`list` match/order) are listed read-only and
// can still be deleted. Plain English strings on purpose, like the create flow
// (regenerating AppLocalizations is not something this change can do safely).
// ============================================================

class TsQuestionsManager extends StatefulWidget {
  final String seriesId;

  /// Only a draft series accepts question changes.
  final bool isDraft;

  /// Called after every successful add / edit / delete / reorder so the parent
  /// can refresh totals (question count, total marks).
  final VoidCallback? onChanged;

  const TsQuestionsManager({
    super.key,
    required this.seriesId,
    required this.isDraft,
    this.onChanged,
  });

  @override
  State<TsQuestionsManager> createState() => _TsQuestionsManagerState();
}

class _TsQuestionsManagerState extends State<TsQuestionsManager> {
  List<TsQuestion> _items = [];
  bool _loading = true;
  bool _busy = false;
  Object? _loadError;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadError = null;
    });
    try {
      final items = await TestSeriesService.listQuestions(widget.seriesId);
      if (!mounted) return;
      setState(() {
        _items = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loadError = e;
        _loading = false;
      });
    }
  }

  void _toast(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  String _errorText(Object e) => tsErrorMessage(AppLocalizations.of(context)!, e);

  /// Runs one write, blocks double-taps, reports errors, refreshes the parent.
  Future<void> _run(Future<void> Function() job) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await job();
      widget.onChanged?.call();
    } catch (e) {
      _toast(_errorText(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _edit({TsQuestion? existing}) async {
    QuestionDraft? draft;
    if (existing == null) {
      draft = QuestionDraft();
    } else {
      draft = QuestionDraft.fromQuestion(existing);
      if (draft == null) {
        _toast('This question type can’t be edited in the app yet. You can delete it and add a new one.');
        return;
      }
    }

    final result = await showModalBottomSheet<QuestionDraft>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => QuestionEditorSheet(draft: draft!),
    );
    if (result == null || !mounted) return;

    await _run(() async {
      for (final o in result.options) {
        final file = o.imageFile;
        if (file != null && (o.imageUrl == null || o.imageUrl!.isEmpty)) {
          o.imageUrl = await TestSeriesService.uploadOptionImage(widget.seriesId, file);
        }
      }
      final json = result.toJson();
      if (existing == null) {
        await TestSeriesService.addQuestion(widget.seriesId, json);
      } else {
        await TestSeriesService.updateQuestion(widget.seriesId, existing.id, json);
      }
      await _load();
    });
  }

  Future<void> _delete(TsQuestion q, int position) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete question?'),
        content: Text('Question $position will be removed from this test.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Delete')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    await _run(() async {
      await TestSeriesService.deleteQuestion(widget.seriesId, q.id);
      await _load();
    });
  }

  Future<void> _onReorder(int oldIndex, int newIndex) async {
    if (_busy) return;
    if (newIndex > oldIndex) newIndex -= 1;
    if (newIndex == oldIndex) return;
    final before = List<TsQuestion>.of(_items);
    final next = List<TsQuestion>.of(_items);
    final moved = next.removeAt(oldIndex);
    next.insert(newIndex, moved);
    setState(() => _items = next); // optimistic
    setState(() => _busy = true);
    try {
      final saved = await TestSeriesService.reorderQuestions(widget.seriesId, next.map((q) => q.id).toList());
      if (!mounted) return;
      setState(() => _items = saved.isEmpty ? next : saved);
      widget.onChanged?.call();
    } catch (e) {
      if (!mounted) return;
      setState(() => _items = before); // roll back
      _toast(_errorText(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  static String _typeLabel(TsQuestionType t) {
    switch (t) {
      case TsQuestionType.mcq:
        return 'MCQ';
      case TsQuestionType.msq:
        return 'Multiple answers';
      case TsQuestionType.trueFalse:
        return 'True / False';
      case TsQuestionType.fillBlank:
        return 'Fill in the blank';
      case TsQuestionType.numeric:
        return 'Numeric';
      case TsQuestionType.text:
        return 'Written answer';
      case TsQuestionType.list:
        return 'Match / order';
      case TsQuestionType.unknown:
        return 'Question';
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final canChange = widget.isDraft;

    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  _loading ? 'Questions' : 'Questions (${_items.length})',
                  style: LsType.head(context, size: 15),
                ),
              ),
              if (canChange)
                FilledButton.icon(
                  onPressed: _busy ? null : () => _edit(),
                  icon: const Icon(Icons.add_rounded, size: 18),
                  label: const Text('Add question'),
                ),
            ],
          ),
          if (!canChange) ...[
            const SizedBox(height: 6),
            Text(
              'Questions are locked once a test is published.',
              style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
            ),
          ],
          const SizedBox(height: 8),
          if (_loading)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 18),
              child: Center(child: CircularProgressIndicator()),
            )
          else if (_loadError != null)
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(_errorText(_loadError!), style: TextStyle(color: cs.error, fontSize: 13)),
                TextButton(onPressed: _load, child: const Text('Retry')),
              ],
            )
          else if (_items.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Text(
                canChange ? 'No questions yet — tap “Add question”.' : 'No questions.',
                style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
              ),
            )
          else
            ReorderableListView.builder(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              buildDefaultDragHandles: canChange,
              itemCount: _items.length,
              onReorder: canChange ? _onReorder : (_, __) {},
              itemBuilder: (context, i) {
                final q = _items[i];
                final editable = canChange && q.type != TsQuestionType.list && q.type != TsQuestionType.unknown;
                return ListTile(
                  key: ValueKey(q.id),
                  contentPadding: EdgeInsets.zero,
                  leading: CircleAvatar(
                    radius: 15,
                    backgroundColor: cs.primaryContainer,
                    child: Text('${i + 1}', style: TextStyle(fontSize: 12, color: cs.onPrimaryContainer)),
                  ),
                  title: Text(q.text, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13.5)),
                  subtitle: Text(
                    '${_typeLabel(q.type)} · ${q.marks} mark${q.marks == 1 ? '' : 's'}',
                    style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
                  ),
                  onTap: editable && !_busy ? () => _edit(existing: q) : null,
                  trailing: canChange
                      ? Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            if (editable)
                              IconButton(
                                tooltip: 'Edit',
                                icon: const Icon(Icons.edit_outlined, size: 20),
                                onPressed: _busy ? null : () => _edit(existing: q),
                              ),
                            IconButton(
                              tooltip: 'Delete',
                              icon: Icon(Icons.delete_outline_rounded, size: 20, color: cs.error),
                              onPressed: _busy ? null : () => _delete(q, i + 1),
                            ),
                            const SizedBox(width: 24), // room for the drag handle
                          ],
                        )
                      : null,
                );
              },
            ),
        ],
      ),
    );
  }
}
