// message/services/parent_service.dart
//
// Feature 8 — Parent/Guardian Mode, Flutter side (the PARENT's device).
//
// Networking: shares `Api.baseUrl` (../../utils/api.dart) with the rest of the app.
//
// Storage note: parent mode intentionally uses ITS OWN SharedPreferences
// keys (`parent_token`, `parent_student_name`, `parent_label`) — never
// `access_token`. A parent's device and a student's device are meant to
// be able to hold BOTH a normal student login AND a parent session at
// the same time without either overwriting the other (e.g. a parent who
// is also a student on the platform, viewing a sibling's progress).
//
// 🌐 LANGUAGE FIX — this service used to throw exceptions carrying hardcoded
// Hinglish sentences ("Invalid ya expired code…"), and it has no BuildContext, so
// they could never follow the app language. It now throws a typed
// [ParentModeError]; the UI turns it into text with
// `error.localized(AppLocalizations.of(context)!)` (ARB keys `parentErr*`).

import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../../l10n/app_localizations.dart';
import '../../utils/api.dart';

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

/// Everything that can go wrong in parent mode. Turned into user-facing text by the UI.
enum ParentModeError {
  invalidCode,
  tooManyAttempts,
  generic,
  sessionExpired,
  accessRevoked,
  dashboardLoadFailed,
}

class ParentModeException implements Exception {
  final ParentModeError error;
  ParentModeException(this.error);

  /// The saved session is gone (missing token or revoked) — retrying is pointless,
  /// the parent has to enter a fresh code.
  bool get needsNewCode =>
      error == ParentModeError.sessionExpired || error == ParentModeError.accessRevoked;

  String localized(AppLocalizations l10n) {
    switch (error) {
      case ParentModeError.invalidCode:
        return l10n.parentErrInvalidCode;
      case ParentModeError.tooManyAttempts:
        return l10n.parentErrTooManyAttempts;
      case ParentModeError.sessionExpired:
        return l10n.parentErrSessionExpired;
      case ParentModeError.accessRevoked:
        return l10n.parentErrAccessRevoked;
      case ParentModeError.dashboardLoadFailed:
        return l10n.parentErrDashboardLoad;
      case ParentModeError.generic:
        return l10n.parentErrGeneric;
    }
  }

  @override
  String toString() => 'ParentModeException(${error.name})';
}

class ParentService {
  ParentService._();
  static final ParentService instance = ParentService._();

  static String get _baseUrl => "${Api.baseUrl}/message";
  static const _timeout = Duration(seconds: 20);

  static const _kParentToken = 'parent_token';
  static const _kParentStudentName = 'parent_student_name';
  static const _kParentLabel = 'parent_label';

  /// Redeem a code the student shared. Stores the returned token locally
  /// on success (separate from the student's own `access_token`).
  Future<void> verifyCode(String code) async {
    final http.Response res;
    try {
      res = await http
          .post(
            Uri.parse('$_baseUrl/parent/verify/'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'code': code.trim().toUpperCase()}),
          )
          .timeout(_timeout);
    } on Exception {
      // no internet / timeout / DNS — all "try again" for the parent
      throw ParentModeException(ParentModeError.generic);
    }

    if (res.statusCode == 404) throw ParentModeException(ParentModeError.invalidCode);
    if (res.statusCode == 429) throw ParentModeException(ParentModeError.tooManyAttempts);
    if (res.statusCode != 200) throw ParentModeException(ParentModeError.generic);

    final Map<String, dynamic> data;
    try {
      data = jsonDecode(res.body) as Map<String, dynamic>;
    } on Exception {
      throw ParentModeException(ParentModeError.generic);
    }
    final token = data['parent_token'];
    if (token is! String || token.isEmpty) throw ParentModeException(ParentModeError.generic);

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kParentToken, token);
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
      throw ParentModeException(ParentModeError.sessionExpired);
    }

    final http.Response res;
    try {
      res = await http
          .get(
            Uri.parse('$_baseUrl/parent/dashboard/'),
            headers: {'X-Parent-Token': token},
          )
          .timeout(_timeout);
    } on Exception {
      throw ParentModeException(ParentModeError.dashboardLoadFailed);
    }

    if (res.statusCode == 403 || res.statusCode == 401) {
      await signOut();
      throw ParentModeException(ParentModeError.accessRevoked);
    }
    if (res.statusCode != 200) {
      throw ParentModeException(ParentModeError.dashboardLoadFailed);
    }

    try {
      return ParentDashboard.fromJson(jsonDecode(res.body));
    } on Exception {
      throw ParentModeException(ParentModeError.dashboardLoadFailed);
    }
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
