// lib/liveclass/screens/session_engagement_report_screen.dart
//
// NEW (Pass 15 frontend catch-up §1.7) — "View Report" entry point on a
// completed session (sessions_list_screen.dart, host/co-teacher/moderator
// only). Read-only summary of `SessionEngagementReport` (attendance count,
// average watch duration, chat message count, poll participation rate,
// hand-raise count) plus a per-student attendance table
// (`SessionAttendanceRow`: watch duration + whether they raised a hand).
//
// ⚠️ Model/endpoint already existed in liveclass_models.dart /
// liveclass_api_service.dart before this screen was written (Pass 15
// architecture skeleton) — this file is the first thing that actually
// calls `LiveClassApi.sessions.engagementReport()`. Field names there are
// flagged unconfirmed against the real serializer; if a field comes back
// null/0 for every session, check that first before assuming attendance
// was actually zero.
//
// API: `GET sessions/{id}/engagement-report/`
// (LiveClassApi.sessions.engagementReport(sessionId)).
//
// On the shared LiveClass design system (liveclass_theme.dart).

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

String _fmtDuration(double seconds) {
  final s = seconds.round();
  final m = s ~/ 60;
  final rem = s % 60;
  if (m <= 0) return '${rem}s';
  return '${m}m ${rem}s';
}

// ===========================================================================
// SCREEN
// ===========================================================================
class SessionEngagementReportScreen extends StatefulWidget {
  final int sessionId;
  final String classroomTitle;
  const SessionEngagementReportScreen({super.key, required this.sessionId, this.classroomTitle = ''});

  @override
  State<SessionEngagementReportScreen> createState() => _SessionEngagementReportScreenState();
}

class _SessionEngagementReportScreenState extends State<SessionEngagementReportScreen> {
  SessionEngagementReport? _report;
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
      final res = await LiveClassApi.sessions.engagementReport(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _report = res;
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
      appBar: liveClassAppBar(widget.classroomTitle.isNotEmpty ? '${widget.classroomTitle} — Report' : 'Session Report'),
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _buildReport(_report!),
      ),
    );
  }

  Widget _buildReport(SessionEngagementReport r) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
      children: [
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 10,
          crossAxisSpacing: 10,
          childAspectRatio: 1.55,
          children: [
            _statCard(Icons.groups_rounded, '${r.attendanceCount}', 'Attendees'),
            _statCard(Icons.timer_outlined, _fmtDuration(r.avgWatchDurationSeconds), 'Avg. Watch Time'),
            _statCard(Icons.chat_bubble_outline_rounded, '${r.chatMessageCount}', 'Chat Messages'),
            _statCard(Icons.back_hand_outlined, '${r.handRaiseCount}', 'Hand Raises'),
          ],
        ),
        const SizedBox(height: 10),
        LiveClassCard(
          margin: EdgeInsets.zero,
          child: Row(
            children: [
              const Icon(Icons.poll_outlined, color: LiveClassColors.navy, size: 22),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Poll Participation', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13.5)),
                    const SizedBox(height: 6),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(6),
                      child: LinearProgressIndicator(
                        value: r.pollParticipationRate.clamp(0, 1).toDouble(),
                        minHeight: 7,
                        backgroundColor: LiveClassColors.bg,
                        valueColor: const AlwaysStoppedAnimation(LiveClassColors.navy),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 10),
              Text('${(r.pollParticipationRate.clamp(0, 1) * 100).round()}%',
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: LiveClassColors.navy)),
            ],
          ),
        ),
        const SizedBox(height: 20),
        Text('Attendance (${r.attendance.length})', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14.5, color: Colors.grey.shade800)),
        const SizedBox(height: 10),
        if (r.attendance.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 20),
            child: Center(
              child: Text('No attendance data for this session.', style: TextStyle(fontSize: 12.5, color: Colors.grey.shade500)),
            ),
          )
        else
          ...r.attendance.map(_attendanceRow),
      ],
    );
  }

  Widget _statCard(IconData icon, String value, String label) {
    return LiveClassCard(
      margin: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(icon, size: 20, color: LiveClassColors.navy),
          const SizedBox(height: 8),
          Text(value, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
          Text(label, style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
        ],
      ),
    );
  }

  Widget _attendanceRow(SessionAttendanceRow a) {
    final name = a.student.fullName.isNotEmpty ? a.student.fullName : a.student.username;
    return LiveClassCard(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      child: Row(
        children: [
          CircleAvatar(
            radius: 16,
            backgroundColor: LiveClassColors.navy.withValues(alpha: 0.08),
            backgroundImage: a.student.profilePicture != null ? NetworkImage(a.student.profilePicture!) : null,
            child: a.student.profilePicture == null
                ? Text(name.isNotEmpty ? name[0].toUpperCase() : '?', style: const TextStyle(color: LiveClassColors.navy, fontSize: 12, fontWeight: FontWeight.bold))
                : null,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(name, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13), maxLines: 1, overflow: TextOverflow.ellipsis),
          ),
          if (a.raisedHand) ...[
            const Icon(Icons.back_hand_rounded, size: 14, color: Colors.orange),
            const SizedBox(width: 10),
          ],
          Text(_fmtDuration(a.watchDurationSeconds.toDouble()), style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
        ],
      ),
    );
  }
}