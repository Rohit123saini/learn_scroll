import 'package:flutter/material.dart';

import '../services/study_profile_service.dart';

// ============================================================
// Study profile + Exam Mode sheet.
//
//  * Class, exam (JEE/NEET/Board/UPSC), focus subjects, exam date — feed inhe
//    dekh ke matching posts upar laata hai (backend: post/feed_exam.py,
//    feed_context.py). Sab optional.
//  * Exam Mode: feed sirf padhai-wale content tak simat jaata hai (education
//    posts, aapke subjects/exam tags, aapke class teachers ki posts). Exam date
//    nikalne par apne-aap band. Khaali feed aaye to matlab abhi koi study post
//    nahi mili — Exam Mode band karke normal feed dekh sakte ho.
//
// `onChanged` — save ke baad caller (profile / home) apna state refresh kare.
// NOTE: strings hardcoded (baaki streak/doubt sheets jaisa).
// ============================================================
Future<void> showStudyProfileSheet(BuildContext context, {VoidCallback? onChanged}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (_) => _StudyProfileSheet(onChanged: onChanged),
  );
}

const _classOptions = <(String, String)>[
  ('6', '6'), ('7', '7'), ('8', '8'), ('9', '9'), ('10', '10'), ('11', '11'), ('12', '12'),
  ('dropper', 'Dropper'), ('graduate', 'Graduate'),
];
const _examOptions = <(String, String)>[
  ('jee', 'JEE'), ('neet', 'NEET'), ('board', 'Board'), ('upsc', 'UPSC'), ('other', 'Other'),
];
const int _maxSubjects = 10;

class _StudyProfileSheet extends StatefulWidget {
  final VoidCallback? onChanged;
  const _StudyProfileSheet({this.onChanged});

  @override
  State<_StudyProfileSheet> createState() => _StudyProfileSheetState();
}

class _StudyProfileSheetState extends State<_StudyProfileSheet> {
  bool loading = true;
  bool saving = false;
  String? error;

  String examTarget = '';
  String classLevel = '';
  List<String> subjects = [];
  DateTime? examDate;
  bool examMode = false;
  bool examModeActive = false;
  final subjectCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    subjectCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final p = await StudyProfileService.fetch();
    if (!mounted) return;
    setState(() {
      loading = false;
      if (p != null) _apply(p);
    });
  }

  void _apply(StudyProfile p) {
    examTarget = p.examTarget;
    classLevel = p.classLevel;
    subjects = List.of(p.focusSubjects);
    examDate = p.examDate;
    examMode = p.examMode;
    examModeActive = p.examModeActive;
  }

  void _addSubject() {
    final s = subjectCtrl.text.trim();
    if (s.isEmpty) return;
    if (subjects.length >= _maxSubjects) {
      setState(() => error = 'Maximum $_maxSubjects subjects.');
      return;
    }
    if (subjects.any((e) => e.toLowerCase() == s.toLowerCase())) {
      subjectCtrl.clear();
      return;
    }
    setState(() {
      subjects.add(s);
      subjectCtrl.clear();
      error = null;
    });
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: examDate != null && !examDate!.isBefore(DateTime(now.year, now.month, now.day)) ? examDate! : now,
      firstDate: DateTime(now.year, now.month, now.day),
      lastDate: DateTime(now.year + 3),
    );
    if (picked != null && mounted) setState(() => examDate = picked);
  }

  String _iso(DateTime d) => '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  Future<void> _save() async {
    // Typed-but-not-added subject ko bhi le lo.
    if (subjectCtrl.text.trim().isNotEmpty) _addSubject();
    setState(() {
      saving = true;
      error = null;
    });
    final r = await StudyProfileService.save({
      'exam_target': examTarget,
      'class_level': classLevel,
      'focus_subjects': subjects,
      'exam_date': examDate == null ? null : _iso(examDate!),
      'exam_mode': examMode,
    });
    if (!mounted) return;
    if (r.ok) {
      widget.onChanged?.call();
      Navigator.pop(context);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(r.profile!.examModeActive
            ? '📚 Exam Mode on — pull-to-refresh karke study feed dekho.'
            : 'Study profile save ho gaya — feed refresh par asar dikhega.'),
      ));
    } else {
      setState(() {
        saving = false;
        error = r.error;
      });
    }
  }

  Widget _chips(String selected, List<(String, String)> options, ValueChanged<String> onPick) => Wrap(
        spacing: 6,
        runSpacing: 0,
        children: [
          for (final o in options)
            ChoiceChip(
              label: Text(o.$2, style: const TextStyle(fontSize: 12)),
              selected: selected == o.$1,
              visualDensity: VisualDensity.compact,
              // Dobara tap karne par unselect (optional field).
              onSelected: (_) => onPick(selected == o.$1 ? '' : o.$1),
            ),
        ],
      );

  Widget _label(ColorScheme cs, String t) => Padding(
        padding: const EdgeInsets.only(top: 14, bottom: 6),
        child: Text(t, style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant)),
      );

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    if (loading) {
      return const SizedBox(height: 220, child: Center(child: CircularProgressIndicator()));
    }
    final dateLabel = examDate == null
        ? 'Exam date set karo (optional)'
        : '${examDate!.day}/${examDate!.month}/${examDate!.year}';

    return SafeArea(
      child: SingleChildScrollView(
        padding: EdgeInsets.fromLTRB(20, 20, 20, 20 + MediaQuery.of(context).viewInsets.bottom),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              const Text('🎯', style: TextStyle(fontSize: 20)),
              const SizedBox(width: 8),
              Text('Study profile', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: cs.onSurface)),
            ]),
            const SizedBox(height: 4),
            Text(
              'Feed aapke exam, class aur subjects ke hisaab se personalise hoga.',
              style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant),
            ),

            // ---- Exam Mode ----
            const SizedBox(height: 12),
            Container(
              decoration: BoxDecoration(color: cs.primary.withOpacity(.08), borderRadius: BorderRadius.circular(12)),
              child: SwitchListTile(
                value: examMode,
                onChanged: saving ? null : (v) => setState(() => examMode = v),
                title: const Text('📚 Exam Mode', style: TextStyle(fontWeight: FontWeight.w700)),
                subtitle: Text(
                  examMode && !examModeActive && examDate != null && examDate!.isBefore(DateTime.now())
                      ? 'Exam date nikal gayi — ab inactive hai.'
                      : 'Feed me sirf padhai ka content — entertainment aur distractions hat jaate hain. '
                          'Exam date ke baad apne-aap band.',
                  style: const TextStyle(fontSize: 11.5),
                ),
              ),
            ),

            _label(cs, 'Class'),
            _chips(classLevel, _classOptions, (v) => setState(() => classLevel = v)),
            _label(cs, 'Exam'),
            _chips(examTarget, _examOptions, (v) => setState(() => examTarget = v)),

            _label(cs, 'Focus subjects (max $_maxSubjects)'),
            Row(children: [
              Expanded(
                child: TextField(
                  controller: subjectCtrl,
                  maxLength: 40,
                  textInputAction: TextInputAction.done,
                  onSubmitted: (_) => _addSubject(),
                  decoration: const InputDecoration(hintText: 'e.g. Physics', counterText: '', isDense: true),
                ),
              ),
              IconButton(tooltip: 'Subject add karo', onPressed: _addSubject, icon: const Icon(Icons.add_circle_rounded)),
            ]),
            if (subjects.isNotEmpty)
              Wrap(spacing: 6, children: [
                for (final s in subjects)
                  InputChip(
                    label: Text(s, style: const TextStyle(fontSize: 12)),
                    visualDensity: VisualDensity.compact,
                    onDeleted: () => setState(() => subjects.remove(s)),
                  ),
              ]),

            _label(cs, 'Exam date'),
            Row(children: [
              OutlinedButton.icon(
                onPressed: _pickDate,
                icon: const Icon(Icons.event_rounded, size: 18),
                label: Text(dateLabel),
              ),
              if (examDate != null)
                IconButton(
                  tooltip: 'Date hatao',
                  onPressed: () => setState(() => examDate = null),
                  icon: const Icon(Icons.close_rounded, size: 18),
                ),
            ]),

            if (error != null)
              Padding(
                padding: const EdgeInsets.only(top: 10),
                child: Text(error!, style: TextStyle(fontSize: 12, color: cs.error)),
              ),
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: saving ? null : _save,
                child: saving
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Text('Save'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
