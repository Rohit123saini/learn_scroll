// lib/notices/models/notice_board_models.dart
//
// Backend: `core` app — `NoticeBoardView` (Task 12). GET core/notice-board/
// merges TWO already-existing notice sources into one feed:
//   - campus.Notice   (campus/department/class/section-scoped)
//   - tuitionclass.Notice (per-classroom)
//
// `fromJson` matches that view's merged-dict shape exactly — see its
// docstring in core/views.py for the field list. Reuses `MinimalUser`
// from campus_models.dart rather than redefining the same
// {id, username, first_name, last_name} shape a second time.

import '../../campus/models/campus_models.dart' show MinimalUser;

enum NoticeBoardSource { campus, tuitionClass }

NoticeBoardSource _sourceFromString(String? v) {
  switch (v) {
    case 'tuition_class':
      return NoticeBoardSource.tuitionClass;
    case 'campus':
    default:
      return NoticeBoardSource.campus;
  }
}

DateTime? _date(dynamic v) {
  if (v == null) return null;
  return DateTime.tryParse(v.toString());
}

class NoticeBoardItem {
  final String id;
  final NoticeBoardSource source;
  final String title;
  final String body;
  final bool isPinned;
  final DateTime? createdAt;
  final MinimalUser? postedBy;

  /// Campus name (source == campus) ya classroom title (source ==
  /// tuitionClass) — jahan se ye notice aaya.
  final String contextLabel;

  /// Campus notices ke liye hi: "section"/"class"/"department"/"campus".
  /// tuition_class rows ke liye hamesha null (classroom se badi koi scope
  /// nahi hoti).
  final String? scopeLabel;

  const NoticeBoardItem({
    required this.id,
    required this.source,
    required this.title,
    required this.body,
    required this.isPinned,
    required this.contextLabel,
    this.createdAt,
    this.postedBy,
    this.scopeLabel,
  });

  factory NoticeBoardItem.fromJson(Map<String, dynamic> j) => NoticeBoardItem(
        id: j['id'].toString(),
        source: _sourceFromString(j['source'] as String?),
        title: (j['title'] ?? '').toString(),
        body: (j['body'] ?? '').toString(),
        isPinned: j['is_pinned'] == true,
        createdAt: _date(j['created_at']),
        postedBy: (j['posted_by'] is Map)
            ? MinimalUser.fromJson(Map<String, dynamic>.from(j['posted_by'] as Map))
            : null,
        contextLabel: (j['context_label'] ?? '').toString(),
        scopeLabel: j['scope_label']?.toString(),
      );
}

class NoticeBoardPage {
  final int count;
  final List<NoticeBoardItem> results;

  const NoticeBoardPage({required this.count, required this.results});

  factory NoticeBoardPage.fromJson(Map<String, dynamic> j) => NoticeBoardPage(
        count: (j['count'] as num?)?.toInt() ?? 0,
        results: ((j['results'] as List?) ?? const [])
            .map((e) => NoticeBoardItem.fromJson(Map<String, dynamic>.from(e as Map)))
            .toList(),
      );
}
