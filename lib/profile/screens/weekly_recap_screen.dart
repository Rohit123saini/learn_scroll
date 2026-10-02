// lib/profile/screens/weekly_recap_screen.dart
//
// TASK G2 (growth_and_feature_tasks.md — Daily/weekly "recap" screen).
//
// "Your Week" card — tests attempted, classes attended, likes received,
// current streak — for the most recently generated recap
// (services/recap_service.dart -> user_profile.WeeklyRecapView, backend).
// Share button downloads the backend-rendered PNG card
// (WeeklyRecapCardAPIView) and hands it to `share_plus`'s
// `Share.shareXFiles`, same package `singlepost.dart`/`profile.dart`
// already use elsewhere in this app for sharing — just image bytes
// instead of plain text, so it goes through a temp file (path_provider,
// same as `media_edit_screen.dart`'s own share flow) rather than
// `Share.share()`'s text-only API.
//
// Entry point: push this from wherever a "Your Week" notification/bell
// row (Notification.NotifType.WEEKLY_RECAP_READY, backend) or a profile
// menu item deep-links to — this file only defines the screen itself.

import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../../services/recap_service.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';

class WeeklyRecapScreen extends StatefulWidget {
  const WeeklyRecapScreen({super.key});

  @override
  State<WeeklyRecapScreen> createState() => _WeeklyRecapScreenState();
}

class _WeeklyRecapScreenState extends State<WeeklyRecapScreen> {
  bool _loading = true;
  WeeklyRecapInfo? _recap;
  bool _sharing = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    final recap = await RecapService.fetchLatest();
    if (!mounted) return;
    setState(() {
      _recap = recap;
      _loading = false;
    });
  }

  Future<void> _share() async {
    final recap = _recap;
    if (recap == null || _sharing) return;

    setState(() => _sharing = true);
    try {
      final Uint8List? bytes = await RecapService.fetchCardPng(recap.id);
      if (!mounted) return;

      if (bytes == null) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text("Couldn't prepare your share card — try again in a bit.")),
        );
        return;
      }

      final dir = await getTemporaryDirectory();
      final file = File(
        '${dir.path}/learnscroll_week_${recap.weekStart.toIso8601String().split("T").first}.png',
      );
      await file.writeAsBytes(bytes, flush: true);

      await Share.shareXFiles(
        [XFile(file.path, mimeType: 'image/png')],
        text: 'My week on LearnScroll \u{1F4CA}',
      );
    } finally {
      if (mounted) setState(() => _sharing = false);
    }
  }

  String _dateRange(WeeklyRecapInfo r) {
    const months = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    String fmt(DateTime d) => '${d.day} ${months[d.month - 1]}';
    return '${fmt(r.weekStart)} \u2013 ${fmt(r.weekEnd)}';
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(title: const Text('Your Week')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _recap == null
                ? ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    children: const [
                      EmptyStateWidget(
                        title: 'No recap yet',
                        subtitle:
                            'Your first "Your Week" card shows up after your first full '
                            'week on LearnScroll — check back on Monday.',
                        icon: Icons.bar_chart_rounded,
                      ),
                    ],
                  )
                : ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.all(kLsPad),
                    children: [
                      LsSectionHead(title: _dateRange(_recap!)),
                      const SizedBox(height: 8),
                      LsCard(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              'Here\u2019s what you got done',
                              style: GoogleFonts.sora(
                                fontSize: 15, fontWeight: FontWeight.w700, color: cs.onSurface,
                              ),
                            ),
                            const SizedBox(height: 14),
                            GridView.count(
                              crossAxisCount: 2,
                              shrinkWrap: true,
                              physics: const NeverScrollableScrollPhysics(),
                              mainAxisSpacing: 10,
                              crossAxisSpacing: 10,
                              childAspectRatio: 1.5,
                              children: [
                                LsScoreTile(
                                  value: '${_recap!.testsAttempted}',
                                  label: 'Tests attempted',
                                  color: Colors.indigo,
                                ),
                                LsScoreTile(
                                  value: '${_recap!.classesAttended}',
                                  label: 'Classes attended',
                                  color: Colors.teal,
                                ),
                                LsScoreTile(
                                  value: '${_recap!.postsLikedReceived}',
                                  label: 'Likes received',
                                  color: Colors.pink,
                                ),
                                LsScoreTile(
                                  value: '${_recap!.streakDays}',
                                  label: 'Day streak',
                                  color: Colors.orange,
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 20),
                      LsPrimaryButton(
                        label: _sharing ? 'Preparing card\u2026' : 'Share your week',
                        icon: Icons.ios_share_rounded,
                        loading: _sharing,
                        onPressed: _sharing ? null : _share,
                      ),
                    ],
                  ),
      ),
    );
  }
}
