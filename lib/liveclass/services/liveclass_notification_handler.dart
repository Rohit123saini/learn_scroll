// lib/liveclass/services/liveclass_notification_handler.dart
//
// Push notifications for the `liveclass` app — same shape as
// `message/services/call_kit_service.dart`, but lighter: koi native
// full-screen CallKit popup nahi chahiye yahan (ye ek "ring" nahi hai), bas
// ek high-importance local notification jo tap karne pe seedha sahi screen
// khol de.
//
// ✅ BACKEND SIDE AB LIVE HAI (pehle yahan note tha ki kuch bhejta hi nahi —
// wo ab outdated hai): `liveclass.send_due_reminders` Celery Beat task
// (har 1 min, CELERY_BEAT_SCHEDULE me registered — tasks.py + settings.py
// dekho) due `ClassReminder` rows dhoondh ke `class_reminder` push bhejta
// hai. Isi tarah in sab events pe bhi ab actual FCM push jaata hai (pehle
// sirf bell-icon wali in-app `Notification` row banti thi, push kabhi nahi
// — views.py + tasks.py me fix kiya gaya):
//   join_request_received, join_request_accepted, join_request_rejected,
//   assignment_graded, certificate_issued, notice_posted, query_answered
// ...aur ye already push kar rahe the (tasks.py, pehle se): waitlist_seat_open,
// pass_refunded, classroom_flagged, session_auto_completed.
//
// ✅ NAYA (production notification coverage audit — dusra pass): in 6
// events pe pehle KUCH bhi nahi jaata tha — na push, na in-app bell row.
// waitlist_seat_open ka task khud pehle se maujood tha lekin use kahin se
// bhi call nahi kiya ja raha tha (dead code — CRITICAL bug, ab fix hai,
// views.py ka promote() action dekho):
//   session_live, session_cancelled, assignment_posted,
//   submission_received (teacher), staff_added, review_posted (teacher),
//   report_reviewed
//
// BACKEND CONTRACT — data payload per type (confirm/adjust field names
// against the real FCM payload if notifications.py's send_notification()
// ever changes its data shape):
//   class_reminder:         session_id, classroom_title, minutes_before, start_time
//   join_request_received:  join_request_id, classroom_id   (recipient: teacher)
//   join_request_accepted:  join_request_id, classroom_id   (recipient: student)
//   join_request_rejected:  join_request_id, classroom_id   (recipient: student)
//   assignment_graded:      submission_id, assignment_id, classroom_id
//   certificate_issued:     certificate_id, classroom_id
//   notice_posted:          notice_id, classroom_id
//   query_answered:         query_id, classroom_id
//   waitlist_seat_open:     session_id, classroom_id           (recipient: student)
//   pass_refunded:          classroom_id, pass_purchase_id
//   classroom_flagged:      classroom_id                       (recipient: teacher)
//   session_auto_completed: session_id, classroom_id           (recipient: teacher)
//   session_live:           session_id, classroom_id           (recipient: enrolled students)
//   session_cancelled:      classroom_id, session_id?          (recipient: enrolled students)
//   assignment_posted:      assignment_id, classroom_id        (recipient: enrolled students)
//   submission_received:    submission_id, assignment_id, classroom_id  (recipient: teacher)
//   staff_added:            classroom_id                       (recipient: added co-teacher/moderator)
//   review_posted:          classroom_id                       (recipient: teacher)
//   report_reviewed:        classroom_id                       (recipient: student who filed it)
//
// `class_reminder` AND `session_live` both get the special "jump straight
// into the live room" treatment below (LiveSessionScreen) — that's the one
// case where a generic "open the classroom" tap isn't good enough (the
// whole point of either push is not making the user find the join button
// again once they know class has started). Every other classroom-scoped
// type above opens `ClassroomDetailScreen` for `classroom_id`, which is
// enough context for the user to find whatever changed (new join request,
// grade, certificate, notice, answered doubt, new assignment/submission,
// staff addition, review, or report outcome).
//
// WIRING (2 chhoti hooks push_notification_service.dart me — neeche
// integration_instructions.md me exact diff hai):
//   1. `firebaseBackgroundHandler` me: data['type'] is one of
//      `_handledTypes` -> `LiveClassNotificationHandler.showBackground(data)`
//   2. `FirebaseMessaging.onMessage.listen` (foreground) me:
//      message.data['type'] is one of `_handledTypes` ->
//      `LiveClassNotificationHandler.instance.showForeground(message)`
//   3. `_handleNotificationResponse` (tap) me: data['type'] is one of
//      `_handledTypes` -> `LiveClassNotificationHandler.handleTap(data)`

import 'dart:convert';
import 'dart:developer' as developer;

import 'package:flutter/material.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../models/liveclass_models.dart' show NotifType;
import '../screens/live_session_screen.dart';
import '../screens/classroom_detail_screen.dart';
import '../screens/my_passes_screen.dart';
import '../screens/pass_gift_claim_screen.dart';
// Reuse the SAME global navigator key calling already uses — set once in
// main.dart via CallKitService.init(navKey). No need for a second key.
import '../../message/services/call_kit_service.dart';

/// Every liveclass push `type` this handler knows how to show/route.
/// push_notification_service.dart should check `_handledTypes.contains(...)`
/// (or just call in and let unknown types fall through to a default/no-op)
/// instead of hardcoding a single `== 'class_reminder'` check.
const Set<String> _handledTypes = {
  'class_reminder', // no NotifType constant — reminder push, not a bell-row Notification
  NotifType.joinRequestReceived,
  NotifType.joinRequestAccepted,
  NotifType.joinRequestRejected,
  NotifType.assignmentGraded,
  NotifType.certificateIssued,
  NotifType.noticePosted,
  NotifType.queryAnswered,
  'waitlist_seat_open', // no NotifType constant — see NotifType.waitlistPromoted for the bell-row equivalent
  NotifType.passRefunded,
  NotifType.classroomFlagged,
  'session_auto_completed', // no NotifType constant — teacher-only push, no bell row
  // Naye types (production notification coverage audit, dusra pass) —
  // dekho file-header comment upar exact data payload ke liye.
  NotifType.sessionLive,
  NotifType.sessionCancelled,
  NotifType.assignmentPosted,
  NotifType.submissionReceived,
  NotifType.staffAdded,
  NotifType.reviewPosted,
  NotifType.reportReviewed,
  // NEW (Pass 16 frontend catch-up §1.8) — 3 more types added alongside
  // auto-renew (Pass 15) and pass gifting (Pass 14). See models.dart's
  // NotifType for the same gap-fix note.
  NotifType.passAutoRenewed,
  NotifType.autoRenewFailed,
  NotifType.passGiftExpired,
  // FIX (push `type` vs NotifType vocabulary audit): these 3 push types
  // were being sent by the backend (tasks.notify_classroom_shared,
  // notify_pass_gift_received, notify_pass_gift_claimed) but were never
  // added here — meaning push_notification_service.dart's
  // `_handledTypes.contains(...)` gate silently swallowed them and a tap
  // on any of these 3 notification types did nothing at all. Now sourced
  // from NotifType (models.dart), which also gained these 3 constants.
  NotifType.classroomShared,
  NotifType.passGiftReceived,
  NotifType.passGiftClaimed,
};

/// These 3 aren't classroom-scoped (no `classroom_id` in payload) and
/// aren't session-scoped either, so neither of `handleTap`'s two existing
/// branches fits — they route to `MyPassesScreen` instead, which is the
/// one place a purchase/gift status actually lives. ⚠️ Tap target choice
/// per frontend doc §4/§1.8 — confirm with product before shipping;
/// `pass_gift_expired` in particular could arguably open a sent-gifts
/// view instead once `pass_gift_claim_screen.dart` exists.
const Set<String> _passLifecycleTapTypes = {
  NotifType.passAutoRenewed,
  NotifType.autoRenewFailed,
  NotifType.passGiftExpired,
};

/// FIX (PassGiftClaimScreen orphan — entry point #1 from that screen's own
/// header comment): `pass_gift_received`/`pass_gift_claimed` DO carry a
/// `classroom_id` (tasks.notify_pass_gift_received/notify_pass_gift_claimed,
/// see tasks.py), so before this fix they silently fell through to the
/// generic classroom_id branch below and opened ClassroomDetailScreen
/// instead — not wrong, but it skipped the one screen actually built for
/// this ("here's your gift, tap to claim it"). Route these to
/// PassGiftClaimScreen(giftId: ...) instead, using the `pass_gift_id` key
/// from the push payload (NOT `gift_id` — confirmed against tasks.py).
const Set<String> _giftTapTypes = {
  NotifType.passGiftReceived,
  NotifType.passGiftClaimed,
};

/// `class_reminder` aur `session_live` dono isi treatment ke hakdaar hain —
/// dono ka poora point hai user ko seedha live room me daalna, na ki use
/// classroom detail se phir se "Enter Class" dhoondhne dena.
const Set<String> _liveRoomTapTypes = {'class_reminder', NotifType.sessionLive};

class LiveClassNotificationHandler {
  LiveClassNotificationHandler._();
  static final instance = LiveClassNotificationHandler._();

  static const String channelId = 'class_reminders';
  static const String channelName = 'Class Reminders';
  static const String channelDescription =
      'Notifies you a few minutes before a live class you set a reminder for starts';

  final _fln = FlutterLocalNotificationsPlugin();

  /// App start pe ek baar call karo (PushNotificationService.instance.init()
  /// ke andar se, ya alag se) — sirf channel register karta hai. Actual
  /// FlutterLocalNotificationsPlugin.initialize() already
  /// push_notification_service.dart me ho chuka hota hai (ek hi plugin
  /// instance-per-isolate kaafi hai for foreground; background isolate apna
  /// khud ka banata hai, jaise neeche showBackground() me).
  Future<void> registerChannel() async {
    const channel = AndroidNotificationChannel(
      channelId,
      channelName,
      description: channelDescription,
      importance: Importance.high,
    );
    await _fln
        .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
        ?.createNotificationChannel(channel);
  }

  static String _titleFor(Map<String, dynamic> data) {
    final classroomTitle = data['classroom_title']?.toString() ?? 'Your class';
    // `session_live` payload (tasks.notify_session_live) doesn't carry
    // minutes_before — the server already puts a full title/body in the
    // FCM `notification` block for this type, so message.notification is
    // used first (see showForeground below); this is only the fallback if
    // that's ever missing.
    if (data['type']?.toString() == NotifType.sessionLive) {
      return '$classroomTitle is live now';
    }
    final minutesBefore = int.tryParse(data['minutes_before']?.toString() ?? '') ?? 0;
    if (minutesBefore <= 0) {
      return '$classroomTitle is starting now';
    }
    return '$classroomTitle starts in $minutesBefore min';
  }

  static const String _body = 'Tap to join the live class';

  /// Both class_reminder and session_live push the exact same "jump into
  /// the room" local notification shape — only the title copy differs
  /// (handled by _titleFor above). Everything else in _handledTypes is
  /// classroom-scoped, not session-scoped, and is expected to already be
  /// displayed by the generic notification path in
  /// push_notification_service.dart (this file only ever built the
  /// specialised session-join notification, never a generic one).
  static bool _isLiveRoomType(String? type) => _liveRoomTapTypes.contains(type);

  AndroidNotificationDetails _androidDetails() => const AndroidNotificationDetails(
        channelId,
        channelName,
        channelDescription: channelDescription,
        importance: Importance.high,
        priority: Priority.high,
        category: AndroidNotificationCategory.reminder,
      );

  /// Foreground me aaya push (app already khula hai). Sirf class_reminder
  /// aur session_live ke liye — dono "seedha room me jump karo" wale
  /// special-case notifications hain. Baaki sab types (join_request_*,
  /// assignment_posted, staff_added, wagera) generic notification path se
  /// already display ho rahe hain (push_notification_service.dart) — is
  /// handler ka un types ke liye kaam sirf tap-routing hai (handleTap).
  Future<void> showForeground(RemoteMessage message) async {
    final data = message.data;
    final type = data['type']?.toString();
    if (!_isLiveRoomType(type)) return;
    final sessionId = data['session_id']?.toString();
    if (sessionId == null || sessionId.isEmpty) return;

    try {
      await _fln.show(
        id: sessionId.hashCode,
        title: message.notification?.title ?? _titleFor(data),
        body: message.notification?.body ?? _body,
        notificationDetails: NotificationDetails(android: _androidDetails()),
        payload: jsonEncode({'type': type, 'session_id': sessionId}),
      );
    } catch (e) {
      developer.log('LiveClass reminder (foreground) show failed: $e');
    }
  }

  /// Background/killed isolate me aaya push — apna khud ka plugin instance
  /// banata hai, bilkul push_notification_service.dart ke
  /// `_showBackgroundChatNotification` jaisa (background isolate me class
  /// ka `_fln` field available nahi hota, isliye static/top-level rehna
  /// padta hai).
  static Future<void> showBackground(Map<String, dynamic> data) async {
    final type = data['type']?.toString();
    if (!_isLiveRoomType(type)) return;
    final sessionId = data['session_id']?.toString();
    if (sessionId == null || sessionId.isEmpty) return;

    try {
      final fln = FlutterLocalNotificationsPlugin();
      const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
      await fln.initialize(settings: const InitializationSettings(android: androidInit));

      const channel = AndroidNotificationChannel(
        channelId,
        channelName,
        description: channelDescription,
        importance: Importance.high,
      );
      await fln
          .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
          ?.createNotificationChannel(channel);

      await fln.show(
        id: sessionId.hashCode,
        title: _titleFor(data),
        body: _body,
        notificationDetails: NotificationDetails(
          android: AndroidNotificationDetails(
            channelId,
            channelName,
            channelDescription: channelDescription,
            importance: Importance.high,
            priority: Priority.high,
            category: AndroidNotificationCategory.reminder,
          ),
        ),
        payload: jsonEncode({'type': type, 'session_id': sessionId}),
      );
    } catch (e) {
      developer.log('LiveClass reminder (background) show failed: $e');
    }
  }

  /// Notification pe tap hone par route decide karta hai:
  ///   - class_reminder / session_live  -> seedha LiveSessionScreen (screen
  ///     khud `join()` call karke LiveKit token le lega — [initialResult]
  ///     null pass kar rahe hain).
  ///   - baaki har classroom-scoped type -> ClassroomDetailScreen(classroomId),
  ///     jo user ko jo bhi badla (naya join request, grade, certificate,
  ///     notice, doubt ka jawaab, naya assignment/submission, staff me add
  ///     hona, review, report ka outcome) dhoondhne ke liye kaafi context deta hai.
  ///
  /// NOTE (fix): pehle ye method HAR type ke liye sirf session_id dekh kar
  /// seedha LiveSessionScreen khol deta tha — sahi tha jab sirf
  /// class_reminder hi is handler se guzarta tha, lekin _handledTypes me
  /// ab 18 types hain jinme se zyadatar ka session se koi lena-dena nahi
  /// (classroom_flagged, staff_added, review_posted, wagera). `type` field
  /// pe branch karke fix kiya.
  static void handleTap(Map<String, dynamic> data) {
    final navigator = CallKitService.navigatorKey?.currentState;
    if (navigator == null) {
      developer.log('LiveClass notification tap: navigator not ready, cannot route.');
      return;
    }

    final type = data['type']?.toString();

    if (_liveRoomTapTypes.contains(type)) {
      final sessionId = int.tryParse(data['session_id']?.toString() ?? '');
      if (sessionId == null) return;
      navigator.push(MaterialPageRoute(
        builder: (_) => LiveSessionScreen(sessionId: sessionId),
      ));
      return;
    }

    if (_passLifecycleTapTypes.contains(type)) {
      navigator.push(MaterialPageRoute(
        builder: (_) => const MyPassesScreen(),
      ));
      return;
    }

    if (_giftTapTypes.contains(type)) {
      final giftId = int.tryParse(data['pass_gift_id']?.toString() ?? '');
      navigator.push(MaterialPageRoute(
        builder: (_) => PassGiftClaimScreen(giftId: giftId),
      ));
      return;
    }

    final classroomId = int.tryParse(data['classroom_id']?.toString() ?? '');
    if (classroomId == null) {
      developer.log('LiveClass notification tap: no classroom_id in payload for type $type — cannot route.');
      return;
    }
    navigator.push(MaterialPageRoute(
      builder: (_) => ClassroomDetailScreen(classroomId: classroomId),
    ));
  }
}