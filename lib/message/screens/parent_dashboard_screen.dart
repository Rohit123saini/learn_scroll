// message/screens/parent_dashboard_screen.dart
//
// Read-only summary for a parent/guardian. Deliberately shows ONLY
// attendance + assignment status per classroom — never chat content.
//
// 🌐 LANGUAGE FIX — all text from AppLocalizations (was hardcoded English/Hinglish).
// 🎨 THEME FIX — the stat tiles were `surface2 @ 60%` drawn on top of a `surface2`
// card, i.e. the same colour in light AND dark mode, so they were invisible. They
// now use `surface` + an outline so they read as tiles in both themes.
// 🔧 FIX — when the session is gone/revoked the old "Retry" button could never
// succeed (the token was already cleared); it now offers "Enter a new code".

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';
import '../../theme_service.dart'; // AppThemeTokens
import '../services/parent_service.dart';
import 'parent_code_entry_screen.dart';

class ParentDashboardScreen extends StatefulWidget {
  const ParentDashboardScreen({super.key});

  @override
  State<ParentDashboardScreen> createState() => _ParentDashboardScreenState();
}

class _ParentDashboardScreenState extends State<ParentDashboardScreen> {
  late Future<ParentDashboard> _future;

  @override
  void initState() {
    super.initState();
    _future = ParentService.instance.fetchDashboard();
  }

  void _load() {
    setState(() => _future = ParentService.instance.fetchDashboard());
  }

  Future<void> _refresh() async {
    _load();
    try {
      await _future;
    } catch (_) {
      // the FutureBuilder below renders the error state
    }
  }

  Future<void> _goToCodeEntry() async {
    await ParentService.instance.signOut();
    if (!mounted) return;
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(builder: (_) => const ParentCodeEntryScreen()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.parentModeTitle),
        actions: [
          IconButton(
            icon: const Icon(Icons.logout),
            tooltip: l10n.parentDashSignOut,
            onPressed: _goToCodeEntry,
          ),
        ],
      ),
      body: FutureBuilder<ParentDashboard>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snapshot.hasError) {
            final error = snapshot.error;
            final parentError = error is ParentModeException ? error : null;
            final message = parentError != null
                ? parentError.localized(l10n)
                : l10n.parentErrDashboardLoad;
            final needsNewCode = parentError?.needsNewCode ?? false;
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Text(message, style: TextStyle(color: cs.onSurfaceVariant), textAlign: TextAlign.center),
                    const SizedBox(height: 16),
                    ElevatedButton(
                      onPressed: needsNewCode ? _goToCodeEntry : _load,
                      child: Text(needsNewCode ? l10n.parentDashEnterNewCode : l10n.retry),
                    ),
                  ],
                ),
              ),
            );
          }

          final dashboard = snapshot.data!;
          return RefreshIndicator(
            onRefresh: _refresh,
            child: ListView(
              // always scrollable so pull-to-refresh also works on the empty state
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  dashboard.studentName,
                  style: TextStyle(color: cs.onSurface, fontSize: 22, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 4),
                Text(
                  l10n.parentDashSubtitle,
                  style: TextStyle(color: cs.onSurfaceVariant, fontSize: 12),
                ),
                const SizedBox(height: 20),
                if (dashboard.classrooms.isEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 40),
                    child: Center(
                      child: Text(l10n.parentDashNoClassrooms, style: TextStyle(color: cs.onSurfaceVariant)),
                    ),
                  )
                else
                  ...dashboard.classrooms.map((c) => _classroomCard(c, l10n)),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _classroomCard(ParentClassroomSummary c, AppLocalizations l10n) {
    final cs = Theme.of(context).colorScheme;
    final tokens = AppThemeTokens.of(context);
    return Container(
      margin: const EdgeInsets.only(bottom: 16),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: tokens.surface2,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(c.groupName, style: TextStyle(color: cs.onSurface, fontSize: 17, fontWeight: FontWeight.w600)),
          const SizedBox(height: 14),
          Row(
            children: [
              _statTile(
                icon: Icons.local_fire_department,
                label: l10n.parentDashStreak,
                value: l10n.parentDashStreakDays(c.attendance.currentStreak),
              ),
              const SizedBox(width: 12),
              _statTile(
                icon: Icons.event_available,
                label: l10n.parentDashTotalClasses,
                value: '${c.attendance.totalClassesAttended}',
              ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              _statTile(
                icon: Icons.pending_actions,
                label: l10n.parentDashAssignmentsPending,
                value: '${c.assignments.pending}',
                highlight: c.assignments.pending > 0,
              ),
              const SizedBox(width: 12),
              _statTile(
                icon: Icons.check_circle_outline,
                label: l10n.parentDashSubmitted,
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
    final cs = Theme.of(context).colorScheme;
    final tokens = AppThemeTokens.of(context);
    return Expanded(
      child: Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          // 🎨 was `surface2.withOpacity(0.6)` on a surface2 card => invisible tile
          color: highlight ? tokens.warning.withOpacity(0.12) : tokens.surface,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: highlight ? tokens.warning.withOpacity(0.4) : cs.outlineVariant),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, color: highlight ? tokens.warning : cs.onSurfaceVariant, size: 20),
            const SizedBox(height: 8),
            Text(value, style: TextStyle(color: cs.onSurface, fontSize: 16, fontWeight: FontWeight.bold)),
            Text(label, style: TextStyle(color: cs.onSurfaceVariant, fontSize: 11)),
          ],
        ),
      ),
    );
  }
}
