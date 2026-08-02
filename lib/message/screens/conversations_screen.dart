// message/screens/conversations_screen.dart
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../models/message_models.dart';
import '../services/message_api_service.dart';
import '../services/message_cache_service.dart';
import 'chat_screen.dart';

class ConversationsScreen extends StatefulWidget {
  const ConversationsScreen({super.key});
  @override
  State<ConversationsScreen> createState() => _ConversationsScreenState();
}

class _ConversationsScreenState extends State<ConversationsScreen> {
  List<ConversationModel> _conversations = [];
  bool _isLoading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadFromCacheThenNetwork();
  }

  // Pehle cache se turant list dikhao (agar hai) — app khulte hi khaali
  // spinner ki jagah purani list dikhti hai. Fresh data network se aate
  // hi neeche se overwrite ho jaata hai aur naya cache save ho jaata hai.
  Future<void> _loadFromCacheThenNetwork() async {
    final cached = await MessageCacheService.getCachedConversations();
    if (cached.isNotEmpty && mounted) {
      setState(() {
        _conversations = cached;
        _isLoading = false;
      });
    }
    _loadConversations(silent: cached.isNotEmpty);
  }

  Future<void> _loadConversations({bool silent = false}) async {
    if (!silent) setState(() { _isLoading = true; _error = null; });
    try {
      final data = await MessageApiService.getConversations();
      if (mounted) setState(() { _conversations = data; _isLoading = false; });
      MessageCacheService.saveConversations(data); // fire-and-forget, latest 30
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoading = false;
          // Cache se list already dikh rahi ho to error banner se use mat
          // dabao — sirf tab dikhao jab list bilkul khaali ho.
          if (_conversations.isEmpty) _error = e.toString();
        });
      }
    }
  }

  void _openChat(ConversationModel convo) async {
    // Chat khulte hi unread badge turant clear kar do (optimistic UI)
    setState(() => convo.unreadCount = 0);
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ChatScreen(conversation: convo)),
    );
    // Wapas aane pe list refresh — last message/unread updated ho sakta hai
    _loadConversations(silent: true);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF0F2F5),
      appBar: AppBar(
        backgroundColor: const Color(0xFF030F27),
        title: const Text("Messages", style: TextStyle(color: Colors.white)),
        iconTheme: const IconThemeData(color: Colors.white),
      ),
      body: RefreshIndicator(
        onRefresh: () => _loadConversations(),
        child: _buildBody(),
      ),
    );
  }

  Widget _buildBody() {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 100),
        Center(child: Text("Failed to load: $_error", style: const TextStyle(color: Colors.red))),
        Center(
          child: TextButton(
            onPressed: () => _loadConversations(),
            child: const Text("Retry"),
          ),
        ),
      ]);
    }
    if (_conversations.isEmpty) {
      return ListView(children: const [
        SizedBox(height: 120),
        Center(child: Icon(Icons.chat_bubble_outline, size: 60, color: Colors.grey)),
        SizedBox(height: 12),
        Center(child: Text("No conversations yet", style: TextStyle(color: Colors.grey))),
      ]);
    }
    return ListView.separated(
      itemCount: _conversations.length,
      separatorBuilder: (_, __) => const Divider(height: 1, indent: 78),
      itemBuilder: (context, index) {
        final convo = _conversations[index];
        return _ConversationTile(
          conversation: convo,
          onTap: () => _openChat(convo),
        );
      },
    );
  }
}

class _ConversationTile extends StatelessWidget {
  final ConversationModel conversation;
  final VoidCallback onTap;
  const _ConversationTile({required this.conversation, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final hasUnread = conversation.unreadCount > 0;
    return ListTile(
      onTap: onTap,
      tileColor: Colors.white,
      leading: CircleAvatar(
        radius: 26,
        backgroundColor: Colors.grey[300],
        backgroundImage: conversation.displayPhoto != null && conversation.displayPhoto!.isNotEmpty
            ? CachedNetworkImageProvider(conversation.displayPhoto!)
            : null,
        child: conversation.displayPhoto == null || conversation.displayPhoto!.isEmpty
            ? Icon(conversation.isGroup ? Icons.group : Icons.person, color: Colors.grey[600])
            : null,
      ),
      title: Text(
        conversation.displayTitle,
        style: TextStyle(fontWeight: hasUnread ? FontWeight.bold : FontWeight.w600, fontSize: 15),
      ),
      subtitle: Text(
        _lastMessagePreview(),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
          color: hasUnread ? Colors.black87 : Colors.grey[600],
          fontWeight: hasUnread ? FontWeight.w600 : FontWeight.normal,
          fontSize: 13,
        ),
      ),
      trailing: Column(
        crossAxisAlignment: CrossAxisAlignment.end,
        mainAxisSize: MainAxisSize.min,
        children: [
          if (conversation.lastMessageAt != null)
            Text(
              timeago.format(conversation.lastMessageAt!, locale: 'en_short'),
              style: TextStyle(fontSize: 11, color: hasUnread ? const Color(0xFF030F27) : Colors.grey),
            ),
          const SizedBox(height: 6),
          if (hasUnread)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
              decoration: BoxDecoration(color: const Color(0xFFEE0979), borderRadius: BorderRadius.circular(12)),
              child: Text(
                conversation.unreadCount > 99 ? '99+' : conversation.unreadCount.toString(),
                style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
        ],
      ),
    );
  }

  String _lastMessagePreview() {
    if (conversation.lastMessageText == null || conversation.lastMessageText!.isEmpty) {
      switch (conversation.lastMessageType) {
        case 'image': return '📷 Photo';
        case 'video': return '🎥 Video';
        case 'audio': return '🎵 Audio';
        case 'file': return '📄 File';
        case 'presentation': return '📊 Presentation';
        case 'location': return '📍 Location';
        default: return 'No messages yet';
      }
    }
    return conversation.lastMessageText!;
  }
}