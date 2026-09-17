// lib/post/widgets/comment_sheet.dart
//
// TASK 2 — extracted from home.dart, where CommentBottomSheet/CommentTile/
// _CommentImageFullScreen/_CommentVideoFullScreen were already fully built
// and wired to the feed's comment icon (`_openCommentSheet`) — exactly the
// "may already be 80% built, just not hooked up to singlepost.dart" case
// the task description called out. Nothing about their internal logic
// changed except:
//
//   1. CommentBottomSheet no longer requires a `PostModel post` — it now
//      takes `postId` / `postOwnerId` / `initialCommentsCount` directly.
//      singlepost.dart only has a raw `Map<String, dynamic>? post` from
//      `ApiService().getPostById()`, not a PostModel, and building a whole
//      PostModel there just to satisfy this widget's old signature would
//      have meant touching singlepost.dart's unrelated media/rendering
//      code for no reason. CommentTile was already decoupled (it only
//      ever took a plain `postOwnerId` String), so it needed no change.
//      home.dart's call site (`_openCommentSheet`) is updated to pass
//      `post.id` / `post.user.id.toString()` / `post.commentsCount`
//      instead of the whole model — same values, just unpacked.
//   2. `_commentsCount` is now a local mirror inside the sheet (seeded
//      from `initialCommentsCount`) instead of mutating `widget.post`
//      directly, and — a real gap in the original — it's now also
//      decremented locally when you delete/hide a top-level comment from
//      inside the sheet (before, only `_send()` ever touched the count,
//      so the header number under-reported after a delete/hide until the
//      sheet was reopened). `onCommentAdded` still fires the same way so
//      the feed/post-page badge count stays in sync either way.
//   3. Upload progress now actually reflects reality for the common case.
//      `CommentService.createComment`'s `onProgress` callback used to only
//      ever fire for the rare >20MB chunked-upload path — a text-only
//      comment or a normal photo/small-file comment (the vast majority)
//      silently stayed at 0% / "Compressing..." the whole time even
//      though the request was genuinely in flight. See comment_service.dart
//      for the fix — this file didn't need to change to benefit from it,
//      since it already passes `onProgress` through; it just wasn't being
//      called until now.
//
// Shared by home.dart's feed (`_openCommentSheet`) and singlepost.dart
// (`_openCommentSheet`) so there's exactly one comment-sheet
// implementation instead of two copies drifting apart over time.

import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:video_player/video_player.dart';
import 'package:image_picker/image_picker.dart';
import 'package:file_selector/file_selector.dart';
import 'package:open_filex/open_filex.dart';
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:wakelock_plus/wakelock_plus.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../../services/comment_service.dart';
import '../../services/auth_service.dart';
import '../../widgets/ls_ui.dart';
import '../../l10n/app_localizations.dart';

// ===================== COMMENT SYSTEM - THREAD SERIES LOGIC =====================
// 🔥 TASK 2 — decoupled from PostModel: takes postId/postOwnerId/
// initialCommentsCount directly instead of a whole PostModel, so this same
// widget works from home.dart's feed (which has a PostModel) AND from
// singlepost.dart (which only has a raw post Map — building a PostModel
// there just to satisfy this widget would've meant touching all of
// singlepost.dart's unrelated rendering code for no reason).
class CommentBottomSheet extends StatefulWidget { final String postId; final String postOwnerId; final int initialCommentsCount; final VoidCallback onCommentAdded; final Function(String) onGoToProfile; const CommentBottomSheet({super.key, required this.postId, required this.postOwnerId, required this.initialCommentsCount, required this.onCommentAdded, required this.onGoToProfile}); @override State<CommentBottomSheet> createState() => _CommentBottomSheetState(); }
class _CommentBottomSheetState extends State<CommentBottomSheet> {
  List<CommentModel> comments = []; bool loading = true; bool isUploading = false; double uploadProgress = 0; final TextEditingController _controller = TextEditingController(); List<File> _selectedFiles = []; String? replyToId; String? replyToName; final ImagePicker _picker = ImagePicker();
  final Map<String, List<CommentModel>> _localReplies = {}; final Map<String, bool> _expandedMap = {};
  // local mirror of the count so the header updates immediately on send,
  // same as `post.commentsCount++` used to do directly on the PostModel.
  late int _commentsCount = widget.initialCommentsCount;
  @override void initState() { super.initState(); _fetchComments(); }
  Future<void> _fetchComments() async { try { final data = await CommentService.getComments(widget.postId); if (mounted) setState(() { comments = data; loading = false; }); } catch (e) { if (mounted) setState(() => loading = false); } }
  bool _checkFileSize(File file) { double sizeMB = file.lengthSync() / (1024*1024); if (sizeMB > 200) { showDialog(context: context, builder: (_) => AlertDialog(title: Row(children: [Icon(Icons.error, color: Colors.red), SizedBox(width: 8), Text("File Too Large")]), content: Text("This file is ${sizeMB.toStringAsFixed(1)}MB, which is much more than 200MB limit.\n\nPlease select a file under 200MB."), actions: [TextButton(onPressed: ()=> Navigator.pop(context), child: Text("OK", style: TextStyle(color: Theme.of(context).colorScheme.primary, fontWeight: FontWeight.bold)))],)); ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Video is much more than 200MB (${sizeMB.toStringAsFixed(1)}MB) - Not allowed"), backgroundColor: Colors.red)); return false; } return true; }
  Future<void> _pickCameraPhoto() async { try { final XFile? photo = await _picker.pickImage(source: ImageSource.camera, imageQuality: 85); if (photo!= null && mounted) { File f = File(photo.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
  Future<void> _pickCameraVideo() async { try { final XFile? v = await _picker.pickVideo(source: ImageSource.camera, maxDuration: Duration(minutes: 5)); if (v!= null && mounted) { File f = File(v.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
  Future<void> _pickGalleryImage() async { try { final XFile? img = await _picker.pickImage(source: ImageSource.gallery, imageQuality: 85); if (img!= null && mounted) { File f = File(img.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
  Future<void> _pickGalleryVideo() async { try { final XFile? v = await _picker.pickVideo(source: ImageSource.gallery); if (v!= null && mounted) { File f = File(v.path); if (_checkFileSize(f)) setState(() => _selectedFiles.add(f)); } } catch (e) {} }
  Future<void> _pickFile() async { try { const XTypeGroup all = XTypeGroup(label: 'all'); final XFile? f = await openFile(acceptedTypeGroups: [all]); if (f!= null && mounted) { File file = File(f.path); if (_checkFileSize(file)) setState(() => _selectedFiles.add(file)); } } catch (e) {} }
  Future<void> _openOptions() async { final cs = Theme.of(context).colorScheme; showModalBottomSheet(context: context, backgroundColor: cs.surface, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))), builder: (c) => SafeArea(child: Wrap(children: [ListTile(leading: const Icon(Icons.camera_alt, color: Colors.blue), title: const Text("Camera Photo"), onTap: () { Navigator.pop(c); _pickCameraPhoto(); }), ListTile(leading: const Icon(Icons.videocam, color: Colors.red), title: const Text("Camera Video"), onTap: () { Navigator.pop(c); _pickCameraVideo(); }), ListTile(leading: const Icon(Icons.photo_library, color: Colors.green), title: const Text("Gallery Photo"), onTap: () { Navigator.pop(c); _pickGalleryImage(); }), ListTile(leading: const Icon(Icons.video_library, color: Colors.purple), title: const Text("Gallery Video"), onTap: () { Navigator.pop(c); _pickGalleryVideo(); }), ListTile(leading: const Icon(Icons.attach_file, color: Colors.orange), title: const Text("Document"), onTap: () { Navigator.pop(c); _pickFile(); }),])),); }

  Future<void> _send() async {
    if (_controller.text.trim().isEmpty && _selectedFiles.isEmpty) return;
    await WakelockPlus.enable(); setState(() { isUploading = true; uploadProgress = 0; }); String? curId = replyToId;
    try {
      final newComment = await CommentService.createComment(postId: widget.postId, parentId: curId, content: _controller.text.trim(), files: _selectedFiles, onProgress: (p) { if (mounted) setState(() => uploadProgress = p); });
      if (mounted) { setState(() { if (curId == null) { comments.insert(0, newComment); _commentsCount++; } else { _localReplies.putIfAbsent(curId, () => []); _localReplies[curId]!.insert(0, newComment); _expandedMap[curId] = true; for (var c in comments) { if (c.id == curId) c.repliesCount++; } } _controller.clear(); _selectedFiles = []; replyToId = null; replyToName = null; isUploading = false; uploadProgress = 0; }); widget.onCommentAdded(); }
    } catch (e) { if (mounted) { setState(() => isUploading = false); ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.failedWithError(e.toString())), backgroundColor: Colors.red)); } } finally { await WakelockPlus.disable(); }
  }
  @override Widget build(BuildContext context) {
    // 🎨 TASK 7.2 — poora comment sheet ab ColorScheme se colors leta hai.
    final cs = Theme.of(context).colorScheme;
    return SafeArea(child: DraggableScrollableSheet(initialChildSize: 0.85, maxChildSize: 0.95, minChildSize: 0.5, expand: false, builder: (context, scrollController) {
      return Column(children: [
        const SizedBox(height: 10), Container(width: 40, height: 4, decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(10))), const SizedBox(height: 10),
        Text("Comments $_commentsCount", style: LsType.head(context, size: 15)), Divider(color: cs.outlineVariant),
        if (isUploading) Padding(padding: const EdgeInsets.all(8), child: Column(children: [LinearProgressIndicator(value: uploadProgress>0? uploadProgress/100 : null, color: cs.primary, backgroundColor: cs.surfaceVariant), const SizedBox(height: 4), Text(uploadProgress>0? '${uploadProgress.toStringAsFixed(1)}% uploading...' : 'Compressing...', style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant))])),
        Expanded(child: loading? const Center(child: CircularProgressIndicator()) : comments.isEmpty? Center(child: Text("No comments yet", style: TextStyle(color: cs.onSurfaceVariant))) : ListView.builder(controller: scrollController, itemCount: comments.length, itemBuilder: (c, i) => CommentTile(key: ValueKey(comments[i].id + comments[i].repliesCount.toString() + (_localReplies[comments[i].id]?.length??0).toString() + (_expandedMap[comments[i].id]?.toString()??'')), comment: comments[i], postOwnerId: widget.postOwnerId, level: 0, localReplies: _localReplies[comments[i].id]??[], allLocalReplies: _localReplies, expandedMap: _expandedMap, onReply: (id, name) => setState(() { replyToId = id; replyToName = name; }), onDeleted: (id) => setState(() { comments.removeWhere((x) => x.id == id); _commentsCount = _commentsCount>0? _commentsCount-1 : 0; }), onEdited: (updated) => setState(() { int idx = comments.indexWhere((x) => x.id == updated.id); if (idx!= -1) comments[idx] = updated; }), onHidden: (id) => setState(() { comments.removeWhere((x) => x.id == id); _commentsCount = _commentsCount>0? _commentsCount-1 : 0; }), onGoToProfile: widget.onGoToProfile))),
        if (replyToName!= null) Container(width: double.infinity, color: cs.primary, padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10), child: Row(children: [Icon(Icons.reply, color: cs.onPrimary.withOpacity(.75), size: 16), const SizedBox(width: 8), Expanded(child: Text("Replying to @$replyToName", style: TextStyle(fontSize: 13, fontWeight: FontWeight.bold, color: cs.onPrimary))), GestureDetector(onTap: () => setState(() { replyToId = null; replyToName = null; }), child: Icon(Icons.close, color: cs.onPrimary, size: 18)) ])),
        if (_selectedFiles.isNotEmpty) Container(height: 90, color: cs.surfaceVariant, child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Padding(padding: const EdgeInsets.all(6), child: Text(AppLocalizations.of(context)!.filesSelectedCount(_selectedFiles.length), style: TextStyle(fontSize: 12, color: cs.primary, fontWeight: FontWeight.bold))), Expanded(child: ListView.builder(scrollDirection: Axis.horizontal, itemCount: _selectedFiles.length, itemBuilder: (c,i){ bool isVideo = _selectedFiles[i].path.toLowerCase().endsWith('.mp4') || _selectedFiles[i].path.toLowerCase().endsWith('.mov'); return Stack(children: [Container(margin: const EdgeInsets.all(6), width: 70, height: 70, decoration: BoxDecoration(borderRadius: BorderRadius.circular(10), color: Colors.black), child: ClipRRect(borderRadius: BorderRadius.circular(10), child: isVideo? const Icon(Icons.videocam, color: Colors.white) : Image.file(_selectedFiles[i], fit: BoxFit.cover))), Positioned(top: 0, right: 0, child: GestureDetector(onTap: ()=>setState(()=>_selectedFiles.removeAt(i)), child: Container(decoration: const BoxDecoration(color: Colors.red, shape: BoxShape.circle), child: const Icon(Icons.close, size: 18, color: Colors.white))))]); })), ])),
        Padding(padding: EdgeInsets.only(left: 10, right: 10, bottom: MediaQuery.of(context).viewPadding.bottom + 10, top: 8), child: Row(children: [IconButton(icon: Icon(Icons.attach_file, color: cs.primary), onPressed: isUploading? null : _openOptions), Expanded(child: TextField(controller: _controller, enabled:!isUploading, style: TextStyle(color: cs.onSurface), decoration: InputDecoration(hintText: "Add comment...", hintStyle: TextStyle(color: cs.onSurfaceVariant), filled: true, fillColor: cs.surfaceVariant, border: OutlineInputBorder(borderRadius: const BorderRadius.all(Radius.circular(25)), borderSide: BorderSide(color: cs.outlineVariant)), enabledBorder: OutlineInputBorder(borderRadius: const BorderRadius.all(Radius.circular(25)), borderSide: BorderSide(color: cs.outlineVariant)), contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10)))), const SizedBox(width: 8), isUploading? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)) : CircleAvatar(backgroundColor: cs.primary, child: IconButton(icon: Icon(Icons.send, color: cs.onPrimary), onPressed: _send))]))
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
  void _showReactionOverlay(BuildContext context) { final cs = Theme.of(context).colorScheme; final RenderBox box = context.findRenderObject() as RenderBox; final Offset pos = box.localToGlobal(Offset.zero); _overlayEntry = OverlayEntry(builder: (c) => Stack(children: [GestureDetector(onTap: ()=> _hideOverlay(), child: Container(color: Colors.transparent, width: double.infinity, height: double.infinity)), Positioned(left: 20, top: pos.dy - 60, child: Material(color: Colors.transparent, child: Container(padding: EdgeInsets.symmetric(horizontal: 8, vertical: 6), decoration: BoxDecoration(color: cs.surface, borderRadius: BorderRadius.circular(30), boxShadow: const [BoxShadow(color: Colors.black26, blurRadius: 10)]), child: Row(children: _emojiMap.entries.map((e){ bool sel = widget.comment.myReaction==e.key; return GestureDetector(onTap: (){ _hideOverlay(); _handleReaction(e.key); }, child: Container(margin: EdgeInsets.symmetric(horizontal: 4), padding: EdgeInsets.all(8), decoration: BoxDecoration(color: sel? _emojiColor[e.key]!.withOpacity(0.15):cs.surfaceVariant, shape: BoxShape.circle, border: sel? Border.all(color: _emojiColor[e.key]!, width: 2):null), child: Text(e.value, style: TextStyle(fontSize: 26)))); }).toList()),),),),])); Overlay.of(context).insert(_overlayEntry!); }
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
  void _showOptions(){ final cs = Theme.of(context).colorScheme; List<Widget> options = []; if(_isMyComment){ options.add(ListTile(leading: Icon(Icons.edit, color: Colors.blue), title: Text("Edit"), onTap: (){ Navigator.pop(context); _editDialog(); })); options.add(ListTile(leading: Icon(Icons.delete, color: Colors.red), title: Text("Delete"), onTap: (){ Navigator.pop(context); _deleteConfirm(); })); } if(_isPostOwner &&!_isMyComment){ options.add(ListTile(leading: Icon(Icons.visibility_off, color: Colors.orange), title: Text("Hide Comment"), onTap: (){ Navigator.pop(context); _hideComment(); })); } if(options.isEmpty) return; showModalBottomSheet(context: context, backgroundColor: cs.surface, shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(12))), builder: (c)=> SafeArea(child: Wrap(children: options))); }
  void _editDialog(){ TextEditingController ctrl = TextEditingController(text: widget.comment.content); showDialog(context: context, builder: (_)=> AlertDialog(title: Text("Edit Comment"), content: TextField(controller: ctrl, maxLines: 4, autofocus: true, decoration: InputDecoration(border: OutlineInputBorder(), hintText: "Edit comment")), actions: [TextButton(onPressed: ()=> Navigator.pop(context), child: Text("Cancel")), ElevatedButton(style: ElevatedButton.styleFrom(backgroundColor: Theme.of(context).colorScheme.primary), onPressed: () async { if(ctrl.text.trim().isEmpty) return; Navigator.pop(context); try{ final updated = await CommentService.editComment(commentId: widget.comment.id, content: ctrl.text.trim()); if(mounted) setState(()=> widget.comment.content = updated.content); widget.comment.isEdited = true; if(widget.onEdited!=null) widget.onEdited!(updated); if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Edited"), backgroundColor: Colors.green)); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Edit failed: $e"), backgroundColor: Colors.red)); } }, child: Text("Save", style: TextStyle(color: Colors.white))) ])); }
  void _deleteConfirm(){ showDialog(context: context, builder: (_)=> AlertDialog(title: Text("Delete?"), content: Text("Delete this comment?"), actions: [TextButton(onPressed: ()=> Navigator.pop(context), child: Text("Cancel")), ElevatedButton(style: ElevatedButton.styleFrom(backgroundColor: Colors.red), onPressed: () async { Navigator.pop(context); try{ await CommentService.deleteComment(widget.comment.id); if(widget.onDeleted!=null) widget.onDeleted!(widget.comment.id); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Delete failed: $e"))); } }, child: Text("Delete", style: TextStyle(color: Colors.white)))])); }
  void _hideComment() async { try{ await CommentService.hideComment(widget.comment.id); if(widget.onHidden!=null) widget.onHidden!(widget.comment.id); if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Comment hidden"), backgroundColor: Colors.orange)); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Hide failed: $e"))); } }
  bool _isImage(String url){ final l=url.toLowerCase(); return l.endsWith('.png')||l.endsWith('.jpg')||l.endsWith('.jpeg')||l.endsWith('.webp')||l.endsWith('.gif'); }
  bool _isVideo(String url,String type){ final l=url.toLowerCase(); return type=='video'||l.endsWith('.mp4')||l.endsWith('.mov')||l.endsWith('.mkv'); }
  Future<void> _openFile(String url,String fileName) async { try{ String? token = await AuthService.getToken(); Directory dir=await getTemporaryDirectory(); String savePath='${dir.path}/${fileName.replaceAll(' ', '_')}'; if(!await File(savePath).exists()){ await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty?{"Authorization":"Bearer $token"}:{})); } await OpenFilex.open(savePath); }catch(e){ if(mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Open failed: $e'))); } }
  void _openMedia(dynamic m){ if(_isImage(m.file)) Navigator.push(context, MaterialPageRoute(builder: (_)=> _CommentImageFullScreen(url: m.file, fileName: m.fileName))); else if(_isVideo(m.file,m.mediaType)) Navigator.push(context, MaterialPageRoute(builder: (_)=> _CommentVideoFullScreen(url: m.file, fileName: m.fileName))); else _openFile(m.file,m.fileName); }
  Widget _buildCommentVideoThumb(){ return Container(height: 90, width: 130, decoration: BoxDecoration(color: Colors.black, borderRadius: BorderRadius.circular(8)), child: Stack(alignment: Alignment.center, children: [Icon(Icons.videocam, color: Colors.white30, size: 30), Container(padding: EdgeInsets.all(4), decoration: BoxDecoration(color: Colors.black54, shape: BoxShape.circle), child: Icon(Icons.play_arrow, color: Colors.white, size: 24))])); }

  @override Widget build(BuildContext context) {
    // 🎨 TASK 7.2 — comment tile ke saare grey/navy literals theme se.
    final cs = Theme.of(context).colorScheme;
    List<MapEntry<String,int>> sorted = widget.comment.reactionCounts.entries.where((e)=> e.value>0 && e.key!='total').toList(); sorted.sort((a,b)=> b.value.compareTo(a.value)); var top3 = sorted.take(3).toList();
    // 🔥 FIX: level 0 = main comment, level 1+ = sab same indent me - ek hi series
    double leftPad = widget.level==0? 12 : 36;
    return Padding(padding: EdgeInsets.only(left: leftPad, right: 12, top: 8, bottom: 8), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      // Thread Line
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        GestureDetector(onTap: ()=> widget.onGoToProfile(widget.comment.user.username), child: CircleAvatar(radius: widget.level==0? 18 : 14, backgroundColor: cs.surfaceVariant, backgroundImage: widget.comment.user.profilePicture!=null && widget.comment.user.profilePicture!.isNotEmpty? CachedNetworkImageProvider(widget.comment.user.profilePicture!, maxWidth: 72) : null)),
        const SizedBox(width: 10),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          GestureDetector(onLongPress: _canShowMenu? ()=> _showOptions() : null, child: Container(padding: EdgeInsets.symmetric(horizontal: 14, vertical: 10), decoration: BoxDecoration(color: cs.surfaceVariant, borderRadius: BorderRadius.circular(16)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [GestureDetector(onTap: ()=> widget.onGoToProfile(widget.comment.user.username), child: Text(widget.comment.user.username, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: cs.onSurface))), if(widget.comment.isEdited) Padding(padding: EdgeInsets.only(left: 6), child: Text("(edited)", style: TextStyle(fontSize: 10, color: cs.onSurfaceVariant))), if(_isMyComment) Padding(padding: EdgeInsets.only(left: 6), child: Text("(you)", style: TextStyle(fontSize: 10, color: cs.primary, fontWeight: FontWeight.bold)))]),
            if(widget.comment.content.isNotEmpty) Padding(padding: EdgeInsets.only(top: 6), child: Text(widget.comment.content, style: TextStyle(fontSize: 14.5, height: 1.4, color: cs.onSurface))),
            if(widget.comment.media.isNotEmpty) Padding(padding: EdgeInsets.only(top: 8), child: Wrap(spacing: 6, runSpacing: 6, children: widget.comment.media.map((m){ if(_isImage(m.file)){ return GestureDetector(onTap: ()=> _openMedia(m), child: ClipRRect(borderRadius: BorderRadius.circular(8), child: CachedNetworkImage(imageUrl: m.file, height: 90, width: 90, fit: BoxFit.cover, memCacheWidth: 180))); }else if(_isVideo(m.file,m.mediaType)){ return GestureDetector(onTap: ()=> _openMedia(m), child: _buildCommentVideoThumb()); }else{ return InkWell(onTap: ()=> _openFile(m.file,m.fileName), child: Container(padding: EdgeInsets.symmetric(horizontal: 10, vertical: 8), decoration: BoxDecoration(color: cs.surface, borderRadius: BorderRadius.circular(8), border: Border.all(color: cs.outlineVariant)), child: Row(mainAxisSize: MainAxisSize.min, children: [Icon(Icons.description, size: 16, color: cs.onSurfaceVariant), const SizedBox(width: 6), SizedBox(width: 70, child: Text(m.fileName, style: TextStyle(fontSize: 12, color: cs.onSurface), overflow: TextOverflow.ellipsis))]))); } }).toList())),
            if(top3.isNotEmpty) Padding(padding: EdgeInsets.only(top: 6), child: Row(children: [Row(children: top3.map((e)=> Text(_emojiMap[e.key]??'', style: TextStyle(fontSize: 12))).toList()), SizedBox(width: 6), Text('${widget.comment.likesCount}', style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant))]))
          ]))),
          const SizedBox(height: 6),
          Row(children: [
            Text(timeago.format(widget.comment.createdAt, locale: Localizations.localeOf(context).languageCode), style: TextStyle(fontSize: 11, color: cs.onSurfaceVariant)),
            SizedBox(width: 14),
            Builder(builder: (likeCtx) {
              return GestureDetector(
                onTap: ()=> _handleReaction('like'),
                onLongPress: ()=> _showReactionOverlay(likeCtx),
                child: Container(padding: EdgeInsets.symmetric(horizontal: 10, vertical: 4), decoration: BoxDecoration(color: widget.comment.myReaction!=null? _emojiColor[widget.comment.myReaction]!.withOpacity(0.12) : Colors.transparent, borderRadius: BorderRadius.circular(20),), child: Row(children: [Icon(widget.comment.myReaction==null? Icons.thumb_up_alt_outlined : Icons.thumb_up_alt, size: 16, color: widget.comment.myReaction!=null? _emojiColor[widget.comment.myReaction] : cs.onSurfaceVariant), SizedBox(width: 4), Text(widget.comment.myReaction!=null? widget.comment.myReaction!.toUpperCase() : 'Like', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: widget.comment.myReaction!=null? _emojiColor[widget.comment.myReaction] : cs.onSurfaceVariant)), if(widget.comment.myReaction!=null)...[SizedBox(width: 4), Text(_emojiMap[widget.comment.myReaction]??'', style: TextStyle(fontSize: 12))]]),),
              );
            }),
            SizedBox(width: 12),
            GestureDetector(onTap: ()=> widget.onReply(widget.comment.id, widget.comment.user.username), child: Row(children: [Icon(Icons.chat_bubble_outline, size: 14, color: cs.onSurfaceVariant), SizedBox(width: 4), Text("Reply", style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant))])),
            if(widget.comment.repliesCount>0 || widget.localReplies.isNotEmpty)...[SizedBox(width: 12), GestureDetector(onTap: (){ if(showReplies) setState(()=> showReplies=false); else _loadReplies(); }, child: Text(loadingReplies? "Loading..." : showReplies? "Hide ${widget.comment.repliesCount + widget.localReplies.length}" : "${widget.comment.repliesCount + widget.localReplies.length} replies", style: TextStyle(fontSize: 12, color: cs.primary, fontWeight: FontWeight.w600)))],
          ]),
        ])),
        if(_canShowMenu) IconButton(icon: Icon(Icons.more_horiz, size: 18, color: cs.onSurfaceVariant), onPressed: ()=> _showOptions(), padding: EdgeInsets.zero, constraints: BoxConstraints(), visualDensity: VisualDensity.compact)
      ]),
      // 🔥 SAB REPLY EK HI SERIES ME
      if(showReplies) Padding(
        padding: EdgeInsets.only(top: 8, left: widget.level==0? 12 : 0),
        child: Container(
          decoration: BoxDecoration(
            border: Border(left: BorderSide(color: cs.outlineVariant, width: 2))
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

class _CommentImageFullScreen extends StatelessWidget { final String url; final String fileName; const _CommentImageFullScreen({required this.url, required this.fileName}); Future<void> _open(String url, String name, BuildContext context) async { String? token = await AuthService.getToken(); Directory dir = await getTemporaryDirectory(); String savePath = '${dir.path}/${name.replaceAll(' ', '_')}'; if (!await File(savePath).exists()) await Dio().download(url, savePath, options: Options(headers: token!=null && token.isNotEmpty? {"Authorization": "Bearer $token"} : {})); await OpenFilex.open(savePath); } @override Widget build(BuildContext context) { final mq = MediaQuery.of(context); final memW = (mq.size.width * mq.devicePixelRatio * 2).round().clamp(600, 2400); return Scaffold(backgroundColor: Colors.black, appBar: AppBar(backgroundColor: Colors.black, iconTheme: IconThemeData(color: Colors.white), title: Text(fileName, style: TextStyle(color: Colors.white, fontSize: 14)), actions: [IconButton(icon: Icon(Icons.open_in_new, color: Colors.white), onPressed: () => _open(url, fileName, context))]), body: SizedBox(width: double.infinity, height: double.infinity, child: InteractiveViewer(minScale: 0.5, maxScale: 6.0, child: Center(child: CachedNetworkImage(imageUrl: url, fit: BoxFit.contain, memCacheWidth: memW))))); } }
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