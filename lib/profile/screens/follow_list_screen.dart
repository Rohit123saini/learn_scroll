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
import '../api_service.dart';
import '../../message/services/message_api_service.dart';
import '../../message/screens/chat_screen.dart';
import 'target_profile.dart';
import 'follow_requests_screen.dart'; // P11-FE

class FollowListScreen extends StatefulWidget {
  final String username;
  final bool followers; // false → "following"
  /// P11-FE — true ONLY when this is the logged-in user's own list. Unlocks
  /// "Remove follower" (and the Follow-requests shortcut) on the followers list.
  /// Must be passed by the caller: on someone else's list, DELETE
  /// /profile/followers/<id>/ would remove that user from MY followers, not theirs.
  final bool isOwner;
  const FollowListScreen({super.key, required this.username, required this.followers, this.isOwner = false});

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

  // TASK 7 (production_readiness_tasks.md) — per-row Follow/Following
  // button state, keyed by user id. Seeded from each row's own
  // `follow_status` field (FollowListRowSerializer, backend) the moment
  // it's fetched — see _seedFollowStatus below — then updated locally
  // (optimistic) after a Follow/Unfollow tap so the row doesn't have to
  // be refetched.
  final Map<int, String?> _followStatus = {};
  final Set<int> _followBusy = {};
  final Set<int> _removeBusy = {}; // P11-FE — follower removals in flight

  bool get _canRemove => widget.isOwner && widget.followers;

  void _seedFollowStatus(List<Map<String, dynamic>> rows) {
    for (final row in rows) {
      final id = row['id'];
      if (id is int) _followStatus[id] = row['follow_status']?.toString();
    }
  }

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
        _seedFollowStatus(res.rows);
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
        _seedFollowStatus(res.rows);
      });
    } catch (_) {
      // leave `_next` so a later scroll retries
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  Future<void> _openChat(Map<String, dynamic> row) async {
    final id = row['id'];
    if (id == null) return;
    try {
      final convo = await MessageApiService.getOrCreateConversation(id.toString());
      if (!mounted) return;
      Navigator.of(context).push(MaterialPageRoute(builder: (_) => ChatScreen(conversation: convo)));
    } catch (_) {
      if (!mounted) return;
      lsSnack(context, AppLocalizations.of(context)!.usersLoadFailed, error: true);
    }
  }

  // `profile/follow/<user_id>/` toggles — one call handles Follow,
  // Follow-back, cancelling a pending request, AND unfollowing, all
  // depending on the row's current status. Callers below only differ in
  // whether they confirm first (unfollow) or not (everything else).
  Future<void> _toggleFollow(Map<String, dynamic> row) async {
    final id = row['id'];
    if (id is! int || _followBusy.contains(id)) return;
    setState(() => _followBusy.add(id));
    try {
      final result = await ApiService.followUser(id);
      if (!mounted) return;
      setState(() {
        _followStatus[id] = result['status']?.toString(); // null → back to "none"
        _followBusy.remove(id);
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _followBusy.remove(id));
      lsSnack(context, 'Could not update follow status, try again.', error: true);
    }
  }

  Future<void> _confirmUnfollow(Map<String, dynamic> row) async {
    final username = row['username']?.toString() ?? '';
    final cs = Theme.of(context).colorScheme;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Unfollow?'),
        content: Text('Unfollow @$username?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: Text('Unfollow', style: TextStyle(color: cs.error)),
          ),
        ],
      ),
    );
    if (confirmed == true) await _toggleFollow(row);
  }

  // P11-FE — "Remove follower": confirm, then OPTIMISTIC — the row leaves the list
  // immediately and is put back at its old position if the request fails.
  Future<void> _confirmRemoveFollower(Map<String, dynamic> row) async {
    final username = row['username']?.toString() ?? '';
    final cs = Theme.of(context).colorScheme;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Remove follower?'),
        content: Text("@$username will be removed from your followers. They won't be notified."),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: Text('Remove', style: TextStyle(color: cs.error)),
          ),
        ],
      ),
    );
    if (confirmed == true) await _removeFollower(row);
  }

  Future<void> _removeFollower(Map<String, dynamic> row) async {
    final id = row['id'];
    if (id is! int || _removeBusy.contains(id)) return;
    final index = _rows.indexWhere((r) => r['id'] == id);
    if (index < 0) return;

    _removeBusy.add(id);
    setState(() => _rows.removeAt(index));
    try {
      await ApiService.removeFollower(id);
      if (!mounted) return;
      lsSnack(context, 'Removed @${row['username'] ?? ''} from your followers');
      // A short list never fires the scroll listener — pull the next page ourselves.
      if (_rows.length < 10) _more();
    } catch (_) {
      if (!mounted) return;
      setState(() => _rows.insert(index > _rows.length ? _rows.length : index, row));
      lsSnack(context, 'Could not remove follower, try again.', error: true);
    } finally {
      _removeBusy.remove(id);
    }
  }

  Widget _buildRemoveMenu(Map<String, dynamic> row) {
    final cs = Theme.of(context).colorScheme;
    return PopupMenuButton<String>(
      tooltip: 'Remove follower',
      padding: EdgeInsets.zero,
      icon: Icon(Icons.more_vert_rounded, size: 20, color: cs.onSurfaceVariant),
      onSelected: (_) => _confirmRemoveFollower(row),
      itemBuilder: (_) => [
        PopupMenuItem<String>(
          value: 'remove',
          child: Text('Remove', style: TextStyle(color: cs.error)),
        ),
      ],
    );
  }

  Widget? _buildFollowButton(Map<String, dynamic> row) {
    final id = row['id'];
    if (id is! int) return null;
    final status = _followStatus[id];
    if (status == 'self') return null; // your own row in your own list
    final busy = _followBusy.contains(id);
    const shrink = MaterialTapTargetSize.shrinkWrap;

    if (status == 'ACCEPTED') {
      // TASK 7: "tap 'Following' → confirm → unfollow" — the one status
      // that gets a confirmation, since it's the only tap here that
      // actually removes an existing relationship rather than
      // creating/cancelling a request.
      return OutlinedButton(
        onPressed: busy ? null : () => _confirmUnfollow(row),
        style: OutlinedButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 12), minimumSize: Size.zero, tapTargetSize: shrink),
        child: const Text('Following', style: TextStyle(fontSize: 12)),
      );
    }
    if (status == 'PENDING') {
      return OutlinedButton(
        onPressed: busy ? null : () => _toggleFollow(row),
        style: OutlinedButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 12), minimumSize: Size.zero, tapTargetSize: shrink),
        child: busy
            ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
            : const Text('Requested', style: TextStyle(fontSize: 12)),
      );
    }
    final cs = Theme.of(context).colorScheme;
    return ElevatedButton(
      onPressed: busy ? null : () => _toggleFollow(row),
      style: ElevatedButton.styleFrom(backgroundColor: cs.primary, foregroundColor: cs.onPrimary, padding: const EdgeInsets.symmetric(horizontal: 12), minimumSize: Size.zero, tapTargetSize: shrink),
      child: busy
          ? SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: cs.onPrimary))
          : const Text('Follow', style: TextStyle(fontSize: 12)),
    );
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
        actions: [
          // P11-FE — shortcut to pending requests (same screen the bell's
          // "Follow requests" row opens). Accepting there adds followers, so
          // reload this list when coming back.
          if (_canRemove)
            IconButton(
              icon: const Icon(Icons.person_add_alt_1_outlined),
              tooltip: 'Follow requests',
              onPressed: () => Navigator.of(context)
                  .push(MaterialPageRoute(builder: (_) => const FollowRequestsScreen()))
                  .then((_) {
                if (mounted) _load();
              }),
            ),
        ],
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
                          // TASK 7 (production_readiness_tasks.md) —
                          // Instagram-style per-row controls: a Message
                          // icon plus a Follow/Following/Requested button,
                          // both self-contained InkWells so they intercept
                          // their own taps — the ListTile's own onTap below
                          // still fires for taps anywhere else on the row.
                          final followButton = _buildFollowButton(u);
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
                            trailing: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                if (u['id'] != null && _followStatus[u['id']] != 'self')
                                  IconButton(
                                    icon: Icon(Icons.chat_bubble_outline_rounded, size: 20, color: cs.onSurfaceVariant),
                                    tooltip: 'Message',
                                    visualDensity: VisualDensity.compact,
                                    onPressed: () => _openChat(u),
                                  ),
                                if (followButton != null) followButton,
                                // P11-FE — own followers list only; never on your own row.
                                if (_canRemove && u['id'] is int && _followStatus[u['id']] != 'self')
                                  _buildRemoveMenu(u),
                              ],
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
