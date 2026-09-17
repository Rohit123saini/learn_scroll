// lib/features/notifications/models/notification_model.dart
//
// Backend: `core` app — `NotificationSerializer` (poora read-only serializer hai,
// koi client-writable field nahi). Fields yahan exactly usi shape se match karte hain:
// id, notif_type, title, message, classroom_id, session_id, data, is_read,
// read_at, created_at, source.
//
// `source` field backend khud compute karta hai (`get_source`) —
// "message" agar notif_type Notification.MESSAGE_APP_TYPES me ho, warna "liveclass".
// Frontend ko ye khud decide nahi karna — bas value ke hisaab se navigation route karna hai.

enum NotificationSource { message, liveclass, unknown }

NotificationSource _sourceFromString(String? value) {
  switch (value) {
    case 'message':
      return NotificationSource.message;
    case 'liveclass':
      return NotificationSource.liveclass;
    default:
      return NotificationSource.unknown;
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
    );
  }
}

/// `GET core/notifications/` ka poora paginated response wrapper.
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
