class SinglePostModel {
  final int id;
  final String caption;
  final String imageUrl;
  final int likesCount;
  final String username;

  SinglePostModel({
    required this.id,
    required this.caption,
    required this.imageUrl,
    required this.likesCount,
    required this.username,
  });

  factory SinglePostModel.fromJson(Map<String, dynamic> json) {
    return SinglePostModel(
      id: json['id'],
      caption: json['caption'] ?? '',
      imageUrl: json['image'] ?? '',
      likesCount: json['likes_count'] ?? 0,
      username: json['user']['username'] ?? '',
    );
  }
}