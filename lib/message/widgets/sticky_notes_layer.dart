// lib/message/widgets/sticky_notes_layer.dart
//
// 🔥 NAYA — Study Room sticky notes ka UI:
//   * StickyNotesLayer         — whiteboard canvas (scene coordinates) ke andar notes ka stack
//   * StickyNoteView           — ek note: drag (header), resize (corner), color pick, inline
//                                text edit, delete, tap/focus par front-most
//   * StickyNotesStatusOverlay — viewport-anchored loading / error(retry) / empty state
//
// State + sync `StickyNoteSync` me hai; ye files sirf render + gestures.

import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/study_room_models.dart';
import '../services/sticky_note_sync.dart';

/// Note colors (ARGB). Pehla default (yellow) hai.
const List<Color> kStickyNoteColors = [
  Color(0xFFFFF59D), // yellow
  Color(0xFFFFCC80), // orange
  Color(0xFFF8BBD0), // pink
  Color(0xFFC5E1A5), // green
  Color(0xFF80DEEA), // cyan
  Color(0xFFB39DDB), // purple
  Color(0xFFFFFFFF), // white
];

/// Canvas Stack me `Positioned.fill(child: StickyNotesLayer(...))` ki tarah lagao.
/// Khaali jagah touches neeche ke drawing canvas tak pahunchte hain (Stack
/// khud hit nahi leta, sirf notes leti hain).
class StickyNotesLayer extends StatelessWidget {
  const StickyNotesLayer({
    super.key,
    required this.sync,
    required this.pageId,
    this.dragEnabled = true,
  });

  final StickyNoteSync sync;
  final String pageId;

  /// Pan/zoom mode me note drag band (canvas pan ke saath takraye nahi).
  final bool dragEnabled;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: sync,
      builder: (context, _) {
        final notes = sync.notesForPage(pageId);
        return Stack(
          fit: StackFit.expand,
          clipBehavior: Clip.none,
          children: [
            for (final n in notes)
              StickyNoteView(
                key: ValueKey('sticky_${n.id}'),
                note: n,
                sync: sync,
                dragEnabled: dragEnabled,
              ),
          ],
        );
      },
    );
  }
}

class StickyNoteView extends StatefulWidget {
  const StickyNoteView({
    super.key,
    required this.note,
    required this.sync,
    this.dragEnabled = true,
  });

  final StickyNoteModel note;
  final StickyNoteSync sync;
  final bool dragEnabled;

  @override
  State<StickyNoteView> createState() => _StickyNoteViewState();
}

class _StickyNoteViewState extends State<StickyNoteView> {
  late final TextEditingController _controller;
  late final FocusNode _focus;
  bool _resizing = false;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.note.text);
    _focus = FocusNode()..addListener(_onFocusChange);
    if (widget.sync.takeAutofocus(widget.note.id)) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _focus.requestFocus();
      });
    }
  }

  @override
  void didUpdateWidget(StickyNoteView oldWidget) {
    super.didUpdateWidget(oldWidget);
    _syncControllerFromModel();
  }

  /// Model (last-write-wins se resolve hua text) hi truth hai. Local typing
  /// pehle model me jaati hai, to wahan mismatch nahi hota; mismatch sirf
  /// tab hota hai jab remote edit jeeta — tab controller ko update karo.
  void _syncControllerFromModel() {
    final text = widget.note.text;
    if (_controller.text == text) return;
    _controller.value = TextEditingValue(
      text: text,
      selection: TextSelection.collapsed(offset: text.length),
    );
  }

  void _onFocusChange() {
    if (_focus.hasFocus) {
      widget.sync.bringToFront(widget.note.id);
    } else {
      widget.sync.flushText(widget.note.id);
    }
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    // notify:false — dispose widget-tree lock ke dauran hota hai, us waqt listeners ko rebuild nahi karwa sakte.
    widget.sync.flushText(widget.note.id, notify: false);
    _focus.removeListener(_onFocusChange);
    _focus.dispose();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final note = widget.note;
    final sync = widget.sync;
    final id = note.id;

    final dragging = sync.isLocalDragging(id) && !_resizing;
    final gestureActive = sync.isLocalDragging(id) || _resizing;
    final remoteMoving = sync.isRemoteMoving(id);
    final active = gestureActive || _focus.hasFocus;

    final isDark = ThemeData.estimateBrightnessForColor(note.color) == Brightness.dark;
    final ink = isDark ? Colors.white : const Color(0xDD000000);
    final softInk = ink.withOpacity(0.55);

    return AnimatedPositioned(
      // Apni drag/resize me zero-lag (finger ke saath), baaki sab (remote
      // move, rollback, z-change) me chhota smooth animation.
      duration: gestureActive ? Duration.zero : Duration(milliseconds: remoteMoving ? 110 : 180),
      curve: remoteMoving ? Curves.linear : Curves.easeOutCubic,
      left: note.position.dx,
      top: note.position.dy,
      width: note.size.width,
      height: note.size.height,
      child: Listener(
        // Kahin bhi touch => ye note front-most (z-index).
        onPointerDown: (_) => sync.bringToFront(id),
        child: AnimatedScale(
          scale: dragging ? 1.04 : 1.0,
          duration: const Duration(milliseconds: 120),
          curve: Curves.easeOut,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 150),
            decoration: BoxDecoration(
              color: note.color,
              borderRadius: BorderRadius.circular(10),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withOpacity(active ? 0.34 : 0.18),
                  blurRadius: active ? 18 : 6,
                  offset: Offset(0, active ? 9 : 2),
                ),
              ],
            ),
            child: Stack(
              children: [
                Column(
                  children: [
                    _buildHeader(id, ink, softInk, note.isPending),
                    Expanded(
                      child: Padding(
                        padding: const EdgeInsets.fromLTRB(10, 0, 10, 12),
                        child: TextField(
                          controller: _controller,
                          focusNode: _focus,
                          maxLines: null,
                          minLines: null,
                          expands: true,
                          maxLength: StickyNoteModel.maxTextLength,
                          maxLengthEnforcement: MaxLengthEnforcement.enforced,
                          buildCounter: (context, {required currentLength, required isFocused, required maxLength}) =>
                              null,
                          textAlignVertical: TextAlignVertical.top,
                          keyboardType: TextInputType.multiline,
                          textCapitalization: TextCapitalization.sentences,
                          cursorColor: ink,
                          style: TextStyle(fontSize: 13.5, height: 1.25, color: ink),
                          decoration: InputDecoration(
                            isDense: true,
                            border: InputBorder.none,
                            contentPadding: EdgeInsets.zero,
                            hintText: 'Type a note…',
                            hintStyle: TextStyle(color: softInk),
                          ),
                          onChanged: (v) => sync.setText(id, v),
                        ),
                      ),
                    ),
                  ],
                ),
                // Resize grip (bottom-right)
                Positioned(
                  right: 0,
                  bottom: 0,
                  child: GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onPanStart: widget.dragEnabled
                        ? (_) {
                            setState(() => _resizing = true);
                            sync.beginDrag(id);
                          }
                        : null,
                    onPanUpdate: widget.dragEnabled ? (d) => sync.resizeBy(id, d.delta) : null,
                    onPanEnd: widget.dragEnabled ? (_) => _endResize() : null,
                    onPanCancel: widget.dragEnabled ? _endResize : null,
                    child: SizedBox(
                      width: 30,
                      height: 30,
                      child: CustomPaint(painter: _ResizeGripPainter(softInk)),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  void _endResize() {
    if (!_resizing) return;
    setState(() => _resizing = false);
    widget.sync.commitResize(widget.note.id);
  }

  Widget _buildHeader(String id, Color ink, Color softInk, bool pending) {
    final sync = widget.sync;
    return SizedBox(
      height: 30,
      child: Row(
        children: [
          // Drag handle — header ka poora khaali hissa
          Expanded(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onPanStart: widget.dragEnabled ? (_) => sync.beginDrag(id) : null,
              onPanUpdate: widget.dragEnabled ? (d) => sync.dragBy(id, d.delta) : null,
              onPanEnd: widget.dragEnabled ? (_) => sync.commitMove(id) : null,
              onPanCancel: widget.dragEnabled ? () => sync.commitMove(id) : null,
              child: Padding(
                padding: const EdgeInsets.only(left: 8),
                child: Row(
                  children: [
                    Icon(Icons.drag_indicator, size: 16, color: softInk),
                    if (pending) ...[
                      const SizedBox(width: 6),
                      SizedBox(
                        width: 10,
                        height: 10,
                        child: CircularProgressIndicator(strokeWidth: 1.6, color: softInk),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ),
          PopupMenuButton<int>(
            tooltip: 'Note color',
            padding: EdgeInsets.zero,
            iconSize: 17,
            icon: Icon(Icons.palette_outlined, size: 17, color: softInk),
            constraints: const BoxConstraints(minWidth: 0),
            onSelected: (value) => sync.setColor(id, Color(value)),
            itemBuilder: (menuContext) => [
              PopupMenuItem<int>(
                enabled: false, // tap swatch ka apna GestureDetector handle karta hai
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: Wrap(
                  spacing: 10,
                  runSpacing: 10,
                  children: [
                    for (final c in kStickyNoteColors)
                      GestureDetector(
                        onTap: () => Navigator.of(menuContext).pop(c.value),
                        child: Container(
                          width: 28,
                          height: 28,
                          decoration: BoxDecoration(
                            color: c,
                            shape: BoxShape.circle,
                            border: Border.all(
                              color: c == widget.note.color ? Colors.black87 : Colors.black26,
                              width: c == widget.note.color ? 2.4 : 1,
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ],
          ),
          InkResponse(
            onTap: () {
              _focus.unfocus();
              sync.deleteNote(id);
            },
            radius: 18,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(4, 6, 8, 6),
              child: Icon(Icons.close, size: 17, color: softInk),
            ),
          ),
        ],
      ),
    );
  }
}

class _ResizeGripPainter extends CustomPainter {
  _ResizeGripPainter(this.color);
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.6
      ..strokeCap = StrokeCap.round;
    final w = size.width;
    final h = size.height;
    for (final inset in [8.0, 14.0, 20.0]) {
      canvas.drawLine(Offset(w - 4, h - inset), Offset(w - inset, h - 4), paint);
    }
  }

  @override
  bool shouldRepaint(_ResizeGripPainter old) => old.color != color;
}

/// Viewport-anchored (zoom/pan ke saath nahi hilta) status chip:
/// loading -> spinner, error -> Retry, empty -> chhota hint (kuch second baad
/// khud chala jaata hai, board ko clutter nahi karta).
class StickyNotesStatusOverlay extends StatelessWidget {
  const StickyNotesStatusOverlay({super.key, required this.sync, required this.pageId});

  final StickyNoteSync sync;
  final String pageId;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: sync,
      builder: (context, _) {
        Widget? chip;
        var needsPointer = false;

        if (sync.isLoading) {
          chip = _chip(
            context,
            leading: const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
            label: 'Sticky notes load ho rahe hain…',
          );
        } else if (sync.loadFailed) {
          needsPointer = true;
          chip = _chip(
            context,
            leading: const Icon(Icons.cloud_off_outlined, size: 16),
            label: 'Notes load nahi ho paye',
            trailing: TextButton(
              style: TextButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: 8),
                minimumSize: const Size(0, 28),
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              ),
              onPressed: () => sync.load(),
              child: const Text('Retry'),
            ),
          );
        } else if (!sync.emptyHintDismissed && sync.notesForPage(pageId).isEmpty) {
          chip = _chip(
            context,
            leading: const Icon(Icons.sticky_note_2_outlined, size: 16),
            label: 'Abhi koi sticky note nahi — toolbar se add karo',
          );
        }

        return Positioned(
          top: 10,
          left: 0,
          right: 0,
          child: IgnorePointer(
            ignoring: !needsPointer,
            child: Center(
              child: AnimatedSwitcher(
                duration: const Duration(milliseconds: 220),
                transitionBuilder: (child, anim) => FadeTransition(
                  opacity: anim,
                  child: SlideTransition(
                    position: Tween<Offset>(begin: const Offset(0, -0.25), end: Offset.zero).animate(anim),
                    child: child,
                  ),
                ),
                child: chip ?? const SizedBox.shrink(key: ValueKey('sticky_status_none')),
              ),
            ),
          ),
        );
      },
    );
  }

  Widget _chip(BuildContext context, {required Widget leading, required String label, Widget? trailing}) {
    final scheme = Theme.of(context).colorScheme;
    return Material(
      key: ValueKey('sticky_status_$label'),
      elevation: 3,
      color: scheme.surface,
      borderRadius: BorderRadius.circular(20),
      child: Padding(
        padding: EdgeInsets.fromLTRB(12, 6, trailing == null ? 14 : 4, 6),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            IconTheme(data: IconThemeData(color: scheme.onSurfaceVariant), child: leading),
            const SizedBox(width: 8),
            ConstrainedBox(
              constraints: BoxConstraints(maxWidth: math.max(160, MediaQuery.of(context).size.width - 140)),
              child: Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12.5, color: scheme.onSurface),
              ),
            ),
            if (trailing != null) trailing,
          ],
        ),
      ),
    );
  }
}
