import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../l10n/app_localizations.dart';
import '../../services/deep_link_service.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/scan_qr_screen.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';
import 'parent_child_overview_screen.dart';

// ============================================================
// PARENT LINKS — "family access"
//
// ⚠️ Scope note: `CampusParentLink` is READ-ONLY from this app's point of
// view (design doc §1) — the only write path is `POST /parent-links/verify/`,
// jo ek raw access-token resolve karta hai. Wo token khud campus app me
// kabhi generate/dikhaya nahi jaata — `message` app ke maujooda
// "ParentAccessCode" flow se aata hai (§10, bridge.resolve_parent_from_token).
//
// Iska matlab: "teacher/school parent ko add karta hai" — ye screen wo
// action nahi karti (wo action is app ke bahar, `message` module me hota
// hai, jahan se code generate hota hai). Ye screen sirf do cheezein karti
// hai: (1) parent apna access-code daal ke link verify kare, (2) already
// linked bachchon ki list dekhe. Naya campus/staff/student screens ki tarah
// standalone hai — kisi ek campus ke context ki zaroorat nahi (parent ke
// bachche alag-alag campuses me ho sakte hain).
// ============================================================

// TASK 11.3 — link + QR: a campus invite link
// (`…/parent-link?campus=12&code=ABCD1234`, tapped or scanned) opens this screen with
// [initialCampusId]/[initialCode]; the verify sheet then opens straight away, pre-filled,
// so the parent only has to press "Link". The sheet also has a "Scan QR" button.
class ParentLinkScreen extends StatefulWidget {
  final String? initialCampusId;
  final String? initialCode;
  const ParentLinkScreen({super.key, this.initialCampusId, this.initialCode});

  @override
  State<ParentLinkScreen> createState() => _ParentLinkScreenState();
}

class _ParentLinkScreenState extends State<ParentLinkScreen> {
  bool _loading = true;
  String? _error;
  List<CampusParentLink> _links = const [];

  @override
  void initState() {
    super.initState();
    _load();
    if ((widget.initialCode ?? '').isNotEmpty) {
      // Open the pre-filled sheet once the first frame is up (needs a mounted Scaffold).
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _openVerify(campusId: widget.initialCampusId, code: widget.initialCode);
      });
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final rows = await CampusService.parentLinks();
      if (!mounted) return;
      setState(() {
        _links = rows;
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

  Future<void> _openVerify({String? campusId, String? code}) async {
    final linked = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _VerifyLinkSheet(initialCampusId: campusId, initialCode: code),
    );
    if (linked == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: lsAppBar(context, title: l10n.parentLinkScreenTitle),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _openVerify(),
        icon: const Icon(Icons.link_rounded),
        label: Text(l10n.parentLinkVerifyTitle),
      ),
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
          title: l10n.parentLinkLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _load,
        ),
      ]);
    }
    if (_links.isEmpty) {
      return ListView(children: [
        const SizedBox(height: 60),
        EmptyStateWidget(
          icon: Icons.family_restroom_rounded,
          title: l10n.parentLinkNoChildren,
          subtitle: l10n.parentLinkVerifyHint,
          actionLabel: l10n.parentLinkVerifyTitle,
          onAction: () => _openVerify(),
        ),
      ]);
    }
    return ListView(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 90),
      children: [
        Padding(
          padding: const EdgeInsets.only(left: 4, bottom: 8),
          child: Text(l10n.parentLinkMyChildrenTitle, style: LsType.head(context, size: 14)),
        ),
        for (final link in _links) _LinkTile(link: link),
      ],
    );
  }
}

class _LinkTile extends StatelessWidget {
  final CampusParentLink link;
  const _LinkTile({required this.link});

  static String _fmt(DateTime d) =>
      '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')}/${d.year}';

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: LsCard(
        // 🔥 FIX — pehle ye card kabhi tappable hi nahi tha, "linked
        // children" list dead-end thi. Backend already parent ko attendance/
        // report-card/notices padhne deta hai (`is_linked_parent_of_student`)
        // — bas koi screen nahi thi. Ab `ParentChildOverviewScreen`.
        onTap: () => Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => ParentChildOverviewScreen(link: link)),
        ),
        child: Row(children: [
          CircleAvatar(
            radius: 18,
            backgroundColor: cs.primaryContainer,
            child: Text(link.student?.initials ?? '?',
                style: TextStyle(fontWeight: FontWeight.w700, color: cs.onPrimaryContainer)),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(link.student?.displayName ?? link.studentId,
                  style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700)),
              if (link.createdAt != null)
                Text(l10n.parentLinkedOn(_fmt(link.createdAt!)),
                    style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
            ]),
          ),
          Icon(Icons.chevron_right_rounded, color: cs.outline),
        ]),
      ),
    );
  }
}

class _VerifyLinkSheet extends StatefulWidget {
  final String? initialCampusId;
  final String? initialCode;
  const _VerifyLinkSheet({this.initialCampusId, this.initialCode});

  @override
  State<_VerifyLinkSheet> createState() => _VerifyLinkSheetState();
}

class _VerifyLinkSheetState extends State<_VerifyLinkSheet> {
  final _campusId = TextEditingController();
  final _token = TextEditingController();
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    if ((widget.initialCampusId ?? '').isNotEmpty) _campusId.text = widget.initialCampusId!;
    if ((widget.initialCode ?? '').isNotEmpty) _token.text = widget.initialCode!;
  }

  @override
  void dispose() {
    _campusId.dispose();
    _token.dispose();
    super.dispose();
  }

  /// Parent taps a link like
  /// "https://learnscroll.app/parent-link?campus=12&code=ABCD1234" and
  /// pastes it here (or the OS opens it straight into this screen with
  /// the fields pre-filled — see the app's deep-link handler). This just
  /// parses campus+code out of any pasted URL so the parent never has to
  /// type either by hand.
  void _tryParsePastedLink(String value) {
    final target = DeepLinkService.parsePayload(value);
    if (target is ParentInviteTarget) {
      if (target.campusId != null) _campusId.text = target.campusId!;
      _token.text = target.code;
      return;
    }
    final uri = Uri.tryParse(value.trim());
    if (uri == null || uri.queryParameters.isEmpty) return;
    final campus = uri.queryParameters['campus'];
    final code = uri.queryParameters['code'] ?? uri.queryParameters['token'];
    if (campus != null) _campusId.text = campus;
    if (code != null) _token.text = code;
  }

  Future<void> _scanQr() async {
    final raw = await Navigator.push<String>(
      context,
      MaterialPageRoute(builder: (_) => const ScanQrScreen(returnRaw: true)),
    );
    if (raw == null || !mounted) return;
    final target = DeepLinkService.parsePayload(raw);
    if (target is! ParentInviteTarget) {
      setState(() => _error = AppLocalizations.of(context)!.parentLinkNotAParentQr);
      return;
    }
    setState(() {
      _error = null;
      if (target.campusId != null) _campusId.text = target.campusId!;
      _token.text = target.code;
    });
  }

  Future<void> _submit() async {
    final l10n = AppLocalizations.of(context)!;
    if (_campusId.text.trim().isEmpty || _token.text.trim().isEmpty) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      // NEW — the working confirm endpoint (see campus/parent_invite.py's
      // module docstring for why the old verifyParentLink() call it used
      // to make here never actually succeeded).
      await CampusService.confirmParentLink(
        campusId: _campusId.text.trim(),
        code: _token.text.trim().toUpperCase(),
      );
      if (mounted) {
        Navigator.pop(context, true);
        lsSnack(context, l10n.parentLinkSuccess);
      }
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
      child: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 36,
            height: 4,
            decoration:
                BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
          ),
          const SizedBox(height: 16),
          Align(
            alignment: Alignment.centerLeft,
            child: Text(l10n.parentLinkVerifyTitle, style: LsType.head(context, size: 16)),
          ),
          const SizedBox(height: 6),
          Align(
            alignment: Alignment.centerLeft,
            child: Text(l10n.parentLinkVerifyHint,
                style: TextStyle(fontSize: 12, height: 1.4, color: cs.onSurfaceVariant)),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _campusId,
            enabled: !_saving,
            decoration: InputDecoration(
              labelText: l10n.parentLinkCampusIdLabel,
              hintText: l10n.parentLinkCampusIdHint,
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _token,
            enabled: !_saving,
            decoration: InputDecoration(
              labelText: l10n.parentLinkTokenLabel,
              hintText: l10n.parentLinkTokenHint,
              border: const OutlineInputBorder(),
              suffixIcon: Row(mainAxisSize: MainAxisSize.min, children: [
                IconButton(
                  tooltip: l10n.parentLinkScanQr,
                  icon: const Icon(Icons.qr_code_scanner_rounded, size: 20),
                  onPressed: _saving ? null : _scanQr,
                ),
                IconButton(
                  tooltip: l10n.parentLinkPasteLink,
                  icon: const Icon(Icons.content_paste_rounded, size: 18),
                  onPressed: () async {
                    final data = await Clipboard.getData('text/plain');
                    if (data?.text != null) _tryParsePastedLink(data!.text!);
                    setState(() {});
                  },
                ),
              ]),
            ),
            onChanged: (v) {
              // Pasting a full link (not just a short code) auto-fills both
              // fields — typing a plain code leaves campus ID untouched.
              if (v.contains('://')) _tryParsePastedLink(v);
            },
            onSubmitted: (_) => _submit(),
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerLeft,
              child: Text(_error!, style: TextStyle(color: cs.error, fontSize: 12.5)),
            ),
          ],
          const SizedBox(height: 14),
          LsPrimaryButton(
            label: l10n.parentLinkSubmit,
            icon: Icons.link_rounded,
            loading: _saving,
            onPressed: _saving ? null : _submit,
          ),
        ]),
      ),
    );
  }
}
