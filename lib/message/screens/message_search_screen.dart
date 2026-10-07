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
// 🔥 NAYA (6.2) — global mode ab 4 sections dikhata hai:
//   Chats (meri existing chats, local cache se naam-match) /
//   People (followers, following, mutual — "Mutual" badge) /
//   Groups (jinme main member hoon) / Messages (purana message search).
//   People+Groups ek hi call me aate hain (`GET /message/search/directory/`),
//   Messages alag call me — dono parallel; ek fail ho to dusra phir bhi dikhta hai.
//   Message filters (sender/date/media) sirf Messages pe lagte hain, isliye
//   filter active hone par baaki sections hide ho jaate hain.
//   Person tap -> `startPrivateChat`, Group tap -> `getConversation`, Chat tap -> seedha ChatScreen.
//
// NOTE: backend kam se kam 2-char query pe hi 200 deta hai, warna 400 —
// isliye client-side bhi 2-char se pehle search fire nahi karte.

import 'dart:async';

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:timeago/timeago.dart' as timeago;

import '../models/message_models.dart';
import '../services/message_api_service.dart';
import '../services/message_cache_service.dart';
import 'chat_screen.dart';
import '../../l10n/app_localizations.dart';
import '../../theme_service.dart'; // 🎨 THEME FIX — AppThemeTokens

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

  // 🔥 NAYA (6.2) — global mode ke extra sections.
  List<ConversationModel> _allChats = []; // cached conversations (one-time load)
  List<ConversationModel> _chatMatches = [];
  List<DirectoryPersonModel> _people = [];
  List<DirectoryGroupModel> _groups = [];
  bool _peopleHasMore = false;
  bool _groupsHasMore = false;
  bool _loadingMorePeople = false;
  bool _loadingMoreGroups = false;
  bool _opening = false; // double-tap guard jab chat open ho rahi ho

  bool get _isGlobal => widget.conversationId == null;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _focusNode.requestFocus();
    });
    if (_isGlobal) _loadChatsOnce();
  }

  Future<void> _loadChatsOnce() async {
    var chats = await MessageCacheService.getCachedConversations();
    if (chats.isEmpty) {
      try {
        chats = await MessageApiService.getConversations();
      } catch (_) {}
    }
    if (!mounted) return;
    _allChats = chats;
    if (_lastQuery.length >= 2) setState(() => _chatMatches = _matchChats(_lastQuery));
  }

  List<ConversationModel> _matchChats(String q) {
    final needle = q.toLowerCase();
    return _allChats
        .where((c) => c.displayTitle.toLowerCase().contains(needle))
        .take(5)
        .toList();
  }

  void _resetResults() {
    _results = [];
    _chatMatches = [];
    _people = [];
    _groups = [];
    _peopleHasMore = false;
    _groupsHasMore = false;
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
        _resetResults();
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
      if (_isGlobal) {
        await _runGlobalSearch(q);
      } else {
        final msgs = await MessageApiService.searchMessages(
          widget.conversationId!,
          q,
          filters: _filters,
        );
        // Stale response guard — user ne tab tak aur type kar diya ho sakta hai.
        if (!mounted || q != _lastQuery) return;
        setState(() {
          _results = msgs.map((m) => SearchResultModel(message: m)).toList();
          _loading = false;
        });
      }
    } catch (e) {
      if (!mounted || q != _lastQuery) return;
      final t = AppLocalizations.of(context)!;
      final throttled = e is MessageApiException && e.statusCode == 429;
      setState(() {
        _loading = false;
        _resetResults();
        _error = throttled ? t.msgSearchTooMany : t.msgSearchFailed;
      });
    }
  }

  /// Global mode: Messages + (People, Groups) parallel. Filters active ho to
  /// sirf Messages (filters sirf messages pe meaningful hain). Ek call fail
  /// ho to dusre ke results phir bhi dikhte hain; dono fail hon tabhi error.
  Future<void> _runGlobalSearch(String q) async {
    final filtersActive = !_filters.isEmpty;
    final messagesF = MessageApiService.searchAllMessages(q, filters: _filters)
        .then<List<SearchResultModel>?>((v) => v)
        .catchError((Object e) {
      if (e is MessageApiException && e.statusCode == 429) throw e;
      return null;
    });
    final dirF = filtersActive
        ? Future<DirectorySearchResult?>.value(null)
        : MessageApiService.searchDirectory(q)
            .then<DirectorySearchResult?>((v) => v)
            .catchError((Object e) {
            if (e is MessageApiException && e.statusCode == 429) throw e;
            return null;
          });

    // Future.wait: dono ek saath listen hote hain (ek jaldi fail ho to "unhandled" nahi banta).
    final both = await Future.wait<Object?>([messagesF, dirF]);
    final messages = both[0] as List<SearchResultModel>?;
    final dir = both[1] as DirectorySearchResult?;
    if (!mounted || q != _lastQuery) return;

    if (messages == null && dir == null && !filtersActive) {
      throw MessageApiException('search failed');
    }
    if (messages == null && filtersActive) {
      throw MessageApiException('search failed');
    }
    setState(() {
      _results = messages ?? [];
      _people = dir?.people ?? [];
      _peopleHasMore = dir?.peopleHasMore ?? false;
      final chatMatches = filtersActive ? <ConversationModel>[] : _matchChats(q);
      _chatMatches = chatMatches;
      // Chats me jo group already dikh raha hai use Groups section me repeat nahi karte.
      final chatIds = chatMatches.map((c) => c.id).toSet();
      _groups = (dir?.groups ?? []).where((g) => !chatIds.contains(g.conversationId)).toList();
      _groupsHasMore = dir?.groupsHasMore ?? false;
      _loading = false;
    });
  }

  Future<void> _seeAllPeople() async {
    if (_loadingMorePeople) return;
    final q = _lastQuery;
    setState(() => _loadingMorePeople = true);
    try {
      final r = await MessageApiService.searchDirectory(q, type: 'people', limit: 50);
      if (!mounted || q != _lastQuery) return;
      setState(() {
        _people = r.people;
        _peopleHasMore = false;
      });
    } catch (_) {
      // silent — pehle wali list screen pe hai
    } finally {
      if (mounted) setState(() => _loadingMorePeople = false);
    }
  }

  Future<void> _seeAllGroups() async {
    if (_loadingMoreGroups) return;
    final q = _lastQuery;
    setState(() => _loadingMoreGroups = true);
    try {
      final r = await MessageApiService.searchDirectory(q, type: 'groups', limit: 50);
      if (!mounted || q != _lastQuery) return;
      final chatIds = _chatMatches.map((c) => c.id).toSet();
      setState(() {
        _groups = r.groups.where((g) => !chatIds.contains(g.conversationId)).toList();
        _groupsHasMore = false;
      });
    } catch (_) {
    } finally {
      if (mounted) setState(() => _loadingMoreGroups = false);
    }
  }

  void _rerunCurrentQuery() => _runSearch(_queryController.text);

  Future<void> _openFilters() async {
    final updated = await showModalBottomSheet<SearchFilterModel>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (_) => _SearchFiltersSheet(initial: _filters),
    );
    if (updated != null) {
      setState(() => _filters = updated);
      _rerunCurrentQuery();
    }
  }

  /// Chat kholne ka common flow (loading dialog + error snackbar). `loader`
  /// ConversationModel laata hai; success pe search screen replace ho jaati hai.
  Future<void> _openChat(Future<ConversationModel> Function() loader, {String? jumpToMessageId}) async {
    if (_opening) return;
    _opening = true;
    final t = AppLocalizations.of(context)!;
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (_) => Center(child: CircularProgressIndicator(color: Theme.of(context).colorScheme.primary)),
    );
    try {
      final conversation = await loader();
      if (!mounted) return;
      Navigator.pop(context); // loading dialog band karo
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(
          builder: (_) => ChatScreen(conversation: conversation, jumpToMessageId: jumpToMessageId),
        ),
      );
    } catch (_) {
      if (!mounted) return;
      Navigator.pop(context); // loading dialog band karo
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(t.msgSearchOpenChatFailed)));
    } finally {
      _opening = false;
    }
  }

  Future<void> _onResultTap(SearchResultModel result) async {
    if (_isGlobal) {
      final preview = result.conversationPreview;
      if (preview == null) return;
      await _openChat(() => MessageApiService.getConversation(preview.id), jumpToMessageId: result.message.id);
    } else {
      Navigator.pop(context, result.message.id);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: cs.primary,
        elevation: 0,
        titleSpacing: 0,
        title: Container(
          height: 40,
          margin: const EdgeInsets.only(right: 8),
          decoration: BoxDecoration(color: cs.onPrimary.withOpacity(0.12), borderRadius: BorderRadius.circular(10)),
          child: TextField(
            controller: _queryController,
            focusNode: _focusNode,
            onChanged: _onQueryChanged,
            textInputAction: TextInputAction.search,
            style: TextStyle(color: cs.onPrimary, fontSize: 14.5),
            cursorColor: cs.onPrimary,
            decoration: InputDecoration(
              hintText: _isGlobal ? AppLocalizations.of(context)!.msgSearchHintAll : AppLocalizations.of(context)!.msgSearchHintChat,
              hintStyle: TextStyle(color: cs.onPrimary.withOpacity(0.6), fontSize: 14.5),
              border: InputBorder.none,
              contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              suffixIcon: _queryController.text.isNotEmpty
                  ? IconButton(
                      icon: Icon(Icons.close, color: cs.onPrimary.withOpacity(0.7), size: 18),
                      onPressed: () {
                        _queryController.clear();
                        _debounce?.cancel();
                        setState(() {
                          _resetResults();
                          _lastQuery = '';
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
              Icon(Icons.tune, color: cs.onPrimary),
              if (!_filters.isEmpty)
                Positioned(
                  right: -1,
                  top: -1,
                  child: Container(
                    width: 8,
                    height: 8,
                    decoration: BoxDecoration(color: AppThemeTokens.of(context).coral, shape: BoxShape.circle),
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
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Wrap(spacing: 6, runSpacing: 6, children: chips),
        if (_isGlobal)
          Padding(
            padding: const EdgeInsets.only(top: 4, left: 2),
            child: Text(
              AppLocalizations.of(context)!.msgSearchFiltersNote,
              style: TextStyle(fontSize: 11, color: Theme.of(context).colorScheme.onSurfaceVariant),
            ),
          ),
      ]),
    );
  }

  Widget _chip(String label, VoidCallback onRemove) {
    return Chip(
      label: Text(label, style: const TextStyle(fontSize: 11.5)),
      backgroundColor: AppThemeTokens.of(context).coral.withOpacity(0.1),
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
    final cs = Theme.of(context).colorScheme;
    final t = AppLocalizations.of(context)!;
    if (_loading) {
      return Center(child: CircularProgressIndicator(color: cs.primary));
    }
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Text(_error!, textAlign: TextAlign.center, style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13)),
        ),
      );
    }
    if (!_searchedOnce) {
      return Center(
        child: Text(t.msgSearchMinChars, style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13)),
      );
    }
    final nothing = _results.isEmpty && _chatMatches.isEmpty && _people.isEmpty && _groups.isEmpty;
    if (nothing) {
      return Center(
        child: Text(
          _isGlobal ? t.msgSearchNoResults : t.msgSearchNoMessages,
          style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13),
        ),
      );
    }
    if (!_isGlobal) {
      return ListView.separated(
        padding: const EdgeInsets.symmetric(vertical: 6),
        itemCount: _results.length,
        separatorBuilder: (_, __) => Divider(height: 1, color: cs.outlineVariant, indent: 72),
        itemBuilder: (context, i) => _buildResultTile(_results[i]),
      );
    }

    // Global: sections — Chats / People / Groups / Messages
    final children = <Widget>[];
    if (_chatMatches.isNotEmpty) {
      children.add(_sectionHeader(t.msgSearchSectionChats));
      children.addAll(_chatMatches.map(_buildChatTile));
    }
    if (_people.isNotEmpty) {
      children.add(_sectionHeader(t.msgSearchSectionPeople));
      children.addAll(_people.map(_buildPersonTile));
      if (_peopleHasMore) children.add(_seeAllButton(_seeAllPeople, _loadingMorePeople));
    }
    if (_groups.isNotEmpty) {
      children.add(_sectionHeader(t.msgSearchSectionGroups));
      children.addAll(_groups.map(_buildGroupTile));
      if (_groupsHasMore) children.add(_seeAllButton(_seeAllGroups, _loadingMoreGroups));
    }
    if (_results.isNotEmpty) {
      children.add(_sectionHeader(t.msgSearchSectionMessages));
      children.addAll(_results.map(_buildResultTile));
    }
    return ListView(padding: const EdgeInsets.only(bottom: 16), children: children);
  }

  Widget _sectionHeader(String title) {
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 4),
      child: Text(title, style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: cs.onSurfaceVariant)),
    );
  }

  Widget _seeAllButton(VoidCallback onTap, bool busy) {
    final t = AppLocalizations.of(context)!;
    return Align(
      alignment: Alignment.centerLeft,
      child: TextButton(
        onPressed: busy ? null : onTap,
        child: busy
            ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
            : Text(t.seeAll),
      ),
    );
  }

  Widget _avatar(String? photo, String fallbackName, {IconData? fallbackIcon}) {
    final cs = Theme.of(context).colorScheme;
    final has = photo != null && photo.isNotEmpty;
    return CircleAvatar(
      radius: 22,
      backgroundColor: AppThemeTokens.of(context).surface2,
      backgroundImage: has ? CachedNetworkImageProvider(photo) : null,
      child: has
          ? null
          : (fallbackIcon != null
              ? Icon(fallbackIcon, color: cs.onSurfaceVariant)
              : Text(fallbackName.isNotEmpty ? fallbackName[0].toUpperCase() : '?',
                  style: TextStyle(color: cs.onSurfaceVariant, fontWeight: FontWeight.w600))),
    );
  }

  Widget _buildChatTile(ConversationModel c) {
    final cs = Theme.of(context).colorScheme;
    return ListTile(
      leading: _avatar(c.displayPhoto, c.displayTitle, fallbackIcon: c.isGroup ? Icons.group : null),
      title: Text(c.displayTitle, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
      subtitle: (c.lastMessageText != null && c.lastMessageText!.trim().isNotEmpty)
          ? Text(c.lastMessageText!.trim(), maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant))
          : null,
      onTap: () => _openChat(() async => c),
    );
  }

  Widget _buildPersonTile(DirectoryPersonModel p) {
    final cs = Theme.of(context).colorScheme;
    final t = AppLocalizations.of(context)!;
    final coral = AppThemeTokens.of(context).coral;
    final relationLabel = p.relation == 'following'
        ? t.msgSearchFollowing
        : (p.relation == 'follower' ? t.msgSearchFollowsYou : null);
    return ListTile(
      leading: _avatar(p.profilePhoto, p.displayName),
      title: Row(children: [
        Flexible(child: Text(p.displayName, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600))),
        if (p.isMutual) ...[
          const SizedBox(width: 6),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
            decoration: BoxDecoration(color: coral.withOpacity(0.12), borderRadius: BorderRadius.circular(8)),
            child: Text(t.msgSearchMutual, style: TextStyle(fontSize: 10.5, color: coral, fontWeight: FontWeight.w600)),
          ),
        ],
      ]),
      subtitle: Text(
        [if (p.username.isNotEmpty) '@${p.username}', if (relationLabel != null) relationLabel].join(' · '),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant),
      ),
      onTap: () => _openChat(() => MessageApiService.startPrivateChat(p.id)),
    );
  }

  Widget _buildGroupTile(DirectoryGroupModel g) {
    final cs = Theme.of(context).colorScheme;
    final t = AppLocalizations.of(context)!;
    final sub = [
      t.msgSearchMembers(g.membersCount),
      if (g.topicTag != null && g.topicTag!.isNotEmpty) g.topicTag!,
    ].join(' · ');
    return ListTile(
      leading: _avatar(g.photoUrl, g.name, fallbackIcon: Icons.group),
      title: Text(g.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
      subtitle: Text(sub, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12, color: cs.onSurfaceVariant)),
      onTap: () => _openChat(() => MessageApiService.getConversation(g.conversationId)),
    );
  }

  Widget _buildResultTile(SearchResultModel result) {
    final msg = result.message;
    final preview = result.conversationPreview;
    final senderName = msg.sender?.displayName ?? 'Unknown';
    final senderPhoto = msg.sender?.profilePhoto;
    final cs = Theme.of(context).colorScheme;
    final coral = AppThemeTokens.of(context).coral;

    return ListTile(
      leading: CircleAvatar(
        radius: 22,
        backgroundColor: AppThemeTokens.of(context).surface2,
        backgroundImage: (senderPhoto != null && senderPhoto.isNotEmpty) ? CachedNetworkImageProvider(senderPhoto) : null,
        child: (senderPhoto == null || senderPhoto.isEmpty)
            ? Text(senderName.isNotEmpty ? senderName[0].toUpperCase() : '?', style: TextStyle(color: cs.onSurfaceVariant, fontWeight: FontWeight.w600))
            : null,
      ),
      title: Row(children: [
        Expanded(child: Text(senderName, style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600), maxLines: 1, overflow: TextOverflow.ellipsis)),
        const SizedBox(width: 6),
        Text(_fmtTime(msg.createdAt), style: TextStyle(fontSize: 10.5, color: cs.onSurfaceVariant)),
      ]),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (preview != null)
            Padding(
              padding: const EdgeInsets.only(top: 2, bottom: 2),
              child: Row(children: [
                Icon(preview.type == 'group' ? Icons.group : Icons.person, size: 12, color: coral),
                const SizedBox(width: 3),
                Flexible(child: Text(preview.name, style: TextStyle(fontSize: 11, color: coral, fontWeight: FontWeight.w600), maxLines: 1, overflow: TextOverflow.ellipsis)),
              ]),
            ),
          Text(_snippetFor(msg), maxLines: 2, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12.5, color: cs.onSurfaceVariant)),
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
    final cs = Theme.of(context).colorScheme;
    final coral = AppThemeTokens.of(context).coral;
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
                  decoration: BoxDecoration(color: cs.outlineVariant, borderRadius: BorderRadius.circular(2)),
                ),
              ),
              const SizedBox(height: 14),
              const Text("Filters", style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
              const SizedBox(height: 16),

              Text("Media type", style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: _mediaTypes.map((t) {
                  final selected = _draft.mediaType == t;
                  return ChoiceChip(
                    label: Text(t),
                    selected: selected,
                    selectedColor: coral.withOpacity(0.15),
                    labelStyle: TextStyle(color: selected ? coral : cs.onSurface, fontWeight: selected ? FontWeight.w600 : FontWeight.normal),
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
                activeColor: coral,
                onChanged: (v) => setState(() {
                  _draft = v ? _draft.copyWith(hasMedia: true) : _draft.copyWith(clearHasMedia: true);
                }),
              ),
              const SizedBox(height: 4),

              Text("Date range", style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
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

              Text("Sender", style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: cs.onSurfaceVariant)),
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
                  decoration: BoxDecoration(border: Border.all(color: cs.outlineVariant), borderRadius: BorderRadius.circular(10)),
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
                    style: ElevatedButton.styleFrom(backgroundColor: cs.primary, padding: const EdgeInsets.symmetric(vertical: 13)),
                    onPressed: () => Navigator.pop(context, _draft),
                    child: Text("Apply", style: TextStyle(color: cs.onPrimary)),
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