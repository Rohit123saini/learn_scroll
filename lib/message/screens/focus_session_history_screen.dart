// message/screens/focus_session_history_screen.dart
//
// Feature 12 — history of past focus sessions (how long, which rule, ended early?).
// Data comes from GET /message/focus-session/history/ (FocusSession rows are never
// deleted server-side).
//
// Entry point: the history icon in `focus_mode_screen.dart`'s AppBar (already wired).
//
// 🌐 LANGUAGE FIX — all text from AppLocalizations (ARB keys `focusHistory*` /
// `focusRule*`, which existed but were unused); the date label used a hardcoded
// English month list, it now uses `intl` with the app locale.
// 🔧 FIX — `setState` after `await` without a `mounted` check; the error/empty
// states were not scrollable, so pull-to-refresh did nothing there.

import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:intl/intl.dart' show DateFormat;
import '../../l10n/app_localizations.dart';
import '../../services/auth_service.dart';
import '../../utils/api.dart';
import '../../theme_service.dart'; // 🎨 THEME FIX — AppThemeTokens

class FocusSessionHistoryEntry {
  final String id;
  final DateTime startsAt;
  final DateTime endsAt;
  final String exceptionRule; // 'teachers_only' | 'nobody'
  final bool endedEarly;
  final int durationMinutes;

  FocusSessionHistoryEntry({
    required this.id,
    required this.startsAt,
    required this.endsAt,
    required this.exceptionRule,
    required this.endedEarly,
    required this.durationMinutes,
  });

  factory FocusSessionHistoryEntry.fromJson(Map<String, dynamic> json) => FocusSessionHistoryEntry(
        id: json['id'],
        startsAt: DateTime.parse(json['starts_at']).toLocal(),
        endsAt: DateTime.parse(json['ends_at']).toLocal(),
        exceptionRule: json['exception_rule'] ?? 'teachers_only',
        endedEarly: json['ended_early'] == true,
        durationMinutes: (json['duration_minutes'] as num?)?.toInt() ?? 0,
      );
}

class FocusSessionHistoryScreen extends StatefulWidget {
  const FocusSessionHistoryScreen({super.key});

  @override
  State<FocusSessionHistoryScreen> createState() => _FocusSessionHistoryScreenState();
}

class _FocusSessionHistoryScreenState extends State<FocusSessionHistoryScreen> {
  static String get _baseUrl => "${Api.baseUrl}/message";

  List<FocusSessionHistoryEntry> _sessions = [];
  bool _loading = true;
  bool _loadFailed = false; // rendered as l10n.focusHistoryLoadFailed

  @override
  void initState() {
    super.initState();
    _fetchHistory();
  }

  // `AuthService.getValidToken()` refreshes an expired access token, same as everywhere else.
  Future<Map<String, String>> _authHeaders() async {
    final token = await AuthService.getValidToken() ?? '';
    return {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer $token',
    };
  }

  Future<void> _fetchHistory() async {
    setState(() {
      _loading = true;
      _loadFailed = false;
    });
    try {
      final res = await http.get(
        Uri.parse('$_baseUrl/focus-session/history/?limit=50'),
        headers: await _authHeaders(),
      ).timeout(const Duration(seconds: 20));
      if (res.statusCode != 200) throw Exception('Load failed');
      final data = jsonDecode(res.body);
      final list = (data['sessions'] as List? ?? []);
      if (!mounted) return;
      setState(() => _sessions = list.map((e) => FocusSessionHistoryEntry.fromJson(e)).toList());
    } catch (_) {
      if (!mounted) return;
      setState(() => _loadFailed = true);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _durationLabel(AppLocalizations l10n, int minutes) {
    if (minutes < 60) return l10n.focusHistoryMinutes(minutes);
    final hours = minutes ~/ 60;
    final rem = minutes % 60;
    return rem == 0 ? l10n.focusHistoryHours(hours) : l10n.focusHistoryHoursMinutes(hours, rem);
  }

  String _dateLabel(DateTime dt) =>
      DateFormat('d MMM, HH:mm', Localizations.localeOf(context).toString()).format(dt);

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final tokens = AppThemeTokens.of(context);
    final l10n = AppLocalizations.of(context)!;

    // Non-list states are still a ListView so pull-to-refresh works on them.
    Widget centered(Widget child) => ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          children: [Padding(padding: const EdgeInsets.only(top: 80), child: Center(child: child))],
        );

    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.focusHistoryTitle),
      ),
      body: RefreshIndicator(
        onRefresh: _fetchHistory,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _loadFailed
                ? centered(Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(l10n.focusHistoryLoadFailed, style: TextStyle(color: cs.error)),
                      const SizedBox(height: 12),
                      TextButton(onPressed: _fetchHistory, child: Text(l10n.retry)),
                    ],
                  ))
                : _sessions.isEmpty
                    ? centered(Text(l10n.focusHistoryEmpty, style: TextStyle(color: cs.onSurfaceVariant)))
                    : ListView.separated(
                        physics: const AlwaysScrollableScrollPhysics(),
                        padding: const EdgeInsets.all(16),
                        itemCount: _sessions.length,
                        separatorBuilder: (_, __) => const SizedBox(height: 10),
                        itemBuilder: (context, i) {
                          final s = _sessions[i];
                          final duration = _durationLabel(l10n, s.durationMinutes);
                          final rule = s.exceptionRule == 'nobody' ? l10n.focusRuleNobody : l10n.focusRuleTeachersOnly;
                          return Container(
                            padding: const EdgeInsets.all(14),
                            decoration: BoxDecoration(
                              color: tokens.surface2,
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: Row(
                              children: [
                                Icon(
                                  s.endedEarly ? Icons.stop_circle_outlined : Icons.check_circle_outline,
                                  color: s.endedEarly ? tokens.warning : tokens.success,
                                ),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        _dateLabel(s.startsAt),
                                        style: TextStyle(color: cs.onSurface, fontWeight: FontWeight.w600),
                                      ),
                                      const SizedBox(height: 2),
                                      Text(
                                        s.endedEarly
                                            ? l10n.focusHistoryMetaEndedEarly(duration, rule)
                                            : l10n.focusHistoryMeta(duration, rule),
                                        style: TextStyle(color: cs.onSurfaceVariant, fontSize: 12),
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            ),
                          );
                        },
                      ),
      ),
    );
  }
}
