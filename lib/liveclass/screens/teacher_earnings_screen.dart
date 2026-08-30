// lib/liveclass/screens/teacher_earnings_screen.dart
//
// Teacher Earnings Dashboard — new screen. The backend (TeacherEarningsView
// in views.py, GET my-earnings/) was fully implemented — total earned,
// this-month earned, a 30-day daily breakdown, and a per-classroom
// breakdown, all backed by real PassDailyCharge aggregates — but the view
// was never even reachable at any URL until a routing fix, and no screen
// anywhere in the module ever called it once it was.
//
// Pass [classroomId] to scope to one classroom the caller teaches (matches
// `?classroom=<id>` on the backend, 403 if they don't teach it); omit it
// for the teacher's overall earnings across every classroom they teach.
//
// API: GET my-earnings/ (optional ?classroom=).
//
// On the shared LiveClass design system (liveclass_theme.dart).

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

class TeacherEarningsScreen extends StatefulWidget {
  final int? classroomId;
  final String classroomTitle;
  const TeacherEarningsScreen({super.key, this.classroomId, this.classroomTitle = ''});

  @override
  State<TeacherEarningsScreen> createState() => _TeacherEarningsScreenState();
}

class _TeacherEarningsScreenState extends State<TeacherEarningsScreen> {
  TeacherEarnings? _earnings;
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
      final earnings = await LiveClassApi.myEarnings(classroomId: widget.classroomId);
      if (!mounted) return;
      setState(() {
        _earnings = earnings;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load earnings.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(
        widget.classroomTitle.isNotEmpty ? 'Earnings — ${widget.classroomTitle}' : 'My Earnings',
      ),
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _buildBody(_earnings!),
      ),
    );
  }

  Widget _buildBody(TeacherEarnings e) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
      children: [
        Row(
          children: [
            Expanded(child: _statCard('Total Earned', '${e.totalEarned}', Icons.savings_outlined)),
            const SizedBox(width: 10),
            Expanded(child: _statCard('This Month', '${e.thisMonthEarned}', Icons.calendar_month_outlined)),
          ],
        ),
        const SizedBox(height: 10),
        _statCard('Sessions Charged', '${e.totalSessionsCharged}', Icons.event_available_outlined, wide: true),
        const SizedBox(height: 20),
        const Text('Last 30 Days', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14.5, color: LiveClassColors.navy)),
        const SizedBox(height: 10),
        e.last30Days.isEmpty
            ? Text('No charges in the last 30 days.', style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600))
            : _dailyBars(e.last30Days),
        const SizedBox(height: 20),
        const Text('By Classroom', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14.5, color: LiveClassColors.navy)),
        const SizedBox(height: 10),
        e.byClassroom.isEmpty
            ? Text('No earnings yet.', style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600))
            : Column(children: e.byClassroom.map(_classroomTile).toList()),
      ],
    );
  }

  Widget _statCard(String label, String value, IconData icon, {bool wide = false}) {
    return LiveClassCard(
      margin: EdgeInsets.zero,
      child: Row(
        children: [
          LiveClassIconBadge(icon: icon, size: 38),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(value, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 18, color: LiveClassColors.navy)),
                Text(label, style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _dailyBars(List<EarningsByDay> days) {
    final maxAmount = days.map((d) => d.amount).fold<int>(0, (a, b) => a > b ? a : b);
    return LiveClassCard(
      margin: EdgeInsets.zero,
      child: SizedBox(
        height: 120,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: days.map((d) {
            final heightFrac = maxAmount == 0 ? 0.0 : d.amount / maxAmount;
            return Expanded(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 1),
                child: Tooltip(
                  message: '${d.date.toLocal().toString().split(' ').first}: ${d.amount}',
                  child: FractionallySizedBox(
                    heightFactor: heightFrac.clamp(0.03, 1.0),
                    alignment: Alignment.bottomCenter,
                    child: Container(
                      decoration: BoxDecoration(
                        gradient: LiveClassColors.gradient,
                        borderRadius: const BorderRadius.vertical(top: Radius.circular(2)),
                      ),
                    ),
                  ),
                ),
              ),
            );
          }).toList(),
        ),
      ),
    );
  }

  Widget _classroomTile(EarningsByClassroom c) {
    return LiveClassCard(
      padding: const EdgeInsets.all(12),
      margin: const EdgeInsets.only(bottom: 10),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(c.classroomTitle, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
                const SizedBox(height: 2),
                Text('${c.sessionsCharged} sessions charged', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
              ],
            ),
          ),
          Text('${c.totalEarned}', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15, color: LiveClassColors.navy)),
        ],
      ),
    );
  }
}