// message/screens/group_media_screen.dart
//
// 🔥 NAYA — WhatsApp/Telegram-style "Media, links and docs" gallery for a
// group. Backend endpoint (`GET /message/groups/<id>/media/`, optional
// `?type=` filter — GroupViewSet.media, §5 of backend doc) aur frontend
// `MessageApiService.getGroupMedia` dono already the — bas koi screen isko
// call hi nahi karti thi. Ye screen wahi missing surface hai, `
// group_profile_screen.dart`'s naye "Media, links and docs" card se pushed.
//
// Images/videos ek grid me (thumbnail_url ?? file_url), baaki files (docs/
// presentations/audio) neeche ek list me — "All" tab dono ek saath dikhata
// hai. Har filter tab apna result cache karta hai taaki tab switch karne
// par baar-baar API call na ho.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:url_launcher/url_launcher.dart';

import '../services/message_api_service.dart';
import 'media_viewer_screen.dart';

class GroupMediaScreen extends StatefulWidget {
  final String groupId;
  const GroupMediaScreen({super.key, required this.groupId});

  @override
  State<GroupMediaScreen> createState() => _GroupMediaScreenState();
}

class _MediaFilter {
  static const all = 'all';
  static const image = 'image';
  static const video = 'video';
  static const file = 'file';
  static const values = [all, image, video, file];
}

class _GroupMediaScreenState extends State<GroupMediaScreen> with SingleTickerProviderStateMixin {
  static const _kNavy = Color(0xFF030F27);
  static const _kAccent = Color(0xFFEE0979);

  late final TabController _tabController;
  final Map<String, List<dynamic>> _cache = {}; // filter -> raw GroupMedia items
  bool _loading = true;
  String? _error;
  String _filter = _MediaFilter.all;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: _MediaFilter.values.length, vsync: this)
      ..addListener(_onTabChanged);
    _load();
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  void _onTabChanged() {
    if (_tabController.indexIsChanging) return;
    final next = _MediaFilter.values[_tabController.index];
    if (next == _filter) return;
    setState(() => _filter = next);
    _load();
  }

  Future<void> _load({bool forceRefresh = false}) async {
    if (forceRefresh) _cache.remove(_filter);
    if (_cache.containsKey(_filter)) {
      setState(() {
        _loading = false;
        _error = null;
      });
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final type = _filter == _MediaFilter.all ? null : _filter;
      final items = await MessageApiService.getGroupMedia(widget.groupId, type: type);
      if (!mounted) return;
      setState(() {
        _cache[_filter] = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = "Media load nahi ho payi: $e";
        _loading = false;
      });
    }
  }

  String? _str(dynamic item, String key) {
    if (item is Map && item[key] != null) return item[key].toString();
    return null;
  }

  Future<void> _openUrl(String url) async {
    try {
      await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Open nahi ho paya: $e")));
    }
  }

  @override
  Widget build(BuildContext context) {
    final items = _cache[_filter] ?? const [];
    return Scaffold(
      backgroundColor: Colors.grey[100],
      appBar: AppBar(
        backgroundColor: _kNavy,
        foregroundColor: Colors.white,
        elevation: 0,
        title: const Text("Media, links and docs", style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
        bottom: TabBar(
          controller: _tabController,
          indicatorColor: _kAccent,
          labelColor: Colors.white,
          unselectedLabelColor: Colors.white60,
          tabs: const [Tab(text: "All"), Tab(text: "Photos"), Tab(text: "Videos"), Tab(text: "Files")],
        ),
      ),
      body: RefreshIndicator(
        onRefresh: () => _load(forceRefresh: true),
        child: _loading
            ? const Center(child: CircularProgressIndicator(color: _kNavy))
            : _error != null
                ? _buildErrorState()
                : items.isEmpty
                    ? _buildEmptyState()
                    : _buildContent(items),
      ),
    );
  }

  Widget _buildErrorState() {
    return ListView(children: [
      Padding(
        padding: const EdgeInsets.all(32),
        child: Center(child: Text(_error!, style: const TextStyle(color: Colors.red, fontSize: 13), textAlign: TextAlign.center)),
      ),
    ]);
  }

  Widget _buildEmptyState() {
    return ListView(children: [
      Padding(
        padding: const EdgeInsets.only(top: 100),
        child: Center(
          child: Column(children: [
            Icon(Icons.perm_media_outlined, size: 48, color: Colors.grey[400]),
            const SizedBox(height: 12),
            Text("Ab tak koi media share nahi hua", style: TextStyle(color: Colors.grey[500], fontSize: 13.5)),
          ]),
        ),
      ),
    ]);
  }

  Widget _buildContent(List<dynamic> items) {
    final visual = items.where((it) => _str(it, 'file_type') == 'image' || _str(it, 'file_type') == 'video').toList();
    final files = items.where((it) => !(_str(it, 'file_type') == 'image' || _str(it, 'file_type') == 'video')).toList();

    return ListView(
      padding: const EdgeInsets.all(2),
      children: [
        if (visual.isNotEmpty)
          GridView.builder(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            padding: const EdgeInsets.all(2),
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 3,
              crossAxisSpacing: 2,
              mainAxisSpacing: 2,
            ),
            itemCount: visual.length,
            itemBuilder: (_, i) => _buildVisualTile(visual, i),
          ),
        if (files.isNotEmpty) ...[
          if (visual.isNotEmpty) const Divider(height: 16),
          ...files.map((f) => _buildFileTile(f)),
        ],
      ],
    );
  }

  Widget _buildVisualTile(List<dynamic> visual, int index) {
    final it = visual[index];
    final isVideo = _str(it, 'file_type') == 'video';
    final thumb = _str(it, 'thumbnail_url') ?? _str(it, 'file_url');
    final fileUrl = _str(it, 'file_url');

    return GestureDetector(
      onTap: () {
        // Photos ek-doosre ke saath swipeable viewer me khulti hain
        // (`MediaViewerScreen` — chat me use hone wala wahi screen, isi
        // gallery ke images ki list ke saath). Video seedha external
        // player/browser me — is screen ke andar koi inline player nahi
        // banaya (chat ka `_AudioBubble`/video player private classes hain,
        // yahan reuse nahi ho sakte).
        if (isVideo) {
          if (fileUrl != null) _openUrl(fileUrl);
          return;
        }
        final imageUrls = visual
            .where((v) => _str(v, 'file_type') == 'image' && _str(v, 'file_url') != null)
            .map((v) => _str(v, 'file_url')!)
            .toList();
        final tapIndex = imageUrls.indexOf(fileUrl ?? '');
        Navigator.push(
          context,
          MaterialPageRoute(
            builder: (_) => MediaViewerScreen(
              urls: imageUrls,
              initialIndex: tapIndex < 0 ? 0 : tapIndex,
              onDownload: (u) => _openUrl(u),
              isDownloaded: (u) => false,
            ),
          ),
        );
      },
      child: Stack(fit: StackFit.expand, children: [
        if (thumb != null)
          CachedNetworkImage(
            imageUrl: thumb,
            fit: BoxFit.cover,
            placeholder: (_, __) => Container(color: Colors.grey[300]),
            errorWidget: (_, __, ___) => Container(color: Colors.grey[300], child: const Icon(Icons.broken_image_outlined, color: Colors.grey)),
          )
        else
          Container(color: Colors.grey[300]),
        if (isVideo)
          const Center(child: Icon(Icons.play_circle_fill_rounded, color: Colors.white, size: 32)),
      ]),
    );
  }

  Widget _buildFileTile(dynamic it) {
    final fileUrl = _str(it, 'file_url');
    final fileType = _str(it, 'file_type') ?? 'file';
    final sizeBytes = it is Map ? it['file_size'] : null;
    return ListTile(
      leading: CircleAvatar(backgroundColor: Colors.grey[200], child: Icon(_iconForType(fileType), color: _kNavy)),
      title: Text(
        _fileNameFromUrl(fileUrl) ?? "File",
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600),
      ),
      subtitle: sizeBytes is num ? Text(_fmtBytes(sizeBytes.toInt()), style: TextStyle(fontSize: 11.5, color: Colors.grey[600])) : null,
      trailing: const Icon(Icons.open_in_new_rounded, size: 18, color: Colors.grey),
      onTap: fileUrl != null ? () => _openUrl(fileUrl) : null,
    );
  }

  IconData _iconForType(String type) {
    switch (type) {
      case 'audio':
        return Icons.audiotrack_rounded;
      case 'presentation':
        return Icons.slideshow_rounded;
      default:
        return Icons.insert_drive_file_rounded;
    }
  }

  String? _fileNameFromUrl(String? url) {
    if (url == null || url.isEmpty) return null;
    final clean = url.split('?').first;
    final parts = clean.split('/');
    return parts.isNotEmpty ? parts.last : null;
  }

  String _fmtBytes(int bytes) {
    if (bytes <= 0) return '';
    const units = ['B', 'KB', 'MB', 'GB'];
    var size = bytes.toDouble();
    var i = 0;
    while (size >= 1024 && i < units.length - 1) {
      size /= 1024;
      i++;
    }
    return "${size.toStringAsFixed(size < 10 && i > 0 ? 1 : 0)} ${units[i]}";
  }
}