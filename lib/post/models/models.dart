// lib/post/models/models.dart
//
// TASK 8 — `screens/singlepost.dart` imports this file and calls
// `SinglePostModel.fromJson(data)` on the raw map returned by
// `ApiService().getPostById()` (`GET /post/details/<id>/`, unwrapped at
// `data['data']`), plus reads `.userId/.username/.title/.caption/
// .category/.categoryLabel/.subcategory/.subcategoryLabel/.createdAt/
// .media/.myReaction/.reactionCounts/.commentsCount` off the result —
// none of that ever existed anywhere in the codebase, which is exactly
// why the build failed ("Type 'SinglePostModel' not found"). This is
// that missing model.
//
// Reuses `PostMediaModel` (same `id/media_type/file/thumbnail/file_name/
// width/height/duration_seconds` shape the home feed's `PostModel`
// already parses post media with) instead of duplicating a second media
// shape for the same endpoint family.
//
// `categoryLabel`/`subcategoryLabel` — `new_post.dart` only ever derives
// these client-side from a fetched key->label map (`_categoryLabel`/
// `_subcategoryLabel`), never sends/receives them as flat response
// fields under a confirmed key. Accepted defensively here under the
// plausible `category_label`/`subcategory_label` keys so a real backend
// value is picked up if present, but left null otherwise rather than
// guessing — `singlepost.dart` already falls back to the raw
// `category`/`subcategory` when the label is null.

import '../../services/home_api_model_service.dart' show UserModel, PostMediaModel, PostModel;

class SinglePostModel {
  final String id;
  final String userId;
  final String username;
  final String? title;
  final String caption;
  final String category;
  final String? categoryLabel;
  final String? subcategory;
  final String? subcategoryLabel;
  final DateTime? createdAt;
  final List<PostMediaModel> media;
  final String? myReaction;
  final Map<String, int> reactionCounts;
  final int commentsCount;
  // 🔥 NAYA — Saved tab feature. PostDetailSerializer extends
  // PostListSerializer (backend), so /post/details/<id>/ already echoes
  // `is_saved`/`saves_count` — this was just never read into the detail
  // page's model before.
  final bool isSaved;
  final int savesCount;
  // Repost feature — same fields PostModel carries (PostDetailSerializer
  // extends PostListSerializer, so /post/details/<id>/ already returns
  // them). `originalPost` is null for a normal post, and ALSO for a repost
  // whose original is gone / not visible to this viewer (see PostModel).
  final String postType;
  final String? repostCaption;
  final PostModel? originalPost;
  final int repostsCount;
  final bool isRepostedByMe;
  bool get isRepost => postType == 'repost';
  // NEW — post edit/visibility feature. `visibility` was already on the
  // backend (create-time-only before now); `isEdited` mirrors the
  // `is_edited` flag PATCH /post/<id>/edit/ sets, so the UI can show an
  // "edited" label the same way Instagram/Twitter do.
  final String visibility;
  final bool isEdited;

  SinglePostModel({
    this.postType = 'text',
    this.repostCaption,
    this.originalPost,
    this.repostsCount = 0,
    this.isRepostedByMe = false,
    required this.id,
    required this.userId,
    required this.username,
    this.title,
    required this.caption,
    required this.category,
    this.categoryLabel,
    this.subcategory,
    this.subcategoryLabel,
    this.createdAt,
    required this.media,
    this.myReaction,
    required this.reactionCounts,
    required this.commentsCount,
    this.isSaved = false,
    this.savesCount = 0,
    this.visibility = 'public',
    this.isEdited = false,
  });

  factory SinglePostModel.fromJson(Map<String, dynamic> json) {
    // Same nested-vs-flat `user` handling `StoryModel.fromJson` (in
    // story_model.dart) already uses, since `PostModel.fromJson` (home
    // feed, same backend family) also expects a nested `user` object
    // rather than a flat `user_id`.
    final userJson = json['user'];
    final user = userJson is Map ? UserModel.fromJson(Map<String, dynamic>.from(userJson)) : null;

    // Same fallback pair `_ReactionTapTarget`'s caller (`_handleReaction`
    // in singlepost.dart) already reconciles a live reaction response
    // against: `my_reaction` if present, else derive from `is_liked`.
    final myReaction = json['my_reaction']?.toString() ?? (json['is_liked'] == true ? 'like' : null);

    // `reactionCounts` mirrors `PostReactionAPIView`'s `counts` shape —
    // same five keys + `total` `_handleReaction` already reads off that
    // endpoint's response, so a nested `json['counts']` here (if the
    // detail endpoint echoes it the same way) or flat `*_count` fields
    // (matching `PostModel.fromJson`'s `like_count`/`confuse_count`/...)
    // both map onto it without guessing a third shape.
    final countsJson = (json['counts'] as Map?) ?? {};
    int countOf(String key, String flatKey) =>
        (countsJson[key] as int?) ?? (json[flatKey] as int?) ?? 0;

    return SinglePostModel(
      id: json['id']?.toString() ?? '',
      userId: user?.id ?? json['user_id']?.toString() ?? '',
      username: user?.username ?? json['username']?.toString() ?? '',
      title: json['title']?.toString(),
      caption: json['caption']?.toString() ?? json['content']?.toString() ?? '',
      category: json['category']?.toString() ?? 'general',
      categoryLabel: json['category_label']?.toString(),
      subcategory: json['subcategory']?.toString(),
      subcategoryLabel: json['subcategory_label']?.toString(),
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? ''),
      media: (json['media'] as List<dynamic>?)
              ?.map((e) => PostMediaModel.fromJson(Map<String, dynamic>.from(e as Map)))
              .toList() ??
          [],
      myReaction: myReaction,
      reactionCounts: {
        'like': countOf('like', 'like_count'),
        'confuse': countOf('confuse', 'confuse_count'),
        'wrong': countOf('wrong', 'wrong_count'),
        'imp': countOf('imp', 'imp_count'),
        'explain': countOf('explain', 'explain_count'),
        'total': countOf('total', 'likes_count'),
      },
      commentsCount: (json['comments_count'] as int?) ?? 0,
      isSaved: json['is_saved'] == true,
      savesCount: (json['saves_count'] as int?) ?? 0,
      postType: json['post_type']?.toString() ?? 'text',
      repostCaption: json['repost_caption']?.toString(),
      originalPost: json['original_post'] is Map && json['original_post']['is_unavailable'] != true
          ? PostModel.fromJson(Map<String, dynamic>.from(json['original_post'] as Map))
          : null,
      repostsCount: (json['reposts_count'] as int?) ?? 0,
      isRepostedByMe: json['is_reposted_by_me'] == true,
      visibility: json['visibility']?.toString() ?? 'public',
      isEdited: json['is_edited'] == true,
    );
  }
}
