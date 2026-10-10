// lib/services/study_profile_service.dart
//
// Student ka study profile (exam target, class, focus subjects, exam date) +
// Exam Mode. Backend: `GET/PATCH /profile/preferences/me/` — wahi endpoint jo
// theme/language ke liye hai (`UserPreferencesApi`), bas naye fields.
//
// Alag service kyun: `UserPreferencesApi.update` fire-and-forget aur silent hai
// (theme/language ke liye theek), jabki yahan user ko validation message dikhana
// padta hai ("exam date past ki hai", "max 10 subjects") aur save ka natija chahiye.
//
// Feed par asar: server profile badalte hi feed ka candidate cache invalidate
// karta hai — client ko agle feed refresh (pull-to-refresh) par naya feed milta hai.

import 'dart:convert';

import 'package:http/http.dart' as http;

import '../utils/api.dart';
import 'auth_service.dart';

class StudyProfile {
  final String examTarget; // '' | jee | neet | board | upsc | other
  final String classLevel; // '' | 6..12 | dropper | graduate | other
  final List<String> focusSubjects;
  final DateTime? examDate;
  final bool examMode; // user ne switch on kiya
  final bool examModeActive; // on AND exam date nikli nahi (server decide karta hai)

  const StudyProfile({
    this.examTarget = '',
    this.classLevel = '',
    this.focusSubjects = const [],
    this.examDate,
    this.examMode = false,
    this.examModeActive = false,
  });

  factory StudyProfile.fromJson(Map<String, dynamic> json) {
    final rawDate = json['exam_date']?.toString();
    return StudyProfile(
      examTarget: (json['exam_target'] ?? '').toString(),
      classLevel: (json['class_level'] ?? '').toString(),
      focusSubjects: (json['focus_subjects'] as List?)?.map((e) => e.toString()).toList() ?? const [],
      examDate: (rawDate == null || rawDate.isEmpty) ? null : DateTime.tryParse(rawDate),
      examMode: json['exam_mode'] == true,
      examModeActive: json['exam_mode_active'] == true,
    );
  }

  /// Aaj se exam tak kitne din (None = date set nahi / nikal chuki).
  int? get daysToExam {
    final d = examDate;
    if (d == null) return null;
    final today = DateTime.now();
    final diff = DateTime(d.year, d.month, d.day).difference(DateTime(today.year, today.month, today.day)).inDays;
    return diff < 0 ? null : diff;
  }

  bool get isEmpty => examTarget.isEmpty && classLevel.isEmpty && focusSubjects.isEmpty && examDate == null;
}

class StudyProfileSaveResult {
  final StudyProfile? profile;
  final String? error;
  const StudyProfileSaveResult.ok(StudyProfile this.profile) : error = null;
  const StudyProfileSaveResult.failed(String this.error) : profile = null;
  bool get ok => profile != null;
}

class StudyProfileService {
  StudyProfileService._();

  static const Duration _timeout = Duration(seconds: 10);
  static const String _endpoint = '/profile/preferences/me/';

  /// null = offline / logged out / unexpected shape (kuch dikhane layak nahi).
  static Future<StudyProfile?> fetch() async {
    try {
      final token = await AuthService.getValidToken();
      if (token == null) return null;
      final res = await http.get(
        Uri.parse('${Api.baseUrl}$_endpoint'),
        headers: {'Authorization': 'Bearer $token'},
      ).timeout(_timeout);
      if (res.statusCode != 200) return null;
      final body = jsonDecode(utf8.decode(res.bodyBytes));
      final data = body is Map ? body['data'] : null;
      return data is Map<String, dynamic> ? StudyProfile.fromJson(data) : null;
    } catch (_) {
      return null;
    }
  }

  /// Sirf wahi keys bhejo jo badal rahi hain (PATCH partial). `exam_date: null`
  /// date hata deta hai; '' se exam_target / class_level clear hote hain.
  static Future<StudyProfileSaveResult> save(Map<String, dynamic> patch) async {
    try {
      final token = await AuthService.getValidToken();
      if (token == null) return const StudyProfileSaveResult.failed('Please log in again.');
      final res = await http
          .patch(
            Uri.parse('${Api.baseUrl}$_endpoint'),
            headers: {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'},
            body: jsonEncode(patch),
          )
          .timeout(_timeout);
      final body = jsonDecode(utf8.decode(res.bodyBytes));
      if (res.statusCode == 200 && body is Map && body['data'] is Map<String, dynamic>) {
        return StudyProfileSaveResult.ok(StudyProfile.fromJson(body['data'] as Map<String, dynamic>));
      }
      return StudyProfileSaveResult.failed(_firstError(body) ?? 'Save nahi ho paya.');
    } catch (_) {
      return const StudyProfileSaveResult.failed('Network problem — dobara try karo.');
    }
  }

  /// Exam Mode ka quick on/off.
  static Future<StudyProfileSaveResult> setExamMode(bool on) => save({'exam_mode': on});

  // DRF validation shape: {"status": false, "errors": {"field": ["msg", ...]}}
  static String? _firstError(dynamic body) {
    if (body is! Map) return null;
    final errors = body['errors'];
    if (errors is Map && errors.isNotEmpty) {
      final v = errors.values.first;
      if (v is List && v.isNotEmpty) return v.first.toString();
      return v.toString();
    }
    return body['message']?.toString();
  }
}
