class SinglePostModel {
  final String id;
  final String caption;
  final String imageUrl;
  final int likesCount;
  final String username;
  final String category;
  final String? categoryLabel;
  final String? subcategory;
  final String? subcategoryLabel;

  SinglePostModel({
    required this.id,
    required this.caption,
    required this.imageUrl,
    required this.likesCount,
    required this.username,
    required this.category,
    this.categoryLabel,
    this.subcategory,
    this.subcategoryLabel,
  });

  factory SinglePostModel.fromJson(Map<String, dynamic> json) {
    // Backend 'image' single field nahi bhejta, 'media' list bhejta hai.
    // Pehli media item ka 'file' url uthao (agar hai to).
    String imageUrl = '';
    final media = json['media'];
    if (media != null && media is List && media.isNotEmpty) {
      imageUrl = media[0]['file'] ?? media[0]['thumbnail'] ?? '';
    }

    return SinglePostModel(
      id: json['id']?.toString() ?? '',
      caption: json['content'] ?? '',
      imageUrl: imageUrl,
      likesCount: json['likes_count'] ?? 0,
      username: json['user']?['username'] ?? '',
      category: json['category'] ?? '',
      categoryLabel: json['category_label'],
      // 🔥 Yahi missing tha - subcategory backend se aata hai lekin
      // pehle parse hi nahi ho raha tha, isliye single post page pe kabhi nahi dikhta.
      subcategory: json['subcategory'],
      subcategoryLabel: json['subcategory_label'],
    );
  }
}