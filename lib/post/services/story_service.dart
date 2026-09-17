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
import '../models/story_model.dart' show StoryModel, StoryViewerEntry;

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
      throw Exception(body != null && body.toString().isNotEmpty
          ? body.toString()
          : 'Failed to upload story: ${e.response?.statusCode ?? e.message}');
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

  // 🚩 TASK 11 FLAG — backend confirmation needed (same as flagged back to
  // Task 4's backend list). `StoryView` already exists server-side (it's
  // what `markViewed()`/`views_count` above are built on — see that
  // method's own doc comment: "deduped server-side, `StoryView`
  // unique_together on story+user"), but nothing in `post_app.md` §16.2 or
  // `post/views.py` that's been confirmed for this task exposes a
  // per-story listing of *who* those views belong to. This method assumes
  // the obvious REST shape (`GET /post/stories/<id>/viewers/`, same
  // `<base>/<id>/<action>/` pattern `markViewed()` above already uses) —
  // if that route doesn't exist yet, it needs adding to
  // `StoryListAPIView`/`StoryViewSet` before "who viewed my story" in
  // `story_viewer_screen.dart` will actually return data (it'll surface
  // as a normal caught exception — empty/error state — until then, not a
  // crash).
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
}