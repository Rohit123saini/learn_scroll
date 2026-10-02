// lib/services/onboarding_service.dart
//
// ============================================================
// TASK G18 (growth_and_feature_tasks.md — Empty states & first-time-user
// onboarding).
//
// Talks to:
//   GET  /post/interests/                 (post.UserInterestsAPIView,
//                                           already exists — TASK 3)
//   PUT  /post/interests/                 (same view, replace-the-set)
//   GET  /core/onboarding/suggestions/    (core.views.OnboardingSuggestionsView)
//   GET  /core/onboarding/complete/       (core.views.OnboardingCompleteView)
//   POST /core/onboarding/complete/       (same view — {"skipped": bool})
//   POST /profile/follow/<id>/            (user_profile.views.FollowAPIView,
//                                           already exists — the real
//                                           follow/unfollow toggle)
//
// Same shared-service shape and same "fail silent, never surface a
// network hiccup as a hard error" contract `recap_service.dart` /
// `streak_service.dart` already document for exactly this reason: a
// one-time onboarding screen that can't load suggestions should just
// show its own empty state (or let the user skip straight through),
// never block or crash the app.
// ============================================================

import 'dart:convert';

import 'package:http/http.dart' as http;

import '../utils/api.dart';
import 'auth_service.dart';

/// One row of `suggested_users` from `/core/onboarding/suggestions/`
/// (shape = `user_profile.serializers.UserSearchSerializer`).
class SuggestedUser {
  final int id;
  final String username;
  final String firstName;
  final String lastName;
  final String? profilePhoto;

  const SuggestedUser({
    required this.id,
    required this.username,
    required this.firstName,
    required this.lastName,
    this.profilePhoto,
  });

  String get displayName {
    final full = '$firstName $lastName'.trim();
    return full.isEmpty ? username : full;
  }

  factory SuggestedUser.fromJson(Map<String, dynamic> json) {
    return SuggestedUser(
      id: (json['id'] as num).toInt(),
      username: (json['username'] as String?) ?? '',
      firstName: (json['first_name'] as String?) ?? '',
      lastName: (json['last_name'] as String?) ?? '',
      profilePhoto: json['profile_photo'] as String?,
    );
  }
}

/// One row of `suggested_campuses`.
class SuggestedCampus {
  final String id;
  final String name;
  final String type;

  const SuggestedCampus({required this.id, required this.name, required this.type});

  factory SuggestedCampus.fromJson(Map<String, dynamic> json) {
    return SuggestedCampus(
      id: json['id'].toString(),
      name: (json['name'] as String?) ?? '',
      type: (json['type'] as String?) ?? '',
    );
  }
}

/// One row of `sample_test_series`
/// (shape = `testseries.serializers.PublicSeriesSerializer`) — kept
/// deliberately minimal here (just what the onboarding card shows); the
/// real `TestSeriesModel` (testseries/services) is used once the user
/// actually opens `TestSeriesDetailScreen`.
class SampleTestSeries {
  final String id;
  final String title;
  final String? description;
  final int durationMinutes;
  final int questionCount;
  final double? avgRating;
  final int reviewCount;
  final String creatorName;

  const SampleTestSeries({
    required this.id,
    required this.title,
    this.description,
    required this.durationMinutes,
    required this.questionCount,
    this.avgRating,
    required this.reviewCount,
    required this.creatorName,
  });

  factory SampleTestSeries.fromJson(Map<String, dynamic> json) {
    return SampleTestSeries(
      id: json['id'].toString(),
      title: (json['title'] as String?) ?? '',
      description: json['description'] as String?,
      durationMinutes: (json['duration_minutes'] as num?)?.toInt() ?? 0,
      questionCount: (json['question_count'] as num?)?.toInt() ?? 0,
      avgRating: (json['avg_rating'] as num?)?.toDouble(),
      reviewCount: (json['review_count'] as num?)?.toInt() ?? 0,
      creatorName: (json['creator_name'] as String?) ?? '',
    );
  }
}

/// Full `/core/onboarding/suggestions/` response.
class OnboardingSuggestions {
  final List<SuggestedUser> users;
  final List<SuggestedCampus> campuses;
  final List<SampleTestSeries> sampleTests;

  const OnboardingSuggestions({
    required this.users,
    required this.campuses,
    required this.sampleTests,
  });

  static const empty = OnboardingSuggestions(users: [], campuses: [], sampleTests: []);

  factory OnboardingSuggestions.fromJson(Map<String, dynamic> json) {
    List<T> _list<T>(String key, T Function(Map<String, dynamic>) from) {
      final raw = json[key];
      if (raw is! List) return <T>[];
      return raw.whereType<Map<String, dynamic>>().map(from).toList();
    }

    return OnboardingSuggestions(
      users: _list('suggested_users', SuggestedUser.fromJson),
      campuses: _list('suggested_campuses', SuggestedCampus.fromJson),
      sampleTests: _list('sample_test_series', SampleTestSeries.fromJson),
    );
  }
}

class OnboardingService {
  OnboardingService._();

  static const Duration _timeout = Duration(seconds: 10);

  static Future<Map<String, String>> _authHeaders() async {
    final token = await AuthService.getValidToken();
    return {
      if (token != null) 'Authorization': 'Bearer $token',
      'Content-Type': 'application/json',
    };
  }

  /// Whether this user has already been through (or skipped) onboarding
  /// on any device — lets the app skip the flow entirely for a returning
  /// user instead of always showing it once after every signup path.
  /// Defaults to `true` (treat as "already done") on any failure — an
  /// onboarding screen that can't be reached should never become a hard
  /// gate in front of the whole app.
  static Future<bool> isFinished() async {
    try {
      final headers = await _authHeaders();
      final res = await http
          .get(Uri.parse('${Api.baseUrl}/core/onboarding/complete/'), headers: headers)
          .timeout(_timeout);
      if (res.statusCode != 200) return true;
      final body = jsonDecode(res.body);
      if (body is! Map<String, dynamic>) return true;
      return (body['completed'] == true) || (body['skipped'] == true);
    } catch (_) {
      return true;
    }
  }

  /// Returns `OnboardingSuggestions.empty` on any failure — the screen's
  /// job is to degrade to "nothing to suggest, just move on", never to
  /// show an error blocking Skip/Next.
  static Future<OnboardingSuggestions> fetchSuggestions() async {
    try {
      final headers = await _authHeaders();
      final res = await http
          .get(Uri.parse('${Api.baseUrl}/core/onboarding/suggestions/'), headers: headers)
          .timeout(_timeout);
      if (res.statusCode != 200) return OnboardingSuggestions.empty;
      final body = jsonDecode(res.body);
      if (body is! Map<String, dynamic>) return OnboardingSuggestions.empty;
      return OnboardingSuggestions.fromJson(body);
    } catch (_) {
      return OnboardingSuggestions.empty;
    }
  }

  /// Replaces the caller's full interest set — mirrors
  /// `UserInterestsUpdateSerializer`'s `{"categories": [...]}` body
  /// exactly (post/views.py, TASK 3). Returns true on success.
  static Future<bool> saveInterests(List<String> categories) async {
    try {
      final headers = await _authHeaders();
      final res = await http
          .put(
            Uri.parse('${Api.baseUrl}/post/interests/'),
            headers: headers,
            body: jsonEncode({'categories': categories}),
          )
          .timeout(_timeout);
      return res.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  /// Toggles follow for `userId` — same endpoint every profile/follow
  /// button in the app already uses. Returns true on success; the
  /// caller (onboarding_screen.dart) treats a failure as "stayed
  /// unfollowed" and lets the user retry the tap.
  static Future<bool> followUser(int userId) async {
    try {
      final headers = await _authHeaders();
      final res = await http
          .post(Uri.parse('${Api.baseUrl}/profile/follow/$userId/'), headers: headers)
          .timeout(_timeout);
      return res.statusCode == 200 || res.statusCode == 201;
    } catch (_) {
      return false;
    }
  }

  /// Marks the flow finished — `skipped: true` if the user tapped Skip,
  /// `false` if they reached and tapped Finish. Best-effort: a failure
  /// here just means the flow might show again next open, which is a
  /// safe fallback, not a crash.
  static Future<void> markComplete({required bool skipped}) async {
    try {
      final headers = await _authHeaders();
      await http
          .post(
            Uri.parse('${Api.baseUrl}/core/onboarding/complete/'),
            headers: headers,
            body: jsonEncode({'skipped': skipped}),
          )
          .timeout(_timeout);
    } catch (_) {
      // Best-effort — see docstring above.
    }
  }
}
