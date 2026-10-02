// message/screens/chat_media_screen.dart
//
// 🔥 NAYA (M8-FE) — "Media, links and docs" library, har chat ke liye
// (private + group dono). Backend: `GET /message/conversations/<id>/media/
// ?type=media|links|docs&page=N` (ConversationViewSet.media, search_utils.
// build_library_items). `GroupMediaScreen` sirf group ke `GroupMedia` table pe
// chalti hai aur "Links" nahi deti — ye screen seedha Message table se chalti hai.
//
// - 3 tabs: Media (grid) / Links (preview tiles) / Docs (file list)
// - Har tab apna page-wise result alag cache karta hai, pehli baar khulne par
//   hi load hota hai; neeche scroll karne par agla page aata hai.
// - Pull-to-refresh har tab pe.

import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:intl/intl.dart' show DateFormat;

import '../services/message_api_service.dart';
import 'media_viewer_screen.dart';
import '../../theme_service.dart'; // 🎨 THEME FIX — AppThemeTokens

/// `name` seedha backend ke `type=` query-param se match karta hai.
enum ChatMediaTab { media, links, docs }

class ChatMediaScreen extends StatefulWidget {
  final String conversationId;
  final ChatMediaTab initialTab;
  const ChatMediaScreen({
    super.key,
    required this.conversationId,
    this.initialTab = ChatMediaTab.media,
  });

  @override
  State<ChatMediaScreen> createState() => _ChatMediaScreenState();
}

class _TabData {
  final List<Map<String, dynamic>> items = [];
  int nextPage = 1;
  bool hasMore = true;
  bool loading = false; // pehla page / refresh
  bool loadingMore = false; // agla page
  bool loaded = false;
  String? error;
}

class _ChatMediaScreenState extends State<ChatMediaScreen> with SingleTickerProviderStateMixin {
  late final TabController _tabController;
  final Map<ChatMediaTab, _TabData> _data = {
    for (final t in ChatMediaTab.values) t: _TabData(),
  };

  @override
  void initState() {
    super.initState();
    _tabController = TabController(
      length: ChatMediaTab.values.length,
      vsync: this,
      initialIndex: widget.initialTab.index,
    )..addListener(_onTabChanged);
    _ensureLoaded(widget.initialTab);
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  void _onTabChanged() {
    if (_tabController.indexIsChanging) return;
    _ensureLoaded(ChatMediaTab.values[_tabController.index]);
  }

  void _ensureLoaded(ChatMediaTab tab) {
    final d = _data[tab]!;
    if (!d.loaded && !d.loading) _fetch(tab, refresh: true);
  }

  Future<void> _fetch(ChatMediaTab tab, {bool refresh = false}) async {
    final d = _data[tab]!;
    if (d.loading || d.loadingMore) return;
    if (!refresh && !d.hasMore) return;

    final page = refresh ? 1 : d.nextPage;
    setState(() {
      if (refresh) {
        d.loading = true;
      } else {
        d.loadingMore = true;
      }
      d.error = null;
    });

    try {
      // VERIFY: MessageApiService.getConversationMedia — neeche patch notes dekho.
      // Ye poora paginated map (`results`, `next`, ...) return karta hai.
      final res = await MessageApiService.getConversationMedia(
        widget.conversationId,
        type: tab.name,
        page: page,
      );
      if (!mounted) return;
      final raw = res['results'];
      final results = (raw is List ? raw : const [])
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .toList();
      setState(() {
        if (refresh) d.items.clear();
        d.items.addAll(results);
        d.hasMore = res['next'] != null; // item-count pe nahi, `next` pe bharosa (pagination message-level hai)
        d.nextPage = page + 1;
        d.loaded = true;
        d.loading = false;
        d.loadingMore = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        d.error = "Load nahi ho paya: $e"; // TODO(l10n)
        d.loading = false;
        d.loadingMore = false;
      });
    }
  }

  // ------------------------------------------------------------------ helpers

  String? _str(dynamic m, String key) {
    if (m is Map && m[key] != null) {
      final s = m[key].toString();
      return s.isEmpty ? null : s;
    }
    return null;
  }

  Future<void> _openUrl(String url) async {
    try {
      await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Open nahi ho paya: $e"))); // TODO(l10n)
    }
  }

  String _metaLine(Map<String, dynamic> it, {String? extra}) {
    final parts = <String>[];
    final sender = _str(it, 'sender_username');
    if (sender != null) parts.add(sender);
    final dt = DateTime.tryParse(_str(it, 'created_at') ?? '')?.toLocal();
    if (dt != null) parts.add(DateFormat('d MMM yyyy').format(dt));
    if (extra != null && extra.isNotEmpty) parts.add(extra);
    return parts.join(' · ');
  }

  String? _fileNameFromUrl(String? url) {
    if (url == null || url.isEmpty) return null;
    final parts = url.split('?').first.split('/');
    return parts.isNotEmpty && parts.last.isNotEmpty ? Uri.decodeComponent(parts.last) : null;
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

  // -------------------------------------------------------------------- build

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: cs.primary,
        foregroundColor: cs.onPrimary,
        elevation: 0,
        title: const Text("Media, links and docs", style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16)), // TODO(l10n)
        bottom: TabBar(
          controller: _tabController,
          indicatorColor: AppThemeTokens.of(context).coral,
          labelColor: cs.onPrimary,
          unselectedLabelColor: cs.onPrimary.withOpacity(0.6),
          tabs: const [Tab(text: "Media"), Tab(text: "Links"), Tab(text: "Docs")], // TODO(l10n)
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: ChatMediaTab.values.map(_buildTab).toList(),
      ),
    );
  }

  Widget _buildTab(ChatMediaTab tab) {
    final d = _data[tab]!;
    final cs = Theme.of(context).colorScheme;

    Widget body;
    if ((d.loading || !d.loaded) && d.items.isEmpty && d.error == null) {
      body = ListView(physics: const AlwaysScrollableScrollPhysics(), children: [
        Padding(
          padding: const EdgeInsets.only(top: 120),
          child: Center(child: CircularProgressIndicator(color: cs.primary)),
        ),
      ]);
    } else if (d.error != null && d.items.isEmpty) {
      body = _stateView(
        icon: Icons.error_outline_rounded,
        text: d.error!,
        color: cs.error,
        action: TextButton(onPressed: () => _fetch(tab, refresh: true), child: const Text("Dobara try karo")), // TODO(l10n)
      );
    } else if (d.items.isEmpty) {
      body = _stateView(
        icon: tab == ChatMediaTab.links
            ? Icons.link_off_rounded
            : (tab == ChatMediaTab.docs ? Icons.folder_open_rounded : Icons.perm_media_outlined),
        text: tab == ChatMediaTab.media
            ? "Ab tak koi photo/video share nahi hui"
            : tab == ChatMediaTab.links
                ? "Ab tak koi link share nahi hua"
                : "Ab tak koi document share nahi hua", // TODO(l10n)
        color: cs.onSurfaceVariant,
      );
    } else {
      body = tab == ChatMediaTab.media ? _buildMediaGrid(tab) : _buildList(tab);
    }

    return RefreshIndicator(
      onRefresh: () => _fetch(tab, refresh: true),
      child: NotificationListener<ScrollNotification>(
        onNotification: (n) {
          // Neeche ~400px bachte hi agla page.
          if (n.metrics.axis == Axis.vertical && n.metrics.extentAfter < 400) _fetch(tab);
          return false;
        },
        child: body,
      ),
    );
  }

  Widget _stateView({required IconData icon, required String text, required Color color, Widget? action}) {
    return ListView(physics: const AlwaysScrollableScrollPhysics(), children: [
      Padding(
        padding: const EdgeInsets.only(top: 100, left: 32, right: 32),
        child: Column(children: [
          Icon(icon, size: 48, color: color),
          const SizedBox(height: 12),
          Text(text, textAlign: TextAlign.center, style: TextStyle(color: color, fontSize: 13.5)),
          if (action != null) ...[const SizedBox(height: 8), action],
        ]),
      ),
    ]);
  }

  Widget _buildFooter(ChatMediaTab tab) {
    final d = _data[tab]!;
    final cs = Theme.of(context).colorScheme;
    if (d.loadingMore) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: Center(
          child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: cs.primary)),
        ),
      );
    }
    if (d.error != null) {
      return TextButton(onPressed: () => _fetch(tab), child: const Text("Load nahi hua — dobara try karo")); // TODO(l10n)
    }
    if (d.hasMore) {
      // Agar pehla page screen na bhare to scroll-trigger kabhi fire nahi hoga — isliye button bhi.
      return TextButton(onPressed: () => _fetch(tab), child: const Text("Aur dikhao")); // TODO(l10n)
    }
    return const SizedBox(height: 16);
  }

  // -------------------------------------------------------------------- Media

  Widget _buildMediaGrid(ChatMediaTab tab) {
    final items = _data[tab]!.items;
    return CustomScrollView(
      physics: const AlwaysScrollableScrollPhysics(),
      slivers: [
        SliverPadding(
          padding: const EdgeInsets.all(2),
          sliver: SliverGrid(
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 3,
              crossAxisSpacing: 2,
              mainAxisSpacing: 2,
            ),
            delegate: SliverChildBuilderDelegate(
              (_, i) => _buildVisualTile(items, i),
              childCount: items.length,
            ),
          ),
        ),
        SliverToBoxAdapter(child: _buildFooter(tab)),
      ],
    );
  }

  Widget _buildVisualTile(List<Map<String, dynamic>> visual, int index) {
    final it = visual[index];
    final isVideo = _str(it, 'file_type') == 'video';
    final fileUrl = _str(it, 'file_url');
    final thumb = _str(it, 'thumbnail_url') ?? (isVideo ? null : fileUrl);
    final tokens = AppThemeTokens.of(context);

    return GestureDetector(
      onTap: () {
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
            placeholder: (_, __) => Container(color: tokens.surface2),
            errorWidget: (_, __, ___) => Container(
              color: tokens.surface2,
              child: Icon(Icons.broken_image_outlined, color: Theme.of(context).colorScheme.onSurfaceVariant),
            ),
          )
        else
          Container(color: tokens.surface2),
        if (isVideo)
          const Center(child: Icon(Icons.play_circle_fill_rounded, color: Colors.white, size: 32)), // media overlay convention — fixed white
      ]),
    );
  }

  // ------------------------------------------------------------ Links / Docs

  Widget _buildList(ChatMediaTab tab) {
    final items = _data[tab]!.items;
    return ListView.builder(
      physics: const AlwaysScrollableScrollPhysics(),
      itemCount: items.length + 1,
      itemBuilder: (_, i) {
        if (i == items.length) return _buildFooter(tab);
        return tab == ChatMediaTab.links ? _buildLinkTile(items[i]) : _buildFileTile(items[i]);
      },
    );
  }

  Widget _buildLinkTile(Map<String, dynamic> it) {
    final cs = Theme.of(context).colorScheme;
    final tokens = AppThemeTokens.of(context);
    final url = _str(it, 'url');
    final domain = _str(it, 'domain');
    // VERIFY: link_preview ke keys (title / image) — tasks.generate_link_preview_task jo store karta hai wahi.
    final preview = it['link_preview'];
    final title = _str(preview, 'title') ?? domain ?? url ?? "Link";
    final image = _str(preview, 'image') ?? _str(preview, 'image_url');

    return ListTile(
      leading: ClipRRect(
        borderRadius: BorderRadius.circular(8),
        child: SizedBox(
          width: 48,
          height: 48,
          child: image != null
              ? CachedNetworkImage(
                  imageUrl: image,
                  fit: BoxFit.cover,
                  placeholder: (_, __) => Container(color: tokens.surface2),
                  errorWidget: (_, __, ___) => Container(color: tokens.surface2, child: Icon(Icons.link_rounded, color: cs.primary)),
                )
              : Container(color: tokens.surface2, child: Icon(Icons.link_rounded, color: cs.primary)),
        ),
      ),
      title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600)),
      subtitle: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (url != null)
          Text(url, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12, color: cs.primary)),
        Text(_metaLine(it), maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
      ]),
      isThreeLine: true,
      onTap: url != null ? () => _openUrl(url) : null,
    );
  }

  Widget _buildFileTile(Map<String, dynamic> it) {
    final cs = Theme.of(context).colorScheme;
    final fileUrl = _str(it, 'file_url');
    final fileType = _str(it, 'file_type') ?? 'file';
    final size = it['file_size'];
    final name = _str(it, 'file_name') ?? _fileNameFromUrl(fileUrl) ?? "File";

    return ListTile(
      leading: CircleAvatar(
        backgroundColor: AppThemeTokens.of(context).surface2,
        child: Icon(_iconForType(fileType), color: cs.primary),
      ),
      title: Text(name, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600)),
      subtitle: Text(
        _metaLine(it, extra: size is num ? _fmtBytes(size.toInt()) : null),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
      ),
      trailing: Icon(Icons.open_in_new_rounded, size: 18, color: cs.onSurfaceVariant),
      onTap: fileUrl != null ? () => _openUrl(fileUrl) : null,
    );
  }
}
