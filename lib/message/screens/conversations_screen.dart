// message/screens/conversations_screen.dart
//
// 🎨 REDESIGN — sab logic/state/API calls bilkul same hain (kuch bhi tod
// nahi hai), sirf visuals professional/polished bana diye hain — rounded
// search pill, better avatar treatment, cleaner unread badges, nicer empty
// states, aur ab wahi bottom nav bar bhi hai jo HomeScreen me hai (Home /
// Search / Chats / Profile) — dekho app_bottom_nav.dart.

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../models/message_models.dart';
import '../services/message_api_service.dart';
import '../services/message_cache_service.dart';
import '../services/inbox_socket_service.dart';
import 'chat_screen.dart';
import 'create_group_screen.dart';
import 'app_bottom_nav.dart'; // 🔥 NAYA

const _kNavy = Color(0xFF030F27);
const _kAccent = Color(0xFFEE0979);
const _kBg = Color(0xFFF6F7FB);

class ConversationsScreen extends StatefulWidget {
  const ConversationsScreen({super.key});
  @override
  State<ConversationsScreen> createState() => _ConversationsScreenState();
}

class _ConversationsScreenState extends State<ConversationsScreen> {
  List<ConversationModel> _conversations = [];
  bool _isLoading = true;
  String? _error;

  final _searchController = TextEditingController();
  final _searchFocusNode = FocusNode();
  Timer? _debounce;
  List<Map<String, dynamic>> _searchResults = [];
  bool _isSearching = false;
  String? _searchError;
  bool _isOpeningChat = false;
  bool _isSearchExpanded = false;

  StreamSubscription? _inboxSub;

  // 🔥 NAYA — Long-press select mode: WhatsApp jaisa. Long-press se
  // select mode on hota hai (jis chat pe long-press hua wo select ho
  // jaati hai), fir tap se aur chats select/deselect kar sakte ho.
  // AppBar tab "N selected" dikhata hai delete/pin/rename actions ke
  // saath.
  bool _isSelectMode = false;
  final Set<String> _selectedIds = {};

  // 🔥 NAYA — Pin aur custom label ke liye local overrides. Jab tak
  // `ConversationModel` me ye fields add nahi ho jaate (backend already
  // support karta hai — `is_pinned` field + `.../settings/` aur
  // `.../label/` endpoints), UI turant reflect ho isliye yahi maintain
  // kar rahe hain, keyed by conversation.id.
  final Map<String, bool> _pinnedOverride = {};
  final Map<String, String> _labelOverride = {};

  @override
  void initState() {
    super.initState();
    _loadFromCacheThenNetwork();
    InboxSocketService.instance.connect();
    _inboxSub = InboxSocketService.instance.events.listen(_onInboxUpdate);
  }

  void _onInboxUpdate(Map<String, dynamic> event) {
    if (event['type'] != 'inbox_update') return;
    final conversationId = event['conversation_id']?.toString();
    if (conversationId == null) return;

    final index = _conversations.indexWhere((c) => c.id == conversationId);
    if (index == -1) {
      _loadConversations(silent: true);
      return;
    }

    final createdAtRaw = event['created_at']?.toString();
    final createdAt = createdAtRaw != null ? DateTime.tryParse(createdAtRaw) : null;

    setState(() {
      final convo = _conversations[index];
      convo.lastMessageText = event['last_message_text']?.toString();
      convo.lastMessageType = event['last_message_type']?.toString();
      convo.lastMessageAt = createdAt ?? convo.lastMessageAt;
      convo.unreadCount = convo.unreadCount + 1;
      _conversations.removeAt(index);
      _conversations.insert(0, convo);
    });

    MessageCacheService.saveConversations(_conversations);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _searchController.dispose();
    _searchFocusNode.dispose();
    _inboxSub?.cancel();
    super.dispose();
  }

  void _toggleSearch() {
    setState(() => _isSearchExpanded = !_isSearchExpanded);
    if (_isSearchExpanded) {
      Future.delayed(const Duration(milliseconds: 50), () {
        if (mounted && _isSearchExpanded) _searchFocusNode.requestFocus();
      });
    } else {
      _searchFocusNode.unfocus();
      _searchController.clear();
      setState(() => _searchResults = []);
    }
  }

  void _onSearchChanged(String query) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), () => _runSearch(query));
    setState(() => _searchError = null);
  }

  Future<void> _runSearch(String query) async {
    if (query.trim().isEmpty) {
      setState(() => _searchResults = []);
      return;
    }
    setState(() => _isSearching = true);
    try {
      final results = await MessageApiService.searchUsers(query);
      if (!mounted) return;
      setState(() {
        _searchResults = results;
        _isSearching = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isSearching = false;
        _searchError = e.toString();
      });
    }
  }

  String _displayName(Map<String, dynamic> user) {
    final first = (user['first_name'] ?? '').toString().trim();
    final last = (user['last_name'] ?? '').toString().trim();
    final full = [first, last].where((s) => s.isNotEmpty).join(' ');
    if (full.isNotEmpty) return full;
    final username = (user['username'] ?? '').toString().trim();
    return username.isNotEmpty ? username : 'User';
  }

  Future<void> _openUserChat(Map<String, dynamic> user) async {
    final userId = user['id']?.toString();
    if (userId == null || _isOpeningChat) return;

    setState(() => _isOpeningChat = true);
    try {
      final convo = await MessageApiService.startPrivateChat(userId);
      if (!mounted) return;
      _searchController.clear();
      setState(() {
        _searchResults = [];
        _isOpeningChat = false;
        _isSearchExpanded = false;
      });
      await Navigator.push(
        context,
        MaterialPageRoute(builder: (_) => ChatScreen(conversation: convo)),
      );
      _loadConversations(silent: true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _isOpeningChat = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Chat shuru nahi ho payi: $e")),
      );
    }
  }

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
      MessageCacheService.saveConversations(data);
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoading = false;
          if (_conversations.isEmpty) _error = e.toString();
        });
      }
    }
  }

  void _openChat(ConversationModel convo) async {
    if (_isSelectMode) {
      _toggleSelect(convo.id);
      return;
    }
    setState(() => convo.unreadCount = 0);
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ChatScreen(conversation: convo)),
    );
    _loadConversations(silent: true);
  }

  bool _isPinned(ConversationModel c) => _pinnedOverride[c.id] ?? false;

  String _displayLabel(ConversationModel c) =>
      _labelOverride[c.id] ?? c.displayTitle;

  List<ConversationModel> _sortedConversations() {
    final list = List<ConversationModel>.from(_conversations);
    list.sort((a, b) {
      final pinnedA = _isPinned(a) ? 1 : 0;
      final pinnedB = _isPinned(b) ? 1 : 0;
      if (pinnedA != pinnedB) return pinnedB - pinnedA; // pinned pehle
      final atA = a.lastMessageAt;
      final atB = b.lastMessageAt;
      if (atA == null && atB == null) return 0;
      if (atA == null) return 1;
      if (atB == null) return -1;
      return atB.compareTo(atA);
    });
    return list;
  }

  void _enterSelectMode(String conversationId) {
    setState(() {
      _isSelectMode = true;
      _selectedIds.add(conversationId);
    });
  }

  void _toggleSelect(String conversationId) {
    setState(() {
      if (_selectedIds.contains(conversationId)) {
        _selectedIds.remove(conversationId);
        if (_selectedIds.isEmpty) _isSelectMode = false;
      } else {
        _selectedIds.add(conversationId);
      }
    });
  }

  void _exitSelectMode() {
    setState(() {
      _isSelectMode = false;
      _selectedIds.clear();
    });
  }

  // 🔥 NAYA — 1: Selected chat(s) delete karna. Sirf apni taraf se hide
  // hoti hain (backend `bulk_delete` — dusre participant/group ki chat
  // waisi hi rehti hai).
  Future<void> _confirmDeleteSelected() async {
    final ids = _selectedIds.toList();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Chat delete karein?'),
        content: Text(
          ids.length == 1
              ? 'Ye chat delete ho jaayegi. Dusre participant ki chat waisi hi rahegi.'
              : '${ids.length} chats delete ho jaayengi. Dusre participants ki chat waisi hi rahegi.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    try {
      await MessageApiService.bulkDeleteConversations(ids);
      if (!mounted) return;
      setState(() {
        _conversations.removeWhere((c) => ids.contains(c.id));
        for (final id in ids) {
          _pinnedOverride.remove(id);
          _labelOverride.remove(id);
        }
        _isSelectMode = false;
        _selectedIds.clear();
      });
      MessageCacheService.saveConversations(_conversations);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Delete fail: $e')),
      );
    }
  }

  // 🔥 NAYA — 2: Selected chat(s) ko pin/unpin karna (top pe rakhna). Agar
  // koi ek bhi selected chat already pinned nahi hai to sabko pin karo,
  // warna sabko unpin karo (WhatsApp jaisa toggle behaviour).
  Future<void> _togglePinSelected() async {
    final ids = _selectedIds.toList();
    final shouldPin = ids.any((id) => !(_pinnedOverride[id] ?? false));

    // Optimistic UI update.
    setState(() {
      for (final id in ids) {
        _pinnedOverride[id] = shouldPin;
      }
    });

    try {
      for (final id in ids) {
        await MessageApiService.updateSettings(id, isPinned: shouldPin);
      }
    } catch (e) {
      if (!mounted) return;
      // rollback on failure
      setState(() {
        for (final id in ids) {
          _pinnedOverride[id] = !shouldPin;
        }
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Pin update fail: $e')),
      );
      return;
    }

    if (!mounted) return;
    setState(() {
      _isSelectMode = false;
      _selectedIds.clear();
    });
  }

  // 🔥 NAYA — 3: Chat ka label/nickname change karna. Sirf single chat
  // select hone par hi available hai.
  Future<void> _renameSelected() async {
    final id = _selectedIds.first;
    final convo = _conversations.firstWhere((c) => c.id == id);
    final controller = TextEditingController(text: _labelOverride[id] ?? '');

    final newLabel = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Chat ka naam badlein'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLength: 100,
          decoration: InputDecoration(hintText: convo.displayTitle),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    if (newLabel == null) return;

    try {
      final saved = await MessageApiService.updateConversationLabel(id, newLabel);
      if (!mounted) return;
      setState(() {
        if (saved == null || saved.isEmpty) {
          _labelOverride.remove(id);
        } else {
          _labelOverride[id] = saved;
        }
        _isSelectMode = false;
        _selectedIds.clear();
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Naam save nahi hua: $e')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      appBar: _isSelectMode ? _buildSelectionAppBar() : _buildDefaultAppBar(),
      body: Column(
        children: [
          AnimatedSize(
            duration: const Duration(milliseconds: 220),
            curve: Curves.easeInOut,
            child: _isSearchExpanded ? _buildSearchField() : const SizedBox(width: double.infinity),
          ),
          Expanded(
            child: _isSearchExpanded && _searchController.text.trim().isNotEmpty
                ? _buildSearchResults()
                : RefreshIndicator(
                    color: _kNavy,
                    onRefresh: () => _loadConversations(),
                    child: _buildBody(),
                  ),
          ),
        ],
      ),
      bottomNavigationBar: const AppBottomNav(current: AppTab.chats), // 🔥 NAYA
    );
  }

  AppBar _buildDefaultAppBar() {
    return AppBar(
      backgroundColor: _kNavy,
      elevation: 0,
      title: const Text(
        "Chats",
        style: TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 20),
      ),
      iconTheme: const IconThemeData(color: Colors.white),
      actions: [
        IconButton(
          icon: Icon(_isSearchExpanded ? Icons.close : Icons.search_rounded, color: Colors.white),
          tooltip: _isSearchExpanded ? 'Close search' : 'Search',
          onPressed: _toggleSearch,
        ),
        IconButton(
          icon: const Icon(Icons.group_add_rounded, color: Colors.white),
          tooltip: 'New group',
          onPressed: () async {
            await Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const CreateGroupScreen()),
            );
            _loadConversations(silent: true);
          },
        ),
        const SizedBox(width: 4),
      ],
    );
  }

  // 🔥 NAYA — Selection mode ka AppBar: "N selected" + Pin/Unpin, Rename
  // (sirf 1 select ho to) aur Delete actions.
  AppBar _buildSelectionAppBar() {
    final count = _selectedIds.length;
    final allPinned = _selectedIds.isNotEmpty &&
        _selectedIds.every((id) => _pinnedOverride[id] ?? false);
    return AppBar(
      backgroundColor: _kNavy,
      elevation: 0,
      leading: IconButton(
        icon: const Icon(Icons.close_rounded, color: Colors.white),
        tooltip: 'Cancel',
        onPressed: _exitSelectMode,
      ),
      title: Text(
        '$count selected',
        style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 18),
      ),
      iconTheme: const IconThemeData(color: Colors.white),
      actions: [
        if (count == 1)
          IconButton(
            icon: const Icon(Icons.edit_rounded, color: Colors.white),
            tooltip: 'Rename',
            onPressed: _renameSelected,
          ),
        IconButton(
          icon: Icon(allPinned ? Icons.push_pin : Icons.push_pin_outlined, color: Colors.white),
          tooltip: allPinned ? 'Unpin' : 'Pin',
          onPressed: count == 0 ? null : _togglePinSelected,
        ),
        IconButton(
          icon: const Icon(Icons.delete_outline_rounded, color: Colors.white),
          tooltip: 'Delete',
          onPressed: count == 0 ? null : _confirmDeleteSelected,
        ),
        const SizedBox(width: 4),
      ],
    );
  }

  Widget _buildSearchField() {
    return Container(
      color: _kNavy,
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
      child: Container(
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(24),
          boxShadow: [
            BoxShadow(color: Colors.black.withOpacity(0.12), blurRadius: 8, offset: const Offset(0, 3)),
          ],
        ),
        child: TextField(
          controller: _searchController,
          focusNode: _searchFocusNode,
          onChanged: _onSearchChanged,
          decoration: InputDecoration(
            hintText: "Search users to chat",
            hintStyle: TextStyle(color: Colors.grey[500], fontSize: 14),
            prefixIcon: Icon(Icons.search_rounded, color: Colors.grey[500]),
            suffixIcon: _isSearching
                ? const Padding(
                    padding: EdgeInsets.all(14),
                    child: SizedBox(
                      width: 16, height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2, color: _kNavy),
                    ),
                  )
                : (_searchController.text.isNotEmpty
                    ? IconButton(
                        icon: Icon(Icons.close_rounded, color: Colors.grey[500]),
                        onPressed: () {
                          _searchController.clear();
                          setState(() => _searchResults = []);
                        },
                      )
                    : null),
            filled: true,
            fillColor: Colors.transparent,
            contentPadding: const EdgeInsets.symmetric(vertical: 12, horizontal: 4),
            border: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: BorderSide.none),
          ),
        ),
      ),
    );
  }

  Widget _buildSearchResults() {
    if (_searchError != null) {
      return Center(
        child: Text("Search fail: $_searchError", style: const TextStyle(color: Colors.red)),
      );
    }
    if (_searchResults.isEmpty && !_isSearching) {
      return _emptyState(icon: Icons.person_search_rounded, text: "Koi user nahi mila");
    }
    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: 4),
      itemCount: _searchResults.length,
      itemBuilder: (context, index) {
        final user = _searchResults[index];
        return ListTile(
          onTap: _isOpeningChat ? null : () => _openUserChat(user),
          tileColor: Colors.white,
          contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
          leading: _avatar(user['avatar']?.toString(), radius: 24, isGroup: false),
          title: Text(_displayName(user), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
          subtitle: Text('@${user['username'] ?? ''}', style: TextStyle(fontSize: 12, color: Colors.grey[600])),
          trailing: _isOpeningChat
              ? const SizedBox(
                  width: 18, height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2, color: _kNavy),
                )
              : Icon(Icons.chevron_right_rounded, color: Colors.grey[400]),
        );
      },
    );
  }

  Widget _buildBody() {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator(color: _kNavy));
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 100),
        Icon(Icons.wifi_off_rounded, size: 52, color: Colors.grey[400]),
        const SizedBox(height: 12),
        Center(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Text("Failed to load: $_error",
                textAlign: TextAlign.center, style: TextStyle(color: Colors.grey[600])),
          ),
        ),
        const SizedBox(height: 8),
        Center(
          child: TextButton(
            onPressed: () => _loadConversations(),
            child: const Text("Retry", style: TextStyle(color: _kNavy, fontWeight: FontWeight.bold)),
          ),
        ),
      ]);
    }
    if (_conversations.isEmpty) {
      return ListView(children: [
        const SizedBox(height: 130),
        _emptyState(icon: Icons.chat_bubble_outline_rounded, text: "No conversations yet"),
        const SizedBox(height: 6),
        Center(
          child: Text("Tap search above to start chatting",
              style: TextStyle(color: Colors.grey[400], fontSize: 12)),
        ),
      ]);
    }
    final sorted = _sortedConversations();
    return ListView.separated(
      padding: const EdgeInsets.only(top: 4),
      itemCount: sorted.length,
      separatorBuilder: (_, __) => Divider(height: 1, indent: 84, color: Colors.grey[200]),
      itemBuilder: (context, index) {
        final convo = sorted[index];
        return _ConversationTile(
          conversation: convo,
          displayTitle: _displayLabel(convo),
          isPinned: _isPinned(convo),
          isSelectMode: _isSelectMode,
          isSelected: _selectedIds.contains(convo.id),
          onTap: () => _openChat(convo),
          onLongPress: () => _enterSelectMode(convo.id),
        );
      },
    );
  }

  Widget _emptyState({required IconData icon, required String text}) {
    return Column(children: [
      Container(
        width: 88, height: 88,
        decoration: BoxDecoration(color: _kNavy.withOpacity(0.06), shape: BoxShape.circle),
        child: Icon(icon, size: 40, color: _kNavy.withOpacity(0.5)),
      ),
      const SizedBox(height: 14),
      Text(text, style: TextStyle(color: Colors.grey[600], fontSize: 14, fontWeight: FontWeight.w500)),
    ]);
  }
}

Widget _avatar(String? photo, {required double radius, required bool isGroup}) {
  final hasPhoto = photo != null && photo.isNotEmpty;
  return Container(
    padding: const EdgeInsets.all(2),
    decoration: BoxDecoration(
      shape: BoxShape.circle,
      border: Border.all(color: Colors.grey[200]!, width: 1),
    ),
    child: CircleAvatar(
      radius: radius,
      backgroundColor: Colors.grey[200],
      backgroundImage: hasPhoto ? CachedNetworkImageProvider(photo) : null,
      child: !hasPhoto
          ? Icon(isGroup ? Icons.group_rounded : Icons.person_rounded, color: Colors.grey[500])
          : null,
    ),
  );
}

class _ConversationTile extends StatelessWidget {
  final ConversationModel conversation;
  final String displayTitle;
  final bool isPinned;
  final bool isSelectMode;
  final bool isSelected;
  final VoidCallback onTap;
  final VoidCallback onLongPress;
  const _ConversationTile({
    required this.conversation,
    required this.displayTitle,
    required this.isPinned,
    required this.isSelectMode,
    required this.isSelected,
    required this.onTap,
    required this.onLongPress,
  });

  @override
  Widget build(BuildContext context) {
    final hasUnread = conversation.unreadCount > 0;
    return InkWell(
      onTap: onTap,
      onLongPress: onLongPress,
      child: Container(
        color: isSelected ? _kAccent.withOpacity(0.08) : Colors.white,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            if (isSelectMode)
              Padding(
                padding: const EdgeInsets.only(right: 10),
                child: Icon(
                  isSelected ? Icons.check_circle_rounded : Icons.circle_outlined,
                  color: isSelected ? _kAccent : Colors.grey[400],
                  size: 24,
                ),
              )
            else
              _avatar(conversation.displayPhoto, radius: 27, isGroup: conversation.isGroup),
            if (isSelectMode) const SizedBox(width: 2),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      if (isPinned) ...[
                        Icon(Icons.push_pin, size: 13, color: Colors.grey[500]),
                        const SizedBox(width: 4),
                      ],
                      Expanded(
                        child: Text(
                          displayTitle,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontWeight: hasUnread ? FontWeight.bold : FontWeight.w600,
                            fontSize: 15.5,
                            color: Colors.black87,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 3),
                  Text(
                    _lastMessagePreview(),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      color: hasUnread ? Colors.black87 : Colors.grey[600],
                      fontWeight: hasUnread ? FontWeight.w600 : FontWeight.normal,
                      fontSize: 13.5,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              mainAxisSize: MainAxisSize.min,
              children: [
                if (conversation.lastMessageAt != null)
                  Text(
                    timeago.format(conversation.lastMessageAt!, locale: 'en_short'),
                    style: TextStyle(
                      fontSize: 11.5,
                      color: hasUnread ? _kAccent : Colors.grey[500],
                      fontWeight: hasUnread ? FontWeight.w600 : FontWeight.normal,
                    ),
                  ),
                const SizedBox(height: 8),
                if (hasUnread)
                  Container(
                    constraints: const BoxConstraints(minWidth: 20),
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(color: _kAccent, borderRadius: BorderRadius.circular(12)),
                    child: Text(
                      conversation.unreadCount > 99 ? '99+' : conversation.unreadCount.toString(),
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold),
                    ),
                  )
                else
                  const SizedBox(height: 20),
              ],
            ),
          ],
        ),
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