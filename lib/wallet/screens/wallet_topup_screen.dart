// lib/wallet/screens/wallet_topup_screen.dart
//
// Coin top-up flow: server order create -> Razorpay checkout SDK -> server verify.
// IMPORTANT (security): Razorpay signature verification hamesha SERVER-SIDE hoti hai
// (`CoinPurchaseRequest.confirm_success()`). Client kabhi khud "payment successful"
// nahi maanta — sirf gateway se mile raw `payment_id`/`signature` ko backend ko
// forward karta hai, backend hi final decide karta hai (§WalletService.verifyPurchase).
//
// Package: `razorpay_flutter` — pubspec.yaml me add karo agar already nahi hai.

import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';
// import 'package:razorpay_flutter/razorpay_flutter.dart'; // TODO: uncomment jab package add ho

import '../models/wallet_models.dart';
import '../services/wallet_service.dart';
import '../../widgets/ls_ui.dart';

class WalletTopupScreen extends StatefulWidget {
  const WalletTopupScreen({super.key});

  @override
  State<WalletTopupScreen> createState() => _WalletTopupScreenState();
}

class _WalletTopupScreenState extends State<WalletTopupScreen> {
  // Preset coin packs — product decision, backend se confirm karo denominations.
  static const List<int> _presetPacks = [100, 250, 500, 1000, 2500];

  int? _selectedPack;
  bool _processing = false;
  String? _error;

  // late final Razorpay _razorpay; // TODO: uncomment jab package add ho

  @override
  void initState() {
    super.initState();
    // _razorpay = Razorpay();
    // _razorpay.on(Razorpay.EVENT_PAYMENT_SUCCESS, _onPaymentSuccess);
    // _razorpay.on(Razorpay.EVENT_PAYMENT_ERROR, _onPaymentError);
  }

  @override
  void dispose() {
    // _razorpay.clear();
    super.dispose();
  }

  CoinPurchaseRequest? _pendingRequest;

  Future<void> _startPurchase() async {
    if (_selectedPack == null) return;
    setState(() {
      _processing = true;
      _error = null;
    });
    try {
      // 🔥 FIX — `initiatePurchase()` ko `gatewayReference` (idempotency
      // key — same reference dobara bhejne pe backend wahi pending
      // request lauta deta hai, duplicate nahi banata) aur `amount`
      // (rupees, coins * kCoinToInrRate) bhi chahiye — pehle sirf `coins`
      // bheja jaa raha tha.
      final gatewayReference = const Uuid().v4();
      final amount = _selectedPack! * kCoinToInrRate;
      final request = await WalletService.instance.initiatePurchase(
        gatewayReference: gatewayReference,
        amount: amount,
        coins: _selectedPack!,
      );
      _pendingRequest = request;

      // 🔥 FIX — `CoinPurchaseRequest` (wallet_models.dart) ke paas
      // `razorpayOrderId` naam ka koi field nahi hai — sirf
      // id/gateway/gatewayReference/amount/coins/status/failureReason/
      // createdAt/updatedAt. Razorpay order kahan/kaise banta hai wallet_
      // service.dart ke header ke hisaab se abhi is upload me confirm
      // nahi hai ("CONFIRM WITH BACKEND"), isliye us field ka null-check
      // yahan hata diya — jab backend confirm ho jaaye ki order-id kis
      // field me aata hai, yahan wapas guard add karna.

      // TODO: uncomment jab razorpay_flutter add ho jaaye —
      // var options = {
      //   'key': '<RAZORPAY_KEY_ID>', // TODO: apna Razorpay publishable key
      //   'amount': (request.amount * 100).toInt(), // paise me
      //   'order_id': request.gatewayReference, // TODO: confirm real order-id field
      //   'name': 'LearnScroll',
      //   'description': '${request.coins} coins top-up',
      // };
      // _razorpay.open(options);

      setState(() => _processing = false);
    } catch (e) {
      setState(() {
        _processing = false;
        _error = 'Purchase start nahi ho paya, dobara try karo.';
      });
    }
  }

  // void _onPaymentSuccess(PaymentSuccessResponse response) async {
  //   if (_pendingRequest == null) return;
  //   setState(() => _processing = true);
  //   try {
  //     await WalletService.instance.verifyPurchase(
  //       orderId: _pendingRequest!.id,
  //       paymentId: response.paymentId!,
  //       signature: response.signature!,
  //     );
  //     if (mounted) {
  //       lsSnack(context, 'Coins add ho gaye!');
  //       Navigator.pop(context);
  //     }
  //   } catch (_) {
  //     setState(() => _error = 'Payment verify nahi ho paya. Support se contact karo agar amount deduct hua ho.');
  //   } finally {
  //     if (mounted) setState(() => _processing = false);
  //   }
  // }

  // void _onPaymentError(PaymentFailureResponse response) {
  //   setState(() => _error = 'Payment cancel/fail ho gaya.');
  // }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Add Coins'),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Pack choose karo', style: TextStyle(color: scheme.onSurfaceVariant)),
            const SizedBox(height: 12),
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: _presetPacks.map((coins) {
                final selected = _selectedPack == coins;
                return ChoiceChip(
                  label: Text('$coins coins  •  ₹${(coins * kCoinToInrRate).toStringAsFixed(0)}'),
                  selected: selected,
                  onSelected: (_) => setState(() => _selectedPack = coins),
                );
              }).toList(),
            ),
            const SizedBox(height: 24),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(_error!, style: const TextStyle(color: Colors.redAccent)),
              ),
            LsPrimaryButton(
              label: _processing ? 'Processing...' : 'Continue to Pay',
              onPressed: (_selectedPack != null && !_processing) ? _startPurchase : null,
            ),
          ],
        ),
      ),
    );
  }
}
