import 'dart:async';
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:timeago/timeago.dart' as timeago;
import 'api_service.dart';
import 'models/search_result.dart';
import '../utils/api.dart';
import '../profile/screens/target_profile.dart';
import '../l10n/app_localizations.dart';

/// Unified LearnScroll search — one bar, filter chips, sectioned
/// results (Instagram-style "All" tab), recent searches.
///
/// Covers everything the backend actually supports today: people
/// (`/profile/search/`) + notices/assignments/tests/messages
/// (`/core/search/`, `core.views.SearchView`). `post` and class
/// "documents" (`liveclass.ClassMaterial`) are NOT here — both are
/// documented STUBS on the backend (`core_app_documentation.md` §6.2 —
/// no model ever got uploaded/wired for either), so there's nothing
/// real to search yet. Add a `SearchFilter` case + a section once the
/// backend actually registers one of those sources.
class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  static const _recentKey = 'ls_recent_searches';
  static const _recentMax = 10;

  final TextEditingController _searchController = TextEditingController();
  final FocusNode _focusNode = FocusNode();
  Timer? _debounce;

  SearchFilter _filter = SearchFilter.all;
  bool _loading = false;

  List<dynamic> _people = [];
  List<SearchResultItem> _results = [];
  List<String> _recent = [];

  @override
  void initState() {
    super.initState();
    _loadRecent();
  }

  @override
  void dispose() {
    _searchController.dispose();
    _focusNode.dispose();
    _debounce?.cancel();
    super.dispose();
  }

  // ---------------------------------------------------------------- data

  Future<void> _loadRecent() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getStringList(_recentKey) ?? [];
      if (mounted) setState(() => _recent = saved);
    } catch (_) {
      // Recent searches are a convenience, not core functionality —
      // fine to silently stay empty if prefs can't be read.
    }
  }

  Future<void> _rememberSearch(String term) async {
    final trimmed = term.trim();
    if (trimmed.isEmpty) return;
    final updated = [
      trimmed,
      ..._recent.where((t) => t.toLowerCase() != trimmed.toLowerCase()),
    ];
    if (updated.length > _recentMax) {
      updated.removeRange(_recentMax, updated.length);
    }
    setState(() => _recent = updated);
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList(_recentKey, updated);
    } catch (_) {}
  }

  Future<void> _removeRecent(String term) async {
    final updated = _recent.where((t) => t != term).toList();
    setState(() => _recent = updated);
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList(_recentKey, updated);
    } catch (_) {}
  }

  Future<void> _clearRecent() async {
    setState(() => _recent = []);
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_recentKey);
    } catch (_) {}
  }

  void _onQueryChanged(String query) {
    if (_debounce?.isActive ?? false) _debounce!.cancel();
    _debounce = Timer(const Duration(milliseconds: 450), () => _runSearch(query));
  }

  Future<void> _runSearch(String rawQuery) async {
    final query = rawQuery.trim();
    if (query.isEmpty) {
      setState(() {
        _people = [];
        _results = [];
        _loading = false;
      });
      return;
    }

    setState(() => _loading = true);

    List<dynamic> people = [];
    List<SearchResultItem> results = [];

    switch (_filter) {
      case SearchFilter.people:
        people = await SearchApiService.searchUsers(query);
        break;
      case SearchFilter.all:
        final both = await Future.wait([
          SearchApiService.searchUsers(query),
          SearchApiService.searchEverything(query),
        ]);
        people = both[0] as List<dynamic>;
        results = both[1] as List<SearchResultItem>;
        break;
      case SearchFilter.notices:
      case SearchFilter.assignments:
      case SearchFilter.tests:
      case SearchFilter.messages:
        results = await SearchApiService.searchEverything(
          query,
          sources: [_filter.backendSource!],
        );
        break;
    }

    // Query may have changed again while these calls were in flight —
    // only apply results if they still match what's in the box.
    if (!mounted || _searchController.text.trim() != query) return;
    setState(() {
      _people = people;
      _results = results;
      _loading = false;
    });
  }

  void _onFilterSelected(SearchFilter filter) {
    if (filter == _filter) return;
    setState(() => _filter = filter);
    if (_searchController.text.trim().isNotEmpty) {
      _runSearch(_searchController.text);
    }
  }

  Map<String, List<SearchResultItem>> _groupBySource(List<SearchResultItem> items) {
    final map = <String, List<SearchResultItem>>{};
    for (final item in items) {
      map.putIfAbsent(item.source, () => []).add(item);
    }
    return map;
  }

  // ---------------------------------------------------------------- nav

  void _openProfile(String username) {
    if (username.trim().isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Error: This user does not have a valid username!'),
          backgroundColor: Colors.redAccent,
        ),
      );
      return;
    }
    _rememberSearch(_searchController.text);
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => TargetProfilePage(username: username)),
    );
  }

  /// `assigments`/`testseries`/`message`/`campus_notice` results don't
  /// have a per-item deep-link route confirmed yet — the screens that
  /// would open them (`assignments_screen.dart`, `test_series_screen.dart`,
  /// `campus_screen.dart`, `conversations_screen.dart`) weren't part of
  /// this pass, and guessing their constructor signatures risks a build
  /// that doesn't compile. Showing the full result here instead of
  /// guessing a route — wire real navigation once those screens'
  /// "open by id" signatures are confirmed.
  void _showResultDetail(SearchResultItem item, AppLocalizations l10n, ColorScheme cs) {
    _rememberSearch(_searchController.text);
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (_) => Padding(
        padding: EdgeInsets.fromLTRB(
          20, 20, 20, MediaQuery.of(context).viewInsets.bottom + 24,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Icon(iconForSource(item.source), color: cs.primary, size: 20),
              const SizedBox(width: 8),
              Text(_sectionLabel(item.source, l10n),
                  style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.primary)),
            ]),
            const SizedBox(height: 12),
            Text(item.title,
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: cs.onSurface)),
            if (item.snippet.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(item.snippet, style: TextStyle(fontSize: 13.5, height: 1.4, color: cs.onSurfaceVariant)),
            ],
            if (item.createdAt != null) ...[
              const SizedBox(height: 10),
              Text(
                timeago.format(item.createdAt!, locale: Localizations.localeOf(context).languageCode),
                style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
              ),
            ],
            const SizedBox(height: 14),
            Text(l10n.searchDetailUnavailable,
                style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant, fontStyle: FontStyle.italic)),
          ],
        ),
      ),
    );
  }

  String _sectionLabel(String source, AppLocalizations l10n) {
    switch (source) {
      case 'campus_notice':
        return l10n.searchSectionNotices;
      case 'assigments':
        return l10n.searchSectionAssignments;
      case 'testseries':
        return l10n.searchSectionTests;
      case 'message':
        return l10n.searchSectionMessages;
      default:
        return source;
    }
  }

  // --------------------------------------------------------------- build

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            _buildSearchBar(cs, l10n),
            _buildFilterChips(cs, l10n),
            const SizedBox(height: 4),
            Expanded(child: _buildBody(cs, l10n)),
          ],
        ),
      ),
    );
  }

  Widget _buildSearchBar(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
      child: Container(
        height: 46,
        padding: const EdgeInsets.symmetric(horizontal: 14),
        decoration: BoxDecoration(
          color: cs.surfaceVariant,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: cs.outlineVariant),
        ),
        child: Row(children: [
          Icon(Icons.search_rounded, size: 18, color: cs.onSurfaceVariant),
          const SizedBox(width: 8),
          Expanded(
            child: TextField(
              controller: _searchController,
              focusNode: _focusNode,
              onChanged: _onQueryChanged,
              onSubmitted: (v) {
                if (v.trim().isNotEmpty) _rememberSearch(v);
              },
              style: TextStyle(fontSize: 14, color: cs.onSurface),
              decoration: InputDecoration(
                isCollapsed: true,
                border: InputBorder.none,
                hintText: l10n.searchHint,
                hintStyle: TextStyle(fontSize: 13.5, color: cs.onSurfaceVariant),
              ),
            ),
          ),
          if (_searchController.text.isNotEmpty)
            GestureDetector(
              onTap: () {
                _searchController.clear();
                _onQueryChanged('');
              },
              child: Icon(Icons.clear_rounded, size: 18, color: cs.onSurfaceVariant),
            ),
        ]),
      ),
    );
  }

  Widget _buildFilterChips(ColorScheme cs, AppLocalizations l10n) {
    final chips = <MapEntry<SearchFilter, String>>[
      MapEntry(SearchFilter.all, l10n.searchFilterAll),
      MapEntry(SearchFilter.people, l10n.searchFilterPeople),
      MapEntry(SearchFilter.notices, l10n.searchFilterNotices),
      MapEntry(SearchFilter.assignments, l10n.searchFilterAssignments),
      MapEntry(SearchFilter.tests, l10n.searchFilterTests),
      MapEntry(SearchFilter.messages, l10n.searchFilterMessages),
    ];
    return SizedBox(
      height: 36,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        itemCount: chips.length,
        separatorBuilder: (_, __) => const SizedBox(width: 8),
        itemBuilder: (_, i) {
          final filter = chips[i].key;
          final label = chips[i].value;
          final selected = filter == _filter;
          return ChoiceChip(
            selected: selected,
            onSelected: (_) => _onFilterSelected(filter),
            label: Text(label),
            avatar: Icon(filter.icon, size: 15, color: selected ? cs.onPrimary : cs.onSurfaceVariant),
            selectedColor: cs.primary,
            backgroundColor: cs.surfaceVariant,
            labelStyle: TextStyle(
              fontSize: 12.5,
              fontWeight: FontWeight.w600,
              color: selected ? cs.onPrimary : cs.onSurface,
            ),
            side: BorderSide(color: selected ? cs.primary : cs.outlineVariant),
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 0),
          );
        },
      ),
    );
  }

  Widget _buildBody(ColorScheme cs, AppLocalizations l10n) {
    if (_searchController.text.trim().isEmpty) {
      return _buildRecentAndEmpty(cs, l10n);
    }
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }

    final hasPeople = _people.isNotEmpty;
    final hasResults = _results.isNotEmpty;
    if (!hasPeople && !hasResults) {
      return _buildNoResults(cs, l10n);
    }

    if (_filter == SearchFilter.people) {
      return ListView.builder(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        itemCount: _people.length,
        itemBuilder: (_, i) => _buildPersonTile(_people[i], cs),
      );
    }

    if (_filter != SearchFilter.all) {
      return ListView.builder(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        itemCount: _results.length,
        itemBuilder: (_, i) => _buildResultTile(_results[i], cs, l10n),
      );
    }

    // "All" — sectioned, Instagram-style.
    final grouped = _groupBySource(_results);
    final sectionOrder = ['campus_notice', 'assigments', 'testseries', 'message'];
    return ListView(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      children: [
        if (hasPeople) ...[
          _sectionHeader(l10n.searchSectionPeople, cs),
          ..._people.map((u) => _buildPersonTile(u, cs)),
          const SizedBox(height: 8),
        ],
        for (final source in sectionOrder)
          if ((grouped[source] ?? []).isNotEmpty) ...[
            _sectionHeader(_sectionLabel(source, l10n), cs),
            ...grouped[source]!.map((item) => _buildResultTile(item, cs, l10n)),
            const SizedBox(height: 8),
          ],
      ],
    );
  }

  Widget _sectionHeader(String label, ColorScheme cs) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6, top: 4),
      child: Text(
        label,
        style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant),
      ),
    );
  }

  Widget _buildRecentAndEmpty(ColorScheme cs, AppLocalizations l10n) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
      children: [
        if (_recent.isNotEmpty) ...[
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(l10n.searchRecentTitle,
                  style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: cs.onSurface)),
              GestureDetector(
                onTap: _clearRecent,
                child: Text(l10n.searchRecentClear,
                    style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: cs.primary)),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: _recent.map((term) {
              return InputChip(
                label: Text(term, style: TextStyle(fontSize: 12.5, color: cs.onSurface)),
                avatar: Icon(Icons.history_rounded, size: 15, color: cs.onSurfaceVariant),
                backgroundColor: cs.surfaceVariant,
                side: BorderSide(color: cs.outlineVariant),
                onPressed: () {
                  _searchController.text = term;
                  _searchController.selection =
                      TextSelection.fromPosition(TextPosition(offset: term.length));
                  _runSearch(term);
                },
                onDeleted: () => _removeRecent(term),
                deleteIcon: Icon(Icons.close_rounded, size: 14, color: cs.onSurfaceVariant),
              );
            }).toList(),
          ),
          const SizedBox(height: 24),
        ],
        Padding(
          padding: const EdgeInsets.only(top: 40),
          child: Column(children: [
            Icon(Icons.travel_explore_rounded, size: 72, color: cs.outlineVariant),
            const SizedBox(height: 12),
            Text(
              l10n.searchEmptyPrompt,
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 13.5, color: cs.onSurfaceVariant),
            ),
          ]),
        ),
      ],
    );
  }

  Widget _buildNoResults(ColorScheme cs, AppLocalizations l10n) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.search_off_rounded, size: 70, color: cs.outlineVariant),
          const SizedBox(height: 10),
          Text(
            l10n.searchNoResultsFor(_searchController.text.trim()),
            textAlign: TextAlign.center,
            style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14, color: cs.onSurfaceVariant),
          ),
        ],
      ),
    );
  }

  Widget _buildPersonTile(dynamic user, ColorScheme cs) {
    final String username = user['username'] ?? '';
    final String firstName = user['first_name'] ?? '';
    final String lastName = user['last_name'] ?? '';
    final String? photoPath = user['profile_photo'];

    String fullImageUrl = '';
    if (photoPath != null && photoPath.isNotEmpty) {
      fullImageUrl = photoPath.startsWith('http') ? photoPath : '${Api.baseUrl}$photoPath';
    }

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: cs.outlineVariant),
      ),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
        dense: true,
        leading: CircleAvatar(
          radius: 22,
          backgroundColor: cs.surfaceVariant,
          child: ClipOval(
            child: photoPath == null || photoPath.isEmpty
                ? Icon(Icons.person, size: 26, color: cs.onSurfaceVariant)
                : CachedNetworkImage(
                    imageUrl: fullImageUrl,
                    width: 44,
                    height: 44,
                    fit: BoxFit.cover,
                    placeholder: (c, u) => const CircularProgressIndicator(strokeWidth: 2),
                    errorWidget: (c, u, e) => Icon(Icons.person, color: cs.onSurfaceVariant),
                  ),
          ),
        ),
        title: Text(username,
            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15, color: cs.onSurface)),
        subtitle: Text(
          (firstName.isEmpty && lastName.isEmpty) ? 'No name provided' : '$firstName $lastName'.trim(),
          style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13),
        ),
        trailing: Icon(Icons.arrow_forward_ios, size: 16, color: cs.onSurfaceVariant),
        onTap: () => _openProfile(username),
      ),
    );
  }

  Widget _buildResultTile(SearchResultItem item, ColorScheme cs, AppLocalizations l10n) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        color: cs.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: cs.outlineVariant),
      ),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
        dense: true,
        leading: CircleAvatar(
          radius: 20,
          backgroundColor: cs.primaryContainer,
          child: Icon(iconForSource(item.source), size: 18, color: cs.primary),
        ),
        title: Text(
          item.title.isEmpty ? _sectionLabel(item.source, l10n) : item.title,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14.5, color: cs.onSurface),
        ),
        subtitle: item.snippet.isEmpty
            ? null
            : Text(
                item.snippet,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: cs.onSurfaceVariant, fontSize: 12.5),
              ),
        trailing: item.createdAt == null
            ? null
            : Text(
                timeago.format(item.createdAt!, locale: Localizations.localeOf(context).languageCode),
                style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant),
              ),
        onTap: () => _showResultDetail(item, l10n, cs),
      ),
    );
  }
}