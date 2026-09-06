// message/screens/parent_dashboard_screen.dart
//
// Read-only summary for a parent/guardian. Deliberately shows ONLY
// attendance + assignment status per classroom — never chat content.

import 'package:flutter/material.dart';
import '../services/parent_service.dart';
import 'parent_code_entry_screen.dart';

class ParentDashboardScreen extends StatefulWidget {
  const ParentDashboardScreen({super.key});

  @override
  State<ParentDashboardScreen> createState() => _ParentDashboardScreenState();
}

class _ParentDashboardScreenState extends State<ParentDashboardScreen> {
  Future<ParentDashboard>? _future;

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    setState(() => _future = ParentService.instance.fetchDashboard());
  }

  Future<void> _signOut() async {
    await ParentService.instance.signOut();
    if (!mounted) return;
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(builder: (_) => const ParentCodeEntryScreen()),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F0F11),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0F0F11),
        title: const Text('Parent Mode'),
        actions: [
          IconButton(icon: const Icon(Icons.logout), onPressed: _signOut),
        ],
      ),
      body: FutureBuilder<ParentDashboard>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snapshot.hasError) {
            final message = snapshot.error is ParentModeException
                ? (snapshot.error as ParentModeException).message
                : 'Load nahi ho paaya.';
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Text(message, style: const TextStyle(color: Colors.white70), textAlign: TextAlign.center),
                    const SizedBox(height: 16),
                    ElevatedButton(onPressed: _load, child: const Text('Retry')),
                  ],
                ),
              ),
            );
          }

          final dashboard = snapshot.data!;
          if (dashboard.classrooms.isEmpty) {
            return const Center(
              child: Text('Abhi koi classroom nahi mila.', style: TextStyle(color: Colors.white70)),
            );
          }

          return RefreshIndicator(
            onRefresh: () async => _load(),
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  dashboard.studentName,
                  style: const TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 4),
                const Text(
                  'Attendance aur assignment status — chat content yahan kabhi nahi dikhega.',
                  style: TextStyle(color: Colors.white38, fontSize: 12),
                ),
                const SizedBox(height: 20),
                ...dashboard.classrooms.map(_classroomCard),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _classroomCard(ParentClassroomSummary c) {
    return Container(
      margin: const EdgeInsets.only(bottom: 16),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.05),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(c.groupName, style: const TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.w600)),
          const SizedBox(height: 14),
          Row(
            children: [
              _statTile(
                icon: Icons.local_fire_department,
                label: 'Current Streak',
                value: '${c.attendance.currentStreak} days',
              ),
              const SizedBox(width: 12),
              _statTile(
                icon: Icons.event_available,
                label: 'Total Classes',
                value: '${c.attendance.totalClassesAttended}',
              ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              _statTile(
                icon: Icons.pending_actions,
                label: 'Assignments Pending',
                value: '${c.assignments.pending}',
                highlight: c.assignments.pending > 0,
              ),
              const SizedBox(width: 12),
              _statTile(
                icon: Icons.check_circle_outline,
                label: 'Submitted',
                value: '${c.assignments.submitted}/${c.assignments.total}',
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _statTile({
    required IconData icon,
    required String label,
    required String value,
    bool highlight = false,
  }) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: highlight ? Colors.orange.withOpacity(0.12) : Colors.white.withOpacity(0.03),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, color: highlight ? Colors.orangeAccent : Colors.white54, size: 20),
            const SizedBox(height: 8),
            Text(value, style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
            Text(label, style: const TextStyle(color: Colors.white38, fontSize: 11)),
          ],
        ),
      ),
    );
  }
}