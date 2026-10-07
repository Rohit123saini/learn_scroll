// Shared media presentation for the home feed AND the single-post screen.
//
// Both screens used to hand-roll their own media tiles (feed: fixed 55%
// screen height + BoxFit.cover, embedded PDF viewer; single post: fixed
// 580px black box + BoxFit.cover), so images were cropped/unreadable and
// documents looked like raw files. Everything visual now lives here so the
// two screens can never drift apart again.
//
// This file is deliberately presentational only: it imports no screens, so
// callers pass callbacks (onTap / onOpen / onDownload) and there is no
// circular import with singlepost.dart.
import 'dart:ui' show ImageFilter;

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/ls_network_image.dart';
import '../../services/home_api_model_service.dart' show PostMediaModel;
import 'post_media_ratio.dart';

enum PostMediaKind { image, video, pdf, doc }

class PostMediaUtil {
  PostMediaUtil._();

  static const _videoExt = ['mp4', 'mov', 'mkv', 'webm', 'm4v', '3gp'];
  static const _imageExt = ['jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'bmp'];
  static const _docExt = ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'rtf', 'zip'];

  /// Lower-case file extension of [urlOrName] (query string / fragment ignored).
  static String ext(String urlOrName) {
    var path = urlOrName;
    final u = Uri.tryParse(urlOrName);
    if (u != null && u.path.isNotEmpty) path = u.path;
    final name = path.split('/').last;
    final i = name.lastIndexOf('.');
    if (i < 0 || i == name.length - 1) return '';
    return name.substring(i + 1).toLowerCase();
  }

  static PostMediaKind kind(PostMediaModel m, {String? url}) {
    final type = m.mediaType.toLowerCase();
    final e = ext(url ?? m.file);
    // Documents are decided by extension first: the backend can label them
    // with a generic media_type, and an "image" default must not win.
    if (e == 'pdf' || type == 'pdf') return PostMediaKind.pdf;
    if (_docExt.contains(e)) return PostMediaKind.doc;
    if (type == 'video' || _videoExt.contains(e)) return PostMediaKind.video;
    if (type == 'image' || type == 'gif' || _imageExt.contains(e)) return PostMediaKind.image;
    return PostMediaKind.doc;
  }

  static String displayName(PostMediaModel m, {String? url}) {
    if (m.fileName.trim().isNotEmpty) return m.fileName.trim();
    final src = (url ?? m.file);
    final u = Uri.tryParse(src);
    final path = (u != null && u.path.isNotEmpty) ? u.path : src;
    final last = path.split('/').last;
    return last.isEmpty ? 'file' : Uri.decodeComponent(last);
  }

  /// Height of the whole media frame for a post.
  ///
  /// Like Instagram/Threads the FIRST visual (image/video) decides the frame
  /// ratio for the whole carousel, clamped between 1.91:1 (widest) and 4:5
  /// (tallest) — see post_media_ratio.dart — so a tall portrait never eats the
  /// screen and a panorama never becomes a sliver.
  /// Anything with a different ratio is letterboxed *inside* the frame (see
  /// [PostImageTile]) instead of being cropped. Documents-only posts get a
  /// compact fixed card height.
  static double frameHeight({
    required double width,
    required List<PostMediaModel> media,
    required double maxHeight,
  }) {
    PostMediaModel? first;
    for (final m in media) {
      final k = kind(m);
      if (k == PostMediaKind.image || k == PostMediaKind.video) {
        first = m;
        break;
      }
    }
    if (first == null) return 232;
    final w = first.width, h = first.height;
    double ratio;
    if (w != null && h != null && w > 0 && h > 0) {
      ratio = w / h;
    } else {
      ratio = kind(first) == PostMediaKind.video ? 16 / 9 : kPostMediaDefaultRatio;
    }
    ratio = ratio.clamp(kPostMediaMinRatio, kPostMediaMaxRatio).toDouble();
    // Never shorter than the widest allowed frame (1.91:1); the screen-height
    // cap can only shrink a tall frame down to that floor, not below it.
    final minHeight = width / kPostMediaMaxRatio;
    var height = width / ratio;
    if (maxHeight > 0 && height > maxHeight) height = maxHeight;
    return height < minHeight ? minHeight : height;
  }
}

/// Image that is NEVER cropped: shown with BoxFit.contain over a blurred,
/// dimmed copy of itself (same cached file, so no second download).
class PostImageTile extends StatelessWidget {
  final String url;
  final VoidCallback? onTap;
  const PostImageTile({super.key, required this.url, this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: Stack(fit: StackFit.expand, children: [
        ImageFiltered(
          imageFilter: ImageFilter.blur(sigmaX: 28, sigmaY: 28),
          child: CachedNetworkImage(
            imageUrl: url,
            fit: BoxFit.cover,
            memCacheWidth: 160,
            fadeInDuration: Duration.zero,
            placeholder: (_, __) => const ColoredBox(color: Colors.black),
            errorWidget: (_, __, ___) => const ColoredBox(color: Colors.black),
          ),
        ),
        const ColoredBox(color: Color(0x55000000)),
        CachedNetworkImage(
          imageUrl: url,
          fit: BoxFit.contain,
          width: double.infinity,
          height: double.infinity,
          memCacheWidth: 1440,
          fadeInDuration: const Duration(milliseconds: 160),
          placeholder: (_, __) => const Center(
            child: SizedBox(
              width: 26,
              height: 26,
              child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white54),
            ),
          ),
          errorWidget: (_, __, ___) => const Center(
            child: Icon(Icons.broken_image_outlined, color: Colors.white38, size: 44),
          ),
        ),
      ]),
    );
  }
}

/// Feed-card image: the ~720px variant (BlurHash placeholder, original as
/// fallback) shown with BoxFit.contain over a blurred, dimmed copy of itself —
/// the same look as [PostImageTile], so feed and single post match. The
/// background reuses the same cached file (no second download).
class PostFeedImage extends StatelessWidget {
  final String url;
  final String? fallbackUrl;
  final String? blurhash;
  final VoidCallback? onTap;
  const PostFeedImage({super.key, required this.url, this.fallbackUrl, this.blurhash, this.onTap});

  @override
  Widget build(BuildContext context) {
    final bgUrl = url.isNotEmpty ? url : (fallbackUrl ?? '');
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: Stack(fit: StackFit.expand, children: [
        if (bgUrl.isNotEmpty)
          ImageFiltered(
            imageFilter: ImageFilter.blur(sigmaX: 28, sigmaY: 28),
            child: CachedNetworkImage(
              imageUrl: bgUrl,
              fit: BoxFit.cover,
              memCacheWidth: 160,
              fadeInDuration: Duration.zero,
              placeholder: (_, __) => const ColoredBox(color: Colors.black),
              errorWidget: (_, __, ___) => const ColoredBox(color: Colors.black),
            ),
          )
        else
          const ColoredBox(color: Colors.black),
        const ColoredBox(color: Color(0x55000000)),
        LsNetworkImage(
          url: url,
          fallbackUrl: fallbackUrl,
          blurhash: blurhash,
          fit: BoxFit.contain,
          width: double.infinity,
          height: double.infinity,
        ),
      ]),
    );
  }
}

/// "2/5" chip shown on multi-media posts (same look on feed + single post).
class PostMediaCounter extends StatelessWidget {
  final int index;
  final int count;
  const PostMediaCounter({super.key, required this.index, required this.count});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(14)),
      child: Text(
        '${index + 1}/$count',
        style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w700),
      ),
    );
  }
}

/// Small round icon button laid over media (mute, fullscreen, ...).
class PostMediaIconButton extends StatelessWidget {
  final IconData icon;
  final VoidCallback onTap;
  const PostMediaIconButton({super.key, required this.icon, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(20),
      child: Container(
        padding: const EdgeInsets.all(7),
        decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(20)),
        child: Icon(icon, color: Colors.white, size: 18),
      ),
    );
  }
}

/// Placeholder shown while a video controller is still initialising
/// (thumbnail if the backend gave one, else a plain black frame + spinner).
class PostVideoLoading extends StatelessWidget {
  final String? thumbnail;
  const PostVideoLoading({super.key, this.thumbnail});

  @override
  Widget build(BuildContext context) {
    final t = thumbnail;
    return Stack(fit: StackFit.expand, children: [
      const ColoredBox(color: Colors.black),
      if (t != null && t.isNotEmpty)
        CachedNetworkImage(
          imageUrl: t,
          fit: BoxFit.contain,
          fadeInDuration: Duration.zero,
          errorWidget: (_, __, ___) => const SizedBox.shrink(),
        ),
      const Center(
        child: SizedBox(
          width: 28,
          height: 28,
          child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white70),
        ),
      ),
    ]);
  }
}

class _DocStyle {
  final Color color;
  final IconData icon;
  const _DocStyle(this.color, this.icon);
}

/// Document card (PDF / Word / Excel / PowerPoint / text / other) — replaces
/// the old embedded PDF viewer and bare "file icon + Open" tile.
class PostDocTile extends StatelessWidget {
  final String fileName;
  final String ext;
  final VoidCallback onOpen;
  final VoidCallback? onDownload;
  const PostDocTile({
    super.key,
    required this.fileName,
    required this.ext,
    required this.onOpen,
    this.onDownload,
  });

  _DocStyle get _style {
    switch (ext) {
      case 'pdf':
        return _DocStyle(const Color(0xFFE5484D), Icons.picture_as_pdf_rounded);
      case 'doc':
      case 'docx':
      case 'rtf':
        return _DocStyle(const Color(0xFF2B6CDE), Icons.description_rounded);
      case 'xls':
      case 'xlsx':
      case 'csv':
        return _DocStyle(const Color(0xFF1E9E5A), Icons.table_chart_rounded);
      case 'ppt':
      case 'pptx':
        return _DocStyle(const Color(0xFFE8792B), Icons.slideshow_rounded);
      case 'txt':
        return _DocStyle(const Color(0xFF6B7280), Icons.notes_rounded);
      case 'zip':
        return _DocStyle(const Color(0xFF8B5CF6), Icons.folder_zip_rounded);
      default:
        return _DocStyle(const Color(0xFF6B7280), Icons.insert_drive_file_rounded);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final s = _style;
    final label = ext.isEmpty ? 'FILE' : ext.toUpperCase();
    return GestureDetector(
      onTap: onOpen,
      behavior: HitTestBehavior.opaque,
      child: Container(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [s.color.withOpacity(0.14), cs.surface],
          ),
        ),
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 26),
        alignment: Alignment.center,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: cs.surface,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: cs.outlineVariant.withOpacity(0.7)),
              boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 12, offset: const Offset(0, 4))],
            ),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Row(children: [
                Container(
                  width: 56,
                  height: 56,
                  decoration: BoxDecoration(color: s.color.withOpacity(0.12), borderRadius: BorderRadius.circular(14)),
                  child: Icon(s.icon, color: s.color, size: 30),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                    Text(
                      fileName,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14.5, height: 1.25, color: cs.onSurface),
                    ),
                    const SizedBox(height: 6),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(color: s.color.withOpacity(0.12), borderRadius: BorderRadius.circular(6)),
                      child: Text(label, style: TextStyle(color: s.color, fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.4)),
                    ),
                  ]),
                ),
              ]),
              const SizedBox(height: 14),
              Row(children: [
                Expanded(
                  child: FilledButton.icon(
                    onPressed: onOpen,
                    icon: const Icon(Icons.open_in_new_rounded, size: 17),
                    label: Text(l10n.open),
                    style: FilledButton.styleFrom(
                      backgroundColor: cs.primary,
                      foregroundColor: cs.onPrimary,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                    ),
                  ),
                ),
                if (onDownload != null) ...[
                  const SizedBox(width: 10),
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: onDownload,
                      icon: const Icon(Icons.download_rounded, size: 17),
                      label: Text(l10n.download),
                      style: OutlinedButton.styleFrom(
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                      ),
                    ),
                  ),
                ],
              ]),
            ]),
          ),
        ),
      ),
    );
  }
}
