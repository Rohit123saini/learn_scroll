// ============================================================
// LIVECLASS — PARENT PORTAL
//
// Backend surface used (this app only — the parent's OWN dashboard/
// report-card view lives in a different app, `message/views_parent.py`,
// which is outside `liveclass/urls.py` and not covered here):
//   POST /classrooms/{id}/participants/{user_id}/parent-code/  (teacher)
//   GET  /classrooms/{id}/parent-queries/                       (teacher)
//   POST /parent-queries/{id}/reply/                            (teacher)
//   POST /sessions/{id}/parent-join/  (UNAUTHENTICATED, IP-throttled)
//
// Three pieces:
//  1. ParentCodeGeneratorTile — a button a teacher taps on a
//     participant's row to hand them a shareable parent-access code.
//  2. ParentQueriesScreen — teacher-side inbox for parent questions.
//  3. ParentJoinScreen — the unauthenticated screen a parent lands on
//     from their signed link; no login, just the token in the URL.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';

class ParentCodeGeneratorTile extends StatelessWidget {
  final LiveClassApi api;
  final int classroomId;
  final int userId;
  const ParentCodeGeneratorTile({super.key, required this.api, required this.classroomId, required this.userId});

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return LsOutlineButton(
      label: t.generateParentCodeCta,
      icon: Icons.family_restroom_rounded,
      onPressed: () async {
        try {
          final res = await api.generateParentCode(classroomId, userId);
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

class ParentQueriesScreen extends StatefulWidget {
  final LiveClassApi api;
  final int classroomId;
  const ParentQueriesScreen({super.key, required this.api, required this.classroomId});

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
    setState(() => _loading = true);
    final data = await widget.api.parentQueries(widget.classroomId);
    setState(() {
      _threads = data;
      _loading = false;
    });
  }

  Future<void> _reply(int id) async {
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
      await widget.api.replyParentQuery(id, ctrl.text.trim());
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
                        child: TextButton(onPressed: () => _reply(m['id'] as int), child: Text(t.replyCta)),
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
  final LiveClassApi api;
  final int sessionId;
  final String parentToken;
  const ParentJoinScreen({super.key, required this.api, required this.sessionId, required this.parentToken});

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
      await widget.api.parentJoin(widget.sessionId, widget.parentToken);
      // Hand the returned observer-role LiveKit token/url to the same
      // video surface `live_session_screen.dart` uses, in read-only mode.
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
