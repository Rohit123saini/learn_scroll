// ============================================================
// LIVECLASS — CERTIFICATES & REPORT CARDS
//
// Backend surface used: GET/POST /certificates/?classroom=, GET
// /report-cards/?classroom= (attendance/homework/marks are always
// server-computed — see StudentReportCard in models.py — so this
// screen only ever reads report cards, never edits their numbers).
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';

class CertificatesReportCardsScreen extends StatefulWidget {
  final LiveClassApi api;
  final int classroomId;
  final bool isTeacher;
  final int? studentIdToIssueFor;
  const CertificatesReportCardsScreen({
    super.key,
    required this.api,
    required this.classroomId,
    this.isTeacher = false,
    this.studentIdToIssueFor,
  });

  @override
  State<CertificatesReportCardsScreen> createState() => _CertificatesReportCardsScreenState();
}

class _CertificatesReportCardsScreenState extends State<CertificatesReportCardsScreen> with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 2, vsync: this);
  List<dynamic> _certificates = const [];
  List<dynamic> _reportCards = const [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    final results = await Future.wait([
      widget.api.certificates(classroomId: widget.classroomId),
      widget.api.reportCards(classroomId: widget.classroomId),
    ]);
    setState(() {
      _certificates = results[0] as List;
      _reportCards = results[1] as List;
      _loading = false;
    });
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.certificatesReportCardsTitle),
      floatingActionButton: widget.isTeacher && widget.studentIdToIssueFor != null && _tabs.index == 0
          ? FloatingActionButton.extended(
              icon: const Icon(Icons.workspace_premium_rounded),
              label: Text(t.issueCertificateCta),
              onPressed: () async {
                await widget.api.issueCertificate(widget.classroomId, widget.studentIdToIssueFor!);
                _load();
              },
            )
          : null,
      body: Column(children: [
        TabBar(
          controller: _tabs,
          onTap: (_) => setState(() {}),
          labelColor: cs.primary,
          unselectedLabelColor: cs.onSurfaceVariant,
          tabs: [Tab(text: t.certificatesTab), Tab(text: t.reportCardsTab)],
        ),
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator())
              : TabBarView(controller: _tabs, children: [
                  _certificates.isEmpty
                      ? EmptyStateWidget(title: t.noCertificatesYet, icon: Icons.workspace_premium_outlined)
                      : ListView(children: _certificates.map((c) {
                          final m = c as Map<String, dynamic>;
                          return LsCard(
                            margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                            child: LsMetaRow(icon: Icons.workspace_premium_rounded, label: m['student_name']?.toString() ?? '', value: m['issued_at']?.toString().split('T').first ?? ''),
                          );
                        }).toList()),
                  _reportCards.isEmpty
                      ? EmptyStateWidget(title: t.noReportCardsYet, icon: Icons.assessment_outlined)
                      : ListView(children: _reportCards.map((c) {
                          final m = c as Map<String, dynamic>;
                          return LsCard(
                            margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                              Text(m['period_label']?.toString() ?? '', style: LsType.head(context, size: 13.5)),
                              const SizedBox(height: 6),
                              Row(children: [
                                Expanded(child: LsScoreTile(value: '${m['attendance_percent'] ?? 0}%', label: t.attendanceLabel, color: cs.primary)),
                                const SizedBox(width: 8),
                                Expanded(child: LsScoreTile(value: '${m['homework_completion_percent'] ?? 0}%', label: t.homeworkLabel, color: Colors.orange)),
                                const SizedBox(width: 8),
                                Expanded(child: LsScoreTile(value: '${m['average_marks'] ?? 0}', label: t.marksLabel, color: Colors.green)),
                              ]),
                              if ((m['teacher_remark'] ?? '').toString().isNotEmpty) ...[
                                const SizedBox(height: 8),
                                Text(m['teacher_remark'].toString(), style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                              ],
                            ]),
                          );
                        }).toList()),
                ]),
        ),
      ]),
    );
  }
}
