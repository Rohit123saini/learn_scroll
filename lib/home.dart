
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:timeago/timeago.dart' as timeago;
import 'package:video_player/video_player.dart';
import 'package:share_plus/share_plus.dart';
import 'package:visibility_detector/visibility_detector.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:image_picker/image_picker.dart';
import 'package:image/image.dart' as imgLib;
import 'package:open_filex/open_filex.dart';
import 'package:permission_handler/permission_handler.dart';

import '../profile/screens/profile.dart';
import 'search/search.dart';
import 'post/screens/new_post.dart';
import '../services/home_api_model_service.dart';
import '../services/comment_service.dart';

class FilterModel {
  final String name;
  final List<double> matrix;
  FilterModel({required this.name, required this.matrix});
}

final List<FilterModel> appFilters = [
  FilterModel(name: "Original", matrix: [1,0,0,0,0, 0,1,0,0,0, 0,0,1,0,0, 0,0,0,1,0]),
  FilterModel(name: "B&W", matrix: [0.2126,0.7152,0.0722,0,0, 0.2126,0.7152,0.0722,0,0, 0.2126,0.7152,0.0722,0,0, 0,0,0,1,0]),
  FilterModel(name: "Sepia", matrix: [0.393,0.769,0.189,0,0, 0.349,0.686,0.168,0,0, 0.272,0.534,0.131,0,0, 0,0,0,1,0]),
  FilterModel(name: "Vivid", matrix: [1.2,0,0,0,0, 0,1.2,0,0,0, 0,0,1.2,0,0, 0,0,0,1,0]),
  FilterModel(name: "Warm", matrix: [1.3,0,0,0,0, 0,1.1,0,0,0, 0,0,0.9,0,0, 0,0,0,1,0]),
];

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _selectedIndex = 0;
  List<PostModel> _posts = [];
  bool _isLoading = true;
  bool _isLoadingMore = false;
  int _currentPage = 1;
  bool _hasMore = true;
  final ScrollController _scrollController = ScrollController();
  final Map<String, String> _emojiMap = {'like': '👍','confuse': '🤔','wrong': '❗','imp': '⭐','explain': '💡'};
  final Map<String, Color> _emojiColor = {'like': Color(0xFF1877F2),'confuse': Color(0xFFF7B928),'wrong': Color(0xFFE0245E),'imp': Color(0xFFFFAD33),'explain': Color(0xFF45BD62)};

  @override void initState() { super.initState(); _loadFeed(); _scrollController.addListener(_onScroll); }
  @override void dispose() { _scrollController.dispose(); super.dispose(); }
  void _onScroll() { if (_scrollController.position.pixels >= _scrollController.position.maxScrollExtent - 300) { if (!_isLoadingMore && _hasMore) _loadMore(); } }

  Future<void> _loadFeed({bool refresh = false}) async {
    if (refresh) setState(() { _isLoading = true; _currentPage = 1; });
    try {
      final feed = await HomeFeedService.getHomeFeed(page: 1, pageSize: 20);
      setState(() { _posts = feed.results; _isLoading = false; _hasMore = feed.next!= null; });
    } catch (e) { setState(() => _isLoading = false); }
  }

  Future<void> _loadMore() async {
    if (_isLoadingMore ||!_hasMore) return;
    setState(() => _isLoadingMore = true); _currentPage++;
    try {
      final feed = await HomeFeedService.getHomeFeed(page: _currentPage, pageSize: 20);
      setState(() { _posts.addAll(feed.results); _isLoadingMore = false; _hasMore = feed.next!= null; });
    } catch (e) { _currentPage--; setState(() => _isLoadingMore = false); }
  }

  void _onItemTapped(int i) => setState(() => _selectedIndex = i);
  void _sharePost(PostModel p) { String t = p.content?? ''; if (p.media.isNotEmpty) t += '\n\n${p.media.first.file}'; Share.share(t); }

  void _openCommentSheet(PostModel post) {
    showModalBottomSheet(
      context: context, isScrollControlled: true, backgroundColor: Colors.transparent,
      builder: (_) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
        child: Container(
          decoration: const BoxDecoration(color: Colors.white, borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
          child: CommentBottomSheet(post: post, onCommentAdded: () => setState(() => post.commentsCount++)),
        ),
      ),
    );
  }

  void _showReactionSheet(PostModel post) {
    showModalBottomSheet(
      context: context, backgroundColor: Colors.white, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (c) => Container(
        padding: const EdgeInsets.symmetric(vertical: 20, horizontal: 10),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(width: 40, height: 4, decoration: BoxDecoration(color: Colors.grey[300], borderRadius: BorderRadius.circular(10))),
          const SizedBox(height: 20),
          Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: _emojiMap.entries.map((e) {
            bool sel = post.myReaction == e.key;
            return GestureDetector(onTap: () { Navigator.pop(context); _handleReaction(post, e.key); }, child: Container(padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: sel? _emojiColor[e.key]!.withOpacity(0.15) : Colors.grey.shade100, shape: BoxShape.circle, border: sel? Border.all(color: _emojiColor[e.key]!, width: 2) : null), child: Text(e.value, style: const TextStyle(fontSize: 30))));
          }).toList()),
        ]),
      ),
    );
  }

  Future<void> _handleReaction(PostModel post, String reaction) async {
    final old = post.myReaction; final idx = _posts.indexWhere((p) => p.id == post.id); if (idx == -1) return;
    setState(() {
      if (old == reaction) { _posts[idx].myReaction = null; _posts[idx].likesCount--; if (reaction == 'like') _posts[idx].likeCount--; if (reaction == 'confuse') _posts[idx].confuseCount--; if (reaction == 'wrong') _posts[idx].wrongCount--; if (reaction == 'imp') _posts[idx].impCount--; if (reaction == 'explain') _posts[idx].explainCount--; }
      else { if (old!= null) { if (old == 'like') _posts[idx].likeCount--; if (old == 'confuse') _posts[idx].confuseCount--; if (old == 'wrong') _posts[idx].wrongCount--; if (old == 'imp') _posts[idx].impCount--; if (old == 'explain') _posts[idx].explainCount--; } else { _posts[idx].likesCount++; } _posts[idx].myReaction = reaction; if (reaction == 'like') _posts[idx].likeCount++; if (reaction == 'confuse') _posts[idx].confuseCount++; if (reaction == 'wrong') _posts[idx].wrongCount++; if (reaction == 'imp') _posts[idx].impCount++; if (reaction == 'explain') _posts[idx].explainCount++; }
    });
    try { final res = await HomeFeedService.toggleReaction(post.id, reaction); final c = res['counts']; setState(() { _posts[idx].likeCount = c['like']; _posts[idx].confuseCount = c['confuse']; _posts[idx].wrongCount = c['wrong']; _posts[idx].impCount = c['imp']; _posts[idx].explainCount = c['explain']; _posts[idx].likesCount = c['total']; _posts[idx].myReaction = res['my_reaction']; }); } catch (e) { setState(() => _posts[idx].myReaction = old); }
  }

  Widget _homePage() {
    return Container(color: const Color(0xFFF0F2F5), child: RefreshIndicator(onRefresh: () => _loadFeed(refresh: true), child: CustomScrollView(controller: _scrollController, slivers: [
      SliverAppBar(automaticallyImplyLeading: false, backgroundColor: const Color(0xFF030F27), toolbarHeight: 65, floating: true, snap: true, title: Row(children: [Image.asset('assets/slogo1.png', height: 42, errorBuilder: (_, __, ___) => const Text("LearnScroll", style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 22)))]), actions: [IconButton(onPressed: () async { await Navigator.push(context, MaterialPageRoute(builder: (_) => const NewPost())); _loadFeed(refresh: true); }, icon: Container(padding: const EdgeInsets.all(6), decoration: const BoxDecoration(color: Colors.white24, shape: BoxShape.circle), child: const Icon(Icons.add, color: Colors.white, size: 20))), const SizedBox(width: 12)]),
      if (_isLoading && _posts.isEmpty) const SliverFillRemaining(child: Center(child: CircularProgressIndicator()))
      else SliverList(delegate: SliverChildBuilderDelegate((context, index) { if (index < _posts.length) return _buildPostCard(_posts[index], index); return const Padding(padding: EdgeInsets.all(16), child: Center(child: CircularProgressIndicator())); }, childCount: _posts.length + (_hasMore? 1 : 0))),
      const SliverToBoxAdapter(child: SizedBox(height: 80)),
    ])));
  }

  Widget _buildPostCard(PostModel post, int idx) {
    List<MapEntry<String, int>> sorted = [MapEntry('like', post.likeCount), MapEntry('confuse', post.confuseCount), MapEntry('wrong', post.wrongCount), MapEntry('imp', post.impCount), MapEntry('explain', post.explainCount)];
    sorted.sort((a, b) => b.value.compareTo(a.value)); var top3 = sorted.where((e) => e.value > 0).take(3).toList();
    return Container(margin: const EdgeInsets.only(bottom: 10), color: Colors.white, child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Padding(padding: const EdgeInsets.fromLTRB(12, 12, 8, 8), child: Row(children: [CircleAvatar(radius: 20, backgroundColor: Colors.grey[300], backgroundImage: post.user.profilePicture!= null && post.user.profilePicture!.isNotEmpty? CachedNetworkImageProvider(post.user.profilePicture!) : null), const SizedBox(width: 10), Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(post.user.username, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15)), Text(timeago.format(post.createdAt), style: TextStyle(fontSize: 12, color: Colors.grey[600]))])), const Icon(Icons.more_horiz)])),
      if (post.content!= null && post.content!.isNotEmpty) Padding(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6), child: Text(post.content!, style: const TextStyle(fontSize: 15, height: 1.3))),
      if (post.hashtags.isNotEmpty) Padding(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 2), child: Wrap(spacing: 6, children: post.hashtags.map((t) => Text('#$t', style: const TextStyle(color: Color(0xFF1877F2), fontWeight: FontWeight.w500))).toList())),
      const SizedBox(height: 6),
      if (post.media.isNotEmpty) _MediaCarousel(mediaList: post.media, postId: post.id, postIndex: idx),
      if (post.likesCount > 0 || post.commentsCount > 0) Padding(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10), child: Row(children: [if (top3.isNotEmpty) Row(children: top3.map((e) => Padding(padding: const EdgeInsets.only(right: 3), child: Text(_emojiMap[e.key]?? '', style: const TextStyle(fontSize: 16)))).toList()), const SizedBox(width: 6), if (post.likesCount > 0) Text('${post.likesCount}', style: TextStyle(fontSize: 14, color: Colors.grey[700])), const Spacer(), if (post.commentsCount > 0) Text('${post.commentsCount} comments', style: TextStyle(fontSize: 13, color: Colors.grey[600]))])),
      const Divider(height: 1, thickness: 0.5),
      Padding(padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2), child: Row(children: [
        Expanded(child: InkWell(onTap: () => _handleReaction(post, 'like'), onLongPress: () => _showReactionSheet(post), child: Container(padding: const EdgeInsets.symmetric(vertical: 12), child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [if (post.myReaction == null)...[const Icon(Icons.thumb_up_alt_outlined, size: 20, color: Color(0xFF65676B)), const SizedBox(width: 6), const Text('Like', style: TextStyle(color: Color(0xFF65676B), fontWeight: FontWeight.w600))] else...[Text(_emojiMap[post.myReaction]?? '👍', style: const TextStyle(fontSize: 18)), const SizedBox(width: 6), Text(post.myReaction!.toUpperCase(), style: TextStyle(color: _emojiColor[post.myReaction], fontWeight: FontWeight.bold, fontSize: 12))]])))),
        Expanded(child: InkWell(onTap: () => _openCommentSheet(post), child: Container(padding: const EdgeInsets.symmetric(vertical: 12), child: const Row(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.chat_bubble_outline, size: 20, color: Color(0xFF65676B)), SizedBox(width: 6), Text('Comment', style: TextStyle(color: Color(0xFF65676B), fontWeight: FontWeight.w600))])))),
        Expanded(child: InkWell(onTap: () => _sharePost(post), child: Container(padding: const EdgeInsets.symmetric(vertical: 12), child: const Row(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.share_outlined, size: 20, color: Color(0xFF65676B)), SizedBox(width: 6), Text('Share', style: TextStyle(color: Color(0xFF65676B), fontWeight: FontWeight.w600))])))),
      ])),
    ]));
  }

  @override Widget build(BuildContext context) {
    return Scaffold(backgroundColor: const Color(0xFFF0F2F5), body: IndexedStack(index: _selectedIndex, children: [_homePage(), const SearchScreen(), ProfileScreen(onBackToHome: () => setState(() => _selectedIndex = 0))]), bottomNavigationBar: BottomNavigationBar(currentIndex: _selectedIndex, onTap: _onItemTapped, backgroundColor: const Color(0xFF030F27), selectedItemColor: Colors.white, unselectedItemColor: Colors.white60, type: BottomNavigationBarType.fixed, items: const [BottomNavigationBarItem(icon: Icon(Icons.home_outlined), activeIcon: Icon(Icons.home), label: "Home"), BottomNavigationBarItem(icon: Icon(Icons.search), label: "Search"), BottomNavigationBarItem(icon: Icon(Icons.person_outline), activeIcon: Icon(Icons.person), label: "Profile")]));
  }
}

class _MediaCarousel extends StatefulWidget {
  final List<PostMediaModel> mediaList; final String postId; final int postIndex;
  const _MediaCarousel({required this.mediaList, required this.postId, required this.postIndex});
  @override State<_MediaCarousel> createState() => _MediaCarouselState();
}

class _MediaCarouselState extends State<_MediaCarousel> {
  late PageController _pageController; int _currentPage = 0; bool _showDots = true; final Map<int, VideoPlayerController> _videoControllers = {};
  @override void initState() { super.initState(); _pageController = PageController(); _initVideos(); if (widget.mediaList.length > 1) Future.delayed(const Duration(seconds: 4), () { if (mounted) setState(() => _showDots = false); }); }
  void _initVideos() { for (int i = 0; i < widget.mediaList.length; i++) { if (widget.mediaList[i].mediaType == 'video') { final c = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file)); _videoControllers[i] = c; c.initialize().then((_) { if (mounted) setState(() {}); }); } } }
  @override void dispose() { _pageController.dispose(); for (var c in _videoControllers.values) { c.dispose(); } super.dispose(); }
  void _handleVisibility(bool v, int i) { final c = _videoControllers[i]; if (c!= null && c.value.isInitialized) { if (v && _currentPage == i) { c.play(); c.setLooping(true); } else { c.pause(); } } }
  bool _isPDF(String u) => u.toLowerCase().endsWith('.pdf');

  Future<void> _downloadFile(String url, String fileName) async {
    try {
      if (Platform.isAndroid) await Permission.storage.request();
      Directory dir = Directory('/storage/emulated/0/Download');
      if (!await dir.exists()) dir = await getApplicationDocumentsDirectory() as Directory;
      String savePath = '${dir.path}/$fileName';
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Downloading $fileName...')));
      await Dio().download(url, savePath);
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Saved to Download/$fileName'), backgroundColor: Colors.green, action: SnackBarAction(label: 'OPEN', onPressed: () => OpenFilex.open(savePath))));
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed: $e'), backgroundColor: Colors.red));
    }
  }

  @override Widget build(BuildContext context) {
    final maxHeight = MediaQuery.of(context).size.height * 0.55;
    return SizedBox(height: maxHeight, child: Stack(alignment: Alignment.center, children: [
      PageView.builder(controller: _pageController, itemCount: widget.mediaList.length, onPageChanged: (i) { _videoControllers[_currentPage]?.pause(); setState(() { _currentPage = i; _showDots = true; }); _videoControllers[i]?.play(); Future.delayed(const Duration(seconds: 4), () { if (mounted) setState(() => _showDots = false); }); }, itemBuilder: (c, i) { final m = widget.mediaList[i]; return VisibilityDetector(key: Key('${widget.postId}_$i'), onVisibilityChanged: (info) => _handleVisibility(info.visibleFraction > 0.5, i), child: _buildMediaItem(m, i, maxHeight)); }),
      if (widget.mediaList.length > 1 && _showDots) Positioned(bottom: 10, child: Container(padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5), decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(20)), child: Row(children: List.generate(widget.mediaList.length, (i) => Container(margin: const EdgeInsets.symmetric(horizontal: 3), width: _currentPage == i? 18 : 7, height: 7, decoration: BoxDecoration(borderRadius: BorderRadius.circular(10), color: _currentPage == i? Colors.white : Colors.white54))))))
    ]));
  }

  Widget _buildMediaItem(PostMediaModel media, int index, double maxHeight) {
    if (media.mediaType == 'video') {
      final c = _videoControllers[index];
      if (c == null ||!c.value.isInitialized) return Container(height: maxHeight, color: Colors.black, child: const Center(child: CircularProgressIndicator(color: Colors.white)));
      return GestureDetector(onTap: () => _openFullScreen(context, index), child: Container(color: Colors.black, child: Center(child: AspectRatio(aspectRatio: c.value.aspectRatio, child: VideoPlayer(c)))));
    } else if (media.mediaType == 'image') {
      return GestureDetector(onTap: () => _openFullScreen(context, index), child: CachedNetworkImage(imageUrl: media.file, fit: BoxFit.cover, width: double.infinity, height: maxHeight));
    } else if (_isPDF(media.file)) {
      return Stack(children: [SfPdfViewer.network(media.file), Positioned(bottom: 10, right: 10, child: ElevatedButton.icon(onPressed: () => _downloadFile(media.file, media.fileName.isNotEmpty? media.fileName : 'doc.pdf'), icon: const Icon(Icons.download), label: const Text('Download'), style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF030F27), foregroundColor: Colors.white)))]);
    } else {
      return Container(color: const Color(0xFFF0F2F5), child: Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [const Icon(Icons.insert_drive_file, size: 60, color: Colors.grey), const SizedBox(height: 10), Padding(padding: const EdgeInsets.symmetric(horizontal: 20), child: Text(media.fileName, style: const TextStyle(fontWeight: FontWeight.bold), textAlign: TextAlign.center)), const SizedBox(height: 15), ElevatedButton.icon(onPressed: () => _downloadFile(media.file, media.fileName), icon: const Icon(Icons.download), label: const Text('Download to Phone'), style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF030F27), foregroundColor: Colors.white)), const SizedBox(height: 8), TextButton.icon(onPressed: () async { String savePath = '/storage/emulated/0/Download/${media.fileName}'; if (!await File(savePath).exists()) { await _downloadFile(media.file, media.fileName); } else { await OpenFilex.open(savePath); } }, icon: const Icon(Icons.open_in_new), label: const Text('Open in other app'))])));
    }
  }
  void _openFullScreen(BuildContext context, int initialIndex) { _videoControllers.values.forEach((c) => c.pause()); Navigator.push(context, MaterialPageRoute(builder: (_) => _FullScreenViewer(mediaList: widget.mediaList, initialIndex: initialIndex))).then((_) => _videoControllers[_currentPage]?.play()); }
}

class _FullScreenViewer extends StatefulWidget { final List<PostMediaModel> mediaList; final int initialIndex; const _FullScreenViewer({required this.mediaList, required this.initialIndex}); @override State<_FullScreenViewer> createState() => _FullScreenViewerState(); }
class _FullScreenViewerState extends State<_FullScreenViewer> {
  late PageController _controller; late int _currentIndex; final Map<int, VideoPlayerController> _videoControllers = {};
  @override void initState() { super.initState(); _currentIndex = widget.initialIndex; _controller = PageController(initialPage: widget.initialIndex); for (int i = 0; i < widget.mediaList.length; i++) { if (widget.mediaList[i].mediaType == 'video') { final c = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file)); _videoControllers[i] = c; c.initialize().then((_) { if (mounted) { setState(() {}); if (i == _currentIndex) c.play(); } }); } } }
  @override void dispose() { for (var c in _videoControllers.values) { c.dispose(); } _controller.dispose(); super.dispose(); }
  bool _isPDF(String u) => u.toLowerCase().endsWith('.pdf');
  Future<void> _downloadFile(String url, String name) async {
    Directory dir = Directory('/storage/emulated/0/Download'); if (!await dir.exists()) dir = await getApplicationDocumentsDirectory() as Directory;
    String savePath = '${dir.path}/$name';
    await Dio().download(url, savePath);
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Saved to Download/$name'), backgroundColor: Colors.green, action: SnackBarAction(label: 'OPEN', onPressed: () => OpenFilex.open(savePath))));
  }
  @override Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(backgroundColor: Colors.black, iconTheme: const IconThemeData(color: Colors.white), title: Text('${_currentIndex + 1}/${widget.mediaList.length}', style: const TextStyle(color: Colors.white)), actions: [if (widget.mediaList[_currentIndex].mediaType!= 'image' && widget.mediaList[_currentIndex].mediaType!= 'video') IconButton(icon: const Icon(Icons.download, color: Colors.white), onPressed: () => _downloadFile(widget.mediaList[_currentIndex].file, widget.mediaList[_currentIndex].fileName.isNotEmpty? widget.mediaList[_currentIndex].fileName : 'file_$_currentIndex'))]),
      body: PageView.builder(controller: _controller, itemCount: widget.mediaList.length, onPageChanged: (i) { _videoControllers[_currentIndex]?.pause(); setState(() => _currentIndex = i); _videoControllers[i]?.play(); }, itemBuilder: (c, i) { final m = widget.mediaList[i]; if (m.mediaType == 'video') { final con = _videoControllers[i]; return con!= null && con.value.isInitialized? Center(child: AspectRatio(aspectRatio: con.value.aspectRatio, child: VideoPlayer(con))) : const Center(child: CircularProgressIndicator(color: Colors.white)); } else if (_isPDF(m.file)) { return SfPdfViewer.network(m.file); } else if (m.mediaType == 'image') { return InteractiveViewer(child: CachedNetworkImage(imageUrl: m.file, fit: BoxFit.contain)); } else { return Center(child: Text(m.fileName, style: const TextStyle(color: Colors.white))); } }),
    );
  }
}

class CommentBottomSheet extends StatefulWidget { final PostModel post; final VoidCallback onCommentAdded; const CommentBottomSheet({super.key, required this.post, required this.onCommentAdded}); @override State<CommentBottomSheet> createState() => _CommentBottomSheetState(); }
class _CommentBottomSheetState extends State<CommentBottomSheet> {
  List<CommentModel> comments = []; bool loading = true; bool isUploading = false; double uploadProgress = 0; final TextEditingController _controller = TextEditingController(); List<File> _selectedFiles = []; String? replyToId; String? replyToName; final ImagePicker _picker = ImagePicker();
  @override void initState() { super.initState(); _fetchComments(); }
  Future<void> _fetchComments() async { try { final data = await CommentService.getComments(widget.post.id); if (mounted) setState(() { comments = data; loading = false; }); } catch (e) { if (mounted) setState(() => loading = false); } }
  Future<void> _openCameraOptions() async {
    showModalBottomSheet(context: context, backgroundColor: Colors.white, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))), builder: (c) => SafeArea(child: Wrap(children: [
      ListTile(leading: const Icon(Icons.camera_alt, color: Colors.blue), title: const Text("Take Photo"), onTap: () async { Navigator.pop(c); try { final XFile? photo = await _picker.pickImage(source: ImageSource.camera, imageQuality: 85); if (photo!= null && mounted) { setState(() => isUploading = true); File? result = await Navigator.push(context, MaterialPageRoute(builder: (_) => FilterEditorScreen(file: File(photo.path), isVideo: false))); if (mounted) setState(() { if (result!= null) _selectedFiles.add(result); isUploading = false; }); } } catch (e) { if (mounted) setState(() => isUploading = false); } }),
      ListTile(leading: const Icon(Icons.videocam, color: Colors.red), title: const Text("Record Video (Any Size upto 4GB)"), onTap: () async { Navigator.pop(c); try { final XFile? video = await _picker.pickVideo(source: ImageSource.camera); if (video!= null && mounted) { setState(() => isUploading = true); File? result = await Navigator.push(context, MaterialPageRoute(builder: (_) => FilterEditorScreen(file: File(video.path), isVideo: true))); if (mounted) setState(() { if (result!= null) _selectedFiles.add(result); isUploading = false; }); } } catch (e) { if (mounted) setState(() => isUploading = false); } }),
      ListTile(leading: const Icon(Icons.photo_library, color: Colors.green), title: const Text("Gallery Photo/Video (Upto 4GB)"), onTap: () async { Navigator.pop(c); final XFile? picked = await _picker.pickImage(source: ImageSource.gallery); if (picked== null) { final XFile? vpicked = await _picker.pickVideo(source: ImageSource.gallery); if (vpicked!= null && mounted) { setState(() => isUploading = true); File? result = await Navigator.push(context, MaterialPageRoute(builder: (_) => FilterEditorScreen(file: File(vpicked.path), isVideo: true))); if (mounted) setState(() { if (result!= null) _selectedFiles.add(result); isUploading = false; }); } } else if (mounted) { setState(() => isUploading = true); File? result = await Navigator.push(context, MaterialPageRoute(builder: (_) => FilterEditorScreen(file: File(picked.path), isVideo: false))); if (mounted) setState(() { if (result!= null) _selectedFiles.add(result); isUploading = false; }); } }),
    ])),);
  }
  Future<void> _send() async {
    if (_controller.text.trim().isEmpty && _selectedFiles.isEmpty) return;
    setState(() { isUploading = true; uploadProgress = 0; });
    try {
      final newComment = await CommentService.createComment(postId: widget.post.id, parentId: replyToId, content: _controller.text.trim(), files: _selectedFiles, onProgress: (p) { if (mounted) setState(() => uploadProgress = p); });
      if (mounted) { setState(() { if (replyToId == null) comments.insert(0, newComment); _controller.clear(); _selectedFiles = []; replyToId = null; replyToName = null; isUploading = false; uploadProgress = 0; }); widget.onCommentAdded(); }
    } catch (e) { if (mounted) { setState(() => isUploading = false); ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed: $e'), backgroundColor: Colors.red)); } }
  }
  @override Widget build(BuildContext context) {
    return SafeArea(child: DraggableScrollableSheet(initialChildSize: 0.85, maxChildSize: 0.95, minChildSize: 0.5, expand: false, builder: (context, scrollController) {
      return Column(children: [
        const SizedBox(height: 10), Container(width: 40, height: 4, decoration: BoxDecoration(color: Colors.grey[300], borderRadius: BorderRadius.circular(10))), const SizedBox(height: 10),
        Text("Comments ${widget.post.commentsCount}", style: const TextStyle(fontWeight: FontWeight.bold)), const Divider(),
        if (isUploading && uploadProgress > 0) Padding(padding: const EdgeInsets.all(8), child: Column(children: [LinearProgressIndicator(value: uploadProgress/100, color: const Color(0xFF030F27)), const SizedBox(height: 4), Text('${uploadProgress.toStringAsFixed(1)}% uploading (4GB support)...', style: const TextStyle(fontSize: 12))])),
        if (isUploading && uploadProgress==0) const LinearProgressIndicator(color: Color(0xFF030F27)),
        Expanded(child: loading? const Center(child: CircularProgressIndicator()) : comments.isEmpty? const Center(child: Text("No comments yet")) : ListView.builder(controller: scrollController, itemCount: comments.length, itemBuilder: (c, i) => CommentTile(comment: comments[i], onReply: (id, name) => setState(() { replyToId = id; replyToName = name; })))),
        if (replyToName!= null) Container(color: Colors.blue.shade50, padding: const EdgeInsets.all(8), child: Row(children: [Text("Replying to @$replyToName"), const Spacer(), IconButton(icon: const Icon(Icons.close, size: 16), onPressed: () => setState(() { replyToId = null; replyToName = null; })) ])),
        if (_selectedFiles.isNotEmpty) Container(height: 85, color: Colors.grey[100], child: ListView.builder(scrollDirection: Axis.horizontal, itemCount: _selectedFiles.length, itemBuilder: (c,i){ bool isVideo = _selectedFiles[i].path.toLowerCase().endsWith('.mp4') || _selectedFiles[i].path.toLowerCase().endsWith('.mov'); return Stack(children: [Container(margin: const EdgeInsets.all(6), width: 70, height: 70, decoration: BoxDecoration(borderRadius: BorderRadius.circular(10), color: Colors.black), child: ClipRRect(borderRadius: BorderRadius.circular(10), child: isVideo? const Icon(Icons.videocam, color: Colors.white) : Image.file(_selectedFiles[i], fit: BoxFit.cover))), Positioned(top: 0, right: 0, child: GestureDetector(onTap: ()=>setState(()=>_selectedFiles.removeAt(i)), child: Container(decoration: const BoxDecoration(color: Colors.red, shape: BoxShape.circle), child: const Icon(Icons.close, size: 18, color: Colors.white))))]); })),
        Padding(padding: EdgeInsets.only(left: 10, right: 10, bottom: MediaQuery.of(context).viewPadding.bottom + 10, top: 8), child: Row(children: [
          IconButton(icon: const Icon(Icons.camera_alt, color: Color(0xFF030F27)), onPressed: isUploading? null : _openCameraOptions),
          Expanded(child: TextField(controller: _controller, enabled:!isUploading, decoration: const InputDecoration(hintText: "Add comment...", border: OutlineInputBorder(borderRadius: BorderRadius.all(Radius.circular(25))), contentPadding: EdgeInsets.symmetric(horizontal: 16)))),
          const SizedBox(width: 8), isUploading? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)) : CircleAvatar(backgroundColor: const Color(0xFF030F27), child: IconButton(icon: const Icon(Icons.send, color: Colors.white), onPressed: _send))
        ]))
      ]);
    }));
  }
}

class FilterEditorScreen extends StatefulWidget { final File file; final bool isVideo; const FilterEditorScreen({super.key, required this.file, required this.isVideo}); @override State<FilterEditorScreen> createState() => _FilterEditorScreenState(); }
class _FilterEditorScreenState extends State<FilterEditorScreen> {
  int selectedIndex = 0; VideoPlayerController? _videoController; bool saving = false;
  @override void initState() { super.initState(); if (widget.isVideo) { _videoController = VideoPlayerController.file(widget.file)..initialize().then((_) { if (mounted) setState(() {}); })..setLooping(true)..play(); } }
  @override void dispose() { _videoController?.dispose(); super.dispose(); }
  Future<File> _applyFilterAndSave() async {
    if (widget.isVideo) return widget.file;
    setState(() => saving = true);
    try {
      final bytes = await widget.file.readAsBytes();
      final original = await Future(() => imgLib.decodeImage(bytes));
      if (original == null) return widget.file;
      imgLib.Image filtered = original;
      if (appFilters[selectedIndex].name == "B&W") filtered = imgLib.grayscale(original);
      if (appFilters[selectedIndex].name == "Sepia") filtered = imgLib.sepia(original);
      if (appFilters[selectedIndex].name == "Vivid") filtered = imgLib.adjustColor(original, saturation: 1.5);
      final dir = await getTemporaryDirectory();
      final out = File('${dir.path}/filtered_${DateTime.now().millisecondsSinceEpoch}.jpg');
      await out.writeAsBytes(imgLib.encodeJpg(filtered, quality: 85));
      return out;
    } finally { if (mounted) setState(() => saving = false); }
  }
  @override Widget build(BuildContext context) {
    return Scaffold(backgroundColor: Colors.black, appBar: AppBar(backgroundColor: Colors.black, iconTheme: const IconThemeData(color: Colors.white), title: const Text("Edit", style: TextStyle(color: Colors.white)), actions: [saving? const Padding(padding: EdgeInsets.all(16), child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))) : TextButton(onPressed: () async { File out = await _applyFilterAndSave(); if (mounted) Navigator.pop(context, out); }, child: const Text("DONE", style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)))]),
      body: Column(children: [
        Expanded(child: Center(child: widget.isVideo? (_videoController!= null && _videoController!.value.isInitialized? AspectRatio(aspectRatio: _videoController!.value.aspectRatio, child: ColorFiltered(colorFilter: ColorFilter.matrix(appFilters[selectedIndex].matrix), child: VideoPlayer(_videoController!))) : const CircularProgressIndicator(color: Colors.white)) : ColorFiltered(colorFilter: ColorFilter.matrix(appFilters[selectedIndex].matrix), child: Image.file(widget.file, fit: BoxFit.contain)))),
        Container(height: 130, color: Colors.black87, child: ListView.builder(scrollDirection: Axis.horizontal, itemCount: appFilters.length, itemBuilder: (c, i) => GestureDetector(onTap: () => setState(() => selectedIndex = i), child: Padding(padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 10), child: Column(children: [Container(decoration: BoxDecoration(border: Border.all(color: selectedIndex == i? Colors.white : Colors.transparent, width: 2), borderRadius: BorderRadius.circular(12)), child: ClipRRect(borderRadius: BorderRadius.circular(10), child: ColorFiltered(colorFilter: ColorFilter.matrix(appFilters[i].matrix), child: widget.isVideo? Container(width: 60, height: 60, color: Colors.grey[800], child: const Icon(Icons.play_arrow, color: Colors.white)) : Image.file(widget.file, width: 60, height: 60, fit: BoxFit.cover)))), const SizedBox(height: 6), Text(appFilters[i].name, style: TextStyle(color: selectedIndex == i? Colors.white : Colors.white70, fontSize: 11))])))))
      ]),
    );
  }
}

class CommentTile extends StatefulWidget { final CommentModel comment; final Function(String, String) onReply; const CommentTile({super.key, required this.comment, required this.onReply}); @override State<CommentTile> createState() => _CommentTileState(); }
class _CommentTileState extends State<CommentTile> {
  List<CommentModel> replies = []; bool showReplies = false; bool loadingReplies = false;
  Future<void> _loadReplies() async { setState(() => loadingReplies = true); final data = await CommentService.getReplies(widget.comment.id); setState(() { replies = data; showReplies = true; loadingReplies = false; }); }
  bool _isImage(String url) { final l = url.toLowerCase(); return l.endsWith('.png') || l.endsWith('.jpg') || l.endsWith('.jpeg') || l.endsWith('.webp') || l.endsWith('.gif'); }
  bool _isPDF(String url) => url.toLowerCase().endsWith('.pdf');

  Future<void> _downloadFile(String url, String fileName) async {
    Directory dir = Directory('/storage/emulated/0/Download'); if (!await dir.exists()) dir = await getApplicationDocumentsDirectory() as Directory;
    String savePath = '${dir.path}/$fileName';
    await Dio().download(url, savePath);
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Saved: Download/$fileName'), backgroundColor: Colors.green, action: SnackBarAction(label: 'OPEN', onPressed: () => OpenFilex.open(savePath))));
  }

  void _openFullScreenImage(String imageUrl) {
    Navigator.push(context, MaterialPageRoute(builder: (_) => Scaffold(backgroundColor: Colors.black, appBar: AppBar(backgroundColor: Colors.black, iconTheme: const IconThemeData(color: Colors.white)), body: Center(child: InteractiveViewer(child: CachedNetworkImage(imageUrl: imageUrl, fit: BoxFit.contain))))));
  }

  @override Widget build(BuildContext context) {
    return Padding(padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        CircleAvatar(radius: 16, backgroundImage: widget.comment.user.profilePicture!= null && widget.comment.user.profilePicture!.isNotEmpty? CachedNetworkImageProvider(widget.comment.user.profilePicture!) : null),
        const SizedBox(width: 8),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Container(padding: const EdgeInsets.all(10), decoration: BoxDecoration(color: Colors.grey.shade100, borderRadius: BorderRadius.circular(12)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(widget.comment.user.username, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12)),
            if (widget.comment.content.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 4), child: Text(widget.comment.content)),
            if (widget.comment.media.isNotEmpty)
            ...widget.comment.media.map((m) {
                if (_isImage(m.file)) {
                  return Padding(padding: const EdgeInsets.only(top: 6), child: GestureDetector(onTap: () => _openFullScreenImage(m.file), child: ClipRRect(borderRadius: BorderRadius.circular(8), child: CachedNetworkImage(imageUrl: m.file, height: 180, fit: BoxFit.cover))));
                } else if (_isPDF(m.file)) {
                  return Padding(padding: const EdgeInsets.only(top: 6), child: InkWell(onTap: () { Navigator.push(context, MaterialPageRoute(builder: (_) => Scaffold(appBar: AppBar(title: Text(m.fileName)), body: SfPdfViewer.network(m.file)))); }, child: Container(padding: const EdgeInsets.all(10), decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8), border: Border.all(color: Colors.grey.shade300)), child: Row(children: [const Icon(Icons.picture_as_pdf, color: Colors.red), const SizedBox(width: 8), Expanded(child: Text(m.fileName)), const Icon(Icons.visibility)]))));
                } else {
                  return Padding(padding: const EdgeInsets.only(top: 6), child: InkWell(onTap: () async { String path = '/storage/emulated/0/Download/${m.fileName}'; if (!await File(path).exists()) { await _downloadFile(m.file, m.fileName); } else { await OpenFilex.open(path); } }, child: Container(padding: const EdgeInsets.all(8), decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(6), border: Border.all(color: Colors.grey.shade300)), child: Row(mainAxisSize: MainAxisSize.min, children: [const Icon(Icons.attach_file, size: 16, color: Color(0xFF030F27)), const SizedBox(width: 6), Flexible(child: Text(m.fileName, style: const TextStyle(fontSize: 12))), const SizedBox(width: 8), const Icon(Icons.download, size: 16)]))));
                }
              }).toList(),
          ])),
          const SizedBox(height: 4),
          Row(children: [Text(timeago.format(widget.comment.createdAt), style: const TextStyle(fontSize: 10, color: Colors.grey)), const SizedBox(width: 12), GestureDetector(onTap: () => widget.onReply(widget.comment.id, widget.comment.user.username), child: const Text("Reply", style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold))), if (widget.comment.repliesCount > 0)...[const SizedBox(width: 12), GestureDetector(onTap: () { if (showReplies) setState(() => showReplies = false); else _loadReplies(); }, child: Text(loadingReplies? "Loading..." : showReplies? "Hide replies" : "${widget.comment.repliesCount} replies", style: const TextStyle(fontSize: 12, color: Colors.blue)))]]),
        ]))
      ]),
      if (showReplies) Padding(padding: const EdgeInsets.only(left: 40, top: 8), child: Column(children: replies.map((r) => CommentTile(comment: r, onReply: widget.onReply)).toList())),
    ]));
  }
}