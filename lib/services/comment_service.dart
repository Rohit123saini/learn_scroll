
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:mime/mime.dart';
import '../utils/api.dart';
import 'auth_service.dart';
import 'home_api_model_service.dart';

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
  });

  factory CommentModel.fromJson(Map<String, dynamic> j) {
    return CommentModel(
      id: j['id'].toString(),
      post: j['post'].toString(),
      user: UserModel.fromJson(j['user'] ?? {}),
      parent: j['parent']?.toString(),
      content: j['content'] ?? '',
      media: (j['media'] as List? ?? [])
          .map((e) => CommentMediaModel.fromJson(e))
          .toList(),
      likesCount: j['likesCount'] ?? j['likes_count'] ?? 0,
      repliesCount: j['repliesCount'] ?? j['replies_count'] ?? 0,
      isEdited: j['is_edited'] ?? false,
      isPinned: j['is_pinned'] ?? false,
      isHidden: j['is_hidden'] ?? false,
      createdAt: DateTime.tryParse(j['created_at'] ?? '') ?? DateTime.now(),
    );
  }
}

class CommentService {
  static String base = "${Api.baseUrl}/post/comment";

  static Future<List<CommentModel>> getComments(String postId) async {
    final token = await AuthService.getToken();
    final res = await http.get(
      Uri.parse("$base/post/$postId/"),
      headers: {"Authorization": "Bearer $token"},
    );
    if (res.statusCode == 200) {
      final List data = jsonDecode(res.body);
      return data.map((e) => CommentModel.fromJson(e)).toList();
    } else {
      throw Exception(res.body);
    }
  }

  static Future<List<CommentModel>> getReplies(String commentId) async {
    final token = await AuthService.getToken();
    final res = await http.get(
      Uri.parse("$base/$commentId/replies/"),
      headers: {"Authorization": "Bearer $token"},
    );
    if (res.statusCode == 200) {
      final List data = jsonDecode(res.body);
      return data.map((e) => CommentModel.fromJson(e)).toList();
    } else {
      throw Exception(res.body);
    }
  }

  // ================= MAIN FUNCTION - 4GB SUPPORT =================
  static Future<CommentModel> createComment({
    required String postId,
    String? parentId,
    required String content,
    List<File>? files,
    Function(double percent)? onProgress,
  }) async {
    if (files != null && files.isNotEmpty) {
      File file = files.first;
      int size = await file.length();
      // 20MB se bada hai to chunked system use karo (4GB tak)
      if (size > 20 * 1024 * 1024) {
        return await _createCommentChunked(
          postId: postId,
          parentId: parentId,
          content: content,
          file: file,
          onProgress: onProgress,
        );
      }
    }
    // Chota file - normal upload
    return await _createCommentNormal(
      postId: postId,
      parentId: parentId,
      content: content,
      files: files,
    );
  }

  // Normal upload - small files
  static Future<CommentModel> _createCommentNormal({
    required String postId,
    String? parentId,
    required String content,
    List<File>? files,
  }) async {
    final token = await AuthService.getToken();
    var req = http.MultipartRequest('POST', Uri.parse("$base/create/"));
    req.headers['Authorization'] = "Bearer $token";

    if (parentId != null && parentId.isNotEmpty) {
      req.fields['parent_id'] = parentId;
    } else {
      req.fields['post_id'] = postId;
    }

    req.fields['content'] = content;

    if (files != null && files.isNotEmpty) {
      for (var f in files) {
        if (!await f.exists()) continue;
        final mimeStr = lookupMimeType(f.path) ?? 'application/octet-stream';
        final mimeSplit = mimeStr.split('/');
        req.files.add(
          await http.MultipartFile.fromPath(
            'files',
            f.path,
            contentType: MediaType(mimeSplit[0], mimeSplit[1]),
          ),
        );
      }
    }

    final streamed = await req.send();
    final res = await http.Response.fromStream(streamed);

    if (res.statusCode == 201) {
      return CommentModel.fromJson(jsonDecode(res.body));
    } else {
      throw Exception(res.body);
    }
  }

  // ================= 4GB CHUNKED UPLOAD - PRODUCTION LEVEL =================
  static Future<CommentModel> _createCommentChunked({
    required String postId,
    String? parentId,
    required String content,
    required File file,
    Function(double percent)? onProgress,
  }) async {
    final token = await AuthService.getToken();
    int fileSize = await file.length();
    int chunkSize = 5 * 1024 * 1024; // 5MB per chunk
    int totalChunks = (fileSize / chunkSize).ceil();
    String fileName = file.path.split('/').last;

    print("CHUNKED UPLOAD START: $fileName - ${(fileSize / (1024 * 1024)).toStringAsFixed(2)} GB - $totalChunks chunks");

    // Step 1: Init
    var initRes = await http.post(
      Uri.parse("$base/chunked/init/"),
      headers: {
        "Authorization": "Bearer $token",
        "Content-Type": "application/json",
      },
      body: jsonEncode({
        "file_name": fileName,
        "total_chunks": totalChunks,
        "total_size": fileSize,
        "post_id": postId,
        "parent_id": parentId,
        "content": content,
      }),
    );

    if (initRes.statusCode != 200) {
      throw Exception("Init failed: ${initRes.body}");
    }

    String uploadId = jsonDecode(initRes.body)['upload_id'];
    print("UPLOAD ID: $uploadId");

    // Step 2: Send Chunks one by one
    RandomAccessFile raf = await file.open(mode: FileMode.read);
    for (int i = 0; i < totalChunks; i++) {
      int start = i * chunkSize;
      int end = (start + chunkSize > fileSize) ? fileSize : start + chunkSize;
      await raf.setPosition(start);
      Uint8List chunkBytes = await raf.read(end - start);

      var chunkReq = http.MultipartRequest(
        'POST',
        Uri.parse("$base/chunked/chunk/"),
      );
      chunkReq.headers['Authorization'] = "Bearer $token";
      chunkReq.fields['upload_id'] = uploadId;
      chunkReq.fields['chunk_index'] = i.toString();
      chunkReq.files.add(
        http.MultipartFile.fromBytes(
          'chunk',
          chunkBytes,
          filename: 'chunk_$i',
        ),
      );

      var chunkStreamed = await chunkReq.send();
      var chunkRes = await http.Response.fromStream(chunkStreamed);

      if (chunkRes.statusCode != 200) {
        await raf.close();
        throw Exception("Chunk $i failed: ${chunkRes.body}");
      }

      double percent = ((i + 1) / totalChunks) * 100;
      print("Uploaded chunk $i/$totalChunks - ${percent.toStringAsFixed(1)}%");
      if (onProgress != null) onProgress(percent);
    }
    await raf.close();

    // Step 3: Complete and merge
    var completeRes = await http.post(
      Uri.parse("$base/chunked/complete/"),
      headers: {
        "Authorization": "Bearer $token",
        "Content-Type": "application/json",
      },
      body: jsonEncode({"upload_id": uploadId}),
    );

    print("COMPLETE STATUS: ${completeRes.statusCode}");

    if (completeRes.statusCode == 201) {
      return CommentModel.fromJson(jsonDecode(completeRes.body));
    } else {
      throw Exception("Complete failed: ${completeRes.body}");
    }
  }

  static Future<void> deleteComment(String commentId) async {
    final token = await AuthService.getToken();
    final res = await http.delete(
      Uri.parse("$base/$commentId/delete/"),
      headers: {"Authorization": "Bearer $token"},
    );
    if (res.statusCode != 200) throw Exception(res.body);
  }

  static Future<void> hideComment(String commentId) async {
    final token = await AuthService.getToken();
    final res = await http.post(
      Uri.parse("$base/$commentId/hide/"),
      headers: {"Authorization": "Bearer $token"},
    );
    if (res.statusCode != 200) throw Exception(res.body);
  }
}