// ============================================================
// LIVECLASS — MODERATION SCREEN (host/co-teacher/moderator only)
//
// Backend surface used: GET /participants/?session=, POST
// /sessions/{id}/kick/{user_id}/, POST /sessions/{id}/mute/{user_id}/,
// GET /chat-message-reports/?session=, POST
// /chat-message-reports/{id}/review/, GET /classroom-reports/?classroom=,
// POST /classroom-reports/{id}/review/.
//
// Three tabs: live participants (kick/mute), chat reports queue,
// classroom-abuse reports queue. Only shown to a session/classroom's
// teacher/co-teacher/moderator/staff — gate the entry point to this
// screen on that role, the backend re-checks it anyway on every call.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';
import '../models/liveclass_models.dart';

class ModerationScreen extends StatefulWidget {
  final LiveClassApi api;
  final int sessionId;
  final int classroomId;
  const ModerationScreen({super.key, required this.api, required this.sessionId, required this.classroomId});

  @override
  State<ModerationScreen> createState() => _ModerationScreenState();
}

class _ModerationScreenState extends State<ModerationScreen> with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 3, vsync: this);

  List<SessionParticipant> _participants = const [];
  List<Map<String, dynamic>> _chatReports = const [];
  List<Map<String, dynamic>> _classroomReports = const [];
  bool _loading = true;
  Object? _error;

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
      final results = await Future.wait([
        widget.api.participants(widget.sessionId),
        widget.api.chatMessageReports(sessionId: widget.sessionId),
        widget.api.classroomReports(classroomId: widget.classroomId, status: 'pending'),
      ]);
      setState(() {
        _participants = (results[0] as List).map((e) => SessionParticipant.fromJson(e as Map<String, dynamic>)).toList();
        _chatReports = (results[1] as List).cast<Map<String, dynamic>>();
        _classroomReports = (results[2] as List).cast<Map<String, dynamic>>();
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.moderationTitle),
      body: Column(children: [
        TabBar(
          controller: _tabs,
          labelColor: cs.primary,
          unselectedLabelColor: cs.onSurfaceVariant,
          tabs: [
            Tab(text: t.participantsTab),
            Tab(text: t.chatReportsTab),
            Tab(text: t.classroomReportsTab),
          ],
        ),
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator())
              : _error != null
                  ? ErrorStateWidget(title: t.couldNotLoadModerationData, retryLabel: t.retry, onRetry: _load)
                  : TabBarView(controller: _tabs, children: [
                      _buildParticipants(t, cs),
                      _buildChatReports(t, cs),
                      _buildClassroomReports(t, cs),
                    ]),
        ),
      ]),
    );
  }

  Widget _buildParticipants(AppLocalizations t, ColorScheme cs) {
    if (_participants.isEmpty) return EmptyStateWidget(title: t.noParticipantsYet, icon: Icons.people_outline_rounded);
    return ListView(children: _participants.map((p) => LsCard(
          margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
          child: Row(children: [
            CircleAvatar(radius: 16, backgroundColor: cs.surfaceVariant, child: Text(p.userName.isNotEmpty ? p.userName[0].toUpperCase() : '?')),
            const SizedBox(width: 10),
            Expanded(child: Text(p.userName, style: TextStyle(fontSize: 13, color: cs.onSurface))),
            if (p.handRaised) const Padding(padding: EdgeInsets.only(right: 6), child: Icon(Icons.back_hand_rounded, size: 16, color: Colors.orange)),
            IconButton(
              icon: Icon(p.muted ? Icons.mic_off_rounded : Icons.mic_rounded, size: 20, color: p.muted ? cs.error : cs.onSurfaceVariant),
              onPressed: () async {
                await widget.api.muteParticipant(widget.sessionId, p.userId, muted: !p.muted);
                _load();
              },
            ),
            IconButton(
              icon: Icon(Icons.person_remove_rounded, size: 20, color: cs.error),
              onPressed: () async {
                await widget.api.kickParticipant(widget.sessionId, p.userId);
                _load();
              },
            ),
          ]),
        )).toList());
  }

  Widget _buildChatReports(AppLocalizations t, ColorScheme cs) {
    if (_chatReports.isEmpty) return EmptyStateWidget(title: t.noChatReportsPending, icon: Icons.shield_outlined);
    return ListView(children: _chatReports.map((r) => LsCard(
          margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(r['reason']?.toString() ?? '', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 12.5, color: cs.onSurface)),
            if ((r['note'] ?? '').toString().isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(r['note'].toString(), style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
            ],
            const SizedBox(height: 8),
            Row(children: [
              Expanded(child: LsOutlineButton(label: t.dismissCta, onPressed: () async {
                await widget.api.reviewChatMessageReport(r['id'] as int, 'dismissed');
                _load();
              })),
              const SizedBox(width: 8),
              Expanded(child: LsPrimaryButton(label: t.actionCta, expanded: true, onPressed: () async {
                await widget.api.reviewChatMessageReport(r['id'] as int, 'actioned');
                _load();
              })),
            ]),
          ]),
        )).toList());
  }

  Widget _buildClassroomReports(AppLocalizations t, ColorScheme cs) {
    if (_classroomReports.isEmpty) return EmptyStateWidget(title: t.noClassroomReportsPending, icon: Icons.shield_outlined);
    return ListView(children: _classroomReports.map((r) => LsCard(
          margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            LsStatusChip(label: r['reason']?.toString().toUpperCase() ?? '', color: cs.error),
            const SizedBox(height: 6),
            Text(r['description']?.toString() ?? '', style: TextStyle(fontSize: 12.5, color: cs.onSurface)),
          ]),
        )).toList());
  }
}
