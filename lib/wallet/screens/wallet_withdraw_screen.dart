// lib/wallet/screens/wallet_withdraw_screen.dart
//
// Withdrawal form — bank ya UPI, jo bhi `payout_details` shape backend
// docs se confirmed hai (§6e, LEARNSCROLL_LIVECLASS.md).
// Client-side `kMinWithdrawalCoins` check sirf UX ke liye — asli enforcement
// server-side hi honi chahiye (WalletService me bhi duplicate check hai,
// defence-in-depth, security guarantee client-side se nahi maani jaani chahiye).

import 'package:flutter/material.dart';

import '../models/wallet_models.dart';
import '../services/wallet_service.dart';
import '../../widgets/ls_ui.dart';

enum _PayoutMethod { bank, upi }

class WalletWithdrawScreen extends StatefulWidget {
  const WalletWithdrawScreen({super.key});

  @override
  State<WalletWithdrawScreen> createState() => _WalletWithdrawScreenState();
}

class _WalletWithdrawScreenState extends State<WalletWithdrawScreen> {
  final _formKey = GlobalKey<FormState>();
  final _coinsController = TextEditingController();

  final _accountHolderController = TextEditingController();
  final _accountNumberController = TextEditingController();
  final _ifscController = TextEditingController();
  final _upiIdController = TextEditingController();

  _PayoutMethod _method = _PayoutMethod.upi;
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _coinsController.dispose();
    _accountHolderController.dispose();
    _accountNumberController.dispose();
    _ifscController.dispose();
    _upiIdController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;

    final coins = int.tryParse(_coinsController.text) ?? 0;
    setState(() {
      _submitting = true;
      _error = null;
    });

    try {
      // 🔥 FIX — `BankPayoutDetails`/`UpiPayoutDetails` alag classes nahi
      // hain, `wallet_models.dart` me ek hi `PayoutDetails` class hai do
      // named constructors ke saath.
      final payout = _method == _PayoutMethod.bank
          ? PayoutDetails.bankTransfer(
              accountHolder: _accountHolderController.text.trim(),
              accountNumber: _accountNumberController.text.trim(),
              ifsc: _ifscController.text.trim(),
            )
          : PayoutDetails.upi(upiId: _upiIdController.text.trim());

      await WalletService.instance.requestWithdrawal(
        coins: coins,
        payoutDetails: payout,
      );

      if (mounted) {
        lsSnack(context, 'Withdrawal request bhej diya, review pending hai.');
        Navigator.pop(context);
      }
    } catch (e) {
      setState(() {
        // 🔥 FIX — `WalletApiException` ke paas `.code` naam ka field nahi
        // hai, `.message` (jo yahi 'below_minimum_withdrawal' string hoti
        // hai jab requestWithdrawal() ka apna client-side pre-check trigger
        // hota hai) aur `.statusCode` hain. Saath hi wallet_service.dart ke
        // header me documented doosri do real rejection shapes (402/403)
        // bhi yahan handle kar di — pehle sirf ek hi case pakda jaa raha
        // tha, baaki "Withdrawal request fail ho gayi" generic message me
        // chup jaate the.
        if (e is WalletApiException && e.message == 'below_minimum_withdrawal') {
          _error = 'Minimum $kMinWithdrawalCoins coins withdraw kar sakte ho.';
        } else if (e is WalletApiException && e.isInsufficientBalance) {
          _error = 'Itne coins available nahi hain.';
        } else if (e is WalletApiException && e.isEligibilityRejected) {
          _error = 'Itne withdrawal-eligible coins nahi hain — kuch coins withdraw ke liye eligible nahi hote.';
        } else {
          _error = 'Withdrawal request fail ho gayi, dobara try karo.';
        }
      });
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Withdraw'),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextFormField(
              controller: _coinsController,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(
                labelText: 'Coins to withdraw (min $kMinWithdrawalCoins)',
              ),
              validator: (v) {
                final n = int.tryParse(v ?? '');
                if (n == null) return 'Valid number daalo';
                if (n < kMinWithdrawalCoins) {
                  return 'Minimum $kMinWithdrawalCoins coins chahiye';
                }
                return null;
              },
            ),
            const SizedBox(height: 20),
            SegmentedButton<_PayoutMethod>(
              segments: const [
                ButtonSegment(value: _PayoutMethod.upi, label: Text('UPI')),
                ButtonSegment(value: _PayoutMethod.bank, label: Text('Bank')),
              ],
              selected: {_method},
              onSelectionChanged: (s) => setState(() => _method = s.first),
            ),
            const SizedBox(height: 20),
            if (_method == _PayoutMethod.upi)
              TextFormField(
                controller: _upiIdController,
                decoration: const InputDecoration(labelText: 'UPI ID'),
                validator: (v) => (v == null || !v.contains('@'))
                    ? 'Valid UPI ID daalo (e.g. name@bank)'
                    : null,
              )
            else ...[
              TextFormField(
                controller: _accountHolderController,
                decoration: const InputDecoration(labelText: 'Account holder name'),
                validator: (v) => (v == null || v.trim().isEmpty) ? 'Required' : null,
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _accountNumberController,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: 'Account number'),
                validator: (v) => (v == null || v.trim().isEmpty) ? 'Required' : null,
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _ifscController,
                decoration: const InputDecoration(labelText: 'IFSC code'),
                validator: (v) => (v == null || v.trim().length != 11)
                    ? 'Valid 11-char IFSC daalo'
                    : null,
              ),
            ],
            const SizedBox(height: 24),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(_error!, style: const TextStyle(color: Colors.redAccent)),
              ),
            LsPrimaryButton(
              label: _submitting ? 'Submitting...' : 'Request Withdrawal',
              onPressed: _submitting ? null : _submit,
            ),
            const SizedBox(height: 8),
            Text(
              'Withdrawal request submit hone ke baad review/approval pending rehta hai — coins tabhi deduct honge jab approve ho.',
              style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant),
            ),
          ],
        ),
      ),
    );
  }
}
