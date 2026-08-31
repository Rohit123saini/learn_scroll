// lib/liveclass/screens/session_engagement_report_screen.dart
//
// NEW screen (frontend integration architecture v3, §1.7, Pass 15).
// ⚠️ ARCHITECTURE SKELETON — `SessionEngagementReport` and
// `SessionApi.engagementReport` (liveclass_models.dart /
// liveclass_api_service.dart) are a best guess from the change-log
// description only (Pass 15 was never written up in the backend doc's own
// §2–§6). Confirm exact field names + endpoint path against real backend
// source before this ships.
//
// Teacher/co-teacher/moderator only — the caller (sessions_list_screen.dart
// or live_session_screen.dart, neither in this batch) should already gate
// navigation to this screen behind the same `canManage` flag threaded
// everywhere else in this module, e.g. from a completed session's card:
//   if (canManage)
//     _manageTile(ctx, Icons.bar_chart_outlined, 'View Report', () =>
//       Navigator.push(context, MaterialPageRoute(builder: (_) =>
//         SessionEngagementReportScreen(sessionId: session.id, sessionTitle: session.classroomTitle))));
//
// No charting library is listed as a dependency for this module yet (doc
// §1.7), so this stays plain stat cards/rows rather than a chart — revisit
// if the real report turns out to be genuinely time-series. Card visual
// precedent per the doc is teacher_earnings_screen.dart (not in this
// batch) — this uses the shared LiveClassCard instead, which that screen
// should also be migrated to for consistency.

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

class SessionEngagementReportScreen extends StatefulWidget {
  final int sessionId;
  final String sessionTitle;

  const SessionEngagementReportScreen({
    super.key,
    required this.sessionId,
    this.sessionTitle = '',
  });

  @override
  State<SessionEngagementReportScreen> createState() => _SessionEngagementReportScreenState();
}

class _SessionEngagementReportScreenState extends State<SessionEngagementReportScreen> {
  bool _loading = true;
  String? _error;
  SessionEngagementReport? _report;

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
      final report = await LiveClassApi.sessions.engagementReport(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _report = report;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load the engagement report.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(
        widget.sessionTitle.isEmpty ? 'Session Report' : 'Report · ${widget.sessionTitle}',
      ),
      body: RefreshIndicator(onRefresh: _load, child: _buildBody()),
    );
  }

  Widget _buildBody() {
    if (_loading) return const LiveClassLoading();
    if (_error != null || _report == null) {
      return LiveClassErrorState(message: _error ?? 'Something went wrong.', onRetry: _load);
    }

    final r = _report!;
    final avgMinutes = r.avgWatchDurationSeconds / 60;

    return ListView(
      padding: const EdgeInsets.all(LiveClassSpacing.lg),
      children: [
        GridView.count(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          crossAxisCount: 2,
          mainAxisSpacing: LiveClassSpacing.md,
          crossAxisSpacing: LiveClassSpacing.md,
          childAspectRatio: 1.5,
          children: [
            _StatCard(icon: Icons.people_outline, label: 'Attendance', value: '${r.attendanceCount}'),
            _StatCard(
              icon: Icons.timer_outlined,
              label: 'Avg. watch time',
              value: '${avgMinutes.toStringAsFixed(avgMinutes < 10 ? 1 : 0)} min',
            ),
            _StatCard(icon: Icons.chat_bubble_outline, label: 'Chat messages', value: '${r.chatMessageCount}'),
            _StatCard(
              icon: Icons.poll_outlined,
              label: 'Poll participation',
              value: '${(r.pollParticipationRate * 100).clamp(0, 100).toStringAsFixed(0)}%',
            ),
            _StatCard(icon: Icons.back_hand_outlined, label: 'Hands raised', value: '${r.handRaiseCount}'),
          ],
        ),
        const SizedBox(height: LiveClassSpacing.xxl),
        const Text('Attendance', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15, color: LiveClassColors.navy)),
        const SizedBox(height: LiveClassSpacing.sm),
        if (r.attendance.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: LiveClassSpacing.lg),
            child: Text('No per-student detail available.', style: TextStyle(color: Colors.grey.shade600, fontSize: 12.5)),
          )
        else
          LiveClassCard(
            padding: EdgeInsets.zero,
            margin: EdgeInsets.zero,
            child: Column(
              children: [
                for (int i = 0; i < r.attendance.length; i++) ...[
                  if (i > 0) Divider(height: 1, indent: LiveClassSpacing.lg, endIndent: LiveClassSpacing.lg),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: LiveClassSpacing.lg, vertical: LiveClassSpacing.sm),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            r.attendance[i].student.fullName,
                            style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w500),
                          ),
                        ),
                        Text(
                          _formatDuration(r.attendance[i].watchDurationSeconds),
                          style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600),
                        ),
                        if (r.attendance[i].raisedHand) ...[
                          const SizedBox(width: LiveClassSpacing.sm),
                          Icon(Icons.back_hand, size: 16, color: LiveClassColors.warning),
                        ],
                      ],
                    ),
                  ),
                ],
              ],
            ),
          ),
      ],
    );
  }
}

String _formatDuration(int seconds) {
  final m = seconds ~/ 60;
  final s = seconds % 60;
  return '${m}m ${s}s';
}

class _StatCard extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;

  const _StatCard({required this.icon, required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return LiveClassCard(
      margin: EdgeInsets.zero,
      padding: const EdgeInsets.all(LiveClassSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          LiveClassIconBadge(icon: icon, size: 34),
          const SizedBox(height: LiveClassSpacing.sm),
          Text(value, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 18, color: LiveClassColors.navy)),
          Text(label, style: TextStyle(color: Colors.grey.shade600, fontSize: 11.5)),
        ],
      ),
    );
  }
}