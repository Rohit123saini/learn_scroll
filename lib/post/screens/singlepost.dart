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
import '../../services/comment_service.dart' show CommentModel, CommentService;
import '../widgets/comment_sheet.dart';
import '../widgets/repost_widgets.dart';
import '../widgets/post_ui.dart';
import '../widgets/post_media_view.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/ask_ai_sheet.dart';
import '../../widgets/skeletons.dart';
import '../../widgets/error_widgets.dart';
import '../../l10n/app_localizations.dart';
import '../models/models.dart';
import '../services/post_extras_service.dart';
import '../../services/home_api_model_service.dart' show HomeFeedService, PostModel;

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
  /// N7-FE — set when opened from a "commented / replied" notification
  /// (`data.comment_id`). Once the post has loaded, the comment sheet
  /// opens by itself and jumps to + highlights this comment (~2s).
  final String? highlightCommentId;
  const SinglePostPage({super.key, required this.postId, this.highlightCommentId});
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
  // 🔥 NAYA — bookmark icon on the detail page, same optimistic-update +
  // rollback shape _handleReaction already uses below, wired to the
  // SAME toggle endpoint the home feed's save icon (home.dart's
  // _toggleSave) and the profile "Saved" tab already use.
  bool isSaved = false;
  int savesCount = 0;
  bool _savePending = false;
  // Repost feature. On a repost page these describe the ORIGINAL (the Repost
  // button targets it, same as the backend's chain-flattening).
  int repostsCount = 0;
  bool isReposted = false;
  bool _repostPending = false;
  int currentIndex = 0;
  String fullImageUrl = "";
  String? myUsername;
  PageController pageController = PageController();
  List<CommentModel> _previewComments = [];
  // N7-FE — consumed exactly once: reopening the sheet later by hand
  // must not re-run the jump/highlight.
  String? _pendingHighlightId;

  @override
  void initState() {
    super.initState();
    _pendingHighlightId = (widget.highlightCommentId?.isNotEmpty ?? false) ? widget.highlightCommentId : null;
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
          isSaved = model.isSaved;
          savesCount = model.savesCount;
          repostsCount = model.isRepost ? (model.originalPost?.repostsCount ?? 0) : model.repostsCount;
          isReposted = model.isRepost ? (model.originalPost?.isRepostedByMe ?? false) : model.isRepostedByMe;
          isLoading = false;
        });
        if (model.username.isNotEmpty) _fetchPhotoFromSearchApi(model.username);
        _loadCommentsPreview();
        if (_pendingHighlightId != null) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted) _openCommentSheet();
          });
        }
      }
    } catch (e) {
      if (mounted) setState(() { error = e.toString(); isLoading = false; });
    }
  }

  /// Inline "top comments" preview under the post — a lighter read of
  /// the same list `CommentBottomSheet` shows in full, so the detail
  /// screen doesn't need a separate/duplicated comments endpoint.
  /// Best-effort: a failure here just leaves the preview empty; the
  /// comment sheet itself (which retries properly) is still reachable.
  Future<void> _loadCommentsPreview() async {
    try {
      final all = await CommentService.getComments(widget.postId);
      if (!mounted) return;
      final topLevel = all.where((c) => c.parent == null && !c.isHidden).toList()
        ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
      setState(() => _previewComments = topLevel.take(2).toList());
    } catch (_) {
      // silent — preview is a bonus, not the primary way to read comments
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

  /// Double-tap-on-media: always LIKEs, never toggles a like off — a
  /// double tap on an already-liked post just replays the heart burst.
  void _handleDoubleTapLike() {
    if (myReaction != 'like') {
      _handleReaction('like');
    } else {
      HapticFeedback.lightImpact();
    }
  }

  Future<void> _toggleSave() async {
    if (_savePending) return;
    HapticFeedback.lightImpact();
    final oldSaved = isSaved;
    final oldCount = savesCount;
    setState(() {
      _savePending = true;
      isSaved = !isSaved;
      savesCount = isSaved ? oldCount + 1 : (oldCount > 0 ? oldCount - 1 : 0);
    });
    try {
      final res = await HomeFeedService.toggleSave(widget.postId);
      if (!mounted) return;
      setState(() {
        isSaved = res['is_saved'] == true;
        savesCount = (res['saves_count'] as int?) ?? savesCount;
        _savePending = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        isSaved = oldSaved;
        savesCount = oldCount;
        _savePending = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(AppLocalizations.of(context)!.saveFailed), backgroundColor: Theme.of(context).colorScheme.error),
      );
    }
  }

  // ---------------- REPOST ----------------
  // tap = quick repost, long-press = "Repost with caption" sheet.
  String? get _repostTargetId =>
      post == null ? null : (post!.isRepost ? post!.originalPost?.id : post!.id);

  RepostPreview? get _repostTargetPreview {
    if (post == null) return null;
    if (!post!.isRepost) return RepostPreview.fromSingle(post!);
    final PostModel? o = post!.originalPost;
    return o == null ? null : RepostPreview.fromPost(o);
  }

  void _repostSnack(String msg, {SnackBarAction? action}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(msg), action: action));
  }

  void _onRepostTap() {
    if (isReposted) {
      _repostSnack(AppLocalizations.of(context)!.repostAlready);
      return;
    }
    _repostNow();
  }

  Future<void> _onRepostLongPress() async {
    final preview = _repostTargetPreview;
    if (preview == null) {
      _repostSnack(AppLocalizations.of(context)!.repostOriginalUnavailable);
      return;
    }
    HapticFeedback.selectionClick();
    final caption = await showRepostCaptionSheet(context, original: preview);
    if (caption == null || !mounted) return;
    await _repostNow(caption: caption);
  }

  Future<void> _repostNow({String? caption}) async {
    final l10n = AppLocalizations.of(context)!;
    final targetId = _repostTargetId;
    if (targetId == null) {
      _repostSnack(l10n.repostOriginalUnavailable);
      return;
    }
    if (_repostPending) return;
    HapticFeedback.lightImpact();
    setState(() => _repostPending = true);
    try {
      final res = await PostExtrasService.repost(targetId, caption: caption);
      if (!mounted) return;
      setState(() {
        repostsCount = res.repostsCount;
        isReposted = true;
        _repostPending = false;
      });
      _repostSnack(l10n.repostDone,
          action: SnackBarAction(label: l10n.repostUndo, onPressed: () => _undoRepost(res.repostId)));
    } on RepostException catch (e) {
      if (mounted) setState(() => _repostPending = false);
      _repostSnack(e.statusCode == 404 ? l10n.repostOriginalUnavailable : l10n.repostFailed);
    } catch (_) {
      if (mounted) setState(() => _repostPending = false);
      _repostSnack(l10n.repostFailed);
    }
  }

  Future<void> _undoRepost(String repostId) async {
    if (repostId.isEmpty) return;
    final l10n = AppLocalizations.of(context)!;
    try {
      final ok = await PostExtrasService.deletePost(repostId);
      if (!mounted) return;
      if (ok) {
        setState(() {
          repostsCount = repostsCount > 0 ? repostsCount - 1 : 0;
          isReposted = false;
        });
        _repostSnack(l10n.repostRemoved);
      } else {
        _repostSnack(l10n.repostFailed);
      }
    } catch (_) {
      _repostSnack(l10n.repostFailed);
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

  // NEW — post edit. Owner-only (same as delete above); backend
  // `PATCH /post/<id>/edit/` 403s for anyone else. Only sends the fields
  // that actually changed, and re-loads the post on success so every
  // derived bit of state (hashtags, `is_edited`, etc) reflects what the
  // server actually saved rather than being patched locally by guess.
  Future<void> _openEditSheet() async {
    if (post == null) return;
    final l10n = AppLocalizations.of(context)!;
    final titleCtrl = TextEditingController(text: post!.title ?? '');
    final contentCtrl = TextEditingController(text: post!.caption);
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) {
        final cs = Theme.of(ctx).colorScheme;
        bool saving = false;
        return StatefulBuilder(
          builder: (ctx, setSheetState) => Padding(
            padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom),
            child: Container(
              decoration: BoxDecoration(color: cs.surface, borderRadius: const BorderRadius.vertical(top: Radius.circular(24))),
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
              child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
                Center(child: Container(width: 40, height: 4, margin: const EdgeInsets.only(bottom: 16), decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)))),
                Text(l10n.editPostCta, style: LsType.head(ctx, size: 18)),
                const SizedBox(height: 16),
                TextField(controller: titleCtrl, decoration: InputDecoration(labelText: l10n.postTitleFieldLabel, border: const OutlineInputBorder())),
                const SizedBox(height: 12),
                TextField(controller: contentCtrl, decoration: InputDecoration(labelText: l10n.captionLabel, border: const OutlineInputBorder()), maxLines: 5, minLines: 3),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton(
                    onPressed: saving
                        ? null
                        : () async {
                            setSheetState(() => saving = true);
                            try {
                              await PostExtrasService.editPost(post!.id, {
                                'title': titleCtrl.text.trim(),
                                'content': contentCtrl.text.trim(),
                              });
                              if (ctx.mounted) Navigator.pop(ctx, true);
                            } on PostEditException catch (e) {
                              setSheetState(() => saving = false);
                              if (ctx.mounted) ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(e.message)));
                            } catch (_) {
                              setSheetState(() => saving = false);
                              if (ctx.mounted) ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(l10n.postUpdateFailed)));
                            }
                          },
                    child: saving
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                        : Text(l10n.save),
                  ),
                ),
              ]),
            ),
          ),
        );
      },
    );
    if (saved == true && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.postUpdated)));
      await _loadPost();
    }
  }

  // NEW — visibility change. Same owner-only backend guard as edit/delete.
  // `PATCH /post/<id>/visibility/` — kept as a separate endpoint/sheet
  // from edit above since a privacy change doesn't mark the post "edited".
  Future<void> _openChangePrivacySheet() async {
    if (post == null) return;
    final l10n = AppLocalizations.of(context)!;
    final options = <String, String>{
      'public': l10n.visibilityPublic,
      'connections': l10n.visibilityConnections,
      'private': l10n.visibilityPrivate,
    };
    final chosen = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (ctx) {
        final cs = Theme.of(ctx).colorScheme;
        return Container(
          decoration: BoxDecoration(color: cs.surface, borderRadius: const BorderRadius.vertical(top: Radius.circular(24))),
          padding: const EdgeInsets.symmetric(vertical: 12),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Padding(padding: const EdgeInsets.fromLTRB(16, 4, 16, 12), child: Align(alignment: Alignment.centerLeft, child: Text(l10n.changePrivacyTitle, style: LsType.head(ctx, size: 18)))),
            for (final entry in options.entries)
              ListTile(
                title: Text(entry.value),
                trailing: post!.visibility == entry.key ? Icon(Icons.check_rounded, color: cs.primary) : null,
                onTap: () => Navigator.pop(ctx, entry.key),
              ),
          ]),
        );
      },
    );
    if (chosen == null || chosen == post!.visibility || !mounted) return;
    try {
      await PostExtrasService.updateVisibility(post!.id, chosen);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.privacyUpdated)));
      await _loadPost();
    } on PostEditException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    } catch (_) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.privacyUpdateFailed)));
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
    final highlightId = _pendingHighlightId;
    _pendingHighlightId = null; // one-shot
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
            highlightCommentId: highlightId, // N7-FE — new optional param
            onCommentAdded: () => setState(() => commentsCount++),
            onGoToProfile: _goToProfile,
          ),
        ),
      ),
    ).then((_) => _loadCommentsPreview());
  }

  /// A light "top comments" read-out under the post, Instagram-style —
  /// "View all N comments" plus the 1-2 most recent top-level comments,
  /// tappable straight into the full `CommentBottomSheet`.
  Widget _buildCommentPreview(ColorScheme cs, AppLocalizations l10n) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        InkWell(
          onTap: _openCommentSheet,
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Text(l10n.commentsCount(commentsCount),
                style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
          ),
        ),
        for (final c in _previewComments)
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: InkWell(
              onTap: _openCommentSheet,
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                GestureDetector(
                  onTap: () => _goToProfile(c.user.username),
                  child: CircleAvatar(
                    radius: 12,
                    backgroundColor: cs.surfaceVariant,
                    backgroundImage: c.user.profilePicture != null && c.user.profilePicture!.isNotEmpty
                        ? CachedNetworkImageProvider(c.user.profilePicture!, maxWidth: 64)
                        : null,
                    child: c.user.profilePicture == null || c.user.profilePicture!.isEmpty
                        ? Icon(Icons.person_rounded, size: 13, color: cs.onSurfaceVariant)
                        : null,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text.rich(
                    TextSpan(children: [
                      TextSpan(text: '${c.user.username}  ', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: cs.onSurface)),
                      TextSpan(text: c.content, style: TextStyle(fontSize: 13, color: cs.onSurface.withOpacity(.85))),
                    ]),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ]),
            ),
          ),
      ]),
    );
  }

  void _openFullScreen() {
    final media = post?.media ?? [];
    if (media.isEmpty) return;
    final m = media[currentIndex];
    final fileUrl = buildMediaUrl(m.file);
    switch (PostMediaUtil.kind(m, url: fileUrl)) {
      case PostMediaKind.video:
        Navigator.push(context, MaterialPageRoute(builder: (_) => FullScreenVideoPage(url: fileUrl)));
        break;
      case PostMediaKind.image:
        // Swipeable + zoomable gallery over every image of this post.
        final urls = <String>[];
        var start = 0;
        for (var i = 0; i < media.length; i++) {
          final u = buildMediaUrl(media[i].file);
          if (PostMediaUtil.kind(media[i], url: u) != PostMediaKind.image) continue;
          if (i == currentIndex) start = urls.length;
          urls.add(u);
        }
        Navigator.push(context, MaterialPageRoute(builder: (_) => FullScreenImagePage(url: fileUrl, urls: urls, initialIndex: start)));
        break;
      case PostMediaKind.pdf:
      case PostMediaKind.doc:
        Navigator.push(context, MaterialPageRoute(builder: (_) => DocumentViewerPage(url: fileUrl, fileName: PostMediaUtil.displayName(m, url: fileUrl))));
        break;
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    if (isLoading) {
      return Scaffold(
        backgroundColor: cs.background,
        appBar: AppBar(backgroundColor: cs.surface, elevation: 0),
        body: LsShimmer(
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 12),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: const [
              Padding(
                padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
                child: Row(children: [
                  LsSkeletonBox(width: 44, height: 44, radius: 22),
                  SizedBox(width: 12),
                  Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    LsSkeletonBox(width: 120, height: 12, radius: 6),
                    SizedBox(height: 6),
                    LsSkeletonBox(width: 70, height: 9, radius: 5),
                  ]),
                ]),
              ),
              Padding(
                padding: EdgeInsets.symmetric(horizontal: 12),
                child: LsSkeletonBox(height: 420, radius: 18),
              ),
              Padding(
                padding: EdgeInsets.fromLTRB(16, 16, 16, 0),
                child: LsSkeletonBox(height: 10, radius: 5),
              ),
            ]),
          ),
        ),
      );
    }
    if (error != null) {
      return Scaffold(
        backgroundColor: cs.background,
        appBar: AppBar(backgroundColor: cs.surface, elevation: 0),
        body: Center(
          child: ErrorStateWidget(
            title: error!,
            retryLabel: AppLocalizations.of(context)!.retry,
            icon: Icons.error_outline_rounded,
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
              // Reposts have no text/category fields of their own and no
              // separate visibility (PostEditAPIView/PostVisibilityAPIView
              // both 400 on a repost row) — only offer delete for those.
              final canEditThis = post != null && !post!.isRepost;
              return PopupMenuButton<String>(
                onSelected: (v) {
                  if (v == 'delete') _confirmDeletePost();
                  if (v == 'edit') _openEditSheet();
                  if (v == 'privacy') _openChangePrivacySheet();
                },
                itemBuilder: (_) => [
                  if (canEditThis) PopupMenuItem(value: 'edit', child: Text(AppLocalizations.of(context)!.editPostCta)),
                  if (canEditThis) PopupMenuItem(value: 'privacy', child: Text(AppLocalizations.of(context)!.changePrivacyCta)),
                  PopupMenuItem(value: 'delete', child: Text(AppLocalizations.of(context)!.deletePostCta)),
                ],
              );
            },
          ),
        ],
      ),
      body: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (post!.isRepost)
            RepostHeader(
              username: username,
              createdAt: createdAt,
              onUserTap: () => _goToProfile(username),
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
            )
          else
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
                    Text(username, style: TextStyle(fontWeight: FontWeight.w800, fontSize: 15, color: cs.onSurface)),
                    if (createdAt != null)
                      Text(
                        // NEW — "edited" tag, same idea as Instagram/Twitter.
                        // `post!.isEdited` mirrors the backend's `is_edited`
                        // flag, which PATCH /post/<id>/edit/ now actually sets.
                        post!.isEdited
                            ? '${timeago.format(createdAt, locale: Localizations.localeOf(context).languageCode)} · ${AppLocalizations.of(context)!.postEditedLabel}'
                            : timeago.format(createdAt, locale: Localizations.localeOf(context).languageCode),
                        style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
                      ),
                  ]),
                ),
              ]),
            ),
          ),
          if (post!.isRepost) ...[
            if ((post!.repostCaption ?? '').trim().isNotEmpty)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
                child: Text(post!.repostCaption!.trim(),
                    style: TextStyle(fontSize: 14.5, height: 1.4, color: cs.onSurface)),
              ),
            EmbeddedOriginalPost(
              preview: post!.originalPost == null ? null : RepostPreview.fromPost(post!.originalPost!),
              margin: const EdgeInsets.fromLTRB(12, 0, 12, 8),
              onTap: post!.originalPost == null
                  ? null
                  : () => Navigator.push(
                      context, MaterialPageRoute(builder: (_) => SinglePostPage(postId: post!.originalPost!.id))),
            ),
          ] else if (media.isNotEmpty)
          // Same media presentation as the home feed card (shared widgets in
          // post_media_view.dart): frame height follows the first image/video's
          // aspect ratio, images are contained (never cropped) over a blurred
          // backdrop, documents get a proper file card.
          DoubleTapLikeOverlay(
            onDoubleTapLike: _handleDoubleTapLike,
            child: LayoutBuilder(builder: (context, cons) {
              final height = PostMediaUtil.frameHeight(width: cons.maxWidth, media: media, maxHeight: MediaQuery.of(context).size.height * 0.62);
              return SizedBox(
                height: height,
                child: ColoredBox(
                  color: Colors.black,
                  child: Stack(children: [
                    PageView.builder(
                      controller: pageController,
                      itemCount: media.length,
                      onPageChanged: (i) => setState(() => currentIndex = i),
                      itemBuilder: (c, i) {
                        final m = media[i];
                        final fileUrl = buildMediaUrl(m.file);
                        switch (PostMediaUtil.kind(m, url: fileUrl)) {
                          case PostMediaKind.video:
                            return SmallVideoPlayer(url: fileUrl);
                          case PostMediaKind.image:
                            return PostImageTile(url: fileUrl, onTap: _openFullScreen);
                          case PostMediaKind.pdf:
                          case PostMediaKind.doc:
                            final name = PostMediaUtil.displayName(m, url: fileUrl);
                            return PostDocTile(
                              fileName: name,
                              ext: PostMediaUtil.ext(m.fileName.isNotEmpty ? m.fileName : fileUrl),
                              onOpen: _openFullScreen,
                              onDownload: () => downloadWithAuth(fileUrl, name.replaceAll(' ', '_'), context),
                            );
                        }
                        // ignore: dead_code
                        return const SizedBox.shrink();
                      },
                    ),
                    if (media.length > 1)
                      Positioned(top: 10, left: 10, child: PostMediaCounter(index: currentIndex, count: media.length)),
                    if (media.length > 1)
                      Positioned(bottom: 10, left: 0, right: 0, child: Center(child: PostCarouselDots(count: media.length, activeIndex: currentIndex))),
                    if (PostMediaUtil.kind(media[currentIndex], url: buildMediaUrl(media[currentIndex].file)) != PostMediaKind.doc &&
                        PostMediaUtil.kind(media[currentIndex], url: buildMediaUrl(media[currentIndex].file)) != PostMediaKind.pdf)
                      Positioned(top: 10, right: 10, child: PostMediaIconButton(icon: Icons.fullscreen_rounded, onTap: _openFullScreen, label: l10n.a11yFullScreen)),
                  ]),
                ),
              );
            }),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            child: Row(children: [
              _ReactionTapTarget(myReaction: myReaction, onReaction: _handleReaction),
              Text('${reactionCounts['total'] ?? 0}', style: TextStyle(fontWeight: FontWeight.w700, color: cs.onSurface, fontSize: 13)),
              const SizedBox(width: 16),
              Semantics(
                button: true,
                label: l10n.comment,
                child: InkWell(
                  onTap: _openCommentSheet,
                  borderRadius: BorderRadius.circular(20),
                  child: Padding(
                    padding: const EdgeInsets.all(8),
                    child: Icon(Icons.chat_bubble_outline_rounded, color: cs.onSurfaceVariant, size: 22),
                  ),
                ),
              ),
              Text('$commentsCount', style: TextStyle(fontWeight: FontWeight.w700, color: cs.onSurface, fontSize: 13)),
              const SizedBox(width: 10),
              Semantics(
                button: true,
                selected: isReposted,
                label: l10n.repostAction,
                child: InkWell(
                  onTap: _onRepostTap,
                  onLongPress: _onRepostLongPress,
                  borderRadius: BorderRadius.circular(20),
                  child: Padding(
                    padding: const EdgeInsets.all(8),
                    child: Icon(Icons.repeat_rounded, color: isReposted ? cs.primary : cs.onSurfaceVariant, size: 22),
                  ),
                ),
              ),
              Text('$repostsCount', style: TextStyle(fontWeight: FontWeight.w700, color: cs.onSurface, fontSize: 13)),
              const Spacer(),
              Semantics(
                button: true,
                label: l10n.share,
                child: InkWell(
                  onTap: () => Share.share(post!.isRepost ? (post!.originalPost?.content ?? '') : "${post!.title ?? ''}\n${post!.caption}"),
                  borderRadius: BorderRadius.circular(20),
                  child: Padding(
                    padding: const EdgeInsets.all(8),
                    child: Icon(Icons.share_outlined, color: cs.onSurfaceVariant, size: 21),
                  ),
                ),
              ),
              Semantics(
                button: true,
                selected: isSaved,
                label: isSaved ? l10n.savedPostsTitle : l10n.save,
                child: InkWell(
                  onTap: _toggleSave,
                  borderRadius: BorderRadius.circular(20),
                  child: Padding(
                    padding: const EdgeInsets.all(8),
                    child: Icon(
                      isSaved ? Icons.bookmark_rounded : Icons.bookmark_border_rounded,
                      color: isSaved ? cs.primary : cs.onSurfaceVariant,
                      size: 22,
                    ),
                  ),
                ),
              ),
              // 🔥 NAYA — Task G15 ("AI doubt-solving assistant"): "Ask AI"
              // entry point from a feed post — for education content this
              // is often "explain this concept", not just social reactions.
              // No dedicated 3-dot post menu exists yet in this screen (that's
              // Task G19 — report/block), so this is added as its own action
              // icon alongside like/comment/repost/share/save for now; once
              // G19's menu ships, this can move in there instead.
              Semantics(
                button: true,
                label: 'Ask AI',
                child: InkWell(
                  onTap: () => showAskAiSheet(
                    context,
                    contextType: 'feed_post',
                    contextText: '${post!.title ?? ''}\n${post!.caption}'.trim(),
                    contextPreview: post!.caption.isNotEmpty ? post!.caption : (post!.title ?? ''),
                    initialQuestion: 'Can you explain this?',
                    sourceId: post!.id,
                    // Study Buddy chips (Explain / Hindi / Quiz / Flashcards)
                    // PDF attachment par bhi chalte hain — pehla PDF bhejte hain.
                    pdfUrl: () {
                      for (final m in post!.media) {
                        if (m.mediaType == 'document' &&
                            m.file.toLowerCase().split('?').first.endsWith('.pdf')) {
                          return m.file;
                        }
                      }
                      return null;
                    }(),
                  ),
                  borderRadius: BorderRadius.circular(20),
                  child: Padding(
                    padding: const EdgeInsets.all(8),
                    child: Icon(Icons.auto_awesome_rounded, color: cs.onSurfaceVariant, size: 21),
                  ),
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
                if ((post!.categoryLabel ?? post!.category).isNotEmpty) PostTagChip(label: post!.categoryLabel ?? post!.category, emphasized: true),
                if ((post!.subcategoryLabel ?? post!.subcategory ?? '').isNotEmpty) PostTagChip(label: post!.subcategoryLabel ?? post!.subcategory ?? ''),
              ]),
            ),
          if (commentsCount > 0) _buildCommentPreview(cs, l10n),
          const SizedBox(height: 20),
        ]),
      ),
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
                return Semantics(
                  button: true,
                  selected: sel,
                  label: e.key,
                  child: GestureDetector(
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
    final likeLabel = AppLocalizations.of(context)!.like;
    return Builder(builder: (btnCtx) {
      return Semantics(
        button: true,
        selected: widget.myReaction != null,
        label: likeLabel,
        onLongPress: () => _showPicker(btnCtx),
        child: InkWell(
          onTap: () => widget.onReaction('like'),
          onLongPress: () => _showPicker(btnCtx),
          borderRadius: BorderRadius.circular(20),
          child: Padding(
            padding: const EdgeInsets.all(8),
            child: widget.myReaction == null
                ? Icon(Icons.favorite_border_rounded, color: cs.onSurfaceVariant)
                : Text(kReactionEmoji[widget.myReaction] ?? '👍', style: const TextStyle(fontSize: 20)),
          ),
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
  VideoPlayerController? _c;
  bool _ok = false;
  bool _failed = false;
  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    try {
      final token = await AuthService.getToken();
      final headers = token != null ? {'Authorization': 'Bearer $token'} : <String, String>{};
      final c = VideoPlayerController.networkUrl(Uri.parse(widget.url), httpHeaders: headers);
      _c = c;
      await c.initialize();
      await c.setLooping(true);
      await c.play();
      if (mounted) setState(() => _ok = true);
    } catch (_) {
      if (mounted) setState(() => _failed = true);
    }
  }

  @override
  void dispose() {
    _c?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_failed) {
      return Center(
        child: IconButton(tooltip: 'Refresh', 
          iconSize: 44,
          icon: const Icon(Icons.refresh_rounded, color: Colors.white70),
          onPressed: () {
            _c?.dispose();
            _c = null;
            setState(() { _failed = false; _ok = false; });
            _init();
          },
        ),
      );
    }
    final c = _c;
    if (!_ok || c == null) return const Center(child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white70));
    return GestureDetector(
      onTap: () { c.value.isPlaying ? c.pause() : c.play(); setState(() {}); },
      behavior: HitTestBehavior.opaque,
      child: Stack(alignment: Alignment.center, children: [
        Center(child: AspectRatio(aspectRatio: c.value.aspectRatio, child: VideoPlayer(c))),
        if (!c.value.isPlaying) const Icon(Icons.play_circle_fill_rounded, color: Colors.white70, size: 60),
        Positioned(
          left: 0,
          right: 0,
          bottom: 0,
          child: VideoProgressIndicator(c, allowScrubbing: true, padding: EdgeInsets.zero, colors: const VideoProgressColors(playedColor: Colors.white, bufferedColor: Colors.white24, backgroundColor: Colors.white12)),
        ),
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
                Positioned(top: 40, left: 10, child: IconButton(tooltip: 'Close', icon: const Icon(Icons.close_rounded, color: Colors.white, size: 28), onPressed: () => Navigator.pop(context))),
                Center(
                  child: IconButton(tooltip: _controller.value.isPlaying ? 'Pause' : 'Play', 
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

// Full-screen image viewer: never cropped (BoxFit.contain), pinch + double-tap
// zoom, swipe between the post's images, close button respects the status bar.
class FullScreenImagePage extends StatefulWidget {
  final String url;
  final List<String>? urls;
  final int initialIndex;
  const FullScreenImagePage({super.key, required this.url, this.urls, this.initialIndex = 0});
  @override
  State<FullScreenImagePage> createState() => _FullScreenImagePageState();
}

class _FullScreenImagePageState extends State<FullScreenImagePage> {
  late final PageController _pc;
  late final List<String> _urls;
  late int _index;
  bool _zoomed = false;

  @override
  void initState() {
    super.initState();
    _urls = (widget.urls != null && widget.urls!.isNotEmpty) ? widget.urls! : [widget.url];
    _index = widget.initialIndex.clamp(0, _urls.length - 1).toInt();
    _pc = PageController(initialPage: _index);
  }

  @override
  void dispose() {
    _pc.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(children: [
        PageView.builder(
          controller: _pc,
          itemCount: _urls.length,
          physics: _zoomed ? const NeverScrollableScrollPhysics() : const PageScrollPhysics(),
          onPageChanged: (i) => setState(() { _index = i; _zoomed = false; }),
          itemBuilder: (_, i) => _ZoomableImage(url: _urls[i], onZoomChanged: (z) { if (z != _zoomed) setState(() => _zoomed = z); }),
        ),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(6),
            child: Row(children: [
              IconButton(tooltip: 'Close', icon: const Icon(Icons.close_rounded, color: Colors.white, size: 28), onPressed: () => Navigator.pop(context)),
              const Spacer(),
              if (_urls.length > 1)
                Padding(padding: const EdgeInsets.only(right: 10), child: PostMediaCounter(index: _index, count: _urls.length)),
            ]),
          ),
        ),
      ]),
    );
  }
}

class _ZoomableImage extends StatefulWidget {
  final String url;
  final ValueChanged<bool> onZoomChanged;
  const _ZoomableImage({required this.url, required this.onZoomChanged});
  @override
  State<_ZoomableImage> createState() => _ZoomableImageState();
}

class _ZoomableImageState extends State<_ZoomableImage> {
  final TransformationController _tc = TransformationController();
  Offset _tapPos = Offset.zero;

  @override
  void dispose() {
    _tc.dispose();
    super.dispose();
  }

  bool get _isZoomed => _tc.value.getMaxScaleOnAxis() > 1.02;

  void _doubleTap() {
    if (_isZoomed) {
      _tc.value = Matrix4.identity();
    } else {
      const scale = 2.6;
      final x = -_tapPos.dx * (scale - 1);
      final y = -_tapPos.dy * (scale - 1);
      _tc.value = Matrix4.identity()
        ..translate(x, y)
        ..scale(scale);
    }
    widget.onZoomChanged(_isZoomed);
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onDoubleTapDown: (d) => _tapPos = d.localPosition,
      onDoubleTap: _doubleTap,
      child: InteractiveViewer(
        transformationController: _tc,
        minScale: 1.0,
        maxScale: 5.0,
        onInteractionEnd: (_) => widget.onZoomChanged(_isZoomed),
        child: SizedBox.expand(
          child: CachedNetworkImage(
            imageUrl: widget.url,
            fit: BoxFit.contain,
            placeholder: (_, __) => const Center(child: CircularProgressIndicator(strokeWidth: 2.4, color: Colors.white54)),
            errorWidget: (_, __, ___) => const Center(child: Icon(Icons.broken_image_outlined, color: Colors.white38, size: 56)),
          ),
        ),
      ),
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
              : IconButton(tooltip: 'Download', icon: const Icon(Icons.download_rounded, color: Colors.white), onPressed: () => downloadWithAuth(widget.url, widget.fileName, context)),
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