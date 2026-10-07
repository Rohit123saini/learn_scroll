import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';

import '../api_service.dart';
import '../model.dart';
import '../../utils/api.dart';
import '../../wallet/screens/wallet_screen.dart';
import '../../referrals/screens/referrals_screen.dart'; // Task G12 — app-wide Invite & Earn
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/profile_media_tiles.dart';
import '../../widgets/share_profile_sheet.dart'; // P9-FE — QR + card + deep link
import '../../widgets/profile_bio_block.dart'; // P7-FE — category · pronouns, linkified bio, link chips
import '../../widgets/badges_ui.dart'; // P13-FE — badges row, "All badges" sheet, new-badge popup
import '../../l10n/app_localizations.dart';
import 'settings_screen.dart'; // 🔥 NAYA [Settings/Nav pass]
import 'edit_profile.dart';
import 'follow_list_screen.dart';
import 'weekly_recap_screen.dart'; // Task 9 — surfaced directly on profile now.
import '../../post/screens/singlepost.dart';
import '../../post/widgets/highlights_row.dart'; // P1-FE — Highlights row
import '../../post/models/highlight_model.dart';
import '../../post/widgets/highlight_launcher.dart'; // P2-FE
import '../../post/screens/reels_screen.dart'; // P13 — video tile -> open in Reels
import '../../post/widgets/pin_overlay.dart'; // P3-FE — pinned posts
import '../../post/services/post_tag_service.dart'; // P5b-FE — Tagged tab
import '../../services/streak_service.dart'; // Task 8 — streak moved here from home.dart.

// ============================================================
// OWN PROFILE — rebuilt on top of the same `ls_ui.dart` / skeletons /
// error-states kit `home.dart` uses, instead of the old hardcoded
// `Color(0xFF030F27)` + plain-white-Scaffold prototype look. Goal: this
// screen should feel like it belongs to the same app as Home, not like a
// different app bolted on.
//
// Real behavioural fixes made along the way (not just a reskin):
//   1. DOUBLE BOTTOM BAR — this screen used to build its own `Scaffold`
//      with its own `bottomNavigationBar` (a big red Logout button), while
//      `home.dart` embeds `ProfileScreen` as a tab inside an `IndexedStack`
//      that ALREADY has its own `bottomNavigationBar` (`_LsBottomNav`).
//      Two bottom bars were stacking on top of each other on the Profile
//      tab. Logout now lives in a "more" bottom sheet instead.
//   2. PAGINATION — `_loadMyPosts()` only ever fetched page 1 and never
//      advanced; any account with more posts than one backend page was
//      silently missing the rest. `ApiService.getMyPostsPage` (added
//      alongside this file) now surfaces DRF's real `next` field, and this
//      screen does real infinite-scroll off the one CustomScrollView
//      (no nested scrollables — same "Task 7.4" rule `home.dart` follows),
//      with de-duping against a race between a pull-refresh and an
//      in-flight "load more" (same class of bug Task 10.4 flags for the
//      main feed, fixed here first since this screen touched it).
//   3. UNAUTHENTICATED DOWNLOADS — `_downloadDocument` used to call
//      `canLaunchUrl`/`launchUrl` or a bare `Dio().download` with no auth
//      header at all (profile_app.md §11 item 3), while the exact same
//      tile on someone else's profile (`target_profile.dart`) already used
//      the authenticated `ApiService.downloadFile`. Your own documents on
//      your own profile could silently fail or fetch the wrong content if
//      the backend ever requires auth for media. Fixed to match, and it
//      now actually opens the file afterward (`OpenFilex`) instead of just
//      naming a path in a SnackBar nobody can act on.
//   4. SILENT STALE DATA — a failed background profile refresh used to
//      just `print()`; a stale cached profile (e.g. after a username
//      change from another device) could sit displayed indefinitely with
//      zero signal. Now surfaces a small dismissible-by-refresh banner.
//   5. FIXED-HEIGHT TABS — the old `SizedBox(height: 600, child:
//      TabBarView(...))` clipped content on short screens/many rows and
//      couldn't scroll as one gesture with the header. Replaced with a
//      single `CustomScrollView` (header + grid as slivers) and state-driven
//      tabs instead of `TabBarView`.
//      P4-FE: the segmented toggle became an Instagram-style icon tab bar
//      (Grid / Reels / Saved / Tagged) in a *pinned* `SliverPersistentHeader`
//      inside that same CustomScrollView — so it still sticks under the app
//      bar while the grid scrolls, with ONE scrollable (no NestedScrollView,
//      no inner scrollviews => nothing that can clip). Documents / Reposts
//      live as filter chips under the Grid tab.
//   6. ACCESSIBILITY — every icon-only control (back, more-menu, document
//      download) now has `Semantics`/`Tooltip`, matching `home.dart`'s
//      `_lsIconButton` pattern exactly.
//
// ⚠️ i18n — checked against the real `app_hi.arb` (478 keys) this time,
// not assumed. Five of what looked like new keys already exist under
// different names — reused those instead of adding near-duplicates:
//   settingsLogout          → used for both the Logout dialog title and button
//   settingsLogoutConfirm   → used for the Logout confirmation message
//   following                → reused for the "Following" stat label
//   download                 → reused for the document-tile download tooltip
//   cancel / retry / downloadedFile(fileName) / downloadFailed(error) / open
//                             → already existed, used as originally assumed
// Everything below is genuinely new — I've already added Hindi entries for
// all of it directly to app_hi.arb (see the diff/patch delivered alongside
// this file). Still need the matching app_en.arb entries (and any other
// locale files) — I don't have your app_en.arb to safely merge into, so
// pulling the English text from the same patch is the last step:
//   back                                    "Back"
//   moreOptions                             "More options"
//   verifiedAccount                         "Verified account"
//   postsStat                               "Posts"
//   followersStat                           "Followers"
//   noNameYet                               "No name yet"
//   privateAccountBadge                     "Private"
//   showingSavedProfileData                 "Showing saved data — pull down to refresh"
//   editProfileButton                       "Edit Profile"
//   shareProfileButton                      "Share Profile"
//   coinsBalance(coin)                      "{coin} coins"
//   mediaTabLabel(count)                    "{count} Photos/Videos"
//   documentsTabLabel(count)                "{count} Documents"
//   postsLoadErrorTitle                     "Couldn't load your posts"
//   postsLoadErrorSubtitle                  "Check your connection and try again."
//   noMediaYetTitle                         "No photos or videos yet"
//   noMediaYetSubtitle                      "Posts you share will show up here."
//   noDocumentsYetTitle                     "No documents yet"
//   noDocumentsYetSubtitle                  "Documents you share will show up here."
//   allCaughtUp                             "You're all caught up"
//   profileLoadErrorTitle                   "Couldn't load your profile"
//   noProfileFound                          "No profile found"
//   downloading                             "Downloading…"
//   shareProfileMessage(username, link)     "Check out {username}'s profile 👇\n{link}"
//   pdfPagesCount(pages)                    "{pages} pages"
//   loadingEllipsis                         "Loading…"
//   fileSizeKb(kb)                          "{kb} KB"
//   videoPostLabel                          "Video post"
//   photoPostLabel                          "Photo post"
// ============================================================

// P10-FE — tile aspect ratios shared by the real grid AND its skeleton, so the
// placeholder is exactly the size of what replaces it (no layout jump when
// switching to the Reels / Documents views while posts are still loading).
const double _kReelAspect = 0.62;
const double _kDocAspect = 0.72;

class ProfileScreen extends StatefulWidget {
  /// Only used if this screen is ever pushed as its own route (it isn't,
  /// today — `home.dart` embeds it as a tab inside an `IndexedStack`,
  /// where `Navigator.canPop` correctly stays false and no back button
  /// renders at all, matching Home/Search's own tabs). Kept for that
  /// future case rather than removed outright.
  final VoidCallback? onBackToHome;

  const ProfileScreen({super.key, this.onBackToHome});

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  ProfileModel? user;
  List<PostModel> myPosts = [];
  bool isLoading = true;
  bool isPostsLoading = true;
  bool isLoadingMorePosts = false;
  bool hasMorePosts = true;
  bool postsLoadFailed = false;
  bool _staleData = false;
  int _postsPage = 1;
  int _highlightsReload = 0; // P1-FE — bump to refetch the Highlights row
  // P4-FE — icon tab bar: 0 = Grid, 1 = Reels, 2 = Saved, 3 = Tagged (P5b-FE).
  int _tab = 0;
  // P4-FE — chips inside the Grid tab: 0 = Photos/Videos, 1 = Documents, 2 = Reposts.
  int _gridFilter = 0;
  // Set when a "load more" fails so the auto-fill below doesn't hammer a dead network.
  bool _morePostsFailed = false;
  String? errorMessage;

  // ---------- Saved tab (Instagram-style, own profile only — never shown
  // on target_profile.dart or anyone else's profile: backend's
  // SavedPostsListAPIView is hardcoded to `request.user`, and
  // `test_saved_list_only_shows_current_users_saves` locks that in as
  // intentional privacy behaviour). ----------
  // Separate list + its own pagination because /post/saved/ is a distinct
  // backend endpoint (not a client-side filter of `myPosts` like the
  // Photos/Videos and Documents tabs above).
  List<PostModel> savedPosts = [];
  bool isSavedLoading = true;
  bool isLoadingMoreSaved = false;
  bool hasMoreSaved = true;
  bool savedLoadFailed = false;
  int _savedPage = 1;

  // ---------- Tagged tab (P5b-FE) — GET /post/tagged/<me>/ ----------
  // Lazy: nothing is fetched until the tab is first opened (most visits never open it).
  // Own profile only shows what I haven't hidden; "Hidden" chip adds `include_hidden=1`.
  List<TaggedPost> taggedPosts = [];
  bool isTaggedLoading = false;
  bool isLoadingMoreTagged = false;
  bool hasMoreTagged = true;
  bool taggedLoadFailed = false;
  bool _taggedLoadedOnce = false;
  bool _showHiddenTags = false;
  int _taggedPage = 1;
  int _taggedRequestId = 0; // drops responses that land after a refresh / filter toggle

  final ScrollController _scrollController = ScrollController();

  // Task 8 — streak moved here from `home.dart` in full: state, the
  // once-per-session check-in call, and the chip/details UI. `null`
  // while never loaded yet (section stays hidden — same "silent fail,
  // keep whatever was already on screen" contract `_loadStreak()` used
  // on Home). Set once by `_loadStreak()` in `initState`.
  //
  // Check-in timing: `StreakService.checkIn()` is idempotent server-side
  // (same-day no-op), so calling it once here — instead of on Home,
  // where every session was guaranteed to pass through — means a user
  // who opens the app and never taps into their own Profile tab that
  // session won't check in that day. Accepted trade-off for now since
  // Profile is a normal, frequently-visited tab (not a buried settings
  // page); revisit with a lightweight app-level check-in call (e.g. from
  // the root shell's initState) if that gap turns out to matter in
  // practice.
  StreakInfo? _streak;

  // P13-FE — my earned badges (newest first). Empty = row stays hidden.
  List<UserBadge> _badges = [];

  List<PostModel> get _mediaPosts =>
      myPosts.where((p) => p.postType == 'image' || p.postType == 'video').toList();

  // P4-FE — Reels tab = my video posts (subset of Grid's Photos/Videos).
  List<PostModel> get _reelPosts => myPosts.where((p) => p.postType == 'video').toList();

  List<PostModel> get _documentPosts => myPosts
      .where((p) => ['document', 'pdf', 'excel', 'docx', 'xls', 'doc'].contains(p.postType))
      .toList();

  // ---------- Reposts tab (public — same as target_profile.dart's, just
  // reading from `myPosts` instead of `targetPosts`; reposts already ride
  // along on the normal posts pagination, no separate endpoint). ----------
  List<PostModel> get _repostPosts =>
      myPosts.where((p) => p.postType == 'repost').toList();

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    _loadData();
    _loadStreak(); // Task 8 — one check-in per app session (see field note above).
  }

  @override
  void dispose() {
    _scrollController.removeListener(_onScroll);
    _scrollController.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scrollController.hasClients) return;
    final pos = _scrollController.position;
    if (pos.pixels >= pos.maxScrollExtent - 400) {
      if (_tab == 2) {
        _loadMoreSaved();
      } else if (_tab == 3) {
        _loadMoreTagged();
      } else {
        _loadMorePosts();
      }
    }
  }

  // P4-FE — Reels / Documents / Reposts are client-side filters of `myPosts`, so a
  // sparse filter (e.g. 2 videos among 20 posts) can leave the list too short to
  // scroll — and with nothing to scroll, `_onScroll` never fires and the next
  // page never loads. After each frame, pull another page while the content
  // doesn't reach ~400px past the viewport. Stops at the last page, while a
  // load is running, and after a failed load (scrolling retries it).
  void _fillIfShort() {
    if (!mounted || !_scrollController.hasClients) return;
    if (_tab == 3) {
      // P5b-FE — a short Tagged grid can't scroll, so top it up the same way.
      if (!_taggedLoadedOnce || !hasMoreTagged || isLoadingMoreTagged || isTaggedLoading || taggedLoadFailed) return;
      final pos = _scrollController.position;
      if (pos.hasContentDimensions && pos.extentAfter < 400) _loadMoreTagged();
      return;
    }
    if (_tab >= 2 || _morePostsFailed) return;
    if (!hasMorePosts || isLoadingMorePosts || isPostsLoading) return;
    final pos = _scrollController.position;
    if (!pos.hasContentDimensions) return;
    if (pos.extentAfter < 400) _loadMorePosts();
  }

  void _setTab(int t) {
    if (t == _tab) return;
    setState(() {
      _tab = t;
      _morePostsFailed = false;
    });
    if (t == 3 && !_taggedLoadedOnce && !isTaggedLoading) _loadTaggedFirstPage(); // P5b-FE
  }

  Future<void> _loadData({bool forceRefresh = false}) async {
    await Future.wait([
      _loadProfile(forceRefresh: forceRefresh),
      _loadPostsFirstPage(),
      _loadSavedFirstPage(),
      if (_taggedLoadedOnce) _loadTaggedFirstPage(), // P5b-FE — keep an already-opened tab fresh on pull-refresh
    ]);
    _loadBadges(); // P13-FE — after the profile (needs the username); not awaited, purely additive
  }

  // P13-FE — badges row + new-badge popup. Silent-fail like the other
  // secondary loaders. The popup only ever runs here (own profile), never
  // on target_profile.dart.
  Future<void> _loadBadges() async {
    final username = user?.username;
    if (username == null || username.isEmpty) return;
    final list = await BadgeService.fetch(username);
    if (!mounted) return;
    setState(() => _badges = list);
    if (list.isNotEmpty) BadgeCelebration.checkAndShow(context, list);
  }

  // ---------- Tagged tab loaders (P5b-FE) ----------
  Future<void> _loadTaggedFirstPage() async {
    final username = user?.username;
    if (username == null || username.isEmpty) return;
    final requestId = ++_taggedRequestId;
    final isFirstLoad = taggedPosts.isEmpty;
    if (isFirstLoad) {
      setState(() {
        isTaggedLoading = true;
        taggedLoadFailed = false;
      });
    }
    try {
      final page = await PostTagService.getTaggedPosts(username, page: 1, includeHidden: _showHiddenTags);
      if (!mounted || requestId != _taggedRequestId) return;
      setState(() {
        taggedPosts = page.items;
        hasMoreTagged = page.hasMore;
        _taggedPage = 1;
        _taggedLoadedOnce = true;
        isTaggedLoading = false;
        taggedLoadFailed = false;
      });
    } catch (_) {
      if (!mounted || requestId != _taggedRequestId) return;
      setState(() {
        isTaggedLoading = false;
        if (isFirstLoad) taggedLoadFailed = true; // refresh failure keeps what's on screen
      });
    }
  }

  Future<void> _loadMoreTagged() async {
    final username = user?.username;
    if (username == null || isLoadingMoreTagged || !hasMoreTagged || isTaggedLoading || !_taggedLoadedOnce) return;
    final requestId = _taggedRequestId;
    setState(() => isLoadingMoreTagged = true);
    final nextPage = _taggedPage + 1;
    try {
      final page = await PostTagService.getTaggedPosts(username, page: nextPage, includeHidden: _showHiddenTags);
      if (!mounted) return;
      if (requestId != _taggedRequestId) {
        setState(() => isLoadingMoreTagged = false); // a refresh/toggle replaced the list meanwhile
        return;
      }
      setState(() {
        final existing = taggedPosts.map((t) => t.post.id).toSet();
        taggedPosts = [...taggedPosts, ...page.items.where((t) => !existing.contains(t.post.id))];
        hasMoreTagged = page.hasMore;
        _taggedPage = nextPage;
        isLoadingMoreTagged = false;
      });
    } catch (_) {
      if (mounted) setState(() => isLoadingMoreTagged = false);
    }
  }

  void _toggleShowHiddenTags() {
    setState(() {
      _showHiddenTags = !_showHiddenTags;
      taggedPosts = [];
      hasMoreTagged = true;
      taggedLoadFailed = false;
    });
    _loadTaggedFirstPage();
  }

  // Long-press a tagged tile -> hide / show on my profile, or remove my tag entirely.
  // "Hide" keeps the tag visible ON the post; "Remove" deletes it (backend: PostTag row).
  Future<void> _showTagMenu(TaggedPost t) async {
    final action = await showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: Icon(t.isHidden ? Icons.visibility_outlined : Icons.visibility_off_outlined),
              title: Text(t.isHidden ? 'Show on my profile' : 'Hide from my profile'),
              onTap: () => Navigator.pop(ctx, 'toggle'),
            ),
            ListTile(
              leading: Icon(Icons.person_remove_outlined, color: Theme.of(ctx).colorScheme.error),
              title: Text('Remove tag', style: TextStyle(color: Theme.of(ctx).colorScheme.error)),
              onTap: () => Navigator.pop(ctx, 'remove'),
            ),
          ],
        ),
      ),
    );
    if (!mounted || action == null) return;
    if (action == 'toggle') {
      await _setTagHidden(t, !t.isHidden);
    } else if (action == 'remove') {
      await _confirmRemoveTag(t);
    }
  }

  Future<void> _setTagHidden(TaggedPost t, bool hidden) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await PostTagService.setHidden(t.post.id, hidden);
      if (!mounted) return;
      setState(() {
        if (hidden && !_showHiddenTags) {
          taggedPosts = taggedPosts.where((x) => x.post.id != t.post.id).toList();
        } else {
          taggedPosts = [for (final x in taggedPosts) x.post.id == t.post.id ? x.copyWith(isHidden: hidden) : x];
        }
      });
      messenger.showSnackBar(SnackBar(
        content: Text(hidden ? 'Hidden from your profile' : 'Showing on your profile'),
        action: SnackBarAction(
          label: 'Undo',
          onPressed: () async {
            try {
              await PostTagService.setHidden(t.post.id, !hidden);
              if (mounted) _loadTaggedFirstPage();
            } catch (_) {}
          },
        ),
      ));
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.toString())));
    }
  }

  Future<void> _confirmRemoveTag(TaggedPost t) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Remove tag?'),
        content: const Text("You'll be untagged from this post and it will disappear from your Tagged tab."),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text('Remove', style: TextStyle(color: Theme.of(ctx).colorScheme.error)),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await PostTagService.removeMyTag(t.post.id);
      if (!mounted) return;
      setState(() => taggedPosts = taggedPosts.where((x) => x.post.id != t.post.id).toList());
      messenger.showSnackBar(const SnackBar(content: Text('Tag removed')));
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.toString())));
    }
  }

  Future<void> _loadSavedFirstPage() async {
    final isFirstLoad = savedPosts.isEmpty;
    if (isFirstLoad) {
      setState(() {
        isSavedLoading = true;
        savedLoadFailed = false;
      });
    }
    try {
      final page = await ApiService.getSavedPosts(page: 1);
      if (mounted) {
        setState(() {
          savedPosts = page.posts;
          hasMoreSaved = page.hasMore;
          _savedPage = 1;
          isSavedLoading = false;
          savedLoadFailed = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          isSavedLoading = false;
          if (isFirstLoad) savedLoadFailed = true;
        });
      }
    }
  }

  Future<void> _loadMoreSaved() async {
    if (isLoadingMoreSaved || !hasMoreSaved || isSavedLoading) return;
    setState(() => isLoadingMoreSaved = true);
    final nextPage = _savedPage + 1;
    try {
      final page = await ApiService.getSavedPosts(page: nextPage);
      if (mounted) {
        setState(() {
          final existingIds = savedPosts.map((p) => p.id).toSet();
          savedPosts = [...savedPosts, ...page.posts.where((p) => !existingIds.contains(p.id))];
          hasMoreSaved = page.hasMore;
          _savedPage = nextPage;
          isLoadingMoreSaved = false;
        });
      }
    } catch (e) {
      if (mounted) setState(() => isLoadingMoreSaved = false);
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
          : await ApiService.getProfile(
              onBackgroundError: (_) {
                if (mounted) setState(() => _staleData = true);
              },
            );
      if (mounted) {
        setState(() {
          user = data;
          isLoading = false;
          if (forceRefresh) _staleData = false;
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

  Future<void> _loadPostsFirstPage() async {
    final isFirstLoad = myPosts.isEmpty;
    if (isFirstLoad) {
      setState(() {
        isPostsLoading = true;
        postsLoadFailed = false;
      });
    }
    try {
      final page = await ApiService.getMyPostsPage(page: 1);
      if (mounted) {
        setState(() {
          myPosts = page.posts;
          hasMorePosts = page.hasMore;
          _postsPage = 1;
          isPostsLoading = false;
          postsLoadFailed = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          isPostsLoading = false;
          // Refresh failed but we already had posts on screen — leave the
          // grid exactly as-is rather than wiping it (same "stale but
          // visible beats blank" call as the profile-header handling).
          if (isFirstLoad) postsLoadFailed = true;
        });
      }
    }
  }

  Future<void> _loadMorePosts() async {
    if (isLoadingMorePosts || !hasMorePosts || isPostsLoading) return;
    setState(() => isLoadingMorePosts = true);
    final nextPage = _postsPage + 1;
    try {
      final page = await ApiService.getMyPostsPage(page: nextPage);
      if (mounted) {
        setState(() {
          // De-dupe in case a pull-refresh reset page 1 while this
          // "load more" call for an old page N was still in flight.
          final existingIds = myPosts.map((p) => p.id).toSet();
          myPosts = [...myPosts, ...page.posts.where((p) => !existingIds.contains(p.id))];
          hasMorePosts = page.hasMore;
          _postsPage = nextPage;
          isLoadingMorePosts = false;
          _morePostsFailed = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          isLoadingMorePosts = false;
          _morePostsFailed = true;
        });
      }
      // Not surfaced as a SnackBar — scrolling near the bottom again
      // retries automatically, and a toast for every failed page during a
      // long scroll session would be noisier than helpful.
    }
  }

  // Task 8 — moved verbatim (behaviour-wise) from `home.dart`'s old
  // `_loadStreak()`. Same silent-fail contract as the rest of this
  // screen's loaders: a failed/offline call just leaves `_streak` null
  // (section stays hidden) rather than showing an error.
  Future<void> _loadStreak() async {
    final result = await StreakService.checkIn();
    if (result == null || !mounted) return;
    setState(() => _streak = result.streak);

    // P13-FE — a 7/30-day milestone has just awarded a badge server-side, but
    // the badges fetch in `_loadData` may have finished before this check-in
    // did. Re-fetch so the popup shows now, not on the next refresh.
    if (result.milestoneReached != null) _loadBadges();

    if (result.milestoneReached != null && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            result.bonusCoins > 0
                ? '🔥 ${result.milestoneReached}-day streak! +${result.bonusCoins} coins credited.'
                : '🔥 ${result.milestoneReached}-day streak!',
          ),
          duration: const Duration(seconds: 4),
        ),
      );
    }
  }

  void _showStreakDetailsSheet(StreakInfo streak) {
    final cs = Theme.of(context).colorScheme;
    showModalBottomSheet(
      context: context,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) {
        return SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Text('🔥', style: TextStyle(fontSize: 22)),
                    const SizedBox(width: 8),
                    Text(
                      '${streak.currentStreak}-day streak',
                      style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: cs.onSurface),
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  streak.isActiveToday
                      ? "You're checked in for today — come back tomorrow to keep it going."
                      : "Open LearnScroll again today to keep your streak alive.",
                  style: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
                ),
                const SizedBox(height: 16),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    _streakStat(cs, 'Best streak', '${streak.longestStreak} days'),
                    _streakStat(cs, 'Total active days', '${streak.totalActiveDays}'),
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _streakStat(ColorScheme cs, String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(value, style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: cs.onSurface)),
        const SizedBox(height: 2),
        Text(label, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
      ],
    );
  }

  Future<void> _refresh() {
    if (mounted) setState(() => _highlightsReload++); // P1-FE
    return _loadData(forceRefresh: true);
  }

  // P2-FE — highlight viewer / create / edit. Each returns true when the row
  // should reload (created, edited, deleted or found gone).
  Future<void> _openHighlight(Highlight h) async {
    if (await openHighlightViewer(context, h) && mounted) setState(() => _highlightsReload++);
  }

  Future<void> _createHighlight() async {
    if (await createHighlightFlow(context) && mounted) setState(() => _highlightsReload++);
  }

  Future<void> _editHighlight(Highlight h) async {
    if (await editHighlightFlow(context, h) && mounted) setState(() => _highlightsReload++);
  }

  Future<void> _goToEditProfile() async {
    if (user == null) return;
    final result = await Navigator.push(
      context,
      MaterialPageRoute(builder: (context) => EditProfileScreen(user: user!)),
    );
    if (result == true) {
      await _loadData(forceRefresh: true);
    }
  }

  Future<void> _downloadDocument(String url, String fileName, AppLocalizations l10n) async {
    lsSnack(context, l10n.downloading);
    try {
      // ✅ Authenticated path (was: canLaunchUrl/launchUrl or a bare
      // Dio().download with no Bearer token at all — see fix #3 above).
      await ApiService.downloadFile(url, fileName);
      final dir = await getApplicationDocumentsDirectory();
      final filePath = '${dir.path}/$fileName';
      if (mounted) lsSnack(context, l10n.downloadedFile(fileName));
      await OpenFilex.open(filePath);
    } catch (e) {
      if (mounted) lsSnack(context, l10n.downloadFailed(e.toString()), error: true);
    }
  }

  void _openSinglePost(String postId) {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (context) => SinglePostPage(postId: postId)),
    );
  }

  // P13 — a video tile opens in Reels (starts on that video, `?start=`); everything else stays a single post.
  void _openPost(PostModel post) {
    if (post.postType == 'video' && post.media.isNotEmpty) {
      ReelsScreen.open(context, startPostId: post.id.toString());
    } else {
      _openSinglePost(post.id.toString());
    }
  }

  // P3-FE — long-press a grid tile -> Pin to profile / Unpin. Not optimistic on
  // purpose: the 3-pin limit is enforced by the server, and a flip-then-rollback
  // on the 4th pin would flicker the grid. The call is quick; on success the
  // flag is flipped on the *current* list item (looked up by id, since a
  // pull-refresh may have replaced the list while the call was in flight) and
  // the grid is re-sorted pinned-first.
  Future<void> _showPinMenu(PostModel post) {
    return showPinMenuSheet(
      context,
      isPinned: post.isPinned,
      onToggle: () => _setPinned(post, !post.isPinned),
    );
  }

  Future<void> _setPinned(PostModel post, bool pin) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final r = await ApiService.setPostPinned(post.id, pin);
      if (!mounted) return;
      setState(() {
        for (final p in myPosts) {
          if (p.id == post.id) p.isPinned = r.isPinned;
        }
        myPosts = sortPinnedFirst(myPosts);
      });
      messenger.showSnackBar(SnackBar(
        content: Text(r.isPinned ? 'Pinned to profile (${r.pinnedCount}/${r.maxPinned})' : 'Unpinned from profile'),
      ));
    } on PinException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      messenger.showSnackBar(SnackBar(content: Text("Couldn't ${pin ? 'pin' : 'unpin'} post. Check your connection and try again.")));
    }
  }

  // P9-FE — was a bare Share.share(<web url>). Now a sheet: QR + card image + copy/share link
  // (deep link learnscroll://u/<username>, handled by DeepLinkService in main.dart).
  void _shareProfile() {
    final u = user;
    if (u == null) return;
    final photo = u.profilePhoto;
    showShareProfileSheet(
      context,
      username: u.username,
      fullName: '${u.firstName} ${u.lastName}'.trim(),
      photoUrl: photo.isEmpty ? '' : (photo.startsWith('http') ? photo : '${Api.baseUrl}$photo'),
    );
  }

  // 🔧 TASK 10 FIX — the (⋯) button used to pop open a 4-item bottom
  // sheet (Settings / Saved Posts / Parent Access / Logout) before you
  // could get anywhere. That intermediate menu is gone: the icon now
  // opens Settings directly. The other three options didn't disappear —
  // they moved *inside* Settings itself as proper sub-items (Saved Posts
  // and Parent Access sit in its new "General" section, Logout was
  // already there at the bottom of that screen).
  void _openSettings() {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => SettingsScreen(user: user!)),
    ).then((_) => _loadData(forceRefresh: true));
  }

  Color _hueShift(Color base, double degrees) {
    final hsl = HSLColor.fromColor(base);
    return hsl.withHue((hsl.hue + degrees) % 360).toColor();
  }

  // Same derivation `home.dart` uses for its wallet quick-action color —
  // deliberately reused so "Coins" here and "Wallet" on Home read as the
  // same feature, not two different shades of orange.
  Color get _lsAmber => _hueShift(Theme.of(context).colorScheme.secondary, 27);

  // ============================================================
  // BUILD
  // ============================================================

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    if (isLoading && user == null) {
      return Scaffold(
        backgroundColor: lsBg(context),
        body: SafeArea(
          child: CustomScrollView(
            physics: const NeverScrollableScrollPhysics(),
            slivers: [
              SliverToBoxAdapter(child: _headerSkeleton(cs)),
              ..._gridSkeletonSlivers(),
            ],
          ),
        ),
      );
    }

    if (errorMessage != null && user == null) {
      return Scaffold(
        backgroundColor: lsBg(context),
        body: SafeArea(
          child: Center(
            child: ErrorStateWidget(
              title: l10n.profileLoadErrorTitle,
              subtitle: errorMessage,
              retryLabel: l10n.retry,
              onRetry: () => _loadData(),
            ),
          ),
        ),
      );
    }

    if (user == null) {
      return Scaffold(
        backgroundColor: lsBg(context),
        body: SafeArea(child: Center(child: Text(l10n.noProfileFound))),
      );
    }

    final showBack = Navigator.canPop(context);
    WidgetsBinding.instance.addPostFrameCallback((_) => _fillIfShort()); // P4-FE

    return Scaffold(
      backgroundColor: lsBg(context),
      body: RefreshIndicator(
        color: cs.primary,
        backgroundColor: cs.surface,
        onRefresh: _refresh,
        child: CustomScrollView(
          controller: _scrollController,
          physics: const AlwaysScrollableScrollPhysics(),
          slivers: [
            SliverAppBar(
              automaticallyImplyLeading: false,
              backgroundColor: lsBg(context),
              surfaceTintColor: Colors.transparent,
              elevation: 0,
              scrolledUnderElevation: 0,
              floating: true,
              snap: true,
              toolbarHeight: 52,
              titleSpacing: kLsPad,
              title: _buildTopBar(cs, l10n, showBack),
            ),
            SliverToBoxAdapter(child: _buildHeader(cs, l10n)),
            // P1-FE — Highlights: own profile => "New +" circle always shown.
            SliverToBoxAdapter(
              child: HighlightsRow(
                isOwner: true,
                reloadToken: _highlightsReload,
                onOpen: _openHighlight,
                onCreate: _createHighlight,
                onEdit: _editHighlight,
              ),
            ),
            // P4-FE — Instagram-style icon tabs, pinned under the app bar.
            SliverPersistentHeader(
              pinned: true,
              delegate: _ProfileTabsDelegate(
                selected: _tab,
                savedLabel: l10n.savedTabLabel(savedPosts.length),
                onSelect: _setTab,
              ),
            ),
            if (_tab == 0) SliverToBoxAdapter(child: _buildGridFilterChips(cs, l10n)),
            ..._buildGridSlivers(cs, l10n),
            // 4.1 — home.dart uses `extendBody: true`, so the bottom nav floats OVER
            // this tab; MediaQuery's bottom padding already includes its height.
            // A fixed 40px spacer left the last grid row hidden behind it.
            SliverToBoxAdapter(child: SizedBox(height: 24 + MediaQuery.paddingOf(context).bottom)),
          ],
        ),
      ),
    );
  }

  // ---------- top bar ----------

  Widget _buildTopBar(ColorScheme cs, AppLocalizations l10n, bool showBack) {
    return Row(children: [
      if (showBack) ...[
        _circleIconButton(
          cs: cs,
          icon: Icons.arrow_back_rounded,
          tooltip: l10n.back,
          onTap: () {
            if (widget.onBackToHome != null) {
              widget.onBackToHome!();
            } else {
              Navigator.maybePop(context);
            }
          },
        ),
        const SizedBox(width: 8),
      ],
      Expanded(
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Flexible(
            child: Text(
              user!.username,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: LsType.head(context, size: 16),
            ),
          ),
          if (user!.isVerified) ...[
            const SizedBox(width: 4),
            Icon(Icons.verified_rounded, size: 16, color: cs.primary, semanticLabel: l10n.verifiedAccount),
          ],
        ]),
      ),
      _circleIconButton(
        cs: cs,
        icon: Icons.more_horiz_rounded,
        tooltip: l10n.settingsTitle,
        onTap: _openSettings,
      ),
    ]);
  }

  Widget _circleIconButton({
    required ColorScheme cs,
    required IconData icon,
    required String tooltip,
    required VoidCallback onTap,
  }) {
    return Semantics(
      button: true,
      label: tooltip,
      child: Tooltip(
        message: tooltip,
        child: InkWell(
          onTap: onTap,
          customBorder: const CircleBorder(),
          child: Container(
            width: 34,
            height: 34,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: cs.surface,
              border: Border.all(color: cs.outlineVariant),
            ),
            child: Icon(icon, size: 16, color: cs.onSurface),
          ),
        ),
      ),
    );
  }

  // ---------- header ----------

  // 4.2 — Instagram-style header: avatar + stats, then name / bio, then the
  // two main actions side by side, then ONE slim row of small chips
  // (coins · streak · invite · weekly recap) instead of four full-width cards.
  Widget _buildHeader(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 6, kLsPad, 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
          _avatar(cs),
          const SizedBox(width: 20),
          Expanded(
            child: Row(children: [
              // Task 9 — Posts stat is tappable too (jumps to the Photos/Videos tab).
              _statColumn('${user!.posts}', l10n.postsStat, cs,
                  onTap: () => setState(() {
                        _tab = 0;
                        _gridFilter = 0;
                      })),
              _statColumn('${user!.followers}', l10n.followersStat, cs,
                  onTap: () => Navigator.push(context,
                      MaterialPageRoute(builder: (_) => FollowListScreen(username: user!.username, followers: true, isOwner: true)))),
              _statColumn('${user!.following}', l10n.following, cs,
                  onTap: () => Navigator.push(context,
                      MaterialPageRoute(builder: (_) => FollowListScreen(username: user!.username, followers: false)))),
            ]),
          ),
        ]),
        const SizedBox(height: 10),
        Text(
          (user!.firstName.isEmpty && user!.lastName.isEmpty)
              ? l10n.noNameYet
              : '${user!.firstName} ${user!.lastName}'.trim(),
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: LsType.head(context, size: 14.5),
        ),
        if (user!.isPrivate) ...[
          const SizedBox(height: 6),
          LsStatusChip(label: l10n.privateAccountBadge, color: cs.onSurfaceVariant, icon: Icons.lock_rounded),
        ],
        // P7-FE — category/pronouns + tappable @/#/URL + link chips.
        ProfileBioBlock(
          bio: user!.bio,
          pronouns: user!.pronouns,
          categoryLabel: user!.categoryLabel,
          links: user!.links,
        ),
        if (_staleData) ...[
          const SizedBox(height: 10),
          _staleBanner(cs, l10n),
        ],
        const SizedBox(height: 12),
        Row(children: [
          Expanded(child: ProfileActionButton(label: l10n.editProfileButton, onPressed: _goToEditProfile)),
          const SizedBox(width: 8),
          Expanded(child: ProfileActionButton(label: l10n.shareProfileButton, onPressed: _shareProfile)),
        ]),
        const SizedBox(height: 10),
        _quickChips(cs, l10n),
        // P13-FE — top-3 badges + "All" sheet (hidden until I've earned one).
        if (_badges.isNotEmpty) ...[
          const SizedBox(height: 10),
          ProfileBadgesRow(badges: _badges, ownerName: user!.username, isOwner: true),
        ],
      ]),
    );
  }

  // 4.2 — coins / streak / invite / "Your Week" as small pills. Same targets
  // the old cards had: Wallet, streak details sheet, shared ReferralsScreen
  // (Task G12 — referrals are app-wide, not Tuition-Class-only) and the
  // weekly recap. Reuses existing l10n keys (coinsBalance, streakLabel,
  // inviteEarn, settingsYourWeek) — no new strings. The streak pill only
  // shows the number (+ 🔥); its full label is the tooltip / screen-reader text.
  Widget _quickChips(ColorScheme cs, AppLocalizations l10n) {
    final streak = _streak;
    return Wrap(spacing: 8, runSpacing: 8, children: [
      ProfileMiniChip(
        leading: Icon(Icons.currency_rupee_rounded, size: 14, color: _lsAmber),
        label: l10n.coinsBalance(user!.coin),
        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const WalletScreen())),
      ),
      if (streak != null && streak.currentStreak > 0)
        ProfileMiniChip(
          leading: const Text('🔥', style: TextStyle(fontSize: 13)),
          label: '${streak.currentStreak}',
          semanticLabel: '${streak.currentStreak} ${l10n.streakLabel}',
          onTap: () => _showStreakDetailsSheet(streak),
        ),
      ProfileMiniChip(
        leading: Icon(Icons.card_giftcard_rounded, size: 14, color: cs.primary),
        label: l10n.inviteEarn,
        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const ReferralsScreen())),
      ),
      ProfileMiniChip(
        leading: Icon(Icons.auto_graph_rounded, size: 14, color: cs.tertiary),
        label: l10n.settingsYourWeek,
        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const WeeklyRecapScreen())),
      ),
    ]);
  }

  Widget _avatar(ColorScheme cs) {
    final photo = user!.profilePhoto;
    final url = photo.isEmpty ? '' : (photo.startsWith('http') ? photo : '${Api.baseUrl}$photo');
    return Container(
      width: 86,
      height: 86,
      padding: const EdgeInsets.all(2),
      decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: cs.outlineVariant, width: 2)),
      child: ClipOval(
        child: photo.isEmpty
            ? Container(
                color: cs.surfaceVariant,
                child: Icon(Icons.person_rounded, size: 40, color: cs.onSurfaceVariant),
              )
            : CachedNetworkImage(
                imageUrl: url,
                width: double.infinity,
                height: double.infinity,
                fit: BoxFit.cover,
                placeholder: (c, u) => Container(color: cs.surfaceVariant),
                errorWidget: (c, u, e) => Container(
                  color: cs.surfaceVariant,
                  child: Icon(Icons.person_rounded, size: 40, color: cs.onSurfaceVariant),
                ),
              ),
      ),
    );
  }

  // Task 9 — small count-up animation instead of a static number, so the
  // stats row reads as a bit more "alive"/premium on every load.
  Widget _statColumn(String value, String label, ColorScheme cs, {VoidCallback? onTap}) {
    final target = int.tryParse(value);
    return Expanded(
      child: InkWell(
        borderRadius: BorderRadius.circular(kLsRadius),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Column(children: [
            target == null
                ? Text(value, style: LsType.head(context, size: 16))
                : TweenAnimationBuilder<int>(
                    tween: IntTween(begin: 0, end: target),
                    duration: const Duration(milliseconds: 700),
                    curve: Curves.easeOutCubic,
                    builder: (context, animatedValue, _) =>
                        Text('$animatedValue', style: LsType.head(context, size: 16)),
                  ),
            const SizedBox(height: 2),
            Text(label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w500, color: cs.onSurface)),
          ]),
        ),
      ),
    );
  }

  Widget _staleBanner(ColorScheme cs, AppLocalizations l10n) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(12)),
      child: Row(children: [
        Icon(Icons.info_outline_rounded, size: 15, color: cs.onSurfaceVariant),
        const SizedBox(width: 8),
        Expanded(
          child: Text(l10n.showingSavedProfileData, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
        ),
      ]),
    );
  }

  // ---------- Grid filter chips (P4-FE) ----------
  // Documents / Reposts used to be top-level segments; under the Instagram-style
  // icon bar they're a secondary row inside the Grid tab. Horizontally
  // scrollable so long (e.g. Hindi) labels can't overflow a narrow screen.

  Widget _buildGridFilterChips(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 6),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(children: [
          _filterChip(cs, index: 0, icon: Icons.grid_on_rounded, label: l10n.mediaTabLabel(_mediaPosts.length)),
          _filterChip(cs, index: 1, icon: Icons.description_outlined, label: l10n.documentsTabLabel(_documentPosts.length)),
          _filterChip(cs, index: 2, icon: Icons.repeat_rounded, label: l10n.repostsTabLabel(_repostPosts.length)),
        ]),
      ),
    );
  }

  Widget _filterChip(ColorScheme cs, {required int index, required IconData icon, required String label}) {
    final selected = _gridFilter == index;
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: Semantics(
        button: true,
        selected: selected,
        label: label,
        child: ChoiceChip(
          avatar: Icon(icon, size: 15, color: selected ? cs.primary : cs.onSurfaceVariant),
          label: Text(
            label,
            maxLines: 1,
            style: TextStyle(
              fontSize: 11.5,
              fontWeight: FontWeight.w700,
              color: selected ? cs.primary : cs.onSurfaceVariant,
            ),
          ),
          selected: selected,
          showCheckmark: false,
          selectedColor: cs.primary.withOpacity(.12),
          backgroundColor: Colors.transparent,
          side: BorderSide(color: selected ? cs.primary : cs.outlineVariant),
          onSelected: (_) => setState(() {
            _gridFilter = index;
            _morePostsFailed = false;
          }),
        ),
      ),
    );
  }

  // ---------- Tagged tab (P5b-FE) ----------
  // Same grid / skeleton / footer pattern as the Saved tab. Tagged posts can be any
  // post type, so each tile reuses `_savedTile`'s per-post look.
  List<Widget> _buildTaggedSlivers(ColorScheme cs, AppLocalizations l10n) {
    final filterRow = SliverToBoxAdapter(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 4),
        child: Align(
          alignment: Alignment.centerLeft,
          child: FilterChip(
            avatar: Icon(Icons.visibility_off_outlined, size: 16, color: cs.onSurfaceVariant),
            label: const Text('Hidden from profile'),
            selected: _showHiddenTags,
            onSelected: (_) => _toggleShowHiddenTags(),
            visualDensity: VisualDensity.compact,
          ),
        ),
      ),
    );

    if (isTaggedLoading || (!_taggedLoadedOnce && !taggedLoadFailed)) {
      return [filterRow, ..._gridSkeletonSlivers()];
    }

    if (taggedLoadFailed && taggedPosts.isEmpty) {
      return [
        filterRow,
        SliverToBoxAdapter(
          child: ErrorStateWidget(
            title: l10n.postsLoadErrorTitle,
            subtitle: l10n.postsLoadErrorSubtitle,
            retryLabel: l10n.retry,
            onRetry: _loadTaggedFirstPage,
          ),
        ),
      ];
    }

    if (taggedPosts.isEmpty) {
      return [
        filterRow,
        SliverToBoxAdapter(
          child: EmptyStateWidget(
            icon: Icons.person_pin_outlined,
            title: _showHiddenTags ? 'Nothing hidden' : 'No tagged posts yet',
            subtitle: _showHiddenTags
                ? 'Posts you hide from your profile will show up here.'
                : "When people tag you in their posts, they'll show up here.",
          ),
        ),
      ];
    }

    return [
      filterRow,
      const SliverToBoxAdapter(child: SizedBox(height: 6)),
      SliverPadding(
        padding: const EdgeInsets.symmetric(horizontal: kLsPad),
        sliver: SliverGrid(
          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 3,
            crossAxisSpacing: 6,
            mainAxisSpacing: 6,
            childAspectRatio: 1.0,
          ),
          delegate: SliverChildBuilderDelegate(
            (context, index) => _taggedTile(taggedPosts[index], cs, l10n),
            childCount: taggedPosts.length,
          ),
        ),
      ),
      if (isLoadingMoreTagged)
        const SliverToBoxAdapter(
          child: Padding(
            padding: EdgeInsets.symmetric(vertical: 18),
            child: Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2))),
          ),
        )
      else if (!hasMoreTagged && taggedPosts.length >= 6)
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 20),
            child: Center(
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(Icons.check_circle_outline_rounded, size: 14, color: cs.onSurfaceVariant),
                const SizedBox(width: 6),
                Text(l10n.allCaughtUp,
                    style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
              ]),
            ),
          ),
        ),
    ];
  }

  Widget _taggedTile(TaggedPost t, ColorScheme cs, AppLocalizations l10n) {
    return GestureDetector(
      key: ValueKey('tag-${t.post.id}'),
      onLongPress: () => _showTagMenu(t),
      child: Stack(
        fit: StackFit.expand,
        children: [
          Opacity(opacity: t.isHidden ? 0.5 : 1, child: _savedTile(t.post, cs, l10n)),
          if (t.isHidden)
            Positioned(
              top: 6,
              right: 6,
              child: Container(
                padding: const EdgeInsets.all(4),
                decoration: const BoxDecoration(color: Colors.black54, shape: BoxShape.circle),
                child: const Icon(Icons.visibility_off_rounded, size: 13, color: Colors.white),
              ),
            ),
        ],
      ),
    );
  }

  // ---------- grid ----------

  List<Widget> _buildGridSlivers(ColorScheme cs, AppLocalizations l10n) {
    if (_tab == 2) return _buildSavedGridSlivers(cs, l10n);
    if (_tab == 3) return _buildTaggedSlivers(cs, l10n);

    if (isPostsLoading) {
      return _gridSkeletonSlivers(
        aspectRatio: _tab == 1 ? _kReelAspect : (_gridFilter == 1 ? _kDocAspect : 1.0),
      );
    }

    if (postsLoadFailed && myPosts.isEmpty) {
      return [
        SliverToBoxAdapter(
          child: ErrorStateWidget(
            title: l10n.postsLoadErrorTitle,
            subtitle: l10n.postsLoadErrorSubtitle,
            retryLabel: l10n.retry,
            onRetry: _loadPostsFirstPage,
          ),
        ),
      ];
    }

    // Grid > Reposts chip keeps its own builder (and loading/error handling above).
    if (_tab == 0 && _gridFilter == 2) return _buildRepostGridSlivers(cs, l10n);

    final isReels = _tab == 1;
    final isDocs = !isReels && _gridFilter == 1;
    final list = isReels ? _reelPosts : (isDocs ? _documentPosts : _mediaPosts);

    if (list.isEmpty) {
      return [
        SliverToBoxAdapter(
          child: EmptyStateWidget(
            icon: isReels
                ? Icons.video_library_outlined
                : (isDocs ? Icons.description_outlined : Icons.photo_library_outlined),
            title: isReels ? 'No reels yet' : (isDocs ? l10n.noDocumentsYetTitle : l10n.noMediaYetTitle),
            subtitle: isReels
                ? 'Videos you share will show up here.'
                : (isDocs ? l10n.noDocumentsYetSubtitle : l10n.noMediaYetSubtitle),
          ),
        ),
      ];
    }

    return [
      SliverPadding(
        padding: const EdgeInsets.symmetric(horizontal: kLsPad),
        sliver: SliverGrid(
          gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 3,
            crossAxisSpacing: 6,
            mainAxisSpacing: 6,
            childAspectRatio: isDocs ? _kDocAspect : (isReels ? _kReelAspect : 1.0),
          ),
          delegate: SliverChildBuilderDelegate(
            (context, index) {
              final post = list[index];
              if (!isDocs) {
                return PinnedTileOverlay(
                  key: ValueKey('pin-${post.id}'),
                  pinned: post.isPinned,
                  onLongPress: () => _showPinMenu(post),
                  child: MediaGridTile(post: post, l10n: l10n, onTap: () => _openPost(post)),
                );
              }
              final file = post.media.isNotEmpty ? post.media.first : null;
              if (file == null) return const SizedBox.shrink();
              return PinnedTileOverlay(
                key: ValueKey('pin-${post.id}'),
                pinned: post.isPinned,
                onLongPress: () => _showPinMenu(post),
                child: InkWell(
                  borderRadius: BorderRadius.circular(10),
                  onTap: () => _openSinglePost(post.id.toString()),
                  child: DocumentGridTile(
                    doc: post,
                    file: file,
                    l10n: l10n,
                    onDownload: () => _downloadDocument(file.file, file.fileName, l10n),
                  ),
                ),
              );
            },
            childCount: list.length,
          ),
        ),
      ),
      if (isLoadingMorePosts)
        const SliverToBoxAdapter(
          child: Padding(
            padding: EdgeInsets.symmetric(vertical: 18),
            child: Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2))),
          ),
        )
      else if (!hasMorePosts && list.length >= 6)
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 20),
            child: Center(
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(Icons.check_circle_outline_rounded, size: 14, color: cs.onSurfaceVariant),
                const SizedBox(width: 6),
                Text(l10n.allCaughtUp,
                    style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
              ]),
            ),
          ),
        ),
    ];
  }

  // ---------- Saved tab grid ----------
  // Follows the exact same grid/skeleton/pagination-footer pattern as
  // Photos-Videos/Documents above, but saved posts can be ANY post_type
  // (image, video, document, text, poll, ...) since they're just
  // "whatever this user bookmarked" — so each tile picks its look per
  // post instead of the whole grid being one type.
  List<Widget> _buildSavedGridSlivers(ColorScheme cs, AppLocalizations l10n) {
    if (isSavedLoading) return _gridSkeletonSlivers();

    if (savedLoadFailed && savedPosts.isEmpty) {
      return [
        SliverToBoxAdapter(
          child: ErrorStateWidget(
            title: l10n.postsLoadErrorTitle,
            subtitle: l10n.postsLoadErrorSubtitle,
            retryLabel: l10n.retry,
            onRetry: _loadSavedFirstPage,
          ),
        ),
      ];
    }

    if (savedPosts.isEmpty) {
      return [
        SliverToBoxAdapter(
          child: EmptyStateWidget(
            icon: Icons.bookmark_border_rounded,
            title: l10n.noSavedPostsYetTitle,
            subtitle: l10n.noSavedPostsYetSubtitle,
          ),
        ),
      ];
    }

    return [
      SliverPadding(
        padding: const EdgeInsets.symmetric(horizontal: kLsPad),
        sliver: SliverGrid(
          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 3,
            crossAxisSpacing: 6,
            mainAxisSpacing: 6,
            childAspectRatio: 1.0,
          ),
          delegate: SliverChildBuilderDelegate(
            (context, index) => _savedTile(savedPosts[index], cs, l10n),
            childCount: savedPosts.length,
          ),
        ),
      ),
      if (isLoadingMoreSaved)
        const SliverToBoxAdapter(
          child: Padding(
            padding: EdgeInsets.symmetric(vertical: 18),
            child: Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2))),
          ),
        )
      else if (!hasMoreSaved && savedPosts.length >= 6)
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 20),
            child: Center(
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(Icons.check_circle_outline_rounded, size: 14, color: cs.onSurfaceVariant),
                const SizedBox(width: 6),
                Text(l10n.allCaughtUp,
                    style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
              ]),
            ),
          ),
        ),
    ];
  }

  Widget _savedTile(PostModel post, ColorScheme cs, AppLocalizations l10n) {
    const docTypes = ['document', 'pdf', 'excel', 'docx', 'xls', 'doc'];
    if (docTypes.contains(post.postType)) {
      final file = post.media.isNotEmpty ? post.media.first : null;
      if (file != null) {
        return InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: () => _openSinglePost(post.id.toString()),
          child: DocumentGridTile(
            doc: post,
            file: file,
            l10n: l10n,
            onDownload: () => _downloadDocument(file.file, file.fileName, l10n),
          ),
        );
      }
    }
    if (post.media.isNotEmpty && (post.postType == 'image' || post.postType == 'video')) {
      return MediaGridTile(post: post, l10n: l10n, onTap: () => _openPost(post));
    }
    // Text/poll/article/link post with no displayable media — same
    // text-preview fallback tile PostListScreen already uses for this case.
    final caption = (post.title?.isNotEmpty == true) ? post.title! : post.content;
    return InkWell(
      borderRadius: BorderRadius.circular(10),
      onTap: () => _openSinglePost(post.id.toString()),
      child: Container(
        decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(10)),
        padding: const EdgeInsets.all(8),
        alignment: Alignment.center,
        child: Text(
          caption.trim(),
          maxLines: 6,
          overflow: TextOverflow.ellipsis,
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
        ),
      ),
    );
  }

  // ---------- Reposts tab grid (public — visible on target_profile.dart
  // too, same `_repostPosts`-from-`postType=='repost'` pattern). Reposts
  // ride along on `myPosts`'s normal pagination (no separate endpoint),
  // and each tile renders the embedded *original* post's preview, not the
  // repost row itself. ----------
  List<Widget> _buildRepostGridSlivers(ColorScheme cs, AppLocalizations l10n) {
    if (isPostsLoading) return _gridSkeletonSlivers();

    if (postsLoadFailed && myPosts.isEmpty) {
      return [
        SliverToBoxAdapter(
          child: ErrorStateWidget(
            title: l10n.postsLoadErrorTitle,
            subtitle: l10n.postsLoadErrorSubtitle,
            retryLabel: l10n.retry,
            onRetry: _loadPostsFirstPage,
          ),
        ),
      ];
    }

    final list = _repostPosts;

    if (list.isEmpty) {
      return [
        SliverToBoxAdapter(
          child: EmptyStateWidget(
            icon: Icons.repeat_rounded,
            title: l10n.noRepostsYetTitle,
            subtitle: l10n.noRepostsYetSubtitle,
          ),
        ),
      ];
    }

    return [
      SliverPadding(
        padding: const EdgeInsets.symmetric(horizontal: kLsPad),
        sliver: SliverGrid(
          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 3,
            crossAxisSpacing: 6,
            mainAxisSpacing: 6,
            childAspectRatio: 1.0,
          ),
          delegate: SliverChildBuilderDelegate(
            (context, index) => _repostTile(list[index], cs, l10n),
            childCount: list.length,
          ),
        ),
      ),
      if (isLoadingMorePosts)
        const SliverToBoxAdapter(
          child: Padding(
            padding: EdgeInsets.symmetric(vertical: 18),
            child: Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2))),
          ),
        )
      else if (!hasMorePosts && list.length >= 6)
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 20),
            child: Center(
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(Icons.check_circle_outline_rounded, size: 14, color: cs.onSurfaceVariant),
                const SizedBox(width: 6),
                Text(l10n.allCaughtUp,
                    style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
              ]),
            ),
          ),
        ),
    ];
  }

  Widget _repostTile(PostModel post, ColorScheme cs, AppLocalizations l10n) {
    final original = post.originalPost;
    Widget tile;
    if (original == null) {
      // Hard-deleted or unavailable original — never leaks hidden content,
      // see PostModel.fromJson's `is_unavailable` stub handling.
      tile = InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: () => _openSinglePost(post.id.toString()),
        child: Container(
          decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(10)),
          alignment: Alignment.center,
          child: Icon(Icons.repeat_rounded, color: cs.onSurfaceVariant, size: 22),
        ),
      );
    } else if (original.media.isNotEmpty && (original.postType == 'image' || original.postType == 'video')) {
      tile = MediaGridTile(post: original, l10n: l10n, onTap: () => _openSinglePost(post.id.toString()));
    } else {
      final caption = (original.title?.isNotEmpty == true) ? original.title! : original.content;
      tile = InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: () => _openSinglePost(post.id.toString()),
        child: Container(
          decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(10)),
          padding: const EdgeInsets.all(8),
          alignment: Alignment.center,
          child: Text(
            caption.trim(),
            maxLines: 6,
            overflow: TextOverflow.ellipsis,
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
          ),
        ),
      );
    }

    return PinnedTileOverlay(
      key: ValueKey('pin-${post.id}'),
      pinned: post.isPinned,
      alignLeft: true, // top-right is taken by the repost badge
      onLongPress: () => _showPinMenu(post),
      child: Stack(
      fit: StackFit.expand,
      children: [
        tile,
        Positioned(
          top: 4,
          right: 4,
          child: IgnorePointer(
            child: Container(
              padding: const EdgeInsets.all(4),
              decoration: const BoxDecoration(color: Colors.black54, shape: BoxShape.circle),
              child: const Icon(Icons.repeat_rounded, size: 12, color: Colors.white),
            ),
          ),
        ),
      ],
    ),
    );
  }

  // ---------- skeletons (mirror the real layout's dimensions — see
  // widgets/skeletons.dart's own "no layout jump" rule) ----------

  Widget _headerSkeleton(ColorScheme cs) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 58, kLsPad, 4),
      child: LsShimmer(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const LsSkeletonBox(width: 84, height: 84, radius: 42),
            const SizedBox(width: 18),
            Expanded(
              child: Row(children: [
                for (var i = 0; i < 3; i++)
                  Expanded(
                    child: Column(children: const [
                      LsSkeletonBox(width: 28, height: 16, radius: 5),
                      SizedBox(height: 6),
                      LsSkeletonBox(width: 44, height: 9, radius: 4),
                    ]),
                  ),
              ]),
            ),
          ]),
          const SizedBox(height: 14),
          const LsSkeletonBox(width: 140, height: 12, radius: 5),
          const SizedBox(height: 16),
          const Row(children: [
            Expanded(child: LsSkeletonBox(height: 40, radius: 24)),
            SizedBox(width: 10),
            Expanded(child: LsSkeletonBox(height: 40, radius: 24)),
          ]),
        ]),
      ),
    );
  }

  List<Widget> _gridSkeletonSlivers({double aspectRatio = 1.0}) {
    return [
      SliverPadding(
        padding: const EdgeInsets.symmetric(horizontal: kLsPad),
        sliver: SliverGrid(
          gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 3,
            crossAxisSpacing: 6,
            mainAxisSpacing: 6,
            childAspectRatio: aspectRatio, // P10-FE — match the tab being loaded
          ),
          delegate: SliverChildBuilderDelegate(
            (context, i) => const LsShimmer(child: LsSkeletonBox(height: double.infinity, radius: 10)),
            childCount: 9,
          ),
        ),
      ),
    ];
  }
}

/// P4-FE — the pinned Instagram-style icon tab bar (Grid / Reels / Saved /
/// Tagged). Fixed 48px, so `minExtent == maxExtent` and it never collapses.
class _ProfileTabsDelegate extends SliverPersistentHeaderDelegate {
  final int selected;
  final String savedLabel;
  final ValueChanged<int> onSelect;

  const _ProfileTabsDelegate({
    required this.selected,
    required this.savedLabel,
    required this.onSelect,
  });

  static const double _height = 48;

  @override
  double get minExtent => _height;

  @override
  double get maxExtent => _height;

  @override
  bool shouldRebuild(covariant _ProfileTabsDelegate old) =>
      old.selected != selected || old.savedLabel != savedLabel;

  @override
  Widget build(BuildContext context, double shrinkOffset, bool overlapsContent) {
    final cs = Theme.of(context).colorScheme;
    final tabs = <List<Object>>[
      [Icons.grid_on_rounded, 'Grid'],
      [Icons.video_library_outlined, 'Reels'],
      [Icons.bookmark_border_rounded, savedLabel],
      [Icons.person_pin_outlined, 'Tagged'],
    ];
    return Material(
      color: lsBg(context),
      child: Container(
        height: _height,
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: cs.outlineVariant, width: .5)),
        ),
        child: Row(
          children: [
            for (int i = 0; i < tabs.length; i++)
              Expanded(
                child: Semantics(
                  button: true,
                  selected: i == selected,
                  label: tabs[i][1] as String,
                  child: InkResponse(
                    onTap: () => onSelect(i),
                    child: Container(
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        border: Border(
                          bottom: BorderSide(
                            color: i == selected ? cs.primary : Colors.transparent,
                            width: 2,
                          ),
                        ),
                      ),
                      child: Icon(
                        tabs[i][0] as IconData,
                        size: 24,
                        color: i == selected ? cs.primary : cs.onSurfaceVariant,
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}