// lib/leaderboard/widgets/leaderboard_board_widget.dart
//
// ============================================================
// TASK G7 (growth_and_feature_tasks.md — Leaderboards).
// "Area: ... Frontend (leaderboard tab/screen, reusable widget across
// testseries/campus/tuitionclass)."
//
// ONE widget, reused for all three scopes — drop it into a test-series
// result screen, a campus section screen, or a standalone leaderboard
// tab; only `scope`/`scopeId`/`title` change per call site. Handles its
// own loading/empty/error/forbidden states and the weekly <-> all-time
// switch, so callers never touch LeaderboardService directly.
// ============================================================

import 'package:flutter/material.dart';

import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../models/leaderboard_models.dart';
import '../services/leaderboard_service.dart';

class LeaderboardBoardWidget extends StatefulWidget {
  final LeaderboardScope scope;
  final String? scopeId;

  /// Starting tab. Defaults to weekly — "so newcomers can compete, not
  /// just all-time toppers" (Task G7).
  final LeaderboardPeriod initialPeriod;

  /// If false, hides the weekly/all-time switch entirely and only ever
  /// shows `initialPeriod` — useful when the caller wants a compact
  /// single-period preview (e.g. embedded in another screen) rather than
  /// the full toggle-able board.
  final bool showPeriodSwitch;

  final int pageSize;

  const LeaderboardBoardWidget({
    super.key,
    required this.scope,
    this.scopeId,
    this.initialPeriod = LeaderboardPeriod.weekly,
    this.showPeriodSwitch = true,
    this.pageSize = 20,
  }) : assert(
          scope == LeaderboardScope.engagement || scopeId != null,
          'scopeId is required for test_series/campus_section boards',
        );

  @override
  State<LeaderboardBoardWidget> createState() => _LeaderboardBoardWidgetState();
}

class _LeaderboardBoardWidgetState extends State<LeaderboardBoardWidget> {
  late LeaderboardPeriod _period = widget.initialPeriod;
  Future<LeaderboardBoard>? _future;

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    setState(() {
      _future = LeaderboardService.getBoard(
        scope: widget.scope,
        scopeId: widget.scopeId,
        period: _period,
        limit: widget.pageSize,
      );
    });
  }

  void _switchPeriod(LeaderboardPeriod period) {
    if (period == _period) return;
    setState(() => _period = period);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (widget.showPeriodSwitch)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: LsFilterChips(
              labels: const ['This Week', 'All Time'],
              selectedIndex: _period == LeaderboardPeriod.weekly ? 0 : 1,
              onSelected: (i) => _switchPeriod(i == 0 ? LeaderboardPeriod.weekly : LeaderboardPeriod.allTime),
              padding: EdgeInsets.zero,
            ),
          ),
        FutureBuilder<LeaderboardBoard>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState != ConnectionState.done) {
              return const Padding(
                padding: EdgeInsets.symmetric(vertical: 40),
                child: Center(child: CircularProgressIndicator()),
              );
            }
            if (snap.hasError) {
              final err = snap.error;
              final forbidden = err is LeaderboardApiException && err.isForbidden;
              return ErrorStateWidget(
                title: forbidden ? 'No access' : 'Couldn\u2019t load leaderboard',
                subtitle: forbidden
                    ? 'You don\u2019t have access to this leaderboard.'
                    : 'Check your connection and try again.',
                retryLabel: 'Retry',
                onRetry: forbidden ? null : _load,
                compact: true,
              );
            }

            final board = snap.data!;
            if (board.entries.isEmpty) {
              return const ErrorStateWidget(
                title: 'No rankings yet',
                subtitle: 'Be the first to show up on this leaderboard.',
                retryLabel: '',
                icon: Icons.leaderboard_outlined,
                compact: true,
              );
            }
            return _LeaderboardList(board: board);
          },
        ),
      ],
    );
  }
}

class _LeaderboardList extends StatelessWidget {
  final LeaderboardBoard board;
  const _LeaderboardList({required this.board});

  @override
  Widget build(BuildContext context) {
    final myRank = board.myRank;
    final myRankOnPage = board.entries.any((e) => e.isMe);

    return Column(
      children: [
        for (final entry in board.entries) _LeaderboardRow(entry: entry, scope: board.scope),
        if (myRank != null && !myRankOnPage) ...[
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 6),
            child: Divider(height: 1),
          ),
          _LeaderboardRow(entry: myRank, scope: board.scope, highlight: true),
        ],
      ],
    );
  }
}

class _LeaderboardRow extends StatelessWidget {
  final LeaderboardEntry entry;
  final LeaderboardScope scope;
  final bool highlight;

  const _LeaderboardRow({required this.entry, required this.scope, this.highlight = false});

  Color _rankColor(BuildContext context) {
    final t = lsTokens(context);
    final cs = Theme.of(context).colorScheme;
    switch (entry.rank) {
      case 1:
        return const Color(0xFFFFC107);
      case 2:
        return const Color(0xFFB0BEC5);
      case 3:
        return const Color(0xFFCD7F32);
      default:
        return entry.isMe || highlight ? t.info : cs.onSurfaceVariant;
    }
  }

  String _scoreLabel() {
    if (scope == LeaderboardScope.campusSection) return '${entry.score.round()}%';
    return entry.score == entry.score.roundToDouble()
        ? '${entry.score.round()}'
        : entry.score.toStringAsFixed(1);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final isTop3 = entry.rank <= 3;
    final showAsHighlighted = entry.isMe || highlight;

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: kLsPad, vertical: 3),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: showAsHighlighted ? cs.primary.withOpacity(0.08) : cs.surface,
        borderRadius: BorderRadius.circular(12),
        border: showAsHighlighted ? Border.all(color: cs.primary.withOpacity(0.4)) : null,
      ),
      child: Row(
        children: [
          SizedBox(
            width: 30,
            child: isTop3
                ? Icon(Icons.emoji_events_rounded, color: _rankColor(context), size: 22)
                : Text(
                    '#${entry.rank}',
                    style: TextStyle(fontWeight: FontWeight.w800, fontSize: 13, color: _rankColor(context)),
                  ),
          ),
          const SizedBox(width: 8),
          CircleAvatar(
            radius: 16,
            backgroundColor: cs.surfaceVariant,
            backgroundImage: (entry.avatarUrl != null && entry.avatarUrl!.isNotEmpty)
                ? NetworkImage(entry.avatarUrl!)
                : null,
            child: (entry.avatarUrl == null || entry.avatarUrl!.isEmpty)
                ? Text(
                    entry.displayName.isNotEmpty ? entry.displayName[0].toUpperCase() : '?',
                    style: TextStyle(fontWeight: FontWeight.w700, color: cs.onSurfaceVariant, fontSize: 12),
                  )
                : null,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              entry.isMe ? '${entry.displayName} (You)' : entry.displayName,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: LsType.head(context, size: 13),
            ),
          ),
          const SizedBox(width: 8),
          Text(
            _scoreLabel(),
            style: TextStyle(fontWeight: FontWeight.w800, fontSize: 13, color: cs.primary),
          ),
        ],
      ),
    );
  }
}
