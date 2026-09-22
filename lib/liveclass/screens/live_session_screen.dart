// ============================================================
// LIVECLASS — LIVE SESSION SCREEN
//
// This is the in-room screen: video area + chat + hand-raise +
// reactions + captions + polls + (host-only) moderation controls.
//
// VIDEO: actual audio/video is LiveKit's job, not this screen's — call
// `api.joinSession(id)` to get `{token, url}` from
// ClassSessionViewSet.join(), then hand those to whatever LiveKit
// Flutter SDK version this project pins (livekit_client). The video
// surface below is left as a clearly-marked slot so wiring it up
// doesn't require restructuring this screen.
//
// LIVE UPDATES: chat/reactions/captions/hand-raise are all backed by
// real persisted endpoints (see GAP_ANALYSIS.md), polled here on a
// short timer as a working baseline. If this project already runs a
// Channels/WebSocket connection for the room (the backend has a
// consumer for it), swap `_poll()` for that stream and drop the timer
// — the REST calls stay correct either way since they're the same
// durable log the WS layer is reading from.
// ============================================================

import 'dart:async';
import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';

import '../../widgets/ls_ui.dart';
import '../../widgets/error_widgets.dart';
import '../api/liveclass_api.dart';
import '../api/liveclass_socket.dart';
import '../models/liveclass_models.dart';
import '../widgets/livekit_stage.dart';
import 'breakout_rooms_panel.dart';
import 'moderation_screen.dart';

class LiveSessionScreen extends StatefulWidget {
  final LiveClassApi api;
  final int sessionId;
  final int classroomId;
  final bool isHost;

  /// Response of `classrooms/<id>/start-or-join/` (or `sessions/<id>/join/`)
  /// when the caller already joined — avoids a second join round-trip.
  final Map<String, dynamic>? joinInfo;
  const LiveSessionScreen({
    super.key,
    required this.api,
    required this.sessionId,
    required this.classroomId,
    this.isHost = false,
    this.joinInfo,
  });

  @override
  State<LiveSessionScreen> createState() => _LiveSessionScreenState();
}

class _LiveSessionScreenState extends State<LiveSessionScreen> {
  Timer? _poller;
  bool _connecting = true;
  Object? _connectError;

  List<ChatMessage> _chat = const [];
  List<LivePoll> _polls = const [];
  bool _handRaised = false;
  Map<String, int> _reactionCounts = const {};
  bool _recording = false;
  final _chatCtrl = TextEditingController();

  final LiveKitStageController _stage = LiveKitStageController();
  LiveClassSocket? _socket;
  StreamSubscription<LiveClassSocketEvent>? _sockSub;
  Timer? _debounce;
  int? _participantId;
  String? _waitlistNote;

  @override
  void initState() {
    super.initState();
    _connect();
  }

  Future<void> _connect() async {
    setState(() {
      _connecting = true;
      _connectError = null;
    });
    try {
      // Join (or reuse the join the caller already did) and hand the
      // LiveKit url/token to the stage widget.
      final info = widget.joinInfo ?? await widget.api.joinSession(widget.sessionId);
      final url = info['livekit_url']?.toString();
      final token = info['livekit_token']?.toString();
      final pid = info['participant_id'];
      _participantId = pid is int ? pid : int.tryParse('$pid');
      if (url != null && url.isNotEmpty && token != null && token.isNotEmpty) {
        _waitlistNote = null;
        unawaited(_stage.connect(url: url, token: token, autoPublish: widget.isHost));
      } else {
        // 202 → session full, caller was waitlisted (no token yet).
        _waitlistNote = AppLocalizations.of(context)!.waitlistedNotice;
      }
      await _poll();
      _startSocket();
      // REST poll stays as a slow backstop; the socket delivers the live updates.
      _poller?.cancel();
      _poller = Timer.periodic(const Duration(seconds: 20), (_) => _poll());
      if (mounted) setState(() => _connecting = false);
    } catch (e) {
      setState(() {
        _connectError = e;
        _connecting = false;
      });
    }
  }

  Future<void> _poll() async {
    try {
      final results = await Future.wait([
        widget.api.chatMessages(widget.sessionId),
        widget.api.polls(widget.sessionId),
        widget.api.reactions(widget.sessionId),
      ]);
      if (!mounted) return;
      setState(() {
        _chat = (results[0] as List).map((e) => ChatMessage.fromJson(e as Map<String, dynamic>)).toList();
        _polls = (results[1] as List).map((e) => LivePoll.fromJson(e as Map<String, dynamic>)).toList();
        _reactionCounts = ((results[2] as Map<String, dynamic>)['counts'] as Map?)?.map((k, v) => MapEntry(k.toString(), v as int)) ?? const {};
      });
    } catch (_) {
      // A missed poll tick isn't fatal — next tick retries. Surfacing a
      // toast every 4s on a flaky connection would be worse than silence.
    }
  }

  void _startSocket() {
    _sockSub?.cancel();
    _socket?.close();
    final s = LiveClassSocket.session(widget.sessionId);
    _socket = s;
    _sockSub = s.events.listen((e) {
      if (!mounted) return;
      final name = e.event;
      if (name == 'recording.started') {
        setState(() => _recording = true);
      } else if (name == 'recording.stopped' || name == 'recording.ready') {
        setState(() => _recording = false);
      } else if (name == 'waitlist.promoted') {
        // A seat opened for us — join again to receive the LiveKit token.
        _connect();
      } else if (name == 'socket.closed' && e.payload['code'] == 4403) {
        final t = AppLocalizations.of(context)!;
        lsSnack(context, t.removedFromSession, error: true);
        Navigator.of(context).maybePop();
      } else if (name.startsWith('chat.') || name.startsWith('poll.') || name.startsWith('hand.')) {
        // The event payload is the same serializer output REST returns, but
        // re-reading the durable log keeps ordering/deletes/pins consistent.
        _debounce?.cancel();
        _debounce = Timer(const Duration(milliseconds: 250), _poll);
      }
    });
    s.connect();
  }

  Future<void> _leave() async {
    final pid = _participantId;
    if (pid != null && !widget.isHost) {
      try {
        await widget.api.leaveSession(pid);
      } catch (_) {}
    }
  }

  Future<void> _endClass() async {
    final t = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(t.endClassCta),
        content: Text(t.endClassConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: Text(t.cancel)),
          TextButton(onPressed: () => Navigator.of(ctx).pop(true), child: Text(t.confirm)),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.api.endSession(widget.sessionId);
      if (mounted) Navigator.of(context).pop();
    } catch (e) {
      if (mounted) lsSnack(context, e.toString(), error: true);
    }
  }

  @override
  void dispose() {
    _poller?.cancel();
    _debounce?.cancel();
    _sockSub?.cancel();
    _socket?.close();
    _stage.dispose();
    _chatCtrl.dispose();
    super.dispose();
  }

  Future<void> _sendChat() async {
    final text = _chatCtrl.text.trim();
    if (text.isEmpty) return;
    _chatCtrl.clear();
    try {
      await widget.api.sendChatMessage(widget.sessionId, text);
      await _poll();
    } catch (_) {}
  }

  Future<void> _toggleHand() async {
    final next = !_handRaised;
    setState(() => _handRaised = next);
    try {
      await widget.api.raiseHand(widget.sessionId, raised: next);
    } catch (_) {
      setState(() => _handRaised = !next);
    }
  }

  Future<void> _sendReaction(String emoji) async {
    try {
      await widget.api.sendReaction(widget.sessionId, emoji);
      await _poll();
    } catch (_) {}
  }

  Future<void> _toggleRecording() async {
    try {
      if (_recording) {
        await widget.api.stopRecording(widget.sessionId);
      } else {
        await widget.api.startRecording(widget.sessionId);
      }
      setState(() => _recording = !_recording);
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    final cs = Theme.of(context).colorScheme;

    if (_connecting) {
      return Scaffold(
        backgroundColor: Colors.black,
        body: Center(child: CircularProgressIndicator(color: cs.primary)),
      );
    }
    if (_connectError != null) {
      return Scaffold(
        backgroundColor: lsBg(context),
        appBar: lsAppBar(context, title: t.liveSessionTitle),
        body: ErrorStateWidget(title: t.couldNotJoinSession, retryLabel: t.retry, onRetry: _connect),
      );
    }

    return Scaffold(
      backgroundColor: Colors.black,
      body: SafeArea(
        child: Column(children: [
          // ---- Header ----
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: Row(children: [
              IconButton(
                icon: const Icon(Icons.close_rounded, color: Colors.white),
                onPressed: () async {
                  await _leave();
                  if (context.mounted) Navigator.of(context).pop();
                },
              ),
              LsStatusChip(label: t.liveBadge, color: Colors.red, solid: true),
              if (_recording) ...[
                const SizedBox(width: 6),
                LsStatusChip(label: t.recordingBadge, color: Colors.redAccent, icon: Icons.fiber_manual_record_rounded),
              ],
              const Spacer(),
              if (widget.isHost) ...[
                IconButton(
                  icon: const Icon(Icons.grid_view_rounded, color: Colors.white),
                  tooltip: t.breakoutRoomsTitle,
                  onPressed: () => showModalBottomSheet(
                    context: context,
                    isScrollControlled: true,
                    builder: (_) => BreakoutRoomsPanel(api: widget.api, sessionId: widget.sessionId),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.shield_outlined, color: Colors.white),
                  tooltip: t.moderationTitle,
                  onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => ModerationScreen(api: widget.api, sessionId: widget.sessionId, classroomId: widget.classroomId),
                  )),
                ),
                IconButton(
                  icon: Icon(_recording ? Icons.stop_circle_outlined : Icons.fiber_manual_record_outlined, color: Colors.white),
                  onPressed: _toggleRecording,
                ),
                IconButton(
                  icon: const Icon(Icons.call_end_rounded, color: Colors.redAccent),
                  tooltip: t.endClassCta,
                  onPressed: _endClass,
                ),
              ],
            ]),
          ),
          // ---- Video stage (LiveKit) ----
          Expanded(
            flex: 3,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: LiveKitStage(
                controller: _stage,
                waitingLabel: _waitlistNote ?? t.waitingForTeacher,
              ),
            ),
          ),
          // ---- Poll banner ----
          if (_polls.any((p) => !p.isClosed))
            _PollBanner(poll: _polls.firstWhere((p) => !p.isClosed), api: widget.api, onVoted: _poll),
          // ---- Reaction bar ----
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            child: Row(children: [
              for (final emoji in const ['heart', 'clap', 'laugh', 'wow'])
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: InkWell(
                    onTap: () => _sendReaction(emoji),
                    borderRadius: BorderRadius.circular(20),
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                      decoration: BoxDecoration(color: Colors.white10, borderRadius: BorderRadius.circular(20)),
                      child: Text('${_emojiFor(emoji)} ${_reactionCounts[emoji] ?? 0}', style: const TextStyle(color: Colors.white, fontSize: 12)),
                    ),
                  ),
                ),
              const Spacer(),
              InkWell(
                onTap: _toggleHand,
                borderRadius: BorderRadius.circular(20),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  decoration: BoxDecoration(
                    color: _handRaised ? cs.primary : Colors.white10,
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    const Icon(Icons.back_hand_rounded, size: 15, color: Colors.white),
                    const SizedBox(width: 6),
                    Text(t.raiseHandCta, style: const TextStyle(color: Colors.white, fontSize: 12)),
                  ]),
                ),
              ),
            ]),
          ),
          // ---- Chat ----
          Expanded(
            flex: 4,
            child: Container(
              margin: const EdgeInsets.fromLTRB(12, 0, 12, 8),
              decoration: BoxDecoration(color: const Color(0xFF1A1625), borderRadius: BorderRadius.circular(16)),
              child: Column(children: [
                Expanded(
                  child: ListView.builder(
                    reverse: true,
                    padding: const EdgeInsets.all(10),
                    itemCount: _chat.length,
                    itemBuilder: (context, i) {
                      final m = _chat[_chat.length - 1 - i];
                      if (m.isDeleted) return const SizedBox.shrink();
                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4),
                        child: RichText(
                          text: TextSpan(children: [
                            TextSpan(text: '${m.senderName}: ', style: TextStyle(color: cs.primary, fontSize: 12.5, fontWeight: FontWeight.w700)),
                            TextSpan(text: m.message, style: const TextStyle(color: Colors.white, fontSize: 12.5)),
                          ]),
                        ),
                      );
                    },
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
                  child: Row(children: [
                    Expanded(
                      child: TextField(
                        controller: _chatCtrl,
                        style: const TextStyle(color: Colors.white, fontSize: 13),
                        onSubmitted: (_) => _sendChat(),
                        decoration: InputDecoration(
                          hintText: t.typeMessageHint,
                          hintStyle: const TextStyle(color: Colors.white38),
                          filled: true,
                          fillColor: Colors.white10,
                          contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                          border: OutlineInputBorder(borderRadius: BorderRadius.circular(20), borderSide: BorderSide.none),
                        ),
                      ),
                    ),
                    IconButton(icon: Icon(Icons.send_rounded, color: cs.primary), onPressed: _sendChat),
                  ]),
                ),
              ]),
            ),
          ),
        ]),
      ),
    );
  }

  String _emojiFor(String key) => switch (key) {
        'heart' => '❤️',
        'clap' => '👏',
        'laugh' => '😂',
        'wow' => '😮',
        _ => '👍',
      };
}

class _PollBanner extends StatelessWidget {
  final LivePoll poll;
  final LiveClassApi api;
  final VoidCallback onVoted;
  const _PollBanner({required this.poll, required this.api, required this.onVoted});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: Colors.white10, borderRadius: BorderRadius.circular(14)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(poll.question, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 13)),
        const SizedBox(height: 8),
        ...poll.options.map((o) => Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: InkWell(
                onTap: poll.myVoteOptionId != null
                    ? null
                    : () async {
                        await api.votePoll(poll.id, o.id);
                        onVoted();
                      },
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                  decoration: BoxDecoration(
                    color: poll.myVoteOptionId == o.id ? Colors.white24 : Colors.white10,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Row(children: [
                    Expanded(child: Text(o.text, style: const TextStyle(color: Colors.white, fontSize: 12.5))),
                    Text('${o.votes}', style: const TextStyle(color: Colors.white70, fontSize: 12)),
                  ]),
                ),
              ),
            )),
      ]),
    );
  }
}
