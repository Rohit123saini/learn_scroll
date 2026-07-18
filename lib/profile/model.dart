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
  });

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
  });

  factory PostModel.fromJson(Map<String, dynamic> json) {
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
    );
  }

  String get firstImageUrl {
    if (media.isEmpty) return '';
    return media.first.file;
  }
}