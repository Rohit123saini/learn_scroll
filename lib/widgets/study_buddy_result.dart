import 'package:flutter/material.dart';

/// AI Study Buddy ka result — mode ke hisaab se alag view:
///   explain    -> {"text": "..."}                         : tinted text card
///   quiz       -> {"questions": [{question, options, answer}]} : tap-to-answer MCQs + score
///   flashcards -> {"cards": [{front, back}]}              : tap-to-flip cards
/// Shape `POST /message/ai/study-buddy/` ke response jaisa hai (message/views_ai.py).
class StudyBuddyResult extends StatefulWidget {
  final Map<String, dynamic> data;
  const StudyBuddyResult({super.key, required this.data});

  @override
  State<StudyBuddyResult> createState() => _StudyBuddyResultState();
}

class _StudyBuddyResultState extends State<StudyBuddyResult> {
  final Map<int, String> _picked = {}; // question index -> chosen option text
  final Set<int> _flipped = {};

  @override
  Widget build(BuildContext context) {
    final mode = widget.data['mode']?.toString();
    switch (mode) {
      case 'quiz':
        return _quiz(context);
      case 'flashcards':
        return _cards(context);
      default:
        return _explain(context);
    }
  }

  Widget _explain(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: cs.primary.withOpacity(.08), borderRadius: BorderRadius.circular(12)),
      child: SelectableText(
        (widget.data['text'] ?? '').toString(),
        style: TextStyle(fontSize: 13, height: 1.5, color: cs.onSurface),
      ),
    );
  }

  Widget _quiz(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final questions = (widget.data['questions'] as List? ?? const [])
        .whereType<Map>()
        .map((q) => Map<String, dynamic>.from(q))
        .toList();
    final answered = _picked.length;
    final correct = [
      for (var i = 0; i < questions.length; i++)
        if (_picked[i] != null && _picked[i] == questions[i]['answer']) i
    ].length;

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      for (var i = 0; i < questions.length; i++) ...[
        Text('${i + 1}. ${questions[i]['question']}',
            style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: cs.onSurface)),
        const SizedBox(height: 6),
        for (final opt in (questions[i]['options'] as List).map((o) => o.toString()))
          _option(context, i, opt, questions[i]['answer'].toString()),
        const SizedBox(height: 12),
      ],
      if (questions.isNotEmpty && answered == questions.length)
        Text('Score: $correct / ${questions.length}',
            style: TextStyle(fontSize: 14, fontWeight: FontWeight.w800, color: cs.primary)),
    ]);
  }

  Widget _option(BuildContext context, int qi, String opt, String answer) {
    final cs = Theme.of(context).colorScheme;
    final picked = _picked[qi];
    final done = picked != null;
    Color? bg;
    if (done && opt == answer) bg = Colors.green.withOpacity(.18);
    if (done && opt == picked && opt != answer) bg = Colors.red.withOpacity(.18);
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: done ? null : () => setState(() => _picked[qi] = opt),
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: bg ?? cs.surfaceVariant,
            borderRadius: BorderRadius.circular(10),
          ),
          child: Text(opt, style: TextStyle(fontSize: 12.5, color: cs.onSurface)),
        ),
      ),
    );
  }

  Widget _cards(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final cards = (widget.data['cards'] as List? ?? const [])
        .whereType<Map>()
        .map((c) => Map<String, dynamic>.from(c))
        .toList();
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text('Card pe tap karke palto', style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
      const SizedBox(height: 8),
      for (var i = 0; i < cards.length; i++)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: InkWell(
            borderRadius: BorderRadius.circular(12),
            onTap: () => setState(() => _flipped.contains(i) ? _flipped.remove(i) : _flipped.add(i)),
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: _flipped.contains(i) ? cs.primary.withOpacity(.10) : cs.surfaceVariant,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(_flipped.contains(i) ? 'ANSWER' : 'QUESTION',
                    style: TextStyle(fontSize: 10, letterSpacing: .8, color: cs.onSurfaceVariant)),
                const SizedBox(height: 4),
                Text(
                  (_flipped.contains(i) ? cards[i]['back'] : cards[i]['front']).toString(),
                  style: TextStyle(fontSize: 13.5, height: 1.4, color: cs.onSurface),
                ),
              ]),
            ),
          ),
        ),
    ]);
  }
}
