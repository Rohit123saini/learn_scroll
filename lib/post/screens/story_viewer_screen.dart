// lib/post/screens/story_viewer_screen.dart
//
// Task 4 — full-screen story viewer. Opens on ONE user's StoryGroup (all
// their active, non-expired stories — home.dart's grouping already
// filtered to one ring = one user). Tap-right/left to advance/go-back
// (Instagram convention), auto-advances images on a timer, holds on video
// until it finishes. Each story is marked viewed via StoryService as soon
// as it's shown (deduped server-side, safe to call every time).
//
// Video playback reuses the same VideoPlayerController pattern already
// used elsewhere in home.dart's post full-screen viewer, so behavior
// (looping off here — a story should end and advance, not loop) stays
// consistent with the rest of the app.
//
// Task 11 additions on top of the above:
//   - pauses on AppLifecycleState.paused/inactive/hidden (app backgrounded),
//     not just on long-press-hold, and resumes only if the pause was ours
//   - prefetches the next story's media (image precache / video
//     initialize-ahead) while the current one is showing
//   - swipe-down-to-dismiss, alongside the existing X button
//   - own-story "N viewers" — tappable, opens a sheet listing who viewed
//     (backend endpoint for this is flagged unconfirmed — see
//     StoryService.getStoryViewers())
//
// UI/UX PASS — the viewer itself stays a full-bleed black, immersive
// surface (that's the right pattern here regardless of the app's
// light/dark setting — same reasoning as the other full-screen media
// viewers in singlepost.dart/comment_sheet.dart). What changed: a
// frosted close button and viewers pill matching the rest of the app's
// glass-chrome language, the LearnScroll brand purple on the viewers
// sheet, and every user-facing string (viewer/viewers count, the
// viewers sheet's title/empty/error states) is now localized via
// AppLocalizations instead of hardcoded English.
//
// STORIES UPGRADE, PART 2 — stickers. A story can carry overlays (mention,
// link, poll, question) drawn on a 9:16 canvas over the media
// (widgets/story_sticker_widgets.dart, same renderer the composer uses):
//   - mention  -> opens that user's profile
//   - link     -> asks "Open this link?" then hands it to the browser
//   - poll     -> viewer votes once (results appear after voting); the owner
//                 always sees results and taps for the "who voted what" list
//   - question -> viewer answers in a sheet (one answer); the owner taps for
//                 the answers list
// Playback pauses while any of those sheets / dialogs / pages is open. The
// outer tap zones use onTapUp (not onTapDown) so tapping a sticker never also
// advances the story.

import 'dart:async';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:video_player/video_player.dart';
import 'package:url_launcher/url_launcher.dart';
import '../models/story_model.dart'
    show StoryGroup, StoryModel, StoryViewerEntry, StorySticker, StickerResponses, StickerResponseRow, kStoryAnswerMax;
import '../services/story_service.dart';
import '../widgets/story_sticker_widgets.dart';
import '../widgets/add_to_highlight_sheet.dart'; // P2-FE
import '../widgets/highlight_editor_screen.dart'; // P2-FE
import '../models/highlight_model.dart'; // P2-FE
import '../../l10n/app_localizations.dart';
import '../../profile/widgets/block_report.dart'; // Block a viewer from the viewers sheet
import '../../profile/screens/target_profile.dart';

const Duration _kImageStoryDuration = Duration(seconds: 5);
// Drag distance (px) past which releasing dismisses instead of snapping back.
const double _kDismissDragThreshold = 120.0;
// Fast-flick fallback — a quick short downward flick should dismiss even
// if it didn't travel _kDismissDragThreshold yet.
const double _kDismissVelocityThreshold = 800.0;
const double _kMaxDragOffset = 400.0;
const Color _kStoryAccent = Color(0xFF8B7CFF); // LearnScroll brand purple

class StoryViewerScreen extends StatefulWidget {
  /// All groups currently in the row — lets the viewer swipe from one
  /// user's stories straight into the next user's, same as Instagram,
  /// instead of dead-ending back to the home row after each user.
  final List<StoryGroup> groups;
  final int initialGroupIndex;

  /// The signed-in user's id. When the group currently showing belongs to
  /// this user, the overlay shows a tappable "N viewers" instead of
  /// nothing — Instagram only ever shows the viewer list on your own
  /// story. Null (not signed in / not passed) just hides that row.
  final String? myUserId;

  /// P2-FE — non-null = playing a HIGHLIGHT (single group, past-24h stories).
  /// Read-only: the backend answers 404 to view/react/reply/vote/answer for
  /// these (post/highlights.py), so none of those are called; the owner gets
  /// an "Edit" button instead of the viewers row. Pops `true` if the owner
  /// edited/deleted it so the caller can refresh.
  final Highlight? highlight;

  const StoryViewerScreen({super.key, required this.groups, required this.initialGroupIndex, this.myUserId, this.highlight});

  @override
  State<StoryViewerScreen> createState() => _StoryViewerScreenState();
}

class _StoryViewerScreenState extends State<StoryViewerScreen> with TickerProviderStateMixin, WidgetsBindingObserver {
  late final PageController _groupController;
  late int _groupIndex;
  int _storyIndex = 0;
  AnimationController? _progressController;
  VideoPlayerController? _videoController;

  // Two independent reasons playback can be paused — kept separate so an
  // app-background/foreground cycle can't accidentally cancel a long-press
  // hold (or vice versa): resuming only un-pauses the reason that caused it.
  bool _userPaused = false; // long-press hold or an in-progress swipe-down drag
  bool _appPaused = false; // AppLifecycleState.paused/inactive/hidden
  bool get _isPaused => _userPaused || _appPaused;

  // Swipe-down-to-dismiss state.
  double _dragOffset = 0;

  // Next-story preload state — at most one in-flight video preload at a
  // time, keyed by story id so `_loadStory` can tell whether the
  // controller it finds here is actually for the story it's about to show.
  VideoPlayerController? _preloadedVideoController;
  String? _preloadedVideoId;

  StoryGroup get _group => widget.groups[_groupIndex];
  StoryModel get _story => _group.stories[_storyIndex];
  bool get _isOwnGroup => widget.myUserId != null && _group.userId == widget.myUserId;
  bool get _readOnly => widget.highlight != null; // P2-FE

  // Story reactions/replies — Instagram-style quick-reaction row + text
  // reply at the bottom (own stories don't get this: you can't react to
  // or reply to yourself, same as the existing `_isOwnGroup` gate on
  // `_buildViewersRow()` below).
  final _replyController = TextEditingController();
  final _replyFocus = FocusNode();
  bool _sendingReply = false;
  // Which emoji (if any) is mid-pop-animation — purely cosmetic feedback,
  // not persisted client-side; the server is the source of truth for
  // whether a reaction landed (`StoryService.reactToStory`'s return value).
  String? _poppingEmoji;

  // Sticker ids with a vote / answer request in flight (buttons dim, no double taps).
  final Set<String> _stickerBusy = {};

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
    _groupIndex = widget.initialGroupIndex;
    _groupController = PageController(initialPage: _groupIndex);
    // Typing a reply shouldn't let the story auto-advance underneath the
    // keyboard — same pause/resume pattern as `_openViewersSheet` below.
    _replyFocus.addListener(() => _togglePause(_replyFocus.hasFocus));
    _loadStory();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
    _progressController?.dispose();
    _videoController?.dispose();
    _preloadedVideoController?.dispose();
    _groupController.dispose();
    _replyController.dispose();
    _replyFocus.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    switch (state) {
      case AppLifecycleState.paused:
      case AppLifecycleState.inactive:
      case AppLifecycleState.hidden:
        if (!_appPaused) {
          _appPaused = true;
          _applyPlaybackState();
        }
        break;
      case AppLifecycleState.resumed:
        if (_appPaused) {
          _appPaused = false;
          _applyPlaybackState();
        }
        break;
      case AppLifecycleState.detached:
        break;
    }
  }

  /// Single place that actually starts/stops the timer + video based on
  /// the combined `_isPaused` flag, so lifecycle changes and user-gesture
  /// changes can't fight over who's driving playback.
  void _applyPlaybackState() {
    if (_isPaused) {
      _progressController?.stop();
      _videoController?.pause();
    } else {
      _progressController?.forward();
      _videoController?.play();
    }
  }

  void _loadStory() {
    _progressController?.dispose();
    _videoController?.dispose();
    _videoController = null;

    // Fire-and-forget — deduped server-side, failure shouldn't block viewing.
    if (!_readOnly) StoryService.markViewed(_story.id).catchError((_) => 0); // highlight stories: 404 by design

    final url = _story.mediaUrl;
    if (_story.mediaType == 'video' && (url ?? '').isNotEmpty) {
      VideoPlayerController c;
      final reusingPreload = _preloadedVideoId == _story.id && _preloadedVideoController != null;
      if (reusingPreload) {
        c = _preloadedVideoController!;
        _preloadedVideoController = null;
        _preloadedVideoId = null;
      } else {
        c = VideoPlayerController.networkUrl(Uri.parse(url!));
      }
      _videoController = c;

      void startPlayback() {
        if (!mounted) return;
        setState(() {});
        _progressController = AnimationController(vsync: this, duration: c.value.duration)
          ..addStatusListener((s) {
            if (s == AnimationStatus.completed) _advance();
          });
        _applyPlaybackState(); // respects an already-held long-press/drag or a backgrounded app
        _preloadNext();
      }

      if (c.value.isInitialized) {
        startPlayback();
      } else {
        c.initialize().then((_) => startPlayback());
      }
    } else {
      _progressController = AnimationController(vsync: this, duration: _kImageStoryDuration)
        ..addStatusListener((s) {
          if (s == AnimationStatus.completed) _advance();
        });
      _applyPlaybackState();
      _preloadNext();
    }
    setState(() {});
  }

  /// Task 11 — prefetches whatever story would be shown next (next story
  /// in this group, or the next group's first story) so advancing to it
  /// never has to wait on a cold network fetch. Images go through
  /// `precacheImage`; video gets its controller created + initialized
  /// ahead of time and handed off in `_loadStory` when we actually get
  /// there — best-effort, errors are swallowed since a failed preload
  /// should just fall back to the normal cold-load path, not surface.
  void _preloadNext() {
    StoryModel? next;
    if (_storyIndex < _group.stories.length - 1) {
      next = _group.stories[_storyIndex + 1];
    } else if (_groupIndex < widget.groups.length - 1) {
      final nextGroup = widget.groups[_groupIndex + 1];
      if (nextGroup.stories.isNotEmpty) next = nextGroup.stories.first;
    }
    if (next == null) return;
    final url = next.mediaUrl;
    if (url == null || url.isEmpty) return;

    if (next.mediaType == 'video') {
      if (_preloadedVideoId == next.id) return; // already preloaded/preloading this one
      _preloadedVideoController?.dispose();
      _preloadedVideoId = next.id;
      final c = VideoPlayerController.networkUrl(Uri.parse(url));
      _preloadedVideoController = c;
      c.initialize().catchError((_) {});
    } else {
      precacheImage(CachedNetworkImageProvider(url), context).catchError((_) => null);
    }
  }

  void _advance() {
    if (_storyIndex < _group.stories.length - 1) {
      setState(() => _storyIndex++);
      _loadStory();
    } else if (_groupIndex < widget.groups.length - 1) {
      _groupController.nextPage(duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
    } else {
      Navigator.of(context).maybePop();
    }
  }

  void _rewind() {
    if (_storyIndex > 0) {
      setState(() => _storyIndex--);
      _loadStory();
    } else if (_groupIndex > 0) {
      _groupController.previousPage(duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
    }
  }

  void _onGroupChanged(int i) {
    // Whatever was preloaded belonged to the old group's next-story guess;
    // it no longer applies once the user pages to a different group.
    _preloadedVideoController?.dispose();
    _preloadedVideoController = null;
    _preloadedVideoId = null;
    setState(() {
      _groupIndex = i;
      _storyIndex = 0;
    });
    _loadStory();
  }

  void _togglePause(bool pause) {
    if (_userPaused == pause) return;
    _userPaused = pause;
    setState(() {});
    _applyPlaybackState();
  }

  void _onVerticalDragStart(DragStartDetails d) {
    _togglePause(true);
  }

  void _onVerticalDragUpdate(DragUpdateDetails d) {
    // Ignore upward movement past neutral — this gesture is swipe-DOWN to
    // dismiss only; there's no swipe-up action here to steal from.
    if (d.delta.dy < 0 && _dragOffset <= 0) return;
    setState(() => _dragOffset = (_dragOffset + d.delta.dy).clamp(0.0, _kMaxDragOffset));
  }

  void _onVerticalDragEnd(DragEndDetails d) {
    final velocity = d.primaryVelocity ?? 0;
    final shouldDismiss = _dragOffset > _kDismissDragThreshold || velocity > _kDismissVelocityThreshold;
    if (shouldDismiss) {
      Navigator.of(context).maybePop();
      return;
    }
    setState(() => _dragOffset = 0);
    _togglePause(false);
  }

  void _onVerticalDragCancel() {
    setState(() => _dragOffset = 0);
    _togglePause(false);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      // Reaction/reply bar is positioned by hand off `viewInsets.bottom`
      // (see `_buildReactionReplyBar`) so it slides up and sits just above
      // the keyboard instead of the Scaffold resizing the whole story
      // (which would otherwise squash/reflow the video or image).
      resizeToAvoidBottomInset: false,
      body: PageView.builder(
        controller: _groupController,
        itemCount: widget.groups.length,
        onPageChanged: _onGroupChanged,
        // Dragging vertically to dismiss shouldn't also fight the
        // PageView's own horizontal drag recognizer for the gesture arena.
        physics: _dragOffset > 0 ? const NeverScrollableScrollPhysics() : const PageScrollPhysics(),
        itemBuilder: (context, gi) {
          final group = widget.groups[gi];
          // Only the active page renders its real story content — this
          // page-builder still runs for neighbours, so guard on gi ==
          // _groupIndex to avoid building a second, unused video controller.
          if (gi != _groupIndex) return const SizedBox.shrink();
          return GestureDetector(
            // onTapUp, not onTapDown: a tap that lands on a sticker is claimed by
            // the sticker and must not also advance / rewind the story.
            onTapUp: (d) {
              if (_dragOffset > 0) return; // mid-dismiss-drag, tap zones are inert
              final w = MediaQuery.of(context).size.width;
              if (d.globalPosition.dx < w / 3) {
                _rewind();
              } else if (d.globalPosition.dx > w * 2 / 3) {
                _advance();
              }
            },
            onLongPressStart: (_) => _togglePause(true),
            onLongPressEnd: (_) => _togglePause(false),
            onVerticalDragStart: _onVerticalDragStart,
            onVerticalDragUpdate: _onVerticalDragUpdate,
            onVerticalDragEnd: _onVerticalDragEnd,
            onVerticalDragCancel: _onVerticalDragCancel,
            child: Transform.translate(
              offset: Offset(0, _dragOffset),
              child: Opacity(
                opacity: 1 - (_dragOffset / _kMaxDragOffset).clamp(0.0, 0.6),
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    _buildMedia(group.stories[_storyIndex]),
                    if (group.stories[_storyIndex].stickers.isNotEmpty) _buildStickerLayer(group.stories[_storyIndex]),
                    _buildTopOverlay(group),
                    if (_readOnly)
                      (_isOwnGroup ? _buildHighlightOwnerBar() : const SizedBox.shrink())
                    else if (_isOwnGroup)
                      _buildViewersRow()
                    else
                      _buildReactionReplyBar(),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }

  /// A story with stickers shows its media inside the same 9:16 canvas the
  /// stickers are positioned against (and the composer previewed), so a sticker
  /// lands on the same spot of the picture for every viewer. Stories without
  /// stickers keep the old full-screen `contain` behaviour.
  Widget _buildMedia(StoryModel story) {
    final media = _buildMediaContent(story);
    if (story.stickers.isEmpty) return media;
    return StoryCanvas(builder: (_, __) => SizedBox.expand(child: media));
  }

  Widget _buildMediaContent(StoryModel story) {
    if (story.mediaType == 'video') {
      final c = _videoController;
      if (c == null || !c.value.isInitialized) {
        return const Center(child: CircularProgressIndicator(color: _kStoryAccent));
      }
      return Center(child: AspectRatio(aspectRatio: c.value.aspectRatio, child: VideoPlayer(c)));
    }
    if ((story.mediaUrl ?? '').isEmpty) {
      return Container(color: Colors.black, child: const Center(child: Icon(Icons.broken_image_rounded, color: Colors.white38, size: 48)));
    }
    return CachedNetworkImage(
      imageUrl: story.mediaUrl!,
      fit: BoxFit.contain,
      placeholder: (_, __) => const Center(child: CircularProgressIndicator(color: _kStoryAccent)),
      errorWidget: (_, __, ___) => const Center(child: Icon(Icons.broken_image_rounded, color: Colors.white38, size: 48)),
    );
  }

  // ─────────────────────────────────────────────────────────────────────
  // Stories upgrade, Part 2 — stickers
  // ─────────────────────────────────────────────────────────────────────

  Widget _buildStickerLayer(StoryModel story) {
    final sorted = [...story.stickers]..sort((a, b) => a.zIndex.compareTo(b.zIndex));
    return StoryCanvas(
      builder: (context, canvas) => Stack(
        clipBehavior: Clip.none,
        children: [
          for (final s in sorted)
            StickerPlacement(
              key: ValueKey('${story.id}_${s.id}'),
              x: s.x,
              y: s.y,
              rotation: s.rotation,
              scale: s.scale,
              canvas: canvas,
              child: StoryStickerView(
                sticker: s,
                isOwner: _isOwnGroup,
                busy: _stickerBusy.contains(s.id),
                onMentionTap: _onMentionTap,
                onLinkTap: _onLinkTap,
                onVote: (sticker, option) async {
                  if (_readOnly) return _readOnlySnack();
                  await _onVote(story, sticker, option);
                },
                onQuestionTap: (sticker) async {
                  if (_readOnly) return _readOnlySnack();
                  await _onQuestionTap(story, sticker);
                },
                onResponsesTap: (sticker) async {
                  if (_readOnly) return _readOnlySnack();
                  _openResponsesSheet(story, sticker);
                },
              ),
            ),
        ],
      ),
    );
  }

  void _readOnlySnack() => _stickerSnack('Highlight stories are read-only.'); // P2-FE

  void _stickerSnack(String message) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message), duration: const Duration(seconds: 2)));
  }

  Future<void> _onVote(StoryModel story, StorySticker sticker, int option) async {
    if (_stickerBusy.contains(sticker.id)) return;
    HapticFeedback.selectionClick();
    setState(() => _stickerBusy.add(sticker.id));
    try {
      await StoryService.votePoll(story.id, sticker, option);
    } on StickerAlreadyRespondedException {
      // Voted before (e.g. a retry after a lost response): the sticker has
      // already been refreshed with the real state, nothing to tell the user.
    } catch (e) {
      if (mounted) _stickerSnack(e.toString().replaceFirst('Exception: ', ''));
    } finally {
      if (mounted) setState(() => _stickerBusy.remove(sticker.id));
    }
  }

  Future<void> _onQuestionTap(StoryModel story, StorySticker sticker) async {
    final l10n = AppLocalizations.of(context)!;
    if (sticker.myAnswered) {
      _stickerSnack(l10n.stickerQuestionAnswered);
      return;
    }
    _togglePause(true);
    final text = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF1C1C1E),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
      builder: (_) => _AnswerSheet(prompt: sticker.prompt),
    );
    if (mounted) _togglePause(false);
    if (!mounted || text == null || text.trim().isEmpty) return;

    setState(() => _stickerBusy.add(sticker.id));
    try {
      await StoryService.answerQuestion(story.id, sticker, text.trim());
      if (mounted) _stickerSnack(l10n.stickerQuestionAnswered);
    } on StickerAlreadyRespondedException {
      // Already answered earlier — state refreshed, the sticker now says so.
    } catch (e) {
      if (mounted) _stickerSnack(e.toString().replaceFirst('Exception: ', ''));
    } finally {
      if (mounted) setState(() => _stickerBusy.remove(sticker.id));
    }
  }

  void _onMentionTap(StorySticker sticker) {
    final username = sticker.mentionUsername;
    if (username == null || username.isEmpty) return;
    // Your own tag: the profile screen for yourself is a tab, not a route.
    if (sticker.mentionUserId != null && sticker.mentionUserId == widget.myUserId) return;
    _togglePause(true);
    Navigator.of(context)
        .push(MaterialPageRoute(builder: (_) => TargetProfilePage(username: username)))
        .whenComplete(() {
      if (mounted) _togglePause(false);
    });
  }

  Future<void> _onLinkTap(StorySticker sticker) async {
    final l10n = AppLocalizations.of(context)!;
    final uri = Uri.tryParse(sticker.url);
    if (uri == null || !(uri.scheme == 'http' || uri.scheme == 'https')) return;
    _togglePause(true);
    final open = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: const Color(0xFF1C1C1E),
        title: Text(l10n.stickerOpenLinkTitle, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 16)),
        content: Text(sticker.url, style: const TextStyle(color: Colors.white70)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(c, true), child: Text(l10n.stickerOpenLink)),
        ],
      ),
    );
    if (mounted) _togglePause(false);
    if (open != true) return;
    try {
      final launched = await launchUrl(uri, mode: LaunchMode.externalApplication);
      if (!launched && mounted) _stickerSnack(l10n.stickerLinkOpenFailed);
    } catch (_) {
      if (mounted) _stickerSnack(l10n.stickerLinkOpenFailed);
    }
  }

  void _openResponsesSheet(StoryModel story, StorySticker sticker) {
    _togglePause(true);
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF1C1C1E),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
      builder: (_) => _StickerResponsesSheet(storyId: story.id, sticker: sticker),
    ).whenComplete(() {
      if (mounted) _togglePause(false);
    });
  }

  Widget _buildTopOverlay(StoryGroup group) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(10, 8, 10, 0),
        child: Column(
          children: [
            // Segmented progress bars — one per story in this user's group.
            Row(
              children: [
                for (int i = 0; i < group.stories.length; i++)
                  Expanded(
                    child: Container(
                      height: 2.5,
                      margin: const EdgeInsets.symmetric(horizontal: 2),
                      decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2)),
                      child: i < _storyIndex
                          ? const DecoratedBox(decoration: BoxDecoration(color: Colors.white))
                          : i == _storyIndex
                              ? AnimatedBuilder(
                                  animation: _progressController ?? kAlwaysDismissedAnimation,
                                  builder: (_, __) => FractionallySizedBox(
                                    alignment: Alignment.centerLeft,
                                    widthFactor: _progressController?.value ?? 0,
                                    child: Container(decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(2))),
                                  ),
                                )
                              : const SizedBox.shrink(),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                CircleAvatar(
                  radius: 16,
                  backgroundColor: Colors.white24,
                  backgroundImage: (group.userProfilePic ?? '').isNotEmpty ? CachedNetworkImageProvider(group.userProfilePic!) : null,
                  child: (group.userProfilePic ?? '').isEmpty
                      ? Text(group.username.isNotEmpty ? group.username[0].toUpperCase() : '?', style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.bold))
                      : null,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    _readOnly && (widget.highlight!.title.isNotEmpty) ? '${group.username} · ${widget.highlight!.title}' : group.username,
                    style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 13.5, shadows: [Shadow(color: Colors.black45, blurRadius: 4)]),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (_story.isCloseFriends)
                  Container(
                    margin: const EdgeInsets.only(right: 8),
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(color: const Color(0xFF2BB673), borderRadius: BorderRadius.circular(10)),
                    child: Row(mainAxisSize: MainAxisSize.min, children: [
                      const Icon(Icons.star_rounded, size: 12, color: Colors.white),
                      const SizedBox(width: 3),
                      Text(AppLocalizations.of(context)!.closeFriends, style: const TextStyle(color: Colors.white, fontSize: 10.5, fontWeight: FontWeight.w700)),
                    ]),
                  ),
                _FrostedCircleButton(icon: Icons.close_rounded, onTap: () => Navigator.of(context).maybePop()),
              ],
            ),
            if ((_story.caption ?? '').isNotEmpty)
              Align(
                alignment: Alignment.centerLeft,
                child: Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(_story.caption!, style: const TextStyle(color: Colors.white, fontSize: 13, shadows: [Shadow(color: Colors.black45, blurRadius: 4)])),
                ),
              ),
          ],
        ),
      ),
    );
  }

  /// Task 11 — own-story "N viewers", Instagram convention: bottom-left,
  /// tap opens the list. Uses the current story's own `viewsCount` for the
  /// label (already on the model, no extra call needed just to show a
  /// number) — the list itself is fetched lazily, only when tapped.
  Widget _buildViewersRow() {
    final l10n = AppLocalizations.of(context)!;
    return Positioned(
      left: 14,
      right: 14,
      bottom: 18,
      child: SafeArea(
        top: false,
        child: Row(
          children: [
            _glassPill(
              icon: Icons.remove_red_eye_outlined,
              label: l10n.viewersCount(_story.viewsCount),
              onTap: _openViewersSheet,
            ),
            const Spacer(),
            // P2-FE — add this story to a highlight (own stories only).
            _glassPill(icon: Icons.auto_awesome_outlined, label: 'Highlight', onTap: _openAddToHighlight),
          ],
        ),
      ),
    );
  }

  /// P2-FE — owner of a highlight: edit it (title / cover / stories / delete).
  Widget _buildHighlightOwnerBar() {
    return Positioned(
      left: 14,
      right: 14,
      bottom: 18,
      child: SafeArea(
        top: false,
        child: Row(
          children: [
            const Spacer(),
            _glassPill(icon: Icons.edit_outlined, label: 'Edit', onTap: _openHighlightEditor),
          ],
        ),
      ),
    );
  }

  Widget _glassPill({required IconData icon, required String label, required VoidCallback onTap}) {
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(20),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
            decoration: BoxDecoration(color: Colors.white.withOpacity(0.14), borderRadius: BorderRadius.circular(20)),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(icon, color: Colors.white, size: 17),
                const SizedBox(width: 6),
                Text(label, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 12.5)),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _openAddToHighlight() async {
    _togglePause(true);
    final msg = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: const Color(0xFF1C1C1E),
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
      builder: (_) => AddToHighlightSheet(storyId: _story.id),
    );
    if (!mounted) return;
    _togglePause(false);
    if (msg != null) _stickerSnack(msg);
  }

  Future<void> _openHighlightEditor() async {
    _togglePause(true);
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute(builder: (_) => HighlightEditorScreen(existing: widget.highlight)),
    );
    if (!mounted) return;
    if (changed == true) {
      Navigator.of(context).pop(true); // caller refreshes the row
    } else {
      _togglePause(false);
    }
  }

  void _openViewersSheet() {
    // Viewing the list shouldn't let the story auto-advance underneath it.
    _togglePause(true);
    final storyId = _story.id;
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF1C1C1E),
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
      builder: (_) => _StoryViewersSheet(storyId: storyId),
    ).whenComplete(() {
      if (mounted) _togglePause(false);
    });
  }

  // ─────────────────────────────────────────────────────────────────────
  // Story reactions + reply — Instagram-style bottom bar: a row of
  // quick-tap emoji (instant reaction, `StoryService.reactToStory`) above
  // a text field ("Reply to <username>...") that delivers as a normal DM
  // (`StoryService.replyToStory`, which the backend inserts straight into
  // the story owner's chat inbox — nothing further to wire up client-side,
  // it arrives over the same websocket the message app already uses).
  // Hidden on your own story, same gate `_buildViewersRow` uses in
  // reverse.
  // ─────────────────────────────────────────────────────────────────────
  static const List<String> _kQuickReactions = ['❤️', '😂', '😮', '😢', '👏', '🔥'];

  Widget _buildReactionReplyBar() {
    final l10n = AppLocalizations.of(context)!;
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;
    final safeBottom = MediaQuery.of(context).padding.bottom;
    return AnimatedPositioned(
      duration: const Duration(milliseconds: 180),
      curve: Curves.easeOut,
      left: 0,
      right: 0,
      bottom: bottomInset > 0 ? bottomInset : safeBottom,
      child: SafeArea(
        top: false,
        // The keyboard already accounts for the bottom inset above; adding
        // SafeArea's own bottom padding on top of it too (when the
        // keyboard IS open) would double-pad, so only let SafeArea apply
        // its bottom inset when the keyboard is closed.
        bottom: bottomInset == 0,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                padding: const EdgeInsets.only(bottom: 10, left: 4),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    for (final emoji in _kQuickReactions) _buildQuickReactionButton(emoji),
                  ],
                ),
              ),
              Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(24),
                      child: BackdropFilter(
                        filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                          decoration: BoxDecoration(
                            color: Colors.white.withOpacity(0.14),
                            borderRadius: BorderRadius.circular(24),
                            border: Border.all(color: Colors.white.withOpacity(0.22)),
                          ),
                          child: TextField(
                            controller: _replyController,
                            focusNode: _replyFocus,
                            style: const TextStyle(color: Colors.white, fontSize: 14),
                            maxLines: 4,
                            minLines: 1,
                            textCapitalization: TextCapitalization.sentences,
                            cursorColor: _kStoryAccent,
                            onChanged: (_) => setState(() {}),
                            decoration: InputDecoration(
                              hintText: l10n.replyToStoryHint(_group.username),
                              hintStyle: const TextStyle(color: Colors.white60),
                              border: InputBorder.none,
                              isDense: true,
                              contentPadding: const EdgeInsets.symmetric(vertical: 12),
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                  if (_replyController.text.trim().isNotEmpty) ...[
                    const SizedBox(width: 8),
                    _buildSendButton(),
                  ],
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildQuickReactionButton(String emoji) {
    final isPopping = _poppingEmoji == emoji;
    return Padding(
      padding: const EdgeInsets.only(right: 10),
      child: GestureDetector(
        onTap: () => _onQuickReact(emoji),
        child: AnimatedScale(
          scale: isPopping ? 1.5 : 1.0,
          duration: const Duration(milliseconds: 220),
          curve: Curves.elasticOut,
          child: Text(emoji, style: const TextStyle(fontSize: 26)),
        ),
      ),
    );
  }

  Widget _buildSendButton() {
    return Material(
      color: _kStoryAccent,
      shape: const CircleBorder(),
      child: InkWell(
        customBorder: const CircleBorder(),
        onTap: _sendingReply ? null : _sendReply,
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: _sendingReply
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                )
              : const Icon(Icons.send_rounded, color: Colors.white, size: 18),
        ),
      ),
    );
  }

  Future<void> _onQuickReact(String emoji) async {
    HapticFeedback.mediumImpact();
    setState(() => _poppingEmoji = emoji);
    Future.delayed(const Duration(milliseconds: 260), () {
      if (mounted && _poppingEmoji == emoji) setState(() => _poppingEmoji = null);
    });
    try {
      await StoryService.reactToStory(_story.id, emoji);
    } catch (_) {
      // Best-effort, same as markViewed() above — a failed reaction isn't
      // worth interrupting story playback for.
    }
  }

  Future<void> _sendReply() async {
    final text = _replyController.text.trim();
    if (text.isEmpty || _sendingReply) return;
    setState(() => _sendingReply = true);
    try {
      await StoryService.replyToStory(_story.id, text);
      if (!mounted) return;
      _replyController.clear();
      _replyFocus.unfocus();
      final l10n = AppLocalizations.of(context)!;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.replySentToStory), duration: const Duration(seconds: 2)),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))),
      );
    } finally {
      if (mounted) setState(() => _sendingReply = false);
    }
  }
}

class _FrostedCircleButton extends StatelessWidget {
  final IconData icon;
  final VoidCallback onTap;
  const _FrostedCircleButton({required this.icon, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(18),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
        child: Material(
          color: Colors.white.withOpacity(0.16),
          shape: const CircleBorder(),
          child: InkWell(
            customBorder: const CircleBorder(),
            onTap: onTap,
            child: Padding(padding: const EdgeInsets.all(7), child: Icon(icon, color: Colors.white, size: 20)),
          ),
        ),
      ),
    );
  }
}

class _StoryViewersSheet extends StatefulWidget {
  final String storyId;
  const _StoryViewersSheet({required this.storyId});

  @override
  State<_StoryViewersSheet> createState() => _StoryViewersSheetState();
}

class _StoryViewersSheetState extends State<_StoryViewersSheet> {
  late final Future<List<StoryViewerEntry>> _future = StoryService.getStoryViewers(widget.storyId);
  // Viewers I blocked from this sheet — dropped from the list immediately.
  final Set<String> _blockedIds = {};

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return SafeArea(
      child: SizedBox(
        height: MediaQuery.of(context).size.height * 0.55,
        child: Column(
          children: [
            const SizedBox(height: 10),
            Container(width: 36, height: 4, decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2))),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
              child: Row(
                children: [
                  const Icon(Icons.remove_red_eye_outlined, color: _kStoryAccent, size: 18),
                  const SizedBox(width: 8),
                  Text(l10n.viewersTitle, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 15)),
                ],
              ),
            ),
            const Divider(color: Colors.white12, height: 1),
            Expanded(
              child: FutureBuilder<List<StoryViewerEntry>>(
                future: _future,
                builder: (context, snap) {
                  if (snap.connectionState != ConnectionState.done) {
                    return const Center(child: CircularProgressIndicator(color: _kStoryAccent));
                  }
                  if (snap.hasError) {
                    // Most likely cause: the backend route this hits
                    // (`GET /post/stories/<id>/viewers/`) doesn't exist yet
                    // — see the flag comment on
                    // `StoryService.getStoryViewers()`.
                    return Center(
                      child: Padding(
                        padding: const EdgeInsets.all(24),
                        child: Column(mainAxisSize: MainAxisSize.min, children: [
                          const Icon(Icons.wifi_off_rounded, color: Colors.white24, size: 28),
                          const SizedBox(height: 10),
                          Text(l10n.couldntLoadViewers, style: const TextStyle(color: Colors.white54), textAlign: TextAlign.center),
                        ]),
                      ),
                    );
                  }
                  final viewers = (snap.data ?? []).where((v) => !_blockedIds.contains(v.userId)).toList();
                  if (viewers.isEmpty) {
                    return Center(
                      child: Padding(
                        padding: const EdgeInsets.all(24),
                        child: Column(mainAxisSize: MainAxisSize.min, children: [
                          const Icon(Icons.remove_red_eye_outlined, color: Colors.white24, size: 28),
                          const SizedBox(height: 10),
                          Text(l10n.noViewsYet, style: const TextStyle(color: Colors.white54)),
                        ]),
                      ),
                    );
                  }
                  return ListView.builder(
                    itemCount: viewers.length,
                    itemBuilder: (context, i) {
                      final v = viewers[i];
                      return ListTile(
                        leading: CircleAvatar(
                          radius: 18,
                          backgroundColor: Colors.white24,
                          backgroundImage: (v.profilePicture ?? '').isNotEmpty ? CachedNetworkImageProvider(v.profilePicture!) : null,
                          child: (v.profilePicture ?? '').isEmpty
                              ? Text(v.username.isNotEmpty ? v.username[0].toUpperCase() : '?', style: const TextStyle(color: Colors.white))
                              : null,
                        ),
                        title: Text(v.username, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
                        trailing: PopupMenuButton<String>(
                          icon: const Icon(Icons.more_horiz_rounded, color: Colors.white54),
                          onSelected: (_) async {
                            final uid = int.tryParse(v.userId);
                            if (uid == null) return;
                            final ok = await blockUserFlow(context, userId: uid, username: v.username);
                            if (ok && mounted) setState(() => _blockedIds.add(v.userId));
                          },
                          itemBuilder: (_) => [
                            PopupMenuItem(value: 'block', child: Text(l10n.blockMenuBlockUser)),
                          ],
                        ),
                      );
                    },
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}


/// Question sticker: the viewer types an answer; pops the text (null = cancelled).
class _AnswerSheet extends StatefulWidget {
  final String prompt;
  const _AnswerSheet({required this.prompt});

  @override
  State<_AnswerSheet> createState() => _AnswerSheetState();
}

class _AnswerSheetState extends State<_AnswerSheet> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _send() {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    Navigator.of(context).pop(text);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Center(child: Container(width: 36, height: 4, decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2)))),
              Padding(
                padding: const EdgeInsets.only(top: 14, bottom: 12),
                child: Text(widget.prompt, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 16)),
              ),
              TextField(
                controller: _controller,
                autofocus: true,
                maxLength: kStoryAnswerMax,
                maxLines: 4,
                minLines: 2,
                textCapitalization: TextCapitalization.sentences,
                style: const TextStyle(color: Colors.white),
                cursorColor: _kStoryAccent,
                onChanged: (_) => setState(() {}),
                decoration: InputDecoration(
                  hintText: l10n.stickerAnswerHint,
                  hintStyle: const TextStyle(color: Colors.white54),
                  counterStyle: const TextStyle(color: Colors.white38, fontSize: 11),
                  filled: true,
                  fillColor: Colors.white.withOpacity(0.08),
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
                ),
              ),
              const SizedBox(height: 10),
              ElevatedButton(
                onPressed: _controller.text.trim().isEmpty ? null : _send,
                style: ElevatedButton.styleFrom(
                  backgroundColor: _kStoryAccent,
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(vertical: 14),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                ),
                child: Text(l10n.stickerSendAnswer, style: const TextStyle(fontWeight: FontWeight.w800)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Owner only: who voted for what (poll) / what people answered (question).
/// Paginated ("Load more"); a poll also shows its per-option totals on top.
class _StickerResponsesSheet extends StatefulWidget {
  final String storyId;
  final StorySticker sticker;
  const _StickerResponsesSheet({required this.storyId, required this.sticker});

  @override
  State<_StickerResponsesSheet> createState() => _StickerResponsesSheetState();
}

class _StickerResponsesSheetState extends State<_StickerResponsesSheet> {
  final List<StickerResponseRow> _rows = [];
  StickerResponses? _summary; // first page carries the poll totals
  int _total = 0;
  int _page = 0;
  bool _loading = true;
  bool _loadingMore = false;
  bool _error = false;

  bool get _isPoll => widget.sticker.kind == StorySticker.kPoll;
  bool get _hasMore => _rows.length < _total;

  @override
  void initState() {
    super.initState();
    _loadPage(1);
  }

  Future<void> _loadPage(int page) async {
    try {
      final r = await StoryService.getStickerResponses(widget.storyId, widget.sticker.id, page: page);
      if (!mounted) return;
      setState(() {
        if (page == 1) {
          _rows.clear();
          _summary = r;
        }
        _rows.addAll(r.rows);
        _total = r.count;
        _page = page;
        _loading = false;
        _loadingMore = false;
        _error = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _loadingMore = false;
        _error = true;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final title = _isPoll ? widget.sticker.pollQuestion : widget.sticker.prompt;
    return SafeArea(
      child: SizedBox(
        height: MediaQuery.of(context).size.height * 0.6,
        child: Column(
          children: [
            const SizedBox(height: 10),
            Container(width: 36, height: 4, decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2))),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
              child: Row(children: [
                Icon(_isPoll ? Icons.poll_outlined : Icons.forum_outlined, color: _kStoryAccent, size: 18),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    title.isNotEmpty ? title : l10n.stickerResponsesTitle,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 15),
                  ),
                ),
              ]),
            ),
            const Divider(color: Colors.white12, height: 1),
            Expanded(child: _buildBody(l10n)),
          ],
        ),
      ),
    );
  }

  Widget _buildBody(AppLocalizations l10n) {
    if (_loading) return const Center(child: CircularProgressIndicator(color: _kStoryAccent));
    if (_error && _rows.isEmpty) {
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(l10n.stickerLoadFailed, style: const TextStyle(color: Colors.white54)),
          TextButton(
            onPressed: () {
              setState(() {
                _loading = true;
                _error = false;
              });
              _loadPage(1);
            },
            child: Text(l10n.retry),
          ),
        ]),
      );
    }
    if (_rows.isEmpty) {
      return Center(child: Text(l10n.stickerNoResponses, style: const TextStyle(color: Colors.white54)));
    }
    final showSummary = _isPoll && _summary != null && _summary!.pollCounts.isNotEmpty;
    final headerCount = showSummary ? 1 : 0;
    return ListView.builder(
      itemCount: headerCount + _rows.length + (_hasMore ? 1 : 0),
      itemBuilder: (context, i) {
        if (showSummary && i == 0) return _buildPollSummary(_summary!);
        final rowIndex = i - headerCount;
        if (rowIndex >= _rows.length) {
          return Center(
            child: _loadingMore
                ? const Padding(padding: EdgeInsets.all(12), child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: _kStoryAccent)))
                : TextButton(
                    onPressed: () {
                      setState(() => _loadingMore = true);
                      _loadPage(_page + 1);
                    },
                    child: Text(l10n.stickerLoadMore),
                  ),
          );
        }
        final row = _rows[rowIndex];
        final subtitle = _isPoll ? row.optionLabel : row.text;
        return ListTile(
          leading: CircleAvatar(
            radius: 18,
            backgroundColor: Colors.white24,
            backgroundImage: (row.profilePicture ?? '').isNotEmpty ? CachedNetworkImageProvider(row.profilePicture!) : null,
            child: (row.profilePicture ?? '').isEmpty
                ? Text(row.username.isNotEmpty ? row.username[0].toUpperCase() : '?', style: const TextStyle(color: Colors.white))
                : null,
          ),
          title: Text(row.username, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
          subtitle: (subtitle ?? '').isNotEmpty ? Text(subtitle!, style: const TextStyle(color: Colors.white70)) : null,
        );
      },
    );
  }

  Widget _buildPollSummary(StickerResponses summary) {
    final options = widget.sticker.pollOptions;
    final total = summary.pollTotal;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
      child: Column(
        children: [
          for (int i = 0; i < options.length; i++)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Expanded(child: Text(options[i], maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600))),
                  Text('${i < summary.pollCounts.length ? summary.pollCounts[i] : 0}', style: const TextStyle(color: Colors.white70, fontSize: 12.5, fontWeight: FontWeight.w700)),
                ]),
                const SizedBox(height: 4),
                ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(
                    value: total > 0 && i < summary.pollCounts.length ? (summary.pollCounts[i] / total).clamp(0.0, 1.0).toDouble() : 0,
                    minHeight: 6,
                    backgroundColor: Colors.white12,
                    color: _kStoryAccent,
                  ),
                ),
              ]),
            ),
        ],
      ),
    );
  }
}
