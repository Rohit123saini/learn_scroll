// lib/tuitionclass/screens/doubts_screen.dart
//
// Screen §14 — Doubts / Query. GET/POST queries/, POST queries/{id}/answer/.
// Any user with classroom access can ask a doubt; teacher/co-teacher/
// moderator answers it, flipping status Open → Answered.
//
// Restyled onto the shared TuitionClass design system (tuitionclass_theme.dart)
// so it matches Certificates/Holidays/Coin Wallet instead of falling back
// to plain Material defaults. Logic/API calls unchanged.

import 'package:flutter/material.dart';

import '../models/tuitionclass_models.dart';
import '../services/tuitionclass_api_service.dart';
import '../theme/tuitionclass_theme.dart';

class DoubtsScreen extends StatefulWidget {
  final int classroomId;
  final bool canManage; // teacher/co-teacher/moderator — can answer

  const DoubtsScreen({super.key, required this.classroomId, required this.canManage});

  @override
  State<DoubtsScreen> createState() => _DoubtsScreenState();
}

class _DoubtsScreenState extends State<DoubtsScreen> {
  List<ClassQuery> _queries = [];
  bool _loading = true;
  String? _error;

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
      final res = await TuitionClassApi.queries.list(widget.classroomId);
      final sorted = [...res.results]..sort((a, b) {
          if (a.status != b.status) return a.status == QueryStatus.open ? -1 : 1;
          return b.createdAt.compareTo(a.createdAt);
        });
      if (!mounted) return;
      setState(() => _queries = sorted);
    } on TuitionClassApiException catch (e) {
      if (!mounted) return;
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _openAskDialog() async {
    // FIX (memory leak): controller was created here and never disposed —
    // every Ask dialog open+close leaked one TextEditingController for the
    // app's lifetime. try/finally guarantees disposal regardless of how
    // the dialog closes.
    final ctrl = TextEditingController();
    try {
      await _showAskDialog(ctrl);
    } finally {
      ctrl.dispose();
    }
  }

  Future<void> _showAskDialog(TextEditingController ctrl) async {
    bool saving = false;
    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) => AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          title: const Text('Ask a Doubt', style: TextStyle(color: TuitionClassColors.navy, fontWeight: FontWeight.bold)),
          content: TextField(
            controller: ctrl,
            maxLines: 4,
            autofocus: true,
            decoration: tuitionClassInputDecoration('Type your question…'),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: TuitionClassColors.navy),
              onPressed: saving
                  ? null
                  : () async {
                      if (ctrl.text.trim().isEmpty) return;
                      setDlg(() => saving = true);
                      try {
                        final q = await TuitionClassApi.queries.ask(
                          ClassQuery(
                            id: 0,
                            classroomId: widget.classroomId,
                            askedBy: UserMini(id: 0, username: '', fullName: ''),
                            question: ctrl.text.trim(),
                            createdAt: DateTime.now(),
                          ),
                        );
                        if (mounted) {
                          setState(() => _queries = [q, ..._queries]);
                          Navigator.pop(ctx);
                        }
                      } on TuitionClassApiException catch (e) {
                        setDlg(() => saving = false);
                        ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(e.message)));
                      }
                    },
              child: saving
                  ? const SizedBox(height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Text('Ask'),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _openAnswerDialog(ClassQuery q) async {
    // FIX (memory leak): same issue as _openAskDialog above — dispose on
    // every path out of the dialog.
    final ctrl = TextEditingController(text: q.answer);
    try {
      await _showAnswerDialog(q, ctrl);
    } finally {
      ctrl.dispose();
    }
  }

  Future<void> _showAnswerDialog(ClassQuery q, TextEditingController ctrl) async {
    bool saving = false;
    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) => AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          title: const Text('Answer Doubt', style: TextStyle(color: TuitionClassColors.navy, fontWeight: FontWeight.bold)),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(q.question, style: const TextStyle(fontSize: 13.5)),
              const SizedBox(height: 12),
              TextField(
                controller: ctrl,
                maxLines: 4,
                autofocus: true,
                decoration: tuitionClassInputDecoration('Your answer…'),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: TuitionClassColors.navy),
              onPressed: saving
                  ? null
                  : () async {
                      if (ctrl.text.trim().isEmpty) return;
                      setDlg(() => saving = true);
                      try {
                        final updated = await TuitionClassApi.queries.answer(q.id, ctrl.text.trim());
                        if (mounted) {
                          setState(() {
                            final idx = _queries.indexWhere((x) => x.id == q.id);
                            if (idx != -1) _queries[idx] = updated;
                          });
                          Navigator.pop(ctx);
                        }
                      } on TuitionClassApiException catch (e) {
                        setDlg(() => saving = false);
                        ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(e.message)));
                      }
                    },
              child: saving
                  ? const SizedBox(height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Text('Submit'),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: TuitionClassColors.bg,
      appBar: tuitionClassAppBar('Doubts'),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: TuitionClassColors.navy,
        onPressed: _openAskDialog,
        icon: const Icon(Icons.help_outline_rounded),
        label: const Text('Ask'),
      ),
      body: RefreshIndicator(
        color: TuitionClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const TuitionClassLoading()
            : _error != null
                ? TuitionClassErrorState(message: _error!, onRetry: _load)
                : _queries.isEmpty
                    ? const TuitionClassEmptyState(
                        icon: Icons.help_outline_rounded,
                        title: 'No doubts asked yet',
                        subtitle: 'Tap "Ask" to post the first question.',
                      )
                    : ListView.builder(
                        padding: const EdgeInsets.fromLTRB(16, 14, 16, 90),
                        itemCount: _queries.length,
                        itemBuilder: (ctx, i) {
                          final q = _queries[i];
                          final answered = q.status == QueryStatus.answered;
                          return TuitionClassCard(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Expanded(
                                      child: Text(q.question, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
                                    ),
                                    const SizedBox(width: 8),
                                    answered
                                        ? const TuitionClassStatusChip(label: 'ANSWERED', color: TuitionClassColors.success, background: TuitionClassColors.successBg)
                                        : const TuitionClassStatusChip(label: 'OPEN', color: TuitionClassColors.warning, background: TuitionClassColors.warningBg),
                                  ],
                                ),
                                const SizedBox(height: 6),
                                Row(
                                  children: [
                                    Icon(Icons.person_outline_rounded, size: 13, color: Colors.grey.shade500),
                                    const SizedBox(width: 3),
                                    Expanded(
                                      child: Text(
                                        // FIX (timezone bug): q.createdAt comes from the API as a
                                        // UTC DateTime (see tuitionclass_models.dart's parsing note).
                                        // Raw DateFormat.format() reads UTC clock fields directly,
                                        // so this used to show the server's UTC time instead of the
                                        // viewer's own — same class of bug tuitionclass_theme.dart's
                                        // tuitionClassFmtDate() already fixes everywhere else in the
                                        // module. Switched to that shared, locale + timezone-safe
                                        // helper instead of a raw DateFormat call.
                                        '${q.askedBy.fullName} · ${tuitionClassFmtDate(q.createdAt, context)}',
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis,
                                        style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500),
                                      ),
                                    ),
                                  ],
                                ),
                                if (answered) ...[
                                  const Divider(height: 22),
                                  Row(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      const Icon(Icons.subdirectory_arrow_right_rounded, size: 18, color: TuitionClassColors.navy),
                                      const SizedBox(width: 6),
                                      Expanded(child: Text(q.answer, style: const TextStyle(fontSize: 13))),
                                    ],
                                  ),
                                  if (q.answeredBy != null)
                                    Padding(
                                      padding: const EdgeInsets.only(top: 4, left: 24),
                                      child: Text('— ${q.answeredBy!.fullName}', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
                                    ),
                                ] else if (widget.canManage) ...[
                                  const SizedBox(height: 10),
                                  Align(
                                    alignment: Alignment.centerRight,
                                    child: FilledButton.tonal(
                                      onPressed: () => _openAnswerDialog(q),
                                      style: FilledButton.styleFrom(backgroundColor: TuitionClassColors.navy.withValues(alpha: 0.08), foregroundColor: TuitionClassColors.navy),
                                      child: const Text('Answer'),
                                    ),
                                  ),
                                ],
                              ],
                            ),
                          );
                        },
                      ),
      ),
    );
  }
}