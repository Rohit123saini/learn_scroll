// lib/support/screens/help_center_screen.dart
// Entry point: Settings -> Help & feedback.
// TODO(l10n): screen copy is English-only for now.

import 'package:flutter/material.dart';

import '../../widgets/ls_ui.dart';
import 'bug_report_screen.dart';
import 'feature_board_screen.dart';
import 'tickets_screen.dart';

class HelpCenterScreen extends StatelessWidget {
  const HelpCenterScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    Widget tile(IconData icon, String title, String sub, Widget page) => InkWell(
          onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => page)),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
            child: Row(children: [
              Icon(icon, size: 22, color: cs.primary),
              const SizedBox(width: 14),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                  const SizedBox(height: 2),
                  Text(sub, style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
                ]),
              ),
              Icon(Icons.chevron_right_rounded, color: cs.onSurfaceVariant),
            ]),
          ),
        );

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Help & feedback'),
      body: ListView(padding: const EdgeInsets.symmetric(vertical: 12), children: [
        LsCard(
          margin: const EdgeInsets.symmetric(horizontal: kLsPad),
          padding: EdgeInsets.zero,
          child: Column(children: [
            tile(Icons.support_agent_rounded, 'Chat with support', 'Ask us anything - we reply here', const TicketsScreen()),
            Divider(height: 1, color: cs.outlineVariant),
            tile(Icons.bug_report_outlined, 'Report a bug', 'Describe the problem, attach a screenshot', const BugReportScreen()),
            Divider(height: 1, color: cs.outlineVariant),
            tile(Icons.lightbulb_outline_rounded, 'Feature requests', 'Vote for what we build next', const FeatureBoardScreen()),
          ]),
        ),
      ]),
    );
  }
}
