// lib/liveclass/screens/doubts_screen.dart
//
// Screen §14 — Doubts / Query. GET/POST queries/, POST queries/{id}/answer/.
// Any user with classroom access can ask a doubt; teacher/co-teacher/
// moderator answers it, flipping status Open → Answered.
//
// Restyled onto the shared LiveClass design system (liveclass_theme.dart)
// so it matches Certificates/Holidays/Coin Wallet instead of falling back
// to plain Material defaults. Logic/API calls unchanged.

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

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
      final res = await LiveClassApi.queries.list(widget.classroomId);
      final sorted = [...res.results]..sort((a, b) {
          if (a.status != b.status) return a.status == QueryStatus.open ? -1 : 1;
          return b.createdAt.compareTo(a.createdAt);
        });
      if (!mounted) return;
      setState(() => _queries = sorted);
    } on LiveClassApiException catch (e) {
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
          title: const Text('Ask a Doubt', style: TextStyle(color: LiveClassColors.navy, fontWeight: FontWeight.bold)),
          content: TextField(
            controller: ctrl,
            maxLines: 4,
            autofocus: true,
            decoration: liveClassInputDecoration('Type your question…'),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: LiveClassColors.navy),
              onPressed: saving
                  ? null
                  : () async {
                      if (ctrl.text.trim().isEmpty) return;
                      setDlg(() => saving = true);
                      try {
                        final q = await LiveClassApi.queries.ask(
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
                      } on LiveClassApiException catch (e) {
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
          title: const Text('Answer Doubt', style: TextStyle(color: LiveClassColors.navy, fontWeight: FontWeight.bold)),
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
                decoration: liveClassInputDecoration('Your answer…'),
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: LiveClassColors.navy),
              onPressed: saving
                  ? null
                  : () async {
                      if (ctrl.text.trim().isEmpty) return;
                      setDlg(() => saving = true);
                      try {
                        final updated = await LiveClassApi.queries.answer(q.id, ctrl.text.trim());
                        if (mounted) {
                          setState(() {
                            final idx = _queries.indexWhere((x) => x.id == q.id);
                            if (idx != -1) _queries[idx] = updated;
                          });
                          Navigator.pop(ctx);
                        }
                      } on LiveClassApiException catch (e) {
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
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar('Doubts'),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: LiveClassColors.navy,
        onPressed: _openAskDialog,
        icon: const Icon(Icons.help_outline_rounded),
        label: const Text('Ask'),
      ),
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _queries.isEmpty
                    ? const LiveClassEmptyState(
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
                          return LiveClassCard(
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
                                        ? const LiveClassStatusChip(label: 'ANSWERED', color: LiveClassColors.success, background: LiveClassColors.successBg)
                                        : const LiveClassStatusChip(label: 'OPEN', color: LiveClassColors.warning, background: LiveClassColors.warningBg),
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
                                        // UTC DateTime (see liveclass_models.dart's parsing note).
                                        // Raw DateFormat.format() reads UTC clock fields directly,
                                        // so this used to show the server's UTC time instead of the
                                        // viewer's own — same class of bug liveclass_theme.dart's
                                        // liveClassFmtDate() already fixes everywhere else in the
                                        // module. Switched to that shared, locale + timezone-safe
                                        // helper instead of a raw DateFormat call.
                                        '${q.askedBy.fullName} · ${liveClassFmtDate(q.createdAt, context)}',
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
                                      const Icon(Icons.subdirectory_arrow_right_rounded, size: 18, color: LiveClassColors.navy),
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
                                      style: FilledButton.styleFrom(backgroundColor: LiveClassColors.navy.withValues(alpha: 0.08), foregroundColor: LiveClassColors.navy),
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