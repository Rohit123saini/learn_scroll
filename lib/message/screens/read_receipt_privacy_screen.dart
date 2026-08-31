// message/screens/read_receipt_privacy_screen.dart
//
// 🔥 NAYA (Phase 4, §1 #10, §2.3 — FRONTEND_INTEGRATION_ARCHITECTURE.md) —
// Read-Receipt Privacy toggle. Mutual switch: off karne se tumhara
// `read_at` dusro ko nahi dikhega, AUR dusro ka `read_at` tumhe bhi nahi
// dikhega (backend §7.11 wording).
//
// `GET/PATCH /message/presence/read-receipts/` already
// `message_api_service.dart` me hai (`getReadReceiptSetting` /
// `setReadReceiptSetting`) — is screen ko sirf UI dena hai.
//
// Entry point abhi standalone hai (asli "Settings" screen upload nahi
// hui — frontend doc §2.3/§8 flag karta hai). Jahan se bhi link karna ho
// (profile/privacy settings, group-profile, chat_screen 3-dot menu),
// bas:
//   Navigator.push(context, MaterialPageRoute(
//     builder: (_) => const ReadReceiptPrivacyScreen(),
//   ));

import 'package:flutter/material.dart';

import '../services/message_api_service.dart';

const Color _kNavy = Color(0xFF030F27);
const Color _kAccent = Color(0xFF3D7EFF);

class ReadReceiptPrivacyScreen extends StatefulWidget {
  const ReadReceiptPrivacyScreen({super.key});

  @override
  State<ReadReceiptPrivacyScreen> createState() => _ReadReceiptPrivacyScreenState();
}

class _ReadReceiptPrivacyScreenState extends State<ReadReceiptPrivacyScreen> {
  bool? _showReadReceipts; // null = abhi load ho raha hai
  bool _saving = false;
  String? _loadError;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loadError = null);
    try {
      final value = await MessageApiService.getReadReceiptSetting();
      if (mounted) setState(() => _showReadReceipts = value);
    } catch (_) {
      if (mounted) setState(() => _loadError = "Couldn't load your current setting.");
    }
  }

  Future<void> _onToggle(bool value) async {
    final previous = _showReadReceipts;
    setState(() {
      _showReadReceipts = value; // optimistic
      _saving = true;
    });
    try {
      final confirmed = await MessageApiService.setReadReceiptSetting(value);
      if (!mounted) return;
      setState(() {
        _showReadReceipts = confirmed;
        _saving = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _showReadReceipts = previous; // revert on failure
        _saving = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Couldn't update this setting. Try again.")),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: _kNavy,
        elevation: 0,
        title: const Text("Read Receipts", style: TextStyle(color: Colors.white, fontSize: 16.5, fontWeight: FontWeight.w600)),
        iconTheme: const IconThemeData(color: Colors.white),
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loadError != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(_loadError!, textAlign: TextAlign.center, style: TextStyle(color: Colors.grey[600], fontSize: 13)),
            const SizedBox(height: 12),
            TextButton(onPressed: _load, child: const Text("Retry")),
          ]),
        ),
      );
    }
    if (_showReadReceipts == null) {
      return const Center(child: CircularProgressIndicator(color: _kAccent));
    }

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: 8),
      children: [
        Container(
          margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          padding: const EdgeInsets.symmetric(horizontal: 4),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: const [
                    Text("Show read receipts", style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                    SizedBox(height: 3),
                    Text(
                      "Let people you message see when you've read their messages.",
                      style: TextStyle(fontSize: 12.5, color: Colors.black54),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              _saving
                  ? const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2.4, color: _kAccent))
                  : Switch(
                      value: _showReadReceipts!,
                      activeColor: _kAccent,
                      onChanged: _onToggle,
                    ),
            ],
          ),
        ),
        const Divider(height: 24),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: const Color(0xFFF3F5FA),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.info_outline, size: 18, color: Colors.grey[700]),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    "This is a mutual setting. Turning it off means your read receipts "
                    "won't be shown to others, AND you won't be able to see others' read "
                    "receipts either.",
                    style: TextStyle(fontSize: 12.5, color: Colors.grey[800], height: 1.4),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Text(
            "Note: this doesn't affect typing indicators or delivery status — only "
            "the blue \"seen\" checkmarks / read timestamps.",
            style: TextStyle(fontSize: 11.5, color: Colors.grey[500]),
          ),
        ),
      ],
    );
  }
}