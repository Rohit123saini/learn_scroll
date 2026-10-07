// lib/post/widgets/highlight_launcher.dart
//
// P2-FE — the three entry points the profile screens call. Each returns
// `true` when the Highlights row should be reloaded.
//
//   openHighlightViewer(context, h, {myUserId})   circle tap
//   editHighlightFlow(context, h)                 circle long-press (own profile):
//                                                 sheet -> edit stories/cover | rename | delete
//   createHighlightFlow(context)                  "New +" circle

import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';
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
    if (context.mounted) _snack(context, AppLocalizations.of(context)!.hlUnavailable);
    return true; // it is gone — refresh the row
  } catch (_) {
    if (context.mounted) _snack(context, AppLocalizations.of(context)!.hlOpenFailed);
    return false;
  }
  if (!context.mounted) return false;
  if (detail.stories.isEmpty) {
    _snack(context, AppLocalizations.of(context)!.hlEmpty);
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

enum _HlAction { edit, rename, delete }

Future<bool> editHighlightFlow(BuildContext context, Highlight h) async {
  final l10n = AppLocalizations.of(context)!;
  final cs = Theme.of(context).colorScheme;
  final action = await showModalBottomSheet<_HlAction>(
    context: context,
    showDragHandle: true,
    builder: (c) => SafeArea(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        ListTile(
          leading: const Icon(Icons.photo_library_outlined),
          title: Text(l10n.hlEditStoriesCover),
          onTap: () => Navigator.pop(c, _HlAction.edit),
        ),
        ListTile(
          leading: const Icon(Icons.edit_outlined),
          title: Text(l10n.hlRename),
          onTap: () => Navigator.pop(c, _HlAction.rename),
        ),
        ListTile(
          leading: Icon(Icons.delete_outline_rounded, color: cs.error),
          title: Text(l10n.hlDeleteAction, style: TextStyle(color: cs.error)),
          onTap: () => Navigator.pop(c, _HlAction.delete),
        ),
      ]),
    ),
  );
  if (action == null || !context.mounted) return false;
  switch (action) {
    case _HlAction.edit:
      return _openEditor(context, h);
    case _HlAction.rename:
      return _rename(context, h);
    case _HlAction.delete:
      return _delete(context, h);
  }
}

Future<bool> _openEditor(BuildContext context, Highlight h) async {
  Highlight detail;
  try {
    detail = await _fetchDetail(context, h.id);
  } on HighlightUnavailableException {
    if (context.mounted) _snack(context, AppLocalizations.of(context)!.hlUnavailable);
    return true;
  } catch (_) {
    if (context.mounted) _snack(context, AppLocalizations.of(context)!.hlOpenFailed);
    return false;
  }
  if (!context.mounted) return false;
  final changed = await Navigator.of(context).push<bool>(
    MaterialPageRoute(builder: (_) => HighlightEditorScreen(existing: detail)),
  );
  return changed == true;
}

Future<bool> _rename(BuildContext context, Highlight h) async {
  final name = await showDialog<String>(context: context, builder: (_) => _RenameDialog(initial: h.title));
  if (name == null || name == h.title || !context.mounted) return false;
  try {
    await HighlightService.updateHighlight(h.id, title: name);
    return true;
  } catch (e) {
    if (context.mounted) _snack(context, e.toString().replaceFirst('Exception: ', ''));
    return false;
  }
}

Future<bool> _delete(BuildContext context, Highlight h) async {
  final l10n = AppLocalizations.of(context)!;
  final ok = await showDialog<bool>(
    context: context,
    builder: (c) => AlertDialog(
      title: Text(l10n.hlDeleteQuestion),
      content: Text(l10n.hlDeleteBody),
      actions: [
        TextButton(onPressed: () => Navigator.pop(c, false), child: Text(l10n.cancel)),
        TextButton(
          onPressed: () => Navigator.pop(c, true),
          child: Text(l10n.delete, style: TextStyle(color: Theme.of(c).colorScheme.error)),
        ),
      ],
    ),
  );
  if (ok != true) return false;
  try {
    await HighlightService.deleteHighlight(h.id);
    return true;
  } catch (e) {
    if (context.mounted) _snack(context, e.toString().replaceFirst('Exception: ', ''));
    return false;
  }
}

/// Owns its controller so it is disposed with the dialog (no use-after-dispose
/// during the close animation).
class _RenameDialog extends StatefulWidget {
  final String initial;
  const _RenameDialog({required this.initial});

  @override
  State<_RenameDialog> createState() => _RenameDialogState();
}

class _RenameDialogState extends State<_RenameDialog> {
  late final TextEditingController _ctrl = TextEditingController(text: widget.initial);

  @override
  void initState() {
    super.initState();
    _ctrl.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final name = _ctrl.text.trim();
    return AlertDialog(
      title: Text(l10n.hlRenameTitle),
      content: TextField(
        controller: _ctrl,
        autofocus: true,
        maxLength: 30, // post/highlights.py::MAX_TITLE_LENGTH
        textCapitalization: TextCapitalization.sentences,
        decoration: InputDecoration(labelText: l10n.hlTitleLabel),
        onSubmitted: (_) {
          if (name.isNotEmpty) Navigator.pop(context, name);
        },
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(l10n.cancel)),
        TextButton(onPressed: name.isEmpty ? null : () => Navigator.pop(context, name), child: Text(l10n.save)),
      ],
    );
  }
}

Future<bool> createHighlightFlow(BuildContext context) async {
  final changed = await Navigator.of(context).push<bool>(
    MaterialPageRoute(builder: (_) => const HighlightEditorScreen()),
  );
  return changed == true;
}
