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

/// `POST /fee-payments/pay/`'s `402` body — insufficient wallet balance
/// (FEE-3, §19). Ek alag exception isliye taaki UI generic error string ki
/// jagah "itne coins aur chahiye" jaisa specific message de sake.
class InsufficientCoinsException extends CampusApiException {
  final int currentBalance;
  final int required;
  final int coinsNeeded;

  InsufficientCoinsException({
    required String message,
    required this.currentBalance,
    required this.required,
    required this.coinsNeeded,
  }) : super(message, statusCode: 402);
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

  static Future<Map<String, dynamic>> _patch(String path, Map<String, dynamic> body) async {
    final r = await http
        .patch(Uri.parse('$_base/$path/'), headers: await _headers(), body: jsonEncode(body))
        .timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
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

  static Future<List<Room>> rooms(String campusId) =>
      _list('rooms', Room.fromJson, query: {'campus': campusId});

  // ---- structural setup — create (Task: campus setup screens) ----
  //
  // Ye sab §19 (design doc) ke exact request contracts follow karte hain.
  // Koi bhi in endpoints me `campus.verification_status` ka gate nahi hai —
  // sirf staff/enrollment/parent-link growth wale endpoints (neeche) us
  // check se guzarte hain. Isliye ye methods `CampusAccess.canGrowMembership`
  // nahi, `canManageCampusSetup` (sirf role check) ke peeche hain — screen
  // khud decide karti hai, service blind hai.

  /// Naya campus banao. Side effect (server pe): requester ka apna pehla
  /// `StaffProfile` (role=admin) isi transaction me ban jaata hai — response
  /// me nahi dikhta, alag se `staff()` call karna padega agar turant chahiye.
  static Future<Campus> createCampus({
    required String name,
    required String type, // school | college | coaching
    int attendanceAlertThresholdPercent = 75,
    bool feeModuleEnabled = false,
  }) async {
    final json = await _post('campuses', {
      'name': name,
      'type': type,
      'attendance_alert_threshold_percent': attendanceAlertThresholdPercent,
      'fee_module_enabled': feeModuleEnabled,
    });
    return Campus.fromJson(json);
  }

  static Future<AcademicSession> createSession({
    required String campusId,
    required String name,
    required DateTime startDate,
    required DateTime endDate,
    bool isCurrent = false,
  }) async {
    final json = await _post('sessions', {
      'campus': campusId,
      'name': name,
      'start_date': _ymd(startDate),
      'end_date': _ymd(endDate),
      'is_current': isCurrent,
    });
    return AcademicSession.fromJson(json);
  }

  /// `POST /sessions/{id}/set-current/` — body nahi chahiye, backend hi
  /// baaki sessions ko un-current karta hai.
  static Future<AcademicSession> setCurrentSession(String sessionId) async {
    final json = await _post('sessions/$sessionId/set-current', const {});
    return AcademicSession.fromJson(json);
  }

  static Future<Department> createDepartment({
    required String campusId,
    required String name,
  }) async {
    final json = await _post('departments', {'campus': campusId, 'name': name});
    return Department.fromJson(json);
  }

  static Future<SchoolClass> createClass({
    required String campusId,
    required String sessionId,
    required String name,
    String? departmentId,
  }) async {
    final json = await _post('classes', {
      'campus': campusId,
      'session': sessionId,
      'department': departmentId,
      'name': name,
    });
    return SchoolClass.fromJson(json);
  }

  /// ⚠️ `campus` field Section pe hota hi nahi — permission backend
  /// `school_class.campus_id` se derive karta hai (§19). Body me sirf
  /// `school_class` bhejna hai, koi `campus` key nahi.
  static Future<Section> createSection({
    required String schoolClassId,
    required String name,
  }) async {
    final json = await _post('sections', {'school_class': schoolClassId, 'name': name});
    return Section.fromJson(json);
  }

  static Future<Subject> createSubject({
    required String campusId,
    required String name,
    String code = '',
    String? departmentId,
  }) async {
    final json = await _post('subjects', {
      'campus': campusId,
      'department': departmentId,
      'name': name,
      'code': code,
    });
    return Subject.fromJson(json);
  }

  static Future<Room> createRoom({
    required String campusId,
    required String name,
    bool isVirtual = false,
  }) async {
    final json = await _post('rooms', {
      'campus': campusId,
      'name': name,
      'is_virtual': isVirtual,
    });
    return Room.fromJson(json);
  }

  // ==========================================================
  // Phase 2 — staff & enrollment
  // ==========================================================

  static Future<List<StaffProfile>> staff(String campusId) =>
      _list('staff', StaffProfile.fromJson, query: {'campus': campusId});

  /// ⚠️ Sirf tabhi kaam karega jab campus `verification_status=approved`
  /// ho (screen se pehle `access.canGrowMembership` check karo — warna
  /// backend se 403 `"This campus is pending platform verification..."`
  /// aayega, jo yahan `CampusApiException` ban ke throw hoga).
  ///
  /// 🔧 CONFIRM WITH BACKEND — abhi koi user-search endpoint campus app me
  /// nahi hai (na design doc me, na urls.py me), isliye `userId` yahan seedha
  /// UUID leta hai. Jis din shared "user search"/invite API wire ho, staff
  /// add sheet me sirf ek autocomplete field jodna hai — is method ka
  /// signature waisa hi rahega.
  static Future<StaffProfile> createStaff({
    required String campusId,
    required String userId,
    required String role,
    bool isActive = true,
  }) async {
    final json = await _post('staff', {
      'campus': campusId,
      'user': userId,
      'role': role,
      'is_active': isActive,
    });
    return StaffProfile.fromJson(json);
  }

  static Future<List<ClassTeacherAssignment>> classTeachers() =>
      _list(_kClassTeacherPath, ClassTeacherAssignment.fromJson);

  /// Ek section ka class teacher assign karo. ⚠️ `section` `OneToOneField`
  /// hai — dobara isi section pe POST karna DB-level `IntegrityError` (500)
  /// dega (§19 GOTCHA, backend me abhi tak koi pre-check nahi hai). Screen
  /// isliye pehle `classTeachers()` se check karti hai ki section ka
  /// assignment already hai ya nahi, aur hai to sirf naya option nahi
  /// dikhati — replace karne ka koi safe tareeka backend pe nahi hai
  /// (delete-then-recreate bhi is method se nahi hota, ViewSet me delete
  /// route hai ya nahi wo confirm nahi hai).
  static Future<ClassTeacherAssignment> assignClassTeacher({
    required String sectionId,
    required String staffId,
  }) async {
    final json = await _post(_kClassTeacherPath, {'section': sectionId, 'staff': staffId});
    return ClassTeacherAssignment.fromJson(json);
  }

  static Future<List<SubjectTeacherAssignment>> subjectTeachers() =>
      _list(_kSubjectTeacherPath, SubjectTeacherAssignment.fromJson);

  /// Naya subject-teacher binding banao. Hamesha `status: pending` bante
  /// hai chahe koi bhi banaye (§19) — admin/CT khud ke liye bhi banayein to
  /// approve alag call se karna padega. Koi bhi active campus member
  /// (khud ke liye "request to teach") ya A/P/CT (kisi aur staff ke liye
  /// "assign") ye call kar sakta hai — backend field-level restrict nahi
  /// karta ki `staff` requester khud ho.
  static Future<SubjectTeacherAssignment> requestSubjectTeacher({
    required String sectionId,
    required String subjectId,
    required String staffId,
  }) async {
    final json = await _post(_kSubjectTeacherPath, {
      'section': sectionId,
      'subject': subjectId,
      'staff': staffId,
    });
    return SubjectTeacherAssignment.fromJson(json);
  }

  static Future<SubjectTeacherAssignment> approveSubjectTeacher(String id) async {
    final json = await _post('$_kSubjectTeacherPath/$id/approve', const {});
    return SubjectTeacherAssignment.fromJson(json);
  }

  static Future<SubjectTeacherAssignment> rejectSubjectTeacher(String id) async {
    final json = await _post('$_kSubjectTeacherPath/$id/reject', const {});
    return SubjectTeacherAssignment.fromJson(json);
  }

  static Future<List<StudentEnrollment>> enrollments({String? sectionId, String? sessionId}) =>
      _list('enrollments', StudentEnrollment.fromJson, query: {
        if (sectionId != null) 'section': sectionId,
        if (sessionId != null) 'session': sessionId,
      });

  /// ⚠️ Sirf tabhi kaam karega jab campus approved ho — `access.canGrowMembership`
  /// screen me pehle hi check karti hai (warna 403 "This campus is pending
  /// platform verification and can't enroll students yet.").
  ///
  /// 🔧 CONFIRM WITH BACKEND — `createStaff` jaisi hi limitation: koi
  /// student-search endpoint nahi hai, isliye `studentId` seedha UUID leta
  /// hai. Same future fix jab shared user-search wire ho.
  static Future<StudentEnrollment> enrollStudent({
    required String studentId,
    required String sectionId,
    required String sessionId,
    String rollNumber = '',
    String status = 'active',
  }) async {
    final json = await _post('enrollments', {
      'student': studentId,
      'section': sectionId,
      'session': sessionId,
      'roll_number': rollNumber,
      'status': status,
    });
    return StudentEnrollment.fromJson(json);
  }

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
  // Parent links (read-only + verify)
  // ==========================================================

  /// `campus` diya to usi campus tak seemit, warna requester ke saare
  /// campuses ke links (backend `CampusMemberScopedMixin` khud scope karta
  /// hai — koi extra query param nahi chahiye).
  static Future<List<CampusParentLink>> parentLinks({String? campusId}) =>
      _list('parent-links', CampusParentLink.fromJson,
          query: {if (campusId != null) 'campus': campusId});

  /// `POST /parent-links/verify/` — plain dict, koi serializer nahi (§19).
  /// Token khud is app me kabhi generate nahi hota — `message` app ke
  /// existing "parent access code" flow se milta hai (§10); yahan sirf
  /// verify hota hai. Campus abhi tak platform-approved nahi hua to bhi
  /// yahi 403 aayega jo `_fail` normal error ki tarah throw karega.
  static Future<CampusParentLink> verifyParentLink({
    required String campusId,
    required String token,
  }) async {
    final json = await _post('parent-links/verify', {'campus': campusId, 'token': token});
    return CampusParentLink.fromJson(json);
  }

  // ==========================================================
  // Phase 6 — assigmentss (thin proxy over unified `assigments` app) & syllabus
  // ==========================================================

  /// 🔧 INFERRED, NOT EXPLICITLY DOCUMENTED — `assigmentsViewSet` ki list
  /// filtering §19 me likhi nahi hai (sirf submissions ka
  /// `?assigments=<id>` confirm hai). Har doosri list is app me apne parent
  /// FK se filter hoti hai (`sections?school_class=`, `enrollments?section=`
  /// waghaira), isliye `?section=` yahan bhi wahi pattern maan ke bhej rahe
  /// hain — agar backend ignore kar de to sirf itna hoga ki poore campus
  /// ke assignments aa jayenge, screen khud client-side `sectionId` se
  /// filter kar leti hai as a safety net.
  static Future<List<CampusAssignment>> assignments(String sectionId) async {
    final rows = await _list('assigmentss', CampusAssignment.fromJson, query: {'section': sectionId});
    return rows.where((a) => a.sectionId == sectionId).toList();
  }

  /// `attachment` abhi is app se bhejna support nahi hai (file upload wire
  /// nahi hua — dekh lo README/PR note) — sirf text fields. Backend field
  /// khud optional hai (`attachment: file|null`), isliye ye chalta hai,
  /// bas attachment-less assignments post honge jab tak upload na jode.
  static Future<CampusAssignment> createAssignment({
    required String sectionId,
    required String subjectId,
    required String sessionId,
    required String title,
    String description = '',
    DateTime? dueDate,
  }) async {
    final json = await _post('assigmentss', {
      'section': sectionId,
      'subject': subjectId,
      'session': sessionId,
      'title': title,
      'description': description,
      'attachment': null,
      if (dueDate != null) 'due_date': _ymd(dueDate),
    });
    return CampusAssignment.fromJson(json);
  }

  /// Poore assignment ka roster (staff view) — confirmed contract
  /// (§19: "a separate GET /assigments-submissions/?assigments=<id> shows
  /// the roster").
  static Future<List<CampusAssignmentSubmission>> assignmentSubmissions(String assignmentId) =>
      _list('assigments-submissions', CampusAssignmentSubmission.fromJson,
          query: {'assigments': assignmentId});

  /// Edge case only (§19) — normal flow me har active enrollment ke liye
  /// submission row assignment POST ke saath hi ban jaati hai. Ye sirf tab
  /// chahiye jab student assignment post hone ke BAAD enroll hua ho aur
  /// uski row missing ho. `student` field jaan-boojh kar nahi bheja —
  /// server khud `request.user` force karta hai.
  static Future<CampusAssignmentSubmission> ensureOwnSubmission(String assignmentId) async {
    final json = await _post('assigments-submissions', {'assigments': assignmentId});
    return CampusAssignmentSubmission.fromJson(json);
  }

  /// Student apni submission "submit" mark karta hai. File upload abhi wire
  /// nahi hai (upar wali note dekho) — khaali body bhi valid hai (§19:
  /// "`{file: file W}` (or nothing)"), server khud `submitted_at`/`status`
  /// set kar deta hai.
  static Future<CampusAssignmentSubmission> submitAssignment(String submissionId) async {
    final json = await _patch('assigments-submissions/$submissionId', const {});
    return CampusAssignmentSubmission.fromJson(json);
  }

  static Future<CampusAssignmentSubmission> gradeSubmission({
    required String submissionId,
    required String grade,
    String feedback = '',
  }) async {
    final json = await _patch(
        'assigments-submissions/$submissionId', {'grade': grade, 'feedback': feedback});
    return CampusAssignmentSubmission.fromJson(json);
  }

  /// 🔧 INFERRED filter params — same posture as `assignments()` above,
  /// creation fields (`subject`/`section`/`session`) doubling as filters.
  static Future<List<SyllabusUnit>> syllabusUnits({
    required String subjectId,
    required String sectionId,
  }) async {
    final rows = await _list('syllabus-units', SyllabusUnit.fromJson,
        query: {'subject': subjectId, 'section': sectionId});
    return rows.where((u) => u.subjectId == subjectId && u.sectionId == sectionId).toList()
      ..sort((a, b) => a.order.compareTo(b.order));
  }

  static Future<SyllabusUnit> createSyllabusUnit({
    required String subjectId,
    required String sectionId,
    required String sessionId,
    required String title,
    int order = 0,
  }) async {
    final json = await _post('syllabus-units', {
      'subject': subjectId,
      'section': sectionId,
      'session': sessionId,
      'title': title,
      'order': order,
    });
    return SyllabusUnit.fromJson(json);
  }

  /// Same inferred-filter posture — `syllabus_unit` hi ek natural filter
  /// hai (`SyllabusProgress` ka khud ka campus/section/subject field nahi
  /// hai, sirf `syllabus_unit` FK).
  static Future<List<SyllabusProgress>> syllabusProgressFor(String syllabusUnitId) async {
    final rows = await _list('syllabus-progress', SyllabusProgress.fromJson,
        query: {'syllabus_unit': syllabusUnitId});
    return rows.where((p) => p.syllabusUnitId == syllabusUnitId).toList();
  }

  static Future<SyllabusProgress> markSyllabusCovered(String progressId) async {
    final json = await _post('syllabus-progress/$progressId/mark-covered', const {});
    return SyllabusProgress.fromJson(json);
  }

  // ==========================================================
  // Phase 7 — results
  // ==========================================================

  static Future<List<ExamTerm>> examTerms(String sessionId) async {
    final rows = await _list('exam-terms', ExamTerm.fromJson, query: {'session': sessionId});
    return rows.where((e) => e.sessionId == sessionId).toList();
  }

  static Future<ExamTerm> createExamTerm({
    required String sessionId,
    required String name,
    required DateTime startDate,
    required DateTime endDate,
  }) async {
    final json = await _post('exam-terms', {
      'session': sessionId,
      'name': name,
      'start_date': _ymd(startDate),
      'end_date': _ymd(endDate),
    });
    return ExamTerm.fromJson(json);
  }

  static Future<List<ResultEntry>> results({String? subjectId, String? examTermId}) =>
      _list('results', ResultEntry.fromJson, query: {
        if (subjectId != null) 'subject': subjectId,
        if (examTermId != null) 'exam_term': examTermId,
      });

  static Future<ResultEntry> createResult({
    required String enrollmentId,
    required String subjectId,
    required String examTermId,
    required double marksObtained,
    required double maxMarks,
    String remarks = '',
  }) async {
    final json = await _post('results', {
      'enrollment': enrollmentId,
      'subject': subjectId,
      'exam_term': examTermId,
      'marks_obtained': marksObtained,
      'max_marks': maxMarks,
      'remarks': remarks,
    });
    return ResultEntry.fromJson(json);
  }

  /// `400` agar `enrollment`/`exam_term` me se koi missing, `404` agar id
  /// resolve nahi hui ya requester authorized nahi (§19) — dono
  /// `CampusApiException` ban ke throw honge, `_fail` se.
  static Future<ReportCard> reportCard({required String enrollmentId, required String examTermId}) async {
    final uri = Uri.parse('$_base/results/report-card/')
        .replace(queryParameters: {'enrollment': enrollmentId, 'exam_term': examTermId});
    final r = await http.get(uri, headers: await _headers()).timeout(_timeout);
    if (r.statusCode != 200) _fail(r);
    return ReportCard.fromJson(Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  // ==========================================================
  // Phase 8 — digital ID cards
  // ==========================================================

  static Future<List<DigitalIDCard>> digitalIdCards(String campusId) =>
      _list('digital-id-cards', DigitalIDCard.fromJson, query: {'campus': campusId});

  /// `userId == self` → apna card (koi bhi campus member). Kisi aur ke liye
  /// sirf admin/principal — server khud check karta hai, yahan koi extra
  /// gate nahi (screen access.isManagement se pehle hi decide kar chuki
  /// hoti hai ki "issue for someone else" button dikhana hai ya nahi).
  static Future<DigitalIDCard> issueDigitalIdCard({
    required String userId,
    required String campusId,
    DateTime? validUntil,
  }) async {
    final json = await _post('digital-id-cards', {
      'user': userId,
      'campus': campusId,
      'valid_until': validUntil != null ? _ymd(validUntil) : null,
    });
    return DigitalIDCard.fromJson(json);
  }

  // ==========================================================
  // Phase 8 — fee module (§8, FEE-1..4 — wallet-backed, see model docstrings)
  // ==========================================================

  static Future<List<FeeStructure>> feeStructures(String campusId) =>
      _list('fee-structures', FeeStructure.fromJson, query: {'campus': campusId});

  /// `403` agar `campus.fee_module_enabled == false` — caller
  /// (`CampusSetupScreen`) is check ko `access.campus.feeModuleEnabled` se
  /// pehle hi UI me gate kar leta hai, par backend yahi 403 dega agar koi
  /// stale state se call kare.
  static Future<FeeStructure> createFeeStructure({
    required String campusId,
    required String sessionId,
    required String title,
    required double amount,
    required DateTime dueDate,
    String? schoolClassId,
    bool isActive = true,
  }) async {
    final json = await _post('fee-structures', {
      'campus': campusId,
      'school_class': schoolClassId,
      'session': sessionId,
      'title': title,
      'amount': amount,
      'due_date': _ymd(dueDate),
      'is_active': isActive,
    });
    return FeeStructure.fromJson(json);
  }

  /// `{invoices_created, already_existed}` — idempotent, dobara call karne
  /// se double-invoice nahi banta (§8).
  static Future<Map<String, int>> generateInvoices(String feeStructureId) async {
    final json = await _post('fee-structures/$feeStructureId/generate-invoices', const {});
    return {
      'invoices_created': (json['invoices_created'] as num?)?.toInt() ?? 0,
      'already_existed': (json['already_existed'] as num?)?.toInt() ?? 0,
    };
  }

  static Future<List<FeeInvoice>> feeInvoices({String? enrollmentId, String? feeStructureId}) =>
      _list('fee-invoices', FeeInvoice.fromJson, query: {
        if (enrollmentId != null) 'enrollment': enrollmentId,
        if (feeStructureId != null) 'fee_structure': feeStructureId,
      });

  static Future<List<FeePayment>> feePayments(String invoiceId) =>
      _list('fee-payments', FeePayment.fromJson, query: {'invoice': invoiceId});

  /// Self-serve — invoice ka apna student ya uska linked parent hi bula
  /// sakta hai. `amount` default poora `amount_due` hai, par ye sirf poore
  /// coins (whole number) le sakta hai — paisa-level partial payment wallet
  /// se nahi ho sakta (§8 unit-mismatch note). `402` par
  /// `InsufficientCoinsException` throw hoti hai, generic `_fail` nahi.
  static Future<FeePayment> payFee({
    required String invoiceId,
    int? amount,
    String? gatewayReference,
  }) async {
    final r = await http
        .post(
          Uri.parse('$_base/fee-payments/pay/'),
          headers: await _headers(),
          body: jsonEncode({
            'invoice': invoiceId,
            if (amount != null) 'amount': amount,
            if (gatewayReference != null) 'gateway_reference': gatewayReference,
          }),
        )
        .timeout(_timeout);
    if (r.statusCode == 402) {
      final decoded = Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map);
      throw InsufficientCoinsException(
        message: (decoded['detail'] ?? '').toString(),
        currentBalance: (decoded['current_balance'] as num?)?.toInt() ?? 0,
        required: (decoded['required'] as num?)?.toInt() ?? 0,
        coinsNeeded: (decoded['coins_needed'] as num?)?.toInt() ?? 0,
      );
    }
    if (r.statusCode != 200 && r.statusCode != 201) _fail(r);
    return FeePayment.fromJson(Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
  }

  /// Office/counter path — cash/cheque/bank-transfer/other, kabhi
  /// `wallet` nahi (backend 400 dega). Sirf staff/admin (§19).
  static Future<FeePayment> recordFeePayment({
    required String invoiceId,
    required String paymentMode,
    double? amount,
    String notes = '',
  }) async {
    final json = await _post('fee-payments/record', {
      'invoice': invoiceId,
      if (amount != null) 'amount': amount,
      'payment_mode': paymentMode,
      'notes': notes,
    });
    return FeePayment.fromJson(json);
  }

  /// Sirf `wallet` + `success` payment refund ho sakti hai (§19) — button
  /// khud caller `FeePayment.isRefundable` se gate karta hai.
  static Future<FeePayment> refundFeePayment(String paymentId) async {
    final json = await _post('fee-payments/$paymentId/refund', const {});
    return FeePayment.fromJson(json);
  }

  // ==========================================================
  // Phase 8 — analytics (read-only, Celery-computed)
  // ==========================================================

  /// `404` agar abhi tak koi snapshot nahi bana (naya campus, ya task abhi
  /// chala hi nahi) — us case me `null` return karta hai, exception nahi;
  /// screen ke liye ye ek normal "abhi data nahi hai" state hai, error nahi.
  static Future<CampusAnalyticsSnapshot?> analyticsLatest(String campusId) async {
    final uri = Uri.parse('$_base/analytics-snapshots/latest/').replace(queryParameters: {'campus': campusId});
    final r = await http.get(uri, headers: await _headers()).timeout(_timeout);
    if (r.statusCode == 404) return null;
    if (r.statusCode != 200) _fail(r);
    return CampusAnalyticsSnapshot.fromJson(
        Map<String, dynamic>.from(jsonDecode(utf8.decode(r.bodyBytes)) as Map));
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
