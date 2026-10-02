// lib/services/activity_service.dart
//
// P14-FE — client for P14-BE (`user_profile` activity endpoints):
//   GET   /profile/activity/            -> ActivitySummary (7 days + saved/liked)
//   PATCH /profile/activity/            -> daily-limit reminder
//   POST  /profile/activity/heartbeat/  -> foreground time (ActivityHeartbeat)
//
// ⚠️ Two assumptions live in the "ASSUMPTIONS" block below — the URL prefix
// and the auth header. Both were inferred (this file's siblings,
// api_service.dart / streak_service.dart, weren't available); if your other
// services differ, fix them HERE, nothing else in P14-FE depends on them.

import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../utils/api.dart';

// ---------------------------------------------------------------- ASSUMPTIONS
String get _base => '${Api.baseUrl}/profile'; // user_profile urls are mounted under /profile/
const _tokenPrefsKey = 'access_token'; // same key main.dart's _checkAuth() reads
const _authScheme = 'Bearer';
// -----------------------------------------------------------------------------

class ActivityException implements Exception {
  final int? statusCode;
  final String message;
  ActivityException(this.message, {this.statusCode});
  @override
  String toString() => message;
}

int? _intOrNull(dynamic v) => v is num ? v.toInt() : null;
int _int(dynamic v) => v is num ? v.toInt() : 0;

class ActivityDay {
  final DateTime date; // local midnight of that day
  final int seconds;
  // null = server couldn't compute it (not the same as 0).
  final int? likes;
  final int? comments;
  final int? shares;

  const ActivityDay({
    required this.date,
    required this.seconds,
    this.likes,
    this.comments,
    this.shares,
  });

  factory ActivityDay.fromJson(Map<String, dynamic> j) => ActivityDay(
        date: DateTime.tryParse((j['date'] ?? '').toString()) ?? DateTime.now(),
        seconds: _int(j['seconds']),
        likes: _intOrNull(j['likes']),
        comments: _intOrNull(j['comments']),
        shares: _intOrNull(j['shares']),
      );
}

class ActivityPost {
  final String postId;
  final String? postType;
  final String? ownerUsername;
  final DateTime? at; // when I liked / saved it

  const ActivityPost({required this.postId, this.postType, this.ownerUsername, this.at});

  factory ActivityPost.fromJson(Map<String, dynamic> j) => ActivityPost(
        postId: (j['post_id'] ?? '').toString(),
        postType: j['post_type']?.toString(),
        ownerUsername: j['owner_username']?.toString(),
        at: DateTime.tryParse((j['at'] ?? '').toString())?.toLocal(),
      );
}

class ActivitySummary {
  final List<ActivityDay> days; // oldest -> newest, last one is today
  final int totalSeconds;
  final int avgSecondsPerDay;
  final int? totalLikes;
  final int? totalComments;
  final int? totalShares;
  final int todaySeconds;
  final int? dailyLimitMinutes; // null = reminder off
  final List<ActivityPost> savedPosts;
  final List<ActivityPost> likedPosts;

  const ActivitySummary({
    required this.days,
    required this.totalSeconds,
    required this.avgSecondsPerDay,
    required this.totalLikes,
    required this.totalComments,
    required this.totalShares,
    required this.todaySeconds,
    required this.dailyLimitMinutes,
    required this.savedPosts,
    required this.likedPosts,
  });

  ActivitySummary copyWith({int? dailyLimitMinutes, bool clearLimit = false}) => ActivitySummary(
        days: days,
        totalSeconds: totalSeconds,
        avgSecondsPerDay: avgSecondsPerDay,
        totalLikes: totalLikes,
        totalComments: totalComments,
        totalShares: totalShares,
        todaySeconds: todaySeconds,
        dailyLimitMinutes: clearLimit ? null : (dailyLimitMinutes ?? this.dailyLimitMinutes),
        savedPosts: savedPosts,
        likedPosts: likedPosts,
      );

  factory ActivitySummary.fromJson(Map<String, dynamic> data) {
    List<T> list<T>(dynamic v, T Function(Map<String, dynamic>) f) => (v as List? ?? const [])
        .whereType<Map>()
        .map((e) => f(Map<String, dynamic>.from(e)))
        .toList();

    final totals = Map<String, dynamic>.from((data['totals'] as Map?) ?? const {});
    final today = Map<String, dynamic>.from((data['today'] as Map?) ?? const {});
    return ActivitySummary(
      days: list(data['days'], ActivityDay.fromJson),
      totalSeconds: _int(totals['seconds']),
      avgSecondsPerDay: _int(totals['avg_seconds_per_day']),
      totalLikes: _intOrNull(totals['likes']),
      totalComments: _intOrNull(totals['comments']),
      totalShares: _intOrNull(totals['shares']),
      todaySeconds: _int(today['seconds']),
      dailyLimitMinutes: _intOrNull(data['daily_limit_minutes']),
      savedPosts: list(data['saved_posts'], ActivityPost.fromJson),
      likedPosts: list(data['liked_posts'], ActivityPost.fromJson),
    );
  }
}

class ActivityService {
  static Future<Map<String, String>> _headers() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString(_tokenPrefsKey) ?? '';
    return {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      if (token.isNotEmpty) 'Authorization': '$_authScheme $token',
    };
  }

  static Map<String, dynamic> _decode(http.Response r) {
    dynamic body;
    try {
      body = jsonDecode(utf8.decode(r.bodyBytes));
    } catch (_) {
      body = null;
    }
    if (r.statusCode < 200 || r.statusCode >= 300) {
      var msg = 'Request failed (${r.statusCode})';
      if (body is Map) {
        final m = body['message'] ?? body['detail'];
        if (m != null) msg = m.toString();
      }
      throw ActivityException(msg, statusCode: r.statusCode);
    }
    if (body is! Map) throw ActivityException('Unexpected response from server');
    return Map<String, dynamic>.from(body);
  }

  /// GET /profile/activity/ — throws [ActivityException] on failure.
  static Future<ActivitySummary> fetch({int limit = 20}) async {
    final r = await http.get(
      Uri.parse('$_base/activity/?limit=$limit'),
      headers: await _headers(),
    );
    final body = _decode(r);
    return ActivitySummary.fromJson(Map<String, dynamic>.from((body['data'] as Map?) ?? const {}));
  }

  /// PATCH /profile/activity/ — [minutes] null or 0 turns the reminder off.
  static Future<void> setDailyLimit(int? minutes) async {
    final r = await http.patch(
      Uri.parse('$_base/activity/'),
      headers: await _headers(),
      body: jsonEncode({'daily_limit_minutes': (minutes == null || minutes == 0) ? null : minutes}),
    );
    _decode(r);
  }

  /// POST /profile/activity/heartbeat/ — returns true when this beat crossed
  /// the user's daily limit (server says so once per day).
  static Future<bool> sendHeartbeat(int seconds) async {
    final r = await http.post(
      Uri.parse('$_base/activity/heartbeat/'),
      headers: await _headers(),
      body: jsonEncode({'seconds': seconds}),
    );
    final body = _decode(r);
    return body['limit_reached'] == true;
  }
}

/// Foreground-time heartbeat. `main.dart`'s `_MyAppState` (a
/// WidgetsBindingObserver) calls [onForeground] / [onBackground] from its
/// lifecycle callback; nothing else needs to touch this.
///
/// While foregrounded it POSTs the elapsed seconds every 60 s; going to the
/// background sends the partial last interval (best effort — the OS may
/// suspend the request). A failed beat is NOT counted as sent, so the next one
/// carries the time (the server caps a single beat at 120 s anyway, so a long
/// outage can't be back-filled — by design). Nothing is sent while logged out.
class ActivityHeartbeat {
  ActivityHeartbeat._();
  static final ActivityHeartbeat instance = ActivityHeartbeat._();

  static const Duration _interval = Duration(seconds: 60);
  static const int _maxBeatSeconds = 120; // keep in step with ACTIVITY_HEARTBEAT_MAX_SECONDS
  static const int _minBeatSeconds = 5; // don't bother the server with tiny slices

  Timer? _timer;
  DateTime? _lastFlush;
  bool _sending = false;

  /// Set once from main.dart; called (at most once a day, per the server)
  /// when this device's beat is the one that crossed the daily limit.
  void Function()? onLimitReached;

  bool get isRunning => _timer != null;

  void onForeground() {
    if (_timer != null) return;
    _lastFlush = DateTime.now();
    _timer = Timer.periodic(_interval, (_) => _flush());
  }

  void onBackground() {
    if (_timer == null) return;
    _timer!.cancel();
    _timer = null;
    // _flush reads `_lastFlush` synchronously before its first await, so it is
    // safe to clear it right after starting the final flush.
    unawaited(_flush());
    _lastFlush = null;
  }

  Future<void> _flush() async {
    final since = _lastFlush;
    if (since == null || _sending) return;
    final now = DateTime.now();
    final seconds = now.difference(since).inSeconds.clamp(0, _maxBeatSeconds).toInt();
    if (seconds < _minBeatSeconds) return;

    _sending = true;
    try {
      final prefs = await SharedPreferences.getInstance();
      final token = prefs.getString(_tokenPrefsKey) ?? '';
      if (token.isEmpty) {
        // Logged out: don't bank this time for whoever logs in next.
        if (_timer != null) _lastFlush = now;
        return;
      }
      final limitReached = await ActivityService.sendHeartbeat(seconds);
      if (_timer != null) _lastFlush = now;
      if (limitReached) onLimitReached?.call();
    } catch (_) {
      // Keep `_lastFlush` as is: the next beat carries this time too.
    } finally {
      _sending = false;
    }
  }
}
