import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import '../utils/ts_error_text.dart';
import '../widgets/ts_review_tile.dart';
import '../widgets/ts_series_card.dart' show TsRatingBit;

/// Ek series ke saare reviews (detail screen sirf top few dikhati hai).
class TestSeriesReviewsScreen extends StatefulWidget {
  final TestSeriesModel series;
  const TestSeriesReviewsScreen({super.key, required this.series});

  @override
  State<TestSeriesReviewsScreen> createState() => _TestSeriesReviewsScreenState();
}

class _TestSeriesReviewsScreenState extends State<TestSeriesReviewsScreen> {
  List<TestSeriesReview> _reviews = [];
  bool _loading = true;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final r = await TestSeriesService.listReviews(widget.series.id);
      if (!mounted) return;
      setState(() {
        _reviews = r;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    Widget body;
    if (_loading && _reviews.isEmpty) {
      body = const Center(child: CircularProgressIndicator());
    } else if (_error != null && _reviews.isEmpty) {
      body = ErrorStateWidget(
        title: l10n.testSeriesErrorTitle,
        subtitle: tsErrorMessage(l10n, _error!),
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    } else {
      body = RefreshIndicator(
        color: cs.primary,
        backgroundColor: cs.surface,
        onRefresh: _load,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.only(top: 6, bottom: 28),
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(kLsPad, 0, kLsPad, 12),
              child: Row(children: [
                Expanded(
                  child: Text(widget.series.title,
                      maxLines: 2, overflow: TextOverflow.ellipsis, style: LsType.head(context, size: 14)),
                ),
                const SizedBox(width: 10),
                TsRatingBit(series: widget.series),
              ]),
            ),
            if (_reviews.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: kLsPad),
                child: Text(l10n.testSeriesNoReviews, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
              )
            else
              ..._reviews.map((r) => TsReviewTile(review: r)),
          ],
        ),
      );
    }

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: l10n.tsReviewsTitle),
      body: body,
    );
  }
}
