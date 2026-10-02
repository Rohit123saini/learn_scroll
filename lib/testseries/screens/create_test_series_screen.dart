import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import '../utils/ts_error_text.dart';

// ============================================================
// TASK 8 — "Create Test Series" for INDIVIDUAL accounts.
//
// Any authenticated user can already POST a `source=individual` series
// directly (`TestSeriesViewSet.perform_create` — no staff/institution
// check on that path; see `permissions.py`/`views.py`). This screen is
// the missing entry point + question builder that actually drives it:
//
//   1. POST  /testseries/                 -> draft series
//   2. POST  /testseries/{id}/questions-bulk/   -> all questions, atomic
//   3. POST  /testseries/{id}/publish/     -> goes live (only once the
//      answer key is complete — enforced here in the question editor,
//      and re-checked server-side regardless).
//
// If step 2 or 3 fails, the series still exists as a draft (visible only
// to its creator — see TestSeriesViewSet.get_queryset), so nothing is
// silently lost; the user can reopen this flow's "My drafts" entry
// point later. Kept as plain English strings (no new l10n keys) since
// regenerating the generated AppLocalizations classes isn't something
// this change can safely do — see PR notes.
// ============================================================

enum _QType { text, mcq, msq }

class _OptionDraft {
  String id;
  String text;
  _OptionDraft({required this.id, this.text = ''});
}

class _QuestionDraft {
  _QType type;
  String text;
  int marks;
  int negativeMarks;
  List<_OptionDraft> options;
  Set<String> correctIds;

  _QuestionDraft({
    this.type = _QType.mcq,
    this.text = '',
    this.marks = 1,
    this.negativeMarks = 0,
    List<_OptionDraft>? options,
    Set<String>? correctIds,
  })  : options = options ?? [_OptionDraft(id: 'a'), _OptionDraft(id: 'b')],
        correctIds = correctIds ?? {};

  bool get isComplete {
    if (text.trim().isEmpty || marks < 1) return false;
    if (type == _QType.text) return true;
    final filled = options.where((o) => o.text.trim().isNotEmpty).length;
    if (filled < 2) return false;
    return correctIds.isNotEmpty;
  }

  String get typeLabel => switch (type) {
        _QType.text => 'Text / Subjective',
        _QType.mcq => 'Multiple choice (single answer)',
        _QType.msq => 'Multiple select',
      };

  Map<String, dynamic> toJson() {
    if (type == _QType.text) {
      return {
        'question_type': 'text',
        'text': text.trim(),
        'marks': marks,
        'negative_marks': 0,
        'options': const [],
        'correct_answer': const {},
      };
    }
    final opts = options
        .where((o) => o.text.trim().isNotEmpty)
        .map((o) => {'id': o.id, 'text': o.text.trim()})
        .toList();
    final validIds = opts.map((o) => o['id']).toSet();
    final correct = correctIds.where(validIds.contains).toList();
    return {
      'question_type': type == _QType.mcq ? 'mcq' : 'msq',
      'text': text.trim(),
      'marks': marks,
      'negative_marks': negativeMarks,
      'options': opts,
      'correct_answer': type == _QType.mcq
          ? {'option_id': correct.isNotEmpty ? correct.first : null}
          : {'option_ids': correct},
    };
  }
}

class CreateTestSeriesScreen extends StatefulWidget {
  const CreateTestSeriesScreen({super.key});

  @override
  State<CreateTestSeriesScreen> createState() => _CreateTestSeriesScreenState();
}

class _CreateTestSeriesScreenState extends State<CreateTestSeriesScreen> {
  final _titleCtrl = TextEditingController();
  final _descCtrl = TextEditingController();
  final _priceCtrl = TextEditingController(text: '10');
  final _durationCtrl = TextEditingController();
  final _attemptsCtrl = TextEditingController(text: '1');

  final List<_QuestionDraft> _questions = [];
  bool _submitting = false;

  @override
  void dispose() {
    _titleCtrl.dispose();
    _descCtrl.dispose();
    _priceCtrl.dispose();
    _durationCtrl.dispose();
    _attemptsCtrl.dispose();
    super.dispose();
  }

  int get _totalMarks => _questions.fold(0, (s, q) => s + q.marks);

  bool get _canSubmit =>
      !_submitting &&
      _titleCtrl.text.trim().isNotEmpty &&
      (int.tryParse(_priceCtrl.text.trim()) ?? 0) >= 1 &&
      _questions.isNotEmpty &&
      _questions.every((q) => q.isComplete);

  Future<void> _editQuestion({_QuestionDraft? existing, int? index}) async {
    final draft = existing == null
        ? _QuestionDraft()
        : _QuestionDraft(
            type: existing.type,
            text: existing.text,
            marks: existing.marks,
            negativeMarks: existing.negativeMarks,
            options: existing.options.map((o) => _OptionDraft(id: o.id, text: o.text)).toList(),
            correctIds: {...existing.correctIds},
          );

    final result = await showModalBottomSheet<_QuestionDraft>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _QuestionEditorSheet(draft: draft),
    );
    if (result == null || !mounted) return;
    setState(() {
      if (index != null) {
        _questions[index] = result;
      } else {
        _questions.add(result);
      }
    });
  }

  Future<void> _submit() async {
    if (!_canSubmit) return;
    final priceCoins = int.tryParse(_priceCtrl.text.trim()) ?? 0;
    final durationMinutes = int.tryParse(_durationCtrl.text.trim());
    final attemptsAllowed = int.tryParse(_attemptsCtrl.text.trim()) ?? 1;

    setState(() => _submitting = true);
    TestSeriesModel? created;
    try {
      created = await TestSeriesService.createSeries(
        title: _titleCtrl.text.trim(),
        description: _descCtrl.text.trim(),
        isPaid: true,
        priceCoins: priceCoins,
        durationMinutes: durationMinutes,
        attemptsAllowed: attemptsAllowed < 1 ? 1 : attemptsAllowed,
      );
      await TestSeriesService.questionsBulk(
        created.id,
        _questions.map((q) => q.toJson()).toList(),
      );
      final published = await TestSeriesService.publishSeries(created.id);
      if (!mounted) return;
      Navigator.pop(context, published);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Test series published.')),
      );
    } catch (e) {
      if (!mounted) return;
      final l10n = AppLocalizations.of(context)!;
      final msg = created == null
          ? tsErrorMessage(l10n, e)
          : '${tsErrorMessage(l10n, e)} Your series was saved as a draft — you can finish it from your profile later.';
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
      if (created != null) Navigator.pop(context, created);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Create Test Series'),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(kLsPad, 12, kLsPad, 120),
        children: [
          const LsSectionHead(title: 'Details', padding: EdgeInsets.only(bottom: 8)),
          TextField(
            controller: _titleCtrl,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(labelText: 'Title', hintText: 'e.g. NEET Physics — Mock Test 1'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _descCtrl,
            maxLines: 3,
            decoration: const InputDecoration(labelText: 'Description (optional)'),
          ),
          const SizedBox(height: 16),
          Row(children: [
            Expanded(
              child: TextField(
                controller: _priceCtrl,
                keyboardType: TextInputType.number,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                onChanged: (_) => setState(() {}),
                decoration: const InputDecoration(labelText: 'Price (coins)', helperText: 'Minimum 1'),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: TextField(
                controller: _durationCtrl,
                keyboardType: TextInputType.number,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                decoration: const InputDecoration(labelText: 'Duration (min)', helperText: 'Optional'),
              ),
            ),
          ]),
          const SizedBox(height: 12),
          TextField(
            controller: _attemptsCtrl,
            keyboardType: TextInputType.number,
            inputFormatters: [FilteringTextInputFormatter.digitsOnly],
            decoration: const InputDecoration(labelText: 'Attempts allowed'),
          ),
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              'Individual test series are paid by default (platform policy) — students spend coins to attempt it.',
              style: TextStyle(fontSize: 11.5, color: cs.onSurface.withOpacity(.6)),
            ),
          ),
          const SizedBox(height: 22),
          Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
            Expanded(
              child: LsSectionHead(
                title: 'Questions (${_questions.length}) · $_totalMarks marks',
                padding: EdgeInsets.zero,
              ),
            ),
            const SizedBox(width: 8),
            LsOutlineButton(label: 'Add', icon: Icons.add_rounded, onPressed: () => _editQuestion()),
          ]),
          const SizedBox(height: 10),
          if (_questions.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 24),
              child: Center(
                child: Text('No questions yet — add at least one to publish.',
                    style: TextStyle(color: cs.onSurface.withOpacity(.6), fontSize: 13)),
              ),
            )
          else
            for (int i = 0; i < _questions.length; i++) _questionTile(cs, i),
        ],
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 10),
          child: LsPrimaryButton(
            label: _submitting ? 'Publishing…' : 'Save & Publish',
            loading: _submitting,
            onPressed: _canSubmit ? _submit : null,
          ),
        ),
      ),
    );
  }

  Widget _questionTile(ColorScheme cs, int i) {
    final q = _questions[i];
    return LsCard(
      margin: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        contentPadding: EdgeInsets.zero,
        title: Text(
          q.text.trim().isEmpty ? '(Untitled question)' : q.text.trim(),
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5),
        ),
        subtitle: Text(
          '${q.typeLabel} · ${q.marks} mark${q.marks == 1 ? '' : 's'}'
          '${!q.isComplete ? ' · incomplete' : ''}',
          style: TextStyle(
            fontSize: 11.5,
            color: q.isComplete ? cs.onSurface.withOpacity(.6) : Colors.redAccent,
          ),
        ),
        trailing: Row(mainAxisSize: MainAxisSize.min, children: [
          IconButton(
            icon: const Icon(Icons.edit_outlined, size: 19),
            onPressed: () => _editQuestion(existing: q, index: i),
          ),
          IconButton(
            icon: const Icon(Icons.delete_outline_rounded, size: 19),
            onPressed: () => setState(() => _questions.removeAt(i)),
          ),
        ]),
      ),
    );
  }
}

// ============================================================
// Add / edit a single question — bottom sheet.
// ============================================================
class _QuestionEditorSheet extends StatefulWidget {
  final _QuestionDraft draft;
  const _QuestionEditorSheet({required this.draft});

  @override
  State<_QuestionEditorSheet> createState() => _QuestionEditorSheetState();
}

class _QuestionEditorSheetState extends State<_QuestionEditorSheet> {
  late _QuestionDraft _d = widget.draft;
  late final _textCtrl = TextEditingController(text: _d.text);
  late final _marksCtrl = TextEditingController(text: '${_d.marks}');
  late final _negCtrl = TextEditingController(text: '${_d.negativeMarks}');

  @override
  void dispose() {
    _textCtrl.dispose();
    _marksCtrl.dispose();
    _negCtrl.dispose();
    super.dispose();
  }

  String _nextOptionId() {
    const letters = 'abcdefghijklmnopqrstuvwxyz';
    for (final c in letters.split('')) {
      if (_d.options.every((o) => o.id != c)) return c;
    }
    return DateTime.now().millisecondsSinceEpoch.toString();
  }

  void _save() {
    _d.text = _textCtrl.text;
    _d.marks = int.tryParse(_marksCtrl.text.trim()) ?? 1;
    _d.negativeMarks = int.tryParse(_negCtrl.text.trim()) ?? 0;
    Navigator.pop(context, _d);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final mq = MediaQuery.of(context);
    return Padding(
      padding: EdgeInsets.only(bottom: mq.viewInsets.bottom),
      child: DraggableScrollableSheet(
        initialChildSize: 0.85,
        maxChildSize: 0.95,
        minChildSize: 0.5,
        expand: false,
        builder: (_, scrollCtrl) => Container(
          decoration: BoxDecoration(
            color: lsBg(context),
            borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
          ),
          child: ListView(
            controller: scrollCtrl,
            padding: const EdgeInsets.fromLTRB(kLsPad, 14, kLsPad, 28),
            children: [
              Center(
                child: Container(
                  width: 36,
                  height: 4,
                  margin: const EdgeInsets.only(bottom: 14),
                  decoration: BoxDecoration(color: cs.onSurface.withOpacity(.2), borderRadius: BorderRadius.circular(4)),
                ),
              ),
              Text('Question', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15, color: cs.onSurface)),
              const SizedBox(height: 12),
              SegmentedButton<_QType>(
                segments: const [
                  ButtonSegment(value: _QType.mcq, label: Text('Single choice')),
                  ButtonSegment(value: _QType.msq, label: Text('Multi-select')),
                  ButtonSegment(value: _QType.text, label: Text('Text')),
                ],
                selected: {_d.type},
                onSelectionChanged: (s) => setState(() => _d.type = s.first),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _textCtrl,
                maxLines: 3,
                decoration: const InputDecoration(labelText: 'Question text'),
              ),
              const SizedBox(height: 12),
              Row(children: [
                Expanded(
                  child: TextField(
                    controller: _marksCtrl,
                    keyboardType: TextInputType.number,
                    inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                    decoration: const InputDecoration(labelText: 'Marks'),
                  ),
                ),
                if (_d.type != _QType.text) ...[
                  const SizedBox(width: 12),
                  Expanded(
                    child: TextField(
                      controller: _negCtrl,
                      keyboardType: TextInputType.number,
                      inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                      decoration: const InputDecoration(labelText: 'Negative marks', helperText: 'On wrong answer'),
                    ),
                  ),
                ],
              ]),
              if (_d.type != _QType.text) ...[
                const SizedBox(height: 18),
                Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                  Text('Options', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: cs.onSurface)),
                  TextButton.icon(
                    onPressed: _d.options.length >= 8
                        ? null
                        : () => setState(() => _d.options.add(_OptionDraft(id: _nextOptionId()))),
                    icon: const Icon(Icons.add_rounded, size: 16),
                    label: const Text('Option'),
                  ),
                ]),
                Text(
                  _d.type == _QType.mcq
                      ? 'Tap the radio to mark the correct option.'
                      : 'Tick every correct option.',
                  style: TextStyle(fontSize: 11.5, color: cs.onSurface.withOpacity(.6)),
                ),
                const SizedBox(height: 4),
                for (int i = 0; i < _d.options.length; i++) _optionRow(cs, i),
              ],
              const SizedBox(height: 22),
              LsPrimaryButton(
                label: 'Save question',
                onPressed: () {
                  _d.text = _textCtrl.text;
                  _d.marks = int.tryParse(_marksCtrl.text.trim()) ?? 1;
                  _d.negativeMarks = int.tryParse(_negCtrl.text.trim()) ?? 0;
                  final tempCheck = _QuestionDraft(
                    type: _d.type,
                    text: _d.text,
                    marks: _d.marks,
                    negativeMarks: _d.negativeMarks,
                    options: _d.options,
                    correctIds: _d.correctIds,
                  );
                  if (!tempCheck.isComplete) {
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                      content: Text('Fill the question text, at least 2 options and mark the correct answer(s).'),
                    ));
                    return;
                  }
                  _save();
                },
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _optionRow(ColorScheme cs, int i) {
    final o = _d.options[i];
    final selected = _d.correctIds.contains(o.id);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(children: [
        _d.type == _QType.mcq
            ? Radio<String>(
                value: o.id,
                groupValue: _d.correctIds.isEmpty ? null : _d.correctIds.first,
                onChanged: (v) => setState(() => _d.correctIds = {if (v != null) v}),
              )
            : Checkbox(
                value: selected,
                onChanged: (v) => setState(() {
                  if (v == true) {
                    _d.correctIds.add(o.id);
                  } else {
                    _d.correctIds.remove(o.id);
                  }
                }),
              ),
        Expanded(
          child: TextField(
            controller: TextEditingController(text: o.text)
              ..selection = TextSelection.collapsed(offset: o.text.length),
            onChanged: (v) => o.text = v,
            decoration: InputDecoration(hintText: 'Option ${i + 1}', isDense: true),
          ),
        ),
        if (_d.options.length > 2)
          IconButton(
            icon: const Icon(Icons.close_rounded, size: 17),
            onPressed: () => setState(() {
              _d.correctIds.remove(o.id);
              _d.options.removeAt(i);
            }),
          ),
      ]),
    );
  }
}
