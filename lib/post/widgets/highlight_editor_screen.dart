// lib/post/widgets/highlight_editor_screen.dart
//
// P2-FE — create / edit a highlight.
//   * title (max 30 chars — backend `clean_title`)
//   * stories picked from the archive (GET /post/stories/archive/, paginated);
//     the order they are picked in is the order they play in
//   * cover: tap a PHOTO in the "Selected" strip (videos can't be a cover);
//     "Automatic" = first photo
//
// Pops `true` when something changed (created / saved / deleted) so the
// caller can reload the Highlights row; `null` otherwise.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../../widgets/ls_ui.dart';
import '../models/highlight_model.dart';
import '../models/story_model.dart' show StoryModel;
import '../services/highlight_service.dart';

const int _kTitleMax = 30; // post/highlights.py::MAX_TITLE_LENGTH

class HighlightEditorScreen extends StatefulWidget {
  /// null => create. For edit pass the DETAIL (`HighlightService.getHighlight`) so `stories` is filled.
  final Highlight? existing;
  const HighlightEditorScreen({super.key, this.existing});

  @override
  State<HighlightEditorScreen> createState() => _HighlightEditorScreenState();
}

class _HighlightEditorScreenState extends State<HighlightEditorScreen> {
  final _titleCtrl = TextEditingController();
  final _scroll = ScrollController();

  // Insertion-ordered (Dart map literals are LinkedHashMap) = play order.
  final Map<String, StoryModel> _selected = {};
  String? _coverId;
  bool _coverTouched = false;

  final List<StoryModel> _archive = [];
  int _page = 1;
  bool _hasMore = true;
  bool _loadingArchive = false;
  bool _archiveFailed = false;

  bool _saving = false;

  bool get _isEdit => widget.existing != null;

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    if (e != null) {
      _titleCtrl.text = e.title;
      for (final s in e.stories) {
        _selected[s.id] = s;
      }
      _coverId = e.coverStoryId;
    }
    _titleCtrl.addListener(() => setState(() {}));
    _scroll.addListener(() {
      if (_scroll.hasClients && _scroll.position.pixels >= _scroll.position.maxScrollExtent - 300) _loadArchive();
    });
    _loadArchive();
  }

  @override
  void dispose() {
    _titleCtrl.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _loadArchive() async {
    if (_loadingArchive || !_hasMore) return;
    setState(() {
      _loadingArchive = true;
      _archiveFailed = false;
    });
    try {
      final page = await HighlightService.getArchive(page: _page);
      if (!mounted) return;
      final seen = _archive.map((s) => s.id).toSet();
      setState(() {
        _archive.addAll(page.stories.where((s) => !seen.contains(s.id)));
        _hasMore = page.hasNext;
        if (page.hasNext) _page++;
        _loadingArchive = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _loadingArchive = false;
          _archiveFailed = true;
        });
      }
    }
  }

  void _toggle(StoryModel s) {
    setState(() {
      if (_selected.containsKey(s.id)) {
        _selected.remove(s.id);
        if (_coverId == s.id) _coverId = null; // server falls back to automatic too
      } else {
        _selected[s.id] = s;
      }
    });
  }

  StoryModel? get _effectiveCover {
    final c = _coverId == null ? null : _selected[_coverId];
    if (c != null && c.mediaType == 'image') return c;
    for (final s in _selected.values) {
      if (s.mediaType == 'image') return s;
    }
    return null;
  }

  bool get _canSave =>
      !_saving && _selected.isNotEmpty && (!_isEdit || _titleCtrl.text.trim().isNotEmpty);

  void _snack(String m) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));

  Future<void> _save() async {
    if (!_canSave) return;
    setState(() => _saving = true);
    try {
      final ids = _selected.keys.toList();
      final title = _titleCtrl.text.trim();
      if (_isEdit) {
        await HighlightService.updateHighlight(
          widget.existing!.id,
          title: title,
          storyIds: ids,
          updateCover: _coverTouched,
          coverStoryId: _coverId,
        );
      } else {
        await HighlightService.createHighlight(
          title: title,
          storyIds: ids,
          coverStoryId: _coverTouched ? _coverId : null,
        );
      }
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        setState(() => _saving = false);
        _snack(e.toString().replaceFirst('Exception: ', ''));
      }
    }
  }

  Future<void> _delete() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Delete highlight?'),
        content: const Text('The stories themselves are not deleted.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(c, true),
            child: Text('Delete', style: TextStyle(color: Theme.of(c).colorScheme.error)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _saving = true);
    try {
      await HighlightService.deleteHighlight(widget.existing!.id);
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        setState(() => _saving = false);
        _snack(e.toString().replaceFirst('Exception: ', ''));
      }
    }
  }

  // ───────────────────────── UI ─────────────────────────

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: AppBar(
        backgroundColor: lsBg(context),
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        title: Text(_isEdit ? 'Edit highlight' : 'New highlight'),
        actions: [
          if (_saving)
            const Padding(
              padding: EdgeInsets.only(right: 16),
              child: Center(child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))),
            )
          else
            TextButton(onPressed: _canSave ? _save : null, child: Text(_isEdit ? 'Save' : 'Create')),
        ],
      ),
      body: CustomScrollView(
        controller: _scroll,
        slivers: [
          SliverToBoxAdapter(child: _header(cs)),
          if (_selected.isNotEmpty) SliverToBoxAdapter(child: _selectedStrip(cs)),
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(kLsPad, 14, kLsPad, 8),
              child: Text('Your stories', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: cs.onSurface)),
            ),
          ),
          ..._archiveSlivers(cs),
          if (_isEdit)
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(kLsPad, 24, kLsPad, 0),
                child: TextButton.icon(
                  onPressed: _saving ? null : _delete,
                  icon: Icon(Icons.delete_outline_rounded, color: cs.error),
                  label: Text('Delete highlight', style: TextStyle(color: cs.error)),
                ),
              ),
            ),
          const SliverToBoxAdapter(child: SizedBox(height: 40)),
        ],
      ),
    );
  }

  Widget _header(ColorScheme cs) {
    final cover = _effectiveCover;
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 8, kLsPad, 0),
      child: Row(children: [
        Container(
          width: 72,
          height: 72,
          padding: const EdgeInsets.all(2),
          decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: cs.outlineVariant, width: 1.5)),
          child: ClipOval(
            child: cover?.mediaUrl == null || cover!.mediaUrl!.isEmpty
                ? Container(color: cs.surfaceVariant, child: Icon(Icons.auto_awesome_rounded, color: cs.onSurfaceVariant))
                : CachedNetworkImage(imageUrl: cover.mediaUrl!, fit: BoxFit.cover),
          ),
        ),
        const SizedBox(width: 16),
        Expanded(
          child: TextField(
            controller: _titleCtrl,
            maxLength: _kTitleMax,
            textCapitalization: TextCapitalization.sentences,
            decoration: const InputDecoration(labelText: 'Title', hintText: 'Highlights'),
          ),
        ),
      ]),
    );
  }

  Widget _selectedStrip(ColorScheme cs) {
    final hasPhoto = _selected.values.any((s) => s.mediaType == 'image');
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(kLsPad, 12, kLsPad, 6),
        child: Row(children: [
          Expanded(
            child: Text('Selected (${_selected.length}) · tap a photo to use it as cover',
                style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
          ),
          if (hasPhoto)
            ChoiceChip(
              label: const Text('Automatic'),
              selected: _coverId == null || _selected[_coverId] == null,
              visualDensity: VisualDensity.compact,
              onSelected: (_) => setState(() {
                _coverId = null;
                _coverTouched = true;
              }),
            ),
        ]),
      ),
      SizedBox(
        height: 84,
        child: ListView.separated(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: kLsPad),
          itemCount: _selected.length,
          separatorBuilder: (_, __) => const SizedBox(width: 8),
          itemBuilder: (_, i) {
            final s = _selected.values.elementAt(i);
            final isCover = _coverId == s.id && s.mediaType == 'image';
            return GestureDetector(
              onTap: s.mediaType == 'image'
                  ? () => setState(() {
                        _coverId = s.id;
                        _coverTouched = true;
                      })
                  : null,
              child: Stack(children: [
                Container(
                  width: 64,
                  height: 84,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: isCover ? cs.primary : Colors.transparent, width: 2),
                  ),
                  child: ClipRRect(borderRadius: BorderRadius.circular(8), child: _thumb(s, cs)),
                ),
                Positioned(
                  top: 2,
                  right: 2,
                  child: GestureDetector(
                    onTap: () => _toggle(s),
                    child: Container(
                      decoration: const BoxDecoration(color: Colors.black54, shape: BoxShape.circle),
                      padding: const EdgeInsets.all(3),
                      child: const Icon(Icons.close_rounded, size: 12, color: Colors.white),
                    ),
                  ),
                ),
                if (isCover)
                  Positioned(
                    bottom: 4,
                    left: 4,
                    child: Icon(Icons.star_rounded, size: 16, color: cs.primary),
                  ),
              ]),
            );
          },
        ),
      ),
    ]);
  }

  List<Widget> _archiveSlivers(ColorScheme cs) {
    if (_archive.isEmpty) {
      if (_loadingArchive) {
        return const [SliverToBoxAdapter(child: Padding(padding: EdgeInsets.all(32), child: Center(child: CircularProgressIndicator())))];
      }
      return [
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Column(children: [
              Icon(_archiveFailed ? Icons.wifi_off_rounded : Icons.history_rounded, color: cs.onSurfaceVariant, size: 30),
              const SizedBox(height: 10),
              Text(
                _archiveFailed ? "Couldn't load your stories." : 'No stories to add yet. Post a story and it shows up here.',
                textAlign: TextAlign.center,
                style: TextStyle(color: cs.onSurfaceVariant),
              ),
              if (_archiveFailed) TextButton(onPressed: _loadArchive, child: const Text('Retry')),
            ]),
          ),
        ),
      ];
    }
    return [
      SliverPadding(
        padding: const EdgeInsets.symmetric(horizontal: kLsPad),
        sliver: SliverGrid(
          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 3,
            mainAxisSpacing: 6,
            crossAxisSpacing: 6,
            childAspectRatio: 9 / 16,
          ),
          delegate: SliverChildBuilderDelegate(
            (context, i) {
              final s = _archive[i];
              final order = _selected.keys.toList().indexOf(s.id);
              return GestureDetector(
                onTap: () => _toggle(s),
                child: Stack(fit: StackFit.expand, children: [
                  ClipRRect(borderRadius: BorderRadius.circular(8), child: _thumb(s, cs)),
                  if (order >= 0) ...[
                    Container(decoration: BoxDecoration(borderRadius: BorderRadius.circular(8), color: cs.primary.withOpacity(0.22))),
                    Positioned(
                      top: 6,
                      right: 6,
                      child: CircleAvatar(
                        radius: 11,
                        backgroundColor: cs.primary,
                        child: Text('${order + 1}', style: TextStyle(fontSize: 11, color: cs.onPrimary, fontWeight: FontWeight.w700)),
                      ),
                    ),
                  ],
                ]),
              );
            },
            childCount: _archive.length,
          ),
        ),
      ),
      if (_loadingArchive)
        const SliverToBoxAdapter(child: Padding(padding: EdgeInsets.all(16), child: Center(child: CircularProgressIndicator(strokeWidth: 2)))),
    ];
  }

  Widget _thumb(StoryModel s, ColorScheme cs) {
    final url = s.mediaUrl ?? '';
    if (s.mediaType == 'video' || url.isEmpty) {
      return Container(
        color: Colors.black87,
        alignment: Alignment.center,
        child: const Icon(Icons.play_circle_outline_rounded, color: Colors.white70, size: 28),
      );
    }
    return CachedNetworkImage(
      imageUrl: url,
      fit: BoxFit.cover,
      placeholder: (_, __) => Container(color: cs.surfaceVariant),
      errorWidget: (_, __, ___) => Container(color: cs.surfaceVariant, child: Icon(Icons.broken_image_rounded, color: cs.onSurfaceVariant)),
    );
  }
}
