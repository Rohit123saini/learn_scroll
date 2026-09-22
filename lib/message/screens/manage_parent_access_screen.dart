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
// Entry point: profile screen → "more" sheet → Parent/Guardian Access
// (profile/screens/profile.dart).
//
// 🌐 LANGUAGE FIX — every user-visible string now comes from AppLocalizations
// (ARB keys `parentAccess*`; they existed in app_en/app_hi.arb but this screen
// still used hardcoded Hinglish). Relative times use `timeago` (Hindi is
// registered in LanguageService) and dates use `intl` with the app locale.
// 🔧 FIX — the device-list Future was created INSIDE the StatefulBuilder, so it
// was re-fetched on every rebuild of the sheet (theme/locale change etc.);
// it now lives outside the builder.
// 🔧 FIX — `setState` after `await` without a `mounted` check in `_fetchCodes`.

import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:intl/intl.dart' show DateFormat;
import 'package:timeago/timeago.dart' as timeago;
import '../../l10n/app_localizations.dart';
import '../../services/auth_service.dart';
import '../../utils/api.dart';
import '../../theme_service.dart'; // 🎨 THEME FIX — AppThemeTokens

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
  // 🔧 FIX — placeholder `'https://YOUR_API_HOST/message'` tha, kabhi
  // real network call kaam hi nahi karta tha. `message_api_service.dart`
  // wahi `Api.baseUrl` use karta hai (`../../utils/api.dart`) — same
  // single source, ab ye screen bhi real host pe hit karegi.
  static String get _baseUrl => "${Api.baseUrl}/message";

  List<ParentCodeEntry> _codes = [];
  bool _loading = true;
  bool _loadFailed = false; // rendered as l10n.parentAccessLoadFailed

  @override
  void initState() {
    super.initState();
    _fetchCodes();
  }

  // 🔧 FIX — pehle raw `access_token` seedha SharedPreferences se padha
  // jaata tha (expiry check nahi), same bug jo `main.dart`/
  // `message_api_service.dart` me tha. `AuthService.getValidToken()` use
  // kar rahe hain ab — expired token pe auto-refresh, dead refresh token
  // pe `null` (jisse ye screen graceful "Load failed" dikhayegi login
  // redirect ki jagah — sahi hai kyunki ye screen khud login flow nahi
  // handle karti).
  Future<Map<String, String>> _authHeaders() async {
    final token = await AuthService.getValidToken() ?? '';
    return {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer $token',
    };
  }

  Future<void> _fetchCodes() async {
    setState(() {
      _loading = true;
      _loadFailed = false;
    });
    try {
      final res = await http.get(Uri.parse('$_baseUrl/parent/codes/'), headers: await _authHeaders());
      if (res.statusCode != 200) throw Exception('Load failed');
      final list = jsonDecode(res.body) as List;
      if (!mounted) return;
      setState(() => _codes = list.map((e) => ParentCodeEntry.fromJson(e)).toList());
    } catch (_) {
      if (!mounted) return;
      setState(() => _loadFailed = true);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _generateCode() async {
    // resolved before any `await` so it is safe to use afterwards
    final l10n = AppLocalizations.of(context)!;
    final labelController = TextEditingController();
    final label = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(l10n.parentAccessLabelDialogTitle),
        content: TextField(
          controller: labelController,
          decoration: InputDecoration(hintText: l10n.parentAccessLabelHint),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, labelController.text),
            child: Text(l10n.parentAccessGenerate),
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
          SnackBar(content: Text(l10n.parentAccessGenerateFailed)),
        );
      }
    }
  }

  void _showCodeDialog(String code) {
    final l10n = AppLocalizations.of(context)!;
    showDialog(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(l10n.parentAccessShareCodeTitle),
        content: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(code, style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold, letterSpacing: 3)),
            IconButton(
              icon: const Icon(Icons.copy),
              onPressed: () {
                Clipboard.setData(ClipboardData(text: code));
                ScaffoldMessenger.of(dialogContext).showSnackBar(
                  SnackBar(content: Text(l10n.parentAccessCodeCopied)),
                );
              },
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext), child: Text(l10n.doneCta)),
        ],
      ),
    );
  }

  Future<void> _revokeCode(ParentCodeEntry entry) async {
    final l10n = AppLocalizations.of(context)!;
    final name = entry.label.isEmpty ? l10n.parentAccessUnnamed : entry.label;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(l10n.parentAccessRevokeTitle),
        content: Text(
          entry.activeDevices > 1
              ? l10n.parentAccessRevokeBodyMany(entry.activeDevices, name)
              : l10n.parentAccessRevokeBodyOne(name),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(dialogContext, true), child: Text(l10n.parentAccessRevoke)),
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
          SnackBar(content: Text(l10n.parentAccessRevokeFailed)),
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
    final l10n = AppLocalizations.of(context)!;
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
            SnackBar(content: Text(l10n.parentAccessRevealRateLimited)),
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
          SnackBar(content: Text(l10n.parentAccessRevealFailed)),
        );
      }
    }
  }

  // 🔧 NEW — extend an expired/expiring code's TTL in place. Every
  // device already verified against this code works again immediately —
  // no need to re-share the code with everyone on it.
  Future<void> _renewCode(ParentCodeEntry entry) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final res = await http.post(
        Uri.parse('$_baseUrl/parent/codes/${entry.id}/renew/'),
        headers: await _authHeaders(),
      );
      if (res.statusCode != 200) throw Exception('Renew failed');
      await _fetchCodes();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.parentAccessRenewed)),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.parentAccessRenewFailed)),
        );
      }
    }
  }

  // 🔧 NEW — "Expires in 12 days" / "Expired 3 days ago" style label (localized + plural-aware).
  String _expiryLabel(AppLocalizations l10n, DateTime? expiresAt, bool isExpired) {
    if (expiresAt == null) return '';
    final diff = expiresAt.difference(DateTime.now());
    if (isExpired || diff.isNegative) {
      final agoDays = diff.inDays.abs();
      return agoDays < 1 ? l10n.parentAccessExpiredToday : l10n.parentAccessExpiredDaysAgo(agoDays);
    }
    if (diff.inDays < 1) return l10n.parentAccessExpiresToday;
    if (diff.inDays <= 14) return l10n.parentAccessExpiresInDays(diff.inDays);
    final date = DateFormat('d MMM y', Localizations.localeOf(context).toString()).format(expiresAt.toLocal());
    return l10n.parentAccessExpiresOn(date);
  }

  /// "5 minutes ago" / "5 मिनट पहले" — `timeago` locale follows the app language.
  String _timeAgo(DateTime dt) =>
      timeago.format(dt, locale: Localizations.localeOf(context).languageCode);

  // 🔧 NEW — bottom sheet: list of devices for one code, each with its
  // own "revoke" action that only kills that ONE device.
  void _openDevicesSheet(ParentCodeEntry entry) {
    // 🔧 FIX — created ONCE, outside the builder (was re-created — and re-fetched —
    // on every rebuild of the sheet).
    Future<List<ParentDeviceEntry>> devicesFuture = _fetchDevices(entry.id);

    showModalBottomSheet(
      context: context,
      backgroundColor: Theme.of(context).colorScheme.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (sheetContext) {
        return StatefulBuilder(
          builder: (sheetContext, setSheetState) {
            final cs = Theme.of(sheetContext).colorScheme;
            final l10n = AppLocalizations.of(sheetContext)!;

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
                    entry.label.isEmpty ? l10n.parentAccessDevicesTitle : l10n.parentAccessDevicesTitleNamed(entry.label),
                    style: TextStyle(color: cs.onSurface, fontSize: 17, fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    l10n.parentAccessDevicesHint,
                    style: TextStyle(color: cs.onSurfaceVariant, fontSize: 12),
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
                        return Padding(
                          padding: const EdgeInsets.symmetric(vertical: 24),
                          child: Text(l10n.parentAccessDevicesLoadFailed, style: TextStyle(color: cs.error)),
                        );
                      }
                      final devices = snapshot.data ?? [];
                      if (devices.isEmpty) {
                        return Padding(
                          padding: const EdgeInsets.symmetric(vertical: 24),
                          child: Text(l10n.parentAccessNoDevices, style: TextStyle(color: cs.onSurfaceVariant)),
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
                            final lastActive = d.lastSeenAt == null ? l10n.parentAccessNeverUsed : _timeAgo(d.lastSeenAt!);
                            return Container(
                              padding: const EdgeInsets.all(12),
                              decoration: BoxDecoration(
                                color: AppThemeTokens.of(context).surface2,
                                borderRadius: BorderRadius.circular(10),
                              ),
                              child: Row(
                                children: [
                                  Icon(Icons.phone_android, color: cs.onSurfaceVariant, size: 20),
                                  const SizedBox(width: 12),
                                  Expanded(
                                    child: Column(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        Text(
                                          l10n.parentAccessLastActive(lastActive),
                                          style: TextStyle(color: cs.onSurface, fontSize: 13),
                                        ),
                                        if (d.createdAt != null)
                                          Text(
                                            l10n.parentAccessVerifiedAt(_timeAgo(d.createdAt!)),
                                            style: TextStyle(color: cs.onSurfaceVariant, fontSize: 11),
                                          ),
                                      ],
                                    ),
                                  ),
                                  IconButton(
                                    icon: Icon(Icons.delete_outline, color: cs.error, size: 20),
                                    tooltip: l10n.parentAccessRevokeDeviceTooltip,
                                    onPressed: () async {
                                      final confirmed = await showDialog<bool>(
                                        context: sheetContext,
                                        builder: (dialogContext) => AlertDialog(
                                          title: Text(l10n.parentAccessRevokeDeviceTitle),
                                          content: Text(l10n.parentAccessRevokeDeviceBody),
                                          actions: [
                                            TextButton(
                                              onPressed: () => Navigator.pop(dialogContext, false),
                                              child: Text(l10n.cancel),
                                            ),
                                            TextButton(
                                              onPressed: () => Navigator.pop(dialogContext, true),
                                              child: Text(l10n.parentAccessRevoke),
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
                                            SnackBar(content: Text(l10n.parentAccessRevokeDeviceFailed)),
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
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.parentAccessTitle),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _generateCode,
        icon: const Icon(Icons.add),
        label: Text(l10n.parentAccessNewCode),
      ),
      body: RefreshIndicator(
        onRefresh: _fetchCodes,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                physics: const AlwaysScrollableScrollPhysics(), // pull-to-refresh also on the empty/error state
                padding: const EdgeInsets.all(16),
                children: [
                  Text(
                    l10n.parentAccessIntro,
                    style: TextStyle(color: cs.onSurfaceVariant, fontSize: 13),
                  ),
                  const SizedBox(height: 16),
                  if (_loadFailed)
                    Text(l10n.parentAccessLoadFailed, style: TextStyle(color: cs.error)),
                  if (_codes.isEmpty && !_loadFailed)
                    Padding(
                      padding: const EdgeInsets.only(top: 40),
                      child: Center(
                        child: Text(l10n.parentAccessEmpty, style: TextStyle(color: cs.onSurfaceVariant)),
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
                            color: AppThemeTokens.of(context).surface2,
                            borderRadius: BorderRadius.circular(12),
                          ),
                          child: Row(
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      c.label.isEmpty ? l10n.parentAccessUnnamed : c.label,
                                      style: TextStyle(color: cs.onSurface, fontWeight: FontWeight.w600),
                                    ),
                                    Row(
                                      children: [
                                        Text(
                                          c.revealedCode ?? c.maskedCode,
                                          style: TextStyle(color: cs.onSurfaceVariant, letterSpacing: 2),
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
                                            color: cs.onSurfaceVariant,
                                          ),
                                        ),
                                      ],
                                    ),
                                    const SizedBox(height: 4),
                                    Row(
                                      children: [
                                        Icon(Icons.phone_android, size: 13, color: cs.onSurfaceVariant),
                                        const SizedBox(width: 4),
                                        Text(
                                          l10n.parentAccessDevicesTap(c.activeDevices),
                                          style: TextStyle(color: cs.onSurfaceVariant, fontSize: 11),
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
                                            color: c.isExpired ? cs.error : cs.onSurfaceVariant,
                                          ),
                                          const SizedBox(width: 4),
                                          Text(
                                            _expiryLabel(l10n, c.expiresAt, c.isExpired),
                                            style: TextStyle(
                                              color: c.isExpired ? cs.error : cs.onSurfaceVariant,
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
                                  child: Text(l10n.parentAccessRenew),
                                ),
                              IconButton(
                                icon: Icon(Icons.delete_outline, color: cs.error),
                                tooltip: l10n.parentAccessRevokeAllTooltip,
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