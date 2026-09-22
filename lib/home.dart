import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter/foundation.dart' show kDebugMode;
import 'package:cached_network_image/cached_network_image.dart';
import 'package:timeago/timeago.dart' as timeago;
import 'package:video_player/video_player.dart';
import 'package:share_plus/share_plus.dart';
import 'package:visibility_detector/visibility_detector.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:image_picker/image_picker.dart';
import 'package:open_filex/open_filex.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:easy_audience_network_plus/easy_audience_network.dart';
import 'profile/screens/profile.dart';
import 'profile/screens/target_profile.dart';
import 'profile/api_service.dart' as ProfileApi;
import 'search/search.dart';
import 'post/screens/new_post.dart';
import 'post/widgets/comment_sheet.dart';
import 'services/home_api_model_service.dart';
import 'services/auth_service.dart';
import 'services/session_service.dart';
import 'message/screens/conversations_screen.dart';
import 'liveclass/screens/explore_screen.dart';
import 'campus/screens/campus_screen.dart';
import 'wallet/screens/wallet_screen.dart';
import 'widgets/skeletons.dart';
import 'widgets/error_widgets.dart';
import 'widgets/ls_ui.dart';
import 'assignments/screens/assignments_screen.dart';
import 'testseries/screens/test_series_screen.dart';
import 'l10n/app_localizations.dart';
import 'notifications/screens/notifications_screen.dart';
import 'notifications/services/notification_service.dart';
import 'post/screens/story_viewer_screen.dart';
import 'post/services/story_service.dart';
import 'post/models/story_model.dart';
import 'post/widgets/story_caption_sheet.dart';
import 'liveclass/screens/classroom_detail_screen.dart';
import 'liveclass/liveclass_bootstrap.dart';
import 'post/screens/post_list_screen.dart';

// ⚠️ PATHS — ye file `lib/home.dart` hai (lib/home/ folder ke andar NAHI),
// exactly jaisa `main.dart` ka `import 'home.dart';` batata hai. Isliye har
// import `lib/` se relative hai, koi `../` nahi. Pichle version me `../`
// laga hua tha (folder-wali assumption) — wo lib ke BAHAR point karta tha
// aur compile hi nahi hota.
// theme_service.dart / language_service.dart bhi lib/ root pe hain (main.dart
// unhe `import 'theme_service.dart'` se hi uthata hai), services/ folder me
// nahi — ye bhi isi pass me theek hua.

/// TODO: fill in the real Meta Audience Network native-ad placement ID for
/// production before shipping (Meta dashboard → your app → placements).
/// Used only in release builds — debug builds use NativeAd.testPlacementId.
const String _prodNativeAdPlacementId = 'REPLACE_WITH_PROD_PLACEMENT_ID';

// ============================================================
// i18n FIX (Task 5.8 gap) — the .arb files weren't part of this review, so
// the hardcoded strings below were swapped for AppLocalizations getters/
// methods that don't exist yet. Add these to app_en.arb (and every other
// locale, e.g. app_hi.arb) before this builds:
//   couldNotOpenClassroom          "Could not open this classroom."
//   couldNotOpenClass              "Could not open this class."
//   camera                         "Camera"
//   recordVideo                    "Record video"
//   chooseFromGallery              "Choose from gallery"
//   yourStory                      "Your Story"
//   couldNotUploadStory            "Could not upload your story."
//   noClassroomsWithReferrals      "None of your classrooms have referrals turned on yet."
//   couldNotCreateReferralLink     "Could not create your referral link."
//   open                           "Open"
//   inviteEarnedSoFar(earned, pending)       "You've earned ₹{earned} so far — ₹{pending} on the way"
//   inviteShareClassroomLink                 "Share a classroom's link — earn a daily % commission for as long as they stay enrolled"
//   referralCommissionEarned(percent, name)  "You'll earn {percent}% of the daily fee for every student you refer to \"{name}\"."
//   openFailed(error)              "Open failed: {error}"
//   failedWithError(error)         "Failed: {error}"
//   filesSelectedCount(count)      "{count} selected"
// ============================================================
// 🔥 NAYE MODULES — quick actions aur feed interstitial ab inhi pe jaate hain.
// Task 1 — Notifications badge + screen (real `core` app endpoint, backend
// already production-ready — koi naya backend kaam nahi chahiye).
// Task 4 — Stories (real `post` app endpoint confirmed — post_app.md §16.2 —
// earlier "no Story concept exists" note was wrong, written before that
// section was cross-checked).
// Task 2 — Classroom switcher (real `?mine=true` endpoint, backend
// production-ready).
// FIX (wrong path — pointed at a different, never-actually-used
// `lib/classrooms/screens/classroom_detail_screen.dart`): the real,
// already-built classroom-detail screen (Screen 2 of the liveclass
// module — my-pass/stats/wishlist calls, owner/active/expired/pending/
// none states, Enter Class / Renew / Request-to-Join bars, all the
// manage-screen navigation) lives at `lib/liveclass/screens/
// classroom_detail_screen.dart`, same folder as `explore_screen.dart`
// above. Importing that one instead of duplicating/using a stray stub.

// ============================================================
// LEARNSCROLL HOME — learnscroll_home_final.html ka 1:1 Flutter port.
//
// HTML → Flutter mapping (design tokens theme_service.dart ke ColorScheme
// se aate hain, yahan koi hex literal nahi — Task 5.8):
//   --bg      → colorScheme.background      (#FFFFFF / #0F0D18)
//   --surface → colorScheme.surface         (#F6F4FF / #1B1730)
//   --surface2→ colorScheme.surfaceVariant  (#EDE8FF / #241F3D)
//   --primary → colorScheme.primary         (#5B3DF6 / #8B7CFF)
//   --accent  → colorScheme.secondary       (#FF6B4A / #FF8A68)
//   --ink     → colorScheme.onSurface       (#1A1625 / #F3F1FF)
//   --muted   → colorScheme.onSurfaceVariant(#847FA0 / #9891B8)
//   --border  → colorScheme.outlineVariant  (#ECE9FB / #2A244A)
//
// Sections (HTML class → method):
//   .brand-row      → _buildBrandRow()          (SliverAppBar, bg = page bg)
//   .search-bar     → _buildLsSearchBar()
//   .classroom-row  → _buildClassroomSwitcher()
//   .invite-strip   → _buildInviteStrip()
//   .stories        → _buildStories()
//   .section(live)  → _buildLiveNow()
//   .quick-grid     → _buildQuickActionsGrid()
//   .feed-title     → _buildFeedTitle()
//   .post-card      → _buildPostCard()
//   .interstitial   → _buildFeedInterstitial()   (Task 6)
//   .bottomnav      → LsBottomNav (widgets/ls_ui.dart, shared)
// ============================================================

// `kLsPad` (18) aur `kLsRadius` ab `widgets/ls_ui.dart` me hain — wahi
// constants assignments aur test-series screens bhi use karti hain, taaki
// teeno modules ka spacing/rhythm ek jaisa rahe.

/// `.post-card` ke beech ad aur interstitial ka cadence (Task 6.2).
/// Ad pehle check hota hai, phir interstitial — dono kabhi ek hi slot pe
/// nahi aate.
///
/// SujhaavFayda1 item 3 — ab `const` nahi, plain mutable top-level vars.
/// Ye hardcoded defaults hain; `_HomeScreenState._loadFeedAdConfig()`
/// app-session start pe backend se real values fetch karke inhe override
/// kar deta hai (see FeedConfigService.getFeedAdConfig / FeedAdConfigAPIView
/// on the backend) — taaki cadence A/B-test ho sake bina app-release ke.
/// Fetch fail ho ya abhi complete na hua ho to yehi defaults chalte rehte hain.
int kAdEveryPosts = 5;
int kInterstitialEveryPosts = 18;

class HomeScreen extends StatefulWidget {
  /// Bottom-nav tab index. 0 = Home, 1 = Search, 2 = Profile.
  /// ⚠️ Ye indices jaan-boojh kar same rakhe gaye hain — dusri screens
  /// (conversations_screen.dart waghera) `HomeScreen(initialIndex: 2)` se
  /// profile pe jump karti hain. Naya bottom nav 5 items dikhata hai par
  /// Classes/Chat push-routes hain, tab nahi — details LsBottomNav call site me.
  final int initialIndex;
  const HomeScreen({super.key, this.initialIndex = 0});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  late int _selectedIndex = widget.initialIndex;

  List<PostModel> _posts = [];
  List<_FeedSlot> _slots = const [];
  bool _isLoading = true;
  bool _isLoadingMore = false;
  bool _feedFailed = false;
  int _currentPage = 1;
  bool _hasMore = true;

  final ScrollController _scrollController = ScrollController();
  final Map<String, String> _emojiMap = {'like': '👍', 'confuse': '🤔', 'wrong': '❗', 'imp': '⭐', 'explain': '💡'};
  // Reaction colors jaan-boojh kar fixed brand accents hain (Facebook jaisa) —
  // "like" light aur dark dono me same blue rehna chahiye.
  final Map<String, Color> _emojiColor = {
    'like': const Color(0xFF1877F2),
    'confuse': const Color(0xFFF7B928),
    'wrong': const Color(0xFFE0245E),
    'imp': const Color(0xFFFFAD33),
    'explain': const Color(0xFF45BD62),
  };
  Set<String> _savedIds = {};
  String? _myUsername;
  // Task 4 — Stories: needed to tell "your own ring" apart from everyone
  // else's in `_buildStories()` (own ring shows an add-button when empty,
  // others never do).
  String? _myUserId;
  bool _storyUploadBusy = false;
  // TASK 2 FOLLOW-UP — real % now that StoryService.createStory reports
  // it (see story_service.dart); 0 means "not uploading" or "just started,
  // no bytes acked yet" — the tile falls back to an indeterminate spinner
  // in that case, same as the comment sheet does before its first byte.
  double _storyUploadProgress = 0;

  // Top sections ka state (Task 4/5).
  List<ClassroomModel> _classrooms = [];
  List<LiveClassModel> _liveClasses = [];
  List<StoryModel> _stories = [];
  InviteEarnModel? _inviteInfo;
  bool _extrasLoading = true;
  // NEW (Task 5) — guards the invite strip's share tap: fetching
  // classrooms/{id}/refer-link/ is a real network call (not instant, like
  // the old `_inviteInfo!.inviteLink` was), so a double-tap could fire it
  // twice while the first is still in flight.
  bool _inviteShareBusy = false;

  // ✅ Task 1 — real endpoint (`GET core/notifications/unread-count/`,
  // `core` app) wired via `_loadUnreadCount()`. Skeleton nahi, seedha
  // silent-fail (fail ho to purana count hi dikhta rahe, crash nahi).
  int _unreadNotifications = 0;

  // Task 8 — ek hi baar redirect chale, chahe notifier kitni baar bhi fire ho.
  bool _sessionExpiryHandled = false;

  // SujhaavFayda1 item 2 — live reaction/comment counts via polling (see
  // _pollLiveCounts). Not a WebSocket — see the note on
  // FeedConfigService/PostCountsAPIView for why.
  Timer? _countsPollTimer;

  @override
  void initState() {
    super.initState();
    _initAds();
    _loadFeed();
    _loadSaved();
    _loadMyUsername();
    _loadHomeExtras();
    _loadUnreadCount();
    _loadMyUserId();
    _loadFeedAdConfig(); // SujhaavFayda1 item 3
    _scrollController.addListener(_onScroll);
    homeSessionExpiredNotifier.addListener(_onSessionExpired);
    if (homeSessionExpiredNotifier.value) _onSessionExpired();
    // SujhaavFayda1 item 2 — poll every 20s while this screen is alive.
    // Kept deliberately simple (no visibility/lifecycle awareness) — a
    // single lightweight bulk-counts call every 20s is cheap enough not
    // to need pausing on backgrounding for this app's scale; add a
    // WidgetsBindingObserver here later if that stops being true.
    _countsPollTimer = Timer.periodic(const Duration(seconds: 20), (_) => _pollLiveCounts());
  }

  @override
  void dispose() {
    homeSessionExpiredNotifier.removeListener(_onSessionExpired);
    _countsPollTimer?.cancel();
    _scrollController.dispose();
    super.dispose();
  }

  // Bug fix (production-readiness): testMode was hardcoded `true`, so a
  // release build would never serve real ads / earn revenue. Now tied to
  // kDebugMode — debug builds still use Meta's test mode, release builds
  // request real ads.
  Future<void> _initAds() async => EasyAudienceNetwork.init(testMode: kDebugMode);

  // ============================================================
  // TASK 8 — SESSION EXPIRED → 3 SEC → LOGIN
  // ============================================================
  void _onSessionExpired() {
    if (!homeSessionExpiredNotifier.value) return;
    if (_sessionExpiryHandled) return;
    _sessionExpiryHandled = true;
    _runSessionExpiryCountdown();
  }

  Future<void> _runSessionExpiryCountdown() async {
    if (mounted) {
      final cs = Theme.of(context).colorScheme;
      final l10n = AppLocalizations.of(context)!;
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(
          content: Row(children: [
            SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: cs.onError)),
            const SizedBox(width: 12),
            Expanded(child: Text(l10n.sessionExpiredRedirecting, style: TextStyle(color: cs.onError))),
          ]),
          backgroundColor: cs.error,
          behavior: SnackBarBehavior.floating,
          duration: const Duration(seconds: 3),
        ));
    }
    await Future.delayed(const Duration(seconds: 3));
    try {
      await AuthService.logout();
    } catch (_) {}
    SessionService.reset();
    _sessionExpiryHandled = false;
    navigatorKey.currentState?.pushNamedAndRemoveUntil('/login', (_) => false);
  }

  // ============================================================
  // DATA
  // ============================================================

  // Task 10.5 — threshold 300 → 700px. 300 pe user ko spinner dikh jaata
  // tha; 700 pe agla page aam taur pe pehle hi aa chuka hota hai.
  void _onScroll() {
    if (_scrollController.position.pixels >= _scrollController.position.maxScrollExtent - 700) {
      if (!_isLoadingMore && _hasMore) _loadMore();
    }
  }

  Future<void> _loadSaved() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() => _savedIds = (prefs.getStringList('saved_posts') ?? []).toSet());
  }

  // Task 4 — Stories: `AuthService.getUserId()` same call the comment-sheet
  // already uses (`_getMyId()` at the bottom of this file) to tell "my own"
  // content apart from everyone else's.
  Future<void> _loadMyUserId() async {
    final id = await AuthService.getUserId();
    if (mounted) setState(() => _myUserId = id);
  }

  // Har section apna try/catch leke chalta hai — ek fail ho to baaki dikhte
  // rahein.
  Future<void> _loadHomeExtras() async {
    if (mounted) setState(() => _extrasLoading = true);
    // SujhaavFayda1 item 5 — getInviteInfo() used to be a separate
    // sequential `await` after this Future.wait, so home load paid for
    // classrooms/liveNow/stories/invite as 3-parallel-then-1-sequential
    // instead of all 4 in parallel. Folded in here (with the same
    // catchError-to-null fallback it already had) so all four fire at once.
    final results = await Future.wait([
      HomeExtrasService.getMyClassrooms().catchError((e) => <ClassroomModel>[]),
      HomeExtrasService.getLiveNow().catchError((e) => <LiveClassModel>[]),
      StoryService.getStories().catchError((e) => <StoryModel>[]),
      HomeExtrasService.getInviteInfo().then<InviteEarnModel?>((v) => v).catchError((e) => null),
    ]);
    if (!mounted) return;
    setState(() {
      _classrooms = results[0] as List<ClassroomModel>;
      _liveClasses = results[1] as List<LiveClassModel>;
      _stories = results[2] as List<StoryModel>;
      _inviteInfo = results[3] as InviteEarnModel?;
      _extrasLoading = false;
    });
  }

  // Task 1 — bell-icon badge. Skeleton nahi, seedha silent-fail: fail ho to
  // purana count hi dikhta rahe (jhoota "0" flash na ho), crash bilkul nahi.
  Future<void> _loadUnreadCount() async {
    try {
      final count = await NotificationService.instance.getUnreadCount();
      if (!mounted) return;
      setState(() => _unreadNotifications = count);
    } catch (_) {
      // silent — purana count as-is rehne do.
    }
  }

  // SujhaavFayda1 item 3 — once-per-session fetch of the ad/interstitial
  // cadence from the backend. Silent-fail like the section above: if this
  // never completes (or the endpoint 500s), the hardcoded kAdEveryPosts/
  // kInterstitialEveryPosts defaults just keep being used, feed never
  // breaks because of it.
  Future<void> _loadFeedAdConfig() async {
    final cfg = await FeedConfigService.getFeedAdConfig();
    if (cfg == null || !mounted) return;
    setState(() {
      kAdEveryPosts = cfg['ad_every_posts'] ?? kAdEveryPosts;
      kInterstitialEveryPosts = cfg['interstitial_every_posts'] ?? kInterstitialEveryPosts;
      _rebuildFeedSlots(); // naya cadence turant reflect ho feed me
    });
  }

  // SujhaavFayda1 item 2 — "feed zinda feel", pull-to-refresh pe depend
  // nahi. Timer.periodic (initState) har 20s pe isko call karta hai;
  // currently-loaded posts (max 100, backend cap se match) ke reaction +
  // comment counts bulk me refresh karta hai. `myReaction` isse nahi
  // chhedte — sirf counts (dusron ki reactions/comments se change hote
  // hain, apni reaction to humein pata hi hai).
  Future<void> _pollLiveCounts() async {
    if (!mounted || _posts.isEmpty) return;
    final ids = _posts.take(100).map((p) => p.id).toList();
    final byId = await HomeFeedService.getPostCounts(ids);
    if (!mounted || byId.isEmpty) return;
    setState(() {
      for (final post in _posts) {
        final data = byId[post.id];
        if (data == null) continue;
        final c = (data['counts'] as Map?) ?? {};
        post.likeCount = (c['like'] as int?) ?? post.likeCount;
        post.confuseCount = (c['confuse'] as int?) ?? post.confuseCount;
        post.wrongCount = (c['wrong'] as int?) ?? post.wrongCount;
        post.impCount = (c['imp'] as int?) ?? post.impCount;
        post.explainCount = (c['explain'] as int?) ?? post.explainCount;
        post.likesCount = (c['total'] as int?) ?? post.likesCount;
        post.commentsCount = (data['comments_count'] as int?) ?? post.commentsCount;
      }
    });
  }

  Future<void> _loadFeed({bool refresh = false}) async {
    if (refresh) {
      setState(() {
        _isLoading = true;
        _feedFailed = false;
        _currentPage = 1;
      });
      try {
        final feed = await HomeFeedService.refreshFeed(page: 1, pageSize: 20);
        if (!mounted) return;
        setState(() {
          _posts = feed.results;
          _isLoading = false;
          _hasMore = feed.next != null;
          _savedIds.addAll(feed.results.where((p) => p.isSaved).map((p) => p.id));
          _rebuildFeedSlots();
        });
      } catch (e) {
        if (mounted) {
          setState(() {
            _isLoading = false;
            _feedFailed = _posts.isEmpty;
          });
        }
      }
      return;
    }

    try {
      final cached = await HomeFeedService.getCachedFeed();
      if (cached != null && mounted && _posts.isEmpty) {
        setState(() {
          _posts = cached.results;
          _isLoading = false;
          _hasMore = cached.next != null;
          _savedIds.addAll(cached.results.where((p) => p.isSaved).map((p) => p.id));
          _rebuildFeedSlots();
        });
      }
      final feed = await HomeFeedService.getHomeFeed(page: 1, pageSize: 20);
      if (!mounted) return;
      setState(() {
        _posts = feed.results;
        _isLoading = false;
        _feedFailed = false;
        _hasMore = feed.next != null;
        _currentPage = 1;
        _savedIds.addAll(feed.results.where((p) => p.isSaved).map((p) => p.id));
        _rebuildFeedSlots();
      });
    } catch (e) {
      // Cache already dikh raha ho to error state mat dikhao — silently
      // stale data ke saath rehna better hai (Instagram jaisa).
      if (mounted) {
        setState(() {
          _isLoading = false;
          _feedFailed = _posts.isEmpty;
        });
      }
    }
  }

  Future<void> _loadMore() async {
    if (_isLoadingMore || !_hasMore) return;
    setState(() => _isLoadingMore = true);
    _currentPage++;
    try {
      final feed = await HomeFeedService.getHomeFeed(page: _currentPage, pageSize: 20);
      if (!mounted) return;
      setState(() {
        _posts.addAll(feed.results);
        _isLoadingMore = false;
        _hasMore = feed.next != null;
        _savedIds.addAll(feed.results.where((p) => p.isSaved).map((p) => p.id));
        _rebuildFeedSlots();
      });
      _prefetchNextPageImages(feed.results); // SujhaavFayda1 item 1
    } catch (e) {
      _currentPage--;
      if (mounted) setState(() => _isLoadingMore = false);
    }
  }

  // SujhaavFayda1 item 1 — abhi tak sirf JSON data yahan fetch hoti thi;
  // images tab tak load nahi hoti jab tak ListView.builder us post ka card
  // actually build na kare (user scroll karke wahan pahunche). Ab
  // _loadMore() ke turant baad — 700px scroll-threshold pe already trigger
  // hota hai (_onScroll) — naye page ka pehla media item (jo card khulte
  // hi dikhta hai) aur author avatar precache kar dete hain, taaki jab tak
  // user wahan scroll karke pahunche, image cache se turant mile, network
  // se nahi. Poora carousel prefetch nahi karte (sirf pehla item per
  // post) — bandwidth vs perceived-smoothness trade-off, Instagram/FB bhi
  // yahi karte hain. Har precache apna error khud absorb karta hai — ek
  // bhi image fail ho to baaki par asar nahi padna chahiye.
  void _prefetchNextPageImages(List<PostModel> posts) {
    if (!mounted) return;
    for (final post in posts) {
      final avatar = post.user.profilePicture;
      if (avatar != null && avatar.isNotEmpty) {
        precacheImage(CachedNetworkImageProvider(avatar), context).catchError((_) {});
      }
      if (post.media.isNotEmpty) {
        final first = post.media.first;
        final url = first.mediaType == 'video' ? first.thumbnail : first.file;
        if (url != null && url.isNotEmpty) {
          precacheImage(CachedNetworkImageProvider(url), context).catchError((_) {});
        }
      }
    }
  }

  // ------------------------------------------------------------
  // FEED SLOTS — Task 6.2
  //
  // Purana code builder ke andar modular arithmetic karta tha
  // (`index % 6 == 5` se ad, par childCount `~/5` se) — dono formulas
  // match nahi karte the, isliye lambi feed me aakhri posts kat jaati thi
  // aur beech me blank SizedBox aate the. Ab slot-list pehle se banti hai:
  // builder sirf lookup karta hai, koi index-math nahi. Interstitial add
  // karna bhi isi wajah se trivial ho gaya.
  // ------------------------------------------------------------
  void _rebuildFeedSlots() {
    final slots = <_FeedSlot>[];
    int sinceAd = 0;
    int sinceInterstitial = 0;
    for (int i = 0; i < _posts.length; i++) {
      slots.add(_FeedSlot.post(i));
      sinceAd++;
      sinceInterstitial++;
      if (sinceAd >= kAdEveryPosts) {
        slots.add(const _FeedSlot(_FeedSlotKind.ad));
        sinceAd = 0;
        continue; // priority: ek slot pe ad + interstitial dono nahi
      }
      if (sinceInterstitial >= kInterstitialEveryPosts) {
        slots.add(const _FeedSlot(_FeedSlotKind.interstitial));
        sinceInterstitial = 0;
      }
    }
    _slots = slots;
  }

  Future<void> _loadMyUsername() async {
    try {
      final d = await ProfileApi.ApiService.getProfile();
      _myUsername = d.username;
    } catch (_) {
      try {
        final t = await AuthService.getValidToken();
        if (t != null) {
          final p = base64.normalize(t.split('.')[1]);
          _myUsername = jsonDecode(utf8.decode(base64Url.decode(p)))['username']?.toString();
        }
      } catch (_) {}
    }
  }

  // ============================================================
  // ACTIONS
  // ============================================================

  Future<void> _goToProfile(String username) async {
    if (username.trim().isEmpty) return;
    if (_myUsername == null) await _loadMyUsername();
    final isMe = _myUsername != null && _myUsername!.toLowerCase().trim() == username.toLowerCase().trim();
    if (!mounted) return;
    if (isMe) {
      if (Navigator.canPop(context)) Navigator.pop(context);
      await Future.delayed(const Duration(milliseconds: 100));
      if (!mounted) return;
      setState(() => _selectedIndex = 2);
    } else {
      Navigator.push(context, MaterialPageRoute(builder: (_) => TargetProfilePage(username: username)));
    }
  }

  Future<void> _toggleSave(PostModel post) async {
    HapticFeedback.selectionClick(); // Task 15.3
    final postId = post.id;
    final wasSaved = post.isSaved;
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      post.isSaved = !wasSaved;
      if (post.isSaved) {
        _savedIds.add(postId);
        post.savesCount++;
      } else {
        _savedIds.remove(postId);
        if (post.savesCount > 0) post.savesCount--;
      }
    });
    await prefs.setStringList('saved_posts', _savedIds.toList());
    try {
      final res = await HomeFeedService.toggleSave(postId);
      final bool apiIsSaved = res['is_saved'] ?? (res['status'] == 'saved');
      final int? apiCount = res['saves_count'];
      if (!mounted) return;
      setState(() {
        post.isSaved = apiIsSaved;
        if (apiCount != null) post.savesCount = apiCount;
        if (apiIsSaved) {
          _savedIds.add(postId);
        } else {
          _savedIds.remove(postId);
        }
      });
      await prefs.setStringList('saved_posts', _savedIds.toList());
    } catch (e) {
      if (!mounted) return;
      setState(() {
        post.isSaved = wasSaved;
        if (wasSaved) {
          _savedIds.add(postId);
        } else {
          _savedIds.remove(postId);
        }
      });
      _snack(AppLocalizations.of(context)!.saveFailed);
    }
  }

  Future<void> _handleReaction(PostModel post, String reaction) async {
    HapticFeedback.lightImpact(); // Task 15.3
    final old = post.myReaction;
    final idx = _posts.indexWhere((p) => p.id == post.id);
    if (idx == -1) return;
    setState(() {
      if (old == reaction) {
        _posts[idx].myReaction = null;
        _posts[idx].likesCount--;
      } else {
        if (old == null) _posts[idx].likesCount++;
        _posts[idx].myReaction = reaction;
      }
    });
    try {
      final res = await HomeFeedService.toggleReaction(post.id, reaction);
      // Bug fix: backend response's counts map isn't guaranteed to have every
      // key (or even be present) — a missing/null value used to crash with a
      // type-cast error since these are non-nullable ints on PostModel.
      final c = (res['counts'] as Map?) ?? {};
      if (!mounted) return;
      setState(() {
        _posts[idx].likeCount = (c['like'] as int?) ?? 0;
        _posts[idx].confuseCount = (c['confuse'] as int?) ?? 0;
        _posts[idx].wrongCount = (c['wrong'] as int?) ?? 0;
        _posts[idx].impCount = (c['imp'] as int?) ?? 0;
        _posts[idx].explainCount = (c['explain'] as int?) ?? 0;
        _posts[idx].likesCount = (c['total'] as int?) ?? 0;
        _posts[idx].myReaction = res['my_reaction'];
      });
    } catch (e) {
      if (mounted) setState(() => _posts[idx].myReaction = old);
    }
  }

  void _sharePost(PostModel p) {
    String t = p.content ?? '';
    if (p.media.isNotEmpty) t += '\n\n${p.media.first.file}';
    Share.share(t);
  }

  void _openCommentSheet(PostModel post) {
    final cs = Theme.of(context).colorScheme;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
        child: Container(
          decoration: BoxDecoration(
              color: cs.surface, borderRadius: const BorderRadius.vertical(top: Radius.circular(20))),
          child: CommentBottomSheet(
            postId: post.id,
            postOwnerId: post.user.id.toString(),
            initialCommentsCount: post.commentsCount,
            onCommentAdded: () => setState(() => post.commentsCount++),
            onGoToProfile: _goToProfile,
          ),
        ),
      ),
    );
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(msg), behavior: SnackBarBehavior.floating));
  }

  void _openSearch() {
    HapticFeedback.selectionClick();
    setState(() => _selectedIndex = 1);
  }

  Future<void> _openNewPost() async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => const NewPost()));
    _loadFeed(refresh: true);
  }

  void _openExplore() => Navigator.push(context, MaterialPageRoute(builder: (_) => const ExploreScreen()));
  void _openChat() => Navigator.push(context, MaterialPageRoute(builder: (_) => const ConversationsScreen()));
  void _openAssignments() =>
      Navigator.push(context, MaterialPageRoute(builder: (_) => const AssignmentsScreen()));
  void _openTestSeries() =>
      Navigator.push(context, MaterialPageRoute(builder: (_) => const TestSeriesScreen()));

  // ============================================================
  // SHARED LITTLE BUILDERS
  // ============================================================

  /// `.section-title` — HTML heading font (Sora).
  ///
  /// `fontFamily: 'Sora'` seedha nahi likha ja sakta: pubspec me koi `Sora`
  /// family register nahi hai, to wo silently system font pe gir jaata.
  /// `LsType.head()` google_fonts (already dependency) se asli Sora laata
  /// hai — dekho theme_service.dart ka sabse neeche wala note.
  Widget _sectionTitle(String text, ColorScheme cs) => Text(text, style: LsType.head(context));

  /// `.section-label` — chhota uppercase muted label.
  Widget _sectionLabel(String text, ColorScheme cs) => Padding(
        padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 7),
        child: Text(
          text.toUpperCase(),
          style: TextStyle(
              fontSize: 10.5, fontWeight: FontWeight.w700, letterSpacing: .35, color: cs.onSurfaceVariant),
        ),
      );

  /// Quick-action aur avatar accents theme se derive karte hain — HTML ke
  /// green (#1F9D6C) aur amber (#C99A44) ColorScheme me nahi hain, isliye
  /// primary/accent ka hue rotate karke nikale gaye hain. Fayda: brand color
  /// badlo ya dark mode me jao, ye chaaron apne aap re-derive ho jaate hain.
  Color _hueShift(Color base, double degrees) {
    final hsl = HSLColor.fromColor(base);
    return hsl.withHue((hsl.hue + degrees) % 360).toColor();
  }

  Color get _lsGreen => _hueShift(Theme.of(context).colorScheme.primary, -92);
  Color get _lsAmber => _hueShift(Theme.of(context).colorScheme.secondary, 27);

  /// `.icon-btn` — 34px circle, surface bg + border.
  Widget _lsIconButton({
    required IconData icon,
    required VoidCallback onTap,
    required String tooltip,
    required ColorScheme cs,
    int badge = 0,
    Gradient? gradient,
  }) {
    final isGradient = gradient != null;
    return Semantics(
      button: true,
      label: tooltip, // Task 15.1
      child: Tooltip(
        message: tooltip,
        child: InkWell(
          onTap: onTap,
          customBorder: const CircleBorder(),
          child: Stack(clipBehavior: Clip.none, children: [
            Container(
              width: 34,
              height: 34,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: isGradient ? null : cs.surface,
                gradient: gradient,
                border: isGradient ? null : Border.all(color: cs.outlineVariant),
              ),
              child: Icon(icon, size: 16, color: isGradient ? cs.onPrimary : cs.onSurface),
            ),
            // `.notif-badge`
            if (badge > 0)
              Positioned(
                top: -2,
                right: -2,
                child: Container(
                  constraints: const BoxConstraints(minWidth: 14),
                  height: 14,
                  padding: const EdgeInsets.symmetric(horizontal: 3),
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: cs.secondary,
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: lsBg(context), width: 1.5),
                  ),
                  child: Text(
                    badge > 9 ? '9+' : '$badge',
                    style: TextStyle(fontSize: 8.5, fontWeight: FontWeight.w700, color: cs.onSecondary, height: 1.2),
                  ),
                ),
              ),
          ]),
        ),
      ),
    );
  }

  // ============================================================
  // TOP SECTIONS
  // ============================================================

  /// `.brand-row` — logo + theme/language/notification/compose buttons.
  /// HTML me ye content ke saath scroll hota hai; yahan floating+snap
  /// SliverAppBar hai, to scroll-down pe chhup jaata hai aur scroll-up pe
  /// turant wapas aa jaata hai (same feel, par compose/search ek flick me
  /// wapas mil jaate hain).
  Widget _buildBrandRow(ColorScheme cs, AppLocalizations l10n) {
    return Row(children: [
      // `.brand-mark`
      Container(
        width: 30,
        height: 30,
        alignment: Alignment.center,
        decoration: BoxDecoration(color: cs.primary, borderRadius: BorderRadius.circular(9)),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(9),
          child: Image.asset(
            'assets/slogo1.png',
            width: 30,
            height: 30,
            fit: BoxFit.cover,
            errorBuilder: (_, __, ___) =>
                Text('LS', style: LsType.head(context, size: 13, color: cs.onPrimary)),
          ),
        ),
      ),
      const SizedBox(width: 9),
      // `.brand-name`
      // Expanded (Flexible + Spacer nahi) — loose Flexible ke saath leftover
      // space row ke aakhir me chala jaata hai aur icon buttons right edge se
      // hat jaate hain.
      Expanded(
        child: Text(
          l10n.appTitle,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
              fontSize: 15.5, fontWeight: FontWeight.w700, letterSpacing: -.15, color: cs.onSurface)
              .merge(LsType.head(context, size: 15.5)),
        ),
      ),
      // `.head-btns`
      // 🔥 Theme aur language toggle home se hata diye gaye hain — dono ab
      // sirf Settings screen ke andar hain (settings_screen.dart,
      // _LanguageSection/_ThemeSection). Purana _buildLanguageToggleButton
      // orphan method yahan se hata diya gaya hai.
      _lsIconButton(
        cs: cs,
        icon: Icons.notifications_none_rounded,
        tooltip: l10n.notificationsTooltip,
        badge: _unreadNotifications,
        // ✅ Task 1 — NotificationsScreen wired. Wapas aane pe badge
        // refresh karo (screen ke andar mark-read/mark-all-read ho chuka
        // ho sakta hai).
        onTap: () async {
          await Navigator.push(
            context,
            MaterialPageRoute(builder: (_) => const NotificationsScreen()),
          );
          _loadUnreadCount();
        },
      ),
      const SizedBox(width: 8),
      _lsIconButton(
        cs: cs,
        icon: Icons.edit_rounded,
        tooltip: l10n.newPostTooltip,
        onTap: _openNewPost,
        gradient: LinearGradient(colors: [cs.secondary, cs.primary]),
      ),
    ]);
  }

  /// `.search-bar`
  Widget _buildLsSearchBar(ColorScheme cs, AppLocalizations l10n) {
    return Semantics(
      button: true,
      label: l10n.searchHint,
      child: GestureDetector(
        onTap: _openSearch,
        behavior: HitTestBehavior.opaque,
        child: Container(
          margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
          decoration: BoxDecoration(
            color: cs.surface,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: cs.outlineVariant),
          ),
          child: Row(children: [
            Icon(Icons.search_rounded, size: 16, color: cs.onSurfaceVariant),
            const SizedBox(width: 9),
            Expanded(
              child: Text(
                l10n.searchHint,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant),
              ),
            ),
          ]),
        ),
      ),
    );
  }

  /// `.section-label` + `.classroom-row`
  Widget _buildClassroomSwitcher(ColorScheme cs, AppLocalizations l10n) {
    if (_extrasLoading && _classrooms.isEmpty) {
      return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _sectionLabel(l10n.yourClassrooms, cs),
        const LsClassroomRowSkeleton(sidePad: kLsPad),
        const SizedBox(height: 16),
      ]);
    }
    if (_classrooms.isEmpty) return const SizedBox.shrink();

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _sectionLabel(l10n.yourClassrooms, cs),
      SizedBox(
        height: 40,
        child: ListView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: kLsPad),
          children: [
            ..._classrooms.map((c) => Padding(
                  padding: const EdgeInsets.only(right: 8),
                  // Center — horizontal ListView children ko poori 40px height
                  // ki tight constraint milti hai; chip ko uski apni height
                  // rakhni hai (HTML: padding 8px 13px).
                  child: Center(
                      child: GestureDetector(
                    // ✅ Task 2 — classroom-detail screen wired (interim,
                    // product-decision pending — see classroom_detail_screen.dart
                    // header comment). Live/non-live dono taps ab isi screen
                    // pe jaate hain; join-CTA screen ke andar hi hai.
                    //
                    // FIX: real ClassroomDetailScreen takes `classroomId`
                    // (int) + an optional `initial` typed to liveclass's own
                    // `Classroom` model — it never had a `classroom:` param,
                    // and this file's lightweight `ClassroomModel` (id/name/
                    // isLive/isActive only) isn't that type anyway. Passing
                    // just the id; the screen refetches full detail itself
                    // on open (see its header comment — 4 parallel calls).
                    onTap: () {
                      HapticFeedback.selectionClick();
                      final classroomId = int.tryParse(c.id);
                      if (classroomId == null) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: Text(l10n.couldNotOpenClassroom)),
                        );
                        return;
                      }
                      Navigator.push(
                        context,
                        MaterialPageRoute(builder: (_) => ClassroomDetailScreen(api: LiveClass.api, classroomId: classroomId)),
                      );
                    },
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 8),
                      decoration: BoxDecoration(
                        color: c.isActive ? cs.primary : cs.surface,
                        borderRadius: BorderRadius.circular(20),
                        border: Border.all(color: c.isActive ? cs.primary : cs.outlineVariant),
                      ),
                      child: Row(mainAxisSize: MainAxisSize.min, children: [
                        // `.live-dot`
                        if (c.isLive)
                          Container(
                            width: 6,
                            height: 6,
                            margin: const EdgeInsets.only(right: 6),
                            decoration: BoxDecoration(
                                color: c.isActive ? cs.onPrimary : cs.secondary, shape: BoxShape.circle),
                          ),
                        Text(
                          c.name,
                          style: TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w700,
                              color: c.isActive ? cs.onPrimary : cs.onSurface),
                        ),
                      ]),
                    ),
                  )),
                )),
            // `.classroom-chip.add` — dashed border HTML me hai; Flutter ka
            // Border dashed support nahi karta, isliye CustomPainter se.
            Center(
                child: GestureDetector(
              onTap: () {
                HapticFeedback.selectionClick();
                _openExplore(); // join = classroom discovery
              },
              child: CustomPaint(
                painter: _DashedBorderPainter(color: cs.primary, radius: 20),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 8),
                  alignment: Alignment.center,
                  child: Text(
                    l10n.joinClassroom,
                    style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.primary),
                  ),
                ),
              ),
            )),
          ],
        ),
      ),
      const SizedBox(height: 16),
    ]);
  }

  /// `.invite-strip` — HTML: linear-gradient(115deg, primary, #8B5CF6 55%,
  /// accent). Beech wala stop primary→accent ke blend se nikala hai taaki
  /// dark theme me bhi gradient utna hi smooth rahe.
  Widget _buildInviteStrip(ColorScheme cs, AppLocalizations l10n) {
    if (_extrasLoading && _inviteInfo == null) return const LsInviteStripSkeleton(sidePad: kLsPad);
    // Invite link ke bina share karna bekaar hai — endpoint fail ho to strip
    // hide, taaki user ko dead button na mile.
    if (_inviteInfo == null) return const SizedBox.shrink();

    // FIX (Task 5) — this used to be a flat one-time "coins per referral"
    // number the caller could never actually earn more than once per
    // friend. The real program is per-classroom, ongoing (a %commission of
    // the referred student's DAILY fee, for as long as they keep the
    // class — see ClassroomModel.referralCommissionPercent /
    // HomeExtrasService.getReferLink doc comments), and the %rate is the
    // TEACHER's call per classroom, not something shown as a single global
    // number here. This strip now shows the caller's own real running
    // totals instead (what class-referral-summary actually returns) —
    // the %commission itself is shown at share-time, per classroom, once
    // getReferLink() below has actually fetched it.
    final earned = _inviteInfo!.totalCommissionEarned;
    final pending = _inviteInfo!.totalCommissionPending;
    final subtitle = earned > 0 || pending > 0
        ? l10n.inviteEarnedSoFar(earned, pending)
        : l10n.inviteShareClassroomLink;
    final mid = Color.lerp(cs.primary, cs.secondary, .45)!;
    return Container(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 16),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: LinearGradient(
          begin: const Alignment(-1, -.6),
          end: const Alignment(1, .6),
          colors: [cs.primary, mid, cs.secondary],
          stops: const [0, .55, 1],
        ),
      ),
      child: Row(children: [
        // `.invite-ico`
        Container(
          width: 34,
          height: 34,
          decoration: BoxDecoration(color: cs.onPrimary.withOpacity(.22), borderRadius: BorderRadius.circular(10)),
          child: Icon(Icons.card_giftcard_rounded, color: cs.onPrimary, size: 17),
        ),
        const SizedBox(width: 11),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.inviteEarn,
                style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onPrimary)),
            const SizedBox(height: 1),
            Text(
              subtitle,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 10.5, height: 1.3, color: cs.onPrimary.withOpacity(.85)),
            ),
          ]),
        ),
        const SizedBox(width: 10),
        // `.invite-cta`
        Semantics(
          button: true,
          label: l10n.inviteCta,
          child: GestureDetector(
            onTap: _inviteShareBusy ? null : _shareReferralLink,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 7),
              decoration: BoxDecoration(color: cs.onPrimary.withOpacity(.92), borderRadius: BorderRadius.circular(20)),
              child: _inviteShareBusy
                  ? SizedBox(
                      width: 13,
                      height: 13,
                      child: CircularProgressIndicator(strokeWidth: 2, color: cs.primary),
                    )
                  : Text(l10n.inviteCta,
                      style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: cs.primary)),
            ),
          ),
        ),
      ]),
    );
  }

  // NEW (Task 5) — the invite strip's actual share flow. Picks the first
  // of the caller's own classrooms (from `_classrooms` — already loaded
  // for the Task 2 switcher, so no extra list call needed) that has
  // `referralEnabled == true`: referrals are a per-classroom, teacher-
  // controlled toggle, not something on by default, so not every
  // classroom the caller sees is shareable. Then fetches that
  // classroom's real refer-link + %commission (getReferLink), tells the
  // person what rate they'll earn, and hands the server-composed
  // share_text to the native share sheet — no custom share UI, per the
  // task doc's production checklist.
  Future<void> _shareReferralLink() async {
    final l10n = AppLocalizations.of(context)!;
    final eligible = _classrooms.where((c) => c.referralEnabled).toList();
    if (eligible.isEmpty) {
      _snack(l10n.noClassroomsWithReferrals);
      return;
    }
    final classroom = eligible.first;
    setState(() => _inviteShareBusy = true);
    try {
      final link = await HomeExtrasService.getReferLink(classroom.id);
      if (!mounted) return;
      HapticFeedback.lightImpact();
      _snack(l10n.referralCommissionEarned(link.commissionPercent.toStringAsFixed(0), classroom.name));
      await Share.share(link.shareText.isNotEmpty ? link.shareText : link.webUrl);
    } catch (e) {
      if (!mounted) return;
      _snack(e is Exception ? e.toString().replaceFirst('Exception: ', '') : l10n.couldNotCreateReferralLink);
    } finally {
      if (mounted) setState(() => _inviteShareBusy = false);
    }
  }

  /// `.stories`
  ///
  /// ✅ Task 4 — real backend (`GET /post/stories/`, post app) wired.
  /// `_stories` (flat, from StoryService.getStories()) gets grouped
  /// per-user here via `groupStories()` — one ring = one user's active
  /// stories. First ring is always "Your Story": tap uploads a new one if
  /// you have none yet, opens the viewer on your own stories if you do.
  /// Ring gradient (unviewed) vs plain grey (all-viewed) mirrors Instagram.
  Widget _buildStories(ColorScheme cs, AppLocalizations l10n) {
    if (_extrasLoading && _stories.isEmpty) return const LsStoriesSkeleton(sidePad: kLsPad);

    final groups = groupStories(_stories);
    final myGroupIndex = _myUserId == null ? -1 : groups.indexWhere((g) => g.userId == _myUserId);
    final hasOwnStory = myGroupIndex != -1;
    // Everyone else's rings, in a fixed order for viewer paging (own group
    // excluded here — it gets its own special first tile below either way).
    final otherGroups = hasOwnStory ? (List.of(groups)..removeAt(myGroupIndex)) : groups;
    // Full paging order for the viewer: own stories first (if any), then
    // everyone else — so swiping past "your story" lands on the next ring
    // shown in the row, not a random order.
    final pagingGroups = hasOwnStory ? [groups[myGroupIndex], ...otherGroups] : otherGroups;
    // Note: if `groups` and `hasOwnStory` are both empty, the row still
    // renders — just the "Your Story" add-tile — same as Instagram does
    // rather than hiding the whole section.

    return SizedBox(
      height: 100,
      child: ListView.builder(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.fromLTRB(kLsPad, 2, kLsPad, 4),
        itemCount: 1 + otherGroups.length,
        itemBuilder: (c, i) {
          if (i == 0) {
            return _buildOwnStoryTile(cs, l10n, hasOwnStory ? groups[myGroupIndex] : null, pagingGroups);
          }
          final group = otherGroups[i - 1];
          return _buildStoryTile(
            cs,
            label: group.username,
            thumbnailUrl: group.userProfilePic,
            viewed: group.allViewed,
            onTap: () {
              HapticFeedback.selectionClick();
              final idx = pagingGroups.indexWhere((g) => g.userId == group.userId);
              Navigator.push(context, MaterialPageRoute(
                builder: (_) => StoryViewerScreen(groups: pagingGroups, initialGroupIndex: idx < 0 ? 0 : idx, myUserId: _myUserId),
              )).then((_) => _loadHomeExtras()); // refresh so viewed-rings turn grey on return
            },
          );
        },
      ),
    );
  }

  /// First tile in the row — either an "add" button (no story yet) or my
  /// own ring (tap opens the viewer on my stories). The small "+" badge
  /// has its own always-active tap target — bottom-right corner, on top
  /// of the Stack — so it can trigger another upload even when `mine`
  /// isn't null, same as Instagram's own-story tile. (Real bug fix: this
  /// badge used to be a plain, non-tappable Container — the tile's ONE
  /// GestureDetector only ever called `_pickAndUploadStory()` when
  /// `mine == null`, so the moment you had a first story, tapping
  /// anywhere on the tile — badge included — only ever opened the
  /// viewer, with no way left to add a second one.)
  Widget _buildOwnStoryTile(ColorScheme cs, AppLocalizations l10n, StoryGroup? mine, List<StoryGroup> pagingGroups) {
    return GestureDetector(
      onTap: () {
        HapticFeedback.selectionClick();
        if (mine == null) {
          _pickAndUploadStory();
        } else {
          Navigator.push(context, MaterialPageRoute(
            builder: (_) => StoryViewerScreen(groups: pagingGroups, initialGroupIndex: 0, myUserId: _myUserId),
          )).then((_) => _loadHomeExtras());
        }
      },
      child: Container(
        width: 74,
        margin: const EdgeInsets.only(right: 14),
        child: Column(children: [
          Stack(
            clipBehavior: Clip.none,
            children: [
              _storyRing(
                cs,
                thumbnailUrl: mine?.userProfilePic,
                viewed: mine?.allViewed ?? true, // plain ring when empty — nothing to "unviewed"
                fallback: _storyInitials('You', cs),
              ),
              if (_storyUploadBusy)
                Positioned.fill(child: Center(child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white, value: _storyUploadProgress > 0 ? _storyUploadProgress / 100 : null)))
              else
                Positioned(
                  bottom: 0,
                  right: 0,
                  child: GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTap: () { HapticFeedback.selectionClick(); _pickAndUploadStory(); },
                    child: Container(
                      width: 22,
                      height: 22,
                      decoration: BoxDecoration(shape: BoxShape.circle, color: cs.primary, border: Border.all(color: cs.surface, width: 2)),
                      child: Icon(Icons.add, size: 14, color: cs.onPrimary),
                    ),
                  ),
                ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            l10n.yourStory,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant),
          ),
        ]),
      ),
    );
  }

  Widget _buildStoryTile(
    ColorScheme cs, {
    required String label,
    required String? thumbnailUrl,
    required bool viewed,
    required VoidCallback onTap,
  }) {
    final initials = label.isEmpty ? '?' : label.substring(0, label.length >= 2 ? 2 : label.length).toUpperCase();
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 74,
        margin: const EdgeInsets.only(right: 14),
        child: Column(children: [
          _storyRing(cs, thumbnailUrl: thumbnailUrl, viewed: viewed, fallback: _storyInitials(initials, cs)),
          const SizedBox(height: 6),
          Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant),
          ),
        ]),
      ),
    );
  }

  // 🔥 Instagram-style ring — outer 72, inner avatar 66. Gradient border
  // when there's an unviewed story, plain outlineVariant grey once every
  // story in the group has been seen (Instagram's own read/unread
  // convention) — `_buildStories` passes `viewed` per-group for this.
  Widget _storyRing(ColorScheme cs, {String? thumbnailUrl, required bool viewed, required Widget fallback}) {
    return Container(
      width: 72,
      height: 72,
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: viewed
            ? null
            : LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight, colors: [cs.secondary, cs.primary]),
        border: viewed ? Border.all(color: cs.outlineVariant, width: 2) : null,
      ),
      child: Container(
        decoration: BoxDecoration(shape: BoxShape.circle, color: cs.surface),
        alignment: Alignment.center,
        clipBehavior: Clip.antiAlias,
        child: thumbnailUrl != null && thumbnailUrl.isNotEmpty
            // Task 10.2 — memCache size set, warna 1080px image 66px ke
            // ring ke liye poori RAM me jaati hai.
            ? CachedNetworkImage(
                imageUrl: thumbnailUrl,
                width: 66,
                height: 66,
                fit: BoxFit.cover,
                memCacheWidth: 200,
                errorWidget: (_, __, ___) => fallback,
              )
            : fallback,
      ),
    );
  }

  Widget _storyInitials(String text, ColorScheme cs) => Center(
        child: Text(text,
            style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: cs.primary)),
      );

  // Task 4 — upload flow. Bottom-sheet camera/gallery choice reuses the
  // same ImagePicker already imported for the comment-sheet's media
  // picker; a story is just a short-lived single image/video, same
  // validation path server-side (StoryCreateSerializer.validate_media
  // reuses PostMedia's own allowlist/size limit).
  Future<void> _pickAndUploadStory() async {
    final l10n = AppLocalizations.of(context)!;
    final source = await showModalBottomSheet<String>(
      context: context,
      builder: (c) => SafeArea(
        child: Wrap(children: [
          ListTile(leading: const Icon(Icons.photo_camera_outlined), title: Text(l10n.camera), onTap: () => Navigator.pop(c, 'camera_image')),
          ListTile(leading: const Icon(Icons.videocam_outlined), title: Text(l10n.recordVideo), onTap: () => Navigator.pop(c, 'camera_video')),
          ListTile(leading: const Icon(Icons.photo_library_outlined), title: Text(l10n.chooseFromGallery), onTap: () => Navigator.pop(c, 'gallery_image')),
        ]),
      ),
    );
    if (source == null) return;

    final picker = ImagePicker();
    XFile? picked;
    String mediaType = 'image';
    try {
      switch (source) {
        case 'camera_image':
          picked = await picker.pickImage(source: ImageSource.camera, imageQuality: 85);
          break;
        case 'camera_video':
          picked = await picker.pickVideo(source: ImageSource.camera, maxDuration: const Duration(minutes: 1));
          mediaType = 'video';
          break;
        case 'gallery_image':
          picked = await picker.pickImage(source: ImageSource.gallery, imageQuality: 85);
          break;
      }
    } catch (_) {
      return;
    }
    if (picked == null || !mounted) return;

    final file = File(picked.path);
    // Task 11 — caption step. `StoryService.createStory` already accepted
    // an optional caption; the flow just never asked for one before this.
    final caption = await showStoryCaptionSheet(context, media: file, mediaType: mediaType);
    if (caption == null || !mounted) return; // closed without tapping Share — don't upload

    setState(() { _storyUploadBusy = true; _storyUploadProgress = 0; });
    try {
      await StoryService.createStory(
        media: file,
        mediaType: mediaType,
        caption: caption.isEmpty ? null : caption,
        onProgress: (p) { if (mounted) setState(() => _storyUploadProgress = p); },
      );
      await _loadHomeExtras();
    } catch (e) {
      if (mounted) _snack(e is Exception ? e.toString().replaceFirst('Exception: ', '') : l10n.couldNotUploadStory);
    } finally {
      if (mounted) setState(() { _storyUploadBusy = false; _storyUploadProgress = 0; });
    }
  }

  /// `.section` (Live now) — `.live-card` horizontal row.
  Widget _buildLiveNow(ColorScheme cs, AppLocalizations l10n) {
    if (_extrasLoading && _liveClasses.isEmpty) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 20),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
            child: _sectionTitle(l10n.liveNow, cs),
          ),
          const LsLiveRowSkeleton(sidePad: kLsPad),
        ]),
      );
    }
    if (_liveClasses.isEmpty) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.only(bottom: 20),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // `.section-head`
        Padding(
          padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
          child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
            _sectionTitle(l10n.liveNow, cs),
            GestureDetector(
              onTap: _openExplore,
              child: Text(l10n.seeAll,
                  style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: cs.primary)),
            ),
          ]),
        ),
        SizedBox(
          // 216, 172 nahi — 172 me thumb(76) + LIVE tag + 2-line title + meta
          // + Join button fit nahi hote the aur card overflow karta tha.
          height: 216,
          child: ListView.builder(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: kLsPad),
            itemCount: _liveClasses.length,
            itemBuilder: (c, i) {
              final l = _liveClasses[i];
              return Container(
                width: 200,
                margin: const EdgeInsets.only(right: 10),
                padding: const EdgeInsets.all(11),
                decoration: BoxDecoration(
                  color: cs.surface,
                  border: Border.all(color: cs.outlineVariant),
                  borderRadius: BorderRadius.circular(16),
                ),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  // `.live-thumb`
                  ClipRRect(
                    borderRadius: BorderRadius.circular(12),
                    child: SizedBox(
                      height: 76,
                      width: double.infinity,
                      child: l.thumbnailUrl != null && l.thumbnailUrl!.isNotEmpty
                          ? CachedNetworkImage(
                              imageUrl: l.thumbnailUrl!,
                              fit: BoxFit.cover,
                              memCacheWidth: 520,
                              errorWidget: (_, __, ___) => _liveThumbFallback(cs),
                            )
                          : _liveThumbFallback(cs),
                    ),
                  ),
                  const SizedBox(height: 9),
                  // `.live-tag`
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                    decoration: BoxDecoration(color: cs.secondary, borderRadius: BorderRadius.circular(20)),
                    child: Text('● ${l10n.liveBadge}',
                        style: TextStyle(fontSize: 9, fontWeight: FontWeight.w700, color: cs.onSecondary)),
                  ),
                  const SizedBox(height: 6),
                  // `.live-title`
                  Expanded(
                    child: Text(
                      l.title,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                          fontSize: 12.5, fontWeight: FontWeight.w700, height: 1.3, color: cs.onSurface),
                    ),
                  ),
                  // `.live-meta`
                  Text(
                    l10n.liveNowCardSubtitle(l.teacherName, l.viewersCount),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant),
                  ),
                  const SizedBox(height: 8),
                  // `.live-join`
                  //
                  // ✅ Task 3 — real join flow wired (interim, same as
                  // Task 2's classroom-detail wiring): navigates into
                  // ClassroomDetailScreen for this card's classroom,
                  // which already has the fully-built "Enter Class"
                  // bottom bar (my-pass check, real POST .../join/,
                  // LiveKit token, room navigation — see that screen's
                  // header comment). NOT a placeholder Explore-tab
                  // redirect anymore. If product wants a true one-tap
                  // join straight into the live room from this card
                  // (skipping the classroom-detail screen), that needs
                  // live_session_screen.dart's constructor to wire
                  // directly — ask for that file to take it further.
                  Semantics(
                    button: true,
                    label: '${l10n.join} ${l.title}',
                    child: GestureDetector(
                      onTap: () {
                        HapticFeedback.lightImpact();
                        final classroomId = int.tryParse(l.classroomId);
                        if (classroomId == null) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            SnackBar(content: Text(l10n.couldNotOpenClass)),
                          );
                          return;
                        }
                        Navigator.push(
                          context,
                          MaterialPageRoute(builder: (_) => ClassroomDetailScreen(api: LiveClass.api, classroomId: classroomId)),
                        );
                      },
                      child: Container(
                        width: double.infinity,
                        padding: const EdgeInsets.symmetric(vertical: 6),
                        decoration: BoxDecoration(color: cs.primary, borderRadius: BorderRadius.circular(20)),
                        child: Text(l10n.join,
                            textAlign: TextAlign.center,
                            style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: cs.onPrimary)),
                      ),
                    ),
                  ),
                ]),
              );
            },
          ),
        ),
      ]),
    );
  }

  Widget _liveThumbFallback(ColorScheme cs) => Container(
        alignment: Alignment.center,
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [cs.primary, Color.lerp(cs.primary, cs.onPrimary, .18)!],
          ),
        ),
        child: Icon(Icons.play_arrow_rounded, color: cs.onPrimary, size: 28),
      );

  /// `.quick-grid` — 4 columns.
  ///
  /// Assignments, Test Series aur ab Wallet bhi asli screens pe jaate hain.
  /// 🔧 Notices abhi bhi "coming soon" hai — uski apni koi standalone
  /// screen nahi hai (campus ke andar NoticesScreen hai, par usko
  /// `CampusAccess` chahiye jo home.dart ke paas load karne ka module
  /// abhi wire nahi hai — campus tab se hi khulti hai).
  Widget _buildQuickActionsGrid(ColorScheme cs, AppLocalizations l10n) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final bgOpacity = isDark ? 0.24 : 0.12;
    final items = <Map<String, Object>>[
      {
        'icon': Icons.description_outlined,
        'label': l10n.assignments,
        'fg': cs.secondary,
        'tap': _openAssignments,
      },
      {
        'icon': Icons.fact_check_outlined,
        'label': l10n.testSeries,
        'fg': cs.primary,
        'tap': _openTestSeries,
      },
      {
        'icon': Icons.campaign_outlined,
        'label': l10n.notices,
        'fg': _lsGreen,
        'tap': () => _snack(l10n.featureComingSoon(l10n.notices)),
      },
      {
        'icon': Icons.account_balance_wallet_outlined,
        'label': l10n.wallet,
        'fg': _lsAmber,
        // ✅ WalletScreen wired — user_profile app ke coin endpoints
        // confirmed hain (wallet_service.dart dekho).
        'tap': () {
          Navigator.push(
            context,
            MaterialPageRoute(builder: (_) => const WalletScreen()),
          );
        },
      },
    ];
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 22),
      child: Row(
        children: items.map((it) {
          final fg = it['fg'] as Color;
          final label = it['label'] as String;
          return Expanded(
            child: Semantics(
              button: true,
              label: label,
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () {
                  HapticFeedback.selectionClick();
                  (it['tap'] as VoidCallback)();
                },
                child: Column(children: [
                  // `.quick-ico`
                  Container(
                    width: 48,
                    height: 48,
                    decoration:
                        BoxDecoration(color: fg.withOpacity(bgOpacity), borderRadius: BorderRadius.circular(15)),
                    child: Icon(it['icon'] as IconData, color: fg, size: 22),
                  ),
                  const SizedBox(height: 6),
                  // `.quick-lbl`
                  Text(
                    label,
                    textAlign: TextAlign.center,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 9.5, fontWeight: FontWeight.w600, color: cs.onSurface),
                  ),
                ]),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  /// `.feed-title`
  Widget _buildFeedTitle(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
      child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        _sectionTitle(l10n.yourFeed, cs),
        Text(l10n.following, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: cs.primary)),
      ]),
    );
  }

  // ============================================================
  // FEED
  // ============================================================

  /// `.interstitial` — Task 6. Har ~18 posts baad ("Jump back in").
  Widget _buildFeedInterstitial(ColorScheme cs, AppLocalizations l10n) {
    final items = <Map<String, Object>>[
      {'icon': Icons.person_add_alt_1_rounded, 'label': l10n.addFriends, 'fg': cs.primary, 'tap': _openSearch},
      {'icon': Icons.play_circle_outline_rounded, 'label': l10n.startClass, 'fg': _lsGreen, 'tap': _openExplore},
      {'icon': Icons.task_alt_rounded, 'label': l10n.startTestSeries, 'fg': _lsAmber, 'tap': _openTestSeries},
    ];
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 16),
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: cs.surfaceVariant,
        border: Border.all(color: cs.outlineVariant),
        borderRadius: BorderRadius.circular(kLsRadius),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l10n.jumpBackIn, style: LsType.head(context, size: 12.5)),
        const SizedBox(height: 11),
        Row(
          children: items.map((it) {
            final fg = it['fg'] as Color;
            final label = it['label'] as String;
            return Expanded(
              child: Padding(
                padding: const EdgeInsets.only(right: 8),
                child: Semantics(
                  button: true,
                  label: label,
                  child: GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTap: () {
                      HapticFeedback.selectionClick();
                      (it['tap'] as VoidCallback)();
                    },
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 10),
                      decoration: BoxDecoration(
                        color: lsBg(context),
                        border: Border.all(color: cs.outlineVariant),
                        borderRadius: BorderRadius.circular(14),
                      ),
                      child: Column(children: [
                        Container(
                          width: 32,
                          height: 32,
                          decoration: BoxDecoration(
                              color: fg.withOpacity(isDark ? .24 : .12), borderRadius: BorderRadius.circular(10)),
                          child: Icon(it['icon'] as IconData, size: 17, color: fg),
                        ),
                        const SizedBox(height: 6),
                        Text(
                          label,
                          textAlign: TextAlign.center,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(fontSize: 9.5, fontWeight: FontWeight.w700, color: cs.onSurface),
                        ),
                      ]),
                    ),
                  ),
                ),
              ),
            );
          }).toList(),
        ),
      ]),
    );
  }

  /// Native ad — ab feed card ki shape me (rounded + border), warna beech me
  /// full-bleed white block design tod deta tha.
  /// NativeAd platform-view hai, Theme inherit nahi karta — isliye colors
  /// manually resolve karke pass karte hain.
  Widget _buildAd(ColorScheme cs) {
    return Container(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      decoration: BoxDecoration(
        color: cs.surface,
        border: Border.all(color: cs.outlineVariant),
        borderRadius: BorderRadius.circular(kLsRadius),
      ),
      clipBehavior: Clip.antiAlias,
      child: SizedBox(
        height: 320,
        width: double.infinity,
        child: NativeAd(
          // Bug fix (production-readiness): was hardcoded to
          // NativeAd.testPlacementId always, so production builds could
          // never earn real ad revenue either. Debug builds keep using
          // Meta's test placement; release builds use the real one.
          // TODO: replace _prodNativeAdPlacementId below with your actual
          // Audience Network placement ID from the Meta dashboard before
          // shipping — it isn't available in the files reviewed here.
          placementId: kDebugMode ? NativeAd.testPlacementId : _prodNativeAdPlacementId,
          adType: NativeAdType.NATIVE_AD,
          width: double.infinity,
          height: 320,
          backgroundColor: cs.surface,
          titleColor: cs.onSurface,
          descriptionColor: cs.onSurfaceVariant,
          buttonColor: cs.primary,
          buttonTitleColor: cs.onPrimary,
          buttonBorderColor: cs.primary,
          listener: NativeAdListener(
            onLoaded: () => debugPrint('Native Ad Loaded'),
            onError: (c, m) => debugPrint('Native Ad Error $c $m'),
            onClicked: () => debugPrint('Native Ad Clicked'),
          ),
        ),
      ),
    );
  }

  String _initials(String name) {
    final parts = name.trim().split(RegExp(r'[\s._-]+')).where((w) => w.isNotEmpty).toList();
    if (parts.isEmpty) return '?';
    if (parts.length == 1) {
      return parts.first.substring(0, parts.first.length >= 2 ? 2 : 1).toUpperCase();
    }
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  /// HTML har post ko 4 brand colors me se ek deta hai — yahan wahi 4
  /// theme se derive karke username ke hash se pick hote hain (same user =
  /// same color, har scroll pe).
  Color _avatarColor(String seed, ColorScheme cs) {
    final palette = [cs.primary, cs.secondary, _lsGreen, _lsAmber];
    return palette[seed.hashCode.abs() % palette.length];
  }

  /// `.post-card` — rounded 18, surface bg, 1px border, 18px side margin.
  Widget _buildPostCard(PostModel post, int idx, ColorScheme cs, AppLocalizations l10n) {
    final sorted = <MapEntry<String, int>>[
      MapEntry('like', post.likeCount),
      MapEntry('confuse', post.confuseCount),
      MapEntry('wrong', post.wrongCount),
      MapEntry('imp', post.impCount),
      MapEntry('explain', post.explainCount),
    ]..sort((a, b) => b.value.compareTo(a.value));
    final top3 = sorted.where((e) => e.value > 0).take(3).toList();
    final isSaved = post.isSaved || _savedIds.contains(post.id);
    final hasPic = post.user.profilePicture != null && post.user.profilePicture!.isNotEmpty;

    // `.post-meta` — "Category · 3h ago". 'general' default category hai,
    // usko dikhane ka koi matlab nahi.
    final cat = post.category.trim();
    final showCat = cat.isNotEmpty && cat.toLowerCase() != 'general';
    final timeLabel = timeago.format(post.createdAt, locale: Localizations.localeOf(context).languageCode);

    return Container(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 14),
      decoration: BoxDecoration(
        color: cs.surface,
        border: Border.all(color: cs.outlineVariant),
        borderRadius: BorderRadius.circular(kLsRadius),
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // `.post-head`
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 6, 8),
          child: Row(children: [
            GestureDetector(
              onTap: () => _goToProfile(post.user.username),
              child: Container(
                width: 32,
                height: 32,
                alignment: Alignment.center,
                clipBehavior: Clip.antiAlias,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: _avatarColor(post.user.username, cs),
                ),
                child: hasPic
                    ? CachedNetworkImage(
                        imageUrl: post.user.profilePicture!,
                        width: 32,
                        height: 32,
                        fit: BoxFit.cover,
                        memCacheWidth: 128, // Task 10.2
                        errorWidget: (_, __, ___) => Text(_initials(post.user.username),
                            style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: Colors.white)),
                      )
                    : Text(_initials(post.user.username),
                        style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: Colors.white)),
              ),
            ),
            const SizedBox(width: 9),
            Expanded(
              child: GestureDetector(
                onTap: () => _goToProfile(post.user.username),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  // `.post-name`
                  Text(post.user.username,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurface)),
                  // `.post-meta`
                  Text(showCat ? '$cat · $timeLabel' : timeLabel,
                      style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
                ]),
              ),
            ),
            Semantics(
              button: true,
              label: l10n.save,
              child: IconButton(
                visualDensity: VisualDensity.compact,
                icon: Icon(isSaved ? Icons.bookmark : Icons.bookmark_border,
                    size: 20, color: isSaved ? cs.primary : cs.onSurfaceVariant),
                onPressed: () => _toggleSave(post),
              ),
            ),
          ]),
        ),
        // `.post-text`
        if (post.content != null && post.content!.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
            child: Text(post.content!,
                style: TextStyle(fontSize: 13, height: 1.5, color: cs.onSurface.withOpacity(.92))),
          ),
        if (post.hashtags.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
            child: Wrap(
              spacing: 6,
              runSpacing: 2,
              children: post.hashtags
                  .map((t) => InkWell(
                        onTap: () => Navigator.push(
                            context, MaterialPageRoute(builder: (_) => PostListScreen.hashtag(t))),
                        child: Text('#$t',
                            style: TextStyle(fontSize: 12, color: cs.primary, fontWeight: FontWeight.w600)),
                      ))
                  .toList(),
            ),
          ),
        if (post.media.isNotEmpty)
          _MediaCarousel(key: ValueKey(post.id), mediaList: post.media, postId: post.id, postIndex: idx),
        // reaction summary
        if (post.likesCount > 0 || post.commentsCount > 0)
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 10, 14, 8),
            child: Row(children: [
              if (top3.isNotEmpty)
                Row(
                  children: top3
                      .map((e) => Padding(
                            padding: const EdgeInsets.only(right: 3),
                            child: Text(_emojiMap[e.key] ?? '', style: const TextStyle(fontSize: 14)),
                          ))
                      .toList(),
                ),
              if (post.likesCount > 0) ...[
                const SizedBox(width: 4),
                Text('${post.likesCount}', style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
              ],
              const Spacer(),
              if (post.commentsCount > 0)
                Text(l10n.commentsCount(post.commentsCount),
                    style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
            ]),
          ),
        Divider(height: 1, thickness: 1, color: cs.outlineVariant),
        // `.post-actions`
        Row(children: [
          Expanded(
            child: _PostReactionButton(
              post: post,
              emojiMap: _emojiMap,
              emojiColor: _emojiColor,
              likeLabel: l10n.like,
              onReaction: (r) => _handleReaction(post, r),
            ),
          ),
          Expanded(
            child: _PostActionButton(
              icon: Icons.chat_bubble_outline_rounded,
              label: l10n.comment,
              onTap: () => _openCommentSheet(post),
            ),
          ),
          Expanded(
            child: _PostActionButton(
              icon: Icons.share_outlined,
              label: l10n.share,
              onTap: () => _sharePost(post),
            ),
          ),
        ]),
      ]),
    );
  }

  // ============================================================
  // PAGE
  // ============================================================

  Widget _homePage() {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return RefreshIndicator(
      color: cs.primary,
      backgroundColor: cs.surface,
      onRefresh: () async {
        HapticFeedback.mediumImpact();
        await Future.wait([_loadFeed(refresh: true), _loadHomeExtras(), _loadUnreadCount()]);
      },
      child: CustomScrollView(
        controller: _scrollController,
        // Task 7.4 — sab kuch ek hi scroll view me; koi nested vertical
        // ListView nahi (horizontal rows fine hain).
        slivers: [
          // `.brand-row`
          SliverAppBar(
            automaticallyImplyLeading: false,
            backgroundColor: lsBg(context),
            surfaceTintColor: Colors.transparent,
            elevation: 0,
            scrolledUnderElevation: 0,
            floating: true,
            snap: true,
            toolbarHeight: 56,
            titleSpacing: kLsPad,
            title: _buildBrandRow(cs, l10n),
          ),
          SliverToBoxAdapter(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              _buildLsSearchBar(cs, l10n),
              _buildClassroomSwitcher(cs, l10n),
              _buildInviteStrip(cs, l10n),
              _buildStories(cs, l10n),
              const SizedBox(height: 6),
              _buildLiveNow(cs, l10n),
              _buildQuickActionsGrid(cs, l10n),
              _buildFeedTitle(cs, l10n),
            ]),
          ),

          // ---------- feed ----------
          if (_isLoading && _posts.isEmpty)
            SliverList(
              delegate: SliverChildBuilderDelegate(
                (context, i) => LsPostCardSkeleton(sidePad: kLsPad, withMedia: i == 1),
                childCount: 3,
              ),
            )
          else if (_feedFailed && _posts.isEmpty)
            SliverToBoxAdapter(
              child: ErrorStateWidget(
                title: l10n.feedErrorTitle,
                subtitle: l10n.feedErrorSubtitle,
                retryLabel: l10n.retry,
                onRetry: () => _loadFeed(refresh: true),
              ),
            )
          else if (_posts.isEmpty)
            SliverToBoxAdapter(
              child: EmptyStateWidget(
                icon: Icons.dynamic_feed_rounded,
                title: l10n.feedEmptyTitle,
                subtitle: l10n.feedEmptySubtitle,
                actionLabel: l10n.addFriends,
                onAction: _openSearch,
              ),
            )
          else
            SliverList(
              delegate: SliverChildBuilderDelegate(
                (context, index) {
                  final slot = _slots[index];
                  switch (slot.kind) {
                    case _FeedSlotKind.post:
                      return _buildPostCard(_posts[slot.postIndex], slot.postIndex, cs, l10n);
                    case _FeedSlotKind.ad:
                      return _buildAd(cs);
                    case _FeedSlotKind.interstitial:
                      return _buildFeedInterstitial(cs, l10n);
                  }
                },
                childCount: _slots.length,
              ),
            ),

          // pagination spinner — list ke bahar, taaki slot-math simple rahe
          if (_isLoadingMore)
            const SliverToBoxAdapter(
              child: Padding(
                padding: EdgeInsets.symmetric(vertical: 18),
                child: Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2))),
              ),
            ),

          // bottom nav ke neeche content na chhupe (.content padding-bottom:88)
          const SliverToBoxAdapter(child: SizedBox(height: 96)),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: lsBg(context),
      // `.bottomnav` HTML me content ke upar float karta hai (gradient fade)
      extendBody: true,
      body: IndexedStack(
        index: _selectedIndex,
        children: [
          _homePage(),
          const SearchScreen(),
          ProfileScreen(onBackToHome: () => setState(() => _selectedIndex = 0)),
        ],
      ),
      bottomNavigationBar: LsBottomNav(
        items: [
          LsBottomNavItemData(icon: Icons.home_outlined, activeIcon: Icons.home_rounded, label: l10n.homeTab),
          LsBottomNavItemData(icon: Icons.school_outlined, activeIcon: Icons.school_rounded, label: l10n.campusTab),
          LsBottomNavItemData(
              icon: Icons.smart_display_outlined, activeIcon: Icons.smart_display_rounded, label: l10n.classesTab),
          LsBottomNavItemData(
              icon: Icons.chat_bubble_outline_rounded, activeIcon: Icons.chat_bubble_rounded, label: l10n.chatTab),
          LsBottomNavItemData(icon: Icons.person_outline_rounded, activeIcon: Icons.person_rounded, label: l10n.profileTab),
        ],
        // Sirf Home(0)/Profile(2) `_selectedIndex` ke IndexedStack tabs hain —
        // Campus/Classes/Chat tap hote hi apna screen push karte hain, isliye
        // "active" kabhi unke liye highlight nahi hota (§ comment neeche).
        activeIndex: _selectedIndex == 0 ? 0 : (_selectedIndex == 2 ? 4 : -1),
        onTap: (i) {
          HapticFeedback.selectionClick();
          switch (i) {
            case 0:
              setState(() => _selectedIndex = 0);
            case 1:
              // ✅ CAMPUS — ab CampusScreen wired hai, "coming soon" snackbar nahi.
              Navigator.push(context, MaterialPageRoute(builder: (_) => const CampusScreen()));
            case 2:
              _openExplore();
            case 3:
              _openChat();
            case 4:
              setState(() => _selectedIndex = 2);
          }
        },
      ),
    );
  }
}

// ============================================================
// FEED SLOTS
// ============================================================

enum _FeedSlotKind { post, ad, interstitial }

class _FeedSlot {
  final _FeedSlotKind kind;
  final int postIndex;
  const _FeedSlot(this.kind) : postIndex = -1;
  const _FeedSlot.post(this.postIndex) : kind = _FeedSlotKind.post;
}


/// `.classroom-chip.add` ka dashed border — Flutter ke Border me dashed
/// style nahi hai, isliye chhota painter.
class _DashedBorderPainter extends CustomPainter {
  final Color color;
  final double radius;
  final double dash;
  final double gap;
  const _DashedBorderPainter({required this.color, this.radius = 20, this.dash = 4, this.gap = 3});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    final rrect = RRect.fromRectAndRadius(Offset.zero & size, Radius.circular(radius));
    final path = Path()..addRRect(rrect);
    for (final metric in path.computeMetrics()) {
      double d = 0;
      while (d < metric.length) {
        final next = (d + dash).clamp(0.0, metric.length);
        canvas.drawPath(metric.extractPath(d, next), paint);
        d = next + gap;
      }
    }
  }

  @override
  bool shouldRepaint(covariant _DashedBorderPainter old) => old.color != color || old.radius != radius;
}

/// `.post-actions` ka Comment/Share button.
class _PostActionButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  const _PostActionButton({required this.icon, required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 11),
        child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          Icon(icon, size: 17, color: cs.onSurfaceVariant),
          const SizedBox(width: 6),
          Flexible(
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant),
            ),
          ),
        ]),
      ),
    );
  }
}

class _PostReactionButton extends StatefulWidget {
  final PostModel post; final Map<String, String> emojiMap; final Map<String, Color> emojiColor; final Function(String) onReaction;
  /// 🔥 NAYA — 'Like' ab l10n se aata hai (Task 5.8), widget ke andar
  /// hardcoded nahi. Baaki reaction labels backend ke keys hain (LIKE/IMP/...)
  /// isliye wo waise hi uppercase dikhte hain.
  final String likeLabel;
  const _PostReactionButton({required this.post, required this.emojiMap, required this.emojiColor, required this.likeLabel, required this.onReaction});
  @override State<_PostReactionButton> createState() => _PostReactionButtonState();
}
class _PostReactionButtonState extends State<_PostReactionButton> {
  OverlayEntry? _overlayEntry;
  // 🎨 TASK 7.2: reaction-picker popover bg (Colors.white) and the unselected
  // emoji chip bg (Colors.grey.shade100) are theme-aware now; the shadow stays
  // a fixed black26 since a soft dark shadow still reads correctly on a dark
  // surface (no visual bug there, unlike a light-grey chip on a dark popover).
  void _showOverlay(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final RenderBox box = context.findRenderObject() as RenderBox; final Offset pos = box.localToGlobal(Offset.zero);
    _overlayEntry = OverlayEntry(builder: (c) => Stack(children: [
      GestureDetector(onTap: _hideOverlay, child: Container(color: Colors.transparent, width: double.infinity, height: double.infinity)),
      Positioned(left: 10, top: pos.dy - 65, child: Material(color: Colors.transparent, child: Container(padding: EdgeInsets.symmetric(horizontal: 8, vertical: 8), decoration: BoxDecoration(color: cs.surface, borderRadius: BorderRadius.circular(30), boxShadow: [BoxShadow(color: Colors.black26, blurRadius: 12)]), child: Row(children: widget.emojiMap.entries.map((e){ bool sel = widget.post.myReaction==e.key; return GestureDetector(onTap: (){ _hideOverlay(); widget.onReaction(e.key); }, child: Container(margin: EdgeInsets.symmetric(horizontal: 4), padding: EdgeInsets.all(10), decoration: BoxDecoration(color: sel? widget.emojiColor[e.key]!.withOpacity(0.18):cs.surfaceVariant, shape: BoxShape.circle, border: sel? Border.all(color: widget.emojiColor[e.key]!, width: 2):null), child: Text(e.value, style: TextStyle(fontSize: 26)))); }).toList()),),),),
    ])); Overlay.of(context).insert(_overlayEntry!);
  }
  void _hideOverlay(){ _overlayEntry?.remove(); _overlayEntry=null; }
  // 🎨 TASK 7.2: Like row icon/text was Color(0xFF65676B) (Messenger-grey,
  // near-invisible on a dark surface) — now cs.onSurfaceVariant.
  @override Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Builder(builder: (btnCtx){
      return InkWell(onTap: () => widget.onReaction('like'), onLongPress: () => _showOverlay(btnCtx), child: Container(padding: const EdgeInsets.symmetric(vertical: 11), child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          if (widget.post.myReaction == null)...[Icon(Icons.thumb_up_alt_outlined, size: 17, color: cs.onSurfaceVariant), const SizedBox(width: 6), Flexible(child: Text(widget.likeLabel, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant, fontWeight: FontWeight.w600)))]
          else...[Text(widget.emojiMap[widget.post.myReaction]?? '👍', style: const TextStyle(fontSize: 17)), const SizedBox(width: 6), Flexible(child: Text(widget.post.myReaction!.toUpperCase(), maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: widget.emojiColor[widget.post.myReaction], fontWeight: FontWeight.bold, fontSize: 11.5)))]
        ])),);
    });
  }
}
class _MediaCarousel extends StatefulWidget { final List<PostMediaModel> mediaList; final String postId; final int postIndex; const _MediaCarousel({super.key, required this.mediaList, required this.postId, required this.postIndex}); @override State<_MediaCarousel> createState() => _MediaCarouselState(); }
class _MediaCarouselState extends State<_MediaCarousel> {
  late PageController _pageController; int _currentPage = 0; bool _showDots = true; final Map<int, VideoPlayerController> _videoControllers = {};
  @override void initState() { super.initState(); _pageController = PageController(); _initVideos(); if (widget.mediaList.length > 1) Future.delayed(const Duration(seconds: 4), () { if (mounted) setState(() => _showDots = false); }); }
  void _initVideos() { for (int i = 0; i < widget.mediaList.length; i++) { if (widget.mediaList[i].mediaType == 'video') { final c = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file)); _videoControllers[i] = c; c.initialize().then((_) { if (mounted) { setState(() {}); if (i==0) { c.setLooping(true); c.setVolume(0); c.play(); } } }); } } }
  @override void dispose() { _pageController.dispose(); for (var c in _videoControllers.values) { c.dispose(); } super.dispose(); }
  void _handleVisibility(bool v, int i) { final c = _videoControllers[i]; if (c!= null && c.value.isInitialized) { if (v) { c.setLooping(true); c.play(); } else { c.pause(); } } }
  Future<void> _openFile(String url, String fileName) async { try { String? token = await AuthService.getToken(); Directory dir = await getTemporaryDirectory(); String savePath = '${dir.path}/${fileName.replaceAll(' ', '_')}'; if (!await File(savePath).exists()) { await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty? {"Authorization": "Bearer $token"} : {})); } await OpenFilex.open(savePath); } catch (e) { if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.openFailed(e.toString())))); } }
  @override Widget build(BuildContext context) { final maxHeight = MediaQuery.of(context).size.height * 0.55; return SizedBox(height: maxHeight, child: Stack(alignment: Alignment.center, children: [PageView.builder(controller: _pageController, itemCount: widget.mediaList.length, onPageChanged: (i) { _videoControllers[_currentPage]?.pause(); setState(() { _currentPage = i; _showDots = true; }); _videoControllers[i]?.setLooping(true); _videoControllers[i]?.play(); Future.delayed(const Duration(seconds: 4), () { if (mounted) setState(() => _showDots = false); }); }, itemBuilder: (c, i) { final m = widget.mediaList[i]; return VisibilityDetector(key: Key('${widget.postId}_$i'), onVisibilityChanged: (info) => _handleVisibility(info.visibleFraction > 0.5, i), child: _buildMediaItem(m, i, maxHeight)); }), if (widget.mediaList.length > 1 && _showDots) Positioned(bottom: 10, child: Container(padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5), decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(20)), child: Row(children: List.generate(widget.mediaList.length, (i) => Container(margin: const EdgeInsets.symmetric(horizontal: 3), width: _currentPage == i? 18 : 7, height: 7, decoration: BoxDecoration(borderRadius: BorderRadius.circular(10), color: _currentPage == i? Colors.white : Colors.white54)))))) ])); }
  // 🎨 TASK 7.2: the video-loading spinner tile and the PDF/generic-file
  // "Open" buttons live inside the normal feed card (not the black
  // full-screen lightbox below), so their old Color(0xFFF0F2F5)/
  // Color(0xFF030F27)/Colors.grey literals are swapped for theme colors.
  // The video-frame background itself stays Colors.black on purpose —
  // that's a letterboxing color for the player, same convention used by
  // every video app regardless of light/dark mode.
  Widget _buildMediaItem(PostMediaModel media, int index, double maxHeight) { final cs = Theme.of(context).colorScheme; if (media.mediaType == 'video') { final c = _videoControllers[index]; if (c == null ||!c.value.isInitialized) return Container(height: maxHeight, color: Colors.black, child: const Center(child: CircularProgressIndicator(color: Colors.white))); return GestureDetector(onTap: () => _openFullScreen(context, index), child: Container(color: Colors.black, child: Center(child: AspectRatio(aspectRatio: c.value.aspectRatio, child: VideoPlayer(c))))); } else if (media.mediaType == 'image') { return GestureDetector(onTap: () => _openFullScreen(context, index), child: CachedNetworkImage(imageUrl: media.file, fit: BoxFit.cover, width: double.infinity, height: maxHeight, memCacheWidth: 1080)); } else if (media.file.toLowerCase().endsWith('.pdf')) { return Stack(children: [SfPdfViewer.network(media.file), Positioned(bottom: 10, right: 10, child: ElevatedButton.icon(onPressed: () => _openFile(media.file, media.fileName.isNotEmpty? media.fileName : 'doc.pdf'), icon: const Icon(Icons.open_in_new), label: Text(AppLocalizations.of(context)!.open), style: ElevatedButton.styleFrom(backgroundColor: cs.primary, foregroundColor: cs.onPrimary)))]); } else { return Container(color: cs.surfaceVariant, child: Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.insert_drive_file, size: 60, color: cs.onSurfaceVariant), const SizedBox(height: 10), Text(media.fileName, style: TextStyle(fontWeight: FontWeight.bold, color: cs.onSurface)), const SizedBox(height: 15), ElevatedButton.icon(onPressed: () => _openFile(media.file, media.fileName), icon: const Icon(Icons.open_in_new), label: Text(AppLocalizations.of(context)!.open), style: ElevatedButton.styleFrom(backgroundColor: cs.primary, foregroundColor: cs.onPrimary)) ]))) ; } }
  void _openFullScreen(BuildContext context, int initialIndex) { _videoControllers.values.forEach((c) => c.pause()); Navigator.push(context, MaterialPageRoute(builder: (_) => _FullScreenViewer(mediaList: widget.mediaList, initialIndex: initialIndex))).then((_) { _videoControllers[_currentPage]?.setLooping(true); _videoControllers[_currentPage]?.play(); }); }
}
class _FullScreenViewer extends StatefulWidget { final List<PostMediaModel> mediaList; final int initialIndex; const _FullScreenViewer({required this.mediaList, required this.initialIndex}); @override State<_FullScreenViewer> createState() => _FullScreenViewerState(); }
class _FullScreenViewerState extends State<_FullScreenViewer> {
  late PageController _controller; late int _currentIndex; final Map<int, VideoPlayerController> _videoControllers = {};
  @override void initState() { super.initState(); _currentIndex = widget.initialIndex; _controller = PageController(initialPage: widget.initialIndex); for (int i = 0; i < widget.mediaList.length; i++) { if (widget.mediaList[i].mediaType == 'video') { final c = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file)); _videoControllers[i] = c; c.initialize().then((_) { if (mounted) { setState(() {}); if (i == _currentIndex) { c.setLooping(true); c.play(); } } }); } } }
  @override void dispose() { for (var c in _videoControllers.values) { c.dispose(); } _controller.dispose(); super.dispose(); }
  Future<void> _openFile(String url, String name) async { String? token = await AuthService.getToken(); Directory dir = await getTemporaryDirectory(); String savePath = '${dir.path}/${name.replaceAll(' ', '_')}'; if (!await File(savePath).exists()) await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty? {"Authorization": "Bearer $token"} : {})); await OpenFilex.open(savePath); }
  // SujhaavFayda1 item 4 — story-ring/avatar disicpline (memCacheWidth)
  // wasn't applied to the post carousel's own fullscreen lightbox: this
  // view lets the user pinch-zoom to 4x, so we don't cap as tight as a
  // thumbnail, but an uncapped CachedNetworkImage still decodes the
  // original resolution (easily 3000px+ from a phone camera) into RAM
  // even though the screen can only ever show devicePixelRatio*width —
  // real spike risk on a long feed with several images opened in a row.
  int _fullScreenMemCacheWidth(BuildContext context) {
    final mq = MediaQuery.of(context);
    // *2 for zoom headroom (maxScale 4.0 here), capped so a huge original
    // can't blow past a sane ceiling regardless of screen size.
    return (mq.size.width * mq.devicePixelRatio * 2).round().clamp(600, 2400);
  }
  @override Widget build(BuildContext context) { return Scaffold(backgroundColor: Colors.black, appBar: AppBar(backgroundColor: Colors.black, iconTheme: const IconThemeData(color: Colors.white), title: Text('${_currentIndex + 1}/${widget.mediaList.length}', style: const TextStyle(color: Colors.white)), actions: [IconButton(icon: const Icon(Icons.open_in_new, color: Colors.white), onPressed: () => _openFile(widget.mediaList[_currentIndex].file, widget.mediaList[_currentIndex].fileName.isNotEmpty? widget.mediaList[_currentIndex].fileName : 'file_$_currentIndex'))]), body: PageView.builder(controller: _controller, itemCount: widget.mediaList.length, onPageChanged: (i) { _videoControllers[_currentIndex]?.pause(); setState(() => _currentIndex = i); _videoControllers[i]?.setLooping(true); _videoControllers[i]?.play(); }, itemBuilder: (c, i) { final m = widget.mediaList[i]; if (m.mediaType == 'video') { final con = _videoControllers[i]; return con!= null && con.value.isInitialized? Center(child: AspectRatio(aspectRatio: con.value.aspectRatio, child: VideoPlayer(con))) : const Center(child: CircularProgressIndicator(color: Colors.white)); } else if (m.file.toLowerCase().endsWith('.pdf')) { return SfPdfViewer.network(m.file); } else if (m.mediaType == 'image') { return InteractiveViewer(minScale: 0.5, maxScale: 4.0, child: CachedNetworkImage(imageUrl: m.file, fit: BoxFit.contain, memCacheWidth: _fullScreenMemCacheWidth(context))); } else { return Center(child: Text(m.fileName, style: const TextStyle(color: Colors.white))); } })); }
}