// lib/post/widgets/highlight_launcher.dart
//
// P2-FE — the three entry points the profile screens call. Each returns
// `true` when the Highlights row should be reloaded.
//
//   openHighlightViewer(context, h, {myUserId})   circle tap
//   editHighlightFlow(context, h)                 circle long-press (own profile)
//   createHighlightFlow(context)                  "New +" circle

import 'package:flutter/material.dart';
import '../models/highlight_model.dart';
import '../models/story_model.dart' show StoryGroup;
import '../screens/story_viewer_screen.dart';
import '../services/highlight_service.dart';
import 'highlight_editor_screen.dart';

void _snack(BuildContext c, String m) => ScaffoldMessenger.of(c).showSnackBar(SnackBar(content: Text(m)));

Future<Highlight> _fetchDetail(BuildContext context, String id) async {
  showDialog(
    context: context,
    barrierDismissible: false,
    useRootNavigator: true,
    builder: (_) => const Center(child: CircularProgressIndicator()),
  );
  try {
    return await HighlightService.getHighlight(id);
  } finally {
    if (context.mounted) Navigator.of(context, rootNavigator: true).pop();
  }
}

/// [myUserId]: the signed-in user's id if the caller has it (used by the
/// viewer only to ignore taps on your own @mention). When the highlight is
/// yours the owner id is used automatically.
Future<bool> openHighlightViewer(BuildContext context, Highlight h, {String? myUserId}) async {
  Highlight detail;
  try {
    detail = await _fetchDetail(context, h.id);
  } on HighlightUnavailableException {
    if (context.mounted) _snack(context, 'This highlight is no longer available.');
    return true; // it is gone — refresh the row
  } catch (_) {
    if (context.mounted) _snack(context, "Couldn't open highlight. Check your connection.");
    return false;
  }
  if (!context.mounted) return false;
  if (detail.stories.isEmpty) {
    _snack(context, 'This highlight is empty.');
    return true;
  }

  // ⚠️ Single spot that depends on `StoryGroup`'s constructor (story_model.dart
  // wasn't shared) — adjust the named args here if they differ.
  final group = StoryGroup(
    userId: detail.ownerId,
    username: detail.ownerUsername,
    userProfilePic: detail.ownerPicture,
    stories: detail.stories,
  );

  final changed = await Navigator.of(context).push<bool>(
    MaterialPageRoute(
      builder: (_) => StoryViewerScreen(
        groups: [group],
        initialGroupIndex: 0,
        myUserId: myUserId ?? (detail.isOwner ? detail.ownerId : null),
        highlight: detail,
      ),
    ),
  );
  return changed == true;
}

Future<bool> editHighlightFlow(BuildContext context, Highlight h) async {
  Highlight detail;
  try {
    detail = await _fetchDetail(context, h.id);
  } catch (_) {
    if (context.mounted) _snack(context, "Couldn't open highlight. Check your connection.");
    return false;
  }
  if (!context.mounted) return false;
  final changed = await Navigator.of(context).push<bool>(
    MaterialPageRoute(builder: (_) => HighlightEditorScreen(existing: detail)),
  );
  return changed == true;
}

Future<bool> createHighlightFlow(BuildContext context) async {
  final changed = await Navigator.of(context).push<bool>(
    MaterialPageRoute(builder: (_) => const HighlightEditorScreen()),
  );
  return changed == true;
}
