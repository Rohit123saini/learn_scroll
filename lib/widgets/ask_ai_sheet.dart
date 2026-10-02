import 'package:flutter/material.dart';

import '../message/services/ai_study_service.dart';
import 'ls_ui.dart';

// ============================================================
// 🔥 NAYA — Task G15 (growth_and_feature_tasks.md, Section E — "AI
// doubt-solving assistant"): "Ask AI" bottom sheet, reusable from
// anywhere in the app — feed post, a wrong test-series question, or
// chat — that has a doubt to explain.
//
// Kept deliberately generic (context_type + context_text + sourceId are
// just passed straight through to `AiStudyService.askDoubt`) so a new
// entry point elsewhere in the app is a one-line call to
// `showAskAiSheet(...)`, not a new screen/widget.
//
// NOTE: strings here are hardcoded (not `l10n.*`) — this feature was
// added standalone without touching the (very large) l10n .arb files;
// if/when this screen gets a proper localization pass, move these into
// app_en.arb / app_hi.arb the way the rest of the app does.
// ============================================================

/// Opens the "Ask AI" sheet. `initialQuestion` pre-fills the question
/// field (e.g. "Why is this wrong?" for a test question) so the student
/// can just tap send, but they can edit/replace it before sending.
/// `contextPreview` is shown above the input as a read-only card so the
/// student can see exactly what the AI will be looking at (a post
/// caption, the question + their answer + the correct answer, etc.).
Future<void> showAskAiSheet(
  BuildContext context, {
  required String contextType,
  String contextText = '',
  String? contextPreview,
  String initialQuestion = '',
  String? sourceId,
}) async {
  final cs = Theme.of(context).colorScheme;
  final ctrl = TextEditingController(text: initialQuestion);
  bool sending = false;
  String? answer;
  String? error;

  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: cs.surface,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setSheet) {
        Future<void> send() async {
          final q = ctrl.text.trim();
          if (q.isEmpty) return;
          setSheet(() {
            sending = true;
            error = null;
          });
          try {
            final result = await AiStudyService.askDoubt(
              question: q,
              contextType: contextType,
              contextText: contextText,
              sourceId: sourceId,
            );
            setSheet(() {
              answer = result;
              sending = false;
            });
          } catch (e) {
            setSheet(() {
              error = e.toString().replaceFirst('Exception: ', '');
              sending = false;
            });
          }
        }

        return Padding(
          padding: EdgeInsets.only(
            left: kLsPad,
            right: kLsPad,
            top: 18,
            bottom: MediaQuery.of(ctx).viewInsets.bottom + 18,
          ),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Icon(Icons.auto_awesome_rounded, size: 18, color: cs.primary),
              const SizedBox(width: 8),
              Text('Ask AI', style: LsType.head(context, size: 15)),
            ]),
            const SizedBox(height: 4),
            Text(
              'Get an instant explanation — no waiting for a reply.',
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
            ),
            if (contextPreview != null && contextPreview.isNotEmpty) ...[
              const SizedBox(height: 12),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(11),
                decoration: BoxDecoration(
                  color: cs.surfaceVariant,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  contextPreview,
                  maxLines: 4,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 12, height: 1.4, color: cs.onSurfaceVariant),
                ),
              ),
            ],
            const SizedBox(height: 14),
            if (answer == null) ...[
              TextField(
                controller: ctrl,
                maxLines: 4,
                minLines: 2,
                autofocus: initialQuestion.isEmpty,
                textCapitalization: TextCapitalization.sentences,
                style: TextStyle(fontSize: 13, color: cs.onSurface),
                decoration: const InputDecoration(hintText: 'What\'s your doubt?'),
              ),
              if (error != null) ...[
                const SizedBox(height: 8),
                Text(error!, style: TextStyle(fontSize: 12, color: lsTokens(context).danger)),
              ],
              const SizedBox(height: 12),
              LsPrimaryButton(
                label: 'Ask AI',
                icon: Icons.auto_awesome_rounded,
                loading: sending,
                onPressed: sending ? null : send,
              ),
            ] else ...[
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: cs.primary.withOpacity(.08),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(answer!, style: TextStyle(fontSize: 13, height: 1.5, color: cs.onSurface)),
              ),
              const SizedBox(height: 12),
              Row(children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => setSheet(() {
                      answer = null;
                      ctrl.clear();
                    }),
                    child: const Text('Ask another doubt'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: LsPrimaryButton(label: 'Done', onPressed: () => Navigator.pop(ctx)),
                ),
              ]),
            ],
          ]),
        );
      },
    ),
  );
  ctrl.dispose();
}
