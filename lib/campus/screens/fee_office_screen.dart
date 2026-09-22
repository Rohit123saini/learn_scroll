import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// FEE OFFICE — staff-side: record a manual payment, refund a wallet one
//
// `record` is for cash/cheque/bank-transfer/other ONLY (wallet payments
// are self-serve via `pay`, backend 400s a wallet `record`, §19). `refund`
// only ever applies to a `success` + `wallet` payment — button is gated by
// `FeePayment.isRefundable` client-side, backend re-checks anyway.
// ============================================================

class FeeOfficeScreen extends StatefulWidget {
  final String campusId;
  final CampusAccess access;
  const FeeOfficeScreen({super.key, required this.campusId, required this.access});

  @override
  State<FeeOfficeScreen> createState() => _FeeOfficeScreenState();
}

class _FeeOfficeScreenState extends State<FeeOfficeScreen> {
  bool _loading = true;
  String? _error;
  List<FeeInvoice> _invoices = const [];
  Map<String, StudentEnrollment> _enrollmentsById = const {};
  Map<String, FeeStructure> _structuresById = const {};

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
        CampusService.feeInvoices(),
        CampusService.enrollments(),
        CampusService.feeStructures(widget.campusId),
      ]);
      if (!mounted) return;
      final invoices = results[0] as List<FeeInvoice>;
      final enrollments = results[1] as List<StudentEnrollment>;
      final structures = results[2] as List<FeeStructure>;
      invoices.sort((a, b) => a.isSettled == b.isSettled ? 0 : (a.isSettled ? 1 : -1));
      setState(() {
        _invoices = invoices;
        _enrollmentsById = {for (final e in enrollments) e.id: e};
        _structuresById = {for (final s in structures) s.id: s};
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: lsAppBar(context, title: l10n.feeOfficeTitle),
      body: RefreshIndicator(onRefresh: _load, child: _body(l10n)),
    );
  }

  Widget _body(AppLocalizations l10n) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 64),
        SizedBox(height: 10),
        LsSkeletonBox(height: 64),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(
          title: l10n.setupLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _load,
        ),
      ]);
    }
    if (_invoices.isEmpty) {
      return EmptyStateWidget(icon: Icons.toll_outlined, title: l10n.myFeesEmpty);
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 40),
      itemCount: _invoices.length,
      itemBuilder: (_, i) {
        final invoice = _invoices[i];
        final enrollment = _enrollmentsById[invoice.enrollmentId];
        final structure = _structuresById[invoice.feeStructureId];
        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: _OfficeInvoiceTile(
            invoice: invoice,
            enrollment: enrollment,
            structure: structure,
            onChanged: _load,
          ),
        );
      },
    );
  }
}

class _OfficeInvoiceTile extends StatelessWidget {
  final FeeInvoice invoice;
  final StudentEnrollment? enrollment;
  final FeeStructure? structure;
  final VoidCallback onChanged;

  const _OfficeInvoiceTile({
    required this.invoice,
    required this.onChanged,
    this.enrollment,
    this.structure,
  });

  Color _statusColor(ColorScheme cs) => switch (invoice.status) {
        'paid' => Colors.green,
        'partial' => Colors.orange,
        'overdue' => cs.error,
        'waived' => cs.outline,
        _ => Colors.orange,
      };

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return LsCard(
      onTap: () => showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        builder: (_) => _InvoiceOfficeSheet(
          invoice: invoice,
          enrollment: enrollment,
          structure: structure,
          onChanged: onChanged,
        ),
      ),
      child: Row(children: [
        CircleAvatar(
          radius: 15,
          backgroundColor: cs.tertiaryContainer,
          child: Text(enrollment?.student?.initials ?? '?', style: const TextStyle(fontSize: 11)),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(enrollment?.student?.displayName ?? invoice.enrollmentId,
                maxLines: 1, overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
            Text(structure?.title ?? '—', style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
          ]),
        ),
        LsStatusChip(label: invoice.status, color: _statusColor(cs)),
      ]),
    );
  }
}

class _InvoiceOfficeSheet extends StatefulWidget {
  final FeeInvoice invoice;
  final StudentEnrollment? enrollment;
  final FeeStructure? structure;
  final VoidCallback onChanged;

  const _InvoiceOfficeSheet({
    required this.invoice,
    required this.onChanged,
    this.enrollment,
    this.structure,
  });

  @override
  State<_InvoiceOfficeSheet> createState() => _InvoiceOfficeSheetState();
}

class _InvoiceOfficeSheetState extends State<_InvoiceOfficeSheet> {
  bool _loadingPayments = true;
  List<FeePayment> _payments = const [];
  final _amount = TextEditingController();
  final _notes = TextEditingController();
  String _mode = 'cash';
  bool _saving = false;
  String? _error;
  String? _busyPaymentId;

  @override
  void initState() {
    super.initState();
    _amount.text = widget.invoice.remaining.toStringAsFixed(0);
    _loadPayments();
  }

  @override
  void dispose() {
    _amount.dispose();
    _notes.dispose();
    super.dispose();
  }

  Future<void> _loadPayments() async {
    try {
      final rows = await CampusService.feePayments(widget.invoice.id);
      if (mounted) setState(() {
        _payments = rows;
        _loadingPayments = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loadingPayments = false);
    }
  }

  Future<void> _record() async {
    final amount = double.tryParse(_amount.text.trim());
    final l10n = AppLocalizations.of(context)!;
    if (amount == null || amount <= 0) {
      setState(() => _error = l10n.resultMarksInvalid);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.recordFeePayment(
        invoiceId: widget.invoice.id,
        paymentMode: _mode,
        amount: amount,
        notes: _notes.text.trim(),
      );
      if (mounted) {
        lsSnack(context, l10n.feePaySuccess);
        widget.onChanged();
        _loadPayments();
        setState(() => _saving = false);
      }
    } on CampusApiException catch (e) {
      if (mounted) setState(() {
        _saving = false;
        _error = e.message;
      });
    }
  }

  Future<void> _refund(FeePayment payment) async {
    setState(() => _busyPaymentId = payment.id);
    try {
      await CampusService.refundFeePayment(payment.id);
      if (mounted) {
        widget.onChanged();
        _loadPayments();
      }
    } on CampusApiException catch (e) {
      if (mounted) lsSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _busyPaymentId = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        top: 18,
        bottom: MediaQuery.of(context).viewInsets.bottom + 18,
      ),
      child: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Center(
            child: Container(
              width: 36,
              height: 4,
              decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
            ),
          ),
          const SizedBox(height: 16),
          Text(widget.enrollment?.student?.displayName ?? widget.invoice.enrollmentId,
              style: LsType.head(context, size: 16)),
          Text(widget.structure?.title ?? '—', style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
          const SizedBox(height: 16),

          if (!widget.invoice.isSettled) ...[
            Text(l10n.feeRecordPayment, style: LsType.head(context, size: 13.5)),
            const SizedBox(height: 10),
            DropdownButtonFormField<String>(
              value: _mode,
              isExpanded: true,
              items: const [
                DropdownMenuItem(value: 'cash', child: Text('Cash')),
                DropdownMenuItem(value: 'cheque', child: Text('Cheque')),
                DropdownMenuItem(value: 'bank_transfer', child: Text('Bank transfer')),
                DropdownMenuItem(value: 'other', child: Text('Other')),
              ],
              decoration:
                  InputDecoration(labelText: l10n.feePaymentModeLabel, border: const OutlineInputBorder()),
              onChanged: _saving ? null : (v) => setState(() => _mode = v ?? _mode),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _amount,
              enabled: !_saving,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              decoration:
                  InputDecoration(labelText: l10n.feeAmountLabel, border: const OutlineInputBorder()),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _notes,
              enabled: !_saving,
              decoration:
                  InputDecoration(labelText: l10n.feeNotesLabel, border: const OutlineInputBorder()),
            ),
            if (_error != null) ...[
              const SizedBox(height: 6),
              Text(_error!, style: TextStyle(color: cs.error, fontSize: 12.5)),
            ],
            const SizedBox(height: 10),
            LsPrimaryButton(label: l10n.feeRecordPayment, loading: _saving, onPressed: _saving ? null : _record),
            const SizedBox(height: 18),
          ],

          Text(l10n.feePaymentHistory, style: LsType.head(context, size: 13.5)),
          const SizedBox(height: 8),
          if (_loadingPayments)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 10),
              child: Center(child: CircularProgressIndicator()),
            )
          else if (_payments.isEmpty)
            Text(l10n.feeNoPayments, style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant))
          else
            for (final p in _payments)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(children: [
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('₹${p.amount.toStringAsFixed(0)} · ${p.paymentMode}',
                          style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
                      Text(p.status, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
                    ]),
                  ),
                  if (p.isRefundable)
                    _busyPaymentId == p.id
                        ? const SizedBox(
                            width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                        : TextButton(onPressed: () => _refund(p), child: Text(l10n.feeRefund)),
                ]),
              ),
        ]),
      ),
    );
  }
}
