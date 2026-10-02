// lib/post/services/post_tag_service.dart
//
// P5b-FE — "tag people" client for the backend P5a-BE endpoints:
//
//   PATCH  /post/<id>/edit/            {"tags": [{"user_id", "x"?, "y"?}, ...]}  (replaces the whole set; [] clears)
//   GET    /post/tagged/<username>/    ?page=&include_hidden=1   (include_hidden only honoured for the owner)
//   PATCH  /post/<id>/tag/             {"hidden": bool}          (tagged user hides/unhides it on THEIR profile)
//   DELETE /post/<id>/tag/             remove my own tag
//
// x / y are relative (0..1) positions on the post's first photo; both null when
// the post has no photo (video / text / document) or the author skipped placing.

import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../profile/model.dart' show PostModel;
import '../../services/auth_service.dart';
import '../../utils/api.dart';

/// A person picked in the composer (or restored from a draft).
class PostTagInput {
  final String userId;
  final String username;
  final String? profilePicture;
  final double? x;
  final double? y;

  const PostTagInput({
    required this.userId,
    required this.username,
    this.profilePicture,
    this.x,
    this.y,
  });

  bool get hasPosition => x != null && y != null;

  PostTagInput copyWith({
    double? x,
    double? y,
    bool clearPosition = false,
  }) {
    return PostTagInput(
      userId: userId,
      username: username,
      profilePicture: profilePicture,
      x: clearPosition ? null : (x ?? this.x),
      y: clearPosition ? null : (y ?? this.y),
    );
  }

  /// Body entry the backend expects. Position is sent only when BOTH x and y exist
  /// (backend rejects x without y) and [withPositions] is true.
  Map<String, dynamic> toJson({bool withPositions = true}) {
    final id = int.tryParse(userId) ?? userId; // backend pk is an integer
    return {
      'user_id': id,
      if (withPositions && hasPosition) 'x': x,
      if (withPositions && hasPosition) 'y': y,
    };
  }
}

/// One row of someone's "Tagged" tab: the post + how THEY are tagged on it.
class TaggedPost {
  final PostModel post;
  final bool isHidden; // only ever true for the tab's owner
  final double? x;
  final double? y;

  const TaggedPost({required this.post, this.isHidden = false, this.x, this.y});

  TaggedPost copyWith({bool? isHidden}) =>
      TaggedPost(post: post, isHidden: isHidden ?? this.isHidden, x: x, y: y);
}

class TaggedPostsPage {
  final List<TaggedPost> items;
  final bool hasMore;
  const TaggedPostsPage(this.items, this.hasMore);
}

class PostTagService {
  PostTagService._();

  static const Duration _timeout = Duration(seconds: 20);

  /// Shared with hashtag_service.dart.
  static Future<Map<String, String>> authHeaders() async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    return {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'};
  }

  static String _errorMessage(http.Response r, String fallback) {
    try {
      final d = jsonDecode(utf8.decode(r.bodyBytes));
      if (d is Map) {
        final m = d['message'] ?? d['detail'] ?? d['error'];
        if (m != null && m.toString().isNotEmpty) return m.toString();
        final errors = d['errors'] ?? d['tags'];
        if (errors != null) return errors.toString();
      }
    } catch (_) {}
    return fallback;
  }

  /// Replace the tag set of one of MY posts. [withPositions] false -> x/y are dropped
  /// (e.g. the photo the positions were placed on is no longer the first one).
  static Future<void> setPostTags(
    String postId,
    List<PostTagInput> tags, {
    bool withPositions = true,
  }) async {
    final r = await http
        .patch(
          Uri.parse('${Api.baseUrl}/post/$postId/edit/'),
          headers: await authHeaders(),
          body: jsonEncode({'tags': [for (final t in tags) t.toJson(withPositions: withPositions)]}),
        )
        .timeout(_timeout);
    if (r.statusCode != 200 && r.statusCode != 201) {
      throw Exception(_errorMessage(r, "Couldn't save tags (${r.statusCode})."));
    }
  }

  static Future<TaggedPostsPage> getTaggedPosts(
    String username, {
    int page = 1,
    bool includeHidden = false,
  }) async {
    final uri = Uri.parse('${Api.baseUrl}/post/tagged/${Uri.encodeComponent(username)}/').replace(
      queryParameters: {
        'page': '$page',
        if (includeHidden) 'include_hidden': '1',
      },
    );
    final r = await http.get(uri, headers: await authHeaders()).timeout(_timeout);
    if (r.statusCode != 200) {
      throw Exception(_errorMessage(r, "Couldn't load tagged posts (${r.statusCode})."));
    }

    dynamic root = jsonDecode(utf8.decode(r.bodyBytes));
    if (root is Map && root['data'] != null && root['results'] == null) root = root['data'];

    List raw = const [];
    String? next;
    if (root is Map) {
      raw = (root['results'] as List?) ?? const [];
      next = root['next']?.toString();
    } else if (root is List) {
      raw = root;
    }

    final items = <TaggedPost>[];
    for (final e in raw) {
      if (e is! Map) continue;
      final row = Map<String, dynamic>.from(e);
      final tag = row['tag'] is Map ? Map<String, dynamic>.from(row['tag'] as Map) : const <String, dynamic>{};
      items.add(TaggedPost(
        post: PostModel.fromJson(row),
        isHidden: tag['is_hidden'] == true,
        x: (tag['x'] as num?)?.toDouble(),
        y: (tag['y'] as num?)?.toDouble(),
      ));
    }
    final hasMore = next != null && next.isNotEmpty && next != 'null';
    return TaggedPostsPage(items, hasMore);
  }

  /// Hide / unhide a post on MY profile's Tagged tab (the tag itself stays on the post).
  static Future<void> setHidden(String postId, bool hidden) async {
    final r = await http
        .patch(
          Uri.parse('${Api.baseUrl}/post/$postId/tag/'),
          headers: await authHeaders(),
          body: jsonEncode({'hidden': hidden}),
        )
        .timeout(_timeout);
    if (r.statusCode != 200) {
      throw Exception(_errorMessage(r, "Couldn't update the tag (${r.statusCode})."));
    }
  }

  /// Remove MY tag from a post (idempotent on the backend).
  static Future<void> removeMyTag(String postId) async {
    final r = await http
        .delete(Uri.parse('${Api.baseUrl}/post/$postId/tag/'), headers: await authHeaders())
        .timeout(_timeout);
    if (r.statusCode != 200 && r.statusCode != 204) {
      throw Exception(_errorMessage(r, "Couldn't remove the tag (${r.statusCode})."));
    }
  }
}
