import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';

// ============================================================
// Shared status / source helpers (list, detail, result teeno use karte hain).
// ============================================================

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

String tsSourceLabel(AppLocalizations l10n, TsSource source) {
  switch (source) {
    case TsSource.individual:
      return l10n.tsSourceIndividual;
    case TsSource.campus:
      return l10n.tsSourceCampus;
    case TsSource.liveclass:
      return l10n.tsSourceLiveClass;
    case TsSource.unknown:
      return l10n.tsSourceIndividual;
  }
}

Color tsSourceColor(BuildContext context, TsSource source) {
  final t = lsTokens(context);
  final cs = Theme.of(context).colorScheme;
  switch (source) {
    case TsSource.campus:
      return t.info;
    case TsSource.liveclass:
      return t.danger;
    case TsSource.individual:
    case TsSource.unknown:
      return cs.primary;
  }
}

/// Selectable chip row (source tabs). `LsFilterChips` 3 labels ke liye bana
/// hai; 4 labels chhoti screen pe overflow na ho isliye ye horizontally scroll hota hai.
class TsChipRow extends StatelessWidget {
  final List<String> labels;
  final int selectedIndex;
  final ValueChanged<int> onSelected;

  const TsChipRow({super.key, required this.labels, required this.selectedIndex, required this.onSelected});

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: kLsPad),
      child: Row(
        children: [
          for (int i = 0; i < labels.length; i++)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: ChoiceChip(
                label: Text(labels[i]),
                selected: i == selectedIndex,
                onSelected: (_) => onSelected(i),
              ),
            ),
        ],
      ),
    );
  }
}
