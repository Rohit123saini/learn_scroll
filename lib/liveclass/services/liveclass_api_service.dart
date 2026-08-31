// lib/liveclass/services/liveclass_api_service.dart
//
// API service layer for the `liveclass` Django app (see urls.py). One Dio
// client shared by every resource-specific helper class below — mirrors the
// endpoint list in urls.py 1:1 so any screen can just call, e.g.:
//   LiveClassApi.classrooms.explore(search: 'python', language: 'Hindi')
//   LiveClassApi.sessions.join(sessionId)
//   LiveClassApi.joinRequests.accept(requestId)
//
// Base URL: same pattern as the rest of the app — Api.baseUrl from
// utils/api.dart, with "/liveclass" appended (see liveclass_models used
// to live/services/ counterpart before this Dio rewrite).

import 'package:dio/dio.dart';

import '../../utils/api.dart';
import '../../services/auth_service.dart';
import '../models/liveclass_models.dart';

final String _baseUrl = "${Api.baseUrl}/liveclass/";

class _Http {
  static Dio? _dio;

  static Future<Dio> client() async {
    if (_dio != null) return _dio!;
    final dio = Dio(BaseOptions(baseUrl: _baseUrl, connectTimeout: const Duration(seconds: 20)));
    dio.interceptors.add(InterceptorsWrapper(onRequest: (options, handler) async {
      final token = await AuthService.getToken();
      if (token != null && token.isNotEmpty) {
        options.headers['Authorization'] = 'Bearer $token';
      }
      return handler.next(options);
    }));
    _dio = dio;
    return dio;
  }
}

/// Thrown for any non-2xx response; screens can catch this and read
/// [message] (built from DRF's {"detail": ...} or field-error bodies) plus
/// the raw [statusCode] — e.g. 403 on sessions/{id}/join/ means "buy a pass",
/// 202 means "added to waitlist".
class LiveClassApiException implements Exception {
  final int? statusCode;
  final String message;
  final dynamic body;
  LiveClassApiException(this.statusCode, this.message, [this.body]);
  @override
  String toString() => 'LiveClassApiException($statusCode): $message';
}

Never _throwFrom(DioException e) {
  final data = e.response?.data;
  String msg = e.message ?? 'Network error';
  if (data is Map && data['detail'] != null) {
    msg = data['detail'].toString();
  } else if (data is Map && data.isNotEmpty) {
    msg = data.values.first.toString();
  }
  throw LiveClassApiException(e.response?.statusCode, msg, data);
}

// ---------------------------------------------------------------------------
// Top-level facade — one place to reach every resource.
// ---------------------------------------------------------------------------
class LiveClassApi {
  static final classrooms = ClassroomApi();
  static final classroomReports = ClassroomReportApi();
  static final chatMessageReports = ChatMessageReportApi();
  static final schedules = ScheduleApi();
  static final sessions = SessionApi();
  static final breakoutRooms = BreakoutRoomApi();
  static final passes = ClassPassApi();
  static final joinRequests = JoinRequestApi();
  static final passPurchases = PassPurchaseApi();
  static final participants = ParticipantApi();
  static final materials = MaterialApi();
  static final chatMessages = ChatMessageApi();
  static final polls = PollApi();
  static final pollTemplates = PollTemplateApi();
  static final assignments = AssignmentApi();
  static final submissions = SubmissionApi();
  static final reviews = ReviewApi();
  static final wishlist = WishlistApi();
  static final coupons = CouponApi();
  static final coinTransactions = CoinTransactionApi();
  static final staff = ClassroomStaffApi();
  static final waitlist = WaitlistApi();
  static final certificates = CertificateApi();
  static final reminders = ReminderApi();
  static final holidays = HolidayApi();
  static final notices = NoticeApi();
  static final queries = QueryApi();
  static final notifications = NotificationApi();
  static final notificationPreferences = NotificationPreferenceApi();
  static final referrals = ReferralApi();
  static final passGifts = PassGiftApi();

  /// GET /liveclass/dashboard/ — single-call home-screen summary.
  static Future<LiveClassDashboard> dashboard() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('dashboard/');
      return LiveClassDashboard.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET /liveclass/my-earnings/ — teacher-only earnings summary; optional
  /// [classroomId] scopes it to one classroom the caller teaches (403 if
  /// they don't teach it). A co-teacher/moderator earns nothing themselves
  /// under the escrow design — this is teacher-only, not staff-visible.
  static Future<TeacherEarnings> myEarnings({int? classroomId}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('my-earnings/', queryParameters: {
        if (classroomId != null) 'classroom': classroomId,
      });
      return TeacherEarnings.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 1. CLASSROOMS
// ---------------------------------------------------------------------------
class ClassroomApi {
  /// GET classrooms/  — the public Explore/search list.
  Future<PaginatedList<Classroom>> explore({
    String? search,
    String? language,
    bool mine = false,
    int page = 1,
  }) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/', queryParameters: {
        if (search != null && search.isNotEmpty) 'search': search,
        if (language != null && language.isNotEmpty) 'language': language,
        if (mine) 'mine': 'true',
        'page': page,
      });
      return PaginatedList.fromJson(res.data, Classroom.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<Classroom> detail(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/$id/');
      return Classroom.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST classrooms/  — create. Pass [coverImagePath] to attach a cover
  /// photo (multipart); omit for a text-only create.
  Future<Classroom> create(Classroom classroom, {String? coverImagePath}) async {
    try {
      final dio = await _Http.client();
      final body = classroom.toJson();
      final data = coverImagePath == null
          ? body
          : FormData.fromMap({
              ...body,
              'cover_image': await MultipartFile.fromFile(coverImagePath),
            });
      final res = await dio.post('classrooms/', data: data);
      return Classroom.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<Classroom> update(int id, Classroom classroom, {String? coverImagePath}) async {
    try {
      final dio = await _Http.client();
      final body = classroom.toJson();
      final data = coverImagePath == null
          ? body
          : FormData.fromMap({
              ...body,
              'cover_image': await MultipartFile.fromFile(coverImagePath),
            });
      final res = await dio.patch('classrooms/$id/', data: data);
      return Classroom.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// DELETE classrooms/{id}/ — soft delete. Only allowed once 30+ days old
  /// AND no active paid pass outstanding (else backend returns 400 — use
  /// [close] instead).
  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('classrooms/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST classrooms/{id}/start-or-join/ — the ONE call for "get me into
  /// this classroom's class right now", for students and teachers alike.
  /// Replaces the old client-side dance: list live sessions, list
  /// scheduled ones, sort them, decide, maybe create-then-join an ad-hoc
  /// one (see the old _enterClass/_offerStartClassNow/_startClassNow in
  /// classroom_detail_screen.dart — all of that collapses into this one
  /// call). Throws [LiveClassApiException] with statusCode 404 and
  /// `e.body['no_session'] == true` when there's genuinely nothing to
  /// join right now (only ever thrown for a student/non-manager — a
  /// classroom's own teacher/co-teacher/moderator/org-staff always gets a
  /// session back, starting a fresh ad-hoc one automatically if needed;
  /// [SessionJoinResult.startedNew] / [SessionJoinResult.session] tell you
  /// when that happened, e.g. to show "Class started" vs "Joined class").
  Future<SessionJoinResult> startOrJoin(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('classrooms/$classroomId/start-or-join/');
      // NOTE (fix): unlike sessions.join()'s 202 short-circuit, this keeps
      // parsing the body on 202 too — start-or-join always attaches
      // "session" (see views.py's start_or_join), and the waitlisted
      // screen still benefits from knowing which session it's queued for.
      final result = SessionJoinResult.fromJson(res.data);
      return res.statusCode == 202
          ? SessionJoinResult(
              roomId: result.roomId,
              role: result.role,
              livekitRole: result.livekitRole,
              livekitUrl: result.livekitUrl,
              livekitToken: result.livekitToken,
              waitlisted: true,
              session: result.session,
              startedNew: result.startedNew,
            )
          : result;
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST classrooms/{id}/close/ — teacher-only. Refunds every active paid
  /// pass, then deactivates. Use this instead of [delete] to stop a
  /// classroom early. Returns {"closed": true, "passes_refunded": N}.
  Future<Map<String, dynamic>> close(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('classrooms/$id/close/');
      return Map<String, dynamic>.from(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<bool> hasAccess(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/$id/has-access/');
      return res.data['has_access'] ?? false;
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET classrooms/{id}/my-pass/ — the single call the classroom-detail
  /// screen should make to decide what UI to render.
  Future<MyPassStatus> myPass(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/$id/my-pass/');
      return MyPassStatus.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassroomStats> stats(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/$id/stats/');
      return ClassroomStats.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  // -----------------------------------------------------------------
  // FEATURE (classroom-wide ban) — was fully implemented on the backend
  // (ClassroomViewSet.ban/bans/unban in views.py) with no frontend caller
  // anywhere in the module. Idempotent on the backend: banning an already-
  // banned student returns the existing ban (200) instead of erroring.
  // -----------------------------------------------------------------

  /// POST classrooms/{id}/ban/ — teacher/co-teacher/moderator only.
  /// Permanently bans a student: best-effort kicks them from any session
  /// they're live in right now, rejects any still-pending join request,
  /// and reverses/refunds any active paid pass for this classroom.
  Future<ClassroomBan> ban({required int classroomId, required int studentId, String reason = ''}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('classrooms/$classroomId/ban/',
          data: ClassroomBan.createBody(studentId: studentId, reason: reason));
      return ClassroomBan.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET classrooms/{id}/bans/ — teacher/co-teacher/moderator only. Note:
  /// unlike almost every other list endpoint in this file, the backend
  /// returns a plain JSON array here (no pagination wrapper), so this
  /// parses `res.data` directly as a List rather than via PaginatedList.
  Future<List<ClassroomBan>> bans(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/$classroomId/bans/');
      return (res.data as List).map((e) => ClassroomBan.fromJson(e)).toList();
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST classrooms/{id}/unban/{studentId}/ — lifts a ban. Does NOT
  /// restore the refunded pass — the student is welcome back but would
  /// need to raise a fresh join request and pay again, same as any other
  /// new student.
  Future<void> unban({required int classroomId, required int studentId}) async {
    try {
      final dio = await _Http.client();
      await dio.post('classrooms/$classroomId/unban/$studentId/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  // -----------------------------------------------------------------
  // FEATURE (recordings library) — recording_url has existed per-session
  // since the LiveKit egress wiring, but there was no browsable "past
  // recordings" list on the frontend; the backend endpoint
  // (ClassroomViewSet.recordings) already existed with no caller.
  // -----------------------------------------------------------------

  /// GET classrooms/{id}/recordings/ — browsable list of this classroom's
  /// past recorded sessions (only ones with a non-empty recording_url,
  /// newest first). Gated the same tier as materials/notices — teacher/
  /// staff/anyone who has ever held a pass, active or expired. A session
  /// that was recorded but hasn't finished uploading yet (LiveKit egress
  /// webhook still pending) simply won't show up here until it does.
  Future<PaginatedList<SessionRecording>> recordings(int classroomId, {int page = 1}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/$classroomId/recordings/', queryParameters: {'page': page});
      return PaginatedList.fromJson(res.data, SessionRecording.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 1B. CLASSROOM REPORTS
// ---------------------------------------------------------------------------
class ClassroomReportApi {
  /// GET classroom-reports/ — own filed reports, or (platform staff) every
  /// report, filterable by [classroomId] / [status].
  Future<PaginatedList<ClassroomReport>> list({int? classroomId, String? status}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classroom-reports/', queryParameters: {
        if (classroomId != null) 'classroom': classroomId,
        if (status != null) 'status': status,
      });
      return PaginatedList.fromJson(res.data, ClassroomReport.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassroomReport> detail(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classroom-reports/$id/');
      return ClassroomReport.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST classroom-reports/ — file a report (only users who've ever held a
  /// pass for this classroom can report it).
  Future<ClassroomReport> file({required int classroomId, required String reason, String description = ''}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('classroom-reports/', data: {
        'classroom': classroomId,
        'reason': reason,
        'description': description,
      });
      return ClassroomReport.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST classroom-reports/{id}/review/ — platform staff only.
  Future<ClassroomReport> review(int id, {required String status, String adminNote = ''}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('classroom-reports/$id/review/', data: {
        'status': status,
        'admin_note': adminNote,
      });
      return ClassroomReport.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 1B. CHAT MESSAGE REPORTS (Pass 14) — ⚠️ ARCHITECTURE SKELETON, endpoint
// paths unconfirmed against urls.py (frontend doc §1.4). Mirrors
// ClassroomReportApi above's shape/convention as the best guess.
// ---------------------------------------------------------------------------
class ChatMessageReportApi {
  /// POST chat-message-reports/ — any participant who's seen the message
  /// can file one (unlike ClassroomReport-style moderation actions, this
  /// is not host-only).
  Future<ChatMessageReport> create({required int messageId, required String reason, String description = ''}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('chat-message-reports/', data: {
        'message': messageId,
        'reason': reason,
        'description': description,
      });
      return ChatMessageReport.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET chat-message-reports/ — own filed reports, or (platform staff)
  /// every report, filterable by [status].
  Future<PaginatedList<ChatMessageReport>> list({String? status}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('chat-message-reports/', queryParameters: {
        if (status != null) 'status': status,
      });
      return PaginatedList.fromJson(res.data, ChatMessageReport.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST chat-message-reports/{id}/review/ — platform staff only.
  Future<ChatMessageReport> review(int id, {required String status, String adminNote = ''}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('chat-message-reports/$id/review/', data: {
        'status': status,
        'admin_note': adminNote,
      });
      return ChatMessageReport.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 2. SCHEDULES
// ---------------------------------------------------------------------------
class ScheduleApi {
  Future<PaginatedList<ClassSchedule>> list({int? classroomId}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('schedules/', queryParameters: {
        if (classroomId != null) 'classroom': classroomId,
      });
      return PaginatedList.fromJson(res.data, ClassSchedule.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassSchedule> create(ClassSchedule schedule) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('schedules/', data: schedule.toJson());
      return ClassSchedule.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassSchedule> update(int id, ClassSchedule schedule) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('schedules/$id/', data: schedule.toJson());
      return ClassSchedule.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('schedules/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 3. SESSIONS
// ---------------------------------------------------------------------------
class SessionApi {
  Future<PaginatedList<ClassSession>> list({int? classroomId, String? status}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('sessions/', queryParameters: {
        if (classroomId != null) 'classroom': classroomId,
        if (status != null) 'status': status,
      });
      return PaginatedList.fromJson(res.data, ClassSession.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET sessions/{id}/engagement-report/ — teacher/co-teacher/moderator
  /// only. ⚠️ Path + shape unconfirmed (Pass 15 architecture skeleton, see
  /// frontend doc §1.7) — confirm exact URL against urls.py before shipping.
  Future<SessionEngagementReport> engagementReport(int sessionId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('sessions/$sessionId/engagement-report/');
      return SessionEngagementReport.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassSession> detail(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('sessions/$id/');
      return ClassSession.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassSession> create(ClassSession session) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('sessions/', data: session.toJson());
      return ClassSession.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassSession> update(int id, ClassSession session) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('sessions/$id/', data: session.toJson());
      return ClassSession.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('sessions/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/join/ — gated by a valid pass. Returns 202 +
  /// [SessionJoinResult.waitlisted]=true if the room is full.
  Future<SessionJoinResult> join(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('sessions/$id/join/');
      if (res.statusCode == 202) {
        return SessionJoinResult(roomId: '', role: 'student', livekitRole: '', livekitUrl: '', livekitToken: '', waitlisted: true);
      }
      return SessionJoinResult.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/token/ — fresh reconnect token, no participant row.
  Future<SessionJoinResult> freshToken(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('sessions/$id/token/');
      return SessionJoinResult.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/end/ — teacher/co-teacher/moderator only.
  Future<void> end(int id) async {
    try {
      final dio = await _Http.client();
      await dio.post('sessions/$id/end/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/kick/{userId}/ — teacher/co-teacher/moderator only.
  Future<void> kick(int sessionId, int userId) async {
    try {
      final dio = await _Http.client();
      await dio.post('sessions/$sessionId/kick/$userId/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/mute/{userId}/ — teacher/co-teacher/moderator only.
  /// Force-mutes (or, with [muted]=false, un-mutes) a participant's mic
  /// without removing them from the room — unlike [kick], this is a live
  /// audio toggle, not a moderation action.
  Future<void> muteParticipant(int sessionId, int userId, {bool muted = true}) async {
    try {
      final dio = await _Http.client();
      await dio.post('sessions/$sessionId/mute/$userId/', data: {'muted': muted});
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/hand/ — any active participant, raises/lowers
  /// THEIR OWN hand. Returns the server's resulting hand_raised state.
  Future<bool> setHandRaised(int sessionId, {bool raised = true}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('sessions/$sessionId/hand/', data: {'raised': raised});
      return res.data['hand_raised'] ?? raised;
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/hand/{userId}/lower/ — teacher/co-teacher/moderator
  /// only. Lowers someone ELSE's raised hand (e.g. after acknowledging it).
  Future<void> lowerHand(int sessionId, int userId) async {
    try {
      final dio = await _Http.client();
      await dio.post('sessions/$sessionId/hand/$userId/lower/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/start-recording/ — teacher/co-teacher/moderator
  /// only. The finished file's URL fills in on the session asynchronously
  /// (once LiveKit's webhook confirms it) — this call just starts the job.
  Future<void> startRecording(int sessionId) async {
    try {
      final dio = await _Http.client();
      await dio.post('sessions/$sessionId/start-recording/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/stop-recording/ — teacher/co-teacher/moderator only.
  Future<void> stopRecording(int sessionId) async {
    try {
      final dio = await _Http.client();
      await dio.post('sessions/$sessionId/stop-recording/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET sessions/{id}/unread/ — `{"chat": N, "polls": N}`, a real DB count
  /// against the caller's `SessionReadState` watermark (Pass 13). Only
  /// meaningful for a session the caller has already joined at least once.
  Future<SessionUnreadCount> unread(int sessionId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('sessions/$sessionId/unread/');
      return SessionUnreadCount.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/mark-read/ — advances the caller's read watermark.
  /// Omit both ids to mark everything that currently exists as read (the
  /// backend's documented default); pass either id to advance just that
  /// half, since chat and polls are tracked independently.
  Future<void> markRead(int sessionId, {int? lastReadChatMessageId, int? lastSeenPollId}) async {
    try {
      final dio = await _Http.client();
      await dio.post('sessions/$sessionId/mark-read/', data: {
        if (lastReadChatMessageId != null) 'last_read_chat_message_id': lastReadChatMessageId,
        if (lastSeenPollId != null) 'last_seen_poll_id': lastSeenPollId,
      });
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 3B. BREAKOUT ROOMS
//
// Backend for live_session_screen.dart's breakout-rooms UI — the screen was
// already calling `LiveClassApi.breakoutRooms.*` assuming exactly this
// shape (see that file's header comment); these endpoints now exist
// server-side (see urls.py/views.py's `sessions/{id}/breakout/*`).
// ---------------------------------------------------------------------------
class BreakoutRoomApi {
  /// GET sessions/{id}/breakout/ — current layout; [] = no breakout running.
  /// Readable by any participant with room access, not just the host.
  Future<List<BreakoutRoom>> list(int sessionId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('sessions/$sessionId/breakout/');
      return (res.data as List).map((e) => BreakoutRoom.fromJson(e)).toList();
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/breakout/ {"room_count": n} — teacher/co-teacher/
  /// moderator only. 400s if a breakout is already running for this
  /// session (close it first).
  Future<List<BreakoutRoom>> create({required int sessionId, required int roomCount}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('sessions/$sessionId/breakout/', data: {'room_count': roomCount});
      return (res.data as List).map((e) => BreakoutRoom.fromJson(e)).toList();
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/breakout/assign/ — teacher/co-teacher/moderator
  /// only. [participantId] is the SessionParticipant row id (same
  /// convention as kick/mute/lower-hand); pass [roomNumber] = null to move
  /// someone back to the main room.
  Future<List<BreakoutRoom>> assign({required int sessionId, required int participantId, int? roomNumber}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('sessions/$sessionId/breakout/assign/', data: {
        'participant_id': participantId,
        'room': roomNumber,
      });
      return (res.data as List).map((e) => BreakoutRoom.fromJson(e)).toList();
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/breakout/close/ — teacher/co-teacher/moderator
  /// only. Ends the breakout; everyone moves back to the main room.
  Future<void> close(int sessionId) async {
    try {
      final dio = await _Http.client();
      await dio.post('sessions/$sessionId/breakout/close/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 4. PASSES
// ---------------------------------------------------------------------------
class ClassPassApi {
  Future<PaginatedList<ClassPass>> list({int? classroomId}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('passes/', queryParameters: {
        if (classroomId != null) 'classroom': classroomId,
      });
      return PaginatedList.fromJson(res.data, ClassPass.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassPass> create(ClassPass pass) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('passes/', data: pass.toJson());
      return ClassPass.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// PATCH passes/{id}/ — blocked server-side if it would retroactively
  /// shrink what an active paid holder already bought (price up,
  /// validity_days/max_classes/pass_type down) while purchases are active.
  Future<ClassPass> update(int id, ClassPass pass) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('passes/$id/', data: pass.toJson());
      return ClassPass.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// DELETE passes/{id}/ — refused outright once ever purchased; PATCH
  /// is_active=false to pause instead.
  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('passes/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 5B. JOIN REQUESTS — the only door into a classroom for a non-owner
// ---------------------------------------------------------------------------
class JoinRequestApi {
  /// GET join-requests/ — own requests (student), or every request on
  /// [classroomId] (teacher/co-teacher/moderator), optionally + [status].
  Future<PaginatedList<ClassJoinRequest>> list({int? classroomId, String? status}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('join-requests/', queryParameters: {
        if (classroomId != null) 'classroom': classroomId,
        if (status != null) 'status': status,
      });
      return PaginatedList.fromJson(res.data, ClassJoinRequest.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassJoinRequest> detail(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('join-requests/$id/');
      return ClassJoinRequest.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST join-requests/ — student requests to join, against a specific
  /// ClassPass. Does NOT charge coins or grant access yet.
  Future<ClassJoinRequest> request({
    required int classroomId,
    required int classPassId,
    String couponCode = '',
    String message = '',
  }) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post(
        'join-requests/',
        data: ClassJoinRequest.createBody(
          classroomId: classroomId,
          classPassId: classPassId,
          couponCode: couponCode,
          message: message,
        ),
      );
      return ClassJoinRequest.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST join-requests/{id}/accept/ — teacher/co-teacher/moderator only.
  /// Charges coins and creates the PassPurchase.
  Future<ClassJoinRequest> accept(int id, {String note = ''}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('join-requests/$id/accept/', data: {'note': note});
      return ClassJoinRequest.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST join-requests/{id}/reject/ — no charge.
  Future<ClassJoinRequest> reject(int id, {String note = ''}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('join-requests/$id/reject/', data: {'note': note});
      return ClassJoinRequest.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST join-requests/{id}/cancel/ — requesting student only, while pending.
  Future<ClassJoinRequest> cancel(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('join-requests/$id/cancel/');
      return ClassJoinRequest.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 5. PASS PURCHASES (refund is teacher/staff; cancel is the student's own)
// ---------------------------------------------------------------------------
class PassPurchaseApi {
  Future<PaginatedList<PassPurchase>> myPurchases() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('pass-purchases/');
      return PaginatedList.fromJson(res.data, PassPurchase.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET pass-purchases/?classroom=<id> — that classroom's teacher/
  /// co-teacher/moderator only (backend enforces via _can_manage_classroom).
  /// Powers the teacher-side purchases/refund roster.
  Future<PaginatedList<PassPurchase>> forClassroom(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('pass-purchases/', queryParameters: {'classroom': classroomId});
      return PaginatedList.fromJson(res.data, PassPurchase.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<PassPurchase> detail(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('pass-purchases/$id/');
      return PassPurchase.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST pass-purchases/{id}/refund/ — classroom's teacher/co-teacher/
  /// moderator, or platform staff only.
  ///
  /// NOTE (fix — stale doc comment): under the current per-day escrow
  /// design (PassPurchase.reverse() in models.py) this credits the student
  /// only remaining_balance (coins still sitting in escrow, un-earned by
  /// the teacher) — NOT coins_spent, and there is no teacher clawback at
  /// all any more. The teacher only ever received coins_released for days
  /// actually taught, so there's nothing to take back from them. Old
  /// comment described the earlier lump-sum design and was never updated.
  Future<void> refund(int id) async {
    try {
      final dio = await _Http.client();
      await dio.post('pass-purchases/$id/refund/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST pass-purchases/{id}/cancel/ — the purchasing student ONLY,
  /// self-service. Same refund semantics as refund() above (only
  /// remaining_balance comes back — whatever's still in escrow for days
  /// nobody's taught yet; coins already released to the teacher for
  /// classes actually held stay with the teacher). No dedicated screen
  /// called this before — see MyPassesScreen's Cancel Pass action.
  /// PATCH pass-purchases/{id}/ (auto_renew field) — ⚠️ endpoint shape
  /// unconfirmed (Pass 15 architecture skeleton, frontend doc §1.8): may
  /// turn out to be its own `.../set-auto-renew/` action instead of a
  /// plain field PATCH. Using the plain-PATCH form here since it matches
  /// this file's default convention and nothing in the change log
  /// suggests a dedicated action was added.
  Future<PassPurchase> setAutoRenew(int id, bool autoRenew) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('pass-purchases/$id/', data: {'auto_renew': autoRenew});
      return PassPurchase.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> cancel(int id) async {
    try {
      final dio = await _Http.client();
      await dio.post('pass-purchases/$id/cancel/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 6. SESSION PARTICIPANTS (attendance)
// ---------------------------------------------------------------------------
class ParticipantApi {
  Future<PaginatedList<SessionParticipant>> list({int? sessionId}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('participants/', queryParameters: {
        if (sessionId != null) 'session': sessionId,
      });
      return PaginatedList.fromJson(res.data, SessionParticipant.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> leave(int id) async {
    try {
      final dio = await _Http.client();
      await dio.post('participants/$id/leave/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 7. MATERIALS
// ---------------------------------------------------------------------------
class MaterialApi {
  Future<PaginatedList<ClassMaterial>> list({required int classroomId, int? sessionId}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('materials/', queryParameters: {
        'classroom': classroomId,
        if (sessionId != null) 'session': sessionId,
      });
      return PaginatedList.fromJson(res.data, ClassMaterial.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST materials/ — [filePath] required unless [materialType] is "link".
  Future<ClassMaterial> upload({
    required int classroomId,
    int? sessionId,
    required String title,
    required String materialType,
    String? filePath,
    String externalLink = '',
  }) async {
    try {
      final dio = await _Http.client();
      final data = FormData.fromMap({
        'classroom': classroomId,
        if (sessionId != null) 'session': sessionId,
        'title': title,
        'material_type': materialType,
        if (externalLink.isNotEmpty) 'external_link': externalLink,
        if (filePath != null) 'file': await MultipartFile.fromFile(filePath),
      });
      final res = await dio.post('materials/', data: data);
      return ClassMaterial.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// PATCH materials/{id}/ — edit an existing material's title/link, or
  /// swap the file. classroom's teacher/co-teacher/moderator only
  /// (enforced server-side by ClassMaterialViewSet.perform_update).
  Future<ClassMaterial> update({
    required int id,
    String? title,
    String? materialType,
    String? filePath,
    String? externalLink,
  }) async {
    try {
      final dio = await _Http.client();
      final data = FormData.fromMap({
        if (title != null) 'title': title,
        if (materialType != null) 'material_type': materialType,
        if (externalLink != null) 'external_link': externalLink,
        if (filePath != null) 'file': await MultipartFile.fromFile(filePath),
      });
      final res = await dio.patch('materials/$id/', data: data);
      return ClassMaterial.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('materials/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 8. CHAT MESSAGES (live-session persisted chat; use the LiveKit data
// channel for realtime delivery — this is the history/reload path)
// ---------------------------------------------------------------------------
class ChatMessageApi {
  Future<PaginatedList<ChatMessage>> list(int sessionId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('chat-messages/', queryParameters: {'session': sessionId});
      return PaginatedList.fromJson(res.data, ChatMessage.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ChatMessage> send({required int sessionId, required String message}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('chat-messages/', data: {'session': sessionId, 'message': message});
      return ChatMessage.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('chat-messages/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  // -- Pass 12: reactions ---------------------------------------------------
  // POST chat-messages/{id}/react/ — sets/changes the caller's reaction
  // (upsertable, same "changing your answer" shape as PollApi.vote).
  // Gated behind the same room-access boundary as sending a chat message;
  // its own `chat_reaction` throttle scope (60/min, more generous than
  // `chat_message_create`'s 20/min since a reaction is a single tap).
  // Returns the updated message with the recomputed `reaction_counts`.
  Future<ChatMessage> react(int messageId, String emoji) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('chat-messages/$messageId/react/', data: {'emoji': emoji});
      return ChatMessage.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// DELETE chat-messages/{id}/react/ — removes the caller's own reaction.
  Future<ChatMessage> unreact(int messageId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.delete('chat-messages/$messageId/react/');
      return ChatMessage.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  // -- Pass 13: pinning ------------------------------------------------------
  // POST chat-messages/{id}/pin/ — host-only (_can_moderate_session, same
  // boundary as poll create/close). At most ONE pinned message per session
  // by construction: the backend unpins whichever was pinned before in the
  // same call, so a successful pin() here always fully replaces the prior
  // pin — no separate unpin() call needed for the message being replaced.
  Future<ChatMessage> pin(int messageId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('chat-messages/$messageId/pin/');
      return ChatMessage.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST chat-messages/{id}/unpin/ — host-only, clears the pin with no
  /// replacement.
  Future<ChatMessage> unpin(int messageId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('chat-messages/$messageId/unpin/');
      return ChatMessage.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 9. LIVE POLLS
// ---------------------------------------------------------------------------
class PollApi {
  Future<PaginatedList<LivePoll>> list(int sessionId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('polls/', queryParameters: {'session': sessionId});
      return PaginatedList.fromJson(res.data, LivePoll.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<LivePoll> create({required int sessionId, required String question, required List<String> options}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('polls/', data: {'session': sessionId, 'question': question, 'options': options});
      return LivePoll.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<PollResponse> vote(int pollId, int selectedOptionIndex) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('polls/$pollId/vote/', data: {'selected_option_index': selectedOptionIndex});
      return PollResponse.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<LivePoll> close(int pollId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('polls/$pollId/close/');
      return LivePoll.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST polls/quick-create/ — fires a saved `PollTemplate` into a live
  /// session in one call (Pass 13). Validates the template belongs to the
  /// session's own classroom and requires the same host-tier check as a
  /// regular create; broadcasts `poll.created` exactly like a manual create,
  /// so no separate handling is needed beyond the usual post-create refresh.
  Future<LivePoll> quickCreate({required int templateId, required int sessionId}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('polls/quick-create/', data: {'template': templateId, 'session': sessionId});
      return LivePoll.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 9B. QUICK-POLL TEMPLATES (Pass 13) — classroom-scoped CRUD, gated behind
// `_can_manage_classroom` (same boundary as Assignment/Notice/ClassHoliday).
// ---------------------------------------------------------------------------
class PollTemplateApi {
  Future<PaginatedList<PollTemplate>> list(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('poll-templates/', queryParameters: {'classroom': classroomId});
      return PaginatedList.fromJson(res.data, PollTemplate.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<PollTemplate> create(PollTemplate template) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('poll-templates/', data: template.toJson());
      return PollTemplate.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<PollTemplate> update(int id, PollTemplate template) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('poll-templates/$id/', data: template.toJson());
      return PollTemplate.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('poll-templates/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 10. ASSIGNMENTS + SUBMISSIONS
// ---------------------------------------------------------------------------
class AssignmentApi {
  Future<PaginatedList<Assignment>> list(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('assignments/', queryParameters: {'classroom': classroomId});
      return PaginatedList.fromJson(res.data, Assignment.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<Assignment> create(Assignment assignment, {String? attachmentPath}) async {
    try {
      final dio = await _Http.client();
      final body = assignment.toJson();
      final data = attachmentPath == null
          ? body
          : FormData.fromMap({...body, 'attachment': await MultipartFile.fromFile(attachmentPath)});
      final res = await dio.post('assignments/', data: data);
      return Assignment.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('assignments/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

class SubmissionApi {
  Future<PaginatedList<AssignmentSubmission>> list(int assignmentId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('submissions/', queryParameters: {'assignment': assignmentId});
      return PaginatedList.fromJson(res.data, AssignmentSubmission.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<AssignmentSubmission> submit({required int assignmentId, required String filePath}) async {
    try {
      final dio = await _Http.client();
      final data = FormData.fromMap({
        'assignment': assignmentId,
        'file': await MultipartFile.fromFile(filePath),
      });
      final res = await dio.post('submissions/', data: data);
      return AssignmentSubmission.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST submissions/{id}/grade/ — teacher only.
  Future<AssignmentSubmission> grade(int id, {required int score, String feedback = ''}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('submissions/$id/grade/', data: {'score': score, 'feedback': feedback});
      return AssignmentSubmission.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 11. REVIEWS
// ---------------------------------------------------------------------------
class ReviewApi {
  Future<PaginatedList<ClassroomReview>> list(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('reviews/', queryParameters: {'classroom': classroomId});
      return PaginatedList.fromJson(res.data, ClassroomReview.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassroomReview> create({required int classroomId, required int rating, String comment = ''}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('reviews/', data: {'classroom': classroomId, 'rating': rating, 'comment': comment});
      return ClassroomReview.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassroomReview> update(int id, {int? rating, String? comment}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('reviews/$id/', data: {
        if (rating != null) 'rating': rating,
        if (comment != null) 'comment': comment,
      });
      return ClassroomReview.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('reviews/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 11B. WISHLIST
// ---------------------------------------------------------------------------
class WishlistApi {
  Future<PaginatedList<ClassroomWishlistItem>> list() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('wishlist-classrooms/');
      return PaginatedList.fromJson(res.data, ClassroomWishlistItem.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassroomWishlistItem> add(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('wishlist-classrooms/', data: {'classroom_id': classroomId});
      return ClassroomWishlistItem.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> remove(int wishlistEntryId) async {
    try {
      final dio = await _Http.client();
      await dio.delete('wishlist-classrooms/$wishlistEntryId/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 12. COUPONS
// ---------------------------------------------------------------------------
class CouponApi {
  Future<PaginatedList<Coupon>> list({int? classroomId}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('coupons/', queryParameters: {
        if (classroomId != null) 'classroom': classroomId,
      });
      return PaginatedList.fromJson(res.data, Coupon.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<Coupon> create(Coupon coupon) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('coupons/', data: coupon.toJson());
      return Coupon.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<Coupon> update(int id, Coupon coupon) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('coupons/$id/', data: coupon.toJson());
      return Coupon.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('coupons/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET coupons/validate/?code=... — checks validity without spending it.
  Future<Coupon> validate(String code) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('coupons/validate/', queryParameters: {'code': code});
      return Coupon.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 13. COIN WALLET
// ---------------------------------------------------------------------------
class CoinTransactionApi {
  Future<PaginatedList<CoinTransaction>> myLedger() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('coin-transactions/');
      return PaginatedList.fromJson(res.data, CoinTransaction.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET coin-transactions/balance/ — real User.coin balance.
  Future<int> balance() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('coin-transactions/balance/');
      return res.data['balance'] ?? res.data['coin'] ?? 0;
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 14. CLASSROOM STAFF
// ---------------------------------------------------------------------------
class ClassroomStaffApi {
  Future<PaginatedList<ClassroomStaff>> list(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('staff/', queryParameters: {'classroom': classroomId});
      return PaginatedList.fromJson(res.data, ClassroomStaff.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassroomStaff> add({required int classroomId, required int userId, required String role}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('staff/',
          data: ClassroomStaff.createBody(classroomId: classroomId, userId: userId, role: role));
      return ClassroomStaff.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassroomStaff> updateRole(int id, String role) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('staff/$id/', data: {'role': role});
      return ClassroomStaff.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> remove(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('staff/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 15. WAITLIST
// ---------------------------------------------------------------------------
class WaitlistApi {
  /// No [sessionId] → caller's own waitlist entries (student "My Waitlist"
  /// view). With [sessionId] → every entry for that session, oldest first
  /// (teacher/co-teacher/moderator "who's waiting" view, used to decide who
  /// to promote next).
  Future<PaginatedList<SessionWaitlistEntry>> myEntries({int? sessionId}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('waitlist/', queryParameters: {
        if (sessionId != null) 'session': sessionId,
      });
      return PaginatedList.fromJson(res.data, SessionWaitlistEntry.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// Convenience alias for the teacher-side "who's waiting for this
  /// session" view — see [myEntries].
  Future<PaginatedList<SessionWaitlistEntry>> forSession(int sessionId) => myEntries(sessionId: sessionId);

  Future<void> leave(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('waitlist/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST waitlist/{id}/promote/ — teacher/co-teacher/moderator only, when a
  /// seat opens up.
  Future<void> promote(int id) async {
    try {
      final dio = await _Http.client();
      await dio.post('waitlist/$id/promote/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 16. CERTIFICATES
// ---------------------------------------------------------------------------
class CertificateApi {
  Future<PaginatedList<Certificate>> list({int? classroomId}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('certificates/', queryParameters: {
        if (classroomId != null) 'classroom': classroomId,
      });
      return PaginatedList.fromJson(res.data, Certificate.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST certificates/ — teacher/co-teacher/moderator only.
  Future<Certificate> issue({required int classroomId, required int studentId, String? certificateFilePath}) async {
    try {
      final dio = await _Http.client();
      final data = FormData.fromMap({
        'classroom': classroomId,
        'student': studentId,
        if (certificateFilePath != null) 'certificate_file': await MultipartFile.fromFile(certificateFilePath),
      });
      final res = await dio.post('certificates/', data: data);
      return Certificate.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 17. REMINDERS
// ---------------------------------------------------------------------------
class ReminderApi {
  Future<PaginatedList<ClassReminder>> list() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('reminders/');
      return PaginatedList.fromJson(res.data, ClassReminder.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassReminder> create(ClassReminder reminder) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('reminders/', data: reminder.toJson());
      return ClassReminder.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('reminders/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 18. HOLIDAYS
// ---------------------------------------------------------------------------
class HolidayApi {
  Future<PaginatedList<ClassHoliday>> list(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('holidays/', queryParameters: {'classroom': classroomId});
      return PaginatedList.fromJson(res.data, ClassHoliday.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassHoliday> create(ClassHoliday holiday) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('holidays/', data: holiday.toJson());
      return ClassHoliday.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('holidays/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 19. NOTICE BOARD
// ---------------------------------------------------------------------------
class NoticeApi {
  Future<PaginatedList<Notice>> list(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('notices/', queryParameters: {'classroom': classroomId});
      return PaginatedList.fromJson(res.data, Notice.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<Notice> create(Notice notice) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('notices/', data: notice.toJson());
      return Notice.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('notices/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<Notice> pin(int id) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('notices/$id/pin/');
      return Notice.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// 20. DOUBTS / QUERIES
// ---------------------------------------------------------------------------
class QueryApi {
  Future<PaginatedList<ClassQuery>> list(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('queries/', queryParameters: {'classroom': classroomId});
      return PaginatedList.fromJson(res.data, ClassQuery.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<ClassQuery> ask(ClassQuery query) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('queries/', data: query.toJson());
      return ClassQuery.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST queries/{id}/answer/ — teacher/co-teacher/moderator only.
  Future<ClassQuery> answer(int id, String answer) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('queries/$id/answer/', data: {'answer': answer});
      return ClassQuery.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// NOTIFICATIONS (bell icon)
// ---------------------------------------------------------------------------
class NotificationApi {
  Future<PaginatedList<AppNotification>> list({bool? isRead}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('notifications/', queryParameters: {
        if (isRead != null) 'is_read': isRead,
      });
      return PaginatedList.fromJson(res.data, AppNotification.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<int> unreadCount() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('notifications/unread-count/');
      return res.data['count'] ?? res.data['unread_count'] ?? 0;
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> delete(int id) async {
    try {
      final dio = await _Http.client();
      await dio.delete('notifications/$id/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> markRead(int id) async {
    try {
      final dio = await _Http.client();
      await dio.post('notifications/$id/mark-read/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  Future<void> markAllRead() async {
    try {
      final dio = await _Http.client();
      await dio.post('notifications/mark-all-read/');
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// NOTIFICATION PREFERENCES (Pass 14) — ⚠️ endpoint path assumed
// (`notification-preferences/`, singular resource, no id — one settings
// object per caller). Confirm against `urls.py` before shipping; same
// architecture-skeleton caveat as the `NotificationPreference` model itself.
// ---------------------------------------------------------------------------
class NotificationPreferenceApi {
  /// GET notification-preferences/ — the caller's current settings. If the
  /// caller has never saved any, expect either an empty object (client
  /// falls back to `NotificationPreference.forType`'s all-on default) or a
  /// pre-populated object with every `NotifType` already present —
  /// `fromJson` handles both since missing keys just fall through to the
  /// same default.
  Future<NotificationPreference> get() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('notification-preferences/');
      return NotificationPreference.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// PATCH notification-preferences/ — partial update; send only the
  /// `perType` entries that changed rather than the whole object where
  /// possible, to avoid clobbering a concurrent change from another device.
  Future<NotificationPreference> update(NotificationPreference prefs) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('notification-preferences/', data: prefs.toJson());
      return NotificationPreference.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// PASS GIFTING (Pass 14) — ⚠️ ARCHITECTURE SKELETON. Exact endpoint paths
// need confirming against urls.py (frontend doc §1.3) — the shapes below
// follow this file's own detail-action convention (`.../claim/`) used
// elsewhere (`.../join/`, `.../token/`, etc.) as the best guess.
// ---------------------------------------------------------------------------
class PassGiftApi {
  /// POST pass-gifts/ — gift a pass you own/can buy to someone else.
  /// [recipient] is a raw identifier (username or email) — this module has
  /// no user-search endpoint anywhere (frontend doc §8), same limitation
  /// reused here rather than inventing one.
  Future<PassGift> send({required int classPassId, required String recipient}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('pass-gifts/', data: {
        'class_pass': classPassId,
        'recipient': recipient,
      });
      return PassGift.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET pass-gifts/?direction=sent — gifts the caller has sent.
  Future<PaginatedList<PassGift>> myGiftsSent() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('pass-gifts/', queryParameters: {'direction': 'sent'});
      return PaginatedList.fromJson(res.data, PassGift.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET pass-gifts/?direction=received — gifts sent to the caller.
  Future<PaginatedList<PassGift>> myGiftsReceived() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('pass-gifts/', queryParameters: {'direction': 'received'});
      return PaginatedList.fromJson(res.data, PassGift.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST pass-gifts/{id}/claim/ — claims the gift; on success this is what
  /// actually creates the PassPurchase on the backend (mirrors how
  /// ClassJoinRequest.accept() does it elsewhere in this app).
  Future<PassGift> claim(int giftId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('pass-gifts/$giftId/claim/');
      return PassGift.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// REFERRAL PROGRAM — was fully implemented on the backend
// (ReferralViewSet in views.py) with no frontend caller anywhere in the
// module.
// ---------------------------------------------------------------------------
class ReferralApi {
  /// GET referrals/ — people the caller has successfully referred (own
  /// ledger only, same privacy posture as coin-transactions/).
  Future<PaginatedList<Referral>> list() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('referrals/');
      return PaginatedList.fromJson(res.data, Referral.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET referrals/my-code/ — own referral code + redemption tally.
  Future<MyReferralCode> myCode() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('referrals/my-code/');
      return MyReferralCode.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST referrals/redeem/ — redeem someone else's referral code. Only
  /// works once, and only within the new-account signup window; the
  /// backend's error message already explains why on a 400 (own code,
  /// already redeemed, window expired, invalid code) — surface
  /// [LiveClassApiException.message] to the user as-is.
  Future<void> redeem(String code) async {
    try {
      final dio = await _Http.client();
      await dio.post('referrals/redeem/', data: {'code': code});
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}