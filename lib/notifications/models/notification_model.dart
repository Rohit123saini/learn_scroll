// lib/features/notifications/models/notification_model.dart
//
// Backend: `core` app — `NotificationSerializer` (poora read-only serializer hai,
// koi client-writable field nahi). Fields yahan exactly usi shape se match karte hain:
// id, notif_type, title, message, classroom_id, session_id, data, is_read,
// read_at, created_at, source.
//
// `source` field backend khud compute karta hai (`get_source`) —
// "message" agar notif_type Notification.MESSAGE_APP_TYPES me ho, warna "tuitionclass".
// Frontend ko ye khud decide nahi karna — bas value ke hisaab se navigation route karna hai.

enum NotificationSource { message, tuitionclass, unknown }

NotificationSource _sourceFromString(String? value) {
  switch (value) {
    case 'message':
      return NotificationSource.message;
    case 'tuitionclass':
      return NotificationSource.tuitionclass;
    default:
      return NotificationSource.unknown;
  }
}

/// One entry of `NotificationSerializer.actors` (max 3, backend-capped).
class NotificationActor {
  final int id;
  final String username;
  final String? photoUrl;

  const NotificationActor({required this.id, required this.username, this.photoUrl});

  factory NotificationActor.fromJson(Map<String, dynamic> json) {
    return NotificationActor(
      id: json['id'] as int,
      username: json['username'] as String? ?? '',
      photoUrl: json['photo'] as String?,
    );
  }
}

class NotificationModel {
  final int id;
  final String notifType;
  final String title;
  final String message;
  final int? classroomId;
  final int? sessionId;
  final Map<String, dynamic>? data;
  final bool isRead;
  final DateTime? readAt;
  final DateTime createdAt;
  final NotificationSource source;

  // N3-BE additive fields — absent on old payloads, so all have safe defaults.
  final String category;
  final List<NotificationActor> actors;
  final int actorCount;
  final String? thumbnailUrl;

  NotificationModel({
    required this.id,
    required this.notifType,
    required this.title,
    required this.message,
    this.classroomId,
    this.sessionId,
    this.data,
    required this.isRead,
    this.readAt,
    required this.createdAt,
    required this.source,
    this.category = '',
    this.actors = const [],
    this.actorCount = 0,
    this.thumbnailUrl,
  });

  factory NotificationModel.fromJson(Map<String, dynamic> json) {
    return NotificationModel(
      id: json['id'] as int,
      notifType: json['notif_type'] as String? ?? '',
      title: json['title'] as String? ?? '',
      message: json['message'] as String? ?? '',
      classroomId: json['classroom_id'] as int?,
      sessionId: json['session_id'] as int?,
      data: (json['data'] as Map?)?.cast<String, dynamic>(),
      isRead: json['is_read'] as bool? ?? false,
      readAt: json['read_at'] != null
          ? DateTime.tryParse(json['read_at'] as String)
          : null,
      createdAt:
          DateTime.tryParse(json['created_at'] as String? ?? '') ??
              DateTime.now(),
      source: _sourceFromString(json['source'] as String?),
      category: json['category'] as String? ?? '',
      actors: (json['actors'] as List? ?? [])
          .whereType<Map>()
          .map((e) => NotificationActor.fromJson(e.cast<String, dynamic>()))
          .toList(),
      actorCount: json['actor_count'] as int? ?? 0,
      thumbnailUrl: json['thumbnail_url'] as String?,
    );
  }

  /// Optimistic local copy — mark-read UI update ke liye, bina naya object
  /// poora backend se refetch kiye.
  NotificationModel copyWithRead() {
    return NotificationModel(
      id: id,
      notifType: notifType,
      title: title,
      message: message,
      classroomId: classroomId,
      sessionId: sessionId,
      data: data,
      isRead: true,
      readAt: DateTime.now(),
      createdAt: createdAt,
      source: source,
      category: category,
      actors: actors,
      actorCount: actorCount,
      thumbnailUrl: thumbnailUrl,
    );
  }
}

/// `GET/PATCH core/notification-preferences/me/` — always exactly the
/// caller's own row (§ `NotificationPreferenceView`'s own docstring).
/// [Task 5] Model/service methods existed only as a URL comment before
/// this — never actually implemented, and nothing in the app ever let a
/// user change these. See `notification_settings_screen.dart`.
class NotificationPreferences {
  final bool pushEnabled;
  final bool emailEnabled;
  final bool smsEnabled;
  final bool whatsappEnabled;

  /// `Notification.NotifType` values (raw backend strings) the user has
  /// muted — an empty list means nothing is muted. The bell-row itself
  /// still gets created for a muted type; muting only turns off
  /// push/email/sms/whatsapp for it (see `allowed_channels_for` on the
  /// backend model). UI groups these into a handful of categories —
  /// see [NotificationCategory] — rather than showing 30-odd raw type
  /// strings.
  final List<String> mutedTypes;
  final String digestFrequency; // 'off' | 'daily' | 'weekly'

  /// [N9] Quiet hours — daily window as 'HH:mm' strings, interpreted
  /// server-side in [timezone] (IANA name). Both null = off. Push/SMS/
  /// WhatsApp are skipped inside the window (bell row + email still
  /// happen; calls / parent-device approvals bypass it).
  final String? quietStart;
  final String? quietEnd;
  final String timezone;

  /// [N9] One-off "pause all" deadline (server-computed, read-only).
  final DateTime? dndUntil;

  const NotificationPreferences({
    required this.pushEnabled,
    required this.emailEnabled,
    required this.smsEnabled,
    required this.whatsappEnabled,
    required this.mutedTypes,
    required this.digestFrequency,
    this.quietStart,
    this.quietEnd,
    this.timezone = 'UTC',
    this.dndUntil,
  });

  bool get quietHoursEnabled => quietStart != null && quietEnd != null;

  bool get isDndActive {
    final u = dndUntil;
    return u != null && u.isAfter(DateTime.now());
  }

  /// Backend sends 'HH:MM:SS' (or 'HH:MM'); null/garbage -> null.
  static String? _hhmm(dynamic v) {
    if (v is! String) return null;
    final parts = v.split(':');
    if (parts.length < 2) return null;
    final h = int.tryParse(parts[0]);
    final m = int.tryParse(parts[1]);
    if (h == null || m == null) return null;
    return '${h.toString().padLeft(2, '0')}:${m.toString().padLeft(2, '0')}';
  }

  factory NotificationPreferences.fromJson(Map<String, dynamic> json) {
    return NotificationPreferences(
      pushEnabled: json['push_enabled'] as bool? ?? true,
      emailEnabled: json['email_enabled'] as bool? ?? true,
      smsEnabled: json['sms_enabled'] as bool? ?? false,
      whatsappEnabled: json['whatsapp_enabled'] as bool? ?? false,
      mutedTypes: (json['muted_types'] as List? ?? []).map((e) => e.toString()).toList(),
      digestFrequency: json['digest_frequency'] as String? ?? 'off',
      quietStart: _hhmm(json['quiet_start']),
      quietEnd: _hhmm(json['quiet_end']),
      timezone: json['timezone'] as String? ?? 'UTC',
      dndUntil: json['dnd_until'] != null
          ? DateTime.tryParse(json['dnd_until'] as String)?.toLocal()
          : null,
    );
  }

  NotificationPreferences copyWith({
    bool? pushEnabled,
    bool? emailEnabled,
    bool? smsEnabled,
    bool? whatsappEnabled,
    List<String>? mutedTypes,
    String? digestFrequency,
    String? quietStart,
    String? quietEnd,
    String? timezone,
    DateTime? dndUntil,
    bool clearQuietHours = false,
    bool clearDndUntil = false,
  }) {
    return NotificationPreferences(
      pushEnabled: pushEnabled ?? this.pushEnabled,
      emailEnabled: emailEnabled ?? this.emailEnabled,
      smsEnabled: smsEnabled ?? this.smsEnabled,
      whatsappEnabled: whatsappEnabled ?? this.whatsappEnabled,
      mutedTypes: mutedTypes ?? this.mutedTypes,
      digestFrequency: digestFrequency ?? this.digestFrequency,
      quietStart: clearQuietHours ? null : (quietStart ?? this.quietStart),
      quietEnd: clearQuietHours ? null : (quietEnd ?? this.quietEnd),
      timezone: timezone ?? this.timezone,
      dndUntil: clearDndUntil ? null : (dndUntil ?? this.dndUntil),
    );
  }
}

/// User-facing grouping of `Notification.NotifType` (core/models.py) —
/// backend exposes 30+ raw type strings, way too granular for a settings
/// screen. Each category mutes/unmutes its whole member set together.
/// `GENERIC` is deliberately left out of every category — it's the
/// catch-all fallback bucket, muting it would risk silencing notification
/// types added later that don't fit an existing category yet.
enum NotificationCategory {
  tuitionClasses,
  assignmentsTests,
  messagesCalls,
  social,
  payments,
  campus,
}

extension NotificationCategoryTypes on NotificationCategory {
  List<String> get notifTypes {
    switch (this) {
      case NotificationCategory.tuitionClasses:
        return const [
          'session_reminder', 'session_live', 'session_cancelled', 'waitlist_promoted',
          'join_request_received', 'join_request_accepted', 'join_request_rejected',
          'classroom_flagged', 'classroom_shared', 'staff_added',
        ];
      case NotificationCategory.assignmentsTests:
        return const [
          'assigments_graded', 'assigments_posted', 'submission_received',
          'query_answered', 'certificate_issued',
        ];
      case NotificationCategory.messagesCalls:
        return const ['chat_message', 'mention', 'incoming_call', 'parent_device_pending'];
      case NotificationCategory.social:
        return const ['post_liked', 'post_commented', 'review_posted', 'report_reviewed', 'story_mention'];
      case NotificationCategory.payments:
        return const [
          'pass_refunded', 'pass_gift_received', 'pass_gift_claimed', 'pass_auto_renewed',
          'auto_renew_failed', 'pass_gift_expired', 'withdrawal_approved',
          'withdrawal_rejected', 'withdrawal_paid',
        ];
      case NotificationCategory.campus:
        return const ['notice_posted'];
    }
  }
}
/// Response shape: {"count": N, "unread_count": N, "results": [...]}
class NotificationListResponse {
  final int count;
  final int unreadCount;
  final List<NotificationModel> results;

  NotificationListResponse({
    required this.count,
    required this.unreadCount,
    required this.results,
  });

  factory NotificationListResponse.fromJson(Map<String, dynamic> json) {
    return NotificationListResponse(
      count: json['count'] as int? ?? 0,
      unreadCount: json['unread_count'] as int? ?? 0,
      results: (json['results'] as List? ?? [])
          .map((e) => NotificationModel.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}
