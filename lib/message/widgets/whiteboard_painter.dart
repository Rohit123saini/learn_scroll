import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../models/study_room_models.dart';

class WhiteboardPainter extends CustomPainter {
  final List<List<DrawingPoint>> strokes;
  final List<ShapeElement> shapes;
  // 🔥 NAYA — jab tak user shape ko drag kar raha hai (finalize hone se
  // pehle), yahi live-preview banata hai — halka transparent taaki pata
  // chale ye abhi "confirm" nahi hua hai.
  final ShapeElement? previewShape;

  WhiteboardPainter({
    required this.strokes,
    this.shapes = const [],
    this.previewShape,
  });

  @override
  void paint(Canvas canvas, Size size) {
    // ---- Freehand strokes (marker / paint / highlighter / eraser) ----
    for (final stroke in strokes) {
      if (stroke.isEmpty) continue;
      for (int i = 0; i < stroke.length - 1; i++) {
        final p1 = stroke[i];
        final p2 = stroke[i + 1];

        if (p1.toolType == ToolType.eraser) {
          final eraserPaint = Paint()
            ..color = Colors.white
            ..strokeWidth = p1.paint.strokeWidth
            ..strokeCap = StrokeCap.round;
          canvas.drawLine(p1.offset, p2.offset, eraserPaint);
        } else if (p1.toolType == ToolType.highlighter) {
          // 🔥 NAYA — highlighter: semi-transparent aur mota, taaki neeche
          // ka text/drawing dikhta rahe (asli highlighter jaisa).
          final hlPaint = Paint()
            ..color = p1.paint.color.withOpacity(0.35)
            ..strokeWidth = p1.paint.strokeWidth * 2.2
            ..strokeCap = StrokeCap.square;
          canvas.drawLine(p1.offset, p2.offset, hlPaint);
        } else {
          canvas.drawLine(p1.offset, p2.offset, p1.paint);
        }
      }
    }

    // ---- Finalized shapes ----
    for (final shape in shapes) {
      _drawShape(canvas, shape);
    }

    // ---- Live preview while dragging a shape ----
    if (previewShape != null) {
      _drawShape(canvas, previewShape!, isPreview: true);
    }
  }

  void _drawShape(Canvas canvas, ShapeElement shape, {bool isPreview = false}) {
    final paint = Paint()
      ..color = isPreview ? shape.color.withOpacity(0.6) : shape.color
      ..strokeWidth = shape.strokeWidth
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    switch (shape.tool) {
      case ToolType.rectangle:
        canvas.drawRect(Rect.fromPoints(shape.start, shape.end), paint);
        break;
      case ToolType.circle:
        canvas.drawOval(Rect.fromPoints(shape.start, shape.end), paint);
        break;
      case ToolType.line:
        canvas.drawLine(shape.start, shape.end, paint);
        break;
      case ToolType.arrowLine:
        canvas.drawLine(shape.start, shape.end, paint);
        _drawArrowHead(canvas, shape.start, shape.end, paint.color);
        break;
      default:
        break;
    }
  }

  void _drawArrowHead(Canvas canvas, Offset start, Offset end, Color color) {
    const arrowLength = 14.0;
    const arrowAngle = 0.45; // radians
    final theta = math.atan2(end.dy - start.dy, end.dx - start.dx);

    final p1 = Offset(
      end.dx - arrowLength * math.cos(theta - arrowAngle),
      end.dy - arrowLength * math.sin(theta - arrowAngle),
    );
    final p2 = Offset(
      end.dx - arrowLength * math.cos(theta + arrowAngle),
      end.dy - arrowLength * math.sin(theta + arrowAngle),
    );

    final headPaint = Paint()
      ..color = color
      ..style = PaintingStyle.fill;
    final path = Path()
      ..moveTo(end.dx, end.dy)
      ..lineTo(p1.dx, p1.dy)
      ..lineTo(p2.dx, p2.dy)
      ..close();
    canvas.drawPath(path, headPaint);
  }

  @override
  bool shouldRepaint(covariant WhiteboardPainter oldDelegate) => true;
}

