import 'package:video_player/video_player.dart';

/// TASK G17 (growth_and_feature_tasks.md) — feed video pre-buffering.
///
/// Problem this fixes: scrolling the feed felt laggy the instant a
/// video-bearing post reached the middle of the screen, because
/// `_MediaCarousel.initState()` (in `home.dart`) only ever *started*
/// creating + initializing its `VideoPlayerController` once that post's
/// widget was actually built — i.e. once the post was already on screen.
/// The first frames of a freshly-scrolled-to video always waited for a
/// brand new connection plus however much buffering the player needed,
/// with zero head start.
///
/// This is a tiny app-wide cache of "primed" controllers, keyed by video
/// URL. `preload(url)` creates a controller, mutes it, and calls
/// `initialize()` (which does the actual network buffering) but never
/// plays it. Whoever ends up building that same video shortly after
/// (`take(url)`) gets the already-buffering-or-buffered controller for
/// free instead of starting from zero.
///
/// REELS (P12) extension — backwards compatible:
///  * Every cached URL now belongs to an *owner* (a caller name). Each owner
///    has its OWN cap, so Reels priming (cap 1) can never evict / be evicted
///    by Home's preloads (cap 2).
///  * `preload(url)` with no extra arguments behaves EXACTLY as before
///    (owner = [homeOwner], cap = 2), so `home.dart` needs no change.
///  * [release] lets a caller drop a primed-but-unused URL right away
///    (e.g. Reels user swiped past it) instead of waiting for eviction.
class FeedVideoPreloader {
  FeedVideoPreloader._();
  static final FeedVideoPreloader instance = FeedVideoPreloader._();

  /// Owner name used when a caller doesn't pass one (Home feed).
  static const String homeOwner = 'home';

  /// Owner name used by the Reels player pool.
  static const String reelsOwner = 'reels';

  /// Default per-owner cap — Home's original value, untouched.
  static const int _maxCached = 2;

  final Map<String, VideoPlayerController> _ready = {};
  final Map<String, Future<void>> _pending = {};
  final Map<String, String> _ownerOf = {}; // url -> owner (ready + pending)
  final Map<String, int> _capOf = {}; // owner -> cap from its latest preload()
  final Map<String, List<String>> _order = {}; // owner -> urls, oldest first (ready only)
  final Set<String> _cancelled = {}; // pending urls released before init finished

  /// Starts buffering [url] in the background if it isn't already
  /// cached/pending. Safe to call repeatedly (e.g. once per post as it
  /// scrolls into view) — a no-op once a preload for that URL is already
  /// underway or done.
  ///
  /// [owner] / [maxCached]: per-caller bucket and its cap. Omit both for
  /// the original Home behaviour (cap 2).
  void preload(String url, {String owner = homeOwner, int? maxCached}) {
    if (url.isEmpty) return;
    _capOf[owner] = (maxCached ?? _maxCached).clamp(1, 8);

    // Released while still buffering, and now wanted again: keep that same
    // in-flight controller instead of starting a second connection.
    if (_cancelled.remove(url)) {
      _ownerOf[url] = owner;
      return;
    }
    if (_ready.containsKey(url) || _pending.containsKey(url)) return;

    final controller = VideoPlayerController.networkUrl(Uri.parse(url));
    _ownerOf[url] = owner;
    _pending[url] = controller.initialize().then((_) {
      _pending.remove(url);
      // release() was called while this was still buffering.
      if (_cancelled.remove(url)) {
        _ownerOf.remove(url);
        controller.dispose();
        return;
      }
      // Rare race: something else already served this URL from a fresh
      // controller before this preload finished. Don't leak this one.
      if (_ready.containsKey(url)) {
        controller.dispose();
        return;
      }
      controller.setVolume(0);
      _ready[url] = controller;
      final o = _ownerOf[url] ?? owner;
      (_order[o] ??= []).add(url);
      _evictIfNeeded(o);
    }).catchError((_) {
      // Best-effort only. A failed preload just means whoever calls
      // take() later gets a cache miss and creates its own controller,
      // exactly as if this cache didn't exist.
      _pending.remove(url);
      _cancelled.remove(url);
      _ownerOf.remove(url);
      controller.dispose();
    });
  }

  /// Hands over a primed, already-initialized controller for [url] if one
  /// is ready, removing it from the cache — the caller now owns its full
  /// lifecycle (play/pause/dispose). Returns null on a cache miss (never
  /// requested, still buffering, or already handed out) so the caller can
  /// fall back to creating its own controller as before this cache existed.
  VideoPlayerController? take(String url) {
    final c = _ready.remove(url);
    if (c != null) {
      final o = _ownerOf.remove(url);
      if (o != null) _order[o]?.remove(url);
    }
    return c;
  }

  /// Drops [url] from the cache right now: a ready controller is disposed,
  /// one still buffering is disposed the moment its initialize() finishes.
  /// No-op if the URL was never primed or was already [take]n (whoever took
  /// it owns it). Safe to call repeatedly.
  void release(String url) {
    final c = _ready.remove(url);
    if (c != null) {
      final o = _ownerOf.remove(url);
      if (o != null) _order[o]?.remove(url);
      c.dispose();
      return;
    }
    if (_pending.containsKey(url)) _cancelled.add(url);
  }

  /// True when a primed, initialized controller for [url] is waiting.
  bool isReady(String url) => _ready.containsKey(url);

  void _evictIfNeeded(String owner) {
    final list = _order[owner];
    if (list == null) return;
    final cap = _capOf[owner] ?? _maxCached;
    while (list.length > cap) {
      final oldest = list.removeAt(0);
      _ownerOf.remove(oldest);
      _ready.remove(oldest)?.dispose();
    }
  }

  /// Drops every cached controller. Not required for normal feed use (the
  /// caps already bound memory), but available for logout/session-reset
  /// so a stale session's buffered video doesn't linger in memory.
  void clear() {
    for (final c in _ready.values) {
      c.dispose();
    }
    _ready.clear();
    _order.clear();
    _ownerOf.removeWhere((url, _) => !_pending.containsKey(url));
    // Still-buffering controllers are disposed as soon as they finish.
    _cancelled.addAll(_pending.keys);
  }
}
