// message/screens/message_search_screen.dart
//
// 🔥 NAYA (Phase 4, §1 #1, §2.1 — FRONTEND_INTEGRATION_ARCHITECTURE.md) —
// Message Search. Ek hi screen, do modes:
//
//  - `conversationId` diya gaya  -> single-conversation search
//    (`GET /conversations/<id>/search/`). Result tap karne pe screen
//    seedha us message ka `id` le kar POP ho jaati hai — `chat_screen.dart`
//    khud `ChatScreen.jumpToMessageId`/`_tryJumpToMessageId` se scroll +
//    highlight karta hai (dekho chat_screen.dart app-bar search icon).
//
//  - `conversationId` null      -> global search, saari conversations me
//    (`GET /search_all/`). Har result ke saath `conversation_preview` bhi
//    aata hai; tap karne pe us conversation ka poora `ConversationModel`
//    fetch karke (`getConversation`) seedha `ChatScreen` me PUSH karte hue
//    `jumpToMessageId` pass kar dete hain.
//
// Filters (sender/date range/media type/has-media) ek bottom-sheet me hain
// (`_SearchFiltersSheet`) — apply hote hi current query re-run hoti hai.
//
// NOTE: backend kam se kam 2-char query pe hi 200 deta hai, warna 400 —
// isliye client-side bhi 2-char se pehle search fire nahi karte.

import 'dart:async';

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../models/message_models.dart';
import '../services/message_api_service.dart';
import 'chat_screen.dart';

const Color _kNavy = Color(0xFF030F27);
const Color _kAccent = Color(0xFF3D7EFF);

class MessageSearchScreen extends StatefulWidget {
  /// Null = global search (saari conversations me).
  final String? conversationId;

  const MessageSearchScreen({super.key, this.conversationId});

  @override
  State<MessageSearchScreen> createState() => _MessageSearchScreenState();
}

class _MessageSearchScreenState extends State<MessageSearchScreen> {
  final TextEditingController _queryController = TextEditingController();
  final FocusNode _focusNode = FocusNode();
  Timer? _debounce;

  SearchFilterModel _filters = SearchFilterModel();
  bool _loading = false;
  bool _searchedOnce = false;
  String? _error;
  String _lastQuery = '';

  // Single-conversation mode ke results bhi isi list me store karte hain
  // (`conversationPreview` un entries me hamesha null rehta hai) — taaki
  // list-building/UI code duplicate na ho.
  List<SearchResultModel> _results = [];

  bool get _isGlobal => widget.conversationId == null;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _focusNode.requestFocus();
    });
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _queryController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _onQueryChanged(String value) {
    setState(() {}); // suffix clear-icon show/hide turant update ho
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), () => _runSearch(value));
  }

  Future<void> _runSearch(String value) async {
    final q = value.trim();
    _lastQuery = q;

    if (q.length < 2) {
      setState(() {
        _results = [];
        _error = null;
        _loading = false;
        _searchedOnce = false;
      });
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
      _searchedOnce = true;
    });

    try {
      List<SearchResultModel> results;
      if (_isGlobal) {
        results = await MessageApiService.searchAllMessages(q, filters: _filters);
      } else {
        final msgs = await MessageApiService.searchMessages(
          widget.conversationId!,
          q,
          filters: _filters,
        );
        results = msgs.map((m) => SearchResultModel(message: m)).toList();
      }

      // Stale response guard — user ne tab tak aur type kar diya ho sakta hai.
      if (!mounted || q != _lastQuery) return;
      setState(() {
        _results = results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted || q != _lastQuery) return;
      final throttled = e is MessageApiException && e.statusCode == 429;
      setState(() {
        _loading = false;
        _results = [];
        _error = throttled
            ? "Too many searches — please wait a moment and try again."
            : "Couldn't search right now. Try again.";
      });
    }
  }

  void _rerunCurrentQuery() => _runSearch(_queryController.text);

  Future<void> _openFilters() async {
    final updated = await showModalBottomSheet<SearchFilterModel>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (_) => _SearchFiltersSheet(initial: _filters),
    );
    if (updated != null) {
      setState(() => _filters = updated);
      _rerunCurrentQuery();
    }
  }

  Future<void> _onResultTap(SearchResultModel result) async {
    if (_isGlobal) {
      final preview = result.conversationPreview;
      if (preview == null) return;
      // Loading feedback — global search se conversation open hone me ek
      // extra REST round-trip lagta hai (poora ConversationModel chahiye,
      // preview me sirf id/name/photo hote hain).
      showDialog(
        context: context,
        barrierDismissible: false,
        builder: (_) => const Center(child: CircularProgressIndicator(color: _kAccent)),
      );
      try {
        final conversation = await MessageApiService.getConversation(preview.id);
        if (!mounted) return;
        Navigator.pop(context); // loading dialog band karo
        Navigator.pushReplacement(
          context,
          MaterialPageRoute(
            builder: (_) => ChatScreen(conversation: conversation, jumpToMessageId: result.message.id),
          ),
        );
      } catch (_) {
        if (!mounted) return;
        Navigator.pop(context); // loading dialog band karo
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text("Couldn't open that chat right now.")),
        );
      }
    } else {
      Navigator.pop(context, result.message.id);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.grey[50],
      appBar: AppBar(
        backgroundColor: _kNavy,
        elevation: 0,
        titleSpacing: 0,
        title: Container(
          height: 40,
          margin: const EdgeInsets.only(right: 8),
          decoration: BoxDecoration(color: Colors.white.withOpacity(0.12), borderRadius: BorderRadius.circular(10)),
          child: TextField(
            controller: _queryController,
            focusNode: _focusNode,
            onChanged: _onQueryChanged,
            textInputAction: TextInputAction.search,
            style: const TextStyle(color: Colors.white, fontSize: 14.5),
            cursorColor: Colors.white,
            decoration: InputDecoration(
              hintText: _isGlobal ? "Search all chats" : "Search in this chat",
              hintStyle: const TextStyle(color: Colors.white54, fontSize: 14.5),
              border: InputBorder.none,
              contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              suffixIcon: _queryController.text.isNotEmpty
                  ? IconButton(
                      icon: const Icon(Icons.close, color: Colors.white70, size: 18),
                      onPressed: () {
                        _queryController.clear();
                        _debounce?.cancel();
                        setState(() {
                          _results = [];
                          _searchedOnce = false;
                          _error = null;
                        });
                      },
                    )
                  : null,
            ),
          ),
        ),
        actions: [
          IconButton(
            icon: Stack(clipBehavior: Clip.none, children: [
              const Icon(Icons.tune, color: Colors.white),
              if (!_filters.isEmpty)
                Positioned(
                  right: -1,
                  top: -1,
                  child: Container(
                    width: 8,
                    height: 8,
                    decoration: const BoxDecoration(color: _kAccent, shape: BoxShape.circle),
                  ),
                ),
            ]),
            tooltip: "Filters",
            onPressed: _openFilters,
          ),
        ],
      ),
      body: Column(children: [
        if (!_filters.isEmpty) _buildActiveFilterChips(),
        Expanded(child: _buildBody()),
      ]),
    );
  }

  Widget _buildActiveFilterChips() {
    final chips = <Widget>[];
    if (_filters.mediaType != null) {
      chips.add(_chip("Type: ${_filters.mediaType}", () => _filters = _filters.copyWith(clearMediaType: true)));
    }
    if (_filters.hasMedia == true) {
      chips.add(_chip("Has media", () => _filters = _filters.copyWith(clearHasMedia: true)));
    }
    if (_filters.dateFrom != null) {
      chips.add(_chip("From ${_fmtDate(_filters.dateFrom!)}", () => _filters = _filters.copyWith(clearDateFrom: true)));
    }
    if (_filters.dateTo != null) {
      chips.add(_chip("To ${_fmtDate(_filters.dateTo!)}", () => _filters = _filters.copyWith(clearDateTo: true)));
    }
    if (_filters.sender != null) {
      chips.add(_chip("Sender", () => _filters = _filters.copyWith(clearSender: true)));
    }
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 0),
      child: Wrap(spacing: 6, runSpacing: 6, children: chips),
    );
  }

  Widget _chip(String label, VoidCallback onRemove) {
    return Chip(
      label: Text(label, style: const TextStyle(fontSize: 11.5)),
      backgroundColor: _kAccent.withOpacity(0.1),
      deleteIcon: const Icon(Icons.close, size: 14),
      onDeleted: () => setState(() {
        onRemove();
        _rerunCurrentQuery();
      }),
      visualDensity: VisualDensity.compact,
      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
      side: BorderSide.none,
    );
  }

  String _fmtDate(DateTime d) => "${d.day}/${d.month}/${d.year}";

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator(color: _kAccent));
    }
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Text(_error!, textAlign: TextAlign.center, style: TextStyle(color: Colors.grey[600], fontSize: 13)),
        ),
      );
    }
    if (!_searchedOnce) {
      return Center(
        child: Text(
          "Type at least 2 characters to search",
          style: TextStyle(color: Colors.grey[500], fontSize: 13),
        ),
      );
    }
    if (_results.isEmpty) {
      return Center(
        child: Text(
          "No messages found",
          style: TextStyle(color: Colors.grey[500], fontSize: 13),
        ),
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.symmetric(vertical: 6),
      itemCount: _results.length,
      separatorBuilder: (_, __) => Divider(height: 1, color: Colors.grey[200], indent: 72),
      itemBuilder: (context, i) => _buildResultTile(_results[i]),
    );
  }

  Widget _buildResultTile(SearchResultModel result) {
    final msg = result.message;
    final preview = result.conversationPreview;
    final senderName = msg.sender?.displayName ?? 'Unknown';
    final senderPhoto = msg.sender?.profilePhoto;

    return ListTile(
      leading: CircleAvatar(
        radius: 22,
        backgroundColor: Colors.grey[300],
        backgroundImage: (senderPhoto != null && senderPhoto.isNotEmpty) ? CachedNetworkImageProvider(senderPhoto) : null,
        child: (senderPhoto == null || senderPhoto.isEmpty)
            ? Text(senderName.isNotEmpty ? senderName[0].toUpperCase() : '?', style: const TextStyle(color: Colors.black54, fontWeight: FontWeight.w600))
            : null,
      ),
      title: Row(children: [
        Expanded(child: Text(senderName, style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600), maxLines: 1, overflow: TextOverflow.ellipsis)),
        const SizedBox(width: 6),
        Text(_fmtTime(msg.createdAt), style: TextStyle(fontSize: 10.5, color: Colors.grey[500])),
      ]),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (preview != null)
            Padding(
              padding: const EdgeInsets.only(top: 2, bottom: 2),
              child: Row(children: [
                Icon(preview.type == 'group' ? Icons.group : Icons.person, size: 12, color: _kAccent),
                const SizedBox(width: 3),
                Flexible(child: Text(preview.name, style: const TextStyle(fontSize: 11, color: _kAccent, fontWeight: FontWeight.w600), maxLines: 1, overflow: TextOverflow.ellipsis)),
              ]),
            ),
          Text(_snippetFor(msg), maxLines: 2, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12.5, color: Colors.grey[700])),
        ],
      ),
      onTap: () => _onResultTap(result),
    );
  }

  String _fmtTime(DateTime d) {
    try {
      return timeago.format(d, allowFromNow: true);
    } catch (_) {
      return "${d.day}/${d.month}/${d.year}";
    }
  }

  String _snippetFor(MessageModel msg) {
    switch (msg.type) {
      case MessageType.image:
        return "📷 Photo${_withCaption(msg.text)}";
      case MessageType.video:
        return "🎥 Video${_withCaption(msg.text)}";
      case MessageType.audio:
        return "🎙️ Voice message";
      case MessageType.file:
        return "📄 File${_withCaption(msg.text)}";
      case MessageType.presentation:
        return "📊 Presentation${_withCaption(msg.text)}";
      case MessageType.location:
        return "📍 Location";
      case MessageType.poll:
        return "📊 ${(msg.text?.trim().isNotEmpty == true) ? msg.text!.trim() : 'Poll'}";
      case MessageType.studyRoom:
        return "🧑‍🎓 Study Room invite";
      default:
        return (msg.text != null && msg.text!.trim().isNotEmpty) ? msg.text!.trim() : "(no text)";
    }
  }

  String _withCaption(String? text) => (text != null && text.trim().isNotEmpty) ? " — ${text.trim()}" : "";
}

// ==========================================================================
// FILTER BOTTOM SHEET — sender / date range / media type / has-media
// ==========================================================================
class _SearchFiltersSheet extends StatefulWidget {
  final SearchFilterModel initial;
  const _SearchFiltersSheet({required this.initial});

  @override
  State<_SearchFiltersSheet> createState() => _SearchFiltersSheetState();
}

class _SearchFiltersSheetState extends State<_SearchFiltersSheet> {
  late SearchFilterModel _draft;
  final TextEditingController _senderController = TextEditingController();
  List<Map<String, dynamic>> _senderSuggestions = [];
  Timer? _senderDebounce;
  String? _selectedSenderLabel;

  static const List<String> _mediaTypes = ['image', 'video', 'audio', 'file', 'presentation'];

  @override
  void initState() {
    super.initState();
    _draft = widget.initial;
  }

  @override
  void dispose() {
    _senderDebounce?.cancel();
    _senderController.dispose();
    super.dispose();
  }

  void _onSenderChanged(String value) {
    _senderDebounce?.cancel();
    if (value.trim().length < 2) {
      setState(() => _senderSuggestions = []);
      return;
    }
    _senderDebounce = Timer(const Duration(milliseconds: 350), () async {
      try {
        final users = await MessageApiService.searchUsers(value.trim());
        if (mounted) setState(() => _senderSuggestions = users);
      } catch (_) {
        // silent — sender filter is a nice-to-have, not worth an error toast
      }
    });
  }

  Future<void> _pickDate({required bool isFrom}) async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: (isFrom ? _draft.dateFrom : _draft.dateTo) ?? now,
      firstDate: DateTime(2015),
      lastDate: now,
    );
    if (picked != null) {
      setState(() {
        _draft = isFrom ? _draft.copyWith(dateFrom: picked) : _draft.copyWith(dateTo: picked);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(18, 14, 18, 18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(color: Colors.grey[300], borderRadius: BorderRadius.circular(2)),
                ),
              ),
              const SizedBox(height: 14),
              const Text("Filters", style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
              const SizedBox(height: 16),

              Text("Media type", style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: Colors.grey[700])),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: _mediaTypes.map((t) {
                  final selected = _draft.mediaType == t;
                  return ChoiceChip(
                    label: Text(t),
                    selected: selected,
                    selectedColor: _kAccent.withOpacity(0.15),
                    labelStyle: TextStyle(color: selected ? _kAccent : Colors.black87, fontWeight: selected ? FontWeight.w600 : FontWeight.normal),
                    onSelected: (_) => setState(() {
                      _draft = selected ? _draft.copyWith(clearMediaType: true) : _draft.copyWith(mediaType: t);
                    }),
                  );
                }).toList(),
              ),
              const SizedBox(height: 12),

              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text("Has media only", style: TextStyle(fontSize: 13.5)),
                value: _draft.hasMedia ?? false,
                activeColor: _kAccent,
                onChanged: (v) => setState(() {
                  _draft = v ? _draft.copyWith(hasMedia: true) : _draft.copyWith(clearHasMedia: true);
                }),
              ),
              const SizedBox(height: 4),

              Text("Date range", style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: Colors.grey[700])),
              const SizedBox(height: 8),
              Row(children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => _pickDate(isFrom: true),
                    child: Text(_draft.dateFrom == null ? "From" : "${_draft.dateFrom!.day}/${_draft.dateFrom!.month}/${_draft.dateFrom!.year}"),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => _pickDate(isFrom: false),
                    child: Text(_draft.dateTo == null ? "To" : "${_draft.dateTo!.day}/${_draft.dateTo!.month}/${_draft.dateTo!.year}"),
                  ),
                ),
              ]),
              const SizedBox(height: 18),

              Text("Sender", style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: Colors.grey[700])),
              const SizedBox(height: 8),
              TextField(
                controller: _senderController,
                onChanged: _onSenderChanged,
                decoration: InputDecoration(
                  hintText: _selectedSenderLabel ?? "Search a person",
                  prefixIcon: const Icon(Icons.person_search, size: 20),
                  suffixIcon: _draft.sender != null
                      ? IconButton(
                          icon: const Icon(Icons.close, size: 18),
                          onPressed: () => setState(() {
                            _draft = _draft.copyWith(clearSender: true);
                            _selectedSenderLabel = null;
                            _senderController.clear();
                            _senderSuggestions = [];
                          }),
                        )
                      : null,
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
                  contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                ),
              ),
              if (_senderSuggestions.isNotEmpty)
                Container(
                  margin: const EdgeInsets.only(top: 6),
                  constraints: const BoxConstraints(maxHeight: 160),
                  decoration: BoxDecoration(border: Border.all(color: Colors.grey[300]!), borderRadius: BorderRadius.circular(10)),
                  child: ListView.builder(
                    shrinkWrap: true,
                    itemCount: _senderSuggestions.length,
                    itemBuilder: (context, i) {
                      final u = _senderSuggestions[i];
                      final fullName = "${u['first_name'] ?? ''} ${u['last_name'] ?? ''}".trim();
                      final display = fullName.isNotEmpty ? fullName : (u['username']?.toString() ?? 'Unknown');
                      return ListTile(
                        dense: true,
                        title: Text(display, style: const TextStyle(fontSize: 13)),
                        subtitle: u['username'] != null ? Text("@${u['username']}", style: const TextStyle(fontSize: 11)) : null,
                        onTap: () => setState(() {
                          _draft = _draft.copyWith(sender: u['id']?.toString());
                          _selectedSenderLabel = display;
                          _senderSuggestions = [];
                          _senderController.text = display;
                        }),
                      );
                    },
                  ),
                ),
              const SizedBox(height: 24),

              Row(children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => Navigator.pop(context, SearchFilterModel()),
                    child: const Text("Clear all"),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(backgroundColor: _kNavy, padding: const EdgeInsets.symmetric(vertical: 13)),
                    onPressed: () => Navigator.pop(context, _draft),
                    child: const Text("Apply", style: TextStyle(color: Colors.white)),
                  ),
                ),
              ]),
            ],
          ),
        ),
      ),
    );
  }
}