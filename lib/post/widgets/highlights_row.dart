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
//  * Fixed geometry (no overflow at any text scale): circle 66, label slot 18,
//    row [kHighlightsRowHeight]. Loading on own profile = shimmer circles.
//  * [HighlightCover] draws the round cover incl. the owner's crop; the cover
//    editor reuses it so what you crop is exactly what the row shows.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import '../../l10n/app_localizations.dart';
import '../../utils/api.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart' show LsShimmer, LsSkeletonBox;
import '../models/highlight_model.dart';
import '../services/highlight_service.dart';

const double _kOuter = 66; // ring box
const double _kRing = 1.5; // ring stroke
const double _kGap = 2; // gap between ring and photo
const double _kInner = _kOuter - 2 * (_kRing + _kGap); // 59 - the photo circle
const double _kLabelH = 18; // label slot (label text scale is clamped)
const double _kVPad = 8;

/// Total height of the row: ring + 4 + label + vertical padding.
const double kHighlightsRowHeight = _kOuter + 4 + _kLabelH + 2 * _kVPad;

/// Resolves a (possibly relative) media path against the API host.
String? highlightMediaUrl(String? raw) {
  if (raw == null || raw.isEmpty) return null;
  return raw.startsWith('http') ? raw : '${Api.baseUrl}$raw';
}

/// The round cover. Pure function of (url, x, y, zoom, size) so the row, the
/// editor header and the crop dialog all draw the same pixels.
///   x / y : focus point -1..1 (0,0 = centre)   zoom : 1..3
/// No url (all-video highlight / load error) => neutral placeholder.
class HighlightCover extends StatelessWidget {
  final String? url;
  final double size;
  final double x;
  final double y;
  final double zoom;

  /// Decode-size zoom. 0 = follow [zoom]. The crop dialog passes 3 so the
  /// image is not re-decoded while the user zooms.
  final double cacheZoom;

  const HighlightCover({
    super.key,
    required this.url,
    required this.size,
    this.x = 0.0,
    this.y = 0.0,
    this.zoom = 1.0,
    this.cacheZoom = 0,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final placeholder = Container(
      color: cs.surfaceVariant,
      alignment: Alignment.center,
      child: Icon(Icons.auto_awesome_rounded, size: size * 0.4, color: cs.onSurfaceVariant),
    );
    final u = url;
    Widget child;
    if (u == null || u.isEmpty) {
      child = placeholder;
    } else {
      final z = zoom.clamp(1.0, 3.0).toDouble();
      final align = Alignment(x.clamp(-1.0, 1.0).toDouble(), y.clamp(-1.0, 1.0).toDouble());
      final dpr = MediaQuery.of(context).devicePixelRatio;
      // Decode only what is drawn (x1.5 headroom for landscape photos, which
      // are height-fitted): keeps memory low in a long row of covers.
      final img = CachedNetworkImage(
        imageUrl: u,
        width: size,
        height: size,
        fit: BoxFit.cover,
        alignment: align,
        memCacheWidth: (size * dpr * (cacheZoom > 0 ? cacheZoom : z) * 1.5).ceil(),
        fadeInDuration: const Duration(milliseconds: 120),
        placeholder: (_, __) => Container(color: cs.surfaceVariant),
        errorWidget: (_, __, ___) => placeholder,
      );
      child = z <= 1.0 ? img : Transform.scale(scale: z, alignment: align, child: img);
    }
    return SizedBox(width: size, height: size, child: ClipOval(child: child));
  }
}

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
      // Own profile: shimmer circles (same geometry => no jump when data lands).
      // Other profiles: nothing until we know there is something (avoids a
      // row that appears and collapses again).
      return showNew ? _skeleton() : const SizedBox.shrink();
    }
    if (_items.isEmpty && !showNew) return const SizedBox.shrink(); // empty => hide

    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    final count = _items.length + (showNew ? 1 : 0);
    return SizedBox(
      height: kHighlightsRowHeight,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: kLsPad, vertical: _kVPad),
        itemCount: count,
        separatorBuilder: (_, __) => const SizedBox(width: 14),
        itemBuilder: (context, i) {
          if (showNew && i == 0) {
            return _circle(cs,
                title: l10n.hlNew,
                onTap: widget.onCreate,
                child: SizedBox(
                  width: _kInner,
                  height: _kInner,
                  child: ClipOval(
                    child: Container(
                      color: cs.surfaceVariant,
                      alignment: Alignment.center,
                      child: Icon(Icons.add_rounded, size: 28, color: cs.onSurface),
                    ),
                  ),
                ));
          }
          final h = _items[i - (showNew ? 1 : 0)];
          return _circle(
            cs,
            title: h.title.isEmpty ? l10n.hlDefaultTitle : h.title,
            onTap: widget.onOpen == null ? null : () => widget.onOpen!(h),
            onLongPress: (widget.isOwner && widget.onEdit != null) ? () => widget.onEdit!(h) : null,
            child: HighlightCover(url: highlightMediaUrl(h.coverUrl), size: _kInner, x: h.coverX, y: h.coverY, zoom: h.coverZoom),
          );
        },
      ),
    );
  }

  Widget _skeleton() {
    return LsShimmer(
      child: SizedBox(
        height: kHighlightsRowHeight,
        child: ListView.separated(
          scrollDirection: Axis.horizontal,
          physics: const NeverScrollableScrollPhysics(),
          padding: const EdgeInsets.symmetric(horizontal: kLsPad, vertical: _kVPad),
          itemCount: 5,
          separatorBuilder: (_, __) => const SizedBox(width: 14),
          itemBuilder: (_, __) => const SizedBox(
            width: _kOuter,
            child: Column(children: [
              LsSkeletonBox(width: _kOuter, height: _kOuter, radius: _kOuter / 2),
              SizedBox(height: 4),
              LsSkeletonBox(width: 40, height: 8, radius: 4, margin: EdgeInsets.only(top: 4)),
            ]),
          ),
        ),
      ),
    );
  }

  Widget _circle(ColorScheme cs, {required String title, required Widget child, VoidCallback? onTap, VoidCallback? onLongPress}) {
    // Label text scale is clamped so a large system font can never push the
    // tile past its fixed height (that was the bottom overflow).
    final mq = MediaQuery.of(context);
    return Semantics(
      button: true,
      label: title,
      excludeSemantics: true,
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        onLongPress: onLongPress,
        child: SizedBox(
          width: _kOuter,
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Container(
              width: _kOuter,
              height: _kOuter,
              padding: const EdgeInsets.all(_kGap),
              decoration: BoxDecoration(shape: BoxShape.circle, border: Border.all(color: cs.outlineVariant, width: _kRing)),
              child: child,
            ),
            const SizedBox(height: 4),
            SizedBox(
              height: _kLabelH,
              child: MediaQuery(
                data: mq.copyWith(textScaler: mq.textScaler.clamp(minScaleFactor: 1.0, maxScaleFactor: 1.15)),
                child: Align(
                  alignment: Alignment.topCenter,
                  child: Text(title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      textAlign: TextAlign.center,
                      style: TextStyle(fontSize: 11.5, height: 1.2, color: cs.onSurface)),
                ),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}
