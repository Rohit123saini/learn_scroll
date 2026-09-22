// ============================================================
// FOLLOWERS / FOLLOWING LIST
//
//   GET /profile/profile/<username>/followers/
//   GET /profile/profile/<username>/following/
//
// Backend (`FollowersListView` / `FollowingListView`) wraps the page in
// {"status": true, "message": "...", "data": <page>} where <page> is the DRF
// paginated {count, next, previous, results} (or a bare list when no
// paginator applies). Rows: id, username, first_name, last_name,
// mutual_friends. Only ACCEPTED follows are listed.
// ============================================================

import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../l10n/app_localizations.dart';
import '../../services/auth_service.dart';
import '../../utils/api.dart';
import '../../widgets/ls_ui.dart';
import 'target_profile.dart';

class FollowListScreen extends StatefulWidget {
  final String username;
  final bool followers; // false → "following"
  const FollowListScreen({super.key, required this.username, required this.followers});

  @override
  State<FollowListScreen> createState() => _FollowListScreenState();
}

class _FollowListScreenState extends State<FollowListScreen> {
  final _scroll = ScrollController();
  final List<Map<String, dynamic>> _rows = [];
  String? _next;
  bool _loading = true;
  bool _loadingMore = false;
  bool _error = false;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(() {
      if (_scroll.position.pixels > _scroll.position.maxScrollExtent - 300) _more();
    });
    _load();
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Uri _first() => Uri.parse(
      '${Api.baseUrl}/profile/profile/${Uri.encodeComponent(widget.username)}/${widget.followers ? 'followers' : 'following'}/');

  Future<({List<Map<String, dynamic>> rows, String? next})> _fetch(Uri uri) async {
    final token = await AuthService.getValidToken();
    if (token == null) throw Exception('User not authenticated');
    final r = await http.get(uri, headers: {'Authorization': 'Bearer $token'}).timeout(const Duration(seconds: 20));
    if (r.statusCode != 200) throw Exception('HTTP ${r.statusCode}');
    final body = jsonDecode(utf8.decode(r.bodyBytes));
    final data = body is Map && body.containsKey('data') ? body['data'] : body;
    List raw;
    String? next;
    if (data is Map) {
      raw = (data['results'] as List?) ?? const [];
      next = data['next']?.toString();
    } else {
      raw = (data as List?) ?? const [];
    }
    return (rows: raw.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList(), next: next);
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = false;
    });
    try {
      final res = await _fetch(_first());
      if (!mounted) return;
      setState(() {
        _rows
          ..clear()
          ..addAll(res.rows);
        _next = res.next;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = true;
        _loading = false;
      });
    }
  }

  Future<void> _more() async {
    final next = _next;
    if (_loadingMore || next == null || next.isEmpty) return;
    setState(() => _loadingMore = true);
    try {
      // `next` carries the request's own scheme/host — pin it to the app's origin.
      final b = Uri.parse(Api.baseUrl);
      final uri = Uri.parse(next).replace(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null);
      final res = await _fetch(uri);
      if (!mounted) return;
      setState(() {
        _rows.addAll(res.rows);
        _next = res.next;
      });
    } catch (_) {
      // leave `_next` so a later scroll retries
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: cs.surface,
      appBar: AppBar(
        backgroundColor: cs.surface,
        elevation: 0,
        iconTheme: IconThemeData(color: cs.onSurface),
        title: Text(widget.followers ? l10n.followersStat : l10n.following, style: LsType.head(context, size: 16)),
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error
                ? ListView(children: [
                    const SizedBox(height: 120),
                    Center(child: Text(l10n.usersLoadFailed, style: TextStyle(color: cs.onSurfaceVariant))),
                    Center(child: TextButton(onPressed: _load, child: Text(l10n.retry))),
                  ])
                : _rows.isEmpty
                    ? ListView(children: [
                        const SizedBox(height: 120),
                        Center(child: Text(l10n.noUsersHere, style: TextStyle(color: cs.onSurfaceVariant))),
                      ])
                    : ListView.builder(
                        controller: _scroll,
                        physics: const AlwaysScrollableScrollPhysics(),
                        itemCount: _rows.length,
                        itemBuilder: (_, i) {
                          final u = _rows[i];
                          final username = u['username']?.toString() ?? '';
                          final name = '${u['first_name'] ?? ''} ${u['last_name'] ?? ''}'.trim();
                          final mutual = u['mutual_friends'];
                          return ListTile(
                            leading: CircleAvatar(
                              backgroundColor: cs.surfaceVariant,
                              child: Text(username.isEmpty ? '?' : username[0].toUpperCase(), style: TextStyle(color: cs.onSurface)),
                            ),
                            title: Text(username, style: const TextStyle(fontWeight: FontWeight.w700)),
                            subtitle: Text(
                              [
                                if (name.isNotEmpty) name,
                                if (mutual is int && mutual > 0) l10n.mutualFriendsCount(mutual),
                              ].join(' · '),
                              style: TextStyle(color: cs.onSurfaceVariant, fontSize: 12),
                            ),
                            onTap: username.isEmpty
                                ? null
                                : () => Navigator.of(context)
                                    .push(MaterialPageRoute(builder: (_) => TargetProfilePage(username: username))),
                          );
                        },
                      ),
      ),
    );
  }
}
