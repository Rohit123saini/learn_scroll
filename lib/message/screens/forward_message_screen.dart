// message/screens/forward_message_screen.dart
//
// Pick one or more conversations to forward a set of messages into.
// Pushed from ChatScreen with a list of message ids (one id for a
// single-message forward, several for multi-select forward). Pops with
// `true` on success so the caller can show a confirmation snackbar.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../models/message_models.dart';
import '../services/message_api_service.dart';

class ForwardMessageScreen extends StatefulWidget {
  final List<String> messageIds;
  const ForwardMessageScreen({super.key, required this.messageIds});

  @override
  State<ForwardMessageScreen> createState() => _ForwardMessageScreenState();
}

class _ForwardMessageScreenState extends State<ForwardMessageScreen> {
  List<ConversationModel> _conversations = [];
  bool _isLoading = true;
  String? _error;
  String _search = '';
  final Set<String> _selectedConversationIds = {};
  bool _isSending = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
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
        _error = "Couldn't load chats: $e";
        _isLoading = false;
      });
    }
  }

  List<ConversationModel> get _filtered {
    if (_search.trim().isEmpty) return _conversations;
    final q = _search.trim().toLowerCase();
    return _conversations
        .where((c) => c.displayTitle.toLowerCase().contains(q))
        .toList();
  }

  void _toggle(String conversationId) {
    setState(() {
      if (_selectedConversationIds.contains(conversationId)) {
        _selectedConversationIds.remove(conversationId);
      } else {
        _selectedConversationIds.add(conversationId);
      }
    });
  }

  Future<void> _send() async {
    if (_selectedConversationIds.isEmpty || _isSending) return;
    setState(() => _isSending = true);
    try {
      await MessageApiService.forwardMessages(
        messageIds: widget.messageIds,
        conversationIds: _selectedConversationIds.toList(),
      );
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        setState(() => _isSending = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Forward failed: $e")),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final count = widget.messageIds.length;
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: const Color(0xFF030F27),
        iconTheme: const IconThemeData(color: Colors.white),
        title: Text(
          count == 1 ? "Forward message" : "Forward $count messages",
          style: const TextStyle(color: Colors.white, fontSize: 16.5),
        ),
      ),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 6),
          child: TextField(
            onChanged: (v) => setState(() => _search = v),
            decoration: InputDecoration(
              hintText: "Search chats",
              prefixIcon: const Icon(Icons.search, size: 20),
              filled: true,
              fillColor: const Color(0xFFF3F5FA),
              contentPadding: const EdgeInsets.symmetric(vertical: 0, horizontal: 12),
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide.none),
            ),
          ),
        ),
        Expanded(child: _buildList()),
      ]),
      bottomNavigationBar: _selectedConversationIds.isEmpty
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: SizedBox(
                  width: double.infinity,
                  height: 46,
                  child: ElevatedButton.icon(
                    onPressed: _isSending ? null : _send,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF030F27),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                    ),
                    icon: _isSending
                        ? const SizedBox(
                            width: 18, height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                          )
                        : const Icon(Icons.send, size: 18, color: Colors.white),
                    label: Text(
                      _isSending
                          ? "Sending..."
                          : "Send to ${_selectedConversationIds.length} ${_selectedConversationIds.length == 1 ? 'chat' : 'chats'}",
                      style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600),
                    ),
                  ),
                ),
              ),
            ),
    );
  }

  Widget _buildList() {
    if (_isLoading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: Colors.black54)),
            const SizedBox(height: 10),
            TextButton(onPressed: _load, child: const Text("Retry")),
          ]),
        ),
      );
    }
    final list = _filtered;
    if (list.isEmpty) {
      return const Center(child: Text("No chats found", style: TextStyle(color: Colors.black45)));
    }
    return ListView.builder(
      itemCount: list.length,
      itemBuilder: (context, index) {
        final c = list[index];
        final selected = _selectedConversationIds.contains(c.id);
        return ListTile(
          onTap: () => _toggle(c.id),
          leading: Stack(children: [
            CircleAvatar(
              radius: 21,
              backgroundColor: Colors.grey[300],
              backgroundImage: c.displayPhoto != null && c.displayPhoto!.isNotEmpty
                  ? CachedNetworkImageProvider(c.displayPhoto!)
                  : null,
              child: c.displayPhoto == null || c.displayPhoto!.isEmpty
                  ? Icon(c.isGroup ? Icons.group : Icons.person, color: Colors.grey[600])
                  : null,
            ),
          ]),
          title: Text(c.displayTitle, maxLines: 1, overflow: TextOverflow.ellipsis),
          trailing: Icon(
            selected ? Icons.check_circle : Icons.radio_button_unchecked,
            color: selected ? const Color(0xFF3D7EFF) : Colors.grey[400],
          ),
        );
      },
    );
  }
}