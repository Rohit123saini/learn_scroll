// lib/leaderboard/services/leaderboard_service.dart
//
// ============================================================
// TASK G7 (growth_and_feature_tasks.md — Leaderboards).
//
// Talks to `GET /leaderboard/board/` and `GET /leaderboard/my-rank/`
// (backend: leaderboard/views.py). ONE service reused across all three
// scopes (test series / campus section / app-wide engagement) — same
// "one widget/one service, different scope_type" shape the backend's
// views.py already documents at its own top.
//
// Same "fail silent, return null, never crash a screen over a leaderboard
// widget" contract `services/streak_service.dart` uses for exactly the
// same reason: a leaderboard tab that can't load should show a retry
// state, not take the whole screen down with it — but here that's the
// caller's job (see LeaderboardBoardWidget), so this service surfaces a
// real exception instead of swallowing it, matching
// `testseries/services/testseries_service.dart`'s typed-exception style.
// ============================================================

import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../services/auth_service.dart';
import '../../utils/api.dart';
import '../models/leaderboard_models.dart';

class LeaderboardApiException implements Exception {
  final String message;
  final int? statusCode;
  const LeaderboardApiException(this.message, {this.statusCode});

  bool get isForbidden => statusCode == 403;
  bool get isUnauthorized => statusCode == 401;

  @override
  String toString() => message;
}

class LeaderboardService {
  LeaderboardService._();

  static const Duration _timeout = Duration(seconds: 12);
  static const String _boardEndpoint = '/leaderboard/board/';
  static const String _myRankEndpoint = '/leaderboard/my-rank/';

  static Future<Map<String, String>> _headers() async {
    final token = await AuthService.getValidToken();
    if (token == null) {
      throw const LeaderboardApiException('Not signed in', statusCode: 401);
    }
    return {'Authorization': 'Bearer $token'};
  }

  static Map<String, String> _scopeParams(
    LeaderboardScope scope,
    String? scopeId,
    LeaderboardPeriod period,
  ) {
    final params = <String, String>{
      'scope_type': scope.apiValue,
      'period': period.apiValue,
    };
    if (scopeId != null) params['scope_id'] = scopeId;
    return params;
  }

  /// One page of a board (top `limit` starting at `offset`) plus the
  /// caller's own rank on that same board, in a single round trip.
  static Future<LeaderboardBoard> getBoard({
    required LeaderboardScope scope,
    String? scopeId,
    required LeaderboardPeriod period,
    int limit = 20,
    int offset = 0,
  }) async {
    final headers = await _headers();
    final params = _scopeParams(scope, scopeId, period)
      ..addAll({'limit': '$limit', 'offset': '$offset'});
    final uri = Uri.parse('${Api.baseUrl}$_boardEndpoint').replace(queryParameters: params);

    http.Response res;
    try {
      res = await http.get(uri, headers: headers).timeout(_timeout);
    } on TimeoutException {
      throw const LeaderboardApiException('Request timed out');
    } catch (_) {
      throw const LeaderboardApiException('Network error');
    }

    if (res.statusCode == 403) {
      throw const LeaderboardApiException('You don\u2019t have access to this leaderboard', statusCode: 403);
    }
    if (res.statusCode != 200) {
      throw LeaderboardApiException('Failed to load leaderboard (${res.statusCode})', statusCode: res.statusCode);
    }

    final body = jsonDecode(res.body);
    if (body is! Map<String, dynamic>) {
      throw const LeaderboardApiException('Unexpected response');
    }
    return LeaderboardBoard.fromJson(body, scope: scope);
  }

  /// Just the caller's own row — for a compact "You're #14 this week" chip
  /// somewhere other than a full board screen (e.g. a profile summary
  /// card). Returns `null` on any failure, same contract as
  /// `StreakService.fetch()` — this is a nice-to-have chip, not core flow.
  static Future<LeaderboardEntry?> getMyRank({
    required LeaderboardScope scope,
    String? scopeId,
    required LeaderboardPeriod period,
  }) async {
    try {
      final headers = await _headers();
      final params = _scopeParams(scope, scopeId, period);
      final uri = Uri.parse('${Api.baseUrl}$_myRankEndpoint').replace(queryParameters: params);
      final res = await http.get(uri, headers: headers).timeout(_timeout);
      if (res.statusCode != 200) return null;
      final body = jsonDecode(res.body);
      if (body is! Map<String, dynamic>) return null;
      if (body['rank'] == null) return null;
      return LeaderboardEntry.fromJson(body);
    } catch (_) {
      return null;
    }
  }
}
