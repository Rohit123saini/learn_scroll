// lib/post/widgets/repost_widgets.dart
//
// Repost feature UI, shared by the home feed (home.dart) and the post
// detail page (screens/singlepost.dart):
//
//   RepostHeader           "Reposted by <user>" strip — replaces the normal
//                          avatar header on a repost card.
//   EmbeddedOriginalPost   bordered preview of the original post (author,
//                          text, first media thumbnail) shown inside a
//                          repost card, or an "unavailable" placeholder.
//   RepostActionButton     Repost button for home.dart's action row
//                          (tap = quick repost, long-press = with caption).
//   showRepostCaptionSheet "Repost with caption" bottom sheet.
//
// Backend contract (post/views.py PostRepostAPIView): a repost is a Post row
// with post_type == 'repost'; `original_post` is the (already flattened)
// source; `repost_caption` is the reposter's optional text. The network call
// itself lives in PostExtrasService.repost().

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../../l10n/app_localizations.dart';
import '../../services/home_api_model_service.dart' show PostModel, PostMediaModel;
import '../../utils/api.dart';
import '../../widgets/ls_ui.dart';
import '../models/models.dart' show SinglePostModel;

/// Mirrors `RepostRequestSerializer.MAX_CAPTION_LENGTH` on the backend.
const int kRepostCaptionMax = 500;

String _abs(String u) => u.startsWith('http') ? u : '${Api.baseUrl}$u';

/// What the embedded preview needs from a post. Built from either the feed's
/// [PostModel] or the detail page's [SinglePostModel], so the preview (and the
/// caption sheet) works from both screens.
class RepostPreview {
  final String username;
  final String? profilePicture;
  final DateTime? createdAt;
  final String text;
  final List<PostMediaModel> media;

  const RepostPreview({
    required this.username,
    this.profilePicture,
    this.createdAt,
    required this.text,
    required this.media,
  });

  factory RepostPreview.fromPost(PostModel p) => RepostPreview(
        username: p.user.username,
        profilePicture: p.user.profilePicture,
        createdAt: p.createdAt,
        text: ((p.content ?? '').trim().isNotEmpty ? p.content! : (p.title ?? '')).trim(),
        media: p.media,
      );

  factory RepostPreview.fromSingle(SinglePostModel p) => RepostPreview(
        username: p.username,
        createdAt: p.createdAt,
        text: (p.caption.trim().isNotEmpty ? p.caption : (p.title ?? '')).trim(),
        media: p.media,
      );
}

/// "Reposted by <user>" header strip for a repost card.
class RepostHeader extends StatelessWidget {
  final String username;
  final DateTime? createdAt;
  final VoidCallback onUserTap;
  final Widget? trailing;
  final EdgeInsetsGeometry padding;

  const RepostHeader({
    super.key,
    required this.username,
    this.createdAt,
    required this.onUserTap,
    this.trailing,
    this.padding = const EdgeInsets.fromLTRB(14, 8, 6, 4),
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final time = createdAt == null
        ? null
        : timeago.format(createdAt!, locale: Localizations.localeOf(context).languageCode);
    return Padding(
      padding: padding,
      child: Row(children: [
        Icon(Icons.repeat_rounded, size: 17, color: cs.onSurfaceVariant),
        const SizedBox(width: 8),
        Expanded(
          child: GestureDetector(
            onTap: onUserTap,
            behavior: HitTestBehavior.opaque,
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(
                l10n.repostedBy(username),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurface),
              ),
              if (time != null) Text(time, style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
            ]),
          ),
        ),
        if (trailing != null) trailing!,
      ]),
    );
  }
}

/// Embedded original-post preview. `preview == null` means the original is
/// gone / not visible to this viewer → "unavailable" placeholder.
class EmbeddedOriginalPost extends StatelessWidget {
  final RepostPreview? preview;
  final VoidCallback? onTap;
  final EdgeInsetsGeometry margin;

  const EmbeddedOriginalPost({
    super.key,
    required this.preview,
    this.onTap,
    this.margin = const EdgeInsets.fromLTRB(14, 0, 14, 12),
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final decoration = BoxDecoration(
      color: cs.surfaceVariant.withOpacity(0.35),
      border: Border.all(color: cs.outlineVariant),
      borderRadius: BorderRadius.circular(12),
    );

    final o = preview;
    if (o == null) {
      return Container(
        margin: margin,
        padding: const EdgeInsets.all(14),
        decoration: decoration,
        child: Row(children: [
          Icon(Icons.info_outline_rounded, size: 18, color: cs.onSurfaceVariant),
          const SizedBox(width: 10),
          Expanded(
            child: Text(l10n.repostOriginalUnavailable, style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
          ),
        ]),
      );
    }

    final hasPic = o.profilePicture != null && o.profilePicture!.isNotEmpty;
    final initial = o.username.isNotEmpty ? o.username[0].toUpperCase() : '?';
    final media = o.media.isNotEmpty ? o.media.first : null;
    final time = o.createdAt == null
        ? null
        : timeago.format(o.createdAt!, locale: Localizations.localeOf(context).languageCode);

    return Container(
      margin: margin,
      decoration: decoration,
      clipBehavior: Clip.antiAlias,
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 6),
              child: Row(children: [
                CircleAvatar(
                  radius: 11,
                  backgroundColor: cs.primary.withOpacity(0.15),
                  backgroundImage: hasPic ? CachedNetworkImageProvider(o.profilePicture!) : null,
                  child: hasPic
                      ? null
                      : Text(initial, style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: cs.primary)),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    o.username,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurface),
                  ),
                ),
                if (time != null) Text(time, style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
              ]),
            ),
            if (o.text.isNotEmpty)
              Padding(
                padding: EdgeInsets.fromLTRB(12, 0, 12, media != null ? 8 : 12),
                child: Text(
                  o.text,
                  maxLines: 5,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 13, height: 1.45, color: cs.onSurface.withOpacity(.92)),
                ),
              ),
            if (media != null) _mediaPreview(media, o.media.length, cs),
          ]),
        ),
      ),
    );
  }

  Widget _mediaPreview(PostMediaModel m, int total, ColorScheme cs) {
    final thumb = (m.thumbnail?.isNotEmpty == true) ? m.thumbnail! : (m.mediaType == 'image' ? m.file : '');
    if (thumb.isEmpty) {
      // Document / audio / video without a thumbnail yet.
      return Container(
        width: double.infinity,
        height: 56,
        color: cs.surfaceVariant,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        child: Row(children: [
          Icon(m.mediaType == 'video' ? Icons.videocam_rounded : Icons.insert_drive_file_rounded,
              color: cs.onSurfaceVariant),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              m.fileName,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
            ),
          ),
        ]),
      );
    }
    return SizedBox(
      width: double.infinity,
      height: 180,
      child: Stack(fit: StackFit.expand, children: [
        CachedNetworkImage(
          imageUrl: _abs(thumb),
          fit: BoxFit.cover,
          memCacheWidth: 720,
          errorWidget: (_, __, ___) => Container(color: cs.surfaceVariant),
        ),
        if (m.mediaType == 'video')
          const Center(child: Icon(Icons.play_circle_fill_rounded, color: Colors.white, size: 38)),
        if (total > 1)
          Positioned(
            right: 8,
            top: 8,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
              decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(10)),
              child: Text('+${total - 1}',
                  style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.w700)),
            ),
          ),
      ]),
    );
  }
}

/// Repost button for the home feed's `.post-actions` row. Same look as
/// home.dart's `_PostActionButton`, plus a long-press for "with caption".
class RepostActionButton extends StatelessWidget {
  final bool active;
  final int count;
  final VoidCallback onTap;
  final VoidCallback onLongPress;

  const RepostActionButton({
    super.key,
    required this.active,
    required this.count,
    required this.onTap,
    required this.onLongPress,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final color = active ? cs.primary : cs.onSurfaceVariant;
    final label = count > 0 ? '${l10n.repostAction} $count' : l10n.repostAction;
    return Semantics(
      button: true,
      selected: active,
      label: l10n.repostAction,
      child: InkWell(
        onTap: onTap,
        onLongPress: onLongPress,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 11),
          child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            Icon(Icons.repeat_rounded, size: 17, color: color),
            const SizedBox(width: 6),
            Flexible(
              child: Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: color),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

/// "Repost with caption" bottom sheet. Resolves to the caption text (may be
/// empty) when the user taps Repost, or null when cancelled/dismissed.
Future<String?> showRepostCaptionSheet(BuildContext context, {required RepostPreview original}) {
  final cs = Theme.of(context).colorScheme;
  return showModalBottomSheet<String>(
    context: context,
    isScrollControlled: true,
    backgroundColor: cs.surface,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    builder: (_) => _RepostCaptionSheet(original: original),
  );
}

class _RepostCaptionSheet extends StatefulWidget {
  final RepostPreview original;
  const _RepostCaptionSheet({required this.original});

  @override
  State<_RepostCaptionSheet> createState() => _RepostCaptionSheetState();
}

class _RepostCaptionSheetState extends State<_RepostCaptionSheet> {
  final _ctrl = TextEditingController();

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Center(
              child: Container(
                width: 36,
                height: 4,
                decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
              ),
            ),
            const SizedBox(height: 14),
            Row(children: [
              Icon(Icons.repeat_rounded, size: 18, color: cs.primary),
              const SizedBox(width: 8),
              Text(l10n.repostWithCaption, style: LsType.head(context, size: 15)),
            ]),
            const SizedBox(height: 12),
            TextField(
              controller: _ctrl,
              autofocus: true,
              minLines: 2,
              maxLines: 4,
              maxLength: kRepostCaptionMax,
              textCapitalization: TextCapitalization.sentences,
              decoration: InputDecoration(
                hintText: l10n.repostCaptionHint,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
            const SizedBox(height: 8),
            EmbeddedOriginalPost(preview: widget.original, margin: EdgeInsets.zero),
            const SizedBox(height: 14),
            Row(mainAxisAlignment: MainAxisAlignment.end, children: [
              TextButton(onPressed: () => Navigator.pop(context), child: Text(l10n.cancel)),
              const SizedBox(width: 8),
              ElevatedButton.icon(
                onPressed: () => Navigator.pop(context, _ctrl.text.trim()),
                icon: const Icon(Icons.repeat_rounded, size: 18),
                label: Text(l10n.repostAction),
              ),
            ]),
          ]),
        ),
      ),
    );
  }
}
