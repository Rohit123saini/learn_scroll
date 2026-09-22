// ============================================================
// LIVECLASS — JOIN REQUEST SHEET
//
// Backend surface used: POST /join-requests/ (body: class_pass,
// coupon_code?, message?). Coins are only ever debited once the
// teacher accepts (see ClassJoinRequest docstring in models.py) — so
// this sheet never touches the wallet directly, it just files the
// request.
// ============================================================

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../api/liveclass_api.dart';
import '../models/liveclass_models.dart';

class JoinRequestSheet extends StatefulWidget {
  final LiveClassApi api;
  final List<ClassPass> passes;
  const JoinRequestSheet({super.key, required this.api, required this.passes});

  @override
  State<JoinRequestSheet> createState() => _JoinRequestSheetState();
}

class _JoinRequestSheetState extends State<JoinRequestSheet> {
  int? _selectedPassId;
  final _couponCtrl = TextEditingController();
  final _messageCtrl = TextEditingController();
  bool _submitting = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _selectedPassId = widget.passes.isNotEmpty ? widget.passes.first.id : null;
  }

  Future<void> _submit() async {
    if (_selectedPassId == null) return;
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await widget.api.createJoinRequest(
        _selectedPassId!,
        couponCode: _couponCtrl.text.trim().isEmpty ? null : _couponCtrl.text.trim(),
        message: _messageCtrl.text.trim().isEmpty ? null : _messageCtrl.text.trim(),
      );
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(kLsPad),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(t.choosePassTitle, style: LsType.head(context, size: 16)),
            const SizedBox(height: 12),
            ...widget.passes.map((p) => RadioListTile<int>(
                  value: p.id,
                  groupValue: _selectedPassId,
                  onChanged: (v) => setState(() => _selectedPassId = v),
                  contentPadding: EdgeInsets.zero,
                  title: Text(p.title.isEmpty ? p.passType : p.title, style: TextStyle(fontSize: 13.5, color: cs.onSurface)),
                  subtitle: Text(
                    t.passSubtitle(p.price.toStringAsFixed(0), p.validityDays),
                    style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
                  ),
                )),
            const SizedBox(height: 8),
            TextField(
              controller: _couponCtrl,
              decoration: InputDecoration(
                labelText: t.couponCodeOptional,
                filled: true,
                fillColor: cs.surfaceVariant,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
              ),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _messageCtrl,
              maxLines: 2,
              decoration: InputDecoration(
                labelText: t.messageToTeacherOptional,
                filled: true,
                fillColor: cs.surfaceVariant,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: 8),
              Text(_error!, style: TextStyle(fontSize: 12, color: cs.error)),
            ],
            const SizedBox(height: 16),
            LsPrimaryButton(
              label: t.sendRequestCta,
              loading: _submitting,
              onPressed: _selectedPassId == null ? null : _submit,
            ),
          ]),
        ),
      ),
    );
  }
}
