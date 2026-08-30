import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:crypto/crypto.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/auth_service.dart';
import '../utils/api.dart';
import 'models.dart';

class ApiService {
  // 🔥 NAYA — chunked upload (4GB tak, comment_view.py ke chunked upload jaisa hi)
  // 5MB ka ek chunk. Bade video/file ke liye normal multipart ki jagah isse use karo.
  static const int _chunkSize = 5 * 1024 * 1024;
  // Ek saath max itne chunks parallel me upload honge (speed vs server load ka balance).
  static const int _parallelChunkUploads = 4;
  // SharedPreferences me resume-fingerprint save karne ki key ka prefix.
  static const String _resumePrefsPrefix = 'chunked_upload_resume_';

  Future<Map<String, dynamic>> createPost({
    String? title,
    required String content,
    required String category,
    String? subcategory,
    required String postType,
    String visibility = 'public',
    List<String>? hashtags,
    Map<String, dynamic>? location,
    List<File>? mediaFiles,
    List<String>? mediaTypes,
    List<String>? mediaCaptions, // 🔥 NAYA — per-attachment captions
    List<Map<String, dynamic>>? pollOptions, // 🔥 NAYA — poll post support
    DateTime? scheduledAt, // 🔥 NAYA — new_post.dart isko already pass kar raha tha, param yahan missing tha
  }) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not logged in');
    var uri = Uri.parse('${Api.baseUrl}/post/create/');
    var request = http.MultipartRequest('POST', uri);
    request.headers['Authorization'] = 'Bearer $token';
    if (title!= null && title.isNotEmpty) {
      request.fields['title'] = title;
    }
    request.fields['content'] = content;
    request.fields['category'] = category;
    if (subcategory != null && subcategory.isNotEmpty) {
      request.fields['subcategory'] = subcategory;
    }
    request.fields['post_type'] = postType;
    request.fields['visibility'] = visibility;
    // 🔥 NAYA — scheduled post
    if (scheduledAt != null) {
      request.fields['scheduled_at'] = scheduledAt.toIso8601String();
      request.fields['is_scheduled'] = 'true';
    }
    // 🔥 FIX — pehle loop me `request.fields['hashtags']` baar-baar overwrite ho raha
    // tha, isliye sirf AAKHRI hashtag hi backend tak jaata tha. `location` jaisa hi
    // JSON list bhej rahe hain ab.
    if (hashtags!= null && hashtags.isNotEmpty) {
      request.fields['hashtags'] = jsonEncode(hashtags);
    }
    if (location!= null) {
      request.fields['location'] = jsonEncode(location);
    }
    if (mediaFiles!= null && mediaFiles.isNotEmpty) {
      for (int i = 0; i < mediaFiles.length; i++) {
        var file = mediaFiles[i];
        request.files.add(await http.MultipartFile.fromPath('media_files', file.path));
      }
      // 🔥 FIX — `media_types` bhi isi tarah overwrite ho raha tha, sirf last file
      // ka type save hota tha. Ab poori list JSON ke roop me, files ke saath
      // same order me bhej rahe hain.
      if (mediaTypes != null && mediaTypes.isNotEmpty) {
        request.fields['media_types'] = jsonEncode(mediaTypes);
      }
      // 🔥 NAYA — per-attachment captions, same order as media_files
      if (mediaCaptions != null && mediaCaptions.isNotEmpty) {
        request.fields['media_captions'] = jsonEncode(mediaCaptions);
      }
    }
    // 🔥 NAYA — poll options
    if (pollOptions != null && pollOptions.isNotEmpty) {
      request.fields['poll_options'] = jsonEncode(pollOptions);
    }
    try {
      var streamedResponse = await request.send();
      var response = await http.Response.fromStream(streamedResponse);
      var data = jsonDecode(response.body);
      if (response.statusCode == 201) {
        return data;
      } else {
        throw Exception(data['message']?? 'Failed to create post');
      }
    } catch (e) {
      if (e is Exception) rethrow;
      throw Exception('Network error: $e');
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // CHUNKED UPLOAD — POST (badi files, e.g. video, 4GB tak)
  // ═══════════════════════════════════════════════════════════════

  Future<String> initPostChunkedUpload({
    required File file,
    String? title,
    required String content,
    required String category,
    String? subcategory,
    required String postType,
    String visibility = 'public',
    List<String>? hashtags,
    Map<String, dynamic>? location,
    List<Map<String, dynamic>>? pollOptions,
    String? mediaCaption,
    String? mediaType,
    DateTime? scheduledAt,
  }) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not logged in');
    final totalSize = await file.length();
    final totalChunks = (totalSize / _chunkSize).ceil();

    final uri = Uri.parse('${Api.baseUrl}/post/chunked/init/');
    final response = await http.post(
      uri,
      headers: {
        'Authorization': 'Bearer $token',
        'Content-Type': 'application/json',
      },
      body: jsonEncode({
        'file_name': file.path.split('/').last,
        'total_chunks': totalChunks,
        'total_size': totalSize,
        'title': title ?? '',
        'content': content,
        'category': category,
        'subcategory': subcategory,
        'post_type': postType,
        'visibility': visibility,
        'hashtags': hashtags ?? [],
        'location': location ?? {},
        'poll_options': pollOptions ?? [],
        'media_caption': mediaCaption ?? '',
        'media_type': mediaType,
        'is_scheduled': scheduledAt != null,
        if (scheduledAt != null) 'scheduled_at': scheduledAt.toIso8601String(),
      }),
    );
    final data = jsonDecode(response.body);
    if (response.statusCode == 200) return data['upload_id'];
    throw Exception(data['error'] ?? 'Chunked upload shuru nahi ho paya');
  }

  // NOTE: ye comment ka hi generic chunk endpoint reuse karta hai — usse sirf
  // upload_id + chunk_index chahiye, post ho ya comment fark nahi padta.
  Future<void> uploadPostChunk({
    required String uploadId,
    required int chunkIndex,
    required List<int> chunkBytes,
  }) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not logged in');
    final uri = Uri.parse('${Api.baseUrl}/post/comment/chunked/chunk/');
    var request = http.MultipartRequest('POST', uri);
    request.headers['Authorization'] = 'Bearer $token';
    request.fields['upload_id'] = uploadId;
    request.fields['chunk_index'] = chunkIndex.toString();
    // 🔥 NAYA — is chunk ka MD5 hash bhi bhejte hain, taaki server verify kar
    // sake ki network me chunk corrupt to nahi hua. Mismatch pe server 400
    // deta hai aur is chunk ko received nahi maanta — humara resume logic
    // (upload_id reuse) isse automatically dubara try kar leta hai.
    request.fields['chunk_hash'] = md5.convert(chunkBytes).toString();
    request.files.add(http.MultipartFile.fromBytes('chunk', chunkBytes, filename: 'chunk_$chunkIndex'));

    var streamedResponse = await request.send();
    var response = await http.Response.fromStream(streamedResponse);
    if (response.statusCode != 200) {
      final data = jsonDecode(response.body);
      throw Exception(data['error'] ?? 'Chunk $chunkIndex upload fail hua');
    }
  }

  Future<Map<String, dynamic>> completePostChunkedUpload(String uploadId) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not logged in');
    final uri = Uri.parse('${Api.baseUrl}/post/chunked/complete/');
    final response = await http.post(
      uri,
      headers: {
        'Authorization': 'Bearer $token',
        'Content-Type': 'application/json',
      },
      body: jsonEncode({'upload_id': uploadId}),
    );
    final data = jsonDecode(response.body);
    if (response.statusCode == 201) return data;
    throw Exception(data['message'] ?? data['error'] ?? 'Post complete nahi ho paya');
  }

  // 🔥 NAYA — resume support: batata hai is upload_id ke kaunse chunks already
  // server pe save ho chuke hain. Comment aur post dono flow ke liye generic hai.
  Future<Map<String, dynamic>> getChunkedUploadStatus(String uploadId) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not logged in');
    final uri = Uri.parse('${Api.baseUrl}/post/chunked/status/$uploadId/');
    final response = await http.get(uri, headers: {'Authorization': 'Bearer $token'});
    if (response.statusCode != 200) {
      throw Exception('Upload status nahi mila (shayad expire ho gaya)');
    }
    return jsonDecode(response.body);
  }

  // File ka fingerprint — path + size + last-modified se bana, taaki same file
  // dubara select karne pe hum pehchan sakein ki ye wahi ruka hua upload hai.
  Future<String> _fileFingerprint(File file) async {
    final stat = await file.stat();
    return '${file.path}_${stat.size}_${stat.modified.millisecondsSinceEpoch}';
  }

  /// High-level helper: file ko disk se seekhte hue chunks me padhta hai
  /// (poori file ek saath RAM me load nahi karta — 4GB file ke liye zaroori),
  /// chunks ko **parallel** (max `_parallelChunkUploads` ek saath) upload
  /// karta hai speed ke liye, phir complete call karke Post banata hai.
  ///
  /// 🔥 NAYA — RESUME SUPPORT: agar pehle isi file ka upload beech me
  /// ruk gaya tha (app band ho gayi, network gaya), to ye automatically
  /// wahi upload_id reuse karega aur sirf **missing chunks** dubara bhejega,
  /// poori file dubara se nahi.
  ///
  /// `onProgress` 0.0 -> 1.0 ke beech progress deta hai.
  Future<Map<String, dynamic>> createPostWithChunkedUpload({
    required File file,
    String? title,
    required String content,
    required String category,
    String? subcategory,
    required String postType,
    String visibility = 'public',
    List<String>? hashtags,
    Map<String, dynamic>? location,
    List<Map<String, dynamic>>? pollOptions,
    String? mediaCaption,
    String? mediaType,
    DateTime? scheduledAt,
    void Function(double progress)? onProgress,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    final fingerprint = await _fileFingerprint(file);
    final prefsKey = '$_resumePrefsPrefix$fingerprint';

    final totalSize = await file.length();
    final totalChunks = (totalSize / _chunkSize).ceil();

    String? uploadId = prefs.getString(prefsKey);
    Set<int> alreadyReceived = {};

    if (uploadId != null) {
      // Purana ruka hua upload mila — check karo ki server pe abhi bhi valid hai
      try {
        final statusData = await getChunkedUploadStatus(uploadId);
        if (statusData['is_completed'] == true) {
          // Pehle hi complete ho chuka tha par local state clear nahi hui —
          // fresh upload shuru karo taaki duplicate post na bane.
          uploadId = null;
        } else {
          alreadyReceived = Set<int>.from((statusData['received_chunks'] as List).map((e) => e as int));
        }
      } catch (_) {
        // upload_id ab valid nahi (expire/cleanup ho gaya) — fresh shuru karo
        uploadId = null;
      }
    }

    if (uploadId == null) {
      uploadId = await initPostChunkedUpload(
        file: file,
        title: title,
        content: content,
        category: category,
        subcategory: subcategory,
        postType: postType,
        visibility: visibility,
        hashtags: hashtags,
        location: location,
        pollOptions: pollOptions,
        mediaCaption: mediaCaption,
        mediaType: mediaType,
        scheduledAt: scheduledAt,
      );
      alreadyReceived = {};
      // Turant save karo — taaki agar chunking ke beech me app crash ho jaye,
      // agli baar isi upload_id se resume ho sake.
      await prefs.setString(prefsKey, uploadId);
    }

    final pending = List<int>.generate(totalChunks, (i) => i).where((i) => !alreadyReceived.contains(i)).toList();

    int completedCount = alreadyReceived.length;
    void reportProgress() => onProgress?.call(completedCount / totalChunks);
    reportProgress();

    final raf = await file.open(mode: FileMode.read);
    try {
      // Parallel batches me upload karo (sequential ki jagah) — bade file
      // ke liye kaafi tez hota hai. Ek chunk fail ho (checksum mismatch ya
      // transient network issue) to usi chunk ko 2 baar tak retry karte hain
      // pehle poore upload ko fail maanne se — sirf X baar fail hone pe
      // exception upar jaati hai (jahan resume state safe rehti hai, agli
      // baar isi jagah se dubara try hoga).
      const maxRetriesPerChunk = 2;
      for (int batchStart = 0; batchStart < pending.length; batchStart += _parallelChunkUploads) {
        final batch = pending.skip(batchStart).take(_parallelChunkUploads).toList();
        await Future.wait(batch.map((chunkIndex) async {
          final start = chunkIndex * _chunkSize;
          final size = (start + _chunkSize < totalSize) ? _chunkSize : totalSize - start;
          await raf.setPosition(start);
          final chunkBytes = await raf.read(size);

          Object? lastError;
          for (int attempt = 0; attempt <= maxRetriesPerChunk; attempt++) {
            try {
              await uploadPostChunk(uploadId: uploadId!, chunkIndex: chunkIndex, chunkBytes: chunkBytes);
              lastError = null;
              break;
            } catch (e) {
              lastError = e;
              if (attempt < maxRetriesPerChunk) {
                await Future.delayed(Duration(milliseconds: 400 * (attempt + 1)));
              }
            }
          }
          if (lastError != null) throw lastError;

          completedCount++;
          reportProgress();
        }));
      }
    } finally {
      await raf.close();
    }

    final result = await completePostChunkedUpload(uploadId);
    // Upload poora ho gaya — resume state ab zaroorat nahi, clear kar do.
    await prefs.remove(prefsKey);
    return result;
  }

  // Category + subcategory tree, taaki dropdown backend ke actual
  // CATEGORY_CHOICES / SUBCATEGORY_CHOICES / CATEGORY_SUBCATEGORY_MAP se match kare.
  Future<Map<String, dynamic>> getCategoryTaxonomy() async {
    try {
      final url = Uri.parse('${Api.baseUrl}/post/categories/');
      final response = await http.get(url, headers: {'Content-Type': 'application/json'});
      final data = jsonDecode(response.body);
      if (response.statusCode == 200 && data['success'] == true) {
        return data['data'];
      } else {
        throw Exception(data['message'] ?? 'Failed to load categories');
      }
    } catch (e) {
      throw Exception('Error: $e');
    }
  }

  // 🔥 NAYA — Freesound music search (media editor "Add Music" feature).
  // Backend `/post/music/search/` sirf CC0-licensed (copyright-free)
  // results deta hai — key kabhi bhi app me nahi aati, backend proxy
  // karta hai. Har result: {id, name, artist, duration, preview_url,
  // license: 'CC0', tags}.
  Future<List<Map<String, dynamic>>> searchFreesoundMusic(String query, {int page = 1}) async {
    try {
      final token = await AuthService.getToken();
      final url = Uri.parse('${Api.baseUrl}/post/music/search/')
          .replace(queryParameters: {'q': query, 'page': '$page'});
      final response = await http.get(
        url,
        headers: {
          'Content-Type': 'application/json',
          if (token != null) 'Authorization': 'Bearer $token',
        },
      );
      final data = jsonDecode(response.body);
      if (response.statusCode == 200 && data['success'] == true) {
        return List<Map<String, dynamic>>.from(data['data']['results'] ?? []);
      } else {
        throw Exception(data['message'] ?? 'Music search failed');
      }
    } catch (e) {
      throw Exception('Error: $e');
    }
  }

  // --- YE NAYA METHOD ADD HUA HAI, ISKI WAJAH SE ERROR AA RAHA THA ---
  Future<Map<String, dynamic>> getPostById(String postId) async {
    try {
      final token = await AuthService.getToken();
      final url = Uri.parse("${Api.baseUrl}/post/details/$postId/");

      final response = await http.get(
        url,
        headers: {
          'Content-Type': 'application/json',
          if (token!= null) 'Authorization': 'Bearer $token',
        },
      );

      final data = jsonDecode(response.body);
      if (response.statusCode == 200 && data['success'] == true) {
        return data['data'];
      } else {
        throw Exception(data['message']?? 'Post not found');
      }
    } catch (e) {
      throw Exception('Error: $e');
    }
  }
}