// M9b — Dedicated "Pending requests" screen (admin/moderator only).
// group_profile_screen.dart ke same folder me rakho.
//
// Pop result: `true` agar kam se kam ek request approve hui (taaki
// GroupProfileScreen member list/count refresh kar sake).

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../services/message_api_service.dart';
import '../../theme_service.dart'; // AppThemeTokens

class PendingRequestsScreen extends StatefulWidget {
  final String groupId;
  const PendingRequestsScreen({super.key, required this.groupId});

  @override
  State<PendingRequestsScreen> createState() => _PendingRequestsScreenState();
}

class _PendingRequestsScreenState extends State<PendingRequestsScreen> {
  List<Map<String, dynamic>> _requests = [];
  final Set<String> _busy = {}; // jin requests pe call chal rahi hai
  bool _loading = true;
  String? _error;
  bool _approvedAny = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final raw = await MessageApiService.getJoinRequests(widget.groupId);
      final out = <Map<String, dynamic>>[];
      for (final r in raw) {
        if (r is! Map) continue;
        final id = r['id']?.toString() ?? '';
        if (id.isEmpty) continue;
        final u = r['user'];
        String pick(String k) => (u is Map ? u[k] : r[k])?.toString().trim() ?? '';
        final full = [pick('first_name'), pick('last_name')].where((s) => s.isNotEmpty).join(' ');
        final username = pick('username');
        out.add({
          'id': id,
          'name': full.isNotEmpty ? full : (username.isNotEmpty ? username : 'User'),
          'username': username,
          'avatar': pick('avatar'),
        });
      }
      if (!mounted) return;
      setState(() {
        _requests = out;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = 'Requests load nahi ho paayi: $e';
      });
    }
  }

  Future<void> _respond(String id, bool approve) async {
    if (_busy.contains(id)) return;
    setState(() => _busy.add(id));
    try {
      if (approve) {
        await MessageApiService.approveJoinRequest(widget.groupId, id);
        _approvedAny = true;
      } else {
        await MessageApiService.rejectJoinRequest(widget.groupId, id);
      }
      if (!mounted) return;
      setState(() {
        _requests.removeWhere((r) => r['id'] == id);
        _busy.remove(id);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy.remove(id));
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Request update fail: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tokens = AppThemeTokens.of(context);

    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) Navigator.of(context).pop(_approvedAny);
      },
      child: Scaffold(
        appBar: AppBar(title: const Text('Pending requests')),
        body: _buildBody(cs, tokens),
      ),
    );
  }

  Widget _buildBody(ColorScheme cs, dynamic tokens) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(_error!, textAlign: TextAlign.center),
            const SizedBox(height: 12),
            OutlinedButton(onPressed: _load, child: const Text('Retry')),
          ]),
        ),
      );
    }
    if (_requests.isEmpty) {
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(Icons.how_to_reg_rounded, size: 44, color: cs.onSurfaceVariant),
          const SizedBox(height: 10),
          Text('Koi pending request nahi hai', style: TextStyle(color: cs.onSurfaceVariant)),
        ]),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.separated(
        padding: const EdgeInsets.symmetric(vertical: 8),
        itemCount: _requests.length,
        separatorBuilder: (_, __) => const Divider(height: 1),
        itemBuilder: (_, i) {
          final r = _requests[i];
          final id = r['id'] as String;
          final avatar = r['avatar'] as String;
          final busy = _busy.contains(id);
          return ListTile(
            leading: CircleAvatar(
              backgroundImage: avatar.isNotEmpty ? CachedNetworkImageProvider(avatar) : null,
              child: avatar.isEmpty ? const Icon(Icons.person_rounded) : null,
            ),
            title: Text(r['name'] as String, style: const TextStyle(fontWeight: FontWeight.w600)),
            subtitle: (r['username'] as String).isNotEmpty ? Text('@${r['username']}') : null,
            trailing: busy
                ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                : Row(mainAxisSize: MainAxisSize.min, children: [
                    TextButton(
                      onPressed: () => _respond(id, false),
                      child: Text('Decline', style: TextStyle(color: cs.error)),
                    ),
                    const SizedBox(width: 4),
                    FilledButton(
                      style: FilledButton.styleFrom(backgroundColor: tokens.coral),
                      onPressed: () => _respond(id, true),
                      child: const Text('Approve'),
                    ),
                  ]),
          );
        },
      ),
    );
  }
}
