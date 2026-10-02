// lib/post/widgets/highlights_row.dart
//
// P1-FE — horizontal circle row (cover + title) shown under the profile
// header. Used by BOTH `profile.dart` (isOwner: true) and
// `target_profile.dart` (isOwner: false).
//
// Rules:
//  * loading / error / empty  -> renders nothing (row is hidden), EXCEPT
//    on own profile, where the "New +" circle stays so the user can create
//    their first highlight.
//  * "New +" only when `isOwner`.
//  * Visibility for other people's highlights is decided by the backend
//    (private account not followed / blocked / hidden stories => empty
//    list), so there is no client-side guessing here.
//  * `reloadToken`: bump it from the parent (e.g. pull-to-refresh) to refetch.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../../utils/api.dart';
import '../../widgets/ls_ui.dart';
import '../models/highlight_model.dart';
import '../services/highlight_service.dart';

class HighlightsRow extends StatefulWidget {
  /// null => my own highlights.
  final int? userId;
  final bool isOwner;
  final int reloadToken;
  final void Function(Highlight highlight)? onOpen;
  final VoidCallback? onCreate;
  final void Function(Highlight highlight)? onEdit; // long-press, own profile only

  const HighlightsRow({
    super.key,
    this.userId,
    required this.isOwner,
    this.reloadToken = 0,
    this.onOpen,
    this.onCreate,
    this.onEdit,
  });

  @override
  State<HighlightsRow> createState() => HighlightsRowState();
}

class HighlightsRowState extends State<HighlightsRow> {
  List<Highlight> _items = const [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    reload();
  }

  @override
  void didUpdateWidget(covariant HighlightsRow old) {
    super.didUpdateWidget(old);
    if (old.reloadToken != widget.reloadToken || old.userId != widget.userId) reload();
  }

  Future<void> reload() async {
    try {
      final list = await HighlightService.getHighlights(userId: widget.isOwner ? null : widget.userId);
      if (!mounted) return;
      setState(() {
        _items = list;
        _loading = false;
      });
    } catch (_) {
      // Non-critical strip: on failure keep whatever we had (or hide).
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final showNew = widget.isOwner;
    if (_loading && _items.isEmpty) {
      return showNew ? const SizedBox(height: 92) : const SizedBox.shrink();
    }
    if (_items.isEmpty && !showNew) return const SizedBox.shrink(); // empty => hide

    final cs = Theme.of(context).colorScheme;
    final count = _items.length + (showNew ? 1 : 0);
    return SizedBox(
      height: 96,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: kLsPad, vertical: 8),
        itemCount: count,
        separatorBuilder: (_, __) => const SizedBox(width: 14),
        itemBuilder: (context, i) {
          if (showNew && i == 0) {
            return _circle(cs, title: 'New', onTap: widget.onCreate, child: Icon(Icons.add_rounded, size: 28, color: cs.onSurface));
          }
          final h = _items[i - (showNew ? 1 : 0)];
          final cover = h.coverUrl;
          final url = cover == null ? null : (cover.startsWith('http') ? cover : '${Api.baseUrl}$cover');
          return _circle(
            cs,
            title: h.title.isEmpty ? 'Highlights' : h.title,
            onTap: widget.onOpen == null ? null : () => widget.onOpen!(h),
            onLongPress: (widget.isOwner && widget.onEdit != null) ? () => widget.onEdit!(h) : null,
            child: url == null
                ? Icon(Icons.auto_awesome_rounded, size: 24, color: cs.onSurfaceVariant)
                : CachedNetworkImage(
                    imageUrl: url,
                    fit: BoxFit.cover,
                    width: 58,
                    height: 58,
                    placeholder: (c, u) => Container(color: cs.surfaceVariant),
                    errorWidget: (c, u, e) => Icon(Icons.auto_awesome_rounded, size: 24, color: cs.onSurfaceVariant),
                  ),
          );
        },
      ),
    );
  }

  Widget _circle(ColorScheme cs, {required String title, required Widget child, VoidCallback? onTap, VoidCallback? onLongPress}) {
    return Semantics(
      button: true,
      label: title,
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        onLongPress: onLongPress,
        child: SizedBox(
          width: 66,
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Container(
              width: 64,
              height: 64,
              padding: const EdgeInsets.all(2),
              decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: cs.outlineVariant, width: 1.5)),
              child: ClipOval(child: Container(color: cs.surfaceVariant, alignment: Alignment.center, child: child)),
            ),
            const SizedBox(height: 4),
            Text(title, maxLines: 1, overflow: TextOverflow.ellipsis, textAlign: TextAlign.center,
                style: TextStyle(fontSize: 11.5, color: cs.onSurface)),
          ]),
        ),
      ),
    );
  }
}
