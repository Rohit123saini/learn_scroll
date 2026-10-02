// ============================================================
// REELS SCREEN — full-screen vertical short-video feed.
//
//   Navigator.push(context, MaterialPageRoute(builder: (_) => ReelsScreen(startPostId: post.id)));
//
// Gestures: tap = pause / play, double-tap = like (heart burst; never un-likes),
// long-press = hold to pause (release resumes), swipe up / down = next / previous.
// Sound: global mute flag (ReelsSoundService, persisted, default sound ON).
//
// * Vertical snapping PageView, one reel per page, cover-fit video on black,
//   overlays kept clear of the status bar / gesture area.
// * [startPostId] -> `GET /post/reels/?start=<id>` (backend puts it first; the
//   start index is looked up in the first page, 0 if it is not there).
// * Player pool (reels_player_pool.dart): previous / current / next only, next is
//   primed via FeedVideoPreloader. Thumbnail + blur-hash until the first frame.
// * Autoplay the page that SETTLES, pause the others, loop. Pauses on app
//   background, when another route / dialog / bottom sheet is pushed on top
//   (needs `appRouteObserver` on MaterialApp.navigatorObservers) and resumes
//   afterwards. wakelock_plus is held while the screen is active.
// * Progress: thin bar + `reportVideoProgress` on page change / dispose (same
//   contract as Home) and `POST /post/feed/seen/` in batches.
// * Pagination: follows `next` verbatim; 404 on a cursor -> fresh session.
//
// P13 - overlays + actions (widgets/reels_overlays.dart):
//   right rail   like (optimistic, rollback) / comment (comment_sheet.dart) / share /
//                save (optimistic, rollback) / more (Not interested, Show fewer like this,
//                Why am I seeing this - feed_feedback_service.dart)
//   bottom       author + Follow (optimistic, rollback), expandable caption, hashtags
//                (-> PostListScreen.hashtag)
//   data         first page: loading / empty / error + retry. Next page is prefetched when
//                3 reels are left; a failed prefetch shows a Retry pill on the last reel.
//   removal      Not interested / Show fewer: the reel slides out, Undo puts it back.
//   entry        ReelsScreen.open(context, startPostId: id)  (Home nav, profile / hashtag /
//                explore video tiles). A start video that Reels cannot show falls back to
//                the normal single-post page.
// ============================================================

import 'dart:async';
import 'dart:math' as math;

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_blurhash/flutter_blurhash.dart';
import 'package:share_plus/share_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:video_player/video_player.dart';
import 'package:visibility_detector/visibility_detector.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../../l10n/app_localizations.dart';
import '../../profile/api_service.dart' as ProfileApi; // followUser (same as Home)
import '../../profile/screens/target_profile.dart';
import '../../services/auth_service.dart';
import '../../services/event_tracker.dart'; // C2-FE — batched impression/dwell analytics
import '../../services/home_api_model_service.dart' show HomeFeedService; // toggleSave (same as Home)
import '../../utils/app_route_observer.dart';
import '../services/api_service.dart' as PostApi; // reportVideoProgress (same as Home)
import '../services/feed_feedback_service.dart';
import '../services/reels_service.dart';
import '../services/reels_sound_service.dart';
import '../widgets/comment_sheet.dart';
import '../widgets/reels_overlays.dart';
import '../widgets/reels_player_pool.dart';
import 'post_list_screen.dart';
import 'singlepost.dart';

class ReelsScreen extends StatefulWidget {
  /// Post id to open first (`?start=`), e.g. from a profile grid or Home.
  final String? startPostId;
  const ReelsScreen({super.key, this.startPostId});

  /// Single entry point: Home nav and the video tiles (profile / hashtag / explore / saved).
  static Future<void> open(BuildContext context, {String? startPostId}) {
    return Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => ReelsScreen(startPostId: startPostId)));
  }

  @override
  State<ReelsScreen> createState() => _ReelsScreenState();
}

class _ReelsScreenState extends State<ReelsScreen> with WidgetsBindingObserver, RouteAware {
  static const int _prefetchAhead = 3; // load the next page when <= 3 reels are left
  static const int _seenFlushAt = 10;

  final List<Reel> _reels = [];
  String? _nextUrl;
  bool _loading = true;
  bool _loadingMore = false;
  bool _failed = false;
  bool _moreFailed = false; // prefetch of the next page failed (Retry pill on the last reel)

  PageController? _pc;
  late final ReelsPlayerPool _pool;
  int _settled = -1;

  bool _appActive = true; // app in foreground
  bool _routeVisible = true; // nothing (route / sheet / dialog) on top of us
  bool _userPaused = false; // tapped to pause
  bool _holdPaused = false; // long-press in progress
  bool _wakelockOn = false;

  final Set<String> _seenPending = {};
  ModalRoute<void>? _route;

  // P13 - action state
  static const Duration _slideOut = Duration(milliseconds: 260);
  final Set<String> _removing = {}; // reels sliding out (Not interested / Show fewer)
  final Set<String> _busyLike = {};
  final Set<String> _busySave = {};
  final Set<String> _busyFollow = {};
  final Set<String> _pendingFollow = {}; // author ids with a follow REQUEST waiting (private account)
  String? _myUserId;
  ScaffoldMessengerState? _messenger;

  final ReelsSoundService _sound = ReelsSoundService.instance;
  late final VoidCallback _onSoundChanged;

  bool get _screenActive => _appActive && _routeVisible;
  bool get _currentRemoving => _settled >= 0 && _settled < _reels.length && _removing.contains(_reels[_settled].id);
  bool get _shouldPlay => _screenActive && !_userPaused && !_holdPaused && !_currentRemoving;

  // ------------------------------------------------------------ lifecycle

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _pool = ReelsPlayerPool(
      urlAt: (i) => (i >= 0 && i < _reels.length) ? _reels[i].video.url : null,
      initialMuted: _sound.isMuted,
    );
    _onSoundChanged = () => _pool.setMuted(_sound.isMuted);
    _sound.muted.addListener(_onSoundChanged);
    AuthService.getUserId().then((id) {
      if (mounted) setState(() => _myUserId = id?.toString());
    }).catchError((_) {});
    _loadFirstPage();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _messenger = ScaffoldMessenger.maybeOf(context);
    final r = ModalRoute.of(context);
    if (r != null && r != _route) {
      if (_route != null) appRouteObserver.unsubscribe(this);
      _route = r;
      appRouteObserver.subscribe(this, r);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    appRouteObserver.unsubscribe(this);
    _messenger?.hideCurrentSnackBar(); // an Undo bar must not outlive the screen
    _sound.muted.removeListener(_onSoundChanged);
    if (_settled >= 0) _reportProgress(_settled); // pool is still alive here
    _flushSeen();
    EventTracker.instance.endSurface(EventSurface.reels); // C2-FE — close open dwell + flush
    _pool.dispose();
    _pc?.dispose();
    if (_wakelockOn) {
      _wakelockOn = false;
      unawaited(WakelockPlus.disable().catchError((_) {}));
    }
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final active = state == AppLifecycleState.resumed;
    if (active == _appActive) return;
    _appActive = active;
    if (!active) {
      if (_settled >= 0) _reportProgress(_settled);
      _flushSeen();
    }
    _applyPlayback();
  }

  // Another route (page / dialog / bottom sheet) was pushed on top of Reels.
  @override
  void didPushNext() {
    _routeVisible = false;
    if (_settled >= 0) _reportProgress(_settled);
    EventTracker.instance.suspendDwell(surface: EventSurface.reels); // C2-FE — covered, not being watched
    _applyPlayback();
  }

  // ...and popped again.
  @override
  void didPopNext() {
    _routeVisible = true;
    EventTracker.instance.resumeDwell(surface: EventSurface.reels); // C2-FE
    _applyPlayback();
  }

  void _applyPlayback() {
    if (!mounted) return;
    _pool.setPlaybackAllowed(_shouldPlay);
    final wantLock = _screenActive && _settled >= 0;
    if (wantLock != _wakelockOn) {
      _wakelockOn = wantLock;
      unawaited((wantLock ? WakelockPlus.enable() : WakelockPlus.disable()).catchError((_) {}));
    }
  }

  // ------------------------------------------------------------ data

  Future<void> _loadFirstPage() async {
    setState(() {
      _loading = true;
      _failed = false;
    });
    try {
      // Stored mute flag must be known BEFORE the first controller is created.
      await _sound.load();
      if (!mounted) return;
      _pool.setMuted(_sound.isMuted);
      var page = await ReelsService.fetchPage(startPostId: widget.startPostId);
      final reels = <Reel>[...page.reels];
      // Every item of a page can be unplayable and dropped; follow `next` a few times.
      var hops = 0;
      while (reels.isEmpty && page.nextUrl != null && hops++ < 3) {
        page = await ReelsService.fetchPage(nextUrl: page.nextUrl);
        reels.addAll(page.reels);
      }
      if (!mounted) return;
      final startId = widget.startPostId;
      var startIndex = startId == null ? 0 : reels.indexWhere((r) => r.id == startId);
      if (startId != null && startIndex < 0) {
        // Backend ignores a start id that is not watchable in Reels -> do not open a
        // different video by surprise; show the post the user tapped instead.
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (!mounted) return;
          Navigator.of(context).pushReplacement(MaterialPageRoute<void>(builder: (_) => SinglePostPage(postId: startId)));
        });
        return;
      }
      if (startIndex < 0) startIndex = 0;
      setState(() {
        _reels
          ..clear()
          ..addAll(reels);
        _nextUrl = page.nextUrl;
        _pc?.dispose();
        _pc = PageController(initialPage: startIndex);
        _loading = false;
      });
      if (reels.isNotEmpty) {
        WidgetsBinding.instance.addPostFrameCallback((_) => _onSettled(startIndex));
      }
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _failed = true;
      });
    }
  }

  Future<void> _loadMore() async {
    final url = _nextUrl;
    if (url == null || _loadingMore) return;
    setState(() {
      _loadingMore = true;
      _moreFailed = false;
    });
    var again = false;
    try {
      ReelsPage page;
      try {
        page = await ReelsService.fetchPage(nextUrl: url);
      } on ReelsException catch (e) {
        if (!e.isInvalidCursor) rethrow;
        page = await ReelsService.fetchPage(); // cursor expired -> new session
      }
      if (!mounted) return;
      final have = _reels.map((r) => r.id).toSet();
      final fresh = page.reels.where((r) => have.add(r.id)).toList();
      final wasAtEnd = _settled >= 0 && _settled == _reels.length - 1;
      final wasEmpty = _reels.isEmpty;
      setState(() {
        if (wasEmpty && fresh.isNotEmpty) {
          _pc?.dispose(); // its PageView is gone (empty state) and it remembers an old initialPage
          _pc = PageController();
        }
        _reels.addAll(fresh);
        _nextUrl = page.nextUrl;
      });
      if (wasAtEnd && fresh.isNotEmpty) _pool.settle(_settled); // prime the new "next"
      if (wasEmpty && fresh.isNotEmpty) _settleAfterRebuild(0);
      // Whole page was duplicates / unplayable: keep going instead of leaving a dead end.
      again = fresh.isEmpty && _nextUrl != null;
    } catch (_) {
      if (mounted) setState(() => _moreFailed = true);
    } finally {
      if (mounted) {
        setState(() => _loadingMore = false);
      } else {
        _loadingMore = false;
      }
    }
    if (again) unawaited(_loadMore());
  }

  // ------------------------------------------------------------ paging

  bool _onScroll(ScrollNotification n) {
    if (n.depth != 0 || n is! ScrollEndNotification) return false;
    final pc = _pc;
    if (pc == null || !pc.hasClients) return false;
    final p = pc.page;
    if (p == null) return false;
    final i = p.round();
    // Microtask: never notify listeners / setState from inside a layout pass.
    if ((p - i).abs() < 0.02) scheduleMicrotask(() => _onSettled(i));
    return false;
  }

  void _onSettled(int i) {
    if (!mounted || i < 0 || i >= _reels.length || i == _settled) return;
    if (_settled >= 0) _reportProgress(_settled); // leaving the previous page
    _settled = i;
    _userPaused = false;
    _holdPaused = false;

    _seenPending.add(_reels[i].id);
    if (_seenPending.length >= _seenFlushAt) _flushSeen();

    _pool.setPlaybackAllowed(_shouldPlay);
    _pool.settle(i);
    _applyPlayback();
    if (mounted) setState(() {});

    if (_nextUrl != null && i >= _reels.length - _prefetchAhead) unawaited(_loadMore());
  }

  // C2-FE — VisibilityDetector feeds impression / dwell / skip to EventTracker.
  // The id is captured here: the callback also fires on dispose, when the list
  // index may no longer exist.
  Widget _trackVisible(String id, Widget child) => VisibilityDetector(
        key: Key('reel_vis_$id'),
        onVisibilityChanged: (info) =>
            EventTracker.instance.onVisibility(id, info.visibleFraction, surface: EventSurface.reels),
        child: child,
      );

  // ------------------------------------------------------------ reporting

  // Same contract as Home (_MediaCarousel._reportProgress): best effort, never
  // interrupts playback, skipped when nothing was watched.
  void _reportProgress(int index) {
    if (index < 0 || index >= _reels.length) return;
    final seconds = _pool.watchedSecondsAt(index);
    if (seconds <= 0) return;
    PostApi.ApiService().reportVideoProgress(_reels[index].id, seconds).catchError((_) {});
  }

  void _flushSeen() {
    if (_seenPending.isEmpty) return;
    final ids = _seenPending.toList();
    _seenPending.clear();
    unawaited(ReelsService.markSeen(ids));
  }

  // ------------------------------------------------------------ UI

  void _toggleUserPause() {
    setState(() => _userPaused = !_userPaused);
    _applyPlayback();
  }

  // Long-press: video is paused only while the finger is down.
  void _onHoldStart() {
    if (_holdPaused) return;
    setState(() => _holdPaused = true);
    _applyPlayback();
  }

  void _onHoldEnd() {
    if (!_holdPaused) return;
    setState(() => _holdPaused = false);
    _applyPlayback();
  }

  // ------------------------------------------------------------ actions (P13)

  int _indexOf(String id) => _reels.indexWhere((r) => r.id == id);

  void _update(String id, Reel Function(Reel r) fn) {
    if (!mounted) return;
    final i = _indexOf(id);
    if (i < 0) return;
    setState(() => _reels[i] = fn(_reels[i]));
  }

  void _snack(String msg, {String? actionLabel, VoidCallback? onAction}) {
    final m = _messenger;
    if (m == null || !mounted) return;
    m
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(msg),
        behavior: SnackBarBehavior.floating,
        duration: const Duration(seconds: 4),
        action: (actionLabel != null && onAction != null) ? SnackBarAction(label: actionLabel, onPressed: onAction) : null,
      ));
  }

  void _snackError() {
    if (mounted) _snack(AppLocalizations.of(context)!.somethingWentWrong);
  }

  // ---- like -------------------------------------------------------------

  bool _isLike(Reel r) => r.myReaction == 'like' || (r.isLiked && r.myReaction == null);

  Reel _withLikeState(Reel r, Reel from) => from.isLiked
      ? r.copyWith(isLiked: true, myReaction: from.myReaction, clearReaction: from.myReaction == null, counts: r.counts.copyWith(likes: from.counts.likes))
      : r.copyWith(isLiked: false, clearReaction: true, counts: r.counts.copyWith(likes: from.counts.likes));

  // Double-tap: like only (like Instagram it never un-likes).
  void _likeReel(int index) {
    if (index < 0 || index >= _reels.length) return;
    _react(_reels[index].id, likeOnly: true);
  }

  // Rail heart: toggles like / unlike.
  void _toggleLike(String id) {
    HapticFeedback.selectionClick();
    _react(id, likeOnly: false);
  }

  // Optimistic; the server answer is authoritative; any failure rolls back to the snapshot.
  Future<void> _react(String id, {required bool likeOnly}) async {
    if (_busyLike.contains(id)) return;
    final i = _indexOf(id);
    if (i < 0) return;
    final before = _reels[i];
    final wasLike = _isLike(before);
    if (likeOnly && wasLike) return;
    _busyLike.add(id);
    _update(
      id,
      (r) => wasLike
          ? r.copyWith(isLiked: false, clearReaction: true, counts: r.counts.copyWith(likes: math.max(0, r.counts.likes - 1)))
          : r.copyWith(isLiked: true, myReaction: 'like', counts: r.counts.copyWith(likes: r.counts.likes + (r.isLiked ? 0 : 1))),
    );
    try {
      final api = PostApi.ApiService();
      var res = await api.toggleReaction(id, 'like');
      // Stale "not liked" copy on a double-tap: server un-liked -> toggle back on once.
      if (likeOnly && res['status'] == 'unliked') res = await api.toggleReaction(id, 'like');
      final counts = res['counts'];
      final likes = (counts is Map) ? ((counts['total'] ?? counts['like']) as num?)?.toInt() : null;
      _update(id, (r) {
        var n = r;
        if (res.containsKey('my_reaction')) {
          final mine = res['my_reaction'];
          n = mine == null ? n.copyWith(isLiked: false, clearReaction: true) : n.copyWith(isLiked: true, myReaction: '$mine');
        }
        if (likes != null) n = n.copyWith(counts: n.counts.copyWith(likes: likes));
        return n;
      });
    } catch (_) {
      _update(id, (r) => _withLikeState(r, before));
      _snackError();
    } finally {
      _busyLike.remove(id);
    }
  }

  // ---- save -------------------------------------------------------------

  Future<void> _toggleSave(String id) async {
    if (_busySave.contains(id)) return;
    final i = _indexOf(id);
    if (i < 0) return;
    HapticFeedback.selectionClick();
    final before = _reels[i];
    final want = !before.isSaved;
    _busySave.add(id);
    _update(id, (r) => r.copyWith(isSaved: want, counts: r.counts.copyWith(saves: math.max(0, r.counts.saves + (want ? 1 : -1)))));
    unawaited(_syncSavedPrefs(id, want));
    try {
      final res = await HomeFeedService.toggleSave(id);
      final saved = (res['is_saved'] is bool) ? res['is_saved'] as bool : res['status'] == 'saved';
      final count = (res['saves_count'] as num?)?.toInt();
      _update(id, (r) => r.copyWith(isSaved: saved, counts: count != null ? r.counts.copyWith(saves: count) : r.counts));
      if (saved != want) unawaited(_syncSavedPrefs(id, saved));
    } catch (_) {
      _update(id, (r) => r.copyWith(isSaved: before.isSaved, counts: r.counts.copyWith(saves: before.counts.saves)));
      unawaited(_syncSavedPrefs(id, before.isSaved));
      if (mounted) _snack(AppLocalizations.of(context)!.saveFailed);
    } finally {
      _busySave.remove(id);
    }
  }

  // Home keeps a local copy of the saved ids (`saved_posts`); keep it in step.
  Future<void> _syncSavedPrefs(String id, bool saved) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final ids = (prefs.getStringList('saved_posts') ?? const <String>[]).toSet();
      if (saved) {
        ids.add(id);
      } else {
        ids.remove(id);
      }
      await prefs.setStringList('saved_posts', ids.toList());
    } catch (_) {}
  }

  // ---- follow -----------------------------------------------------------

  void _setAuthorFollow(String authorId, bool following) {
    if (!mounted) return;
    setState(() {
      for (var i = 0; i < _reels.length; i++) {
        final r = _reels[i];
        if (r.author.id == authorId) _reels[i] = r.copyWith(author: r.author.copyWith(isFollowing: following));
      }
    });
  }

  // Same shape as Home's _toggleFollowFromFeed: flip at once, reconcile with the server, roll back on error.
  Future<void> _toggleFollow(String authorId) async {
    final uid = int.tryParse(authorId);
    if (uid == null || authorId == _myUserId || _busyFollow.contains(authorId)) return;
    final i = _reels.indexWhere((r) => r.author.id == authorId);
    if (i < 0) return;
    HapticFeedback.selectionClick();
    final wasFollowing = _reels[i].author.isFollowing;
    final wasPending = _pendingFollow.contains(authorId);
    _busyFollow.add(authorId);
    setState(() => _pendingFollow.remove(authorId));
    _setAuthorFollow(authorId, wasPending ? false : !wasFollowing);
    try {
      final res = await ProfileApi.ApiService.followUser(uid);
      final status = res['status']?.toString();
      if (!mounted) return;
      setState(() {
        if (status == 'PENDING') _pendingFollow.add(authorId);
      });
      _setAuthorFollow(authorId, status == 'ACCEPTED');
    } catch (_) {
      if (!mounted) return;
      setState(() {
        if (wasPending) _pendingFollow.add(authorId);
      });
      _setAuthorFollow(authorId, wasFollowing);
      _snackError();
    } finally {
      _busyFollow.remove(authorId);
    }
  }

  // ---- comment / share / navigation -------------------------------------

  void _bumpComments(String id) => _update(id, (r) => r.copyWith(counts: r.counts.copyWith(comments: r.counts.comments + 1)));

  // Same sheet as Home. It is a route on top of Reels, so playback pauses until it closes.
  Future<void> _openComments(Reel reel) async {
    final cs = Theme.of(context).colorScheme;
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (sheetCtx) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.of(sheetCtx).viewInsets.bottom),
        child: Container(
          decoration: BoxDecoration(color: cs.surface, borderRadius: const BorderRadius.vertical(top: Radius.circular(20))),
          child: CommentBottomSheet(
            postId: reel.id,
            postOwnerId: reel.author.id,
            initialCommentsCount: reel.counts.comments,
            onCommentAdded: () => _bumpComments(reel.id),
            onGoToProfile: _openProfile,
          ),
        ),
      ),
    );
  }

  void _share(Reel reel) {
    HapticFeedback.selectionClick();
    final text = [reel.caption.trim(), reel.video.url].where((s) => s.isNotEmpty).join('\n\n');
    Share.share(text);
  }

  void _openProfile(String username) {
    if (username.trim().isEmpty || !mounted) return;
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => TargetProfilePage(username: username)));
  }

  void _openAuthor(Reel reel) {
    if (reel.author.id == _myUserId) return; // own reel: the Profile tab is one tap away
    _openProfile(reel.author.username);
  }

  void _openHashtag(String tag) {
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => PostListScreen.hashtag(tag)));
  }

  // ---- more menu: Not interested / Show fewer / Why ----------------------

  Future<void> _openMore(Reel reel) async {
    final isMine = reel.author.id == _myUserId;
    final action = await showReelMoreSheet(context, canHide: !isMine);
    if (!mounted || action == null) return;
    switch (action) {
      case ReelMoreAction.notInterested:
        return _hideReel(reel, fewer: false);
      case ReelMoreAction.showFewer:
        return _hideReel(reel, fewer: true);
      case ReelMoreAction.why:
        return showReelWhySheet(context, reel.id);
    }
  }

  Future<void> _hideReel(Reel reel, {required bool fewer}) async {
    final idx = _indexOf(reel.id);
    if (idx < 0 || _removing.contains(reel.id)) return;
    final l10n = AppLocalizations.of(context)!;
    _startRemoval(reel.id);
    ShowFewerResult? result;
    try {
      if (fewer) {
        final tags = reel.hashtags.where((t) => t.isNotEmpty).take(2);
        result = await FeedFeedbackService.showFewer(reel.id, [
          const ShowFewerTarget.category(),
          for (final t in tags) ShowFewerTarget.hashtag(t),
        ]);
      } else {
        await FeedFeedbackService.notInterested(reel.id);
      }
    } catch (_) {
      if (!mounted) return;
      _abortRemoval(reel, idx);
      _snack(l10n.somethingWentWrong);
      return;
    }
    if (!mounted) return;
    _snack(
      fewer ? l10n.reelFewerDone : l10n.reelHidden,
      actionLabel: l10n.undoLabel,
      onAction: () => _undoHide(reel, idx, result),
    );
  }

  Future<void> _undoHide(Reel reel, int idx, ShowFewerResult? result) async {
    if (!mounted) return;
    _restoreReel(reel, idx);
    try {
      await FeedFeedbackService.undoNotInterested(reel.id);
      for (final f in result?.feedback ?? const <FeedFeedbackItem>[]) {
        try {
          await FeedFeedbackService.removeFeedback(f.id);
        } catch (_) {}
      }
    } catch (_) {
      // Server still has it hidden: take it out of the list again instead of lying about the state.
      if (!mounted) return;
      _startRemoval(reel.id);
      _snackError();
    }
  }

  // The reel slides out first, then leaves the list (and the player pool).
  void _startRemoval(String id) {
    if (!mounted || _removing.contains(id)) return;
    setState(() => _removing.add(id));
    _applyPlayback(); // stop the video that is sliding away
    Future<void>.delayed(_slideOut, () => _finishRemoval(id));
  }

  // The request failed: if the slide-out is still running it simply slides back, else re-insert.
  void _abortRemoval(Reel reel, int idx) {
    if (_removing.remove(reel.id)) {
      setState(() {});
      _applyPlayback();
    } else {
      _restoreReel(reel, idx);
    }
  }

  void _finishRemoval(String id) {
    if (!mounted || !_removing.contains(id)) return;
    final idx = _indexOf(id);
    if (idx < 0) {
      _removing.remove(id);
      return;
    }
    if (idx == _settled) _reportProgress(idx);
    final wasSettled = idx == _settled;
    _pool.dropFrom(idx); // controllers at / after idx now belong to other reels
    setState(() {
      _removing.remove(id);
      _reels.removeAt(idx);
    });
    if (_reels.isEmpty) {
      _settled = -1;
      _applyPlayback();
      if (_nextUrl != null) unawaited(_loadMore());
      return;
    }
    if (wasSettled || _settled > idx) {
      // The page that is now at [target] (the next reel, or the previous one when the last was removed) plays.
      final target = math.min(wasSettled ? idx : _settled - 1, _reels.length - 1);
      _settleAfterRebuild(target);
    } else if (_settled >= 0) {
      _pool.settle(_settled); // re-prime the "next" video
    }
  }

  // Undo: put the reel back where it was and jump to it.
  void _restoreReel(Reel reel, int idx) {
    if (!mounted || _indexOf(reel.id) >= 0) return;
    if (_settled >= 0) _reportProgress(_settled);
    final at = idx.clamp(0, _reels.length);
    _pool.dropFrom(at);
    final hadPages = _reels.isNotEmpty && (_pc?.hasClients ?? false);
    setState(() {
      _reels.insert(at, reel);
      if (!hadPages) {
        _pc?.dispose();
        _pc = PageController(initialPage: at);
      }
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final pc = _pc;
      if (hadPages && pc != null && pc.hasClients) pc.jumpToPage(at);
      _settled = -1;
      _onSettled(at);
    });
  }

  // After the list changed: wait for the PageView to lay out, then treat [index] as freshly settled.
  void _settleAfterRebuild(int index) {
    _settled = -1;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || _reels.isEmpty) return;
      _onSettled(index.clamp(0, _reels.length - 1));
    });
  }

  ReelActions _actionsFor(Reel reel) => ReelActions(
        onLike: () => _toggleLike(reel.id),
        onComment: () => _openComments(reel),
        onShare: () => _share(reel),
        onSave: () => _toggleSave(reel.id),
        onMore: () => _openMore(reel),
        onAuthorTap: () => _openAuthor(reel),
        onFollow: () => _toggleFollow(reel.author.id),
        onHashtag: _openHashtag,
      );

  @override
  Widget build(BuildContext context) {
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.light.copyWith(
        statusBarColor: Colors.transparent,
        systemNavigationBarColor: Colors.black,
        systemNavigationBarIconBrightness: Brightness.light,
      ),
      child: Scaffold(
        backgroundColor: Colors.black,
        body: _buildBody(context),
      ),
    );
  }

  Widget _buildBody(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;

    if (_loading) {
      return Stack(fit: StackFit.expand, children: [
        const Center(child: SizedBox(width: 28, height: 28, child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white70))),
        const _TopBar(),
      ]);
    }
    if (_pc != null && _reels.isEmpty && _loadingMore) {
      return Stack(fit: StackFit.expand, children: [
        const Center(child: SizedBox(width: 28, height: 28, child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white70))),
        const _TopBar(),
      ]);
    }
    if (_failed || _pc == null || _reels.isEmpty) {
      return Stack(fit: StackFit.expand, children: [
        Center(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(_failed ? l10n.somethingWentWrong : l10n.reelsEmpty, style: const TextStyle(color: Colors.white70, fontSize: 15)),
            if (_failed) ...[
              const SizedBox(height: 12),
              OutlinedButton(
                onPressed: _loadFirstPage,
                style: OutlinedButton.styleFrom(foregroundColor: Colors.white, side: const BorderSide(color: Colors.white54)),
                child: Text(l10n.retry),
              ),
            ],
          ]),
        ),
        const _TopBar(),
      ]);
    }

    return Stack(children: [
      NotificationListener<ScrollNotification>(
        onNotification: _onScroll,
        child: PageView.builder(
          controller: _pc,
          scrollDirection: Axis.vertical,
          pageSnapping: true,
          physics: const PageScrollPhysics(),
          itemCount: _reels.length,
          itemBuilder: (context, i) => _trackVisible(
            _reels[i].id,
            _ReelPage(
            key: ValueKey(_reels[i].id),
            reel: _reels[i],
            index: i,
            pool: _pool,
            showPausedIcon: _userPaused && i == _settled,
            holding: _holdPaused && i == _settled,
            actions: _actionsFor(_reels[i]),
            showFollow: _myUserId == null || _reels[i].author.id != _myUserId,
            followPending: _pendingFollow.contains(_reels[i].author.id),
            removing: _removing.contains(_reels[i].id),
            onTap: _toggleUserPause,
            onDoubleTapLike: () => _likeReel(i),
            onHoldStart: _onHoldStart,
            onHoldEnd: _onHoldEnd,
          ),
          ),
        ),
      ),
      // Next page is loading / failed while the user is on the last reel.
      if (_settled >= 0 && _settled == _reels.length - 1 && (_loadingMore || _moreFailed))
        Positioned(
          top: MediaQuery.of(context).padding.top + 60,
          left: 0,
          right: 0,
          child: Center(child: _MoreStatusPill(loading: _loadingMore, onRetry: _loadMore)),
        ),
      _TopBar(
        trailing: ValueListenableBuilder<bool>(
          valueListenable: _sound.muted,
          builder: (context, muted, _) => _RoundIconButton(
            icon: muted ? Icons.volume_off_rounded : Icons.volume_up_rounded,
            onTap: () => _sound.toggle(),
          ),
        ),
      ),
    ]);
  }
}

// ============================================================ overlays

class _TopBar extends StatelessWidget {
  final Widget? trailing;
  const _TopBar({this.trailing});

  @override
  Widget build(BuildContext context) {
    return Positioned(
      top: 0,
      left: 0,
      right: 0,
      child: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          child: Row(children: [
            _RoundIconButton(icon: Icons.arrow_back_rounded, onTap: () => Navigator.maybePop(context)),
            const Spacer(),
            if (trailing != null) trailing!,
          ]),
        ),
      ),
    );
  }
}

class _RoundIconButton extends StatelessWidget {
  final IconData icon;
  final VoidCallback onTap;
  const _RoundIconButton({required this.icon, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.black38,
      shape: const CircleBorder(),
      child: InkWell(
        customBorder: const CircleBorder(),
        onTap: onTap,
        child: Padding(padding: const EdgeInsets.all(8), child: Icon(icon, color: Colors.white, size: 24)),
      ),
    );
  }
}

// ============================================================ one page

class _ReelPage extends StatefulWidget {
  final Reel reel;
  final int index;
  final ReelsPlayerPool pool;
  final bool showPausedIcon;
  final bool holding;
  final ReelActions actions;
  final bool showFollow;
  final bool followPending;
  final bool removing;
  final VoidCallback onTap;
  final VoidCallback onDoubleTapLike;
  final VoidCallback onHoldStart;
  final VoidCallback onHoldEnd;

  const _ReelPage({
    super.key,
    required this.reel,
    required this.index,
    required this.pool,
    required this.showPausedIcon,
    required this.holding,
    required this.actions,
    required this.showFollow,
    required this.followPending,
    required this.removing,
    required this.onTap,
    required this.onDoubleTapLike,
    required this.onHoldStart,
    required this.onHoldEnd,
  });

  @override
  State<_ReelPage> createState() => _ReelPageState();
}

class _Burst {
  final int id;
  final Offset at;
  final double angle;
  const _Burst(this.id, this.at, this.angle);
}

class _ReelPageState extends State<_ReelPage> {
  final List<_Burst> _bursts = [];
  final math.Random _rnd = math.Random();
  int _burstSeq = 0;
  Offset _lastTapDown = Offset.zero;

  void _addBurst() {
    final size = MediaQuery.of(context).size;
    final at = _lastTapDown == Offset.zero ? Offset(size.width / 2, size.height / 2) : _lastTapDown;
    setState(() => _bursts.add(_Burst(_burstSeq++, at, (_rnd.nextDouble() - 0.5) * 0.5)));
  }

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.of(context).padding.bottom;
    final reel = widget.reel;
    final pool = widget.pool;
    final index = widget.index;
    return ListenableBuilder(
      listenable: pool,
      builder: (context, _) {
        final c = pool.controllerAt(index);
        final isCurrent = index == pool.currentIndex;
        final page = GestureDetector(
          behavior: HitTestBehavior.opaque,
          // tap = pause / play (waits ~300ms to tell it apart from a double-tap)
          onTap: isCurrent ? widget.onTap : null,
          // double-tap = like + heart burst at the finger
          onDoubleTapDown: isCurrent ? (d) => _lastTapDown = d.localPosition : null,
          onDoubleTap: isCurrent
              ? () {
                  HapticFeedback.lightImpact();
                  _addBurst();
                  widget.onDoubleTapLike();
                }
              : null,
          // long-press = hold to pause, release to resume
          onLongPressStart: isCurrent ? (_) => widget.onHoldStart() : null,
          onLongPressEnd: isCurrent ? (_) => widget.onHoldEnd() : null,
          onLongPressCancel: isCurrent ? widget.onHoldEnd : null,
          // swipe up / down is the PageView's own vertical drag (next / previous)
          child: Stack(fit: StackFit.expand, children: [
            _ReelVideoLayer(
              video: reel.video,
              controller: c,
              isCurrent: isCurrent,
              failed: pool.hasFailed(index),
              onRetry: () => pool.retry(index),
            ),
            // Bottom scrim so the caption stays readable on bright videos.
            const IgnorePointer(
              child: Align(
                alignment: Alignment.bottomCenter,
                child: SizedBox(
                  height: 320,
                  width: double.infinity,
                  child: DecoratedBox(
                    decoration: BoxDecoration(
                      gradient: LinearGradient(begin: Alignment.bottomCenter, end: Alignment.topCenter, colors: [Colors.black54, Colors.transparent]),
                    ),
                  ),
                ),
              ),
            ),
            // Bottom: author + Follow, expandable caption, hashtags (interactive).
            Positioned(
              left: 16,
              right: 84,
              bottom: bottomInset + 18,
              child: IgnorePointer(
                ignoring: widget.holding,
                child: AnimatedOpacity(
                  opacity: widget.holding ? 0 : 1, // clean view while holding
                  duration: const Duration(milliseconds: 150),
                  child: ReelInfoOverlay(
                    reel: reel,
                    showFollow: widget.showFollow,
                    followPending: widget.followPending,
                    actions: widget.actions,
                  ),
                ),
              ),
            ),
            // Right rail: like, comment, share, save, more.
            Positioned(
              right: 8,
              bottom: bottomInset + 18,
              child: IgnorePointer(
                ignoring: widget.holding,
                child: AnimatedOpacity(
                  opacity: widget.holding ? 0 : 1,
                  duration: const Duration(milliseconds: 150),
                  child: ReelActionRail(reel: reel, actions: widget.actions),
                ),
              ),
            ),
            if (widget.showPausedIcon)
              const IgnorePointer(child: Center(child: Icon(Icons.play_arrow_rounded, size: 84, color: Colors.white70))),
            if (isCurrent && c != null && c.value.isInitialized)
              Positioned(left: 0, right: 0, bottom: bottomInset, child: _ProgressBar(controller: c)),
            for (final b in _bursts)
              _HeartBurst(
                key: ValueKey(b.id),
                at: b.at,
                angle: b.angle,
                onDone: () {
                  if (mounted) setState(() => _bursts.removeWhere((x) => x.id == b.id));
                },
              ),
          ]),
        );
        // Not interested / Show fewer: slide up + fade, then the screen drops the reel.
        return IgnorePointer(
          ignoring: widget.removing,
          child: AnimatedSlide(
            offset: widget.removing ? const Offset(0, -1) : Offset.zero,
            duration: const Duration(milliseconds: 260),
            curve: Curves.easeInCubic,
            child: AnimatedOpacity(
              opacity: widget.removing ? 0 : 1,
              duration: const Duration(milliseconds: 260),
              child: page,
            ),
          ),
        );
      },
    );
  }
}

/// "Loading more" / "Retry" pill shown under the top bar while the user is on the last reel.
class _MoreStatusPill extends StatelessWidget {
  final bool loading;
  final VoidCallback onRetry;
  const _MoreStatusPill({required this.loading, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Material(
      color: Colors.black54,
      shape: const StadiumBorder(),
      child: InkWell(
        customBorder: const StadiumBorder(),
        onTap: loading ? null : onRetry,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          child: loading
              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white70))
              : Row(mainAxisSize: MainAxisSize.min, children: [
                  const Icon(Icons.refresh_rounded, color: Colors.white, size: 18),
                  const SizedBox(width: 6),
                  Text(l10n.retry, style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600)),
                ]),
        ),
      ),
    );
  }
}

/// One heart that pops, floats up and fades out (~0.85s), then calls [onDone].
class _HeartBurst extends StatelessWidget {
  final Offset at;
  final double angle;
  final VoidCallback onDone;
  const _HeartBurst({super.key, required this.at, required this.angle, required this.onDone});

  static const double _size = 96;

  @override
  Widget build(BuildContext context) {
    // Positioned must sit directly under the Stack (only non-render widgets above it).
    return TweenAnimationBuilder<double>(
      tween: Tween<double>(begin: 0, end: 1),
      duration: const Duration(milliseconds: 850),
      onEnd: onDone,
      builder: (context, t, _) {
        final pop = Curves.easeOutBack.transform((t / 0.3).clamp(0.0, 1.0));
        final scale = 0.4 + 0.8 * pop;
        final opacity = t < 0.6 ? 1.0 : (1 - (t - 0.6) / 0.4).clamp(0.0, 1.0);
        final rise = 70 * Curves.easeOut.transform(t);
        return Positioned(
          left: at.dx - _size / 2,
          top: at.dy - _size / 2 - rise,
          child: IgnorePointer(
            child: Opacity(
              opacity: opacity,
              child: Transform.rotate(
                angle: angle,
                child: Transform.scale(
                  scale: scale,
                  child: const Icon(
                    Icons.favorite_rounded,
                    size: _size,
                    color: Color(0xFFFF3B5C),
                    shadows: [Shadow(blurRadius: 18, color: Colors.black38)],
                  ),
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}

class _ProgressBar extends StatelessWidget {
  final VideoPlayerController controller;
  const _ProgressBar({required this.controller});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<VideoPlayerValue>(
      valueListenable: controller,
      builder: (context, v, _) {
        final total = v.duration.inMilliseconds;
        final f = total > 0 ? (v.position.inMilliseconds / total).clamp(0.0, 1.0) : 0.0;
        return IgnorePointer(
          child: LinearProgressIndicator(
            value: f,
            minHeight: 2,
            backgroundColor: Colors.white24,
            valueColor: const AlwaysStoppedAnimation<Color>(Colors.white),
          ),
        );
      },
    );
  }
}

// ============================================================ video layer

/// Cover-fit video with a blur-hash + thumbnail placeholder that stays until the
/// first frame is on screen.
class _ReelVideoLayer extends StatefulWidget {
  final ReelVideo video;
  final VideoPlayerController? controller;
  final bool isCurrent;
  final bool failed;
  final VoidCallback onRetry;

  const _ReelVideoLayer({
    required this.video,
    required this.controller,
    required this.isCurrent,
    required this.failed,
    required this.onRetry,
  });

  @override
  State<_ReelVideoLayer> createState() => _ReelVideoLayerState();
}

class _ReelVideoLayerState extends State<_ReelVideoLayer> {
  VideoPlayerController? _bound;
  bool _shown = false; // first frame has been shown -> placeholder can go
  Timer? _fallback;

  @override
  void initState() {
    super.initState();
    _bind(widget.controller);
  }

  @override
  void didUpdateWidget(covariant _ReelVideoLayer old) {
    super.didUpdateWidget(old);
    if (widget.controller != _bound) {
      _bind(widget.controller);
    } else {
      _check(notify: false); // build() runs right after this
    }
  }

  @override
  void dispose() {
    _fallback?.cancel();
    _bound?.removeListener(_check);
    super.dispose();
  }

  void _bind(VideoPlayerController? c) {
    _bound?.removeListener(_check);
    _fallback?.cancel();
    _fallback = null;
    _bound = c;
    _shown = false;
    c?.addListener(_check);
    _check(notify: false); // called from initState / didUpdateWidget -> build() follows
  }

  void _check({bool notify = true}) {
    final c = _bound;
    if (_shown || c == null) return;
    final v = c.value;
    if (!v.isInitialized) return;
    if (v.position > Duration.zero) {
      _reveal(notify: notify);
    } else if (widget.isCurrent && v.isPlaying) {
      // Some platforms report position late; never keep the placeholder forever.
      _fallback ??= Timer(const Duration(milliseconds: 500), () => _reveal());
    }
  }

  void _reveal({bool notify = true}) {
    _fallback?.cancel();
    _fallback = null;
    if (_shown) return;
    if (!notify) {
      _shown = true;
    } else if (mounted) {
      setState(() => _shown = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final ready = c != null && c.value.isInitialized && c.value.size.width > 0 && c.value.size.height > 0;
    final size = (c != null && ready) ? c.value.size : Size.zero;
    return Stack(fit: StackFit.expand, children: [
      const ColoredBox(color: Colors.black),
      if (ready)
        ClipRect(
          child: FittedBox(
            fit: BoxFit.cover,
            child: SizedBox(width: size.width, height: size.height, child: VideoPlayer(c!)),
          ),
        ),
      IgnorePointer(
        child: AnimatedOpacity(
          opacity: (_shown && ready) ? 0 : 1,
          duration: const Duration(milliseconds: 180),
          child: _Placeholder(video: widget.video),
        ),
      ),
      if (!widget.failed && widget.isCurrent && !(_shown && ready))
        const Center(child: SizedBox(width: 26, height: 26, child: CircularProgressIndicator(strokeWidth: 2.2, color: Colors.white70))),
      if (widget.failed)
        Center(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.error_outline_rounded, color: Colors.white70, size: 40),
            const SizedBox(height: 8),
            OutlinedButton(
              onPressed: widget.onRetry,
              style: OutlinedButton.styleFrom(foregroundColor: Colors.white, side: const BorderSide(color: Colors.white54)),
              child: Text(AppLocalizations.of(context)!.retry),
            ),
          ]),
        ),
    ]);
  }
}

class _Placeholder extends StatelessWidget {
  final ReelVideo video;
  const _Placeholder({required this.video});

  @override
  Widget build(BuildContext context) {
    final hash = video.blurHash;
    final thumb = video.thumbnail;
    return Stack(fit: StackFit.expand, children: [
      const ColoredBox(color: Colors.black),
      if (hash != null && hash.isNotEmpty)
        BlurHash(
          hash: hash,
          imageFit: BoxFit.cover,
          duration: const Duration(milliseconds: 150),
          errorBuilder: (_, __, ___) => const SizedBox.shrink(),
        ),
      if (thumb != null && thumb.isNotEmpty)
        CachedNetworkImage(
          imageUrl: thumb,
          fit: BoxFit.cover,
          fadeInDuration: Duration.zero,
          errorWidget: (_, __, ___) => const SizedBox.shrink(),
        ),
    ]);
  }
}