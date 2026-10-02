// lib/post/widgets/story_caption_sheet.dart
//
// Task 11 — the create-story entry point in home.dart (`_pickAndUploadStory`)
// picked media and called `StoryService.createStory` immediately, with no
// step in between — even though `createStory` already accepts an optional
// `caption`, nothing in the upload flow ever offered the user a way to set
// one. This sheet is that missing step: full-bleed preview of the picked
// photo/video, an optional caption field, and a Share button. Backing out
// (X button or system back) aborts the upload — `home.dart` treats a
// `null` return as "don't upload".
//
// UI/UX PASS — same contract (returns the caption or `null` on close),
// same muted-looping video preview behaviour. Only the chrome around the
// preview was redesigned: a frosted close button, a glassy rounded caption
// pill with a live character counter, and a circular accent Share button
// that matches the LearnScroll brand purple used across the app — plus
// full localization via AppLocalizations, so the hint and button text
// follow whichever language (English/Hindi) is active.
//
// STORIES UPGRADE, PART 2 — stickers. The top-right button opens the sticker
// tray (Mention / Link / Poll / Question, see story_sticker_pickers.dart).
// Placed stickers are drawn on a 9:16 canvas through the SAME widgets the
// viewer uses (story_sticker_widgets.dart), and can be dragged, pinch-scaled
// and rotated; dragging one onto the trash zone removes it. The media preview
// is `contain`-fitted inside that canvas (like the viewer) so a sticker sits
// on the same spot of the picture that viewers will see.

import 'dart:io';
import 'dart:math' as math;
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

import '../../l10n/app_localizations.dart';
import '../models/story_model.dart' show StickerDraft, StorySticker;
import '../screens/close_friends_screen.dart';
import 'story_sticker_pickers.dart';
import 'story_sticker_widgets.dart';

const int _kCaptionMaxLength = 200;
const Color _kStoryAccent = Color(0xFF8B7CFF); // matches home.dart's dark-mode primary

/// What the compose sheet returns when the user taps Share.
/// Stories upgrade, Part 1: `audience` is 'everyone' or 'close_friends'.
class StoryComposeResult {
  final String caption; // possibly empty
  final String audience;
  // Stories upgrade, Part 2: overlays in stacking order (last = on top).
  final List<StickerDraft> stickers;
  const StoryComposeResult({required this.caption, this.audience = 'everyone', this.stickers = const []});
}

/// Returns the caption + audience when the user taps Share, or `null` if
/// they close the sheet without sharing.
Future<StoryComposeResult?> showStoryCaptionSheet(
  BuildContext context, {
  required File media,
  required String mediaType,
}) {
  return showModalBottomSheet<StoryComposeResult>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.black,
    useSafeArea: true,
    // A vertical drag on a sticker must move the sticker, not the sheet.
    // The X button and the system back gesture still close it.
    enableDrag: false,
    builder: (_) => _StoryCaptionSheet(media: media, mediaType: mediaType),
  );
}

class _StoryCaptionSheet extends StatefulWidget {
  final File media;
  final String mediaType;
  const _StoryCaptionSheet({required this.media, required this.mediaType});

  @override
  State<_StoryCaptionSheet> createState() => _StoryCaptionSheetState();
}

class _StoryCaptionSheetState extends State<_StoryCaptionSheet> {
  final _captionController = TextEditingController();
  VideoPlayerController? _videoController;
  String _audience = 'everyone'; // 'everyone' | 'close_friends'

  // Stories upgrade, Part 2 — stickers being placed.
  final List<StickerDraft> _stickers = [];
  final GlobalKey _canvasKey = GlobalKey();
  String? _activeStickerId; // sticker currently under the finger
  bool _overTrash = false;
  Offset _startFocal = Offset.zero; // gesture snapshot, canvas coordinates
  double _startX = 0.5;
  double _startY = 0.5;
  double _startScale = 1;
  double _startRotation = 0;

  @override
  void initState() {
    super.initState();
    _captionController.addListener(() => setState(() {})); // drives the live counter
    if (widget.mediaType == 'video') {
      final c = VideoPlayerController.file(widget.media);
      _videoController = c;
      c.initialize().then((_) {
        if (!mounted) return;
        setState(() {});
        c.setLooping(true);
        c.setVolume(0); // preview is muted, same as most compose-screen previews
        c.play();
      });
    }
  }

  @override
  void dispose() {
    _videoController?.dispose();
    _captionController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final remaining = _kCaptionMaxLength - _captionController.text.length;
    return SizedBox(
      height: MediaQuery.of(context).size.height * 0.88,
      child: Stack(
        fit: StackFit.expand,
        children: [
          StoryCanvas(builder: (context, canvas) => _buildCanvas(canvas)),
          // Soft top scrim so the close button reads on bright media too.
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            // IgnorePointer: purely decorative, must not block dragging a sticker near the top.
            child: IgnorePointer(
              child: Container(
                height: 90,
                decoration: const BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [Colors.black54, Colors.transparent],
                  ),
                ),
              ),
            ),
          ),
          Positioned(
            top: 12,
            left: 12,
            child: _FrostedCircleButton(
              icon: Icons.close_rounded,
              onTap: () => Navigator.of(context).pop(), // pops with null → caller aborts
            ),
          ),
          Positioned(
            top: 12,
            right: 12,
            child: _FrostedCircleButton(icon: Icons.emoji_emotions_outlined, onTap: _addSticker),
          ),
          if (_activeStickerId != null) _buildTrashZone(l10n),
          if (_activeStickerId == null)
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: Container(
              padding: EdgeInsets.fromLTRB(14, 32, 14, MediaQuery.of(context).viewInsets.bottom > 0 ? 14 : MediaQuery.of(context).padding.bottom + 14),
              decoration: const BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Colors.transparent, Colors.black87],
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                mainAxisSize: MainAxisSize.min,
                children: [
                  _buildAudienceRow(l10n),
                  const SizedBox(height: 10),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Expanded(
                        child: ClipRRect(
                          borderRadius: BorderRadius.circular(22),
                          child: BackdropFilter(
                            filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
                            child: Container(
                              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                              decoration: BoxDecoration(
                                color: Colors.white.withOpacity(0.14),
                                borderRadius: BorderRadius.circular(22),
                                border: Border.all(color: Colors.white.withOpacity(0.18)),
                              ),
                              child: TextField(
                                controller: _captionController,
                                style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w500),
                                maxLength: _kCaptionMaxLength,
                                maxLines: 3,
                                minLines: 1,
                                textCapitalization: TextCapitalization.sentences,
                                cursorColor: _kStoryAccent,
                                decoration: InputDecoration(
                                  hintText: l10n.addCaptionHint,
                                  hintStyle: const TextStyle(color: Colors.white60),
                                  counterText: '',
                                  border: InputBorder.none,
                                  isDense: true,
                                  contentPadding: const EdgeInsets.symmetric(vertical: 12),
                                ),
                              ),
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(width: 10),
                      _ShareButton(
                        onTap: () => Navigator.of(context).pop(StoryComposeResult(
                          caption: _captionController.text.trim(),
                          audience: _audience,
                          stickers: List<StickerDraft>.of(_stickers),
                        )),
                        label: l10n.share,
                      ),
                    ],
                  ),
                  if (_captionController.text.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 6, right: 4),
                      child: Text(
                        '$remaining',
                        style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: remaining <= 20 ? const Color(0xFFFF6B6B) : Colors.white54),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  // ─────────────────────────────────────────────────────────────────────
  // Stickers
  // ─────────────────────────────────────────────────────────────────────

  Future<void> _addSticker() async {
    FocusScope.of(context).unfocus();
    final draft = await pickStickerDraft(context, existing: _stickers, audience: _audience);
    if (draft == null || !mounted) return;
    setState(() => _stickers.add(draft));
  }

  /// Switching to Close Friends: the server only allows mentioning Close
  /// Friends there, and the composer does not know that list, so existing
  /// mention stickers are dropped (after asking) instead of failing the upload.
  Future<void> _setAudience(String value) async {
    if (value == _audience) return;
    if (value == 'close_friends' && _stickers.any((d) => d.kind == StorySticker.kMention)) {
      final l10n = AppLocalizations.of(context)!;
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          content: Text(l10n.stickerCloseFriendsMentionWarn),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: Text(l10n.cancel)),
            TextButton(onPressed: () => Navigator.pop(c, true), child: Text(l10n.stickerRemoveMentions)),
          ],
        ),
      );
      if (ok != true || !mounted) return;
      setState(() => _stickers.removeWhere((d) => d.kind == StorySticker.kMention));
    }
    setState(() => _audience = value);
  }

  Widget _buildCanvas(Size canvas) {
    return Stack(
      key: _canvasKey,
      clipBehavior: Clip.hardEdge,
      children: [
        Positioned.fill(child: _buildPreview()),
        for (final d in _stickers) _buildDraft(d, canvas),
      ],
    );
  }

  Widget _buildDraft(StickerDraft d, Size canvas) {
    return StickerPlacement(
      key: ValueKey(d.localId),
      x: d.x,
      y: d.y,
      rotation: d.rotation,
      scale: d.scale,
      canvas: canvas,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onScaleStart: (details) => _onStickerScaleStart(d, details),
        onScaleUpdate: (details) => _onStickerScaleUpdate(d, details, canvas),
        onScaleEnd: (_) => _onStickerScaleEnd(d),
        // The sticker itself is inert here — this detector owns every gesture.
        child: IgnorePointer(child: StoryStickerView(sticker: d.toPreview())),
      ),
    );
  }

  Offset? _canvasPoint(Offset global) {
    final box = _canvasKey.currentContext?.findRenderObject();
    return box is RenderBox ? box.globalToLocal(global) : null;
  }

  void _onStickerScaleStart(StickerDraft d, ScaleStartDetails details) {
    final p = _canvasPoint(details.focalPoint);
    if (p == null) return;
    _startFocal = p;
    _startX = d.x;
    _startY = d.y;
    _startScale = d.scale;
    _startRotation = d.rotation;
    setState(() {
      _activeStickerId = d.localId;
      _overTrash = false;
      _stickers.remove(d); // touching a sticker brings it to the front
      _stickers.add(d);
    });
  }

  void _onStickerScaleUpdate(StickerDraft d, ScaleUpdateDetails details, Size canvas) {
    if (_activeStickerId != d.localId) return;
    final p = _canvasPoint(details.focalPoint);
    if (p == null) return;
    var rotation = _startRotation + details.rotation * 180 / math.pi;
    while (rotation > 180) {
      rotation -= 360;
    }
    while (rotation < -180) {
      rotation += 360;
    }
    final screen = MediaQuery.of(context).size;
    setState(() {
      d.x = (_startX + (p.dx - _startFocal.dx) / canvas.width).clamp(0.0, 1.0).toDouble();
      d.y = (_startY + (p.dy - _startFocal.dy) / canvas.height).clamp(0.0, 1.0).toDouble();
      d.scale = (_startScale * details.scale).clamp(0.4, 4.0).toDouble();
      d.rotation = rotation;
      // Trash zone = bottom-centre of the screen, where the button shows up.
      _overTrash = details.focalPoint.dy > screen.height - 150 && (details.focalPoint.dx - screen.width / 2).abs() < screen.width * 0.25;
    });
  }

  void _onStickerScaleEnd(StickerDraft d) {
    if (_activeStickerId != d.localId) return;
    setState(() {
      if (_overTrash) _stickers.remove(d);
      _activeStickerId = null;
      _overTrash = false;
    });
  }

  Widget _buildTrashZone(AppLocalizations l10n) {
    return Positioned(
      left: 0,
      right: 0,
      bottom: MediaQuery.of(context).padding.bottom + 24,
      child: IgnorePointer(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          AnimatedContainer(
            duration: const Duration(milliseconds: 120),
            width: _overTrash ? 64 : 52,
            height: _overTrash ? 64 : 52,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: _overTrash ? const Color(0xFFFF4D4D) : Colors.white.withOpacity(0.2),
              border: Border.all(color: Colors.white.withOpacity(0.5)),
            ),
            child: Icon(Icons.delete_outline_rounded, color: Colors.white, size: _overTrash ? 30 : 26),
          ),
          const SizedBox(height: 6),
          Text(l10n.stickerDragToRemove, style: const TextStyle(color: Colors.white70, fontSize: 11.5, fontWeight: FontWeight.w600)),
        ]),
      ),
    );
  }

  /// Audience picker: "Your story" (all followers) vs "Close Friends" (green).
  /// "Edit list" opens the Close Friends editor without leaving the sheet.
  Widget _buildAudienceRow(AppLocalizations l10n) {
    Widget chip(String value, String label, IconData icon, Color activeColor) {
      final selected = _audience == value;
      return GestureDetector(
        onTap: () => _setAudience(value),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
          decoration: BoxDecoration(
            color: selected ? activeColor : Colors.white.withOpacity(0.14),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: selected ? activeColor : Colors.white.withOpacity(0.18)),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, size: 15, color: Colors.white),
            const SizedBox(width: 5),
            Text(label, style: const TextStyle(color: Colors.white, fontSize: 12.5, fontWeight: FontWeight.w600)),
          ]),
        ),
      );
    }

    return Row(children: [
      chip('everyone', l10n.storyAudienceYourStory, Icons.public_rounded, _kStoryAccent),
      const SizedBox(width: 8),
      chip('close_friends', l10n.closeFriends, Icons.star_rounded, kCloseFriendsGreen),
      if (_audience == 'close_friends') ...[
        const SizedBox(width: 4),
        TextButton(
          onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const CloseFriendsScreen())),
          style: TextButton.styleFrom(foregroundColor: Colors.white, visualDensity: VisualDensity.compact),
          child: Text(l10n.closeFriendsEditList, style: const TextStyle(fontSize: 12.5, decoration: TextDecoration.underline)),
        ),
      ],
      const Spacer(),
    ]);
  }

  Widget _buildPreview() {
    if (widget.mediaType == 'video') {
      final c = _videoController;
      if (c == null || !c.value.isInitialized) {
        return const Center(child: CircularProgressIndicator(color: _kStoryAccent));
      }
      // `contain`, like the viewer, so sticker positions line up with the picture.
      return Center(child: AspectRatio(aspectRatio: c.value.aspectRatio, child: VideoPlayer(c)));
    }
    return Image.file(widget.media, fit: BoxFit.contain, width: double.infinity, height: double.infinity);
  }
}

class _FrostedCircleButton extends StatelessWidget {
  final IconData icon;
  final VoidCallback onTap;
  const _FrostedCircleButton({required this.icon, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(20),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
        child: Material(
          color: Colors.white.withOpacity(0.16),
          shape: const CircleBorder(),
          child: InkWell(
            customBorder: const CircleBorder(),
            onTap: onTap,
            child: Padding(padding: const EdgeInsets.all(8), child: Icon(icon, color: Colors.white, size: 20)),
          ),
        ),
      ),
    );
  }
}

class _ShareButton extends StatelessWidget {
  final VoidCallback onTap;
  final String label;
  const _ShareButton({required this.onTap, required this.label});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: _kStoryAccent,
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Text(label, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 13.5)),
            const SizedBox(width: 6),
            const Icon(Icons.send_rounded, color: Colors.white, size: 15),
          ]),
        ),
      ),
    );
  }
}