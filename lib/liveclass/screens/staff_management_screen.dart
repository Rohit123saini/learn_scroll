// lib/liveclass/screens/staff_management_screen.dart
//
// Screen 19 — Staff Management (see LIVECLASS_SCREEN_ARCHITECTURE.md §19).
// Reached from Classroom Detail's manage sheet, owner/admin only.
//
// API: `staff/` CRUD, scoped by `?classroom=`.
//   - Add: POST staff/ with {classroom, user_id, role}. The backend only
//     accepts a numeric user id (no user-search endpoint exists in this
//     API today), so the add form asks for the id directly — same
//     limitation the join-requests/certificates flows have.
//   - Update role: PATCH staff/{id}/ {role}.
//   - Remove: DELETE staff/{id}/.
//
// Now on the shared LiveClass design system (liveclass_theme.dart).

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

String _roleLabel(String role) {
  switch (role) {
    case StaffRole.coTeacher:
      return 'Co-Teacher';
    case StaffRole.moderator:
      return 'Moderator';
    case StaffRole.ta:
      return 'Teaching Assistant';
    default:
      return role;
  }
}

// ===========================================================================
// SCREEN
// ===========================================================================
class StaffManagementScreen extends StatefulWidget {
  final int classroomId;
  final String classroomTitle;
  const StaffManagementScreen({super.key, required this.classroomId, this.classroomTitle = ''});

  @override
  State<StaffManagementScreen> createState() => _StaffManagementScreenState();
}

class _StaffManagementScreenState extends State<StaffManagementScreen> {
  List<ClassroomStaff> _staff = [];
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
      final res = await LiveClassApi.staff.list(widget.classroomId);
      if (!mounted) return;
      setState(() {
        _staff = res.results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load staff list.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _openAddSheet() async {
    // FIX (memory leak): this controller used to be created here and never
    // disposed — every open+close of this sheet leaked one
    // TextEditingController for the lifetime of the app. try/finally
    // guarantees disposal on every exit path (submitted, cancelled, or
    // dismissed).
    final userIdCtrl = TextEditingController();
    try {
      return await _showAddSheet(userIdCtrl);
    } finally {
      userIdCtrl.dispose();
    }
  }

  Future<void> _showAddSheet(TextEditingController userIdCtrl) async {
    String role = StaffRole.ta;
    bool submitting = false;

    final added = await showModalBottomSheet<bool>(
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
              await LiveClassApi.staff.add(classroomId: widget.classroomId, userId: userId, role: role);
              if (!mounted) return;
              Navigator.pop(ctx, true);
            } on LiveClassApiException catch (e) {
              setSheetState(() => submitting = false);
              _snack(e.message);
            } catch (_) {
              setSheetState(() => submitting = false);
              _snack('Could not add — please try again.');
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
                const Text('Add Staff', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: LiveClassColors.navy)),
                const SizedBox(height: 4),
                Text("Get the user's User ID from their app profile.", style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
                const SizedBox(height: 14),
                TextField(controller: userIdCtrl, keyboardType: TextInputType.number, decoration: liveClassInputDecoration('User ID')),
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  value: role,
                  decoration: liveClassInputDecoration(''),
                  items: const {
                    StaffRole.coTeacher: 'Co-Teacher',
                    StaffRole.moderator: 'Moderator',
                    StaffRole.ta: 'Teaching Assistant',
                  }.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
                  onChanged: (v) => setSheetState(() => role = v!),
                ),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: LiveClassColors.navy,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                    ),
                    onPressed: submitting ? null : submit,
                    child: submitting
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : const Text('Add Staff'),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );
    if (added == true) {
      _snack('Staff added.');
      _load();
    }
  }

  Future<void> _changeRole(ClassroomStaff s) async {
    final newRole = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(LiveClassRadius.sheet))),
      builder: (ctx) => SafeArea(
        child: Wrap(
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(20, 16, 20, 4),
              child: Text('Change Role', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: LiveClassColors.navy)),
            ),
            for (final r in [StaffRole.coTeacher, StaffRole.moderator, StaffRole.ta])
              ListTile(title: Text(_roleLabel(r)), onTap: () => Navigator.pop(ctx, r)),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
    if (newRole == null || newRole == s.role) return;
    try {
      await LiveClassApi.staff.updateRole(s.id, newRole);
      _snack('Role updated.');
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not update.');
    }
  }

  Future<void> _confirmRemove(ClassroomStaff s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Remove Staff?'),
        content: Text('${s.user.fullName.isNotEmpty ? s.user.fullName : s.user.username} will be removed from the team.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            style: TextButton.styleFrom(foregroundColor: LiveClassColors.danger),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await LiveClassApi.staff.remove(s.id);
      _snack('Staff removed.');
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not remove.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(widget.classroomTitle.isNotEmpty ? 'Staff — ${widget.classroomTitle}' : 'Staff'),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: LiveClassColors.navy,
        onPressed: _openAddSheet,
        icon: const Icon(Icons.person_add_alt_1_rounded),
        label: const Text('Add Staff'),
      ),
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _staff.isEmpty
                    ? const LiveClassEmptyState(icon: Icons.group_outlined, title: 'No staff added yet.')
                    : ListView.builder(
                        padding: const EdgeInsets.fromLTRB(16, 14, 16, 90),
                        itemCount: _staff.length,
                        itemBuilder: (_, i) => _staffTile(_staff[i]),
                      ),
      ),
    );
  }

  Widget _staffTile(ClassroomStaff s) {
    return LiveClassCard(
      padding: const EdgeInsets.all(12),
      margin: const EdgeInsets.only(bottom: 10),
      child: Row(
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: const BoxDecoration(gradient: LiveClassColors.gradient, shape: BoxShape.circle),
            alignment: Alignment.center,
            child: Text(
              (s.user.fullName.isNotEmpty ? s.user.fullName : s.user.username).substring(0, 1).toUpperCase(),
              style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(s.user.fullName.isNotEmpty ? s.user.fullName : s.user.username,
                    style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
                const SizedBox(height: 2),
                Text(_roleLabel(s.role), style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
              ],
            ),
          ),
          PopupMenuButton<String>(
            onSelected: (v) {
              if (v == 'role') _changeRole(s);
              if (v == 'remove') _confirmRemove(s);
            },
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'role', child: Text('Change Role')),
              PopupMenuItem(value: 'remove', child: Text('Remove')),
            ],
          ),
        ],
      ),
    );
  }
}