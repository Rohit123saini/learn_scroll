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
//
// 🔥 NAYA (this pass):
//   • Public <-> Private ab yahin se toggle ho sakta hai (Switch on the
//     privacy card) — admin/moderator only.
//   • "Who can send messages": Everyone / Admins & moderators only.
//   • "Daily message limit per member": No limit / 1 / 2 / 5 / 10 / 20 —
//     admin/moderator hamesha exempt hain. Enforced on the BACKEND
//     (`group_rules.check_group_send_permission`, called from both the
//     REST send endpoint and the WebSocket consumer) — this UI is just
//     the settings surface, the actual rule can't be bypassed by editing
//     the app.
//   • Group photo: tap the avatar (admin/moderator only) to change or
//     remove it.
//   • Name & description: tap the text itself (admin/moderator only) —
//     it turns into a live text field right there (WhatsApp/Instagram
//     style inline edit), with tick/cross to save/cancel. No dialog, no
//     separate "Edit" screen.

import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart'; // Clipboard — invite link copy
import 'package:cached_network_image/cached_network_image.dart';
import 'package:image_picker/image_picker.dart'; // 🔧 FIX — group photo pick ke liye chahiye tha, missing tha

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

  // 🔥 NAYA — "kaun message bhej sakta hai" aur "daily message limit"
  String _messagePermission = 'everyone'; // 'everyone' | 'admins_mods'
  int? _dailyMessageLimit; // null = unlimited
  bool _savingSettings = false;

  // 🔥 NAYA — "kaun call start kar sakta hai" / "kaun study room start kar
  // sakta hai". Same shape as `_messagePermission` — 'everyone' | 'admins_mods'.
  // Backend enforce karta hai (`group_rules.check_group_call_permission` /
  // `check_group_study_room_permission`), ye UI sirf settings surface hai.
  String _callPermission = 'everyone';
  String _studyRoomPermission = 'everyone';

  String? _myRole; // 'admin' | 'moderator' | 'member' | null
  bool get _isAdmin => _myRole == 'admin';
  bool get _isAdminOrMod => _myRole == 'admin' || _myRole == 'moderator';
  // Public group me sab kuch khula hai; private me sirf admin/moderator.
  bool get _canSeeMembersAndLink => !_isPrivate || _isAdminOrMod;
  bool get _canAddMembers => !_isPrivate || _isAdminOrMod;

  // 🔥 NAYA — private group ki pending join-requests (admin/moderator only)
  List<Map<String, dynamic>> _joinRequests = [];
  bool _loadingRequests = false;

  // 🔥 NAYA — inline tap-to-edit for name/description (WhatsApp/Instagram
  // style — tap the text itself, it turns into a live text field right
  // there, tick/cross to save/cancel). Replaces the old "Edit" dialog.
  bool _editingName = false;
  bool _editingDescription = false;
  bool _savingName = false;
  bool _savingDescription = false;
  final TextEditingController _nameCtrl = TextEditingController();
  final TextEditingController _descCtrl = TextEditingController();
  final FocusNode _nameFocus = FocusNode();
  final FocusNode _descFocus = FocusNode();

  bool _busy = false; // leave/delete/promote jaisi actions ke waqt double-tap se bachne ke liye

  @override
  void dispose() {
    _nameCtrl.dispose();
    _descCtrl.dispose();
    _nameFocus.dispose();
    _descFocus.dispose();
    super.dispose();
  }

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
        _messagePermission = data['message_permission']?.toString() ?? 'everyone';
        _dailyMessageLimit = data['daily_message_limit'] is int
            ? data['daily_message_limit'] as int
            : int.tryParse(data['daily_message_limit']?.toString() ?? '');
        _callPermission = data['call_permission']?.toString() ?? 'everyone';
        _studyRoomPermission = data['study_room_permission']?.toString() ?? 'everyone';
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
  // INLINE EDIT — NAME (tap the name itself, admin + moderator only)
  // ------------------------------------------------------------------
  void _startEditName() {
    if (!_isAdminOrMod || _busy || _editingName) return;
    _nameCtrl.text = _name;
    _nameCtrl.selection = TextSelection(baseOffset: 0, extentOffset: _nameCtrl.text.length);
    setState(() => _editingName = true);
    WidgetsBinding.instance.addPostFrameCallback((_) => _nameFocus.requestFocus());
  }

  void _cancelEditName() {
    if (_savingName) return;
    setState(() => _editingName = false);
  }

  Future<void> _saveName() async {
    final newName = _nameCtrl.text.trim();
    if (newName.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Group name khali nahi ho sakta")),
      );
      return;
    }
    if (newName == _name) {
      setState(() => _editingName = false);
      return;
    }
    setState(() => _savingName = true);
    try {
      await MessageApiService.updateGroup(widget.groupId, {'name': newName});
      if (!mounted) return;
      setState(() {
        _name = newName;
        _editingName = false;
        _savingName = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _savingName = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Name update fail: $e")));
    }
  }

  // ------------------------------------------------------------------
  // INLINE EDIT — DESCRIPTION (tap the description itself, admin + moderator only)
  // ------------------------------------------------------------------
  void _startEditDescription() {
    if (!_isAdminOrMod || _busy || _editingDescription) return;
    _descCtrl.text = _description;
    setState(() => _editingDescription = true);
    WidgetsBinding.instance.addPostFrameCallback((_) => _descFocus.requestFocus());
  }

  void _cancelEditDescription() {
    if (_savingDescription) return;
    setState(() => _editingDescription = false);
  }

  Future<void> _saveDescription() async {
    final newDesc = _descCtrl.text.trim();
    if (newDesc == _description) {
      setState(() => _editingDescription = false);
      return;
    }
    setState(() => _savingDescription = true);
    try {
      await MessageApiService.updateGroup(widget.groupId, {'description': newDesc});
      if (!mounted) return;
      setState(() {
        _description = newDesc;
        _editingDescription = false;
        _savingDescription = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _savingDescription = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Description update fail: $e")));
    }
  }

  // ------------------------------------------------------------------
  // PRIVACY TOGGLE — public <-> private (admin + moderator)
  // ------------------------------------------------------------------
  Future<void> _togglePrivacy(bool makePrivate) async {
    if (_savingSettings) return;
    final prev = _isPrivate;
    setState(() {
      _isPrivate = makePrivate;
      _savingSettings = true;
    });
    try {
      await MessageApiService.updateGroup(widget.groupId, {'is_private': makePrivate});
      if (mounted) setState(() => _savingSettings = false);
      // private ho gaya to admin/moderator ke liye join-requests card bhi
      // relevant ho sakta hai, refresh kar lo.
      if (makePrivate && _isAdminOrMod) _loadJoinRequests();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isPrivate = prev; // rollback
        _savingSettings = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Privacy update fail: $e")));
    }
  }

  // ------------------------------------------------------------------
  // MESSAGE PERMISSION — 'everyone' | 'admins_mods' (admin + moderator)
  // ------------------------------------------------------------------
  Future<void> _updateMessagePermission(String value) async {
    if (_savingSettings || value == _messagePermission) return;
    final prev = _messagePermission;
    setState(() {
      _messagePermission = value;
      _savingSettings = true;
    });
    try {
      await MessageApiService.updateGroup(widget.groupId, {'message_permission': value});
      if (mounted) setState(() => _savingSettings = false);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _messagePermission = prev;
        _savingSettings = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Update fail: $e")));
    }
  }

  // ------------------------------------------------------------------
  // DAILY MESSAGE LIMIT — null (unlimited) | 1 | 2 | 5 | 10 | 20 (admin + moderator)
  // ------------------------------------------------------------------
  Future<void> _updateDailyLimit(int? value) async {
    if (_savingSettings || value == _dailyMessageLimit) return;
    final prev = _dailyMessageLimit;
    setState(() {
      _dailyMessageLimit = value;
      _savingSettings = true;
    });
    try {
      await MessageApiService.updateGroup(widget.groupId, {'daily_message_limit': value});
      if (mounted) setState(() => _savingSettings = false);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _dailyMessageLimit = prev;
        _savingSettings = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Update fail: $e")));
    }
  }

  // ------------------------------------------------------------------
  // CALL PERMISSION — 'everyone' | 'admins_mods' (admin + moderator)
  // ------------------------------------------------------------------
  Future<void> _updateCallPermission(String value) async {
    if (_savingSettings || value == _callPermission) return;
    final prev = _callPermission;
    setState(() {
      _callPermission = value;
      _savingSettings = true;
    });
    try {
      await MessageApiService.updateGroup(widget.groupId, {'call_permission': value});
      if (mounted) setState(() => _savingSettings = false);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _callPermission = prev;
        _savingSettings = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Update fail: $e")));
    }
  }

  // ------------------------------------------------------------------
  // STUDY ROOM PERMISSION — 'everyone' | 'admins_mods' (admin + moderator)
  // ------------------------------------------------------------------
  Future<void> _updateStudyRoomPermission(String value) async {
    if (_savingSettings || value == _studyRoomPermission) return;
    final prev = _studyRoomPermission;
    setState(() {
      _studyRoomPermission = value;
      _savingSettings = true;
    });
    try {
      await MessageApiService.updateGroup(widget.groupId, {'study_room_permission': value});
      if (mounted) setState(() => _savingSettings = false);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _studyRoomPermission = prev;
        _savingSettings = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Update fail: $e")));
    }
  }

  // ------------------------------------------------------------------
  // GROUP PHOTO — change (upload + set) / remove (admin + moderator)
  // ------------------------------------------------------------------
  Future<void> _changeGroupPhoto() async {
    if (_busy) return;
    // Tumhare app me jo bhi image-picker already use ho raha hai (jaise
    // profile photo screen me) wahi yahan use karo — placeholder call:
    // final picked = await ImagePickerService.pickImage();
    // Neeche generic pattern hai jo tumhare `MessageApiService.uploadFile`
    // ke saath already-existing media-send flow jaisa hi hai.
    final File? pickedFile = await _pickImageFile(); // tumhare project ka image-picker helper
    if (pickedFile == null) return;

    setState(() => _busy = true);
    try {
      final uploaded = await MessageApiService.uploadFile(pickedFile);
      await MessageApiService.updateGroup(widget.groupId, {'photo_url': uploaded.fileUrl});
      if (!mounted) return;
      setState(() {
        _photoUrl = uploaded.fileUrl;
        _busy = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Photo update fail: $e")));
    }
  }

  Future<void> _removeGroupPhoto() async {
    if (_busy || _photoUrl == null || _photoUrl!.isEmpty) return;
    setState(() => _busy = true);
    try {
      await MessageApiService.removeGroupPhoto(widget.groupId);
      if (!mounted) return;
      setState(() {
        _photoUrl = null;
        _busy = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Photo remove fail: $e")));
    }
  }

  Future<void> _showPhotoOptions() async {
    if (!_isAdminOrMod || _busy) return;
    final action = await showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: const Icon(Icons.photo_camera_rounded, color: _kAccent),
            title: const Text("Change group photo"),
            onTap: () => Navigator.pop(ctx, 'change'),
          ),
          if (_photoUrl != null && _photoUrl!.isNotEmpty)
            ListTile(
              leading: const Icon(Icons.delete_outline_rounded, color: Colors.red),
              title: const Text("Remove photo", style: TextStyle(color: Colors.red)),
              onTap: () => Navigator.pop(ctx, 'remove'),
            ),
        ]),
      ),
    );
    if (action == 'change') await _changeGroupPhoto();
    if (action == 'remove') await _removeGroupPhoto();
  }

  // 🔧 FIX — ye pehle `UnimplementedError()` throw karta tha, isiliye
  // avatar pe tap karke photo change karna kabhi kaam hi nahi karta tha
  // (har baar seedha catch block me gir ke "Photo update fail" dikhata
  // tha). Ab wahi `ImagePicker` pattern use kiya hai jo chat wallpaper
  // (`chat_screen.dart` -> `_pickChatWallpaper`) me already kaam kar raha
  // hai — gallery se pick karo, imageQuality thoda compress kar do.
  Future<File?> _pickImageFile() async {
    final picked = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      imageQuality: 85,
    );
    if (picked == null) return null;
    return File(picked.path);
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
                      _buildPrivacyCard(),
                      _buildMessageRulesCard(),
                      if (_canSeeMembersAndLink && _inviteCode != null) _buildInviteLinkCard(),
                      if (_isPrivate && _isAdminOrMod) _buildJoinRequestsCard(),
                      const SizedBox(height: 14),
                      _buildMembersSection(),
                      const SizedBox(height: 16),
                      _buildDangerZone(),
                    ],
                  ),
                ),
    );
  }

  // 🎨 NAYA — Hero-style gradient header. Avatar + name + description sab
  // yahin inline-editable hain (admin/moderator only) — WhatsApp/
  // Instagram jaisa "tap on the text itself" pattern, koi separate
  // dialog/screen nahi. Har field ka apna save/cancel state hai taaki
  // dono ek saath bhi edit ho sakein.
  Widget _buildHeader() {
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [_kNavy, Color(0xFF10214F)],
        ),
        borderRadius: const BorderRadius.only(
          bottomLeft: Radius.circular(28),
          bottomRight: Radius.circular(28),
        ),
        boxShadow: [
          BoxShadow(color: _kNavy.withOpacity(0.25), blurRadius: 18, offset: const Offset(0, 8)),
        ],
      ),
      padding: const EdgeInsets.fromLTRB(24, 28, 24, 26),
      child: Column(children: [
        // ---------------- AVATAR ----------------
        Material(
          color: Colors.transparent,
          shape: const CircleBorder(),
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: _isAdminOrMod ? _showPhotoOptions : null,
            child: Stack(children: [
              Container(
                padding: const EdgeInsets.all(3),
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(color: Colors.white.withOpacity(0.85), width: 2.5),
                  boxShadow: [
                    BoxShadow(color: Colors.black.withOpacity(0.25), blurRadius: 14, offset: const Offset(0, 6)),
                  ],
                ),
                child: CircleAvatar(
                  radius: 46,
                  backgroundColor: Colors.white.withOpacity(0.12),
                  backgroundImage: (_photoUrl != null && _photoUrl!.isNotEmpty) ? CachedNetworkImageProvider(_photoUrl!) : null,
                  child: (_photoUrl == null || _photoUrl!.isEmpty) ? const Icon(Icons.group_rounded, size: 42, color: Colors.white70) : null,
                ),
              ),
              if (_isAdminOrMod)
                Positioned(
                  bottom: 2,
                  right: 2,
                  child: Container(
                    padding: const EdgeInsets.all(6),
                    decoration: BoxDecoration(
                      color: _kAccent,
                      shape: BoxShape.circle,
                      border: Border.all(color: _kNavy, width: 2),
                      boxShadow: [BoxShadow(color: _kAccent.withOpacity(0.5), blurRadius: 8)],
                    ),
                    child: const Icon(Icons.camera_alt_rounded, size: 14, color: Colors.white),
                  ),
                ),
            ]),
          ),
        ),
        const SizedBox(height: 18),

        // ---------------- NAME (inline editable) ----------------
        _buildInlineEditableField(
          isEditing: _editingName,
          isSaving: _savingName,
          canEdit: _isAdminOrMod,
          controller: _nameCtrl,
          focusNode: _nameFocus,
          onTapToEdit: _startEditName,
          onSave: _saveName,
          onCancel: _cancelEditName,
          maxLines: 1,
          textAlign: TextAlign.center,
          emptyPlaceholder: null,
          viewStyle: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: Colors.white, height: 1.25),
          editStyle: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: Colors.white, height: 1.25),
          displayText: _name,
          hintText: "Group name",
        ),

        const SizedBox(height: 8),

        // ---------------- ROLE BADGE ----------------
        if (_myRole != null)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
            decoration: BoxDecoration(
              color: Colors.white.withOpacity(0.12),
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: Colors.white.withOpacity(0.18)),
            ),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(
                _myRole == 'admin' ? Icons.shield_rounded : (_myRole == 'moderator' ? Icons.verified_user_rounded : Icons.person_rounded),
                size: 13, color: Colors.white70,
              ),
              const SizedBox(width: 5),
              Text(
                _myRole == 'admin' ? "You're an admin" : (_myRole == 'moderator' ? "You're a moderator" : "Member"),
                style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: Colors.white70),
              ),
            ]),
          ),

        const SizedBox(height: 14),

        // ---------------- DESCRIPTION (inline editable) ----------------
        _buildInlineEditableField(
          isEditing: _editingDescription,
          isSaving: _savingDescription,
          canEdit: _isAdminOrMod,
          controller: _descCtrl,
          focusNode: _descFocus,
          onTapToEdit: _startEditDescription,
          onSave: _saveDescription,
          onCancel: _cancelEditDescription,
          maxLines: 4,
          minLines: 1,
          textAlign: TextAlign.center,
          emptyPlaceholder: _isAdminOrMod ? "Add group description" : null,
          viewStyle: TextStyle(fontSize: 13, color: Colors.white.withOpacity(0.75), height: 1.4),
          editStyle: TextStyle(fontSize: 13, color: Colors.white.withOpacity(0.95), height: 1.4),
          displayText: _description,
          hintText: "Group description",
        ),
      ]),
    );
  }

  // 🎨 NAYA — reusable "tap text -> becomes live text field -> tick/cross
  // to save/cancel" widget. View mode me hover/tap par halka pencil icon
  // dikhta hai (sirf editable ho to), edit mode me AnimatedSwitcher se
  // smooth cross-fade + scale ke saath text field aata hai, saath me
  // ek chhota pill me tick (save) aur cross (cancel) icon — save ke waqt
  // pill ke andar hi chhota spinner dikhta hai.
  Widget _buildInlineEditableField({
    required bool isEditing,
    required bool isSaving,
    required bool canEdit,
    required TextEditingController controller,
    required FocusNode focusNode,
    required VoidCallback onTapToEdit,
    required Future<void> Function() onSave,
    required VoidCallback onCancel,
    required String displayText,
    required TextStyle viewStyle,
    required TextStyle editStyle,
    required String hintText,
    String? emptyPlaceholder,
    int maxLines = 1,
    int? minLines,
    TextAlign textAlign = TextAlign.start,
  }) {
    final hasText = displayText.trim().isNotEmpty;

    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 220),
      switchInCurve: Curves.easeOutCubic,
      switchOutCurve: Curves.easeInCubic,
      transitionBuilder: (child, animation) => FadeTransition(
        opacity: animation,
        child: ScaleTransition(scale: Tween(begin: 0.97, end: 1.0).animate(animation), child: child),
      ),
      child: isEditing
          ? Padding(
              key: const ValueKey('editing'),
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Container(
                  padding: EdgeInsets.symmetric(horizontal: 14, vertical: maxLines > 1 ? 8 : 4),
                  decoration: BoxDecoration(
                    color: Colors.white.withOpacity(0.14),
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: _kAccent.withOpacity(0.7), width: 1.4),
                  ),
                  child: TextField(
                    controller: controller,
                    focusNode: focusNode,
                    maxLines: maxLines,
                    minLines: minLines,
                    textAlign: textAlign,
                    style: editStyle,
                    cursorColor: _kAccent,
                    enabled: !isSaving,
                    textInputAction: maxLines > 1 ? TextInputAction.newline : TextInputAction.done,
                    onSubmitted: maxLines > 1 ? null : (_) => onSave(),
                    decoration: InputDecoration(
                      isDense: true,
                      border: InputBorder.none,
                      hintText: hintText,
                      hintStyle: editStyle.copyWith(color: Colors.white38),
                    ),
                  ),
                ),
                const SizedBox(height: 8),
                Row(mainAxisSize: MainAxisSize.min, children: [
                  _InlinePillButton(
                    icon: Icons.close_rounded,
                    color: Colors.white70,
                    background: Colors.white.withOpacity(0.1),
                    onTap: isSaving ? null : onCancel,
                  ),
                  const SizedBox(width: 10),
                  _InlinePillButton(
                    icon: Icons.check_rounded,
                    color: Colors.white,
                    background: _kAccent,
                    isLoading: isSaving,
                    onTap: isSaving ? null : onSave,
                  ),
                ]),
              ]),
            )
          : GestureDetector(
              key: const ValueKey('viewing'),
              onTap: canEdit ? onTapToEdit : null,
              behavior: HitTestBehavior.opaque,
              child: Container(
                padding: EdgeInsets.symmetric(horizontal: canEdit ? 10 : 0, vertical: 4),
                decoration: canEdit
                    ? BoxDecoration(borderRadius: BorderRadius.circular(10), color: Colors.white.withOpacity(0.0))
                    : null,
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  Flexible(
                    child: Text(
                      hasText ? displayText : (emptyPlaceholder ?? ''),
                      textAlign: textAlign,
                      maxLines: maxLines,
                      overflow: TextOverflow.ellipsis,
                      style: hasText
                          ? viewStyle
                          : viewStyle.copyWith(fontStyle: FontStyle.italic, color: Colors.white38),
                    ),
                  ),
                  if (canEdit) ...[
                    const SizedBox(width: 6),
                    Icon(Icons.edit_rounded, size: 13, color: Colors.white.withOpacity(0.45)),
                  ],
                ]),
              ),
            ),
    );
  }

  // 🎨 NAYA — shared "grouped settings" card shell (rounded corners, soft
  // shadow, margin around it) — WhatsApp/iOS Settings jaisa look, flat
  // full-bleed white bars ki jagah. Optional `label` upar ek small caps
  // section heading ke tor pe (jaise "PRIVACY", "PERMISSIONS") dikhta hai.
  Widget _cardShell({required Widget child, String? label, EdgeInsetsGeometry? padding}) {
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (label != null)
          Padding(
            padding: const EdgeInsets.only(left: 20, bottom: 6),
            child: Text(
              label.toUpperCase(),
              style: TextStyle(
                fontSize: 11.5,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.6,
                color: Colors.grey[500],
              ),
            ),
          ),
        Container(
          width: double.infinity,
          margin: const EdgeInsets.symmetric(horizontal: 12),
          padding: padding ?? const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(16),
            boxShadow: [
              BoxShadow(color: Colors.black.withOpacity(0.045), blurRadius: 10, offset: const Offset(0, 3)),
            ],
          ),
          child: child,
        ),
      ]),
    );
  }

  Widget _buildPrivacyCard() {
    return _cardShell(
      label: 'Privacy',
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
        // 🔥 NAYA — sirf admin/moderator hi public<->private switch kar
        // sakte hain, baaki members ko sirf read-only dikhta hai (icon+text).
        if (_isAdminOrMod)
          Switch(
            value: _isPrivate,
            activeColor: _kAccent,
            onChanged: _savingSettings ? null : _togglePrivacy,
          ),
      ]),
    );
  }

  // 🔥 "Kaun message bhej sakta hai" (Everyone / sirf Admin & Moderator)
  // aur "Daily message limit" (Unlimited / 1 / 2 / 5 / 10 / 20) — dono
  // sirf admin/moderator ko dikhte/change karne layak hain, baaki members
  // ke liye ye card poori tarah hidden rehta hai.
  Widget _buildMessageRulesCard() {
    if (!_isAdminOrMod) return const SizedBox.shrink();
    return _cardShell(
      label: 'Permissions',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text("Messaging", style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: _kNavy)),
        const SizedBox(height: 10),
        Row(children: [
          Icon(Icons.forum_outlined, color: _kNavy, size: 19),
          const SizedBox(width: 12),
          const Expanded(child: Text("Who can send messages", style: TextStyle(fontSize: 13.5))),
          DropdownButton<String>(
            value: _messagePermission,
            underline: const SizedBox.shrink(),
            items: const [
              DropdownMenuItem(value: 'everyone', child: Text("Everyone", style: TextStyle(fontSize: 13))),
              DropdownMenuItem(value: 'admins_mods', child: Text("Admins & mods", style: TextStyle(fontSize: 13))),
            ],
            onChanged: _savingSettings ? null : (v) { if (v != null) _updateMessagePermission(v); },
          ),
        ]),
        const Divider(height: 22),
        Row(children: [
          Icon(Icons.timer_outlined, color: _kNavy, size: 19),
          const SizedBox(width: 12),
          const Expanded(child: Text("Daily message limit per member", style: TextStyle(fontSize: 13.5))),
          DropdownButton<int?>(
            value: _dailyMessageLimit,
            underline: const SizedBox.shrink(),
            items: const [
              DropdownMenuItem(value: null, child: Text("No limit", style: TextStyle(fontSize: 13))),
              DropdownMenuItem(value: 1, child: Text("1 / day", style: TextStyle(fontSize: 13))),
              DropdownMenuItem(value: 2, child: Text("2 / day", style: TextStyle(fontSize: 13))),
              DropdownMenuItem(value: 5, child: Text("5 / day", style: TextStyle(fontSize: 13))),
              DropdownMenuItem(value: 10, child: Text("10 / day", style: TextStyle(fontSize: 13))),
              DropdownMenuItem(value: 20, child: Text("20 / day", style: TextStyle(fontSize: 13))),
            ],
            onChanged: _savingSettings ? null : (v) => _updateDailyLimit(v),
          ),
        ]),
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Text(
            "Admins & moderators are always exempt from the daily limit.",
            style: TextStyle(fontSize: 11, color: Colors.grey[500]),
          ),
        ),

        const Divider(height: 26),

        // ---------------- CALLS ----------------
        const Text("Calls", style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: _kNavy)),
        const SizedBox(height: 10),
        Row(children: [
          Icon(Icons.call_outlined, color: _kNavy, size: 19),
          const SizedBox(width: 12),
          const Expanded(child: Text("Who can start a call", style: TextStyle(fontSize: 13.5))),
          DropdownButton<String>(
            value: _callPermission,
            underline: const SizedBox.shrink(),
            items: const [
              DropdownMenuItem(value: 'everyone', child: Text("Everyone", style: TextStyle(fontSize: 13))),
              DropdownMenuItem(value: 'admins_mods', child: Text("Admins & mods", style: TextStyle(fontSize: 13))),
            ],
            onChanged: _savingSettings ? null : (v) { if (v != null) _updateCallPermission(v); },
          ),
        ]),

        const Divider(height: 26),

        // ---------------- STUDY ROOM ----------------
        const Text("Study room", style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: _kNavy)),
        const SizedBox(height: 10),
        Row(children: [
          Icon(Icons.school_outlined, color: _kNavy, size: 19),
          const SizedBox(width: 12),
          const Expanded(child: Text("Who can start the study room", style: TextStyle(fontSize: 13.5))),
          DropdownButton<String>(
            value: _studyRoomPermission,
            underline: const SizedBox.shrink(),
            items: const [
              DropdownMenuItem(value: 'everyone', child: Text("Everyone", style: TextStyle(fontSize: 13))),
              DropdownMenuItem(value: 'admins_mods', child: Text("Admins & mods", style: TextStyle(fontSize: 13))),
            ],
            onChanged: _savingSettings ? null : (v) { if (v != null) _updateStudyRoomPermission(v); },
          ),
        ]),
        if (_isPrivate)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: Text(
              "This group is private — messages, calls and the study room default to admins & moderators only until you change them here.",
              style: TextStyle(fontSize: 11, color: Colors.grey[500]),
            ),
          ),
      ]),
    );
  }

  Widget _buildInviteLinkCard() {
    return _cardShell(
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

  // 🔥 private group me pending join-requests (sirf admin/moderator ko
  // dikhta hai). Har request ke saath ek chhota Approve/Reject action hai —
  // approve karte hi requester turant member ban jaata hai aur members
  // list/count apne-aap refresh ho jaati hai.
  Widget _buildJoinRequestsCard() {
    if (_loadingRequests) {
      return _cardShell(
        padding: const EdgeInsets.all(16),
        child: const Center(child: SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: _kNavy))),
      );
    }
    if (_joinRequests.isEmpty) return const SizedBox.shrink();
    return _cardShell(
      label: 'Pending requests',
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 6, 4, 4),
          child: Row(children: [
            const Text("Join requests", style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13.5, color: _kNavy)),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(color: _kAccent.withOpacity(0.12), borderRadius: BorderRadius.circular(20)),
              child: Text('${_joinRequests.length}', style: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: _kAccent)),
            ),
          ]),
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
      return _cardShell(
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

    return _cardShell(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 6, 4, 6),
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
    return _cardShell(
      padding: const EdgeInsets.symmetric(vertical: 4),
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
// Small circular pill button used by the inline-edit tick/cross controls.
// Own tiny widget so the ripple + loading-spinner swap is self-contained.
// ========================================================================
class _InlinePillButton extends StatelessWidget {
  final IconData icon;
  final Color color;
  final Color background;
  final VoidCallback? onTap;
  final bool isLoading;
  const _InlinePillButton({
    required this.icon,
    required this.color,
    required this.background,
    required this.onTap,
    this.isLoading = false,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: background,
      shape: const CircleBorder(),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: SizedBox(
          width: 34,
          height: 34,
          child: Center(
            child: isLoading
                ? SizedBox(
                    width: 15,
                    height: 15,
                    child: CircularProgressIndicator(strokeWidth: 2, color: color),
                  )
                : Icon(icon, size: 18, color: color),
          ),
        ),
      ),
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