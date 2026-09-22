import 'package:flutter/material.dart';

/// One row from `GET /core/search/` (`core.search.search_everything()`),
/// see `core_app_documentation.md` §6.2 for the exact response-dict shape:
/// `{source, id, title, snippet, created_at, rank, similarity, extra}`.
///
/// `source` is one of `core`'s registered `SOURCES` keys — as of this
/// pass that's `assigments` | `testseries` | `message` | `campus_notice`
/// | `user` | `friend`. `post` / `class_material` are documented STUBS
/// on the backend (no model wired yet), so they never come back here —
/// don't request them.
class SearchResultItem {
  final String source;
  final String id;
  final String title;
  final String snippet;
  final DateTime? createdAt;
  final double? rank;
  final double? similarity;
  final Map<String, dynamic> extra;

  const SearchResultItem({
    required this.source,
    required this.id,
    required this.title,
    required this.snippet,
    this.createdAt,
    this.rank,
    this.similarity,
    this.extra = const {},
  });

  factory SearchResultItem.fromJson(Map<String, dynamic> json) {
    return SearchResultItem(
      source: (json['source'] ?? '').toString(),
      id: (json['id'] ?? '').toString(),
      title: (json['title'] ?? '').toString(),
      snippet: (json['snippet'] ?? '').toString(),
      createdAt: DateTime.tryParse((json['created_at'] ?? '').toString()),
      rank: (json['rank'] as num?)?.toDouble(),
      similarity: (json['similarity'] as num?)?.toDouble(),
      extra: json['extra'] is Map
          ? Map<String, dynamic>.from(json['extra'] as Map)
          : const {},
    );
  }
}

/// The filter chips on the search screen. `all`/`people` are UI-only —
/// `all` means "don't send ?sources for the name-search sources, plus
/// call people-search separately"; `people` is served entirely by
/// `/profile/search/` (kept as its own dedicated call since it already
/// covers the full user base with no extra backend work needed — see
/// `SearchApiService.searchUsers`). `friends` IS a real `/core/search/`
/// source (`core.search.FRIEND_SOURCE`) — search scoped to people the
/// caller follows or is followed by.
enum SearchFilter { all, people, friends, notices, assignments, tests, messages }

extension SearchFilterX on SearchFilter {
  /// `?sources=` value for `/core/search/`. Null for `all`/`people`
  /// (handled specially by the caller — see class docstring above).
  String? get backendSource {
    switch (this) {
      case SearchFilter.friends:
        return 'friend';
      case SearchFilter.notices:
        return 'campus_notice';
      case SearchFilter.assignments:
        // Backend's own spelling (core/views.py, testseries/models.py
        // both use it) — not a typo on our side, must match verbatim.
        return 'assigments';
      case SearchFilter.tests:
        return 'testseries';
      case SearchFilter.messages:
        return 'message';
      case SearchFilter.all:
      case SearchFilter.people:
        return null;
    }
  }

  IconData get icon {
    switch (this) {
      case SearchFilter.all:
        return Icons.apps_rounded;
      case SearchFilter.people:
        return Icons.person_search_rounded;
      case SearchFilter.friends:
        return Icons.group_rounded;
      case SearchFilter.notices:
        return Icons.campaign_rounded;
      case SearchFilter.assignments:
        return Icons.assignment_rounded;
      case SearchFilter.tests:
        return Icons.task_alt_rounded;
      case SearchFilter.messages:
        return Icons.chat_bubble_outline_rounded;
    }
  }
}

/// Icon for a raw backend `source` string (used once results already
/// have a source key attached, e.g. in the "All" sectioned view).
IconData iconForSource(String source) {
  switch (source) {
    case 'campus_notice':
      return Icons.campaign_rounded;
    case 'assigments':
      return Icons.assignment_rounded;
    case 'testseries':
      return Icons.task_alt_rounded;
    case 'message':
      return Icons.chat_bubble_outline_rounded;
    case 'friend':
      return Icons.group_rounded;
    case 'user':
      return Icons.person_search_rounded;
    default:
      return Icons.search_rounded;
  }
}