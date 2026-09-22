// lib/post/screens/singlepost.dart
//
// UI/UX PASS — rebuilt to match home.dart's LearnScroll design language
// (theme_service.dart's ColorScheme, LsType headers, full light/dark
// support) and fully localized via AppLocalizations. Every method below
// is functionally identical to before: post loading, the real reaction
// system (tap = like/unlike, long-press = 5-reaction picker, optimistic
// update + rollback), the shared CommentBottomSheet, the media carousel
// (image/video/pdf/doc) and every full-screen viewer still work exactly
// as they did — only colors, spacing, typography and copy changed.
//
// models.dart lives at lib/post/models/models.dart, alongside
// services/api_service.dart which already imports it the same way.

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:video_player/video_player.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:share_plus/share_plus.dart';
import 'package:timeago/timeago.dart' as timeago;
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';

import '../../utils/api.dart';
import '../services/api_service.dart';
import '../../search/api_service.dart' as SearchApi;
import '../../profile/screens/target_profile.dart';
import '../../profile/screens/profile.dart';
import '../../profile/api_service.dart' as ProfileApi;
import '../../services/auth_service.dart';
import '../widgets/comment_sheet.dart';
import '../../widgets/ls_ui.dart';
import '../../l10n/app_localizations.dart';
import '../models/models.dart';
import '../services/post_extras_service.dart';
import '../../services/home_api_model_service.dart' show HomeFeedService;

const Map<String, String> kReactionEmoji = {'like': '👍', 'confuse': '🤔', 'wrong': '❗', 'imp': '⭐', 'explain': '💡'};
const Map<String, Color> kReactionColor = {
  'like': Color(0xFF1877F2),
  'confuse': Color(0xFFF7B928),
  'wrong': Color(0xFFE0245E),
  'imp': Color(0xFFFFAD33),
  'explain': Color(0xFF45BD62),
};

Future<void> downloadWithAuth(String url, String fileName, BuildContext context) async {
  final l10n = AppLocalizations.of(context)!;
  try {
    final token = await AuthService.getToken();
    final dio = Dio();
    final dir = await getApplicationDocumentsDirectory();
    final path = "${dir.path}/$fileName";
    await dio.download(url, path, options: Options(headers: {if (token != null) 'Authorization': 'Bearer $token'}));
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.downloadedFile(fileName)), backgroundColor: const Color(0xFF16A34A)));
      await OpenFilex.open(path);
    }
  } catch (e) {
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.downloadFailed(e.toString())), backgroundColor: Theme.of(context).colorScheme.error));
    }
  }
}

class SinglePostPage extends StatefulWidget {
  final String postId;
  const SinglePostPage({super.key, required this.postId});
  @override
  State<SinglePostPage> createState() => _SinglePostPageState();
}

class _SinglePostPageState extends State<SinglePostPage> {
  SinglePostModel? post;
  bool isLoading = true;
  String? error;
  String? myReaction;
  Map<String, int> reactionCounts = {'like': 0, 'confuse': 0, 'wrong': 0, 'imp': 0, 'explain': 0, 'total': 0};
  int commentsCount = 0;
  int currentIndex = 0;
  String fullImageUrl = "";
  String? myUsername;
  PageController pageController = PageController();

  @override
  void initState() {
    super.initState();
    _initAll();
  }

  Future<void> _initAll() async {
    await _loadMyUsername();
    await _loadPost();
  }

  Future<void> _loadMyUsername() async {
    try {
      final d = await ProfileApi.ApiService.getProfile();
      myUsername = d.username;
    } catch (_) {
      try {
        final t = await AuthService.getToken();
        if (t != null) {
          String p = base64.normalize(t.split('.')[1]);
          myUsername = jsonDecode(utf8.decode(base64Url.decode(p)))['username']?.toString();
        }
      } catch (_) {}
    }
  }

  Future<void> _loadPost() async {
    try {
      final data = await ApiService().getPostById(widget.postId);
      if (mounted) {
        final model = SinglePostModel.fromJson(data);
        setState(() {
          post = model;
          myReaction = model.myReaction;
          reactionCounts = model.reactionCounts;
          commentsCount = model.commentsCount;
          isLoading = false;
        });
        if (model.username.isNotEmpty) _fetchPhotoFromSearchApi(model.username);
      }
    } catch (e) {
      if (mounted) setState(() { error = e.toString(); isLoading = false; });
    }
  }

  Future<void> _handleReaction(String reaction) async {
    HapticFeedback.lightImpact();
    final oldReaction = myReaction;
    final oldCounts = Map<String, int>.from(reactionCounts);
    setState(() {
      if (oldReaction == reaction) {
        myReaction = null;
        reactionCounts['total'] = (reactionCounts['total'] ?? 0) - 1;
      } else {
        if (oldReaction == null) reactionCounts['total'] = (reactionCounts['total'] ?? 0) + 1;
        myReaction = reaction;
      }
    });
    try {
      final res = await ApiService().toggleReaction(widget.postId, reaction);
      if (!mounted) return;
      final c = (res['counts'] as Map?) ?? {};
      setState(() {
        reactionCounts = {
          'like': (c['like'] as int?) ?? 0,
          'confuse': (c['confuse'] as int?) ?? 0,
          'wrong': (c['wrong'] as int?) ?? 0,
          'imp': (c['imp'] as int?) ?? 0,
          'explain': (c['explain'] as int?) ?? 0,
          'total': (c['total'] as int?) ?? 0,
        };
        myReaction = res['my_reaction'];
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        myReaction = oldReaction;
        reactionCounts = oldCounts;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(AppLocalizations.of(context)!.reactionUpdateFailed), backgroundColor: Theme.of(context).colorScheme.error),
      );
    }
  }

  Future<void> _fetchPhotoFromSearchApi(String username) async {
    try {
      final r = await SearchApi.SearchApiService.searchUsers(username);
      if (r.isNotEmpty) {
        dynamic m = r.firstWhere((u) => u['username'] == username, orElse: () => r[0]);
        final p = m['profile_photo'];
        String url = "";
        if (p != null && p.isNotEmpty) url = p.startsWith('http') ? p : "${Api.baseUrl}$p";
        if (mounted) setState(() => fullImageUrl = url);
      }
    } catch (_) {}
  }

  String buildMediaUrl(String? path) {
    if (path == null || path.isEmpty) return "";
    if (path.startsWith('http')) {
      if (path.contains("/media/")) return "${Api.baseUrl}${path.substring(path.indexOf("/media/"))}";
      return path;
    }
    return "${Api.baseUrl}$path";
  }

  Future<void> _confirmDeletePost() async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.deletePostCta),
        content: Text(l10n.deletePostConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.deletePostCta)),
        ],
      ),
    );
    if (ok != true || post == null) return;
    try {
      final done = await PostExtrasService.deletePost(post!.id);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(done ? l10n.postDeleted : l10n.postDeleteFailed)));
      if (done) {
        await HomeFeedService.clearFeedCache();
        if (mounted) Navigator.of(context).pop(true);
      }
    } catch (_) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.postDeleteFailed)));
    }
  }

  Future<void> _goToProfile(String postUsername) async {
    if (postUsername.trim().isEmpty) return;
    if (myUsername == null) await _loadMyUsername();
    bool isMe = myUsername != null && myUsername!.toLowerCase().trim() == postUsername.toLowerCase().trim();
    if (!mounted) return;
    if (isMe) {
      Navigator.push(context, MaterialPageRoute(builder: (_) => const ProfileScreen()));
    } else {
      Navigator.push(context, MaterialPageRoute(builder: (_) => TargetProfilePage(username: postUsername)));
    }
  }

  void _openCommentSheet() {
    if (post == null) return;
    final cs = Theme.of(context).colorScheme;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
        child: Container(
          decoration: BoxDecoration(color: cs.surface, borderRadius: const BorderRadius.vertical(top: Radius.circular(24))),
          child: CommentBottomSheet(
            postId: widget.postId,
            postOwnerId: post!.userId,
            initialCommentsCount: commentsCount,
            onCommentAdded: () => setState(() => commentsCount++),
            onGoToProfile: _goToProfile,
          ),
        ),
      ),
    );
  }

  void _openFullScreen() {
    final media = post?.media ?? [];
    if (media.isEmpty) return;
    final m = media[currentIndex];
    final fileUrl = buildMediaUrl(m.file);
    final ext = fileUrl.split('.').last.toLowerCase().split('?').first;
    final type = m.mediaType.toLowerCase();
    if (type == 'video' || ['mp4', 'mov', 'mkv'].contains(ext)) {
      Navigator.push(context, MaterialPageRoute(builder: (_) => FullScreenVideoPage(url: fileUrl)));
    } else if (['jpg', 'jpeg', 'png', 'webp', 'gif'].contains(ext) || type == 'image') {
      Navigator.push(context, MaterialPageRoute(builder: (_) => FullScreenImagePage(url: fileUrl)));
    } else {
      Navigator.push(context, MaterialPageRoute(builder: (_) => DocumentViewerPage(url: fileUrl, fileName: fileUrl.split('/').last.split('?').first)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    if (isLoading) return Scaffold(backgroundColor: cs.background, body: Center(child: CircularProgressIndicator(color: cs.primary)));
    if (error != null) {
      return Scaffold(
        backgroundColor: cs.background,
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Icon(Icons.error_outline_rounded, color: cs.error, size: 40),
              const SizedBox(height: 12),
              Text(error!, textAlign: TextAlign.center, style: TextStyle(color: cs.onSurfaceVariant)),
            ]),
          ),
        ),
      );
    }

    final media = post!.media;
    final createdAt = post!.createdAt;
    final username = post!.username;

    return Scaffold(
      backgroundColor: cs.background,
      appBar: AppBar(
        backgroundColor: cs.surface,
        elevation: 0,
        scrolledUnderElevation: 1,
        surfaceTintColor: cs.surface,
        iconTheme: IconThemeData(color: cs.onSurface),
        title: Text(username, style: LsType.head(context, size: 16)),
        actions: [
          // Owner-only: backend `DELETE /post/<id>/delete/` 403s for anyone else.
          FutureBuilder<String?>(
            future: AuthService.getUserId(),
            builder: (context, snap) {
              if (snap.data == null || snap.data != post!.userId) return const SizedBox.shrink();
              return PopupMenuButton<String>(
                onSelected: (v) {
                  if (v == 'delete') _confirmDeletePost();
                },
                itemBuilder: (_) => [
                  PopupMenuItem(value: 'delete', child: Text(AppLocalizations.of(context)!.deletePostCta)),
                ],
              );
            },
          ),
        ],
      ),
      body: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          InkWell(
            onTap: () => _goToProfile(username),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
              child: Row(children: [
                CircleAvatar(
                  radius: 22,
                  backgroundColor: cs.surfaceVariant,
                  child: ClipOval(
                    child: fullImageUrl.isEmpty
                        ? Icon(Icons.person_rounded, color: cs.onSurfaceVariant)
                        : CachedNetworkImage(imageUrl: fullImageUrl, width: 44, height: 44, fit: BoxFit.cover),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(username, style: TextStyle(fontWeight: FontWeight.w800, fontSize: 14.5, color: cs.onSurface)),
                    if (createdAt != null)
                      Text(timeago.format(createdAt, locale: Localizations.localeOf(context).languageCode), style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
                  ]),
                ),
              ]),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(18),
              child: Container(
                height: 580,
                color: Colors.black,
                child: Stack(children: [
                  PageView.builder(
                    controller: pageController,
                    itemCount: media.length,
                    onPageChanged: (i) => setState(() => currentIndex = i),
                    itemBuilder: (c, i) {
                      final fileUrl = buildMediaUrl(media[i].file);
                      final ext = fileUrl.split('.').last.toLowerCase().split('?').first;
                      final type = media[i].mediaType.toLowerCase();
                      if (type == 'video' || ['mp4', 'mov', 'mkv'].contains(ext)) return SmallVideoPlayer(url: fileUrl);
                      if (ext == 'pdf' || type == 'pdf') return SmallPdfViewer(url: fileUrl);
                      if (['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt'].contains(ext)) {
                        return DocThumbnail(url: fileUrl, ext: ext, onOpen: _openFullScreen);
                      }
                      return CachedNetworkImage(imageUrl: fileUrl, fit: BoxFit.cover, width: double.infinity);
                    },
                  ),
                  if (media.length > 1)
                    Positioned(
                      top: 12,
                      left: 0,
                      right: 0,
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: List.generate(media.length, (i) {
                          final active = i == currentIndex;
                          return AnimatedContainer(
                            duration: const Duration(milliseconds: 200),
                            margin: const EdgeInsets.symmetric(horizontal: 2.5),
                            width: active ? 16 : 6,
                            height: 6,
                            decoration: BoxDecoration(color: active ? Colors.white : Colors.white38, borderRadius: BorderRadius.circular(4)),
                          );
                        }),
                      ),
                    ),
                  Positioned(
                    top: 10,
                    right: 10,
                    child: InkWell(
                      onTap: _openFullScreen,
                      borderRadius: BorderRadius.circular(20),
                      child: Container(
                        padding: const EdgeInsets.all(7),
                        decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(20)),
                        child: const Icon(Icons.fullscreen_rounded, color: Colors.white, size: 20),
                      ),
                    ),
                  ),
                ]),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            child: Row(children: [
              _ReactionTapTarget(myReaction: myReaction, onReaction: _handleReaction),
              Text('${reactionCounts['total'] ?? 0}', style: TextStyle(fontWeight: FontWeight.w700, color: cs.onSurface, fontSize: 13)),
              const SizedBox(width: 16),
              InkWell(
                onTap: _openCommentSheet,
                borderRadius: BorderRadius.circular(20),
                child: Padding(
                  padding: const EdgeInsets.all(8),
                  child: Icon(Icons.chat_bubble_outline_rounded, color: cs.onSurfaceVariant, size: 22),
                ),
              ),
              Text('$commentsCount', style: TextStyle(fontWeight: FontWeight.w700, color: cs.onSurface, fontSize: 13)),
              const Spacer(),
              InkWell(
                onTap: () => Share.share("${post!.title ?? ''}\n${post!.caption}"),
                borderRadius: BorderRadius.circular(20),
                child: Padding(
                  padding: const EdgeInsets.all(8),
                  child: Icon(Icons.share_outlined, color: cs.onSurfaceVariant, size: 21),
                ),
              ),
            ]),
          ),
          if ((post!.title ?? '').isNotEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Text(post!.title ?? '', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 15.5, color: cs.onSurface)),
            ),
          if ((post!.caption).isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 6, 16, 0),
              child: Text(post!.caption, style: TextStyle(fontSize: 14, height: 1.4, color: cs.onSurfaceVariant)),
            ),
          if ((post!.categoryLabel ?? post!.category).isNotEmpty || (post!.subcategoryLabel ?? post!.subcategory ?? '').isNotEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              child: Wrap(spacing: 8, runSpacing: 8, children: [
                if ((post!.categoryLabel ?? post!.category).isNotEmpty) _TagChip(label: post!.categoryLabel ?? post!.category, cs: cs, emphasized: true),
                if ((post!.subcategoryLabel ?? post!.subcategory ?? '').isNotEmpty) _TagChip(label: post!.subcategoryLabel ?? post!.subcategory ?? '', cs: cs),
              ]),
            ),
          const SizedBox(height: 20),
        ]),
      ),
    );
  }
}

class _TagChip extends StatelessWidget {
  final String label;
  final ColorScheme cs;
  final bool emphasized;
  const _TagChip({required this.label, required this.cs, this.emphasized = false});
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: emphasized ? cs.primary.withOpacity(0.12) : cs.surfaceVariant,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(label, style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: emphasized ? cs.primary : cs.onSurfaceVariant)),
    );
  }
}

// Real reaction tap target: tap = quick like/unlike, long-press = full
// 5-reaction picker (like/confuse/wrong/imp/explain), same emoji set +
// colors as home.dart's feed reaction button for visual consistency.
class _ReactionTapTarget extends StatefulWidget {
  final String? myReaction;
  final void Function(String reaction) onReaction;
  const _ReactionTapTarget({required this.myReaction, required this.onReaction});
  @override
  State<_ReactionTapTarget> createState() => _ReactionTapTargetState();
}

class _ReactionTapTargetState extends State<_ReactionTapTarget> {
  OverlayEntry? _overlayEntry;

  void _showPicker(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final RenderBox box = context.findRenderObject() as RenderBox;
    final Offset pos = box.localToGlobal(Offset.zero);
    _overlayEntry = OverlayEntry(builder: (c) => Stack(children: [
      GestureDetector(onTap: _hidePicker, child: Container(color: Colors.transparent, width: double.infinity, height: double.infinity)),
      Positioned(
        left: pos.dx,
        top: pos.dy - 62,
        child: Material(
          color: Colors.transparent,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
            decoration: BoxDecoration(color: cs.surface, borderRadius: BorderRadius.circular(30), boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.2), blurRadius: 12)]),
            child: Row(
              children: kReactionEmoji.entries.map((e) {
                final sel = widget.myReaction == e.key;
                return GestureDetector(
                  onTap: () { _hidePicker(); widget.onReaction(e.key); },
                  child: Container(
                    margin: const EdgeInsets.symmetric(horizontal: 4),
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: sel ? kReactionColor[e.key]!.withOpacity(0.18) : cs.surfaceVariant,
                      shape: BoxShape.circle,
                      border: sel ? Border.all(color: kReactionColor[e.key]!, width: 2) : null,
                    ),
                    child: Text(e.value, style: const TextStyle(fontSize: 26)),
                  ),
                );
              }).toList(),
            ),
          ),
        ),
      ),
    ]));
    Overlay.of(context).insert(_overlayEntry!);
  }

  void _hidePicker() {
    _overlayEntry?.remove();
    _overlayEntry = null;
  }

  @override
  void dispose() {
    _overlayEntry?.remove();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Builder(builder: (btnCtx) {
      return InkWell(
        onTap: () => widget.onReaction('like'),
        onLongPress: () => _showPicker(btnCtx),
        borderRadius: BorderRadius.circular(20),
        child: Padding(
          padding: const EdgeInsets.all(8),
          child: widget.myReaction == null
              ? Icon(Icons.favorite_border_rounded, color: cs.onSurfaceVariant)
              : Text(kReactionEmoji[widget.myReaction] ?? '👍', style: const TextStyle(fontSize: 20)),
        ),
      );
    });
  }
}

// Small in-carousel player — autoplays, keeps playing across scroll.
class SmallVideoPlayer extends StatefulWidget {
  final String url;
  const SmallVideoPlayer({super.key, required this.url});
  @override
  State<SmallVideoPlayer> createState() => _SmallVideoPlayerState();
}

class _SmallVideoPlayerState extends State<SmallVideoPlayer> {
  late VideoPlayerController _c;
  bool _ok = false;
  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    final token = await AuthService.getToken();
    final headers = token != null ? {'Authorization': 'Bearer $token'} : <String, String>{};
    _c = VideoPlayerController.networkUrl(Uri.parse(widget.url), httpHeaders: headers);
    await _c.initialize();
    await _c.setLooping(true);
    await _c.play();
    if (mounted) setState(() => _ok = true);
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_ok) return const Center(child: CircularProgressIndicator(color: Colors.white));
    return GestureDetector(
      onTap: () { _c.value.isPlaying ? _c.pause() : _c.play(); setState(() {}); },
      child: Stack(alignment: Alignment.center, children: [
        AspectRatio(aspectRatio: _c.value.aspectRatio, child: VideoPlayer(_c)),
        if (!_c.value.isPlaying) const Icon(Icons.play_circle_fill_rounded, color: Colors.white70, size: 60),
      ]),
    );
  }
}

// PDF preview inside the carousel.
class SmallPdfViewer extends StatefulWidget {
  final String url;
  const SmallPdfViewer({super.key, required this.url});
  @override
  State<SmallPdfViewer> createState() => _SmallPdfViewerState();
}

class _SmallPdfViewerState extends State<SmallPdfViewer> {
  String? token;
  bool loading = true;
  @override
  void initState() {
    super.initState();
    _loadToken();
  }

  Future<void> _loadToken() async {
    final t = await AuthService.getToken();
    if (mounted) setState(() { token = t; loading = false; });
  }

  @override
  Widget build(BuildContext context) {
    if (loading) return const Center(child: CircularProgressIndicator(color: Colors.white));
    return SfPdfViewer.network(widget.url, headers: token != null ? {'Authorization': 'Bearer $token'} : null, canShowScrollHead: false, canShowPaginationDialog: false);
  }
}

// Doc/spreadsheet/slide thumbnail — Open + Download in the carousel.
class DocThumbnail extends StatelessWidget {
  final String url;
  final String ext;
  final VoidCallback onOpen;
  const DocThumbnail({super.key, required this.url, required this.ext, required this.onOpen});
  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final fileName = url.split('/').last.split('?').first;
    return Container(
      color: const Color(0xFF161320),
      child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
        Icon(ext == 'pdf' ? Icons.picture_as_pdf_rounded : Icons.description_rounded, color: const Color(0xFF8B7CFF), size: 64),
        const SizedBox(height: 10),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: Text(fileName, style: const TextStyle(color: Colors.white70, fontSize: 12), overflow: TextOverflow.ellipsis, maxLines: 1, textAlign: TextAlign.center),
        ),
        const SizedBox(height: 16),
        Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          ElevatedButton(
            onPressed: onOpen,
            style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF8B7CFF), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20))),
            child: Text(l10n.open),
          ),
          const SizedBox(width: 10),
          OutlinedButton.icon(
            onPressed: () => downloadWithAuth(url, fileName, context),
            icon: const Icon(Icons.download_rounded, size: 16, color: Colors.white),
            label: Text(l10n.download, style: const TextStyle(color: Colors.white)),
            style: OutlinedButton.styleFrom(side: const BorderSide(color: Colors.white38), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20))),
          ),
        ]),
      ]),
    );
  }
}

// Immersive full-screen video with tap-to-toggle controls.
class FullScreenVideoPage extends StatefulWidget {
  final String url;
  const FullScreenVideoPage({super.key, required this.url});
  @override
  State<FullScreenVideoPage> createState() => _FullScreenVideoPageState();
}

class _FullScreenVideoPageState extends State<FullScreenVideoPage> {
  late VideoPlayerController _controller;
  bool _show = true;
  Timer? _t;
  String _fmt(Duration d) {
    String t(int n) => n.toString().padLeft(2, '0');
    return "${t(d.inMinutes.remainder(60))}:${t(d.inSeconds.remainder(60))}";
  }

  @override
  void initState() {
    super.initState();
    SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp, DeviceOrientation.landscapeLeft, DeviceOrientation.landscapeRight]);
    _controller = VideoPlayerController.networkUrl(Uri.parse(widget.url))
      ..initialize().then((_) {
        if (mounted) {
          setState(() {});
          _controller.play();
          _hide();
        }
      });
    _controller.addListener(() { if (mounted) setState(() {}); });
  }

  void _hide() {
    _t?.cancel();
    _t = Timer(const Duration(seconds: 3), () { if (mounted && _controller.value.isPlaying) setState(() => _show = false); });
  }

  void _tap() {
    setState(() => _show = !_show);
    if (_show) _hide();
  }

  @override
  void dispose() {
    _t?.cancel();
    SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]);
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: GestureDetector(
        onTap: _tap,
        child: Stack(children: [
          Center(
            child: _controller.value.isInitialized
                ? AspectRatio(aspectRatio: _controller.value.aspectRatio, child: VideoPlayer(_controller))
                : const CircularProgressIndicator(color: Colors.white),
          ),
          if (_show)
            Container(
              color: Colors.black38,
              child: Stack(children: [
                Positioned(top: 40, left: 10, child: IconButton(icon: const Icon(Icons.close_rounded, color: Colors.white, size: 28), onPressed: () => Navigator.pop(context))),
                Center(
                  child: IconButton(
                    icon: Icon(_controller.value.isPlaying ? Icons.pause_circle_rounded : Icons.play_circle_rounded, color: Colors.white, size: 72),
                    onPressed: () { setState(() { _controller.value.isPlaying ? _controller.pause() : _controller.play(); }); _hide(); },
                  ),
                ),
                Positioned(
                  bottom: 20,
                  left: 12,
                  right: 12,
                  child: Column(children: [
                    VideoProgressIndicator(_controller, allowScrubbing: true, padding: const EdgeInsets.symmetric(vertical: 6), colors: const VideoProgressColors(playedColor: Color(0xFF8B7CFF))),
                    Row(children: [
                      Text(_fmt(_controller.value.position), style: const TextStyle(color: Colors.white, fontSize: 12)),
                      Expanded(
                        child: Slider(
                          min: 0,
                          max: _controller.value.duration.inMilliseconds.toDouble().clamp(1, double.infinity),
                          value: _controller.value.position.inMilliseconds.toDouble().clamp(0, _controller.value.duration.inMilliseconds.toDouble().clamp(1, double.infinity)),
                          activeColor: const Color(0xFF8B7CFF),
                          inactiveColor: Colors.white24,
                          onChanged: (v) => _controller.seekTo(Duration(milliseconds: v.toInt())),
                        ),
                      ),
                      Text(_fmt(_controller.value.duration), style: const TextStyle(color: Colors.white, fontSize: 12)),
                    ]),
                  ]),
                ),
              ]),
            ),
        ]),
      ),
    );
  }
}

// Full-screen zoomable image.
class FullScreenImagePage extends StatelessWidget {
  final String url;
  const FullScreenImagePage({super.key, required this.url});
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(children: [
        SizedBox.expand(child: InteractiveViewer(minScale: 0.9, maxScale: 5.0, child: CachedNetworkImage(imageUrl: url, fit: BoxFit.cover, width: double.infinity, height: double.infinity))),
        Positioned(top: 40, left: 10, child: IconButton(icon: const Icon(Icons.close_rounded, color: Colors.white, size: 28), onPressed: () => Navigator.pop(context))),
      ]),
    );
  }
}

// Document viewer — PDF inline, other types get a direct-download card.
class DocumentViewerPage extends StatefulWidget {
  final String url;
  final String fileName;
  const DocumentViewerPage({super.key, required this.url, required this.fileName});
  @override
  State<DocumentViewerPage> createState() => _DocumentViewerPageState();
}

class _DocumentViewerPageState extends State<DocumentViewerPage> {
  bool isDownloading = false;
  bool isPreparing = true;
  String? localPath;
  bool get isPdf => widget.fileName.toLowerCase().endsWith('.pdf');
  @override
  void initState() {
    super.initState();
    _prepare();
  }

  Future<void> _prepare() async {
    try {
      final token = await AuthService.getToken();
      final dir = await getTemporaryDirectory();
      final path = "${dir.path}/${widget.fileName}";
      await Dio().download(widget.url, path, options: Options(headers: {if (token != null) 'Authorization': 'Bearer $token'}));
      if (mounted) setState(() { localPath = path; isPreparing = false; });
    } catch (e) {
      if (mounted) setState(() => isPreparing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: const Color(0xFF0F0D18),
      appBar: AppBar(
        backgroundColor: const Color(0xFF161320),
        iconTheme: const IconThemeData(color: Colors.white),
        title: Text(widget.fileName, style: const TextStyle(color: Colors.white, fontSize: 13), overflow: TextOverflow.ellipsis),
        actions: [
          isDownloading
              ? const Padding(padding: EdgeInsets.all(14), child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white)))
              : IconButton(icon: const Icon(Icons.download_rounded, color: Colors.white), onPressed: () => downloadWithAuth(widget.url, widget.fileName, context)),
        ],
      ),
      body: isPreparing
          ? Center(
              child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                const CircularProgressIndicator(color: Color(0xFF8B7CFF)),
                const SizedBox(height: 10),
                Text(l10n.loadingDocument, style: const TextStyle(color: Colors.white70)),
              ]),
            )
          : isPdf && localPath != null
              ? SfPdfViewer.file(File(localPath!))
              : isPdf
                  ? FutureBuilder<String?>(
                      future: AuthService.getToken(),
                      builder: (c, s) {
                        final h = s.data != null ? {'Authorization': 'Bearer ${s.data}'} : null;
                        return SfPdfViewer.network(widget.url, headers: h as Map<String, String>?);
                      },
                    )
                  : Center(
                      child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                        const Icon(Icons.insert_drive_file_rounded, size: 80, color: Colors.white38),
                        const SizedBox(height: 12),
                        Padding(padding: const EdgeInsets.symmetric(horizontal: 20), child: Text(widget.fileName, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white70))),
                        const SizedBox(height: 8),
                        Text(l10n.previewNotSupported, style: const TextStyle(color: Colors.white38, fontSize: 12)),
                        const SizedBox(height: 20),
                        ElevatedButton.icon(
                          onPressed: () => downloadWithAuth(widget.url, widget.fileName, context),
                          icon: const Icon(Icons.download_rounded),
                          label: Text(l10n.directDownloadOpen),
                          style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF8B7CFF), foregroundColor: Colors.white, shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20))),
                        ),
                        if (localPath != null)
                          Padding(
                            padding: const EdgeInsets.only(top: 10),
                            child: OutlinedButton(
                              onPressed: () => OpenFilex.open(localPath!),
                              style: OutlinedButton.styleFrom(side: const BorderSide(color: Colors.white38), shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20))),
                              child: Text(l10n.openDownloadedFile, style: const TextStyle(color: Colors.white)),
                            ),
                          ),
                      ]),
                    ),
    );
  }
}