// lib/liveclass/screens/my_passes_screen.dart
//
// Screen 9 — My Passes / Purchase History (see
// LIVECLASS_SCREEN_ARCHITECTURE.md §9).
// API: GET pass-purchases/ (read-only, scoped to the caller's own purchases
// by the backend). Refund is a teacher/staff-only action performed from
// their manage panel, not here — this screen only ever displays status.
//
// Now on the shared LiveClass design system (liveclass_theme.dart).

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import 'pass_gift_claim_screen.dart';

String _passTypeLabel(String type) {
  switch (type) {
    case PassType.free:
      return 'Free';
    case PassType.daily:
      return 'Daily';
    case PassType.weekly:
      return 'Weekly';
    case PassType.monthly:
      return 'Monthly';
    case PassType.yearly:
      return 'Yearly';
    default:
      return type;
  }
}

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
      return LiveClassColors.success;
    case 'refunded':
      return Colors.grey.shade600;
    case 'failed':
      return LiveClassColors.danger;
    default:
      return LiveClassColors.warning;
  }
}

// ===========================================================================
// SCREEN
// ===========================================================================
class MyPassesScreen extends StatefulWidget {
  const MyPassesScreen({super.key});

  @override
  State<MyPassesScreen> createState() => _MyPassesScreenState();
}

class _MyPassesScreenState extends State<MyPassesScreen> {
  List<PassPurchase> _all = [];
  bool _loading = true;
  String? _error;
  bool _activeOnly = false;
  final Set<int> _busyIds = {}; // cancel in flight per-purchase
  final Set<int> _autoRenewBusyIds = {}; // auto-renew toggle in flight per-purchase

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
      final res = await LiveClassApi.passPurchases.myPurchases();
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
        _error = e is LiveClassApiException ? e.message : 'Could not load purchase history.';
      });
    }
  }

  List<PassPurchase> get _visible => _activeOnly ? _all.where((p) => p.isValid).toList() : _all;

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  // NOTE (fix — student had no way to stop paying for a pass they no
  // longer want): PassPurchase.reverse() / the pass-purchases/{id}/cancel/
  // endpoint have existed on the backend for the student's own use, but
  // nothing on this screen ever called it. Only remainingBalance (coins
  // still sitting in escrow for days nobody's taught yet) comes back —
  // whatever's already been released to the teacher for classes actually
  // held stays with them. Confirmation dialog quotes that exact number so
  // a student never sees a bigger refund promised than they'll actually get.
  Future<void> _confirmCancel(PassPurchase p) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Cancel Pass?'),
        content: Text(
          p.remainingBalance > 0
              ? '${p.remainingBalance} coins will be refunded to your wallet (the amount already released to the teacher '
                  'for classes already held is not refundable). Access will end immediately. This cannot be undone.'
              : 'There is no refundable balance left on this pass (the full amount has already been released to the teacher). '
                  'Access will end immediately. This cannot be undone.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Go Back')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Cancel Pass', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;

    setState(() => _busyIds.add(p.id));
    try {
      await LiveClassApi.passPurchases.cancel(p.id);
      _snack('Pass cancelled.');
      await _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not cancel pass.');
    } finally {
      if (mounted) setState(() => _busyIds.remove(p.id));
    }
  }

  // NEW (Pass 15 frontend catch-up §1.8) — toggle auto_renew on a purchase.
  // Optimistic swap first (this list is read via LiveClassApi.passPurchases
  // .myPurchases(), which has no per-item copyWith exposed here, so the
  // simplest correct approach is to just replace the whole item with the
  // server's response once it lands, same as _confirmCancel's _load()
  // refresh but without a full-list reload for a single-field flip).
  Future<void> _toggleAutoRenew(PassPurchase p, bool next) async {
    setState(() => _autoRenewBusyIds.add(p.id));
    try {
      final updated = await LiveClassApi.passPurchases.setAutoRenew(p.id, next);
      if (!mounted) return;
      setState(() {
        final idx = _all.indexWhere((x) => x.id == p.id);
        if (idx != -1) _all[idx] = updated;
      });
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not update auto-renew.');
    } finally {
      if (mounted) setState(() => _autoRenewBusyIds.remove(p.id));
    }
  }

  // NEW (Pass 14 frontend catch-up §1.3) — "Gift this pass" entry point 1
  // (frontend doc §1.3). Reuses the module's "ID-only, no user-search"
  // limitation (frontend doc §8) — same pattern PassGiftApi.send already
  // documents.
  Future<void> _openGiftSheet(PassPurchase p) async {
    final ctrl = TextEditingController();
    final recipient = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (sheetCtx) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.of(sheetCtx).viewInsets.bottom),
        child: Container(
          decoration: const BoxDecoration(color: Colors.white, borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Gift this pass', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
              const SizedBox(height: 6),
              Text(
                'Enter the recipient\'s username or email. They\'ll have 7 days to claim it.',
                style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600),
              ),
              const SizedBox(height: 16),
              TextField(controller: ctrl, decoration: liveClassInputDecoration('Username or email')),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(backgroundColor: LiveClassColors.navy, foregroundColor: Colors.white),
                  onPressed: () => Navigator.pop(sheetCtx, ctrl.text.trim()),
                  child: const Text('Send Gift'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
    if (recipient == null || recipient.isEmpty) return;
    try {
      await LiveClassApi.passGifts.send(classPassId: p.classPassId, recipient: recipient);
      _snack('Gift sent to $recipient.');
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not send gift.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(
        'My Passes',
        actions: [
          // FIX (orphan screen — PassGiftClaimScreen had zero navigation
          // edges anywhere in the app despite being fully built and backed
          // by a ready API). This is entry point #2 from that screen's own
          // header comment: "general browsing — a 'My gifts' entry from
          // my_passes_screen.dart". Opens on the Received/Sent tabs (no
          // giftId), matching PassGiftClaimScreen()'s no-id constructor.
          IconButton(
            tooltip: 'Gifted passes',
            icon: const Icon(Icons.card_giftcard_outlined),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => PassGiftClaimScreen(
                  onClaimed: (claimed) {
                    // A claimed gift grants access to a classroom — refresh
                    // this list so the newly-claimed pass shows up without
                    // requiring a manual pull-to-refresh.
                    _load();
                  },
                ),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: Center(
              child: FilterChip(
                label: const Text('Active only', style: TextStyle(fontSize: 12)),
                selected: _activeOnly,
                onSelected: (v) => setState(() => _activeOnly = v),
                selectedColor: LiveClassColors.navy.withValues(alpha: 0.1),
                checkmarkColor: LiveClassColors.navy,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
              ),
            ),
          ),
        ],
      ),
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _visible.isEmpty
                    ? const LiveClassEmptyState(icon: Icons.card_membership_outlined, title: 'No passes purchased yet.')
                    : ListView.builder(
                        padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
                        itemCount: _visible.length,
                        itemBuilder: (_, i) => _passCard(_visible[i]),
                      ),
      ),
    );
  }

  Widget _passCard(PassPurchase p) {
    final color = _statusColor(p.status);
    final now = DateTime.now();
    final expired = p.expiresAt.isBefore(now);
    final progress = (p.maxClasses != null && p.maxClasses! > 0) ? p.classesAttended / p.maxClasses! : null;

    return LiveClassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const LiveClassIconBadge(icon: Icons.card_membership_rounded, size: 40),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(p.classroomTitle.isNotEmpty ? p.classroomTitle : 'Classroom',
                        style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14), maxLines: 1, overflow: TextOverflow.ellipsis),
                    Text(
                      p.classPassTitle.isNotEmpty ? p.classPassTitle : _passTypeLabel(p.classPassType),
                      style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500),
                    ),
                  ],
                ),
              ),
              LiveClassStatusChip(label: _statusLabel(p.status).toUpperCase(), color: color, background: color.withValues(alpha: 0.12)),
            ],
          ),
          // NEW (Pass 14 frontend catch-up §1.3) — "Gifted to you by X"
          // badge. Only shown when the backend actually resolved a
          // gifter (giftId/giftedBy on PassPurchase — unconfirmed field,
          // see liveclass_models.dart), so a normal purchase shows
          // nothing extra here.
          if (p.giftId != null && p.giftedBy != null) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                Icon(Icons.card_giftcard_rounded, size: 14, color: Colors.pink.shade300),
                const SizedBox(width: 6),
                Text('Gifted to you by ${p.giftedBy!.fullName}',
                    style: TextStyle(fontSize: 11.5, color: Colors.pink.shade400, fontWeight: FontWeight.w600)),
              ],
            ),
          ],
          const Divider(height: 22),
          Row(
            children: [
              Expanded(child: _statLabel('Purchased', liveClassFmtDate(p.purchasedAt))),
              Expanded(
                child: _statLabel(
                  'Expires',
                  liveClassFmtDate(p.expiresAt),
                  color: expired && p.status == 'success' ? Colors.red.shade400 : null,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(child: _statLabel('Coins Spent', '${p.coinsSpent}')),
              if (p.couponCode != null && p.couponCode!.isNotEmpty) Expanded(child: _statLabel('Coupon', p.couponCode!)),
            ],
          ),
          // NOTE (fix — the whole point of the escrow design was invisible
          // here): coinsSpent alone doesn't tell a student how much of
          // their pass is still "at risk" (un-taught, refundable) vs.
          // already paid out to the teacher for classes actually held —
          // that's exactly what remainingBalance is. Only shown once a
          // purchase has actually had at least one day released
          // (coinsReleased > 0) OR the pass has since expired with money
          // still stuck — a fresh, fully-unreleased purchase would just
          // show remainingBalance == coinsSpent, which is redundant with
          // the row above.
          if (p.status == 'success' && (p.coinsReleased > 0 || p.remainingBalance < p.coinsSpent)) ...[
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(child: _statLabel('Released to Teacher', '${p.coinsReleased}')),
                Expanded(
                  child: _statLabel(
                    'Held in escrow (refundable)',
                    '${p.remainingBalance}',
                    color: p.remainingBalance > 0 ? LiveClassColors.navy : null,
                  ),
                ),
              ],
            ),
          ],
          if (progress != null) ...[
            const SizedBox(height: 12),
            Text('Classes: ${p.classesAttended}/${p.maxClasses}', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
            const SizedBox(height: 4),
            ClipRRect(
              borderRadius: BorderRadius.circular(6),
              child: LinearProgressIndicator(
                value: progress.clamp(0, 1).toDouble(),
                minHeight: 6,
                backgroundColor: LiveClassColors.bg,
                valueColor: const AlwaysStoppedAnimation(LiveClassColors.navy),
              ),
            ),
          ],
          if (expired && p.status == 'success' && !p.isValid) ...[
            const SizedBox(height: 10),
            Row(
              children: [
                Icon(Icons.info_outline_rounded, size: 14, color: Colors.orange.shade700),
                const SizedBox(width: 6),
                Expanded(
                  child: Text('This pass has expired. Renew to continue access.',
                      style: TextStyle(fontSize: 11.5, color: Colors.orange.shade800)),
                ),
              ],
            ),
          ],
          // NEW (Pass 15 frontend catch-up §1.8) — auto-renew failure
          // banner. Shown instead of letting a failed renewal read as a
          // plain "expired" pass with no explanation.
          if (p.renewalFailedAt != null) ...[
            const SizedBox(height: 10),
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(color: LiveClassColors.dangerBg, borderRadius: BorderRadius.circular(10)),
              child: Row(
                children: [
                  const Icon(Icons.error_outline_rounded, size: 16, color: LiveClassColors.danger),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Auto-renew failed on ${liveClassFmtDate(p.renewalFailedAt!)} — renew manually to keep access.',
                      style: const TextStyle(fontSize: 11.5, color: LiveClassColors.danger),
                    ),
                  ),
                ],
              ),
            ),
          ],
          // NEW (Pass 15 §1.8) — renewal-chain provenance footnote.
          if (p.renewedFrom != null) ...[
            const SizedBox(height: 6),
            Text('Renewed from purchase #${p.renewedFrom}', style: TextStyle(fontSize: 10.5, color: Colors.grey.shade500)),
          ],
          // NEW (Pass 15 §1.8) — auto-renew toggle, only on an active,
          // unexpired, non-free purchase (nothing to renew on a free pass).
          if (p.status == 'success' && !expired) ...[
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: Text('Auto-renew', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: Colors.grey.shade800)),
                ),
                _autoRenewBusyIds.contains(p.id)
                    ? const SizedBox(
                        width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: LiveClassColors.navy))
                    : Switch(
                        value: p.autoRenew,
                        onChanged: (v) => _toggleAutoRenew(p, v),
                        activeThumbColor: LiveClassColors.navy,
                      ),
              ],
            ),
          ],
          // NOTE (fix — no self-service cancel existed on this screen):
          // only offered while the pass is still SUCCESS and unexpired —
          // an already-expired pass's leftover escrow is handled
          // automatically by the backend's expire_and_refund_passes sweep,
          // so there's nothing useful for the student to trigger by hand
          // here once it's past expiresAt (and reverse() would just be a
          // race against that same sweep for no benefit).
          if (p.status == 'success' && !expired) ...[
            const SizedBox(height: 12),
            Row(
              children: [
                // NEW (Pass 14 §1.3) — "Gift this pass" entry point 1.
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => _openGiftSheet(p),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: LiveClassColors.navy,
                      side: const BorderSide(color: LiveClassColors.navy),
                    ),
                    icon: const Icon(Icons.card_giftcard_outlined, size: 16),
                    label: const Text('Gift'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _busyIds.contains(p.id) ? null : () => _confirmCancel(p),
                    style: OutlinedButton.styleFrom(foregroundColor: Colors.red, side: const BorderSide(color: Colors.red)),
                    icon: _busyIds.contains(p.id)
                        ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.red))
                        : const Icon(Icons.cancel_outlined, size: 16),
                    label: Text(_busyIds.contains(p.id) ? 'Cancelling…' : 'Cancel Pass'),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _statLabel(String label, String value, {Color? color}) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: TextStyle(fontSize: 10.5, color: Colors.grey.shade500)),
        const SizedBox(height: 2),
        Text(value, style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: color ?? Colors.black87)),
      ],
    );
  }
}