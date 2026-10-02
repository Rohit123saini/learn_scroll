import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../profile/model.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/profile_media_tiles.dart';
import '../services/hashtag_service.dart';
import 'reels_screen.dart';
import 'singlepost.dart';

// ============================================================
// P7-FE — "#tag" screen: public posts carrying a hashtag (3-column grid, infinite scroll).
// Opened by tapping a #hashtag in a LinkifiedText. If the app already has a hashtag
// screen, point app_link_handlers.dart's openHashtagFeed at it instead and delete this.
// ============================================================

class HashtagPostsScreen extends StatefulWidget {
  final String tag; // with or without '#'
  const HashtagPostsScreen({super.key, required this.tag});

  @override
  State<HashtagPostsScreen> createState() => _HashtagPostsScreenState();
}

class _HashtagPostsScreenState extends State<HashtagPostsScreen> {
  final _scroll = ScrollController();
  List<PostModel> _posts = [];
  String? _next;
  bool _loading = true;
  bool _loadingMore = false;
  bool _failed = false;

  String get _tag => widget.tag.replaceFirst(RegExp(r'^#'), '');

  @override
  void initState() {
    super.initState();
    _scroll.addListener(() {
      if (_scroll.hasClients && _scroll.position.pixels >= _scroll.position.maxScrollExtent - 400) _loadMore();
    });
    _load();
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _failed = false;
    });
    try {
      final page = await HashtagService.getPosts(_tag);
      if (!mounted) return;
      setState(() {
        _posts = page.posts;
        _next = page.next;
        _loading = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _failed = true;
        });
      }
    }
  }

  Future<void> _loadMore() async {
    if (_loadingMore || _next == null || _loading) return;
    setState(() => _loadingMore = true);
    try {
      final page = await HashtagService.getPosts(_tag, nextUrl: _next);
      if (!mounted) return;
      setState(() {
        final seen = _posts.map((p) => p.id).toSet();
        _posts = [..._posts, ...page.posts.where((p) => !seen.contains(p.id))];
        _next = page.next;
        _loadingMore = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  void _open(PostModel p) {
    if (p.postType == 'video' && p.media.isNotEmpty) {
      ReelsScreen.open(context, startPostId: p.id.toString());
    } else {
      Navigator.push(context, MaterialPageRoute(builder: (_) => SinglePostPage(postId: p.id.toString())));
    }
  }

  Widget _tile(PostModel p, ColorScheme cs, AppLocalizations l10n) {
    if (p.media.isNotEmpty && (p.postType == 'image' || p.postType == 'video')) {
      return MediaGridTile(post: p, l10n: l10n, onTap: () => _open(p));
    }
    final caption = (p.title?.isNotEmpty == true) ? p.title! : p.content;
    return InkWell(
      borderRadius: BorderRadius.circular(10),
      onTap: () => _open(p),
      child: Container(
        decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(10)),
        padding: const EdgeInsets.all(8),
        alignment: Alignment.center,
        child: Text(caption.trim(),
            maxLines: 6,
            overflow: TextOverflow.ellipsis,
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    Widget body;
    if (_loading) {
      body = const Center(child: CircularProgressIndicator(strokeWidth: 2));
    } else if (_failed) {
      body = ErrorStateWidget(
        title: l10n.postsLoadErrorTitle,
        subtitle: l10n.postsLoadErrorSubtitle,
        retryLabel: l10n.retry,
        onRetry: _load,
      );
    } else if (_posts.isEmpty) {
      body = EmptyStateWidget(
        icon: Icons.tag_rounded,
        title: 'No posts for #$_tag yet',
        subtitle: 'Public posts with this hashtag will show up here.',
      );
    } else {
      body = RefreshIndicator(
        onRefresh: _load,
        child: GridView.builder(
          controller: _scroll,
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(kLsPad),
          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 3,
            crossAxisSpacing: 6,
            mainAxisSpacing: 6,
          ),
          itemCount: _posts.length,
          itemBuilder: (_, i) => _tile(_posts[i], cs, l10n),
        ),
      );
    }

    return Scaffold(
      backgroundColor: lsBg(context),
      appBar: AppBar(title: Text('#$_tag')),
      body: body,
    );
  }
}
