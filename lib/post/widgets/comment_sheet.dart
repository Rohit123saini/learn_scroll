// lib/post/widgets/comment_sheet.dart
//
// UI/UX PASS — redesigned to match home.dart's LearnScroll design language
// (theme_service.dart's ColorScheme/AppThemeTokens, LsType headers, fully
// localized via AppLocalizations/language_service.dart). No behavioural
// change: every method, callback and service call below is the same as
// before — comments, threaded replies, reactions, edit/delete/hide, file
// attachments (camera/gallery/document), chunked-upload progress and the
// full-screen media viewers all work exactly as they did. Only the widget
// tree was rebuilt for a calmer, more polished look that adapts to light/
// dark theme and to whichever language is active.
//
// Shared by home.dart's feed (`_openCommentSheet`) and singlepost.dart
// (`_openCommentSheet`) so there's exactly one comment-sheet
// implementation instead of two copies drifting apart over time.

import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:video_player/video_player.dart';
import 'package:image_picker/image_picker.dart';
import 'package:file_selector/file_selector.dart';
import 'package:open_filex/open_filex.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../../services/comment_service.dart';
import '../../services/auth_service.dart';
import '../../widgets/ls_ui.dart';
import '../../l10n/app_localizations.dart';

// ===================== COMMENT SYSTEM - THREAD SERIES LOGIC =====================
// Decoupled from PostModel: takes postId/postOwnerId/initialCommentsCount
// directly, so this same widget works from home.dart's feed AND from
// singlepost.dart (which only has a raw post Map).

class CommentBottomSheet extends StatefulWidget {
  final String postId;
  final String postOwnerId;
  final int initialCommentsCount;
  final VoidCallback onCommentAdded;
  final Function(String) onGoToProfile;
  const CommentBottomSheet({
    super.key,
    required this.postId,
    required this.postOwnerId,
    required this.initialCommentsCount,
    required this.onCommentAdded,
    required this.onGoToProfile,
  });
  @override
  State<CommentBottomSheet> createState() => _CommentBottomSheetState();
}

class _CommentBottomSheetState extends State<CommentBottomSheet> {
  List<CommentModel> comments = [];
  bool loading = true;
  bool isUploading = false;
  double uploadProgress = 0;
  final TextEditingController _controller = TextEditingController();
  final FocusNode _focusNode = FocusNode();
  List<File> _selectedFiles = [];
  String? replyToId;
  String? replyToName;
  final ImagePicker _picker = ImagePicker();
  final Map<String, List<CommentModel>> _localReplies = {};
  final Map<String, bool> _expandedMap = {};
  // local mirror of the count so the header updates immediately on send.
  late int _commentsCount = widget.initialCommentsCount;

  @override
  void initState() {
    super.initState();
    _fetchComments();
  }

  @override
  void dispose() {
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  Future<void> _fetchComments() async {
    try {
      final data = await CommentService.getComments(widget.postId);
      if (mounted) setState(() { comments = data; loading = false; });
    } catch (e) {
      if (mounted) setState(() => loading = false);
    }
  }

  bool _checkFileSize(File file) {
    final l10n = AppLocalizations.of(context)!;
    double sizeMB = file.lengthSync() / (1024 * 1024);
    if (sizeMB > 200) {
      final cs = Theme.of(context).colorScheme;
      showDialog(
        context: context,
        builder: (_) => AlertDialog(
          title: Row(children: [
            Icon(Icons.error_rounded, color: cs.error),
            const SizedBox(width: 8),
            Text(l10n.fileTooLargeTitle),
          ]),
          content: Text(l10n.fileTooLargeBody(sizeMB.toStringAsFixed(1))),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: Text(l10n.ok, style: TextStyle(color: cs.primary, fontWeight: FontWeight.bold)),
            ),
          ],
        ),
      );
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(l10n.fileTooLargeBody(sizeMB.toStringAsFixed(1))),
        backgroundColor: Theme.of(context).colorScheme.error,
      ));
      return false;
    }
    return true;
  }

  Future<void> _pickCameraPhoto() async {
    try {
      final XFile? photo = await _picker.pickImage(source: ImageSource.camera, imageQuality: 85);
      if (photo != null && mounted) {
        File f = File(photo.path);
        if (_checkFileSize(f)) setState(() => _selectedFiles.add(f));
      }
    } catch (_) {}
  }

  Future<void> _pickCameraVideo() async {
    try {
      final XFile? v = await _picker.pickVideo(source: ImageSource.camera, maxDuration: const Duration(minutes: 5));
      if (v != null && mounted) {
        File f = File(v.path);
        if (_checkFileSize(f)) setState(() => _selectedFiles.add(f));
      }
    } catch (_) {}
  }

  Future<void> _pickGalleryImage() async {
    try {
      final XFile? img = await _picker.pickImage(source: ImageSource.gallery, imageQuality: 85);
      if (img != null && mounted) {
        File f = File(img.path);
        if (_checkFileSize(f)) setState(() => _selectedFiles.add(f));
      }
    } catch (_) {}
  }

  Future<void> _pickGalleryVideo() async {
    try {
      final XFile? v = await _picker.pickVideo(source: ImageSource.gallery);
      if (v != null && mounted) {
        File f = File(v.path);
        if (_checkFileSize(f)) setState(() => _selectedFiles.add(f));
      }
    } catch (_) {}
  }

  Future<void> _pickFile() async {
    try {
      const XTypeGroup all = XTypeGroup(label: 'all');
      final XFile? f = await openFile(acceptedTypeGroups: [all]);
      if (f != null && mounted) {
        File file = File(f.path);
        if (_checkFileSize(file)) setState(() => _selectedFiles.add(file));
      }
    } catch (_) {}
  }

  Future<void> _openOptions() async {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    showModalBottomSheet(
      context: context,
      backgroundColor: cs.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
      builder: (c) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.only(top: 6, bottom: 6),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const _SheetGrabber(),
            const SizedBox(height: 6),
            _AttachOptionTile(icon: Icons.camera_alt_rounded, color: const Color(0xFF2563EB), label: l10n.attachCameraPhoto, onTap: () { Navigator.pop(c); _pickCameraPhoto(); }),
            _AttachOptionTile(icon: Icons.videocam_rounded, color: const Color(0xFFDC2626), label: l10n.attachCameraVideo, onTap: () { Navigator.pop(c); _pickCameraVideo(); }),
            _AttachOptionTile(icon: Icons.photo_library_rounded, color: const Color(0xFF16A34A), label: l10n.attachGalleryPhoto, onTap: () { Navigator.pop(c); _pickGalleryImage(); }),
            _AttachOptionTile(icon: Icons.video_library_rounded, color: const Color(0xFF9333EA), label: l10n.attachGalleryVideo, onTap: () { Navigator.pop(c); _pickGalleryVideo(); }),
            _AttachOptionTile(icon: Icons.attach_file_rounded, color: const Color(0xFFEA580C), label: l10n.attachDocument, onTap: () { Navigator.pop(c); _pickFile(); }),
          ]),
        ),
      ),
    );
  }

  Future<void> _send() async {
    if (_controller.text.trim().isEmpty && _selectedFiles.isEmpty) return;
    await WakelockPlus.enable();
    setState(() { isUploading = true; uploadProgress = 0; });
    String? curId = replyToId;
    try {
      final newComment = await CommentService.createComment(
        postId: widget.postId,
        parentId: curId,
        content: _controller.text.trim(),
        files: _selectedFiles,
        onProgress: (p) { if (mounted) setState(() => uploadProgress = p); },
      );
      if (mounted) {
        setState(() {
          if (curId == null) {
            comments.insert(0, newComment);
            _commentsCount++;
          } else {
            _localReplies.putIfAbsent(curId, () => []);
            _localReplies[curId]!.insert(0, newComment);
            _expandedMap[curId] = true;
            for (var c in comments) { if (c.id == curId) c.repliesCount++; }
          }
          _controller.clear();
          _selectedFiles = [];
          replyToId = null;
          replyToName = null;
          isUploading = false;
          uploadProgress = 0;
        });
        widget.onCommentAdded();
      }
    } catch (e) {
      if (mounted) {
        setState(() => isUploading = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(AppLocalizations.of(context)!.failedWithError(e.toString())),
          backgroundColor: Theme.of(context).colorScheme.error,
        ));
      }
    } finally {
      await WakelockPlus.disable();
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return SafeArea(
      child: DraggableScrollableSheet(
        initialChildSize: 0.85,
        maxChildSize: 0.95,
        minChildSize: 0.5,
        expand: false,
        builder: (context, scrollController) {
          return Container(
            decoration: BoxDecoration(
              color: cs.surface,
              borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
            ),
            child: Column(children: [
              const SizedBox(height: 10),
              const _SheetGrabber(),
              const SizedBox(height: 12),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 18),
                child: Row(children: [
                  Text(l10n.comment, style: LsType.head(context, size: 16)),
                  const SizedBox(width: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                    decoration: BoxDecoration(color: cs.primary.withOpacity(0.12), borderRadius: BorderRadius.circular(20)),
                    child: Text('$_commentsCount', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800, color: cs.primary)),
                  ),
                ]),
              ),
              const SizedBox(height: 10),
              Divider(color: cs.outlineVariant, height: 1),
              if (isUploading)
                Padding(
                  padding: const EdgeInsets.fromLTRB(18, 10, 18, 4),
                  child: Column(children: [
                    ClipRRect(
                      borderRadius: BorderRadius.circular(20),
                      child: LinearProgressIndicator(
                        value: uploadProgress > 0 ? uploadProgress / 100 : null,
                        color: cs.primary,
                        backgroundColor: cs.surfaceVariant,
                        minHeight: 6,
                      ),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      uploadProgress > 0 ? l10n.uploadingPercent(uploadProgress.toStringAsFixed(0)) : l10n.compressing,
                      style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant, fontWeight: FontWeight.w600),
                    ),
                  ]),
                ),
              Expanded(
                child: loading
                    ? Center(child: CircularProgressIndicator(color: cs.primary))
                    : comments.isEmpty
                        ? _EmptyComments(l10n: l10n, cs: cs)
                        : ListView.builder(
                            controller: scrollController,
                            padding: const EdgeInsets.only(top: 6, bottom: 10),
                            itemCount: comments.length,
                            itemBuilder: (c, i) => CommentTile(
                              key: ValueKey(comments[i].id +
                                  comments[i].repliesCount.toString() +
                                  (_localReplies[comments[i].id]?.length ?? 0).toString() +
                                  (_expandedMap[comments[i].id]?.toString() ?? '')),
                              comment: comments[i],
                              postOwnerId: widget.postOwnerId,
                              level: 0,
                              localReplies: _localReplies[comments[i].id] ?? [],
                              allLocalReplies: _localReplies,
                              expandedMap: _expandedMap,
                              onReply: (id, name) {
                                setState(() { replyToId = id; replyToName = name; });
                                _focusNode.requestFocus();
                              },
                              onDeleted: (id) => setState(() {
                                comments.removeWhere((x) => x.id == id);
                                _commentsCount = _commentsCount > 0 ? _commentsCount - 1 : 0;
                              }),
                              onEdited: (updated) => setState(() {
                                int idx = comments.indexWhere((x) => x.id == updated.id);
                                if (idx != -1) comments[idx] = updated;
                              }),
                              onHidden: (id) => setState(() {
                                comments.removeWhere((x) => x.id == id);
                                _commentsCount = _commentsCount > 0 ? _commentsCount - 1 : 0;
                              }),
                              onGoToProfile: widget.onGoToProfile,
                            ),
                          ),
              ),
              if (replyToName != null)
                Container(
                  width: double.infinity,
                  margin: const EdgeInsets.fromLTRB(14, 0, 14, 8),
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
                  decoration: BoxDecoration(color: cs.primary.withOpacity(0.10), borderRadius: BorderRadius.circular(14)),
                  child: Row(children: [
                    Icon(Icons.reply_rounded, color: cs.primary, size: 16),
                    const SizedBox(width: 8),
                    Expanded(child: Text(l10n.replyingTo(replyToName!), style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.primary), overflow: TextOverflow.ellipsis)),
                    GestureDetector(
                      onTap: () => setState(() { replyToId = null; replyToName = null; }),
                      child: Icon(Icons.close_rounded, color: cs.primary, size: 18),
                    ),
                  ]),
                ),
              if (_selectedFiles.isNotEmpty)
                Container(
                  height: 96,
                  padding: const EdgeInsets.symmetric(vertical: 6),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Text(l10n.filesSelectedCount(_selectedFiles.length), style: TextStyle(fontSize: 12, color: cs.primary, fontWeight: FontWeight.w700)),
                    ),
                    Expanded(
                      child: ListView.builder(
                        padding: const EdgeInsets.symmetric(horizontal: 10),
                        scrollDirection: Axis.horizontal,
                        itemCount: _selectedFiles.length,
                        itemBuilder: (c, i) {
                          bool isVideo = _selectedFiles[i].path.toLowerCase().endsWith('.mp4') || _selectedFiles[i].path.toLowerCase().endsWith('.mov');
                          return Stack(children: [
                            Container(
                              margin: const EdgeInsets.all(6),
                              width: 66,
                              height: 66,
                              decoration: BoxDecoration(borderRadius: BorderRadius.circular(14), color: Colors.black, boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.15), blurRadius: 6, offset: const Offset(0, 2))]),
                              child: ClipRRect(
                                borderRadius: BorderRadius.circular(14),
                                child: isVideo ? const Icon(Icons.videocam_rounded, color: Colors.white) : Image.file(_selectedFiles[i], fit: BoxFit.cover),
                              ),
                            ),
                            Positioned(
                              top: 0,
                              right: 0,
                              child: GestureDetector(
                                onTap: () => setState(() => _selectedFiles.removeAt(i)),
                                child: Container(
                                  decoration: BoxDecoration(color: cs.error, shape: BoxShape.circle, border: Border.all(color: cs.surface, width: 1.5)),
                                  child: const Icon(Icons.close_rounded, size: 16, color: Colors.white),
                                ),
                              ),
                            ),
                          ]);
                        },
                      ),
                    ),
                  ]),
                ),
              Padding(
                padding: EdgeInsets.only(left: 10, right: 10, bottom: MediaQuery.of(context).viewPadding.bottom + 10, top: 8),
                child: Row(children: [
                  IconButton(
                    icon: Icon(Icons.add_circle_rounded, color: isUploading ? cs.onSurfaceVariant.withOpacity(0.4) : cs.primary, size: 26),
                    onPressed: isUploading ? null : _openOptions,
                  ),
                  Expanded(
                    child: TextField(
                      controller: _controller,
                      focusNode: _focusNode,
                      enabled: !isUploading,
                      minLines: 1,
                      maxLines: 4,
                      textCapitalization: TextCapitalization.sentences,
                      style: TextStyle(color: cs.onSurface, fontSize: 14),
                      decoration: InputDecoration(
                        hintText: l10n.addCommentHint,
                        hintStyle: TextStyle(color: cs.onSurfaceVariant),
                        filled: true,
                        fillColor: cs.surfaceVariant,
                        isDense: true,
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(22), borderSide: BorderSide.none),
                        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(22), borderSide: BorderSide.none),
                        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(22), borderSide: BorderSide(color: cs.primary, width: 1.4)),
                        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 11),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  isUploading
                      ? SizedBox(width: 40, height: 40, child: Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2.2, color: cs.primary))))
                      : Material(
                          color: cs.primary,
                          shape: const CircleBorder(),
                          child: InkWell(
                            customBorder: const CircleBorder(),
                            onTap: _send,
                            child: Padding(padding: const EdgeInsets.all(9), child: Icon(Icons.arrow_upward_rounded, color: cs.onPrimary, size: 20)),
                          ),
                        ),
                ]),
              ),
            ]),
          );
        },
      ),
    );
  }
}

class _SheetGrabber extends StatelessWidget {
  const _SheetGrabber();
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(width: 40, height: 4, decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(10)));
  }
}

class _EmptyComments extends StatelessWidget {
  final AppLocalizations l10n;
  final ColorScheme cs;
  const _EmptyComments({required this.l10n, required this.cs});
  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Container(
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(color: cs.surfaceVariant, shape: BoxShape.circle),
          child: Icon(Icons.mode_comment_outlined, size: 30, color: cs.onSurfaceVariant),
        ),
        const SizedBox(height: 14),
        Text(l10n.noCommentsYet, style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: cs.onSurface)),
        const SizedBox(height: 4),
        Text(l10n.beFirstToComment, style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
      ]),
    );
  }
}

class _AttachOptionTile extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String label;
  final VoidCallback onTap;
  const _AttachOptionTile({required this.icon, required this.color, required this.label, required this.onTap});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListTile(
      leading: Container(
        padding: const EdgeInsets.all(9),
        decoration: BoxDecoration(color: color.withOpacity(0.12), borderRadius: BorderRadius.circular(12)),
        child: Icon(icon, color: color, size: 20),
      ),
      title: Text(label, style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: cs.onSurface)),
      onTap: onTap,
    );
  }
}

class CommentTile extends StatefulWidget {
  final CommentModel comment;
  final String postOwnerId;
  final Function(String, String) onReply;
  final Function(String)? onDeleted;
  final Function(CommentModel)? onEdited;
  final Function(String)? onHidden;
  final List<CommentModel> localReplies;
  final Map<String, List<CommentModel>> allLocalReplies;
  final Map<String, bool> expandedMap;
  final int level;
  final Function(String) onGoToProfile;
  const CommentTile({
    super.key,
    required this.comment,
    required this.postOwnerId,
    required this.onReply,
    this.onDeleted,
    this.onEdited,
    this.onHidden,
    this.localReplies = const [],
    this.allLocalReplies = const {},
    this.expandedMap = const {},
    this.level = 0,
    required this.onGoToProfile,
  });
  @override
  State<CommentTile> createState() => _CommentTileState();
}

class _CommentTileState extends State<CommentTile> {
  List<CommentModel> replies = [];
  bool showReplies = false;
  bool loadingReplies = false;
  String? _myUserId;
  final Map<String, String> _emojiMap = {'like': '👍', 'confuse': '🤔', 'wrong': '❗', 'imp': '⭐', 'explain': '💡'};
  final Map<String, Color> _emojiColor = {
    'like': const Color(0xFF1877F2),
    'confuse': const Color(0xFFF7B928),
    'wrong': const Color(0xFFE0245E),
    'imp': const Color(0xFFFFAD33),
    'explain': const Color(0xFF45BD62),
  };
  OverlayEntry? _overlayEntry;

  @override
  void initState() {
    super.initState();
    _getMyId();
    if (widget.localReplies.isNotEmpty) { replies = widget.localReplies; showReplies = true; }
    if (widget.expandedMap[widget.comment.id] == true) {
      showReplies = true;
      if (replies.isEmpty) _loadReplies();
    }
  }

  @override
  void didUpdateWidget(covariant CommentTile oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.localReplies.length != oldWidget.localReplies.length || widget.expandedMap[widget.comment.id] == true) {
      setState(() {
        final ids = replies.map((e) => e.id).toSet();
        for (var r in widget.localReplies) { if (!ids.contains(r.id)) replies.insert(0, r); }
        if (widget.localReplies.isNotEmpty) showReplies = true;
      });
    }
  }

  @override
  void dispose() {
    _hideOverlay();
    super.dispose();
  }

  Future<void> _getMyId() async {
    final id = await AuthService.getUserId();
    if (mounted) setState(() => _myUserId = id);
  }

  bool get _isMyComment => _myUserId != null && widget.comment.user.id.toString() == _myUserId.toString();
  bool get _isPostOwner => _myUserId != null && widget.postOwnerId == _myUserId.toString();
  bool get _canShowMenu => _isMyComment || _isPostOwner;

  void _showReactionOverlay(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final RenderBox box = context.findRenderObject() as RenderBox;
    final Offset pos = box.localToGlobal(Offset.zero);
    _overlayEntry = OverlayEntry(
      builder: (c) => Stack(children: [
        GestureDetector(onTap: () => _hideOverlay(), child: Container(color: Colors.transparent, width: double.infinity, height: double.infinity)),
        Positioned(
          left: 20,
          top: pos.dy - 62,
          child: Material(
            color: Colors.transparent,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
              decoration: BoxDecoration(color: cs.surface, borderRadius: BorderRadius.circular(30), boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.18), blurRadius: 14, offset: const Offset(0, 4))]),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: _emojiMap.entries.map((e) {
                  bool sel = widget.comment.myReaction == e.key;
                  return GestureDetector(
                    onTap: () { _hideOverlay(); _handleReaction(e.key); },
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 150),
                      margin: const EdgeInsets.symmetric(horizontal: 3),
                      padding: const EdgeInsets.all(8),
                      decoration: BoxDecoration(
                        color: sel ? _emojiColor[e.key]!.withOpacity(0.15) : cs.surfaceVariant,
                        shape: BoxShape.circle,
                        border: sel ? Border.all(color: _emojiColor[e.key]!, width: 2) : null,
                      ),
                      child: Text(e.value, style: const TextStyle(fontSize: 24)),
                    ),
                  );
                }).toList(),
              ),
            ),
          ),
        ),
      ]),
    );
    Overlay.of(context).insert(_overlayEntry!);
  }

  void _hideOverlay() { _overlayEntry?.remove(); _overlayEntry = null; }

  Future<void> _loadReplies() async {
    setState(() => loadingReplies = true);
    try {
      final data = await CommentService.getReplies(widget.comment.id);
      final merged = [...widget.localReplies, ...data];
      final ids = <String>{};
      final unique = merged.where((e) => ids.add(e.id)).toList();
      setState(() { replies = unique; showReplies = true; loadingReplies = false; });
    } catch (_) {
      setState(() => loadingReplies = false);
    }
  }

  Future<void> _handleReaction(String reaction) async {
    final old = widget.comment.myReaction;
    final oldCount = widget.comment.likesCount;
    setState(() {
      if (old == reaction) {
        widget.comment.myReaction = null;
        if (widget.comment.likesCount > 0) widget.comment.likesCount--;
      } else {
        if (old == null) widget.comment.likesCount++;
        widget.comment.myReaction = reaction;
      }
    });
    try {
      final res = await CommentService.toggleCommentReaction(widget.comment.id, reaction);
      if (mounted) {
        setState(() {
          widget.comment.myReaction = res['my_reaction'] ?? res['myReaction'];
          var counts = res['counts'] ?? res['reaction_counts'];
          if (counts != null) {
            widget.comment.reactionCounts = Map<String, int>.from(counts.map((k, v) => MapEntry(k.toString(), (v as int?) ?? 0)));
            widget.comment.likesCount = counts['total'] ?? oldCount;
          }
        });
      }
    } catch (_) {
      if (mounted) setState(() { widget.comment.myReaction = old; widget.comment.likesCount = oldCount; });
    }
  }

  void _showOptions() {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    List<Widget> options = [];
    if (_isMyComment) {
      options.add(_AttachOptionTile(icon: Icons.edit_rounded, color: cs.primary, label: l10n.editComment, onTap: () { Navigator.pop(context); _editDialog(); }));
      options.add(_AttachOptionTile(icon: Icons.delete_rounded, color: cs.error, label: l10n.delete, onTap: () { Navigator.pop(context); _deleteConfirm(); }));
    }
    if (_isPostOwner && !_isMyComment) {
      options.add(_AttachOptionTile(icon: Icons.visibility_off_rounded, color: const Color(0xFFEA580C), label: l10n.hideComment, onTap: () { Navigator.pop(context); _hideComment(); }));
    }
    if (options.isEmpty) return;
    showModalBottomSheet(
      context: context,
      backgroundColor: cs.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(22))),
      builder: (c) => SafeArea(child: Column(mainAxisSize: MainAxisSize.min, children: [const SizedBox(height: 6), const _SheetGrabber(), const SizedBox(height: 6), ...options, const SizedBox(height: 6)])),
    );
  }

  void _editDialog() {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    TextEditingController ctrl = TextEditingController(text: widget.comment.content);
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
        title: Text(l10n.editComment),
        content: TextField(
          controller: ctrl,
          maxLines: 4,
          autofocus: true,
          decoration: InputDecoration(border: const OutlineInputBorder(), hintText: l10n.editComment),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: Text(l10n.cancel)),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: cs.primary),
            onPressed: () async {
              if (ctrl.text.trim().isEmpty) return;
              Navigator.pop(context);
              try {
                final updated = await CommentService.editComment(commentId: widget.comment.id, content: ctrl.text.trim());
                if (mounted) setState(() => widget.comment.content = updated.content);
                widget.comment.isEdited = true;
                if (widget.onEdited != null) widget.onEdited!(updated);
                if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.commentEdited), backgroundColor: cs.primary));
              } catch (e) {
                if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.editFailed(e.toString())), backgroundColor: cs.error));
              }
            },
            child: Text(l10n.save, style: TextStyle(color: cs.onPrimary)),
          ),
        ],
      ),
    );
  }

  void _deleteConfirm() {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
        title: Text(l10n.deleteComment),
        content: Text(l10n.deleteCommentBody),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: Text(l10n.cancel)),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: cs.error),
            onPressed: () async {
              Navigator.pop(context);
              try {
                await CommentService.deleteComment(widget.comment.id);
                if (widget.onDeleted != null) widget.onDeleted!(widget.comment.id);
              } catch (e) {
                if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.deleteFailed(e.toString()))));
              }
            },
            child: Text(l10n.delete, style: const TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }

  void _hideComment() async {
    final l10n = AppLocalizations.of(context)!;
    try {
      await CommentService.hideComment(widget.comment.id);
      if (widget.onHidden != null) widget.onHidden!(widget.comment.id);
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.commentHidden), backgroundColor: const Color(0xFFEA580C)));
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.hideFailed(e.toString()))));
    }
  }

  bool _isImage(String url) { final l = url.toLowerCase(); return l.endsWith('.png') || l.endsWith('.jpg') || l.endsWith('.jpeg') || l.endsWith('.webp') || l.endsWith('.gif'); }
  bool _isVideo(String url, String type) { final l = url.toLowerCase(); return type == 'video' || l.endsWith('.mp4') || l.endsWith('.mov') || l.endsWith('.mkv'); }

  Future<void> _openFile(String url, String fileName) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      String? token = await AuthService.getToken();
      Directory dir = await getTemporaryDirectory();
      String savePath = '${dir.path}/${fileName.replaceAll(' ', '_')}';
      if (!await File(savePath).exists()) {
        await Dio().download(url, savePath, options: Options(headers: token != null && token.isNotEmpty ? {"Authorization": "Bearer $token"} : {}));
      }
      await OpenFilex.open(savePath);
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.openFileFailed(e.toString()))));
    }
  }

  void _openMedia(dynamic m) {
    if (_isImage(m.file)) {
      Navigator.push(context, MaterialPageRoute(builder: (_) => _CommentImageFullScreen(url: m.file, fileName: m.fileName)));
    } else if (_isVideo(m.file, m.mediaType)) {
      Navigator.push(context, MaterialPageRoute(builder: (_) => _CommentVideoFullScreen(url: m.file, fileName: m.fileName)));
    } else {
      _openFile(m.file, m.fileName);
    }
  }

  Widget _buildCommentVideoThumb() {
    return Container(
      height: 90,
      width: 120,
      decoration: BoxDecoration(color: Colors.black, borderRadius: BorderRadius.circular(12)),
      child: Stack(alignment: Alignment.center, children: [
        const Icon(Icons.videocam_rounded, color: Colors.white30, size: 28),
        Container(padding: const EdgeInsets.all(6), decoration: const BoxDecoration(color: Colors.black54, shape: BoxShape.circle), child: const Icon(Icons.play_arrow_rounded, color: Colors.white, size: 22)),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    List<MapEntry<String, int>> sorted = widget.comment.reactionCounts.entries.where((e) => e.value > 0 && e.key != 'total').toList();
    sorted.sort((a, b) => b.value.compareTo(a.value));
    var top3 = sorted.take(3).toList();
    double leftPad = widget.level == 0 ? 14 : 36;

    return Padding(
      padding: EdgeInsets.only(left: leftPad, right: 14, top: 9, bottom: 9),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          GestureDetector(
            onTap: () => widget.onGoToProfile(widget.comment.user.username),
            child: CircleAvatar(
              radius: widget.level == 0 ? 18 : 14,
              backgroundColor: cs.surfaceVariant,
              backgroundImage: widget.comment.user.profilePicture != null && widget.comment.user.profilePicture!.isNotEmpty
                  ? CachedNetworkImageProvider(widget.comment.user.profilePicture!, maxWidth: 72)
                  : null,
              child: widget.comment.user.profilePicture == null || widget.comment.user.profilePicture!.isEmpty
                  ? Icon(Icons.person_rounded, size: widget.level == 0 ? 18 : 14, color: cs.onSurfaceVariant)
                  : null,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              GestureDetector(
                onLongPress: _canShowMenu ? () => _showOptions() : null,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(16)),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Row(children: [
                      Flexible(
                        child: GestureDetector(
                          onTap: () => widget.onGoToProfile(widget.comment.user.username),
                          child: Text(widget.comment.user.username, overflow: TextOverflow.ellipsis, style: TextStyle(fontWeight: FontWeight.w800, fontSize: 13.5, color: cs.onSurface)),
                        ),
                      ),
                      if (widget.comment.isEdited) Padding(padding: const EdgeInsets.only(left: 6), child: Text('· ${l10n.edited}', style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant))),
                      if (_isMyComment) Padding(padding: const EdgeInsets.only(left: 6), child: Text('· ${l10n.you}', style: TextStyle(fontSize: 10.5, color: cs.primary, fontWeight: FontWeight.bold))),
                    ]),
                    if (widget.comment.content.isNotEmpty)
                      Padding(padding: const EdgeInsets.only(top: 6), child: Text(widget.comment.content, style: TextStyle(fontSize: 14, height: 1.4, color: cs.onSurface))),
                    if (widget.comment.media.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Wrap(
                          spacing: 6,
                          runSpacing: 6,
                          children: widget.comment.media.map((m) {
                            if (_isImage(m.file)) {
                              return GestureDetector(
                                onTap: () => _openMedia(m),
                                child: ClipRRect(borderRadius: BorderRadius.circular(12), child: CachedNetworkImage(imageUrl: m.file, height: 88, width: 88, fit: BoxFit.cover, memCacheWidth: 176)),
                              );
                            } else if (_isVideo(m.file, m.mediaType)) {
                              return GestureDetector(onTap: () => _openMedia(m), child: _buildCommentVideoThumb());
                            } else {
                              return InkWell(
                                onTap: () => _openFile(m.file, m.fileName),
                                borderRadius: BorderRadius.circular(10),
                                child: Container(
                                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                                  decoration: BoxDecoration(color: cs.surface, borderRadius: BorderRadius.circular(10), border: Border.all(color: cs.outlineVariant)),
                                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                                    Icon(Icons.description_rounded, size: 16, color: cs.onSurfaceVariant),
                                    const SizedBox(width: 6),
                                    SizedBox(width: 70, child: Text(m.fileName, style: TextStyle(fontSize: 12, color: cs.onSurface), overflow: TextOverflow.ellipsis)),
                                  ]),
                                ),
                              );
                            }
                          }).toList(),
                        ),
                      ),
                    if (top3.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 6),
                        child: Row(children: [
                          Row(children: top3.map((e) => Text(_emojiMap[e.key] ?? '', style: const TextStyle(fontSize: 12))).toList()),
                          const SizedBox(width: 6),
                          Text('${widget.comment.likesCount}', style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
                        ]),
                      ),
                  ]),
                ),
              ),
              const SizedBox(height: 6),
              Row(children: [
                Text(timeago.format(widget.comment.createdAt, locale: Localizations.localeOf(context).languageCode), style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
                const SizedBox(width: 14),
                Builder(builder: (likeCtx) {
                  final active = widget.comment.myReaction != null;
                  return GestureDetector(
                    onTap: () => _handleReaction('like'),
                    onLongPress: () => _showReactionOverlay(likeCtx),
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 150),
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                      decoration: BoxDecoration(color: active ? _emojiColor[widget.comment.myReaction]!.withOpacity(0.12) : Colors.transparent, borderRadius: BorderRadius.circular(20)),
                      child: Row(mainAxisSize: MainAxisSize.min, children: [
                        Icon(active ? Icons.thumb_up_alt_rounded : Icons.thumb_up_alt_outlined, size: 15, color: active ? _emojiColor[widget.comment.myReaction] : cs.onSurfaceVariant),
                        const SizedBox(width: 4),
                        Text(active ? widget.comment.myReaction!.toUpperCase() : l10n.like, style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: active ? _emojiColor[widget.comment.myReaction] : cs.onSurfaceVariant)),
                        if (active) ...[const SizedBox(width: 4), Text(_emojiMap[widget.comment.myReaction] ?? '', style: const TextStyle(fontSize: 12))],
                      ]),
                    ),
                  );
                }),
                const SizedBox(width: 10),
                GestureDetector(
                  onTap: () => widget.onReply(widget.comment.id, widget.comment.user.username),
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Icon(Icons.chat_bubble_outline_rounded, size: 13, color: cs.onSurfaceVariant),
                    const SizedBox(width: 4),
                    Text(l10n.reply, style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant)),
                  ]),
                ),
                if (widget.comment.repliesCount > 0 || widget.localReplies.isNotEmpty) ...[
                  const SizedBox(width: 12),
                  GestureDetector(
                    onTap: () { if (showReplies) setState(() => showReplies = false); else _loadReplies(); },
                    child: Text(
                      loadingReplies ? l10n.loadingReplies : (showReplies ? l10n.hideReplies : l10n.viewReplies(widget.comment.repliesCount + widget.localReplies.length)),
                      style: TextStyle(fontSize: 12, color: cs.primary, fontWeight: FontWeight.w700),
                    ),
                  ),
                ],
              ]),
            ]),
          ),
          if (_canShowMenu)
            IconButton(
              icon: Icon(Icons.more_horiz_rounded, size: 18, color: cs.onSurfaceVariant),
              onPressed: () => _showOptions(),
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(),
              visualDensity: VisualDensity.compact,
            ),
        ]),
        if (showReplies)
          Padding(
            padding: EdgeInsets.only(top: 6, left: widget.level == 0 ? 14 : 0),
            child: Container(
              decoration: BoxDecoration(border: Border(left: BorderSide(color: cs.outlineVariant, width: 2))),
              child: Column(
                children: replies.map((r) {
                  List<CommentModel> nestedLocal = widget.allLocalReplies[r.id] ?? [];
                  return CommentTile(
                    comment: r,
                    postOwnerId: widget.postOwnerId,
                    level: 1,
                    localReplies: nestedLocal,
                    allLocalReplies: widget.allLocalReplies,
                    expandedMap: widget.expandedMap,
                    onReply: widget.onReply,
                    onDeleted: (id) => setState(() => replies.removeWhere((x) => x.id == id)),
                    onEdited: (updated) => setState(() { int idx = replies.indexWhere((x) => x.id == updated.id); if (idx != -1) replies[idx] = updated; }),
                    onHidden: (id) => setState(() => replies.removeWhere((x) => x.id == id)),
                    onGoToProfile: widget.onGoToProfile,
                  );
                }).toList(),
              ),
            ),
          ),
      ]),
    );
  }
}

class _CommentImageFullScreen extends StatelessWidget {
  final String url;
  final String fileName;
  const _CommentImageFullScreen({required this.url, required this.fileName});

  Future<void> _open(String url, String name, BuildContext context) async {
    String? token = await AuthService.getToken();
    Directory dir = await getTemporaryDirectory();
    String savePath = '${dir.path}/${name.replaceAll(' ', '_')}';
    if (!await File(savePath).exists()) {
      await Dio().download(url, savePath, options: Options(headers: token != null && token.isNotEmpty ? {"Authorization": "Bearer $token"} : {}));
    }
    await OpenFilex.open(savePath);
  }

  @override
  Widget build(BuildContext context) {
    final mq = MediaQuery.of(context);
    final memW = (mq.size.width * mq.devicePixelRatio * 2).round().clamp(600, 2400);
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        iconTheme: const IconThemeData(color: Colors.white),
        title: Text(fileName, style: const TextStyle(color: Colors.white, fontSize: 14), overflow: TextOverflow.ellipsis),
        actions: [IconButton(icon: const Icon(Icons.open_in_new_rounded, color: Colors.white), onPressed: () => _open(url, fileName, context))],
      ),
      body: SizedBox(
        width: double.infinity,
        height: double.infinity,
        child: InteractiveViewer(minScale: 0.5, maxScale: 6.0, child: Center(child: CachedNetworkImage(imageUrl: url, fit: BoxFit.contain, memCacheWidth: memW))),
      ),
    );
  }
}

class _CommentVideoFullScreen extends StatefulWidget {
  final String url;
  final String fileName;
  const _CommentVideoFullScreen({required this.url, required this.fileName});
  @override
  State<_CommentVideoFullScreen> createState() => _CommentVideoFullScreenState();
}

class _CommentVideoFullScreenState extends State<_CommentVideoFullScreen> {
  late VideoPlayerController _controller;
  bool _initialized = false;
  bool _showControls = true;
  Timer? _hideTimer;

  @override
  void initState() {
    super.initState();
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
    _controller = VideoPlayerController.networkUrl(Uri.parse(widget.url))
      ..initialize().then((_) {
        if (mounted) {
          setState(() => _initialized = true);
          _controller.setLooping(true);
          _controller.play();
          _startHideTimer();
        }
      });
    _controller.addListener(() { if (mounted) setState(() {}); });
  }

  @override
  void dispose() {
    _hideTimer?.cancel();
    _controller.dispose();
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
    SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]);
    super.dispose();
  }

  void _startHideTimer() {
    _hideTimer?.cancel();
    _hideTimer = Timer(const Duration(seconds: 5), () { if (mounted) setState(() => _showControls = false); });
  }

  void _toggleControls() { setState(() => _showControls = !_showControls); if (_showControls) _startHideTimer(); }
  String _format(Duration d) => "${d.inMinutes}:${(d.inSeconds % 60).toString().padLeft(2, '0')}";

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: GestureDetector(
        onTap: _toggleControls,
        child: Stack(children: [
          Center(
            child: _initialized
                ? AspectRatio(aspectRatio: _controller.value.aspectRatio, child: VideoPlayer(_controller))
                : const CircularProgressIndicator(color: Colors.white),
          ),
          if (_showControls)
            Positioned(
              top: 0,
              left: 0,
              right: 0,
              child: AppBar(
                backgroundColor: Colors.black54,
                iconTheme: const IconThemeData(color: Colors.white),
                title: Text(widget.fileName, style: const TextStyle(color: Colors.white, fontSize: 14), overflow: TextOverflow.ellipsis),
                actions: [IconButton(icon: const Icon(Icons.close_rounded, color: Colors.white), onPressed: () => Navigator.pop(context))],
              ),
            ),
          if (_showControls && _initialized)
            Positioned(
              bottom: 0,
              left: 0,
              right: 0,
              child: Container(
                padding: EdgeInsets.only(left: 10, right: 10, bottom: MediaQuery.of(context).padding.bottom + 20, top: 10),
                decoration: const BoxDecoration(gradient: LinearGradient(begin: Alignment.bottomCenter, end: Alignment.topCenter, colors: [Colors.black87, Colors.transparent])),
                child: Column(children: [
                  VideoProgressIndicator(_controller, allowScrubbing: true, colors: const VideoProgressColors(playedColor: Color(0xFF8B7CFF))),
                  const SizedBox(height: 12),
                  Row(children: [
                    IconButton(
                      icon: Icon(_controller.value.isPlaying ? Icons.pause_circle_filled_rounded : Icons.play_circle_filled_rounded, color: Colors.white, size: 44),
                      onPressed: () { setState(() { _controller.value.isPlaying ? _controller.pause() : _controller.play(); }); _startHideTimer(); },
                    ),
                    Text(_format(_controller.value.position), style: const TextStyle(color: Colors.white, fontSize: 12)),
                    Text(' / ${_format(_controller.value.duration)}', style: const TextStyle(color: Colors.white54, fontSize: 12)),
                    const Spacer(),
                    IconButton(icon: const Icon(Icons.replay_10_rounded, color: Colors.white), onPressed: () { _controller.seekTo(_controller.value.position - const Duration(seconds: 10)); _startHideTimer(); }),
                    IconButton(icon: const Icon(Icons.forward_10_rounded, color: Colors.white), onPressed: () { _controller.seekTo(_controller.value.position + const Duration(seconds: 10)); _startHideTimer(); }),
                  ]),
                ]),
              ),
            ),
        ]),
      ),
    );
  }
}