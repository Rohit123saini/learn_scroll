// lib/liveclass/screens/assignments_screen.dart
//
// Screen §12 (list half) — Assignments. GET/POST assignments/, DELETE
// assignments/{id}/. Teacher creates (title, description, attachment,
// due_date, max_score); tapping a card opens SubmissionGradingScreen, which
// shows either "submit" (student) or the grading queue (teacher).
//
// Restyled onto the shared LiveClass design system (liveclass_theme.dart)
// so it matches Certificates/Holidays/Coin Wallet instead of falling back
// to plain Material defaults. Logic/API calls unchanged.

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import 'submission_grading_screen.dart';

class AssignmentsScreen extends StatefulWidget {
  final int classroomId;
  final int? sessionId;
  final bool canManage; // teacher/co-teacher/moderator

  const AssignmentsScreen({
    super.key,
    required this.classroomId,
    this.sessionId,
    required this.canManage,
  });

  @override
  State<AssignmentsScreen> createState() => _AssignmentsScreenState();
}

class _AssignmentsScreenState extends State<AssignmentsScreen> {
  List<Assignment> _assignments = [];
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
      final res = await LiveClassApi.assignments.list(widget.classroomId);
      if (!mounted) return;
      setState(() => _assignments = res.results);
    } on LiveClassApiException catch (e) {
      if (!mounted) return;
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _confirmDelete(Assignment a) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete assignment?'),
        content: Text('"${a.title}" and its submissions will be removed.'),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
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
    final previous = List<Assignment>.from(_assignments);
    setState(() => _assignments.removeWhere((x) => x.id == a.id));
    try {
      await LiveClassApi.assignments.delete(a.id);
    } on LiveClassApiException catch (e) {
      if (!mounted) return;
      setState(() => _assignments = previous);
      _snack(e.message);
    }
  }

  Future<void> _openCreateSheet() async {
    // FIX (memory leak): these three controllers used to be created here
    // and never disposed — every open+close of this sheet leaked three
    // TextEditingControllers for the lifetime of the app. try/finally
    // guarantees disposal on every exit path (submitted, cancelled, or
    // dismissed).
    final titleCtrl = TextEditingController();
    final descCtrl = TextEditingController();
    final scoreCtrl = TextEditingController(text: '100');
    try {
      await _showCreateSheet(titleCtrl, descCtrl, scoreCtrl);
    } finally {
      titleCtrl.dispose();
      descCtrl.dispose();
      scoreCtrl.dispose();
    }
  }

  Future<void> _showCreateSheet(
    TextEditingController titleCtrl,
    TextEditingController descCtrl,
    TextEditingController scoreCtrl,
  ) async {
    DateTime? dueDate;
    PlatformFile? attachment;
    bool saving = false;

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(LiveClassRadius.sheet))),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) => Padding(
          padding: EdgeInsets.only(
            left: 20,
            right: 20,
            top: 20,
            bottom: MediaQuery.of(ctx).viewInsets.bottom + 20,
          ),
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
                const Text('New Assignment', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: LiveClassColors.navy)),
                const SizedBox(height: 16),
                TextField(controller: titleCtrl, decoration: liveClassInputDecoration('e.g. Week 3 problem set', label: 'Title')),
                const SizedBox(height: 12),
                TextField(
                  controller: descCtrl,
                  maxLines: 3,
                  decoration: liveClassInputDecoration('What should students do?', label: 'Description'),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: scoreCtrl,
                  keyboardType: TextInputType.number,
                  decoration: liveClassInputDecoration('100', label: 'Max score'),
                ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: () async {
                    final now = DateTime.now();
                    final date = await showDatePicker(
                      context: ctx,
                      initialDate: now.add(const Duration(days: 3)),
                      firstDate: now,
                      lastDate: now.add(const Duration(days: 365)),
                    );
                    if (date == null) return;
                    if (!ctx.mounted) return;
                    final time = await showTimePicker(context: ctx, initialTime: const TimeOfDay(hour: 23, minute: 59));
                    setSheet(() => dueDate = DateTime(date.year, date.month, date.day, time?.hour ?? 23, time?.minute ?? 59));
                  },
                  style: OutlinedButton.styleFrom(
                    alignment: Alignment.centerLeft,
                    padding: const EdgeInsets.symmetric(vertical: 13, horizontal: 14),
                    side: BorderSide(color: Colors.grey.shade300),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                  ),
                  icon: Icon(Icons.event_rounded, color: dueDate == null ? Colors.grey.shade600 : LiveClassColors.navy),
                  label: Text(
                    dueDate == null ? 'Set due date' : liveClassFmtDateTime(dueDate!, ctx),
                    style: TextStyle(color: dueDate == null ? Colors.grey.shade600 : LiveClassColors.navy, fontWeight: FontWeight.w600),
                  ),
                ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: () async {
                    final res = await FilePicker.platform.pickFiles();
                    if (res != null && res.files.isNotEmpty) {
                      setSheet(() => attachment = res.files.first);
                    }
                  },
                  style: OutlinedButton.styleFrom(
                    alignment: Alignment.centerLeft,
                    padding: const EdgeInsets.symmetric(vertical: 13, horizontal: 14),
                    side: BorderSide(color: Colors.grey.shade300),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                  ),
                  icon: Icon(Icons.attach_file_rounded, color: attachment == null ? Colors.grey.shade600 : LiveClassColors.navy),
                  label: Text(
                    attachment?.name ?? 'Attach file (optional)',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(color: attachment == null ? Colors.grey.shade600 : LiveClassColors.navy, fontWeight: FontWeight.w600),
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
                            if (titleCtrl.text.trim().isEmpty || dueDate == null) {
                              ScaffoldMessenger.of(ctx)
                                  .showSnackBar(const SnackBar(content: Text('Title and due date are required')));
                              return;
                            }
                            final maxScore = int.tryParse(scoreCtrl.text.trim()) ?? 100;
                            setSheet(() => saving = true);
                            try {
                              final a = await LiveClassApi.assignments.create(
                                Assignment(
                                  id: 0,
                                  classroomId: widget.classroomId,
                                  sessionId: widget.sessionId,
                                  title: titleCtrl.text.trim(),
                                  description: descCtrl.text.trim(),
                                  dueDate: dueDate!,
                                  maxScore: maxScore,
                                  createdAt: DateTime.now(),
                                ),
                                attachmentPath: attachment?.path,
                              );
                              if (mounted) {
                                setState(() => _assignments = [a, ..._assignments]);
                                Navigator.pop(ctx);
                              }
                            } on LiveClassApiException catch (e) {
                              setSheet(() => saving = false);
                              ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(e.message)));
                            }
                          },
                    child: saving
                        ? const SizedBox(
                            height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : const Text('Create'),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // FIX (i18n / timezone audit — see utils/liveclass_datetime.dart / the
  // matching fix already applied in doubts_screen.dart, holidays_screen.dart,
  // submission_grading_screen.dart etc.): this used to be a hand-rolled
  // formatter reading `.day`/`.month`/`.hour` straight off `a.dueDate`, which
  // comes from the API as a UTC DateTime (see liveclass_models.dart's
  // parsing) — with no `.toLocal()` call, and against the deprecated,
  // English-only `kLiveClassMonths` array. A due date set for 11:59 PM IST
  // rendered hours off (and only in English) for every viewer. Every call
  // site below now goes through the shared, locale + timezone-safe
  // `liveClassFmtDateTime()` helper this file already imports from
  // liveclass_theme.dart instead.

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar('Assignments'),
      floatingActionButton: widget.canManage
          ? FloatingActionButton.extended(
              backgroundColor: LiveClassColors.navy,
              onPressed: _openCreateSheet,
              icon: const Icon(Icons.add_rounded),
              label: const Text('New'),
            )
          : null,
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _assignments.isEmpty
                    ? const LiveClassEmptyState(
                        icon: Icons.assignment_outlined,
                        title: 'No assignments yet',
                        subtitle: 'Assignments you create will show up here.',
                      )
                    : ListView.builder(
                        padding: EdgeInsets.fromLTRB(16, 14, 16, widget.canManage ? 90 : 24),
                        itemCount: _assignments.length,
                        itemBuilder: (ctx, i) {
                          final a = _assignments[i];
                          final overdue = DateTime.now().isAfter(a.dueDate);
                          return LiveClassCard(
                            onTap: () => Navigator.push(
                              context,
                              MaterialPageRoute(
                                builder: (_) => SubmissionGradingScreen(assignment: a, canManage: widget.canManage),
                              ),
                            ),
                            child: Row(
                              children: [
                                LiveClassIconBadge(icon: Icons.assignment_rounded, size: 44),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(a.title, maxLines: 1, overflow: TextOverflow.ellipsis,
                                          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
                                      const SizedBox(height: 4),
                                      Row(
                                        children: [
                                          Icon(Icons.event_rounded, size: 13, color: overdue ? LiveClassColors.danger : Colors.grey.shade500),
                                          const SizedBox(width: 4),
                                          Text(
                                            'Due ${liveClassFmtDateTime(a.dueDate, ctx)}',
                                            style: TextStyle(
                                              fontSize: 11.5,
                                              color: overdue ? LiveClassColors.danger : Colors.grey.shade500,
                                              fontWeight: overdue ? FontWeight.w600 : FontWeight.normal,
                                            ),
                                          ),
                                        ],
                                      ),
                                      const SizedBox(height: 6),
                                      Row(
                                        children: [
                                          if (overdue) ...[
                                            const LiveClassStatusChip(label: 'PAST DUE', color: LiveClassColors.danger, background: LiveClassColors.dangerBg),
                                            const SizedBox(width: 6),
                                          ],
                                          Container(
                                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                                            decoration: BoxDecoration(color: Colors.grey.shade100, borderRadius: BorderRadius.circular(8)),
                                            child: Text('Max ${a.maxScore}', style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w600, color: Colors.grey.shade700)),
                                          ),
                                        ],
                                      ),
                                    ],
                                  ),
                                ),
                                const SizedBox(width: 6),
                                widget.canManage
                                    ? IconButton(
                                        icon: const Icon(Icons.delete_outline_rounded, color: LiveClassColors.danger),
                                        onPressed: () => _confirmDelete(a),
                                      )
                                    : Icon(Icons.chevron_right_rounded, color: Colors.grey.shade400),
                              ],
                            ),
                          );
                        },
                      ),
      ),
    );
  }
}