// ============================================================
// LIVECLASS — WALLET SCREEN
//
// Backend surface used: GET /coin-transactions/balance/,
// GET /coin-transactions/, POST /coin-purchases/initiate/,
// GET/POST /withdrawals/, POST /withdrawals/{id}/cancel/.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';
import '../models/liveclass_models.dart';

class WalletScreen extends StatefulWidget {
  final LiveClassApi api;
  const WalletScreen({super.key, required this.api});

  @override
  State<WalletScreen> createState() => _WalletScreenState();
}

class _WalletScreenState extends State<WalletScreen> {
  int? _balance;
  List<CoinTransaction> _txns = const [];
  List<CoinWithdrawal> _withdrawals = const [];
  Object? _error;
  bool _loading = true;

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
        widget.api.coinBalance(),
        widget.api.coinTransactions(),
        widget.api.withdrawals(),
      ]);
      setState(() {
        _balance = results[0] as int;
        _txns = (results[1] as List).map((e) => CoinTransaction.fromJson(e as Map<String, dynamic>)).toList();
        _withdrawals = (results[2] as List).map((e) => CoinWithdrawal.fromJson(e as Map<String, dynamic>)).toList();
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _topUp(BuildContext context) async {
    final t = AppLocalizations.of(context)!;
    final ctrl = TextEditingController(text: '100');
    final coins = await showDialog<int>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(t.buyCoinsTitle),
        content: TextField(controller: ctrl, keyboardType: TextInputType.number),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: Text(t.cancelCta)),
          TextButton(onPressed: () => Navigator.pop(context, int.tryParse(ctrl.text)), child: Text(t.continueCta)),
        ],
      ),
    );
    if (coins == null || coins <= 0) return;
    try {
      final order = await widget.api.initiateCoinPurchase(coins);
      // ---- PAYMENT GATEWAY WIRING (fill in the one this project uses) ----
      // `order` is whatever CoinPurchaseViewSet.initiate() returns — an
      // order/session id from the configured gateway. Example shape for
      // Razorpay (`razorpay_flutter` package), the common choice for an
      // INR-denominated coin top-up:
      //
      //   final razorpay = Razorpay();
      //   razorpay.on(Razorpay.EVENT_PAYMENT_SUCCESS, (resp) async {
      //     await widget.api.verifyCoinPurchase(order['id'] as int, {
      //       'razorpay_payment_id': resp.paymentId,
      //       'razorpay_order_id': resp.orderId,
      //       'razorpay_signature': resp.signature,
      //     });
      //     if (mounted) { lsSnack(context, t.orderStartedMessage); _load(); }
      //   });
      //   razorpay.on(Razorpay.EVENT_PAYMENT_ERROR, (resp) {
      //     if (mounted) lsSnack(context, resp.message ?? '', error: true);
      //   });
      //   razorpay.open({
      //     'key': order['gateway_key'],
      //     'order_id': order['gateway_order_id'],
      //     'amount': order['amount_paise'],
      //     'currency': 'INR',
      //   });
      //
      // Left as a comment rather than a hard dependency because the
      // actual gateway (Razorpay/Cashfree/Stripe/etc.) wasn't specified
      // anywhere in the uploaded backend files — swap in whichever one
      // `settings.py` / `coin_purchase_views.py` actually configures.
      if (context.mounted) lsSnack(context, AppLocalizations.of(context)!.orderStartedMessage);
    } catch (e) {
      if (context.mounted) lsSnack(context, e.toString(), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.walletTitle),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? ListView(children: const [LsPostCardSkeleton()])
            : _error != null
                ? ErrorStateWidget(title: t.couldNotLoadWallet, retryLabel: t.retry, onRetry: _load)
                : ListView(children: [
                    Padding(
                      padding: const EdgeInsets.all(kLsPad),
                      child: LsCard(
                        child: Column(children: [
                          Text(t.coinBalanceLabel, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                          const SizedBox(height: 6),
                          Text('${_balance ?? 0}', style: LsType.head(context, size: 30)),
                          const SizedBox(height: 12),
                          Row(children: [
                            Expanded(child: LsPrimaryButton(label: t.buyCoinsCta, icon: Icons.add_rounded, onPressed: () => _topUp(context))),
                            const SizedBox(width: 10),
                            Expanded(child: LsOutlineButton(label: t.withdrawCta, icon: Icons.account_balance_wallet_outlined, onPressed: () {})),
                          ]),
                        ]),
                      ),
                    ),
                    LsSectionHead(title: t.transactionsTitle),
                    if (_txns.isEmpty)
                      EmptyStateWidget(title: t.noTransactionsYet, icon: Icons.receipt_long_outlined)
                    else
                      ..._txns.map((tx) => LsCard(
                            margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 8),
                            child: Row(children: [
                              Icon(tx.txnType == 'credit' ? Icons.add_circle_outline_rounded : Icons.remove_circle_outline_rounded,
                                  color: tx.txnType == 'credit' ? Colors.green : cs.error, size: 18),
                              const SizedBox(width: 10),
                              Expanded(child: Text(tx.reason, style: TextStyle(fontSize: 12.5, color: cs.onSurface))),
                              Text('${tx.txnType == 'credit' ? '+' : '-'}${tx.coins}', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurface)),
                            ]),
                          )),
                    if (_withdrawals.isNotEmpty) ...[
                      LsSectionHead(title: t.withdrawalsTitle),
                      ..._withdrawals.map((w) => LsCard(
                            margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 8),
                            child: Row(children: [
                              LsStatusChip(label: w.status.toUpperCase(), color: cs.primary),
                              const SizedBox(width: 10),
                              Expanded(child: Text('${w.coins} ${t.coinsUnit}', style: TextStyle(fontSize: 12.5, color: cs.onSurface))),
                              Text('₹${w.amountInr.toStringAsFixed(0)}', style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
                            ]),
                          )),
                    ],
                    const SizedBox(height: 24),
                  ]),
      ),
    );
  }
}
