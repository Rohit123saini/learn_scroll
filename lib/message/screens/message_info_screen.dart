// message/screens/message_info_screen.dart
//
// 🔥 NAYA — "Seen by" / message-info screen (WhatsApp-style: long-press a
// message you sent → Info → who's read/received it, and when). Backend
// endpoint (`GET /message/messages/<id>/read-status/`, §4 of backend doc)
// already existed, undocumented and with zero frontend caller — this
// screen + `MessageApiService.getReadStatus` + `MessageReadStatusModel`
// are that missing piece end-to-end.
//
// Read-receipt privacy toggle (`ReadReceiptSettingsView`) is enforced
// entirely server-side — backend just omits/nulls `read_at` when it
// applies. This screen doesn't re-implement that logic, it only renders
// whatever the response actually contains.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../models/message_models.dart';
import '../services/message_api_service.dart';

class MessageInfoScreen extends StatefulWidget {
  final String messageId;
  // Preview line at the top (kept lightweight — just the text/type, not a
  // full bubble re-render) so the user can confirm which message this is.
  final String? messagePreview;

  const MessageInfoScreen({super.key, required this.messageId, this.messagePreview});

  @override
  State<MessageInfoScreen> createState() => _MessageInfoScreenState();
}

class _MessageInfoScreenState extends State<MessageInfoScreen> {
  static const _kNavy = Color(0xFF030F27);
  static const _kAccent = Color(0xFFEE0979);

  bool _loading = true;
  String? _error;
  List<MessageReadStatusModel> _statuses = [];

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
      final statuses = await MessageApiService.getReadStatus(widget.messageId);
      if (!mounted) return;
      setState(() {
        _statuses = statuses;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = "Info load nahi ho paayi: $e";
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    // Read pehle (jinka readAt sabse recent), phir sirf-delivered, phir
    // baaki (abhi tak deliver hi nahi hua — offline/app band).
    final read = _statuses.where((s) => s.isRead).toList()
      ..sort((a, b) => (b.readAt ?? DateTime(0)).compareTo(a.readAt ?? DateTime(0)));
    final deliveredOnly = _statuses.where((s) => !s.isRead && s.isDelivered).toList();
    final pending = _statuses.where((s) => !s.isRead && !s.isDelivered).toList();

    return Scaffold(
      backgroundColor: Colors.grey[100],
      appBar: AppBar(
        backgroundColor: _kNavy,
        foregroundColor: Colors.white,
        elevation: 0,
        title: const Text("Message info", style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: _kNavy))
          : _error != null
              ? Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(_error!, style: const TextStyle(color: Colors.red), textAlign: TextAlign.center)))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    children: [
                      if (widget.messagePreview != null && widget.messagePreview!.trim().isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
                          child: Container(
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(10)),
                            child: Text(widget.messagePreview!, maxLines: 3, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13.5)),
                          ),
                        ),
                      if (read.isNotEmpty) _sectionHeader(Icons.done_all_rounded, "Read by", read.length),
                      ...read.map((s) => _personTile(s, s.readAt)),
                      if (deliveredOnly.isNotEmpty) _sectionHeader(Icons.done_rounded, "Delivered to", deliveredOnly.length),
                      ...deliveredOnly.map((s) => _personTile(s, s.deliveredAt)),
                      if (pending.isNotEmpty) _sectionHeader(Icons.schedule_rounded, "Not yet delivered", pending.length),
                      ...pending.map((s) => _personTile(s, null)),
                      if (_statuses.isEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 60),
                          child: Center(child: Text("Koi status abhi tak nahi hai", style: TextStyle(color: Colors.grey[500], fontSize: 13.5))),
                        ),
                    ],
                  ),
                ),
    );
  }

  Widget _sectionHeader(IconData icon, String title, int count) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
      child: Row(children: [
        Icon(icon, size: 16, color: _kAccent),
        const SizedBox(width: 6),
        Text("$title ($count)", style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 12.5, color: _kNavy)),
      ]),
    );
  }

  Widget _personTile(MessageReadStatusModel s, DateTime? timestamp) {
    final u = s.user;
    return ListTile(
      dense: true,
      leading: CircleAvatar(
        radius: 18,
        backgroundColor: Colors.grey[200],
        backgroundImage: (u.profilePhoto != null && u.profilePhoto!.isNotEmpty) ? CachedNetworkImageProvider(u.profilePhoto!) : null,
        child: (u.profilePhoto == null || u.profilePhoto!.isEmpty) ? Icon(Icons.person_rounded, color: Colors.grey[500], size: 18) : null,
      ),
      title: Text(u.displayName, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
      subtitle: timestamp != null ? Text(timeago.format(timestamp), style: TextStyle(fontSize: 11.5, color: Colors.grey[600])) : null,
    );
  }
}