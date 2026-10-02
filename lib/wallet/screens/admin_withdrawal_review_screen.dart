// lib/wallet/screens/admin_withdrawal_review_screen.dart
//
// ============================================================
// ADMIN — COIN WITHDRAWAL REVIEW DASHBOARD
//
// Feature suggestion #1 (learnscroll_feature_suggestions.md). Backend
// side of this was already mostly built before this screen existed —
// `CoinWithdrawalRequest` already had `reviewed_by`/`MIN_WITHDRAWAL_COINS`/
// `amount_inr` (Task 38), and `CoinWithdrawalAdminActionView` already
// drove the PENDING -> PROCESSING -> SUCCESS/REJECTED lifecycle. The one
// piece that didn't exist anywhere — API or UI — was a way to actually
// SEE the queue of everyone's requests; this screen (+ the new
// `CoinWithdrawalAdminListView` / `WalletService.getAdminWithdrawals()`)
// is that.
//
// Staff-only: the backend enforces this (`IsAdminUser` on both the list
// and action endpoints) — a non-staff account gets a 403 from either
// call, surfaced here as a normal error state, not a client-side gate.
// This screen doesn't try to hide its entry point from non-admin users
// either; wherever it's linked from (an admin section of the app) is
// this app's own call, out of scope for this file.
// ============================================================

import 'package:flutter/material.dart';

import '../models/wallet_models.dart';
import '../services/wallet_service.dart';
import '../../widgets/ls_ui.dart';

/// Filter tabs shown above the list. `null` = every status.
const List<String?> _kStatusFilters = [
  null,
  CoinWithdrawalStatus.pending,
  CoinWithdrawalStatus.processing,
  CoinWithdrawalStatus.success,
  CoinWithdrawalStatus.rejected,
];

String _filterLabel(String? status) => switch (status) {
      null => 'All',
      CoinWithdrawalStatus.pending => 'Pending',
      CoinWithdrawalStatus.processing => 'Processing',
      CoinWithdrawalStatus.success => 'Success',
      CoinWithdrawalStatus.rejected => 'Rejected',
      _ => status!,
    };

Color _statusColor(BuildContext context, String status) {
  final cs = Theme.of(context).colorScheme;
  switch (status) {
    case CoinWithdrawalStatus.pending:
      return Colors.amber.shade700;
    case CoinWithdrawalStatus.processing:
      return Colors.blueAccent;
    case CoinWithdrawalStatus.success:
      return Colors.green.shade600;
    case CoinWithdrawalStatus.rejected:
      return cs.error;
    default:
      return cs.onSurfaceVariant;
  }
}

class AdminWithdrawalReviewScreen extends StatefulWidget {
  const AdminWithdrawalReviewScreen({super.key});

  @override
  State<AdminWithdrawalReviewScreen> createState() => _AdminWithdrawalReviewScreenState();
}

class _AdminWithdrawalReviewScreenState extends State<AdminWithdrawalReviewScreen> {
  int _filterIndex = 1; // default: Pending — that's the actual review queue.
  bool _loading = true;
  String? _error;
  List<AdminCoinWithdrawalRequest> _rows = const [];

  /// Row ids currently mid-action — disables that row's buttons only,
  /// not the whole screen, so approving one request doesn't freeze the
  /// list while it's in flight.
  final Set<int> _busyIds = {};

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
      final status = _kStatusFilters[_filterIndex];
      final rows = await WalletService.instance.getAdminWithdrawals(status: status);
      if (!mounted) return;
      setState(() {
        _rows = rows;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e is WalletApiException ? e.message : 'Withdrawal requests load nahi ho paayi.';
        _loading = false;
      });
    }
  }

  Future<void> _act(AdminCoinWithdrawalRequest row, String action, {String reason = ''}) async {
    setState(() => _busyIds.add(row.id));
    try {
      final updated = await WalletService.instance.adminWithdrawalAction(
        withdrawalId: row.id,
        action: action,
        reason: reason,
      );
      if (!mounted) return;
      setState(() {
        final i = _rows.indexWhere((r) => r.id == row.id);
        if (i != -1) {
          // Filtered view ("Pending" tab) me ab ye row wahan nahi rehni
          // chahiye agar status filter se bahar nikal gaya — seedha
          // remove kar do, poora reload karne ki zaroorat nahi.
          final statusFilter = _kStatusFilters[_filterIndex];
          if (statusFilter != null && updated.status != statusFilter) {
            _rows = List.of(_rows)..removeAt(i);
          } else {
            _rows = List.of(_rows)..[i] = updated;
          }
        }
      });
      if (mounted) {
        lsSnack(context, switch (action) {
          'processing' => 'Processing me move kar diya.',
          'success' => 'Withdrawal successful mark kar diya.',
          _ => 'Withdrawal reject kar diya, coins refund ho gaye.',
        });
      }
    } catch (e) {
      if (mounted) {
        final msg = e is WalletApiException
            ? (e.isConflict ? 'Ye request already ${row.status} hai — action apply nahi hoga.' : e.message)
            : 'Action fail ho gaya, dobara try karo.';
        lsSnack(context, msg, error: true);
      }
    } finally {
      if (mounted) setState(() => _busyIds.remove(row.id));
    }
  }

  Future<void> _confirmReject(AdminCoinWithdrawalRequest row) async {
    final controller = TextEditingController();
    final reason = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Reject withdrawal?'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${row.coins} coins ${row.user.username} ko wapas refund ho jaayenge.'),
            const SizedBox(height: 12),
            TextField(
              controller: controller,
              decoration: const InputDecoration(labelText: 'Reason (optional)'),
              maxLength: 255,
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            style: FilledButton.styleFrom(backgroundColor: Theme.of(ctx).colorScheme.error),
            child: const Text('Reject'),
          ),
        ],
      ),
    );
    if (reason != null) {
      await _act(row, 'reject', reason: reason);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Withdrawal Requests'),
      body: Column(
        children: [
          const SizedBox(height: kSpaceSm),
          LsFilterChips(
            labels: _kStatusFilters.map(_filterLabel).toList(),
            selectedIndex: _filterIndex,
            onSelected: (i) {
              setState(() => _filterIndex = i);
              _load();
            },
          ),
          const SizedBox(height: kSpaceSm),
          Expanded(child: _buildBody()),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(kLsPad),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(_error!, textAlign: TextAlign.center),
              const SizedBox(height: kSpaceMd),
              LsOutlineButton(label: 'Retry', onPressed: _load),
            ],
          ),
        ),
      );
    }
    if (_rows.isEmpty) {
      return Center(
        child: Text(
          '${_filterLabel(_kStatusFilters[_filterIndex])} withdrawal requests nahi hain.',
          style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant),
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.builder(
        padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, kLsPad),
        itemCount: _rows.length,
        itemBuilder: (context, i) => _WithdrawalRow(
          row: _rows[i],
          busy: _busyIds.contains(_rows[i].id),
          onMarkProcessing: () => _act(_rows[i], 'processing'),
          onMarkSuccess: () => _act(_rows[i], 'success'),
          onReject: () => _confirmReject(_rows[i]),
        ),
      ),
    );
  }
}

class _WithdrawalRow extends StatelessWidget {
  final AdminCoinWithdrawalRequest row;
  final bool busy;
  final VoidCallback onMarkProcessing;
  final VoidCallback onMarkSuccess;
  final VoidCallback onReject;

  const _WithdrawalRow({
    required this.row,
    required this.busy,
    required this.onMarkProcessing,
    required this.onMarkSuccess,
    required this.onReject,
  });

  String get _payoutSummary {
    if (row.payoutMethod == PayoutMethod.upi) {
      return 'UPI — ${row.payoutDetails['upi_id'] ?? '—'}';
    }
    if (row.payoutMethod == PayoutMethod.bankTransfer) {
      final acct = (row.payoutDetails['account_number'] ?? '').toString();
      final masked = acct.length > 4 ? '••••${acct.substring(acct.length - 4)}' : acct;
      return 'Bank — ${row.payoutDetails['account_holder'] ?? '—'} ($masked, ${row.payoutDetails['ifsc'] ?? '—'})';
    }
    return row.payoutMethod.isEmpty ? '—' : row.payoutMethod;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return LsCard(
      margin: const EdgeInsets.only(bottom: kSpaceMd),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(row.user.username,
                    style: LsType.head(context, size: 14), overflow: TextOverflow.ellipsis),
              ),
              LsStatusChip(label: row.status.toUpperCase(), color: _statusColor(context, row.status)),
            ],
          ),
          if (row.user.email != null || row.user.phone != null) ...[
            const SizedBox(height: 2),
            Text(
              [row.user.email, row.user.phone].whereType<String>().join(' · '),
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
            ),
          ],
          const Divider(height: kSpaceLg),
          LsMetaRow(icon: Icons.monetization_on_outlined, label: 'Coins', value: '${row.coins}'),
          LsMetaRow(
            icon: Icons.currency_rupee,
            label: 'Payout amount',
            value: '₹${row.amountInr.toStringAsFixed(2)}',
          ),
          LsMetaRow(icon: Icons.account_balance_wallet_outlined, label: 'Payout to', value: _payoutSummary),
          if (row.reviewedBy != null)
            LsMetaRow(
              icon: Icons.verified_user_outlined,
              label: 'Reviewed by',
              value: row.reviewedBy!.username,
            ),
          if (row.status == CoinWithdrawalStatus.rejected && row.failureReason.isNotEmpty)
            LsMetaRow(
              icon: Icons.info_outline,
              label: 'Reason',
              value: row.failureReason,
              valueColor: cs.error,
            ),
          if (row.createdAt != null)
            LsMetaRow(
              icon: Icons.schedule,
              label: 'Requested',
              value: _formatDate(row.createdAt!),
            ),
          if (row.isActionable) ...[
            const SizedBox(height: kSpaceSm),
            Row(
              children: [
                if (row.isPending)
                  Expanded(
                    child: LsOutlineButton(
                      label: busy ? '...' : 'Processing',
                      onPressed: busy ? null : onMarkProcessing,
                    ),
                  ),
                if (row.isProcessing)
                  Expanded(
                    child: LsPrimaryButton(
                      label: 'Mark paid',
                      loading: busy,
                      onPressed: busy ? null : onMarkSuccess,
                    ),
                  ),
                const SizedBox(width: kSpaceSm),
                Expanded(
                  child: LsOutlineButton(
                    label: 'Reject',
                    onPressed: busy ? null : onReject,
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  String _formatDate(DateTime d) {
    final local = d.toLocal();
    String two(int n) => n.toString().padLeft(2, '0');
    return '${two(local.day)}/${two(local.month)}/${local.year} ${two(local.hour)}:${two(local.minute)}';
  }
}
