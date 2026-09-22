import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// REPORT CARD — student's own view
//
// `GET /results/report-card/?enrollment=&exam_term=` — `404` agar id
// resolve nahi ya requester authorized nahi (§19). Student sirf apna
// `enrollment.id` bhejta hai, backend khud confirm karta hai ki ye unhi ka
// hai.
// ============================================================

class ReportCardScreen extends StatefulWidget {
  final StudentEnrollment enrollment;
  final List<Subject> subjects;
  final String? sessionId;

  const ReportCardScreen({
    super.key,
    required this.enrollment,
    required this.subjects,
    this.sessionId,
  });

  @override
  State<ReportCardScreen> createState() => _ReportCardScreenState();
}

class _ReportCardScreenState extends State<ReportCardScreen> {
  List<ExamTerm> _examTerms = const [];
  String? _examTermId;
  bool _loadingTerms = true;

  bool _loadingCard = false;
  String? _error;
  ReportCard? _card;

  @override
  void initState() {
    super.initState();
    _loadTerms();
  }

  Future<void> _loadTerms() async {
    final sessionId = widget.sessionId;
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
      if (_examTermId != null) _loadCard();
    } catch (_) {
      if (mounted) setState(() => _loadingTerms = false);
    }
  }

  Future<void> _loadCard() async {
    if (_examTermId == null) return;
    setState(() {
      _loadingCard = true;
      _error = null;
      _card = null;
    });
    try {
      final card = await CampusService.reportCard(
        enrollmentId: widget.enrollment.id,
        examTermId: _examTermId!,
      );
      if (!mounted) return;
      setState(() {
        _card = card;
        _loadingCard = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loadingCard = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: lsAppBar(context, title: l10n.reportCardTitle),
      body: ListView(
        padding: const EdgeInsets.all(14),
        children: [
          if (_loadingTerms)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 30),
              child: Center(child: CircularProgressIndicator()),
            )
          else if (_examTerms.isEmpty)
            EmptyStateWidget(icon: Icons.fact_check_outlined, title: l10n.setupExamTermsEmpty)
          else ...[
            DropdownButtonFormField<String>(
              value: _examTermId,
              isExpanded: true,
              items: _examTerms.map((t) => DropdownMenuItem(value: t.id, child: Text(t.name))).toList(),
              decoration:
                  InputDecoration(labelText: l10n.examTermPickLabel, border: const OutlineInputBorder()),
              onChanged: (v) {
                setState(() => _examTermId = v);
                _loadCard();
              },
            ),
            const SizedBox(height: 16),
            if (_loadingCard)
              const Center(child: CircularProgressIndicator())
            else if (_error != null)
              ErrorStateWidget(
                title: l10n.setupLoadFailed,
                subtitle: _error,
                retryLabel: l10n.retry,
                onRetry: _loadCard,
              )
            else if (_card != null) ...[
              Row(children: [
                Expanded(
                  child: LsScoreTile(
                    value: _card!.totalObtained.toStringAsFixed(0),
                    label: l10n.resultObtainedLabel,
                    color: cs.primary,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: LsScoreTile(
                    value: '${_card!.percentage.toStringAsFixed(1)}%',
                    label: l10n.reportCardPercentage,
                    color: _card!.percentage >= 40 ? Colors.green : cs.error,
                  ),
                ),
              ]),
              const SizedBox(height: 16),
              for (final row in _card!.subjects)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: LsCard(
                    child: Row(children: [
                      Expanded(
                        child: Text(row.subjectName,
                            style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                      ),
                      Text('${row.marksObtained.toStringAsFixed(0)} / ${row.maxMarks.toStringAsFixed(0)}',
                          style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: cs.primary)),
                    ]),
                  ),
                ),
            ],
          ],
        ],
      ),
    );
  }
}
