import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../controllers/attempt_controller.dart';

/// Question palette: answered / not answered / marked-for-review / current.
/// 100+ questions par bhi scroll hota hai.
Future<void> showTsPaletteSheet({
  required BuildContext context,
  required TestAttemptController controller,
  required ValueChanged<int> onSelect,
}) {
  final cs = Theme.of(context).colorScheme;
  final l10n = AppLocalizations.of(context)!;
  final t = lsTokens(context);
  final maxH = MediaQuery.of(context).size.height * 0.75;

  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: cs.surface,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: maxH),
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(kLsPad),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.testPaletteTitle, style: LsType.head(context, size: 14)),
            const SizedBox(height: 4),
            Text(l10n.testAnsweredOf(controller.answeredCount, controller.answerableCount),
                style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
            const SizedBox(height: 12),
            Wrap(spacing: 14, runSpacing: 6, children: [
              _Legend(color: cs.primary, label: l10n.tsLegendAnswered),
              _Legend(color: cs.surfaceVariant, label: l10n.tsLegendNotAnswered, border: cs.outlineVariant),
              _Legend(color: t.warning, label: l10n.tsLegendMarked),
            ]),
            const SizedBox(height: 14),
            Wrap(
              spacing: 9,
              runSpacing: 9,
              children: List.generate(controller.questions.length, (i) {
                final q = controller.questions[i];
                final answered = controller.isAnswered(q);
                final marked = controller.isMarked(q.id);
                final current = i == controller.index;
                final bg = answered ? cs.primary : cs.surfaceVariant;
                final fg = answered ? cs.onPrimary : cs.onSurface;
                return Semantics(
                  button: true,
                  selected: current,
                  label:
                      '${i + 1}, ${answered ? l10n.tsLegendAnswered : l10n.tsLegendNotAnswered}${marked ? ', ${l10n.tsLegendMarked}' : ''}',
                  excludeSemantics: true,
                  child: GestureDetector(
                    onTap: () {
                      Navigator.pop(ctx);
                      onSelect(i);
                    },
                    child: Stack(clipBehavior: Clip.none, children: [
                      Container(
                        width: 44,
                        height: 44,
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                          color: bg,
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(color: current ? cs.secondary : Colors.transparent, width: 2),
                        ),
                        child: Text('${i + 1}',
                            style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: fg)),
                      ),
                      if (marked)
                        Positioned(
                          top: -3,
                          right: -3,
                          child: Container(
                            width: 14,
                            height: 14,
                            decoration: BoxDecoration(
                              color: t.warning,
                              shape: BoxShape.circle,
                              border: Border.all(color: cs.surface, width: 2),
                            ),
                          ),
                        ),
                    ]),
                  ),
                );
              }),
            ),
            const SizedBox(height: 16),
          ]),
        ),
      ),
    ),
  );
}

class _Legend extends StatelessWidget {
  final Color color;
  final Color? border;
  final String label;
  const _Legend({required this.color, required this.label, this.border});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Container(
        width: 12,
        height: 12,
        decoration: BoxDecoration(
          color: color,
          borderRadius: BorderRadius.circular(4),
          border: border == null ? null : Border.all(color: border!),
        ),
      ),
      const SizedBox(width: 6),
      Text(label, style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
    ]);
  }
}
