// ============================================================
// POST LIST SCREEN — one grid for "Saved posts", "Explore" and "#hashtag"
//
// Takes a page loader so the three backend list endpoints share the same
// paginated UI (3-column thumbnail grid, infinite scroll, pull-to-refresh).
// Tapping a tile opens the normal `SinglePostPage`.
// ============================================================

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../services/home_api_model_service.dart' show FeedResponse, PostModel;
import '../../utils/api.dart';
import '../../widgets/ls_ui.dart';
import '../services/post_extras_service.dart';
import 'singlepost.dart';

typedef PostPageLoader = Future<FeedResponse> Function(int page);

class PostListScreen extends StatefulWidget {
  final String title;
  final PostPageLoader loader;
  const PostListScreen({super.key, required this.title, required this.loader});

  factory PostListScreen.saved(BuildContext context) => PostListScreen(
        title: AppLocalizations.of(context)!.savedPostsTitle,
        loader: (p) => PostExtrasService.saved(page: p),
      );

  factory PostListScreen.explore(BuildContext context) => PostListScreen(
        title: AppLocalizations.of(context)!.explorePostsTitle,
        loader: (p) => PostExtrasService.explore(page: p),
      );

  factory PostListScreen.hashtag(String tag) => PostListScreen(
        title: '#${tag.startsWith('#') ? tag.substring(1) : tag}',
        loader: (p) => PostExtrasService.hashtag(tag, page: p),
      );

  @override
  State<PostListScreen> createState() => _PostListScreenState();
}

class _PostListScreenState extends State<PostListScreen> {
  final _scroll = ScrollController();
  final List<PostModel> _posts = [];
  int _page = 1;
  bool _hasMore = true;
  bool _loading = true;
  bool _loadingMore = false;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(() {
      if (_scroll.position.pixels > _scroll.position.maxScrollExtent - 400) _more();
    });
    _refresh();
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final f = await widget.loader(1);
      if (!mounted) return;
      setState(() {
        _posts
          ..clear()
          ..addAll(f.results);
        _page = 1;
        _hasMore = f.next != null;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _more() async {
    if (_loadingMore || !_hasMore || _loading) return;
    setState(() => _loadingMore = true);
    try {
      final f = await widget.loader(_page + 1);
      if (!mounted) return;
      setState(() {
        _posts.addAll(f.results);
        _page += 1;
        _hasMore = f.next != null;
      });
    } catch (_) {
      // keep what we have; a later scroll retries
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  String _abs(String u) => u.startsWith('http') ? u : '${Api.baseUrl}$u';

  Widget _tile(PostModel p, ColorScheme cs) {
    final m = p.media.isNotEmpty ? p.media.first : null;
    final thumb = m == null ? null : (m.thumbnail?.isNotEmpty == true ? m.thumbnail! : (m.mediaType == 'image' ? m.file : null));
    return InkWell(
      onTap: () => Navigator.of(context)
          .push(MaterialPageRoute(builder: (_) => SinglePostPage(postId: p.id)))
          .then((_) => _refresh()),
      child: Container(
        color: cs.surfaceVariant,
        child: thumb != null && thumb.isNotEmpty
            ? Stack(fit: StackFit.expand, children: [
                CachedNetworkImage(imageUrl: _abs(thumb), fit: BoxFit.cover, errorWidget: (_, __, ___) => const SizedBox.shrink()),
                if (m?.mediaType == 'video')
                  const Positioned(right: 6, top: 6, child: Icon(Icons.play_circle_fill_rounded, color: Colors.white, size: 20)),
              ])
            : Padding(
                padding: const EdgeInsets.all(8),
                child: Center(
                  child: Text(
                    (p.title?.isNotEmpty == true ? p.title! : (p.content ?? '')).trim(),
                    maxLines: 6,
                    overflow: TextOverflow.ellipsis,
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
                  ),
                ),
              ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: cs.surface,
      appBar: AppBar(
        backgroundColor: cs.surface,
        elevation: 0,
        iconTheme: IconThemeData(color: cs.onSurface),
        title: Text(widget.title, style: LsType.head(context, size: 16)),
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? ListView(children: [
                    const SizedBox(height: 120),
                    Center(child: Text(t.postsLoadFailed, style: TextStyle(color: cs.onSurfaceVariant))),
                    Center(child: TextButton(onPressed: _refresh, child: Text(t.retry))),
                  ])
                : _posts.isEmpty
                    ? ListView(children: [
                        const SizedBox(height: 120),
                        Center(child: Text(t.noPostsHere, style: TextStyle(color: cs.onSurfaceVariant))),
                      ])
                    : GridView.builder(
                        controller: _scroll,
                        physics: const AlwaysScrollableScrollPhysics(),
                        padding: const EdgeInsets.all(2),
                        gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                          crossAxisCount: 3,
                          mainAxisSpacing: 2,
                          crossAxisSpacing: 2,
                        ),
                        itemCount: _posts.length,
                        itemBuilder: (_, i) => _tile(_posts[i], cs),
                      ),
      ),
    );
  }
}
