import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../services/assignment_models.dart';
import '../services/assignment_service.dart';

// ============================================================
// CREATE ASSIGNMENT
//
// This is the piece that was missing end-to-end: everything else in this
// module assumed an assignment already existed. `AssignmentService
// .createAssignment()` (`POST {mount}/assigmentss/`) is the *only* create
// entry point the backend exposes to a mobile client — campus/liveclass
// assignments are created by those apps' own bridge, not here.
//
// Because `assigmentsViewSet.perform_create()` hard-wires
// `source=personal`, and there's no roster for personal assignments
// (`get_queryset()` only ever returns them to their own poster, or to
// someone who already holds a submission for one), this is a genuine
// **self-assignment**: create it, then it shows up in your own Pending
// tab exactly like anything else you need to submit. There's no separate
// "assign to students" step to build here — the backend doesn't support
// one for this source.
//
// Attachment + structured (question-based) creation together are NOT
// supported: `assigmentsCreateSerializer.questions` is a nested list, and
// DRF has no way to parse a nested list out of multipart/form-data (the
// same limitation `submit_structured`'s own backend docstring calls out
// for `answers` — that endpoint just has a server-side JSON-string
// workaround written for it; this one doesn't). So the attachment picker
// is disabled the moment structured mode is switched on, rather than
// silently sending something the server can't parse.
// ============================================================

class CreateAssignmentScreen extends StatefulWidget {
  const CreateAssignmentScreen({super.key});

  @override
  State<CreateAssignmentScreen> createState() => _CreateAssignmentScreenState();
}

class _CreateAssignmentScreenState extends State<CreateAssignmentScreen> {
  final _titleCtrl = TextEditingController();
  final _descCtrl = TextEditingController();
  final _totalMarksCtrl = TextEditingController();

  DateTime? _dueDate;
  bool _structured = false;
  File? _attachment;
  bool _saving = false;
  int _idSeq = 0;

  final List<_QuestionDraft> _questions = [];

  @override
  void dispose() {
    _titleCtrl.dispose();
    _descCtrl.dispose();
    _totalMarksCtrl.dispose();
    for (final q in _questions) {
      q.dispose();
    }
    super.dispose();
  }

  String _newQuestionId() => 'q${_idSeq++}';

  void _addQuestion() => setState(() => _questions.add(_QuestionDraft(id: _newQuestionId())));

  void _removeQuestion(_QuestionDraft q) {
    setState(() {
      q.dispose();
      _questions.remove(q);
    });
  }

  Future<void> _pickAttachment() async {
    try {
      const all = XTypeGroup(label: 'all');
      final f = await openFile(acceptedTypeGroups: [all]);
      if (f != null && mounted) setState(() => _attachment = File(f.path));
    } catch (e) {
      if (mounted) lsSnack(context, AppLocalizations.of(context)!.assignmentPickFailed, error: true);
    }
  }

  Future<void> _pickDueDate() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: _dueDate ?? now,
      firstDate: now.subtract(const Duration(days: 1)),
      lastDate: now.add(const Duration(days: 365 * 3)),
    );
    if (picked != null && mounted) setState(() => _dueDate = picked);
  }

  /// Har cheez client-side yahan isliye check hoti hai taaki ek adhoori
  /// draft server tak jaake 400 se wapas aane ke bajaye yahin ruk jaaye —
  /// server apni taraf se `assigmentsCreateSerializer` me phir bhi apni
  /// validation zaroor karega, ye sirf ek fast first pass hai.
  String? _validate(AppLocalizations l10n) {
    if (_titleCtrl.text.trim().isEmpty) return l10n.assignmentCreateValidationTitle;
    if (!_structured) return null;
    if (_questions.isEmpty) return l10n.assignmentCreateValidationQuestion;
    for (final q in _questions) {
      if (q.textCtrl.text.trim().isEmpty) return l10n.assignmentCreateValidationQuestion;
      if ((int.tryParse(q.marksCtrl.text.trim()) ?? 0) <= 0) return l10n.assignmentCreateValidationQuestion;
      final needsOptions = q.type == AssignmentQuestionType.mcq ||
          q.type == AssignmentQuestionType.msq ||
          q.type == AssignmentQuestionType.list;
      if (!needsOptions) continue;

      // `_questionPayload` drops any option whose text was left blank, so
      // the correct-answer check below only counts against options that
      // will actually be sent — an option cleared out *after* being
      // marked correct must not slip through as a "valid" selection.
      final filledIds = q.options.where((o) => o.textCtrl.text.trim().isNotEmpty).map((o) => o.id).toSet();
      if (filledIds.length < 2) return l10n.assignmentCreateValidationOptions;

      if (q.type == AssignmentQuestionType.mcq &&
          (q.correctOptionId == null || !filledIds.contains(q.correctOptionId))) {
        return l10n.assignmentCreateValidationCorrect;
      }
      if (q.type == AssignmentQuestionType.msq &&
          (q.correctOptionIds.isEmpty || !filledIds.containsAll(q.correctOptionIds))) {
        return l10n.assignmentCreateValidationCorrect;
      }
    }
    return null;
  }

  Map<String, dynamic> _questionPayload(_QuestionDraft q, int order) {
    final opts = q.options
        .where((o) => o.textCtrl.text.trim().isNotEmpty)
        .map((o) => {'id': o.id, 'text': o.textCtrl.text.trim()})
        .toList();

    dynamic correct = {};
    switch (q.type) {
      case AssignmentQuestionType.mcq:
        correct = {'option_id': q.correctOptionId};
        break;
      case AssignmentQuestionType.msq:
        correct = {'option_ids': q.correctOptionIds.toList()};
        break;
      case AssignmentQuestionType.list:
        // Sequence = the order options currently sit in this list — see
        // `_QuestionEditor`'s up/down reordering. This app's own submit-
        // side UI (`assignment_detail_screen.dart`) only ever renders the
        // "order" sub-mode for `list` questions, never left/right
        // "match" pairs, so this create screen only builds that mode too
        // — a `match`-shaped question would have nowhere to be answered.
        correct = {'list_mode': 'order', 'sequence': opts.map((o) => o['id']).toList()};
        break;
      case AssignmentQuestionType.text:
      case AssignmentQuestionType.unknown:
        correct = {};
    }

    return {
      'order': order,
      'question_type': q.type.name,
      'text': q.textCtrl.text.trim(),
      'marks': int.tryParse(q.marksCtrl.text.trim()) ?? 0,
      'options': opts,
      'correct_answer': correct,
    };
  }

  Future<void> _submit() async {
    final l10n = AppLocalizations.of(context)!;
    final err = _validate(l10n);
    if (err != null) {
      lsSnack(context, err, error: true);
      return;
    }

    setState(() => _saving = true);
    try {
      await AssignmentService.createAssignment(
        title: _titleCtrl.text.trim(),
        description: _descCtrl.text.trim(),
        dueDate: _dueDate,
        hasStructuredQuestions: _structured,
        totalMarks: _structured ? null : int.tryParse(_totalMarksCtrl.text.trim()),
        attachment: _structured ? null : _attachment,
        questions: _structured
            ? [for (var i = 0; i < _questions.length; i++) _questionPayload(_questions[i], i)]
            : const [],
      );
      if (!mounted) return;
      lsSnack(context, l10n.assignmentCreateSuccess);
      Navigator.pop(context, true);
    } catch (e) {
      if (mounted) lsSnack(context, l10n.assignmentCreateFailed, error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: lsAppBar(context, title: l10n.assignmentCreateTitle),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(kLsPad, 14, kLsPad, 24),
        children: [
          LsCard(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(l10n.assignmentTitleLabel, style: LsType.head(context, size: 12.5)),
              const SizedBox(height: 6),
              TextField(controller: _titleCtrl, textInputAction: TextInputAction.next),
              const SizedBox(height: 14),
              Text(l10n.assignmentDescriptionLabel, style: LsType.head(context, size: 12.5)),
              const SizedBox(height: 6),
              TextField(
                controller: _descCtrl,
                minLines: 3,
                maxLines: 6,
                decoration: InputDecoration(hintText: l10n.assignmentDescriptionHint),
              ),
            ]),
          ),
          const SizedBox(height: 12),
          LsCard(
            child: Row(children: [
              Icon(Icons.event_outlined, size: 18, color: cs.primary),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  _dueDate == null ? l10n.assignmentNoDueDate : DateFormat.yMMMd().format(_dueDate!),
                  style: const TextStyle(fontSize: 13),
                ),
              ),
              if (_dueDate != null)
                IconButton(
                  icon: const Icon(Icons.close, size: 18),
                  tooltip: l10n.assignmentClearDueDate,
                  onPressed: () => setState(() => _dueDate = null),
                ),
              LsOutlineButton(label: l10n.assignmentPickDueDate, onPressed: _pickDueDate),
            ]),
          ),
          const SizedBox(height: 12),
          LsCard(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(child: Text(l10n.assignmentStructuredToggleLabel, style: LsType.head(context, size: 13))),
                Switch(
                  value: _structured,
                  onChanged: (v) {
                    HapticFeedback.selectionClick();
                    setState(() {
                      _structured = v;
                      // Nested-multipart limitation — see file header.
                      if (v) _attachment = null;
                    });
                  },
                ),
              ]),
              const SizedBox(height: 2),
              Text(
                l10n.assignmentStructuredToggleHint,
                style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
              ),
            ]),
          ),
          const SizedBox(height: 12),
          if (!_structured) ...[
            LsCard(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(l10n.assignmentTotalMarksLabel, style: LsType.head(context, size: 12.5)),
                const SizedBox(height: 6),
                TextField(
                  controller: _totalMarksCtrl,
                  keyboardType: TextInputType.number,
                  decoration: InputDecoration(hintText: l10n.assignmentTotalMarksHint),
                ),
              ]),
            ),
            const SizedBox(height: 12),
            LsCard(
              child: Row(children: [
                Icon(Icons.attach_file_rounded, size: 18, color: cs.primary),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    _attachment == null ? l10n.assignmentAttachFile : _attachment!.path.split('/').last,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 13),
                  ),
                ),
                if (_attachment != null)
                  IconButton(
                    icon: const Icon(Icons.close, size: 18),
                    tooltip: l10n.assignmentRemoveFile,
                    onPressed: () => setState(() => _attachment = null),
                  ),
                LsOutlineButton(
                  label: l10n.assignmentAttachFile,
                  icon: Icons.upload_file_outlined,
                  onPressed: _pickAttachment,
                ),
              ]),
            ),
          ] else ...[
            LsSectionHead(title: l10n.assignmentQuestionsLabel, padding: EdgeInsets.zero),
            const SizedBox(height: 10),
            for (var i = 0; i < _questions.length; i++)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: _QuestionEditor(
                  key: ValueKey(_questions[i].id),
                  draft: _questions[i],
                  index: i,
                  onRemove: () => _removeQuestion(_questions[i]),
                ),
              ),
            Align(
              alignment: Alignment.centerLeft,
              child: LsOutlineButton(label: l10n.assignmentAddQuestion, icon: Icons.add, onPressed: _addQuestion),
            ),
          ],
        ],
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 10),
          child: LsPrimaryButton(
            label: l10n.assignmentCreateTitle,
            icon: Icons.check_rounded,
            loading: _saving,
            onPressed: _saving ? null : _submit,
          ),
        ),
      ),
    );
  }
}

class _OptionDraft {
  final String id;
  final TextEditingController textCtrl = TextEditingController();
  _OptionDraft(this.id);
  void dispose() => textCtrl.dispose();
}

class _QuestionDraft {
  final String id;
  AssignmentQuestionType type;
  final TextEditingController textCtrl = TextEditingController();
  final TextEditingController marksCtrl = TextEditingController(text: '1');
  final List<_OptionDraft> options = [];
  String? correctOptionId; // mcq
  final Set<String> correctOptionIds = {}; // msq
  int _optSeq = 0;

  _QuestionDraft({required this.id, this.type = AssignmentQuestionType.text});

  String nextOptionId() => '${id}_o${_optSeq++}';

  void dispose() {
    textCtrl.dispose();
    marksCtrl.dispose();
    for (final o in options) {
      o.dispose();
    }
  }
}

class _QuestionEditor extends StatefulWidget {
  final _QuestionDraft draft;
  final int index;
  final VoidCallback onRemove;
  const _QuestionEditor({super.key, required this.draft, required this.index, required this.onRemove});

  @override
  State<_QuestionEditor> createState() => _QuestionEditorState();
}

class _QuestionEditorState extends State<_QuestionEditor> {
  _QuestionDraft get d => widget.draft;

  void _addOption() => setState(() => d.options.add(_OptionDraft(d.nextOptionId())));

  void _removeOption(_OptionDraft o) {
    setState(() {
      o.dispose();
      d.options.remove(o);
      d.correctOptionIds.remove(o.id);
      if (d.correctOptionId == o.id) d.correctOptionId = null;
    });
  }

  void _moveOption(int from, int to) {
    setState(() {
      final o = d.options.removeAt(from);
      d.options.insert(to, o);
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    final needsOptions = d.type == AssignmentQuestionType.mcq ||
        d.type == AssignmentQuestionType.msq ||
        d.type == AssignmentQuestionType.list;

    return LsCard(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(l10n.questionShort(widget.index + 1), style: LsType.head(context, size: 13)),
          const Spacer(),
          IconButton(
            icon: Icon(Icons.delete_outline, size: 19, color: cs.error),
            tooltip: l10n.assignmentRemoveQuestion,
            onPressed: widget.onRemove,
          ),
        ]),
        DropdownButtonFormField<AssignmentQuestionType>(
          value: d.type,
          decoration: InputDecoration(labelText: l10n.assignmentQuestionTypeLabel),
          items: [
            DropdownMenuItem(value: AssignmentQuestionType.text, child: Text(l10n.assignmentQuestionTypeText)),
            DropdownMenuItem(value: AssignmentQuestionType.mcq, child: Text(l10n.assignmentQuestionTypeMcq)),
            DropdownMenuItem(value: AssignmentQuestionType.msq, child: Text(l10n.assignmentQuestionTypeMsq)),
            DropdownMenuItem(value: AssignmentQuestionType.list, child: Text(l10n.assignmentQuestionTypeList)),
          ],
          onChanged: (v) {
            if (v == null) return;
            setState(() {
              d.type = v;
              // Type badalne pe purani correct-answer selection ka koi
              // matlab nahi rehta — clear kar dete hain taaki galti se
              // stale selection submit na ho jaaye.
              d.correctOptionId = null;
              d.correctOptionIds.clear();
            });
          },
        ),
        const SizedBox(height: 10),
        TextField(
          controller: d.textCtrl,
          minLines: 1,
          maxLines: 3,
          decoration: InputDecoration(hintText: l10n.assignmentQuestionTextHint),
        ),
        const SizedBox(height: 10),
        SizedBox(
          width: 100,
          child: TextField(
            controller: d.marksCtrl,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(labelText: l10n.assignmentQuestionMarksLabel),
          ),
        ),
        if (needsOptions) ...[
          const SizedBox(height: 12),
          Text(
            d.type == AssignmentQuestionType.mcq
                ? l10n.answerHintSelectOne
                : d.type == AssignmentQuestionType.msq
                    ? l10n.answerHintSelectMultiple
                    : l10n.answerHintArrange,
            style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant),
          ),
          const SizedBox(height: 6),
          for (var i = 0; i < d.options.length; i++)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(children: [
                if (d.type == AssignmentQuestionType.mcq)
                  Radio<String>(
                    value: d.options[i].id,
                    groupValue: d.correctOptionId,
                    onChanged: (v) => setState(() => d.correctOptionId = v),
                  )
                else if (d.type == AssignmentQuestionType.msq)
                  Checkbox(
                    value: d.correctOptionIds.contains(d.options[i].id),
                    onChanged: (v) => setState(() {
                      if (v == true) {
                        d.correctOptionIds.add(d.options[i].id);
                      } else {
                        d.correctOptionIds.remove(d.options[i].id);
                      }
                    }),
                  )
                else ...[
                  IconButton(
                    icon: const Icon(Icons.arrow_upward_rounded, size: 16),
                    onPressed: i == 0 ? null : () => _moveOption(i, i - 1),
                  ),
                  IconButton(
                    icon: const Icon(Icons.arrow_downward_rounded, size: 16),
                    onPressed: i == d.options.length - 1 ? null : () => _moveOption(i, i + 1),
                  ),
                ],
                Expanded(
                  child: TextField(
                    controller: d.options[i].textCtrl,
                    decoration: InputDecoration(hintText: l10n.assignmentOptionHint),
                  ),
                ),
                IconButton(
                  icon: Icon(Icons.close, size: 16, color: cs.onSurfaceVariant),
                  tooltip: l10n.assignmentRemoveOption,
                  onPressed: () => _removeOption(d.options[i]),
                ),
              ]),
            ),
          Align(
            alignment: Alignment.centerLeft,
            child: LsOutlineButton(label: l10n.assignmentAddOption, icon: Icons.add, onPressed: _addOption),
          ),
        ],
      ]),
    );
  }
}
