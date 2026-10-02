// ============================================================
// FOLLOW REQUESTS  (P11-FE)
//
//   GET  /profile/follow-requests/            (paginated, via ApiService.getFollowRequests)
//   POST /profile/accept-request/<follow_id>/ (ApiService.acceptFollowRequest)
//   POST /profile/reject-request/<follow_id>/ (ApiService.rejectFollowRequest)
//
// Pending requests addressed to the logged-in user (private accounts). Every
// row is the requester's user row + `follow_id`.
//
// Confirm / Delete are OPTIMISTIC: the row leaves the list the instant you
// tap, and is put back at its old position (with a snackbar) if the request
// fails. A pull-to-refresh re-syncs with the server.
//
// Entry points:
//   * notifications → the "Follow requests" row (N10):
//       Navigator.push(context, MaterialPageRoute(builder: (_) => const FollowRequestsScreen()))
//   * own followers list → app-bar "Follow requests" icon (follow_list_screen.dart)
// ============================================================

import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../api_service.dart';
import 'target_profile.dart';

class FollowRequestsScreen extends StatefulWidget {
  const FollowRequestsScreen({super.key});

  @override
  State<FollowRequestsScreen> createState() => _FollowRequestsScreenState();
}

class _FollowRequestsScreenState extends State<FollowRequestsScreen> {
  final _scroll = ScrollController();
  final List<Map<String, dynamic>> _rows = [];
  String? _next;
  bool _loading = true;
  bool _loadingMore = false;
  bool _error = false;
  int _loadToken = 0; // drops a slow response that lands after a newer refresh
  final Set<int> _busy = {}; // follow_ids with an accept/delete in flight

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

  Future<void> _load() async {
    final token = ++_loadToken;
    setState(() {
      _loading = true;
      _error = false;
    });
    try {
      final res = await ApiService.getFollowRequests();
      if (!mounted || token != _loadToken) return;
      setState(() {
        _rows
          ..clear()
          ..addAll(res.rows);
        _next = res.next;
        _loading = false;
      });
    } catch (_) {
      if (!mounted || token != _loadToken) return;
      setState(() {
        _error = true;
        _loading = false;
      });
    }
  }

  Future<void> _more() async {
    final next = _next;
    if (_loading || _loadingMore || next == null || next.isEmpty) return;
    final token = _loadToken;
    setState(() => _loadingMore = true);
    try {
      final res = await ApiService.getFollowRequests(nextUrl: next);
      if (!mounted || token != _loadToken) return;
      setState(() {
        final known = _rows.map((r) => r['follow_id']).toSet();
        _rows.addAll(res.rows.where((r) => !known.contains(r['follow_id'])));
        _next = res.next;
      });
    } catch (_) {
      // leave `_next` so a later scroll retries
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  Future<void> _resolve(Map<String, dynamic> row, {required bool accept}) async {
    final followId = row['follow_id'];
    if (followId is! int || _busy.contains(followId)) return;
    final index = _rows.indexOf(row);
    if (index < 0) return;
    final username = row['username']?.toString() ?? '';

    _busy.add(followId);
    setState(() => _rows.removeAt(index)); // optimistic
    try {
      if (accept) {
        await ApiService.acceptFollowRequest(followId);
      } else {
        await ApiService.rejectFollowRequest(followId);
      }
      if (!mounted) return;
      if (accept) lsSnack(context, '@$username is now following you');
      // Removing rows can leave the list too short to scroll — fetch more ourselves.
      if (_rows.length < 10) _more();
    } catch (_) {
      if (!mounted) return;
      setState(() => _rows.insert(index > _rows.length ? _rows.length : index, row)); // rollback
      lsSnack(
        context,
        accept ? 'Could not confirm the request, try again.' : 'Could not delete the request, try again.',
        error: true,
      );
    } finally {
      _busy.remove(followId);
    }
  }

  Widget _requestTile(Map<String, dynamic> u, ColorScheme cs, AppLocalizations l10n) {
    final username = u['username']?.toString() ?? '';
    final name = '${u['first_name'] ?? ''} ${u['last_name'] ?? ''}'.trim();
    final mutual = u['mutual_friends'];
    const shrink = MaterialTapTargetSize.shrinkWrap;
    const pad = EdgeInsets.symmetric(horizontal: 12);

    return ListTile(
      key: ValueKey('fr-${u['follow_id']}'),
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
          ElevatedButton(
            onPressed: () => _resolve(u, accept: true),
            style: ElevatedButton.styleFrom(
              backgroundColor: cs.primary,
              foregroundColor: cs.onPrimary,
              padding: pad,
              minimumSize: Size.zero,
              tapTargetSize: shrink,
            ),
            child: Text(l10n.confirm, style: const TextStyle(fontSize: 12)),
          ),
          const SizedBox(width: 8),
          OutlinedButton(
            onPressed: () => _resolve(u, accept: false),
            style: OutlinedButton.styleFrom(padding: pad, minimumSize: Size.zero, tapTargetSize: shrink),
            child: Text(l10n.delete, style: const TextStyle(fontSize: 12)),
          ),
        ],
      ),
      onTap: username.isEmpty
          ? null
          : () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => TargetProfilePage(username: username))),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    Widget body;
    if (_loading) {
      body = const Center(child: CircularProgressIndicator());
    } else if (_error) {
      body = ListView(children: [
        const SizedBox(height: 120),
        Center(child: Text(l10n.usersLoadFailed, style: TextStyle(color: cs.onSurfaceVariant))),
        Center(child: TextButton(onPressed: _load, child: Text(l10n.retry))),
      ]);
    } else if (_rows.isEmpty) {
      body = ListView(children: [
        const SizedBox(height: 120),
        Icon(Icons.person_add_alt_1_outlined, size: 40, color: cs.onSurfaceVariant),
        const SizedBox(height: 12),
        Center(child: Text('No follow requests', style: TextStyle(color: cs.onSurfaceVariant))),
      ]);
    } else {
      body = ListView.builder(
        controller: _scroll,
        physics: const AlwaysScrollableScrollPhysics(),
        itemCount: _rows.length + (_loadingMore ? 1 : 0),
        itemBuilder: (_, i) {
          if (i >= _rows.length) {
            return const Padding(
              padding: EdgeInsets.symmetric(vertical: 18),
              child: Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2))),
            );
          }
          return _requestTile(_rows[i], cs, l10n);
        },
      );
    }

    return Scaffold(
      backgroundColor: cs.surface,
      appBar: AppBar(
        backgroundColor: cs.surface,
        elevation: 0,
        iconTheme: IconThemeData(color: cs.onSurface),
        title: Text('Follow requests', style: LsType.head(context, size: 16)),
      ),
      body: RefreshIndicator(onRefresh: _load, child: body),
    );
  }
}
