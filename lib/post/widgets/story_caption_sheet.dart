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

import 'dart:io';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

import '../../l10n/app_localizations.dart';

const int _kCaptionMaxLength = 200;
const Color _kStoryAccent = Color(0xFF8B7CFF); // matches home.dart's dark-mode primary

/// Returns the entered caption (possibly empty string) when the user taps
/// Share, or `null` if they close the sheet without sharing.
Future<String?> showStoryCaptionSheet(
  BuildContext context, {
  required File media,
  required String mediaType,
}) {
  return showModalBottomSheet<String>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.black,
    useSafeArea: true,
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
          _buildPreview(),
          // Soft top scrim so the close button reads on bright media too.
          Positioned(
            top: 0,
            left: 0,
            right: 0,
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
          Positioned(
            top: 12,
            left: 12,
            child: _FrostedCircleButton(
              icon: Icons.close_rounded,
              onTap: () => Navigator.of(context).pop(), // pops with null → caller aborts
            ),
          ),
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
                      _ShareButton(onTap: () => Navigator.of(context).pop(_captionController.text.trim()), label: l10n.share),
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

  Widget _buildPreview() {
    if (widget.mediaType == 'video') {
      final c = _videoController;
      if (c == null || !c.value.isInitialized) {
        return const Center(child: CircularProgressIndicator(color: _kStoryAccent));
      }
      return FittedBox(
        fit: BoxFit.cover,
        child: SizedBox(width: c.value.size.width, height: c.value.size.height, child: VideoPlayer(c)),
      );
    }
    return Image.file(widget.media, fit: BoxFit.cover, width: double.infinity, height: double.infinity);
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