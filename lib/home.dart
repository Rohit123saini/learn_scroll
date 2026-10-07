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
import 'package:image_picker/image_picker.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:easy_audience_network_plus/easy_audience_network.dart';
import 'services/ad_config.dart'; // Task 15 — real placement ID now build-config driven, not hardcoded
import 'profile/screens/profile.dart';
import 'profile/screens/target_profile.dart';
import 'profile/api_service.dart' as ProfileApi;
import 'search/search.dart';
import 'post/screens/new_post.dart';
import 'post/screens/interests_screen.dart';
import 'post/widgets/comment_sheet.dart';
import 'post/widgets/post_ui.dart';
import 'post/widgets/poll_doubt_widgets.dart'; // TASK G6 — poll voting + doubt answers
import 'post/services/api_service.dart' as PostApi; // TASK G4 — video watch-progress reporting
import 'services/home_api_model_service.dart';
import 'services/auth_service.dart';
import 'services/account_manager.dart'; // P15-FE
import 'services/event_tracker.dart'; // C2-FE — batched impression/dwell analytics
import 'services/session_service.dart';
import 'message/screens/conversations_screen.dart';
import 'tuitionclass/screens/explore_screen.dart';
import 'campus/screens/campus_screen.dart';
import 'wallet/screens/wallet_screen.dart';
import 'notices/screens/notice_board_screen.dart';
import 'widgets/skeletons.dart';
import 'widgets/error_widgets.dart';
import 'widgets/ls_ui.dart';
import 'widgets/ls_network_image.dart'; // C4-FE — blurhash placeholder + size-aware (thumb/medium) images
import 'widgets/feed_video_preloader.dart'; // TASK G17 — feed video pre-buffering
import 'assignments/screens/assignments_screen.dart';
import 'testseries/screens/test_series_screen.dart';
import 'l10n/app_localizations.dart';
import 'notifications/screens/notifications_screen.dart';
import 'notifications/services/notification_service.dart';
import 'message/services/inbox_socket_service.dart'; // N10-FE — live bell badge
import 'post/screens/story_viewer_screen.dart';
import 'post/services/story_service.dart';
import 'post/models/story_model.dart';
import 'post/widgets/story_caption_sheet.dart';
import 'tuitionclass/screens/classroom_detail_screen.dart';
import 'tuitionclass/services/tuitionclass_api_service.dart' as tc_api show TuitionClassApi; // TASK 10.3
import 'tuitionclass/models/tuitionclass_models.dart' as tc_models show ClassSession; // TASK 10.3
import 'tuitionclass/widgets/class_time_chip.dart' show ClassTimeChip; // TASK 10.3
import 'referrals/screens/referrals_screen.dart'; // TASK 1 — home invite strip -> full Refer & Earn page
import 'post/screens/post_list_screen.dart';
import 'post/screens/singlepost.dart' show SinglePostPage, FullScreenImagePage, FullScreenVideoPage, DocumentViewerPage, downloadWithAuth;
import 'post/screens/reels_screen.dart' show ReelsScreen; // P13 — Reels nav tab
import 'post/widgets/post_media_view.dart';
import 'post/screens/ratio_crop_screen.dart';
import 'post/services/post_extras_service.dart' show PostExtrasService, RepostException;
import 'post/widgets/repost_widgets.dart' show EmbeddedOriginalPost, RepostActionButton, RepostHeader, RepostPreview, showRepostCaptionSheet;

// ⚠️ PATHS — ye file `lib/home.dart` hai (lib/home/ folder ke andar NAHI),
// exactly jaisa `main.dart` ka `import 'home.dart';` batata hai. Isliye har
// import `lib/` se relative hai, koi `../` nahi. Pichle version me `../`
// laga hua tha (folder-wali assumption) — wo lib ke BAHAR point karta tha
// aur compile hi nahi hota.
// theme_service.dart / language_service.dart bhi lib/ root pe hain (main.dart
// unhe `import 'theme_service.dart'` se hi uthata hai), services/ folder me
// nahi — ye bhi isi pass me theek hua.

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
// already-built classroom-detail screen (Screen 2 of the tuitionclass
// module — my-pass/stats/wishlist calls, owner/active/expired/pending/
// none states, Enter Class / Renew / Request-to-Join bars, all the
// manage-screen navigation) lives at `lib/tuitionclass/screens/
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

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  late int _selectedIndex = widget.initialIndex;

  List<PostModel> _posts = [];
  // Repost feature: ids of original posts with a repost request in flight
  // (double-tap guard).
  final Set<String> _repostPending = {};
  List<_FeedSlot> _slots = const [];
  bool _isLoading = true;
  bool _isLoadingMore = false;
  bool _feedFailed = false;
  int _currentPage = 1;
  bool _hasMore = true;
  // Home tabs: FeedSource.mixed = "For you", FeedSource.following = "Following".
  // Every feed request + cache read/write below uses this value.
  String _feedSource = FeedSource.mixed;

  // Task 12 — pull-to-refresh vs. load-more race guard. `profile.dart`'s
  // own posts-pagination fix for this same bug class only had a guard
  // (`isLoadingMorePosts || !hasMorePosts || isPostsLoading`) plus a
  // de-dupe-by-id on the merge; that stops duplicates but a load-more
  // response for an old page N landing AFTER a refresh has already reset
  // `_currentPage` to 1 still leaves `_currentPage` pointing at the wrong
  // number afterwards (next load-more then fetches the wrong page and a
  // gap opens up). This counter is the "request-id/token compare" the
  // task separately asks for: every refresh (`_loadFeed`) bumps it, and
  // any `_loadMore()` in flight from an older generation discards its
  // response entirely on arrival instead of touching `_posts`/
  // `_currentPage` at all.
  int _feedGen = 0;

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
  List<TuitionClassModel> _tuitionClasses = [];
  tc_models.ClassSession? _nextClass; // TASK 10.3 — main Home "Next class" card
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
  // 🔥 FIX [Settings/Nav pass] — ab `source: 'tuitionclass'` se fetch hota
  // hai, taaki unread messages iss (bell) badge me count na hon — wo ab
  // sirf Chats tab ke apne badge (`_unreadMessages` neeche) me dikhte hain.
  int _unreadNotifications = 0;

  // 🔥 NAYA [Settings/Nav pass] — Chats tab ka apna badge, message-type
  // notifications se (`source: 'message'`), Instagram ke DM-icon badge
  // jaisa — bell alag, chat-count alag.
  int _unreadMessages = 0;

  // Task 8 — ek hi baar redirect chale, chahe notifier kitni baar bhi fire ho.
  bool _sessionExpiryHandled = false;

  // N10-FE — live bell badge. `InboxSocketService.notificationSync` sirf
  // SIGNAL hai (payload ka unread_count total hai, bell sirf
  // source=tuitionclass dikhata hai), isliye event pe REST refetch hota hai,
  // 400ms debounce ke saath (mark-all-read / burst me ek hi refetch).
  StreamSubscription<Map<String, dynamic>>? _badgeSub;
  Timer? _badgeDebounce;

  // Task 8 — the streak chip + its check-in call used to live here
  // (`_streak`, `_loadStreak()`, `_buildStreakChip`). Moved out entirely:
  // the whole streak UI now lives in `profile/screens/profile.dart`, and
  // the once-per-session `StreakService.checkIn()` call moved with it
  // (see that file's `initState` for the check-in-timing note).

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
    _loadUnreadMessageCount();
    _loadMyUserId();
    _loadFeedAdConfig(); // SujhaavFayda1 item 3
    // Task 8 — streak check-in call moved to profile.dart's initState.
    _scrollController.addListener(_onScroll);
    homeSessionExpiredNotifier.addListener(_onSessionExpired);
    if (homeSessionExpiredNotifier.value) _onSessionExpired();
    // N10-FE — global inbox socket (idempotent; ConversationsScreen kholne ka
    // intezaar nahi) + bell/Chats badge live sync + resume fallback.
    WidgetsBinding.instance.addObserver(this);
    _badgeSub = InboxSocketService.instance.notificationSync.listen((_) => _scheduleBadgeRefresh());
    InboxSocketService.instance.connect();
    // SujhaavFayda1 item 2 — poll every 20s while this screen is alive.
    // Kept deliberately simple (no visibility/lifecycle awareness) — a
    // single lightweight bulk-counts call every 20s is cheap enough not
    // to need pausing on backgrounding for this app's scale; add a
    // WidgetsBindingObserver here later if that stops being true.
    _countsPollTimer = Timer.periodic(const Duration(seconds: 20), (_) => _pollLiveCounts());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _badgeSub?.cancel();
    _badgeDebounce?.cancel();
    homeSessionExpiredNotifier.removeListener(_onSessionExpired);
    _countsPollTimer?.cancel();
    EventTracker.instance.endSurface(EventSurface.feed); // C2-FE — close open dwells + flush
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
    // P15-FE — drop only the expired account; fall into another saved
    // account if one is still valid, otherwise the login screen.
    var switched = false;
    try {
      switched = await AccountManager.instance.logoutCurrent();
    } catch (_) {
      try {
        await AuthService.logout();
      } catch (_) {}
    }
    SessionService.reset();
    _sessionExpiryHandled = false;
    navigatorKey.currentState?.pushNamedAndRemoveUntil(switched ? '/home' : '/login', (_) => false);
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
      HomeExtrasService.getLiveNow().catchError((e) => <TuitionClassModel>[]),
      StoryService.getStories().catchError((e) => <StoryModel>[]),
      HomeExtrasService.getInviteInfo().then<InviteEarnModel?>((v) => v).catchError((e) => null),
      // TASK 10.3 — soonest upcoming/live class (also syncs ServerClock).
      tc_api.TuitionClassApi.dashboard().then<tc_models.ClassSession?>((d) {
        final l = d.upcomingSessions.toList()..sort((a, b) => a.scheduledStart.compareTo(b.scheduledStart));
        return l.isEmpty ? null : l.first;
      }).catchError((e) => null),
    ]);
    if (!mounted) return;
    setState(() {
      _classrooms = results[0] as List<ClassroomModel>;
      _tuitionClasses = results[1] as List<TuitionClassModel>;
      _stories = results[2] as List<StoryModel>;
      _inviteInfo = results[3] as InviteEarnModel?;
      _nextClass = results[4] as tc_models.ClassSession?;
      _extrasLoading = false;
    });
  }

  // Task 1 — bell-icon badge. Skeleton nahi, seedha silent-fail: fail ho to
  // purana count hi dikhta rahe (jhoota "0" flash na ho), crash bilkul nahi.
  Future<void> _loadUnreadCount() async {
    try {
      final count = await NotificationService.instance.getUnreadCount(source: 'tuitionclass');
      if (!mounted) return;
      setState(() => _unreadNotifications = count);
    } catch (_) {
      // silent — purana count as-is rehne do.
    }
  }

  // Task 8 — `_loadStreak()` (the streak check-in call) moved to
  // `profile/screens/profile.dart`'s `initState`.

  // 🔥 NAYA [Settings/Nav pass] — Chats tab badge, same silent-fail pattern.
  Future<void> _loadUnreadMessageCount() async {
    try {
      final count = await NotificationService.instance.getUnreadCount(source: 'message');
      if (!mounted) return;
      setState(() => _unreadMessages = count);
    } catch (_) {
      // silent — purana count as-is rehne do.
    }
  }

  // N10-FE — socket event -> debounced REST refetch of both badges
  // (bell = tuitionclass, Chats = message; ek notification dono me se
  // kisi ek ko hi badalti hai, par payload se pata nahi chalta kisko).
  void _scheduleBadgeRefresh() {
    _badgeDebounce?.cancel();
    _badgeDebounce = Timer(const Duration(milliseconds: 400), () {
      if (!mounted) return;
      _loadUnreadCount();
      _loadUnreadMessageCount();
    });
  }

  // N10-FE — REST fallback. Background me OS socket maar deta hai aur us
  // dauran ke events chhut jaate hain; resume pe socket ko turant
  // reconnect karao (backoff ka intezaar nahi) aur counts REST se
  // sync karo — chahe `isConnected` true dikhe (stale socket ho sakta hai).
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) return;
    InboxSocketService.instance.reconnectNow();
    _loadUnreadCount();
    _loadUnreadMessageCount();
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
    // Task 12 — bump the generation FIRST, synchronously, before any
    // `await`. This is what invalidates an in-flight `_loadMore()` from
    // the previous generation the instant a refresh starts (not just
    // once the refresh's own response lands) — otherwise a load-more
    // response arriving in the gap between "refresh started" and
    // "refresh finished" would still race the refresh's own state update.
    // Guard runs BEFORE the generation bump: a refused (stacked) refresh
    // must not invalidate the request that is already in flight.
    if (refresh && _isLoading) return; // already refreshing — don't stack a second one
    final myGen = ++_feedGen;
    // Tab snapshot — the tab can change while awaiting; the generation
    // check drops the stale response, `source` keeps the request/cache
    // pointed at the tab this call was started for.
    final source = _feedSource;

    if (refresh) {
      setState(() {
        _isLoading = true;
        _feedFailed = false;
        _currentPage = 1;
      });
      try {
        final feed = await HomeFeedService.refreshFeed(page: 1, pageSize: 20, source: source);
        if (!mounted || myGen != _feedGen) return; // Task 12 — stale response, a newer refresh has already superseded this one.
        setState(() {
          _posts = feed.results;
          _isLoading = false;
          _hasMore = feed.next != null;
          _currentPage = 1;
          _savedIds.addAll(feed.results.where((p) => p.isSaved).map((p) => p.id));
          _rebuildFeedSlots();
        });
      } catch (e) {
        if (mounted && myGen == _feedGen) {
          setState(() {
            _isLoading = false;
            _feedFailed = _posts.isEmpty;
          });
        }
      }
      return;
    }

    try {
      final cached = await HomeFeedService.getCachedFeed(source: source);
      if (cached != null && mounted && _posts.isEmpty && myGen == _feedGen) {
        setState(() {
          _posts = cached.results;
          _isLoading = false;
          _hasMore = cached.next != null;
          _savedIds.addAll(cached.results.where((p) => p.isSaved).map((p) => p.id));
          _rebuildFeedSlots();
        });
      }
      final feed = await HomeFeedService.getHomeFeed(page: 1, pageSize: 20, source: source);
      if (!mounted || myGen != _feedGen) return; // Task 12 — a refresh/newer cold-start superseded this call while it was in flight.
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
      if (mounted && myGen == _feedGen) {
        setState(() {
          _isLoading = false;
          _feedFailed = _posts.isEmpty;
        });
      }
    }
  }

  /// "For you" / "Following" tab switch. The old tab's posts are dropped
  /// immediately and the new tab loads cache-first from ITS OWN cache
  /// (see HomeFeedService), so the two lists never bleed into each other.
  /// `_loadFeed()` bumps `_feedGen`, which also discards any load-more /
  /// refresh still in flight for the previous tab.
  void _switchFeedSource(String source) {
    if (source == _feedSource) return;
    HapticFeedback.selectionClick();
    setState(() {
      _feedSource = source;
      _posts = [];
      _rebuildFeedSlots();
      _isLoading = true;
      _isLoadingMore = false;
      _feedFailed = false;
      _currentPage = 1;
      _hasMore = true;
    });
    _loadFeed();
  }

  Future<void> _loadMore() async {
    // Task 12 — `_isLoading` added to this guard so a load-more can't
    // even START while a refresh is in flight (same spirit as
    // `profile.dart`'s `isLoadingMorePosts || !hasMorePosts ||
    // isPostsLoading` guard for its posts pagination).
    if (_isLoadingMore || !_hasMore || _isLoading) return;
    final myGen = _feedGen; // snapshot — NOT incremented; load-more doesn't start a new generation, it just needs to know if one happens to it.
    setState(() => _isLoadingMore = true);
    final requestedPage = _currentPage + 1;
    final source = _feedSource; // same generation => same tab (a tab switch bumps _feedGen)
    try {
      final feed = await HomeFeedService.getHomeFeed(page: requestedPage, pageSize: 20, source: source);
      if (!mounted) return;
      if (myGen != _feedGen) {
        // Task 12 — a refresh started (and possibly already finished)
        // while this page was in flight. `_posts`/`_currentPage` belong
        // to a newer generation now; applying this response would either
        // duplicate posts or, worse, leave `_currentPage` pointing at a
        // page number that doesn't match the now-current sequence at
        // all. Throw it away — the feed will naturally re-request
        // whatever page it actually needs next time the user scrolls.
        setState(() => _isLoadingMore = false);
        return;
      }
      setState(() {
        // De-dupe defense-in-depth (matches `profile.dart`'s pagination
        // fix) even within the same generation — a post that shifted
        // across the page boundary between two quick load-more calls
        // shouldn't show up twice.
        final existingIds = _posts.map((p) => p.id).toSet();
        _posts.addAll(feed.results.where((p) => !existingIds.contains(p.id)));
        _isLoadingMore = false;
        _hasMore = feed.next != null;
        _currentPage = requestedPage;
        _savedIds.addAll(feed.results.where((p) => p.isSaved).map((p) => p.id));
        _rebuildFeedSlots();
      });
      _prefetchNextPageImages(feed.results); // SujhaavFayda1 item 1
    } catch (e) {
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
        // C4-FE — warm the SAME size the feed card renders (medium_720, via `feedUrl`), not the
        // original: a different URL is a guaranteed cache miss and ~10x the bytes for nothing.
        final url = first.mediaType == 'video' ? first.thumbnail : first.feedUrl;
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

  // TASK 2 — Follow button on feed post cards.
  // `_followActionLoadingIds` disables the button mid-request (avoids a
  // double-tap firing two toggles, which the backend already treats
  // idempotently but would flash the wrong local state in between).
  // `_pendingFollowUserIds` tracks authors we've sent a not-yet-accepted
  // request to *this session* (private accounts) so the button can show
  // "Requested" — the backend doesn't send pending state on the initial
  // feed load, only the accepted `is_following` flag.
  final Set<String> _followActionLoadingIds = {};
  final Set<String> _pendingFollowUserIds = {};

  // TASK G16 — optimistic follow, same shape as _toggleSave/_handleReaction
  // above (flip state instantly, reconcile/rollback against the real
  // response). This used to be the odd one out: it put the button into a
  // loading-spinner state and only updated after the round trip, while
  // save/reactions right next to it on the same card already updated
  // instantly. The one thing follow genuinely can't know client-side is
  // whether the target account is private (feed's UserModel has no
  // `isPrivate` flag), so a fresh follow of a private account will
  // instantly show "Following" and then correct itself to "Requested" a
  // moment later when the server responds — same trade-off Instagram makes
  // for this exact case, and unfollow (the far more common tap since the
  // button only shows for accounts you don't already follow) has no such
  // ambiguity at all.
  Future<void> _toggleFollowFromFeed(PostModel post) async {
    final authorId = post.user.id;
    if (authorId.isEmpty || _followActionLoadingIds.contains(authorId)) return;
    HapticFeedback.selectionClick();

    final wasFollowing = post.user.isFollowing;
    final wasPending = _pendingFollowUserIds.contains(authorId);
    setState(() {
      // Optimistic guess: unfollow is unambiguous; a fresh follow assumes
      // the common case (public account, accepted immediately).
      for (final p in _posts) {
        if (p.user.id == authorId) p.user.isFollowing = !wasFollowing;
      }
      _pendingFollowUserIds.remove(authorId);
    });

    try {
      final res = await ProfileApi.ApiService.followUser(int.parse(authorId));
      final status = res['status']?.toString();
      if (!mounted) return;
      setState(() {
        _pendingFollowUserIds.remove(authorId);
        for (final p in _posts) {
          if (p.user.id != authorId) continue;
          if (status == 'PENDING') {
            _pendingFollowUserIds.add(authorId);
            p.user.isFollowing = false;
          } else if (status == 'ACCEPTED') {
            p.user.isFollowing = true;
          } else {
            // status == null -> the toggle just unfollowed/cancelled.
            p.user.isFollowing = false;
          }
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        for (final p in _posts) {
          if (p.user.id != authorId) continue;
          p.user.isFollowing = wasFollowing;
        }
        if (wasPending) _pendingFollowUserIds.add(authorId);
      });
      _snack(AppLocalizations.of(context)!.somethingWentWrong);
    }
  }

  Widget _buildFeedFollowButton(ColorScheme cs, AppLocalizations l10n, PostModel post) {
    final authorId = post.user.id;
    final isLoading = _followActionLoadingIds.contains(authorId);
    final isPending = _pendingFollowUserIds.contains(authorId);
    final label = isPending ? l10n.requestedLabel : l10n.follow;
    return Padding(
      padding: const EdgeInsets.only(left: 8),
      child: Semantics(
        button: true,
        label: label,
        child: Material(
          color: Colors.transparent,
          borderRadius: BorderRadius.circular(20),
          child: InkWell(
            onTap: isLoading ? null : () => _toggleFollowFromFeed(post),
            borderRadius: BorderRadius.circular(20),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(20),
                border: Border.all(color: cs.primary),
              ),
              child: isLoading
                  ? SizedBox(
                      width: 13,
                      height: 13,
                      child: CircularProgressIndicator(strokeWidth: 2, color: cs.primary),
                    )
                  : Text(label,
                      style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: cs.primary)),
            ),
          ),
        ),
      ),
    );
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

  /// Instagram-style double-tap-on-media: always LIKEs, never toggles a
  /// like off (unlike the reaction bar's tap, which does toggle) — a
  /// double tap on an already-liked post should just replay the heart
  /// burst, not silently unlike it.
  void _handleDoubleTapLike(PostModel post) {
    if (post.myReaction != 'like') {
      _handleReaction(post, 'like');
    } else {
      HapticFeedback.lightImpact();
    }
  }

  void _sharePost(PostModel p) {
    // A repost carries no text/media of its own — share the original's.
    final src = p.originalPost ?? p;
    String t = src.content ?? '';
    if (src.media.isNotEmpty) t += '\n\n${src.media.first.file}';
    Share.share(t);
  }

  // ============================================================
  // REPOST — tap = quick repost (one tap, like a retweet), long-press =
  // "Repost with caption" sheet. Backend: POST /post/<id>/repost/.
  // ============================================================

  /// The post a Repost tap acts on: a repost card's button targets its
  /// ORIGINAL (the backend flattens repost-of-a-repost the same way); a
  /// normal post targets itself. Null = repost card whose original is
  /// gone / not visible to this viewer.
  PostModel? _repostTarget(PostModel post) => post.isRepost ? post.originalPost : post;

  /// Applies a repost state change to every copy of [targetId] in the feed —
  /// the post itself and any repost card that embeds it.
  void _applyRepostState(String targetId, {required bool byMe, int? count, int delta = 0}) {
    for (final p in _posts) {
      final hit = p.id == targetId ? p : (p.originalPost?.id == targetId ? p.originalPost : null);
      if (hit == null) continue;
      final next = count ?? (hit.repostsCount + delta);
      hit.repostsCount = next < 0 ? 0 : next;
      hit.isRepostedByMe = byMe;
    }
  }

  void _onRepostTap(PostModel post) {
    final target = _repostTarget(post);
    if (target != null && target.isRepostedByMe) {
      // The backend allows duplicates, but a plain tap on an already
      // reposted post is almost always an accident.
      _snack(AppLocalizations.of(context)!.repostAlready);
      return;
    }
    _repost(post);
  }

  Future<void> _onRepostLongPress(PostModel post) async {
    final target = _repostTarget(post);
    if (target == null) {
      _snack(AppLocalizations.of(context)!.repostOriginalUnavailable);
      return;
    }
    HapticFeedback.selectionClick();
    final caption = await showRepostCaptionSheet(context, original: RepostPreview.fromPost(target));
    if (caption == null || !mounted) return; // cancelled
    await _repost(post, caption: caption);
  }

  Future<void> _repost(PostModel post, {String? caption}) async {
    final l10n = AppLocalizations.of(context)!;
    final target = _repostTarget(post);
    if (target == null) {
      _snack(l10n.repostOriginalUnavailable);
      return;
    }
    if (_repostPending.contains(target.id)) return;
    HapticFeedback.lightImpact();
    _repostPending.add(target.id);
    try {
      final res = await PostExtrasService.repost(target.id, caption: caption);
      if (!mounted) return;
      setState(() => _applyRepostState(target.id, byMe: true, count: res.repostsCount));
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(
          content: Text(l10n.repostDone),
          behavior: SnackBarBehavior.floating,
          action: SnackBarAction(label: l10n.repostUndo, onPressed: () => _undoRepost(target.id, res.repostId)),
        ));
    } on RepostException catch (e) {
      _snack(e.statusCode == 404 ? l10n.repostOriginalUnavailable : l10n.repostFailed);
    } catch (_) {
      _snack(l10n.repostFailed);
    } finally {
      _repostPending.remove(target.id);
    }
  }

  /// Undo = soft-delete the repost row we just created (existing
  /// DELETE /post/<id>/delete/). Delete returns no body, so the count is
  /// adjusted locally.
  Future<void> _undoRepost(String targetId, String repostId) async {
    if (repostId.isEmpty) return;
    final l10n = AppLocalizations.of(context)!;
    try {
      final ok = await PostExtrasService.deletePost(repostId);
      if (!mounted) return;
      if (ok) {
        setState(() => _applyRepostState(targetId, byMe: false, delta: -1));
        _snack(l10n.repostRemoved);
      } else {
        _snack(l10n.repostFailed);
      }
    } catch (_) {
      _snack(l10n.repostFailed);
    }
  }

  void _openOriginalPost(PostModel original) {
    Navigator.push(context, MaterialPageRoute(builder: (_) => SinglePostPage(postId: original.id)));
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
  // TASK 3 (production_readiness_tasks.md) — Interests entry point.
  void _openInterests() => Navigator.push(context, MaterialPageRoute(builder: (_) => const InterestsScreen()));
  void _openChat() async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => const ConversationsScreen()));
    // Wapas aane pe Chats badge refresh — andar messages read ho chuke ho sakte hain.
    _loadUnreadMessageCount();
  }
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

  /// `.icon-btn` — TASK 5 (production_readiness_tasks.md): bumped from a
  /// 34px circle / 16px glyph to a 44x44 circle / 20px glyph so the
  /// bell/compose/interests buttons in the top bar both read clearly next
  /// to the 15.5px brand title AND meet the ≥44x44 minimum touch-target
  /// size (WCAG 2.5.5 / Material accessibility guidance) — previously the
  /// 34px circle was both the visible AND the tappable area, under target.
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
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: isGradient ? null : cs.surface,
                gradient: gradient,
                border: isGradient ? null : Border.all(color: cs.outlineVariant),
              ),
              child: Icon(icon, size: 20, color: isGradient ? cs.onPrimary : cs.onSurface),
            ),
            // `.notif-badge`
            if (badge > 0)
              Positioned(
                top: -2,
                right: -2,
                child: Container(
                  constraints: const BoxConstraints(minWidth: 16),
                  height: 16,
                  padding: const EdgeInsets.symmetric(horizontal: 3),
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: cs.secondary,
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: lsBg(context), width: 1.5),
                  ),
                  child: Text(
                    badge > 9 ? '9+' : '$badge',
                    style: TextStyle(fontSize: 9.5, fontWeight: FontWeight.w700, color: cs.onSecondary, height: 1.2),
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

  // Task 8 — `_buildStreakChip`/`_showStreakDetailsSheet`/`_streakStat`
  // moved to `profile/screens/profile.dart` (as a proper profile section
  // instead of an app-bar chip + bottom sheet).

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
      // Task 8 — streak chip removed from the home app-bar entirely
      // (moved to profile.dart, see that file's header section).
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
      // TASK 3 (production_readiness_tasks.md) — Interests entry point.
      // Opens the chip picker (interests_screen.dart); selections feed
      // HomeFeedView/ExploreFeedAPIView's ranking bonus server-side.
      _lsIconButton(
        cs: cs,
        icon: Icons.interests_rounded,
        tooltip: 'Interests',
        onTap: _openInterests,
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
                    // (int) + an optional `initial` typed to tuitionclass's own
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
                        MaterialPageRoute(builder: (_) => ClassroomDetailScreen(classroomId: classroomId)),
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
    // TASK 1 — tapping the strip itself (anywhere outside the small
    // "Invite" CTA below, which keeps its own instant-share tap target)
    // now opens the full Refer & Earn page (ReferralsScreen), so users
    // get a dedicated place to see per-classroom %commission + pending
    // amount, not just the instant share-sheet the CTA triggers.
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () {
        HapticFeedback.selectionClick();
        Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => const ReferralsScreen()),
        );
      },
      child: Container(
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
      ),
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
            closeFriends: group.hasUnviewedCloseFriends,
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

  // Shared geometry for every tile in the row — own-story tile, other
  // users' tiles, AND `LsStoriesSkeleton`'s loading placeholders all use
  // these same numbers, so nothing shifts width when real data replaces
  // the skeleton or when a tile flips between viewed/unviewed.
  static const double _kStoryTileWidth = 74;
  static const double _kStoryTileGap = 14;
  static const TextStyle _kStoryLabelStyle = TextStyle(fontSize: 10.5, fontWeight: FontWeight.w600, height: 1.15);

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
        width: _kStoryTileWidth,
        margin: const EdgeInsets.only(right: _kStoryTileGap),
        child: Column(children: [
          Stack(
            clipBehavior: Clip.none,
            children: [
              _storyRing(
                cs,
                thumbnailUrl: mine?.userProfilePic,
                viewed: mine?.allViewed ?? true, // plain ring when empty — nothing to "unviewed"
                closeFriends: mine?.hasUnviewedCloseFriends ?? false,
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
            textAlign: TextAlign.center,
            style: _kStoryLabelStyle.copyWith(color: cs.onSurfaceVariant),
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
    bool closeFriends = false,
    required VoidCallback onTap,
  }) {
    final initials = label.isEmpty ? '?' : label.substring(0, label.length >= 2 ? 2 : label.length).toUpperCase();
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: _kStoryTileWidth,
        margin: const EdgeInsets.only(right: _kStoryTileGap),
        child: Column(children: [
          _storyRing(cs, thumbnailUrl: thumbnailUrl, viewed: viewed, closeFriends: closeFriends, fallback: _storyInitials(initials, cs)),
          const SizedBox(height: 6),
          Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            textAlign: TextAlign.center,
            // Unviewed = full-strength text, viewed = a touch muted —
            // mirrors the ring state so a glanced-at row instantly reads
            // which tiles still have something new (Instagram convention).
            style: _kStoryLabelStyle.copyWith(
              color: viewed ? cs.onSurfaceVariant.withOpacity(0.72) : cs.onSurfaceVariant,
              fontWeight: viewed ? FontWeight.w500 : FontWeight.w600,
            ),
          ),
        ]),
      ),
    );
  }

  // 🎨 Instagram-style ring — outer 72, fixed ring band, fixed inner gap,
  // fixed avatar. Both states (gradient = unviewed, muted grey = viewed)
  // are drawn the SAME way — a circle `color`/`gradient` fill on the
  // outer band, not a `Border.all` — so the ring is always exactly
  // `_kStoryRingBand` thick and the avatar is always exactly the same
  // size in both states.
  //
  // Real bug this replaces: the old version filled the unviewed ring via
  // padding (3px auto-filled by the gradient) but drew the viewed ring
  // via `Border.all(width: 2)` — 1px thinner, and a stroke instead of a
  // fill, so a transparent 1px sliver showed between the grey border and
  // the avatar that never appeared on the gradient ring. Two different
  // ring thicknesses next to each other in the same row is exactly the
  // kind of thing that reads as "unpolished" at a glance.
  //
  // The `_kStoryRingGap` layer is new too — a thin ring-colored gap
  // between the colored band and the photo, same as Instagram's own
  // avatar rings, so the ring doesn't look like it's just a thick
  // colored border glued to the photo.
  static const double _kStoryRingSize = 72;
  static const double _kStoryRingBand = 3;
  static const double _kStoryRingGap = 2;

  Widget _storyRing(ColorScheme cs, {String? thumbnailUrl, required bool viewed, bool closeFriends = false, required Widget fallback}) {
    return Container(
      width: _kStoryRingSize,
      height: _kStoryRingSize,
      padding: const EdgeInsets.all(_kStoryRingBand),
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        // Unviewed: warm brand gradient (accent → primary), 3 stops for a
        // smoother sweep than a flat 2-color blend. Viewed: flat muted
        // grey fill — same band thickness, no gradient, no border.
        // Stories upgrade, Part 1: an unseen Close Friends story gets Instagram's
        // green ring instead of the brand gradient.
        gradient: viewed
            ? null
            : LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: closeFriends
                    ? const [Color(0xFF7ED957), Color(0xFF2BB673), Color(0xFF1E9E63)]
                    : [cs.secondary, Color.lerp(cs.secondary, cs.primary, 0.55)!, cs.primary],
              ),
        color: viewed ? cs.outlineVariant : null,
      ),
      child: Container(
        padding: const EdgeInsets.all(_kStoryRingGap),
        decoration: BoxDecoration(shape: BoxShape.circle, color: cs.surface),
        child: ClipOval(
          child: thumbnailUrl != null && thumbnailUrl.isNotEmpty
              // Task 10.2 — memCache size set, warna 1080px image poori RAM
              // me jaati hai for what renders as a ~57px circle.
              ? CachedNetworkImage(
                  imageUrl: thumbnailUrl,
                  fit: BoxFit.cover,
                  memCacheWidth: 200,
                  errorWidget: (_, __, ___) => fallback,
                )
              : fallback,
        ),
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

    var file = File(picked.path);
    // 1.3-FE — stories are 9:16: photos go through the fixed-ratio crop first
    // (cancel = don't post). Videos are not cropped; the viewer letterboxes them.
    if (mediaType == 'image') {
      final cropped = await showRatioCrop(context, file, aspect: 9 / 16, title: l10n.storyCropTitle, outputWidth: 1080);
      if (cropped == null || !mounted) return;
      file = cropped;
    }
    // Task 11 — caption step. `StoryService.createStory` already accepted
    // an optional caption; the flow just never asked for one before this.
    final composed = await showStoryCaptionSheet(context, media: file, mediaType: mediaType);
    if (composed == null || !mounted) return; // closed without tapping Share — don't upload
    final caption = composed.caption;

    setState(() { _storyUploadBusy = true; _storyUploadProgress = 0; });
    try {
      await StoryService.createStory(
        media: file,
        mediaType: mediaType,
        caption: caption.isEmpty ? null : caption,
        audience: composed.audience,
        stickers: composed.stickers, // Stories upgrade, Part 2: mention / link / poll / question overlays
        onProgress: (p) { if (mounted) setState(() => _storyUploadProgress = p); },
      );
      await _loadHomeExtras();
    } catch (e) {
      if (mounted) _snack(e is Exception ? e.toString().replaceFirst('Exception: ', '') : l10n.couldNotUploadStory);
    } finally {
      if (mounted) setState(() { _storyUploadBusy = false; _storyUploadProgress = 0; });
    }
  }

  /// TASK 10.3 — "Next class": the user's soonest upcoming/live class with a
  /// local-time chip ("Starts in 2h 10m" / "Live now"). Hidden when none.
  Widget _buildNextClass(ColorScheme cs, AppLocalizations l10n) {
    final s = _nextClass;
    if (s == null) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 20),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _sectionTitle(l10n.homeNextClass, cs),
        const SizedBox(height: 10),
        Material(
          color: cs.surfaceContainerHighest.withValues(alpha: 0.5),
          borderRadius: BorderRadius.circular(14),
          child: InkWell(
            borderRadius: BorderRadius.circular(14),
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => ClassroomDetailScreen(classroomId: s.classroomId)),
            ),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Row(children: [
                Icon(Icons.event_available_rounded, color: cs.primary),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(s.classroomTitle,
                        maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14)),
                    const SizedBox(height: 6),
                    ClassTimeChip.session(s, compact: true),
                  ]),
                ),
                Icon(Icons.chevron_right_rounded, color: cs.onSurfaceVariant),
              ]),
            ),
          ),
        ),
      ]),
    );
  }

  /// `.section` (Live now) — `.live-card` horizontal row.
  Widget _buildLiveNow(ColorScheme cs, AppLocalizations l10n) {
    if (_extrasLoading && _tuitionClasses.isEmpty) {
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
    if (_tuitionClasses.isEmpty) return const SizedBox.shrink();

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
            itemCount: _tuitionClasses.length,
            itemBuilder: (c, i) {
              final l = _tuitionClasses[i];
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
                          MaterialPageRoute(builder: (_) => ClassroomDetailScreen(classroomId: classroomId)),
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
  /// Assignments, Test Series, Wallet aur ab Notices bhi asli screens pe
  /// jaate hain.
  /// ✅ Task 12 fix — Notices ab "coming soon" nahi hai. Campus ke andar
  /// `NoticesScreen` ko `CampusAccess` chahiye (ek waqt me ek campus ka
  /// scope), isliye home.dart use seedha open nahi kar sakta — iski
  /// jagah `NoticeBoardScreen` khulti hai, jo naye `core/notice-board/`
  /// endpoint se campus ke SAARE notices + user ki tuition-class classrooms
  /// ke notices, dono ek hi merged feed me dikhati hai (koi
  /// `CampusAccess` load karne ki zaroorat nahi).
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
        // ✅ Task 12 — merged campus + tuition-class Notice Board.
        'tap': () {
          Navigator.push(
            context,
            MaterialPageRoute(builder: (_) => const NoticeBoardScreen()),
          );
        },
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

  /// `.feed-title` — title + "For you" / "Following" tabs.
  Widget _buildFeedTitle(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _sectionTitle(l10n.yourFeed, cs),
        const SizedBox(height: 8),
        _buildFeedTabs(cs, l10n),
      ]),
    );
  }

  Widget _buildFeedTabs(ColorScheme cs, AppLocalizations l10n) {
    Widget tab(String source, String label) {
      final selected = _feedSource == source;
      return Expanded(
        child: Semantics(
          button: true,
          selected: selected,
          label: label,
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: () => _switchFeedSource(source),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 180),
              padding: const EdgeInsets.symmetric(vertical: 8),
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: selected ? cs.primary : Colors.transparent,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(
                label,
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w700,
                  color: selected ? cs.onPrimary : cs.onSurfaceVariant,
                ),
              ),
            ),
          ),
        ),
      );
    }

    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        color: cs.surface,
        border: Border.all(color: cs.outlineVariant),
        borderRadius: BorderRadius.circular(13),
      ),
      child: Row(children: [
        tab(FeedSource.mixed, l10n.feedTabForYou),
        tab(FeedSource.following, l10n.following),
      ]),
    );
  }

  /// "Suggested for you" / "Trending" strip on top of a card, driven by the
  /// backend's per-post `feed_source`. Followed posts (and posts without a
  /// source) get no strip.
  Widget _buildFeedSourceBadge(PostModel post, ColorScheme cs, AppLocalizations l10n) {
    final trending = post.isTrending;
    final Color fg = trending ? _lsAmber : cs.primary;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
      color: fg.withOpacity(.08),
      child: Row(children: [
        Icon(trending ? Icons.local_fire_department_rounded : Icons.auto_awesome_rounded, size: 14, color: fg),
        const SizedBox(width: 6),
        Text(
          trending ? l10n.feedBadgeTrending : l10n.feedBadgeSuggested,
          style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: fg),
        ),
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
    if (!kDebugMode && !AdConfig.isConfigured) {
      // Release build with no real placement ID configured — falling
      // back to the test placement (see placementId below) instead of
      // shipping a placeholder string. This should never fire once
      // release builds pass --dart-define=META_NATIVE_AD_PLACEMENT_ID.
      debugPrint(
        'AdConfig: META_NATIVE_AD_PLACEMENT_ID not set in this release build — '
        'falling back to NativeAd.testPlacementId, real ad revenue will not be earned.',
      );
    }
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
          // Meta's test placement; release builds use the real one —
          // read from build-time config now (Task 15, `AdConfig`,
          // `lib/services/ad_config.dart`), not a hardcoded placeholder
          // string. If a release build ships without
          // --dart-define=META_NATIVE_AD_PLACEMENT_ID=... set, fall back
          // to the test placement (logged once below) instead of handing
          // the native SDK an empty/placeholder ID, which would surface
          // as a native-side load error with no real explanation.
          placementId: (kDebugMode || !AdConfig.isConfigured)
              ? NativeAd.testPlacementId
              : AdConfig.metaNativeAdPlacementId,
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
    final repostTarget = _repostTarget(post);

    // TASK G17 (growth_and_feature_tasks.md) — once this card is mostly
    // on-screen, kick off buffering for the *next* post's video in the
    // background so scrolling to it doesn't start from zero. See
    // `_maybePreloadNextVideo` / `FeedVideoPreloader`.
    return VisibilityDetector(
      key: Key('feed_post_vis_${post.id}'),
      onVisibilityChanged: (info) {
        // C2-FE — impression (once/session) + dwell/skip, batched by EventTracker.
        EventTracker.instance.onVisibility(post.id, info.visibleFraction);
        if (info.visibleFraction > 0.5) _maybePreloadNextVideo(idx);
      },
      child: Container(
      // FIX (Task 1 — feed card corners + sizing): card ab full available
      // width leta hai (side gutters hata diye — pehle `kLsPad` left/right
      // margin card ko screen se andar khinch deta tha). Height ko is
      // change ne touch nahi kiya — media ka `maxHeight` (_MediaCarousel
      // me `MediaQuery...*0.55`) aur baaki padding/spacing waisi hi hai,
      // sirf horizontal margin 0 hui hai. Corner radius poore card pe
      // (header se footer tak) already `clipBehavior: Clip.antiAlias` +
      // `borderRadius: kLsRadius` se uniformly apply hota hai — koi child
      // (media/header/footer) apna alag radius define nahi karta, isliye
      // "complete corners" already guaranteed hain, bas ab full-width pe.
      margin: const EdgeInsets.fromLTRB(0, 0, 0, 14),
      decoration: BoxDecoration(
        color: cs.surface,
        border: Border.all(color: cs.outlineVariant),
        borderRadius: BorderRadius.circular(kLsRadius),
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (post.isSuggested || post.isTrending) _buildFeedSourceBadge(post, cs, l10n),
        // `.post-head` — a repost swaps the avatar header for "Reposted by <user>"
        if (post.isRepost)
          RepostHeader(
            username: post.user.username,
            createdAt: post.createdAt,
            onUserTap: () => _goToProfile(post.user.username),
            trailing: Semantics(
              button: true,
              label: l10n.save,
              child: IconButton(
                visualDensity: VisualDensity.compact,
                icon: Icon(isSaved ? Icons.bookmark : Icons.bookmark_border,
                    size: 20, color: isSaved ? cs.primary : cs.onSurfaceVariant),
                onPressed: () => _toggleSave(post),
              ),
            ),
          )
        else
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
                      style: TextStyle(fontSize: 13, fontWeight: FontWeight.w800, color: cs.onSurface)),
                  // `.post-meta`
                  const SizedBox(height: 1),
                  Text(showCat ? '$cat · $timeLabel' : timeLabel,
                      style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w500, color: cs.onSurfaceVariant)),
                ]),
              ),
            ),
            // TASK 2 — hidden for the viewer's own posts and once they
            // already follow the author (per spec: "hide if already
            // following"); shown as Follow / Requested otherwise.
            if (!post.user.isOwnPost && !post.user.isFollowing)
              _buildFeedFollowButton(cs, l10n, post),
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
              runSpacing: 6,
              children: post.hashtags
                  .map((t) => PostTagChip(
                        label: t,
                        hashtag: true,
                        emphasized: true,
                        onTap: () => Navigator.push(
                            context, MaterialPageRoute(builder: (_) => PostListScreen.hashtag(t))),
                      ))
                  .toList(),
            ),
          ),
        if (post.media.isNotEmpty)
          DoubleTapLikeOverlay(
            onDoubleTapLike: () => _handleDoubleTapLike(post),
            child: _MediaCarousel(key: ValueKey(post.id), mediaList: post.media, postId: post.id, postIndex: idx),
          ),
        // repost: reposter's optional caption + embedded original-post preview
        if (post.isRepost) ...[
          if ((post.repostCaption ?? '').trim().isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
              child: Text(post.repostCaption!.trim(),
                  style: TextStyle(fontSize: 13, height: 1.5, color: cs.onSurface.withOpacity(.92))),
            ),
          EmbeddedOriginalPost(
            preview: post.originalPost == null ? null : RepostPreview.fromPost(post.originalPost!),
            onTap: post.originalPost == null ? null : () => _openOriginalPost(post.originalPost!),
          ),
        ],
        // TASK G6 — poll (any post_type can carry one) + "Ask a doubt"
        // question/answer summary (post_type == 'doubt' only).
        if (post.poll != null)
          PollCardWidget(
            postId: post.id,
            poll: post.poll!,
            onVoted: (updated) => setState(() => post.poll = updated),
          ),
        if (post.isDoubt)
          DoubtSummaryWidget(
            answersCount: post.answersCount,
            bestAnswer: post.bestAnswer,
            onTap: () => DoubtAnswersSheet.open(
              context,
              postId: post.id,
              isOwnPost: post.user.isOwnPost,
              onAnswersCountChanged: (count) => setState(() => post.answersCount = count),
            ),
          ),
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
            child: RepostActionButton(
              active: repostTarget?.isRepostedByMe ?? false,
              count: repostTarget?.repostsCount ?? 0,
              onTap: () => _onRepostTap(post),
              onLongPress: () => _onRepostLongPress(post),
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
      ),
    );
  }

  // TASK G17 — see the VisibilityDetector wrapping this card's Container
  // above. Only looks at the *next* post (not further ahead) and only its
  // first video, matching the "pre-buffer next 1-2 videos" spec — going
  // further would mean buffering posts the user may never scroll to.
  void _maybePreloadNextVideo(int postIndex) {
    final nextIndex = postIndex + 1;
    if (nextIndex >= _posts.length) return;
    for (final m in _posts[nextIndex].media) {
      if (m.mediaType == 'video' && m.file.isNotEmpty) {
        FeedVideoPreloader.instance.preload(m.file);
        break;
      }
    }
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
              _buildNextClass(cs, l10n),
              _buildLiveNow(cs, l10n),
              _buildQuickActionsGrid(cs, l10n),
              _buildFeedTitle(cs, l10n),
            ]),
          ),

          // ---------- feed ----------
          if (_isLoading && _posts.isEmpty)
            SliverList(
              delegate: SliverChildBuilderDelegate(
                // FIX (Task 1) — real card ab full-width hai (side margin
                // 0), skeleton ko bhi match karna zaroori hai warna
                // loading→loaded transition pe card ka width achanak
                // badalta dikhega.
                (context, i) => LsPostCardSkeleton(sidePad: 0, withMedia: i == 1),
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

  // TASK 9 — hardware/gesture back handling for the app's root screen.
  //
  // Before this: `HomeScreen` had no `PopScope`/`WillPopScope` at all, so
  // a back-press here fell straight through to Flutter's default —
  // Android just closes/finishes the Activity with no confirmation, and
  // (on the OEMs/task-managers Android is affected by) the swiped-away
  // task gets a fresh cold start (`main()` -> `runApp` -> `_checkAuth()`
  // FutureBuilder spinner -> `HomeScreen` again) the next time it's
  // reopened. That fresh cold start — feed reset to the top, tab back on
  // Home, nothing restored — is exactly what read as "the whole app
  // reloads/restarts" (`bhar aa jata hai`) even though technically nothing
  // was wrong with any individual screen's own back navigation (every
  // pushed screen already used plain `Navigator.push`/`MaterialPageRoute`,
  // which pops one screen at a time correctly on its own).
  //
  // Fix, in order, when back is pressed on THIS screen specifically:
  //   1. Not on the Home tab (Search/Profile, `_selectedIndex` 1/2) ->
  //      just return to the Home tab, like Instagram/most feed apps —
  //      this is the "navigate back one screen normally" part for the
  //      bottom-nav's in-place tabs (they don't go through the Navigator
  //      at all, so the Navigator itself has nothing to pop here).
  //   2. Already on the Home tab (root) -> show the "Exit app?" dialog
  //      instead of silently exiting or falling through to the OS
  //      default; only `SystemNavigator.pop()` on explicit confirmation.
  // Pushed screens (Campus, Chat, post detail, etc.) are unaffected —
  // they're additional routes ABOVE this one, so the platform back
  // button already pops them one at a time before ever reaching here.
  Future<void> _onBackPressed() async {
    if (_selectedIndex != 0) {
      setState(() => _selectedIndex = 0);
      return;
    }
    final l10n = AppLocalizations.of(context)!;
    final shouldExit = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Exit app?'),
        content: const Text('Are you sure you want to close the app?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Exit')),
        ],
      ),
    );
    if (shouldExit == true) {
      SystemNavigator.pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return PopScope(
      // `canPop: false` on this ROOT screen only — every screen pushed
      // on top of it (Campus, Chat, post detail, ...) is a separate
      // route with its own default pop behavior and is never affected
      // by this; this only intercepts back once the user is back down
      // to the app's home route with nothing left above it to pop.
      canPop: false,
      onPopInvokedWithResult: (didPop, result) {
        if (didPop) return;
        _onBackPressed();
      },
      child: Scaffold(
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
            // P13 — Reels: full-screen vertical video feed (pushed, so it has no nav of its own).
            LsBottomNavItemData(icon: Icons.movie_outlined, activeIcon: Icons.movie_rounded, label: l10n.reelsTab),
            LsBottomNavItemData(icon: Icons.school_outlined, activeIcon: Icons.school_rounded, label: l10n.campusTab),
            LsBottomNavItemData(
                icon: Icons.smart_display_outlined, activeIcon: Icons.smart_display_rounded, label: l10n.classesTab),
            LsBottomNavItemData(
                icon: Icons.chat_bubble_outline_rounded,
                activeIcon: Icons.chat_bubble_rounded,
                label: l10n.chatTab,
                badge: _unreadMessages),
            LsBottomNavItemData(icon: Icons.person_outline_rounded, activeIcon: Icons.person_rounded, label: l10n.profileTab),
          ],
          // Sirf Home(0)/Profile(2) `_selectedIndex` ke IndexedStack tabs hain —
          // Reels/Campus/Classes/Chat tap hote hi apna screen push karte hain, isliye
          // "active" kabhi unke liye highlight nahi hota (§ comment neeche).
          // Nav order: 0 Home, 1 Reels, 2 Campus, 3 Classes, 4 Chat, 5 Profile.
          activeIndex: _selectedIndex == 0 ? 0 : (_selectedIndex == 2 ? 5 : -1),
          onTap: (i) {
            HapticFeedback.selectionClick();
            switch (i) {
              case 0:
                setState(() => _selectedIndex = 0);
              case 1:
                ReelsScreen.open(context);
              case 2:
                // ✅ CAMPUS — ab CampusScreen wired hai, "coming soon" snackbar nahi.
                Navigator.push(context, MaterialPageRoute(builder: (_) => const CampusScreen()));
              case 3:
                _openExplore();
              case 4:
                _openChat();
              case 5:
                setState(() => _selectedIndex = 2);
            }
          },
        ),
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
  bool _muted = true; // feed videos autoplay muted; the speaker button toggles all slides
  // TASK G17 — bookkeeping for the current-slide-plus-next buffering
  // strategy below (`_initVideoAt`): which indices have an in-flight
  // `initialize()` call, and whether each index should autoplay once its
  // controller becomes ready (it may become the current page before its
  // own initialize() call — kicked off as a "next slide" preload —
  // actually finishes).
  final Set<int> _initializing = {};
  final Map<int, bool> _wantAutoplay = {};
  @override void initState() { super.initState(); _pageController = PageController(); _initVideos(); if (widget.mediaList.length > 1) Future.delayed(const Duration(seconds: 4), () { if (mounted) setState(() => _showDots = false); }); }
  // TASK G17 (growth_and_feature_tasks.md) — used to eagerly `initialize()`
  // *every* video in the carousel at once, which is exactly the "scroll
  // feels laggy" problem for a multi-video post: N simultaneous
  // connections/buffers competing for bandwidth instead of one. Now only
  // the current slide (autoplaying) and the next slide (silently
  // buffering ahead of time) are initialized up front; further slides are
  // picked up one at a time as the user swipes, in `onPageChanged` below.
  void _initVideos() {
    _initVideoAt(0, autoplay: true);
    if (widget.mediaList.length > 1) _initVideoAt(1, autoplay: false);
  }
  // Creates (or reuses an app-wide pre-buffered controller for — see
  // `FeedVideoPreloader`) the video controller at slide [i], and plays it
  // once ready IF [autoplay] (or an earlier call already asked for
  // autoplay at this index) and it's still the currently-shown slide by
  // the time it's ready. Safe to call more than once for the same index.
  void _initVideoAt(int i, {required bool autoplay}) {
    if (i < 0 || i >= widget.mediaList.length) return;
    final m = widget.mediaList[i];
    if (m.mediaType != 'video') return;
    _wantAutoplay[i] = autoplay || (_wantAutoplay[i] ?? false);
    var c = _videoControllers[i];
    if (c == null) {
      c = FeedVideoPreloader.instance.take(m.file) ?? VideoPlayerController.networkUrl(Uri.parse(m.file));
      _videoControllers[i] = c;
    }
    if (c.value.isInitialized) {
      if (mounted) setState(() {});
      if ((_wantAutoplay[i] ?? false) && i == _currentPage) { c.setLooping(true); c.setVolume(_muted ? 0 : 1); c.play(); }
      return;
    }
    if (_initializing.contains(i)) return; // already buffering; its own .then() will handle autoplay
    _initializing.add(i);
    c.initialize().then((_) {
      _initializing.remove(i);
      if (!mounted) return;
      setState(() {});
      final ctrl = _videoControllers[i];
      if (ctrl != null && (_wantAutoplay[i] ?? false) && i == _currentPage) { ctrl.setLooping(true); ctrl.setVolume(_muted ? 0 : 1); ctrl.play(); }
    });
  }
  @override void dispose() { _pageController.dispose(); _videoControllers.forEach((i, c) { _reportProgress(i, c); c.dispose(); }); super.dispose(); }
  void _handleVisibility(bool v, int i) { final c = _videoControllers[i]; if (c!= null && c.value.isInitialized) { if (v) { c.setLooping(true); c.play(); } else { c.pause(); _reportProgress(i, c); } } }
  // TASK G4 (growth_and_feature_tasks.md) — best-effort watch-progress
  // report so the feed backend can rank by video-completion-rate. Fired
  // on pause (scroll-away) and dispose, not per-frame; failures are
  // swallowed on purpose (ApiService.reportVideoProgress's own docstring)
  // — this must never interrupt playback or surface an error to the user.
  void _reportProgress(int i, VideoPlayerController c) { if (widget.mediaList[i].mediaType!= 'video' ||!c.value.isInitialized) return; final seconds = c.value.position.inMilliseconds / 1000.0; if (seconds <= 0) return; PostApi.ApiService().reportVideoProgress(widget.postId, seconds).catchError((_) {}); }
  void _toggleMute() { setState(() => _muted = !_muted); _videoControllers.values.forEach((c) { if (c.value.isInitialized) c.setVolume(_muted ? 0 : 1); }); }
  void _openDoc(PostMediaModel m) { Navigator.push(context, MaterialPageRoute(builder: (_) => DocumentViewerPage(url: m.file, fileName: PostMediaUtil.displayName(m)))); }
  @override Widget build(BuildContext context) {
    // Frame height follows the first image/video's real aspect ratio (see PostMediaUtil.frameHeight) instead of a fixed 55% of the screen, so nothing is cropped and there is no dead space.
    return LayoutBuilder(builder: (context, cons) {
      final height = PostMediaUtil.frameHeight(width: cons.maxWidth, media: widget.mediaList, maxHeight: MediaQuery.of(context).size.height * 0.62);
      final multi = widget.mediaList.length > 1;
      final hasVideo = widget.mediaList.any((m) => PostMediaUtil.kind(m) == PostMediaKind.video);
      return SizedBox(height: height, child: ColoredBox(color: Colors.black, child: Stack(alignment: Alignment.center, children: [
        PageView.builder(controller: _pageController, itemCount: widget.mediaList.length, onPageChanged: (i) { final prev = _videoControllers[_currentPage]; if (prev != null) { prev.pause(); _reportProgress(_currentPage, prev); } setState(() { _currentPage = i; _showDots = true; }); _initVideoAt(i, autoplay: true); _initVideoAt(i + 1, autoplay: false); /* TASK G17 — keep one slide ahead buffered */ Future.delayed(const Duration(seconds: 4), () { if (mounted) setState(() => _showDots = false); }); }, itemBuilder: (c, i) { final m = widget.mediaList[i]; return VisibilityDetector(key: Key('${widget.postId}_$i'), onVisibilityChanged: (info) => _handleVisibility(info.visibleFraction > 0.5, i), child: _buildMediaItem(m, i, height)); }),
        if (multi) Positioned(top: 10, left: 10, child: PostMediaCounter(index: _currentPage, count: widget.mediaList.length)),
        if (hasVideo && PostMediaUtil.kind(widget.mediaList[_currentPage]) == PostMediaKind.video) Positioned(top: 10, right: 10, child: PostMediaIconButton(icon: _muted ? Icons.volume_off_rounded : Icons.volume_up_rounded, onTap: _toggleMute)),
        if (multi && _showDots) Positioned(bottom: 10, child: PostCarouselDots(count: widget.mediaList.length, activeIndex: _currentPage)),
      ])));
    });
  }
  // 🎨 TASK 7.2: the video-loading spinner tile and the PDF/generic-file
  // "Open" buttons live inside the normal feed card (not the black
  // full-screen lightbox below), so their old Color(0xFFF0F2F5)/
  // Color(0xFF030F27)/Colors.grey literals are swapped for theme colors.
  // The video-frame background itself stays Colors.black on purpose —
  // that's a letterboxing color for the player, same convention used by
  // every video app regardless of light/dark mode.
  Widget _buildMediaItem(PostMediaModel media, int index, double frameHeight) {
    switch (PostMediaUtil.kind(media)) {
      case PostMediaKind.video:
        final c = _videoControllers[index];
        if (c == null || !c.value.isInitialized) return PostVideoLoading(thumbnail: media.thumbnail);
        // Letterboxed (contain) on black — never cropped, tap opens the full-screen player.
        return GestureDetector(onTap: () => _openFullScreen(context, index), behavior: HitTestBehavior.opaque, child: Center(child: AspectRatio(aspectRatio: c.value.aspectRatio, child: VideoPlayer(c))));
      case PostMediaKind.image:
        // C4-FE — feed shows the ~720px `medium_url` (the API falls back to the original for
        // old posts / animated GIFs) with a BlurHash placeholder until it loads. The original
        // is only downloaded when the user taps through to full screen (_openFullScreen below).
        // Was: PostImageTile(url: media.file, onTap: () => _openFullScreen(context, index));
        // 1.2-FE — now blur + contain (PostFeedImage), same look as the single-post
        // screen, instead of a bare contain on a flat background.
        return PostFeedImage(url: media.feedUrl, fallbackUrl: media.file, blurhash: media.blurhash, onTap: () => _openFullScreen(context, index));
      case PostMediaKind.pdf:
      case PostMediaKind.doc:
        final name = PostMediaUtil.displayName(media);
        return PostDocTile(fileName: name, ext: PostMediaUtil.ext(media.fileName.isNotEmpty ? media.fileName : media.file), onOpen: () => _openDoc(media), onDownload: () => downloadWithAuth(media.file, name.replaceAll(' ', '_'), context));
    }
    // ignore: dead_code
    return const SizedBox.shrink();
  }
  // TASK 2 (FIX_TASKS.md) — feed ka apna alag `_FullScreenViewer` hata diya
  // gaya; ab yahi `singlepost.dart` wale `FullScreenImagePage` /
  // `FullScreenVideoPage` open karta hai, taaki feed aur single-post dono
  // jagah tap-to-view ka experience same rahe. Same `Navigator.push` +
  // `MaterialPageRoute` (koi custom transition kisi bhi jagah nahi thi, to
  // dono jagah wahi default push transition hai). Video pause-on-open /
  // resume-current-page-on-return wala logic jaisa tha waisa hi rakha hai.
  void _openFullScreen(BuildContext context, int initialIndex) {
    _videoControllers.values.forEach((c) => c.pause());
    final m = widget.mediaList[initialIndex];
    final Widget page;
    if (PostMediaUtil.kind(m) == PostMediaKind.video) {
      page = FullScreenVideoPage(url: m.file);
    } else {
      // Swipeable, pinch/double-tap-zoomable gallery over all images of the post.
      final urls = <String>[];
      var start = 0;
      for (var i = 0; i < widget.mediaList.length; i++) {
        if (PostMediaUtil.kind(widget.mediaList[i]) != PostMediaKind.image) continue;
        if (i == initialIndex) start = urls.length;
        urls.add(widget.mediaList[i].file);
      }
      page = FullScreenImagePage(url: m.file, urls: urls, initialIndex: start);
    }
    Navigator.push(context, MaterialPageRoute(builder: (_) => page)).then((_) {
      final c = _videoControllers[_currentPage];
      if (c != null && c.value.isInitialized) { c.setLooping(true); c.play(); }
    });
  }
}