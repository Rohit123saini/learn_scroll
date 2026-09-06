// message/services/parent_service.dart
//
// Feature 8 — Parent/Guardian Mode, Flutter side.
//
// ⚠️ WIRING NOTE: this file wasn't given the app's actual networking
// setup (no api_client.dart / base-URL constant was in the uploaded
// files), so `_baseUrl` below is a placeholder — swap it for whatever
// this app already uses to hit `/message/...` from other services
// (push_notification_service.dart, call_manager.dart, etc.), so this
// stays consistent with the rest of the app instead of a second,
// diverging HTTP setup.
//
// Storage note: parent mode intentionally uses ITS OWN SharedPreferences
// keys (`parent_token`, `parent_student_name`, `parent_label`) — never
// `access_token`. A parent's device and a student's device are meant to
// be able to hold BOTH a normal student login AND a parent session at
// the same time without either overwriting the other (e.g. a parent who
// is also a student on the platform, viewing a sibling's progress).

import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class ParentAttendanceStats {
  final int currentStreak;
  final int longestStreak;
  final int totalClassesAttended;
  final String? lastAttended;

  ParentAttendanceStats({
    required this.currentStreak,
    required this.longestStreak,
    required this.totalClassesAttended,
    required this.lastAttended,
  });

  factory ParentAttendanceStats.fromJson(Map<String, dynamic> json) {
    return ParentAttendanceStats(
      currentStreak: json['current_streak'] ?? 0,
      longestStreak: json['longest_streak'] ?? 0,
      totalClassesAttended: json['total_classes_attended'] ?? 0,
      lastAttended: json['last_attended'],
    );
  }
}

class ParentAssignmentStats {
  final int pending;
  final int submitted;
  final int total;

  ParentAssignmentStats({
    required this.pending,
    required this.submitted,
    required this.total,
  });

  factory ParentAssignmentStats.fromJson(Map<String, dynamic> json) {
    return ParentAssignmentStats(
      pending: json['pending'] ?? 0,
      submitted: json['submitted'] ?? 0,
      total: json['total'] ?? 0,
    );
  }
}

class ParentClassroomSummary {
  final String groupName;
  final ParentAttendanceStats attendance;
  final ParentAssignmentStats assignments;

  ParentClassroomSummary({
    required this.groupName,
    required this.attendance,
    required this.assignments,
  });

  factory ParentClassroomSummary.fromJson(Map<String, dynamic> json) {
    return ParentClassroomSummary(
      groupName: json['group_name'] ?? '',
      attendance: ParentAttendanceStats.fromJson(json['attendance'] ?? {}),
      assignments: ParentAssignmentStats.fromJson(json['assignments'] ?? {}),
    );
  }
}

class ParentDashboard {
  final String studentName;
  final List<ParentClassroomSummary> classrooms;

  ParentDashboard({required this.studentName, required this.classrooms});

  factory ParentDashboard.fromJson(Map<String, dynamic> json) {
    return ParentDashboard(
      studentName: json['student_name'] ?? '',
      classrooms: (json['classrooms'] as List? ?? [])
          .map((c) => ParentClassroomSummary.fromJson(c))
          .toList(),
    );
  }
}

class ParentModeException implements Exception {
  final String message;
  ParentModeException(this.message);
  @override
  String toString() => message;
}

class ParentService {
  ParentService._();
  static final ParentService instance = ParentService._();

  // TODO: replace with the app's real base URL / ApiClient.
  static const String _baseUrl = 'https://YOUR_API_HOST/message';

  static const _kParentToken = 'parent_token';
  static const _kParentStudentName = 'parent_student_name';
  static const _kParentLabel = 'parent_label';

  /// Redeem a code the student shared. Stores the returned token locally
  /// on success (separate from the student's own `access_token`).
  Future<void> verifyCode(String code) async {
    final res = await http.post(
      Uri.parse('$_baseUrl/parent/verify/'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'code': code.trim().toUpperCase()}),
    );

    if (res.statusCode == 404) {
      throw ParentModeException('Invalid ya expired code. Dobara check karo.');
    }
    if (res.statusCode == 429) {
      throw ParentModeException('Bahut attempts ho gaye — thodi der baad try karo.');
    }
    if (res.statusCode != 200) {
      throw ParentModeException('Kuch galat ho gaya. Dobara try karo.');
    }

    final data = jsonDecode(res.body) as Map<String, dynamic>;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kParentToken, data['parent_token']);
    await prefs.setString(_kParentStudentName, data['student_name'] ?? '');
    await prefs.setString(_kParentLabel, data['label'] ?? '');
  }

  Future<bool> hasActiveSession() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString(_kParentToken);
    return token != null && token.isNotEmpty;
  }

  Future<ParentDashboard> fetchDashboard() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString(_kParentToken);
    if (token == null || token.isEmpty) {
      throw ParentModeException('Session expire ho gaya. Code dobara daalo.');
    }

    final res = await http.get(
      Uri.parse('$_baseUrl/parent/dashboard/'),
      headers: {'X-Parent-Token': token},
    );

    if (res.statusCode == 403 || res.statusCode == 401) {
      await signOut();
      throw ParentModeException('Access revoke ho gaya hai. Student se naya code lo.');
    }
    if (res.statusCode != 200) {
      throw ParentModeException('Dashboard load nahi ho paaya. Dobara try karo.');
    }

    return ParentDashboard.fromJson(jsonDecode(res.body));
  }

  Future<String?> cachedStudentName() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_kParentStudentName);
  }

  Future<void> signOut() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_kParentToken);
    await prefs.remove(_kParentStudentName);
    await prefs.remove(_kParentLabel);
  }
}