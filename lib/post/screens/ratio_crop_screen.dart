// lib/post/screens/ratio_crop_screen.dart
//
// 1.3-FE — ONE fixed-ratio crop screen shared by:
//   * new post  : Original / 1:1 / 4:5 / 16:9 ratio picker (new_post.dart)
//   * story     : 9:16 (home.dart -> _pickAndUploadStory)
//   * avatar    : 1:1 with a circle guide (profile/screens/edit_profile.dart)
//
// Pure Flutter + the `image` package already in pubspec — no cropper plugin
// (the old image_cropper was removed from this project on purpose).
//
// How it works: the photo sits under a fixed-ratio frame; pinch to zoom
// (1x..6x, the photo always covers the frame — no empty bars) and drag to
// reposition. On "Done" the visible region is converted to fractions of the
// source image ([computeCropRect]) and cut out in an isolate (EXIF
// orientation baked in, resized down to [outputWidth], JPEG q90), so a 12 MP
// photo never janks the UI thread. Cancel -> null (caller keeps the original).
import 'dart:io';
import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/foundation.dart' show compute;
import 'package:flutter/material.dart';
import 'package:image/image.dart' as img;
import 'package:path_provider/path_provider.dart';

import '../../l10n/app_localizations.dart';

/// Opens the crop screen. Returns the cropped JPEG, or null if cancelled.
///
/// [aspect] = width / height of the crop frame (1.0, 4/5, 16/9, 9/16 ...).
Future<File?> showRatioCrop(
  BuildContext context,
  File image, {
  required double aspect,
  required String title,
  bool circleGuide = false,
  int outputWidth = 1440,
}) {
  return Navigator.of(context).push<File>(
    MaterialPageRoute(
      fullscreenDialog: true,
      builder: (_) => RatioCropScreen(
        image: image,
        aspect: aspect,
        title: title,
        circleGuide: circleGuide,
        outputWidth: outputWidth,
      ),
    ),
  );
}

/// Visible region of the source image, as fractions (0..1) of its size.
class CropRect {
  final double left, top, width, height;
  const CropRect(this.left, this.top, this.width, this.height);

  @override
  String toString() => 'CropRect($left, $top, $width, $height)';
}

const double kCropMaxZoom = 6.0;

/// Scale at which the image exactly COVERS the frame (zoom == 1).
double cropBaseScale(double imageW, double imageH, double frameW, double frameH) =>
    math.max(frameW / imageW, frameH / imageH);

/// Keeps the photo covering the frame: the image edge may never move inside it.
Offset clampCropOffset({
  required double imageW,
  required double imageH,
  required double frameW,
  required double frameH,
  required double zoom,
  required Offset offset,
}) {
  final s = cropBaseScale(imageW, imageH, frameW, frameH) * zoom;
  final maxDx = math.max(0.0, (imageW * s - frameW) / 2);
  final maxDy = math.max(0.0, (imageH * s - frameH) / 2);
  return Offset(offset.dx.clamp(-maxDx, maxDx).toDouble(), offset.dy.clamp(-maxDy, maxDy).toDouble());
}

/// Pure maths: which part of the image is under the frame right now.
/// [offset] is the image-centre shift from the frame centre, in screen px.
CropRect computeCropRect({
  required double imageW,
  required double imageH,
  required double frameW,
  required double frameH,
  required double zoom,
  required Offset offset,
}) {
  final s = cropBaseScale(imageW, imageH, frameW, frameH) * zoom;
  final visW = frameW / s; // visible size in source pixels
  final visH = frameH / s;
  final x0 = (imageW * s / 2 - frameW / 2 - offset.dx) / s;
  final y0 = (imageH * s / 2 - frameH / 2 - offset.dy) / s;
  final w = (visW / imageW).clamp(0.0, 1.0).toDouble();
  final h = (visH / imageH).clamp(0.0, 1.0).toDouble();
  final left = (x0 / imageW).clamp(0.0, 1.0 - w).toDouble();
  final top = (y0 / imageH).clamp(0.0, 1.0 - h).toDouble();
  return CropRect(left, top, w, h);
}

class _CropJob {
  final String srcPath;
  final String outPath;
  final double left, top, width, height;
  final int outWidth;
  const _CropJob(this.srcPath, this.outPath, this.left, this.top, this.width, this.height, this.outWidth);
}

/// Runs in an isolate (via `compute`). Returns the written path, or null.
String? _cropIsolate(_CropJob job) {
  final decoded = img.decodeImage(File(job.srcPath).readAsBytesSync());
  if (decoded == null) return null;
  final oriented = img.bakeOrientation(decoded);
  final iw = oriented.width, ih = oriented.height;
  final x = (job.left * iw).round().clamp(0, iw - 1).toInt();
  final y = (job.top * ih).round().clamp(0, ih - 1).toInt();
  final w = (job.width * iw).round().clamp(1, iw - x).toInt();
  final h = (job.height * ih).round().clamp(1, ih - y).toInt();
  var out = img.copyCrop(oriented, x: x, y: y, width: w, height: h);
  if (out.width > job.outWidth) {
    out = img.copyResize(out, width: job.outWidth, interpolation: img.Interpolation.average);
  }
  File(job.outPath).writeAsBytesSync(img.encodeJpg(out, quality: 90));
  return job.outPath;
}

class RatioCropScreen extends StatefulWidget {
  final File image;
  final double aspect;
  final String title;
  final bool circleGuide;
  final int outputWidth;

  const RatioCropScreen({
    super.key,
    required this.image,
    required this.aspect,
    required this.title,
    this.circleGuide = false,
    this.outputWidth = 1440,
  });

  @override
  State<RatioCropScreen> createState() => _RatioCropScreenState();
}

class _RatioCropScreenState extends State<RatioCropScreen> {
  double? _imgW, _imgH;
  bool _loadFailed = false;
  bool _busy = false;

  double _zoom = 1;
  Offset _offset = Offset.zero;
  double _startZoom = 1;
  Offset _startOffset = Offset.zero;
  Offset _startFocal = Offset.zero;
  Size _frame = Size.zero;

  @override
  void initState() {
    super.initState();
    _loadSize();
  }

  Future<void> _loadSize() async {
    try {
      // Same decoder Image.file uses, so the size here is the ORIENTED size
      // the user sees (EXIF rotation applied).
      final codec = await ui.instantiateImageCodec(await widget.image.readAsBytes());
      final frame = await codec.getNextFrame();
      final w = frame.image.width.toDouble(), h = frame.image.height.toDouble();
      frame.image.dispose();
      codec.dispose();
      if (!mounted) return;
      setState(() {
        _imgW = w;
        _imgH = h;
      });
    } catch (_) {
      if (mounted) setState(() => _loadFailed = true);
    }
  }

  void _onScaleStart(ScaleStartDetails d) {
    _startZoom = _zoom;
    _startOffset = _offset;
    _startFocal = d.localFocalPoint;
  }

  void _onScaleUpdate(ScaleUpdateDetails d) {
    final iw = _imgW, ih = _imgH;
    if (iw == null || ih == null || _frame.isEmpty) return;
    final zoom = (_startZoom * d.scale).clamp(1.0, kCropMaxZoom).toDouble();
    final raw = _startOffset * (zoom / _startZoom) + (d.localFocalPoint - _startFocal);
    setState(() {
      _zoom = zoom;
      _offset = clampCropOffset(
        imageW: iw, imageH: ih, frameW: _frame.width, frameH: _frame.height, zoom: zoom, offset: raw,
      );
    });
  }

  void _reset() => setState(() {
        _zoom = 1;
        _offset = Offset.zero;
      });

  Future<void> _confirm() async {
    final iw = _imgW, ih = _imgH;
    if (_busy || iw == null || ih == null || _frame.isEmpty) return;
    setState(() => _busy = true);
    try {
      final rect = computeCropRect(
        imageW: iw, imageH: ih, frameW: _frame.width, frameH: _frame.height, zoom: _zoom, offset: _offset,
      );
      final dir = await getTemporaryDirectory();
      final outPath = '${dir.path}/crop_${DateTime.now().microsecondsSinceEpoch}.jpg';
      final path = await compute(
        _cropIsolate,
        _CropJob(widget.image.path, outPath, rect.left, rect.top, rect.width, rect.height, widget.outputWidth),
      );
      if (path == null) throw StateError('crop failed');
      if (!mounted) return;
      Navigator.pop(context, File(path));
    } catch (_) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.cropFailed)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final ready = _imgW != null && _imgH != null;
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.close_rounded),
          onPressed: _busy ? null : () => Navigator.pop(context),
        ),
        title: Text(widget.title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
        actions: [
          if (_busy)
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 16),
              child: Center(
                child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2.2, color: Colors.white)),
              ),
            )
          else
            TextButton(
              onPressed: ready ? _confirm : null,
              child: Text(l10n.doneLabel, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800)),
            ),
        ],
      ),
      body: SafeArea(
        child: Column(children: [
          Expanded(
            child: _loadFailed
                ? Center(child: Text(l10n.cropFailed, style: const TextStyle(color: Colors.white70)))
                : !ready
                    ? const Center(child: CircularProgressIndicator(color: Colors.white54))
                    : LayoutBuilder(builder: (context, cons) => _buildFrame(cons)),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
            child: Row(children: [
              Expanded(
                child: Text(l10n.cropHintDrag, style: const TextStyle(color: Colors.white60, fontSize: 12.5)),
              ),
              TextButton(
                onPressed: (_busy || !ready) ? null : _reset,
                child: Text(l10n.resetTooltip, style: const TextStyle(color: Colors.white)),
              ),
            ]),
          ),
        ]),
      ),
    );
  }

  Widget _buildFrame(BoxConstraints cons) {
    const pad = 16.0;
    final availW = math.max(1.0, cons.maxWidth - pad * 2);
    final availH = math.max(1.0, cons.maxHeight - pad * 2);
    var fw = availW;
    var fh = fw / widget.aspect;
    if (fh > availH) {
      fh = availH;
      fw = fh * widget.aspect;
    }
    _frame = Size(fw, fh);

    final iw = _imgW!, ih = _imgH!;
    final s = cropBaseScale(iw, ih, fw, fh) * _zoom;
    final dispW = iw * s, dispH = ih * s;
    // The frame size can change (rotation / keyboard); keep the photo covering it.
    final offset = clampCropOffset(imageW: iw, imageH: ih, frameW: fw, frameH: fh, zoom: _zoom, offset: _offset);
    final left = (fw - dispW) / 2 + offset.dx;
    final top = (fh - dispH) / 2 + offset.dy;

    return Center(
      child: GestureDetector(
        onScaleStart: _onScaleStart,
        onScaleUpdate: _onScaleUpdate,
        child: SizedBox(
          width: fw,
          height: fh,
          child: ClipRect(
            child: Stack(clipBehavior: Clip.hardEdge, children: [
              Positioned(
                left: left,
                top: top,
                width: dispW,
                height: dispH,
                child: Image.file(widget.image, fit: BoxFit.fill, filterQuality: FilterQuality.medium, gaplessPlayback: true),
              ),
              Positioned.fill(
                child: IgnorePointer(
                  child: CustomPaint(painter: widget.circleGuide ? _CircleGuidePainter() : _ThirdsGridPainter()),
                ),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}

class _ThirdsGridPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()
      ..color = const Color(0x55FFFFFF)
      ..strokeWidth = 1;
    for (var i = 1; i < 3; i++) {
      canvas.drawLine(Offset(size.width * i / 3, 0), Offset(size.width * i / 3, size.height), p);
      canvas.drawLine(Offset(0, size.height * i / 3), Offset(size.width, size.height * i / 3), p);
    }
    canvas.drawRect(
      Offset.zero & size,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5
        ..color = Colors.white70,
    );
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

/// Dims everything outside the circle the avatar will be shown in. The saved
/// file stays a plain square — the app/server render the circle.
class _CircleGuidePainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.shortestSide / 2;
    final outside = Path.combine(
      PathOperation.difference,
      Path()..addRect(Offset.zero & size),
      Path()..addOval(Rect.fromCircle(center: center, radius: radius)),
    );
    canvas.drawPath(outside, Paint()..color = const Color(0x99000000));
    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5
        ..color = Colors.white70,
    );
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
