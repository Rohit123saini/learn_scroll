import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:share_plus/share_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';

import '../api_service.dart';
import '../model.dart';
import '../../utils/api.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/profile_media_tiles.dart';
import '../../l10n/app_localizations.dart';
import '../../post/screens/singlepost.dart';
import '../../message/services/message_api_service.dart';
import '../../message/screens/chat_screen.dart';
import 'follow_list_screen.dart';

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
  int _postsPage = 1;
  int _selectedTab = 0;
  String? errorMessage;

  final ScrollController _scrollController = ScrollController();

  bool get _isPrivateGated =>
      targetUser != null &&
      targetUser!.isPrivate &&
      targetUser!.myFollowStatus != 'ACCEPTED' &&
      targetUser!.myId != targetUser!.targetUserId;

  List<PostModel> get _mediaPosts =>
      targetPosts.where((p) => p.postType == 'image' || p.postType == 'video').toList();

  List<PostModel> get _documentPosts => targetPosts
      .where((p) => ['document', 'pdf', 'excel', 'docx', 'xls', 'doc'].contains(p.postType))
      .toList();

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
    setState(() {
      isLoading = true;
      errorMessage = null;
    });
    try {
      final data = await ApiService.getTargetProfile(widget.username);
      if (!mounted) return;
      setState(() {
        targetUser = data;
        isLoading = false;
      });
      if (_isPrivateGated) {
        setState(() => isPostsLoading = false);
      } else {
        await _loadPostsFirstPage();
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
    if (targetUser == null || isLoadingMorePosts || !hasMorePosts || isPostsLoading || _isPrivateGated) return;
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

  Future<void> handleFollow(AppLocalizations l10n) async {
    if (targetUser == null || isActionLoading) return;
    setState(() => isActionLoading = true);
    try {
      final result = await ApiService.followUser(targetUser!.targetUserId);
      await _loadProfile();
      if (mounted && result['message'] != null) lsSnack(context, result['message'].toString());
    } catch (e) {
      if (mounted) lsSnack(context, l10n.somethingWentWrong, error: true);
    } finally {
      if (mounted) setState(() => isActionLoading = false);
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
            if (_isPrivateGated)
              SliverToBoxAdapter(child: _privateGateNotice(cs, l10n))
            else ...[
              SliverToBoxAdapter(child: _buildSegmentedTabs(cs, l10n)),
              ..._buildGridSlivers(cs, l10n),
            ],
            const SliverToBoxAdapter(child: SizedBox(height: 40)),
          ],
        ),
      ),
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
    ]);
  }

  Widget _buildHeader(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 6, kLsPad, 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
          _avatar(cs),
          const SizedBox(width: 18),
          Expanded(
            child: Row(children: [
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
        if (targetUser!.bio.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(targetUser!.bio, style: TextStyle(fontSize: 12.5, height: 1.45, color: cs.onSurface)),
        ],
        const SizedBox(height: 14),
        _followSection(cs, l10n),
      ]),
    );
  }

  Widget _avatar(ColorScheme cs) {
    final photo = targetUser!.profilePhoto;
    final url = photo.isEmpty ? '' : (photo.startsWith('http') ? photo : '${Api.baseUrl}$photo');
    return Container(
      width: 84,
      height: 84,
      padding: const EdgeInsets.all(2),
      decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: cs.outlineVariant, width: 2)),
      child: ClipOval(
        child: photo.isEmpty
            ? Container(color: cs.surfaceVariant, child: Icon(Icons.person_rounded, size: 40, color: cs.onSurfaceVariant))
            : CachedNetworkImage(
                imageUrl: url,
                width: 80,
                height: 80,
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
              style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
        ]),
      ),
    );
  }

  Widget _followSection(ColorScheme cs, AppLocalizations l10n) {
    // Defensive — this screen is for someone else's profile; if it's ever
    // opened on your own username, show nothing rather than a Follow
    // button pointed at yourself.
    if (targetUser!.myId == targetUser!.targetUserId) return const SizedBox.shrink();

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
            child: LsOutlineButton(label: l10n.delete, onPressed: isActionLoading ? null : () => handleRejectRequest(l10n)),
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
          child: LsOutlineButton(
            label: l10n.messageButton,
            icon: Icons.mail_outline_rounded,
            onPressed: isActionLoading ? null : () => _openChatWithUser(l10n),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: LsOutlineButton(label: l10n.shareProfileButton, icon: Icons.ios_share_rounded, onPressed: _shareProfile),
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
