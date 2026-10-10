import 'package:flutter/material.dart';

import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// T4 §F — DOUBTS for ONE subject-class
//
// Private by default: only the author, that subject's teacher(s), the
// class-teacher and campus admin see a doubt (a classmate's public doubt is
// shown read-only when the campus allows it). Visibility is enforced by the
// backend — this screen just renders what it is given and shows the API's
// error text when an action is refused.
// ============================================================

class DoubtsScreen extends StatefulWidget {
  final MyClassCard card;
  const DoubtsScreen({super.key, required this.card});

  @override
  State<DoubtsScreen> createState() => _DoubtsScreenState();
}

class _DoubtsScreenState extends State<DoubtsScreen> {
  bool _loading = true;
  String? _error;
  List<Doubt> _doubts = const [];

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
      final d = await CampusService.doubts(sectionId: widget.card.sectionId, subjectId: widget.card.subjectId);
      if (!mounted) return;
      setState(() {
        _doubts = d;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _ask() async {
    final ctrl = TextEditingController();
    final text = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Ask ${widget.card.teacherName}'),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          minLines: 3,
          maxLines: 6,
          decoration: const InputDecoration(hintText: 'Type your doubt…'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, ctrl.text.trim()), child: const Text('Post')),
        ],
      ),
    );
    if (text == null || text.isEmpty) return;
    try {
      await CampusService.postDoubt(
        sectionId: widget.card.sectionId,
        subjectId: widget.card.subjectId,
        text: text,
      );
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString())));
    }
  }

  Color _statusColor(String s, ColorScheme cs) =>
      s == 'resolved' ? Colors.green : (s == 'answered' ? cs.primary : cs.tertiary);

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Doubts', style: LsType.head(context, size: 15)),
          Text(widget.card.subjectName, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ]),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _ask,
        icon: const Icon(Icons.add),
        label: const Text('Ask a doubt'),
      ),
      body: RefreshIndicator(onRefresh: _load, child: _body(cs)),
    );
  }

  Widget _body(ColorScheme cs) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 70),
        SizedBox(height: 8),
        LsSkeletonBox(height: 70),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(title: 'Could not load doubts', subtitle: _error, retryLabel: 'Retry', onRetry: _load),
      ]);
    }
    if (_doubts.isEmpty) {
      return ListView(children: const [
        SizedBox(height: 60),
        EmptyStateWidget(icon: Icons.help_outline, title: 'No doubts yet'),
      ]);
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 90),
      itemCount: _doubts.length,
      itemBuilder: (_, i) {
        final d = _doubts[i];
        return Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: LsCard(
            padding: const EdgeInsets.all(12),
            child: InkWell(
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => DoubtThreadScreen(doubtId: d.id)),
              ).then((_) => _load()),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Expanded(
                    child: Text(d.author?.username ?? '', style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                  ),
                  LsStatusChip(label: d.status, color: _statusColor(d.status, cs)),
                ]),
                const SizedBox(height: 6),
                Text(d.text, maxLines: 3, overflow: TextOverflow.ellipsis),
                const SizedBox(height: 6),
                Text('${d.repliesCount} replies${d.isPublic ? ' · shared with class' : ''}',
                    style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
              ]),
            ),
          ),
        );
      },
    );
  }
}

class DoubtThreadScreen extends StatefulWidget {
  final String doubtId;
  const DoubtThreadScreen({super.key, required this.doubtId});

  @override
  State<DoubtThreadScreen> createState() => _DoubtThreadScreenState();
}

class _DoubtThreadScreenState extends State<DoubtThreadScreen> {
  Doubt? _doubt;
  String? _error;
  bool _busy = false;
  final _reply = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _reply.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final d = await CampusService.doubt(widget.doubtId);
      if (!mounted) return;
      setState(() {
        _doubt = d;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    }
  }

  Future<void> _run(Future<void> Function() fn) async {
    setState(() => _busy = true);
    try {
      await fn();
      await _load();
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString())));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _send() async {
    final text = _reply.text.trim();
    if (text.isEmpty) return;
    await _run(() async {
      await CampusService.replyToDoubt(widget.doubtId, text);
      _reply.clear();
    });
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final d = _doubt;
    return Scaffold(
      appBar: AppBar(
        title: Text('Doubt', style: LsType.head(context, size: 15)),
        actions: [
          if (d != null)
            TextButton(
              onPressed: _busy
                  ? null
                  : () => _run(() async {
                        d.isResolved
                            ? await CampusService.reopenDoubt(d.id)
                            : await CampusService.resolveDoubt(d.id);
                      }),
              child: Text(d.isResolved ? 'Reopen' : 'Mark resolved'),
            ),
        ],
      ),
      body: d == null
          ? (_error != null
              ? ErrorStateWidget(title: 'Could not load doubt', subtitle: _error, retryLabel: 'Retry', onRetry: _load)
              : const Center(child: CircularProgressIndicator()))
          : Column(children: [
              Expanded(
                child: ListView(padding: const EdgeInsets.all(14), children: [
                  LsCard(
                    padding: const EdgeInsets.all(12),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(d.author?.username ?? '', style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                      const SizedBox(height: 6),
                      Text(d.text),
                    ]),
                  ),
                  const SizedBox(height: 10),
                  for (final r in d.replies)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: LsCard(
                        padding: const EdgeInsets.all(12),
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Text('${r.author?.username ?? ''}${r.isStaffReply ? ' · Teacher' : ''}',
                              style: TextStyle(
                                  fontSize: 11.5, color: r.isStaffReply ? cs.primary : cs.onSurfaceVariant)),
                          const SizedBox(height: 6),
                          Text(r.text),
                        ]),
                      ),
                    ),
                ]),
              ),
              SafeArea(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
                  child: Row(children: [
                    Expanded(
                      child: TextField(
                        controller: _reply,
                        minLines: 1,
                        maxLines: 4,
                        decoration: const InputDecoration(hintText: 'Write a reply…'),
                      ),
                    ),
                    IconButton(tooltip: 'Send', onPressed: _busy ? null : _send, icon: const Icon(Icons.send_rounded)),
                  ]),
                ),
              ),
            ]),
    );
  }
}
