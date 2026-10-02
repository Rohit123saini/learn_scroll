// message/screens/study_groups_discovery_screen.dart
//
// 🔥 NAYA — Task G14 ("Study Groups as a first-class feature").
//
// Pehle group sirf `CreateGroupScreen` se, apne existing contacts me se
// member chun ke banta tha — koi bhi PUBLIC group baad me kisi naye,
// ad-hoc-contact-na-hone-wale student ko kabhi mil hi nahi sakta tha
// (sirf invite-code jaante hue hi join ho sakta tha). Ye screen wahi gap
// bharti hai: subject/exam se search/browse karo (backend: `GET /message/
// groups/discover/`, sirf PUBLIC groups — private groups is list me kabhi
// nahi aate, unka raasta ab bhi invite-code/direct-add hi hai), aur ek tap
// me join karke seedha chat me pahunch jao.

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../services/message_api_service.dart';
import 'chat_screen.dart';
import '../../theme_service.dart';

class StudyGroupsDiscoveryScreen extends StatefulWidget {
  const StudyGroupsDiscoveryScreen({super.key});

  @override
  State<StudyGroupsDiscoveryScreen> createState() => _StudyGroupsDiscoveryScreenState();
}

class _StudyGroupsDiscoveryScreenState extends State<StudyGroupsDiscoveryScreen> {
  final _searchController = TextEditingController();
  Timer? _debounce;

  List<Map<String, dynamic>> _groups = [];
  bool _isLoading = true;
  String? _error;

  // group-id -> in-flight join/open action, taaki double-tap se do baar
  // request na chali jaaye aur sirf usi card pe spinner dikhe.
  final Set<String> _busyGroupIds = {};

  @override
  void initState() {
    super.initState();
    _runSearch('');
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _searchController.dispose();
    super.dispose();
  }

  void _onSearchChanged(String query) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), () => _runSearch(query));
  }

  Future<void> _runSearch(String query) async {
    setState(() {
      _isLoading = true;
      _error = null;
    });
    try {
      final data = await MessageApiService.discoverGroups(query: query);
      final results = data['results'];
      if (!mounted) return;
      setState(() {
        _groups = results is List ? results.cast<Map<String, dynamic>>() : [];
        _isLoading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isLoading = false;
        _error = e.toString();
      });
    }
  }

  Future<void> _openConversation(String? conversationId) async {
    if (conversationId == null || conversationId.isEmpty) return;
    final conversation = await MessageApiService.getConversation(conversationId);
    if (!mounted) return;
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => ChatScreen(conversation: conversation)),
    );
  }

  Future<void> _onGroupTap(Map<String, dynamic> group) async {
    final id = group['id']?.toString();
    if (id == null || _busyGroupIds.contains(id)) return;

    setState(() => _busyGroupIds.add(id));
    try {
      final isMember = group['is_member'] == true;
      if (isMember) {
        // Already member — bas seedha chat khol do.
        final full = await MessageApiService.getGroup(id);
        await _openConversation(full['conversation_id']?.toString());
      } else {
        final inviteCode = group['invite_code']?.toString();
        if (inviteCode == null || inviteCode.isEmpty) {
          throw Exception('Is group ka invite code nahi mila.');
        }
        final result = await MessageApiService.joinGroupByInviteCode(inviteCode);
        if (!mounted) return;
        if (result['status'] == 'joined') {
          final conversationId = (result['group'] is Map)
              ? (result['group']['conversation_id']?.toString())
              : null;
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('${group['name'] ?? 'Group'} join ho gaya!')),
          );
          await _openConversation(conversationId);
        } else {
          // Discover sirf public groups dikhata hai, isliye ye path
          // normally nahi aana chahiye — phir bhi defensively handle.
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(result['detail']?.toString() ?? 'Request bhej di gayi.')),
          );
        }
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Kuch gadbad ho gayi: $e')),
      );
    } finally {
      if (mounted) setState(() => _busyGroupIds.remove(id));
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: cs.primary,
        elevation: 0,
        foregroundColor: cs.onPrimary,
        title: const Text('Study Groups', style: TextStyle(fontWeight: FontWeight.w700)),
      ),
      body: Column(
        children: [
          _buildSearchField(),
          Expanded(child: _buildBody()),
        ],
      ),
    );
  }

  Widget _buildSearchField() {
    final cs = Theme.of(context).colorScheme;
    return Container(
      color: cs.surface,
      padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
      child: TextField(
        controller: _searchController,
        onChanged: _onSearchChanged,
        decoration: InputDecoration(
          hintText: 'Search by subject/exam — e.g. NEET, JEE, UPSC',
          hintStyle: TextStyle(color: cs.onSurfaceVariant, fontSize: 14),
          prefixIcon: Icon(Icons.search_rounded, color: cs.onSurfaceVariant),
          filled: true,
          fillColor: AppThemeTokens.of(context).surface2,
          contentPadding: const EdgeInsets.symmetric(vertical: 0, horizontal: 12),
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
        ),
      ),
    );
  }

  Widget _buildBody() {
    final cs = Theme.of(context).colorScheme;
    if (_isLoading) {
      return Center(child: CircularProgressIndicator(color: cs.primary));
    }
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text('Groups load nahi ho paye: $_error', textAlign: TextAlign.center, style: TextStyle(color: cs.error)),
        ),
      );
    }
    if (_groups.isEmpty) {
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 80, height: 80,
            decoration: BoxDecoration(color: cs.primary.withOpacity(0.06), shape: BoxShape.circle),
            child: Icon(Icons.groups_rounded, size: 36, color: cs.primary.withOpacity(0.5)),
          ),
          const SizedBox(height: 12),
          Text('Koi public study group nahi mila', style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13)),
        ]),
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.symmetric(vertical: 8),
      itemCount: _groups.length,
      separatorBuilder: (_, __) => Divider(height: 1, indent: 78, color: cs.outlineVariant),
      itemBuilder: (context, index) => _buildGroupTile(_groups[index]),
    );
  }

  Widget _buildGroupTile(Map<String, dynamic> group) {
    final cs = Theme.of(context).colorScheme;
    final coral = AppThemeTokens.of(context).coral;
    final id = group['id']?.toString() ?? '';
    final isMember = group['is_member'] == true;
    final isBusy = _busyGroupIds.contains(id);
    final topicTag = (group['topic_tag'] ?? '').toString().trim();
    final membersCount = group['members_count'] ?? 0;
    final photoUrl = (group['photo_url'] ?? '').toString();

    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      leading: CircleAvatar(
        radius: 26,
        backgroundColor: AppThemeTokens.of(context).surface2,
        backgroundImage: photoUrl.isNotEmpty ? CachedNetworkImageProvider(photoUrl) : null,
        child: photoUrl.isEmpty ? Icon(Icons.groups_rounded, color: cs.onSurfaceVariant) : null,
      ),
      title: Text(
        (group['name'] ?? '').toString(),
        style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 3),
        child: Row(children: [
          if (topicTag.isNotEmpty) ...[
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                color: coral.withOpacity(0.12),
                borderRadius: BorderRadius.circular(20),
              ),
              child: Text(topicTag, style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w600, color: coral)),
            ),
            const SizedBox(width: 6),
          ],
          Flexible(
            child: Text(
              '$membersCount members',
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ]),
      ),
      trailing: SizedBox(
        width: 84,
        child: isBusy
            ? const Center(child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)))
            : OutlinedButton(
                onPressed: () => _onGroupTap(group),
                style: OutlinedButton.styleFrom(
                  foregroundColor: isMember ? cs.primary : coral,
                  side: BorderSide(color: isMember ? cs.primary : coral),
                  padding: const EdgeInsets.symmetric(horizontal: 8),
                ),
                child: Text(isMember ? 'Open' : 'Join', style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
              ),
      ),
    );
  }
}
