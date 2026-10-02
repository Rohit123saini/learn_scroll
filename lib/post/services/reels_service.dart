// ============================================================
// REELS — vertical short-video feed. Service + models only (no UI).
// Backend contract: backend/post/post_app.md -> "Addendum - Reels feed"
//
//   GET  /post/reels/                     first page of a new scrolling session
//   GET  /post/reels/?start=<post_id>     same, but that video is FIRST
//   GET  <ReelsPage.next>                 next page (follow the URL exactly as given)
//
// Response: {count, next, previous: null, results: [Reel]}. Order is frozen per session.
// Seen / watch time reuse the existing endpoints (no Reels-specific action endpoints):
//   POST /post/feed/seen/            {post_ids: [...]}  (<= 50 per call) -> markSeen()
//   POST /post/<id>/video-progress/  {watched_seconds}  -> ApiService.reportVideoProgress
//
// Errors: 404 = the cursor is no longer valid -> start a new session (fetchPage() without
// [nextUrl]); other non-2xx -> ReelsException.
// ============================================================

import 'dart:convert';
import 'package:http/http.dart' as http;

import '../../services/auth_service.dart';
import '../../utils/api.dart';

class ReelsException implements Exception {
  final int statusCode;
  final String message;
  const ReelsException(this.statusCode, this.message);

  /// 404 on a `next` URL: the frozen list / cursor is gone -> begin a new session.
  bool get isInvalidCursor => statusCode == 404;

  @override
  String toString() => 'ReelsException($statusCode): $message';
}

class ReelVideo {
  final String url;
  final String? thumbnail;
  final int? duration; // seconds, may be null for a `?start=` video with unknown length
  final int? width;
  final int? height;
  final String? blurHash; // placeholder while the video buffers
  const ReelVideo({required this.url, this.thumbnail, this.duration, this.width, this.height, this.blurHash});

  /// height / width, or null when a side is unknown.
  double? get aspect => (width != null && height != null && width! > 0) ? height! / width! : null;

  factory ReelVideo.fromJson(Map<String, dynamic> j) => ReelVideo(
        url: '${j['url'] ?? ''}',
        thumbnail: j['thumbnail'] as String?,
        duration: (j['duration'] as num?)?.toInt(),
        width: (j['width'] as num?)?.toInt(),
        height: (j['height'] as num?)?.toInt(),
        blurHash: j['blur_hash'] as String?,
      );
}

class ReelAuthor {
  final String id; // pass to POST /profile/follow/<id>/
  final String username;
  final String names; // full name, '' when the user has none
  final String? profilePhoto;
  final bool isFollowing;
  const ReelAuthor({
    required this.id,
    required this.username,
    required this.names,
    this.profilePhoto,
    required this.isFollowing,
  });

  /// Full name if there is one, else the username.
  String get displayName => names.isNotEmpty ? names : username;

  ReelAuthor copyWith({bool? isFollowing}) => ReelAuthor(
        id: id,
        username: username,
        names: names,
        profilePhoto: profilePhoto,
        isFollowing: isFollowing ?? this.isFollowing,
      );

  factory ReelAuthor.fromJson(Map<String, dynamic> j) => ReelAuthor(
        id: '${j['id']}',
        username: '${j['username'] ?? ''}',
        names: '${j['names'] ?? ''}',
        profilePhoto: j['profile_photo'] as String?,
        isFollowing: j['is_following'] == true,
      );
}

class ReelCounts {
  final int likes;
  final int comments;
  final int shares;
  final int saves;
  const ReelCounts({this.likes = 0, this.comments = 0, this.shares = 0, this.saves = 0});

  ReelCounts copyWith({int? likes, int? comments, int? shares, int? saves}) => ReelCounts(
        likes: likes ?? this.likes,
        comments: comments ?? this.comments,
        shares: shares ?? this.shares,
        saves: saves ?? this.saves,
      );

  factory ReelCounts.fromJson(Map<String, dynamic> j) => ReelCounts(
        likes: (j['likes'] as num?)?.toInt() ?? 0,
        comments: (j['comments'] as num?)?.toInt() ?? 0,
        shares: (j['shares'] as num?)?.toInt() ?? 0,
        saves: (j['saves'] as num?)?.toInt() ?? 0,
      );
}

/// One reel (`results[]` of `GET /post/reels/`). Immutable; use [copyWith] for optimistic UI.
class Reel {
  final String id; // post id
  final ReelVideo video;
  final String caption;
  final List<String> hashtags; // without '#'
  final ReelAuthor author;
  final ReelCounts counts;
  final bool isLiked;
  final String? myReaction; // like | confuse | wrong | imp | explain | null
  final bool isSaved;
  final String? feedSource; // following | recommended
  const Reel({
    required this.id,
    required this.video,
    required this.caption,
    required this.hashtags,
    required this.author,
    required this.counts,
    required this.isLiked,
    this.myReaction,
    required this.isSaved,
    this.feedSource,
  });

  Reel copyWith({
    ReelAuthor? author,
    ReelCounts? counts,
    bool? isLiked,
    String? myReaction,
    bool clearReaction = false,
    bool? isSaved,
  }) =>
      Reel(
        id: id,
        video: video,
        caption: caption,
        hashtags: hashtags,
        author: author ?? this.author,
        counts: counts ?? this.counts,
        isLiked: isLiked ?? this.isLiked,
        myReaction: clearReaction ? null : (myReaction ?? this.myReaction),
        isSaved: isSaved ?? this.isSaved,
        feedSource: feedSource,
      );

  /// Null when the item is not playable (no `video` / empty url) - [ReelsService] drops those.
  static Reel? tryParse(Map<String, dynamic> j) {
    final v = j['video'];
    if (v is! Map) return null;
    final video = ReelVideo.fromJson(Map<String, dynamic>.from(v));
    if (video.url.isEmpty) return null;
    return Reel(
      id: '${j['id']}',
      video: video,
      caption: '${j['caption'] ?? ''}',
      hashtags: ((j['hashtags'] as List?) ?? const []).map((e) => '$e').toList(),
      author: ReelAuthor.fromJson(Map<String, dynamic>.from((j['author'] as Map?) ?? const {})),
      counts: ReelCounts.fromJson(Map<String, dynamic>.from((j['counts'] as Map?) ?? const {})),
      isLiked: j['is_liked'] == true,
      myReaction: j['my_reaction'] as String?,
      isSaved: j['is_saved'] == true,
      feedSource: j['feed_source'] as String?,
    );
  }
}

/// One page of a Reels session.
class ReelsPage {
  final List<Reel> reels;

  /// Full URL of the next page (pass it back to [ReelsService.fetchPage]); null = end of the list.
  final String? nextUrl;

  /// Size of the frozen list for this session (not of this page).
  final int count;
  const ReelsPage({required this.reels, required this.nextUrl, required this.count});

  bool get hasMore => nextUrl != null;
}

class ReelsService {
  ReelsService._();
  static const Duration _timeout = Duration(seconds: 20);
  static const int _seenBatch = 50; // POST /post/feed/seen/ accepts at most 50 ids

  static Future<Map<String, String>> _headers() async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    return {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'};
  }

  /// Fetches one page.
  ///  * new session: `fetchPage()`  (optionally [startPostId] to open a given video first, e.g. from a profile / Home)
  ///  * next page:   `fetchPage(nextUrl: previousPage.nextUrl)` - the URL is used exactly as the server gave it.
  /// Throws [ReelsException] (check [ReelsException.isInvalidCursor] -> restart with `fetchPage()`).
  static Future<ReelsPage> fetchPage({String? nextUrl, String? startPostId, int? pageSize}) async {
    final Uri uri;
    if (nextUrl != null && nextUrl.isNotEmpty) {
      uri = Uri.parse(nextUrl);
    } else {
      uri = Uri.parse('${Api.baseUrl}/post/reels/').replace(queryParameters: {
        if (startPostId != null && startPostId.isNotEmpty) 'start': startPostId,
        if (pageSize != null) 'page_size': '$pageSize',
      });
    }
    final r = await http.get(uri, headers: await _headers()).timeout(_timeout);
    final decoded = jsonDecode(utf8.decode(r.bodyBytes));
    final map = decoded is Map ? Map<String, dynamic>.from(decoded) : <String, dynamic>{};
    if (r.statusCode != 200) {
      throw ReelsException(r.statusCode, (map['message'] ?? map['detail'] ?? 'Request failed (${r.statusCode})').toString());
    }
    final seen = <String>{};
    final reels = <Reel>[];
    for (final e in (map['results'] as List?) ?? const []) {
      if (e is! Map) continue;
      final reel = Reel.tryParse(Map<String, dynamic>.from(e));
      if (reel != null && seen.add(reel.id)) reels.add(reel); // unplayable / duplicate items dropped
    }
    final next = map['next'];
    return ReelsPage(
      reels: reels,
      nextUrl: (next is String && next.isNotEmpty) ? next : null,
      count: (map['count'] as num?)?.toInt() ?? reels.length,
    );
  }

  /// Reports reels that were on screen: existing `POST /post/feed/seen/` (no Reels-specific endpoint).
  /// Fire-and-forget: never throws. Splits into batches of 50.
  static Future<void> markSeen(Iterable<String> postIds) async {
    final ids = postIds.toSet().toList();
    if (ids.isEmpty) return;
    try {
      final headers = await _headers();
      final uri = Uri.parse('${Api.baseUrl}/post/feed/seen/');
      for (var i = 0; i < ids.length; i += _seenBatch) {
        final chunk = ids.sublist(i, i + _seenBatch > ids.length ? ids.length : i + _seenBatch);
        await http.post(uri, headers: headers, body: jsonEncode({'post_ids': chunk})).timeout(_timeout);
      }
    } catch (_) {
      // seen is best effort
    }
  }
}
