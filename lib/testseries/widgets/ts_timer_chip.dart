// FontFeature — timer ke digits tabular rahein, warna har second pe width
// badalti hai aur ghadi "hilti" dikhti hai.
import 'dart:ui' show FontFeature;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../config/testseries_config.dart';
import '../utils/ts_format.dart';

/// AppBar ka countdown chip. Sirf ye chip har second rebuild hota hai —
/// poori screen nahi (ValueListenable).
class TsTimerChip extends StatelessWidget {
  final ValueListenable<Duration> remaining;
  const TsTimerChip({super.key, required this.remaining});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = lsTokens(context);
    final l10n = AppLocalizations.of(context)!;
    final dark = Theme.of(context).brightness == Brightness.dark;

    return ValueListenableBuilder<Duration>(
      valueListenable: remaining,
      builder: (context, left, _) {
        final low = left.inMinutes < TsConfig.lowTimeMinutes;
        final color = low ? t.danger : cs.primary;
        final text = tsFormatRemaining(left);
        return Center(
          child: Semantics(
            // Har second ka announce nahi — sirf ek stable label.
            label: l10n.tsSemanticsTimer(text),
            excludeSemantics: true,
            child: Container(
              margin: const EdgeInsets.only(right: 8),
              padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 6),
              decoration: BoxDecoration(
                color: color.withOpacity(dark ? .22 : .12),
                borderRadius: BorderRadius.circular(20),
              ),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(Icons.timer_outlined, size: 14, color: color),
                const SizedBox(width: 5),
                Text(text,
                    style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                        fontFeatures: const [FontFeature.tabularFigures()],
                        color: color)),
              ]),
            ),
          ),
        );
      },
    );
  }
}