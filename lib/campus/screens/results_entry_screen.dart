import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// RESULTS ENTRY — one section, one subject, one exam term at a time
//
// `ResultEntry` ka apna `section` field nahi hai (sirf `enrollment`/
// `subject`/`exam_term`, §19), isliye "is section ke saare results" ek
// single GET se nahi aata — hum `enrollments(section)` se roster lete hain,
// aur `results(subject, exam_term)` se already-entered rows, phir dono ko
// `enrollmentId` se match karte hain. Jo match nahi hota, uske liye input
// field khula rehta hai.
//
// ⚠️ Sirf CREATE support hai (koi PATCH contract §19 me nahi diya) — isliye
// ek baar result save hone ke baad ye screen usse read-only dikhati hai,
// dobara edit karne ka koi button nahi (backend khud allow karta hai ya
// nahi, confirm nahi hai — galat guess se behtar hai edit na dena).
// ============================================================

class ResultsEntryScreen extends StatefulWidget {
  final String sectionId;
  final String sectionLabel;
  final List<Subject> subjects;
  final CampusAccess access;

  const ResultsEntryScreen({
    super.key,
    required this.sectionId,
    required this.sectionLabel,
    required this.subjects,
    required this.access,
  });

  @override
  State<ResultsEntryScreen> createState() => _ResultsEntryScreenState();
}

class _ResultsEntryScreenState extends State<ResultsEntryScreen> {
  List<ExamTerm> _examTerms = const [];
  String? _examTermId;
  String? _subjectId;
  bool _loadingTerms = true;

  bool _loadingRows = false;
  String? _error;
  List<StudentEnrollment> _enrollments = const [];
  Map<String, ResultEntry> _resultsByEnrollment = const {};

  @override
  void initState() {
    super.initState();
    _subjectId = widget.subjects.isNotEmpty ? widget.subjects.first.id : null;
    _loadTerms();
  }

  Future<void> _loadTerms() async {
    final sessionId = widget.access.currentSession?.id;
    if (sessionId == null) {
      setState(() => _loadingTerms = false);
      return;
    }
    try {
      final terms = await CampusService.examTerms(sessionId);
      if (!mounted) return;
      setState(() {
        _examTerms = terms;
        _examTermId = terms.isNotEmpty ? terms.first.id : null;
        _loadingTerms = false;
      });
      if (_examTermId != null && _subjectId != null) _loadRows();
    } catch (_) {
      if (mounted) setState(() => _loadingTerms = false);
    }
  }

  Future<void> _loadRows() async {
    if (_examTermId == null || _subjectId == null) return;
    setState(() {
      _loadingRows = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        CampusService.enrollments(sectionId: widget.sectionId),
        CampusService.results(subjectId: _subjectId, examTermId: _examTermId),
      ]);
      if (!mounted) return;
      final enrollments = (results[0] as List<StudentEnrollment>).where((e) => e.isActive).toList();
      final resultRows = results[1] as List<ResultEntry>;
      setState(() {
        _enrollments = enrollments;
        _resultsByEnrollment = {for (final r in resultRows) r.enrollmentId: r};
        _loadingRows = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loadingRows = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: lsAppBar(context, title: '${l10n.resultsTitle} — ${widget.sectionLabel}'),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 14, 14, 6),
          child: Row(children: [
            Expanded(
              child: DropdownButtonFormField<String>(
                value: _examTermId,
                isExpanded: true,
                items: _examTerms.map((t) => DropdownMenuItem(value: t.id, child: Text(t.name))).toList(),
                decoration:
                    InputDecoration(labelText: l10n.examTermPickLabel, border: const OutlineInputBorder()),
                onChanged: _loadingTerms
                    ? null
                    : (v) {
                        setState(() => _examTermId = v);
                        _loadRows();
                      },
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: DropdownButtonFormField<String>(
                value: _subjectId,
                isExpanded: true,
                items:
                    widget.subjects.map((s) => DropdownMenuItem(value: s.id, child: Text(s.label))).toList(),
                decoration:
                    InputDecoration(labelText: l10n.subjectPickLabel, border: const OutlineInputBorder()),
                onChanged: (v) {
                  setState(() => _subjectId = v);
                  _loadRows();
                },
              ),
            ),
          ]),
        ),
        Expanded(child: _body(l10n, cs)),
      ]),
    );
  }

  Widget _body(AppLocalizations l10n, ColorScheme cs) {
    if (_loadingTerms) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_examTerms.isEmpty) {
      return EmptyStateWidget(icon: Icons.fact_check_outlined, title: l10n.setupExamTermsEmpty);
    }
    if (_loadingRows) {
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
          onRetry: _loadRows,
        ),
      ]);
    }
    if (_enrollments.isEmpty) {
      return EmptyStateWidget(icon: Icons.groups_2_outlined, title: l10n.enrollmentsEmpty);
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(14, 6, 14, 40),
      itemCount: _enrollments.length,
      itemBuilder: (_, i) {
        final enrollment = _enrollments[i];
        final existing = _resultsByEnrollment[enrollment.id];
        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: _ResultRow(
            enrollment: enrollment,
            existing: existing,
            subjectId: _subjectId!,
            examTermId: _examTermId!,
            onSaved: () => setState(() {}),
          ),
        );
      },
    );
  }
}

class _ResultRow extends StatefulWidget {
  final StudentEnrollment enrollment;
  final ResultEntry? existing;
  final String subjectId;
  final String examTermId;
  final VoidCallback onSaved;

  const _ResultRow({
    required this.enrollment,
    required this.subjectId,
    required this.examTermId,
    required this.onSaved,
    this.existing,
  });

  @override
  State<_ResultRow> createState() => _ResultRowState();
}

class _ResultRowState extends State<_ResultRow> {
  final _obtained = TextEditingController();
  final _max = TextEditingController(text: '100');
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _obtained.dispose();
    _max.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final obtained = double.tryParse(_obtained.text.trim());
    final max = double.tryParse(_max.text.trim());
    final l10n = AppLocalizations.of(context)!;
    if (obtained == null || max == null) {
      setState(() => _error = l10n.resultMarksInvalid);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.createResult(
        enrollmentId: widget.enrollment.id,
        subjectId: widget.subjectId,
        examTermId: widget.examTermId,
        marksObtained: obtained,
        maxMarks: max,
      );
      widget.onSaved();
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
    final existing = widget.existing;
    return LsCard(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(
            child: Text(widget.enrollment.student?.displayName ?? widget.enrollment.studentId,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
          ),
          if (widget.enrollment.rollNumber.isNotEmpty)
            LsStatusChip(label: '#${widget.enrollment.rollNumber}', color: cs.primary),
        ]),
        const SizedBox(height: 8),
        if (existing != null)
          LsStatusChip(
            label: '${existing.marksObtained.toStringAsFixed(0)} / ${existing.maxMarks.toStringAsFixed(0)}',
            color: Colors.green,
            solid: true,
          )
        else
          Row(children: [
            Expanded(
              child: TextField(
                controller: _obtained,
                enabled: !_saving,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                    labelText: l10n.resultObtainedLabel, isDense: true, border: const OutlineInputBorder()),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextField(
                controller: _max,
                enabled: !_saving,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                    labelText: l10n.resultMaxLabel, isDense: true, border: const OutlineInputBorder()),
              ),
            ),
            const SizedBox(width: 8),
            _saving
                ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                : IconButton.filledTonal(
                    onPressed: _save,
                    icon: const Icon(Icons.check_rounded, size: 18),
                  ),
          ]),
        if (_error != null) ...[
          const SizedBox(height: 6),
          Text(_error!, style: TextStyle(color: cs.error, fontSize: 12)),
        ],
      ]),
    );
  }
}
