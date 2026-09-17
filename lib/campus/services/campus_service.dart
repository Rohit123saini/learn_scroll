import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../utils/api.dart';
import '../../services/auth_service.dart';
import '../models/campus_models.dart';

// ============================================================
// CAMPUS — API SERVICE
//
// Pattern bilkul `assignment_service.dart` jaisa hai — static methods,
// `AuthService.getValidToken()`, `_asList()` se pagination normalize.
// Jaan-boojh kar same rakha hai taaki dono files ek jaisi padhi jaayein.
//
// Endpoints `campus/urls.py` ke DefaultRouter se aaye hain. Jahan backend
// ke naam me `assigments` typo hai wahan URL me wahi typo rakha hai (warna
// 404 aayega) — par Dart side ka method saaf naam se hai, aur ek const me
// alag kar diya hai taaki rename ke din sirf ek line badle.
// ============================================================

/// 🔧 CONFIRM WITH BACKEND — `campus.urls` root URLconf me kahan mount hai.
/// `assignment_service.dart` ne bhi yahi call liya tha (`/assigments`), to
/// same convention: no `/api` prefix. Galat ho to sirf ye line badalni hai.
const String kCampusMount = '/campus';

/// Backend rename (`assigments` → `assignments`) ke din sirf ye do lines
/// badlengi, 15 call sites nahi.
const String _kClassTeacherPath = 'class-teacher-assigmentss';
const String _kSubjectTeacherPath = 'subject-teacher-assigmentss';

class CampusApiException implements Exception {
  final String message;
  final int? statusCode;
  CampusApiException(this.message, {this.statusCode});

  bool get isForbidden => statusCode == 403;
  bool get isNotFound => statusCode == 404;

  @override
  String toString() => message;
}

class CampusService {
  CampusService._();

  static const Duration _timeout = Duration(seconds: 15);

  static String get _base => '${Api.baseUrl}$kCampusMount';

  static Future<Map<String, String>> _headers({bool json = true}) async {
    final token = await AuthService.getValidToken();
    if (token == null || token.isEmpty) {
      throw CampusApiException('NOT_AUTHENTICATED');
    }
    return {
      'Authorization': 'Bearer $token',
      if (json) 'Content-Type': 'application/json',
    };
  }

  static List<dynamic> _asList(dynamic decoded) {
    if (decoded is List) return decoded;
    if (decoded is Map && decoded['results'] is List) return decoded['results'] as List;
    return const [];
  }

  static Never _fail(http.Response r) {
    // DRF ka `{"detail": "..."}` nikaal ke dikhao — "Request failed (403)"
    // se user ko kuch samajh nahi aata, "Aap is section ke class teacher
    // nahi hain" se aata hai.
    String message = 'Request failed (${r.statusCode})';
    try {
      final decoded = jsonDecode(utf8.decode(r.bodyBytes));
      if (decoded is Map && decoded['detail'] != null) {
        message = decoded['detail'].toString();
      } else if (decoded is Map && decoded.isNotEmpty) {
        final first = decoded.entries.first;
        final v = first.value;
        message = '${first.key}: ${v is List ? v.join(', ') : v}';
      }
    } catch (_) {
      // body JSON nahi tha — default message hi theek hai
    }
    throw CampusApiException(message, statusCode: r.statusCode);
  }

  static Future<List<T>> _list<T>(
    String path,
    T Function(Map<String, dynamic>) parse, {
    Map<String, String>? query,
  }) async {
    final uri = Uri.parse('$_base/$path/').replace(
      queryParameters: (query == null || query.isEmpty) ? null : query,
    );
    final r = await http.get(uri, headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return _asList(jsonDecode(utf8.decode(r.bodyBytes)))
        .map((e) => parse(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  static Future<Map<String, dynamic>> _post(String path, Map<String, dynamic> body) async {
    final r = await http
        .post(Uri.parse('$_base/$path/'), headers: await _headers(), body: jsonEncode(body))
        .timeout(_timeout);
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    final decoded = jsonDecode(utf8.decode(r.bodyBytes));
    return decoded is Map ? Map<String, dynamic>.from(decoded) : <String, dynamic>{};
  }

  // ==========================================================
  // Phase 1 — hierarchy
  // ==========================================================

  /// Mere saare campuses. Backend already scope karta hai — jis campus me
  /// main na staff hoon, na student, na parent, wo list me aata hi nahi.
  static Future<List<Campus>> myCampuses() => _list('campuses', Campus.fromJson);

  static Future<List<AcademicSession>> sessions(String campusId) =>
      _list('sessions', AcademicSession.fromJson, query: {'campus': campusId});

  static Future<List<Department>> departments(String campusId) =>
      _list('departments', Department.fromJson, query: {'campus': campusId});

  static Future<List<SchoolClass>> classes(String campusId, {String? sessionId}) =>
      _list('classes', SchoolClass.fromJson, query: {
        'campus': campusId,
        if (sessionId != null) 'session': sessionId,
      });

  static Future<List<Section>> sections({String? schoolClassId}) =>
      _list('sections', Section.fromJson,
          query: {if (schoolClassId != null) 'school_class': schoolClassId});

  static Future<List<Subject>> subjects(String campusId) =>
      _list('subjects', Subject.fromJson, query: {'campus': campusId});

  // ==========================================================
  // Phase 2 — staff & enrollment
  // ==========================================================

  static Future<List<StaffProfile>> staff(String campusId) =>
      _list('staff', StaffProfile.fromJson, query: {'campus': campusId});

  static Future<List<ClassTeacherAssignment>> classTeachers() =>
      _list(_kClassTeacherPath, ClassTeacherAssignment.fromJson);

  static Future<List<SubjectTeacherAssignment>> subjectTeachers() =>
      _list(_kSubjectTeacherPath, SubjectTeacherAssignment.fromJson);

  static Future<List<StudentEnrollment>> enrollments({String? sectionId, String? sessionId}) =>
      _list('enrollments', StudentEnrollment.fromJson, query: {
        if (sectionId != null) 'section': sectionId,
        if (sessionId != null) 'session': sessionId,
      });

  /// Ek section ka roster, roll number se sorted.
  ///
  /// Roll numbers strings hain (`CharField`) par unme numbers hote hain —
  /// plain string sort me "10" < "2" aa jaata hai, jo attendance sheet me
  /// bahut confusing lagta hai. Isliye numeric-aware sort.
  static Future<List<StudentEnrollment>> roster(String sectionId, {String? sessionId}) async {
    final rows = await enrollments(sectionId: sectionId, sessionId: sessionId);
    final active = rows.where((e) => e.isActive).toList();
    active.sort((a, b) {
      final an = int.tryParse(a.rollNumber);
      final bn = int.tryParse(b.rollNumber);
      if (an != null && bn != null) return an.compareTo(bn);
      if (a.rollNumber.isEmpty && b.rollNumber.isEmpty) {
        return (a.student?.displayName ?? '').compareTo(b.student?.displayName ?? '');
      }
      if (a.rollNumber.isEmpty) return 1;
      if (b.rollNumber.isEmpty) return -1;
      return a.rollNumber.compareTo(b.rollNumber);
    });
    return active;
  }

  // ==========================================================
  // Phase 3 — notices
  // ==========================================================

  static Future<List<Notice>> notices(String campusId, {String? sectionId}) =>
      _list('notices', Notice.fromJson, query: {
        'campus': campusId,
        if (sectionId != null) 'section': sectionId,
      });

  /// Notice post karo. Scope ke liye sirf EK field bhejo — section, ya
  /// school_class, ya department, ya kuch nahi (= poora campus).
  ///
  /// Backend `NoticeSerializer.validate()` check karta hai ki jo scope
  /// diya hai wo usi campus ka ho, aur `can_post_notice()` check karta hai
  /// ki tumhe us scope pe post karne ka haq hai ya nahi.
  static Future<Notice> postNotice({
    required String campusId,
    required String sessionId,
    required String title,
    required String body,
    String? departmentId,
    String? schoolClassId,
    String? sectionId,
    DateTime? pinUntil,
  }) async {
    final json = await _post('notices', {
      'campus': campusId,
      'session': sessionId,
      'title': title,
      'body': body,
      if (departmentId != null) 'department': departmentId,
      if (schoolClassId != null) 'school_class': schoolClassId,
      if (sectionId != null) 'section': sectionId,
      if (pinUntil != null) 'pin_until': pinUntil.toUtc().toIso8601String(),
    });
    return Notice.fromJson(json);
  }

  // ==========================================================
  // Phase 5 — timetable & attendance
  // ==========================================================

  static Future<List<TimeSlot>> timeSlots(String campusId) =>
      _list('time-slots', TimeSlot.fromJson, query: {'campus': campusId});

  static Future<List<TimetableEntry>> timetable({String? sectionId, String? sessionId}) =>
      _list('timetable-entries', TimetableEntry.fromJson, query: {
        if (sectionId != null) 'section': sectionId,
        if (sessionId != null) 'session': sessionId,
      });

  static Future<List<Attendance>> attendance({
    String? enrollmentId,
    String? subjectId,
    DateTime? date,
  }) =>
      _list('attendance', Attendance.fromJson, query: {
        if (enrollmentId != null) 'enrollment': enrollmentId,
        if (subjectId != null) 'subject': subjectId,
        if (date != null) 'date': _ymd(date),
      });

  /// Ek din ka attendance mark karo.
  ///
  /// ⚠️ Backend pe koi bulk endpoint NAHI hai — `AttendanceViewSet` plain
  /// ModelViewSet hai, aur model pe `unique_attendance_per_day_subject`
  /// constraint hai. Isliye har student ka alag POST jaata hai.
  ///
  /// Sequential bhej rahe hain, parallel nahi: 60 students ka ek saath
  /// burst throttle (`campus-write`) me phans jaayega aur aadha roster
  /// silently save hoke aadha fail ho jaayega. Sequential slow hai par
  /// har row ka result pata chalta hai.
  ///
  /// Return: jo enrollment ids fail hue unki list — UI unhe wapas dikhata
  /// hai "in 3 ka save nahi hua, retry karo".
  static Future<List<String>> markAttendance({
    required Map<String, String> statusByEnrollmentId,
    required DateTime date,
    String? subjectId,
    void Function(int done, int total)? onProgress,
  }) async {
    final entries = statusByEnrollmentId.entries.toList();
    final failed = <String>[];

    for (var i = 0; i < entries.length; i++) {
      final e = entries[i];
      try {
        await _post('attendance', {
          'enrollment': e.key,
          'date': _ymd(date),
          'status': e.value,
          if (subjectId != null) 'subject': subjectId,
        });
      } on CampusApiException {
        failed.add(e.key);
      }
      onProgress?.call(i + 1, entries.length);
    }
    return failed;
  }

  /// `/attendance/summary/?enrollment=<id>` — backend har baar compute
  /// karta hai, store nahi. Student/parent apna dekh sakta hai, staff
  /// kisi ka bhi (backend hi decide karta hai).
  static Future<AttendanceSummary> attendanceSummary({
    required String enrollmentId,
    String? subjectId,
  }) async {
    final uri = Uri.parse('$_base/attendance/summary/').replace(queryParameters: {
      'enrollment': enrollmentId,
      if (subjectId != null) 'subject': subjectId,
    });
    final r = await http.get(uri, headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return AttendanceSummary.fromJson(
        Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  // ==========================================================
  // Phase 4 — live sessions
  // ==========================================================

  static Future<List<CampusLiveSession>> liveSessions({String? sectionId}) =>
      _list('live-sessions', CampusLiveSession.fromJson,
          query: {if (sectionId != null) 'section': sectionId});

  static Future<CampusLiveSession> scheduleLiveSession({
    required String sectionId,
    required String subjectId,
    required String teacherStaffId,
    required DateTime scheduledAt,
  }) async {
    final json = await _post('live-sessions', {
      'section': sectionId,
      'subject': subjectId,
      'teacher': teacherStaffId,
      'scheduled_at': scheduledAt.toUtc().toIso8601String(),
    });
    return CampusLiveSession.fromJson(json);
  }

  /// `POST /live-sessions/{id}/join/` — response me video room ka token
  /// aata hai. Shape backend pe LiveKit util se banti hai, isliye raw map
  /// return kar rahe hain; live-class screen isko consume karti hai.
  static Future<Map<String, dynamic>> joinLiveSession(String id) =>
      _post('live-sessions/$id/join', const {});

  static Future<Map<String, dynamic>> startLiveSession(String id) =>
      _post('live-sessions/$id/start', const {});

  static Future<Map<String, dynamic>> endLiveSession(String id) =>
      _post('live-sessions/$id/end', const {});

  // ==========================================================
  // Derived access
  // ==========================================================

  /// Ek campus ke liye mera poora access picture.
  ///
  /// Chaar calls parallel me — kyunki ye chaaron independent hain aur
  /// hub screen tab tak kuch dikha nahi sakti. Sequential karne pe
  /// pehli screen 4× slow khulti.
  ///
  /// 🔁 Jab backend pe `GET /access/me/?campus=<id>` live ho jaaye, poora
  /// method ek call se replace ho jaayega — screens ko haath nahi lagega.
  static Future<CampusAccess> loadAccess(Campus campus, {String? myUserId}) async {
    final results = await Future.wait([
      sessions(campus.id).catchError((_) => <AcademicSession>[]),
      staff(campus.id).catchError((_) => <StaffProfile>[]),
      classTeachers().catchError((_) => <ClassTeacherAssignment>[]),
      subjectTeachers().catchError((_) => <SubjectTeacherAssignment>[]),
      enrollments().catchError((_) => <StudentEnrollment>[]),
    ]);

    final sessionList = results[0] as List<AcademicSession>;
    final staffList = results[1] as List<StaffProfile>;
    final ctList = results[2] as List<ClassTeacherAssignment>;
    final stList = results[3] as List<SubjectTeacherAssignment>;
    final enrollList = results[4] as List<StudentEnrollment>;

    final uid = myUserId ?? await AuthService.getUserId();

    // `/staff/` list me poore campus ka staff aata hai — mera row dhoondho.
    StaffProfile? myStaff;
    for (final s in staffList) {
      if (s.campusId == campus.id && s.userId == uid && s.isActive) {
        myStaff = s;
        break;
      }
    }

    final myStaffId = myStaff?.id;
    final ctSections = <String>{};
    final stKeys = <String>{};

    if (myStaffId != null) {
      for (final ct in ctList) {
        if (ct.staffId == myStaffId) ctSections.add(ct.sectionId);
      }
      for (final st in stList) {
        // Sirf approved — pending binding se access nahi milta.
        if (st.staffId == myStaffId && st.isApproved) {
          stKeys.add(CampusAccess.subjectKey(st.sectionId, st.subjectId));
        }
      }
    }

    AcademicSession? current;
    for (final s in sessionList) {
      if (s.isCurrent) {
        current = s;
        break;
      }
    }
    current ??= sessionList.isNotEmpty ? sessionList.first : null;

    return CampusAccess(
      campus: campus,
      currentSession: current,
      staff: myStaff,
      classTeacherSectionIds: ctSections,
      subjectTeacherKeys: stKeys,
      myEnrollments: enrollList.where((e) => e.studentId == uid && e.isActive).toList(),
    );
  }

  static String _ymd(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-'
      '${d.month.toString().padLeft(2, '0')}-'
      '${d.day.toString().padLeft(2, '0')}';
}
