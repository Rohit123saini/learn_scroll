
// import 'dart:io';
// import 'package:flutter/material.dart';
// import 'package:image_picker/image_picker.dart';
// import '../api_service.dart'; // Ye sahi hai same folder me
// // import './api_service.dart';
// class NewPost extends StatefulWidget { // CreatePostScreen se NewPost kar de
//   const NewPost({super.key});

//   @override
//   State<NewPost> createState() => _NewPostState();
// }

// class _NewPostState extends State<NewPost> { // _CreatePostScreenState se _NewPostState
//   final _formKey = GlobalKey<FormState>();
//   final _titleController = TextEditingController();
//   final _contentController = TextEditingController();
//   final _apiService = ApiService();
//   final _picker = ImagePicker();

//   bool _isLoading = false;
//   String _category = 'general';
//   String _postType = 'text';
//   String _visibility = 'public';
//   List<File> _selectedMedia = [];
//   List<String> _selectedMediaTypes = [];

//   final List<String> _categories = [
//     'general', 'tech', 'jobs', 'news', 'education', 
//     'business', 'entertainment', 'sports', 'lifestyle', 'other'
//   ];

//   final List<String> _visibilities = ['public', 'connections', 'private'];

//   @override
//   void dispose() {
//     _titleController.dispose();
//     _contentController.dispose();
//     super.dispose();
//   }

//   Future<void> _pickImage() async {
//     try {
//       final XFile? image = await _picker.pickImage(source: ImageSource.gallery);
//       if (image!= null) {
//         setState(() {
//           _selectedMedia.add(File(image.path));
//           _selectedMediaTypes.add('image');
//           if (_postType == 'text') _postType = 'image';
//         });
//       }
//     } catch (e) {
//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(content: Text('Error picking image: $e')),
//         );
//       }
//     }
//   }

//   Future<void> _pickVideo() async {
//     try {
//       final XFile? video = await _picker.pickVideo(source: ImageSource.gallery);
//       if (video!= null) {
//         setState(() {
//           _selectedMedia.add(File(video.path));
//           _selectedMediaTypes.add('video');
//           _postType = 'video';
//         });
//       }
//     } catch (e) {
//       if (mounted) {
//         ScaffoldMessenger.of(context).showSnackBar(
//           SnackBar(content: Text('Error picking video: $e')),
//         );
//       }
//     }
//   }

//   void _removeMedia(int index) {
//     setState(() {
//       _selectedMedia.removeAt(index);
//       _selectedMediaTypes.removeAt(index);
//       if (_selectedMedia.isEmpty && _postType!= 'text') {
//         _postType = 'text';
//       }
//     });
//   }

//   Future<void> _createPost() async {
//     if (!_formKey.currentState!.validate()) return;
//     if (_contentController.text.trim().isEmpty && _selectedMedia.isEmpty) {
//       ScaffoldMessenger.of(context).showSnackBar(
//         const SnackBar(content: Text('Content or media is required')),
//       );
//       return;
//     }

//     setState(() => _isLoading = true);

//     try {
//       final hashtagRegex = RegExp(r'#(\w+)');
//       final hashtags = hashtagRegex
//         .allMatches(_contentController.text)
//         .map((m) => m.group(1)!)
//         .toList();

//       final result = await _apiService.createPost(
//         title: _titleController.text.trim(),
//         content: _contentController.text.trim(),
//         category: _category,
//         postType: _postType,
//         visibility: _visibility,
//         hashtags: hashtags,
//         mediaFiles: _selectedMedia.isEmpty? null : _selectedMedia,
//         mediaTypes: _selectedMediaTypes.isEmpty? null : _selectedMediaTypes,
//       );

//       if (!mounted) return;
      
//       Navigator.pop(context, true);
//       ScaffoldMessenger.of(context).showSnackBar(
//         SnackBar(
//           content: Text(result['message']?? 'Post created successfully!'),
//           backgroundColor: Colors.green,
//         ),
//       );
//     } catch (e) {
//       if (!mounted) return;
//       setState(() => _isLoading = false);
//       ScaffoldMessenger.of(context).showSnackBar(
//         SnackBar(
//           content: Text(e.toString().replaceAll('Exception: ', '')),
//           backgroundColor: Colors.red,
//         ),
//       );
//     }
//   }

//   @override
//   Widget build(BuildContext context) {
//     return Scaffold(
//       backgroundColor: Colors.white,
//       appBar: AppBar(
//         backgroundColor: const Color(0xFF030F27),
//         elevation: 0,
//         leading: IconButton(
//           icon: const Icon(Icons.close, color: Colors.white),
//           onPressed: () => Navigator.pop(context),
//         ),
//         title: const Text(
//           'Create Post',
//           style: TextStyle(color: Colors.white, fontWeight: FontWeight.w600),
//         ),
//         actions: [
//           TextButton(
//             onPressed: _isLoading? null : _createPost,
//             child: _isLoading
//               ? const SizedBox(
//                     width: 20,
//                     height: 20,
//                     child: CircularProgressIndicator(
//                       strokeWidth: 2,
//                       color: Colors.white,
//                     ),
//                   )
//                 : const Text(
//                     'Post',
//                     style: TextStyle(
//                       color: Colors.white,
//                       fontSize: 16,
//                       fontWeight: FontWeight.bold,
//                     ),
//                   ),
//           ),
//           const SizedBox(width: 8),
//         ],
//       ),
//       body: Form(
//         key: _formKey,
//         child: SingleChildScrollView(
//           padding: const EdgeInsets.all(16),
//           child: Column(
//             crossAxisAlignment: CrossAxisAlignment.start,
//             children: [
//               TextFormField(
//                 controller: _titleController,
//                 decoration: InputDecoration(
//                   hintText: 'Title (optional)',
//                   hintStyle: TextStyle(color: Colors.grey.shade400),
//                   border: OutlineInputBorder(
//                     borderRadius: BorderRadius.circular(12),
//                     borderSide: BorderSide(color: Colors.grey.shade300),
//                   ),
//                   focusedBorder: OutlineInputBorder(
//                     borderRadius: BorderRadius.circular(12),
//                     borderSide: const BorderSide(color: Color(0xFF6366F1)),
//                   ),
//                 ),
//               ),
//               const SizedBox(height: 16),
//               TextFormField(
//                 controller: _contentController,
//                 maxLines: 8,
//                 decoration: InputDecoration(
//                   hintText: "What's on your mind? #hashtags",
//                   hintStyle: TextStyle(color: Colors.grey.shade400),
//                   border: OutlineInputBorder(
//                     borderRadius: BorderRadius.circular(12),
//                     borderSide: BorderSide(color: Colors.grey.shade300),
//                   ),
//                   focusedBorder: OutlineInputBorder(
//                     borderRadius: BorderRadius.circular(12),
//                     borderSide: const BorderSide(color: Color(0xFF6366F1)),
//                   ),
//                 ),
//                 validator: (val) {
//                   if ((val == null || val.trim().isEmpty) && _selectedMedia.isEmpty) {
//                     return 'Content or media is required';
//                   }
//                   return null;
//                 },
//               ),
//               const SizedBox(height: 16),
//               if (_selectedMedia.isNotEmpty)...[
//                 SizedBox(
//                   height: 100,
//                   child: ListView.builder(
//                     scrollDirection: Axis.horizontal,
//                     itemCount: _selectedMedia.length,
//                     itemBuilder: (context, index) {
//                       return Stack(
//                         children: [
//                           Container(
//                             margin: const EdgeInsets.only(right: 8),
//                             width: 100,
//                             decoration: BoxDecoration(
//                               borderRadius: BorderRadius.circular(12),
//                               image: _selectedMediaTypes[index] == 'image'
//                                 ? DecorationImage(
//                                       image: FileImage(_selectedMedia[index]),
//                                       fit: BoxFit.cover,
//                                     )
//                                   : null,
//                               color: Colors.grey.shade200,
//                             ),
//                             child: _selectedMediaTypes[index] == 'video'
//                               ? const Icon(Icons.videocam, size: 40, color: Colors.grey)
//                                 : null,
//                           ),
//                           Positioned(
//                             top: 4,
//                             right: 12,
//                             child: GestureDetector(
//                               onTap: () => _removeMedia(index),
//                               child: Container(
//                                 padding: const EdgeInsets.all(4),
//                                 decoration: const BoxDecoration(
//                                   color: Colors.black54,
//                                   shape: BoxShape.circle,
//                                 ),
//                                 child: const Icon(Icons.close, size: 16, color: Colors.white),
//                               ),
//                             ),
//                           ),
//                         ],
//                       );
//                     },
//                   ),
//                 ),
//                 const SizedBox(height: 16),
//               ],
//               Row(
//                 children: [
//                   Expanded(
//                     child: OutlinedButton.icon(
//                       onPressed: _pickImage,
//                       icon: const Icon(Icons.photo),
//                       label: const Text('Photo'),
//                       style: OutlinedButton.styleFrom(
//                         padding: const EdgeInsets.symmetric(vertical: 12),
//                         shape: RoundedRectangleBorder(
//                           borderRadius: BorderRadius.circular(12),
//                         ),
//                       ),
//                     ),
//                   ),
//                   const SizedBox(width: 12),
//                   Expanded(
//                     child: OutlinedButton.icon(
//                       onPressed: _pickVideo,
//                       icon: const Icon(Icons.videocam),
//                       label: const Text('Video'),
//                       style: OutlinedButton.styleFrom(
//                         padding: const EdgeInsets.symmetric(vertical: 12),
//                         shape: RoundedRectangleBorder(
//                           borderRadius: BorderRadius.circular(12),
//                         ),
//                       ),
//                     ),
//                   ),
//                 ],
//               ),
//               const SizedBox(height: 24),
//               DropdownButtonFormField<String>(
//                 value: _category,
//                 decoration: InputDecoration(
//                   labelText: 'Category',
//                   border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
//                 ),
//                 items: _categories.map((cat) {
//                   return DropdownMenuItem(
//                     value: cat,
//                     child: Text(cat.toUpperCase()),
//                   );
//                 }).toList(),
//                 onChanged: (val) => setState(() => _category = val!),
//               ),
//               const SizedBox(height: 16),
//               DropdownButtonFormField<String>(
//                 value: _visibility,
//                 decoration: InputDecoration(
//                   labelText: 'Visibility',
//                   border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
//                 ),
//                 items: _visibilities.map((vis) {
//                   return DropdownMenuItem(
//                     value: vis,
//                     child: Text(vis.toUpperCase()),
//                   );
//                 }).toList(),
//                 onChanged: (val) => setState(() => _visibility = val!),
//               ),
//             ],
//           ),
//         ),
//       ),
//     );
//   }
// }






















import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:file_selector/file_selector.dart'; // 👈 file_selector import
import '../api_service.dart';

class NewPost extends StatefulWidget {
  const NewPost({super.key});

  @override
  State<NewPost> createState() => _NewPostState();
}

class _NewPostState extends State<NewPost> {
  final _formKey = GlobalKey<FormState>();
  final _titleController = TextEditingController();
  final _contentController = TextEditingController();
  final _apiService = ApiService();
  final _picker = ImagePicker();

  bool _isLoading = false;
  String _category = 'general';
  String _postType = 'text';
  String _visibility = 'public';
  List<File> _selectedMedia = [];
  List<String> _selectedMediaTypes = [];

  final List<String> _categories = [
    'general', 'tech', 'jobs', 'news', 'education',
    'business', 'entertainment', 'sports', 'lifestyle', 'other'
  ];

  final List<String> _visibilities = ['public', 'connections', 'private'];

  @override
  void dispose() {
    _titleController.dispose();
    _contentController.dispose();
    super.dispose();
  }

  // 👇 Bottom sheet - User se puchega kya lena hai
  void _showMediaPickerDialog() {
    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => Container(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: Colors.grey.shade300,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const SizedBox(height: 20),
            const Text(
              'Add Media',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 20),
            ListTile(
              leading: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: const Color(0xFF6366F1).withOpacity(0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Icon(Icons.photo_library, color: Color(0xFF6366F1)),
              ),
              title: const Text('Images'),
              subtitle: const Text('Select multiple images'),
              trailing: const Icon(Icons.arrow_forward_ios, size: 16),
              onTap: () {
                Navigator.pop(context);
                _pickImages();
              },
            ),
            ListTile(
              leading: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: const Color(0xFF6366F1).withOpacity(0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Icon(Icons.video_library, color: Color(0xFF6366F1)),
              ),
              title: const Text('Videos'),
              subtitle: const Text('Select multiple videos'),
              trailing: const Icon(Icons.arrow_forward_ios, size: 16),
              onTap: () {
                Navigator.pop(context);
                _pickVideos();
              },
            ),
            ListTile(
              leading: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: const Color(0xFF6366F1).withOpacity(0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Icon(Icons.insert_drive_file, color: Color(0xFF6366F1)),
              ),
              title: const Text('Documents'),
              subtitle: const Text('PDF, DOC, XLS, etc.'),
              trailing: const Icon(Icons.arrow_forward_ios, size: 16),
              onTap: () {
                Navigator.pop(context);
                _pickDocuments();
              },
            ),
            const SizedBox(height: 10),
          ],
        ),
      ),
    );
  }

  // Multiple Images - file_selector se
  Future<void> _pickImages() async {
    try {
      const XTypeGroup typeGroup = XTypeGroup(
        label: 'images',
        extensions: <String>['jpg', 'jpeg', 'png', 'gif', 'webp'],
      );
      final List<XFile> files = await openFiles(acceptedTypeGroups: <XTypeGroup>[typeGroup]);

      if (files.isNotEmpty) {
        setState(() {
          for (var xfile in files) {
            _selectedMedia.add(File(xfile.path));
            _selectedMediaTypes.add('image');
          }
          if (_postType == 'text') _postType = 'image';
        });
      }
    } catch (e) {
      _showError('Error picking images: $e');
    }
  }

  // Multiple Videos - file_selector se
  Future<void> _pickVideos() async {
    try {
      const XTypeGroup typeGroup = XTypeGroup(
        label: 'videos',
        extensions: <String>['mp4', 'mov', 'avi', 'mkv', 'webm'],
      );
      final List<XFile> files = await openFiles(acceptedTypeGroups: <XTypeGroup>[typeGroup]);

      if (files.isNotEmpty) {
        setState(() {
          for (var xfile in files) {
            _selectedMedia.add(File(xfile.path));
            _selectedMediaTypes.add('video');
          }
          _postType = 'video';
        });
      }
    } catch (e) {
      _showError('Error picking videos: $e');
    }
  }

  // Multiple Documents - file_selector se
  Future<void> _pickDocuments() async {
    try {
      const XTypeGroup typeGroup = XTypeGroup(
        label: 'documents',
        extensions: <String>['pdf', 'doc', 'docx', 'txt', 'xls', 'xlsx', 'ppt', 'pptx', 'zip'],
      );
      final List<XFile> files = await openFiles(acceptedTypeGroups: <XTypeGroup>[typeGroup]);

      if (files.isNotEmpty) {
        setState(() {
          for (var xfile in files) {
            _selectedMedia.add(File(xfile.path));
            _selectedMediaTypes.add('document');
          }
          _postType = 'document';
        });
      }
    } catch (e) {
      _showError('Error picking documents: $e');
    }
  }

  void _removeMedia(int index) {
    setState(() {
      _selectedMedia.removeAt(index);
      _selectedMediaTypes.removeAt(index);
      if (_selectedMedia.isEmpty) {
        _postType = 'text';
      }
    });
  }

  void _showError(String msg) {
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(msg), backgroundColor: Colors.red),
      );
    }
  }

  Future<void> _createPost() async {
    if (!_formKey.currentState!.validate()) return;
    if (_contentController.text.trim().isEmpty && _selectedMedia.isEmpty) {
      _showError('Content or media is required');
      return;
    }

    setState(() => _isLoading = true);

    try {
      final hashtagRegex = RegExp(r'#(\w+)');
      final hashtags = hashtagRegex
      .allMatches(_contentController.text)
      .map((m) => m.group(1)!)
      .toList();

      final result = await _apiService.createPost(
        title: _titleController.text.trim(),
        content: _contentController.text.trim(),
        category: _category,
        postType: _postType,
        visibility: _visibility,
        hashtags: hashtags,
        mediaFiles: _selectedMedia.isEmpty? null : _selectedMedia,
        mediaTypes: _selectedMediaTypes.isEmpty? null : _selectedMediaTypes,
      );

      if (!mounted) return;

      Navigator.pop(context, true);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(result['message']?? 'Post created successfully!'),
          backgroundColor: Colors.green,
        ),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _isLoading = false);
      _showError(e.toString().replaceAll('Exception: ', ''));
    }
  }

  String _getFileSize(File file) {
    try {
      int bytes = file.lengthSync();
      if (bytes < 1024) return '$bytes B';
      if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
      return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
    } catch (e) {
      return '';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: const Color(0xFF030F27),
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.close, color: Colors.white),
          onPressed: () => Navigator.pop(context),
        ),
        title: const Text(
          'Create Post',
          style: TextStyle(color: Colors.white, fontWeight: FontWeight.w600),
        ),
        actions: [
          TextButton(
            onPressed: _isLoading? null : _createPost,
            child: _isLoading
            ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                : const Text(
                    'Post',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: Form(
        key: _formKey,
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              TextFormField(
                controller: _titleController,
                decoration: InputDecoration(
                  hintText: 'Title (optional)',
                  hintStyle: TextStyle(color: Colors.grey.shade400),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: BorderSide(color: Colors.grey.shade300),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: const BorderSide(color: Color(0xFF6366F1)),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _contentController,
                maxLines: 8,
                decoration: InputDecoration(
                  hintText: "What's on your mind? #hashtags",
                  hintStyle: TextStyle(color: Colors.grey.shade400),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: BorderSide(color: Colors.grey.shade300),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: const BorderSide(color: Color(0xFF6366F1)),
                  ),
                ),
                validator: (val) {
                  if ((val == null || val.trim().isEmpty) && _selectedMedia.isEmpty) {
                    return 'Content or media is required';
                  }
                  return null;
                },
              ),
              const SizedBox(height: 16),

              // Selected files preview
              if (_selectedMedia.isNotEmpty)...[
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      '${_selectedMedia.length} files selected',
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    TextButton(
                      onPressed: () {
                        setState(() {
                          _selectedMedia.clear();
                          _selectedMediaTypes.clear();
                          _postType = 'text';
                        });
                      },
                      child: const Text('Clear All'),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                SizedBox(
                  height: 120,
                  child: ListView.builder(
                    scrollDirection: Axis.horizontal,
                    itemCount: _selectedMedia.length,
                    itemBuilder: (context, index) {
                      final file = _selectedMedia[index];
                      final type = _selectedMediaTypes[index];
                      final fileName = file.path.split('/').last;

                      return Stack(
                        children: [
                          Container(
                            margin: const EdgeInsets.only(right: 8),
                            width: 100,
                            decoration: BoxDecoration(
                              borderRadius: BorderRadius.circular(12),
                              color: Colors.grey.shade100,
                              border: Border.all(color: Colors.grey.shade300),
                              image: type == 'image'
                              ? DecorationImage(
                                      image: FileImage(file),
                                      fit: BoxFit.cover,
                                    )
                                  : null,
                            ),
                            child: type!= 'image'
                            ? Column(
                                    mainAxisAlignment: MainAxisAlignment.center,
                                    children: [
                                      Icon(
                                        type == 'video'? Icons.videocam : Icons.insert_drive_file,
                                        size: 36,
                                        color: const Color(0xFF6366F1),
                                      ),
                                      const SizedBox(height: 6),
                                      Padding(
                                        padding: const EdgeInsets.symmetric(horizontal: 4),
                                        child: Text(
                                          fileName,
                                          style: const TextStyle(fontSize: 10),
                                          maxLines: 2,
                                          overflow: TextOverflow.ellipsis,
                                          textAlign: TextAlign.center,
                                        ),
                                      ),
                                      const SizedBox(height: 2),
                                      Text(
                                        _getFileSize(file),
                                        style: TextStyle(
                                          fontSize: 9,
                                          color: Colors.grey.shade600,
                                        ),
                                      ),
                                    ],
                                  )
                                : null,
                          ),
                          Positioned(
                            top: 4,
                            right: 12,
                            child: GestureDetector(
                              onTap: () => _removeMedia(index),
                              child: Container(
                                padding: const EdgeInsets.all(4),
                                decoration: const BoxDecoration(
                                  color: Colors.black87,
                                  shape: BoxShape.circle,
                                ),
                                child: const Icon(Icons.close, size: 14, color: Colors.white),
                              ),
                            ),
                          ),
                        ],
                      );
                    },
                  ),
                ),
                const SizedBox(height: 16),
              ],

              // Add Media Button
              OutlinedButton.icon(
                onPressed: _showMediaPickerDialog,
                icon: const Icon(Icons.add_photo_alternate),
                label: Text(_selectedMedia.isEmpty? 'Add Media' : 'Add More Media'),
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size(double.infinity, 48),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                  side: const BorderSide(color: Color(0xFF6366F1)),
                ),
              ),

              const SizedBox(height: 24),
              DropdownButtonFormField<String>(
                value: _category,
                decoration: InputDecoration(
                  labelText: 'Category',
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                ),
                items: _categories.map((cat) {
                  return DropdownMenuItem(
                    value: cat,
                    child: Text(cat.toUpperCase()),
                  );
                }).toList(),
                onChanged: (val) => setState(() => _category = val!),
              ),
              const SizedBox(height: 16),
              DropdownButtonFormField<String>(
                value: _visibility,
                decoration: InputDecoration(
                  labelText: 'Visibility',
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                ),
                items: _visibilities.map((vis) {
                  return DropdownMenuItem(
                    value: vis,
                    child: Text(vis.toUpperCase()),
                  );
                }).toList(),
                onChanged: (val) => setState(() => _visibility = val!),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
