
// import 'dart:convert';
// import 'package:http/http.dart' as http;
// import 'package:shared_preferences/shared_preferences.dart';
// import '../utils/api.dart';
// import 'auth_service.dart';

// // ===================== MODELS =====================

// class UserModel {
//   final String id;
//   final String username;
//   final String? profilePicture;

//   UserModel({required this.id, required this.username, this.profilePicture});

//   factory UserModel.fromJson(Map<String, dynamic> j) {
//     String? pic = j['profilePicture'] ?? j['profile_picture'] ?? j['profile_photo'];
//     return UserModel(
//       id: j['id'].toString(),
//       username: j['username'] ?? '',
//       profilePicture: pic,
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
//       id: json['id']?.toString()?? '',
//       mediaType: json['media_type']?? 'image',
//       file: json['file']?? '',
//       thumbnail: json['thumbnail'],
//       fileName: json['file_name']?? '',
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
//   int likesCount;
//   int commentsCount;
//   int sharesCount;
//   int viewsCount;
//   int savesCount;
//   int likeCount;
//   int confuseCount;
//   int wrongCount;
//   int impCount;
//   int explainCount;
//   String? myReaction;
//   bool isLiked;
//   bool isSaved;
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
//     required this.likeCount,
//     required this.confuseCount,
//     required this.wrongCount,
//     required this.impCount,
//     required this.explainCount,
//     this.myReaction,
//     required this.isLiked,
//     required this.isSaved,
//     required this.createdAt,
//     required this.media,
//   });

//   factory PostModel.fromJson(Map<String, dynamic> json) {
//     return PostModel(
//       id: json['id']?.toString()?? '',
//       user: UserModel.fromJson(json['user']?? {}),
//       title: json['title'],
//       content: json['content'],
//       category: json['category']?? 'general',
//       postType: json['post_type']?? 'text',
//       visibility: json['visibility']?? 'public',
//       hashtags: List<String>.from(json['hashtags']?? []),
//       location: json['location'],
//       likesCount: json['likes_count']?? 0,
//       commentsCount: json['comments_count']?? 0,
//       sharesCount: json['shares_count']?? 0,
//       viewsCount: json['views_count']?? 0,
//       savesCount: json['saves_count']?? 0,
//       likeCount: json['like_count']?? 0,
//       confuseCount: json['confuse_count']?? 0,
//       wrongCount: json['wrong_count']?? 0,
//       impCount: json['imp_count']?? 0,
//       explainCount: json['explain_count']?? 0,
//       myReaction: json['my_reaction'],
//       isLiked: json['is_liked']?? json['my_reaction']!= null,
//       isSaved: json['is_saved']?? false,
//       createdAt: DateTime.tryParse(json['created_at']?? '')?? DateTime.now(),
//       media: (json['media'] as List<dynamic>?)?.map((e) => PostMediaModel.fromJson(e)).toList()?? [],
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
//       count: json['count']?? 0,
//       next: json['next'],
//       previous: json['previous'],
//       results: (json['results'] as List<dynamic>?)?.map((e) => PostModel.fromJson(e)).toList()?? [],
//     );
//   }
// }

// // ===================== API SERVICE WITH CACHE =====================

// class HomeFeedService {
//   static const String _feedCacheKey = 'cached_feed_raw_v2';

//   // 🔥 1. Cache se turant feed
//   static Future<FeedResponse?> getCachedFeed() async {
//     try {
//       final prefs = await SharedPreferences.getInstance();
//       final cached = prefs.getString(_feedCacheKey);
//       if (cached != null && cached.isNotEmpty) {
//         return FeedResponse.fromJson(jsonDecode(cached));
//       }
//     } catch (e) {
//       print("Feed cache parse error: $e");
//     }
//     return null;
//   }

//   // 🔥 2. API se fresh + cache save (sirf page 1)
//   static Future<FeedResponse> getFeedFromAPI({int page = 1, int pageSize = 20}) async {
//     final token = await AuthService.getToken();
//     if (token == null) throw Exception('User not authenticated');
//     final url = Uri.parse("${Api.baseUrl}/post/feed/?page=$page&page_size=$pageSize");

//     final response = await http.get(url, headers: {
//       "Authorization": "Bearer $token",
//       "Content-Type": "application/json"
//     });

//     if (response.statusCode == 200) {
//       if (page == 1) {
//         final prefs = await SharedPreferences.getInstance();
//         await prefs.setString(_feedCacheKey, response.body); // RAW BODY SAVE - no toJson needed
//       }
//       return FeedResponse.fromJson(jsonDecode(response.body));
//     } else {
//       throw Exception('Failed to load feed: ${response.statusCode}');
//     }
//   }

//   // 🔥 3. MAIN CACHE-FIRST LOGIC - jaise Profile me hai
//   static Future<FeedResponse> getHomeFeed({int page = 1, int pageSize = 20}) async {
//     // Page 1 ke liye cache check
//     if (page == 1) {
//       final prefs = await SharedPreferences.getInstance();
//       final cached = prefs.getString(_feedCacheKey);
//       if (cached != null && cached.isNotEmpty) {
//         try {
//           final cachedFeed = FeedResponse.fromJson(jsonDecode(cached));
//           // Background me refresh
//           getFeedFromAPI(page: page, pageSize: pageSize).catchError((e) {
//             print("Background feed refresh failed: $e");
//           });
//           return cachedFeed;
//         } catch (e) {
//           print("Cache decode failed, fetching API: $e");
//         }
//       }
//     }
//     return await getFeedFromAPI(page: page, pageSize: pageSize);
//   }

//   // 🔥 4. Pull to refresh - hamesha API
//   static Future<FeedResponse> refreshFeed({int page = 1, int pageSize = 20}) async {
//     return await getFeedFromAPI(page: page, pageSize: pageSize);
//   }

//   static Future<void> clearFeedCache() async {
//     final prefs = await SharedPreferences.getInstance();
//     await prefs.remove(_feedCacheKey);
//   }

//   static Future<bool> toggleLike(String postId) async {
//     final token = await AuthService.getToken();
//     if (token == null) throw Exception('User not authenticated');
//     final url = Uri.parse("${Api.baseUrl}/post/$postId/like/");
//     final response = await http.post(url, headers: {"Authorization": "Bearer $token"});
//     return response.statusCode == 200 || response.statusCode == 201;
//   }

//   static Future<bool> toggleSave(String postId) async {
//     final token = await AuthService.getToken();
//     if (token == null) throw Exception('User not authenticated');
//     final url = Uri.parse("${Api.baseUrl}/post/$postId/save/");
//     final response = await http.post(url, headers: {"Authorization": "Bearer $token"});
//     return response.statusCode == 200 || response.statusCode == 201;
//   }

//   static Future<Map<String, dynamic>> toggleReaction(String postId, String reaction) async {
//     final token = await AuthService.getToken();
//     if (token == null) throw Exception('User not authenticated');
//     final url = Uri.parse("${Api.baseUrl}/post/like/$postId/reaction/");
//     final response = await http.post(
//       url,
//       headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
//       body: jsonEncode({"reaction": reaction}),
//     );
//     if (response.statusCode == 200) {
//       return jsonDecode(response.body);
//     } else {
//       throw Exception('Reaction failed: ${response.body}');
//     }
//   }


// static Future<Map<String, dynamic>> toggleSave(String postId) async {
//   final token = await AuthService.getToken();
//   final url = Uri.parse("${Api.baseUrl}/post/$postId/save/");
//   final res = await http.post(url, headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"}, body: jsonEncode({"collection_name": "default"}));
//   if (res.statusCode == 200 || res.statusCode == 201) return jsonDecode(res.body);
//   throw Exception(res.body);
// }

   

// }



































import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../utils/api.dart';
import 'auth_service.dart';

// ===================== MODELS =====================

class UserModel {
  final String id;
  final String username;
  final String? profilePicture;
  UserModel({required this.id, required this.username, this.profilePicture});
  factory UserModel.fromJson(Map<String, dynamic> j) {
    String? pic = j['profilePicture'] ?? j['profile_picture'] ?? j['profile_photo'];
    return UserModel(id: j['id'].toString(), username: j['username'] ?? '', profilePicture: pic);
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
  PostMediaModel({required this.id, required this.mediaType, required this.file, this.thumbnail, required this.fileName, this.width, this.height, this.durationSeconds});
  factory PostMediaModel.fromJson(Map<String, dynamic> json) {
    return PostMediaModel(
      id: json['id']?.toString() ?? '', mediaType: json['media_type'] ?? 'image', file: json['file'] ?? '',
      thumbnail: json['thumbnail'], fileName: json['file_name'] ?? '', width: json['width'], height: json['height'], durationSeconds: json['duration_seconds'],
    );
  }
}

class PostModel {
  final String id; final UserModel user; final String? title; final String? content; final String category; final String postType; final String visibility;
  final List<String> hashtags; final Map<String, dynamic>? location;
  int likesCount; int commentsCount; int sharesCount; int viewsCount; int savesCount;
  int likeCount; int confuseCount; int wrongCount; int impCount; int explainCount;
  String? myReaction; bool isLiked; bool isSaved; final DateTime createdAt; final List<PostMediaModel> media;
  PostModel({required this.id, required this.user, this.title, this.content, required this.category, required this.postType, required this.visibility, required this.hashtags, this.location, required this.likesCount, required this.commentsCount, required this.sharesCount, required this.viewsCount, required this.savesCount, required this.likeCount, required this.confuseCount, required this.wrongCount, required this.impCount, required this.explainCount, this.myReaction, required this.isLiked, required this.isSaved, required this.createdAt, required this.media});
  factory PostModel.fromJson(Map<String, dynamic> json) {
    return PostModel(
      id: json['id']?.toString() ?? '', user: UserModel.fromJson(json['user'] ?? {}), title: json['title'], content: json['content'],
      category: json['category'] ?? 'general', postType: json['post_type'] ?? 'text', visibility: json['visibility'] ?? 'public',
      hashtags: List<String>.from(json['hashtags'] ?? []), location: json['location'],
      likesCount: json['likes_count'] ?? 0, commentsCount: json['comments_count'] ?? 0, sharesCount: json['shares_count'] ?? 0, viewsCount: json['views_count'] ?? 0, savesCount: json['saves_count'] ?? 0,
      likeCount: json['like_count'] ?? 0, confuseCount: json['confuse_count'] ?? 0, wrongCount: json['wrong_count'] ?? 0, impCount: json['imp_count'] ?? 0, explainCount: json['explain_count'] ?? 0,
      myReaction: json['my_reaction'], isLiked: json['is_liked'] ?? json['my_reaction'] != null, isSaved: json['is_saved'] ?? false,
      createdAt: DateTime.tryParse(json['created_at'] ?? '') ?? DateTime.now(),
      media: (json['media'] as List<dynamic>?)?.map((e) => PostMediaModel.fromJson(e)).toList() ?? [],
    );
  }
}

class FeedResponse {
  final int count; final String? next; final String? previous; final List<PostModel> results;
  FeedResponse({required this.count, this.next, this.previous, required this.results});
  factory FeedResponse.fromJson(Map<String, dynamic> json) {
    return FeedResponse(
      count: json['count'] ?? 0, next: json['next'], previous: json['previous'],
      results: (json['results'] as List<dynamic>?)?.map((e) => PostModel.fromJson(e)).toList() ?? [],
    );
  }
}

// ===================== API SERVICE WITH CACHE =====================

class HomeFeedService {
  static const String _feedCacheKey = 'cached_feed_raw_v2';

  static Future<FeedResponse?> getCachedFeed() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final cached = prefs.getString(_feedCacheKey);
      if (cached != null && cached.isNotEmpty) return FeedResponse.fromJson(jsonDecode(cached));
    } catch (e) { print("Feed cache parse error: $e"); }
    return null;
  }

  static Future<FeedResponse> getFeedFromAPI({int page = 1, int pageSize = 20}) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/feed/?page=$page&page_size=$pageSize");
    final response = await http.get(url, headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"});
    if (response.statusCode == 200) {
      if (page == 1) {
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString(_feedCacheKey, response.body);
      }
      return FeedResponse.fromJson(jsonDecode(response.body));
    } else { throw Exception('Failed to load feed: ${response.statusCode}'); }
  }

  static Future<FeedResponse> getHomeFeed({int page = 1, int pageSize = 20}) async {
    if (page == 1) {
      final prefs = await SharedPreferences.getInstance();
      final cached = prefs.getString(_feedCacheKey);
      if (cached != null && cached.isNotEmpty) {
        try {
          final cachedFeed = FeedResponse.fromJson(jsonDecode(cached));
          getFeedFromAPI(page: page, pageSize: pageSize).catchError((e) => print("Background refresh failed: $e"));
          return cachedFeed;
        } catch (e) { print("Cache decode failed: $e"); }
      }
    }
    return await getFeedFromAPI(page: page, pageSize: pageSize);
  }

  static Future<FeedResponse> refreshFeed({int page = 1, int pageSize = 20}) async => await getFeedFromAPI(page: page, pageSize: pageSize);

  static Future<void> clearFeedCache() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_feedCacheKey);
  }

  static Future<bool> toggleLike(String postId) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/$postId/like/");
    final response = await http.post(url, headers: {"Authorization": "Bearer $token"});
    return response.statusCode == 200 || response.statusCode == 201;
  }

  // 🔥 FINAL - ONLY ONE toggleSave
  static Future<Map<String, dynamic>> toggleSave(String postId, {String collection = 'default'}) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/$postId/save/");
    final response = await http.post(
      url,
      headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
      body: jsonEncode({"collection_name": collection}),
    );
    if (response.statusCode == 200 || response.statusCode == 201) {
      return jsonDecode(response.body);
    } else {
      throw Exception('Save failed: ${response.body}');
    }
  }

  static Future<Map<String, dynamic>> toggleReaction(String postId, String reaction) async {
    final token = await AuthService.getToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/like/$postId/reaction/");
    final response = await http.post(url, headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"}, body: jsonEncode({"reaction": reaction}));
    if (response.statusCode == 200) return jsonDecode(response.body);
    throw Exception('Reaction failed: ${response.body}');
  }
}