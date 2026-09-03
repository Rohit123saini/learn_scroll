// lib/liveclass/screens/classroom_detail_screen.dart
//
// Screen 2 — Classroom Detail (see LIVECLASS_SCREEN_ARCHITECTURE.md §2).
//
// On open, fires 4 calls in parallel:
//   GET classrooms/{id}/          basic info
//   GET classrooms/{id}/my-pass/  access_level -> decides which UI renders
//   GET classrooms/{id}/stats/    rating/enrolled/timing/holidays
//   GET wishlist-classrooms/      to pre-check the heart icon
//
// UI switches entirely on my-pass's `access_level`:
//   owner/admin -> manage icon in the app bar (Edit/Schedule/Passes/Staff/
//                  Close Classroom) + full tabs, no bottom CTA bar
//   active      -> full tabs + "Enter Class" bottom bar
//   expired     -> full tabs + "Renew Pass" bottom bar
//   pending     -> About + Reviews only + disabled "Request Sent —
//                  Waiting for Approval" bar (tap offers Cancel Request).
//                  (Flutter Phase 1, item 2.) Flips to active/none live via
//                  LiveClassUserSocket's `join_request.decided` event
//                  without reopening this screen — see initState below.
//   none        -> About + Reviews only + "Request to Join" bottom bar
//
// Screens this pushes into: Edit Classroom, Schedule Manager, Passes,
// Staff, Certificates, Join Requests Inbox, Request/Renew Pass, and the
// Live Session Room — all wired to the real screens below.

import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart' hide MaterialType;
import 'package:flutter/services.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';
import 'package:file_selector/file_selector.dart';
import 'package:intl/intl.dart';
import 'package:share_plus/share_plus.dart';

import '../../services/auth_service.dart';
import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import '../utils/liveclass_datetime.dart';
import 'banned_students_screen.dart';
import 'chat_message_reports_screen.dart';
import 'classroom_form_screen.dart';
import 'classroom_recordings_screen.dart';
import 'request_join_screen.dart';
import 'schedule_manager_screen.dart';
import 'sessions_list_screen.dart';
import 'teacher_earnings_screen.dart';
// FIX (CRITICAL — build-breaking missing class, production readiness
// audit): this screen's _openRequestJoin() (below) has always
// constructed RequestJoinScreen(classroomId: ..., classroom: ...), but
// no file in the module ever defined that class, so every build failed
// with "The method 'RequestJoinScreen' isn't defined". The old
// request_join_screen.dart on disk was actually a stale duplicate of
// join_requests_screen.dart's `class JoinRequestsScreen` (same
// .inbox()/.mine() constructors) — never imported anywhere, and if it
// HAD been imported alongside join_requests_screen.dart below it would
// have been an ambiguous-import compile error on its own (two files
// defining the same class name). request_join_screen.dart now defines
// the actual missing RequestJoinScreen class instead (a student-facing
// pass-selection + coupon + message form that POSTs join-requests/) —
// a genuinely different class name from JoinRequestsScreen, so this
// import is safe alongside join_requests_screen.dart below with no
// ambiguity.
import 'join_requests_screen.dart';
import 'live_session_screen.dart';
import 'pass_management_screen.dart';
import 'staff_management_screen.dart';
import 'certificates_screen.dart';
import 'coupons_screen.dart';
import 'holidays_screen.dart';
import 'materials_screen.dart';
import 'notice_board_screen.dart';
import 'poll_templates_screen.dart';
import 'doubts_screen.dart';
import 'assignments_screen.dart';
import 'classroom_purchases_screen.dart';
import 'waitlist_screen.dart';

// FIX (design-system drift): this file already imported liveclass_theme.dart
// but ALSO kept its own locally-duplicated literal color consts — two
// sources of truth for the same three colors. Now aliased to the shared
// tokens so there's exactly one place the palette lives. Zero-risk: same
// values, every existing _kNavy/_kBg/_kGradient call site is unchanged.
const _kNavy = LiveClassColors.navy;
const _kBg = LiveClassColors.bg;
const _kGradient = LiveClassColors.gradient;

// ---------------------------------------------------------------------------
// Date/time helpers
//
// FIX (i18n / timezone audit — see utils/liveclass_datetime.dart for the
// full writeup): this used to hand-roll English-only month names AND
// never called `.toLocal()` before reading `.hour`/`.day` off a `DateTime`
// parsed from the API's UTC timestamps — every session/schedule time
// rendered in English, in UTC, for every viewer regardless of device
// locale or timezone. Now delegates to the shared, locale + `.toLocal()`
// aware helpers (`liveClassFmtDate`/`liveClassFmtDateTime`), and the
// `LiveClassDateTime` schedule-conversion helper for the recurring
// `ClassSchedule` timezone display below.
String _fmtDate(DateTime d, [BuildContext? context]) => liveClassFmtDate(d, context);
String _fmtTime(DateTime d, [BuildContext? context]) => DateFormat.jm(context == null ? null : Localizations.maybeLocaleOf(context)?.toString()).format(d.toLocal());
String _fmtDateTime(DateTime d, [BuildContext? context]) => liveClassFmtDateTime(d, context);

// ===========================================================================
// SCREEN
// ===========================================================================
class ClassroomDetailScreen extends StatefulWidget {
  final int classroomId;
  /// Optional — pass the card's [Classroom] from Explore for an instant
  /// first paint; the screen still refetches fresh data on open.
  final Classroom? initial;
  const ClassroomDetailScreen({super.key, required this.classroomId, this.initial});

  @override
  State<ClassroomDetailScreen> createState() => _ClassroomDetailScreenState();
}

class _ClassroomDetailScreenState extends State<ClassroomDetailScreen> {
  Classroom? _classroom;
  MyPassStatus? _myPass;
  ClassroomStats? _stats;
  int? _wishlistEntryId;
  // NOTE (fix): a teacher had zero indication anywhere on this screen that
  // a student had actually requested to join — the only way to find out
  // was to remember to open Settings -> "Join Requests" and check. This
  // surfaces the pending count as a badge instead.
  int _pendingRequestCount = 0;

  bool _loading = true;
  String? _error;
  bool _actionBusy = false;

  // REALTIME (Flutter Phase 1, items 2 & 3): user-scoped socket so a
  // teacher's pending badge and a student's own pending->active/none
  // transition update live, without reopening this screen. CONFIRMED
  // (realtime fix pass): the backend route/event this expects
  // (`ws/liveclass/user/`, `broadcast_to_user()`, `UserConsumer` — see
  // LiveClassUserSocket's header comment in liveclass_api_service.dart)
  // now exists. If the channel layer or socket is ever unreachable this
  // still degrades silently to the existing load-on-open behaviour —
  // that fallback is a deliberate safety net, not a sign anything here
  // is unwired.
  final LiveClassUserSocket _userSocket = LiveClassUserSocket();
  StreamSubscription<LiveSocketEvent>? _userSocketSub;

  // NOTE (fix — Phase 4, item 3 / classroom stats push): `_stats`
  // (enrolled_count, rating) used to be refreshed ONLY by `_load()` (first
  // open / manual pull-to-refresh) or, in a stop-gap fix, by a plain
  // `Timer.periodic(30s)` GET — that stop-gap worked but re-hit the network
  // every 30s regardless of whether anything had actually changed, purely
  // background battery/network cost for a screen that might sit open for a
  // long time with the stats never moving.
  //
  // Now uses a per-classroom WebSocket push (`LiveClassClassroomSocket`,
  // same pattern as `_userSocket` above — see that class's header comment
  // in liveclass_api_service.dart for the backend piece it needs) so a GET
  // only fires when a `classroom.stats` event actually arrives. The
  // interval-based `_statsTimer` stays, but as a much-longer backstop
  // (5 min, not 30s) for the same "channel layer hiccup should still
  // degrade gracefully, not go stale forever" reason every other realtime
  // feature in this app keeps a poll/reopen fallback.
  late final LiveClassClassroomSocket _classroomSocket = LiveClassClassroomSocket(widget.classroomId);
  StreamSubscription<LiveSocketEvent>? _classroomSocketSub;
  Timer? _statsTimer;

  @override
  void initState() {
    super.initState();
    _classroom = widget.initial;
    _load();
    _userSocket.connect();
    _userSocketSub = _userSocket.events.listen(_onUserSocketEvent);
    _classroomSocket.connect();
    _classroomSocketSub = _classroomSocket.events.listen(_onClassroomSocketEvent);
    // Backstop only — see the field comment above for why this is now 5
    // minutes instead of the old 30s poll.
    _statsTimer = Timer.periodic(const Duration(minutes: 5), (_) => _refreshStats());
  }

  void _onClassroomSocketEvent(LiveSocketEvent e) {
    if (!mounted) return;
    if (e.type == 'classroom.stats' && e.data['classroom_id'] == widget.classroomId) {
      // Server already computed the fresh numbers — apply them directly
      // instead of firing a follow-up GET (see _broadcast_classroom_stats
      // in models.py for the exact payload shape).
      final data = e.data;
      setState(() {
        _stats = ClassroomStats(
          ratingAvg: (data['rating_avg'] as num?)?.toDouble() ?? _stats?.ratingAvg ?? 0,
          ratingCount: data['rating_count'] as int? ?? _stats?.ratingCount ?? 0,
          enrolledCount: data['enrolled_count'] as int? ?? _stats?.enrolledCount ?? 0,
          weeklyTiming: _stats?.weeklyTiming ?? '',
          upcomingHolidays: _stats?.upcomingHolidays ?? const [],
        );
      });
    }
  }

  @override
  void dispose() {
    _statsTimer?.cancel();
    _userSocketSub?.cancel();
    _userSocket.dispose();
    _classroomSocketSub?.cancel();
    _classroomSocket.dispose();
    super.dispose();
  }

  // Lightweight counterpart to `_load()` — just the stats GET, no loading
  // spinner, no touching `_classroom`/`_myPass`. Silent on failure, same
  // "never block/interrupt the screen over this" reasoning `_load()` already
  // uses for the wishlist lookup.
  Future<void> _refreshStats() async {
    if (!mounted || _loading) return;
    try {
      final stats = await LiveClassApi.classrooms.stats(widget.classroomId);
      if (!mounted) return;
      setState(() => _stats = stats);
    } catch (_) {
      // Best-effort background refresh — next tick will just try again.
    }
  }

  void _onUserSocketEvent(LiveSocketEvent e) {
    if (!mounted) return;
    switch (e.type) {
      case 'join_request.created':
        // Teacher-side: someone just raised a request against THIS
        // classroom. Bump the badge without a re-fetch; a full
        // `_loadPendingRequestCount()` still runs on manual refresh/reopen
        // as a correctness backstop in case an event is ever missed.
        if (_canManage && e.data['classroom_id'] == widget.classroomId) {
          final serverCount = e.data['pending_count'];
          setState(() {
            _pendingRequestCount = serverCount is int ? serverCount : _pendingRequestCount + 1;
          });
        }
        break;
      case 'join_request.decided':
        // Student-side: MY pending request on this classroom was just
        // accepted/rejected. Flip the CTA immediately instead of leaving
        // "Request Sent — Waiting for Approval" showing until next reopen.
        if (e.data['classroom_id'] == widget.classroomId &&
            _myPass?.pendingRequestId != null &&
            e.data['id'] == _myPass!.pendingRequestId) {
          _load();
        }
        break;
      case 'staff.added':
        // NOTE (fix — Phase 4, item 2): I was just promoted to staff on
        // THIS classroom (see ClassroomStaffViewSet.perform_create's
        // _safe_broadcast_to_user push in views.py). Previously
        // `_pendingRequestCount` only got fetched once, inside `_load()`,
        // gated on whatever `_canManage` was AT THAT MOMENT — if the
        // promotion happened while this screen was already open, the
        // manage-tier UI (and the join-request badge with it) wouldn't
        // show up until the user backed out and reopened the classroom.
        // Re-running `_load()` here recomputes `_canManage` fresh from the
        // server and — same as any other load — fires
        // `_loadPendingRequestCount()` immediately if it's now true.
        if (e.data['classroom_id'] == widget.classroomId) {
          _load();
        }
        break;
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        LiveClassApi.classrooms.detail(widget.classroomId),
        LiveClassApi.classrooms.myPass(widget.classroomId),
        LiveClassApi.classrooms.stats(widget.classroomId),
      ]);
      int? wishlistId;
      try {
        final wishlist = await LiveClassApi.wishlist.list();
        for (final w in wishlist.results) {
          if (w.classroom.id == widget.classroomId) {
            wishlistId = w.id;
            break;
          }
        }
      } catch (_) {
        // wishlist state is a nice-to-have — never block the screen on it
      }
      if (!mounted) return;
      final myPass = results[1] as MyPassStatus;
      final canManage = myPass.accessLevel == 'owner' || myPass.accessLevel == 'admin';
      setState(() {
        _classroom = results[0] as Classroom;
        _myPass = myPass;
        _stats = results[2] as ClassroomStats;
        _wishlistEntryId = wishlistId;
        _loading = false;
      });
      if (canManage) _loadPendingRequestCount();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load the classroom.';
      });
    }
  }

  Future<void> _loadPendingRequestCount() async {
    try {
      final res = await LiveClassApi.joinRequests.list(classroomId: widget.classroomId, status: JoinRequestStatus.pending);
      if (!mounted) return;
      setState(() => _pendingRequestCount = res.count);
    } catch (_) {
      // Badge is a nice-to-have — never block the screen on it.
    }
  }

  String get _accessLevel => _myPass?.accessLevel ?? 'none';
  bool get _canManage => _accessLevel == 'owner' || _accessLevel == 'admin';
  bool get _hasFullAccess => _canManage || _accessLevel == 'active' || _accessLevel == 'expired';
  bool get _everHadAccess => _accessLevel != 'none';
  // Flutter Phase 1, item 2: caller has a join request sitting with the
  // teacher right now.
  bool get _isPending => _accessLevel == 'pending';

  // -------------------------------------------------------------------
  // Wishlist
  // -------------------------------------------------------------------
  // NOTE (fix — Phase 4, double-tap guard): _wishlistEntryId is set
  // optimistically BEFORE the await below, so a second tap landing while
  // the first request is still in flight used to read the already-flipped
  // optimistic state, fire its own add()/remove() against the server on
  // top of the first one, and then race it back — whichever response lands
  // second silently overwrites the other's result in state (e.g. a fast
  // add-then-remove double-tap can leave the heart showing "wishlisted"
  // even though the server's last-committed state is "removed", or vice
  // versa). `_wishlistBusy` blocks re-entrancy for the duration of exactly
  // one in-flight call, same guard shape as `_actionBusy` already uses
  // elsewhere on this screen for Enter Class / Cancel Request.
  bool _wishlistBusy = false;

  Future<void> _toggleWishlist() async {
    if (_wishlistBusy) return;
    final wasWishlisted = _wishlistEntryId != null;
    final previousId = _wishlistEntryId;
    setState(() {
      _wishlistBusy = true;
      _wishlistEntryId = wasWishlisted ? null : -1; // optimistic
    });
    try {
      if (wasWishlisted) {
        await LiveClassApi.wishlist.remove(previousId!);
      } else {
        final item = await LiveClassApi.wishlist.add(widget.classroomId);
        if (mounted) setState(() => _wishlistEntryId = item.id);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _wishlistEntryId = previousId);
      _snack(e is LiveClassApiException ? e.message : 'Something went wrong');
    } finally {
      if (mounted) setState(() => _wishlistBusy = false);
    }
  }

  // -------------------------------------------------------------------
  // Enter Class
  // -------------------------------------------------------------------
  // NOTE (fix — simplification): this used to be up to 4 sequential calls
  // (list live -> list scheduled -> client-side sort/decide -> maybe a
  // confirm dialog -> create -> join) spread across this method plus
  // _offerStartClassNow/_startClassNow below. All of that now lives
  // server-side behind ONE call — see ClassroomViewSet.start_or_join() in
  // views.py — so a flaky network only has one request to retry, and a
  // teacher's "class always just works" comes from the server's own
  // is_host/is_manager check instead of this screen re-deriving it.
  Future<void> _enterClass() async {
    if (_actionBusy) return;
    setState(() => _actionBusy = true);
    try {
      final result = await LiveClassApi.classrooms.startOrJoin(widget.classroomId);
      if (!mounted) return;
      if (result.waitlisted) {
        _snack('Session full — you have been added to the waitlist.');
        return;
      }
      // NOTE (fix): start-or-join always attaches the actual ClassSession
      // now (see views.py's start_or_join) — not just on the
      // freshly-created-ad-hoc-session path — so this is never null on a
      // successful (non-waitlisted) response.
      final session = result.session;
      if (session == null) {
        // Defensive only — shouldn't happen given the backend contract
        // above; fail safely instead of pushing a screen with a bogus id.
        _snack('Something went wrong, please try again.');
        return;
      }
      Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => LiveSessionScreen(sessionId: session.id, session: session, initialResult: result),
        ),
      );
      if (result.startedNew && mounted) _snack('Class started.');
    } on LiveClassApiException catch (e) {
      if (e.statusCode == 403) {
        _showPassRequiredDialog();
      } else if (e.statusCode == 404 && e.body is Map && e.body['no_session'] == true) {
        await _showNoSessionMessage();
      } else {
        _snack(e.message);
      }
    } catch (_) {
      _snack('Something went wrong, please try again.');
    } finally {
      if (mounted) setState(() => _actionBusy = false);
    }
  }

  // Student (or a teacher who genuinely has no schedule set up) — show the
  // real next-class info instead of a generic "check the schedule tab".
  Future<void> _showNoSessionMessage() async {
    String detail = 'There is no live class right now.';
    try {
      final schedules = await LiveClassApi.schedules.list(classroomId: widget.classroomId);
      final active = schedules.results.where((s) => s.isActive).toList();
      if (active.isNotEmpty) {
        final timing = _stats?.weeklyTiming ?? '';
        detail = timing.isNotEmpty
            ? 'There is no live class right now. Class schedule: $timing.'
            : 'There is no live class right now — the next class will start as per the schedule.';
      } else {
        detail = 'No schedule has been set for this classroom yet.';
      }
    } catch (_) {
      // fall back to the generic message above
    }
    if (!mounted) return;
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Class Hasn\'t Started Yet'),
        content: Text(detail),
        actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('OK'))],
      ),
    );
  }

  void _showPassRequiredDialog() {
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Pass Required'),
        content: const Text('You need a valid pass to enter this class.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
            onPressed: () {
              Navigator.pop(context);
              _openRequestJoin();
            },
            child: const Text('Request to Join'),
          ),
        ],
      ),
    );
  }

  // -------------------------------------------------------------------
  // Close classroom (owner-only, from the manage sheet)
  // -------------------------------------------------------------------
  Future<void> _confirmCloseClassroom() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Close Classroom?'),
        content: const Text(
            'All active students will be refunded for their paid passes and the classroom will be deactivated. This cannot be undone.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Close Classroom', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      final res = await LiveClassApi.classrooms.close(widget.classroomId);
      if (!mounted) return;
      _snack('Classroom closed. ${res['passes_refunded'] ?? 0} pass(es) refunded.');
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not close.');
    }
  }

  // -------------------------------------------------------------------
  // Report
  // -------------------------------------------------------------------
  // -------------------------------------------------------------------
  // Share (Phase 2, item 6) + Refer & Earn (Phase 2, item 9)
  // -------------------------------------------------------------------
  Future<void> _openShareSheet() async {
    ClassroomShareResult? result;
    String? error;
    try {
      result = await LiveClassApi.classrooms.share(widget.classroomId);
    } catch (e) {
      error = e is LiveClassApiException ? e.message : 'Could not create a share link.';
    }
    if (!mounted) return;
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) {
        if (error != null) {
          return Padding(
            padding: const EdgeInsets.all(24),
            child: Text(error!, style: const TextStyle(fontSize: 13.5)),
          );
        }
        final r = result!;
        return Padding(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Share this Classroom', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
              const SizedBox(height: 4),
              Text('${r.shareCount} share${r.shareCount == 1 ? '' : 's'} so far',
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
              const SizedBox(height: 14),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(color: _kBg, borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                child: Text(r.webUrl.isNotEmpty ? r.webUrl : r.shareText, style: const TextStyle(fontSize: 12.5)),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.copy_rounded, size: 16),
                      label: const Text('Copy Link'),
                      onPressed: () async {
                        await Clipboard.setData(ClipboardData(text: r.webUrl.isNotEmpty ? r.webUrl : r.deepLink));
                        _snack('Link copied.');
                      },
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                      icon: const Icon(Icons.ios_share_rounded, size: 16),
                      label: const Text('Share'),
                      onPressed: () async {
                        // NOTE (fix — item 6): now opens the OS's real
                        // native share sheet (WhatsApp/SMS/email/etc.) via
                        // share_plus instead of only ever copying to the
                        // clipboard. Falls back to clipboard-copy if the
                        // platform share sheet itself fails to launch
                        // (e.g. no share targets registered).
                        try {
                          await SharePlus.instance.share(
                            ShareParams(text: r.shareText, subject: 'Join this classroom'),
                          );
                        } catch (_) {
                          await Clipboard.setData(ClipboardData(text: r.shareText));
                          _snack('Share message copied — paste it anywhere.');
                        }
                      },
                    ),
                  ),
                ],
              ),
              if (_classroom?.referralEnabled == true && !_canManage) ...[
                const Divider(height: 28),
                Row(
                  children: [
                    const Icon(Icons.percent_rounded, size: 18, color: LiveClassColors.success),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'Refer & Earn ${_classroom!.referralCommissionPercent.toStringAsFixed(0)}% commission when someone joins through your link.',
                        style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton(
                    onPressed: () {
                      Navigator.pop(ctx);
                      _openReferLink();
                    },
                    child: const Text('Get My Referral Link'),
                  ),
                ),
              ],
            ],
          ),
        );
      },
    );
  }

  Future<void> _openReferLink() async {
    try {
      final link = await LiveClassApi.classrooms.referLink(widget.classroomId);
      if (!mounted) return;
      await showModalBottomSheet<void>(
        context: context,
        backgroundColor: Colors.white,
        shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
        builder: (ctx) => Padding(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Your Referral Link', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
              const SizedBox(height: 4),
              Text('You earn ${link.commissionPercent.toStringAsFixed(0)}% on purchases made through this link.',
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
              const SizedBox(height: 14),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(color: _kBg, borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                child: Text(link.webUrl, style: const TextStyle(fontSize: 12.5)),
              ),
              const SizedBox(height: 12),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                  onPressed: () async {
                    await Clipboard.setData(ClipboardData(text: link.webUrl));
                    _snack('Referral link copied.');
                  },
                  child: const Text('Copy Link'),
                ),
              ),
            ],
          ),
        ),
      );
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not create your referral link.');
    }
  }

  Future<void> _openShareStats() async {
    try {
      final stats = await LiveClassApi.classrooms.shareStats(widget.classroomId);
      if (!mounted) return;
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        backgroundColor: Colors.white,
        shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
        builder: (ctx) => DraggableScrollableSheet(
          initialChildSize: 0.6,
          minChildSize: 0.3,
          maxChildSize: 0.9,
          expand: false,
          builder: (_, scrollCtrl) => ListView(
            controller: scrollCtrl,
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
            children: [
              const Text('Share Insights', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
              const SizedBox(height: 4),
              Text('${stats.shareCount} total share${stats.shareCount == 1 ? '' : 's'}',
                  style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600)),
              if (stats.byChannel.isNotEmpty) ...[
                const SizedBox(height: 14),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: stats.byChannel.entries
                      .map((e) => Chip(label: Text('${e.key}: ${e.value}'), backgroundColor: _kBg))
                      .toList(),
                ),
              ],
              const SizedBox(height: 18),
              const Text('Recent Shares', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13.5)),
              const SizedBox(height: 8),
              if (stats.recent.isEmpty)
                Text('No shares yet.', style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600))
              else
                ...stats.recent.map((log) => ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.share_outlined, size: 18, color: _kNavy),
                      title: Text(log.sharedBy.fullName.isNotEmpty ? log.sharedBy.fullName : log.sharedBy.username,
                          style: const TextStyle(fontSize: 13)),
                      subtitle: Text('${log.channel} · ${liveClassFmtDateTime(log.createdAt)}', style: const TextStyle(fontSize: 11)),
                    )),
            ],
          ),
        ),
      );
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not load share insights.');
    }
  }

  // -------------------------------------------------------------------
  // Reported Messages (Phase 2, item 7)
  // -------------------------------------------------------------------
  void _openChatMessageReports() {
    Navigator.push(context, MaterialPageRoute(builder: (_) => ChatMessageReportsScreen(classroomId: widget.classroomId)))
        .then((_) => _load());
  }

  Future<void> _openReportDialog() async {
    // FIX (memory leak): this controller used to be created here and never
    // disposed — every open+close of this sheet leaked one
    // TextEditingController for the lifetime of the app. try/finally
    // guarantees disposal on every exit path.
    final descCtrl = TextEditingController();
    try {
      await _showReportDialog(descCtrl);
    } finally {
      descCtrl.dispose();
    }
  }

  Future<void> _showReportDialog(TextEditingController descCtrl) async {
    String reason = ReportReason.other;
    final result = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          return Padding(
            padding: EdgeInsets.only(
                left: 20, right: 20, top: 20, bottom: MediaQuery.of(ctx).viewInsets.bottom + 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Report Classroom', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const SizedBox(height: 14),
                _dropdown(
                  value: reason,
                  items: const {
                    ReportReason.scam: 'Scam',
                    ReportReason.notDelivering: 'Not delivering as promised',
                    ReportReason.inappropriate: 'Inappropriate content',
                    ReportReason.other: 'Other',
                  },
                  onChanged: (v) => setSheetState(() => reason = v!),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: descCtrl,
                  maxLines: 3,
                  decoration: _inputDecoration('Describe the issue (optional)'),
                ),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(backgroundColor: Colors.red.shade600, foregroundColor: Colors.white),
                    onPressed: () => Navigator.pop(ctx, true),
                    child: const Padding(padding: EdgeInsets.symmetric(vertical: 12), child: Text('Submit Report')),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );
    if (result != true) return;
    try {
      await LiveClassApi.classroomReports.file(
        classroomId: widget.classroomId,
        reason: reason,
        description: descCtrl.text.trim(),
      );
      _snack('Report submitted. Platform staff will review it.');
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not submit the report.');
    }
  }

  // -------------------------------------------------------------------
  // Manage sheet (owner/admin)
  // -------------------------------------------------------------------
  void _openManageSheet() {
    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.white,
      // NOTE (fix): without isScrollControlled the sheet caps itself at a
      // fraction of the screen and a plain Wrap doesn't scroll — with 14+
      // tiles in this list, everything past that cap (down to "Close
      // Classroom") was getting clipped and was completely unreachable.
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) => DraggableScrollableSheet(
        initialChildSize: 0.75,
        minChildSize: 0.4,
        maxChildSize: 0.92,
        expand: false,
        builder: (_, scrollCtrl) => SafeArea(
          top: false,
          child: ListView(
            controller: scrollCtrl, // scrollable list instead of Wrap
            children: [
              const Padding(
                padding: EdgeInsets.fromLTRB(20, 16, 20, 4),
                child: Text('Manage Classroom', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
              ),
              _manageTile(ctx, Icons.edit_outlined, 'Edit Classroom', _openEditClassroom),
              _manageTile(ctx, Icons.calendar_month_outlined, 'Schedule Manager', _openScheduleManager),
              _manageTile(ctx, Icons.event_note_outlined, 'Sessions', _openSessionsList),
              _manageTile(ctx, Icons.event_busy_outlined, 'Holidays', _openHolidays),
              _manageTile(ctx, Icons.confirmation_number_outlined, 'Passes', _openPassManagement),
              _manageTile(ctx, Icons.receipt_long_outlined, 'Purchases', _openPurchases),
              _manageTile(ctx, Icons.local_offer_outlined, 'Coupons', _openCoupons),
              _manageTile(ctx, Icons.savings_outlined, 'My Earnings', _openEarnings),
              _manageTile(ctx, Icons.groups_outlined, 'Staff', _openStaffManagement),
              _manageTile(ctx, Icons.mail_outline_rounded, 'Join Requests', _openJoinRequestsInbox, badgeCount: _pendingRequestCount),
              _manageTile(ctx, Icons.workspace_premium_outlined, 'Certificates', _openCertificates),
              _manageTile(ctx, Icons.quiz_outlined, 'Poll Templates', _openPollTemplates),
              _manageTile(ctx, Icons.video_library_outlined, 'Recordings', _openRecordings),
              _manageTile(ctx, Icons.person_off_outlined, 'Banned Students', _openBannedStudents),
              // FEATURE (Phase 2, item 7 — chat report review): backend +
              // Dart caller (ChatMessageReportApi.review) existed since
              // Pass 14 with no tile anywhere to reach the moderation queue.
              _manageTile(ctx, Icons.report_gmailerrorred_outlined, 'Reported Messages', _openChatMessageReports),
              // FEATURE (Phase 2, item 6 — share insights): teacher's view
              // of ClassroomViewSet.share_stats (who shared, which channel).
              _manageTile(ctx, Icons.insights_outlined, 'Share Insights', _openShareStats),
              const Divider(height: 8),
              _manageTile(ctx, Icons.folder_outlined, 'Materials (full screen)', _openMaterialsFullScreen),
              _manageTile(ctx, Icons.campaign_outlined, 'Notice Board (full screen)', _openNoticeBoardFullScreen),
              _manageTile(ctx, Icons.help_outline_rounded, 'Doubts (full screen)', _openDoubtsFullScreen),
              _manageTile(ctx, Icons.assignment_outlined, 'Assignments (full screen)', _openAssignmentsFullScreen),
              const Divider(height: 8),
              _manageTile(
                ctx,
                Icons.block_flipped,
                'Close Classroom',
                () {
                  Navigator.pop(ctx);
                  _confirmCloseClassroom();
                },
                danger: true,
              ),
              const SizedBox(height: 8),
            ],
          ),
        ),
      ),
    );
  }

  Widget _manageTile(BuildContext ctx, IconData icon, String label, VoidCallback onTap, {bool danger = false, int? badgeCount}) {
    return ListTile(
      leading: Icon(icon, color: danger ? Colors.red : _kNavy),
      title: Text(label, style: TextStyle(color: danger ? Colors.red : Colors.black87, fontWeight: FontWeight.w600)),
      trailing: (badgeCount != null && badgeCount > 0)
          ? Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(color: const Color(0xFFEE0979), borderRadius: BorderRadius.circular(20)),
              child: Text(
                badgeCount > 99 ? '99+' : '$badgeCount new',
                style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            )
          : null,
      onTap: () {
        if (!danger) Navigator.pop(ctx);
        onTap();
      },
    );
  }

  Future<void> _openEditClassroom() async {
    final result = await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ClassroomFormScreen(existing: _classroom)),
    );
    if (!mounted || result == null) return;
    if (result == 'closed' || result == 'deleted') {
      // Classroom no longer manageable the same way — back out to whatever
      // list screen brought us here (Explore, My Classrooms, etc.).
      Navigator.pop(context);
      return;
    }
    _load();
  }

  void _openScheduleManager() {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ScheduleManagerScreen(classroomId: widget.classroomId, canManage: true)),
    ).then((_) => _load());
  }

  void _openSessionsList() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => SessionsListScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? '', canManage: true),
      ),
    ).then((_) => _load());
  }

  void _openHolidays() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => HolidaysScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? ''),
      ),
    ).then((_) => _load());
  }

  // NEW (frontend integration architecture v3, §1.11): quick-poll templates
  // manage entry point, alongside the other classroom-scoped manage tiles.
  void _openPollTemplates() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => PollTemplatesScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? ''),
      ),
    ).then((_) => _load());
  }

  void _openCoupons() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => CouponsScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? ''),
      ),
    ).then((_) => _load());
  }

  void _openPassManagement() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => PassManagementScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? ''),
      ),
    ).then((_) => _load());
  }

  // NOTE (fix): PassPurchaseApi.refund() already existed with no screen
  // ever calling it — there was also no way to even list a classroom's
  // purchases as its teacher until the backend fix in views.py
  // (PassPurchaseViewSet.get_queryset() now accepts ?classroom=<id> for a
  // manager). This is where a teacher goes to refund ONE student (e.g.
  // resolving a complaint) — refunding everyone is "Close Classroom".
  void _openPurchases() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ClassroomPurchasesScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? ''),
      ),
    ).then((_) => _load());
  }

  void _openStaffManagement() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => StaffManagementScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? ''),
      ),
    ).then((_) => _load());
  }

  // NOTE (fix — new frontend for existing backend feature): my-earnings/
  // was fully implemented on the backend (TeacherEarningsView) but had no
  // path() entry at all until the backend routing fix, and no screen ever
  // called it once it was reachable. Scoped to this classroom, matching
  // how Purchases/Coupons are scoped from this same sheet.
  void _openEarnings() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => TeacherEarningsScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? ''),
      ),
    ).then((_) => _load());
  }

  // NOTE (fix — new frontend for existing backend feature): the recordings
  // library (classrooms/{id}/recordings/) was fully implemented on the
  // backend with no screen anywhere in the module ever calling it.
  void _openRecordings() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ClassroomRecordingsScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? ''),
      ),
    ).then((_) => _load());
  }

  // NOTE (fix — new frontend for existing backend feature): classroom-wide
  // ban/unban (classrooms/{id}/ban/, bans/, unban/{student_id}/) was fully
  // implemented on the backend with no screen anywhere in the module ever
  // calling it — a teacher had no in-app way to permanently ban a student.
  void _openBannedStudents() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => BannedStudentsScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? ''),
      ),
    ).then((_) => _load());
  }

  void _openJoinRequestsInbox() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => JoinRequestsScreen.inbox(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? ''),
      ),
    ).then((_) => _load());
  }

  void _openCertificates() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => CertificatesScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? '', canIssue: true),
      ),
    ).then((_) => _load());
  }

  // Dedicated full-screen variants — same data as the in-tab views below,
  // but with the fuller create/manage UI (e.g. Assignments' due-date +
  // attachment picker, Notice Board's priority/pin/expiry). These screens
  // existed in the module but had no navigation entry point until now.
  void _openMaterialsFullScreen() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => MaterialsScreen(classroomId: widget.classroomId, classroomTitle: _classroom?.title ?? '', canManage: _canManage),
      ),
    ).then((_) => _load());
  }

  void _openNoticeBoardFullScreen() {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => NoticeBoardScreen(classroomId: widget.classroomId, canManage: _canManage)),
    ).then((_) => _load());
  }

  void _openDoubtsFullScreen() {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => DoubtsScreen(classroomId: widget.classroomId, canManage: _canManage)),
    ).then((_) => _load());
  }

  void _openAssignmentsFullScreen() {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => AssignmentsScreen(classroomId: widget.classroomId, canManage: _canManage)),
    ).then((_) => _load());
  }

  /// Used for both the fresh "Request to Join" flow and "Renew Pass" —
  /// renewing an expired pass is the same request-a-pass screen, since a
  /// join request is what creates the PassPurchase either way.
  void _openRequestJoin() {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => RequestJoinScreen(classroomId: widget.classroomId, classroom: _classroom)),
    ).then((_) => _load());
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  // =====================================================================
  // BUILD
  // =====================================================================
  @override
  Widget build(BuildContext context) {
    if (_loading && _classroom == null) {
      return Scaffold(backgroundColor: _kBg, appBar: liveClassAppBar(''), body: const LiveClassLoading());
    }
    if (_error != null && _classroom == null) {
      return Scaffold(
        backgroundColor: _kBg,
        appBar: liveClassAppBar(''),
        body: LiveClassErrorState(message: _error!, onRetry: _load),
      );
    }

    final classroom = _classroom!;
    final tabs = _hasFullAccess || _canManage
        ? const ['About', 'Schedule', 'Materials', 'Notices', 'Doubts', 'Reviews', 'Assignments']
        : const ['About', 'Reviews'];

    return Scaffold(
      backgroundColor: _kBg,
      // NOTE (fix — '_dependents.isEmpty' crash): tabs.length isn't fixed —
      // it flips between 2 ('About'/'Reviews') and 7 depending on
      // _hasFullAccess/_canManage, and _load() (which can change those) is
      // called again after several in-place actions (accepting a join
      // request, buying a pass, etc. — see the .then((_) => _load()) call
      // sites above), not just once in initState. Without a key, Flutter
      // tries to update the SAME DefaultTabController element in place when
      // `length` changes on a later build, while the old TabBar/TabBarView
      // underneath are still mid-detach from it — that race is exactly
      // what throws the framework's '_dependents.isEmpty' assertion. Keying
      // on the tab set forces Flutter to tear down and remount just this
      // subtree as a new one instead of updating the old controller in
      // place, so there's nothing left with stale dependents when the tab
      // count changes.
      body: DefaultTabController(
        key: ValueKey('tabs-${tabs.length}'),
        length: tabs.length,
        child: NestedScrollView(
          headerSliverBuilder: (context, innerBoxIsScrolled) => [
            _buildSliverAppBar(classroom),
            SliverToBoxAdapter(child: _buildInfoCard(classroom)),
            SliverPersistentHeader(
              pinned: true,
              delegate: _TabBarDelegate(TabBar(
                isScrollable: true,
                labelColor: _kNavy,
                unselectedLabelColor: Colors.grey,
                indicatorColor: _kNavy,
                labelStyle: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                tabs: tabs.map((t) => Tab(text: t)).toList(),
              )),
            ),
          ],
          body: TabBarView(
            children: tabs.map((t) => _buildTabBody(t, classroom)).toList(),
          ),
        ),
      ),
      // NOTE (fix): this used to hide the bottom bar entirely for
      // owner/admin, which silently threw away the 'owner'/'admin' ->
      // "Enter Class" case in _buildBottomBar() below — teacher had no
      // quick way to jump into their own live/joinable session from this
      // screen (only via Settings -> Sessions, or the Schedule tab list).
      // _buildBottomBar() already returns the right thing for every
      // access level now, so just always show it.
      bottomNavigationBar: _buildBottomBar(),
    );
  }

  // ---------------------------------------------------------------------
  Widget _buildSliverAppBar(Classroom classroom) {
    return SliverAppBar(
      backgroundColor: _kNavy,
      expandedHeight: 220,
      pinned: true,
      iconTheme: const IconThemeData(color: Colors.white),
      flexibleSpace: FlexibleSpaceBar(
        background: Stack(
          fit: StackFit.expand,
          children: [
            classroom.coverImage != null && classroom.coverImage!.isNotEmpty
                ? CachedNetworkImage(
                    imageUrl: classroom.coverImage!,
                    fit: BoxFit.cover,
                    errorWidget: (_, __, ___) => Container(decoration: const BoxDecoration(gradient: _kGradient)),
                  )
                : Container(decoration: const BoxDecoration(gradient: _kGradient)),
            DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Colors.black.withOpacity(0.05), Colors.black.withOpacity(0.65)],
                ),
              ),
            ),
            Positioned(
              left: 16,
              right: 16,
              bottom: 14,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    classroom.title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 20),
                  ),
                  if (classroom.subject.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 2),
                      child: Text(classroom.subject, style: const TextStyle(color: Colors.white70, fontSize: 13)),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
      actions: [
        // FEATURE (Phase 2, item 6 — share button): ClassroomViewSet.share/
        // .share_stats/.my_shares were fully ready server-side with zero
        // frontend caller anywhere in the module. Visible to everyone
        // (sharing doesn't require access to the classroom itself).
        IconButton(icon: const Icon(Icons.share_outlined), onPressed: _openShareSheet),
        if (!_canManage)
          IconButton(
            icon: Icon(_wishlistEntryId != null ? Icons.favorite_rounded : Icons.favorite_border_rounded,
                color: _wishlistEntryId != null ? Colors.redAccent : Colors.white),
            onPressed: _toggleWishlist,
          ),
        if (!_canManage && _everHadAccess)
          IconButton(icon: const Icon(Icons.flag_outlined), onPressed: _openReportDialog),
        if (_canManage)
          Stack(
            clipBehavior: Clip.none,
            children: [
              IconButton(icon: const Icon(Icons.settings_outlined), onPressed: _openManageSheet),
              if (_pendingRequestCount > 0)
                Positioned(
                  top: 6,
                  right: 4,
                  child: IgnorePointer(
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                      decoration: BoxDecoration(color: const Color(0xFFEE0979), borderRadius: BorderRadius.circular(10)),
                      constraints: const BoxConstraints(minWidth: 16),
                      child: Text(
                        _pendingRequestCount > 99 ? '99+' : '$_pendingRequestCount',
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: Colors.white, fontSize: 9, fontWeight: FontWeight.bold),
                      ),
                    ),
                  ),
                ),
            ],
          ),
      ],
    );
  }

  Widget _buildInfoCard(Classroom classroom) {
    final stats = _stats;
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              CircleAvatar(
                radius: 18,
                backgroundColor: _kBg,
                backgroundImage: (classroom.teacher?.profilePicture != null && classroom.teacher!.profilePicture!.isNotEmpty)
                    ? CachedNetworkImageProvider(classroom.teacher!.profilePicture!)
                    : null,
                child: (classroom.teacher?.profilePicture == null || classroom.teacher!.profilePicture!.isEmpty)
                    ? const Icon(Icons.person, color: Colors.grey)
                    : null,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(classroom.teacher?.fullName ?? 'Unknown Teacher',
                        style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
                    if (classroom.classroomType == ClassroomType.organisation && classroom.organisationName.isNotEmpty)
                      Text(classroom.organisationName, style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
                  ],
                ),
              ),
              Row(
                children: [
                  const Icon(Icons.star_rounded, size: 18, color: Color(0xFFFFAD33)),
                  const SizedBox(width: 2),
                  Text(
                    classroom.ratingCount > 0 ? classroom.ratingAvg.toStringAsFixed(1) : 'New',
                    style: const TextStyle(fontWeight: FontWeight.bold),
                  ),
                  if (classroom.ratingCount > 0)
                    Text(' (${classroom.ratingCount})', style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
                ],
              ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _infoChip(Icons.language, classroom.language),
              if (stats != null && stats.weeklyTiming.isNotEmpty) _infoChip(Icons.schedule, stats.weeklyTiming),
              if (stats != null) _infoChip(Icons.groups_2_outlined, '${stats.enrolledCount} enrolled'),
              if (classroom.classroomType == ClassroomType.organisation) _infoChip(Icons.apartment_rounded, 'Organisation'),
            ],
          ),
          if (_accessLevel == 'expired' && _myPass?.expiresAt != null) ...[
            const SizedBox(height: 10),
            Text('Your pass expired on ${_fmtDate(_myPass!.expiresAt!)}.',
                style: TextStyle(color: Colors.red.shade600, fontSize: 12.5, fontWeight: FontWeight.w600)),
          ] else if (_accessLevel == 'active' && _myPass?.expiresAt != null) ...[
            const SizedBox(height: 10),
            Text('Pass is valid until ${_fmtDate(_myPass!.expiresAt!)}.',
                style: TextStyle(color: Colors.green.shade700, fontSize: 12.5, fontWeight: FontWeight.w600)),
          ] else if (_isPending) ...[
            const SizedBox(height: 10),
            Text('Your request to join is awaiting the teacher\'s approval.',
                style: TextStyle(color: Colors.orange.shade800, fontSize: 12.5, fontWeight: FontWeight.w600)),
          ],
        ],
      ),
    );
  }

  Widget _infoChip(IconData icon, String label) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(color: _kBg, borderRadius: BorderRadius.circular(20)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: _kNavy),
          const SizedBox(width: 5),
          Text(label, style: const TextStyle(fontSize: 12, color: _kNavy, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }

  Widget _buildTabBody(String tab, Classroom classroom) {
    switch (tab) {
      case 'About':
        return _AboutTab(classroom: classroom, stats: _stats);
      case 'Schedule':
        return _ScheduleTab(classroomId: widget.classroomId, classroomTitle: classroom.title, canManage: _canManage);
      case 'Materials':
        return _MaterialsTab(classroomId: widget.classroomId, canManage: _canManage);
      case 'Notices':
        return _NoticesTab(classroomId: widget.classroomId, canManage: _canManage);
      case 'Doubts':
        return _DoubtsTab(classroomId: widget.classroomId, canManage: _canManage, canAsk: _hasFullAccess && !_canManage);
      case 'Reviews':
        return _ReviewsTab(
          classroomId: widget.classroomId,
          canReview: _accessLevel == 'active' || _accessLevel == 'expired',
        );
      case 'Assignments':
        return _AssignmentsTab(classroomId: widget.classroomId, canManage: _canManage, canSubmit: _hasFullAccess && !_canManage);
      default:
        return const SizedBox.shrink();
    }
  }

  Widget? _buildBottomBar() {
    switch (_accessLevel) {
      // NOTE (fix): owner/admin (co-teacher/moderator) fell through to the
      // `default` branch below and got shown "Request to Join" on their OWN
      // classroom — there was no way to enter/start the live session from
      // this screen's main CTA at all. Backend already allows this
      // (_has_room_access grants owner/admin unconditionally in views.py);
      // this was purely a missing UI case.
      case 'owner':
      case 'admin':
      case 'active':
        return _BottomActionBar(
          label: 'Enter Class',
          gradient: true,
          busy: _actionBusy,
          onTap: _enterClass,
        );
      case 'expired':
        return _BottomActionBar(
          label: 'Renew Pass',
          gradient: true,
          onTap: _openRequestJoin,
        );
      case 'pending':
        // Flutter Phase 1, item 2: styled as inactive (not the primary
        // gradient CTA) so it doesn't invite re-tapping to "submit again"
        // — but tapping still does something useful (offers to cancel the
        // request) rather than being a dead end.
        return _BottomActionBar(
          label: 'Request Sent — Waiting for Approval',
          gradient: false,
          disabled: true,
          busy: _actionBusy,
          onTap: _confirmCancelJoinRequest,
        );
      default:
        return _BottomActionBar(
          label: 'Request to Join',
          gradient: false,
          onTap: _openRequestJoin,
        );
    }
  }

  // Flutter Phase 1, item 2: let a student back out of a pending request
  // instead of just waiting silently — reuses
  // LiveClassApi.joinRequests.cancel, already built for
  // `request_join_screen.dart` / `join_requests_screen.dart`'s own "mine"
  // tab, just not previously reachable from this screen's main CTA.
  Future<void> _confirmCancelJoinRequest() async {
    final requestId = _myPass?.pendingRequestId;
    if (requestId == null) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Cancel Request?'),
        content: const Text('This withdraws your join request. You can request to join again later.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Keep Waiting')),
          TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Cancel Request')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _actionBusy = true);
    try {
      await LiveClassApi.joinRequests.cancel(requestId);
      if (!mounted) return;
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(e is LiveClassApiException ? e.message : 'Could not cancel the request.')),
      );
    } finally {
      if (mounted) setState(() => _actionBusy = false);
    }
  }
}

// ===========================================================================
// Bottom CTA bar
// ===========================================================================
class _BottomActionBar extends StatelessWidget {
  final String label;
  final bool gradient;
  final bool busy;
  // Flutter Phase 1, item 2: muted/outline styling for the "already
  // pending" state — visually distinct from the primary "Enter
  // Class"/"Request to Join" CTAs so it doesn't read as an active call to
  // action, even though [onTap] is still wired (to "Cancel Request", not
  // "submit again") so the bar isn't a dead end.
  final bool disabled;
  final VoidCallback onTap;
  const _BottomActionBar({
    required this.label,
    required this.gradient,
    required this.onTap,
    this.busy = false,
    this.disabled = false,
  });

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
        decoration: BoxDecoration(color: Colors.white, boxShadow: [
          BoxShadow(color: Colors.black.withOpacity(0.08), blurRadius: 10, offset: const Offset(0, -2)),
        ]),
        child: SizedBox(
          width: double.infinity,
          height: 48,
          child: DecoratedBox(
            decoration: BoxDecoration(
              gradient: disabled ? null : (gradient ? _kGradient : null),
              color: disabled ? Colors.grey.shade200 : (gradient ? null : _kNavy),
              borderRadius: BorderRadius.circular(12),
              border: disabled ? Border.all(color: Colors.grey.shade400) : null,
            ),
            child: Material(
              color: Colors.transparent,
              child: InkWell(
                borderRadius: BorderRadius.circular(12),
                onTap: busy ? null : onTap,
                child: Center(
                  child: busy
                      ? SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            color: disabled ? _kNavy : Colors.white,
                            strokeWidth: 2,
                          ),
                        )
                      : Text(
                          label,
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            color: disabled ? Colors.grey.shade700 : Colors.white,
                            fontWeight: FontWeight.bold,
                            fontSize: 15,
                          ),
                        ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// ===========================================================================
// Sticky tab bar delegate
// ===========================================================================
class _TabBarDelegate extends SliverPersistentHeaderDelegate {
  final TabBar tabBar;
  _TabBarDelegate(this.tabBar);

  @override
  double get minExtent => tabBar.preferredSize.height;
  @override
  double get maxExtent => tabBar.preferredSize.height;

  @override
  Widget build(BuildContext context, double shrinkOffset, bool overlapsContent) {
    return Container(color: Colors.white, child: tabBar);
  }

  @override
  bool shouldRebuild(covariant _TabBarDelegate oldDelegate) => oldDelegate.tabBar != tabBar;
}

// ===========================================================================
// TAB 1 — About
// ===========================================================================
class _AboutTab extends StatelessWidget {
  final Classroom classroom;
  final ClassroomStats? stats;
  const _AboutTab({required this.classroom, required this.stats});

  @override
  Widget build(BuildContext context) {
    final features = <String, bool>{
      'Whiteboard': classroom.whiteboardEnabled,
      'Screen Share': classroom.screenShareEnabled,
      'Chat': classroom.chatEnabled,
      'Recording': classroom.recordingEnabled,
    }..removeWhere((k, v) => !v);

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
      children: [
        if (classroom.description.isNotEmpty) ...[
          const Text('About this classroom', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
          const SizedBox(height: 6),
          Text(classroom.description, style: const TextStyle(fontSize: 13.5, height: 1.5, color: Colors.black87)),
          const SizedBox(height: 18),
        ],
        if (features.isNotEmpty) ...[
          const Text('Features', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: features.keys.map((f) => _featurePill(f)).toList(),
          ),
          const SizedBox(height: 18),
        ],
        if (stats != null) ...[
          const Text('Class stats', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(child: _statBox('Enrolled', '${stats!.enrolledCount}')),
              const SizedBox(width: 10),
              Expanded(child: _statBox('Rating', stats!.ratingCount > 0 ? stats!.ratingAvg.toStringAsFixed(1) : 'New')),
            ],
          ),
          if (stats!.weeklyTiming.isNotEmpty) ...[
            const SizedBox(height: 10),
            _statBox('Weekly timing', stats!.weeklyTiming, wide: true),
          ],
          if (stats!.upcomingHolidays.isNotEmpty) ...[
            const SizedBox(height: 18),
            const Text('Upcoming holidays', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: stats!.upcomingHolidays
                  .map((h) => _featurePill('${_fmtDate(h.date)}${h.reason.isNotEmpty ? ' · ${h.reason}' : ''}'))
                  .toList(),
            ),
          ],
        ],
      ],
    );
  }

  Widget _featurePill(String label) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      decoration: BoxDecoration(color: _kBg, borderRadius: BorderRadius.circular(20)),
      child: Text(label, style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: _kNavy)),
    );
  }

  Widget _statBox(String label, String value, {bool wide = false}) {
    return Container(
      width: wide ? double.infinity : null,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(border: Border.all(color: Colors.grey.shade200), borderRadius: BorderRadius.circular(12)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
          const SizedBox(height: 3),
          Text(value, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15, color: _kNavy)),
        ],
      ),
    );
  }
}

// ===========================================================================
// TAB 2 — Schedule (recurring pattern + upcoming sessions)
// ===========================================================================
class _ScheduleTab extends StatefulWidget {
  final int classroomId;
  final String classroomTitle;
  final bool canManage;
  const _ScheduleTab({required this.classroomId, required this.classroomTitle, required this.canManage});

  @override
  State<_ScheduleTab> createState() => _ScheduleTabState();
}

class _ScheduleTabState extends State<_ScheduleTab> with AutomaticKeepAliveClientMixin {
  List<ClassSchedule> _schedules = [];
  List<ClassSession> _sessions = [];
  bool _loading = true;
  String? _error;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        LiveClassApi.schedules.list(classroomId: widget.classroomId),
        LiveClassApi.sessions.list(classroomId: widget.classroomId),
      ]);
      if (!mounted) return;
      setState(() {
        _schedules = (results[0] as PaginatedList<ClassSchedule>).results;
        _sessions = (results[1] as PaginatedList<ClassSession>).results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load the schedule';
      });
    }
  }

  // Every user with classroom access can browse the full session
  // calendar/history + recordings; only a manager also gets create/edit/
  // delete/end there (SessionsListScreen already supports both modes).
  void _openFullSessionsList() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => SessionsListScreen(
          classroomId: widget.classroomId,
          classroomTitle: widget.classroomTitle,
          canManage: widget.canManage,
        ),
      ),
    ).then((_) => _load());
  }

  void _enterSession(ClassSession s) {
    Navigator.push(context, MaterialPageRoute(builder: (_) => LiveSessionScreen(sessionId: s.id, session: s)));
  }

  // NOTE (fix): WaitlistApi.forSession()/promote() already existed with no
  // screen anywhere calling them — a teacher had no way to see who's
  // waiting on a full session, let alone manually promote a specific one
  // (auto-promotion on seat-free covers the common case, but not "bump
  // this particular student now").
  void _openWaitlist(ClassSession s) {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => WaitlistScreen(sessionId: s.id, classroomTitle: widget.classroomTitle, canManage: true),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return RefreshIndicator(
      color: _kNavy,
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        children: [
          if (widget.canManage)
            Align(
              alignment: Alignment.centerRight,
              child: TextButton.icon(
                onPressed: () {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => ScheduleManagerScreen(classroomId: widget.classroomId, canManage: widget.canManage),
                    ),
                  ).then((_) => _load());
                },
                icon: const Icon(Icons.add, size: 18),
                label: const Text('Manage'),
              ),
            ),
          if (_loading)
            const Padding(padding: EdgeInsets.only(top: 60), child: LiveClassLoading())
          else if (_error != null)
            LiveClassErrorState(message: _error!, onRetry: _load)
          else ...[
            if (_schedules.isNotEmpty) ...[
              const Text('Recurring pattern', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
              const SizedBox(height: 8),
              ..._schedules.map(_scheduleCard),
              const SizedBox(height: 18),
            ],
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text('Upcoming sessions', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
                TextButton(
                  onPressed: _openFullSessionsList,
                  style: TextButton.styleFrom(padding: EdgeInsets.zero, minimumSize: const Size(0, 0)),
                  child: const Text('View All', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
                ),
              ],
            ),
            const SizedBox(height: 8),
            if (_sessions.isEmpty)
              _InlineMessage(icon: Icons.event_busy_rounded, title: 'No session scheduled', subtitle: '')
            else
              // Only the next few here — full history/calendar + recordings
              // + "who's waiting" all live in the dedicated Sessions List
              // screen, reachable above via "View All" for every user (this
              // used to be teacher-only, leaving students with no way to
              // browse sessions or pick a specific one to join).
              ..._sessions.take(3).map(_sessionCard),
          ],
        ],
      ),
    );
  }

  Widget _scheduleCard(ClassSchedule s) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12), border: Border.all(color: Colors.grey.shade200)),
      child: Row(
        children: [
          const Icon(Icons.repeat_rounded, color: _kNavy, size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(_recurrenceLabel(s), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                const SizedBox(height: 2),
                // FIX (timezone audit): used to print the raw stored pair
                // ('10:00:00 · Asia/Kolkata') as-is, leaving a student
                // outside that zone to convert it themselves. Now resolved
                // against the IANA zone the teacher set and shown in the
                // viewer's own device time, with the original still noted
                // alongside for transparency (mirrors how Calendly/Zoom
                // surface cross-timezone scheduling).
                Text('${LiveClassDateTime.of(context).scheduleTimeLabel(s)} · ${s.durationMinutes} min',
                    style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
              ],
            ),
          ),
          if (!s.isActive)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(color: Colors.grey.shade200, borderRadius: BorderRadius.circular(10)),
              child: const Text('Paused', style: TextStyle(fontSize: 10, fontWeight: FontWeight.w600)),
            ),
        ],
      ),
    );
  }

  String _recurrenceLabel(ClassSchedule s) {
    switch (s.recurrenceType) {
      case RecurrenceType.daily:
        return 'Daily';
      case RecurrenceType.weekday:
        return 'Weekdays';
      case RecurrenceType.weekend:
        return 'Weekends';
      case RecurrenceType.weekly:
        return s.daysOfWeek.isEmpty ? 'Weekly' : 'Weekly · ${s.daysOfWeek.join(', ')}';
      case RecurrenceType.monthly:
        return 'Monthly · day ${s.dayOfMonth ?? '-'}';
      case RecurrenceType.yearly:
        return 'Yearly';
      default:
        return 'On ${_fmtDate(s.startDate, context)}';
    }
  }

  Widget _sessionCard(ClassSession s) {
    final color = switch (s.status) {
      SessionStatus.live => Colors.red,
      SessionStatus.completed => Colors.grey,
      SessionStatus.cancelled => Colors.grey,
      _ => _kNavy,
    };
    // NOTE (fix): isJoinable is meant to gate STUDENTS until the teacher
    // has actually started the class — the host must always be able to
    // enter their own scheduled/ad-hoc session (see the matching fix in
    // sessions_list_screen.dart's _sessionCard for the full story).
    final canEnter = s.status == SessionStatus.live || (s.status == SessionStatus.scheduled && (s.isJoinable || widget.canManage));
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.grey.shade200),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: canEnter ? () => _enterSession(s) : null,
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              children: [
                Container(
                  width: 8,
                  height: 8,
                  margin: const EdgeInsets.only(right: 10),
                  decoration: BoxDecoration(color: color, shape: BoxShape.circle),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(_fmtDateTime(s.scheduledStart, context), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                      const SizedBox(height: 2),
                      Text(
                        canEnter || s.status != SessionStatus.scheduled
                            ? s.status.toUpperCase()
                            : 'NOT JOINABLE YET',
                        style: TextStyle(fontSize: 11, color: color, fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                ),
                if (widget.canManage && (s.status == SessionStatus.live || s.status == SessionStatus.scheduled))
                  IconButton(
                    icon: const Icon(Icons.hourglass_top_rounded, size: 19, color: Colors.grey),
                    tooltip: 'Waitlist',
                    onPressed: () => _openWaitlist(s),
                  ),
                if (canEnter) Icon(Icons.chevron_right_rounded, color: Colors.grey.shade400),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ===========================================================================
// TAB 3 — Materials
// ===========================================================================
class _MaterialsTab extends StatefulWidget {
  final int classroomId;
  final bool canManage;
  const _MaterialsTab({required this.classroomId, required this.canManage});

  @override
  State<_MaterialsTab> createState() => _MaterialsTabState();
}

class _MaterialsTabState extends State<_MaterialsTab> with AutomaticKeepAliveClientMixin {
  List<ClassMaterial> _items = [];
  bool _loading = true;
  String? _error;
  bool _uploading = false;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await LiveClassApi.materials.list(classroomId: widget.classroomId);
      if (!mounted) return;
      setState(() {
        _items = res.results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load materials';
      });
    }
  }

  Future<void> _openMaterial(ClassMaterial m) async {
    if (m.file != null && m.file!.isNotEmpty) {
      await _downloadAndOpen(m.file!, m.title);
    } else if (m.externalLink.isNotEmpty) {
      await Clipboard.setData(ClipboardData(text: m.externalLink));
      _snack('Link copied — paste it in your browser.');
    }
  }

  Future<void> _downloadAndOpen(String url, String name) async {
    _snack('Downloading…');
    try {
      final token = await AuthService.getToken();
      final dir = await getTemporaryDirectory();
      final savePath = '${dir.path}/${name.replaceAll(' ', '_')}';
      final file = File(savePath);
      if (!await file.exists()) {
        // FIX (production readiness audit — corrupt-cache bug, same as
        // certificates_screen.dart/materials_screen.dart): a download that
        // fails partway used to leave a partial/corrupt file at
        // `savePath`, which the `exists()` check above then treated as
        // already-cached forever — the material became permanently
        // unopenable in-app. A failed download now deletes its own partial
        // file so the next tap retries instead of replaying the corrupt one.
        try {
          await Dio().download(url, savePath,
              options: Options(headers: token != null && token.isNotEmpty ? {'Authorization': 'Bearer $token'} : {}));
        } catch (_) {
          if (await file.exists()) await file.delete();
          rethrow;
        }
      }
      await OpenFilex.open(savePath);
    } catch (_) {
      _snack('Could not open.');
    }
  }

  Future<void> _openUploadDialog() async {
    // FIX (memory leak): both controllers used to be created here and never
    // disposed — every open+close of this sheet leaked two
    // TextEditingControllers for the lifetime of the app. try/finally
    // guarantees disposal on every exit path.
    final titleCtrl = TextEditingController();
    final linkCtrl = TextEditingController();
    try {
      await _showUploadDialog(titleCtrl, linkCtrl);
    } finally {
      titleCtrl.dispose();
      linkCtrl.dispose();
    }
  }

  Future<void> _showUploadDialog(TextEditingController titleCtrl, TextEditingController linkCtrl) async {
    String type = MaterialType.pdf;
    XFile? picked;

    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          return Padding(
            padding: EdgeInsets.only(left: 20, right: 20, top: 20, bottom: MediaQuery.of(ctx).viewInsets.bottom + 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Upload Material', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const SizedBox(height: 14),
                TextField(controller: titleCtrl, decoration: _inputDecoration('Title')),
                const SizedBox(height: 12),
                _dropdown(
                  value: type,
                  items: const {
                    MaterialType.pdf: 'PDF',
                    MaterialType.ppt: 'Presentation',
                    MaterialType.doc: 'Document',
                    MaterialType.image: 'Image',
                    MaterialType.video: 'Video',
                    MaterialType.link: 'External Link',
                  },
                  onChanged: (v) => setSheetState(() => type = v!),
                ),
                const SizedBox(height: 12),
                if (type == MaterialType.link)
                  TextField(controller: linkCtrl, decoration: _inputDecoration('https://…'))
                else
                  OutlinedButton.icon(
                    onPressed: () async {
                      final f = await openFile();
                      if (f != null) setSheetState(() => picked = f);
                    },
                    icon: const Icon(Icons.attach_file),
                    label: Text(picked == null ? 'Choose file' : picked!.name, overflow: TextOverflow.ellipsis),
                  ),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                    onPressed: () {
                      if (titleCtrl.text.trim().isEmpty) return;
                      Navigator.pop(ctx, true);
                    },
                    child: const Padding(padding: EdgeInsets.symmetric(vertical: 12), child: Text('Upload')),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );

    if (ok != true) return;
    setState(() => _uploading = true);
    try {
      await LiveClassApi.materials.upload(
        classroomId: widget.classroomId,
        title: titleCtrl.text.trim(),
        materialType: type,
        filePath: picked?.path,
        externalLink: linkCtrl.text.trim(),
      );
      _snack('Material uploaded.');
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not upload.');
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  IconData _iconFor(String type) {
    switch (type) {
      case MaterialType.pdf:
        return Icons.picture_as_pdf_rounded;
      case MaterialType.ppt:
        return Icons.slideshow_rounded;
      case MaterialType.doc:
        return Icons.description_rounded;
      case MaterialType.image:
        return Icons.image_rounded;
      case MaterialType.video:
        return Icons.videocam_rounded;
      default:
        return Icons.link_rounded;
    }
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return RefreshIndicator(
      color: _kNavy,
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        children: [
          if (widget.canManage)
            Align(
              alignment: Alignment.centerRight,
              child: ElevatedButton.icon(
                style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                onPressed: _uploading ? null : _openUploadDialog,
                icon: _uploading
                    ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : const Icon(Icons.upload_rounded, size: 18),
                label: const Text('Upload'),
              ),
            ),
          const SizedBox(height: 10),
          if (_loading)
            const Padding(padding: EdgeInsets.only(top: 60), child: LiveClassLoading())
          else if (_error != null)
            LiveClassErrorState(message: _error!, onRetry: _load)
          else if (_items.isEmpty)
            const _InlineMessage(icon: Icons.folder_off_outlined, title: 'No materials yet', subtitle: 'Notes/PDFs added by the teacher will show up here.')
          else
            ..._items.map((m) => Container(
                  margin: const EdgeInsets.only(bottom: 8),
                  decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12), border: Border.all(color: Colors.grey.shade200)),
                  child: ListTile(
                    leading: Icon(_iconFor(m.materialType), color: _kNavy),
                    title: Text(m.title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
                    subtitle: Text('${m.uploadedBy.fullName} · ${_fmtDate(m.uploadedAt)}', style: const TextStyle(fontSize: 11.5)),
                    trailing: const Icon(Icons.chevron_right_rounded, color: Colors.grey),
                    onTap: () => _openMaterial(m),
                  ),
                )),
        ],
      ),
    );
  }
}

// ===========================================================================
// TAB 4 — Notices
// ===========================================================================
class _NoticesTab extends StatefulWidget {
  final int classroomId;
  final bool canManage;
  const _NoticesTab({required this.classroomId, required this.canManage});

  @override
  State<_NoticesTab> createState() => _NoticesTabState();
}

class _NoticesTabState extends State<_NoticesTab> with AutomaticKeepAliveClientMixin {
  List<Notice> _items = [];
  bool _loading = true;
  String? _error;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await LiveClassApi.notices.list(widget.classroomId);
      if (!mounted) return;
      final sorted = [...res.results]..sort((a, b) => b.isPinned == a.isPinned ? b.createdAt.compareTo(a.createdAt) : (b.isPinned ? 1 : -1));
      setState(() {
        _items = sorted;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load notices';
      });
    }
  }

  Color _priorityColor(String p) {
    switch (p) {
      case NoticePriority.urgent:
        return Colors.red;
      case NoticePriority.low:
        return Colors.grey;
      default:
        return _kNavy;
    }
  }

  Future<void> _openPostDialog() async {
    // FIX (memory leak): both controllers used to be created here and never
    // disposed — every open+close of this sheet leaked two
    // TextEditingControllers for the lifetime of the app. try/finally
    // guarantees disposal on every exit path.
    final titleCtrl = TextEditingController();
    final msgCtrl = TextEditingController();
    try {
      await _showPostDialog(titleCtrl, msgCtrl);
    } finally {
      titleCtrl.dispose();
      msgCtrl.dispose();
    }
  }

  Future<void> _showPostDialog(TextEditingController titleCtrl, TextEditingController msgCtrl) async {
    String priority = NoticePriority.normal;

    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          return Padding(
            padding: EdgeInsets.only(left: 20, right: 20, top: 20, bottom: MediaQuery.of(ctx).viewInsets.bottom + 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Post Notice', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const SizedBox(height: 14),
                TextField(controller: titleCtrl, decoration: _inputDecoration('Title')),
                const SizedBox(height: 12),
                TextField(controller: msgCtrl, maxLines: 3, decoration: _inputDecoration('Message')),
                const SizedBox(height: 12),
                _dropdown(
                  value: priority,
                  items: const {NoticePriority.low: 'Low', NoticePriority.normal: 'Normal', NoticePriority.urgent: 'Urgent'},
                  onChanged: (v) => setSheetState(() => priority = v!),
                ),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                    onPressed: () {
                      if (titleCtrl.text.trim().isEmpty || msgCtrl.text.trim().isEmpty) return;
                      Navigator.pop(ctx, true);
                    },
                    child: const Padding(padding: EdgeInsets.symmetric(vertical: 12), child: Text('Post')),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );

    if (ok != true) return;
    try {
      await LiveClassApi.notices.create(Notice(
        id: 0,
        classroomId: widget.classroomId,
        postedBy: UserMini(id: 0, username: '', fullName: ''),
        title: titleCtrl.text.trim(),
        message: msgCtrl.text.trim(),
        priority: priority,
        createdAt: DateTime.now(),
      ));
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not post.');
    }
  }

  Future<void> _pin(Notice n, VoidCallback reload) async {
    try {
      await LiveClassApi.notices.pin(n.id);
      reload();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not pin.');
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return RefreshIndicator(
      color: _kNavy,
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        children: [
          if (widget.canManage)
            Align(
              alignment: Alignment.centerRight,
              child: ElevatedButton.icon(
                style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                onPressed: _openPostDialog,
                icon: const Icon(Icons.campaign_outlined, size: 18),
                label: const Text('Post Notice'),
              ),
            ),
          const SizedBox(height: 10),
          if (_loading)
            const Padding(padding: EdgeInsets.only(top: 60), child: LiveClassLoading())
          else if (_error != null)
            LiveClassErrorState(message: _error!, onRetry: _load)
          else if (_items.isEmpty)
            const _InlineMessage(icon: Icons.campaign_outlined, title: 'No notices yet', subtitle: '')
          else
            ..._items.map((n) => Container(
                  margin: const EdgeInsets.only(bottom: 8),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(12),
                    border: Border(left: BorderSide(color: _priorityColor(n.priority), width: 4)),
                    boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.04), blurRadius: 6, offset: const Offset(0, 2))],
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          if (n.isPinned) const Padding(padding: EdgeInsets.only(right: 6), child: Icon(Icons.push_pin_rounded, size: 14, color: _kNavy)),
                          Expanded(child: Text(n.title, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14))),
                          if (widget.canManage && !n.isPinned)
                            IconButton(
                              icon: const Icon(Icons.push_pin_outlined, size: 18),
                              onPressed: () => _pin(n, _load),
                              tooltip: 'Pin',
                              padding: EdgeInsets.zero,
                              constraints: const BoxConstraints(),
                            ),
                        ],
                      ),
                      const SizedBox(height: 4),
                      Text(n.message, style: const TextStyle(fontSize: 13, height: 1.4)),
                      const SizedBox(height: 6),
                      Text('${n.postedBy.fullName} · ${_fmtDate(n.createdAt)}', style: TextStyle(fontSize: 11, color: Colors.grey.shade600)),
                    ],
                  ),
                )),
        ],
      ),
    );
  }
}

// ===========================================================================
// TAB 5 — Doubts
// ===========================================================================
class _DoubtsTab extends StatefulWidget {
  final int classroomId;
  final bool canManage;
  final bool canAsk;
  const _DoubtsTab({required this.classroomId, required this.canManage, required this.canAsk});

  @override
  State<_DoubtsTab> createState() => _DoubtsTabState();
}

class _DoubtsTabState extends State<_DoubtsTab> with AutomaticKeepAliveClientMixin {
  List<ClassQuery> _items = [];
  bool _loading = true;
  String? _error;
  final _askCtrl = TextEditingController();
  bool _asking = false;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _askCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await LiveClassApi.queries.list(widget.classroomId);
      if (!mounted) return;
      setState(() {
        _items = res.results.reversed.toList();
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load doubts';
      });
    }
  }

  Future<void> _ask() async {
    final q = _askCtrl.text.trim();
    if (q.isEmpty || _asking) return;
    setState(() => _asking = true);
    try {
      await LiveClassApi.queries.ask(ClassQuery(
        id: 0,
        classroomId: widget.classroomId,
        askedBy: UserMini(id: 0, username: '', fullName: ''),
        question: q,
        createdAt: DateTime.now(),
      ));
      _askCtrl.clear();
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not post the doubt.');
    } finally {
      if (mounted) setState(() => _asking = false);
    }
  }

  Future<void> _answer(ClassQuery q) async {
    // FIX (memory leak): this controller used to be created here and never
    // disposed — every open+close of this dialog leaked one
    // TextEditingController for the lifetime of the app. try/finally
    // guarantees disposal on every exit path.
    final ctrl = TextEditingController();
    try {
      await _showAnswerDialog(q, ctrl);
    } finally {
      ctrl.dispose();
    }
  }

  Future<void> _showAnswerDialog(ClassQuery q, TextEditingController ctrl) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Answer Doubt'),
        content: TextField(controller: ctrl, maxLines: 4, autofocus: true, decoration: _inputDecoration('Your answer')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Submit'),
          ),
        ],
      ),
    );
    if (ok != true || ctrl.text.trim().isEmpty) return;
    try {
      await LiveClassApi.queries.answer(q.id, ctrl.text.trim());
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not submit the answer.');
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return RefreshIndicator(
      color: _kNavy,
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        children: [
          if (widget.canAsk) ...[
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _askCtrl,
                    decoration: _inputDecoration('Write your doubt…'),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  style: IconButton.styleFrom(backgroundColor: _kNavy),
                  onPressed: _asking ? null : _ask,
                  icon: _asking
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Icon(Icons.send_rounded, color: Colors.white, size: 18),
                ),
              ],
            ),
            const SizedBox(height: 14),
          ],
          if (_loading)
            const Padding(padding: EdgeInsets.only(top: 60), child: LiveClassLoading())
          else if (_error != null)
            LiveClassErrorState(message: _error!, onRetry: _load)
          else if (_items.isEmpty)
            const _InlineMessage(icon: Icons.help_outline_rounded, title: 'No doubts yet', subtitle: '')
          else
            ..._items.map((q) => Container(
                  margin: const EdgeInsets.only(bottom: 8),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12), border: Border.all(color: Colors.grey.shade200)),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(child: Text(q.question, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5))),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                            decoration: BoxDecoration(
                              color: q.status == QueryStatus.answered ? Colors.green.shade50 : Colors.orange.shade50,
                              borderRadius: BorderRadius.circular(10),
                            ),
                            child: Text(
                              q.status == QueryStatus.answered ? 'Answered' : 'Open',
                              style: TextStyle(
                                fontSize: 10,
                                fontWeight: FontWeight.bold,
                                color: q.status == QueryStatus.answered ? Colors.green.shade700 : Colors.orange.shade800,
                              ),
                            ),
                          ),
                        ],
                      ),
                      Text('${q.askedBy.fullName} · ${_fmtDate(q.createdAt)}', style: TextStyle(fontSize: 11, color: Colors.grey.shade600)),
                      if (q.answer.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Container(
                          padding: const EdgeInsets.all(10),
                          decoration: BoxDecoration(color: _kBg, borderRadius: BorderRadius.circular(8)),
                          child: Text(q.answer, style: const TextStyle(fontSize: 13, height: 1.4)),
                        ),
                      ] else if (widget.canManage) ...[
                        const SizedBox(height: 6),
                        Align(
                          alignment: Alignment.centerRight,
                          child: TextButton(onPressed: () => _answer(q), child: const Text('Answer')),
                        ),
                      ],
                    ],
                  ),
                )),
        ],
      ),
    );
  }
}

// ===========================================================================
// TAB 6 — Reviews
// ===========================================================================
class _ReviewsTab extends StatefulWidget {
  final int classroomId;
  final bool canReview;
  const _ReviewsTab({required this.classroomId, required this.canReview});

  @override
  State<_ReviewsTab> createState() => _ReviewsTabState();
}

class _ReviewsTabState extends State<_ReviewsTab> with AutomaticKeepAliveClientMixin {
  List<ClassroomReview> _items = [];
  bool _loading = true;
  String? _error;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await LiveClassApi.reviews.list(widget.classroomId);
      if (!mounted) return;
      setState(() {
        _items = res.results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load reviews';
      });
    }
  }

  Future<void> _openReviewDialog() async {
    // FIX (memory leak): this controller used to be created here and never
    // disposed — every open+close of this sheet leaked one
    // TextEditingController for the lifetime of the app. try/finally
    // guarantees disposal on every exit path.
    final ctrl = TextEditingController();
    try {
      await _showReviewDialog(ctrl);
    } finally {
      ctrl.dispose();
    }
  }

  Future<void> _showReviewDialog(TextEditingController ctrl) async {
    int rating = 5;
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          return Padding(
            padding: EdgeInsets.only(left: 20, right: 20, top: 20, bottom: MediaQuery.of(ctx).viewInsets.bottom + 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Write a Review', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const SizedBox(height: 12),
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: List.generate(5, (i) {
                    final filled = i < rating;
                    return IconButton(
                      onPressed: () => setSheetState(() => rating = i + 1),
                      icon: Icon(filled ? Icons.star_rounded : Icons.star_border_rounded, color: const Color(0xFFFFAD33), size: 30),
                    );
                  }),
                ),
                TextField(controller: ctrl, maxLines: 3, decoration: _inputDecoration('Your experience (optional)')),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                    onPressed: () => Navigator.pop(ctx, true),
                    child: const Padding(padding: EdgeInsets.symmetric(vertical: 12), child: Text('Submit Review')),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );
    if (ok != true) return;
    try {
      await LiveClassApi.reviews.create(classroomId: widget.classroomId, rating: rating, comment: ctrl.text.trim());
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not submit the review.');
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return RefreshIndicator(
      color: _kNavy,
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        children: [
          if (widget.canReview)
            Align(
              alignment: Alignment.centerRight,
              child: ElevatedButton.icon(
                style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                onPressed: _openReviewDialog,
                icon: const Icon(Icons.rate_review_outlined, size: 18),
                label: const Text('Write a Review'),
              ),
            ),
          const SizedBox(height: 10),
          if (_loading)
            const Padding(padding: EdgeInsets.only(top: 60), child: LiveClassLoading())
          else if (_error != null)
            LiveClassErrorState(message: _error!, onRetry: _load)
          else if (_items.isEmpty)
            const _InlineMessage(icon: Icons.reviews_outlined, title: 'No reviews yet', subtitle: '')
          else
            ..._items.map((r) => Container(
                  margin: const EdgeInsets.only(bottom: 8),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12), border: Border.all(color: Colors.grey.shade200)),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          CircleAvatar(
                            radius: 14,
                            backgroundColor: _kBg,
                            backgroundImage: (r.student.profilePicture != null && r.student.profilePicture!.isNotEmpty)
                                ? CachedNetworkImageProvider(r.student.profilePicture!)
                                : null,
                            child: (r.student.profilePicture == null || r.student.profilePicture!.isEmpty)
                                ? const Icon(Icons.person, size: 14, color: Colors.grey)
                                : null,
                          ),
                          const SizedBox(width: 8),
                          Expanded(child: Text(r.student.fullName, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13))),
                          Row(
                            children: List.generate(
                                5, (i) => Icon(i < r.rating ? Icons.star_rounded : Icons.star_border_rounded, size: 15, color: const Color(0xFFFFAD33))),
                          ),
                        ],
                      ),
                      if (r.comment.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Text(r.comment, style: const TextStyle(fontSize: 13, height: 1.4)),
                      ],
                      const SizedBox(height: 6),
                      Text(_fmtDate(r.createdAt), style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
                    ],
                  ),
                )),
        ],
      ),
    );
  }
}

// ===========================================================================
// TAB 7 — Assignments
// ===========================================================================
class _AssignmentsTab extends StatefulWidget {
  final int classroomId;
  final bool canManage;
  final bool canSubmit;
  const _AssignmentsTab({required this.classroomId, required this.canManage, required this.canSubmit});

  @override
  State<_AssignmentsTab> createState() => _AssignmentsTabState();
}

class _AssignmentsTabState extends State<_AssignmentsTab> with AutomaticKeepAliveClientMixin {
  List<Assignment> _items = [];
  bool _loading = true;
  String? _error;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await LiveClassApi.assignments.list(widget.classroomId);
      if (!mounted) return;
      setState(() {
        _items = res.results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load assignments';
      });
    }
  }

  Future<void> _openCreateDialog() async {
    // FIX (memory leak): these three controllers used to be created here
    // and never disposed — every open+close of this sheet leaked three
    // TextEditingControllers for the lifetime of the app. try/finally
    // guarantees disposal on every exit path.
    final titleCtrl = TextEditingController();
    final descCtrl = TextEditingController();
    final scoreCtrl = TextEditingController(text: '100');
    try {
      await _showCreateDialog(titleCtrl, descCtrl, scoreCtrl);
    } finally {
      titleCtrl.dispose();
      descCtrl.dispose();
      scoreCtrl.dispose();
    }
  }

  Future<void> _showCreateDialog(
    TextEditingController titleCtrl,
    TextEditingController descCtrl,
    TextEditingController scoreCtrl,
  ) async {
    DateTime dueDate = DateTime.now().add(const Duration(days: 7));

    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          return Padding(
            padding: EdgeInsets.only(left: 20, right: 20, top: 20, bottom: MediaQuery.of(ctx).viewInsets.bottom + 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('New Assignment', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const SizedBox(height: 14),
                TextField(controller: titleCtrl, decoration: _inputDecoration('Title')),
                const SizedBox(height: 12),
                TextField(controller: descCtrl, maxLines: 3, decoration: _inputDecoration('Description')),
                const SizedBox(height: 12),
                TextField(controller: scoreCtrl, keyboardType: TextInputType.number, decoration: _inputDecoration('Max score')),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: () async {
                    final picked = await showDatePicker(
                      context: ctx,
                      initialDate: dueDate,
                      firstDate: DateTime.now(),
                      lastDate: DateTime.now().add(const Duration(days: 365)),
                    );
                    if (picked != null) setSheetState(() => dueDate = picked);
                  },
                  icon: const Icon(Icons.event_outlined),
                  label: Text('Due: ${_fmtDate(dueDate)}'),
                ),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                    onPressed: () {
                      if (titleCtrl.text.trim().isEmpty) return;
                      Navigator.pop(ctx, true);
                    },
                    child: const Padding(padding: EdgeInsets.symmetric(vertical: 12), child: Text('Create')),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );

    if (ok != true) return;
    try {
      await LiveClassApi.assignments.create(Assignment(
        id: 0,
        classroomId: widget.classroomId,
        title: titleCtrl.text.trim(),
        description: descCtrl.text.trim(),
        dueDate: dueDate,
        maxScore: int.tryParse(scoreCtrl.text.trim()) ?? 100,
        createdAt: DateTime.now(),
      ));
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not create the assignment.');
    }
  }

  Future<void> _submit(Assignment a) async {
    final file = await openFile();
    if (file == null) return;
    try {
      await LiveClassApi.submissions.submit(assignmentId: a.id, filePath: file.path);
      _snack('Submission sent.');
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not submit.');
    }
  }

  Future<void> _viewSubmissions(Assignment a) async {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) => _SubmissionsSheet(assignment: a),
    );
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return RefreshIndicator(
      color: _kNavy,
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        children: [
          if (widget.canManage)
            Align(
              alignment: Alignment.centerRight,
              child: ElevatedButton.icon(
                style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                onPressed: _openCreateDialog,
                icon: const Icon(Icons.add_task_rounded, size: 18),
                label: const Text('New Assignment'),
              ),
            ),
          const SizedBox(height: 10),
          if (_loading)
            const Padding(padding: EdgeInsets.only(top: 60), child: LiveClassLoading())
          else if (_error != null)
            LiveClassErrorState(message: _error!, onRetry: _load)
          else if (_items.isEmpty)
            const _InlineMessage(icon: Icons.assignment_outlined, title: 'No assignments yet', subtitle: '')
          else
            ..._items.map((a) {
              final overdue = a.dueDate.isBefore(DateTime.now());
              return Container(
                margin: const EdgeInsets.only(bottom: 8),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(12), border: Border.all(color: Colors.grey.shade200)),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(a.title, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
                    if (a.description.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(a.description, style: const TextStyle(fontSize: 12.5, height: 1.4), maxLines: 3, overflow: TextOverflow.ellipsis),
                    ],
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Icon(Icons.event_outlined, size: 14, color: overdue ? Colors.red : Colors.grey.shade600),
                        const SizedBox(width: 4),
                        Text('Due ${_fmtDate(a.dueDate)}', style: TextStyle(fontSize: 11.5, color: overdue ? Colors.red : Colors.grey.shade600)),
                        const SizedBox(width: 14),
                        Icon(Icons.grade_outlined, size: 14, color: Colors.grey.shade600),
                        const SizedBox(width: 4),
                        Text('Max ${a.maxScore}', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Align(
                      alignment: Alignment.centerRight,
                      child: widget.canManage
                          ? TextButton(onPressed: () => _viewSubmissions(a), child: const Text('View Submissions'))
                          : widget.canSubmit
                              ? TextButton(onPressed: () => _submit(a), child: const Text('Submit'))
                              : const SizedBox.shrink(),
                    ),
                  ],
                ),
              );
            }),
        ],
      ),
    );
  }
}

// ===========================================================================
// Submissions bottom sheet (teacher grading)
// ===========================================================================
class _SubmissionsSheet extends StatefulWidget {
  final Assignment assignment;
  const _SubmissionsSheet({required this.assignment});

  @override
  State<_SubmissionsSheet> createState() => _SubmissionsSheetState();
}

class _SubmissionsSheetState extends State<_SubmissionsSheet> {
  List<AssignmentSubmission> _items = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final res = await LiveClassApi.submissions.list(widget.assignment.id);
      if (!mounted) return;
      setState(() {
        _items = res.results;
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _grade(AssignmentSubmission s) async {
    // FIX (memory leak): both controllers used to be created here and never
    // disposed — every open+close of this dialog (Grade/Regrade) leaked
    // two TextEditingControllers for the lifetime of the app. try/finally
    // guarantees disposal on every exit path.
    final scoreCtrl = TextEditingController(text: s.score?.toString() ?? '');
    final feedbackCtrl = TextEditingController(text: s.feedback);
    try {
      await _showGradeDialog(s, scoreCtrl, feedbackCtrl);
    } finally {
      scoreCtrl.dispose();
      feedbackCtrl.dispose();
    }
  }

  Future<void> _showGradeDialog(
    AssignmentSubmission s,
    TextEditingController scoreCtrl,
    TextEditingController feedbackCtrl,
  ) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Grade ${s.student.fullName}'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: scoreCtrl,
              keyboardType: TextInputType.number,
              decoration: _inputDecoration('Score (max ${widget.assignment.maxScore})'),
            ),
            const SizedBox(height: 10),
            TextField(controller: feedbackCtrl, maxLines: 3, decoration: _inputDecoration('Feedback (optional)')),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    final score = int.tryParse(scoreCtrl.text.trim());
    if (score == null) return;
    try {
      await LiveClassApi.submissions.grade(s.id, score: score, feedback: feedbackCtrl.text.trim());
      _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(e is LiveClassApiException ? e.message : 'Could not save the grade.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(left: 16, right: 16, top: 16, bottom: MediaQuery.of(context).viewInsets.bottom + 16),
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.7),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Submissions — ${widget.assignment.title}', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
            const SizedBox(height: 12),
            Flexible(
              child: _loading
                  ? const Center(child: Padding(padding: EdgeInsets.all(30), child: CircularProgressIndicator(color: _kNavy)))
                  : _items.isEmpty
                      ? const Padding(padding: EdgeInsets.all(30), child: Text('No submissions yet.'))
                      : ListView.builder(
                          shrinkWrap: true,
                          itemCount: _items.length,
                          itemBuilder: (ctx, i) {
                            final s = _items[i];
                            return ListTile(
                              contentPadding: EdgeInsets.zero,
                              title: Text(s.student.fullName, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
                              subtitle: Text(
                                '${_fmtDate(s.submittedAt)}${s.isLate ? ' · Late' : ''}${s.score != null ? ' · Score: ${s.score}/${widget.assignment.maxScore}' : ''}',
                                style: TextStyle(fontSize: 11.5, color: s.isLate ? Colors.red : Colors.grey.shade600),
                              ),
                              trailing: TextButton(onPressed: () => _grade(s), child: Text(s.score != null ? 'Edit' : 'Grade')),
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

// ===========================================================================
// Shared small widgets / helpers
// ===========================================================================
InputDecoration _inputDecoration(String hint) => InputDecoration(
      hintText: hint,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      isDense: true,
    );

Widget _dropdown({required String value, required Map<String, String> items, required ValueChanged<String?> onChanged}) {
  return DropdownButtonFormField<String>(
    value: value,
    decoration: _inputDecoration(''),
    items: items.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
    onChanged: onChanged,
  );
}

class _InlineMessage extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final String? actionLabel;
  final VoidCallback? onAction;
  const _InlineMessage({required this.icon, required this.title, required this.subtitle, this.actionLabel, this.onAction});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 40),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 44, color: Colors.grey.shade400),
            const SizedBox(height: 10),
            Text(title, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: _kNavy)),
            if (subtitle.isNotEmpty) ...[
              const SizedBox(height: 4),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Text(subtitle, textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
              ),
            ],
            if (actionLabel != null) ...[
              const SizedBox(height: 10),
              TextButton(onPressed: onAction, child: Text(actionLabel!)),
            ],
          ],
        ),
      ),
    );
  }
}