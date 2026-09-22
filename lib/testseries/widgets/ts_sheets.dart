import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_service.dart';
import '../utils/ts_error_text.dart';

// ============================================================
// Result screen ke do bottom sheets: review likho, doubt poochho.
// Dono `true` return karte hain agar server ne accept kiya.
// ============================================================

Future<bool> showTsReviewSheet(BuildContext context, {required String seriesId}) async {
  final cs = Theme.of(context).colorScheme;
  final l10n = AppLocalizations.of(context)!;
  final commentCtrl = TextEditingController();
  int rating = 5;
  bool sending = false;
  bool done = false;

  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: cs.surface,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setSheet) => Padding(
        padding: EdgeInsets.only(
          left: kLsPad,
          right: kLsPad,
          top: 18,
          bottom: MediaQuery.of(ctx).viewInsets.bottom + 18,
        ),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l10n.testRateTitle, style: LsType.head(context, size: 15)),
          const SizedBox(height: 4),
          Text(l10n.testRateSubtitle, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
          const SizedBox(height: 14),
          Row(
            children: List.generate(5, (i) {
              final on = i < rating;
              return IconButton(
                tooltip: '${i + 1}',
                onPressed: sending
                    ? null
                    : () {
                        HapticFeedback.selectionClick();
                        setSheet(() => rating = i + 1);
                      },
                icon: Icon(on ? Icons.star_rounded : Icons.star_outline_rounded,
                    size: 30, color: lsTokens(context).warning),
              );
            }),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: commentCtrl,
            maxLines: 4,
            minLines: 2,
            textCapitalization: TextCapitalization.sentences,
            style: TextStyle(fontSize: 13, color: cs.onSurface),
            decoration: InputDecoration(hintText: l10n.testReviewHint),
          ),
          const SizedBox(height: 14),
          LsPrimaryButton(
            label: l10n.testSubmitReview,
            icon: Icons.send_rounded,
            loading: sending,
            onPressed: sending
                ? null
                : () async {
                    setSheet(() => sending = true);
                    try {
                      await TestSeriesService.createReview(
                        seriesId: seriesId,
                        rating: rating,
                        comment: commentCtrl.text.trim(),
                      );
                      done = true;
                      if (ctx.mounted) Navigator.pop(ctx);
                    } catch (e) {
                      // Backend ka message (e.g. "already reviewed") tsErrorMessage
                      // validation/conflict me wahi dikhata hai.
                      setSheet(() => sending = false);
                      if (ctx.mounted) lsSnack(ctx, tsErrorMessage(l10n, e), error: true);
                    }
                  },
          ),
        ]),
      ),
    ),
  );
  commentCtrl.dispose();
  return done;
}

Future<bool> showTsQuerySheet(BuildContext context, {required String attemptId}) async {
  final cs = Theme.of(context).colorScheme;
  final l10n = AppLocalizations.of(context)!;
  final ctrl = TextEditingController();
  bool anonymous = false;
  bool sending = false;
  bool done = false;

  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: cs.surface,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setSheet) => Padding(
        padding: EdgeInsets.only(
          left: kLsPad,
          right: kLsPad,
          top: 18,
          bottom: MediaQuery.of(ctx).viewInsets.bottom + 18,
        ),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l10n.testAskQueryTitle, style: LsType.head(context, size: 15)),
          const SizedBox(height: 4),
          Text(l10n.testAskQuerySubtitle, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
          const SizedBox(height: 14),
          TextField(
            controller: ctrl,
            maxLines: 5,
            minLines: 3,
            textCapitalization: TextCapitalization.sentences,
            style: TextStyle(fontSize: 13, color: cs.onSurface),
            decoration: InputDecoration(hintText: l10n.testQueryHint),
          ),
          const SizedBox(height: 6),
          Row(children: [
            Checkbox(
              value: anonymous,
              onChanged: sending ? null : (v) => setSheet(() => anonymous = v ?? false),
            ),
            Expanded(
              child: Text(l10n.testQueryAnonymous, style: TextStyle(fontSize: 12, color: cs.onSurface)),
            ),
          ]),
          const SizedBox(height: 8),
          LsPrimaryButton(
            label: l10n.testSendQuery,
            icon: Icons.send_rounded,
            loading: sending,
            onPressed: sending
                ? null
                : () async {
                    if (ctrl.text.trim().isEmpty) {
                      lsSnack(ctx, l10n.testQueryEmpty, error: true);
                      return;
                    }
                    setSheet(() => sending = true);
                    try {
                      await TestSeriesService.askQuery(
                        attemptId: attemptId,
                        text: ctrl.text.trim(),
                        isAnonymous: anonymous,
                      );
                      done = true;
                      if (ctx.mounted) Navigator.pop(ctx);
                    } catch (e) {
                      setSheet(() => sending = false);
                      if (ctx.mounted) lsSnack(ctx, tsErrorMessage(l10n, e), error: true);
                    }
                  },
          ),
        ]),
      ),
    ),
  );
  ctrl.dispose();
  return done;
}
