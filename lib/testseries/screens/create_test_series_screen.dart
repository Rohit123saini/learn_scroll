import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import '../utils/ts_error_text.dart';
import '../widgets/question_editor_sheet.dart';

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

  final List<QuestionDraft> _questions = [];
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

  Future<void> _editQuestion({QuestionDraft? existing, int? index}) async {
    final draft = existing == null ? QuestionDraft() : existing.copy();

    final result = await showModalBottomSheet<QuestionDraft>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => QuestionEditorSheet(draft: draft),
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
          IconButton(tooltip: 'Edit', 
            icon: const Icon(Icons.edit_outlined, size: 19),
            onPressed: () => _editQuestion(existing: q, index: i),
          ),
          IconButton(tooltip: 'Delete', 
            icon: const Icon(Icons.delete_outline_rounded, size: 19),
            onPressed: () => setState(() => _questions.removeAt(i)),
          ),
        ]),
      ),
    );
  }
}

