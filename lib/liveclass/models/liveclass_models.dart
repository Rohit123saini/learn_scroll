// ============================================================
// LIVECLASS — DATA MODELS
//
// Field names/types mirror `liveclass/models.py` + `serializers.py`
// 1:1 so the frontend never silently drifts from the backend contract.
// Every model has a tolerant `fromJson` (unknown/missing keys never
// crash the UI — they just fall back to a safe default) because a
// backend field can be added/renamed across passes without shipping
// a paired app release.
// ============================================================

int _int(dynamic v, [int fallback = 0]) =>
    v == null ? fallback : (v is int ? v : int.tryParse(v.toString()) ?? fallback);

int? _intN(dynamic v) => v == null ? null : (v is int ? v : int.tryParse(v.toString()));

double _double(dynamic v, [double fallback = 0]) =>
    v == null ? fallback : (v is num ? v.toDouble() : double.tryParse(v.toString()) ?? fallback);

bool _bool(dynamic v, [bool fallback = false]) => v == null ? fallback : (v is bool ? v : v.toString() == 'true');

String _str(dynamic v, [String fallback = '']) => v?.toString() ?? fallback;

DateTime? _dt(dynamic v) => v == null ? null : DateTime.tryParse(v.toString());

/// Backend `UserMiniSerializer` nests users as {id, username, full_name,
/// profile_picture}; older payloads / write-serializers send a bare id.
/// These helpers accept both so a model never silently reads 0 / ''.
Map<String, dynamic>? _obj(dynamic v) => v is Map ? Map<String, dynamic>.from(v) : null;

int _idOf(dynamic v) => v is Map ? _int(v['id']) : _int(v);

String _userName(dynamic v, [String fallback = '']) {
  final m = _obj(v);
  if (m == null) return fallback;
  final full = _str(m['full_name']);
  return full.isNotEmpty ? full : _str(m['username'], fallback);
}

// ------------------------------------------------------------------
// Classroom
// ------------------------------------------------------------------
enum ClassroomType { individual, organisation }

ClassroomType classroomTypeFrom(String? v) =>
    v == 'organisation' ? ClassroomType.organisation : ClassroomType.individual;

class Classroom {
  final int id;
  final ClassroomType classroomType;
  final String organisationName;
  final int teacherId;
  final String teacherName;
  final String? teacherAvatarUrl;
  final String title;
  final String subject;
  final String description;
  final String language;
  final String? coverImageUrl;
  final bool isActive;
  final bool recordingEnabled;
  final bool referralEnabled;
  final bool chatGroupEnabled;
  final double ratingAvg;
  final int ratingCount;
  final int enrolledCount;
  final DateTime? createdAt;

  Classroom({
    required this.id,
    required this.classroomType,
    required this.organisationName,
    required this.teacherId,
    required this.teacherName,
    this.teacherAvatarUrl,
    required this.title,
    required this.subject,
    required this.description,
    required this.language,
    this.coverImageUrl,
    required this.isActive,
    required this.recordingEnabled,
    required this.referralEnabled,
    required this.chatGroupEnabled,
    required this.ratingAvg,
    required this.ratingCount,
    required this.enrolledCount,
    this.createdAt,
  });

  factory Classroom.fromJson(Map<String, dynamic> j) => Classroom(
        id: _int(j['id']),
        classroomType: classroomTypeFrom(j['classroom_type']?.toString()),
        organisationName: _str(j['organisation_name']),
        teacherId: _idOf(j['teacher'] ?? j['teacher_id']),
        teacherName: _obj(j['teacher']) != null
            ? _userName(j['teacher'])
            : _str(j['teacher_name'] ?? j['teacher_username']),
        teacherAvatarUrl: (_obj(j['teacher'])?['profile_picture'] ?? j['teacher_avatar_url'])?.toString(),
        title: _str(j['title']),
        subject: _str(j['subject']),
        description: _str(j['description']),
        language: _str(j['language'], 'en'),
        coverImageUrl: j['cover_image_url']?.toString() ?? j['cover_image']?.toString(),
        isActive: _bool(j['is_active'], true),
        recordingEnabled: _bool(j['recording_enabled']),
        referralEnabled: _bool(j['referral_enabled']),
        chatGroupEnabled: _bool(j['chat_group_enabled']),
        ratingAvg: _double(j['rating_avg']),
        ratingCount: _int(j['rating_count']),
        enrolledCount: _int(j['enrolled_count']),
        createdAt: _dt(j['created_at']),
      );
}

// ------------------------------------------------------------------
// ClassSchedule
// ------------------------------------------------------------------
class ClassSchedule {
  final int id;
  final int classroomId;
  final String recurrenceType; // specific_date/daily/weekday/weekend/weekly/monthly/yearly
  final List<String> daysOfWeek;
  final DateTime? startTime;
  final DateTime? endTime;
  /// Backend `start_time` is a plain TimeField ("HH:MM:SS"), not a datetime —
  /// kept as text so it can be shown/edited without a fake date attached.
  final String startTimeText;
  final int durationMinutes;

  ClassSchedule({
    required this.id,
    required this.classroomId,
    required this.recurrenceType,
    required this.daysOfWeek,
    this.startTime,
    this.endTime,
    this.startTimeText = '',
    this.durationMinutes = 0,
  });

  factory ClassSchedule.fromJson(Map<String, dynamic> j) => ClassSchedule(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        recurrenceType: _str(j['recurrence_type']),
        daysOfWeek: (j['days_of_week'] as List?)?.map((e) => e.toString()).toList() ?? const [],
        startTime: _dt(j['start_time']),
        endTime: _dt(j['end_time']),
        startTimeText: _str(j['start_time']),
        durationMinutes: _int(j['duration_minutes']),
      );
}

// ------------------------------------------------------------------
// ClassSession
// ------------------------------------------------------------------
enum SessionStatus { scheduled, live, completed, cancelled }

SessionStatus sessionStatusFrom(String? v) {
  switch (v) {
    case 'live':
      return SessionStatus.live;
    case 'completed':
      return SessionStatus.completed;
    case 'cancelled':
      return SessionStatus.cancelled;
    default:
      return SessionStatus.scheduled;
  }
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
  final SessionStatus status;
  final String recordingUrl;
  final bool recordingInProgress;

  ClassSession({
    required this.id,
    required this.classroomId,
    required this.classroomTitle,
    this.scheduleId,
    required this.roomId,
    required this.scheduledStart,
    required this.scheduledEnd,
    this.actualStart,
    this.actualEnd,
    required this.status,
    required this.recordingUrl,
    required this.recordingInProgress,
  });

  factory ClassSession.fromJson(Map<String, dynamic> j) => ClassSession(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        classroomTitle: _str(j['classroom_title']),
        scheduleId: _intN(j['schedule']),
        roomId: _str(j['room_id']),
        scheduledStart: _dt(j['scheduled_start']) ?? DateTime.now(),
        scheduledEnd: _dt(j['scheduled_end']) ?? DateTime.now(),
        actualStart: _dt(j['actual_start']),
        actualEnd: _dt(j['actual_end']),
        status: sessionStatusFrom(j['status']?.toString()),
        recordingUrl: _str(j['recording_url']),
        recordingInProgress: _bool(j['is_recording'] ?? j['recording_in_progress']),
      );
}

/// Lightweight shape for `sessions/live-now/`.
class LiveNowSession {
  final int sessionId;
  final int classroomId;
  final String classroomTitle;
  final String? coverImageUrl;
  final int viewerCount;

  LiveNowSession({
    required this.sessionId,
    required this.classroomId,
    required this.classroomTitle,
    this.coverImageUrl,
    required this.viewerCount,
  });

  factory LiveNowSession.fromJson(Map<String, dynamic> j) => LiveNowSession(
        sessionId: _int(j['id'] ?? j['session_id']),
        classroomId: _idOf(j['classroom'] ?? j['classroom_id']),
        classroomTitle: _str(_obj(j['classroom'])?['title'] ?? j['classroom_title']),
        coverImageUrl: (_obj(j['classroom'])?['cover_image'] ?? j['cover_image_url'])?.toString(),
        viewerCount: _int(j['participant_count'] ?? j['viewer_count']),
      );
}

// ------------------------------------------------------------------
// ClassPass
// ------------------------------------------------------------------
class ClassPass {
  final int id;
  final int classroomId;
  final String passType; // free/daily/weekly/monthly/yearly
  final String title;
  final double price; // in coins
  final int validityDays;
  final int? maxClasses;
  final bool isActive;
  final bool giftingAllowed;

  ClassPass({
    required this.id,
    required this.classroomId,
    required this.passType,
    required this.title,
    required this.price,
    required this.validityDays,
    this.maxClasses,
    required this.isActive,
    required this.giftingAllowed,
  });

  factory ClassPass.fromJson(Map<String, dynamic> j) => ClassPass(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        passType: _str(j['pass_type']),
        title: _str(j['title']),
        price: _double(j['price']),
        validityDays: _int(j['validity_days']),
        maxClasses: _intN(j['max_classes']),
        isActive: _bool(j['is_active'], true),
        giftingAllowed: _bool(j['allow_gifting'] ?? j['gifting_allowed']),
      );
}

// ------------------------------------------------------------------
// ClassJoinRequest
// ------------------------------------------------------------------
enum JoinRequestStatus { pending, accepted, rejected, cancelled }

JoinRequestStatus joinStatusFrom(String? v) {
  switch (v) {
    case 'accepted':
      return JoinRequestStatus.accepted;
    case 'rejected':
      return JoinRequestStatus.rejected;
    case 'cancelled':
      return JoinRequestStatus.cancelled;
    default:
      return JoinRequestStatus.pending;
  }
}

class ClassJoinRequest {
  final int id;
  final int classroomId;
  final int classPassId;
  final int studentId;
  final String studentName;
  final String couponCode;
  final String message;
  final JoinRequestStatus status;
  final DateTime? createdAt;

  ClassJoinRequest({
    required this.id,
    required this.classroomId,
    required this.classPassId,
    required this.studentId,
    required this.studentName,
    required this.couponCode,
    required this.message,
    required this.status,
    this.createdAt,
  });

  factory ClassJoinRequest.fromJson(Map<String, dynamic> j) => ClassJoinRequest(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        classPassId: _int(j['class_pass']),
        studentId: _idOf(j['student']),
        studentName: _obj(j['student']) != null ? _userName(j['student']) : _str(j['student_name']),
        couponCode: _str(j['coupon_code']),
        message: _str(j['message']),
        status: joinStatusFrom(j['status']?.toString()),
        createdAt: _dt(j['requested_at'] ?? j['created_at']),
      );
}

// ------------------------------------------------------------------
// PassPurchase
// ------------------------------------------------------------------
class PassPurchase {
  final int id;
  final int studentId;
  final int classPassId;
  final String status; // pending/success/failed/refunded
  final double amountPaid;
  final int coinsSpent;
  final bool autoRenew;
  final DateTime? expiresAt;

  PassPurchase({
    required this.id,
    required this.studentId,
    required this.classPassId,
    required this.status,
    required this.amountPaid,
    required this.coinsSpent,
    required this.autoRenew,
    this.expiresAt,
  });

  factory PassPurchase.fromJson(Map<String, dynamic> j) => PassPurchase(
        id: _int(j['id']),
        studentId: _idOf(j['student']),
        classPassId: _idOf(j['class_pass']),
        status: _str(j['status']),
        amountPaid: _double(j['amount_paid']),
        coinsSpent: _int(j['coins_spent']),
        autoRenew: _bool(j['auto_renew']),
        expiresAt: _dt(j['expires_at']),
      );
}

/// Shape for `classrooms/{id}/my-pass/`.
class MyPassStatus {
  /// owner / active / expired / pending / none. Backend key is `status`
  /// (legacy label, "owner" for teacher/co-teacher/moderator/org-staff);
  /// `access_level` carries the same value with "admin" instead of "owner".
  final String state;
  final DateTime? expiresAt;
  final bool canEnterClass;
  final bool canViewInternals;
  final int? pendingRequestId;
  MyPassStatus({
    required this.state,
    this.expiresAt,
    this.canEnterClass = false,
    this.canViewInternals = false,
    this.pendingRequestId,
  });
  bool get isManager => state == 'owner' || state == 'admin';
  factory MyPassStatus.fromJson(Map<String, dynamic> j) => MyPassStatus(
        state: _str(j['status'] ?? j['state'] ?? j['access_level'], 'none'),
        expiresAt: _dt(j['expires_at']),
        canEnterClass: _bool(j['can_enter_class'] ?? j['has_access']),
        canViewInternals: _bool(j['can_view_internals']),
        pendingRequestId: _intN(j['pending_request_id']),
      );
}

// ------------------------------------------------------------------
// Coupon
// ------------------------------------------------------------------
class Coupon {
  final int id;
  final int? classroomId; // null = usable across all of this teacher's classrooms
  final String code;
  final int? discountPercent;
  final double? discountAmount;
  final DateTime? validFrom;
  final DateTime? validUntil;
  final int? maxUses;
  final int usedCount;
  final bool isActive;

  Coupon({
    required this.id,
    this.classroomId,
    required this.code,
    this.discountPercent,
    this.discountAmount,
    this.validFrom,
    this.validUntil,
    this.maxUses,
    required this.usedCount,
    required this.isActive,
  });

  factory Coupon.fromJson(Map<String, dynamic> j) => Coupon(
        id: _int(j['id']),
        classroomId: _intN(j['classroom']),
        code: _str(j['code']),
        discountPercent: _intN(j['discount_percent']),
        discountAmount: j['discount_amount'] == null ? null : _double(j['discount_amount']),
        validFrom: _dt(j['valid_from']),
        validUntil: _dt(j['valid_until']),
        maxUses: _intN(j['max_uses']),
        usedCount: _int(j['used_count']),
        isActive: _bool(j['is_active'], true),
      );
}

// ------------------------------------------------------------------
// SessionParticipant
// ------------------------------------------------------------------
class SessionParticipant {
  final int id;
  final int sessionId;
  final int userId;
  final String userName;
  final String role; // host/student
  final DateTime? joinedAt;
  final DateTime? leftAt;
  final bool kicked;
  final bool handRaised;
  final bool muted;

  SessionParticipant({
    required this.id,
    required this.sessionId,
    required this.userId,
    required this.userName,
    required this.role,
    this.joinedAt,
    this.leftAt,
    required this.kicked,
    required this.handRaised,
    required this.muted,
  });

  factory SessionParticipant.fromJson(Map<String, dynamic> j) => SessionParticipant(
        id: _int(j['id']),
        sessionId: _int(j['session']),
        userId: _idOf(j['user']),
        userName: _obj(j['user']) != null ? _userName(j['user']) : _str(j['user_name']),
        role: _str(j['role'], 'student'),
        joinedAt: _dt(j['joined_at']),
        leftAt: _dt(j['left_at']),
        kicked: j['kicked_at'] != null,
        handRaised: _bool(j['hand_raised']),
        muted: _bool(j['muted']),
      );
}

// ------------------------------------------------------------------
// ChatMessage
// ------------------------------------------------------------------
class ChatMessage {
  final int id;
  final int sessionId;
  final int senderId;
  final String senderName;
  final String message;
  final DateTime? sentAt;
  final bool isDeleted;
  final bool isPinned;
  final int? replyToId;
  final Map<String, int> reactions;

  ChatMessage({
    required this.id,
    required this.sessionId,
    required this.senderId,
    required this.senderName,
    required this.message,
    this.sentAt,
    required this.isDeleted,
    required this.isPinned,
    this.replyToId,
    required this.reactions,
  });

  factory ChatMessage.fromJson(Map<String, dynamic> j) => ChatMessage(
        id: _int(j['id']),
        sessionId: _int(j['session']),
        senderId: _idOf(j['sender']),
        senderName: _obj(j['sender']) != null ? _userName(j['sender']) : _str(j['sender_name']),
        message: _str(j['message']),
        sentAt: _dt(j['sent_at']),
        isDeleted: _bool(j['is_deleted']),
        isPinned: _bool(j['is_pinned']),
        replyToId: _intN(j['reply_to']),
        reactions: ((j['reaction_counts'] ?? j['reactions']) as Map?)?.map((k, v) => MapEntry(k.toString(), _int(v))) ?? const {},
      );
}

// ------------------------------------------------------------------
// Poll
// ------------------------------------------------------------------
class PollOption {
  final int id;
  final String text;
  final int votes;
  PollOption({required this.id, required this.text, required this.votes});
  factory PollOption.fromJson(Map<String, dynamic> j) =>
      PollOption(id: _int(j['id']), text: _str(j['text']), votes: _int(j['votes']));
}

class LivePoll {
  final int id;
  final int sessionId;
  final String question;
  final bool isClosed;
  final List<PollOption> options;
  final int? myVoteOptionId;

  LivePoll({
    required this.id,
    required this.sessionId,
    required this.question,
    required this.isClosed,
    required this.options,
    this.myVoteOptionId,
  });

  factory LivePoll.fromJson(Map<String, dynamic> j) => LivePoll(
        id: _int(j['id']),
        sessionId: _int(j['session']),
        question: _str(j['question']),
        // Backend: `is_active` (True until closed). Old shape: `is_closed`.
        isClosed: j.containsKey('is_active') ? !_bool(j['is_active'], true) : _bool(j['is_closed']),
        // Backend: `options` = ["A", "B"] (plain strings, voted by INDEX) and
        // `result_counts` = {"0": n, "1": m}. Old shape: [{id,text,votes}].
        options: () {
          final raw = j['options'] as List? ?? const [];
          final counts = (j['result_counts'] as Map?) ?? const {};
          return [
            for (var i = 0; i < raw.length; i++)
              raw[i] is Map
                  ? PollOption.fromJson(Map<String, dynamic>.from(raw[i] as Map))
                  : PollOption(id: i, text: raw[i].toString(), votes: _int(counts['$i'] ?? counts[i])),
          ];
        }(),
        myVoteOptionId: _intN(j['my_vote_option_id'] ?? j['my_vote']),
      );
}

// ------------------------------------------------------------------
// ClassMaterial
// ------------------------------------------------------------------
class ClassMaterial {
  final int id;
  final int classroomId;
  final int? sessionId;
  final String title;
  final String materialType; // pdf/ppt/doc/image/video/link
  final String? fileUrl;
  final String externalLink;
  final DateTime? uploadedAt;

  ClassMaterial({
    required this.id,
    required this.classroomId,
    this.sessionId,
    required this.title,
    required this.materialType,
    this.fileUrl,
    required this.externalLink,
    this.uploadedAt,
  });

  factory ClassMaterial.fromJson(Map<String, dynamic> j) => ClassMaterial(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        sessionId: _intN(j['session']),
        title: _str(j['title']),
        materialType: _str(j['material_type']),
        fileUrl: j['file']?.toString(),
        externalLink: _str(j['external_link']),
        uploadedAt: _dt(j['uploaded_at']),
      );
}

// ------------------------------------------------------------------
// Notice / Holiday / Query / Reminder
// ------------------------------------------------------------------
class ClassNotice {
  final int id;
  final int classroomId;
  final String title;
  final String message;
  final String priority; // low/normal/urgent
  final bool isPinned;
  final DateTime? createdAt;
  final DateTime? expiresAt;

  ClassNotice({
    required this.id,
    required this.classroomId,
    required this.title,
    required this.message,
    required this.priority,
    required this.isPinned,
    this.createdAt,
    this.expiresAt,
  });

  factory ClassNotice.fromJson(Map<String, dynamic> j) => ClassNotice(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        title: _str(j['title']),
        message: _str(j['message']),
        priority: _str(j['priority'], 'normal'),
        isPinned: _bool(j['is_pinned']),
        createdAt: _dt(j['created_at']),
        expiresAt: _dt(j['expires_at']),
      );
}

class ClassQuery {
  final int id;
  final int classroomId;
  final int studentId;
  final String studentName;
  final String question;
  final String? answer;
  final DateTime? createdAt;
  final DateTime? answeredAt;

  ClassQuery({
    required this.id,
    required this.classroomId,
    required this.studentId,
    required this.studentName,
    required this.question,
    this.answer,
    this.createdAt,
    this.answeredAt,
  });

  factory ClassQuery.fromJson(Map<String, dynamic> j) => ClassQuery(
        id: _int(j['id']),
        classroomId: _int(j['classroom']),
        studentId: _idOf(j['asked_by'] ?? j['student']),
        studentName: _obj(j['asked_by']) != null ? _userName(j['asked_by']) : _str(j['student_name']),
        question: _str(j['question']),
        answer: j['answer']?.toString(),
        createdAt: _dt(j['created_at']),
        answeredAt: _dt(j['answered_at']),
      );
}

// ------------------------------------------------------------------
// Coins / Wallet
// ------------------------------------------------------------------
class CoinTransaction {
  final int id;
  final String txnType; // credit/debit
  final String reason;
  final int coins;
  final DateTime? createdAt;

  CoinTransaction({required this.id, required this.txnType, required this.reason, required this.coins, this.createdAt});

  factory CoinTransaction.fromJson(Map<String, dynamic> j) => CoinTransaction(
        id: _int(j['id']),
        txnType: _str(j['txn_type']),
        reason: _str(j['reason']),
        coins: _int(j['coins'] ?? j['amount']),
        createdAt: _dt(j['created_at']),
      );
}

class CoinWithdrawal {
  final int id;
  final int coins;
  final double amountInr;
  final String status; // pending/approved/rejected/paid/cancelled
  final String payoutMethod; // bank_transfer/upi
  final DateTime? createdAt;

  CoinWithdrawal({
    required this.id,
    required this.coins,
    required this.amountInr,
    required this.status,
    required this.payoutMethod,
    this.createdAt,
  });

  factory CoinWithdrawal.fromJson(Map<String, dynamic> j) => CoinWithdrawal(
        id: _int(j['id']),
        coins: _int(j['coins']),
        amountInr: _double(j['amount_inr']),
        status: _str(j['status']),
        payoutMethod: _str(j['payout_method']),
        createdAt: _dt(j['requested_at'] ?? j['created_at']),
      );
}

// ------------------------------------------------------------------
// Dashboard / earnings / progress (plain APIView response shapes)
// ------------------------------------------------------------------
class LiveClassDashboard {
  /// Not part of `GET /dashboard/` — filled from `sessions/live-now/` by the screen.
  List<LiveNowSession> liveNow;
  final List<ClassSession> upcoming;
  final int coinBalance;
  final int unreadNotices;
  final int teachingCount;
  final int enrolledCount;
  final int certificatesCount;
  final int wishlistCount;
  final int pendingJoinRequests;

  LiveClassDashboard({
    required this.liveNow,
    required this.upcoming,
    required this.coinBalance,
    required this.unreadNotices,
    this.teachingCount = 0,
    this.enrolledCount = 0,
    this.certificatesCount = 0,
    this.wishlistCount = 0,
    this.pendingJoinRequests = 0,
  });

  factory LiveClassDashboard.fromJson(Map<String, dynamic> j) => LiveClassDashboard(
        liveNow: (j['live_now'] as List? ?? const [])
            .map((e) => LiveNowSession.fromJson(e as Map<String, dynamic>))
            .toList(),
        upcoming: ((j['upcoming_sessions'] ?? j['upcoming']) as List? ?? const [])
            .map((e) => ClassSession.fromJson(Map<String, dynamic>.from(e as Map)))
            .toList(),
        coinBalance: _int(j['coin_balance']),
        unreadNotices: _int(j['unread_notifications_count'] ?? j['unread_notices']),
        teachingCount: _int(j['teaching_classrooms_count']),
        enrolledCount: _int(j['enrolled_classrooms_count']),
        certificatesCount: _int(j['certificates_count']),
        wishlistCount: _int(j['wishlist_count']),
        pendingJoinRequests: _int(j['pending_join_requests_count']),
      );
}

class TeacherEarnings {
  final int totalCoins;
  final double totalInr;
  final Map<String, int> byDay;
  final Map<String, int> byClassroom;

  TeacherEarnings({required this.totalCoins, required this.totalInr, required this.byDay, required this.byClassroom});

  factory TeacherEarnings.fromJson(Map<String, dynamic> j) => TeacherEarnings(
        // Backend: total_earned (coins; 1 coin = 1 INR — CoinWithdrawal.COIN_TO_INR_RATE),
        // last_30_days [{date, amount}], by_classroom [{classroom_id, classroom_title,
        // total_earned, sessions_charged}].
        totalCoins: _int(j['total_earned'] ?? j['total_coins']),
        totalInr: _double(j['total_inr'] ?? j['total_earned']),
        byDay: j['last_30_days'] is List
            ? {
                for (final e in (j['last_30_days'] as List).whereType<Map>())
                  e['date'].toString(): _int(e['amount']),
              }
            : ((j['by_day'] as Map?)?.map((k, v) => MapEntry(k.toString(), _int(v))) ?? const {}),
        byClassroom: j['by_classroom'] is List
            ? {
                for (final e in (j['by_classroom'] as List).whereType<Map>())
                  _str(e['classroom_title'], e['classroom_id'].toString()): _int(e['total_earned']),
              }
            : ((j['by_classroom'] as Map?)?.map((k, v) => MapEntry(k.toString(), _int(v))) ?? const {}),
      );
}

class StudentProgress {
  final int classesAttended;
  final double attendancePercent;
  final int attendanceStreak;
  final int assignmentsCompleted;
  final int certificatesEarned;

  StudentProgress({
    required this.classesAttended,
    required this.attendancePercent,
    required this.attendanceStreak,
    required this.assignmentsCompleted,
    required this.certificatesEarned,
  });

  factory StudentProgress.fromJson(Map<String, dynamic> j) => StudentProgress(
        classesAttended: _int(j['classes_attended']),
        attendancePercent: _double(j['attendance_percent']),
        attendanceStreak: _int(j['current_streak_days'] ?? j['attendance_streak']),
        assignmentsCompleted: _int(j['assigmentss_submitted'] ?? j['assignments_completed']),
        certificatesEarned: _int(j['certificates_earned']),
      );
}
