// message/screens/focus_session_history_screen.dart
//
// 🔧 NEW — Feature 12 gap fix: pehle sirf CURRENT active focus session
// dikhta tha (focus_mode_screen.dart) — past sessions (kitni der padha,
// kitni baar use kiya, kitni baar early end kiya) dekhne ka koi tarika
// nahi tha. Backend me data already tha (FocusSession rows kabhi delete
// nahi hoti), sirf ise dikhane wala screen + endpoint missing tha
// (GET /message/focus-session/history/, see views_focus.py).
//
// ⚠️ WIRING NOTE: `focus_mode_screen.dart` upload nahi hui thi, isliye
// yahan directly edit nahi kar saka. Wahan entry point add karne ke
// liye AppBar me ek action button:
//
//   AppBar(
//     title: const Text('Focus Mode'),
//     actions: [
//       IconButton(
//         icon: const Icon(Icons.history),
//         tooltip: 'History',
//         onPressed: () => Navigator.push(context, MaterialPageRoute(
//           builder: (_) => const FocusSessionHistoryScreen())),
//       ),
//     ],
//   )
//
// ⚠️ Same as manage_parent_access_screen.dart: placeholder `_baseUrl` +
// `_authHeaders()` below — plug in the app's real HTTP client/base URL.

import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

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
  // TODO: replace with the app's real base URL / ApiClient.
  static const String _baseUrl = 'https://YOUR_API_HOST/message';

  List<FocusSessionHistoryEntry> _sessions = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _fetchHistory();
  }

  Future<Map<String, String>> _authHeaders() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString('access_token') ?? '';
    return {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer $token',
    };
  }

  Future<void> _fetchHistory() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await http.get(
        Uri.parse('$_baseUrl/focus-session/history/?limit=50'),
        headers: await _authHeaders(),
      );
      if (res.statusCode != 200) throw Exception('Load failed');
      final data = jsonDecode(res.body);
      final list = (data['sessions'] as List? ?? []);
      setState(() => _sessions = list.map((e) => FocusSessionHistoryEntry.fromJson(e)).toList());
    } catch (_) {
      setState(() => _error = 'History load nahi ho paayi.');
    } finally {
      setState(() => _loading = false);
    }
  }

  String _durationLabel(int minutes) {
    if (minutes < 60) return '$minutes min';
    final hours = minutes ~/ 60;
    final rem = minutes % 60;
    return rem == 0 ? '${hours}h' : '${hours}h ${rem}m';
  }

  String _dateLabel(DateTime dt) {
    final months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return '${dt.day} ${months[dt.month - 1]}, ${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F0F11),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0F0F11),
        title: const Text('Focus Mode — History'),
      ),
      body: RefreshIndicator(
        onRefresh: _fetchHistory,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? Center(child: Text(_error!, style: const TextStyle(color: Colors.redAccent)))
                : _sessions.isEmpty
                    ? ListView(
                        children: const [
                          Padding(
                            padding: EdgeInsets.only(top: 80),
                            child: Center(
                              child: Text(
                                'Abhi tak koi focus session nahi hui.',
                                style: TextStyle(color: Colors.white38),
                              ),
                            ),
                          ),
                        ],
                      )
                    : ListView.separated(
                        padding: const EdgeInsets.all(16),
                        itemCount: _sessions.length,
                        separatorBuilder: (_, __) => const SizedBox(height: 10),
                        itemBuilder: (context, i) {
                          final s = _sessions[i];
                          return Container(
                            padding: const EdgeInsets.all(14),
                            decoration: BoxDecoration(
                              color: Colors.white.withOpacity(0.05),
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: Row(
                              children: [
                                Icon(
                                  s.endedEarly ? Icons.stop_circle_outlined : Icons.check_circle_outline,
                                  color: s.endedEarly ? Colors.orangeAccent : Colors.greenAccent,
                                ),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        _dateLabel(s.startsAt),
                                        style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600),
                                      ),
                                      const SizedBox(height: 2),
                                      Text(
                                        '${_durationLabel(s.durationMinutes)} · '
                                        '${s.exceptionRule == 'nobody' ? 'Full silence' : 'Teachers only'}'
                                        '${s.endedEarly ? ' · ended early' : ''}',
                                        style: const TextStyle(color: Colors.white38, fontSize: 12),
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