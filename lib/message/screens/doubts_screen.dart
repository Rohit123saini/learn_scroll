// message/screens/doubts_screen.dart
//
// 🎨 NAYA SCREEN — "Doubts" tab per classroom (group).
//
// Do features ek hi screen me:
//   1. Persistent, upvotable question board — students apna doubt post
//      karte hain, dusre "मुझे भी yahi doubt hai" upvote karte hain, list
//      backend se already sabse-upvoted-pehle order me aati hai
//      (`DoubtQuestion.Meta.ordering`), teacher (admin/moderator) upar se
//      answer karta jaata hai.
//   2. "Ask Anonymously" — jab group.allow_anonymous_doubts true hai (group
//      settings se teacher-toggleable, `group_profile_screen.dart` me
//      "Permissions" card ke andar), student doubt post karte waqt
//      anonymous checkbox tick kar sakta hai. Identity DB me kabhi nahi
//      khoti — sirf display-level hide hoti hai, teacher "Reveal" bata ke
//      dekh sakta hai (`DoubtQuestionSerializer.get_author`).
//
// Realtime: naya doubt / upvote / answer / reveal — sab group ke chat
// WebSocket room (`chat_{conversation_id}`) pe `doubt_event` type se
// broadcast hote hain (`ChatConsumer.doubt_broadcast`, consumers.py), isliye
// ye screen apna khud ka `ChatSocketService` connect karta hai (REST se
// list load hone ke baad) aur wahi conversationId use karta hai jo chat
// screen already use kar rahi hai.

import 'package:flutter/material.dart';

import '../models/message_models.dart';
import '../services/doubts_api_service.dart';
import '../services/chat_socket_service.dart';
import '../../services/auth_service.dart';

const _kNavy = Color(0xFF030F27);
const _kAccent = Color(0xFFEE0979);
const _kBg = Color(0xFFF6F7FB);

enum _StatusFilter { all, unanswered, answered }

class DoubtsScreen extends StatefulWidget {
  final String groupId;
  final String conversationId;
  final String groupName;

  const DoubtsScreen({
    super.key,
    required this.groupId,
    required this.conversationId,
    this.groupName = 'Doubts',
  });

  @override
  State<DoubtsScreen> createState() => _DoubtsScreenState();
}

class _DoubtsScreenState extends State<DoubtsScreen> {
  bool _loading = true;
  String? _loadError;
  String? _myUserId;
  bool _isTeacher = false; // admin ya moderator
  bool _allowAnonymousDoubts = true;

  List<DoubtQuestionModel> _doubts = [];
  String? _nextPage;
  bool _loadingMore = false;
  _StatusFilter _filter = _StatusFilter.all;

  final Set<String> _busyIds = {}; // per-doubt inline spinners ke liye
  final ChatSocketService _socket = ChatSocketService();

  @override
  void initState() {
    super.initState();
    _load();
    _socket.connect(widget.conversationId);
    _socket.events.listen(_onSocketEvent);
  }

  @override
  void dispose() {
    _socket.dispose();
    super.dispose();
  }

  void _onSocketEvent(Map<String, dynamic> event) {
    if (event['type'] != 'doubt_event') return;
    final raw = event['doubt'];
    if (raw is! Map) return;
    final incoming = DoubtQuestionModel.fromJson(Map<String, dynamic>.from(raw));
    if (!mounted) return;
    setState(() {
      final idx = _doubts.indexWhere((d) => d.id == incoming.id);
      if (idx == -1) {
        // naya doubt — sirf tab list me daalo jab current filter se match
        // karta ho (e.g. "Unanswered" filter khula ho to naya doubt
        // hamesha match karega, kyunki naya doubt kabhi answered nahi hota).
        if (_matchesFilter(incoming)) _doubts.insert(0, incoming);
      } else if (_matchesFilter(incoming)) {
        _doubts[idx] = incoming;
      } else {
        _doubts.removeAt(idx); // e.g. answered ho gaya, "Unanswered" filter me se hat gaya
      }
      _doubts.sort((a, b) {
        final byVotes = b.upvotesCount.compareTo(a.upvotesCount);
        return byVotes != 0 ? byVotes : b.createdAt.compareTo(a.createdAt);
      });
    });
  }

  bool _matchesFilter(DoubtQuestionModel d) {
    switch (_filter) {
      case _StatusFilter.answered:
        return d.isAnswered;
      case _StatusFilter.unanswered:
        return !d.isAnswered;
      case _StatusFilter.all:
        return true;
    }
  }

  String? get _statusParam {
    switch (_filter) {
      case _StatusFilter.answered:
        return 'answered';
      case _StatusFilter.unanswered:
        return 'unanswered';
      case _StatusFilter.all:
        return null;
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadError = null;
    });
    try {
      final myId = await AuthService.getUserId();
      final group = await DoubtsApiService.getGroup(widget.groupId);
      final members = (group['members'] as List? ?? []);
      String? myRole;
      for (final m in members) {
        if (m is Map && m['user'] is Map && (m['user']['id']?.toString() == myId)) {
          myRole = m['role']?.toString();
          break;
        }
      }
      final result = await DoubtsApiService.getDoubts(widget.groupId, status: _statusParam);
      if (!mounted) return;
      setState(() {
        _myUserId = myId;
        _isTeacher = myRole == 'admin' || myRole == 'moderator';
        _allowAnonymousDoubts = group['allow_anonymous_doubts'] != false;
        _doubts = result.doubts;
        _nextPage = result.nextPage;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _loadError = "Doubts load nahi ho paaye: $e";
      });
    }
  }

  Future<void> _loadMore() async {
    if (_nextPage == null || _loadingMore) return;
    setState(() => _loadingMore = true);
    try {
      final result = await DoubtsApiService.getDoubts(widget.groupId, pageUrl: _nextPage);
      if (!mounted) return;
      setState(() {
        _doubts.addAll(result.doubts);
        _nextPage = result.nextPage;
        _loadingMore = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loadingMore = false);
    }
  }

  void _changeFilter(_StatusFilter f) {
    if (f == _filter) return;
    setState(() => _filter = f);
    _load();
  }

  Future<void> _toggleUpvote(DoubtQuestionModel d) async {
    if (_busyIds.contains(d.id)) return;
    setState(() => _busyIds.add(d.id));
    // optimistic update
    final prev = d;
    final optimistic = d.copyWith(
      upvotedByMe: !d.upvotedByMe,
      upvotesCount: d.upvotesCount + (d.upvotedByMe ? -1 : 1),
    );
    _replaceDoubt(optimistic);
    try {
      final updated = d.upvotedByMe
          ? await DoubtsApiService.removeUpvote(widget.groupId, d.id)
          : await DoubtsApiService.upvote(widget.groupId, d.id);
      _replaceDoubt(updated);
    } catch (e) {
      _replaceDoubt(prev); // rollback
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Upvote fail: $e")));
    } finally {
      if (mounted) setState(() => _busyIds.remove(d.id));
    }
  }

  void _replaceDoubt(DoubtQuestionModel updated) {
    if (!mounted) return;
    setState(() {
      final idx = _doubts.indexWhere((x) => x.id == updated.id);
      if (idx != -1) _doubts[idx] = updated;
      _doubts.sort((a, b) {
        final byVotes = b.upvotesCount.compareTo(a.upvotesCount);
        return byVotes != 0 ? byVotes : b.createdAt.compareTo(a.createdAt);
      });
    });
  }

  Future<void> _revealAuthor(DoubtQuestionModel d) async {
    if (_busyIds.contains(d.id)) return;
    setState(() => _busyIds.add(d.id));
    try {
      final updated = await DoubtsApiService.reveal(widget.groupId, d.id);
      _replaceDoubt(updated);
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Reveal fail: $e")));
    } finally {
      if (mounted) setState(() => _busyIds.remove(d.id));
    }
  }

  Future<void> _answerDoubt(DoubtQuestionModel d) async {
    final ctrl = TextEditingController();
    final answerText = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(
          left: 18, right: 18, top: 18,
          bottom: 18 + MediaQuery.of(ctx).viewInsets.bottom,
        ),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text("Answer this doubt", style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15, color: _kNavy)),
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(color: _kBg, borderRadius: BorderRadius.circular(10)),
            child: Text(d.text, style: const TextStyle(fontSize: 13.5, color: Colors.black87)),
          ),
          const SizedBox(height: 14),
          TextField(
            controller: ctrl,
            autofocus: true,
            maxLines: 5,
            minLines: 3,
            decoration: InputDecoration(
              hintText: "Type your answer…",
              filled: true,
              fillColor: _kBg,
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide.none),
            ),
          ),
          const SizedBox(height: 14),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              style: ElevatedButton.styleFrom(backgroundColor: _kAccent, foregroundColor: Colors.white, padding: const EdgeInsets.symmetric(vertical: 13)),
              onPressed: () {
                final v = ctrl.text.trim();
                if (v.isEmpty) return;
                Navigator.pop(ctx, v);
              },
              child: const Text("Send answer"),
            ),
          ),
        ]),
      ),
    );
    if (answerText == null || answerText.isEmpty) return;

    setState(() => _busyIds.add(d.id));
    try {
      final updated = await DoubtsApiService.answer(widget.groupId, d.id, answerText);
      _replaceDoubt(updated);
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Answer fail: $e")));
    } finally {
      if (mounted) setState(() => _busyIds.remove(d.id));
    }
  }

  Future<void> _openAskSheet() async {
    final ctrl = TextEditingController();
    bool anonymous = false;
    final created = await showModalBottomSheet<DoubtQuestionModel>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheetState) => Padding(
          padding: EdgeInsets.only(
            left: 18, right: 18, top: 18,
            bottom: 18 + MediaQuery.of(ctx).viewInsets.bottom,
          ),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text("Ask a doubt", style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15, color: _kNavy)),
            const SizedBox(height: 12),
            TextField(
              controller: ctrl,
              autofocus: true,
              maxLines: 5,
              minLines: 3,
              decoration: InputDecoration(
                hintText: "Type your doubt…",
                filled: true,
                fillColor: _kBg,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide.none),
              ),
            ),
            if (_allowAnonymousDoubts)
              CheckboxListTile(
                value: anonymous,
                onChanged: (v) => setSheetState(() => anonymous = v ?? false),
                controlAffinity: ListTileControlAffinity.leading,
                contentPadding: EdgeInsets.zero,
                title: const Text("Ask anonymously", style: TextStyle(fontSize: 13.5)),
                subtitle: Text(
                  "Your name is hidden from everyone, including the teacher, unless they choose to reveal it.",
                  style: TextStyle(fontSize: 11, color: Colors.grey[600]),
                ),
              ),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                style: ElevatedButton.styleFrom(backgroundColor: _kAccent, foregroundColor: Colors.white, padding: const EdgeInsets.symmetric(vertical: 13)),
                onPressed: () async {
                  final v = ctrl.text.trim();
                  if (v.isEmpty) return;
                  try {
                    final doubt = await DoubtsApiService.createDoubt(
                      widget.groupId, text: v, isAnonymous: anonymous,
                    );
                    Navigator.pop(ctx, doubt);
                  } catch (e) {
                    if (ctx.mounted) {
                      ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text("Post fail: $e")));
                    }
                  }
                },
                child: const Text("Post doubt"),
              ),
            ),
          ]),
        ),
      ),
    );
    if (created != null && mounted && _matchesFilter(created)) {
      setState(() {
        if (!_doubts.any((d) => d.id == created.id)) _doubts.insert(0, created);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      appBar: AppBar(
        backgroundColor: _kNavy,
        iconTheme: const IconThemeData(color: Colors.white),
        title: Text("Doubts · ${widget.groupName}", style: const TextStyle(color: Colors.white, fontSize: 16)),
      ),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: _kAccent,
        onPressed: _openAskSheet,
        icon: const Icon(Icons.help_outline_rounded, color: Colors.white),
        label: const Text("Ask a doubt", style: TextStyle(color: Colors.white)),
      ),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          child: Row(children: [
            _filterChip("All", _StatusFilter.all),
            const SizedBox(width: 8),
            _filterChip("Unanswered", _StatusFilter.unanswered),
            const SizedBox(width: 8),
            _filterChip("Answered", _StatusFilter.answered),
          ]),
        ),
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator(color: _kNavy))
              : _loadError != null
                  ? Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(_loadError!, style: const TextStyle(color: Colors.red))))
                  : _doubts.isEmpty
                      ? _buildEmpty()
                      : RefreshIndicator(
                          onRefresh: _load,
                          child: NotificationListener<ScrollNotification>(
                            onNotification: (n) {
                              if (n.metrics.pixels > n.metrics.maxScrollExtent - 200) _loadMore();
                              return false;
                            },
                            child: ListView.builder(
                              padding: const EdgeInsets.fromLTRB(12, 4, 12, 90),
                              itemCount: _doubts.length + (_nextPage != null ? 1 : 0),
                              itemBuilder: (ctx, i) {
                                if (i >= _doubts.length) {
                                  return const Padding(
                                    padding: EdgeInsets.all(16),
                                    child: Center(child: CircularProgressIndicator(strokeWidth: 2, color: _kNavy)),
                                  );
                                }
                                return _buildDoubtCard(_doubts[i]);
                              },
                            ),
                          ),
                        ),
        ),
      ]),
    );
  }

  Widget _filterChip(String label, _StatusFilter f) {
    final selected = _filter == f;
    return ChoiceChip(
      label: Text(label, style: TextStyle(fontSize: 12.5, color: selected ? Colors.white : _kNavy)),
      selected: selected,
      selectedColor: _kNavy,
      backgroundColor: Colors.white,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20), side: BorderSide(color: Colors.grey.shade300)),
      onSelected: (_) => _changeFilter(f),
    );
  }

  Widget _buildEmpty() {
    return ListView(children: [
      const SizedBox(height: 80),
      Icon(Icons.help_outline_rounded, size: 46, color: Colors.grey[400]),
      const SizedBox(height: 12),
      Center(
        child: Text(
          _filter == _StatusFilter.answered
              ? "No answered doubts yet."
              : "No doubts here yet — be the first to ask.",
          style: TextStyle(color: Colors.grey[600], fontSize: 13),
        ),
      ),
    ]);
  }

  Widget _buildDoubtCard(DoubtQuestionModel d) {
    final busy = _busyIds.contains(d.id);
    final canReveal = _isTeacher && d.isAnonymous && !d.isRevealed;
    final showRevealHint = _isTeacher && d.isAnonymous && d.author == null && !d.isRevealed;

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.04), blurRadius: 8, offset: const Offset(0, 2))],
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          // ---------------- UPVOTE ----------------
          InkWell(
            onTap: busy ? null : () => _toggleUpvote(d),
            borderRadius: BorderRadius.circular(10),
            child: Container(
              width: 46,
              padding: const EdgeInsets.symmetric(vertical: 8),
              decoration: BoxDecoration(
                color: d.upvotedByMe ? _kAccent.withOpacity(0.12) : _kBg,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Column(children: [
                Icon(Icons.arrow_upward_rounded, size: 18, color: d.upvotedByMe ? _kAccent : Colors.grey[500]),
                Text('${d.upvotesCount}', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: d.upvotedByMe ? _kAccent : _kNavy)),
              ]),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Icon(d.isAnonymous ? Icons.visibility_off_rounded : Icons.person_rounded, size: 13, color: Colors.grey[500]),
                const SizedBox(width: 4),
                Expanded(
                  child: Text(
                    d.displayName(_myUserId ?? ''),
                    style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: Colors.grey[600]),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (d.isAnswered)
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(color: Colors.green.withOpacity(0.12), borderRadius: BorderRadius.circular(20)),
                    child: const Text("Answered", style: TextStyle(fontSize: 10, color: Colors.green, fontWeight: FontWeight.w700)),
                  ),
              ]),
              const SizedBox(height: 6),
              Text(d.text, style: const TextStyle(fontSize: 14, color: _kNavy)),
              if (canReveal)
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: InkWell(
                    onTap: busy ? null : () => _revealAuthor(d),
                    child: Row(mainAxisSize: MainAxisSize.min, children: [
                      Icon(Icons.remove_red_eye_outlined, size: 14, color: _kAccent),
                      const SizedBox(width: 4),
                      Text("Reveal who asked", style: TextStyle(fontSize: 11.5, color: _kAccent, fontWeight: FontWeight.w600)),
                    ]),
                  ),
                )
              else if (showRevealHint)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text("Identity hidden until revealed", style: TextStyle(fontSize: 10.5, color: Colors.grey[500])),
                ),
              if (d.isAnswered) ...[
                const SizedBox(height: 8),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(color: _kBg, borderRadius: BorderRadius.circular(10)),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(
                      d.answeredBy?.displayName ?? 'Teacher',
                      style: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: _kNavy),
                    ),
                    const SizedBox(height: 3),
                    Text(d.answerText, style: const TextStyle(fontSize: 13, color: Colors.black87)),
                  ]),
                ),
              ] else if (_isTeacher) ...[
                const SizedBox(height: 8),
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton.icon(
                    onPressed: busy ? null : () => _answerDoubt(d),
                    icon: const Icon(Icons.reply_rounded, size: 16, color: _kAccent),
                    label: const Text("Answer", style: TextStyle(color: _kAccent, fontWeight: FontWeight.w600)),
                  ),
                ),
              ],
            ]),
          ),
        ]),
      ]),
    );
  }
}