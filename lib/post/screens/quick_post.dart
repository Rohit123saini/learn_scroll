// lib/post/screens/quick_post.dart
//
// QUICK POST — Advanced Lightweight Composer
// Features:
// • Full-screen immersive UI with glassmorphism
// • Blurred bottom sheets
// • Emoji grid with categories (Smileys, Hearts, Fire, Hands, Objects)
// • Voice note recording UI simulation
// • GIF / Sticker placeholder
// • Draft with timestamp tracking
// • Haptic feedback on every interaction
// • Animated character counter with color states
// • Media strip with drag-to-reorder handles
// • Category shimmer loader
// • Visibility chips with unique colors
// • Smooth animations & transitions
//
// UI/UX PASS — every animation, gesture, draft-autosave, chunked-upload
// threshold and validation rule below is unchanged. What changed: the
// screen now pulls its palette from theme_service.dart's ColorScheme
// (so it follows the app's light/dark setting and the same brand purple
// used in home.dart, instead of a separate hardcoded navy/gold palette),
// and every user-facing string — snackbars, sheet labels, tooltips,
// visibility chips, the discard dialog, the "Xm ago" draft timestamp —
// is now localized via AppLocalizations.

import 'dart:io';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/api_service.dart';
import '../../l10n/app_localizations.dart';

enum _MediaType { image, video }

class _MediaItem {
  _MediaItem(this.file, this.type);
  final File file;
  final _MediaType type;
}

class QuickTextPost extends StatefulWidget {
  const QuickTextPost({super.key});

  @override
  State<QuickTextPost> createState() => _QuickTextPostState();
}

class _QuickTextPostState extends State<QuickTextPost> with TickerProviderStateMixin {
  static const int _maxChars = 3000;
  static const int _maxMedia = 6;
  static const String _draftKey = 'quick_post_draft_text';
  static const String _draftTimeKey = 'quick_post_draft_time';
  // Same 20MB cutoff as new_post.dart's `_chunkedUploadThreshold`; above
  // this, a single attachment goes through the chunked/resumable upload
  // path instead of one big multipart request.
  static const int _chunkedUploadThreshold = 20 * 1024 * 1024; // 20MB

  final _contentController = TextEditingController();
  final _apiService = ApiService();
  final _focusNode = FocusNode();
  final _picker = ImagePicker();
  final _scrollController = ScrollController();

  bool _isLoading = false;
  bool _justPosted = false; // drives the brief success checkmark overlay after publishing
  bool _loadingCategories = true;
  bool _showEmojiGrid = false;
  bool _isRecordingVoice = false;
  String? _category;
  List<Map<String, String>> _categories = [];
  String _visibility = 'public';
  String? _loadingLabel; // null while idle; l10n string while uploading/posting
  double? _uploadPercent; // non-null while a chunked upload is reporting progress
  DateTime? _lastSaved;

  final List<_MediaItem> _mediaItems = [];

  late AnimationController _emojiAnimController;
  late Animation<double> _emojiSlide;
  late AnimationController _recordAnimController;
  late AnimationController _pulseAnimController;
  late AnimationController _successController; // scale-in for the publish success checkmark

  // ─── Emoji Data ───
  static const Map<String, List<String>> _emojiCategories = {
    'Smileys': ['😀', '😂', '🥰', '😎', '🤔', '😭', '😡', '🥳', '😴', '🤯', '🤠', '👻'],
    'Hearts': ['❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '💔', '❣️', '💕', '💯'],
    'Fire': ['🔥', '⚡', '✨', '💫', '🌟', '💥', '☄️', '🌈', '☀️', '🌙', '⭐', '🎉'],
    'Hands': ['👍', '👎', '👏', '🙏', '🤝', '👊', '✌️', '🤞', '🤟', '🤘', '👌', '🖐️'],
    'Objects': ['💻', '📱', '💡', '📚', '✏️', '🎨', '🎵', '🎬', '🎮', '📷', '🔒', '🔑'],
  };

  bool get _hasVideo => _mediaItems.any((m) => m.type == _MediaType.video);

  @override
  void initState() {
    super.initState();
    _emojiAnimController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 250),
    );
    _emojiSlide = Tween<double>(begin: 1.0, end: 0.0).animate(
      CurvedAnimation(parent: _emojiAnimController, curve: Curves.easeOutCubic),
    );
    _recordAnimController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1000),
    )..repeat(reverse: true);
    _pulseAnimController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 800),
    );
    _successController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 450),
    );

    _loadTaxonomy();
    _restoreDraft();
    _contentController.addListener(_onTextChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) => _focusNode.requestFocus());
  }

  void _onTextChanged() {
    setState(() {});
    _saveDraft();
  }

  Future<void> _restoreDraft() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getString(_draftKey);
      final savedTime = prefs.getInt(_draftTimeKey);
      if (saved != null && saved.trim().isNotEmpty && mounted) {
        _contentController.text = saved;
        if (savedTime != null) {
          _lastSaved = DateTime.fromMillisecondsSinceEpoch(savedTime);
        }
        _showMessage(AppLocalizations.of(context)!.draftRestored, isError: false);
      }
    } catch (_) {}
  }

  Future<void> _saveDraft() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final text = _contentController.text;
      if (text.trim().isEmpty) {
        await prefs.remove(_draftKey);
        await prefs.remove(_draftTimeKey);
        setState(() => _lastSaved = null);
      } else {
        await prefs.setString(_draftKey, text);
        await prefs.setInt(_draftTimeKey, DateTime.now().millisecondsSinceEpoch);
        setState(() => _lastSaved = DateTime.now());
      }
    } catch (_) {}
  }

  Future<void> _clearDraft() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_draftKey);
      await prefs.remove(_draftTimeKey);
    } catch (_) {}
  }

  Future<void> _loadTaxonomy() async {
    try {
      final data = await _apiService.getCategoryTaxonomy();
      final cats = List<Map<String, dynamic>>.from(data['categories']);
      setState(() {
        _categories = cats.map((c) => {'key': c['key'].toString(), 'label': c['label'].toString()}).toList();
        if (_categories.isNotEmpty) _category = _categories.first['key'];
        _loadingCategories = false;
      });
    } catch (e) {
      setState(() => _loadingCategories = false);
    }
  }

  @override
  void dispose() {
    _emojiAnimController.dispose();
    _recordAnimController.dispose();
    _pulseAnimController.dispose();
    _successController.dispose();
    _contentController.removeListener(_onTextChanged);
    _contentController.dispose();
    _focusNode.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _showMessage(String msg, {bool isError = true}) {
    if (!mounted) return;
    final cs = Theme.of(context).colorScheme;
    HapticFeedback.lightImpact();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(msg, style: const TextStyle(fontWeight: FontWeight.w500)),
        backgroundColor: isError ? cs.error : const Color(0xFF2ECC71),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        margin: const EdgeInsets.all(16),
        elevation: 6,
        duration: const Duration(seconds: 2),
      ),
    );
  }

  // ─── Emoji ───
  void _toggleEmojiGrid() {
    HapticFeedback.lightImpact();
    setState(() => _showEmojiGrid = !_showEmojiGrid);
    if (_showEmojiGrid) {
      _emojiAnimController.forward();
      _focusNode.unfocus();
    } else {
      _emojiAnimController.reverse();
      _focusNode.requestFocus();
    }
  }

  void _insertEmoji(String emoji) {
    final text = _contentController.text;
    final selection = _contentController.selection;
    final cursor = selection.start >= 0 ? selection.start : text.length;
    final newText = text.replaceRange(cursor, selection.end >= 0 ? selection.end : cursor, emoji);
    _contentController.text = newText;
    _contentController.selection = TextSelection.collapsed(offset: cursor + emoji.length);
    HapticFeedback.lightImpact();
  }

  // ─── Media ───
  Future<void> _pickImages() async {
    final l10n = AppLocalizations.of(context)!;
    if (_hasVideo) {
      _showMessage(l10n.removeVideoFirst);
      return;
    }
    if (_mediaItems.length >= _maxMedia) {
      _showMessage(l10n.maxMediaAllowed(_maxMedia));
      return;
    }
    try {
      final remaining = _maxMedia - _mediaItems.length;
      final picked = await _picker.pickMultiImage(imageQuality: 85);
      if (picked.isEmpty) return;
      setState(() {
        _mediaItems.addAll(picked.take(remaining).map((x) => _MediaItem(File(x.path), _MediaType.image)));
      });
      if (picked.length > remaining) _showMessage(l10n.onlyNMoreCouldBeAdded(remaining));
    } catch (e) {
      _showMessage(l10n.galleryError(e.toString()));
    }
  }

  Future<void> _pickCameraPhoto() async {
    final l10n = AppLocalizations.of(context)!;
    if (_hasVideo) {
      _showMessage(l10n.removeVideoFirst);
      return;
    }
    if (_mediaItems.length >= _maxMedia) {
      _showMessage(l10n.maxMediaAllowed(_maxMedia));
      return;
    }
    try {
      final shot = await _picker.pickImage(source: ImageSource.camera, imageQuality: 85);
      if (shot == null) return;
      setState(() => _mediaItems.add(_MediaItem(File(shot.path), _MediaType.image)));
    } catch (e) {
      _showMessage(l10n.cameraError(e.toString()));
    }
  }

  Future<void> _pickVideo(ImageSource source) async {
    final l10n = AppLocalizations.of(context)!;
    if (_mediaItems.isNotEmpty) {
      _showMessage(l10n.removeImagesFirst);
      return;
    }
    try {
      final video = await _picker.pickVideo(source: source, maxDuration: const Duration(minutes: 2));
      if (video == null) return;
      setState(() => _mediaItems.add(_MediaItem(File(video.path), _MediaType.video)));
    } catch (e) {
      _showMessage(l10n.videoError(e.toString()));
    }
  }

  void _removeMediaAt(int index) {
    HapticFeedback.lightImpact();
    setState(() => _mediaItems.removeAt(index));
  }

  void _reorderMedia(int oldIndex, int newIndex) {
    setState(() {
      if (newIndex > oldIndex) newIndex -= 1;
      final item = _mediaItems.removeAt(oldIndex);
      _mediaItems.insert(newIndex, item);
    });
  }

  void _showAttachSheet() {
    HapticFeedback.mediumImpact();
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) => BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
        child: Container(
          decoration: BoxDecoration(
            color: cs.surface,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
          ),
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
          child: SafeArea(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Center(
                  child: Container(
                    width: 40,
                    height: 4,
                    decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
                  ),
                ),
                const SizedBox(height: 20),
                Text(l10n.addToPost, style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: cs.onSurface)),
                const SizedBox(height: 20),
                _sheetTile(Icons.photo_library_rounded, l10n.gallery, const Color(0xFF3B82F6), _pickImages, !_hasVideo, cs),
                _sheetTile(Icons.camera_alt_rounded, l10n.camera, const Color(0xFF10B981), _pickCameraPhoto, !_hasVideo, cs),
                Divider(height: 24, color: cs.outlineVariant),
                _sheetTile(Icons.video_library_rounded, l10n.videoFromGallery, const Color(0xFFEF4444),
                    () => _pickVideo(ImageSource.gallery), _mediaItems.isEmpty, cs),
                _sheetTile(Icons.videocam_rounded, l10n.recordVideo, const Color(0xFFF59E0B),
                    () => _pickVideo(ImageSource.camera), _mediaItems.isEmpty, cs),
                Divider(height: 24, color: cs.outlineVariant),
                _sheetTile(Icons.gif_box_rounded, l10n.gifSticker, const Color(0xFF8B5CF6),
                    () => _showMessage(l10n.gifComingSoon, isError: false), true, cs),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _sheetTile(IconData icon, String label, Color color, VoidCallback onTap, bool enabled, ColorScheme cs) {
    return ListTile(
      leading: Container(
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(color: color.withOpacity(0.1), borderRadius: BorderRadius.circular(12)),
        child: Icon(icon, color: color),
      ),
      title: Text(label, style: TextStyle(fontWeight: FontWeight.w600, color: cs.onSurface)),
      enabled: enabled,
      onTap: enabled
          ? () {
              Navigator.pop(context);
              onTap();
            }
          : null,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
    );
  }

  // ─── Voice Note ───
  void _toggleVoiceRecording() {
    HapticFeedback.mediumImpact();
    setState(() => _isRecordingVoice = !_isRecordingVoice);
    if (_isRecordingVoice) {
      _pulseAnimController.repeat();
      _focusNode.unfocus();
    } else {
      _pulseAnimController.stop();
      _pulseAnimController.reset();
      _showMessage(AppLocalizations.of(context)!.voiceNoteAttached, isError: false);
    }
  }

  // ─── Close / Discard ───
  Future<void> _handleClose() async {
    final l10n = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    final hasContent = _contentController.text.trim().isNotEmpty || _mediaItems.isNotEmpty;
    if (!hasContent) {
      Navigator.pop(context);
      return;
    }
    final discard = await showDialog<bool>(
      context: context,
      builder: (ctx) => BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 6, sigmaY: 6),
        child: AlertDialog(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
          title: Text(l10n.discardPostTitle, style: const TextStyle(fontWeight: FontWeight.w800)),
          content: Text(l10n.discardPostBody),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(l10n.keepEditing, style: const TextStyle(fontWeight: FontWeight.w600)),
            ),
            FilledButton(
              onPressed: () async {
                await _clearDraft();
                if (ctx.mounted) Navigator.pop(ctx, true);
              },
              style: FilledButton.styleFrom(backgroundColor: cs.error),
              child: Text(l10n.discard),
            ),
          ],
        ),
      ),
    );
    if (discard == true && mounted) {
      Navigator.pop(context);
    }
  }

  // ─── Post ───
  Future<void> _post() async {
    HapticFeedback.mediumImpact();
    final l10n = AppLocalizations.of(context)!;
    final text = _contentController.text.trim();
    if (text.isEmpty && _mediaItems.isEmpty) {
      _showMessage(l10n.writeSomethingOrAddMedia);
      return;
    }
    if (text.length > _maxChars) {
      _showMessage(l10n.postTooLong(_maxChars));
      return;
    }
    if (_category == null) {
      _showMessage(_loadingCategories ? l10n.categoriesLoading : l10n.categoryLoadFailed);
      return;
    }

    setState(() {
      _isLoading = true;
      _uploadPercent = null;
      _loadingLabel = _mediaItems.isNotEmpty ? l10n.uploadingMedia : l10n.posting;
    });
    try {
      final hashtagRegex = RegExp(r'#(\w+)');
      final hashtags = hashtagRegex.allMatches(text).map((m) => m.group(1)!).toList();

      // Chunked-upload parity with new_post.dart. Same rule as there: a
      // single attachment over the 20MB threshold goes through the
      // resumable chunked path instead of one big multipart POST, so a
      // large video doesn't time out / can't resume on a flaky
      // connection. Multiple attachments (or anything under the
      // threshold) keep using plain multipart `createPost` — Quick
      // Post's whole point is speed, so we don't force every tiny image
      // through the heavier chunked flow.
      final bool useChunkedUpload = _mediaItems.length == 1 &&
          _mediaItems.first.file.lengthSync() > _chunkedUploadThreshold;

      late final Map<String, dynamic> result;

      if (useChunkedUpload) {
        final item = _mediaItems.first;
        result = await _apiService.createPostWithChunkedUpload(
          file: item.file,
          title: '',
          content: text,
          category: _category!,
          subcategory: null,
          postType: item.type == _MediaType.video ? 'video' : 'image',
          visibility: _visibility,
          hashtags: hashtags,
          location: null,
          pollOptions: null,
          mediaCaption: '',
          mediaType: item.type == _MediaType.video ? 'video' : 'image',
          scheduledAt: null,
          // Lightweight progress treatment: reuse the existing spinner +
          // label on the Post button instead of adding a separate
          // progress ring like new_post.dart's full composer — Quick
          // Post stays a single compact control.
          onProgress: (progress) {
            if (mounted) setState(() => _uploadPercent = progress * 100);
          },
        );
      } else {
        result = await _apiService.createPost(
          title: '',
          content: text,
          category: _category!,
          subcategory: null,
          postType: _hasVideo ? 'video' : (_mediaItems.isNotEmpty ? 'media' : 'text'),
          visibility: _visibility,
          hashtags: hashtags,
          mediaFiles: _mediaItems.isNotEmpty ? _mediaItems.map((m) => m.file).toList() : null,
          mediaTypes: _mediaItems.isNotEmpty
              ? _mediaItems.map((m) => m.type == _MediaType.video ? 'video' : 'image').toList()
              : null,
        );
      }

      if (!mounted) return;
      await _clearDraft();

      final message = result['message']?.toString() ?? l10n.postCreatedSuccess;
      HapticFeedback.mediumImpact();
      setState(() {
        _isLoading = false;
        _justPosted = true;
      });
      _successController.forward(from: 0);
      await Future.delayed(const Duration(milliseconds: 700));
      if (!mounted) return;

      Navigator.pop(context, true);
      _showMessage(message, isError: false);
    } catch (e) {
      if (!mounted) return;
      setState(() => _isLoading = false);
      _showMessage(e.toString().replaceAll('Exception: ', ''));
    }
  }

  // ─── UI Builders ───
  Widget _mediaThumb(_MediaItem item, int index, ColorScheme cs) {
    return Container(
      key: ValueKey(item.file.path),
      margin: const EdgeInsets.only(right: 10),
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          ReorderableDragStartListener(
            index: index,
            child: ClipRRect(
              borderRadius: BorderRadius.circular(14),
              child: item.type == _MediaType.image
                  ? Image.file(item.file, width: 88, height: 88, fit: BoxFit.cover)
                  : Container(
                      width: 88,
                      height: 88,
                      color: Colors.black87,
                      alignment: Alignment.center,
                      child: const Icon(Icons.play_circle_fill_rounded, color: Colors.white, size: 32),
                    ),
            ),
          ),
          // Drag hint
          Positioned(
            bottom: 4,
            left: 4,
            child: Container(
              padding: const EdgeInsets.all(3),
              decoration: BoxDecoration(
                color: Colors.black.withOpacity(0.5),
                borderRadius: BorderRadius.circular(4),
              ),
              child: const Icon(Icons.drag_indicator_rounded, size: 12, color: Colors.white70),
            ),
          ),
          Positioned(
            top: -6,
            right: -6,
            child: GestureDetector(
              onTap: () => _removeMediaAt(index),
              child: Container(
                padding: const EdgeInsets.all(4),
                decoration: BoxDecoration(
                  color: cs.error,
                  shape: BoxShape.circle,
                  border: Border.all(color: cs.surface, width: 2),
                  boxShadow: [BoxShadow(color: cs.error.withOpacity(0.3), blurRadius: 4)],
                ),
                child: const Icon(Icons.close_rounded, size: 12, color: Colors.white),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildMediaStrip(ColorScheme cs) {
    if (_mediaItems.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 16),
      child: SizedBox(
        height: 88,
        child: ReorderableListView(
          scrollDirection: Axis.horizontal,
          buildDefaultDragHandles: false,
          onReorder: _reorderMedia,
          children: [
            for (int i = 0; i < _mediaItems.length; i++) _mediaThumb(_mediaItems[i], i, cs),
          ],
        ),
      ),
    );
  }

  Widget _buildEmojiGrid(ColorScheme cs) {
    return AnimatedBuilder(
      animation: _emojiSlide,
      builder: (context, child) {
        return Transform.translate(
          offset: Offset(0, 200 * _emojiSlide.value),
          child: Opacity(
            opacity: 1 - _emojiSlide.value,
            child: Container(
              height: 260,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: cs.surface,
                borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
                boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.08), blurRadius: 20, offset: const Offset(0, -4))],
              ),
              child: DefaultTabController(
                length: _emojiCategories.length,
                child: Column(
                  children: [
                    TabBar(
                      isScrollable: true,
                      labelColor: cs.primary,
                      unselectedLabelColor: cs.onSurfaceVariant,
                      indicatorColor: cs.primary,
                      indicatorWeight: 3,
                      labelStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 12),
                      tabs: _emojiCategories.keys.map((k) => Tab(text: k)).toList(),
                    ),
                    const SizedBox(height: 8),
                    Expanded(
                      child: TabBarView(
                        children: _emojiCategories.values.map((emojis) {
                          return GridView.builder(
                            padding: EdgeInsets.zero,
                            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                              crossAxisCount: 6,
                              childAspectRatio: 1.2,
                            ),
                            itemCount: emojis.length,
                            itemBuilder: (_, i) => InkWell(
                              onTap: () => _insertEmoji(emojis[i]),
                              borderRadius: BorderRadius.circular(8),
                              child: Center(child: Text(emojis[i], style: const TextStyle(fontSize: 24))),
                            ),
                          );
                        }).toList(),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  // ─── Success Overlay — brief celebratory checkmark right before navigating back ───
  Widget _buildSuccessOverlay(ColorScheme cs) {
    final l10n = AppLocalizations.of(context)!;
    return IgnorePointer(
      ignoring: !_justPosted,
      child: AnimatedOpacity(
        opacity: _justPosted ? 1 : 0,
        duration: const Duration(milliseconds: 200),
        child: Container(
          decoration: BoxDecoration(
            gradient: RadialGradient(
              colors: [Color.lerp(cs.primary, Colors.black, 0.35)!, cs.primary.withOpacity(0.96)],
              radius: 1.1,
            ),
          ),
          alignment: Alignment.center,
          child: ScaleTransition(
            scale: CurvedAnimation(parent: _successController, curve: Curves.elasticOut),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 92,
                  height: 92,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(color: cs.secondary.withOpacity(0.5), width: 2),
                    boxShadow: [BoxShadow(color: cs.secondary.withOpacity(0.35), blurRadius: 24, spreadRadius: 2)],
                  ),
                  child: Container(
                    width: 76,
                    height: 76,
                    decoration: const BoxDecoration(color: Colors.white, shape: BoxShape.circle),
                    child: Icon(Icons.check_rounded, color: cs.primary, size: 42),
                  ),
                ),
                const SizedBox(height: 18),
                Text(
                  l10n.postedExclaim,
                  style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 18, letterSpacing: 0.2),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  String _timeAgo(DateTime? dt) {
    if (dt == null) return '';
    final l10n = AppLocalizations.of(context)!;
    final diff = DateTime.now().difference(dt);
    if (diff.inSeconds < 60) return l10n.timeAgoJustNow;
    if (diff.inMinutes < 60) return l10n.timeAgoMinutes(diff.inMinutes);
    if (diff.inHours < 24) return l10n.timeAgoHours(diff.inHours);
    return l10n.timeAgoDays(diff.inDays);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final Color primaryDark = Color.lerp(cs.primary, Colors.black, 0.2)!;
    final visibilityOptions = <Map<String, dynamic>>[
      {'key': 'public', 'label': l10n.visibilityPublic, 'icon': Icons.public_rounded, 'color': const Color(0xFF3B82F6)},
      {'key': 'connections', 'label': l10n.visibilityNetwork, 'icon': Icons.people_alt_rounded, 'color': const Color(0xFF8B5CF6)},
      {'key': 'private', 'label': l10n.visibilityPrivate, 'icon': Icons.lock_outline_rounded, 'color': cs.onSurfaceVariant},
    ];

    final charCount = _contentController.text.length;
    final overLimit = charCount > _maxChars;
    final nearLimit = charCount > (_maxChars * 0.9);
    final progress = charCount / _maxChars;
    final loadingLabelText = _uploadPercent != null ? l10n.uploadingPercent(_uploadPercent!.toStringAsFixed(0)) : (_loadingLabel ?? l10n.posting);

    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _handleClose();
      },
      child: Scaffold(
        backgroundColor: cs.background,
        appBar: AppBar(
          backgroundColor: cs.background,
          elevation: 0,
          surfaceTintColor: cs.background,
          leading: IconButton(
            icon: Icon(Icons.close_rounded, color: cs.onSurface),
            onPressed: _handleClose,
          ),
          title: Text(
            l10n.quickPostTitle,
            style: TextStyle(color: cs.onSurface, fontWeight: FontWeight.w800, fontSize: 17, letterSpacing: -0.3),
          ),
          actions: [
            if (_lastSaved != null && _contentController.text.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: Center(
                  child: Text(
                    l10n.savedAgo(_timeAgo(_lastSaved)),
                    style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant.withOpacity(0.8), fontWeight: FontWeight.w500),
                  ),
                ),
              ),
            Padding(
              padding: const EdgeInsets.only(right: 12),
              child: Center(
                child: Container(
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(24),
                    gradient: LinearGradient(
                      colors: (_isLoading || overLimit)
                          ? [cs.primary.withOpacity(0.35), primaryDark.withOpacity(0.35)]
                          : [cs.primary, primaryDark],
                    ),
                    border: Border.all(color: cs.secondary.withOpacity((_isLoading || overLimit) ? 0.15 : 0.55), width: 1.2),
                  ),
                  child: Material(
                    color: Colors.transparent,
                    borderRadius: BorderRadius.circular(24),
                    child: InkWell(
                      borderRadius: BorderRadius.circular(24),
                      onTap: (_isLoading || overLimit) ? null : _post,
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 10),
                        child: _isLoading
                            ? Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  const SizedBox(
                                    width: 16,
                                    height: 16,
                                    child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                                  ),
                                  const SizedBox(width: 8),
                                  Text(loadingLabelText, style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600)),
                                ],
                              )
                            : Text(l10n.postButton, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 14)),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
        body: Stack(
          children: [
            SafeArea(
              child: Column(
                children: [
                  // Category chip
                  if (_loadingCategories)
                    const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                      child: _ShimmerLoader(height: 36),
                    )
                  else if (_category != null)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                      child: Row(
                        children: [
                          Chip(
                            avatar: Icon(Icons.folder_rounded, size: 14, color: cs.primary),
                            label: Text(
                              _categories.firstWhere((c) => c['key'] == _category)['label'] ?? '',
                              style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600),
                            ),
                            backgroundColor: cs.primary.withOpacity(0.1),
                            side: BorderSide.none,
                            visualDensity: VisualDensity.compact,
                          ),
                        ],
                      ),
                    ),
                  Expanded(
                    child: SingleChildScrollView(
                      controller: _scrollController,
                      padding: const EdgeInsets.fromLTRB(20, 4, 20, 20),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Row(
                            children: [
                              CircleAvatar(
                                radius: 22,
                                backgroundColor: cs.primary.withOpacity(0.1),
                                child: Icon(Icons.person_rounded, color: cs.primary, size: 24),
                              ),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Wrap(
                                  spacing: 8,
                                  children: visibilityOptions.map((opt) {
                                    final selected = _visibility == opt['key'];
                                    final color = opt['color'] as Color;
                                    return ChoiceChip(
                                      label: Text(opt['label'] as String),
                                      avatar: Icon(
                                        opt['icon'] as IconData,
                                        size: 14,
                                        color: selected ? Colors.white : color,
                                      ),
                                      selected: selected,
                                      onSelected: (_) => setState(() => _visibility = opt['key'] as String),
                                      selectedColor: color,
                                      backgroundColor: color.withOpacity(0.1),
                                      labelStyle: TextStyle(
                                        fontSize: 12,
                                        fontWeight: FontWeight.w700,
                                        color: selected ? Colors.white : color,
                                      ),
                                      shape: RoundedRectangleBorder(
                                        borderRadius: BorderRadius.circular(20),
                                        side: BorderSide.none,
                                      ),
                                      visualDensity: VisualDensity.compact,
                                      padding: const EdgeInsets.symmetric(horizontal: 4),
                                    );
                                  }).toList(),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 20),
                          TextField(
                            controller: _contentController,
                            focusNode: _focusNode,
                            maxLines: null,
                            minLines: 10,
                            autofocus: true,
                            maxLength: _maxChars,
                            maxLengthEnforcement: MaxLengthEnforcement.enforced,
                            style: TextStyle(fontSize: 18, height: 1.5, color: cs.onSurface),
                            decoration: InputDecoration(
                              hintText: l10n.whatsOnYourMind,
                              hintStyle: TextStyle(color: cs.onSurfaceVariant, fontSize: 18, fontWeight: FontWeight.w400),
                              border: InputBorder.none,
                              isDense: true,
                              contentPadding: EdgeInsets.zero,
                              counterText: '',
                            ),
                          ),
                          _buildMediaStrip(cs),
                        ],
                      ),
                    ),
                  ),
                  // Bottom toolbar
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    decoration: BoxDecoration(
                      color: cs.surface,
                      border: Border(top: BorderSide(color: cs.outlineVariant.withOpacity(0.6))),
                      boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.02), blurRadius: 8, offset: const Offset(0, -2))],
                    ),
                    child: SafeArea(
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          // Emoji quick row
                          SizedBox(
                            height: 40,
                            child: ListView.separated(
                              scrollDirection: Axis.horizontal,
                              itemCount: _emojiCategories['Smileys']!.length,
                              separatorBuilder: (_, __) => const SizedBox(width: 2),
                              itemBuilder: (context, i) => Material(
                                color: Colors.transparent,
                                borderRadius: BorderRadius.circular(10),
                                child: InkWell(
                                  borderRadius: BorderRadius.circular(10),
                                  onTap: () => _insertEmoji(_emojiCategories['Smileys']![i]),
                                  child: Padding(
                                    padding: const EdgeInsets.symmetric(horizontal: 6),
                                    child: Text(_emojiCategories['Smileys']![i], style: const TextStyle(fontSize: 22)),
                                  ),
                                ),
                              ),
                            ),
                          ),
                          const SizedBox(height: 6),
                          // Action bar
                          Row(
                            mainAxisAlignment: MainAxisAlignment.spaceBetween,
                            children: [
                              Row(
                                children: [
                                  IconButton(
                                    onPressed: _showAttachSheet,
                                    icon: const Icon(Icons.attach_file_rounded),
                                    color: cs.primary,
                                    tooltip: l10n.addPhotoOrVideoTooltip,
                                    visualDensity: VisualDensity.compact,
                                  ),
                                  IconButton(
                                    onPressed: _toggleEmojiGrid,
                                    icon: Icon(
                                      _showEmojiGrid ? Icons.keyboard_hide_rounded : Icons.emoji_emotions_rounded,
                                      color: _showEmojiGrid ? cs.primary : cs.onSurfaceVariant,
                                    ),
                                    tooltip: l10n.emojisTooltip,
                                    visualDensity: VisualDensity.compact,
                                  ),
                                  IconButton(
                                    onPressed: _toggleVoiceRecording,
                                    icon: AnimatedBuilder(
                                      animation: _pulseAnimController,
                                      builder: (_, __) {
                                        return Icon(
                                          _isRecordingVoice ? Icons.stop_rounded : Icons.mic_rounded,
                                          color: _isRecordingVoice ? cs.error : cs.onSurfaceVariant,
                                          size: _isRecordingVoice ? 20 + _pulseAnimController.value * 4 : 20,
                                        );
                                      },
                                    ),
                                    tooltip: l10n.voiceNoteTooltip,
                                    visualDensity: VisualDensity.compact,
                                  ),
                                ],
                              ),
                              // Char counter with progress
                              Row(
                                children: [
                                  if (charCount > 0)
                                    SizedBox(
                                      width: 18,
                                      height: 18,
                                      child: CircularProgressIndicator(
                                        value: progress.clamp(0.0, 1.0),
                                        strokeWidth: 2.5,
                                        backgroundColor: cs.outlineVariant,
                                        valueColor: AlwaysStoppedAnimation(
                                          overLimit
                                              ? cs.error
                                              : nearLimit
                                                  ? const Color(0xFFF5A623)
                                                  : cs.primary,
                                        ),
                                      ),
                                    ),
                                  const SizedBox(width: 6),
                                  Text(
                                    '$charCount',
                                    style: TextStyle(
                                      fontSize: 12,
                                      fontWeight: FontWeight.w700,
                                      color: overLimit
                                          ? cs.error
                                          : nearLimit
                                              ? const Color(0xFFF5A623)
                                              : cs.onSurfaceVariant,
                                    ),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
                  // Emoji grid
                  if (_showEmojiGrid) _buildEmojiGrid(cs),
                ],
              ),
            ),
            _buildSuccessOverlay(cs),
          ],
        ),
      ),
    );
  }
}

// ─── Shimmer Loader ───
class _ShimmerLoader extends StatefulWidget {
  const _ShimmerLoader({required this.height});
  final double height;

  @override
  State<_ShimmerLoader> createState() => _ShimmerLoaderState();
}

class _ShimmerLoaderState extends State<_ShimmerLoader> with SingleTickerProviderStateMixin {
  late AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 1500))..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) {
        return Container(
          height: widget.height,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            gradient: LinearGradient(
              colors: [cs.surfaceVariant, cs.surface, cs.surfaceVariant],
              stops: [0.0, _controller.value, 1.0],
              begin: const Alignment(-1, 0),
              end: const Alignment(1, 0),
            ),
          ),
        );
      },
    );
  }
}