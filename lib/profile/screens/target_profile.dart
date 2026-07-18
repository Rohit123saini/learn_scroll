// import 'package:flutter/material.dart';
// import 'package:cached_network_image/cached_network_image.dart';
// import 'package:share_plus/share_plus.dart';

// import '../api_service.dart';
// import '../model.dart';
// import '../../utils/api.dart';

// class TargetProfilePage extends StatefulWidget {
//   final String username;

//   const TargetProfilePage({Key? key, required this.username}) : super(key: key);

//   @override
//   State<TargetProfilePage> createState() => _TargetProfilePageState();
// }

// class _TargetProfilePageState extends State<TargetProfilePage> {
//   TargetProfileModel? targetUser;
//   bool isLoading = true;
//   bool isActionLoading = false;
//   String? errorMessage;
//   static const bgColor = Color(0xFF030F27);

//   @override
//   void initState() {
//     super.initState();
//     fetchTargetProfile();
//   }

//   Future<void> fetchTargetProfile() async {
//     setState(() {
//       isLoading = true;
//       errorMessage = null;
//     });

//     try {
//       final data = await ApiService.getTargetProfile(widget.username);
//       setState(() {
//         targetUser = data;
//         isLoading = false;
//       });
//     } catch (e) {
//       setState(() {
//         errorMessage = e.toString();
//         isLoading = false;
//       });
//     }
//   }

//   Future<void> handleFollow() async {
//     if (targetUser == null || isActionLoading) return;

//     setState(() => isActionLoading = true);

//     try {
//       final result = await ApiService.followUser(targetUser!.targetUserId);
//       await fetchTargetProfile();

//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text(result['message']),
//             backgroundColor: Colors.green,
//             duration: const Duration(seconds: 2),
//           ),
//         );
//       }
//     } catch (e) {
//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text('Error: ${e.toString()}'),
//             backgroundColor: Colors.red,
//           ),
//         );
//       }
//     } finally {
//       if (mounted) setState(() => isActionLoading = false);
//     }
//   }

//   Future<void> handleAcceptRequest() async {
//     if (targetUser?.theirFollowId == null || isActionLoading) return;

//     setState(() => isActionLoading = true);

//     try {
//       final result = await ApiService.acceptFollowRequest(targetUser!.theirFollowId!);
//       await fetchTargetProfile();

//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text(result['message']),
//             backgroundColor: Colors.green,
//           ),
//         );
//       }
//     } catch (e) {
//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text('Error: ${e.toString()}'),
//             backgroundColor: Colors.red,
//           ),
//         );
//       }
//     } finally {
//       if (mounted) setState(() => isActionLoading = false);
//     }
//   }

//   Future<void> handleRejectRequest() async {
//     if (targetUser?.theirFollowId == null || isActionLoading) return;

//     setState(() => isActionLoading = true);

//     try {
//       final result = await ApiService.rejectFollowRequest(targetUser!.theirFollowId!);
//       await fetchTargetProfile();

//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text(result['message']),
//             backgroundColor: Colors.orange,
//           ),
//         );
//       }
//     } catch (e) {
//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(
//             content: Text('Error: ${e.toString()}'),
//             backgroundColor: Colors.red,
//           ),
//         );
//       }
//     } finally {
//       if (mounted) setState(() => isActionLoading = false);
//     }
//   }

//   Widget _buildFollowButton() {
//     if (targetUser == null) return const SizedBox.shrink();

//     // Apni profile pe button mat dikhao
//     if (targetUser!.myId == targetUser!.targetUserId) {
//       return const SizedBox.shrink();
//     }

//     final myStatus = targetUser!.myFollowStatus;
//     final theirStatus = targetUser!.theirFollowStatus;

//     // Case 1: Usne mujhe request bheji hai PENDING
//     if (theirStatus == 'PENDING') {
//       return Column(
//         children: [
//           Row(
//             children: [
//               Expanded(
//                 child: ElevatedButton(
//                   onPressed: isActionLoading? null : handleAcceptRequest,
//                   style: ElevatedButton.styleFrom(
//                     backgroundColor: Colors.blue,
//                     foregroundColor: Colors.white,
//                     padding: const EdgeInsets.symmetric(vertical: 12),
//                     shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
//                   ),
//                   child: isActionLoading
//                      ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
//                       : const Text('Confirm', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
//                 ),
//               ),
//               const SizedBox(width: 8),
//               Expanded(
//                 child: OutlinedButton(
//                   onPressed: isActionLoading? null : handleRejectRequest,
//                   style: OutlinedButton.styleFrom(
//                     padding: const EdgeInsets.symmetric(vertical: 12),
//                     shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
//                     side: BorderSide(color: Colors.grey[400]!),
//                   ),
//                   child: const Text('Delete', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
//                 ),
//               ),
//             ],
//           ),
//           const SizedBox(height: 12),
//           _buildMainFollowButton(),
//         ],
//       );
//     }

//     return _buildMainFollowButton();
//   }

//   Widget _buildMainFollowButton() {
//     final myStatus = targetUser!.myFollowStatus;
//     final theirStatus = targetUser!.theirFollowStatus;

//     String buttonText = 'Follow';
//     Color buttonColor = bgColor;
//     Color textColor = Colors.white;
//     bool isOutlined = false;

//     if (myStatus == 'PENDING') {
//       buttonText = 'Requested';
//       buttonColor = Colors.grey.shade200;
//       textColor = Colors.black87;
//       isOutlined = true;
//     } else if (myStatus == 'ACCEPTED') {
//       buttonText = 'Following';
//       buttonColor = Colors.grey.shade200;
//       textColor = Colors.black87;
//       isOutlined = true;
//     } else if (myStatus == null && theirStatus == 'ACCEPTED') {
//       buttonText = 'Follow Back';
//       buttonColor = bgColor;
//       textColor = Colors.white;
//     }

//     return SizedBox(
//       width: double.infinity,
//       height: 45,
//       child: isOutlined
//          ? OutlinedButton(
//               onPressed: isActionLoading? null : handleFollow,
//               style: OutlinedButton.styleFrom(
//                 backgroundColor: Colors.grey.shade200,
//                 elevation: 0,
//                 shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
//                 side: BorderSide.none,
//               ),
//               child: isActionLoading
//                  ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2))
//                   : Text(buttonText, style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: textColor)),
//             )
//           : ElevatedButton(
//               onPressed: isActionLoading? null : handleFollow,
//               style: ElevatedButton.styleFrom(
//                 backgroundColor: buttonColor,
//                 elevation: 0,
//                 shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
//               ),
//               child: isActionLoading
//                  ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
//                   : Text(buttonText, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white)),
//             ),
//     );
//   }

//   @override
//   Widget build(BuildContext context) {
//     return Scaffold(
//       backgroundColor: Colors.white,
//       body: isLoading
//          ? const Center(child: CircularProgressIndicator(color: bgColor))
//           : errorMessage!= null
//              ? Center(
//                   child: Padding(
//                     padding: const EdgeInsets.all(16.0),
//                     child: Column(
//                       mainAxisAlignment: MainAxisAlignment.center,
//                       children: [
//                         const Icon(Icons.error_outline, color: Colors.red, size: 60),
//                         const SizedBox(height: 16),
//                         Text(
//                           errorMessage!,
//                           textAlign: TextAlign.center,
//                           style: const TextStyle(color: Colors.red, fontSize: 16),
//                         ),
//                         const SizedBox(height: 16),
//                         ElevatedButton(
//                           onPressed: fetchTargetProfile,
//                           child: const Text("Retry"),
//                         ),
//                       ],
//                     ),
//                   ),
//                 )
//               : RefreshIndicator(
//                   onRefresh: fetchTargetProfile,
//                   color: bgColor,
//                   child: SingleChildScrollView(
//                     physics: const AlwaysScrollableScrollPhysics(),
//                     child: Column(
//                       crossAxisAlignment: CrossAxisAlignment.start,
//                       children: [
//                         /// ================= HEADER =================
//                         Container(
//                           width: double.infinity,
//                           padding: const EdgeInsets.only(
//                             top: 45,
//                             left: 4,
//                             right: 4,
//                             bottom: 10,
//                           ),
//                           color: bgColor,
//                           child: Row(
//                             children: [
//                               // Back button
//                               IconButton(
//                                 icon: const Icon(Icons.arrow_back, color: Colors.white),
//                                 onPressed: () => Navigator.maybePop(context),
//                               ),

//                               Expanded(
//                                 child: Center(
//                                   child: Row(
//                                     mainAxisSize: MainAxisSize.min,
//                                     children: [
//                                       Text(
//                                         targetUser!.username,
//                                         style: const TextStyle(
//                                           color: Colors.white,
//                                           fontSize: 20,
//                                           fontWeight: FontWeight.bold,
//                                         ),
//                                       ),
//                                       if (targetUser!.isVerified)...[
//                                         const SizedBox(width: 4),
//                                         const Icon(Icons.verified, color: Colors.blue, size: 18),
//                                       ]
//                                     ],
//                                   ),
//                                 ),
//                               ),

//                               // More Button
//                               IconButton(
//                                 icon: const Icon(Icons.more_vert, color: Colors.white),
//                                 onPressed: () {
//                                   ScaffoldMessenger.of(context).showSnackBar(
//                                     const SnackBar(content: Text("More Options")),
//                                   );
//                                 },
//                               ),
//                             ],
//                           ),
//                         ),

//                         const SizedBox(height: 20),

//                         /// ================= PROFILE INFO =================
//                         Padding(
//                           padding: const EdgeInsets.symmetric(horizontal: 16),
//                           child: Row(
//                             children: [
//                               /// PROFILE IMAGE
//                               CircleAvatar(
//                                 radius: 45,
//                                 backgroundColor: Colors.grey.shade300,
//                                 child: ClipOval(
//                                   child: targetUser!.profilePhoto.isEmpty
//                                      ? const Icon(Icons.person, size: 45, color: Colors.grey)
//                                       : CachedNetworkImage(
//                                           imageUrl: targetUser!.profilePhoto.startsWith('http')
//                                              ? targetUser!.profilePhoto
//                                               : "${Api.baseUrl}${targetUser!.profilePhoto}",
//                                           width: 90,
//                                           height: 90,
//                                           fit: BoxFit.cover,
//                                           placeholder: (c, u) =>
//                                               const CircularProgressIndicator(strokeWidth: 2),
//                                           errorWidget: (c, u, e) =>
//                                               const Icon(Icons.error, size: 45, color: Colors.grey),
//                                         ),
//                                 ),
//                               ),

//                               const SizedBox(width: 25),

//                               /// NAME, FOLLOWERS & FOLLOWING
//                               Expanded(
//                                 child: Column(
//                                   crossAxisAlignment: CrossAxisAlignment.start,
//                                   children: [
//                                     Row(
//                                       children: [
//                                         Expanded(
//                                           child: Text(
//                                             (targetUser!.firstName.isEmpty && targetUser!.lastName.isEmpty)
//                                                ? "No Name"
//                                                 : "${targetUser!.firstName} ${targetUser!.lastName}".trim(),
//                                             style: const TextStyle(
//                                               fontSize: 22,
//                                               fontWeight: FontWeight.bold,
//                                             ),
//                                           ),
//                                         ),
//                                         if (targetUser!.isPrivate)...[
//                                           const SizedBox(width: 6),
//                                           Icon(Icons.lock, size: 18, color: Colors.grey[600]),
//                                         ],
//                                       ],
//                                     ),
//                                     const SizedBox(height: 8),
//                                     Row(
//                                       children: [
//                                         Column(
//                                           crossAxisAlignment: CrossAxisAlignment.start,
//                                           children: [
//                                             Text(
//                                               "${targetUser!.followers}",
//                                               style: const TextStyle(
//                                                 fontSize: 16,
//                                                 fontWeight: FontWeight.bold,
//                                               ),
//                                             ),
//                                             const Text("Followers", style: TextStyle(color: Colors.grey)),
//                                           ],
//                                         ),
//                                         const SizedBox(width: 30),
//                                         Column(
//                                           crossAxisAlignment: CrossAxisAlignment.start,
//                                           children: [
//                                             Text(
//                                               "${targetUser!.following}",
//                                               style: const TextStyle(
//                                                 fontSize: 16,
//                                                 fontWeight: FontWeight.bold,
//                                               ),
//                                             ),
//                                             const Text("Following", style: TextStyle(color: Colors.grey)),
//                                           ],
//                                         ),
//                                       ],
//                                     ),
//                                   ],
//                                 ),
//                               )
//                             ],
//                           ),
//                         ),

//                         const SizedBox(height: 20),

//                         /// ================= BIO =================
//                         if (targetUser!.bio.isNotEmpty)
//                           Padding(
//                             padding: const EdgeInsets.symmetric(horizontal: 16),
//                             child: Text(
//                               targetUser!.bio,
//                               style: const TextStyle(fontSize: 14, height: 1.4),
//                             ),
//                           ),

//                         const SizedBox(height: 25),

//                         /// ================= ACTION BUTTONS =================
//                         Padding(
//                           padding: const EdgeInsets.symmetric(horizontal: 16),
//                           child: Column(
//                             children: [
//                               // Follow/Confirm/Delete Buttons
//                               _buildFollowButton(),
//                               const SizedBox(height: 12),

//                               // Message + Share Profile Buttons
//                               if (targetUser!.myId!= targetUser!.targetUserId)...[
//                                 Row(
//                                   children: [
//                                     // Message Button
//                                     Expanded(
//                                       child: SizedBox(
//                                         height: 45,
//                                         child: ElevatedButton.icon(
//                                           onPressed: () {
//                                             ScaffoldMessenger.of(context).showSnackBar(
//                                               const SnackBar(content: Text("Message feature coming soon")),
//                                             );
//                                           },
//                                           icon: const Icon(Icons.message, size: 18, color: Colors.black87),
//                                           label: const Text(
//                                             "Message",
//                                             style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.black87),
//                                           ),
//                                           style: ElevatedButton.styleFrom(
//                                             backgroundColor: Colors.grey.shade200,
//                                             elevation: 0,
//                                             shape: RoundedRectangleBorder(
//                                               borderRadius: BorderRadius.circular(8),
//                                             ),
//                                           ),
//                                         ),
//                                       ),
//                                     ),

//                                     const SizedBox(width: 12),

//                                     // Share Profile Button
//                                     Expanded(
//                                       child: SizedBox(
//                                         height: 45,
//                                         child: ElevatedButton.icon(
//                                           onPressed: () {
//                                             Share.share(
//                                               "Check out ${targetUser!.username}'s profile 👇\n"
//                                               "${Api.baseUrl}/profile/${targetUser!.username}",
//                                             );
//                                           },
//                                           icon: const Icon(Icons.share, size: 18, color: Colors.white),
//                                           label: const Text(
//                                             "Share Profile",
//                                             style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white),
//                                           ),
//                                           style: ElevatedButton.styleFrom(
//                                             backgroundColor: bgColor,
//                                             elevation: 0,
//                                             shape: RoundedRectangleBorder(
//                                               borderRadius: BorderRadius.circular(8),
//                                             ),
//                                           ),
//                                         ),
//                                       ),
//                                     ),
//                                   ],
//                                 ),
//                               ],
//                             ],
//                           ),
//                         ),

//                         const SizedBox(height: 20),
//                         const Divider(indent: 16, endIndent: 16),
//                         const SizedBox(height: 10),

//                         /// ================= POSTS COMPONENT =================
//                         if (targetUser!.isPrivate && targetUser!.myFollowStatus!= 'ACCEPTED')...[
//                           Container(
//                             margin: const EdgeInsets.symmetric(horizontal: 16),
//                             padding: const EdgeInsets.all(16),
//                             decoration: BoxDecoration(
//                               color: Colors.grey[100],
//                               borderRadius: BorderRadius.circular(12),
//                               border: Border.all(color: Colors.grey[300]!),
//                             ),
//                             child: Row(
//                               children: [
//                                 Icon(Icons.lock_outline, color: Colors.grey[600]),
//                                 const SizedBox(width: 12),
//                                 Expanded(
//                                   child: Text(
//                                     targetUser!.myFollowStatus == 'PENDING'
//                                        ? 'Follow request sent. Wait for approval to see posts.'
//                                         : 'This account is private. Follow to see their posts.',
//                                     style: TextStyle(color: Colors.grey[700]),
//                                   ),
//                                 ),
//                               ],
//                             ),
//                           ),
//                         ] else...[
//                           Padding(
//                             padding: const EdgeInsets.symmetric(horizontal: 16),
//                             child: Column(
//                               crossAxisAlignment: CrossAxisAlignment.start,
//                               children: [
//                                 Text(
//                                   "${targetUser!.posts}",
//                                   style: const TextStyle(
//                                     fontSize: 22,
//                                     fontWeight: FontWeight.bold,
//                                   ),
//                                 ),
//                                 const Text(
//                                   "Total Posts",
//                                   style: TextStyle(color: Colors.grey, fontSize: 14),
//                                 ),
//                               ],
//                             ),
//                           ),
//                         ],

//                         const SizedBox(height: 40),
//                       ],
//                     ),
//                   ),
//                 ),
//     );
//   }
// }























import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:share_plus/share_plus.dart';
import 'package:video_player/video_player.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:syncfusion_flutter_pdf/pdf.dart';
import 'dart:io';

import '../api_service.dart';
import '../model.dart';
import '../../utils/api.dart';
import '../../post/screens/singlepost.dart';

class TargetProfilePage extends StatefulWidget {
  final String username;

  const TargetProfilePage({Key? key, required this.username}) : super(key: key);

  @override
  State<TargetProfilePage> createState() => _TargetProfilePageState();
}

class _TargetProfilePageState extends State<TargetProfilePage>
    with SingleTickerProviderStateMixin {
  TargetProfileModel? targetUser;
  List<PostModel> targetPosts = [];
  List<PostModel> mediaPosts = [];
  List<PostModel> documentPosts = [];
  bool isLoading = true;
  bool isPostsLoading = true;
  bool isActionLoading = false;
  String? errorMessage;
  String? postsError;
  static const bgColor = Color(0xFF030F27);
  late TabController _tabController;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 2, vsync: this);
    fetchTargetProfile();
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  Future<void> fetchTargetProfile() async {
    setState(() {
      isLoading = true;
      errorMessage = null;
    });

    try {
      final data = await ApiService.getTargetProfile(widget.username);
      setState(() {
        targetUser = data;
        isLoading = false;
      });
      // Profile load hone ke baad posts load karo
      _loadTargetPosts();
    } catch (e) {
      setState(() {
        errorMessage = e.toString();
        isLoading = false;
      });
    }
  }

  Future<void> _loadTargetPosts() async {
    if (targetUser == null) return;

    // Private account check - follow nahi kiya to posts mat load karo
    if (targetUser!.isPrivate && targetUser!.myFollowStatus!= 'ACCEPTED') {
      setState(() {
        isPostsLoading = false;
        postsError = 'PRIVATE_ACCOUNT';
      });
      return;
    }

    setState(() {
      isPostsLoading = true;
      postsError = null;
    });

    try {
      // Yahi pe targetUserId pass kar rahe hain
      final posts = await ApiService.getTargetUserPosts(targetUser!.targetUserId);
      if (mounted) {
        setState(() {
          targetPosts = posts;
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
        setState(() {
          isPostsLoading = false;
          if (e.toString().contains('PRIVATE_ACCOUNT')) {
            postsError = 'PRIVATE_ACCOUNT';
          } else {
            postsError = e.toString();
          }
        });
      }
    }
  }

  Future<void> handleFollow() async {
    if (targetUser == null || isActionLoading) return;

    setState(() => isActionLoading = true);

    try {
      final result = await ApiService.followUser(targetUser!.targetUserId);
      await fetchTargetProfile(); // Refresh karo taaki posts bhi load ho jaye

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(result['message']),
            backgroundColor: Colors.green,
            duration: const Duration(seconds: 2),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error: ${e.toString()}'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => isActionLoading = false);
    }
  }

  Future<void> handleAcceptRequest() async {
    if (targetUser?.theirFollowId == null || isActionLoading) return;

    setState(() => isActionLoading = true);

    try {
      final result = await ApiService.acceptFollowRequest(targetUser!.theirFollowId!);
      await fetchTargetProfile();

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(result['message']),
            backgroundColor: Colors.green,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error: ${e.toString()}'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => isActionLoading = false);
    }
  }

  Future<void> handleRejectRequest() async {
    if (targetUser?.theirFollowId == null || isActionLoading) return;

    setState(() => isActionLoading = true);

    try {
      final result = await ApiService.rejectFollowRequest(targetUser!.theirFollowId!);
      await fetchTargetProfile();

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(result['message']),
            backgroundColor: Colors.orange,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Error: ${e.toString()}'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => isActionLoading = false);
    }
  }

  Future<void> _downloadDocument(String url, String fileName) async {
    try {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Downloading...")),
      );
      await ApiService.downloadFile(url, fileName);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Downloaded: $fileName")),
      );
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Download failed: $e"), backgroundColor: Colors.red),
      );
    }
  }

  void _openSinglePost(String postId) {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => SinglePostPage(postId: postId),
      ),
    );
  }

  Widget _buildFollowButton() {
    if (targetUser == null) return const SizedBox.shrink();

    // Apni profile pe button mat dikhao
    if (targetUser!.myId == targetUser!.targetUserId) {
      return const SizedBox.shrink();
    }

    final myStatus = targetUser!.myFollowStatus;
    final theirStatus = targetUser!.theirFollowStatus;

    // Case 1: Usne mujhe request bheji hai PENDING
    if (theirStatus == 'PENDING') {
      return Column(
        children: [
          Row(
            children: [
              Expanded(
                child: ElevatedButton(
                  onPressed: isActionLoading? null : handleAcceptRequest,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.blue,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                  ),
                  child: isActionLoading
                     ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Text('Confirm', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton(
                  onPressed: isActionLoading? null : handleRejectRequest,
                  style: OutlinedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                    side: BorderSide(color: Colors.grey[400]!),
                  ),
                  child: const Text('Delete', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          _buildMainFollowButton(),
        ],
      );
    }

    return _buildMainFollowButton();
  }

  Widget _buildMainFollowButton() {
    final myStatus = targetUser!.myFollowStatus;
    final theirStatus = targetUser!.theirFollowStatus;

    String buttonText = 'Follow';
    Color buttonColor = bgColor;
    Color textColor = Colors.white;
    bool isOutlined = false;

    if (myStatus == 'PENDING') {
      buttonText = 'Requested';
      buttonColor = Colors.grey.shade200;
      textColor = Colors.black87;
      isOutlined = true;
    } else if (myStatus == 'ACCEPTED') {
      buttonText = 'Following';
      buttonColor = Colors.grey.shade200;
      textColor = Colors.black87;
      isOutlined = true;
    } else if (myStatus == null && theirStatus == 'ACCEPTED') {
      buttonText = 'Follow Back';
      buttonColor = bgColor;
      textColor = Colors.white;
    }

    return SizedBox(
      width: double.infinity,
      height: 45,
      child: isOutlined
         ? OutlinedButton(
              onPressed: isActionLoading? null : handleFollow,
              style: OutlinedButton.styleFrom(
                backgroundColor: Colors.grey.shade200,
                elevation: 0,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                side: BorderSide.none,
              ),
              child: isActionLoading
                 ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2))
                  : Text(buttonText, style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: textColor)),
            )
          : ElevatedButton(
              onPressed: isActionLoading? null : handleFollow,
              style: ElevatedButton.styleFrom(
                backgroundColor: buttonColor,
                elevation: 0,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
              ),
              child: isActionLoading
                 ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : Text(buttonText, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white)),
            ),
    );
  }

  Widget _buildPostsSection() {
    // Private account hai aur follow nahi kiya
    if (targetUser!.isPrivate && targetUser!.myFollowStatus!= 'ACCEPTED') {
      return Container(
        margin: const EdgeInsets.symmetric(horizontal: 16),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Colors.grey[100],
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: Colors.grey[300]!),
        ),
        child: Row(
          children: [
            Icon(Icons.lock_outline, color: Colors.grey[600]),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                targetUser!.myFollowStatus == 'PENDING'
                   ? 'Follow request sent. Wait for approval to see posts.'
                    : 'This account is private. Follow to see their posts.',
                style: TextStyle(color: Colors.grey[700]),
              ),
            ),
          ],
        ),
      );
    }

    // Posts loading ya error
    if (isPostsLoading) {
      return const Center(child: CircularProgressIndicator(color: bgColor));
    }

    if (postsError!= null && postsError!= 'PRIVATE_ACCOUNT') {
      return Center(child: Text("Error loading posts: $postsError"));
    }

    // Posts dikhao
    return Column(
      children: [
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
              // TAB 1: Photos & Videos
              mediaPosts.isEmpty
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

              // TAB 2: Documents
              documentPosts.isEmpty
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
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: isLoading
         ? const Center(child: CircularProgressIndicator(color: bgColor))
          : errorMessage!= null
             ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(16.0),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        const Icon(Icons.error_outline, color: Colors.red, size: 60),
                        const SizedBox(height: 16),
                        Text(
                          errorMessage!,
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: Colors.red, fontSize: 16),
                        ),
                        const SizedBox(height: 16),
                        ElevatedButton(
                          onPressed: fetchTargetProfile,
                          child: const Text("Retry"),
                        ),
                      ],
                    ),
                  ),
                )
              : RefreshIndicator(
                  onRefresh: fetchTargetProfile,
                  color: bgColor,
                  child: SingleChildScrollView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        /// ================= HEADER =================
                        Container(
                          width: double.infinity,
                          padding: const EdgeInsets.only(
                            top: 45,
                            left: 4,
                            right: 4,
                            bottom: 10,
                          ),
                          color: bgColor,
                          child: Row(
                            children: [
                              IconButton(
                                icon: const Icon(Icons.arrow_back, color: Colors.white),
                                onPressed: () => Navigator.maybePop(context),
                              ),
                              Expanded(
                                child: Center(
                                  child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Text(
                                        targetUser!.username,
                                        style: const TextStyle(
                                          color: Colors.white,
                                          fontSize: 20,
                                          fontWeight: FontWeight.bold,
                                        ),
                                      ),
                                      if (targetUser!.isVerified)...[
                                        const SizedBox(width: 4),
                                        const Icon(Icons.verified, color: Colors.blue, size: 18),
                                      ]
                                    ],
                                  ),
                                ),
                              ),
                              IconButton(
                                icon: const Icon(Icons.more_vert, color: Colors.white),
                                onPressed: () {
                                  ScaffoldMessenger.of(context).showSnackBar(
                                    const SnackBar(content: Text("More Options")),
                                  );
                                },
                              ),
                            ],
                          ),
                        ),

                        const SizedBox(height: 20),

                        /// ================= PROFILE INFO =================
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 16),
                          child: Row(
                            children: [
                              CircleAvatar(
                                radius: 45,
                                backgroundColor: Colors.grey.shade300,
                                child: ClipOval(
                                  child: targetUser!.profilePhoto.isEmpty
                                     ? const Icon(Icons.person, size: 45, color: Colors.grey)
                                      : CachedNetworkImage(
                                          imageUrl: targetUser!.profilePhoto.startsWith('http')
                                             ? targetUser!.profilePhoto
                                              : "${Api.baseUrl}${targetUser!.profilePhoto}",
                                          width: 90,
                                          height: 90,
                                          fit: BoxFit.cover,
                                          placeholder: (c, u) =>
                                              const CircularProgressIndicator(strokeWidth: 2),
                                          errorWidget: (c, u, e) =>
                                              const Icon(Icons.error, size: 45, color: Colors.grey),
                                        ),
                                ),
                              ),
                              const SizedBox(width: 25),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Row(
                                      children: [
                                        Expanded(
                                          child: Text(
                                            (targetUser!.firstName.isEmpty && targetUser!.lastName.isEmpty)
                                               ? "No Name"
                                                : "${targetUser!.firstName} ${targetUser!.lastName}".trim(),
                                            style: const TextStyle(
                                              fontSize: 22,
                                              fontWeight: FontWeight.bold,
                                            ),
                                          ),
                                        ),
                                        if (targetUser!.isPrivate)...[
                                          const SizedBox(width: 6),
                                          Icon(Icons.lock, size: 18, color: Colors.grey[600]),
                                        ],
                                      ],
                                    ),
                                    const SizedBox(height: 8),
                                    Row(
                                      children: [
                                        Column(
                                          crossAxisAlignment: CrossAxisAlignment.start,
                                          children: [
                                            Text(
                                              "${targetUser!.followers}",
                                              style: const TextStyle(
                                                fontSize: 16,
                                                fontWeight: FontWeight.bold,
                                              ),
                                            ),
                                            const Text("Followers", style: TextStyle(color: Colors.grey)),
                                          ],
                                        ),
                                        const SizedBox(width: 30),
                                        Column(
                                          crossAxisAlignment: CrossAxisAlignment.start,
                                          children: [
                                            Text(
                                              "${targetUser!.following}",
                                              style: const TextStyle(
                                                fontSize: 16,
                                                fontWeight: FontWeight.bold,
                                              ),
                                            ),
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

                        /// ================= BIO =================
                        if (targetUser!.bio.isNotEmpty)
                          Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 16),
                            child: Text(
                              targetUser!.bio,
                              style: const TextStyle(fontSize: 14, height: 1.4),
                            ),
                          ),

                        const SizedBox(height: 25),

                        /// ================= ACTION BUTTONS =================
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 16),
                          child: Column(
                            children: [
                              _buildFollowButton(),
                              const SizedBox(height: 12),
                              if (targetUser!.myId!= targetUser!.targetUserId)...[
                                Row(
                                  children: [
                                    Expanded(
                                      child: SizedBox(
                                        height: 45,
                                        child: ElevatedButton.icon(
                                          onPressed: () {
                                            ScaffoldMessenger.of(context).showSnackBar(
                                              const SnackBar(content: Text("Message feature coming soon")),
                                            );
                                          },
                                          icon: const Icon(Icons.message, size: 18, color: Colors.black87),
                                          label: const Text(
                                            "Message",
                                            style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.black87),
                                          ),
                                          style: ElevatedButton.styleFrom(
                                            backgroundColor: Colors.grey.shade200,
                                            elevation: 0,
                                            shape: RoundedRectangleBorder(
                                              borderRadius: BorderRadius.circular(8),
                                            ),
                                          ),
                                        ),
                                      ),
                                    ),
                                    const SizedBox(width: 12),
                                    Expanded(
                                      child: SizedBox(
                                        height: 45,
                                        child: ElevatedButton.icon(
                                          onPressed: () {
                                            Share.share(
                                              "Check out ${targetUser!.username}'s profile 👇\n"
                                              "${Api.baseUrl}/profile/${targetUser!.username}",
                                            );
                                          },
                                          icon: const Icon(Icons.share, size: 18, color: Colors.white),
                                          label: const Text(
                                            "Share Profile",
                                            style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white),
                                          ),
                                          style: ElevatedButton.styleFrom(
                                            backgroundColor: bgColor,
                                            elevation: 0,
                                            shape: RoundedRectangleBorder(
                                              borderRadius: BorderRadius.circular(8),
                                            ),
                                          ),
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                              ],
                            ],
                          ),
                        ),

                        const SizedBox(height: 20),

                        /// ================= POSTS SECTION =================
                        _buildPostsSection(),

                        const SizedBox(height: 40),
                      ],
                    ),
                  ),
                ),
    );
  }
}

// VideoFirstFrame aur DocumentGridTile classes - profile.dart se copy kar lo
// Ye same rahenge

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