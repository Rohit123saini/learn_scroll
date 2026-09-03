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

import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as ws_status;

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

  /// GET /liveclass/my-progress/ — the caller's own attendance/assignment/
  /// certificate stats + attendance streak (item 8 — student progress).
  static Future<StudentProgress> myProgress() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('my-progress/');
      return StudentProgress.fromJson(res.data);
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

  /// GET classrooms/{id}/recordings/ — ClassroomViewSet.recordings on the
  /// backend; a browsable, paginated list of this classroom's past
  /// recorded sessions (see classroom_recordings_screen.dart). Same access
  /// tier as materials/notices — teacher/staff/anyone who has ever held a
  /// pass, active or expired — not owner-only. Unlike `bans` above, this
  /// one DOES go through normal DRF pagination, same shape as every other
  /// list call in this file.
  Future<PaginatedList<SessionRecording>> recordings(int classroomId, {int page = 1}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/$classroomId/recordings/', queryParameters: {'page': page});
      return PaginatedList.fromJson(res.data, SessionRecording.fromJson);
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
  // -----------------------------------------------------------------
  // FEATURE (item 6 — share button + share count): backend
  // (ClassroomViewSet.share/.share_stats/.my_shares) was fully ready with
  // no frontend caller anywhere in the module.
  // -----------------------------------------------------------------

  /// POST classrooms/{id}/share/ — logs a share and bumps share_count.
  /// Pass [toUserId] for an in-app share (notifies that user; channel is
  /// forced to `in_app` server-side regardless of [channel]) — omit it for
  /// an outside-the-app share (WhatsApp/SMS/copy-link/native share sheet),
  /// where this call's only job is to hand back [ClassroomShareResult]'s
  /// web_url/deep_link/share_text for the OS's own share UI to use.
  Future<ClassroomShareResult> share(int classroomId, {int? toUserId, String? channel}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('classrooms/$classroomId/share/', data: {
        if (toUserId != null) 'to_user_id': toUserId,
        if (channel != null) 'channel': channel,
      });
      return ClassroomShareResult.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET classrooms/{id}/share-stats/ — teacher/co-teacher/moderator only:
  /// who's sharing this classroom, via which channel, behind share_count.
  Future<ClassroomShareStats> shareStats(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/$classroomId/share-stats/');
      return ClassroomShareStats.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET classrooms/my-shares/ — the caller's own share history, across
  /// every classroom they've ever shared.
  Future<PaginatedList<ClassroomMyShare>> myShares({int page = 1}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/my-shares/', queryParameters: {'page': page});
      return PaginatedList.fromJson(res.data, ClassroomMyShare.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  // -----------------------------------------------------------------
  // FEATURE (item 9 — refer & earn a classroom): the class-level
  // counterpart to ReferralApi (that's referring people to the app; this
  // is referring a specific classroom, paid out as a per-day commission —
  // see PassPurchaseApi.referralEarnings). 400s if the classroom's teacher
  // hasn't turned referral_enabled on — check Classroom.referralEnabled
  // before showing the "Refer & Earn" entry point to avoid a wasted call.
  // -----------------------------------------------------------------

  /// GET classrooms/{id}/refer-link/ — the caller's own referral link for
  /// this classroom + the commission rate it pays right now.
  Future<ReferLinkResult> referLink(int classroomId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/$classroomId/refer-link/');
      return ReferLinkResult.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  // -----------------------------------------------------------------
  // FEATURE (item 9 — personalized discovery): content-based recommender,
  // scored off the caller's own purchase + wishlist history (subject/
  // language match), falling back to rating/enrollment for a new user
  // with no history yet. Excludes classrooms already purchased.
  // -----------------------------------------------------------------

  /// GET classrooms/recommended/ — personalized "Recommended for you" feed.
  Future<PaginatedList<Classroom>> recommended({int limit = 10}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('classrooms/recommended/', queryParameters: {'limit': limit});
      return PaginatedList.fromJson(res.data, Classroom.fromJson);
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
// 1B. CHAT MESSAGE REPORTS (Pass 14) — FIXED (audit): confirmed against
// views.py (ChatMessageReportViewSet.perform_create/review).
// ---------------------------------------------------------------------------
class ChatMessageReportApi {
  /// POST chat-message-reports/ — any participant who's seen the message
  /// can file one (unlike ClassroomReport-style moderation actions, this
  /// is not host-only). FIXED: backend reads 'note', not 'description' —
  /// the old key meant every report's detail text was silently dropped
  /// (no error; the report was still created, just always with an empty
  /// note). Also confirmed: re-filing against the same message updates the
  /// existing row (unique_together message+reporter) rather than erroring.
  Future<ChatMessageReport> create({required int messageId, required String reason, String note = ''}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('chat-message-reports/', data: {
        'message': messageId,
        'reason': reason,
        'note': note,
      });
      return ChatMessageReport.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET chat-message-reports/ — item 7 (teacher review queue): CONFIRMED
  /// against ChatMessageReportViewSet.get_queryset — this endpoint is
  /// scoped by SESSION, not classroom (?session=<id>, requires
  /// teacher/co-teacher/moderator of that session). With no [sessionId],
  /// a non-staff caller only ever sees reports THEY personally filed —
  /// never a moderation queue. [status] filters either way.
  Future<PaginatedList<ChatMessageReport>> list({int? sessionId, String? status}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('chat-message-reports/', queryParameters: {
        if (sessionId != null) 'session': sessionId,
        if (status != null) 'status': status,
      });
      return PaginatedList.fromJson(res.data, ChatMessageReport.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST chat-message-reports/{id}/review/ — teacher/co-teacher/moderator,
  /// or platform staff. FIXED: the backend's review() only reads 'status'
  /// (actioning also soft-deletes the reported message) — there is no
  /// admin-note field on this action at all, so the old adminNote param
  /// was silently discarded on every call. Dropped it here rather than
  /// keep sending a value that goes nowhere; if a moderator note is
  /// actually needed, that has to be added on the backend first.
  Future<ChatMessageReport> review(int id, {required String status}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('chat-message-reports/$id/review/', data: {
        'status': status,
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

  /// POST sessions/{id}/whiteboard/ — any active participant. Checkpoints
  /// the whiteboard's full current stroke map to the server (or `null` to
  /// clear) so it survives everyone who held it in memory leaving the
  /// room. See live_session_screen.dart's whiteboard autosave for when
  /// this is called; live peer-to-peer sync between connected clients is
  /// unchanged. Silent on failure — same "best-effort autosave, next tick
  /// tries again" spirit as classroom_detail_screen's old stats poll.
  Future<void> saveWhiteboard(int sessionId, Map<String, dynamic>? snapshot) async {
    try {
      final dio = await _Http.client();
      await dio.post('sessions/$sessionId/whiteboard/', data: {'snapshot': snapshot});
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST sessions/{id}/spotlight/ — teacher/co-teacher/moderator only.
  /// Persists the host's pinned/spotlighted participant (or `null` to
  /// clear) so a reconnect/late join can read it straight off the session
  /// instead of only ever seeing it via a live peer broadcast.
  Future<void> setSpotlight(int sessionId, String? identity) async {
    try {
      final dio = await _Http.client();
      await dio.post('sessions/$sessionId/spotlight/', data: {'identity': identity});
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

  /// NEW (persistence fix) — POST sessions/{id}/reactions/. Logs one emoji
  /// tap server-side, in addition to (not instead of) the existing LiveKit
  /// data-channel broadcast in live_session_screen.dart's `_sendReaction` —
  /// live delivery to already-connected peers is unchanged; this just makes
  /// the tap durable. Returns the session's fresh running total so the
  /// caller can re-seed its own `_reactionTotalCount` badge. Silent on
  /// failure — same best-effort spirit as [saveWhiteboard]: a dropped
  /// reaction log is not worth interrupting the call over.
  Future<int?> logReaction(int sessionId, String reaction) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('sessions/$sessionId/reactions/', data: {'reaction': reaction});
      return SessionReactionSummary.fromJson(res.data).total;
    } on DioException catch (_) {
      return null;
    }
  }

  /// NEW (persistence fix) — GET sessions/{id}/reactions/. The session's
  /// running total + per-emoji counts so far — call this on join/reconnect
  /// to seed the reaction badge instead of starting it back at zero.
  Future<SessionReactionSummary> reactionSummary(int sessionId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('sessions/$sessionId/reactions/');
      return SessionReactionSummary.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// NEW (persistence fix) — POST sessions/{id}/captions/. Persists the
  /// caller's own just-finished on-device-STT caption line, in addition to
  /// (not instead of) the existing LiveKit data-channel broadcast in
  /// live_session_screen.dart's `_startCaptionListenBurst` — speaker is
  /// always resolved server-side from the authenticated caller. Silent on
  /// failure, same best-effort spirit as [logReaction]/[saveWhiteboard]: a
  /// caption line that fails to persist should never interrupt captioning.
  Future<void> logCaption(int sessionId, String text) async {
    try {
      final dio = await _Http.client();
      await dio.post('sessions/$sessionId/captions/', data: {'text': text});
    } on DioException catch (_) {
      // best-effort — the live data-channel broadcast already carried it
    }
  }

  /// NEW (persistence fix) — GET sessions/{id}/captions/. The session's
  /// transcript so far (oldest first, capped to the last 200 lines) —
  /// covers every participant's OWN recognized speech (each device still
  /// only transcribes its own mic), aggregated server-side. Call this on
  /// join/reconnect to catch up on what was said before the caller arrived.
  Future<List<SessionCaptionLine>> captionHistory(int sessionId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('sessions/$sessionId/captions/');
      return (res.data as List).map((e) => SessionCaptionLine.fromJson((e as Map).cast<String, dynamic>())).toList();
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
  /// POST pass-purchases/{id}/toggle-auto-renew/ — FIXED (audit): confirmed
  /// against urls.py -> PassPurchaseViewSet only mixes in List/Retrieve
  /// (no UpdateModelMixin), and auto_renew is deliberately read-only on
  /// PassPurchaseSerializer "so a student can't smuggle it in through a
  /// plain PATCH" (see toggle_auto_renew's docstring in views.py). A plain
  /// PATCH pass-purchases/{id}/ was 405ing every time — this must go
  /// through the dedicated action instead.
  /// unconfirmed (Pass 15 architecture skeleton, frontend doc §1.8): may
  /// turn out to be its own `.../set-auto-renew/` action instead of a
  /// plain field PATCH. Using the plain-PATCH form here since it matches
  /// this file's default convention and nothing in the change log
  /// suggests a dedicated action was added.
  Future<PassPurchase> setAutoRenew(int id, bool autoRenew) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('pass-purchases/$id/toggle-auto-renew/', data: {'auto_renew': autoRenew});
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

  /// GET pass-purchases/referral-earnings/ — item 9: the caller's own view
  /// of what their refer-links have brought in (own referrals only,
  /// referred_by == caller) — every purchase currently crediting them a
  /// commission, plus a running total so the screen doesn't have to sum
  /// coin-transaction rows itself.
  Future<ReferralEarnings> referralEarnings({int page = 1}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('pass-purchases/referral-earnings/', queryParameters: {'page': page});
      return ReferralEarnings.fromJson(res.data);
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
  /// [search] — NEW (message search): backend does a plain case-insensitive
  /// substring match over `message`, scoped to this [sessionId] (never
  /// cross-session — see ChatMessageViewSet.get_queryset in views.py). Pass
  /// null/blank for the normal unfiltered history load.
  Future<PaginatedList<ChatMessage>> list(int sessionId, {String? search}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('chat-messages/', queryParameters: {
        'session': sessionId,
        if (search != null && search.trim().isNotEmpty) 'search': search.trim(),
      });
      return PaginatedList.fromJson(res.data, ChatMessage.fromJson);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// [replyTo] — NEW (reply feature): id of an earlier message in the same
  /// session to quote. Omit for a normal, non-reply message. The backend
  /// validates the quoted message actually belongs to this session
  /// (ChatMessageSerializer.validate in serializers.py) — a mismatch comes
  /// back as a 400, surfaced to the caller as a LiveClassApiException same
  /// as any other validation error.
  Future<ChatMessage> send({required int sessionId, required String message, int? replyTo}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('chat-messages/', data: {
        'session': sessionId,
        'message': message,
        if (replyTo != null) 'reply_to': replyTo,
      });
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

  // -- NEW: read receipts ---------------------------------------------------
  // See ChatMessageRead's docstring in models.py for how this differs from
  // SessionsApi.markRead above (that's a single per-user unread-BADGE
  // watermark; this is per-message "who has actually seen it").

  /// POST chat-messages/{id}/read/ — mark one message as seen by the
  /// caller. Idempotent; safe to call again for an already-read message.
  Future<ChatMessage> markMessageRead(int messageId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('chat-messages/$messageId/read/');
      return ChatMessage.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// POST chat-messages/mark-read/ — bulk version: marks every not-yet-read,
  /// not-own message in [sessionId] up to and including [upToMessageId] as
  /// seen, in one call. This is what the chat panel should call when it's
  /// first opened (or scrolled to the bottom) instead of firing one
  /// [markMessageRead] per visible bubble. Returns how many NEW receipts
  /// were actually recorded (already-read messages don't count again).
  Future<int> markChatRead({required int sessionId, required int upToMessageId}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('chat-messages/mark-read/', data: {
        'session': sessionId,
        'up_to': upToMessageId,
      });
      final marked = res.data['marked_read'];
      return marked is int ? marked : int.parse(marked.toString());
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET chat-messages/{id}/read-receipts/ — full "seen by" list (who +
  /// when), oldest-first, for the sender (or anyone in the session) to tap
  /// a message and check.
  Future<List<ChatMessageReadReceipt>> readReceipts(int messageId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('chat-messages/$messageId/read-receipts/');
      return (res.data as List).map((e) => ChatMessageReadReceipt.fromJson(e as Map<String, dynamic>)).toList();
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

  /// PATCH polls/{id}/ — host-tier only (`_can_moderate_session` on the
  /// backend, same boundary as close()/vote()). Only `question`/`options`
  /// are meant to move here; `session` is fixed at creation and
  /// `is_active`/`closed_at` are only ever flipped via close().
  Future<LivePoll> update(int id, {String? question, List<String>? options}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('polls/$id/', data: {
        if (question != null) 'question': question,
        if (options != null) 'options': options,
      });
      return LivePoll.fromJson(res.data);
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

  /// PATCH assignments/{id}/ — classroom manager only
  /// (`_can_manage_classroom`, same boundary as create()/delete()).
  Future<Assignment> update(int id, Assignment assignment, {String? attachmentPath}) async {
    try {
      final dio = await _Http.client();
      final body = assignment.toJson();
      final data = attachmentPath == null
          ? body
          : FormData.fromMap({...body, 'attachment': await MultipartFile.fromFile(attachmentPath)});
      final res = await dio.patch('assignments/$id/', data: data);
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

  /// PATCH submissions/{id}/ — the submitting student only, and only the
  /// `file` (score/feedback stay teacher-only via grade()). Backend
  /// (AssignmentSubmissionViewSet.perform_update) rejects this once
  /// `graded_at` is set — a graded submission is frozen, same as it is for
  /// delete().
  Future<AssignmentSubmission> update(int id, {required String filePath}) async {
    try {
      final dio = await _Http.client();
      final data = FormData.fromMap({'file': await MultipartFile.fromFile(filePath)});
      final res = await dio.patch('submissions/$id/', data: data);
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

  /// PATCH reminders/{id}/ — always your own row; get_queryset() on the
  /// backend filters to `user=self.request.user` unconditionally, so no
  /// separate ownership check is needed there. `is_sent` stays read-only.
  Future<ClassReminder> update(int id, {DateTime? remindAt, String? channel}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('reminders/$id/', data: {
        if (remindAt != null) 'remind_at': remindAt.toIso8601String(),
        if (channel != null) 'channel': channel,
      });
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

  /// PATCH holidays/{id}/ — classroom manager only
  /// (`_can_manage_classroom`; ClassHolidayViewSet previously had no
  /// perform_update override at all — see the backend NOTE (fix) alongside
  /// it — closed as part of adding this wrapper).
  Future<ClassHoliday> update(int id, ClassHoliday holiday) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('holidays/$id/', data: holiday.toJson());
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

  /// PATCH notices/{id}/ — classroom manager only (`_can_manage_classroom`,
  /// same boundary as create()/delete()/pin()).
  Future<Notice> update(int id, Notice notice) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('notices/$id/', data: notice.toJson());
      return Notice.fromJson(res.data);
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

  /// PATCH queries/{id}/ — the original asker only, and only while the
  /// query is still open. ClassQuerySerializer keeps status/answer/
  /// answered_by/answered_at read-only, so `question` is the only field
  /// this can actually change; ClassQueryViewSet.perform_update (see
  /// backend NOTE (fix) alongside it — previously missing entirely) enforces
  /// both the ownership and the "not already answered" rule server-side.
  Future<ClassQuery> update(int id, String question) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('queries/$id/', data: {'question': question});
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
// NOTIFICATION PREFERENCES (Pass 14) — FIXED (audit): confirmed against
// urls.py -> NotificationPreferenceView is only reachable at
// `notification-preferences/me/`, not `notification-preferences/`. Every
// call below was 404ing before this fix.
// ---------------------------------------------------------------------------
class NotificationPreferenceApi {
  /// GET notification-preferences/me/ — the caller's current settings. If
  /// the caller has never saved any, expect either an empty object (client
  /// falls back to `NotificationPreference.forType`'s all-on default) or a
  /// pre-populated object with every `NotifType` already present —
  /// `fromJson` handles both since missing keys just fall through to the
  /// same default.
  Future<NotificationPreference> get() async {
    try {
      final dio = await _Http.client();
      final res = await dio.get('notification-preferences/me/');
      return NotificationPreference.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// PATCH notification-preferences/me/ — partial update; send only the
  /// `perType` entries that changed rather than the whole object where
  /// possible, to avoid clobbering a concurrent change from another device.
  Future<NotificationPreference> update(NotificationPreference prefs) async {
    try {
      final dio = await _Http.client();
      final res = await dio.patch('notification-preferences/me/', data: prefs.toJson());
      return NotificationPreference.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }
}

// ---------------------------------------------------------------------------
// PASS GIFTING (Pass 14) — FIXED (audit): confirmed against urls.py's
// documented body ({"recipient_id", "class_pass", "gift_message"}) and
// views.py's PassGiftViewSet.
// ---------------------------------------------------------------------------
class PassGiftApi {
  /// POST pass-gifts/ — gift a pass you own/can buy to someone else.
  /// FIXED: was sending 'recipient', backend expects 'recipient_id'
  /// (a numeric user id — NOT a raw username/email; there is still no
  /// user-search endpoint anywhere in this module (frontend doc §8), so
  /// the caller of send() must resolve a concrete user id some other way
  /// before calling this). [giftMessage] was previously not even a
  /// parameter here, even though the backend saves and shows it —
  /// added so the gift-message feature is actually reachable from the app.
  Future<PassGift> send({required int classPassId, required int recipientId, String giftMessage = ''}) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('pass-gifts/', data: {
        'class_pass': classPassId,
        'recipient_id': recipientId,
        'gift_message': giftMessage,
      });
      return PassGift.fromJson(res.data);
    } on DioException catch (e) {
      _throwFrom(e);
    }
  }

  /// GET pass-gifts/?direction=sent — gifts the caller has sent. (The
  /// ?direction filter previously had no effect on the backend — both
  /// this and myGiftsReceived() returned the same sent+received mix; now
  /// fixed server-side in PassGiftViewSet.get_queryset().)
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

  /// POST pass-gifts/{id}/cancel/ — item 10: gifter only, while still
  /// PENDING. Refunds the gifter's coins server-side
  /// (PassGift.refund_to_gifter()) — nothing further to do client-side
  /// besides refreshing the sent-gifts list.
  Future<PassGift> cancel(int giftId) async {
    try {
      final dio = await _Http.client();
      final res = await dio.post('pass-gifts/$giftId/cancel/');
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
// ---------------------------------------------------------------------------
// REALTIME — WebSocket client (Flutter Phase 1, item 1)
//
// Backend (`consumers.py`/`realtime.py`) pushes events over a per-SESSION
// channel: `participant.kicked`, `presence.snapshot`,
// `presence.joined`/`presence.left`, `chat.message`,
// `chat.message_deleted`, `chat.reaction`, `chat.pinned`/`chat.unpinned`,
// `poll.created`, `poll.updated`, `poll.closed`, `hand.raised`/
// `hand.lowered`, `recording.started`/`stopped`/`ready` (grep
// `broadcast_to_session` in views.py for the full list).
//
// Needs `web_socket_channel` in pubspec.yaml — confirmed present
// (`web_socket_channel: ^3.0.3`).
//
// WIRED IN (realtime fix pass): `live_session_screen.dart`'s
// `_onLiveSocketEvent` now listens for every event above and patches
// `_chatMessages`/`_polls`/`_participants`/`_handRaised`/`_isRecording`
// directly — the old 4s/6s/8s/10s REST poll timers for chat, polls, and
// the recording indicator were removed there in the same pass (the
// roster/mic-mute participants poll and the notice/battery/breakout
// timers stayed, since the backend doesn't broadcast those). See that
// file's `_onLiveSocketEvent` for the exact call sites.
// ---------------------------------------------------------------------------

/// One event received off a live-session WebSocket:
/// `{"type": "chat.message", "data": {...}}`. This envelope shape is an
/// ASSUMPTION — `consumers.py` wasn't part of this upload, so the exact
/// wrapping `broadcast_to_session` uses server-side couldn't be confirmed.
/// If the real consumer sends something else (e.g. the event dict
/// flattened with no separate "data" key), only [LiveSocketEvent.fromJson]
/// needs to change — every screen listening on [LiveClassSocket.events]
/// stays the same.
class LiveSocketEvent {
  final String type;
  final Map<String, dynamic> data;
  LiveSocketEvent(this.type, this.data);

  // FIX (realtime fix pass): the real backend envelope — both
  // SessionConsumer.session_event and UserConsumer.user_event (see
  // consumers.py) — sends `{"event": ..., "payload": ..., "ts": ...}`,
  // not a `{"type", "data"}` shape. This was parsing every real inbound
  // message as `type: ''`, `data: {}` (the `?? ''`/empty-map fallbacks
  // below silently swallowing the miss instead of throwing), which
  // matched no downstream `switch` case anywhere this stream is
  // consumed — every push looked like it "arrived" but was actually a
  // no-op. Field names on this class (`type`/`data`) are kept as-is so
  // every existing `e.type`/`e.data` call site (classroom_detail_
  // screen.dart, live_session_screen.dart, etc.) needs no change — only
  // the JSON keys read here change to match what the server actually
  // sends.
  factory LiveSocketEvent.fromJson(Map<String, dynamic> j) => LiveSocketEvent(
        j['event'] ?? '',
        (j['payload'] is Map) ? Map<String, dynamic>.from(j['payload'] as Map) : <String, dynamic>{},
      );
}

enum LiveSocketStatus { connecting, connected, reconnecting, disconnected }

/// Connects to a single live session's realtime channel and re-broadcasts
/// every event as a [Stream] so any screen/widget can just `.listen()` —
/// no REST refetch needed for anything the backend already pushes.
///
/// Usage (e.g. in `LiveSessionScreen`):
/// ```dart
/// final _socket = LiveClassSocket(widget.sessionId);
/// StreamSubscription<LiveSocketEvent>? _socketSub;
///
/// @override
/// void initState() {
///   super.initState();
///   _socket.connect();
///   _socketSub = _socket.events.listen((e) {
///     switch (e.type) {
///       case 'participant.kicked':
///         if (e.data['user_id'] == _myUserId) _leaveAndShowKickedDialog();
///         break;
///       case 'presence.snapshot':
///       case 'presence.joined':
///       case 'presence.left':
///         _updateOnlineAvatars(e);
///         break;
///       case 'chat.message':
///         _appendChatMessage(e.data);
///         break;
///       // ...poll.*, hand.*, recording.* etc.
///     }
///   });
/// }
///
/// @override
/// void dispose() {
///   _socketSub?.cancel();
///   _socket.dispose();
///   super.dispose();
/// }
/// ```
class LiveClassSocket {
  final int sessionId;
  WebSocketChannel? _channel;
  StreamSubscription? _channelSub;
  final _eventsController = StreamController<LiveSocketEvent>.broadcast();
  final _statusController = StreamController<LiveSocketStatus>.broadcast();

  bool _disposed = false;
  bool _manuallyDisconnected = false;
  int _reconnectAttempt = 0;
  Timer? _reconnectTimer;
  Timer? _pingTimer;

  LiveClassSocket(this.sessionId);

  /// Every event the backend pushes for this session, decoded. Survives
  /// reconnects — cancel your subscription and call [dispose] when the
  /// screen goes away, don't recreate [LiveClassSocket] per-reconnect.
  Stream<LiveSocketEvent> get events => _eventsController.stream;

  /// Connection lifecycle, for an optional small "reconnecting…" chip.
  Stream<LiveSocketStatus> get status => _statusController.stream;

  Future<void> connect() async {
    if (_disposed) return;
    _manuallyDisconnected = false;
    await _open();
  }

  Future<void> _open() async {
    _emitStatus(_reconnectAttempt == 0 ? LiveSocketStatus.connecting : LiveSocketStatus.reconnecting);

    final token = await AuthService.getToken();
    final wsBase = Api.baseUrl.replaceFirst('https://', 'wss://').replaceFirst('http://', 'ws://');
    // CONFIRMED (realtime fix pass) against the real ws_auth.py/
    // routing.py: `?token=` query-param JWT auth is correct — ws_auth.
    // JWTAuthMiddleware reads exactly that. The path below was also
    // wrong — routing.py's real route is `ws/liveclass/session/<id>/`
    // (this used to guess `ws/session/<id>/`, missing the `liveclass/`
    // prefix, which 404'd/closed every connect attempt immediately).
    final uri = Uri.parse('$wsBase/ws/liveclass/session/$sessionId/').replace(
      queryParameters: {if (token != null && token.isNotEmpty) 'token': token},
    );

    try {
      final channel = WebSocketChannel.connect(uri);
      _channel = channel;
      _channelSub = channel.stream.listen(
        _onMessage,
        onDone: _onDisconnect,
        onError: (_) => _onDisconnect(),
        cancelOnError: true,
      );
      _reconnectAttempt = 0;
      _emitStatus(LiveSocketStatus.connected);
      _startPing();
    } catch (_) {
      _onDisconnect();
    }
  }

  void _onMessage(dynamic raw) {
    try {
      final decoded = jsonDecode(raw as String) as Map<String, dynamic>;
      _eventsController.add(LiveSocketEvent.fromJson(decoded));
    } catch (_) {
      // Malformed/unrecognised frame — ignore rather than crash the stream.
    }
  }

  void _onDisconnect() {
    _pingTimer?.cancel();
    _channelSub?.cancel();
    _channel = null;
    if (_disposed || _manuallyDisconnected) {
      _emitStatus(LiveSocketStatus.disconnected);
      return;
    }
    _emitStatus(LiveSocketStatus.reconnecting);
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    _reconnectTimer?.cancel();
    // Exponential backoff: 1s, 2s, 4s, 8s, 16s, capped at 30s.
    const delays = [1, 2, 4, 8, 16, 30];
    final delaySeconds = delays[_reconnectAttempt.clamp(0, delays.length - 1)];
    _reconnectAttempt++;
    _reconnectTimer = Timer(Duration(seconds: delaySeconds), () {
      if (!_disposed && !_manuallyDisconnected) _open();
    });
  }

  void _startPing() {
    _pingTimer?.cancel();
    // Keeps NAT/proxy connections from silently timing out an idle socket.
    // Harmless if the backend doesn't expect a client ping frame, as long
    // as it ignores unrecognised frame types (same as this client does).
    _pingTimer = Timer.periodic(const Duration(seconds: 25), (_) {
      try {
        _channel?.sink.add(jsonEncode({'type': 'ping'}));
      } catch (_) {}
    });
  }

  void _emitStatus(LiveSocketStatus s) {
    if (!_statusController.isClosed) _statusController.add(s);
  }

  /// Send a client -> server event (e.g. a typing indicator). Envelope
  /// shape mirrors the assumed inbound one — verify against consumers.py.
  void send(String type, [Map<String, dynamic>? data]) {
    try {
      _channel?.sink.add(jsonEncode({'type': type, 'data': data ?? {}}));
    } catch (_) {}
  }

  Future<void> disconnect() async {
    _manuallyDisconnected = true;
    _reconnectTimer?.cancel();
    _pingTimer?.cancel();
    await _channelSub?.cancel();
    await _channel?.sink.close(ws_status.normalClosure);
    _channel = null;
    _emitStatus(LiveSocketStatus.disconnected);
  }

  void dispose() {
    _disposed = true;
    disconnect();
    _eventsController.close();
    _statusController.close();
  }
}

// ---------------------------------------------------------------------------
// REALTIME — per-user WebSocket client (Flutter Phase 1, items 2 & 3)
//
// For events that aren't tied to a live session at all — a teacher's
// pending-join-request badge (item 3), a student's own request flipping
// pending -> active/none (item 2), a mid-session staff promote (Phase 4
// item 2) — a SESSION channel is the wrong scope; these need a channel
// keyed by user id instead.
//
// CONFIRMED (realtime fix pass): the backend side now exists —
// `broadcast_to_user()` in realtime.py, `UserConsumer` in consumers.py,
// wired to routing.py's `ws/liveclass/user/$` route (matches the path
// below). Previously this connected to a guessed `/ws/user/`, which
// 404'd/closed immediately since that route never existed.
// ---------------------------------------------------------------------------
class LiveClassUserSocket {
  WebSocketChannel? _channel;
  StreamSubscription? _channelSub;
  final _eventsController = StreamController<LiveSocketEvent>.broadcast();
  bool _disposed = false;
  bool _manuallyDisconnected = false;
  int _reconnectAttempt = 0;
  Timer? _reconnectTimer;
  Timer? _pingTimer;

  Stream<LiveSocketEvent> get events => _eventsController.stream;

  Future<void> connect() async {
    if (_disposed) return;
    _manuallyDisconnected = false;
    await _open();
  }

  Future<void> _open() async {
    final token = await AuthService.getToken();
    final wsBase = Api.baseUrl.replaceFirst('https://', 'wss://').replaceFirst('http://', 'ws://');
    final uri = Uri.parse('$wsBase/ws/liveclass/user/').replace(
      queryParameters: {if (token != null && token.isNotEmpty) 'token': token},
    );
    try {
      final channel = WebSocketChannel.connect(uri);
      _channel = channel;
      _channelSub = channel.stream.listen(_onMessage, onDone: _onDisconnect, onError: (_) => _onDisconnect(), cancelOnError: true);
      _reconnectAttempt = 0;
      _startPing();
    } catch (_) {
      _onDisconnect();
    }
  }

  void _onMessage(dynamic raw) {
    try {
      final decoded = jsonDecode(raw as String) as Map<String, dynamic>;
      _eventsController.add(LiveSocketEvent.fromJson(decoded));
    } catch (_) {}
  }

  void _onDisconnect() {
    _pingTimer?.cancel();
    _channelSub?.cancel();
    _channel = null;
    if (_disposed || _manuallyDisconnected) return;
    _reconnectTimer?.cancel();
    const delays = [1, 2, 4, 8, 16, 30];
    final delaySeconds = delays[_reconnectAttempt.clamp(0, delays.length - 1)];
    _reconnectAttempt++;
    _reconnectTimer = Timer(Duration(seconds: delaySeconds), () {
      if (!_disposed && !_manuallyDisconnected) _open();
    });
  }

  void _startPing() {
    _pingTimer?.cancel();
    _pingTimer = Timer.periodic(const Duration(seconds: 25), (_) {
      try {
        _channel?.sink.add(jsonEncode({'type': 'ping'}));
      } catch (_) {}
    });
  }

  Future<void> disconnect() async {
    _manuallyDisconnected = true;
    _reconnectTimer?.cancel();
    _pingTimer?.cancel();
    await _channelSub?.cancel();
    await _channel?.sink.close(ws_status.normalClosure);
    _channel = null;
  }

  void dispose() {
    _disposed = true;
    disconnect();
    _eventsController.close();
  }
}

// ---------------------------------------------------------------------------
// REALTIME — per-CLASSROOM WebSocket client (fix — classroom stats push)
//
// classroom_detail_screen.dart's enrolled_count/rating used to be refreshed
// by a plain `Timer.periodic(30s)` GET (see that file's old `_statsTimer`)
// since there was no realtime push for it — unlike join-request badges,
// which already go over LiveClassUserSocket above. This is that push,
// scoped per-CLASSROOM (not per-user, since enrolled_count/rating are
// public info any current viewer of the classroom cares about, not just
// "my own" events) — mirrors LiveClassUserSocket exactly, just keyed by
// classroom id instead of by the caller's own identity.
//
// NEEDS A BACKEND COUNTERPART not present in this pass's uploaded files:
// `broadcast_to_classroom()` in realtime.py, a `ClassroomConsumer` in
// consumers.py, and a `ws/liveclass/classroom/<id>/` route in routing.py —
// see the docstring on `_broadcast_classroom_stats()` in models.py (added
// this same pass) for the exact shape expected, mirrored off this file's
// existing UserConsumer/`ws/liveclass/user/` pattern. Until those land,
// this class fails to connect and classroom_detail_screen.dart's longer
// backstop poll (see that file) is what actually keeps stats fresh.
// ---------------------------------------------------------------------------
class LiveClassClassroomSocket {
  final int classroomId;
  WebSocketChannel? _channel;
  StreamSubscription? _channelSub;
  final _eventsController = StreamController<LiveSocketEvent>.broadcast();
  bool _disposed = false;
  bool _manuallyDisconnected = false;
  int _reconnectAttempt = 0;
  Timer? _reconnectTimer;
  Timer? _pingTimer;

  LiveClassClassroomSocket(this.classroomId);

  Stream<LiveSocketEvent> get events => _eventsController.stream;

  Future<void> connect() async {
    if (_disposed) return;
    _manuallyDisconnected = false;
    await _open();
  }

  Future<void> _open() async {
    final token = await AuthService.getToken();
    final wsBase = Api.baseUrl.replaceFirst('https://', 'wss://').replaceFirst('http://', 'ws://');
    final uri = Uri.parse('$wsBase/ws/liveclass/classroom/$classroomId/').replace(
      queryParameters: {if (token != null && token.isNotEmpty) 'token': token},
    );
    try {
      final channel = WebSocketChannel.connect(uri);
      _channel = channel;
      _channelSub = channel.stream.listen(_onMessage, onDone: _onDisconnect, onError: (_) => _onDisconnect(), cancelOnError: true);
      _reconnectAttempt = 0;
      _startPing();
    } catch (_) {
      _onDisconnect();
    }
  }

  void _onMessage(dynamic raw) {
    try {
      final decoded = jsonDecode(raw as String) as Map<String, dynamic>;
      _eventsController.add(LiveSocketEvent.fromJson(decoded));
    } catch (_) {}
  }

  void _onDisconnect() {
    _pingTimer?.cancel();
    _channelSub?.cancel();
    _channel = null;
    if (_disposed || _manuallyDisconnected) return;
    _reconnectTimer?.cancel();
    const delays = [1, 2, 4, 8, 16, 30];
    final delaySeconds = delays[_reconnectAttempt.clamp(0, delays.length - 1)];
    _reconnectAttempt++;
    _reconnectTimer = Timer(Duration(seconds: delaySeconds), () {
      if (!_disposed && !_manuallyDisconnected) _open();
    });
  }

  void _startPing() {
    _pingTimer?.cancel();
    _pingTimer = Timer.periodic(const Duration(seconds: 25), (_) {
      try {
        _channel?.sink.add(jsonEncode({'type': 'ping'}));
      } catch (_) {}
    });
  }

  Future<void> disconnect() async {
    _manuallyDisconnected = true;
    _reconnectTimer?.cancel();
    _pingTimer?.cancel();
    await _channelSub?.cancel();
    await _channel?.sink.close(ws_status.normalClosure);
    _channel = null;
  }

  void dispose() {
    _disposed = true;
    disconnect();
    _eventsController.close();
  }
}