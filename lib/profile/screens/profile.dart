
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:share_plus/share_plus.dart';
import 'package:video_player/video_player.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:syncfusion_flutter_pdf/pdf.dart';
import 'dart:io';
import 'package:timeago/timeago.dart' as timeago;

import '../api_service.dart';
import '../model.dart';
import '../../utils/api.dart';
import '../../services/auth_service.dart';
import '../../login/login_screen.dart';
import 'edit_profile.dart';
// NAYA IMPORT - path apne project ke hisab se check kar lena
import '../../post/screens/singlepost.dart';

class ProfileScreen extends StatefulWidget {
  final VoidCallback? onBackToHome;

  const ProfileScreen({super.key, this.onBackToHome});

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen>
    with SingleTickerProviderStateMixin {
  ProfileModel? user;
  List<PostModel> myPosts = [];
  List<PostModel> mediaPosts = [];
  List<PostModel> documentPosts = [];
  bool isLoading = true;
  bool isPostsLoading = true;
  bool isRefreshing = false;
  String? errorMessage;
  static const bgColor = Color(0xFF030F27);
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
    _loadData();
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  Future<void> _loadData({bool forceRefresh = false}) async {
    if (forceRefresh) {
      setState(() => isRefreshing = true);
    }
    await Future.wait([
      _loadProfile(forceRefresh: forceRefresh),
      _loadMyPosts(),
    ]);
    if (forceRefresh) {
      setState(() => isRefreshing = false);
    }
  }

  Future<void> _loadProfile({bool forceRefresh = false}) async {
    if (!forceRefresh) {
      setState(() {
        isLoading = true;
        errorMessage = null;
      });
    }
    try {
      final data = forceRefresh
         ? await ApiService.refreshProfile()
          : await ApiService.getProfile();
      if (mounted) {
        setState(() {
          user = data;
          isLoading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          errorMessage = e.toString();
          isLoading = false;
        });
      }
    }
  }

  Future<void> _loadMyPosts() async {
    setState(() => isPostsLoading = true);
    try {
      final posts = await ApiService.getMyPosts();
      if (mounted) {
        setState(() {
          myPosts = posts;
          mediaPosts = posts
             .where((p) => p.postType == 'image' || p.postType == 'video')
             .toList();
          documentPosts = posts
             .where((p) => ['document', 'pdf', 'excel', 'docx', 'xls', 'doc']
                 .contains(p.postType))
             .toList();
          isPostsLoading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() => isPostsLoading = false);
        print("Posts load error: $e");
      }
    }
  }

  Future<void> _refresh() async {
    await _loadData(forceRefresh: true);
  }

  Future<void> _goToEditProfile() async {
    if (user == null) return;
    final result = await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => EditProfileScreen(user: user!),
      ),
    );
    if (result == true) {
      await _loadData(forceRefresh: true);
    }
  }

  Future<void> _logout() async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text("Logout"),
        content: const Text("Are you sure you want to logout?"),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text("Cancel"),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text("Logout", style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (confirm == true) {
      await ApiService.clearProfileCache();
      await AuthService.logout();
      if (mounted) {
        Navigator.pushAndRemoveUntil(
          context,
          MaterialPageRoute(builder: (context) => const LoginScreen()),
          (route) => false,
        );
      }
    }
  }

  Future<void> _downloadDocument(String url, String fileName) async {
    try {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Downloading...")),
      );
      if (await canLaunchUrl(Uri.parse(url))) {
        await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
      } else {
        final dio = Dio();
        final dir = await getApplicationDocumentsDirectory();
        final filePath = "${dir.path}/$fileName";
        await dio.download(url, filePath);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Downloaded: $filePath")),
        );
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Download failed: $e"), backgroundColor: Colors.red),
      );
    }
  }

  void _openSinglePost(String postId) {
    // Yahan dynamic id jayegi
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => SinglePostPage(postId: postId),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (isLoading && user == null) {
      return const Scaffold(
        backgroundColor: Colors.white,
        body: Center(child: CircularProgressIndicator(color: bgColor)),
      );
    }
    if (errorMessage!= null && user == null) {
      return Scaffold(
        backgroundColor: Colors.white,
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(16.0),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Icon(Icons.error_outline, color: Colors.red, size: 60),
                const SizedBox(height: 16),
                Text(errorMessage!,
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: Colors.red, fontSize: 16)),
                const SizedBox(height: 16),
                ElevatedButton(
                  onPressed: () => _loadData(),
                  style: ElevatedButton.styleFrom(backgroundColor: bgColor),
                  child: const Text("Retry", style: TextStyle(color: Colors.white)),
                ),
              ],
            ),
          ),
        ),
      );
    }
    if (user == null) {
      return const Scaffold(
        backgroundColor: Colors.white,
        body: Center(child: Text("No Profile Found")),
      );
    }

    return Scaffold(
      backgroundColor: Colors.white,
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: SizedBox(
            width: double.infinity,
            height: 45,
            child: ElevatedButton.icon(
              onPressed: _logout,
              icon: const Icon(Icons.logout, size: 20, color: Colors.white),
              label: const Text("Logout",
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white)),
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.red.shade600,
                elevation: 0,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
              ),
            ),
          ),
        ),
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        color: bgColor,
        child: Stack(
          children: [
            SingleChildScrollView(
              physics: const AlwaysScrollableScrollPhysics(),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.only(top: 45, left: 4, right: 4, bottom: 10),
                    color: bgColor,
                    child: Row(
                      children: [
                        IconButton(
                          icon: const Icon(Icons.arrow_back, color: Colors.white),
                          onPressed: () {
                            if (widget.onBackToHome!= null) {
                              widget.onBackToHome!();
                            } else {
                              Navigator.maybePop(context);
                            }
                          },
                        ),
                        Expanded(
                          child: Center(
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Flexible(
                                  child: Text(
                                    user!.username,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                        color: Colors.white, fontSize: 20, fontWeight: FontWeight.bold),
                                  ),
                                ),
                                if (user!.isVerified)...[
                                  const SizedBox(width: 4),
                                  const Icon(Icons.verified, color: Colors.blue, size: 18),
                                ]
                              ],
                            ),
                          ),
                        ),
                        IconButton(
                          icon: const Icon(Icons.edit, color: Colors.white),
                          onPressed: _goToEditProfile,
                          tooltip: "Edit Profile",
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 20),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Row(
                      children: [
                        CircleAvatar(
                          radius: 45,
                          backgroundColor: Colors.grey.shade300,
                          child: ClipOval(
                            child: user!.profilePhoto.isEmpty
                               ? const Icon(Icons.person, size: 45, color: Colors.grey)
                                : CachedNetworkImage(
                                    imageUrl: user!.profilePhoto.startsWith('http')
                                       ? user!.profilePhoto
                                        : "${Api.baseUrl}${user!.profilePhoto}",
                                    width: 90,
                                    height: 90,
                                    fit: BoxFit.cover,
                                    placeholder: (c, u) => const CircularProgressIndicator(strokeWidth: 2),
                                    errorWidget: (c, u, e) => const Icon(Icons.error, size: 45, color: Colors.grey),
                                  ),
                          ),
                        ),
                        const SizedBox(width: 25),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                (user!.firstName.isEmpty && user!.lastName.isEmpty)
                                   ? "No Name"
                                    : "${user!.firstName} ${user!.lastName}".trim(),
                                style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
                              ),
                              const SizedBox(height: 8),
                              Row(
                                children: [
                                  Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text("${user!.followers}",
                                          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                                      const Text("Followers", style: TextStyle(color: Colors.grey)),
                                    ],
                                  ),
                                  const SizedBox(width: 30),
                                  Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text("${user!.following}",
                                          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                                      const Text("Following", style: TextStyle(color: Colors.grey)),
                                    ],
                                  ),
                                ],
                              ),
                            ],
                          ),
                        )
                      ],
                    ),
                  ),
                  const SizedBox(height: 20),
                  if (user!.bio.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Text(user!.bio, style: const TextStyle(fontSize: 14, height: 1.4)),
                    ),
                  const SizedBox(height: 25),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Row(
                      children: [
                        Expanded(
                          child: SizedBox(
                            height: 45,
                            child: ElevatedButton.icon(
                              onPressed: () {
                                ScaffoldMessenger.of(context)
                                   .showSnackBar(SnackBar(content: Text("Coins: ${user!.coin}")));
                              },
                              icon: const Icon(Icons.currency_rupee, size: 20, color: Colors.black87),
                              label: Text("Coins: ${user!.coin}",
                                  style: const TextStyle(
                                      fontSize: 14, fontWeight: FontWeight.bold, color: Colors.black87)),
                              style: ElevatedButton.styleFrom(
                                backgroundColor: Colors.grey.shade200,
                                elevation: 0,
                                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(width: 16),
                        Expanded(
                          child: SizedBox(
                            height: 45,
                            child: ElevatedButton.icon(
                              onPressed: () {
                                Share.share(
                                    "Check out ${user!.username}'s profile 👇\n${Api.baseUrl}/profile/${user!.username}");
                              },
                              icon: const Icon(Icons.share, size: 18, color: Colors.white),
                              label: const Text("Share Profile",
                                  style:
                                      TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white)),
                              style: ElevatedButton.styleFrom(
                                backgroundColor: bgColor,
                                elevation: 0,
                                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 20),
                  const Divider(indent: 16, endIndent: 16),
                  TabBar(
                    controller: _tabController,
                    labelColor: bgColor,
                    unselectedLabelColor: Colors.grey,
                    indicatorColor: bgColor,
                    tabs: [
                      Tab(
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            const Icon(Icons.photo_library, size: 18),
                            const SizedBox(width: 4),
                            Text("${mediaPosts.length} Photos/Videos"),
                          ],
                        ),
                      ),
                      Tab(
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            const Icon(Icons.description, size: 18),
                            const SizedBox(width: 4),
                            Text("${documentPosts.length} Documents"),
                          ],
                        ),
                      ),
                    ],
                  ),
                  SizedBox(
                    height: 600,
                    child: TabBarView(
                      controller: _tabController,
                      children: [
                        // TAB 1: Photos & Videos - CLICK LOGIC ADDED
                        isPostsLoading
                           ? const Center(child: CircularProgressIndicator(color: bgColor))
                            : mediaPosts.isEmpty
                               ? const Center(child: Text("No Photos/Videos Yet"))
                                : GridView.builder(
                                    padding: const EdgeInsets.all(2),
                                    gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                                      crossAxisCount: 3,
                                      crossAxisSpacing: 2,
                                      mainAxisSpacing: 2,
                                      childAspectRatio: 0.75,
                                    ),
                                    itemCount: mediaPosts.length,
                                    itemBuilder: (context, index) {
                                      final post = mediaPosts[index];
                                      return InkWell(
                                        onTap: () => _openSinglePost(post.id.toString()),
                                        child: Column(
                                          children: [
                                            Expanded(
                                              child: Stack(
                                                fit: StackFit.expand,
                                                children: [
                                                  Container(
                                                    decoration: BoxDecoration(
                                                      color: Colors.grey.shade300,
                                                      borderRadius: BorderRadius.circular(4),
                                                    ),
                                                    child: ClipRRect(
                                                      borderRadius: BorderRadius.circular(4),
                                                      child: post.postType == 'video'
                                                         ? VideoFirstFrame(videoUrl: post.firstImageUrl)
                                                          : CachedNetworkImage(
                                                              imageUrl: post.firstImageUrl,
                                                              fit: BoxFit.cover,
                                                              placeholder: (c, u) => Container(color: Colors.grey.shade300),
                                                              errorWidget: (c, u, e) => Container(
                                                                color: Colors.grey.shade300,
                                                                child: const Icon(Icons.error),
                                                              ),
                                                            ),
                                                    ),
                                                  ),
                                                  if (post.postType == 'video')
                                                    const Center(
                                                      child: Icon(Icons.play_circle_fill, size: 40, color: Colors.white),
                                                    ),
                                                ],
                                              ),
                                            ),
                                            const SizedBox(height: 4),
                                            Text(
                                              post.title?? post.content,
                                              maxLines: 1,
                                              overflow: TextOverflow.ellipsis,
                                              style: const TextStyle(fontSize: 11),
                                            ),
                                          ],
                                        ),
                                      );
                                    },
                                  ),

                        // TAB 2: Documents - CLICK LOGIC ADDED
                        isPostsLoading
                           ? const Center(child: CircularProgressIndicator(color: bgColor))
                            : documentPosts.isEmpty
                               ? const Center(child: Text("No Documents Yet"))
                                : GridView.builder(
                                    padding: const EdgeInsets.all(2),
                                    gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                                      crossAxisCount: 3,
                                      crossAxisSpacing: 2,
                                      mainAxisSpacing: 2,
                                      childAspectRatio: 0.75,
                                    ),
                                    itemCount: documentPosts.length,
                                    itemBuilder: (context, index) {
                                      final doc = documentPosts[index];
                                      final file = doc.media.isNotEmpty? doc.media.first : null;
                                      if (file == null) return const SizedBox.shrink();
                                      return InkWell(
                                        onTap: () => _openSinglePost(doc.id.toString()),
                                        child: DocumentGridTile(
                                          doc: doc,
                                          file: file,
                                          onDownload: () => _downloadDocument(file.file, file.fileName),
                                        ),
                                      );
                                    },
                                  ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 40),
                ],
              ),
            ),
            if (isRefreshing)
              Positioned(
                top: 100,
                left: 0,
                right: 0,
                child: Center(
                  child: Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(30),
                      boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.1), blurRadius: 10)],
                    ),
                    child: const CircularProgressIndicator(color: bgColor),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class VideoFirstFrame extends StatefulWidget {
  final String videoUrl;
  const VideoFirstFrame({super.key, required this.videoUrl});
  @override
  State<VideoFirstFrame> createState() => _VideoFirstFrameState();
}

class _VideoFirstFrameState extends State<VideoFirstFrame> {
  late VideoPlayerController _controller;
  bool _initialized = false;
  bool _hasError = false;
  @override
  void initState() {
    super.initState();
    _initializeVideo();
  }

  Future<void> _initializeVideo() async {
    try {
      _controller = VideoPlayerController.networkUrl(
        Uri.parse(widget.videoUrl),
        httpHeaders: {'User-Agent': 'Mozilla/5.0'},
      );
      await _controller.initialize();
      await _controller.seekTo(Duration.zero);
      await _controller.setVolume(0.0);
      await _controller.pause();
      if (mounted) setState(() => _initialized = true);
    } catch (e) {
      if (mounted) setState(() => _hasError = true);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_hasError) {
      return Container(color: Colors.black, child: const Icon(Icons.videocam, size: 50, color: Colors.white54));
    }
    if (!_initialized) {
      return Container(
        color: Colors.black,
        child: const Center(child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2)),
      );
    }
    return AspectRatio(aspectRatio: _controller.value.aspectRatio, child: VideoPlayer(_controller));
  }
}

class DocumentGridTile extends StatefulWidget {
  final PostModel doc;
  final PostMediaModel file;
  final VoidCallback onDownload;
  const DocumentGridTile({super.key, required this.doc, required this.file, required this.onDownload});
  @override
  State<DocumentGridTile> createState() => _DocumentGridTileState();
}

class _DocumentGridTileState extends State<DocumentGridTile> {
  int _totalPages = 0;
  bool _isLoadingPages = true;
  @override
  void initState() {
    super.initState();
    if (widget.file.file.toLowerCase().endsWith('.pdf')) {
      _getPdfPages();
    } else {
      setState(() => _isLoadingPages = false);
    }
  }

  Future<void> _getPdfPages() async {
    try {
      final response = await Dio().get(widget.file.file, options: Options(responseType: ResponseType.bytes));
      final PdfDocument document = PdfDocument(inputBytes: response.data);
      if (mounted) {
        setState(() {
          _totalPages = document.pages.count;
          _isLoadingPages = false;
        });
      }
      document.dispose();
    } catch (e) {
      if (mounted) setState(() => _isLoadingPages = false);
    }
  }

  IconData _getFileIcon() {
    final ext = widget.file.file.toLowerCase();
    if (ext.endsWith('.pdf')) return Icons.picture_as_pdf;
    if (ext.endsWith('.xls') || ext.endsWith('.xlsx')) return Icons.table_chart;
    if (ext.endsWith('.doc') || ext.endsWith('.docx')) return Icons.description;
    return Icons.insert_drive_file;
  }

  Color _getFileColor() {
    final ext = widget.file.file.toLowerCase();
    if (ext.endsWith('.pdf')) return Colors.red;
    if (ext.endsWith('.xls') || ext.endsWith('.xlsx')) return Colors.green;
    if (ext.endsWith('.doc') || ext.endsWith('.docx')) return Colors.blue;
    return Colors.grey;
  }

  @override
  Widget build(BuildContext context) {
    final isPdf = widget.file.file.toLowerCase().endsWith('.pdf');
    return Column(
      children: [
        Expanded(
          child: Container(
            decoration: BoxDecoration(
              color: Colors.grey.shade100,
              borderRadius: BorderRadius.circular(4),
              border: Border.all(color: Colors.grey.shade300),
            ),
            child: Stack(
              fit: StackFit.expand,
              children: [
                if (isPdf)
                  ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: SfPdfViewer.network(
                      widget.file.file,
                      canShowScrollHead: false,
                      canShowPaginationDialog: false,
                      canShowScrollStatus: false,
                      enableDoubleTapZooming: false,
                      pageLayoutMode: PdfPageLayoutMode.single,
                    ),
                  )
                else
                  Center(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(_getFileIcon(), size: 50, color: _getFileColor()),
                        const SizedBox(height: 4),
                        Text(widget.file.file.split('.').last.toUpperCase(),
                            style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: _getFileColor())),
                      ],
                    ),
                  ),
                Positioned(
                  top: 4,
                  right: 4,
                  child: InkWell(
                    onTap: widget.onDownload,
                    child: Container(
                      padding: const EdgeInsets.all(4),
                      decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(4)),
                      child: const Icon(Icons.download, size: 16, color: Colors.white),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 4),
        Text(widget.doc.title?? widget.file.fileName, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 11)),
        if (isPdf)
          Text(_isLoadingPages? 'Loading...' : '$_totalPages pages', style: const TextStyle(fontSize: 9, color: Colors.grey))
        else
          Text('${(widget.file.fileSizeBytes?? 0) / 1024 ~/ 1} KB', style: const TextStyle(fontSize: 9, color: Colors.grey)),
      ],
    );
  }
}

