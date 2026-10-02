import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../profile/model.dart'; // PostModel
import '../../utils/api.dart';
import 'post_tag_service.dart';

// ============================================================
// P7-FE — GET /post/hashtag/<tag>/   (backend HashtagPostsAPIView)
// Ranked by engagement and CURSOR-paginated: the next page is the absolute `next` URL
// from the previous response, not a page number.
// ============================================================

class HashtagPage {
  final List<PostModel> posts;
  final String? next;
  const HashtagPage(this.posts, this.next);
}

class HashtagService {
  HashtagService._();

  static Future<HashtagPage> getPosts(String tag, {String? nextUrl}) async {
    final clean = tag.trim().replaceFirst(RegExp(r'^#'), '').toLowerCase();
    final uri = Uri.parse(nextUrl ?? '${Api.baseUrl}/post/hashtag/${Uri.encodeComponent(clean)}/');
    final r = await http.get(uri, headers: await PostTagService.authHeaders());
    if (r.statusCode != 200) throw Exception("Couldn't load #$clean.");

    dynamic root = jsonDecode(utf8.decode(r.bodyBytes));
    if (root is Map && root['data'] != null && root['results'] == null) root = root['data'];
    List raw = const [];
    String? next;
    if (root is Map) {
      raw = (root['results'] as List?) ?? const [];
      next = root['next']?.toString();
    } else if (root is List) {
      raw = root;
    }
    final posts = [
      for (final e in raw)
        if (e is Map) PostModel.fromJson(Map<String, dynamic>.from(e)),
    ];
    return HashtagPage(posts, (next == null || next.isEmpty || next == 'null') ? null : next);
  }
}
