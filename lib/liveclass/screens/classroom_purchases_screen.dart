// lib/liveclass/screens/classroom_purchases_screen.dart
//
// Teacher-side pass-purchase roster for ONE classroom — the missing UI for
// PassPurchaseApi.refund()/forClassroom(). Both the backend `refund` action
// and the Dart client method already existed with no screen anywhere
// calling them; there was also no way to even list a classroom's purchases
// as its teacher (PassPurchaseViewSet.get_queryset() was hard-scoped to
// "own purchases only"). Fixed on the backend to accept ?classroom=<id> for
// that classroom's teacher/co-teacher/moderator — see views.py.
//
// Reached from the classroom detail screen's manage sheet, owner/admin
// only. Use this for a SINGLE student's refund (e.g. resolving a
// complaint) — refunding everyone at once is "Close Classroom" instead.
//
// API:
//   GET  pass-purchases/?classroom=<id>   (teacher-scoped list)
//   POST pass-purchases/{id}/refund/      (only remaining_balance — coins
//                                          still sitting in escrow for
//                                          un-taught days — comes back to
//                                          the student; no teacher clawback,
//                                          coins_released for classes
//                                          actually held stays with them)

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

// FIX (design-system drift — production readiness audit): this file already
// imports theme/liveclass_theme.dart (for liveClassFmtDate) but was still
// keeping its own hand-duplicated hex literals instead of aliasing the
// shared tokens — the exact drift risk wishlist_screen.dart's and
// waitlist_screen.dart's matching fix already called out: if
// LiveClassColors.navy/bg ever changes, this screen would silently stop
// matching the rest of the module with no compile-time signal. Aliased
// instead — zero visual change, single source of truth going forward.
const _kNavy = LiveClassColors.navy;
const _kBg = LiveClassColors.bg;

String _statusLabel(String s) {
  switch (s) {
    case 'success':
      return 'Active';
    case 'refunded':
      return 'Refunded';
    case 'failed':
      return 'Failed';
    default:
      return 'Pending';
  }
}

Color _statusColor(String s) {
  switch (s) {
    case 'success':
      return const Color(0xFF2E7D32);
    case 'refunded':
      return Colors.grey.shade600;
    case 'failed':
      return const Color(0xFFC62828);
    default:
      return Colors.orange.shade800;
  }
}

// FIX (i18n / timezone audit — see utils/liveclass_datetime.dart): was a
// hardcoded English month array with no `.toLocal()` call before reading
// `.day`/`.month` off a UTC-parsed `DateTime`. Delegates to the shared
// locale + `.toLocal()` aware helper now.
String _fmtDate(DateTime d, [BuildContext? context]) => liveClassFmtDate(d, context);

// ===========================================================================
// SCREEN
// ===========================================================================
class ClassroomPurchasesScreen extends StatefulWidget {
  final int classroomId;
  final String classroomTitle;
  const ClassroomPurchasesScreen({super.key, required this.classroomId, this.classroomTitle = ''});

  @override
  State<ClassroomPurchasesScreen> createState() => _ClassroomPurchasesScreenState();
}

class _ClassroomPurchasesScreenState extends State<ClassroomPurchasesScreen> {
  List<PassPurchase> _all = [];
  bool _loading = true;
  String? _error;
  bool _activeOnly = false;
  final Set<int> _busyIds = {}; // refund in flight per-purchase

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
      final res = await LiveClassApi.passPurchases.forClassroom(widget.classroomId);
      final items = res.results.toList()..sort((a, b) => b.purchasedAt.compareTo(a.purchasedAt));
      if (!mounted) return;
      setState(() {
        _all = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load purchases.';
      });
    }
  }

  List<PassPurchase> get _visible => _activeOnly ? _all.where((p) => p.isValid).toList() : _all;

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _confirmRefund(PassPurchase p) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Refund?'),
        content: Text(
          // NOTE (fix — dialog overstated the refund): used to quote
          // p.coinsSpent as what goes back, but reverse() only ever
          // refunds remainingBalance — days already taught have already
          // paid the teacher (coinsReleased) and stay with them.
          // Quoting the full amount here misled the teacher about how
          // much actually leaves escrow, on every purchase that had at
          // least one day charged against it.
          p.remainingBalance > 0
              ? '${p.student.fullName.isNotEmpty ? p.student.fullName : p.student.username} will get ${p.remainingBalance} coins back '
                  '(the balance still sitting in escrow — the ${p.coinsReleased} coins for classes already held have already gone to you '
                  'and will not be taken back), and their access to this classroom will end immediately. This cannot be undone.'
              : '${p.student.fullName.isNotEmpty ? p.student.fullName : p.student.username} has no remaining balance left to refund '
                  '(the full amount has already gone to you) — only their access will end immediately. This cannot be undone.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Refund', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;

    setState(() => _busyIds.add(p.id));
    try {
      await LiveClassApi.passPurchases.refund(p.id);
      _snack('Refund complete.');
      await _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Refund failed.');
    } finally {
      if (mounted) setState(() => _busyIds.remove(p.id));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      appBar: liveClassAppBar(
        widget.classroomTitle.isNotEmpty ? 'Purchases — ${widget.classroomTitle}' : 'Purchases',
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: Center(
              child: FilterChip(
                label: const Text('Active only', style: TextStyle(fontSize: 12)),
                selected: _activeOnly,
                onSelected: (v) => setState(() => _activeOnly = v),
                selectedColor: _kNavy.withOpacity(0.1),
                checkmarkColor: _kNavy,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
              ),
            ),
          ),
        ],
      ),
      body: RefreshIndicator(
        color: _kNavy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _visible.isEmpty
                    ? ListView(
                        children: const [
                          Padding(
                            padding: EdgeInsets.only(top: 100),
                            child: Center(child: Text('No purchases yet.', style: TextStyle(color: Colors.black45))),
                          ),
                        ],
                      )
                    : ListView.builder(
                        padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
                        itemCount: _visible.length,
                        itemBuilder: (_, i) => _purchaseCard(_visible[i]),
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
          ? Text(u.fullName.isNotEmpty ? u.fullName[0].toUpperCase() : '?', style: const TextStyle(color: _kNavy, fontWeight: FontWeight.bold))
          : null,
    );
  }

  Widget _purchaseCard(PassPurchase p) {
    final busy = _busyIds.contains(p.id);
    final color = _statusColor(p.status);
    // NOTE: only a currently SUCCESS purchase can be refunded — backend
    // itself also rejects anything else, this just avoids a pointless
    // round-trip (and a confusing enabled-but-fails button) for a purchase
    // that's already refunded/failed/pending.
    final canRefund = p.status == 'success';

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.04), blurRadius: 8, offset: const Offset(0, 2))],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _avatar(p.student),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(p.student.fullName.isNotEmpty ? p.student.fullName : p.student.username,
                        style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13.5)),
                    Text('@${p.student.username}', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                decoration: BoxDecoration(color: color.withOpacity(0.12), borderRadius: BorderRadius.circular(20)),
                child: Text(_statusLabel(p.status).toUpperCase(),
                    style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: color)),
              ),
            ],
          ),
          const Divider(height: 22),
          Row(
            children: [
              Expanded(child: _stat('Pass', p.classPassTitle.isNotEmpty ? p.classPassTitle : '—')),
              Expanded(child: _stat('Coins', '${p.coinsSpent}')),
              Expanded(child: _stat('Purchased', _fmtDate(p.purchasedAt, context))),
            ],
          ),
          // NOTE (fix — teacher had no visibility into escrow split): a
          // flat "Coins" total doesn't tell the teacher how much of a
          // purchase is theirs already (coinsReleased, for days actually
          // taught) vs. still sitting in the student's escrow
          // (remainingBalance) — the exact number a Refund tap would move.
          // Shown only when there's something to actually split out
          // (mirrors MyPassesScreen's same condition).
          if (p.status == 'success' && (p.coinsReleased > 0 || p.remainingBalance < p.coinsSpent)) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(child: _stat('You received', '${p.coinsReleased}')),
                Expanded(child: _stat('Held in escrow', '${p.remainingBalance}')),
              ],
            ),
          ],
          if (p.couponCode != null && p.couponCode!.isNotEmpty) ...[
            const SizedBox(height: 8),
            _stat('Coupon', p.couponCode!),
          ],
          if (canRefund) ...[
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                onPressed: busy ? null : () => _confirmRefund(p),
                style: OutlinedButton.styleFrom(foregroundColor: Colors.red, side: const BorderSide(color: Colors.red)),
                icon: busy
                    ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.red))
                    : const Icon(Icons.currency_exchange_rounded, size: 16),
                label: Text(busy ? 'Refunding…' : 'Refund'),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _stat(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: TextStyle(fontSize: 10.5, color: Colors.grey.shade500)),
        const SizedBox(height: 2),
        Text(value, style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600), maxLines: 1, overflow: TextOverflow.ellipsis),
      ],
    );
  }
}