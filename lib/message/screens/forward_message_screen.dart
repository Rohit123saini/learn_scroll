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
import '../../theme_service.dart'; // 🎨 THEME FIX — AppThemeTokens

class ForwardMessageScreen extends StatefulWidget {
  // 🔧 FIX (Phase 3, §4.3) — pehle sirf `messageIds` liya jaata tha, jisse
  // is screen ko pata hi nahi chalta tha ki selected messages me se koi
  // text-type hai ya poll — caption UI logic ke liye poore `MessageModel`
  // objects chahiye the.
  final List<MessageModel> messages;
  const ForwardMessageScreen({super.key, required this.messages});

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
  // 🔥 NAYA (Phase 3, §4.3/§7.9) — optional caption jo forward ke saath
  // jaata hai. Backend text-wale messages ka apna text overwrite nahi
  // karta — isliye jab tak koi non-text (media/location) message
  // selection me nahi hai, caption field dikhane ka koi matlab nahi
  // (§4.3 UI hint), isliye niche `_captionApplicable` getter se decide
  // hota hai.
  final TextEditingController _captionController = TextEditingController();

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

  // 🔥 NAYA (Phase 3, §4.3) — caption sirf tab dikhana hai jab selection
  // me kam se kam ek non-text message (media/location/etc.) ho. Sab
  // messages text-type hain to caption field hide/disable kar do —
  // backend text wale message ka apna text overwrite nahi karta, UI me
  // confusion na ho isliye.
  bool get _captionApplicable =>
      widget.messages.any((m) => m.type != MessageType.text);

  @override
  void dispose() {
    _captionController.dispose();
    super.dispose();
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
      // 🔧 FIX (Phase 3, §4.3) — poll messages backend silently drop
      // karta hai; defensively yahan bhi exclude kar diya, chahe caller
      // (chat_screen.dart) already selection se rok chuka ho.
      final ids = widget.messages
          .where((m) => m.type != MessageType.poll)
          .map((m) => m.id)
          .toList();
      await MessageApiService.forwardMessages(
        messageIds: ids,
        conversationIds: _selectedConversationIds.toList(),
        caption: _captionApplicable ? _captionController.text : null,
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
    final count = widget.messages.length;
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: cs.primary,
        iconTheme: IconThemeData(color: cs.onPrimary),
        title: Text(
          count == 1 ? "Forward message" : "Forward $count messages",
          style: TextStyle(color: cs.onPrimary, fontSize: 16.5),
        ),
      ),
      body: Column(children: [
        // 🔥 NAYA (Phase 3, §4.3) — caption input, sirf tab dikhta hai jab
        // selection me koi non-text message ho.
        if (_captionApplicable)
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 0),
            child: TextField(
              controller: _captionController,
              maxLines: 3,
              minLines: 1,
              decoration: const InputDecoration(
                hintText: "Add a caption (optional)",
              ),
            ),
          ),
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 6),
          child: TextField(
            onChanged: (v) => setState(() => _search = v),
            decoration: const InputDecoration(
              hintText: "Search chats",
              prefixIcon: Icon(Icons.search, size: 20),
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
                      backgroundColor: cs.primary,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                    ),
                    icon: _isSending
                        ? SizedBox(
                            width: 18, height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2, color: cs.onPrimary),
                          )
                        : Icon(Icons.send, size: 18, color: cs.onPrimary),
                    label: Text(
                      _isSending
                          ? "Sending..."
                          : "Send to ${_selectedConversationIds.length} ${_selectedConversationIds.length == 1 ? 'chat' : 'chats'}",
                      style: TextStyle(color: cs.onPrimary, fontWeight: FontWeight.w600),
                    ),
                  ),
                ),
              ),
            ),
    );
  }

  Widget _buildList() {
    final cs = Theme.of(context).colorScheme;
    if (_isLoading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(_error!, textAlign: TextAlign.center, style: TextStyle(color: cs.onSurfaceVariant)),
            const SizedBox(height: 10),
            TextButton(onPressed: _load, child: const Text("Retry")),
          ]),
        ),
      );
    }
    final list = _filtered;
    if (list.isEmpty) {
      return Center(child: Text("No chats found", style: TextStyle(color: cs.onSurfaceVariant)));
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
              backgroundColor: AppThemeTokens.of(context).surface2,
              backgroundImage: c.displayPhoto != null && c.displayPhoto!.isNotEmpty
                  ? CachedNetworkImageProvider(c.displayPhoto!)
                  : null,
              child: c.displayPhoto == null || c.displayPhoto!.isEmpty
                  ? Icon(c.isGroup ? Icons.group : Icons.person, color: cs.onSurfaceVariant)
                  : null,
            ),
          ]),
          title: Text(c.displayTitle, maxLines: 1, overflow: TextOverflow.ellipsis),
          trailing: Icon(
            selected ? Icons.check_circle : Icons.radio_button_unchecked,
            color: selected ? cs.primary : cs.onSurfaceVariant,
          ),
        );
      },
    );
  }
}