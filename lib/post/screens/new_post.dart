import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:file_selector/file_selector.dart';
import 'package:intl/intl.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../api_service.dart';
import 'quick_post.dart';
import 'media_edit_screen.dart';

/// ═══════════════════════════════════════════════════════════════════
/// NEW POST — Advanced Full Composer
/// Features:
/// • Glassmorphism AppBar & Cards
/// • Hero Animation Banner
/// • Poll Creation (up to 4 options)
/// • Location Tagging
/// • Rich Text Toolbar (Bold / Italic)
/// • Media Captions
/// • One-tap Clear All (resets the whole composer + cached draft)
/// • Shimmer Loaders
/// • Haptic Feedback
/// • Animated Floating Publish Button
/// • Professional Color Palette
/// • File Type Color Coding
/// • Auto-save Drafts (local persistence via SharedPreferences)
/// • Schedule Post with DateTime Picker
/// • Category → Subcategory cascading
/// • Drag-to-reorder media hints
/// ═══════════════════════════════════════════════════════════════════
class NewPost extends StatefulWidget {
  const NewPost({super.key});

  @override
  State<NewPost> createState() => _NewPostState();
}

class _NewPostState extends State<NewPost> with TickerProviderStateMixin {
  // ─── Design Tokens (matched to ProfileScreen's navy brand identity) ───
  static const Color _navy = Color(0xFF0B142B); // heading text — deep navy, not flat black
  static const Color _primary = Color(0xFF030F27); // same navy as ProfileScreen's bgColor — brand anchor
  static const Color _primaryDark = Color(0xFF010914); // pressed/depth state for the primary
  static const Color _primarySoft = Color(0xFFE9EBF3); // soft navy tint for chips/icon badges
  static const Color _accent = Color(0xFFC9A24B); // warm gold — small premium highlight, used sparingly
  static const Color _bg = Colors.white;
  static const Color _surface = Colors.white;
  static const Color _border = Color(0xFFE1E4EC); // soft cool-gray hairline, matches navy undertone
  static const Color _muted = Color(0xFF6B7280); // secondary text
  static const Color _success = Color(0xFF2ECC71);
  static const Color _warning = Color(0xFFF5A623);
  static const Color _error = Color(0xFFED4956);

  // ─── Controllers & Keys ───
  final _formKey = GlobalKey<FormState>();
  final _titleController = TextEditingController();
  final _contentController = TextEditingController();
  final _scrollController = ScrollController();
  final _apiService = ApiService();
  final _picker = ImagePicker();

  // ─── State ───
  bool _isLoading = false;
  // NEW — chunked upload progress (0.0 -> 1.0), only set when a
  // large file is being sent in chunks. null means normal (non-chunked) flow.
  double? _chunkUploadProgress;
  // Attachments larger than this use chunked upload, otherwise normal multipart.
  static const int _chunkedUploadThreshold = 20 * 1024 * 1024; // 20MB
  bool _justPosted = false; // drives the brief success checkmark overlay after publishing
  bool _loadingCategories = true;
  bool _isBold = false;
  bool _isItalic = false;
  String? _category;
  String? _subcategory;
  bool _categoryError = false; // true after a failed submit attempt with no category picked
  final _detailsCardKey = GlobalKey();
  String _postType = 'text';
  String _visibility = 'public';
  String? _location;
  DateTime? _scheduledDateTime;

  // ─── Draft Auto-save ───
  static const String _draftPrefsKey = 'new_post_draft_v1';
  static const Duration _autoSaveDebounce = Duration(seconds: 1);
  Timer? _autoSaveTimer;
  DateTime? _lastSavedAt;
  bool _restoringDraft = false;

  // ─── @mentions / #hashtags autocomplete ───
  String? _activeTokenType; // '@' or '#' when a token is being typed, else null
  int _tokenStartIndex = -1;
  List<String> _suggestionResults = [];
  // NOTE: demo/local suggestion sources. Swap these for real calls, e.g.
  // `await _apiService.searchUsers(query)` / `_apiService.searchHashtags(query)`,
  // once those endpoints exist — same pattern as `_locationSuggestions` below.
  static const List<String> _demoUsers = [
    'aarav.dev', 'priya_singh', 'rohan.codes', 'neha.writes', 'kunal_photo',
    'ishita.designs', 'vikram_travels', 'ananya.fit', 'devansh.tech', 'meera_art',
  ];
  static const List<String> _trendingHashtags = [
    'flutter', 'flutterdev', 'coding', 'techindia', 'startup', 'motivation',
    'photography', 'travel', 'foodie', 'fitness', 'design', 'opensource',
  ];

  final List<_MediaAttachment> _attachments = [];
  final List<_PollOption> _pollOptions = [];
  final List<Map<String, String>> _categories = [];
  final List<Map<String, String>> _allSubcategories = [];
  Map<String, List<String>> _categorySubcategoryMap = {};

  late AnimationController _fabAnimController;
  late Animation<double> _fabScale;
  late AnimationController _entryController; // drives the one-time staggered entrance of the composer sections
  late AnimationController _successController; // scale-in for the publish success checkmark

  // ─── Constants ───
  static const int _maxAttachments = 10;
  static const int _maxPollOptions = 4;
  static const List<String> _locationSuggestions = [
    'Mumbai, India',
    'Delhi, India',
    'Bangalore, India',
    'Hyderabad, India',
    'Chennai, India',
    'Pune, India',
    'Remote / Work from Home',
  ];

  final List<Map<String, dynamic>> _visibilityOptions = const [
    {'key': 'public', 'label': 'Public', 'icon': Icons.public_rounded, 'desc': 'Anyone can see'},
    {'key': 'connections', 'label': 'Connections', 'icon': Icons.people_alt_rounded, 'desc': 'Only your network'},
    {'key': 'private', 'label': 'Only me', 'icon': Icons.lock_outline_rounded, 'desc': 'Just you'},
  ];

  // ─── Getters ───
  List<Map<String, String>> get _subcategoriesForSelectedCategory {
    if (_category == null) return [];
    final validKeys = _categorySubcategoryMap[_category] ?? [];
    return _allSubcategories.where((s) => validKeys.contains(s['key'])).toList();
  }

  String get _visibilityDescription {
    for (final o in _visibilityOptions) {
      if (o['key'] == _visibility) return o['desc'] as String;
    }
    return '';
  }

  String _visibilityLabelFor(String key) {
    for (final o in _visibilityOptions) {
      if (o['key'] == key) return o['label'] as String;
    }
    return 'Public';
  }

  IconData _visibilityIconFor(String key) {
    for (final o in _visibilityOptions) {
      if (o['key'] == key) return o['icon'] as IconData;
    }
    return Icons.public_rounded;
  }

  bool get _hasPoll => _pollOptions.isNotEmpty;
  bool get _canPost =>
      _titleController.text.trim().isNotEmpty ||
      _contentController.text.trim().isNotEmpty ||
      _attachments.isNotEmpty ||
      _hasPoll;

  // ─── Lifecycle ───
  @override
  void initState() {
    super.initState();
    _fabAnimController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 300),
    );
    _fabScale = CurvedAnimation(
      parent: _fabAnimController,
      curve: Curves.easeOutBack,
    );
    _entryController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 700),
    )..forward();
    _successController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 450),
    );
    _loadTaxonomy();
    _contentController.addListener(_onContentChanged);
    _contentController.addListener(_detectMentionOrHashtag);
    _titleController.addListener(_onContentChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) => _checkForSavedDraft());
  }

  void _onContentChanged() {
    setState(() {});
    if (_canPost && _fabAnimController.status != AnimationStatus.completed) {
      _fabAnimController.forward();
    } else if (!_canPost && _fabAnimController.status == AnimationStatus.completed) {
      _fabAnimController.reverse();
    }
    _scheduleAutoSave();
  }

  @override
  void dispose() {
    _autoSaveTimer?.cancel();
    _fabAnimController.dispose();
    _entryController.dispose();
    _successController.dispose();
    _titleController.dispose();
    _contentController.dispose();
    _scrollController.dispose();
    for (final o in _pollOptions) {
      o.controller.dispose();
    }
    super.dispose();
  }

  // ─── Data Loading ───
  Future<void> _loadTaxonomy() async {
    try {
      final data = await _apiService.getCategoryTaxonomy();
      final cats = List<Map<String, dynamic>>.from(data['categories']);
      final subs = List<Map<String, dynamic>>.from(data['subcategories']);
      final map = Map<String, dynamic>.from(data['category_subcategory_map']);

      setState(() {
        _categories.addAll(cats.map((c) => {
              'key': c['key'].toString(),
              'label': c['label'].toString(),
            }));
        _allSubcategories.addAll(subs.map((s) => {
              'key': s['key'].toString(),
              'label': s['label'].toString(),
            }));
        _categorySubcategoryMap = map.map((k, v) => MapEntry(k, List<String>.from(v)));
        if (_categories.isNotEmpty) _category = _categories.first['key'];
        _loadingCategories = false;
      });
    } catch (e) {
      _showError('Failed to load categories: $e');
      setState(() => _loadingCategories = false);
    }
  }

  // ─── Media Handling ───
  // Photos & Videos now come straight from the native gallery/photo picker
  // (image_picker) instead of the generic file browser — matches how
  // Instagram/WhatsApp let you pick media, and skips the extra "browse
  // files" step entirely.
  Future<void> _pickImages() async {
    if (_attachments.length >= _maxAttachments) {
      _showError('Max $_maxAttachments files allowed');
      return;
    }
    try {
      final files = await _picker.pickMultiImage(imageQuality: 90);
      if (files.isNotEmpty) _addMediaFiles(files, 'image');
    } catch (e) {
      _showError('Could not select images from gallery: $e');
    }
  }

  Future<void> _pickVideos() async {
    if (_attachments.length >= _maxAttachments) {
      _showError('Max $_maxAttachments files allowed');
      return;
    }
    try {
      // Native gallery pickers only support one video at a time — user can
      // tap "Videos" again to add more, same as most social apps.
      final video = await _picker.pickVideo(source: ImageSource.gallery);
      if (video != null) _addMediaFiles([video], 'video');
    } catch (e) {
      _showError('Could not select video from gallery: $e');
    }
  }

  Future<void> _pickDocuments() async {
    if (_attachments.length >= _maxAttachments) {
      _showError('Max $_maxAttachments files allowed');
      return;
    }
    try {
      const typeGroup = XTypeGroup(
        label: 'documents',
        extensions: ['pdf', 'doc', 'docx', 'txt', 'xls', 'xlsx', 'ppt', 'pptx', 'zip', 'rar'],
      );
      final files = await openFiles(acceptedTypeGroups: [typeGroup]);
      _addMediaFiles(files, 'document');
    } catch (e) {
      _showError('Could not select documents: $e');
    }
  }

  // Tapping "Camera" now opens a tiny chooser — Take Photo or Record Video —
  // so a direct-from-camera video clip is one tap away, not buried anywhere.
  void _showCameraOptions() {
    if (_attachments.length >= _maxAttachments) {
      _showError('Max $_maxAttachments files allowed');
      return;
    }
    HapticFeedback.mediumImpact();
    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (ctx) => BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 8, sigmaY: 8),
        child: Container(
          decoration: const BoxDecoration(
            color: _surface,
            borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
          ),
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(color: _border, borderRadius: BorderRadius.circular(2)),
                ),
              ),
              const SizedBox(height: 18),
              const Text('Use Camera', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: _navy)),
              const SizedBox(height: 12),
              ListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 4),
                leading: Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: const Color(0xFF10B981).withOpacity(0.12),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: const Icon(Icons.camera_alt_rounded, color: Color(0xFF10B981), size: 20),
                ),
                title: const Text('Take Photo', style: TextStyle(fontSize: 14.5, fontWeight: FontWeight.w700, color: _navy)),
                subtitle: const Text('Capture a photo right now', style: TextStyle(fontSize: 11.5, color: _muted)),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                onTap: () {
                  Navigator.pop(ctx);
                  _pickCameraPhoto();
                },
              ),
              ListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 4),
                leading: Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: const Color(0xFFEF4444).withOpacity(0.12),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: const Icon(Icons.videocam_rounded, color: Color(0xFFEF4444), size: 20),
                ),
                title: const Text('Record Video', style: TextStyle(fontSize: 14.5, fontWeight: FontWeight.w700, color: _navy)),
                subtitle: const Text('Shoot a quick video clip', style: TextStyle(fontSize: 11.5, color: _muted)),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                onTap: () {
                  Navigator.pop(ctx);
                  _pickCameraVideo();
                },
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _pickCameraPhoto() async {
    if (_attachments.length >= _maxAttachments) {
      _showError('Max $_maxAttachments files allowed');
      return;
    }
    try {
      final shot = await _picker.pickImage(source: ImageSource.camera, imageQuality: 90);
      if (shot != null) {
        setState(() {
          _attachments.add(_MediaAttachment(File(shot.path), 'image'));
          _updatePostType();
        });
      }
    } catch (e) {
      _showError('Camera error: $e');
    }
  }

  Future<void> _pickCameraVideo() async {
    if (_attachments.length >= _maxAttachments) {
      _showError('Max $_maxAttachments files allowed');
      return;
    }
    try {
      final clip = await _picker.pickVideo(
        source: ImageSource.camera,
        maxDuration: const Duration(minutes: 5),
      );
      if (clip != null) {
        setState(() {
          _attachments.add(_MediaAttachment(File(clip.path), 'video'));
          _updatePostType();
        });
      }
    } catch (e) {
      _showError('Camera error: $e');
    }
  }

  void _addMediaFiles(List<XFile> files, String type) {
    final remaining = _maxAttachments - _attachments.length;
    final toAdd = files.take(remaining).toList();
    setState(() {
      for (final f in toAdd) {
        _attachments.add(_MediaAttachment(File(f.path), type));
      }
      _updatePostType();
    });
    if (files.length > remaining) {
      _showError('Only $remaining more file(s) could be added');
    }
  }

  void _updatePostType() {
    if (_attachments.isEmpty) {
      _postType = 'text';
    } else if (_attachments.every((a) => a.type == 'image')) {
      _postType = 'image';
    } else if (_attachments.every((a) => a.type == 'video')) {
      _postType = 'video';
    } else if (_attachments.every((a) => a.type == 'document')) {
      _postType = 'document';
    } else {
      _postType = 'mixed';
    }
  }

  void _removeAttachment(int index) {
    HapticFeedback.lightImpact();
    final att = _attachments[index];
    setState(() => att.removing = true);
    Future.delayed(const Duration(milliseconds: 160), () {
      if (!mounted) return;
      setState(() {
        _attachments.remove(att);
        _updatePostType();
      });
      final restoreIndex = index.clamp(0, _attachments.length);
      _showUndoSnack('Attachment removed', () {
        if (!mounted) return;
        setState(() {
          att.removing = false;
          _attachments.insert(restoreIndex, att);
          _updatePostType();
        });
      });
    });
  }

  void _updateAttachmentCaption(int index, String caption) {
    setState(() => _attachments[index].caption = caption);
    _scheduleAutoSave();
  }

  Future<void> _editCaption(int index) async {
    HapticFeedback.selectionClick();
    final controller = TextEditingController(text: _attachments[index].caption);
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Caption', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLength: 120,
          maxLines: 2,
          decoration: InputDecoration(
            hintText: 'Write something about this media...',
            border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: const BorderSide(color: _primary, width: 1.5),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel', style: TextStyle(color: _muted, fontWeight: FontWeight.w700)),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('Save', style: TextStyle(color: _primary, fontWeight: FontWeight.w700)),
          ),
        ],
      ),
    );
    if (result != null) _updateAttachmentCaption(index, result);
  }

  // Insta-style edit: crop/rotate + filters + brightness/contrast/saturation.
  // Available for both images and videos now — MediaEditScreen receives the
  // raw file either way; if it doesn't yet have video-specific tools, it
  // still opens so trimming/adjust support can be added there later.
  Future<void> _editAttachment(int index) async {
    final att = _attachments[index];
    if (att.type != 'image' && att.type != 'video') return;
    HapticFeedback.lightImpact();
    final edited = await Navigator.push<File>(
      context,
      MaterialPageRoute(builder: (_) => MediaEditScreen(file: att.file)),
    );
    if (edited == null || !mounted) return;
    setState(() {
      _attachments[index] = _MediaAttachment(edited, att.type, caption: att.caption);
    });
  }

  // ─── Poll ───
  void _addPoll() {
    HapticFeedback.mediumImpact();
    if (_pollOptions.length >= _maxPollOptions) return;
    setState(() => _pollOptions.add(_PollOption(TextEditingController())));
    _scheduleAutoSave();
    _scrollToBottom();
  }

  void _removePoll() {
    HapticFeedback.lightImpact();
    setState(() {
      for (final o in _pollOptions) o.controller.dispose();
      _pollOptions.clear();
    });
  }

  void _removePollOption(int index) {
    HapticFeedback.lightImpact();
    final removedText = _pollOptions[index].controller.text;
    setState(() {
      _pollOptions[index].controller.dispose();
      _pollOptions.removeAt(index);
    });
    final restoreIndex = index.clamp(0, _pollOptions.length);
    _showUndoSnack('Poll option removed', () {
      if (!mounted || _pollOptions.length >= _maxPollOptions) return;
      setState(() {
        _pollOptions.insert(restoreIndex, _PollOption(TextEditingController(text: removedText)));
      });
    });
  }

  // ─── Schedule ───
  // Smart quick-pick suggestions — common scheduling moments, computed relative to now.
  List<Map<String, dynamic>> get _scheduleQuickPicks {
    final now = DateTime.now();
    DateTime atHour(DateTime base, int hour) => DateTime(base.year, base.month, base.day, hour);
    final tonight = atHour(now, 19);
    final tomorrowMorning = atHour(now.add(const Duration(days: 1)), 9);
    final tomorrowEvening = atHour(now.add(const Duration(days: 1)), 19);
    final picks = <Map<String, dynamic>>[
      {'label': 'In 1 hour', 'time': now.add(const Duration(hours: 1))},
      if (tonight.isAfter(now.add(const Duration(minutes: 30))))
        {'label': 'This evening', 'time': tonight},
      {'label': 'Tomorrow morning', 'time': tomorrowMorning},
      {'label': 'Tomorrow evening', 'time': tomorrowEvening},
    ];
    return picks;
  }

  void _applyQuickSchedule(DateTime time) {
    HapticFeedback.selectionClick();
    setState(() => _scheduledDateTime = time);
    _scheduleAutoSave();
  }

  Future<void> _pickScheduleDateTime() async {
    final now = DateTime.now();
    final initial = _scheduledDateTime ?? now.add(const Duration(hours: 1));

    final date = await showDatePicker(
      context: context,
      initialDate: initial.isBefore(now) ? now : initial,
      firstDate: now,
      lastDate: now.add(const Duration(days: 365)),
      builder: (context, child) => Theme(
        data: Theme.of(context).copyWith(
          colorScheme: Theme.of(context).colorScheme.copyWith(primary: _primary),
        ),
        child: child!,
      ),
    );
    if (date == null || !mounted) return;

    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(initial),
      builder: (context, child) => Theme(
        data: Theme.of(context).copyWith(
          colorScheme: Theme.of(context).colorScheme.copyWith(primary: _primary),
        ),
        child: child!,
      ),
    );
    if (time == null || !mounted) return;

    final combined = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    if (combined.isBefore(now)) {
      _showError('Select a future time');
      return;
    }
    HapticFeedback.mediumImpact();
    setState(() => _scheduledDateTime = combined);
    _scheduleAutoSave();
  }

  String _formatSchedule(DateTime dt) {
    return DateFormat("MMM d, yyyy 'at' h:mm a").format(dt);
  }

  // ─── Location ───
  void _showLocationPicker() {
    HapticFeedback.mediumImpact();
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) => BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 8, sigmaY: 8),
        child: Container(
          decoration: const BoxDecoration(
            color: _surface,
            borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
          ),
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(color: _border, borderRadius: BorderRadius.circular(2)),
                ),
              ),
              const SizedBox(height: 20),
              const Text('Add Location', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
              const SizedBox(height: 16),
              ..._locationSuggestions.map((loc) => ListTile(
                    leading: const Icon(Icons.location_on_rounded, color: _primary),
                    title: Text(loc, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500)),
                    trailing: _location == loc ? const Icon(Icons.check_rounded, color: _success) : null,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                    onTap: () {
                      HapticFeedback.selectionClick();
                      setState(() => _location = loc);
                      _scheduleAutoSave();
                      Navigator.pop(ctx);
                    },
                  )),
              ListTile(
                leading: const Icon(Icons.not_listed_location_rounded, color: _muted),
                title: const Text('Clear location', style: TextStyle(color: _muted)),
                onTap: () {
                  setState(() => _location = null);
                  _scheduleAutoSave();
                  Navigator.pop(ctx);
                },
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ─── Draft Auto-save ───
  // Persists text fields (title/content/category/subcategory/visibility/
  // location/schedule/poll options) to SharedPreferences. Media file
  // attachments are intentionally NOT persisted (raw file bytes aren't
  // safe/portable to store this way) — only their existence is implied
  // by the rest of the draft.
  void _scheduleAutoSave() {
    if (_restoringDraft) return; // don't re-save while we're applying a restored draft
    _autoSaveTimer?.cancel();
    _autoSaveTimer = Timer(_autoSaveDebounce, _saveDraftSilently);
  }

  Map<String, dynamic> _draftToJson() {
    return {
      'title': _titleController.text,
      'content': _contentController.text,
      'category': _category,
      'subcategory': _subcategory,
      'visibility': _visibility,
      'location': _location,
      'scheduledAt': _scheduledDateTime?.toIso8601String(),
      'pollOptions': _pollOptions.map((o) => o.controller.text).toList(),
      'savedAt': DateTime.now().toIso8601String(),
    };
  }

  Future<void> _saveDraftSilently() async {
    if (!mounted) return;
    final hasContent = _titleController.text.trim().isNotEmpty ||
        _contentController.text.trim().isNotEmpty ||
        _pollOptions.any((o) => o.controller.text.trim().isNotEmpty);
    try {
      final prefs = await SharedPreferences.getInstance();
      if (!hasContent) {
        await prefs.remove(_draftPrefsKey);
        if (mounted) setState(() => _lastSavedAt = null);
        return;
      }
      await prefs.setString(_draftPrefsKey, jsonEncode(_draftToJson()));
      if (mounted) setState(() => _lastSavedAt = DateTime.now());
    } catch (_) {
      // Auto-save is best-effort — a failure here shouldn't interrupt composing.
    }
  }

  Future<void> _checkForSavedDraft() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_draftPrefsKey);
      if (raw == null || !mounted) return;
      final data = jsonDecode(raw) as Map<String, dynamic>;
      final hasContent = ((data['title'] as String?) ?? '').trim().isNotEmpty ||
          ((data['content'] as String?) ?? '').trim().isNotEmpty;
      if (!hasContent) {
        await prefs.remove(_draftPrefsKey);
        return;
      }
      // Restore the cached draft straight away — no confirmation prompt.
      _applyDraft(data);
    } catch (_) {
      // Corrupt/unreadable draft — ignore silently.
    }
  }

  void _applyDraft(Map<String, dynamic> data) {
    HapticFeedback.mediumImpact();
    _restoringDraft = true;
    setState(() {
      _titleController.text = (data['title'] as String?) ?? '';
      _contentController.text = (data['content'] as String?) ?? '';
      _category = data['category'] as String?;
      _subcategory = data['subcategory'] as String?;
      _visibility = (data['visibility'] as String?) ?? 'public';
      _location = data['location'] as String?;
      final scheduledRaw = data['scheduledAt'] as String?;
      _scheduledDateTime = scheduledRaw != null ? DateTime.tryParse(scheduledRaw) : null;

      final options = (data['pollOptions'] as List?)?.cast<String>() ?? const [];
      for (final o in _pollOptions) {
        o.controller.dispose();
      }
      _pollOptions.clear();
      for (final text in options) {
        _pollOptions.add(_PollOption(TextEditingController(text: text)));
      }

      final savedAtRaw = data['savedAt'] as String?;
      _lastSavedAt = savedAtRaw != null ? DateTime.tryParse(savedAtRaw) : null;
    });
    _restoringDraft = false;
    _showSuccess('Draft restored');
  }

  Future<void> _clearDraft() async {
    _autoSaveTimer?.cancel();
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_draftPrefsKey);
    } catch (_) {}
    if (mounted) setState(() => _lastSavedAt = null);
  }

  String _draftSavedLabel() {
    if (_lastSavedAt == null) return '';
    final diff = DateTime.now().difference(_lastSavedAt!);
    if (diff.inSeconds < 10) return 'Draft saved just now';
    if (diff.inMinutes < 1) return 'Draft saved ${diff.inSeconds}s ago';
    if (diff.inHours < 1) return 'Draft saved ${diff.inMinutes}m ago';
    return 'Draft saved ${_formatSchedule(_lastSavedAt!)}';
  }

  // ─── @mentions / #hashtags autocomplete ───
  void _detectMentionOrHashtag() {
    final text = _contentController.text;
    final cursor = _contentController.selection.baseOffset;
    if (cursor < 0 || cursor > text.length) {
      _clearSuggestions();
      return;
    }
    // Walk backward from the cursor to find the start of the current token.
    int i = cursor - 1;
    while (i >= 0 && text[i] != ' ' && text[i] != '\n') {
      if (text[i] == '@' || text[i] == '#') break;
      i--;
    }
    if (i < 0 || (text[i] != '@' && text[i] != '#')) {
      _clearSuggestions();
      return;
    }
    final tokenType = text[i];
    final query = text.substring(i + 1, cursor).toLowerCase();
    final source = tokenType == '@' ? _demoUsers : _trendingHashtags;
    final results = query.isEmpty
        ? source.take(6).toList()
        : source.where((s) => s.toLowerCase().contains(query)).take(6).toList();
    setState(() {
      _activeTokenType = tokenType;
      _tokenStartIndex = i;
      _suggestionResults = results;
    });
  }

  void _clearSuggestions() {
    if (_activeTokenType == null && _suggestionResults.isEmpty) return;
    setState(() {
      _activeTokenType = null;
      _tokenStartIndex = -1;
      _suggestionResults = [];
    });
  }

  void _applySuggestion(String value) {
    if (_tokenStartIndex < 0 || _activeTokenType == null) return;
    HapticFeedback.selectionClick();
    final text = _contentController.text;
    final cursor = _contentController.selection.baseOffset;
    final end = cursor < 0 ? text.length : cursor;
    final prefix = text.substring(0, _tokenStartIndex);
    final suffix = text.substring(end);
    final insertion = '$_activeTokenType$value ';
    _contentController.value = TextEditingValue(
      text: '$prefix$insertion$suffix',
      selection: TextSelection.collapsed(offset: prefix.length + insertion.length),
    );
    _clearSuggestions();
    _scheduleAutoSave();
  }

  // ─── Formatting ───
  void _toggleBold() {
    HapticFeedback.lightImpact();
    setState(() => _isBold = !_isBold);
    _insertWrap('**', '**');
  }

  void _toggleItalic() {
    HapticFeedback.lightImpact();
    setState(() => _isItalic = !_isItalic);
    _insertWrap('*', '*');
  }

  void _insertWrap(String left, String right) {
    final text = _contentController.text;
    final sel = _contentController.selection;
    final start = sel.start >= 0 ? sel.start : text.length;
    final end = sel.end >= 0 ? sel.end : text.length;
    final selected = text.substring(start, end);
    final replacement = '$left$selected$right';
    _contentController.text = text.replaceRange(start, end, replacement);
    _contentController.selection = TextSelection.collapsed(offset: start + replacement.length);
  }

  // ─── Clear All ───
  // Resets the entire composer (text, media, poll, category, visibility,
  // location, schedule) AND wipes the cached draft in one confirmed tap —
  // replaces the old "Clear draft" link, which only cleared the cache
  // without touching what was still on screen.
  bool get _hasAnythingToClear =>
      _canPost ||
      _lastSavedAt != null ||
      _category != null ||
      _location != null ||
      _scheduledDateTime != null ||
      _visibility != 'public';

  Future<void> _clearAll() async {
    if (!_hasAnythingToClear) return;
    HapticFeedback.lightImpact();
    _clearSuggestions();
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Clear everything?', style: TextStyle(fontWeight: FontWeight.w800)),
        content: const Text(
          'Title, content, media, poll and the saved draft will all be cleared together. This cannot be undone.',
          style: TextStyle(fontSize: 13.5, height: 1.4),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel', style: TextStyle(color: _muted, fontWeight: FontWeight.w700)),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Clear all', style: TextStyle(color: _error, fontWeight: FontWeight.w700)),
          ),
        ],
      ),
    );
    if (confirm != true || !mounted) return;

    HapticFeedback.mediumImpact();
    _autoSaveTimer?.cancel();
    _restoringDraft = true; // suppress auto-save while we reset every field
    setState(() {
      _titleController.clear();
      _contentController.clear();
      _attachments.clear();
      for (final o in _pollOptions) {
        o.controller.dispose();
      }
      _pollOptions.clear();
      _category = null;
      _subcategory = null;
      _categoryError = false;
      _visibility = 'public';
      _location = null;
      _scheduledDateTime = null;
      _postType = 'text';
      _lastSavedAt = null;
    });
    _restoringDraft = false;

    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_draftPrefsKey);
    } catch (_) {
      // Best-effort — the on-screen reset above already happened either way.
    }

    if (_fabAnimController.status == AnimationStatus.completed) {
      _fabAnimController.reverse();
    }
    if (mounted) _showSuccess('Everything cleared');
  }

  // ─── Submit ───
  Future<void> _createPost() async {
    HapticFeedback.mediumImpact();
    if (!_formKey.currentState!.validate()) return;
    if (_contentController.text.trim().isEmpty && _attachments.isEmpty && !_hasPoll) {
      _showError('Add some content first');
      return;
    }
    if (_category == null) {
      setState(() => _categoryError = true);
      _showError('Select a category');
      await Future.delayed(const Duration(milliseconds: 80));
      if (mounted && _detailsCardKey.currentContext != null) {
        Scrollable.ensureVisible(
          _detailsCardKey.currentContext!,
          duration: const Duration(milliseconds: 350),
          curve: Curves.easeOut,
          alignment: 0.2,
        );
      }
      return;
    }
    if (_hasPoll && _pollOptions.length < 2) {
      _showError('Poll needs at least 2 options');
      return;
    }
    if (_hasPoll && _pollOptions.any((o) => o.controller.text.trim().isEmpty)) {
      _showError('Fill in all poll options');
      return;
    }
    if (_hasPoll) {
      final texts = _pollOptions.map((o) => o.controller.text.trim().toLowerCase()).toList();
      if (texts.toSet().length != texts.length) {
        _showError('Poll options cannot be the same');
        return;
      }
    }

    setState(() {
      _isLoading = true;
      _chunkUploadProgress = null;
    });

    try {
      final hashtagRegex = RegExp(r'#(\w+)');
      final hashtags = hashtagRegex.allMatches(_contentController.text).map((m) => m.group(1)!).toList();

      final pollData = _hasPoll
          ? _pollOptions.map((o) => {'text': o.controller.text.trim(), 'votes': 0}).toList()
          : null;

      // FIX: createPost() expects Map<String, dynamic>? for `location`,
      // but _location is a plain String? (e.g. "Mumbai, India").
      // Wrap it so the type matches. If you'd rather keep location as a
      // plain string end-to-end, change the `location` parameter type in
      // api_service.dart to String? instead and pass `_location` directly.
      final locationData = _location != null ? {'name': _location} : null;

      // NEW — use chunked upload when there's a single large file (e.g. video)
      // (4GB tak), taaki ek single bade request me poora upload timeout/fail
      // isn't needed. Small or multiple files are fine with normal multipart.
      final bool useChunkedUpload = _attachments.length == 1 &&
          _attachments.first.file.lengthSync() > _chunkedUploadThreshold;

      late final Map<String, dynamic> result;

      if (useChunkedUpload) {
        final attachment = _attachments.first;
        result = await _apiService.createPostWithChunkedUpload(
          file: attachment.file,
          title: _titleController.text.trim(),
          content: _contentController.text.trim(),
          category: _category!,
          subcategory: _subcategory,
          postType: _postType,
          visibility: _visibility,
          hashtags: hashtags,
          location: locationData,
          pollOptions: pollData,
          mediaCaption: attachment.caption,
          mediaType: attachment.type,
          scheduledAt: _scheduledDateTime,
          onProgress: (progress) {
            if (mounted) setState(() => _chunkUploadProgress = progress);
          },
        );
      } else {
        result = await _apiService.createPost(
          title: _titleController.text.trim(),
          content: _contentController.text.trim(),
          category: _category!,
          subcategory: _subcategory,
          postType: _postType,
          visibility: _visibility,
          hashtags: hashtags,
          mediaFiles: _attachments.isEmpty ? null : _attachments.map((a) => a.file).toList(),
          mediaTypes: _attachments.isEmpty ? null : _attachments.map((a) => a.type).toList(),
          mediaCaptions: _attachments.isEmpty ? null : _attachments.map((a) => a.caption).toList(),
          scheduledAt: _scheduledDateTime,
          location: locationData,
          pollOptions: pollData,
        );
      }

      await _clearDraft();
      if (!mounted) return;

      final message = result['message'] ??
          (_scheduledDateTime != null
              ? 'Post scheduled for ${_formatSchedule(_scheduledDateTime!)}!'
              : 'Post created successfully!');

      HapticFeedback.mediumImpact();
      setState(() {
        _isLoading = false;
        _chunkUploadProgress = null;
        _justPosted = true;
      });
      _successController.forward(from: 0);
      await Future.delayed(const Duration(milliseconds: 700));
      if (!mounted) return;

      Navigator.pop(context, true);
      _showSuccess(message);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isLoading = false;
        _chunkUploadProgress = null;
      });
      _showError(e.toString().replaceAll('Exception: ', ''));
    }
  }

  // ─── Success Overlay — brief celebratory checkmark right before navigating back ───
  Widget _buildSuccessOverlay() {
    return IgnorePointer(
      ignoring: !_justPosted,
      child: AnimatedOpacity(
        opacity: _justPosted ? 1 : 0,
        duration: const Duration(milliseconds: 200),
        child: Container(
          decoration: BoxDecoration(
            gradient: RadialGradient(
              colors: [_primaryDark, _primary.withOpacity(0.96)],
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
                    border: Border.all(color: _accent.withOpacity(0.5), width: 2),
                    boxShadow: [BoxShadow(color: _accent.withOpacity(0.35), blurRadius: 24, spreadRadius: 2)],
                  ),
                  child: Container(
                    width: 76,
                    height: 76,
                    decoration: const BoxDecoration(color: Colors.white, shape: BoxShape.circle),
                    child: const Icon(Icons.check_rounded, color: _primary, size: 42),
                  ),
                ),
                const SizedBox(height: 18),
                const Text(
                  'Posted!',
                  style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 18, letterSpacing: 0.2),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // ─── Helpers ───
  void _scrollToBottom() {
    Future.delayed(const Duration(milliseconds: 100), () {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void _showError(String msg) => _showSnack(msg, _error);
  void _showSuccess(String msg) => _showSnack(msg, _success);

  void _showSnack(String msg, Color color) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(msg, style: const TextStyle(fontWeight: FontWeight.w500)),
        backgroundColor: color,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        margin: const EdgeInsets.all(16),
        elevation: 6,
        action: SnackBarAction(
          label: 'DISMISS',
          textColor: Colors.white,
          onPressed: () => ScaffoldMessenger.of(context).hideCurrentSnackBar(),
        ),
      ),
    );
  }

  // Reversible-action snackbar — used for undoable removals (attachment, poll option).
  void _showUndoSnack(String msg, VoidCallback onUndo) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(msg, style: const TextStyle(fontWeight: FontWeight.w500)),
          backgroundColor: _navy,
          behavior: SnackBarBehavior.floating,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          margin: const EdgeInsets.all(16),
          elevation: 6,
          duration: const Duration(seconds: 4),
          action: SnackBarAction(
            label: 'UNDO',
            textColor: _accent,
            onPressed: () {
              HapticFeedback.selectionClick();
              onUndo();
            },
          ),
        ),
      );
  }

  String _getFileSize(File file) {
    try {
      final bytes = file.lengthSync();
      if (bytes < 1024) return '$bytes B';
      if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
      return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
    } catch (_) {
      return '';
    }
  }

  IconData _fileIcon(String type) {
    return switch (type) {
      'image' => Icons.image_rounded,
      'video' => Icons.videocam_rounded,
      'document' => Icons.description_rounded,
      _ => Icons.insert_drive_file_rounded,
    };
  }

  Color _fileColor(String type) {
    return switch (type) {
      'image' => _primary,
      'video' => _error,
      'document' => _warning,
      _ => _muted,
    };
  }

  Future<void> _openQuickPost() async {
    HapticFeedback.mediumImpact();
    final result = await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const QuickTextPost()),
    );
    if (result == true && mounted) Navigator.pop(context, true);
  }

  // ═══════════════════════════════════════════════════════════════════
  // BUILD
  // ═══════════════════════════════════════════════════════════════════
  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop) return;
        if (await _confirmDiscardIfNeeded() && mounted) Navigator.pop(context);
      },
      child: Scaffold(
      backgroundColor: _bg,
      extendBodyBehindAppBar: false,
      appBar: _buildGlassAppBar(),
      body: Stack(
        children: [
          // Soft ambient wash behind the whole composer — a faint navy
          // bloom at the top and a whisper of gold at the corner, so the
          // page reads as premium rather than a flat white sheet.
          Positioned.fill(
            child: IgnorePointer(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      _primarySoft.withOpacity(0.55),
                      _bg,
                      _bg,
                    ],
                    stops: const [0.0, 0.32, 1.0],
                  ),
                ),
              ),
            ),
          ),
          Positioned(
            top: -80,
            right: -60,
            child: IgnorePointer(
              child: Container(
                width: 220,
                height: 220,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: RadialGradient(
                    colors: [_accent.withOpacity(0.10), _accent.withOpacity(0.0)],
                  ),
                ),
              ),
            ),
          ),
          Form(
            key: _formKey,
            child: SingleChildScrollView(
              controller: _scrollController,
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 120),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _stagger(0, _buildQuickPostBanner()),
                  const SizedBox(height: 16),
                  _stagger(1, _buildComposerCard()),
                  const SizedBox(height: 16),
                  _stagger(2, _buildFormatToolbar()),
                  const SizedBox(height: 16),
                  if (_attachments.isNotEmpty) ...[
                    _stagger(3, _buildMediaCard()),
                    const SizedBox(height: 16),
                  ],
                  if (_hasPoll) ...[
                    _stagger(4, _buildPollCard()),
                    const SizedBox(height: 16),
                  ],
                  _stagger(5, _buildAddMediaCard()),
                  const SizedBox(height: 16),
                  _stagger(6, _buildDetailsCard()),
                  const SizedBox(height: 16),
                  _stagger(7, _buildScheduleCard()),
                  const SizedBox(height: 16),
                  _stagger(8, _buildLocationCard()),
                  const SizedBox(height: 32),
                ],
              ),
            ),
          ),
          _buildSuccessOverlay(),
        ],
      ),
      floatingActionButton: _canPost
          ? ScaleTransition(
              scale: _fabScale,
              child: Container(
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(24),
                  boxShadow: [
                    BoxShadow(
                      color: _primary.withOpacity(_isLoading ? 0.15 : 0.4),
                      blurRadius: 22,
                      offset: const Offset(0, 8),
                    ),
                  ],
                ),
                child: Material(
                  borderRadius: BorderRadius.circular(24),
                  child: Ink(
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(24),
                      gradient: LinearGradient(
                        begin: Alignment.topLeft,
                        end: Alignment.bottomRight,
                        colors: _isLoading
                            ? [_primary.withOpacity(0.55), _primaryDark.withOpacity(0.55)]
                            : [_primary, _primaryDark],
                      ),
                      border: Border.all(color: _accent.withOpacity(_isLoading ? 0.15 : 0.55), width: 1.2),
                    ),
                    child: InkWell(
                      borderRadius: BorderRadius.circular(24),
                      onTap: _isLoading ? null : _createPost,
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            _isLoading
                                ? SizedBox(
                                    width: 16,
                                    height: 16,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                      // NEW — chunked upload shows actual progress,
                                      // everywhere else it's the same indeterminate spinner as before
                                      value: _chunkUploadProgress,
                                      valueColor: const AlwaysStoppedAnimation(Colors.white),
                                    ),
                                  )
                                : Icon(
                                    _scheduledDateTime != null ? Icons.schedule_send_rounded : Icons.send_rounded,
                                    size: 18,
                                    color: _accent,
                                  ),
                            const SizedBox(width: 10),
                            Text(
                              _isLoading
                                  ? (_chunkUploadProgress != null
                                      ? 'Uploading ${(_chunkUploadProgress! * 100).toStringAsFixed(0)}%'
                                      : 'Posting...')
                                  : (_scheduledDateTime != null ? 'Schedule' : 'Share'),
                              style: const TextStyle(
                                color: Colors.white,
                                fontWeight: FontWeight.w700,
                                fontSize: 14,
                                letterSpacing: 0.2,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            )
          : null,
      ),
    );
  }

  // ─── Close handling — only interrupts with a confirm if there's content the
  // auto-save hasn't caught up with yet, so a stray tap/back-gesture can't
  // silently lose work. Returns true when it's safe to actually leave.
  Future<bool> _confirmDiscardIfNeeded() async {
    if (!_canPost || _lastSavedAt != null) return true;
    HapticFeedback.lightImpact();
    final discard = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Discard post?', style: TextStyle(fontWeight: FontWeight.w800)),
        content: const Text(
          "What you've written hasn't been saved yet. If you close now, it will be lost.",
          style: TextStyle(fontSize: 13.5, height: 1.4),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Keep editing', style: TextStyle(color: _muted, fontWeight: FontWeight.w700)),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Discard', style: TextStyle(color: _error, fontWeight: FontWeight.w700)),
          ),
        ],
      ),
    );
    return discard ?? false;
  }

  Future<void> _handleClose() async {
    if (await _confirmDiscardIfNeeded() && mounted) Navigator.pop(context);
  }

  // ─── AppBar — navy gradient header + gold hairline, same visual language
  // as TargetProfilePage's header, so moving between the two feels like one
  // app instead of two different screens bolted together. ───
  PreferredSizeWidget _buildGlassAppBar() {
    return PreferredSize(
      preferredSize: const Size.fromHeight(kToolbarHeight),
      child: Container(
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [_primary, _primaryDark],
          ),
          border: Border(
            bottom: BorderSide(color: _accent.withOpacity(0.4), width: 1),
          ),
          boxShadow: [
            BoxShadow(color: _primary.withOpacity(0.25), blurRadius: 16, offset: const Offset(0, 6)),
          ],
        ),
        child: AppBar(
          backgroundColor: Colors.transparent,
          surfaceTintColor: Colors.transparent,
          elevation: 0,
          centerTitle: true,
          // Close (not back) is intentional here — new_post is a modal
          // compose flow with unsaved content, so it keeps its own guarded
          // _handleClose instead of TargetProfile's plain Navigator.maybePop.
          leading: IconButton(
            icon: const Icon(Icons.close_rounded, color: Colors.white),
            tooltip: 'Close',
            onPressed: _handleClose,
          ),
          title: const Text(
            'New post',
            style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 18, letterSpacing: -0.2),
          ),
          actions: [
            Padding(
              padding: const EdgeInsets.only(right: 12),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 200),
                padding: const EdgeInsets.symmetric(horizontal: 4),
                decoration: BoxDecoration(
                  color: Colors.white.withOpacity(0.08),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: TextButton.icon(
                  onPressed: _hasAnythingToClear ? _clearAll : null,
                  style: TextButton.styleFrom(
                    foregroundColor: _hasAnythingToClear ? Colors.white : Colors.white38,
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                  ),
                  icon: const Icon(Icons.delete_sweep_rounded, size: 17),
                  label: const Text(
                    'Clear all',
                    style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // ─── Quick Post Banner ───
  Widget _buildQuickPostBanner() {
    return Hero(
      tag: 'quick_post_banner',
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: _openQuickPost,
          borderRadius: BorderRadius.circular(14),
          child: Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [_primary, _primaryDark],
              ),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: _accent.withOpacity(0.25), width: 1),
              boxShadow: [
                BoxShadow(color: _primary.withOpacity(0.35), blurRadius: 18, offset: const Offset(0, 8)),
              ],
            ),
            child: Stack(
              children: [
                // Decorative gold glow tucked in the corner — small premium
                // touch, purely visual, sits behind the row content.
                Positioned(
                  top: -30,
                  right: -20,
                  child: IgnorePointer(
                    child: Container(
                      width: 110,
                      height: 110,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        gradient: RadialGradient(
                          colors: [_accent.withOpacity(0.22), _accent.withOpacity(0.0)],
                        ),
                      ),
                    ),
                  ),
                ),
                Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: _accent.withOpacity(0.16),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: _accent.withOpacity(0.4), width: 1),
                  ),
                  child: const Icon(Icons.bolt_rounded, color: _accent, size: 22),
                ),
                const SizedBox(width: 14),
                const Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Quick Post',
                        style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 14.5, letterSpacing: 0.1),
                      ),
                      SizedBox(height: 3),
                      Text(
                        'Just want to write text? Fast compose here',
                        style: TextStyle(color: Colors.white70, fontSize: 12.5),
                      ),
                    ],
                  ),
                ),
                Container(
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(color: Colors.white.withOpacity(0.12), shape: BoxShape.circle),
                  child: const Icon(Icons.arrow_forward_ios_rounded, color: Colors.white, size: 13),
                ),
              ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // ─── Card Decoration (soft-lifted surface — gentle shadow + larger radius
  // for a more premium feel, still light and airy, not heavy) ───
  BoxDecoration get _cardDecoration => BoxDecoration(
        color: _surface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: _border, width: 1),
        boxShadow: [
          BoxShadow(color: _navy.withOpacity(0.06), blurRadius: 22, offset: const Offset(0, 8)),
          BoxShadow(color: _accent.withOpacity(0.05), blurRadius: 10, offset: const Offset(0, 2)),
          BoxShadow(color: _navy.withOpacity(0.02), blurRadius: 2, offset: const Offset(0, 1)),
        ],
      );

  // ─── Staggered one-time entrance for top-level sections (fade + gentle rise) ───
  Widget _stagger(int index, Widget child) {
    final start = (index * 0.08).clamp(0.0, 0.6);
    final end = (start + 0.4).clamp(0.0, 1.0);
    final curved = CurvedAnimation(
      parent: _entryController,
      curve: Interval(start, end, curve: Curves.easeOutCubic),
    );
    return AnimatedBuilder(
      animation: curved,
      child: child,
      builder: (context, kid) => Opacity(
        opacity: curved.value,
        child: Transform.translate(
          offset: Offset(0, (1 - curved.value) * 16),
          child: kid,
        ),
      ),
    );
  }

  // Icon sits in a soft tinted badge (defaults to brand navy, Poll passes
  // gold) instead of a bare glyph — small touch that makes every card
  // header feel considered rather than plain.
  Widget _sectionLabel(String text, {IconData? icon, Color? color}) {
    final tint = color ?? _primary;
    return Row(
      children: [
        if (icon != null) ...[
          Container(
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              color: tint.withOpacity(0.10),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Icon(icon, size: 14, color: tint),
          ),
          const SizedBox(width: 10),
        ],
        Text(
          text.toUpperCase(),
          style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w800, color: _muted, letterSpacing: 0.8),
        ),
      ],
    );
  }

  // ─── Composer Card ───
  Widget _buildComposerCard() {
    final charCount = _contentController.text.length;
    return AnimatedContainer(
      duration: const Duration(milliseconds: 300),
      padding: const EdgeInsets.all(18),
      decoration: _cardDecoration,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Visibility Chips — Wrap so the row always stays fully on-screen
          // (wraps to a 2nd line on narrow devices instead of clipping/scrolling off-screen)
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const CircleAvatar(
                radius: 16,
                backgroundColor: _primarySoft,
                child: Icon(Icons.person_rounded, color: _primary, size: 18),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: _visibilityOptions.map((opt) {
                    final selected = _visibility == opt['key'];
                    return AnimatedContainer(
                      duration: const Duration(milliseconds: 200),
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(20),
                        border: selected ? Border.all(color: _accent.withOpacity(0.5), width: 1.2) : null,
                      ),
                      child: ChoiceChip(
                        label: Text(opt['label'] as String),
                        avatar: Icon(
                          opt['icon'] as IconData,
                          size: 14,
                          color: selected ? Colors.white : _muted,
                        ),
                        selected: selected,
                        onSelected: (_) {
                          setState(() => _visibility = opt['key'] as String);
                          _scheduleAutoSave();
                        },
                        selectedColor: _primary,
                        backgroundColor: const Color(0xFFF1F5F9),
                        labelStyle: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w700,
                          color: selected ? Colors.white : Colors.black87,
                        ),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(20),
                          side: BorderSide.none,
                        ),
                        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 0),
                        labelPadding: const EdgeInsets.symmetric(horizontal: 2),
                        visualDensity: VisualDensity.compact,
                        materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      ),
                    );
                  }).toList(),
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Padding(
            padding: const EdgeInsets.only(left: 42),
            child: AnimatedSwitcher(
              duration: const Duration(milliseconds: 200),
              transitionBuilder: (child, anim) => FadeTransition(opacity: anim, child: child),
              child: Text(
                _visibilityDescription,
                key: ValueKey(_visibility),
                style: const TextStyle(fontSize: 11, color: _muted, fontWeight: FontWeight.w500),
              ),
            ),
          ),
          if (_lastSavedAt != null) ...[
            const SizedBox(height: 10),
            AnimatedSwitcher(
              duration: const Duration(milliseconds: 260),
              transitionBuilder: (child, anim) => FadeTransition(
                opacity: anim,
                child: ScaleTransition(scale: Tween(begin: 0.94, end: 1.0).animate(anim), child: child),
              ),
              child: Row(
                key: ValueKey(_lastSavedAt),
                children: [
                  const Icon(Icons.cloud_done_rounded, size: 14, color: _success),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      '${_draftSavedLabel()} · cached on this device',
                      style: const TextStyle(fontSize: 11, color: _muted, fontWeight: FontWeight.w600),
                    ),
                  ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 16),
          const Divider(height: 1, color: _border),
          const SizedBox(height: 16),
          // Title
          TextFormField(
            controller: _titleController,
            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: _navy),
            decoration: InputDecoration(
              hintText: 'Add a title (optional)',
              hintStyle: TextStyle(color: _muted.withOpacity(0.6), fontWeight: FontWeight.w600, fontSize: 16),
              border: InputBorder.none,
              isDense: true,
              contentPadding: EdgeInsets.zero,
              suffixIcon: _titleController.text.isNotEmpty
                  ? IconButton(
                      icon: const Icon(Icons.clear_rounded, size: 18, color: _muted),
                      tooltip: 'Clear title',
                      onPressed: () => setState(() => _titleController.clear()),
                    )
                  : null,
            ),
          ),
          const SizedBox(height: 12),
          // Content
          TextFormField(
            controller: _contentController,
            maxLines: null,
            minLines: 5,
            style: TextStyle(
              fontSize: 15,
              height: 1.5,
              color: _navy,
              fontWeight: _isBold ? FontWeight.w700 : FontWeight.w400,
              fontStyle: _isItalic ? FontStyle.italic : FontStyle.normal,
            ),
            decoration: const InputDecoration(
              hintText: "What's on your mind? Use #hashtags to boost reach",
              hintStyle: TextStyle(color: _muted, fontSize: 15),
              border: InputBorder.none,
              isDense: true,
              contentPadding: EdgeInsets.zero,
            ),
            validator: (val) {
              if ((val == null || val.trim().isEmpty) && _attachments.isEmpty && !_hasPoll) {
                return 'Content, media or poll is required';
              }
              return null;
            },
          ),
          if (_suggestionResults.isNotEmpty) ...[
            const SizedBox(height: 10),
            SizedBox(
              height: 34,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: _suggestionResults.length,
                separatorBuilder: (_, __) => const SizedBox(width: 8),
                itemBuilder: (context, index) {
                  final value = _suggestionResults[index];
                  return ActionChip(
                    onPressed: () => _applySuggestion(value),
                    avatar: Icon(
                      _activeTokenType == '@' ? Icons.alternate_email_rounded : Icons.tag_rounded,
                      size: 13,
                      color: _primary,
                    ),
                    label: Text(value, style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: _navy)),
                    backgroundColor: _primarySoft,
                    side: BorderSide.none,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                    visualDensity: VisualDensity.compact,
                    padding: const EdgeInsets.symmetric(horizontal: 6),
                  );
                },
              ),
            ),
          ],
          const SizedBox(height: 8),
          // Char count + location chip
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              if (_location != null)
                Chip(
                  avatar: const Icon(Icons.location_on_rounded, size: 14, color: _primary),
                  label: Text(_location!, style: const TextStyle(fontSize: 11)),
                  backgroundColor: _primarySoft,
                  side: BorderSide.none,
                  visualDensity: VisualDensity.compact,
                  deleteIcon: const Icon(Icons.close_rounded, size: 14),
                  onDeleted: () {
                    setState(() => _location = null);
                    _scheduleAutoSave();
                  },
                )
              else
                const SizedBox.shrink(),
              _buildCharCountRing(charCount),
            ],
          ),
        ],
      ),
    );
  }

  // ─── Char count ring — small circular progress toward a soft target, no hard cap ───
  Widget _buildCharCountRing(int charCount) {
    const softTarget = 300; // purely visual guidance, not enforced
    final progress = (charCount / softTarget).clamp(0.0, 1.0);
    final nearLimit = charCount >= softTarget;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          '$charCount',
          style: TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.w700,
            color: nearLimit ? _warning : _muted.withOpacity(0.8),
          ),
        ),
        const SizedBox(width: 6),
        SizedBox(
          width: 16,
          height: 16,
          child: Stack(
            alignment: Alignment.center,
            children: [
              SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                  value: 1,
                  strokeWidth: 2,
                  valueColor: AlwaysStoppedAnimation(_border),
                ),
              ),
              SizedBox(
                width: 16,
                height: 16,
                child: TweenAnimationBuilder<double>(
                  tween: Tween(begin: 0, end: progress),
                  duration: const Duration(milliseconds: 220),
                  curve: Curves.easeOut,
                  builder: (context, value, _) => CircularProgressIndicator(
                    value: value,
                    strokeWidth: 2,
                    valueColor: AlwaysStoppedAnimation(nearLimit ? _warning : _primary),
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  // ─── Format Toolbar ───
  // Wrapped in a horizontal scroller (never clips/overflows on narrow
  // screens) and, once a category is picked, shows a live tappable badge
  // right in the toolbar — jump straight back to the category picker
  // without hunting for the Details card below.
  Widget _buildFormatToolbar() {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 200),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: _surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: _border.withOpacity(0.6)),
        boxShadow: [
          BoxShadow(color: _navy.withOpacity(0.04), blurRadius: 14, offset: const Offset(0, 5)),
        ],
      ),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            _toolbarBtn(Icons.format_bold_rounded, _toggleBold, _isBold),
            _toolbarBtn(Icons.format_italic_rounded, _toggleItalic, _isItalic),
            const VerticalDivider(width: 24, indent: 4, endIndent: 4),
            _toolbarBtn(Icons.poll_rounded, _addPoll, false, active: _hasPoll, gold: true),
            _toolbarBtn(Icons.location_on_rounded, _showLocationPicker, false, active: _location != null, gold: true),
            if (_category != null) ...[
              const VerticalDivider(width: 24, indent: 4, endIndent: 4),
              _categoryBadge(),
            ],
          ],
        ),
      ),
    );
  }

  Widget _categoryBadge() {
    return InkWell(
      onTap: () {
        HapticFeedback.selectionClick();
        _showCategoryPicker();
      },
      borderRadius: BorderRadius.circular(20),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        decoration: BoxDecoration(color: _primarySoft, borderRadius: BorderRadius.circular(20)),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(_iconForCategory(_category), size: 13, color: _primary),
            const SizedBox(width: 5),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 100),
              child: Text(
                _subcategory != null
                    ? (_subcategoryLabel(_subcategory) ?? _categoryLabel(_category) ?? '')
                    : (_categoryLabel(_category) ?? ''),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: _primary),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _toolbarBtn(IconData icon, VoidCallback onTap, bool isActive, {bool active = false, bool gold = false}) {
    final selected = isActive || active;
    final tint = gold ? _accent : _primary;
    return Material(
      color: selected ? tint.withOpacity(0.12) : Colors.transparent,
      borderRadius: BorderRadius.circular(10),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(10),
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Icon(icon, size: 20, color: selected ? tint : _muted),
        ),
      ),
    );
  }

  // ─── Media Card ───
  Widget _buildMediaCard() {
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: _cardDecoration,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              _sectionLabel('${_attachments.length} Attachment${_attachments.length > 1 ? 's' : ''}',
                  icon: Icons.attach_file_rounded),
              TextButton.icon(
                onPressed: () => setState(() {
                  _attachments.clear();
                  _updatePostType();
                }),
                icon: Icon(Icons.delete_outline_rounded, size: 16, color: _error),
                label: const Text('Clear', style: TextStyle(color: _error, fontWeight: FontWeight.w600)),
                style: TextButton.styleFrom(visualDensity: VisualDensity.compact),
              ),
            ],
          ),
          const SizedBox(height: 10),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: TweenAnimationBuilder<double>(
              tween: Tween(begin: 0, end: _attachments.length / _maxAttachments),
              duration: const Duration(milliseconds: 280),
              curve: Curves.easeOut,
              builder: (context, value, _) => LinearProgressIndicator(
                value: value,
                minHeight: 4,
                backgroundColor: _border,
                valueColor: AlwaysStoppedAnimation(
                  _attachments.length >= _maxAttachments ? _warning : _primary,
                ),
              ),
            ),
          ),
          const SizedBox(height: 14),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: List.generate(_attachments.length, (index) {
              final att = _attachments[index];
              final fileName = att.file.path.split('/').last;
              final isImage = att.type == 'image';
              final isVideo = att.type == 'video';

              return _AnimatedAttachmentTile(
                key: ValueKey(att),
                removing: att.removing,
                child: Stack(
                clipBehavior: Clip.none,
                children: [
                  Container(
                    width: 110,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(16),
                      color: const Color(0xFFF8FAFC),
                      border: Border.all(color: _border),
                      boxShadow: [
                        BoxShadow(color: _navy.withOpacity(0.06), blurRadius: 10, offset: const Offset(0, 4)),
                      ],
                    ),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        ClipRRect(
                          borderRadius: const BorderRadius.vertical(top: Radius.circular(16)),
                          child: isImage
                              ? Image.file(att.file, width: 110, height: 90, fit: BoxFit.cover)
                              : Stack(
                                  alignment: Alignment.center,
                                  children: [
                                    Container(
                                      width: 110,
                                      height: 90,
                                      color: _fileColor(att.type).withOpacity(0.1),
                                      child: Icon(_fileIcon(att.type), size: 32, color: _fileColor(att.type)),
                                    ),
                                    // Small play badge so a video thumbnail
                                    // reads as "video" even without a real
                                    // frame preview.
                                    if (isVideo)
                                      Container(
                                        padding: const EdgeInsets.all(6),
                                        decoration: BoxDecoration(
                                          color: Colors.black.withOpacity(0.45),
                                          shape: BoxShape.circle,
                                        ),
                                        child: const Icon(Icons.play_arrow_rounded, size: 16, color: Colors.white),
                                      ),
                                  ],
                                ),
                        ),
                        Padding(
                          padding: const EdgeInsets.all(8),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                fileName,
                                style: const TextStyle(fontSize: 10, fontWeight: FontWeight.w600),
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                              const SizedBox(height: 2),
                              Text(
                                _getFileSize(att.file),
                                style: TextStyle(fontSize: 9, color: Colors.grey.shade500),
                              ),
                              const SizedBox(height: 4),
                              GestureDetector(
                                onTap: () => _editCaption(index),
                                child: Text(
                                  att.caption.isEmpty ? '+ Add caption' : att.caption,
                                  style: TextStyle(
                                    fontSize: 9,
                                    fontWeight: FontWeight.w600,
                                    fontStyle: att.caption.isEmpty ? FontStyle.italic : FontStyle.normal,
                                    color: att.caption.isEmpty ? _primary : _navy,
                                  ),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                  // Remove button
                  Positioned(
                    top: -8,
                    right: -8,
                    child: Semantics(
                      button: true,
                      label: 'Remove attachment',
                      child: GestureDetector(
                        onTap: () => _removeAttachment(index),
                        child: Container(
                          padding: const EdgeInsets.all(5),
                          decoration: BoxDecoration(
                            color: _error,
                            shape: BoxShape.circle,
                            border: Border.all(color: Colors.white, width: 2),
                            boxShadow: [BoxShadow(color: _error.withOpacity(0.3), blurRadius: 6)],
                          ),
                          child: const Icon(Icons.close_rounded, size: 12, color: Colors.white),
                        ),
                      ),
                    ),
                  ),
                  // Type badge
                  Positioned(
                    top: 6,
                    left: 6,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
                      decoration: BoxDecoration(
                        color: Colors.black.withOpacity(0.6),
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Text(
                        att.type.toUpperCase(),
                        style: const TextStyle(color: Colors.white, fontSize: 8, fontWeight: FontWeight.w800),
                      ),
                    ),
                  ),
                  // Edit button (crop/filters/adjust) — images & videos
                  if (isImage || isVideo)
                    Positioned(
                      bottom: 68,
                      right: 6,
                      child: Semantics(
                        button: true,
                        label: isImage ? 'Edit image' : 'Edit video',
                        child: GestureDetector(
                          onTap: () => _editAttachment(index),
                          child: Container(
                            padding: const EdgeInsets.all(6),
                            decoration: BoxDecoration(
                              color: Colors.black.withOpacity(0.55),
                              shape: BoxShape.circle,
                              border: Border.all(color: Colors.white, width: 1.5),
                            ),
                            child: const Icon(Icons.edit_rounded, size: 13, color: Colors.white),
                          ),
                        ),
                      ),
                    ),
                ],
              ),
              );
            }),
          ),
        ],
      ),
    );
  }

  // ─── Poll Card ───
  Widget _buildPollCard() {
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: _cardDecoration,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              _sectionLabel('Poll', icon: Icons.poll_rounded, color: _accent),
              IconButton(
                onPressed: _removePoll,
                icon: const Icon(Icons.delete_outline_rounded),
                color: _error,
                iconSize: 20,
                visualDensity: VisualDensity.compact,
                tooltip: 'Remove poll',
              ),
            ],
          ),
          const SizedBox(height: 12),
          ...List.generate(_pollOptions.length, (index) {
            return Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: TextField(
                controller: _pollOptions[index].controller,
                onChanged: (_) => _scheduleAutoSave(),
                decoration: InputDecoration(
                  hintText: 'Option ${index + 1}',
                  prefixIcon: Icon(Icons.radio_button_unchecked_rounded, size: 18, color: _muted.withOpacity(0.5)),
                  suffixIcon: _pollOptions.length > 2
                      ? IconButton(
                          icon: const Icon(Icons.close_rounded, size: 18, color: _muted),
                          tooltip: 'Remove option',
                          onPressed: () => _removePollOption(index),
                        )
                      : null,
                  filled: true,
                  fillColor: const Color(0xFFF8FAFC),
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
                  enabledBorder:
                      OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
                  focusedBorder:
                      OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: _primary)),
                  contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                ),
              ),
            );
          }),
          if (_pollOptions.length < _maxPollOptions)
            TextButton.icon(
              onPressed: _addPoll,
              icon: const Icon(Icons.add_rounded, size: 18),
              label: const Text('Add option'),
              style: TextButton.styleFrom(
                foregroundColor: _primary,
                alignment: Alignment.centerLeft,
                padding: EdgeInsets.zero,
              ),
            ),
        ],
      ),
    );
  }

  // ─── Add Media Card ───
  Widget _buildAddMediaCard() {
    final items = [
      {'icon': Icons.image_rounded, 'label': 'Photos', 'color': _primary, 'onTap': _pickImages},
      {'icon': Icons.videocam_rounded, 'label': 'Videos', 'color': _error, 'onTap': _pickVideos},
      {'icon': Icons.description_rounded, 'label': 'Files', 'color': _warning, 'onTap': _pickDocuments},
      {'icon': Icons.camera_alt_rounded, 'label': 'Camera', 'color': _success, 'onTap': _showCameraOptions},
    ];
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: _cardDecoration,
      child: Row(
        children: items.map((item) {
          return Expanded(
            child: Material(
              color: Colors.transparent,
              borderRadius: BorderRadius.circular(14),
              child: InkWell(
                borderRadius: BorderRadius.circular(14),
                onTap: item['onTap'] as VoidCallback,
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: Column(
                    children: [
                      Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: (item['color'] as Color).withOpacity(0.1),
                          shape: BoxShape.circle,
                        ),
                        child: Icon(item['icon'] as IconData, color: item['color'] as Color, size: 22),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        item['label'] as String,
                        style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  // ─── Details Card ───
  // Two-step flow: Category is shown first; picking one opens the
  // Subcategory picker automatically right after.
  Widget _buildDetailsCard() {
    return Container(
      key: _detailsCardKey,
      padding: const EdgeInsets.all(18),
      decoration: _categoryError ? _cardDecoration.copyWith(border: Border.all(color: _error.withOpacity(0.4))) : _cardDecoration,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _sectionLabel('Post Details', icon: Icons.tune_rounded),
          const SizedBox(height: 16),
          _loadingCategories
              ? const _ShimmerLoader(height: 60)
              : _categorySelectorTile(
                  label: 'CATEGORY *',
                  placeholder: 'Select a category',
                  valueLabel: _categoryLabel(_category),
                  icon: _iconForCategory(_category),
                  hasError: _categoryError,
                  onTap: _showCategoryPicker,
                ),
          if (_categoryError) ...[
            const SizedBox(height: 6),
            const Text(
              'Selecting a category is required',
              style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: _error),
            ),
          ],
          // Quick-pick row — one tap sets the category right here without
          // opening the full sheet; picking one still auto-advances into
          // Subcategory, same as the sheet flow.
          if (!_loadingCategories && _categories.isNotEmpty) ...[
            const SizedBox(height: 12),
            SizedBox(
              height: 30,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: _categories.length,
                separatorBuilder: (_, __) => const SizedBox(width: 8),
                itemBuilder: (context, i) {
                  final c = _categories[i];
                  final key = c['key'];
                  final selected = _category == key;
                  return ChoiceChip(
                    label: Text(
                      c['label'] ?? '',
                      style: TextStyle(
                        fontSize: 11.5,
                        fontWeight: FontWeight.w700,
                        color: selected ? Colors.white : _navy,
                      ),
                    ),
                    avatar: Icon(_iconForCategory(key), size: 13, color: selected ? Colors.white : _primary),
                    selected: selected,
                    onSelected: (_) {
                      HapticFeedback.selectionClick();
                      final changed = _category != key;
                      setState(() {
                        _category = key;
                        if (changed) _subcategory = null;
                        _categoryError = false;
                      });
                      _scheduleAutoSave();
                      if (changed) {
                        final hasSubs = (_categorySubcategoryMap[key] ?? const <String>[]).isNotEmpty;
                        if (hasSubs) {
                          Future.delayed(const Duration(milliseconds: 200), () {
                            if (mounted) _showSubcategoryPicker();
                          });
                        }
                      }
                    },
                    selectedColor: _primary,
                    backgroundColor: const Color(0xFFF1F5F9),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20), side: BorderSide.none),
                    visualDensity: VisualDensity.compact,
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                  );
                },
              ),
            ),
          ],
          AnimatedSize(
            duration: const Duration(milliseconds: 250),
            curve: Curves.easeOut,
            alignment: Alignment.topCenter,
            child: (_category == null || _subcategoriesForSelectedCategory.isEmpty)
                ? const SizedBox(width: double.infinity)
                : Padding(
                    padding: const EdgeInsets.only(top: 14),
                    child: AnimatedOpacity(
                      duration: const Duration(milliseconds: 220),
                      opacity: _subcategoriesForSelectedCategory.isEmpty ? 0 : 1,
                      child: _categorySelectorTile(
                        label: 'SUBCATEGORY',
                        placeholder: 'Select a subcategory',
                        valueLabel: _subcategoryLabel(_subcategory),
                        icon: Icons.subdirectory_arrow_right_rounded,
                        onTap: _showSubcategoryPicker,
                      ),
                    ),
                  ),
          ),
        ],
      ),
    );
  }

  // ─── Category → key/label lookup helpers ───
  String? _categoryLabel(String? key) {
    if (key == null) return null;
    for (final c in _categories) {
      if (c['key'] == key) return c['label'];
    }
    return null;
  }

  String? _subcategoryLabel(String? key) {
    if (key == null) return null;
    for (final s in _allSubcategories) {
      if (s['key'] == key) return s['label'];
    }
    return null;
  }

  // Icon per top-level category — falls back to a generic tag icon for
  // any key not explicitly mapped (keeps this resilient to new categories
  // added later in models.py without needing a Flutter update).
  IconData _iconForCategory(String? key) {
    switch (key) {
      case 'school_education':
        return Icons.school_rounded;
      case 'higher_education':
        return Icons.account_balance_rounded;
      case 'subjects':
        return Icons.menu_book_rounded;
      case 'skills':
        return Icons.construction_rounded;
      case 'experiments':
        return Icons.science_rounded;
      case 'literature':
        return Icons.auto_stories_rounded;
      case 'motivational':
        return Icons.emoji_events_rounded;
      case 'competitive_exams':
        return Icons.edit_note_rounded;
      case 'general_knowledge':
        return Icons.public_rounded;
      case 'arts_culture':
        return Icons.palette_rounded;
      case 'sports':
        return Icons.sports_basketball_rounded;
      case 'environment':
        return Icons.eco_rounded;
      case 'parenting':
        return Icons.family_restroom_rounded;
      default:
        return Icons.category_rounded;
    }
  }

  // ─── Tappable selector tile (replaces the old plain dropdown) ───
  Widget _categorySelectorTile({
    required String label,
    required String placeholder,
    required String? valueLabel,
    required IconData icon,
    required VoidCallback onTap,
    bool hasError = false,
  }) {
    final hasValue = valueLabel != null;
    return InkWell(
      onTap: () {
        HapticFeedback.selectionClick();
        onTap();
      },
      borderRadius: BorderRadius.circular(14),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: hasError
              ? _error.withOpacity(0.04)
              : hasValue
                  ? _primarySoft.withOpacity(0.35)
                  : const Color(0xFFF8FAFC),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(
            color: hasError ? _error : (hasValue ? _primary.withOpacity(0.28) : _border),
            width: hasError ? 1.4 : (hasValue ? 1.3 : 1),
          ),
        ),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: hasValue ? _primarySoft : const Color(0xFFEDEFF4),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Icon(icon, size: 16, color: hasValue ? _primary : _muted),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    label,
                    style: TextStyle(
                      fontSize: 10.5,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.5,
                      color: hasError ? _error : _muted,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    valueLabel ?? placeholder,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w700,
                      color: hasValue ? _navy : _muted,
                    ),
                  ),
                ],
              ),
            ),
            const Icon(Icons.chevron_right_rounded, color: _muted, size: 20),
          ],
        ),
      ),
    );
  }

  // Shared bottom-sheet chrome for category/subcategory pickers.
  // Takes raw `entries` (each needs a 'label') + a `tileBuilder` so it can
  // own live search/filter state internally — entries auto-filter as the
  // user types, with a friendly empty-state if nothing matches. Search box
  // only shows once there are enough items to make it worth it.
  Widget _buildPickerSheet({
    required String title,
    required List<Map<String, String>> entries,
    required Widget Function(Map<String, String> entry) tileBuilder,
  }) {
    final searchController = TextEditingController();
    final queryNotifier = ValueNotifier<String>('');
    final showSearch = entries.length > 6;

    return BackdropFilter(
      filter: ImageFilter.blur(sigmaX: 8, sigmaY: 8),
      child: Container(
        constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.75),
        decoration: const BoxDecoration(
          color: _surface,
          borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
        ),
        padding: const EdgeInsets.fromLTRB(20, 14, 20, 8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(color: _border, borderRadius: BorderRadius.circular(2)),
              ),
            ),
            const SizedBox(height: 18),
            Row(
              children: [
                Expanded(
                  child: Text(title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: _navy)),
                ),
                Text('${entries.length}', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: _muted)),
              ],
            ),
            if (showSearch) ...[
              const SizedBox(height: 12),
              TextField(
                controller: searchController,
                onChanged: (v) => queryNotifier.value = v.trim().toLowerCase(),
                style: const TextStyle(fontSize: 13.5),
                decoration: InputDecoration(
                  hintText: 'Search $title'.replaceFirst('Select ', ''),
                  hintStyle: const TextStyle(fontSize: 13, color: _muted),
                  prefixIcon: const Icon(Icons.search_rounded, size: 20, color: _muted),
                  suffixIcon: ValueListenableBuilder<String>(
                    valueListenable: queryNotifier,
                    builder: (ctx, q, _) => q.isEmpty
                        ? const SizedBox.shrink()
                        : IconButton(
                            icon: const Icon(Icons.clear_rounded, size: 18, color: _muted),
                            tooltip: 'Clear search',
                            onPressed: () {
                              searchController.clear();
                              queryNotifier.value = '';
                            },
                          ),
                  ),
                  isDense: true,
                  filled: true,
                  fillColor: const Color(0xFFF8FAFC),
                  contentPadding: const EdgeInsets.symmetric(vertical: 12),
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: _border)),
                  enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: _border)),
                  focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: _primary, width: 1.5)),
                ),
              ),
            ],
            const SizedBox(height: 6),
            Flexible(
              child: ValueListenableBuilder<String>(
                valueListenable: queryNotifier,
                builder: (ctx, query, _) {
                  final filtered = query.isEmpty
                      ? entries
                      : entries.where((e) => (e['label'] ?? '').toLowerCase().contains(query)).toList();
                  if (filtered.isEmpty) {
                    return Padding(
                      padding: const EdgeInsets.symmetric(vertical: 36),
                      child: Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.search_off_rounded, size: 30, color: _muted.withOpacity(0.5)),
                            const SizedBox(height: 8),
                            Text('No match for "$query"', style: const TextStyle(fontSize: 12.5, color: _muted, fontWeight: FontWeight.w600)),
                          ],
                        ),
                      ),
                    );
                  }
                  return SingleChildScrollView(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Column(children: filtered.map(tileBuilder).toList()),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }

  // Step 1 — pick the top-level category.
  void _showCategoryPicker() {
    HapticFeedback.mediumImpact();
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) => _buildPickerSheet(
        title: 'Select Category',
        entries: _categories,
        tileBuilder: (c) {
          final key = c['key'];
          final selected = _category == key;
          return ListTile(
            contentPadding: const EdgeInsets.symmetric(horizontal: 4),
            leading: Container(
              padding: const EdgeInsets.all(9),
              decoration: BoxDecoration(
                color: selected ? _primary : _primarySoft,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Icon(_iconForCategory(key), size: 18, color: selected ? Colors.white : _primary),
            ),
            title: Text(c['label'] ?? '', style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: _navy)),
            subtitle: Text(
              '${(_categorySubcategoryMap[key] ?? const <String>[]).length} subcategories',
              style: const TextStyle(fontSize: 11, color: _muted),
            ),
            trailing: selected ? const Icon(Icons.check_circle_rounded, color: _success) : null,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
            onTap: () {
              HapticFeedback.selectionClick();
              final changed = _category != key;
              setState(() {
                _category = key;
                if (changed) _subcategory = null;
                _categoryError = false;
              });
              _scheduleAutoSave();
              Navigator.pop(ctx);
              // Auto-advance straight into Subcategory once Category is picked,
              // so the flow is: see Category → pick it → Subcategory shows next.
              final hasSubs = (_categorySubcategoryMap[key] ?? const <String>[]).isNotEmpty;
              if (changed && hasSubs) {
                Future.delayed(const Duration(milliseconds: 220), () {
                  if (mounted) _showSubcategoryPicker();
                });
              }
            },
          );
        },
      ),
    );
  }

  // Step 2 — pick the subcategory that belongs to the chosen category.
  void _showSubcategoryPicker() {
    if (_category == null) return;
    final subs = _subcategoriesForSelectedCategory;
    if (subs.isEmpty) return;
    HapticFeedback.mediumImpact();
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) => _buildPickerSheet(
        title: 'Select Subcategory',
        entries: subs,
        tileBuilder: (s) {
          final selected = _subcategory == s['key'];
          return ListTile(
            contentPadding: const EdgeInsets.symmetric(horizontal: 4),
            title: Text(s['label'] ?? '', style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: _navy)),
            trailing: selected ? const Icon(Icons.check_circle_rounded, color: _success) : null,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
            onTap: () {
              HapticFeedback.selectionClick();
              setState(() => _subcategory = s['key']);
              _scheduleAutoSave();
              Navigator.pop(ctx);
            },
          );
        },
      ),
    );
  }

  // ─── Schedule Card ───
  Widget _buildScheduleCard() {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 300),
      padding: const EdgeInsets.all(18),
      decoration: _cardDecoration,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(child: _sectionLabel('Schedule', icon: Icons.schedule_rounded)),
              Switch.adaptive(
                value: _scheduledDateTime != null,
                activeColor: _primary,
                onChanged: (val) {
                  if (val) {
                    _pickScheduleDateTime();
                  } else {
                    setState(() => _scheduledDateTime = null);
                    _scheduleAutoSave();
                  }
                },
              ),
            ],
          ),
          if (_scheduledDateTime == null) ...[
            const SizedBox(height: 12),
            const Divider(height: 1, color: _border),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: _scheduleQuickPicks.map((pick) {
                return ActionChip(
                  avatar: const Icon(Icons.bolt_rounded, size: 14, color: _accent),
                  label: Text(pick['label'] as String, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700)),
                  backgroundColor: _primarySoft,
                  side: BorderSide.none,
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                  visualDensity: VisualDensity.compact,
                  onPressed: () => _applyQuickSchedule(pick['time'] as DateTime),
                );
              }).toList(),
            ),
          ],
          if (_scheduledDateTime != null) ...[
            const SizedBox(height: 12),
            const Divider(height: 1, color: _border),
            const SizedBox(height: 14),
            InkWell(
              onTap: _pickScheduleDateTime,
              borderRadius: BorderRadius.circular(14),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                decoration: BoxDecoration(
                  color: _primarySoft,
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(color: _primary.withOpacity(0.3)),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.event_available_rounded, size: 20, color: _primary),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            _formatSchedule(_scheduledDateTime!),
                            style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: _navy),
                          ),
                          const SizedBox(height: 2),
                          const Text(
                            'Auto-publish at scheduled time',
                            style: TextStyle(fontSize: 11, color: _muted),
                          ),
                        ],
                      ),
                    ),
                    const Icon(Icons.edit_rounded, size: 18, color: _primary),
                  ],
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  // ─── Location Card ───
  Widget _buildLocationCard() {
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: _cardDecoration,
      child: InkWell(
        onTap: _showLocationPicker,
        borderRadius: BorderRadius.circular(14),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(color: _primarySoft, borderRadius: BorderRadius.circular(12)),
              child: const Icon(Icons.location_on_rounded, color: _primary, size: 20),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _sectionLabel('Location'),
                  const SizedBox(height: 4),
                  Text(
                    _location ?? 'Add location tag',
                    style: TextStyle(
                      fontSize: 13.5,
                      fontWeight: FontWeight.w600,
                      color: _location != null ? _navy : _muted,
                    ),
                  ),
                ],
              ),
            ),
            Icon(
              _location != null ? Icons.check_circle_rounded : Icons.chevron_right_rounded,
              color: _location != null ? _success : _muted,
              size: 22,
            ),
          ],
        ),
      ),
    );
  }

}

// ═══════════════════════════════════════════════════════════════════
// Helper Classes
// ═══════════════════════════════════════════════════════════════════

class _MediaAttachment {
  _MediaAttachment(this.file, this.type, {this.caption = ''});
  final File file;
  final String type;
  String caption;
  bool removing = false;
}

class _PollOption {
  _PollOption(this.controller);
  final TextEditingController controller;
}

// ─── Animated Attachment Tile (pop-in on add, shrink-out on remove) ───
class _AnimatedAttachmentTile extends StatefulWidget {
  const _AnimatedAttachmentTile({super.key, required this.removing, required this.child});
  final bool removing;
  final Widget child;

  @override
  State<_AnimatedAttachmentTile> createState() => _AnimatedAttachmentTileState();
}

class _AnimatedAttachmentTileState extends State<_AnimatedAttachmentTile> {
  bool _entered = false;

  @override
  void initState() {
    super.initState();
    // Starts scaled-down/invisible, then pops in on the very next frame —
    // gives a fresh "just landed" feel without ever re-triggering on rebuild.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) setState(() => _entered = true);
    });
  }

  @override
  Widget build(BuildContext context) {
    final show = _entered && !widget.removing;
    return AnimatedScale(
      scale: show ? 1.0 : (widget.removing ? 0.72 : 0.8),
      duration: Duration(milliseconds: widget.removing ? 160 : 280),
      curve: widget.removing ? Curves.easeIn : Curves.easeOutBack,
      child: AnimatedOpacity(
        opacity: show ? 1.0 : 0.0,
        duration: Duration(milliseconds: widget.removing ? 160 : 220),
        curve: Curves.easeOut,
        child: widget.child,
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
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) {
        return Container(
          height: widget.height,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            gradient: LinearGradient(
              colors: const [Color(0xFFE2E8F0), Color(0xFFF1F5F9), Color(0xFFE2E8F0)],
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