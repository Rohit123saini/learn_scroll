// lib/liveclass/screens/coupons_screen.dart
//
// Screen 18 — Coupons (see LIVECLASS_SCREEN_ARCHITECTURE.md §18). Reached
// from Classroom Detail's manage sheet, owner/admin only.
//
// API: `coupons/` CRUD, always scoped server-side to "coupons I created"
// (CouponViewSet.get_queryset filters by created_by=request.user — a
// classroom's coupon list is never visible to anyone but the teacher who
// made it), narrowed here by `?classroom=`.
//   - Create/update: at least one of discount_percent / discount_amount is
//     required, and valid_until must be after valid_from — both enforced
//     server-side, surfaced verbatim on failure.
//   - classroom is fixed to this screen's classroom on create (a coupon
//     "usable across all of my classrooms" — classroom=null — isn't
//     exposed here since this screen is opened per-classroom).

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

String _discountLabel(Coupon c) {
  if (c.discountPercent != null && c.discountPercent! > 0) return '${c.discountPercent}% off';
  if (c.discountAmount != null && c.discountAmount! > 0) return '${c.discountAmount!.toStringAsFixed(0)} coins off';
  return 'No discount set';
}

// ===========================================================================
// SCREEN
// ===========================================================================
class CouponsScreen extends StatefulWidget {
  final int classroomId;
  final String classroomTitle;
  const CouponsScreen({super.key, required this.classroomId, this.classroomTitle = ''});

  @override
  State<CouponsScreen> createState() => _CouponsScreenState();
}

class _CouponsScreenState extends State<CouponsScreen> {
  List<Coupon> _coupons = [];
  bool _loading = true;
  String? _error;

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
      final res = await LiveClassApi.coupons.list(classroomId: widget.classroomId);
      if (!mounted) return;
      setState(() {
        _coupons = res.results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load coupons.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _openEditor({Coupon? existing}) async {
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) => _CouponEditorSheet(classroomId: widget.classroomId, existing: existing),
    );
    if (saved == true) _load();
  }

  Future<void> _toggleActive(Coupon c) async {
    try {
      await LiveClassApi.coupons.update(
        c.id,
        Coupon(
          id: c.id,
          classroomId: c.classroomId,
          createdBy: c.createdBy,
          code: c.code,
          discountPercent: c.discountPercent,
          discountAmount: c.discountAmount,
          validFrom: c.validFrom,
          validUntil: c.validUntil,
          maxUses: c.maxUses,
          isActive: !c.isActive,
        ),
      );
      _snack(c.isActive ? 'Coupon deactivated.' : 'Coupon activated.');
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Update failed.');
    }
  }

  Future<void> _confirmDelete(Coupon c) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Delete Coupon?'),
        content: Text('"${c.code}" will be permanently deleted. This cannot be undone.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await LiveClassApi.coupons.delete(c.id);
      _snack('Coupon deleted.');
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Delete failed.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(widget.classroomTitle.isNotEmpty ? 'Coupons — ${widget.classroomTitle}' : 'Coupons'),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: LiveClassColors.navy,
        onPressed: () => _openEditor(),
        icon: const Icon(Icons.add),
        label: const Text('New Coupon'),
      ),
      body: _loading
          ? const LiveClassLoading()
          : _error != null
              ? LiveClassErrorState(message: _error!, onRetry: _load)
              : RefreshIndicator(
                  color: LiveClassColors.navy,
                  onRefresh: _load,
                  child: _coupons.isEmpty
                      ? const LiveClassEmptyState(
                          icon: Icons.local_offer_outlined,
                          title: 'No coupons created yet.',
                        )
                      : ListView.builder(
                          padding: const EdgeInsets.fromLTRB(16, 14, 16, 90),
                          itemCount: _coupons.length,
                          itemBuilder: (_, i) => _couponCard(_coupons[i]),
                        ),
                ),
    );
  }

  Widget _couponCard(Coupon c) {
    final expired = c.validUntil.isBefore(DateTime.now());
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.04), blurRadius: 8, offset: const Offset(0, 2))],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 38,
                height: 38,
                decoration: BoxDecoration(gradient: LiveClassColors.gradient, borderRadius: BorderRadius.circular(10)),
                child: const Icon(Icons.local_offer_rounded, color: Colors.white, size: 18),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(c.code, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14, letterSpacing: 0.5)),
                    Text(_discountLabel(c), style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                decoration: BoxDecoration(
                  color: (c.isValid ? Colors.green : Colors.grey).withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(c.isValid ? 'VALID' : (expired ? 'EXPIRED' : (c.isActive ? 'INACTIVE' : 'PAUSED')),
                    style: TextStyle(
                        fontSize: 10, fontWeight: FontWeight.bold, color: c.isValid ? Colors.green.shade700 : Colors.grey.shade700)),
              ),
            ],
          ),
          const Divider(height: 22),
          Row(
            children: [
              Expanded(child: _stat('Valid', '${liveClassFmtDate(c.validFrom)} – ${liveClassFmtDate(c.validUntil)}')),
              Expanded(child: _stat('Used', '${c.usedCount}${c.maxUses != null ? ' / ${c.maxUses}' : ''}')),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              TextButton.icon(
                onPressed: () => _openEditor(existing: c),
                icon: const Icon(Icons.edit_outlined, size: 16),
                label: const Text('Edit'),
              ),
              TextButton.icon(
                onPressed: () => _toggleActive(c),
                icon: Icon(c.isActive ? Icons.pause_circle_outline : Icons.play_circle_outline, size: 16),
                label: Text(c.isActive ? 'Deactivate' : 'Activate'),
              ),
              TextButton.icon(
                onPressed: () => _confirmDelete(c),
                style: TextButton.styleFrom(foregroundColor: Colors.red.shade600),
                icon: const Icon(Icons.delete_outline, size: 16),
                label: const Text('Delete'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _stat(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: TextStyle(fontSize: 10.5, color: Colors.grey.shade500)),
        const SizedBox(height: 2),
        Text(value, style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
      ],
    );
  }
}

// ===========================================================================
// Create/Edit bottom sheet
// ===========================================================================
class _CouponEditorSheet extends StatefulWidget {
  final int classroomId;
  final Coupon? existing;
  const _CouponEditorSheet({required this.classroomId, this.existing});

  @override
  State<_CouponEditorSheet> createState() => _CouponEditorSheetState();
}

class _CouponEditorSheetState extends State<_CouponEditorSheet> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _codeCtrl;
  late final TextEditingController _percentCtrl;
  late final TextEditingController _amountCtrl;
  late final TextEditingController _maxUsesCtrl;
  late DateTime _validFrom;
  late DateTime _validUntil;
  bool _isActive = true;
  bool _saving = false;

  bool get _isEdit => widget.existing != null;

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    _codeCtrl = TextEditingController(text: e?.code ?? '');
    _percentCtrl = TextEditingController(text: e?.discountPercent?.toString() ?? '');
    _amountCtrl = TextEditingController(text: e?.discountAmount != null ? e!.discountAmount!.toStringAsFixed(0) : '');
    _maxUsesCtrl = TextEditingController(text: e?.maxUses?.toString() ?? '');
    _validFrom = e?.validFrom ?? DateTime.now();
    _validUntil = e?.validUntil ?? DateTime.now().add(const Duration(days: 30));
    _isActive = e?.isActive ?? true;
  }

  @override
  void dispose() {
    _codeCtrl.dispose();
    _percentCtrl.dispose();
    _amountCtrl.dispose();
    _maxUsesCtrl.dispose();
    super.dispose();
  }

  Future<void> _pickDate({required bool isFrom}) async {
    final picked = await showDatePicker(
      context: context,
      initialDate: isFrom ? _validFrom : _validUntil,
      firstDate: DateTime.now().subtract(const Duration(days: 365)),
      lastDate: DateTime.now().add(const Duration(days: 365 * 3)),
    );
    if (picked == null) return;
    setState(() {
      if (isFrom) {
        _validFrom = picked;
      } else {
        _validUntil = picked;
      }
    });
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    final percent = _percentCtrl.text.trim().isEmpty ? null : int.tryParse(_percentCtrl.text.trim());
    final amount = _amountCtrl.text.trim().isEmpty ? null : double.tryParse(_amountCtrl.text.trim());
    if ((percent == null || percent <= 0) && (amount == null || amount <= 0)) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Enter at least one of discount % or discount amount.')));
      return;
    }
    if (!_validUntil.isAfter(_validFrom)) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Valid Until must be after Valid From.')));
      return;
    }
    final maxUses = _maxUsesCtrl.text.trim().isEmpty ? null : int.tryParse(_maxUsesCtrl.text.trim());

    setState(() => _saving = true);
    final draft = Coupon(
      id: widget.existing?.id ?? 0,
      classroomId: widget.classroomId,
      createdBy: widget.existing?.createdBy ?? UserMini(id: 0, username: '', fullName: ''),
      code: _codeCtrl.text.trim().toUpperCase(),
      discountPercent: percent,
      discountAmount: amount,
      validFrom: _validFrom,
      validUntil: _validUntil,
      maxUses: maxUses,
      isActive: _isActive,
    );
    try {
      if (_isEdit) {
        await LiveClassApi.coupons.update(widget.existing!.id, draft);
      } else {
        await LiveClassApi.coupons.create(draft);
      }
      if (!mounted) return;
      Navigator.pop(context, true);
    } on LiveClassApiException catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Save failed — please try again.')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(left: 20, right: 20, top: 20, bottom: MediaQuery.of(context).viewInsets.bottom + 20),
      child: Form(
        key: _formKey,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(_isEdit ? 'Edit Coupon' : 'New Coupon', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
              const SizedBox(height: 16),
              TextFormField(
                controller: _codeCtrl,
                textCapitalization: TextCapitalization.characters,
                decoration: liveClassInputDecoration('Code (e.g. WELCOME10)'),
                validator: (v) => (v == null || v.trim().isEmpty) ? 'Code is required' : null,
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: TextFormField(
                      controller: _percentCtrl,
                      keyboardType: TextInputType.number,
                      decoration: liveClassInputDecoration('Discount %'),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: TextFormField(
                      controller: _amountCtrl,
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      decoration: liveClassInputDecoration('Discount amount (coins)'),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text('At least one field is required.', style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: () => _pickDate(isFrom: true),
                      child: Text('From: ${liveClassFmtDate(_validFrom)}'),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: OutlinedButton(
                      onPressed: () => _pickDate(isFrom: false),
                      child: Text('Until: ${liveClassFmtDate(_validUntil)}'),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _maxUsesCtrl,
                keyboardType: TextInputType.number,
                decoration: liveClassInputDecoration('Max uses (optional — blank = unlimited)'),
              ),
              const SizedBox(height: 8),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                value: _isActive,
                onChanged: (v) => setState(() => _isActive = v),
                title: const Text('Active', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                activeColor: LiveClassColors.navy,
              ),
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(backgroundColor: LiveClassColors.navy, foregroundColor: Colors.white),
                  onPressed: _saving ? null : _save,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    child: _saving
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : Text(_isEdit ? 'Save Changes' : 'Create Coupon'),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}