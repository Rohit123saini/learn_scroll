// message/screens/conversations_list_screen.dart
//
// 🎨 REDESIGN — WhatsApp-style multi-select delete (long-press se select
// mode, appbar me trash icon se bulk delete) — logic same hai, bas visuals
// professional bana diye + wahi bottom nav jo home me hai.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../models/message_models.dart';
import '../services/message_api_service.dart';
import 'chat_screen.dart';
import 'app_bottom_nav.dart'; // 🔥 NAYA

const _kNavy = Color(0xFF030F27);
const _kAccent = Color(0xFF25D366);
const _kBg = Color(0xFFF6F7FB);

class ConversationsListScreen extends StatefulWidget {
  const ConversationsListScreen({super.key});

  @override
  State<ConversationsListScreen> createState() =>
      _ConversationsListScreenState();
}

class _ConversationsListScreenState extends State<ConversationsListScreen> {
  List<ConversationModel> _conversations = [];
  bool _isLoading = true;
  String? _error;

  bool _selectionMode = false;
  final Set<String> _selectedIds = {};
  bool _isDeleting = false;

  @override
  void initState() {
    super.initState();
    _loadConversations();
  }

  Future<void> _loadConversations() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });
    try {
      final list = await MessageApiService.getConversations();
      if (!mounted) return;
      setState(() {
        _conversations = list;
        _isLoading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _isLoading = false;
      });
    }
  }

  void _enterSelectionMode(String id) {
    setState(() {
      _selectionMode = true;
      _selectedIds.add(id);
    });
  }

  void _toggleSelect(String id) {
    setState(() {
      if (_selectedIds.contains(id)) {
        _selectedIds.remove(id);
        if (_selectedIds.isEmpty) _selectionMode = false;
      } else {
        _selectedIds.add(id);
      }
    });
  }

  void _cancelSelection() {
    setState(() {
      _selectionMode = false;
      _selectedIds.clear();
    });
  }

  Future<void> _deleteSelected() async {
    final count = _selectedIds.length;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text(count == 1 ? "Delete chat?" : "Delete $count chats?",
            style: const TextStyle(fontWeight: FontWeight.bold)),
        content: const Text(
            "Ye chat(s) sirf tumhare liye delete hongi — dusre participant ya group ke members par koi asar nahi padega."),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text("Cancel", style: TextStyle(color: Colors.grey))),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text("Delete", style: TextStyle(color: Colors.red, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    final idsToDelete = _selectedIds.toList();
    setState(() => _isDeleting = true);
    try {
      await MessageApiService.bulkDeleteConversations(idsToDelete);
      if (!mounted) return;
      setState(() {
        _conversations.removeWhere((c) => idsToDelete.contains(c.id));
        _selectedIds.clear();
        _selectionMode = false;
        _isDeleting = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _isDeleting = false);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text("Delete failed: $e")));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      appBar: _selectionMode ? _buildSelectionAppBar() : _buildNormalAppBar(),
      body: _buildBody(),
      bottomNavigationBar: _selectionMode
          ? null
          : const AppBottomNav(current: AppTab.chats), // 🔥 NAYA
    );
  }

  AppBar _buildNormalAppBar() {
    return AppBar(
      backgroundColor: _kNavy,
      elevation: 0,
      iconTheme: const IconThemeData(color: Colors.white),
      title: const Text("Chats",
          style: TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 20)),
    );
  }

  AppBar _buildSelectionAppBar() {
    return AppBar(
      backgroundColor: _kNavy,
      elevation: 0,
      iconTheme: const IconThemeData(color: Colors.white),
      leading: IconButton(
        icon: const Icon(Icons.close_rounded, color: Colors.white),
        onPressed: _cancelSelection,
      ),
      title: Text("${_selectedIds.length} selected",
          style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
      actions: [
        if (_isDeleting)
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 16),
            child: SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(
                  strokeWidth: 2, color: Colors.white),
            ),
          )
        else
          IconButton(
            icon: const Icon(Icons.delete_outline_rounded, color: Colors.white),
            tooltip: "Delete selected",
            onPressed: _selectedIds.isEmpty ? null : _deleteSelected,
          ),
      ],
    );
  }

  Widget _buildBody() {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator(color: _kNavy));
    }
    if (_error != null) {
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(Icons.wifi_off_rounded, size: 48, color: Colors.grey[400]),
          const SizedBox(height: 10),
          Text("Failed to load chats: $_error", style: TextStyle(color: Colors.grey[600])),
          const SizedBox(height: 8),
          TextButton(
            onPressed: _loadConversations,
            child: const Text("Retry", style: TextStyle(color: _kNavy, fontWeight: FontWeight.bold)),
          ),
        ]),
      );
    }
    if (_conversations.isEmpty) {
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 88, height: 88,
            decoration: BoxDecoration(color: _kNavy.withOpacity(0.06), shape: BoxShape.circle),
            child: Icon(Icons.chat_bubble_outline_rounded, size: 40, color: _kNavy.withOpacity(0.5)),
          ),
          const SizedBox(height: 14),
          Text("No chats yet", style: TextStyle(color: Colors.grey[600], fontSize: 14, fontWeight: FontWeight.w500)),
        ]),
      );
    }

    return RefreshIndicator(
      color: _kNavy,
      onRefresh: _loadConversations,
      child: ListView.separated(
        padding: const EdgeInsets.only(top: 4),
        itemCount: _conversations.length,
        separatorBuilder: (_, __) => Divider(height: 1, indent: 84, color: Colors.grey[200]),
        itemBuilder: (context, index) {
          final conversation = _conversations[index];
          final isSelected = _selectedIds.contains(conversation.id);
          return _ConversationTile(
            conversation: conversation,
            isSelected: isSelected,
            selectionMode: _selectionMode,
            onTap: () {
              if (_selectionMode) {
                _toggleSelect(conversation.id);
              } else {
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => ChatScreen(conversation: conversation),
                  ),
                );
              }
            },
            onLongPress: () {
              if (!_selectionMode) _enterSelectionMode(conversation.id);
            },
          );
        },
      ),
    );
  }
}

class _ConversationTile extends StatelessWidget {
  final ConversationModel conversation;
  final bool isSelected;
  final bool selectionMode;
  final VoidCallback onTap;
  final VoidCallback onLongPress;

  const _ConversationTile({
    required this.conversation,
    required this.isSelected,
    required this.selectionMode,
    required this.onTap,
    required this.onLongPress,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: isSelected ? const Color(0xFFE9F9F1) : Colors.white,
      child: InkWell(
        onTap: onTap,
        onLongPress: onLongPress,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          child: Row(children: [
            AnimatedSwitcher(
              duration: const Duration(milliseconds: 150),
              child: selectionMode
                  ? Padding(
                      key: const ValueKey('checkbox'),
                      padding: const EdgeInsets.only(right: 14),
                      child: Icon(
                        isSelected ? Icons.check_circle_rounded : Icons.radio_button_unchecked,
                        color: isSelected ? _kAccent : Colors.grey[400],
                        size: 26,
                      ),
                    )
                  : Padding(
                      key: const ValueKey('avatar'),
                      padding: const EdgeInsets.only(right: 12),
                      child: Container(
                        padding: const EdgeInsets.all(2),
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          border: Border.all(color: Colors.grey[200]!, width: 1),
                        ),
                        child: CircleAvatar(
                          radius: 27,
                          backgroundColor: Colors.grey[200],
                          backgroundImage: (conversation.displayPhoto != null &&
                                  conversation.displayPhoto!.isNotEmpty)
                              ? CachedNetworkImageProvider(conversation.displayPhoto!)
                              : null,
                          child: (conversation.displayPhoto == null ||
                                  conversation.displayPhoto!.isEmpty)
                              ? Icon(
                                  conversation.isGroup ? Icons.group_rounded : Icons.person_rounded,
                                  color: Colors.grey[500],
                                )
                              : null,
                        ),
                      ),
                    ),
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(conversation.displayTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          fontWeight: FontWeight.w700, fontSize: 15.5, color: Colors.black87)),
                  if (conversation.lastMessageText != null &&
                      conversation.lastMessageText!.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 3),
                      child: Text(
                        conversation.lastMessageText!,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(color: Colors.grey[600], fontSize: 13.5),
                      ),
                    ),
                ],
              ),
            ),
          ]),
        ),
      ),
    );
  }
}