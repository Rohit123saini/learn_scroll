// lib/services/recap_service.dart
//
// ============================================================
// TASK G2 (growth_and_feature_tasks.md — Daily/weekly "recap" screen).
//
// Talks to `GET /profile/recap/latest/` (`user_profile.views.
// WeeklyRecapView`) and `GET /profile/recap/<id>/card/`
// (`WeeklyRecapCardAPIView`, returns `image/png` bytes) — backend. Same
// shared-service shape and same "fail silent, never surface a network
// hiccup to the user" contract `streak_service.dart` already documents
// for exactly this reason: a recap screen that can't load this once
// should just show its own empty state, not crash anything else.
// ============================================================

import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../utils/api.dart';
import 'auth_service.dart';

/// Plain data holder for one `/profile/recap/latest/` response's `data`
/// object. Deliberately NOT a full `models/` entry — same small,
/// service-local scope `StreakInfo` keeps in streak_service.dart.
class WeeklyRecapInfo {
  final int id;
  final DateTime weekStart;
  final DateTime weekEnd;
  final int testsAttempted;
  final int classesAttended;
  final int postsLikedReceived;
  final int streakDays;

  const WeeklyRecapInfo({
    required this.id,
    required this.weekStart,
    required this.weekEnd,
    required this.testsAttempted,
    required this.classesAttended,
    required this.postsLikedReceived,
    required this.streakDays,
  });

  factory WeeklyRecapInfo.fromJson(Map<String, dynamic> json) {
    return WeeklyRecapInfo(
      id: (json['id'] as num).toInt(),
      weekStart: DateTime.parse(json['week_start'] as String),
      weekEnd: DateTime.parse(json['week_end'] as String),
      testsAttempted: (json['tests_attempted'] as num?)?.toInt() ?? 0,
      classesAttended: (json['classes_attended'] as num?)?.toInt() ?? 0,
      postsLikedReceived: (json['posts_liked_received'] as num?)?.toInt() ?? 0,
      streakDays: (json['streak_days'] as num?)?.toInt() ?? 0,
    );
  }
}

class RecapService {
  RecapService._();

  static const Duration _timeout = Duration(seconds: 10);
  static const String _latestEndpoint = '/profile/recap/latest/';

  /// Returns `null` for absolutely any reason it couldn't be fetched
  /// (logged out, offline, server error, no recap generated yet — the
  /// backend 404s that last case) — same "null means nothing to show
  /// yet, not an error to surface" contract `StreakService.fetch()` uses.
  static Future<WeeklyRecapInfo?> fetchLatest() async {
    try {
      final token = await AuthService.getValidToken();
      if (token == null) return null;

      final res = await http.get(
        Uri.parse('${Api.baseUrl}$_latestEndpoint'),
        headers: {'Authorization': 'Bearer $token'},
      ).timeout(_timeout);

      if (res.statusCode != 200) return null;
      final body = jsonDecode(res.body);
      if (body is! Map<String, dynamic>) return null;
      final data = body['data'];
      if (data is! Map<String, dynamic>) return null;
      return WeeklyRecapInfo.fromJson(data);
    } catch (_) {
      return null;
    }
  }

  /// Fetches the rendered PNG share-card for `recapId` as raw bytes.
  /// Returns `null` on any failure, INCLUDING the backend's 501 (Pillow
  /// not installed on that deployment) — the caller (recap_screen.dart)
  /// treats a null exactly like a network failure: show the stats
  /// screen, just hide/disable the "Share" button.
  static Future<Uint8List?> fetchCardPng(int recapId) async {
    try {
      final token = await AuthService.getValidToken();
      if (token == null) return null;

      final res = await http.get(
        Uri.parse('${Api.baseUrl}/profile/recap/$recapId/card/'),
        headers: {'Authorization': 'Bearer $token'},
      ).timeout(_timeout);

      if (res.statusCode != 200) return null;
      return res.bodyBytes;
    } catch (_) {
      return null;
    }
  }
}
