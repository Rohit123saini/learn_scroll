// lib/message/services/sticky_note_sync.dart
//
// 🔥 NAYA — Study Room collaborative sticky notes ka poora client-side
// state + sync engine. Screen (`study_room_screen.dart`) sirf isko socket
// events feed karta hai aur `StickyNotesLayer` isko sunkar render karta hai.
//
// Flow (backend contract: backend/message/sticky_notes.py):
//
//   1. `load()`            GET /message/study-room/<id>/notes/  (+ server clock offset)
//   2. local action        -> turant local state badlo (OPTIMISTIC, `isPending=true`)
//                          -> `note_*` op queue me -> socket se bhejo (opId ke saath)
//   3. server `note_ack`   -> op confirm; agar server ne kuch group reject kiya
//                             (stale ts) to server ki value se rollback
//   4. remote `note_upsert`/`note_removed`/`note_drag` -> per-group last-write-wins merge
//   5. socket reconnect    -> pending ops resend + silent re-fetch (missed events)
//
// Conflict rule (per field-group: pos / size / color / text / z): jis write
// ka timestamp bada (>=) wo jeetta hai. Timestamp = device clock + server
// offset (load() me nikalta hai), aur hamesha strictly increasing.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';

import '../models/study_room_models.dart';

/// `(action, data)` -> study-room socket event bhejo (screen ka `_sendRoomEvent`).
typedef StickySendEvent = void Function(String action, Map<String, dynamic> data);

class _PendingOp {
  _PendingOp({
    required this.opId,
    required this.action,
    required this.noteId,
    required this.data,
    this.coalesceKey,
  });

  final String opId;
  final String action;
  final String noteId;
  Map<String, dynamic> data;
  final String? coalesceKey;
  bool sent = false;
  DateTime? sentAt;
  int attempts = 0;
}

class StickyNoteSync extends ChangeNotifier {
  StickyNoteSync({
    required this.currentUserId,
    required this.sendEvent,
    required this.isSocketConnected,
    required this.fetchNotes,
    this.onNotice,
  });

  final String currentUserId;
  final StickySendEvent sendEvent;
  final bool Function() isSocketConnected;
  final Future<Map<String, dynamic>?> Function() fetchNotes;

  /// User ko dikhane layak short message (snackbar) — e.g. "Limit reached".
  final void Function(String message)? onNotice;

  // ---- tunables ----
  static const Duration _textDebounce = Duration(milliseconds: 350);
  static const Duration _dragSendInterval = Duration(milliseconds: 70);
  static const Duration _remoteMoveHold = Duration(milliseconds: 350);
  static const Duration _ackTimeout = Duration(seconds: 6);
  static const Duration _retryTick = Duration(seconds: 2);
  static const Duration _emptyHintDuration = Duration(seconds: 7);
  static const int _maxAttempts = 4;

  // ---- state ----
  final Map<String, StickyNoteModel> _notes = {};
  final Map<String, StickyNoteModel> _deletedSnapshots = {}; // forbidden-delete rollback
  final Set<String> _deletedIds = {}; // tombstones: late upsert inhe wapas na laaye
  final Map<String, _PendingOp> _pending = {}; // opId -> op (insertion-ordered)
  final Set<String> _dirtyText = {};
  final Map<String, Timer> _textTimers = {};
  final Map<String, Timer> _remoteMoveTimers = {};
  final Set<String> _remoteMoving = {};
  final Set<String> _localGesture = {}; // main abhi drag/resize kar raha hoon
  final Map<String, DateTime> _lastDragSent = {};
  String? _autofocusId;
  Timer? _retryTimer;
  Timer? _hintTimer;

  int _clockOffsetMs = 0;
  int _lastTs = 0;
  int _seq = 0;
  int _maxNotes = 200;
  bool _disposed = false;

  bool isLoading = true;
  bool loadFailed = false;
  bool emptyHintDismissed = false;

  // ------------------------------------------------------------------
  // Read API (UI)
  // ------------------------------------------------------------------
  Iterable<StickyNoteModel> get allNotes => _notes.values;

  /// Page ke notes, neeche se upar (z_index, phir id — deterministic tie-break).
  List<StickyNoteModel> notesForPage(String pageId) {
    final list = _notes.values.where((n) => n.pageId == pageId).toList();
    list.sort((a, b) {
      final z = a.zIndex.compareTo(b.zIndex);
      return z != 0 ? z : a.id.compareTo(b.id);
    });
    return list;
  }

  bool isLocalDragging(String id) => _localGesture.contains(id);
  bool isRemoteMoving(String id) => _remoteMoving.contains(id);
  int get pendingOpCount => _pending.length;

  /// Naye bane note par ek baar keyboard/focus dene ke liye.
  bool takeAutofocus(String id) {
    if (_autofocusId == id) {
      _autofocusId = null;
      return true;
    }
    return false;
  }

  // ------------------------------------------------------------------
  // Clock — server-corrected, strictly increasing
  // ------------------------------------------------------------------
  int _nextTs() {
    final now = DateTime.now().millisecondsSinceEpoch + _clockOffsetMs;
    _lastTs = now > _lastTs ? now : _lastTs + 1;
    return _lastTs;
  }

  // ------------------------------------------------------------------
  // Load / resync
  // ------------------------------------------------------------------
  /// REST se notes laao. `silent: true` = reconnect resync (spinner/error state nahi).
  Future<void> load({bool silent = false}) async {
    if (!silent) {
      isLoading = true;
      loadFailed = false;
      _notify();
    }
    try {
      final before = DateTime.now().millisecondsSinceEpoch;
      final data = await fetchNotes();
      final after = DateTime.now().millisecondsSinceEpoch;
      if (data == null) throw StateError('empty notes response');

      final serverMs = (data['server_time_ms'] as num?)?.toInt();
      if (serverMs != null) _clockOffsetMs = serverMs - ((before + after) ~/ 2);
      final limits = data['limits'];
      if (limits is Map && limits['max_notes'] is num) {
        _maxNotes = (limits['max_notes'] as num).toInt();
      }

      final server = <String, StickyNoteModel>{};
      for (final raw in (data['notes'] as List?) ?? const []) {
        if (raw is! Map) continue;
        try {
          final n = StickyNoteModel.fromJson(raw.cast<String, dynamic>());
          server[n.id] = n;
        } catch (_) {
          // ek kharab row poori list na bigaade
        }
      }

      // Server hi truth hai — sirf wo local notes bachao jinka `note_add` abhi ack nahi hua.
      final unackedAdds = _pending.values.where((o) => o.action == 'note_add').map((o) => o.noteId).toSet();
      for (final id in _notes.keys.toList()) {
        if (!server.containsKey(id) && !unackedAdds.contains(id)) _notes.remove(id);
      }
      for (final s in server.values) {
        if (_deletedIds.contains(s.id)) continue;
        final local = _notes[s.id];
        if (local == null) {
          _notes[s.id] = s;
        } else {
          local.mergeFrom(s, skip: _localGesture.contains(s.id) ? {StickyGroup.pos, StickyGroup.size} : const {});
        }
      }
      loadFailed = false;
      _scheduleEmptyHint();
    } catch (_) {
      if (!silent) loadFailed = true; // silent failure: jo local hai wahi rakho
    } finally {
      isLoading = false;
      _notify();
    }
  }

  /// Socket khula (pehli baar ya reconnect). Reconnect par missed updates ke liye resync.
  void onSocketOpen({required bool isReconnect}) {
    for (final op in _pending.values) {
      op.sent = false; // naye socket pe sab dobara — server idempotent + LWW-safe hai
    }
    _flushPending();
    if (isReconnect) load(silent: true);
  }

  // ------------------------------------------------------------------
  // Local actions (optimistic)
  // ------------------------------------------------------------------
  /// Naya note. Limit hit ho to null (notice diya jaata hai).
  StickyNoteModel? addNote({
    required String pageId,
    required Offset position,
    String text = '',
    Color? color,
  }) {
    if (_notes.length >= _maxNotes) {
      onNotice?.call('Room me maximum $_maxNotes sticky notes hi ho sakte hain.');
      return null;
    }
    final ts = _nextTs();
    final note = StickyNoteModel(
      id: const Uuid().v4(),
      userId: currentUserId,
      pageId: pageId,
      text: text,
      position: position,
      color: color ?? const Color(StickyNoteModel.defaultColorValue),
      zIndex: _maxZ() + 1,
      fieldTs: {for (final g in StickyGroup.all) g: ts},
      isPending: true,
    );
    _notes[note.id] = note;
    emptyHintDismissed = true;
    if (text.isEmpty) _autofocusId = note.id;
    _enqueue('note_add', note.id, {'note': note.toJson(), 'ts': ts});
    _notify();
    return note;
  }

  void beginDrag(String id) => _localGesture.add(id);

  void dragBy(String id, Offset delta) {
    final n = _notes[id];
    if (n == null) return;
    n.position = n.position + delta;
    _notify();
    _sendDragPreview(n);
  }

  /// Drag khatam — final position persist + (agar upar nahi hai to) note front me.
  void commitMove(String id) {
    if (!_localGesture.remove(id)) return; // onPanEnd + onPanCancel dono aa sakte hain
    final n = _notes[id];
    if (n == null) return;
    final ts = _nextTs();
    n.fieldTs[StickyGroup.pos] = ts;
    final needFront = !_isTop(n);
    if (needFront) {
      n.zIndex = _maxZ() + 1;
      n.fieldTs[StickyGroup.z] = ts;
    }
    _enqueue(
      'note_move',
      id,
      {'noteId': id, 'x': n.position.dx, 'y': n.position.dy, 'ts': ts, if (needFront) 'front': true},
      coalesceKey: 'move|$id',
    );
    _notify();
  }

  void resizeBy(String id, Offset delta) {
    final n = _notes[id];
    if (n == null) return;
    n.size = Size(
      (n.size.width + delta.dx).clamp(StickyNoteModel.minWidth, StickyNoteModel.maxWidth).toDouble(),
      (n.size.height + delta.dy).clamp(StickyNoteModel.minHeight, StickyNoteModel.maxHeight).toDouble(),
    );
    _notify();
    _sendDragPreview(n);
  }

  void commitResize(String id) {
    if (!_localGesture.remove(id)) return;
    final n = _notes[id];
    if (n == null) return;
    final ts = _nextTs();
    n.fieldTs[StickyGroup.size] = ts;
    final needFront = !_isTop(n);
    if (needFront) {
      n.zIndex = _maxZ() + 1;
      n.fieldTs[StickyGroup.z] = ts;
    }
    _enqueue(
      'note_move',
      id,
      {'noteId': id, 'width': n.size.width, 'height': n.size.height, 'ts': ts, if (needFront) 'front': true},
      coalesceKey: 'move|$id',
    );
    _notify();
  }

  /// Har keystroke pe local text badalta hai; server ko 350ms debounce ke baad bhejta hai.
  void setText(String id, String text) {
    final n = _notes[id];
    if (n == null || n.text == text) return;
    n.text = text;
    n.fieldTs[StickyGroup.text] = _nextTs();
    n.isPending = true;
    _dirtyText.add(id);
    _textTimers[id]?.cancel();
    _textTimers[id] = Timer(_textDebounce, () => flushText(id));
  }

  /// Debounce ka intezaar kiye bina pending text abhi bhej do (blur / dispose par).
  void flushText(String id, {bool notify = true}) {
    _textTimers.remove(id)?.cancel();
    if (!_dirtyText.remove(id)) return;
    final n = _notes[id];
    if (n == null) return;
    _enqueue(
      'note_edit',
      id,
      {'noteId': id, 'text': n.text, 'ts': n.fieldTs[StickyGroup.text] ?? _nextTs()},
      coalesceKey: 'edit-text|$id',
    );
    if (notify) _notify();
  }

  void flushAll() {
    for (final id in _dirtyText.toList()) {
      flushText(id);
    }
  }

  void setColor(String id, Color color) {
    final n = _notes[id];
    if (n == null || n.color == color) return;
    final ts = _nextTs();
    n.color = color;
    n.fieldTs[StickyGroup.color] = ts;
    _enqueue(
      'note_edit',
      id,
      {'noteId': id, 'color': color.value, 'ts': ts},
      coalesceKey: 'edit-color|$id',
    );
    _notify();
  }

  /// Touch/focus par: jo note abhi sabse upar nahi hai use front-most banao.
  void bringToFront(String id) {
    final n = _notes[id];
    if (n == null || _isTop(n)) return;
    final ts = _nextTs();
    n.zIndex = _maxZ() + 1;
    n.fieldTs[StickyGroup.z] = ts;
    _enqueue('note_front', id, {'noteId': id, 'ts': ts}, coalesceKey: 'front|$id');
    _notify();
  }

  /// Delete (optimistic). Server "forbidden" bole to note wapas aa jaata hai.
  bool deleteNote(String id) {
    final n = _notes.remove(id);
    if (n == null) return false;
    _deletedSnapshots[id] = n;
    _deletedIds.add(id);
    _textTimers.remove(id)?.cancel();
    _dirtyText.remove(id);
    _pending.removeWhere((_, op) => op.noteId == id && !op.sent && op.action != 'note_delete');
    _enqueue('note_delete', id, {'noteId': id, 'ts': _nextTs()});
    _notify();
    return true;
  }

  /// Board clear / page remove: us page ke notes local se hata do (server
  /// ne relay hue `clear_board`/`remove_page` par DB se khud saaf kar diya).
  void clearPageLocal(String pageId) {
    final ids = _notes.values.where((n) => n.pageId == pageId).map((n) => n.id).toList();
    if (ids.isEmpty) return;
    for (final id in ids) {
      _notes.remove(id);
      _deletedIds.add(id);
      _textTimers.remove(id)?.cancel();
      _dirtyText.remove(id);
    }
    _pending.removeWhere((_, op) => ids.contains(op.noteId));
    _notify();
  }

  void dismissEmptyHint() {
    if (emptyHintDismissed) return;
    emptyHintDismissed = true;
    _notify();
  }

  // ------------------------------------------------------------------
  // Socket events (server -> client)
  // ------------------------------------------------------------------
  void handleEvent(String action, Map<String, dynamic> data) {
    switch (action) {
      case 'note_ack':
        _handleAck(data);
        break;
      case 'note_upsert':
        final server = _parseNote(data['note']);
        if (server == null) return;
        _mergeServer(server);
        _notify();
        break;
      case 'note_removed':
        final id = data['noteId']?.toString();
        if (id == null || id.isEmpty) return;
        _removeRemote(id);
        _notify();
        break;
      case 'note_drag':
        _applyRemoteDrag(data);
        break;
    }
  }

  StickyNoteModel? _parseNote(dynamic raw) {
    if (raw is! Map) return null;
    try {
      return StickyNoteModel.fromJson(raw.cast<String, dynamic>());
    } catch (_) {
      return null;
    }
  }

  void _mergeServer(StickyNoteModel s, {Set<String> force = const {}, bool adoptZ = false}) {
    if (_deletedIds.contains(s.id)) return;
    final local = _notes[s.id];
    if (local == null) {
      _notes[s.id] = s;
      return;
    }
    local.mergeFrom(
      s,
      force: force,
      adoptZ: adoptZ,
      // Main is note ko drag/resize kar raha hoon => remote pos/size mere haath ke neeche na khinche.
      skip: _localGesture.contains(s.id) ? {StickyGroup.pos, StickyGroup.size} : const {},
    );
  }

  void _removeRemote(String id) {
    _notes.remove(id);
    _deletedIds.add(id);
    _textTimers.remove(id)?.cancel();
    _dirtyText.remove(id);
    _pending.removeWhere((_, op) => op.noteId == id);
  }

  void _applyRemoteDrag(Map<String, dynamic> data) {
    final id = data['noteId']?.toString();
    final n = id == null ? null : _notes[id];
    if (n == null || _localGesture.contains(id)) return;
    final x = data['x'];
    final y = data['y'];
    if (x is! num || y is! num) return;
    n.position = Offset(x.toDouble(), y.toDouble());
    final w = data['width'];
    final h = data['height'];
    if (w is num && h is num) n.size = Size(w.toDouble(), h.toDouble());
    _remoteMoving.add(id!);
    _remoteMoveTimers[id]?.cancel();
    _remoteMoveTimers[id] = Timer(_remoteMoveHold, () {
      _remoteMoving.remove(id);
      _remoteMoveTimers.remove(id);
      _notify();
    });
    _notify();
  }

  void _handleAck(Map<String, dynamic> data) {
    final opId = data['opId']?.toString() ?? '';
    final op = _pending.remove(opId);
    final status = data['status']?.toString();
    final reason = data['reason']?.toString();
    final noteId = (data['noteId']?.toString() ?? '').isNotEmpty ? data['noteId'].toString() : (op?.noteId ?? '');
    final server = _parseNote(data['note']);
    final rejected = ((data['rejected'] as List?) ?? const []).map((e) => e.toString()).toSet();

    switch (status) {
      case 'applied':
        if (server != null) _mergeServer(server, adoptZ: true);
        _deletedSnapshots.remove(noteId);
        break;
      case 'partial':
      case 'stale':
        // Server ne hamara purana write reject kiya — us group me server ki value hi sahi.
        if (server != null) _mergeServer(server, force: rejected, adoptZ: true);
        break;
      case 'deleted':
        // Delete ka confirm, ya note kisi aur ne pehle hi hata diya.
        _notes.remove(noteId);
        _deletedIds.add(noteId);
        _deletedSnapshots.remove(noteId);
        break;
      case 'rejected':
        _handleRejected(op, noteId, reason, server);
        break;
    }
    final n = _notes[noteId];
    if (n != null) n.isPending = _pending.values.any((o) => o.noteId == noteId) || _dirtyText.contains(noteId);
    _notify();
  }

  void _handleRejected(_PendingOp? op, String noteId, String? reason, StickyNoteModel? server) {
    switch (reason) {
      case 'rate_limited':
        // Thoda ruk kar wahi op dobara (retry timer bhejega).
        if (op != null) {
          op.sent = false;
          _pending[op.opId] = op;
          _ensureRetryTimer();
        }
        return;
      case 'forbidden':
        if (op?.action == 'note_delete') {
          final restored = server ?? _deletedSnapshots[noteId];
          if (restored != null) {
            _deletedIds.remove(noteId);
            restored.isPending = false;
            _notes[noteId] = restored;
          }
          onNotice?.call('Ye note sirf uska creator ya group admin delete kar sakta hai.');
        } else {
          if (server != null) _mergeServer(server, force: StickyGroup.all.toSet(), adoptZ: true);
          onNotice?.call('Is room me sticky notes use karne ki permission nahi hai.');
        }
        break;
      case 'not_found':
        _notes.remove(noteId);
        break;
      case 'limit_reached':
        _notes.remove(noteId);
        onNotice?.call('Room me maximum $_maxNotes sticky notes hi ho sakte hain.');
        break;
      case 'not_member':
        onNotice?.call('Aap is room ke member nahi hain.');
        break;
      default:
        if (op?.action == 'note_add') {
          _notes.remove(noteId);
          onNotice?.call('Note save nahi ho paya. Dobara try karo.');
        } else {
          // Kuch gadbad — server se sach maang lo.
          load(silent: true);
        }
    }
  }

  // ------------------------------------------------------------------
  // Op queue
  // ------------------------------------------------------------------
  void _enqueue(String action, String noteId, Map<String, dynamic> data, {String? coalesceKey}) {
    if (coalesceKey != null) {
      // Offline rehte hue ek hi cheez (jaise text) baar-baar badle to sirf latest bhejo.
      for (final op in _pending.values) {
        if (op.coalesceKey == coalesceKey && !op.sent) {
          op.data = {...op.data, ...data};
          _trySend(op);
          return;
        }
      }
    }
    final op = _PendingOp(
      opId: 'op_${currentUserId}_${DateTime.now().microsecondsSinceEpoch}_${_seq++}',
      action: action,
      noteId: noteId,
      data: data,
      coalesceKey: coalesceKey,
    );
    _pending[op.opId] = op;
    _trySend(op);
    _ensureRetryTimer();
    _markPending(noteId);
  }

  void _markPending(String noteId) {
    final n = _notes[noteId];
    if (n != null) n.isPending = true;
  }

  void _trySend(_PendingOp op) {
    if (!isSocketConnected()) return;
    op.sent = true;
    op.sentAt = DateTime.now();
    op.attempts++;
    sendEvent(op.action, {...op.data, 'opId': op.opId});
  }

  void _flushPending() {
    for (final op in _pending.values.toList()) {
      if (!op.sent) _trySend(op);
    }
  }

  void _ensureRetryTimer() {
    _retryTimer ??= Timer.periodic(_retryTick, (_) => _retryTickHandler());
  }

  void _retryTickHandler() {
    if (_pending.isEmpty) {
      _retryTimer?.cancel();
      _retryTimer = null;
      return;
    }
    if (!isSocketConnected()) return;
    final now = DateTime.now();
    var gaveUp = false;
    for (final op in _pending.values.toList()) {
      if (!op.sent) {
        _trySend(op);
      } else if (op.sentAt != null && now.difference(op.sentAt!) > _ackTimeout) {
        if (op.attempts >= _maxAttempts) {
          _pending.remove(op.opId);
          gaveUp = true;
        } else {
          _trySend(op); // same opId — server idempotent hai
        }
      }
    }
    if (gaveUp) {
      onNotice?.call('Kuch changes sync nahi ho paye — refresh kar rahe hain.');
      load(silent: true);
    }
  }

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------
  int _maxZ() {
    var top = 0;
    for (final n in _notes.values) {
      if (n.zIndex > top) top = n.zIndex;
    }
    return top;
  }

  bool _isTop(StickyNoteModel n) {
    final top = _maxZ();
    if (n.zIndex < top) return false;
    // Same z par koi aur note ho to tie => front nahi maana jaata.
    return _notes.values.where((o) => o.id != n.id && o.zIndex == top).isEmpty;
  }

  void _sendDragPreview(StickyNoteModel n) {
    final now = DateTime.now();
    final last = _lastDragSent[n.id];
    if (last != null && now.difference(last) < _dragSendInterval) return;
    _lastDragSent[n.id] = now;
    if (!isSocketConnected()) return;
    sendEvent('note_drag', {
      'noteId': n.id,
      'x': n.position.dx,
      'y': n.position.dy,
      'width': n.size.width,
      'height': n.size.height,
    });
  }

  void _scheduleEmptyHint() {
    _hintTimer?.cancel();
    if (_notes.isEmpty && !emptyHintDismissed) {
      _hintTimer = Timer(_emptyHintDuration, dismissEmptyHint);
    }
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _retryTimer?.cancel();
    _hintTimer?.cancel();
    for (final t in _textTimers.values) {
      t.cancel();
    }
    for (final t in _remoteMoveTimers.values) {
      t.cancel();
    }
    super.dispose();
  }
}
