import 'dart:io';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart' show DateFormat;
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// MY FEES — student's own invoices, paid from the coin wallet
//
// `POST /fee-payments/pay/` debits `User.coin` directly (FEE-2) — is app se
// koi gateway screen nahi khulti, seedha wallet se paisa katta hai. Sirf
// poore coins (whole number) chalte hain — paisa-level partial payment
// wallet se possible nahi (§8 unit-mismatch), isliye amount field integer
// hi leta hai.
// ============================================================

class MyFeesScreen extends StatefulWidget {
  final String campusId;
  final List<StudentEnrollment> myEnrollments;
  const MyFeesScreen({super.key, required this.campusId, required this.myEnrollments});

  @override
  State<MyFeesScreen> createState() => _MyFeesScreenState();
}

class _MyFeesScreenState extends State<MyFeesScreen> {
  bool _loading = true;
  String? _error;
  List<FeeInvoice> _invoices = const [];
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
        Future.wait(widget.myEnrollments.map((e) => CampusService.feeInvoices(enrollmentId: e.id))),
        CampusService.feeStructures(widget.campusId),
      ]);
      if (!mounted) return;
      final invoiceLists = results[0] as List<List<FeeInvoice>>;
      final structures = results[1] as List<FeeStructure>;
      setState(() {
        _invoices = invoiceLists.expand((l) => l).toList();
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
      appBar: lsAppBar(context, title: l10n.myFeesTitle),
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
        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: _InvoiceTile(
            invoice: invoice,
            structure: _structuresById[invoice.feeStructureId],
            onPaid: _load,
          ),
        );
      },
    );
  }
}

class _InvoiceTile extends StatefulWidget {
  final FeeInvoice invoice;
  final FeeStructure? structure;
  final VoidCallback onPaid;
  const _InvoiceTile({required this.invoice, required this.onPaid, this.structure});

  @override
  State<_InvoiceTile> createState() => _InvoiceTileState();
}

class _InvoiceTileState extends State<_InvoiceTile> {
  bool _paying = false;

  Color _statusColor(ColorScheme cs) => switch (widget.invoice.status) {
        'paid' => Colors.green,
        'partial' => Colors.orange,
        'overdue' => cs.error,
        'waived' => cs.outline,
        _ => Colors.orange,
      };

  String _statusLabel(AppLocalizations l10n) => switch (widget.invoice.status) {
        'paid' => l10n.feeStatusPaid,
        'partial' => l10n.feeStatusPartial,
        'overdue' => l10n.feeStatusOverdue,
        'waived' => l10n.feeStatusWaived,
        _ => l10n.feeStatusPending,
      };

  Future<void> _pay() async {
    final l10n = AppLocalizations.of(context)!;
    final amount = await showModalBottomSheet<int>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _PayAmountSheet(invoice: widget.invoice),
    );
    if (amount == null) return;
    setState(() => _paying = true);
    try {
      await CampusService.payFee(invoiceId: widget.invoice.id, amount: amount);
      if (mounted) {
        lsSnack(context, l10n.feePaySuccess);
        widget.onPaid();
      }
    } on InsufficientCoinsException catch (e) {
      if (mounted) {
        setState(() => _paying = false);
        showDialog<void>(
          context: context,
          builder: (_) => AlertDialog(
            title: Text(l10n.feeInsufficientCoinsTitle),
            content: Text(l10n.feeInsufficientCoinsBody(e.coinsNeeded)),
            actions: [
              TextButton(onPressed: () => Navigator.pop(context), child: Text(l10n.save)),
            ],
          ),
        );
      }
      return;
    } on CampusApiException catch (e) {
      if (mounted) {
        setState(() => _paying = false);
        lsSnack(context, e.message, error: true);
      }
      return;
    }
    if (mounted) setState(() => _paying = false);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final invoice = widget.invoice;
    return LsCard(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(
            child: Text(widget.structure?.title ?? '—',
                style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700)),
          ),
          LsStatusChip(label: _statusLabel(l10n), color: _statusColor(cs)),
        ]),
        const SizedBox(height: 8),
        LsMetaRow(
            icon: Icons.toll_outlined,
            label: l10n.feeAmountDueLabel,
            value: '₹${invoice.amountDue.toStringAsFixed(0)}'),
        if (invoice.amountPaid > 0)
          LsMetaRow(
              icon: Icons.check_circle_outline_rounded,
              label: l10n.feeAmountPaidLabel,
              value: '₹${invoice.amountPaid.toStringAsFixed(0)}'),
        if (invoice.amountPaid > 0) ...[
          const SizedBox(height: 10),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              icon: const Icon(Icons.receipt_long_outlined, size: 17),
              label: Text(l10n.feeReceiptsCta),
              onPressed: () => showModalBottomSheet<void>(
                context: context,
                isScrollControlled: true,
                useSafeArea: true,
                builder: (_) => _ReceiptsSheet(invoiceId: invoice.id),
              ),
            ),
          ),
        ],
        if (!invoice.isSettled) ...[
          const SizedBox(height: 4),
          LsPrimaryButton(
            label: l10n.feePayCta,
            icon: Icons.wallet_outlined,
            loading: _paying,
            onPressed: _paying ? null : _pay,
          ),
        ],
      ]),
    );
  }
}

// Task 5 subtask 4 — student-facing counterpart to the office's payment
// history list in `fee_office_screen.dart`, read-only (no record/refund
// here — this is the payer's own view of what they've already paid).
class _ReceiptsSheet extends StatefulWidget {
  final String invoiceId;
  const _ReceiptsSheet({required this.invoiceId});

  @override
  State<_ReceiptsSheet> createState() => _ReceiptsSheetState();
}

class _ReceiptsSheetState extends State<_ReceiptsSheet> {
  bool _loading = true;
  List<FeePayment> _payments = const [];
  String? _downloadingId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final rows = await CampusService.feePayments(widget.invoiceId);
      if (mounted) setState(() {
        _payments = rows;
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  // Same download-to-temp-file, then `Share.shareXFiles` shape
  // `test_result_screen._shareCertificate` uses for the certificate share
  // card, and `fee_office_screen._downloadReceipt` uses for reprints.
  Future<void> _download(FeePayment payment) async {
    if (_downloadingId != null) return;
    final l10n = AppLocalizations.of(context)!;
    setState(() => _downloadingId = payment.id);
    try {
      final bytes = await CampusService.downloadFeeReceiptPdf(payment.id);
      final dir = await getTemporaryDirectory();
      final file = File('${dir.path}/fee_receipt_${payment.id}.pdf');
      await file.writeAsBytes(bytes, flush: true);
      if (mounted) {
        await Share.shareXFiles([XFile(file.path)], text: l10n.feeReceiptsTitle);
      }
    } on CampusApiException catch (e) {
      if (mounted) {
        lsSnack(context, e.isReceiptUnavailable ? l10n.feeReceiptUnavailable : e.message, error: true);
      }
    } catch (_) {
      if (mounted) lsSnack(context, l10n.feeReceiptDownloadFailed, error: true);
    } finally {
      if (mounted) setState(() => _downloadingId = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final successPayments = _payments.where((p) => p.status == 'success').toList();
    return Padding(
      padding: EdgeInsets.only(
        left: 16,
        right: 16,
        top: 18,
        bottom: MediaQuery.of(context).viewInsets.bottom + 18,
      ),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        Center(
          child: Container(
            width: 36,
            height: 4,
            decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
          ),
        ),
        const SizedBox(height: 16),
        Text(l10n.feeReceiptsTitle, style: LsType.head(context, size: 16)),
        const SizedBox(height: 12),
        if (_loading)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 10),
            child: Center(child: CircularProgressIndicator()),
          )
        else if (successPayments.isEmpty)
          Text(l10n.feeNoPayments, style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant))
        else
          for (final p in successPayments)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(children: [
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('₹${p.amount.toStringAsFixed(0)} · ${p.paymentMode.replaceAll('_', ' ')}',
                        style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
                    if (p.createdAt != null)
                      Text(
                        DateFormat('d MMM y, h:mm a', Localizations.localeOf(context).toString())
                            .format(p.createdAt!.toLocal()),
                        style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant),
                      ),
                  ]),
                ),
                _downloadingId == p.id
                    ? const Padding(
                        padding: EdgeInsets.symmetric(horizontal: 10),
                        child:
                            SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                      )
                    : IconButton(
                        icon: const Icon(Icons.download_rounded, size: 19),
                        tooltip: l10n.feeReceiptDownload,
                        onPressed: () => _download(p),
                      ),
              ]),
            ),
      ]),
    );
  }
}

class _PayAmountSheet extends StatefulWidget {
  final FeeInvoice invoice;
  const _PayAmountSheet({required this.invoice});

  @override
  State<_PayAmountSheet> createState() => _PayAmountSheetState();
}

class _PayAmountSheetState extends State<_PayAmountSheet> {
  late final _amount =
      TextEditingController(text: widget.invoice.remaining.toStringAsFixed(0));
  String? _error;

  @override
  void dispose() {
    _amount.dispose();
    super.dispose();
  }

  void _confirm() {
    final l10n = AppLocalizations.of(context)!;
    final value = int.tryParse(_amount.text.trim());
    if (value == null || value <= 0) {
      setState(() => _error = l10n.resultMarksInvalid);
      return;
    }
    Navigator.pop(context, value);
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
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Container(
          width: 36,
          height: 4,
          decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
        ),
        const SizedBox(height: 16),
        Align(
          alignment: Alignment.centerLeft,
          child: Text(l10n.feePayCta, style: LsType.head(context, size: 16)),
        ),
        const SizedBox(height: 6),
        Align(
          alignment: Alignment.centerLeft,
          child: Text(l10n.feeWalletCoinsNote,
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant, height: 1.4)),
        ),
        const SizedBox(height: 14),
        TextField(
          controller: _amount,
          keyboardType: TextInputType.number,
          decoration: InputDecoration(labelText: l10n.feeAmountLabel, border: const OutlineInputBorder()),
        ),
        if (_error != null) ...[
          const SizedBox(height: 8),
          Align(
            alignment: Alignment.centerLeft,
            child: Text(_error!, style: TextStyle(color: cs.error, fontSize: 12.5)),
          ),
        ],
        const SizedBox(height: 12),
        LsPrimaryButton(label: l10n.feePayCta, icon: Icons.wallet_outlined, onPressed: _confirm),
      ]),
    );
  }
}
