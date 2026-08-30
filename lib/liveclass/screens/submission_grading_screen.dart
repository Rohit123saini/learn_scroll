// lib/liveclass/screens/submission_grading_screen.dart
//
// Screen §12 (submission + grading half). POST submissions/, POST
// submissions/{id}/grade/. Teacher sees every student's submission and
// grades it (score capped at max_score + feedback); a student sees the
// assignment brief and either a submit button or their own status/grade.
// (The submissions/ list endpoint is expected to scope itself to "own" for
// non-manager callers — same pattern as join-requests/ elsewhere in this
// app — so this screen doesn't need to know the signed-in user's id.)
//
// Restyled onto the shared LiveClass design system (liveclass_theme.dart)
// so it matches Assignments/Certificates instead of falling back to plain
// Material defaults. Logic/API calls unchanged.

import 'dart:io';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';

import '../../services/auth_service.dart';
import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

class SubmissionGradingScreen extends StatefulWidget {
  final Assignment assignment;
  final bool canManage;

  const SubmissionGradingScreen({
    super.key,
    required this.assignment,
    required this.canManage,
  });

  @override
  State<SubmissionGradingScreen> createState() => _SubmissionGradingScreenState();
}

class _SubmissionGradingScreenState extends State<SubmissionGradingScreen> {
  List<AssignmentSubmission> _submissions = [];
  bool _loading = true;
  bool _submitting = false;
  String? _error;

  Assignment get _a => widget.assignment;

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
      final res = await LiveClassApi.submissions.list(_a.id);
      if (!mounted) return;
      setState(() => _submissions = res.results);
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

  Future<void> _submit() async {
    final res = await FilePicker.platform.pickFiles();
    if (res == null || res.files.isEmpty || res.files.first.path == null) return;
    if (!mounted) return;
    setState(() => _submitting = true);
    try {
      final s = await LiveClassApi.submissions.submit(assignmentId: _a.id, filePath: res.files.first.path!);
      if (mounted) setState(() => _submissions = [s]);
    } on LiveClassApiException catch (e) {
      _snack(e.message);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  Future<void> _openFile(String? url, {String name = 'file'}) async {
    if (url == null || url.isEmpty) return;
    try {
      final token = await AuthService.getToken();
      final dir = await getTemporaryDirectory();
      final savePath = '${dir.path}/${name.replaceAll(' ', '_')}';
      final file = File(savePath);
      if (!await file.exists()) {
        // FIX (production readiness audit — corrupt-cache bug, same as
        // certificates_screen.dart/materials_screen.dart): a download that
        // fails partway used to leave a partial/corrupt file at
        // `savePath`, which the `exists()` check above then treated as
        // already-cached forever — the submission/attachment became
        // permanently unopenable in-app. A failed download now deletes its
        // own partial file so the next tap retries instead of replaying
        // the corrupt one.
        try {
          await Dio().download(url, savePath,
              options: Options(headers: token != null && token.isNotEmpty ? {'Authorization': 'Bearer $token'} : {}));
        } catch (_) {
          if (await file.exists()) await file.delete();
          rethrow;
        }
      }
      await OpenFilex.open(savePath);
    } catch (_) {
      _snack("Couldn't open file");
    }
  }

  Future<void> _openGradeDialog(AssignmentSubmission s) async {
    // FIX (memory leak): both controllers were created here and never
    // disposed — every "Grade"/"Regrade" open+close leaked two
    // TextEditingControllers for the app's lifetime. try/finally
    // guarantees disposal on every exit path.
    final scoreCtrl = TextEditingController(text: s.score?.toString() ?? '');
    final feedbackCtrl = TextEditingController(text: s.feedback);
    try {
      await _showGradeDialog(s, scoreCtrl, feedbackCtrl);
    } finally {
      scoreCtrl.dispose();
      feedbackCtrl.dispose();
    }
  }

  Future<void> _showGradeDialog(AssignmentSubmission s, TextEditingController scoreCtrl, TextEditingController feedbackCtrl) async {
    bool saving = false;

    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) => AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          title: Text('Grade ${s.student.fullName}', style: const TextStyle(color: LiveClassColors.navy, fontWeight: FontWeight.bold)),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: scoreCtrl,
                keyboardType: TextInputType.number,
                decoration: liveClassInputDecoration('Out of ${_a.maxScore}', label: 'Score'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: feedbackCtrl,
                maxLines: 3,
                decoration: liveClassInputDecoration('Feedback (optional)'),
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
                      final score = int.tryParse(scoreCtrl.text.trim());
                      if (score == null || score < 0 || score > _a.maxScore) {
                        ScaffoldMessenger.of(ctx)
                            .showSnackBar(SnackBar(content: Text('Score must be 0–${_a.maxScore}')));
                        return;
                      }
                      setDlg(() => saving = true);
                      try {
                        final graded =
                            await LiveClassApi.submissions.grade(s.id, score: score, feedback: feedbackCtrl.text.trim());
                        if (mounted) {
                          setState(() {
                            final idx = _submissions.indexWhere((x) => x.id == s.id);
                            if (idx != -1) _submissions[idx] = graded;
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
                  : const Text('Save'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _brief() {
    final overdue = DateTime.now().isAfter(_a.dueDate);
    return LiveClassCard(
      margin: const EdgeInsets.all(14),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const LiveClassIconBadge(icon: Icons.assignment_rounded, size: 44),
              const SizedBox(width: 12),
              Expanded(child: Text(_a.title, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 17))),
            ],
          ),
          if (_a.description.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(_a.description, style: const TextStyle(fontSize: 13.5)),
          ],
          const SizedBox(height: 12),
          Row(
            children: [
              Icon(Icons.event_rounded, size: 16, color: overdue ? LiveClassColors.danger : Colors.grey.shade600),
              const SizedBox(width: 6),
              Text(
                // FIX (timezone bug): _a.dueDate comes from the API as a UTC
              // DateTime (see liveclass_models.dart's parsing). Raw
              // DateFormat.format() reads UTC clock fields directly — a
              // student in IST could see a deadline hours off from what
              // the teacher actually set, and be marked late incorrectly.
              // Switched to the shared, timezone-safe liveClassFmtDateTime()
              // helper (same fix already applied elsewhere in the module).
              'Due ${liveClassFmtDateTime(_a.dueDate, context)}',
                style: TextStyle(fontSize: 12.5, color: overdue ? LiveClassColors.danger : Colors.grey.shade700, fontWeight: overdue ? FontWeight.w600 : FontWeight.normal),
              ),
              const Spacer(),
              Text('Max score: ${_a.maxScore}', style: TextStyle(fontSize: 12.5, color: Colors.grey.shade700)),
            ],
          ),
          if (_a.attachment != null) ...[
            const SizedBox(height: 8),
            TextButton.icon(
              style: TextButton.styleFrom(foregroundColor: LiveClassColors.navy),
              onPressed: () => _openFile(_a.attachment, name: 'assignment_${_a.id}_attachment'),
              icon: const Icon(Icons.attach_file_rounded, size: 18),
              label: const Text('View attachment'),
            ),
          ],
        ],
      ),
    );
  }

  Widget _studentBody() {
    final mine = _submissions.isNotEmpty ? _submissions.first : null;
    return ListView(
      children: [
        _brief(),
        if (mine == null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14),
            child: SizedBox(
              width: double.infinity,
              child: ElevatedButton.icon(
                style: ElevatedButton.styleFrom(
                  backgroundColor: LiveClassColors.navy,
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                ),
                onPressed: _submitting ? null : _submit,
                icon: _submitting
                    ? const SizedBox(height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : const Icon(Icons.upload_file_rounded),
                label: Text(_submitting ? 'Submitting…' : 'Submit assignment'),
              ),
            ),
          )
        else
          LiveClassCard(
            margin: const EdgeInsets.symmetric(horizontal: 14),
            padding: EdgeInsets.zero,
            child: ListTile(
              contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
              leading: LiveClassIconBadge(
                icon: mine.score != null ? Icons.grade_rounded : Icons.hourglass_top_rounded,
                size: 40,
                color: mine.score != null ? Colors.amber.shade700 : Colors.grey.shade400,
                gradient: null,
              ),
              title: Text('Submitted ${liveClassFmtDateTime(mine.submittedAt, context)}', style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
              subtitle: Text(
                mine.score != null
                    ? 'Score: ${mine.score}/${_a.maxScore}${mine.feedback.isNotEmpty ? "\n${mine.feedback}" : ""}'
                    : mine.isLate
                        ? 'Submitted late · awaiting grading'
                        : 'Awaiting grading',
                style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
              ),
              isThreeLine: mine.score != null && mine.feedback.isNotEmpty,
              trailing: mine.file != null
                  ? IconButton(
                      icon: const Icon(Icons.open_in_new_rounded, color: LiveClassColors.navy),
                      onPressed: () => _openFile(mine.file, name: 'submission_${mine.id}'),
                    )
                  : null,
            ),
          ),
        const SizedBox(height: 16),
      ],
    );
  }

  Widget _teacherBody() {
    if (_submissions.isEmpty) {
      return ListView(
        children: [
          _brief(),
          const SizedBox(height: 60),
          Icon(Icons.inbox_outlined, size: 40, color: Colors.grey.shade400),
          const SizedBox(height: 10),
          Center(child: Text('No submissions yet', style: TextStyle(color: Colors.grey.shade500))),
        ],
      );
    }
    return ListView(
      children: [
        _brief(),
        ..._submissions.map((s) => LiveClassCard(
              margin: const EdgeInsets.only(left: 14, right: 14, bottom: 8),
              padding: EdgeInsets.zero,
              child: ListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                leading: CircleAvatar(
                  backgroundColor: LiveClassColors.navy.withValues(alpha: 0.08),
                  foregroundColor: LiveClassColors.navy,
                  child: Text(s.student.fullName.isNotEmpty ? s.student.fullName[0] : '?'),
                ),
                title: Text(s.student.fullName, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
                subtitle: Text(
                  // FIX (timezone bug): same as the due-date note above —
                  // s.submittedAt is UTC from the API.
                  '${liveClassFmtDateTime(s.submittedAt, context)}'
                  '${s.isLate ? " · Late" : ""}'
                  '${s.score != null ? " · Score ${s.score}/${_a.maxScore}" : " · Not graded"}',
                  style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600),
                ),
                onTap: () => s.file != null ? _openFile(s.file, name: 'submission_${s.id}') : null,
                trailing: TextButton(
                  style: TextButton.styleFrom(foregroundColor: LiveClassColors.navy),
                  onPressed: () => _openGradeDialog(s),
                  child: Text(s.score != null ? 'Regrade' : 'Grade'),
                ),
              ),
            )),
        const SizedBox(height: 12),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(widget.canManage ? 'Submissions' : 'Assignment'),
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : widget.canManage
                    ? _teacherBody()
                    : _studentBody(),
      ),
    );
  }
}