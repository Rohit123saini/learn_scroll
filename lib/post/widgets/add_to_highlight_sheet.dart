// lib/post/widgets/add_to_highlight_sheet.dart
//
// P2-FE — bottom sheet behind the story viewer's "Highlight" button (own
// story only). Lists my highlights (tap = add this story) + "New highlight".
// Pops a short result message (String) for the viewer to show as a snackbar,
// or null if dismissed.
//
// Dark styling on purpose — same as the viewer's other sheets.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../models/highlight_model.dart';
import '../services/highlight_service.dart';

const Color _kAccent = Color(0xFF8B7CFF);

class AddToHighlightSheet extends StatefulWidget {
  final String storyId;
  const AddToHighlightSheet({super.key, required this.storyId});

  @override
  State<AddToHighlightSheet> createState() => _AddToHighlightSheetState();
}

class _AddToHighlightSheetState extends State<AddToHighlightSheet> {
  late Future<List<Highlight>> _future = HighlightService.getHighlights();
  bool _busy = false;

  Future<void> _addTo(Highlight h) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final added = await HighlightService.addStory(h.id, widget.storyId);
      if (mounted) Navigator.pop(context, added ? 'Added to "${h.title}"' : 'Already in "${h.title}"');
    } catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))));
      }
    }
  }

  Future<void> _createNew() async {
    if (_busy) return;
    final ctrl = TextEditingController(text: 'Highlights');
    final title = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: const Color(0xFF1C1C1E),
        title: const Text('New highlight', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w700)),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          maxLength: 30,
          style: const TextStyle(color: Colors.white),
          cursorColor: _kAccent,
          decoration: const InputDecoration(hintText: 'Title', hintStyle: TextStyle(color: Colors.white38)),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(c, ctrl.text.trim()), child: const Text('Create')),
        ],
      ),
    );
    if (title == null || !mounted) return;
    setState(() => _busy = true);
    try {
      final h = await HighlightService.createHighlight(title: title, storyIds: [widget.storyId]);
      if (mounted) Navigator.pop(context, 'Added to "${h.title}"');
    } catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SizedBox(
        height: MediaQuery.of(context).size.height * 0.5,
        child: Column(children: [
          const SizedBox(height: 10),
          Container(width: 36, height: 4, decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2))),
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 14, 16, 6),
            child: Row(children: [
              Icon(Icons.auto_awesome_rounded, color: _kAccent, size: 18),
              SizedBox(width: 8),
              Text('Add to highlight', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 15)),
            ]),
          ),
          const Divider(color: Colors.white12, height: 1),
          ListTile(
            leading: const CircleAvatar(backgroundColor: Colors.white12, child: Icon(Icons.add_rounded, color: Colors.white)),
            title: const Text('New highlight', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
            onTap: _busy ? null : _createNew,
          ),
          Expanded(
            child: FutureBuilder<List<Highlight>>(
              future: _future,
              builder: (context, snap) {
                if (snap.connectionState != ConnectionState.done) {
                  return const Center(child: CircularProgressIndicator(color: _kAccent));
                }
                if (snap.hasError) {
                  return Center(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                      const Text("Couldn't load your highlights.", style: TextStyle(color: Colors.white54)),
                      TextButton(onPressed: () => setState(() => _future = HighlightService.getHighlights()), child: const Text('Retry')),
                    ]),
                  );
                }
                final items = snap.data ?? [];
                return ListView.builder(
                  itemCount: items.length,
                  itemBuilder: (_, i) {
                    final h = items[i];
                    return ListTile(
                      enabled: !_busy,
                      leading: CircleAvatar(
                        backgroundColor: Colors.white12,
                        backgroundImage: h.coverUrl != null ? CachedNetworkImageProvider(h.coverUrl!) : null,
                        child: h.coverUrl == null ? const Icon(Icons.auto_awesome_rounded, color: Colors.white54, size: 18) : null,
                      ),
                      title: Text(h.title, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
                      subtitle: Text('${h.itemsCount} stories', style: const TextStyle(color: Colors.white54, fontSize: 12)),
                      onTap: () => _addTo(h),
                    );
                  },
                );
              },
            ),
          ),
        ]),
      ),
    );
  }
}
