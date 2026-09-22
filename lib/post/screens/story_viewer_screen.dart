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

import 'dart:async';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:video_player/video_player.dart';
import '../models/story_model.dart' show StoryGroup, StoryModel, StoryViewerEntry;
import '../services/story_service.dart';
import '../../l10n/app_localizations.dart';

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

  const StoryViewerScreen({super.key, required this.groups, required this.initialGroupIndex, this.myUserId});

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

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
    _groupIndex = widget.initialGroupIndex;
    _groupController = PageController(initialPage: _groupIndex);
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
    StoryService.markViewed(_story.id).catchError((_) => 0);

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
            onTapDown: (d) {
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
                    _buildTopOverlay(group),
                    if (_isOwnGroup) _buildViewersRow(),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }

  Widget _buildMedia(StoryModel story) {
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
                    group.username,
                    style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 13.5, shadows: [Shadow(color: Colors.black45, blurRadius: 4)]),
                    overflow: TextOverflow.ellipsis,
                  ),
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
        child: GestureDetector(
          onTap: _openViewersSheet,
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
                    const Icon(Icons.remove_red_eye_outlined, color: Colors.white, size: 17),
                    const SizedBox(width: 6),
                    Text(l10n.viewersCount(_story.viewsCount), style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 12.5)),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
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
                  final viewers = snap.data ?? [];
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