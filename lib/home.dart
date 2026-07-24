
// import 'dart:async';
// import 'dart:convert';
// import 'dart:io';
// import 'package:flutter/material.dart';
// import 'package:flutter/services.dart';
// import 'package:cached_network_image/cached_network_image.dart';
// import 'package:timeago/timeago.dart' as timeago;
// import 'package:video_player/video_player.dart';
// import 'package:share_plus/share_plus.dart';
// import 'package:visibility_detector/visibility_detector.dart';
// import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
// import 'package:dio/dio.dart';
// import 'package:path_provider/path_provider.dart';
// import 'package:image_picker/image_picker.dart';
// import 'package:open_filex/open_filex.dart';
// import 'package:file_selector/file_selector.dart';
// import 'package:shared_preferences/shared_preferences.dart';
// import 'package:wakelock_plus/wakelock_plus.dart';
// import 'package:easy_audience_network_plus/easy_audience_network.dart';

// import '../profile/screens/profile.dart';
// import '../profile/screens/target_profile.dart';
// import '../profile/api_service.dart' as ProfileApi;
// import 'search/search.dart';
// import 'post/screens/new_post.dart';
// import '../services/home_api_model_service.dart';
// import '../services/comment_service.dart';
// import '../services/auth_service.dart';

// class HomeScreen extends StatefulWidget {
//   const HomeScreen({super.key});
//   @override State<HomeScreen> createState() => _HomeScreenState();
// }

// class _HomeScreenState extends State<HomeScreen> {
//   int _selectedIndex = 0;
//   List<PostModel> _posts = [];
//   bool _isLoading = true;
//   bool _isLoadingMore = false;
//   int _currentPage = 1;
//   bool _hasMore = true;
//   final ScrollController _scrollController = ScrollController();
//   final Map<String, String> _emojiMap = {'like': '👍','confuse': '🤔','wrong': '❗','imp': '⭐','explain': '💡'};
//   final Map<String, Color> _emojiColor = {'like': Color(0xFF1877F2),'confuse': Color(0xFFF7B928),'wrong': Color(0xFFE0245E),'imp': Color(0xFFFFAD33),'explain': Color(0xFF45BD62)};
//   Set<String> _savedIds = {};
//   String? _myUsername;

//   @override void initState() {
//     super.initState();
//     _initAds();
//     _loadFeed(); _loadSaved(); _loadMyUsername(); _scrollController.addListener(_onScroll);
//   }

//   Future<void> _initAds() async { await EasyAudienceNetwork.init(testMode: true); }
//   @override void dispose() { _scrollController.dispose(); super.dispose(); }
//   void _onScroll() { if (_scrollController.position.pixels >= _scrollController.position.maxScrollExtent - 300) { if (!_isLoadingMore && _hasMore) _loadMore(); } }
//   Future<void> _loadSaved() async { final prefs = await SharedPreferences.getInstance(); setState(() => _savedIds = (prefs.getStringList('saved_posts')?? []).toSet()); }

//   Future<void> _toggleSave(PostModel post) async {
//     final postId = post.id;
//     final wasSaved = post.isSaved;
//     final prefs = await SharedPreferences.getInstance();
//     setState(() {
//       post.isSaved =!wasSaved;
//       if (post.isSaved) { _savedIds.add(postId); post.savesCount++; } else { _savedIds.remove(postId); if (post.savesCount > 0) post.savesCount--; }
//     });
//     await prefs.setStringList('saved_posts', _savedIds.toList());
//     try {
//       final res = await HomeFeedService.toggleSave(postId);
//       final bool apiIsSaved = res['is_saved']?? (res['status'] == 'saved');
//       final int? apiCount = res['saves_count'];
//       if (mounted) {
//         setState(() {
//           post.isSaved = apiIsSaved;
//           if (apiCount!= null) post.savesCount = apiCount;
//           if (apiIsSaved) _savedIds.add(postId); else _savedIds.remove(postId);
//         });
//         await prefs.setStringList('saved_posts', _savedIds.toList());
//       }
//     } catch (e) {
//       if (mounted) {
//         setState(() { post.isSaved = wasSaved; if (wasSaved) _savedIds.add(postId); else _savedIds.remove(postId); });
//         ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Save failed: $e')));
//       }
//     }
//   }

//   Future<void> _loadMyUsername() async {
//     try { final d = await ProfileApi.ApiService.getProfile(); _myUsername = d.username; }
//     catch (_) { try{ final t=await AuthService.getToken(); if(t!=null){ String p=base64.normalize(t.split('.')[1]); _myUsername=jsonDecode(utf8.decode(base64Url.decode(p)))['username']?.toString(); } }catch(_){} }
//   }
//   Future<void> _goToProfile(String username) async {
//     if(username.trim().isEmpty) return;
//     if(_myUsername==null) await _loadMyUsername();
//     bool isMe = _myUsername!=null && _myUsername!.toLowerCase().trim()==username.toLowerCase().trim();
//     if(!mounted) return;
//     if(isMe) { if(Navigator.canPop(context)){ Navigator.pop(context); } await Future.delayed(Duration(milliseconds: 100)); if(!mounted) return; setState(() => _selectedIndex = 2); }
//     else { Navigator.push(context, MaterialPageRoute(builder:(_)=>TargetProfilePage(username: username))); }
//   }
//   Future<void> _loadFeed({bool refresh = false}) async {
//     if (refresh) { setState(() { _isLoading = true; _currentPage = 1; }); try { final feed = await HomeFeedService.refreshFeed(page: 1, pageSize: 20); if(mounted) setState(() { _posts = feed.results; _isLoading = false; _hasMore = feed.next!= null; _savedIds.addAll(feed.results.where((p)=>p.isSaved).map((p)=>p.id)); }); } catch (e) { if(mounted) setState(() => _isLoading = false); } return; }
//     try {
//       final cached = await HomeFeedService.getCachedFeed();
//       if (cached!= null && mounted && _posts.isEmpty) { setState(() { _posts = cached.results; _isLoading = false; _hasMore = cached.next!= null; _savedIds.addAll(cached.results.where((p)=>p.isSaved).map((p)=>p.id)); }); }
//       final feed = await HomeFeedService.getHomeFeed(page: 1, pageSize: 20);
//       if(mounted) setState(() { _posts = feed.results; _isLoading = false; _hasMore = feed.next!= null; _currentPage = 1; _savedIds.addAll(feed.results.where((p)=>p.isSaved).map((p)=>p.id)); });
//     } catch (e) { if(mounted) setState(() => _isLoading = false); }
//   }
//   Future<void> _loadMore() async { if (_isLoadingMore ||!_hasMore) return; setState(() => _isLoadingMore = true); _currentPage++; try { final feed = await HomeFeedService.getHomeFeed(page: _currentPage, pageSize: 20); if(mounted) setState(() { _posts.addAll(feed.results); _isLoadingMore = false; _hasMore = feed.next!= null; _savedIds.addAll(feed.results.where((p)=>p.isSaved).map((p)=>p.id)); }); } catch (e) { _currentPage--; if(mounted) setState(() => _isLoadingMore = false); } }
//   void _onItemTapped(int i) => setState(() => _selectedIndex = i);
//   void _sharePost(PostModel p) { String t = p.content?? ''; if (p.media.isNotEmpty) t += '\n\n${p.media.first.file}'; Share.share(t); }
//   void _openCommentSheet(PostModel post) { showModalBottomSheet(context: context, isScrollControlled: true, backgroundColor: Colors.transparent, builder: (_) => Padding(padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom), child: Container(decoration: const BoxDecoration(color: Colors.white, borderRadius: BorderRadius.vertical(top: Radius.circular(20))), child: CommentBottomSheet(post: post, onCommentAdded: () => setState(() => post.commentsCount++), onGoToProfile: _goToProfile)))); }
//   Future<void> _handleReaction(PostModel post, String reaction) async { final old = post.myReaction; final idx = _posts.indexWhere((p) => p.id == post.id); if (idx == -1) return; setState(() { if (old == reaction) { _posts[idx].myReaction = null; _posts[idx].likesCount--; } else { if (old==null) _posts[idx].likesCount++; _posts[idx].myReaction = reaction; } }); try { final res = await HomeFeedService.toggleReaction(post.id, reaction); final c = res['counts']; if(mounted) setState(() { _posts[idx].likeCount = c['like']; _posts[idx].confuseCount = c['confuse']; _posts[idx].wrongCount = c['wrong']; _posts[idx].impCount = c['imp']; _posts[idx].explainCount = c['explain']; _posts[idx].likesCount = c['total']; _posts[idx].myReaction = res['my_reaction']; }); } catch (e) { if(mounted) setState(() => _posts[idx].myReaction = old); } }

//   // 🔥 BANNER -> NATIVE AD
//   Widget _buildAd() {
//     return Container(
//       margin: const EdgeInsets.only(bottom: 10),
//       color: Colors.white,
//       child: Container(
//         height: 320,
//         width: double.infinity,
//         child: NativeAd(
//           placementId: NativeAd.testPlacementId,
//           adType: NativeAdType.NATIVE_AD,
//           width: double.infinity,
//           height: 320,
//           backgroundColor: Colors.white,
//           titleColor: Colors.black,
//           descriptionColor: Colors.black54,
//           buttonColor: const Color(0xFF030F27),
//           buttonTitleColor: Colors.white,
//           buttonBorderColor: const Color(0xFF030F27),
//           listener: NativeAdListener(
//             onLoaded: ()=> print("Native Ad Loaded"),
//             onError: (c,m)=> print("Native Ad Error $c $m"),
//             onClicked: ()=> print("Native Ad Clicked"),
//           ),
//         ),
//       ),
//     );
//   }

//   Widget _homePage() {
//     return Container(color: const Color(0xFFF0F2F5), child: RefreshIndicator(onRefresh: () => _loadFeed(refresh: true), child: CustomScrollView(controller: _scrollController, slivers: [
//       SliverAppBar(automaticallyImplyLeading: false, backgroundColor: const Color(0xFF030F27), toolbarHeight: 70, floating: true, snap: true, title: Row(children: [Image.asset('assets/slogo1.png', height: 42, errorBuilder: (_, __, ___) => const Text("LearnScroll", style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 22)))]), actions: [Container(margin: EdgeInsets.only(right: 14), height: 44, width: 44, decoration: BoxDecoration(gradient: LinearGradient(colors: [Color(0xFFFF6A00), Color(0xFFEE0979)]), shape: BoxShape.circle), child: IconButton(onPressed: () async { await Navigator.push(context, MaterialPageRoute(builder: (_) => const NewPost())); _loadFeed(refresh: true); }, icon: Icon(Icons.edit_rounded, color: Colors.white, size: 22)))]),
//       if (_isLoading && _posts.isEmpty) const SliverFillRemaining(child: Center(child: CircularProgressIndicator()))
//       else SliverList(delegate: SliverChildBuilderDelegate((context, index) {
//         if (_hasMore && index == _posts.length + (_posts.length ~/ 5)) return const Padding(padding: EdgeInsets.all(16), child: Center(child: CircularProgressIndicator()));
//         if (index % 6 == 5) return _buildAd();
//         int postIndex = index - (index ~/ 6);
//         if (postIndex < _posts.length) return _buildPostCard(_posts[postIndex], postIndex);
//         return SizedBox.shrink();
//       }, childCount: _posts.length + (_posts.length ~/ 5) + (_hasMore? 1 : 0))),
//       const SliverToBoxAdapter(child: SizedBox(height: 80)),
//     ])));
//   }
//   Widget _buildPostCard(PostModel post, int idx) {
//     List<MapEntry<String, int>> sorted = [MapEntry('like', post.likeCount), MapEntry('confuse', post.confuseCount), MapEntry('wrong', post.wrongCount), MapEntry('imp', post.impCount), MapEntry('explain', post.explainCount)];
//     sorted.sort((a, b) => b.value.compareTo(a.value)); var top3 = sorted.where((e) => e.value > 0).take(3).toList();
//     bool isSaved = post.isSaved || _savedIds.contains(post.id);
//     return Container(margin: const EdgeInsets.only(bottom: 10), color: Colors.white, child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
//       Padding(padding: const EdgeInsets.fromLTRB(12, 12, 8, 8), child: Row(children: [
//         GestureDetector(onTap: ()=> _goToProfile(post.user.username), child: CircleAvatar(radius: 20, backgroundColor: Colors.grey[300], backgroundImage: post.user.profilePicture!= null && post.user.profilePicture!.isNotEmpty? CachedNetworkImageProvider(post.user.profilePicture!) : null)),
//         const SizedBox(width: 10),
//         Expanded(child: GestureDetector(onTap: ()=> _goToProfile(post.user.username), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(post.user.username, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15)), Text(timeago.format(post.createdAt), style: TextStyle(fontSize: 12, color: Colors.grey[600]))]))),
//         IconButton(icon: Icon(isSaved? Icons.bookmark : Icons.bookmark_border, color: isSaved? Color(0xFF030F27) : Colors.grey), onPressed: () => _toggleSave(post))
//       ])),
//       if (post.content!= null && post.content!.isNotEmpty) Padding(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6), child: Text(post.content!, style: const TextStyle(fontSize: 15, height: 1.3))),
//       if (post.hashtags.isNotEmpty) Padding(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 2), child: Wrap(spacing: 6, children: post.hashtags.map((t) => Text('#$t', style: const TextStyle(color: Color(0xFF1877F2), fontWeight: FontWeight.w500))).toList())),
//       const SizedBox(height: 6),
//       if (post.media.isNotEmpty) _MediaCarousel(mediaList: post.media, postId: post.id, postIndex: idx),
//       if (post.likesCount > 0 || post.commentsCount > 0) Padding(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10), child: Row(children: [if (top3.isNotEmpty) Row(children: top3.map((e) => Padding(padding: const EdgeInsets.only(right: 3), child: Text(_emojiMap[e.key]?? '', style: const TextStyle(fontSize: 16)))).toList()), const SizedBox(width: 6), if (post.likesCount > 0) Text('${post.likesCount}', style: TextStyle(fontSize: 14, color: Colors.grey[700])), const Spacer(), if (post.commentsCount > 0) Text('${post.commentsCount} comments', style: TextStyle(fontSize: 13, color: Colors.grey[600]))])),
//       const Divider(height: 1, thickness: 0.5),
//       Padding(padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2), child: Row(children: [
//         Expanded(child: _PostReactionButton(post: post, emojiMap: _emojiMap, emojiColor: _emojiColor, onReaction: (r) => _handleReaction(post, r))),
//         Expanded(child: InkWell(onTap: () => _openCommentSheet(post), child: Container(padding: const EdgeInsets.symmetric(vertical: 12), child: const Row(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.chat_bubble_outline, size: 20, color: Color(0xFF65676B)), SizedBox(width: 6), Text('Comment', style: TextStyle(color: Color(0xFF65676B), fontWeight: FontWeight.w600))])))),
//         Expanded(child: InkWell(onTap: () => _sharePost(post), child: Container(padding: const EdgeInsets.symmetric(vertical: 12), child: const Row(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.share_outlined, size: 20, color: Color(0xFF65676B)), SizedBox(width: 6), Text('Share', style: TextStyle(color: Color(0xFF65676B), fontWeight: FontWeight.w600))])))),
//       ])),
//     ]));
//   }
//   @override Widget build(BuildContext context) { return Scaffold(backgroundColor: const Color(0xFFF0F2F5), body: IndexedStack(index: _selectedIndex, children: [_homePage(), const SearchScreen(), ProfileScreen(onBackToHome: () => setState(() => _selectedIndex = 0))]), bottomNavigationBar: BottomNavigationBar(currentIndex: _selectedIndex, onTap: _onItemTapped, backgroundColor: const Color(0xFF030F27), selectedItemColor: Colors.white, unselectedItemColor: Colors.white60, type: BottomNavigationBarType.fixed, items: const [BottomNavigationBarItem(icon: Icon(Icons.home_outlined), activeIcon: Icon(Icons.home), label: "Home"), BottomNavigationBarItem(icon: Icon(Icons.search), label: "Search"), BottomNavigationBarItem(icon: Icon(Icons.person_outline), activeIcon: Icon(Icons.person), label: "Profile")])); }
// }

// //... _PostReactionButton, _MediaCarousel, _FullScreenViewer same as before (no change)...
// class _PostReactionButton extends StatefulWidget {
//   final PostModel post; final Map<String, String> emojiMap; final Map<String, Color> emojiColor; final Function(String) onReaction;
//   const _PostReactionButton({required this.post, required this.emojiMap, required this.emojiColor, required this.onReaction});
//   @override State<_PostReactionButton> createState() => _PostReactionButtonState();
// }
// class _PostReactionButtonState extends State<_PostReactionButton> {
//   OverlayEntry? _overlayEntry;
//   void _showOverlay(BuildContext context) {
//     final RenderBox box = context.findRenderObject() as RenderBox; final Offset pos = box.localToGlobal(Offset.zero);
//     _overlayEntry = OverlayEntry(builder: (c) => Stack(children: [
//       GestureDetector(onTap: _hideOverlay, child: Container(color: Colors.transparent, width: double.infinity, height: double.infinity)),
//       Positioned(left: 10, top: pos.dy - 65, child: Material(color: Colors.transparent, child: Container(padding: EdgeInsets.symmetric(horizontal: 8, vertical: 8), decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(30), boxShadow: [BoxShadow(color: Colors.black26, blurRadius: 12)]), child: Row(children: widget.emojiMap.entries.map((e){ bool sel = widget.post.myReaction==e.key; return GestureDetector(onTap: (){ _hideOverlay(); widget.onReaction(e.key); }, child: Container(margin: EdgeInsets.symmetric(horizontal: 4), padding: EdgeInsets.all(10), decoration: BoxDecoration(color: sel? widget.emojiColor[e.key]!.withOpacity(0.18):Colors.grey.shade100, shape: BoxShape.circle, border: sel? Border.all(color: widget.emojiColor[e.key]!, width: 2):null), child: Text(e.value, style: TextStyle(fontSize: 26)))); }).toList()),),),),
//     ])); Overlay.of(context).insert(_overlayEntry!);
//   }
//   void _hideOverlay(){ _overlayEntry?.remove(); _overlayEntry=null; }
//   @override Widget build(BuildContext context) {
//     return Builder(builder: (btnCtx){
//       return InkWell(onTap: () => widget.onReaction('like'), onLongPress: () => _showOverlay(btnCtx), child: Container(padding: const EdgeInsets.symmetric(vertical: 12), child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
//           if (widget.post.myReaction == null)...[const Icon(Icons.thumb_up_alt_outlined, size: 20, color: Color(0xFF65676B)), const SizedBox(width: 6), const Text('Like', style: TextStyle(color: Color(0xFF65676B), fontWeight: FontWeight.w600))]
//           else...[Text(widget.emojiMap[widget.post.myReaction]?? '👍', style: const TextStyle(fontSize: 18)), const SizedBox(width: 6), Text(widget.post.myReaction!.toUpperCase(), style: TextStyle(color: widget.emojiColor[widget.post.myReaction], fontWeight: FontWeight.bold, fontSize: 12))]
//         ])),);
//     });
//   }
// }
// class _MediaCarousel extends StatefulWidget { final List<PostMediaModel> mediaList; final String postId; final int postIndex; const _MediaCarousel({required this.mediaList, required this.postId, required this.postIndex}); @override State<_MediaCarousel> createState() => _MediaCarouselState(); }
// class _MediaCarouselState extends State<_MediaCarousel> {
//   late PageController _pageController; int _currentPage = 0; bool _showDots = true; final Map<int, VideoPlayerController> _videoControllers = {};
//   @override void initState() { super.initState(); _pageController = PageController(); _initVideos(); if (widget.mediaList.length > 1) Future.delayed(const Duration(seconds: 4), () { if (mounted) setState(() => _showDots = false); }); }
//   void _initVideos() { for (int i = 0; i < widget.mediaList.length; i++) { if (widget.mediaList[i].mediaType == 'video') { final c = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file)); _videoControllers[i] = c; c.initialize().then((_) { if (mounted) { setState(() {}); if (i==0) { c.setLooping(true); c.setVolume(0); c.play(); } } }); } } }
//   @override void dispose() { _pageController.dispose(); for (var c in _videoControllers.values) { c.dispose(); } super.dispose(); }
//   void _handleVisibility(bool v, int i) { final c = _videoControllers[i]; if (c!= null && c.value.isInitialized) { if (v) { c.setLooping(true); c.play(); } else { c.pause(); } } }
//   Future<void> _openFile(String url, String fileName) async { try { String? token = await AuthService.getToken(); Directory dir = await getTemporaryDirectory(); String savePath = '${dir.path}/${fileName.replaceAll(' ', '_')}'; if (!await File(savePath).exists()) { await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty? {"Authorization": "Bearer $token"} : {})); } await OpenFilex.open(savePath); } catch (e) { if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Open failed: $e'))); } }
//   @override Widget build(BuildContext context) { final maxHeight = MediaQuery.of(context).size.height * 0.55; return SizedBox(height: maxHeight, child: Stack(alignment: Alignment.center, children: [PageView.builder(controller: _pageController, itemCount: widget.mediaList.length, onPageChanged: (i) { _videoControllers[_currentPage]?.pause(); setState(() { _currentPage = i; _showDots = true; }); _videoControllers[i]?.setLooping(true); _videoControllers[i]?.play(); Future.delayed(const Duration(seconds: 4), () { if (mounted) setState(() => _showDots = false); }); }, itemBuilder: (c, i) { final m = widget.mediaList[i]; return VisibilityDetector(key: Key('${widget.postId}_$i'), onVisibilityChanged: (info) => _handleVisibility(info.visibleFraction > 0.5, i), child: _buildMediaItem(m, i, maxHeight)); }), if (widget.mediaList.length > 1 && _showDots) Positioned(bottom: 10, child: Container(padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5), decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(20)), child: Row(children: List.generate(widget.mediaList.length, (i) => Container(margin: const EdgeInsets.symmetric(horizontal: 3), width: _currentPage == i? 18 : 7, height: 7, decoration: BoxDecoration(borderRadius: BorderRadius.circular(10), color: _currentPage == i? Colors.white : Colors.white54)))))) ])); }
//   Widget _buildMediaItem(PostMediaModel media, int index, double maxHeight) { if (media.mediaType == 'video') { final c = _videoControllers[index]; if (c == null ||!c.value.isInitialized) return Container(height: maxHeight, color: Colors.black, child: const Center(child: CircularProgressIndicator(color: Colors.white))); return GestureDetector(onTap: () => _openFullScreen(context, index), child: Container(color: Colors.black, child: Center(child: AspectRatio(aspectRatio: c.value.aspectRatio, child: VideoPlayer(c))))); } else if (media.mediaType == 'image') { return GestureDetector(onTap: () => _openFullScreen(context, index), child: CachedNetworkImage(imageUrl: media.file, fit: BoxFit.cover, width: double.infinity, height: maxHeight)); } else if (media.file.toLowerCase().endsWith('.pdf')) { return Stack(children: [SfPdfViewer.network(media.file), Positioned(bottom: 10, right: 10, child: ElevatedButton.icon(onPressed: () => _openFile(media.file, media.fileName.isNotEmpty? media.fileName : 'doc.pdf'), icon: const Icon(Icons.open_in_new), label: const Text('Open'), style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF030F27), foregroundColor: Colors.white)))]); } else { return Container(color: const Color(0xFFF0F2F5), child: Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [const Icon(Icons.insert_drive_file, size: 60, color: Colors.grey), const SizedBox(height: 10), Text(media.fileName, style: const TextStyle(fontWeight: FontWeight.bold)), const SizedBox(height: 15), ElevatedButton.icon(onPressed: () => _openFile(media.file, media.fileName), icon: const Icon(Icons.open_in_new), label: const Text('Open'), style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF030F27), foregroundColor: Colors.white)) ]))) ; } }
//   void _openFullScreen(BuildContext context, int initialIndex) { _videoControllers.values.forEach((c) => c.pause()); Navigator.push(context, MaterialPageRoute(builder: (_) => _FullScreenViewer(mediaList: widget.mediaList, initialIndex: initialIndex))).then((_) { _videoControllers[_currentPage]?.setLooping(true); _videoControllers[_currentPage]?.play(); }); }
// }
// class _FullScreenViewer extends StatefulWidget { final List<PostMediaModel> mediaList; final int initialIndex; const _FullScreenViewer({required this.mediaList, required this.initialIndex}); @override State<_FullScreenViewer> createState() => _FullScreenViewerState(); }
// class _FullScreenViewerState extends State<_FullScreenViewer> {
//   late PageController _controller; late int _currentIndex; final Map<int, VideoPlayerController> _videoControllers = {};
//   @override void initState() { super.initState(); _currentIndex = widget.initialIndex; _controller = PageController(initialPage: widget.initialIndex); for (int i = 0; i < widget.mediaList.length; i++) { if (widget.mediaList[i].mediaType == 'video') { final c = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file)); _videoControllers[i] = c; c.initialize().then((_) { if (mounted) { setState(() {}); if (i == _currentIndex) { c.setLooping(true); c.play(); } } }); } } }
//   @override void dispose() { for (var c in _videoControllers.values) { c.dispose(); } _controller.dispose(); super.dispose(); }
//   Future<void> _openFile(String url, String name) async { String? token = await AuthService.getToken(); Directory dir = await getTemporaryDirectory(); String savePath = '${dir.path}/${name.replaceAll(' ', '_')}'; if (!await File(savePath).exists()) await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty? {"Authorization": "Bearer $token"} : {})); await OpenFilex.open(savePath); }
//   @override Widget build(BuildContext context) { return Scaffold(backgroundColor: Colors.black, appBar: AppBar(backgroundColor: Colors.black, iconTheme: const IconThemeData(color: Colors.white), title: Text('${_currentIndex + 1}/${widget.mediaList.length}', style: const TextStyle(color: Colors.white)), actions: [IconButton(icon: const Icon(Icons.open_in_new, color: Colors.white), onPressed: () => _openFile(widget.mediaList[_currentIndex].file, widget.mediaList[_currentIndex].fileName.isNotEmpty? widget.mediaList[_currentIndex].fileName : 'file_$_currentIndex'))]), body: PageView.builder(controller: _controller, itemCount: widget.mediaList.length, onPageChanged: (i) { _videoControllers[_currentIndex]?.pause(); setState(() => _currentIndex = i); _videoControllers[i]?.setLooping(true); _videoControllers[i]?.play(); }, itemBuilder: (c, i) { final m = widget.mediaList[i]; if (m.mediaType == 'video') { final con = _videoControllers[i]; return con!= null && con.value.isInitialized? Center(child: AspectRatio(aspectRatio: con.value.aspectRatio, child: VideoPlayer(con))) : const Center(child: CircularProgressIndicator(color: Colors.white)); } else if (m.file.toLowerCase().endsWith('.pdf')) { return SfPdfViewer.network(m.file); } else if (m.mediaType == 'image') { return InteractiveViewer(minScale: 0.5, maxScale: 4.0, child: CachedNetworkImage(imageUrl: m.file, fit: BoxFit.contain)); } else { return Center(child: Text(m.fileName, style: const TextStyle(color: Colors.white))); } })); }
// }

// // ===================== COMMENT SYSTEM - THREAD SERIES LOGIC =====================
// class CommentBottomSheet extends StatefulWidget { final PostModel post; final VoidCallback onCommentAdded; final Function(String) onGoToProfile; const CommentBottomSheet({super.key, required this.post, required this.onCommentAdded, required this.onGoToProfile}); @override State<CommentBottomSheet> createState() => _CommentBottomSheetState(); }
// class _CommentBottomSheetState extends State<CommentBottomSheet> {
//   List<CommentModel> comments = []; bool loading = true; bool isUploading = false; double uploadProgress = 0; final TextEditingController _controller = TextEditingController(); List<File> _selectedFiles = []; String? replyToId; String? replyToName; final ImagePicker _picker = ImagePicker();
//   final Map<String, List<CommentModel>> _localReplies = {}; final Map<String, bool> _expandedMap = {};
//   @override void initState() { super.initState(); _fetchComments(); }
//   Future<void> _fetchComments() async { try { final data = await CommentService.getComments(widget.post.id); if (mounted) setState(() { comments = data; loading = false; }); } catch (e) { if (mounted) setState(() => loading = false); } }
//   bool _checkFileSize(File file) { double sizeMB = file.lengthSync() / (1024*1024); if (sizeMB > 200) { showDialog(context: context, builder: (_) => AlertDialog(title: Row(children: [Icon(Icons.error, color: Colors.red), SizedBox(width: 8), Text("File Too Large")]), content: Text("This file is ${sizeMB.toStringAsFixed(1)}MB, which is much more than 200MB limit.\n\nPlease select a file under 200MB."), actions: [TextButton(onPressed: ()=> Navigator.pop(context), child: Text("OK", style: TextStyle(color: Color(0xFF030F27), fontWeight: FontWeight.bold)))],)); ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Video is much more than 200MB (${sizeMB.toStringAsFixed(1)}MB) - Not allowed"), backgroundColor: Colors.red)); return false; } return true; }
//   Future<void> _pickCameraPhoto() async { try { final XFile? photo = await _picker.pickImage(source: ImageSource.camera, imageQuality: 85); if (photo!= null && mounted) { File f = File(photo.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
//   Future<void> _pickCameraVideo() async { try { final XFile? v = await _picker.pickVideo(source: ImageSource.camera, maxDuration: Duration(minutes: 5)); if (v!= null && mounted) { File f = File(v.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
//   Future<void> _pickGalleryImage() async { try { final XFile? img = await _picker.pickImage(source: ImageSource.gallery, imageQuality: 85); if (img!= null && mounted) { File f = File(img.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
//   Future<void> _pickGalleryVideo() async { try { final XFile? v = await _picker.pickVideo(source: ImageSource.gallery); if (v!= null && mounted) { File f = File(v.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
//   Future<void> _pickFile() async { try { const XTypeGroup all = XTypeGroup(label: 'all'); final XFile? f = await openFile(acceptedTypeGroups: [all]); if (f!= null && mounted) { File file = File(f.path); if (_checkFileSize(file)) setState(() => _selectedFiles.add(file)); } } catch (e) {} }
//   Future<void> _openOptions() async { showModalBottomSheet(context: context, backgroundColor: Colors.white, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))), builder: (c) => SafeArea(child: Wrap(children: [ListTile(leading: const Icon(Icons.camera_alt, color: Colors.blue), title: const Text("Camera Photo"), onTap: () { Navigator.pop(c); _pickCameraPhoto(); }), ListTile(leading: const Icon(Icons.videocam, color: Colors.red), title: const Text("Camera Video"), onTap: () { Navigator.pop(c); _pickCameraVideo(); }), ListTile(leading: const Icon(Icons.photo_library, color: Colors.green), title: const Text("Gallery Photo"), onTap: () { Navigator.pop(c); _pickGalleryImage(); }), ListTile(leading: const Icon(Icons.video_library, color: Colors.purple), title: const Text("Gallery Video"), onTap: () { Navigator.pop(c); _pickGalleryVideo(); }), ListTile(leading: const Icon(Icons.attach_file, color: Colors.orange), title: const Text("Document"), onTap: () { Navigator.pop(c); _pickFile(); }),])),); }

//   Future<void> _send() async {
//     if (_controller.text.trim().isEmpty && _selectedFiles.isEmpty) return;
//     await WakelockPlus.enable(); setState(() { isUploading = true; uploadProgress = 0; }); String? curId = replyToId;
//     try {
//       final newComment = await CommentService.createComment(postId: widget.post.id, parentId: curId, content: _controller.text.trim(), files: _selectedFiles, onProgress: (p) { if (mounted) setState(() => uploadProgress = p); });
//       if (mounted) { setState(() { if (curId == null) { comments.insert(0, newComment); } else { _localReplies.putIfAbsent(curId, () => []); _localReplies[curId]!.insert(0, newComment); _expandedMap[curId] = true; for (var c in comments) { if (c.id == curId) c.repliesCount++; } } _controller.clear(); _selectedFiles = []; replyToId = null; replyToName = null; isUploading = false; uploadProgress = 0; }); widget.onCommentAdded(); }
//     } catch (e) { if (mounted) { setState(() => isUploading = false); ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed: $e'), backgroundColor: Colors.red)); } } finally { await WakelockPlus.disable(); }
//   }
//   @override Widget build(BuildContext context) {
//     return SafeArea(child: DraggableScrollableSheet(initialChildSize: 0.85, maxChildSize: 0.95, minChildSize: 0.5, expand: false, builder: (context, scrollController) {
//       return Column(children: [
//         const SizedBox(height: 10), Container(width: 40, height: 4, decoration: BoxDecoration(color: Colors.grey[300], borderRadius: BorderRadius.circular(10))), const SizedBox(height: 10),
//         Text("Comments ${widget.post.commentsCount}", style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)), const Divider(),
//         if (isUploading) Padding(padding: const EdgeInsets.all(8), child: Column(children: [LinearProgressIndicator(value: uploadProgress>0? uploadProgress/100 : null, color: const Color(0xFF030F27)), const SizedBox(height: 4), Text(uploadProgress>0? '${uploadProgress.toStringAsFixed(1)}% uploading...' : 'Compressing...', style: const TextStyle(fontSize: 12))])),
//         Expanded(child: loading? const Center(child: CircularProgressIndicator()) : comments.isEmpty? const Center(child: Text("No comments yet")) : ListView.builder(controller: scrollController, itemCount: comments.length, itemBuilder: (c, i) => CommentTile(key: ValueKey(comments[i].id + comments[i].repliesCount.toString() + (_localReplies[comments[i].id]?.length??0).toString() + (_expandedMap[comments[i].id]?.toString()??'')), comment: comments[i], postOwnerId: widget.post.user.id.toString(), level: 0, localReplies: _localReplies[comments[i].id]??[], allLocalReplies: _localReplies, expandedMap: _expandedMap, onReply: (id, name) => setState(() { replyToId = id; replyToName = name; }), onDeleted: (id) => setState(() => comments.removeWhere((x) => x.id == id)), onEdited: (updated) => setState(() { int idx = comments.indexWhere((x) => x.id == updated.id); if (idx!= -1) comments[idx] = updated; }), onHidden: (id) => setState(() => comments.removeWhere((x) => x.id == id)), onGoToProfile: widget.onGoToProfile))),
//         if (replyToName!= null) Container(width: double.infinity, color: const Color(0xFF030F27), padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10), child: Row(children: [const Icon(Icons.reply, color: Colors.white70, size: 16), const SizedBox(width: 8), Expanded(child: Text("Replying to @$replyToName", style: TextStyle(fontSize: 13, fontWeight: FontWeight.bold, color: Colors.white))), GestureDetector(onTap: () => setState(() { replyToId = null; replyToName = null; }), child: Icon(Icons.close, color: Colors.white, size: 18)) ])),
//         if (_selectedFiles.isNotEmpty) Container(height: 90, color: Colors.grey[100], child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Padding(padding: const EdgeInsets.all(6), child: Text('${_selectedFiles.length} selected', style: TextStyle(fontSize: 12, color: Colors.green, fontWeight: FontWeight.bold))), Expanded(child: ListView.builder(scrollDirection: Axis.horizontal, itemCount: _selectedFiles.length, itemBuilder: (c,i){ bool isVideo = _selectedFiles[i].path.toLowerCase().endsWith('.mp4') || _selectedFiles[i].path.toLowerCase().endsWith('.mov'); return Stack(children: [Container(margin: const EdgeInsets.all(6), width: 70, height: 70, decoration: BoxDecoration(borderRadius: BorderRadius.circular(10), color: Colors.black), child: ClipRRect(borderRadius: BorderRadius.circular(10), child: isVideo? const Icon(Icons.videocam, color: Colors.white) : Image.file(_selectedFiles[i], fit: BoxFit.cover))), Positioned(top: 0, right: 0, child: GestureDetector(onTap: ()=>setState(()=>_selectedFiles.removeAt(i)), child: Container(decoration: const BoxDecoration(color: Colors.red, shape: BoxShape.circle), child: const Icon(Icons.close, size: 18, color: Colors.white))))]); })), ])),
//         Padding(padding: EdgeInsets.only(left: 10, right: 10, bottom: MediaQuery.of(context).viewPadding.bottom + 10, top: 8), child: Row(children: [IconButton(icon: const Icon(Icons.attach_file, color: Color(0xFF030F27)), onPressed: isUploading? null : _openOptions), Expanded(child: TextField(controller: _controller, enabled:!isUploading, decoration: const InputDecoration(hintText: "Add comment...", border: OutlineInputBorder(borderRadius: BorderRadius.all(Radius.circular(25))), contentPadding: EdgeInsets.symmetric(horizontal: 16, vertical: 10)))), const SizedBox(width: 8), isUploading? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)) : CircleAvatar(backgroundColor: const Color(0xFF030F27), child: IconButton(icon: const Icon(Icons.send, color: Colors.white), onPressed: _send))]))
//       ]);
//     }));
//   }
// }

// class CommentTile extends StatefulWidget {
//   final CommentModel comment; final String postOwnerId; final Function(String, String) onReply; final Function(String)? onDeleted; final Function(CommentModel)? onEdited; final Function(String)? onHidden; final List<CommentModel> localReplies; final Map<String, List<CommentModel>> allLocalReplies; final Map<String, bool> expandedMap; final int level; final Function(String) onGoToProfile;
//   const CommentTile({super.key, required this.comment, required this.postOwnerId, required this.onReply, this.onDeleted, this.onEdited, this.onHidden, this.localReplies = const [], this.allLocalReplies = const {}, this.expandedMap = const {}, this.level = 0, required this.onGoToProfile});
//   @override State<CommentTile> createState() => _CommentTileState();
// }
// class _CommentTileState extends State<CommentTile> {
//   List<CommentModel> replies = []; bool showReplies = false; bool loadingReplies = false; String? _myUserId;
//   final Map<String, String> _emojiMap = {'like': '👍','confuse': '🤔','wrong': '❗','imp': '⭐','explain': '💡'};
//   final Map<String, Color> _emojiColor = {'like': Color(0xFF1877F2),'confuse': Color(0xFFF7B928),'wrong': Color(0xFFE0245E),'imp': Color(0xFFFFAD33),'explain': Color(0xFF45BD62)};
//   OverlayEntry? _overlayEntry;
//   @override void initState() { super.initState(); _getMyId(); if (widget.localReplies.isNotEmpty) { replies = widget.localReplies; showReplies = true; } if(widget.expandedMap[widget.comment.id]==true){ showReplies = true; if(replies.isEmpty) _loadReplies(); } }
//   @override void didUpdateWidget(covariant CommentTile oldWidget) { super.didUpdateWidget(oldWidget); if (widget.localReplies.length!= oldWidget.localReplies.length || widget.expandedMap[widget.comment.id]==true) { setState(() { final ids = replies.map((e)=>e.id).toSet(); for(var r in widget.localReplies){ if(!ids.contains(r.id)) replies.insert(0, r); } if(widget.localReplies.isNotEmpty) showReplies = true; }); } }
//   Future<void> _getMyId() async { final id = await AuthService.getUserId(); if(mounted) setState(()=> _myUserId = id); }
//   bool get _isMyComment => _myUserId!=null && widget.comment.user.id.toString() == _myUserId.toString();
//   bool get _isPostOwner => _myUserId!=null && widget.postOwnerId == _myUserId.toString();
//   bool get _canShowMenu => _isMyComment || _isPostOwner;
//   void _showReactionOverlay(BuildContext context) { final RenderBox box = context.findRenderObject() as RenderBox; final Offset pos = box.localToGlobal(Offset.zero); _overlayEntry = OverlayEntry(builder: (c) => Stack(children: [GestureDetector(onTap: ()=> _hideOverlay(), child: Container(color: Colors.transparent, width: double.infinity, height: double.infinity)), Positioned(left: 20, top: pos.dy - 60, child: Material(color: Colors.transparent, child: Container(padding: EdgeInsets.symmetric(horizontal: 8, vertical: 6), decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(30), boxShadow: [BoxShadow(color: Colors.black26, blurRadius: 10)]), child: Row(children: _emojiMap.entries.map((e){ bool sel = widget.comment.myReaction==e.key; return GestureDetector(onTap: (){ _hideOverlay(); _handleReaction(e.key); }, child: Container(margin: EdgeInsets.symmetric(horizontal: 4), padding: EdgeInsets.all(8), decoration: BoxDecoration(color: sel? _emojiColor[e.key]!.withOpacity(0.15):Colors.grey.shade100, shape: BoxShape.circle, border: sel? Border.all(color: _emojiColor[e.key]!, width: 2):null), child: Text(e.value, style: TextStyle(fontSize: 26)))); }).toList()),),),),])); Overlay.of(context).insert(_overlayEntry!); }
//   void _hideOverlay(){ _overlayEntry?.remove(); _overlayEntry=null; }

//   // 🔥 THREAD LOGIC: All replies in one series
//   Future<void> _loadReplies() async {
//     setState(()=> loadingReplies = true);
//     try{
//       final data = await CommentService.getReplies(widget.comment.id);
//       final merged = [...widget.localReplies,...data];
//       final ids = <String>{};
//       final unique = merged.where((e)=> ids.add(e.id)).toList();
//       setState((){ replies = unique; showReplies = true; loadingReplies = false; });
//     }catch(e){ setState(()=> loadingReplies = false); }
//   }

//   Future<void> _handleReaction(String reaction) async { final old = widget.comment.myReaction; final oldCount = widget.comment.likesCount; setState((){ if(old==reaction){ widget.comment.myReaction=null; if(widget.comment.likesCount>0) widget.comment.likesCount--; } else{ if(old==null) widget.comment.likesCount++; widget.comment.myReaction=reaction; } }); try{ final res = await CommentService.toggleCommentReaction(widget.comment.id, reaction); if(mounted) setState((){ widget.comment.myReaction = res['my_reaction']??res['myReaction']; var counts = res['counts']??res['reaction_counts']; if(counts!=null){ widget.comment.reactionCounts = Map<String,int>.from(counts.map((k,v)=>MapEntry(k.toString(),(v as int?)??0))); widget.comment.likesCount = counts['total']?? oldCount; } }); }catch(e){ if(mounted) setState((){ widget.comment.myReaction=old; widget.comment.likesCount=oldCount; }); } }
//   void _showOptions(){ List<Widget> options = []; if(_isMyComment){ options.add(ListTile(leading: Icon(Icons.edit, color: Colors.blue), title: Text("Edit"), onTap: (){ Navigator.pop(context); _editDialog(); })); options.add(ListTile(leading: Icon(Icons.delete, color: Colors.red), title: Text("Delete"), onTap: (){ Navigator.pop(context); _deleteConfirm(); })); } if(_isPostOwner &&!_isMyComment){ options.add(ListTile(leading: Icon(Icons.visibility_off, color: Colors.orange), title: Text("Hide Comment"), onTap: (){ Navigator.pop(context); _hideComment(); })); } if(options.isEmpty) return; showModalBottomSheet(context: context, backgroundColor: Colors.white, shape: RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(12))), builder: (c)=> SafeArea(child: Wrap(children: options))); }
//   void _editDialog(){ TextEditingController ctrl = TextEditingController(text: widget.comment.content); showDialog(context: context, builder: (_)=> AlertDialog(title: Text("Edit Comment"), content: TextField(controller: ctrl, maxLines: 4, autofocus: true, decoration: InputDecoration(border: OutlineInputBorder(), hintText: "Edit comment")), actions: [TextButton(onPressed: ()=> Navigator.pop(context), child: Text("Cancel")), ElevatedButton(style: ElevatedButton.styleFrom(backgroundColor: Color(0xFF030F27)), onPressed: () async { if(ctrl.text.trim().isEmpty) return; Navigator.pop(context); try{ final updated = await CommentService.editComment(commentId: widget.comment.id, content: ctrl.text.trim()); if(mounted) setState(()=> widget.comment.content = updated.content); widget.comment.isEdited = true; if(widget.onEdited!=null) widget.onEdited!(updated); if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Edited"), backgroundColor: Colors.green)); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Edit failed: $e"), backgroundColor: Colors.red)); } }, child: Text("Save", style: TextStyle(color: Colors.white))) ])); }
//   void _deleteConfirm(){ showDialog(context: context, builder: (_)=> AlertDialog(title: Text("Delete?"), content: Text("Delete this comment?"), actions: [TextButton(onPressed: ()=> Navigator.pop(context), child: Text("Cancel")), ElevatedButton(style: ElevatedButton.styleFrom(backgroundColor: Colors.red), onPressed: () async { Navigator.pop(context); try{ await CommentService.deleteComment(widget.comment.id); if(widget.onDeleted!=null) widget.onDeleted!(widget.comment.id); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Delete failed: $e"))); } }, child: Text("Delete", style: TextStyle(color: Colors.white)))])); }
//   void _hideComment() async { try{ await CommentService.hideComment(widget.comment.id); if(widget.onHidden!=null) widget.onHidden!(widget.comment.id); if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Comment hidden"), backgroundColor: Colors.orange)); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Hide failed: $e"))); } }
//   bool _isImage(String url){ final l=url.toLowerCase(); return l.endsWith('.png')||l.endsWith('.jpg')||l.endsWith('.jpeg')||l.endsWith('.webp')||l.endsWith('.gif'); }
//   bool _isVideo(String url,String type){ final l=url.toLowerCase(); return type=='video'||l.endsWith('.mp4')||l.endsWith('.mov')||l.endsWith('.mkv'); }
//   Future<void> _openFile(String url,String fileName) async { try{ String? token = await AuthService.getToken(); Directory dir=await getTemporaryDirectory(); String savePath='${dir.path}/${fileName.replaceAll(' ', '_')}'; if(!await File(savePath).exists()){ await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty?{"Authorization":"Bearer $token"}:{})); } await OpenFilex.open(savePath); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Open failed: $e'))); } }
//   void _openMedia(dynamic m){ if(_isImage(m.file)) Navigator.push(context, MaterialPageRoute(builder: (_)=> _CommentImageFullScreen(url: m.file, fileName: m.fileName))); else if(_isVideo(m.file,m.mediaType)) Navigator.push(context, MaterialPageRoute(builder: (_)=> _CommentVideoFullScreen(url: m.file, fileName: m.fileName))); else _openFile(m.file,m.fileName); }
//   Widget _buildCommentVideoThumb(){ return Container(height: 90, width: 130, decoration: BoxDecoration(color: Colors.black, borderRadius: BorderRadius.circular(8)), child: Stack(alignment: Alignment.center, children: [Icon(Icons.videocam, color: Colors.white30, size: 30), Container(padding: EdgeInsets.all(4), decoration: BoxDecoration(color: Colors.black54, shape: BoxShape.circle), child: Icon(Icons.play_arrow, color: Colors.white, size: 24))])); }

//   @override Widget build(BuildContext context) {
//     List<MapEntry<String,int>> sorted = widget.comment.reactionCounts.entries.where((e)=> e.value>0 && e.key!='total').toList(); sorted.sort((a,b)=> b.value.compareTo(a.value)); var top3 = sorted.take(3).toList();
//     // 🔥 FIX: level 0 = main comment, level 1+ = sab same indent me - ek hi series
//     double leftPad = widget.level==0? 12 : 36;
//     return Padding(padding: EdgeInsets.only(left: leftPad, right: 12, top: 8, bottom: 8), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
//       // Thread Line
//       Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
//         GestureDetector(onTap: ()=> widget.onGoToProfile(widget.comment.user.username), child: CircleAvatar(radius: widget.level==0? 18 : 14, backgroundImage: widget.comment.user.profilePicture!=null && widget.comment.user.profilePicture!.isNotEmpty? CachedNetworkImageProvider(widget.comment.user.profilePicture!) : null)),
//         const SizedBox(width: 10),
//         Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
//           GestureDetector(onLongPress: _canShowMenu? ()=> _showOptions() : null, child: Container(padding: EdgeInsets.symmetric(horizontal: 14, vertical: 10), decoration: BoxDecoration(color: Colors.grey.shade100, borderRadius: BorderRadius.circular(16)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
//             Row(children: [GestureDetector(onTap: ()=> widget.onGoToProfile(widget.comment.user.username), child: Text(widget.comment.user.username, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: Color(0xFF030F27)))), if(widget.comment.isEdited) Padding(padding: EdgeInsets.only(left: 6), child: Text("(edited)", style: TextStyle(fontSize: 10, color: Colors.grey))), if(_isMyComment) Padding(padding: EdgeInsets.only(left: 6), child: Text("(you)", style: TextStyle(fontSize: 10, color: Colors.blue, fontWeight: FontWeight.bold)))]),
//             if(widget.comment.content.isNotEmpty) Padding(padding: EdgeInsets.only(top: 6), child: Text(widget.comment.content, style: TextStyle(fontSize: 15, height: 1.4))),
//             if(widget.comment.media.isNotEmpty) Padding(padding: EdgeInsets.only(top: 8), child: Wrap(spacing: 6, runSpacing: 6, children: widget.comment.media.map((m){ if(_isImage(m.file)){ return GestureDetector(onTap: ()=> _openMedia(m), child: ClipRRect(borderRadius: BorderRadius.circular(8), child: CachedNetworkImage(imageUrl: m.file, height: 90, width: 90, fit: BoxFit.cover))); }else if(_isVideo(m.file,m.mediaType)){ return GestureDetector(onTap: ()=> _openMedia(m), child: _buildCommentVideoThumb()); }else{ return InkWell(onTap: ()=> _openFile(m.file,m.fileName), child: Container(padding: EdgeInsets.symmetric(horizontal: 10, vertical: 8), decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8), border: Border.all(color: Colors.grey.shade300)), child: Row(mainAxisSize: MainAxisSize.min, children: [Icon(Icons.description, size: 16), SizedBox(width: 6), SizedBox(width: 70, child: Text(m.fileName, style: TextStyle(fontSize: 12), overflow: TextOverflow.ellipsis))]))); } }).toList())),
//             if(top3.isNotEmpty) Padding(padding: EdgeInsets.only(top: 6), child: Row(children: [Row(children: top3.map((e)=> Text(_emojiMap[e.key]??'', style: TextStyle(fontSize: 12))).toList()), SizedBox(width: 6), Text('${widget.comment.likesCount}', style: TextStyle(fontSize: 11, color: Colors.grey[600]))]))
//           ]))),
//           const SizedBox(height: 6),
//           Row(children: [
//             Text(timeago.format(widget.comment.createdAt), style: TextStyle(fontSize: 11, color: Colors.grey)),
//             SizedBox(width: 14),
//             Builder(builder: (likeCtx) {
//               return GestureDetector(
//                 onTap: ()=> _handleReaction('like'),
//                 onLongPress: ()=> _showReactionOverlay(likeCtx),
//                 child: Container(padding: EdgeInsets.symmetric(horizontal: 10, vertical: 4), decoration: BoxDecoration(color: widget.comment.myReaction!=null? _emojiColor[widget.comment.myReaction]!.withOpacity(0.12) : Colors.transparent, borderRadius: BorderRadius.circular(20),), child: Row(children: [Icon(widget.comment.myReaction==null? Icons.thumb_up_alt_outlined : Icons.thumb_up_alt, size: 16, color: widget.comment.myReaction!=null? _emojiColor[widget.comment.myReaction] : Color(0xFF65676B)), SizedBox(width: 4), Text(widget.comment.myReaction!=null? widget.comment.myReaction!.toUpperCase() : 'Like', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: widget.comment.myReaction!=null? _emojiColor[widget.comment.myReaction] : Color(0xFF65676B))), if(widget.comment.myReaction!=null)...[SizedBox(width: 4), Text(_emojiMap[widget.comment.myReaction]??'', style: TextStyle(fontSize: 12))]]),),
//               );
//             }),
//             SizedBox(width: 12),
//             GestureDetector(onTap: ()=> widget.onReply(widget.comment.id, widget.comment.user.username), child: Row(children: [Icon(Icons.chat_bubble_outline, size: 14, color: Color(0xFF65676B)), SizedBox(width: 4), Text("Reply", style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: Color(0xFF65676B)))])),
//             if(widget.comment.repliesCount>0 || widget.localReplies.isNotEmpty)...[SizedBox(width: 12), GestureDetector(onTap: (){ if(showReplies) setState(()=> showReplies=false); else _loadReplies(); }, child: Text(loadingReplies? "Loading..." : showReplies? "Hide ${widget.comment.repliesCount + widget.localReplies.length}" : "${widget.comment.repliesCount + widget.localReplies.length} replies", style: TextStyle(fontSize: 12, color: Colors.blue, fontWeight: FontWeight.w600)))],
//           ]),
//         ])),
//         if(_canShowMenu) IconButton(icon: Icon(Icons.more_horiz, size: 18), onPressed: ()=> _showOptions(), padding: EdgeInsets.zero, constraints: BoxConstraints(), visualDensity: VisualDensity.compact)
//       ]),
//       // 🔥 SAB REPLY EK HI SERIES ME
//       if(showReplies) Padding(
//         padding: EdgeInsets.only(top: 8, left: widget.level==0? 12 : 0),
//         child: Container(
//           decoration: BoxDecoration(
//             border: Border(left: BorderSide(color: Colors.grey.shade300, width: 2))
//           ),
//           child: Column(children: replies.map((r){
//             List<CommentModel> nestedLocal = widget.allLocalReplies[r.id]??[];
//             return CommentTile(
//               comment: r,
//               postOwnerId: widget.postOwnerId,
//               level: 1, // 🔥 FIXED: sabka level 1 taki ek hi series me aaye
//               localReplies: nestedLocal,
//               allLocalReplies: widget.allLocalReplies,
//               expandedMap: widget.expandedMap,
//               onReply: widget.onReply,
//               onDeleted: (id)=> setState(()=> replies.removeWhere((x)=> x.id==id)),
//               onEdited: (updated)=> setState((){ int idx=replies.indexWhere((x)=> x.id==updated.id); if(idx!=-1) replies[idx]=updated; }),
//               onHidden: (id)=> setState(()=> replies.removeWhere((x)=> x.id==id)),
//               onGoToProfile: widget.onGoToProfile
//             );
//           }).toList())
//         )
//       ),
//     ]));
//   }
// }

// class _CommentImageFullScreen extends StatelessWidget { final String url; final String fileName; const _CommentImageFullScreen({required this.url, required this.fileName}); Future<void> _open(String url, String name, BuildContext context) async { String? token = await AuthService.getToken(); Directory dir = await getTemporaryDirectory(); String savePath = '${dir.path}/${name.replaceAll(' ', '_')}'; if (!await File(savePath).exists()) await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty? {"Authorization": "Bearer $token"} : {})); await OpenFilex.open(savePath); } @override Widget build(BuildContext context) { return Scaffold(backgroundColor: Colors.black, appBar: AppBar(backgroundColor: Colors.black, iconTheme: IconThemeData(color: Colors.white), title: Text(fileName, style: TextStyle(color: Colors.white, fontSize: 14)), actions: [IconButton(icon: Icon(Icons.open_in_new, color: Colors.white), onPressed: () => _open(url, fileName, context))]), body: SizedBox(width: double.infinity, height: double.infinity, child: InteractiveViewer(minScale: 0.5, maxScale: 6.0, child: Center(child: CachedNetworkImage(imageUrl: url, fit: BoxFit.contain))))); } }
// class _CommentVideoFullScreen extends StatefulWidget { final String url; final String fileName; const _CommentVideoFullScreen({required this.url, required this.fileName}); @override State<_CommentVideoFullScreen> createState() => _CommentVideoFullScreenState(); }
// class _CommentVideoFullScreenState extends State<_CommentVideoFullScreen> {
//   late VideoPlayerController _controller; bool _initialized = false; bool _showControls = true; Timer? _hideTimer;
//   @override void initState() { super.initState(); SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky); _controller = VideoPlayerController.networkUrl(Uri.parse(widget.url))..initialize().then((_) { if (mounted) { setState(() => _initialized = true); _controller.setLooping(true); _controller.play(); _startHideTimer(); } }); _controller.addListener(() { if (mounted) setState(() {}); }); }
//   @override void dispose() { _hideTimer?.cancel(); _controller.dispose(); SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge); SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]); super.dispose(); }
//   void _startHideTimer() { _hideTimer?.cancel(); _hideTimer = Timer(Duration(seconds: 5), () { if (mounted) setState(() => _showControls = false); }); }
//   void _toggleControls() { setState(() => _showControls =!_showControls); if (_showControls) _startHideTimer(); }
//   String _format(Duration d) => "${d.inMinutes}:${(d.inSeconds%60).toString().padLeft(2,'0')}";
//   @override Widget build(BuildContext context) { return Scaffold(backgroundColor: Colors.black, body: GestureDetector(onTap: _toggleControls, child: Stack(children: [Center(child: _initialized? AspectRatio(aspectRatio: _controller.value.aspectRatio, child: VideoPlayer(_controller)) : CircularProgressIndicator(color: Colors.white)), if (_showControls) Positioned(top: 0, left: 0, right: 0, child: AppBar(backgroundColor: Colors.black54, iconTheme: IconThemeData(color: Colors.white), title: Text(widget.fileName, style: TextStyle(color: Colors.white, fontSize: 14)), actions: [IconButton(icon: Icon(Icons.close, color: Colors.white), onPressed: () => Navigator.pop(context))])), if (_showControls && _initialized) Positioned(bottom: 0, left: 0, right: 0, child: Container(padding: EdgeInsets.only(left: 10, right: 10, bottom: MediaQuery.of(context).padding.bottom + 20, top: 10), decoration: BoxDecoration(gradient: LinearGradient(begin: Alignment.bottomCenter, end: Alignment.topCenter, colors: [Colors.black87, Colors.transparent])), child: Column(children: [VideoProgressIndicator(_controller, allowScrubbing: true, colors: VideoProgressColors(playedColor: Color(0xFFEE0979))), SizedBox(height: 12), Row(children: [IconButton(icon: Icon(_controller.value.isPlaying? Icons.pause_circle_filled : Icons.play_circle_filled, color: Colors.white, size: 44), onPressed: () { setState(() { _controller.value.isPlaying? _controller.pause() : _controller.play(); }); _startHideTimer(); }), Text(_format(_controller.value.position), style: TextStyle(color: Colors.white, fontSize: 12)), Text(' / ${_format(_controller.value.duration)}', style: TextStyle(color: Colors.white54, fontSize: 12)), Spacer(), IconButton(icon: Icon(Icons.replay_10, color: Colors.white), onPressed: () { _controller.seekTo(_controller.value.position - Duration(seconds: 10)); _startHideTimer(); }), IconButton(icon: Icon(Icons.forward_10, color: Colors.white), onPressed: () { _controller.seekTo(_controller.value.position + Duration(seconds: 10)); _startHideTimer(); }),])]))),])),); }
// }


































import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:timeago/timeago.dart' as timeago;
import 'package:video_player/video_player.dart';
import 'package:share_plus/share_plus.dart';
import 'package:visibility_detector/visibility_detector.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:image_picker/image_picker.dart';
import 'package:open_filex/open_filex.dart';
import 'package:file_selector/file_selector.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
import 'package:easy_audience_network_plus/easy_audience_network.dart';

import '../profile/screens/profile.dart';
import '../profile/screens/target_profile.dart';
import '../profile/api_service.dart' as ProfileApi;
import 'search/search.dart';
import 'post/screens/new_post.dart';
import '../services/home_api_model_service.dart';
import '../services/comment_service.dart';
import '../services/auth_service.dart';
import '../message/screens/conversations_screen.dart';

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
  Set<String> _savedIds = {};
  String? _myUsername;

  @override void initState() {
    super.initState();
    _initAds();
    _loadFeed(); _loadSaved(); _loadMyUsername(); _scrollController.addListener(_onScroll);
  }

  Future<void> _initAds() async { await EasyAudienceNetwork.init(testMode: true); }
  @override void dispose() { _scrollController.dispose(); super.dispose(); }
  void _onScroll() { if (_scrollController.position.pixels >= _scrollController.position.maxScrollExtent - 300) { if (!_isLoadingMore && _hasMore) _loadMore(); } }
  Future<void> _loadSaved() async { final prefs = await SharedPreferences.getInstance(); setState(() => _savedIds = (prefs.getStringList('saved_posts')?? []).toSet()); }

  Future<void> _toggleSave(PostModel post) async {
    final postId = post.id;
    final wasSaved = post.isSaved;
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      post.isSaved =!wasSaved;
      if (post.isSaved) { _savedIds.add(postId); post.savesCount++; } else { _savedIds.remove(postId); if (post.savesCount > 0) post.savesCount--; }
    });
    await prefs.setStringList('saved_posts', _savedIds.toList());
    try {
      final res = await HomeFeedService.toggleSave(postId);
      final bool apiIsSaved = res['is_saved']?? (res['status'] == 'saved');
      final int? apiCount = res['saves_count'];
      if (mounted) {
        setState(() {
          post.isSaved = apiIsSaved;
          if (apiCount!= null) post.savesCount = apiCount;
          if (apiIsSaved) _savedIds.add(postId); else _savedIds.remove(postId);
        });
        await prefs.setStringList('saved_posts', _savedIds.toList());
      }
    } catch (e) {
      if (mounted) {
        setState(() { post.isSaved = wasSaved; if (wasSaved) _savedIds.add(postId); else _savedIds.remove(postId); });
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Save failed: $e')));
      }
    }
  }

  Future<void> _loadMyUsername() async {
    try { final d = await ProfileApi.ApiService.getProfile(); _myUsername = d.username; }
    catch (_) { try{ final t=await AuthService.getToken(); if(t!=null){ String p=base64.normalize(t.split('.')[1]); _myUsername=jsonDecode(utf8.decode(base64Url.decode(p)))['username']?.toString(); } }catch(_){} }
  }
  Future<void> _goToProfile(String username) async {
    if(username.trim().isEmpty) return;
    if(_myUsername==null) await _loadMyUsername();
    bool isMe = _myUsername!=null && _myUsername!.toLowerCase().trim()==username.toLowerCase().trim();
    if(!mounted) return;
    if(isMe) { if(Navigator.canPop(context)){ Navigator.pop(context); } await Future.delayed(Duration(milliseconds: 100)); if(!mounted) return; setState(() => _selectedIndex = 2); }
    else { Navigator.push(context, MaterialPageRoute(builder:(_)=>TargetProfilePage(username: username))); }
  }
  Future<void> _loadFeed({bool refresh = false}) async {
    if (refresh) { setState(() { _isLoading = true; _currentPage = 1; }); try { final feed = await HomeFeedService.refreshFeed(page: 1, pageSize: 20); if(mounted) setState(() { _posts = feed.results; _isLoading = false; _hasMore = feed.next!= null; _savedIds.addAll(feed.results.where((p)=>p.isSaved).map((p)=>p.id)); }); } catch (e) { if(mounted) setState(() => _isLoading = false); } return; }
    try {
      final cached = await HomeFeedService.getCachedFeed();
      if (cached!= null && mounted && _posts.isEmpty) { setState(() { _posts = cached.results; _isLoading = false; _hasMore = cached.next!= null; _savedIds.addAll(cached.results.where((p)=>p.isSaved).map((p)=>p.id)); }); }
      final feed = await HomeFeedService.getHomeFeed(page: 1, pageSize: 20);
      if(mounted) setState(() { _posts = feed.results; _isLoading = false; _hasMore = feed.next!= null; _currentPage = 1; _savedIds.addAll(feed.results.where((p)=>p.isSaved).map((p)=>p.id)); });
    } catch (e) { if(mounted) setState(() => _isLoading = false); }
  }
  Future<void> _loadMore() async { if (_isLoadingMore ||!_hasMore) return; setState(() => _isLoadingMore = true); _currentPage++; try { final feed = await HomeFeedService.getHomeFeed(page: _currentPage, pageSize: 20); if(mounted) setState(() { _posts.addAll(feed.results); _isLoadingMore = false; _hasMore = feed.next!= null; _savedIds.addAll(feed.results.where((p)=>p.isSaved).map((p)=>p.id)); }); } catch (e) { _currentPage--; if(mounted) setState(() => _isLoadingMore = false); } }
  void _onItemTapped(int i) => setState(() => _selectedIndex = i);
  void _sharePost(PostModel p) { String t = p.content?? ''; if (p.media.isNotEmpty) t += '\n\n${p.media.first.file}'; Share.share(t); }
  void _openCommentSheet(PostModel post) { showModalBottomSheet(context: context, isScrollControlled: true, backgroundColor: Colors.transparent, builder: (_) => Padding(padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom), child: Container(decoration: const BoxDecoration(color: Colors.white, borderRadius: BorderRadius.vertical(top: Radius.circular(20))), child: CommentBottomSheet(post: post, onCommentAdded: () => setState(() => post.commentsCount++), onGoToProfile: _goToProfile)))); }
  Future<void> _handleReaction(PostModel post, String reaction) async { final old = post.myReaction; final idx = _posts.indexWhere((p) => p.id == post.id); if (idx == -1) return; setState(() { if (old == reaction) { _posts[idx].myReaction = null; _posts[idx].likesCount--; } else { if (old==null) _posts[idx].likesCount++; _posts[idx].myReaction = reaction; } }); try { final res = await HomeFeedService.toggleReaction(post.id, reaction); final c = res['counts']; if(mounted) setState(() { _posts[idx].likeCount = c['like']; _posts[idx].confuseCount = c['confuse']; _posts[idx].wrongCount = c['wrong']; _posts[idx].impCount = c['imp']; _posts[idx].explainCount = c['explain']; _posts[idx].likesCount = c['total']; _posts[idx].myReaction = res['my_reaction']; }); } catch (e) { if(mounted) setState(() => _posts[idx].myReaction = old); } }

  // 🔥 BANNER -> NATIVE AD
  Widget _buildAd() {
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      color: Colors.white,
      child: Container(
        height: 320,
        width: double.infinity,
        child: NativeAd(
          placementId: NativeAd.testPlacementId,
          adType: NativeAdType.NATIVE_AD,
          width: double.infinity,
          height: 320,
          backgroundColor: Colors.white,
          titleColor: Colors.black,
          descriptionColor: Colors.black54,
          buttonColor: const Color(0xFF030F27),
          buttonTitleColor: Colors.white,
          buttonBorderColor: const Color(0xFF030F27),
          listener: NativeAdListener(
            onLoaded: ()=> print("Native Ad Loaded"),
            onError: (c,m)=> print("Native Ad Error $c $m"),
            onClicked: ()=> print("Native Ad Clicked"),
          ),
        ),
      ),
    );
  }

  Widget _homePage() {
    return Container(color: const Color(0xFFF0F2F5), child: RefreshIndicator(onRefresh: () => _loadFeed(refresh: true), child: CustomScrollView(controller: _scrollController, slivers: [
      SliverAppBar(automaticallyImplyLeading: false, backgroundColor: const Color(0xFF030F27), toolbarHeight: 70, floating: true, snap: true, title: Row(children: [Image.asset('assets/slogo1.png', height: 42, errorBuilder: (_, __, ___) => const Text("LearnScroll", style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 22)))]), actions: [
        IconButton(
          onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const ConversationsScreen())),
          icon: const Icon(Icons.chat_bubble_outline_rounded, color: Colors.white, size: 24),
        ),
        Container(margin: EdgeInsets.only(right: 14), height: 44, width: 44, decoration: BoxDecoration(gradient: LinearGradient(colors: [Color(0xFFFF6A00), Color(0xFFEE0979)]), shape: BoxShape.circle), child: IconButton(onPressed: () async { await Navigator.push(context, MaterialPageRoute(builder: (_) => const NewPost())); _loadFeed(refresh: true); }, icon: Icon(Icons.edit_rounded, color: Colors.white, size: 22))),
      ]),
      if (_isLoading && _posts.isEmpty) const SliverFillRemaining(child: Center(child: CircularProgressIndicator()))
      else SliverList(delegate: SliverChildBuilderDelegate((context, index) {
        if (_hasMore && index == _posts.length + (_posts.length ~/ 5)) return const Padding(padding: EdgeInsets.all(16), child: Center(child: CircularProgressIndicator()));
        if (index % 6 == 5) return _buildAd();
        int postIndex = index - (index ~/ 6);
        if (postIndex < _posts.length) return _buildPostCard(_posts[postIndex], postIndex);
        return SizedBox.shrink();
      }, childCount: _posts.length + (_posts.length ~/ 5) + (_hasMore? 1 : 0))),
      const SliverToBoxAdapter(child: SizedBox(height: 80)),
    ])));
  }
  Widget _buildPostCard(PostModel post, int idx) {
    List<MapEntry<String, int>> sorted = [MapEntry('like', post.likeCount), MapEntry('confuse', post.confuseCount), MapEntry('wrong', post.wrongCount), MapEntry('imp', post.impCount), MapEntry('explain', post.explainCount)];
    sorted.sort((a, b) => b.value.compareTo(a.value)); var top3 = sorted.where((e) => e.value > 0).take(3).toList();
    bool isSaved = post.isSaved || _savedIds.contains(post.id);
    return Container(margin: const EdgeInsets.only(bottom: 10), color: Colors.white, child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Padding(padding: const EdgeInsets.fromLTRB(12, 12, 8, 8), child: Row(children: [
        GestureDetector(onTap: ()=> _goToProfile(post.user.username), child: CircleAvatar(radius: 20, backgroundColor: Colors.grey[300], backgroundImage: post.user.profilePicture!= null && post.user.profilePicture!.isNotEmpty? CachedNetworkImageProvider(post.user.profilePicture!) : null)),
        const SizedBox(width: 10),
        Expanded(child: GestureDetector(onTap: ()=> _goToProfile(post.user.username), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(post.user.username, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15)), Text(timeago.format(post.createdAt), style: TextStyle(fontSize: 12, color: Colors.grey[600]))]))),
        IconButton(icon: Icon(isSaved? Icons.bookmark : Icons.bookmark_border, color: isSaved? Color(0xFF030F27) : Colors.grey), onPressed: () => _toggleSave(post))
      ])),
      if (post.content!= null && post.content!.isNotEmpty) Padding(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6), child: Text(post.content!, style: const TextStyle(fontSize: 15, height: 1.3))),
      if (post.hashtags.isNotEmpty) Padding(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 2), child: Wrap(spacing: 6, children: post.hashtags.map((t) => Text('#$t', style: const TextStyle(color: Color(0xFF1877F2), fontWeight: FontWeight.w500))).toList())),
      const SizedBox(height: 6),
      if (post.media.isNotEmpty) _MediaCarousel(mediaList: post.media, postId: post.id, postIndex: idx),
      if (post.likesCount > 0 || post.commentsCount > 0) Padding(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10), child: Row(children: [if (top3.isNotEmpty) Row(children: top3.map((e) => Padding(padding: const EdgeInsets.only(right: 3), child: Text(_emojiMap[e.key]?? '', style: const TextStyle(fontSize: 16)))).toList()), const SizedBox(width: 6), if (post.likesCount > 0) Text('${post.likesCount}', style: TextStyle(fontSize: 14, color: Colors.grey[700])), const Spacer(), if (post.commentsCount > 0) Text('${post.commentsCount} comments', style: TextStyle(fontSize: 13, color: Colors.grey[600]))])),
      const Divider(height: 1, thickness: 0.5),
      Padding(padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2), child: Row(children: [
        Expanded(child: _PostReactionButton(post: post, emojiMap: _emojiMap, emojiColor: _emojiColor, onReaction: (r) => _handleReaction(post, r))),
        Expanded(child: InkWell(onTap: () => _openCommentSheet(post), child: Container(padding: const EdgeInsets.symmetric(vertical: 12), child: const Row(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.chat_bubble_outline, size: 20, color: Color(0xFF65676B)), SizedBox(width: 6), Text('Comment', style: TextStyle(color: Color(0xFF65676B), fontWeight: FontWeight.w600))])))),
        Expanded(child: InkWell(onTap: () => _sharePost(post), child: Container(padding: const EdgeInsets.symmetric(vertical: 12), child: const Row(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.share_outlined, size: 20, color: Color(0xFF65676B)), SizedBox(width: 6), Text('Share', style: TextStyle(color: Color(0xFF65676B), fontWeight: FontWeight.w600))])))),
      ])),
    ]));
  }
  @override Widget build(BuildContext context) { return Scaffold(backgroundColor: const Color(0xFFF0F2F5), body: IndexedStack(index: _selectedIndex, children: [_homePage(), const SearchScreen(), ProfileScreen(onBackToHome: () => setState(() => _selectedIndex = 0))]), bottomNavigationBar: BottomNavigationBar(currentIndex: _selectedIndex, onTap: _onItemTapped, backgroundColor: const Color(0xFF030F27), selectedItemColor: Colors.white, unselectedItemColor: Colors.white60, type: BottomNavigationBarType.fixed, items: const [BottomNavigationBarItem(icon: Icon(Icons.home_outlined), activeIcon: Icon(Icons.home), label: "Home"), BottomNavigationBarItem(icon: Icon(Icons.search), label: "Search"), BottomNavigationBarItem(icon: Icon(Icons.person_outline), activeIcon: Icon(Icons.person), label: "Profile")])); }
}

//... _PostReactionButton, _MediaCarousel, _FullScreenViewer same as before (no change)...
class _PostReactionButton extends StatefulWidget {
  final PostModel post; final Map<String, String> emojiMap; final Map<String, Color> emojiColor; final Function(String) onReaction;
  const _PostReactionButton({required this.post, required this.emojiMap, required this.emojiColor, required this.onReaction});
  @override State<_PostReactionButton> createState() => _PostReactionButtonState();
}
class _PostReactionButtonState extends State<_PostReactionButton> {
  OverlayEntry? _overlayEntry;
  void _showOverlay(BuildContext context) {
    final RenderBox box = context.findRenderObject() as RenderBox; final Offset pos = box.localToGlobal(Offset.zero);
    _overlayEntry = OverlayEntry(builder: (c) => Stack(children: [
      GestureDetector(onTap: _hideOverlay, child: Container(color: Colors.transparent, width: double.infinity, height: double.infinity)),
      Positioned(left: 10, top: pos.dy - 65, child: Material(color: Colors.transparent, child: Container(padding: EdgeInsets.symmetric(horizontal: 8, vertical: 8), decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(30), boxShadow: [BoxShadow(color: Colors.black26, blurRadius: 12)]), child: Row(children: widget.emojiMap.entries.map((e){ bool sel = widget.post.myReaction==e.key; return GestureDetector(onTap: (){ _hideOverlay(); widget.onReaction(e.key); }, child: Container(margin: EdgeInsets.symmetric(horizontal: 4), padding: EdgeInsets.all(10), decoration: BoxDecoration(color: sel? widget.emojiColor[e.key]!.withOpacity(0.18):Colors.grey.shade100, shape: BoxShape.circle, border: sel? Border.all(color: widget.emojiColor[e.key]!, width: 2):null), child: Text(e.value, style: TextStyle(fontSize: 26)))); }).toList()),),),),
    ])); Overlay.of(context).insert(_overlayEntry!);
  }
  void _hideOverlay(){ _overlayEntry?.remove(); _overlayEntry=null; }
  @override Widget build(BuildContext context) {
    return Builder(builder: (btnCtx){
      return InkWell(onTap: () => widget.onReaction('like'), onLongPress: () => _showOverlay(btnCtx), child: Container(padding: const EdgeInsets.symmetric(vertical: 12), child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          if (widget.post.myReaction == null)...[const Icon(Icons.thumb_up_alt_outlined, size: 20, color: Color(0xFF65676B)), const SizedBox(width: 6), const Text('Like', style: TextStyle(color: Color(0xFF65676B), fontWeight: FontWeight.w600))]
          else...[Text(widget.emojiMap[widget.post.myReaction]?? '👍', style: const TextStyle(fontSize: 18)), const SizedBox(width: 6), Text(widget.post.myReaction!.toUpperCase(), style: TextStyle(color: widget.emojiColor[widget.post.myReaction], fontWeight: FontWeight.bold, fontSize: 12))]
        ])),);
    });
  }
}
class _MediaCarousel extends StatefulWidget { final List<PostMediaModel> mediaList; final String postId; final int postIndex; const _MediaCarousel({required this.mediaList, required this.postId, required this.postIndex}); @override State<_MediaCarousel> createState() => _MediaCarouselState(); }
class _MediaCarouselState extends State<_MediaCarousel> {
  late PageController _pageController; int _currentPage = 0; bool _showDots = true; final Map<int, VideoPlayerController> _videoControllers = {};
  @override void initState() { super.initState(); _pageController = PageController(); _initVideos(); if (widget.mediaList.length > 1) Future.delayed(const Duration(seconds: 4), () { if (mounted) setState(() => _showDots = false); }); }
  void _initVideos() { for (int i = 0; i < widget.mediaList.length; i++) { if (widget.mediaList[i].mediaType == 'video') { final c = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file)); _videoControllers[i] = c; c.initialize().then((_) { if (mounted) { setState(() {}); if (i==0) { c.setLooping(true); c.setVolume(0); c.play(); } } }); } } }
  @override void dispose() { _pageController.dispose(); for (var c in _videoControllers.values) { c.dispose(); } super.dispose(); }
  void _handleVisibility(bool v, int i) { final c = _videoControllers[i]; if (c!= null && c.value.isInitialized) { if (v) { c.setLooping(true); c.play(); } else { c.pause(); } } }
  Future<void> _openFile(String url, String fileName) async { try { String? token = await AuthService.getToken(); Directory dir = await getTemporaryDirectory(); String savePath = '${dir.path}/${fileName.replaceAll(' ', '_')}'; if (!await File(savePath).exists()) { await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty? {"Authorization": "Bearer $token"} : {})); } await OpenFilex.open(savePath); } catch (e) { if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Open failed: $e'))); } }
  @override Widget build(BuildContext context) { final maxHeight = MediaQuery.of(context).size.height * 0.55; return SizedBox(height: maxHeight, child: Stack(alignment: Alignment.center, children: [PageView.builder(controller: _pageController, itemCount: widget.mediaList.length, onPageChanged: (i) { _videoControllers[_currentPage]?.pause(); setState(() { _currentPage = i; _showDots = true; }); _videoControllers[i]?.setLooping(true); _videoControllers[i]?.play(); Future.delayed(const Duration(seconds: 4), () { if (mounted) setState(() => _showDots = false); }); }, itemBuilder: (c, i) { final m = widget.mediaList[i]; return VisibilityDetector(key: Key('${widget.postId}_$i'), onVisibilityChanged: (info) => _handleVisibility(info.visibleFraction > 0.5, i), child: _buildMediaItem(m, i, maxHeight)); }), if (widget.mediaList.length > 1 && _showDots) Positioned(bottom: 10, child: Container(padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5), decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(20)), child: Row(children: List.generate(widget.mediaList.length, (i) => Container(margin: const EdgeInsets.symmetric(horizontal: 3), width: _currentPage == i? 18 : 7, height: 7, decoration: BoxDecoration(borderRadius: BorderRadius.circular(10), color: _currentPage == i? Colors.white : Colors.white54)))))) ])); }
  Widget _buildMediaItem(PostMediaModel media, int index, double maxHeight) { if (media.mediaType == 'video') { final c = _videoControllers[index]; if (c == null ||!c.value.isInitialized) return Container(height: maxHeight, color: Colors.black, child: const Center(child: CircularProgressIndicator(color: Colors.white))); return GestureDetector(onTap: () => _openFullScreen(context, index), child: Container(color: Colors.black, child: Center(child: AspectRatio(aspectRatio: c.value.aspectRatio, child: VideoPlayer(c))))); } else if (media.mediaType == 'image') { return GestureDetector(onTap: () => _openFullScreen(context, index), child: CachedNetworkImage(imageUrl: media.file, fit: BoxFit.cover, width: double.infinity, height: maxHeight)); } else if (media.file.toLowerCase().endsWith('.pdf')) { return Stack(children: [SfPdfViewer.network(media.file), Positioned(bottom: 10, right: 10, child: ElevatedButton.icon(onPressed: () => _openFile(media.file, media.fileName.isNotEmpty? media.fileName : 'doc.pdf'), icon: const Icon(Icons.open_in_new), label: const Text('Open'), style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF030F27), foregroundColor: Colors.white)))]); } else { return Container(color: const Color(0xFFF0F2F5), child: Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [const Icon(Icons.insert_drive_file, size: 60, color: Colors.grey), const SizedBox(height: 10), Text(media.fileName, style: const TextStyle(fontWeight: FontWeight.bold)), const SizedBox(height: 15), ElevatedButton.icon(onPressed: () => _openFile(media.file, media.fileName), icon: const Icon(Icons.open_in_new), label: const Text('Open'), style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF030F27), foregroundColor: Colors.white)) ]))) ; } }
  void _openFullScreen(BuildContext context, int initialIndex) { _videoControllers.values.forEach((c) => c.pause()); Navigator.push(context, MaterialPageRoute(builder: (_) => _FullScreenViewer(mediaList: widget.mediaList, initialIndex: initialIndex))).then((_) { _videoControllers[_currentPage]?.setLooping(true); _videoControllers[_currentPage]?.play(); }); }
}
class _FullScreenViewer extends StatefulWidget { final List<PostMediaModel> mediaList; final int initialIndex; const _FullScreenViewer({required this.mediaList, required this.initialIndex}); @override State<_FullScreenViewer> createState() => _FullScreenViewerState(); }
class _FullScreenViewerState extends State<_FullScreenViewer> {
  late PageController _controller; late int _currentIndex; final Map<int, VideoPlayerController> _videoControllers = {};
  @override void initState() { super.initState(); _currentIndex = widget.initialIndex; _controller = PageController(initialPage: widget.initialIndex); for (int i = 0; i < widget.mediaList.length; i++) { if (widget.mediaList[i].mediaType == 'video') { final c = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file)); _videoControllers[i] = c; c.initialize().then((_) { if (mounted) { setState(() {}); if (i == _currentIndex) { c.setLooping(true); c.play(); } } }); } } }
  @override void dispose() { for (var c in _videoControllers.values) { c.dispose(); } _controller.dispose(); super.dispose(); }
  Future<void> _openFile(String url, String name) async { String? token = await AuthService.getToken(); Directory dir = await getTemporaryDirectory(); String savePath = '${dir.path}/${name.replaceAll(' ', '_')}'; if (!await File(savePath).exists()) await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty? {"Authorization": "Bearer $token"} : {})); await OpenFilex.open(savePath); }
  @override Widget build(BuildContext context) { return Scaffold(backgroundColor: Colors.black, appBar: AppBar(backgroundColor: Colors.black, iconTheme: const IconThemeData(color: Colors.white), title: Text('${_currentIndex + 1}/${widget.mediaList.length}', style: const TextStyle(color: Colors.white)), actions: [IconButton(icon: const Icon(Icons.open_in_new, color: Colors.white), onPressed: () => _openFile(widget.mediaList[_currentIndex].file, widget.mediaList[_currentIndex].fileName.isNotEmpty? widget.mediaList[_currentIndex].fileName : 'file_$_currentIndex'))]), body: PageView.builder(controller: _controller, itemCount: widget.mediaList.length, onPageChanged: (i) { _videoControllers[_currentIndex]?.pause(); setState(() => _currentIndex = i); _videoControllers[i]?.setLooping(true); _videoControllers[i]?.play(); }, itemBuilder: (c, i) { final m = widget.mediaList[i]; if (m.mediaType == 'video') { final con = _videoControllers[i]; return con!= null && con.value.isInitialized? Center(child: AspectRatio(aspectRatio: con.value.aspectRatio, child: VideoPlayer(con))) : const Center(child: CircularProgressIndicator(color: Colors.white)); } else if (m.file.toLowerCase().endsWith('.pdf')) { return SfPdfViewer.network(m.file); } else if (m.mediaType == 'image') { return InteractiveViewer(minScale: 0.5, maxScale: 4.0, child: CachedNetworkImage(imageUrl: m.file, fit: BoxFit.contain)); } else { return Center(child: Text(m.fileName, style: const TextStyle(color: Colors.white))); } })); }
}

// ===================== COMMENT SYSTEM - THREAD SERIES LOGIC =====================
class CommentBottomSheet extends StatefulWidget { final PostModel post; final VoidCallback onCommentAdded; final Function(String) onGoToProfile; const CommentBottomSheet({super.key, required this.post, required this.onCommentAdded, required this.onGoToProfile}); @override State<CommentBottomSheet> createState() => _CommentBottomSheetState(); }
class _CommentBottomSheetState extends State<CommentBottomSheet> {
  List<CommentModel> comments = []; bool loading = true; bool isUploading = false; double uploadProgress = 0; final TextEditingController _controller = TextEditingController(); List<File> _selectedFiles = []; String? replyToId; String? replyToName; final ImagePicker _picker = ImagePicker();
  final Map<String, List<CommentModel>> _localReplies = {}; final Map<String, bool> _expandedMap = {};
  @override void initState() { super.initState(); _fetchComments(); }
  Future<void> _fetchComments() async { try { final data = await CommentService.getComments(widget.post.id); if (mounted) setState(() { comments = data; loading = false; }); } catch (e) { if (mounted) setState(() => loading = false); } }
  bool _checkFileSize(File file) { double sizeMB = file.lengthSync() / (1024*1024); if (sizeMB > 200) { showDialog(context: context, builder: (_) => AlertDialog(title: Row(children: [Icon(Icons.error, color: Colors.red), SizedBox(width: 8), Text("File Too Large")]), content: Text("This file is ${sizeMB.toStringAsFixed(1)}MB, which is much more than 200MB limit.\n\nPlease select a file under 200MB."), actions: [TextButton(onPressed: ()=> Navigator.pop(context), child: Text("OK", style: TextStyle(color: Color(0xFF030F27), fontWeight: FontWeight.bold)))],)); ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Video is much more than 200MB (${sizeMB.toStringAsFixed(1)}MB) - Not allowed"), backgroundColor: Colors.red)); return false; } return true; }
  Future<void> _pickCameraPhoto() async { try { final XFile? photo = await _picker.pickImage(source: ImageSource.camera, imageQuality: 85); if (photo!= null && mounted) { File f = File(photo.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
  Future<void> _pickCameraVideo() async { try { final XFile? v = await _picker.pickVideo(source: ImageSource.camera, maxDuration: Duration(minutes: 5)); if (v!= null && mounted) { File f = File(v.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
  Future<void> _pickGalleryImage() async { try { final XFile? img = await _picker.pickImage(source: ImageSource.gallery, imageQuality: 85); if (img!= null && mounted) { File f = File(img.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
  Future<void> _pickGalleryVideo() async { try { final XFile? v = await _picker.pickVideo(source: ImageSource.gallery); if (v!= null && mounted) { File f = File(v.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
  Future<void> _pickFile() async { try { const XTypeGroup all = XTypeGroup(label: 'all'); final XFile? f = await openFile(acceptedTypeGroups: [all]); if (f!= null && mounted) { File file = File(f.path); if (_checkFileSize(file)) setState(() => _selectedFiles.add(file)); } } catch (e) {} }
  Future<void> _openOptions() async { showModalBottomSheet(context: context, backgroundColor: Colors.white, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))), builder: (c) => SafeArea(child: Wrap(children: [ListTile(leading: const Icon(Icons.camera_alt, color: Colors.blue), title: const Text("Camera Photo"), onTap: () { Navigator.pop(c); _pickCameraPhoto(); }), ListTile(leading: const Icon(Icons.videocam, color: Colors.red), title: const Text("Camera Video"), onTap: () { Navigator.pop(c); _pickCameraVideo(); }), ListTile(leading: const Icon(Icons.photo_library, color: Colors.green), title: const Text("Gallery Photo"), onTap: () { Navigator.pop(c); _pickGalleryImage(); }), ListTile(leading: const Icon(Icons.video_library, color: Colors.purple), title: const Text("Gallery Video"), onTap: () { Navigator.pop(c); _pickGalleryVideo(); }), ListTile(leading: const Icon(Icons.attach_file, color: Colors.orange), title: const Text("Document"), onTap: () { Navigator.pop(c); _pickFile(); }),])),); }

  Future<void> _send() async {
    if (_controller.text.trim().isEmpty && _selectedFiles.isEmpty) return;
    await WakelockPlus.enable(); setState(() { isUploading = true; uploadProgress = 0; }); String? curId = replyToId;
    try {
      final newComment = await CommentService.createComment(postId: widget.post.id, parentId: curId, content: _controller.text.trim(), files: _selectedFiles, onProgress: (p) { if (mounted) setState(() => uploadProgress = p); });
      if (mounted) { setState(() { if (curId == null) { comments.insert(0, newComment); } else { _localReplies.putIfAbsent(curId, () => []); _localReplies[curId]!.insert(0, newComment); _expandedMap[curId] = true; for (var c in comments) { if (c.id == curId) c.repliesCount++; } } _controller.clear(); _selectedFiles = []; replyToId = null; replyToName = null; isUploading = false; uploadProgress = 0; }); widget.onCommentAdded(); }
    } catch (e) { if (mounted) { setState(() => isUploading = false); ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed: $e'), backgroundColor: Colors.red)); } } finally { await WakelockPlus.disable(); }
  }
  @override Widget build(BuildContext context) {
    return SafeArea(child: DraggableScrollableSheet(initialChildSize: 0.85, maxChildSize: 0.95, minChildSize: 0.5, expand: false, builder: (context, scrollController) {
      return Column(children: [
        const SizedBox(height: 10), Container(width: 40, height: 4, decoration: BoxDecoration(color: Colors.grey[300], borderRadius: BorderRadius.circular(10))), const SizedBox(height: 10),
        Text("Comments ${widget.post.commentsCount}", style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)), const Divider(),
        if (isUploading) Padding(padding: const EdgeInsets.all(8), child: Column(children: [LinearProgressIndicator(value: uploadProgress>0? uploadProgress/100 : null, color: const Color(0xFF030F27)), const SizedBox(height: 4), Text(uploadProgress>0? '${uploadProgress.toStringAsFixed(1)}% uploading...' : 'Compressing...', style: const TextStyle(fontSize: 12))])),
        Expanded(child: loading? const Center(child: CircularProgressIndicator()) : comments.isEmpty? const Center(child: Text("No comments yet")) : ListView.builder(controller: scrollController, itemCount: comments.length, itemBuilder: (c, i) => CommentTile(key: ValueKey(comments[i].id + comments[i].repliesCount.toString() + (_localReplies[comments[i].id]?.length??0).toString() + (_expandedMap[comments[i].id]?.toString()??'')), comment: comments[i], postOwnerId: widget.post.user.id.toString(), level: 0, localReplies: _localReplies[comments[i].id]??[], allLocalReplies: _localReplies, expandedMap: _expandedMap, onReply: (id, name) => setState(() { replyToId = id; replyToName = name; }), onDeleted: (id) => setState(() => comments.removeWhere((x) => x.id == id)), onEdited: (updated) => setState(() { int idx = comments.indexWhere((x) => x.id == updated.id); if (idx!= -1) comments[idx] = updated; }), onHidden: (id) => setState(() => comments.removeWhere((x) => x.id == id)), onGoToProfile: widget.onGoToProfile))),
        if (replyToName!= null) Container(width: double.infinity, color: const Color(0xFF030F27), padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10), child: Row(children: [const Icon(Icons.reply, color: Colors.white70, size: 16), const SizedBox(width: 8), Expanded(child: Text("Replying to @$replyToName", style: TextStyle(fontSize: 13, fontWeight: FontWeight.bold, color: Colors.white))), GestureDetector(onTap: () => setState(() { replyToId = null; replyToName = null; }), child: Icon(Icons.close, color: Colors.white, size: 18)) ])),
        if (_selectedFiles.isNotEmpty) Container(height: 90, color: Colors.grey[100], child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Padding(padding: const EdgeInsets.all(6), child: Text('${_selectedFiles.length} selected', style: TextStyle(fontSize: 12, color: Colors.green, fontWeight: FontWeight.bold))), Expanded(child: ListView.builder(scrollDirection: Axis.horizontal, itemCount: _selectedFiles.length, itemBuilder: (c,i){ bool isVideo = _selectedFiles[i].path.toLowerCase().endsWith('.mp4') || _selectedFiles[i].path.toLowerCase().endsWith('.mov'); return Stack(children: [Container(margin: const EdgeInsets.all(6), width: 70, height: 70, decoration: BoxDecoration(borderRadius: BorderRadius.circular(10), color: Colors.black), child: ClipRRect(borderRadius: BorderRadius.circular(10), child: isVideo? const Icon(Icons.videocam, color: Colors.white) : Image.file(_selectedFiles[i], fit: BoxFit.cover))), Positioned(top: 0, right: 0, child: GestureDetector(onTap: ()=>setState(()=>_selectedFiles.removeAt(i)), child: Container(decoration: const BoxDecoration(color: Colors.red, shape: BoxShape.circle), child: const Icon(Icons.close, size: 18, color: Colors.white))))]); })), ])),
        Padding(padding: EdgeInsets.only(left: 10, right: 10, bottom: MediaQuery.of(context).viewPadding.bottom + 10, top: 8), child: Row(children: [IconButton(icon: const Icon(Icons.attach_file, color: Color(0xFF030F27)), onPressed: isUploading? null : _openOptions), Expanded(child: TextField(controller: _controller, enabled:!isUploading, decoration: const InputDecoration(hintText: "Add comment...", border: OutlineInputBorder(borderRadius: BorderRadius.all(Radius.circular(25))), contentPadding: EdgeInsets.symmetric(horizontal: 16, vertical: 10)))), const SizedBox(width: 8), isUploading? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)) : CircleAvatar(backgroundColor: const Color(0xFF030F27), child: IconButton(icon: const Icon(Icons.send, color: Colors.white), onPressed: _send))]))
      ]);
    }));
  }
}

class CommentTile extends StatefulWidget {
  final CommentModel comment; final String postOwnerId; final Function(String, String) onReply; final Function(String)? onDeleted; final Function(CommentModel)? onEdited; final Function(String)? onHidden; final List<CommentModel> localReplies; final Map<String, List<CommentModel>> allLocalReplies; final Map<String, bool> expandedMap; final int level; final Function(String) onGoToProfile;
  const CommentTile({super.key, required this.comment, required this.postOwnerId, required this.onReply, this.onDeleted, this.onEdited, this.onHidden, this.localReplies = const [], this.allLocalReplies = const {}, this.expandedMap = const {}, this.level = 0, required this.onGoToProfile});
  @override State<CommentTile> createState() => _CommentTileState();
}
class _CommentTileState extends State<CommentTile> {
  List<CommentModel> replies = []; bool showReplies = false; bool loadingReplies = false; String? _myUserId;
  final Map<String, String> _emojiMap = {'like': '👍','confuse': '🤔','wrong': '❗','imp': '⭐','explain': '💡'};
  final Map<String, Color> _emojiColor = {'like': Color(0xFF1877F2),'confuse': Color(0xFFF7B928),'wrong': Color(0xFFE0245E),'imp': Color(0xFFFFAD33),'explain': Color(0xFF45BD62)};
  OverlayEntry? _overlayEntry;
  @override void initState() { super.initState(); _getMyId(); if (widget.localReplies.isNotEmpty) { replies = widget.localReplies; showReplies = true; } if(widget.expandedMap[widget.comment.id]==true){ showReplies = true; if(replies.isEmpty) _loadReplies(); } }
  @override void didUpdateWidget(covariant CommentTile oldWidget) { super.didUpdateWidget(oldWidget); if (widget.localReplies.length!= oldWidget.localReplies.length || widget.expandedMap[widget.comment.id]==true) { setState(() { final ids = replies.map((e)=>e.id).toSet(); for(var r in widget.localReplies){ if(!ids.contains(r.id)) replies.insert(0, r); } if(widget.localReplies.isNotEmpty) showReplies = true; }); } }
  Future<void> _getMyId() async { final id = await AuthService.getUserId(); if(mounted) setState(()=> _myUserId = id); }
  bool get _isMyComment => _myUserId!=null && widget.comment.user.id.toString() == _myUserId.toString();
  bool get _isPostOwner => _myUserId!=null && widget.postOwnerId == _myUserId.toString();
  bool get _canShowMenu => _isMyComment || _isPostOwner;
  void _showReactionOverlay(BuildContext context) { final RenderBox box = context.findRenderObject() as RenderBox; final Offset pos = box.localToGlobal(Offset.zero); _overlayEntry = OverlayEntry(builder: (c) => Stack(children: [GestureDetector(onTap: ()=> _hideOverlay(), child: Container(color: Colors.transparent, width: double.infinity, height: double.infinity)), Positioned(left: 20, top: pos.dy - 60, child: Material(color: Colors.transparent, child: Container(padding: EdgeInsets.symmetric(horizontal: 8, vertical: 6), decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(30), boxShadow: [BoxShadow(color: Colors.black26, blurRadius: 10)]), child: Row(children: _emojiMap.entries.map((e){ bool sel = widget.comment.myReaction==e.key; return GestureDetector(onTap: (){ _hideOverlay(); _handleReaction(e.key); }, child: Container(margin: EdgeInsets.symmetric(horizontal: 4), padding: EdgeInsets.all(8), decoration: BoxDecoration(color: sel? _emojiColor[e.key]!.withOpacity(0.15):Colors.grey.shade100, shape: BoxShape.circle, border: sel? Border.all(color: _emojiColor[e.key]!, width: 2):null), child: Text(e.value, style: TextStyle(fontSize: 26)))); }).toList()),),),),])); Overlay.of(context).insert(_overlayEntry!); }
  void _hideOverlay(){ _overlayEntry?.remove(); _overlayEntry=null; }

  // 🔥 THREAD LOGIC: All replies in one series
  Future<void> _loadReplies() async {
    setState(()=> loadingReplies = true);
    try{
      final data = await CommentService.getReplies(widget.comment.id);
      final merged = [...widget.localReplies,...data];
      final ids = <String>{};
      final unique = merged.where((e)=> ids.add(e.id)).toList();
      setState((){ replies = unique; showReplies = true; loadingReplies = false; });
    }catch(e){ setState(()=> loadingReplies = false); }
  }

  Future<void> _handleReaction(String reaction) async { final old = widget.comment.myReaction; final oldCount = widget.comment.likesCount; setState((){ if(old==reaction){ widget.comment.myReaction=null; if(widget.comment.likesCount>0) widget.comment.likesCount--; } else{ if(old==null) widget.comment.likesCount++; widget.comment.myReaction=reaction; } }); try{ final res = await CommentService.toggleCommentReaction(widget.comment.id, reaction); if(mounted) setState((){ widget.comment.myReaction = res['my_reaction']??res['myReaction']; var counts = res['counts']??res['reaction_counts']; if(counts!=null){ widget.comment.reactionCounts = Map<String,int>.from(counts.map((k,v)=>MapEntry(k.toString(),(v as int?)??0))); widget.comment.likesCount = counts['total']?? oldCount; } }); }catch(e){ if(mounted) setState((){ widget.comment.myReaction=old; widget.comment.likesCount=oldCount; }); } }
  void _showOptions(){ List<Widget> options = []; if(_isMyComment){ options.add(ListTile(leading: Icon(Icons.edit, color: Colors.blue), title: Text("Edit"), onTap: (){ Navigator.pop(context); _editDialog(); })); options.add(ListTile(leading: Icon(Icons.delete, color: Colors.red), title: Text("Delete"), onTap: (){ Navigator.pop(context); _deleteConfirm(); })); } if(_isPostOwner &&!_isMyComment){ options.add(ListTile(leading: Icon(Icons.visibility_off, color: Colors.orange), title: Text("Hide Comment"), onTap: (){ Navigator.pop(context); _hideComment(); })); } if(options.isEmpty) return; showModalBottomSheet(context: context, backgroundColor: Colors.white, shape: RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(12))), builder: (c)=> SafeArea(child: Wrap(children: options))); }
  void _editDialog(){ TextEditingController ctrl = TextEditingController(text: widget.comment.content); showDialog(context: context, builder: (_)=> AlertDialog(title: Text("Edit Comment"), content: TextField(controller: ctrl, maxLines: 4, autofocus: true, decoration: InputDecoration(border: OutlineInputBorder(), hintText: "Edit comment")), actions: [TextButton(onPressed: ()=> Navigator.pop(context), child: Text("Cancel")), ElevatedButton(style: ElevatedButton.styleFrom(backgroundColor: Color(0xFF030F27)), onPressed: () async { if(ctrl.text.trim().isEmpty) return; Navigator.pop(context); try{ final updated = await CommentService.editComment(commentId: widget.comment.id, content: ctrl.text.trim()); if(mounted) setState(()=> widget.comment.content = updated.content); widget.comment.isEdited = true; if(widget.onEdited!=null) widget.onEdited!(updated); if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Edited"), backgroundColor: Colors.green)); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Edit failed: $e"), backgroundColor: Colors.red)); } }, child: Text("Save", style: TextStyle(color: Colors.white))) ])); }
  void _deleteConfirm(){ showDialog(context: context, builder: (_)=> AlertDialog(title: Text("Delete?"), content: Text("Delete this comment?"), actions: [TextButton(onPressed: ()=> Navigator.pop(context), child: Text("Cancel")), ElevatedButton(style: ElevatedButton.styleFrom(backgroundColor: Colors.red), onPressed: () async { Navigator.pop(context); try{ await CommentService.deleteComment(widget.comment.id); if(widget.onDeleted!=null) widget.onDeleted!(widget.comment.id); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Delete failed: $e"))); } }, child: Text("Delete", style: TextStyle(color: Colors.white)))])); }
  void _hideComment() async { try{ await CommentService.hideComment(widget.comment.id); if(widget.onHidden!=null) widget.onHidden!(widget.comment.id); if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Comment hidden"), backgroundColor: Colors.orange)); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Hide failed: $e"))); } }
  bool _isImage(String url){ final l=url.toLowerCase(); return l.endsWith('.png')||l.endsWith('.jpg')||l.endsWith('.jpeg')||l.endsWith('.webp')||l.endsWith('.gif'); }
  bool _isVideo(String url,String type){ final l=url.toLowerCase(); return type=='video'||l.endsWith('.mp4')||l.endsWith('.mov')||l.endsWith('.mkv'); }
  Future<void> _openFile(String url,String fileName) async { try{ String? token = await AuthService.getToken(); Directory dir=await getTemporaryDirectory(); String savePath='${dir.path}/${fileName.replaceAll(' ', '_')}'; if(!await File(savePath).exists()){ await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty?{"Authorization":"Bearer $token"}:{})); } await OpenFilex.open(savePath); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Open failed: $e'))); } }
  void _openMedia(dynamic m){ if(_isImage(m.file)) Navigator.push(context, MaterialPageRoute(builder: (_)=> _CommentImageFullScreen(url: m.file, fileName: m.fileName))); else if(_isVideo(m.file,m.mediaType)) Navigator.push(context, MaterialPageRoute(builder: (_)=> _CommentVideoFullScreen(url: m.file, fileName: m.fileName))); else _openFile(m.file,m.fileName); }
  Widget _buildCommentVideoThumb(){ return Container(height: 90, width: 130, decoration: BoxDecoration(color: Colors.black, borderRadius: BorderRadius.circular(8)), child: Stack(alignment: Alignment.center, children: [Icon(Icons.videocam, color: Colors.white30, size: 30), Container(padding: EdgeInsets.all(4), decoration: BoxDecoration(color: Colors.black54, shape: BoxShape.circle), child: Icon(Icons.play_arrow, color: Colors.white, size: 24))])); }

  @override Widget build(BuildContext context) {
    List<MapEntry<String,int>> sorted = widget.comment.reactionCounts.entries.where((e)=> e.value>0 && e.key!='total').toList(); sorted.sort((a,b)=> b.value.compareTo(a.value)); var top3 = sorted.take(3).toList();
    // 🔥 FIX: level 0 = main comment, level 1+ = sab same indent me - ek hi series
    double leftPad = widget.level==0? 12 : 36;
    return Padding(padding: EdgeInsets.only(left: leftPad, right: 12, top: 8, bottom: 8), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      // Thread Line
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        GestureDetector(onTap: ()=> widget.onGoToProfile(widget.comment.user.username), child: CircleAvatar(radius: widget.level==0? 18 : 14, backgroundImage: widget.comment.user.profilePicture!=null && widget.comment.user.profilePicture!.isNotEmpty? CachedNetworkImageProvider(widget.comment.user.profilePicture!) : null)),
        const SizedBox(width: 10),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          GestureDetector(onLongPress: _canShowMenu? ()=> _showOptions() : null, child: Container(padding: EdgeInsets.symmetric(horizontal: 14, vertical: 10), decoration: BoxDecoration(color: Colors.grey.shade100, borderRadius: BorderRadius.circular(16)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [GestureDetector(onTap: ()=> widget.onGoToProfile(widget.comment.user.username), child: Text(widget.comment.user.username, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: Color(0xFF030F27)))), if(widget.comment.isEdited) Padding(padding: EdgeInsets.only(left: 6), child: Text("(edited)", style: TextStyle(fontSize: 10, color: Colors.grey))), if(_isMyComment) Padding(padding: EdgeInsets.only(left: 6), child: Text("(you)", style: TextStyle(fontSize: 10, color: Colors.blue, fontWeight: FontWeight.bold)))]),
            if(widget.comment.content.isNotEmpty) Padding(padding: EdgeInsets.only(top: 6), child: Text(widget.comment.content, style: TextStyle(fontSize: 15, height: 1.4))),
            if(widget.comment.media.isNotEmpty) Padding(padding: EdgeInsets.only(top: 8), child: Wrap(spacing: 6, runSpacing: 6, children: widget.comment.media.map((m){ if(_isImage(m.file)){ return GestureDetector(onTap: ()=> _openMedia(m), child: ClipRRect(borderRadius: BorderRadius.circular(8), child: CachedNetworkImage(imageUrl: m.file, height: 90, width: 90, fit: BoxFit.cover))); }else if(_isVideo(m.file,m.mediaType)){ return GestureDetector(onTap: ()=> _openMedia(m), child: _buildCommentVideoThumb()); }else{ return InkWell(onTap: ()=> _openFile(m.file,m.fileName), child: Container(padding: EdgeInsets.symmetric(horizontal: 10, vertical: 8), decoration: BoxDecoration(color: Colors.white, borderRadius: BorderRadius.circular(8), border: Border.all(color: Colors.grey.shade300)), child: Row(mainAxisSize: MainAxisSize.min, children: [Icon(Icons.description, size: 16), SizedBox(width: 6), SizedBox(width: 70, child: Text(m.fileName, style: TextStyle(fontSize: 12), overflow: TextOverflow.ellipsis))]))); } }).toList())),
            if(top3.isNotEmpty) Padding(padding: EdgeInsets.only(top: 6), child: Row(children: [Row(children: top3.map((e)=> Text(_emojiMap[e.key]??'', style: TextStyle(fontSize: 12))).toList()), SizedBox(width: 6), Text('${widget.comment.likesCount}', style: TextStyle(fontSize: 11, color: Colors.grey[600]))]))
          ]))),
          const SizedBox(height: 6),
          Row(children: [
            Text(timeago.format(widget.comment.createdAt), style: TextStyle(fontSize: 11, color: Colors.grey)),
            SizedBox(width: 14),
            Builder(builder: (likeCtx) {
              return GestureDetector(
                onTap: ()=> _handleReaction('like'),
                onLongPress: ()=> _showReactionOverlay(likeCtx),
                child: Container(padding: EdgeInsets.symmetric(horizontal: 10, vertical: 4), decoration: BoxDecoration(color: widget.comment.myReaction!=null? _emojiColor[widget.comment.myReaction]!.withOpacity(0.12) : Colors.transparent, borderRadius: BorderRadius.circular(20),), child: Row(children: [Icon(widget.comment.myReaction==null? Icons.thumb_up_alt_outlined : Icons.thumb_up_alt, size: 16, color: widget.comment.myReaction!=null? _emojiColor[widget.comment.myReaction] : Color(0xFF65676B)), SizedBox(width: 4), Text(widget.comment.myReaction!=null? widget.comment.myReaction!.toUpperCase() : 'Like', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: widget.comment.myReaction!=null? _emojiColor[widget.comment.myReaction] : Color(0xFF65676B))), if(widget.comment.myReaction!=null)...[SizedBox(width: 4), Text(_emojiMap[widget.comment.myReaction]??'', style: TextStyle(fontSize: 12))]]),),
              );
            }),
            SizedBox(width: 12),
            GestureDetector(onTap: ()=> widget.onReply(widget.comment.id, widget.comment.user.username), child: Row(children: [Icon(Icons.chat_bubble_outline, size: 14, color: Color(0xFF65676B)), SizedBox(width: 4), Text("Reply", style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: Color(0xFF65676B)))])),
            if(widget.comment.repliesCount>0 || widget.localReplies.isNotEmpty)...[SizedBox(width: 12), GestureDetector(onTap: (){ if(showReplies) setState(()=> showReplies=false); else _loadReplies(); }, child: Text(loadingReplies? "Loading..." : showReplies? "Hide ${widget.comment.repliesCount + widget.localReplies.length}" : "${widget.comment.repliesCount + widget.localReplies.length} replies", style: TextStyle(fontSize: 12, color: Colors.blue, fontWeight: FontWeight.w600)))],
          ]),
        ])),
        if(_canShowMenu) IconButton(icon: Icon(Icons.more_horiz, size: 18), onPressed: ()=> _showOptions(), padding: EdgeInsets.zero, constraints: BoxConstraints(), visualDensity: VisualDensity.compact)
      ]),
      // 🔥 SAB REPLY EK HI SERIES ME
      if(showReplies) Padding(
        padding: EdgeInsets.only(top: 8, left: widget.level==0? 12 : 0),
        child: Container(
          decoration: BoxDecoration(
            border: Border(left: BorderSide(color: Colors.grey.shade300, width: 2))
          ),
          child: Column(children: replies.map((r){
            List<CommentModel> nestedLocal = widget.allLocalReplies[r.id]??[];
            return CommentTile(
              comment: r,
              postOwnerId: widget.postOwnerId,
              level: 1, // 🔥 FIXED: sabka level 1 taki ek hi series me aaye
              localReplies: nestedLocal,
              allLocalReplies: widget.allLocalReplies,
              expandedMap: widget.expandedMap,
              onReply: widget.onReply,
              onDeleted: (id)=> setState(()=> replies.removeWhere((x)=> x.id==id)),
              onEdited: (updated)=> setState((){ int idx=replies.indexWhere((x)=> x.id==updated.id); if(idx!=-1) replies[idx]=updated; }),
              onHidden: (id)=> setState(()=> replies.removeWhere((x)=> x.id==id)),
              onGoToProfile: widget.onGoToProfile
            );
          }).toList())
        )
      ),
    ]));
  }
}

class _CommentImageFullScreen extends StatelessWidget { final String url; final String fileName; const _CommentImageFullScreen({required this.url, required this.fileName}); Future<void> _open(String url, String name, BuildContext context) async { String? token = await AuthService.getToken(); Directory dir = await getTemporaryDirectory(); String savePath = '${dir.path}/${name.replaceAll(' ', '_')}'; if (!await File(savePath).exists()) await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty? {"Authorization": "Bearer $token"} : {})); await OpenFilex.open(savePath); } @override Widget build(BuildContext context) { return Scaffold(backgroundColor: Colors.black, appBar: AppBar(backgroundColor: Colors.black, iconTheme: IconThemeData(color: Colors.white), title: Text(fileName, style: TextStyle(color: Colors.white, fontSize: 14)), actions: [IconButton(icon: Icon(Icons.open_in_new, color: Colors.white), onPressed: () => _open(url, fileName, context))]), body: SizedBox(width: double.infinity, height: double.infinity, child: InteractiveViewer(minScale: 0.5, maxScale: 6.0, child: Center(child: CachedNetworkImage(imageUrl: url, fit: BoxFit.contain))))); } }
class _CommentVideoFullScreen extends StatefulWidget { final String url; final String fileName; const _CommentVideoFullScreen({required this.url, required this.fileName}); @override State<_CommentVideoFullScreen> createState() => _CommentVideoFullScreenState(); }
class _CommentVideoFullScreenState extends State<_CommentVideoFullScreen> {
  late VideoPlayerController _controller; bool _initialized = false; bool _showControls = true; Timer? _hideTimer;
  @override void initState() { super.initState(); SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky); _controller = VideoPlayerController.networkUrl(Uri.parse(widget.url))..initialize().then((_) { if (mounted) { setState(() => _initialized = true); _controller.setLooping(true); _controller.play(); _startHideTimer(); } }); _controller.addListener(() { if (mounted) setState(() {}); }); }
  @override void dispose() { _hideTimer?.cancel(); _controller.dispose(); SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge); SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]); super.dispose(); }
  void _startHideTimer() { _hideTimer?.cancel(); _hideTimer = Timer(Duration(seconds: 5), () { if (mounted) setState(() => _showControls = false); }); }
  void _toggleControls() { setState(() => _showControls =!_showControls); if (_showControls) _startHideTimer(); }
  String _format(Duration d) => "${d.inMinutes}:${(d.inSeconds%60).toString().padLeft(2,'0')}";
  @override Widget build(BuildContext context) { return Scaffold(backgroundColor: Colors.black, body: GestureDetector(onTap: _toggleControls, child: Stack(children: [Center(child: _initialized? AspectRatio(aspectRatio: _controller.value.aspectRatio, child: VideoPlayer(_controller)) : CircularProgressIndicator(color: Colors.white)), if (_showControls) Positioned(top: 0, left: 0, right: 0, child: AppBar(backgroundColor: Colors.black54, iconTheme: IconThemeData(color: Colors.white), title: Text(widget.fileName, style: TextStyle(color: Colors.white, fontSize: 14)), actions: [IconButton(icon: Icon(Icons.close, color: Colors.white), onPressed: () => Navigator.pop(context))])), if (_showControls && _initialized) Positioned(bottom: 0, left: 0, right: 0, child: Container(padding: EdgeInsets.only(left: 10, right: 10, bottom: MediaQuery.of(context).padding.bottom + 20, top: 10), decoration: BoxDecoration(gradient: LinearGradient(begin: Alignment.bottomCenter, end: Alignment.topCenter, colors: [Colors.black87, Colors.transparent])), child: Column(children: [VideoProgressIndicator(_controller, allowScrubbing: true, colors: VideoProgressColors(playedColor: Color(0xFFEE0979))), SizedBox(height: 12), Row(children: [IconButton(icon: Icon(_controller.value.isPlaying? Icons.pause_circle_filled : Icons.play_circle_filled, color: Colors.white, size: 44), onPressed: () { setState(() { _controller.value.isPlaying? _controller.pause() : _controller.play(); }); _startHideTimer(); }), Text(_format(_controller.value.position), style: TextStyle(color: Colors.white, fontSize: 12)), Text(' / ${_format(_controller.value.duration)}', style: TextStyle(color: Colors.white54, fontSize: 12)), Spacer(), IconButton(icon: Icon(Icons.replay_10, color: Colors.white), onPressed: () { _controller.seekTo(_controller.value.position - Duration(seconds: 10)); _startHideTimer(); }), IconButton(icon: Icon(Icons.forward_10, color: Colors.white), onPressed: () { _controller.seekTo(_controller.value.position + Duration(seconds: 10)); _startHideTimer(); }),])]))),])),); }
}