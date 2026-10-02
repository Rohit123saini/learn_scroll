// lib/post/services/story_service.dart
//
// Task 4 — Stories: read (list) + write (create, mark-viewed) calls.
// Read-side (`getStories()`) lives here too, not in
// `services/home_api_model_service.dart` — that file's own comments say
// it moved here (see its "3.3 — stories row" section), alongside the
// story models that moved to `post/models/story_model.dart`.
//
// `createStory()`/`markViewed()` were always here for the calls that
// don't fit `HomeExtrasService`'s cache-first Future.wait shape: uploading
// a new story (multipart, needs a File + progress) and marking one viewed
// (fire-and-forget POST). Multipart pattern below is copied from
// `comment_service.dart`'s `_createCommentNormal()` — same libraries,
// same content-type handling — so it behaves identically to the upload
// path that's already production-tested there.
//
// Backend (confirmed, post_app.md §16.2 / post/views.py):
//   GET  /post/stories/            — list, active/non-expired only
//   POST /post/stories/create/     — multipart: media (file, required),
//                                     media_type ("image"|"video"), caption
//                                     (optional), expires_at (optional —
//                                     server defaults to +24h if omitted)
//   POST /post/stories/<id>/view/  — marks viewed, returns
//                                     {"success": true, "views_count": N}

// TASK 2 FOLLOW-UP — `createStory()` now takes an optional `onProgress`
// callback and reports real upload progress via `dio`'s `onSendProgress`,
// same fix as `comment_service.dart`'s `_createCommentNormal()` (this
// file's own header already says its multipart pattern was copied from
// that method — so it had the exact same gap: `package:http`'s
// `MultipartRequest.send()` has no upload-progress hook, so home.dart's
// own-story tile could only ever show a boolean spinner, never a real
// percentage, no matter how long the upload took). `markViewed()` is a
// tiny fire-and-forget POST with no body worth tracking — left on `http`,
// unchanged.

import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:dio/dio.dart' as dio;
import 'package:http_parser/http_parser.dart';
import 'package:mime/mime.dart';
import '../../utils/api.dart';
import '../../services/auth_service.dart';
import '../../services/home_api_model_service.dart' show kApiTimeout;
import '../models/story_model.dart'
    show StoryModel, StoryViewerEntry, StickerDraft, StorySticker, StoryMentionCandidate, StickerResponses;

class StoryService {
  static String get _base => "${Api.baseUrl}/post/stories";

  /// Read-side — GET /post/stories/, all of the caller's active
  /// (non-expired) stories plus everyone they follow's, flat (not yet
  /// grouped by user). `home.dart`'s `_loadHomeExtras()` Future.wait's
  /// this alongside classrooms/live-now, same as `HomeExtrasService`'s
  /// other GETs, then `groupStories()` (see `post/models/story_model.dart`)
  /// turns the flat list into one ring per user.
  ///
  /// No client-side cache here on purpose — a story row is short-lived by
  /// definition (24h) and the row is meant to reflect just-uploaded/
  /// just-viewed state immediately on return from the viewer/upload sheet
  /// (`home.dart` already forces a refetch via `_loadHomeExtras()` in both
  /// of those `.then()`s), so a stale-while-revalidate cache would only
  /// ever fight that refetch. Same plain-fetch shape as
  /// `HomeExtrasService.getMyClassrooms()`/`getLiveNow()`, which this call
  /// sits alongside in the same `Future.wait`.
  static Future<List<StoryModel>> getStories() async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("$_base/");
    final response = await http
        .get(url, headers: {"Authorization": "Bearer $token"})
        .timeout(kApiTimeout);
    if (response.statusCode == 200) {
      final decoded = jsonDecode(response.body);
      // DRF-paginated ({"results": [...]}) or a plain list — same both-
      // shapes handling `HomeExtrasService.getMyClassrooms()` already uses
      // elsewhere in this codebase for the same reason (pagination can be
      // toggled backend-side without the client shape being guaranteed).
      final List raw = decoded is Map ? (decoded['results'] as List? ?? []) : decoded as List;
      return raw
          .whereType<Map>()
          .map((e) => StoryModel.fromJson(Map<String, dynamic>.from(e)))
          .toList();
    } else {
      throw Exception('Failed to load stories: ${response.statusCode}');
    }
  }

  /// Uploads a new story. `mediaType` must be "image" or "video" — pick it
  /// from how the file was captured/picked, same as the comment-sheet does
  /// for its own media (don't try to sniff it from the file extension).
  static Future<StoryModel> createStory({
    required File media,
    required String mediaType,
    String? caption,
    String audience = 'everyone', // 'everyone' | 'close_friends'
    List<StickerDraft> stickers = const [], // mention / link / poll / question overlays
    Function(double percent)? onProgress,
  }) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');

    final mimeStr = lookupMimeType(media.path) ?? 'application/octet-stream';
    final ms = mimeStr.split('/');
    final Map<String, dynamic> fields = {
      'media_type': mediaType,
      'media': await dio.MultipartFile.fromFile(
        media.path,
        filename: media.path.split('/').last,
        contentType: MediaType(ms[0], ms.length > 1 ? ms[1] : 'octet-stream'),
      ),
    };
    if (caption != null && caption.trim().isNotEmpty) {
      fields['caption'] = caption.trim();
    }
    if (audience != 'everyone') fields['audience'] = audience;
    if (stickers.isNotEmpty) {
      // Multipart can't carry an array, so the list travels as one JSON
      // string (`post/story_stickers.py::parse_stickers_payload`). Position in
      // the list is the stacking order.
      fields['stickers'] = jsonEncode([for (var i = 0; i < stickers.length; i++) stickers[i].toJson(i)]);
    }

    try {
      final res = await dio.Dio().post(
        "$_base/create/",
        data: dio.FormData.fromMap(fields),
        options: dio.Options(headers: {"Authorization": "Bearer $token"}),
        onSendProgress: (sent, total) {
          if (total > 0 && onProgress != null) onProgress((sent / total) * 100);
        },
      );
      final body = res.data;
      final decoded = body is String ? jsonDecode(body) : body;
      return StoryModel.fromJson(decoded as Map<String, dynamic>);
    } on dio.DioException catch (e) {
      final body = e.response?.data;
      throw Exception(_errorText(body) ??
          (body != null && body.toString().isNotEmpty
              ? body.toString()
              : 'Failed to upload story: ${e.response?.statusCode ?? e.message}'));
    }
  }

  /// Marks a story viewed. Deduped server-side (`StoryView` unique_together
  /// on story+user) — safe to call every time the viewer screen shows a
  /// story, no need to track "already called" client-side.
  static Future<int> markViewed(String storyId) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final res = await http.post(
      Uri.parse("$_base/$storyId/view/"),
      headers: {"Authorization": "Bearer $token"},
    );
    if (res.statusCode == 200) {
      final decoded = jsonDecode(res.body);
      return decoded['views_count'] ?? 0;
    } else if (res.statusCode == 404) {
      // Story expired between load and view — not an error the viewer
      // screen needs to surface, it just won't bump the count.
      return 0;
    } else {
      throw Exception('Failed to mark story viewed: ${res.statusCode}');
    }
  }

  // ✅ CONFIRMED (story reactions/replies task) — `GET
  // /post/stories/<id>/viewers/` now exists (`StoryViewersAPIView`,
  // post/views.py) and matches the shape this method already assumed:
  // owner-only, each row is `{"user": {...}, "viewed_at": ..., "reaction":
  // {"emoji": ..., "created_at": ...} | null}` — `StoryViewerEntry.fromJson`
  // (story_model.dart) already reads the `reaction` key.
  static Future<List<StoryViewerEntry>> getStoryViewers(String storyId) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final res = await http
        .get(Uri.parse("$_base/$storyId/viewers/"), headers: {"Authorization": "Bearer $token"})
        .timeout(kApiTimeout);
    if (res.statusCode == 200) {
      final decoded = jsonDecode(res.body);
      final List raw = decoded is Map ? (decoded['results'] as List? ?? []) : decoded as List;
      return raw
          .whereType<Map>()
          .map((e) => StoryViewerEntry.fromJson(Map<String, dynamic>.from(e)))
          .toList();
    } else {
      throw Exception('Failed to load story viewers: ${res.statusCode}');
    }
  }

  /// Toggle a quick reaction (Instagram-style emoji tap) on a story.
  /// `POST /post/stories/<id>/react/` — tapping the same emoji again
  /// removes it, a different emoji replaces it (server enforces one
  /// reaction per (story, user)). Returns whether the story now has a
  /// reaction from the caller (`false` = it was just removed).
  static Future<bool> reactToStory(String storyId, String emoji) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final res = await http.post(
      Uri.parse("$_base/$storyId/react/"),
      headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
      body: jsonEncode({"emoji": emoji}),
    );
    if (res.statusCode == 200) {
      final decoded = jsonDecode(res.body);
      return decoded['reacted'] == true;
    } else if (res.statusCode == 404) {
      return false; // story expired mid-tap — nothing to react to anymore
    } else {
      throw Exception('Failed to react to story: ${res.statusCode}');
    }
  }

  /// Reply to a story — delivered as a normal DM, not a separate reply
  /// system. `POST /post/stories/<id>/reply/` internally reuses the
  /// message app's send-message pipeline (`message.services.
  /// create_message_and_broadcast`) so it shows up instantly in the
  /// recipient's chat via the SAME websocket the message app already uses
  /// (`chat_socket_service.dart`/`inbox_socket_service.dart`) — nothing
  /// extra to wire up here beyond this REST call, which is only the
  /// fallback path (same REST-fallback-alongside-websocket pattern as
  /// every other message send in this app).
  static Future<void> replyToStory(String storyId, String text) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final res = await http.post(
      Uri.parse("$_base/$storyId/reply/"),
      headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
      body: jsonEncode({"text": text}),
    );
    if (res.statusCode != 201) {
      String detail = 'Failed to send reply: ${res.statusCode}';
      try {
        final decoded = jsonDecode(res.body);
        if (decoded is Map && decoded['detail'] != null) detail = decoded['detail'].toString();
      } catch (_) {}
      throw Exception(detail);
    }
  }

  // ─────────────────────────────────────────────────────────────────────
  // Stories upgrade, Part 2 — stickers / mentions.
  //   GET  /post/stories/<id>/                              one active story (what a story_mention notification opens)
  //   GET  /post/stories/mention-candidates/?q=&audience=   people to @mention
  //   POST /post/stories/<id>/stickers/<sid>/vote/          {"option": 0}   -> {"sticker": {...}}
  //   POST /post/stories/<id>/stickers/<sid>/answer/        {"text": "..."} -> {"sticker": {...}}
  //   GET  /post/stories/<id>/stickers/<sid>/responses/     owner only: who voted what / answers
  // ─────────────────────────────────────────────────────────────────────

  /// Pulls the human message out of a DRF error body: `{"errors": {"stickers":
  /// ["Sticker 2: ..."]}}`, `{"detail": "..."}` or `{"stickers": [...]}`.
  static String? _errorText(dynamic body) {
    try {
      final decoded = body is String ? jsonDecode(body) : body;
      if (decoded is! Map) return null;
      dynamic pick(Map m) {
        if (m['detail'] != null) return m['detail'];
        for (final v in m.values) {
          if (v is List && v.isNotEmpty) return v.first;
          if (v is Map) {
            final inner = pick(v);
            if (inner != null) return inner;
          }
          if (v is String && v.isNotEmpty) return v;
        }
        return null;
      }

      final text = pick(decoded);
      return text?.toString();
    } catch (_) {
      return null;
    }
  }

  static Future<Map<String, String>> _jsonHeaders() async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    return {"Authorization": "Bearer $token", "Content-Type": "application/json"};
  }

  /// One active story with its stickers. Throws when it is gone (expired,
  /// deleted, blocked, or a Close Friends story the caller may not see — the
  /// server answers 404 for all of them on purpose).
  static Future<StoryModel> getStory(String storyId) async {
    final res = await http.get(Uri.parse("$_base/$storyId/"), headers: await _jsonHeaders()).timeout(kApiTimeout);
    if (res.statusCode == 200) {
      return StoryModel.fromJson(Map<String, dynamic>.from(jsonDecode(res.body) as Map));
    }
    throw StoryUnavailableException(res.statusCode);
  }

  static Future<List<StoryMentionCandidate>> getMentionCandidates({String q = '', String audience = 'everyone'}) async {
    final uri = Uri.parse("$_base/mention-candidates/").replace(queryParameters: {
      if (q.trim().isNotEmpty) 'q': q.trim(),
      'audience': audience,
    });
    final res = await http.get(uri, headers: await _jsonHeaders()).timeout(kApiTimeout);
    if (res.statusCode != 200) throw Exception('Failed to load people: ${res.statusCode}');
    final decoded = jsonDecode(res.body);
    final List raw = decoded is Map ? (decoded['results'] as List? ?? []) : decoded as List;
    return raw.whereType<Map>().map((e) => StoryMentionCandidate.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  /// Votes on a poll. On success (or "you already voted" — 409) the sticker's
  /// state is refreshed in place and returned, so the caller just rebuilds.
  static Future<StorySticker> votePoll(String storyId, StorySticker sticker, int option) async {
    final res = await http.post(
      Uri.parse("$_base/$storyId/stickers/${sticker.id}/vote/"),
      headers: await _jsonHeaders(),
      body: jsonEncode({"option": option}),
    );
    return _applyResponse(res, sticker, 'Could not submit your vote');
  }

  static Future<StorySticker> answerQuestion(String storyId, StorySticker sticker, String text) async {
    final res = await http.post(
      Uri.parse("$_base/$storyId/stickers/${sticker.id}/answer/"),
      headers: await _jsonHeaders(),
      body: jsonEncode({"text": text}),
    );
    return _applyResponse(res, sticker, 'Could not send your answer');
  }

  static StorySticker _applyResponse(http.Response res, StorySticker sticker, String fallback) {
    Map<String, dynamic>? body;
    try {
      final d = jsonDecode(res.body);
      if (d is Map) body = Map<String, dynamic>.from(d);
    } catch (_) {}
    final fresh = body?['sticker'];
    if ((res.statusCode == 201 || res.statusCode == 409) && fresh is Map) {
      sticker.applyServerJson(Map<String, dynamic>.from(fresh));
      if (res.statusCode == 409) {
        // Already responded earlier (e.g. a retry after a lost response) —
        // state is now up to date, but tell the caller it wasn't a new one.
        throw StickerAlreadyRespondedException(body?['detail']?.toString() ?? fallback);
      }
      return sticker;
    }
    throw Exception(_errorText(body) ?? '$fallback (${res.statusCode})');
  }

  /// Owner only. `page` is 1-based (StandardPagination).
  static Future<StickerResponses> getStickerResponses(String storyId, String stickerId, {int page = 1}) async {
    final uri = Uri.parse("$_base/$storyId/stickers/$stickerId/responses/").replace(queryParameters: {'page': '$page'});
    final res = await http.get(uri, headers: await _jsonHeaders()).timeout(kApiTimeout);
    if (res.statusCode != 200) throw Exception('Failed to load responses: ${res.statusCode}');
    return StickerResponses.fromJson(Map<String, dynamic>.from(jsonDecode(res.body) as Map));
  }
}

/// `GET /post/stories/<id>/` answered non-200 — the story is gone or hidden.
class StoryUnavailableException implements Exception {
  final int statusCode;
  StoryUnavailableException(this.statusCode);
  @override
  String toString() => 'Story unavailable ($statusCode)';
}

/// 409 from vote / answer: this viewer already responded. The sticker passed
/// in has already been refreshed with the real state.
class StickerAlreadyRespondedException implements Exception {
  final String message;
  StickerAlreadyRespondedException(this.message);
  @override
  String toString() => message;
}
