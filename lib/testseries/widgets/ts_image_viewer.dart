import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../../l10n/app_localizations.dart';
import '../services/testseries_service.dart';

/// Question / answer image: inline preview + tap pe full-screen zoom.
class TsNetworkImage extends StatelessWidget {
  final String? url; // raw (relative bhi chalega)
  final double maxHeight;
  final String? semanticLabel;

  const TsNetworkImage({super.key, required this.url, this.maxHeight = 220, this.semanticLabel});

  @override
  Widget build(BuildContext context) {
    final resolved = TestSeriesService.resolveMedia(url);
    if (resolved == null) return const SizedBox.shrink();
    final cs = Theme.of(context).colorScheme;

    return GestureDetector(
      onTap: () => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => _TsImageViewerPage(url: resolved)),
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(12),
        child: Container(
          color: cs.surfaceVariant,
          constraints: BoxConstraints(maxHeight: maxHeight),
          width: double.infinity,
          // TASK G17 (growth_and_feature_tasks.md) — was a bare
          // Image.network with no cache: every question/response card
          // re-downloaded its image on each rebuild, and tapping through
          // to the full-screen zoom below re-fetched it again from
          // scratch. CachedNetworkImage shares one disk/memory cache
          // across both, keyed by URL, plus a bounded memCacheHeight so a
          // full-resolution exam attachment isn't decoded at full size
          // just to show a small inline preview.
          child: Semantics(
            image: true,
            label: semanticLabel,
            child: CachedNetworkImage(
              imageUrl: resolved,
              fit: BoxFit.contain,
              memCacheHeight: (maxHeight * 2).round(),
              progressIndicatorBuilder: (context, url, progress) => SizedBox(
                height: 120,
                child: Center(child: CircularProgressIndicator(strokeWidth: 2, value: progress.progress)),
              ),
              errorWidget: (context, url, error) => SizedBox(
                height: 90,
                child: Center(child: Icon(Icons.broken_image_outlined, color: cs.onSurfaceVariant)),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _TsImageViewerPage extends StatelessWidget {
  final String url;
  const _TsImageViewerPage({required this.url});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.close_rounded),
          tooltip: l10n.cancel,
          onPressed: () => Navigator.of(context).pop(),
        ),
      ),
      body: Center(
        child: InteractiveViewer(
          minScale: 1,
          maxScale: 5,
          // TASK G17 — same CachedNetworkImage + URL as the inline
          // preview above, so opening the zoom view reuses whatever was
          // already downloaded instead of re-fetching the full image.
          child: CachedNetworkImage(
            imageUrl: url,
            fit: BoxFit.contain,
            errorWidget: (context, url, error) =>
                const Icon(Icons.broken_image_outlined, color: Colors.white54, size: 48),
          ),
        ),
      ),
    );
  }
}
