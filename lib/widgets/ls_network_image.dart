// lib/widgets/ls_network_image.dart
//
// C4-FE — ONE image widget for every post-media surface, so "blurhash until it
// loads + the right size for the place" is decided in a single spot instead of
// being re-implemented per screen:
//
//   profile grid  -> LsNetworkImage(url: media.gridUrl, ...)   // thumb_url  (~320px)
//   feed card     -> LsNetworkImage(url: media.feedUrl, ...)   // medium_url  (~720px)
//   fullscreen    -> the ORIGINAL `file` (FullScreenImagePage), never a variant
//
// While the image downloads it paints the BlurHash (decoded locally from a ~28
// char string, no network). If the server has no hash yet (old post, task still
// queued) or the hash is malformed, it falls back to a plain surface colour —
// a bad hash must never throw inside a scrolling list.
//
// If the resized variant 404s (e.g. variants were regenerated and the old URL
// went stale in a cached response) it silently retries ONCE with `fallbackUrl`
// (the original file) before showing the error tile.
//
// Needs `flutter_blurhash` in pubspec.yaml:   flutter pub add flutter_blurhash
import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_blurhash/flutter_blurhash.dart';

// BlurHash base-83 alphabet (raw string: it contains `$`).
const String _kBase83 =
    r'0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~';

/// True only for a well-formed BlurHash: right alphabet, and a length that
/// matches the component counts encoded in its first character
/// (`4 + 2 * numX * numY`). Mirrors the backend encoder (post/services.py).
bool isValidBlurhash(String? hash) {
  if (hash == null || hash.length < 6) return false;
  for (var i = 0; i < hash.length; i++) {
    if (!_kBase83.contains(hash[i])) return false;
  }
  final sizeFlag = _kBase83.indexOf(hash[0]);
  final numX = sizeFlag % 9 + 1;
  final numY = sizeFlag ~/ 9 + 1;
  return hash.length == 4 + 2 * numX * numY;
}

class LsNetworkImage extends StatelessWidget {
  const LsNetworkImage({
    super.key,
    required this.url,
    this.blurhash,
    this.fallbackUrl,
    this.fit = BoxFit.cover,
    this.width,
    this.height,
    this.memCacheWidth,
    this.fadeInDuration = const Duration(milliseconds: 180),
    this.errorBuilder,
  });

  /// The size-appropriate URL (thumb / medium). Empty is allowed if
  /// [fallbackUrl] is set.
  final String url;

  /// `blurhash` from the API; null/empty/malformed -> plain colour placeholder.
  final String? blurhash;

  /// Tried once if [url] fails to load — normally the original `file`.
  final String? fallbackUrl;

  final BoxFit fit;
  final double? width;
  final double? height;

  /// Decode-size cap in pixels (saves RAM for small grid tiles). Leave null for
  /// feed/medium images — they are already ~720px wide.
  final int? memCacheWidth;

  final Duration fadeInDuration;

  /// Custom error tile; default is a surface-coloured box with a broken-image icon.
  final WidgetBuilder? errorBuilder;

  @override
  Widget build(BuildContext context) {
    final fb = fallbackUrl;
    if (url.isNotEmpty) return _load(context, url, retryWith: fb);
    if (fb != null && fb.isNotEmpty) return _load(context, fb);
    return _error(context);
  }

  Widget _load(BuildContext context, String src, {String? retryWith}) {
    return CachedNetworkImage(
      imageUrl: src,
      width: width,
      height: height,
      fit: fit,
      memCacheWidth: memCacheWidth,
      fadeInDuration: fadeInDuration,
      fadeOutDuration: fadeInDuration, // default is 1s — the blur would linger over the loaded image
      placeholder: (_, __) => _placeholder(context),
      errorWidget: (ctx, _, __) {
        if (retryWith != null && retryWith.isNotEmpty && retryWith != src) {
          return _load(ctx, retryWith);
        }
        return _error(ctx);
      },
    );
  }

  Widget _placeholder(BuildContext context) {
    final hash = blurhash;
    if (isValidBlurhash(hash)) {
      return SizedBox(
        width: width,
        height: height,
        child: BlurHash(hash: hash!, imageFit: BoxFit.cover),
      );
    }
    return Container(width: width, height: height, color: Theme.of(context).colorScheme.surfaceVariant);
  }

  Widget _error(BuildContext context) {
    final builder = errorBuilder;
    if (builder != null) return builder(context);
    final cs = Theme.of(context).colorScheme;
    return Container(
      width: width,
      height: height,
      color: cs.surfaceVariant,
      alignment: Alignment.center,
      child: Icon(Icons.broken_image_outlined, color: cs.onSurfaceVariant),
    );
  }
}
