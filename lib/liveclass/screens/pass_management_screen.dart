// lib/liveclass/screens/pass_management_screen.dart
//
// Screen 7 (teacher side) — Passes management (see
// LIVECLASS_SCREEN_ARCHITECTURE.md §7). Reached from Classroom Detail's
// manage sheet, owner/admin only.
//
// API: `passes/` CRUD, scoped by `?classroom=`.
//   - Create: pass_type, title, price (coins), validity_days, optional
//     max_classes cap.
//   - Delete: refused server-side once the pass has ever been purchased —
//     PATCH is_active=false (pause) instead. The pause action is always
//     offered; delete is only offered while never-purchased is plausible,
//     and any 400 from the backend is surfaced verbatim either way.
//   - Update: server also refuses a PATCH that would retroactively shrink
//     what an active paid holder already bought (price up,
//     validity_days/max_classes/pass_type down) while purchases are
//     active — again surfaced verbatim from the API error.

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

// FIX (design-system drift — production readiness audit): same gap as
// explore_screen.dart — a fully hand-rolled hex-literal palette with no tie
// back to liveclass_theme.dart, instead of aliasing the shared tokens like
// every other manage-sheet screen in the module already does (see
// classroom_purchases_screen.dart's matching fix). Aliased instead — zero
// visual change, single source of truth going forward.
const _kNavy = LiveClassColors.navy;
const _kBg = LiveClassColors.bg;
const _kGradient = LiveClassColors.gradient;

String _passTypeLabel(String type) {
  switch (type) {
    case PassType.free:
      return 'Free';
    case PassType.daily:
      return 'Daily';
    case PassType.weekly:
      return 'Weekly';
    case PassType.monthly:
      return 'Monthly';
    case PassType.yearly:
      return 'Yearly';
    default:
      return type;
  }
}

String _coins(num n) => n == n.roundToDouble() ? n.toStringAsFixed(0) : n.toStringAsFixed(2);

InputDecoration _inputDecoration(String hint) => InputDecoration(
      hintText: hint,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      isDense: true,
    );

// ===========================================================================
// SCREEN
// ===========================================================================
class PassManagementScreen extends StatefulWidget {
  final int classroomId;
  final String classroomTitle;
  /// True right after a fresh classroom is created — auto-opens the "New
  /// Pass" sheet on first frame so the teacher isn't left with a classroom
  /// that has no pricing plan and is therefore unjoinable by any student.
  final bool autoOpenCreate;
  const PassManagementScreen({
    super.key,
    required this.classroomId,
    this.classroomTitle = '',
    this.autoOpenCreate = false,
  });

  @override
  State<PassManagementScreen> createState() => _PassManagementScreenState();
}

class _PassManagementScreenState extends State<PassManagementScreen> {
  List<ClassPass> _passes = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
    if (widget.autoOpenCreate) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _openEditor());
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await LiveClassApi.passes.list(classroomId: widget.classroomId);
      if (!mounted) return;
      setState(() {
        _passes = res.results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load passes.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _openEditor({ClassPass? existing}) async {
    final wasEmpty = _passes.isEmpty;
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) => _PassEditorSheet(classroomId: widget.classroomId, existing: existing),
    );
    if (saved == true) {
      await _load();
      // NOTE (UX): nudge on the very first pass only — most classrooms want
      // more than one tier (e.g. a cheap "1 class trial" alongside a
      // monthly), and nothing in the UI hinted that was possible/expected.
      if (wasEmpty && existing == null && mounted) {
        _snack('Pass created. You can add more tiers like daily/weekly/monthly if you like.');
      }
    }
  }

  Future<void> _togglePause(ClassPass pass) async {
    try {
      await LiveClassApi.passes.update(
        pass.id,
        ClassPass(
          id: pass.id,
          classroomId: pass.classroomId,
          passType: pass.passType,
          title: pass.title,
          price: pass.price,
          validityDays: pass.validityDays,
          maxClasses: pass.maxClasses,
          isActive: !pass.isActive,
          // FIX (introduced alongside allowGifting itself): without this,
          // toggling pause/resume here would silently reset a pass's
          // gifting flag back to the default every time, since this
          // rebuilds a whole new ClassPass rather than patching one field.
          allowGifting: pass.allowGifting,
        ),
      );
      _snack(pass.isActive ? 'Pass paused.' : 'Pass activated.');
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not update.');
    }
  }

  Future<void> _confirmDelete(ClassPass pass) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Delete Pass?'),
        content: const Text(
            'If this pass has ever been purchased, the backend will refuse the delete — pause it instead in that case.'),
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
      await LiveClassApi.passes.delete(pass.id);
      _snack('Pass deleted.');
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not delete.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      appBar: AppBar(
        backgroundColor: Colors.white,
        foregroundColor: _kNavy,
        elevation: 0.5,
        title: Text(widget.classroomTitle.isNotEmpty ? 'Passes — ${widget.classroomTitle}' : 'Passes',
            style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15), maxLines: 1, overflow: TextOverflow.ellipsis),
      ),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: _kNavy,
        onPressed: () => _openEditor(),
        icon: const Icon(Icons.add),
        label: const Text('New Pass'),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: _kNavy))
          : _error != null
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                      Text(_error!, textAlign: TextAlign.center),
                      const SizedBox(height: 12),
                      ElevatedButton(onPressed: _load, child: const Text('Retry')),
                    ]),
                  ),
                )
              : RefreshIndicator(
                  color: _kNavy,
                  onRefresh: _load,
                  child: _passes.isEmpty
                      ? ListView(
                          children: [
                            const Padding(
                              padding: EdgeInsets.only(top: 100),
                              child: Center(
                                child: Padding(
                                  padding: EdgeInsets.symmetric(horizontal: 32),
                                  child: Text(
                                    'No pass created yet.\nUntil at least one pass is active, no student can join this classroom.',
                                    textAlign: TextAlign.center,
                                    style: TextStyle(color: Colors.black45),
                                  ),
                                ),
                              ),
                            ),
                          ],
                        )
                      : ListView.builder(
                          padding: const EdgeInsets.fromLTRB(16, 14, 16, 90),
                          itemCount: _passes.length,
                          itemBuilder: (_, i) => _passCard(_passes[i]),
                        ),
                ),
    );
  }

  Widget _passCard(ClassPass p) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.04), blurRadius: 8, offset: const Offset(0, 2))],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 38,
                height: 38,
                decoration: BoxDecoration(gradient: _kGradient, borderRadius: BorderRadius.circular(10)),
                child: const Icon(Icons.confirmation_number_rounded, color: Colors.white, size: 18),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(p.title.isNotEmpty ? p.title : _passTypeLabel(p.passType),
                        style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
                    Text(_passTypeLabel(p.passType), style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                decoration: BoxDecoration(
                  color: (p.isActive ? Colors.green : Colors.grey).withOpacity(0.12),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(p.isActive ? 'ACTIVE' : 'PAUSED',
                    style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: p.isActive ? Colors.green.shade700 : Colors.grey.shade700)),
              ),
            ],
          ),
          const Divider(height: 22),
          Row(
            children: [
              Expanded(child: _stat('Price', '${_coins(p.price)} coins')),
              Expanded(child: _stat('Validity', '${p.validityDays} din')),
              Expanded(child: _stat('Max Classes', p.maxClasses?.toString() ?? 'Unlimited')),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              TextButton.icon(
                onPressed: () => _openEditor(existing: p),
                icon: const Icon(Icons.edit_outlined, size: 16),
                label: const Text('Edit'),
              ),
              TextButton.icon(
                onPressed: () => _togglePause(p),
                icon: Icon(p.isActive ? Icons.pause_circle_outline : Icons.play_circle_outline, size: 16),
                label: Text(p.isActive ? 'Pause' : 'Activate'),
              ),
              TextButton.icon(
                onPressed: () => _confirmDelete(p),
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
class _PassEditorSheet extends StatefulWidget {
  final int classroomId;
  final ClassPass? existing;
  const _PassEditorSheet({required this.classroomId, this.existing});

  @override
  State<_PassEditorSheet> createState() => _PassEditorSheetState();
}

class _PassEditorSheetState extends State<_PassEditorSheet> {
  final _formKey = GlobalKey<FormState>();
  late String _passType;
  late final TextEditingController _titleCtrl;
  late final TextEditingController _priceCtrl;
  late final TextEditingController _validityCtrl;
  late final TextEditingController _maxClassesCtrl;
  bool _isActive = true;
  // NEW (Pass 14 frontend catch-up §1.3) — per-pass "allow gifting" gate.
  // See liveclass_models.dart's ClassPass.allowGifting note on the
  // unconfirmed default.
  bool _allowGifting = true;
  bool _saving = false;

  bool get _isEdit => widget.existing != null;

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    _passType = e?.passType ?? PassType.monthly;
    _titleCtrl = TextEditingController(text: e?.title ?? '');
    _priceCtrl = TextEditingController(text: e != null ? _coins(e.price) : '');
    _validityCtrl = TextEditingController(text: e?.validityDays.toString() ?? '30');
    _maxClassesCtrl = TextEditingController(text: e?.maxClasses?.toString() ?? '');
    _isActive = e?.isActive ?? true;
    _allowGifting = e?.allowGifting ?? true;
  }

  @override
  void dispose() {
    _titleCtrl.dispose();
    _priceCtrl.dispose();
    _validityCtrl.dispose();
    _maxClassesCtrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    final price = double.tryParse(_priceCtrl.text.trim()) ?? 0;
    final validity = int.tryParse(_validityCtrl.text.trim()) ?? 0;
    final maxClasses = _maxClassesCtrl.text.trim().isEmpty ? null : int.tryParse(_maxClassesCtrl.text.trim());

    setState(() => _saving = true);
    final draft = ClassPass(
      id: widget.existing?.id ?? 0,
      classroomId: widget.classroomId,
      passType: _passType,
      title: _titleCtrl.text.trim(),
      price: price,
      validityDays: validity,
      maxClasses: maxClasses,
      isActive: _isActive,
      allowGifting: _allowGifting,
    );
    try {
      if (_isEdit) {
        await LiveClassApi.passes.update(widget.existing!.id, draft);
      } else {
        await LiveClassApi.passes.create(draft);
      }
      if (!mounted) return;
      Navigator.pop(context, true);
    } on LiveClassApiException catch (e) {
      // Surfaces the "would shrink what active holders paid for" 400 verbatim.
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Could not save — please try again.')));
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
              Text(_isEdit ? 'Edit Pass' : 'New Pass', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
              const SizedBox(height: 16),
              const Text('Pass Type', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
              const SizedBox(height: 6),
              DropdownButtonFormField<String>(
                value: _passType,
                decoration: _inputDecoration(''),
                items: const {
                  PassType.free: 'Free',
                  PassType.daily: 'Daily',
                  PassType.weekly: 'Weekly',
                  PassType.monthly: 'Monthly',
                  PassType.yearly: 'Yearly',
                }.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
                onChanged: (v) => setState(() => _passType = v!),
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _titleCtrl,
                decoration: _inputDecoration('Title (e.g. "1 Month Access")'),
                validator: (v) => (v == null || v.trim().isEmpty) ? 'Title is required' : null,
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: TextFormField(
                      controller: _priceCtrl,
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      decoration: _inputDecoration('Price (coins)'),
                      validator: (v) {
                        final n = double.tryParse((v ?? '').trim());
                        if (n == null || n < 0) return 'Enter a valid price';
                        return null;
                      },
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: TextFormField(
                      controller: _validityCtrl,
                      keyboardType: TextInputType.number,
                      decoration: _inputDecoration('Validity (days)'),
                      validator: (v) {
                        final n = int.tryParse((v ?? '').trim());
                        if (n == null || n <= 0) return 'Enter valid days';
                        return null;
                      },
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _maxClassesCtrl,
                keyboardType: TextInputType.number,
                decoration: _inputDecoration('Max classes (optional — blank = unlimited)'),
              ),
              const SizedBox(height: 8),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                value: _isActive,
                onChanged: (v) => setState(() => _isActive = v),
                title: const Text('Active', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                activeColor: _kNavy,
              ),
              // NEW (Pass 14 frontend catch-up §1.3) — "allow gifting"
              // toggle per ClassPass, see liveclass_models.dart's
              // ClassPass.allowGifting note.
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                value: _allowGifting,
                onChanged: (v) => setState(() => _allowGifting = v),
                title: const Text('Allow gifting', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                subtitle: Text('Students can send this pass as a gift to someone else', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
                activeColor: _kNavy,
              ),
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                  onPressed: _saving ? null : _save,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    child: _saving
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : Text(_isEdit ? 'Save Changes' : 'Create Pass'),
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