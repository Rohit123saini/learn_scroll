// message/screens/manage_parent_access_screen.dart
//
// STUDENT side of Feature 8 — generate a code to hand to a parent/
// guardian, see existing codes, revoke one. Uses the student's OWN
// normal `access_token` (same auth as every other screen in the app —
// NOT the parent_service.dart flow, which is for the parent's device).
//
// 🔧 GAP FIX — per-device revoke. Previously the ONLY way to cut a
// parent's access was revoking the WHOLE code, which killed every
// device that had ever verified it (one code can be verified by
// multiple phones — e.g. Mom's + Dad's). Tapping a code now opens a
// bottom sheet listing each individual device (`ParentToken`) so a
// single lost/stolen phone can be revoked on its own, leaving the
// code and every other device on it untouched.
//
// ⚠️ WIRING NOTE: same as parent_service.dart — plug in the app's real
// HTTP client / base URL / auth-header helper instead of the
// placeholders below. Add an entry point from wherever account/privacy
// settings live, e.g.:
//
//   ListTile(
//     leading: const Icon(Icons.family_restroom),
//     title: const Text('Parent/Guardian Access'),
//     onTap: () => Navigator.push(context, MaterialPageRoute(
//       builder: (_) => const ManageParentAccessScreen())),
//   )

import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class ParentCodeEntry {
  final String id;
  final String label;
  // 🔧 GAP FIX — backend no longer sends plaintext `code` on the list
  // endpoint (reveal-once pattern). This holds the masked form
  // ("••••9QRT") by default; `revealedCode` is filled in-memory only
  // after the student explicitly taps "Reveal", and is never persisted.
  final String maskedCode;
  String? revealedCode;
  final int activeDevices;
  final DateTime? expiresAt;
  final bool isExpired;
  ParentCodeEntry({
    required this.id,
    required this.label,
    required this.maskedCode,
    this.revealedCode,
    this.activeDevices = 0,
    this.expiresAt,
    this.isExpired = false,
  });

  factory ParentCodeEntry.fromJson(Map<String, dynamic> json) => ParentCodeEntry(
        id: json['id'],
        label: json['label'] ?? '',
        maskedCode: json['masked_code'] ?? '',
        activeDevices: (json['active_devices'] as num?)?.toInt() ?? 0,
        expiresAt: json['expires_at'] != null ? DateTime.tryParse(json['expires_at']) : null,
        isExpired: json['is_expired'] == true,
      );
}

// 🔧 NEW — one row in the per-code device list.
class ParentDeviceEntry {
  final String id;
  final DateTime? createdAt;
  final DateTime? lastSeenAt;
  ParentDeviceEntry({required this.id, this.createdAt, this.lastSeenAt});

  factory ParentDeviceEntry.fromJson(Map<String, dynamic> json) => ParentDeviceEntry(
        id: json['id'],
        createdAt: json['created_at'] != null ? DateTime.tryParse(json['created_at']) : null,
        lastSeenAt: json['last_seen_at'] != null ? DateTime.tryParse(json['last_seen_at']) : null,
      );
}

class ManageParentAccessScreen extends StatefulWidget {
  const ManageParentAccessScreen({super.key});

  @override
  State<ManageParentAccessScreen> createState() => _ManageParentAccessScreenState();
}

class _ManageParentAccessScreenState extends State<ManageParentAccessScreen> {
  // TODO: replace with the app's real base URL / ApiClient.
  static const String _baseUrl = 'https://YOUR_API_HOST/message';

  List<ParentCodeEntry> _codes = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _fetchCodes();
  }

  Future<Map<String, String>> _authHeaders() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString('access_token') ?? '';
    return {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer $token', // adjust prefix if the app uses Token/JWT differently
    };
  }

  Future<void> _fetchCodes() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await http.get(Uri.parse('$_baseUrl/parent/codes/'), headers: await _authHeaders());
      if (res.statusCode != 200) throw Exception('Load failed');
      final list = jsonDecode(res.body) as List;
      setState(() => _codes = list.map((e) => ParentCodeEntry.fromJson(e)).toList());
    } catch (_) {
      setState(() => _error = 'Codes load nahi ho paaye.');
    } finally {
      setState(() => _loading = false);
    }
  }

  Future<void> _generateCode() async {
    final labelController = TextEditingController();
    final label = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Kiske liye hai ye code?'),
        content: TextField(
          controller: labelController,
          decoration: const InputDecoration(hintText: 'e.g. Mom, Papa'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, labelController.text),
            child: const Text('Generate'),
          ),
        ],
      ),
    );
    if (label == null) return;

    try {
      final res = await http.post(
        Uri.parse('$_baseUrl/parent/codes/'),
        headers: await _authHeaders(),
        body: jsonEncode({'label': label.trim()}),
      );
      if (res.statusCode != 201) throw Exception('Generate failed');
      await _fetchCodes();
      final data = jsonDecode(res.body);
      if (mounted) _showCodeDialog(data['code']);
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Code generate nahi ho paaya. Dobara try karo.')),
        );
      }
    }
  }

  void _showCodeDialog(String code) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Ye code parent ke saath share karo'),
        content: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(code, style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold, letterSpacing: 3)),
            IconButton(
              icon: const Icon(Icons.copy),
              onPressed: () {
                Clipboard.setData(ClipboardData(text: code));
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Copied')),
                );
              },
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Done')),
        ],
      ),
    );
  }

  Future<void> _revokeCode(ParentCodeEntry entry) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Access revoke karein?'),
        content: Text(
          entry.activeDevices > 1
              ? '"${entry.label.isEmpty ? 'Ye' : entry.label}" se linked SAARE '
                  '${entry.activeDevices} devices ka access turant band ho jaayega.'
              : '"${entry.label.isEmpty ? 'Ye' : entry.label}" ka access turant band ho jaayega.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Revoke')),
        ],
      ),
    );
    if (confirmed != true) return;

    try {
      final res = await http.delete(
        Uri.parse('$_baseUrl/parent/codes/'),
        headers: await _authHeaders(),
        body: jsonEncode({'id': entry.id}),
      );
      if (res.statusCode != 200) throw Exception('Revoke failed');
      await _fetchCodes();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Revoke nahi ho paaya. Dobara try karo.')),
        );
      }
    }
  }

  // 🔧 NEW — fetch the individual devices verified against one code.
  Future<List<ParentDeviceEntry>> _fetchDevices(String codeId) async {
    final res = await http.get(
      Uri.parse('$_baseUrl/parent/codes/$codeId/tokens/'),
      headers: await _authHeaders(),
    );
    if (res.statusCode != 200) throw Exception('Devices load failed');
    final list = jsonDecode(res.body) as List;
    return list.map((e) => ParentDeviceEntry.fromJson(e)).toList();
  }

  // 🔧 NEW — revoke exactly one device, code + other devices untouched.
  Future<void> _revokeDevice(String codeId, String tokenId) async {
    final res = await http.delete(
      Uri.parse('$_baseUrl/parent/codes/$codeId/tokens/$tokenId/'),
      headers: await _authHeaders(),
    );
    if (res.statusCode != 200) throw Exception('Device revoke failed');
  }

  // 🔧 NEW — reveal-once: fetch the full plaintext code on demand
  // (throttled 10/hour server-side) and auto re-mask it after a short
  // window so it doesn't stay exposed on screen indefinitely.
  Future<void> _revealCode(ParentCodeEntry entry) async {
    if (entry.revealedCode != null) {
      // Already revealed — tapping again just re-masks it, no API call.
      setState(() => entry.revealedCode = null);
      return;
    }
    try {
      final res = await http.post(
        Uri.parse('$_baseUrl/parent/codes/${entry.id}/reveal/'),
        headers: await _authHeaders(),
      );
      if (res.statusCode == 429) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Bahut baar reveal kiya — thodi der baad try karo.')),
          );
        }
        return;
      }
      if (res.statusCode != 200) throw Exception('Reveal failed');
      final data = jsonDecode(res.body);
      setState(() => entry.revealedCode = data['code']);
      // Auto re-mask after 20s so it doesn't stay exposed if the
      // student walks away with the screen open.
      Future.delayed(const Duration(seconds: 20), () {
        if (mounted) setState(() => entry.revealedCode = null);
      });
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Code reveal nahi ho paaya.')),
        );
      }
    }
  }

  // 🔧 NEW — extend an expired/expiring code's TTL in place. Every
  // device already verified against this code works again immediately —
  // no need to re-share the code with everyone on it.
  Future<void> _renewCode(ParentCodeEntry entry) async {
    try {
      final res = await http.post(
        Uri.parse('$_baseUrl/parent/codes/${entry.id}/renew/'),
        headers: await _authHeaders(),
      );
      if (res.statusCode != 200) throw Exception('Renew failed');
      await _fetchCodes();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Access renew ho gaya.')),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Renew nahi ho paaya. Dobara try karo.')),
        );
      }
    }
  }

  // 🔧 NEW — "Expires in 12 days" / "Expired 3 din pehle" style label.
  String _expiryLabel(DateTime? expiresAt, bool isExpired) {
    if (expiresAt == null) return '';
    final diff = expiresAt.difference(DateTime.now());
    if (isExpired || diff.isNegative) {
      final agoDays = diff.inDays.abs();
      return 'Expired ${agoDays < 1 ? 'aaj' : '$agoDays din pehle'}';
    }
    if (diff.inDays < 1) return 'Aaj expire ho raha hai';
    if (diff.inDays <= 14) return '${diff.inDays} din me expire hoga';
    return 'Expires ${expiresAt.day}/${expiresAt.month}/${expiresAt.year}';
  }

  String _relativeTime(DateTime? dt) {
    if (dt == null) return 'Kabhi use nahi hua';
    final diff = DateTime.now().difference(dt);
    if (diff.inMinutes < 1) return 'Abhi';
    if (diff.inMinutes < 60) return '${diff.inMinutes} min pehle';
    if (diff.inHours < 24) return '${diff.inHours} ghante pehle';
    return '${diff.inDays} din pehle';
  }

  // 🔧 NEW — bottom sheet: list of devices for one code, each with its
  // own "revoke" action that only kills that ONE device.
  void _openDevicesSheet(ParentCodeEntry entry) {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF17171A),
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (sheetContext) {
        return StatefulBuilder(
          builder: (sheetContext, setSheetState) {
            Future<List<ParentDeviceEntry>>? devicesFuture;
            devicesFuture ??= _fetchDevices(entry.id);

            return Padding(
              padding: EdgeInsets.only(
                left: 20, right: 20, top: 20,
                bottom: MediaQuery.of(sheetContext).viewInsets.bottom + 20,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    entry.label.isEmpty ? 'Devices' : '${entry.label} — devices',
                    style: const TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    'Sirf ek device revoke karne se baaki devices ka access chalu rehta hai.',
                    style: TextStyle(color: Colors.white38, fontSize: 12),
                  ),
                  const SizedBox(height: 16),
                  FutureBuilder<List<ParentDeviceEntry>>(
                    future: devicesFuture,
                    builder: (context, snapshot) {
                      if (snapshot.connectionState == ConnectionState.waiting) {
                        return const Padding(
                          padding: EdgeInsets.symmetric(vertical: 24),
                          child: Center(child: CircularProgressIndicator()),
                        );
                      }
                      if (snapshot.hasError) {
                        return const Padding(
                          padding: EdgeInsets.symmetric(vertical: 24),
                          child: Text('Devices load nahi ho paaye.', style: TextStyle(color: Colors.redAccent)),
                        );
                      }
                      final devices = snapshot.data ?? [];
                      if (devices.isEmpty) {
                        return const Padding(
                          padding: EdgeInsets.symmetric(vertical: 24),
                          child: Text('Abhi koi device is code se verify nahi hua.', style: TextStyle(color: Colors.white38)),
                        );
                      }
                      return ConstrainedBox(
                        constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.5),
                        child: ListView.separated(
                          shrinkWrap: true,
                          itemCount: devices.length,
                          separatorBuilder: (_, __) => const SizedBox(height: 8),
                          itemBuilder: (context, i) {
                            final d = devices[i];
                            return Container(
                              padding: const EdgeInsets.all(12),
                              decoration: BoxDecoration(
                                color: Colors.white.withOpacity(0.05),
                                borderRadius: BorderRadius.circular(10),
                              ),
                              child: Row(
                                children: [
                                  const Icon(Icons.phone_android, color: Colors.white38, size: 20),
                                  const SizedBox(width: 12),
                                  Expanded(
                                    child: Column(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        Text(
                                          'Last active: ${_relativeTime(d.lastSeenAt)}',
                                          style: const TextStyle(color: Colors.white, fontSize: 13),
                                        ),
                                        if (d.createdAt != null)
                                          Text(
                                            'Verified: ${_relativeTime(d.createdAt)}',
                                            style: const TextStyle(color: Colors.white38, fontSize: 11),
                                          ),
                                      ],
                                    ),
                                  ),
                                  IconButton(
                                    icon: const Icon(Icons.delete_outline, color: Colors.redAccent, size: 20),
                                    tooltip: 'Sirf ye device revoke karo',
                                    onPressed: () async {
                                      final confirmed = await showDialog<bool>(
                                        context: sheetContext,
                                        builder: (dialogContext) => AlertDialog(
                                          title: const Text('Ye device revoke karein?'),
                                          content: const Text('Sirf ye ek device disconnect hoga, baaki chalte rahenge.'),
                                          actions: [
                                            TextButton(
                                              onPressed: () => Navigator.pop(dialogContext, false),
                                              child: const Text('Cancel'),
                                            ),
                                            TextButton(
                                              onPressed: () => Navigator.pop(dialogContext, true),
                                              child: const Text('Revoke'),
                                            ),
                                          ],
                                        ),
                                      );
                                      if (confirmed != true) return;
                                      try {
                                        await _revokeDevice(entry.id, d.id);
                                        setSheetState(() {
                                          devicesFuture = _fetchDevices(entry.id);
                                        });
                                        if (mounted) await _fetchCodes(); // refresh device counts on the list screen
                                      } catch (_) {
                                        if (sheetContext.mounted) {
                                          ScaffoldMessenger.of(sheetContext).showSnackBar(
                                            const SnackBar(content: Text('Device revoke nahi ho paaya.')),
                                          );
                                        }
                                      }
                                    },
                                  ),
                                ],
                              ),
                            );
                          },
                        ),
                      );
                    },
                  ),
                ],
              ),
            );
          },
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F0F11),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0F0F11),
        title: const Text('Parent/Guardian Access'),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _generateCode,
        icon: const Icon(Icons.add),
        label: const Text('New Code'),
      ),
      body: RefreshIndicator(
        onRefresh: _fetchCodes,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  const Text(
                    "Parent/guardian ko yahan se code do — unhe sirf attendance "
                    "aur assignment status dikhega, koi chat message nahi. Code pe tap "
                    "karke uske individual devices manage kar sakte ho.",
                    style: TextStyle(color: Colors.white70, fontSize: 13),
                  ),
                  const SizedBox(height: 16),
                  if (_error != null)
                    Text(_error!, style: const TextStyle(color: Colors.redAccent)),
                  if (_codes.isEmpty && _error == null)
                    const Padding(
                      padding: EdgeInsets.only(top: 40),
                      child: Center(
                        child: Text('Abhi koi active code nahi hai.', style: TextStyle(color: Colors.white38)),
                      ),
                    ),
                  ..._codes.map((c) => InkWell(
                        // 🔧 NEW — tap the card body to open the per-device
                        // list; the trailing delete icon still revokes the
                        // WHOLE code (kept for the "kill everything now" case).
                        onTap: () => _openDevicesSheet(c),
                        borderRadius: BorderRadius.circular(12),
                        child: Container(
                          margin: const EdgeInsets.only(bottom: 12),
                          padding: const EdgeInsets.all(14),
                          decoration: BoxDecoration(
                            color: Colors.white.withOpacity(0.05),
                            borderRadius: BorderRadius.circular(12),
                          ),
                          child: Row(
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      c.label.isEmpty ? 'Unnamed' : c.label,
                                      style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600),
                                    ),
                                    Row(
                                      children: [
                                        Text(
                                          c.revealedCode ?? c.maskedCode,
                                          style: const TextStyle(color: Colors.white54, letterSpacing: 2),
                                        ),
                                        const SizedBox(width: 6),
                                        // 🔧 GAP FIX — reveal-once: full code sirf tap pe milta
                                        // hai, throttled server-side (10/hour). List load pe
                                        // kabhi apne aap plaintext nahi dikhta.
                                        InkWell(
                                          onTap: () => _revealCode(c),
                                          child: Icon(
                                            c.revealedCode != null ? Icons.visibility_off : Icons.visibility,
                                            size: 14,
                                            color: Colors.white38,
                                          ),
                                        ),
                                      ],
                                    ),
                                    const SizedBox(height: 4),
                                    Row(
                                      children: [
                                        Icon(Icons.phone_android, size: 13, color: Colors.white38),
                                        const SizedBox(width: 4),
                                        Text(
                                          c.activeDevices == 1
                                              ? '1 device · tap to manage'
                                              : '${c.activeDevices} devices · tap to manage',
                                          style: const TextStyle(color: Colors.white38, fontSize: 11),
                                        ),
                                      ],
                                    ),
                                    // 🔧 NEW — expiry status (backend TTL gap fix).
                                    if (c.expiresAt != null) ...[
                                      const SizedBox(height: 4),
                                      Row(
                                        children: [
                                          Icon(
                                            c.isExpired ? Icons.error_outline : Icons.schedule,
                                            size: 13,
                                            color: c.isExpired ? Colors.redAccent : Colors.white38,
                                          ),
                                          const SizedBox(width: 4),
                                          Text(
                                            _expiryLabel(c.expiresAt, c.isExpired),
                                            style: TextStyle(
                                              color: c.isExpired ? Colors.redAccent : Colors.white38,
                                              fontSize: 11,
                                            ),
                                          ),
                                        ],
                                      ),
                                    ],
                                  ],
                                ),
                              ),
                              // 🔧 NEW — expired/expiring code: offer Renew
                              // instead of forcing a full re-share.
                              if (c.isExpired)
                                TextButton(
                                  onPressed: () => _renewCode(c),
                                  child: const Text('Renew'),
                                ),
                              IconButton(
                                icon: const Icon(Icons.delete_outline, color: Colors.redAccent),
                                tooltip: 'Poora code revoke karo (saare devices)',
                                onPressed: () => _revokeCode(c),
                              ),
                            ],
                          ),
                        ),
                      )),
                ],
              ),
      ),
    );
  }
}