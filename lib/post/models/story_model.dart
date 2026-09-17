// lib/post/models/story_model.dart
//
// Task 4 — Stories: read-side models.
//
// `services/home_api_model_service.dart` and `post/services/
// story_service.dart` both carried a comment saying `StoryModel`/
// `StoryGroup`/`groupStories()` "moved" here — but the move was never
// actually done, this file never existed. Nothing defined these three,
// which broke every caller:
//   - home.dart:            `List<StoryModel> _stories`, `groupStories(_stories)`
//   - story_viewer_screen.dart: `import '...home_api_model_service.dart'
//                             show StoryGroup, StoryModel;` (not exported
//                             there anymore per that file's own comment)
// This file is the actual move target.
//
// Reuses `UserModel` from home_api_model_service.dart (id/username/
// profilePicture, with the same multi-key profile-pic fallback already
// established there) instead of duplicating a second user shape.

import '../../services/home_api_model_service.dart' show UserModel;

class StoryModel {
  final String id;
  final UserModel user;
  final String mediaType; // "image" | "video"
  final String? mediaUrl;
  final String? thumbnail;
  final String? caption;
  final DateTime? createdAt;
  final DateTime? expiresAt;
  final bool isViewed;
  final int viewsCount;

  StoryModel({
    required this.id,
    required this.user,
    required this.mediaType,
    this.mediaUrl,
    this.thumbnail,
    this.caption,
    this.createdAt,
    this.expiresAt,
    required this.isViewed,
    this.viewsCount = 0,
  });

  factory StoryModel.fromJson(Map<String, dynamic> json) {
    final userJson = json['user'];
    return StoryModel(
      id: json['id']?.toString() ?? '',
      user: userJson is Map
          ? UserModel.fromJson(Map<String, dynamic>.from(userJson))
          : UserModel(id: '', username: ''),
      mediaType: (json['media_type'] ?? 'image').toString(),
      // Backend key for the story's own file isn't nailed down across the
      // docs the same way post-media's `file` is — accept the reasonable
      // alternates defensively, same fallback style `UserModel.fromJson`
      // already uses for profile pic, rather than assuming one and
      // silently rendering a broken-image icon if it's actually another.
      mediaUrl: (json['media'] ?? json['file'] ?? json['media_url'])?.toString(),
      thumbnail: json['thumbnail']?.toString(),
      caption: json['caption']?.toString(),
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? ''),
      expiresAt: DateTime.tryParse(json['expires_at']?.toString() ?? ''),
      isViewed: (json['is_viewed'] as bool?) ?? (json['viewed'] as bool?) ?? false,
      // Same key `story_service.dart`'s markViewed() response already
      // reads (`views_count`) — the list endpoint is expected to echo it
      // per-story too, same convention `PostModel.viewsCount` uses.
      viewsCount: (json['views_count'] as int?) ?? 0,
    );
  }
}

/// Task 11 — one entry in "who viewed my story". Only meaningful for the
/// caller's own stories; the list endpoint this comes from is unconfirmed
/// (see the flag comment on `StoryService.getStoryViewers()`).
class StoryViewerEntry {
  final String userId;
  final String username;
  final String? profilePicture;
  final DateTime? viewedAt;

  StoryViewerEntry({
    required this.userId,
    required this.username,
    this.profilePicture,
    this.viewedAt,
  });

  factory StoryViewerEntry.fromJson(Map<String, dynamic> json) {
    // Two plausible shapes depending on how the backend nests it: a flat
    // user-ish object, or {"user": {...}, "viewed_at": ...}. Handle both
    // rather than guessing wrong and silently dropping every row.
    final userJson = json['user'];
    final user = userJson is Map ? Map<String, dynamic>.from(userJson) : json;
    return StoryViewerEntry(
      userId: (user['id'] ?? '').toString(),
      username: (user['username'] ?? '').toString(),
      profilePicture: (user['profilePicture'] ?? user['profile_picture'] ?? user['profile_photo'])?.toString(),
      viewedAt: DateTime.tryParse((json['viewed_at'] ?? json['created_at'] ?? '').toString()),
    );
  }
}

class StoryGroup {
  final String userId;
  final String username;
  final String? userProfilePic;
  final List<StoryModel> stories;

  StoryGroup({
    required this.userId,
    required this.username,
    this.userProfilePic,
    required this.stories,
  });

  /// Ring turns plain grey once every story in the group has been seen
  /// (Instagram's read/unread convention) — `home.dart` reads this
  /// directly per-group to pick the ring style.
  bool get allViewed => stories.isNotEmpty && stories.every((s) => s.isViewed);
}

/// Groups the flat `StoryService.getStories()` list into one `StoryGroup`
/// per user — one ring = one user's active stories, oldest first within
/// the group (the order they were actually posted in, so the viewer plays
/// them back the same way Instagram does).
///
/// Defensively drops anything already past its `expires_at` — the backend
/// list endpoint is expected to only return active stories, but a story
/// can expire in the gap between the request going out and the row
/// rendering, and `story_viewer_screen.dart`'s own header comment already
/// assumes this function is the one guaranteeing "active, non-expired"
/// per group, so the guard belongs here rather than being silently
/// assumed.
List<StoryGroup> groupStories(List<StoryModel> stories) {
  final now = DateTime.now();
  final active = stories.where((s) => s.expiresAt == null || s.expiresAt!.isAfter(now)).toList();

  final order = <String>[];
  final byUser = <String, List<StoryModel>>{};
  for (final s in active) {
    final uid = s.user.id;
    if (!byUser.containsKey(uid)) {
      order.add(uid);
      byUser[uid] = [];
    }
    byUser[uid]!.add(s);
  }

  return order.map((uid) {
    final userStories = byUser[uid]!
      ..sort((a, b) {
        final at = a.createdAt;
        final bt = b.createdAt;
        if (at == null && bt == null) return 0;
        if (at == null) return 1;
        if (bt == null) return -1;
        return at.compareTo(bt);
      });
    final first = userStories.first;
    return StoryGroup(
      userId: uid,
      username: first.user.username,
      userProfilePic: first.user.profilePicture,
      stories: userStories,
    );
  }).toList();
}