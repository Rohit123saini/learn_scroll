// lib/tuitionclass/screens/my_progress_screen.dart
//
// Student Progress Dashboard — Phase 2, item 8. Backend
// (StudentProgressView, GET my-progress/) was fully ready with a
// confirmed serializer shape (StudentProgressSerializer, serializers.py)
// but `TuitionClassApi.myProgress()` was never called from anywhere — the
// Insta-"activity/insights"-style card the frontend spec asked for
// didn't exist yet.
//
// This is account-wide (own activity across every classroom the caller
// has ever had access to), not classroom-scoped — same situation as
// referral_screen.dart. It isn't reached from Classroom Detail's manage
// sheet for that reason. Wired in here from Explore's app bar (the
// module's own entry tab, already uploaded) via an "insights" icon next
// to the create-classroom button, since this app's main menu / profile
// section wasn't part of this upload — if that section exists elsewhere,
// point its own entry point at this screen too.
//
// API: GET my-progress/ (TuitionClassApi.myProgress()).
//
// On the shared TuitionClass design system (tuitionclass_theme.dart).

import 'package:flutter/material.dart';

import '../models/tuitionclass_models.dart';
import '../services/tuitionclass_api_service.dart';
import '../theme/tuitionclass_theme.dart';

class MyProgressScreen extends StatefulWidget {
  const MyProgressScreen({super.key});

  @override
  State<MyProgressScreen> createState() => _MyProgressScreenState();
}

class _MyProgressScreenState extends State<MyProgressScreen> {
  StudentProgress? _progress;
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
      final progress = await TuitionClassApi.myProgress();
      if (!mounted) return;
      setState(() {
        _progress = progress;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is TuitionClassApiException ? e.message : 'Could not load your progress.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: TuitionClassColors.bg,
      appBar: tuitionClassAppBar('My Progress'),
      body: RefreshIndicator(
        color: TuitionClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const TuitionClassLoading()
            : _error != null
                ? TuitionClassErrorState(message: _error!, onRetry: _load)
                : _buildBody(),
      ),
    );
  }

  Widget _buildBody() {
    final p = _progress!;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
      children: [
        // Streak hero card — Insta "activity streak" style highlight.
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(gradient: TuitionClassColors.gradient, borderRadius: BorderRadius.circular(TuitionClassRadius.card)),
          child: Row(
            children: [
              const Icon(Icons.local_fire_department_rounded, color: Colors.white, size: 40),
              const SizedBox(width: 14),
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('${p.currentStreakDays} day${p.currentStreakDays == 1 ? '' : 's'}',
                      style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 22)),
                  Text('Current attendance streak', style: TextStyle(color: Colors.white.withOpacity(0.9), fontSize: 12.5)),
                  const SizedBox(height: 2),
                  Text('Best: ${p.longestStreakDays} day${p.longestStreakDays == 1 ? '' : 's'}',
                      style: TextStyle(color: Colors.white.withOpacity(0.8), fontSize: 11.5)),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        GridView.count(
          crossAxisCount: 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: 1.5,
          children: [
            _statCard(Icons.event_available_rounded, '${p.classesAttended}', 'Classes Attended'),
            _statCard(Icons.school_outlined, '${p.classroomsEnrolled}', 'Classrooms Enrolled'),
            _statCard(Icons.assignment_turned_in_outlined, '${p.assignmentsSubmitted}', 'Assignments Submitted'),
            _statCard(Icons.workspace_premium_outlined, '${p.certificatesEarned}', 'Certificates Earned'),
          ],
        ),
      ],
    );
  }

  Widget _statCard(IconData icon, String value, String label) {
    return TuitionClassCard(
      margin: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(icon, color: TuitionClassColors.navy, size: 24),
          const SizedBox(height: 8),
          Text(value, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 20, color: TuitionClassColors.navy)),
          Text(label, style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
        ],
      ),
    );
  }
}