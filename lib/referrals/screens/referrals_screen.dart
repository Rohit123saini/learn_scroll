// lib/referrals/screens/referrals_screen.dart
//
// ============================================================
// REFERRALS — APP-WIDE "INVITE & EARN" SCREEN  (Task G12)
//
// Same feature `tuitionclass/screens/referrals_screen.dart` already ships
// (own code, redeem, ledger, class-referral-summary) — this is the
// module-independent copy that Profile/Settings link to, so "invite a
// friend" doesn't require reaching into the Tuition Class module. It talks
// to `ReferralsApi` (root `/referrals/...`, see referral_urls.py on the
// backend) instead of `TuitionClassApi`, and doesn't take a `TuitionClassApi`
// constructor param — it can be pushed from anywhere in the app.
//
// The tuitionclass-embedded screen is left in place on purpose (it still
// makes sense inside that module's own nav shell) — this isn't a
// deletion, it's a second, more discoverable entry point onto the same
// backend feature.
// ============================================================

import 'package:flutter/material.dart';
import 'package:share_plus/share_plus.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../api/referrals_api.dart';

class ReferralsScreen extends StatefulWidget {
  const ReferralsScreen({super.key});

  @override
  State<ReferralsScreen> createState() => _ReferralsScreenState();
}

class _ReferralsScreenState extends State<ReferralsScreen> {
  final _api = ReferralsApi.instance;

  Map<String, dynamic>? _myCode;
  List<dynamic> _ledger = const [];
  Map<String, dynamic>? _classSummary;
  Map<String, dynamic>? _earnings; // TASK 12 — commission totals + recent payouts
  final _redeemCtrl = TextEditingController();
  bool _loading = true;
  bool _redeeming = false;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _load();
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
        _api.myReferralCode(),
        _api.myReferrals(),
        _api.classReferralSummary(),
        _api.earnings(),
      ]);
      if (!mounted) return;
      setState(() {
        _myCode = results[0] as Map<String, dynamic>;
        _ledger = results[1] as List;
        _classSummary = results[2] as Map<String, dynamic>;
        _earnings = results[3] as Map<String, dynamic>;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _redeem() async {
    final t = AppLocalizations.of(context)!;
    final code = _redeemCtrl.text.trim();
    if (code.isEmpty || _redeeming) return;
    setState(() => _redeeming = true);
    try {
      await _api.redeem(code);
      _redeemCtrl.clear();
      if (mounted) lsSnack(context, t.referralRedeemedMessage);
      await _load();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    } finally {
      if (mounted) setState(() => _redeeming = false);
    }
  }

  void _shareCode() {
    final code = _myCode?['code']?.toString();
    if (code == null || code.isEmpty) return;
    final bonus = _myCode?['bonus_per_referral'];
    final bonusLine = bonus != null ? ' We both get $bonus bonus coins.' : '';
    Share.share(
      'Join me on LearnScroll! Use my referral code $code when you sign up.$bonusLine',
    );
  }

  List<Widget> _buildEarnings(AppLocalizations t, ColorScheme cs) {
    final e = _earnings!;
    final recent = (e['recent'] as List?) ?? const [];
    return [
      LsSectionHead(title: t.referralEarningsTitle, padding: EdgeInsets.zero),
      LsCard(
        child: Column(children: [
          LsMetaRow(
            icon: Icons.monetization_on_outlined,
            label: t.referralTotalCommissionLabel,
            value: '${e['total_commission_earned'] ?? 0}',
          ),
          const SizedBox(height: 8),
          LsMetaRow(
            icon: Icons.quiz_outlined,
            label: t.referralTestSeriesCommissionLabel,
            value: '${e['testseries_commission'] ?? 0}',
          ),
          const SizedBox(height: 8),
          LsMetaRow(
            icon: Icons.groups_rounded,
            label: t.referralClassCommissionLabel,
            value: '${e['classroom_commission'] ?? 0}',
          ),
          const SizedBox(height: 8),
          LsMetaRow(
            icon: Icons.link_rounded,
            label: t.referralPeopleAttributedLabel,
            value: '${e['people_attributed'] ?? 0}',
          ),
          const SizedBox(height: 8),
          LsMetaRow(
            icon: Icons.shopping_bag_outlined,
            label: t.referralPeopleConvertedLabel,
            value: '${e['people_converted'] ?? 0}',
          ),
          const SizedBox(height: 10),
          Text(
            t.referralWindowNote((e['attribution_days'] as num?)?.toInt() ?? 30),
            style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
          ),
        ]),
      ),
      const SizedBox(height: 12),
      LsSectionHead(title: t.referralRecentCommissionsTitle, padding: EdgeInsets.zero),
      if (recent.isEmpty)
        EmptyStateWidget(title: t.referralNoCommissionsYet, icon: Icons.savings_outlined)
      else
        ...recent.map((r) {
          final m = Map<String, dynamic>.from(r as Map);
          final who = m['referee'] as Map?;
          final name = who?['full_name']?.toString() ?? who?['username']?.toString() ?? '';
          final kind = m['kind'] == 'testseries' ? t.referralTestSeriesKind : t.referralClassKind;
          return LsCard(
            margin: const EdgeInsets.only(top: 8),
            child: LsMetaRow(
              icon: m['kind'] == 'testseries' ? Icons.quiz_outlined : Icons.groups_rounded,
              label: name.isEmpty ? kind : '$kind · $name',
              value: '+${m['commission_coins'] ?? 0}',
            ),
          );
        }),
    ];
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
                        Text(t.timesRedeemedLabel(_myCode?['referral_count'] as int? ?? 0),
                            style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                        const SizedBox(height: 14),
                        LsPrimaryButton(label: t.inviteCta, icon: Icons.ios_share_rounded, onPressed: _shareCode),
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
                      LsPrimaryButton(
                        label: t.redeemCta,
                        expanded: false,
                        loading: _redeeming,
                        onPressed: _redeem,
                      ),
                    ]),
                    const SizedBox(height: 20),
                    // TASK 12 / 12.5 — commission earnings across test series
                    // AND classes, plus the funnel (opened your link -> bought).
                    if (_earnings != null) ..._buildEarnings(t, cs),
                    const SizedBox(height: 20),
                    LsSectionHead(title: t.classroomReferralSummaryTitle, padding: EdgeInsets.zero),
                    // TASK 2 — totals row now shows BOTH earned and pending
                    // (pending was fetched before but silently discarded).
                    if (_classSummary != null)
                      LsCard(
                        child: Column(children: [
                          LsMetaRow(
                            icon: Icons.groups_rounded,
                            label: t.commissionEarnedLabel,
                            value: '${_classSummary!['total_commission_earned'] ?? 0}',
                          ),
                          const SizedBox(height: 8),
                          LsMetaRow(
                            icon: Icons.hourglass_top_rounded,
                            label: t.pendingCommissionLabel,
                            value: '${_classSummary!['total_commission_pending'] ?? 0}',
                          ),
                        ]),
                      ),
                    // TASK 2 — per-classroom breakdown: which classroom,
                    // at what %rate, how many referred, how much earned.
                    // `by_classroom` used to be fetched and thrown away —
                    // this is the only place that %commission (the thing
                    // the person actually cares about — "kitne % milta
                    // hai is classroom se") is shown on this screen.
                    if (_classSummary != null &&
                        (_classSummary!['by_classroom'] as List?)?.isNotEmpty == true)
                      ...List<Map<String, dynamic>>.from(_classSummary!['by_classroom'] as List).map((row) {
                        final percent = num.tryParse('${row['commission_percent'] ?? 0}') ?? 0;
                        return LsCard(
                          margin: const EdgeInsets.only(top: 8),
                          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Text(
                              row['classroom_title']?.toString() ?? '',
                              style: LsType.head(context, size: 14),
                            ),
                            const SizedBox(height: 6),
                            LsMetaRow(
                              icon: Icons.percent_rounded,
                              label: t.commissionRateLabel,
                              value: '${percent.toStringAsFixed(0)}%',
                            ),
                            const SizedBox(height: 4),
                            LsMetaRow(
                              icon: Icons.person_add_alt_1_rounded,
                              label: t.studentsReferredLabel,
                              value: '${row['referred_count'] ?? 0}',
                            ),
                            const SizedBox(height: 4),
                            LsMetaRow(
                              icon: Icons.monetization_on_outlined,
                              label: t.commissionEarnedLabel,
                              value: '${row['commission_earned'] ?? 0}',
                            ),
                          ]),
                        );
                      }),
                    const SizedBox(height: 20),
                    LsSectionHead(title: t.peopleYouReferredTitle, padding: EdgeInsets.zero),
                    if (_ledger.isEmpty)
                      EmptyStateWidget(title: t.noReferralsYet, icon: Icons.person_add_alt_outlined)
                    else
                      ..._ledger.map((r) {
                        final m = r as Map<String, dynamic>;
                        final referred = m['referred'] as Map?;
                        final name = referred?['full_name']?.toString() ?? referred?['username']?.toString() ?? '';
                        return LsCard(
                          margin: const EdgeInsets.only(top: 8),
                          child: LsMetaRow(icon: Icons.person_rounded, label: name, value: '+${m['bonus_amount'] ?? 0}'),
                        );
                      }),
                  ]),
      ),
    );
  }
}
