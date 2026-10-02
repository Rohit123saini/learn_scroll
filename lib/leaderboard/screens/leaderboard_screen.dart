// lib/leaderboard/screens/leaderboard_screen.dart
//
// TASK G7 (growth_and_feature_tasks.md — Leaderboards).
//
// Thin Scaffold around LeaderboardBoardWidget — the actual list/toggle
// logic lives in the widget so it can also be embedded directly inside
// another screen (e.g. a "Top 3 this week" preview card) without this
// AppBar wrapper. Use this screen when the leaderboard is the whole
// destination (a "View full leaderboard" tap from anywhere).

import 'package:flutter/material.dart';

import '../../widgets/ls_ui.dart';
import '../models/leaderboard_models.dart';
import '../widgets/leaderboard_board_widget.dart';

class LeaderboardScreen extends StatelessWidget {
  final LeaderboardScope scope;
  final String? scopeId;
  final String title;
  final LeaderboardPeriod initialPeriod;

  const LeaderboardScreen({
    super.key,
    required this.scope,
    this.scopeId,
    required this.title,
    this.initialPeriod = LeaderboardPeriod.weekly,
  });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: title),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.only(top: 10, bottom: 24),
          child: LeaderboardBoardWidget(
            scope: scope,
            scopeId: scopeId,
            initialPeriod: initialPeriod,
          ),
        ),
      ),
    );
  }
}
