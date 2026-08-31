// lib/liveclass/models/liveclass_models.dart
//
// Dart models for the `liveclass` Django app (see urls.py / serializers.py).
// Every class mirrors its serializer 1:1 — same field names, same
// read-only-vs-writable split. `fromJson` parses what the API returns;
// `toJson` (only present on writable models) builds the body for
// POST/PUT/PATCH — it deliberately omits server-controlled fields
// (teacher, student, sender, uploaded_by, created_by, status, etc.)
// exactly like the serializers' `read_only_fields` do.

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
DateTime? _dt(dynamic v) => v == null ? null : DateTime.parse(v as String);

// FIX (timezone audit): every `toJson()` below that sends a full instant
// (as opposed to a plain calendar date, which stays date-only and has no
// zone ambiguity) used to call `.toIso8601String()` directly on whatever
// `DateTime` it was handed. That's safe for a value that was itself
// parsed from the API (already UTC-aware, via `_dt()`/`DateTime.parse()`
// on the 'Z'-suffixed strings Django returns) — but several screens build
// these DateTimes locally from date/time pickers instead (see e.g. the
// ad-hoc session form in sessions_list_screen.dart), which produces a
// LOCAL, non-UTC `DateTime`. `DateTime.toIso8601String()` on a local
// instance omits any zone/offset suffix entirely, so the backend — which
// (per Django's `USE_TZ=True`) interprets a bare, offset-less timestamp
// in the *server's* configured timezone, not the sender's — could file
// the session under a shifted instant with no error raised anywhere.
// `.toUtc()` first makes this correct (and a harmless no-op) regardless
// of which of those two cases a given `DateTime` came from.
String _instantJson(DateTime d) => d.toUtc().toIso8601String();
double _num(dynamic v) => v == null ? 0 : (v is num ? v.toDouble() : double.parse(v.toString()));
double? _numN(dynamic v) => v == null ? null : (v is num ? v.toDouble() : double.parse(v.toString()));
int _int(dynamic v) => v == null ? 0 : (v is int ? v : int.parse(v.toString()));
int? _intN(dynamic v) => v == null ? null : (v is int ? v : int.parse(v.toString()));

// ---------------------------------------------------------------------------
// 0. USER (MINI) — nested everywhere (teacher/student/sender/uploaded_by/...)
// ---------------------------------------------------------------------------
class UserMini {
  final int id;
  final String username;
  final String fullName;
  final String? profilePicture;

  UserMini({required this.id, required this.username, required this.fullName, this.profilePicture});

  factory UserMini.fromJson(Map<String, dynamic> j) => UserMini(
        id: _int(j['id']),
        username: j['username'] ?? '',
        fullName: j['full_name'] ?? j['username'] ?? '',
        profilePicture: j['profile_picture'],
      );
}

// ---------------------------------------------------------------------------
// 1. CLASSROOM
// ---------------------------------------------------------------------------
class ClassroomType {
  static const individual = 'individual';
  static const organisation = 'organisation';
}

class Classroom {
  final int id;
  final UserMini? teacher;
  final String classroomType;
  final String organisationName;
  final String title;
  final String subject;
  final String description;
  final String language;
  final String? coverImage;
  final bool whiteboardEnabled;
  final bool screenShareEnabled;
  final bool chatEnabled;
  final bool recordingEnabled;
  final int maxParticipants;
  final double ratingAvg;
  final int ratingCount;
  final bool isActive;
  final bool isFlagged;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  Classroom({
    required this.id,
    this.teacher,
    this.classroomType = ClassroomType.individual,
    this.organisationName = '',
    required this.title,
    this.subject = '',
    this.description = '',
    this.language = 'English',
    this.coverImage,
    this.whiteboardEnabled = true,
    this.screenShareEnabled = true,
    this.chatEnabled = true,
    this.recordingEnabled = true,
    this.maxParticipants = 100,
    this.ratingAvg = 0,
    this.ratingCount = 0,
    this.isActive = true,
    this.isFlagged = false,
    this.createdAt,
    this.updatedAt,
  });

  factory Classroom.fromJson(Map<String, dynamic> j) => Classroom(
        id: _int(j['id']),
        teacher: j['teacher'] != null ? UserMini.fromJson(j['teacher']) : null,
        classroomType: j['classroom_type'] ?? ClassroomType.individual,
        organisationName: j['organisation_name'] ?? '',
        title: j['title'] ?? '',
        subject: j['subject'] ?? '',
        description: j['description'] ?? '',
        language: j['language'] ?? 'English',
        coverImage: j['cover_image'],
        whiteboardEnabled: j['whiteboard_enabled'] ?? true,
        screenShareEnabled: j['screen_share_enabled'] ?? true,
        chatEnabled: j['chat_enabled'] ?? true,
        recordingEnabled: j['recording_enabled'] ?? true,
        maxParticipants: _int(j['max_participants']),
        ratingAvg: _num(j['rating_avg']),
        ratingCount: _int(j['rating_count']),
        isActive: j['is_active'] ?? true,
        isFlagged: j['is_flagged'] ?? false,
        createdAt: _dt(j['created_at']),
        updatedAt: _dt(j['updated_at']),
      );

  // For create/update (multipart if coverImageFile is attached — handled in
  // the api service, not here, since File upload needs FormData).
  Map<String, dynamic> toJson() => {
        'classroom_type': classroomType,
        if (organisationName.isNotEmpty) 'organisation_name': organisationName,
        'title': title,
        'subject': subject,
        'description': description,
        'language': language,
        'whiteboard_enabled': whiteboardEnabled,
        'screen_share_enabled': screenShareEnabled,
        'chat_enabled': chatEnabled,
        'recording_enabled': recordingEnabled,
        'max_participants': maxParticipants,
        'is_active': isActive,
      };
}

/// Response shape of classrooms/{id}/my-pass/
class MyPassStatus {
  final String accessLevel; // owner|admin|active|expired|none (status kept for back-compat)
  final String status;
  final bool hasAccess;
  final bool canViewInternals;
  final bool canEnterClass;
  final DateTime? expiresAt;

  MyPassStatus({
    required this.accessLevel,
    required this.status,
    required this.hasAccess,
    required this.canViewInternals,
    required this.canEnterClass,
    this.expiresAt,
  });

  factory MyPassStatus.fromJson(Map<String, dynamic> j) => MyPassStatus(
        accessLevel: j['access_level'] ?? 'none',
        status: j['status'] ?? 'none',
        hasAccess: j['has_access'] ?? false,
        canViewInternals: j['can_view_internals'] ?? false,
        canEnterClass: j['can_enter_class'] ?? false,
        expiresAt: _dt(j['expires_at']),
      );
}

/// Response shape of classrooms/{id}/stats/
class ClassroomStats {
  final double ratingAvg;
  final int ratingCount;
  final int enrolledCount;
  final String weeklyTiming;
  final List<ClassHoliday> upcomingHolidays;

  ClassroomStats({
    required this.ratingAvg,
    required this.ratingCount,
    required this.enrolledCount,
    required this.weeklyTiming,
    required this.upcomingHolidays,
  });

  factory ClassroomStats.fromJson(Map<String, dynamic> j) => ClassroomStats(
        ratingAvg: _num(j['rating_avg']),
        ratingCount: _int(j['rating_count']),
        enrolledCount: _int(j['enrolled_count']),
        weeklyTiming: j['weekly_timing'] ?? '',
        upcomingHolidays: (j['upcoming_holidays'] as List? ?? [])
            .map((e) => ClassHoliday.fromJson(e))
            .toList(),
      );
}

// ---------------------------------------------------------------------------
// 2. SCHEDULE
// ---------------------------------------------------------------------------
class RecurrenceType {
  static const specificDate = 'specific_date';
  static const daily = 'daily';
  static const weekday = 'weekday';
  static const weekend = 'weekend';
  static const weekly = 'weekly';
  static const monthly = 'monthly';
  static const yearly = 'yearly';
}

class ClassSchedule {
  final int id;
  final int classroomId;
  final String recurrenceType;
  final List<String> daysOfWeek; // used when recurrenceType == weekly
  final int? dayOfMonth; // used when recurrenceType == monthly
  final DateTime startDate;
  final DateTime? endDate;
  final String startTime; // "HH:mm:ss"
  final int durationMinutes;
  final String timezone;
  final bool isActive;
  final DateTime? createdAt;

  ClassSchedule({
    required this.id,
    required this.classroomId,
    required this.recurrenceType,
    this.daysOfWeek = const [],
    this.dayOfMonth,
    required this.startDate,
    this.endDate,
    required this.startTime,
    this.durationMinutes = 60,
    this.timezone = 'Asia/Kolkata',
    this.isActive = true,
    this.createdAt,
  });

  factory ClassSchedule.fromJson(Map<String, dynamic> j) => ClassSchedule(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        recurrenceType: j['recurrence_type'] ?? '',
        daysOfWeek: (j['days_of_week'] as List? ?? []).map((e) => e.toString()).toList(),
        dayOfMonth: _intN(j['day_of_month']),
        startDate: DateTime.parse(j['start_date']),
        endDate: j['end_date'] != null ? DateTime.parse(j['end_date']) : null,
        startTime: j['start_time'] ?? '',
        durationMinutes: _int(j['duration_minutes']),
        timezone: j['timezone'] ?? 'Asia/Kolkata',
        isActive: j['is_active'] ?? true,
        createdAt: _dt(j['created_at']),
      );

  Map<String, dynamic> toJson() => {
        'classroom': classroomId,
        'recurrence_type': recurrenceType,
        if (recurrenceType == RecurrenceType.weekly) 'days_of_week': daysOfWeek,
        if (recurrenceType == RecurrenceType.monthly) 'day_of_month': dayOfMonth,
        'start_date': startDate.toIso8601String().split('T').first,
        if (endDate != null) 'end_date': endDate!.toIso8601String().split('T').first,
        'start_time': startTime,
        'duration_minutes': durationMinutes,
        'timezone': timezone,
        'is_active': isActive,
      };
}

// ---------------------------------------------------------------------------
// 3. SESSION
// ---------------------------------------------------------------------------
class SessionStatus {
  static const scheduled = 'scheduled';
  static const live = 'live';
  static const completed = 'completed';
  static const cancelled = 'cancelled';
}

class ClassSession {
  final int id;
  final int classroomId;
  final String classroomTitle;
  final int? scheduleId;
  final String roomId;
  final DateTime scheduledStart;
  final DateTime scheduledEnd;
  final DateTime? actualStart;
  final DateTime? actualEnd;
  final String status;
  final String recordingUrl;
  final bool isRecording;
  final bool isJoinable;
  final DateTime? createdAt;

  ClassSession({
    required this.id,
    required this.classroomId,
    this.classroomTitle = '',
    this.scheduleId,
    this.roomId = '',
    required this.scheduledStart,
    required this.scheduledEnd,
    this.actualStart,
    this.actualEnd,
    this.status = SessionStatus.scheduled,
    this.recordingUrl = '',
    this.isRecording = false,
    this.isJoinable = false,
    this.createdAt,
  });

  factory ClassSession.fromJson(Map<String, dynamic> j) => ClassSession(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        classroomTitle: j['classroom_title'] ?? '',
        scheduleId: _intN(j['schedule']),
        roomId: j['room_id'] ?? '',
        scheduledStart: DateTime.parse(j['scheduled_start']),
        scheduledEnd: DateTime.parse(j['scheduled_end']),
        actualStart: _dt(j['actual_start']),
        actualEnd: _dt(j['actual_end']),
        status: j['status'] ?? SessionStatus.scheduled,
        recordingUrl: j['recording_url'] ?? '',
        isRecording: j['is_recording'] ?? false,
        isJoinable: j['is_joinable'] ?? false,
        createdAt: _dt(j['created_at']),
      );

  Map<String, dynamic> toJson() => {
        'classroom': classroomId,
        if (scheduleId != null) 'schedule': scheduleId,
        'scheduled_start': _instantJson(scheduledStart),
        'scheduled_end': _instantJson(scheduledEnd),
        'status': status,
      };
}

/// Response shape of sessions/{id}/join/ and sessions/{id}/token/
class SessionJoinResult {
  final String roomId;
  final int? participantId; // null on token()
  final String role; // "host" | "student"
  final String livekitRole;
  final String livekitUrl;
  final String livekitToken;
  final bool waitlisted; // true when API returned 202 (session full)
  // NOTE (fix — simplification): populated only when this result came from
  // ClassroomApi.startOrJoin() and the backend had to create a fresh
  // ad-hoc session to satisfy it (see classrooms/{id}/start-or-join/'s
  // 201 response) — null for a plain sessions/{id}/join() or token() call,
  // and for a startOrJoin() call that found an existing session to join.
  final ClassSession? session;
  final bool startedNew;

  SessionJoinResult({
    required this.roomId,
    this.participantId,
    required this.role,
    required this.livekitRole,
    required this.livekitUrl,
    required this.livekitToken,
    this.waitlisted = false,
    this.session,
    this.startedNew = false,
  });

  factory SessionJoinResult.fromJson(Map<String, dynamic> j) => SessionJoinResult(
        roomId: j['room_id'] ?? '',
        participantId: _intN(j['participant_id']),
        role: j['role'] ?? 'student',
        livekitRole: j['livekit_role'] ?? '',
        livekitUrl: j['livekit_url'] ?? '',
        livekitToken: j['livekit_token'] ?? '',
        session: j['session'] == null ? null : ClassSession.fromJson(j['session']),
        startedNew: j['started_new'] ?? false,
      );
}

// ---------------------------------------------------------------------------
// 4. PASS
// ---------------------------------------------------------------------------
class PassType {
  static const free = 'free';
  static const daily = 'daily';
  static const weekly = 'weekly';
  static const monthly = 'monthly';
  static const yearly = 'yearly';
}

class ClassPass {
  final int id;
  final int classroomId;
  final String passType;
  final String title;
  final double price; // in coins
  final int validityDays;
  final int? maxClasses;
  final bool isActive;
  final DateTime? createdAt;

  // NEW (Pass 14 frontend catch-up §1.3) — "allow gifting" gate. The
  // frontend doc flags this as "if backend gates gifting per-pass —
  // confirm" (§0 module map, pass_management_screen.dart row); modeled
  // here defaulting to true (opt-out) rather than false (opt-in) only
  // because that matches this codebase's general bias of defaulting new
  // boolean flags to whatever preserves prior behavior for existing rows
  // — confirm the actual server-side default against ClassPass.models.py
  // before relying on it, and flip the default here if wrong.
  final bool allowGifting;

  ClassPass({
    required this.id,
    required this.classroomId,
    required this.passType,
    this.title = '',
    this.price = 0,
    required this.validityDays,
    this.maxClasses,
    this.isActive = true,
    this.createdAt,
    this.allowGifting = true,
  });

  factory ClassPass.fromJson(Map<String, dynamic> j) => ClassPass(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        passType: j['pass_type'] ?? '',
        title: j['title'] ?? '',
        price: _num(j['price']),
        validityDays: _int(j['validity_days']),
        maxClasses: _intN(j['max_classes']),
        isActive: j['is_active'] ?? true,
        createdAt: _dt(j['created_at']),
        allowGifting: j['allow_gifting'] ?? true,
      );

  Map<String, dynamic> toJson() => {
        'classroom': classroomId,
        'pass_type': passType,
        'title': title,
        'price': price,
        'validity_days': validityDays,
        if (maxClasses != null) 'max_classes': maxClasses,
        'is_active': isActive,
        'allow_gifting': allowGifting,
      };

  ClassPass copyWith({bool? allowGifting}) => ClassPass(
        id: id,
        classroomId: classroomId,
        passType: passType,
        title: title,
        price: price,
        validityDays: validityDays,
        maxClasses: maxClasses,
        isActive: isActive,
        createdAt: createdAt,
        allowGifting: allowGifting ?? this.allowGifting,
      );
}

// ---------------------------------------------------------------------------
// 5. PASS PURCHASE (read-only from the client's point of view)
// ---------------------------------------------------------------------------
class PassPurchase {
  final int id;
  final UserMini student;
  final int classPassId;
  final String classroomTitle;
  final String classPassTitle;
  final String classPassType;
  final int? maxClasses; // denormalized from the ClassPass, same pattern as classPassTitle/classPassType
  final int? couponId;
  final String? couponCode;
  final String paymentMethod;
  final double amountPaid;
  final int coinsSpent;
  final String transactionId;
  final String status; // pending|success|failed|refunded
  final DateTime purchasedAt;
  final DateTime expiresAt;
  final int classesAttended;
  final bool isActive;
  final bool isValid;
  // NOTE (fix — escrow fields were never parsed): the backend's per-day
  // escrow design (see PassPurchase in models.py) puts coinsSpent into
  // escrow up front and releases it to the teacher one taught day at a
  // time — PassPurchaseSerializer already exposes exactly what's still
  // "at risk" (remainingBalance) plus the audit trail behind it
  // (coinsReleased, perDayRate, lastChargeDate), but this model dropped
  // all four on the floor, so no screen could ever show a student/teacher
  // anything but the flat coinsSpent total — wrong ever since a purchase
  // has at least one taught day charged against it.
  final int remainingBalance;
  final int coinsReleased;
  final double perDayRate;
  final DateTime? lastChargeDate;
  // NEW (Pass 15 frontend catch-up §1.8) — auto-renew. ⚠️ ARCHITECTURE
  // SKELETON, same caveat as PassGift/SessionEngagementReport further
  // down: Pass 15 was never written up in the backend doc's own §2–§6.
  // `renewedInto` in particular is flagged by the frontend doc as
  // "confirm this field exists" — kept nullable/best-effort so a missing
  // key just parses to null rather than throwing.
  final bool autoRenew;
  final int? renewedFrom; // this purchase's id, if it IS a renewal result
  final int? renewedInto; // the purchase that superseded this one, if any
  final DateTime? renewalFailedAt;
  // NEW (Pass 14 frontend catch-up §1.3) — gift back-reference, so a
  // purchase created by claiming a PassGift can show "Gifted to you by
  // X" instead of the normal purchase-flow copy. ⚠️ Field existence
  // unconfirmed against PassPurchaseSerializer — confirm before relying
  // on it; null/absent just means "not a gifted purchase" either way.
  final int? giftId;
  final UserMini? giftedBy;

  PassPurchase({
    required this.id,
    required this.student,
    required this.classPassId,
    this.classroomTitle = '',
    this.classPassTitle = '',
    this.classPassType = '',
    this.maxClasses,
    this.couponId,
    this.couponCode,
    required this.paymentMethod,
    required this.amountPaid,
    required this.coinsSpent,
    this.transactionId = '',
    required this.status,
    required this.purchasedAt,
    required this.expiresAt,
    this.classesAttended = 0,
    this.isActive = true,
    this.isValid = false,
    this.remainingBalance = 0,
    this.coinsReleased = 0,
    this.perDayRate = 0,
    this.lastChargeDate,
    this.autoRenew = false,
    this.renewedFrom,
    this.renewedInto,
    this.renewalFailedAt,
    this.giftId,
    this.giftedBy,
  });

  factory PassPurchase.fromJson(Map<String, dynamic> j) => PassPurchase(
        id: _int(j['id']),
        student: UserMini.fromJson(j['student']),
        classPassId: _int(j['class_pass']),
        classroomTitle: j['classroom_title'] ?? '',
        classPassTitle: j['class_pass_title'] ?? '',
        classPassType: j['class_pass_type'] ?? '',
        maxClasses: _intN(j['class_pass_max_classes']),
        couponId: _intN(j['coupon']),
        couponCode: j['coupon_code'],
        paymentMethod: j['payment_method'] ?? '',
        amountPaid: _num(j['amount_paid']),
        coinsSpent: _int(j['coins_spent']),
        transactionId: j['transaction_id'] ?? '',
        status: j['status'] ?? '',
        purchasedAt: DateTime.parse(j['purchased_at']),
        expiresAt: DateTime.parse(j['expires_at']),
        classesAttended: _int(j['classes_attended']),
        isActive: j['is_active'] ?? true,
        isValid: j['is_valid'] ?? false,
        // NOTE: remainingBalance defaults to coinsSpent (not 0) when the
        // backend response happens to omit it — an old cached response or
        // a stale API version shouldn't make a screen show "0 left" (and
        // therefore hide a still-cancellable pass) for a purchase that
        // hasn't actually had anything released yet.
        remainingBalance: _intN(j['remaining_balance']) ?? _int(j['coins_spent']),
        coinsReleased: _int(j['coins_released']),
        perDayRate: _num(j['per_day_rate']),
        lastChargeDate: j['last_charge_date'] != null ? DateTime.parse(j['last_charge_date']) : null,
        autoRenew: j['auto_renew'] ?? false,
        renewedFrom: _intN(j['renewed_from']),
        renewedInto: _intN(j['renewed_into']),
        renewalFailedAt: _dt(j['renewal_failed_at']),
        giftId: _intN(j['gift']),
        giftedBy: j['gifted_by'] != null ? UserMini.fromJson(j['gifted_by']) : null,
      );
}

// ---------------------------------------------------------------------------
// 5B. CLASS JOIN REQUEST
// ---------------------------------------------------------------------------
class JoinRequestStatus {
  static const pending = 'pending';
  static const accepted = 'accepted';
  static const rejected = 'rejected';
  static const cancelled = 'cancelled';
}

class ClassJoinRequest {
  final int id;
  final int classroomId;
  final String classroomTitle;
  final int classPassId;
  final String classPassTitle;
  final double classPassPrice;
  final UserMini student;
  final String couponCode;
  final String message;
  final String status;
  final String decisionNote;
  final UserMini? decidedBy;
  final DateTime? decidedAt;
  final int? passPurchaseId;
  final DateTime requestedAt;

  ClassJoinRequest({
    required this.id,
    required this.classroomId,
    this.classroomTitle = '',
    required this.classPassId,
    this.classPassTitle = '',
    this.classPassPrice = 0,
    required this.student,
    this.couponCode = '',
    this.message = '',
    this.status = JoinRequestStatus.pending,
    this.decisionNote = '',
    this.decidedBy,
    this.decidedAt,
    this.passPurchaseId,
    required this.requestedAt,
  });

  factory ClassJoinRequest.fromJson(Map<String, dynamic> j) => ClassJoinRequest(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        classroomTitle: j['classroom_title'] ?? '',
        classPassId: _int(j['class_pass']),
        classPassTitle: j['class_pass_title'] ?? '',
        classPassPrice: _num(j['class_pass_price']),
        student: UserMini.fromJson(j['student']),
        couponCode: j['coupon_code'] ?? '',
        message: j['message'] ?? '',
        status: j['status'] ?? JoinRequestStatus.pending,
        decisionNote: j['decision_note'] ?? '',
        decidedBy: j['decided_by'] != null ? UserMini.fromJson(j['decided_by']) : null,
        decidedAt: _dt(j['decided_at']),
        passPurchaseId: _intN(j['pass_purchase']),
        requestedAt: DateTime.parse(j['requested_at']),
      );

  // POST body to create a join request
  static Map<String, dynamic> createBody({
    required int classroomId,
    required int classPassId,
    String couponCode = '',
    String message = '',
  }) =>
      {
        'classroom': classroomId,
        'class_pass': classPassId,
        if (couponCode.isNotEmpty) 'coupon_code': couponCode,
        if (message.isNotEmpty) 'message': message,
      };
}

// ---------------------------------------------------------------------------
// 6. SESSION PARTICIPANT
// ---------------------------------------------------------------------------
class ParticipantRole {
  static const host = 'host';
  static const student = 'student';
}

class SessionParticipant {
  final int id;
  final int sessionId;
  final UserMini user;
  final String role;
  final DateTime joinedAt;
  final DateTime? leftAt;
  final bool handRaised;
  final DateTime? handRaisedAt;

  SessionParticipant({
    required this.id,
    required this.sessionId,
    required this.user,
    required this.role,
    required this.joinedAt,
    this.leftAt,
    this.handRaised = false,
    this.handRaisedAt,
  });

  factory SessionParticipant.fromJson(Map<String, dynamic> j) => SessionParticipant(
        id: _int(j['id']),
        sessionId: _int(j['session']),
        user: UserMini.fromJson(j['user']),
        role: j['role'] ?? ParticipantRole.student,
        joinedAt: DateTime.parse(j['joined_at']),
        leftAt: _dt(j['left_at']),
        handRaised: j['hand_raised'] ?? false,
        handRaisedAt: _dt(j['hand_raised_at']),
      );
}

// ---------------------------------------------------------------------------
// 6B. BREAKOUT ROOM
//
// Public so it can be shared between liveclass_api_service.dart (parsing)
// and live_session_screen.dart (display) — was previously a private
// `_BreakoutRoom` class living only inside live_session_screen.dart, which
// meant `LiveClassApi.breakoutRooms` literally couldn't return it (Dart
// privacy is per-file, not shared across files that both happen to be
// "the liveclass feature"). Shape matches
// `sessions/{id}/breakout/`'s response: `room` + a flat list of
// participant identity strings (str(user_id) — same convention as the
// LiveKit identity used everywhere else in this app, e.g.
// `_remoteCameraTrack(p.user.id.toString())`).
// ---------------------------------------------------------------------------
class BreakoutRoom {
  final int roomNumber;
  final List<String> participantIdentities;

  BreakoutRoom({required this.roomNumber, required this.participantIdentities});

  factory BreakoutRoom.fromJson(Map<String, dynamic> json) => BreakoutRoom(
        roomNumber: _int(json['room']),
        participantIdentities: (json['participant_ids'] as List? ?? []).map((e) => e.toString()).toList(),
      );
}

// ---------------------------------------------------------------------------
// 7. CLASS MATERIAL
// ---------------------------------------------------------------------------
class MaterialType {
  static const pdf = 'pdf';
  static const ppt = 'ppt';
  static const doc = 'doc';
  static const image = 'image';
  static const video = 'video';
  static const link = 'link';
}

class ClassMaterial {
  final int id;
  final int classroomId;
  final int? sessionId;
  final UserMini uploadedBy;
  final String title;
  final String materialType;
  final String? file;
  final String externalLink;
  final DateTime uploadedAt;

  ClassMaterial({
    required this.id,
    required this.classroomId,
    this.sessionId,
    required this.uploadedBy,
    required this.title,
    required this.materialType,
    this.file,
    this.externalLink = '',
    required this.uploadedAt,
  });

  factory ClassMaterial.fromJson(Map<String, dynamic> j) => ClassMaterial(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        sessionId: _intN(j['session']),
        uploadedBy: UserMini.fromJson(j['uploaded_by']),
        title: j['title'] ?? '',
        materialType: j['material_type'] ?? '',
        file: j['file'],
        externalLink: j['external_link'] ?? '',
        uploadedAt: DateTime.parse(j['uploaded_at']),
      );
}

// ---------------------------------------------------------------------------
// 8. CHAT MESSAGE
// ---------------------------------------------------------------------------
class ChatMessage {
  final int id;
  final int sessionId;
  final UserMini sender;
  final String message;
  final DateTime sentAt;
  final bool isDeleted;
  // FIX (Pass 12/13 frontend catch-up): backend's `ChatMessageSerializer`
  // gained `reaction_counts`/`my_reaction` in Pass 12 (new `ChatReaction`
  // model, upsertable one-row-per-(message,user), same "changing your
  // answer" shape as `PollResponse`), and `ChatMessage` itself gained
  // `is_pinned`/`pinned_by`/`pinned_at` in Pass 13 (at most one pinned
  // message per session, enforced by `ChatMessageViewSet.pin()` unpinning
  // whichever was pinned before in the same call). See backend doc §11
  // Pass 12/13 — this is the exact, confirmed field set (unlike the
  // Pass 14/15 models further down, which are architecture skeletons).
  final Map<String, int> reactionCounts; // emoji -> count
  final String? myReaction; // emoji the CURRENT user picked, or null
  final bool isPinned;
  final UserMini? pinnedBy;
  final DateTime? pinnedAt;

  ChatMessage({
    required this.id,
    required this.sessionId,
    required this.sender,
    required this.message,
    required this.sentAt,
    this.isDeleted = false,
    this.reactionCounts = const {},
    this.myReaction,
    this.isPinned = false,
    this.pinnedBy,
    this.pinnedAt,
  });

  factory ChatMessage.fromJson(Map<String, dynamic> j) => ChatMessage(
        id: _int(j['id']),
        sessionId: _int(j['session']),
        sender: UserMini.fromJson(j['sender']),
        message: j['message'] ?? '',
        sentAt: DateTime.parse(j['sent_at']),
        isDeleted: j['is_deleted'] ?? false,
        reactionCounts: (j['reaction_counts'] as Map? ?? {})
            .map((k, v) => MapEntry(k.toString(), _int(v))),
        myReaction: j['my_reaction'],
        isPinned: j['is_pinned'] ?? false,
        pinnedBy: j['pinned_by'] != null ? UserMini.fromJson(j['pinned_by']) : null,
        pinnedAt: _dt(j['pinned_at']),
      );

  /// Convenience for optimistic local updates (see live_session_screen.dart's
  /// `_reactToChat`/`_removeChatReaction`/pin handlers) — returns a copy with
  /// only the given fields swapped, so a tap can update the UI immediately
  /// and get corrected on the next `chat.reaction`/`chat.pinned` WS event or
  /// `_loadChat` refresh.
  ChatMessage copyWith({
    Map<String, int>? reactionCounts,
    Object? myReaction = _unset,
    bool? isPinned,
    Object? pinnedBy = _unset,
    Object? pinnedAt = _unset,
  }) =>
      ChatMessage(
        id: id,
        sessionId: sessionId,
        sender: sender,
        message: message,
        sentAt: sentAt,
        isDeleted: isDeleted,
        reactionCounts: reactionCounts ?? this.reactionCounts,
        myReaction: identical(myReaction, _unset) ? this.myReaction : myReaction as String?,
        isPinned: isPinned ?? this.isPinned,
        pinnedBy: identical(pinnedBy, _unset) ? this.pinnedBy : pinnedBy as UserMini?,
        pinnedAt: identical(pinnedAt, _unset) ? this.pinnedAt : pinnedAt as DateTime?,
      );
}

/// Sentinel so `copyWith` above can tell "not passed" apart from
/// "explicitly passed null" (needed to clear `myReaction`/`pinnedBy`/`pinnedAt`).
const Object _unset = Object();

// ---------------------------------------------------------------------------
// 9. LIVE POLL + RESPONSE
// ---------------------------------------------------------------------------
class LivePoll {
  final int id;
  final int sessionId;
  final int createdById;
  final String question;
  final List<String> options;
  final bool isActive;
  final DateTime createdAt;
  final DateTime? closedAt;
  final Map<int, int> resultCounts;

  LivePoll({
    required this.id,
    required this.sessionId,
    required this.createdById,
    required this.question,
    required this.options,
    this.isActive = true,
    required this.createdAt,
    this.closedAt,
    this.resultCounts = const {},
  });

  factory LivePoll.fromJson(Map<String, dynamic> j) => LivePoll(
        id: _int(j['id']),
        sessionId: _int(j['session']),
        createdById: _int(j['created_by']),
        question: j['question'] ?? '',
        options: (j['options'] as List? ?? []).map((e) => e.toString()).toList(),
        isActive: j['is_active'] ?? true,
        createdAt: DateTime.parse(j['created_at']),
        closedAt: _dt(j['closed_at']),
        resultCounts: (j['result_counts'] as Map? ?? {})
            .map((k, v) => MapEntry(int.parse(k.toString()), _int(v))),
      );

  Map<String, dynamic> toJson() => {
        'session': sessionId,
        'question': question,
        'options': options,
      };
}

class PollResponse {
  final int id;
  final int pollId;
  final UserMini student;
  final int selectedOptionIndex;
  final DateTime answeredAt;

  PollResponse({
    required this.id,
    required this.pollId,
    required this.student,
    required this.selectedOptionIndex,
    required this.answeredAt,
  });

  factory PollResponse.fromJson(Map<String, dynamic> j) => PollResponse(
        id: _int(j['id']),
        pollId: _int(j['poll']),
        student: UserMini.fromJson(j['student']),
        selectedOptionIndex: _int(j['selected_option_index']),
        answeredAt: DateTime.parse(j['answered_at']),
      );
}

// ---------------------------------------------------------------------------
// 9B. QUICK-POLL TEMPLATES (Pass 13) — classroom-scoped, CRUD gated behind
// `_can_manage_classroom` on the backend (same boundary as Assignment/
// Notice/ClassHoliday). `LivePollViewSet.quick_create()` fires a saved
// template into a live session in one call — see `PollApi.quickCreate`.
// ---------------------------------------------------------------------------
class PollTemplate {
  final int id;
  final int classroomId;
  final UserMini? createdBy;
  final String question;
  final List<String> options;
  final DateTime? createdAt;

  PollTemplate({
    required this.id,
    required this.classroomId,
    this.createdBy,
    required this.question,
    required this.options,
    this.createdAt,
  });

  factory PollTemplate.fromJson(Map<String, dynamic> j) => PollTemplate(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        createdBy: j['created_by'] != null ? UserMini.fromJson(j['created_by']) : null,
        question: j['question'] ?? '',
        options: (j['options'] as List? ?? []).map((e) => e.toString()).toList(),
        createdAt: _dt(j['created_at']),
      );

  // For create/update — mirrors _CreatePollSheet's option-list UI pattern
  // (live_session_screen.dart) so both flows can share the same widget.
  Map<String, dynamic> toJson() => {
        'classroom': classroomId,
        'question': question,
        'options': options,
      };
}

/// Response shape of GET sessions/{id}/unread/ — `{"chat": N, "polls": N}`.
/// Deliberately a real DB count against the caller's `SessionReadState`
/// watermark, not derived from the (capped, 50 events/15 min) WS replay
/// buffer — see backend doc §11 Pass 13.
class SessionUnreadCount {
  final int chat;
  final int polls;

  SessionUnreadCount({required this.chat, required this.polls});

  factory SessionUnreadCount.fromJson(Map<String, dynamic> j) => SessionUnreadCount(
        chat: _int(j['chat']),
        polls: _int(j['polls']),
      );
}

// ---------------------------------------------------------------------------
// 10. ASSIGNMENT + SUBMISSION
// ---------------------------------------------------------------------------
class Assignment {
  final int id;
  final int classroomId;
  final int? sessionId;
  final String title;
  final String description;
  final String? attachment;
  final DateTime dueDate;
  final int maxScore;
  final DateTime createdAt;

  Assignment({
    required this.id,
    required this.classroomId,
    this.sessionId,
    required this.title,
    this.description = '',
    this.attachment,
    required this.dueDate,
    this.maxScore = 100,
    required this.createdAt,
  });

  factory Assignment.fromJson(Map<String, dynamic> j) => Assignment(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        sessionId: _intN(j['session']),
        title: j['title'] ?? '',
        description: j['description'] ?? '',
        attachment: j['attachment'],
        dueDate: DateTime.parse(j['due_date']),
        maxScore: _int(j['max_score']),
        createdAt: DateTime.parse(j['created_at']),
      );

  Map<String, dynamic> toJson() => {
        'classroom': classroomId,
        if (sessionId != null) 'session': sessionId,
        'title': title,
        'description': description,
        'due_date': _instantJson(dueDate),
        'max_score': maxScore,
      };
}

class AssignmentSubmission {
  final int id;
  final int assignmentId;
  final UserMini student;
  final String? file;
  final DateTime submittedAt;
  final int? score;
  final String feedback;
  final DateTime? gradedAt;
  final bool isLate;

  AssignmentSubmission({
    required this.id,
    required this.assignmentId,
    required this.student,
    this.file,
    required this.submittedAt,
    this.score,
    this.feedback = '',
    this.gradedAt,
    this.isLate = false,
  });

  factory AssignmentSubmission.fromJson(Map<String, dynamic> j) => AssignmentSubmission(
        id: _int(j['id']),
        assignmentId: _int(j['assignment']),
        student: UserMini.fromJson(j['student']),
        file: j['file'],
        submittedAt: DateTime.parse(j['submitted_at']),
        score: _intN(j['score']),
        feedback: j['feedback'] ?? '',
        gradedAt: _dt(j['graded_at']),
        isLate: j['is_late'] ?? false,
      );
}

// ---------------------------------------------------------------------------
// 11. CLASSROOM REVIEW
// ---------------------------------------------------------------------------
class ClassroomReview {
  final int id;
  final int classroomId;
  final UserMini student;
  final int rating; // 1-5
  final String comment;
  final DateTime createdAt;

  ClassroomReview({
    required this.id,
    required this.classroomId,
    required this.student,
    required this.rating,
    this.comment = '',
    required this.createdAt,
  });

  factory ClassroomReview.fromJson(Map<String, dynamic> j) => ClassroomReview(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        student: UserMini.fromJson(j['student']),
        rating: _int(j['rating']),
        comment: j['comment'] ?? '',
        createdAt: DateTime.parse(j['created_at']),
      );

  Map<String, dynamic> toJson() => {
        'classroom': classroomId,
        'rating': rating,
        'comment': comment,
      };
}

// ---------------------------------------------------------------------------
// 11B. WISHLIST
// ---------------------------------------------------------------------------
class ClassroomWishlistItem {
  final int id;
  final Classroom classroom;
  final DateTime createdAt;

  ClassroomWishlistItem({required this.id, required this.classroom, required this.createdAt});

  factory ClassroomWishlistItem.fromJson(Map<String, dynamic> j) => ClassroomWishlistItem(
        id: _int(j['id']),
        classroom: Classroom.fromJson(j['classroom']),
        createdAt: DateTime.parse(j['created_at']),
      );
}

// ---------------------------------------------------------------------------
// 11C. CLASSROOM REPORT
// ---------------------------------------------------------------------------
class ReportReason {
  static const scam = 'scam';
  static const notDelivering = 'not_delivering';
  static const inappropriate = 'inappropriate';
  static const other = 'other';
}

class ReportStatus {
  static const pending = 'pending';
  static const reviewed = 'reviewed';
  static const actionTaken = 'action_taken';
  static const dismissed = 'dismissed';
}

class ClassroomReport {
  final int id;
  final int classroomId;
  final String classroomTitle;
  final UserMini reportedBy;
  final String reason;
  final String description;
  final String status;
  final int? reviewedById;
  final String adminNote;
  final DateTime? reviewedAt;
  final DateTime createdAt;

  ClassroomReport({
    required this.id,
    required this.classroomId,
    this.classroomTitle = '',
    required this.reportedBy,
    required this.reason,
    this.description = '',
    this.status = ReportStatus.pending,
    this.reviewedById,
    this.adminNote = '',
    this.reviewedAt,
    required this.createdAt,
  });

  factory ClassroomReport.fromJson(Map<String, dynamic> j) => ClassroomReport(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        classroomTitle: j['classroom_title'] ?? '',
        reportedBy: UserMini.fromJson(j['reported_by']),
        reason: j['reason'] ?? '',
        description: j['description'] ?? '',
        status: j['status'] ?? ReportStatus.pending,
        reviewedById: _intN(j['reviewed_by']),
        adminNote: j['admin_note'] ?? '',
        reviewedAt: _dt(j['reviewed_at']),
        createdAt: DateTime.parse(j['created_at']),
      );

  Map<String, dynamic> toJson() => {
        'classroom': classroomId,
        'reason': reason,
        'description': description,
      };
}

// ---------------------------------------------------------------------------
// 11B. CHAT MESSAGE REPORT (Pass 14) — ⚠️ ARCHITECTURE SKELETON, same
// caveat as PassGift/NotificationPreference: Pass 14 was never written up
// in the backend doc's own §2–§6. Shape below mirrors ClassroomReport
// above (same reason/description/status/adminNote/reviewedBy shape) just
// scoped to a chat message instead of a classroom, per the frontend doc
// §1.4 — confirm exact field names against ChatMessageReportSerializer
// before trusting fromJson on real data. Reuses ReportStatus above (same
// pending/reviewed/action_taken/dismissed set) since the change log gives
// no indication message reports have a different status vocabulary.
// ---------------------------------------------------------------------------
class ChatMessageReport {
  final int id;
  final int messageId;
  final String messagePreview; // denormalized snippet, if backend sends one
  final int sessionId;
  final UserMini reportedBy;
  final String reason;
  final String description;
  final String status;
  final int? reviewedById;
  final String adminNote;
  final DateTime? reviewedAt;
  final DateTime createdAt;

  ChatMessageReport({
    required this.id,
    required this.messageId,
    this.messagePreview = '',
    this.sessionId = 0,
    required this.reportedBy,
    required this.reason,
    this.description = '',
    this.status = ReportStatus.pending,
    this.reviewedById,
    this.adminNote = '',
    this.reviewedAt,
    required this.createdAt,
  });

  factory ChatMessageReport.fromJson(Map<String, dynamic> j) => ChatMessageReport(
        id: _int(j['id']),
        messageId: _int(j['message']),
        messagePreview: j['message_preview'] ?? j['message_text'] ?? '',
        sessionId: _int(j['session'] ?? j['session_id']),
        reportedBy: UserMini.fromJson(j['reported_by']),
        reason: j['reason'] ?? '',
        description: j['description'] ?? '',
        status: j['status'] ?? ReportStatus.pending,
        reviewedById: _intN(j['reviewed_by']),
        adminNote: j['admin_note'] ?? '',
        reviewedAt: _dt(j['reviewed_at']),
        createdAt: DateTime.parse(j['created_at']),
      );

  Map<String, dynamic> toJson() => {
        'message': messageId,
        'reason': reason,
        'description': description,
      };
}

// ---------------------------------------------------------------------------
// 12. COUPON
// ---------------------------------------------------------------------------
class Coupon {
  final int id;
  final int? classroomId; // null = usable across all of this teacher's classrooms
  final UserMini createdBy;
  final String code;
  final int? discountPercent;
  final double? discountAmount;
  final DateTime validFrom;
  final DateTime validUntil;
  final int? maxUses;
  final int usedCount;
  final bool isActive;
  final bool isValid;

  Coupon({
    required this.id,
    this.classroomId,
    required this.createdBy,
    required this.code,
    this.discountPercent,
    this.discountAmount,
    required this.validFrom,
    required this.validUntil,
    this.maxUses,
    this.usedCount = 0,
    this.isActive = true,
    this.isValid = false,
  });

  factory Coupon.fromJson(Map<String, dynamic> j) => Coupon(
        id: _int(j['id']),
        classroomId: _intN(j['classroom']),
        createdBy: UserMini.fromJson(j['created_by']),
        code: j['code'] ?? '',
        discountPercent: _intN(j['discount_percent']),
        discountAmount: _numN(j['discount_amount']),
        validFrom: DateTime.parse(j['valid_from']),
        validUntil: DateTime.parse(j['valid_until']),
        maxUses: _intN(j['max_uses']),
        usedCount: _int(j['used_count']),
        isActive: j['is_active'] ?? true,
        isValid: j['is_valid'] ?? false,
      );

  Map<String, dynamic> toJson() => {
        if (classroomId != null) 'classroom': classroomId,
        'code': code,
        if (discountPercent != null) 'discount_percent': discountPercent,
        if (discountAmount != null) 'discount_amount': discountAmount,
        'valid_from': _instantJson(validFrom),
        'valid_until': _instantJson(validUntil),
        if (maxUses != null) 'max_uses': maxUses,
        'is_active': isActive,
      };
}

// ---------------------------------------------------------------------------
// 13. COIN TRANSACTION (read-only ledger)
// ---------------------------------------------------------------------------
class CoinTxnType {
  static const credit = 'credit';
  static const debit = 'debit';
}

class CoinTransaction {
  final int id;
  final int userId;
  final String txnType;
  final String reason;
  final int amount;
  final int balanceAfter;
  final String referenceId;
  final DateTime createdAt;

  CoinTransaction({
    required this.id,
    required this.userId,
    required this.txnType,
    required this.reason,
    required this.amount,
    required this.balanceAfter,
    this.referenceId = '',
    required this.createdAt,
  });

  factory CoinTransaction.fromJson(Map<String, dynamic> j) => CoinTransaction(
        id: _int(j['id']),
        userId: _int(j['user']),
        txnType: j['txn_type'] ?? '',
        reason: j['reason'] ?? '',
        amount: _int(j['amount']),
        balanceAfter: _int(j['balance_after']),
        referenceId: j['reference_id'] ?? '',
        createdAt: DateTime.parse(j['created_at']),
      );
}

// ---------------------------------------------------------------------------
// 14. CLASSROOM STAFF (co-teacher / moderator / TA)
// ---------------------------------------------------------------------------
class StaffRole {
  static const coTeacher = 'co_teacher';
  static const moderator = 'moderator';
  static const ta = 'ta';
}

class ClassroomStaff {
  final int id;
  final int classroomId;
  final UserMini user;
  final String role;
  final DateTime addedAt;

  ClassroomStaff({
    required this.id,
    required this.classroomId,
    required this.user,
    required this.role,
    required this.addedAt,
  });

  factory ClassroomStaff.fromJson(Map<String, dynamic> j) => ClassroomStaff(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        user: UserMini.fromJson(j['user']),
        role: j['role'] ?? StaffRole.ta,
        addedAt: DateTime.parse(j['added_at']),
      );

  // create body: classroom, user_id, role
  static Map<String, dynamic> createBody({
    required int classroomId,
    required int userId,
    required String role,
  }) =>
      {'classroom': classroomId, 'user_id': userId, 'role': role};
}

// ---------------------------------------------------------------------------
// 14B. CLASSROOM BAN (permanent, classroom-wide — see
// ClassSessionViewSet.kick() for the one-session-only version). Mirrors
// ClassroomBanSerializer's user_id-write / user-read split, same pattern as
// ClassroomStaff above.
// ---------------------------------------------------------------------------
class ClassroomBan {
  final int id;
  final int classroomId;
  final UserMini student;
  final UserMini? bannedBy;
  final String reason;
  final DateTime createdAt;

  ClassroomBan({
    required this.id,
    required this.classroomId,
    required this.student,
    this.bannedBy,
    this.reason = '',
    required this.createdAt,
  });

  factory ClassroomBan.fromJson(Map<String, dynamic> j) => ClassroomBan(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        student: UserMini.fromJson(j['student']),
        bannedBy: j['banned_by'] != null ? UserMini.fromJson(j['banned_by']) : null,
        reason: j['reason'] ?? '',
        createdAt: DateTime.parse(j['created_at']),
      );

  // POST classrooms/{id}/ban/ body: {student_id, reason}
  static Map<String, dynamic> createBody({required int studentId, String reason = ''}) =>
      {'student_id': studentId, if (reason.isNotEmpty) 'reason': reason};
}

// ---------------------------------------------------------------------------
// 14C. SESSION RECORDING — narrow read-only shape for
// GET classrooms/{id}/recordings/ (backend's SessionRecordingSerializer).
// Deliberately NOT the full ClassSession — the backend only sends id,
// classroom, classroom_title, scheduled_start, actual_end, recording_url,
// so reusing ClassSession.fromJson here would crash on the missing
// scheduled_end/status fields.
// ---------------------------------------------------------------------------
class SessionRecording {
  final int id;
  final int classroomId;
  final String classroomTitle;
  final DateTime scheduledStart;
  final DateTime? actualEnd;
  final String recordingUrl;

  SessionRecording({
    required this.id,
    required this.classroomId,
    this.classroomTitle = '',
    required this.scheduledStart,
    this.actualEnd,
    this.recordingUrl = '',
  });

  factory SessionRecording.fromJson(Map<String, dynamic> j) => SessionRecording(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        classroomTitle: j['classroom_title'] ?? '',
        scheduledStart: DateTime.parse(j['scheduled_start']),
        actualEnd: _dt(j['actual_end']),
        recordingUrl: j['recording_url'] ?? '',
      );
}

// ---------------------------------------------------------------------------
// 15. WAITLIST
// ---------------------------------------------------------------------------
class SessionWaitlistEntry {
  final int id;
  final int sessionId;
  final UserMini student;
  final DateTime joinedAt;
  final bool notified;

  SessionWaitlistEntry({
    required this.id,
    required this.sessionId,
    required this.student,
    required this.joinedAt,
    this.notified = false,
  });

  factory SessionWaitlistEntry.fromJson(Map<String, dynamic> j) => SessionWaitlistEntry(
        id: _int(j['id']),
        sessionId: _int(j['session']),
        student: UserMini.fromJson(j['student']),
        joinedAt: DateTime.parse(j['joined_at']),
        notified: j['notified'] ?? false,
      );
}

// ---------------------------------------------------------------------------
// 16. CERTIFICATE
// ---------------------------------------------------------------------------
class Certificate {
  final int id;
  final int classroomId;
  final String classroomTitle;
  final UserMini student;
  final String certificateId;
  final String? certificateFile;
  final DateTime issuedAt;

  Certificate({
    required this.id,
    required this.classroomId,
    this.classroomTitle = '',
    required this.student,
    required this.certificateId,
    this.certificateFile,
    required this.issuedAt,
  });

  factory Certificate.fromJson(Map<String, dynamic> j) => Certificate(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        classroomTitle: j['classroom_title'] ?? '',
        student: UserMini.fromJson(j['student']),
        certificateId: j['certificate_id'] ?? '',
        certificateFile: j['certificate_file'],
        issuedAt: DateTime.parse(j['issued_at']),
      );
}

// ---------------------------------------------------------------------------
// 17. CLASS REMINDER
// ---------------------------------------------------------------------------
class ReminderChannel {
  static const push = 'push';
  static const sms = 'sms';
  static const email = 'email';
}

class ClassReminder {
  final int id;
  final int sessionId;
  final UserMini user;
  final DateTime remindAt;
  final String channel;
  final bool isSent;

  ClassReminder({
    required this.id,
    required this.sessionId,
    required this.user,
    required this.remindAt,
    this.channel = ReminderChannel.push,
    this.isSent = false,
  });

  factory ClassReminder.fromJson(Map<String, dynamic> j) => ClassReminder(
        id: _int(j['id']),
        sessionId: _int(j['session']),
        user: UserMini.fromJson(j['user']),
        remindAt: DateTime.parse(j['remind_at']),
        channel: j['channel'] ?? ReminderChannel.push,
        isSent: j['is_sent'] ?? false,
      );

  Map<String, dynamic> toJson() => {
        'session': sessionId,
        'remind_at': _instantJson(remindAt),
        'channel': channel,
      };
}

// ---------------------------------------------------------------------------
// 18. CLASS HOLIDAY / OFF-DAY
// ---------------------------------------------------------------------------
class ClassHoliday {
  final int id;
  final int classroomId;
  final int? scheduleId; // null = off across every schedule
  final DateTime date;
  final String reason;
  final UserMini? createdBy;
  final DateTime? createdAt;

  ClassHoliday({
    required this.id,
    required this.classroomId,
    this.scheduleId,
    required this.date,
    this.reason = '',
    this.createdBy,
    this.createdAt,
  });

  factory ClassHoliday.fromJson(Map<String, dynamic> j) => ClassHoliday(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        scheduleId: _intN(j['schedule']),
        date: DateTime.parse(j['date']),
        reason: j['reason'] ?? '',
        createdBy: j['created_by'] != null ? UserMini.fromJson(j['created_by']) : null,
        createdAt: _dt(j['created_at']),
      );

  Map<String, dynamic> toJson() => {
        'classroom': classroomId,
        if (scheduleId != null) 'schedule': scheduleId,
        'date': date.toIso8601String().split('T').first,
        'reason': reason,
      };
}

// ---------------------------------------------------------------------------
// 19. NOTICE BOARD
// ---------------------------------------------------------------------------
class NoticePriority {
  static const low = 'low';
  static const normal = 'normal';
  static const urgent = 'urgent';
}

class Notice {
  final int id;
  final int classroomId;
  final UserMini postedBy;
  final String title;
  final String message;
  final String priority;
  final bool isPinned;
  final DateTime createdAt;
  final DateTime? expiresAt;
  final bool isExpired;

  Notice({
    required this.id,
    required this.classroomId,
    required this.postedBy,
    required this.title,
    required this.message,
    this.priority = NoticePriority.normal,
    this.isPinned = false,
    required this.createdAt,
    this.expiresAt,
    this.isExpired = false,
  });

  factory Notice.fromJson(Map<String, dynamic> j) => Notice(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        postedBy: UserMini.fromJson(j['posted_by']),
        title: j['title'] ?? '',
        message: j['message'] ?? '',
        priority: j['priority'] ?? NoticePriority.normal,
        isPinned: j['is_pinned'] ?? false,
        createdAt: DateTime.parse(j['created_at']),
        expiresAt: _dt(j['expires_at']),
        isExpired: j['is_expired'] ?? false,
      );

  Map<String, dynamic> toJson() => {
        'classroom': classroomId,
        'title': title,
        'message': message,
        'priority': priority,
        'is_pinned': isPinned,
        if (expiresAt != null) 'expires_at': _instantJson(expiresAt!),
      };
}

// ---------------------------------------------------------------------------
// 20. CLASS QUERY / DOUBT
// ---------------------------------------------------------------------------
class QueryStatus {
  static const open = 'open';
  static const answered = 'answered';
}

class ClassQuery {
  final int id;
  final int classroomId;
  final int? sessionId;
  final UserMini askedBy;
  final String question;
  final String status;
  final String answer;
  final UserMini? answeredBy;
  final DateTime? answeredAt;
  final DateTime createdAt;

  ClassQuery({
    required this.id,
    required this.classroomId,
    this.sessionId,
    required this.askedBy,
    required this.question,
    this.status = QueryStatus.open,
    this.answer = '',
    this.answeredBy,
    this.answeredAt,
    required this.createdAt,
  });

  factory ClassQuery.fromJson(Map<String, dynamic> j) => ClassQuery(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        sessionId: _intN(j['session']),
        askedBy: UserMini.fromJson(j['asked_by']),
        question: j['question'] ?? '',
        status: j['status'] ?? QueryStatus.open,
        answer: j['answer'] ?? '',
        answeredBy: j['answered_by'] != null ? UserMini.fromJson(j['answered_by']) : null,
        answeredAt: _dt(j['answered_at']),
        createdAt: DateTime.parse(j['created_at']),
      );

  Map<String, dynamic> toJson() => {
        'classroom': classroomId,
        if (sessionId != null) 'session': sessionId,
        'question': question,
      };
}

// ---------------------------------------------------------------------------
// 21. NOTIFICATION (read-only)
// ---------------------------------------------------------------------------
class NotifType {
  static const joinRequestReceived = 'join_request_received';
  static const joinRequestAccepted = 'join_request_accepted';
  static const joinRequestRejected = 'join_request_rejected';
  static const passRefunded = 'pass_refunded';
  static const sessionReminder = 'session_reminder';
  static const assignmentGraded = 'assignment_graded';
  static const queryAnswered = 'query_answered';
  static const certificateIssued = 'certificate_issued';
  static const waitlistPromoted = 'waitlist_promoted';
  static const classroomFlagged = 'classroom_flagged';
  static const noticePosted = 'notice_posted';
  // NOTE (fix): the backend's Notification.NotifType (models.py) had these
  // 7 new types added after a production notification coverage audit —
  // adding them here had been missed. Being a plain String field means
  // AppNotification.fromJson doesn't crash on rows with these values even
  // without the constants, but no UI code (NotificationsScreen's icon/
  // switch-case mapping, or any other type-based branching) could refer
  // to them by name.
  static const sessionLive = 'session_live';
  static const sessionCancelled = 'session_cancelled';
  static const assignmentPosted = 'assignment_posted';
  static const submissionReceived = 'submission_received';
  static const staffAdded = 'staff_added';
  static const reviewPosted = 'review_posted';
  static const reportReviewed = 'report_reviewed';
  // NEW (Pass 16, frontend catch-up §1.8) — 3 more types added alongside
  // auto-renew (Pass 15) and pass gifting (Pass 14). Same "constants +
  // icon map + handler _handledTypes" gap-fix shape as the 7 types above.
  static const passAutoRenewed = 'pass_auto_renewed';
  static const autoRenewFailed = 'auto_renew_failed';
  static const passGiftExpired = 'pass_gift_expired';
  static const generic = 'generic';
}

class AppNotification {
  final int id;
  final String notifType;
  final String title;
  final String message;
  final int? classroomId;
  final String? classroomTitle;
  final int? sessionId;
  final bool isRead;
  final DateTime createdAt;
  final DateTime? readAt;

  AppNotification({
    required this.id,
    required this.notifType,
    required this.title,
    this.message = '',
    this.classroomId,
    this.classroomTitle,
    this.sessionId,
    this.isRead = false,
    required this.createdAt,
    this.readAt,
  });

  factory AppNotification.fromJson(Map<String, dynamic> j) => AppNotification(
        id: _int(j['id']),
        notifType: j['notif_type'] ?? NotifType.generic,
        title: j['title'] ?? '',
        message: j['message'] ?? '',
        classroomId: _intN(j['classroom']),
        classroomTitle: j['classroom_title'],
        sessionId: _intN(j['session']),
        isRead: j['is_read'] ?? false,
        createdAt: DateTime.parse(j['created_at']),
        readAt: _dt(j['read_at']),
      );
}

// ---------------------------------------------------------------------------
// 21A. NOTIFICATION PREFERENCES (Pass 14) — per-notification-type channel
// toggles. ⚠️ ARCHITECTURE SKELETON, not a confirmed field mirror like the
// Pass 12/13 models above — Pass 14 was never written up in the backend
// doc's own §2–§6 (see that doc's top-of-file warning and §11 Pass 16).
// Shape assumed here: ONE settings object keyed by notif-type string, each
// with independent push/email toggles — CONFIRM against
// `NotificationPreferenceSerializer` before shipping; the alternative shape
// (one row per (user, notifType)) would need a list-based model instead.
// sms has no toggle here since notifications.py documents it as a no-op.
// ---------------------------------------------------------------------------
class NotifChannelPref {
  final bool push;
  final bool email;

  const NotifChannelPref({this.push = true, this.email = true});

  factory NotifChannelPref.fromJson(Map<String, dynamic> j) => NotifChannelPref(
        push: j['push'] ?? true,
        email: j['email'] ?? true,
      );

  Map<String, dynamic> toJson() => {'push': push, 'email': email};

  NotifChannelPref copyWith({bool? push, bool? email}) =>
      NotifChannelPref(push: push ?? this.push, email: email ?? this.email);
}

class NotificationPreference {
  /// notifType string (see `NotifType`) -> that type's channel toggles. A
  /// type missing from the map is treated as opted-in on every channel by
  /// [forType] below — confirm this default matches the backend's own
  /// "opted in unless explicitly turned off" behavior.
  final Map<String, NotifChannelPref> perType;

  NotificationPreference({required this.perType});

  factory NotificationPreference.fromJson(Map<String, dynamic> j) => NotificationPreference(
        perType: j.map((k, v) => MapEntry(k, NotifChannelPref.fromJson(v as Map<String, dynamic>))),
      );

  Map<String, dynamic> toJson() => perType.map((k, v) => MapEntry(k, v.toJson()));

  NotifChannelPref forType(String notifType) => perType[notifType] ?? const NotifChannelPref();

  /// Returns a copy with one type's prefs replaced — for a single row's
  /// toggle flipping without resending the whole map from scratch.
  NotificationPreference withType(String notifType, NotifChannelPref pref) {
    final updated = Map<String, NotifChannelPref>.from(perType)..[notifType] = pref;
    return NotificationPreference(perType: updated);
  }
}

/// Every `NotifType` value the preferences screen shows a row for. Dart has
/// no enum reflection, so this list is maintained by hand — add an entry
/// here whenever `NotifType` above gains one (including the 3 Pass 16
/// auto-renew/gift types once those constants exist).
const List<String> kAllNotifTypesForPreferences = [
  NotifType.joinRequestReceived,
  NotifType.joinRequestAccepted,
  NotifType.joinRequestRejected,
  NotifType.passRefunded,
  NotifType.sessionReminder,
  NotifType.sessionLive,
  NotifType.sessionCancelled,
  NotifType.assignmentPosted,
  NotifType.assignmentGraded,
  NotifType.submissionReceived,
  NotifType.queryAnswered,
  NotifType.certificateIssued,
  NotifType.waitlistPromoted,
  NotifType.classroomFlagged,
  NotifType.noticePosted,
  NotifType.staffAdded,
  NotifType.reviewPosted,
  NotifType.reportReviewed,
  NotifType.generic,
];

// ---------------------------------------------------------------------------
// 21B. REFERRAL PROGRAM
// ---------------------------------------------------------------------------
class Referral {
  final int id;
  final UserMini referred;
  final int bonusAmount;
  final DateTime createdAt;

  Referral({required this.id, required this.referred, required this.bonusAmount, required this.createdAt});

  factory Referral.fromJson(Map<String, dynamic> j) => Referral(
        id: _int(j['id']),
        referred: UserMini.fromJson(j['referred']),
        bonusAmount: _int(j['bonus_amount']),
        createdAt: DateTime.parse(j['created_at']),
      );
}

/// Response shape of GET referrals/my-code/ — not tied to a model, `code`
/// is computed server-side (referral_code_for_user), not a stored field.
class MyReferralCode {
  final String code;
  final int referralCount;
  final int totalBonusEarned;
  final int bonusPerReferral;

  MyReferralCode({
    required this.code,
    required this.referralCount,
    required this.totalBonusEarned,
    required this.bonusPerReferral,
  });

  factory MyReferralCode.fromJson(Map<String, dynamic> j) => MyReferralCode(
        code: j['code'] ?? '',
        referralCount: _int(j['referral_count']),
        totalBonusEarned: _int(j['total_bonus_earned']),
        bonusPerReferral: _int(j['bonus_per_referral']),
      );
}

// ---------------------------------------------------------------------------
// 21C. TEACHER EARNINGS DASHBOARD — GET my-earnings/ (optional ?classroom=)
// ---------------------------------------------------------------------------
class EarningsByDay {
  final DateTime date;
  final int amount;

  EarningsByDay({required this.date, required this.amount});

  factory EarningsByDay.fromJson(Map<String, dynamic> j) =>
      EarningsByDay(date: DateTime.parse(j['date']), amount: _int(j['amount']));
}

class EarningsByClassroom {
  final int classroomId;
  final String classroomTitle;
  final int totalEarned;
  final int sessionsCharged;

  EarningsByClassroom({
    required this.classroomId,
    required this.classroomTitle,
    required this.totalEarned,
    required this.sessionsCharged,
  });

  factory EarningsByClassroom.fromJson(Map<String, dynamic> j) => EarningsByClassroom(
        classroomId: _int(j['classroom_id']),
        classroomTitle: j['classroom_title'] ?? '',
        totalEarned: _int(j['total_earned']),
        sessionsCharged: _int(j['sessions_charged']),
      );
}

class TeacherEarnings {
  final int totalEarned;
  final int totalSessionsCharged;
  final int thisMonthEarned;
  final List<EarningsByDay> last30Days;
  final List<EarningsByClassroom> byClassroom;

  TeacherEarnings({
    required this.totalEarned,
    required this.totalSessionsCharged,
    required this.thisMonthEarned,
    required this.last30Days,
    required this.byClassroom,
  });

  factory TeacherEarnings.fromJson(Map<String, dynamic> j) => TeacherEarnings(
        totalEarned: _int(j['total_earned']),
        totalSessionsCharged: _int(j['total_sessions_charged']),
        thisMonthEarned: _int(j['this_month_earned']),
        last30Days: (j['last_30_days'] as List? ?? []).map((e) => EarningsByDay.fromJson(e)).toList(),
        byClassroom: (j['by_classroom'] as List? ?? []).map((e) => EarningsByClassroom.fromJson(e)).toList(),
      );
}

// ---------------------------------------------------------------------------
// DASHBOARD — GET /liveclass/dashboard/ (home-screen summary)
// ---------------------------------------------------------------------------
class LiveClassDashboard {
  final List<ClassSession> upcomingSessions;
  final int teachingClassroomsCount;
  final int enrolledClassroomsCount;
  final int certificatesCount;
  final int wishlistCount;
  final int pendingJoinRequestsCount;
  final int unreadNotificationsCount;

  LiveClassDashboard({
    required this.upcomingSessions,
    required this.teachingClassroomsCount,
    required this.enrolledClassroomsCount,
    required this.certificatesCount,
    required this.wishlistCount,
    required this.pendingJoinRequestsCount,
    required this.unreadNotificationsCount,
  });

  factory LiveClassDashboard.fromJson(Map<String, dynamic> j) => LiveClassDashboard(
        upcomingSessions:
            (j['upcoming_sessions'] as List? ?? []).map((e) => ClassSession.fromJson(e)).toList(),
        teachingClassroomsCount: _int(j['teaching_classrooms_count']),
        enrolledClassroomsCount: _int(j['enrolled_classrooms_count']),
        certificatesCount: _int(j['certificates_count']),
        wishlistCount: _int(j['wishlist_count']),
        pendingJoinRequestsCount: _int(j['pending_join_requests_count']),
        unreadNotificationsCount: _int(j['unread_notifications_count']),
      );
}

// ---------------------------------------------------------------------------
// 22. PASS GIFTING (Pass 14) — `PassGift`. ⚠️ ARCHITECTURE SKELETON — Pass 14
// was never written up in the backend doc's own §2–§6 (see that doc's
// top-of-file warning). Field names below are the frontend-doc's best-guess
// (§1.3) from the change-log description only — CONFIRM against
// `PassGiftSerializer` in serializers.py before trusting `fromJson` on real
// data. `giftedTo` is modeled as a nullable `UserMini` with a raw string
// fallback since the doc flags "email/username string — confirm" as open.
// ---------------------------------------------------------------------------
class PassGiftStatus {
  static const pending = 'pending';
  static const claimed = 'claimed';
  static const expired = 'expired';
  static const refunded = 'refunded';
}

class PassGift {
  final int id;
  final int classPassId;
  final String classPassTitle;
  final String classroomTitle;
  final UserMini gifter;
  final UserMini? giftedTo; // null if backend only ever returns raw identifier below
  final String? giftedToRaw; // email/username as sent, if giftedTo couldn't be resolved
  final String status;
  final DateTime claimWindowExpiresAt;
  final DateTime createdAt;

  PassGift({
    required this.id,
    required this.classPassId,
    this.classPassTitle = '',
    this.classroomTitle = '',
    required this.gifter,
    this.giftedTo,
    this.giftedToRaw,
    this.status = PassGiftStatus.pending,
    required this.claimWindowExpiresAt,
    required this.createdAt,
  });

  bool get isPending => status == PassGiftStatus.pending;
  Duration get timeLeft => claimWindowExpiresAt.difference(DateTime.now());

  factory PassGift.fromJson(Map<String, dynamic> j) {
    final giftedToJson = j['gifted_to'];
    return PassGift(
      id: _int(j['id']),
      classPassId: _int(j['class_pass'] ?? j['class_pass_id']),
      classPassTitle: j['class_pass_title'] ?? '',
      classroomTitle: j['classroom_title'] ?? '',
      gifter: UserMini.fromJson(j['gifter']),
      giftedTo: (giftedToJson is Map<String, dynamic>) ? UserMini.fromJson(giftedToJson) : null,
      giftedToRaw: (giftedToJson is String) ? giftedToJson : j['gifted_to_email'] ?? j['gifted_to_username'],
      status: j['status'] ?? PassGiftStatus.pending,
      claimWindowExpiresAt: DateTime.parse(j['claim_window_expires_at']),
      createdAt: DateTime.parse(j['created_at']),
    );
  }
}

// ---------------------------------------------------------------------------
// 23. SESSION ENGAGEMENT REPORT (Pass 15) — ⚠️ ARCHITECTURE SKELETON, name
// and fields TBC (frontend doc §1.7). Read `serializers.py` before shipping;
// this shape (attendance list + a handful of aggregate numbers) is the
// frontend doc's best guess from the change-log description only.
// ---------------------------------------------------------------------------
class SessionAttendanceRow {
  final UserMini student;
  final int watchDurationSeconds;
  final bool raisedHand;

  SessionAttendanceRow({required this.student, this.watchDurationSeconds = 0, this.raisedHand = false});

  factory SessionAttendanceRow.fromJson(Map<String, dynamic> j) => SessionAttendanceRow(
        student: UserMini.fromJson(j['student']),
        watchDurationSeconds: _int(j['watch_duration_seconds']),
        raisedHand: j['raised_hand'] ?? false,
      );
}

class SessionEngagementReport {
  final int sessionId;
  final int attendanceCount;
  final double avgWatchDurationSeconds;
  final int chatMessageCount;
  final double pollParticipationRate; // 0.0–1.0
  final int handRaiseCount;
  final List<SessionAttendanceRow> attendance;

  SessionEngagementReport({
    required this.sessionId,
    this.attendanceCount = 0,
    this.avgWatchDurationSeconds = 0,
    this.chatMessageCount = 0,
    this.pollParticipationRate = 0,
    this.handRaiseCount = 0,
    this.attendance = const [],
  });

  factory SessionEngagementReport.fromJson(Map<String, dynamic> j) => SessionEngagementReport(
        sessionId: _int(j['session'] ?? j['session_id']),
        attendanceCount: _int(j['attendance_count']),
        avgWatchDurationSeconds: _num(j['avg_watch_duration_seconds']),
        chatMessageCount: _int(j['chat_message_count']),
        pollParticipationRate: _num(j['poll_participation_rate']),
        handRaiseCount: _int(j['hand_raise_count']),
        attendance: (j['attendance'] as List? ?? [])
            .map((e) => SessionAttendanceRow.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

/// Generic paginated list wrapper — DRF's default pagination shape
/// ({"count", "next", "previous", "results": [...]}).
class PaginatedList<T> {
  final int count;
  final String? next;
  final String? previous;
  final List<T> results;

  PaginatedList({required this.count, this.next, this.previous, required this.results});

  factory PaginatedList.fromJson(Map<String, dynamic> j, T Function(Map<String, dynamic>) fromJson) =>
      PaginatedList<T>(
        count: _int(j['count']),
        next: j['next'],
        previous: j['previous'],
        results: (j['results'] as List? ?? []).map((e) => fromJson(e)).toList(),
      );
}