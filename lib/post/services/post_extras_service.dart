// ============================================================
// POST EXTRAS — backend post features that had no Flutter client
//
//   GET    /post/saved/?page=&collection_name=   my saved posts
//   GET    /post/explore/?page=&category=        discovery feed (not own / not followed)
//   GET    /post/hashtag/<tag>/?page=            posts carrying a hashtag
//   DELETE /post/<id>/delete/                    delete own post (204)
//
// All three list endpoints return DRF-paginated `PostListSerializer` rows —
// the same shape `FeedResponse` already parses for the home feed.
// ============================================================

import 'dart:convert';
import 'package:http/http.dart' as http;

import '../../services/auth_service.dart';
import '../../services/home_api_model_service.dart' show FeedResponse;
import '../../utils/api.dart';

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

  /// True when the post is gone (204/200). 403 → not the owner, 404 → already deleted.
  static Future<bool> deletePost(String postId) async {
    final r = await http
        .delete(Uri.parse('${Api.baseUrl}/post/$postId/delete/'), headers: await _headers())
        .timeout(_timeout);
    return r.statusCode == 204 || r.statusCode == 200 || r.statusCode == 404;
  }
}
