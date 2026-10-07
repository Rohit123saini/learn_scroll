import 'dart:io';
import 'package:http_parser/http_parser.dart'; 
import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../utils/api.dart';
import './model.dart';
import './discovery_models.dart'; // P8-FE
import './profile_link.dart'; // P7-FE
import '../services/auth_service.dart';
import '../services/crash_reporting_service.dart';
import 'package:path_provider/path_provider.dart';

/// Thrown by `ApiService.getTargetProfile` on HTTP 404 — the user doesn't
/// exist OR they blocked the viewer (the backend deliberately looks identical).
class ProfileNotFoundException implements Exception {
  const ProfileNotFoundException();
  @override
  String toString() => 'Profile not found';
}

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
        CrashReportingService.logError("ProfileApiService.backgroundRefresh", e);
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
    } else if (response.statusCode == 404) {
      // Also what the backend returns when that person blocked you.
      throw const ProfileNotFoundException();
    } else {
      throw Exception('Failed to load target profile: ${response.body}');
    }
  }

  static Future<TargetProfileModel> refreshTargetProfile(String username) async {
    return await getTargetProfile(username);
  }

  // 🔥 P8-FE — "Followed by X, Y + N others". Decorative, so callers should swallow
  // errors (the profile must never fail to open because this line couldn't load).
  static Future<MutualFollowers> getMutualFollowers(String username) async {
    final token = await AuthService.getValidToken();
    if (token == null) return MutualFollowers.empty;
    final url = Uri.parse("${Api.baseUrl}/profile/profile/${Uri.encodeComponent(username)}/mutuals/");
    final response = await http.get(url, headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    });
    if (response.statusCode != 200) throw Exception('Mutuals failed (${response.statusCode})');
    final body = jsonDecode(utf8.decode(response.bodyBytes));
    return body is Map<String, dynamic> ? MutualFollowers.fromJson(body) : MutualFollowers.empty;
  }

  // 🔥 P8-FE — "Suggested for you" under a profile (viewer's follows / blocked /
  // restricted are already excluded server-side).
  static Future<List<MiniUser>> getSimilarUsers(String username, {int limit = 10}) async {
    final token = await AuthService.getValidToken();
    if (token == null) return const [];
    final url = Uri.parse(
        "${Api.baseUrl}/profile/profile/${Uri.encodeComponent(username)}/similar/?limit=$limit");
    final response = await http.get(url, headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    });
    if (response.statusCode != 200) throw Exception('Suggestions failed (${response.statusCode})');
    final body = jsonDecode(utf8.decode(response.bodyBytes));
    return body is Map ? MiniUser.listFrom(body['suggested_users']) : const [];
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

  // 🔥 P11-FE — GET /profile/follow-requests/ : PENDING requests addressed to me,
  // newest first, paginated. Same {status, message, data: {results, next}} envelope
  // as the followers/following lists; every row is a user row (id, username,
  // first_name, last_name, mutual_friends, …) PLUS `follow_id` — the id that
  // acceptFollowRequest / rejectFollowRequest above take.
  //
  // First page: call with no args. Next page: pass the previous page's `next`
  // (its scheme/host are pinned to Api.baseUrl, same as FollowListScreen does).
  static Future<({List<Map<String, dynamic>> rows, String? next, int? total})> getFollowRequests({String? nextUrl}) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('Session expired. Please log in again.');

    final Uri url;
    if (nextUrl != null && nextUrl.isNotEmpty) {
      final b = Uri.parse(Api.baseUrl);
      url = Uri.parse(nextUrl).replace(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null);
    } else {
      url = Uri.parse("${Api.baseUrl}/profile/follow-requests/");
    }

    final response = await http.get(url, headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    }).timeout(const Duration(seconds: 20));

    if (response.statusCode != 200) {
      throw Exception(_extractErrorMessage(response, fallback: 'Failed to load follow requests'));
    }

    final body = jsonDecode(utf8.decode(response.bodyBytes));
    final data = body is Map && body.containsKey('data') ? body['data'] : body;
    List raw;
    String? next;
    int? total; // DRF page `count` = ALL pending requests, not just this page
    if (data is Map) {
      raw = (data['results'] as List?) ?? const [];
      next = data['next']?.toString();
      final c = data['count'];
      total = c is num ? c.toInt() : int.tryParse('${c ?? ''}');
    } else {
      raw = (data as List?) ?? const [];
    }
    return (
      rows: raw.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList(),
      next: next,
      total: total ?? raw.length,
    );
  }

  // 🔥 P11-FE — DELETE /profile/followers/<user_id>/ : "Remove follower".
  // Silent on the backend (the removed user is NOT notified). A 404 *with a JSON
  // body* means "that user isn't your follower (any more)" — double-tap, or already
  // removed from another device — which is exactly the end state the caller wants,
  // so it counts as success. A 404 without a JSON body (route missing / old
  // backend) is still an error.
  static Future<void> removeFollower(int userId) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('Session expired. Please log in again.');
    final url = Uri.parse("${Api.baseUrl}/profile/followers/$userId/");

    final response = await http.delete(url, headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    });

    if (response.statusCode == 200) return;
    if (response.statusCode == 404 && _tryDecodeMap(response) != null) return;
    throw Exception(_extractErrorMessage(response, fallback: 'Remove follower failed'));
  }

  // 🔥 10. Update Profile - Image + text
  static Future<UpdateProfileResponse> updateProfile({
    String? username,
    String? firstName,
    String? lastName,
    String? bio,
    String? pronouns, // P7-FE
    String? categoryLabel, // P7-FE
    List<ProfileLink>? links, // P7-FE — [] clears them
    File? profilePhoto,
    bool? isPrivate,
  }) async {
    final token = await AuthService.getValidToken();
    final url = Uri.parse("${Api.baseUrl}/profile/update/");
    
    var request = http.MultipartRequest('PATCH', url);
    request.headers['Authorization'] = 'Bearer $token';

    if (username != null && username.isNotEmpty) request.fields['username'] = username;
    if (firstName != null) request.fields['first_name'] = firstName;
    if (lastName != null) request.fields['last_name'] = lastName;
    if (bio != null) request.fields['bio'] = bio;
    // P7-FE — bio upgrade (backend P6-BE). `links` travels as a JSON string in the multipart body.
    if (pronouns != null) request.fields['pronouns'] = pronouns;
    if (categoryLabel != null) request.fields['category_label'] = categoryLabel;
    if (links != null) request.fields['links'] = jsonEncode(links.map((l) => l.toJson()).toList());
    // 🔥 NAYA [Settings/Nav pass] — Settings > Account > "Private account"
    // toggle. Backend `UpdateProfileView` `is_private` already accept karta
    // tha (docstring: "Allowed fields: ... is_private") — bas frontend se
    // kabhi bheja hi nahi jaata tha.
    if (isPrivate != null) request.fields['is_private'] = isPrivate.toString();

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
      // UpdateProfileView wraps DRF field errors as {"status": false, "message": ..,
      // "errors": {"username": [..]}} — the old code only looked at the TOP level, so
      // this branch never fired and the duplicate-username copy never showed. Check
      // `errors` first, top level second (in case a proxy/other view returns bare DRF).
      final errs = body['errors'] is Map<String, dynamic> ? body['errors'] as Map<String, dynamic> : body;
      if (errs['username'] is List && (errs['username'] as List).isNotEmpty) {
        return 'username:${(errs['username'] as List).first}';
      }
      // P7-FE — show the real reason ("You can add at most 3 links.", "Link 2: enter a
      // valid http:// or https:// URL.") instead of the generic "Validation failed.".
      for (final key in const ['links', 'pronouns', 'category_label', 'bio', 'profile_photo']) {
        final v = errs[key];
        if (v is List && v.isNotEmpty) return v.first.toString();
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

// 🔥 NAYA — Instagram jaisa "Saved" tab (own profile only).
// GET /post/saved/?page= -> current user ke saved posts, paginated,
// newest-saved-first (backend already orders by `-saved_by__created_at`).
// Same PostsPage shape as getMyPostsPage so profile.dart's grid + real
// infinite-scroll pattern works unchanged for this tab too.
static Future<PostsPage> getSavedPosts({int page = 1}) async {
  final token = await AuthService.getValidToken();
  final url = Uri.parse("${Api.baseUrl}/post/saved/?page=$page");

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
    throw Exception('Failed to load saved posts: ${response.body}');
  }
}


// P3-FE — pin / unpin one of MY posts (profile pinned posts, max 3).
// POST   /post/<id>/pin/ -> 200 {data:{is_pinned,pinned_count,max_pinned}}
//                           400 {code:"pin_limit_reached"|"post_not_pinnable", message}
//                           403 not owner / 404 gone
// DELETE /post/<id>/pin/ -> 200 (idempotent)
static Future<PinResult> setPostPinned(String postId, bool pinned) async {
  final token = await AuthService.getValidToken();
  if (token == null) throw Exception('User not authenticated');
  final uri = Uri.parse("${Api.baseUrl}/post/$postId/pin/");
  final headers = {"Authorization": "Bearer $token"};
  final res = await (pinned ? http.post(uri, headers: headers) : http.delete(uri, headers: headers))
      .timeout(const Duration(seconds: 15));

  Map<String, dynamic>? body;
  try {
    final d = jsonDecode(res.body);
    if (d is Map) body = Map<String, dynamic>.from(d);
  } catch (_) {}

  if (res.statusCode == 200) {
    final data = body?['data'] is Map ? Map<String, dynamic>.from(body!['data'] as Map) : const <String, dynamic>{};
    return PinResult(
      isPinned: data['is_pinned'] == true,
      pinnedCount: (data['pinned_count'] as num?)?.toInt() ?? 0,
      maxPinned: (data['max_pinned'] as num?)?.toInt() ?? 3,
    );
  }
  final fallback = res.statusCode == 404
      ? 'This post is no longer available.'
      : res.statusCode == 403
          ? 'You can only pin your own posts.'
          : 'Could not ${pinned ? 'pin' : 'unpin'} post (${res.statusCode}).';
  throw PinException(body?['message']?.toString() ?? fallback, code: body?['code']?.toString());
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

// ============================================================
// 🔥 NAYA [Settings/Nav pass] — Settings > Privacy > Blocked accounts.
// Backend `BlockedUsersView`/`UnblockUserView` (user_profile/views.py)
// already production-ready thay, bas frontend se kabhi call hi nahi
// hote thay — koi settings UI hi nahi thi.
// ============================================================

/// GET /profile/blocked-users/ -> maine jinko block kiya hai unki list.
static Future<List<BlockedUserModel>> getBlockedUsers() async {
  final token = await AuthService.getValidToken();
  final url = Uri.parse("${Api.baseUrl}/profile/blocked-users/");
  final response = await http.get(url, headers: {
    "Authorization": "Bearer $token",
    "Content-Type": "application/json",
  });

  if (response.statusCode == 200) {
    final data = jsonDecode(response.body);
    final List results = data['data'] ?? [];
    return results.map((e) => BlockedUserModel.fromJson(e)).toList();
  }
  throw Exception(_extractErrorMessage(response, fallback: 'Failed to load blocked accounts'));
}

/// POST /profile/blocked-users/ {"blocked": <user_id>} — block karo (profile
/// ka ⋮ menu). Idempotent: dobara block karne par backend 200 deta hai.
///
/// Optional extras (the block dialog's checkboxes):
///  * [blockNewAccounts] — "Also block new accounts they may create".
///  * [reportReason]    — also report the account in the same request
///    (one of: spam, harassment, hate, nudity, violence, self_harm, scam,
///    impersonation, other). Returns true if a report was filed.
static Future<bool> blockUser(
  int userId, {
  bool blockNewAccounts = false,
  String? reportReason,
}) async {
  final token = await AuthService.getValidToken();
  final url = Uri.parse("${Api.baseUrl}/profile/blocked-users/");
  final response = await http.post(
    url,
    headers: {
      "Authorization": "Bearer $token",
      "Content-Type": "application/json",
    },
    body: jsonEncode({
      "blocked": userId,
      if (blockNewAccounts) "block_new_accounts": true,
      if (reportReason != null) "report_reason": reportReason,
    }),
  );
  if (response.statusCode != 200 && response.statusCode != 201) {
    throw Exception(_extractErrorMessage(response, fallback: 'Failed to block user'));
  }
  try {
    return (jsonDecode(response.body) as Map)['report_filed'] == true;
  } catch (_) {
    return false;
  }
}

/// One page of the blocked list: GET /profile/blocked-users/?q=&limit=&offset=
static Future<BlockedPage> getBlockedUsersPage({
  String query = '',
  int offset = 0,
  int limit = 30,
}) async {
  final token = await AuthService.getValidToken();
  final url = Uri.parse("${Api.baseUrl}/profile/blocked-users/").replace(queryParameters: {
    if (query.trim().isNotEmpty) 'q': query.trim(),
    'limit': '$limit',
    'offset': '$offset',
  });
  final response = await http.get(url, headers: {
    "Authorization": "Bearer $token",
    "Content-Type": "application/json",
  });
  if (response.statusCode != 200) {
    throw Exception(_extractErrorMessage(response, fallback: 'Failed to load blocked accounts'));
  }
  final data = jsonDecode(response.body) as Map<String, dynamic>;
  final List results = data['data'] ?? [];
  return BlockedPage(
    items: results.map((e) => BlockedUserModel.fromJson(e)).toList(),
    hasMore: data['has_more'] == true,
    nextOffset: data['next_offset'] as int?,
  );
}

/// POST /profile/restricted-users/ {"restricted": id} — silent, one-way.
static Future<void> restrictUser(int userId) async {
  final token = await AuthService.getValidToken();
  final response = await http.post(
    Uri.parse("${Api.baseUrl}/profile/restricted-users/"),
    headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
    body: jsonEncode({"restricted": userId}),
  );
  if (response.statusCode != 200 && response.statusCode != 201) {
    throw Exception(_extractErrorMessage(response, fallback: 'Failed to restrict user'));
  }
}

/// DELETE /profile/restricted-users/<id>/
static Future<void> unrestrictUser(int userId) async {
  final token = await AuthService.getValidToken();
  final response = await http.delete(
    Uri.parse("${Api.baseUrl}/profile/restricted-users/$userId/"),
    headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
  );
  if (response.statusCode != 200 && response.statusCode != 204) {
    throw Exception(_extractErrorMessage(response, fallback: 'Failed to unrestrict user'));
  }
}

/// POST /post/muted-accounts/ {"user_id": id} — hides their posts AND stories
/// from my feed / story tray; follow, profile and chat stay as they were.
static Future<void> muteUser(int userId) async {
  final token = await AuthService.getValidToken();
  final response = await http.post(
    Uri.parse("${Api.baseUrl}/post/muted-accounts/"),
    headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
    body: jsonEncode({"user_id": userId}),
  );
  if (response.statusCode != 200 && response.statusCode != 201) {
    throw Exception(_extractErrorMessage(response, fallback: 'Failed to mute user'));
  }
}

/// DELETE /post/muted-accounts/<id>/ (idempotent)
static Future<void> unmuteUser(int userId) async {
  final token = await AuthService.getValidToken();
  final response = await http.delete(
    Uri.parse("${Api.baseUrl}/post/muted-accounts/$userId/"),
    headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
  );
  if (response.statusCode != 200) {
    throw Exception(_extractErrorMessage(response, fallback: 'Failed to unmute user'));
  }
}

/// POST /profile/reports/ — report an account / post / comment / story.
/// [targetType]: user | post | comment | story. Returns true when the report
/// is new, false when I had already reported the same thing.
static Future<bool> reportContent({
  required String targetType,
  required String targetId,
  required String reason,
  String details = '',
}) async {
  final token = await AuthService.getValidToken();
  final response = await http.post(
    Uri.parse("${Api.baseUrl}/profile/reports/"),
    headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
    body: jsonEncode({
      "target_type": targetType,
      "target_id": targetId,
      "reason": reason,
      if (details.trim().isNotEmpty) "details": details.trim(),
    }),
  );
  if (response.statusCode == 201) return true;
  if (response.statusCode == 200) return false;
  throw Exception(_extractErrorMessage(response, fallback: 'Failed to send report'));
}

/// DELETE /profile/blocked-users/<id>/ — `<id>` target user ki id bhi ho
/// sakti hai (backend dono accept karta hai — dekho UnblockUserView).
static Future<void> unblockUser(int userId) async {
  final token = await AuthService.getValidToken();
  final url = Uri.parse("${Api.baseUrl}/profile/blocked-users/$userId/");
  final response = await http.delete(url, headers: {
    "Authorization": "Bearer $token",
    "Content-Type": "application/json",
  });
  if (response.statusCode != 200) {
    throw Exception(_extractErrorMessage(response, fallback: 'Failed to unblock user'));
  }
}






}
















