import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';

class TsReviewTile extends StatelessWidget {
  final TestSeriesReview review;
  const TsReviewTile({super.key, required this.review});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = lsTokens(context);
    return LsCard(
      margin: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 10),
      padding: const EdgeInsets.all(12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(
            child: Text(review.student,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.onSurface)),
          ),
          Semantics(
            label: '${review.rating}/5',
            excludeSemantics: true,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: List.generate(
                5,
                (i) => Icon(
                  i < review.rating ? Icons.star_rounded : Icons.star_outline_rounded,
                  size: 13,
                  color: t.warning,
                ),
              ),
            ),
          ),
        ]),
        if (review.comment.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(review.comment, style: TextStyle(fontSize: 12, height: 1.45, color: cs.onSurfaceVariant)),
        ],
        if (review.createdAt != null) ...[
          const SizedBox(height: 6),
          Text(
            DateFormat.yMMMd(Localizations.localeOf(context).toString()).format(review.createdAt!.toLocal()),
            style: TextStyle(fontSize: 10, color: cs.onSurfaceVariant),
          ),
        ],
      ]),
    );
  }
}
