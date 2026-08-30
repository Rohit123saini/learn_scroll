// lib/liveclass/screens/banned_students_screen.dart
//
// Banned Students — reached from Classroom Detail's manage sheet,
// owner/admin only. New screen: the backend
// (ClassroomViewSet.ban/bans/unban in views.py) already fully implemented
// this — permanently ban a student, best-effort kick them from any live
// session, reject their pending join requests, refund their active paid
// pass — but no screen anywhere in the module ever called it.
//
// Modeled directly on staff_management_screen.dart (same list/add-sheet/
// confirm-remove shape) so it matches the rest of the module instead of
// introducing a new pattern.
//
// API: `classrooms/{id}/ban/`, `classrooms/{id}/bans/`,
// `classrooms/{id}/unban/{student_id}/`.
//   - Ban: POST classrooms/{id}/ban/ with {student_id, reason}. Same
//     numeric-user-id limitation as Staff/Join-Requests/Certificates —
//     there's no user-search endpoint in this API today.
//   - List: GET classrooms/{id}/bans/ — NOT paginated on the backend
//     (plain array), unlike most other list calls in this module.
//   - Lift: POST classrooms/{id}/unban/{student_id}/.
//
// On the shared LiveClass design system (liveclass_theme.dart).

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

class BannedStudentsScreen extends StatefulWidget {
  final int classroomId;
  final String classroomTitle;
  const BannedStudentsScreen({super.key, required this.classroomId, this.classroomTitle = ''});

  @override
  State<BannedStudentsScreen> createState() => _BannedStudentsScreenState();
}

class _BannedStudentsScreenState extends State<BannedStudentsScreen> {
  List<ClassroomBan> _bans = [];
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
      final bans = await LiveClassApi.classrooms.bans(widget.classroomId);
      if (!mounted) return;
      setState(() {
        _bans = bans;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load banned students.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _openBanSheet() async {
    // Same try/finally disposal pattern as StaffManagementScreen — created
    // fresh per open, disposed on every exit path (submitted/cancelled/
    // dismissed), never leaked as a class-level field.
    final userIdCtrl = TextEditingController();
    final reasonCtrl = TextEditingController();
    try {
      return await _showBanSheet(userIdCtrl, reasonCtrl);
    } finally {
      userIdCtrl.dispose();
      reasonCtrl.dispose();
    }
  }

  Future<void> _showBanSheet(TextEditingController userIdCtrl, TextEditingController reasonCtrl) async {
    bool submitting = false;

    final banned = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(LiveClassRadius.sheet))),
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          Future<void> submit() async {
            final userId = int.tryParse(userIdCtrl.text.trim());
            if (userId == null) {
              _snack('Enter a valid numeric User ID.');
              return;
            }
            setSheetState(() => submitting = true);
            try {
              await LiveClassApi.classrooms.ban(
                classroomId: widget.classroomId,
                studentId: userId,
                reason: reasonCtrl.text.trim(),
              );
              if (!mounted) return;
              Navigator.pop(ctx, true);
            } on LiveClassApiException catch (e) {
              setSheetState(() => submitting = false);
              _snack(e.message);
            } catch (_) {
              setSheetState(() => submitting = false);
              _snack('Could not ban — please try again.');
            }
          }

          return Padding(
            padding: EdgeInsets.only(left: 20, right: 20, top: 20, bottom: MediaQuery.of(ctx).viewInsets.bottom + 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Center(
                  child: Container(
                    width: 36,
                    height: 4,
                    margin: const EdgeInsets.only(bottom: 16),
                    decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(2)),
                  ),
                ),
                const Text('Ban Student', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: LiveClassColors.navy)),
                const SizedBox(height: 4),
                Text("Get the user's User ID from their app profile.", style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
                const SizedBox(height: 4),
                Text(
                  'This kicks them from any live session, rejects pending join requests, '
                  'and refunds their active pass. This cannot be undone from within the app '
                  'other than unbanning — they would need to buy a fresh pass to return.',
                  style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600),
                ),
                const SizedBox(height: 14),
                TextField(controller: userIdCtrl, keyboardType: TextInputType.number, decoration: liveClassInputDecoration('User ID')),
                const SizedBox(height: 12),
                TextField(
                  controller: reasonCtrl,
                  maxLines: 2,
                  decoration: liveClassInputDecoration('Reason (optional)'),
                ),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: LiveClassColors.danger,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                    ),
                    onPressed: submitting ? null : submit,
                    child: submitting
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : const Text('Ban Student'),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );
    if (banned == true) {
      _snack('Student banned.');
      _load();
    }
  }

  Future<void> _confirmUnban(ClassroomBan ban) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Lift Ban?'),
        content: Text(
          '${ban.student.fullName.isNotEmpty ? ban.student.fullName : ban.student.username} will be able to '
          'request to join this classroom again. Their refunded pass is not restored — they would pay again.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            style: TextButton.styleFrom(foregroundColor: LiveClassColors.navy),
            child: const Text('Lift Ban'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await LiveClassApi.classrooms.unban(classroomId: widget.classroomId, studentId: ban.student.id);
      _snack('Ban lifted.');
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not lift ban.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(widget.classroomTitle.isNotEmpty ? 'Banned Students — ${widget.classroomTitle}' : 'Banned Students'),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: LiveClassColors.danger,
        onPressed: _openBanSheet,
        icon: const Icon(Icons.block_flipped),
        label: const Text('Ban Student'),
      ),
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _bans.isEmpty
                    ? const LiveClassEmptyState(icon: Icons.block_flipped, title: 'No students banned from this classroom.')
                    : ListView.builder(
                        padding: const EdgeInsets.fromLTRB(16, 14, 16, 90),
                        itemCount: _bans.length,
                        itemBuilder: (_, i) => _banTile(_bans[i]),
                      ),
      ),
    );
  }

  Widget _banTile(ClassroomBan ban) {
    final name = ban.student.fullName.isNotEmpty ? ban.student.fullName : ban.student.username;
    return LiveClassCard(
      padding: const EdgeInsets.all(12),
      margin: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(color: LiveClassColors.dangerBg, shape: BoxShape.circle),
            alignment: Alignment.center,
            child: Text(
              name.substring(0, 1).toUpperCase(),
              style: const TextStyle(color: LiveClassColors.danger, fontWeight: FontWeight.bold),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(name, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
                if (ban.reason.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(ban.reason, style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
                ],
                const SizedBox(height: 2),
                Text(
                  ban.bannedBy != null
                      ? 'Banned by ${ban.bannedBy!.fullName.isNotEmpty ? ban.bannedBy!.fullName : ban.bannedBy!.username} · ${liveClassFmtDate(ban.createdAt, context)}'
                      : liveClassFmtDate(ban.createdAt, context),
                  style: TextStyle(fontSize: 11, color: Colors.grey.shade500),
                ),
              ],
            ),
          ),
          TextButton(
            onPressed: () => _confirmUnban(ban),
            child: const Text('Unban'),
          ),
        ],
      ),
    );
  }
}