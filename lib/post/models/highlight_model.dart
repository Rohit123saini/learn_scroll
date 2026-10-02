// lib/post/models/highlight_model.dart
//
// Story Highlights. Shape confirmed against `post/highlights.py::
// build_highlight_rows` + `post/highlight_views.py::_detail_payload`:
//
//   row    = {id, title, cover_url, cover_story_id, items_count,
//             created_at, updated_at, user: {id, username, profile_picture}}
//   detail = row + {is_owner, stories: [StorySerializer, ..]}
//
// `cover_url` is null for an all-video highlight (no still to show) and
// `items_count` only counts the items the CALLER may see.

import 'story_model.dart' show StoryModel;

class Highlight {
  final String id;
  final String title;
  final String? coverUrl;
  final String? coverStoryId; // effective cover (explicit OR automatic)
  final int itemsCount;
  final bool isOwner; // detail endpoint only (false on list rows)
  final DateTime? updatedAt;

  final String ownerId;
  final String ownerUsername;
  final String? ownerPicture;

  /// Detail endpoint only: the stories this viewer may see, in order.
  final List<StoryModel> stories;

  const Highlight({
    required this.id,
    required this.title,
    this.coverUrl,
    this.coverStoryId,
    this.itemsCount = 0,
    this.isOwner = false,
    this.updatedAt,
    this.ownerId = '',
    this.ownerUsername = '',
    this.ownerPicture,
    this.stories = const [],
  });

  factory Highlight.fromJson(Map<String, dynamic> j) {
    final user = j['user'] is Map ? Map<String, dynamic>.from(j['user'] as Map) : const <String, dynamic>{};
    final rawStories = j['stories'];
    final cover = j['cover_url'];
    return Highlight(
      id: j['id'].toString(),
      title: (j['title'] ?? '').toString(),
      coverUrl: cover is String && cover.isNotEmpty ? cover : null,
      coverStoryId: j['cover_story_id']?.toString(),
      itemsCount: j['items_count'] is int ? j['items_count'] as int : int.tryParse('${j['items_count']}') ?? 0,
      isOwner: j['is_owner'] == true,
      updatedAt: DateTime.tryParse('${j['updated_at'] ?? ''}'),
      ownerId: (user['id'] ?? '').toString(),
      ownerUsername: (user['username'] ?? '').toString(),
      ownerPicture: user['profile_picture'] is String && (user['profile_picture'] as String).isNotEmpty
          ? user['profile_picture'] as String
          : null,
      stories: rawStories is List
          ? rawStories.whereType<Map>().map((e) => StoryModel.fromJson(Map<String, dynamic>.from(e))).toList()
          : const [],
    );
  }
}
