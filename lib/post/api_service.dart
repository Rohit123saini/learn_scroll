
import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import '../services/auth_service.dart';
import '../utils/api.dart';
import 'models.dart';

class ApiService {

  Future<Map<String, dynamic>> createPost({
    String? title,
    required String content,
    required String category,
    required String postType,
    String visibility = 'public',
    List<String>? hashtags,
    Map<String, dynamic>? location,
    List<File>? mediaFiles,
    List<String>? mediaTypes,
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
    request.fields['post_type'] = postType;
    request.fields['visibility'] = visibility;
    if (hashtags!= null && hashtags.isNotEmpty) {
      for (int i = 0; i < hashtags.length; i++) {
        request.fields['hashtags'] = hashtags[i];
      }
    }
    if (location!= null) {
      request.fields['location'] = jsonEncode(location);
    }
    if (mediaFiles!= null && mediaFiles.isNotEmpty) {
      for (int i = 0; i < mediaFiles.length; i++) {
        var file = mediaFiles[i];
        var mediaType = mediaTypes?[i]?? 'image';
        request.files.add(await http.MultipartFile.fromPath('media_files', file.path));
        request.fields['media_types'] = mediaType;
      }
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




