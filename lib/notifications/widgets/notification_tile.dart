// lib/notifications/widgets/notification_tile.dart
//
// N5-FE — notification row: overlapping actor avatars (leading), title /
// message, post thumbnail + timeago (trailing). Behaviour carried over
// from notifications_screen.dart's old inline ListTile and
// _buildPostPreviewThumb (TASK 3), unchanged:
//   * thumbnail only for post_liked / post_commented / query_answered /
//     story_mention, and only if `data.media_type` is set (text-only post
//     => no thumbnail block);
//   * video with no thumbnail yet (media_url null) => videocam placeholder;
//   * tapping anywhere (thumbnail included) triggers the row's onTap.
//
// Title text comes straight from the backend (`batch_title` already builds
// "X, Y and 12 others liked your post"), so the tile never composes names.

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../../widgets/ls_ui.dart'; // kLsRadius
import '../models/notification_model.dart';

const double _kAvatarSize = 40; // single avatar
const double _kStackedAvatarSize = 32; // each avatar in a 2-3 stack
const double _kStackOverlap = 18; // horizontal step between stacked avatars
const double _kNotifThumbSize = 44;

const Set<String> _kPostPreviewTypes = {
  'post_liked',
  'post_commented',
  'query_answered',
  'story_mention',
};

class NotificationTile extends StatelessWidget {
  final NotificationModel notification;
  final String timeLabel;

  /// Follow-request quick action (Confirm/Delete, Follow back) — built by
  /// the screen because it needs the screen's in-flight state.
  final Widget? trailingAction;
  final VoidCallback? onTap;
  final VoidCallback? onLongPress;

  const NotificationTile({
    super.key,
    required this.notification,
    required this.timeLabel,
    this.trailingAction,
    this.onTap,
    this.onLongPress,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final n = notification;
    final thumb = _buildThumb(n, scheme);
    final timeText = Text(
      timeLabel,
      style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant),
    );

    return ListTile(
      tileColor: n.isRead ? null : scheme.primary.withOpacity(0.06),
      leading: _AvatarStack(actors: _effectiveActors(n)),
      title: Text(
        n.title,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
          fontWeight: n.isRead ? FontWeight.normal : FontWeight.w600,
          color: scheme.onSurface,
        ),
      ),
      subtitle: Text(
        n.message,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(color: scheme.onSurfaceVariant),
      ),
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (thumb != null) ...[thumb, const SizedBox(width: 10)],
          trailingAction == null
              ? timeText
              : Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [timeText, const SizedBox(height: 6), trailingAction!],
                ),
        ],
      ),
      onTap: onTap,
      onLongPress: onLongPress,
    );
  }

  /// Rows created before N3-BE have no `actors`, but single-actor rows
  /// already carry data.actor_id / actor_username (core.create_notification,
  /// TASK 6). Build a photo-less actor from that so the avatar at least
  /// shows an initial; otherwise falls through to the generic placeholder.
  static List<NotificationActor> _effectiveActors(NotificationModel n) {
    if (n.actors.isNotEmpty) return n.actors;
    final id = n.data?['actor_id'];
    final name = n.data?['actor_username'];
    final actorId = id is int ? id : int.tryParse('$id');
    if (actorId != null && name is String && name.isNotEmpty) {
      return [NotificationActor(id: actorId, username: name)];
    }
    return const [];
  }

  static Widget? _buildThumb(NotificationModel n, ColorScheme scheme) {
    if (!_kPostPreviewTypes.contains(n.notifType)) return null;
    final mediaType = n.data?['media_type']?.toString();
    if (mediaType == null || mediaType.isEmpty) return null; // text-only post
    final url = n.thumbnailUrl;
    final isVideo = mediaType == 'video';

    return ClipRRect(
      borderRadius: BorderRadius.circular(kLsRadius),
      child: SizedBox(
        width: _kNotifThumbSize,
        height: _kNotifThumbSize,
        child: (url != null && url.isNotEmpty)
            ? Stack(fit: StackFit.expand, children: [
                // Video: only its thumbnail/first-frame image is loaded
                // in the list, never the video itself.
                CachedNetworkImage(
                  imageUrl: url,
                  fit: BoxFit.cover,
                  memCacheWidth: (_kNotifThumbSize * 3).round(),
                  errorWidget: (_, __, ___) => Container(
                    color: scheme.surfaceVariant,
                    child: Icon(Icons.broken_image_rounded,
                        size: 18, color: scheme.onSurfaceVariant),
                  ),
                ),
                if (isVideo)
                  Container(
                    color: Colors.black26,
                    child: const Icon(Icons.play_arrow_rounded,
                        color: Colors.white, size: 20),
                  ),
              ])
            : Container(
                color: scheme.surfaceVariant,
                child: Icon(
                  isVideo ? Icons.videocam_rounded : Icons.insert_drive_file_rounded,
                  size: 18,
                  color: scheme.onSurfaceVariant,
                ),
              ),
      ),
    );
  }
}

/// 0 actors -> generic placeholder, 1 -> single avatar,
/// 2-3 -> overlapping avatars (first/most-recent-named actor on top).
class _AvatarStack extends StatelessWidget {
  final List<NotificationActor> actors;

  const _AvatarStack({required this.actors});

  @override
  Widget build(BuildContext context) {
    if (actors.length <= 1) {
      return _Avatar(
        size: _kAvatarSize,
        actor: actors.isEmpty ? null : actors.first,
      );
    }

    final shown = actors.take(3).toList();
    final width = _kStackedAvatarSize + (shown.length - 1) * _kStackOverlap;
    final ringColor = Theme.of(context).scaffoldBackgroundColor;

    return SizedBox(
      width: width,
      height: _kStackedAvatarSize,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          // Paint last first so the first actor ends up on top.
          for (int i = shown.length - 1; i >= 0; i--)
            Positioned(
              left: i * _kStackOverlap,
              child: Container(
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(color: ringColor, width: 2),
                ),
                child: _Avatar(size: _kStackedAvatarSize - 4, actor: shown[i]),
              ),
            ),
        ],
      ),
    );
  }
}

class _Avatar extends StatelessWidget {
  final double size;
  final NotificationActor? actor;

  const _Avatar({required this.size, required this.actor});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final photo = actor?.photoUrl;
    final name = actor?.username ?? '';

    Widget fallback() => Center(
          child: name.isNotEmpty
              ? Text(
                  name.characters.first.toUpperCase(),
                  style: TextStyle(
                    fontSize: size * 0.42,
                    fontWeight: FontWeight.w600,
                    color: scheme.onPrimaryContainer,
                  ),
                )
              : Icon(Icons.notifications_none_rounded,
                  size: size * 0.55, color: scheme.onPrimaryContainer),
        );

    return ClipOval(
      child: Container(
        width: size,
        height: size,
        color: scheme.primaryContainer,
        child: (photo != null && photo.isNotEmpty)
            ? CachedNetworkImage(
                imageUrl: photo,
                fit: BoxFit.cover,
                memCacheWidth: (size * 3).round(),
                errorWidget: (_, __, ___) => fallback(),
                placeholder: (_, __) => fallback(),
              )
            : fallback(),
      ),
    );
  }
}
