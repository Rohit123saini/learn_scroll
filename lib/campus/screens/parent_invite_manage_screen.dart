import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../widgets/ls_ui.dart';
import '../services/campus_service.dart';

// ============================================================
// CAMPUS — "Add Parent" automation (admin/principal only)
//
// Three actions, matching the three requested flows:
//   1. Send to ALL students in one click   -> CampusService.parentInviteBulk
//   2. Add for ONE specific student        -> CampusService.parentInviteSingle
//   3. Send a notice to everyone           -> CampusService.postNotice
//      (campus-only scope = whole campus; already fans out push/bell —
//      see campus/views.py NoticeViewSet.perform_create)
//
// Entry point: push this from wherever the admin/staff panel lives, e.g.
//   Navigator.push(context, MaterialPageRoute(
//     builder: (_) => ParentInviteManageScreen(campusId: campus.id, sessionId: currentSessionId),
//   ));
// ============================================================

class ParentInviteManageScreen extends StatefulWidget {
  final String campusId;

  /// Needed only for the "send notice" action (Notice.session is required
  /// server-side) — pass the campus's current AcademicSession id.
  final String sessionId;

  const ParentInviteManageScreen({super.key, required this.campusId, required this.sessionId});

  @override
  State<ParentInviteManageScreen> createState() => _ParentInviteManageScreenState();
}

class _ParentInviteManageScreenState extends State<ParentInviteManageScreen> {
  bool _bulkSending = false;

  Future<void> _sendToAll() async {
    setState(() => _bulkSending = true);
    try {
      final result = await CampusService.parentInviteBulk(campusId: widget.campusId);
      final sentCount = result['sent_count'] ?? 0;
      final total = result['total_students'] ?? 0;
      if (mounted) {
        lsSnack(context, 'Parent-add link sent to $sentCount of $total students.');
      }
    } on CampusApiException catch (e) {
      if (mounted) lsSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _bulkSending = false);
    }
  }

  Future<void> _addForSpecificStudent() async {
    final studentId = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => const _StudentIdSheet(),
    );
    if (studentId == null || studentId.trim().isEmpty) return;
    if (!mounted) return;

    try {
      final result = await CampusService.parentInviteSingle(
        campusId: widget.campusId,
        studentId: studentId.trim(),
      );
      if (mounted) await _showLinkResult(result);
    } on CampusApiException catch (e) {
      if (mounted) lsSnack(context, e.message, error: true);
    }
  }

  Future<void> _showLinkResult(Map<String, dynamic> result) async {
    final link = result['link']?.toString() ?? '';
    final studentName = result['student_name']?.toString() ?? 'Student';
    await showDialog(
      context: context,
      builder: (_) => AlertDialog(
        title: Text('Link ready — $studentName'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'The student was also notified in-app. You can copy/share this link directly:',
              style: TextStyle(fontSize: 12.5),
            ),
            const SizedBox(height: 10),
            SelectableText(link, style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () {
              Clipboard.setData(ClipboardData(text: link));
              lsSnack(context, 'Link copied.');
            },
            child: const Text('Copy'),
          ),
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Done')),
        ],
      ),
    );
  }

  Future<void> _sendNoticeToEveryone() async {
    final draft = await showModalBottomSheet<_NoticeDraft>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => const _NoticeComposeSheet(),
    );
    if (draft == null) return;
    try {
      await CampusService.postNotice(
        campusId: widget.campusId,
        sessionId: widget.sessionId,
        title: draft.title,
        body: draft.body,
        // No department/schoolClass/section -> scope = whole campus.
      );
      if (mounted) lsSnack(context, 'Notice sent to everyone in this campus.');
    } on CampusApiException catch (e) {
      if (mounted) lsSnack(context, e.message, error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: lsAppBar(context, title: 'Add Parent'),
      body: ListView(
        padding: const EdgeInsets.all(14),
        children: [
          _ActionCard(
            icon: Icons.groups_rounded,
            title: 'Send to all students',
            subtitle:
                'One click — every currently enrolled student gets their own parent-add link. '
                'They forward it to their parent; tapping it confirms the parent automatically.',
            buttonLabel: 'Send to all',
            loading: _bulkSending,
            onPressed: _sendToAll,
          ),
          const SizedBox(height: 12),
          _ActionCard(
            icon: Icons.person_add_alt_1_rounded,
            title: 'Add for a specific student',
            subtitle: 'Generate a link for just one student — useful if a parent missed the bulk link.',
            buttonLabel: 'Choose student',
            onPressed: _addForSpecificStudent,
          ),
          const SizedBox(height: 12),
          _ActionCard(
            icon: Icons.campaign_rounded,
            title: 'Send a notice to everyone',
            subtitle: 'One click — posts a campus-wide notice and pushes it to every student and linked parent.',
            buttonLabel: 'Compose notice',
            onPressed: _sendNoticeToEveryone,
          ),
          const SizedBox(height: 20),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4),
            child: Text(
              'Already-linked parents are listed separately — see "Parent Links".',
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
            ),
          ),
        ],
      ),
    );
  }
}

class _ActionCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final String buttonLabel;
  final bool loading;
  final VoidCallback onPressed;

  const _ActionCard({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.buttonLabel,
    required this.onPressed,
    this.loading = false,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return LsCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            CircleAvatar(radius: 18, backgroundColor: cs.primaryContainer, child: Icon(icon, size: 18, color: cs.onPrimaryContainer)),
            const SizedBox(width: 10),
            Expanded(child: Text(title, style: LsType.head(context, size: 14))),
          ]),
          const SizedBox(height: 8),
          Text(subtitle, style: TextStyle(fontSize: 12, height: 1.4, color: cs.onSurfaceVariant)),
          const SizedBox(height: 12),
          LsPrimaryButton(label: buttonLabel, loading: loading, onPressed: onPressed, expanded: false),
        ],
      ),
    );
  }
}

class _StudentIdSheet extends StatefulWidget {
  const _StudentIdSheet();

  @override
  State<_StudentIdSheet> createState() => _StudentIdSheetState();
}

class _StudentIdSheetState extends State<_StudentIdSheet> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(left: 16, right: 16, top: 18, bottom: MediaQuery.of(context).viewInsets.bottom + 18),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Student', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
          const SizedBox(height: 6),
          const Text(
            'Enter the student\'s user ID (from their roster row / profile).',
            style: TextStyle(fontSize: 12),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _controller,
            decoration: const InputDecoration(labelText: 'Student ID', border: OutlineInputBorder()),
            onSubmitted: (v) => Navigator.pop(context, v),
          ),
          const SizedBox(height: 14),
          LsPrimaryButton(
            label: 'Generate link',
            icon: Icons.link_rounded,
            onPressed: () => Navigator.pop(context, _controller.text),
          ),
        ],
      ),
    );
  }
}

class _NoticeDraft {
  final String title;
  final String body;
  _NoticeDraft(this.title, this.body);
}

class _NoticeComposeSheet extends StatefulWidget {
  const _NoticeComposeSheet();

  @override
  State<_NoticeComposeSheet> createState() => _NoticeComposeSheetState();
}

class _NoticeComposeSheetState extends State<_NoticeComposeSheet> {
  final _title = TextEditingController();
  final _body = TextEditingController();

  @override
  void dispose() {
    _title.dispose();
    _body.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(left: 16, right: 16, top: 18, bottom: MediaQuery.of(context).viewInsets.bottom + 18),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Notice — everyone in this campus', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
          const SizedBox(height: 12),
          TextField(controller: _title, decoration: const InputDecoration(labelText: 'Title', border: OutlineInputBorder())),
          const SizedBox(height: 12),
          TextField(
            controller: _body,
            maxLines: 4,
            decoration: const InputDecoration(labelText: 'Message', border: OutlineInputBorder()),
          ),
          const SizedBox(height: 14),
          LsPrimaryButton(
            label: 'Send to everyone',
            icon: Icons.campaign_rounded,
            onPressed: () {
              if (_title.text.trim().isEmpty || _body.text.trim().isEmpty) return;
              Navigator.pop(context, _NoticeDraft(_title.text.trim(), _body.text.trim()));
            },
          ),
        ],
      ),
    );
  }
}
