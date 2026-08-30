import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:video_player/video_player.dart';
import 'package:syncfusion_flutter_pdfviewer/pdfviewer.dart';
import 'package:share_plus/share_plus.dart';
import 'package:timeago/timeago.dart' as timeago;
import 'package:dio/dio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';

import '../../utils/api.dart';
import '../api_service.dart';
import '../../search/api_service.dart' as SearchApi;
import '../../profile/screens/target_profile.dart';
import '../../profile/screens/profile.dart';
import '../../profile/api_service.dart' as ProfileApi;
import '../../services/auth_service.dart';

Future<void> downloadWithAuth(String url, String fileName, BuildContext context) async {
  try {
    final token = await AuthService.getToken();
    final dio = Dio();
    final dir = await getApplicationDocumentsDirectory();
    final path = "${dir.path}/$fileName";
    await dio.download(url, path, options: Options(headers: {if (token != null) 'Authorization': 'Bearer $token'}));
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Downloaded: $fileName"), backgroundColor: Colors.green));
      await OpenFilex.open(path);
    }
  } catch (e) {
    if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Download failed: $e"), backgroundColor: Colors.red));
  }
}

class SinglePostPage extends StatefulWidget {
  final String postId;
  const SinglePostPage({super.key, required this.postId});
  @override State<SinglePostPage> createState() => _SinglePostPageState();
}

class _SinglePostPageState extends State<SinglePostPage> {
  Map<String, dynamic>? post;
  bool isLoading = true;
  String? error;
  bool isLiked = false;
  int currentIndex = 0;
  String fullImageUrl = "";
  String? myUsername;
  PageController pageController = PageController();

  @override void initState() { super.initState(); _initAll(); }
  Future<void> _initAll() async { await _loadMyUsername(); await _loadPost(); }

  Future<void> _loadMyUsername() async {
    try { final d = await ProfileApi.ApiService.getProfile(); myUsername = d.username; }
    catch (_) { try{ final t=await AuthService.getToken(); if(t!=null){ String p=base64.normalize(t.split('.')[1]); myUsername=jsonDecode(utf8.decode(base64Url.decode(p)))['username']?.toString(); } }catch(_){} }
  }

  Future<void> _loadPost() async {
    try {
      final data = await ApiService().getPostById(widget.postId);
      if (mounted) { setState((){ post=data; isLiked=data['is_liked']??false; isLoading=false; }); final u=data['user']['username']??''; if(u.isNotEmpty) _fetchPhotoFromSearchApi(u); }
    } catch (e){ if(mounted) setState(()=>{error=e.toString(), isLoading=false}); }
  }

  Future<void> _fetchPhotoFromSearchApi(String username) async {
    try{ final r=await SearchApi.ApiService.searchUsers(username); if(r.isNotEmpty){ dynamic m=r.firstWhere((u)=>u['username']==username, orElse:()=>r[0]); final p=m['profile_photo']; String url=""; if(p!=null&&p.isNotEmpty) url=p.startsWith('http')?p:"${Api.baseUrl}$p"; if(mounted) setState(()=>fullImageUrl=url);} }catch(_){}
  }

  String buildMediaUrl(String? path){ if(path==null||path.isEmpty) return ""; if(path.startsWith('http')){ if(path.contains("/media/")) return "${Api.baseUrl}${path.substring(path.indexOf("/media/"))}"; return path; } return "${Api.baseUrl}$path"; }

  Future<void> _goToProfile(String postUsername) async {
    if(postUsername.trim().isEmpty) return; if(myUsername==null) await _loadMyUsername();
    bool isMe = myUsername!=null && myUsername!.toLowerCase().trim()==postUsername.toLowerCase().trim();
    if(!mounted) return; if(isMe) Navigator.push(context, MaterialPageRoute(builder:(_)=>const ProfileScreen())); else Navigator.push(context, MaterialPageRoute(builder:(_)=>TargetProfilePage(username: postUsername)));
  }

  void _openFullScreen(){
    final media=(post!['media'] as List?)??[]; if(media.isEmpty) return;
    final m=media[currentIndex]; final fileUrl=buildMediaUrl(m['file']); final ext=fileUrl.split('.').last.toLowerCase().split('?').first; final type=(m['media_type']??'').toString().toLowerCase();
    if(type=='video'||['mp4','mov','mkv'].contains(ext)) Navigator.push(context, MaterialPageRoute(builder:(_)=>FullScreenVideoPage(url:fileUrl)));
    else if(['jpg','jpeg','png','webp','gif'].contains(ext)||type=='image') Navigator.push(context, MaterialPageRoute(builder:(_)=>FullScreenImagePage(url:fileUrl)));
    else Navigator.push(context, MaterialPageRoute(builder:(_)=>DocumentViewerPage(url:fileUrl, fileName:fileUrl.split('/').last.split('?').first)));
  }

  @override Widget build(BuildContext context){
    if(isLoading) return const Scaffold(body:Center(child:CircularProgressIndicator()));
    if(error!=null) return Scaffold(body:Center(child:Text(error!)));
    final user=post!['user']; final media=(post!['media'] as List?)??[]; final createdAt=DateTime.tryParse(post!['created_at']??''); final username=user['username']??'';
    return Scaffold(
      backgroundColor:Colors.white,
      appBar:AppBar(backgroundColor:const Color(0xFF030F27), iconTheme:const IconThemeData(color:Colors.white), title:Text(username, style:const TextStyle(color:Colors.white))),
      body:SingleChildScrollView(child:Column(crossAxisAlignment:CrossAxisAlignment.start, children:[
        InkWell(onTap:()=>_goToProfile(username), child:ListTile(leading:CircleAvatar(radius:22, backgroundColor:Colors.grey.shade200, child:ClipOval(child:fullImageUrl.isEmpty?const Icon(Icons.person):CachedNetworkImage(imageUrl:fullImageUrl, width:44, height:44, fit:BoxFit.cover))), title:Text(username, style:const TextStyle(fontWeight:FontWeight.bold)), subtitle:Text(createdAt!=null?timeago.format(createdAt):''))),
        Container(height:580, color:Colors.black, child:Stack(children:[
          PageView.builder(controller:pageController, itemCount:media.length, onPageChanged:(i)=>setState(()=>currentIndex=i), itemBuilder:(c,i){
            final fileUrl=buildMediaUrl(media[i]['file']); final ext=fileUrl.split('.').last.toLowerCase().split('?').first; final type=(media[i]['media_type']??'').toString().toLowerCase();
            if(type=='video'||['mp4','mov','mkv'].contains(ext)) return SmallVideoPlayer(url: fileUrl);
            if(ext=='pdf'||type=='pdf') return SmallPdfViewer(url: fileUrl);
            if(['doc','docx','xls','xlsx','ppt','pptx','txt'].contains(ext)) return DocThumbnail(url: fileUrl, ext: ext, onOpen: _openFullScreen);
            return CachedNetworkImage(imageUrl:fileUrl, fit:BoxFit.cover, width:double.infinity);
          }),
          Positioned(top:10,right:10, child:InkWell(onTap:_openFullScreen, child:Container(padding:const EdgeInsets.all(6), decoration:BoxDecoration(color:Colors.black54, borderRadius:BorderRadius.circular(20)), child:const Icon(Icons.fullscreen, color:Colors.white, size:20)))),
        ])),
        Padding(padding:const EdgeInsets.symmetric(horizontal:8, vertical:6), child:Row(children:[IconButton(icon:Icon(isLiked?Icons.favorite:Icons.favorite_border, color:isLiked?Colors.red:Colors.black87), onPressed:()=>setState(()=>isLiked=!isLiked)), Text("${post!['likes_count']??0}"), const SizedBox(width:12), IconButton(icon:const Icon(Icons.chat_bubble_outline), onPressed:(){}), Text("${post!['comments_count']??0}"), const Spacer(), IconButton(icon:const Icon(Icons.share_outlined), onPressed:()=>Share.share("${post!['title']??''}\n${post!['content']??''}"))])),
        Padding(padding:const EdgeInsets.symmetric(horizontal:16), child:Text(post!['title']??'', style:const TextStyle(fontWeight:FontWeight.bold))),
        // 🔥 Category + Subcategory - pehle subcategory kabhi render hi nahi hota tha
        if((post!['category_label']??post!['category']??'').toString().isNotEmpty || (post!['subcategory_label']??post!['subcategory']??'').toString().isNotEmpty)
          Padding(padding:const EdgeInsets.symmetric(horizontal:16, vertical:4), child:Wrap(spacing:8, children:[
            if((post!['category_label']??post!['category']??'').toString().isNotEmpty)
              Chip(label:Text((post!['category_label']??post!['category']).toString()), backgroundColor:Colors.grey.shade200),
            if((post!['subcategory_label']??post!['subcategory']??'').toString().isNotEmpty)
              Chip(label:Text((post!['subcategory_label']??post!['subcategory']).toString()), backgroundColor:Colors.grey.shade200),
          ])),
        const SizedBox(height:20),
      ])),
    );
  }
}

// CHOTI SCREEN VIDEO - AUTO PLAY, PAUSED NAHI HOGA
class SmallVideoPlayer extends StatefulWidget { final String url; const SmallVideoPlayer({super.key, required this.url}); @override State<SmallVideoPlayer> createState()=>_SmallVideoPlayerState(); }
class _SmallVideoPlayerState extends State<SmallVideoPlayer>{
  late VideoPlayerController _c; bool _ok=false;
  @override void initState(){ super.initState(); _init(); }
  Future<void> _init() async {
    final token = await AuthService.getToken();
    final headers = token!=null? {'Authorization':'Bearer $token'}: <String,String>{};
    _c = VideoPlayerController.networkUrl(Uri.parse(widget.url), httpHeaders: headers);
    await _c.initialize(); await _c.setLooping(true); await _c.play();
    if(mounted) setState(()=>_ok=true);
  }
  @override void dispose(){ _c.dispose(); super.dispose(); }
  @override Widget build(BuildContext context){ if(!_ok) return const Center(child:CircularProgressIndicator(color:Colors.white)); return GestureDetector(onTap:(){ _c.value.isPlaying? _c.pause(): _c.play(); setState((){}); }, child:Stack(alignment:Alignment.center, children:[AspectRatio(aspectRatio:_c.value.aspectRatio, child:VideoPlayer(_c)), if(!_c.value.isPlaying) const Icon(Icons.play_circle_fill, color:Colors.white70, size:60)])); }
}

// CHOTI SCREEN PDF - AB DISPLAY HOGA
class SmallPdfViewer extends StatefulWidget { final String url; const SmallPdfViewer({super.key, required this.url}); @override State<SmallPdfViewer> createState()=>_SmallPdfViewerState(); }
class _SmallPdfViewerState extends State<SmallPdfViewer>{
  String? token; bool loading=true;
  @override void initState(){ super.initState(); _loadToken(); }
  Future<void> _loadToken() async { final t=await AuthService.getToken(); if(mounted) setState((){ token=t; loading=false; }); }
  @override Widget build(BuildContext context){ if(loading) return const Center(child:CircularProgressIndicator(color:Colors.white)); return SfPdfViewer.network(widget.url, headers: token!=null? {'Authorization':'Bearer $token'}: null, canShowScrollHead:false, canShowPaginationDialog:false); }
}

// DOC THUMB - DIRECT DOWNLOAD BUTTON
class DocThumbnail extends StatelessWidget {
  final String url; final String ext; final VoidCallback onOpen;
  const DocThumbnail({super.key, required this.url, required this.ext, required this.onOpen});
  @override Widget build(BuildContext context){
    final fileName = url.split('/').last.split('?').first;
    return Container(color:Colors.grey.shade900, child:Column(mainAxisAlignment:MainAxisAlignment.center, children:[
      Icon(ext=='pdf'?Icons.picture_as_pdf:Icons.description, color:Colors.white, size:70),
      const SizedBox(height:8), Padding(padding:const EdgeInsets.symmetric(horizontal:12), child:Text(fileName, style:const TextStyle(color:Colors.white70, fontSize:12), overflow:TextOverflow.ellipsis, maxLines:1)),
      const SizedBox(height:12),
      Row(mainAxisAlignment:MainAxisAlignment.center, children:[
        ElevatedButton(onPressed:onOpen, child:const Text("Open")),
        const SizedBox(width:10),
        OutlinedButton.icon(onPressed:()=>downloadWithAuth(url, fileName, context), icon:const Icon(Icons.download, size:16, color:Colors.white), label:const Text("Download", style:TextStyle(color:Colors.white)), style:OutlinedButton.styleFrom(side:const BorderSide(color:Colors.white54))),
      ])
    ]));
  }
}

// FULLSCREEN VIDEO
class FullScreenVideoPage extends StatefulWidget { final String url; const FullScreenVideoPage({super.key, required this.url}); @override State<FullScreenVideoPage> createState()=>_FullScreenVideoPageState(); }
class _FullScreenVideoPageState extends State<FullScreenVideoPage>{
  late VideoPlayerController _controller; bool _show=true; Timer? _t;
  String _fmt(Duration d){ String t(int n)=>n.toString().padLeft(2,'0'); return "${t(d.inMinutes.remainder(60))}:${t(d.inSeconds.remainder(60))}"; }
  @override void initState(){ super.initState(); SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp, DeviceOrientation.landscapeLeft, DeviceOrientation.landscapeRight]); _controller=VideoPlayerController.networkUrl(Uri.parse(widget.url))..initialize().then((_){ if(mounted){setState((){}); _controller.play(); _hide();}}); _controller.addListener(()=>{if(mounted)setState((){})});}
  void _hide(){ _t?.cancel(); _t=Timer(const Duration(seconds:3),(){ if(mounted&&_controller.value.isPlaying) setState(()=>_show=false);});}
  void _tap(){ setState(()=>_show=!_show); if(_show) _hide();}
  @override void dispose(){ _t?.cancel(); SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]); _controller.dispose(); super.dispose();}
  @override Widget build(BuildContext context){ return Scaffold(backgroundColor:Colors.black, body:GestureDetector(onTap:_tap, child:Stack(children:[Center(child:_controller.value.isInitialized? AspectRatio(aspectRatio:_controller.value.aspectRatio, child:VideoPlayer(_controller)): const CircularProgressIndicator(color:Colors.white)), if(_show) Container(color:Colors.black38, child:Stack(children:[Positioned(top:40,left:10,child:IconButton(icon:const Icon(Icons.close,color:Colors.white,size:28), onPressed:()=>Navigator.pop(context))), Center(child:IconButton(icon:Icon(_controller.value.isPlaying? Icons.pause_circle: Icons.play_circle, color:Colors.white, size:72), onPressed:(){ setState(()=>_controller.value.isPlaying? _controller.pause(): _controller.play()); _hide();})), Positioned(bottom:20,left:12,right:12, child:Column(children:[VideoProgressIndicator(_controller, allowScrubbing:true, padding:const EdgeInsets.symmetric(vertical:6)), Row(children:[Text(_fmt(_controller.value.position), style:const TextStyle(color:Colors.white,fontSize:12)), Expanded(child:Slider(min:0, max:_controller.value.duration.inMilliseconds.toDouble().clamp(1,double.infinity), value:_controller.value.position.inMilliseconds.toDouble().clamp(0,_controller.value.duration.inMilliseconds.toDouble().clamp(1,double.infinity)), activeColor:Colors.red, inactiveColor:Colors.white24, onChanged:(v)=>_controller.seekTo(Duration(milliseconds:v.toInt())))), Text(_fmt(_controller.value.duration), style:const TextStyle(color:Colors.white,fontSize:12))])]))]))]))); }
}

// BIG IMAGE - ZOOM OUT PE FULL COVER
class FullScreenImagePage extends StatelessWidget {
  final String url; const FullScreenImagePage({super.key, required this.url});
  @override Widget build(BuildContext context){
    return Scaffold(backgroundColor:Colors.black, body:Stack(children:[
      SizedBox.expand(child:InteractiveViewer(minScale:0.9, maxScale:5.0, child:CachedNetworkImage(imageUrl:url, fit:BoxFit.cover, width:double.infinity, height:double.infinity))),
      Positioned(top:40,left:10,child:IconButton(icon:const Icon(Icons.close,color:Colors.white,size:28), onPressed:()=>Navigator.pop(context))),
    ]));
  }
}

// DOCUMENT VIEWER - PDF/DOC + ALWAYS DOWNLOAD BUTTON
class DocumentViewerPage extends StatefulWidget { final String url; final String fileName; const DocumentViewerPage({super.key, required this.url, required this.fileName}); @override State<DocumentViewerPage> createState()=>_DocumentViewerPageState(); }
class _DocumentViewerPageState extends State<DocumentViewerPage>{
  bool isDownloading=false; bool isPreparing=true; String? localPath;
  bool get isPdf => widget.fileName.toLowerCase().endsWith('.pdf');
  @override void initState(){ super.initState(); _prepare(); }
  Future<void> _prepare() async {
    try{
      final token=await AuthService.getToken(); final dir=await getTemporaryDirectory(); final path="${dir.path}/${widget.fileName}";
      await Dio().download(widget.url, path, options:Options(headers:{if(token!=null)'Authorization':'Bearer $token'}));
      if(mounted) setState(()=>{localPath=path, isPreparing=false});
    }catch(e){ if(mounted) setState(()=>isPreparing=false); }
  }
  @override Widget build(BuildContext context){
    return Scaffold(
      appBar:AppBar(backgroundColor:const Color(0xFF030F27), iconTheme:const IconThemeData(color:Colors.white), title:Text(widget.fileName, style:const TextStyle(color:Colors.white,fontSize:13), overflow:TextOverflow.ellipsis),
        actions:[isDownloading? const Padding(padding:EdgeInsets.all(14), child:SizedBox(width:18,height:18,child:CircularProgressIndicator(strokeWidth:2,color:Colors.white))): IconButton(icon:const Icon(Icons.download, color:Colors.white), onPressed:()=>downloadWithAuth(widget.url, widget.fileName, context))]),
      body: isPreparing? const Center(child:Column(mainAxisAlignment:MainAxisAlignment.center, children:[CircularProgressIndicator(), SizedBox(height:10), Text("Loading document...")]))
        : isPdf && localPath!=null? SfPdfViewer.file(File(localPath!))
        : isPdf? FutureBuilder<String?>(future:AuthService.getToken(), builder:(c,s){ final h=s.data!=null? {'Authorization':'Bearer ${s.data}'}:null; return SfPdfViewer.network(widget.url, headers:h as Map<String,String>?); })
        : Center(child:Column(mainAxisAlignment:MainAxisAlignment.center, children:[
            const Icon(Icons.insert_drive_file, size:80, color:Colors.grey), const SizedBox(height:12), Padding(padding:const EdgeInsets.symmetric(horizontal:20), child:Text(widget.fileName, textAlign:TextAlign.center)),
            const SizedBox(height:8), const Text("Preview not supported for this file", style:TextStyle(color:Colors.grey, fontSize:12)),
            const SizedBox(height:20),
            ElevatedButton.icon(onPressed:()=>downloadWithAuth(widget.url, widget.fileName, context), icon:const Icon(Icons.download), label:const Text("Direct Download & Open")),
            if(localPath!=null) Padding(padding:const EdgeInsets.only(top:10), child:OutlinedButton(onPressed:()=>OpenFilex.open(localPath!), child:const Text("Open Downloaded File")))
          ])),
    );
  }
}