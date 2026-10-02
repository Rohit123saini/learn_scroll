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
  // Stories upgrade, Part 1 — who can see this story: "everyone" (all
  // followers) or "close_friends" (owner's Close Friends list only).
  final String audience;
  // Stories upgrade, Part 2 — overlays placed on the story (mention / link /
  // poll / question), lowest z-index first. Empty for old stories.
  final List<StorySticker> stickers;
  // Who reacted to this story (owner-view only) — NOT part of the
  // `stories/` list response (that would leak reactions to non-owners).
  // Populated after the fact by `StoryService.getStoryViewers()` when the
  // viewer screen is opened by the story's own owner; empty otherwise.
  List<StoryViewerEntry> reactions;

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
    this.audience = 'everyone',
    List<StorySticker>? stickers,
    List<StoryViewerEntry>? reactions,
  })  : stickers = stickers ?? const [],
        reactions = reactions ?? [];

  bool get isCloseFriends => audience == 'close_friends';

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
      // Backend `StorySerializer.is_viewed_by_me` (older guesses kept as fallbacks).
      isViewed: (json['is_viewed_by_me'] as bool?) ?? (json['is_viewed'] as bool?) ?? (json['viewed'] as bool?) ?? false,
      // Same key `story_service.dart`'s markViewed() response already
      // reads (`views_count`) — the list endpoint is expected to echo it
      // per-story too, same convention `PostModel.viewsCount` uses.
      viewsCount: (json['views_count'] as int?) ?? 0,
      audience: (json['audience'] ?? 'everyone').toString(),
      stickers: (json['stickers'] is List)
          ? (json['stickers'] as List)
              .whereType<Map>()
              .map((e) => StorySticker.fromJson(Map<String, dynamic>.from(e)))
              .toList()
          : const [],
    );
  }
}

/// Stories upgrade, Part 2 — ONE overlay on top of a story. Mention, link,
/// poll and question all share the same placement (x/y = centre as a 0..1
/// fraction of the story canvas, rotation in degrees, scale, z-index); only
/// `data` differs per kind. Mirrors `StoryStickerSerializer` on the backend:
///
///   mention  -> {user: {id, username, profile_picture}}
///   link     -> {url, label, host}
///   poll     -> {question, options: [..], my_vote: int?, results: {counts: [..], total: n}?}
///   question -> {prompt, my_answered: bool, answers_count: n?}   (count = owner only)
///
/// `data` is replaced in place by [applyServerJson] after a vote / answer, so
/// the viewer can rebuild without refetching the whole story list.
class StorySticker {
  static const String kMention = 'mention';
  static const String kLink = 'link';
  static const String kPoll = 'poll';
  static const String kQuestion = 'question';

  final String id;
  final String kind;
  final double x;
  final double y;
  final double rotation;
  final double scale;
  final int zIndex;
  Map<String, dynamic> data;

  StorySticker({
    required this.id,
    required this.kind,
    this.x = 0.5,
    this.y = 0.5,
    this.rotation = 0,
    this.scale = 1,
    this.zIndex = 0,
    Map<String, dynamic>? data,
  }) : data = data ?? <String, dynamic>{};

  static double _d(dynamic v, double fallback) => v is num ? v.toDouble() : fallback;

  factory StorySticker.fromJson(Map<String, dynamic> json) {
    final raw = json['data'];
    return StorySticker(
      id: json['id']?.toString() ?? '',
      kind: (json['kind'] ?? '').toString(),
      x: _d(json['x'], 0.5).clamp(0.0, 1.0).toDouble(),
      y: _d(json['y'], 0.5).clamp(0.0, 1.0).toDouble(),
      rotation: _d(json['rotation'], 0),
      scale: _d(json['scale'], 1).clamp(0.4, 4.0).toDouble(),
      zIndex: (json['z_index'] as num?)?.toInt() ?? 0,
      data: raw is Map ? Map<String, dynamic>.from(raw) : <String, dynamic>{},
    );
  }

  /// Replace the kind-specific state with a fresh sticker JSON from the server
  /// (the vote / answer endpoints return `{"sticker": {...}}`).
  void applyServerJson(Map<String, dynamic> stickerJson) {
    final raw = stickerJson['data'];
    if (raw is Map) data = Map<String, dynamic>.from(raw);
  }

  // ---- mention ----
  Map<String, dynamic>? get _user => data['user'] is Map ? Map<String, dynamic>.from(data['user'] as Map) : null;
  String? get mentionUserId => _user?['id']?.toString();
  String? get mentionUsername => _user?['username']?.toString();
  String? get mentionProfilePicture => _user?['profile_picture']?.toString();

  // ---- link ----
  String get url => (data['url'] ?? '').toString();
  String get label => (data['label'] ?? '').toString();
  String get host => (data['host'] ?? '').toString();

  // ---- poll ----
  String get pollQuestion => (data['question'] ?? '').toString();
  List<String> get pollOptions =>
      (data['options'] is List) ? (data['options'] as List).map((e) => e.toString()).toList() : const [];
  int? get myVote => (data['my_vote'] as num?)?.toInt();
  Map<String, dynamic>? get _results => data['results'] is Map ? Map<String, dynamic>.from(data['results'] as Map) : null;

  /// Null until the viewer has voted (or when they are the owner: always set).
  List<int>? get pollCounts {
    final r = _results;
    if (r == null || r['counts'] is! List) return null;
    return (r['counts'] as List).map((e) => (e as num?)?.toInt() ?? 0).toList();
  }

  int get pollTotal => (_results?['total'] as num?)?.toInt() ?? 0;
  bool get hasPollResults => pollCounts != null;

  // ---- question ----
  String get prompt => (data['prompt'] ?? '').toString();
  bool get myAnswered => data['my_answered'] == true;
  int? get answersCount => (data['answers_count'] as num?)?.toInt(); // owner only
}

/// Task 11 — one entry in "who viewed my story". Only meaningful for the
/// caller's own stories; the list endpoint this comes from is unconfirmed
/// (see the flag comment on `StoryService.getStoryViewers()`).
class StoryViewerEntry {
  final String userId;
  final String username;
  final String? profilePicture;
  final DateTime? viewedAt;
  // Reactions/replies — owner-only "who reacted" list
  // (`GET /post/story/<id>/viewers/`, `StoryViewerEntrySerializer`).
  // Null means this viewer hasn't reacted.
  final String? reactionEmoji;
  final DateTime? reactedAt;

  StoryViewerEntry({
    required this.userId,
    required this.username,
    this.profilePicture,
    this.viewedAt,
    this.reactionEmoji,
    this.reactedAt,
  });

  factory StoryViewerEntry.fromJson(Map<String, dynamic> json) {
    // Two plausible shapes depending on how the backend nests it: a flat
    // user-ish object, or {"user": {...}, "viewed_at": ...}. Handle both
    // rather than guessing wrong and silently dropping every row.
    final userJson = json['user'];
    final user = userJson is Map ? Map<String, dynamic>.from(userJson) : json;
    final reactionJson = json['reaction'];
    final reaction = reactionJson is Map ? Map<String, dynamic>.from(reactionJson) : null;
    return StoryViewerEntry(
      userId: (user['id'] ?? '').toString(),
      username: (user['username'] ?? '').toString(),
      profilePicture: (user['profilePicture'] ?? user['profile_picture'] ?? user['profile_photo'])?.toString(),
      viewedAt: DateTime.tryParse((json['viewed_at'] ?? json['created_at'] ?? '').toString()),
      reactionEmoji: reaction?['emoji']?.toString(),
      reactedAt: reaction != null ? DateTime.tryParse(reaction['created_at']?.toString() ?? '') : null,
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

  /// Green-ring rule (Instagram): the ring is green while there is still an
  /// UNSEEN Close Friends story in the group. Once seen it goes grey like any
  /// other ring, so `allViewed` still wins for the "viewed" state.
  bool get hasUnviewedCloseFriends => stories.any((s) => s.isCloseFriends && !s.isViewed);
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


// ─────────────────────────────────────────────────────────────────────────
// Composer side — a sticker the user is placing but has not posted yet.
// Limits mirror post/story_stickers.py (the server re-checks everything).
// ─────────────────────────────────────────────────────────────────────────
const int kStoryMaxStickers = 10;
const int kStoryMaxMentions = 5;
const int kStoryMaxLinks = 1;
const int kStoryMaxPolls = 1;
const int kStoryMaxQuestions = 1;
const int kStoryPollMinOptions = 2;
const int kStoryPollMaxOptions = 4;
const int kStoryPollQuestionMax = 100;
const int kStoryPollOptionMax = 25;
const int kStoryQuestionPromptMax = 100;
const int kStoryLinkLabelMax = 40;
const int kStoryAnswerMax = 300;

class StickerDraft {
  static int _seq = 0;

  final String localId;
  final String kind; // StorySticker.kMention | kLink | kPoll | kQuestion
  double x;
  double y;
  double rotation;
  double scale;

  // mention
  final int? userId;
  final String? username;
  final String? profilePicture;
  // link
  final String url;
  final String label;
  // poll
  final String question;
  final List<String> options;
  // question
  final String prompt;

  StickerDraft._({
    required this.kind,
    this.x = 0.5,
    this.y = 0.5,
    this.rotation = 0,
    this.scale = 1,
    this.userId,
    this.username,
    this.profilePicture,
    this.url = '',
    this.label = '',
    this.question = '',
    this.options = const [],
    this.prompt = '',
  }) : localId = 'draft_${_seq++}';

  factory StickerDraft.mention({required int userId, required String username, String? profilePicture, double y = 0.35}) =>
      StickerDraft._(kind: StorySticker.kMention, userId: userId, username: username, profilePicture: profilePicture, y: y);

  factory StickerDraft.link({required String url, String label = '', double y = 0.8}) =>
      StickerDraft._(kind: StorySticker.kLink, url: url.trim(), label: label.trim(), y: y);

  factory StickerDraft.poll({required String question, required List<String> options, double y = 0.6}) =>
      StickerDraft._(kind: StorySticker.kPoll, question: question.trim(), options: options.map((o) => o.trim()).toList(), y: y);

  factory StickerDraft.question({required String prompt, double y = 0.25}) =>
      StickerDraft._(kind: StorySticker.kQuestion, prompt: prompt.trim(), y: y);

  /// One element of the `stickers` JSON list of POST /post/stories/create/.
  Map<String, dynamic> toJson(int zIndex) {
    final base = <String, dynamic>{
      'kind': kind,
      'x': double.parse(x.clamp(0.0, 1.0).toStringAsFixed(4)),
      'y': double.parse(y.clamp(0.0, 1.0).toStringAsFixed(4)),
      'rotation': double.parse(rotation.clamp(-180.0, 180.0).toStringAsFixed(2)),
      'scale': double.parse(scale.clamp(0.4, 4.0).toStringAsFixed(3)),
      'z_index': zIndex,
    };
    switch (kind) {
      case StorySticker.kMention:
        base['user_id'] = userId;
        break;
      case StorySticker.kLink:
        base['url'] = url;
        if (label.isNotEmpty) base['label'] = label;
        break;
      case StorySticker.kPoll:
        base['question'] = question;
        base['options'] = options;
        break;
      case StorySticker.kQuestion:
        base['prompt'] = prompt;
        break;
    }
    return base;
  }

  /// The same overlay as a read-side [StorySticker], so the composer preview
  /// renders through exactly the widgets the viewer uses.
  StorySticker toPreview() {
    final Map<String, dynamic> data;
    switch (kind) {
      case StorySticker.kMention:
        data = {'user': {'id': '$userId', 'username': username, 'profile_picture': profilePicture}};
        break;
      case StorySticker.kLink:
        data = {'url': url, 'label': label, 'host': _hostOf(url)};
        break;
      case StorySticker.kPoll:
        data = {'question': question, 'options': options, 'my_vote': null, 'results': null};
        break;
      default:
        data = {'prompt': prompt, 'my_answered': false, 'answers_count': null};
    }
    return StorySticker(id: localId, kind: kind, x: x, y: y, rotation: rotation, scale: scale, data: data);
  }

  static String _hostOf(String raw) {
    final u = Uri.tryParse(raw.contains('://') ? raw : 'https://$raw');
    return u?.host ?? raw;
  }
}

/// A person the composer can @mention (`GET /post/stories/mention-candidates/`).
class StoryMentionCandidate {
  final int id;
  final String username;
  final String name;
  final String? profilePicture;

  const StoryMentionCandidate({required this.id, required this.username, required this.name, this.profilePicture});

  factory StoryMentionCandidate.fromJson(Map<String, dynamic> json) => StoryMentionCandidate(
        id: (json['id'] as num?)?.toInt() ?? int.tryParse('${json['id']}') ?? 0,
        username: (json['username'] ?? '').toString(),
        name: (json['name'] ?? json['username'] ?? '').toString(),
        profilePicture: json['profile_picture']?.toString(),
      );
}

/// One row of the owner-only responses list: a poll vote (`optionLabel` set)
/// or a question answer (`text` set).
class StickerResponseRow {
  final String id;
  final String username;
  final String? profilePicture;
  final String? optionLabel;
  final int? optionIndex;
  final String? text;
  final DateTime? createdAt;

  const StickerResponseRow({
    required this.id,
    required this.username,
    this.profilePicture,
    this.optionLabel,
    this.optionIndex,
    this.text,
    this.createdAt,
  });

  factory StickerResponseRow.fromJson(Map<String, dynamic> json) {
    final u = json['user'] is Map ? Map<String, dynamic>.from(json['user'] as Map) : const <String, dynamic>{};
    return StickerResponseRow(
      id: json['id']?.toString() ?? '',
      username: (u['username'] ?? '').toString(),
      profilePicture: u['profile_picture']?.toString(),
      optionLabel: json['option']?.toString(),
      optionIndex: (json['option_index'] as num?)?.toInt(),
      text: json['text']?.toString(),
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? ''),
    );
  }
}

class StickerResponses {
  final List<StickerResponseRow> rows;
  final int count;
  final List<int> pollCounts; // poll only
  final int pollTotal; // poll only

  const StickerResponses({required this.rows, required this.count, this.pollCounts = const [], this.pollTotal = 0});

  factory StickerResponses.fromJson(Map<String, dynamic> json) {
    final results = (json['results'] is List) ? json['results'] as List : const [];
    final summary = json['summary'] is Map ? Map<String, dynamic>.from(json['summary'] as Map) : const <String, dynamic>{};
    return StickerResponses(
      rows: results.whereType<Map>().map((e) => StickerResponseRow.fromJson(Map<String, dynamic>.from(e))).toList(),
      count: (json['count'] as num?)?.toInt() ?? results.length,
      pollCounts: (summary['counts'] is List) ? (summary['counts'] as List).map((e) => (e as num?)?.toInt() ?? 0).toList() : const [],
      pollTotal: (summary['total'] as num?)?.toInt() ?? 0,
    );
  }
}
