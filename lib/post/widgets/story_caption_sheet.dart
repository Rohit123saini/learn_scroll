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

import 'dart:io';
import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

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
    return SizedBox(
      height: MediaQuery.of(context).size.height * 0.88,
      child: Stack(
        fit: StackFit.expand,
        children: [
          _buildPreview(),
          Positioned(
            top: 8,
            left: 8,
            child: IconButton(
              icon: const Icon(Icons.close, color: Colors.white),
              onPressed: () => Navigator.of(context).pop(), // pops with null → caller aborts
            ),
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: Container(
              padding: const EdgeInsets.fromLTRB(14, 24, 10, 14),
              decoration: const BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Colors.transparent, Colors.black87],
                ),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: TextField(
                      controller: _captionController,
                      style: const TextStyle(color: Colors.white, fontSize: 14),
                      maxLength: 200,
                      maxLines: 3,
                      minLines: 1,
                      textCapitalization: TextCapitalization.sentences,
                      decoration: const InputDecoration(
                        hintText: 'Add a caption…',
                        hintStyle: TextStyle(color: Colors.white54),
                        counterText: '',
                        border: InputBorder.none,
                        isDense: true,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  FilledButton(
                    onPressed: () => Navigator.of(context).pop(_captionController.text.trim()),
                    child: const Text('Share'),
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
        return const Center(child: CircularProgressIndicator(color: Colors.white));
      }
      return FittedBox(
        fit: BoxFit.cover,
        child: SizedBox(width: c.value.size.width, height: c.value.size.height, child: VideoPlayer(c)),
      );
    }
    return Image.file(widget.media, fit: BoxFit.cover, width: double.infinity, height: double.infinity);
  }
}
