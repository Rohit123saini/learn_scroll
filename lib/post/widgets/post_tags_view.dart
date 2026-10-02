import 'package:flutter/material.dart';

// ============================================================
// P5b-FE — show a post's tagged people (backend `tagged_users`, P5a-BE).
//
//   tagged_users: [{ user: {id, username, profile_picture}, x, y, is_hidden? }]
//
// Usage in singlepost.dart (wrap the FIRST image of the post):
//
//   final tags = PostTagView.listFrom(post['tagged_users']);   // or your model's raw json
//   PostTagsOverlay(
//     tags: tags,
//     onOpenUser: (t) => Navigator.push(... profile of t.username ...),
//     child: yourFirstImageWidget,
//   )
//
// For posts without a photo (video / text / document) just show
//   if (tags.isNotEmpty) PostTagsLine(tags: tags, onOpenUser: ...)
// ============================================================

class PostTagView {
  final String userId;
  final String username;
  final String? profilePicture;
  final double? x;
  final double? y;

  const PostTagView({
    required this.userId,
    required this.username,
    this.profilePicture,
    this.x,
    this.y,
  });

  bool get hasPosition => x != null && y != null;

  static List<PostTagView> listFrom(dynamic raw) {
    if (raw is! List) return const [];
    final out = <PostTagView>[];
    for (final e in raw) {
      if (e is! Map) continue;
      final u = e['user'];
      if (u is! Map) continue;
      final name = (u['username'] ?? '').toString();
      if (name.isEmpty) continue;
      out.add(PostTagView(
        userId: (u['id'] ?? '').toString(),
        username: name,
        profilePicture: u['profile_picture']?.toString(),
        x: (e['x'] as num?)?.toDouble(),
        y: (e['y'] as num?)?.toDouble(),
      ));
    }
    return out;
  }
}

/// Wraps an image: a small "tag" badge bottom-left; tap it (or the image) to reveal
/// name chips at their x/y. People tagged without a position are listed in a sheet
/// opened from the same badge.
class PostTagsOverlay extends StatefulWidget {
  final Widget child;
  final List<PostTagView> tags;
  final ValueChanged<PostTagView>? onOpenUser;

  const PostTagsOverlay({super.key, required this.child, required this.tags, this.onOpenUser});

  @override
  State<PostTagsOverlay> createState() => _PostTagsOverlayState();
}

class _PostTagsOverlayState extends State<PostTagsOverlay> {
  bool _show = false;

  @override
  Widget build(BuildContext context) {
    final tags = widget.tags;
    if (tags.isEmpty) return widget.child;
    final positioned = tags.where((t) => t.hasPosition).toList();

    return Stack(
      children: [
        widget.child,
        if (_show && positioned.isNotEmpty)
          Positioned.fill(
            child: LayoutBuilder(builder: (context, c) {
              return Stack(
                clipBehavior: Clip.none,
                children: [
                  for (final t in positioned)
                    Positioned(
                      // clamp so a chip near an edge doesn't get cut off the screen
                      left: (t.x! * c.maxWidth).clamp(40.0, (c.maxWidth - 40).clamp(40.0, double.infinity)),
                      top: t.y! * c.maxHeight,
                      child: FractionalTranslation(
                        translation: const Offset(-0.5, 0),
                        child: GestureDetector(
                          onTap: widget.onOpenUser == null ? null : () => widget.onOpenUser!(t),
                          child: _chip(t.username),
                        ),
                      ),
                    ),
                ],
              );
            }),
          ),
        Positioned(
          left: 10,
          bottom: 10,
          child: Semantics(
            button: true,
            label: 'Tagged people',
            child: GestureDetector(
              onTap: () {
                // Positioned tags toggle on the photo; list-only tags open the sheet.
                if (positioned.isNotEmpty && positioned.length == tags.length) {
                  setState(() => _show = !_show);
                } else {
                  if (positioned.isNotEmpty) setState(() => _show = true);
                  showPostTagsSheet(context, tags, onOpenUser: widget.onOpenUser);
                }
              },
              child: Container(
                padding: const EdgeInsets.all(7),
                decoration: const BoxDecoration(color: Colors.black54, shape: BoxShape.circle),
                child: const Icon(Icons.person_pin_rounded, size: 16, color: Colors.white),
              ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _chip(String name) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
        decoration: BoxDecoration(color: Colors.black.withOpacity(.78), borderRadius: BorderRadius.circular(8)),
        child: Text(name, style: const TextStyle(color: Colors.white, fontSize: 11.5, fontWeight: FontWeight.w700)),
      );
}

/// "with @a, @b and 2 others" line for posts without a photo. Tap opens the list.
class PostTagsLine extends StatelessWidget {
  final List<PostTagView> tags;
  final ValueChanged<PostTagView>? onOpenUser;
  const PostTagsLine({super.key, required this.tags, this.onOpenUser});

  @override
  Widget build(BuildContext context) {
    if (tags.isEmpty) return const SizedBox.shrink();
    final cs = Theme.of(context).colorScheme;
    final first = tags.take(2).map((t) => '@${t.username}').join(', ');
    final rest = tags.length - 2;
    return InkWell(
      onTap: () => showPostTagsSheet(context, tags, onOpenUser: onOpenUser),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(
          children: [
            Icon(Icons.person_pin_outlined, size: 16, color: cs.onSurfaceVariant),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                rest > 0 ? 'with $first and $rest other${rest == 1 ? '' : 's'}' : 'with $first',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

Future<void> showPostTagsSheet(
  BuildContext context,
  List<PostTagView> tags, {
  ValueChanged<PostTagView>? onOpenUser,
}) {
  return showModalBottomSheet<void>(
    context: context,
    useSafeArea: true,
    builder: (ctx) => SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text('In this post', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800)),
            ),
          ),
          Flexible(
            child: ListView.builder(
              shrinkWrap: true,
              itemCount: tags.length,
              itemBuilder: (_, i) {
                final t = tags[i];
                return ListTile(
                  leading: CircleAvatar(
                    backgroundImage: (t.profilePicture != null && t.profilePicture!.isNotEmpty)
                        ? NetworkImage(t.profilePicture!)
                        : null,
                    child: (t.profilePicture == null || t.profilePicture!.isEmpty)
                        ? Text(t.username[0].toUpperCase())
                        : null,
                  ),
                  title: Text(t.username, style: const TextStyle(fontWeight: FontWeight.w600)),
                  onTap: onOpenUser == null
                      ? null
                      : () {
                          Navigator.pop(ctx);
                          onOpenUser(t);
                        },
                );
              },
            ),
          ),
        ],
      ),
    ),
  );
}
