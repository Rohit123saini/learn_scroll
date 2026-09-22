import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// DIGITAL ID CARD
//
// `qr_token` hamesha server-generated (§19) — is app se sirf token STRING
// dikhta/copy hota hai, ek asli QR image nahi (koi QR-drawing package is
// pass me confirm nahi kar paya, safe side liya hai jaise file-upload me
// liya tha). Scanning-hardware integration khud backend pe bhi abhi future
// work hai.
// ============================================================

class DigitalIdCardScreen extends StatefulWidget {
  final String campusId;
  final CampusAccess access;
  const DigitalIdCardScreen({super.key, required this.campusId, required this.access});

  @override
  State<DigitalIdCardScreen> createState() => _DigitalIdCardScreenState();
}

class _DigitalIdCardScreenState extends State<DigitalIdCardScreen> {
  bool _loading = true;
  String? _error;
  List<DigitalIDCard> _cards = const [];
  bool _issuing = false;

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
      final rows = await CampusService.digitalIdCards(widget.campusId);
      if (!mounted) return;
      setState(() {
        _cards = rows;
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

  DigitalIDCard? get _myCard {
    final myId = widget.access.myUserId;
    if (myId == null) return null;
    for (final c in _cards) {
      if (c.userId == myId) return c;
    }
    return null;
  }

  Future<void> _issueOwn() async {
    final myId = widget.access.myUserId;
    if (myId == null) return;
    setState(() => _issuing = true);
    try {
      await CampusService.issueDigitalIdCard(userId: myId, campusId: widget.campusId);
      if (mounted) _load();
    } on CampusApiException catch (e) {
      if (mounted) lsSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _issuing = false);
    }
  }

  Future<void> _issueForOthers() async {
    final issued = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _IssueForOthersSheet(campusId: widget.campusId),
    );
    if (issued == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: lsAppBar(context, title: l10n.idCardTitle),
      body: RefreshIndicator(onRefresh: _load, child: _body(l10n)),
      floatingActionButton: widget.access.isManagement
          ? FloatingActionButton.extended(
              onPressed: _issueForOthers,
              icon: const Icon(Icons.badge_outlined),
              label: Text(l10n.idCardIssueForOthers),
            )
          : null,
    );
  }

  Widget _body(AppLocalizations l10n) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [LsSkeletonBox(height: 160)]);
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
    final mine = _myCard;
    return ListView(
      padding: const EdgeInsets.all(14),
      children: [
        if (mine != null)
          _CardTile(card: mine)
        else if (widget.access.myUserId != null)
          EmptyStateWidget(
            icon: Icons.badge_outlined,
            title: l10n.idCardNotIssued,
            actionLabel: _issuing ? null : l10n.idCardIssueOwn,
            onAction: _issuing ? null : _issueOwn,
          ),
        if (widget.access.isManagement) ...[
          const SizedBox(height: 18),
          Text(l10n.idCardAllIssued, style: LsType.head(context, size: 14)),
          const SizedBox(height: 10),
          for (final c in _cards.where((c) => c.id != mine?.id)) _CardTile(card: c, compact: true),
        ],
      ],
    );
  }
}

class _CardTile extends StatelessWidget {
  final DigitalIDCard card;
  final bool compact;
  const _CardTile({required this.card, this.compact = false});

  static String _fmt(DateTime d) =>
      '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')}/${d.year}';

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: LsCard(
        tinted: !compact,
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            CircleAvatar(
              radius: 16,
              backgroundColor: cs.primaryContainer,
              child: Text(card.user?.initials ?? '?',
                  style: TextStyle(fontWeight: FontWeight.w700, color: cs.onPrimaryContainer)),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(card.user?.displayName ?? card.userId,
                  maxLines: 1, overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700)),
            ),
          ]),
          if (!compact) ...[
            const SizedBox(height: 14),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 12),
              decoration: BoxDecoration(
                color: cs.surface,
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: cs.outlineVariant),
              ),
              child: Column(children: [
                Icon(Icons.qr_code_2_rounded, size: 56, color: cs.onSurfaceVariant),
                const SizedBox(height: 8),
                SelectableText(card.qrToken,
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 11, fontFamily: 'monospace', color: cs.onSurfaceVariant)),
              ]),
            ),
            const SizedBox(height: 10),
          ],
          if (card.issuedAt != null)
            LsMetaRow(icon: Icons.event_outlined, label: l10n.idCardIssuedOn, value: _fmt(card.issuedAt!)),
          if (card.validUntil != null)
            LsMetaRow(
                icon: Icons.event_busy_outlined, label: l10n.idCardValidUntil, value: _fmt(card.validUntil!)),
        ]),
      ),
    );
  }
}

class _IssueForOthersSheet extends StatefulWidget {
  final String campusId;
  const _IssueForOthersSheet({required this.campusId});

  @override
  State<_IssueForOthersSheet> createState() => _IssueForOthersSheetState();
}

class _IssueForOthersSheetState extends State<_IssueForOthersSheet> {
  final _userId = TextEditingController();
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _userId.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_userId.text.trim().isEmpty) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.issueDigitalIdCard(userId: _userId.text.trim(), campusId: widget.campusId);
      if (mounted) Navigator.pop(context, true);
    } on CampusApiException catch (e) {
      if (mounted) setState(() {
        _saving = false;
        _error = e.message;
      });
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
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Container(
          width: 36,
          height: 4,
          decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
        ),
        const SizedBox(height: 16),
        Align(
          alignment: Alignment.centerLeft,
          child: Text(l10n.idCardIssueForOthers, style: LsType.head(context, size: 16)),
        ),
        const SizedBox(height: 16),
        TextField(
          controller: _userId,
          enabled: !_saving,
          decoration: InputDecoration(
            labelText: l10n.staffUserIdLabel,
            hintText: l10n.staffUserIdHint,
            border: const OutlineInputBorder(),
          ),
        ),
        if (_error != null) ...[
          const SizedBox(height: 8),
          Align(
            alignment: Alignment.centerLeft,
            child: Text(_error!, style: TextStyle(color: cs.error, fontSize: 12.5)),
          ),
        ],
        const SizedBox(height: 14),
        LsPrimaryButton(label: l10n.save, loading: _saving, onPressed: _saving ? null : _submit),
      ]),
    );
  }
}
