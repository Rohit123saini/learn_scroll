import 'package:flutter/material.dart';

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
          child: Image.network(
            resolved,
            fit: BoxFit.contain,
            semanticLabel: semanticLabel,
            loadingBuilder: (context, child, progress) {
              if (progress == null) return child;
              return SizedBox(
                height: 120,
                child: Center(child: CircularProgressIndicator(strokeWidth: 2, value: _fraction(progress))),
              );
            },
            errorBuilder: (context, _, __) => SizedBox(
              height: 90,
              child: Center(child: Icon(Icons.broken_image_outlined, color: cs.onSurfaceVariant)),
            ),
          ),
        ),
      ),
    );
  }

  double? _fraction(ImageChunkEvent p) {
    final total = p.expectedTotalBytes;
    if (total == null || total == 0) return null;
    return p.cumulativeBytesLoaded / total;
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
          child: Image.network(
            url,
            fit: BoxFit.contain,
            errorBuilder: (context, _, __) =>
                const Icon(Icons.broken_image_outlined, color: Colors.white54, size: 48),
          ),
        ),
      ),
    );
  }
}
