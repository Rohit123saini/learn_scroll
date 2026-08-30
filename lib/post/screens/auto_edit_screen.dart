// ═══════════════════════════════════════════════════════════════════
// Auto Edit / Montage Screen — "beyond Insta Edits"
// ═══════════════════════════════════════════════════════════════════
// Pick multiple photos/videos → pick background music → beat
// detection auto-cuts + arranges every item on the beat → the AUTO
// path additionally reads the music's ENERGY under each slot and
// picks a matching transition (punchy Flash/Shake/Glitch on loud
// sections, smooth Slide/Dissolve/Wipe on quiet ones), plus a coarse
// BPM estimate shown in the review step, plus a slow auto Ken-Burns
// zoom on every photo. From there it's fully EDITABLE — well beyond
// "pick a template": reorder clips (drag), change any clip's
// transition, trim a video's in-point and re-time its speed
// (0.5x–2x), toggle Ken Burns per photo, add a burned-in caption per
// clip, switch the whole montage's aspect ratio (9:16 / 1:1 / 4:5 /
// 16:9) and apply a global color grade (Vibrant / Moody / B&W /
// Vintage / Warm) — then re-render.
//
// This is a SEPARATE screen from MediaEditScreen because it operates
// on a *set* of media items, not one — MediaEditScreen is still what
// you'd open afterwards for a single-clip touch-up (filters/text/etc)
// on the exported montage.mp4 if wanted.
//
// Usage (mirrors MediaEditScreen):
//   final File? result = await Navigator.push(context, MaterialPageRoute(
//     builder: (_) => const AutoEditScreen(),
//   ));
//
// ── Beat + energy analysis (no new package) ─────────────────────
// One ffmpeg pass decodes the chosen music to mono 22.05kHz PCM WAV;
// a plain-Dart short-time-energy onset detector (windowed RMS →
// adaptive-threshold local maxima, min-gap enforced) turns that into
// a list of beat timestamps. The same energy curve is kept around
// afterwards so auto mode can classify each clip's slot as a
// low/medium/high-energy moment (average energy under that slot vs
// the track's overall average) and pick a fitting transition + Ken
// Burns intensity for it. A BPM guess comes from the median gap
// between detected beats, octave-corrected into a normal 70–180
// range. None of this is a real onset/tempo model — it's a
// lightweight heuristic good enough for picking cut points and a
// "vibe" per section; on ambient or very sparse tracks it falls back
// to evenly-spaced cuts and a flat medium-energy tier (see
// `_assignSlotDurations`).
//
// ── Render pipeline (ffmpeg_kit_flutter_new — already a dependency
// for MediaEditScreen's Trim/Speed/Music tabs, no new package) ────
//   1. Normalize each item to a short clip at the CURRENT target
//      canvas (scale+center-crop to fill, like Reels — canvas size
//      depends on the chosen aspect ratio), duration = its assigned
//      beat-slot length (+ a fixed xfade overlap tail for every clip
//      except the last — see the offset-math comment on
//      `_buildMontageVideo`). Videos are read starting at their
//      trim in-point, at their chosen speed (via `setpts`, reading
//      proportionally more/less source so the OUTPUT duration always
//      stays exactly the assigned slot length regardless of speed).
//      Photos with Ken Burns on get an animated crop that shrinks
//      from an oversized cover-fill down to the target canvas over
//      the slot's duration — a slow zoom-in, entirely via ffmpeg
//      filter expressions, same technique already used for the Shake
//      transition's crop-wiggle (the two combine into one crop
//      filter when both are active).
//   2. Chain all clips with ffmpeg's native `xfade` filter. Twelve
//      transitions ship: Flash / Zoom / Wipe / Circle Open / Dissolve
//      / Radial / Squeeze / Smooth Slide / Slide map straight to
//      xfade's own built-in transition catalog — reliable, no custom
//      pixel math needed. Glitch / RGB Split / Shake use an xfade
//      base (pixelize / distance / hblur) PLUS a hand-written pixel
//      effect (rgbashift+noise, or an animated crop) burned into the
//      transition window only, via ffmpeg filter expressions — same
//      "burn a windowed effect with an enable/if() expression" idea
//      already used elsewhere in this app's FX tab.
//   3. An optional global color-grade filter (eq/curves/colorbalance)
//      is appended to every clip's chain, and an optional per-clip
//      caption is burned in last via `drawtext` — see the
//      `_captionFontPath` note below before relying on captions.
//   4. One `-filter_complex` pass produces the full silent montage.
//   5. Music is attached in a second pass (`-shortest`, looped if the
//      track's shorter than the montage, faded out at the tail) —
//      same volume/attach shape as MediaEditScreen's `_musicFile`
//      logic, just simplified since the montage never has its own
//      audio to mix against.
//
// ⚠️ Sanity-check before shipping:
//   - All filter strings here (transitions, Ken Burns crop, color
//     grades, drawtext) are hand-authored (I could not run ffmpeg
//     here to execute them) — the *shape* of each filtergraph is
//     correct ffmpeg syntax and the offset/duration math is derived
//     from first principles (documented inline), but tune the
//     constants (shift amounts, shake radius, xfade duration, Ken
//     Burns zoom amount, grade strength) against real footage before
//     shipping.
//   - Captions are OFF by default: `_captionFontPath` is null, and
//     `_buildClipFilter` silently skips `drawtext` when it is, so a
//     missing font can't break every render. `drawtext` needs either
//     a real `fontfile=` path on-device or an ffmpeg_kit build with
//     fontconfig baked in — set `_captionFontPath` to a real font
//     file on your target devices (or wire up fontconfig) before
//     turning the caption UI on for real users.
//   - Assumes an ffmpeg_kit_flutter_new build with timeline/`enable`
//     support (the "full"/"full-gpl" packages have this; "min" does
//     not) — same requirement your Trim/Speed tabs already have.
//   - `_probeDuration` uses FFprobeKit.getMediaInformation(...).
//     getDuration() — check this matches your installed
//     ffmpeg_kit_flutter_new version's exact API.
//
// ── Add Music (Freesound, Instagram-style) ───────────────────────
// Lifted wholesale from MediaEditScreen's Music tab: besides the
// existing device file-picker, the pickMusic step now also has a
// Freesound search box. Backend (`/post/music/search/`) proxies the
// query and hard-filters to CC0 (public-domain, copyright-free)
// sounds only — the Freesound API key never lives in this app. Tap a
// result to preview it (streamed via video_player), tap "Use" to open
// a crop sheet (1–20s window), and the downloaded+trimmed clip is
// handed to the SAME `_musicFile` this screen's beat-analysis/render
// pipeline already reads — no other call-site changes needed.
//
// pubspec.yaml: reuses ffmpeg_kit_flutter_new, video_player,
// file_selector, path_provider — all already added for
// MediaEditScreen. Freesound search additionally needs:
//   http: ^1.2.0   (only if not already a dependency elsewhere)
// ═══════════════════════════════════════════════════════════════════

import 'dart:async';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';
import 'package:ffmpeg_kit_flutter_new/ffmpeg_kit.dart';
import 'package:ffmpeg_kit_flutter_new/ffprobe_kit.dart';
import 'package:ffmpeg_kit_flutter_new/return_code.dart';
import 'package:file_selector/file_selector.dart';
import 'package:path_provider/path_provider.dart';
import 'package:http/http.dart' as http;
// ⚠️ CHECK THIS PATH — set to match wherever api_service.dart actually
// sits relative to this screen in your project (e.g. '../api/api_service.dart'
// or '../services/api_service.dart'). Only used for the Freesound search
// call — same backend endpoint (`/post/music/search/`) MediaEditScreen's
// Music tab already uses, CC0-filtered server-side.
import '../api_service.dart';

// Reuse the same video-extension check MediaEditScreen exports so
// picked items route to the right normalization path.
bool _isVideoFile(File file) {
  final ext = file.path.toLowerCase().split('.').last;
  return {'mp4', 'mov', 'm4v', 'webm', 'mkv', 'avi', '3gp'}.contains(ext);
}

const int _targetFps = 30;
const double _xfadeDur = 0.4; // seconds of overlap for every cut

// Set this to a valid on-device font file path before relying on the
// caption feature — see the "Sanity-check before shipping" note at
// the top of this file. Left null on purpose so a missing font can
// never silently break every render; captions are just skipped.
const String? _captionFontPath = null; // e.g. '/system/fonts/Roboto-Bold.ttf'

// ── Canvas (aspect ratio) ───────────────────────────────────────────
enum _AspectRatio { reel, square, portrait45, landscape }

extension _AspectRatioMeta on _AspectRatio {
  String get label => switch (this) {
        _AspectRatio.reel => '9:16 Reel',
        _AspectRatio.square => '1:1 Square',
        _AspectRatio.portrait45 => '4:5 Portrait',
        _AspectRatio.landscape => '16:9 Wide',
      };

  (int, int) get dims => switch (this) {
        _AspectRatio.reel => (1080, 1920),
        _AspectRatio.square => (1080, 1080),
        _AspectRatio.portrait45 => (1080, 1350),
        _AspectRatio.landscape => (1920, 1080),
      };
}

// ── Global color grade ──────────────────────────────────────────────
enum _ColorGrade { none, vibrant, moody, bw, vintage, warm }

extension _ColorGradeMeta on _ColorGrade {
  String get label => switch (this) {
        _ColorGrade.none => 'None',
        _ColorGrade.vibrant => 'Vibrant',
        _ColorGrade.moody => 'Moody',
        _ColorGrade.bw => 'B&W',
        _ColorGrade.vintage => 'Vintage',
        _ColorGrade.warm => 'Warm',
      };

  // ffmpeg filter fragment for this grade (may itself contain commas
  // chaining more than one filter), or null to skip the stage
  // entirely.
  String? get ffmpegFilter => switch (this) {
        _ColorGrade.none => null,
        _ColorGrade.vibrant => 'eq=saturation=1.45:contrast=1.08:brightness=0.01',
        _ColorGrade.moody => 'eq=saturation=0.85:contrast=1.18:brightness=-0.04',
        _ColorGrade.bw => 'hue=s=0,eq=contrast=1.15',
        _ColorGrade.vintage =>
          'eq=saturation=0.8:contrast=0.95:brightness=0.02,colorbalance=rs=0.06:gs=0.02:bs=-0.05',
        _ColorGrade.warm => 'colorbalance=rs=0.09:gs=0.02:bs=-0.08:rm=0.05',
      };
}

// ── Transitions ──────────────────────────────────────────────────────
enum _TransitionKind {
  glitch,
  flash,
  zoom,
  shake,
  rgbSplit,
  slide,
  wipe,
  circleOpen,
  dissolve,
  radial,
  squeeze,
  smoothSlide,
}

extension _TransitionMeta on _TransitionKind {
  String get label => switch (this) {
        _TransitionKind.glitch => 'Glitch',
        _TransitionKind.flash => 'Flash',
        _TransitionKind.zoom => 'Zoom',
        _TransitionKind.shake => 'Shake',
        _TransitionKind.rgbSplit => 'RGB Split',
        _TransitionKind.slide => 'Slide',
        _TransitionKind.wipe => 'Wipe',
        _TransitionKind.circleOpen => 'Circle',
        _TransitionKind.dissolve => 'Dissolve',
        _TransitionKind.radial => 'Radial',
        _TransitionKind.squeeze => 'Squeeze',
        _TransitionKind.smoothSlide => 'Smooth Slide',
      };

  IconData get icon => switch (this) {
        _TransitionKind.glitch => Icons.broken_image_outlined,
        _TransitionKind.flash => Icons.flash_on,
        _TransitionKind.zoom => Icons.zoom_in,
        _TransitionKind.shake => Icons.vibration,
        _TransitionKind.rgbSplit => Icons.blur_linear,
        _TransitionKind.slide => Icons.swap_horiz,
        _TransitionKind.wipe => Icons.swipe,
        _TransitionKind.circleOpen => Icons.circle_outlined,
        _TransitionKind.dissolve => Icons.grain,
        _TransitionKind.radial => Icons.radio_button_checked,
        _TransitionKind.squeeze => Icons.compress,
        _TransitionKind.smoothSlide => Icons.swap_horizontal_circle,
      };

  // ffmpeg's own built-in xfade transition name this maps to.
  String get xfadeName => switch (this) {
        _TransitionKind.flash => 'fadewhite',
        _TransitionKind.slide => 'slideleft',
        _TransitionKind.zoom => 'zoomin',
        _TransitionKind.glitch => 'pixelize',
        _TransitionKind.rgbSplit => 'distance',
        _TransitionKind.shake => 'hblur',
        _TransitionKind.wipe => 'wipeleft',
        _TransitionKind.circleOpen => 'circleopen',
        _TransitionKind.dissolve => 'dissolve',
        _TransitionKind.radial => 'radial',
        _TransitionKind.squeeze => 'squeezeh',
        _TransitionKind.smoothSlide => 'smoothleft',
      };
}

int _clipIdCounter = 0;

class _MontageClip {
  final int id = _clipIdCounter++; // stable identity for list keys — survives reorder & duplicate files
  final File file;
  final bool isVideo;
  Duration slotDuration;
  Duration trimStart; // videos only — in-point picked from the source
  Duration? sourceDuration; // videos only — probed full length, bounds the trim/speed sheet
  double speed; // videos only — 0.5x–2.0x playback rate
  bool kenBurns; // photos only — slow zoom pan across the slot
  String? caption; // optional text burned into this clip's slot (see _captionFontPath)
  _TransitionKind transitionOut; // transition into the NEXT clip (ignored on the last clip)

  _MontageClip({
    required this.file,
    required this.isVideo,
    required this.slotDuration,
    this.trimStart = Duration.zero,
    this.sourceDuration,
    this.speed = 1.0,
    this.kenBurns = false,
    this.caption,
    this.transitionOut = _TransitionKind.slide,
  });
}

// Everything `_analyzeAudio` learns about the chosen track in one
// pass: the beat timestamps, the raw short-time-energy curve they
// came from (kept so `_energyTierForWindow` can classify any time
// range afterwards), and a coarse BPM guess.
class _BeatAnalysis {
  final List<Duration> beats;
  final List<double> energies;
  final int hop;
  final int sampleRate;
  final double? bpm;

  _BeatAnalysis({
    required this.beats,
    required this.energies,
    required this.hop,
    required this.sampleRate,
    required this.bpm,
  });
}

enum _EnergyTier { low, medium, high }

enum _Step { pickMedia, pickMusic, generating, review }

class AutoEditScreen extends StatefulWidget {
  const AutoEditScreen({super.key});

  @override
  State<AutoEditScreen> createState() => _AutoEditScreenState();
}

class _AutoEditScreenState extends State<AutoEditScreen> {
  _Step _step = _Step.pickMedia;
  final List<File> _pickedMedia = [];
  File? _musicFile;
  String? _musicFileName;
  Duration _musicDuration = Duration.zero;

  // ─── Freesound music search (Instagram-style "Add Music") ───
  // Only ever shows CC0 (public-domain / copyright-free) results — the
  // backend already hard-filters to CC0 before this even sees them.
  // Device-picked music (above) stays available too — this is an
  // additional source, not a replacement, and shares the same
  // `_musicFile` sink the beat-analysis/render pipeline reads.
  final ApiService _apiService = ApiService();
  final TextEditingController _freesoundQueryCtrl = TextEditingController();
  List<Map<String, dynamic>> _freesoundResults = [];
  bool _freesoundSearching = false;
  String? _freesoundError;
  Map<String, dynamic>? _playingPreviewTrack;
  VideoPlayerController? _freesoundPreviewPlayer; // audio-only stream, reuses video_player
  bool _isCroppingFreesoundTrack = false;
  bool _musicFromFreesound = false; // true = _musicFile is an already-cropped 1-20s clip
  // Selected-track crop state (shown as its own step body before "Use").
  Map<String, dynamic>? _freesoundCropTrack;
  double _freesoundCropStart = 0;
  double _freesoundCropDuration = 15; // clamped to [1, 20]

  static const Color _primary = Color(0xFF6366F1);

  List<_MontageClip> _clips = [];
  File? _renderedVideo;
  Duration _totalDuration = Duration.zero;
  VideoPlayerController? _previewController;

  _AspectRatio _aspectRatio = _AspectRatio.reel;
  _ColorGrade _colorGrade = _ColorGrade.none;
  double? _detectedBpm;

  // Every intermediate/final render file this screen has created on
  // disk, so repeated Regenerate/Auto/Manual runs don't leak
  // normalized-clip + silent-montage + final .mp4 files into the
  // temp dir forever. `_renderedVideo` is excluded while it's the
  // CURRENT preview/export candidate — see `_trackTemp` / `_render`.
  final List<File> _tempFiles = [];

  void _trackTemp(File f) => _tempFiles.add(f);

  Future<void> _cleanupTemp({File? keep}) async {
    for (final f in List<File>.from(_tempFiles)) {
      if (keep != null && f.path == keep.path) continue;
      _tempFiles.remove(f);
      unawaited(f.delete().catchError((_) => f));
    }
  }

  String _progressLabel = '';
  double _progress = 0;
  String? _error;

  int get _targetW => _aspectRatio.dims.$1;
  int get _targetH => _aspectRatio.dims.$2;

  // Set right before Navigator.pop(context, _renderedVideo) on Export
  // — tells dispose() to leave that file alone since the caller now
  // owns it, instead of sweeping it up as an abandoned temp render.
  bool _exported = false;

  @override
  void dispose() {
    _previewController?.dispose();
    _freesoundPreviewPlayer?.dispose();
    _freesoundQueryCtrl.dispose();
    final keep = _exported ? _renderedVideo : null;
    for (final f in _tempFiles) {
      if (keep != null && f.path == keep.path) continue;
      unawaited(f.delete().catchError((_) => f));
    }
    super.dispose();
  }

  // ── Step 1: pick photos/videos ──────────────────────────────────
  Future<void> _pickMedia() async {
    final typeGroup = XTypeGroup(
      label: 'media',
      extensions: const [
        'jpg', 'jpeg', 'png', 'heic', 'webp',
        'mp4', 'mov', 'm4v', 'webm', 'mkv', '3gp',
      ],
    );
    final files = await openFiles(acceptedTypeGroups: [typeGroup]);
    if (files.isEmpty) return;
    setState(() {
      _pickedMedia.addAll(files.map((x) => File(x.path)));
    });
  }

  // ── Step 2: pick background music ───────────────────────────────
  // Device-picker. A Freesound (CC0) search sheet below shares the
  // same `_musicFile` sink — either path ends up with a `File`, which
  // is all the rest of this screen cares about.
  Future<void> _pickMusic() async {
    final typeGroup = XTypeGroup(label: 'audio', extensions: const ['mp3', 'm4a', 'wav', 'aac', 'ogg']);
    final file = await openFile(acceptedTypeGroups: [typeGroup]);
    if (file == null) return;
    final dur = await _probeDuration(file.path);
    setState(() {
      _musicFile = File(file.path);
      _musicFileName = file.name;
      _musicDuration = dur;
      _musicFromFreesound = false;
    });
  }

  Future<Duration> _probeDuration(String path) async {
    final session = await FFprobeKit.getMediaInformation(path);
    final info = session.getMediaInformation();
    final durSec = double.tryParse(info?.getDuration() ?? '') ?? 0.0;
    return Duration(milliseconds: (durSec * 1000).round());
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

  // Opens the crop step for a chosen track — start-offset + duration
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
  // already uses — the beat/energy analysis and render pass don't
  // need to know or care where the file came from.
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
      final croppedFile = File(croppedPath);
      _trackTemp(croppedFile);
      final dur = await _probeDuration(croppedPath);

      if (!mounted) return;
      setState(() {
        _musicFile = croppedFile;
        _musicFileName = '${track['name']} — ${track['artist']} (CC0)';
        _musicDuration = dur;
        _musicFromFreesound = true;
        _freesoundCropTrack = null;
        _freesoundResults = [];
        _freesoundQueryCtrl.clear();
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = 'Music add nahi ho paya: $e');
    } finally {
      if (mounted) setState(() => _isCroppingFreesoundTrack = false);
    }
  }

  // ── Audio analysis: beat onsets + energy curve + a BPM guess ────
  Future<_BeatAnalysis> _analyzeAudio(File audioSource) async {
    const sampleRate = 22050;
    const hop = 512;
    _BeatAnalysis empty() =>
        _BeatAnalysis(beats: [], energies: [], hop: hop, sampleRate: sampleRate, bpm: null);

    final tempDir = await getTemporaryDirectory();
    final wavPath = '${tempDir.path}/beat_${DateTime.now().microsecondsSinceEpoch}.wav';
    final session = await FFmpegKit.execute(
      '-y -i "${audioSource.path}" -ac 1 -ar $sampleRate -f wav "$wavPath"',
    );
    if (!ReturnCode.isSuccess(await session.getReturnCode())) return empty();

    final wavFile = File(wavPath);
    if (!await wavFile.exists()) return empty();
    final bytes = await wavFile.readAsBytes();
    unawaited(wavFile.delete().catchError((_) => wavFile));
    if (bytes.length < 44) return empty();

    // Locate the 'data' subchunk defensively (usually starts right at
    // byte 44, but some encoders add extra chunks before it).
    int dataStart = 44;
    for (int i = 12; i < bytes.length - 8; i++) {
      if (bytes[i] == 0x64 && bytes[i + 1] == 0x61 && bytes[i + 2] == 0x74 && bytes[i + 3] == 0x61) {
        dataStart = i + 8;
        break;
      }
    }

    const windowSize = 1024;
    final byteData = ByteData.sublistView(bytes);
    final sampleCount = (bytes.length - dataStart) ~/ 2;

    final energies = <double>[];
    for (int start = 0; start + windowSize <= sampleCount; start += hop) {
      double sum = 0;
      for (int i = 0; i < windowSize; i++) {
        final byteOffset = dataStart + (start + i) * 2;
        if (byteOffset + 1 >= bytes.length) break;
        final sample = byteData.getInt16(byteOffset, Endian.little) / 32768.0;
        sum += sample * sample;
      }
      energies.add(sum / windowSize);
    }
    if (energies.isEmpty) return empty();

    // Local peak = louder than the ~1s moving average around it,
    // enforcing a minimum gap so cuts can't happen faster than ~3.5/s.
    const smoothingWindow = 43;
    const minGapSec = 0.28;
    final beats = <Duration>[];
    double lastBeatSec = -1;

    for (int i = 0; i < energies.length; i++) {
      final lo = math.max(0, i - smoothingWindow);
      final hi = math.min(energies.length, i + smoothingWindow);
      double avg = 0;
      for (int j = lo; j < hi; j++) {
        avg += energies[j];
      }
      avg /= (hi - lo);

      final isPeak = energies[i] > avg * 1.35 &&
          (i == 0 || energies[i] >= energies[i - 1]) &&
          (i == energies.length - 1 || energies[i] >= energies[i + 1]);

      if (isPeak) {
        final sec = (i * hop) / sampleRate;
        if (sec - lastBeatSec >= minGapSec) {
          beats.add(Duration(milliseconds: (sec * 1000).round()));
          lastBeatSec = sec;
        }
      }
    }

    return _BeatAnalysis(
      beats: beats,
      energies: energies,
      hop: hop,
      sampleRate: sampleRate,
      bpm: _estimateBpm(beats),
    );
  }

  // Median inter-beat interval → BPM, octave-corrected into a normal
  // 70–180 range. A coarse guess (see file header) — good enough to
  // show the user a number and to weight auto pacing, not a tempo
  // lock.
  double? _estimateBpm(List<Duration> beats) {
    if (beats.length < 4) return null;
    final intervals = <double>[];
    for (int i = 1; i < beats.length; i++) {
      final sec = (beats[i] - beats[i - 1]).inMilliseconds / 1000.0;
      if (sec >= 0.25 && sec <= 1.2) intervals.add(sec);
    }
    if (intervals.length < 3) return null;
    intervals.sort();
    final median = intervals[intervals.length ~/ 2];
    if (median <= 0) return null;
    var bpm = 60 / median;
    while (bpm < 70) {
      bpm *= 2;
    }
    while (bpm > 180) {
      bpm /= 2;
    }
    return bpm;
  }

  // Average energy under [start, end) vs the track's overall average
  // — buckets a slot as a quiet/normal/loud moment so auto mode can
  // pick a fitting transition + Ken Burns feel for it.
  _EnergyTier _energyTierForWindow(_BeatAnalysis a, Duration start, Duration end) {
    if (a.energies.isEmpty) return _EnergyTier.medium;
    final startIdx =
        ((start.inMilliseconds / 1000) * a.sampleRate / a.hop).round().clamp(0, a.energies.length - 1);
    final endIdx = ((end.inMilliseconds / 1000) * a.sampleRate / a.hop)
        .round()
        .clamp(startIdx + 1, a.energies.length);
    double sum = 0;
    for (int i = startIdx; i < endIdx; i++) {
      sum += a.energies[i];
    }
    final windowAvg = sum / (endIdx - startIdx);
    final overallAvg = a.energies.reduce((x, y) => x + y) / a.energies.length;
    if (overallAvg <= 0) return _EnergyTier.medium;
    if (windowAvg > overallAvg * 1.3) return _EnergyTier.high;
    if (windowAvg < overallAvg * 0.7) return _EnergyTier.low;
    return _EnergyTier.medium;
  }

  // Turns raw beat timestamps into one duration per media item,
  // beat-aligned where possible, evenly-spread as a fallback when
  // beats are too sparse/dense for the item count.
  List<Duration> _assignSlotDurations({
    required int itemCount,
    required List<Duration> beats,
    required Duration musicDuration,
  }) {
    const minSlot = Duration(milliseconds: 500);
    const maxSlot = Duration(milliseconds: 2600);
    final musicMs = musicDuration.inMilliseconds > 0 ? musicDuration : const Duration(seconds: 15);

    final cuts = <Duration>[Duration.zero];
    for (final b in beats) {
      if (b > cuts.last + minSlot && b < musicMs) cuts.add(b);
    }
    cuts.add(musicMs);

    List<Duration> chosen;
    if (cuts.length - 1 >= itemCount) {
      chosen = [cuts.first];
      for (int k = 1; k < itemCount; k++) {
        final idx = ((k / itemCount) * (cuts.length - 1)).round().clamp(1, cuts.length - 1);
        chosen.add(cuts[idx]);
      }
      chosen.add(cuts.last);
    } else {
      // Not enough distinct beats detected (quiet/ambient track) —
      // fall back to an even split so every item still gets a slot.
      chosen = List.generate(
        itemCount + 1,
        (k) => Duration(milliseconds: (musicMs.inMilliseconds * k / itemCount).round()),
      );
    }

    final slots = <Duration>[];
    for (int i = 0; i < itemCount; i++) {
      var d = chosen[i + 1] - chosen[i];
      if (d < minSlot) d = minSlot;
      if (d > maxSlot) d = maxSlot;
      slots.add(d);
    }
    return slots;
  }

  // Even-split slot durations — no beat detection at all, just
  // divides the music (or a sensible default window when no music
  // duration is known yet) equally across every item, clamped to the
  // SAME [minSlot, maxSlot] bounds `_assignSlotDurations` uses, so
  // the render pipeline behaves identically regardless of which mode
  // produced the clip list.
  List<Duration> _assignEvenSlotDurations({
    required int itemCount,
    required Duration musicDuration,
  }) {
    const minSlot = Duration(milliseconds: 500);
    const maxSlot = Duration(milliseconds: 2600);
    final musicMs = musicDuration.inMilliseconds > 0
        ? musicDuration
        : Duration(milliseconds: 1200 * itemCount);

    final perSlotMs = (musicMs.inMilliseconds / itemCount).round();
    var d = Duration(milliseconds: perSlotMs);
    if (d < minSlot) d = minSlot;
    if (d > maxSlot) d = maxSlot;
    return List.generate(itemCount, (_) => d);
  }

  // ── Auto-generate: beats → energy-aware slots/transitions/Ken Burns → render ──
  Future<void> _autoGenerate() async {
    if (_pickedMedia.isEmpty || _musicFile == null) return;
    setState(() {
      _step = _Step.generating;
      _error = null;
      _progress = 0;
      _progressLabel = 'Reading music…';
    });

    try {
      final analysis = await _analyzeAudio(_musicFile!);
      setState(() => _progressLabel = 'Placing cuts on the beat…');

      final slots = _assignSlotDurations(
        itemCount: _pickedMedia.length,
        beats: analysis.beats,
        musicDuration: _musicDuration,
      );

      // Tier-bucketed transition pools — auto mode picks the FEEL of
      // each cut from the music's actual energy under that clip
      // instead of a flat round robin, so drops get punchy cuts and
      // quiet stretches get smooth ones.
      const highPool = [
        _TransitionKind.flash,
        _TransitionKind.shake,
        _TransitionKind.glitch,
        _TransitionKind.rgbSplit,
      ];
      const midPool = [
        _TransitionKind.zoom,
        _TransitionKind.circleOpen,
        _TransitionKind.radial,
        _TransitionKind.squeeze,
      ];
      const lowPool = [
        _TransitionKind.slide,
        _TransitionKind.wipe,
        _TransitionKind.dissolve,
        _TransitionKind.smoothSlide,
      ];
      int highCursor = 0, midCursor = 0, lowCursor = 0;

      final clips = <_MontageClip>[];
      Duration cursor = Duration.zero;
      for (int i = 0; i < _pickedMedia.length; i++) {
        setState(() => _progressLabel = 'Scoring clip ${i + 1} of ${_pickedMedia.length}…');
        final file = _pickedMedia[i];
        final end = cursor + slots[i];
        final tier = _energyTierForWindow(analysis, cursor, end);
        final _TransitionKind t;
        switch (tier) {
          case _EnergyTier.high:
            t = highPool[highCursor++ % highPool.length];
          case _EnergyTier.medium:
            t = midPool[midCursor++ % midPool.length];
          case _EnergyTier.low:
            t = lowPool[lowCursor++ % lowPool.length];
        }
        final isVideo = _isVideoFile(file);
        final srcDur = isVideo ? await _probeDuration(file.path) : null;
        clips.add(_MontageClip(
          file: file,
          isVideo: isVideo,
          slotDuration: slots[i],
          sourceDuration: srcDur,
          kenBurns: !isVideo, // photos always get a subtle auto zoom in auto mode
          transitionOut: t,
        ));
        cursor = end;
      }
      _clips = clips;
      _detectedBpm = analysis.bpm;
      await _render();
    } catch (e) {
      setState(() {
        _error = 'Generate failed: $e';
        _step = _Step.pickMusic;
      });
    }
  }

  // ── Manual-generate: even slots → default transitions → render ──
  // Same pipeline as auto (same _MontageClip shape, same _render()),
  // just skips beat/energy analysis entirely — every clip starts at
  // an equal, evenly-spread duration, no Ken Burns, and a cycled
  // default transition. Nothing here is beat-aware; it exists purely
  // so the user gets a starting point to hand-edit in the review step
  // — and the review step is where this mode earns its "advanced"
  // keep: reorder, per-clip duration, per-clip transition, per-video
  // trim + speed, per-photo Ken Burns toggle, and per-clip captions
  // all work on `_clips` regardless of which mode built it, plus the
  // global aspect ratio / color grade picker (tune icon, top right).
  Future<void> _manualGenerate() async {
    if (_pickedMedia.isEmpty || _musicFile == null) return;
    setState(() {
      _step = _Step.generating;
      _error = null;
      _progress = 0;
      _progressLabel = 'Setting up clips…';
    });

    try {
      final slots = _assignEvenSlotDurations(
        itemCount: _pickedMedia.length,
        musicDuration: _musicDuration,
      );

      final order = _TransitionKind.values; // cycle through all 12 as a starting point
      final clips = <_MontageClip>[];
      for (int i = 0; i < _pickedMedia.length; i++) {
        final file = _pickedMedia[i];
        final isVideo = _isVideoFile(file);
        final srcDur = isVideo ? await _probeDuration(file.path) : null;
        clips.add(_MontageClip(
          file: file,
          isVideo: isVideo,
          slotDuration: slots[i],
          sourceDuration: srcDur,
          transitionOut: order[i % order.length],
        ));
      }
      _clips = clips;
      _detectedBpm = null;
      await _render();
    } catch (e) {
      setState(() {
        _error = 'Setup failed: $e';
        _step = _Step.pickMusic;
      });
    }
  }

  // ── Render (normalize each clip → xfade chain → attach music) ───
  //
  // Every normalized clip + the silent montage + the previous final
  // render are throwaway once THIS render succeeds — they're tracked
  // in `_tempFiles` as they're created and swept after the new final
  // file is ready, so hitting Regenerate repeatedly (very likely —
  // it's a review-step button) doesn't leave N renders' worth of
  // .mp4s sitting in the temp dir. The just-produced `_renderedVideo`
  // itself is deliberately kept out of that sweep — see `_exported`/
  // `dispose()` — since it may still be on screen or about to be
  // handed back to the caller via Export.
  Future<void> _render() async {
    setState(() {
      _step = _Step.generating;
      _error = null;
      _progress = 0;
    });
    try {
      final tempDir = await getTemporaryDirectory();
      final silentMontage = await _buildMontageVideo(
        _clips,
        tempDir,
        onProgress: (p, label) {
          if (mounted) setState(() { _progress = p; _progressLabel = label; });
        },
        onTempFile: _trackTemp,
      );

      final totalDuration = _clips.fold<Duration>(
        Duration.zero, (sum, c) => sum + c.slotDuration,
      );

      setState(() => _progressLabel = 'Adding music…');
      final finalFile = await _attachMusic(silentMontage, _musicFile!, totalDuration, tempDir);
      _trackTemp(finalFile);

      _previewController?.dispose();
      final controller = VideoPlayerController.file(finalFile);
      await controller.initialize();
      controller.setLooping(true);
      controller.play();

      setState(() {
        _renderedVideo = finalFile;
        _totalDuration = totalDuration;
        _previewController = controller;
        _step = _Step.review;
      });
      // Sweep every tracked temp file except the one that's now on
      // screen — this catches this run's normalized clips + silent
      // montage AND the previous render (it was tracked the same way
      // when IT was produced), so Regenerate never accumulates.
      await _cleanupTemp(keep: finalFile);
    } catch (e) {
      setState(() {
        _error = 'Render failed: $e';
        _step = _Step.review;
      });
    }
  }

  // Normalizes every clip to a uniform canvas, then chains them with
  // xfade.
  //
  // Offset math: give every non-last clip a normalized duration of
  // `slot + xfadeDur` (the extra tail is the material the NEXT xfade
  // blends with) and the last clip exactly `slot` (nothing follows
  // it). Then, for the xfade merging clip i into the running chain,
  // offset_i = sum(slot_0 .. slot_{i-1}) — i.e. just the prefix sum of
  // slot durations, because the "+xfadeDur / -xfadeDur" from each
  // step's own contribution cancels out of the running total. Net
  // result: final montage length = sum(all slot durations), exactly
  // matching the beat-aligned pacing from `_assignSlotDurations`, with
  // the crossfades absorbed inside slot boundaries rather than adding
  // extra length. Per-clip speed only changes how much SOURCE time a
  // video clip consumes to fill its slot — the OUTPUT duration fed
  // into this math is always exactly the slot length (padded with a
  // frozen last frame via `tpad` if the sped-up/trimmed source runs
  // short), so none of the above changes when speed ≠ 1.0.
  Future<File> _buildMontageVideo(
    List<_MontageClip> clips,
    Directory tempDir, {
    required void Function(double progress, String label) onProgress,
    required void Function(File file) onTempFile,
  }) async {
    final n = clips.length;
    final normalizedPaths = <String>[];

    for (int i = 0; i < n; i++) {
      onProgress(i / (n + 1), 'Preparing clip ${i + 1} of $n…');
      final clip = clips[i];
      final isLast = i == n - 1;
      final durSec = clip.slotDuration.inMilliseconds / 1000.0 + (isLast ? 0 : _xfadeDur);
      final outPath = '${tempDir.path}/norm_${i}_${DateTime.now().microsecondsSinceEpoch}.mp4';

      final winStart = math.max(0.0, durSec - _xfadeDur);
      final vf = _buildClipFilter(
        outgoing: isLast ? null : clip.transitionOut,
        winStart: winStart,
        winEnd: durSec,
        kenBurns: !clip.isVideo && clip.kenBurns,
        durSec: durSec,
        caption: clip.caption,
      );

      String cmd;
      if (clip.isVideo) {
        final trimStartSec = clip.trimStart.inMilliseconds / 1000.0;
        final speed = clip.speed.clamp(0.5, 2.0);
        // How much source time we need to read to fill durSec once
        // sped up/down by `speed` (setpts=PTS/speed shortens playback
        // time by that same factor).
        final readSec = durSec * speed;
        final speedFilter = speed == 1.0 ? '' : 'setpts=PTS/${speed.toStringAsFixed(3)},';
        // tpad freezes the last frame if the source (after the speed
        // change) is shorter than durSec starting at trimStart, so
        // every normalized clip is guaranteed exactly durSec long
        // (required for the offset math above to hold) regardless of
        // trim point or speed.
        cmd = '-y -ss $trimStartSec -t $readSec -i "${clip.file.path}" -an '
            '-vf "$speedFilter$vf,tpad=stop_mode=clone:stop_duration=$durSec" '
            '-t $durSec -r $_targetFps "$outPath"';
      } else {
        cmd = '-y -loop 1 -t $durSec -i "${clip.file.path}" '
            '-vf "$vf" -r $_targetFps "$outPath"';
      }

      final session = await FFmpegKit.execute(cmd);
      if (!ReturnCode.isSuccess(await session.getReturnCode())) {
        throw Exception('Failed to normalize clip ${i + 1}');
      }
      normalizedPaths.add(outPath);
      onTempFile(File(outPath));
    }

    if (n == 1) return File(normalizedPaths.first);

    onProgress(n / (n + 1), 'Blending transitions…');
    final inputs = normalizedPaths.map((p) => '-i "$p"').join(' ');
    final filters = <String>[];
    double cumSlotSec = clips[0].slotDuration.inMilliseconds / 1000.0;
    String lastLabel = '0:v';

    for (int i = 1; i < n; i++) {
      final transition = clips[i - 1].transitionOut.xfadeName;
      final outLabel = i == n - 1 ? 'vout' : 'v$i';
      filters.add(
        '[$lastLabel][$i:v]xfade=transition=$transition:duration=$_xfadeDur:'
        'offset=${cumSlotSec.toStringAsFixed(3)}[$outLabel]',
      );
      lastLabel = outLabel;
      if (i < n - 1) cumSlotSec += clips[i].slotDuration.inMilliseconds / 1000.0;
    }

    final outPath = '${tempDir.path}/montage_${DateTime.now().microsecondsSinceEpoch}.mp4';
    final session = await FFmpegKit.execute(
      '-y $inputs -filter_complex "${filters.join(';')}" -map "[vout]" '
      '-r $_targetFps -pix_fmt yuv420p "$outPath"',
    );
    if (!ReturnCode.isSuccess(await session.getReturnCode())) {
      throw Exception('Transition chain failed');
    }
    onTempFile(File(outPath));
    return File(outPath);
  }

  // Base cover-fill scale/crop to the target canvas (optionally
  // animated into a Ken Burns zoom-in), plus the extra pixel-fx
  // burned into the tail window only for clips with an outgoing
  // transition, plus an optional global color grade and an optional
  // per-clip caption.
  String _buildClipFilter({
    required _TransitionKind? outgoing,
    required double winStart,
    required double winEnd,
    required bool kenBurns,
    required double durSec,
    String? caption,
  }) {
    final durStr = durSec.toStringAsFixed(3);

    String cropStage;
    if (kenBurns) {
      // Ken Burns: start on an oversized cover-fill crop and shrink
      // the crop window down to the target canvas over the clip's
      // duration — a slow zoom-in. Photos only (guarded by the
      // caller): panning an already-moving video the same way just
      // looks broken, so this path is never hit for videos. When the
      // outgoing transition is Shake, the same jitter used in the
      // plain-shake branch below is folded into this crop's x/y so
      // the two effects combine instead of fighting over the crop.
      final ow = (_targetW * 1.22).round();
      final oh = (_targetH * 1.22).round();
      final jitterX = outgoing == _TransitionKind.shake
          ? "+if(between(t,$winStart,$winEnd),10*sin(20*(t-$winStart)),0)"
          : '';
      final jitterY = outgoing == _TransitionKind.shake
          ? "+if(between(t,$winStart,$winEnd),10*cos(24*(t-$winStart)),0)"
          : '';
      cropStage = 'scale=$ow:$oh:force_original_aspect_ratio=increase,crop=$ow:$oh,'
          "crop=w='$ow-($ow-$_targetW)*(t/$durStr)':h='$oh-($oh-$_targetH)*(t/$durStr)':"
          "x='($ow-out_w)/2$jitterX':y='($oh-out_h)/2$jitterY'";
    } else if (outgoing == _TransitionKind.shake) {
      // Shake needs to change the CROP itself (not just add an
      // independent filter), because crop changes frame size — so
      // it's its own filter chain that always outputs exactly target
      // size, wiggling only inside the window via an if() expression
      // (an `enable=` flag would fully bypass the crop outside the
      // window and break the fixed canvas size).
      cropStage = 'scale=${_targetW + 40}:${_targetH + 40}:force_original_aspect_ratio=increase,'
          'crop=${_targetW + 40}:${_targetH + 40},'
          'crop=$_targetW:$_targetH:'
          "x='20+if(between(t,$winStart,$winEnd),14*sin(20*(t-$winStart)),0)':"
          "y='20+if(between(t,$winStart,$winEnd),14*cos(24*(t-$winStart)),0)'";
    } else {
      cropStage = 'scale=$_targetW:$_targetH:force_original_aspect_ratio=increase,'
          'crop=$_targetW:$_targetH';
    }

    final enable = "enable='between(t,${winStart.toStringAsFixed(3)},${winEnd.toStringAsFixed(3)})'";
    final pixelFx = <String>[];
    switch (outgoing) {
      case _TransitionKind.rgbSplit:
        pixelFx.add('rgbashift=rh=8:bh=-8:rv=2:bv=-2:$enable');
      case _TransitionKind.glitch:
        pixelFx.add('rgbashift=rh=10:bh=-10:$enable');
        pixelFx.add('noise=alls=25:allf=t+u:$enable');
      default:
        break; // handled entirely by the xfade transition (or the crop, for shake)
    }

    final grade = _colorGrade.ffmpegFilter;

    final stages = <String>[
      cropStage,
      ...pixelFx,
      if (grade != null) grade,
      'fps=$_targetFps',
      'format=yuv420p',
    ];

    if (caption != null && caption.trim().isNotEmpty && _captionFontPath != null) {
      final escaped = caption
          .replaceAll('\\', r'\\')
          .replaceAll(':', r'\:')
          .replaceAll("'", r"\'");
      stages.add(
        "drawtext=fontfile='$_captionFontPath':text='$escaped':"
        "fontsize=${(_targetW * 0.06).round()}:fontcolor=white:"
        "borderw=3:bordercolor=black@0.6:x=(w-text_w)/2:y=h-h*0.16",
      );
    }

    return stages.join(',');
  }

  Future<File> _attachMusic(File video, File music, Duration totalDuration, Directory tempDir) async {
    final outPath = '${tempDir.path}/final_${DateTime.now().microsecondsSinceEpoch}.mp4';
    final totalSec = totalDuration.inMilliseconds / 1000.0;
    final fadeStart = math.max(0.0, totalSec - 0.6);
    final session = await FFmpegKit.execute(
      '-y -i "${video.path}" -stream_loop -1 -i "${music.path}" '
      '-map 0:v -map 1:a -c:v copy '
      '-af "afade=t=out:st=$fadeStart:d=0.6" '
      '-shortest -t $totalSec "$outPath"',
    );
    if (!ReturnCode.isSuccess(await session.getReturnCode())) {
      throw Exception('Attaching music failed');
    }
    return File(outPath);
  }

  // ── Edit actions (post auto/manual-generate) ─────────────────────
  void _reorder(int oldIndex, int newIndex) {
    setState(() {
      if (newIndex > oldIndex) newIndex -= 1;
      final item = _clips.removeAt(oldIndex);
      _clips.insert(newIndex, item);
    });
  }

  Future<void> _pickTransitionFor(int clipIndex) async {
    final chosen = await showModalBottomSheet<_TransitionKind>(
      context: context,
      backgroundColor: const Color(0xFF1C1C1E),
      builder: (ctx) => SafeArea(
        child: Wrap(
          children: _TransitionKind.values.map((t) {
            return ListTile(
              leading: Icon(t.icon, color: Colors.white),
              title: Text(t.label, style: const TextStyle(color: Colors.white)),
              onTap: () => Navigator.pop(ctx, t),
            );
          }).toList(),
        ),
      ),
    );
    if (chosen != null) {
      setState(() => _clips[clipIndex].transitionOut = chosen);
    }
  }

  void _adjustDuration(int clipIndex, Duration newDuration) {
    setState(() => _clips[clipIndex].slotDuration = newDuration);
  }

  Future<void> _editCaption(int clipIndex) async {
    final controller = TextEditingController(text: _clips[clipIndex].caption ?? '');
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF1C1C1E),
        title: const Text('Clip text', style: TextStyle(color: Colors.white)),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLength: 60,
          style: const TextStyle(color: Colors.white),
          decoration: const InputDecoration(hintText: 'Caption for this clip'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, ''), child: const Text('Clear')),
          TextButton(onPressed: () => Navigator.pop(ctx, controller.text), child: const Text('Save')),
        ],
      ),
    );
    if (result == null) return;
    setState(() => _clips[clipIndex].caption = result.trim().isEmpty ? null : result.trim());
    if (result.trim().isNotEmpty && _captionFontPath == null && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text('Set _captionFontPath before rendering, or this text will be skipped.'),
      ));
    }
  }

  Future<void> _openTrimSpeedSheet(int clipIndex) async {
    final clip = _clips[clipIndex];
    final maxTrimMs = clip.sourceDuration != null
        ? math.max(0, clip.sourceDuration!.inMilliseconds - 500)
        : 0;
    double trimMs = clip.trimStart.inMilliseconds.toDouble().clamp(0, maxTrimMs.toDouble());
    double speed = clip.speed;

    await showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF1C1C1E),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) => Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Trim & Speed', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
              const SizedBox(height: 12),
              if (maxTrimMs > 0) ...[
                Text('In-point: ${(trimMs / 1000).toStringAsFixed(1)}s', style: const TextStyle(color: Colors.white70)),
                Slider(
                  value: trimMs,
                  min: 0,
                  max: maxTrimMs.toDouble(),
                  onChanged: (v) => setSheet(() => trimMs = v),
                ),
              ] else
                const Text('Source length unknown — in-point disabled', style: TextStyle(color: Colors.white38, fontSize: 12)),
              const SizedBox(height: 12),
              Text('Speed: ${speed.toStringAsFixed(2)}x', style: const TextStyle(color: Colors.white70)),
              Slider(
                value: speed,
                min: 0.5,
                max: 2.0,
                divisions: 30,
                onChanged: (v) => setSheet(() => speed = v),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: () {
                    setState(() {
                      clip.trimStart = Duration(milliseconds: trimMs.round());
                      clip.speed = speed;
                    });
                    Navigator.pop(ctx);
                  },
                  child: const Text('Done'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _openStyleSheet() async {
    await showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF1C1C1E),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) => Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Canvas', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: _AspectRatio.values.map((a) {
                  return ChoiceChip(
                    label: Text(a.label),
                    selected: _aspectRatio == a,
                    onSelected: (_) {
                      setState(() => _aspectRatio = a);
                      setSheet(() {});
                    },
                  );
                }).toList(),
              ),
              const SizedBox(height: 16),
              const Text('Color grade', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: _ColorGrade.values.map((g) {
                  return ChoiceChip(
                    label: Text(g.label),
                    selected: _colorGrade == g,
                    onSelected: (_) {
                      setState(() => _colorGrade = g);
                      setSheet(() {});
                    },
                  );
                }).toList(),
              ),
              if (_step == _Step.review) ...[
                const SizedBox(height: 16),
                const Text(
                  'Hit Regenerate to re-render with the new canvas/grade.',
                  style: TextStyle(color: Colors.white38, fontSize: 12),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        title: const Text('Auto Edit'),
        actions: [
          if (_step == _Step.pickMusic || _step == _Step.review)
            IconButton(
              icon: const Icon(Icons.tune, color: Colors.white),
              tooltip: 'Canvas & color grade',
              onPressed: _openStyleSheet,
            ),
          if (_step == _Step.review && _renderedVideo != null)
            TextButton(
              onPressed: () {
                _exported = true;
                Navigator.pop(context, _renderedVideo);
              },
              child: const Text('Export', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
            ),
        ],
      ),
      body: switch (_step) {
        _Step.pickMedia => _buildPickMediaStep(),
        _Step.pickMusic => _buildPickMusicStep(),
        _Step.generating => _buildGeneratingStep(),
        _Step.review => _buildReviewStep(),
      },
    );
  }

  Widget _buildPickMediaStep() {
    return Column(
      children: [
        Expanded(
          child: _pickedMedia.isEmpty
              ? Center(
                  child: Text('Photos/videos add karo — kam se kam 2',
                      style: TextStyle(color: Colors.white.withOpacity(0.6))),
                )
              : GridView.builder(
                  padding: const EdgeInsets.all(12),
                  gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                    crossAxisCount: 3, crossAxisSpacing: 6, mainAxisSpacing: 6,
                  ),
                  itemCount: _pickedMedia.length,
                  itemBuilder: (ctx, i) {
                    final file = _pickedMedia[i];
                    final isVideo = _isVideoFile(file);
                    return Stack(
                      fit: StackFit.expand,
                      children: [
                        ClipRRect(
                          borderRadius: BorderRadius.circular(8),
                          child: isVideo
                              ? Container(color: Colors.grey[900], child: const Icon(Icons.videocam, color: Colors.white54))
                              : Image.file(file, fit: BoxFit.cover),
                        ),
                        Positioned(
                          top: 4, right: 4,
                          child: GestureDetector(
                            onTap: () => setState(() => _pickedMedia.removeAt(i)),
                            child: const CircleAvatar(radius: 12, backgroundColor: Colors.black87, child: Icon(Icons.close, size: 14, color: Colors.white)),
                          ),
                        ),
                      ],
                    );
                  },
                ),
        ),
        Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _pickMedia,
                  icon: const Icon(Icons.add_photo_alternate),
                  label: const Text('Add Media'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: ElevatedButton(
                  onPressed: _pickedMedia.length >= 2 ? () => setState(() => _step = _Step.pickMusic) : null,
                  child: const Text('Next: Music'),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildPickMusicStep() {
    // Track-crop step takes over the whole body, same as MediaEditScreen's
    // Music tab, until "Use this sound" or back is tapped.
    if (_freesoundCropTrack != null) return _buildFreesoundCropSheet();

    return Column(
      children: [
        if (_error != null)
          Padding(padding: const EdgeInsets.all(16), child: Text(_error!, style: const TextStyle(color: Colors.redAccent))),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
          child: Column(
            children: [
              Icon(Icons.music_note, size: 48, color: Colors.white.withOpacity(_musicFile == null ? 0.3 : 0.9)),
              const SizedBox(height: 10),
              Text(
                _musicFile == null ? 'Background music chuno' : (_musicFileName ?? _musicFile!.path.split('/').last),
                textAlign: TextAlign.center,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: Colors.white),
              ),
              const SizedBox(height: 6),
              Text(
                '${_aspectRatio.label} · ${_colorGrade.label} grade — tap the tune icon to change',
                style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 12),
              ),
              const SizedBox(height: 14),
              OutlinedButton.icon(onPressed: _pickMusic, icon: const Icon(Icons.library_music), label: const Text('Device se music chuno')),
            ],
          ),
        ),
        const Divider(color: Colors.white12, height: 1),
        Expanded(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
            child: _buildFreesoundSearch(),
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
          child: Row(
            children: [
              Expanded(
                child: ElevatedButton.icon(
                  onPressed: _musicFile != null ? _autoGenerate : null,
                  icon: const Icon(Icons.auto_awesome),
                  label: const Text('Auto (Beat Sync)'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _musicFile != null ? _manualGenerate : null,
                  icon: const Icon(Icons.tune),
                  label: const Text('Manual Setup'),
                ),
              ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Text(
            'Auto: energy-aware cuts, transitions & Ken Burns, BPM detected.\n'
            'Manual: clips evenly split, khud se sab kuch set karo.',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 12),
          ),
        ),
      ],
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

  // Crop step — Instagram-style: pick any 1–20s window inside the
  // chosen track before it's downloaded/trimmed and handed to the
  // regular `_musicFile` pipeline.
  Widget _buildFreesoundCropSheet() {
    final track = _freesoundCropTrack!;
    final trackDuration = ((track['duration'] as num?) ?? 20).toDouble();
    final maxDuration = math.min(20.0, trackDuration).clamp(1.0, 20.0);
    final maxStart = math.max(0.0, trackDuration - _freesoundCropDuration);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
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
          const SizedBox(height: 8),
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

  Widget _buildGeneratingStep() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          CircularProgressIndicator(value: _progress > 0 ? _progress : null, color: Colors.white),
          const SizedBox(height: 16),
          Text(_progressLabel, style: const TextStyle(color: Colors.white70)),
        ],
      ),
    );
  }

  Widget _buildReviewStep() {
    final controller = _previewController;
    return Column(
      children: [
        if (_error != null)
          Padding(padding: const EdgeInsets.all(12), child: Text(_error!, style: const TextStyle(color: Colors.redAccent))),
        if (_detectedBpm != null || _colorGrade != _ColorGrade.none || _aspectRatio != _AspectRatio.reel)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Wrap(
              alignment: WrapAlignment.center,
              spacing: 8,
              children: [
                if (_detectedBpm != null)
                  Chip(
                    avatar: const Icon(Icons.graphic_eq, size: 16, color: Colors.black),
                    label: Text('~${_detectedBpm!.round()} BPM · beat-synced', style: const TextStyle(fontSize: 12)),
                    backgroundColor: Colors.white,
                  ),
                Chip(
                  avatar: const Icon(Icons.crop, size: 16, color: Colors.black),
                  label: Text(_aspectRatio.label, style: const TextStyle(fontSize: 12)),
                  backgroundColor: Colors.white70,
                ),
                if (_colorGrade != _ColorGrade.none)
                  Chip(
                    avatar: const Icon(Icons.palette, size: 16, color: Colors.black),
                    label: Text(_colorGrade.label, style: const TextStyle(fontSize: 12)),
                    backgroundColor: Colors.white70,
                  ),
              ],
            ),
          ),
        if (controller != null && controller.value.isInitialized)
          AspectRatio(
            aspectRatio: controller.value.aspectRatio,
            child: VideoPlayer(controller),
          )
        else
          const Expanded(child: Center(child: CircularProgressIndicator(color: Colors.white))),
        Expanded(
          child: ReorderableListView.builder(
            padding: const EdgeInsets.symmetric(vertical: 8),
            itemCount: _clips.length,
            onReorder: _reorder,
            itemBuilder: (ctx, i) => _buildClipCard(i),
          ),
        ),
        Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () => setState(() => _step = _Step.pickMusic),
                  icon: const Icon(Icons.music_note),
                  label: const Text('Change Music'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: ElevatedButton.icon(
                  onPressed: _render,
                  icon: const Icon(Icons.refresh),
                  label: const Text('Regenerate'),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildClipCard(int i) {
    final clip = _clips[i];
    final isLast = i == _clips.length - 1;
    final hasCaption = clip.caption != null && clip.caption!.isNotEmpty;

    return Card(
      key: ValueKey(clip.id),
      color: const Color(0xFF1C1C1E),
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          ListTile(
            dense: true,
            leading: Icon(clip.isVideo ? Icons.videocam : Icons.image, color: Colors.white70),
            title: Text(
              'Clip ${i + 1} · ${(clip.slotDuration.inMilliseconds / 1000).toStringAsFixed(1)}s'
              '${clip.isVideo && clip.speed != 1.0 ? ' · ${clip.speed.toStringAsFixed(2)}x' : ''}',
              style: const TextStyle(color: Colors.white),
            ),
            subtitle: isLast
                ? const Text('Last clip', style: TextStyle(color: Colors.white38))
                : Slider(
                    value: clip.slotDuration.inMilliseconds.toDouble().clamp(500, 2600),
                    min: 500, max: 2600,
                    onChanged: (v) => _adjustDuration(i, Duration(milliseconds: v.round())),
                  ),
            trailing: isLast ? const Icon(Icons.drag_handle, color: Colors.white38) : null,
          ),
          Padding(
            padding: const EdgeInsets.only(left: 12, right: 12, bottom: 10),
            child: Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                if (!isLast)
                  ActionChip(
                    avatar: Icon(clip.transitionOut.icon, size: 16, color: Colors.white),
                    label: Text(clip.transitionOut.label, style: const TextStyle(color: Colors.white, fontSize: 12)),
                    backgroundColor: Colors.white12,
                    onPressed: () => _pickTransitionFor(i),
                  ),
                if (!clip.isVideo)
                  FilterChip(
                    avatar: Icon(Icons.movie_filter, size: 16, color: clip.kenBurns ? Colors.black : Colors.white),
                    label: Text('Ken Burns', style: TextStyle(fontSize: 12, color: clip.kenBurns ? Colors.black : Colors.white)),
                    selected: clip.kenBurns,
                    selectedColor: Colors.white,
                    backgroundColor: Colors.white12,
                    onSelected: (v) => setState(() => clip.kenBurns = v),
                  ),
                if (clip.isVideo)
                  ActionChip(
                    avatar: const Icon(Icons.content_cut, size: 16, color: Colors.white),
                    label: const Text('Trim & Speed', style: TextStyle(color: Colors.white, fontSize: 12)),
                    backgroundColor: Colors.white12,
                    onPressed: () => _openTrimSpeedSheet(i),
                  ),
                ActionChip(
                  avatar: Icon(Icons.text_fields, size: 16, color: hasCaption ? Colors.black : Colors.white),
                  label: Text(
                    hasCaption ? 'Text ✓' : 'Add Text',
                    style: TextStyle(fontSize: 12, color: hasCaption ? Colors.black : Colors.white),
                  ),
                  backgroundColor: hasCaption ? Colors.white : Colors.white12,
                  onPressed: () => _editCaption(i),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}