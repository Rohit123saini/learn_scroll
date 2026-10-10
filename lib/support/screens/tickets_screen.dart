// lib/support/screens/tickets_screen.dart
// My support tickets + new-ticket form + ticket thread (chat).
// TODO(l10n): screen copy is English-only for now.

import 'package:flutter/material.dart';

import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../support_service.dart';

const Map<String, String> kTicketCategories = {
  'account': 'Account & login',
  'payment': 'Payments',
  'class': 'Classes',
  'test': 'Tests',
  'safety': 'Safety & abuse',
  'other': 'Something else',
};

String _statusLabel(String s) => switch (s) {
      'answered' => 'Replied',
      'resolved' => 'Resolved',
      'closed' => 'Closed',
      _ => 'Open',
    };

class TicketsScreen extends StatefulWidget {
  const TicketsScreen({super.key});
  @override
  State<TicketsScreen> createState() => _TicketsScreenState();
}

class _TicketsScreenState extends State<TicketsScreen> {
  List<SupportTicket>? _items;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final r = await SupportService.tickets();
      if (mounted) setState(() => _items = r);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
  }

  Future<void> _open(Widget page) async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => page));
    if (mounted) _load();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    Widget body;
    if (_error != null && _items == null) {
      body = ErrorStateWidget(title: _error!, retryLabel: 'Retry', onRetry: _load);
    } else if (_items == null) {
      body = const Center(child: CircularProgressIndicator());
    } else if (_items!.isEmpty) {
      body = EmptyStateWidget(
        title: 'No conversations yet',
        subtitle: 'Need help? Start a chat and our team will reply here.',
        icon: Icons.support_agent_rounded,
        actionLabel: 'New message',
        onAction: () => _open(const NewTicketScreen()),
      );
    } else {
      body = RefreshIndicator(
        onRefresh: _load,
        child: ListView.separated(
          padding: const EdgeInsets.all(kLsPad),
          itemCount: _items!.length,
          separatorBuilder: (_, __) => const SizedBox(height: 8),
          itemBuilder: (_, i) {
            final t = _items![i];
            return LsCard(
              onTap: () => _open(TicketThreadScreen(ticketId: t.id, subject: t.subject)),
              child: Row(children: [
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(t.subject,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(fontWeight: t.hasUnreadReply ? FontWeight.w800 : FontWeight.w600)),
                    const SizedBox(height: 3),
                    Text(t.preview,
                        maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13)),
                  ]),
                ),
                const SizedBox(width: 10),
                Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                  if (t.hasUnreadReply)
                    Semantics(
                      label: 'New reply',
                      child: Container(width: 10, height: 10, decoration: BoxDecoration(color: cs.primary, shape: BoxShape.circle)),
                    ),
                  const SizedBox(height: 4),
                  Text(_statusLabel(t.status), style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                ]),
              ]),
            );
          },
        ),
      );
    }
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'Support chat'),
      body: body,
      floatingActionButton: (_items == null || _items!.isEmpty)
          ? null
          : FloatingActionButton.extended(
              onPressed: () => _open(const NewTicketScreen()),
              icon: const Icon(Icons.edit_rounded),
              label: const Text('New message'),
            ),
    );
  }
}

// -------------------------------------------------------------- new ticket
class NewTicketScreen extends StatefulWidget {
  const NewTicketScreen({super.key});
  @override
  State<NewTicketScreen> createState() => _NewTicketScreenState();
}

class _NewTicketScreenState extends State<NewTicketScreen> {
  final _subject = TextEditingController();
  final _message = TextEditingController();
  String _category = 'other';
  bool _busy = false;

  @override
  void dispose() {
    _subject.dispose();
    _message.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final s = _subject.text.trim(), m = _message.text.trim();
    if (s.isEmpty || m.isEmpty) {
      lsSnack(context, 'Please add a subject and a message.', error: true);
      return;
    }
    setState(() => _busy = true);
    try {
      final t = await SupportService.createTicket(subject: s, category: _category, message: m);
      if (!mounted) return;
      Navigator.pushReplacement(
          context, MaterialPageRoute(builder: (_) => TicketThreadScreen(ticketId: t.id, subject: t.subject)));
    } catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        lsSnack(context, e.toString(), error: true);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: 'New message'),
      body: ListView(padding: const EdgeInsets.all(kLsPad), children: [
        DropdownButtonFormField<String>(
          value: _category,
          decoration: const InputDecoration(labelText: 'Topic', border: OutlineInputBorder()),
          items: [for (final e in kTicketCategories.entries) DropdownMenuItem(value: e.key, child: Text(e.value))],
          onChanged: (v) => setState(() => _category = v ?? 'other'),
        ),
        const SizedBox(height: 14),
        TextField(
          controller: _subject,
          maxLength: 120,
          textCapitalization: TextCapitalization.sentences,
          decoration: const InputDecoration(labelText: 'Subject', border: OutlineInputBorder()),
        ),
        const SizedBox(height: 6),
        TextField(
          controller: _message,
          maxLength: 2000,
          minLines: 5,
          maxLines: 10,
          textCapitalization: TextCapitalization.sentences,
          decoration: const InputDecoration(labelText: 'How can we help?', alignLabelWithHint: true, border: OutlineInputBorder()),
        ),
        const SizedBox(height: 16),
        LsPrimaryButton(label: 'Send', icon: Icons.send_rounded, loading: _busy, onPressed: _busy ? null : _submit),
      ]),
    );
  }
}

// ------------------------------------------------------------------ thread
class TicketThreadScreen extends StatefulWidget {
  final String ticketId;
  final String subject;
  const TicketThreadScreen({super.key, required this.ticketId, required this.subject});
  @override
  State<TicketThreadScreen> createState() => _TicketThreadScreenState();
}

class _TicketThreadScreenState extends State<TicketThreadScreen> {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  SupportTicket? _ticket;
  String? _error;
  bool _sending = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final t = await SupportService.ticket(widget.ticketId);
      if (!mounted) return;
      setState(() {
        _ticket = t;
        _error = null;
      });
      _toBottom();
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
  }

  void _toBottom() => WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
      });

  Future<void> _send() async {
    final body = _input.text.trim();
    if (body.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      await SupportService.reply(widget.ticketId, body);
      _input.clear();
      await _load();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  Future<void> _close() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Close this conversation?'),
        content: const Text('You can still start a new one anytime.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(c, true), child: const Text('Close')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await SupportService.closeTicket(widget.ticketId);
      await _load();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = _ticket;
    Widget body;
    if (t == null) {
      body = _error != null
          ? ErrorStateWidget(title: _error!, retryLabel: 'Retry', onRetry: _load)
          : const Center(child: CircularProgressIndicator());
    } else {
      body = Column(children: [
        Expanded(
          child: ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.all(kLsPad),
            itemCount: t.messages.length,
            itemBuilder: (_, i) {
              final m = t.messages[i];
              final mine = !m.isStaff;
              return Align(
                alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
                child: Semantics(
                  label: '${mine ? 'You' : 'Support'}: ${m.body}',
                  child: Container(
                    margin: const EdgeInsets.symmetric(vertical: 4),
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.78),
                    decoration: BoxDecoration(
                      color: mine ? cs.primary : cs.surfaceContainerHighest,
                      borderRadius: BorderRadius.circular(16),
                    ),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      if (!mine)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 2),
                          child: Text(m.senderLabel.isEmpty ? 'Support' : m.senderLabel,
                              style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: cs.primary)),
                        ),
                      Text(m.body, style: TextStyle(color: mine ? cs.onPrimary : cs.onSurface)),
                    ]),
                  ),
                ),
              );
            },
          ),
        ),
        if (t.isClosed)
          Padding(
            padding: const EdgeInsets.all(14),
            child: Text('This conversation is closed. Start a new message if you still need help.',
                textAlign: TextAlign.center, style: TextStyle(color: cs.onSurfaceVariant, fontSize: 12.5)),
          ),
        if (!t.isClosed)
          SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 6, 8, 8),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _input,
                  minLines: 1,
                  maxLines: 4,
                  maxLength: 2000,
                  textCapitalization: TextCapitalization.sentences,
                  decoration: const InputDecoration(
                      hintText: 'Write a reply', counterText: '', border: OutlineInputBorder(), isDense: true),
                ),
              ),
              IconButton(
                tooltip: 'Send',
                onPressed: _sending ? null : _send,
                icon: _sending
                    ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.send_rounded),
              ),
            ]),
          ),
        ),
      ]);
    }
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: lsAppBar(context, title: widget.subject, actions: [
        if (t != null && !t.isClosed)
          IconButton(tooltip: 'Close conversation', onPressed: _close, icon: const Icon(Icons.check_circle_outline_rounded)),
      ]),
      body: body,
    );
  }
}
