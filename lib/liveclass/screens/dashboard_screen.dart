// ============================================================
// LIVECLASS — DASHBOARD SCREEN
//
// Backend surface used: GET /dashboard/ (single-call home summary),
// GET /my-earnings/ (teacher only), GET /my-progress/ (student stats).
// Shows earnings/progress side by side via a tab — a user can be both
// a teacher on one classroom and a student on another, so this isn't
// gated behind a fixed "role".
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';
import '../models/liveclass_models.dart';
import 'live_session_screen.dart';

class LiveClassDashboardScreen extends StatefulWidget {
  final LiveClassApi api;
  const LiveClassDashboardScreen({super.key, required this.api});

  @override
  State<LiveClassDashboardScreen> createState() => _LiveClassDashboardScreenState();
}

class _LiveClassDashboardScreenState extends State<LiveClassDashboardScreen> {
  LiveClassDashboard? _dashboard;
  TeacherEarnings? _earnings;
  StudentProgress? _progress;
  Object? _error;
  bool _loading = true;

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
      final dash = await widget.api.dashboard();
      // `GET /dashboard/` has no live-now list — it's its own endpoint.
      List<LiveNowSession> liveNow = const [];
      try {
        liveNow = (await widget.api.liveNow())
            .map((e) => LiveNowSession.fromJson(Map<String, dynamic>.from(e as Map)))
            .toList();
      } catch (_) {}
      TeacherEarnings? earnings;
      StudentProgress? progress;
      try {
        earnings = TeacherEarnings.fromJson(await widget.api.myEarnings());
      } catch (_) {
        // Not a teacher on anything yet — fine, section just won't show.
      }
      try {
        progress = StudentProgress.fromJson(await widget.api.myProgress());
      } catch (_) {}
      setState(() {
        _dashboard = LiveClassDashboard.fromJson(dash)..liveNow = liveNow;
        _earnings = earnings;
        _progress = progress;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.dashboardTitle),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? ListView(children: const [LsLiveRowSkeleton(), LsPostCardSkeleton()])
            : _error != null
                ? ErrorStateWidget(title: t.couldNotLoadDashboard, retryLabel: t.retry, onRetry: _load)
                : ListView(children: [
                    if (_dashboard!.liveNow.isNotEmpty) ...[
                      LsSectionHead(title: t.liveNow),
                      SizedBox(
                        height: 176,
                        child: ListView.builder(
                          scrollDirection: Axis.horizontal,
                          padding: const EdgeInsets.symmetric(horizontal: kLsPad),
                          itemCount: _dashboard!.liveNow.length,
                          itemBuilder: (context, i) {
                            final s = _dashboard!.liveNow[i];
                            return LsCard(
                              margin: const EdgeInsets.only(right: 10),
                              padding: const EdgeInsets.all(10),
                              onTap: () => Navigator.of(context).push(MaterialPageRoute(
                                builder: (_) => LiveSessionScreen(api: widget.api, sessionId: s.sessionId, classroomId: s.classroomId),
                              )),
                              child: SizedBox(
                                width: 160,
                                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                  LsStatusChip(label: t.liveBadge, color: Colors.red, solid: true),
                                  const SizedBox(height: 8),
                                  Text(s.classroomTitle, maxLines: 2, overflow: TextOverflow.ellipsis, style: LsType.head(context, size: 12.5)),
                                  const Spacer(),
                                  Text(t.viewerCountLabel(s.viewerCount), style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
                                ]),
                              ),
                            );
                          },
                        ),
                      ),
                    ],
                    LsSectionHead(title: t.upcomingSessionsTitle),
                    if (_dashboard!.upcoming.isEmpty)
                      EmptyStateWidget(title: t.noUpcomingSessions, icon: Icons.event_busy_rounded)
                    else
                      ..._dashboard!.upcoming.map((s) => LsCard(
                            margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
                            child: LsMetaRow(icon: Icons.schedule_rounded, label: s.classroomTitle, value: '${s.scheduledStart.hour}:${s.scheduledStart.minute.toString().padLeft(2, '0')}'),
                          )),
                    if (_progress != null) ...[
                      LsSectionHead(title: t.myProgressTitle),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: kLsPad),
                        child: Row(children: [
                          Expanded(child: LsScoreTile(value: '${_progress!.classesAttended}', label: t.attendanceLabel, color: cs.primary)),
                          const SizedBox(width: 8),
                          Expanded(child: LsScoreTile(value: '${_progress!.attendanceStreak}', label: t.streakLabel, color: Colors.orange)),
                          const SizedBox(width: 8),
                          Expanded(child: LsScoreTile(value: '${_progress!.certificatesEarned}', label: t.certificatesLabel, color: Colors.green)),
                        ]),
                      ),
                      const SizedBox(height: 20),
                    ],
                    if (_earnings != null) ...[
                      LsSectionHead(title: t.myEarningsTitle),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: kLsPad),
                        child: LsCard(
                          child: LsMetaRow(icon: Icons.currency_rupee_rounded, label: t.totalEarnedLabel, value: '₹${_earnings!.totalInr.toStringAsFixed(0)}'),
                        ),
                      ),
                      const SizedBox(height: 20),
                    ],
                  ]),
      ),
    );
  }
}
