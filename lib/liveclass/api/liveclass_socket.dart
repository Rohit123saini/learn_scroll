// ============================================================
// LIVECLASS — REALTIME SOCKET
//
// One small client for the three Channels routes in `liveclass/routing.py`:
//
//   ws/liveclass/session/<id>/     chat.*, poll.*, hand.*, recording.*,
//                                  presence.*, waitlist.promoted,
//                                  participant.kicked, chat.typing
//   ws/liveclass/user/             join_request.created / .decided, staff.added
//   ws/liveclass/classroom/<id>/   classroom.stats
//
// Auth: same JWT-in-query-string scheme as message/ chat sockets
// (`?token=<access>` — see LearnScroll/ws_auth.py).
// Frame shape (consumers.py): {"event": "<dotted.name>", "payload": {...}, "ts": <unix>}
// plus a `connection.ack` frame carrying `server_time`.
//
// Reconnect: exponential backoff (cap 30s) and `?since=<last ts>` so the
// session consumer replays anything missed while we were offline.
// Keep-alive: `{"type":"ping"}` every 25s — the session consumer evicts a
// socket that stays silent for 90s (idle watchdog).
// Close codes 4401/4403/4404 (auth / kicked / not found) are terminal — no retry.
// ============================================================

import 'dart:async';
import 'dart:convert';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../services/auth_service.dart';
import '../../utils/api.dart';

class LiveClassSocketEvent {
  final String event;
  final Map<String, dynamic> payload;
  final double? ts;
  final bool replayed;
  const LiveClassSocketEvent(this.event, this.payload, this.ts, this.replayed);
}

class LiveClassSocket {
  /// Path after the host, e.g. `/ws/liveclass/session/12/`.
  final String path;
  LiveClassSocket._(this.path);

  factory LiveClassSocket.session(int sessionId) => LiveClassSocket._('/ws/liveclass/session/$sessionId/');
  factory LiveClassSocket.user() => LiveClassSocket._('/ws/liveclass/user/');
  factory LiveClassSocket.classroom(int classroomId) => LiveClassSocket._('/ws/liveclass/classroom/$classroomId/');

  final _events = StreamController<LiveClassSocketEvent>.broadcast();
  Stream<LiveClassSocketEvent> get events => _events.stream;

  /// True while a live connection is open (after `connection.ack`).
  bool connected = false;

  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  Timer? _ping;
  Timer? _retry;
  bool _closedByUs = false;
  bool _connecting = false;
  int _attempts = 0;
  double? _lastTs;

  static String _wsBase() {
    final base = Api.baseUrl;
    if (base.startsWith('https://')) return base.replaceFirst('https://', 'wss://');
    if (base.startsWith('http://')) return base.replaceFirst('http://', 'ws://');
    return base;
  }

  Future<void> connect() async {
    if (_connecting || connected || _closedByUs) return;
    _connecting = true;
    try {
      final token = await AuthService.getValidToken();
      if (token == null || token.isEmpty) {
        _connecting = false;
        _scheduleRetry();
        return;
      }
      final since = _lastTs != null ? '&since=${_lastTs!.toStringAsFixed(3)}' : '';
      final uri = Uri.parse('${_wsBase()}$path?token=$token$since');
      final ch = WebSocketChannel.connect(uri);
      _channel = ch;
      _sub = ch.stream.listen(
        _onFrame,
        onDone: () => _onClosed(ch.closeCode),
        onError: (_) => _onClosed(null),
        cancelOnError: true,
      );
    } catch (_) {
      _connecting = false;
      _scheduleRetry();
    }
  }

  void _onFrame(dynamic raw) {
    Map<String, dynamic> data;
    try {
      final decoded = jsonDecode(raw as String);
      if (decoded is! Map) return;
      data = Map<String, dynamic>.from(decoded);
    } catch (_) {
      return;
    }
    final event = data['event']?.toString();
    if (event == null) return; // e.g. {"type":"pong"} / error frames
    final ts = (data['ts'] is num) ? (data['ts'] as num).toDouble() : null;
    if (event == 'connection.ack') {
      connected = true;
      _connecting = false;
      _attempts = 0;
      // Fresh baseline only on our very first connect; afterwards keep the
      // last-seen event ts so a reconnect replays what we missed.
      final st = data['server_time'];
      if (_lastTs == null && st is num) _lastTs = st.toDouble();
      _startPing();
    } else if (ts != null) {
      _lastTs = ts;
    }
    final payload = data['payload'] is Map ? Map<String, dynamic>.from(data['payload'] as Map) : <String, dynamic>{};
    if (!_events.isClosed) {
      _events.add(LiveClassSocketEvent(event, payload, ts, data['replayed'] == true || payload['replayed'] == true));
    }
  }

  void _startPing() {
    _ping?.cancel();
    _ping = Timer.periodic(const Duration(seconds: 25), (_) => send({'type': 'ping'}));
  }

  /// Only two client → server frames exist: ping and (session socket) typing.
  void send(Map<String, dynamic> frame) {
    try {
      _channel?.sink.add(jsonEncode(frame));
    } catch (_) {}
  }

  void sendTyping() => send({'type': 'typing'});

  void _onClosed(int? code) {
    connected = false;
    _connecting = false;
    _ping?.cancel();
    _sub?.cancel();
    _sub = null;
    _channel = null;
    if (_closedByUs) return;
    if (code == 4401 || code == 4403 || code == 4404) {
      // Unauthenticated / kicked / no such object — retrying can't help.
      if (!_events.isClosed) {
        _events.add(LiveClassSocketEvent('socket.closed', {'code': code}, null, false));
      }
      return;
    }
    _scheduleRetry();
  }

  void _scheduleRetry() {
    if (_closedByUs) return;
    _retry?.cancel();
    final secs = (1 << _attempts.clamp(0, 5).toInt()).clamp(1, 30).toInt();
    _attempts++;
    _retry = Timer(Duration(seconds: secs), connect);
  }

  Future<void> close() async {
    _closedByUs = true;
    connected = false;
    _ping?.cancel();
    _retry?.cancel();
    await _sub?.cancel();
    try {
      await _channel?.sink.close();
    } catch (_) {}
    await _events.close();
  }
}
