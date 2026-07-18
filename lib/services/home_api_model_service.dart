// import 'dart:convert';
// import 'package:http/http.dart' as http;
// import '../utils/api.dart'; // Jaha Api.baseUrl hai
// import 'auth_service.dart'; // Jaha AuthService hai

// // ===================== MODELS =====================

// class UserModel {
//   final String id;
//   final String username;
//   final String? profilePicture;
//   final String? fullName;

//   UserModel({
//     required this.id,
//     required this.username,
//     this.profilePicture,
//     this.fullName,
//   });

//   factory UserModel.fromJson(Map<String, dynamic> json) {
//     return UserModel(
//       id: json['id']?.toString() ?? '',
//       username: json['username'] ?? '',
//       profilePicture: json['profile_picture'],
//       fullName: json['full_name'],
//     );
//   }
// }

// class PostMediaModel {
//   final String id;
//   final String mediaType;
//   final String file;
//   final String? thumbnail;
//   final String fileName;
//   final int? width;
//   final int? height;
//   final int? durationSeconds;

//   PostMediaModel({
//     required this.id,
//     required this.mediaType,
//     required this.file,
//     this.thumbnail,
//     required this.fileName,
//     this.width,
//     this.height,
//     this.durationSeconds,
//   });

//   factory PostMediaModel.fromJson(Map<String, dynamic> json) {
//     return PostMediaModel(
//       id: json['id']?.toString() ?? '',
//       mediaType: json['media_type'] ?? 'image',
//       file: json['file'] ?? '',
//       thumbnail: json['thumbnail'],
//       fileName: json['file_name'] ?? '',
//       width: json['width'],
//       height: json['height'],
//       durationSeconds: json['duration_seconds'],
//     );
//   }
// }

// class PostModel {
//   final String id;
//   final UserModel user;
//   final String? title;
//   final String? content;
//   final String category;
//   final String postType;
//   final String visibility;
//   final List<String> hashtags;
//   final Map<String, dynamic>? location;
//   final int likesCount;
//   final int commentsCount;
//   final int sharesCount;
//   final int viewsCount;
//   final int savesCount;
//   final bool isLiked;
//   final bool isSaved;
//   final DateTime createdAt;
//   final List<PostMediaModel> media;

//   PostModel({
//     required this.id,
//     required this.user,
//     this.title,
//     this.content,
//     required this.category,
//     required this.postType,
//     required this.visibility,
//     required this.hashtags,
//     this.location,
//     required this.likesCount,
//     required this.commentsCount,
//     required this.sharesCount,
//     required this.viewsCount,
//     required this.savesCount,
//     required this.isLiked,
//     required this.isSaved,
//     required this.createdAt,
//     required this.media,
//   });

//   factory PostModel.fromJson(Map<String, dynamic> json) {
//     return PostModel(
//       id: json['id']?.toString() ?? '',
//       user: UserModel.fromJson(json['user'] ?? {}),
//       title: json['title'],
//       content: json['content'],
//       category: json['category'] ?? 'general',
//       postType: json['post_type'] ?? 'text',
//       visibility: json['visibility'] ?? 'public',
//       hashtags: List<String>.from(json['hashtags'] ?? []),
//       location: json['location'],
//       likesCount: json['likes_count'] ?? 0,
//       commentsCount: json['comments_count'] ?? 0,
//       sharesCount: json['shares_count'] ?? 0,
//       viewsCount: json['views_count'] ?? 0,
//       savesCount: json['saves_count'] ?? 0,
//       isLiked: json['is_liked'] ?? false,
//       isSaved: json['is_saved'] ?? false,
//       createdAt: DateTime.tryParse(json['created_at'] ?? '') ?? DateTime.now(),
//       media: (json['media'] as List<dynamic>?)
//               ?.map((e) => PostMediaModel.fromJson(e))
//               .toList() ??
//           [],
//     );
//   }
// }

// class FeedResponse {
//   final int count;
//   final String? next;
//   final String? previous;
//   final List<PostModel> results;

//   FeedResponse({
//     required this.count,
//     this.next,
//     this.previous,
//     required this.results,
//   });

//   factory FeedResponse.fromJson(Map<String, dynamic> json) {
//     return FeedResponse(
//       count: json['count'] ?? 0,
//       next: json['next'],
//       previous: json['previous'],
//       results: (json['results'] as List<dynamic>?)
//               ?.map((e) => PostModel.fromJson(e))
//               .toList() ??
//           [],
//     );
//   }
// }

// // ===================== API SERVICE =====================

// class HomeFeedService {
//   // Get Home Feed
//   static Future<FeedResponse> getHomeFeed({int page = 1, int pageSize = 20}) async {
//     final token = await AuthService.getToken();
    
//     if (token == null) {
//       throw Exception('User not authenticated');
//     }

//     final url = Uri.parse("${Api.baseUrl}/post/feed/?page=$page&page_size=$pageSize");

//     try {
//       final response = await http.get(
//         url,
//         headers: {
//           "Authorization": "Bearer $token",
//           "Content-Type": "application/json",
//         },
//       );

//       if (response.statusCode == 200) {
//         final data = jsonDecode(response.body);
//         return FeedResponse.fromJson(data);
//       } else if (response.statusCode == 401) {
//         throw Exception('Unauthorized: Please login again');
//       } else {
//         throw Exception('Failed to load feed: ${response.statusCode}');
//       }
//     } catch (e) {
//       throw Exception('Network error: $e');
//     }
//   }

// //   Like/Unlike Post
//   static Future<bool> toggleLike(String postId) async {
//     final token = await AuthService.getToken();
//     if (token == null) throw Exception('User not authenticated');

//     final url = Uri.parse("${Api.baseUrl}/post/$postId/like/");
    
//     final response = await http.post(
//       url,
//       headers: {
//         "Authorization": "Bearer $token",
//         "Content-Type": "application/json",
//       },
//     );

//     return response.statusCode == 200 || response.statusCode == 201;
//   }

//   // Save/Unsave Post
//   static Future<bool> toggleSave(String postId) async {
//     final token = await AuthService.getToken();
//     if (token == null) throw Exception('User not authenticated');

//     final url = Uri.parse("${Api.baseUrl}/post/$postId/save/");
    
//     final response = await http.post(
//       url,
//       headers: {
//         "Authorization": "Bearer $token",
//         "Content-Type": "application/json",
//       },
//     );

//     return response.statusCode == 200 || response.statusCode == 201;
//   }
// }
































import 'dart:convert';
import 'package:http/http.dart' as http;
import '../utils/api.dart';
import 'auth_service.dart';

// ===================== MODELS =====================

class UserModel {
  final String id;
  final String username;
  final String? profilePicture;
  final String? fullName;

  UserModel({
    required this.id,
    required this.username,
    this.profilePicture,
    this.fullName,
  });

  factory UserModel.fromJson(Map<String, dynamic> json) {
    return UserModel(
      id: json['id']?.toString()?? '',
      username: json['username']?? '',
      profilePicture: json['profile_picture'],
      fullName: json['full_name'],
    );
  }
}

class PostMediaModel {
  final String id;
  final String mediaType;
  final String file;
  final String? thumbnail;
  final String fileName;
  final int? width;
  final int? height;
  final int? durationSeconds;

  PostMediaModel({
    required this.id,
    required this.mediaType,
    required this.file,
    this.thumbnail,
    required this.fileName,
    this.width,
    this.height,
    this.durationSeconds,
  });

  factory PostMediaModel.fromJson(Map<String, dynamic> json) {
    return PostMediaModel(
      id: json['id']?.toString()?? '',
      mediaType: json['media_type']?? 'image',
      file: json['file']?? '',
      thumbnail: json['thumbnail'],
      fileName: json['file_name']?? '',
      width: json['width'],
      height: json['height'],
      durationSeconds: json['duration_seconds'],
    );
  }
}

class PostModel {
  final String id;
  final UserModel user;
  final String? title;
  final String? content;
  final String category;
  final String postType;
  final String visibility;
  final List<String> hashtags;
  final Map<String, dynamic>? location;
  int likesCount;
  int commentsCount;
  int sharesCount;
  int viewsCount;
  int savesCount;
  int likeCount;
  int confuseCount;
  int wrongCount;
  int impCount;
  int explainCount;
  String? myReaction;
  bool isLiked;
  bool isSaved;
  final DateTime createdAt;
  final List<PostMediaModel> media;

  PostModel({
    required this.id,
    required this.user,
    this.title,
    this.content,
    required this.category,
    required this.postType,
    required this.visibility,
    required this.hashtags,
    this.location,
    required this.likesCount,
    required this.commentsCount,
    required this.sharesCount,
    required this.viewsCount,
    required this.savesCount,
    required this.likeCount,
    required this.confuseCount,
    required this.wrongCount,
    required this.impCount,
    required this.explainCount,
    this.myReaction,
    required this.isLiked,
    required this.isSaved,
    required this.createdAt,
    required this.media,
  });

  factory PostModel.fromJson(Map<String, dynamic> json) {
    return PostModel(
      id: json['id']?.toString()?? '',
      user: UserModel.fromJson(json['user']?? {}),
      title: json['title'],
      content: json['content'],
      category: json['category']?? 'general',
      postType: json['post_type']?? 'text',
      visibility: json['visibility']?? 'public',
      hashtags: List<String>.from(json['hashtags']?? []),
      location: json['location'],
      likesCount: json['likes_count']?? 0,
      commentsCount: json['comments_count']?? 0,
      sharesCount: json['shares_count']?? 0,
      viewsCount: json['views_count']?? 0,
      savesCount: json['saves_count']?? 0,
      likeCount: json['like_count']?? 0,
      confuseCount: json['confuse_count']?? 0,
      wrongCount: json['wrong_count']?? 0,
      impCount: json['imp_count']?? 0,
      explainCount: json['explain_count']?? 0,
      myReaction: json['my_reaction'],
      isLiked: json['is_liked']?? json['my_reaction']!= null,
      isSaved: json['is_saved']?? false,
      createdAt: DateTime.tryParse(json['created_at']?? '')?? DateTime.now(),
      media: (json['media'] as List<dynamic>?)?.map((e) => PostMediaModel.fromJson(e)).toList()?? [],
    );
  }
}

class FeedResponse {
  final int count;
  final String? next;
  final String? previous;
  final List<PostModel> results;

  FeedResponse({
    required this.count,
    this.next,
    this.previous,
    required this.results,
  });

  factory FeedResponse.fromJson(Map<String, dynamic> json) {
    return FeedResponse(
      count: json['count']?? 0,
      next: json['next'],
      previous: json['previous'],
      results: (json['results'] as List<dynamic>?)?.map((e) => PostModel.fromJson(e)).toList()?? [],
    );
  }
}

// ===================== API SERVICE =====================

class HomeFeedService {
  static Future<FeedResponse> getHomeFeed({int page = 1, int pageSize = 20}) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/feed/?page=$page&page_size=$pageSize");
    try {
      final response = await http.get(url, headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"});
      if (response.statusCode == 200) {
        return FeedResponse.fromJson(jsonDecode(response.body));
      } else {
        throw Exception('Failed to load feed: ${response.statusCode}');
      }
    } catch (e) {
      throw Exception('Network error: $e');
    }
  }

  static Future<bool> toggleLike(String postId) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/$postId/like/");
    final response = await http.post(url, headers: {"Authorization": "Bearer $token"});
    return response.statusCode == 200 || response.statusCode == 201;
  }

  static Future<bool> toggleSave(String postId) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/$postId/save/");
    final response = await http.post(url, headers: {"Authorization": "Bearer $token"});
    return response.statusCode == 200 || response.statusCode == 201;
  }

  // NEW REACTION API - http://127.0.0.1:8000/post/like/{id}/reaction/
  static Future<Map<String, dynamic>> toggleReaction(String postId, String reaction) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/like/$postId/reaction/");
    final response = await http.post(
      url,
      headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
      body: jsonEncode({"reaction": reaction}),
    );
    if (response.statusCode == 200) {
      return jsonDecode(response.body);
    } else {
      throw Exception('Reaction failed: ${response.body}');
    }
  }
}