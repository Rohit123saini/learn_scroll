import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
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

class ParentLinkScreen extends StatefulWidget {
  const ParentLinkScreen({super.key});

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

  Future<void> _openVerify() async {
    final linked = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => const _VerifyLinkSheet(),
    );
    if (linked == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: lsAppBar(context, title: l10n.parentLinkScreenTitle),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _openVerify,
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
          onAction: _openVerify,
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
  const _VerifyLinkSheet();

  @override
  State<_VerifyLinkSheet> createState() => _VerifyLinkSheetState();
}

class _VerifyLinkSheetState extends State<_VerifyLinkSheet> {
  final _campusId = TextEditingController();
  final _token = TextEditingController();
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _campusId.dispose();
    _token.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final l10n = AppLocalizations.of(context)!;
    if (_campusId.text.trim().isEmpty || _token.text.trim().isEmpty) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await CampusService.verifyParentLink(
        campusId: _campusId.text.trim(),
        token: _token.text.trim(),
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
            ),
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
