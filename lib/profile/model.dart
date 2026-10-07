import 'profile_link.dart';

class ProfileModel {
  final int id;
  final String username;
  final String firstName;
  final String lastName;
  final String profilePhoto;
  final String bio;
  final bool isPrivate;
  final bool isVerified;
  final int followers;
  final int following;
  final int posts;
  final int coin;
  // P7-FE (backend P6-BE) — bio upgrade fields.
  final String pronouns;
  final String categoryLabel;
  final List<ProfileLink> links;

  ProfileModel({
    required this.id,
    required this.username,
    required this.firstName,
    required this.lastName,
    required this.profilePhoto,
    required this.bio,
    required this.isPrivate,
    required this.isVerified,
    required this.followers,
    required this.following,
    required this.posts,
    required this.coin,
    this.pronouns = '',
    this.categoryLabel = '',
    this.links = const [],
  });

  factory ProfileModel.fromJson(Map<String, dynamic> json) {
    final data = json["data"] ?? {};
    return ProfileModel(
      id: data["id"] ?? 0,
      username: data["username"] ?? "",
      firstName: data["first_name"] ?? "",
      lastName: data["last_name"] ?? "",
      profilePhoto: data["profile_photo"] ?? "",
      bio: data["bio"] ?? "",
      isPrivate: data["is_private"] ?? false,
      isVerified: data["is_verified"] ?? false,
      followers: data["followers_count"] ?? 0,
      following: data["following_count"] ?? 0,
      posts: data["posts_count"] ?? 0,
      coin: data["coin"] ?? 0,
      pronouns: (data["pronouns"] ?? '').toString(),
      categoryLabel: (data["category_label"] ?? '').toString(),
      links: ProfileLink.listFrom(data["links"]),
    );
  }
}

class TargetProfileModel {
  final int myId;
  final String myUsername;
  final int targetUserId;
  final String targetUsername;
  final String username;
  final String firstName;
  final String lastName;
  final String profilePhoto;
  final String bio;
  final bool isPrivate;
  final bool isVerified;
  final int followers;
  final int following;
  final int posts;
  // 🔥 Main user ne target ko follow kiya
  final String? myFollowStatus; // null, PENDING, ACCEPTED
  final int? myFollowId;
  // 🔥 Target user ne main user ko follow kiya
  final String? theirFollowStatus; // null, PENDING, ACCEPTED
  final int? theirFollowId;
  // P7-FE (backend P6-BE) — bio upgrade fields.
  final String pronouns;
  final String categoryLabel;
  final List<ProfileLink> links;
  // Block system — true when *I* blocked this account (backend then sends a
  // minimal card: photo + name only). If *they* blocked me the API 404s instead.
  final bool isBlockedByMe;
  // I restricted / muted this account (profile ⋮ menu shows the opposite action).
  final bool amIRestricting;
  final bool isMutedByMe;

  TargetProfileModel({
    required this.myId,
    required this.myUsername,
    required this.targetUserId,
    required this.targetUsername,
    required this.username,
    required this.firstName,
    required this.lastName,
    required this.profilePhoto,
    required this.bio,
    required this.isPrivate,
    required this.isVerified,
    required this.followers,
    required this.following,
    required this.posts,
    this.myFollowStatus,
    this.myFollowId,
    this.theirFollowStatus,
    this.theirFollowId,
    this.pronouns = '',
    this.categoryLabel = '',
    this.links = const [],
    this.isBlockedByMe = false,
    this.amIRestricting = false,
    this.isMutedByMe = false,
  });

  // TASK G16 — needed so the follow button can update optimistically
  // (flip state instantly, roll back on failure) instead of always
  // re-fetching the whole profile after every tap. Every field on this
  // model is `final`, which is fine for "parsed once from a response" but
  // means an in-place instant UI update needs a copy, not a mutation.
  TargetProfileModel copyWith({
    int? followers,
    String? myFollowStatus,
    bool clearMyFollowStatus = false,
    int? myFollowId,
    bool clearMyFollowId = false,
    bool? isBlockedByMe,
    bool? amIRestricting,
    bool? isMutedByMe,
  }) {
    return TargetProfileModel(
      myId: myId,
      myUsername: myUsername,
      targetUserId: targetUserId,
      targetUsername: targetUsername,
      username: username,
      firstName: firstName,
      lastName: lastName,
      profilePhoto: profilePhoto,
      bio: bio,
      isPrivate: isPrivate,
      isVerified: isVerified,
      followers: followers ?? this.followers,
      following: following,
      posts: posts,
      myFollowStatus: clearMyFollowStatus ? null : (myFollowStatus ?? this.myFollowStatus),
      myFollowId: clearMyFollowId ? null : (myFollowId ?? this.myFollowId),
      theirFollowStatus: theirFollowStatus,
      theirFollowId: theirFollowId,
      pronouns: pronouns,
      categoryLabel: categoryLabel,
      links: links,
      isBlockedByMe: isBlockedByMe ?? this.isBlockedByMe,
      amIRestricting: amIRestricting ?? this.amIRestricting,
      isMutedByMe: isMutedByMe ?? this.isMutedByMe,
    );
  }

  factory TargetProfileModel.fromJson(Map<String, dynamic> json) {
    final dataMap = json['data'] ?? {};
    return TargetProfileModel(
      myId: json['my_id'] ?? 0,
      myUsername: json['my_username'] ?? '',
      targetUserId: json['target_user_id'] ?? 0,
      targetUsername: json['target_username'] ?? '',
      username: dataMap['username'] ?? '',
      firstName: dataMap['first_name'] ?? '',
      lastName: dataMap['last_name'] ?? '',
      profilePhoto: dataMap['profile_photo'] ?? '',
      bio: dataMap['bio'] ?? '',
      isPrivate: dataMap['is_private'] ?? false,
      isVerified: dataMap['is_verified'] ?? false,
      followers: dataMap['followers_count'] ?? 0,
      following: dataMap['following_count'] ?? 0,
      posts: dataMap['posts_count'] ?? 0,
      myFollowStatus: json['my_follow_status'],
      myFollowId: json['my_follow_id'],
      theirFollowStatus: json['their_follow_status'],
      theirFollowId: json['their_follow_id'],
      pronouns: (dataMap['pronouns'] ?? '').toString(),
      categoryLabel: (dataMap['category_label'] ?? '').toString(),
      links: ProfileLink.listFrom(dataMap['links']),
      isBlockedByMe: json['is_blocked_by_me'] == true,
      amIRestricting: json['am_i_restricting'] == true,
      isMutedByMe: json['is_muted_by_me'] == true,
    );
  }
}

class UpdateProfileResponse {
  final bool status;
  final String message;
  final ProfileModel data;

  UpdateProfileResponse({
    required this.status,
    required this.message,
    required this.data,
  });

  factory UpdateProfileResponse.fromJson(Map<String, dynamic> json) {
    return UpdateProfileResponse(
      status: json['status'] ?? false,
      message: json['message'] ?? '',
      data: ProfileModel.fromJson(json['data']),
    );
  }
}

class PostMediaModel {
  final String id;
  final String mediaType;
  final String file;
  final String? thumbnail;
  final String fileName;
  final int fileSizeBytes;
  final String mimeType;
  final int displayOrder;

  PostMediaModel({
    required this.id,
    required this.mediaType,
    required this.file,
    this.thumbnail,
    required this.fileName,
    required this.fileSizeBytes,
    required this.mimeType,
    required this.displayOrder,
  });

  factory PostMediaModel.fromJson(Map<String, dynamic> json) {
    return PostMediaModel(
      id: json['id']?? '',
      mediaType: json['media_type']?? '',
      file: json['file']?? '',
      thumbnail: json['thumbnail'],
      fileName: json['file_name']?? '',
      fileSizeBytes: json['file_size_bytes']?? 0,
      mimeType: json['mime_type']?? '',
      displayOrder: json['display_order']?? 0,
    );
  }
}




class PostModel {
  final String id;
  final String? title;
  final String content;
  final String category;
  final String postType;
  final String visibility;
  final List<String> hashtags;
  final int likesCount;
  final int commentsCount;
  final int viewsCount;
  final int savesCount;
  final bool isLiked;
  final bool isSaved;
  final String createdAt;
  final List<PostMediaModel> media;
  final Map<String, dynamic>? user; // 🔥 Add kar de
  final String? thumbnailUrl;
  // 🔥 NAYA — Repost feature (target-profile "Reposts" tab). `original_post`
  // backend se ya to (a) null (plain post), (b) a stub `{id, is_unavailable:
  // true}` (soft-deleted/moderated/no-longer-visible original — see
  // `get_original_post` in serializers.py), ya (c) poora nested post aata
  // hai. `originalPost` sirf case (c) me set hota hai; case (b) sirf
  // `isOriginalUnavailable` se flag hota hai taaki UI "unavailable"
  // placeholder dikha sake bina kisi hidden content ko leak kiye.
  final PostModel? originalPost;
  final bool isOriginalUnavailable;
  final String? repostCaption;
  final int repostsCount;
  final bool isRepostedByMe;
  // P3-FE — profile pinned posts (backend `is_pinned`, max 3). Not `final`: the
  // profile screen flips it in place after a successful pin/unpin call.
  bool isPinned;
  PostModel({
    required this.id,
    this.title,
    required this.content,
    required this.category,
    required this.postType,
    required this.visibility,
    required this.hashtags,
    required this.likesCount,
    required this.commentsCount,
    required this.viewsCount,
    required this.savesCount,
    required this.isLiked,
    required this.isSaved,
    required this.createdAt,
    required this.media,
    this.user, // 🔥 Add kar
    this.thumbnailUrl,
    this.originalPost,
    this.isOriginalUnavailable = false,
    this.repostCaption,
    this.repostsCount = 0,
    this.isRepostedByMe = false,
    this.isPinned = false,
  });

  factory PostModel.fromJson(Map<String, dynamic> json) {
    final rawOriginal = json['original_post'] as Map<String, dynamic>?;
    final originalIsStub = rawOriginal != null && rawOriginal['is_unavailable'] == true;
    return PostModel(
      id: json['id']?? '',
      title: json['title'],
      content: json['content']?? '',
      category: json['category']?? '',
      postType: json['post_type']?? '',
      visibility: json['visibility']?? '',
      hashtags: List<String>.from(json['hashtags']?? []),
      likesCount: json['likes_count']?? 0,
      commentsCount: json['comments_count']?? 0,
      viewsCount: json['views_count']?? 0,
      savesCount: json['saves_count']?? 0,
      isLiked: json['is_liked']?? false,
      isSaved: json['is_saved']?? false,
      createdAt: json['created_at']?? '',
      media: (json['media'] as List<dynamic>?)
            ?.map((e) => PostMediaModel.fromJson(e))
            .toList()??
          [],
      user: json['user'], // 🔥 Add kar
      thumbnailUrl: json['thumbnail_url'], 
      originalPost: (rawOriginal != null && !originalIsStub) ? PostModel.fromJson(rawOriginal) : null,
      isOriginalUnavailable: originalIsStub,
      repostCaption: json['repost_caption'],
      repostsCount: json['reposts_count']?? 0,
      isRepostedByMe: json['is_reposted_by_me']?? false,
      isPinned: json['is_pinned'] == true,
    );
  }

  String get firstImageUrl {
    if (media.isEmpty) return '';
    return media.first.file;
  }
}

/// One page of `getMyPostsPage` — posts plus whether the backend's DRF
/// pagination says there's another page (`next != null`), so the caller
/// can drive real infinite-scroll instead of guessing from page length.
class PostsPage {
  final List<PostModel> posts;
  final bool hasMore;
  const PostsPage({required this.posts, required this.hasMore});
}
/// [Settings/Nav pass] — Settings > Privacy > Blocked accounts row.
/// Maps `BlockUserSerializer`'s `{id, blocked, blocked_detail, created_at}`
/// shape (`user_profile/serializers.py`). `id` yahan **block record** ki id
/// hai, `blockedUserId` target user ki — unblock backend dono accept karta
/// hai, isliye UI target-user-id hi bhejta hai (simplest).
/// One page of `ApiService.getBlockedUsersPage`.
class BlockedPage {
  final List<BlockedUserModel> items;
  final bool hasMore;
  final int? nextOffset;
  const BlockedPage({required this.items, required this.hasMore, this.nextOffset});
}

class BlockedUserModel {
  final int id;
  final int blockedUserId;
  final String username;
  final String profilePhoto;
  // "Also block new accounts" was on for this block — kept so Undo restores it.
  final bool blockNewAccounts;

  const BlockedUserModel({
    required this.id,
    required this.blockedUserId,
    required this.username,
    required this.profilePhoto,
    this.blockNewAccounts = false,
  });

  factory BlockedUserModel.fromJson(Map<String, dynamic> json) {
    final detail = (json['blocked_detail'] as Map?) ?? {};
    return BlockedUserModel(
      id: json['id'] ?? 0,
      blockedUserId: json['blocked'] ?? detail['id'] ?? 0,
      username: detail['username'] ?? '',
      profilePhoto: detail['profile_photo'] ?? '',
      blockNewAccounts: json['block_new_accounts'] == true,
    );
  }
}


/// P3-FE — result of `ApiService.setPostPinned`.
class PinResult {
  final bool isPinned;
  final int pinnedCount;
  final int maxPinned;
  const PinResult({required this.isPinned, required this.pinnedCount, required this.maxPinned});
}

/// 400 / 403 / 404 from the pin endpoint. `message` is already user-readable
/// (comes from the backend), `code` e.g. "pin_limit_reached".
class PinException implements Exception {
  final String message;
  final String? code;
  PinException(this.message, {this.code});
  @override
  String toString() => message;
}

/// P3-FE — pinned posts first, then newest first (same order the backend
/// returns). Used after a local pin/unpin so the grid reorders instantly
/// without refetching. `List.sort` isn't stable, so the original index is the
/// final tie-breaker.
List<PostModel> sortPinnedFirst(List<PostModel> posts) {
  int byDateDesc(PostModel a, PostModel b) {
    final da = DateTime.tryParse(a.createdAt);
    final db = DateTime.tryParse(b.createdAt);
    if (da != null && db != null) return db.compareTo(da);
    return b.createdAt.compareTo(a.createdAt);
  }

  final indexed = posts.asMap().entries.toList();
  indexed.sort((a, b) {
    if (a.value.isPinned != b.value.isPinned) return a.value.isPinned ? -1 : 1;
    final c = byDateDesc(a.value, b.value);
    return c != 0 ? c : a.key.compareTo(b.key);
  });
  return indexed.map((e) => e.value).toList();
}
