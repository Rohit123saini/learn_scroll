// lib/post/services/close_friends_service.dart
//
// Stories upgrade, Part 1 — Close Friends list API (no UI here).
//
// Backend (post/close_friends_views.py):
//   GET    /post/close-friends/               my list (paginated)
//   PUT    /post/close-friends/               replace whole list {"user_ids": [..]}
//   POST   /post/close-friends/<user_id>/     add one
//   DELETE /post/close-friends/<user_id>/     remove one
//   GET    /post/close-friends/candidates/    followers + following, `?q=` search,
//                                              each row carries `is_close_friend`
//
// Every list endpoint is DRF-paginated ({"results": [...], "next": url|null});
// `_getPage()` follows `next` verbatim, same convention as the other services.

import 'dart:convert';
import 'package:http/http.dart' as http;
import '../../utils/api.dart';
import '../../services/auth_service.dart';
import '../../services/home_api_model_service.dart' show kApiTimeout;

class CloseFriendUser {
  final int id;
  final String username;
  final String name;
  final String? profilePicture;
  bool isCloseFriend;

  CloseFriendUser({
    required this.id,
    required this.username,
    required this.name,
    this.profilePicture,
    this.isCloseFriend = false,
  });

  factory CloseFriendUser.fromJson(Map<String, dynamic> json) {
    final rawId = json['id'];
    return CloseFriendUser(
      id: rawId is int ? rawId : int.tryParse(rawId?.toString() ?? '') ?? 0,
      username: (json['username'] ?? '').toString(),
      name: (json['name'] ?? json['username'] ?? '').toString(),
      profilePicture: (json['profile_picture'] ?? json['profile_photo'])?.toString(),
      isCloseFriend: json['is_close_friend'] == true,
    );
  }
}

class CloseFriendsPage {
  final List<CloseFriendUser> users;
  final String? next; // absolute URL, pass back to `loadMore`
  CloseFriendsPage(this.users, this.next);
}

class CloseFriendsService {
  static String get _base => "${Api.baseUrl}/post/close-friends";

  static Future<Map<String, String>> _headers({bool json = false}) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    return {
      "Authorization": "Bearer $token",
      if (json) "Content-Type": "application/json",
    };
  }

  static Future<CloseFriendsPage> _getPage(Uri url) async {
    final res = await http.get(url, headers: await _headers()).timeout(kApiTimeout);
    if (res.statusCode != 200) {
      throw Exception('Close friends request failed: ${res.statusCode}');
    }
    final decoded = jsonDecode(res.body);
    final List raw = decoded is Map ? (decoded['results'] as List? ?? []) : decoded as List;
    final next = decoded is Map ? decoded['next']?.toString() : null;
    return CloseFriendsPage(
      raw.whereType<Map>().map((e) => CloseFriendUser.fromJson(Map<String, dynamic>.from(e))).toList(),
      (next == null || next.isEmpty || next == 'null') ? null : next,
    );
  }

  /// My current Close Friends list (first page).
  static Future<CloseFriendsPage> getMine({int pageSize = 50}) =>
      _getPage(Uri.parse("$_base/?page_size=$pageSize"));

  /// People I can add (accepted followers + people I follow). `query` is a
  /// server-side search over username / first / last name.
  static Future<CloseFriendsPage> getCandidates({String query = '', int pageSize = 50}) {
    final q = query.trim();
    final url = Uri.parse("$_base/candidates/").replace(queryParameters: {
      'page_size': '$pageSize',
      if (q.isNotEmpty) 'q': q,
    });
    return _getPage(url);
  }

  /// Next page of either list — `next` comes straight from the previous page.
  static Future<CloseFriendsPage> loadMore(String next) => _getPage(Uri.parse(next));

  /// Add one person. Returns true when they are on the list afterwards.
  static Future<bool> add(int userId) async {
    final res = await http.post(Uri.parse("$_base/$userId/"), headers: await _headers()).timeout(kApiTimeout);
    if (res.statusCode == 200 || res.statusCode == 201) return true;
    throw Exception('Failed to add close friend: ${res.statusCode}');
  }

  /// Remove one person (idempotent server-side).
  static Future<void> remove(int userId) async {
    final res = await http.delete(Uri.parse("$_base/$userId/"), headers: await _headers()).timeout(kApiTimeout);
    if (res.statusCode != 204 && res.statusCode != 200) {
      throw Exception('Failed to remove close friend: ${res.statusCode}');
    }
  }

  /// Replace the entire list in one call. Returns the ids the server skipped
  /// (blocked / unknown users).
  static Future<List<int>> replaceAll(List<int> userIds) async {
    final res = await http
        .put(Uri.parse("$_base/"), headers: await _headers(json: true), body: jsonEncode({"user_ids": userIds}))
        .timeout(kApiTimeout);
    if (res.statusCode != 200) throw Exception('Failed to update close friends: ${res.statusCode}');
    final decoded = jsonDecode(res.body);
    final skipped = decoded is Map ? decoded['skipped_user_ids'] as List? ?? [] : [];
    return skipped.map((e) => e is int ? e : int.tryParse(e.toString()) ?? 0).toList();
  }
}
