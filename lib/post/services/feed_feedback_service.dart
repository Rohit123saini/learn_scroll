// ============================================================
// FEED FEEDBACK — "Not interested", "Mute", "Show fewer like this",
// "Why am I seeing this". Backend contract: post/FEED_FEEDBACK_CONTROLS_TASK.md
//
//   POST   /post/<id>/not-interested/  {reason?}            hide one post          (201 new / 200 repeat)
//   DELETE /post/<id>/not-interested/                       undo hide              (always 200)
//   POST   /post/muted-accounts/       {user_id}            mute an account        (201 / 200)
//   DELETE /post/muted-accounts/<uid>/                      unmute                 (always 200)
//   POST   /post/<id>/show-fewer/      {targets:[{kind,key?}], reason?}
//                                                            hide + dampen          (201 / 200)
//   GET    /post/feedback/?kind=       my "show fewer" list  (paginated)
//   DELETE /post/feedback/<feedback_id>/                    stop dampening one item (always 200)
//   GET    /post/<id>/why/                                   why is this post in my feed
//
// All calls need the bearer token. Errors are `{success:false, message}` with
// 400 (bad input / own post) or 404 (post gone / private) -> FeedFeedbackException.
// ============================================================

import 'dart:convert';
import 'package:http/http.dart' as http;

import '../../services/auth_service.dart';
import '../../utils/api.dart';

class FeedFeedbackException implements Exception {
  final int statusCode;
  final String message;
  const FeedFeedbackException(this.statusCode, this.message);
  @override
  String toString() => 'FeedFeedbackException($statusCode): $message';
}

/// What "Show fewer" can dampen. [wire] is the value sent as `kind`.
enum FeedbackKind {
  category('category'),
  hashtag('hashtag'),
  author('author');

  final String wire;
  const FeedbackKind(this.wire);
  static FeedbackKind? parse(String? v) {
    for (final k in FeedbackKind.values) {
      if (k.wire == v) return k;
    }
    return null;
  }
}

/// One thing to see fewer of. [hashtag] needs [key] (one of the post's tags,
/// with or without '#'); category / author are derived by the server.
class ShowFewerTarget {
  final FeedbackKind kind;
  final String? key;
  const ShowFewerTarget.category() : kind = FeedbackKind.category, key = null;
  const ShowFewerTarget.author() : kind = FeedbackKind.author, key = null;
  const ShowFewerTarget.hashtag(String tag) : kind = FeedbackKind.hashtag, key = tag;

  Map<String, dynamic> toJson() => {'kind': kind.wire, if (key != null) 'key': key};
}

/// A stored "show fewer" preference (`data.feedback[]` and `GET /post/feedback/`).
class FeedFeedbackItem {
  final String id; // use with DELETE /post/feedback/<id>/
  final FeedbackKind kind;
  final String key;
  final String? label; // "Technology" | "#python" | "@username" (null if the author is gone)
  final double strength; // decayed weight, 0..3 — e.g. show as 1 / 2 / 3 dots
  const FeedFeedbackItem({required this.id, required this.kind, required this.key, this.label, required this.strength});

  factory FeedFeedbackItem.fromJson(Map<String, dynamic> j) => FeedFeedbackItem(
        id: '${j['id']}',
        kind: FeedbackKind.parse(j['kind'] as String?) ?? FeedbackKind.category,
        key: '${j['key']}',
        label: j['label'] as String?,
        strength: (j['strength'] as num?)?.toDouble() ?? 0,
      );
}

class ShowFewerResult {
  final bool newlyHidden; // 201 = hidden just now, 200 = it already was
  final List<FeedFeedbackItem> feedback;
  const ShowFewerResult({required this.newlyHidden, required this.feedback});
}

/// One line of the "Why am I seeing this" sheet. Switch on [code] for the icon;
/// show [text] (English, ready to display) until it is localised in the app.
class WhyReason {
  /// following | trending | interest_category | liked_category |
  /// friend_of_follow | popular | own_post | not_in_feed
  final String code;
  final String text;
  final Map<String, dynamic> meta; // following: user_id, username | *_category: category, label | friend_of_follow: via[{id,username}]
  const WhyReason({required this.code, required this.text, required this.meta});

  factory WhyReason.fromJson(Map<String, dynamic> j) => WhyReason(
        code: '${j['code']}',
        text: '${j['text']}',
        meta: Map<String, dynamic>.from((j['meta'] as Map?) ?? const {}),
      );
}

class WhyResult {
  final String postId;
  final String? feedSource; // following | recommended | trending | null (not part of my feed)
  final List<WhyReason> reasons; // [0] = headline
  final List<FeedFeedbackItem> dampened; // my active "show fewer" rows matching this post (id is empty here)
  const WhyResult({required this.postId, this.feedSource, required this.reasons, required this.dampened});

  factory WhyResult.fromJson(Map<String, dynamic> j) => WhyResult(
        postId: '${j['post_id']}',
        feedSource: j['feed_source'] as String?,
        reasons: ((j['reasons'] as List?) ?? const [])
            .map((e) => WhyReason.fromJson(Map<String, dynamic>.from(e as Map)))
            .toList(),
        dampened: ((j['dampened'] as List?) ?? const [])
            .map((e) => FeedFeedbackItem.fromJson({'id': '', ...Map<String, dynamic>.from(e as Map)}))
            .toList(),
      );
}

class FeedFeedbackService {
  FeedFeedbackService._();
  static const Duration _timeout = Duration(seconds: 20);

  static Future<Map<String, String>> _headers() async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    return {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'};
  }

  static Uri _u(String path, [Map<String, String>? q]) => Uri.parse('${Api.baseUrl}/post/$path').replace(queryParameters: q);

  static Map<String, dynamic> _body(http.Response r, {Set<int> ok = const {200, 201}}) {
    final decoded = jsonDecode(utf8.decode(r.bodyBytes));
    final map = decoded is Map ? Map<String, dynamic>.from(decoded) : <String, dynamic>{};
    if (!ok.contains(r.statusCode)) {
      throw FeedFeedbackException(r.statusCode, (map['message'] ?? map['detail'] ?? 'Request failed (${r.statusCode})').toString());
    }
    return map;
  }

  // ---- Part 1 -------------------------------------------------------------
  /// "Not interested". Show an Undo snackbar and call [undoNotInterested].
  static Future<void> notInterested(String postId, {String reason = 'not_interested'}) async {
    final r = await http
        .post(_u('$postId/not-interested/'), headers: await _headers(), body: jsonEncode({'reason': reason}))
        .timeout(_timeout);
    _body(r);
  }

  static Future<void> undoNotInterested(String postId) async {
    final r = await http.delete(_u('$postId/not-interested/'), headers: await _headers()).timeout(_timeout);
    _body(r);
  }

  static Future<void> mute(String userId) async {
    final r = await http
        .post(_u('muted-accounts/'), headers: await _headers(), body: jsonEncode({'user_id': userId}))
        .timeout(_timeout);
    _body(r);
  }

  static Future<void> unmute(String userId) async {
    final r = await http.delete(_u('muted-accounts/$userId/'), headers: await _headers()).timeout(_timeout);
    _body(r);
  }

  // ---- Part 2 -------------------------------------------------------------
  /// "Show fewer like this": hides the post at once AND dampens [targets]
  /// (max 5). Remove the post from the list immediately; for Undo call
  /// [undoNotInterested] + [removeFeedback] for every item of the result.
  static Future<ShowFewerResult> showFewer(
    String postId,
    List<ShowFewerTarget> targets, {
    String reason = 'not_interested',
  }) async {
    final r = await http
        .post(
          _u('$postId/show-fewer/'),
          headers: await _headers(),
          body: jsonEncode({'targets': targets.map((t) => t.toJson()).toList(), 'reason': reason}),
        )
        .timeout(_timeout);
    final data = Map<String, dynamic>.from(_body(r)['data'] as Map);
    return ShowFewerResult(
      newlyHidden: r.statusCode == 201,
      feedback: ((data['feedback'] as List?) ?? const [])
          .map((e) => FeedFeedbackItem.fromJson(Map<String, dynamic>.from(e as Map)))
          .toList(),
    );
  }

  /// "Manage" screen: everything I asked to see fewer of (first page only; follow `next` for more).
  static Future<List<FeedFeedbackItem>> myFeedback({FeedbackKind? kind}) async {
    final r = await http
        .get(_u('feedback/', {if (kind != null) 'kind': kind.wire}), headers: await _headers())
        .timeout(_timeout);
    return ((_body(r)['results'] as List?) ?? const [])
        .map((e) => FeedFeedbackItem.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  static Future<void> removeFeedback(String feedbackId) async {
    final r = await http.delete(_u('feedback/$feedbackId/'), headers: await _headers()).timeout(_timeout);
    _body(r);
  }

  /// "Why am I seeing this" bottom sheet.
  static Future<WhyResult> why(String postId) async {
    final r = await http.get(_u('$postId/why/'), headers: await _headers()).timeout(_timeout);
    return WhyResult.fromJson(Map<String, dynamic>.from(_body(r)['data'] as Map));
  }
}
