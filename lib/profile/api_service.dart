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
    final token = await AuthService.getValidToken();
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
  //
  // `onBackgroundError` — optional. Pehle ye background refresh failure
  // sirf print() hoti thi (profile_app.md §11 item 6) — ek stale cached
  // profile (e.g. dusre device se username badalne ke baad) indefinitely
  // dikhta reh sakta tha, user ko pata hi nahi chalta. Ab caller chahe to
  // ek callback de sakta hai (profile.dart deta hai — ek chhota "showing
  // saved data" warning ke liye); na de to bilkul pehle jaisa hi silent
  // behaviour (home.dart ka fire-and-forget getProfile() call ab bhi
  // bina kisi change ke chalta hai).
  static Future<ProfileModel> getProfile({
    void Function(Object error)? onBackgroundError,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    
    // Step 1: Cache check karo
    final cachedData = prefs.getString(_profileCacheKey);
    if (cachedData != null) {
      // Cache mila to turant return kar do
      final cachedProfile = ProfileModel.fromJson(jsonDecode(cachedData));
      
      // Step 2: Background me API call - error ignore kar dena
      getProfileFromAPI().catchError((e) {
        print("Background refresh failed: $e");
        onBackgroundError?.call(e);
        // catchError needs a return value matching the Future's type —
        // rethrow-free, caller already got the cached profile above.
        return cachedProfile;
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
    final token = await AuthService.getValidToken();
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
    final token = await AuthService.getValidToken();
    // 🔥 FIX: token expire ho chuka ho aur refresh bhi fail ho jaye
    // (getValidToken() null deta hai + khud hi onForceLogout call kar
    // chuka hota hai) to yahan seedha "Bearer null" bhejne ki jagah ek
    // saaf error — warna "Follow failed" jaisa confusing message aata
    // tha jiski asal wajah session expiry thi.
    if (token == null) throw Exception('Session expired. Please log in again.');
    final url = Uri.parse("${Api.baseUrl}/profile/follow/$userId/");
    
    final response = await http.post(url, headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    });

    if (response.statusCode == 200 || response.statusCode == 201) {
      return jsonDecode(response.body); 
    } else {
      throw Exception(_extractErrorMessage(response, fallback: 'Follow failed'));
    }
  }

  // 🔥 8. Accept Request
  static Future<Map<String, dynamic>> acceptFollowRequest(int followId) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('Session expired. Please log in again.');
    final url = Uri.parse("${Api.baseUrl}/profile/accept-request/$followId/");
    
    final response = await http.post(url, headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    });

    if (response.statusCode == 200) {
      return jsonDecode(response.body); 
    } else {
      throw Exception(_extractErrorMessage(response, fallback: 'Accept failed'));
    }
  }

  // 🔥 9. Reject Request
  static Future<Map<String, dynamic>> rejectFollowRequest(int followId) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('Session expired. Please log in again.');
    final url = Uri.parse("${Api.baseUrl}/profile/reject-request/$followId/");
    
    final response = await http.post(url, headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    });

    if (response.statusCode == 200) {
      return jsonDecode(response.body); 
    } else {
      throw Exception(_extractErrorMessage(response, fallback: 'Reject failed'));
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
    final token = await AuthService.getValidToken();
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
      throw Exception(_extractUpdateError(response));
    }
  }

  // DRF validation errors come back as {"field": ["message", ...]}. The old
  // code threw the raw response body straight at the UI (a user hitting a
  // duplicate username would see literal JSON in a SnackBar). This pulls
  // out the actual message — with a `username:` prefix specifically for
  // that field, so EditProfileScreen can show the same signupUsernameExists
  // copy the signup flow already uses, instead of pattern-matching on
  // English text that breaks the moment this app ships another locale.
  static String _extractUpdateError(http.Response response) {
    if (_tryDecodeMap(response) case final body?) {
      if (body['username'] is List && (body['username'] as List).isNotEmpty) {
        return 'username:${(body['username'] as List).first}';
      }
    }
    return _extractErrorMessage(response, fallback: 'Update failed');
  }

  // Same idea, generalized for the follow/accept/reject endpoints — no
  // field needs special sentinel treatment there, just "don't show the
  // raw JSON body".
  static String _extractErrorMessage(http.Response response, {required String fallback}) {
    if (_tryDecodeMap(response) case final body?) {
      for (final value in body.values) {
        if (value is List && value.isNotEmpty) return value.first.toString();
        if (value is String && value.isNotEmpty) return value;
      }
    }
    return '$fallback (${response.statusCode})';
  }

  static Map<String, dynamic>? _tryDecodeMap(http.Response response) {
    try {
      final decoded = jsonDecode(response.body);
      return decoded is Map<String, dynamic> ? decoded : null;
    } catch (_) {
      return null;
    }
  }

// 🔥 11. Get My Posts - sirf login user ke posts
static Future<List<PostModel>> getMyPosts({int page = 1}) async {
  final token = await AuthService.getValidToken();
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

// 🔥 11b. Same as getMyPosts, but keeps DRF's `next` field so the caller
// actually knows whether another page exists, instead of guessing from
// a possibly-short-but-not-empty page. Used for the profile screen's
// real infinite-scroll (old getMyPosts() only ever got called with the
// default page=1 — no caller anywhere ever advanced past it).
static Future<PostsPage> getMyPostsPage({int page = 1}) async {
  final token = await AuthService.getValidToken();
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
    final List results = data['results'] ?? [];
    return PostsPage(
      posts: results.map((e) => PostModel.fromJson(e)).toList(),
      hasMore: data['next'] != null,
    );
  } else {
    throw Exception('Failed to load posts: ${response.body}');
  }
}


// 🔥 Add this method in ApiService class
static Future<void> downloadFile(String url, String fileName) async {
  try {
    final token = await AuthService.getValidToken();
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
  final token = await AuthService.getValidToken();
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

// Same fix as getMyPostsPage — surfaces DRF's real `next` field instead of
// silently stopping at page 1 forever (confirmed the same bug existed here:
// target_profile.dart's caller never passed page > 1 anywhere).
static Future<PostsPage> getTargetUserPostsPage(int targetUserId, {int page = 1}) async {
  final token = await AuthService.getValidToken();
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
    final List results = data['results'] ?? [];
    return PostsPage(
      posts: results.map((e) => PostModel.fromJson(e)).toList(),
      hasMore: data['next'] != null,
    );
  } else if (response.statusCode == 403) {
    throw Exception('PRIVATE_ACCOUNT');
  } else {
    throw Exception('Failed to load posts: ${response.body}');
  }
}











}
















