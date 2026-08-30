// lib/liveclass/screens/notice_board_screen.dart
//
// Screen §13 — Notice Board. GET/POST notices/, POST notices/{id}/pin/,
// DELETE notices/{id}/. Priority-coded cards (low/normal/urgent), pinned
// notices sorted to the top. Teacher/staff post; everyone with classroom
// access reads.
//
// Restyled onto the shared LiveClass design system (liveclass_theme.dart)
// so it matches Certificates/Holidays/Materials instead of falling back to
// plain Material defaults. Logic/API calls unchanged.

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

Color _priorityColor(String priority) {
  switch (priority) {
    case NoticePriority.urgent:
      return LiveClassColors.danger;
    case NoticePriority.low:
      return Colors.grey.shade600;
    default:
      return LiveClassColors.navy;
  }
}

class NoticeBoardScreen extends StatefulWidget {
  final int classroomId;
  final bool canManage; // teacher/co-teacher/moderator

  const NoticeBoardScreen({super.key, required this.classroomId, required this.canManage});

  @override
  State<NoticeBoardScreen> createState() => _NoticeBoardScreenState();
}

class _NoticeBoardScreenState extends State<NoticeBoardScreen> {
  List<Notice> _notices = [];
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
      final res = await LiveClassApi.notices.list(widget.classroomId);
      final sorted = [...res.results]..sort((a, b) {
          if (a.isPinned != b.isPinned) return a.isPinned ? -1 : 1;
          return b.createdAt.compareTo(a.createdAt);
        });
      if (!mounted) return;
      setState(() => _notices = sorted);
    } on LiveClassApiException catch (e) {
      if (!mounted) return;
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _togglePin(Notice n) async {
    try {
      final updated = await LiveClassApi.notices.pin(n.id);
      if (!mounted) return;
      setState(() {
        _notices = [..._notices.where((x) => x.id != n.id), updated]
          ..sort((a, b) {
            if (a.isPinned != b.isPinned) return a.isPinned ? -1 : 1;
            return b.createdAt.compareTo(a.createdAt);
          });
      });
    } on LiveClassApiException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  Future<void> _confirmDelete(Notice n) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Delete notice?'),
        content: Text('"${n.title}" will be removed.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: TextButton.styleFrom(foregroundColor: LiveClassColors.danger),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    final previous = List<Notice>.from(_notices);
    setState(() => _notices.removeWhere((x) => x.id == n.id));
    try {
      await LiveClassApi.notices.delete(n.id);
    } on LiveClassApiException catch (e) {
      if (!mounted) return;
      setState(() => _notices = previous);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  Future<void> _openComposeSheet() async {
    // FIX (memory leak): both controllers were created here and never
    // disposed — every "Post Notice" open+close leaked two
    // TextEditingControllers for the app's lifetime. try/finally
    // guarantees disposal on every exit path.
    final titleCtrl = TextEditingController();
    final messageCtrl = TextEditingController();
    try {
      await _showComposeSheet(titleCtrl, messageCtrl);
    } finally {
      titleCtrl.dispose();
      messageCtrl.dispose();
    }
  }

  Future<void> _showComposeSheet(TextEditingController titleCtrl, TextEditingController messageCtrl) async {
    String priority = NoticePriority.normal;
    DateTime? expiresAt;
    bool saving = false;

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(LiveClassRadius.sheet))),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) => Padding(
          padding: EdgeInsets.only(left: 20, right: 20, top: 20, bottom: MediaQuery.of(ctx).viewInsets.bottom + 20),
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              mainAxisSize: MainAxisSize.min,
              children: [
                Center(
                  child: Container(
                    width: 36,
                    height: 4,
                    margin: const EdgeInsets.only(bottom: 16),
                    decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(2)),
                  ),
                ),
                const Text('Post Notice', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: LiveClassColors.navy)),
                const SizedBox(height: 16),
                TextField(controller: titleCtrl, decoration: liveClassInputDecoration('Title', label: 'Title')),
                const SizedBox(height: 12),
                TextField(controller: messageCtrl, maxLines: 4, decoration: liveClassInputDecoration('Message', label: 'Message')),
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  initialValue: priority,
                  decoration: liveClassInputDecoration('', label: 'Priority'),
                  items: const [
                    DropdownMenuItem(value: NoticePriority.low, child: Text('Low')),
                    DropdownMenuItem(value: NoticePriority.normal, child: Text('Normal')),
                    DropdownMenuItem(value: NoticePriority.urgent, child: Text('Urgent')),
                  ],
                  onChanged: (v) => setSheet(() => priority = v ?? NoticePriority.normal),
                ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: () async {
                    final now = DateTime.now();
                    final date = await showDatePicker(
                      context: ctx,
                      initialDate: now.add(const Duration(days: 7)),
                      firstDate: now,
                      lastDate: now.add(const Duration(days: 365)),
                    );
                    if (date != null) setSheet(() => expiresAt = date);
                  },
                  style: OutlinedButton.styleFrom(
                    alignment: Alignment.centerLeft,
                    padding: const EdgeInsets.symmetric(vertical: 13, horizontal: 14),
                    side: BorderSide(color: Colors.grey.shade300),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                  ),
                  icon: Icon(Icons.timer_off_outlined, color: expiresAt == null ? Colors.grey.shade600 : LiveClassColors.navy),
                  label: Text(
                    // expiresAt here is a locally-picked DateTime (from showDatePicker),
                    // not one parsed from the API — already in local time, so no
                    // .toLocal() bug like the two calls below. Still switched to the
                    // shared helper for consistent locale-aware formatting.
                    expiresAt == null ? 'Auto-hide date (optional)' : liveClassFmtDate(expiresAt!, context),
                    style: TextStyle(color: expiresAt == null ? Colors.grey.shade600 : LiveClassColors.navy, fontWeight: FontWeight.w600),
                  ),
                ),
                const SizedBox(height: 20),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: LiveClassColors.navy,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                    ),
                    onPressed: saving
                        ? null
                        : () async {
                            if (titleCtrl.text.trim().isEmpty || messageCtrl.text.trim().isEmpty) {
                              ScaffoldMessenger.of(ctx)
                                  .showSnackBar(const SnackBar(content: Text('Title and message are required')));
                              return;
                            }
                            setSheet(() => saving = true);
                            try {
                              final n = await LiveClassApi.notices.create(
                                Notice(
                                  id: 0,
                                  classroomId: widget.classroomId,
                                  postedBy: UserMini(id: 0, username: '', fullName: ''),
                                  title: titleCtrl.text.trim(),
                                  message: messageCtrl.text.trim(),
                                  priority: priority,
                                  createdAt: DateTime.now(),
                                  expiresAt: expiresAt,
                                ),
                              );
                              if (mounted) {
                                setState(() => _notices = [n, ..._notices]);
                                Navigator.pop(ctx);
                              }
                            } on LiveClassApiException catch (e) {
                              setSheet(() => saving = false);
                              ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(e.message)));
                            }
                          },
                    child: saving
                        ? const SizedBox(height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : const Text('Post'),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar('Notice Board'),
      floatingActionButton: widget.canManage
          ? FloatingActionButton.extended(
              backgroundColor: LiveClassColors.navy,
              onPressed: _openComposeSheet,
              icon: const Icon(Icons.campaign_rounded),
              label: const Text('Post'),
            )
          : null,
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _notices.isEmpty
                    ? const LiveClassEmptyState(icon: Icons.campaign_outlined, title: 'No notices yet')
                    : ListView.builder(
                        padding: EdgeInsets.fromLTRB(16, 14, 16, widget.canManage ? 90 : 24),
                        itemCount: _notices.length,
                        itemBuilder: (ctx, i) {
                          final n = _notices[i];
                          final color = _priorityColor(n.priority);
                          return Opacity(
                            opacity: n.isExpired ? 0.55 : 1,
                            child: Container(
                              margin: const EdgeInsets.only(bottom: LiveClassSpacing.md),
                              padding: const EdgeInsets.all(14),
                              decoration: BoxDecoration(
                                color: Colors.white,
                                borderRadius: BorderRadius.circular(LiveClassRadius.card),
                                border: Border.all(color: color.withValues(alpha: 0.35)),
                                boxShadow: const [LiveClassColors.cardShadow],
                              ),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    children: [
                                      if (n.isPinned) ...[
                                        Icon(Icons.push_pin_rounded, size: 15, color: color),
                                        const SizedBox(width: 4),
                                      ],
                                      Expanded(
                                        child: Text(n.title, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
                                      ),
                                      const SizedBox(width: 8),
                                      LiveClassStatusChip(label: n.priority.toUpperCase(), color: color, background: color.withValues(alpha: 0.12)),
                                    ],
                                  ),
                                  const SizedBox(height: 6),
                                  Text(n.message, style: const TextStyle(fontSize: 13)),
                                  const SizedBox(height: 10),
                                  Row(
                                    children: [
                                      Expanded(
                                        child: Text(
                                          // FIX (timezone bug): n.createdAt is UTC from the API —
                                          // see the matching note in doubts_screen.dart. Switched
                                          // to the shared liveClassFmtDate() helper.
                                          '${n.postedBy.fullName} · ${liveClassFmtDate(n.createdAt, context)}'
                                          '${n.isExpired ? " · Expired" : ""}',
                                          style: TextStyle(fontSize: 11, color: Colors.grey.shade500),
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis,
                                        ),
                                      ),
                                      if (widget.canManage) ...[
                                        IconButton(
                                          visualDensity: VisualDensity.compact,
                                          icon: Icon(n.isPinned ? Icons.push_pin_rounded : Icons.push_pin_outlined, color: LiveClassColors.navy),
                                          onPressed: () => _togglePin(n),
                                        ),
                                        IconButton(
                                          visualDensity: VisualDensity.compact,
                                          icon: const Icon(Icons.delete_outline_rounded, color: LiveClassColors.danger),
                                          onPressed: () => _confirmDelete(n),
                                        ),
                                      ],
                                    ],
                                  ),
                                ],
                              ),
                            ),
                          );
                        },
                      ),
      ),
    );
  }
}