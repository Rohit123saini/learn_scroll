// message/screens/message_requests_screen.dart
//
// 🔥 NAYA (M1-FE) — "Message requests" folder (Instagram jaisa).
//
// Backend (M1-BE):
//   GET  /message/requests/                      -> pending requests (page size 20)
//   POST /message/requests/<id>/accept|decline/  -> ChatScreen ke bottom bar se
//   WS inbox: `message_request`, `message_request_resolved`
//
// Is screen ka kaam sirf list dikhana hai. Request kholne par ChatScreen
// `isMessageRequest: true` ke saath khulti hai — wahan Accept / Delete / Block
// bar aata hai aur accept hone tak reply input band rehta hai.

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../models/message_models.dart';
import '../services/message_api_service.dart';
import '../services/inbox_socket_service.dart';
import 'chat_screen.dart';
import '../../theme_service.dart';

class MessageRequestsScreen extends StatefulWidget {
  const MessageRequestsScreen({super.key});
  @override
  State<MessageRequestsScreen> createState() => _MessageRequestsScreenState();
}

class _MessageRequestsScreenState extends State<MessageRequestsScreen> {
  final List<ConversationModel> _items = [];
  final ScrollController _scroll = ScrollController();
  StreamSubscription? _inboxSub;
  Timer? _reloadDebounce;

  bool _isLoading = true;
  bool _isLoadingMore = false;
  bool _hasMore = false;
  int _page = 1;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
    _scroll.addListener(_onScroll);
    // Doosre device pe accept/decline ya naya request aaye to list sync.
    _inboxSub = InboxSocketService.instance.events.listen((event) {
      final type = event['type'];
      if (type == 'message_request' || type == 'message_request_resolved') {
        _reloadDebounce?.cancel();
        _reloadDebounce = Timer(const Duration(milliseconds: 400), () => _load(silent: true));
      }
    });
  }

  @override
  void dispose() {
    _reloadDebounce?.cancel();
    _inboxSub?.cancel();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) setState(() { _isLoading = true; _error = null; });
    try {
      final page = await MessageApiService.getMessageRequests(page: 1);
      InboxSocketService.instance.applyRequestsPage(page); // badge/count sync
      if (!mounted) return;
      setState(() {
        _items
          ..clear()
          ..addAll(page.items);
        _page = 1;
        _hasMore = page.hasMore;
        _isLoading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isLoading = false;
        if (_items.isEmpty) _error = e.toString();
      });
    }
  }

  void _onScroll() {
    if (!_scroll.hasClients || _isLoadingMore || !_hasMore || _isLoading) return;
    if (_scroll.position.extentAfter < 300) _loadMore();
  }

  Future<void> _loadMore() async {
    setState(() => _isLoadingMore = true);
    try {
      final page = await MessageApiService.getMessageRequests(page: _page + 1);
      if (!mounted) return;
      setState(() {
        final known = _items.map((c) => c.id).toSet();
        _items.addAll(page.items.where((c) => !known.contains(c.id)));
        _page += 1;
        _hasMore = page.hasMore;
        _isLoadingMore = false;
      });
    } catch (_) {
      if (mounted) setState(() => _isLoadingMore = false);
    }
  }

  Future<void> _openRequest(ConversationModel convo) async {
    setState(() => convo.unreadCount = 0);
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ChatScreen(conversation: convo, isMessageRequest: true)),
    );
    if (!mounted) return;
    _load(silent: true); // accept/delete/block ke baad list se hat jaani chahiye
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: cs.primary,
        elevation: 0,
        iconTheme: IconThemeData(color: cs.onPrimary),
        title: Text(
          'Message requests',
          style: TextStyle(color: cs.onPrimary, fontWeight: FontWeight.w700, fontSize: 20),
        ),
      ),
      body: RefreshIndicator(
        color: cs.primary,
        onRefresh: () => _load(silent: true),
        child: _buildBody(cs),
      ),
    );
  }

  Widget _buildBody(ColorScheme cs) {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 100),
        Icon(Icons.wifi_off_rounded, size: 52, color: cs.onSurfaceVariant),
        const SizedBox(height: 12),
        Center(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Text('Failed to load: $_error',
                textAlign: TextAlign.center, style: TextStyle(color: cs.onSurfaceVariant)),
          ),
        ),
        const SizedBox(height: 8),
        Center(
          child: TextButton(
            onPressed: _load,
            child: Text('Retry', style: TextStyle(color: cs.primary, fontWeight: FontWeight.bold)),
          ),
        ),
      ]);
    }
    if (_items.isEmpty) {
      return ListView(children: [
        const SizedBox(height: 130),
        Center(
          child: Container(
            width: 88, height: 88,
            decoration: BoxDecoration(color: cs.primary.withOpacity(0.06), shape: BoxShape.circle),
            child: Icon(Icons.mark_chat_unread_outlined, size: 40, color: cs.primary.withOpacity(0.5)),
          ),
        ),
        const SizedBox(height: 14),
        Center(
          child: Text('No message requests',
              style: TextStyle(color: cs.onSurfaceVariant, fontSize: 14, fontWeight: FontWeight.w500)),
        ),
      ]);
    }

    return ListView.separated(
      controller: _scroll,
      physics: const AlwaysScrollableScrollPhysics(),
      // +1 header (info text), +1 footer (load-more spinner) jab zaroorat ho
      itemCount: _items.length + 1 + (_isLoadingMore ? 1 : 0),
      separatorBuilder: (_, i) => i == 0
          ? const SizedBox.shrink()
          : Divider(height: 1, indent: 84, color: cs.outlineVariant),
      itemBuilder: (context, index) {
        if (index == 0) return _buildInfoHeader(cs);
        final i = index - 1;
        if (i >= _items.length) {
          return const Padding(
            padding: EdgeInsets.all(16),
            child: Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))),
          );
        }
        return _RequestTile(conversation: _items[i], onTap: () => _openRequest(_items[i]));
      },
    );
  }

  Widget _buildInfoHeader(ColorScheme cs) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Text(
        'Ye un logon ke messages hain jinhe aap follow nahi karte. Accept karne par chat aapke inbox me aa jaayegi; tab tak unhe ye nahi dikhega ki aapne message padha.',
        style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant),
      ),
    );
  }
}

class _RequestTile extends StatelessWidget {
  final ConversationModel conversation;
  final VoidCallback onTap;
  const _RequestTile({required this.conversation, required this.onTap});

  String _preview() {
    final text = conversation.lastMessageText;
    if (text != null && text.isNotEmpty) return text;
    switch (conversation.lastMessageType) {
      case 'image': return '📷 Photo';
      case 'video': return '🎥 Video';
      case 'audio': return '🎵 Audio';
      case 'file': return '📄 File';
      case 'presentation': return '📊 Presentation';
      case 'location': return '📍 Location';
      default: return 'Sent you a message';
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tokens = AppThemeTokens.of(context);
    final hasUnread = conversation.unreadCount > 0;
    final photo = conversation.displayPhoto;
    final hasPhoto = photo != null && photo.isNotEmpty;

    return InkWell(
      onTap: onTap,
      child: Container(
        color: cs.surface,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        child: Row(children: [
          Container(
            padding: const EdgeInsets.all(2),
            decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: cs.outlineVariant, width: 1)),
            child: CircleAvatar(
              radius: 27,
              backgroundColor: tokens.surface2,
              backgroundImage: hasPhoto ? CachedNetworkImageProvider(photo) : null,
              child: !hasPhoto ? Icon(Icons.person_rounded, color: cs.onSurfaceVariant) : null,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(
                conversation.displayTitle,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontWeight: hasUnread ? FontWeight.bold : FontWeight.w600,
                  fontSize: 15.5,
                  color: cs.onSurface,
                ),
              ),
              const SizedBox(height: 3),
              Text(
                _preview(),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: hasUnread ? cs.onSurface : cs.onSurfaceVariant,
                  fontWeight: hasUnread ? FontWeight.w600 : FontWeight.normal,
                  fontSize: 13.5,
                ),
              ),
            ]),
          ),
          const SizedBox(width: 8),
          Column(crossAxisAlignment: CrossAxisAlignment.end, mainAxisSize: MainAxisSize.min, children: [
            if (conversation.lastMessageAt != null)
              Text(
                timeago.format(conversation.lastMessageAt!, locale: 'en_short'),
                style: TextStyle(
                  fontSize: 11.5,
                  color: hasUnread ? tokens.coral : cs.onSurfaceVariant,
                  fontWeight: hasUnread ? FontWeight.w600 : FontWeight.normal,
                ),
              ),
            const SizedBox(height: 8),
            if (hasUnread)
              Container(
                constraints: const BoxConstraints(minWidth: 20),
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(color: tokens.coral, borderRadius: BorderRadius.circular(12)),
                child: Text(
                  conversation.unreadCount > 99 ? '99+' : conversation.unreadCount.toString(),
                  textAlign: TextAlign.center,
                  style: TextStyle(color: cs.onSecondary, fontSize: 11, fontWeight: FontWeight.bold),
                ),
              )
            else
              const SizedBox(height: 20),
          ]),
        ]),
      ),
    );
  }
}
