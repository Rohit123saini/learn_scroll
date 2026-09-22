import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:share_plus/share_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';

import '../api_service.dart';
import '../model.dart';
import '../../utils/api.dart';
import '../../services/auth_service.dart';
import '../../login/login_screen.dart';
import '../../wallet/screens/wallet_screen.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/profile_media_tiles.dart';
import '../../l10n/app_localizations.dart';
import '../../message/screens/manage_parent_access_screen.dart' show ManageParentAccessScreen; // Feature 8 entry
import 'edit_profile.dart';
import 'follow_list_screen.dart';
import '../../post/screens/singlepost.dart';
import '../../post/screens/post_list_screen.dart';

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
//      single `CustomScrollView` (header + grid as slivers), a lightweight
//      segmented toggle instead of `TabBarView` (avoids the
//      NestedScrollView complexity a real TabBarView-in-slivers needs).
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
  int _selectedTab = 0; // 0 = Photos/Videos, 1 = Documents
  String? errorMessage;

  final ScrollController _scrollController = ScrollController();

  List<PostModel> get _mediaPosts =>
      myPosts.where((p) => p.postType == 'image' || p.postType == 'video').toList();

  List<PostModel> get _documentPosts => myPosts
      .where((p) => ['document', 'pdf', 'excel', 'docx', 'xls', 'doc'].contains(p.postType))
      .toList();

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    _loadData();
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
      _loadMorePosts();
    }
  }

  Future<void> _loadData({bool forceRefresh = false}) async {
    await Future.wait([
      _loadProfile(forceRefresh: forceRefresh),
      _loadPostsFirstPage(),
    ]);
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
        });
      }
    } catch (e) {
      if (mounted) setState(() => isLoadingMorePosts = false);
      // Not surfaced as a SnackBar — scrolling near the bottom again
      // retries automatically, and a toast for every failed page during a
      // long scroll session would be noisier than helpful.
    }
  }

  Future<void> _refresh() => _loadData(forceRefresh: true);

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

  Future<void> _logout(AppLocalizations l10n) async {
    final cs = Theme.of(context).colorScheme;
    final confirm = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l10n.settingsLogout),
        content: Text(l10n.settingsLogoutConfirm),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(l10n.cancel),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(l10n.settingsLogout, style: TextStyle(color: cs.error)),
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

  void _shareProfile() {
    if (user == null) return;
    final l10n = AppLocalizations.of(context)!;
    Share.share(l10n.shareProfileMessage(user!.username, '${Api.baseUrl}/profile/${user!.username}'));
  }

  void _openMoreMenu(AppLocalizations l10n) {
    final cs = Theme.of(context).colorScheme;
    showModalBottomSheet(
      context: context,
      backgroundColor: cs.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(kLsRadius)),
      ),
      builder: (sheetCtx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const SizedBox(height: 10),
          Container(
            width: 36,
            height: 4,
            decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
          ),
          const SizedBox(height: 6),
          // Feature 8 — student generates/revokes codes for a parent or guardian
          // (ManageParentAccessScreen had no entry point anywhere in the app).
          ListTile(
            leading: Icon(Icons.bookmark_border_rounded, color: cs.onSurface),
            title: Text(l10n.savedPostsTitle),
            onTap: () {
              Navigator.pop(sheetCtx);
              Navigator.push(context, MaterialPageRoute(builder: (_) => PostListScreen.saved(context)));
            },
          ),
          ListTile(
            leading: Icon(Icons.family_restroom, color: cs.onSurface),
            title: Text(l10n.parentAccessTitle),
            onTap: () {
              Navigator.pop(sheetCtx);
              Navigator.push(context, MaterialPageRoute(builder: (_) => const ManageParentAccessScreen()));
            },
          ),
          ListTile(
            leading: Icon(Icons.logout_rounded, color: cs.error),
            title: Text(l10n.settingsLogout, style: TextStyle(color: cs.error, fontWeight: FontWeight.w700)),
            onTap: () {
              Navigator.pop(sheetCtx);
              _logout(l10n);
            },
          ),
          const SizedBox(height: 6),
        ]),
      ),
    );
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
            SliverToBoxAdapter(child: _buildSegmentedTabs(cs, l10n)),
            ..._buildGridSlivers(cs, l10n),
            const SliverToBoxAdapter(child: SizedBox(height: 40)),
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
        tooltip: l10n.moreOptions,
        onTap: () => _openMoreMenu(l10n),
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

  Widget _buildHeader(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 6, kLsPad, 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
          _avatar(cs),
          const SizedBox(width: 18),
          Expanded(
            child: Row(children: [
              _statColumn('${user!.posts}', l10n.postsStat, cs),
              _statColumn('${user!.followers}', l10n.followersStat, cs,
                  onTap: () => Navigator.push(context,
                      MaterialPageRoute(builder: (_) => FollowListScreen(username: user!.username, followers: true)))),
              _statColumn('${user!.following}', l10n.following, cs,
                  onTap: () => Navigator.push(context,
                      MaterialPageRoute(builder: (_) => FollowListScreen(username: user!.username, followers: false)))),
            ]),
          ),
        ]),
        const SizedBox(height: 12),
        Text(
          (user!.firstName.isEmpty && user!.lastName.isEmpty)
              ? l10n.noNameYet
              : '${user!.firstName} ${user!.lastName}'.trim(),
          style: LsType.head(context, size: 14.5),
        ),
        if (user!.isPrivate) ...[
          const SizedBox(height: 6),
          LsStatusChip(label: l10n.privateAccountBadge, color: cs.onSurfaceVariant, icon: Icons.lock_rounded),
        ],
        if (user!.bio.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(user!.bio, style: TextStyle(fontSize: 12.5, height: 1.45, color: cs.onSurface)),
        ],
        if (_staleData) ...[
          const SizedBox(height: 10),
          _staleBanner(cs, l10n),
        ],
        const SizedBox(height: 14),
        Row(children: [
          Expanded(
            child: LsOutlineButton(
              label: l10n.editProfileButton,
              icon: Icons.edit_outlined,
              onPressed: _goToEditProfile,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: LsOutlineButton(
              label: l10n.shareProfileButton,
              icon: Icons.ios_share_rounded,
              onPressed: _shareProfile,
            ),
          ),
        ]),
        const SizedBox(height: 10),
        _coinsRow(cs, l10n),
      ]),
    );
  }

  Widget _avatar(ColorScheme cs) {
    final photo = user!.profilePhoto;
    final url = photo.isEmpty ? '' : (photo.startsWith('http') ? photo : '${Api.baseUrl}$photo');
    return Container(
      width: 84,
      height: 84,
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
                width: 80,
                height: 80,
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

  Widget _statColumn(String value, String label, ColorScheme cs, {VoidCallback? onTap}) {
    return Expanded(
      child: InkWell(
        onTap: onTap,
        child: Column(children: [
          Text(value, style: LsType.head(context, size: 16)),
          const SizedBox(height: 2),
          Text(label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
        ]),
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

  Widget _coinsRow(ColorScheme cs, AppLocalizations l10n) {
    final label = l10n.coinsBalance(user!.coin);
    return Semantics(
      button: true,
      label: label,
      child: InkWell(
        borderRadius: BorderRadius.circular(kLsRadius),
        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const WalletScreen())),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
          decoration: BoxDecoration(
            color: cs.surface,
            borderRadius: BorderRadius.circular(kLsRadius),
            border: Border.all(color: cs.outlineVariant),
          ),
          child: Row(children: [
            Container(
              width: 30,
              height: 30,
              decoration: BoxDecoration(color: _lsAmber.withOpacity(.15), shape: BoxShape.circle),
              child: Icon(Icons.currency_rupee_rounded, size: 16, color: _lsAmber),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(label, style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurface)),
            ),
            Icon(Icons.chevron_right_rounded, size: 18, color: cs.onSurfaceVariant),
          ]),
        ),
      ),
    );
  }

  // ---------- segmented tabs (replaces the old fixed-height TabBarView) ----------

  Widget _buildSegmentedTabs(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 10),
      child: Row(children: [
        Expanded(
          child: _segmentButton(
            cs,
            icon: Icons.grid_on_rounded,
            label: l10n.mediaTabLabel(_mediaPosts.length),
            selected: _selectedTab == 0,
            onTap: () => setState(() => _selectedTab = 0),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _segmentButton(
            cs,
            icon: Icons.description_outlined,
            label: l10n.documentsTabLabel(_documentPosts.length),
            selected: _selectedTab == 1,
            onTap: () => setState(() => _selectedTab = 1),
          ),
        ),
      ]),
    );
  }

  Widget _segmentButton(
    ColorScheme cs, {
    required IconData icon,
    required String label,
    required bool selected,
    required VoidCallback onTap,
  }) {
    return Semantics(
      button: true,
      selected: selected,
      label: label,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 10),
          decoration: BoxDecoration(
            color: selected ? cs.primary.withOpacity(.12) : Colors.transparent,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: selected ? cs.primary : cs.outlineVariant),
          ),
          child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(icon, size: 15, color: selected ? cs.primary : cs.onSurfaceVariant),
            const SizedBox(width: 6),
            Flexible(
              child: Text(label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                      fontSize: 11.5,
                      fontWeight: FontWeight.w700,
                      color: selected ? cs.primary : cs.onSurfaceVariant)),
            ),
          ]),
        ),
      ),
    );
  }

  // ---------- grid ----------

  List<Widget> _buildGridSlivers(ColorScheme cs, AppLocalizations l10n) {
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

    final list = _selectedTab == 0 ? _mediaPosts : _documentPosts;

    if (list.isEmpty) {
      return [
        SliverToBoxAdapter(
          child: EmptyStateWidget(
            icon: _selectedTab == 0 ? Icons.photo_library_outlined : Icons.description_outlined,
            title: _selectedTab == 0 ? l10n.noMediaYetTitle : l10n.noDocumentsYetTitle,
            subtitle: _selectedTab == 0 ? l10n.noMediaYetSubtitle : l10n.noDocumentsYetSubtitle,
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
            childAspectRatio: _selectedTab == 0 ? 1.0 : 0.72,
          ),
          delegate: SliverChildBuilderDelegate(
            (context, index) {
              final post = list[index];
              if (_selectedTab == 0) {
                return MediaGridTile(post: post, l10n: l10n, onTap: () => _openSinglePost(post.id.toString()));
              }
              final file = post.media.isNotEmpty ? post.media.first : null;
              if (file == null) return const SizedBox.shrink();
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

  List<Widget> _gridSkeletonSlivers() {
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
            (context, i) => const LsShimmer(child: LsSkeletonBox(height: double.infinity, radius: 10)),
            childCount: 9,
          ),
        ),
      ),
    ];
  }
}

