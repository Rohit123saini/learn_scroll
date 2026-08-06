// message/screens/group_profile_screen.dart
//
// 🎨 NAYA SCREEN — Group info / settings.
//
// Ye screen `ChatScreen` se group ka naam ya "Group info" menu-item tap
// karne pe khulti hai. Sab kuch backend (`GroupViewSet` — views.py) ke
// already-existing rules ke upar bana hai, koi naya backend endpoint nahi
// chahiye:
//
//   • PUBLIC group  → koi bhi member invite-link bhej sakta hai, members
//     ki poori list/count sabko dikhti hai, koi bhi member naye logo ko
//     seedha add kar sakta hai (`GroupViewSet.add_members` — is_private
//     false ho to koi role-check nahi lagta).
//   • PRIVATE group → invite-link sirf ADMIN/MODERATOR ko dikhta hai
//     (bhejne ke liye), members list/count bhi sirf ADMIN/MODERATOR ko
//     dikhti hai (baaki members ko "list private hai" wala placeholder
//     dikhta hai), naye members sirf ADMIN/MODERATOR add kar sakte hain
//     (`_require_admin` backend me).
//
// Roles (backend `GroupMember.Role`): admin / moderator / member.
//   • ADMIN     → sab kuch: naam/photo/description change, members add/
//     remove/ban, kisi ko bhi admin ya moderator bana/hata sakta hai,
//     poora group delete kar sakta hai (sirf admin — moderator bhi nahi).
//   • MODERATOR → members add/remove/ban kar sakta hai, invite-link dekh
//     bhej sakta hai — lekin group delete NAHI kar sakta, aur naam/photo
//     bhi backend `IsGroupAdminOrModerator` permission ke through allow
//     hai (dono PATCH kar sakte hain — isliye "Edit" yahan admin+mod dono
//     ko dikhaya hai, delete sirf admin ko).
//   • MEMBER    → bas dekh sakta hai (public group me), khud group choad
//     sakta hai.
//
// Multiple admins bhi ho sakte hain — ek admin doosre kisi bhi member ko
// "Make admin" kar sakta hai, ye simple role-reassign hai
// (`updateGroupMember(role: 'admin')`), koi limit nahi.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart'; // Clipboard — invite link copy
import 'package:cached_network_image/cached_network_image.dart';

import '../services/message_api_service.dart';
import '../../services/auth_service.dart';
import 'conversations_screen.dart';

const _kNavy = Color(0xFF030F27);
const _kAccent = Color(0xFFEE0979);
const _kBg = Color(0xFFF6F7FB);

class GroupProfileScreen extends StatefulWidget {
  final String groupId;
  const GroupProfileScreen({super.key, required this.groupId});

  @override
  State<GroupProfileScreen> createState() => _GroupProfileScreenState();
}

class _GroupProfileScreenState extends State<GroupProfileScreen> {
  bool _loading = true;
  String? _loadError;
  String? _myUserId;

  // raw parsed group fields
  String _name = '';
  String _description = '';
  String? _photoUrl;
  bool _isPrivate = false;
  String? _inviteCode;
  List<Map<String, dynamic>> _members = []; // normalized: {id, name, username, avatar, role, is_muted, is_banned}

  String? _myRole; // 'admin' | 'moderator' | 'member' | null
  bool get _isAdmin => _myRole == 'admin';
  bool get _isAdminOrMod => _myRole == 'admin' || _myRole == 'moderator';
  // Public group me sab kuch khula hai; private me sirf admin/moderator.
  bool get _canSeeMembersAndLink => !_isPrivate || _isAdminOrMod;
  bool get _canAddMembers => !_isPrivate || _isAdminOrMod;

  // 🔥 NAYA — private group ki pending join-requests (admin/moderator only)
  List<Map<String, dynamic>> _joinRequests = [];
  bool _loadingRequests = false;

  bool _busy = false; // leave/delete/promote jaisi actions ke waqt double-tap se bachne ke liye

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadError = null;
    });
    try {
      _myUserId = await AuthService.getUserId();
      final data = await MessageApiService.getGroup(widget.groupId);

      final membersRaw = data['members'] ?? data['group_members'] ?? [];
      final normalized = <Map<String, dynamic>>[];
      if (membersRaw is List) {
        for (final m in membersRaw) {
          if (m is! Map) continue;
          final userField = m['user'];
          final id = (userField is Map ? userField['id'] : (m['user_id'] ?? m['id']))?.toString() ?? '';
          if (id.isEmpty) continue;
          final first = (userField is Map ? userField['first_name'] : m['first_name'])?.toString().trim() ?? '';
          final last = (userField is Map ? userField['last_name'] : m['last_name'])?.toString().trim() ?? '';
          final username = (userField is Map ? userField['username'] : m['username'])?.toString().trim() ?? '';
          final fullName = [first, last].where((s) => s.isNotEmpty).join(' ');
          normalized.add({
            'id': id,
            'name': fullName.isNotEmpty ? fullName : (username.isNotEmpty ? username : 'User'),
            'username': username,
            'avatar': (userField is Map ? userField['avatar'] : m['avatar'])?.toString(),
            'role': m['role']?.toString() ?? 'member',
            'is_muted': m['is_muted'] == true,
            'is_banned': m['is_banned'] == true,
          });
        }
      }
      normalized.removeWhere((m) => m['is_banned'] == true);
      // admin sabse upar, phir moderator, phir member — WhatsApp jaisa hi order
      const roleOrder = {'admin': 0, 'moderator': 1, 'member': 2};
      normalized.sort((a, b) => (roleOrder[a['role']] ?? 2).compareTo(roleOrder[b['role']] ?? 2));

      String? myRole;
      for (final m in normalized) {
        if (m['id'] == _myUserId) {
          myRole = m['role'] as String?;
          break;
        }
      }

      if (!mounted) return;
      setState(() {
        _name = data['name']?.toString() ?? '';
        _description = data['description']?.toString() ?? '';
        _photoUrl = data['photo_url']?.toString();
        _isPrivate = data['is_private'] == true;
        _inviteCode = data['invite_code']?.toString();
        _members = normalized;
        _myRole = myRole;
        _loading = false;
      });

      // Private group + main admin/moderator hoon → pending join-requests
      // bhi laa lo (public group me ye concept hi nahi hai — join() wahan
      // turant member bana deta hai, koi request banti hi nahi).
      if (_isPrivate && (myRole == 'admin' || myRole == 'moderator')) {
        _loadJoinRequests();
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _loadError = "Group info load nahi ho paayi: $e";
      });
    }
  }

  Future<void> _loadJoinRequests() async {
    setState(() => _loadingRequests = true);
    try {
      final raw = await MessageApiService.getJoinRequests(widget.groupId);
      final normalized = <Map<String, dynamic>>[];
      for (final r in raw) {
        if (r is! Map) continue;
        final userField = r['user'];
        final id = r['id']?.toString() ?? '';
        if (id.isEmpty) continue;
        final first = (userField is Map ? userField['first_name'] : r['first_name'])?.toString().trim() ?? '';
        final last = (userField is Map ? userField['last_name'] : r['last_name'])?.toString().trim() ?? '';
        final username = (userField is Map ? userField['username'] : r['username'])?.toString().trim() ?? '';
        final fullName = [first, last].where((s) => s.isNotEmpty).join(' ');
        normalized.add({
          'request_id': id,
          'name': fullName.isNotEmpty ? fullName : (username.isNotEmpty ? username : 'User'),
          'username': username,
          'avatar': (userField is Map ? userField['avatar'] : r['avatar'])?.toString(),
        });
      }
      if (!mounted) return;
      setState(() {
        _joinRequests = normalized;
        _loadingRequests = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loadingRequests = false);
    }
  }

  Future<void> _respondToJoinRequest(String requestId, bool approve) async {
    try {
      if (approve) {
        await MessageApiService.approveJoinRequest(widget.groupId, requestId);
      } else {
        await MessageApiService.rejectJoinRequest(widget.groupId, requestId);
      }
      if (!mounted) return;
      setState(() => _joinRequests.removeWhere((r) => r['request_id'] == requestId));
      if (approve) _load(); // member list/count refresh ho jaaye
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Request update fail: $e")));
    }
  }

  // ------------------------------------------------------------------
  // EDIT NAME / DESCRIPTION (admin + moderator)
  // ------------------------------------------------------------------
  Future<void> _editGroupInfo() async {
    final nameCtrl = TextEditingController(text: _name);
    final descCtrl = TextEditingController(text: _description);
    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text("Edit group"),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(controller: nameCtrl, decoration: const InputDecoration(labelText: "Group name")),
          const SizedBox(height: 10),
          TextField(controller: descCtrl, decoration: const InputDecoration(labelText: "Description"), maxLines: 3),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text("Cancel")),
          ElevatedButton(onPressed: () => Navigator.pop(ctx, true), child: const Text("Save")),
        ],
      ),
    );
    if (saved != true) return;
    final newName = nameCtrl.text.trim();
    if (newName.isEmpty) return;
    try {
      await MessageApiService.updateGroup(widget.groupId, {
        'name': newName,
        'description': descCtrl.text.trim(),
      });
      if (!mounted) return;
      setState(() {
        _name = newName;
        _description = descCtrl.text.trim();
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Update fail: $e")));
    }
  }

  // ------------------------------------------------------------------
  // INVITE LINK
  // ------------------------------------------------------------------
  String get _inviteLink => _inviteCode == null ? '' : "https://yourapp.link/join/$_inviteCode";

  Future<void> _copyInviteLink() async {
    if (_inviteLink.isEmpty) return;
    await Clipboard.setData(ClipboardData(text: _inviteLink));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("Invite link copy ho gaya")));
  }

  // ------------------------------------------------------------------
  // ADD MEMBERS (public: sab; private: admin/moderator — same rule
  // backend `add_members` action me bhi hai)
  // ------------------------------------------------------------------
  Future<void> _openAddMembers() async {
    final added = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _AddMembersSheet(
        groupId: widget.groupId,
        existingMemberIds: _members.map((m) => m['id'] as String).toSet(),
      ),
    );
    if (added == true) _load();
  }

  // ------------------------------------------------------------------
  // MEMBER ROW ACTIONS (admin only — moderator manage nahi karta doosre
  // admins/moderators ko, sirf admin final authority hai role ke upar)
  // ------------------------------------------------------------------
  Future<void> _showMemberActions(Map<String, dynamic> member) async {
    if (!_isAdmin || member['id'] == _myUserId || _busy) return;
    final role = member['role'] as String;
    final action = await showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: const Icon(Icons.shield_rounded, color: _kAccent),
            title: Text(role == 'admin' ? "Remove as admin" : "Make group admin"),
            onTap: () => Navigator.pop(ctx, role == 'admin' ? 'demote_admin' : 'make_admin'),
          ),
          ListTile(
            leading: const Icon(Icons.verified_user_outlined, color: _kNavy),
            title: Text(role == 'moderator' ? "Remove as moderator" : "Make moderator"),
            onTap: () => Navigator.pop(ctx, role == 'moderator' ? 'demote_mod' : 'make_mod'),
          ),
          ListTile(
            leading: const Icon(Icons.person_remove_rounded, color: Colors.red),
            title: const Text("Remove from group", style: TextStyle(color: Colors.red)),
            onTap: () => Navigator.pop(ctx, 'remove'),
          ),
          ListTile(
            leading: const Icon(Icons.block_rounded, color: Colors.red),
            title: const Text("Ban from group", style: TextStyle(color: Colors.red)),
            onTap: () => Navigator.pop(ctx, 'ban'),
          ),
        ]),
      ),
    );
    if (action == null) return;

    setState(() => _busy = true);
    try {
      switch (action) {
        case 'make_admin':
          await MessageApiService.updateGroupMember(widget.groupId, member['id'], {'role': 'admin'});
          break;
        case 'demote_admin':
          await MessageApiService.updateGroupMember(widget.groupId, member['id'], {'role': 'member'});
          break;
        case 'make_mod':
          await MessageApiService.updateGroupMember(widget.groupId, member['id'], {'role': 'moderator'});
          break;
        case 'demote_mod':
          await MessageApiService.updateGroupMember(widget.groupId, member['id'], {'role': 'member'});
          break;
        case 'remove':
          await MessageApiService.removeGroupMember(widget.groupId, member['id']);
          break;
        case 'ban':
          await MessageApiService.updateGroupMember(widget.groupId, member['id'], {'is_banned': true});
          break;
      }
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Action fail: $e")));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ------------------------------------------------------------------
  // LEAVE / DELETE
  // ------------------------------------------------------------------
  Future<void> _leaveGroup() async {
    if (_myUserId == null || _busy) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text("Leave group?"),
        content: const Text("Aap is group se nikal jaoge, dobara add hone ke liye kisi member ko invite karna padega."),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text("Cancel")),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text("Leave", style: TextStyle(color: Colors.red))),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _busy = true);
    try {
      await MessageApiService.removeGroupMember(widget.groupId, _myUserId!);
      if (!mounted) return;
      Navigator.of(context).pop(true); // caller (ChatScreen) is result se pata karega group chhod diya
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Leave fail: $e")));
    }
  }

  Future<void> _deleteGroup() async {
    if (!_isAdmin || _busy) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text("Delete group?"),
        content: const Text("Ye poora group, saare messages aur media permanently delete ho jaayenge — ye undo nahi ho sakta."),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text("Cancel")),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text("Delete", style: TextStyle(color: Colors.red))),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _busy = true);
    try {
      await MessageApiService.deleteGroup(widget.groupId);
      if (!mounted) return;
      // Group hi khatam ho gaya — seedha conversations list pe wapas.
      Navigator.of(context).pushAndRemoveUntil(
        MaterialPageRoute(builder: (_) => const ConversationsScreen()),
        (route) => false,
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Delete fail: $e")));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      appBar: AppBar(
        backgroundColor: _kNavy,
        elevation: 0,
        foregroundColor: Colors.white,
        title: const Text("Group info", style: TextStyle(fontWeight: FontWeight.w700)),
        actions: [
          if (!_loading && _isAdminOrMod)
            IconButton(icon: const Icon(Icons.edit_rounded), tooltip: "Edit", onPressed: _editGroupInfo),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: _kNavy))
          : _loadError != null
              ? Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(_loadError!, style: const TextStyle(color: Colors.red))))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.only(bottom: 24),
                    children: [
                      _buildHeader(),
                      const SizedBox(height: 10),
                      _buildPrivacyCard(),
                      if (_canSeeMembersAndLink && _inviteCode != null) _buildInviteLinkCard(),
                      if (_isPrivate && _isAdminOrMod) _buildJoinRequestsCard(),
                      const SizedBox(height: 10),
                      _buildMembersSection(),
                      const SizedBox(height: 16),
                      _buildDangerZone(),
                    ],
                  ),
                ),
    );
  }

  Widget _buildHeader() {
    return Container(
      width: double.infinity,
      color: Colors.white,
      padding: const EdgeInsets.symmetric(vertical: 24),
      child: Column(children: [
        CircleAvatar(
          radius: 44,
          backgroundColor: Colors.grey[200],
          backgroundImage: (_photoUrl != null && _photoUrl!.isNotEmpty) ? CachedNetworkImageProvider(_photoUrl!) : null,
          child: (_photoUrl == null || _photoUrl!.isEmpty) ? Icon(Icons.group_rounded, size: 40, color: Colors.grey[500]) : null,
        ),
        const SizedBox(height: 12),
        Text(_name, style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w700, color: _kNavy), textAlign: TextAlign.center),
        if (_description.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 6, left: 24, right: 24),
            child: Text(_description, style: TextStyle(fontSize: 13, color: Colors.grey[600]), textAlign: TextAlign.center),
          ),
      ]),
    );
  }

  Widget _buildPrivacyCard() {
    return Container(
      width: double.infinity,
      color: Colors.white,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      child: Row(children: [
        Icon(_isPrivate ? Icons.lock_rounded : Icons.public_rounded, color: _kNavy, size: 20),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(_isPrivate ? "Private group" : "Public group", style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
            Text(
              _isPrivate
                  ? "Sirf admin/moderator members list aur invite link access kar sakte hain"
                  : "Har member invite link bhej sakta hai aur members list dekh sakta hai",
              style: TextStyle(fontSize: 11.5, color: Colors.grey[600]),
            ),
          ]),
        ),
      ]),
    );
  }

  Widget _buildInviteLinkCard() {
    return Container(
      width: double.infinity,
      color: Colors.white,
      margin: const EdgeInsets.only(top: 1),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      child: Row(children: [
        const Icon(Icons.link_rounded, color: _kAccent, size: 20),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text("Invite link", style: TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
            Text(_inviteLink, style: TextStyle(fontSize: 12, color: Colors.grey[600]), overflow: TextOverflow.ellipsis),
          ]),
        ),
        IconButton(icon: const Icon(Icons.copy_rounded, size: 19, color: _kNavy), onPressed: _copyInviteLink),
      ]),
    );
  }

  // 🔥 NAYA — private group me pending join-requests (sirf admin/moderator
  // ko dikhta hai). Har request ke saath ek chhota Approve/Reject action
  // hai — approve karte hi requester turant member ban jaata hai aur
  // members list/count apne-aap refresh ho jaati hai.
  Widget _buildJoinRequestsCard() {
    if (_loadingRequests) {
      return Container(
        width: double.infinity,
        color: Colors.white,
        margin: const EdgeInsets.only(top: 1),
        padding: const EdgeInsets.all(16),
        child: const Center(child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: _kNavy))),
      );
    }
    if (_joinRequests.isEmpty) return const SizedBox.shrink();
    return Container(
      width: double.infinity,
      color: Colors.white,
      margin: const EdgeInsets.only(top: 1),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Text("Join requests (${_joinRequests.length})", style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: _kNavy)),
        ),
        ..._joinRequests.map((r) => ListTile(
              leading: CircleAvatar(
                radius: 18,
                backgroundColor: Colors.grey[200],
                backgroundImage: (r['avatar'] != null && (r['avatar'] as String).isNotEmpty) ? CachedNetworkImageProvider(r['avatar']) : null,
                child: (r['avatar'] == null || (r['avatar'] as String).isEmpty) ? Icon(Icons.person_rounded, color: Colors.grey[500], size: 18) : null,
              ),
              title: Text(r['name'] as String, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
              subtitle: (r['username'] as String).isNotEmpty ? Text('@${r['username']}', style: TextStyle(fontSize: 11, color: Colors.grey[600])) : null,
              trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                IconButton(icon: const Icon(Icons.check_circle_rounded, color: Colors.green), onPressed: () => _respondToJoinRequest(r['request_id'], true)),
                IconButton(icon: const Icon(Icons.cancel_rounded, color: Colors.red), onPressed: () => _respondToJoinRequest(r['request_id'], false)),
              ]),
            )),
      ]),
    );
  }

  Widget _buildMembersSection() {
    if (!_canSeeMembersAndLink) {
      // Private group + main sirf ek regular member — list/count hide.
      return Container(
        width: double.infinity,
        color: Colors.white,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
        child: Row(children: [
          Icon(Icons.visibility_off_rounded, color: Colors.grey[500], size: 18),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              "Ye private group hai — members list sirf admin/moderator ko dikhti hai",
              style: TextStyle(fontSize: 12.5, color: Colors.grey[600]),
            ),
          ),
        ]),
      );
    }

    return Container(
      width: double.infinity,
      color: Colors.white,
      child: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 6),
          child: Row(children: [
            Expanded(
              child: Text("${_members.length} Members", style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: _kNavy)),
            ),
            if (_canAddMembers)
              TextButton.icon(
                onPressed: _openAddMembers,
                icon: const Icon(Icons.person_add_alt_1_rounded, size: 17, color: _kAccent),
                label: const Text("Add", style: TextStyle(color: _kAccent, fontWeight: FontWeight.w600)),
              ),
          ]),
        ),
        ListView.builder(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          itemCount: _members.length,
          itemBuilder: (context, index) {
            final m = _members[index];
            final isMe = m['id'] == _myUserId;
            final role = m['role'] as String;
            return ListTile(
              onTap: (_isAdmin && !isMe) ? () => _showMemberActions(m) : null,
              leading: CircleAvatar(
                radius: 20,
                backgroundColor: Colors.grey[200],
                backgroundImage: (m['avatar'] != null && (m['avatar'] as String).isNotEmpty) ? CachedNetworkImageProvider(m['avatar']) : null,
                child: (m['avatar'] == null || (m['avatar'] as String).isEmpty) ? Icon(Icons.person_rounded, color: Colors.grey[500]) : null,
              ),
              title: Text("${m['name']}${isMe ? ' (You)' : ''}", style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
              subtitle: (m['username'] as String).isNotEmpty ? Text('@${m['username']}', style: TextStyle(fontSize: 11.5, color: Colors.grey[600])) : null,
              trailing: role == 'member'
                  ? null
                  : Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                      decoration: BoxDecoration(
                        color: role == 'admin' ? _kAccent.withOpacity(0.12) : _kNavy.withOpacity(0.08),
                        borderRadius: BorderRadius.circular(20),
                      ),
                      child: Text(
                        role == 'admin' ? "Admin" : "Moderator",
                        style: TextStyle(fontSize: 10.5, fontWeight: FontWeight.w700, color: role == 'admin' ? _kAccent : _kNavy),
                      ),
                    ),
            );
          },
        ),
      ]),
    );
  }

  Widget _buildDangerZone() {
    return Container(
      width: double.infinity,
      color: Colors.white,
      child: Column(children: [
        ListTile(
          leading: const Icon(Icons.exit_to_app_rounded, color: Colors.red),
          title: const Text("Leave group", style: TextStyle(color: Colors.red, fontWeight: FontWeight.w600)),
          onTap: _busy ? null : _leaveGroup,
        ),
        // 🔥 Sirf ADMIN — moderator ko bhi ye button nahi dikhta (backend
        // `destroy()` bhi strictly role == 'admin' hi allow karta hai).
        if (_isAdmin)
          ListTile(
            leading: const Icon(Icons.delete_forever_rounded, color: Colors.red),
            title: const Text("Delete group", style: TextStyle(color: Colors.red, fontWeight: FontWeight.w600)),
            onTap: _busy ? null : _deleteGroup,
          ),
      ]),
    );
  }
}

// ========================================================================
// ADD MEMBERS — bottom sheet, "New Group" screen jaisa hi search + select
// pattern, bas yahan existing group me `addGroupMembers()` call karta hai.
// ========================================================================
class _AddMembersSheet extends StatefulWidget {
  final String groupId;
  final Set<String> existingMemberIds;
  const _AddMembersSheet({required this.groupId, required this.existingMemberIds});

  @override
  State<_AddMembersSheet> createState() => _AddMembersSheetState();
}

class _AddMembersSheetState extends State<_AddMembersSheet> {
  final _searchCtrl = TextEditingController();
  List<Map<String, dynamic>> _results = [];
  final Set<String> _picked = {};
  bool _searching = false;
  bool _adding = false;
  String? _error;

  Future<void> _search(String query) async {
    if (query.trim().isEmpty) {
      setState(() => _results = []);
      return;
    }
    setState(() => _searching = true);
    try {
      final res = await MessageApiService.searchUsers(query);
      if (!mounted) return;
      setState(() {
        _results = res.where((u) => !widget.existingMemberIds.contains(u['id']?.toString())).toList();
        _searching = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _searching = false;
        _error = e.toString();
      });
    }
  }

  Future<void> _confirmAdd() async {
    if (_picked.isEmpty) return;
    setState(() => _adding = true);
    try {
      await MessageApiService.addGroupMembers(widget.groupId, _picked.toList());
      if (!mounted) return;
      Navigator.pop(context, true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _adding = false;
        _error = "Add fail: $e";
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: Container(
        height: MediaQuery.of(context).size.height * 0.75,
        decoration: const BoxDecoration(color: Colors.white, borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
        child: Column(children: [
          const SizedBox(height: 10),
          Container(width: 40, height: 4, decoration: BoxDecoration(color: Colors.grey[300], borderRadius: BorderRadius.circular(4))),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
            child: TextField(
              controller: _searchCtrl,
              onChanged: _search,
              decoration: InputDecoration(
                hintText: "Search users to add",
                prefixIcon: const Icon(Icons.search_rounded),
                filled: true,
                fillColor: _kBg,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
              ),
            ),
          ),
          if (_error != null) Padding(padding: const EdgeInsets.symmetric(horizontal: 16), child: Text(_error!, style: const TextStyle(color: Colors.red, fontSize: 12))),
          Expanded(
            child: _searching
                ? const Center(child: CircularProgressIndicator(color: _kNavy))
                : ListView.builder(
                    itemCount: _results.length,
                    itemBuilder: (context, i) {
                      final u = _results[i];
                      final id = u['id']?.toString() ?? '';
                      final selected = _picked.contains(id);
                      final first = (u['first_name'] ?? '').toString().trim();
                      final last = (u['last_name'] ?? '').toString().trim();
                      final name = [first, last].where((s) => s.isNotEmpty).join(' ');
                      return CheckboxListTile(
                        value: selected,
                        onChanged: (v) => setState(() => v == true ? _picked.add(id) : _picked.remove(id)),
                        title: Text(name.isNotEmpty ? name : (u['username']?.toString() ?? 'User')),
                        subtitle: Text('@${u['username'] ?? ''}'),
                        activeColor: _kAccent,
                      );
                    },
                  ),
          ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: (_picked.isEmpty || _adding) ? null : _confirmAdd,
                  style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white, padding: const EdgeInsets.symmetric(vertical: 14)),
                  child: _adding
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : Text(_picked.isEmpty ? "Add members" : "Add ${_picked.length} member${_picked.length > 1 ? 's' : ''}"),
                ),
              ),
            ),
          ),
        ]),
      ),
    );
  }
}