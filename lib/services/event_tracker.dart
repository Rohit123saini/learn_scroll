// ============================================================
// C2-FE — EventTracker: batched analytics events (impression / dwell / tap / skip)
//
//   EventTracker.instance.track('tap', post.id, 0);                       // raw event
//   EventTracker.instance.onVisibility(post.id, info.visibleFraction);    // from VisibilityDetector
//   EventTracker.instance.endSurface('feed');                             // screen dispose
//
// Backend (C1-BE): `POST /post/events/` body {"events": [{post_id, event_type,
// surface, dwell_ms}]}, max 100 per request, all-or-nothing validation (one bad
// event => 400 for the whole batch), throttle scope "post_events".
//
// * Events collect in memory; flush every 10 s, as soon as 20 are queued, and
//   when the app goes to background / is detached (plus `endSurface()` on dispose).
// * Offline / 401 / 429 / 5xx / timeout => the events stay queued (and are mirrored
//   to SharedPreferences so an app kill does not lose them); retried with backoff
//   on the next tick. Other 4xx (bad payload) => that batch is dropped, otherwise
//   it would be retried forever.
// * `impression` is sent at most once per (surface, post) per app session.
// * Dwell is measured only while a post is >= 50% visible AND the app / screen is
//   active (suspended on background and while another route covers Reels).
// * Best effort, never throws into UI code.
// ============================================================

import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart' show debugPrint, kDebugMode;
import 'package:flutter/widgets.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../utils/api.dart';
import 'auth_service.dart';

class EventType {
  static const impression = 'impression';
  static const dwell = 'dwell';
  static const tap = 'tap';
  static const skip = 'skip';
  static const all = {impression, dwell, tap, skip};
}

class EventSurface {
  static const feed = 'feed';
  static const reels = 'reels';
  static const profile = 'profile';
  static const explore = 'explore';
  static const all = {feed, reels, profile, explore};
}

class _Event {
  final String type;
  final String postId;
  final String surface;
  final int dwellMs;
  const _Event(this.type, this.postId, this.surface, this.dwellMs);

  // dwell_ms only matters for dwell / skip (backend forces 0 for the rest).
  Map<String, dynamic> toJson() => {
        'post_id': postId,
        'event_type': type,
        'surface': surface,
        if (type == EventType.dwell || type == EventType.skip) 'dwell_ms': dwellMs,
      };

  static _Event? fromJson(dynamic j) {
    if (j is! Map) return null;
    final t = j['event_type'], p = j['post_id'], s = j['surface'];
    if (t is! String || p is! String || s is! String) return null;
    if (!EventType.all.contains(t) || !EventSurface.all.contains(s)) return null;
    final d = j['dwell_ms'];
    return _Event(t, p, s, d is int ? d : 0);
  }
}

/// One continuous "post is on screen" stretch.
class _Visit {
  final String surface;
  final String postId;
  final Stopwatch sw = Stopwatch();
  bool dwellSent = false; // a dwell segment already went out (background split)
  _Visit(this.surface, this.postId);

  int takeMs() {
    final ms = sw.elapsedMilliseconds;
    sw
      ..stop()
      ..reset();
    return ms;
  }
}

enum _Send { ok, drop, retry }

class EventTracker with WidgetsBindingObserver {
  EventTracker._();
  static final EventTracker instance = EventTracker._();

  // ---- tuning knobs ----
  static const int flushEvery = 20; // events
  static const Duration flushInterval = Duration(seconds: 10);
  static const int _maxBatch = 100; // backend limit per request
  static const int _maxQueue = 500; // oldest dropped beyond this
  static const int _maxDwellMs = 60 * 60 * 1000; // backend rejects > 1h (and then the whole batch)
  static const int _maxImpressionKeys = 5000;
  static const double visibleThreshold = 0.5;
  static const int skipBelowMs = 1000; // left the screen faster than this => 'skip' instead of 'dwell'
  static const String _prefsKey = 'event_tracker_queue_v1';

  static final RegExp _uuid =
      RegExp(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$');

  final List<_Event> _queue = [];
  final Set<String> _impressed = {}; // "surface:postId", once per session
  final Map<String, _Visit> _active = {}; // "surface:postId" -> visit in progress
  final Set<String> _suspended = {}; // surfaces whose dwell timers are paused ('*' = whole app)

  bool _inited = false;
  bool _flushing = false;
  bool _flushAgain = false;
  bool _persistedNonEmpty = false;
  Timer? _timer;
  DateTime _nextAttempt = DateTime.fromMillisecondsSinceEpoch(0);
  Duration _backoff = flushInterval;

  // ------------------------------------------------------------ setup

  /// Idempotent. Called lazily by [track] / [onVisibility], so `main()` does not
  /// need to call it (calling it early only restores an offline queue sooner).
  void init() {
    if (_inited) return;
    _inited = true;
    WidgetsBinding.instance.addObserver(this);
    _timer = Timer.periodic(flushInterval, (_) => _maybeFlush());
    unawaited(_restore());
  }

  /// Drop everything (queue, dedupe state, persisted copy) — call on logout so one
  /// account's events are never sent under the next account's token.
  Future<void> clear() async {
    _queue.clear();
    _impressed.clear();
    _active.clear();
    _suspended.clear();
    await _persist();
  }

  // ------------------------------------------------------------ public API

  /// Queue one event. [dwellMs] is used for `dwell` / `skip`, ignored otherwise.
  void track(String type, String postId, int dwellMs, {String surface = EventSurface.feed}) {
    if (!EventType.all.contains(type) || !EventSurface.all.contains(surface)) return;
    // One bad id would make the backend 400 the whole batch -> validate here.
    if (!_uuid.hasMatch(postId)) return;
    _ensureInit();

    if (type == EventType.impression) {
      final key = '$surface:$postId';
      if (!_impressed.add(key)) return; // already counted this session
      if (_impressed.length > _maxImpressionKeys) _impressed.remove(_impressed.first);
    }

    final ms = dwellMs < 0 ? 0 : (dwellMs > _maxDwellMs ? _maxDwellMs : dwellMs);
    _queue.add(_Event(type, postId, surface, ms));
    while (_queue.length > _maxQueue) {
      _queue.removeAt(0);
    }
    if (_queue.length >= flushEvery) _maybeFlush();
  }

  /// Feed this from `VisibilityDetector.onVisibilityChanged` (every call, with the
  /// raw `visibleFraction`). Emits the impression when the post first becomes
  /// >= 50% visible and a dwell (or skip) when it stops being visible.
  void onVisibility(String postId, double visibleFraction, {String surface = EventSurface.feed}) {
    _ensureInit();
    final key = '$surface:$postId';
    final visit = _active[key];
    if (visibleFraction >= visibleThreshold) {
      if (visit != null) return;
      final v = _Visit(surface, postId);
      if (!_isSuspended(surface)) v.sw.start();
      _active[key] = v;
      track(EventType.impression, postId, 0, surface: surface);
    } else if (visit != null) {
      _endVisit(key);
    }
  }

  /// Pause dwell timers for one [surface] (or everything when null) — e.g. another
  /// route is covering Reels. Emits the dwell collected so far.
  void suspendDwell({String? surface}) {
    _suspended.add(surface ?? '*');
    for (final v in _active.values) {
      if (surface != null && v.surface != surface) continue;
      _closeSegment(v);
    }
  }

  void resumeDwell({String? surface}) {
    _suspended.remove(surface ?? '*');
    for (final v in _active.values) {
      if (surface != null && v.surface != surface) continue;
      if (!_isSuspended(v.surface) && !v.sw.isRunning) v.sw.start();
    }
  }

  /// A screen showing [surface] is going away: close its open visits (emits the
  /// dwells synchronously) and flush.
  void endSurface(String surface) {
    for (final key in _active.keys.where((k) => _active[k]!.surface == surface).toList()) {
      _endVisit(key);
    }
    _suspended.remove(surface);
    unawaited(flush(force: true));
  }

  // ------------------------------------------------------------ visits

  bool _isSuspended(String surface) => _suspended.contains('*') || _suspended.contains(surface);

  // Suspend path: emit what we have, keep the visit open for when we resume.
  void _closeSegment(_Visit v) {
    final ms = v.takeMs();
    if (ms > 0) {
      track(EventType.dwell, v.postId, ms, surface: v.surface);
      v.dwellSent = true;
    }
  }

  void _endVisit(String key) {
    final v = _active.remove(key);
    if (v == null) return;
    final ms = v.takeMs();
    if (v.dwellSent) {
      if (ms > 0) track(EventType.dwell, v.postId, ms, surface: v.surface);
    } else if (ms >= skipBelowMs) {
      track(EventType.dwell, v.postId, ms, surface: v.surface);
    } else {
      track(EventType.skip, v.postId, ms, surface: v.surface);
    }
  }

  // ------------------------------------------------------------ lifecycle

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    switch (state) {
      case AppLifecycleState.resumed:
        resumeDwell();
        break;
      case AppLifecycleState.paused:
      case AppLifecycleState.detached:
        suspendDwell();
        unawaited(_persist()); // in case the OS kills us before the flush lands
        unawaited(flush(force: true));
        break;
      default:
        break; // inactive / hidden: transient, `paused` follows if it is real
    }
  }

  // ------------------------------------------------------------ flushing

  void _ensureInit() {
    if (!_inited) init();
  }

  void _maybeFlush() {
    if (_queue.isEmpty || DateTime.now().isBefore(_nextAttempt)) return;
    unawaited(flush());
  }

  /// Send everything queued, in batches of <= 100. [force] ignores the retry backoff.
  Future<void> flush({bool force = false}) async {
    if (_queue.isEmpty) return;
    if (!force && DateTime.now().isBefore(_nextAttempt)) return;
    if (_flushing) {
      _flushAgain = true;
      return;
    }
    _flushing = true;
    try {
      do {
        _flushAgain = false;
        while (_queue.isNotEmpty) {
          final batch = _queue.take(_maxBatch).toList();
          final result = await _send(batch);
          if (result == _Send.retry) {
            _nextAttempt = DateTime.now().add(_backoff);
            final next = _backoff * 2;
            _backoff = next > const Duration(minutes: 5) ? const Duration(minutes: 5) : next;
            _flushAgain = false;
            break;
          }
          // ok / drop: take the batch out by identity (queue may have been trimmed meanwhile)
          final sent = batch.toSet();
          _queue.removeWhere(sent.contains);
          _backoff = flushInterval;
          _nextAttempt = DateTime.fromMillisecondsSinceEpoch(0);
        }
      } while (_flushAgain && _queue.isNotEmpty);
    } finally {
      _flushing = false;
      unawaited(_persist());
    }
  }

  Future<_Send> _send(List<_Event> batch) async {
    try {
      final token = await AuthService.getToken();
      if (token == null) return _Send.retry; // logged out / session gone: keep, don't spin
      final res = await http
          .post(
            Uri.parse('${Api.baseUrl}/post/events/'),
            headers: {
              'Content-Type': 'application/json',
              'Authorization': 'Bearer $token',
            },
            body: jsonEncode({'events': batch.map((e) => e.toJson()).toList()}),
          )
          .timeout(const Duration(seconds: 15));
      final code = res.statusCode;
      if (code >= 200 && code < 300) return _Send.ok;
      if (code == 401 || code == 408 || code == 429 || code >= 500) return _Send.retry;
      if (kDebugMode) debugPrint('EventTracker: dropping batch of ${batch.length}, HTTP $code ${res.body}');
      return _Send.drop; // other 4xx: payload will never be accepted
    } catch (e) {
      if (kDebugMode) debugPrint('EventTracker: send failed ($e), will retry');
      return _Send.retry; // offline, DNS, timeout ...
    }
  }

  // ------------------------------------------------------------ persistence

  Future<void> _persist() async {
    try {
      if (_queue.isEmpty && !_persistedNonEmpty) return;
      final prefs = await SharedPreferences.getInstance();
      if (_queue.isEmpty) {
        await prefs.remove(_prefsKey);
        _persistedNonEmpty = false;
      } else {
        await prefs.setString(
          _prefsKey,
          jsonEncode({
            'uid': prefs.getString('user_id'), // saved at login (ApiService._saveSession)
            'events': _queue.map((e) => e.toJson()).toList(),
          }),
        );
        _persistedNonEmpty = true;
      }
    } catch (_) {}
  }

  Future<void> _restore() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_prefsKey);
      if (raw == null) return;
      final data = jsonDecode(raw);
      await prefs.remove(_prefsKey);
      if (data is! Map) return;
      // Events recorded under another account must not be sent as this one.
      final uid = data['uid'];
      final current = prefs.getString('user_id');
      if (uid != null && current != null && uid != current) return;
      final list = data['events'];
      if (list is! List) return;
      final restored = list.map(_Event.fromJson).whereType<_Event>().toList();
      _queue.insertAll(0, restored); // older than anything tracked since start-up
      while (_queue.length > _maxQueue) {
        _queue.removeAt(0);
      }
      _persistedNonEmpty = false;
      if (_queue.isNotEmpty) unawaited(flush());
    } catch (_) {}
  }
}