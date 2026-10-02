// lib/post/services/highlight_service.dart
//
// Story Highlights — read (P1) + write (P2) calls. Style mirrors
// `story_service.dart` (http + AuthService.getValidToken + kApiTimeout).
//
// Backend (post/highlight_views.py):
//   GET    /post/highlights/?user_id=            list (omit user_id = mine). {"count","results"}
//                                                 Not allowed to see => EMPTY list (200), never 403.
//   POST   /post/highlights/                     {title?, story_ids[], cover_story_id?} -> 201 detail
//   GET    /post/highlights/<id>/                detail (+ `stories`), 404 = hidden/gone
//   PATCH  /post/highlights/<id>/                owner: {title?, story_ids? (ordered, replaces set),
//                                                 cover_story_id? (null = automatic)} -> detail
//   DELETE /post/highlights/<id>/                owner: 204
//   POST   /post/highlights/<id>/stories/        owner: {story_id} -> 201 added / 200 already there
//   DELETE /post/highlights/<id>/stories/<sid>/  owner: idempotent; last story deletes the highlight
//   GET    /post/stories/archive/?page=          owner: stories that can go into a highlight
//
// Errors are 400 {"detail", "code", <field>: [msg]} — surfaced as `HighlightException(message)`.

import 'dart:convert';
import 'package:http/http.dart' as http;
import '../../utils/api.dart';
import '../../services/auth_service.dart';
import '../../services/home_api_model_service.dart' show kApiTimeout;
import '../models/highlight_model.dart';
import '../models/story_model.dart' show StoryModel;

class HighlightService {
  static String get _base => "${Api.baseUrl}/post/highlights";

  static Future<Map<String, String>> _headers({bool json = false}) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    return {"Authorization": "Bearer $token", if (json) "Content-Type": "application/json"};
  }

  // ───────────────────────── read ─────────────────────────

  /// [userId] null => the caller's own highlights.
  static Future<List<Highlight>> getHighlights({int? userId}) async {
    final uri = Uri.parse("$_base/").replace(queryParameters: {
      if (userId != null) 'user_id': '$userId',
    });
    final res = await http.get(uri, headers: await _headers()).timeout(kApiTimeout);
    if (res.statusCode == 200) {
      final decoded = jsonDecode(res.body);
      final List raw = decoded is Map ? (decoded['results'] as List? ?? []) : decoded as List;
      return raw.whereType<Map>().map((e) => Highlight.fromJson(Map<String, dynamic>.from(e))).toList();
    }
    throw Exception('Failed to load highlights: ${res.statusCode}');
  }

  /// One highlight + its stories (only the ones the caller may see).
  static Future<Highlight> getHighlight(String highlightId) async {
    final res = await http.get(Uri.parse("$_base/$highlightId/"), headers: await _headers()).timeout(kApiTimeout);
    if (res.statusCode == 200) {
      return Highlight.fromJson(Map<String, dynamic>.from(jsonDecode(res.body) as Map));
    }
    throw HighlightUnavailableException(res.statusCode);
  }

  /// Owner only. `page` is 1-based (StandardPagination).
  static Future<StoryArchivePage> getArchive({int page = 1}) async {
    final uri = Uri.parse("${Api.baseUrl}/post/stories/archive/").replace(queryParameters: {'page': '$page'});
    final res = await http.get(uri, headers: await _headers()).timeout(kApiTimeout);
    if (res.statusCode != 200) throw Exception('Failed to load archive: ${res.statusCode}');
    final decoded = jsonDecode(res.body);
    final List raw = decoded is Map ? (decoded['results'] as List? ?? []) : decoded as List;
    return StoryArchivePage(
      stories: raw.whereType<Map>().map((e) => StoryModel.fromJson(Map<String, dynamic>.from(e))).toList(),
      hasNext: decoded is Map && decoded['next'] != null,
    );
  }

  // ───────────────────────── write ─────────────────────────

  static Future<Highlight> createHighlight({
    String? title,
    required List<String> storyIds,
    String? coverStoryId,
  }) async {
    final res = await http
        .post(
          Uri.parse("$_base/"),
          headers: await _headers(json: true),
          body: jsonEncode({
            if (title != null && title.trim().isNotEmpty) 'title': title.trim(),
            'story_ids': storyIds,
            if (coverStoryId != null) 'cover_story_id': coverStoryId,
          }),
        )
        .timeout(kApiTimeout);
    return _detailOrThrow(res, 201, 'Could not create highlight');
  }

  /// Only what is passed is sent. `updateCover: true` sends `cover_story_id`
  /// even when null (null = back to the automatic cover).
  static Future<Highlight> updateHighlight(
    String highlightId, {
    String? title,
    List<String>? storyIds,
    bool updateCover = false,
    String? coverStoryId,
  }) async {
    final res = await http
        .patch(
          Uri.parse("$_base/$highlightId/"),
          headers: await _headers(json: true),
          body: jsonEncode({
            if (title != null) 'title': title,
            if (storyIds != null) 'story_ids': storyIds,
            if (updateCover) 'cover_story_id': coverStoryId,
          }),
        )
        .timeout(kApiTimeout);
    return _detailOrThrow(res, 200, 'Could not update highlight');
  }

  static Future<void> deleteHighlight(String highlightId) async {
    final res = await http.delete(Uri.parse("$_base/$highlightId/"), headers: await _headers()).timeout(kApiTimeout);
    if (res.statusCode != 204 && res.statusCode != 404) {
      throw HighlightException(_errorText(res.body) ?? 'Could not delete highlight (${res.statusCode})');
    }
  }

  /// Returns true if newly added, false if it was already in the highlight.
  static Future<bool> addStory(String highlightId, String storyId) async {
    final res = await http
        .post(
          Uri.parse("$_base/$highlightId/stories/"),
          headers: await _headers(json: true),
          body: jsonEncode({'story_id': storyId}),
        )
        .timeout(kApiTimeout);
    if (res.statusCode == 201) return true;
    if (res.statusCode == 200) return false;
    throw HighlightException(_errorText(res.body) ?? 'Could not add to highlight (${res.statusCode})');
  }

  // ───────────────────────── helpers ─────────────────────────

  static Highlight _detailOrThrow(http.Response res, int okCode, String fallback) {
    if (res.statusCode == okCode) {
      return Highlight.fromJson(Map<String, dynamic>.from(jsonDecode(res.body) as Map));
    }
    throw HighlightException(_errorText(res.body) ?? '$fallback (${res.statusCode})');
  }

  static String? _errorText(String body) {
    try {
      final d = jsonDecode(body);
      if (d is! Map) return null;
      if (d['detail'] != null) return d['detail'].toString();
      for (final v in d.values) {
        if (v is List && v.isNotEmpty) return v.first.toString();
        if (v is String && v.isNotEmpty) return v;
      }
    } catch (_) {}
    return null;
  }
}

class StoryArchivePage {
  final List<StoryModel> stories;
  final bool hasNext;
  const StoryArchivePage({required this.stories, required this.hasNext});
}

/// A rule was broken (400) — `message` is already user-readable.
class HighlightException implements Exception {
  final String message;
  HighlightException(this.message);
  @override
  String toString() => message;
}

/// Detail answered non-200 (404 = deleted, or every story hidden from viewer).
class HighlightUnavailableException implements Exception {
  final int statusCode;
  HighlightUnavailableException(this.statusCode);
  @override
  String toString() => 'Highlight unavailable ($statusCode)';
}
