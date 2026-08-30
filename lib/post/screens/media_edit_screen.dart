// ═══════════════════════════════════════════════════════════════════
// Media Edit Screen — Instagram-style photo/video editor
// IMAGES: No modal "Crop" dialog and no Rotate/Flip buttons either —
// reframing is 100% gesture, directly on the live canvas (Stories/
// Reels style, but with rotate added on top): pinch with two fingers
// to zoom in/out, drag to reposition, twist to rotate — as much or as
// little as you want, all one continuous gesture — and everything
// (filters, draw, text, stickers, photos) zooms/pans/rotates together
// as one WYSIWYG surface. Baked in at export by rotating the final
// composited frame to match, then cropping it down to whatever's
// visible in the viewport — see `_reframeScale` / `_reframeOffset` /
// `_reframeRotation` below. + Filter presets + Brightness/Contrast/
// Saturation/Warmth sliders + Vignette +
// Text overlays (Classic/Bold/Neon/Typewriter/Highlight/Gradient/
// Outline, drag + pinch-to-scale + rotate, a curated Google Fonts
// family picker per overlay, a per-overlay background on/off toggle,
// and a small set of entrance animations — fade/pop/bounce/slide/pulse
// — that play live in the editor) + Draw/Doodle (freehand
// brush, 9 colors, adjustable width) + Stickers (emoji, your own PNG
// pack, AND multiple photos picked straight from the device — see
// "Add Photo" in the Stickers tab — all sharing the same drag/pinch/
// scale/rotate/undo machinery, freely overlappable/layerable on top
// of each other and the base photo) + Undo/Redo (covers strokes too)
// + hold-to-compare-original.
//
// Color adjustments/vignette are baked pixel-by-pixel in an isolate
// (matches the live GPU ColorFiltered preview exactly). Text overlays,
// doodle strokes, and the FX layer are composited on top afterwards
// using dart:ui, in the same z-order as the live preview (FX → draw
// strokes → text/stickers), on the main isolate — Canvas/TextPainter
// aren't available in `compute`.
//
// SNAPCHAT-STYLE ADDITIONS:
// • Face Filters — one-shot ML Kit face detection on load (images
//   only); picking a filter (dog/cat/glasses/crown/bunny) drops a
//   normal emoji sticker auto-positioned/scaled onto the detected
//   face's bounding box. Reuses the exact same drag/pinch/rotate/
//   undo machinery as any other sticker — no new rendering path.
//   No live camera, so this is "smart placement", not real-time
//   AR tracking; the user can drag it after placement like normal.
// • Live/Dynamic Stickers — Time / Date / Battery % pill stickers
//   (Highlight text style) that auto-refresh every 30s while editing
//   and bake to whatever value showed at export time, same as how
//   Snapchat's own timestamp/battery stickers freeze once you post.
// • Magic Eraser — a new tab where you paint a mask over an unwanted
//   spot (same stroke UI as Draw) and hit Apply; an isolate pass
//   inpaints the masked pixels via iterative neighbor-averaging
//   diffusion restricted to the mask's bounding box. This is a
//   heuristic "heal" good for small blemishes/text/watermarks, not
//   a real generative model — good for object removal on plain
//   backgrounds, not large/complex objects on busy backgrounds.
// • Auto-Enhance — one tap in Adjust computes suggested brightness/
//   contrast/saturation from the image's histogram (auto-levels) and
//   dials the sliders to it; still fully manually adjustable after.
//
// VIDEOS: Instagram-style Trim tab (drag start/end handles on a real
// filmstrip of ffmpeg-extracted thumbnails, min/max duration clamp,
// scrub-while-drag preview, play/pause, live playhead) + Cover-frame
// picker (now actually baked into the export as an mp4 attached-pic
// cover, not just a UI-only picker) + Rotate + Speed control (0.5x-3x,
// pitch-preserved audio via chained ffmpeg atempo) + Background Music
// (pick any audio file from device, volume slider, start-offset
// slider, mix-with-original or fully-replace, auto-loops to cover the
// clip length) + Boomerang (forward+reverse loop) + Multi-clip
// stitching (append extra clips, normalized to the main clip's
// resolution before concatenation).
//
// ADD MUSIC (Freesound, Instagram-style):
// • Music tab now has a second source besides "pick from device": a
//   Freesound search box. Backend (`/post/music/search/`) proxies the
//   query — the Freesound API key lives only in backend .env, never in
//   this app — and hard-filters results to CC0 (public-domain,
//   copyright-free) sounds only, so nothing shown here needs
//   attribution or carries any usage restriction.
// • Tap a result to preview it (streamed straight from its preview
//   URL via video_player), tap "Use" to open a crop sheet — pick any
//   1–20s window (clip-length + start-point sliders) — then the
//   preview is downloaded and ffmpeg-trimmed to exactly that window.
// • The cropped clip is handed to the SAME `_musicFile` the device-
//   picker already sets, so volume/mix/export logic is 100% shared —
//   this only adds an acquisition path, not a second pipeline.
// • Music tab is now available for IMAGES too (previously video-only).
//   Since a JPEG can't carry an audio track, if music is attached the
//   image export bakes a still-image + audio mp4 instead of a JPEG
//   (`-loop 1` on the image, `-shortest` capped to the music clip's
//   length) — same idea as Instagram turning a photo post with music
//   into a short video. The existing `isVideoFile(edited)` check at
//   the call site already handles routing an .mp4 result correctly,
//   no other call-site changes needed.
//
// pubspec.yaml addition needed for Freesound search:
//   http: ^1.2.0   (only if not already a dependency elsewhere)
// (No new package for preview playback — reuses video_player, which
// plays audio-only network sources fine.)
//
// Trim/cover/rotate are a **hand-rolled controller + widgets**
// (`_SimpleVideoEditController`, `_buildTrimSlider`, `_buildCoverPicker`)
// built directly on `video_player` + `ffmpeg_kit_flutter_new` — the
// `video_editor` package has been removed entirely. This drops a whole
// third-party dependency (one less package to break on Flutter/Gradle
// upgrades) and gives full control over the trim UX and the exact
// ffmpeg command used, instead of going through video_editor's own
// FFmpeg config builder. Filmstrip thumbnails are extracted once via a
// single `fps=` ffmpeg pass on load; export chains: trim -> boomerang
// -> clip stitching -> FX -> speed -> music -> cover-attach, each an
// ffmpeg pass, with progress split evenly across however many passes
// actually run.
//
// Background-music file selection uses `file_selector` (federated
// plugin) instead of `file_picker` — a smaller, Flutter-team-maintained
// dependency with native platform pickers on every target. Extra clip
// selection (for stitching) reuses the same `file_selector` picker.
//
// pubspec.yaml additions needed for video support:
//   video_player: ^2.9.0
//   ffmpeg_kit_flutter_new: ^1.6.0
//   file_selector: ^1.0.0         (background music + extra-clip picker)
// (video_editor is NOT needed anymore. Draw + Stickers + Gradient/
// Outline text use only Flutter's own Canvas/dart:ui — no new
// packages needed for those either.)
//
// pubspec.yaml additions needed for the Snapchat-style additions:
//   google_mlkit_face_detection: ^0.13.1   (Face Filters — one-shot detection)
//   battery_plus: ^6.0.0                   (Battery % live sticker)
// (Magic Eraser and Auto-Enhance use only dart:ui/`image` — no new
// packages needed for those.)
//
// pubspec.yaml addition needed for the multi-font text picker:
//   google_fonts: ^6.2.1
// (image_cropper has been removed entirely — pinch-zoom/pan reframing
// uses only Flutter's own InteractiveViewer, no package needed.)
//
// Usage:
//   final File? edited = await Navigator.push(context, MaterialPageRoute(
//     builder: (_) => MediaEditScreen(file: att.file),
//   ));
//   if (edited != null) setState(() => _attachments[index] = _MediaAttachment(edited, isVideoFile(edited) ? 'video' : 'image'));
// ═══════════════════════════════════════════════════════════════════

import 'dart:async';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart' show compute;
import 'package:flutter/services.dart' show rootBundle, HapticFeedback;
import 'package:google_fonts/google_fonts.dart';
import 'package:image/image.dart' as img;
import 'package:path_provider/path_provider.dart';
import 'package:video_player/video_player.dart';
import 'package:ffmpeg_kit_flutter_new/ffmpeg_kit.dart';
import 'package:ffmpeg_kit_flutter_new/ffprobe_kit.dart';
import 'package:ffmpeg_kit_flutter_new/return_code.dart';
import 'package:file_selector/file_selector.dart';
import 'package:google_mlkit_face_detection/google_mlkit_face_detection.dart';
import 'package:battery_plus/battery_plus.dart';
import 'package:http/http.dart' as http;
// ⚠️ CHECK THIS PATH — set to match wherever api_service.dart actually
// sits relative to this screen in your project (e.g. '../api/api_service.dart'
// or '../services/api_service.dart'). Only used for the Freesound search call.
import '../api_service.dart';
import 'auto_edit_screen.dart';

part 'media_edit_screen_painters.dart';

// Recognized video extensions — used to auto-detect media type from
// the incoming file so callers don't have to pass an extra flag.
bool isVideoFile(File file) {
  final ext = file.path.toLowerCase().split('.').last;
  return {'mp4', 'mov', 'm4v', 'webm', 'mkv', 'avi', '3gp'}.contains(ext);
}

class MediaEditScreen extends StatefulWidget {
  const MediaEditScreen({super.key, required this.file});
  final File file;

  @override
  State<MediaEditScreen> createState() => _MediaEditScreenState();
}

enum _EditTab { filters, adjust, text, draw, stickers, fx, eraser, trim, cover, speed, music, boomerang, clips, photoClips }

enum _TextStyleKind { classic, bold, neon, typewriter, highlight, gradient, outline }

// Live/Dynamic stickers — content that isn't typed by the user but
// pulled from the device at add-time (and refreshed periodically
// while still in the editor, Snapchat-timestamp-sticker style).
// `none` means a normal user-typed text/emoji overlay.
enum _DynamicKind { none, time, date, battery }

// Live/animated filter effects — looping overlays for both image and
// video preview. Preview animates continuously; on export, images bake
// a single representative frame (a static JPEG can't loop), while
// video gets the real animated ffmpeg filter applied over its full
// trimmed duration.
enum _FxKind {
  none,
  colorShift,
  glitch,
  sparkle,
  filmGrain,
  lightLeak,
  rainbow,
  snow,
  vignettePulse,
  bokeh,
  confetti,
  embers,
  neonPulse,
  rain,
  lensFlare,
  fireworks,
  bubbles,
  hearts,
  fireflies,
  aurora,
  petals,
  staticNoise,
  frost,
  smoke,
  lightning,
}

// Entrance animation for a text/sticker overlay — plays on a loop live
// in the editor (so the user can see the vibe before posting), same
// "preview animates, export freezes the settled frame" deal as _FxKind
// above: a still JPEG can't loop, so baking always uses the overlay's
// resting position/opacity/scale, never a mid-animation frame.
enum _TextAnimKind { none, fadeIn, popIn, bounce, pulse, slideUp }

// Which chip strip is showing in the bottom of the Instagram-style
// full-screen text editor's tabbed options panel (Style/Font/FX all
// share one compact row instead of stacking three permanent rows).
enum _TextPanel { style, font, anim }

// Curated Google Fonts family names for the text tool, grouped roughly
// the way Instagram/CapCut style pickers do — a plain sans default, a
// couple of bold display faces, a condensed impact face, a serif, two
// script/handwriting faces, and a typewriter monospace (this replaces
// the old hardcoded `fontFamily: 'monospace'` on the Typewriter style).
// `label` is what the chip shows; `family` is the exact GoogleFonts
// name used to look up the TextStyle.
class _FontOption {
  const _FontOption(this.label, this.family);
  final String label;
  final String family;
}

const List<_FontOption> _fontOptions = [
  _FontOption('Classic', 'Roboto'),
  _FontOption('Arial', 'Arial'), // falls back to the device's system Arial, not a Google Fonts family
  _FontOption('Poppins', 'Poppins'),
  _FontOption('Anton', 'Anton'),
  _FontOption('Bebas', 'Bebas Neue'),
  _FontOption('Oswald', 'Oswald'),
  _FontOption('Playfair', 'Playfair Display'),
  _FontOption('Caveat', 'Caveat'),
  _FontOption('Pacifico', 'Pacifico'),
  _FontOption('Marker', 'Permanent Marker'),
  _FontOption('Mono', 'Courier Prime'),
];

class _FilterPreset {
  const _FilterPreset(this.name, this.matrix);
  final String name;
  final List<double> matrix;
}

// A single text overlay placed on the image. dx/dy are fractions
// (0..1) of the image's own bounds so they map 1:1 between the
// preview widget and the full-resolution baked output.
class _TextOverlay {
  _TextOverlay({
    required this.id,
    required this.text,
    required this.style,
    required this.color,
    this.color2,
    this.dx = 0.5,
    this.dy = 0.5,
    this.fontSize = 46, // reference size at a 1000px-wide canvas
    this.rotation = 0, // radians
    this.scale = 1,
    this.isSticker = false,
    this.dynamicKind = _DynamicKind.none,
    this.assetPath,
    this.imageFile,
    this.fontFamily = 'Roboto',
    this.showBackground,
    this.animKind = _TextAnimKind.none,
    this.textAlign = TextAlign.center,
  });

  final String id;
  String text;
  _TextStyleKind style;
  Color color;
  Color? color2; // second stop for the Gradient style; null = falls back to accent
  double dx;
  double dy;
  double fontSize;
  double rotation;
  double scale;
  // Google Fonts family name (see _fontOptions) — user-pickable per overlay.
  String fontFamily;
  // Overrides whether this overlay paints a background chip/pill.
  // null = use the style's own default (Highlight/Typewriter are
  // pill-backed, everything else isn't) — set explicitly once the user
  // taps the Background toggle so their choice sticks regardless of style.
  bool? showBackground;
  _TextAnimKind animKind;
  // Multi-line text alignment (left/center/right) — set from the
  // Instagram-style text editor's alignment-cycle button.
  TextAlign textAlign;
  // When true, `text` holds a single emoji glyph rendered plain/large
  // (no background/shadow/stroke) instead of going through the
  // Classic/Bold/Neon/etc styling — lets stickers reuse the exact same
  // drag + pinch-to-scale + rotate + undo machinery as text overlays.
  final bool isSticker;
  // != none for Live/Dynamic stickers (time/date/battery) — `text` is
  // still what actually renders and exports; this just tells the
  // periodic refresh timer which overlays to keep updating live.
  _DynamicKind dynamicKind;
  // Non-null for a PNG sticker from assets/stickers/ (your own pack) —
  // when set, this renders/bakes as that image instead of `text`.
  // Still isSticker: true, so drag/pinch/rotate/undo/export are 100%
  // shared with emoji and face-filter stickers.
  String? assetPath;
  // Non-null for a user-picked photo from their own gallery/device
  // (multiple can be added, dragged/pinched/rotated and freely
  // overlapped/layered — "override" one on top of another — exactly
  // like any other sticker). Still isSticker: true so it shares all
  // the same drag/pinch/rotate/undo/delete/export machinery.
  File? imageFile;

  _TextOverlay copy() => _TextOverlay(
        id: id,
        text: text,
        style: style,
        color: color,
        color2: color2,
        dx: dx,
        dy: dy,
        fontSize: fontSize,
        rotation: rotation,
        scale: scale,
        isSticker: isSticker,
        dynamicKind: dynamicKind,
        assetPath: assetPath,
        imageFile: imageFile,
        fontFamily: fontFamily,
        showBackground: showBackground,
        animKind: animKind,
        textAlign: textAlign,
      );
}

// Resolves a GoogleFonts family name to a TextStyle merged over `base`.
// 'Arial' is left alone (system font, not part of the Google Fonts
// catalog); anything else goes through GoogleFonts.getFont, which
// falls back gracefully to the platform default if a family fails to
// load rather than throwing.
TextStyle _applyFontFamily(String family, TextStyle base) {
  if (family == 'Arial') return base.copyWith(fontFamily: 'Arial');
  try {
    return GoogleFonts.getFont(family, textStyle: base);
  } catch (_) {
    return base;
  }
}

// Whether this style shows a background chip/pill by default, before
// any per-overlay override from _TextOverlay.showBackground.
bool _styleDefaultBackground(_TextStyleKind kind) {
  switch (kind) {
    case _TextStyleKind.typewriter:
    case _TextStyleKind.highlight:
      return true;
    case _TextStyleKind.classic:
    case _TextStyleKind.bold:
    case _TextStyleKind.neon:
    case _TextStyleKind.gradient:
    case _TextStyleKind.outline:
      return false;
  }
}

// A single freehand doodle stroke. `points` are fractions (0..1) of
// the image's own bounds, same convention as _TextOverlay's dx/dy, so
// strokes map 1:1 between the preview widget and the full-resolution
// baked output regardless of screen size.
class _DrawStroke {
  _DrawStroke({required this.color, required this.width, List<Offset>? points})
      : points = points ?? [];
  final Color color;
  final double width; // reference width at a 1000px-wide canvas
  final List<Offset> points;

  _DrawStroke copy() => _DrawStroke(color: color, width: width, points: List.of(points));
}

// Snapshot of everything undo/redo needs to restore.
class _HistorySnapshot {
  const _HistorySnapshot({
    required this.file,
    required this.workingWidth,
    required this.workingHeight,
    required this.filterIndex,
    required this.filterIntensity,
    required this.brightness,
    required this.contrast,
    required this.saturation,
    required this.warmth,
    required this.vignette,
    required this.overlays,
    required this.strokes,
  });
  final File file;
  final int workingWidth;
  final int workingHeight;
  final int filterIndex;
  final double filterIntensity;
  final double brightness;
  final double contrast;
  final double saturation;
  final double warmth;
  final double vignette;
  final List<_TextOverlay> overlays;
  final List<_DrawStroke> strokes;
}

// ═══════════════════════════════════════════════════════════════════
// MANUAL "Add Photos" montage — the manual counterpart to
// AutoEditScreen's auto-cut flow (see auto_edit_screen.dart), lived
// right here in MediaEditScreen instead of a separate screen: the
// user is already editing one photo/video, and can add 2+ more
// photos/videos straight from this tab, order them, crop/pan/zoom +
// (for photos) duration or (for videos) speed each one individually,
// pick a transition into the next clip, then Done bakes the whole
// thing — the already-edited primary media as clip 1, followed by
// every clip added here — into one combined video, with whatever
// music is set in the Music tab attached at the end exactly once
// (same `_musicFile`/`_musicVolume`/`_musicStartOffsetSec` sink the
// single-clip export already uses).
//
// Transition catalog and the xfade-chain render approach are the same
// six transitions (and the same "xfade built-ins for Flash/Zoom/Slide,
// hand-written pixel fx burned into the xfade window for Glitch/RGB
// Split/Shake" trick) as AutoEditScreen — kept as a private copy here
// (Dart's `_`-prefixed names are file-private, so no clash) rather
// than importing that screen's internals, so this file stays
// self-contained. See `_buildManualClipsMontage` / `_manualClipFilter`
// near the bottom of this file for the render pipeline itself.
// ═══════════════════════════════════════════════════════════════════
enum _TransitionKind { glitch, flash, zoom, shake, rgbSplit, slide }

extension _ManualTransitionMeta on _TransitionKind {
  String get label => switch (this) {
        _TransitionKind.glitch => 'Glitch',
        _TransitionKind.flash => 'Flash',
        _TransitionKind.zoom => 'Zoom',
        _TransitionKind.shake => 'Shake',
        _TransitionKind.rgbSplit => 'RGB Split',
        _TransitionKind.slide => 'Slide',
      };

  IconData get icon => switch (this) {
        _TransitionKind.glitch => Icons.broken_image_outlined,
        _TransitionKind.flash => Icons.flash_on,
        _TransitionKind.zoom => Icons.zoom_in,
        _TransitionKind.shake => Icons.vibration,
        _TransitionKind.rgbSplit => Icons.blur_linear,
        _TransitionKind.slide => Icons.swap_horiz,
      };

  // ffmpeg's own built-in xfade transition name this maps to.
  String get xfadeName => switch (this) {
        _TransitionKind.flash => 'fadewhite',
        _TransitionKind.slide => 'slideleft',
        _TransitionKind.zoom => 'zoomin',
        _TransitionKind.glitch => 'pixelize',
        _TransitionKind.rgbSplit => 'distance',
        _TransitionKind.shake => 'hblur',
      };
}

// A single clip added via the "Add Photos" tab. zoom/panX/panY/
// rotationDeg are the manual crop/reframe for this one clip (set via
// the pinch-zoom-pan InteractiveViewer in `_editManualClip`'s bottom
// sheet) — independent of the primary media's own `_reframeScale` /
// `_reframeOffset` / `_reframeRotation`, since each clip here needs
// its own framing. panX/panY are fractions (-1..1) of the *extra*
// pannable range once zoomed in (0 = centered), matching how much
// slack `zoom` actually leaves — see `_manualClipFilter`.
class _ManualClip {
  _ManualClip({
    required this.file,
    required this.isVideo,
    this.duration = const Duration(seconds: 2), // photos only
    this.speed = 1.0, // videos only — 0.5x (slow) .. 2.5x (fast)
    this.zoom = 1.0, // >= 1.0
    this.panX = 0.0,
    this.panY = 0.0,
    this.rotationDeg = 0.0, // -45..45
    this.transitionOut = _TransitionKind.slide,
    this.sourceDurationSec,
  });

  final File file;
  final bool isVideo;
  Duration duration;
  double speed;
  double zoom;
  double panX;
  double panY;
  double rotationDeg;
  _TransitionKind transitionOut; // transition into the NEXT clip (ignored on the last clip)
  double? sourceDurationSec; // videos only — probed natural length, before speed
}

// The small "N" index chip shown on manual-montage clip thumbnails
// (both the primary-clip card and each _manualClips entry).
class _ClipBadge extends StatelessWidget {
  const _ClipBadge({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
      decoration: BoxDecoration(color: Colors.black87, borderRadius: BorderRadius.circular(4)),
      child: Text(text, style: const TextStyle(color: Colors.white, fontSize: 9, fontWeight: FontWeight.w700)),
    );
  }
}

// ═══════════════════════════════════════════════════════════════════
// Hand-rolled video trim/cover/rotate controller — replaces the
// `video_editor` package. Wraps a plain `video_player` controller and
// tracks trim range, chosen cover frame, and 90°-step rotation; the
// filmstrip thumbnails it holds are shared by both the Trim slider and
// the Cover picker (extracted once via a single ffmpeg pass).
// ═══════════════════════════════════════════════════════════════════
class _SimpleVideoEditController extends ChangeNotifier {
  _SimpleVideoEditController({
    required this.video,
    required this.duration,
    required this.minTrim,
    required this.maxTrim,
  })  : startTrim = Duration.zero,
        endTrim = maxTrim < duration ? maxTrim : duration,
        coverPosition = Duration.zero;

  final VideoPlayerController video;
  final Duration duration; // full, untrimmed clip length
  final Duration minTrim; // shortest allowed trimmed length
  final Duration maxTrim; // longest allowed trimmed length (Reels-style cap)

  Duration startTrim;
  Duration endTrim;
  Duration coverPosition;
  int rotationTurns = 0; // 0..3, each step = +90° clockwise

  List<Uint8List> thumbnails = [];
  bool thumbnailsReady = false;

  Duration get trimmedDuration => endTrim - startTrim;

  void setTrim(Duration start, Duration end) {
    startTrim = start;
    endTrim = end;
    if (coverPosition < startTrim || coverPosition > endTrim) coverPosition = startTrim;
    notifyListeners();
  }

  void setCover(Duration position) {
    coverPosition = position;
    notifyListeners();
  }

  void rotateLeft() {
    rotationTurns = (rotationTurns + 3) % 4;
    notifyListeners();
  }

  void rotateRight() {
    rotationTurns = (rotationTurns + 1) % 4;
    notifyListeners();
  }

  void setThumbnails(List<Uint8List> thumbs) {
    thumbnails = thumbs;
    thumbnailsReady = true;
    notifyListeners();
  }

  @override
  void dispose() {
    video.dispose();
    super.dispose();
  }
}

class _MediaEditScreenState extends State<MediaEditScreen> with TickerProviderStateMixin {
  static const Color _primary = Color(0xFF6366F1);
  static const Color _accent = Color(0xFF8B5CF6);
  static const Color _muted = Color(0xFF9095A6);
  static const Color _bg = Color(0xFF0B0B0F);

  static const List<Color> _textColorSwatches = [
    Colors.white,
    Colors.black,
    _primary,
    _accent,
    Color(0xFFFF5252),
    Color(0xFFFFA726),
    Color(0xFFFFEB3B),
    Color(0xFF66FF66),
    Color(0xFF40E0FF),
    Color(0xFFFF4FA3),
  ];

  late File _workingFile; // possibly cropped/rotated, capped resolution
  int _workingWidth = 1;
  int _workingHeight = 1;
  bool _isPreparing = true;
  bool _isSaving = false;
  bool _isTransforming = false; // rotate/flip in progress

  // ─── Auto Edit result (reversible until Done is pressed) ───
  // Holding the result here instead of popping immediately means the
  // Auto Edit montage is just a *preview* — "Remove" throws it away
  // with nothing baked into _workingFile, exactly like backing out of
  // any other tab before hitting Done.
  File? _autoEditResult;
  VideoPlayerController? _autoEditPreviewController;

  // ─── Video trim mode ───
  late final bool _isVideo = isVideoFile(widget.file);
  _SimpleVideoEditController? _videoController;
  double _exportProgress = 0;

  // ─── Animated FX (live filter effects) ───
  late final AnimationController _fxAnimController;
  _FxKind _selectedFx = _FxKind.none;

  // ─── Polished screen-entrance animation (fade + gentle scale-up on
  // first frame) — purely cosmetic, no effect on export/state ───
  late final AnimationController _entranceController;
  late final Animation<double> _entranceFade;
  late final Animation<double> _entranceScale;

  // ─── Text overlay entrance animations (preview-only loop; export
  // always bakes the settled/resting frame — see _TextAnimKind) ───
  late final AnimationController _textAnimController;

  // ─── Pinch-zoom + pan reframing — replaces the old modal Crop
  // dialog. A background-only gesture layer (same pattern as the
  // Draw/Eraser full-fill layers below) drives a plain Transform
  // around the whole composed canvas — image, draw strokes, FX and
  // text/stickers all scale/pan together — so what's visible while
  // pinching is exactly what export crops the final composited frame
  // down to. Deliberately NOT an InteractiveViewer: nesting its own
  // scale recognizer around the per-overlay drag/pinch/rotate
  // GestureDetectors would fight them in the gesture arena.
  double _reframeScale = 1.0; // >= 1.0, 1.0 = full uncropped frame
  Offset _reframeOffset = Offset.zero; // pan, in unscaled local px, clamped so content always covers the box
  double _reframeRotation = 0.0; // radians — two-finger rotate, same gesture as zoom/pan (no button)
  double _reframeBaseScale = 1.0;
  Offset _reframeBaseOffset = Offset.zero;
  double _reframeBaseRotation = 0.0;
  Offset _reframeGestureStartFocal = Offset.zero;
  // True only while a pinch/pan/rotate gesture is actively in progress —
  // drives the fading rule-of-thirds grid below, Instagram-crop style.
  bool _isReframing = false;
  bool get _isReframed => _reframeScale != 1.0 || _reframeOffset != Offset.zero || _reframeRotation != 0.0;
  double _lastBoxW = 1;
  double _lastBoxH = 1;

  Offset _clampReframeOffset(Offset offset, double scale, double boxW, double boxH) {
    final maxDx = boxW * (scale - 1) / 2;
    final maxDy = boxH * (scale - 1) / 2;
    return Offset(offset.dx.clamp(-maxDx, maxDx), offset.dy.clamp(-maxDy, maxDy));
  }

  _EditTab _tab = _EditTab.filters;
  int _selectedFilterIndex = 0;
  double _filterIntensity = 100; // 0..100 — blends the preset with the original
  double _brightness = 0; // -100..100
  double _contrast = 0; // -100..100
  double _saturation = 0; // -100..100
  double _warmth = 0; // -100..100 (cool..warm)
  double _vignette = 0; // 0..100

  bool _showOriginal = false; // hold-to-compare

  final List<_TextOverlay> _textOverlays = [];
  String? _selectedOverlayId;
  int _overlayIdCounter = 0;
  double _dragStartScale = 1;
  double _dragStartRotation = 0;

  // ─── Live "type-on-photo" preview (Instagram-style): while the
  // Add/Edit Text sheet is open, every keystroke and every style/font/
  // color/background/animation pick updates this and it's rendered
  // directly on the canvas immediately — not just after tapping Add/
  // Save. When editing an existing overlay, that overlay is hidden
  // from its normal spot in the Stack (see _hiddenOverlayId) while
  // this live stand-in shows in its place, so there's never a
  // duplicate/stale copy visible underneath the live one.
  _TextOverlay? _liveTextPreview;
  String? _hiddenOverlayId;

  // ─── Draw / doodle tool ───
  static const List<Color> _drawColorSwatches = [
    Colors.white,
    Colors.black,
    _primary,
    Color(0xFFFF5252),
    Color(0xFFFFA726),
    Color(0xFFFFEB3B),
    Color(0xFF66FF66),
    Color(0xFF40E0FF),
    Color(0xFFFF4FA3),
  ];
  final List<_DrawStroke> _drawStrokes = [];
  Color _selectedDrawColor = Colors.white;
  double _selectedDrawWidth = 8; // reference width at 1000px-wide canvas
  _DrawStroke? _activeStroke; // in-progress stroke, promoted to _drawStrokes on release

  // ─── Magic Eraser: paint a mask, then bake an inpaint pass ───
  final List<_DrawStroke> _eraserStrokes = []; // mask strokes, cleared after Apply
  double _eraserBrushWidth = 34; // reference width at a 1000px-wide canvas — deliberately fat, it's a mask
  _DrawStroke? _activeEraserStroke;
  bool _isErasing = false; // inpaint pass in progress

  // ─── Face Filters: one-shot detection (images only) ───
  FaceDetector? _faceDetector;
  List<Face> _detectedFaces = [];
  bool _isDetectingFaces = false;

  // ─── Live/Dynamic stickers (Time/Date/Battery) ───
  Timer? _dynamicStickerTimer;
  int? _batteryLevel;

  // ─── Video speed ───
  static const List<double> _speedOptions = [0.5, 1.0, 1.5, 2.0, 3.0];
  double _videoSpeed = 1.0;

  // ─── Background music ───
  File? _musicFile;
  String? _musicFileName;
  double _musicVolume = 0.8; // 0..1
  double _musicStartOffsetSec = 0; // where in the track playback begins
  bool _keepOriginalAudio = true; // false = music fully replaces the clip's own audio
  bool _isPickingMusic = false;

  // ─── Freesound music search (Instagram-style "Add Music") ───
  // Only ever shows CC0 (public-domain / copyright-free) results — the
  // backend (`/post/music/search/`) already hard-filters to CC0 before
  // this even sees them, so nothing here needs its own license check.
  // Device-picked music (above) stays available too — this is an
  // additional source, not a replacement.
  final ApiService _apiService = ApiService();
  final TextEditingController _freesoundQueryCtrl = TextEditingController();
  List<Map<String, dynamic>> _freesoundResults = [];
  bool _freesoundSearching = false;
  String? _freesoundError;
  Map<String, dynamic>? _playingPreviewTrack;
  VideoPlayerController? _freesoundPreviewPlayer; // audio-only stream, reuses video_player
  bool _isCroppingFreesoundTrack = false;
  bool _musicFromFreesound = false; // true = _musicFile is an already-cropped 1-20s clip
  // Selected-track crop state (shown in a bottom sheet before "Use").
  Map<String, dynamic>? _freesoundCropTrack;
  double _freesoundCropStart = 0;
  double _freesoundCropDuration = 15; // clamped to [1, 20]

  // ─── Boomerang (forward + reverse loop) ───
  bool _boomerangEnabled = false;

  // ─── Multi-clip stitching (append extra clips after the main one) ───
  final List<File> _extraClips = [];

  // ── "Add Photos" manual montage (see _ManualClip above) ──
  final List<_ManualClip> _manualClips = [];
  bool _isPickingManualClip = false;
  // Only relevant once _manualClips is non-empty — this photo/video's
  // own slot in the montage (as clip 1). Duration only applies if
  // this primary media is a photo; a video primary uses its own
  // trimmed length instead (see _onDoneManualMontage).
  Duration _primaryClipDuration = const Duration(seconds: 2);
  _TransitionKind _primaryTransitionOut = _TransitionKind.slide;
  bool _isPickingClip = false;

  final List<_HistorySnapshot> _undoStack = [];
  final List<_HistorySnapshot> _redoStack = [];

  late final List<_FilterPreset> _presets;

  @override
  void initState() {
    super.initState();
    _presets = _buildPresets();
    _fxAnimController = AnimationController(vsync: this, duration: const Duration(seconds: 3))..repeat();
    _textAnimController = AnimationController(vsync: this, duration: const Duration(seconds: 2))..repeat();
    _entranceController = AnimationController(vsync: this, duration: const Duration(milliseconds: 420))..forward();
    _entranceFade = CurvedAnimation(parent: _entranceController, curve: Curves.easeOut);
    _entranceScale = Tween<double>(begin: 0.97, end: 1.0)
        .animate(CurvedAnimation(parent: _entranceController, curve: Curves.easeOutCubic));
    if (_isVideo) {
      _tab = _EditTab.trim;
      _prepareVideoController();
    } else {
      _prepareWorkingFile();
      _faceDetector = FaceDetector(
        options: FaceDetectorOptions(performanceMode: FaceDetectorMode.accurate, enableLandmarks: true),
      );
    }
    // Refreshes Time/Date/Battery stickers' displayed text every 30s
    // while the editor is open, so they don't look stale mid-edit —
    // export just bakes whatever they read at that moment (same as
    // Snapchat's own timestamp sticker freezing once you post).
    _dynamicStickerTimer = Timer.periodic(const Duration(seconds: 30), (_) => _refreshDynamicStickers());
  }

  @override
  void dispose() {
    _fxAnimController.dispose();
    _textAnimController.dispose();
    _entranceController.dispose();
    _videoController?.dispose();
    _dynamicStickerTimer?.cancel();
    _faceDetector?.close();
    _freesoundPreviewPlayer?.dispose();
    _autoEditPreviewController?.dispose();
    _freesoundQueryCtrl.dispose();
    super.dispose();
  }

  // ─── Video setup: load into our own lightweight controller ───
  Future<void> _prepareVideoController() async {
    final vp = VideoPlayerController.file(widget.file);
    try {
      await vp.initialize();
      if (!mounted) {
        vp.dispose();
        return;
      }
      await vp.setLooping(true);
      final duration = vp.value.duration;
      // Instagram reels/stories-style cap; raise/remove if you need longer trims.
      const maxDuration = Duration(seconds: 90);
      final controller = _SimpleVideoEditController(
        video: vp,
        duration: duration,
        minTrim: const Duration(seconds: 1),
        maxTrim: duration < maxDuration ? duration : maxDuration,
      );
      setState(() {
        _videoController = controller;
        _isPreparing = false;
      });
      // Filmstrip thumbnails aren't needed to start editing (trim
      // works immediately against a placeholder track), so this runs
      // in the background and just fills in once ready.
      _generateFilmstripThumbnails(widget.file, duration).then((thumbs) {
        if (!mounted || _videoController != controller) return;
        controller.setThumbnails(thumbs);
      });
    } catch (e) {
      vp.dispose();
      if (!mounted) return;
      setState(() => _isPreparing = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Video load nahi ho paya: $e')),
      );
    }
  }

  // Extracts ~12 evenly-spaced frames via a single ffmpeg `fps=` pass
  // (one process launch instead of one per thumbnail) and reads them
  // back as JPEG bytes for the Trim filmstrip and Cover picker.
  static Future<List<Uint8List>> _generateFilmstripThumbnails(
    File videoFile,
    Duration duration, {
    int count = 12,
  }) async {
    try {
      final dir = await getTemporaryDirectory();
      final stamp = DateTime.now().millisecondsSinceEpoch;
      final pattern = '${dir.path}/thumb_${stamp}_%03d.jpg';
      final totalSec = duration.inMilliseconds / 1000.0;
      final fps = totalSec > 0 ? count / totalSec : 1.0;
      final session = await FFmpegKit.execute(
        '-y -i "${videoFile.path}" -vf "fps=$fps,scale=180:-1" -vsync vfr -frames:v $count "$pattern"',
      );
      final code = await session.getReturnCode();
      if (!ReturnCode.isSuccess(code)) return [];
      final thumbs = <Uint8List>[];
      for (int i = 1; i <= count; i++) {
        final f = File('${dir.path}/thumb_${stamp}_${i.toString().padLeft(3, '0')}.jpg');
        if (await f.exists()) thumbs.add(await f.readAsBytes());
      }
      return thumbs;
    } catch (_) {
      return [];
    }
  }

  // ─── Background music: pick / remove ───
  // Uses `file_selector`'s openFile — a federated plugin with native
  // pickers per platform, in place of the old `file_picker` dependency.
  Future<void> _pickMusic() async {
    setState(() => _isPickingMusic = true);
    try {
      const XTypeGroup audioTypeGroup = XTypeGroup(
        label: 'audio',
        extensions: ['mp3', 'wav', 'm4a', 'aac', 'ogg', 'flac', 'wma'],
        // uniformTypeIdentifiers is only consulted on iOS/macOS; audio
        // covers the common container/codec UTIs those pickers expect.
        uniformTypeIdentifiers: ['public.audio'],
      );
      final XFile? picked = await openFile(acceptedTypeGroups: [audioTypeGroup]);
      if (!mounted) return;
      if (picked != null) {
        setState(() {
          _musicFile = File(picked.path);
          _musicFileName = picked.name;
          _musicStartOffsetSec = 0;
          _musicFromFreesound = false;
        });
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Music select nahi ho paya: $e')),
      );
    } finally {
      if (mounted) setState(() => _isPickingMusic = false);
    }
  }

  void _removeMusic() {
    setState(() {
      _musicFile = null;
      _musicFileName = null;
      _musicStartOffsetSec = 0;
      _musicFromFreesound = false;
    });
  }

  // ─── Freesound: search (CC0-only, filtered server-side) ───
  Future<void> _searchFreesound(String query) async {
    if (query.trim().isEmpty) return;
    setState(() {
      _freesoundSearching = true;
      _freesoundError = null;
    });
    try {
      final results = await _apiService.searchFreesoundMusic(query.trim());
      if (!mounted) return;
      setState(() => _freesoundResults = results);
    } catch (e) {
      if (!mounted) return;
      setState(() => _freesoundError = 'Search fail ho gaya, dobara try karo');
    } finally {
      if (mounted) setState(() => _freesoundSearching = false);
    }
  }

  // Streams the track's preview URL directly (no download needed just
  // to audition it) via video_player — it plays audio-only network
  // sources fine, so no extra audio-player dependency is needed.
  // Tapping the currently-playing track again stops it.
  Future<void> _togglePreviewPlayback(Map<String, dynamic> track) async {
    final isSame = _playingPreviewTrack?['id'] == track['id'];
    await _freesoundPreviewPlayer?.dispose();
    _freesoundPreviewPlayer = null;
    if (isSame) {
      setState(() => _playingPreviewTrack = null);
      return;
    }
    setState(() => _playingPreviewTrack = track);
    try {
      final url = track['preview_url'] as String?;
      if (url == null) return;
      final player = VideoPlayerController.networkUrl(Uri.parse(url));
      await player.initialize();
      if (!mounted) {
        player.dispose();
        return;
      }
      await player.play();
      setState(() => _freesoundPreviewPlayer = player);
    } catch (_) {
      if (!mounted) return;
      setState(() => _playingPreviewTrack = null);
    }
  }

  // Opens the crop sheet for a chosen track — start-offset + duration
  // (1–20s, Instagram-style) sliders, clamped to the track's actual
  // length. Nothing is downloaded/trimmed until "Use this sound".
  void _openFreesoundCropSheet(Map<String, dynamic> track) {
    final trackDuration = ((track['duration'] as num?) ?? 20).toDouble();
    final maxCrop = math.min(20.0, trackDuration).clamp(1.0, 20.0);
    setState(() {
      _freesoundCropTrack = track;
      _freesoundCropDuration = maxCrop;
      _freesoundCropStart = 0;
    });
  }

  // Downloads the chosen track's preview, trims it to the picked
  // [start, start+duration] window via ffmpeg, then hands the result
  // to the SAME `_musicFile` pipeline the device-picked-music flow
  // already uses — volume/mix controls and the export mix pass below
  // don't need to know or care where the file came from.
  Future<void> _applyFreesoundCrop() async {
    final track = _freesoundCropTrack;
    if (track == null) return;
    final url = track['preview_url'] as String?;
    if (url == null) return;
    setState(() => _isCroppingFreesoundTrack = true);
    try {
      await _freesoundPreviewPlayer?.pause();
      final response = await http.get(Uri.parse(url));
      if (response.statusCode != 200) {
        throw Exception('Download fail (${response.statusCode})');
      }
      final dir = await getTemporaryDirectory();
      final stamp = DateTime.now().millisecondsSinceEpoch;
      final rawPath = '${dir.path}/freesound_raw_$stamp.mp3';
      await File(rawPath).writeAsBytes(response.bodyBytes);

      final croppedPath = '${dir.path}/freesound_cropped_$stamp.m4a';
      final session = await FFmpegKit.execute(
        '-y -i "$rawPath" -ss $_freesoundCropStart -t $_freesoundCropDuration -c:a aac "$croppedPath"',
      );
      final code = await session.getReturnCode();
      if (!ReturnCode.isSuccess(code)) {
        throw Exception('Crop fail ho gaya');
      }

      if (!mounted) return;
      setState(() {
        _musicFile = File(croppedPath);
        _musicFileName = '${track['name']} — ${track['artist']} (CC0)';
        _musicStartOffsetSec = 0; // already cropped to exactly the chosen window
        _musicFromFreesound = true;
        _freesoundCropTrack = null;
        _freesoundResults = [];
        _freesoundQueryCtrl.clear();
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Music add nahi ho paya: $e')),
      );
    } finally {
      if (mounted) setState(() => _isCroppingFreesoundTrack = false);
    }
  }

  // ─── Extra clips: pick / remove / reorder ───
  // Reuses the same `file_selector` picker as background music, just
  // with a video type group instead of an audio one — no new
  // dependency needed.
  Future<void> _pickExtraClip() async {
    setState(() => _isPickingClip = true);
    try {
      const XTypeGroup videoTypeGroup = XTypeGroup(
        label: 'video',
        extensions: ['mp4', 'mov', 'm4v', 'webm', 'mkv', 'avi', '3gp'],
        uniformTypeIdentifiers: ['public.movie'],
      );
      final XFile? picked = await openFile(acceptedTypeGroups: [videoTypeGroup]);
      if (!mounted) return;
      if (picked != null) {
        setState(() => _extraClips.add(File(picked.path)));
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Clip select nahi ho paya: $e')),
      );
    } finally {
      if (mounted) setState(() => _isPickingClip = false);
    }
  }

  void _removeExtraClip(int index) {
    setState(() => _extraClips.removeAt(index));
  }

  // ─── Manual montage clips: pick (photos AND videos in one go) ───
  Future<void> _pickManualClips() async {
    setState(() => _isPickingManualClip = true);
    try {
      const XTypeGroup mediaTypeGroup = XTypeGroup(
        label: 'media',
        extensions: [
          'jpg', 'jpeg', 'png', 'heic', 'webp',
          'mp4', 'mov', 'm4v', 'webm', 'mkv', 'avi', '3gp',
        ],
      );
      final List<XFile> picked = await openFiles(acceptedTypeGroups: [mediaTypeGroup]);
      if (!mounted || picked.isEmpty) return;
      final newClips = <_ManualClip>[];
      for (final x in picked) {
        final file = File(x.path);
        final isVideo = isVideoFile(file);
        double? durationSec;
        if (isVideo) {
          durationSec = await _probeAudioDurationSec(file.path);
        }
        newClips.add(_ManualClip(
          file: file,
          isVideo: isVideo,
          sourceDurationSec: durationSec,
        ));
      }
      if (!mounted) return;
      setState(() => _manualClips.addAll(newClips));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Photo/video add nahi ho paya: $e')),
      );
    } finally {
      if (mounted) setState(() => _isPickingManualClip = false);
    }
  }

  void _removeManualClip(int index) {
    setState(() => _manualClips.removeAt(index));
  }

  void _reorderManualClips(int oldIndex, int newIndex) {
    setState(() {
      if (newIndex > oldIndex) newIndex -= 1;
      final item = _manualClips.removeAt(oldIndex);
      _manualClips.insert(newIndex, item);
    });
  }

  // ─── Setup: downscale original so editing stays smooth ───
  Future<void> _prepareWorkingFile() async {
    try {
      final bytes = await widget.file.readAsBytes();
      final resized = await compute(_downscaleForEditing, bytes);
      final dir = await getTemporaryDirectory();
      final path = '${dir.path}/edit_working_${DateTime.now().millisecondsSinceEpoch}.jpg';
      final file = await File(path).writeAsBytes(resized);
      final dims = await compute(_dimsOfBytes, resized);
      if (!mounted) return;
      setState(() {
        _workingFile = file;
        _workingWidth = dims[0];
        _workingHeight = dims[1];
        _isPreparing = false;
      });
      _detectFaces();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _workingFile = widget.file;
        _isPreparing = false;
      });
    }
  }

  // ─── Face Filters: one-shot ML Kit detection on the working image ───
  Future<void> _detectFaces() async {
    final detector = _faceDetector;
    if (detector == null) return;
    setState(() => _isDetectingFaces = true);
    try {
      final faces = await detector.processImage(InputImage.fromFile(_workingFile));
      if (!mounted) return;
      setState(() {
        _detectedFaces = faces;
        _isDetectingFaces = false;
      });
    } catch (_) {
      // No hard failure — Face Filters chips just fall back to
      // center-placement (same as a normal sticker) when this fails.
      if (!mounted) return;
      setState(() => _isDetectingFaces = false);
    }
  }

  // Drops a face-filter emoji sticker, smart-positioned onto the
  // first detected face when one exists (eyes for `atEyes` filters
  // like glasses, otherwise centered on/above the face box), and
  // falls back to a normal centered sticker placement otherwise.
  // Reuses the exact same _TextOverlay + isSticker machinery as any
  // other sticker, so drag/pinch/rotate/undo all work unchanged.
  void _addFaceFilterSticker(String emoji, {bool atEyes = false, bool aboveHead = false}) {
    _captureHistory();
    double dx = 0.5, dy = 0.5;
    double fontSize = 100;
    if (_detectedFaces.isNotEmpty && _workingWidth > 1 && _workingHeight > 1) {
      final box = _detectedFaces.first.boundingBox;
      final cx = (box.left + box.right) / 2 / _workingWidth;
      final faceH = box.height / _workingHeight;
      fontSize = (box.width / _workingWidth) * 260; // scale glyph to face width
      if (atEyes) {
        final leftEye = _detectedFaces.first.landmarks[FaceLandmarkType.leftEye];
        final rightEye = _detectedFaces.first.landmarks[FaceLandmarkType.rightEye];
        if (leftEye != null && rightEye != null) {
          dx = (leftEye.position.x + rightEye.position.x) / 2 / _workingWidth;
          dy = (leftEye.position.y + rightEye.position.y) / 2 / _workingHeight;
        } else {
          dx = cx;
          dy = (box.top + box.height * 0.35) / _workingHeight;
        }
      } else if (aboveHead) {
        dx = cx;
        dy = (box.top - box.height * 0.25) / _workingHeight;
      } else {
        dx = cx;
        dy = (box.top + box.height * 0.5) / _workingHeight;
      }
    }
    setState(() {
      final id = 'face_${_overlayIdCounter++}';
      _textOverlays.add(_TextOverlay(
        id: id,
        text: emoji,
        style: _TextStyleKind.classic,
        color: Colors.white,
        fontSize: fontSize,
        dx: dx.clamp(0.05, 0.95),
        dy: dy.clamp(0.05, 0.95),
        isSticker: true,
      ));
      _selectedOverlayId = id;
    });
  }

  // ─── Live/Dynamic stickers: Time / Date / Battery % ───
  String _dynamicStickerText(_DynamicKind kind) {
    final now = DateTime.now();
    switch (kind) {
      case _DynamicKind.none:
        return '';
      case _DynamicKind.time:
        final h = now.hour % 12 == 0 ? 12 : now.hour % 12;
        final m = now.minute.toString().padLeft(2, '0');
        return '${h.toString()}:$m ${now.hour >= 12 ? 'PM' : 'AM'}';
      case _DynamicKind.date:
        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return '${now.day} ${months[now.month - 1]}';
      case _DynamicKind.battery:
        return _batteryLevel != null ? '🔋 $_batteryLevel%' : '🔋 --%';
    }
  }

  Future<void> _addDynamicSticker(_DynamicKind kind) async {
    if (kind == _DynamicKind.battery && _batteryLevel == null) {
      try {
        _batteryLevel = await Battery().batteryLevel;
      } catch (_) {}
    }
    if (!mounted) return;
    _captureHistory();
    setState(() {
      final id = 'dyn_${_overlayIdCounter++}';
      _textOverlays.add(_TextOverlay(
        id: id,
        text: _dynamicStickerText(kind),
        style: _TextStyleKind.highlight, // pill-shaped background — reads as a "widget" not typed text
        color: Colors.black,
        color2: Colors.white,
        fontSize: 40,
        isSticker: false,
        dynamicKind: kind,
      ));
      _selectedOverlayId = id;
    });
  }

  // Called every 30s by _dynamicStickerTimer — refreshes the displayed
  // text of any live overlays without touching position/style, and
  // without polluting the undo stack (this is a passive tick, not a
  // user edit).
  Future<void> _refreshDynamicStickers() async {
    if (!mounted || _textOverlays.every((o) => o.dynamicKind == _DynamicKind.none)) return;
    if (_textOverlays.any((o) => o.dynamicKind == _DynamicKind.battery)) {
      try {
        _batteryLevel = await Battery().batteryLevel;
      } catch (_) {}
    }
    if (!mounted) return;
    setState(() {
      for (final o in _textOverlays) {
        if (o.dynamicKind != _DynamicKind.none) o.text = _dynamicStickerText(o.dynamicKind);
      }
    });
  }

  // ─── Undo/Redo history ───
  bool get _canReset =>
      _brightness != 0 ||
      _contrast != 0 ||
      _saturation != 0 ||
      _warmth != 0 ||
      _vignette != 0 ||
      _selectedFilterIndex != 0 ||
      _filterIntensity != 100;

  _HistorySnapshot _currentSnapshot() => _HistorySnapshot(
        file: _workingFile,
        workingWidth: _workingWidth,
        workingHeight: _workingHeight,
        filterIndex: _selectedFilterIndex,
        filterIntensity: _filterIntensity,
        brightness: _brightness,
        contrast: _contrast,
        saturation: _saturation,
        warmth: _warmth,
        vignette: _vignette,
        overlays: _textOverlays.map((o) => o.copy()).toList(),
        strokes: _drawStrokes.map((s) => s.copy()).toList(),
      );

  void _captureHistory() {
    _undoStack.add(_currentSnapshot());
    if (_undoStack.length > 20) _undoStack.removeAt(0);
    _redoStack.clear();
  }

  void _applySnapshot(_HistorySnapshot s) {
    _workingFile = s.file;
    _workingWidth = s.workingWidth;
    _workingHeight = s.workingHeight;
    _selectedFilterIndex = s.filterIndex;
    _filterIntensity = s.filterIntensity;
    _brightness = s.brightness;
    _contrast = s.contrast;
    _saturation = s.saturation;
    _warmth = s.warmth;
    _vignette = s.vignette;
    _textOverlays
      ..clear()
      ..addAll(s.overlays.map((o) => o.copy()));
    _drawStrokes
      ..clear()
      ..addAll(s.strokes.map((st) => st.copy()));
    _selectedOverlayId = null;
  }

  void _undo() {
    if (_undoStack.isEmpty) return;
    final current = _currentSnapshot();
    final prev = _undoStack.removeLast();
    _redoStack.add(current);
    setState(() => _applySnapshot(prev));
  }

  void _redo() {
    if (_redoStack.isEmpty) return;
    final current = _currentSnapshot();
    final next = _redoStack.removeLast();
    _undoStack.add(current);
    setState(() => _applySnapshot(next));
  }

  void _resetAdjustments() {
    if (!_canReset) return;
    _captureHistory();
    setState(() {
      _brightness = 0;
      _contrast = 0;
      _saturation = 0;
      _warmth = 0;
      _vignette = 0;
      _selectedFilterIndex = 0;
      _filterIntensity = 100;
    });
  }

  // ─── Auto-Enhance: one-tap auto-levels ───
  bool _isAutoEnhancing = false;

  Future<void> _autoEnhance() async {
    if (_isPreparing || _isSaving || _isTransforming || _isAutoEnhancing) return;
    setState(() => _isAutoEnhancing = true);
    try {
      final bytes = await _workingFile.readAsBytes();
      final suggestion = await compute(_computeAutoEnhance, bytes);
      if (!mounted) return;
      _captureHistory();
      setState(() {
        _brightness = suggestion[0];
        _contrast = suggestion[1];
        _saturation = suggestion[2];
        _isAutoEnhancing = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _isAutoEnhancing = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Auto-enhance nahi ho paya: $e')),
      );
    }
  }

  // ─── Color matrix helpers (Android/Flutter ColorFilter.matrix convention) ───
  static List<double> get _identity => const [
        1, 0, 0, 0, 0, //
        0, 1, 0, 0, 0, //
        0, 0, 1, 0, 0, //
        0, 0, 0, 1, 0,
      ];

  // Blends matrix `b` toward matrix `a` (identity, typically) by `t`
  // (0 = fully a, 1 = fully b) — powers the filter-intensity slider so
  // a preset can be dialed back instead of being all-or-nothing.
  static List<double> _lerpMatrix(List<double> a, List<double> b, double t) {
    return List<double>.generate(20, (i) => a[i] + (b[i] - a[i]) * t);
  }

  static List<double> _multiply(List<double> a, List<double> b) {
    // Returns the matrix that represents "apply a, then apply b".
    List<double> to5x5(List<double> m) => [...m, 0, 0, 0, 0, 1];
    final ma = to5x5(a);
    final mb = to5x5(b);
    final result = List<double>.filled(25, 0);
    for (int r = 0; r < 5; r++) {
      for (int c = 0; c < 5; c++) {
        double sum = 0;
        for (int k = 0; k < 5; k++) {
          sum += mb[r * 5 + k] * ma[k * 5 + c];
        }
        result[r * 5 + c] = sum;
      }
    }
    return result.sublist(0, 20);
  }

  static List<double> _brightnessMatrix(double value) {
    final t = value / 100 * 255;
    return [
      1, 0, 0, 0, t, //
      0, 1, 0, 0, t, //
      0, 0, 1, 0, t, //
      0, 0, 0, 1, 0,
    ];
  }

  static List<double> _contrastMatrix(double value) {
    final c = 1 + value / 100;
    final t = (1 - c) * 127.5;
    return [
      c, 0, 0, 0, t, //
      0, c, 0, 0, t, //
      0, 0, c, 0, t, //
      0, 0, 0, 1, 0,
    ];
  }

  static List<double> _saturationMatrix(double value) {
    final s = 1 + value / 100;
    const lumR = 0.213, lumG = 0.715, lumB = 0.072;
    return [
      lumR + (1 - lumR) * s, lumG * (1 - s), lumB * (1 - s), 0, 0, //
      lumR * (1 - s), lumG + (1 - lumG) * s, lumB * (1 - s), 0, 0, //
      lumR * (1 - s), lumG * (1 - s), lumB + (1 - lumB) * s, 0, 0, //
      0, 0, 0, 1, 0,
    ];
  }

  static List<double> _tintMatrix(double dr, double dg, double db) {
    return [
      1, 0, 0, 0, dr, //
      0, 1, 0, 0, dg, //
      0, 0, 1, 0, db, //
      0, 0, 0, 1, 0,
    ];
  }

  // Continuous warm/cool shift, independent of the Warm/Cool presets.
  static List<double> _warmthMatrix(double value) {
    final t = value / 100 * 30;
    return _tintMatrix(t, t * 0.35, -t);
  }

  List<_FilterPreset> _buildPresets() {
    final mono = _multiply(_saturationMatrix(-100), _contrastMatrix(8));
    final noir = _multiply(mono, _contrastMatrix(22));
    final vivid = _multiply(_saturationMatrix(45), _contrastMatrix(12));
    final warm = _tintMatrix(18, 6, -18);
    final cool = _tintMatrix(-14, 2, 18);
    final fade = _multiply(_contrastMatrix(-22), _multiply(_brightnessMatrix(10), _saturationMatrix(-18)));
    return [
      _FilterPreset('Normal', _identity),
      _FilterPreset('Vivid', vivid),
      _FilterPreset('Warm', warm),
      _FilterPreset('Cool', cool),
      _FilterPreset('Mono', mono),
      _FilterPreset('Noir', noir),
      _FilterPreset('Fade', fade),
    ];
  }

  List<double> get _liveMatrix {
    var m = _brightnessMatrix(_brightness);
    m = _multiply(m, _contrastMatrix(_contrast));
    m = _multiply(m, _saturationMatrix(_saturation));
    m = _multiply(m, _warmthMatrix(_warmth));
    final presetMatrix = _lerpMatrix(_identity, _presets[_selectedFilterIndex].matrix, _filterIntensity / 100);
    m = _multiply(m, presetMatrix);
    return m;
  }

  // ─── Reframe (pinch-zoom + pan) — resets the InteractiveViewer back
  // to showing the full uncropped frame. Rotate/flip stay as the
  // one-tap quick actions below; there's no modal dialog for either.
  void _resetReframe() {
    if (!_isReframed) return;
    setState(() {
      _reframeScale = 1.0;
      _reframeOffset = Offset.zero;
      _reframeRotation = 0.0;
    });
  }

  // Visible-viewport rect, in the original working image's own
  // fraction (0..1) coordinates — same convention _TextOverlay.dx/dy
  // and _DrawStroke points already use, since the preview box is
  // exactly the image's own extent (fit: contain with matching
  // aspect ratio). This is what export crops the final composited
  // frame down to when the user has pinch-zoomed/panned/rotated to
  // reframe. Rotation itself is baked into the pixels first (see
  // _composeOverlays), so this only ever needs to account for
  // scale+pan — same formula regardless of rotation.
  Rect _reframeCropFraction(double boxW, double boxH) {
    if (!_isReframed) return const Rect.fromLTRB(0, 0, 1, 1);
    final center = Offset(boxW / 2, boxH / 2);
    Offset toContent(Offset screen) => center + (screen - center - _reframeOffset) / _reframeScale;
    final tl = toContent(Offset.zero);
    final br = toContent(Offset(boxW, boxH));
    return Rect.fromLTRB(
      (tl.dx / boxW).clamp(0.0, 1.0),
      (tl.dy / boxH).clamp(0.0, 1.0),
      (br.dx / boxW).clamp(0.0, 1.0),
      (br.dy / boxH).clamp(0.0, 1.0),
    );
  }

  // ─── Quick rotate / flip (no cropper UI needed) ───
  Future<void> _quickTransform(Uint8List Function(Uint8List) isolateFn, String tag) async {
    if (_isPreparing || _isSaving || _isTransforming) return;
    setState(() => _isTransforming = true);
    try {
      final bytes = await _workingFile.readAsBytes();
      final transformed = await compute(isolateFn, bytes);
      final dir = await getTemporaryDirectory();
      final path = '${dir.path}/edit_${tag}_${DateTime.now().millisecondsSinceEpoch}.jpg';
      final file = await File(path).writeAsBytes(transformed);
      final dims = await compute(_dimsOfBytes, transformed);
      if (!mounted) return;
      _captureHistory();
      setState(() {
        _workingFile = file;
        _workingWidth = dims[0];
        _workingHeight = dims[1];
        _isTransforming = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _isTransforming = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Transform nahi ho paya: $e')),
      );
    }
  }

  Future<void> _rotateLeft() => _quickTransform(_rotateImage90Ccw, 'rot');
  Future<void> _flipHorizontal() => _quickTransform(_flipImageHorizontal, 'fliph');
  Future<void> _flipVertical() => _quickTransform(_flipImageVertical, 'flipv');

  // ─── Text overlays: add / edit / delete ───
  // ─── Text overlays: add / edit / delete ───
  // Full-screen, Instagram-style text editor: no bottom-sheet form —
  // you type directly over the dimmed photo/first-frame (Stories/Reels
  // text-mode style), seeing the exact chosen style/font/color/
  // alignment live as you type, and pick everything from compact
  // icon bars instead of a scrolling list of labeled rows. Keeps every
  // existing feature (7 styles, 11 fonts, 6 entrance animations, the
  // per-overlay background toggle, gradient 2nd color) — just presented
  // the way Instagram presents its own, smaller set: an "Aa" cycle +
  // alignment toggle up top, a tabbed Style/Font/FX chip strip and the
  // color palette down bottom.
  Future<void> _openAddTextSheet({String? editId}) async {
    final existingIndex = editId == null ? -1 : _textOverlays.indexWhere((o) => o.id == editId);
    final existing = existingIndex == -1 ? null : _textOverlays[existingIndex];
    final controller = TextEditingController(text: existing?.text ?? '');
    final focusNode = FocusNode();
    _TextStyleKind selectedStyle = existing?.style ?? _TextStyleKind.classic;
    Color selectedColor = existing?.color ?? Colors.white;
    Color selectedColor2 = existing?.color2 ?? _accent;
    String selectedFont = existing?.fontFamily ?? 'Roboto';
    _TextAnimKind selectedAnim = existing?.animKind ?? _TextAnimKind.none;
    TextAlign selectedAlign = existing?.textAlign ?? TextAlign.center;
    bool? selectedBgOverride = existing?.showBackground;
    _TextPanel activePanel = _TextPanel.style;

    // Hide the real overlay (if editing one) from its normal spot in
    // the main canvas Stack while this full-screen editor is open —
    // restored the instant it closes, below.
    _hiddenOverlayId = existing?.id;

    // Dimmed photo (or the video's already-extracted first filmstrip
    // thumbnail — no need to spin up a live VideoPlayer just to type
    // text) behind the typing surface, exactly like Stories/Reels.
    final Widget background = _isVideo
        ? ((_videoController?.thumbnails.isNotEmpty ?? false)
            ? Image.memory(_videoController!.thumbnails.first, fit: BoxFit.cover)
            : Container(color: Colors.black))
        : Image.file(_workingFile, fit: BoxFit.cover);

    IconData alignIcon(TextAlign a) {
      if (a == TextAlign.left) return Icons.format_align_left;
      if (a == TextAlign.right) return Icons.format_align_right;
      return Icons.format_align_center;
    }

    TextAlign nextAlign(TextAlign a) {
      if (a == TextAlign.center) return TextAlign.left;
      if (a == TextAlign.left) return TextAlign.right;
      return TextAlign.center;
    }

    _TextStyleKind nextStyle(_TextStyleKind k) {
      final vals = _TextStyleKind.values;
      return vals[(vals.indexOf(k) + 1) % vals.length];
    }

    await Navigator.of(context).push(PageRouteBuilder(
      opaque: true,
      transitionDuration: const Duration(milliseconds: 160),
      reverseTransitionDuration: const Duration(milliseconds: 120),
      transitionsBuilder: (_, anim, __, child) => FadeTransition(opacity: anim, child: child),
      pageBuilder: (routeContext, __, ___) {
        return StatefulBuilder(
          builder: (sheetContext, setSheetState) {
            final screenW = MediaQuery.of(sheetContext).size.width;
            final double liveFontSize = 46 * (screenW / 1000) * (existing?.scale ?? 1.0);

            List<Widget> panelChips() {
              switch (activePanel) {
                case _TextPanel.style:
                  return _TextStyleKind.values
                      .map((kind) => _chip(
                            label: _styleLabel(kind),
                            selected: kind == selectedStyle,
                            onTap: () => setSheetState(() => selectedStyle = kind),
                          ))
                      .toList();
                case _TextPanel.font:
                  return _fontOptions
                      .map((opt) => _chip(
                            label: opt.label,
                            selected: opt.family == selectedFont,
                            onTap: () => setSheetState(() => selectedFont = opt.family),
                            textStyle: _applyFontFamily(opt.family, const TextStyle(color: Colors.white, fontSize: 13)),
                          ))
                      .toList();
                case _TextPanel.anim:
                  return _TextAnimKind.values
                      .map((kind) => _chip(
                            label: _animLabel(kind),
                            selected: kind == selectedAnim,
                            onTap: () => setSheetState(() => selectedAnim = kind),
                          ))
                      .toList();
              }
            }

            void save() {
              final text = controller.text.trim();
              Navigator.pop(routeContext);
              if (text.isEmpty) return;
              _captureHistory();
              setState(() {
                if (existing != null) {
                  existing.text = text;
                  existing.style = selectedStyle;
                  existing.color = selectedColor;
                  existing.color2 = selectedColor2;
                  existing.fontFamily = selectedFont;
                  existing.showBackground = selectedBgOverride;
                  existing.animKind = selectedAnim;
                  existing.textAlign = selectedAlign;
                } else {
                  final id = 'txt_${_overlayIdCounter++}';
                  _textOverlays.add(_TextOverlay(
                    id: id,
                    text: text,
                    style: selectedStyle,
                    color: selectedColor,
                    color2: selectedColor2,
                    fontFamily: selectedFont,
                    showBackground: selectedBgOverride,
                    animKind: selectedAnim,
                    textAlign: selectedAlign,
                  ));
                  _selectedOverlayId = id;
                }
              });
            }

            return Scaffold(
              backgroundColor: Colors.black,
              resizeToAvoidBottomInset: true,
              body: Stack(
                fit: StackFit.expand,
                children: [
                  background,
                  Container(color: Colors.black.withOpacity(0.45)),
                  SafeArea(
                    child: GestureDetector(
                      behavior: HitTestBehavior.opaque,
                      onTap: () => focusNode.requestFocus(),
                      child: Column(
                        children: [
                          // ── Top bar: close · Aa (cycle style) · alignment · Done ──
                          Padding(
                            padding: const EdgeInsets.fromLTRB(6, 4, 12, 4),
                            child: Row(
                              children: [
                                IconButton(
                                  icon: const Icon(Icons.close, color: Colors.white, size: 26),
                                  onPressed: () => Navigator.pop(routeContext),
                                ),
                                if (existing != null)
                                  IconButton(
                                    icon: const Icon(Icons.delete_outline, color: Colors.redAccent, size: 22),
                                    onPressed: () {
                                      Navigator.pop(routeContext);
                                      _deleteOverlay(existing.id);
                                    },
                                  ),
                                const Spacer(),
                                _topBarChip(
                                  onTap: () => setSheetState(() => selectedStyle = nextStyle(selectedStyle)),
                                  child: const Text('Aa', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 15)),
                                ),
                                const SizedBox(width: 8),
                                _topBarChip(
                                  onTap: () => setSheetState(() => selectedAlign = nextAlign(selectedAlign)),
                                  child: Icon(alignIcon(selectedAlign), color: Colors.white, size: 18),
                                ),
                                const SizedBox(width: 12),
                                GestureDetector(
                                  onTap: save,
                                  child: Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                                    decoration: BoxDecoration(color: _primary, borderRadius: BorderRadius.circular(18)),
                                    child: const Text('Done', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 14)),
                                  ),
                                ),
                              ],
                            ),
                          ),
                          // ── Live typing surface — WYSIWYG, exactly what gets baked ──
                          Expanded(
                            child: Center(
                              child: SingleChildScrollView(
                                padding: const EdgeInsets.symmetric(horizontal: 28),
                                child: ConstrainedBox(
                                  constraints: BoxConstraints(maxWidth: screenW - 56),
                                  child: Stack(
                                    alignment: Alignment.center,
                                    children: [
                                      IgnorePointer(
                                        child: _buildStyledText(
                                          controller.text.isEmpty ? ' ' : controller.text,
                                          selectedStyle,
                                          liveFontSize,
                                          selectedColor,
                                          selectedColor2,
                                          fontFamily: selectedFont,
                                          showBackground: selectedBgOverride,
                                          textAlign: selectedAlign,
                                        ),
                                      ),
                                      TextField(
                                        controller: controller,
                                        focusNode: focusNode,
                                        autofocus: true,
                                        maxLines: null,
                                        textAlign: selectedAlign,
                                        cursorColor: selectedColor,
                                        cursorWidth: 3,
                                        onChanged: (_) => setSheetState(() {}),
                                        style: TextStyle(fontSize: liveFontSize, color: Colors.transparent, height: 1.2),
                                        decoration: const InputDecoration(
                                          border: InputBorder.none,
                                          isCollapsed: true,
                                          contentPadding: EdgeInsets.zero,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            ),
                          ),
                          // ── Bottom controls: tabbed Style/Font/FX chips + color palette ──
                          Container(
                            padding: EdgeInsets.only(top: 10, bottom: MediaQuery.of(sheetContext).viewInsets.bottom > 0 ? 10 : 24),
                            decoration: BoxDecoration(
                              gradient: LinearGradient(
                                begin: Alignment.topCenter,
                                end: Alignment.bottomCenter,
                                colors: [Colors.transparent, Colors.black.withOpacity(0.55)],
                              ),
                            ),
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Row(
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    _panelTab('Style', _TextPanel.style, activePanel, (p) => setSheetState(() => activePanel = p)),
                                    const SizedBox(width: 18),
                                    _panelTab('Font', _TextPanel.font, activePanel, (p) => setSheetState(() => activePanel = p)),
                                    const SizedBox(width: 18),
                                    _panelTab('FX', _TextPanel.anim, activePanel, (p) => setSheetState(() => activePanel = p)),
                                    const SizedBox(width: 18),
                                    GestureDetector(
                                      onTap: () => setSheetState(() {
                                        selectedBgOverride = !(selectedBgOverride ?? _styleDefaultBackground(selectedStyle));
                                      }),
                                      child: Container(
                                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                                        decoration: BoxDecoration(
                                          color: (selectedBgOverride ?? _styleDefaultBackground(selectedStyle)) ? _primary : Colors.white10,
                                          borderRadius: BorderRadius.circular(8),
                                        ),
                                        child: const Row(
                                          mainAxisSize: MainAxisSize.min,
                                          children: [
                                            Icon(Icons.crop_din, color: Colors.white, size: 13),
                                            SizedBox(width: 4),
                                            Text('BG', style: TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.w800)),
                                          ],
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                                const SizedBox(height: 10),
                                SizedBox(
                                  height: 40,
                                  child: ListView(
                                    scrollDirection: Axis.horizontal,
                                    padding: const EdgeInsets.symmetric(horizontal: 16),
                                    children: panelChips(),
                                  ),
                                ),
                                const SizedBox(height: 12),
                                // Color strip — Instagram's signature bottom
                                // row; swaps to the gradient's 2nd stop
                                // while that style is active.
                                SizedBox(
                                  height: 34,
                                  child: ListView.separated(
                                    scrollDirection: Axis.horizontal,
                                    padding: const EdgeInsets.symmetric(horizontal: 16),
                                    itemCount: _textColorSwatches.length,
                                    separatorBuilder: (_, __) => const SizedBox(width: 10),
                                    itemBuilder: (context, i) {
                                      final c = _textColorSwatches[i];
                                      final isGradientRow = selectedStyle == _TextStyleKind.gradient;
                                      final sel = isGradientRow ? c.value == selectedColor2.value : c.value == selectedColor.value;
                                      return GestureDetector(
                                        onTap: () => setSheetState(() {
                                          if (isGradientRow) {
                                            selectedColor2 = c;
                                          } else {
                                            selectedColor = c;
                                          }
                                        }),
                                        child: Container(
                                          width: 30,
                                          height: 30,
                                          decoration: BoxDecoration(
                                            color: c,
                                            shape: BoxShape.circle,
                                            border: Border.all(color: sel ? Colors.white : Colors.white30, width: sel ? 3 : 1),
                                          ),
                                        ),
                                      );
                                    },
                                  ),
                                ),
                                if (selectedStyle == _TextStyleKind.gradient)
                                  const Padding(
                                    padding: EdgeInsets.only(top: 4),
                                    child: Text('Color 2 (gradient) — tap a swatch above', style: TextStyle(color: _muted, fontSize: 10)),
                                  ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            );
          },
        );
      },
    ));

    // Route closed (Done/Delete/Close all funnel through Navigator.pop
    // above) — drop the hidden-overlay flag so the real overlay's
    // normal spot in the main canvas Stack shows again (it already has
    // whatever the Done handler wrote to it, if that's how it closed).
    controller.dispose();
    focusNode.dispose();
    if (mounted) {
      setState(() {
        _liveTextPreview = null;
        _hiddenOverlayId = null;
      });
    }
  }

  Widget _topBarChip({required Widget child, required VoidCallback onTap}) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(color: Colors.white.withOpacity(0.15), shape: BoxShape.circle),
        child: child,
      ),
    );
  }

  Widget _panelTab(String label, _TextPanel panel, _TextPanel active, ValueChanged<_TextPanel> onTap) {
    final sel = panel == active;
    return GestureDetector(
      onTap: () => onTap(panel),
      child: Text(
        label,
        style: TextStyle(
          color: sel ? Colors.white : _muted,
          fontSize: 12,
          fontWeight: sel ? FontWeight.w800 : FontWeight.w600,
        ),
      ),
    );
  }

  Widget _chip({required String label, required bool selected, required VoidCallback onTap, TextStyle? textStyle}) {
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected ? _primary : Colors.white10,
            borderRadius: BorderRadius.circular(10),
          ),
          child: Text(
            label,
            style: (textStyle ?? const TextStyle(color: Colors.white, fontSize: 12)).copyWith(
              fontWeight: selected ? FontWeight.w800 : FontWeight.w500,
            ),
          ),
        ),
      ),
    );
  }

  void _deleteOverlay(String id) {
    final idx = _textOverlays.indexWhere((o) => o.id == id);
    if (idx == -1) return;
    _captureHistory();
    setState(() {
      _textOverlays.removeAt(idx);
      if (_selectedOverlayId == id) _selectedOverlayId = null;
    });
  }

  void _deleteSelectedOverlay() {
    final id = _selectedOverlayId;
    if (id == null) return;
    _deleteOverlay(id);
  }

  // ─── Draw / doodle tool: freehand strokes ───
  void _startStroke(Offset fractionalPoint) {
    _captureHistory();
    _activeStroke = _DrawStroke(color: _selectedDrawColor, width: _selectedDrawWidth, points: [fractionalPoint]);
    setState(() => _drawStrokes.add(_activeStroke!));
  }

  void _extendStroke(Offset fractionalPoint) {
    if (_activeStroke == null) return;
    setState(() => _activeStroke!.points.add(fractionalPoint));
  }

  void _endStroke() {
    if (_activeStroke == null) return;
    // Drop accidental taps that never became a real stroke.
    if (_activeStroke!.points.length < 2) {
      setState(() => _drawStrokes.remove(_activeStroke));
      _undoStack.removeLast(); // the capture in _startStroke didn't produce a real change
    }
    _activeStroke = null;
  }

  bool get _canClearDrawing => _drawStrokes.isNotEmpty;

  void _clearDrawing() {
    if (!_canClearDrawing) return;
    _captureHistory();
    setState(() => _drawStrokes.clear());
  }

  // ─── Magic Eraser: mask strokes (not undo-tracked — they're a
  // scratch layer that either gets applied and baked in, or cleared;
  // the *result* of Apply goes through the normal file-history path,
  // same as crop/rotate) ───
  void _startEraserStroke(Offset fractionalPoint) {
    _activeEraserStroke = _DrawStroke(color: Colors.redAccent, width: _eraserBrushWidth, points: [fractionalPoint]);
    setState(() => _eraserStrokes.add(_activeEraserStroke!));
  }

  void _extendEraserStroke(Offset fractionalPoint) {
    if (_activeEraserStroke == null) return;
    setState(() => _activeEraserStroke!.points.add(fractionalPoint));
  }

  void _endEraserStroke() {
    if (_activeEraserStroke == null) return;
    if (_activeEraserStroke!.points.length < 2) {
      setState(() => _eraserStrokes.remove(_activeEraserStroke));
    }
    _activeEraserStroke = null;
  }

  void _clearEraserMask() {
    if (_eraserStrokes.isEmpty) return;
    setState(() => _eraserStrokes.clear());
  }

  // Bakes the painted mask into `_workingFile` permanently via an
  // inpaint isolate pass, then pushes the result through the same
  // file-swap + history flow as crop/rotate/flip (_quickTransform).
  Future<void> _applyMagicEraser() async {
    if (_eraserStrokes.isEmpty || _isPreparing || _isSaving || _isTransforming || _isErasing) return;
    setState(() => _isErasing = true);
    try {
      final bytes = await _workingFile.readAsBytes();
      final maskPoints = _eraserStrokes
          .map((s) => {'width': s.width, 'points': s.points.map((p) => [p.dx, p.dy]).toList()})
          .toList();
      final result = await compute(_magicErase, {'bytes': bytes, 'strokes': maskPoints});
      final dir = await getTemporaryDirectory();
      final path = '${dir.path}/edit_erase_${DateTime.now().millisecondsSinceEpoch}.jpg';
      final file = await File(path).writeAsBytes(result);
      final dims = await compute(_dimsOfBytes, result);
      if (!mounted) return;
      _captureHistory();
      setState(() {
        _workingFile = file;
        _workingWidth = dims[0];
        _workingHeight = dims[1];
        _eraserStrokes.clear();
        _isErasing = false;
      });
      _detectFaces(); // face position may have shifted relative to the erased content
    } catch (e) {
      if (!mounted) return;
      setState(() => _isErasing = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Eraser apply nahi ho paya: $e')),
      );
    }
  }

  // ─── Stickers: emoji picker sheet (reuses _TextOverlay machinery) ───
  static const List<String> _stickerEmojis = [
    '😂', '❤️', '🔥', '✨', '🎉', '😍', '👍', '💯', '🥳', '😎',
    '🤩', '🙌', '💕', '⭐️', '🎊', '👀', '😅', '🤔', '😢', '😮',
    '🙏', '💀', '👑', '🚀', '🌈', '☀️', '🍕', '🎶', '📸', '💥',
  ];

  // Emoji + how it should be smart-placed relative to a detected face.
  static const List<(String emoji, bool atEyes, bool aboveHead)> _faceFilterOptions = [
    ('🐶', false, false), // dog face, centered on the face
    ('🐱', false, false), // cat face, centered on the face
    ('🕶️', true, false), // sunglasses, sat on the eye line
    ('👑', false, true), // crown, floating above the head
    ('🐰', false, false), // bunny face
  ];

  // ─── Your PNG sticker pack: assets/stickers/<file>.png ───
  // Just the pubspec.yaml `assets: - assets/stickers/` line is needed
  // (already in your config) — every file under that folder is
  // available by path, so no per-file listing there.
  static const Map<String, String> _stickerPackCategories = {
    'bye': 'Bye',
    'gm': 'Good Morning',
    'gn': 'Good Night',
    'hi': 'Hi',
    'laugh': 'Laugh',
    'no': 'No',
    'okdone': 'Ok / Done',
    'sorry': 'Sorry',
    'ty': 'Thank You',
    'wup': "What's Up",
  };

  static const Map<String, List<String>> _stickerPackFiles = {
    'bye': ['bear', 'bunny', 'cat', 'cloud', 'frog', 'ghost', 'hand', 'retro', 'skull', 'toast'],
    'gm': ['bear', 'cat', 'cloud', 'coffee', 'flower', 'frog', 'ghost', 'retro', 'sun', 'toast'],
    'gn': ['bear', 'bunny', 'cat', 'cloud', 'frog', 'ghost', 'milk', 'moon', 'retro', 'stars'],
    'hi': ['astronaut', 'cactus', 'catpaw', 'cloud', 'frog', 'ghost', 'monster', 'retro', 'smiley', 'toast'],
    'laugh': ['cat', 'cloud', 'emoji', 'frog', 'ghost', 'hamster', 'meme', 'skull', 'toast', 'villain'],
    'no': ['bear', 'bunny', 'cat', 'cloud', 'frog', 'ghost', 'hand', 'retro', 'skull', 'toast'],
    'okdone': ['bear', 'bunny', 'cat', 'cloud', 'frog', 'ghost', 'hands', 'retro', 'tick', 'toast'],
    'sorry': ['bear', 'cat1', 'cloud', 'cookie', 'frog', 'ghost', 'hands', 'heart', 'puppy', 'retro'],
    'ty': ['bear', 'bunny', 'cat', 'cloud', 'flower', 'frog', 'ghost', 'hands', 'retro', 'toast'],
    'wup': ['alien', 'bear', 'bunny', 'cat', 'cloud', 'frog', 'ghost', 'retro', 'skull', 'toast'],
  };

  String _stickerCategory = 'bye'; // last-selected tab in the "My Stickers" section

  Future<void> _openStickerPicker({String? editId}) async {
    await showModalBottomSheet(
      context: context,
      backgroundColor: _bg,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (sheetContext) {
        return Padding(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Add Sticker', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 16)),
                const SizedBox(height: 14),
                Wrap(
                  spacing: 10,
                  runSpacing: 10,
                  children: _stickerEmojis.map((emoji) {
                    return GestureDetector(
                      onTap: () {
                        Navigator.pop(sheetContext);
                        _addOrReplaceSticker(emoji, editId: editId);
                      },
                      child: Container(
                        width: 46,
                        height: 46,
                        alignment: Alignment.center,
                        decoration: BoxDecoration(color: Colors.white10, borderRadius: BorderRadius.circular(10)),
                        child: Text(emoji, style: const TextStyle(fontSize: 26)),
                      ),
                    );
                  }).toList(),
                ),
                // Your own PNG sticker pack (assets/stickers/) — also
                // valid for editId != null, so double-tapping an
                // existing sticker can swap it to one of these too.
                const SizedBox(height: 20),
                const Text('My Stickers', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 16)),
                const SizedBox(height: 10),
                StatefulBuilder(
                  builder: (sbContext, sbSetState) {
                    final files = _stickerPackFiles[_stickerCategory] ?? const [];
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SizedBox(
                          height: 32,
                          child: ListView.separated(
                            scrollDirection: Axis.horizontal,
                            itemCount: _stickerPackCategories.length,
                            separatorBuilder: (_, __) => const SizedBox(width: 8),
                            itemBuilder: (context, i) {
                              final code = _stickerPackCategories.keys.elementAt(i);
                              final label = _stickerPackCategories[code]!;
                              final selected = code == _stickerCategory;
                              return GestureDetector(
                                onTap: () => sbSetState(() => _stickerCategory = code),
                                child: Container(
                                  padding: const EdgeInsets.symmetric(horizontal: 12),
                                  alignment: Alignment.center,
                                  decoration: BoxDecoration(
                                    color: selected ? _primary : Colors.white10,
                                    borderRadius: BorderRadius.circular(16),
                                  ),
                                  child: Text(
                                    label,
                                    style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: selected ? FontWeight.w800 : FontWeight.w500),
                                  ),
                                ),
                              );
                            },
                          ),
                        ),
                        const SizedBox(height: 12),
                        Wrap(
                          spacing: 10,
                          runSpacing: 10,
                          children: files.map((name) {
                            final assetPath = 'assets/stickers/${_stickerCategory}_$name.png';
                            return GestureDetector(
                              onTap: () {
                                Navigator.pop(sheetContext);
                                _addOrReplaceAssetSticker(assetPath, editId: editId);
                              },
                              child: Container(
                                width: 54,
                                height: 54,
                                padding: const EdgeInsets.all(4),
                                decoration: BoxDecoration(color: Colors.white10, borderRadius: BorderRadius.circular(10)),
                                child: Image.asset(
                                  assetPath,
                                  fit: BoxFit.contain,
                                  errorBuilder: (_, __, ___) => const Icon(Icons.broken_image_outlined, color: _muted, size: 20),
                                ),
                              ),
                            );
                          }).toList(),
                        ),
                      ],
                    );
                  },
                ),
                // Face Filters + Live stickers only make sense as new
                // adds, not as an in-place swap while editing an
                // existing sticker (which just replaces its glyph).
                if (editId == null) ...[
                  const SizedBox(height: 20),
                  Row(
                    children: [
                      const Text('Face Filters', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 16)),
                      const SizedBox(width: 8),
                      if (_isDetectingFaces)
                        const SizedBox(width: 12, height: 12, child: CircularProgressIndicator(strokeWidth: 2, color: _accent))
                      else if (_detectedFaces.isEmpty)
                        const Text('(no face detected — placed centered)', style: TextStyle(color: _muted, fontSize: 11)),
                    ],
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    'Auto-placed on the detected face — drag/pinch/rotate after.',
                    style: TextStyle(color: _muted, fontSize: 11),
                  ),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 10,
                    runSpacing: 10,
                    children: _faceFilterOptions.map((f) {
                      return GestureDetector(
                        onTap: () {
                          Navigator.pop(sheetContext);
                          _addFaceFilterSticker(f.$1, atEyes: f.$2, aboveHead: f.$3);
                        },
                        child: Container(
                          width: 46,
                          height: 46,
                          alignment: Alignment.center,
                          decoration: BoxDecoration(color: _primary.withOpacity(0.18), borderRadius: BorderRadius.circular(10)),
                          child: Text(f.$1, style: const TextStyle(fontSize: 24)),
                        ),
                      );
                    }).toList(),
                  ),
                  const SizedBox(height: 20),
                  const Text('Live Stickers', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 16)),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 10,
                    runSpacing: 10,
                    children: [
                      _dynamicStickerChip(sheetContext, Icons.access_time_rounded, 'Time', _DynamicKind.time),
                      _dynamicStickerChip(sheetContext, Icons.calendar_today_rounded, 'Date', _DynamicKind.date),
                      _dynamicStickerChip(sheetContext, Icons.battery_full_rounded, 'Battery', _DynamicKind.battery),
                    ],
                  ),
                ],
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _dynamicStickerChip(BuildContext sheetContext, IconData icon, String label, _DynamicKind kind) {
    return GestureDetector(
      onTap: () {
        Navigator.pop(sheetContext);
        _addDynamicSticker(kind);
      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(color: Colors.white10, borderRadius: BorderRadius.circular(20)),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 16, color: Colors.white),
            const SizedBox(width: 6),
            Text(label, style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600)),
          ],
        ),
      ),
    );
  }

  void _addOrReplaceSticker(String emoji, {String? editId}) {
    _captureHistory();
    setState(() {
      if (editId != null) {
        final existing = _textOverlays.firstWhere((o) => o.id == editId);
        existing.text = emoji;
        existing.assetPath = null; // in case it was previously a PNG-pack sticker
        existing.imageFile = null; // in case it was previously a gallery photo
      } else {
        final id = 'stk_${_overlayIdCounter++}';
        _textOverlays.add(_TextOverlay(
          id: id,
          text: emoji,
          style: _TextStyleKind.classic,
          color: Colors.white,
          fontSize: 90,
          scale: 1.5, // land noticeably bigger than before — user can still pinch to resize from here
          isSticker: true,
        ));
        _selectedOverlayId = id;
      }
    });
  }

  // Adds (or, when editing, swaps an existing sticker to) a PNG from
  // your assets/stickers/ pack. `text` stays empty since assetPath is
  // what actually renders/exports for these — kept isSticker: true so
  // drag/pinch/rotate/undo/delete are all shared with emoji stickers.
  void _addOrReplaceAssetSticker(String assetPath, {String? editId}) {
    _captureHistory();
    setState(() {
      if (editId != null) {
        final existing = _textOverlays.firstWhere((o) => o.id == editId);
        existing.assetPath = assetPath;
        existing.imageFile = null; // in case it was previously a gallery photo
        existing.text = '';
      } else {
        final id = 'stk_${_overlayIdCounter++}';
        _textOverlays.add(_TextOverlay(
          id: id,
          text: '',
          style: _TextStyleKind.classic,
          color: Colors.white,
          fontSize: 90,
          scale: 1.5, // land noticeably bigger than before — user can still pinch to resize from here
          isSticker: true,
          assetPath: assetPath,
        ));
        _selectedOverlayId = id;
      }
    });
  }

  // ─── Add Photo: pick one or more images from the device and drop
  // them on the canvas as overlays — no crop/insert dialog, they land
  // centered (each new one nudged slightly so a multi-pick doesn't
  // stack in one exact spot) and are immediately drag/pinch/rotate-
  // able, exactly like an emoji or PNG-pack sticker. Free to overlap/
  // layer on top of each other or the base photo — "override" is just
  // whichever one the user drags on top, same z-order as add order.
  // Reuses the same `file_selector` picker already used for music and
  // extra-clip selection — no new dependency needed.
  bool _isPickingImages = false;

  Future<void> _pickGalleryImages() async {
    if (_isPickingImages) return;
    setState(() => _isPickingImages = true);
    try {
      const XTypeGroup imageTypeGroup = XTypeGroup(
        label: 'images',
        extensions: ['jpg', 'jpeg', 'png', 'webp', 'heic', 'bmp'],
        uniformTypeIdentifiers: ['public.image'],
      );
      final List<XFile> picked = await openFiles(acceptedTypeGroups: [imageTypeGroup]);
      if (!mounted || picked.isEmpty) return;
      _captureHistory();
      setState(() {
        for (int i = 0; i < picked.length; i++) {
          final id = 'img_${_overlayIdCounter++}';
          // Small staggered offset per added photo so a multi-select
          // doesn't land as one indistinguishable stack — still fully
          // draggable to wherever the user wants right after.
          final stagger = (i % 5) * 0.04;
          _textOverlays.add(_TextOverlay(
            id: id,
            text: '',
            style: _TextStyleKind.classic,
            color: Colors.white,
            fontSize: 90,
            scale: 1.3, // gallery photos already render large (2.6x base) — a smaller bump keeps them from swamping the canvas
            isSticker: true,
            imageFile: File(picked[i].path),
            dx: (0.5 + stagger).clamp(0.15, 0.85),
            dy: (0.5 + stagger).clamp(0.15, 0.85),
          ));
          _selectedOverlayId = id;
        }
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Photo select nahi ho paya: $e')),
      );
    } finally {
      if (mounted) setState(() => _isPickingImages = false);
    }
  }

  String _styleLabel(_TextStyleKind kind) {
    switch (kind) {
      case _TextStyleKind.classic:
        return 'Classic';
      case _TextStyleKind.bold:
        return 'Bold';
      case _TextStyleKind.neon:
        return 'Neon';
      case _TextStyleKind.typewriter:
        return 'Typewriter';
      case _TextStyleKind.highlight:
        return 'Highlight';
      case _TextStyleKind.gradient:
        return 'Gradient';
      case _TextStyleKind.outline:
        return 'Outline';
    }
  }

  String _animLabel(_TextAnimKind kind) {
    switch (kind) {
      case _TextAnimKind.none:
        return 'None';
      case _TextAnimKind.fadeIn:
        return 'Fade';
      case _TextAnimKind.popIn:
        return 'Pop';
      case _TextAnimKind.bounce:
        return 'Bounce';
      case _TextAnimKind.pulse:
        return 'Pulse';
      case _TextAnimKind.slideUp:
        return 'Slide Up';
    }
  }

  // ─── Animated FX (live filter effects, shared by image + video) ───
  String _fxLabel(_FxKind kind) {
    switch (kind) {
      case _FxKind.none:
        return 'None';
      case _FxKind.colorShift:
        return 'Color Shift';
      case _FxKind.glitch:
        return 'Glitch';
      case _FxKind.sparkle:
        return 'Sparkle';
      case _FxKind.filmGrain:
        return 'Film Grain';
      case _FxKind.lightLeak:
        return 'Light Leak';
      case _FxKind.rainbow:
        return 'Rainbow';
      case _FxKind.snow:
        return 'Snow';
      case _FxKind.vignettePulse:
        return 'Vignette Pulse';
      case _FxKind.bokeh:
        return 'Bokeh';
      case _FxKind.confetti:
        return 'Confetti';
      case _FxKind.embers:
        return 'Embers';
      case _FxKind.neonPulse:
        return 'Neon Pulse';
      case _FxKind.rain:
        return 'Rain';
      case _FxKind.lensFlare:
        return 'Lens Flare';
      case _FxKind.fireworks:
        return 'Fireworks';
      case _FxKind.bubbles:
        return 'Bubbles';
      case _FxKind.hearts:
        return 'Hearts';
      case _FxKind.fireflies:
        return 'Fireflies';
      case _FxKind.aurora:
        return 'Aurora';
      case _FxKind.petals:
        return 'Petals';
      case _FxKind.staticNoise:
        return 'Static';
      case _FxKind.frost:
        return 'Frost';
      case _FxKind.smoke:
        return 'Smoke';
      case _FxKind.lightning:
        return 'Lightning';
    }
  }

  IconData _fxIcon(_FxKind kind) {
    switch (kind) {
      case _FxKind.none:
        return Icons.block_rounded;
      case _FxKind.colorShift:
        return Icons.gradient_rounded;
      case _FxKind.glitch:
        return Icons.blur_on_rounded;
      case _FxKind.sparkle:
        return Icons.auto_awesome_rounded;
      case _FxKind.filmGrain:
        return Icons.movie_filter_rounded;
      case _FxKind.lightLeak:
        return Icons.wb_sunny_rounded;
      case _FxKind.rainbow:
        return Icons.color_lens_rounded;
      case _FxKind.snow:
        return Icons.ac_unit_rounded;
      case _FxKind.vignettePulse:
        return Icons.vignette_rounded;
      case _FxKind.bokeh:
        return Icons.blur_circular_rounded;
      case _FxKind.confetti:
        return Icons.celebration_rounded;
      case _FxKind.embers:
        return Icons.local_fire_department_rounded;
      case _FxKind.neonPulse:
        return Icons.bolt_rounded;
      case _FxKind.rain:
        return Icons.water_drop_rounded;
      case _FxKind.lensFlare:
        return Icons.wb_iridescent_rounded;
      case _FxKind.fireworks:
        return Icons.flare_rounded;
      case _FxKind.bubbles:
        return Icons.bubble_chart_rounded;
      case _FxKind.hearts:
        return Icons.favorite_rounded;
      case _FxKind.fireflies:
        return Icons.emoji_nature_rounded;
      case _FxKind.aurora:
        return Icons.waves_rounded;
      case _FxKind.petals:
        return Icons.local_florist_rounded;
      case _FxKind.staticNoise:
        return Icons.tv_rounded;
      case _FxKind.frost:
        return Icons.grain_rounded;
      case _FxKind.smoke:
        return Icons.cloud_rounded;
      case _FxKind.lightning:
        return Icons.flash_on_rounded;
    }
  }

  // ffmpeg -vf expression for baking the animated effect into an
  // exported video over its full (trimmed) duration. Returns null
  // for "none" — skip the second export pass entirely.
  String? _fxFilterString(_FxKind kind) {
    switch (kind) {
      case _FxKind.none:
        return null;
      case _FxKind.colorShift:
        return "hue=h='40*sin(2*PI*t/3)':s=1.3";
      case _FxKind.glitch:
        return "noise=alls=25:allf=t+u,hue=h='18*sin(2*PI*t*5)'";
      case _FxKind.sparkle:
        return "eq=brightness='0.06*sin(2*PI*t*6)':saturation=1.15,hue=h='12*sin(2*PI*t*4)'";
      case _FxKind.filmGrain:
        // Vintage-VHS look: constant photographic grain plus a slow
        // contrast/brightness flicker, and a mild desaturation so it
        // reads as "old film" rather than just "noisy".
        return "noise=alls=22:allf=t,eq=contrast=1.08:brightness='0.02*sin(2*PI*t*9)':saturation=0.9";
      case _FxKind.lightLeak:
        // Warm cinematic light-leak pulse — pushes reds/yellows up and
        // blues down on a slow breathing cycle, with a gentle overall
        // brightness lift so it reads as a leak rather than a tint.
        return "curves=r='0/0.05 0.5/0.65 1/1':b='0/0 0.5/0.32 1/0.85',eq=brightness='0.04+0.03*sin(2*PI*t/4)':saturation=1.1";
      case _FxKind.rainbow:
        // Fast, highly-saturated hue cycling stands in for the moving
        // rainbow band seen in the live preview — ffmpeg has no cheap
        // way to sweep an actual gradient band across frames, so this
        // is an approximation, same spirit as the other bakes above.
        return "hue=h='360*mod(t*0.6,1)':s=1.5,eq=contrast=1.08";
      case _FxKind.snow:
        // Falling snow can't be cheaply expressed as an ffmpeg filter
        // chain, so export approximates the mood with a cool blue-white
        // tint and a light, snow-flurry-like flicker.
        return "curves=b='0/0.08 0.5/0.62 1/1',eq=brightness='0.015*sin(2*PI*t*4)':saturation=0.95";
      case _FxKind.vignettePulse:
        // Ffmpeg's own `vignette` filter accepts a time expression for
        // its angle, which reads as a breathing/pulsing vignette when
        // driven by a sine — a direct (not approximated) match for the
        // live preview's radial pulse.
        return "vignette=PI/4+PI/4*sin(2*PI*t/3)";
      case _FxKind.bokeh:
        // Soft floating light orbs aren't cheaply expressible in
        // ffmpeg's filter language either; export approximates with a
        // gentle warm brightness/saturation breathing pulse.
        return "eq=brightness='0.03*sin(2*PI*t*1.5)':saturation=1.08";
      case _FxKind.confetti:
        // Falling colored confetti has no ffmpeg equivalent; export
        // approximates the festive mood with a punchy, gently
        // oscillating saturation/contrast lift.
        return "eq=saturation='1.2+0.15*sin(2*PI*t*3)':contrast=1.05";
      case _FxKind.embers:
        // Rising warm particles approximated with a fire-toned curve
        // shift (reds/greens up, blues down) plus a slow flicker.
        return "curves=r='0/0.05 0.5/0.6 1/1':g='0/0 0.5/0.25 1/0.7':b='0/0 1/0.6',eq=brightness='0.02*sin(2*PI*t*2)'";
      case _FxKind.neonPulse:
        // Pulsing color/contrast lift stands in for the animated
        // glowing border seen in the live preview.
        return "eq=saturation='1.3+0.2*sin(2*PI*t*2)':contrast='1.1+0.05*sin(2*PI*t*2)'";
      case _FxKind.rain:
        // Cool, slightly darker tint with a fast subtle flicker
        // approximates the mood of falling rain streaks.
        return "curves=b='0/0.05 0.5/0.55 1/0.95',eq=brightness='-0.02+0.01*sin(2*PI*t*8)':saturation=0.85";
      case _FxKind.lensFlare:
        // A moving specular highlight can't be cheaply drawn in
        // ffmpeg's filter graph; export approximates with a warm
        // highlight curve and a slow brightness breathing pulse.
        return "curves=r='0/0.1 0.5/0.7 1/1':g='0/0.05 0.5/0.6 1/1':b='0/0 0.5/0.4 1/0.9',eq=brightness='0.03*sin(2*PI*t*1.5)'";
      case _FxKind.fireworks:
        // Bursting particles approximated with punchy saturation and
        // periodic brightness pops.
        return "eq=brightness='0.05*abs(sin(2*PI*t*0.8))':saturation=1.3";
      case _FxKind.bubbles:
        // Rising translucent bubbles approximated with a cool,
        // gently breathing brightness/blue lift.
        return "curves=b='0/0.05 0.5/0.55 1/0.95',eq=brightness='0.02*sin(2*PI*t*2)'";
      case _FxKind.hearts:
        // Warm pink pulse stands in for floating heart particles.
        return "curves=r='0/0.1 0.5/0.65 1/1':b='0/0.05 0.5/0.4 1/0.9',eq=saturation='1.15+0.1*sin(2*PI*t*2)'";
      case _FxKind.fireflies:
        // Warm glow flicker approximates wandering firefly points.
        return "eq=brightness='0.02*sin(2*PI*t*3)':saturation=1.1";
      case _FxKind.aurora:
        // Slow hue drift across greens/purples stands in for flowing
        // aurora ribbons.
        return "hue=h='30*sin(2*PI*t/5)':s=1.2,eq=brightness=0.02";
      case _FxKind.petals:
        // Soft warm/pink saturation breathing approximates falling
        // petals.
        return "eq=saturation='1.1+0.08*sin(2*PI*t*2)'";
      case _FxKind.staticNoise:
        // A direct match — ffmpeg's own noise filter plus a
        // desaturating contrast lift gives real TV-static texture,
        // not just an approximation.
        return "noise=alls=40:allf=t+u,eq=contrast=1.1:saturation=0.3";
      case _FxKind.frost:
        // Cool edge-darkening vignette plus a blue-leaning curve
        // approximates icy edges creeping inward.
        return "vignette=PI/5,curves=b='0/0.1 0.5/0.6 1/1'";
      case _FxKind.smoke:
        // Soft desaturation/contrast dip approximates drifting haze.
        return "eq=saturation='0.9-0.05*sin(2*PI*t*1.5)':contrast=0.95";
      case _FxKind.lightning:
        // Sudden brief brightness spikes approximate flash strikes.
        return "eq=contrast=1.15:brightness='0.08*pow(abs(sin(2*PI*t*0.65)),12)'";
    }
  }

  // ffmpeg's `atempo` filter only accepts factors in [0.5, 2.0], so a
  // speed outside that range (e.g. 3x) needs to be split into chained
  // atempo instances that multiply together to the target speed, while
  // keeping audio pitch natural (unlike simply speeding up the raw PCM).
  static String _atempoChain(double speed) {
    final factors = <double>[];
    double remaining = speed;
    while (remaining > 2.0) {
      factors.add(2.0);
      remaining /= 2.0;
    }
    while (remaining < 0.5) {
      factors.add(0.5);
      remaining /= 0.5;
    }
    factors.add(remaining);
    return factors.map((f) => 'atempo=$f').join(',');
  }

  // ffmpeg -vf expression for baking in the chosen 90°-step rotation.
  // transpose=1 is 90° clockwise, transpose=2 is 90° counter-clockwise;
  // 180° is just clockwise applied twice. Returns null for "no rotation"
  // so the trim pass skips -vf entirely (cheaper, avoids re-encoding
  // video that doesn't need it).
  static String? _rotateFilterString(int turns) {
    switch (turns % 4) {
      case 1:
        return 'transpose=1';
      case 2:
        return 'transpose=1,transpose=1';
      case 3:
        return 'transpose=2';
      default:
        return null;
    }
  }

  static Color _contrastingTextColor(Color bg) {
    return bg.computeLuminance() > 0.5 ? Colors.black : Colors.white;
  }

  // Reads the video stream's width/height via ffprobe — used to scale
  // every extra clip to the main clip's resolution before concat, so
  // mismatched-resolution clips don't break the concat filter.
  static Future<List<int>?> _probeDimensions(String path) async {
    try {
      final session = await FFprobeKit.getMediaInformation(path);
      final info = session.getMediaInformation();
      if (info == null) return null;
      for (final stream in info.getStreams()) {
        if (stream.getType() == 'video') {
          final w = stream.getWidth();
          final h = stream.getHeight();
          if (w != null && h != null) return [w, h];
        }
      }
      return null;
    } catch (_) {
      return null;
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // Manual montage render pipeline (the "Add Photos" tab's Done
  // path) — normalize-each-clip-then-xfade-chain, same shape as
  // AutoEditScreen's `_buildMontageVideo`/`_buildClipFilter` (see
  // auto_edit_screen.dart), plus a per-clip pan/zoom/rotate stage
  // upfront so each clip can be individually cropped/reframed before
  // the cover-fill + transition-fx step.
  // ═══════════════════════════════════════════════════════════════
  static const int _mTargetW = 1080;
  static const int _mTargetH = 1920;
  static const int _mTargetFps = 30;
  static const double _mXfadeDur = 0.4;

  // Per-clip crop stage: rotate → zoom (cover-fill at coverW*Z x
  // coverH*Z, then crop off the overshoot) → pan (crop the final
  // coverW x coverH window out of the zoomed frame, offset by
  // panX/panY as a fraction of the slack zooming created). Always
  // outputs exactly coverW x coverH, whatever the source's own
  // aspect ratio/orientation.
  String _manualPanZoomRotateFilter(_ManualClip clip, {required int coverW, required int coverH}) {
    final buffer = StringBuffer();
    if (clip.rotationDeg != 0) {
      final rad = (clip.rotationDeg * math.pi / 180).toStringAsFixed(5);
      buffer.write('rotate=$rad:ow=rotw($rad):oh=roth($rad):c=black@0,');
    }
    final zoom = clip.zoom.clamp(1.0, 3.0);
    final scaledW = (coverW * zoom).round();
    final scaledH = (coverH * zoom).round();
    buffer.write('scale=$scaledW:$scaledH:force_original_aspect_ratio=increase,');
    buffer.write('crop=$scaledW:$scaledH,');
    final panX = clip.panX.clamp(-1.0, 1.0).toStringAsFixed(3);
    final panY = clip.panY.clamp(-1.0, 1.0).toStringAsFixed(3);
    buffer.write(
      'crop=$coverW:$coverH:'
      "x='(in_w-$coverW)/2+$panX*(in_w-$coverW)/2':"
      "y='(in_h-$coverH)/2+$panY*(in_h-$coverH)/2'",
    );
    return buffer.toString();
  }

  // Full per-clip filter: pan/zoom/rotate crop, then (for clips with
  // an outgoing transition) the hand-written pixel-fx burned into the
  // tail window only — identical Glitch/RGB-Split/Shake treatment to
  // AutoEditScreen; Flash/Zoom/Slide are handled entirely by xfade's
  // own built-in transition catalog, so they need no extra filter here.
  String _manualClipFilter(_ManualClip clip, {required _TransitionKind? outgoing, required double winStart, required double winEnd}) {
    if (outgoing == _TransitionKind.shake) {
      // Shake wiggles the crop itself, so it needs headroom around the
      // target canvas — cover-fill/pan/zoom at target+40 first, then
      // wiggle-crop down to the exact target size only inside the
      // transition window.
      final panZoom = _manualPanZoomRotateFilter(clip, coverW: _mTargetW + 40, coverH: _mTargetH + 40);
      return '$panZoom,'
          'crop=$_mTargetW:$_mTargetH:'
          "x='20+if(between(t,$winStart,$winEnd),14*sin(20*(t-$winStart)),0)':"
          "y='20+if(between(t,$winStart,$winEnd),14*cos(24*(t-$winStart)),0)',"
          'fps=$_mTargetFps,format=yuv420p';
    }

    final panZoom = _manualPanZoomRotateFilter(clip, coverW: _mTargetW, coverH: _mTargetH);
    final base = '$panZoom,fps=$_mTargetFps,format=yuv420p';
    final enable = "enable='between(t,${winStart.toStringAsFixed(3)},${winEnd.toStringAsFixed(3)})'";
    switch (outgoing) {
      case _TransitionKind.rgbSplit:
        return '$base,rgbashift=rh=8:bh=-8:rv=2:bv=-2:$enable';
      case _TransitionKind.glitch:
        return '$base,rgbashift=rh=10:bh=-10:$enable,noise=alls=25:allf=t+u:$enable';
      case _TransitionKind.flash:
      case _TransitionKind.zoom:
      case _TransitionKind.slide:
      case null:
        return base;
      case _TransitionKind.shake:
        return base; // unreachable — handled above
    }
  }

  // A clip's own slot length in the montage, BEFORE the xfade overlap
  // tail: for photos, whatever duration was set; for videos, the
  // probed natural length divided by the clip's own speed.
  double _manualSlotSec(_ManualClip clip) {
    if (!clip.isVideo) return clip.duration.inMilliseconds / 1000.0;
    final natural = clip.sourceDurationSec ?? 3.0;
    final speed = clip.speed.clamp(0.1, 10.0);
    return math.max(0.3, natural / speed);
  }

  // Normalizes every clip to the fixed montage canvas (pan/zoom/
  // rotate + cover-fill, at each clip's own slot duration) then
  // chains them with ffmpeg's `xfade` filter — same offset math as
  // AutoEditScreen's `_buildMontageVideo` (see the comment there):
  // every non-last clip is rendered `slot + xfadeDur` long so the
  // NEXT xfade has real tail material to blend with, and each xfade's
  // `offset` is just the running prefix-sum of slot durations.
  Future<File> _buildManualClipsMontage(
    List<_ManualClip> clips,
    Directory tempDir, {
    required void Function(double progress, String label) onProgress,
  }) async {
    final n = clips.length;
    final normalizedPaths = <String>[];

    for (int i = 0; i < n; i++) {
      onProgress(i / (n + 1), 'Clip ${i + 1}/$n taiyar ho raha hai…');
      final clip = clips[i];
      final isLast = i == n - 1;
      final slotSec = _manualSlotSec(clip);
      final durSec = slotSec + (isLast ? 0 : _mXfadeDur);
      final outPath = '${tempDir.path}/manual_norm_${i}_${DateTime.now().microsecondsSinceEpoch}.mp4';

      final winStart = math.max(0.0, durSec - _mXfadeDur);
      final vf = _manualClipFilter(clip, outgoing: isLast ? null : clip.transitionOut, winStart: winStart, winEnd: durSec);

      String cmd;
      if (clip.isVideo) {
        final speed = clip.speed.clamp(0.1, 10.0);
        final speedVf = speed != 1.0 ? 'setpts=PTS/$speed,' : '';
        // tpad freezes the last frame if the (speed-adjusted) source
        // is shorter than durSec, so every normalized clip is
        // guaranteed exactly durSec long — required for the xfade
        // offset math below to hold.
        cmd = '-y -i "${clip.file.path}" -an '
            '-vf "$speedVf$vf,tpad=stop_mode=clone:stop_duration=$durSec" '
            '-t $durSec -r $_mTargetFps "$outPath"';
      } else {
        cmd = '-y -loop 1 -t $durSec -i "${clip.file.path}" '
            '-vf "$vf" -r $_mTargetFps "$outPath"';
      }

      final session = await FFmpegKit.execute(cmd);
      if (!ReturnCode.isSuccess(await session.getReturnCode())) {
        throw Exception('Clip ${i + 1} normalize fail ho gaya');
      }
      normalizedPaths.add(outPath);
    }

    if (n == 1) return File(normalizedPaths.first);

    onProgress(n / (n + 1), 'Transitions blend ho rahe hain…');
    final inputs = normalizedPaths.map((p) => '-i "$p"').join(' ');
    final filters = <String>[];
    double cumSlotSec = _manualSlotSec(clips[0]);
    String lastLabel = '0:v';

    for (int i = 1; i < n; i++) {
      final transition = clips[i - 1].transitionOut.xfadeName;
      final outLabel = i == n - 1 ? 'vout' : 'v$i';
      filters.add(
        '[$lastLabel][$i:v]xfade=transition=$transition:duration=$_mXfadeDur:'
        'offset=${cumSlotSec.toStringAsFixed(3)}[$outLabel]',
      );
      lastLabel = outLabel;
      if (i < n - 1) cumSlotSec += _manualSlotSec(clips[i]);
    }

    final outPath = '${tempDir.path}/manual_montage_${DateTime.now().microsecondsSinceEpoch}.mp4';
    final session = await FFmpegKit.execute(
      '-y $inputs -filter_complex "${filters.join(';')}" -map "[vout]" '
      '-r $_mTargetFps -pix_fmt yuv420p "$outPath"',
    );
    if (!ReturnCode.isSuccess(await session.getReturnCode())) {
      throw Exception('Transition chain fail ho gaya');
    }
    return File(outPath);
  }

  // ─── "Add Photos" Done path — bake the primary media (exactly like
  // the single-clip export, minus its own music-attach step), put it
  // at the front of the manual clip list, render the transition
  // montage, then attach music (if any is set) exactly once. ───
  Future<void> _onDoneManualMontage() async {
    setState(() {
      _isSaving = true;
      _exportProgress = 0;
    });
    try {
      final dir = await getTemporaryDirectory();
      final stamp = DateTime.now().millisecondsSinceEpoch;

      File primaryFile;
      bool primaryIsVideo;
      double? primaryNaturalDurationSec; // pre-speed length, videos only

      if (_isVideo) {
        final controller = _videoController;
        if (controller == null) throw Exception('Video controller ready nahi hai');
        final startSec = controller.startTrim.inMilliseconds / 1000.0;
        final trimDurSec = controller.trimmedDuration.inMilliseconds / 1000.0;
        final rotateFilter = _rotateFilterString(controller.rotationTurns);
        final vf = rotateFilter != null ? ' -vf "$rotateFilter"' : '';
        final trimOutPath = '${dir.path}/manual_primary_trim_$stamp.mp4';
        final trimSession = await FFmpegKit.execute(
          '-y -ss $startSec -i "${widget.file.path}" -t $trimDurSec$vf -an "$trimOutPath"',
        );
        if (!ReturnCode.isSuccess(await trimSession.getReturnCode())) {
          throw Exception('Primary clip trim fail ho gaya');
        }
        File outFile = File(trimOutPath);

        final fxFilter = _fxFilterString(_selectedFx);
        if (fxFilter != null) {
          final fxOutPath = '${dir.path}/manual_primary_fx_$stamp.mp4';
          final fxSession = await FFmpegKit.execute('-y -i "${outFile.path}" -vf "$fxFilter" -an "$fxOutPath"');
          if (!ReturnCode.isSuccess(await fxSession.getReturnCode())) {
            throw Exception('Primary clip FX fail ho gaya');
          }
          outFile = File(fxOutPath);
        }

        primaryFile = outFile;
        primaryIsVideo = true;
        primaryNaturalDurationSec = trimDurSec;
      } else {
        final bytes = await _workingFile.readAsBytes();
        final matrix = _liveMatrix;
        final baked = await compute(_bakeImage, {
          'bytes': bytes,
          'matrix': matrix,
          'vignette': _vignette,
        });
        final needsCompositing = _textOverlays.isNotEmpty ||
            _selectedFx != _FxKind.none ||
            _drawStrokes.isNotEmpty ||
            (_isReframed && _reframeRotation != 0);
        var finalBytes = needsCompositing ? await _composeOverlays(baked) : baked;
        if (_isReframed) {
          final cropRect = _reframeCropFraction(_lastBoxW, _lastBoxH);
          finalBytes = await compute(_cropToFraction, {
            'bytes': finalBytes,
            'rect': [cropRect.left, cropRect.top, cropRect.right, cropRect.bottom],
          });
        }
        final jpgOutPath = '${dir.path}/manual_primary_$stamp.jpg';
        primaryFile = await File(jpgOutPath).writeAsBytes(finalBytes);
        primaryIsVideo = false;
      }

      final allClips = <_ManualClip>[
        _ManualClip(
          file: primaryFile,
          isVideo: primaryIsVideo,
          duration: _primaryClipDuration,
          speed: primaryIsVideo ? _videoSpeed : 1.0,
          sourceDurationSec: primaryNaturalDurationSec,
          transitionOut: _primaryTransitionOut,
        ),
        ..._manualClips,
      ];

      final silentMontage = await _buildManualClipsMontage(allClips, dir, onProgress: (p, label) {
        if (mounted) setState(() => _exportProgress = p * 0.85);
      });

      File finalFile = silentMontage;

      if (_musicFile != null) {
        final totalSec = allClips.fold<double>(0, (sum, c) => sum + _manualSlotSec(c));
        final musicOutPath = '${dir.path}/manual_final_$stamp.mp4';
        final session = await FFmpegKit.execute(
          '-y -i "${silentMontage.path}" -stream_loop -1 -ss $_musicStartOffsetSec -i "${_musicFile!.path}" '
          '-map 0:v -map 1:a -c:v copy -af "volume=$_musicVolume" -shortest -t $totalSec "$musicOutPath"',
        );
        if (!ReturnCode.isSuccess(await session.getReturnCode())) {
          throw Exception('Music attach fail ho gaya');
        }
        finalFile = File(musicOutPath);
      }

      if (!mounted) return;
      setState(() => _exportProgress = 1.0);
      Navigator.pop(context, finalFile);
    } catch (e) {
      if (!mounted) return;
      setState(() => _isSaving = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Montage export fail ho gaya: $e')),
      );
    }
  }

  // ─── Export trimmed (+ cover) video via ffmpeg and pop ───
  Future<void> _onDoneVideo() async {
    final controller = _videoController;
    if (controller == null) return;
    setState(() {
      _isSaving = true;
      _exportProgress = 0;
    });
    try {
      final dir = await getTemporaryDirectory();
      final trimOutPath = '${dir.path}/edited_trim_${DateTime.now().millisecondsSinceEpoch}.mp4';
      final startSec = controller.startTrim.inMilliseconds / 1000.0;
      final durSec = controller.trimmedDuration.inMilliseconds / 1000.0;
      final rotateFilter = _rotateFilterString(controller.rotationTurns);
      final vf = rotateFilter != null ? ' -vf "$rotateFilter"' : '';
      // `-ss` before `-i` is a fast (keyframe) seek — matches the
      // scrub-while-drag preview and is plenty accurate for trim
      // handles snapped to filmstrip frames.
      final trimCommand = '-y -ss $startSec -i "${widget.file.path}" -t $durSec$vf -c:a aac "$trimOutPath"';

      final fxFilter = _fxFilterString(_selectedFx);
      final needsSpeed = _videoSpeed != 1.0;
      final needsMusic = _musicFile != null;
      final needsCover = controller.thumbnailsReady && controller.thumbnails.isNotEmpty;
      final totalMs = controller.trimmedDuration.inMilliseconds;
      // Progress is split evenly across however many passes actually
      // run (trim is always pass 1; FX, speed, music, and cover-attach
      // each add an extra pass), so the UI's percentage stays roughly
      // linear regardless of how many effects are stacked.
      final totalPasses = 1 +
          (_boomerangEnabled ? 1 : 0) +
          (_extraClips.isNotEmpty ? 1 : 0) +
          (fxFilter != null ? 1 : 0) +
          (needsSpeed ? 1 : 0) +
          (needsMusic ? 1 : 0) +
          (needsCover ? 1 : 0);
      final passSpan = 1.0 / totalPasses;
      int passesDone = 0;

      final trimmedFile = await _runFfmpegPass(
        trimCommand,
        trimOutPath,
        totalMs,
        progressStart: 0,
        progressSpan: passSpan,
      );
      if (trimmedFile == null) {
        if (!mounted) return;
        setState(() => _isSaving = false);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Video trim export fail ho gaya')),
        );
        return;
      }
      passesDone++;

      File outFile = trimmedFile;

      if (_boomerangEnabled) {
        // Reuses the same (already-trimmed) clip as two inputs: the
        // second one gets `reverse`/`areverse`'d, then both are
        // concatenated forward-then-backward for the classic
        // Boomerang loop.
        final dir = await getTemporaryDirectory();
        final boomerangOutPath = '${dir.path}/edited_boomerang_${DateTime.now().millisecondsSinceEpoch}.mp4';
        final boomerangFile = await _runFfmpegPass(
          '-y -i "${outFile.path}" -i "${outFile.path}" -filter_complex '
          '"[1:v]reverse[rv];[1:a]areverse[ra];[0:v][0:a][rv][ra]concat=n=2:v=1:a=1[outv][outa]" '
          '-map "[outv]" -map "[outa]" "$boomerangOutPath"',
          boomerangOutPath,
          totalMs,
          progressStart: passesDone * passSpan,
          progressSpan: passSpan,
        );
        if (boomerangFile == null) {
          if (!mounted) return;
          setState(() => _isSaving = false);
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Boomerang bake fail ho gaya')),
          );
          return;
        }
        outFile = boomerangFile;
        passesDone++;
      }

      if (_extraClips.isNotEmpty) {
        // Probe the main clip's resolution so every extra clip can be
        // scaled + letterbox-padded to match before concat — the
        // concat filter breaks if input streams don't share the same
        // dimensions.
        final mainDims = await _probeDimensions(outFile.path);
        final mainW = mainDims != null ? mainDims[0] : controller.video.value.size.width.round();
        final mainH = mainDims != null ? mainDims[1] : controller.video.value.size.height.round();
        final allClipPaths = [outFile.path, ..._extraClips.map((f) => f.path)];
        final inputArgs = allClipPaths.map((p) => '-i "$p"').join(' ');
        final scaleChains = StringBuffer();
        final concatLabels = StringBuffer();
        for (int i = 0; i < allClipPaths.length; i++) {
          scaleChains.write(
            '[$i:v]scale=$mainW:$mainH:force_original_aspect_ratio=decrease,'
            'pad=$mainW:$mainH:(ow-iw)/2:(oh-ih)/2,setsar=1[v$i];',
          );
          concatLabels.write('[v$i][$i:a]');
        }
        final filterComplex = '$scaleChains${concatLabels}concat=n=${allClipPaths.length}:v=1:a=1[outv][outa]';
        final dir = await getTemporaryDirectory();
        final clipsOutPath = '${dir.path}/edited_clips_${DateTime.now().millisecondsSinceEpoch}.mp4';
        final clipsFile = await _runFfmpegPass(
          '-y $inputArgs -filter_complex "$filterComplex" -map "[outv]" -map "[outa]" "$clipsOutPath"',
          clipsOutPath,
          totalMs,
          progressStart: passesDone * passSpan,
          progressSpan: passSpan,
        );
        if (clipsFile == null) {
          if (!mounted) return;
          setState(() => _isSaving = false);
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Clips jodne me fail ho gaya')),
          );
          return;
        }
        outFile = clipsFile;
        passesDone++;
      }

      if (fxFilter != null) {
        final dir = await getTemporaryDirectory();
        final fxOutPath = '${dir.path}/edited_fx_${DateTime.now().millisecondsSinceEpoch}.mp4';
        final fxFile = await _runFfmpegPass(
          '-y -i "${outFile.path}" -vf "$fxFilter" -c:a copy "$fxOutPath"',
          fxOutPath,
          totalMs,
          progressStart: passesDone * passSpan,
          progressSpan: passSpan,
        );
        if (fxFile == null) {
          if (!mounted) return;
          setState(() => _isSaving = false);
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('FX bake fail ho gaya')),
          );
          return;
        }
        outFile = fxFile;
        passesDone++;
      }

      if (needsSpeed) {
        final dir = await getTemporaryDirectory();
        final speedOutPath = '${dir.path}/edited_speed_${DateTime.now().millisecondsSinceEpoch}.mp4';
        final speedFile = await _runFfmpegPass(
          '-y -i "${outFile.path}" -vf "setpts=PTS/$_videoSpeed" -af "${_atempoChain(_videoSpeed)}" "$speedOutPath"',
          speedOutPath,
          totalMs,
          progressStart: passesDone * passSpan,
          progressSpan: passSpan,
        );
        if (speedFile == null) {
          if (!mounted) return;
          setState(() => _isSaving = false);
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Speed adjust fail ho gaya')),
          );
          return;
        }
        outFile = speedFile;
        passesDone++;
      }

      if (needsMusic) {
        final dir = await getTemporaryDirectory();
        final musicOutPath = '${dir.path}/edited_music_${DateTime.now().millisecondsSinceEpoch}.mp4';
        // -stream_loop -1 repeats the music track so it always covers
        // the clip even if the chosen song is shorter; -shortest then
        // cuts the mixed output back down to the video's own length.
        // -ss before the music -i applies the chosen start-offset so
        // playback begins from wherever the user picked in the track.
        final filterComplex = _keepOriginalAudio
            ? '[1:a]volume=$_musicVolume[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[aout]'
            : '[1:a]volume=$_musicVolume[aout]';
        final musicFile = await _runFfmpegPass(
          '-y -i "${outFile.path}" -stream_loop -1 -ss $_musicStartOffsetSec -i "${_musicFile!.path}" '
          '-filter_complex "$filterComplex" -map 0:v -map "[aout]" -c:v copy -shortest "$musicOutPath"',
          musicOutPath,
          totalMs,
          progressStart: passesDone * passSpan,
          progressSpan: passSpan,
        );
        if (musicFile == null) {
          if (!mounted) return;
          setState(() => _isSaving = false);
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Music mix fail ho gaya')),
          );
          return;
        }
        outFile = musicFile;
        passesDone++;
      }

      if (needsCover) {
        // Bake the chosen filmstrip frame in as the mp4's
        // "attached_pic" cover stream — this is what file managers,
        // share sheets, and most players show as the video's
        // thumbnail, without touching a single playable video frame.
        final coverJpgPath = '${dir.path}/edited_cover_frame_${DateTime.now().millisecondsSinceEpoch}.jpg';
        final coverOutPath = '${dir.path}/edited_final_${DateTime.now().millisecondsSinceEpoch}.mp4';
        final coverSec = ((controller.coverPosition - controller.startTrim).inMilliseconds / 1000.0)
            .clamp(0.0, durSec > 0.05 ? durSec - 0.05 : 0.0);
        await FFmpegKit.execute('-y -ss $coverSec -i "${outFile.path}" -frames:v 1 "$coverJpgPath"');
        final coveredFile = await _runFfmpegPass(
          '-y -i "${outFile.path}" -i "$coverJpgPath" -map 0 -map 1 -c copy -disposition:v:1 attached_pic "$coverOutPath"',
          coverOutPath,
          totalMs,
          progressStart: passesDone * passSpan,
          progressSpan: passSpan,
        );
        // Non-fatal if this pass fails (some ffmpeg builds are picky
        // about attached_pic on certain containers) — keep the last
        // good export rather than blocking the whole save on it.
        if (coveredFile != null) outFile = coveredFile;
      }

      if (!mounted) return;
      Navigator.pop(context, outFile);
    } catch (e) {
      if (!mounted) return;
      setState(() => _isSaving = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Video trim save nahi ho paya: $e')),
      );
    }
  }

  // Runs one ffmpeg command, resolving to the output File on success
  // (or null on failure), reporting progress into _exportProgress
  // scaled to [progressStart, progressStart + progressSpan].
  Future<File?> _runFfmpegPass(
    String command,
    String outputPath,
    int totalMs, {
    required double progressStart,
    required double progressSpan,
  }) {
    final completer = Completer<File?>();
    FFmpegKit.executeAsync(
      command,
      (session) async {
        final code = await session.getReturnCode();
        completer.complete(ReturnCode.isSuccess(code) ? File(outputPath) : null);
      },
      null,
      (stats) {
        if (totalMs <= 0 || !mounted) return;
        final passProgress = (stats.getTime() / totalMs).clamp(0.0, 1.0);
        setState(() => _exportProgress = progressStart + passProgress * progressSpan);
      },
    );
    return completer.future;
  }

  // ─── Auto Edit entry point: multi-media montage. Result is held as
  // a PREVIEW (see _autoEditResult) — nothing is final until the user
  // taps "Use This" (or the screen's own Done), and "Remove" discards
  // it with zero side effects on the media being edited ───
  Future<void> _openAutoEdit() async {
    final File? result = await Navigator.push<File>(
      context,
      MaterialPageRoute(builder: (_) => const AutoEditScreen()),
    );
    if (result == null || !mounted) return;

    _autoEditPreviewController?.dispose();
    final controller = VideoPlayerController.file(result);
    await controller.initialize();
    controller.setLooping(true);
    controller.play();
    if (!mounted) return;
    setState(() {
      _autoEditResult = result;
      _autoEditPreviewController = controller;
    });
  }

  // Discards the Auto Edit preview and returns to normal editing —
  // nothing was ever baked into _workingFile, so there's nothing else
  // to undo.
  void _removeAutoEdit() {
    _autoEditPreviewController?.dispose();
    setState(() {
      _autoEditResult = null;
      _autoEditPreviewController = null;
    });
  }

  // Confirms the Auto Edit montage as the final export — same pop
  // contract as a normal Done tap.
  void _useAutoEditResult() {
    if (_autoEditResult != null) Navigator.pop(context, _autoEditResult);
  }

  // ─── Bake filters/adjustments (+ text) into the final file and pop ───
  Future<void> _onDone() async {
    // 🔥 NAYA — "Add Photos" se kam se kam ek clip juda hai to poora
    // manual montage banega (primary + added clips + transitions +
    // music), single-clip export ki jagah. See _onDoneManualMontage.
    if (_manualClips.isNotEmpty) return _onDoneManualMontage();
    if (_isVideo) return _onDoneVideo();
    setState(() => _isSaving = true);
    try {
      final bytes = await _workingFile.readAsBytes();
      final matrix = _liveMatrix;
      final baked = await compute(_bakeImage, {
        'bytes': bytes,
        'matrix': matrix,
        'vignette': _vignette,
      });

      final needsCompositing = _textOverlays.isNotEmpty ||
          _selectedFx != _FxKind.none ||
          _drawStrokes.isNotEmpty ||
          (_isReframed && _reframeRotation != 0);
      var finalBytes = needsCompositing ? await _composeOverlays(baked) : baked;

      // Pinch-zoom/pan reframe — crop the fully-composited frame (so
      // text/stickers/draw strokes, which are positioned relative to
      // the *full* original image, stay correctly placed) down to
      // whatever the user actually framed on screen. This is the last
      // step, mirroring how the live preview transforms the whole
      // canvas as one unit.
      if (_isReframed) {
        final cropRect = _reframeCropFraction(_lastBoxW, _lastBoxH);
        finalBytes = await compute(_cropToFraction, {'bytes': finalBytes, 'rect': [cropRect.left, cropRect.top, cropRect.right, cropRect.bottom]});
      }

      final dir = await getTemporaryDirectory();
      final stamp = DateTime.now().millisecondsSinceEpoch;
      final jpgOutPath = '${dir.path}/edited_$stamp.jpg';
      final jpgOutFile = await File(jpgOutPath).writeAsBytes(finalBytes);

      // 🔥 NAYA — agar music attach ki gayi hai (device pick ya Freesound
      // crop), toh static JPEG audio carry nahi kar sakti, isliye photo
      // ko ek chhoti silent-video-with-audio (mp4) me bake karte hain —
      // Instagram photo-post-with-music jaisa hi. Caller (isVideoFile
      // check) ye already .mp4 extension se video attachment treat kar
      // lega, koi extra wiring nahi chahiye.
      if (_musicFile != null) {
        final clipDuration = await _probeAudioDurationSec(_musicFile!.path) ?? _freesoundCropDuration;
        final mp4OutPath = '${dir.path}/edited_music_$stamp.mp4';
        final session = await FFmpegKit.execute(
          '-y -loop 1 -i "$jpgOutPath" -i "${_musicFile!.path}" '
          '-vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -c:v libx264 -tune stillimage '
          '-pix_fmt yuv420p -c:a aac -b:a 128k -shortest -t $clipDuration "$mp4OutPath"',
        );
        final code = await session.getReturnCode();
        if (!mounted) return;
        if (!ReturnCode.isSuccess(code)) {
          setState(() => _isSaving = false);
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Music ke saath export fail ho gaya, bina music try karo')),
          );
          return;
        }
        Navigator.pop(context, File(mp4OutPath));
        return;
      }

      if (!mounted) return;
      Navigator.pop(context, jpgOutFile);
    } catch (e) {
      if (!mounted) return;
      setState(() => _isSaving = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Edit save nahi ho paya: $e')),
      );
    }
  }

  // Reads an audio (or video) file's duration via ffprobe — used to
  // size the still-image-+-music export to exactly the cropped clip's
  // length instead of guessing.
  static Future<double?> _probeAudioDurationSec(String path) async {
    try {
      final session = await FFprobeKit.getMediaInformation(path);
      final info = session.getMediaInformation();
      final durationStr = info?.getDuration();
      if (durationStr == null) return null;
      return double.tryParse(durationStr);
    } catch (_) {
      return null;
    }
  }

  // Composites text overlays and/or the animated FX (baked at its
  // current loop phase — a still JPEG can't loop, so export freezes
  // one representative frame of the effect) onto the already
  // color-baked JPEG using dart:ui. Must run on the main isolate
  // (Canvas/TextPainter require the Flutter engine bindings), so this
  // stays out of `compute`.
  Future<Uint8List> _composeOverlays(Uint8List bakedJpgBytes) async {
    final codec = await ui.instantiateImageCodec(bakedJpgBytes);
    final frame = await codec.getNextFrame();
    final baseImage = frame.image;
    final w = baseImage.width.toDouble();
    final h = baseImage.height.toDouble();

    // Preload every distinct PNG-pack sticker used, once, before
    // drawing — decoding is async but Canvas drawing below isn't.
    final assetPaths = _textOverlays.map((o) => o.assetPath).whereType<String>().toSet();
    final assetImages = <String, ui.Image>{};
    for (final path in assetPaths) {
      try {
        final data = await rootBundle.load(path);
        final assetCodec = await ui.instantiateImageCodec(data.buffer.asUint8List());
        final assetFrame = await assetCodec.getNextFrame();
        assetImages[path] = assetFrame.image;
      } catch (_) {
        // Missing/renamed asset — falls back to skipping that sticker
        // in the export rather than crashing the whole save.
      }
    }

    // Preload every user-picked gallery image overlay (added via "Add
    // Photo" — multiple images dropped on the canvas, drag/pinch/rotate
    // to arrange/overlap, same machinery as PNG-pack stickers above).
    final galleryPaths = _textOverlays.map((o) => o.imageFile?.path).whereType<String>().toSet();
    final galleryImages = <String, ui.Image>{};
    for (final path in galleryPaths) {
      try {
        final data = await File(path).readAsBytes();
        final galleryCodec = await ui.instantiateImageCodec(data);
        final galleryFrame = await galleryCodec.getNextFrame();
        galleryImages[path] = galleryFrame.image;
      } catch (_) {
        // Missing/moved file — skip that image in the export rather
        // than crashing the whole save.
      }
    }

    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder, Rect.fromLTWH(0, 0, w, h));

    // Reframe rotation (two-finger rotate, part of the same pinch-zoom
    // gesture as the crop below — no button) is baked in first, as an
    // outer transform around everything drawn below (image, FX, draw
    // strokes, text/image overlays), rotating around the frame's own
    // center and clipped to the same w x h canvas — exactly mirroring
    // how the live preview's Transform wraps the whole Stack. The crop
    // step later (_reframeCropFraction / _cropToFraction) then only
    // has to account for scale+pan, since rotation is already applied
    // to these pixels.
    if (_isReframed && _reframeRotation != 0) {
      canvas.translate(w / 2, h / 2);
      canvas.rotate(_reframeRotation);
      canvas.translate(-w / 2, -h / 2);
    }

    canvas.drawImage(baseImage, Offset.zero, Paint());

    if (_selectedFx != _FxKind.none) {
      _drawFxEffect(canvas, Size(w, h), _selectedFx, _fxAnimController.value);
    }

    for (final stroke in _drawStrokes) {
      _paintDrawStrokeOnCanvas(canvas, stroke, w, h);
    }

    for (final o in _textOverlays) {
      final fontSize = o.fontSize * (w / 1000) * o.scale;
      canvas.save();
      canvas.translate(o.dx * w, o.dy * h);
      canvas.rotate(o.rotation);
      final assetImage = o.assetPath != null ? assetImages[o.assetPath] : null;
      final galleryImage = o.imageFile != null ? galleryImages[o.imageFile!.path] : null;
      final overlayImage = assetImage ?? galleryImage;
      if (overlayImage != null) {
        // Gallery photos default to a larger on-canvas size than emoji/
        // pack stickers (fontSize * 1.3) since they're meant to cover
        // real area of the frame, not sit like a small sticker.
        final size = fontSize * (o.imageFile != null ? 2.6 : 1.3);
        final aspect = overlayImage.width / overlayImage.height;
        final dstW = aspect >= 1 ? size : size * aspect;
        final dstH = aspect >= 1 ? size / aspect : size;
        final dst = Rect.fromCenter(center: Offset.zero, width: dstW, height: dstH);
        canvas.drawImageRect(
          overlayImage,
          Rect.fromLTWH(0, 0, overlayImage.width.toDouble(), overlayImage.height.toDouble()),
          dst,
          Paint()..filterQuality = FilterQuality.high,
        );
      } else {
        _paintOverlayText(canvas, o, fontSize);
      }
      canvas.restore();
    }

    final picture = recorder.endRecording();
    final composed = await picture.toImage(w.round(), h.round());
    final byteData = await composed.toByteData(format: ui.ImageByteFormat.png);
    final pngBytes = byteData!.buffer.asUint8List();
    return compute(_pngToJpg, pngBytes);
  }

  void _paintOverlayText(Canvas canvas, _TextOverlay o, double fontSize) {
    if (o.isSticker) {
      _paintSimpleText(canvas, o.text, fontSize, Colors.white, FontWeight.normal);
      return;
    }
    final bg = o.showBackground ?? _styleDefaultBackground(o.style);
    // Typewriter defaults to the monospace family unless the user
    // explicitly picked a different one (see the matching comment in
    // _buildStyledText — same "default family unless overridden" rule
    // so the live preview and the baked export always agree).
    final typewriterFamily = o.fontFamily == 'Roboto' ? 'Courier Prime' : o.fontFamily;
    switch (o.style) {
      case _TextStyleKind.classic:
        if (bg) _paintBgChip(canvas, o.text, fontSize, o.fontFamily, FontWeight.w600);
        _paintSimpleText(
          canvas,
          o.text,
          fontSize,
          o.color,
          FontWeight.w600,
          shadows: const [Shadow(color: Colors.black54, blurRadius: 4, offset: Offset(0, 1))],
          fontFamily: o.fontFamily,
          textAlign: o.textAlign,
        );
        break;
      case _TextStyleKind.bold:
        if (bg) _paintBgChip(canvas, o.text, fontSize, o.fontFamily, FontWeight.w900);
        _paintSimpleText(canvas, o.text, fontSize, Colors.black, FontWeight.w900, strokeWidth: fontSize * 0.08, fontFamily: o.fontFamily, textAlign: o.textAlign);
        _paintSimpleText(canvas, o.text, fontSize, o.color, FontWeight.w900, fontFamily: o.fontFamily, textAlign: o.textAlign);
        break;
      case _TextStyleKind.neon:
        if (bg) _paintBgChip(canvas, o.text, fontSize, o.fontFamily, FontWeight.w800);
        _paintSimpleText(
          canvas,
          o.text,
          fontSize,
          o.color,
          FontWeight.w800,
          shadows: [
            Shadow(color: o.color.withOpacity(0.9), blurRadius: fontSize * 0.35),
            Shadow(color: o.color.withOpacity(0.6), blurRadius: fontSize * 0.7),
          ],
          fontFamily: o.fontFamily,
          textAlign: o.textAlign,
        );
        break;
      case _TextStyleKind.typewriter:
        if (bg) {
          _paintPillText(canvas, o.text, fontSize, o.color, Colors.black.withOpacity(0.55), fontFamily: typewriterFamily, radius: 4, textAlign: o.textAlign);
        } else {
          _paintSimpleText(canvas, o.text, fontSize, o.color, FontWeight.w500, fontFamily: typewriterFamily, textAlign: o.textAlign);
        }
        break;
      case _TextStyleKind.highlight:
        if (bg) {
          _paintPillText(
            canvas,
            o.text,
            fontSize,
            _contrastingTextColor(o.color),
            o.color,
            fontFamily: o.fontFamily,
            radius: fontSize * 0.2,
            bold: true,
            textAlign: o.textAlign,
          );
        } else {
          _paintSimpleText(canvas, o.text, fontSize, o.color, FontWeight.w700, fontFamily: o.fontFamily, textAlign: o.textAlign);
        }
        break;
      case _TextStyleKind.gradient:
        if (bg) _paintBgChip(canvas, o.text, fontSize, o.fontFamily, FontWeight.w800);
        _paintGradientText(canvas, o.text, fontSize, o.color, o.color2 ?? _accent, fontFamily: o.fontFamily, textAlign: o.textAlign);
        break;
      case _TextStyleKind.outline:
        if (bg) _paintBgChip(canvas, o.text, fontSize, o.fontFamily, FontWeight.w800);
        _paintSimpleText(
          canvas,
          o.text,
          fontSize,
          o.color,
          FontWeight.w800,
          strokeWidth: fontSize * 0.06,
          fontFamily: o.fontFamily,
          textAlign: o.textAlign,
        );
        break;
    }
  }

  // Soft translucent rounded chip sized to the text, drawn *behind* it
  // for styles that don't have their own pill background (Classic,
  // Bold, Neon, Gradient, Outline) when the user's turned the
  // Background toggle on for one of them.
  void _paintBgChip(Canvas canvas, String text, double fontSize, String fontFamily, FontWeight weight) {
    final tp = TextPainter(
      text: TextSpan(text: text, style: _applyFontFamily(fontFamily, TextStyle(fontSize: fontSize, fontWeight: weight))),
      textDirection: TextDirection.ltr,
      textAlign: TextAlign.center,
    )..layout();
    final padH = fontSize * 0.3;
    final padV = fontSize * 0.14;
    final rect = Rect.fromCenter(center: Offset.zero, width: tp.width + padH * 2, height: tp.height + padV * 2);
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, Radius.circular(fontSize * 0.16)),
      Paint()..color = Colors.black.withOpacity(0.4),
    );
  }

  // Gradient fill text: paint opaque white glyphs into an offscreen
  // layer, then multiply a linear gradient over just those pixels
  // (BlendMode.srcIn) — the canvas equivalent of a ShaderMask, so the
  // export bake matches the live ShaderMask preview exactly.
  void _paintGradientText(Canvas canvas, String text, double fontSize, Color c1, Color c2, {String fontFamily = 'Roboto', TextAlign textAlign = TextAlign.center}) {
    final tp = TextPainter(
      text: TextSpan(text: text, style: _applyFontFamily(fontFamily, TextStyle(fontSize: fontSize, fontWeight: FontWeight.w800, color: Colors.white))),
      textDirection: TextDirection.ltr,
      textAlign: textAlign,
    )..layout();
    final rect = Rect.fromCenter(center: Offset.zero, width: tp.width, height: tp.height).inflate(4);
    canvas.saveLayer(rect, Paint());
    tp.paint(canvas, Offset(-tp.width / 2, -tp.height / 2));
    canvas.drawRect(
      rect,
      Paint()
        ..shader = ui.Gradient.linear(rect.topLeft, rect.bottomRight, [c1, c2])
        ..blendMode = BlendMode.srcIn,
    );
    canvas.restore();
  }

  void _paintSimpleText(
    Canvas canvas,
    String text,
    double fontSize,
    Color color,
    FontWeight weight, {
    double? strokeWidth,
    List<Shadow>? shadows,
    String fontFamily = 'Roboto',
    TextAlign textAlign = TextAlign.center,
  }) {
    final baseStyle = TextStyle(
      fontSize: fontSize,
      fontWeight: weight,
      color: strokeWidth != null ? null : color,
      shadows: shadows,
      foreground: strokeWidth != null
          ? (Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = strokeWidth
            ..color = color)
          : null,
    );
    final tp = TextPainter(
      text: TextSpan(text: text, style: _applyFontFamily(fontFamily, baseStyle)),
      textDirection: TextDirection.ltr,
      textAlign: textAlign,
    )..layout();
    tp.paint(canvas, Offset(-tp.width / 2, -tp.height / 2));
  }

  void _paintPillText(
    Canvas canvas,
    String text,
    double fontSize,
    Color textColor,
    Color bgColor, {
    String fontFamily = 'Roboto',
    double radius = 6,
    bool bold = false,
    TextAlign textAlign = TextAlign.center,
  }) {
    final baseStyle = TextStyle(fontSize: fontSize, color: textColor, fontWeight: bold ? FontWeight.w700 : FontWeight.w500);
    final tp = TextPainter(
      text: TextSpan(text: text, style: _applyFontFamily(fontFamily, baseStyle)),
      textDirection: TextDirection.ltr,
      textAlign: textAlign,
    )..layout();
    final padH = fontSize * 0.35;
    final padV = fontSize * 0.15;
    final rect = Rect.fromCenter(center: Offset.zero, width: tp.width + padH * 2, height: tp.height + padV * 2);
    final rrect = RRect.fromRectAndRadius(rect, Radius.circular(radius));
    canvas.drawRRect(rrect, Paint()..color = bgColor);
    tp.paint(canvas, Offset(-tp.width / 2, -tp.height / 2));
  }

  @override
  Widget build(BuildContext context) {
    if (_autoEditResult != null) return _buildAutoEditReview();
    final busy = _isPreparing || _isSaving || _isTransforming;
    return Scaffold(
      backgroundColor: _bg,
      appBar: AppBar(
        backgroundColor: _bg,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.close_rounded),
          onPressed: _isSaving ? null : () => Navigator.pop(context),
        ),
        title: Text(_isVideo ? 'Trim' : 'Edit', style: const TextStyle(fontWeight: FontWeight.w700)),
        actions: [
          // Entry point into the multi-media montage flow (pick several
          // photos/videos, beat-synced music, glitch/flash/zoom/shake/
          // rgb-split/slide transitions) — see auto_edit_screen.dart.
          // On success it hands back a finished .mp4, so we pop THIS
          // screen with that file, same contract as a normal Done tap.
          IconButton(
            tooltip: 'Auto Edit',
            icon: const Icon(Icons.auto_awesome_rounded),
            onPressed: busy ? null : _openAutoEdit,
          ),
          if (!_isVideo && _isReframed)
            TextButton.icon(
              onPressed: busy ? null : _resetReframe,
              icon: const Icon(Icons.zoom_out_map_rounded, color: Colors.white, size: 18),
              label: const Text('Reset zoom', style: TextStyle(color: Colors.white)),
            ),
          Padding(
            padding: const EdgeInsets.only(right: 12, left: 4),
            child: _buildDoneButton(busy),
          ),
        ],
      ),
      body: FadeTransition(
        opacity: _entranceFade,
        child: ScaleTransition(
          scale: _entranceScale,
          child: _isPreparing
          ? const Center(child: CircularProgressIndicator(color: _primary))
          : _isVideo
              ? _buildVideoBody()
              : Column(
              children: [
                // ── Live preview (hold to compare with original, drag text) ──
                Expanded(
                  child: Center(
                    child: AspectRatio(
                      aspectRatio: _workingWidth / _workingHeight,
                      child: GestureDetector(
                        onLongPressStart: (_) => setState(() => _showOriginal = true),
                        onLongPressEnd: (_) => setState(() => _showOriginal = false),
                        onLongPressCancel: () => setState(() => _showOriginal = false),
                        onTap: () => setState(() => _selectedOverlayId = null),
                        child: LayoutBuilder(
                          builder: (context, constraints) {
                            final boxW = constraints.maxWidth;
                            final boxH = constraints.maxHeight;
                            _lastBoxW = boxW;
                            _lastBoxH = boxH;
                            final reframeEnabled = !_showOriginal && _tab != _EditTab.draw && _tab != _EditTab.eraser;
                            return Stack(
                              children: [
                              ClipRect(
                              child: Transform(
                              alignment: Alignment.center,
                              transform: Matrix4.identity()
                                ..translate(_reframeOffset.dx, _reframeOffset.dy)
                                ..scale(_reframeScale)
                                ..rotateZ(_reframeRotation),
                              child: Stack(
                              alignment: Alignment.center,
                              clipBehavior: Clip.hardEdge,
                              children: [
                                // ── Reframe: pinch to zoom, drag to pan, twist with two
                                // fingers to rotate — all one gesture, no buttons, no
                                // modal — background-only layer (same pattern as the
                                // Draw/Eraser layers further down), so it never competes
                                // with per-overlay dragging.
                                if (reframeEnabled)
                                  Positioned.fill(
                                    child: GestureDetector(
                                      behavior: HitTestBehavior.translucent,
                                      onScaleStart: (d) {
                                        _reframeBaseScale = _reframeScale;
                                        _reframeBaseOffset = _reframeOffset;
                                        _reframeBaseRotation = _reframeRotation;
                                        // GLOBAL focal point, not localFocalPoint — this
                                        // GestureDetector lives *inside* the very Transform
                                        // it's driving, so localFocalPoint gets re-measured
                                        // through the live-updating scale/rotation on every
                                        // frame (a feedback loop) and the pinch/pan drifts
                                        // away from your fingers mid-gesture. focalPoint is
                                        // in screen space and stays stable regardless of how
                                        // much we've already zoomed/rotated this gesture.
                                        _reframeGestureStartFocal = d.focalPoint;
                                        setState(() => _isReframing = true);
                                      },
                                      onScaleUpdate: (d) {
                                        final newScale = (_reframeBaseScale * d.scale).clamp(1.0, 5.0);
                                        final focalDelta = d.focalPoint - _reframeGestureStartFocal;
                                        setState(() {
                                          _reframeScale = newScale;
                                          _reframeOffset = _clampReframeOffset(_reframeBaseOffset + focalDelta, newScale, boxW, boxH);
                                          _reframeRotation = _reframeBaseRotation + d.rotation;
                                        });
                                      },
                                      onScaleEnd: (_) => setState(() => _isReframing = false),
                                    ),
                                  ),
                                _showOriginal
                                    ? Image.file(widget.file, fit: BoxFit.contain)
                                    : ColorFiltered(
                                        colorFilter: ColorFilter.matrix(_liveMatrix),
                                        child: Image.file(_workingFile, fit: BoxFit.contain),
                                      ),
                                if (!_showOriginal && _vignette > 0)
                                  Positioned.fill(
                                    child: IgnorePointer(
                                      child: DecoratedBox(
                                        decoration: BoxDecoration(
                                          gradient: RadialGradient(
                                            center: Alignment.center,
                                            radius: 0.9,
                                            stops: const [0.55, 1.0],
                                            colors: [
                                              Colors.transparent,
                                              Colors.black.withOpacity((_vignette / 100) * 0.75),
                                            ],
                                          ),
                                        ),
                                      ),
                                    ),
                                  ),
                                if (!_showOriginal && _selectedFx != _FxKind.none)
                                  Positioned.fill(
                                    child: IgnorePointer(
                                      child: AnimatedBuilder(
                                        animation: _fxAnimController,
                                        builder: (context, _) => CustomPaint(
                                          painter: _FxPainter(_selectedFx, _fxAnimController.value),
                                        ),
                                      ),
                                    ),
                                  ),
                                if (!_showOriginal && _drawStrokes.isNotEmpty)
                                  Positioned.fill(
                                    child: IgnorePointer(
                                      child: CustomPaint(painter: _DrawPainter(_drawStrokes)),
                                    ),
                                  ),
                                if (!_showOriginal && _eraserStrokes.isNotEmpty)
                                  Positioned.fill(
                                    child: IgnorePointer(
                                      child: CustomPaint(painter: _DrawPainter(_eraserStrokes, opacity: 0.45)),
                                    ),
                                  ),
                                if (!_showOriginal)
                                  ..._textOverlays
                                      .where((o) => o.id != _hiddenOverlayId)
                                      .map((o) => _buildOverlayWidget(o, boxW, boxH)),
                                // Live "type-on-photo" preview — mirrors the Add/Edit
                                // Text sheet in real time (see _openAddTextSheet /
                                // updateLivePreview), so text updates directly on the
                                // image as you type, Insta-style, not just after Save.
                                if (!_showOriginal && _liveTextPreview != null)
                                  _buildLiveTextPreview(_liveTextPreview!, boxW, boxH),
                                if (!_showOriginal && _tab == _EditTab.draw)
                                  Positioned.fill(
                                    child: GestureDetector(
                                      behavior: HitTestBehavior.opaque,
                                      onPanStart: (d) => _startStroke(Offset(d.localPosition.dx / boxW, d.localPosition.dy / boxH)),
                                      onPanUpdate: (d) => _extendStroke(Offset(d.localPosition.dx / boxW, d.localPosition.dy / boxH)),
                                      onPanEnd: (_) => _endStroke(),
                                    ),
                                  ),
                                if (!_showOriginal && _tab == _EditTab.eraser && !_isErasing)
                                  Positioned.fill(
                                    child: GestureDetector(
                                      behavior: HitTestBehavior.opaque,
                                      onPanStart: (d) => _startEraserStroke(Offset(d.localPosition.dx / boxW, d.localPosition.dy / boxH)),
                                      onPanUpdate: (d) => _extendEraserStroke(Offset(d.localPosition.dx / boxW, d.localPosition.dy / boxH)),
                                      onPanEnd: (_) => _endEraserStroke(),
                                    ),
                                  ),
                                if (_isTransforming || _isErasing) const CircularProgressIndicator(color: _primary),
                                if (_showOriginal)
                                  Positioned(
                                    top: 10,
                                    child: Container(
                                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                                      decoration: BoxDecoration(
                                        color: Colors.black54,
                                        borderRadius: BorderRadius.circular(12),
                                      ),
                                      child: const Text(
                                        'Original',
                                        style: TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.w700),
                                      ),
                                    ),
                                  ),
                              ],
                              ),
                              ),
                              ),
                              // Rule-of-thirds grid — screen-fixed (sibling of the
                              // pinched/rotated content, not inside it), so it fades
                              // in only while actively pinch/pan/rotating and always
                              // reads as an axis-aligned viewfinder overlay, exactly
                              // like Instagram's own crop grid, rather than spinning
                              // and stretching along with the photo underneath it.
                              if (reframeEnabled)
                                Positioned.fill(
                                  child: IgnorePointer(
                                    child: AnimatedOpacity(
                                      duration: const Duration(milliseconds: 150),
                                      opacity: _isReframing ? 1.0 : 0.0,
                                      child: CustomPaint(painter: _ReframeGridPainter()),
                                    ),
                                  ),
                                ),
                              ],
                            );
                          },
                        ),
                      ),
                    ),
                  ),
                ),
                // ── Quick actions: flip / undo / redo / reset — no Rotate
                // button here anymore, rotate is a two-finger gesture on
                // the canvas itself now (part of the same pinch-zoom
                // reframe as the crop, see _reframeRotation above) ──
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    _quickIconButton(Icons.swap_horiz_rounded, 'Flip horizontal', busy ? null : _flipHorizontal),
                    _quickIconButton(Icons.swap_vert_rounded, 'Flip vertical', busy ? null : _flipVertical),
                    Container(width: 1, height: 20, color: Colors.white12, margin: const EdgeInsets.symmetric(horizontal: 6)),
                    _quickIconButton(Icons.undo_rounded, 'Undo', (_undoStack.isEmpty || busy) ? null : _undo),
                    _quickIconButton(Icons.redo_rounded, 'Redo', (_redoStack.isEmpty || busy) ? null : _redo),
                    _quickIconButton(Icons.restore_rounded, 'Reset', (_canReset && !busy) ? _resetAdjustments : null),
                  ],
                ),
                // ── Tab switch ──
                SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      _tabButton('Filters', _EditTab.filters, Icons.photo_filter_outlined),
                      const SizedBox(width: 16),
                      _tabButton('Adjust', _EditTab.adjust, Icons.tune),
                      const SizedBox(width: 16),
                      _tabButton('Text', _EditTab.text, Icons.text_fields),
                      const SizedBox(width: 16),
                      _tabButton('Draw', _EditTab.draw, Icons.brush_outlined),
                      const SizedBox(width: 16),
                      _tabButton('Stickers', _EditTab.stickers, Icons.emoji_emotions_outlined),
                      const SizedBox(width: 16),
                      _tabButton('FX', _EditTab.fx, Icons.auto_awesome_outlined),
                      const SizedBox(width: 16),
                      _tabButton('Eraser', _EditTab.eraser, Icons.auto_fix_high),
                      const SizedBox(width: 16),
                      // 🔥 NAYA — photo posts pe bhi music laga sakte ho ab
                      // (Instagram jaisa hi); export image+audio ko chhoti
                      // mp4 me bake kar deta hai, see _onDone().
                      _tabButton('Music', _EditTab.music, Icons.music_note_outlined),
                      const SizedBox(width: 16),
                      // 🔥 NAYA — manual multi-photo/video montage: is
                      // photo ke saath aur photos/videos jodo, har ek ka
                      // crop/duration/transition set karo, see
                      // _buildPhotoClipsTab / _onDoneManualMontage.
                      _tabButton('Add Photos', _EditTab.photoClips, Icons.add_photo_alternate_outlined),
                    ],
                  ),
                ),
                const SizedBox(height: 12),
                SizedBox(
                  height: _tab == _EditTab.filters
                      ? (_selectedFilterIndex != 0 ? 146 : 104)
                      : (_tab == _EditTab.adjust
                          ? 300
                          : (_tab == _EditTab.draw || _tab == _EditTab.eraser
                              ? 116
                              : (_tab == _EditTab.music
                                  ? _musicTabHeight
                                  : (_tab == _EditTab.photoClips ? 118 : 96)))),
                  child: switch (_tab) {
                    _EditTab.filters => _buildFilterStrip(),
                    _EditTab.adjust => _buildAdjustSliders(),
                    _EditTab.text => _buildTextTab(),
                    _EditTab.draw => _buildDrawTab(),
                    _EditTab.stickers => _buildStickersTab(),
                    _EditTab.fx => _buildFxSelector(),
                    _EditTab.eraser => _buildEraserTab(),
                    _EditTab.music => _buildMusicTab(),
                    _EditTab.photoClips => _buildPhotoClipsTab(),
                    _EditTab.trim || _EditTab.cover || _EditTab.speed || _EditTab.boomerang || _EditTab.clips => const SizedBox.shrink(),
                  },
                ),
                const SizedBox(height: 12),
              ],
            ),
        ),
      ),
    );
  }

  // Non-interactive stand-in for the overlay currently being typed/
  // styled in the Add/Edit Text sheet — same positioning + styling
  // (_buildStyledText/_wrapWithTextAnim) as a real overlay, just
  // without drag/pinch/rotate handling or the selection border, since
  // the sheet — not the canvas — owns the gesture focus right now.
  Widget _buildLiveTextPreview(_TextOverlay o, double w, double h) {
    if (o.text.trim().isEmpty) return const SizedBox.shrink();
    final baseFontSize = o.fontSize * (w / 1000);
    return Positioned(
      left: o.dx * w,
      top: o.dy * h,
      child: FractionalTranslation(
        translation: const Offset(-0.5, -0.5),
        child: IgnorePointer(
          child: Transform.rotate(
            angle: o.rotation,
            child: Transform.scale(
              scale: o.scale,
              child: _wrapWithTextAnim(
                o.animKind,
                _buildStyledText(
                  o.text,
                  o.style,
                  baseFontSize,
                  o.color,
                  o.color2,
                  isSticker: false,
                  fontFamily: o.fontFamily,
                  showBackground: o.showBackground,
                  textAlign: o.textAlign,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildOverlayWidget(_TextOverlay o, double w, double h) {
    final baseFontSize = o.fontSize * (w / 1000);
    final selected = _selectedOverlayId == o.id;
    return Positioned(
      left: o.dx * w,
      top: o.dy * h,
      child: FractionalTranslation(
        translation: const Offset(-0.5, -0.5),
        child: GestureDetector(
          onTap: () => setState(() => _selectedOverlayId = o.id),
          onDoubleTap: () => o.isSticker ? _openStickerPicker(editId: o.id) : _openAddTextSheet(editId: o.id),
          onScaleStart: (_) {
            _captureHistory();
            _dragStartScale = o.scale;
            _dragStartRotation = o.rotation;
            setState(() => _selectedOverlayId = o.id);
          },
          onScaleUpdate: (details) {
            setState(() {
              o.dx = (o.dx + details.focalPointDelta.dx / w).clamp(0.0, 1.0);
              o.dy = (o.dy + details.focalPointDelta.dy / h).clamp(0.0, 1.0);
              // Insta-style resistance: text/stickers hardly "zoom out"
              // past a real, still-legible floor (0.55 vs. the old
              // 0.3 — nothing shrinks to an illegible speck anymore),
              // and the max is a touch tighter too (3.5 vs. 4.0) so
              // scaling reads as controlled/professional rather than
              // wildly loose in either direction.
              o.scale = (_dragStartScale * details.scale).clamp(0.55, 3.5);
              o.rotation = _dragStartRotation + details.rotation;
            });
          },
          child: Transform.rotate(
            angle: o.rotation,
            child: Transform.scale(
              scale: o.scale,
              child: Container(
                padding: const EdgeInsets.all(6),
                decoration: selected
                    ? BoxDecoration(border: Border.all(color: _primary, width: 1.4), borderRadius: BorderRadius.circular(4))
                    : null,
                child: o.imageFile != null
                    ? Image.file(
                        o.imageFile!,
                        width: baseFontSize * 2.6,
                        fit: BoxFit.contain,
                        errorBuilder: (_, __, ___) => Icon(Icons.broken_image_outlined, color: _muted, size: baseFontSize * 0.6),
                      )
                    : o.assetPath != null
                    ? Image.asset(
                        o.assetPath!,
                        width: baseFontSize * 1.3,
                        height: baseFontSize * 1.3,
                        fit: BoxFit.contain,
                        errorBuilder: (_, __, ___) => Icon(Icons.broken_image_outlined, color: _muted, size: baseFontSize * 0.6),
                      )
                    : _wrapWithTextAnim(
                        o.animKind,
                        _buildStyledText(
                          o.text,
                          o.style,
                          baseFontSize,
                          o.color,
                          o.color2,
                          isSticker: o.isSticker,
                          fontFamily: o.fontFamily,
                          showBackground: o.showBackground,
                          textAlign: o.textAlign,
                        ),
                      ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildStyledText(
    String text,
    _TextStyleKind kind,
    double fontSize,
    Color color,
    Color? color2, {
    bool isSticker = false,
    String fontFamily = 'Roboto',
    bool? showBackground,
    TextAlign textAlign = TextAlign.center,
  }) {
    if (isSticker) {
      return Text(text, textAlign: TextAlign.center, style: TextStyle(fontSize: fontSize));
    }
    final bg = showBackground ?? _styleDefaultBackground(kind);
    // Wraps plain (non-pill) styles in a soft translucent chip when the
    // user's turned Background on for them (off by default — see
    // _styleDefaultBackground).
    Widget withChip(Widget child) {
      if (!bg) return child;
      return Container(
        padding: EdgeInsets.symmetric(horizontal: fontSize * 0.3, vertical: fontSize * 0.14),
        decoration: BoxDecoration(color: Colors.black.withOpacity(0.4), borderRadius: BorderRadius.circular(fontSize * 0.16)),
        child: child,
      );
    }

    switch (kind) {
      case _TextStyleKind.classic:
        return withChip(Text(
          text,
          textAlign: textAlign,
          style: _applyFontFamily(
            fontFamily,
            TextStyle(
              fontSize: fontSize,
              fontWeight: FontWeight.w600,
              color: color,
              shadows: const [Shadow(color: Colors.black54, blurRadius: 4, offset: Offset(0, 1))],
            ),
          ),
        ));
      case _TextStyleKind.bold:
        return withChip(Stack(
          alignment: Alignment.center,
          children: [
            Text(
              text,
              textAlign: textAlign,
              style: _applyFontFamily(
                fontFamily,
                TextStyle(
                  fontSize: fontSize,
                  fontWeight: FontWeight.w900,
                  foreground: Paint()
                    ..style = PaintingStyle.stroke
                    ..strokeWidth = fontSize * 0.08
                    ..color = Colors.black,
                ),
              ),
            ),
            Text(
              text,
              textAlign: textAlign,
              style: _applyFontFamily(fontFamily, TextStyle(fontSize: fontSize, fontWeight: FontWeight.w900, color: color)),
            ),
          ],
        ));
      case _TextStyleKind.neon:
        return withChip(Text(
          text,
          textAlign: textAlign,
          style: _applyFontFamily(
            fontFamily,
            TextStyle(
              fontSize: fontSize,
              fontWeight: FontWeight.w800,
              color: color,
              shadows: [
                Shadow(color: color.withOpacity(0.9), blurRadius: fontSize * 0.35),
                Shadow(color: color.withOpacity(0.6), blurRadius: fontSize * 0.7),
              ],
            ),
          ),
        ));
      case _TextStyleKind.typewriter:
        final label = Text(
          text,
          textAlign: textAlign,
          style: _applyFontFamily(
            fontFamily == 'Roboto' ? 'Courier Prime' : fontFamily, // default to the typewriter mono unless the user picked their own font
            TextStyle(fontSize: fontSize, color: color, fontWeight: FontWeight.w500),
          ),
        );
        if (!bg) return label;
        return Container(
          padding: EdgeInsets.symmetric(horizontal: fontSize * 0.3, vertical: fontSize * 0.12),
          color: Colors.black.withOpacity(0.55),
          child: label,
        );
      case _TextStyleKind.highlight:
        final textColor = _contrastingTextColor(color);
        final label = Text(
          text,
          textAlign: textAlign,
          style: _applyFontFamily(fontFamily, TextStyle(fontSize: fontSize, fontWeight: FontWeight.w700, color: bg ? textColor : color)),
        );
        if (!bg) return label;
        return Container(
          padding: EdgeInsets.symmetric(horizontal: fontSize * 0.35, vertical: fontSize * 0.15),
          decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(fontSize * 0.2)),
          child: label,
        );
      case _TextStyleKind.gradient:
        return withChip(ShaderMask(
          blendMode: BlendMode.srcIn,
          shaderCallback: (bounds) => LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [color, color2 ?? _accent],
          ).createShader(bounds),
          child: Text(
            text,
            textAlign: textAlign,
            style: _applyFontFamily(fontFamily, TextStyle(fontSize: fontSize, fontWeight: FontWeight.w800, color: Colors.white)),
          ),
        ));
      case _TextStyleKind.outline:
        return withChip(Text(
          text,
          textAlign: textAlign,
          style: _applyFontFamily(
            fontFamily,
            TextStyle(
              fontSize: fontSize,
              fontWeight: FontWeight.w800,
              foreground: Paint()
                ..style = PaintingStyle.stroke
                ..strokeWidth = fontSize * 0.06
                ..color = color,
            ),
          ),
        ));
    }
  }

  // Live-preview-only entrance animation wrapper — loops on
  // _textAnimController so the user can see the vibe while editing.
  // Export always bakes the resting/settled state (opacity 1, no
  // offset, base scale) — see _TextAnimKind and _composeOverlays.
  Widget _wrapWithTextAnim(_TextAnimKind kind, Widget child) {
    if (kind == _TextAnimKind.none) return child;
    return AnimatedBuilder(
      animation: _textAnimController,
      builder: (context, c) {
        // 0..1 sawtooth per loop, eased into a quick "settle then hold"
        // curve so it reads as an entrance rather than a constant wobble.
        final t = _textAnimController.value;
        final entrance = Curves.easeOutBack.transform(math.min(1, t * 2.2));
        switch (kind) {
          case _TextAnimKind.none:
            return c!;
          case _TextAnimKind.fadeIn:
            return Opacity(opacity: entrance.clamp(0.0, 1.0), child: c);
          case _TextAnimKind.popIn:
            return Transform.scale(scale: 0.4 + 0.6 * entrance, child: Opacity(opacity: entrance.clamp(0.0, 1.0), child: c));
          case _TextAnimKind.bounce:
            final bounce = math.sin(t * math.pi * 2) * 6;
            return Transform.translate(offset: Offset(0, -bounce.abs()), child: c);
          case _TextAnimKind.pulse:
            final pulse = 1 + 0.06 * math.sin(t * math.pi * 2);
            return Transform.scale(scale: pulse, child: c);
          case _TextAnimKind.slideUp:
            final dy = (1 - entrance) * 24;
            return Transform.translate(offset: Offset(0, dy), child: Opacity(opacity: entrance.clamp(0.0, 1.0), child: c));
        }
      },
      child: child,
    );
  }

  // Polished "Done" CTA — a gradient pill (instead of a flat
  // TextButton) with a soft glow shadow, a small check icon, and an
  // inline progress readout for video export. Purely cosmetic; the
  // underlying _onDone save/export logic is unchanged.
  // ─── Auto Edit result preview — shown instead of the normal editor
  // while _autoEditResult is set. "Remove" clears it (back to normal
  // editing, nothing changed). "Use This" finalizes it, same as Done.
  Widget _buildAutoEditReview() {
    final controller = _autoEditPreviewController;
    return Scaffold(
      backgroundColor: _bg,
      appBar: AppBar(
        backgroundColor: _bg,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.close_rounded),
          tooltip: 'Remove',
          onPressed: _removeAutoEdit,
        ),
        title: const Text('Auto Edit', style: TextStyle(fontWeight: FontWeight.w700)),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12, left: 4),
            child: ElevatedButton.icon(
              onPressed: _useAutoEditResult,
              icon: const Icon(Icons.check_rounded, size: 16),
              label: const Text('Use This'),
              style: ElevatedButton.styleFrom(backgroundColor: _primary, foregroundColor: Colors.white),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: Center(
              child: controller != null && controller.value.isInitialized
                  ? AspectRatio(aspectRatio: controller.value.aspectRatio, child: VideoPlayer(controller))
                  : const CircularProgressIndicator(color: _primary),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: OutlinedButton.icon(
              onPressed: _removeAutoEdit,
              icon: const Icon(Icons.delete_outline_rounded),
              label: const Text('Remove — back to editing'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildDoneButton(bool busy) {
    final disabled = busy && !_isSaving;
    return AnimatedContainer(
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOut,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: disabled
              ? [Colors.white12, Colors.white12]
              : [_primary, _accent],
        ),
        borderRadius: BorderRadius.circular(999),
        boxShadow: disabled
            ? []
            : [
                BoxShadow(
                  color: _primary.withOpacity(0.35),
                  blurRadius: 14,
                  offset: const Offset(0, 4),
                ),
              ],
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(999),
          onTap: (busy && !_isSaving)
              ? null
              : () {
                  HapticFeedback.mediumImpact();
                  _onDone();
                },
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 9),
            child: _isSaving
                ? Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                      ),
                      if (_isVideo) ...[
                        const SizedBox(width: 8),
                        Text('${(_exportProgress * 100).round()}%',
                            style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w700)),
                      ],
                    ],
                  )
                : Row(
                    mainAxisSize: MainAxisSize.min,
                    children: const [
                      Icon(Icons.check_rounded, color: Colors.white, size: 16),
                      SizedBox(width: 4),
                      Text('Done', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 13)),
                    ],
                  ),
          ),
        ),
      ),
    );
  }

  Widget _quickIconButton(IconData icon, String tooltip, VoidCallback? onTap) {
    final enabled = onTap != null;
    return Tooltip(
      message: tooltip,
      child: IconButton(
        onPressed: enabled
            ? () {
                HapticFeedback.selectionClick();
                onTap();
              }
            : null,
        splashRadius: 20,
        icon: Icon(icon, size: 20, color: enabled ? Colors.white : _muted.withOpacity(0.35)),
      ),
    );
  }

  // Instagram-style icon tab (icon only, no printed name) — the label
  // is still passed in for the tooltip (long-press/hover) and for
  // screen readers via Semantics, so nothing is lost for accessibility,
  // it's just not painted on-screen anymore.
  Widget _tabButton(String label, _EditTab tab, IconData icon) {
    final selected = _tab == tab;
    return Tooltip(
      message: label,
      child: Semantics(
        label: label,
        button: true,
        selected: selected,
        child: GestureDetector(
          onTap: () {
            if (selected) return;
            HapticFeedback.selectionClick();
            setState(() => _tab = tab);
          },
          child: AnimatedScale(
            duration: const Duration(milliseconds: 180),
            curve: Curves.easeOut,
            scale: selected ? 1.06 : 1.0,
            child: Column(
              children: [
                AnimatedContainer(
                  duration: const Duration(milliseconds: 180),
                  curve: Curves.easeOut,
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: selected ? Colors.white.withOpacity(0.12) : Colors.transparent,
                  ),
                  child: Icon(
                    icon,
                    size: 22,
                    color: selected ? Colors.white : _muted,
                  ),
                ),
                const SizedBox(height: 4),
                AnimatedContainer(
                  duration: const Duration(milliseconds: 220),
                  curve: Curves.easeOut,
                  width: selected ? 20 : 0,
                  height: 3,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(2),
                    gradient: selected ? LinearGradient(colors: [_primary, _accent]) : null,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildFilterStrip() {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox(
          height: 90,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 16),
            itemCount: _presets.length,
            separatorBuilder: (_, __) => const SizedBox(width: 14),
            itemBuilder: (context, index) {
              final preset = _presets[index];
              final selected = index == _selectedFilterIndex;
              return GestureDetector(
                onTap: () {
                  if (index == _selectedFilterIndex) return;
                  HapticFeedback.selectionClick();
                  _captureHistory();
                  setState(() {
                    _selectedFilterIndex = index;
                    _filterIntensity = 100;
                  });
                },
                child: Column(
                  children: [
                    AnimatedContainer(
                      duration: const Duration(milliseconds: 200),
                      curve: Curves.easeOut,
                      width: 64,
                      height: 64,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        border: Border.all(color: selected ? _primary : Colors.transparent, width: 2.5),
                        boxShadow: selected
                            ? [BoxShadow(color: _primary.withOpacity(0.45), blurRadius: 10, spreadRadius: 1)]
                            : [],
                      ),
                      padding: const EdgeInsets.all(2),
                      child: ClipOval(
                        child: ColorFiltered(
                          colorFilter: ColorFilter.matrix(preset.matrix),
                          child: Image.file(_workingFile, fit: BoxFit.cover),
                        ),
                      ),
                    ),
                    const SizedBox(height: 6),
                    AnimatedDefaultTextStyle(
                      duration: const Duration(milliseconds: 200),
                      style: TextStyle(
                        color: selected ? Colors.white : _muted,
                        fontSize: 11,
                        fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                      ),
                      child: Text(preset.name),
                    ),
                  ],
                ),
              );
            },
          ),
        ),
        // Intensity slider — only meaningful once a non-Normal preset
        // is picked, so it fades in/out instead of always taking space.
        if (_selectedFilterIndex != 0)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: Row(
              children: [
                const Icon(Icons.tune_rounded, color: _muted, size: 16),
                const SizedBox(width: 8),
                Expanded(
                  child: SliderTheme(
                    data: SliderTheme.of(context).copyWith(
                      activeTrackColor: _primary,
                      inactiveTrackColor: Colors.white24,
                      thumbColor: _accent,
                      overlayColor: _primary.withOpacity(0.2),
                      trackHeight: 2,
                    ),
                    child: Slider(
                      value: _filterIntensity,
                      min: 0,
                      max: 100,
                      onChangeStart: (_) => _captureHistory(),
                      onChanged: (v) => setState(() => _filterIntensity = v),
                    ),
                  ),
                ),
                SizedBox(
                  width: 32,
                  child: Text(
                    '${_filterIntensity.round()}',
                    textAlign: TextAlign.end,
                    style: const TextStyle(color: Colors.white70, fontSize: 11),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _buildAdjustSliders() {
    return SingleChildScrollView(
      padding: const EdgeInsets.symmetric(horizontal: 24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: OutlinedButton.icon(
              onPressed: _isAutoEnhancing ? null : _autoEnhance,
              icon: _isAutoEnhancing
                  ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: _accent))
                  : const Icon(Icons.auto_awesome_rounded, size: 16, color: _accent),
              label: Text(_isAutoEnhancing ? 'Analyzing…' : 'Auto-Enhance', style: const TextStyle(color: Colors.white)),
            ),
          ),
          _adjustSlider('Brightness', Icons.wb_sunny_rounded, _brightness, (v) => setState(() => _brightness = v)),
          _adjustSlider('Contrast', Icons.contrast_rounded, _contrast, (v) => setState(() => _contrast = v)),
          _adjustSlider('Saturation', Icons.opacity_rounded, _saturation, (v) => setState(() => _saturation = v)),
          _adjustSlider('Warmth', Icons.thermostat_rounded, _warmth, (v) => setState(() => _warmth = v)),
          _adjustSlider(
            'Vignette',
            Icons.vignette_rounded,
            _vignette,
            (v) => setState(() => _vignette = v),
            min: 0,
          ),
        ],
      ),
    );
  }

  Widget _buildTextTab() {
    return Center(
      child: Wrap(
        spacing: 12,
        runSpacing: 8,
        alignment: WrapAlignment.center,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          ElevatedButton.icon(
            onPressed: () => _openAddTextSheet(),
            icon: const Icon(Icons.add_rounded, size: 18),
            label: const Text('Add Text'),
            style: ElevatedButton.styleFrom(backgroundColor: _primary, foregroundColor: Colors.white),
          ),
          if (_selectedOverlayId != null) ...[
            OutlinedButton.icon(
              onPressed: () => _openAddTextSheet(editId: _selectedOverlayId),
              icon: const Icon(Icons.edit_rounded, size: 16, color: Colors.white),
              label: const Text('Edit', style: TextStyle(color: Colors.white)),
            ),
            OutlinedButton.icon(
              onPressed: _deleteSelectedOverlay,
              icon: const Icon(Icons.delete_outline_rounded, size: 16, color: Colors.redAccent),
              label: const Text('Delete', style: TextStyle(color: Colors.redAccent)),
            ),
          ] else if (_textOverlays.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text(
                'Tap Add Text, then drag/pinch/rotate it on the photo',
                textAlign: TextAlign.center,
                style: TextStyle(color: _muted, fontSize: 12),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildDrawTab() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Expanded(
                child: SizedBox(
                  height: 32,
                  child: ListView.separated(
                    scrollDirection: Axis.horizontal,
                    itemCount: _drawColorSwatches.length,
                    separatorBuilder: (_, __) => const SizedBox(width: 10),
                    itemBuilder: (context, i) {
                      final c = _drawColorSwatches[i];
                      final sel = c.value == _selectedDrawColor.value;
                      return GestureDetector(
                        onTap: () => setState(() => _selectedDrawColor = c),
                        child: Container(
                          width: 32,
                          height: 32,
                          decoration: BoxDecoration(
                            color: c,
                            shape: BoxShape.circle,
                            border: Border.all(color: sel ? _primary : Colors.white24, width: sel ? 3 : 1),
                          ),
                        ),
                      );
                    },
                  ),
                ),
              ),
              IconButton(
                onPressed: _canClearDrawing ? _clearDrawing : null,
                icon: Icon(Icons.layers_clear_rounded, size: 20, color: _canClearDrawing ? Colors.white : _muted.withOpacity(0.35)),
                tooltip: 'Clear drawing',
              ),
            ],
          ),
          Row(
            children: [
              const Icon(Icons.brush_rounded, color: _muted, size: 18),
              const SizedBox(width: 10),
              const SizedBox(
                width: 78,
                child: Text('Brush', style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600)),
              ),
              Expanded(
                child: SliderTheme(
                  data: SliderTheme.of(context).copyWith(
                    activeTrackColor: _primary,
                    inactiveTrackColor: Colors.white24,
                    thumbColor: _accent,
                    overlayColor: _primary.withOpacity(0.2),
                    trackHeight: 2,
                  ),
                  child: Slider(
                    value: _selectedDrawWidth,
                    min: 2,
                    max: 40,
                    onChanged: (v) => setState(() => _selectedDrawWidth = v),
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildStickersTab() {
    return Center(
      child: Wrap(
        spacing: 12,
        runSpacing: 8,
        alignment: WrapAlignment.center,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          ElevatedButton.icon(
            onPressed: () => _openStickerPicker(),
            icon: const Icon(Icons.emoji_emotions_outlined, size: 18),
            label: const Text('Add Sticker'),
            style: ElevatedButton.styleFrom(backgroundColor: _primary, foregroundColor: Colors.white),
          ),
          // Add one or more photos from the device straight onto the
          // canvas — no crop/insert dialog, they land centered
          // (staggered if you pick several at once) and are
          // immediately drag/pinch/rotate-able and freely overlappable
          // with each other and the base photo, same as any sticker.
          OutlinedButton.icon(
            onPressed: _isPickingImages ? null : _pickGalleryImages,
            icon: _isPickingImages
                ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                : const Icon(Icons.add_photo_alternate_outlined, size: 18, color: Colors.white),
            label: Text(_isPickingImages ? 'Loading...' : 'Add Photo', style: const TextStyle(color: Colors.white)),
          ),
          if (_selectedOverlayId != null && _textOverlays.any((o) => o.id == _selectedOverlayId && o.isSticker))
            OutlinedButton.icon(
              onPressed: _deleteSelectedOverlay,
              icon: const Icon(Icons.delete_outline_rounded, size: 16, color: Colors.redAccent),
              label: const Text('Delete', style: TextStyle(color: Colors.redAccent)),
            )
          else if (!_textOverlays.any((o) => o.isSticker))
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text(
                'Tap Add Sticker, then drag/pinch/rotate it on the photo',
                textAlign: TextAlign.center,
                style: TextStyle(color: _muted, fontSize: 12),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildEraserTab() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              const Icon(Icons.auto_fix_high_rounded, color: _muted, size: 18),
              const SizedBox(width: 10),
              const SizedBox(
                width: 78,
                child: Text('Brush', style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600)),
              ),
              Expanded(
                child: SliderTheme(
                  data: SliderTheme.of(context).copyWith(
                    activeTrackColor: Colors.redAccent,
                    inactiveTrackColor: Colors.white24,
                    thumbColor: Colors.redAccent,
                    overlayColor: Colors.redAccent.withOpacity(0.2),
                    trackHeight: 2,
                  ),
                  child: Slider(
                    value: _eraserBrushWidth,
                    min: 14,
                    max: 80,
                    onChanged: (v) => setState(() => _eraserBrushWidth = v),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              OutlinedButton.icon(
                onPressed: _eraserStrokes.isNotEmpty && !_isErasing ? _clearEraserMask : null,
                icon: const Icon(Icons.layers_clear_rounded, size: 16, color: Colors.white70),
                label: const Text('Clear', style: TextStyle(color: Colors.white70)),
              ),
              const SizedBox(width: 12),
              ElevatedButton.icon(
                onPressed: _eraserStrokes.isNotEmpty && !_isErasing ? _applyMagicEraser : null,
                icon: _isErasing
                    ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : const Icon(Icons.auto_fix_high_rounded, size: 16),
                label: Text(_isErasing ? 'Erasing…' : 'Apply'),
                style: ElevatedButton.styleFrom(backgroundColor: _primary, foregroundColor: Colors.white),
              ),
            ],
          ),
          if (_eraserStrokes.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 6),
              child: Text(
                'Paint over a blemish/watermark, then tap Apply. Best for small spots on plain backgrounds.',
                textAlign: TextAlign.center,
                style: TextStyle(color: _muted, fontSize: 11),
              ),
            ),
        ],
      ),
    );
  }

  // ═══════════════════════════════════════════════════════════════
  // Video trim mode UI (Instagram-style trim + cover picker)
  // ═══════════════════════════════════════════════════════════════

  Widget _buildVideoBody() {
    final controller = _videoController;
    if (controller == null) return const SizedBox.shrink();
    final rawAspect = controller.video.value.aspectRatio;
    // 90°/270° rotation swaps which dimension is "wide" — flip the
    // aspect ratio the outer box reserves so the rotated video doesn't
    // get letterboxed/cropped oddly.
    final isSideways = controller.rotationTurns % 2 == 1;
    final displayAspect = isSideways ? 1 / rawAspect : rawAspect;
    return Column(
      children: [
        // ── Live preview: rotated frame + tap-to-play/pause ──
        Expanded(
          child: Center(
            child: GestureDetector(
              onTap: () {
                setState(() {
                  controller.video.value.isPlaying ? controller.video.pause() : controller.video.play();
                });
              },
              child: AspectRatio(
                aspectRatio: displayAspect,
                child: Stack(
                  alignment: Alignment.center,
                  children: [
                    RotatedBox(
                      quarterTurns: controller.rotationTurns,
                      child: AspectRatio(aspectRatio: rawAspect, child: VideoPlayer(controller.video)),
                    ),
                    if (_selectedFx != _FxKind.none)
                      Positioned.fill(
                        child: IgnorePointer(
                          child: AnimatedBuilder(
                            animation: _fxAnimController,
                            builder: (context, _) => CustomPaint(
                              painter: _FxPainter(_selectedFx, _fxAnimController.value),
                            ),
                          ),
                        ),
                      ),
                    AnimatedBuilder(
                      animation: controller.video,
                      builder: (context, _) {
                        if (controller.video.value.isPlaying) return const SizedBox.shrink();
                        return Container(
                          padding: const EdgeInsets.all(14),
                          decoration: const BoxDecoration(color: Colors.black45, shape: BoxShape.circle),
                          child: const Icon(Icons.play_arrow_rounded, color: Colors.white, size: 34),
                        );
                      },
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
        // ── Quick actions: rotate ──
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            _quickIconButton(
              Icons.rotate_left_rounded,
              'Rotate',
              _isSaving ? null : () => setState(controller.rotateLeft),
            ),
            _quickIconButton(
              Icons.rotate_right_rounded,
              'Rotate right',
              _isSaving ? null : () => setState(controller.rotateRight),
            ),
          ],
        ),
        // ── Tab switch: Trim / Cover / Speed / Music / FX ──
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              _tabButton('Trim', _EditTab.trim, Icons.content_cut),
              const SizedBox(width: 16),
              _tabButton('Cover', _EditTab.cover, Icons.image_outlined),
              const SizedBox(width: 16),
              _tabButton('Speed', _EditTab.speed, Icons.speed),
              const SizedBox(width: 16),
              _tabButton('Music', _EditTab.music, Icons.music_note_outlined),
              const SizedBox(width: 16),
              _tabButton('FX', _EditTab.fx, Icons.auto_awesome_outlined),
              const SizedBox(width: 16),
              _tabButton('Boomerang', _EditTab.boomerang, Icons.all_inclusive),
              const SizedBox(width: 16),
              _tabButton('Clips', _EditTab.clips, Icons.video_library_outlined),
              const SizedBox(width: 16),
              // 🔥 NAYA — manual multi-photo/video montage, see
              // _buildPhotoClipsTab / _onDoneManualMontage.
              _tabButton('Add Photos', _EditTab.photoClips, Icons.add_photo_alternate_outlined),
            ],
          ),
        ),
        const SizedBox(height: 12),
        SizedBox(
          height: _tab == _EditTab.clips
              ? 140
              : (_tab == _EditTab.music
                  ? _musicTabHeight
                  : (_tab == _EditTab.photoClips ? 118 : 96)),
          child: switch (_tab) {
            _EditTab.cover => _buildCoverPicker(controller),
            _EditTab.speed => _buildSpeedSelector(),
            _EditTab.music => _buildMusicTab(),
            _EditTab.fx => _buildFxSelector(),
            _EditTab.boomerang => _buildBoomerangTab(),
            _EditTab.clips => _buildClipsTab(),
            _EditTab.photoClips => _buildPhotoClipsTab(),
            _ => _buildTrimSlider(controller),
          },
        ),
        const SizedBox(height: 12),
      ],
    );
  }

  // Reels/TikTok-style speed ramp — 0.5x slow-mo up to 3x fast-forward.
  // Applied on export via ffmpeg's setpts (video) + atempo (audio, kept
  // in pitch) so the live trim/cover preview stays untouched.
  Widget _buildSpeedSelector() {
    return Center(
      child: Wrap(
        spacing: 10,
        runSpacing: 10,
        alignment: WrapAlignment.center,
        children: _speedOptions.map((speed) {
          final selected = speed == _videoSpeed;
          return GestureDetector(
            onTap: () => setState(() => _videoSpeed = speed),
            child: Container(
              width: 56,
              height: 56,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: selected ? _primary : Colors.white10,
                shape: BoxShape.circle,
                border: Border.all(color: selected ? _primary : Colors.transparent, width: 2.5),
              ),
              child: Text(
                '${speed}x',
                style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: selected ? FontWeight.w800 : FontWeight.w500),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  // Background music picker + mix controls. Music auto-loops
  // (`-stream_loop -1` on export) to cover the clip even if the
  // chosen track is shorter than the trimmed video.
  // Height budget the outer tab switch reserves for the Music tab —
  // bigger while the Freesound search list or the crop sheet is open,
  // compact once a track is picked (matches the old fixed-96 layout).
  double get _musicTabHeight {
    if (_freesoundCropTrack != null) return 210;
    if (_musicFile == null) return 300;
    return 110;
  }

  Widget _buildMusicTab() {
    if (_freesoundCropTrack != null) return _buildFreesoundCropSheet();
    if (_musicFile == null) {
      return Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          ElevatedButton.icon(
            onPressed: _isPickingMusic ? null : _pickMusic,
            icon: _isPickingMusic
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                  )
                : const Icon(Icons.music_note_rounded, size: 18),
            label: Text(_isPickingMusic ? 'Loading...' : 'Choose from device'),
            style: ElevatedButton.styleFrom(backgroundColor: _primary, foregroundColor: Colors.white),
          ),
          const SizedBox(height: 10),
          Row(
            children: const [
              Expanded(child: Divider(color: Colors.white24)),
              Padding(padding: EdgeInsets.symmetric(horizontal: 8), child: Text('ya', style: TextStyle(color: Colors.white54, fontSize: 11))),
              Expanded(child: Divider(color: Colors.white24)),
            ],
          ),
          const SizedBox(height: 10),
          Expanded(child: _buildFreesoundSearch()),
        ],
      );
    }
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.music_note_rounded, color: _primary, size: 18),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  _musicFileName ?? 'Music track',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                ),
              ),
              IconButton(
                onPressed: _removeMusic,
                icon: const Icon(Icons.close_rounded, size: 18, color: Colors.redAccent),
                tooltip: 'Remove music',
              ),
            ],
          ),
          Row(
            children: [
              const Icon(Icons.volume_up_rounded, color: _muted, size: 18),
              const SizedBox(width: 10),
              Expanded(
                child: SliderTheme(
                  data: SliderTheme.of(context).copyWith(
                    activeTrackColor: _primary,
                    inactiveTrackColor: Colors.white24,
                    thumbColor: _accent,
                    overlayColor: _primary.withOpacity(0.2),
                    trackHeight: 2,
                  ),
                  child: Slider(
                    value: _musicVolume,
                    min: 0,
                    max: 1,
                    onChanged: (v) => setState(() => _musicVolume = v),
                  ),
                ),
              ),
              SizedBox(
                width: 32,
                child: Text('${(_musicVolume * 100).round()}', textAlign: TextAlign.end, style: const TextStyle(color: Colors.white70, fontSize: 11)),
              ),
            ],
          ),
          // Freesound tracks are already cropped to exactly the
          // chosen window, so a separate "start from" offset doesn't
          // apply (and could silently seek past the short clip into
          // silence) — only shown for device-picked files.
          if (!_musicFromFreesound)
            Row(
              children: [
                const Icon(Icons.fast_forward_rounded, color: _muted, size: 18),
                const SizedBox(width: 10),
                const SizedBox(
                  width: 78,
                  child: Text('Start from', style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600)),
                ),
                Expanded(
                  child: SliderTheme(
                    data: SliderTheme.of(context).copyWith(
                      activeTrackColor: _primary,
                      inactiveTrackColor: Colors.white24,
                      thumbColor: _accent,
                      overlayColor: _primary.withOpacity(0.2),
                      trackHeight: 2,
                    ),
                    child: Slider(
                      value: _musicStartOffsetSec,
                      min: 0,
                      max: 180,
                      onChanged: (v) => setState(() => _musicStartOffsetSec = v),
                    ),
                  ),
                ),
                SizedBox(
                  width: 36,
                  child: Text(
                    '${(_musicStartOffsetSec ~/ 60)}:${(_musicStartOffsetSec.round() % 60).toString().padLeft(2, '0')}',
                    textAlign: TextAlign.end,
                    style: const TextStyle(color: Colors.white70, fontSize: 11),
                  ),
                ),
              ],
            ),
          // Only meaningful for video (a photo has no original audio
          // track to mix with).
          if (_isVideo)
            GestureDetector(
              onTap: () => setState(() => _keepOriginalAudio = !_keepOriginalAudio),
              child: Row(
                children: [
                  Icon(
                    _keepOriginalAudio ? Icons.check_box_rounded : Icons.check_box_outline_blank_rounded,
                    color: _keepOriginalAudio ? _primary : _muted,
                    size: 18,
                  ),
                  const SizedBox(width: 8),
                  const Text('Video ki original audio bhi rakho', style: TextStyle(color: Colors.white70, fontSize: 12)),
                ],
              ),
            ),
        ],
      ),
    );
  }

  // Freesound search box + results — only ever shows CC0 (copyright-
  // free) tracks, filtered server-side, so nothing here needs its own
  // license check or attribution UI.
  Widget _buildFreesoundSearch() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _freesoundQueryCtrl,
                style: const TextStyle(color: Colors.white, fontSize: 13),
                decoration: InputDecoration(
                  hintText: 'Freesound par music dhundo (e.g. lofi, guitar)',
                  hintStyle: const TextStyle(color: Colors.white38, fontSize: 12),
                  filled: true,
                  fillColor: Colors.white10,
                  contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide.none),
                ),
                onSubmitted: _searchFreesound,
              ),
            ),
            const SizedBox(width: 8),
            IconButton(
              onPressed: _freesoundSearching ? null : () => _searchFreesound(_freesoundQueryCtrl.text),
              icon: _freesoundSearching
                  ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Icon(Icons.search_rounded, color: Colors.white),
            ),
          ],
        ),
        const Padding(
          padding: EdgeInsets.only(top: 4, bottom: 4),
          child: Text(
            'Sirf copyright-free (CC0) music dikhaya jata hai',
            style: TextStyle(color: Colors.white38, fontSize: 10.5),
          ),
        ),
        if (_freesoundError != null)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(_freesoundError!, style: const TextStyle(color: Colors.redAccent, fontSize: 12)),
          ),
        Expanded(
          child: _freesoundResults.isEmpty
              ? Center(
                  child: Text(
                    _freesoundSearching ? '' : 'Kuch search karo...',
                    style: const TextStyle(color: Colors.white38, fontSize: 12),
                  ),
                )
              : ListView.separated(
                  itemCount: _freesoundResults.length,
                  separatorBuilder: (_, __) => const Divider(color: Colors.white12, height: 10),
                  itemBuilder: (context, i) {
                    final track = _freesoundResults[i];
                    final isPlaying = _playingPreviewTrack?['id'] == track['id'];
                    final duration = ((track['duration'] as num?) ?? 0).toDouble();
                    return Row(
                      children: [
                        IconButton(
                          onPressed: () => _togglePreviewPlayback(track),
                          icon: Icon(
                            isPlaying ? Icons.pause_circle_filled_rounded : Icons.play_circle_fill_rounded,
                            color: _primary,
                            size: 26,
                          ),
                        ),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                '${track['name']}',
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(color: Colors.white, fontSize: 12.5, fontWeight: FontWeight.w600),
                              ),
                              Row(
                                children: [
                                  Text(
                                    '${track['artist']} · ${duration.round()}s',
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(color: Colors.white54, fontSize: 10.5),
                                  ),
                                  const SizedBox(width: 6),
                                  Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                                    decoration: BoxDecoration(color: Colors.green.withOpacity(0.2), borderRadius: BorderRadius.circular(4)),
                                    child: const Text('CC0', style: TextStyle(color: Colors.greenAccent, fontSize: 9, fontWeight: FontWeight.w700)),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                        TextButton(
                          onPressed: () => _openFreesoundCropSheet(track),
                          child: const Text('Use', style: TextStyle(color: _primary, fontWeight: FontWeight.w700, fontSize: 12)),
                        ),
                      ],
                    );
                  },
                ),
        ),
      ],
    );
  }

  // Crop sheet — Instagram-style: pick any 1–20s window inside the
  // chosen track before it's downloaded/trimmed and handed to the
  // regular music pipeline.
  Widget _buildFreesoundCropSheet() {
    final track = _freesoundCropTrack!;
    final trackDuration = ((track['duration'] as num?) ?? 20).toDouble();
    final maxDuration = math.min(20.0, trackDuration).clamp(1.0, 20.0);
    final maxStart = math.max(0.0, trackDuration - _freesoundCropDuration);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              IconButton(
                onPressed: _isCroppingFreesoundTrack
                    ? null
                    : () => setState(() {
                          _freesoundCropTrack = null;
                          _freesoundPreviewPlayer?.pause();
                        }),
                icon: const Icon(Icons.arrow_back_rounded, color: Colors.white, size: 18),
              ),
              Expanded(
                child: Text(
                  '${track['name']}',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w700),
                ),
              ),
            ],
          ),
          Row(
            children: [
              const SizedBox(width: 90, child: Text('Clip length', style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600))),
              Expanded(
                child: Slider(
                  value: _freesoundCropDuration,
                  min: 1,
                  max: maxDuration,
                  activeColor: _primary,
                  onChanged: (v) => setState(() {
                    _freesoundCropDuration = v;
                    if (_freesoundCropStart > trackDuration - v) {
                      _freesoundCropStart = math.max(0, trackDuration - v);
                    }
                  }),
                ),
              ),
              SizedBox(width: 34, child: Text('${_freesoundCropDuration.round()}s', style: const TextStyle(color: Colors.white70, fontSize: 11))),
            ],
          ),
          if (maxStart > 0)
            Row(
              children: [
                const SizedBox(width: 90, child: Text('Start point', style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600))),
                Expanded(
                  child: Slider(
                    value: _freesoundCropStart.clamp(0, maxStart),
                    min: 0,
                    max: maxStart,
                    activeColor: _primary,
                    onChanged: (v) => setState(() => _freesoundCropStart = v),
                  ),
                ),
                SizedBox(width: 34, child: Text('${_freesoundCropStart.round()}s', style: const TextStyle(color: Colors.white70, fontSize: 11))),
              ],
            ),
          const SizedBox(height: 4),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: _isCroppingFreesoundTrack ? null : _applyFreesoundCrop,
              style: ElevatedButton.styleFrom(backgroundColor: _primary, foregroundColor: Colors.white),
              child: _isCroppingFreesoundTrack
                  ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Text('Use this sound'),
            ),
          ),
        ],
      ),
    );
  }

  // Boomerang toggle — on export the (already-trimmed) clip is played
  // forward then backward in one continuous loop, classic Instagram/
  // Snapchat style.
  Widget _buildBoomerangTab() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          GestureDetector(
            onTap: () => setState(() => _boomerangEnabled = !_boomerangEnabled),
            child: Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                color: _boomerangEnabled ? _primary : Colors.white10,
                shape: BoxShape.circle,
                border: Border.all(color: _boomerangEnabled ? _primary : Colors.transparent, width: 2.5),
              ),
              child: const Icon(Icons.all_inclusive_rounded, color: Colors.white, size: 26),
            ),
          ),
          const SizedBox(height: 10),
          Text(
            _boomerangEnabled ? 'Boomerang ON — forward + reverse loop' : 'Tap to enable Boomerang',
            style: TextStyle(
              color: _boomerangEnabled ? Colors.white : _muted,
              fontSize: 12,
              fontWeight: _boomerangEnabled ? FontWeight.w700 : FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }

  // Multi-clip stitching: pick extra clips (via file_selector) and
  // append them, in order, after the main trimmed clip. Each extra
  // clip gets scaled/padded to the main clip's resolution at export
  // time so mismatched resolutions don't break the concat.
  Widget _buildClipsTab() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  _extraClips.isEmpty
                      ? 'Koi extra clip nahi juda \u2014 add karke ek ke baad ek jod sakte ho'
                      : '${_extraClips.length} extra clip${_extraClips.length == 1 ? '' : 's'} joda jayega end me',
                  style: const TextStyle(color: _muted, fontSize: 12),
                ),
              ),
              ElevatedButton.icon(
                onPressed: _isPickingClip ? null : _pickExtraClip,
                icon: _isPickingClip
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                      )
                    : const Icon(Icons.add_rounded, size: 16),
                label: const Text('Add Clip'),
                style: ElevatedButton.styleFrom(backgroundColor: _primary, foregroundColor: Colors.white),
              ),
            ],
          ),
          if (_extraClips.isNotEmpty)
            SizedBox(
              height: 60,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: _extraClips.length,
                separatorBuilder: (_, __) => const SizedBox(width: 8),
                itemBuilder: (context, i) {
                  final name = _extraClips[i].path.split(Platform.pathSeparator).last;
                  return Container(
                    width: 140,
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    decoration: BoxDecoration(color: Colors.white10, borderRadius: BorderRadius.circular(8)),
                    child: Row(
                      children: [
                        const Icon(Icons.movie_rounded, color: _primary, size: 16),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            '${i + 1}. $name',
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: Colors.white, fontSize: 11),
                          ),
                        ),
                        GestureDetector(
                          onTap: () => _removeExtraClip(i),
                          child: const Icon(Icons.close_rounded, color: Colors.redAccent, size: 16),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ),
        ],
      ),
    );
  }

  // ── "Add Photos" manual montage tab ──
  // Current primary photo/video (with its own filters/text/etc, as
  // usual) always ends up as clip 1 of the montage — this strip is
  // just the clips ADDED on top of it. Drag to reorder, tap a clip to
  // crop/pan/zoom + set its duration (photo) or speed (video) + pick
  // its transition into the next clip, tap × to remove.
  Widget _buildPhotoClipsTab() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  _manualClips.isEmpty
                      ? 'Aur photos/videos jodo — sabko milake ek video banega'
                      : '${_manualClips.length + 1} clips ka video banega (isi photo/video ke saath)',
                  style: const TextStyle(color: _muted, fontSize: 12),
                ),
              ),
              ElevatedButton.icon(
                onPressed: _isPickingManualClip ? null : _pickManualClips,
                icon: _isPickingManualClip
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                      )
                    : const Icon(Icons.add_rounded, size: 16),
                label: const Text('Add Photos'),
                style: ElevatedButton.styleFrom(backgroundColor: _primary, foregroundColor: Colors.white),
              ),
            ],
          ),
          if (_manualClips.isNotEmpty)
            SizedBox(
              height: 78,
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  GestureDetector(
                    onTap: _editPrimaryClipSettings,
                    child: Container(
                      width: 64,
                      margin: const EdgeInsets.only(right: 8),
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(color: _primary, width: 1.5),
                      ),
                      child: Stack(
                        fit: StackFit.expand,
                        children: [
                          ClipRRect(
                            borderRadius: BorderRadius.circular(9),
                            child: _isVideo
                                ? Container(color: Colors.grey[900], child: const Icon(Icons.videocam, color: Colors.white54))
                                : Image.file(_workingFile, fit: BoxFit.cover),
                          ),
                          const Positioned(
                            left: 3, bottom: 3,
                            child: _ClipBadge(text: '1'),
                          ),
                          Positioned(
                            right: 3, bottom: 3,
                            child: Icon(_primaryTransitionOut.icon, color: Colors.white, size: 13,
                                shadows: const [Shadow(blurRadius: 3, color: Colors.black)]),
                          ),
                        ],
                      ),
                    ),
                  ),
                  Expanded(
                    child: ReorderableListView.builder(
                      scrollDirection: Axis.horizontal,
                      buildDefaultDragHandles: false,
                      itemCount: _manualClips.length,
                      onReorder: _reorderManualClips,
                      itemBuilder: (context, i) {
                  final clip = _manualClips[i];
                  final isLast = i == _manualClips.length - 1;
                  return ReorderableDragStartListener(
                    key: ValueKey('manual_clip_$i${clip.file.path}'),
                    index: i,
                    child: GestureDetector(
                      onTap: () => _editManualClip(i),
                      child: Container(
                        width: 64,
                        margin: const EdgeInsets.only(right: 8),
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(color: Colors.white24),
                        ),
                        child: Stack(
                          fit: StackFit.expand,
                          children: [
                            ClipRRect(
                              borderRadius: BorderRadius.circular(9),
                              child: clip.isVideo
                                  ? Container(color: Colors.grey[900], child: const Icon(Icons.videocam, color: Colors.white54))
                                  : Image.file(clip.file, fit: BoxFit.cover),
                            ),
                            Positioned(
                              left: 3, bottom: 3,
                              child: Container(
                                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                                decoration: BoxDecoration(color: Colors.black87, borderRadius: BorderRadius.circular(4)),
                                child: Text('${i + 2}', style: const TextStyle(color: Colors.white, fontSize: 9, fontWeight: FontWeight.w700)),
                              ),
                            ),
                            if (!isLast)
                              Positioned(
                                right: 3, bottom: 3,
                                child: Icon(clip.transitionOut.icon, color: Colors.white, size: 13,
                                    shadows: const [Shadow(blurRadius: 3, color: Colors.black)]),
                              ),
                            Positioned(
                              top: 3, right: 3,
                              child: GestureDetector(
                                onTap: () => _removeManualClip(i),
                                child: const CircleAvatar(radius: 9, backgroundColor: Colors.black87, child: Icon(Icons.close, size: 12, color: Colors.white)),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  );
                },
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  // Duration (if the primary is a photo) + transition-into-clip-2 for
  // the currently-open photo/video, once it's the head of a montage.
  Future<void> _editPrimaryClipSettings() async {
    double durationSec = _primaryClipDuration.inMilliseconds / 1000.0;
    _TransitionKind transition = _primaryTransitionOut;
    await showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF16161A),
      isScrollControlled: true,
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          return Padding(
            padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom),
            child: SafeArea(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const SizedBox(height: 16),
                    const Text('Clip 1 (ye photo/video)', style: TextStyle(color: Colors.white70, fontSize: 12)),
                    const SizedBox(height: 8),
                    if (!_isVideo)
                      Row(
                        children: [
                          const Icon(Icons.timer_outlined, color: Colors.white54, size: 18),
                          Expanded(
                            child: Slider(
                              value: durationSec, min: 0.5, max: 6.0, divisions: 22,
                              activeColor: _primary,
                              label: '${durationSec.toStringAsFixed(1)}s',
                              onChanged: (v) => setSheetState(() => durationSec = v),
                            ),
                          ),
                          SizedBox(width: 44, child: Text('${durationSec.toStringAsFixed(1)}s', style: const TextStyle(color: Colors.white70, fontSize: 12))),
                        ],
                      )
                    else
                      const Padding(
                        padding: EdgeInsets.symmetric(vertical: 8),
                        child: Text('Iski length wahi rahegi jo Trim tab me set hai',
                            style: TextStyle(color: Colors.white38, fontSize: 11)),
                      ),
                    const SizedBox(height: 6),
                    const Align(alignment: Alignment.centerLeft, child: Text('Clip 2 me transition', style: TextStyle(color: Colors.white54, fontSize: 11))),
                    const SizedBox(height: 6),
                    SizedBox(
                      height: 70,
                      child: ListView.separated(
                        scrollDirection: Axis.horizontal,
                        itemCount: _TransitionKind.values.length,
                        separatorBuilder: (_, __) => const SizedBox(width: 8),
                        itemBuilder: (ctx, i) {
                          final t = _TransitionKind.values[i];
                          final sel = t == transition;
                          return GestureDetector(
                            onTap: () => setSheetState(() => transition = t),
                            child: Container(
                              width: 64,
                              decoration: BoxDecoration(
                                color: sel ? _primary.withOpacity(0.25) : Colors.white10,
                                borderRadius: BorderRadius.circular(10),
                                border: Border.all(color: sel ? _primary : Colors.transparent, width: 1.5),
                              ),
                              child: Column(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  Icon(t.icon, color: Colors.white, size: 18),
                                  const SizedBox(height: 4),
                                  Text(t.label, style: const TextStyle(color: Colors.white, fontSize: 10)),
                                ],
                              ),
                            ),
                          );
                        },
                      ),
                    ),
                    const SizedBox(height: 14),
                    SizedBox(
                      width: double.infinity,
                      child: ElevatedButton(
                        onPressed: () {
                          setState(() {
                            _primaryClipDuration = Duration(milliseconds: (durationSec * 1000).round());
                            _primaryTransitionOut = transition;
                          });
                          Navigator.pop(ctx);
                        },
                        style: ElevatedButton.styleFrom(backgroundColor: _primary, foregroundColor: Colors.white, padding: const EdgeInsets.symmetric(vertical: 12)),
                        child: const Text('Done'),
                      ),
                    ),
                    const SizedBox(height: 12),
                  ],
                ),
              ),
            ),
          );
        });
      },
    );
  }

  // Per-clip editor: pinch-zoom/pan (InteractiveViewer — a smaller,
  // self-contained gesture surface than the primary media's hand-
  // rolled reframe, since it only needs to feed zoom/pan numbers to
  // ffmpeg, not drive a live composited multi-layer preview) + a
  // rotation slider + duration (photos) or speed (videos) +
  // transition-into-next picker.
  Future<void> _editManualClip(int index) async {
    final clip = _manualClips[index];
    final isLast = index == _manualClips.length - 1;
    final transformController = TransformationController(
      Matrix4.identity()
        ..translate(clip.panX * 80, clip.panY * 80)
        ..scale(clip.zoom),
    );
    double rotation = clip.rotationDeg;
    double durationSec = clip.duration.inMilliseconds / 1000.0;
    double speed = clip.speed;
    _TransitionKind transition = clip.transitionOut;

    await showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF16161A),
      isScrollControlled: true,
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          return Padding(
            padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom),
            child: SafeArea(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const SizedBox(height: 12),
                  Container(width: 36, height: 4, decoration: BoxDecoration(color: Colors.white24, borderRadius: BorderRadius.circular(2))),
                  const SizedBox(height: 14),
                  Text('Clip ${index + 2} — pinch to zoom, drag to pan',
                      style: const TextStyle(color: Colors.white70, fontSize: 12)),
                  const SizedBox(height: 10),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(12),
                    child: SizedBox(
                      height: 260,
                      child: InteractiveViewer(
                        transformationController: transformController,
                        minScale: 1.0,
                        maxScale: 3.0,
                        child: Transform.rotate(
                          angle: rotation * math.pi / 180,
                          child: clip.isVideo
                              ? Container(color: Colors.grey[900], child: const Center(child: Icon(Icons.videocam, color: Colors.white54, size: 40)))
                              : Image.file(clip.file, fit: BoxFit.cover, width: double.infinity, height: double.infinity),
                        ),
                      ),
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                    child: Column(
                      children: [
                        Row(
                          children: [
                            const Icon(Icons.rotate_left_rounded, color: Colors.white54, size: 18),
                            Expanded(
                              child: Slider(
                                value: rotation, min: -45, max: 45,
                                activeColor: _primary,
                                onChanged: (v) => setSheetState(() => rotation = v),
                              ),
                            ),
                            const Icon(Icons.rotate_right_rounded, color: Colors.white54, size: 18),
                          ],
                        ),
                        if (clip.isVideo) ...[
                          Row(
                            children: [
                              const Icon(Icons.slow_motion_video_rounded, color: Colors.white54, size: 18),
                              Expanded(
                                child: Slider(
                                  value: speed, min: 0.5, max: 2.5, divisions: 20,
                                  activeColor: _primary,
                                  label: '${speed.toStringAsFixed(2)}x',
                                  onChanged: (v) => setSheetState(() => speed = v),
                                ),
                              ),
                              SizedBox(width: 44, child: Text('${speed.toStringAsFixed(2)}x', style: const TextStyle(color: Colors.white70, fontSize: 12))),
                            ],
                          ),
                        ] else ...[
                          Row(
                            children: [
                              const Icon(Icons.timer_outlined, color: Colors.white54, size: 18),
                              Expanded(
                                child: Slider(
                                  value: durationSec, min: 0.5, max: 6.0, divisions: 22,
                                  activeColor: _primary,
                                  label: '${durationSec.toStringAsFixed(1)}s',
                                  onChanged: (v) => setSheetState(() => durationSec = v),
                                ),
                              ),
                              SizedBox(width: 44, child: Text('${durationSec.toStringAsFixed(1)}s', style: const TextStyle(color: Colors.white70, fontSize: 12))),
                            ],
                          ),
                        ],
                        if (!isLast) ...[
                          const SizedBox(height: 6),
                          Align(alignment: Alignment.centerLeft, child: Text('Agle clip me transition', style: const TextStyle(color: Colors.white54, fontSize: 11))),
                          const SizedBox(height: 6),
                          SizedBox(
                            height: 70,
                            child: ListView.separated(
                              scrollDirection: Axis.horizontal,
                              itemCount: _TransitionKind.values.length,
                              separatorBuilder: (_, __) => const SizedBox(width: 8),
                              itemBuilder: (ctx, i) {
                                final t = _TransitionKind.values[i];
                                final sel = t == transition;
                                return GestureDetector(
                                  onTap: () => setSheetState(() => transition = t),
                                  child: Container(
                                    width: 64,
                                    decoration: BoxDecoration(
                                      color: sel ? _primary.withOpacity(0.25) : Colors.white10,
                                      borderRadius: BorderRadius.circular(10),
                                      border: Border.all(color: sel ? _primary : Colors.transparent, width: 1.5),
                                    ),
                                    child: Column(
                                      mainAxisAlignment: MainAxisAlignment.center,
                                      children: [
                                        Icon(t.icon, color: Colors.white, size: 18),
                                        const SizedBox(height: 4),
                                        Text(t.label, style: const TextStyle(color: Colors.white, fontSize: 10)),
                                      ],
                                    ),
                                  ),
                                );
                              },
                            ),
                          ),
                        ],
                        const SizedBox(height: 14),
                        SizedBox(
                          width: double.infinity,
                          child: ElevatedButton(
                            onPressed: () {
                              final m = transformController.value;
                              // Recover zoom (uniform scale) and pan
                              // (translation, normalized back to the
                              // -1..1 fraction this was seeded from)
                              // from the InteractiveViewer's matrix.
                              final newZoom = m.getMaxScaleOnAxis().clamp(1.0, 3.0);
                              final newPanX = (m.getTranslation().x / 80).clamp(-3.0, 3.0);
                              final newPanY = (m.getTranslation().y / 80).clamp(-3.0, 3.0);
                              setState(() {
                                clip.zoom = newZoom;
                                clip.panX = newPanX;
                                clip.panY = newPanY;
                                clip.rotationDeg = rotation;
                                clip.duration = Duration(milliseconds: (durationSec * 1000).round());
                                clip.speed = speed;
                                clip.transitionOut = transition;
                              });
                              Navigator.pop(ctx);
                            },
                            style: ElevatedButton.styleFrom(backgroundColor: _primary, foregroundColor: Colors.white, padding: const EdgeInsets.symmetric(vertical: 12)),
                            child: const Text('Done'),
                          ),
                        ),
                        const SizedBox(height: 12),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          );
        });
      },
    );
  }

  // Instagram-style trim: draggable start/end handles over a real
  // filmstrip, clamped to [minTrim, maxTrim], with scrub-while-drag
  // (seeks the video as each handle moves) and a live playhead while
  // playing. Fully hand-rolled — no video_editor widget underneath.
  Widget _buildTrimSlider(_SimpleVideoEditController controller) {
    String fmt(Duration d) => '${d.inMinutes}:${(d.inSeconds % 60).toString().padLeft(2, '0')}';
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
      child: AnimatedBuilder(
        animation: Listenable.merge([controller, controller.video]),
        builder: (context, _) {
          final total = controller.duration;
          return Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(fmt(controller.startTrim), style: const TextStyle(color: Colors.white70, fontSize: 12)),
                    Text(fmt(controller.trimmedDuration),
                        style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w700)),
                    Text(fmt(controller.endTrim), style: const TextStyle(color: Colors.white70, fontSize: 12)),
                  ],
                ),
              ),
              SizedBox(
                height: 52,
                child: LayoutBuilder(
                  builder: (context, constraints) {
                    final trackWidth = constraints.maxWidth;
                    double dxFromPos(Duration d) =>
                        total.inMilliseconds == 0 ? 0 : (d.inMilliseconds / total.inMilliseconds) * trackWidth;
                    Duration posFromDx(double dx) {
                      final frac = (dx / trackWidth).clamp(0.0, 1.0);
                      return Duration(milliseconds: (frac * total.inMilliseconds).round());
                    }

                    void onStartDrag(DragUpdateDetails d) {
                      var newStart = posFromDx(dxFromPos(controller.startTrim) + d.delta.dx);
                      final latestStart = controller.endTrim - controller.minTrim;
                      final earliestStart = controller.endTrim - controller.maxTrim;
                      if (newStart < Duration.zero) newStart = Duration.zero;
                      if (newStart > latestStart) newStart = latestStart;
                      if (newStart < earliestStart) newStart = earliestStart;
                      controller.setTrim(newStart, controller.endTrim);
                      controller.video.seekTo(newStart);
                    }

                    void onEndDrag(DragUpdateDetails d) {
                      var newEnd = posFromDx(dxFromPos(controller.endTrim) + d.delta.dx);
                      final earliestEnd = controller.startTrim + controller.minTrim;
                      final latestEnd = controller.startTrim + controller.maxTrim;
                      if (newEnd > total) newEnd = total;
                      if (newEnd < earliestEnd) newEnd = earliestEnd;
                      if (newEnd > latestEnd) newEnd = latestEnd;
                      controller.setTrim(controller.startTrim, newEnd);
                      controller.video.seekTo(newEnd);
                    }

                    final startX = dxFromPos(controller.startTrim);
                    final endX = dxFromPos(controller.endTrim);
                    const handleW = 20.0;

                    return ClipRRect(
                      borderRadius: BorderRadius.circular(8),
                      child: Stack(
                        children: [
                          Positioned.fill(
                            child: controller.thumbnailsReady && controller.thumbnails.isNotEmpty
                                ? Row(
                                    children: controller.thumbnails
                                        .map((t) => Expanded(child: Image.memory(t, fit: BoxFit.cover)))
                                        .toList(),
                                  )
                                : Container(color: Colors.white10),
                          ),
                          Positioned(left: 0, top: 0, bottom: 0, width: startX, child: Container(color: Colors.black54)),
                          Positioned(
                            right: 0,
                            top: 0,
                            bottom: 0,
                            width: (trackWidth - endX).clamp(0.0, trackWidth),
                            child: Container(color: Colors.black54),
                          ),
                          Positioned(
                            left: startX,
                            right: trackWidth - endX,
                            top: 0,
                            bottom: 0,
                            child: IgnorePointer(
                              child: Container(
                                decoration: BoxDecoration(
                                  border: Border.all(color: _primary, width: 2),
                                ),
                              ),
                            ),
                          ),
                          if (controller.video.value.isPlaying)
                            Positioned(
                              left: (dxFromPos(controller.video.value.position) - 1).clamp(0.0, trackWidth - 2),
                              top: 0,
                              bottom: 0,
                              child: IgnorePointer(child: Container(width: 2, color: Colors.white)),
                            ),
                          Positioned(
                            left: (startX - handleW / 2).clamp(0.0, trackWidth - handleW),
                            top: 0,
                            bottom: 0,
                            child: GestureDetector(
                              behavior: HitTestBehavior.opaque,
                              onHorizontalDragUpdate: onStartDrag,
                              child: Container(
                                width: handleW,
                                alignment: Alignment.center,
                                decoration: BoxDecoration(color: _primary, borderRadius: BorderRadius.circular(6)),
                                child: const Icon(Icons.drag_indicator_rounded, color: Colors.white, size: 14),
                              ),
                            ),
                          ),
                          Positioned(
                            left: (endX - handleW / 2).clamp(0.0, trackWidth - handleW),
                            top: 0,
                            bottom: 0,
                            child: GestureDetector(
                              behavior: HitTestBehavior.opaque,
                              onHorizontalDragUpdate: onEndDrag,
                              child: Container(
                                width: handleW,
                                alignment: Alignment.center,
                                decoration: BoxDecoration(color: _primary, borderRadius: BorderRadius.circular(6)),
                                child: const Icon(Icons.drag_indicator_rounded, color: Colors.white, size: 14),
                              ),
                            ),
                          ),
                        ],
                      ),
                    );
                  },
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  // Cover picker built from the same filmstrip thumbnails as the Trim
  // slider — tap one to select it as the cover; it's baked into the
  // exported mp4 as an attached-pic cover stream on export.
  Widget _buildCoverPicker(_SimpleVideoEditController controller) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('Cover frame chuno', style: TextStyle(color: _muted, fontSize: 12, fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          SizedBox(
            height: 56,
            child: AnimatedBuilder(
              animation: controller,
              builder: (context, _) {
                if (!controller.thumbnailsReady) {
                  return const Center(
                    child: SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2, color: _primary),
                    ),
                  );
                }
                if (controller.thumbnails.isEmpty) {
                  return const Center(
                    child: Text('Cover preview available nahi hai', style: TextStyle(color: _muted, fontSize: 12)),
                  );
                }
                final count = controller.thumbnails.length;
                final span = controller.duration.inMilliseconds / count;
                return ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: count,
                  separatorBuilder: (_, __) => const SizedBox(width: 6),
                  itemBuilder: (context, i) {
                    final framePos = Duration(milliseconds: (span * i + span / 2).round());
                    final selected = (framePos - controller.coverPosition).inMilliseconds.abs() < span;
                    return GestureDetector(
                      onTap: () => controller.setCover(framePos),
                      child: Container(
                        width: 44,
                        clipBehavior: Clip.hardEdge,
                        decoration: BoxDecoration(
                          border: Border.all(color: selected ? _primary : Colors.transparent, width: 2),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Image.memory(controller.thumbnails[i], fit: BoxFit.cover),
                      ),
                    );
                  },
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  // Shared FX picker strip — same widget rendered from both the image
  // "FX" tab and the video "FX" tab, since the effect list and
  // behaviour (live loop, baked-on-export) is identical for both.
  Widget _buildFxSelector() {
    return ListView.separated(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: 16),
      itemCount: _FxKind.values.length,
      separatorBuilder: (_, __) => const SizedBox(width: 14),
      itemBuilder: (context, index) {
        final kind = _FxKind.values[index];
        final selected = kind == _selectedFx;
        return GestureDetector(
          onTap: () {
            if (selected) return;
            HapticFeedback.selectionClick();
            setState(() => _selectedFx = kind);
          },
          child: AnimatedScale(
            duration: const Duration(milliseconds: 180),
            curve: Curves.easeOut,
            scale: selected ? 1.08 : 1.0,
            child: Column(
              children: [
                AnimatedContainer(
                  duration: const Duration(milliseconds: 220),
                  curve: Curves.easeOut,
                  width: 56,
                  height: 56,
                  decoration: BoxDecoration(
                    gradient: selected ? LinearGradient(colors: [_primary, _accent]) : null,
                    color: selected ? null : Colors.white10,
                    shape: BoxShape.circle,
                    border: Border.all(color: selected ? _primary : Colors.transparent, width: 2.5),
                    boxShadow: selected
                        ? [BoxShadow(color: _primary.withOpacity(0.4), blurRadius: 12, spreadRadius: 1)]
                        : [],
                  ),
                  child: Icon(_fxIcon(kind), color: Colors.white, size: 22),
                ),
                const SizedBox(height: 6),
                AnimatedDefaultTextStyle(
                  duration: const Duration(milliseconds: 180),
                  style: TextStyle(
                    color: selected ? Colors.white : _muted,
                    fontSize: 11,
                    fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                  ),
                  child: Text(_fxLabel(kind)),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _adjustSlider(
    String label,
    IconData icon,
    double value,
    ValueChanged<double> onChanged, {
    double min = -100,
  }) {
    return Row(
      children: [
        Icon(icon, color: _muted, size: 18),
        const SizedBox(width: 10),
        SizedBox(
          width: 78,
          child: Text(label, style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600)),
        ),
        Expanded(
          child: SliderTheme(
            data: SliderTheme.of(context).copyWith(
              activeTrackColor: _primary,
              inactiveTrackColor: Colors.white24,
              thumbColor: _accent,
              overlayColor: _primary.withOpacity(0.2),
              trackHeight: 2,
            ),
            child: Slider(
              value: value,
              min: min,
              max: 100,
              onChangeStart: (_) => _captureHistory(),
              onChanged: onChanged,
            ),
          ),
        ),
        SizedBox(
          width: 32,
          child: Text(
            value.round().toString(),
            textAlign: TextAlign.end,
            style: const TextStyle(color: Colors.white70, fontSize: 11),
          ),
        ),
      ],
    );
  }
}