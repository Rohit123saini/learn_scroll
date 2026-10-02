// lib/wallet/screens/wallet_topup_screen.dart
//
// ============================================================
// 🔥 FIX (per request) — Razorpay hata diya.
//
// Ye file pehle poora Razorpay checkout flow try kar rahi thi
// (`razorpay_flutter` package + `RazorpayConfig.keyId`), lekin is
// project ke upload me `pubspec.yaml` hi nahi hai (sirf `lib/` +
// `backend/`) — matlab `razorpay_flutter` dependency kabhi actually
// add/build ho hi nahi sakti thi is upload ke against. Wo hissa ab
// hata diya gaya hai.
//
// Abhi ke liye payment gateway wire NAHI kiya gaya hai (jaise bola gaya,
// "abhi aise hi chod do") — ye screen sirf pack-selection UI dikhati hai
// aur "Continue to Pay" pe seedha ek "coming soon" message deti hai.
// Backend `POST /profile/buy-coin/` (`WalletService.initiatePurchase`)
// ko bhi is se call NAHI kiya — is se koi orphan PENDING
// `CoinPurchaseRequest` row backend me nahi banti jo kabhi complete hi
// nahi hogi.
//
// Jab bhi actual gateway (Razorpay ya koi aur) wire karna ho:
//   1. Iska package pubspec.yaml me add karo, `flutter pub get`.
//   2. `_startPurchase` me `WalletService.instance.initiatePurchase(...)`
//      call karo (jaisa `wallet_service.dart`'s header comment
//      documents), phir gateway ka checkout SDK open karo.
//   3. Success callback pe koi client-side "confirm"/"verify" endpoint
//      MAT call karna — `wallet_service.dart` ka header confirm karta
//      hai ki coin-credit backend webhook se hoti hai, client sirf
//      `refreshAfterPurchase()` karta hai.
// ============================================================

import 'package:flutter/material.dart';

import '../models/wallet_models.dart';
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

  void _continueToPay() {
    lsSnack(
      context,
      'Payments abhi available nahi hain — coin top-up thodi der me aa raha hai.',
    );
  }

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
            const SizedBox(height: 16),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: scheme.surfaceVariant.withOpacity(0.5),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Row(
                children: [
                  Icon(Icons.info_outline, size: 18, color: scheme.onSurfaceVariant),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Coin top-up abhi coming soon hai — payment gateway is samay setup nahi hai.',
                      style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 12.5),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 24),
            LsPrimaryButton(
              label: 'Continue to Pay',
              onPressed: _selectedPack != null ? _continueToPay : null,
            ),
          ],
        ),
      ),
    );
  }
}
