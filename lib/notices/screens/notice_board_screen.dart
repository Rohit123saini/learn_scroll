import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/notice_board_models.dart';
import '../services/notice_board_service.dart';

// ============================================================
// HOME — NOTICE BOARD (Task 12)
//
// `home.dart`'s "Notices" quick-action tile used to be a bare
// "coming soon" snackbar. This screen is a read-only, MERGED feed of
// both notice sources that already existed elsewhere in the app —
// campus notices (`campus/screens/notices_screen.dart`, scoped to one
// campus at a time) and tuition-class notices (per classroom) — so the
// user doesn't have to open Campus and every classroom separately just
// to see what's new. Posting still happens from those existing
// screens; this is purely "everything in one place".
// ============================================================

class NoticeBoardScreen extends StatefulWidget {
  const NoticeBoardScreen({super.key});

  @override
  State<NoticeBoardScreen> createState() => _NoticeBoardScreenState();
}

class _NoticeBoardScreenState extends State<NoticeBoardScreen> {
  bool _loading = true;
  String? _error;
  List<NoticeBoardItem> _notices = const [];

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
      final page = await NoticeBoardService.fetch();
      if (!mounted) return;
      setState(() {
        _notices = page.results;
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
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.noticeBoardTitle, style: LsType.head(context, size: 15)),
      ),
      body: RefreshIndicator(onRefresh: _load, child: _body(cs, l10n)),
    );
  }

  Widget _body(ColorScheme cs, AppLocalizations l10n) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 92),
        SizedBox(height: 10),
        LsSkeletonBox(height: 92),
        SizedBox(height: 10),
        LsSkeletonBox(height: 92),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(
          title: l10n.noticeBoardLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _load,
        ),
      ]);
    }
    if (_notices.isEmpty) {
      return ListView(children: [
        const SizedBox(height: 60),
        EmptyStateWidget(
          icon: Icons.campaign_outlined,
          title: l10n.noticeBoardEmpty,
          subtitle: l10n.noticeBoardEmptySubtitle,
        ),
      ]);
    }

    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 24),
      itemCount: _notices.length,
      itemBuilder: (_, i) {
        final n = _notices[i];
        final isLive = n.source == NoticeBoardSource.tuitionClass;
        final sourceLabel = isLive ? l10n.noticeBoardSourceTuitionClass : l10n.noticeBoardSourceCampus;
        final scopeSuffix = switch (n.scopeLabel) {
          'section' => ' · ${l10n.noticeScopeSection}',
          'class' => ' · ${l10n.noticeScopeClass}',
          'department' => ' · ${l10n.noticeScopeDepartment}',
          _ => '',
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
                LsStatusChip(
                  label: sourceLabel,
                  color: isLive ? cs.tertiary : cs.secondary,
                ),
              ]),
              const SizedBox(height: 6),
              Text(
                '${n.contextLabel}$scopeSuffix',
                style: TextStyle(fontSize: 11.5, color: cs.outline, fontWeight: FontWeight.w600),
              ),
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
