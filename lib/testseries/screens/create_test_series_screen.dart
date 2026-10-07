import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';

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
//
// Task 8 (question types): ab true/false, fill-in-the-blank, numeric aur
// option-image bhi banaye ja sakte hain. Naye strings l10n (EN+HI) me hain.
// Option images series banne ke baad upload hoti hain (endpoint series id
// maangta hai), phir URL question JSON me jaata hai.
// ============================================================

enum _QType { mcq, msq, trueFalse, fillBlank, numeric, text }

class _OptionDraft {
  String id;
  String text;

  /// Image abhi tak upload nahi hui (series draft banne ke baad upload hoti hai).
  File? imageFile;

  /// Upload ho chuki image ka URL (publish flow me set hota hai).
  String? imageUrl;

  _OptionDraft({required this.id, this.text = '', this.imageFile, this.imageUrl});

  bool get hasImage => imageFile != null || (imageUrl != null && imageUrl!.isNotEmpty);
  bool get isFilled => text.trim().isNotEmpty || hasImage;

  _OptionDraft copy() => _OptionDraft(id: id, text: text, imageFile: imageFile, imageUrl: imageUrl);
}

class _QuestionDraft {
  _QType type;
  String text;
  int marks;
  int negativeMarks;
  List<_OptionDraft> options;
  Set<String> correctIds;

  // true_false / fill_blank / numeric (Task 8)
  bool? tfAnswer;
  String acceptedAnswers; // ek line = ek accepted answer
  bool caseSensitive;
  String numericValue;
  String tolerance;

  _QuestionDraft({
    this.type = _QType.mcq,
    this.text = '',
    this.marks = 1,
    this.negativeMarks = 0,
    List<_OptionDraft>? options,
    Set<String>? correctIds,
    this.tfAnswer,
    this.acceptedAnswers = '',
    this.caseSensitive = false,
    this.numericValue = '',
    this.tolerance = '',
  })  : options = options ?? [_OptionDraft(id: 'a'), _OptionDraft(id: 'b')],
        correctIds = correctIds ?? {};

  _QuestionDraft copy() => _QuestionDraft(
        type: type,
        text: text,
        marks: marks,
        negativeMarks: negativeMarks,
        options: options.map((o) => o.copy()).toList(),
        correctIds: {...correctIds},
        tfAnswer: tfAnswer,
        acceptedAnswers: acceptedAnswers,
        caseSensitive: caseSensitive,
        numericValue: numericValue,
        tolerance: tolerance,
      );

  bool get isOptionType => type == _QType.mcq || type == _QType.msq;
  bool get isAutoGraded => type != _QType.text;

  List<String> get acceptedList =>
      acceptedAnswers.split('\n').map((e) => e.trim()).where((e) => e.isNotEmpty).toList();

  double? get _numeric => double.tryParse(numericValue.trim().replaceAll(',', ''));
  double? get _tolerance =>
      tolerance.trim().isEmpty ? 0.0 : double.tryParse(tolerance.trim().replaceAll(',', ''));

  bool get isComplete {
    if (text.trim().isEmpty || marks < 1) return false;
    if (isAutoGraded && negativeMarks > marks) return false; // backend bhi reject karta hai
    switch (type) {
      case _QType.text:
        return true;
      case _QType.trueFalse:
        return tfAnswer != null;
      case _QType.fillBlank:
        return acceptedList.isNotEmpty;
      case _QType.numeric:
        final n = _numeric;
        final t = _tolerance;
        return n != null && n.isFinite && t != null && t >= 0;
      case _QType.mcq:
      case _QType.msq:
        final filledIds = options.where((o) => o.isFilled).map((o) => o.id).toSet();
        if (filledIds.length < 2) return false;
        final valid = correctIds.where(filledIds.contains);
        return type == _QType.mcq ? valid.length == 1 : valid.isNotEmpty;
    }
  }

  String typeLabel(AppLocalizations l10n) => switch (type) {
        _QType.text => l10n.tsTypeText,
        _QType.mcq => l10n.tsTypeMcq,
        _QType.msq => l10n.tsTypeMsq,
        _QType.trueFalse => l10n.tsTypeTrueFalse,
        _QType.fillBlank => l10n.tsTypeFillBlank,
        _QType.numeric => l10n.tsTypeNumeric,
      };

  Map<String, dynamic> toJson() {
    final base = <String, dynamic>{
      'text': text.trim(),
      'marks': marks,
      'negative_marks': isAutoGraded ? negativeMarks : 0,
    };
    switch (type) {
      case _QType.text:
        return {...base, 'question_type': 'text', 'options': const [], 'correct_answer': const {}};
      case _QType.trueFalse:
        return {
          ...base,
          'question_type': 'true_false',
          'options': const [],
          'correct_answer': {'value': tfAnswer == true},
        };
      case _QType.fillBlank:
        return {
          ...base,
          'question_type': 'fill_blank',
          'options': const [],
          'correct_answer': {'answers': acceptedList, 'case_sensitive': caseSensitive},
        };
      case _QType.numeric:
        return {
          ...base,
          'question_type': 'numeric',
          'options': const [],
          'correct_answer': {'value': _numeric, 'tolerance': _tolerance ?? 0},
        };
      case _QType.mcq:
      case _QType.msq:
        final filled = options.where((o) => o.isFilled).toList();
        final opts = filled.map((o) {
          final m = <String, dynamic>{'id': o.id, 'text': o.text.trim()};
          if (o.imageUrl != null && o.imageUrl!.isNotEmpty) m['image'] = o.imageUrl;
          return m;
        }).toList();
        final validIds = opts.map((o) => o['id']).toSet();
        final correct = correctIds.where(validIds.contains).toList();
        return {
          ...base,
          'question_type': type == _QType.mcq ? 'mcq' : 'msq',
          'options': opts,
          'correct_answer': type == _QType.mcq
              ? {'option_id': correct.isNotEmpty ? correct.first : null}
              : {'option_ids': correct},
        };
    }
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
    final draft = existing == null ? _QuestionDraft() : existing.copy();

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

  /// Option images series banne ke baad upload hoti hain; fail hui to poora
  /// publish ruk jaata hai (series draft rehti hai) — adhoori image ke saath
  /// question kabhi nahi jaata.
  Future<void> _uploadPendingOptionImages(String seriesId) async {
    for (final q in _questions) {
      if (!q.isOptionType) continue;
      for (final o in q.options) {
        final f = o.imageFile;
        if (f == null || (o.imageUrl != null && o.imageUrl!.isNotEmpty)) continue;
        o.imageUrl = await TestSeriesService.uploadOptionImage(seriesId, f);
      }
    }
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
      await _uploadPendingOptionImages(created.id);
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
          '${q.typeLabel(AppLocalizations.of(context)!)} · ${q.marks} mark${q.marks == 1 ? '' : 's'}'
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
  late final _acceptedCtrl = TextEditingController(text: _d.acceptedAnswers);
  late final _numberCtrl = TextEditingController(text: _d.numericValue);
  late final _tolCtrl = TextEditingController(text: _d.tolerance);

  @override
  void dispose() {
    _textCtrl.dispose();
    _marksCtrl.dispose();
    _negCtrl.dispose();
    _acceptedCtrl.dispose();
    _numberCtrl.dispose();
    _tolCtrl.dispose();
    super.dispose();
  }

  String _nextOptionId() {
    const letters = 'abcdefghijklmnopqrstuvwxyz';
    for (final c in letters.split('')) {
      if (_d.options.every((o) => o.id != c)) return c;
    }
    return DateTime.now().millisecondsSinceEpoch.toString();
  }

  /// Controllers ki values draft me likh do.
  void _syncDraft() {
    _d.text = _textCtrl.text;
    _d.marks = int.tryParse(_marksCtrl.text.trim()) ?? 1;
    _d.negativeMarks = int.tryParse(_negCtrl.text.trim()) ?? 0;
    _d.acceptedAnswers = _acceptedCtrl.text;
    _d.numericValue = _numberCtrl.text;
    _d.tolerance = _tolCtrl.text;
  }

  void _save() {
    final l10n = AppLocalizations.of(context)!;
    _syncDraft();
    if (!_d.isComplete) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.tsQuestionIncomplete)));
      return;
    }
    Navigator.pop(context, _d);
  }

  Future<void> _pickOptionImage(_OptionDraft o) async {
    final img = await ImagePicker().pickImage(source: ImageSource.gallery, maxWidth: 1280, imageQuality: 85);
    if (img == null || !mounted) return;
    setState(() {
      o.imageFile = File(img.path);
      o.imageUrl = null; // nayi image -> publish pe upload hogi
    });
  }

  String _typeLabel(AppLocalizations l10n, _QType t) => switch (t) {
        _QType.mcq => l10n.tsTypeMcq,
        _QType.msq => l10n.tsTypeMsq,
        _QType.trueFalse => l10n.tsTypeTrueFalse,
        _QType.fillBlank => l10n.tsTypeFillBlank,
        _QType.numeric => l10n.tsTypeNumeric,
        _QType.text => l10n.tsTypeText,
      };

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
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
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  for (final t in _QType.values)
                    ChoiceChip(
                      label: Text(_typeLabel(l10n, t)),
                      selected: _d.type == t,
                      onSelected: (_) => setState(() {
                        if (_d.type == t) return;
                        _d.type = t;
                        // Single-answer type me ek se zyada correct nahi reh sakte.
                        if (t == _QType.mcq && _d.correctIds.length > 1) {
                          _d.correctIds = {_d.correctIds.first};
                        }
                      }),
                    ),
                ],
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
                if (_d.isAutoGraded) ...[
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
              ..._answerSection(cs, l10n),
              const SizedBox(height: 22),
              LsPrimaryButton(label: 'Save question', onPressed: _save),
            ],
          ),
        ),
      ),
    );
  }

  List<Widget> _answerSection(ColorScheme cs, AppLocalizations l10n) {
    switch (_d.type) {
      case _QType.text:
        return const [];

      case _QType.trueFalse:
        return [
          const SizedBox(height: 18),
          Text(l10n.tsBuilderCorrectAnswer,
              style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: cs.onSurface)),
          const SizedBox(height: 8),
          SegmentedButton<bool>(
            emptySelectionAllowed: true,
            segments: [
              ButtonSegment(value: true, label: Text(l10n.tsTrue)),
              ButtonSegment(value: false, label: Text(l10n.tsFalse)),
            ],
            selected: {if (_d.tfAnswer != null) _d.tfAnswer!},
            onSelectionChanged: (s) => setState(() => _d.tfAnswer = s.isEmpty ? null : s.first),
          ),
        ];

      case _QType.fillBlank:
        return [
          const SizedBox(height: 18),
          TextField(
            controller: _acceptedCtrl,
            minLines: 2,
            maxLines: 6,
            textCapitalization: TextCapitalization.none,
            decoration: InputDecoration(
              labelText: l10n.tsAcceptedAnswers,
              helperText: l10n.tsAcceptedAnswersHelper,
              helperMaxLines: 2,
            ),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: _d.caseSensitive,
            title: Text(l10n.tsCaseSensitive, style: const TextStyle(fontSize: 13.5)),
            onChanged: (v) => setState(() => _d.caseSensitive = v),
          ),
        ];

      case _QType.numeric:
        const numFormat = r'[0-9.,\-]';
        return [
          const SizedBox(height: 18),
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(
              child: TextField(
                controller: _numberCtrl,
                keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
                inputFormatters: [FilteringTextInputFormatter.allow(RegExp(numFormat))],
                decoration: InputDecoration(labelText: l10n.tsCorrectNumber),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: TextField(
                controller: _tolCtrl,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9.,]'))],
                decoration: InputDecoration(
                  labelText: l10n.tsTolerance,
                  helperText: l10n.tsToleranceHelper,
                  helperMaxLines: 2,
                ),
              ),
            ),
          ]),
        ];

      case _QType.mcq:
      case _QType.msq:
        return [
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
            _d.type == _QType.mcq ? 'Tap the radio to mark the correct option.' : 'Tick every correct option.',
            style: TextStyle(fontSize: 11.5, color: cs.onSurface.withOpacity(.6)),
          ),
          const SizedBox(height: 4),
          for (int i = 0; i < _d.options.length; i++) _optionRow(cs, l10n, i),
        ];
    }
  }

  Widget _optionRow(ColorScheme cs, AppLocalizations l10n, int i) {
    final o = _d.options[i];
    final selected = _d.correctIds.contains(o.id);
    return Padding(
      key: ValueKey('opt_${o.id}'),
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
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
            child: _OptionTextField(
              initial: o.text,
              hint: l10n.tsOptionHint(i + 1),
              onChanged: (v) => o.text = v,
            ),
          ),
          IconButton(
            tooltip: o.hasImage ? l10n.tsRemoveOptionImage : l10n.tsAddOptionImage,
            icon: Icon(o.hasImage ? Icons.hide_image_outlined : Icons.add_photo_alternate_outlined, size: 19),
            onPressed: o.hasImage
                ? () => setState(() {
                      o.imageFile = null;
                      o.imageUrl = null;
                    })
                : () => _pickOptionImage(o),
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
        if (o.imageFile != null)
          Padding(
            padding: const EdgeInsets.only(left: 48, top: 4, bottom: 4),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(10),
              child: Image.file(o.imageFile!,
                  height: 90, fit: BoxFit.cover, cacheHeight: 270,
                  semanticLabel: l10n.tsOptionImage,
                  errorBuilder: (_, __, ___) => Icon(Icons.broken_image_outlined, color: cs.onSurfaceVariant)),
            ),
          ),
      ]),
    );
  }
}

/// Option ka text field — apna controller rakhta hai taaki har rebuild pe
/// cursor reset na ho (pehle har build pe naya controller ban jaata tha).
class _OptionTextField extends StatefulWidget {
  final String initial;
  final String hint;
  final ValueChanged<String> onChanged;
  const _OptionTextField({required this.initial, required this.hint, required this.onChanged});

  @override
  State<_OptionTextField> createState() => _OptionTextFieldState();
}

class _OptionTextFieldState extends State<_OptionTextField> {
  late final TextEditingController _c = TextEditingController(text: widget.initial);

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: _c,
      onChanged: widget.onChanged,
      decoration: InputDecoration(hintText: widget.hint, isDense: true),
    );
  }
}
