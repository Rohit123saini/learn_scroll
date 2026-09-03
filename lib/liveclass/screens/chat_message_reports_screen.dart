// lib/liveclass/screens/chat_message_reports_screen.dart
//
// "Reported Messages" — Phase 2, item 7. Backend
// (ChatMessageReportViewSet.review, in views.py) has been fully ready
// with a Dart caller (ChatMessageReportApi.review) since Pass 14, but
// no screen anywhere in the module ever surfaced the moderation queue —
// the manage sheet on Classroom Detail had no tile for it at all.
//
// CONFIRMED against views.py (ChatMessageReportViewSet.get_queryset):
// this is a SESSION-scoped queue, not classroom-scoped — there is no
// "all reports for this classroom" endpoint. So this screen is a
// classroom-scoped shell around a session picker: pick one of this
// classroom's sessions, then review that session's chat reports.
//
// Review actions are exactly two (CONFIRMED against
// ChatMessageReportViewSet.review — everything except 'pending' is a
// valid target status): 'actioned' (also soft-deletes the reported
// message server-side) and 'dismissed'. There is no separate
// admin-note field on this action — ChatMessageReportApi.review()
// already dropped that parameter for the same reason.
//
// On the shared LiveClass design system (liveclass_theme.dart).

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

const _kStatusPending = 'pending';
const _kStatusActioned = 'actioned';
const _kStatusDismissed = 'dismissed';

class ChatMessageReportsScreen extends StatefulWidget {
  final int classroomId;
  const ChatMessageReportsScreen({super.key, required this.classroomId});

  @override
  State<ChatMessageReportsScreen> createState() => _ChatMessageReportsScreenState();
}

class _ChatMessageReportsScreenState extends State<ChatMessageReportsScreen> {
  List<ClassSession> _sessions = [];
  int? _selectedSessionId;
  bool _sessionsLoading = true;
  String? _sessionsError;

  String _statusFilter = _kStatusPending;
  List<ChatMessageReport> _reports = [];
  bool _reportsLoading = false;
  String? _reportsError;
  final Set<int> _busyIds = {};

  @override
  void initState() {
    super.initState();
    _loadSessions();
  }

  Future<void> _loadSessions() async {
    setState(() {
      _sessionsLoading = true;
      _sessionsError = null;
    });
    try {
      final res = await LiveClassApi.sessions.list(classroomId: widget.classroomId);
      // Most recent first — that's almost always where a fresh report
      // will be sitting.
      final sessions = res.results.toList()..sort((a, b) => b.scheduledStart.compareTo(a.scheduledStart));
      if (!mounted) return;
      setState(() {
        _sessions = sessions;
        _selectedSessionId = sessions.isNotEmpty ? sessions.first.id : null;
        _sessionsLoading = false;
      });
      if (_selectedSessionId != null) _loadReports();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _sessionsLoading = false;
        _sessionsError = e is LiveClassApiException ? e.message : 'Could not load sessions.';
      });
    }
  }

  Future<void> _loadReports() async {
    final sessionId = _selectedSessionId;
    if (sessionId == null) return;
    setState(() {
      _reportsLoading = true;
      _reportsError = null;
    });
    try {
      final res = await LiveClassApi.chatMessageReports.list(sessionId: sessionId, status: _statusFilter);
      if (!mounted) return;
      setState(() {
        _reports = res.results;
        _reportsLoading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _reportsLoading = false;
        _reportsError = e is LiveClassApiException ? e.message : 'Could not load reports.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _review(ChatMessageReport r, String status) async {
    setState(() => _busyIds.add(r.id));
    try {
      await LiveClassApi.chatMessageReports.review(r.id, status: status);
      if (!mounted) return;
      setState(() {
        _busyIds.remove(r.id);
        _reports.removeWhere((x) => x.id == r.id);
      });
      _snack(status == _kStatusActioned ? 'Message removed and report actioned.' : 'Report dismissed.');
    } catch (e) {
      if (!mounted) return;
      setState(() => _busyIds.remove(r.id));
      _snack(e is LiveClassApiException ? e.message : 'Could not update this report — please try again.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar('Reported Messages'),
      body: _sessionsLoading
          ? const LiveClassLoading()
          : _sessionsError != null
              ? LiveClassErrorState(message: _sessionsError!, onRetry: _loadSessions)
              : _sessions.isEmpty
                  ? const LiveClassEmptyState(
                      icon: Icons.forum_outlined,
                      title: 'No sessions yet.',
                      subtitle: 'Chat reports show up here once this classroom has run a session.',
                    )
                  : _buildBody(),
    );
  }

  Widget _buildBody() {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 4),
          child: DropdownButtonFormField<int>(
            value: _selectedSessionId,
            decoration: liveClassInputDecoration('Session', label: 'Session'),
            isExpanded: true,
            items: _sessions
                .map((s) => DropdownMenuItem(
                      value: s.id,
                      child: Text(
                        '${liveClassFmtDate(s.scheduledStart)} · ${s.classroomTitle.isNotEmpty ? s.classroomTitle : "Session #${s.id}"}',
                        overflow: TextOverflow.ellipsis,
                      ),
                    ))
                .toList(),
            onChanged: (v) {
              if (v == null) return;
              setState(() => _selectedSessionId = v);
              _loadReports();
            },
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
          child: Row(
            children: [
              _statusChip('Pending', _kStatusPending),
              const SizedBox(width: 8),
              _statusChip('Actioned', _kStatusActioned),
              const SizedBox(width: 8),
              _statusChip('Dismissed', _kStatusDismissed),
            ],
          ),
        ),
        const SizedBox(height: 6),
        Expanded(
          child: RefreshIndicator(
            color: LiveClassColors.navy,
            onRefresh: _loadReports,
            child: _reportsLoading
                ? const LiveClassLoading()
                : _reportsError != null
                    ? LiveClassErrorState(message: _reportsError!, onRetry: _loadReports)
                    : _reports.isEmpty
                        ? const LiveClassEmptyState(
                            icon: Icons.mark_chat_read_outlined,
                            title: 'Nothing here.',
                            subtitle: 'No chat reports match this filter for the selected session.',
                          )
                        : ListView.builder(
                            padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
                            itemCount: _reports.length,
                            itemBuilder: (_, i) => _reportTile(_reports[i]),
                          ),
          ),
        ),
      ],
    );
  }

  Widget _statusChip(String label, String value) {
    final selected = _statusFilter == value;
    return ChoiceChip(
      label: Text(label),
      selected: selected,
      selectedColor: LiveClassColors.navy,
      labelStyle: TextStyle(color: selected ? Colors.white : Colors.grey.shade800, fontWeight: FontWeight.w600, fontSize: 12.5),
      onSelected: (_) {
        setState(() => _statusFilter = value);
        _loadReports();
      },
    );
  }

  Widget _reportTile(ChatMessageReport r) {
    final busy = _busyIds.contains(r.id);
    return LiveClassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  r.reason.isNotEmpty ? r.reason.replaceAll('_', ' ') : 'Reported message',
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13.5, color: LiveClassColors.navy),
                ),
              ),
              Text(liveClassFmtDateTime(r.createdAt), style: TextStyle(fontSize: 10.5, color: Colors.grey.shade500)),
            ],
          ),
          if (r.messagePreview.isNotEmpty) ...[
            const SizedBox(height: 8),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(color: LiveClassColors.bg, borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
              child: Text(r.messagePreview, style: const TextStyle(fontSize: 12.5, fontStyle: FontStyle.italic)),
            ),
          ],
          if (r.description.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(r.description, style: TextStyle(fontSize: 12.5, color: Colors.grey.shade700)),
          ],
          const SizedBox(height: 8),
          Text('Reported by ${r.reportedBy.fullName.isNotEmpty ? r.reportedBy.fullName : r.reportedBy.username}',
              style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
          if (_statusFilter == _kStatusPending) ...[
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: busy ? null : () => _review(r, _kStatusDismissed),
                    child: const Text('Dismiss'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(backgroundColor: Colors.red.shade600, foregroundColor: Colors.white),
                    onPressed: busy ? null : () => _review(r, _kStatusActioned),
                    child: busy
                        ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : const Text('Action (remove)'),
                  ),
                ),
              ],
            ),
          ] else ...[
            const SizedBox(height: 8),
            LiveClassStatusChip(
              label: r.status.toUpperCase(),
              color: r.status == _kStatusActioned ? LiveClassColors.danger : Colors.grey.shade700,
              background: r.status == _kStatusActioned ? LiveClassColors.dangerBg : Colors.grey.shade200,
            ),
          ],
        ],
      ),
    );
  }
}