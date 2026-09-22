import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../config/testseries_config.dart';
import '../services/attempt_draft_store.dart';
import '../services/testseries_models.dart';
import '../services/testseries_service.dart';
import '../utils/ts_error_text.dart';
import '../widgets/ts_series_card.dart';
import '../widgets/ts_status.dart';
import 'test_series_detail_screen.dart';

// Purane imports (`show tsStatusColor, tsStatusLabel`, `TestSeriesCard`) na tootein.
export '../widgets/ts_series_card.dart' show TestSeriesCard;
export '../widgets/ts_status.dart' show tsStatusColor, tsStatusLabel;

// ============================================================
// TEST SERIES — LIST SCREEN
//
// Home ke quick-action "Test Series" tile aur feed interstitial ke
// "Start Test Series" button, dono yahin aate hain.
//
// Teen forms ek hi screen se: All / Individual / Campus / Live class
// (source tabs) + Free / Paid filter + search.
//
// Live class screen se seedha kholne ke liye:
//   TestSeriesScreen(initialSource: TsSource.liveclass, lockSource: true)
// (lockSource true ho to source tabs chhup jaate hain aur server ko
// `?source=liveclass` bheja jaata hai.)
//
// Series aur meri attempts dono parallel fetch hoti hain, taaki har card
// seedha sahi CTA dikha sake: Start / Resume / View result.
//
// Pagination: DRF `next` follow hota hai (infinite scroll). Search shuru
// karte hi baaki pages background me load ho jaate hain taaki search poori
// list pe chale.
// ============================================================

class TestSeriesScreen extends StatefulWidget {
  final TsSource initialSource;
  final bool lockSource;

  const TestSeriesScreen({
    super.key,
    this.initialSource = TsSource.unknown, // unknown = All
    this.lockSource = false,
  });

  @override
  State<TestSeriesScreen> createState() => _TestSeriesScreenState();
}

class _TestSeriesScreenState extends State<TestSeriesScreen> {
  final ScrollController _scroll = ScrollController();
  final TextEditingController _searchCtrl = TextEditingController();
  Timer? _searchDebounce;

  List<TestSeriesModel> _series = [];
  Map<String, TestAttemptModel> _attemptBySeries = {};
  String? _nextUrl;

  bool _loading = true;
  bool _failed = false;
  Object? _error;
  bool _loadingMore = false;
  bool _loadMoreFailed = false;
  int _autoFilled = 0; // filter/search ke liye auto-load kiye pages (cap: TsConfig.maxPages)

  int _price = 0; // 0 all, 1 free, 2 paid
  late TsSource _source; // unknown = all
  String _query = '';

  static const _sourceTabs = [TsSource.unknown, TsSource.individual, TsSource.campus, TsSource.liveclass];

  @override
  void initState() {
    super.initState();
    _source = widget.initialSource;
    _scroll.addListener(_onScroll);
    _load();
  }

  @override
  void dispose() {
    _searchDebounce?.cancel();
    _searchCtrl.dispose();
    _scroll.dispose();
    super.dispose();
  }

  // ---------------- loading ----------------

  String? get _serverSource {
    // Sirf locked mode me server-side filter; warna client-side (saare
    // pages ek hi list me hain, aur source string ke variants — personal /
    // individual — se bachna hai).
    if (!widget.lockSource) return null;
    switch (widget.initialSource) {
      case TsSource.campus:
        return 'campus';
      case TsSource.liveclass:
        return 'liveclass';
      case TsSource.individual:
        return 'individual';
      case TsSource.unknown:
        return null;
    }
  }

  Future<void> _load() async {
    if (mounted) {
      setState(() {
        _loading = true;
        _failed = false;
        _loadMoreFailed = false;
      });
    }
    try {
      // Pehle wo submits bhejo jo pichhli baar network na hone se atke the.
      final flushed = TsSubmissionQueue.flush();

      final results = await Future.wait<Object?>([
        TestSeriesService.listSeriesPage(source: _serverSource),
        // Attempts fail ho jaayein to series phir bhi dikhni chahiye —
        // CTA tab "Start" par default kar jaata hai.
        TestSeriesService.listMyAttempts().catchError((_) => <TestAttemptModel>[]),
      ]);
      final page = results[0] as TsPage<TestSeriesModel>;
      final attempts = results[1] as List<TestAttemptModel>;

      final map = <String, TestAttemptModel>{};
      for (final a in attempts) {
        final existing = map[a.seriesId];
        // Multi-attempt allowed hai, isliye sabse latest attempt_number
        // wali attempt hi card pe dikhni chahiye.
        if (existing == null || a.attemptNumber > existing.attemptNumber) {
          map[a.seriesId] = a;
        }
      }

      if (!mounted) return;
      setState(() {
        _series = page.items;
        _nextUrl = page.nextUrl;
        _attemptBySeries = map;
        _loading = false;
      });

      unawaited(_afterFlush(flushed));
      _autoFilled = 0;
      _maybeFillViewport();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e;
        _failed = _series.isEmpty;
      });
    }
  }

  /// Queue flush ka result aane par user ko batao aur attempts refresh karo.
  Future<void> _afterFlush(Future<List<TestAttemptModel>> flushed) async {
    final done = await flushed;
    if (done.isEmpty || !mounted) return;
    lsSnack(context, AppLocalizations.of(context)!.tsPendingSynced);
    try {
      final attempts = await TestSeriesService.listMyAttempts();
      if (!mounted) return;
      final map = <String, TestAttemptModel>{};
      for (final a in attempts) {
        final e = map[a.seriesId];
        if (e == null || a.attemptNumber > e.attemptNumber) map[a.seriesId] = a;
      }
      setState(() => _attemptBySeries = map);
    } catch (_) {}
  }

  Future<void> _loadMore() async {
    final next = _nextUrl;
    if (next == null || _loadingMore) return;
    setState(() {
      _loadingMore = true;
      _loadMoreFailed = false;
    });
    try {
      final page = await TestSeriesService.listSeriesPage(pageUrl: next);
      if (!mounted) return;
      setState(() {
        _series = [..._series, ...page.items];
        _nextUrl = page.nextUrl;
        _loadingMore = false;
      });
      _maybeFillViewport();
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loadingMore = false;
        _loadMoreFailed = true;
      });
    }
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    if (_scroll.position.extentAfter < 400 && !_loadMoreFailed) _loadMore();
  }

  /// Filter / search ke baad list chhoti reh jaaye to scroll hi nahi hoga
  /// aur `_onScroll` kabhi fire nahi hoga — isliye khud agla page maango.
  void _maybeFillViewport() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || _nextUrl == null || _loadingMore || _loadMoreFailed) return;
      if (_autoFilled >= TsConfig.maxPages) return;
      final searching = _query.isNotEmpty;
      if (searching || _visible.length < TsConfig.viewportFillMinItems) {
        _autoFilled++;
        _loadMore();
      }
    });
  }

  // ---------------- filtering ----------------

  List<TestSeriesModel> get _visible {
    Iterable<TestSeriesModel> it = _series.where((s) {
      // Draft browse me nahi; archived tabhi jab meri attempt ho (purana result dekhne ke liye).
      if (s.isDraft) return false;
      if (s.isArchived && !_attemptBySeries.containsKey(s.id)) return false;
      return true;
    });

    if (_source != TsSource.unknown && !widget.lockSource) {
      it = it.where((s) => s.sourceType == _source);
    }
    if (_price == 1) it = it.where((s) => !s.isPaid);
    if (_price == 2) it = it.where((s) => s.isPaid);

    final q = _query.trim().toLowerCase();
    if (q.isNotEmpty) {
      it = it.where((s) => s.title.toLowerCase().contains(q) || s.creator.toLowerCase().contains(q));
    }
    return it.toList();
  }

  void _onSearchChanged(String v) {
    _searchDebounce?.cancel();
    _searchDebounce = Timer(TsConfig.searchDebounce, () {
      if (!mounted) return;
      setState(() => _query = v);
      _autoFilled = 0;
      _maybeFillViewport();
    });
  }

  Future<void> _open(TestSeriesModel s) async {
    HapticFeedback.selectionClick();
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => TestSeriesDetailScreen(seriesId: s.id, initialSeries: s),
      ),
    );
    if (mounted) _load();
  }

  // ---------------- build ----------------

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: l10n.testSeries),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(kLsPad, 4, kLsPad, 8),
          child: TextField(
            controller: _searchCtrl,
            onChanged: _onSearchChanged,
            textInputAction: TextInputAction.search,
            style: TextStyle(fontSize: 13.5, color: cs.onSurface),
            decoration: InputDecoration(
              hintText: l10n.tsSearchHint,
              prefixIcon: const Icon(Icons.search_rounded, size: 20),
              suffixIcon: _query.isEmpty && _searchCtrl.text.isEmpty
                  ? null
                  : IconButton(
                      icon: const Icon(Icons.close_rounded, size: 18),
                      onPressed: () {
                        _searchCtrl.clear();
                        _searchDebounce?.cancel();
                        setState(() => _query = '');
                      },
                    ),
              isDense: true,
            ),
          ),
        ),
        if (!widget.lockSource) ...[
          TsChipRow(
            labels: [
              l10n.testSeriesTabAll,
              l10n.tsSourceIndividual,
              l10n.tsSourceCampus,
              l10n.tsSourceLiveClass,
            ],
            selectedIndex: _sourceTabs.indexOf(_source).clamp(0, 3).toInt(),
            onSelected: (i) {
              HapticFeedback.selectionClick();
              setState(() => _source = _sourceTabs[i]);
              _autoFilled = 0;
              _maybeFillViewport();
            },
          ),
          const SizedBox(height: 4),
        ],
        LsFilterChips(
          labels: [l10n.testSeriesTabAll, l10n.testSeriesTabFree, l10n.testSeriesTabPaid],
          selectedIndex: _price,
          onSelected: (i) {
            HapticFeedback.selectionClick();
            setState(() => _price = i);
            _autoFilled = 0;
            _maybeFillViewport();
          },
        ),
        const SizedBox(height: 6),
        Expanded(child: _buildBody(cs, l10n)),
      ]),
    );
  }

  Widget _buildBody(ColorScheme cs, AppLocalizations l10n) {
    if (_loading && _series.isEmpty) {
      return ListView.builder(
        padding: const EdgeInsets.only(top: 4, bottom: 24),
        itemCount: 4,
        itemBuilder: (_, __) => const LsPostCardSkeleton(sidePad: kLsPad),
      );
    }
    if (_failed && _series.isEmpty) {
      return ErrorStateWidget(
        title: l10n.testSeriesErrorTitle,
        subtitle: _error == null ? l10n.feedErrorSubtitle : tsErrorMessage(l10n, _error!),
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    }

    final items = _visible;
    final searching = _query.trim().isNotEmpty;

    return RefreshIndicator(
      color: cs.primary,
      backgroundColor: cs.surface,
      onRefresh: _load,
      child: items.isEmpty
          ? ListView(
              controller: _scroll,
              physics: const AlwaysScrollableScrollPhysics(),
              children: [
                const SizedBox(height: 40),
                if (_loadingMore || (_nextUrl != null && !_loadMoreFailed))
                  const Center(child: Padding(padding: EdgeInsets.all(24), child: CircularProgressIndicator()))
                else
                  EmptyStateWidget(
                    icon: searching ? Icons.search_off_rounded : Icons.fact_check_outlined,
                    title: searching ? l10n.tsNoSearchResults : l10n.testSeriesEmptyTitle,
                    subtitle: searching ? null : l10n.testSeriesEmptySubtitle,
                  ),
              ],
            )
          : ListView.builder(
              controller: _scroll,
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.only(top: 4, bottom: 28),
              itemCount: items.length + 1,
              itemBuilder: (context, i) {
                if (i == items.length) return _footer(cs, l10n);
                return TestSeriesCard(
                  series: items[i],
                  attempt: _attemptBySeries[items[i].id],
                  onTap: () => _open(items[i]),
                );
              },
            ),
    );
  }

  Widget _footer(ColorScheme cs, AppLocalizations l10n) {
    if (_loadingMore) {
      return const Padding(
        padding: EdgeInsets.all(16),
        child: Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2))),
      );
    }
    if (_loadMoreFailed) {
      return TextButton(
        onPressed: () {
          setState(() => _loadMoreFailed = false);
          _loadMore();
        },
        child: Text(l10n.tsLoadMoreFailed),
      );
    }
    return const SizedBox(height: 4);
  }
}
