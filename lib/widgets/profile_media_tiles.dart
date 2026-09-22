import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:video_player/video_player.dart';
import 'package:dio/dio.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:syncfusion_flutter_pdf/pdf.dart';

import '../profile/model.dart';
import '../l10n/app_localizations.dart';

// ============================================================
// Shared profile grid tiles — previously copy-pasted verbatim into both
// profile.dart (own profile) and target_profile.dart (someone else's
// profile). One copy now; a fix here reaches both screens instead of
// needing to be made twice (and inevitably drifting, like the two copies
// already had before this pass — target_profile.dart's version was still
// on hardcoded colors and no accessibility labels while profile.dart's
// had already moved on).
// ============================================================

class MediaGridTile extends StatelessWidget {
  final PostModel post;
  final AppLocalizations l10n;
  final VoidCallback onTap;
  const MediaGridTile({super.key, required this.post, required this.l10n, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final isVideo = post.postType == 'video';
    final caption = (post.title?.isNotEmpty == true) ? post.title! : post.content;
    return Semantics(
      button: true,
      label: caption.isNotEmpty ? caption : (isVideo ? l10n.videoPostLabel : l10n.photoPostLabel),
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: onTap,
        child: ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: Stack(fit: StackFit.expand, children: [
            Container(color: cs.surfaceVariant),
            isVideo
                ? VideoFirstFrame(videoUrl: post.firstImageUrl)
                : CachedNetworkImage(
                    imageUrl: post.firstImageUrl,
                    fit: BoxFit.cover,
                    memCacheWidth: 360,
                    placeholder: (c, u) => Container(color: cs.surfaceVariant),
                    errorWidget: (c, u, e) =>
                        Container(color: cs.surfaceVariant, child: Icon(Icons.broken_image_outlined, color: cs.onSurfaceVariant)),
                  ),
            if (isVideo)
              const Center(child: Icon(Icons.play_circle_fill_rounded, size: 34, color: Colors.white)),
          ]),
        ),
      ),
    );
  }
}

class VideoFirstFrame extends StatefulWidget {
  final String videoUrl;
  const VideoFirstFrame({super.key, required this.videoUrl});
  @override
  State<VideoFirstFrame> createState() => _VideoFirstFrameState();
}

class _VideoFirstFrameState extends State<VideoFirstFrame> {
  late VideoPlayerController _controller;
  bool _initialized = false;
  bool _hasError = false;

  @override
  void initState() {
    super.initState();
    _initializeVideo();
  }

  Future<void> _initializeVideo() async {
    try {
      _controller = VideoPlayerController.networkUrl(
        Uri.parse(widget.videoUrl),
        httpHeaders: {'User-Agent': 'Mozilla/5.0'},
      );
      await _controller.initialize();
      await _controller.seekTo(Duration.zero);
      await _controller.setVolume(0.0);
      await _controller.pause();
      if (mounted) setState(() => _initialized = true);
    } catch (e) {
      if (mounted) setState(() => _hasError = true);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    if (_hasError) {
      return Container(color: cs.surfaceVariant, child: Icon(Icons.videocam_off_outlined, color: cs.onSurfaceVariant));
    }
    if (!_initialized) {
      return Container(
        color: cs.surfaceVariant,
        child: const Center(child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))),
      );
    }
    return FittedBox(
      fit: BoxFit.cover,
      child: SizedBox(
        width: _controller.value.size.width,
        height: _controller.value.size.height,
        child: VideoPlayer(_controller),
      ),
    );
  }
}

class DocumentGridTile extends StatefulWidget {
  final PostModel doc;
  final PostMediaModel file;
  final AppLocalizations l10n;
  final VoidCallback onDownload;
  const DocumentGridTile({
    super.key,
    required this.doc,
    required this.file,
    required this.l10n,
    required this.onDownload,
  });
  @override
  State<DocumentGridTile> createState() => _DocumentGridTileState();
}

class _DocumentGridTileState extends State<DocumentGridTile> {
  int _totalPages = 0;
  bool _isLoadingPages = true;

  @override
  void initState() {
    super.initState();
    if (widget.file.file.toLowerCase().endsWith('.pdf')) {
      _getPdfPages();
    } else {
      setState(() => _isLoadingPages = false);
    }
  }

  Future<void> _getPdfPages() async {
    try {
      final response = await Dio().get(widget.file.file, options: Options(responseType: ResponseType.bytes));
      final PdfDocument document = PdfDocument(inputBytes: response.data);
      if (mounted) {
        setState(() {
          _totalPages = document.pages.count;
          _isLoadingPages = false;
        });
      }
      document.dispose();
    } catch (e) {
      if (mounted) setState(() => _isLoadingPages = false);
    }
  }

  IconData _getFileIcon() {
    final ext = widget.file.file.toLowerCase();
    if (ext.endsWith('.pdf')) return Icons.picture_as_pdf;
    if (ext.endsWith('.xls') || ext.endsWith('.xlsx')) return Icons.table_chart;
    if (ext.endsWith('.doc') || ext.endsWith('.docx')) return Icons.description;
    return Icons.insert_drive_file;
  }

  // File-type colors kept as fixed literals on purpose — this is a
  // universal convention (red=PDF, green=spreadsheet, blue=doc), not a
  // brand/theme color, so it stays legible in both light and dark mode
  // without needing a ColorScheme slot of its own.
  Color _getFileColor() {
    final ext = widget.file.file.toLowerCase();
    if (ext.endsWith('.pdf')) return Colors.red.shade400;
    if (ext.endsWith('.xls') || ext.endsWith('.xlsx')) return Colors.green.shade400;
    if (ext.endsWith('.doc') || ext.endsWith('.docx')) return Colors.blue.shade400;
    return Colors.grey;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final isPdf = widget.file.file.toLowerCase().endsWith('.pdf');
    final title = widget.doc.title ?? widget.file.fileName;
    return Column(
      children: [
        Expanded(
          child: Container(
            decoration: BoxDecoration(
              color: cs.surfaceVariant,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: cs.outlineVariant),
            ),
            clipBehavior: Clip.antiAlias,
            child: Stack(
              fit: StackFit.expand,
              children: [
                if (isPdf)
                  SfPdfViewer.network(
                    widget.file.file,
                    canShowScrollHead: false,
                    canShowPaginationDialog: false,
                    canShowScrollStatus: false,
                    enableDoubleTapZooming: false,
                    pageLayoutMode: PdfPageLayoutMode.single,
                  )
                else
                  Center(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(_getFileIcon(), size: 44, color: _getFileColor()),
                        const SizedBox(height: 4),
                        Text(widget.file.file.split('.').last.toUpperCase(),
                            style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: _getFileColor())),
                      ],
                    ),
                  ),
                Positioned(
                  top: 4,
                  right: 4,
                  child: Semantics(
                    button: true,
                    label: widget.l10n.download,
                    child: Tooltip(
                      message: widget.l10n.download,
                      child: InkWell(
                        onTap: widget.onDownload,
                        borderRadius: BorderRadius.circular(6),
                        child: Container(
                          padding: const EdgeInsets.all(4),
                          decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(6)),
                          child: const Icon(Icons.download_rounded, size: 15, color: Colors.white),
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 4),
        Text(title, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 11, color: cs.onSurface)),
        if (isPdf)
          Text(
            _isLoadingPages ? widget.l10n.loadingEllipsis : widget.l10n.pdfPagesCount(_totalPages),
            style: TextStyle(fontSize: 9, color: cs.onSurfaceVariant),
          )
        else
          Text(
            widget.l10n.fileSizeKb((widget.file.fileSizeBytes) ~/ 1024),
            style: TextStyle(fontSize: 9, color: cs.onSurfaceVariant),
          ),
      ],
    );
  }
}
