// lib/leaderboard/models/leaderboard_models.dart
//
// ============================================================
// TASK G7 (growth_and_feature_tasks.md — Leaderboards).
//
// Plain data holders for `GET /leaderboard/board/` and
// `GET /leaderboard/my-rank/` (backend: leaderboard/views.py +
// leaderboard/serializers.py). Deliberately scope-agnostic — the same
// `LeaderboardEntry`/`LeaderboardBoard` shapes are reused across all
// three scopes (test series / campus section / app-wide engagement),
// matching the backend's own single `LeaderboardEntry` table.
// ============================================================

enum LeaderboardScope { testSeries, campusSection, engagement }

enum LeaderboardPeriod { weekly, allTime }

extension LeaderboardScopeX on LeaderboardScope {
  /// Wire value the backend's `scope_type` query param / field expects.
  String get apiValue {
    switch (this) {
      case LeaderboardScope.testSeries:
        return 'test_series';
      case LeaderboardScope.campusSection:
        return 'campus_section';
      case LeaderboardScope.engagement:
        return 'engagement';
    }
  }
}

extension LeaderboardPeriodX on LeaderboardPeriod {
  String get apiValue => this == LeaderboardPeriod.weekly ? 'weekly' : 'all_time';
}

/// One row on a board — a ranked user + their score + why (`metadata`).
class LeaderboardEntry {
  final int rank;
  final double score;
  final String userId;
  final String username;
  final String displayName;
  final String? avatarUrl;
  final bool isMe;
  final Map<String, dynamic> metadata;

  const LeaderboardEntry({
    required this.rank,
    required this.score,
    required this.userId,
    required this.username,
    required this.displayName,
    this.avatarUrl,
    this.isMe = false,
    this.metadata = const {},
  });

  factory LeaderboardEntry.fromJson(Map<String, dynamic> json) {
    final user = (json['user'] as Map?)?.cast<String, dynamic>() ?? const {};
    return LeaderboardEntry(
      rank: (json['rank'] as num?)?.toInt() ?? 0,
      score: (json['score'] as num?)?.toDouble() ?? 0,
      userId: (user['id'] ?? '').toString(),
      username: (user['username'] ?? '').toString(),
      displayName: (user['full_name'] ?? user['username'] ?? '').toString(),
      avatarUrl: user['profile_picture'] as String?,
      isMe: json['is_me'] == true,
      metadata: (json['metadata'] as Map?)?.cast<String, dynamic>() ?? const {},
    );
  }
}

/// A full board page response — the ranked list plus the caller's own row
/// (which may sit outside the current page, so it's kept separate rather
/// than requiring it to also appear in `entries`).
class LeaderboardBoard {
  final LeaderboardScope scope;
  final String? scopeId;
  final LeaderboardPeriod period;
  final String periodKey;
  final int totalCount;
  final List<LeaderboardEntry> entries;
  final LeaderboardEntry? myRank;

  const LeaderboardBoard({
    required this.scope,
    required this.scopeId,
    required this.period,
    required this.periodKey,
    required this.totalCount,
    required this.entries,
    this.myRank,
  });

  factory LeaderboardBoard.fromJson(Map<String, dynamic> json, {required LeaderboardScope scope}) {
    final results = (json['results'] as List?) ?? const [];
    final myRankJson = json['my_rank'];
    return LeaderboardBoard(
      scope: scope,
      scopeId: json['scope_id'] as String?,
      period: (json['period'] == 'weekly') ? LeaderboardPeriod.weekly : LeaderboardPeriod.allTime,
      periodKey: (json['period_key'] ?? '').toString(),
      totalCount: (json['count'] as num?)?.toInt() ?? results.length,
      entries: results
          .whereType<Map>()
          .map((e) => LeaderboardEntry.fromJson(e.cast<String, dynamic>()))
          .toList(growable: false),
      myRank: (myRankJson is Map) ? LeaderboardEntry.fromJson(myRankJson.cast<String, dynamic>()) : null,
    );
  }

  static LeaderboardBoard empty(LeaderboardScope scope, LeaderboardPeriod period) => LeaderboardBoard(
        scope: scope,
        scopeId: null,
        period: period,
        periodKey: '',
        totalCount: 0,
        entries: const [],
        myRank: null,
      );
}
