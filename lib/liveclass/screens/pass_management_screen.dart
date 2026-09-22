// ============================================================
// LIVECLASS — PASS MANAGEMENT SCREEN (teacher side)
//
// Backend surface used: GET/POST /passes/?classroom=, PATCH /passes/{id}/,
// GET/POST/PATCH/DELETE /coupons/, GET /pass-gifts/.
//
// NOTE on pass edit limits (mirrors backend rules exactly so the UI
// never offers an action the API will 400 on): once a pass has ever
// been purchased, price can't be raised and validity_days/max_classes/
// pass_type can't be reduced/changed while an active paid purchase is
// outstanding — see ClassPassViewSet in views.py. This screen keeps
// editing to the one safe, always-allowed toggle (`is_active`) rather
// than exposing a full edit form that could 400 mid-save.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';
import '../models/liveclass_models.dart';

class PassManagementScreen extends StatefulWidget {
  final LiveClassApi api;
  final int classroomId;
  const PassManagementScreen({super.key, required this.api, required this.classroomId});

  @override
  State<PassManagementScreen> createState() => _PassManagementScreenState();
}

class _PassManagementScreenState extends State<PassManagementScreen> with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 3, vsync: this);

  List<ClassPass> _passes = const [];
  List<Coupon> _coupons = const [];
  List<dynamic> _gifts = const [];
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
        widget.api.passes(widget.classroomId),
        widget.api.coupons(classroomId: widget.classroomId),
        widget.api.passGifts(),
      ]);
      setState(() {
        _passes = (results[0] as List).map((e) => ClassPass.fromJson(e as Map<String, dynamic>)).toList();
        _coupons = (results[1] as List).map((e) => Coupon.fromJson(e as Map<String, dynamic>)).toList();
        _gifts = results[2] as List;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  // ---------------- Passes ----------------

  Future<void> _createPassDialog() async {
    final t = AppLocalizations.of(context)!;
    final titleCtrl = TextEditingController();
    final priceCtrl = TextEditingController(text: '0');
    final daysCtrl = TextEditingController(text: '30');
    String passType = 'monthly';
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(builder: (context, setStateDialog) => AlertDialog(
            title: Text(t.newPassTitle),
            content: Column(mainAxisSize: MainAxisSize.min, children: [
              TextField(controller: titleCtrl, decoration: InputDecoration(labelText: t.passTitleLabel)),
              DropdownButton<String>(
                value: passType,
                items: const ['free', 'daily', 'weekly', 'monthly', 'yearly']
                    .map((v) => DropdownMenuItem(value: v, child: Text(v)))
                    .toList(),
                onChanged: (v) => setStateDialog(() => passType = v ?? passType),
              ),
              TextField(controller: priceCtrl, keyboardType: TextInputType.number, decoration: InputDecoration(labelText: t.priceInCoinsLabel)),
              TextField(controller: daysCtrl, keyboardType: TextInputType.number, decoration: InputDecoration(labelText: t.validityDaysLabel)),
            ]),
            actions: [
              TextButton(onPressed: () => Navigator.pop(context, false), child: Text(t.cancelCta)),
              TextButton(onPressed: () => Navigator.pop(context, true), child: Text(t.createCta)),
            ],
          )),
    );
    if (ok != true) return;
    try {
      await widget.api.createPass({
        'classroom': widget.classroomId,
        'pass_type': passType,
        'title': titleCtrl.text.trim(),
        'price': int.tryParse(priceCtrl.text) ?? 0,
        'validity_days': int.tryParse(daysCtrl.text) ?? 30,
      });
      _load();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    }
  }

  Future<void> _togglePassActive(ClassPass p) async {
    try {
      await widget.api.updatePass(p.id, {'is_active': !p.isActive});
      _load();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    }
  }

  // ---------------- Coupons ----------------

  Future<void> _createCouponDialog() async {
    final t = AppLocalizations.of(context)!;
    final codeCtrl = TextEditingController();
    final percentCtrl = TextEditingController();
    final daysValidCtrl = TextEditingController(text: '30');
    final maxUsesCtrl = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(t.newCouponTitle),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(controller: codeCtrl, textCapitalization: TextCapitalization.characters, decoration: InputDecoration(labelText: t.couponCodeLabel)),
          TextField(controller: percentCtrl, keyboardType: TextInputType.number, decoration: InputDecoration(labelText: t.discountPercentLabel)),
          TextField(controller: daysValidCtrl, keyboardType: TextInputType.number, decoration: InputDecoration(labelText: t.validForDaysLabel)),
          TextField(controller: maxUsesCtrl, keyboardType: TextInputType.number, decoration: InputDecoration(labelText: t.maxUsesOptionalLabel)),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: Text(t.cancelCta)),
          TextButton(onPressed: () => Navigator.pop(context, true), child: Text(t.createCta)),
        ],
      ),
    );
    if (ok != true || codeCtrl.text.trim().isEmpty) return;
    try {
      final validDays = int.tryParse(daysValidCtrl.text) ?? 30;
      await widget.api.createCoupon({
        'classroom': widget.classroomId,
        'code': codeCtrl.text.trim().toUpperCase(),
        if (percentCtrl.text.trim().isNotEmpty) 'discount_percent': int.tryParse(percentCtrl.text),
        'valid_until': DateTime.now().add(Duration(days: validDays)).toUtc().toIso8601String(),
        if (maxUsesCtrl.text.trim().isNotEmpty) 'max_uses': int.tryParse(maxUsesCtrl.text),
      });
      _load();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    }
  }

  Future<void> _toggleCouponActive(Coupon c) async {
    try {
      await widget.api.updateCoupon(c.id, {'is_active': !c.isActive});
      _load();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    }
  }

  Future<void> _deleteCoupon(Coupon c) async {
    try {
      await widget.api.deleteCoupon(c.id);
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
      appBar: lsAppBar(context, title: t.managePassesTitle),
      floatingActionButton: _tabs.index == 0
          ? FloatingActionButton(onPressed: _createPassDialog, child: const Icon(Icons.add_rounded))
          : _tabs.index == 1
              ? FloatingActionButton(onPressed: _createCouponDialog, child: const Icon(Icons.add_rounded))
              : null,
      body: Column(children: [
        TabBar(
          controller: _tabs,
          onTap: (_) => setState(() {}),
          labelColor: cs.primary,
          unselectedLabelColor: cs.onSurfaceVariant,
          tabs: [Tab(text: t.passesTitle), Tab(text: t.couponsTab), Tab(text: t.giftsTitle)],
        ),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _load,
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                    ? ErrorStateWidget(title: t.couldNotLoadPasses, retryLabel: t.retry, onRetry: _load)
                    : TabBarView(controller: _tabs, children: [
                        _passes.isEmpty
                            ? EmptyStateWidget(title: t.noPassesYet, icon: Icons.confirmation_number_outlined)
                            : ListView(children: _passes.map((p) => LsCard(
                                  margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                                  child: Row(children: [
                                    Expanded(
                                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                        Text(p.title.isEmpty ? p.passType : p.title, style: LsType.head(context, size: 13.5)),
                                        const SizedBox(height: 3),
                                        Text('${p.price.toStringAsFixed(0)} ${t.coinsUnit} · ${p.validityDays}${t.daysAbbrev}',
                                            style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
                                      ]),
                                    ),
                                    Switch(value: p.isActive, onChanged: (_) => _togglePassActive(p)),
                                  ]),
                                )).toList()),
                        _coupons.isEmpty
                            ? EmptyStateWidget(title: t.noCouponsYet, icon: Icons.local_offer_outlined)
                            : ListView(children: _coupons.map((c) => LsCard(
                                  margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                                  child: Row(children: [
                                    Expanded(
                                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                        Text(c.code, style: LsType.head(context, size: 13.5)),
                                        const SizedBox(height: 3),
                                        Text(
                                          c.discountPercent != null
                                              ? t.discountPercentValueLabel(c.discountPercent!)
                                              : t.discountAmountValueLabel((c.discountAmount ?? 0).toStringAsFixed(0)),
                                          style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
                                        ),
                                        Text(t.couponUsageLabel(c.usedCount, c.maxUses?.toString() ?? '∞'),
                                            style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
                                      ]),
                                    ),
                                    Switch(value: c.isActive, onChanged: (_) => _toggleCouponActive(c)),
                                    IconButton(icon: Icon(Icons.delete_outline_rounded, color: cs.error, size: 20), onPressed: () => _deleteCoupon(c)),
                                  ]),
                                )).toList()),
                        _gifts.isEmpty
                            ? EmptyStateWidget(title: t.noGiftsYet, icon: Icons.card_giftcard_outlined)
                            : ListView(children: _gifts.map((g) {
                                final m = g as Map<String, dynamic>;
                                return LsCard(
                                  margin: const EdgeInsets.fromLTRB(kLsPad, 10, kLsPad, 0),
                                  child: LsMetaRow(icon: Icons.card_giftcard_rounded, label: m['status']?.toString() ?? '', value: m['gift_message']?.toString() ?? ''),
                                );
                              }).toList()),
                      ]),
          ),
        ),
      ]),
    );
  }
}
