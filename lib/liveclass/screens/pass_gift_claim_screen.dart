// lib/liveclass/screens/pass_gift_claim_screen.dart
//
// NEW screen (frontend integration architecture v3, §1.3, Pass 14).
// ⚠️ ARCHITECTURE SKELETON — `PassGift` / `PassGiftApi` (liveclass_models.dart
// / liveclass_api_service.dart) are a best guess from the change-log
// description only (Pass 14 was never written up in the backend doc's own
// §2–§6). Confirm exact field names + endpoint paths against real backend
// source before this ships.
//
// Two entry points per the doc:
//  1. A notification tap (liveclass_notification_handler.dart — not in this
//     batch) should open this screen directly on a specific gift, passing
//     the gift id from the push payload → PassGiftClaimScreen(giftId: …).
//  2. General browsing — a "My gifts" entry from my_passes_screen.dart (not
//     in this batch) → PassGiftClaimScreen() with no id, opening on the
//     Received/Sent tabs instead.
//
// On successful claim, this calls [onClaimed] with the classroom id the
// gift unlocked (if the backend response exposes one) — wire that to push
// into ClassroomDetailScreen from wherever this screen is pushed from
// (ClassroomDetailScreen isn't imported directly here to avoid a cycle,
// since that screen would be the one pushing this one).

import 'dart:async';

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

class PassGiftClaimScreen extends StatefulWidget {
  /// If set, this screen opens directly on that single gift (e.g. from a
  /// notification tap) instead of the Received/Sent tab list.
  final int? giftId;

  /// Called after a successful claim — wire this to push into
  /// ClassroomDetailScreen from the caller.
  final void Function(PassGift claimed)? onClaimed;

  const PassGiftClaimScreen({super.key, this.giftId, this.onClaimed});

  @override
  State<PassGiftClaimScreen> createState() => _PassGiftClaimScreenState();
}

class _PassGiftClaimScreenState extends State<PassGiftClaimScreen> {
  @override
  Widget build(BuildContext context) {
    if (widget.giftId != null) {
      return Scaffold(
        backgroundColor: LiveClassColors.bg,
        appBar: liveClassAppBar('Gift'),
        body: _SingleGiftView(giftId: widget.giftId!, onClaimed: widget.onClaimed),
      );
    }
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        backgroundColor: LiveClassColors.bg,
        appBar: AppBar(
          backgroundColor: Colors.white,
          foregroundColor: LiveClassColors.navy,
          elevation: 0.5,
          title: const Text('Pass Gifts', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
          bottom: const TabBar(
            labelColor: LiveClassColors.navy,
            unselectedLabelColor: Colors.grey,
            indicatorColor: LiveClassColors.navy,
            tabs: [Tab(text: 'Received'), Tab(text: 'Sent')],
          ),
        ),
        body: TabBarView(
          children: [
            _GiftListView(direction: _Direction.received, onClaimed: widget.onClaimed),
            _GiftListView(direction: _Direction.sent, onClaimed: null),
          ],
        ),
      ),
    );
  }
}

enum _Direction { received, sent }

class _SingleGiftView extends StatefulWidget {
  final int giftId;
  final void Function(PassGift claimed)? onClaimed;

  const _SingleGiftView({required this.giftId, this.onClaimed});

  @override
  State<_SingleGiftView> createState() => _SingleGiftViewState();
}

class _SingleGiftViewState extends State<_SingleGiftView> {
  bool _loading = true;
  String? _error;
  PassGift? _gift;
  bool _claiming = false;
  Timer? _ticker;

  @override
  void initState() {
    super.initState();
    _load();
    // Re-render every 30s so the countdown stays roughly live.
    _ticker = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      // No single-gift GET is listed in the doc's PassGiftApi group — fall
      // back to fetching the received list and picking the id out. Add a
      // dedicated `PassGiftApi.detail(id)` once the real endpoint is
      // confirmed, rather than paging through everything.
      final page = await LiveClassApi.passGifts.myGiftsReceived();
      final match = page.results.where((g) => g.id == widget.giftId).toList();
      if (!mounted) return;
      setState(() {
        _loading = false;
        if (match.isEmpty) {
          _error = 'This gift could not be found.';
        } else {
          _gift = match.first;
        }
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load this gift.';
      });
    }
  }

  Future<void> _claim() async {
    final gift = _gift;
    if (gift == null || _claiming) return;
    setState(() => _claiming = true);
    try {
      final claimed = await LiveClassApi.passGifts.claim(gift.id);
      if (!mounted) return;
      setState(() => _gift = claimed);
      widget.onClaimed?.call(claimed);
      _snack('Gift claimed! Enjoy the class.');
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not claim this gift. Please try again.');
    } finally {
      if (mounted) setState(() => _claiming = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const LiveClassLoading();
    if (_error != null || _gift == null) {
      return LiveClassErrorState(message: _error ?? 'Something went wrong.', onRetry: _load);
    }
    return SingleChildScrollView(
      padding: const EdgeInsets.all(LiveClassSpacing.xl),
      child: _GiftCard(gift: _gift!, claiming: _claiming, onClaim: _claim),
    );
  }
}

class _GiftListView extends StatefulWidget {
  final _Direction direction;
  final void Function(PassGift claimed)? onClaimed;

  const _GiftListView({required this.direction, this.onClaimed});

  @override
  State<_GiftListView> createState() => _GiftListViewState();
}

class _GiftListViewState extends State<_GiftListView> {
  bool _loading = true;
  String? _error;
  List<PassGift> _gifts = [];
  int? _claimingId;
  int? _cancellingId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final page = widget.direction == _Direction.received
          ? await LiveClassApi.passGifts.myGiftsReceived()
          : await LiveClassApi.passGifts.myGiftsSent();
      if (!mounted) return;
      setState(() {
        _gifts = page.results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load gifts.';
      });
    }
  }

  Future<void> _claim(PassGift gift) async {
    setState(() => _claimingId = gift.id);
    try {
      final claimed = await LiveClassApi.passGifts.claim(gift.id);
      if (!mounted) return;
      setState(() => _gifts = _gifts.map((g) => g.id == claimed.id ? claimed : g).toList());
      widget.onClaimed?.call(claimed);
      _snack('Gift claimed! Enjoy the class.');
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not claim this gift.');
    } finally {
      if (mounted) setState(() => _claimingId = null);
    }
  }

  /// item 10 — gifter can cancel their own gift while it's still PENDING.
  /// Refund happens server-side (PassGift.refund_to_gifter()); we just
  /// swap the updated (now-cancelled) gift into the list.
  Future<void> _cancel(PassGift gift) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Cancel this gift?'),
        content: Text('${gift.coinsSpent} coins will be refunded to you.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('No')),
          TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Yes, cancel')),
        ],
      ),
    );
    if (ok != true) return;

    setState(() => _cancellingId = gift.id);
    try {
      final cancelled = await LiveClassApi.passGifts.cancel(gift.id);
      if (!mounted) return;
      setState(() => _gifts = _gifts.map((g) => g.id == cancelled.id ? cancelled : g).toList());
      _snack('Gift cancelled — coins refunded.');
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not cancel this gift.');
    } finally {
      if (mounted) setState(() => _cancellingId = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const LiveClassLoading();
    if (_error != null) return LiveClassErrorState(message: _error!, onRetry: _load);
    if (_gifts.isEmpty) {
      return LiveClassEmptyState(
        icon: Icons.card_giftcard,
        title: widget.direction == _Direction.received ? 'No gifts received yet' : 'You haven\'t gifted a pass yet',
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.builder(
        padding: const EdgeInsets.all(LiveClassSpacing.lg),
        itemCount: _gifts.length,
        itemBuilder: (context, i) {
          final g = _gifts[i];
          if (widget.direction == _Direction.received && g.isPending) {
            return Padding(
              padding: const EdgeInsets.only(bottom: LiveClassSpacing.md),
              child: _GiftCard(gift: g, claiming: _claimingId == g.id, onClaim: () => _claim(g), compact: true),
            );
          }
          return LiveClassCard(
            child: Row(
              children: [
                const LiveClassIconBadge(icon: Icons.card_giftcard, size: 38),
                const SizedBox(width: LiveClassSpacing.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        g.classPassTitle.isEmpty ? 'Class pass' : g.classPassTitle,
                        style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        // FIX (stale field names): PassGift's real field is
                        // `recipient` (a non-nullable UserMini) — this
                        // screen was never updated after liveclass_models
                        // .dart's PassGift class got corrected against the
                        // real PassGiftSerializer (see that class's own
                        // doc comment); `giftedTo`/`giftedToRaw` never
                        // existed there, so this wouldn't even compile.
                        widget.direction == _Direction.received
                            ? 'From ${g.gifter.fullName}'
                            : 'To ${g.recipient.fullName}',
                        style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                      ),
                    ],
                  ),
                ),
                if (widget.direction == _Direction.sent && g.isPending)
                  TextButton(
                    onPressed: _cancellingId == g.id ? null : () => _cancel(g),
                    child: _cancellingId == g.id
                        ? const SizedBox(
                            height: 14, width: 14, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Text('Cancel', style: TextStyle(fontSize: 12.5)),
                  )
                else
                  _StatusChip(status: g.status),
              ],
            ),
          );
        },
      ),
    );
  }
}

String _statusLabel(String status) {
  switch (status) {
    case PassGiftStatus.pending:
      return 'Pending';
    case PassGiftStatus.claimed:
      return 'Claimed';
    case PassGiftStatus.expired:
      return 'Expired';
    // FIX (stale status constant): PassGiftStatus has no `refunded` value
    // on the real backend — cancelling refunds the gifter as a side
    // effect, but the gift row itself lands on CANCELLED, not a separate
    // REFUNDED status (see the PassGiftStatus doc comment in
    // liveclass_models.dart). This case referenced a constant that no
    // longer exists and would fail to compile.
    case PassGiftStatus.cancelled:
      return 'Cancelled';
    default:
      return status;
  }
}

class _StatusChip extends StatelessWidget {
  final String status;
  const _StatusChip({required this.status});

  @override
  Widget build(BuildContext context) {
    Color color;
    Color bg;
    switch (status) {
      case PassGiftStatus.claimed:
        color = LiveClassColors.success;
        bg = LiveClassColors.successBg;
        break;
      case PassGiftStatus.expired:
      case PassGiftStatus.cancelled:
        color = Colors.grey.shade700;
        bg = Colors.grey.shade200;
        break;
      default:
        color = LiveClassColors.warning;
        bg = LiveClassColors.warningBg;
    }
    return LiveClassStatusChip(label: _statusLabel(status).toUpperCase(), color: color, background: bg);
  }
}

class _GiftCard extends StatelessWidget {
  final PassGift gift;
  final bool claiming;
  final VoidCallback onClaim;
  final bool compact;

  const _GiftCard({
    required this.gift,
    required this.claiming,
    required this.onClaim,
    this.compact = false,
  });

  @override
  Widget build(BuildContext context) {
    final expired = gift.status == PassGiftStatus.expired || (gift.isPending && gift.timeLeft.isNegative);
    final daysLeft = gift.timeLeft.inHours / 24;

    return LiveClassCard(
      margin: EdgeInsets.zero,
      padding: EdgeInsets.all(compact ? 14 : 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const LiveClassIconBadge(icon: Icons.card_giftcard, size: 42),
              const SizedBox(width: LiveClassSpacing.md),
              Expanded(
                child: Text(
                  gift.classPassTitle.isEmpty ? 'A class pass' : gift.classPassTitle,
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15, color: LiveClassColors.navy),
                ),
              ),
            ],
          ),
          const SizedBox(height: LiveClassSpacing.sm),
          if (gift.classroomTitle.isNotEmpty)
            Text(gift.classroomTitle, style: TextStyle(color: Colors.grey.shade600, fontSize: 12.5)),
          const SizedBox(height: 3),
          Text('From ${gift.gifter.fullName}', style: TextStyle(color: Colors.grey.shade600, fontSize: 12.5)),
          const SizedBox(height: LiveClassSpacing.md),
          if (gift.isPending && !expired)
            Text(
              daysLeft >= 1
                  ? 'Claim within ${daysLeft.ceil()} day${daysLeft.ceil() == 1 ? "" : "s"}'
                  : 'Claim within ${gift.timeLeft.inHours}h ${gift.timeLeft.inMinutes % 60}m',
              style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 12.5, color: LiveClassColors.warning),
            )
          else
            Text(
              expired ? 'This gift has expired.' : _statusLabel(gift.status),
              style: TextStyle(color: Colors.grey.shade600, fontSize: 12.5),
            ),
          const SizedBox(height: LiveClassSpacing.md),
          if (gift.isPending && !expired)
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                style: ElevatedButton.styleFrom(backgroundColor: LiveClassColors.navy, foregroundColor: Colors.white),
                onPressed: claiming ? null : onClaim,
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: claiming
                      ? const SizedBox(
                          height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Text('Claim Gift'),
                ),
              ),
            ),
        ],
      ),
    );
  }
}