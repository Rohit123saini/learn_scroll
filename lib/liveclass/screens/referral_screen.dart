// lib/liveclass/screens/referral_screen.dart
//
// Referral Program — new screen. The backend (ReferralViewSet in
// views.py: referrals/, referrals/my-code/, referrals/redeem/) was fully
// implemented — own referral code, redemption tally, redeeming someone
// else's code — but no screen anywhere in the module ever called any of
// these three endpoints.
//
// This screen is account-wide, not classroom-scoped, so unlike the other
// new screens in this pass it isn't reached from Classroom Detail's manage
// sheet. Wire it in from wherever this app's main menu / profile / wallet
// section lives — that file wasn't part of this upload, so it isn't
// touched here.
//
// API: GET referrals/my-code/, GET referrals/, POST referrals/redeem/.
//
// On the shared LiveClass design system (liveclass_theme.dart).

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

class ReferralScreen extends StatefulWidget {
  const ReferralScreen({super.key});

  @override
  State<ReferralScreen> createState() => _ReferralScreenState();
}

class _ReferralScreenState extends State<ReferralScreen> {
  MyReferralCode? _myCode;
  List<Referral> _referrals = [];
  bool _loading = true;
  String? _error;

  // FEATURE (Phase 2, item 9 — classroom referral earnings): distinct
  // from the sign-up code system above (referrals/my-code — referring
  // PEOPLE to the app). This is PassPurchaseApi.referralEarnings() — the
  // caller's own commission ledger from referring specific CLASSROOMS via
  // ClassroomApi.referLink (see classroom_detail_screen.dart's "Refer &
  // Earn" entry point). Backend was ready with no frontend caller
  // anywhere in the module. Fetched separately and best-effort — a
  // failure here shouldn't block the sign-up referral section above,
  // which is this screen's original purpose.
  ReferralEarnings? _earnings;
  bool _earningsLoading = true;

  final _redeemCtrl = TextEditingController();
  bool _redeeming = false;

  @override
  void initState() {
    super.initState();
    _load();
    _loadEarnings();
  }

  @override
  void dispose() {
    _redeemCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        LiveClassApi.referrals.myCode(),
        LiveClassApi.referrals.list(),
      ]);
      if (!mounted) return;
      setState(() {
        _myCode = results[0] as MyReferralCode;
        _referrals = (results[1] as PaginatedList<Referral>).results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load referral info.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _loadEarnings() async {
    try {
      final earnings = await LiveClassApi.passPurchases.referralEarnings();
      if (!mounted) return;
      setState(() {
        _earnings = earnings;
        _earningsLoading = false;
      });
    } catch (_) {
      // Best-effort — never block the sign-up referral section on this.
      if (!mounted) return;
      setState(() => _earningsLoading = false);
    }
  }

  Future<void> _copyCode() async {
    if (_myCode == null) return;
    await Clipboard.setData(ClipboardData(text: _myCode!.code));
    _snack('Code copied.');
  }

  Future<void> _redeem() async {
    final code = _redeemCtrl.text.trim();
    if (code.isEmpty) {
      _snack('Enter a referral code.');
      return;
    }
    setState(() => _redeeming = true);
    try {
      await LiveClassApi.referrals.redeem(code);
      if (!mounted) return;
      _redeemCtrl.clear();
      _snack('Referral code redeemed!');
      setState(() => _redeeming = false);
      _load();
    } on LiveClassApiException catch (e) {
      setState(() => _redeeming = false);
      _snack(e.message);
    } catch (_) {
      setState(() => _redeeming = false);
      _snack('Could not redeem — please try again.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar('Refer & Earn'),
      body: RefreshIndicator(
        color: LiveClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const LiveClassLoading()
            : _error != null
                ? LiveClassErrorState(message: _error!, onRetry: _load)
                : _buildBody(),
      ),
    );
  }

  Widget _buildBody() {
    final code = _myCode!;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
      children: [
        LiveClassCard(
          margin: EdgeInsets.zero,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Share your code — you and your friend both get ${code.bonusPerReferral} coins when they sign up.',
                style: TextStyle(fontSize: 12.5, color: Colors.grey.shade700),
              ),
              const SizedBox(height: 14),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                decoration: BoxDecoration(
                  gradient: LiveClassColors.gradient,
                  borderRadius: BorderRadius.circular(LiveClassRadius.chip),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        code.code,
                        style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 20, letterSpacing: 1.5),
                      ),
                    ),
                    IconButton(
                      onPressed: _copyCode,
                      icon: const Icon(Icons.copy_rounded, color: Colors.white),
                      tooltip: 'Copy code',
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),
              Row(
                children: [
                  Expanded(child: _statTile('${code.referralCount}', 'Referred')),
                  const SizedBox(width: 10),
                  Expanded(child: _statTile('${code.totalBonusEarned}', 'Coins Earned')),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 20),
        const Text('Have a code?', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14.5, color: LiveClassColors.navy)),
        const SizedBox(height: 4),
        Text(
          'Redeeming only works within a short window after you first sign up.',
          style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600),
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _redeemCtrl,
                textCapitalization: TextCapitalization.characters,
                decoration: liveClassInputDecoration('Enter referral code'),
              ),
            ),
            const SizedBox(width: 10),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor: LiveClassColors.navy,
                foregroundColor: Colors.white,
                padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 15),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
              ),
              onPressed: _redeeming ? null : _redeem,
              child: _redeeming
                  ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Text('Redeem'),
            ),
          ],
        ),
        const SizedBox(height: 20),
        const Text('People You Referred', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14.5, color: LiveClassColors.navy)),
        const SizedBox(height: 10),
        _referrals.isEmpty
            ? Text("You haven't referred anyone yet.", style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600))
            : Column(children: _referrals.map(_referralTile).toList()),
        if (!_earningsLoading && _earnings != null) ...[
          const SizedBox(height: 24),
          const Text('Classroom Referral Earnings',
              style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14.5, color: LiveClassColors.navy)),
          const SizedBox(height: 4),
          Text(
            'Commission from classrooms you\'ve referred (via each classroom\'s own "Refer & Earn" link).',
            style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600),
          ),
          const SizedBox(height: 10),
          LiveClassCard(
            margin: EdgeInsets.zero,
            child: Row(
              children: [
                const Icon(Icons.savings_outlined, color: LiveClassColors.success, size: 22),
                const SizedBox(width: 10),
                Text('${_earnings!.totalEarned}', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 18, color: LiveClassColors.navy)),
                const SizedBox(width: 6),
                Text('coins earned total', style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600)),
              ],
            ),
          ),
          const SizedBox(height: 10),
          _earnings!.purchases.results.isEmpty
              ? Text('No classroom referrals yet.', style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600))
              : Column(children: _earnings!.purchases.results.map(_earningTile).toList()),
        ],
      ],
    );
  }

  Widget _earningTile(PassPurchase p) {
    return LiveClassCard(
      padding: const EdgeInsets.all(12),
      margin: const EdgeInsets.only(bottom: 10),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(p.classroomTitle.isNotEmpty ? p.classroomTitle : 'Classroom',
                    style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
                const SizedBox(height: 2),
                Text(
                  '${p.student.fullName.isNotEmpty ? p.student.fullName : p.student.username} · ${liveClassFmtDate(p.purchasedAt)}',
                  style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600),
                ),
              ],
            ),
          ),
          Text('+${p.referralCoinsReleased}', style: const TextStyle(fontWeight: FontWeight.bold, color: LiveClassColors.success)),
        ],
      ),
    );
  }

  Widget _statTile(String value, String label) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 10),
      decoration: BoxDecoration(color: LiveClassColors.bg, borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
      child: Column(
        children: [
          Text(value, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: LiveClassColors.navy)),
          Text(label, style: TextStyle(fontSize: 11, color: Colors.grey.shade600)),
        ],
      ),
    );
  }

  Widget _referralTile(Referral r) {
    final name = r.referred.fullName.isNotEmpty ? r.referred.fullName : r.referred.username;
    return LiveClassCard(
      padding: const EdgeInsets.all(12),
      margin: const EdgeInsets.only(bottom: 10),
      child: Row(
        children: [
          Container(
            width: 36,
            height: 36,
            decoration: const BoxDecoration(gradient: LiveClassColors.gradient, shape: BoxShape.circle),
            alignment: Alignment.center,
            child: Text(name.substring(0, 1).toUpperCase(), style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
          ),
          const SizedBox(width: 12),
          Expanded(child: Text(name, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5))),
          Text('+${r.bonusAmount}', style: const TextStyle(fontWeight: FontWeight.bold, color: LiveClassColors.success)),
        ],
      ),
    );
  }
}