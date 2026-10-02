// ============================================================
// REELS — player pool.
//
// Keeps VideoPlayerControllers for PREVIOUS / CURRENT / NEXT reel only; anything
// else is disposed the moment the window moves.
//
//   * previous, current : owned by the pool (created here, or handed over by
//                         FeedVideoPreloader.take() when they were primed).
//   * next (+1)         : primed through FeedVideoPreloader (owner 'reels',
//                         cap 1) so it buffers silently and is taken over as
//                         soon as the user settles on it. Home's own preloads
//                         (cap 2) are a different bucket and are never touched.
//
// The pool only knows indexes + a url lookup, so the Reels list can keep
// growing (pagination) underneath it.
// ============================================================

import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:video_player/video_player.dart';

import '../../widgets/feed_video_preloader.dart';

class ReelsPlayerPool extends ChangeNotifier {
  /// Video URL of reel [index], or null when the index is out of range.
  final String? Function(int index) urlAt;

  ReelsPlayerPool({required this.urlAt, bool initialMuted = false}) : _muted = initialMuted;

  final Map<int, VideoPlayerController> _ctrls = {};
  final Map<int, VoidCallback> _trackers = {};
  final Map<int, double> _maxSeconds = {}; // furthest point reached (survives loops)
  final Set<int> _failed = {};

  int _current = -1;
  bool _canPlay = false; // false while app is backgrounded / route covered / user paused
  bool _muted;
  bool _disposed = false;
  String? _primedUrl; // url currently primed in FeedVideoPreloader for "next"

  int get currentIndex => _current;
  bool get muted => _muted;

  VideoPlayerController? controllerAt(int index) => _ctrls[index];

  bool isReady(int index) => _ctrls[index]?.value.isInitialized ?? false;

  bool hasFailed(int index) => _failed.contains(index);

  /// Furthest playback position (seconds) reached on [index] since its controller
  /// was created; 0 if unknown. A loop restarting at 0:00 does not lower it.
  double watchedSecondsAt(int index) {
    final c = _ctrls[index];
    final now = (c != null && c.value.isInitialized) ? c.value.position.inMilliseconds / 1000.0 : 0.0;
    return math.max(now, _maxSeconds[index] ?? 0.0);
  }

  // ---------------------------------------------------------------- window

  /// Call when a page has SETTLED (not while dragging). Slides the window to
  /// [index]±1, disposes the rest, pauses everything except [index] and plays it
  /// (if playback is currently allowed).
  void settle(int index) {
    if (_disposed) return;
    _current = index;

    // 1. Dispose everything outside [index-1, index+1].
    for (final i in _ctrls.keys.toList()) {
      if ((i - index).abs() > 1) _disposeAt(i);
    }

    // 2. Current page: keep / take a primed controller / create.
    _ensure(index);

    // 3. Only the settled page plays.
    for (final e in _ctrls.entries) {
      if (e.key != index && e.value.value.isInitialized) e.value.pause();
    }

    // 4. Next (+1): prime through the preloader (unless we already hold it, e.g.
    //    after swiping back). A previously primed url that is no longer "next"
    //    is released straight away.
    final nextUrl = _ctrls.containsKey(index + 1) ? null : urlAt(index + 1);
    if (_primedUrl != null && _primedUrl != nextUrl) {
      FeedVideoPreloader.instance.release(_primedUrl!);
    }
    if (nextUrl != null && nextUrl.isNotEmpty) {
      FeedVideoPreloader.instance.preload(nextUrl, owner: FeedVideoPreloader.reelsOwner, maxCached: 1);
      _primedUrl = nextUrl;
    } else {
      _primedUrl = null;
    }

    _applyPlayState();
    notifyListeners();
  }

  /// The reel list changed underneath the pool (a reel was removed / re-inserted
  /// at [index]): every held controller at or after [index] now belongs to a
  /// different reel, so drop them. The caller settles again afterwards; the
  /// primed "next" video (FeedVideoPreloader) is re-used by [settle].
  void dropFrom(int index) {
    if (_disposed) return;
    for (final i in _ctrls.keys.toList()) {
      if (i >= index) _disposeAt(i);
    }
    _failed.removeWhere((i) => i >= index);
    if (_current >= index) _current = -1;
    notifyListeners();
  }

  /// Retry a reel whose controller failed to initialize.
  void retry(int index) {
    if (_disposed) return;
    _failed.remove(index);
    _disposeAt(index);
    if ((index - _current).abs() <= 1) _ensure(index);
    _applyPlayState();
    notifyListeners();
  }

  // ---------------------------------------------------------------- playback

  /// Allow / stop playback of the current reel (lifecycle, route push, sheet,
  /// tap-to-pause). Does not touch which controllers are held.
  void setPlaybackAllowed(bool allowed) {
    if (_disposed) return;
    _canPlay = allowed;
    _applyPlayState();
    notifyListeners();
  }

  /// Global sound flag (ReelsSoundService). Applied to EVERY held controller now
  /// and to each controller created later (see [_ensure]).
  void setMuted(bool muted) {
    if (_disposed) return;
    if (_muted == muted) return;
    _muted = muted;
    for (final c in _ctrls.values) {
      if (c.value.isInitialized) c.setVolume(muted ? 0 : 1);
    }
    notifyListeners();
  }

  void _applyPlayState() {
    for (final e in _ctrls.entries) {
      final c = e.value;
      if (!c.value.isInitialized) continue;
      if (e.key == _current && _canPlay) {
        if (!c.value.isPlaying) c.play();
      } else if (c.value.isPlaying) {
        c.pause();
      }
    }
  }

  // ---------------------------------------------------------------- internals

  void _ensure(int index) {
    if (_ctrls.containsKey(index)) return;
    final url = urlAt(index);
    if (url == null || url.isEmpty) return;

    // Was it primed? Then it is already (being) buffered.
    final primed = FeedVideoPreloader.instance.take(url);
    if (url == _primedUrl) {
      _primedUrl = null;
      // Priming had not finished yet (miss): drop it so the same video is not buffered twice.
      if (primed == null) FeedVideoPreloader.instance.release(url);
    }
    final c = primed ?? VideoPlayerController.networkUrl(Uri.parse(url));
    _ctrls[index] = c;
    _failed.remove(index);
    c.setVolume(_muted ? 0 : 1); // new controller starts with the global flag (re-applied once initialized)

    void track() {
      if (!c.value.isInitialized) return;
      final s = c.value.position.inMilliseconds / 1000.0;
      if (s > (_maxSeconds[index] ?? 0)) _maxSeconds[index] = s;
    }

    _trackers[index] = track;
    c.addListener(track);

    void onReady() {
      // Window may have moved on (controller already disposed / replaced).
      if (_disposed || _ctrls[index] != c) return;
      c.setLooping(true);
      c.setVolume(_muted ? 0 : 1);
      _applyPlayState();
      notifyListeners();
    }

    if (c.value.isInitialized) {
      onReady();
    } else {
      c.initialize().then((_) => onReady()).catchError((Object _) {
        if (_disposed || _ctrls[index] != c) return;
        _failed.add(index);
        notifyListeners();
      });
    }
  }

  void _disposeAt(int index) {
    final c = _ctrls.remove(index);
    final t = _trackers.remove(index);
    _maxSeconds.remove(index);
    if (c == null) return;
    if (t != null) c.removeListener(t);
    c.dispose();
  }

  @override
  void dispose() {
    if (_disposed) return;
    _disposed = true;
    for (final i in _ctrls.keys.toList()) {
      _disposeAt(i);
    }
    if (_primedUrl != null) FeedVideoPreloader.instance.release(_primedUrl!);
    _primedUrl = null;
    super.dispose();
  }
}
