import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import 'test_series_detail_screen.dart';

// ============================================================
// TEST SERIES — LIST SCREEN
//
// Home ke quick-action "Test Series" tile aur feed interstitial ke
// "Start Test Series" button, dono yahin aate hain.
//
// Series aur meri attempts dono parallel fetch hoti hain, taaki har card
// seedha sahi CTA dikha sake: Start / Resume / View result — user ko
// andar jaake pata na chale ki uska attempt already chal raha tha.
// ============================================================

class TestSeriesScreen extends StatefulWidget {
  const TestSeriesScreen({super.key});
  @override
  State<TestSeriesScreen> createState() => _TestSeriesScreenState();
}

class _TestSeriesScreenState extends State<TestSeriesScreen> {
  List<TestSeriesModel> _series = [];
  Map<String, TestAttemptModel> _attemptBySeries = {};
  bool _loading = true;
  bool _failed = false;
  int _filter = 0; // 0 all, 1 free, 2 paid

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (mounted) {
      setState(() {
        _loading = true;
        _failed = false;
      });
    }
    try {
      final results = await Future.wait([
        TestSeriesService.listSeries(),
        // Attempts fail ho jaayein to series phir bhi dikhni chahiye —
        // CTA tab "Start" par default kar jaata hai.
        TestSeriesService.listMyAttempts().catchError((_) => <TestAttemptModel>[]),
      ]);
      final series = results[0] as List<TestSeriesModel>;
      final attempts = results[1] as List<TestAttemptModel>;

      final map = <String, TestAttemptModel>{};
      for (final a in attempts) {
        final existing = map[a.seriesId];
        // Multi-attempt allowed hai, isliye sabse latest attempt_number
        // wali attempt hi card pe dikhni chahiye.
        if (existing == null || a.attemptNumber > existing.attemptNumber) {
          map[a.seriesId] = a;
        }
      }

      if (!mounted) return;
      setState(() {
        _series = series;
        _attemptBySeries = map;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _failed = _series.isEmpty;
      });
    }
  }

  List<TestSeriesModel> get _visible {
    // Draft series list me nahi aani chahiye — backend apne drafts bhi
    // return karta hai (creator ke liye), par student ke browse view me
    // wo attempt karne layak nahi hai.
    final published = _series.where((s) => !s.isDraft);
    switch (_filter) {
      case 1:
        return published.where((s) => !s.isPaid).toList();
      case 2:
        return published.where((s) => s.isPaid).toList();
      default:
        return published.toList();
    }
  }

  Future<void> _open(TestSeriesModel s) async {
    HapticFeedback.selectionClick();
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => TestSeriesDetailScreen(seriesId: s.id, initialSeries: s),
      ),
    );
    if (mounted) _load();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: l10n.testSeries),
      body: Column(children: [
        LsFilterChips(
          labels: [l10n.testSeriesTabAll, l10n.testSeriesTabFree, l10n.testSeriesTabPaid],
          selectedIndex: _filter,
          onSelected: (i) {
            HapticFeedback.selectionClick();
            setState(() => _filter = i);
          },
        ),
        const SizedBox(height: 6),
        Expanded(child: _buildBody(cs, l10n)),
      ]),
    );
  }

  Widget _buildBody(ColorScheme cs, AppLocalizations l10n) {
    if (_loading && _series.isEmpty) {
      return ListView.builder(
        padding: const EdgeInsets.only(top: 4, bottom: 24),
        itemCount: 4,
        itemBuilder: (_, __) => const LsPostCardSkeleton(sidePad: kLsPad),
      );
    }
    if (_failed && _series.isEmpty) {
      return ErrorStateWidget(
        title: l10n.testSeriesErrorTitle,
        subtitle: l10n.feedErrorSubtitle,
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    }

    final items = _visible;
    return RefreshIndicator(
      color: cs.primary,
      backgroundColor: cs.surface,
      onRefresh: _load,
      child: items.isEmpty
          ? ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              children: [
                const SizedBox(height: 40),
                EmptyStateWidget(
                  icon: Icons.fact_check_outlined,
                  title: l10n.testSeriesEmptyTitle,
                  subtitle: l10n.testSeriesEmptySubtitle,
                ),
              ],
            )
          : ListView.builder(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.only(top: 4, bottom: 28),
              itemCount: items.length,
              itemBuilder: (context, i) => TestSeriesCard(
                series: items[i],
                attempt: _attemptBySeries[items[i].id],
                onTap: () => _open(items[i]),
              ),
            ),
    );
  }
}

/// Detail screen bhi isi card ka header wala hissa reuse karta hai, isliye
/// public hai.
class TestSeriesCard extends StatelessWidget {
  final TestSeriesModel series;
  final TestAttemptModel? attempt;
  final VoidCallback onTap;

  const TestSeriesCard({super.key, required this.series, required this.attempt, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = lsTokens(context);
    final l10n = AppLocalizations.of(context)!;

    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 12),
      onTap: onTap,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: Text(series.title,
                maxLines: 2, overflow: TextOverflow.ellipsis, style: LsType.head(context, size: 13.5)),
          ),
          const SizedBox(width: 10),
          if (series.isPaid)
            LsStatusChip(
                label: l10n.testSeriesCoins(series.priceCoins), color: t.warning, icon: Icons.toll_rounded)
          else
            LsStatusChip(label: l10n.testSeriesFree, color: t.success),
        ]),
        if (series.creator.isNotEmpty) ...[
          const SizedBox(height: 3),
          Text(l10n.testSeriesBy(series.creator),
              maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
        ],
        const SizedBox(height: 10),
        Wrap(spacing: 14, runSpacing: 6, children: [
          _MetaBit(icon: Icons.workspace_premium_outlined, text: l10n.testSeriesMarks(series.totalMarks)),
          if (series.durationMinutes != null)
            _MetaBit(icon: Icons.timer_outlined, text: l10n.testSeriesDuration(series.durationMinutes!)),
          if (series.attemptsAllowed > 1)
            _MetaBit(icon: Icons.replay_rounded, text: l10n.testSeriesAttempts(series.attemptsAllowed)),
          _RatingBit(series: series),
        ]),
        const SizedBox(height: 12),
        Row(children: [
          if (attempt != null)
            LsStatusChip(
              label: tsStatusLabel(l10n, attempt!.status),
              color: tsStatusColor(context, attempt!.status),
            ),
          const Spacer(),
          Text(
            _ctaLabel(l10n),
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.primary),
          ),
          const SizedBox(width: 3),
          Icon(Icons.chevron_right_rounded, size: 17, color: cs.primary),
        ]),
      ]),
    );
  }

  String _ctaLabel(AppLocalizations l10n) {
    final a = attempt;
    if (a == null) return l10n.testSeriesStart;
    if (a.isInProgress) return l10n.testSeriesResume;
    return l10n.testSeriesViewResult;
  }
}

class _MetaBit extends StatelessWidget {
  final IconData icon;
  final String text;
  const _MetaBit({required this.icon, required this.text});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(icon, size: 13, color: cs.onSurfaceVariant),
      const SizedBox(width: 5),
      Text(text, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
    ]);
  }
}

class _RatingBit extends StatelessWidget {
  final TestSeriesModel series;
  const _RatingBit({required this.series});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = lsTokens(context);
    final l10n = AppLocalizations.of(context)!;
    // avgRating null = abhi koi review nahi (0 rating se alag cheez hai).
    if (series.avgRating == null) {
      return Text(l10n.testSeriesNoRatings, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant));
    }
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(Icons.star_rounded, size: 14, color: t.warning),
      const SizedBox(width: 4),
      Text(
        l10n.testSeriesRating(series.avgRating!.toStringAsFixed(1), series.reviewCount),
        style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant),
      ),
    ]);
  }
}

// ---------------- shared status helpers ----------------

Color tsStatusColor(BuildContext context, TsAttemptStatus status) {
  final t = lsTokens(context);
  final cs = Theme.of(context).colorScheme;
  switch (status) {
    case TsAttemptStatus.checked:
      return t.success;
    case TsAttemptStatus.partiallyChecked:
      return t.info;
    case TsAttemptStatus.submitted:
      return cs.primary;
    case TsAttemptStatus.inProgress:
      return t.warning;
    case TsAttemptStatus.unknown:
      return cs.onSurfaceVariant;
  }
}

String tsStatusLabel(AppLocalizations l10n, TsAttemptStatus status) {
  switch (status) {
    case TsAttemptStatus.checked:
      return l10n.testStatusChecked;
    case TsAttemptStatus.partiallyChecked:
      return l10n.testStatusPartiallyChecked;
    case TsAttemptStatus.submitted:
      return l10n.testStatusSubmitted;
    case TsAttemptStatus.inProgress:
      return l10n.testStatusInProgress;
    case TsAttemptStatus.unknown:
      return l10n.testStatusSubmitted;
  }
}
