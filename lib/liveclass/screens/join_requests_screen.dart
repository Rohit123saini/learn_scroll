// lib/liveclass/screens/join_requests_screen.dart
//
// Screen 8 — Join Requests (see LIVECLASS_SCREEN_ARCHITECTURE.md §8).
// Same underlying resource (`join-requests/`) serves two very different
// views, so one file with two named constructors:
//
//   JoinRequestsScreen.inbox(classroomId: ..., classroomTitle: ...)
//     Teacher/co-teacher/moderator inbox for ONE classroom. Accept
//     (`POST .../accept/` — charges coins, creates PassPurchase) / Reject
//     (`POST .../reject/` — no charge), both with an optional note.
//
//   JoinRequestsScreen.mine()
//     Student's own requests across EVERY classroom (`GET join-requests/`
//     with no `classroom` param — backend scopes this to "own" for a
//     non-manager, same as request_join_screen.dart already relies on).
//     Pending ones can be cancelled (`POST .../cancel/`).
//
// Both share the status-tab list UI; only the card content + actions differ.

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

// FIX (design-system drift — production readiness audit): this file already
// imports theme/liveclass_theme.dart (for liveClassFmtDate below) but was
// still keeping its own hand-duplicated hex literals instead of aliasing
// the shared tokens — the exact drift risk classroom_purchases_screen.dart's
// and wishlist_screen.dart's matching fix already called out. Aliased
// instead — zero visual change, single source of truth going forward.
const _kNavy = LiveClassColors.navy;
const _kBg = LiveClassColors.bg;
const _kGradient = LiveClassColors.gradient;

// FIX (timezone bug): this used to be a hardcoded English month array with
// no `.toLocal()` call before reading `.day`/`.hour` off a UTC-parsed
// DateTime from the API — a user outside UTC would see the request time
// shifted from what they actually experienced. Delegates to the shared
// locale + `.toLocal()`-aware helper now (same fix already applied to
// doubts/holidays/submission-grading elsewhere in this module).
// FIX (locale consistency audit): this wrapper took no `context` param,
// so it could never thread the device/app locale through to
// liveClassFmtDateTime — every call site below was silently stuck on
// intl's default locale. Widened to the same optional
// `[BuildContext? context]` pattern used elsewhere in the module and
// threaded through at each call site (both are State instance methods).
String _fmtDateTime(DateTime d, [BuildContext? context]) => liveClassFmtDateTime(d, context);

String _coins(num n) => n == n.roundToDouble() ? n.toStringAsFixed(0) : n.toStringAsFixed(2);

String _statusLabel(String s) {
  switch (s) {
    case JoinRequestStatus.accepted:
      return 'Accepted';
    case JoinRequestStatus.rejected:
      return 'Rejected';
    case JoinRequestStatus.cancelled:
      return 'Cancelled';
    default:
      return 'Pending';
  }
}

Color _statusColor(String s) {
  switch (s) {
    case JoinRequestStatus.accepted:
      return const Color(0xFF2E7D32);
    case JoinRequestStatus.rejected:
      return const Color(0xFFC62828);
    case JoinRequestStatus.cancelled:
      return Colors.grey.shade600;
    default:
      return Colors.orange.shade800;
  }
}

// ===========================================================================
// SCREEN
// ===========================================================================
class JoinRequestsScreen extends StatefulWidget {
  final bool isInbox;
  final int? classroomId;
  final String classroomTitle;

  /// Teacher/co-teacher/moderator inbox for one classroom.
  const JoinRequestsScreen.inbox({super.key, required int classroomId, String classroomTitle = ''})
      : isInbox = true,
        classroomId = classroomId,
        classroomTitle = classroomTitle;

  /// Student's own requests across every classroom.
  const JoinRequestsScreen.mine({super.key})
      : isInbox = false,
        classroomId = null,
        classroomTitle = '';

  @override
  State<JoinRequestsScreen> createState() => _JoinRequestsScreenState();
}

class _JoinRequestsScreenState extends State<JoinRequestsScreen> with SingleTickerProviderStateMixin {
  static const _tabs = [
    (null, 'All'),
    (JoinRequestStatus.pending, 'Pending'),
    (JoinRequestStatus.accepted, 'Accepted'),
    (JoinRequestStatus.rejected, 'Rejected'),
    (JoinRequestStatus.cancelled, 'Cancelled'),
  ];

  late TabController _tabCtrl;
  List<ClassJoinRequest> _all = [];
  bool _loading = true;
  String? _error;
  final Set<int> _busyIds = {}; // accept/reject/cancel in flight per-request

  @override
  void initState() {
    super.initState();
    _tabCtrl = TabController(length: _tabs.length, vsync: this, initialIndex: widget.isInbox ? 1 : 0);
    _tabCtrl.addListener(() {
      if (!_tabCtrl.indexIsChanging) setState(() {});
    });
    _load();
  }

  @override
  void dispose() {
    _tabCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await LiveClassApi.joinRequests.list(classroomId: widget.classroomId);
      final items = res.results.toList()..sort((a, b) => b.requestedAt.compareTo(a.requestedAt));
      if (!mounted) return;
      setState(() {
        _all = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load requests.';
      });
    }
  }

  List<ClassJoinRequest> get _filtered {
    final status = _tabs[_tabCtrl.index].$1;
    if (status == null) return _all;
    return _all.where((r) => r.status == status).toList();
  }

  void _snack(String msg) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));

  void _replace(ClassJoinRequest updated) {
    setState(() {
      final i = _all.indexWhere((r) => r.id == updated.id);
      if (i != -1) _all[i] = updated;
    });
  }

  // -------------------------------------------------------------------
  // Teacher actions
  // -------------------------------------------------------------------
  Future<void> _decide(ClassJoinRequest req, {required bool accept}) async {
    final noteCtrl = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: Text(accept ? 'Accept Request?' : 'Reject Request?'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(accept
                ? '${req.student.fullName} will be charged ${_coins(req.classPassPrice)} coins and granted access.'
                : '${req.student.fullName}\'s request will be rejected — no charge will be made.'),
            const SizedBox(height: 14),
            TextField(
              controller: noteCtrl,
              maxLines: 2,
              decoration: InputDecoration(
                hintText: 'Note (optional)',
                filled: true,
                fillColor: _kBg,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide.none),
                contentPadding: const EdgeInsets.all(12),
              ),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(accept ? 'Accept' : 'Reject', style: TextStyle(color: accept ? Colors.green.shade700 : Colors.red)),
          ),
        ],
      ),
    );
    final note = noteCtrl.text.trim();
    noteCtrl.dispose();
    if (confirmed != true) return;

    setState(() => _busyIds.add(req.id));
    try {
      final updated = accept
          ? await LiveClassApi.joinRequests.accept(req.id, note: note)
          : await LiveClassApi.joinRequests.reject(req.id, note: note);
      if (!mounted) return;
      _replace(updated);
      _snack(accept ? 'Request accepted.' : 'Request rejected.');
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Something went wrong — please try again.');
    } finally {
      if (mounted) setState(() => _busyIds.remove(req.id));
    }
  }

  // -------------------------------------------------------------------
  // Student action
  // -------------------------------------------------------------------
  Future<void> _cancel(ClassJoinRequest req) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Cancel Request?'),
        content: Text('Your pending request for "${req.classroomTitle}" will be cancelled.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Cancel Request', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;

    setState(() => _busyIds.add(req.id));
    try {
      final updated = await LiveClassApi.joinRequests.cancel(req.id);
      if (!mounted) return;
      _replace(updated);
      _snack('Request cancelled.');
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not cancel.');
    } finally {
      if (mounted) setState(() => _busyIds.remove(req.id));
    }
  }

  // -------------------------------------------------------------------
  // Build
  // -------------------------------------------------------------------
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      appBar: AppBar(
        backgroundColor: Colors.white,
        foregroundColor: _kNavy,
        elevation: 0.5,
        title: Text(
          widget.isInbox
              ? (widget.classroomTitle.isNotEmpty ? 'Join Requests · ${widget.classroomTitle}' : 'Join Requests')
              : 'My Requests',
          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
        ),
        bottom: TabBar(
          controller: _tabCtrl,
          isScrollable: true,
          labelColor: _kNavy,
          unselectedLabelColor: Colors.black45,
          indicatorColor: _kNavy,
          labelStyle: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
          tabs: _tabs.map((t) => Tab(text: t.$2)).toList(),
        ),
      ),
      body: _loading
          // FIX (design-system consistency audit): exact match to
          // LiveClassLoading's own rendering (Center + navy
          // CircularProgressIndicator) — zero-visual-change swap.
          ? const LiveClassLoading()
          // FIX (design-system consistency audit): the loading state and
          // both card widgets in this file were already migrated onto the
          // shared theme widgets, but the error and empty states were
          // still on this file's own local `_ErrorState` / a hand-rolled
          // centered Text — left over from before liveclass_theme.dart
          // existed. Swapped to LiveClassErrorState/LiveClassEmptyState;
          // `_ErrorState` below is now unused and removed.
          : _error != null
              ? LiveClassErrorState(message: _error!, onRetry: _load)
              : RefreshIndicator(
                  color: _kNavy,
                  onRefresh: _load,
                  child: _filtered.isEmpty
                      ? LiveClassEmptyState(
                          icon: Icons.inbox_outlined,
                          title: widget.isInbox ? 'No requests in this category.' : 'You haven\'t sent any requests yet.',
                        )
                      : ListView.builder(
                          padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
                          itemCount: _filtered.length,
                          itemBuilder: (_, i) => widget.isInbox ? _inboxCard(_filtered[i]) : _mineCard(_filtered[i]),
                        ),
                ),
    );
  }

  Widget _avatar(UserMini u) {
    return CircleAvatar(
      radius: 18,
      backgroundColor: _kBg,
      backgroundImage: u.profilePicture != null && u.profilePicture!.isNotEmpty ? NetworkImage(u.profilePicture!) : null,
      child: (u.profilePicture == null || u.profilePicture!.isEmpty)
          ? Text(u.fullName.isNotEmpty ? u.fullName[0].toUpperCase() : '?',
              style: const TextStyle(color: _kNavy, fontWeight: FontWeight.bold))
          : null,
    );
  }

  Widget _statusPill(String status) {
    final c = _statusColor(status);
    // FIX (deprecated-API audit): `withOpacity` is deprecated in favour of
    // `withValues(alpha:)` (precision loss on wide-gamut color — see
    // Color.withOpacity's own deprecation note); coin_wallet_screen.dart
    // and coupons_screen.dart already made this swap, this call site
    // hadn't yet. Same alpha value, zero visual change.
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(color: c.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(20)),
      child: Text(_statusLabel(status).toUpperCase(), style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: c)),
    );
  }

  // -------------------------------------------------------------------
  // Teacher inbox card
  // -------------------------------------------------------------------
  // 🔴 FIX (design-system consistency audit): exact match to LiveClassCard's
  // defaults (white, radius 14, shadow black@0.04/blur 8/offset (0,2)) —
  // zero-visual-change swap, same as the rest of the module.
  Widget _inboxCard(ClassJoinRequest r) {
    final busy = _busyIds.contains(r.id);
    return LiveClassCard(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _avatar(r.student),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(r.student.fullName, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13.5)),
                    Text('@${r.student.username}', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
                  ],
                ),
              ),
              _statusPill(r.status),
            ],
          ),
          const Divider(height: 22),
          _kv('Pass', r.classPassTitle.isNotEmpty ? r.classPassTitle : '—'),
          _kv('Price', '${_coins(r.classPassPrice)} coins'),
          if (r.couponCode.isNotEmpty) _kv('Coupon', r.couponCode),
          if (r.message.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text('"${r.message}"', style: TextStyle(fontSize: 12.5, color: Colors.grey.shade700, fontStyle: FontStyle.italic)),
          ],
          const SizedBox(height: 6),
          Text('Requested: ${_fmtDateTime(r.requestedAt, context)}', style: TextStyle(fontSize: 11, color: Colors.grey.shade400)),
          if (r.decisionNote.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text('Note: ${r.decisionNote}', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
          ],
          if (r.status == JoinRequestStatus.pending) ...[
            const SizedBox(height: 14),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: busy ? null : () => _decide(r, accept: false),
                    style: OutlinedButton.styleFrom(foregroundColor: Colors.red, side: const BorderSide(color: Colors.red)),
                    child: const Text('Reject'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: DecoratedBox(
                    decoration: BoxDecoration(gradient: _kGradient, borderRadius: BorderRadius.circular(8)),
                    child: Material(
                      color: Colors.transparent,
                      child: InkWell(
                        borderRadius: BorderRadius.circular(8),
                        onTap: busy ? null : () => _decide(r, accept: true),
                        child: Padding(
                          padding: const EdgeInsets.symmetric(vertical: 11),
                          child: Center(
                            child: busy
                                ? const SizedBox(
                                    width: 16, height: 16, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))
                                : const Text('Accept', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  // -------------------------------------------------------------------
  // Student "My Requests" card
  // -------------------------------------------------------------------
  // 🔴 FIX (design-system consistency audit — the inbox card above and the
  // loading/empty states in build() were already migrated, this "My
  // Requests" card was the one call site left still hand-rolling the
  // Container): exact match to LiveClassCard's defaults (white, radius 14,
  // shadow black@0.04/blur 8/offset (0,2)) — zero-visual-change swap.
  Widget _mineCard(ClassJoinRequest r) {
    final busy = _busyIds.contains(r.id);
    return LiveClassCard(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(r.classroomTitle.isNotEmpty ? r.classroomTitle : 'Classroom #${r.classroomId}',
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
              ),
              _statusPill(r.status),
            ],
          ),
          const SizedBox(height: 10),
          _kv('Pass', r.classPassTitle.isNotEmpty ? r.classPassTitle : '—'),
          _kv('Price', '${_coins(r.classPassPrice)} coins'),
          const SizedBox(height: 6),
          Text('Requested: ${_fmtDateTime(r.requestedAt, context)}', style: TextStyle(fontSize: 11, color: Colors.grey.shade400)),
          if (r.decidedAt != null) ...[
            const SizedBox(height: 3),
            Text('Decided: ${_fmtDateTime(r.decidedAt!, context)}', style: TextStyle(fontSize: 11, color: Colors.grey.shade400)),
          ],
          if (r.decisionNote.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text('Note from teacher: "${r.decisionNote}"',
                style: TextStyle(fontSize: 12, color: Colors.grey.shade700, fontStyle: FontStyle.italic)),
          ],
          if (r.status == JoinRequestStatus.pending) ...[
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                onPressed: busy ? null : () => _cancel(r),
                style: OutlinedButton.styleFrom(foregroundColor: Colors.red, side: const BorderSide(color: Colors.red)),
                child: busy
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Text('Cancel Request'),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _kv(String k, String v) => Padding(
        padding: const EdgeInsets.only(bottom: 3),
        child: Row(
          children: [
            SizedBox(width: 56, child: Text(k, style: TextStyle(fontSize: 12, color: Colors.grey.shade500))),
            Expanded(child: Text(v, style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600))),
          ],
        ),
      );
}