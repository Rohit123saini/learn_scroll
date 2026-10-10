import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:image_picker/image_picker.dart';

import '../message/services/ai_study_service.dart';
import 'forward_doubt_sheet.dart';
import 'ls_ui.dart';
import 'study_buddy_result.dart';

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
  // Study Buddy chips ke liye — post ka PDF attachment (agar hai). Chip tap
  // par hi download hota hai (max 10 MB), pehle nahi.
  String? pdfUrl,
}) async {
  final cs = Theme.of(context).colorScheme;
  final ctrl = TextEditingController(text: initialQuestion);
  bool sending = false;
  String? answer;
  String? error;
  // Photo doubt: student sawaal ki photo kheench/chun ke bhej sakta hai
  // (question text optional tab). Server step-by-step solution deta hai.
  Uint8List? photo;
  String photoName = 'doubt.jpg';
  final picker = ImagePicker();
  // AI Study Buddy chips (Explain simply / Hindi / Quiz / Flashcards).
  final bool buddyAvailable = contextText.trim().length >= 10 || pdfUrl != null;
  String buddyLang = 'en'; // quiz/flashcards isi language me bante hain
  bool buddyLoading = false;
  Map<String, dynamic>? buddy;
  Uint8List? pdfBytes; // pehli chip-tap par download, phir reuse

  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: cs.surface,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setSheet) {
        Future<void> pickPhoto(ImageSource source) async {
          try {
            final x = await picker.pickImage(source: source, maxWidth: 1600, imageQuality: 85);
            if (x == null) return;
            final bytes = await x.readAsBytes();
            setSheet(() {
              photo = bytes;
              photoName = x.name.isNotEmpty ? x.name : 'doubt.jpg';
              error = null;
            });
          } catch (_) {
            setSheet(() => error = 'Photo load nahi hui — permission check karke dobara try karo');
          }
        }

        Future<void> runBuddy(String mode, {String? lang}) async {
          setSheet(() {
            buddyLoading = true;
            error = null;
            if (lang != null) buddyLang = lang;
          });
          try {
            if (pdfUrl != null && pdfBytes == null) {
              final r = await http.get(Uri.parse(pdfUrl));
              if (r.statusCode != 200) throw Exception('PDF load nahi hui');
              if (r.bodyBytes.length > 10 * 1024 * 1024) {
                throw Exception('PDF 10 MB se badi hai — chhoti file ya text try karo');
              }
              pdfBytes = r.bodyBytes;
            }
            final result = await AiStudyService.studyBuddy(
              mode: mode,
              language: buddyLang,
              content: contextText,
              pdfBytes: pdfBytes,
            );
            setSheet(() {
              buddy = result;
              buddyLoading = false;
            });
          } catch (e) {
            setSheet(() {
              buddyLoading = false;
              error = e.toString().replaceFirst('Exception: ', '');
            });
          }
        }

        Widget buddyChip(String label, IconData icon, VoidCallback onTap) => ActionChip(
              avatar: Icon(icon, size: 16),
              label: Text(label, style: const TextStyle(fontSize: 12)),
              onPressed: buddyLoading ? null : onTap,
            );

        Future<void> send() async {
          final q = ctrl.text.trim();
          // Photo ho to question optional; warna text zaroori.
          if (q.isEmpty && photo == null) return;
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
              imageBytes: photo,
              imageName: photoName,
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
            if (buddy != null) ...[
              ConstrainedBox(
                constraints: BoxConstraints(maxHeight: MediaQuery.of(ctx).size.height * 0.5),
                child: SingleChildScrollView(child: StudyBuddyResult(data: buddy!)),
              ),
              const SizedBox(height: 12),
              Row(children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => setSheet(() => buddy = null),
                    child: const Text('Back'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(child: LsPrimaryButton(label: 'Done', onPressed: () => Navigator.pop(ctx))),
              ]),
            ] else if (answer == null) ...[
              if (buddyAvailable) ...[
                Wrap(spacing: 8, runSpacing: 4, children: [
                  buddyChip('Explain simply', Icons.lightbulb_outline_rounded, () => runBuddy('explain', lang: 'en')),
                  buddyChip('Hindi me samjhao', Icons.translate_rounded, () => runBuddy('explain', lang: 'hi')),
                  buddyChip('Quiz banao', Icons.quiz_outlined, () => runBuddy('quiz')),
                  buddyChip('5 flashcards', Icons.style_outlined, () => runBuddy('flashcards')),
                ]),
                Row(children: [
                  Text('Quiz/cards language:', style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
                  const SizedBox(width: 6),
                  for (final l in const [('en', 'English'), ('hi', 'हिन्दी'), ('hinglish', 'Hinglish')])
                    Padding(
                      padding: const EdgeInsets.only(right: 4),
                      child: ChoiceChip(
                        label: Text(l.$2, style: const TextStyle(fontSize: 11)),
                        selected: buddyLang == l.$1,
                        visualDensity: VisualDensity.compact,
                        onSelected: buddyLoading ? null : (_) => setSheet(() => buddyLang = l.$1),
                      ),
                    ),
                ]),
                if (buddyLoading)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 8),
                    child: LinearProgressIndicator(),
                  ),
                const SizedBox(height: 10),
              ],
              TextField(
                controller: ctrl,
                maxLines: 4,
                minLines: 2,
                autofocus: initialQuestion.isEmpty,
                textCapitalization: TextCapitalization.sentences,
                style: TextStyle(fontSize: 13, color: cs.onSurface),
                decoration: InputDecoration(
                  hintText: photo == null
                      ? 'What\'s your doubt?'
                      : 'Add a note (optional) — e.g. "step 3 samajh nahi aaya"',
                ),
              ),
              const SizedBox(height: 10),
              if (photo != null)
                Stack(children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(12),
                    child: Image.memory(photo!, height: 120, fit: BoxFit.cover),
                  ),
                  Positioned(
                    top: 4,
                    right: 4,
                    child: InkWell(
                      onTap: sending ? null : () => setSheet(() => photo = null),
                      child: const CircleAvatar(
                        radius: 12,
                        backgroundColor: Colors.black54,
                        child: Icon(Icons.close_rounded, size: 14, color: Colors.white),
                      ),
                    ),
                  ),
                ])
              else
                Row(children: [
                  OutlinedButton.icon(
                    onPressed: sending ? null : () => pickPhoto(ImageSource.camera),
                    icon: const Icon(Icons.photo_camera_rounded, size: 18),
                    label: const Text('Photo'),
                  ),
                  const SizedBox(width: 8),
                  OutlinedButton.icon(
                    onPressed: sending ? null : () => pickPhoto(ImageSource.gallery),
                    icon: const Icon(Icons.photo_library_rounded, size: 18),
                    label: const Text('Gallery'),
                  ),
                ]),
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
              const SizedBox(height: 8),
              // AI se bhi clear na ho to feed (top students) ya classroom
              // teacher ko forward — see forward_doubt_sheet.dart.
              TextButton.icon(
                onPressed: () => showForwardDoubtSheet(
                  ctx,
                  question: ctrl.text,
                  aiAnswer: answer ?? '',
                  photo: photo,
                  photoName: photoName,
                ),
                icon: const Icon(Icons.help_outline_rounded, size: 18),
                label: const Text('Abhi bhi samajh nahi aaya?'),
              ),
              const SizedBox(height: 4),
              Row(children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => setSheet(() {
                      answer = null;
                      photo = null;
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
