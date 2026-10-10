// lib/services/streak_service.dart
//
// ============================================================
// TASK G1 (growth_and_feature_tasks.md — Streaks).
//
// Talks to `GET/POST /profile/streak/` (`user_profile.views.StreakView`,
// backend). Same shared-service shape and same "fail silent, never
// surface a network hiccup to the user" contract
// `services/user_preferences_api.dart` already documents for exactly
// this reason — a streak check-in that silently doesn't sync this one
// time must never crash the home screen or show an error toast; the
// user's actual usage today is what matters, not whether this one
// network call landed.
//
// `checkIn()` is the ONE call site that should ever be invoked from the
// app — it performs today's check-in AND returns the fresh streak state
// in one round trip (`StreakView.post`, backend), so callers never need
// a separate fetch()-then-checkIn() pair. `fetch()` (GET, read-only) is
// kept separately for screens that only want to *display* the streak
// without advancing it (e.g. a settings/profile screen opened well
// after the home screen already checked in for today).
// ============================================================

import 'dart:convert';

import 'package:http/http.dart' as http;

import '../utils/api.dart';
import 'auth_service.dart';

/// Plain data holder for one `/profile/streak/` response's `data` object.
/// Deliberately NOT named `StreakModel` / kept out of `models/` — this is
/// a single small service-local shape, same "just enough of a model to
/// avoid stringly-typed maps at call sites" scope
/// `UserPreferencesApi`'s raw-map return keeps for its own two fields.
class StreakInfo {
  final int currentStreak;
  final int longestStreak;
  final int totalActiveDays;
  final bool isActiveToday;

  // Streak freeze + daily goal (additive server fields — purane server par
  // sab default pe aate hain, isliye parsing hamesha safe hai).
  final int freezeTokens;
  final int freezesUsedTotal;
  final int freezeCostCoins;
  final int freezeMaxTokens;
  final int dailyGoalMinutes;
  final bool goalCompletedToday;
  final int goalsCompletedTotal;

  const StreakInfo({
    required this.currentStreak,
    required this.longestStreak,
    required this.totalActiveDays,
    required this.isActiveToday,
    this.freezeTokens = 0,
    this.freezesUsedTotal = 0,
    this.freezeCostCoins = 50,
    this.freezeMaxTokens = 2,
    this.dailyGoalMinutes = 10,
    this.goalCompletedToday = false,
    this.goalsCompletedTotal = 0,
  });

  factory StreakInfo.fromJson(Map<String, dynamic> json) {
    return StreakInfo(
      currentStreak: (json['current_streak'] as num?)?.toInt() ?? 0,
      longestStreak: (json['longest_streak'] as num?)?.toInt() ?? 0,
      totalActiveDays: (json['total_active_days'] as num?)?.toInt() ?? 0,
      isActiveToday: json['is_active_today'] == true,
      freezeTokens: (json['freeze_tokens'] as num?)?.toInt() ?? 0,
      freezesUsedTotal: (json['freezes_used_total'] as num?)?.toInt() ?? 0,
      freezeCostCoins: (json['freeze_cost_coins'] as num?)?.toInt() ?? 50,
      freezeMaxTokens: (json['freeze_max_tokens'] as num?)?.toInt() ?? 2,
      dailyGoalMinutes: (json['daily_goal_minutes'] as num?)?.toInt() ?? 10,
      goalCompletedToday: json['goal_completed_today'] == true,
      goalsCompletedTotal: (json['goals_completed_total'] as num?)?.toInt() ?? 0,
    );
  }

  static const zero = StreakInfo(
    currentStreak: 0,
    longestStreak: 0,
    totalActiveDays: 0,
    isActiveToday: false,
  );
}

/// Result of a `checkIn()` call — the streak state plus whether this
/// particular call is the one that just crossed a milestone (so the
/// caller can show a one-time celebratory toast instead of re-showing it
/// on every subsequent poll of the same day).
class StreakCheckInResult {
  final StreakInfo streak;
  final int? milestoneReached;
  final int bonusCoins;

  /// Aaj kitne freeze tokens kharch hue (0 = koi nahi) — >0 ho to client
  /// "streak bachi" dikha sakta hai.
  final int freezeUsed;

  const StreakCheckInResult({
    required this.streak,
    this.milestoneReached,
    this.bonusCoins = 0,
    this.freezeUsed = 0,
  });
}

/// `GET/PATCH /profile/daily-goal/` ka `data` — aaj ka "N minute challenge".
/// Progress server ke foreground-heartbeat (`DailyUsage`) se aata hai.
class DailyGoalInfo {
  final int goalMinutes;
  final List<int> options;
  final int todaySeconds;
  final bool completed;
  final int goalsCompletedTotal;
  final int freezeTokens;

  const DailyGoalInfo({
    required this.goalMinutes,
    required this.options,
    required this.todaySeconds,
    required this.completed,
    required this.goalsCompletedTotal,
    required this.freezeTokens,
  });

  factory DailyGoalInfo.fromJson(Map<String, dynamic> json) {
    final opts = (json['options'] as List?)?.whereType<num>().map((e) => e.toInt()).toList();
    return DailyGoalInfo(
      goalMinutes: (json['goal_minutes'] as num?)?.toInt() ?? 10,
      options: (opts == null || opts.isEmpty) ? const [5, 10, 15, 20, 30, 45, 60] : opts,
      todaySeconds: (json['today_seconds'] as num?)?.toInt() ?? 0,
      completed: json['completed'] == true,
      goalsCompletedTotal: (json['goals_completed_total'] as num?)?.toInt() ?? 0,
      freezeTokens: (json['freeze_tokens'] as num?)?.toInt() ?? 0,
    );
  }

  /// 0.0 - 1.0 progress bar ke liye.
  double get fraction {
    final goalSeconds = goalMinutes * 60;
    if (goalSeconds <= 0) return 0;
    return (todaySeconds / goalSeconds).clamp(0.0, 1.0).toDouble();
  }

  int get minutesDone => todaySeconds ~/ 60;
}

/// `buyFreeze()` ka natija — success ya user-facing message ke saath failure
/// (cap poora / coins kam / network).
class BuyFreezeResult {
  final bool ok;
  final StreakInfo? streak;
  final int? coinBalance;
  final String? message;

  const BuyFreezeResult.success(StreakInfo this.streak, this.coinBalance)
      : ok = true,
        message = null;

  const BuyFreezeResult.failure(String this.message)
      : ok = false,
        streak = null,
        coinBalance = null;
}

class StreakService {
  StreakService._();

  static const Duration _timeout = Duration(seconds: 10);
  static const String _endpoint = '/profile/streak/';

  /// Read-only fetch — never advances the streak. Returns `null` for
  /// absolutely any reason it couldn't (logged out, offline, server
  /// error, unexpected shape) — same "null means nothing to show yet,
  /// not an error to surface" contract `UserPreferencesApi.fetch()` uses.
  static Future<StreakInfo?> fetch() async {
    try {
      final token = await AuthService.getValidToken();
      if (token == null) return null;

      final res = await http.get(
        Uri.parse('${Api.baseUrl}$_endpoint'),
        headers: {'Authorization': 'Bearer $token'},
      ).timeout(_timeout);

      if (res.statusCode != 200) return null;
      final body = jsonDecode(res.body);
      if (body is! Map<String, dynamic>) return null;
      final data = body['data'];
      if (data is! Map<String, dynamic>) return null;
      return StreakInfo.fromJson(data);
    } catch (_) {
      return null;
    }
  }

  /// Records today's check-in (idempotent server-side — safe to call once
  /// per app session even if the user already checked in earlier today)
  /// and returns the updated streak. Call this once, e.g. from the home
  /// screen's `initState`/`_loadHomeExtras` — not from every screen or
  /// every API call; see `StreakView.post`'s own docstring (backend) for
  /// why a single deliberate call site is the right shape here.
  ///
  /// Returns `null` on any failure — callers should treat that exactly
  /// like `fetch()` returning `null` (keep showing whatever streak count
  /// is already on screen, or hide the chip, rather than erroring).
  static Future<StreakCheckInResult?> checkIn() async {
    try {
      final token = await AuthService.getValidToken();
      if (token == null) return null;

      final res = await http.post(
        Uri.parse('${Api.baseUrl}$_endpoint'),
        headers: {
          'Authorization': 'Bearer $token',
          'Content-Type': 'application/json',
        },
      ).timeout(_timeout);

      if (res.statusCode != 200) return null;
      final body = jsonDecode(res.body);
      if (body is! Map<String, dynamic>) return null;
      final data = body['data'];
      if (data is! Map<String, dynamic>) return null;

      return StreakCheckInResult(
        streak: StreakInfo.fromJson(data),
        milestoneReached: (body['milestone_reached'] as num?)?.toInt(),
        bonusCoins: (body['bonus_coins'] as num?)?.toInt() ?? 0,
        freezeUsed: (body['freeze_used'] as num?)?.toInt() ?? 0,
      );
    } catch (_) {
      return null;
    }
  }

  /// Coins se ek freeze token kharido — `POST /profile/streak/freeze/`.
  /// Failure par server ka message (e.g. coins kam / max tokens) laut aata hai.
  static Future<BuyFreezeResult> buyFreeze() async {
    try {
      final token = await AuthService.getValidToken();
      if (token == null) return const BuyFreezeResult.failure('Please log in again.');
      final res = await http.post(
        Uri.parse('${Api.baseUrl}${_endpoint}freeze/'),
        headers: {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'},
      ).timeout(_timeout);
      final body = jsonDecode(utf8.decode(res.bodyBytes));
      if (res.statusCode == 201 && body is Map<String, dynamic> && body['data'] is Map<String, dynamic>) {
        return BuyFreezeResult.success(
          StreakInfo.fromJson(body['data'] as Map<String, dynamic>),
          (body['coin_balance'] as num?)?.toInt(),
        );
      }
      if (res.statusCode == 503) {
        return const BuyFreezeResult.failure('Wallet abhi busy hai — thodi der baad try karo.');
      }
      final msg = body is Map ? body['message']?.toString() : null;
      return BuyFreezeResult.failure(msg ?? 'Freeze nahi mil paya.');
    } catch (_) {
      return const BuyFreezeResult.failure('Network problem — dobara try karo.');
    }
  }

  /// Aaj ka daily-goal state (null = kuch dikhane layak nahi / offline).
  static Future<DailyGoalInfo?> fetchDailyGoal() async {
    try {
      final token = await AuthService.getValidToken();
      if (token == null) return null;
      final res = await http.get(
        Uri.parse('${Api.baseUrl}/profile/daily-goal/'),
        headers: {'Authorization': 'Bearer $token'},
      ).timeout(_timeout);
      if (res.statusCode != 200) return null;
      final body = jsonDecode(res.body);
      final data = body is Map ? body['data'] : null;
      return data is Map<String, dynamic> ? DailyGoalInfo.fromJson(data) : null;
    } catch (_) {
      return null;
    }
  }

  /// Goal minutes badlo (server sirf apni options list me se maanta hai).
  static Future<DailyGoalInfo?> setDailyGoal(int minutes) async {
    try {
      final token = await AuthService.getValidToken();
      if (token == null) return null;
      final res = await http.patch(
        Uri.parse('${Api.baseUrl}/profile/daily-goal/'),
        headers: {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'},
        body: jsonEncode({'goal_minutes': minutes}),
      ).timeout(_timeout);
      if (res.statusCode != 200) return null;
      final body = jsonDecode(res.body);
      final data = body is Map ? body['data'] : null;
      return data is Map<String, dynamic> ? DailyGoalInfo.fromJson(data) : null;
    } catch (_) {
      return null;
    }
  }
}
