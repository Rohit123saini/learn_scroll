// lib/profile/screens/blocked_accounts_screen.dart
//
// [Settings/Nav pass] — Settings > Privacy > "Blocked accounts".
// Backend: `user_profile.BlockedUsersView` / `UnblockUserView`.
//
// Block-system pass: search box (server-side `?q=`), paged loading (30 at a
// time, loads more while scrolling) and an "Undo" on the unblock snackbar
// that blocks the person again (with the same "also block new accounts"
// setting they had).

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../api_service.dart';
import '../model.dart';
import '../../utils/api.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../../l10n/app_localizations.dart';

class BlockedAccountsScreen extends StatefulWidget {
  const BlockedAccountsScreen({super.key});

  @override
  State<BlockedAccountsScreen> createState() => _BlockedAccountsScreenState();
}

class _BlockedAccountsScreenState extends State<BlockedAccountsScreen> {
  static const int _pageSize = 30;

  final TextEditingController _search = TextEditingController();
  final ScrollController _scroll = ScrollController();
  Timer? _debounce;

  bool _loading = true;
  bool _hasError = false;
  bool _loadingMore = false;
  bool _hasMore = false;
  int? _nextOffset;
  String _query = '';
  int _loadSeq = 0; // a newer search/refresh makes older responses stale
  List<BlockedUserModel> _items = [];
  final Set<int> _unblocking = {};

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
    _load();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (_scroll.hasClients && _scroll.position.extentAfter < 300) _loadMore();
  }

  void _onSearchChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), () {
      if (!mounted || value.trim() == _query) return;
      _query = value.trim();
      _load();
    });
  }

  Future<void> _load() async {
    final seq = ++_loadSeq;
    setState(() {
      _loading = true;
      _hasError = false;
    });
    try {
      final page = await ApiService.getBlockedUsersPage(query: _query, limit: _pageSize);
      if (!mounted || seq != _loadSeq) return;
      setState(() {
        _items = page.items;
        _hasMore = page.hasMore;
        _nextOffset = page.nextOffset;
        _loading = false;
      });
    } catch (_) {
      if (!mounted || seq != _loadSeq) return;
      setState(() {
        _loading = false;
        _hasError = true;
      });
    }
  }

  Future<void> _loadMore() async {
    if (_loading || _loadingMore || !_hasMore || _nextOffset == null) return;
    final seq = _loadSeq;
    setState(() => _loadingMore = true);
    try {
      final page = await ApiService.getBlockedUsersPage(query: _query, offset: _nextOffset!, limit: _pageSize);
      if (!mounted || seq != _loadSeq) return;
      final known = _items.map((e) => e.blockedUserId).toSet();
      setState(() {
        _items = [..._items, ...page.items.where((e) => !known.contains(e.blockedUserId))];
        _hasMore = page.hasMore;
        _nextOffset = page.nextOffset;
        _loadingMore = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  Future<void> _unblock(BlockedUserModel u) async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _unblocking.add(u.blockedUserId));
    try {
      await ApiService.unblockUser(u.blockedUserId);
      if (!mounted) return;
      setState(() {
        _items.removeWhere((e) => e.blockedUserId == u.blockedUserId);
        _unblocking.remove(u.blockedUserId);
      });
      final messenger = ScaffoldMessenger.of(context);
      messenger.hideCurrentSnackBar();
      messenger.showSnackBar(SnackBar(
        content: Text(l10n.chatUserUnblocked),
        duration: const Duration(seconds: 5),
        action: SnackBarAction(label: l10n.blockedUndo, onPressed: () => _reblock(u)),
      ));
    } catch (e) {
      if (!mounted) return;
      setState(() => _unblocking.remove(u.blockedUserId));
      lsSnack(context, l10n.chatUnblockFailed(e.toString()), error: true);
    }
  }

  /// Undo of an unblock: block them again, keeping the old "new accounts" choice.
  Future<void> _reblock(BlockedUserModel u) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      await ApiService.blockUser(u.blockedUserId, blockNewAccounts: u.blockNewAccounts);
      if (!mounted) return;
      await _load();
    } catch (e) {
      if (mounted) lsSnack(context, l10n.chatBlockFailed(e.toString()), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Blocked accounts'),
      body: Column(
        children: [
          _searchField(cs),
          Expanded(child: _body(cs)),
        ],
      ),
    );
  }

  Widget _searchField(ColorScheme cs) {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
      child: TextField(
        controller: _search,
        onChanged: _onSearchChanged,
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          hintText: l10n.blockedSearchHint,
          prefixIcon: const Icon(Icons.search_rounded),
          suffixIcon: _search.text.isEmpty
              ? null
              : IconButton(
                  icon: const Icon(Icons.close_rounded),
                  onPressed: () {
                    _search.clear();
                    _query = '';
                    setState(() {});
                    _load();
                  },
                ),
          isDense: true,
          filled: true,
          fillColor: cs.surfaceVariant.withOpacity(0.5),
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
        ),
      ),
    );
  }

  Widget _body(ColorScheme cs) {
    final l10n = AppLocalizations.of(context)!;
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_hasError && _items.isEmpty) {
      return ErrorStateWidget(title: "Blocked list load nahi ho payi", retryLabel: 'Retry', onRetry: _load);
    }
    if (_items.isEmpty) {
      return _query.isEmpty
          ? const EmptyStateWidget(title: 'Aapne kisi ko block nahi kiya hai')
          : EmptyStateWidget(title: l10n.blockedNoMatches);
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.separated(
        controller: _scroll,
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.symmetric(vertical: 8),
        itemCount: _items.length + (_loadingMore ? 1 : 0),
        separatorBuilder: (_, __) => Divider(height: 1, color: cs.outlineVariant),
        itemBuilder: (context, i) {
          if (i >= _items.length) {
            return const Padding(
              padding: EdgeInsets.all(16),
              child: Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))),
            );
          }
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
                  ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                  : Text(l10n.chatUnblock),
            ),
          );
        },
      ),
    );
  }
}
