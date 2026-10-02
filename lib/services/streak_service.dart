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

  const StreakInfo({
    required this.currentStreak,
    required this.longestStreak,
    required this.totalActiveDays,
    required this.isActiveToday,
  });

  factory StreakInfo.fromJson(Map<String, dynamic> json) {
    return StreakInfo(
      currentStreak: (json['current_streak'] as num?)?.toInt() ?? 0,
      longestStreak: (json['longest_streak'] as num?)?.toInt() ?? 0,
      totalActiveDays: (json['total_active_days'] as num?)?.toInt() ?? 0,
      isActiveToday: json['is_active_today'] == true,
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

  const StreakCheckInResult({
    required this.streak,
    this.milestoneReached,
    this.bonusCoins = 0,
  });
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
      );
    } catch (_) {
      return null;
    }
  }
}
