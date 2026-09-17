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

// ===================== HOME PAGE MODELS (Task 3) =====================

// 3.1 — classroom switcher chips ("YOUR CLASSROOMS" row)
//
// FIX (real-field mismatch, found while wiring Task 5): `ClassroomSerializer`
// (backend) has no `name` field — classrooms are keyed by `title`. Was
// reading `json['name']`, which is never present on the real
// `classrooms/?mine=true` response, so every chip's label silently came
// through as ''. Also: `is_live` isn't a field on Classroom at all (only
// ClassSession has a live/not-live status) — kept defaulting to `false`
// via `?? false`, just noted here so a future pass doesn't assume it's
// wired when it isn't.
//
// referralEnabled/referralCommissionPercent — NEW (Task 5): real fields
// on ClassroomSerializer, needed so the home invite strip can tell which
// of the caller's own classrooms it's even allowed to generate a
// refer-link for (`refer-link/` 400s with "doesn't have referrals
// enabled" otherwise) and show the %commission before sharing.
class ClassroomModel {
  final String id;
  final String name;
  final bool isLive;
  final bool isActive;
  final bool referralEnabled;
  final double referralCommissionPercent;
  ClassroomModel({
    required this.id,
    required this.name,
    required this.isLive,
    required this.isActive,
    this.referralEnabled = false,
    this.referralCommissionPercent = 0,
  });
  factory ClassroomModel.fromJson(Map<String, dynamic> json) {
    return ClassroomModel(
      id: json['id']?.toString() ?? '',
      name: json['title'] ?? json['name'] ?? '',
      isLive: json['is_live'] ?? false,
      isActive: json['is_active'] ?? false,
      referralEnabled: json['referral_enabled'] ?? false,
      referralCommissionPercent: double.tryParse('${json['referral_commission_percent'] ?? 0}') ?? 0,
    );
  }
}

// 3.2 — "Live now" cards
//
// ✅ CONFIRMED (Task 3) — real shape of GET /liveclass/sessions/live-now/
// (LiveNowSessionSerializer, backend). NOT the same shape as the old mock
// (`title`/`teacher_name`/`subject`/`viewers_count`/`thumbnail_url` flat on
// the session) — a session has no title of its own, so `title` here is the
// CLASSROOM's title (nested `classroom` object: id/title/subject/
// cover_image/teacher), same "card" shape ClassroomMiniSerializer already
// uses elsewhere. `id` is the ClassSession id (needed for the join call,
// NOT the classroom id — see classroomId below for that).
class LiveClassModel {
  final String id; // ClassSession id
  final String classroomId;
  final String title; // classroom title
  final String teacherName;
  final String subject;
  final int viewersCount; // live participant_count
  final String? thumbnailUrl; // classroom cover_image
  final String? roomId;
  LiveClassModel({
    required this.id,
    required this.classroomId,
    required this.title,
    required this.teacherName,
    required this.subject,
    required this.viewersCount,
    this.thumbnailUrl,
    this.roomId,
  });
  factory LiveClassModel.fromJson(Map<String, dynamic> json) {
    final classroom = json['classroom'] as Map<String, dynamic>? ?? {};
    final teacher = classroom['teacher'] as Map<String, dynamic>?;
    return LiveClassModel(
      id: json['id']?.toString() ?? '',
      classroomId: classroom['id']?.toString() ?? '',
      title: classroom['title'] ?? '',
      teacherName: teacher != null ? (teacher['full_name'] ?? teacher['username'] ?? '') : '',
      subject: classroom['subject'] ?? '',
      viewersCount: json['participant_count'] ?? 0,
      thumbnailUrl: classroom['cover_image'],
      roomId: json['room_id'],
    );
  }
}

// 3.3 — stories row
//
// MOVED — `StoryModel`/`StoryGroup`/`groupStories()` now live in
// `post/models/story_model.dart`, and the `getStories()` call that used to
// sit in `HomeExtrasService` below now lives in `post/services/
// story_service.dart` as `StoryService.getStories()`, alongside the story
// create/mark-viewed calls that were already there. See those files for
// the real backend shape (`post_app.md` §16.2 + `post/views.py`
// StoryListAPIView).

// 3.4 — Invite & Earn.
//
// FIX (Task 5 — wrong mapping, real backend shape is different): this used
// to assume `coins_per_referral`/`invite_link` flat keys, a one-time
// signup-style referral. The real system (already fully built — see
// Classroom.referral_enabled/referral_commission_percent, ClassroomViewSet
// .refer_link/.referral_dashboard, ReferralViewSet.class_referral_summary
// in views.py) is a CLASS-LEVEL, ongoing-commission program: a teacher
// turns referrals on for a classroom and sets a %commission; anyone who
// shares that classroom's refer-link earns that % of the DAILY per-day
// fee for every day the referred student keeps the class (see
// PassDailyCharge / `referral_per_day_rate` in views.py) — not a flat
// one-time bonus, and not something the referrer sets themselves.
//
// This model is the GLOBAL summary (GET referrals/class-referral-summary/)
// — the caller's own totals across every classroom they've ever referred
// someone into. Per-classroom detail (needed to actually generate a share
// link + see that classroom's %) is ReferLinkModel below.
class InviteEarnModel {
  final String referralCode;
  final int totalStudentsReferred;
  final int totalCommissionEarned;
  final int totalCommissionPending;
  InviteEarnModel({
    required this.referralCode,
    required this.totalStudentsReferred,
    required this.totalCommissionEarned,
    required this.totalCommissionPending,
  });
  factory InviteEarnModel.fromJson(Map<String, dynamic> json) {
    return InviteEarnModel(
      referralCode: json['referral_code'] ?? '',
      totalStudentsReferred: json['total_students_referred'] ?? 0,
      totalCommissionEarned: json['total_commission_earned'] ?? 0,
      totalCommissionPending: json['total_commission_pending'] ?? 0,
    );
  }
}

// NEW (Task 5) — GET classrooms/{id}/refer-link/ response. Only callable
// for a classroom with `referral_enabled == true` (see ClassroomModel
// above) — the backend 400s with "This classroom doesn't have referrals
// enabled." otherwise, which HomeExtrasService.getReferLink() below
// surfaces as a plain Exception for the UI to catch and show a snackbar
// for, rather than crashing.
class ReferLinkModel {
  final String referralCode;
  final String webUrl;
  final String deepLink;
  final String shareText;
  final double commissionPercent;
  ReferLinkModel({
    required this.referralCode,
    required this.webUrl,
    required this.deepLink,
    required this.shareText,
    required this.commissionPercent,
  });
  factory ReferLinkModel.fromJson(Map<String, dynamic> json) {
    return ReferLinkModel(
      referralCode: json['referral_code'] ?? '',
      webUrl: json['web_url'] ?? '',
      deepLink: json['deep_link'] ?? '',
      shareText: json['share_text'] ?? '',
      commissionPercent: double.tryParse('${json['commission_percent'] ?? 0}') ?? 0,
    );
  }
}

// ===================== API SERVICE WITH CACHE =====================
//
// 🔥 TASK 11.3 — har http call pe timeout. Pehle koi nahi tha: server
// hang ho jaata to app hamesha ke liye spinner dikhata rehta.
// 🔥 TASK 8.4 — saare token lookups ab AuthService.getValidToken() use
// karte hain, getToken() nahi. Farq:
//   getToken()      → jo bhi token disk pe pada hai wahi de deta hai, chahe
//                     expire ho chuka ho → request 401 khaake silently fail,
//                     aur onForceLogout kabhi trigger hi nahi hota.
//   getValidToken() → expiry-aware: zaroorat ho to pehle proactive refresh
//                     karta hai, aur refresh-token bhi mar chuka ho to
//                     AuthService.onForceLogout() fire karta hai — jo
//                     session_service.dart ka notifier set karta hai aur
//                     HomeScreen 3 sec baad login pe redirect kar deta hai.

/// Task 11.3 — sab home calls ke liye ek hi timeout.
const Duration kApiTimeout = Duration(seconds: 15);

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
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/feed/?page=$page&page_size=$pageSize");
    final response = await http
        .get(url, headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"})
        .timeout(kApiTimeout); // Task 11.3
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
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/$postId/like/");
    final response =
        await http.post(url, headers: {"Authorization": "Bearer $token"}).timeout(kApiTimeout);
    return response.statusCode == 200 || response.statusCode == 201;
  }

  // 🔥 FINAL - ONLY ONE toggleSave
  static Future<Map<String, dynamic>> toggleSave(String postId, {String collection = 'default'}) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/$postId/save/");
    final response = await http.post(
      url,
      headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
      body: jsonEncode({"collection_name": collection}),
    ).timeout(kApiTimeout);
    if (response.statusCode == 200 || response.statusCode == 201) {
      return jsonDecode(response.body);
    } else {
      throw Exception('Save failed: ${response.body}');
    }
  }

  static Future<Map<String, dynamic>> toggleReaction(String postId, String reaction) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/post/like/$postId/reaction/");
    final response = await http
        .post(url,
            headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"},
            body: jsonEncode({"reaction": reaction}))
        .timeout(kApiTimeout);
    if (response.statusCode == 200) return jsonDecode(response.body);
    throw Exception('Reaction failed: ${response.body}');
  }

  // NEW — SujhaavFayda1 item 2 ("feed zinda feel"). Bulk, lightweight
  // polling call: given the post ids currently on screen, returns just
  // their reaction + comment counts (see PostCountsAPIView on the
  // backend) instead of refetching the whole feed page. Called on a
  // timer from HomeScreen so counts update without pull-to-refresh.
  // Returns an empty map on any failure — this must never surface an
  // error to the user, it's a background nicety.
  static Future<Map<String, dynamic>> getPostCounts(List<String> postIds) async {
    if (postIds.isEmpty) return {};
    try {
      final url = Uri.parse("${Api.baseUrl}/post/counts/?ids=${postIds.join(',')}");
      final response = await http.get(url).timeout(kApiTimeout);
      if (response.statusCode != 200) return {};
      final decoded = jsonDecode(response.body);
      final Map<String, dynamic> byId = {};
      for (final r in (decoded['results'] as List? ?? [])) {
        final id = r['id']?.toString();
        if (id != null) byId[id] = r;
      }
      return byId;
    } catch (_) {
      return {};
    }
  }
}

// NEW — SujhaavFayda1 item 3. Ad/interstitial cadence used to be two
// hardcoded Dart consts baked in at compile time (no way to A/B test
// without an app release). Now fetched once per session from the
// backend (see FeedAdConfigAPIView) with the old hardcoded values as
// the fallback if the call fails or hasn't completed yet.
class FeedConfigService {
  static Future<Map<String, int>?> getFeedAdConfig() async {
    try {
      final url = Uri.parse("${Api.baseUrl}/post/feed/ad-config/");
      final response = await http.get(url).timeout(kApiTimeout);
      if (response.statusCode != 200) return null;
      final data = jsonDecode(response.body);
      final adEvery = data['ad_every_posts'];
      final interstitialEvery = data['interstitial_every_posts'];
      if (adEvery is! int || interstitialEvery is! int) return null;
      return {'ad_every_posts': adEvery, 'interstitial_every_posts': interstitialEvery};
    } catch (_) {
      return null;
    }
  }
}

// ===================== HOME EXTRAS (Task 4) =====================
//
// Status per LEARNSCROLL_LIVECLASS.md / campus_app_design.md (docs upload,
// Sept 2026):
//   - getInviteInfo()/getReferLink() → ✅ Task 5 — REAL endpoints confirmed,
//     wired below (was assumed coins_per_referral/invite_link shape before;
//     see InviteEarnModel/ReferLinkModel doc comments for the real, class-
//     level ongoing-commission shape).
//   - getMyClassrooms() → ✅ Task 2 — REAL endpoint confirmed
//     (GET /liveclass/classrooms/?mine=true), wired below.
//   - getLiveNow() → ✅ Task 3 — REAL endpoint confirmed (a new backend
//     action, GET /liveclass/sessions/live-now/, was added since no
//     existing endpoint matched the shape this screen needs — see
//     ClassSessionViewSet.live_now() in views.py), wired below.
//   - getStories() → MOVED to `StoryService.getStories()` in
//     `post/services/story_service.dart`, alongside story create + mark-
//     viewed (multipart upload, same pattern as comment_service.dart).
//     `StoryModel`/`StoryGroup`/`groupStories()` moved to
//     `post/models/story_model.dart`.
class HomeExtrasService {
  // ✅ CONFIRMED (Task 2) — GET /liveclass/classrooms/?mine=true, caller ki
  // khud ki enrolled/teaching classrooms. Response DRF-paginated ho sakta
  // hai ({"results":[...]}) ya plain list — dono handle kiya hai neeche.
  //
  // Cache-first, short-TTL (2 min) — `HomeFeedService.getHomeFeed()` wala hi
  // pattern reuse kiya hai: cache fresh ho to turant wahi dikhao + background
  // me silently refresh karo; stale/missing ho to seedha API call karo.
  // Short TTL isliye kyunki `isLive`/`isActive` jaldi badal sakte hain — feed
  // jaisa indefinite cache yahan stale "LIVE" badge dikha sakta tha.
  static const String _classroomsCacheKey = 'cached_my_classrooms_v1';
  static const Duration _classroomsCacheTtl = Duration(minutes: 2);

  static Future<List<ClassroomModel>> getMyClassrooms() async {
    final prefs = await SharedPreferences.getInstance();
    final cached = prefs.getString(_classroomsCacheKey);
    if (cached != null && cached.isNotEmpty) {
      try {
        final decoded = jsonDecode(cached) as Map<String, dynamic>;
        final cachedAt = DateTime.tryParse(decoded['cached_at'] as String? ?? '');
        final data = decoded['data'] as List? ?? [];
        if (cachedAt != null && DateTime.now().difference(cachedAt) < _classroomsCacheTtl) {
          _getMyClassroomsFromAPI().catchError((e) => <ClassroomModel>[]); // background refresh
          return data.map((e) => ClassroomModel.fromJson(e as Map<String, dynamic>)).toList();
        }
      } catch (e) {
        print("Classrooms cache parse error: $e"); // corrupt cache — fresh fetch neeche ho jaayega
      }
    }
    return await _getMyClassroomsFromAPI();
  }

  static Future<List<ClassroomModel>> _getMyClassroomsFromAPI() async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/liveclass/classrooms/?mine=true");
    final response = await http
        .get(url, headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"})
        .timeout(kApiTimeout);
    if (response.statusCode == 200) {
      final decoded = jsonDecode(response.body);
      final List raw = decoded is Map ? (decoded['results'] as List? ?? []) : (decoded as List);
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(
        _classroomsCacheKey,
        jsonEncode({'cached_at': DateTime.now().toIso8601String(), 'data': raw}),
      );
      return raw.map((e) => ClassroomModel.fromJson(e as Map<String, dynamic>)).toList();
    } else {
      throw Exception('Failed to load classrooms: ${response.statusCode}');
    }
  }

  // ✅ CONFIRMED (Task 2) — GET /liveclass/classrooms/{id}/. Detail response
  // is a superset of the list-row fields (id, name, is_live, is_active) that
  // `ClassroomModel.fromJson` already parses; classroom-detail screen ke
  // liye abhi bas yehi confirmed fields use kiye hain. Richer fields (agar
  // koi ho — teacher, schedule, stats wagera) is doc-set me confirm nahi
  // hue, isliye display nahi kiye — backend confirm kare to
  // `ClassroomModel`/us screen ko expand kar sakte hain.
  static Future<ClassroomModel> getClassroomDetail(String id) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/liveclass/classrooms/$id/");
    final response = await http
        .get(url, headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"})
        .timeout(kApiTimeout);
    if (response.statusCode == 200) {
      return ClassroomModel.fromJson(jsonDecode(response.body));
    } else {
      throw Exception('Failed to load classroom: ${response.statusCode}');
    }
  }

  // ✅ CONFIRMED (Task 3) — GET /liveclass/sessions/live-now/, NEW backend
  // action (ClassSessionViewSet.live_now, see views.py) built specifically
  // for this card row — neither of the two candidates the task doc flagged
  // (`sessions/?status=live` cross-classroom filter, or a per-classroom
  // loop off `classrooms/{id}/`) already existed, so this one was added.
  // Cross-classroom (every classroom the caller can access, same as a
  // no-`?classroom=` call to plain `sessions/`), LIVE-status only,
  // `?limit=` capped server-side at 20 — no client-side cache here (unlike
  // getMyClassrooms above): "who's live right now" is exactly the kind of
  // data a short-TTL cache would go stale on fastest, and this row is meant
  // to be refetched on every home-resume per the task doc's polling note.
  static Future<List<LiveClassModel>> getLiveNow({int limit = 10}) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/liveclass/sessions/live-now/?limit=$limit");
    final response = await http
        .get(url, headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"})
        .timeout(kApiTimeout);
    if (response.statusCode == 200) {
      final List raw = jsonDecode(response.body) as List;
      return raw.map((e) => LiveClassModel.fromJson(e as Map<String, dynamic>)).toList();
    } else {
      throw Exception('Failed to load live-now sessions: ${response.statusCode}');
    }
  }

  // MOVED — getStories()/_getStoriesFromAPI() (incl. the stale-while-
  // revalidate cache pattern that used to be documented here) now live as
  // `StoryService.getStories()` in `post/services/story_service.dart`.

  // ✅ CONFIRMED (Task 5) — GET /liveclass/referrals/class-referral-summary/.
  // FIX: was hitting `referrals/my-code/` — that's the separate, signup-
  // level "invite a friend to the app" program (flat REFERRAL_BONUS_COINS
  // per signup), a real endpoint but the WRONG one for this strip. The
  // task doc's own plan (HOME_BACKEND_INTEGRATION_TASKS.md, Task 5, step 1)
  // calls for `class-referral-summary/` here instead — the global rollup of
  // the CLASS-LEVEL commission program (ReferralViewSet.class_referral_
  // summary in views.py) this strip is actually meant to represent.
  static Future<InviteEarnModel> getInviteInfo() async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/liveclass/referrals/class-referral-summary/");
    final response = await http
        .get(url, headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"})
        .timeout(kApiTimeout);
    if (response.statusCode == 200) {
      return InviteEarnModel.fromJson(jsonDecode(response.body));
    } else {
      throw Exception('Failed to load invite info: ${response.statusCode}');
    }
  }

  // NEW (Task 5) — GET /liveclass/classrooms/{id}/refer-link/. Generates
  // (or re-fetches — it's idempotent server-side, same code every call)
  // the caller's own share-link for ONE classroom, plus that classroom's
  // current %commission so the UI can show "earn X%" before the person
  // shares. Only valid for a classroom with `referral_enabled == true`
  // (see ClassroomModel) — the backend 400s "This classroom doesn't have
  // referrals enabled." otherwise, surfaced here as a plain Exception.
  static Future<ReferLinkModel> getReferLink(String classroomId) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final url = Uri.parse("${Api.baseUrl}/liveclass/classrooms/$classroomId/refer-link/");
    final response = await http
        .get(url, headers: {"Authorization": "Bearer $token", "Content-Type": "application/json"})
        .timeout(kApiTimeout);
    if (response.statusCode == 200) {
      return ReferLinkModel.fromJson(jsonDecode(response.body));
    } else if (response.statusCode == 400) {
      throw Exception("This classroom doesn't have referrals enabled.");
    } else {
      throw Exception('Failed to load referral link: ${response.statusCode}');
    }
  }
}