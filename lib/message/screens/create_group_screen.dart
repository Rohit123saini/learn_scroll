// message/screens/create_group_screen.dart
//
// 🎨 REDESIGN — visuals polished (chips, search results, bottom bar), sab
// logic bilkul same rakha hai — especially wo critical fix jahan sirf
// `user['id']` (UUID) bheja jaata hai backend ko, kabhi username nahi.
// Isi wajah se pehle wala `{0: [Must be a valid UUID.]}` error fix rehta hai.
//
// NOTE: create/setup screen hai (WhatsApp/Instagram me bhi "new group" ya
// "new post" screen par bottom tab bar nahi hoti — ye ek focused task hai),
// isliye yahan bottom nav nahi laga — agar chahiye to bata dena, add kar dunga.

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';

import '../services/message_api_service.dart';
import 'conversations_screen.dart';

const _kNavy = Color(0xFF030F27);
const _kAccent = Color(0xFFEE0979);
const _kBg = Color(0xFFF6F7FB);

class CreateGroupScreen extends StatefulWidget {
  const CreateGroupScreen({super.key});

  @override
  State<CreateGroupScreen> createState() => _CreateGroupScreenState();
}

class _CreateGroupScreenState extends State<CreateGroupScreen> {
  final _searchController = TextEditingController();
  final _groupNameController = TextEditingController();
  Timer? _debounce;

  List<Map<String, dynamic>> _searchResults = [];
  bool _isSearching = false;
  String? _searchError;

  final List<Map<String, dynamic>> _selectedUsers = [];

  bool _isCreating = false;
  String? _createError;

  // 🔥 NAYA — group banate waqt hi admin (creator) ko choose karna hai
  // group PUBLIC hoga ya PRIVATE. Ye choice `createGroup(isPrivate: ...)`
  // ke through backend ko jaati hai — backend (`GroupViewSet.create`)
  // isi flag ko `Group.is_private` me store karta hai, aur usi ke basis
  // par baad me members/invite-link/join-request rules apply hote hain.
  // Default PUBLIC rakha hai (jaisa pehle bhi tha, kyunki isPrivate ka
  // default value `false` hi tha).
  bool _isPrivate = false;

  @override
  void dispose() {
    _debounce?.cancel();
    _searchController.dispose();
    _groupNameController.dispose();
    super.dispose();
  }

  void _onSearchChanged(String query) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), () => _runSearch(query));
  }

  Future<void> _runSearch(String query) async {
    if (query.trim().isEmpty) {
      setState(() => _searchResults = []);
      return;
    }
    setState(() {
      _isSearching = true;
      _searchError = null;
    });
    try {
      final results = await MessageApiService.searchUsers(query);
      if (!mounted) return;
      setState(() {
        _searchResults = results;
        _isSearching = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isSearching = false;
        _searchError = e.toString();
      });
    }
  }

  String _displayName(Map<String, dynamic> user) {
    final first = (user['first_name'] ?? '').toString().trim();
    final last = (user['last_name'] ?? '').toString().trim();
    final full = [first, last].where((s) => s.isNotEmpty).join(' ');
    if (full.isNotEmpty) return full;
    final username = (user['username'] ?? '').toString().trim();
    return username.isNotEmpty ? username : 'User';
  }

  bool _isSelected(Map<String, dynamic> user) {
    final id = user['id']?.toString();
    return _selectedUsers.any((u) => u['id']?.toString() == id);
  }

  void _toggleSelect(Map<String, dynamic> user) {
    final id = user['id']?.toString();
    if (id == null || id.isEmpty) {
      // Agar backend se id hi nahi aayi to select mat hone do — warna
      // wahi UUID error phir se aayega.
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Ye user select nahi ho sakta (id missing).")),
      );
      return;
    }
    setState(() {
      if (_isSelected(user)) {
        _selectedUsers.removeWhere((u) => u['id']?.toString() == id);
      } else {
        _selectedUsers.add(user);
      }
    });
  }

  void _removeSelected(Map<String, dynamic> user) {
    final id = user['id']?.toString();
    setState(() {
      _selectedUsers.removeWhere((u) => u['id']?.toString() == id);
    });
  }

  Future<void> _createGroup() async {
    final name = _groupNameController.text.trim();

    if (name.isEmpty) {
      setState(() => _createError = "Group ka naam daalna zaroori hai.");
      return;
    }
    if (_selectedUsers.length < 2) {
      setState(() => _createError = "Kam se kam 2 members select karo.");
      return;
    }

    // 🔥 Yahi wo critical line hai — sirf 'id' (UUID) bheja ja raha hai,
    // kuch aur nahi (na username, na poora object).
    final memberIds = _selectedUsers
        .map((u) => u['id'].toString())
        .where((id) => id.isNotEmpty)
        .toList();

    setState(() {
      _isCreating = true;
      _createError = null;
    });

    try {
      final result = await MessageApiService.createGroup(
        name: name,
        memberIds: memberIds,
        isPrivate: _isPrivate, // 🔥 NAYA — chuna hua Public/Private type
      );

      if (!mounted) return;

      if (result.containsKey('member_ids') ||
          result.containsKey('detail') ||
          result.containsKey('error')) {
        setState(() {
          _isCreating = false;
          _createError = result.toString();
        });
        return;
      }

      setState(() => _isCreating = false);

      if (!mounted) return;
      Navigator.of(context).pushAndRemoveUntil(
        MaterialPageRoute(builder: (_) => const ConversationsScreen()),
        (route) => false,
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _isCreating = false;
        _createError = "Group create nahi ho paya: $e";
      });
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
        title: const Text("New Group", style: TextStyle(fontWeight: FontWeight.w700)),
      ),
      body: Column(
        children: [
          if (_selectedUsers.isNotEmpty) _buildSelectedChips(),
          _buildGroupTypeChoice(), // 🔥 NAYA — Public / Private choose karo
          _buildSearchField(),
          if (_createError != null)
            Container(
              margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              decoration: BoxDecoration(
                color: Colors.red.withOpacity(0.08),
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: Colors.red.withOpacity(0.2)),
              ),
              child: Row(children: [
                const Icon(Icons.error_outline_rounded, color: Colors.red, size: 18),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(_createError!, style: const TextStyle(color: Colors.red, fontSize: 13)),
                ),
              ]),
            ),
          Expanded(child: _buildSearchResults()),
          _buildBottomBar(),
        ],
      ),
    );
  }

  Widget _buildSelectedChips() {
    return Container(
      width: double.infinity,
      color: Colors.white,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      child: Wrap(
        spacing: 8,
        runSpacing: 8,
        children: _selectedUsers.map((user) {
          return Chip(
            avatar: CircleAvatar(
              backgroundColor: Colors.grey[200],
              backgroundImage: (user['avatar'] != null && user['avatar'].toString().isNotEmpty)
                  ? CachedNetworkImageProvider(user['avatar'].toString())
                  : null,
              child: (user['avatar'] == null || user['avatar'].toString().isEmpty)
                  ? const Icon(Icons.person_rounded, size: 16, color: Colors.grey)
                  : null,
            ),
            label: Text(_displayName(user), style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w500)),
            onDeleted: () => _removeSelected(user),
            deleteIconColor: Colors.grey[500],
            backgroundColor: _kBg,
            side: BorderSide(color: Colors.grey[300]!),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
          );
        }).toList(),
      ),
    );
  }

  // 🔥 NAYA — "New Group" screen ke andar hi Public/Private group-type
  // picker. Dono cards ke neeche chhota sa explainer hai taaki creator ko
  // pata rahe is choice ka baad me kya asar hoga:
  //  • PUBLIC  → koi bhi member group link share/bhej sakta hai, sabko
  //              members ki poori list/count dikhti hai, aur koi bhi
  //              member naye logo ko seedha add kar sakta hai.
  //  • PRIVATE → group link sirf ADMIN/MODERATOR bhej sakte hain, members
  //              list/count sirf admin/moderator ko dikhti hai (baaki
  //              members ko nahi), naye members sirf admin/moderator hi
  //              add kar sakte hain (ya invite-link se join-request bhej
  //              ke admin approval ka wait).
  Widget _buildGroupTypeChoice() {
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.fromLTRB(14, 4, 14, 4),
      child: Row(
        children: [
          Expanded(
            child: _groupTypeCard(
              selected: !_isPrivate,
              icon: Icons.public_rounded,
              title: "Public",
              subtitle: "Sabko link, members list dikhegi",
              onTap: () => setState(() => _isPrivate = false),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: _groupTypeCard(
              selected: _isPrivate,
              icon: Icons.lock_rounded,
              title: "Private",
              subtitle: "Sirf admin/mod ko link, members list dikhegi",
              onTap: () => setState(() => _isPrivate = true),
            ),
          ),
        ],
      ),
    );
  }

  Widget _groupTypeCard({
    required bool selected,
    required IconData icon,
    required String title,
    required String subtitle,
    required VoidCallback onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: selected ? const Color(0xFFFFF0F6) : _kBg,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: selected ? _kAccent : Colors.grey[300]!, width: selected ? 1.4 : 1),
        ),
        child: Row(
          children: [
            Icon(icon, size: 20, color: selected ? _kAccent : Colors.grey[600]),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(title, style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700, color: selected ? _kNavy : Colors.grey[800])),
                  const SizedBox(height: 1),
                  Text(subtitle, style: TextStyle(fontSize: 10.5, color: Colors.grey[600]), maxLines: 2),
                ],
              ),
            ),
            const SizedBox(width: 4),
            Icon(
              selected ? Icons.check_circle_rounded : Icons.radio_button_unchecked,
              size: 18,
              color: selected ? _kAccent : Colors.grey[400],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildSearchField() {
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.fromLTRB(14, 4, 14, 12),
      child: TextField(
        controller: _searchController,
        onChanged: _onSearchChanged,
        decoration: InputDecoration(
          hintText: "Search users to add",
          hintStyle: TextStyle(color: Colors.grey[500], fontSize: 14),
          prefixIcon: Icon(Icons.search_rounded, color: Colors.grey[500]),
          filled: true,
          fillColor: _kBg,
          contentPadding: const EdgeInsets.symmetric(vertical: 0, horizontal: 12),
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
        ),
      ),
    );
  }

  Widget _buildSearchResults() {
    if (_isSearching) {
      return const Center(child: CircularProgressIndicator(color: _kNavy));
    }
    if (_searchError != null) {
      return Center(
        child: Text("Search fail: $_searchError", style: const TextStyle(color: Colors.red)),
      );
    }
    if (_searchResults.isEmpty) {
      return Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 80, height: 80,
            decoration: BoxDecoration(color: _kNavy.withOpacity(0.06), shape: BoxShape.circle),
            child: Icon(Icons.person_search_rounded, size: 36, color: _kNavy.withOpacity(0.5)),
          ),
          const SizedBox(height: 12),
          Text("Search for people to add", style: TextStyle(color: Colors.grey[500], fontSize: 13)),
        ]),
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.symmetric(vertical: 4),
      itemCount: _searchResults.length,
      separatorBuilder: (_, __) => Divider(height: 1, indent: 78, color: Colors.grey[200]),
      itemBuilder: (context, index) {
        final user = _searchResults[index];
        final selected = _isSelected(user);
        return Material(
          color: selected ? const Color(0xFFFFF0F6) : Colors.white,
          child: ListTile(
            onTap: () => _toggleSelect(user),
            contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            leading: Container(
              padding: const EdgeInsets.all(2),
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(color: Colors.grey[200]!, width: 1),
              ),
              child: CircleAvatar(
                radius: 24,
                backgroundColor: Colors.grey[200],
                backgroundImage: (user['avatar'] != null && user['avatar'].toString().isNotEmpty)
                    ? CachedNetworkImageProvider(user['avatar'].toString())
                    : null,
                child: (user['avatar'] == null || user['avatar'].toString().isEmpty)
                    ? Icon(Icons.person_rounded, color: Colors.grey[500])
                    : null,
              ),
            ),
            title: Text(_displayName(user), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
            subtitle: Text('@${user['username'] ?? ''}', style: TextStyle(fontSize: 12, color: Colors.grey[600])),
            trailing: Icon(
              selected ? Icons.check_circle_rounded : Icons.radio_button_unchecked,
              color: selected ? _kAccent : Colors.grey[400],
            ),
          ),
        );
      },
    );
  }

  Widget _buildBottomBar() {
    return SafeArea(
      child: Container(
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 14),
        decoration: BoxDecoration(
          color: Colors.white,
          boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 8, offset: const Offset(0, -2))],
        ),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _groupNameController,
                decoration: InputDecoration(
                  hintText: "Group name",
                  hintStyle: TextStyle(color: Colors.grey[500], fontSize: 14),
                  filled: true,
                  fillColor: _kBg,
                  contentPadding: const EdgeInsets.symmetric(vertical: 0, horizontal: 14),
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
                ),
              ),
            ),
            const SizedBox(width: 10),
            ElevatedButton(
              onPressed: _isCreating ? null : _createGroup,
              style: ElevatedButton.styleFrom(
                backgroundColor: _kNavy,
                foregroundColor: Colors.white,
                elevation: 0,
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              ),
              child: _isCreating
                  ? const SizedBox(
                      width: 18, height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                    )
                  : const Text("Create", style: TextStyle(fontWeight: FontWeight.w600)),
            ),
          ],
        ),
      ),
    );
  }
}