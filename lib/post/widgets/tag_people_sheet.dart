// lib/post/widgets/tag_people_sheet.dart
//
// P5b-FE — bottom sheet used by new_post.dart to tag people in a post.
//
//   final result = await showTagPeopleSheet(context, initial: tags, photo: firstPhotoOrNull);
//   // null  -> dismissed, keep what you had
//   // list  -> the new tag set (may be empty = "clear all")
//
// * Search is the same `/profile/search/` call the composer's @mention uses.
// * When [photo] is given (first attachment is an image) the person can tap the
//   photo to place the selected tag at a spot (x/y in 0..1). Without a photo
//   (video / text / document) tags are just a list, no positions.
// * Max 20 people (backend limit).

import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';

import '../../search/api_service.dart' as search_api;
import '../services/post_tag_service.dart';

const int kMaxPostTags = 20;

Future<List<PostTagInput>?> showTagPeopleSheet(
  BuildContext context, {
  required List<PostTagInput> initial,
  File? photo,
}) {
  return showModalBottomSheet<List<PostTagInput>>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    backgroundColor: Colors.transparent,
    builder: (_) => _TagPeopleSheet(initial: initial, photo: photo),
  );
}

class _TagPeopleSheet extends StatefulWidget {
  const _TagPeopleSheet({required this.initial, this.photo});
  final List<PostTagInput> initial;
  final File? photo;

  @override
  State<_TagPeopleSheet> createState() => _TagPeopleSheetState();
}

class _TagPeopleSheetState extends State<_TagPeopleSheet> {
  late List<PostTagInput> _tags = List.of(widget.initial);
  final TextEditingController _search = TextEditingController();
  Timer? _debounce;
  int _requestId = 0;
  bool _searching = false;
  List<Map<String, dynamic>> _results = [];
  String? _placingUserId; // tag currently being placed on the photo

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  void _onQueryChanged(String q) {
    _debounce?.cancel();
    final query = q.trim().replaceFirst(RegExp(r'^@'), '');
    if (query.isEmpty) {
      setState(() {
        _results = [];
        _searching = false;
      });
      return;
    }
    _debounce = Timer(const Duration(milliseconds: 300), () => _runSearch(query));
  }

  Future<void> _runSearch(String query) async {
    final id = ++_requestId;
    setState(() => _searching = true);
    List<dynamic> rows = const [];
    try {
      rows = await search_api.SearchApiService.searchUsers(query);
    } catch (_) {}
    if (!mounted || id != _requestId) return;
    setState(() {
      _results = [
        for (final r in rows)
          if (r is Map && r['id'] != null && (r['username'] ?? '').toString().isNotEmpty)
            Map<String, dynamic>.from(r),
      ];
      _searching = false;
    });
  }

  bool _isTagged(String userId) => _tags.any((t) => t.userId == userId);

  void _add(Map<String, dynamic> u) {
    final id = u['id'].toString();
    if (_isTagged(id)) return;
    if (_tags.length >= kMaxPostTags) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('You can tag up to $kMaxPostTags people.')),
      );
      return;
    }
    final pic = (u['profile_photo'] ?? u['profile_picture'])?.toString();
    setState(() {
      _tags = [
        ..._tags,
        PostTagInput(
          userId: id,
          username: u['username'].toString(),
          profilePicture: (pic == null || pic.isEmpty) ? null : pic,
        ),
      ];
      if (widget.photo != null) _placingUserId = id; // go straight to placing it
    });
  }

  void _remove(String userId) {
    setState(() {
      _tags = _tags.where((t) => t.userId != userId).toList();
      if (_placingUserId == userId) _placingUserId = null;
    });
  }

  void _placeAt(Offset local, Size size) {
    final id = _placingUserId;
    if (id == null || size.width <= 0 || size.height <= 0) return;
    final x = (local.dx / size.width).clamp(0.0, 1.0);
    final y = (local.dy / size.height).clamp(0.0, 1.0);
    setState(() {
      _tags = [for (final t in _tags) t.userId == id ? t.copyWith(x: x, y: y) : t];
    });
  }

  Widget _photoView(File f) {
    // dart:io File images don't work on Flutter web — there `path` is a blob/url.
    return kIsWeb ? Image.network(f.path, fit: BoxFit.fill) : Image.file(f, fit: BoxFit.fill);
  }

  // The photo is shown at its OWN aspect ratio (loose constraints + BoxFit.fill), so a tap's
  // fraction of the widget size == the fraction of the real image the backend expects.
  Widget _photoPlacer(ColorScheme cs) {
    final photo = widget.photo!;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.fromLTRB(16, 4, 16, 8),
      decoration: BoxDecoration(color: Colors.black, borderRadius: BorderRadius.circular(14)),
      clipBehavior: Clip.antiAlias,
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxHeight: 260),
          child: Stack(
            children: [
              _photoView(photo),
              Positioned.fill(
                child: LayoutBuilder(
                  builder: (context, box) => GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTapDown: (d) => _placeAt(d.localPosition, Size(box.maxWidth, box.maxHeight)),
                    child: Stack(
                      clipBehavior: Clip.none,
                      children: [
                        for (final t in _tags)
                          if (t.hasPosition)
                            Positioned(
                              left: t.x! * box.maxWidth,
                              top: t.y! * box.maxHeight,
                              child: FractionalTranslation(
                                translation: const Offset(-0.5, -0.5),
                                child: _TagChip(label: t.username, highlighted: t.userId == _placingUserId),
                              ),
                            ),
                      ],
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;
    PostTagInput? placing;
    for (final t in _tags) {
      if (t.userId == _placingUserId) placing = t;
    }

    return Padding(
      padding: EdgeInsets.only(bottom: bottomInset),
      child: DraggableScrollableSheet(
        initialChildSize: 0.9,
        minChildSize: 0.5,
        maxChildSize: 0.95,
        expand: false,
        builder: (context, scroll) {
          return Container(
            decoration: BoxDecoration(
              color: cs.surface,
              borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
            ),
            child: Column(
              children: [
                const SizedBox(height: 10),
                Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(8, 8, 8, 0),
                  child: Row(
                    children: [
                      TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
                      const Expanded(
                        child: Text('Tag people', textAlign: TextAlign.center, style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800)),
                      ),
                      TextButton(
                        onPressed: () => Navigator.pop(context, _tags),
                        child: const Text('Done', style: TextStyle(fontWeight: FontWeight.w800)),
                      ),
                    ],
                  ),
                ),
                if (widget.photo != null) ...[
                  _photoPlacer(cs),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Text(
                      placing != null
                          ? 'Tap the photo to place @${placing.username}'
                          : (_tags.isEmpty ? 'Search a person below, then tap the photo to place the tag.' : 'Tap a person below to (re)place their tag on the photo.'),
                      style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant),
                    ),
                  ),
                  const SizedBox(height: 6),
                ],
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                  child: TextField(
                    controller: _search,
                    onChanged: _onQueryChanged,
                    textInputAction: TextInputAction.search,
                    decoration: InputDecoration(
                      hintText: 'Search people',
                      prefixIcon: const Icon(Icons.search_rounded),
                      filled: true,
                      fillColor: cs.surfaceContainerHighest.withOpacity(0.5),
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
                      contentPadding: const EdgeInsets.symmetric(vertical: 12),
                    ),
                  ),
                ),
                Expanded(
                  child: ListView(
                    controller: scroll,
                    padding: const EdgeInsets.only(bottom: 16),
                    children: [
                      if (_searching)
                        const Padding(
                          padding: EdgeInsets.all(16),
                          child: Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))),
                        ),
                      if (!_searching && _search.text.trim().isNotEmpty && _results.isEmpty)
                        Padding(
                          padding: const EdgeInsets.all(16),
                          child: Text('No people found', textAlign: TextAlign.center, style: TextStyle(color: cs.onSurfaceVariant)),
                        ),
                      for (final u in _results)
                        ListTile(
                          leading: _Avatar(url: (u['profile_photo'] ?? u['profile_picture'])?.toString(), name: u['username'].toString()),
                          title: Text('@${u['username']}', style: const TextStyle(fontWeight: FontWeight.w700)),
                          subtitle: _fullName(u) == null ? null : Text(_fullName(u)!),
                          trailing: _isTagged(u['id'].toString())
                              ? Icon(Icons.check_circle_rounded, color: cs.primary)
                              : const Icon(Icons.add_circle_outline_rounded),
                          onTap: () => _add(u),
                        ),
                      if (_tags.isNotEmpty) ...[
                        Padding(
                          padding: const EdgeInsets.fromLTRB(16, 14, 16, 4),
                          child: Text('Tagged (${_tags.length}/$kMaxPostTags)', style: TextStyle(fontWeight: FontWeight.w800, color: cs.onSurfaceVariant, fontSize: 12.5)),
                        ),
                        for (final t in _tags)
                          ListTile(
                            leading: _Avatar(url: t.profilePicture, name: t.username),
                            title: Text('@${t.username}', style: const TextStyle(fontWeight: FontWeight.w700)),
                            subtitle: widget.photo == null
                                ? null
                                : Text(t.hasPosition ? 'Placed on photo' : 'Not placed', style: TextStyle(color: t.hasPosition ? cs.primary : cs.onSurfaceVariant)),
                            selected: t.userId == _placingUserId,
                            onTap: widget.photo == null ? null : () => setState(() => _placingUserId = t.userId),
                            trailing: IconButton(
                              icon: const Icon(Icons.close_rounded),
                              tooltip: 'Remove',
                              onPressed: () => _remove(t.userId),
                            ),
                          ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }

  String? _fullName(Map<String, dynamic> u) {
    final n = '${u['first_name'] ?? ''} ${u['last_name'] ?? ''}'.trim();
    return n.isEmpty ? null : n;
  }
}

class _Avatar extends StatelessWidget {
  const _Avatar({this.url, required this.name});
  final String? url;
  final String name;

  @override
  Widget build(BuildContext context) {
    final has = url != null && url!.isNotEmpty;
    return CircleAvatar(
      radius: 20,
      backgroundImage: has ? NetworkImage(url!) : null,
      child: has ? null : Text(name.isEmpty ? '?' : name[0].toUpperCase()),
    );
  }
}

class _TagChip extends StatelessWidget {
  const _TagChip({required this.label, this.highlighted = false});
  final String label;
  final bool highlighted;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: highlighted ? Theme.of(context).colorScheme.primary : Colors.black87,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Text(label, style: const TextStyle(color: Colors.white, fontSize: 11.5, fontWeight: FontWeight.w700)),
    );
  }
}
