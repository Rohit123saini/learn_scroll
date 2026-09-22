import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';
import 'ts_image_viewer.dart';

/// Result breakdown ka ek question.
class TsResponseCard extends StatelessWidget {
  final int index;
  final TsResponse response;
  final TsQuestion? question;

  const TsResponseCard({super.key, required this.index, required this.response, required this.question});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = lsTokens(context);
    final l10n = AppLocalizations.of(context)!;

    final Color statusColor;
    final String statusLabel;
    if (response.awaitingReview) {
      statusColor = t.info;
      statusLabel = l10n.testAwaitingReview;
    } else if (response.isCorrect == true) {
      statusColor = t.success;
      statusLabel = l10n.answerCorrect;
    } else if (response.isCorrect == false) {
      statusColor = t.danger;
      statusLabel = l10n.answerIncorrect;
    } else {
      statusColor = cs.primary;
      statusLabel = l10n.assignmentReviewed;
    }

    final q = question;
    final correct = q?.correctAnswer;

    // Sirf photo ka jawab ho to "Not answered" galat hai.
    var yourAnswer = tsReadableAnswer(l10n, response.answerData, q);
    if (response.answerAttachment != null && yourAnswer == l10n.answerNotAnswered) {
      yourAnswer = l10n.tsYourPhoto;
    }

    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(l10n.questionShort(index + 1),
              style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: cs.primary)),
          const Spacer(),
          LsStatusChip(label: statusLabel, color: statusColor),
          const SizedBox(width: 8),
          // Question load na hua ho to "x of 0" dikhana galat hai — sirf awarded marks.
          Text(
            q != null
                ? l10n.assignmentMarksOf(response.marksAwarded ?? 0, q.marks)
                : '${response.marksAwarded ?? 0}',
            style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: cs.onSurface),
          ),
        ]),
        if (q != null) ...[
          const SizedBox(height: 9),
          Text(q.text, style: TextStyle(fontSize: 13, height: 1.45, color: cs.onSurface)),
          if (q.attachment != null) ...[
            const SizedBox(height: 8),
            TsNetworkImage(url: q.attachment, maxHeight: 160, semanticLabel: l10n.tsQuestionImage),
          ],
        ],
        const SizedBox(height: 11),
        _Block(
          title: l10n.testYourAnswer,
          text: yourAnswer,
          background: cs.surfaceVariant,
          child: response.answerAttachment == null
              ? null
              : Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: TsNetworkImage(
                    url: response.answerAttachment,
                    maxHeight: 160,
                    semanticLabel: l10n.tsYourPhoto,
                  ),
                ),
        ),
        // Sirf tab jab backend ne bheja ho (creator view / result release ke baad).
        if (correct != null) ...[
          const SizedBox(height: 8),
          _Block(
            title: l10n.tsCorrectAnswer,
            text: tsReadableAnswer(l10n, correct, q),
            background: t.success.withOpacity(.10),
          ),
        ],
        if (response.reviewerFeedback.isNotEmpty) ...[
          const SizedBox(height: 10),
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(Icons.rate_review_outlined, size: 15, color: cs.onSurfaceVariant),
            const SizedBox(width: 8),
            Expanded(
              child: Text(response.reviewerFeedback,
                  style: TextStyle(fontSize: 12, height: 1.45, color: cs.onSurfaceVariant)),
            ),
          ]),
        ],
      ]),
    );
  }
}

class _Block extends StatelessWidget {
  final String title;
  final String text;
  final Color background;
  final Widget? child;
  const _Block({required this.title, required this.text, required this.background, this.child});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(11),
      decoration: BoxDecoration(color: background, borderRadius: BorderRadius.circular(12)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(title, style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant)),
        const SizedBox(height: 5),
        Text(text, style: TextStyle(fontSize: 12.5, height: 1.45, color: cs.onSurface)),
        if (child != null) child!,
      ]),
    );
  }
}

/// `answer_data` / `correct_answer` ko padhne layak text me badalta hai.
/// Shape question type ke hisaab se badalti hai (§4.3), aur question object
/// na mile to option ids hi dikhti hain — crash kabhi nahi.
String tsReadableAnswer(AppLocalizations l10n, dynamic data, TsQuestion? question) {
  if (data == null) return l10n.answerNotAnswered;

  String label(String id) {
    final q = question;
    if (q == null) return id;
    for (final o in [...q.choices, ...q.matchLeft, ...q.matchRight]) {
      if (o.id == id) return o.text;
    }
    return id;
  }

  if (data is String) return data.trim().isEmpty ? l10n.answerNotAnswered : data;

  if (data is Map) {
    if (data['option_id'] != null) return label(data['option_id'].toString());
    final ids = data['option_ids'];
    if (ids is List) {
      return ids.isEmpty ? l10n.answerNotAnswered : ids.map((e) => label(e.toString())).join(', ');
    }
    final seq = data['sequence'];
    if (seq is List) {
      return seq.isEmpty
          ? l10n.answerNotAnswered
          : seq.asMap().entries.map((e) => '${e.key + 1}. ${label(e.value.toString())}').join('\n');
    }
    final pairs = data['pairs'];
    if (pairs is Map) {
      return pairs.isEmpty
          ? l10n.answerNotAnswered
          : pairs.entries.map((e) => '${label(e.key.toString())} → ${label(e.value.toString())}').join('\n');
    }
    final text = data['text'];
    if (text != null) {
      return text.toString().trim().isEmpty ? l10n.answerNotAnswered : text.toString();
    }
    if (data.isEmpty) return l10n.answerNotAnswered;
    return data.toString();
  }

  if (data is List) {
    return data.isEmpty ? l10n.answerNotAnswered : data.map((e) => label(e.toString())).join(', ');
  }
  return data.toString();
}
