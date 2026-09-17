import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// CAMPUS — NOTICES
//
// Read sabke liye. Post karna role pe depend karta hai:
//   • Management  -> poore campus ka notice
//   • Class teacher -> sirf apni section ka
//   • Baaki        -> compose button dikhta hi nahi
//
// Ye teenon `CampusAccess` se decide hote hain, yahan koi role string
// compare nahi hai — wahi ek jagah rule rehta hai.
// ============================================================

class NoticesScreen extends StatefulWidget {
  final CampusAccess access;
  final Map<String, String> sectionLabels;

  const NoticesScreen({super.key, required this.access, this.sectionLabels = const {}});

  @override
  State<NoticesScreen> createState() => _NoticesScreenState();
}

class _NoticesScreenState extends State<NoticesScreen> {
  bool _loading = true;
  String? _error;
  List<Notice> _notices = const [];

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
      final rows = await CampusService.notices(widget.access.campus.id);
      if (!mounted) return;
      rows.sort((a, b) {
        if (a.isPinned != b.isPinned) return a.isPinned ? -1 : 1;
        return (b.createdAt ?? DateTime(0)).compareTo(a.createdAt ?? DateTime(0));
      });
      setState(() {
        _notices = rows;
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

  bool get _canCompose {
    final a = widget.access;
    return a.canPostCampusNotice || a.classTeacherSectionIds.isNotEmpty;
  }

  Future<void> _compose() async {
    final created = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _ComposeSheet(
        access: widget.access,
        sectionLabels: widget.sectionLabels,
      ),
    );
    if (created == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.campusNoticesTitle, style: LsType.head(context, size: 15)),
      ),
      floatingActionButton: _canCompose
          ? FloatingActionButton.extended(
              onPressed: _compose,
              icon: const Icon(Icons.campaign_outlined),
              label: Text(l10n.campusPostNotice),
            )
          : null,
      body: RefreshIndicator(onRefresh: _load, child: _body(cs, l10n)),
    );
  }

  Widget _body(ColorScheme cs, AppLocalizations l10n) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 92),
        SizedBox(height: 10),
        LsSkeletonBox(height: 92),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(
          title: l10n.campusNoticesLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _load,
        ),
      ]);
    }
    if (_notices.isEmpty) {
      return EmptyStateWidget(
        icon: Icons.campaign_outlined,
        title: l10n.campusNoNotices,
        actionLabel: _canCompose ? l10n.campusPostNotice : null,
        onAction: _canCompose ? _compose : null,
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 90),
      itemCount: _notices.length,
      itemBuilder: (_, i) {
        final n = _notices[i];
        final scopeName = n.sectionId != null
            ? (widget.sectionLabels[n.sectionId] ?? l10n.noticeScopeSection)
            : switch (n.scopeLabel) {
                'class' => l10n.noticeScopeClass,
                'department' => l10n.noticeScopeDepartment,
                _ => l10n.noticeScopeCampus,
              };

        return Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: LsCard(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                if (n.isPinned) ...[
                  Icon(Icons.push_pin_rounded, size: 14, color: cs.primary),
                  const SizedBox(width: 6),
                ],
                Expanded(child: Text(n.title, style: LsType.head(context, size: 14.5))),
                LsStatusChip(label: scopeName, color: cs.secondary),
              ]),
              const SizedBox(height: 8),
              Text(n.body,
                  style: TextStyle(fontSize: 13, height: 1.42, color: cs.onSurfaceVariant)),
              const SizedBox(height: 10),
              Row(children: [
                Icon(Icons.person_outline_rounded, size: 13, color: cs.outline),
                const SizedBox(width: 5),
                Expanded(
                  child: Text(n.postedBy?.displayName ?? '—',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 11.5, color: cs.outline)),
                ),
                if (n.createdAt != null)
                  Text(_ago(n.createdAt!, l10n),
                      style: TextStyle(fontSize: 11.5, color: cs.outline)),
              ]),
            ]),
          ),
        );
      },
    );
  }

  static String _ago(DateTime d, AppLocalizations l10n) {
    final diff = DateTime.now().difference(d);
    if (diff.inMinutes < 60) return l10n.timeMinutesAgo(diff.inMinutes);
    if (diff.inHours < 24) return l10n.timeHoursAgo(diff.inHours);
    return l10n.timeDaysAgo(diff.inDays);
  }
}

// ------------------------------------------------------------
// Compose sheet
// ------------------------------------------------------------

class _ComposeSheet extends StatefulWidget {
  final CampusAccess access;
  final Map<String, String> sectionLabels;

  const _ComposeSheet({required this.access, required this.sectionLabels});

  @override
  State<_ComposeSheet> createState() => _ComposeSheetState();
}

class _ComposeSheetState extends State<_ComposeSheet> {
  final _title = TextEditingController();
  final _body = TextEditingController();

  /// null = poore campus ka notice. Sirf management ke liye available.
  String? _sectionId;
  bool _pin = false;
  bool _sending = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    // Class teacher (jo management nahi hai) ka default apni pehli section.
    // Uske paas "poore campus" ka option hai hi nahi — backend 403 dega.
    if (!widget.access.canPostCampusNotice &&
        widget.access.classTeacherSectionIds.isNotEmpty) {
      _sectionId = widget.access.classTeacherSectionIds.first;
    }
  }

  @override
  void dispose() {
    _title.dispose();
    _body.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final session = widget.access.currentSession;
    final l10n = AppLocalizations.of(context)!;

    if (session == null) {
      setState(() => _error = l10n.noticeNoSessionError);
      return;
    }
    if (_title.text.trim().isEmpty || _body.text.trim().isEmpty) {
      setState(() => _error = l10n.noticeEmptyError);
      return;
    }

    setState(() {
      _sending = true;
      _error = null;
    });
    try {
      await CampusService.postNotice(
        campusId: widget.access.campus.id,
        sessionId: session.id,
        title: _title.text.trim(),
        body: _body.text.trim(),
        sectionId: _sectionId,
        pinUntil: _pin ? DateTime.now().add(const Duration(days: 7)) : null,
      );
      if (mounted) Navigator.pop(context, true);
    } on CampusApiException catch (e) {
      if (mounted) {
        setState(() {
          _sending = false;
          _error = e.message;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    // Kis-kis scope pe post kar sakta hoon — management ko campus + har
    // section, class teacher ko sirf apni sections.
    final options = <DropdownMenuItem<String?>>[
      if (widget.access.canPostCampusNotice)
        DropdownMenuItem(value: null, child: Text(l10n.noticeScopeWholeCampus)),
      ...(widget.access.canPostCampusNotice
              ? widget.sectionLabels.keys
              : widget.access.classTeacherSectionIds)
          .map((id) => DropdownMenuItem<String?>(
                value: id,
                child: Text(widget.sectionLabels[id] ?? l10n.noticeScopeSection),
              )),
    ];

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
            decoration: BoxDecoration(
                color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
          ),
          const SizedBox(height: 16),
          Align(
            alignment: Alignment.centerLeft,
            child: Text(l10n.campusPostNotice, style: LsType.head(context, size: 16)),
          ),
          const SizedBox(height: 16),
          DropdownButtonFormField<String?>(
            value: _sectionId,
            items: options,
            isExpanded: true,
            decoration: InputDecoration(
              labelText: l10n.noticeAudienceLabel,
              border: const OutlineInputBorder(),
            ),
            onChanged: _sending ? null : (v) => setState(() => _sectionId = v),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _title,
            enabled: !_sending,
            textCapitalization: TextCapitalization.sentences,
            decoration: InputDecoration(
              labelText: l10n.noticeTitleLabel,
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _body,
            enabled: !_sending,
            maxLines: 5,
            textCapitalization: TextCapitalization.sentences,
            decoration: InputDecoration(
              labelText: l10n.noticeBodyLabel,
              alignLabelWithHint: true,
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 6),
          SwitchListTile(
            value: _pin,
            onChanged: _sending ? null : (v) => setState(() => _pin = v),
            contentPadding: EdgeInsets.zero,
            title: Text(l10n.noticePinLabel, style: const TextStyle(fontSize: 13.5)),
            subtitle: Text(l10n.noticePinHint,
                style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
          ),
          if (_error != null) ...[
            const SizedBox(height: 6),
            Align(
              alignment: Alignment.centerLeft,
              child: Text(_error!, style: TextStyle(color: cs.error, fontSize: 12.5)),
            ),
          ],
          const SizedBox(height: 14),
          LsPrimaryButton(
            label: l10n.noticeSendLabel,
            icon: Icons.send_rounded,
            loading: _sending,
            onPressed: _sending ? null : _send,
          ),
        ]),
      ),
    );
  }
}
