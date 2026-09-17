import 'dart:convert';
import 'package:http/http.dart' as http;
import '../utils/api.dart';
import '../services/auth_service.dart';
import 'models/search_result.dart';

/// `search/api_service.dart` — sibling of `search/search.dart`, same
/// layout as `profile/api_service.dart` (which `home.dart` imports as
/// `ProfileApi` — that alias is a hint there's already another generic
/// `ApiService` class floating around, so this one is named specifically
/// rather than reusing that name).
///
/// PATH FIX: the old file here imported `../../utils/api.dart` and
/// `../../services/auth_service.dart` — two levels up from `lib/search/`
/// lands outside `lib/` entirely, so it could never have compiled once
/// actually placed at `lib/search/api_service.dart`. One level up (same
/// pattern `search/search.dart` itself already used) is correct.
class SearchApiService {
  /// People/friends search — `user_profile` app, endpoint unchanged.
  /// `core`'s unified search has no user/friend source (see
  /// `core_app_documentation.md` §6.2's source table), so this stays a
  /// separate call from [searchEverything].
  static Future<List<dynamic>> searchUsers(String query) async {
    try {
      final token = await AuthService.getToken();
      final url = Uri.parse('${Api.baseUrl}/profile/search/')
          .replace(queryParameters: {'search': query});

      final response = await http.get(
        url,
        headers: {
          'Content-Type': 'application/json',
          if (token != null) 'Authorization': 'Bearer $token',
        },
      );

      if (response.statusCode == 200) {
        final Map<String, dynamic> data = json.decode(response.body);
        if (data['status'] == true) {
          return (data['data'] as List?) ?? [];
        }
      }
      return [];
    } catch (e) {
      // ignore: avoid_print
      print('People search error: $e');
      return [];
    }
  }

  /// TASK 6 — #hashtag autocomplete for `new_post.dart`'s composer.
  ///
  /// ✅ CONFIRMED against real `post/urls.py` + `post/views.py`:
  ///   - Path is `post/hashtags/trending/` -> `name="trending-hashtags"`,
  ///     i.e. `GET {baseUrl}/post/hashtags/trending/`. Matches below as-is.
  ///   - `TrendingHashtagsAPIView` takes only `days` (default 7) and
  ///     `limit` (default 20, capped at 50) query params — see its
  ///     docstring/`get()`. There is NO `search`/`q` param and no
  ///     server-side filtering of any kind: it always returns the same
  ///     app-wide most-common-tags list for the window, full stop.
  ///   - Response shape is `{"success": true, "days": ..., "results":
  ///     [{"hashtag": "...", "count": N}, ...]}` — a flat list under
  ///     `results`, each item keyed `hashtag` (not `name`/`tag`). Parsing
  ///     below is narrowed to match that exactly; the old `data`/
  ///     `hashtags` fallback keys and `name`/`tag` fallback keys were
  ///     guesses for a shape that doesn't exist and are removed.
  ///
  /// 🟡 STILL FLAGGED FOR TASK 4: since there's no real prefix search,
  /// `query` is NOT sent to the server (it would silently be ignored —
  /// sending it implied a filtering contract that doesn't exist). Instead
  /// we ask for the max allowed `limit` (50) so `new_post.dart` has the
  /// widest possible candidate pool to prefix-filter client-side against.
  /// This is still only ever "trending tags that happen to match", not a
  /// true hashtag search — a post-count-independent `GET
  /// /post/hashtags/search/?q=` (or equivalent) endpoint is needed
  /// server-side for real prefix search over ALL hashtags ever used, not
  /// just the current top-50 trending ones. Please add that to Task 4.
  ///
  /// [query] is accepted (and still passed by `new_post.dart`) purely so
  /// the caller can keep its existing call site / client-side filtering
  /// unchanged; it is intentionally NOT forwarded to the server since the
  /// real endpoint has nothing to do with it.
  static Future<List<String>> trendingHashtags({String? query}) async {
    try {
      final token = await AuthService.getToken();
      // No `search` param — the real endpoint doesn't support one (see
      // docstring above). `limit: 50` (its hard cap) maximizes how many
      // trending tags are available for the caller to filter locally.
      final url = Uri.parse('${Api.baseUrl}/post/hashtags/trending/')
          .replace(queryParameters: {'limit': '50'});

      final response = await http.get(
        url,
        headers: {
          'Content-Type': 'application/json',
          if (token != null) 'Authorization': 'Bearer $token',
        },
      );

      if (response.statusCode == 200) {
        final Map<String, dynamic> data = json.decode(response.body);
        final List<dynamic> raw = (data['results'] as List?) ?? [];
        return raw
            .map((e) {
              if (e is Map) {
                return (e['hashtag'] ?? '').toString();
              }
              return e.toString();
            })
            .map((s) => s.trim())
            .where((s) => s.isNotEmpty)
            .toList();
      }
      return [];
    } catch (e) {
      // ignore: avoid_print
      print('Trending hashtags error: $e');
      return [];
    }
  }

  /// Unified cross-app search — `GET /core/search/?q=...&sources=...`
  /// (`core.views.SearchView`, Task 18). Omit `sources` to search every
  /// source `SearchView` currently scopes: `assigments`, `testseries`,
  /// `message`, `campus_notice`. Never pass `post`/`class_material` —
  /// they're backend stubs (§6.2) and would just come back empty.
  static Future<List<SearchResultItem>> searchEverything(
    String query, {
    List<String>? sources,
  }) async {
    try {
      final token = await AuthService.getToken();
      final params = <String, String>{'q': query};
      if (sources != null && sources.isNotEmpty) {
        params['sources'] = sources.join(',');
      }
      final url = Uri.parse('${Api.baseUrl}/core/search/')
          .replace(queryParameters: params);

      final response = await http.get(
        url,
        headers: {
          'Content-Type': 'application/json',
          if (token != null) 'Authorization': 'Bearer $token',
        },
      );

      if (response.statusCode == 200) {
        final Map<String, dynamic> data = json.decode(response.body);
        final List<dynamic> raw = (data['results'] as List?) ?? [];
        return raw
            .whereType<Map<String, dynamic>>()
            .map(SearchResultItem.fromJson)
            .toList();
      }
      return [];
    } catch (e) {
      // ignore: avoid_print
      print('Unified search error: $e');
      return [];
    }
  }
}