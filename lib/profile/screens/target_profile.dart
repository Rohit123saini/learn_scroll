import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:share_plus/share_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';

import '../api_service.dart';
import '../model.dart';
import '../widgets/block_report.dart'; // block dialog / report sheet
import '../../utils/api.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/profile_media_tiles.dart';
import '../../widgets/profile_bio_block.dart'; // P7-FE
import '../../widgets/badges_ui.dart'; // P13-FE — badges row + "All badges" sheet
import '../discovery_models.dart'; // P8-FE
import '../../l10n/app_localizations.dart';
import '../../post/screens/singlepost.dart';
import '../../post/screens/reels_screen.dart'; // P13 — video tile -> open in Reels
import '../../post/widgets/pin_overlay.dart'; // P3-FE — pinned posts (read-only badge here)
import '../../message/services/message_api_service.dart';
import '../../message/screens/chat_screen.dart';
import 'follow_list_screen.dart';
import '../../post/widgets/highlights_row.dart'; // P1-FE — Highlights row
import '../../post/models/highlight_model.dart';
import '../../post/widgets/highlight_launcher.dart'; // P2-FE

// ============================================================
// TARGET (someone else's) PROFILE — rebuilt on the same ls_ui.dart /
// skeletons / error-states system as profile.dart and home.dart, instead
// of its own separate hardcoded-navy styling. This file previously had
// ~1000 dead commented-out lines (an old backup kept in place) sitting
// above the real code — removed; the real code starts where this comment
// does now.
//
// Real behavioural fixes made along the way, mirroring the same fixes
// profile.dart got:
//   1. PAGINATION — `getTargetUserPosts` was never called past page 1
//      here either; anyone whose posts you're viewing with more than one
//      backend page had the rest silently missing. Fixed the same way,
//      with `ApiService.getTargetUserPostsPage` surfacing DRF's `next`.
//   2. RAW BACKEND ERRORS — follow/accept/reject all threw the raw JSON
//      response body on failure (`Error: Exception: Follow failed: 400 -
//      {...}`); `api_service.dart` now extracts a clean message for all
//      three, shown as `somethingWentWrong` here rather than the JSON.
//   3. UNAUTHENTICATED-LOOKING DOWNLOAD UX — download already used the
//      authenticated path here (unlike the old profile.dart), but never
//      opened the file afterward and showed no clean progress/failure
//      copy. Aligned with the same download→open flow profile.dart got.
//   4. DUPLICATED WIDGETS — `VideoFirstFrame`/`DocumentGridTile` were
//      copy-pasted here verbatim (and already drifting — this copy was
//      still on hardcoded grey/red/green with no accessibility labels
//      while profile.dart's had moved on). Both screens now import the
//      same `widgets/profile_media_tiles.dart`.
//   5. FAKE "MORE OPTIONS" BUTTON — the app bar had a ⋮ icon that only
//      ever showed a "More Options" SnackBar with nothing behind it — a
//      button that does nothing is worse than no button. Removed rather
//      than faked; if block/report should live here, that's a real
//      feature to scope separately (there's no backend endpoint for it
//      in this api_service.dart yet).
//   6. FIXED-HEIGHT TABS — same `SizedBox(height: 600) + TabBarView` issue
//      as the old profile.dart; replaced with the same CustomScrollView +
//      segmented-toggle approach.
//
// ⚠️ i18n — almost entirely reused from the keys the profile.dart /
// edit_profile.dart passes already added to app_hi.arb (back, moreOptions
// [unused now], verifiedAccount, postsStat, followersStat, following,
// noNameYet, privateAccountBadge, shareProfileButton, shareProfileMessage,
// mediaTabLabel, documentsTabLabel, postsLoadErrorTitle/Subtitle,
// noMediaYetTitle/Subtitle, noDocumentsYetTitle/Subtitle, allCaughtUp,
// profileLoadErrorTitle, noProfileFound, downloading, downloadedFile,
// downloadFailed, download, loadingEllipsis, pdfPagesCount, fileSizeKb,
// videoPostLabel, photoPostLabel, retry, confirm, delete,
// somethingWentWrong). Genuinely new, added to app_hi.arb by this pass /
// still need app_en.arb:
//   follow                          "Follow"
//   followBack                      "Follow Back"
//   requestedLabel                  "Requested"
//   messageButton                   "Message"
//   privateAccountMessage           "This account is private. Follow to see their posts."
//   privateAccountPendingMessage    "Follow request sent. Wait for approval to see posts."
//   chatOpenFailed(error)           "Couldn't open chat: {error}"
// ============================================================

class TargetProfilePage extends StatefulWidget {
  final String username;
  const TargetProfilePage({super.key, required this.username});

  @override
  State<TargetProfilePage> createState() => _TargetProfilePageState();
}

class _TargetProfilePageState extends State<TargetProfilePage> {
  TargetProfileModel? targetUser;
  List<PostModel> targetPosts = [];
  bool isLoading = true;
  bool isPostsLoading = true;
  bool isLoadingMorePosts = false;
  bool hasMorePosts = true;
  bool postsLoadFailed = false;
  bool isActionLoading = false;
  bool _blockBusy = false; // block / unblock call in flight
  bool _notFound = false; // 404: no such user, or they blocked me
  int _postsPage = 1;
  int _selectedTab = 0;
  int _highlightsReload = 0; // P1-FE
  String? errorMessage;

  // ---------- P8-FE ----------
  // "Followed by X, Y + N others" — loaded after the profile, failures are silent.
  MutualFollowers _mutuals = MutualFollowers.empty;
  // P13-FE — their earned badges; loaded after the profile, failures are silent.
  List<UserBadge> _badges = [];
  // "Suggested for you" carousel: revealed by a FRESH follow tap, hidden again on unfollow,
  // and once the user hits X it stays gone for the rest of this visit.
  List<MiniUser> _suggested = [];
  bool _suggestionsOpen = false;
  bool _suggestionsLoading = false;
  bool _suggestionsDismissed = false;
  final Map<int, String> _suggFollow = {}; // userId -> 'ACCEPTED' | 'PENDING' (absent = not following)
  final Set<int> _suggBusy = {};

  final ScrollController _scrollController = ScrollController();

  bool get _isPrivateGated =>
      targetUser != null &&
      targetUser!.isPrivate &&
      targetUser!.myFollowStatus != 'ACCEPTED' &&
      targetUser!.myId != targetUser!.targetUserId;

  bool get _isBlockedByMe => targetUser?.isBlockedByMe ?? false;

  // Posts / highlights / badges are not shown for a private account I can't
  // see into, nor for an account I blocked.
  bool get _contentHidden => _isPrivateGated || _isBlockedByMe;

  List<PostModel> get _mediaPosts =>
      targetPosts.where((p) => p.postType == 'image' || p.postType == 'video').toList();

  List<PostModel> get _documentPosts => targetPosts
      .where((p) => ['document', 'pdf', 'excel', 'docx', 'xls', 'doc'].contains(p.postType))
      .toList();

  // 🔥 NAYA — "Reposts" tab (Task 4 replacement: Saved tab ki jagah, kyunki
  // saved posts backend me hamesha `request.user`-only hote hain — koi bhi
  // dusre user ke saves nahi dekh sakta, `SavedPostsListAPIView` aur
  // `test_saved_list_only_shows_current_users_saves` dono isi ko enforce
  // karte hain, isliye target profile pe "Saved" dikhana galat/privacy-
  // breaking hota). Repost `post_type == 'repost'` waala ek normal Post row
  // hi hai (targetPosts me already aata hai, koi extra API call nahi
  // chahiye), isliye ye local filter hi kaafi hai.
  List<PostModel> get _repostPosts =>
      targetPosts.where((p) => p.postType == 'repost').toList();

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    _loadProfile();
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

  Future<void> _loadProfile() async {
    _highlightsReload++; // P1-FE — refetch highlights with the profile (follow state may have changed)
    setState(() {
      isLoading = true;
      errorMessage = null;
      _notFound = false;
    });
    try {
      final data = await ApiService.getTargetProfile(widget.username);
      if (!mounted) return;
      setState(() {
        targetUser = data;
        isLoading = false;
        if (data.isBlockedByMe) {
          // Everything about them disappears the moment I block.
          targetPosts = [];
          hasMorePosts = false;
          _mutuals = MutualFollowers.empty;
          _badges = [];
          _suggested = [];
          _suggestionsOpen = false;
        }
      });
      if (!data.isBlockedByMe) {
        _loadMutuals(data); // P8-FE — fire and forget
      }
      _loadBadges(data.username); // P13-FE — fire and forget
      if (_contentHidden) {
        setState(() => isPostsLoading = false);
      } else {
        hasMorePosts = true;
        await _loadPostsFirstPage();
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          if (e is ProfileNotFoundException) {
            _notFound = true;
            targetUser = null;
          } else {
            errorMessage = e.toString();
          }
          isLoading = false;
        });
      }
    }
  }

  // P13-FE — skipped for a private account I can't see into (same gate as
  // the posts grid); a failed call just leaves the row hidden.
  Future<void> _loadBadges(String username) async {
    if (_contentHidden) {
      if (_badges.isNotEmpty && mounted) setState(() => _badges = []);
      return;
    }
    final list = await BadgeService.fetch(username);
    if (!mounted) return;
    setState(() => _badges = list);
  }

  Future<void> _loadPostsFirstPage() async {
    if (targetUser == null) return;
    final isFirstLoad = targetPosts.isEmpty;
    if (isFirstLoad) {
      setState(() {
        isPostsLoading = true;
        postsLoadFailed = false;
      });
    }
    try {
      final page = await ApiService.getTargetUserPostsPage(targetUser!.targetUserId, page: 1);
      if (mounted) {
        setState(() {
          targetPosts = page.posts;
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
          if (isFirstLoad) postsLoadFailed = true;
        });
      }
    }
  }

  Future<void> _loadMorePosts() async {
    if (targetUser == null || isLoadingMorePosts || !hasMorePosts || isPostsLoading || _contentHidden) return;
    setState(() => isLoadingMorePosts = true);
    final nextPage = _postsPage + 1;
    try {
      final page = await ApiService.getTargetUserPostsPage(targetUser!.targetUserId, page: nextPage);
      if (mounted) {
        setState(() {
          final existingIds = targetPosts.map((p) => p.id).toSet();
          targetPosts = [...targetPosts, ...page.posts.where((p) => !existingIds.contains(p.id))];
          hasMorePosts = page.hasMore;
          _postsPage = nextPage;
          isLoadingMorePosts = false;
        });
      }
    } catch (e) {
      if (mounted) setState(() => isLoadingMorePosts = false);
    }
  }

  // TASK G16 — optimistic follow/unfollow. This used to block the whole
  // button behind `isActionLoading` and then re-fetch the *entire* profile
  // (`_loadProfile()`, header + posts + everything) just to reflect a
  // follow-status flip — by far the heaviest "wait for the network before
  // the UI moves" spot found in this pass, since every other screen only
  // ever waits on the one thing it changed. Now the button flips instantly
  // (using `isPrivate` to guess PENDING vs ACCEPTED for a fresh follow,
  // same guess feed's follow button makes) and only the true follow/unfollow
  // path avoids a full profile reload — accept/reject still go through
  // `_loadProfile()` below since those affect the incoming-request banner,
  // not something this screen tracks separately.
  Future<void> handleFollow(AppLocalizations l10n) async {
    final current = targetUser;
    if (current == null || isActionLoading) return;
    HapticFeedback.selectionClick();

    final wasFollowingBack = current.myFollowStatus == 'ACCEPTED';
    final isFreshFollow = current.myFollowStatus == null;
    final optimistic = isFreshFollow
        ? current.copyWith(
            myFollowStatus: current.isPrivate ? 'PENDING' : 'ACCEPTED',
            followers: current.isPrivate ? current.followers : current.followers + 1,
          )
        : current.copyWith(
            clearMyFollowStatus: true,
            clearMyFollowId: true,
            followers: wasFollowingBack ? (current.followers > 0 ? current.followers - 1 : 0) : current.followers,
          );
    setState(() => targetUser = optimistic);

    try {
      final result = await ApiService.followUser(current.targetUserId);
      if (!mounted) return;
      final status = result['status']?.toString(); // null | 'PENDING' | 'ACCEPTED'
      // Recompute the follower count from the ORIGINAL (pre-tap) number
      // rather than nudging the optimistic guess — simpler to get right,
      // and self-corrects the private-account PENDING/ACCEPTED guess above
      // without a full profile reload.
      final actualDelta = isFreshFollow
          ? (status == 'ACCEPTED' ? 1 : 0)
          : (wasFollowingBack ? -1 : 0);
      final newFollowers = current.followers + actualDelta;
      setState(() {
        targetUser = current.copyWith(
          myFollowStatus: status,
          clearMyFollowStatus: status == null,
          followers: newFollowers < 0 ? 0 : newFollowers,
        );
      });
      if (result['message'] != null) lsSnack(context, result['message'].toString());
      // P8-FE — a fresh follow opens "Suggested for you"; unfollowing closes it again.
      if (isFreshFollow) {
        _openSuggestions();
      } else if (_suggestionsOpen) {
        setState(() => _suggestionsOpen = false);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => targetUser = current); // rollback to the exact pre-tap snapshot
      lsSnack(context, l10n.somethingWentWrong, error: true);
    }
  }

  Future<void> handleAcceptRequest(AppLocalizations l10n) async {
    if (targetUser?.theirFollowId == null || isActionLoading) return;
    setState(() => isActionLoading = true);
    try {
      final result = await ApiService.acceptFollowRequest(targetUser!.theirFollowId!);
      await _loadProfile();
      if (mounted && result['message'] != null) lsSnack(context, result['message'].toString());
    } catch (e) {
      if (mounted) lsSnack(context, l10n.somethingWentWrong, error: true);
    } finally {
      if (mounted) setState(() => isActionLoading = false);
    }
  }

  Future<void> handleRejectRequest(AppLocalizations l10n) async {
    if (targetUser?.theirFollowId == null || isActionLoading) return;
    setState(() => isActionLoading = true);
    try {
      final result = await ApiService.rejectFollowRequest(targetUser!.theirFollowId!);
      await _loadProfile();
      if (mounted && result['message'] != null) lsSnack(context, result['message'].toString());
    } catch (e) {
      if (mounted) lsSnack(context, l10n.somethingWentWrong, error: true);
    } finally {
      if (mounted) setState(() => isActionLoading = false);
    }
  }

  // P2-FE — read-only highlight viewer (visibility already enforced by the backend).
  Future<void> _openHighlight(Highlight h) async {
    final changed = await openHighlightViewer(context, h, myUserId: '${targetUser?.myId}');
    if (changed && mounted) setState(() => _highlightsReload++);
  }

  Future<void> _openChatWithUser(AppLocalizations l10n) async {
    if (targetUser == null || isActionLoading) return;
    setState(() => isActionLoading = true);
    try {
      final conversation = await MessageApiService.getOrCreateConversation(targetUser!.targetUserId.toString());
      if (mounted) {
        Navigator.push(context, MaterialPageRoute(builder: (_) => ChatScreen(conversation: conversation)));
      }
    } catch (e) {
      if (mounted) lsSnack(context, l10n.chatOpenFailed(e.toString()), error: true);
    } finally {
      if (mounted) setState(() => isActionLoading = false);
    }
  }

  Future<void> _downloadDocument(String url, String fileName, AppLocalizations l10n) async {
    lsSnack(context, l10n.downloading);
    try {
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
    Navigator.push(context, MaterialPageRoute(builder: (context) => SinglePostPage(postId: postId)));
  }

  // P13 — a video tile opens in Reels (starts on that video, `?start=`); everything else stays a single post.
  void _openPost(PostModel post) {
    if (post.postType == 'video' && post.media.isNotEmpty) {
      ReelsScreen.open(context, startPostId: post.id.toString());
    } else {
      _openSinglePost(post.id.toString());
    }
  }

  void _shareProfile() {
    if (targetUser == null) return;
    final l10n = AppLocalizations.of(context)!;
    Share.share(l10n.shareProfileMessage(targetUser!.username, '${Api.baseUrl}/profile/${targetUser!.username}'));
  }

  // ============================================================
  // BUILD
  // ============================================================

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    if (isLoading && targetUser == null) {
      return Scaffold(
        backgroundColor: lsBg(context),
        body: SafeArea(
          child: CustomScrollView(
            physics: const NeverScrollableScrollPhysics(),
            slivers: [
              SliverToBoxAdapter(child: _headerSkeleton()),
              ..._gridSkeletonSlivers(),
            ],
          ),
        ),
      );
    }

    if (_notFound && targetUser == null) {
      return Scaffold(
        backgroundColor: lsBg(context),
        appBar: AppBar(
          backgroundColor: lsBg(context),
          surfaceTintColor: Colors.transparent,
          elevation: 0,
          leading: BackButton(onPressed: () => Navigator.maybePop(context)),
        ),
        body: SafeArea(child: Center(child: Text(l10n.noProfileFound))),
      );
    }

    if (errorMessage != null && targetUser == null) {
      return Scaffold(
        backgroundColor: lsBg(context),
        body: SafeArea(
          child: Center(
            child: ErrorStateWidget(
              title: l10n.profileLoadErrorTitle,
              subtitle: errorMessage,
              retryLabel: l10n.retry,
              onRetry: _loadProfile,
            ),
          ),
        ),
      );
    }

    if (targetUser == null) {
      return Scaffold(
        backgroundColor: lsBg(context),
        body: SafeArea(child: Center(child: Text(l10n.noProfileFound))),
      );
    }

    return Scaffold(
      backgroundColor: lsBg(context),
      body: RefreshIndicator(
        color: cs.primary,
        backgroundColor: cs.surface,
        onRefresh: _loadProfile,
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
              title: _buildTopBar(cs, l10n),
            ),
            SliverToBoxAdapter(child: _buildHeader(cs, l10n)),
            // P8-FE — slides in under the header after a fresh follow
            SliverToBoxAdapter(child: _buildSuggestedCarousel(cs, l10n)),
            if (_isBlockedByMe)
              SliverToBoxAdapter(child: _blockedNotice(cs, l10n))
            else if (_isPrivateGated)
              SliverToBoxAdapter(child: _privateGateNotice(cs, l10n))
            else ...[
              // P1-FE — no "New +" here; empty (or hidden by backend
              // visibility rules) => the row renders nothing.
              SliverToBoxAdapter(
                child: HighlightsRow(
                  userId: targetUser!.targetUserId,
                  isOwner: false,
                  reloadToken: _highlightsReload,
                  onOpen: _openHighlight,
                ),
              ),
              SliverToBoxAdapter(child: _buildSegmentedTabs(cs, l10n)),
              ..._buildGridSlivers(cs, l10n),
            ],
            // 4.1 — keep the last row clear of the system gesture bar / any bottom overlay.
            SliverToBoxAdapter(child: SizedBox(height: 24 + MediaQuery.paddingOf(context).bottom)),
          ],
        ),
      ),
    );
  }

  // ============================================================
  // P8-FE — mutual line + "Suggested for you" carousel
  // ============================================================
  String _absPhoto(String photo) =>
      photo.isEmpty ? '' : (photo.startsWith('http') ? photo : '${Api.baseUrl}$photo');

  Future<void> _loadMutuals(TargetProfileModel t) async {
    if (t.myId == t.targetUserId) return; // no "mutuals" with yourself
    try {
      final m = await ApiService.getMutualFollowers(t.username);
      if (mounted) setState(() => _mutuals = m);
    } catch (_) {
      // decorative line — never surface an error for it
    }
  }

  Widget _miniAvatar(MiniUser u, double radius, ColorScheme cs) {
    final url = _absPhoto(u.profilePhoto);
    return CircleAvatar(
      radius: radius,
      backgroundColor: cs.surfaceVariant,
      backgroundImage: url.isEmpty ? null : CachedNetworkImageProvider(url),
      child: url.isEmpty
          ? Text(u.username.isEmpty ? '?' : u.username[0].toUpperCase(),
              style: TextStyle(fontSize: radius * 0.85, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant))
          : null,
    );
  }

  // "Followed by a, b + 5 others" with up to 3 overlapping avatars.
  Widget _mutualLine(ColorScheme cs) {
    final people = _mutuals.preview;
    final total = _mutuals.total < people.length ? people.length : _mutuals.total;
    final names = people.take(2).map((u) => u.username).toList();
    final String label;
    if (total <= 1 || names.length == 1) {
      label = 'Followed by ${names.first}';
    } else if (total == 2) {
      label = 'Followed by ${names[0]} and ${names[1]}';
    } else {
      final others = total - 2;
      label = 'Followed by ${names[0]}, ${names[1]} + $others other${others == 1 ? '' : 's'}';
    }

    const r = 11.0;
    const overlap = 15.0;
    final shown = people.take(3).toList();
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: () => Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => FollowListScreen(username: targetUser!.username, followers: true)),
        ),
        child: Row(children: [
          SizedBox(
            width: r * 2 + (shown.length - 1) * overlap + 4,
            height: r * 2 + 4,
            child: Stack(children: [
              for (int i = 0; i < shown.length; i++)
                Positioned(
                  left: i * overlap,
                  child: Container(
                    padding: const EdgeInsets.all(2),
                    decoration: BoxDecoration(color: cs.surface, shape: BoxShape.circle),
                    child: _miniAvatar(shown[i], r, cs),
                  ),
                ),
            ]),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(label,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12, height: 1.3, color: cs.onSurfaceVariant)),
          ),
        ]),
      ),
    );
  }

  void _openSuggestions() {
    if (_suggestionsDismissed || !mounted) return;
    setState(() => _suggestionsOpen = true);
    if (_suggested.isEmpty && !_suggestionsLoading) _loadSuggestions();
  }

  Future<void> _loadSuggestions() async {
    final t = targetUser;
    if (t == null) return;
    setState(() => _suggestionsLoading = true);
    try {
      final list = await ApiService.getSimilarUsers(t.username);
      if (!mounted) return;
      setState(() {
        _suggested = list;
        _suggestionsLoading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _suggestionsLoading = false); // nothing to show => section hides itself
    }
  }

  void _dismissSuggestions() {
    setState(() {
      _suggestionsOpen = false;
      _suggestionsDismissed = true;
    });
  }

  Future<void> _toggleFollowSuggestion(MiniUser u, AppLocalizations l10n) async {
    if (_suggBusy.contains(u.id)) return;
    HapticFeedback.selectionClick();
    final before = _suggFollow[u.id];
    setState(() {
      _suggBusy.add(u.id);
      if (before == null) {
        _suggFollow[u.id] = 'ACCEPTED'; // optimistic; corrected from the response below
      } else {
        _suggFollow.remove(u.id);
      }
    });
    try {
      final result = await ApiService.followUser(u.id);
      if (!mounted) return;
      final st = result['status']?.toString(); // null | 'PENDING' | 'ACCEPTED'
      setState(() {
        if (st == null) {
          _suggFollow.remove(u.id);
        } else {
          _suggFollow[u.id] = st;
        }
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        if (before == null) {
          _suggFollow.remove(u.id);
        } else {
          _suggFollow[u.id] = before;
        }
      });
      lsSnack(context, l10n.somethingWentWrong, error: true);
    } finally {
      if (mounted) setState(() => _suggBusy.remove(u.id));
    }
  }

  Widget _buildSuggestedCarousel(ColorScheme cs, AppLocalizations l10n) {
    final visible = _suggestionsOpen && !_suggestionsDismissed && (_suggestionsLoading || _suggested.isNotEmpty);
    return AnimatedSize(
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOutCubic,
      alignment: Alignment.topCenter,
      child: !visible
          ? const SizedBox(width: double.infinity)
          : Padding(
              padding: const EdgeInsets.only(top: 6, bottom: 8),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(kLsPad, 0, 4, 0),
                  child: Row(children: [
                    const Expanded(
                      child: Text('Suggested for you', style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w800)),
                    ),
                    IconButton(
                      tooltip: 'Dismiss',
                      visualDensity: VisualDensity.compact,
                      icon: Icon(Icons.close_rounded, size: 20, color: cs.onSurfaceVariant),
                      onPressed: _dismissSuggestions,
                    ),
                  ]),
                ),
                SizedBox(
                  height: 196,
                  child: _suggestionsLoading && _suggested.isEmpty
                      ? ListView.separated(
                          scrollDirection: Axis.horizontal,
                          padding: const EdgeInsets.symmetric(horizontal: kLsPad),
                          itemCount: 3,
                          separatorBuilder: (_, __) => const SizedBox(width: 10),
                          itemBuilder: (_, __) => Container(
                            width: 142,
                            decoration: BoxDecoration(
                              color: cs.surfaceVariant.withOpacity(.5),
                              borderRadius: BorderRadius.circular(14),
                            ),
                          ),
                        )
                      : ListView.separated(
                          scrollDirection: Axis.horizontal,
                          padding: const EdgeInsets.symmetric(horizontal: kLsPad),
                          itemCount: _suggested.length,
                          separatorBuilder: (_, __) => const SizedBox(width: 10),
                          itemBuilder: (_, i) => _suggestionCard(_suggested[i], cs, l10n),
                        ),
                ),
              ]),
            ),
    );
  }

  Widget _suggestionCard(MiniUser u, ColorScheme cs, AppLocalizations l10n) {
    final st = _suggFollow[u.id];
    final following = st != null;
    final label = st == 'PENDING' ? 'Requested' : (following ? 'Following' : 'Follow');
    return Container(
      key: ValueKey('sugg-${u.id}'),
      width: 142,
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: cs.outlineVariant),
      ),
      child: Stack(children: [
        InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: () => Navigator.push(
            context,
            MaterialPageRoute(builder: (_) => TargetProfilePage(username: u.username)),
          ),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(10, 14, 10, 10),
            child: Column(children: [
              _miniAvatar(u, 32, cs),
              const SizedBox(height: 8),
              Text(u.username,
                  maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700)),
              const SizedBox(height: 2),
              Text(u.fullName.isEmpty ? ' ' : u.fullName,
                  maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
              const Spacer(),
              SizedBox(
                width: double.infinity,
                height: 30,
                child: following
                    ? OutlinedButton(
                        onPressed: _suggBusy.contains(u.id) ? null : () => _toggleFollowSuggestion(u, l10n),
                        style: OutlinedButton.styleFrom(padding: EdgeInsets.zero, visualDensity: VisualDensity.compact),
                        child: Text(label, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700)),
                      )
                    : FilledButton(
                        onPressed: _suggBusy.contains(u.id) ? null : () => _toggleFollowSuggestion(u, l10n),
                        style: FilledButton.styleFrom(padding: EdgeInsets.zero, visualDensity: VisualDensity.compact),
                        child: Text(label, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700)),
                      ),
              ),
            ]),
          ),
        ),
        Positioned(
          top: 0,
          right: 0,
          child: IconButton(
            tooltip: 'Remove',
            visualDensity: VisualDensity.compact,
            iconSize: 16,
            icon: Icon(Icons.close_rounded, color: cs.onSurfaceVariant),
            onPressed: () => setState(() => _suggested = _suggested.where((x) => x.id != u.id).toList()),
          ),
        ),
      ]),
    );
  }

  Widget _buildTopBar(ColorScheme cs, AppLocalizations l10n) {
    return Row(children: [
      Semantics(
        button: true,
        label: l10n.back,
        child: Tooltip(
          message: l10n.back,
          child: InkWell(
            onTap: () => Navigator.maybePop(context),
            customBorder: const CircleBorder(),
            child: Container(
              width: 34,
              height: 34,
              decoration: BoxDecoration(shape: BoxShape.circle, color: cs.surface, border: Border.all(color: cs.outlineVariant)),
              child: Icon(Icons.arrow_back_rounded, size: 16, color: cs.onSurface),
            ),
          ),
        ),
      ),
      const SizedBox(width: 8),
      Expanded(
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Flexible(
            child: Text(targetUser!.username,
                maxLines: 1, overflow: TextOverflow.ellipsis, style: LsType.head(context, size: 16)),
          ),
          if (targetUser!.isVerified) ...[
            const SizedBox(width: 4),
            Icon(Icons.verified_rounded, size: 16, color: cs.primary, semanticLabel: l10n.verifiedAccount),
          ],
        ]),
      ),
      if (targetUser!.myId != targetUser!.targetUserId)
        Semantics(
          button: true,
          label: l10n.moreOptions,
          child: Tooltip(
            message: l10n.moreOptions,
            child: InkWell(
              onTap: _blockBusy ? null : _openMoreMenu,
              customBorder: const CircleBorder(),
              child: Container(
                width: 34,
                height: 34,
                decoration: BoxDecoration(shape: BoxShape.circle, color: cs.surface, border: Border.all(color: cs.outlineVariant)),
                child: Icon(Icons.more_horiz_rounded, size: 18, color: cs.onSurface),
              ),
            ),
          ),
        ),
    ]);
  }

  // ============================================================
  // BLOCK / UNBLOCK (Instagram-style)
  //  * ⋮ menu -> Block user / Unblock user
  //  * block asks for confirmation, unblock is instant (same as chat screen)
  //  * after either one the profile is reloaded, so the screen flips between
  //    the normal profile and the "You've blocked this account" card
  //  * backend: POST /profile/blocked-users/, DELETE /profile/blocked-users/<id>/
  // ============================================================
  Future<void> _openMoreMenu() async {
    final t = targetUser;
    if (t == null || t.myId == t.targetUserId) return;
    final l10n = AppLocalizations.of(context)!;
    final blocked = t.isBlockedByMe;
    final errorColor = Theme.of(context).colorScheme.error;

    final action = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          // Blocked: the only things that make sense are Unblock and Report.
          if (!blocked) ...[
            ListTile(
              leading: const Icon(Icons.visibility_off_outlined),
              title: Text(t.isMutedByMe ? l10n.profileUnmute : l10n.profileMute),
              onTap: () => Navigator.pop(ctx, t.isMutedByMe ? 'unmute' : 'mute'),
            ),
            ListTile(
              leading: const Icon(Icons.shield_outlined),
              title: Text(t.amIRestricting ? l10n.profileUnrestrict : l10n.profileRestrict),
              onTap: () => Navigator.pop(ctx, t.amIRestricting ? 'unrestrict' : 'restrict'),
            ),
          ],
          ListTile(
            leading: const Icon(Icons.flag_outlined),
            title: Text(l10n.reportAction),
            onTap: () => Navigator.pop(ctx, 'report'),
          ),
          ListTile(
            leading: Icon(blocked ? Icons.lock_open_rounded : Icons.block_rounded, color: blocked ? null : errorColor),
            title: Text(
              blocked ? l10n.chatUnblockUser : l10n.chatBlockUser,
              style: blocked ? null : TextStyle(color: errorColor),
            ),
            onTap: () => Navigator.pop(ctx, blocked ? 'unblock' : 'block'),
          ),
        ]),
      ),
    );
    if (!mounted || action == null) return;
    switch (action) {
      case 'block':
        await _blockTarget(l10n);
        break;
      case 'unblock':
        await _unblockTarget(l10n);
        break;
      case 'mute':
      case 'unmute':
      case 'restrict':
      case 'unrestrict':
        await _toggleSoftAction(action, l10n);
        break;
      case 'report':
        await showReportSheet(context, targetType: 'user', targetId: '${t.targetUserId}');
        break;
    }
  }

  /// Mute / Restrict (and their undo): reversible, silent, no confirmation.
  /// The profile is reloaded so the menu shows the opposite action next time.
  Future<void> _toggleSoftAction(String action, AppLocalizations l10n) async {
    final t = targetUser;
    if (t == null || _blockBusy) return;
    setState(() => _blockBusy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      switch (action) {
        case 'mute':
          await ApiService.muteUser(t.targetUserId);
          break;
        case 'unmute':
          await ApiService.unmuteUser(t.targetUserId);
          break;
        case 'restrict':
          await ApiService.restrictUser(t.targetUserId);
          break;
        default:
          await ApiService.unrestrictUser(t.targetUserId);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _blockBusy = false);
      messenger.showSnackBar(SnackBar(content: Text(l10n.profileMoreFailed(e.toString()))));
      return;
    }
    if (!mounted) return;
    setState(() => _blockBusy = false);
    final msg = {
      'mute': l10n.profileMutedSnack,
      'unmute': l10n.profileUnmutedSnack,
      'restrict': l10n.profileRestrictedSnack,
      'unrestrict': l10n.profileUnrestrictedSnack,
    }[action]!;
    messenger.showSnackBar(SnackBar(content: Text(msg)));
    await _loadProfile();
  }

  Future<void> _blockTarget(AppLocalizations l10n) async {
    final t = targetUser;
    if (t == null || _blockBusy) return;

    setState(() => _blockBusy = true);
    // Shared flow: confirm (+ "also report" / "also block new accounts") -> API -> snackbar.
    final ok = await blockUserFlow(context, userId: t.targetUserId, username: t.username);
    if (!mounted) return;
    setState(() => _blockBusy = false);
    if (ok) await _loadProfile(); // comes back as the minimal "blocked" card
  }

  Future<void> _unblockTarget(AppLocalizations l10n) async {
    final t = targetUser;
    if (t == null || _blockBusy) return;

    setState(() => _blockBusy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ApiService.unblockUser(t.targetUserId);
    } catch (e) {
      if (!mounted) return;
      setState(() => _blockBusy = false);
      messenger.showSnackBar(SnackBar(content: Text(l10n.chatUnblockFailed(e.toString()))));
      return;
    }
    if (!mounted) return;
    setState(() => _blockBusy = false);
    messenger.showSnackBar(SnackBar(content: Text(l10n.chatUserUnblocked)));
    await _loadProfile(); // full profile + posts are back
  }

  Widget _blockedNotice(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 24, kLsPad, 24),
      child: EmptyStateWidget(
        icon: Icons.block_rounded,
        title: l10n.profileBlockedByMeTitle,
        subtitle: l10n.profileBlockedByMeSubtitle,
      ),
    );
  }

  Widget _buildHeader(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 6, kLsPad, 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
          _avatar(cs),
          const SizedBox(width: 20),
          Expanded(
            child: _isBlockedByMe
                ? const SizedBox.shrink()
                : Row(children: [
              _statColumn('${targetUser!.posts}', l10n.postsStat, cs),
              _statColumn('${targetUser!.followers}', l10n.followersStat, cs,
                  onTap: () => Navigator.push(context,
                      MaterialPageRoute(builder: (_) => FollowListScreen(username: targetUser!.username, followers: true)))),
              _statColumn('${targetUser!.following}', l10n.following, cs,
                  onTap: () => Navigator.push(context,
                      MaterialPageRoute(builder: (_) => FollowListScreen(username: targetUser!.username, followers: false)))),
            ]),
          ),
        ]),
        const SizedBox(height: 12),
        Row(children: [
          Flexible(
            child: Text(
              (targetUser!.firstName.isEmpty && targetUser!.lastName.isEmpty)
                  ? l10n.noNameYet
                  : '${targetUser!.firstName} ${targetUser!.lastName}'.trim(),
              style: LsType.head(context, size: 14.5),
            ),
          ),
        ]),
        if (targetUser!.isPrivate) ...[
          const SizedBox(height: 6),
          LsStatusChip(label: l10n.privateAccountBadge, color: cs.onSurfaceVariant, icon: Icons.lock_rounded),
        ],
        // P7-FE — see profile.dart. Private/restricted view sends none of these => renders nothing.
        ProfileBioBlock(
          bio: targetUser!.bio,
          pronouns: targetUser!.pronouns,
          categoryLabel: targetUser!.categoryLabel,
          links: targetUser!.links,
        ),
        if (!_mutuals.isEmpty) _mutualLine(cs), // P8-FE
        const SizedBox(height: 14),
        _followSection(cs, l10n),
        // P13-FE — top-3 badges + "All" sheet (hidden when none / private-gated).
        if (_badges.isNotEmpty && !_contentHidden) ...[
          const SizedBox(height: 10),
          ProfileBadgesRow(badges: _badges, ownerName: targetUser!.username),
        ],
      ]),
    );
  }

  Widget _avatar(ColorScheme cs) {
    final photo = targetUser!.profilePhoto;
    final url = photo.isEmpty ? '' : (photo.startsWith('http') ? photo : '${Api.baseUrl}$photo');
    return Container(
      width: 86,
      height: 86,
      padding: const EdgeInsets.all(2),
      decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: cs.outlineVariant, width: 2)),
      child: ClipOval(
        child: photo.isEmpty
            ? Container(color: cs.surfaceVariant, child: Icon(Icons.person_rounded, size: 40, color: cs.onSurfaceVariant))
            : CachedNetworkImage(
                imageUrl: url,
                width: double.infinity,
                height: double.infinity,
                fit: BoxFit.cover,
                placeholder: (c, u) => Container(color: cs.surfaceVariant),
                errorWidget: (c, u, e) =>
                    Container(color: cs.surfaceVariant, child: Icon(Icons.person_rounded, size: 40, color: cs.onSurfaceVariant)),
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
              style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w500, color: cs.onSurface)),
        ]),
      ),
    );
  }

  Widget _followSection(ColorScheme cs, AppLocalizations l10n) {
    // Defensive — this screen is for someone else's profile; if it's ever
    // opened on your own username, show nothing rather than a Follow
    // button pointed at yourself.
    if (targetUser!.myId == targetUser!.targetUserId) return const SizedBox.shrink();

    // Blocked by me: no Follow / Message / Share — just a way back.
    if (_isBlockedByMe) {
      return LsPrimaryButton(
        label: l10n.chatUnblock,
        loading: _blockBusy,
        onPressed: _blockBusy ? null : () => _unblockTarget(l10n),
      );
    }

    final myStatus = targetUser!.myFollowStatus;
    final theirStatus = targetUser!.theirFollowStatus;
    final children = <Widget>[];

    if (theirStatus == 'PENDING') {
      children.addAll([
        Row(children: [
          Expanded(
            child: LsPrimaryButton(
              label: l10n.confirm,
              loading: isActionLoading,
              onPressed: isActionLoading ? null : () => handleAcceptRequest(l10n),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: ProfileActionButton(label: l10n.delete, onPressed: isActionLoading ? null : () => handleRejectRequest(l10n)),
          ),
        ]),
        const SizedBox(height: 10),
      ]);
    }

    children.add(_mainFollowButton(l10n, myStatus, theirStatus));
    children.addAll([
      const SizedBox(height: 10),
      Row(children: [
        Expanded(
          child: ProfileActionButton(
            label: l10n.messageButton,
            onPressed: isActionLoading ? null : () => _openChatWithUser(l10n),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: ProfileActionButton(label: l10n.shareProfileButton, onPressed: _shareProfile),
        ),
      ]),
    ]);

    return Column(children: children);
  }

  Widget _mainFollowButton(AppLocalizations l10n, String? myStatus, String? theirStatus) {
    if (myStatus == 'PENDING') {
      return LsOutlineButton(label: l10n.requestedLabel, onPressed: isActionLoading ? null : () => handleFollow(l10n));
    }
    if (myStatus == 'ACCEPTED') {
      return LsOutlineButton(label: l10n.following, onPressed: isActionLoading ? null : () => handleFollow(l10n));
    }
    final label = (myStatus == null && theirStatus == 'ACCEPTED') ? l10n.followBack : l10n.follow;
    return LsPrimaryButton(label: label, loading: isActionLoading, onPressed: isActionLoading ? null : () => handleFollow(l10n));
  }

  Widget _privateGateNotice(ColorScheme cs, AppLocalizations l10n) {
    final pending = targetUser!.myFollowStatus == 'PENDING';
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 24, kLsPad, 24),
      child: EmptyStateWidget(
        icon: Icons.lock_rounded,
        title: l10n.privateAccountBadge,
        subtitle: pending ? l10n.privateAccountPendingMessage : l10n.privateAccountMessage,
      ),
    );
  }

  // ---------- segmented tabs ----------

  Widget _buildSegmentedTabs(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 10),
      child: Row(children: [
        Expanded(
          child: _segmentButton(cs,
              icon: Icons.grid_on_rounded,
              label: l10n.mediaTabLabel(_mediaPosts.length),
              selected: _selectedTab == 0,
              onTap: () => setState(() => _selectedTab = 0)),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _segmentButton(cs,
              icon: Icons.description_outlined,
              label: l10n.documentsTabLabel(_documentPosts.length),
              selected: _selectedTab == 1,
              onTap: () => setState(() => _selectedTab = 1)),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _segmentButton(cs,
              icon: Icons.repeat_rounded,
              label: l10n.repostsTabLabel(_repostPosts.length),
              selected: _selectedTab == 2,
              onTap: () => setState(() => _selectedTab = 2)),
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

    if (postsLoadFailed && targetPosts.isEmpty) {
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

    if (_selectedTab == 2) return _buildRepostGridSlivers(cs, l10n);

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
                return PinnedTileOverlay(
                  pinned: post.isPinned,
                  child: MediaGridTile(post: post, l10n: l10n, onTap: () => _openPost(post)),
                );
              }
              final file = post.media.isNotEmpty ? post.media.first : null;
              if (file == null) return const SizedBox.shrink();
              return PinnedTileOverlay(
                pinned: post.isPinned,
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

  // ---------- Reposts tab grid ----------
  // Same grid/skeleton/pagination-footer shape as Media/Documents above
  // (reposts already live inside `targetPosts`/`_loadMorePosts`'s own
  // pagination — no separate endpoint or page-cursor needed), but each
  // tile renders the embedded *original* post's preview (Twitter/IG-style
  // "reposted" card), not the repost row itself.
  List<Widget> _buildRepostGridSlivers(ColorScheme cs, AppLocalizations l10n) {
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
      // Hard-deleted or unavailable original (see `PostModel.fromJson` —
      // `is_unavailable` stub never leaks the hidden post's content).
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

    // Small "repeat" badge, top-right, marks this tile as a repost without
    // blocking taps on the underlying media/text tile beneath it.
    return PinnedTileOverlay(
      key: ValueKey('pin-${post.id}'),
      pinned: post.isPinned,
      alignLeft: true, // top-right is taken by the repost badge
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

  // ---------- skeletons ----------

  Widget _headerSkeleton() {
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
          const LsSkeletonBox(height: 44, radius: 24),
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