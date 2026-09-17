// lib/wallet/screens/wallet_screen.dart
//
// Home ke "Wallet" quick action se yahan push karo:
//   Navigator.push(context, MaterialPageRoute(builder: (_) => const WalletScreen()));
//
// Security note: Ye screen sirf balance + history dikhati hai — koi bhi
// payment credential (card, UPI PIN, bank OTP) yahan kabhi capture nahi hoti.
// Top-up Razorpay ke apne secure checkout SDK se hota hai (wallet_topup_screen.dart),
// withdrawal sirf payout_details collect karti hai (bank/UPI), koi live-payment
// credential nahi.
//
// ✅ Endpoints confirmed (user_profile app) — `_load()` neeche `getBalance()`
// + `getLedger()` dono call karta hai, top-up/withdraw screen se wapas aane
// pe bhi (naya coin purchase server-side webhook se credit hota hai, is
// screen ka apna koi "confirm" call nahi hai — dekho wallet_service.dart
// header).

import 'package:flutter/material.dart';

import '../models/wallet_models.dart';
import '../services/wallet_service.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import 'wallet_topup_screen.dart';
import 'wallet_withdraw_screen.dart';

class WalletScreen extends StatefulWidget {
  const WalletScreen({super.key});

  @override
  State<WalletScreen> createState() => _WalletScreenState();
}

class _WalletScreenState extends State<WalletScreen> {
  WalletBalance? _balance;
  List<LedgerEntry> _ledger = [];
  bool _loading = true;
  bool _hasError = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _hasError = false;
    });
    try {
      // Balance short-TTL hi rakhna — money data stale-cache nahi honi chahiye,
      // isliye har screen-open pe fresh fetch, koi SharedPreferences cache nahi
      // (feed ke `_loadFeed` cache-first pattern se jaan-boojh kar alag).
      final results = await Future.wait([
        WalletService.instance.getBalance(),
        WalletService.instance.getLedger(),
      ]);
      setState(() {
        _balance = results[0] as WalletBalance;
        _ledger = results[1] as List<LedgerEntry>;
        _loading = false;
      });
    } catch (_) {
      setState(() {
        _loading = false;
        _hasError = true;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Wallet'),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _hasError
              ? ErrorStateWidget(title: 'Wallet load nahi ho paya', retryLabel: 'Retry', onRetry: _load)
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      _buildBalanceCard(scheme),
                      const SizedBox(height: 16),
                      Row(
                        children: [
                          Expanded(
                            child: LsPrimaryButton(
                              label: 'Add Coins',
                              onPressed: () async {
                                await Navigator.push(
                                  context,
                                  MaterialPageRoute(
                                    builder: (_) => const WalletTopupScreen(),
                                  ),
                                );
                                _load(); // wapas aane pe fresh balance
                              },
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: LsOutlineButton(
                              label: 'Withdraw',
                              onPressed: (_balance?.coinBalance ?? 0) >=
                                      kMinWithdrawalCoins
                                  ? () async {
                                      await Navigator.push(
                                        context,
                                        MaterialPageRoute(
                                          builder: (_) =>
                                              const WalletWithdrawScreen(),
                                        ),
                                      );
                                      _load();
                                    }
                                  : null, // min-withdrawal se kam ho to disabled
                            ),
                          ),
                        ],
                      ),
                      if ((_balance?.coinBalance ?? 0) < kMinWithdrawalCoins)
                        Padding(
                          padding: const EdgeInsets.only(top: 8),
                          child: Text(
                            'Withdraw ke liye kam se kam $kMinWithdrawalCoins coins chahiye.',
                            style: TextStyle(
                              fontSize: 12,
                              color: scheme.onSurfaceVariant,
                            ),
                          ),
                        ),
                      const SizedBox(height: 24),
                      LsSectionHead(title: 'Transaction History'),
                      const SizedBox(height: 8),
                      if (_ledger.isEmpty)
                        const EmptyStateWidget(title: 'Abhi koi transaction nahi hai')
                      else
                        ..._ledger.map((e) => _buildLedgerRow(e, scheme)),
                    ],
                  ),
                ),
    );
  }

  Widget _buildBalanceCard(ColorScheme scheme) {
    final coins = _balance?.coinBalance ?? 0;
    return LsCard(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Balance', style: TextStyle(color: scheme.onSurfaceVariant)),
            const SizedBox(height: 4),
            Text(
              '$coins coins',
              style: TextStyle(
                fontSize: 28,
                fontWeight: FontWeight.bold,
                color: scheme.onSurface,
              ),
            ),
            Text(
              '≈ ₹${(coins * kCoinToInrRate).toStringAsFixed(0)}',
              style: TextStyle(color: scheme.onSurfaceVariant),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildLedgerRow(LedgerEntry entry, ColorScheme scheme) {
    final isCredit = entry.type == LedgerEntryType.credit;
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: Icon(
        isCredit ? Icons.arrow_downward : Icons.arrow_upward,
        color: isCredit ? Colors.green : Colors.redAccent,
      ),
      title: Text(entry.reason),
      subtitle: Text('${entry.createdAt}'),
      trailing: Text(
        // ⚠️ `entry.amount` ab SIGNED hai (backend `CoinLedger.amount` ka
        // exact mirror) — `.magnitude` (unsigned) use karo, warna debit
        // row pe "--50" jaisa double-negative dikhega.
        '${isCredit ? '+' : '-'}${entry.magnitude}',
        style: TextStyle(
          fontWeight: FontWeight.bold,
          color: isCredit ? Colors.green : Colors.redAccent,
        ),
      ),
    );
  }
}