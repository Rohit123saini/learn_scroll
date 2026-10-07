// lib/post/widgets/highlight_editor_screen.dart
//
// P2-FE — create / edit a highlight.
//   * title (max 30 chars — backend `clean_title`)
//   * stories picked from the archive (GET /post/stories/archive/, paginated);
//     the order they are picked in is the order they play in; the "Selected"
//     strip can be re-ordered (long-press + drag)
//   * cover: tap a PHOTO in the "Selected" strip (videos can't be a cover);
//     "Automatic" = first photo. "Adjust cover" = pan/zoom crop of the round
//     cover (stored as focus x/y + zoom, the photo is not modified)
//   * rename = the title field; delete = button at the bottom
//
// Pops `true` when something changed (created / saved / deleted) so the
// caller can reload the Highlights row; `null` otherwise.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../../l10n/app_localizations.dart';
import '../../widgets/ls_ui.dart';
import '../models/highlight_model.dart';
import '../models/story_model.dart' show StoryModel;
import '../services/highlight_service.dart';
import 'highlights_row.dart' show HighlightCover;

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
  double _cropX = 0.0, _cropY = 0.0, _cropZoom = 1.0;

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
      _cropX = e.coverX;
      _cropY = e.coverY;
      _cropZoom = e.coverZoom;
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
        if (_coverId == s.id) {
          _coverId = null; // server falls back to automatic too
          _resetCrop();
        }
      } else {
        _selected[s.id] = s;
      }
    });
  }

  void _resetCrop() {
    _cropX = 0.0;
    _cropY = 0.0;
    _cropZoom = 1.0;
  }

  void _setCover(String? id) {
    setState(() {
      if (_coverId != id) _resetCrop(); // a new cover starts uncropped
      _coverId = id;
      _coverTouched = true;
    });
  }

  void _reorder(int oldIndex, int newIndex) {
    if (newIndex > oldIndex) newIndex -= 1;
    final entries = _selected.entries.toList();
    final moved = entries.removeAt(oldIndex);
    entries.insert(newIndex, moved);
    setState(() {
      _selected
        ..clear()
        ..addEntries(entries);
    });
  }

  Future<void> _adjustCover() async {
    final cover = _effectiveCover;
    final url = cover?.mediaUrl;
    if (cover == null || url == null || url.isEmpty) return;
    final result = await showDialog<HighlightCrop>(
      context: context,
      builder: (_) => _CoverCropDialog(
        url: url,
        initial: _coverId == cover.id ? HighlightCrop(x: _cropX, y: _cropY, zoom: _cropZoom) : HighlightCrop.none,
      ),
    );
    if (result == null || !mounted) return;
    setState(() {
      _coverId = cover.id; // cropping an automatic cover makes it explicit
      _coverTouched = true;
      _cropX = result.x;
      _cropY = result.y;
      _cropZoom = result.zoom;
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
          crop: HighlightCrop(x: _cropX, y: _cropY, zoom: _cropZoom),
        );
      } else {
        await HighlightService.createHighlight(
          title: title,
          storyIds: ids,
          coverStoryId: _coverTouched ? _coverId : null,
          crop: HighlightCrop(x: _cropX, y: _cropY, zoom: _cropZoom),
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
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(l10n.hlDeleteQuestion),
        content: Text(l10n.hlDeleteBody),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(c, true),
            child: Text(l10n.delete, style: TextStyle(color: Theme.of(c).colorScheme.error)),
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
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: AppBar(
        backgroundColor: lsBg(context),
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        title: Text(_isEdit ? l10n.hlEditHighlight : l10n.hlNewHighlight),
        actions: [
          if (_saving)
            const Padding(
              padding: EdgeInsets.only(right: 16),
              child: Center(child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))),
            )
          else
            TextButton(onPressed: _canSave ? _save : null, child: Text(_isEdit ? l10n.save : l10n.hlCreate)),
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
              child: Text(l10n.hlYourStories, style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: cs.onSurface)),
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
                  label: Text(l10n.hlDeleteAction, style: TextStyle(color: cs.error)),
                ),
              ),
            ),
          const SliverToBoxAdapter(child: SizedBox(height: 40)),
        ],
      ),
    );
  }

  Widget _header(ColorScheme cs) {
    final l10n = AppLocalizations.of(context)!;
    final cover = _effectiveCover;
    return Padding(
      padding: const EdgeInsets.fromLTRB(kLsPad, 8, kLsPad, 0),
      child: Row(children: [
        Container(
          width: 72,
          height: 72,
          padding: const EdgeInsets.all(2),
          decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: cs.outlineVariant, width: 1.5)),
          child: HighlightCover(
            url: cover?.mediaUrl,
            size: 65, // 72 - ring 2*1.5 - padding 2*2
            x: _coverId == cover?.id ? _cropX : 0.0,
            y: _coverId == cover?.id ? _cropY : 0.0,
            zoom: _coverId == cover?.id ? _cropZoom : 1.0,
          ),
        ),
        const SizedBox(width: 16),
        Expanded(
          child: TextField(
            controller: _titleCtrl,
            maxLength: _kTitleMax,
            textCapitalization: TextCapitalization.sentences,
            decoration: InputDecoration(labelText: l10n.hlTitleLabel, hintText: l10n.hlDefaultTitle),
          ),
        ),
      ]),
    );
  }

  Widget _selectedStrip(ColorScheme cs) {
    final l10n = AppLocalizations.of(context)!;
    final hasPhoto = _selected.values.any((s) => s.mediaType == 'image');
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(kLsPad, 12, kLsPad, 6),
        child: Row(children: [
          Expanded(
            child: Text('${l10n.hlSelectedCount(_selected.length)}\n${l10n.hlCoverHint}',
                style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
          ),
          if (hasPhoto) ...[
            IconButton(
              tooltip: l10n.hlAdjustCover,
              visualDensity: VisualDensity.compact,
              icon: const Icon(Icons.crop_rounded),
              onPressed: _adjustCover,
            ),
            ChoiceChip(
              label: Text(l10n.hlCoverAutomatic),
              selected: _coverId == null || _selected[_coverId] == null,
              visualDensity: VisualDensity.compact,
              onSelected: (_) => _setCover(null),
            ),
          ],
        ]),
      ),
      SizedBox(
        height: 84,
        child: ReorderableListView.builder(
          scrollDirection: Axis.horizontal,
          buildDefaultDragHandles: false,
          padding: const EdgeInsets.symmetric(horizontal: kLsPad),
          itemCount: _selected.length,
          onReorder: _reorder,
          proxyDecorator: (child, _, __) => Material(color: Colors.transparent, child: child),
          itemBuilder: (_, i) {
            final s = _selected.values.elementAt(i);
            final isCover = _coverId == s.id && s.mediaType == 'image';
            return ReorderableDelayedDragStartListener(
              key: ValueKey(s.id),
              index: i,
              child: Padding(
                padding: const EdgeInsets.only(right: 8),
                child: GestureDetector(
                  onTap: s.mediaType == 'image' ? () => _setCover(s.id) : null,
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
                ),
              ),
            );
          },
        ),
      ),
    ]);
  }

  List<Widget> _archiveSlivers(ColorScheme cs) {
    final l10n = AppLocalizations.of(context)!;
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
                _archiveFailed ? l10n.hlLoadStoriesFailed : l10n.hlNoStories,
                textAlign: TextAlign.center,
                style: TextStyle(color: cs.onSurfaceVariant),
              ),
              if (_archiveFailed) TextButton(onPressed: _loadArchive, child: Text(l10n.retry)),
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

/// Pan / zoom the round cover. Returns the chosen [HighlightCrop] (null = cancel).
/// Same [HighlightCover] widget as the profile row => WYSIWYG.
class _CoverCropDialog extends StatefulWidget {
  final String url;
  final HighlightCrop initial;
  const _CoverCropDialog({required this.url, required this.initial});

  @override
  State<_CoverCropDialog> createState() => _CoverCropDialogState();
}

class _CoverCropDialogState extends State<_CoverCropDialog> {
  static const double _size = 240;
  late double _x = widget.initial.x;
  late double _y = widget.initial.y;
  late double _zoom = widget.initial.zoom;
  double _zoomAtStart = 1.0;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return AlertDialog(
      title: Text(l10n.hlAdjustCover),
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        GestureDetector(
          onScaleStart: (_) => _zoomAtStart = _zoom,
          onScaleUpdate: (d) => setState(() {
            _zoom = (_zoomAtStart * d.scale).clamp(1.0, 3.0).toDouble();
            // Dragging right reveals the left part => focus moves left.
            _x = (_x - d.focalPointDelta.dx / (_size / 2) / _zoom).clamp(-1.0, 1.0).toDouble();
            _y = (_y - d.focalPointDelta.dy / (_size / 2) / _zoom).clamp(-1.0, 1.0).toDouble();
          }),
          child: Container(
            padding: const EdgeInsets.all(2),
            decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: cs.outlineVariant, width: 1.5)),
            child: HighlightCover(url: widget.url, size: _size, x: _x, y: _y, zoom: _zoom, cacheZoom: 3),
          ),
        ),
        const SizedBox(height: 8),
        Slider(value: _zoom, min: 1.0, max: 3.0, onChanged: (v) => setState(() => _zoom = v)),
        Text(l10n.hlCropHint, textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
      ]),
      actions: [
        TextButton(
          onPressed: () => setState(() {
            _x = 0.0;
            _y = 0.0;
            _zoom = 1.0;
          }),
          child: Text(l10n.hlReset),
        ),
        TextButton(onPressed: () => Navigator.pop(context), child: Text(l10n.cancel)),
        TextButton(onPressed: () => Navigator.pop(context, HighlightCrop(x: _x, y: _y, zoom: _zoom)), child: Text(l10n.hlDone)),
      ],
    );
  }
}
