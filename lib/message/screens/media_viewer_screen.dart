// message/screens/media_viewer_screen.dart
//
// WhatsApp/Instagram jaisa fullscreen image viewer:
//   • image pe tap karte hi ye poori screen khulti hai
//   • multiple photos (album) hon to left/right SWIPE se agli/pichhli photo
//   • pinch-to-zoom (InteractiveViewer) har photo pe
//   • 🔥 NAYA — DOUBLE-TAP se bhi zoom in/out (Instagram/WhatsApp jaisa):
//     jahan double-tap kiya wahi center bana ke zoom-in hota hai; agar
//     photo already zoomed hai to double-tap se wapas normal size pe
//     zoom-out ho jaati hai
//   • niche ek chhoti thumbnail strip — dikhati hai ki abhi kaun si photo
//     dikh rahi hai, tap karke seedha us photo pe jump ho jaata hai
//   • strip + top bar 3 second ke baad apne aap fade-out ho jaate hain
//   • zoom (pinch/pan/double-tap scale > 1) shuru karte hi turant hide ho
//     jaate hain
//   • screen pe kahin bhi tap karo, wapas dikh jaate hain (timer reset)
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

class MediaViewerScreen extends StatefulWidget {
  final List<String> urls; // sab photos ke URLs (single photo ho to bhi ek-item list)
  final int initialIndex; // jis photo pe tap hua tha, wahan se hi khulna chahiye
  final void Function(String url)? onDownload; // long-press ya download icon ke liye (optional)
  final bool Function(String url)? isDownloaded; // download icon ka state (check/download)

  const MediaViewerScreen({
    super.key,
    required this.urls,
    this.initialIndex = 0,
    this.onDownload,
    this.isDownloaded,
  });

  @override
  State<MediaViewerScreen> createState() => _MediaViewerScreenState();
}

class _MediaViewerScreenState extends State<MediaViewerScreen> {
  late final PageController _pageController;
  late final ScrollController _stripController;
  late int _index;

  bool _controlsVisible = true;
  Timer? _hideTimer;

  // Har page ka apna zoom-state track karna padta hai — taaki agar user
  // photo-2 ko zoom kare, photo-1 ka zoom usse affect na ho.
  final Map<int, TransformationController> _zoomControllers = {};

  // 🔥 NAYA — double-tap se zoom-in karte waqt tap ki exact position
  // chahiye hoti hai (jahan tap hua wahi center bana ke zoom karna hai),
  // isliye onDoubleTapDown me position record karke onDoubleTap me use
  // karte hain.
  Offset? _doubleTapPosition;

  static const double _thumbSize = 52;
  static const double _thumbGap = 8;

  // Double-tap zoom scale — Instagram/WhatsApp jaisa hi ek fixed level
  // pe zoom-in hota hai.
  static const double _doubleTapScale = 3.0;

  @override
  void initState() {
    super.initState();
    _index = widget.initialIndex.clamp(0, widget.urls.length - 1);
    _pageController = PageController(initialPage: _index);
    _stripController = ScrollController();
    _restartHideTimer();
    // Pehli thumbnail-strip position ko selected item ke around center karo
    WidgetsBinding.instance.addPostFrameCallback((_) => _centerThumbStrip(animate: false));
  }

  TransformationController _zoomControllerFor(int i) {
    return _zoomControllers.putIfAbsent(i, () {
      final c = TransformationController();
      c.addListener(() => _onZoomChanged(i));
      return c;
    });
  }

  // ---- auto-hide timer logic ----
  void _restartHideTimer() {
    _hideTimer?.cancel();
    _hideTimer = Timer(const Duration(seconds: 3), () {
      if (mounted) setState(() => _controlsVisible = false);
    });
  }

  void _showControls() {
    if (!mounted) return;
    setState(() => _controlsVisible = true);
    _restartHideTimer();
  }

  // Screen pe (single) tap karne par: agar controls dikh rahe hain to
  // hide, warna wapas show + 3-sec timer restart (WhatsApp jaisa hi
  // toggle behaviour). Flutter khud hi double-tap ka wait karke ye
  // sirf single-tap confirm hone par hi call karta hai, isliye double-tap
  // se ye galti se trigger nahi hota.
  void _onScreenTap() {
    if (_controlsVisible) {
      _hideTimer?.cancel();
      setState(() => _controlsVisible = false);
    } else {
      _showControls();
    }
  }

  // 🔥 NAYA — double-tap se zoom-in/zoom-out. `onDoubleTapDown` se mila
  // tap-position use karke us point ko center banate hue zoom-in karte
  // hain; agar photo already zoomed hai (scale > 1) to seedha identity
  // matrix pe reset karke zoom-out kar dete hain.
  void _onDoubleTap(int i) {
    final controller = _zoomControllerFor(i);
    final currentScale = controller.value.getMaxScaleOnAxis();
    if (currentScale > 1.01) {
      // already zoomed — wapas normal size pe le aao
      controller.value = Matrix4.identity();
    } else {
      final pos = _doubleTapPosition;
      if (pos == null) return;
      // Tap point ko screen ke center pe le aane ke liye translate, phir
      // scale — isse zoom hamesha tap kiye gaye point ke around hota hai,
      // random top-left corner se nahi.
      controller.value = Matrix4.identity()
        ..translate(-pos.dx * (_doubleTapScale - 1), -pos.dy * (_doubleTapScale - 1))
        ..scale(_doubleTapScale);
    }
    _onZoomChanged(i);
  }

  // Zoom shuru hote hi (scale > 1) list turant hide — user ko zoomed photo
  // dekhne me koi cheez beech me nahi aani chahiye.
  void _onZoomChanged(int i) {
    if (i != _index) return; // sirf currently-visible page ka zoom matter karta hai
    final scale = _zoomControllers[i]?.value.getMaxScaleOnAxis() ?? 1.0;
    if (scale > 1.05 && _controlsVisible) {
      _hideTimer?.cancel();
      setState(() => _controlsVisible = false);
    } else if (scale <= 1.05 && !_controlsVisible) {
      // 🔥 NAYA — double-tap se zoom-out karte hi controls wapas dikha do,
      // WhatsApp/Instagram jaisa hi — user ka intent yahan "photo se bahar
      // aana" hota hai, isliye turant top-bar/strip wapas milne chahiye.
      _showControls();
    }
  }

  void _onPageChanged(int i) {
    setState(() => _index = i);
    _showControls();
    _centerThumbStrip(animate: true);
  }

  void _goToPage(int i) {
    _pageController.animateToPage(i, duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
    _showControls();
  }

  void _centerThumbStrip({required bool animate}) {
    if (!_stripController.hasClients) return;
    final target = (_index * (_thumbSize + _thumbGap)) - (MediaQuery.of(context).size.width / 2) + (_thumbSize / 2);
    final clamped = target.clamp(0.0, _stripController.position.maxScrollExtent);
    if (animate) {
      _stripController.animateTo(clamped, duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
    } else {
      _stripController.jumpTo(clamped);
    }
  }

  @override
  void dispose() {
    _hideTimer?.cancel();
    _pageController.dispose();
    _stripController.dispose();
    for (final c in _zoomControllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final count = widget.urls.length;
    final currentUrl = widget.urls[_index];
    final downloaded = widget.isDownloaded?.call(currentUrl) ?? false;

    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(children: [
        // ---- Swipeable + zoomable photo pages ----
        // 🔧 FIX: pehle single-tap-toggle wala GestureDetector poore
        // PageView ke UPAR (bahar) tha aur double-tap sirf idea tha —
        // ab tap + double-tap dono HAR PAGE ke apne GestureDetector me
        // hain (InteractiveViewer ke andar), taaki double-tap ka
        // tap-position exactly us photo ke local-coordinates me sahi mile.
        PageView.builder(
          controller: _pageController,
          itemCount: count,
          onPageChanged: _onPageChanged,
          itemBuilder: (_, i) {
            return GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: _onScreenTap,
              onDoubleTapDown: (details) => _doubleTapPosition = details.localPosition,
              onDoubleTap: () => _onDoubleTap(i),
              child: InteractiveViewer(
                transformationController: _zoomControllerFor(i),
                minScale: 1,
                maxScale: 4,
                onInteractionEnd: (_) => _onZoomChanged(i),
                child: Center(
                  child: CachedNetworkImage(
                    imageUrl: widget.urls[i],
                    fit: BoxFit.contain,
                    placeholder: (_, __) => const CircularProgressIndicator(color: Colors.white),
                    errorWidget: (_, __, ___) => const Icon(Icons.broken_image, color: Colors.white54, size: 60),
                  ),
                ),
              ),
            );
          },
        ),

        // ---- Top bar: close, counter, download ----
        AnimatedOpacity(
          opacity: _controlsVisible ? 1 : 0,
          duration: const Duration(milliseconds: 200),
          child: IgnorePointer(
            ignoring: !_controlsVisible,
            child: SafeArea(
              bottom: false,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                decoration: const BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [Colors.black54, Colors.transparent],
                  ),
                ),
                child: Row(children: [
                  IconButton(icon: const Icon(Icons.close, color: Colors.white), onPressed: () => Navigator.pop(context)),
                  const Spacer(),
                  if (count > 1)
                    Text("${_index + 1} / $count", style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w500)),
                  const Spacer(),
                  if (widget.onDownload != null)
                    IconButton(
                      icon: Icon(downloaded ? Icons.check : Icons.download, color: Colors.white),
                      onPressed: downloaded ? null : () => widget.onDownload?.call(currentUrl),
                    )
                  else
                    const SizedBox(width: 48), // top bar centre-balance ke liye
                ]),
              ),
            ),
          ),
        ),

        // ---- Bottom thumbnail strip: kaun si photo select hai dikhata
        // hai, 3-sec baad ya zoom pe fade-out, tap se wapas show ----
        if (count > 1)
          Align(
            alignment: Alignment.bottomCenter,
            child: AnimatedOpacity(
              opacity: _controlsVisible ? 1 : 0,
              duration: const Duration(milliseconds: 200),
              child: IgnorePointer(
                ignoring: !_controlsVisible,
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.only(top: 14, bottom: 4),
                  decoration: const BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.bottomCenter,
                      end: Alignment.topCenter,
                      colors: [Colors.black87, Colors.transparent],
                    ),
                  ),
                  child: SafeArea(
                    top: false,
                    child: SizedBox(
                      height: _thumbSize,
                      child: ListView.separated(
                        controller: _stripController,
                        scrollDirection: Axis.horizontal,
                        padding: const EdgeInsets.symmetric(horizontal: 14),
                        itemCount: count,
                        separatorBuilder: (_, __) => const SizedBox(width: _thumbGap),
                        itemBuilder: (_, i) {
                          final selected = i == _index;
                          return GestureDetector(
                            onTap: () => _goToPage(i),
                            child: AnimatedContainer(
                              duration: const Duration(milliseconds: 150),
                              width: _thumbSize,
                              height: _thumbSize,
                              decoration: BoxDecoration(
                                borderRadius: BorderRadius.circular(8),
                                border: Border.all(
                                  color: selected ? Colors.white : Colors.white30,
                                  width: selected ? 2.5 : 1,
                                ),
                              ),
                              clipBehavior: Clip.antiAlias,
                              child: Opacity(
                                opacity: selected ? 1 : 0.55,
                                child: CachedNetworkImage(
                                  imageUrl: widget.urls[i],
                                  fit: BoxFit.cover,
                                  placeholder: (_, __) => Container(color: Colors.white12),
                                  errorWidget: (_, __, ___) => const Icon(Icons.broken_image, color: Colors.white38, size: 16),
                                ),
                              ),
                            ),
                          );
                        },
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
      ]),
    );
  }
}