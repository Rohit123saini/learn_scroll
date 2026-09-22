import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';
import 'ts_status.dart';

/// List card. Detail screen bhi iske chhote pieces reuse karti hai.
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
    final source = series.sourceType;

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
          TsMetaBit(icon: Icons.workspace_premium_outlined, text: l10n.testSeriesMarks(series.totalMarks)),
          if (series.durationMinutes != null)
            TsMetaBit(icon: Icons.timer_outlined, text: l10n.testSeriesDuration(series.durationMinutes!)),
          if (series.attemptsAllowed > 1)
            TsMetaBit(icon: Icons.replay_rounded, text: l10n.testSeriesAttempts(series.attemptsAllowed)),
          TsRatingBit(series: series),
        ]),
        const SizedBox(height: 12),
        Row(children: [
          Expanded(
            child: Wrap(spacing: 6, runSpacing: 6, children: [
              if (source != TsSource.unknown)
                LsStatusChip(label: tsSourceLabel(l10n, source), color: tsSourceColor(context, source)),
              if (attempt != null)
                LsStatusChip(
                  label: tsStatusLabel(l10n, attempt!.status),
                  color: tsStatusColor(context, attempt!.status),
                ),
            ]),
          ),
          const SizedBox(width: 8),
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

class TsMetaBit extends StatelessWidget {
  final IconData icon;
  final String text;
  const TsMetaBit({super.key, required this.icon, required this.text});

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

class TsRatingBit extends StatelessWidget {
  final TestSeriesModel series;
  const TsRatingBit({super.key, required this.series});

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
