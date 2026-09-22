// ============================================================
// CAMPUS — MODELS
//
// Har class ka `fromJson` seedha `campus/serializers.py` ke `fields = [...]`
// se match karta hai. Koi field maine khud se nahi joda — jo backend bhejta
// hai bas wahi. Jahan serializer me `*_detail` nested object hai (jaise
// `StaffProfileSerializer.user_detail`), wahan `MinimalUser` use hua hai.
//
// Sab id UUID strings hain (`CampusBaseModel.id` = UUIDField), int nahi.
// ============================================================

// 🔥 FIX — `.characters` grapheme-cluster getter `String` par built-in nahi
// hai, `package:characters` se aata hai (Flutter SDK ka hi transitive dep
// hai, pubspec me alag se add karne ki zaroorat nahi). Isi ke bina
// `MinimalUser.initials` (neeche) compile hi nahi hota tha.
import 'package:characters/characters.dart';

// ------------------------------------------------------------
// shared
// ------------------------------------------------------------

/// `MinimalUserSerializer` — id, username, first_name, last_name.
class MinimalUser {
  final String id;
  final String username;
  final String firstName;
  final String lastName;

  const MinimalUser({
    required this.id,
    required this.username,
    this.firstName = '',
    this.lastName = '',
  });

  /// Roster/attendance list me yahi dikhta hai. Naam khaali ho to username
  /// pe gir jao — blank row dikhana sabse bura hai.
  String get displayName {
    final full = '$firstName $lastName'.trim();
    return full.isEmpty ? username : full;
  }

  String get initials {
    final n = displayName.trim();
    if (n.isEmpty) return '?';
    final parts = n.split(RegExp(r'\s+'));
    if (parts.length == 1) return parts.first.characters.take(1).toString().toUpperCase();
    return (parts.first.characters.take(1).toString() + parts.last.characters.take(1).toString())
        .toUpperCase();
  }

  factory MinimalUser.fromJson(Map<String, dynamic> j) => MinimalUser(
        id: j['id'].toString(),
        username: (j['username'] ?? '').toString(),
        firstName: (j['first_name'] ?? '').toString(),
        lastName: (j['last_name'] ?? '').toString(),
      );
}

DateTime? _date(dynamic v) {
  if (v == null) return null;
  return DateTime.tryParse(v.toString());
}

// ------------------------------------------------------------
// Phase 1 — hierarchy
// ------------------------------------------------------------

class Campus {
  final String id;
  final String name;
  final String type; // school | college | coaching
  final bool isActive;
  final bool feeModuleEnabled;
  final int attendanceAlertThresholdPercent;

  /// `pending` | `approved` | `rejected`. Campus banate hi `pending` hota
  /// hai — platform admin approve kare tabhi doosre log usme add ho sakte
  /// hain. UI me isko chhupana nahi chahiye, warna creator ko samajh hi
  /// nahi aayega ki uska campus abhi kaam kyun nahi kar raha.
  final String verificationStatus;
  final MinimalUser? createdBy;
  final DateTime? createdAt;

  const Campus({
    required this.id,
    required this.name,
    required this.type,
    this.isActive = true,
    this.feeModuleEnabled = false,
    this.attendanceAlertThresholdPercent = 75,
    this.verificationStatus = 'pending',
    this.createdBy,
    this.createdAt,
  });

  bool get isApproved => verificationStatus == 'approved';
  bool get isPending => verificationStatus == 'pending';
  bool get isRejected => verificationStatus == 'rejected';

  factory Campus.fromJson(Map<String, dynamic> j) => Campus(
        id: j['id'].toString(),
        name: (j['name'] ?? '').toString(),
        type: (j['type'] ?? '').toString(),
        isActive: j['is_active'] as bool? ?? true,
        feeModuleEnabled: j['fee_module_enabled'] as bool? ?? false,
        attendanceAlertThresholdPercent:
            (j['attendance_alert_threshold_percent'] as num?)?.toInt() ?? 75,
        verificationStatus: (j['verification_status'] ?? 'pending').toString(),
        createdBy: j['created_by'] is Map
            ? MinimalUser.fromJson(Map<String, dynamic>.from(j['created_by'] as Map))
            : null,
        createdAt: _date(j['created_at']),
      );
}

class AcademicSession {
  final String id;
  final String campusId;
  final String name; // "2026-27"
  final DateTime? startDate;
  final DateTime? endDate;
  final bool isCurrent;

  const AcademicSession({
    required this.id,
    required this.campusId,
    required this.name,
    this.startDate,
    this.endDate,
    this.isCurrent = false,
  });

  factory AcademicSession.fromJson(Map<String, dynamic> j) => AcademicSession(
        id: j['id'].toString(),
        campusId: j['campus'].toString(),
        name: (j['name'] ?? '').toString(),
        startDate: _date(j['start_date']),
        endDate: _date(j['end_date']),
        isCurrent: j['is_current'] as bool? ?? false,
      );
}

class Department {
  final String id;
  final String campusId;
  final String name;

  const Department({required this.id, required this.campusId, required this.name});

  factory Department.fromJson(Map<String, dynamic> j) => Department(
        id: j['id'].toString(),
        campusId: j['campus'].toString(),
        name: (j['name'] ?? '').toString(),
      );
}

class SchoolClass {
  final String id;
  final String campusId;
  final String? departmentId;
  final String sessionId;
  final String name; // "Class 10", "B.Sc 2nd Year"

  const SchoolClass({
    required this.id,
    required this.campusId,
    required this.sessionId,
    required this.name,
    this.departmentId,
  });

  factory SchoolClass.fromJson(Map<String, dynamic> j) => SchoolClass(
        id: j['id'].toString(),
        campusId: j['campus'].toString(),
        departmentId: j['department']?.toString(),
        sessionId: j['session'].toString(),
        name: (j['name'] ?? '').toString(),
      );
}

class Section {
  final String id;
  final String schoolClassId;
  final String name; // "A", "B"

  const Section({required this.id, required this.schoolClassId, required this.name});

  factory Section.fromJson(Map<String, dynamic> j) => Section(
        id: j['id'].toString(),
        schoolClassId: j['school_class'].toString(),
        name: (j['name'] ?? '').toString(),
      );
}

class Subject {
  final String id;
  final String campusId;
  final String? departmentId;
  final String name;
  final String code;

  const Subject({
    required this.id,
    required this.campusId,
    required this.name,
    this.departmentId,
    this.code = '',
  });

  String get label => code.isEmpty ? name : '$name ($code)';

  factory Subject.fromJson(Map<String, dynamic> j) => Subject(
        id: j['id'].toString(),
        campusId: j['campus'].toString(),
        departmentId: j['department']?.toString(),
        name: (j['name'] ?? '').toString(),
        code: (j['code'] ?? '').toString(),
      );
}

/// `RoomSerializer` — `campus/urls.py` ke `rooms` route se aata hai.
/// Timetable entries isse optionally reference karte hain
/// (`TimetableEntry.roomId`); virtual room ho to physical location nahi
/// hoti, isliye `isVirtual` flag hai.
class Room {
  final String id;
  final String campusId;
  final String name;
  final bool isVirtual;

  const Room({
    required this.id,
    required this.campusId,
    required this.name,
    this.isVirtual = false,
  });

  factory Room.fromJson(Map<String, dynamic> j) => Room(
        id: j['id'].toString(),
        campusId: j['campus'].toString(),
        name: (j['name'] ?? '').toString(),
        isVirtual: j['is_virtual'] as bool? ?? false,
      );
}

// ------------------------------------------------------------
// Phase 2 — staff & enrollment
// ------------------------------------------------------------

/// `StaffProfile.Role` ke exact string values. Strings badalna mana hai —
/// ye DB me likhe hue hain (`campus/models.py`, class StaffProfile.Role).
enum CampusRole {
  admin('admin'),
  principalHod('principal_hod'),
  classTeacher('class_teacher'),
  subjectTeacher('subject_teacher'),
  nonTeaching('non_teaching'),
  student('student'),
  parent('parent'),
  none('none');

  const CampusRole(this.value);
  final String value;

  static CampusRole from(String? raw) => CampusRole.values.firstWhere(
        (r) => r.value == raw,
        orElse: () => CampusRole.none,
      );

  /// Management = campus-wide access. Ye wahi do roles hain jinhe backend
  /// `is_campus_admin_or_principal()` allow karta hai.
  bool get isManagement => this == CampusRole.admin || this == CampusRole.principalHod;

  bool get isStaff => const {
        CampusRole.admin,
        CampusRole.principalHod,
        CampusRole.classTeacher,
        CampusRole.subjectTeacher,
        CampusRole.nonTeaching,
      }.contains(this);

  bool get canTeach => this == CampusRole.classTeacher || this == CampusRole.subjectTeacher;
}

class StaffProfile {
  final String id;
  final String campusId;
  final String userId;
  final MinimalUser? user;
  final CampusRole role;
  final bool isActive;

  const StaffProfile({
    required this.id,
    required this.campusId,
    required this.userId,
    required this.role,
    this.user,
    this.isActive = true,
  });

  factory StaffProfile.fromJson(Map<String, dynamic> j) => StaffProfile(
        id: j['id'].toString(),
        campusId: j['campus'].toString(),
        userId: j['user'].toString(),
        user: j['user_detail'] is Map
            ? MinimalUser.fromJson(Map<String, dynamic>.from(j['user_detail'] as Map))
            : null,
        role: CampusRole.from(j['role']?.toString()),
        isActive: j['is_active'] as bool? ?? true,
      );
}

/// `ClassTeacherassigmentsSerializer` — section ke saath OneToOne.
/// (Naam backend ke typo se aaya hai; yahan saaf naam rakha hai, kyunki
/// Dart side pe wo typo carry karne ka koi fayda nahi.)
class ClassTeacherAssignment {
  final String id;
  final String sectionId;
  final String staffId;
  final StaffProfile? staff;

  const ClassTeacherAssignment({
    required this.id,
    required this.sectionId,
    required this.staffId,
    this.staff,
  });

  factory ClassTeacherAssignment.fromJson(Map<String, dynamic> j) => ClassTeacherAssignment(
        id: j['id'].toString(),
        sectionId: j['section'].toString(),
        staffId: j['staff'].toString(),
        staff: j['staff_detail'] is Map
            ? StaffProfile.fromJson(Map<String, dynamic>.from(j['staff_detail'] as Map))
            : null,
      );
}

/// Subject teacher binding. ⚠️ `status` yahan zaroori hai — backend me ye
/// `pending` → `approved`/`rejected` flow se guzarta hai, aur sirf
/// `approved` wale ko us subject pe access milta hai. Pending binding ko
/// UI me "assigned" dikhana galat hoga.
class SubjectTeacherAssignment {
  final String id;
  final String sectionId;
  final String subjectId;
  final String staffId;
  final StaffProfile? staff;
  final String status; // pending | approved | rejected

  const SubjectTeacherAssignment({
    required this.id,
    required this.sectionId,
    required this.subjectId,
    required this.staffId,
    this.staff,
    this.status = 'pending',
  });

  bool get isApproved => status == 'approved';

  factory SubjectTeacherAssignment.fromJson(Map<String, dynamic> j) => SubjectTeacherAssignment(
        id: j['id'].toString(),
        sectionId: j['section'].toString(),
        subjectId: j['subject'].toString(),
        staffId: j['staff'].toString(),
        staff: j['staff_detail'] is Map
            ? StaffProfile.fromJson(Map<String, dynamic>.from(j['staff_detail'] as Map))
            : null,
        status: (j['status'] ?? 'pending').toString(),
      );
}

class StudentEnrollment {
  final String id;
  final String studentId;
  final MinimalUser? student;
  final String sectionId;
  final String sessionId;
  final String rollNumber;
  final String status; // active | transferred | left ...

  const StudentEnrollment({
    required this.id,
    required this.studentId,
    required this.sectionId,
    required this.sessionId,
    this.student,
    this.rollNumber = '',
    this.status = 'active',
  });

  bool get isActive => status == 'active';

  factory StudentEnrollment.fromJson(Map<String, dynamic> j) => StudentEnrollment(
        id: j['id'].toString(),
        studentId: j['student'].toString(),
        student: j['student_detail'] is Map
            ? MinimalUser.fromJson(Map<String, dynamic>.from(j['student_detail'] as Map))
            : null,
        sectionId: j['section'].toString(),
        sessionId: j['session'].toString(),
        rollNumber: (j['roll_number'] ?? '').toString(),
        status: (j['status'] ?? 'active').toString(),
      );
}

/// `CampusParentLinkSerializer` — read-only from the API (§19). The ONLY
/// write path is `POST /parent-links/verify/`, jo yahan model ke bahar
/// (`CampusService.verifyParentLink`) plain dict leta hai, koi serializer
/// nahi. Token khud campus app me kabhi banta/dikhta nahi — `message`
/// app ke existing `ParentAccessCode` flow se aata hai (§10); campus sirf
/// usko resolve/verify karta hai.
class CampusParentLink {
  final String id;
  final String campusId;
  final String studentId;
  final MinimalUser? student;
  final String parentId;
  final MinimalUser? parent;
  final DateTime? createdAt;

  /// `active_enrollment` — [Task 4 gap-fix] child ka is campus me abhi ka
  /// enrollment (attendance-summary/report-card ke liye zaroori
  /// `enrollment` id + display context). `null` = student is campus me
  /// currently kisi active section me enrolled nahi (transferred/
  /// graduated/abhi tak enroll hi nahi hua) — screen ko "no active
  /// enrollment" state dikhana chahiye, error nahi.
  final ParentChildEnrollment? activeEnrollment;

  const CampusParentLink({
    required this.id,
    required this.campusId,
    required this.studentId,
    required this.parentId,
    this.student,
    this.parent,
    this.createdAt,
    this.activeEnrollment,
  });

  factory CampusParentLink.fromJson(Map<String, dynamic> j) => CampusParentLink(
        id: j['id'].toString(),
        campusId: j['campus'].toString(),
        studentId: j['student'].toString(),
        student: j['student_detail'] is Map
            ? MinimalUser.fromJson(Map<String, dynamic>.from(j['student_detail'] as Map))
            : null,
        parentId: j['parent'].toString(),
        parent: j['parent_detail'] is Map
            ? MinimalUser.fromJson(Map<String, dynamic>.from(j['parent_detail'] as Map))
            : null,
        createdAt: _date(j['created_at']),
        activeEnrollment: j['active_enrollment'] is Map
            ? ParentChildEnrollment.fromJson(Map<String, dynamic>.from(j['active_enrollment'] as Map))
            : null,
      );
}

/// `CampusParentLinkSerializer.get_active_enrollment()` ka chhota,
/// read-only shape — sirf wahi fields jo parent overview screen ko
/// chahiye (§ backend serializer's own FIX note).
class ParentChildEnrollment {
  final String id;
  final String sectionId;
  final String sectionName;
  final String? schoolClassId;
  final String schoolClassName;
  final String? departmentId;
  final String sessionId;

  const ParentChildEnrollment({
    required this.id,
    required this.sectionId,
    required this.sectionName,
    required this.schoolClassName,
    required this.sessionId,
    this.schoolClassId,
    this.departmentId,
  });

  factory ParentChildEnrollment.fromJson(Map<String, dynamic> j) => ParentChildEnrollment(
        id: j['id'].toString(),
        sectionId: j['section_id'].toString(),
        sectionName: j['section_name']?.toString() ?? '',
        schoolClassId: j['school_class_id']?.toString(),
        schoolClassName: j['school_class_name']?.toString() ?? '',
        departmentId: j['department_id']?.toString(),
        sessionId: j['session_id'].toString(),
      );

  String get label => schoolClassName.isNotEmpty ? '$schoolClassName · $sectionName' : sectionName;
}

// ------------------------------------------------------------
// Phase 3 — notices
// ------------------------------------------------------------

class Notice {
  final String id;
  final String campusId;
  final String? departmentId;
  final String? schoolClassId;
  final String? sectionId;
  final String sessionId;
  final MinimalUser? postedBy;
  final String title;
  final String body;
  final DateTime? pinUntil;
  final DateTime? createdAt;

  const Notice({
    required this.id,
    required this.campusId,
    required this.sessionId,
    required this.title,
    required this.body,
    this.departmentId,
    this.schoolClassId,
    this.sectionId,
    this.postedBy,
    this.pinUntil,
    this.createdAt,
  });

  bool get isPinned => pinUntil != null && pinUntil!.isAfter(DateTime.now());

  /// Kis tak pahuncha — sabse chhota scope jeetta hai. Ye backend ke
  /// `NoticeSerializer.validate()` ki mirror-image hai: section set hai to
  /// wahi asli audience hai, chahe campus bhi bhara ho.
  String get scopeLabel {
    if (sectionId != null) return 'section';
    if (schoolClassId != null) return 'class';
    if (departmentId != null) return 'department';
    return 'campus';
  }

  factory Notice.fromJson(Map<String, dynamic> j) => Notice(
        id: j['id'].toString(),
        campusId: j['campus'].toString(),
        departmentId: j['department']?.toString(),
        schoolClassId: j['school_class']?.toString(),
        sectionId: j['section']?.toString(),
        sessionId: j['session'].toString(),
        postedBy: j['posted_by'] is Map
            ? MinimalUser.fromJson(Map<String, dynamic>.from(j['posted_by'] as Map))
            : null,
        title: (j['title'] ?? '').toString(),
        body: (j['body'] ?? '').toString(),
        pinUntil: _date(j['pin_until']),
        createdAt: _date(j['created_at']),
      );
}

// ------------------------------------------------------------
// Phase 5 — timetable & attendance
// ------------------------------------------------------------

class TimeSlot {
  final String id;
  final String campusId;

  /// `Day.choices` — backend me `PositiveSmallIntegerField`. Django ka
  /// convention 0=Monday hai (Python `weekday()` jaisa), 1=Monday nahi.
  final int dayOfWeek;
  final String startTime; // "09:00:00"
  final String endTime;
  final String label; // "Period 3"

  const TimeSlot({
    required this.id,
    required this.campusId,
    required this.dayOfWeek,
    required this.startTime,
    required this.endTime,
    this.label = '',
  });

  static const List<String> dayNames = [
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday',
  ];

  String get dayName => dayOfWeek >= 0 && dayOfWeek < 7 ? dayNames[dayOfWeek] : '—';

  /// "09:00:00" -> "9:00 AM". Server se hamesha 24h HH:mm:ss aata hai.
  String get prettyStart => _pretty(startTime);
  String get prettyEnd => _pretty(endTime);

  static String _pretty(String raw) {
    final parts = raw.split(':');
    if (parts.length < 2) return raw;
    final h = int.tryParse(parts[0]) ?? 0;
    final m = parts[1];
    final suffix = h >= 12 ? 'PM' : 'AM';
    final h12 = h % 12 == 0 ? 12 : h % 12;
    return '$h12:$m $suffix';
  }

  factory TimeSlot.fromJson(Map<String, dynamic> j) => TimeSlot(
        id: j['id'].toString(),
        campusId: j['campus'].toString(),
        dayOfWeek: (j['day_of_week'] as num?)?.toInt() ?? 0,
        startTime: (j['start_time'] ?? '').toString(),
        endTime: (j['end_time'] ?? '').toString(),
        label: (j['label'] ?? '').toString(),
      );
}

class TimetableEntry {
  final String id;
  final String sectionId;
  final String subjectId;
  final String staffId;
  final String timeSlotId;
  final String? roomId;
  final String sessionId;

  const TimetableEntry({
    required this.id,
    required this.sectionId,
    required this.subjectId,
    required this.staffId,
    required this.timeSlotId,
    required this.sessionId,
    this.roomId,
  });

  factory TimetableEntry.fromJson(Map<String, dynamic> j) => TimetableEntry(
        id: j['id'].toString(),
        sectionId: j['section'].toString(),
        subjectId: j['subject'].toString(),
        staffId: j['staff'].toString(),
        timeSlotId: j['time_slot'].toString(),
        roomId: j['room']?.toString(),
        sessionId: j['session'].toString(),
      );
}

/// `Attendance.Status` — backend me `present`/`absent`/`late`/`excused`
/// jaise values hain. Enum me hardcode karne ki jagah string rakhi hai
/// taaki backend ek naya status add kare to app crash na ho.
class AttendanceStatus {
  static const present = 'present';
  static const absent = 'absent';
  static const late = 'late';
  static const excused = 'excused';

  static const all = [present, absent, late, excused];

  static String label(String s) => switch (s) {
        present => 'Present',
        absent => 'Absent',
        late => 'Late',
        excused => 'Excused',
        _ => s,
      };
}

class Attendance {
  final String id;
  final String enrollmentId;
  final DateTime date;
  final String? subjectId;
  final String status;
  final String? markedById;

  const Attendance({
    required this.id,
    required this.enrollmentId,
    required this.date,
    required this.status,
    this.subjectId,
    this.markedById,
  });

  factory Attendance.fromJson(Map<String, dynamic> j) => Attendance(
        id: j['id'].toString(),
        enrollmentId: j['enrollment'].toString(),
        date: _date(j['date']) ?? DateTime.now(),
        subjectId: j['subject']?.toString(),
        status: (j['status'] ?? '').toString(),
        markedById: j['marked_by']?.toString(),
      );
}

/// `/attendance/summary/?enrollment=<id>` ka response.
///
/// Backend ise kabhi store nahi karta — har baar compute karta hai
/// (`compute_attendance_summary`, campus/services.py). Isliye yahan bhi
/// koi caching nahi, aur exact keys ka pata backend se hi chalta hai —
/// `raw` rakh liya hai taaki koi extra key aaye to chhoote na.
class AttendanceSummary {
  final int total;
  final int present;
  final double percent;
  final Map<String, dynamic> raw;

  const AttendanceSummary({
    required this.total,
    required this.present,
    required this.percent,
    this.raw = const {},
  });

  int get absent => total - present;

  /// `Campus.attendance_alert_threshold_percent` (default 75) se compare.
  bool isBelow(int threshold) => total > 0 && percent < threshold;

  factory AttendanceSummary.fromJson(Map<String, dynamic> j) {
    final total = (j['total'] ?? j['total_classes'] ?? j['total_days'] ?? 0) as num;
    final present = (j['present'] ?? j['present_count'] ?? j['present_days'] ?? 0) as num;
    final pct = j['percent'] ?? j['percentage'] ?? j['attendance_percent'];
    return AttendanceSummary(
      total: total.toInt(),
      present: present.toInt(),
      percent: pct is num
          ? pct.toDouble()
          : (total > 0 ? (present.toDouble() / total.toDouble()) * 100 : 0),
      raw: j,
    );
  }
}

// ------------------------------------------------------------
// Phase 4 — live sessions
// ------------------------------------------------------------

class CampusLiveSession {
  final String id;
  final String sectionId;
  final String subjectId;
  final String teacherId;
  final DateTime? scheduledAt;
  final String status; // scheduled | live | ended | cancelled
  final String roomId;

  const CampusLiveSession({
    required this.id,
    required this.sectionId,
    required this.subjectId,
    required this.teacherId,
    required this.status,
    this.scheduledAt,
    this.roomId = '',
  });

  bool get isLive => status == 'live';
  bool get isScheduled => status == 'scheduled';

  factory CampusLiveSession.fromJson(Map<String, dynamic> j) => CampusLiveSession(
        id: j['id'].toString(),
        sectionId: j['section'].toString(),
        subjectId: j['subject'].toString(),
        teacherId: j['teacher'].toString(),
        scheduledAt: _date(j['scheduled_at']),
        status: (j['status'] ?? 'scheduled').toString(),
        roomId: (j['room_id'] ?? '').toString(),
      );
}

// ------------------------------------------------------------
// Phase 6 — assigmentss & syllabus
// ------------------------------------------------------------

/// `assigmentsViewSet` ka response — `campus.assigments` model DEPRECATED
/// hai (read-only history), asli data `assigments` app (unified) me hai,
/// par response shape jaan-boojh kar OLD shape rakha gaya hai
/// (`_serialize_campus_assigments()`), isliye ye model waisa hi hai jaisa
/// pehle hota. **`posted_by` sirf ek id hai — koi `*_detail` nested object
/// nahi backend deta**, isliye yahan `MinimalUser` nahi, sirf `String?`.
class CampusAssignment {
  final String id;
  final String sectionId;
  final String subjectId;
  final String? postedById;
  final String title;
  final String description;
  final String? attachmentUrl;
  final DateTime? dueDate;
  final String sessionId;

  const CampusAssignment({
    required this.id,
    required this.sectionId,
    required this.subjectId,
    required this.title,
    required this.sessionId,
    this.postedById,
    this.description = '',
    this.attachmentUrl,
    this.dueDate,
  });

  factory CampusAssignment.fromJson(Map<String, dynamic> j) => CampusAssignment(
        id: j['id'].toString(),
        sectionId: j['section'].toString(),
        subjectId: j['subject'].toString(),
        postedById: j['posted_by']?.toString(),
        title: (j['title'] ?? '').toString(),
        description: (j['description'] ?? '').toString(),
        attachmentUrl: j['attachment']?.toString(),
        dueDate: _date(j['due_date']),
        sessionId: j['session'].toString(),
      );
}

/// `assigmentsSubmissionViewSet` — same OLD-shape posture as above.
/// ⚠️ `status` ab `submitted`/`late`/`missing` ke alawa unified model se
/// `checked`/`partially_checked` bhi ho sakta hai (structured-question
/// grading path) — purana 3-state UI assumption yahan galat hoga, isliye
/// koi bhi status string blind trust karo, enum me lock mat karo.
class CampusAssignmentSubmission {
  final String id;
  final String assignmentId;
  final String studentId;
  final MinimalUser? student;
  final DateTime? submittedAt;
  final String? fileUrl;
  final String status; // submitted | late | missing | checked | partially_checked
  final String? grade;
  final String feedback;

  const CampusAssignmentSubmission({
    required this.id,
    required this.assignmentId,
    required this.studentId,
    this.student,
    this.submittedAt,
    this.fileUrl,
    this.status = 'missing',
    this.grade,
    this.feedback = '',
  });

  bool get isSubmitted => status != 'missing';
  bool get isGraded => grade != null && grade!.isNotEmpty;

  factory CampusAssignmentSubmission.fromJson(Map<String, dynamic> j) => CampusAssignmentSubmission(
        id: j['id'].toString(),
        assignmentId: j['assigments'].toString(),
        studentId: j['student'].toString(),
        student: j['student_detail'] is Map
            ? MinimalUser.fromJson(Map<String, dynamic>.from(j['student_detail'] as Map))
            : null,
        submittedAt: _date(j['submitted_at']),
        fileUrl: j['file']?.toString(),
        status: (j['status'] ?? 'missing').toString(),
        grade: j['grade']?.toString(),
        feedback: (j['feedback'] ?? '').toString(),
      );
}

class SyllabusUnit {
  final String id;
  final String subjectId;
  final String sectionId;
  final String sessionId;
  final String title;
  final int order;

  const SyllabusUnit({
    required this.id,
    required this.subjectId,
    required this.sectionId,
    required this.sessionId,
    required this.title,
    this.order = 0,
  });

  factory SyllabusUnit.fromJson(Map<String, dynamic> j) => SyllabusUnit(
        id: j['id'].toString(),
        subjectId: j['subject'].toString(),
        sectionId: j['section'].toString(),
        sessionId: j['session'].toString(),
        title: (j['title'] ?? '').toString(),
        order: (j['order'] as num?)?.toInt() ?? 0,
      );
}

/// Har `SyllabusUnit` ke saath ek `SyllabusProgress` row auto ban jaati hai
/// (unit create hote hi `get_or_create`, §19) — is app se sirf
/// `mark-covered` action call hota hai, koi generic create/update/delete
/// nahi (405).
class SyllabusProgress {
  final String id;
  final String syllabusUnitId;
  final DateTime? coveredOn;
  final String? coveredById;

  const SyllabusProgress({
    required this.id,
    required this.syllabusUnitId,
    this.coveredOn,
    this.coveredById,
  });

  bool get isCovered => coveredOn != null;

  factory SyllabusProgress.fromJson(Map<String, dynamic> j) => SyllabusProgress(
        id: j['id'].toString(),
        syllabusUnitId: j['syllabus_unit'].toString(),
        coveredOn: _date(j['covered_on']),
        coveredById: j['covered_by']?.toString(),
      );
}

// ------------------------------------------------------------
// Phase 7 — results
// ------------------------------------------------------------

class ExamTerm {
  final String id;
  final String sessionId;
  final String name;
  final DateTime? startDate;
  final DateTime? endDate;

  const ExamTerm({
    required this.id,
    required this.sessionId,
    required this.name,
    this.startDate,
    this.endDate,
  });

  factory ExamTerm.fromJson(Map<String, dynamic> j) => ExamTerm(
        id: j['id'].toString(),
        sessionId: j['session'].toString(),
        name: (j['name'] ?? '').toString(),
        startDate: _date(j['start_date']),
        endDate: _date(j['end_date']),
      );
}

class ResultEntry {
  final String id;
  final String enrollmentId;
  final String subjectId;
  final String examTermId;
  final double marksObtained;
  final double maxMarks;
  final String remarks;
  final String? enteredById;

  const ResultEntry({
    required this.id,
    required this.enrollmentId,
    required this.subjectId,
    required this.examTermId,
    required this.marksObtained,
    required this.maxMarks,
    this.remarks = '',
    this.enteredById,
  });

  factory ResultEntry.fromJson(Map<String, dynamic> j) => ResultEntry(
        id: j['id'].toString(),
        enrollmentId: j['enrollment'].toString(),
        subjectId: j['subject'].toString(),
        examTermId: j['exam_term'].toString(),
        marksObtained: (j['marks_obtained'] as num?)?.toDouble() ?? 0,
        maxMarks: (j['max_marks'] as num?)?.toDouble() ?? 0,
        remarks: (j['remarks'] ?? '').toString(),
        enteredById: j['entered_by']?.toString(),
      );
}

class ReportCardSubjectRow {
  final String subjectId;
  final String subjectName;
  final double marksObtained;
  final double maxMarks;
  final String remarks;

  const ReportCardSubjectRow({
    required this.subjectId,
    required this.subjectName,
    required this.marksObtained,
    required this.maxMarks,
    this.remarks = '',
  });

  factory ReportCardSubjectRow.fromJson(Map<String, dynamic> j) => ReportCardSubjectRow(
        subjectId: j['subject'].toString(),
        subjectName: (j['subject_name'] ?? '').toString(),
        marksObtained: (j['marks_obtained'] as num?)?.toDouble() ?? 0,
        maxMarks: (j['max_marks'] as num?)?.toDouble() ?? 0,
        remarks: (j['remarks'] ?? '').toString(),
      );
}

/// `GET /results/report-card/?enrollment=&exam_term=` ka poora response.
class ReportCard {
  final String enrollmentId;
  final String examTermId;
  final List<ReportCardSubjectRow> subjects;
  final double totalObtained;
  final double totalMax;
  final double percentage;

  const ReportCard({
    required this.enrollmentId,
    required this.examTermId,
    required this.subjects,
    required this.totalObtained,
    required this.totalMax,
    required this.percentage,
  });

  factory ReportCard.fromJson(Map<String, dynamic> j) => ReportCard(
        enrollmentId: j['enrollment'].toString(),
        examTermId: j['exam_term'].toString(),
        subjects: (j['subjects'] as List? ?? const [])
            .map((e) => ReportCardSubjectRow.fromJson(Map<String, dynamic>.from(e as Map)))
            .toList(),
        totalObtained: (j['total_obtained'] as num?)?.toDouble() ?? 0,
        totalMax: (j['total_max'] as num?)?.toDouble() ?? 0,
        percentage: (j['percentage'] as num?)?.toDouble() ?? 0,
      );
}

// ------------------------------------------------------------
// Phase 8 — optional / future-ready modules
// ------------------------------------------------------------

/// `qr_token` hamesha server-generated hai (§19) — client kabhi nahi bhejta.
/// Actual QR-image render abhi wire nahi hai (koi QR-drawing package
/// confirm nahi kar paya is pass me) — screen token ko text/copyable form
/// me dikhati hai; scanning hardware integration khud backend pe bhi
/// "future work" flag hai.
class DigitalIDCard {
  final String id;
  final String userId;
  final MinimalUser? user;
  final String campusId;
  final String qrToken;
  final DateTime? issuedAt;
  final DateTime? validUntil;

  const DigitalIDCard({
    required this.id,
    required this.userId,
    required this.campusId,
    required this.qrToken,
    this.user,
    this.issuedAt,
    this.validUntil,
  });

  factory DigitalIDCard.fromJson(Map<String, dynamic> j) => DigitalIDCard(
        id: j['id'].toString(),
        userId: j['user'].toString(),
        user: j['user_detail'] is Map
            ? MinimalUser.fromJson(Map<String, dynamic>.from(j['user_detail'] as Map))
            : null,
        campusId: j['campus'].toString(),
        qrToken: (j['qr_token'] ?? '').toString(),
        issuedAt: _date(j['issued_at']),
        validUntil: _date(j['valid_until']),
      );
}

/// Ek fee ki DEFINITION — payment nahi (§8). `schoolClassId == null` matlab
/// campus-wide fee.
class FeeStructure {
  final String id;
  final String campusId;
  final String? schoolClassId;
  final String sessionId;
  final String title;
  final double amount;
  final DateTime? dueDate;
  final bool isActive;

  const FeeStructure({
    required this.id,
    required this.campusId,
    required this.sessionId,
    required this.title,
    required this.amount,
    this.schoolClassId,
    this.dueDate,
    this.isActive = true,
  });

  factory FeeStructure.fromJson(Map<String, dynamic> j) => FeeStructure(
        id: j['id'].toString(),
        campusId: j['campus'].toString(),
        schoolClassId: j['school_class']?.toString(),
        sessionId: j['session'].toString(),
        title: (j['title'] ?? '').toString(),
        amount: (j['amount'] as num?)?.toDouble() ?? 0,
        dueDate: _date(j['due_date']),
        isActive: j['is_active'] as bool? ?? true,
      );
}

/// Ek student ka ek `FeeStructure` ke against obligation. `amountPaid` aur
/// `status` dono server-computed hain (`recompute_status()`,
/// `amount_paid` @property) — kabhi client se set nahi karna, dobara fetch
/// karke hi latest dikhana.
class FeeInvoice {
  final String id;
  final String enrollmentId;
  final String feeStructureId;
  final double amountDue;
  final double amountPaid;
  final String status; // pending | partial | paid | overdue | waived
  final DateTime? createdAt;

  const FeeInvoice({
    required this.id,
    required this.enrollmentId,
    required this.feeStructureId,
    required this.amountDue,
    this.amountPaid = 0,
    this.status = 'pending',
    this.createdAt,
  });

  double get remaining => (amountDue - amountPaid).clamp(0, amountDue);
  bool get isSettled => status == 'paid' || status == 'waived';

  factory FeeInvoice.fromJson(Map<String, dynamic> j) => FeeInvoice(
        id: j['id'].toString(),
        enrollmentId: j['enrollment'].toString(),
        feeStructureId: j['fee_structure'].toString(),
        amountDue: (j['amount_due'] as num?)?.toDouble() ?? 0,
        amountPaid: (j['amount_paid'] as num?)?.toDouble() ?? 0,
        status: (j['status'] ?? 'pending').toString(),
        createdAt: _date(j['created_at']),
      );
}

/// Ek money-movement event. `paymentMode: wallet` self-serve path se aata
/// hai (coin wallet se debit — FEE-2), baaki (`cash`/`cheque`/
/// `bank_transfer`/`other`) office-staff `record` se.
class FeePayment {
  final String id;
  final String invoiceId;
  final double amount;
  final String? paidById;
  final String payerRole; // student | parent | admin
  final String paymentMode; // wallet | cash | cheque | bank_transfer | other
  final String status; // pending | success | failed | refunded
  final String gatewayReference;
  final String? recordedById;
  final String notes;
  final DateTime? createdAt;

  const FeePayment({
    required this.id,
    required this.invoiceId,
    required this.amount,
    required this.payerRole,
    required this.paymentMode,
    required this.status,
    this.paidById,
    this.gatewayReference = '',
    this.recordedById,
    this.notes = '',
    this.createdAt,
  });

  bool get isSuccess => status == 'success';
  bool get isRefundable => isSuccess && paymentMode == 'wallet';

  factory FeePayment.fromJson(Map<String, dynamic> j) => FeePayment(
        id: j['id'].toString(),
        invoiceId: j['invoice'].toString(),
        amount: (j['amount'] as num?)?.toDouble() ?? 0,
        paidById: j['paid_by']?.toString(),
        payerRole: (j['payer_role'] ?? '').toString(),
        paymentMode: (j['payment_mode'] ?? '').toString(),
        status: (j['status'] ?? 'pending').toString(),
        gatewayReference: (j['gateway_reference'] ?? '').toString(),
        recordedById: j['recorded_by']?.toString(),
        notes: (j['notes'] ?? '').toString(),
        createdAt: _date(j['created_at']),
      );
}

/// `GET /analytics-snapshots/latest/?campus=` ka `data` field — backend
/// jaan-boojh kar minimal hai (§12: teacher-workload waghera abhi nahi
/// compute hote), isliye yahan bhi sirf inhi 4 numbers ka structured wrapper
/// hai; kal koi naya key aaye to `raw` se mil jaayega.
class CampusAnalyticsSnapshot {
  final String id;
  final String campusId;
  final String sessionId;
  final DateTime? computedAt;
  final double avgAttendancePercent;
  final double avgMarksObtained;
  final double syllabusCompletionPercent;
  final int activeEnrollments;
  final Map<String, dynamic> raw;

  const CampusAnalyticsSnapshot({
    required this.id,
    required this.campusId,
    required this.sessionId,
    this.computedAt,
    this.avgAttendancePercent = 0,
    this.avgMarksObtained = 0,
    this.syllabusCompletionPercent = 0,
    this.activeEnrollments = 0,
    this.raw = const {},
  });

  factory CampusAnalyticsSnapshot.fromJson(Map<String, dynamic> j) {
    final data = j['data'] is Map ? Map<String, dynamic>.from(j['data'] as Map) : <String, dynamic>{};
    double asDouble(dynamic v) => v is num ? v.toDouble() : 0;
    return CampusAnalyticsSnapshot(
      id: j['id'].toString(),
      campusId: j['campus'].toString(),
      sessionId: j['session'].toString(),
      computedAt: _date(j['computed_at']),
      avgAttendancePercent: asDouble(data['avg_attendance_percent']),
      avgMarksObtained: asDouble(data['avg_marks_obtained']),
      syllabusCompletionPercent: asDouble(data['syllabus_completion_percent']),
      activeEnrollments: (data['active_enrollments'] as num?)?.toInt() ?? 0,
      raw: data,
    );
  }
}

// ------------------------------------------------------------
// Derived: mera access
// ------------------------------------------------------------

/// "Main is campus me kaun hoon, aur kya kar sakta hoon."
///
/// ⚠️ Ye abhi CLIENT side pe derive hota hai — staff profile, class-teacher
/// binding, subject-teacher binding aur enrollment, chaaron list endpoints
/// se. Jab backend pe `accessctl` wire ho jaaye aur `GET /access/me/` live
/// ho jaaye, to `CampusService.loadAccess()` ko sirf usi ek call se replace
/// karna hai — ye class aur saari screens waise ki waisi rahengi.
///
/// Aur ye sirf UI ke liye hai. Server har write pe dobara check karta hai;
/// yahan button chhupana security nahi, sirf achhi UX hai.
class CampusAccess {
  final Campus campus;
  final AcademicSession? currentSession;
  final StaffProfile? staff;

  /// Jin sections ka main class teacher hoon.
  final Set<String> classTeacherSectionIds;

  /// (sectionId, subjectId) pairs jinpe main approved subject teacher hoon.
  final Set<String> subjectTeacherKeys;

  /// Jin sections me main student ke roop me enrolled hoon.
  final List<StudentEnrollment> myEnrollments;

  const CampusAccess({
    required this.campus,
    this.currentSession,
    this.staff,
    this.classTeacherSectionIds = const {},
    this.subjectTeacherKeys = const {},
    this.myEnrollments = const [],
  });

  static String subjectKey(String sectionId, String subjectId) => '$sectionId::$subjectId';

  CampusRole get role {
    if (staff != null) return staff!.role;
    if (myEnrollments.isNotEmpty) return CampusRole.student;
    return CampusRole.none;
  }

  bool get isManagement => role.isManagement;
  bool get isStaff => role.isStaff;
  bool get isStudent => role == CampusRole.student;

  // ---- capability checks — screens sirf ye poochhti hain ----

  /// Management ko poore campus ka access hai, isliye har section pass.
  /// Class teacher ko sirf apni section ka. Yahi ek jagah hai jahan ye
  /// rule likha hai — har screen me dobara nahi.
  bool canManageSection(String sectionId) =>
      isManagement || classTeacherSectionIds.contains(sectionId);

  /// Attendance sirf us subject ka mark ho sakta hai jiska teacher ho —
  /// ya class teacher ho to apni section ka koi bhi subject.
  bool canMarkAttendance(String sectionId, String? subjectId) {
    if (isManagement) return true;
    if (classTeacherSectionIds.contains(sectionId)) return true;
    if (subjectId == null) return false;
    return subjectTeacherKeys.contains(subjectKey(sectionId, subjectId));
  }

  bool canPostNoticeForSection(String sectionId) => canManageSection(sectionId);
  bool get canPostCampusNotice => isManagement;
  bool get canManageStaff => role == CampusRole.admin;
  bool get canViewCampusAnalytics => isManagement;

  /// "Main" (logged-in user) ka apna `login.User` id — na `Campus`, na
  /// `CampusAccess` khud ise seedha carry karta (server `request.user` se
  /// derive karta hai), isliye yahan staff row ya apni hi enrollment se
  /// nikala hai. Digital ID card jaisi "apna record banao" cheezon ke liye
  /// chahiye, jahan `user` field client ko khud bharna padta hai (§19).
  /// Pure parent (na staff na student) ke liye `null` — parent dashboard
  /// abhi is app ka hissa nahi hai.
  String? get myUserId => staff?.userId ?? (myEnrollments.isNotEmpty ? myEnrollments.first.studentId : null);

  /// Structural setup — sessions/departments/classes/sections/subjects/rooms.
  /// Backend §13: admin aur principal-HOD dono ko same treat karta hai
  /// (`is_campus_admin_or_principal`), isliye `isManagement` hi sahi check
  /// hai — `canManageStaff` jaanbujh kar sirf `admin` tak seemit hai, ye
  /// alag hai.
  bool get canManageCampusSetup => isManagement;

  /// Staff/enrollment/parent-link add karne se pehle ye bhi check karo —
  /// campus jab tak platform-approved nahi hai, ye teenon 403 dete hain
  /// (creator ka apna pehla staff row iska apvaad hai, wo already ban chuka
  /// hota hai `POST /campuses/` ke waqt). Structural setup (sessions/
  /// classes/sections/subjects/rooms) is check se azaad hai — waha gate
  /// nahi lagta.
  bool get canGrowMembership => canManageCampusSetup && campus.isApproved;
  bool get canCreateAssignment => role.canTeach || isManagement;
  bool get canScheduleLiveClass => role.canTeach || isManagement;

  /// Jin sections pe mujhe kuch na kuch access hai — hub screen isi list
  /// se cards banata hai.
  Set<String> get accessibleSectionIds => {
        ...classTeacherSectionIds,
        ...subjectTeacherKeys.map((k) => k.split('::').first),
        ...myEnrollments.map((e) => e.sectionId),
      };
}