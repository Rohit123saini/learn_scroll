// lib/profile/screens/blocked_accounts_screen.dart
//
// [Settings/Nav pass] — Settings > Privacy > "Blocked accounts". Backend
// (`user_profile.BlockedUsersView`/`UnblockUserView`) tha hi production-
// ready, bas is list ko manage karne ki koi jagah nahi thi poori app me.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../api_service.dart';
import '../model.dart';
import '../../utils/api.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';

class BlockedAccountsScreen extends StatefulWidget {
  const BlockedAccountsScreen({super.key});

  @override
  State<BlockedAccountsScreen> createState() => _BlockedAccountsScreenState();
}

class _BlockedAccountsScreenState extends State<BlockedAccountsScreen> {
  bool _loading = true;
  bool _hasError = false;
  List<BlockedUserModel> _items = [];
  final Set<int> _unblocking = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _hasError = false;
    });
    try {
      final items = await ApiService.getBlockedUsers();
      if (!mounted) return;
      setState(() {
        _items = items;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _hasError = true;
      });
    }
  }

  Future<void> _unblock(BlockedUserModel u) async {
    setState(() => _unblocking.add(u.blockedUserId));
    try {
      await ApiService.unblockUser(u.blockedUserId);
      if (!mounted) return;
      setState(() {
        _items.removeWhere((e) => e.blockedUserId == u.blockedUserId);
        _unblocking.remove(u.blockedUserId);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _unblocking.remove(u.blockedUserId));
      lsSnack(context, e.toString(), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Blocked accounts'),
      body: _body(cs),
    );
  }

  Widget _body(ColorScheme cs) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_hasError && _items.isEmpty) {
      return ErrorStateWidget(title: "Blocked list load nahi ho payi", retryLabel: 'Retry', onRetry: _load);
    }
    if (_items.isEmpty) {
      return const EmptyStateWidget(title: 'Aapne kisi ko block nahi kiya hai');
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.separated(
        padding: const EdgeInsets.symmetric(vertical: 8),
        itemCount: _items.length,
        separatorBuilder: (_, __) => Divider(height: 1, color: cs.outlineVariant),
        itemBuilder: (context, i) {
          final u = _items[i];
          final photo = u.profilePhoto;
          final url = photo.isEmpty ? '' : (photo.startsWith('http') ? photo : '${Api.baseUrl}$photo');
          final busy = _unblocking.contains(u.blockedUserId);
          return ListTile(
            leading: CircleAvatar(
              radius: 20,
              backgroundColor: cs.surfaceVariant,
              backgroundImage: url.isEmpty ? null : CachedNetworkImageProvider(url),
              child: url.isEmpty ? Icon(Icons.person_rounded, color: cs.onSurfaceVariant) : null,
            ),
            title: Text(u.username, style: TextStyle(color: cs.onSurface, fontWeight: FontWeight.w600)),
            trailing: OutlinedButton(
              onPressed: busy ? null : () => _unblock(u),
              child: busy
                  ? const SizedBox(
                      width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Text('Unblock'),
            ),
          );
        },
      ),
    );
  }
}
