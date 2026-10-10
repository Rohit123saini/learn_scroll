import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../message/services/doubts_api_service.dart';
import '../post/services/api_service.dart';
import 'ls_ui.dart';

// ============================================================
// Doubt Solver — "Abhi bhi samajh nahi aaya?" forward sheet.
//
// AI ke jawab se bhi doubt clear na ho to student isi sheet se apna
// doubt aage bhej sakta hai, do jagah:
//   1. Feed pe doubt post (post_type 'doubt') — top students/teachers
//      answer karte hain, asker "best answer" pin kar sakta hai.
//      Photo ho to post ke saath attach jaati hai.
//   2. Apne classroom/group ki Doubt Queue me — teacher answer karta hai.
//      Queue text-only hai, isliye photo nahi jaati; sawaal ka text bhejte
//      hain (student ka note, ya AI ne photo se jo "Question:" padha tha).
//
// Koi naya backend endpoint nahi — dono existing APIs hain:
//   POST /post/create/                        (ApiService.createPost)
//   POST /message/groups/<id>/doubts/         (DoubtsApiService.createDoubt)
//
// NOTE: strings hardcoded hain (ask_ai_sheet.dart jaisa) — l10n pass me
// move karna.
// ============================================================

/// `question` — student ka likha sawaal (khaali ho sakta hai agar sirf photo
/// bheji thi). `aiAnswer` — AI ka jawab (sirf "Question:" line nikalne ke
/// kaam aata hai jab student ne text nahi likha). `photo` — original photo.
Future<void> showForwardDoubtSheet(
  BuildContext context, {
  required String question,
  String aiAnswer = '',
  Uint8List? photo,
  String photoName = 'doubt.jpg',
}) async {
  final cs = Theme.of(context).colorScheme;
  final text = buildForwardDoubtText(question: question, aiAnswer: aiAnswer);

  bool busy = false;
  bool pickingGroup = false;
  bool anonymous = false;
  String? error;
  String? doneMessage;
  List<Map<String, dynamic>> groups = [];

  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: cs.surface,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setSheet) {
        Future<void> postToFeed() async {
          setSheet(() {
            busy = true;
            error = null;
          });
          File? tmp;
          try {
            if (photo != null) {
              tmp = File('${Directory.systemTemp.path}/doubt_${DateTime.now().millisecondsSinceEpoch}.jpg');
              await tmp.writeAsBytes(photo);
            }
            await ApiService().createPost(
              content: text,
              category: 'education',
              postType: 'doubt',
              mediaFiles: tmp != null ? [tmp] : null,
              mediaTypes: tmp != null ? ['image'] : null,
            );
            setSheet(() {
              busy = false;
              doneMessage = 'Doubt feed pe post ho gaya — jawab aate hi notification milega.';
            });
          } catch (e) {
            setSheet(() {
              busy = false;
              error = e.toString().replaceFirst('Exception: ', '');
            });
          } finally {
            try {
              await tmp?.delete();
            } catch (_) {}
          }
        }

        Future<void> openGroupPicker() async {
          setSheet(() {
            busy = true;
            error = null;
          });
          try {
            final g = await DoubtsApiService.listMyGroups();
            setSheet(() {
              groups = g;
              pickingGroup = true;
              busy = false;
              if (g.isEmpty) error = 'Aap abhi kisi classroom/group me nahi ho.';
            });
          } catch (e) {
            setSheet(() {
              busy = false;
              error = e.toString().replaceFirst('Exception: ', '');
            });
          }
        }

        Future<void> sendToGroup(Map<String, dynamic> g) async {
          setSheet(() {
            busy = true;
            error = null;
          });
          try {
            await DoubtsApiService.createDoubt(
              g['id'].toString(),
              text: text,
              isAnonymous: anonymous,
            );
            setSheet(() {
              busy = false;
              doneMessage = '"${g['name'] ?? 'Classroom'}" ki Doubt Queue me bhej diya — teacher jawab dega.';
            });
          } catch (e) {
            setSheet(() {
              busy = false;
              error = e.toString().replaceFirst('Exception: ', '');
            });
          }
        }

        Widget body;
        if (doneMessage != null) {
          body = Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Icon(Icons.check_circle_rounded, color: cs.primary),
              const SizedBox(width: 8),
              Expanded(child: Text(doneMessage!, style: TextStyle(fontSize: 13, color: cs.onSurface))),
            ]),
            const SizedBox(height: 14),
            LsPrimaryButton(label: 'Done', onPressed: () => Navigator.pop(ctx)),
          ]);
        } else if (pickingGroup && groups.isNotEmpty) {
          body = Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Kaunsi classroom/group me bhejna hai?',
                style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              dense: true,
              title: const Text('Anonymously poocho', style: TextStyle(fontSize: 13)),
              subtitle: const Text('Classmates ko naam nahi dikhega (teacher dekh sakta hai)',
                  style: TextStyle(fontSize: 11)),
              value: anonymous,
              onChanged: busy ? null : (v) => setSheet(() => anonymous = v),
            ),
            ConstrainedBox(
              constraints: BoxConstraints(maxHeight: MediaQuery.of(ctx).size.height * 0.35),
              child: ListView(
                shrinkWrap: true,
                children: [
                  for (final g in groups)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.groups_rounded),
                      title: Text((g['name'] ?? 'Group').toString(), maxLines: 1, overflow: TextOverflow.ellipsis),
                      enabled: !busy,
                      onTap: () => sendToGroup(g),
                    ),
                ],
              ),
            ),
          ]);
        } else {
          body = Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(11),
              decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(12)),
              child: Text(
                text,
                maxLines: 4,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12, height: 1.4, color: cs.onSurfaceVariant),
              ),
            ),
            if (photo != null) ...[
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: Image.memory(photo, height: 80, fit: BoxFit.cover),
              ),
            ],
            const SizedBox(height: 14),
            LsPrimaryButton(
              label: 'Feed pe top students se poocho',
              icon: Icons.forum_rounded,
              loading: busy,
              onPressed: busy ? null : postToFeed,
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: busy ? null : openGroupPicker,
              icon: const Icon(Icons.school_rounded, size: 18),
              label: const Text('Apne teacher / classroom ko bhejo'),
            ),
            if (photo != null)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  'Note: classroom Doubt Queue text-only hai — photo ka sawaal text me jaayega.',
                  style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant),
                ),
              ),
          ]);
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
              Icon(Icons.help_outline_rounded, size: 18, color: cs.primary),
              const SizedBox(width: 8),
              Text('Abhi bhi samajh nahi aaya?', style: LsType.head(context, size: 15)),
            ]),
            const SizedBox(height: 12),
            body,
            if (error != null) ...[
              const SizedBox(height: 8),
              Text(error!, style: TextStyle(fontSize: 12, color: lsTokens(context).danger)),
            ],
          ]),
        );
      },
    ),
  );
}

/// Forward kiye jaane wale doubt ka text. Student ne likha ho to wahi;
/// warna AI ke jawab ki "Question:" line (photo se padha hua sawaal).
/// Aakhir me ek chhota note, taaki answerers ko pata ho AI try ho chuka hai.
/// Queue ki limit (2000 chars) ke andar rakha gaya hai.
String buildForwardDoubtText({required String question, String aiAnswer = ''}) {
  var q = question.trim();
  // Photo-only doubt ka default prompt student ka likha hua sawaal nahi hai.
  if (q == 'Solve this step by step.') q = '';
  if (q.isEmpty) {
    final m = RegExp(r'^\s*Question:\s*(.+)$', multiLine: true).firstMatch(aiAnswer);
    q = m?.group(1)?.trim() ?? '';
  }
  if (q.isEmpty) q = 'Mujhe is sawaal me help chahiye (photo dekho).';
  const note = '\n\n(AI se try kiya tha, par abhi bhi clear nahi hua.)';
  if (q.length > 1900) q = q.substring(0, 1900);
  return '$q$note';
}
