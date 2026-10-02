// ============================================================
// TUITIONCLASS — PARENT PORTAL (FEATURE: optional Parent, Section 4)
//
// Backend surface used (this app only — the parent's OWN dashboard/
// report-card view lives in a different app, `message/views_parent.py`,
// which is outside `tuitionclass/urls.py` and not covered here):
//   POST /tuitionclass/classrooms/{id}/participants/{userId}/parent-code/ (teacher, single)
//   POST /tuitionclass/classrooms/{id}/parent-codes/bulk/                 (teacher, all pending)
//   GET  /tuitionclass/classrooms/{id}/parent-queries/                    (teacher inbox)
//   POST /tuitionclass/parent-queries/{id}/reply/                        (teacher reply)
//   POST /tuitionclass/sessions/{id}/parent-join/  (UNAUTHENTICATED, IP-throttled)
//
// Three pieces:
//  1. ParentCodeGeneratorTile — a button a teacher taps on a
//     participant's row to hand them a shareable parent-access code.
//  2. BulkParentCodeButton — one click, sends the link to every student
//     whose parent_status is still `none` (Classroom.parents_enabled must
//     be on; per D7, skipping never blocks join/attendance/payment).
//  3. ParentQueriesScreen — teacher-side inbox for parent questions.
//  4. ParentJoinScreen — the unauthenticated screen a parent lands on
//     from their signed link; no login, just the token in the URL.
//
// NOTE (P3.2 port): l10n keys referenced below don't exist in app_en.arb /
// app_hi.arb yet — added in P4 alongside the full rename.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../services/tuitionclass_api_service.dart';

class ParentCodeGeneratorTile extends StatelessWidget {
  final int classroomId;
  final int userId;
  const ParentCodeGeneratorTile({super.key, required this.classroomId, required this.userId});

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return LsOutlineButton(
      label: t.generateParentCodeCta,
      icon: Icons.family_restroom_rounded,
      onPressed: () async {
        try {
          final res = await TuitionClassApi.parents.generateCode(classroomId, userId);
          final code = res['code']?.toString() ?? res['link']?.toString() ?? '';
          if (context.mounted) {
            showDialog(
              context: context,
              builder: (context) => AlertDialog(
                title: Text(t.parentCodeGeneratedTitle),
                content: SelectableText(code),
                actions: [TextButton(onPressed: () => Navigator.pop(context), child: Text(t.doneCta))],
              ),
            );
          }
        } catch (e) {
          if (context.mounted) lsSnack(context, e.toString(), error: true);
        }
      },
    );
  }
}

/// One-click sibling of [ParentCodeGeneratorTile] — sends every enrolled
/// student in this classroom their own parent-add link in one tap,
/// instead of a teacher generating one participant at a time. Drop this
/// on the classroom's roster/manage screen, e.g. next to a "Notices" or
/// "Manage students" action.
class BulkParentCodeButton extends StatefulWidget {
  final int classroomId;
  const BulkParentCodeButton({super.key, required this.classroomId});

  @override
  State<BulkParentCodeButton> createState() => _BulkParentCodeButtonState();
}

class _BulkParentCodeButtonState extends State<BulkParentCodeButton> {
  bool _sending = false;

  Future<void> _send() async {
    setState(() => _sending = true);
    try {
      final res = await TuitionClassApi.parents.bulkGenerateCodes(widget.classroomId);
      final sentCount = res['sent_count'] ?? 0;
      final total = res['total_students'] ?? 0;
      if (mounted) lsSnack(context, AppLocalizations.of(context)!.parentLinkSentMessage(int.tryParse('$sentCount') ?? 0, int.tryParse('$total') ?? 0));
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return LsPrimaryButton(
      label: AppLocalizations.of(context)!.parentSendToAllCta,
      icon: Icons.groups_rounded,
      loading: _sending,
      expanded: false,
      onPressed: _sending ? null : _send,
    );
  }
}

/// Roster-row widget for the classroom manage screen: shows the student's
/// current parent_status and offers Add-parent / Skip actions when it's
/// still `none` (D7 — always optional, skip never blocks anything).
class ParentStatusChip extends StatefulWidget {
  final int classroomId;
  final int userId;
  final String initialStatus; // none | skipped | invited | linked
  final VoidCallback? onChanged;
  const ParentStatusChip({
    super.key,
    required this.classroomId,
    required this.userId,
    required this.initialStatus,
    this.onChanged,
  });

  @override
  State<ParentStatusChip> createState() => _ParentStatusChipState();
}

class _ParentStatusChipState extends State<ParentStatusChip> {
  late String _status = widget.initialStatus;
  bool _busy = false;

  Color _color(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    switch (_status) {
      case 'linked':
        return Colors.green;
      case 'invited':
        return Colors.orange;
      case 'skipped':
        return Colors.grey;
      default:
        return cs.primary;
    }
  }

  String _statusLabel(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    switch (_status) {
      case 'linked':
        return t.parentStatusLinked;
      case 'invited':
        return t.parentStatusInvited;
      case 'skipped':
        return t.parentStatusSkipped;
      default:
        return _status;
    }
  }

  Future<void> _skip() async {
    setState(() => _busy = true);
    try {
      await TuitionClassApi.parents.skip(widget.classroomId, widget.userId);
      if (mounted) setState(() => _status = 'skipped');
      widget.onChanged?.call();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _invite() async {
    setState(() => _busy = true);
    try {
      await TuitionClassApi.parents.invite(widget.classroomId, widget.userId);
      if (mounted) setState(() => _status = 'invited');
      widget.onChanged?.call();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_status == 'none') {
      if (_busy) return const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2));
      return Row(mainAxisSize: MainAxisSize.min, children: [
        TextButton(onPressed: _invite, child: Text(AppLocalizations.of(context)!.addParentCta)),
        TextButton(onPressed: _skip, child: Text(AppLocalizations.of(context)!.skipCta)),
      ]);
    }
    return Chip(
      label: Text(_statusLabel(context), style: const TextStyle(fontSize: 11)),
      backgroundColor: _color(context).withOpacity(0.12),
      labelStyle: TextStyle(color: _color(context)),
      visualDensity: VisualDensity.compact,
    );
  }
}

class ParentQueriesScreen extends StatefulWidget {
  final int classroomId;
  const ParentQueriesScreen({super.key, required this.classroomId});

  @override
  State<ParentQueriesScreen> createState() => _ParentQueriesScreenState();
}

class _ParentQueriesScreenState extends State<ParentQueriesScreen> {
  List<dynamic> _threads = const [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (!mounted) return;
    setState(() => _loading = true);
    final data = await TuitionClassApi.parents.queries(widget.classroomId);
    if (!mounted) return;
    setState(() {
      _threads = data;
      _loading = false;
    });
  }

  Future<void> _reply(String id) async {
    final t = AppLocalizations.of(context)!;
    final ctrl = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(t.replyToParentTitle),
        content: TextField(controller: ctrl, maxLines: 3),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: Text(t.cancelCta)),
          TextButton(onPressed: () => Navigator.pop(context, true), child: Text(t.sendCta)),
        ],
      ),
    );
    if (ok == true && ctrl.text.trim().isNotEmpty) {
      await TuitionClassApi.parents.reply(id, ctrl.text.trim());
      _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.parentQueriesTitle),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _threads.isEmpty
              ? EmptyStateWidget(title: t.noParentQueriesYet, icon: Icons.family_restroom_outlined)
              : ListView(children: _threads.map((th) {
                  final m = th as Map<String, dynamic>;
                  return LsCard(
                    margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(m['text']?.toString() ?? '', style: TextStyle(fontSize: 13, color: cs.onSurface)),
                      const SizedBox(height: 8),
                      Align(
                        alignment: Alignment.centerRight,
                        child: TextButton(onPressed: () => _reply(m['id'].toString()), child: Text(t.replyCta)),
                      ),
                    ]),
                  );
                }).toList()),
    );
  }
}

/// No auth token needed — the parent's whole identity is the signed
/// `parent_token` from their link. IP-throttled server-side, so keep
/// this screen simple: one action, one result, no retry storm.
class ParentJoinScreen extends StatefulWidget {
  final int sessionId;
  final String parentToken;
  const ParentJoinScreen({super.key, required this.sessionId, required this.parentToken});

  @override
  State<ParentJoinScreen> createState() => _ParentJoinScreenState();
}

class _ParentJoinScreenState extends State<ParentJoinScreen> {
  bool _loading = true;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _join();
  }

  Future<void> _join() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      await TuitionClassApi.sessions.parentJoin(widget.sessionId, widget.parentToken);
      // Hand the returned observer-role LiveKit token/url to the same
      // video surface live_session_screen.dart uses, in read-only mode.
      setState(() => _loading = false);
    } catch (e) {
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: Colors.black,
      body: Center(
        child: _loading
            ? const CircularProgressIndicator(color: Colors.white)
            : _error != null
                ? ErrorStateWidget(title: t.couldNotJoinSession, retryLabel: t.retry, onRetry: _join)
                : Text(t.parentObserverConnectedMessage, style: const TextStyle(color: Colors.white)),
      ),
    );
  }
}
