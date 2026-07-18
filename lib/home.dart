
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
// import 'package:permission_handler/permission_handler.dart';
// import 'package:device_info_plus/device_info_plus.dart';
// import 'dart:io';

// import '../profile/screens/profile.dart';
// import 'search/search.dart';
// import 'post/screens/new_post.dart';
// import '../services/home_api_model_service.dart';

// class HomeScreen extends StatefulWidget {
//   const HomeScreen({super.key});
//   @override
//   State<HomeScreen> createState() => _HomeScreenState();
// }

// class _HomeScreenState extends State<HomeScreen> {
//   int _selectedIndex = 0;
//   List<PostModel> _posts = [];
//   bool _isLoading = true;
//   bool _isLoadingMore = false;
//   int _currentPage = 1;
//   bool _hasMore = true;
//   final ScrollController _scrollController = ScrollController();

//   // Reaction Map
//   final Map<String, String> _emojiMap = {
//     'like': '👍',
//     'confuse': '🤔',
//     'wrong': '❗',
//     'imp': '⭐',
//     'explain': '💡',
//   };

//   @override
//   void initState() {
//     super.initState();
//     _loadFeed();
//     _scrollController.addListener(_onScroll);
//   }

//   @override
//   void dispose() {
//     _scrollController.dispose();
//     super.dispose();
//   }

//   void _onScroll() {
//     if (_scrollController.position.pixels >= _scrollController.position.maxScrollExtent - 200) {
//       if (!_isLoadingMore && _hasMore) _loadMore();
//     }
//   }

//   Future<void> _loadFeed({bool refresh = false}) async {
//     if (refresh) {
//       setState(() {
//         _isLoading = true;
//         _currentPage = 1;
//       });
//     }
//     try {
//       final feedData = await HomeFeedService.getHomeFeed(page: 1, pageSize: 20);
//       setState(() {
//         _posts = feedData.results;
//         _isLoading = false;
//         _hasMore = feedData.next!= null;
//       });
//     } catch (e) {
//       setState(() => _isLoading = false);
//       if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Error: $e')));
//     }
//   }

//   Future<void> _loadMore() async {
//     if (_isLoadingMore ||!_hasMore) return;
//     setState(() => _isLoadingMore = true);
//     _currentPage++;
//     try {
//       final feedData = await HomeFeedService.getHomeFeed(page: _currentPage, pageSize: 20);
//       setState(() {
//         _posts.addAll(feedData.results);
//         _isLoadingMore = false;
//         _hasMore = feedData.next!= null;
//       });
//     } catch (e) {
//       _currentPage--;
//       setState(() => _isLoadingMore = false);
//     }
//   }

//   void _onItemTapped(int index) => setState(() => _selectedIndex = index);

//   void _sharePost(PostModel post) {
//     String shareText = post.content?? '';
//     if (post.media.isNotEmpty) shareText += '\n\n${post.media.first.file}';
//     Share.share(shareText, subject: 'Check out this post on LearnScroll');
//   }

//   // ===================== REACTION LOGIC =====================
//   void _showReactionSheet(PostModel post) {
//     showModalBottomSheet(
//       context: context,
//       backgroundColor: Colors.white,
//       shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
//       builder: (context) {
//         return Padding(
//           padding: const EdgeInsets.symmetric(vertical: 20, horizontal: 10),
//           child: Row(
//             mainAxisAlignment: MainAxisAlignment.spaceEvenly,
//             children: _emojiMap.entries.map((e) {
//               final isSelected = post.myReaction == e.key;
//               return GestureDetector(
//                 onTap: () {
//                   Navigator.pop(context);
//                   _onReactionTap(post, e.key);
//                 },
//                 child: Container(
//                   padding: const EdgeInsets.all(12),
//                   decoration: BoxDecoration(
//                     color: isSelected? Colors.blue.shade100 : Colors.grey.shade100,
//                     shape: BoxShape.circle,
//                     border: isSelected? Border.all(color: Colors.blue, width: 2) : null,
//                   ),
//                   child: Column(
//                     mainAxisSize: MainAxisSize.min,
//                     children: [
//                       Text(e.value, style: const TextStyle(fontSize: 26)),
//                       const SizedBox(height: 2),
//                       Text(e.key, style: const TextStyle(fontSize: 10)),
//                     ],
//                   ),
//                 ),
//               );
//             }).toList(),
//           ),
//         );
//       },
//     );
//   }

//   Future<void> _onReactionTap(PostModel post, String reaction) async {
//     final oldReaction = post.myReaction;
//     final index = _posts.indexWhere((p) => p.id == post.id);
//     if (index == -1) return;

//     // Optimistic Update
//     setState(() {
//       if (oldReaction == reaction) {
//         // UNLIKE
//         _posts[index].myReaction = null;
//         _posts[index].likesCount--;
//         if (reaction == 'like') _posts[index].likeCount--;
//         if (reaction == 'confuse') _posts[index].confuseCount--;
//         if (reaction == 'wrong') _posts[index].wrongCount--;
//         if (reaction == 'imp') _posts[index].impCount--;
//         if (reaction == 'explain') _posts[index].explainCount--;
//       } else {
//         if (oldReaction!= null) {
//           if (oldReaction == 'like') _posts[index].likeCount--;
//           if (oldReaction == 'confuse') _posts[index].confuseCount--;
//           if (oldReaction == 'wrong') _posts[index].wrongCount--;
//           if (oldReaction == 'imp') _posts[index].impCount--;
//           if (oldReaction == 'explain') _posts[index].explainCount--;
//         } else {
//           _posts[index].likesCount++;
//         }
//         _posts[index].myReaction = reaction;
//         if (reaction == 'like') _posts[index].likeCount++;
//         if (reaction == 'confuse') _posts[index].confuseCount++;
//         if (reaction == 'wrong') _posts[index].wrongCount++;
//         if (reaction == 'imp') _posts[index].impCount++;
//         if (reaction == 'explain') _posts[index].explainCount++;
//       }
//     });

//     try {
//       final result = await HomeFeedService.toggleReaction(post.id, reaction);
//       final counts = result['counts'];
//       setState(() {
//         _posts[index].likeCount = counts['like'];
//         _posts[index].confuseCount = counts['confuse'];
//         _posts[index].wrongCount = counts['wrong'];
//         _posts[index].impCount = counts['imp'];
//         _posts[index].explainCount = counts['explain'];
//         _posts[index].likesCount = counts['total'];
//         _posts[index].myReaction = result['my_reaction'];
//       });
//     } catch (e) {
//       setState(() => _posts[index].myReaction = oldReaction);
//       if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed: $e')));
//       _loadFeed(refresh: true);
//     }
//   }

//   Widget _homePage() {
//     return SafeArea(
//       bottom: false,
//       child: Container(
//         color: Colors.white,
//         child: RefreshIndicator(
//           onRefresh: () => _loadFeed(refresh: true),
//           child: CustomScrollView(
//             controller: _scrollController,
//             slivers: [
//               SliverAppBar(
//                 automaticallyImplyLeading: false,
//                 backgroundColor: const Color(0xFF030F27),
//                 elevation: 0,
//                 toolbarHeight: 75,
//                 floating: true,
//                 snap: true,
//                 pinned: false,
//                 title: Image.asset('assets/slogo1.png', height: 55, fit: BoxFit.contain,
//                     errorBuilder: (context, error, stackTrace) => const Text("LearnScroll", style: TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.bold))),
//                 actions: [
//                   IconButton(
//                     onPressed: () async {
//                       await Navigator.push(context, MaterialPageRoute(builder: (context) => const NewPost()));
//                       _loadFeed(refresh: true);
//                     },
//                     icon: const Icon(Icons.add_box_outlined, color: Colors.white, size: 28),
//                   ),
//                   const SizedBox(width: 8),
//                 ],
//               ),
//               if (_isLoading && _posts.isEmpty)
//                 const SliverFillRemaining(child: Center(child: CircularProgressIndicator()))
//               else if (_posts.isEmpty)
//                 SliverFillRemaining(
//                   child: Center(
//                     child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
//                       const Icon(Icons.dynamic_feed, size: 80, color: Colors.grey),
//                       const SizedBox(height: 16),
//                       const Text('No posts yet', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
//                       const SizedBox(height: 8),
//                       const Text('Follow people to see their posts', style: TextStyle(color: Colors.grey)),
//                       const SizedBox(height: 20),
//                       ElevatedButton(onPressed: () => _loadFeed(refresh: true), child: const Text('Refresh')),
//                     ]),
//                   ),
//                 )
//               else
//                 SliverList(
//                   delegate: SliverChildBuilderDelegate((context, index) {
//                     if (index < _posts.length) return _buildPostCard(_posts[index], index);
//                     if (_hasMore) return const Padding(padding: EdgeInsets.all(16.0), child: Center(child: CircularProgressIndicator()));
//                     return const SizedBox.shrink();
//                   }, childCount: _posts.length + (_hasMore? 1 : 0)),
//                 ),
//             ],
//           ),
//         ),
//       ),
//     );
//   }

//   Widget _buildPostCard(PostModel post, int postIndex) {
//     return Container(
//       margin: const EdgeInsets.only(bottom: 8),
//       color: Colors.white,
//       child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
//         ListTile(
//           leading: CircleAvatar(
//             backgroundColor: Colors.grey[300],
//             backgroundImage: post.user.profilePicture!= null? CachedNetworkImageProvider(post.user.profilePicture!) : null,
//             child: post.user.profilePicture == null? Text(post.user.username[0].toUpperCase()) : null,
//           ),
//           title: Text(post.user.username, style: const TextStyle(fontWeight: FontWeight.bold)),
//           subtitle: Text('${post.category} • ${timeago.format(post.createdAt)}', style: const TextStyle(fontSize: 12)),
//           trailing: const Icon(Icons.more_horiz),
//         ),
//         if (post.media.isNotEmpty) _MediaCarousel(mediaList: post.media, postId: post.id, postIndex: postIndex),
//         if (post.content!= null && post.content!.isNotEmpty)
//           Padding(padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8), child: Text(post.content!, maxLines: 3, overflow: TextOverflow.ellipsis)),
//         if (post.hashtags.isNotEmpty)
//           Padding(
//             padding: const EdgeInsets.symmetric(horizontal: 16),
//             child: Wrap(spacing: 8, children: post.hashtags.map((tag) => Text('#$tag', style: const TextStyle(color: Colors.blue))).toList()),
//           ),
//         // REACTION COUNTS ROW - Sabko dikhega
//         if (post.likesCount > 0)
//           Padding(
//             padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
//             child: Row(children: [
//               if (post.likeCount > 0) Padding(padding: const EdgeInsets.only(right: 6), child: Text('👍 ${post.likeCount}', style: const TextStyle(fontSize: 12))),
//               if (post.confuseCount > 0) Padding(padding: const EdgeInsets.only(right: 6), child: Text('🤔 ${post.confuseCount}', style: const TextStyle(fontSize: 12))),
//               if (post.wrongCount > 0) Padding(padding: const EdgeInsets.only(right: 6), child: Text('❗ ${post.wrongCount}', style: const TextStyle(fontSize: 12, color: Colors.red))),
//               if (post.impCount > 0) Padding(padding: const EdgeInsets.only(right: 6), child: Text('⭐ ${post.impCount}', style: const TextStyle(fontSize: 12))),
//               if (post.explainCount > 0) Padding(padding: const EdgeInsets.only(right: 6), child: Text('💡 ${post.explainCount}', style: const TextStyle(fontSize: 12))),
//               const Spacer(),
//               Text('${post.likesCount} reactions', style: const TextStyle(color: Colors.grey, fontSize: 12)),
//             ]),
//           ),
//         Padding(
//           padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
//           child: Row(children: [
//             // LIKE WITH LONG PRESS
//             GestureDetector(
//               onLongPress: () => _showReactionSheet(post),
//               onTap: () => _onReactionTap(post, 'like'),
//               child: Container(
//                 padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
//                 decoration: BoxDecoration(
//                   color: post.myReaction!= null? Colors.blue.shade50 : Colors.transparent,
//                   borderRadius: BorderRadius.circular(20),
//                   border: Border.all(color: post.myReaction!= null? Colors.blue : Colors.transparent),
//                 ),
//                 child: Row(children: [
//                   Text(post.myReaction!= null? (_emojiMap[post.myReaction]?? '👍') : '👍', style: const TextStyle(fontSize: 18)),
//                   if (post.myReaction!= null)...[
//                     const SizedBox(width: 4),
//                     Text(post.myReaction!, style: const TextStyle(fontSize: 12, color: Colors.blue, fontWeight: FontWeight.bold)),
//                   ]
//                 ]),
//               ),
//             ),
//             const SizedBox(width: 8),
//             const Icon(Icons.comment_outlined, size: 20),
//             const SizedBox(width: 4),
//             Text('${post.commentsCount}'),
//             const SizedBox(width: 16),
//             IconButton(icon: const Icon(Icons.share_outlined, size: 20), onPressed: () => _sharePost(post), padding: EdgeInsets.zero, constraints: const BoxConstraints()),
//             const SizedBox(width: 4),
//             Text('${post.sharesCount}'),
//             const Spacer(),
//             IconButton(icon: Icon(post.isSaved? Icons.bookmark : Icons.bookmark_border), onPressed: () {}),
//           ]),
//         ),
//         const Divider(height: 1),
//       ]),
//     );
//   }

//   @override
//   Widget build(BuildContext context) {
//     return AnnotatedRegion<SystemUiOverlayStyle>(
//       value: SystemUiOverlayStyle.light.copyWith(statusBarColor: const Color(0xFF030F27), statusBarIconBrightness: Brightness.light, statusBarBrightness: Brightness.dark),
//       child: Scaffold(
//         backgroundColor: const Color(0xFF030F27),
//         body: IndexedStack(index: _selectedIndex, children: [
//           _homePage(),
//           const SearchScreen(),
//           ProfileScreen(onBackToHome: () => setState(() => _selectedIndex = 0)),
//         ]),
//         bottomNavigationBar: BottomNavigationBar(
//           currentIndex: _selectedIndex,
//           onTap: _onItemTapped,
//           type: BottomNavigationBarType.fixed,
//           backgroundColor: const Color(0xFF030F27),
//           selectedItemColor: Colors.white,
//           unselectedItemColor: Colors.white60,
//           items: const [
//             BottomNavigationBarItem(icon: Icon(Icons.home_outlined), activeIcon: Icon(Icons.home), label: "Home"),
//             BottomNavigationBarItem(icon: Icon(Icons.search), label: "Search"),
//             BottomNavigationBarItem(icon: Icon(Icons.person_outline), activeIcon: Icon(Icons.person), label: "Profile"),
//           ],
//         ),
//       ),
//     );
//   }
// }

// // ======== MEDIA CAROUSEL - SAME AS YOURS (NO CHANGE) ========
// class _MediaCarousel extends StatefulWidget {
//   final List<PostMediaModel> mediaList;
//   final String postId;
//   final int postIndex;
//   const _MediaCarousel({required this.mediaList, required this.postId, required this.postIndex});
//   @override
//   State<_MediaCarousel> createState() => _MediaCarouselState();
// }

// class _MediaCarouselState extends State<_MediaCarousel> {
//   late PageController _pageController;
//   int _currentPage = 0;
//   bool _showDots = true;
//   Map<int, VideoPlayerController> _videoControllers = {};
//   @override
//   void initState() {
//     super.initState();
//     _pageController = PageController();
//     _initVideos();
//     if (widget.mediaList.length > 1) Future.delayed(const Duration(seconds: 7), () { if (mounted) setState(() => _showDots = false); });
//   }
//   void _initVideos() {
//     for (int i = 0; i < widget.mediaList.length; i++) {
//       if (widget.mediaList[i].mediaType == 'video') {
//         final controller = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file));
//         _videoControllers[i] = controller;
//         controller.initialize().then((_) { if (mounted) setState(() {}); });
//       }
//     }
//   }
//   @override
//   void dispose() {
//     _pageController.dispose();
//     _videoControllers.values.forEach((c) => c.dispose());
//     super.dispose();
//   }
//   void _handleVisibility(bool isVisible, int index) {
//     if (widget.mediaList[index].mediaType == 'video') {
//       final controller = _videoControllers[index];
//       if (controller!= null && controller.value.isInitialized) {
//         if (isVisible && _currentPage == index) { controller.play(); controller.setLooping(true); } else { controller.pause(); }
//       }
//     }
//   }
//   bool _isPDF(String url) => url.toLowerCase().endsWith('.pdf');
//   bool _isDoc(String url) {
//     final lower = url.toLowerCase();
//     return lower.endsWith('.doc') || lower.endsWith('.docx') || lower.endsWith('.xls') || lower.endsWith('.xlsx') || lower.endsWith('.ppt') || lower.endsWith('.pptx') || lower.endsWith('.zip') || lower.endsWith('.rar') || lower.endsWith('.txt');
//   }
//   Future<void> _downloadFile(String url, String fileName) async {
//     try {
//       bool permissionGranted = false;
//       if (Platform.isAndroid) {
//         final deviceInfo = await DeviceInfoPlugin().androidInfo;
//         if (deviceInfo.version.sdkInt >= 33) permissionGranted = true;
//         else { var status = await Permission.storage.request(); permissionGranted = status.isGranted; }
//       } else permissionGranted = true;
//       if (!permissionGranted) return;
//       if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Downloading $fileName...')));
//       Directory? dir;
//       if (Platform.isAndroid) {
//         dir = Directory('/storage/emulated/0/Download');
//         if (!await dir.exists()) dir = await getExternalStorageDirectory();
//       } else dir = await getApplicationDocumentsDirectory();
//       String savePath = '${dir!.path}/$fileName';
//       Dio dio = Dio();
//       await dio.download(url, savePath);
//       if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Downloaded: $fileName')));
//     } catch (e) {
//       if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Download failed: $e')));
//     }
//   }
//   @override
//   Widget build(BuildContext context) {
//     final screenHeight = MediaQuery.of(context).size.height;
//     final maxHeight = screenHeight * 0.6;
//     return GestureDetector(
//       onTap: () { setState(() => _showDots = true); Future.delayed(const Duration(seconds: 7), () { if (mounted) setState(() => _showDots = false); }); },
//       child: SizedBox(
//         height: maxHeight,
//         child: Stack(alignment: Alignment.center, children: [
//           PageView.builder(
//             controller: _pageController,
//             itemCount: widget.mediaList.length,
//             onPageChanged: (index) {
//               if (widget.mediaList[_currentPage].mediaType == 'video') _videoControllers[_currentPage]?.pause();
//               setState(() { _currentPage = index; _showDots = true; });
//               if (widget.mediaList[index].mediaType == 'video') _videoControllers[index]?.play();
//               Future.delayed(const Duration(seconds: 7), () { if (mounted) setState(() => _showDots = false); });
//             },
//             itemBuilder: (context, index) {
//               final media = widget.mediaList[index];
//               return VisibilityDetector(key: Key('${widget.postId}_$index'), onVisibilityChanged: (info) => _handleVisibility(info.visibleFraction > 0.5, index), child: _buildMediaItem(media, index, maxHeight));
//             },
//           ),
//           if (widget.mediaList.length > 1 && _showDots)
//             Positioned(bottom: 10, child: Container(padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4), decoration: BoxDecoration(color: Colors.black45, borderRadius: BorderRadius.circular(12)), child: Row(mainAxisAlignment: MainAxisAlignment.center, children: List.generate(widget.mediaList.length, (index) => Container(margin: const EdgeInsets.symmetric(horizontal: 3), width: 8, height: 8, decoration: BoxDecoration(shape: BoxShape.circle, color: _currentPage == index? Colors.white : Colors.white54)))))),
//         ]),
//       ),
//     );
//   }
//   Widget _buildMediaItem(PostMediaModel media, int index, double maxHeight) {
//     if (media.mediaType == 'video') {
//       final controller = _videoControllers[index];
//       if (controller == null ||!controller.value.isInitialized) return Container(height: maxHeight, color: Colors.black, child: const Center(child: CircularProgressIndicator(color: Colors.white)));
//       return GestureDetector(onTap: () => _openFullScreen(context, index), child: Container(color: Colors.black, child: Center(child: AspectRatio(aspectRatio: controller.value.aspectRatio, child: VideoPlayer(controller)))));
//     } else if (_isPDF(media.file)) {
//       return GestureDetector(onTap: () => _openFullScreen(context, index), child: Container(color: Colors.grey[100], child: Stack(children: [SfPdfViewer.network(media.file), Positioned(top: 8, right: 8, child: InkWell(onTap: () => _downloadFile(media.file, media.fileName), child: Container(padding: const EdgeInsets.all(6), decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(4)), child: const Icon(Icons.download, color: Colors.white, size: 20))))])));
//     } else if (_isDoc(media.file)) {
//       return GestureDetector(onTap: () => _downloadFile(media.file, media.fileName), child: Container(color: Colors.grey[100], child: Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [const Icon(Icons.description, size: 80, color: Colors.blue), Padding(padding: const EdgeInsets.symmetric(horizontal: 24), child: Text(media.fileName, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold), textAlign: TextAlign.center)), const SizedBox(height: 16), ElevatedButton.icon(onPressed: () => _downloadFile(media.file, media.fileName), icon: const Icon(Icons.download), label: const Text('Download File'))]))));
//     } else {
//       return GestureDetector(onTap: () => _openFullScreen(context, index), child: CachedNetworkImage(imageUrl: media.file, width: double.infinity, fit: BoxFit.cover, placeholder: (context, url) => Container(height: maxHeight, color: Colors.grey[200], child: const Center(child: CircularProgressIndicator())), errorWidget: (context, url, error) => Container(height: maxHeight, color: Colors.grey[200], child: const Icon(Icons.error, size: 50))));
//     }
//   }
//   void _openFullScreen(BuildContext context, int initialIndex) {
//     _videoControllers.values.forEach((c) => c.pause());
//     Navigator.push(context, MaterialPageRoute(builder: (context) => _FullScreenViewer(mediaList: widget.mediaList, initialIndex: initialIndex))).then((_) { if (widget.mediaList[_currentPage].mediaType == 'video') _videoControllers[_currentPage]?.play(); });
//   }
// }

// class _FullScreenViewer extends StatefulWidget {
//   final List<PostMediaModel> mediaList;
//   final int initialIndex;
//   const _FullScreenViewer({required this.mediaList, required this.initialIndex});
//   @override
//   State<_FullScreenViewer> createState() => _FullScreenViewerState();
// }

// class _FullScreenViewerState extends State<_FullScreenViewer> {
//   late PageController _controller;
//   late int _currentIndex;
//   Map<int, VideoPlayerController> _videoControllers = {};
//   @override
//   void initState() { super.initState(); _currentIndex = widget.initialIndex; _controller = PageController(initialPage: widget.initialIndex); _initVideos(); }
//   void _initVideos() { for (int i = 0; i < widget.mediaList.length; i++) { if (widget.mediaList[i].mediaType == 'video') { final controller = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file)); _videoControllers[i] = controller; controller.initialize().then((_) { if (mounted) { setState(() {}); if (i == _currentIndex) controller.play(); } }); } } }
//   bool _isPDF(String url) => url.toLowerCase().endsWith('.pdf');
//   bool _isDoc(String url) { final lower = url.toLowerCase(); return lower.endsWith('.doc') || lower.endsWith('.docx') || lower.endsWith('.xls') || lower.endsWith('.xlsx') || lower.endsWith('.ppt') || lower.endsWith('.pptx') || lower.endsWith('.zip') || lower.endsWith('.rar') || lower.endsWith('.txt'); }
//   Future<void> _downloadFile(String url, String fileName) async { try { Directory? dir; if (Platform.isAndroid) { dir = Directory('/storage/emulated/0/Download'); if (!await dir.exists()) dir = await getExternalStorageDirectory(); } else dir = await getApplicationDocumentsDirectory(); String savePath = '${dir!.path}/$fileName'; Dio dio = Dio(); await dio.download(url, savePath); if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Downloaded: $fileName'))); } catch (e) { if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Download failed: $e'))); } }
//   @override
//   void dispose() { _videoControllers.values.forEach((c) => c.dispose()); _controller.dispose(); super.dispose(); }
//   @override
//   Widget build(BuildContext context) {
//     final currentMedia = widget.mediaList[_currentIndex];
//     return Scaffold(backgroundColor: Colors.black, appBar: AppBar(backgroundColor: Colors.black, iconTheme: const IconThemeData(color: Colors.white), title: Text('${_currentIndex + 1}/${widget.mediaList.length}', style: const TextStyle(color: Colors.white)), actions: [if (_isPDF(currentMedia.file) || _isDoc(currentMedia.file)) IconButton(icon: const Icon(Icons.download), onPressed: () => _downloadFile(currentMedia.file, currentMedia.fileName))]), body: PageView.builder(controller: _controller, itemCount: widget.mediaList.length, onPageChanged: (i) { _videoControllers[_currentIndex]?.pause(); setState(() => _currentIndex = i); _videoControllers[i]?.play(); }, itemBuilder: (context, index) { final media = widget.mediaList[index]; if (media.mediaType == 'video') { final controller = _videoControllers[index]; return controller!= null && controller.value.isInitialized? Center(child: AspectRatio(aspectRatio: controller.value.aspectRatio, child: VideoPlayer(controller))) : const Center(child: CircularProgressIndicator(color: Colors.white)); } else if (_isPDF(media.file)) { return SfPdfViewer.network(media.file); } else if (_isDoc(media.file)) { return Container(color: Colors.white, child: Center(child: ElevatedButton.icon(onPressed: () => _downloadFile(media.file, media.fileName), icon: const Icon(Icons.download), label: const Text('Download File')))); } else { return InteractiveViewer(child: CachedNetworkImage(imageUrl: media.file, fit: BoxFit.contain)); } }));
//   }
// }































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
import 'package:permission_handler/permission_handler.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'dart:io';

import '../profile/screens/profile.dart';
import 'search/search.dart';
import 'post/screens/new_post.dart';
import '../services/home_api_model_service.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _selectedIndex = 0;
  List<PostModel> _posts = [];
  bool _isLoading = true;
  bool _isLoadingMore = false;
  int _currentPage = 1;
  bool _hasMore = true;
  final ScrollController _scrollController = ScrollController();
  final Map<String, String> _emojiMap = {
    'like': '👍',
    'confuse': '🤔',
    'wrong': '❗',
    'imp': '⭐',
    'explain': '💡',
  };

  @override
  void initState() {
    super.initState();
    _loadFeed();
    _scrollController.addListener(_onScroll);
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (_scrollController.position.pixels >= _scrollController.position.maxScrollExtent - 200) {
      if (!_isLoadingMore && _hasMore) _loadMore();
    }
  }

  Future<void> _loadFeed({bool refresh = false}) async {
    if (refresh) {
      setState(() {
        _isLoading = true;
        _currentPage = 1;
      });
    }
    try {
      final feedData = await HomeFeedService.getHomeFeed(page: 1, pageSize: 20);
      setState(() {
        _posts = feedData.results;
        _isLoading = false;
        _hasMore = feedData.next!= null;
      });
    } catch (e) {
      setState(() => _isLoading = false);
    }
  }

  Future<void> _loadMore() async {
    if (_isLoadingMore ||!_hasMore) return;
    setState(() => _isLoadingMore = true);
    _currentPage++;
    try {
      final feedData = await HomeFeedService.getHomeFeed(page: _currentPage, pageSize: 20);
      setState(() {
        _posts.addAll(feedData.results);
        _isLoadingMore = false;
        _hasMore = feedData.next!= null;
      });
    } catch (e) {
      _currentPage--;
      setState(() => _isLoadingMore = false);
    }
  }

  void _onItemTapped(int index) => setState(() => _selectedIndex = index);

  void _sharePost(PostModel post) {
    String shareText = post.content?? '';
    if (post.media.isNotEmpty) shareText += '\n\n${post.media.first.file}';
    Share.share(shareText);
  }

  void _showReactionSheet(PostModel post) {
    showModalBottomSheet(
      context: context,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (context) {
        return Padding(
          padding: const EdgeInsets.all(20),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceEvenly,
            children: _emojiMap.entries.map((e) {
              bool isSel = post.myReaction == e.key;
              return GestureDetector(
                onTap: () {
                  Navigator.pop(context);
                  _handleReaction(post, e.key);
                },
                child: Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: isSel? Colors.blue.shade100 : Colors.grey.shade100,
                    shape: BoxShape.circle,
                    border: isSel? Border.all(color: Colors.blue, width: 2) : null,
                  ),
                  child: Text(e.value, style: const TextStyle(fontSize: 28)),
                ),
              );
            }).toList(),
          ),
        );
      },
    );
  }

  Future<void> _handleReaction(PostModel post, String reaction) async {
    final oldReaction = post.myReaction;
    final idx = _posts.indexWhere((p) => p.id == post.id);
    if (idx == -1) return;

    setState(() {
      if (oldReaction == reaction) {
        _posts[idx].myReaction = null;
        _posts[idx].likesCount--;
        if (reaction == 'like') _posts[idx].likeCount--;
        if (reaction == 'confuse') _posts[idx].confuseCount--;
        if (reaction == 'wrong') _posts[idx].wrongCount--;
        if (reaction == 'imp') _posts[idx].impCount--;
        if (reaction == 'explain') _posts[idx].explainCount--;
      } else {
        if (oldReaction!= null) {
          if (oldReaction == 'like') _posts[idx].likeCount--;
          if (oldReaction == 'confuse') _posts[idx].confuseCount--;
          if (oldReaction == 'wrong') _posts[idx].wrongCount--;
          if (oldReaction == 'imp') _posts[idx].impCount--;
          if (oldReaction == 'explain') _posts[idx].explainCount--;
        } else {
          _posts[idx].likesCount++;
        }
        _posts[idx].myReaction = reaction;
        if (reaction == 'like') _posts[idx].likeCount++;
        if (reaction == 'confuse') _posts[idx].confuseCount++;
        if (reaction == 'wrong') _posts[idx].wrongCount++;
        if (reaction == 'imp') _posts[idx].impCount++;
        if (reaction == 'explain') _posts[idx].explainCount++;
      }
    });

    try {
      final res = await HomeFeedService.toggleReaction(post.id, reaction);
      final c = res['counts'];
      setState(() {
        _posts[idx].likeCount = c['like'];
        _posts[idx].confuseCount = c['confuse'];
        _posts[idx].wrongCount = c['wrong'];
        _posts[idx].impCount = c['imp'];
        _posts[idx].explainCount = c['explain'];
        _posts[idx].likesCount = c['total'];
        _posts[idx].myReaction = res['my_reaction'];
      });
    } catch (e) {
      setState(() => _posts[idx].myReaction = oldReaction);
    }
  }

  Widget _homePage() {
    return SafeArea(
      bottom: false,
      child: Container(
        color: Colors.white,
        child: RefreshIndicator(
          onRefresh: () => _loadFeed(refresh: true),
          child: CustomScrollView(
            controller: _scrollController,
            slivers: [
              SliverAppBar(
                automaticallyImplyLeading: false,
                backgroundColor: const Color(0xFF030F27),
                toolbarHeight: 75,
                floating: true,
                snap: true,
                title: Image.asset('assets/slogo1.png', height: 55, errorBuilder: (_, __, ___) => const Text("LearnScroll", style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold))),
                actions: [
                  IconButton(
                    onPressed: () async {
                      await Navigator.push(context, MaterialPageRoute(builder: (_) => const NewPost()));
                      _loadFeed(refresh: true);
                    },
                    icon: const Icon(Icons.add_box_outlined, color: Colors.white, size: 28),
                  ),
                  const SizedBox(width: 8),
                ],
              ),
              if (_isLoading && _posts.isEmpty)
                const SliverFillRemaining(child: Center(child: CircularProgressIndicator()))
              else
                SliverList(
                  delegate: SliverChildBuilderDelegate((context, index) {
                    if (index < _posts.length) return _buildPostCard(_posts[index], index);
                    return const Padding(padding: EdgeInsets.all(16), child: Center(child: CircularProgressIndicator()));
                  }, childCount: _posts.length + (_hasMore? 1 : 0)),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildPostCard(PostModel post, int postIndex) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      color: Colors.white,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ListTile(
            leading: CircleAvatar(
              backgroundColor: Colors.grey[300],
              backgroundImage: post.user.profilePicture!= null? CachedNetworkImageProvider(post.user.profilePicture!) : null,
              child: post.user.profilePicture == null? Text(post.user.username[0].toUpperCase()) : null,
            ),
            title: Text(post.user.username, style: const TextStyle(fontWeight: FontWeight.bold)),
            subtitle: Text('${post.category} • ${timeago.format(post.createdAt)}', style: const TextStyle(fontSize: 12)),
            trailing: const Icon(Icons.more_horiz),
          ),
          if (post.media.isNotEmpty) _MediaCarousel(mediaList: post.media, postId: post.id, postIndex: postIndex),
          if (post.content!= null && post.content!.isNotEmpty) Padding(padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8), child: Text(post.content!)),
          if (post.hashtags.isNotEmpty) Padding(padding: const EdgeInsets.symmetric(horizontal: 16), child: Wrap(spacing: 8, children: post.hashtags.map((t) => Text('#$t', style: const TextStyle(color: Colors.blue))).toList())),
          if (post.likesCount > 0)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
              child: Row(children: [
                if (post.likeCount > 0) Text('👍 ${post.likeCount} '),
                if (post.confuseCount > 0) Text('🤔 ${post.confuseCount} '),
                if (post.wrongCount > 0) Text('❗ ${post.wrongCount} '),
                if (post.impCount > 0) Text('⭐ ${post.impCount} '),
                if (post.explainCount > 0) Text('💡 ${post.explainCount} '),
                const Spacer(),
                Text('${post.likesCount} reactions', style: const TextStyle(fontSize: 12, color: Colors.grey)),
              ]),
            ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            child: Row(children: [
              GestureDetector(
                onLongPress: () => _showReactionSheet(post),
                onTap: () => _handleReaction(post, 'like'),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  decoration: BoxDecoration(
                    color: post.myReaction!= null? Colors.blue.shade50 : Colors.transparent,
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: post.myReaction!= null? Colors.blue : Colors.transparent),
                  ),
                  child: Row(children: [
                    Text(post.myReaction!= null? (_emojiMap[post.myReaction]?? '👍') : '👍', style: const TextStyle(fontSize: 18)),
                    const SizedBox(width: 4),
                    Text(post.myReaction?? 'Like', style: TextStyle(fontSize: 12, color: post.myReaction!= null? Colors.blue : Colors.black54, fontWeight: post.myReaction!= null? FontWeight.bold : FontWeight.normal)),
                  ]),
                ),
              ),
              const SizedBox(width: 16),
              const Icon(Icons.comment_outlined, size: 20),
              const SizedBox(width: 4),
              Text('${post.commentsCount}'),
              const SizedBox(width: 16),
              IconButton(icon: const Icon(Icons.share_outlined, size: 20), onPressed: () => _sharePost(post), padding: EdgeInsets.zero, constraints: const BoxConstraints()),
              Text('${post.sharesCount}'),
              const Spacer(),
              IconButton(icon: Icon(post.isSaved? Icons.bookmark : Icons.bookmark_border), onPressed: () {}),
            ]),
          ),
          const Divider(height: 1),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF030F27),
      body: IndexedStack(index: _selectedIndex, children: [
        _homePage(),
        const SearchScreen(),
        ProfileScreen(onBackToHome: () => setState(() => _selectedIndex = 0)),
      ]),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _selectedIndex,
        onTap: _onItemTapped,
        backgroundColor: const Color(0xFF030F27),
        selectedItemColor: Colors.white,
        unselectedItemColor: Colors.white60,
        items: const [
          BottomNavigationBarItem(icon: Icon(Icons.home_outlined), label: "Home"),
          BottomNavigationBarItem(icon: Icon(Icons.search), label: "Search"),
          BottomNavigationBarItem(icon: Icon(Icons.person_outline), label: "Profile"),
        ],
      ),
    );
  }
}

class _MediaCarousel extends StatefulWidget {
  final List<PostMediaModel> mediaList;
  final String postId;
  final int postIndex;
  const _MediaCarousel({required this.mediaList, required this.postId, required this.postIndex});
  @override
  State<_MediaCarousel> createState() => _MediaCarouselState();
}

class _MediaCarouselState extends State<_MediaCarousel> {
  late PageController _pageController;
  int _currentPage = 0;
  bool _showDots = true;
  Map<int, VideoPlayerController> _videoControllers = {};
  @override
  void initState() {
    super.initState();
    _pageController = PageController();
    _initVideos();
    if (widget.mediaList.length > 1) Future.delayed(const Duration(seconds: 7), () { if (mounted) setState(() => _showDots = false); });
  }
  void _initVideos() {
    for (int i = 0; i < widget.mediaList.length; i++) {
      if (widget.mediaList[i].mediaType == 'video') {
        final c = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file));
        _videoControllers[i] = c;
        c.initialize().then((_) { if (mounted) setState(() {}); });
      }
    }
  }
  @override
  void dispose() {
    _pageController.dispose();
    _videoControllers.values.forEach((c) => c.dispose());
    super.dispose();
  }
  void _handleVisibility(bool v, int i) {
    final c = _videoControllers[i];
    if (c!= null && c.value.isInitialized) {
      if (v && _currentPage == i) { c.play(); c.setLooping(true); } else c.pause();
    }
  }
  bool _isPDF(String u) => u.toLowerCase().endsWith('.pdf');
  bool _isDoc(String u) { final l = u.toLowerCase(); return l.endsWith('.doc') || l.endsWith('.docx') || l.endsWith('.xls') || l.endsWith('.xlsx') || l.endsWith('.ppt') || l.endsWith('.pptx'); }
  Future<void> _downloadFile(String url, String fileName) async {
    try {
      Directory? dir;
      if (Platform.isAndroid) { dir = Directory('/storage/emulated/0/Download'); if (!await dir.exists()) dir = await getExternalStorageDirectory(); } else dir = await getApplicationDocumentsDirectory();
      await Dio().download(url, '${dir!.path}/$fileName');
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Downloaded $fileName')));
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed $e')));
    }
  }
  @override
  Widget build(BuildContext context) {
    final maxHeight = MediaQuery.of(context).size.height * 0.6;
    return SizedBox(
      height: maxHeight,
      child: Stack(alignment: Alignment.center, children: [
        PageView.builder(
          controller: _pageController,
          itemCount: widget.mediaList.length,
          onPageChanged: (i) { _videoControllers[_currentPage]?.pause(); setState(() { _currentPage = i; _showDots = true; }); _videoControllers[i]?.play(); Future.delayed(const Duration(seconds: 7), () { if (mounted) setState(() => _showDots = false); }); },
          itemBuilder: (c, i) { final m = widget.mediaList[i]; return VisibilityDetector(key: Key('${widget.postId}_$i'), onVisibilityChanged: (info) => _handleVisibility(info.visibleFraction > 0.5, i), child: _buildMediaItem(m, i, maxHeight)); },
        ),
        if (widget.mediaList.length > 1 && _showDots) Positioned(bottom: 10, child: Container(padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4), decoration: BoxDecoration(color: Colors.black45, borderRadius: BorderRadius.circular(12)), child: Row(children: List.generate(widget.mediaList.length, (i) => Container(margin: const EdgeInsets.symmetric(horizontal: 3), width: 8, height: 8, decoration: BoxDecoration(shape: BoxShape.circle, color: _currentPage == i? Colors.white : Colors.white54))))))
      ]),
    );
  }
  Widget _buildMediaItem(PostMediaModel media, int index, double maxHeight) {
    if (media.mediaType == 'video') { final c = _videoControllers[index]; if (c == null ||!c.value.isInitialized) return Container(height: maxHeight, color: Colors.black, child: const Center(child: CircularProgressIndicator(color: Colors.white))); return GestureDetector(onTap: () => _openFullScreen(context, index), child: Container(color: Colors.black, child: Center(child: AspectRatio(aspectRatio: c.value.aspectRatio, child: VideoPlayer(c))))); }
    else if (_isPDF(media.file)) return SfPdfViewer.network(media.file);
    else if (_isDoc(media.file)) return Center(child: ElevatedButton.icon(onPressed: () => _downloadFile(media.file, media.fileName), icon: const Icon(Icons.download), label: const Text('Download')));
    else return GestureDetector(onTap: () => _openFullScreen(context, index), child: CachedNetworkImage(imageUrl: media.file, fit: BoxFit.cover, placeholder: (c, u) => Container(color: Colors.grey[200], child: const Center(child: CircularProgressIndicator())), errorWidget: (c, u, e) => const Icon(Icons.error)));
  }
  void _openFullScreen(BuildContext context, int initialIndex) { _videoControllers.values.forEach((c) => c.pause()); Navigator.push(context, MaterialPageRoute(builder: (_) => _FullScreenViewer(mediaList: widget.mediaList, initialIndex: initialIndex))).then((_) => _videoControllers[_currentPage]?.play()); }
}

class _FullScreenViewer extends StatefulWidget {
  final List<PostMediaModel> mediaList;
  final int initialIndex;
  const _FullScreenViewer({required this.mediaList, required this.initialIndex});
  @override
  State<_FullScreenViewer> createState() => _FullScreenViewerState();
}

class _FullScreenViewerState extends State<_FullScreenViewer> {
  late PageController _controller;
  late int _currentIndex;
  Map<int, VideoPlayerController> _videoControllers = {};
  @override
  void initState() { super.initState(); _currentIndex = widget.initialIndex; _controller = PageController(initialPage: widget.initialIndex); for (int i = 0; i < widget.mediaList.length; i++) { if (widget.mediaList[i].mediaType == 'video') { final c = VideoPlayerController.networkUrl(Uri.parse(widget.mediaList[i].file)); _videoControllers[i] = c; c.initialize().then((_) { if (mounted) { setState(() {}); if (i == _currentIndex) c.play(); } }); } } }
  @override
  void dispose() { _videoControllers.values.forEach((c) => c.dispose()); _controller.dispose(); super.dispose(); }
  bool _isPDF(String u) => u.toLowerCase().endsWith('.pdf');
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(backgroundColor: Colors.black, title: Text('${_currentIndex + 1}/${widget.mediaList.length}', style: const TextStyle(color: Colors.white))),
      body: PageView.builder(
        controller: _controller,
        itemCount: widget.mediaList.length,
        onPageChanged: (i) { _videoControllers[_currentIndex]?.pause(); setState(() => _currentIndex = i); _videoControllers[i]?.play(); },
        itemBuilder: (c, i) { final m = widget.mediaList[i]; if (m.mediaType == 'video') { final con = _videoControllers[i]; return con!= null && con.value.isInitialized? Center(child: AspectRatio(aspectRatio: con.value.aspectRatio, child: VideoPlayer(con))) : const Center(child: CircularProgressIndicator(color: Colors.white)); } else if (_isPDF(m.file)) return SfPdfViewer.network(m.file); else return InteractiveViewer(child: CachedNetworkImage(imageUrl: m.file, fit: BoxFit.contain)); },
      ),
    );
  }
}