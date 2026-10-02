import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter/painting.dart';
import 'package:flutter_cache_manager/flutter_cache_manager.dart';

/// C5 — size-limited image caches for the whole app.
///
/// Disk: `flutter_cache_manager` can't cap by bytes, only by object count and
/// age. 400 objects x ~150-250 KB (feed thumbs / avatars) ≈ 60-100 MB worst
/// case; least-recently-used files are evicted past that. Tune [maxObjects].
/// Memory: decoded-image cache capped so feed scrolling can't balloon RAM.
class AppImageCache {
  AppImageCache._();

  static const int maxObjects = 400;
  static const Duration stalePeriod = Duration(days: 30);

  static final CacheManager manager = CacheManager(
    Config(
      'learnscrollImageCache',
      stalePeriod: stalePeriod,
      maxNrOfCacheObjects: maxObjects,
    ),
  );

  /// Call once in main() after WidgetsFlutterBinding.ensureInitialized().
  static void configureMemoryCache() {
    final cache = PaintingBinding.instance.imageCache;
    cache.maximumSize = 150; // images
    cache.maximumSizeBytes = 80 << 20; // 80 MB decoded
  }

  /// Wipe the disk cache (e.g. a "Clear cache" button / on logout).
  static Future<void> clear() => manager.emptyCache();
}

/// Drop-in `CachedNetworkImage` that always uses [AppImageCache.manager] and
/// decodes at display size instead of full resolution.
///
/// Pass [memCacheWidth] (logical px * devicePixelRatio is applied for you via
/// [cacheWidth]) — e.g. AppCachedImage(url: u, cacheWidth: 120) for a 40px
/// avatar, so a 4000px photo is never decoded in full.
class AppCachedImage extends StatelessWidget {
  final String url;
  final double? width;
  final double? height;
  final BoxFit fit;
  final int? cacheWidth; // logical pixels
  final Widget? placeholder;
  final Widget? errorWidget;

  const AppCachedImage({
    super.key,
    required this.url,
    this.width,
    this.height,
    this.fit = BoxFit.cover,
    this.cacheWidth,
    this.placeholder,
    this.errorWidget,
  });

  @override
  Widget build(BuildContext context) {
    final dpr = MediaQuery.devicePixelRatioOf(context);
    final target = cacheWidth ?? width?.round();
    return CachedNetworkImage(
      imageUrl: url,
      cacheManager: AppImageCache.manager,
      width: width,
      height: height,
      fit: fit,
      memCacheWidth: target == null ? null : (target * dpr).round(),
      placeholder: placeholder == null ? null : (_, __) => placeholder!,
      errorWidget: (_, __, ___) =>
          errorWidget ?? const Icon(Icons.broken_image_outlined),
    );
  }
}
