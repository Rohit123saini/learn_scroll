// ============================================================
// POST EXTRAS — backend post features that had no Flutter client
//
//   GET    /post/saved/?page=&collection_name=   my saved posts
//   GET    /post/explore/?page=&category=        discovery feed (not own / not followed)
//   GET    /post/hashtag/<tag>/?page=            posts carrying a hashtag
//   DELETE /post/<id>/delete/                    delete own post (204)
//   POST   /post/<id>/repost/                    repost (quick, or with `repost_caption`)
//   PATCH  /post/<id>/edit/                       edit own post's text/category fields
//   PATCH  /post/<id>/visibility/                 change own post's visibility
//
// All three list endpoints return DRF-paginated `PostListSerializer` rows —
// the same shape `FeedResponse` already parses for the home feed.
// ============================================================

import 'dart:convert';
import 'package:http/http.dart' as http;

import '../../services/auth_service.dart';
import '../../services/home_api_model_service.dart' show FeedResponse;
import '../../utils/api.dart';

/// Result of `POST /post/<id>/repost/`.
class RepostResult {
  final String repostId; // the NEW repost row's id (needed to undo it)
  final int repostsCount; // original's fresh reposts_count
  const RepostResult({required this.repostId, required this.repostsCount});
}

/// Repost failure carrying the backend's message (403 private/blocked/not
/// public, 404 gone, 400 caption too long) so the UI can decide what to show.
class RepostException implements Exception {
  final int statusCode;
  final String message;
  const RepostException(this.statusCode, this.message);
  @override
  String toString() => 'RepostException($statusCode): $message';
}

class PostExtrasService {
  PostExtrasService._();
  static const Duration _timeout = Duration(seconds: 20);

  static Future<Map<String, String>> _headers() async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    return {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'};
  }

  static Future<FeedResponse> _feed(Uri uri) async {
    final r = await http.get(uri, headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) throw Exception('Failed to load posts (${r.statusCode})');
    return FeedResponse.fromJson(Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  static Future<FeedResponse> saved({int page = 1, String? collection}) => _feed(
        Uri.parse('${Api.baseUrl}/post/saved/').replace(queryParameters: {
          'page': '$page',
          if (collection != null && collection.isNotEmpty) 'collection_name': collection,
        }),
      );

  static Future<FeedResponse> explore({int page = 1, String? category}) => _feed(
        Uri.parse('${Api.baseUrl}/post/explore/').replace(queryParameters: {
          'page': '$page',
          if (category != null && category.isNotEmpty) 'category': category,
        }),
      );

  static Future<FeedResponse> hashtag(String tag, {int page = 1}) {
    final clean = tag.startsWith('#') ? tag.substring(1) : tag;
    return _feed(
      Uri.parse('${Api.baseUrl}/post/hashtag/${Uri.encodeComponent(clean)}/').replace(queryParameters: {'page': '$page'}),
    );
  }

  /// Repost [postId] as the current user. No [caption] = quick repost (one
  /// tap, like a retweet); a non-blank [caption] = "repost with caption".
  /// Reposting a repost is fine — the backend re-points it at the root original.
  static Future<RepostResult> repost(String postId, {String? caption}) async {
    final text = caption?.trim();
    final r = await http
        .post(
          Uri.parse('${Api.baseUrl}/post/$postId/repost/'),
          headers: await _headers(),
          body: jsonEncode({if (text != null && text.isNotEmpty) 'repost_caption': text}),
        )
        .timeout(_timeout);
    Map<String, dynamic> body = {};
    try {
      body = Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map);
    } catch (_) {}
    if (r.statusCode != 201 && r.statusCode != 200) {
      throw RepostException(r.statusCode, body['message']?.toString() ?? 'Repost failed (${r.statusCode})');
    }
    final data = (body['data'] as Map?) ?? {};
    final original = (body['original'] as Map?) ?? {};
    return RepostResult(
      repostId: data['id']?.toString() ?? '',
      repostsCount: (original['reposts_count'] as int?) ?? 0,
    );
  }

  /// True when the post is gone (204/200). 403 → not the owner, 404 → already deleted.
  static Future<bool> deletePost(String postId) async {
    final r = await http
        .delete(Uri.parse('${Api.baseUrl}/post/$postId/delete/'), headers: await _headers())
        .timeout(_timeout);
    return r.statusCode == 204 || r.statusCode == 200 || r.statusCode == 404;
  }

  /// Edits [postId]'s own text/category fields (title/content/category/
  /// subcategory/hashtags) — never media, `post_type`, or `visibility`
  /// (see [updateVisibility] for that). Only the keys actually present in
  /// [fields] are sent, so callers can PATCH just what changed.
  /// Throws [PostEditException] with the backend's message on failure
  /// (403 not the owner, 400 e.g. text post edited to empty content).
  static Future<void> editPost(String postId, Map<String, dynamic> fields) async {
    final r = await http
        .patch(
          Uri.parse('${Api.baseUrl}/post/$postId/edit/'),
          headers: await _headers(),
          body: jsonEncode(fields),
        )
        .timeout(_timeout);
    if (r.statusCode == 200) return;
    Map<String, dynamic> body = {};
    try {
      body = Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map);
    } catch (_) {}
    throw PostEditException(r.statusCode, body['message']?.toString() ?? 'Edit failed (${r.statusCode})');
  }

  /// Changes [postId]'s visibility to `public` / `connections` / `private`.
  /// Throws [PostEditException] with the backend's message on failure.
  static Future<void> updateVisibility(String postId, String visibility) async {
    final r = await http
        .patch(
          Uri.parse('${Api.baseUrl}/post/$postId/visibility/'),
          headers: await _headers(),
          body: jsonEncode({'visibility': visibility}),
        )
        .timeout(_timeout);
    if (r.statusCode == 200) return;
    Map<String, dynamic> body = {};
    try {
      body = Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map);
    } catch (_) {}
    throw PostEditException(r.statusCode, body['message']?.toString() ?? 'Update failed (${r.statusCode})');
  }
}

/// Edit/visibility-update failure carrying the backend's message (403 not
/// the owner, 400 validation) so the UI can show it directly.
class PostEditException implements Exception {
  final int statusCode;
  final String message;
  const PostEditException(this.statusCode, this.message);
  @override
  String toString() => 'PostEditException($statusCode): $message';
}
