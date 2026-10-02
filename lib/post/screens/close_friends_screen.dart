// lib/post/screens/close_friends_screen.dart
//
// Stories upgrade, Part 1 — manage the Close Friends list.
//
// One list: my accepted followers + people I follow (server `candidates/`
// endpoint), each with a round check. Tapping toggles instantly (optimistic)
// and calls add/remove; on failure the row flips back and a snackbar shows.
// Search is server-side (debounced). The list is private and one-way — the
// info card says so, and nobody is notified.

import 'dart:async';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../services/close_friends_service.dart';

const Color kCloseFriendsGreen = Color(0xFF2BB673);

class CloseFriendsScreen extends StatefulWidget {
  const CloseFriendsScreen({super.key});

  @override
  State<CloseFriendsScreen> createState() => _CloseFriendsScreenState();
}

class _CloseFriendsScreenState extends State<CloseFriendsScreen> {
  final _searchController = TextEditingController();
  final _scroll = ScrollController();
  Timer? _debounce;

  List<CloseFriendUser> _users = [];
  String? _next;
  bool _loading = true;
  bool _loadingMore = false;
  bool _failed = false;
  int _requestSeq = 0; // drops stale responses when the user types fast
  final Set<int> _busy = {}; // rows with an in-flight add/remove

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
    _load();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _searchController.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (_next != null && !_loadingMore && _scroll.position.pixels > _scroll.position.maxScrollExtent - 300) {
      _loadMore();
    }
  }

  Future<void> _load() async {
    final seq = ++_requestSeq;
    setState(() { _loading = true; _failed = false; });
    try {
      final page = await CloseFriendsService.getCandidates(query: _searchController.text);
      if (!mounted || seq != _requestSeq) return;
      setState(() { _users = page.users; _next = page.next; _loading = false; });
    } catch (_) {
      if (!mounted || seq != _requestSeq) return;
      setState(() { _loading = false; _failed = true; });
    }
  }

  Future<void> _loadMore() async {
    final next = _next;
    if (next == null) return;
    final seq = _requestSeq;
    setState(() => _loadingMore = true);
    try {
      final page = await CloseFriendsService.loadMore(next);
      if (!mounted || seq != _requestSeq) return;
      setState(() { _users = [..._users, ...page.users]; _next = page.next; _loadingMore = false; });
    } catch (_) {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  void _onSearchChanged(String _) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), _load);
  }

  Future<void> _toggle(CloseFriendUser user) async {
    if (_busy.contains(user.id)) return;
    final want = !user.isCloseFriend;
    setState(() { user.isCloseFriend = want; _busy.add(user.id); });
    try {
      if (want) {
        await CloseFriendsService.add(user.id);
      } else {
        await CloseFriendsService.remove(user.id);
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => user.isCloseFriend = !want); // roll back
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(AppLocalizations.of(context)!.closeFriendsUpdateFailed)),
      );
    } finally {
      if (mounted) setState(() => _busy.remove(user.id));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.closeFriends, style: const TextStyle(fontWeight: FontWeight.w700)),
        actions: [
          TextButton(onPressed: () => Navigator.of(context).maybePop(), child: Text(l10n.closeFriendsDone)),
        ],
      ),
      body: Column(children: [
        Container(
          margin: const EdgeInsets.fromLTRB(16, 4, 16, 10),
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: kCloseFriendsGreen.withOpacity(0.12),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Icon(Icons.star_rounded, color: kCloseFriendsGreen, size: 20),
            const SizedBox(width: 10),
            Expanded(child: Text(l10n.closeFriendsInfo, style: TextStyle(fontSize: 12.5, height: 1.35, color: cs.onSurfaceVariant))),
          ]),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: TextField(
            controller: _searchController,
            onChanged: _onSearchChanged,
            textInputAction: TextInputAction.search,
            decoration: InputDecoration(
              hintText: l10n.closeFriendsSearchHint,
              prefixIcon: const Icon(Icons.search_rounded),
              isDense: true,
              filled: true,
              fillColor: cs.surfaceContainerHighest.withOpacity(0.5),
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: BorderSide.none),
            ),
          ),
        ),
        const SizedBox(height: 6),
        Expanded(child: _buildBody(l10n, cs)),
      ]),
    );
  }

  Widget _buildBody(AppLocalizations l10n, ColorScheme cs) {
    if (_loading) return const Center(child: CircularProgressIndicator(color: kCloseFriendsGreen));
    if (_failed) {
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(l10n.closeFriendsLoadFailed),
          TextButton(onPressed: _load, child: Text(l10n.retry)),
        ]),
      );
    }
    if (_users.isEmpty) {
      return Center(child: Text(l10n.closeFriendsEmpty, style: TextStyle(color: cs.onSurfaceVariant)));
    }
    return ListView.builder(
      controller: _scroll,
      itemCount: _users.length + (_loadingMore ? 1 : 0),
      itemBuilder: (_, i) {
        if (i >= _users.length) {
          return const Padding(
            padding: EdgeInsets.all(16),
            child: Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2, color: kCloseFriendsGreen))),
          );
        }
        final u = _users[i];
        return ListTile(
          onTap: () => _toggle(u),
          leading: CircleAvatar(
            backgroundColor: cs.surfaceContainerHighest,
            backgroundImage: (u.profilePicture ?? '').isNotEmpty ? CachedNetworkImageProvider(u.profilePicture!) : null,
            child: (u.profilePicture ?? '').isEmpty
                ? Text(u.username.isNotEmpty ? u.username[0].toUpperCase() : '?', style: TextStyle(color: cs.primary, fontWeight: FontWeight.w700))
                : null,
          ),
          title: Text(u.username, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
          subtitle: u.name != u.username ? Text(u.name, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)) : null,
          trailing: AnimatedContainer(
            duration: const Duration(milliseconds: 150),
            width: 24,
            height: 24,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: u.isCloseFriend ? kCloseFriendsGreen : Colors.transparent,
              border: Border.all(color: u.isCloseFriend ? kCloseFriendsGreen : cs.outline, width: 1.6),
            ),
            child: u.isCloseFriend ? const Icon(Icons.check_rounded, size: 16, color: Colors.white) : null,
          ),
        );
      },
    );
  }
}
