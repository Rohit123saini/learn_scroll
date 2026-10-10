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
import 'create_test_series_screen.dart';
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
// Teen forms ek hi screen se: All / Individual / Campus / Tuition class
// (source tabs) + Free / Paid filter + search.
//
// Tuition class screen se seedha kholne ke liye:
//   TestSeriesScreen(initialSource: TsSource.tuitionclass, lockSource: true)
// (lockSource true ho to source tabs chhup jaate hain aur server ko
// `?source=tuitionclass` bheja jaata hai.)
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

  // TASK G9 — Part 2. Subject/difficulty/min-rating filters, now sent
  // straight to the backend (see `_load()`/`TestSeriesService.
  // listSeriesPage()`) instead of being applied client-side like
  // price/search/source used to be.
  String? _subject; // null = "All subjects"
  String? _difficulty; // null = "All" | 'easy' | 'medium' | 'hard'
  double? _minRating; // null = "Any rating"

  // Subject is a free-text field on the backend (no fixed choice list),
  // so there's no "list all subjects" endpoint to seed a dropdown from.
  // Instead we build the chip set from whatever subjects we've actually
  // seen across the browse list + both rails as pages come in — good
  // enough for a filter chip row, and it only ever grows during this
  // screen's lifetime.
  final Set<String> _knownSubjects = {};

  // TASK G9 — Part 2. "Trending" + "Following" discovery rails.
  List<TestSeriesModel> _trending = [];
  bool _trendingLoading = true;
  bool _trendingFailed = false;

  List<TestSeriesModel> _following = [];
  bool _followingLoading = true;
  bool _followingFailed = false;

  static const _sourceTabs = [TsSource.unknown, TsSource.individual, TsSource.campus, TsSource.tuitionclass];

  @override
  void initState() {
    super.initState();
    _source = widget.initialSource;
    _scroll.addListener(_onScroll);
    _load();
    _loadTrending();
    _loadFollowing();
  }

  @override
  void dispose() {
    _searchDebounce?.cancel();
    _searchCtrl.dispose();
    _scroll.dispose();
    super.dispose();
  }

  // ---------------- loading ----------------

  // TASK G9 — Part 2. Used to be locked-mode-only (everything else stayed
  // client-side, so switching the source tab just re-filtered the
  // already-fetched list). Now that search/filter is backend-driven end
  // to end, the source tab is sent to the backend too, same as
  // subject/difficulty/price/minRating/search below.
  String? get _serverSource {
    final src = widget.lockSource ? widget.initialSource : _source;
    switch (src) {
      case TsSource.campus:
        return 'campus';
      case TsSource.tuitionclass:
        return 'tuitionclass';
      case TsSource.individual:
        return 'individual';
      case TsSource.unknown:
        return null;
    }
  }

  String? get _priceParam => _price == 1 ? 'free' : (_price == 2 ? 'paid' : null);

  void _rememberSubjects(Iterable<TestSeriesModel> items) {
    for (final s in items) {
      if (s.subject.trim().isNotEmpty) _knownSubjects.add(s.subject.trim());
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
    // TASK G16 — cached-last-known-data on reopen. Only for the true
    // default view (no lockSource, "All" tab, no search/price/subject/
    // difficulty/rating filter, and only on this screen's very first
    // load) — same guard `listSeriesPage` itself uses to decide what's
    // worth caching in the first place. Shows the last list instantly
    // (skeleton skipped entirely) while the real fetch below still runs
    // and will overwrite it moments later either way, so a stale card or
    // two is never visible for long.
    final isDefaultView = !widget.lockSource &&
        _source == TsSource.unknown &&
        _price == 0 &&
        _query.isEmpty &&
        _subject == null &&
        _difficulty == null &&
        _minRating == null;
    if (isDefaultView && _series.isEmpty) {
      final cached = await TestSeriesService.getCachedDefaultSeriesPage();
      if (cached != null && mounted && _series.isEmpty) {
        setState(() {
          _series = cached.items;
          _nextUrl = cached.nextUrl;
          _loading = false;
        });
        _rememberSubjects(cached.items);
      }
    }
    try {
      // Pehle wo submits bhejo jo pichhli baar network na hone se atke the.
      final flushed = TsSubmissionQueue.flush();

      final results = await Future.wait<Object?>([
        TestSeriesService.listSeriesPage(
          source: _serverSource,
          subject: _subject,
          difficulty: _difficulty,
          price: _priceParam,
          minRating: _minRating,
          search: _query.isEmpty ? null : _query,
        ),
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
      _rememberSubjects(page.items);

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
      _rememberSubjects(page.items);
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

  // TASK G9 — Part 2. Search/source/price/subject/difficulty/min-rating
  // are all backend-driven now (see `_load()`); the only thing left to
  // filter client-side is hiding a user's own drafts/archived-without-
  // attempt from the browse list — that's a visibility policy for this
  // screen specifically, not a search/filter concern, and it depends on
  // `_attemptBySeries` (a client-side join across two separate API
  // calls) so it can't move to the backend query itself.
  List<TestSeriesModel> get _visible {
    return _series.where((s) {
      if (s.isDraft) return false;
      if (s.isArchived && !_attemptBySeries.containsKey(s.id)) return false;
      return true;
    }).toList();
  }

  void _onSearchChanged(String v) {
    _searchDebounce?.cancel();
    _searchDebounce = Timer(TsConfig.searchDebounce, () {
      if (!mounted) return;
      setState(() => _query = v);
      _refetchAll();
    });
  }

  /// Any filter (source/price/subject/difficulty/min-rating/search)
  /// changed — refetch the browse list AND the Trending rail (Following
  /// isn't filterable, so it's left alone; see `TestSeriesService.
  /// following()`).
  void _refetchAll() {
    _load();
    _loadTrending();
  }

  Future<void> _loadTrending() async {
    if (mounted) {
      setState(() {
        _trendingLoading = true;
        _trendingFailed = false;
      });
    }
    try {
      final items = await TestSeriesService.trending(
        subject: _subject,
        difficulty: _difficulty,
        price: _priceParam,
        minRating: _minRating,
        search: _query.isEmpty ? null : _query,
        limit: 10,
      );
      if (!mounted) return;
      setState(() {
        _trending = items;
        _trendingLoading = false;
      });
      _rememberSubjects(items);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _trendingLoading = false;
        _trendingFailed = true;
      });
    }
  }

  Future<void> _loadFollowing() async {
    if (mounted) {
      setState(() {
        _followingLoading = true;
        _followingFailed = false;
      });
    }
    try {
      final items = await TestSeriesService.following(limit: 10);
      if (!mounted) return;
      setState(() {
        _following = items;
        _followingLoading = false;
      });
      _rememberSubjects(items);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _followingLoading = false;
        _followingFailed = true;
      });
    }
  }

  /// Pull-to-refresh — refreshes the browse list AND both rails together.
  Future<void> _refreshAll() async {
    await Future.wait([_load(), _loadTrending(), _loadFollowing()]);
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

  // TASK 8 — any authenticated user can create their own (individual)
  // test series; this is the entry point into that flow.
  Future<void> _createNew() async {
    HapticFeedback.selectionClick();
    final created = await Navigator.push<TestSeriesModel>(
      context,
      MaterialPageRoute(builder: (_) => const CreateTestSeriesScreen()),
    );
    if (created != null && mounted) _load();
  }

  // ---------------- build ----------------

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: l10n.testSeries),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _createNew,
        icon: const Icon(Icons.add_rounded),
        label: const Text('Create'),
      ),
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
                  : IconButton(tooltip: 'Close', 
                      icon: const Icon(Icons.close_rounded, size: 18),
                      onPressed: () {
                        _searchCtrl.clear();
                        _searchDebounce?.cancel();
                        setState(() => _query = '');
                        _refetchAll();
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
              l10n.tsSourceTuitionClass,
            ],
            selectedIndex: _sourceTabs.indexOf(_source).clamp(0, 3).toInt(),
            onSelected: (i) {
              HapticFeedback.selectionClick();
              setState(() => _source = _sourceTabs[i]);
              _refetchAll();
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
            _refetchAll();
          },
        ),
        const SizedBox(height: 6),
        // TASK G9 — Part 2. Difficulty + min-rating (fixed choice sets) +
        // subject (dynamic, built from `_knownSubjects` — see that
        // field's docstring). Kept as their own row rather than folded
        // into the Free/Paid row above since three independent facets in
        // one horizontal chip row would get confusing about which chip
        // belongs to which filter.
        _buildDiscoveryFilters(cs, l10n),
        const SizedBox(height: 6),
        Expanded(child: _buildBody(cs, l10n)),
      ]),
    );
  }

  Widget _buildDiscoveryFilters(ColorScheme cs, AppLocalizations l10n) {
    const difficulties = [null, 'easy', 'medium', 'hard'];
    const difficultyLabels = ['All levels', 'Easy', 'Medium', 'Hard'];
    const ratings = [null, 3.0, 4.0, 4.5];
    const ratingLabels = ['Any rating', '3.0+', '4.0+', '4.5+'];
    final subjects = _knownSubjects.toList()..sort();

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: kLsPad),
      child: Row(children: [
        // Subject — dynamic list, so a dropdown reads better than an
        // ever-growing chip row once a few dozen subjects show up.
        Container(
          height: 34,
          padding: const EdgeInsets.symmetric(horizontal: 10),
          decoration: BoxDecoration(
            border: Border.all(color: cs.outlineVariant),
            borderRadius: BorderRadius.circular(18),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String?>(
              value: _subject,
              isDense: true,
              icon: const Icon(Icons.expand_more_rounded, size: 16),
              style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.onSurface),
              hint: Text('Subject', style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
              items: [
                const DropdownMenuItem<String?>(value: null, child: Text('All subjects')),
                ...subjects.map((s) => DropdownMenuItem<String?>(value: s, child: Text(s))),
              ],
              onChanged: (v) {
                HapticFeedback.selectionClick();
                setState(() => _subject = v);
                _refetchAll();
              },
            ),
          ),
        ),
        const SizedBox(width: 8),
        for (int i = 0; i < difficulties.length; i++)
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: ChoiceChip(
              label: Text(difficultyLabels[i], style: const TextStyle(fontSize: 12)),
              selected: _difficulty == difficulties[i],
              onSelected: (_) {
                HapticFeedback.selectionClick();
                setState(() => _difficulty = difficulties[i]);
                _refetchAll();
              },
            ),
          ),
        const SizedBox(width: 4),
        for (int i = 0; i < ratings.length; i++)
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: ChoiceChip(
              avatar: ratings[i] == null ? null : Icon(Icons.star_rounded, size: 14, color: Colors.amber.shade700),
              label: Text(ratingLabels[i], style: const TextStyle(fontSize: 12)),
              selected: _minRating == ratings[i],
              onSelected: (_) {
                HapticFeedback.selectionClick();
                setState(() => _minRating = ratings[i]);
                _refetchAll();
              },
            ),
          ),
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
      onRefresh: _refreshAll,
      child: ListView.builder(
        controller: _scroll,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(top: 4, bottom: 28),
        // rails (2) + either the empty-state row (1) or every card + footer (items.length + 1)
        itemCount: 2 + (items.isEmpty ? 1 : items.length + 1),
        itemBuilder: (context, i) {
          if (i == 0) {
            return _buildRail(
              cs,
              l10n,
              title: 'Trending',
              icon: Icons.trending_up_rounded,
              items: _trending,
              loading: _trendingLoading,
              failed: _trendingFailed,
              onRetry: _loadTrending,
              emptyText: 'No trending test series right now.',
            );
          }
          if (i == 1) {
            return _buildRail(
              cs,
              l10n,
              title: l10n.following,
              icon: Icons.person_add_alt_1_rounded,
              items: _following,
              loading: _followingLoading,
              failed: _followingFailed,
              onRetry: _loadFollowing,
              emptyText: 'Follow creators to see their new test series here.',
            );
          }
          final j = i - 2;
          if (items.isEmpty) {
            return Padding(
              padding: const EdgeInsets.only(top: 24),
              child: (_loadingMore || (_nextUrl != null && !_loadMoreFailed))
                  ? const Center(child: Padding(padding: EdgeInsets.all(24), child: CircularProgressIndicator()))
                  : EmptyStateWidget(
                      icon: searching ? Icons.search_off_rounded : Icons.fact_check_outlined,
                      title: searching ? l10n.tsNoSearchResults : l10n.testSeriesEmptyTitle,
                      subtitle: searching ? null : l10n.testSeriesEmptySubtitle,
                    ),
            );
          }
          if (j == items.length) return _footer(cs, l10n);
          return TestSeriesCard(
            series: items[j],
            attempt: _attemptBySeries[items[j].id],
            onTap: () => _open(items[j]),
          );
        },
      ),
    );
  }

  // TASK G9 — Part 2. Shared horizontal-rail layout for "Trending" and
  // "Following" — reuses `TestSeriesCard` (same card as the vertical
  // browse list) wrapped in a fixed-width SizedBox since a horizontal
  // ListView needs bounded item widths. Handles its own loading/empty/
  // error states rather than hiding entirely, so the section always
  // reads as "yes, this rail exists" even with nothing in it yet.
  Widget _buildRail(
    ColorScheme cs,
    AppLocalizations l10n, {
    required String title,
    required IconData icon,
    required List<TestSeriesModel> items,
    required bool loading,
    required bool failed,
    required VoidCallback onRetry,
    required String emptyText,
  }) {
    Widget content;
    if (loading && items.isEmpty) {
      content = SizedBox(
        height: 150,
        child: ListView.builder(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: kLsPad),
          itemCount: 3,
          itemBuilder: (_, __) => const Padding(
            padding: EdgeInsets.only(right: 12),
            child: SizedBox(width: 220, child: LsPostCardSkeleton(sidePad: 0)),
          ),
        ),
      );
    } else if (failed && items.isEmpty) {
      content = Padding(
        padding: const EdgeInsets.symmetric(horizontal: kLsPad, vertical: 10),
        child: Row(children: [
          Expanded(
            child: Text("Couldn't load — check your connection.",
                style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
          ),
          TextButton(onPressed: onRetry, child: Text(l10n.retry)),
        ]),
      );
    } else if (items.isEmpty) {
      content = Padding(
        padding: const EdgeInsets.symmetric(horizontal: kLsPad, vertical: 10),
        child: Text(emptyText, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
      );
    } else {
      content = SizedBox(
        height: 150,
        child: ListView.builder(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: kLsPad),
          itemCount: items.length,
          itemBuilder: (context, i) {
            final s = items[i];
            return SizedBox(
              width: 240,
              child: TestSeriesCard(
                series: s,
                attempt: _attemptBySeries[s.id],
                onTap: () => _open(s),
              ),
            );
          },
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(kLsPad, 4, kLsPad, 6),
          child: Row(children: [
            Icon(icon, size: 15, color: cs.primary),
            const SizedBox(width: 6),
            Text(title, style: LsType.head(context, size: 14)),
          ]),
        ),
        content,
      ]),
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
