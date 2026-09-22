// ============================================================
// LIVECLASS — API CLIENT
//
// One thin wrapper class per the endpoint map documented in
// `liveclass/urls.py`'s module docstring. Every backend surface that
// the app is expected to call is represented here as a method, even
// if a screen for it isn't built yet in this pass — see
// GAP_ANALYSIS.md for which methods already have a screen wired to
// them vs. which are ready-to-use but still need a UI.
//
// Uses `package:http` on purpose (no extra dependency beyond what a
// typical Flutter project already ships) — swap the `http.Client` for
// a Dio instance later without touching call sites if the project
// already standardises on Dio elsewhere.
// ============================================================

import 'dart:convert';
import 'package:http/http.dart' as http;

class ApiException implements Exception {
  final int statusCode;
  final String code;
  final String message;
  ApiException(this.statusCode, this.code, this.message);
  @override
  String toString() => 'ApiException($statusCode, $code): $message';
}

/// Supplies the current access token; swap for your auth layer's
/// real token store (e.g. `AuthService.instance.accessToken`).
typedef TokenProvider = Future<String?> Function();

class LiveClassApi {
  final String baseUrl; // e.g. https://api.learnscroll.app/liveclass
  final TokenProvider getToken;
  final http.Client _client;

  LiveClassApi({
    required this.baseUrl,
    required this.getToken,
    http.Client? client,
  }) : _client = client ?? http.Client();

  Uri _u(String path, [Map<String, dynamic>? query]) {
    final q = query?.map((k, v) => MapEntry(k, v?.toString()))
      ?..removeWhere((k, v) => v == null);
    return Uri.parse('$baseUrl$path').replace(queryParameters: q?.isEmpty ?? true ? null : q);
  }

  Future<Map<String, String>> _headers({bool json = true}) async {
    final token = await getToken();
    return {
      if (json) 'Content-Type': 'application/json',
      'Accept': 'application/json',
      if (token != null && token.isNotEmpty) 'Authorization': 'Bearer $token',
    };
  }

  dynamic _decode(http.Response r) {
    final body = r.body.isEmpty ? null : jsonDecode(utf8.decode(r.bodyBytes));
    if (r.statusCode >= 200 && r.statusCode < 300) return body;
    // `liveclass/exceptions.py` normalises every error to one JSON shape:
    // {"error": {"code": "...", "message": "..."}}
    final err = (body is Map ? body['error'] : null) as Map?;
    throw ApiException(
      r.statusCode,
      err?['code']?.toString() ?? 'unknown_error',
      err?['message']?.toString() ?? (body is Map ? (body['detail'] ?? body['error'])?.toString() : null) ?? r.reasonPhrase ?? 'Request failed',
    );
  }

  /// Backend list endpoints are DRF-paginated: {count, next, previous, results}
  /// (project default `StandardPagination`, chat/ledger use `LiveClassPagination`).
  /// Every screen here expects a plain List, so `_get` unwraps that shape and
  /// follows `next` (capped) — non-list responses pass through untouched.
  static const int _maxPages = 20;

  static bool _isPage(dynamic b) => b is Map && b['results'] is List && b.containsKey('count');

  Uri _rebase(String next) {
    // `next` is built from the request's Host header; behind a proxy that can
    // be an internal scheme/host — force it onto the configured origin.
    final n = Uri.parse(next);
    final b = Uri.parse(baseUrl);
    return n.replace(scheme: b.scheme, host: b.host, port: b.hasPort ? b.port : null);
  }

  Future<List<dynamic>> _collectPages(Map first, {int maxPages = _maxPages}) async {
    final out = List<dynamic>.from(first['results'] as List);
    dynamic next = first['next'];
    var pages = 1;
    while (next is String && next.isNotEmpty && pages < maxPages) {
      final r = _decode(await _client.get(_rebase(next), headers: await _headers(json: false)));
      if (!_isPage(r)) break;
      out.addAll((r as Map)['results'] as List);
      next = r['next'];
      pages++;
    }
    return out;
  }

  Future<dynamic> _get(String path, {Map<String, dynamic>? query}) async {
    final body = _decode(await _client.get(_u(path, query), headers: await _headers(json: false)));
    return _isPage(body) ? await _collectPages(body as Map) : body;
  }

  Future<dynamic> _post(String path, {Map<String, dynamic>? body, Map<String, dynamic>? query}) async =>
      _decode(await _client.post(_u(path, query), headers: await _headers(), body: body == null ? null : jsonEncode(body)));

  Future<dynamic> _patch(String path, {Map<String, dynamic>? body}) async =>
      _decode(await _client.patch(_u(path), headers: await _headers(), body: jsonEncode(body ?? {})));

  Future<dynamic> _delete(String path) async =>
      _decode(await _client.delete(_u(path), headers: await _headers(json: false)));

  // ---------------- Dashboard / earnings / progress ----------------
  Future<Map<String, dynamic>> dashboard() async => await _get('/dashboard/');
  Future<Map<String, dynamic>> myEarnings({int? classroomId}) async =>
      await _get('/my-earnings/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> myProgress() async => await _get('/my-progress/');

  // ---------------- Classrooms ----------------
  Future<List<dynamic>> classrooms({String? search, String? language, bool? mine}) async =>
      await _get('/classrooms/', query: {'search': search, 'language': language, 'mine': mine});
  Future<Map<String, dynamic>> classroom(int id) async => await _get('/classrooms/$id/');
  Future<Map<String, dynamic>> createClassroom(Map<String, dynamic> body) async =>
      await _post('/classrooms/', body: body);
  Future<Map<String, dynamic>> updateClassroom(int id, Map<String, dynamic> body) async =>
      await _patch('/classrooms/$id/', body: body);
  Future<void> deleteClassroom(int id) async => await _delete('/classrooms/$id/');
  Future<void> closeClassroom(int id) async => await _post('/classrooms/$id/close/');
  Future<Map<String, dynamic>> hasAccess(int id) async => await _get('/classrooms/$id/has_access/');
  Future<Map<String, dynamic>> myPass(int id) async => await _get('/classrooms/$id/my-pass/');
  Future<Map<String, dynamic>> classroomStats(int id) async => await _get('/classrooms/$id/stats/');
  Future<Map<String, dynamic>> shareClassroom(int id, {int? toUserId}) async =>
      await _post('/classrooms/$id/share/', body: {'to_user_id': toUserId});
  Future<Map<String, dynamic>> shareStats(int id) async => await _get('/classrooms/$id/share-stats/');
  Future<Map<String, dynamic>> referLink(int id) async => await _get('/classrooms/$id/refer-link/');
  Future<Map<String, dynamic>> referralDashboard(int id) async => await _get('/classrooms/$id/referral-dashboard/');
  Future<Map<String, dynamic>> createChatGroup(int id) async => await _post('/classrooms/$id/create_group/');
  Future<Map<String, dynamic>> chatGroupStatus(int id) async => await _get('/classrooms/$id/group/');
  Future<List<dynamic>> recommendedClassrooms({int limit = 10}) async =>
      await _get('/classrooms/recommended/', query: {'limit': limit});

  // ---------------- Coin purchases (top-up) ----------------
  Future<List<dynamic>> coinPurchases() async => await _get('/coin-purchases/');
  Future<Map<String, dynamic>> initiateCoinPurchase(int coins) async =>
      await _post('/coin-purchases/initiate/', body: {'coins': coins});
  Future<Map<String, dynamic>> verifyCoinPurchase(int id, Map<String, dynamic> gatewayPayload) async =>
      await _post('/coin-purchases/$id/verify/', body: gatewayPayload);
  Future<Map<String, dynamic>> retryCoinPurchase(int id) async => await _post('/coin-purchases/$id/retry/');

  // ---------------- Schedules ----------------
  Future<List<dynamic>> schedules({int? classroomId}) async =>
      await _get('/schedules/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> createSchedule(Map<String, dynamic> body) async =>
      await _post('/schedules/', body: body);
  Future<Map<String, dynamic>> updateSchedule(int id, Map<String, dynamic> body) async =>
      await _patch('/schedules/$id/', body: body);
  Future<void> deleteSchedule(int id) async => await _delete('/schedules/$id/');

  // ---------------- Sessions ----------------
  Future<List<dynamic>> sessions({int? classroomId}) async =>
      await _get('/sessions/', query: {'classroom': classroomId});
  Future<List<dynamic>> liveNow({int limit = 10}) async => await _get('/sessions/live-now/', query: {'limit': limit});
  Future<Map<String, dynamic>> session(int id) async => await _get('/sessions/$id/');
  Future<Map<String, dynamic>> joinSession(int id) async => await _post('/sessions/$id/join/');
  Future<Map<String, dynamic>> refreshToken(int id) async => await _post('/sessions/$id/token/');
  Future<Map<String, dynamic>> parentJoin(int id, String parentToken) async =>
      await _post('/sessions/$id/parent-join/', body: {'parent_token': parentToken});
  Future<void> endSession(int id) async => await _post('/sessions/$id/end/');
  Future<void> kickParticipant(int sessionId, int userId) async => await _post('/sessions/$sessionId/kick/$userId/');
  Future<void> muteParticipant(int sessionId, int userId, {bool muted = true}) async =>
      await _post('/sessions/$sessionId/mute/$userId/', body: {'muted': muted});
  Future<void> raiseHand(int sessionId, {bool raised = true}) async =>
      await _post('/sessions/$sessionId/hand/', body: {'raised': raised});
  Future<void> lowerHandFor(int sessionId, int userId) async =>
      await _post('/sessions/$sessionId/hand/$userId/lower/');
  Future<Map<String, dynamic>> startRecording(int sessionId) async =>
      await _post('/sessions/$sessionId/start-recording/');
  Future<Map<String, dynamic>> stopRecording(int sessionId) async =>
      await _post('/sessions/$sessionId/stop-recording/');
  Future<List<dynamic>> breakoutRooms(int sessionId) async => await _get('/sessions/$sessionId/breakout/');
  Future<List<dynamic>> createBreakoutRooms(int sessionId, int roomCount) async =>
      await _post('/sessions/$sessionId/breakout/', body: {'room_count': roomCount});
  Future<void> assignBreakout(int sessionId, int participantId, int? room) async =>
      await _post('/sessions/$sessionId/breakout/assign/', body: {'participant_id': participantId, 'room': room});
  Future<void> closeBreakout(int sessionId) async => await _post('/sessions/$sessionId/breakout/close/');
  Future<Map<String, dynamic>> reactions(int sessionId) async => await _get('/sessions/$sessionId/reactions/');
  Future<void> sendReaction(int sessionId, String reaction) async =>
      await _post('/sessions/$sessionId/reactions/', body: {'reaction': reaction});
  Future<List<dynamic>> captions(int sessionId) async => await _get('/sessions/$sessionId/captions/');
  Future<void> appendCaption(int sessionId, String text) async =>
      await _post('/sessions/$sessionId/captions/', body: {'text': text});
  Future<Map<String, dynamic>> engagementReport(int sessionId) async =>
      await _get('/sessions/$sessionId/engagement-report/');
  Future<Map<String, dynamic>> unreadCount(int sessionId) async => await _get('/sessions/$sessionId/unread/');
  Future<void> markSessionRead(int sessionId) async => await _post('/sessions/$sessionId/mark-read/');

  // ---------------- Passes / join requests / purchases / gifts ----------------
  Future<List<dynamic>> passes(int classroomId) async => await _get('/passes/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> createPass(Map<String, dynamic> body) async => await _post('/passes/', body: body);
  Future<Map<String, dynamic>> updatePass(int id, Map<String, dynamic> body) async =>
      await _patch('/passes/$id/', body: body);

  Future<List<dynamic>> joinRequests({int? classroomId, String? status}) async =>
      await _get('/join-requests/', query: {'classroom': classroomId, 'status': status});
  Future<Map<String, dynamic>> createJoinRequest(int classPassId, {String? couponCode, String? message}) async =>
      await _post('/join-requests/', body: {
        'class_pass': classPassId,
        if (couponCode != null) 'coupon_code': couponCode,
        if (message != null) 'message': message,
      });
  Future<Map<String, dynamic>> acceptJoinRequest(int id) async => await _post('/join-requests/$id/accept/');
  Future<Map<String, dynamic>> rejectJoinRequest(int id) async => await _post('/join-requests/$id/reject/');
  Future<Map<String, dynamic>> cancelJoinRequest(int id) async => await _post('/join-requests/$id/cancel/');

  Future<List<dynamic>> passPurchases({int? classroomId}) async =>
      await _get('/pass-purchases/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> refundPurchase(int id) async => await _post('/pass-purchases/$id/refund/');
  Future<void> toggleAutoRenew(int id, bool autoRenew) async =>
      await _post('/pass-purchases/$id/toggle-auto-renew/', body: {'auto_renew': autoRenew});

  Future<List<dynamic>> passGifts() async => await _get('/pass-gifts/');
  Future<Map<String, dynamic>> sendPassGift(int recipientId, int classPassId, {String? giftMessage}) async =>
      await _post('/pass-gifts/', body: {
        'recipient_id': recipientId,
        'class_pass': classPassId,
        if (giftMessage != null) 'gift_message': giftMessage,
      });
  Future<Map<String, dynamic>> claimPassGift(int id) async => await _post('/pass-gifts/$id/claim/');
  Future<void> cancelPassGift(int id) async => await _post('/pass-gifts/$id/cancel/');

  // ---------------- Participants ----------------
  Future<List<dynamic>> participants(int sessionId) async =>
      await _get('/participants/', query: {'session': sessionId});
  Future<void> leaveSession(int participantId) async => await _post('/participants/$participantId/leave/');

  // ---------------- Materials ----------------
  Future<List<dynamic>> materials({int? classroomId, int? sessionId}) async =>
      await _get('/materials/', query: {'classroom': classroomId, 'session': sessionId});
  Future<Map<String, dynamic>> createMaterial(Map<String, dynamic> body) async =>
      await _post('/materials/', body: body);
  Future<void> deleteMaterial(int id) async => await _delete('/materials/$id/');

  // ---------------- Chat ----------------
  /// Chat history is chronological + paginated (oldest first), so page 1 is the
  /// OLDEST slice — a live view needs the LAST page(s). Returns the newest
  /// messages (up to ~2 pages), oldest → newest.
  Future<List<dynamic>> chatMessages(int sessionId, {String? search, int pageSize = 100}) async {
    final q = <String, dynamic>{'session': sessionId, 'search': search, 'page_size': pageSize};
    final first = _decode(await _client.get(_u('/chat-messages/', q), headers: await _headers(json: false)));
    if (!_isPage(first)) return first is List ? first : const [];
    final firstMap = first as Map;
    if (firstMap['next'] == null) return List<dynamic>.from(firstMap['results'] as List);
    final count = _int0(firstMap['count']);
    final lastPage = (count / pageSize).ceil();
    Future<List<dynamic>> page(int n) async {
      final r = _decode(await _client.get(_u('/chat-messages/', {...q, 'page': n}), headers: await _headers(json: false)));
      return _isPage(r) ? List<dynamic>.from((r as Map)['results'] as List) : <dynamic>[];
    }

    final items = await page(lastPage);
    if (items.length < pageSize ~/ 2 && lastPage > 1) {
      return [...await page(lastPage - 1), ...items];
    }
    return items;
  }

  static int _int0(dynamic v) => v is int ? v : int.tryParse('$v') ?? 0;

  /// One call: join the classroom's live session (or the next joinable one).
  /// Managers with nothing live get an ad-hoc class started server-side (201).
  /// Response: {session: {...}, room_id, participant_id, role, livekit_role,
  /// livekit_url, livekit_token, started_new?}. 404 `no_session` for students
  /// when nothing is live (surfaces as ApiException). 202 = waitlisted.
  Future<Map<String, dynamic>> startOrJoinClassroom(int classroomId) async =>
      Map<String, dynamic>.from(await _post('/classrooms/$classroomId/start-or-join/') as Map);
  Future<Map<String, dynamic>> sendChatMessage(int sessionId, String message, {int? replyTo}) async =>
      await _post('/chat-messages/', body: {'session': sessionId, 'message': message, if (replyTo != null) 'reply_to': replyTo});
  Future<void> deleteChatMessage(int id) async => await _delete('/chat-messages/$id/');
  Future<void> reactToMessage(int id, String reaction) async =>
      await _post('/chat-messages/$id/react/', body: {'reaction': reaction});
  Future<void> unreactToMessage(int id) async => await _delete('/chat-messages/$id/react/');
  Future<void> pinMessage(int id) async => await _post('/chat-messages/$id/pin/');
  Future<void> unpinMessage(int id) async => await _post('/chat-messages/$id/unpin/');
  Future<void> markMessageRead(int id) async => await _post('/chat-messages/$id/read/');
  Future<void> markReadUpTo(int sessionId, int messageId) async =>
      await _post('/chat-messages/mark-read/', body: {'session': sessionId, 'up_to': messageId});
  Future<List<dynamic>> readReceipts(int id) async => await _get('/chat-messages/$id/read-receipts/');

  Future<List<dynamic>> chatMessageReports({int? sessionId}) async =>
      await _get('/chat-message-reports/', query: {'session': sessionId});
  Future<Map<String, dynamic>> reportChatMessage(int messageId, String reason, {String? note}) async =>
      await _post('/chat-message-reports/', body: {'message': messageId, 'reason': reason, if (note != null) 'note': note});
  Future<void> reviewChatMessageReport(int id, String status) async =>
      await _post('/chat-message-reports/$id/review/', body: {'status': status});

  // ---------------- Polls ----------------
  Future<List<dynamic>> polls(int sessionId) async => await _get('/polls/', query: {'session': sessionId});
  Future<Map<String, dynamic>> createPoll(int sessionId, String question, List<String> options) async =>
      await _post('/polls/', body: {'session': sessionId, 'question': question, 'options': options});
  Future<void> votePoll(int id, int optionId) async => await _post('/polls/$id/vote/', body: {'selected_option_index': optionId});
  Future<void> closePoll(int id) async => await _post('/polls/$id/close/');
  Future<Map<String, dynamic>> quickCreatePoll(int sessionId, int templateId) async =>
      await _post('/polls/quick-create/', body: {'session': sessionId, 'template': templateId});
  Future<List<dynamic>> pollTemplates() async => await _get('/poll-templates/');

  // ---------------- Assignments / submissions / reviews ----------------
  Future<List<dynamic>> assignments({int? classroomId}) async =>
      await _get('/assigmentss/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> createAssignment(Map<String, dynamic> body) async =>
      await _post('/assigmentss/', body: body);
  /// Classroom submissions: a manager's grading queue (whole roster), or the
  /// caller's own rows. `classroom` is REQUIRED by the backend; `assigments`
  /// (uuid string) narrows to one assignment.
  ///
  /// Submitting / grading / publishing are NOT here: since the assignment
  /// merge those live on the unified app — `/assigments/submissions/{id}/…`
  /// (see lib/assignments/services/assignment_service.dart). The old
  /// `POST /liveclass/submissions/` and `…/grade/` no longer exist.
  Future<List<dynamic>> submissions({required int classroomId, String? assignmentId}) async =>
      await _get('/submissions/', query: {'classroom': classroomId, 'assigments': assignmentId});

  Future<List<dynamic>> reviews({int? classroomId}) async => await _get('/reviews/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> createReview(int classroomId, int rating, {String? comment}) async =>
      await _post('/reviews/', body: {'classroom': classroomId, 'rating': rating, if (comment != null) 'comment': comment});

  // ---------------- Wishlist / coupons / coins / withdrawals ----------------
  Future<List<dynamic>> wishlist() async => await _get('/wishlist-classrooms/');
  Future<void> addWishlist(int classroomId) async => await _post('/wishlist-classrooms/', body: {'classroom_id': classroomId});
  Future<void> removeWishlist(int id) async => await _delete('/wishlist-classrooms/$id/');

  Future<Map<String, dynamic>> validateCoupon(String code) async => await _get('/coupons/validate/', query: {'code': code});
  Future<List<dynamic>> coupons({int? classroomId}) async => await _get('/coupons/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> createCoupon(Map<String, dynamic> body) async => await _post('/coupons/', body: body);
  Future<Map<String, dynamic>> updateCoupon(int id, Map<String, dynamic> body) async => await _patch('/coupons/$id/', body: body);
  Future<void> deleteCoupon(int id) async => await _delete('/coupons/$id/');

  Future<List<dynamic>> coinTransactions() async => await _get('/coin-transactions/');
  Future<int> coinBalance() async {
    final res = await _get('/coin-transactions/balance/');
    // Backend: {"coin": <int>} (User.coin). Older shape: {"balance": <int>}.
    final v = res['coin'] ?? res['balance'];
    return v is int ? v : int.tryParse(v.toString()) ?? 0;
  }

  Future<List<dynamic>> withdrawals({String? status}) async => await _get('/withdrawals/', query: {'status': status});
  Future<Map<String, dynamic>> requestWithdrawal(int coins, String payoutMethod, Map<String, dynamic> payoutDetails) async =>
      await _post('/withdrawals/', body: {'coins': coins, 'payout_method': payoutMethod, 'payout_details': payoutDetails});
  Future<void> cancelWithdrawal(int id) async => await _post('/withdrawals/$id/cancel/');

  // ---------------- Waitlist / certificates / reminders / holidays / notices ----------------
  Future<List<dynamic>> waitlist() async => await _get('/waitlist/');
  Future<void> leaveWaitlist(int id) async => await _delete('/waitlist/$id/');
  Future<void> promoteWaitlist(int id) async => await _post('/waitlist/$id/promote/');

  Future<List<dynamic>> certificates({int? classroomId}) async =>
      await _get('/certificates/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> issueCertificate(int classroomId, int studentId) async =>
      await _post('/certificates/', body: {'classroom': classroomId, 'student': studentId});

  Future<List<dynamic>> reminders({int? classroomId}) async => await _get('/reminders/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> createReminder(Map<String, dynamic> body) async => await _post('/reminders/', body: body);

  Future<List<dynamic>> holidays(int classroomId) async => await _get('/holidays/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> createHoliday(Map<String, dynamic> body) async => await _post('/holidays/', body: body);

  Future<List<dynamic>> notices(int classroomId) async => await _get('/notices/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> createNotice(Map<String, dynamic> body) async => await _post('/notices/', body: body);
  Future<void> pinNotice(int id) async => await _post('/notices/$id/pin/');

  // ---------------- Doubts / queries ----------------
  Future<List<dynamic>> queries({int? classroomId}) async => await _get('/queries/', query: {'classroom': classroomId});
  Future<Map<String, dynamic>> askQuery(int classroomId, String question) async =>
      await _post('/queries/', body: {'classroom': classroomId, 'question': question});
  Future<void> answerQuery(int id, String answer) async => await _post('/queries/$id/answer/', body: {'answer': answer});

  // ---------------- Reports (classroom abuse) ----------------
  Future<List<dynamic>> classroomReports({int? classroomId, String? status}) async =>
      await _get('/classroom-reports/', query: {'classroom': classroomId, 'status': status});
  Future<Map<String, dynamic>> fileClassroomReport(int classroomId, String reason, {String? description}) async =>
      await _post('/classroom-reports/', body: {'classroom': classroomId, 'reason': reason, if (description != null) 'description': description});

  // ---------------- Parent portal ----------------
  Future<Map<String, dynamic>> generateParentCode(int classroomId, int userId) async =>
      await _post('/classrooms/$classroomId/participants/$userId/parent-code/');
  Future<List<dynamic>> parentQueries(int classroomId, {String? status}) async =>
      await _get('/classrooms/$classroomId/parent-queries/', query: {'status': status});
  Future<void> replyParentQuery(int id, String text, {bool close = false}) async =>
      await _post('/parent-queries/$id/reply/', body: {'text': text, 'close': close});
  Future<List<dynamic>> reportCards({int? classroomId}) async =>
      await _get('/report-cards/', query: {'classroom': classroomId});

  // ---------------- Referrals ----------------
  Future<List<dynamic>> referrals() async => await _get('/referrals/');
  Future<Map<String, dynamic>> myReferralCode() async => await _get('/referrals/my-code/');
  Future<Map<String, dynamic>> redeemReferral(String code) async => await _post('/referrals/redeem/', body: {'code': code});
  Future<Map<String, dynamic>> classReferralSummary() async => await _get('/referrals/class-referral-summary/');

  // ---------------- Health ----------------
  Future<bool> healthy() async {
    try {
      await _get('/healthz/');
      return true;
    } catch (_) {
      return false;
    }
  }
}
