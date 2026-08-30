part of 'media_edit_screen.dart';

// Instagram-style rule-of-thirds crop grid — three equal columns/rows
// drawn as thin translucent white lines over the viewport. Purely a
// visual guide (screen-fixed, drawn as a sibling of the pinched/
// rotated content, never baked into the export) that fades in only
// while a reframe gesture is in progress, same as tapping-and-holding
// to pinch-crop in Instagram's own editor.
class _ReframeGridPainter extends CustomPainter {
  const _ReframeGridPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = Colors.white.withOpacity(0.7)
      ..strokeWidth = 1;
    final thirdW = size.width / 3;
    final thirdH = size.height / 3;
    for (int i = 1; i <= 2; i++) {
      canvas.drawLine(Offset(thirdW * i, 0), Offset(thirdW * i, size.height), paint);
      canvas.drawLine(Offset(0, thirdH * i), Offset(size.width, thirdH * i), paint);
    }
  }

  @override
  bool shouldRepaint(covariant _ReframeGridPainter oldDelegate) => false;
}

// ═══════════════════════════════════════════════════════════════════
// Animated FX overlay — CustomPainter for the live looping preview.
// Draws the exact same shapes as the bake step below (via
// _drawFxEffect) so what you see is what gets exported.
// ═══════════════════════════════════════════════════════════════════

// Shared draw routine for one freehand stroke — used by the live
// CustomPainter preview (_DrawPainter) and the export bake step
// (_composeOverlays), so what you draw is exactly what gets exported.
void _paintDrawStrokeOnCanvas(Canvas canvas, _DrawStroke stroke, double w, double h, {double opacity = 1.0}) {
  if (stroke.points.length < 2) return;
  final path = Path()..moveTo(stroke.points.first.dx * w, stroke.points.first.dy * h);
  for (final p in stroke.points.skip(1)) {
    path.lineTo(p.dx * w, p.dy * h);
  }
  final paint = Paint()
    ..color = stroke.color.withOpacity(stroke.color.opacity * opacity)
    ..strokeWidth = stroke.width * (w / 1000)
    ..strokeCap = StrokeCap.round
    ..strokeJoin = StrokeJoin.round
    ..style = PaintingStyle.stroke;
  canvas.drawPath(path, paint);
}

// Live doodle preview — draws every committed + in-progress stroke.
// Repaints on every setState while drawing since strokes mutate in
// place; cheap enough for typical doodle stroke counts.
class _DrawPainter extends CustomPainter {
  const _DrawPainter(this.strokes, {this.opacity = 1.0});
  final List<_DrawStroke> strokes;
  final double opacity; // used to render the Magic Eraser mask as a translucent overlay

  @override
  void paint(Canvas canvas, Size size) {
    for (final stroke in strokes) {
      _paintDrawStrokeOnCanvas(canvas, stroke, size.width, size.height, opacity: opacity);
    }
  }

  @override
  bool shouldRepaint(covariant _DrawPainter oldDelegate) => true;
}

class _FxPainter extends CustomPainter {
  const _FxPainter(this.kind, this.t);
  final _FxKind kind;
  final double t; // 0..1 loop phase

  @override
  void paint(Canvas canvas, Size size) => _drawFxEffect(canvas, size, kind, t);

  @override
  bool shouldRepaint(covariant _FxPainter oldDelegate) => oldDelegate.t != t || oldDelegate.kind != kind;
}

// Shared draw routine: same logic used for the live animated preview
// (every tick) and for baking a single representative frame into a
// still-image export. `t` is a 0..1 loop phase.
void _drawFxEffect(Canvas canvas, Size size, _FxKind kind, double t) {
  if (kind == _FxKind.none) return;
  final w = size.width;
  final h = size.height;
  final angle = t * 2 * math.pi;

  switch (kind) {
    case _FxKind.none:
      break;

    case _FxKind.colorShift:
      // Slowly rotating hue wash, blended additively so it tints
      // without flattening the image underneath.
      final hue = (math.sin(angle) * 0.5 + 0.5) * 360;
      final color = HSVColor.fromAHSV(0.28, hue, 0.85, 1).toColor();
      final paint = Paint()
        ..shader = ui.Gradient.linear(
          Offset.zero,
          Offset(w, h),
          [color, color.withOpacity(0.05)],
        )
        ..blendMode = BlendMode.plus;
      canvas.drawRect(Rect.fromLTWH(0, 0, w, h), paint);
      break;

    case _FxKind.glitch:
      // A handful of horizontally-offset, color-fringed slices that
      // jitter with t — classic RGB-split glitch look.
      final rnd = math.Random(1234);
      final sliceCount = 6;
      for (int i = 0; i < sliceCount; i++) {
        final phase = angle * (1 + i * 0.4) + rnd.nextDouble() * 6.28;
        final sliceY = (i / sliceCount) * h + (math.sin(phase) * 0.5 + 0.5) * (h / sliceCount);
        final sliceH = h / (sliceCount * 2.2);
        final dx = math.sin(phase * 2) * w * 0.03;
        for (final c in [Colors.redAccent, Colors.cyanAccent, Colors.transparent]) {
          if (c == Colors.transparent) continue;
          final paint = Paint()
            ..color = c.withOpacity(0.16)
            ..blendMode = BlendMode.plus;
          canvas.drawRect(Rect.fromLTWH(dx, sliceY, w, sliceH), paint);
        }
      }
      break;

    case _FxKind.sparkle:
      // Twinkling dots at fixed pseudo-random spots, each with its
      // own blink phase so they don't flash in unison.
      final rnd = math.Random(42);
      const dotCount = 22;
      for (int i = 0; i < dotCount; i++) {
        final dotX = rnd.nextDouble() * w;
        final dotY = rnd.nextDouble() * h;
        final phase = rnd.nextDouble() * 2 * math.pi;
        final blink = (math.sin(angle * 2 + phase) * 0.5 + 0.5);
        if (blink < 0.55) continue; // most dots stay invisible most of the time
        final radius = 1.2 + blink * 2.2;
        final paint = Paint()
          ..color = Colors.white.withOpacity((blink - 0.55) / 0.45 * 0.9)
          ..blendMode = BlendMode.plus;
        canvas.drawCircle(Offset(dotX, dotY), radius, paint);
      }
      break;

    case _FxKind.filmGrain:
      // Dense monochrome static (grain) redrawn each frame from a
      // seed tied to the loop phase, plus faint horizontal scanlines
      // and a slow vignette-darkening pulse — classic VHS/vintage
      // film texture.
      final rnd = math.Random((t * 997).floor());
      const grainCount = 260;
      for (int i = 0; i < grainCount; i++) {
        final gx = rnd.nextDouble() * w;
        final gy = rnd.nextDouble() * h;
        final shade = rnd.nextDouble();
        final paint = Paint()
          ..color = (shade > 0.5 ? Colors.white : Colors.black).withOpacity(0.06 + shade * 0.05)
          ..blendMode = BlendMode.plus;
        canvas.drawCircle(Offset(gx, gy), 0.6 + rnd.nextDouble() * 0.8, paint);
      }
      final scanlinePaint = Paint()
        ..color = Colors.black.withOpacity(0.10)
        ..strokeWidth = 1;
      for (double y = 0; y < h; y += 3) {
        canvas.drawLine(Offset(0, y), Offset(w, y), scanlinePaint);
      }
      final flicker = (math.sin(angle * 9) * 0.5 + 0.5) * 0.05;
      canvas.drawRect(Rect.fromLTWH(0, 0, w, h), Paint()..color = Colors.black.withOpacity(flicker));
      break;

    case _FxKind.lightLeak:
      // A warm radial glow that drifts diagonally across the frame on
      // a slow breathing loop, blended additively so it reads as a
      // light source washing over the image rather than a flat tint.
      final driftX = (math.sin(angle) * 0.5 + 0.5) * w * 1.3 - w * 0.15;
      final driftY = (math.cos(angle * 0.7) * 0.5 + 0.5) * h * 0.6 - h * 0.1;
      final breathe = math.sin(angle * 1.3) * 0.5 + 0.5;
      final leakPaint = Paint()
        ..shader = ui.Gradient.radial(
          Offset(driftX, driftY),
          w * 0.55,
          [
            const Color(0xFFFFF0B3).withOpacity(0.35 + breathe * 0.15),
            const Color(0xFFFF8A65).withOpacity(0.12),
            Colors.transparent,
          ],
          [0.0, 0.5, 1.0],
        )
        ..blendMode = BlendMode.plus;
      canvas.drawRect(Rect.fromLTWH(0, 0, w, h), leakPaint);
      break;

    case _FxKind.rainbow:
      // A saturated rainbow band sweeping diagonally across the frame
      // on a loop, blended additively so the base image stays visible
      // underneath it.
      final shift = (math.sin(angle) * 0.5 + 0.5) * (w + h) * 1.4 - h * 0.7;
      const rainbowColors = [
        Colors.red,
        Colors.orange,
        Colors.yellow,
        Colors.green,
        Colors.cyan,
        Colors.blue,
        Colors.purple,
      ];
      final rainbowPaint = Paint()
        ..shader = ui.Gradient.linear(
          Offset(shift - h * 0.5, 0),
          Offset(shift + h * 0.5, h),
          [for (final c in rainbowColors) c.withOpacity(0.20)],
        )
        ..blendMode = BlendMode.plus;
      canvas.drawRect(Rect.fromLTWH(0, 0, w, h), rainbowPaint);
      break;

    case _FxKind.snow:
      // Falling flakes at fixed pseudo-random horizontal spots, each
      // looping vertically at its own speed with a little side-to-side
      // sway so it doesn't read as a rigid grid.
      final rnd = math.Random(7);
      const flakeCount = 44;
      for (int i = 0; i < flakeCount; i++) {
        final seedX = rnd.nextDouble();
        final seedPhase = rnd.nextDouble();
        final speed = 0.6 + rnd.nextDouble() * 0.7;
        final flakeSize = 1.4 + rnd.nextDouble() * 2.6;
        final fy = ((t * speed + seedPhase) % 1.0) * h;
        final sway = math.sin(angle * speed * 2 + seedPhase * 10) * 10;
        final fx = (seedX * w + sway).clamp(0.0, w);
        final paint = Paint()
          ..color = Colors.white.withOpacity(0.5)
          ..blendMode = BlendMode.plus;
        canvas.drawCircle(Offset(fx, fy), flakeSize, paint);
      }
      break;

    case _FxKind.vignettePulse:
      // A radial dark edge that breathes in and out — center stays
      // clear, corners darken/lighten on a slow sine cycle.
      final pulse = math.sin(angle * 1.2) * 0.5 + 0.5;
      final maxRadius = math.sqrt(w * w + h * h) / 2;
      final vignettePaint = Paint()
        ..shader = ui.Gradient.radial(
          Offset(w / 2, h / 2),
          maxRadius,
          [Colors.transparent, Colors.black.withOpacity(0.15 + pulse * 0.35)],
          const [0.55, 1.0],
        );
      canvas.drawRect(Rect.fromLTWH(0, 0, w, h), vignettePaint);
      break;

    case _FxKind.bokeh:
      // Soft, blurred, drifting light orbs in a few pastel hues —
      // classic "bokeh" glow effect, each orb on its own slow orbit.
      final rnd = math.Random(99);
      const orbCount = 8;
      const orbColors = [
        Color(0xFFFFD1DC),
        Color(0xFFB5EAEA),
        Color(0xFFFFF6BD),
        Color(0xFFC1FBA4),
      ];
      for (int i = 0; i < orbCount; i++) {
        final baseX = rnd.nextDouble() * w;
        final baseY = rnd.nextDouble() * h;
        final phase = rnd.nextDouble() * 2 * math.pi;
        final speed = 0.4 + rnd.nextDouble() * 0.5;
        final driftX = math.sin(angle * speed + phase) * w * 0.06;
        final driftY = math.cos(angle * speed * 0.8 + phase) * h * 0.06;
        final orbRadius = 18 + rnd.nextDouble() * 26;
        final pulse = math.sin(angle * 1.5 + phase) * 0.5 + 0.5;
        final paint = Paint()
          ..color = orbColors[i % orbColors.length].withOpacity(0.16 + pulse * 0.12)
          ..blendMode = BlendMode.plus
          ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 18);
        canvas.drawCircle(Offset(baseX + driftX, baseY + driftY), orbRadius, paint);
      }
      break;

    case _FxKind.confetti:
      // Small rotating rectangles falling and swaying like paper
      // confetti, each with its own color/speed/spin.
      final rnd = math.Random(21);
      const confettiCount = 34;
      const confettiColors = [
        Colors.pinkAccent,
        Colors.amberAccent,
        Colors.lightGreenAccent,
        Colors.lightBlueAccent,
        Colors.deepPurpleAccent,
      ];
      for (int i = 0; i < confettiCount; i++) {
        final seedX = rnd.nextDouble();
        final seedPhase = rnd.nextDouble();
        final speed = 0.5 + rnd.nextDouble() * 0.6;
        final pieceSize = 4.0 + rnd.nextDouble() * 4.0;
        final fy = ((t * speed + seedPhase) % 1.0) * h;
        final sway = math.sin(angle * speed * 1.5 + seedPhase * 10) * 14;
        final fx = (seedX * w + sway).clamp(0.0, w);
        final rot = angle * (1 + seedPhase * 3);
        final color = confettiColors[i % confettiColors.length];
        canvas.save();
        canvas.translate(fx, fy);
        canvas.rotate(rot);
        canvas.drawRect(
          Rect.fromCenter(center: Offset.zero, width: pieceSize, height: pieceSize * 1.6),
          Paint()..color = color.withOpacity(0.85),
        );
        canvas.restore();
      }
      break;

    case _FxKind.embers:
      // Warm glowing particles that rise from the bottom and fade,
      // flickering between orange and gold — campfire-spark look.
      final rnd = math.Random(55);
      const emberCount = 26;
      for (int i = 0; i < emberCount; i++) {
        final seedX = rnd.nextDouble();
        final seedPhase = rnd.nextDouble();
        final speed = 0.4 + rnd.nextDouble() * 0.5;
        final fy = h - ((t * speed + seedPhase) % 1.0) * h;
        final sway = math.sin(angle * speed * 2 + seedPhase * 10) * 12;
        final fx = (seedX * w + sway).clamp(0.0, w);
        final flicker = math.sin(angle * 4 + seedPhase * 6) * 0.5 + 0.5;
        final paint = Paint()
          ..color = Color.lerp(const Color(0xFFFF7A18), const Color(0xFFFFD36E), flicker)!
              .withOpacity(0.55 + flicker * 0.3)
          ..blendMode = BlendMode.plus
          ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3);
        canvas.drawCircle(Offset(fx, fy), 1.5 + flicker * 2.0, paint);
      }
      break;

    case _FxKind.neonPulse:
      // A glowing color-cycling border that breathes in thickness and
      // brightness around the frame edge.
      final hue = (math.sin(angle) * 0.5 + 0.5) * 360;
      final glowColor = HSVColor.fromAHSV(1, hue, 0.9, 1).toColor();
      final neonPulse = math.sin(angle * 2) * 0.5 + 0.5;
      final borderPaint = Paint()
        ..color = glowColor.withOpacity(0.5 + neonPulse * 0.3)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 10 + neonPulse * 6
        ..maskFilter = MaskFilter.blur(BlurStyle.normal, 14 + neonPulse * 6)
        ..blendMode = BlendMode.plus;
      canvas.drawRect(Rect.fromLTWH(4, 4, w - 8, h - 8), borderPaint);
      break;

    case _FxKind.rain:
      // Fast, short diagonal streaks falling top-to-bottom — a
      // lightweight rain-shower look.
      final rnd = math.Random(13);
      const dropCount = 60;
      for (int i = 0; i < dropCount; i++) {
        final seedX = rnd.nextDouble();
        final seedPhase = rnd.nextDouble();
        final speed = 1.2 + rnd.nextDouble() * 0.8;
        final dropLength = 14.0 + rnd.nextDouble() * 10;
        final fy = ((t * speed + seedPhase) % 1.0) * (h + dropLength) - dropLength;
        final fx = seedX * w;
        final paint = Paint()
          ..color = Colors.lightBlueAccent.withOpacity(0.35)
          ..strokeWidth = 1.4
          ..blendMode = BlendMode.plus;
        canvas.drawLine(Offset(fx, fy), Offset(fx - 4, fy + dropLength), paint);
      }
      break;

    case _FxKind.lensFlare:
      // A bright specular highlight drifting across the frame, with a
      // few smaller secondary flares strung along the line back to
      // center — classic anamorphic-lens-flare look.
      final flarePos = Offset(
        (math.sin(angle) * 0.5 + 0.5) * w,
        (math.cos(angle * 0.6) * 0.3 + 0.35) * h,
      );
      final flareCenter = Offset(w / 2, h / 2);
      final mainFlarePaint = Paint()
        ..shader = ui.Gradient.radial(
          flarePos,
          w * 0.18,
          [Colors.white.withOpacity(0.8), Colors.white.withOpacity(0.0)],
        )
        ..blendMode = BlendMode.plus;
      canvas.drawCircle(flarePos, w * 0.18, mainFlarePaint);
      final dir = flareCenter - flarePos;
      for (final f in const [0.3, 0.55, 0.8]) {
        final pos = flarePos + dir * f;
        final r = 8.0 + 14 * f;
        final paint = Paint()
          ..color = Colors.amberAccent.withOpacity(0.25 * (1 - f))
          ..blendMode = BlendMode.plus
          ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 6);
        canvas.drawCircle(pos, r, paint);
      }
      break;

    case _FxKind.fireworks:
      // A handful of bursts, each on its own repeating cycle: an
      // expanding ring of sparks that fades out before the next burst
      // at that slot begins.
      final fwRnd = math.Random(303);
      const burstCount = 4;
      const burstColors = [Colors.redAccent, Colors.cyanAccent, Colors.amberAccent, Colors.purpleAccent, Colors.greenAccent];
      for (int b = 0; b < burstCount; b++) {
        final burstPhase = fwRnd.nextDouble();
        final cx = (fwRnd.nextDouble() * 0.7 + 0.15) * w;
        final cy = (fwRnd.nextDouble() * 0.5 + 0.1) * h;
        final localT = (t * 0.8 + burstPhase) % 1.0;
        if (localT > 0.5) continue;
        final progress = localT / 0.5;
        final burstRadius = progress * (w * 0.12);
        final opacity = (1 - progress).clamp(0.0, 1.0);
        final color = burstColors[b % burstColors.length];
        const sparks = 14;
        for (int s = 0; s < sparks; s++) {
          final sparkAngle = (s / sparks) * 2 * math.pi;
          final px = cx + math.cos(sparkAngle) * burstRadius;
          final py = cy + math.sin(sparkAngle) * burstRadius;
          canvas.drawCircle(
            Offset(px, py),
            2.2,
            Paint()
              ..color = color.withOpacity(opacity * 0.85)
              ..blendMode = BlendMode.plus,
          );
        }
      }
      break;

    case _FxKind.bubbles:
      // Translucent rising circles with a small highlight dot, drift
      // and sway upward and loop from the bottom once off-screen.
      final bubbleRnd = math.Random(88);
      const bubbleCount = 18;
      for (int i = 0; i < bubbleCount; i++) {
        final seedX = bubbleRnd.nextDouble();
        final seedPhase = bubbleRnd.nextDouble();
        final speed = 0.3 + bubbleRnd.nextDouble() * 0.4;
        final radius = 4.0 + bubbleRnd.nextDouble() * 10.0;
        final fy = h - ((t * speed + seedPhase) % 1.0) * h;
        final sway = math.sin(angle * speed * 1.5 + seedPhase * 8) * 10;
        final fx = (seedX * w + sway).clamp(0.0, w);
        canvas.drawCircle(
          Offset(fx, fy),
          radius,
          Paint()
            ..color = Colors.white.withOpacity(0.18)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.4
            ..blendMode = BlendMode.plus,
        );
        canvas.drawCircle(
          Offset(fx - radius * 0.35, fy - radius * 0.35),
          radius * 0.2,
          Paint()
            ..color = Colors.white.withOpacity(0.35)
            ..blendMode = BlendMode.plus,
        );
      }
      break;

    case _FxKind.hearts:
      // Small pink heart shapes drifting upward and swaying, fading
      // slightly the higher they rise.
      final heartRnd = math.Random(45);
      const heartCount = 14;
      for (int i = 0; i < heartCount; i++) {
        final seedX = heartRnd.nextDouble();
        final seedPhase = heartRnd.nextDouble();
        final speed = 0.3 + heartRnd.nextDouble() * 0.4;
        final heartSize = 8.0 + heartRnd.nextDouble() * 8.0;
        final fy = h - ((t * speed + seedPhase) % 1.0) * h;
        final sway = math.sin(angle * speed * 1.5 + seedPhase * 8) * 12;
        final fx = (seedX * w + sway).clamp(0.0, w);
        final heartOpacity = (0.7 - ((h - fy) / h) * 0.3).clamp(0.15, 0.7);
        canvas.save();
        canvas.translate(fx, fy);
        canvas.scale(heartSize / 20);
        final heartPath = Path()
          ..moveTo(0, 6)
          ..cubicTo(-10, -4, -10, -14, 0, -8)
          ..cubicTo(10, -14, 10, -4, 0, 6)
          ..close();
        canvas.drawPath(
          heartPath,
          Paint()
            ..color = Colors.pinkAccent.withOpacity(heartOpacity)
            ..blendMode = BlendMode.plus,
        );
        canvas.restore();
      }
      break;

    case _FxKind.fireflies:
      // Glowing dots wandering in small loose orbits, each blinking
      // on its own phase.
      final flyRnd = math.Random(71);
      const fireflyCount = 16;
      for (int i = 0; i < fireflyCount; i++) {
        final baseX = flyRnd.nextDouble() * w;
        final baseY = flyRnd.nextDouble() * h;
        final phase = flyRnd.nextDouble() * 2 * math.pi;
        final speed = 0.3 + flyRnd.nextDouble() * 0.4;
        final wanderX = math.sin(angle * speed + phase) * w * 0.08;
        final wanderY = math.cos(angle * speed * 1.3 + phase) * h * 0.08;
        final blink = math.sin(angle * 3 + phase * 2) * 0.5 + 0.5;
        canvas.drawCircle(
          Offset(baseX + wanderX, baseY + wanderY),
          2.0 + blink * 1.6,
          Paint()
            ..color = const Color(0xFFCFFF6B).withOpacity(0.2 + blink * 0.55)
            ..blendMode = BlendMode.plus
            ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3),
        );
      }
      break;

    case _FxKind.aurora:
      // A few layered, softly blurred wavy ribbons drifting across
      // the upper frame — aurora-borealis mood.
      const auroraColors = [Color(0xFF6EF2C1), Color(0xFF7AA6FF), Color(0xFFB388FF)];
      for (int i = 0; i < auroraColors.length; i++) {
        final baseY = h * (0.15 + i * 0.12);
        final amp = h * 0.08;
        final phase = angle * (0.6 + i * 0.2) + i * 2.0;
        final auroraPath = Path()..moveTo(0, baseY);
        for (double x = 0; x <= w; x += w / 40) {
          final y = baseY + math.sin((x / w) * 3 * math.pi + phase) * amp;
          auroraPath.lineTo(x, y);
        }
        auroraPath.lineTo(w, baseY + amp * 3);
        auroraPath.lineTo(0, baseY + amp * 3);
        auroraPath.close();
        canvas.drawPath(
          auroraPath,
          Paint()
            ..color = auroraColors[i].withOpacity(0.14)
            ..blendMode = BlendMode.plus
            ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 18),
        );
      }
      break;

    case _FxKind.petals:
      // Small falling/swaying petal ovals in pink/white tones,
      // rotating gently as they drift down.
      final petalRnd = math.Random(64);
      const petalCount = 24;
      const petalColors = [Color(0xFFFFAFCC), Color(0xFFFFC8DD), Colors.white];
      for (int i = 0; i < petalCount; i++) {
        final seedX = petalRnd.nextDouble();
        final seedPhase = petalRnd.nextDouble();
        final speed = 0.35 + petalRnd.nextDouble() * 0.45;
        final petalSize = 5.0 + petalRnd.nextDouble() * 5.0;
        final fy = ((t * speed + seedPhase) % 1.0) * h;
        final sway = math.sin(angle * speed * 1.2 + seedPhase * 10) * 16;
        final fx = (seedX * w + sway).clamp(0.0, w);
        final rot = angle * (1 + seedPhase * 2);
        canvas.save();
        canvas.translate(fx, fy);
        canvas.rotate(rot);
        canvas.drawOval(
          Rect.fromCenter(center: Offset.zero, width: petalSize, height: petalSize * 1.8),
          Paint()
            ..color = petalColors[i % petalColors.length].withOpacity(0.6)
            ..blendMode = BlendMode.plus,
        );
        canvas.restore();
      }
      break;

    case _FxKind.staticNoise:
      // Sparse blocky flicker (distinct from the fine, dense grain of
      // Film Grain) plus a rolling horizontal glitch bar — old-TV look.
      final staticRnd = math.Random((t * 733).floor());
      const blockCols = 26;
      const blockRows = 16;
      final blockW = w / blockCols;
      final blockH = h / blockRows;
      for (int r = 0; r < blockRows; r++) {
        for (int c = 0; c < blockCols; c++) {
          if (staticRnd.nextDouble() > 0.14) continue;
          final shade = staticRnd.nextDouble();
          canvas.drawRect(
            Rect.fromLTWH(c * blockW, r * blockH, blockW, blockH),
            Paint()
              ..color = Colors.white.withOpacity(0.08 + shade * 0.1)
              ..blendMode = BlendMode.plus,
          );
        }
      }
      final barY = ((t * 1.7) % 1.0) * h;
      canvas.drawRect(
        Rect.fromLTWH(0, barY, w, 6),
        Paint()
          ..color = Colors.white.withOpacity(0.12)
          ..blendMode = BlendMode.plus,
      );
      break;

    case _FxKind.frost:
      // A cool blue vignette plus tiny ice-crystal crosses creeping
      // in from each edge.
      final frostRnd = math.Random(19);
      final frostPulse = math.sin(angle * 0.8) * 0.5 + 0.5;
      canvas.drawRect(
        Rect.fromLTWH(0, 0, w, h),
        Paint()
          ..shader = ui.Gradient.radial(
            Offset(w / 2, h / 2),
            math.sqrt(w * w + h * h) / 2,
            [Colors.transparent, const Color(0xFFBEEFFF).withOpacity(0.12 + frostPulse * 0.1)],
            const [0.5, 1.0],
          ),
      );
      const crystalCount = 20;
      for (int i = 0; i < crystalCount; i++) {
        final edgeSide = i % 4;
        double cx, cy;
        switch (edgeSide) {
          case 0:
            cx = frostRnd.nextDouble() * w;
            cy = frostRnd.nextDouble() * h * 0.12;
            break;
          case 1:
            cx = frostRnd.nextDouble() * w;
            cy = h - frostRnd.nextDouble() * h * 0.12;
            break;
          case 2:
            cx = frostRnd.nextDouble() * w * 0.12;
            cy = frostRnd.nextDouble() * h;
            break;
          default:
            cx = w - frostRnd.nextDouble() * w * 0.12;
            cy = frostRnd.nextDouble() * h;
        }
        final crystalSize = 3.0 + frostRnd.nextDouble() * 3.0;
        final crystalPaint = Paint()
          ..color = Colors.white.withOpacity(0.25 + frostPulse * 0.2)
          ..strokeWidth = 1.2
          ..blendMode = BlendMode.plus;
        canvas.drawLine(Offset(cx - crystalSize, cy), Offset(cx + crystalSize, cy), crystalPaint);
        canvas.drawLine(Offset(cx, cy - crystalSize), Offset(cx, cy + crystalSize), crystalPaint);
        canvas.drawLine(Offset(cx - crystalSize * 0.7, cy - crystalSize * 0.7), Offset(cx + crystalSize * 0.7, cy + crystalSize * 0.7), crystalPaint);
        canvas.drawLine(Offset(cx - crystalSize * 0.7, cy + crystalSize * 0.7), Offset(cx + crystalSize * 0.7, cy - crystalSize * 0.7), crystalPaint);
      }
      break;

    case _FxKind.smoke:
      // A handful of large, very soft blurred grey blobs drifting
      // slowly across the frame — light haze/smoke mood.
      final smokeRnd = math.Random(303);
      const smokeCount = 5;
      for (int i = 0; i < smokeCount; i++) {
        final baseX = smokeRnd.nextDouble() * w;
        final baseY = smokeRnd.nextDouble() * h;
        final phase = smokeRnd.nextDouble() * 2 * math.pi;
        final speed = 0.15 + smokeRnd.nextDouble() * 0.2;
        final driftX = math.sin(angle * speed + phase) * w * 0.1;
        final driftY = math.cos(angle * speed * 0.7 + phase) * h * 0.1;
        final smokeRadius = 60 + smokeRnd.nextDouble() * 50;
        canvas.drawCircle(
          Offset(baseX + driftX, baseY + driftY),
          smokeRadius,
          Paint()
            ..color = Colors.white.withOpacity(0.06)
            ..blendMode = BlendMode.plus
            ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 40),
        );
      }
      break;

    case _FxKind.lightning:
      // A brief full-frame flash timed with a jagged bolt path,
      // both fading out fast — repeats every loop.
      final flashPhase = (t * 1.3) % 1.0;
      final flash = flashPhase < 0.06 ? (1 - flashPhase / 0.06) : 0.0;
      if (flash > 0) {
        canvas.drawRect(Rect.fromLTWH(0, 0, w, h), Paint()..color = Colors.white.withOpacity(flash * 0.18));
        final boltRnd = math.Random((t * 5).floor());
        final startX = w * (0.2 + boltRnd.nextDouble() * 0.6);
        final boltPath = Path()..moveTo(startX, 0);
        double cx = startX;
        double cy = 0;
        while (cy < h) {
          cx += (boltRnd.nextDouble() - 0.5) * w * 0.12;
          cy += h * 0.12;
          boltPath.lineTo(cx, cy);
        }
        canvas.drawPath(
          boltPath,
          Paint()
            ..color = Colors.white.withOpacity(flash * 0.9)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 2.2
            ..blendMode = BlendMode.plus,
        );
      }
      break;
  }
}

// ═══════════════════════════════════════════════════════════════════
// Top-level isolate functions (must be top-level/static for `compute`)
// ═══════════════════════════════════════════════════════════════════

Uint8List _downscaleForEditing(Uint8List bytes) {
  final decoded = img.decodeImage(bytes);
  if (decoded == null) return bytes;
  const maxDim = 1440;
  img.Image working = decoded;
  if (decoded.width > maxDim || decoded.height > maxDim) {
    working = decoded.width >= decoded.height
        ? img.copyResize(decoded, width: maxDim)
        : img.copyResize(decoded, height: maxDim);
  }
  return Uint8List.fromList(img.encodeJpg(working, quality: 92));
}

List<int> _dimsOfBytes(Uint8List bytes) {
  final decoded = img.decodeImage(bytes);
  if (decoded == null) return [1, 1];
  return [decoded.width, decoded.height];
}

Uint8List _rotateImage90Ccw(Uint8List bytes) {
  final decoded = img.decodeImage(bytes);
  if (decoded == null) return bytes;
  final rotated = img.copyRotate(decoded, angle: -90);
  return Uint8List.fromList(img.encodeJpg(rotated, quality: 92));
}

Uint8List _flipImageHorizontal(Uint8List bytes) {
  final decoded = img.decodeImage(bytes);
  if (decoded == null) return bytes;
  final flipped = img.flipHorizontal(decoded);
  return Uint8List.fromList(img.encodeJpg(flipped, quality: 92));
}

Uint8List _flipImageVertical(Uint8List bytes) {
  final decoded = img.decodeImage(bytes);
  if (decoded == null) return bytes;
  final flipped = img.flipVertical(decoded);
  return Uint8List.fromList(img.encodeJpg(flipped, quality: 92));
}

Uint8List _pngToJpg(Uint8List pngBytes) {
  final decoded = img.decodePng(pngBytes);
  if (decoded == null) return pngBytes;
  return Uint8List.fromList(img.encodeJpg(decoded, quality: 92));
}

// Crops the final composited frame to a fractional rect (left, top,
// right, bottom, each 0..1) — the pinch-zoom/pan reframe's baked
// equivalent of the old modal Crop dialog. Runs as the very last step
// of export, on the fully-composited image, so overlay/stroke
// positions (which are relative to the *original* full frame) never
// need to be recalculated for the crop.
Uint8List _cropToFraction(Map<String, dynamic> args) {
  final bytes = args['bytes'] as Uint8List;
  final rect = (args['rect'] as List).cast<double>();
  final decoded = img.decodeImage(bytes);
  if (decoded == null) return bytes;
  final w = decoded.width, h = decoded.height;
  int x0 = (rect[0] * w).round().clamp(0, w - 1);
  int y0 = (rect[1] * h).round().clamp(0, h - 1);
  int x1 = (rect[2] * w).round().clamp(x0 + 1, w);
  int y1 = (rect[3] * h).round().clamp(y0 + 1, h);
  final cropped = img.copyCrop(decoded, x: x0, y: y0, width: x1 - x0, height: y1 - y0);
  return Uint8List.fromList(img.encodeJpg(cropped, quality: 92));
}

Uint8List _bakeImage(Map<String, dynamic> args) {
  final bytes = args['bytes'] as Uint8List;
  final matrix = args['matrix'] as List<double>;
  final vignette = (args['vignette'] as double?) ?? 0;
  final decoded = img.decodeImage(bytes);
  if (decoded == null) return bytes;

  // Identity matrix + no vignette: nothing to bake, return as-is.
  const identity = [
    1, 0, 0, 0, 0, //
    0, 1, 0, 0, 0, //
    0, 0, 1, 0, 0, //
    0, 0, 0, 1, 0,
  ];
  bool isIdentity = true;
  for (int i = 0; i < 20; i++) {
    if ((matrix[i] - identity[i]).abs() > 0.0001) {
      isIdentity = false;
      break;
    }
  }
  final hasVignette = vignette > 0;
  if (isIdentity && !hasVignette) {
    return Uint8List.fromList(img.encodeJpg(decoded, quality: 92));
  }

  final width = decoded.width;
  final height = decoded.height;
  final cx = width / 2;
  final cy = height / 2;
  final maxDist = math.sqrt(cx * cx + cy * cy);

  for (final pixel in decoded) {
    double r = pixel.r.toDouble();
    double g = pixel.g.toDouble();
    double b = pixel.b.toDouble();
    final a = pixel.a.toDouble();

    if (!isIdentity) {
      final newR = matrix[0] * r + matrix[1] * g + matrix[2] * b + matrix[3] * a + matrix[4];
      final newG = matrix[5] * r + matrix[6] * g + matrix[7] * b + matrix[8] * a + matrix[9];
      final newB = matrix[10] * r + matrix[11] * g + matrix[12] * b + matrix[13] * a + matrix[14];
      r = newR;
      g = newG;
      b = newB;
    }

    if (hasVignette) {
      final dx = pixel.x - cx;
      final dy = pixel.y - cy;
      final dist = math.sqrt(dx * dx + dy * dy) / maxDist;
      final factor = 1 - (vignette / 100) * dist * dist * 0.85;
      r *= factor;
      g *= factor;
      b *= factor;
    }

    pixel.r = r.clamp(0, 255);
    pixel.g = g.clamp(0, 255);
    pixel.b = b.clamp(0, 255);
  }

  return Uint8List.fromList(img.encodeJpg(decoded, quality: 92));
}

// ─── Auto-Enhance: simple auto-levels from a luminance histogram ───
// Runs on a downscaled sample (the working file is already capped at
// 1440px, so this is cheap) and returns [brightness, contrast,
// saturation] suggestions in the same -100..100 range the sliders
// use, so the result plugs straight into the existing _liveMatrix /
// _bakeImage pipeline with zero new baking code.
List<double> _computeAutoEnhance(Uint8List bytes) {
  final decoded = img.decodeImage(bytes);
  if (decoded == null) return [0, 0, 0];

  // Sample every Nth pixel for speed — plenty for a histogram.
  const step = 4;
  int count = 0;
  double sumLum = 0;
  double minLum = 255, maxLum = 0;
  double sumSat = 0;

  for (int y = 0; y < decoded.height; y += step) {
    for (int x = 0; x < decoded.width; x += step) {
      final p = decoded.getPixel(x, y);
      final r = p.r.toDouble(), g = p.g.toDouble(), b = p.b.toDouble();
      final lum = 0.299 * r + 0.587 * g + 0.114 * b;
      sumLum += lum;
      if (lum < minLum) minLum = lum;
      if (lum > maxLum) maxLum = lum;
      final maxC = math.max(r, math.max(g, b));
      final minC = math.min(r, math.min(g, b));
      sumSat += maxC == 0 ? 0 : (maxC - minC) / maxC;
      count++;
    }
  }
  if (count == 0) return [0, 0, 0];

  final avgLum = sumLum / count;
  final avgSat = sumSat / count;
  final range = (maxLum - minLum).clamp(1, 255);

  // Nudge the average toward mid-gray (128) — under/over-exposed
  // shots get a real push, well-exposed ones stay near untouched.
  final brightness = ((128 - avgLum) / 128 * 45).clamp(-40.0, 40.0);
  // Flat/low-range images (small maxLum-minLum spread) get a contrast
  // boost proportional to how compressed their histogram is.
  final contrast = ((255 - range) / 255 * 35).clamp(0.0, 35.0);
  // Desaturated-looking images (low average saturation) get a modest
  // saturation lift; already-vivid ones are left alone.
  final saturation = avgSat < 0.35 ? ((0.35 - avgSat) / 0.35 * 25).clamp(0.0, 25.0) : 0.0;

  return [brightness, contrast, saturation];
}

// ─── Magic Eraser: heuristic inpaint over a hand-painted mask ───
// Not a generative model — this is iterative neighbor-averaging
// diffusion (a simplified "heal" filter), restricted to the mask's
// bounding box (dilated a bit) for speed. Good for small blemishes,
// text, or watermarks on fairly plain/uniform backgrounds; a large
// or complex object on a busy background will smear rather than
// reconstruct detail, since there's no real content understanding.
Uint8List _magicErase(Map<String, dynamic> args) {
  final bytes = args['bytes'] as Uint8List;
  final strokesArg = args['strokes'] as List;
  final decoded = img.decodeImage(bytes);
  if (decoded == null) return bytes;
  final width = decoded.width;
  final height = decoded.height;

  // Rasterize the fractional-coordinate strokes into a boolean mask
  // at full image resolution, and track the mask's bounding box so
  // the diffusion pass only touches the pixels that matter.
  final mask = List<bool>.filled(width * height, false);
  int minX = width, minY = height, maxX = 0, maxY = 0;
  bool any = false;

  void markDisc(double cx, double cy, double radius) {
    final r = radius.clamp(1, 400).toInt();
    final ix = cx.round(), iy = cy.round();
    final x0 = math.max(0, ix - r), x1 = math.min(width - 1, ix + r);
    final y0 = math.max(0, iy - r), y1 = math.min(height - 1, iy + r);
    for (int y = y0; y <= y1; y++) {
      for (int x = x0; x <= x1; x++) {
        final dx = x - ix, dy = y - iy;
        if (dx * dx + dy * dy <= r * r) {
          mask[y * width + x] = true;
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
          any = true;
        }
      }
    }
  }

  for (final s in strokesArg) {
    final strokeMap = s as Map;
    final strokeWidth = (strokeMap['width'] as num).toDouble();
    final radius = (strokeWidth / 1000) * width / 2;
    final points = (strokeMap['points'] as List).map((p) {
      final coords = p as List;
      return Offset((coords[0] as num).toDouble() * width, (coords[1] as num).toDouble() * height);
    }).toList();
    for (int i = 0; i < points.length; i++) {
      if (i == 0) {
        markDisc(points[i].dx, points[i].dy, radius);
        continue;
      }
      // Stamp discs along each segment so a fast drag doesn't leave gaps.
      final a = points[i - 1], b = points[i];
      final dist = (b - a).distance;
      final steps = math.max(1, (dist / (radius * 0.6)).ceil());
      for (int t = 0; t <= steps; t++) {
        final f = t / steps;
        markDisc(a.dx + (b.dx - a.dx) * f, a.dy + (b.dy - a.dy) * f, radius);
      }
    }
  }
  if (!any) return bytes;

  // Small margin around the mask so the fill has real border context
  // to pull from, without processing the whole image.
  const margin = 12;
  minX = math.max(0, minX - margin);
  minY = math.max(0, minY - margin);
  maxX = math.min(width - 1, maxX + margin);
  maxY = math.min(height - 1, maxY + margin);
  final boxW = maxX - minX + 1;
  final boxH = maxY - minY + 1;

  // Working buffers for just the crop region.
  final r = Float32List(boxW * boxH);
  final g = Float32List(boxW * boxH);
  final b = Float32List(boxW * boxH);
  final isMasked = List<bool>.filled(boxW * boxH, false);
  for (int y = 0; y < boxH; y++) {
    for (int x = 0; x < boxW; x++) {
      final p = decoded.getPixel(minX + x, minY + y);
      final idx = y * boxW + x;
      r[idx] = p.r.toDouble();
      g[idx] = p.g.toDouble();
      b[idx] = p.b.toDouble();
      isMasked[idx] = mask[(minY + y) * width + (minX + x)];
    }
  }

  // Seed masked pixels from the nearest unmasked pixel (cheap
  // approximation via a few expanding passes) so the diffusion below
  // converges fast instead of starting from flat black.
  for (int pass = 0; pass < 3; pass++) {
    for (int y = 0; y < boxH; y++) {
      for (int x = 0; x < boxW; x++) {
        final idx = y * boxW + x;
        if (!isMasked[idx]) continue;
        double sr = 0, sg = 0, sb = 0;
        int n = 0;
        for (final d in const [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
          final nx = x + d[0], ny = y + d[1];
          if (nx < 0 || ny < 0 || nx >= boxW || ny >= boxH) continue;
          final nIdx = ny * boxW + nx;
          if (isMasked[nIdx] && pass == 0) continue; // first pass only pulls from real pixels
          sr += r[nIdx];
          sg += g[nIdx];
          sb += b[nIdx];
          n++;
        }
        if (n > 0) {
          r[idx] = sr / n;
          g[idx] = sg / n;
          b[idx] = sb / n;
        }
      }
    }
  }

  // Iterative averaging (Jacobi-style diffusion) smooths the seeded
  // fill into a plausible blend of its surroundings.
  const iterations = 60;
  final r2 = Float32List(boxW * boxH);
  final g2 = Float32List(boxW * boxH);
  final b2 = Float32List(boxW * boxH);
  for (int iter = 0; iter < iterations; iter++) {
    for (int y = 0; y < boxH; y++) {
      for (int x = 0; x < boxW; x++) {
        final idx = y * boxW + x;
        if (!isMasked[idx]) {
          r2[idx] = r[idx];
          g2[idx] = g[idx];
          b2[idx] = b[idx];
          continue;
        }
        double sr = r[idx], sg = g[idx], sb = b[idx];
        int n = 1;
        for (final d in const [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
          final nx = x + d[0], ny = y + d[1];
          if (nx < 0 || ny < 0 || nx >= boxW || ny >= boxH) continue;
          final nIdx = ny * boxW + nx;
          sr += r[nIdx];
          sg += g[nIdx];
          sb += b[nIdx];
          n++;
        }
        r2[idx] = sr / n;
        g2[idx] = sg / n;
        b2[idx] = sb / n;
      }
    }
    r.setAll(0, r2);
    g.setAll(0, g2);
    b.setAll(0, b2);
  }

  // Write the healed crop back into the full-resolution image.
  for (int y = 0; y < boxH; y++) {
    for (int x = 0; x < boxW; x++) {
      final idx = y * boxW + x;
      if (!isMasked[idx]) continue;
      final pixel = decoded.getPixel(minX + x, minY + y);
      pixel.r = r[idx].clamp(0, 255);
      pixel.g = g[idx].clamp(0, 255);
      pixel.b = b[idx].clamp(0, 255);
    }
  }

  return Uint8List.fromList(img.encodeJpg(decoded, quality: 92));
}