// ============================================================
// LIVECLASS — REFERRALS SCREEN
//
// Backend surface used: GET /referrals/my-code/, POST
// /referrals/redeem/, GET /referrals/class-referral-summary/,
// GET /referrals/ (own ledger).
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';

class ReferralsScreen extends StatefulWidget {
  final LiveClassApi api;
  const ReferralsScreen({super.key, required this.api});

  @override
  State<ReferralsScreen> createState() => _ReferralsScreenState();
}

class _ReferralsScreenState extends State<ReferralsScreen> {
  Map<String, dynamic>? _myCode;
  List<dynamic> _ledger = const [];
  Map<String, dynamic>? _classSummary;
  final _redeemCtrl = TextEditingController();
  bool _loading = true;
  Object? _error;

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
        widget.api.myReferralCode(),
        widget.api.referrals(),
        widget.api.classReferralSummary(),
      ]);
      setState(() {
        _myCode = results[0] as Map<String, dynamic>;
        _ledger = results[1] as List;
        _classSummary = results[2] as Map<String, dynamic>;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _redeem() async {
    final t = AppLocalizations.of(context)!;
    final code = _redeemCtrl.text.trim();
    if (code.isEmpty) return;
    try {
      await widget.api.redeemReferral(code);
      _redeemCtrl.clear();
      if (mounted) lsSnack(context, t.referralRedeemedMessage);
      _load();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: t.referAndEarnTitle),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? ErrorStateWidget(title: t.couldNotLoadReferrals, retryLabel: t.retry, onRetry: _load)
                : ListView(padding: const EdgeInsets.all(kLsPad), children: [
                    LsCard(
                      child: Column(children: [
                        Text(t.yourReferralCodeLabel, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                        const SizedBox(height: 6),
                        Text(_myCode?['code']?.toString() ?? '-', style: LsType.head(context, size: 20)),
                        const SizedBox(height: 4),
                        Text(t.timesRedeemedLabel(_myCode?['redeemed_count'] as int? ?? 0),
                            style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                      ]),
                    ),
                    const SizedBox(height: 16),
                    Row(children: [
                      Expanded(
                        child: TextField(
                          controller: _redeemCtrl,
                          decoration: InputDecoration(
                            hintText: t.enterReferralCodeHint,
                            filled: true,
                            fillColor: cs.surfaceVariant,
                            border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      LsPrimaryButton(label: t.redeemCta, expanded: false, onPressed: _redeem),
                    ]),
                    const SizedBox(height: 20),
                    LsSectionHead(title: t.classroomReferralSummaryTitle, padding: EdgeInsets.zero),
                    if (_classSummary != null)
                      LsCard(
                        child: LsMetaRow(
                          icon: Icons.groups_rounded,
                          label: t.commissionEarnedLabel,
                          value: '${_classSummary!['commission_earned'] ?? 0}',
                        ),
                      ),
                    const SizedBox(height: 20),
                    LsSectionHead(title: t.peopleYouReferredTitle, padding: EdgeInsets.zero),
                    if (_ledger.isEmpty)
                      EmptyStateWidget(title: t.noReferralsYet, icon: Icons.person_add_alt_outlined)
                    else
                      ..._ledger.map((r) {
                        final m = r as Map<String, dynamic>;
                        return LsCard(
                          margin: const EdgeInsets.only(top: 8),
                          child: LsMetaRow(icon: Icons.person_rounded, label: m['referred_name']?.toString() ?? '', value: '+${m['bonus_amount'] ?? 0}'),
                        );
                      }),
                  ]),
      ),
    );
  }
}
