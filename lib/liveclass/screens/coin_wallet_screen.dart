// lib/liveclass/screens/coin_wallet_screen.dart
//
// Screen 10 — Coin Wallet (see LIVECLASS_SCREEN_ARCHITECTURE.md §10).
//
// This screen was missing from the module even though its API was already
// built in liveclass_api_service.dart (CoinTransactionApi) and it's part
// of the LIVECLASS_SCREEN_ARCHITECTURE.md folder layout — added to close
// that gap.
//
// Not part of the classroom flow itself — like Notifications/My Passes/
// Wishlist, this is meant to be pushed from wherever the app's coin
// balance / wallet icon lives (home app-bar, profile menu, etc.), e.g.:
//   IconButton(
//     icon: const Icon(Icons.monetization_on_outlined),
//     onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const CoinWalletScreen())),
//   )
//
// API: `GET coin-transactions/` (own ledger, newest first) +
// `GET coin-transactions/balance/`. Both fetched in parallel on open.
// Read-only — coins are only ever earned/spent as a side effect of other
// actions (pass purchase, refund, referral bonus, admin top-up), never
// directly from this screen.
//
// Now on the shared LiveClass design system (liveclass_theme.dart).

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

String _reasonLabel(String reason) {
  switch (reason) {
    case 'pass_purchase':
      return 'Pass Purchase';
    case 'refund':
      return 'Refund';
    case 'referral_bonus':
      return 'Referral Bonus';
    case 'topup':
      return 'Top-up';
    case 'admin':
      return 'Admin Adjustment';
    default:
      return reason.isEmpty ? 'Transaction' : reason;
  }
}

IconData _reasonIcon(String reason) {
  switch (reason) {
    case 'pass_purchase':
      return Icons.confirmation_number_outlined;
    case 'refund':
      return Icons.replay_rounded;
    case 'referral_bonus':
      return Icons.card_giftcard_rounded;
    case 'topup':
      return Icons.add_card_rounded;
    case 'admin':
      return Icons.admin_panel_settings_outlined;
    default:
      return Icons.swap_horiz_rounded;
  }
}

// ===========================================================================
// SCREEN
// ===========================================================================
class CoinWalletScreen extends StatefulWidget {
  const CoinWalletScreen({super.key});

  @override
  State<CoinWalletScreen> createState() => _CoinWalletScreenState();
}

class _CoinWalletScreenState extends State<CoinWalletScreen> {
  List<CoinTransaction> _ledger = [];
  int _balance = 0;
  bool _loading = true;
  String? _error;

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
        LiveClassApi.coinTransactions.myLedger(),
        LiveClassApi.coinTransactions.balance(),
      ]);
      final ledger = (results[0] as PaginatedList<CoinTransaction>).results.toList()
        ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
      final balance = results[1] as int;
      if (!mounted) return;
      setState(() {
        _ledger = ledger;
        _balance = balance;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load wallet.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar('Coin Wallet'),
      body: _loading
          ? const LiveClassLoading()
          : _error != null
              ? LiveClassErrorState(message: _error!, onRetry: _load)
              : RefreshIndicator(
                  color: LiveClassColors.navy,
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
                    children: [
                      _balanceCard(),
                      const SizedBox(height: 22),
                      Text('Transaction History', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: Colors.grey.shade800)),
                      const SizedBox(height: 10),
                      if (_ledger.isEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 50),
                          child: Center(
                            child: Column(
                              children: [
                                Icon(Icons.receipt_long_outlined, size: 40, color: Colors.grey.shade400),
                                const SizedBox(height: 10),
                                Text('No transactions yet.', style: TextStyle(color: Colors.grey.shade500, fontSize: 13)),
                              ],
                            ),
                          ),
                        )
                      else
                        ..._ledger.map(_txnTile),
                    ],
                  ),
                ),
    );
  }

  Widget _balanceCard() {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        gradient: LiveClassColors.gradient,
        borderRadius: BorderRadius.circular(18),
        boxShadow: [BoxShadow(color: const Color(0xFFEE0979).withValues(alpha: 0.25), blurRadius: 16, offset: const Offset(0, 6))],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Current Balance', style: TextStyle(color: Colors.white70, fontSize: 12.5, fontWeight: FontWeight.w600, letterSpacing: 0.3)),
          const SizedBox(height: 8),
          Row(
            children: [
              const Icon(Icons.monetization_on_rounded, color: Colors.white, size: 28),
              const SizedBox(width: 8),
              Text('$_balance', style: const TextStyle(color: Colors.white, fontSize: 32, fontWeight: FontWeight.bold)),
              const SizedBox(width: 6),
              const Padding(padding: EdgeInsets.only(top: 10), child: Text('coins', style: TextStyle(color: Colors.white70, fontSize: 13))),
            ],
          ),
        ],
      ),
    );
  }

  Widget _txnTile(CoinTransaction t) {
    final isCredit = t.txnType == CoinTxnType.credit;
    final color = isCredit ? LiveClassColors.success : LiveClassColors.danger;
    return LiveClassCard(
      padding: const EdgeInsets.all(12),
      margin: const EdgeInsets.only(bottom: 10),
      child: Row(
        children: [
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(color: color.withValues(alpha: 0.1), shape: BoxShape.circle),
            alignment: Alignment.center,
            child: Icon(_reasonIcon(t.reason), size: 17, color: color),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(_reasonLabel(t.reason), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
                const SizedBox(height: 2),
                Text(liveClassFmtDateTime(t.createdAt), style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text('${isCredit ? '+' : '-'}${t.amount}', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: color)),
              const SizedBox(height: 2),
              Text('Bal: ${t.balanceAfter}', style: TextStyle(fontSize: 10.5, color: Colors.grey.shade400)),
            ],
          ),
        ],
      ),
    );
  }
}