// lib/liveclass/screens/request_join_screen.dart
//
// "Request to Join" — the student-facing form that actually creates a
// ClassJoinRequest (`POST join-requests/`). Fixes a genuine missing
// screen: classroom_detail_screen.dart's `_openRequestJoin()` has always
// pushed `RequestJoinScreen(classroomId: ..., classroom: ...)`, but no
// file in the module ever defined that class — this file used to be a
// stale duplicate of join_requests_screen.dart's `JoinRequestsScreen`
// (same class name, same two named constructors, nothing importing it)
// left over from a fixed ambiguous-import compile error. Replaced here
// with the actual missing screen instead of deleting the file outright,
// since the filename already matched what classroom_detail_screen.dart
// expects to import.
//
// Used for BOTH entry points that route through _openRequestJoin():
//   - accessLevel == 'none'  -> fresh "Request to Join"
//   - accessLevel == 'expired' -> "Renew Pass" (same flow — renewing is
//     just a new join request against a pass, same as joining fresh)
//
// Flow: pick one active ClassPass -> optional coupon code (validated
// live via `coupons/validate/` before submit, never blindly trusted) ->
// optional message to the teacher -> POST join-requests/. Does NOT charge
// coins or grant access itself — that only happens when the teacher (or
// co-teacher/moderator) accepts the request from JoinRequestsScreen.inbox().
//
// API: `ClassPassApi.list(classroomId:)` (passes to choose from) ·
// `CouponApi.validate(code)` (optional, before submit) ·
// `JoinRequestApi.request({classroomId, classPassId, couponCode, message})`.
//
// On the shared LiveClass design system (liveclass_theme.dart).

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

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

// ===========================================================================
// SCREEN
// ===========================================================================
class RequestJoinScreen extends StatefulWidget {
  final int classroomId;
  final Classroom? classroom;
  const RequestJoinScreen({super.key, required this.classroomId, this.classroom});

  @override
  State<RequestJoinScreen> createState() => _RequestJoinScreenState();
}

class _RequestJoinScreenState extends State<RequestJoinScreen> {
  List<ClassPass> _passes = [];
  bool _loading = true;
  String? _error;

  int? _selectedPassId;
  Coupon? _appliedCoupon;
  bool _validatingCoupon = false;
  String? _couponError;
  bool _submitting = false;

  // Class-level controllers, disposed in dispose() — this is a persistent
  // full screen (not a bottom sheet opened/closed repeatedly), so this
  // doesn't need the outer-method-plus-try/finally leak-fix pattern used
  // by sheet controllers elsewhere in the module (see referral_screen.dart
  // for the same reasoning).
  final _couponCtrl = TextEditingController();
  final _messageCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _couponCtrl.dispose();
    _messageCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await LiveClassApi.passes.list(classroomId: widget.classroomId);
      final active = res.results.where((p) => p.isActive).toList()..sort((a, b) => a.price.compareTo(b.price));
      if (!mounted) return;
      setState(() {
        _passes = active;
        _selectedPassId = active.isNotEmpty ? active.first.id : null;
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

  Future<void> _applyCoupon() async {
    final code = _couponCtrl.text.trim();
    if (code.isEmpty) return;
    setState(() {
      _validatingCoupon = true;
      _couponError = null;
      _appliedCoupon = null;
    });
    try {
      final coupon = await LiveClassApi.coupons.validate(code);
      if (!mounted) return;
      setState(() {
        _appliedCoupon = coupon;
        _validatingCoupon = false;
      });
    } on LiveClassApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _validatingCoupon = false;
        _couponError = e.message;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _validatingCoupon = false;
        _couponError = 'Could not validate this code.';
      });
    }
  }

  void _clearCoupon() {
    setState(() {
      _appliedCoupon = null;
      _couponError = null;
      _couponCtrl.clear();
    });
  }

  Future<void> _submit() async {
    final passId = _selectedPassId;
    if (passId == null) {
      _snack('Select a pass to request.');
      return;
    }
    setState(() => _submitting = true);
    try {
      await LiveClassApi.joinRequests.request(
        classroomId: widget.classroomId,
        classPassId: passId,
        // Only send a coupon code that's actually been validated — never
        // pass through unvalidated text, since the backend still enforces
        // it either way and a stale/typo'd code here would just surface
        // as a confusing 400 on submit instead of the inline validation
        // error the Apply button already showed.
        couponCode: _appliedCoupon?.code ?? '',
        message: _messageCtrl.text.trim(),
      );
      if (!mounted) return;
      _snack('Request sent — you\'ll be notified once the teacher responds.');
      Navigator.pop(context, true);
    } on LiveClassApiException catch (e) {
      setState(() => _submitting = false);
      _snack(e.message);
    } catch (_) {
      setState(() => _submitting = false);
      _snack('Could not send request — please try again.');
    }
  }

  @override
  Widget build(BuildContext context) {
    final title = widget.classroom?.title.isNotEmpty == true ? 'Join — ${widget.classroom!.title}' : 'Request to Join';
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(title),
      body: _loading
          ? const LiveClassLoading()
          : _error != null
              ? LiveClassErrorState(message: _error!, onRetry: _load)
              : _passes.isEmpty
                  ? const LiveClassEmptyState(
                      icon: Icons.confirmation_number_outlined,
                      title: 'No passes available for this classroom yet.',
                      subtitle: 'Check back once the teacher publishes one.',
                    )
                  : _buildForm(),
      bottomNavigationBar: _passes.isEmpty
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 10, 16, 14),
                child: SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: LiveClassColors.navy,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 15),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                    ),
                    onPressed: _submitting ? null : _submit,
                    child: _submitting
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : const Text('Send Request'),
                  ),
                ),
              ),
            ),
    );
  }

  Widget _buildForm() {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
      children: [
        Text('Choose a pass', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14.5, color: Colors.grey.shade800)),
        const SizedBox(height: 10),
        ..._passes.map(_passTile),
        const SizedBox(height: 20),
        Text('Have a coupon?', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14.5, color: Colors.grey.shade800)),
        const SizedBox(height: 10),
        if (_appliedCoupon != null)
          LiveClassCard(
            margin: EdgeInsets.zero,
            child: Row(
              children: [
                const Icon(Icons.local_offer_rounded, color: LiveClassColors.success, size: 20),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(_appliedCoupon!.code, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13.5)),
                      Text(_couponSummary(_appliedCoupon!), style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
                    ],
                  ),
                ),
                IconButton(onPressed: _clearCoupon, icon: const Icon(Icons.close_rounded, size: 18)),
              ],
            ),
          )
        else
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: TextField(
                  controller: _couponCtrl,
                  textCapitalization: TextCapitalization.characters,
                  decoration: liveClassInputDecoration('Enter coupon code').copyWith(errorText: _couponError),
                ),
              ),
              const SizedBox(width: 10),
              SizedBox(
                height: 48,
                child: OutlinedButton(
                  onPressed: _validatingCoupon ? null : _applyCoupon,
                  style: OutlinedButton.styleFrom(
                    foregroundColor: LiveClassColors.navy,
                    side: const BorderSide(color: LiveClassColors.navy),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(LiveClassRadius.chip)),
                  ),
                  child: _validatingCoupon
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: LiveClassColors.navy))
                      : const Text('Apply'),
                ),
              ),
            ],
          ),
        const SizedBox(height: 20),
        Text('Message to teacher (optional)', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14.5, color: Colors.grey.shade800)),
        const SizedBox(height: 10),
        TextField(
          controller: _messageCtrl,
          maxLines: 3,
          decoration: liveClassInputDecoration('Anything the teacher should know before accepting…'),
        ),
        const SizedBox(height: 90), // clear of the bottom CTA
      ],
    );
  }

  String _couponSummary(Coupon c) {
    if (c.discountPercent != null) return '${c.discountPercent}% off';
    if (c.discountAmount != null) return '${c.discountAmount} coins off';
    return 'Applied';
  }

  Widget _passTile(ClassPass p) {
    final selected = _selectedPassId == p.id;
    return GestureDetector(
      onTap: () => setState(() => _selectedPassId = p.id),
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(LiveClassRadius.card),
          border: Border.all(color: selected ? LiveClassColors.navy : Colors.grey.shade200, width: selected ? 1.5 : 1),
          boxShadow: const [LiveClassColors.cardShadow],
        ),
        child: Row(
          children: [
            Icon(
              selected ? Icons.radio_button_checked_rounded : Icons.radio_button_off_rounded,
              color: selected ? LiveClassColors.navy : Colors.grey.shade400,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    p.title.isNotEmpty ? p.title : _passTypeLabel(p.passType),
                    style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    [
                      _passTypeLabel(p.passType),
                      '${p.validityDays} day${p.validityDays == 1 ? '' : 's'}',
                      if (p.maxClasses != null) '${p.maxClasses} classes',
                    ].join(' · '),
                    style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600),
                  ),
                ],
              ),
            ),
            Text(
              p.price == 0 ? 'Free' : '${p.price.toStringAsFixed(0)} coins',
              style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: LiveClassColors.navy),
            ),
          ],
        ),
      ),
    );
  }
}