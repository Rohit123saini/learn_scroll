# `profile` Flutter Frontend — Complete Self-Contained Reference

Ye ek hi file hai jisme poore **profile** (own profile, edit profile, target/
other-user profile with follow + message, aur standalone document/video
viewers) Flutter frontend ka sara code, connections, data-flow aur known
issues cover hain. Iske alawa kisi aur file ki zaroorat nahi — sab kuch
(model → api_service → profile → edit_profile → target_profile →
document_viewer_screen → video_player_screen) yahin milega, saath me har
piece kya kaam karta hai uski explanation bhi.

---

## 1. App Overview

**Module:** `profile` feature — Flutter side (screens + api client + models)
**Purpose:** Apni profile dekhna/edit karna, kisi aur user ki profile dekhna
(follow/unfollow/accept/reject + direct message shuru karna), aur us profile
ke posts (Photos/Videos + Documents tabs) browse karna.

**Design language:** Same navy (`#030F27`) brand-anchor jo `post` module me
bhi hai — iska matlab ye module us Flutter app ke usi design-system ka hissa
hai jo `flutter_post_app.md` me documented hai.

**Backend dependency:** Har network call `ApiService` (static methods, koi
instance nahi banta — `post` module ke `ApiService` se alag pattern, wahan
instance banta tha) se hoti hai, jo `Api.baseUrl` + `AuthService.getToken()`
use karta hai (dono is module ke bahar, shared/core me).

**Files in this module:**
| File | Responsibility |
|---|---|
| `model.dart` | `ProfileModel`, `TargetProfileModel`, `UpdateProfileResponse`, `PostMediaModel`, `PostModel` — profile aur uske posts ke liye parsed models |
| `api_service.dart` | Saara network layer: cache-first profile load, target profile, follow/accept/reject, update profile (multipart), my-posts / target-user-posts, generic authenticated file download |
| `profile.dart` | Apni profile screen: header, stats, bio, Coins, Share, Logout, Photos/Videos + Documents tabs (grid), pull-to-refresh, edit-profile entry point |
| `edit_profile.dart` | Profile edit form: username/first/last/bio + photo picker → `ApiService.updateProfile` |
| `target_profile.dart` | Kisi aur user ki profile: follow-state-aware button (Follow/Requested/Following/Follow Back/Confirm-Delete), direct message button, same Photos/Videos + Documents tabs with private-account gating |
| `document_viewer_screen.dart` | Standalone full-screen PDF/document viewer widget (Syncfusion) — **not wired into any screen in this batch, see §11** |
| `video_player_screen.dart` | Standalone full-screen video player widget (`chewie` + `video_player`) — **also not wired into any screen in this batch, see §11** |

---

## 2. ⚠️ External Dependencies Required

### pubspec.yaml packages
```yaml
dependencies:
  image_picker:                      # edit_profile.dart photo picker
  cached_network_image:              # avatar/media thumbnails everywhere
  http:
  http_parser:                        # api_service.dart MultipartFile contentType
  shared_preferences:                  # profile cache (cache-first load)
  path_provider:                       # download target directory
  share_plus:                          # Share Profile button
  video_player:
  chewie:                              # video_player_screen.dart only
  url_launcher:                        # profile.dart's own document download path
  dio:                                  # document download (both profile.dart's own path and ApiService.downloadFile use plain http, not dio here — target_profile.dart's PDF-page-count probe DOES use dio)
  syncfusion_flutter_pdfviewer:
  syncfusion_flutter_pdf:              # target_profile.dart / profile.dart use this (not just the viewer package) to read PDF page-count client-side
  timeago:                             # profile.dart relative "joined"/post timestamps
```

### Project-local files this module imports (not included here — live elsewhere in the app)
```
../../utils/api.dart                        # Api.baseUrl
../../services/auth_service.dart            # AuthService.getToken()/.logout()
../../login/login_screen.dart               # LoginScreen — post-logout redirect
../../post/screens/singlepost.dart          # SinglePostPage — tapping any post grid tile
../../message/services/message_api_service.dart   # MessageApiService.getOrCreateConversation — target_profile.dart ONLY
../../message/screens/chat_screen.dart             # ChatScreen — target_profile.dart ONLY
```
`target_profile.dart` alone pulls in the `message` module (2 files) that
`profile.dart` (own profile) does not need at all — makes sense, you can't
message yourself.

### Backend endpoints this module calls
```
GET   /profile/                          — own profile          (ApiService.getProfile/getProfileFromAPI/refreshProfile)
GET   /profile/profile/<username>/       — target profile        (ApiService.getTargetProfile) — note the doubled "profile/profile/" segment, see §11
POST  /profile/follow/<user_id>/         — follow/unfollow toggle
POST  /profile/accept-request/<follow_id>/  — accept a pending follow request
POST  /profile/reject-request/<follow_id>/  — reject a pending follow request
PATCH /profile/update/                   — update own profile (multipart: username/first_name/last_name/bio/profile_photo)
GET   /post/list/?my_posts=true&page=N          — own posts (§11 item 5: `my_posts` param — see note)
GET   /post/list/?target_user_id=<id>&page=N    — target user's posts (matches `post` module backend's `PostListAPIView` — see `post_app.md`)
```
`getMyPosts`/`getTargetUserPosts` both call the **`post` module's** own
`/post/list/` endpoint (documented in `post_app.md` §6/§13) — this is the
one genuinely cross-checked match between the two modules' docs.

---

Neeche sab 7 files ka poora code hai, dependency-order me (model → api_service → profile → edit_profile → target_profile → standalone viewers).

---

## 3. `model.dart` (full code)

```dart
class ProfileModel {
  final int id;
  final String username;
  final String firstName;
  final String lastName;
  final String profilePhoto;
  final String bio;
  final bool isPrivate;
  final bool isVerified;
  final int followers;
  final int following;
  final int posts;
  final int coin;

  ProfileModel({
    required this.id,
    required this.username,
    required this.firstName,
    required this.lastName,
    required this.profilePhoto,
    required this.bio,
    required this.isPrivate,
    required this.isVerified,
    required this.followers,
    required this.following,
    required this.posts,
    required this.coin,
  });

  factory ProfileModel.fromJson(Map<String, dynamic> json) {
    final data = json["data"] ?? {};
    return ProfileModel(
      id: data["id"] ?? 0,
      username: data["username"] ?? "",
      firstName: data["first_name"] ?? "",
      lastName: data["last_name"] ?? "",
      profilePhoto: data["profile_photo"] ?? "",
      bio: data["bio"] ?? "",
      isPrivate: data["is_private"] ?? false,
      isVerified: data["is_verified"] ?? false,
      followers: data["followers_count"] ?? 0,
      following: data["following_count"] ?? 0,
      posts: data["posts_count"] ?? 0,
      coin: data["coin"] ?? 0,
    );
  }
}

class TargetProfileModel {
  final int myId;
  final String myUsername;
  final int targetUserId;
  final String targetUsername;
  final String username;
  final String firstName;
  final String lastName;
  final String profilePhoto;
  final String bio;
  final bool isPrivate;
  final bool isVerified;
  final int followers;
  final int following;
  final int posts;
  // 🔥 Main user ne target ko follow kiya
  final String? myFollowStatus; // null, PENDING, ACCEPTED
  final int? myFollowId;
  // 🔥 Target user ne main user ko follow kiya
  final String? theirFollowStatus; // null, PENDING, ACCEPTED
  final int? theirFollowId;

  TargetProfileModel({
    required this.myId,
    required this.myUsername,
    required this.targetUserId,
    required this.targetUsername,
    required this.username,
    required this.firstName,
    required this.lastName,
    required this.profilePhoto,
    required this.bio,
    required this.isPrivate,
    required this.isVerified,
    required this.followers,
    required this.following,
    required this.posts,
    this.myFollowStatus,
    this.myFollowId,
    this.theirFollowStatus,
    this.theirFollowId,
  });

  factory TargetProfileModel.fromJson(Map<String, dynamic> json) {
    final dataMap = json['data'] ?? {};
    return TargetProfileModel(
      myId: json['my_id'] ?? 0,
      myUsername: json['my_username'] ?? '',
      targetUserId: json['target_user_id'] ?? 0,
      targetUsername: json['target_username'] ?? '',
      username: dataMap['username'] ?? '',
      firstName: dataMap['first_name'] ?? '',
      lastName: dataMap['last_name'] ?? '',
      profilePhoto: dataMap['profile_photo'] ?? '',
      bio: dataMap['bio'] ?? '',
      isPrivate: dataMap['is_private'] ?? false,
      isVerified: dataMap['is_verified'] ?? false,
      followers: dataMap['followers_count'] ?? 0,
      following: dataMap['following_count'] ?? 0,
      posts: dataMap['posts_count'] ?? 0,
      myFollowStatus: json['my_follow_status'],
      myFollowId: json['my_follow_id'],
      theirFollowStatus: json['their_follow_status'],
      theirFollowId: json['their_follow_id'],
    );
  }
}

class UpdateProfileResponse {
  final bool status;
  final String message;
  final ProfileModel data;

  UpdateProfileResponse({
    required this.status,
    required this.message,
    required this.data,
  });

  factory UpdateProfileResponse.fromJson(Map<String, dynamic> json) {
    return UpdateProfileResponse(
      status: json['status'] ?? false,
      message: json['message'] ?? '',
      data: ProfileModel.fromJson(json['data']),
    );
  }
}

class PostMediaModel {
  final String id;
  final String mediaType;
  final String file;
  final String? thumbnail;
  final String fileName;
  final int fileSizeBytes;
  final String mimeType;
  final int displayOrder;

  PostMediaModel({
    required this.id,
    required this.mediaType,
    required this.file,
    this.thumbnail,
    required this.fileName,
    required this.fileSizeBytes,
    required this.mimeType,
    required this.displayOrder,
  });

  factory PostMediaModel.fromJson(Map<String, dynamic> json) {
    return PostMediaModel(
      id: json['id']?? '',
      mediaType: json['media_type']?? '',
      file: json['file']?? '',
      thumbnail: json['thumbnail'],
      fileName: json['file_name']?? '',
      fileSizeBytes: json['file_size_bytes']?? 0,
      mimeType: json['mime_type']?? '',
      displayOrder: json['display_order']?? 0,
    );
  }
}




class PostModel {
  final String id;
  final String? title;
  final String content;
  final String category;
  final String postType;
  final String visibility;
  final List<String> hashtags;
  final int likesCount;
  final int commentsCount;
  final int viewsCount;
  final int savesCount;
  final bool isLiked;
  final bool isSaved;
  final String createdAt;
  final List<PostMediaModel> media;
  final Map<String, dynamic>? user; // 🔥 Add kar de
  final String? thumbnailUrl;
  PostModel({
    required this.id,
    this.title,
    required this.content,
    required this.category,
    required this.postType,
    required this.visibility,
    required this.hashtags,
    required this.likesCount,
    required this.commentsCount,
    required this.viewsCount,
    required this.savesCount,
    required this.isLiked,
    required this.isSaved,
    required this.createdAt,
    required this.media,
    this.user, // 🔥 Add kar
    this.thumbnailUrl,
  });

  factory PostModel.fromJson(Map<String, dynamic> json) {
    return PostModel(
      id: json['id']?? '',
      title: json['title'],
      content: json['content']?? '',
      category: json['category']?? '',
      postType: json['post_type']?? '',
      visibility: json['visibility']?? '',
      hashtags: List<String>.from(json['hashtags']?? []),
      likesCount: json['likes_count']?? 0,
      commentsCount: json['comments_count']?? 0,
      viewsCount: json['views_count']?? 0,
      savesCount: json['saves_count']?? 0,
      isLiked: json['is_liked']?? false,
      isSaved: json['is_saved']?? false,
      createdAt: json['created_at']?? '',
      media: (json['media'] as List<dynamic>?)
            ?.map((e) => PostMediaModel.fromJson(e))
            .toList()??
          [],
      user: json['user'], // 🔥 Add kar
      thumbnailUrl: json['thumbnail_url'], 
    );
  }

  String get firstImageUrl {
    if (media.isEmpty) return '';
    return media.first.file;
  }
}```

---

## 4. `api_service.dart` (full code)

```dart
import 'dart:io';
import 'package:http_parser/http_parser.dart'; 
import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../utils/api.dart';
import './model.dart';
import '../services/auth_service.dart';
import 'package:path_provider/path_provider.dart';

class ApiService {
  static const String _profileCacheKey = 'cached_profile';

  // 🔥 1. Cache se turant data do
  static Future<ProfileModel?> getCachedProfile() async {
    final prefs = await SharedPreferences.getInstance();
    final cachedData = prefs.getString(_profileCacheKey);
    if (cachedData != null) {
      return ProfileModel.fromJson(jsonDecode(cachedData));
    }
    return null;
  }

  // 🔥 2. API se fresh data laao + cache update karo
  static Future<ProfileModel> getProfileFromAPI() async {
    final token = await AuthService.getToken();
    final url = Uri.parse("${Api.baseUrl}/profile/");

    final response = await http.get(
      url,
      headers: {
        "Authorization": "Bearer $token",
        "Content-Type": "application/json",
      },
    );

    if (response.statusCode == 200) {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_profileCacheKey, response.body);
      return ProfileModel.fromJson(jsonDecode(response.body));
    } else {
      throw Exception('Failed to load profile: ${response.body}');
    }
  }

  // 🔥 3. Main function - CACHE FIRST, phir background refresh
  static Future<ProfileModel> getProfile() async {
    final prefs = await SharedPreferences.getInstance();
    
    // Step 1: Cache check karo
    final cachedData = prefs.getString(_profileCacheKey);
    if (cachedData != null) {
      // Cache mila to turant return kar do
      final cachedProfile = ProfileModel.fromJson(jsonDecode(cachedData));
      
      // Step 2: Background me API call - error ignore kar dena
      getProfileFromAPI().catchError((e) {
        print("Background refresh failed: $e");
      });
      
      return cachedProfile; // Offline me yahi dikhega
    }

    // Cache nahi mila to API se lao
    return await getProfileFromAPI();
  }

  // 🔥 4. Pull to refresh ke liye - hamesha API call
  static Future<ProfileModel> refreshProfile() async {
    return await getProfileFromAPI();
  }

  // 🔥 5. Cache clear
  static Future<void> clearProfileCache() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_profileCacheKey);
  }

  // 🔥 6. Target Profile
  static Future<TargetProfileModel> getTargetProfile(String username) async {
    final token = await AuthService.getToken();
    final url = Uri.parse("${Api.baseUrl}/profile/profile/$username/");
    
    final response = await http.get(url, headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    });

    if (response.statusCode == 200) {
      return TargetProfileModel.fromJson(jsonDecode(response.body));
    } else {
      throw Exception('Failed to load target profile: ${response.body}');
    }
  }

  static Future<TargetProfileModel> refreshTargetProfile(String username) async {
    return await getTargetProfile(username);
  }

  // 🔥 7. Follow/Unfollow
  static Future<Map<String, dynamic>> followUser(int userId) async {
    final token = await AuthService.getToken();
    final url = Uri.parse("${Api.baseUrl}/profile/follow/$userId/");
    
    final response = await http.post(url, headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    });

    if (response.statusCode == 200 || response.statusCode == 201) {
      return jsonDecode(response.body); 
    } else {
      throw Exception('Follow failed: ${response.statusCode} - ${response.body}');
    }
  }

  // 🔥 8. Accept Request
  static Future<Map<String, dynamic>> acceptFollowRequest(int followId) async {
    final token = await AuthService.getToken();
    final url = Uri.parse("${Api.baseUrl}/profile/accept-request/$followId/");
    
    final response = await http.post(url, headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    });

    if (response.statusCode == 200) {
      return jsonDecode(response.body); 
    } else {
      throw Exception('Accept failed: ${response.statusCode} - ${response.body}');
    }
  }

  // 🔥 9. Reject Request
  static Future<Map<String, dynamic>> rejectFollowRequest(int followId) async {
    final token = await AuthService.getToken();
    final url = Uri.parse("${Api.baseUrl}/profile/reject-request/$followId/");
    
    final response = await http.post(url, headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    });

    if (response.statusCode == 200) {
      return jsonDecode(response.body); 
    } else {
      throw Exception('Reject failed: ${response.statusCode} - ${response.body}');
    }
  }

  // 🔥 10. Update Profile - Image + text
  static Future<UpdateProfileResponse> updateProfile({
    String? username,
    String? firstName,
    String? lastName,
    String? bio,
    File? profilePhoto,
  }) async {
    final token = await AuthService.getToken();
    final url = Uri.parse("${Api.baseUrl}/profile/update/");
    
    var request = http.MultipartRequest('PATCH', url);
    request.headers['Authorization'] = 'Bearer $token';

    if (username != null && username.isNotEmpty) request.fields['username'] = username;
    if (firstName != null) request.fields['first_name'] = firstName;
    if (lastName != null) request.fields['last_name'] = lastName;
    if (bio != null) request.fields['bio'] = bio;

    if (profilePhoto != null) {
      String extension = profilePhoto.path.split('.').last.toLowerCase();
      MediaType contentType = MediaType('image', 'jpeg');
      if (extension == 'png') contentType = MediaType('image', 'png');
      
      request.files.add(
        await http.MultipartFile.fromPath(
          'profile_photo',
          profilePhoto.path,
          contentType: contentType,
        ),
      );
    }

    final streamedResponse = await request.send();
    final response = await http.Response.fromStream(streamedResponse);

    if (response.statusCode == 200) {
      final prefs = await SharedPreferences.getInstance();
      final updatedData = jsonDecode(response.body);
      await prefs.setString(_profileCacheKey, jsonEncode(updatedData['data']));
      
      return UpdateProfileResponse.fromJson(updatedData);
    } else {
      throw Exception('Update failed: ${response.statusCode} - ${response.body}');
    }
  }

// 🔥 11. Get My Posts - sirf login user ke posts
static Future<List<PostModel>> getMyPosts({int page = 1}) async {
  final token = await AuthService.getToken();
  final url = Uri.parse("${Api.baseUrl}/post/list/?my_posts=true&page=$page");

  final response = await http.get(
    url,
    headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    },
  );

  if (response.statusCode == 200) {
    final data = jsonDecode(response.body);
    final List results = data['results']?? [];
    return results.map((e) => PostModel.fromJson(e)).toList();
  } else {
    throw Exception('Failed to load posts: ${response.body}');
  }
}


// 🔥 Add this method in ApiService class
static Future<void> downloadFile(String url, String fileName) async {
  try {
    final token = await AuthService.getToken();
    final response = await http.get(
      Uri.parse(url),
      headers: {"Authorization": "Bearer $token"},
    );

    if (response.statusCode == 200) {
      final directory = await getApplicationDocumentsDirectory();
      final file = File('${directory.path}/$fileName');
      await file.writeAsBytes(response.bodyBytes);
    } else {
      throw Exception('Download failed: ${response.statusCode}');
    }
  } catch (e) {
    throw Exception('Download error: $e');
  }
}


















// 🔥 12. Get Target User Posts - user_id ke hisab se
static Future<List<PostModel>> getTargetUserPosts(int targetUserId, {int page = 1}) async {
  final token = await AuthService.getToken();
  final url = Uri.parse("${Api.baseUrl}/post/list/?target_user_id=$targetUserId&page=$page");

  final response = await http.get(
    url,
    headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    },
  );

  if (response.statusCode == 200) {
    final data = jsonDecode(response.body);
    final List results = data['results']?? [];
    return results.map((e) => PostModel.fromJson(e)).toList();
  } else if (response.statusCode == 403) {
    throw Exception('PRIVATE_ACCOUNT');
  } else {
    throw Exception('Failed to load posts: ${response.body}');
  }
}











}
















```

---

## 5. `profile.dart` (full code)

```dart

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:share_plus/share_plus.dart';
import 'package:video_player/video_player.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:syncfusion_flutter_pdf/pdf.dart';
import 'dart:io';
import 'package:timeago/timeago.dart' as timeago;

import '../api_service.dart';
import '../model.dart';
import '../../utils/api.dart';
import '../../services/auth_service.dart';
import '../../login/login_screen.dart';
import 'edit_profile.dart';
// NAYA IMPORT - path apne project ke hisab se check kar lena
import '../../post/screens/singlepost.dart';

class ProfileScreen extends StatefulWidget {
  final VoidCallback? onBackToHome;

  const ProfileScreen({super.key, this.onBackToHome});

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen>
    with SingleTickerProviderStateMixin {
  ProfileModel? user;
  List<PostModel> myPosts = [];
  List<PostModel> mediaPosts = [];
  List<PostModel> documentPosts = [];
  bool isLoading = true;
  bool isPostsLoading = true;
  bool isRefreshing = false;
  String? errorMessage;
  static const bgColor = Color(0xFF030F27);
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
    _loadData();
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  Future<void> _loadData({bool forceRefresh = false}) async {
    if (forceRefresh) {
      setState(() => isRefreshing = true);
    }
    await Future.wait([
      _loadProfile(forceRefresh: forceRefresh),
      _loadMyPosts(),
    ]);
    if (forceRefresh) {
      setState(() => isRefreshing = false);
    }
  }

  Future<void> _loadProfile({bool forceRefresh = false}) async {
    if (!forceRefresh) {
      setState(() {
        isLoading = true;
        errorMessage = null;
      });
    }
    try {
      final data = forceRefresh
         ? await ApiService.refreshProfile()
          : await ApiService.getProfile();
      if (mounted) {
        setState(() {
          user = data;
          isLoading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          errorMessage = e.toString();
          isLoading = false;
        });
      }
    }
  }

  Future<void> _loadMyPosts() async {
    setState(() => isPostsLoading = true);
    try {
      final posts = await ApiService.getMyPosts();
      if (mounted) {
        setState(() {
          myPosts = posts;
          mediaPosts = posts
             .where((p) => p.postType == 'image' || p.postType == 'video')
             .toList();
          documentPosts = posts
             .where((p) => ['document', 'pdf', 'excel', 'docx', 'xls', 'doc']
                 .contains(p.postType))
             .toList();
          isPostsLoading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() => isPostsLoading = false);
        print("Posts load error: $e");
      }
    }
  }

  Future<void> _refresh() async {
    await _loadData(forceRefresh: true);
  }

  Future<void> _goToEditProfile() async {
    if (user == null) return;
    final result = await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => EditProfileScreen(user: user!),
      ),
    );
    if (result == true) {
      await _loadData(forceRefresh: true);
    }
  }

  Future<void> _logout() async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text("Logout"),
        content: const Text("Are you sure you want to logout?"),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text("Cancel"),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text("Logout", style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (confirm == true) {
      await ApiService.clearProfileCache();
      await AuthService.logout();
      if (mounted) {
        Navigator.pushAndRemoveUntil(
          context,
          MaterialPageRoute(builder: (context) => const LoginScreen()),
          (route) => false,
        );
      }
    }
  }

  Future<void> _downloadDocument(String url, String fileName) async {
    try {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Downloading...")),
      );
      if (await canLaunchUrl(Uri.parse(url))) {
        await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
      } else {
        final dio = Dio();
        final dir = await getApplicationDocumentsDirectory();
        final filePath = "${dir.path}/$fileName";
        await dio.download(url, filePath);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Downloaded: $filePath")),
        );
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Download failed: $e"), backgroundColor: Colors.red),
      );
    }
  }

  void _openSinglePost(String postId) {
    // Yahan dynamic id jayegi
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => SinglePostPage(postId: postId),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (isLoading && user == null) {
      return const Scaffold(
        backgroundColor: Colors.white,
        body: Center(child: CircularProgressIndicator(color: bgColor)),
      );
    }
    if (errorMessage!= null && user == null) {
      return Scaffold(
        backgroundColor: Colors.white,
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(16.0),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Icon(Icons.error_outline, color: Colors.red, size: 60),
                const SizedBox(height: 16),
                Text(errorMessage!,
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: Colors.red, fontSize: 16)),
                const SizedBox(height: 16),
                ElevatedButton(
                  onPressed: () => _loadData(),
                  style: ElevatedButton.styleFrom(backgroundColor: bgColor),
                  child: const Text("Retry", style: TextStyle(color: Colors.white)),
                ),
              ],
            ),
          ),
        ),
      );
    }
    if (user == null) {
      return const Scaffold(
        backgroundColor: Colors.white,
        body: Center(child: Text("No Profile Found")),
      );
    }

    return Scaffold(
      backgroundColor: Colors.white,
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: SizedBox(
            width: double.infinity,
            height: 45,
            child: ElevatedButton.icon(
              onPressed: _logout,
              icon: const Icon(Icons.logout, size: 20, color: Colors.white),
              label: const Text("Logout",
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white)),
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.red.shade600,
                elevation: 0,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
              ),
            ),
          ),
        ),
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        color: bgColor,
        child: Stack(
          children: [
            SingleChildScrollView(
              physics: const AlwaysScrollableScrollPhysics(),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.only(top: 45, left: 4, right: 4, bottom: 10),
                    color: bgColor,
                    child: Row(
                      children: [
                        IconButton(
                          icon: const Icon(Icons.arrow_back, color: Colors.white),
                          onPressed: () {
                            if (widget.onBackToHome!= null) {
                              widget.onBackToHome!();
                            } else {
                              Navigator.maybePop(context);
                            }
                          },
                        ),
                        Expanded(
                          child: Center(
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Flexible(
                                  child: Text(
                                    user!.username,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                        color: Colors.white, fontSize: 20, fontWeight: FontWeight.bold),
                                  ),
                                ),
                                if (user!.isVerified)...[
                                  const SizedBox(width: 4),
                                  const Icon(Icons.verified, color: Colors.blue, size: 18),
                                ]
                              ],
                            ),
                          ),
                        ),
                        IconButton(
                          icon: const Icon(Icons.edit, color: Colors.white),
                          onPressed: _goToEditProfile,
                          tooltip: "Edit Profile",
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 20),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Row(
                      children: [
                        CircleAvatar(
                          radius: 45,
                          backgroundColor: Colors.grey.shade300,
                          child: ClipOval(
                            child: user!.profilePhoto.isEmpty
                               ? const Icon(Icons.person, size: 45, color: Colors.grey)
                                : CachedNetworkImage(
                                    imageUrl: user!.profilePhoto.startsWith('http')
                                       ? user!.profilePhoto
                                        : "${Api.baseUrl}${user!.profilePhoto}",
                                    width: 90,
                                    height: 90,
                                    fit: BoxFit.cover,
                                    placeholder: (c, u) => const CircularProgressIndicator(strokeWidth: 2),
                                    errorWidget: (c, u, e) => const Icon(Icons.error, size: 45, color: Colors.grey),
                                  ),
                          ),
                        ),
                        const SizedBox(width: 25),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                (user!.firstName.isEmpty && user!.lastName.isEmpty)
                                   ? "No Name"
                                    : "${user!.firstName} ${user!.lastName}".trim(),
                                style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
                              ),
                              const SizedBox(height: 8),
                              Row(
                                children: [
                                  Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text("${user!.followers}",
                                          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                                      const Text("Followers", style: TextStyle(color: Colors.grey)),
                                    ],
                                  ),
                                  const SizedBox(width: 30),
                                  Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text("${user!.following}",
                                          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                                      const Text("Following", style: TextStyle(color: Colors.grey)),
                                    ],
                                  ),
                                ],
                              ),
                            ],
                          ),
                        )
                      ],
                    ),
                  ),
                  const SizedBox(height: 20),
                  if (user!.bio.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Text(user!.bio, style: const TextStyle(fontSize: 14, height: 1.4)),
                    ),
                  const SizedBox(height: 25),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Row(
                      children: [
                        Expanded(
                          child: SizedBox(
                            height: 45,
                            child: ElevatedButton.icon(
                              onPressed: () {
                                ScaffoldMessenger.of(context)
                                   .showSnackBar(SnackBar(content: Text("Coins: ${user!.coin}")));
                              },
                              icon: const Icon(Icons.currency_rupee, size: 20, color: Colors.black87),
                              label: Text("Coins: ${user!.coin}",
                                  style: const TextStyle(
                                      fontSize: 14, fontWeight: FontWeight.bold, color: Colors.black87)),
                              style: ElevatedButton.styleFrom(
                                backgroundColor: Colors.grey.shade200,
                                elevation: 0,
                                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(width: 16),
                        Expanded(
                          child: SizedBox(
                            height: 45,
                            child: ElevatedButton.icon(
                              onPressed: () {
                                Share.share(
                                    "Check out ${user!.username}'s profile 👇\n${Api.baseUrl}/profile/${user!.username}");
                              },
                              icon: const Icon(Icons.share, size: 18, color: Colors.white),
                              label: const Text("Share Profile",
                                  style:
                                      TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white)),
                              style: ElevatedButton.styleFrom(
                                backgroundColor: bgColor,
                                elevation: 0,
                                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 20),
                  const Divider(indent: 16, endIndent: 16),
                  TabBar(
                    controller: _tabController,
                    labelColor: bgColor,
                    unselectedLabelColor: Colors.grey,
                    indicatorColor: bgColor,
                    tabs: [
                      Tab(
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            const Icon(Icons.photo_library, size: 18),
                            const SizedBox(width: 4),
                            Text("${mediaPosts.length} Photos/Videos"),
                          ],
                        ),
                      ),
                      Tab(
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            const Icon(Icons.description, size: 18),
                            const SizedBox(width: 4),
                            Text("${documentPosts.length} Documents"),
                          ],
                        ),
                      ),
                    ],
                  ),
                  SizedBox(
                    height: 600,
                    child: TabBarView(
                      controller: _tabController,
                      children: [
                        // TAB 1: Photos & Videos - CLICK LOGIC ADDED
                        isPostsLoading
                           ? const Center(child: CircularProgressIndicator(color: bgColor))
                            : mediaPosts.isEmpty
                               ? const Center(child: Text("No Photos/Videos Yet"))
                                : GridView.builder(
                                    padding: const EdgeInsets.all(2),
                                    gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                                      crossAxisCount: 3,
                                      crossAxisSpacing: 2,
                                      mainAxisSpacing: 2,
                                      childAspectRatio: 0.75,
                                    ),
                                    itemCount: mediaPosts.length,
                                    itemBuilder: (context, index) {
                                      final post = mediaPosts[index];
                                      return InkWell(
                                        onTap: () => _openSinglePost(post.id.toString()),
                                        child: Column(
                                          children: [
                                            Expanded(
                                              child: Stack(
                                                fit: StackFit.expand,
                                                children: [
                                                  Container(
                                                    decoration: BoxDecoration(
                                                      color: Colors.grey.shade300,
                                                      borderRadius: BorderRadius.circular(4),
                                                    ),
                                                    child: ClipRRect(
                                                      borderRadius: BorderRadius.circular(4),
                                                      child: post.postType == 'video'
                                                         ? VideoFirstFrame(videoUrl: post.firstImageUrl)
                                                          : CachedNetworkImage(
                                                              imageUrl: post.firstImageUrl,
                                                              fit: BoxFit.cover,
                                                              placeholder: (c, u) => Container(color: Colors.grey.shade300),
                                                              errorWidget: (c, u, e) => Container(
                                                                color: Colors.grey.shade300,
                                                                child: const Icon(Icons.error),
                                                              ),
                                                            ),
                                                    ),
                                                  ),
                                                  if (post.postType == 'video')
                                                    const Center(
                                                      child: Icon(Icons.play_circle_fill, size: 40, color: Colors.white),
                                                    ),
                                                ],
                                              ),
                                            ),
                                            const SizedBox(height: 4),
                                            Text(
                                              post.title?? post.content,
                                              maxLines: 1,
                                              overflow: TextOverflow.ellipsis,
                                              style: const TextStyle(fontSize: 11),
                                            ),
                                          ],
                                        ),
                                      );
                                    },
                                  ),

                        // TAB 2: Documents - CLICK LOGIC ADDED
                        isPostsLoading
                           ? const Center(child: CircularProgressIndicator(color: bgColor))
                            : documentPosts.isEmpty
                               ? const Center(child: Text("No Documents Yet"))
                                : GridView.builder(
                                    padding: const EdgeInsets.all(2),
                                    gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                                      crossAxisCount: 3,
                                      crossAxisSpacing: 2,
                                      mainAxisSpacing: 2,
                                      childAspectRatio: 0.75,
                                    ),
                                    itemCount: documentPosts.length,
                                    itemBuilder: (context, index) {
                                      final doc = documentPosts[index];
                                      final file = doc.media.isNotEmpty? doc.media.first : null;
                                      if (file == null) return const SizedBox.shrink();
                                      return InkWell(
                                        onTap: () => _openSinglePost(doc.id.toString()),
                                        child: DocumentGridTile(
                                          doc: doc,
                                          file: file,
                                          onDownload: () => _downloadDocument(file.file, file.fileName),
                                        ),
                                      );
                                    },
                                  ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 40),
                ],
              ),
            ),
            if (isRefreshing)
              Positioned(
                top: 100,
                left: 0,
                right: 0,
                child: Center(
                  child: Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(30),
                      boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.1), blurRadius: 10)],
                    ),
                    child: const CircularProgressIndicator(color: bgColor),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class VideoFirstFrame extends StatefulWidget {
  final String videoUrl;
  const VideoFirstFrame({super.key, required this.videoUrl});
  @override
  State<VideoFirstFrame> createState() => _VideoFirstFrameState();
}

class _VideoFirstFrameState extends State<VideoFirstFrame> {
  late VideoPlayerController _controller;
  bool _initialized = false;
  bool _hasError = false;
  @override
  void initState() {
    super.initState();
    _initializeVideo();
  }

  Future<void> _initializeVideo() async {
    try {
      _controller = VideoPlayerController.networkUrl(
        Uri.parse(widget.videoUrl),
        httpHeaders: {'User-Agent': 'Mozilla/5.0'},
      );
      await _controller.initialize();
      await _controller.seekTo(Duration.zero);
      await _controller.setVolume(0.0);
      await _controller.pause();
      if (mounted) setState(() => _initialized = true);
    } catch (e) {
      if (mounted) setState(() => _hasError = true);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_hasError) {
      return Container(color: Colors.black, child: const Icon(Icons.videocam, size: 50, color: Colors.white54));
    }
    if (!_initialized) {
      return Container(
        color: Colors.black,
        child: const Center(child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2)),
      );
    }
    return AspectRatio(aspectRatio: _controller.value.aspectRatio, child: VideoPlayer(_controller));
  }
}

class DocumentGridTile extends StatefulWidget {
  final PostModel doc;
  final PostMediaModel file;
  final VoidCallback onDownload;
  const DocumentGridTile({super.key, required this.doc, required this.file, required this.onDownload});
  @override
  State<DocumentGridTile> createState() => _DocumentGridTileState();
}

class _DocumentGridTileState extends State<DocumentGridTile> {
  int _totalPages = 0;
  bool _isLoadingPages = true;
  @override
  void initState() {
    super.initState();
    if (widget.file.file.toLowerCase().endsWith('.pdf')) {
      _getPdfPages();
    } else {
      setState(() => _isLoadingPages = false);
    }
  }

  Future<void> _getPdfPages() async {
    try {
      final response = await Dio().get(widget.file.file, options: Options(responseType: ResponseType.bytes));
      final PdfDocument document = PdfDocument(inputBytes: response.data);
      if (mounted) {
        setState(() {
          _totalPages = document.pages.count;
          _isLoadingPages = false;
        });
      }
      document.dispose();
    } catch (e) {
      if (mounted) setState(() => _isLoadingPages = false);
    }
  }

  IconData _getFileIcon() {
    final ext = widget.file.file.toLowerCase();
    if (ext.endsWith('.pdf')) return Icons.picture_as_pdf;
    if (ext.endsWith('.xls') || ext.endsWith('.xlsx')) return Icons.table_chart;
    if (ext.endsWith('.doc') || ext.endsWith('.docx')) return Icons.description;
    return Icons.insert_drive_file;
  }

  Color _getFileColor() {
    final ext = widget.file.file.toLowerCase();
    if (ext.endsWith('.pdf')) return Colors.red;
    if (ext.endsWith('.xls') || ext.endsWith('.xlsx')) return Colors.green;
    if (ext.endsWith('.doc') || ext.endsWith('.docx')) return Colors.blue;
    return Colors.grey;
  }

  @override
  Widget build(BuildContext context) {
    final isPdf = widget.file.file.toLowerCase().endsWith('.pdf');
    return Column(
      children: [
        Expanded(
          child: Container(
            decoration: BoxDecoration(
              color: Colors.grey.shade100,
              borderRadius: BorderRadius.circular(4),
              border: Border.all(color: Colors.grey.shade300),
            ),
            child: Stack(
              fit: StackFit.expand,
              children: [
                if (isPdf)
                  ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: SfPdfViewer.network(
                      widget.file.file,
                      canShowScrollHead: false,
                      canShowPaginationDialog: false,
                      canShowScrollStatus: false,
                      enableDoubleTapZooming: false,
                      pageLayoutMode: PdfPageLayoutMode.single,
                    ),
                  )
                else
                  Center(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(_getFileIcon(), size: 50, color: _getFileColor()),
                        const SizedBox(height: 4),
                        Text(widget.file.file.split('.').last.toUpperCase(),
                            style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: _getFileColor())),
                      ],
                    ),
                  ),
                Positioned(
                  top: 4,
                  right: 4,
                  child: InkWell(
                    onTap: widget.onDownload,
                    child: Container(
                      padding: const EdgeInsets.all(4),
                      decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(4)),
                      child: const Icon(Icons.download, size: 16, color: Colors.white),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 4),
        Text(widget.doc.title?? widget.file.fileName, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 11)),
        if (isPdf)
          Text(_isLoadingPages? 'Loading...' : '$_totalPages pages', style: const TextStyle(fontSize: 9, color: Colors.grey))
        else
          Text('${(widget.file.fileSizeBytes?? 0) / 1024 ~/ 1} KB', style: const TextStyle(fontSize: 9, color: Colors.grey)),
      ],
    );
  }
}

```

---

## 6. `edit_profile.dart` (full code)

```dart
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../api_service.dart';
import '../model.dart';
import '../../utils/api.dart';

class EditProfileScreen extends StatefulWidget {
  final ProfileModel user;

  const EditProfileScreen({super.key, required this.user});

  @override
  State<EditProfileScreen> createState() => _EditProfileScreenState();
}

class _EditProfileScreenState extends State<EditProfileScreen> {
  late TextEditingController _usernameController;
  late TextEditingController _firstNameController;
  late TextEditingController _lastNameController;
  late TextEditingController _bioController;
  
  File? _selectedImage;
  bool _isLoading = false;
  static const bgColor = Color(0xFF030F27);

  @override
  void initState() {
    super.initState();
    _usernameController = TextEditingController(text: widget.user.username);
    _firstNameController = TextEditingController(text: widget.user.firstName);
    _lastNameController = TextEditingController(text: widget.user.lastName);
    _bioController = TextEditingController(text: widget.user.bio);
  }

  Future<void> _pickImage() async {
    final picker = ImagePicker();
    final pickedFile = await picker.pickImage(
      source: ImageSource.gallery,
      imageQuality: 80,
    );
    
    if (pickedFile != null) {
      setState(() {
        _selectedImage = File(pickedFile.path);
      });
    }
  }

  Future<void> _updateProfile() async {
    setState(() => _isLoading = true);

    try {
      await ApiService.updateProfile(
        username: _usernameController.text.trim(),
        firstName: _firstNameController.text.trim(),
        lastName: _lastNameController.text.trim(),
        bio: _bioController.text.trim(),
        profilePhoto: _selectedImage,
      );

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text("Profile updated successfully!"),
            backgroundColor: Colors.green,
          ),
        );
        Navigator.pop(context, true);
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text("Error: $e"),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  @override
  void dispose() {
    _usernameController.dispose();
    _firstNameController.dispose();
    _lastNameController.dispose();
    _bioController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: bgColor,
        title: const Text("Edit Profile", style: TextStyle(color: Colors.white)),
        iconTheme: const IconThemeData(color: Colors.white),
        actions: [
          TextButton(
            onPressed: _isLoading ? null : _updateProfile,
            child: _isLoading
               ? const SizedBox(
                    height: 20,
                    width: 20,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                : const Text(
                    "Save",
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
          ),
        ],
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            const SizedBox(height: 20),
            
            Center(
              child: Stack(
                children: [
                  CircleAvatar(
                    radius: 60,
                    backgroundColor: Colors.grey.shade300,
                    child: ClipOval(
                      child: _selectedImage != null
                         ? Image.file(
                              _selectedImage!,
                              width: 120,
                              height: 120,
                              fit: BoxFit.cover,
                            )
                          : widget.user.profilePhoto.isEmpty
                             ? const Icon(Icons.person, size: 60, color: Colors.grey)
                              : CachedNetworkImage(
                                  imageUrl: widget.user.profilePhoto.startsWith('http')
                                     ? widget.user.profilePhoto
                                      : "${Api.baseUrl}${widget.user.profilePhoto}",
                                  width: 120,
                                  height: 120,
                                  fit: BoxFit.cover,
                                  placeholder: (c, u) =>
                                      const CircularProgressIndicator(strokeWidth: 2),
                                  errorWidget: (c, u, e) =>
                                      const Icon(Icons.error, size: 60, color: Colors.grey),
                                ),
                    ),
                  ),
                  Positioned(
                    bottom: 0,
                    right: 0,
                    child: GestureDetector(
                      onTap: _pickImage,
                      child: Container(
                        padding: const EdgeInsets.all(8),
                        decoration: const BoxDecoration(
                          color: bgColor,
                          shape: BoxShape.circle,
                        ),
                        child: const Icon(
                          Icons.camera_alt,
                          color: Colors.white,
                          size: 20,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),

            const SizedBox(height: 40),

            TextField(
              controller: _usernameController,
              decoration: InputDecoration(
                labelText: "Username",
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
                prefixIcon: const Icon(Icons.alternate_email),
              ),
            ),

            const SizedBox(height: 16),

            TextField(
              controller: _firstNameController,
              decoration: InputDecoration(
                labelText: "First Name",
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
                prefixIcon: const Icon(Icons.person),
              ),
            ),

            const SizedBox(height: 16),

            TextField(
              controller: _lastNameController,
              decoration: InputDecoration(
                labelText: "Last Name",
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
                prefixIcon: const Icon(Icons.person_outline),
              ),
            ),

            const SizedBox(height: 16),

            TextField(
              controller: _bioController,
              maxLines: 4,
              decoration: InputDecoration(
                labelText: "Bio",
                alignLabelWithHint: true,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
                prefixIcon: const Icon(Icons.info_outline),
              ),
            ),
          ],
        ),
      ),
    );
  }
}```

---

## 7. `target_profile.dart` (full code)

```dart


// import 'package:flutter/material.dart';
// import 'package:cached_network_image/cached_network_image.dart';
// import 'package:share_plus/share_plus.dart';
// import 'package:video_player/video_player.dart';
// import 'package:dio/dio.dart';
// import 'package:path_provider/path_provider.dart';
// import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
// import 'package:syncfusion_flutter_pdf/pdf.dart';
// import 'dart:io';

// import '../api_service.dart';
// import '../model.dart';
// import '../../utils/api.dart';
// import '../../post/screens/singlepost.dart';

// class TargetProfilePage extends StatefulWidget {
//   final String username;

//   const TargetProfilePage({Key? key, required this.username}) : super(key: key);

//   @override
//   State<TargetProfilePage> createState() => _TargetProfilePageState();
// }

// class _TargetProfilePageState extends State<TargetProfilePage>
//     with SingleTickerProviderStateMixin {
//   TargetProfileModel? targetUser;
//   List<PostModel> targetPosts = [];
//   List<PostModel> mediaPosts = [];
//   List<PostModel> documentPosts = [];
//   bool isLoading = true;
//   bool isPostsLoading = true;
//   bool isActionLoading = false;
//   String? errorMessage;
//   String? postsError;
//   static const bgColor = Color(0xFF030F27);
//   late TabController _tabController;

//   @override
//   void initState() {
//     super.initState();
//     _tabController = TabController(length: 2, vsync: this);
//     fetchTargetProfile();
//   }

//   @override
//   void dispose() {
//     _tabController.dispose();
//     super.dispose();
//   }

//   Future<void> fetchTargetProfile() async {
//     setState(() {
//       isLoading = true;
//       errorMessage = null;
//     });

//     try {
//       final data = await ApiService.getTargetProfile(widget.username);
//       setState(() {
//         targetUser = data;
//         isLoading = false;
//       });
//       // Profile load hone ke baad posts load karo
//       _loadTargetPosts();
//     } catch (e) {
//       setState(() {
//         errorMessage = e.toString();
//         isLoading = false;
//       });
//     }
//   }

//   Future<void> _loadTargetPosts() async {
//     if (targetUser == null) return;

//     // Private account check - follow nahi kiya to posts mat load karo
//     if (targetUser!.isPrivate && targetUser!.myFollowStatus!= 'ACCEPTED') {
//       setState(() {
//         isPostsLoading = false;
//         postsError = 'PRIVATE_ACCOUNT';
//       });
//       return;
//     }

//     setState(() {
//       isPostsLoading = true;
//       postsError = null;
//     });

//     try {
//       // Yahi pe targetUserId pass kar rahe hain
//       final posts = await ApiService.getTargetUserPosts(targetUser!.targetUserId);
//       if (mounted) {
//         setState(() {
//           targetPosts = posts;
//           mediaPosts = posts
//              .where((p) => p.postType == 'image' || p.postType == 'video')
//              .toList();
//           documentPosts = posts
//              .where((p) => ['document', 'pdf', 'excel', 'docx', 'xls', 'doc']
//                  .contains(p.postType))
//              .toList();
//           isPostsLoading = false;
//         });
//       }
//     } catch (e) {
//       if (mounted) {
//         setState(() {
//           isPostsLoading = false;
//           if (e.toString().contains('PRIVATE_ACCOUNT')) {
//             postsError = 'PRIVATE_ACCOUNT';
//           } else {
//             postsError = e.toString();
//           }
//         });
//       }
//     }
//   }

//   Future<void> handleFollow() async {
//     if (targetUser == null || isActionLoading) return;

//     setState(() => isActionLoading = true);

//     try {
//       final result = await ApiService.followUser(targetUser!.targetUserId);
//       await fetchTargetProfile(); // Refresh karo taaki posts bhi load ho jaye

//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text(result['message']),
//             backgroundColor: Colors.green,
//             duration: const Duration(seconds: 2),
//           ),
//         );
//       }
//     } catch (e) {
//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text('Error: ${e.toString()}'),
//             backgroundColor: Colors.red,
//           ),
//         );
//       }
//     } finally {
//       if (mounted) setState(() => isActionLoading = false);
//     }
//   }

//   Future<void> handleAcceptRequest() async {
//     if (targetUser?.theirFollowId == null || isActionLoading) return;

//     setState(() => isActionLoading = true);

//     try {
//       final result = await ApiService.acceptFollowRequest(targetUser!.theirFollowId!);
//       await fetchTargetProfile();

//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text(result['message']),
//             backgroundColor: Colors.green,
//           ),
//         );
//       }
//     } catch (e) {
//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text('Error: ${e.toString()}'),
//             backgroundColor: Colors.red,
//           ),
//         );
//       }
//     } finally {
//       if (mounted) setState(() => isActionLoading = false);
//     }
//   }

//   Future<void> handleRejectRequest() async {
//     if (targetUser?.theirFollowId == null || isActionLoading) return;

//     setState(() => isActionLoading = true);

//     try {
//       final result = await ApiService.rejectFollowRequest(targetUser!.theirFollowId!);
//       await fetchTargetProfile();

//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text(result['message']),
//             backgroundColor: Colors.orange,
//           ),
//         );
//       }
//     } catch (e) {
//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text('Error: ${e.toString()}'),
//             backgroundColor: Colors.red,
//           ),
//         );
//       }
//     } finally {
//       if (mounted) setState(() => isActionLoading = false);
//     }
//   }

//   Future<void> _downloadDocument(String url, String fileName) async {
//     try {
//       ScaffoldMessenger.of(context).showSnackBar(
//         const SnackBar(content: Text("Downloading...")),
//       );
//       await ApiService.downloadFile(url, fileName);
//       ScaffoldMessenger.of(context).showSnackBar(
//         SnackBar(content: Text("Downloaded: $fileName")),
//       );
//     } catch (e) {
//       ScaffoldMessenger.of(context).showSnackBar(
//         SnackBar(content: Text("Download failed: $e"), backgroundColor: Colors.red),
//       );
//     }
//   }

//   void _openSinglePost(String postId) {
//     Navigator.push(
//       context,
//       MaterialPageRoute(
//         builder: (context) => SinglePostPage(postId: postId),
//       ),
//     );
//   }

//   Widget _buildFollowButton() {
//     if (targetUser == null) return const SizedBox.shrink();

//     // Apni profile pe button mat dikhao
//     if (targetUser!.myId == targetUser!.targetUserId) {
//       return const SizedBox.shrink();
//     }

//     final myStatus = targetUser!.myFollowStatus;
//     final theirStatus = targetUser!.theirFollowStatus;

//     // Case 1: Usne mujhe request bheji hai PENDING
//     if (theirStatus == 'PENDING') {
//       return Column(
//         children: [
//           Row(
//             children: [
//               Expanded(
//                 child: ElevatedButton(
//                   onPressed: isActionLoading? null : handleAcceptRequest,
//                   style: ElevatedButton.styleFrom(
//                     backgroundColor: Colors.blue,
//                     foregroundColor: Colors.white,
//                     padding: const EdgeInsets.symmetric(vertical: 12),
//                     shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
//                   ),
//                   child: isActionLoading
//                      ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
//                       : const Text('Confirm', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
//                 ),
//               ),
//               const SizedBox(width: 8),
//               Expanded(
//                 child: OutlinedButton(
//                   onPressed: isActionLoading? null : handleRejectRequest,
//                   style: OutlinedButton.styleFrom(
//                     padding: const EdgeInsets.symmetric(vertical: 12),
//                     shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
//                     side: BorderSide(color: Colors.grey[400]!),
//                   ),
//                   child: const Text('Delete', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
//                 ),
//               ),
//             ],
//           ),
//           const SizedBox(height: 12),
//           _buildMainFollowButton(),
//         ],
//       );
//     }

//     return _buildMainFollowButton();
//   }

//   Widget _buildMainFollowButton() {
//     final myStatus = targetUser!.myFollowStatus;
//     final theirStatus = targetUser!.theirFollowStatus;

//     String buttonText = 'Follow';
//     Color buttonColor = bgColor;
//     Color textColor = Colors.white;
//     bool isOutlined = false;

//     if (myStatus == 'PENDING') {
//       buttonText = 'Requested';
//       buttonColor = Colors.grey.shade200;
//       textColor = Colors.black87;
//       isOutlined = true;
//     } else if (myStatus == 'ACCEPTED') {
//       buttonText = 'Following';
//       buttonColor = Colors.grey.shade200;
//       textColor = Colors.black87;
//       isOutlined = true;
//     } else if (myStatus == null && theirStatus == 'ACCEPTED') {
//       buttonText = 'Follow Back';
//       buttonColor = bgColor;
//       textColor = Colors.white;
//     }

//     return SizedBox(
//       width: double.infinity,
//       height: 45,
//       child: isOutlined
//          ? OutlinedButton(
//               onPressed: isActionLoading? null : handleFollow,
//               style: OutlinedButton.styleFrom(
//                 backgroundColor: Colors.grey.shade200,
//                 elevation: 0,
//                 shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
//                 side: BorderSide.none,
//               ),
//               child: isActionLoading
//                  ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2))
//                   : Text(buttonText, style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: textColor)),
//             )
//           : ElevatedButton(
//               onPressed: isActionLoading? null : handleFollow,
//               style: ElevatedButton.styleFrom(
//                 backgroundColor: buttonColor,
//                 elevation: 0,
//                 shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
//               ),
//               child: isActionLoading
//                  ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
//                   : Text(buttonText, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white)),
//             ),
//     );
//   }

//   Widget _buildPostsSection() {
//     // Private account hai aur follow nahi kiya
//     if (targetUser!.isPrivate && targetUser!.myFollowStatus!= 'ACCEPTED') {
//       return Container(
//         margin: const EdgeInsets.symmetric(horizontal: 16),
//         padding: const EdgeInsets.all(16),
//         decoration: BoxDecoration(
//           color: Colors.grey[100],
//           borderRadius: BorderRadius.circular(12),
//           border: Border.all(color: Colors.grey[300]!),
//         ),
//         child: Row(
//           children: [
//             Icon(Icons.lock_outline, color: Colors.grey[600]),
//             const SizedBox(width: 12),
//             Expanded(
//               child: Text(
//                 targetUser!.myFollowStatus == 'PENDING'
//                    ? 'Follow request sent. Wait for approval to see posts.'
//                     : 'This account is private. Follow to see their posts.',
//                 style: TextStyle(color: Colors.grey[700]),
//               ),
//             ),
//           ],
//         ),
//       );
//     }

//     // Posts loading ya error
//     if (isPostsLoading) {
//       return const Center(child: CircularProgressIndicator(color: bgColor));
//     }

//     if (postsError!= null && postsError!= 'PRIVATE_ACCOUNT') {
//       return Center(child: Text("Error loading posts: $postsError"));
//     }

//     // Posts dikhao
//     return Column(
//       children: [
//         const Divider(indent: 16, endIndent: 16),
//         TabBar(
//           controller: _tabController,
//           labelColor: bgColor,
//           unselectedLabelColor: Colors.grey,
//           indicatorColor: bgColor,
//           tabs: [
//             Tab(
//               child: Row(
//                 mainAxisAlignment: MainAxisAlignment.center,
//                 children: [
//                   const Icon(Icons.photo_library, size: 18),
//                   const SizedBox(width: 4),
//                   Text("${mediaPosts.length} Photos/Videos"),
//                 ],
//               ),
//             ),
//             Tab(
//               child: Row(
//                 mainAxisAlignment: MainAxisAlignment.center,
//                 children: [
//                   const Icon(Icons.description, size: 18),
//                   const SizedBox(width: 4),
//                   Text("${documentPosts.length} Documents"),
//                 ],
//               ),
//             ),
//           ],
//         ),
//         SizedBox(
//           height: 600,
//           child: TabBarView(
//             controller: _tabController,
//             children: [
//               // TAB 1: Photos & Videos
//               mediaPosts.isEmpty
//                  ? const Center(child: Text("No Photos/Videos Yet"))
//                   : GridView.builder(
//                       padding: const EdgeInsets.all(2),
//                       gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
//                         crossAxisCount: 3,
//                         crossAxisSpacing: 2,
//                         mainAxisSpacing: 2,
//                         childAspectRatio: 0.75,
//                       ),
//                       itemCount: mediaPosts.length,
//                       itemBuilder: (context, index) {
//                         final post = mediaPosts[index];
//                         return InkWell(
//                           onTap: () => _openSinglePost(post.id.toString()),
//                           child: Column(
//                             children: [
//                               Expanded(
//                                 child: Stack(
//                                   fit: StackFit.expand,
//                                   children: [
//                                     Container(
//                                       decoration: BoxDecoration(
//                                         color: Colors.grey.shade300,
//                                         borderRadius: BorderRadius.circular(4),
//                                       ),
//                                       child: ClipRRect(
//                                         borderRadius: BorderRadius.circular(4),
//                                         child: post.postType == 'video'
//                                            ? VideoFirstFrame(videoUrl: post.firstImageUrl)
//                                             : CachedNetworkImage(
//                                                 imageUrl: post.firstImageUrl,
//                                                 fit: BoxFit.cover,
//                                                 placeholder: (c, u) => Container(color: Colors.grey.shade300),
//                                                 errorWidget: (c, u, e) => Container(
//                                                   color: Colors.grey.shade300,
//                                                   child: const Icon(Icons.error),
//                                                 ),
//                                               ),
//                                       ),
//                                     ),
//                                     if (post.postType == 'video')
//                                       const Center(
//                                         child: Icon(Icons.play_circle_fill, size: 40, color: Colors.white),
//                                       ),
//                                   ],
//                                 ),
//                               ),
//                               const SizedBox(height: 4),
//                               Text(
//                                 post.title?? post.content,
//                                 maxLines: 1,
//                                 overflow: TextOverflow.ellipsis,
//                                 style: const TextStyle(fontSize: 11),
//                               ),
//                             ],
//                           ),
//                         );
//                       },
//                     ),

//               // TAB 2: Documents
//               documentPosts.isEmpty
//                  ? const Center(child: Text("No Documents Yet"))
//                   : GridView.builder(
//                       padding: const EdgeInsets.all(2),
//                       gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
//                         crossAxisCount: 3,
//                         crossAxisSpacing: 2,
//                         mainAxisSpacing: 2,
//                         childAspectRatio: 0.75,
//                       ),
//                       itemCount: documentPosts.length,
//                       itemBuilder: (context, index) {
//                         final doc = documentPosts[index];
//                         final file = doc.media.isNotEmpty? doc.media.first : null;
//                         if (file == null) return const SizedBox.shrink();
//                         return InkWell(
//                           onTap: () => _openSinglePost(doc.id.toString()),
//                           child: DocumentGridTile(
//                             doc: doc,
//                             file: file,
//                             onDownload: () => _downloadDocument(file.file, file.fileName),
//                           ),
//                         );
//                       },
//                     ),
//             ],
//           ),
//         ),
//       ],
//     );
//   }

//   @override
//   Widget build(BuildContext context) {
//     return Scaffold(
//       backgroundColor: Colors.white,
//       body: isLoading
//          ? const Center(child: CircularProgressIndicator(color: bgColor))
//           : errorMessage!= null
//              ? Center(
//                   child: Padding(
//                     padding: const EdgeInsets.all(16.0),
//                     child: Column(
//                       mainAxisAlignment: MainAxisAlignment.center,
//                       children: [
//                         const Icon(Icons.error_outline, color: Colors.red, size: 60),
//                         const SizedBox(height: 16),
//                         Text(
//                           errorMessage!,
//                           textAlign: TextAlign.center,
//                           style: const TextStyle(color: Colors.red, fontSize: 16),
//                         ),
//                         const SizedBox(height: 16),
//                         ElevatedButton(
//                           onPressed: fetchTargetProfile,
//                           child: const Text("Retry"),
//                         ),
//                       ],
//                     ),
//                   ),
//                 )
//               : RefreshIndicator(
//                   onRefresh: fetchTargetProfile,
//                   color: bgColor,
//                   child: SingleChildScrollView(
//                     physics: const AlwaysScrollableScrollPhysics(),
//                     child: Column(
//                       crossAxisAlignment: CrossAxisAlignment.start,
//                       children: [
//                         /// ================= HEADER =================
//                         Container(
//                           width: double.infinity,
//                           padding: const EdgeInsets.only(
//                             top: 45,
//                             left: 4,
//                             right: 4,
//                             bottom: 10,
//                           ),
//                           color: bgColor,
//                           child: Row(
//                             children: [
//                               IconButton(
//                                 icon: const Icon(Icons.arrow_back, color: Colors.white),
//                                 onPressed: () => Navigator.maybePop(context),
//                               ),
//                               Expanded(
//                                 child: Center(
//                                   child: Row(
//                                     mainAxisSize: MainAxisSize.min,
//                                     children: [
//                                       Text(
//                                         targetUser!.username,
//                                         style: const TextStyle(
//                                           color: Colors.white,
//                                           fontSize: 20,
//                                           fontWeight: FontWeight.bold,
//                                         ),
//                                       ),
//                                       if (targetUser!.isVerified)...[
//                                         const SizedBox(width: 4),
//                                         const Icon(Icons.verified, color: Colors.blue, size: 18),
//                                       ]
//                                     ],
//                                   ),
//                                 ),
//                               ),
//                               IconButton(
//                                 icon: const Icon(Icons.more_vert, color: Colors.white),
//                                 onPressed: () {
//                                   ScaffoldMessenger.of(context).showSnackBar(
//                                     const SnackBar(content: Text("More Options")),
//                                   );
//                                 },
//                               ),
//                             ],
//                           ),
//                         ),

//                         const SizedBox(height: 20),

//                         /// ================= PROFILE INFO =================
//                         Padding(
//                           padding: const EdgeInsets.symmetric(horizontal: 16),
//                           child: Row(
//                             children: [
//                               CircleAvatar(
//                                 radius: 45,
//                                 backgroundColor: Colors.grey.shade300,
//                                 child: ClipOval(
//                                   child: targetUser!.profilePhoto.isEmpty
//                                      ? const Icon(Icons.person, size: 45, color: Colors.grey)
//                                       : CachedNetworkImage(
//                                           imageUrl: targetUser!.profilePhoto.startsWith('http')
//                                              ? targetUser!.profilePhoto
//                                               : "${Api.baseUrl}${targetUser!.profilePhoto}",
//                                           width: 90,
//                                           height: 90,
//                                           fit: BoxFit.cover,
//                                           placeholder: (c, u) =>
//                                               const CircularProgressIndicator(strokeWidth: 2),
//                                           errorWidget: (c, u, e) =>
//                                               const Icon(Icons.error, size: 45, color: Colors.grey),
//                                         ),
//                                 ),
//                               ),
//                               const SizedBox(width: 25),
//                               Expanded(
//                                 child: Column(
//                                   crossAxisAlignment: CrossAxisAlignment.start,
//                                   children: [
//                                     Row(
//                                       children: [
//                                         Expanded(
//                                           child: Text(
//                                             (targetUser!.firstName.isEmpty && targetUser!.lastName.isEmpty)
//                                                ? "No Name"
//                                                 : "${targetUser!.firstName} ${targetUser!.lastName}".trim(),
//                                             style: const TextStyle(
//                                               fontSize: 22,
//                                               fontWeight: FontWeight.bold,
//                                             ),
//                                           ),
//                                         ),
//                                         if (targetUser!.isPrivate)...[
//                                           const SizedBox(width: 6),
//                                           Icon(Icons.lock, size: 18, color: Colors.grey[600]),
//                                         ],
//                                       ],
//                                     ),
//                                     const SizedBox(height: 8),
//                                     Row(
//                                       children: [
//                                         Column(
//                                           crossAxisAlignment: CrossAxisAlignment.start,
//                                           children: [
//                                             Text(
//                                               "${targetUser!.followers}",
//                                               style: const TextStyle(
//                                                 fontSize: 16,
//                                                 fontWeight: FontWeight.bold,
//                                               ),
//                                             ),
//                                             const Text("Followers", style: TextStyle(color: Colors.grey)),
//                                           ],
//                                         ),
//                                         const SizedBox(width: 30),
//                                         Column(
//                                           crossAxisAlignment: CrossAxisAlignment.start,
//                                           children: [
//                                             Text(
//                                               "${targetUser!.following}",
//                                               style: const TextStyle(
//                                                 fontSize: 16,
//                                                 fontWeight: FontWeight.bold,
//                                               ),
//                                             ),
//                                             const Text("Following", style: TextStyle(color: Colors.grey)),
//                                           ],
//                                         ),
//                                       ],
//                                     ),
//                                   ],
//                                 ),
//                               )
//                             ],
//                           ),
//                         ),

//                         const SizedBox(height: 20),

//                         /// ================= BIO =================
//                         if (targetUser!.bio.isNotEmpty)
//                           Padding(
//                             padding: const EdgeInsets.symmetric(horizontal: 16),
//                             child: Text(
//                               targetUser!.bio,
//                               style: const TextStyle(fontSize: 14, height: 1.4),
//                             ),
//                           ),

//                         const SizedBox(height: 25),

//                         /// ================= ACTION BUTTONS =================
//                         Padding(
//                           padding: const EdgeInsets.symmetric(horizontal: 16),
//                           child: Column(
//                             children: [
//                               _buildFollowButton(),
//                               const SizedBox(height: 12),
//                               if (targetUser!.myId!= targetUser!.targetUserId)...[
//                                 Row(
//                                   children: [
//                                     Expanded(
//                                       child: SizedBox(
//                                         height: 45,
//                                         child: ElevatedButton.icon(
//                                           onPressed: () {
//                                             ScaffoldMessenger.of(context).showSnackBar(
//                                               const SnackBar(content: Text("Message feature coming soon")),
//                                             );
//                                           },
//                                           icon: const Icon(Icons.message, size: 18, color: Colors.black87),
//                                           label: const Text(
//                                             "Message",
//                                             style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.black87),
//                                           ),
//                                           style: ElevatedButton.styleFrom(
//                                             backgroundColor: Colors.grey.shade200,
//                                             elevation: 0,
//                                             shape: RoundedRectangleBorder(
//                                               borderRadius: BorderRadius.circular(8),
//                                             ),
//                                           ),
//                                         ),
//                                       ),
//                                     ),
//                                     const SizedBox(width: 12),
//                                     Expanded(
//                                       child: SizedBox(
//                                         height: 45,
//                                         child: ElevatedButton.icon(
//                                           onPressed: () {
//                                             Share.share(
//                                               "Check out ${targetUser!.username}'s profile 👇\n"
//                                               "${Api.baseUrl}/profile/${targetUser!.username}",
//                                             );
//                                           },
//                                           icon: const Icon(Icons.share, size: 18, color: Colors.white),
//                                           label: const Text(
//                                             "Share Profile",
//                                             style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white),
//                                           ),
//                                           style: ElevatedButton.styleFrom(
//                                             backgroundColor: bgColor,
//                                             elevation: 0,
//                                             shape: RoundedRectangleBorder(
//                                               borderRadius: BorderRadius.circular(8),
//                                             ),
//                                           ),
//                                         ),
//                                       ),
//                                     ),
//                                   ],
//                                 ),
//                               ],
//                             ],
//                           ),
//                         ),

//                         const SizedBox(height: 20),

//                         /// ================= POSTS SECTION =================
//                         _buildPostsSection(),

//                         const SizedBox(height: 40),
//                       ],
//                     ),
//                   ),
//                 ),
//     );
//   }
// }

// // VideoFirstFrame aur DocumentGridTile classes - profile.dart se copy kar lo
// // Ye same rahenge

// class VideoFirstFrame extends StatefulWidget {
//   final String videoUrl;
//   const VideoFirstFrame({super.key, required this.videoUrl});
//   @override
//   State<VideoFirstFrame> createState() => _VideoFirstFrameState();
// }

// class _VideoFirstFrameState extends State<VideoFirstFrame> {
//   late VideoPlayerController _controller;
//   bool _initialized = false;
//   bool _hasError = false;
//   @override
//   void initState() {
//     super.initState();
//     _initializeVideo();
//   }

//   Future<void> _initializeVideo() async {
//     try {
//       _controller = VideoPlayerController.networkUrl(
//         Uri.parse(widget.videoUrl),
//         httpHeaders: {'User-Agent': 'Mozilla/5.0'},
//       );
//       await _controller.initialize();
//       await _controller.seekTo(Duration.zero);
//       await _controller.setVolume(0.0);
//       await _controller.pause();
//       if (mounted) setState(() => _initialized = true);
//     } catch (e) {
//       if (mounted) setState(() => _hasError = true);
//     }
//   }

//   @override
//   void dispose() {
//     _controller.dispose();
//     super.dispose();
//   }

//   @override
//   Widget build(BuildContext context) {
//     if (_hasError) {
//       return Container(color: Colors.black, child: const Icon(Icons.videocam, size: 50, color: Colors.white54));
//     }
//     if (!_initialized) {
//       return Container(
//         color: Colors.black,
//         child: const Center(child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2)),
//       );
//     }
//     return AspectRatio(aspectRatio: _controller.value.aspectRatio, child: VideoPlayer(_controller));
//   }
// }

// class DocumentGridTile extends StatefulWidget {
//   final PostModel doc;
//   final PostMediaModel file;
//   final VoidCallback onDownload;
//   const DocumentGridTile({super.key, required this.doc, required this.file, required this.onDownload});
//   @override
//   State<DocumentGridTile> createState() => _DocumentGridTileState();
// }

// class _DocumentGridTileState extends State<DocumentGridTile> {
//   int _totalPages = 0;
//   bool _isLoadingPages = true;
//   @override
//   void initState() {
//     super.initState();
//     if (widget.file.file.toLowerCase().endsWith('.pdf')) {
//       _getPdfPages();
//     } else {
//       setState(() => _isLoadingPages = false);
//     }
//   }

//   Future<void> _getPdfPages() async {
//     try {
//       final response = await Dio().get(widget.file.file, options: Options(responseType: ResponseType.bytes));
//       final PdfDocument document = PdfDocument(inputBytes: response.data);
//       if (mounted) {
//         setState(() {
//           _totalPages = document.pages.count;
//           _isLoadingPages = false;
//         });
//       }
//       document.dispose();
//     } catch (e) {
//       if (mounted) setState(() => _isLoadingPages = false);
//     }
//   }

//   IconData _getFileIcon() {
//     final ext = widget.file.file.toLowerCase();
//     if (ext.endsWith('.pdf')) return Icons.picture_as_pdf;
//     if (ext.endsWith('.xls') || ext.endsWith('.xlsx')) return Icons.table_chart;
//     if (ext.endsWith('.doc') || ext.endsWith('.docx')) return Icons.description;
//     return Icons.insert_drive_file;
//   }

//   Color _getFileColor() {
//     final ext = widget.file.file.toLowerCase();
//     if (ext.endsWith('.pdf')) return Colors.red;
//     if (ext.endsWith('.xls') || ext.endsWith('.xlsx')) return Colors.green;
//     if (ext.endsWith('.doc') || ext.endsWith('.docx')) return Colors.blue;
//     return Colors.grey;
//   }

//   @override
//   Widget build(BuildContext context) {
//     final isPdf = widget.file.file.toLowerCase().endsWith('.pdf');
//     return Column(
//       children: [
//         Expanded(
//           child: Container(
//             decoration: BoxDecoration(
//               color: Colors.grey.shade100,
//               borderRadius: BorderRadius.circular(4),
//               border: Border.all(color: Colors.grey.shade300),
//             ),
//             child: Stack(
//               fit: StackFit.expand,
//               children: [
//                 if (isPdf)
//                   ClipRRect(
//                     borderRadius: BorderRadius.circular(4),
//                     child: SfPdfViewer.network(
//                       widget.file.file,
//                       canShowScrollHead: false,
//                       canShowPaginationDialog: false,
//                       canShowScrollStatus: false,
//                       enableDoubleTapZooming: false,
//                       pageLayoutMode: PdfPageLayoutMode.single,
//                     ),
//                   )
//                 else
//                   Center(
//                     child: Column(
//                       mainAxisAlignment: MainAxisAlignment.center,
//                       children: [
//                         Icon(_getFileIcon(), size: 50, color: _getFileColor()),
//                         const SizedBox(height: 4),
//                         Text(widget.file.file.split('.').last.toUpperCase(),
//                             style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: _getFileColor())),
//                       ],
//                     ),
//                   ),
//                 Positioned(
//                   top: 4,
//                   right: 4,
//                   child: InkWell(
//                     onTap: widget.onDownload,
//                     child: Container(
//                       padding: const EdgeInsets.all(4),
//                       decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(4)),
//                       child: const Icon(Icons.download, size: 16, color: Colors.white),
//                     ),
//                   ),
//                 ),
//               ],
//             ),
//           ),
//         ),
//         const SizedBox(height: 4),
//         Text(widget.doc.title?? widget.file.fileName, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 11)),
//         if (isPdf)
//           Text(_isLoadingPages? 'Loading...' : '$_totalPages pages', style: const TextStyle(fontSize: 9, color: Colors.grey))
//         else
//           Text('${(widget.file.fileSizeBytes?? 0) / 1024 ~/ 1} KB', style: const TextStyle(fontSize: 9, color: Colors.grey)),
//       ],
//     );
//   }
// }




































import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:share_plus/share_plus.dart';
import 'package:video_player/video_player.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:syncfusion_flutter_pdf/pdf.dart';
import 'dart:io';

import '../api_service.dart';
import '../model.dart';
import '../../utils/api.dart';
import '../../post/screens/singlepost.dart';
import '../../message/services/message_api_service.dart';
import '../../message/screens/chat_screen.dart';

class TargetProfilePage extends StatefulWidget {
  final String username;

  const TargetProfilePage({Key? key, required this.username}) : super(key: key);

  @override
  State<TargetProfilePage> createState() => _TargetProfilePageState();
}

class _TargetProfilePageState extends State<TargetProfilePage>
    with SingleTickerProviderStateMixin {
  TargetProfileModel? targetUser;
  List<PostModel> targetPosts = [];
  List<PostModel> mediaPosts = [];
  List<PostModel> documentPosts = [];
  bool isLoading = true;
  bool isPostsLoading = true;
  bool isActionLoading = false;
  String? errorMessage;
  String? postsError;
  static const bgColor = Color(0xFF030F27);
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
    fetchTargetProfile();
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  Future<void> fetchTargetProfile() async {
    setState(() {
      isLoading = true;
      errorMessage = null;
    });

    try {
      final data = await ApiService.getTargetProfile(widget.username);
      setState(() {
        targetUser = data;
        isLoading = false;
      });
      // Profile load hone ke baad posts load karo
      _loadTargetPosts();
    } catch (e) {
      setState(() {
        errorMessage = e.toString();
        isLoading = false;
      });
    }
  }

  Future<void> _loadTargetPosts() async {
    if (targetUser == null) return;

    // Private account check - follow nahi kiya to posts mat load karo
    if (targetUser!.isPrivate && targetUser!.myFollowStatus!= 'ACCEPTED') {
      setState(() {
        isPostsLoading = false;
        postsError = 'PRIVATE_ACCOUNT';
      });
      return;
    }

    setState(() {
      isPostsLoading = true;
      postsError = null;
    });

    try {
      // Yahi pe targetUserId pass kar rahe hain
      final posts = await ApiService.getTargetUserPosts(targetUser!.targetUserId);
      if (mounted) {
        setState(() {
          targetPosts = posts;
          mediaPosts = posts
             .where((p) => p.postType == 'image' || p.postType == 'video')
             .toList();
          documentPosts = posts
             .where((p) => ['document', 'pdf', 'excel', 'docx', 'xls', 'doc']
                 .contains(p.postType))
             .toList();
          isPostsLoading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          isPostsLoading = false;
          if (e.toString().contains('PRIVATE_ACCOUNT')) {
            postsError = 'PRIVATE_ACCOUNT';
          } else {
            postsError = e.toString();
          }
        });
      }
    }
  }

  // 🔥 Target user ke sath seedha chat kholna — pehle se conversation ho to wahi khulegi,
  // na ho to backend nayi bana dega (get-or-create pattern)
  Future<void> _openChatWithUser() async {
    if (targetUser == null || isActionLoading) return;

    setState(() => isActionLoading = true);

    try {
      final conversation = await MessageApiService.getOrCreateConversation(
        targetUser!.targetUserId.toString(),
      );

      if (mounted) {
        Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => ChatScreen(conversation: conversation)),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Chat open nahi ho paayi: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => isActionLoading = false);
    }
  }

  Future<void> handleFollow() async {
    if (targetUser == null || isActionLoading) return;

    setState(() => isActionLoading = true);

    try {
      final result = await ApiService.followUser(targetUser!.targetUserId);
      await fetchTargetProfile(); // Refresh karo taaki posts bhi load ho jaye

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(result['message']),
            backgroundColor: Colors.green,
            duration: const Duration(seconds: 2),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error: ${e.toString()}'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => isActionLoading = false);
    }
  }

  Future<void> handleAcceptRequest() async {
    if (targetUser?.theirFollowId == null || isActionLoading) return;

    setState(() => isActionLoading = true);

    try {
      final result = await ApiService.acceptFollowRequest(targetUser!.theirFollowId!);
      await fetchTargetProfile();

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(result['message']),
            backgroundColor: Colors.green,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error: ${e.toString()}'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => isActionLoading = false);
    }
  }

  Future<void> handleRejectRequest() async {
    if (targetUser?.theirFollowId == null || isActionLoading) return;

    setState(() => isActionLoading = true);

    try {
      final result = await ApiService.rejectFollowRequest(targetUser!.theirFollowId!);
      await fetchTargetProfile();

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(result['message']),
            backgroundColor: Colors.orange,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error: ${e.toString()}'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => isActionLoading = false);
    }
  }

  Future<void> _downloadDocument(String url, String fileName) async {
    try {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Downloading...")),
      );
      await ApiService.downloadFile(url, fileName);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Downloaded: $fileName")),
      );
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Download failed: $e"), backgroundColor: Colors.red),
      );
    }
  }

  void _openSinglePost(String postId) {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => SinglePostPage(postId: postId),
      ),
    );
  }

  Widget _buildFollowButton() {
    if (targetUser == null) return const SizedBox.shrink();

    // Apni profile pe button mat dikhao
    if (targetUser!.myId == targetUser!.targetUserId) {
      return const SizedBox.shrink();
    }

    final myStatus = targetUser!.myFollowStatus;
    final theirStatus = targetUser!.theirFollowStatus;

    // Case 1: Usne mujhe request bheji hai PENDING
    if (theirStatus == 'PENDING') {
      return Column(
        children: [
          Row(
            children: [
              Expanded(
                child: ElevatedButton(
                  onPressed: isActionLoading? null : handleAcceptRequest,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.blue,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                  ),
                  child: isActionLoading
                     ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Text('Confirm', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton(
                  onPressed: isActionLoading? null : handleRejectRequest,
                  style: OutlinedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                    side: BorderSide(color: Colors.grey[400]!),
                  ),
                  child: const Text('Delete', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          _buildMainFollowButton(),
        ],
      );
    }

    return _buildMainFollowButton();
  }

  Widget _buildMainFollowButton() {
    final myStatus = targetUser!.myFollowStatus;
    final theirStatus = targetUser!.theirFollowStatus;

    String buttonText = 'Follow';
    Color buttonColor = bgColor;
    Color textColor = Colors.white;
    bool isOutlined = false;

    if (myStatus == 'PENDING') {
      buttonText = 'Requested';
      buttonColor = Colors.grey.shade200;
      textColor = Colors.black87;
      isOutlined = true;
    } else if (myStatus == 'ACCEPTED') {
      buttonText = 'Following';
      buttonColor = Colors.grey.shade200;
      textColor = Colors.black87;
      isOutlined = true;
    } else if (myStatus == null && theirStatus == 'ACCEPTED') {
      buttonText = 'Follow Back';
      buttonColor = bgColor;
      textColor = Colors.white;
    }

    return SizedBox(
      width: double.infinity,
      height: 45,
      child: isOutlined
         ? OutlinedButton(
              onPressed: isActionLoading? null : handleFollow,
              style: OutlinedButton.styleFrom(
                backgroundColor: Colors.grey.shade200,
                elevation: 0,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                side: BorderSide.none,
              ),
              child: isActionLoading
                 ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2))
                  : Text(buttonText, style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: textColor)),
            )
          : ElevatedButton(
              onPressed: isActionLoading? null : handleFollow,
              style: ElevatedButton.styleFrom(
                backgroundColor: buttonColor,
                elevation: 0,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
              ),
              child: isActionLoading
                 ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : Text(buttonText, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white)),
            ),
    );
  }

  Widget _buildPostsSection() {
    // Private account hai aur follow nahi kiya
    if (targetUser!.isPrivate && targetUser!.myFollowStatus!= 'ACCEPTED') {
      return Container(
        margin: const EdgeInsets.symmetric(horizontal: 16),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Colors.grey[100],
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: Colors.grey[300]!),
        ),
        child: Row(
          children: [
            Icon(Icons.lock_outline, color: Colors.grey[600]),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                targetUser!.myFollowStatus == 'PENDING'
                   ? 'Follow request sent. Wait for approval to see posts.'
                    : 'This account is private. Follow to see their posts.',
                style: TextStyle(color: Colors.grey[700]),
              ),
            ),
          ],
        ),
      );
    }

    // Posts loading ya error
    if (isPostsLoading) {
      return const Center(child: CircularProgressIndicator(color: bgColor));
    }

    if (postsError!= null && postsError!= 'PRIVATE_ACCOUNT') {
      return Center(child: Text("Error loading posts: $postsError"));
    }

    // Posts dikhao
    return Column(
      children: [
        const Divider(indent: 16, endIndent: 16),
        TabBar(
          controller: _tabController,
          labelColor: bgColor,
          unselectedLabelColor: Colors.grey,
          indicatorColor: bgColor,
          tabs: [
            Tab(
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Icon(Icons.photo_library, size: 18),
                  const SizedBox(width: 4),
                  Text("${mediaPosts.length} Photos/Videos"),
                ],
              ),
            ),
            Tab(
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Icon(Icons.description, size: 18),
                  const SizedBox(width: 4),
                  Text("${documentPosts.length} Documents"),
                ],
              ),
            ),
          ],
        ),
        SizedBox(
          height: 600,
          child: TabBarView(
            controller: _tabController,
            children: [
              // TAB 1: Photos & Videos
              mediaPosts.isEmpty
                 ? const Center(child: Text("No Photos/Videos Yet"))
                  : GridView.builder(
                      padding: const EdgeInsets.all(2),
                      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                        crossAxisCount: 3,
                        crossAxisSpacing: 2,
                        mainAxisSpacing: 2,
                        childAspectRatio: 0.75,
                      ),
                      itemCount: mediaPosts.length,
                      itemBuilder: (context, index) {
                        final post = mediaPosts[index];
                        return InkWell(
                          onTap: () => _openSinglePost(post.id.toString()),
                          child: Column(
                            children: [
                              Expanded(
                                child: Stack(
                                  fit: StackFit.expand,
                                  children: [
                                    Container(
                                      decoration: BoxDecoration(
                                        color: Colors.grey.shade300,
                                        borderRadius: BorderRadius.circular(4),
                                      ),
                                      child: ClipRRect(
                                        borderRadius: BorderRadius.circular(4),
                                        child: post.postType == 'video'
                                           ? VideoFirstFrame(videoUrl: post.firstImageUrl)
                                            : CachedNetworkImage(
                                                imageUrl: post.firstImageUrl,
                                                fit: BoxFit.cover,
                                                placeholder: (c, u) => Container(color: Colors.grey.shade300),
                                                errorWidget: (c, u, e) => Container(
                                                  color: Colors.grey.shade300,
                                                  child: const Icon(Icons.error),
                                                ),
                                              ),
                                      ),
                                    ),
                                    if (post.postType == 'video')
                                      const Center(
                                        child: Icon(Icons.play_circle_fill, size: 40, color: Colors.white),
                                      ),
                                  ],
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                post.title?? post.content,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(fontSize: 11),
                              ),
                            ],
                          ),
                        );
                      },
                    ),

              // TAB 2: Documents
              documentPosts.isEmpty
                 ? const Center(child: Text("No Documents Yet"))
                  : GridView.builder(
                      padding: const EdgeInsets.all(2),
                      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                        crossAxisCount: 3,
                        crossAxisSpacing: 2,
                        mainAxisSpacing: 2,
                        childAspectRatio: 0.75,
                      ),
                      itemCount: documentPosts.length,
                      itemBuilder: (context, index) {
                        final doc = documentPosts[index];
                        final file = doc.media.isNotEmpty? doc.media.first : null;
                        if (file == null) return const SizedBox.shrink();
                        return InkWell(
                          onTap: () => _openSinglePost(doc.id.toString()),
                          child: DocumentGridTile(
                            doc: doc,
                            file: file,
                            onDownload: () => _downloadDocument(file.file, file.fileName),
                          ),
                        );
                      },
                    ),
            ],
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: isLoading
         ? const Center(child: CircularProgressIndicator(color: bgColor))
          : errorMessage!= null
             ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(16.0),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        const Icon(Icons.error_outline, color: Colors.red, size: 60),
                        const SizedBox(height: 16),
                        Text(
                          errorMessage!,
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: Colors.red, fontSize: 16),
                        ),
                        const SizedBox(height: 16),
                        ElevatedButton(
                          onPressed: fetchTargetProfile,
                          child: const Text("Retry"),
                        ),
                      ],
                    ),
                  ),
                )
              : RefreshIndicator(
                  onRefresh: fetchTargetProfile,
                  color: bgColor,
                  child: SingleChildScrollView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        /// ================= HEADER =================
                        Container(
                          width: double.infinity,
                          padding: const EdgeInsets.only(
                            top: 45,
                            left: 4,
                            right: 4,
                            bottom: 10,
                          ),
                          color: bgColor,
                          child: Row(
                            children: [
                              IconButton(
                                icon: const Icon(Icons.arrow_back, color: Colors.white),
                                onPressed: () => Navigator.maybePop(context),
                              ),
                              Expanded(
                                child: Center(
                                  child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Text(
                                        targetUser!.username,
                                        style: const TextStyle(
                                          color: Colors.white,
                                          fontSize: 20,
                                          fontWeight: FontWeight.bold,
                                        ),
                                      ),
                                      if (targetUser!.isVerified)...[
                                        const SizedBox(width: 4),
                                        const Icon(Icons.verified, color: Colors.blue, size: 18),
                                      ]
                                    ],
                                  ),
                                ),
                              ),
                              IconButton(
                                icon: const Icon(Icons.more_vert, color: Colors.white),
                                onPressed: () {
                                  ScaffoldMessenger.of(context).showSnackBar(
                                    const SnackBar(content: Text("More Options")),
                                  );
                                },
                              ),
                            ],
                          ),
                        ),

                        const SizedBox(height: 20),

                        /// ================= PROFILE INFO =================
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 16),
                          child: Row(
                            children: [
                              CircleAvatar(
                                radius: 45,
                                backgroundColor: Colors.grey.shade300,
                                child: ClipOval(
                                  child: targetUser!.profilePhoto.isEmpty
                                     ? const Icon(Icons.person, size: 45, color: Colors.grey)
                                      : CachedNetworkImage(
                                          imageUrl: targetUser!.profilePhoto.startsWith('http')
                                             ? targetUser!.profilePhoto
                                              : "${Api.baseUrl}${targetUser!.profilePhoto}",
                                          width: 90,
                                          height: 90,
                                          fit: BoxFit.cover,
                                          placeholder: (c, u) =>
                                              const CircularProgressIndicator(strokeWidth: 2),
                                          errorWidget: (c, u, e) =>
                                              const Icon(Icons.error, size: 45, color: Colors.grey),
                                        ),
                                ),
                              ),
                              const SizedBox(width: 25),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Row(
                                      children: [
                                        Expanded(
                                          child: Text(
                                            (targetUser!.firstName.isEmpty && targetUser!.lastName.isEmpty)
                                               ? "No Name"
                                                : "${targetUser!.firstName} ${targetUser!.lastName}".trim(),
                                            style: const TextStyle(
                                              fontSize: 22,
                                              fontWeight: FontWeight.bold,
                                            ),
                                          ),
                                        ),
                                        if (targetUser!.isPrivate)...[
                                          const SizedBox(width: 6),
                                          Icon(Icons.lock, size: 18, color: Colors.grey[600]),
                                        ],
                                      ],
                                    ),
                                    const SizedBox(height: 8),
                                    Row(
                                      children: [
                                        Column(
                                          crossAxisAlignment: CrossAxisAlignment.start,
                                          children: [
                                            Text(
                                              "${targetUser!.followers}",
                                              style: const TextStyle(
                                                fontSize: 16,
                                                fontWeight: FontWeight.bold,
                                              ),
                                            ),
                                            const Text("Followers", style: TextStyle(color: Colors.grey)),
                                          ],
                                        ),
                                        const SizedBox(width: 30),
                                        Column(
                                          crossAxisAlignment: CrossAxisAlignment.start,
                                          children: [
                                            Text(
                                              "${targetUser!.following}",
                                              style: const TextStyle(
                                                fontSize: 16,
                                                fontWeight: FontWeight.bold,
                                              ),
                                            ),
                                            const Text("Following", style: TextStyle(color: Colors.grey)),
                                          ],
                                        ),
                                      ],
                                    ),
                                  ],
                                ),
                              )
                            ],
                          ),
                        ),

                        const SizedBox(height: 20),

                        /// ================= BIO =================
                        if (targetUser!.bio.isNotEmpty)
                          Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 16),
                            child: Text(
                              targetUser!.bio,
                              style: const TextStyle(fontSize: 14, height: 1.4),
                            ),
                          ),

                        const SizedBox(height: 25),

                        /// ================= ACTION BUTTONS =================
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 16),
                          child: Column(
                            children: [
                              _buildFollowButton(),
                              const SizedBox(height: 12),
                              if (targetUser!.myId!= targetUser!.targetUserId)...[
                                Row(
                                  children: [
                                    Expanded(
                                      child: SizedBox(
                                        height: 45,
                                        child: ElevatedButton.icon(
                                          onPressed: isActionLoading ? null : _openChatWithUser,
                                          icon: isActionLoading
                                              ? const SizedBox(
                                                  height: 16,
                                                  width: 16,
                                                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.black87),
                                                )
                                              : const Icon(Icons.message, size: 18, color: Colors.black87),
                                          label: const Text(
                                            "Message",
                                            style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.black87),
                                          ),
                                          style: ElevatedButton.styleFrom(
                                            backgroundColor: Colors.grey.shade200,
                                            elevation: 0,
                                            shape: RoundedRectangleBorder(
                                              borderRadius: BorderRadius.circular(8),
                                            ),
                                          ),
                                        ),
                                      ),
                                    ),
                                    const SizedBox(width: 12),
                                    Expanded(
                                      child: SizedBox(
                                        height: 45,
                                        child: ElevatedButton.icon(
                                          onPressed: () {
                                            Share.share(
                                              "Check out ${targetUser!.username}'s profile 👇\n"
                                              "${Api.baseUrl}/profile/${targetUser!.username}",
                                            );
                                          },
                                          icon: const Icon(Icons.share, size: 18, color: Colors.white),
                                          label: const Text(
                                            "Share Profile",
                                            style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white),
                                          ),
                                          style: ElevatedButton.styleFrom(
                                            backgroundColor: bgColor,
                                            elevation: 0,
                                            shape: RoundedRectangleBorder(
                                              borderRadius: BorderRadius.circular(8),
                                            ),
                                          ),
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                              ],
                            ],
                          ),
                        ),

                        const SizedBox(height: 20),

                        /// ================= POSTS SECTION =================
                        _buildPostsSection(),

                        const SizedBox(height: 40),
                      ],
                    ),
                  ),
                ),
    );
  }
}

// VideoFirstFrame aur DocumentGridTile classes - profile.dart se copy kar lo
// Ye same rahenge

class VideoFirstFrame extends StatefulWidget {
  final String videoUrl;
  const VideoFirstFrame({super.key, required this.videoUrl});
  @override
  State<VideoFirstFrame> createState() => _VideoFirstFrameState();
}

class _VideoFirstFrameState extends State<VideoFirstFrame> {
  late VideoPlayerController _controller;
  bool _initialized = false;
  bool _hasError = false;
  @override
  void initState() {
    super.initState();
    _initializeVideo();
  }

  Future<void> _initializeVideo() async {
    try {
      _controller = VideoPlayerController.networkUrl(
        Uri.parse(widget.videoUrl),
        httpHeaders: {'User-Agent': 'Mozilla/5.0'},
      );
      await _controller.initialize();
      await _controller.seekTo(Duration.zero);
      await _controller.setVolume(0.0);
      await _controller.pause();
      if (mounted) setState(() => _initialized = true);
    } catch (e) {
      if (mounted) setState(() => _hasError = true);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_hasError) {
      return Container(color: Colors.black, child: const Icon(Icons.videocam, size: 50, color: Colors.white54));
    }
    if (!_initialized) {
      return Container(
        color: Colors.black,
        child: const Center(child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2)),
      );
    }
    return AspectRatio(aspectRatio: _controller.value.aspectRatio, child: VideoPlayer(_controller));
  }
}

class DocumentGridTile extends StatefulWidget {
  final PostModel doc;
  final PostMediaModel file;
  final VoidCallback onDownload;
  const DocumentGridTile({super.key, required this.doc, required this.file, required this.onDownload});
  @override
  State<DocumentGridTile> createState() => _DocumentGridTileState();
}

class _DocumentGridTileState extends State<DocumentGridTile> {
  int _totalPages = 0;
  bool _isLoadingPages = true;
  @override
  void initState() {
    super.initState();
    if (widget.file.file.toLowerCase().endsWith('.pdf')) {
      _getPdfPages();
    } else {
      setState(() => _isLoadingPages = false);
    }
  }

  Future<void> _getPdfPages() async {
    try {
      final response = await Dio().get(widget.file.file, options: Options(responseType: ResponseType.bytes));
      final PdfDocument document = PdfDocument(inputBytes: response.data);
      if (mounted) {
        setState(() {
          _totalPages = document.pages.count;
          _isLoadingPages = false;
        });
      }
      document.dispose();
    } catch (e) {
      if (mounted) setState(() => _isLoadingPages = false);
    }
  }

  IconData _getFileIcon() {
    final ext = widget.file.file.toLowerCase();
    if (ext.endsWith('.pdf')) return Icons.picture_as_pdf;
    if (ext.endsWith('.xls') || ext.endsWith('.xlsx')) return Icons.table_chart;
    if (ext.endsWith('.doc') || ext.endsWith('.docx')) return Icons.description;
    return Icons.insert_drive_file;
  }

  Color _getFileColor() {
    final ext = widget.file.file.toLowerCase();
    if (ext.endsWith('.pdf')) return Colors.red;
    if (ext.endsWith('.xls') || ext.endsWith('.xlsx')) return Colors.green;
    if (ext.endsWith('.doc') || ext.endsWith('.docx')) return Colors.blue;
    return Colors.grey;
  }

  @override
  Widget build(BuildContext context) {
    final isPdf = widget.file.file.toLowerCase().endsWith('.pdf');
    return Column(
      children: [
        Expanded(
          child: Container(
            decoration: BoxDecoration(
              color: Colors.grey.shade100,
              borderRadius: BorderRadius.circular(4),
              border: Border.all(color: Colors.grey.shade300),
            ),
            child: Stack(
              fit: StackFit.expand,
              children: [
                if (isPdf)
                  ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: SfPdfViewer.network(
                      widget.file.file,
                      canShowScrollHead: false,
                      canShowPaginationDialog: false,
                      canShowScrollStatus: false,
                      enableDoubleTapZooming: false,
                      pageLayoutMode: PdfPageLayoutMode.single,
                    ),
                  )
                else
                  Center(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(_getFileIcon(), size: 50, color: _getFileColor()),
                        const SizedBox(height: 4),
                        Text(widget.file.file.split('.').last.toUpperCase(),
                            style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: _getFileColor())),
                      ],
                    ),
                  ),
                Positioned(
                  top: 4,
                  right: 4,
                  child: InkWell(
                    onTap: widget.onDownload,
                    child: Container(
                      padding: const EdgeInsets.all(4),
                      decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(4)),
                      child: const Icon(Icons.download, size: 16, color: Colors.white),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 4),
        Text(widget.doc.title?? widget.file.fileName, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 11)),
        if (isPdf)
          Text(_isLoadingPages? 'Loading...' : '$_totalPages pages', style: const TextStyle(fontSize: 9, color: Colors.grey))
        else
          Text('${(widget.file.fileSizeBytes?? 0) / 1024 ~/ 1} KB', style: const TextStyle(fontSize: 9, color: Colors.grey)),
      ],
    );
  }
}```

---

## 8. `document_viewer_screen.dart` (full code)

```dart
import 'package:flutter/material.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:path_provider/path_provider.dart';
import 'package:http/http.dart' as http;
import 'dart:io';

class DocumentViewerScreen extends StatefulWidget {
  final String documentUrl;
  final String fileName;
  final String mimeType;

  const DocumentViewerScreen({
    super.key,
    required this.documentUrl,
    required this.fileName,
    required this.mimeType,
  });

  @override
  State<DocumentViewerScreen> createState() => _DocumentViewerScreenState();
}

class _DocumentViewerScreenState extends State<DocumentViewerScreen> {
  @override
  Widget build(BuildContext context) {
    final isPdf = widget.mimeType.contains('pdf') || 
                  widget.fileName.toLowerCase().endsWith('.pdf');

    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: const Color(0xFF030F27),
        iconTheme: const IconThemeData(color: Colors.white),
        title: Text(
          widget.fileName,
          style: const TextStyle(color: Colors.white, fontSize: 16),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
      ),
      body: isPdf
        ? SfPdfViewer.network(
              widget.documentUrl,
              canShowScrollHead: true,
              canShowScrollStatus: true,
              onDocumentLoadFailed: (PdfDocumentLoadFailedDetails details) {
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text('Error: ${details.error}')),
                );
              },
            )
          : Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(
                    Icons.description,
                    size: 80,
                    color: Colors.grey[400],
                  ),
                  const SizedBox(height: 16),
                  Text(
                    "Preview available for PDF only",
                    style: TextStyle(color: Colors.grey[600]),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    widget.fileName,
                    style: const TextStyle(fontWeight: FontWeight.bold),
                    textAlign: TextAlign.center,
                  ),
                ],
              ),
            ),
    );
  }
}```

---

## 9. `video_player_screen.dart` (full code)

```dart
import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';
import 'package:chewie/chewie.dart';

class VideoPlayerScreen extends StatefulWidget {
  final String videoUrl;
  final String title;
  
  const VideoPlayerScreen({
    super.key, 
    required this.videoUrl,
    required this.title,
  });

  @override
  State<VideoPlayerScreen> createState() => _VideoPlayerScreenState();
}

class _VideoPlayerScreenState extends State<VideoPlayerScreen> {
  late VideoPlayerController _videoPlayerController;
  ChewieController? _chewieController;
  bool _isInitialized = false;
  bool _hasError = false;
  String _errorMessage = '';

  @override
  void initState() {
    super.initState();
    _initializePlayer();
  }

  Future<void> _initializePlayer() async {
    try {
      _videoPlayerController = VideoPlayerController.networkUrl(
        Uri.parse(widget.videoUrl),
        httpHeaders: {
          'User-Agent': 'Mozilla/5.0',
          'Accept': '*/*',
        },
      );
      
      await _videoPlayerController.initialize();
      
      _chewieController = ChewieController(
        videoPlayerController: _videoPlayerController,
        autoPlay: true,
        looping: false,
        allowFullScreen: true,
        allowMuting: true,
        showControls: true,
        materialProgressColors: ChewieProgressColors(
          playedColor: const Color(0xFF030F27),
          handleColor: const Color(0xFF030F27),
          backgroundColor: Colors.grey,
          bufferedColor: Colors.grey.shade400,
        ),
        errorBuilder: (context, errorMessage) {
          return Center(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Icon(Icons.error_outline, color: Colors.red, size: 60),
                const SizedBox(height: 16),
                const Text(
                  'Video load nahi hui',
                  style: TextStyle(color: Colors.white, fontSize: 18),
                ),
                const SizedBox(height: 8),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 20),
                  child: Text(
                    errorMessage,
                    style: const TextStyle(color: Colors.grey, fontSize: 12),
                    textAlign: TextAlign.center,
                  ),
                ),
              ],
            ),
          );
        },
      );
      
      if (mounted) {
        setState(() => _isInitialized = true);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _hasError = true;
          _errorMessage = e.toString();
        });
      }
    }
  }

  @override
  void dispose() {
    _videoPlayerController.dispose();
    _chewieController?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        iconTheme: const IconThemeData(color: Colors.white),
        title: Text(
          widget.title,
          style: const TextStyle(color: Colors.white, fontSize: 16),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
      ),
      body: Center(
        child: _hasError
         ? Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Icon(Icons.error_outline, color: Colors.red, size: 60),
                const SizedBox(height: 16),
                const Text(
                  'Video load nahi hui',
                  style: TextStyle(color: Colors.white, fontSize: 18),
                ),
                const SizedBox(height: 8),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 20),
                  child: Text(
                    _errorMessage,
                    style: const TextStyle(color: Colors.grey, fontSize: 12),
                    textAlign: TextAlign.center,
                  ),
                ),
              ],
            )
          : _isInitialized
            ? Chewie(controller: _chewieController!)
              : const Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    CircularProgressIndicator(color: Colors.white),
                    SizedBox(height: 16),
                    Text(
                      'Loading video...',
                      style: TextStyle(color: Colors.white),
                    ),
                  ],
                ),
      ),
    );
  }
}```

---

## 10. Business Logic Flows

### 10.1 Own Profile Load (`profile.dart`) — Cache-First
```
initState → _loadData() → parallel: _loadProfile() + _loadMyPosts()
        │
        ▼
ApiService.getProfile():
  SharedPreferences has a cached profile? → return it IMMEDIATELY,
    then kick off ApiService.getProfileFromAPI() in the background
    (errors from this background refresh are silently swallowed —
    only a print(), no UI update triggers from it — see §12)
  No cache → straight API call, cache the raw response body on success
        │
        ▼
Pull-to-refresh (_refresh) always calls ApiService.refreshProfile()
  → forces a fresh API hit + cache overwrite, bypassing the cache-first path
```

### 10.2 Edit Profile → Update → Back to Own Profile
```
EditProfileScreen(user: <current ProfileModel>) opened from profile.dart's
  app-bar edit icon
        │
        ▼
Optional new photo via image_picker (gallery, quality 80)
        │
        ▼
Save → ApiService.updateProfile(username, firstName, lastName, bio, photo)
  → PATCH /profile/update/ multipart (fields sent only if non-null/non-empty
    — partial update semantics; photo's contentType forced to image/jpeg
    unless the extension is exactly '.png')
        │
        ▼
On 200: cache immediately overwritten with the fresh `data` from the
  response — Navigator.pop(context, true)
        │
        ▼
profile.dart's _goToEditProfile sees `result == true` → _loadData(forceRefresh: true)
  → re-fetches from API (cache already matches, so this is a touch redundant
    but harmless double-fetch)
```

### 10.3 Target (other user's) Profile — Follow State Machine
```
fetchTargetProfile() → GET /profile/profile/<username>/
        │
        ▼
_buildFollowButton() reads BOTH directions from the one response:
  myFollowStatus  (have I followed them?  null / PENDING / ACCEPTED)
  theirFollowStatus (have they followed me? null / PENDING / ACCEPTED)
        │
        ├─ theirFollowStatus == 'PENDING' → show Confirm/Delete row
        │    (accept/reject THEIR incoming request) PLUS the main button below it
        └─ otherwise → just the main button:
             myStatus PENDING     → "Requested" (outlined, tap re-triggers handleFollow
                                     — same follow endpoint toggles/cancels a pending request)
             myStatus ACCEPTED    → "Following" (outlined)
             myStatus null +
               theirStatus ACCEPTED → "Follow Back" (filled)
             otherwise             → "Follow" (filled)
        │
        ▼
Any of handleFollow / handleAcceptRequest / handleRejectRequest →
  call the matching endpoint → on success, fetchTargetProfile() is called
  AGAIN in full (re-fetches profile AND re-triggers _loadTargetPosts) —
  simplest-possible state sync, at the cost of an extra round-trip per tap
```

### 10.4 Target Profile — Posts Visibility Gating
```
_loadTargetPosts():
  targetUser.isPrivate && myFollowStatus != 'ACCEPTED' → don't call the
    posts API at all client-side, just show the "private / follow to see
    posts" placeholder (isPostsLoading=false, postsError='PRIVATE_ACCOUNT')
  otherwise → ApiService.getTargetUserPosts(targetUserId) → GET /post/list/
    ?target_user_id=<id> — backend ALSO enforces this same private-account
    check server-side (see post_app.md's PostListAPIView.get_queryset —
    returns Post.objects.none() for a private, non-followed target), so
    this is defense-in-depth, not the only gate
```

### 10.5 Target Profile — Direct Message
```
Message icon button → _openChatWithUser() → isActionLoading guard →
  MessageApiService.getOrCreateConversation(targetUserId) — get-or-create
  pattern, so tapping Message on the same user twice reopens the SAME
  conversation, never duplicates one — → Navigator.push(ChatScreen(conversation))
```
This is the one feature in this whole module that reaches into a THIRD
module (`message`) beyond `profile`+`post` — see §5 for the coupling note.

### 10.6 Photos/Videos + Documents Tabs (shared shape, both `profile.dart` and `target_profile.dart`)
```
Posts split client-side by postType:
  mediaPosts    = postType in {image, video}
  documentPosts = postType in {document, pdf, excel, docx, xls, doc}
        │
        ▼
Photos/Videos grid tile: video → VideoFirstFrame (silent, muted, paused-at-
  frame-0 video_player preview, NOT chewie, NOT video_player_screen.dart —
  see §5) with a play-icon overlay; image → CachedNetworkImage. Tap ANY
  tile (media or document) → _openSinglePost(post.id) →
  Navigator.push(SinglePostPage(postId)) — the actual full media viewing
  (including real video playback, PDF, doc, etc.) happens over in the
  `post` module's `singlepost.dart` (see `flutter_post_app.md` §10.7),
  NOT in this module.
        │
        ▼
Documents grid tile (DocumentGridTile): PDF → inline SfPdfViewer.network
  thumbnail-as-preview (page count fetched separately via a raw Dio GET +
  `syncfusion_flutter_pdf`'s PdfDocument, purely to show "N pages" — a
  second network fetch of the same file just for a page count) +
  a small download-icon overlay wired to `onDownload` (see §10.7).
  Non-PDF → a file-type icon + extension label, no preview.
```

### 10.7 Document Download — Two Different Implementations
```
profile.dart's _downloadDocument(url, fileName):
  try canLaunchUrl(url) → launchUrl(..., externalApplication)  (no auth
    header attached at all)
  else → raw Dio().download(url, filePath)  (also no auth header)
  → shows the actual saved filePath in a SnackBar on the Dio fallback

target_profile.dart's _downloadDocument(url, fileName):
  → ApiService.downloadFile(url, fileName)  (DOES send the Bearer token,
    writes bytes straight to ApplicationDocumentsDirectory)
  → shows only a generic "Downloaded: $fileName" SnackBar — no real path,
    and the file is never opened (no OpenFilex/launchUrl call afterwards)
```
See §12 for why this divergence matters.

---

## 11. Known Issues / Things To Double-Check

1. **`document_viewer_screen.dart` and `video_player_screen.dart` are both
   unused/dead in this batch** — neither `DocumentViewerScreen` nor
   `VideoPlayerScreen` is referenced anywhere in `profile.dart`,
   `target_profile.dart`, or `edit_profile.dart`. Every actual document/video
   preview in this module is done inline (`SfPdfViewer.network` directly in
   `DocumentGridTile`, `VideoFirstFrame` using plain `video_player`). Either
   these two screens are meant to be wired in somewhere (tapping a document
   tile currently opens `SinglePostPage` in the `post` module instead, not
   `DocumentViewerScreen`), or they're leftover/for a different call-site not
   in this upload — confirm before assuming they're reachable.
2. **Two unrelated video-preview stacks exist across the app** —
   `VideoFirstFrame` here (and in the `post` module's `singlepost.dart`) uses
   plain `video_player` with a muted first-frame; `video_player_screen.dart`
   separately wraps `chewie` for a full player UI. If `chewie` is meant to be
   the real full-screen video experience, no screen in this module (or the
   `post` module, per `flutter_post_app.md`) currently opens it — full-screen
   video playback instead happens via `post` module's own
   `FullScreenVideoPage` (plain `video_player`, no `chewie`, no controls
   chrome). Confirm which one is actually intended to ship.
3. **Two different document-download code paths, only one authenticated**
   (§10.7) — `profile.dart`'s own `_downloadDocument` never attaches the
   Bearer token (`canLaunchUrl`/`launchUrl` externally, or a bare
   `Dio().download`), while `target_profile.dart`'s calls
   `ApiService.downloadFile` which does attach it. If post media requires
   auth to fetch (private posts, signed URLs, etc.), **downloading your own
   documents from your own profile screen may silently fail or fetch the
   wrong content**, while the exact same tile on someone else's profile
   works correctly. Same UX also differs: one shows the real saved path,
   the other doesn't and never opens the file afterward.
4. **`GET /profile/profile/<username>/`** — note the doubled `profile/`
   segment (`ApiService.getTargetProfile`, api_service.dart). This is almost
   certainly intentional (module-prefix `profile/` + a `profile/<username>/`
   sub-route) but worth a second look given how easy a typo like this is to
   introduce — confirm it matches the actual registered backend URL name,
   not just "it currently returns 200 so it must be right."
5. **`getMyPosts`'s `my_posts=true` query param appears to be a no-op** —
   the `post` module's backend `PostListAPIView.get_queryset` (per
   `post_app.md` §6) only branches on whether `target_user_id` is present;
   it already defaults to the logged-in user's own posts when
   `target_user_id` is absent, regardless of `my_posts`. Not a bug (the
   endpoint still returns the right posts), just dead weight — harmless to
   leave, but don't assume the backend actually reads that flag if you ever
   refactor either side.
6. **Background profile refresh failures are completely silent** (§10.1) —
   `getProfile()`'s background `getProfileFromAPI().catchError(...)` only
   `print()`s. A stale cached profile (e.g. after a username change from
   another device) can sit displayed indefinitely with zero user-facing
   signal that a background refresh has been failing.
7. **`edit_profile.dart` has no "remove photo" option** — you can only pick
   a new photo; there's no way to clear an existing `profile_photo` back to
   empty from this screen. `ApiService.updateProfile`'s `profilePhoto`
   parameter is nullable but nothing in the UI ever calls it with an
   explicit "clear" signal (there's no multipart field sent for
   "no photo" vs "unchanged photo" — both look identical to the backend
   as-is).
8. **`edit_profile.dart`'s username field has no client-side validation or
   availability check** — it's a plain `TextField`, so a duplicate/invalid
   username is only caught (if at all) by whatever the backend's
   `PATCH /profile/update/` returns, surfaced only as a generic red
   SnackBar with the raw exception text.
9. **PDF page-count is fetched via a second full network request per
   document tile** (§10.6) — `DocumentGridTile._getPdfPages()` does its own
   `Dio().get(...bytes)` + `syncfusion_flutter_pdf`'s `PdfDocument` parse,
   completely separate from the `SfPdfViewer.network` already rendering the
   same file right next to it. For a grid with many PDF tiles this means
   2x the network/CPU cost per tile just to show "N pages" — worth caching
   or getting the page count from the backend's `PostMedia` metadata
   instead (per `post_app.md` §3, `PostMedia.metadata` is a free-form
   JSONField that could carry this).
10. **`VideoFirstFrame` and `DocumentGridTile` are duplicated verbatim**
    between `profile.dart` and `target_profile.dart` (identical class
    bodies, confirmed byte-for-byte) — `target_profile.dart`'s own comment
    even says "profile.dart se copy kar lo, same rahenge." A shared widgets
    file would remove this duplication; right now a fix to one needs to be
    manually mirrored to the other or they'll drift.
11. **`target_profile.dart` has a ~1000-line fully-commented-out earlier
    version at the top of the file** (lines 1-1002), before the real active
    class starts. It's the same screen minus the message/chat feature.
    Harmless (compiles fine, just dead comment weight) but worth deleting
    if this file is ever cleaned up — same pattern already flagged for
    `comment_serializers.py`/`comment_view.py` on the backend side, so it
    looks like a house habit rather than a one-off.

---

## 12. Quick Setup Checklist (to run this module standalone)

- [ ] All pubspec.yaml packages from §2 added (note: `chewie` is ONLY
      needed if `video_player_screen.dart` is actually wired in somewhere
      outside this batch — see §11 item 1).
- [ ] Project-local files present and matching import paths used here:
      `utils/api.dart`, `services/auth_service.dart`, `login/login_screen.dart`,
      `post/screens/singlepost.dart` — and, only if `target_profile.dart` is
      included, `message/services/message_api_service.dart` +
      `message/screens/chat_screen.dart`.
- [ ] Backend `profile` app's 5 endpoints (§2) confirmed live: own profile,
      target profile, follow, accept-request, reject-request, update.
- [ ] Backend `post` app (see `post_app.md` §15) installed and reachable —
      both `getMyPosts` and `getTargetUserPosts` depend on its
      `/post/list/` endpoint.
- [ ] Decide + fix the auth-header inconsistency in document download
      (§11 item 3) before shipping — at minimum, make `profile.dart`'s own
      `_downloadDocument` go through `ApiService.downloadFile` like
      `target_profile.dart`'s already does.
- [ ] Decide whether `document_viewer_screen.dart` / `video_player_screen.dart`
      should be wired into the Documents/Photos-Videos tabs here, or removed
      if truly unused (§11 item 1).

With the above satisfied, everything in this single document — model,
api_service, profile, edit_profile, target_profile, document_viewer_screen,
video_player_screen — is enough to understand and run this whole Flutter
`profile` frontend module end to end, against the backend `profile` app and
the `post` module documented in `flutter_post_app.md` / `post_app.md`.
