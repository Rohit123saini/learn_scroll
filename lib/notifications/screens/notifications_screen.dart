// lib/features/notifications/screens/notifications_screen.dart
//
// Home ke bell-icon tap se yahan push karo:
//   Navigator.push(context, MaterialPageRoute(builder: (_) => const NotificationsScreen()));
//
// Design-system: `ls_ui.dart` ke shared widgets use kiye hain (lsAppBar, lsBg, lsSnack)
// taaki Home/Assignments/Test-Series ke saath visually consistent rahe.
// Colors kahin bhi hardcoded nahi — sab Theme.of(context).colorScheme se.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show HapticFeedback;
import 'package:cached_network_image/cached_network_image.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../models/notification_model.dart';
import '../widgets/notification_tile.dart';
import '../services/notification_service.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/skeletons.dart'; // Task 11 — LsListSkeleton for the first-load state.
import '../../tuitionclass/screens/classroom_detail_screen.dart';
import '../../campus/screens/campus_screen.dart';
import '../../assignments/screens/assignments_screen.dart';
import '../../testseries/screens/test_series_detail_screen.dart';
import '../../wallet/screens/wallet_screen.dart';
import '../../post/screens/singlepost.dart';
import '../../post/screens/story_viewer_screen.dart';
import '../../post/models/story_model.dart' show StoryGroup;
import '../../post/services/story_service.dart';
import '../../l10n/app_localizations.dart';
import '../../message/services/message_api_service.dart';
import '../../message/screens/chat_screen.dart';
import '../../profile/screens/target_profile.dart';
import '../../profile/api_service.dart' as profile_api;
import '../../profile/widgets/block_report.dart'; // Block from the notification menu
import '../../profile/screens/follow_requests_screen.dart'; // N11-FE — P11-FE (class name/path confirm karo)
// import '../../../widgets/skeletons.dart'; // agar generic list-skeleton chahiye to add karo

// ============================================================
// 🔥 FIX [Settings/Nav pass] —
//   1) Settings gear yahan se hata diya gaya — notification-channel
//      settings ab sirf Profile > (⋯) > Settings > Notifications se
//      milti hain (ek hi jagah), is list-screen ke top se nahi.
//      (dekho profile/screens/settings_screen.dart)
//   2) Message-type notifications (chat/mention/incoming-call/parent-
//      device) ab is list me bilkul nahi aatin — Instagram jaisa,
//      unke liye unread count Chats tab ke badge pe dikhta hai
//      (dekho widgets/app_bottom_nav.dart). Isliye list hamesha
//      `source: 'tuitionclass'` se fetch hoti hai.
//   3) `_onTapNotification` ab sirf TODO nahi hai — notif_type ke
//      hisaab se best-effort deep-link karta hai. Naya type add ho to
//      `_navigateForNotification` me ek aur case jod dena, isi pattern se.
// ============================================================

enum _NotifAction { delete, muteActor, muteType, blockActor }

const double _kScrollThreshold = 700; // home.dart wala hi 700px convention (Task 10.5)

class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key});

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  final _scrollController = ScrollController();
  final List<NotificationModel> _items = [];

  bool _loading = true;
  bool _loadingMore = false;
  bool _hasError = false;
  bool _hasMore = true;
  int _offset = 0;
  static const int _limit = 30;

  // TASK 6 (production_readiness_tasks.md) — local optimistic state for
  // the follow-request quick actions, keyed by notification id (not list
  // index, so it survives _loadMore reflows). Seeded lazily from each
  // row's own `data` payload the first time it's read in
  // _followBackState/_buildFollowAction below.
  final Map<int, bool> _isFollowingActor = {}; // follow_request_accepted rows
  final Set<int> _confirmedFollowRequests = {}; // follow_request_received rows
  final Set<int> _rejectedFollowRequests = {};
  final Set<int> _followActionInFlight = {};

  // N11-FE — "Follow requests (N)" header row. N == 0 -> row hide.
  // Silent-fail: fetch fail ho to purana (ya 0 = hidden) hi rehta hai.
  int _followRequestCount = 0;
  List<String> _followRequestAvatars = const [];
  static const int _kFollowRequestAvatarCount = 3;

  Future<void> _loadFollowRequests() async {
    try {
      // GET /profile/follow-requests/ is a DRF page: `total` is the real pending
      // count, `rows` the first page. The rows carry no avatar key on older
      // backends, so the avatar stack just stays empty there.
      final res = await profile_api.ApiService.getFollowRequests();
      if (!mounted) return;
      setState(() {
        _followRequestCount = res.total ?? res.rows.length;
        _followRequestAvatars = [
          for (final u in res.rows.take(_kFollowRequestAvatarCount))
            if ('${u['profile_photo'] ?? u['profile_picture'] ?? ''}'.isNotEmpty)
              '${u['profile_photo'] ?? u['profile_picture']}',
        ];
      });
    } catch (_) {
      // silent — purana count as-is.
    }
  }

  Future<void> _openFollowRequests() async {
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const FollowRequestsScreen()),
    );
    // Wapas aane pe accept/decline ho chuka hoga — count + avatars refresh.
    _loadFollowRequests();
  }

  Widget _buildFollowRequestsRow(ColorScheme scheme) {
    const double r = 20, overlap = 26;
    final avatars = _followRequestAvatars;
    final stackW = avatars.isEmpty ? 0.0 : overlap * (avatars.length - 1) + r * 2;
    return InkWell(
      onTap: _openFollowRequests,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(
          children: [
            if (avatars.isNotEmpty)
              SizedBox(
                width: stackW,
                height: r * 2,
                child: Stack(
                  children: [
                    // pehla avatar sabse upar rahe — reverse order me paint.
                    for (var i = avatars.length - 1; i >= 0; i--)
                      Positioned(
                        left: i * overlap,
                        child: Container(
                          padding: const EdgeInsets.all(1.5),
                          decoration: BoxDecoration(shape: BoxShape.circle, color: lsBg(context)),
                          child: CircleAvatar(
                            radius: r - 1.5,
                            backgroundColor: scheme.surfaceContainerHighest,
                            backgroundImage: CachedNetworkImageProvider(avatars[i]),
                          ),
                        ),
                      ),
                  ],
                ),
              )
            else
              CircleAvatar(
                radius: r,
                backgroundColor: scheme.surfaceContainerHighest,
                child: Icon(Icons.person_add_alt_1_rounded, color: scheme.onSurfaceVariant),
              ),
            const SizedBox(width: 14),
            Expanded(
              child: Text(
                'Follow requests ($_followRequestCount)',
                style: const TextStyle(fontSize: 14.5, fontWeight: FontWeight.w600),
              ),
            ),
            Icon(Icons.chevron_right_rounded, color: scheme.onSurfaceVariant),
          ],
        ),
      ),
    );
  }

  Future<void> _confirmFollowRequest(NotificationModel n) async {
    final rawId = n.data?['follow_id'];
    final followId = rawId is int ? rawId : int.tryParse('$rawId');
    if (followId == null) return; // older row, created before this fix
    setState(() => _followActionInFlight.add(n.id));
    try {
      await profile_api.ApiService.acceptFollowRequest(followId);
      if (!mounted) return;
      setState(() {
        _confirmedFollowRequests.add(n.id);
        _followActionInFlight.remove(n.id);
      });
      _loadFollowRequests(); // N11-FE — N ghatao
    } catch (_) {
      if (!mounted) return;
      setState(() => _followActionInFlight.remove(n.id));
      lsSnack(context, 'Could not confirm the request, try again.', error: true);
    }
  }

  Future<void> _rejectFollowRequest(NotificationModel n) async {
    final rawId = n.data?['follow_id'];
    final followId = rawId is int ? rawId : int.tryParse('$rawId');
    if (followId == null) return;
    setState(() => _followActionInFlight.add(n.id));
    try {
      await profile_api.ApiService.rejectFollowRequest(followId);
      if (!mounted) return;
      setState(() {
        _rejectedFollowRequests.add(n.id);
        _followActionInFlight.remove(n.id);
      });
      _loadFollowRequests(); // N11-FE — N ghatao
    } catch (_) {
      if (!mounted) return;
      setState(() => _followActionInFlight.remove(n.id));
      lsSnack(context, 'Could not remove the request, try again.', error: true);
    }
  }

  // `follow/<user_id>/` is a toggle (see user_profile.FollowAPIView.post) —
  // one call handles both "follow back" and "unfollow" depending on
  // current state, so this method covers both directions.
  Future<void> _toggleFollowBack(NotificationModel n, bool currentlyFollowing) async {
    final rawId = n.data?['actor_id'];
    final actorId = rawId is int ? rawId : int.tryParse('$rawId');
    if (actorId == null) return; // older row, created before this fix
    setState(() => _followActionInFlight.add(n.id));
    try {
      await profile_api.ApiService.followUser(actorId);
      if (!mounted) return;
      setState(() {
        _isFollowingActor[n.id] = !currentlyFollowing;
        _followActionInFlight.remove(n.id);
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _followActionInFlight.remove(n.id));
      lsSnack(context, 'Could not update follow status, try again.', error: true);
    }
  }

  /// Trailing quick-action for a follow-type row, or null for every other
  /// notif_type (falls back to the plain timeago Text in that case).
  Widget? _buildFollowAction(NotificationModel n, ColorScheme scheme) {
    final busy = _followActionInFlight.contains(n.id);
    const btnPad = EdgeInsets.symmetric(horizontal: 12);
    const shrink = MaterialTapTargetSize.shrinkWrap;

    if (n.notifType == 'follow_request_received') {
      if (_rejectedFollowRequests.contains(n.id)) return null;
      if (_confirmedFollowRequests.contains(n.id)) {
        return Text('Following', style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: scheme.onSurfaceVariant));
      }
      return Row(mainAxisSize: MainAxisSize.min, children: [
        SizedBox(
          height: 30,
          child: ElevatedButton(
            onPressed: busy ? null : () => _confirmFollowRequest(n),
            style: ElevatedButton.styleFrom(backgroundColor: scheme.primary, foregroundColor: scheme.onPrimary, padding: btnPad, minimumSize: Size.zero, tapTargetSize: shrink),
            child: busy
                ? SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: scheme.onPrimary))
                : const Text('Confirm', style: TextStyle(fontSize: 12)),
          ),
        ),
        const SizedBox(width: 6),
        SizedBox(
          height: 30,
          child: OutlinedButton(
            onPressed: busy ? null : () => _rejectFollowRequest(n),
            style: OutlinedButton.styleFrom(padding: btnPad, minimumSize: Size.zero, tapTargetSize: shrink, side: BorderSide(color: scheme.outlineVariant)),
            child: const Text('Delete', style: TextStyle(fontSize: 12)),
          ),
        ),
      ]);
    }

    if (n.notifType == 'follow_request_accepted') {
      if (n.data?['actor_id'] == null) return null; // older row, no actor recorded
      final following = _isFollowingActor[n.id] ?? (n.data?['is_following_actor'] == true);
      return SizedBox(
        height: 30,
        child: following
            ? OutlinedButton(
                onPressed: busy ? null : () => _toggleFollowBack(n, true),
                style: OutlinedButton.styleFrom(padding: btnPad, minimumSize: Size.zero, tapTargetSize: shrink, side: BorderSide(color: scheme.outlineVariant)),
                child: const Text('Following', style: TextStyle(fontSize: 12)),
              )
            : ElevatedButton(
                onPressed: busy ? null : () => _toggleFollowBack(n, false),
                style: ElevatedButton.styleFrom(backgroundColor: scheme.primary, foregroundColor: scheme.onPrimary, padding: btnPad, minimumSize: Size.zero, tapTargetSize: shrink),
                child: busy
                    ? SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: scheme.onPrimary))
                    : const Text('Follow back', style: TextStyle(fontSize: 12)),
              ),
      );
    }
    return null;
  }

  // ------------------------------------------------------------------
  // N6-FE — swipe-to-delete (with undo) + long-press actions.
  // ------------------------------------------------------------------

  static int? _asInt(Object? v) => v is int ? v : int.tryParse('$v');

  static String _humanType(String t) {
    final s = t.replaceAll('_', ' ').trim();
    return s.isEmpty ? t : s[0].toUpperCase() + s.substring(1);
  }

  void _restoreItem(NotificationModel n, int index) {
    if (!mounted || _items.any((x) => x.id == n.id)) return;
    setState(() => _items.insert(index.clamp(0, _items.length), n));
  }

  /// Left-swipe delete. The row disappears at once, but the real DELETE
  /// is only sent when the snackbar closes WITHOUT "Undo" — the backend
  /// has no restore, so undo has to happen before the call, not after.
  void _deleteWithUndo(NotificationModel n) {
    final index = _items.indexWhere((x) => x.id == n.id);
    if (index < 0) return;
    setState(() => _items.removeAt(index));

    final messenger = ScaffoldMessenger.of(context);
    messenger.hideCurrentSnackBar(); // a previous pending delete fires now
    final ctrl = messenger.showSnackBar(
      SnackBar(
        content: const Text('Notification deleted'),
        duration: const Duration(seconds: 4),
        action: SnackBarAction(label: 'Undo', onPressed: () {}),
      ),
    );
    ctrl.closed.then((reason) async {
      if (reason == SnackBarClosedReason.action) {
        _restoreItem(n, index);
        return;
      }
      try {
        await NotificationService.instance.deleteNotification(n.id);
        // Server list is now one row shorter; keep the offset aligned so
        // the next _loadMore doesn't skip a row.
        _offset = _offset > 0 ? _offset - 1 : 0;
      } catch (_) {
        if (!mounted) return;
        _restoreItem(n, index);
        lsSnack(context, 'Could not delete the notification, try again.', error: true);
      }
    });
  }

  void _showUndoSnack(String message, Future<void> Function() onUndo) {
    final messenger = ScaffoldMessenger.of(context);
    messenger.hideCurrentSnackBar();
    messenger.showSnackBar(
      SnackBar(
        content: Text(message),
        action: SnackBarAction(
          label: 'Undo',
          onPressed: () async {
            try {
              await onUndo();
            } catch (_) {
              if (mounted) lsSnack(context, 'Could not undo, try again.', error: true);
            }
          },
        ),
      ),
    );
  }

  Future<void> _showActionsSheet(NotificationModel n) async {
    HapticFeedback.mediumImpact();
    final actorId = _asInt(n.data?['actor_id']);
    final actorUsername = n.data?['actor_username']?.toString();
    final canMuteActor = actorId != null && actorUsername != null && actorUsername.isNotEmpty;
    final typeLabel = _humanType(n.notifType);

    final choice = await showModalBottomSheet<_NotifAction>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.delete_outline),
              title: const Text('Delete'),
              onTap: () => Navigator.pop(ctx, _NotifAction.delete),
            ),
            if (canMuteActor)
              ListTile(
                leading: const Icon(Icons.notifications_off_outlined),
                title: Text('Mute notifications from @$actorUsername'),
                onTap: () => Navigator.pop(ctx, _NotifAction.muteActor),
              ),
            ListTile(
              leading: const Icon(Icons.block_outlined),
              title: Text('Turn off "$typeLabel"'),
              onTap: () => Navigator.pop(ctx, _NotifAction.muteType),
            ),
            if (canMuteActor)
              ListTile(
                leading: Icon(Icons.person_off_outlined, color: Theme.of(ctx).colorScheme.error),
                title: Text(
                  '${AppLocalizations.of(ctx)!.blockMenuBlockUser} @$actorUsername',
                  style: TextStyle(color: Theme.of(ctx).colorScheme.error),
                ),
                onTap: () => Navigator.pop(ctx, _NotifAction.blockActor),
              ),
          ],
        ),
      ),
    );
    if (choice == null || !mounted) return;

    switch (choice) {
      case _NotifAction.delete:
        _deleteWithUndo(n);
        break;
      case _NotifAction.muteActor:
        final mutedId = actorId!;
        try {
          await NotificationService.instance.muteUser(mutedId);
        } catch (_) {
          if (mounted) lsSnack(context, 'Could not mute @$actorUsername, try again.', error: true);
          return;
        }
        if (!mounted) return;
        _showUndoSnack(
          'Muted @$actorUsername',
          () => NotificationService.instance.unmuteUser(mutedId),
        );
        break;
      case _NotifAction.blockActor:
        final blockId = actorId!;
        final ok = await blockUserFlow(context, userId: blockId, username: actorUsername ?? '');
        if (!ok || !mounted) return;
        // Everything this person triggered leaves the bell now (the server
        // hides them too, and brings them back if the block is lifted).
        setState(() => _items.removeWhere((x) => _asInt(x.data?['actor_id']) == blockId));
        break;
      case _NotifAction.muteType:
        try {
          await NotificationService.instance.setTypeMuted(n.notifType, muted: true);
        } catch (_) {
          if (mounted) lsSnack(context, 'Could not update settings, try again.', error: true);
          return;
        }
        if (!mounted) return;
        _showUndoSnack(
          '"$typeLabel" turned off',
          () => NotificationService.instance.setTypeMuted(n.notifType, muted: false),
        );
        break;
    }
  }

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    _loadFirstPage();
    _loadFollowRequests(); // N11-FE
  }

  @override
  void dispose() {
    _scrollController.removeListener(_onScroll);
    _scrollController.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_hasMore || _loadingMore) return;
    final pos = _scrollController.position;
    if (pos.maxScrollExtent - pos.pixels < _kScrollThreshold) {
      _loadMore();
    }
  }

  Future<void> _loadFirstPage() async {
    _loadFollowRequests(); // N11-FE — pull-to-refresh pe row bhi sync (fire-and-forget)
    setState(() {
      _loading = true;
      _hasError = false;
    });
    try {
      final res = await NotificationService.instance.getNotifications(
        limit: _limit,
        offset: 0,
        source: 'tuitionclass',
      );
      setState(() {
        _items
          ..clear()
          ..addAll(res.results);
        _offset = res.results.length;
        _hasMore = res.results.length >= _limit;
        _loading = false;
      });
    } catch (_) {
      setState(() {
        _loading = false;
        _hasError = true;
      });
    }
  }

  Future<void> _loadMore() async {
    setState(() => _loadingMore = true);
    try {
      final res = await NotificationService.instance.getNotifications(
        limit: _limit,
        offset: _offset,
        source: 'tuitionclass',
      );
      setState(() {
        _items.addAll(res.results);
        _offset += res.results.length;
        _hasMore = res.results.length >= _limit;
        _loadingMore = false;
      });
    } catch (_) {
      // Instagram-jaisa: agla page fail ho to silently ruk jao, poori list error na ho.
      setState(() => _loadingMore = false);
    }
  }

  Future<void> _onMarkAllRead() async {
    // Optimistic: pehle UI update, phir background me API.
    setState(() {
      for (var i = 0; i < _items.length; i++) {
        if (!_items[i].isRead) _items[i] = _items[i].copyWithRead();
      }
    });
    try {
      await NotificationService.instance.markAllRead();
    } catch (_) {
      if (mounted) {
        lsSnack(context, 'Sab read mark karne me kuch gadbad hui, retry karo.');
      }
    }
  }

  Future<void> _openMentionedStory(NotificationModel n, {bool ownStory = false}) async {
    final storyId = n.data?['story_id']?.toString();
    if (storyId == null || storyId.isEmpty) return;
    final l10n = AppLocalizations.of(context)!;
    try {
      final story = await StoryService.getStory(storyId);
      if (!mounted) return;
      final group = StoryGroup(
        userId: story.user.id,
        username: story.user.username,
        userProfilePic: story.user.profilePicture,
        stories: [story],
      );
      // Mention: someone else's story -> myUserId stays null, so the viewer shows
      // the react / reply bar. `story_reaction`: it is MY story -> owner controls.
      Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => StoryViewerScreen(
            groups: [group],
            initialGroupIndex: 0,
            myUserId: ownStory ? story.user.id.toString() : null,
          ),
        ),
      );
    } on StoryUnavailableException {
      if (mounted) lsSnack(context, l10n.storyMentionUnavailable);
    } catch (_) {
      if (mounted) lsSnack(context, l10n.stickerLoadFailed, error: true);
    }
  }

  Future<void> _onTapNotification(int index) async {
    final n = _items[index];
    if (!n.isRead) {
      setState(() => _items[index] = n.copyWithRead());
      // Fire-and-forget — UI already updated, fail hone par silently ignore.
      NotificationService.instance.markRead(n.id).catchError((_) {});
    }
    if (!mounted) return;
    // N7-FE — a malformed/unknown payload must never crash the tap: the
    // row is already marked read, so the worst case is "nothing opens".
    try {
      await _navigateForNotification(n);
    } catch (_) {
      if (mounted) lsSnack(context, 'Could not open this notification.', error: true);
    }
  }

  /// N7-FE — post deep link. `data.comment_id` (comment / reply
  /// notifications) makes SinglePostPage open the comments, scroll to
  /// that comment and flash it. Returns false when there's no post_id.
  bool _openPost(NotificationModel n) {
    final postId = n.data?['post_id']?.toString();
    if (postId == null || postId.isEmpty) return false;
    final rawComment = n.data?['comment_id']?.toString();
    final commentId = (rawComment == null || rawComment.isEmpty) ? null : rawComment;
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => SinglePostPage(postId: postId, highlightCommentId: commentId)),
    );
    return true;
  }

  /// notif_type ke hisaab se best-effort deep-link. classroom_id sabse
  /// reliable signal hai (jahan bhi set hai wahi jaao), uske baad
  /// notif_type prefix se app-section decide karo. Ye list poori 30+
  /// types cover nahi karti — jo type yahan miss hai wo bas mark-read
  /// ho jaata hai, kahin navigate nahi karta (safe no-op). Naya type
  /// route karna ho to yahi pattern follow karo.
  Future<void> _navigateForNotification(NotificationModel n) async {
    if (n.source == NotificationSource.message) {
      final conversationId = n.data?['conversation_id']?.toString();
      if (conversationId == null) return;
      try {
        final convo = await MessageApiService.getConversation(conversationId);
        if (!mounted) return;
        Navigator.push(context, MaterialPageRoute(builder: (_) => ChatScreen(conversation: convo)));
      } catch (_) {
        if (mounted) lsSnack(context, 'Chat khulne me gadbad hui, dobara try karo.', error: true);
      }
      return;
    }

    // classroom_id jahan bhi mila, sabse specific destination hai.
    // TASK 9.3: "new test in your class" opens that class straight on its Tests tab.
    if (n.classroomId != null) {
      final isClassTest = n.notifType == 'testseries_posted';
      Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => ClassroomDetailScreen(
            classroomId: n.classroomId!,
            initialTab: isClassTest ? 'Tests' : null,
          ),
        ),
      );
      return;
    }

    final type = n.notifType;
    // TASK 6 (production_readiness_tasks.md) — "tapping the row (outside
    // the button) navigates to that user's profile screen." Needs
    // `actor_username` in `data`, which core/services.py::create_notification
    // now populates automatically for every actor-triggered notification;
    // older rows created before that fix simply won't have it, so this is
    // a safe no-op for those rather than a crash.
    if (type == 'follow_request_received' || type == 'follow_request_accepted') {
      final actorUsername = n.data?['actor_username']?.toString();
      if (actorUsername != null && actorUsername.isNotEmpty) {
        Navigator.push(context, MaterialPageRoute(builder: (_) => TargetProfilePage(username: actorUsername)));
      }
      return;
    }

    // Stories upgrade, Part 2 — "X mentioned you in their story": open that
    // story (`data.story_id`) in the story viewer. The server answers 404
    // once it has expired or the author removed / restricted access, which
    // is shown as "no longer available" instead of an error.
    if (type == 'story_mention') {
      await _openMentionedStory(n);
      return;
    }

    // Task 3.4 — "X reacted to your story": open my story with the owner controls.
    if (type == 'story_reaction') {
      await _openMentionedStory(n, ownStory: true);
      return;
    }

    // post_tag (P5a: data.post_id) was not routed before. post_reposted: data.post_id = the original.
    if (type == 'post_liked' || type == 'post_commented' || type == 'post_tag' || type == 'post_reposted') {
      _openPost(n);
      return;
    }

    if (type.startsWith('testseries') || type == 'certificate_issued') {
      final seriesId = n.data?['series_id']?.toString();
      if (seriesId != null) {
        Navigator.push(context, MaterialPageRoute(builder: (_) => TestSeriesDetailScreen(seriesId: seriesId)));
      }
      return;
    }

    const assignmentTypes = {
      'assigments_graded',
      'assigments_posted',
      'submission_received',
      'assigments_posted_campus',
      'assigments_due_reminder',
      'assigments_due_soon',
      'staff_assigments_approved',
      'staff_assigments_rejected',
    };
    if (assignmentTypes.contains(type)) {
      Navigator.push(context, MaterialPageRoute(builder: (_) => const AssignmentsScreen()));
      return;
    }

    const campusTypes = {
      'notice_posted',
      'campus_session_scheduled',
      'campus_session_live',
      'low_attendance_alert',
      'result_published',
      'fee_due_reminder',
      'campus_reward_earned',
    };
    if (campusTypes.contains(type)) {
      Navigator.push(context, MaterialPageRoute(builder: (_) => const CampusScreen()));
      return;
    }

    const walletTypes = {
      'withdrawal_approved',
      'withdrawal_rejected',
      'withdrawal_paid',
      'pass_refunded',
      'pass_gift_received',
      'pass_gift_claimed',
      'pass_auto_renewed',
      'auto_renew_failed',
      'pass_gift_expired',
    };
    if (walletTypes.contains(type)) {
      Navigator.push(context, MaterialPageRoute(builder: (_) => const WalletScreen()));
      return;
    }
    // N7-FE — unknown / future type: route by whatever the payload carries
    // instead of silently doing nothing (e.g. new_post_from_followed).
    // Still a safe no-op when none of these keys exist.
    if (_openPost(n)) return;
    final fallbackStory = n.data?['story_id']?.toString();
    if (fallbackStory != null && fallbackStory.isNotEmpty) {
      await _openMentionedStory(n);
      return;
    }
    final fallbackUser = n.data?['actor_username']?.toString();
    if (fallbackUser != null && fallbackUser.isNotEmpty) {
      Navigator.push(context, MaterialPageRoute(builder: (_) => TargetProfilePage(username: fallbackUser)));
      return;
    }
    // Baaki types (join-request, waitlist, generic, etc.) — koi dedicated
    // deep-link screen nahi, bas mark-read ho chuka hai, yahin ruk jao.
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(
        context,
        title: 'Notifications',
        actions: [
          TextButton(
            onPressed: _items.any((n) => !n.isRead) ? _onMarkAllRead : null,
            child: const Text('Mark all read'),
          ),
        ],
      ),
      body: _buildBody(scheme),
    );
  }

  Widget _buildBody(ColorScheme scheme) {
    if (_loading) {
      // Task 11 — was a bare CircularProgressIndicator with a TODO
      // pointing here; now uses the shared list-row skeleton (same shape
      // conversations/discovery/leaderboard cold-starts already use), so
      // notifications doesn't look like the one screen that "forgot" the
      // loading-state pass.
      return const LsListSkeleton(count: 8, trailingChip: true);
    }
    if (_hasError && _items.isEmpty) {
      return ErrorStateWidget(
        title: 'Notifications load nahi ho payi',
        // 🔥 FIX — `retryLabel` ErrorStateWidget me `required` hai (Task 11.1),
        // ye call pehle bina isi ke tha isliye "Required named parameter
        // 'retryLabel' must be provided" error aa raha tha.
        retryLabel: 'Retry',
        onRetry: _loadFirstPage,
      );
    }
    if (_items.isEmpty) {
      const empty = EmptyStateWidget(
        title: 'Koi notification nahi hai',
        // retry button jaan-boojh kar nahi — empty list retry se bhi empty hi rahegi.
      );
      // N11-FE — notifications na hon tab bhi pending requests ki row dikhe.
      if (_followRequestCount > 0) {
        return Column(
          children: [
            _buildFollowRequestsRow(scheme),
            Divider(height: 1, color: scheme.outlineVariant),
            const Expanded(child: empty),
          ],
        );
      }
      return empty;
    }

    // N11-FE — N > 0 ho to index 0 pe "Follow requests" row; baaki sab
    // indices `hdr` se shift hote hain (`i = index - hdr`).
    final hdr = _followRequestCount > 0 ? 1 : 0;
    return RefreshIndicator(
      onRefresh: _loadFirstPage,
      child: ListView.separated(
        controller: _scrollController,
        // AlwaysScrollable: kam rows hon tab bhi pull-to-refresh chale.
        physics: const AlwaysScrollableScrollPhysics(),
        itemCount: hdr + _items.length + (_hasMore ? 1 : 0),
        separatorBuilder: (_, __) => Divider(height: 1, color: scheme.outlineVariant),
        itemBuilder: (context, rawIndex) {
          if (hdr == 1 && rawIndex == 0) return _buildFollowRequestsRow(scheme);
          final index = rawIndex - hdr;
          if (index >= _items.length) {
            return const Padding(
              padding: EdgeInsets.all(16),
              child: Center(child: CircularProgressIndicator()),
            );
          }
          final n = _items[index];
          final followAction = _buildFollowAction(n, scheme);
          // N5-FE — row UI (overlapping actor avatars, title/message,
          // post thumbnail, follow quick-action, timeago) now lives in
          // NotificationTile. Follow actions stay here since they need
          // this screen's in-flight/confirmed state.
          final tile = NotificationTile(
            notification: n,
            timeLabel: timeago.format(n.createdAt),
            trailingAction: followAction,
            onTap: () => _onTapNotification(index),
          );
          // N6-FE — left swipe = delete (undo snackbar), long-press = menu.
          return Dismissible(
            key: ValueKey('notif_${n.id}'),
            direction: DismissDirection.endToStart,
            background: Container(
              color: scheme.error,
              alignment: Alignment.centerRight,
              padding: const EdgeInsets.symmetric(horizontal: 20),
              child: Icon(Icons.delete_outline, color: scheme.onError),
            ),
            onDismissed: (_) => _deleteWithUndo(n),
            child: GestureDetector(
              onLongPress: () => _showActionsSheet(n),
              child: tile,
            ),
          );
        },
      ),
    );
  }
}