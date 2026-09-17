// post/comment_service.dart (client) — Flutter side comment service.
//
// TASK — cleanup + correctness pass:
//   • Removed the ~370-line commented-out duplicate of this whole file that
//     used to sit above the real class (dead code, made it unclear which
//     version was "current").
//   • Every call now uses AuthService.getValidToken() instead of
//     AuthService.getToken() — matches the Task 8.4 fix already applied in
//     home_api_model_service.dart. getToken() returns whatever is on disk
//     even if it's expired, so an expired access token silently 401s and
//     (if the refresh token is also dead) AuthService.onForceLogout() never
//     fires, since that path only runs through getValidToken(). Every
//     comment action (load/post/react/edit/delete/hide) now goes through
//     the same expiry-aware + force-logout path as the rest of the app.
//   • Every http call now has .timeout(kApiTimeout) (Task 11.3, same
//     constant home_api_model_service.dart defines) — previously a hung
//     server meant an infinite spinner on comments specifically.
//   • TASK 2 FIX — `_createCommentNormal` (text-only comments, and any
//     comment with files under the 20MB chunked-upload threshold — i.e.
//     the vast majority of real comments: a message, a photo, a short
//     voice note) silently NEVER called the `onProgress` callback at all.
//     `package:http`'s `MultipartRequest.send()` has no upload-progress
//     hook, so the UI's progress bar (comment_sheet.dart) sat at 0% /
//     "Compressing..." for the entire upload no matter how long it took —
//     only the rare >20MB chunked path ever moved the bar, one chunk at a
//     time. Rewrote just this one method to send via `package:dio`
//     instead (already a project dependency, used elsewhere for
//     downloads), whose `onSendProgress` gives real byte-level progress.
//     Public API (`createComment(...)`) and every other method are
//     unchanged — same params, same return type, same error shape
//     (`Exception` with the server's error body as the message).

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import 'package:dio/dio.dart' as dio;
import 'package:http_parser/http_parser.dart';
import 'package:mime/mime.dart';
import '../utils/api.dart';
import 'auth_service.dart';
import 'home_api_model_service.dart'; // kApiTimeout

// ================= MODELS =================

class CommentMediaModel {
  final String id;
  final String mediaType;
  final String file;
  final String fileName;
  final int fileSize;
  final String? mimeType;

  CommentMediaModel({
    required this.id,
    required this.mediaType,
    required this.file,
    required this.fileName,
    required this.fileSize,
    this.mimeType,
  });

  factory CommentMediaModel.fromJson(Map<String, dynamic> j) {
    return CommentMediaModel(
      id: j['id'].toString(),
      mediaType: j['mediaType'] ?? j['media_type'] ?? 'other',
      file: j['file'] ?? '',
      fileName: j['fileName'] ?? j['file_name'] ?? 'file',
      fileSize: j['fileSize'] ?? j['file_size'] ?? 0,
      mimeType: j['mimeType'] ?? j['mime_type'],
    );
  }
}

class CommentModel {
  final String id;
  final String post;
  final UserModel user;
  final String? parent;
  String content;
  final List<CommentMediaModel> media;
  int likesCount;
  int repliesCount;
  bool isEdited;
  bool isPinned;
  bool isHidden;
  final DateTime createdAt;
  String? myReaction;
  Map<String, int> reactionCounts;

  CommentModel({
    required this.id,
    required this.post,
    required this.user,
    this.parent,
    required this.content,
    required this.media,
    required this.likesCount,
    required this.repliesCount,
    required this.isEdited,
    required this.isPinned,
    required this.isHidden,
    required this.createdAt,
    this.myReaction,
    this.reactionCounts = const {},
  });

  factory CommentModel.fromJson(Map<String, dynamic> j) {
    return CommentModel(
      id: j['id'].toString(),
      post: j['post'].toString(),
      user: UserModel.fromJson(j['user'] ?? {}),
      parent: j['parent']?.toString(),
      content: j['content'] ?? '',
      media: (j['media'] as List? ?? []).map((e) => CommentMediaModel.fromJson(e)).toList(),
      likesCount: j['likesCount'] ?? j['likes_count'] ?? (j['reaction_counts']?['total'] ?? 0),
      repliesCount: j['repliesCount'] ?? j['replies_count'] ?? 0,
      isEdited: j['is_edited'] ?? false,
      isPinned: j['is_pinned'] ?? false,
      isHidden: j['is_hidden'] ?? false,
      createdAt: DateTime.tryParse(j['created_at'] ?? '') ?? DateTime.now(),
      myReaction: j['my_reaction'] ?? j['myReaction'],
      reactionCounts: (j['reaction_counts'] != null || j['reactionCounts'] != null)
          ? Map<String, int>.from(
              ((j['reaction_counts'] ?? j['reactionCounts']) as Map).map(
                (k, v) => MapEntry(k.toString(), (v as int?) ?? 0),
              ),
            )
          : {},
    );
  }
}

// ================= SERVICE =================

class CommentService {
  static String base = "${Api.baseUrl}/post/comment";

  static Future<String> _requireToken() async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    return token;
  }

  static Future<List<CommentModel>> getComments(String postId) async {
    final token = await _requireToken();
    final res = await http
        .get(
          Uri.parse("$base/post/$postId/"),
          headers: {"Authorization": "Bearer $token"},
        )
        .timeout(kApiTimeout);
    if (res.statusCode == 200) {
      final List data = jsonDecode(res.body);
      return data.map((e) => CommentModel.fromJson(e)).toList();
    } else {
      throw Exception(res.body);
    }
  }

  static Future<List<CommentModel>> getReplies(String commentId) async {
    final token = await _requireToken();
    final res = await http
        .get(
          Uri.parse("$base/$commentId/replies/"),
          headers: {"Authorization": "Bearer $token"},
        )
        .timeout(kApiTimeout);
    if (res.statusCode == 200) {
      final List data = jsonDecode(res.body);
      return data.map((e) => CommentModel.fromJson(e)).toList();
    } else {
      throw Exception(res.body);
    }
  }

  static Future<Map<String, dynamic>> toggleCommentReaction(
      String commentId, String reaction) async {
    final token = await _requireToken();
    final res = await http
        .post(
          Uri.parse("$base/$commentId/react/"),
          headers: {
            "Authorization": "Bearer $token",
            "Content-Type": "application/json"
          },
          body: jsonEncode({"reaction": reaction}),
        )
        .timeout(kApiTimeout);
    if (res.statusCode == 200) return jsonDecode(res.body);
    else throw Exception(res.body);
  }

  static Future<CommentModel> editComment({
    required String commentId,
    required String content,
  }) async {
    final token = await _requireToken();
    final res = await http
        .patch(
          Uri.parse("$base/$commentId/update/"),
          headers: {
            "Authorization": "Bearer $token",
            "Content-Type": "application/json"
          },
          body: jsonEncode({"content": content}),
        )
        .timeout(kApiTimeout);
    if (res.statusCode == 200) {
      return CommentModel.fromJson(jsonDecode(res.body));
    } else {
      throw Exception(res.body);
    }
  }

  static Future<CommentModel> createComment({
    required String postId,
    String? parentId,
    required String content,
    List<File>? files,
    Function(double percent)? onProgress,
  }) async {
    if (files != null && files.isNotEmpty) {
      int size = await files.first.length();
      if (size > 200 * 1024 * 1024) {
        throw Exception("File too large: 200MB limit");
      }
      if (size > 20 * 1024 * 1024) {
        return await _createCommentChunked(
            postId: postId,
            parentId: parentId,
            content: content,
            file: files.first,
            onProgress: onProgress);
      }
    }
    return await _createCommentNormal(
        postId: postId, parentId: parentId, content: content, files: files, onProgress: onProgress);
  }

  static Future<CommentModel> _createCommentNormal({
    required String postId,
    String? parentId,
    required String content,
    List<File>? files,
    Function(double percent)? onProgress,
  }) async {
    final token = await _requireToken();
    final Map<String, dynamic> fields = {"content": content};
    if (parentId != null && parentId.isNotEmpty) {
      fields['parent_id'] = parentId;
    } else {
      fields['post_id'] = postId;
    }
    if (files != null) {
      final List<dio.MultipartFile> parts = [];
      for (var f in files) {
        if (!await f.exists()) continue;
        final mimeStr = lookupMimeType(f.path) ?? 'application/octet-stream';
        final ms = mimeStr.split('/');
        parts.add(await dio.MultipartFile.fromFile(f.path,
            filename: f.path.split('/').last, contentType: MediaType(ms[0], ms[1])));
      }
      if (parts.isNotEmpty) fields['files'] = parts;
    }
    try {
      final res = await dio.Dio().post(
        "$base/create/",
        data: dio.FormData.fromMap(fields),
        options: dio.Options(
          headers: {"Authorization": "Bearer $token"},
          sendTimeout: kApiTimeout,
          receiveTimeout: kApiTimeout,
        ),
        onSendProgress: (sent, total) {
          if (total > 0 && onProgress != null) onProgress((sent / total) * 100);
        },
      );
      final body = res.data;
      final decoded = body is String ? jsonDecode(body) : body;
      return CommentModel.fromJson(decoded as Map<String, dynamic>);
    } on dio.DioException catch (e) {
      throw Exception(e.response?.data ?? e.message ?? 'Upload failed');
    }
  }

  static Future<CommentModel> _createCommentChunked({
    required String postId,
    String? parentId,
    required String content,
    required File file,
    Function(double percent)? onProgress,
  }) async {
    final token = await _requireToken();
    int fileSize = await file.length();
    int chunkSize = 5 * 1024 * 1024;
    int totalChunks = (fileSize / chunkSize).ceil();
    String fileName = file.path.split('/').last;

    Map<String, dynamic> initPayload = {
      "file_name": fileName,
      "total_chunks": totalChunks,
      "total_size": fileSize,
      "content": content,
    };
    if (parentId != null && parentId.isNotEmpty) {
      initPayload["parent_id"] = parentId;
    } else {
      initPayload["post_id"] = postId;
    }

    var initRes = await http
        .post(
          Uri.parse("$base/chunked/init/"),
          headers: {
            "Authorization": "Bearer $token",
            "Content-Type": "application/json"
          },
          body: jsonEncode(initPayload),
        )
        .timeout(kApiTimeout);

    if (initRes.statusCode != 200 && initRes.statusCode != 201) {
      throw Exception("Init failed: ${initRes.body}");
    }

    String uploadId =
        (jsonDecode(initRes.body)['upload_id'] ?? jsonDecode(initRes.body)['id'])
            .toString();

    RandomAccessFile raf = await file.open(mode: FileMode.read);
    for (int i = 0; i < totalChunks; i++) {
      int start = i * chunkSize;
      int end = (start + chunkSize > fileSize) ? fileSize : start + chunkSize;
      await raf.setPosition(start);
      Uint8List chunkBytes = await raf.read(end - start);

      var chunkReq =
          http.MultipartRequest('POST', Uri.parse("$base/chunked/chunk/"));
      chunkReq.headers['Authorization'] = "Bearer $token";
      chunkReq.fields['upload_id'] = uploadId;
      chunkReq.fields['chunk_index'] = i.toString();
      chunkReq.files.add(http.MultipartFile.fromBytes('chunk', chunkBytes,
          filename: 'chunk_$i'));

      var chunkStreamed = await chunkReq.send().timeout(kApiTimeout);
      var chunkRes = await http.Response.fromStream(chunkStreamed);

      if (chunkRes.statusCode != 200 && chunkRes.statusCode != 201) {
        await raf.close();
        throw Exception("Chunk $i failed: ${chunkRes.body}");
      }

      if (onProgress != null) onProgress(((i + 1) / totalChunks) * 100);
    }
    await raf.close();

    var completeRes = await http
        .post(
          Uri.parse("$base/chunked/complete/"),
          headers: {
            "Authorization": "Bearer $token",
            "Content-Type": "application/json"
          },
          body: jsonEncode({"upload_id": uploadId}),
        )
        .timeout(kApiTimeout);

    if (completeRes.statusCode == 201 || completeRes.statusCode == 200) {
      return CommentModel.fromJson(jsonDecode(completeRes.body));
    } else {
      throw Exception("Complete failed: ${completeRes.body}");
    }
  }

  static Future<void> deleteComment(String commentId) async {
    final token = await _requireToken();
    final res = await http
        .delete(
          Uri.parse("$base/$commentId/delete/"),
          headers: {"Authorization": "Bearer $token"},
        )
        .timeout(kApiTimeout);
    if (res.statusCode != 200 && res.statusCode != 204) throw Exception(res.body);
  }

  static Future<void> hideComment(String commentId) async {
    final token = await _requireToken();
    final res = await http
        .post(
          Uri.parse("$base/$commentId/hide/"),
          headers: {"Authorization": "Bearer $token"},
        )
        .timeout(kApiTimeout);
    if (res.statusCode != 200) throw Exception(res.body);
  }
}