// lib/liveclass/screens/notifications_screen.dart
//
// Screen 23 — Notifications / Bell Icon (see
// LIVECLASS_SCREEN_ARCHITECTURE.md §23).
//
// Not part of the liveclass tab flow itself — this is meant to be pushed
// from the app's home app-bar bell icon (see LiveClassApi.dashboard()'s
// unread_notifications_count for the badge). Wire it up wherever that bell
// icon lives, e.g.:
//   IconButton(
//     icon: Badge(label: Text('$unreadCount'), child: const Icon(Icons.notifications_outlined)),
//     onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const NotificationsScreen())),
//   )
//
// API: `notifications/` list (own, newest first, ?is_read= filter),
// `unread-count/`, `{id}/mark-read/`, `mark-all-read/`, `{id}/` DELETE.
// Tapping an unread notification marks it read; tapping one with a
// classroom/session attached deep-links into that classroom (a session id
// alone still routes through Classroom Detail, since that's the only
// screen we have a stable route into here).
//
// Now on the shared LiveClass design system (liveclass_theme.dart).

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import 'classroom_detail_screen.dart';

String _fmtRelative(DateTime d) {
  final diff = DateTime.now().difference(d);
  if (diff.inMinutes < 1) return 'Just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  if (diff.inDays < 7) return '${diff.inDays}d ago';
  return liveClassFmtDate(d);
}

IconData _notifIcon(String type) {
  switch (type) {
    case NotifType.joinRequestReceived:
      return Icons.mail_outline_rounded;
    case NotifType.joinRequestAccepted:
      return Icons.check_circle_outline_rounded;
    case NotifType.joinRequestRejected:
      return Icons.cancel_outlined;
    case NotifType.passRefunded:
      return Icons.replay_rounded;
    case NotifType.sessionReminder:
      return Icons.alarm_rounded;
    case NotifType.assignmentGraded:
      return Icons.grading_rounded;
    case NotifType.queryAnswered:
      return Icons.question_answer_outlined;
    case NotifType.certificateIssued:
      return Icons.workspace_premium_outlined;
    case NotifType.waitlistPromoted:
      return Icons.trending_up_rounded;
    case NotifType.classroomFlagged:
      return Icons.flag_outlined;
    case NotifType.noticePosted:
      return Icons.campaign_outlined;
    // Newer types (production notification coverage audit) — the
    // NotifType constants were added in models.dart, but the icon
    // mapping here was missing until now (all of these were falling
    // back to the default bell icon).
    case NotifType.sessionLive:
      return Icons.videocam_rounded;
    case NotifType.sessionCancelled:
      return Icons.event_busy_rounded;
    case NotifType.assignmentPosted:
      return Icons.assignment_outlined;
    case NotifType.submissionReceived:
      return Icons.assignment_turned_in_outlined;
    case NotifType.staffAdded:
      return Icons.person_add_alt_rounded;
    case NotifType.reviewPosted:
      return Icons.star_outline_rounded;
    case NotifType.reportReviewed:
      return Icons.fact_check_outlined;
    default:
      return Icons.notifications_none_rounded;
  }
}

// ===========================================================================
// SCREEN
// ===========================================================================
class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key});

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  List<AppNotification> _all = [];
  bool _loading = true;
  String? _error;
  bool _unreadOnly = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await LiveClassApi.notifications.list(isRead: _unreadOnly ? false : null);
      final items = res.results.toList()..sort((a, b) => b.createdAt.compareTo(a.createdAt));
      if (!mounted) return;
      setState(() {
        _all = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load notifications.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _markAllRead() async {
    try {
      await LiveClassApi.notifications.markAllRead();
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Mark-all-read failed.');
    }
  }

  Future<void> _onTapNotification(AppNotification n) async {
    if (!n.isRead) {
      // Optimistic — flip locally, fire the API, no need to block the tap.
      setState(() {
        final idx = _all.indexWhere((x) => x.id == n.id);
        if (idx != -1) {
          _all[idx] = AppNotification(
            id: n.id,
            notifType: n.notifType,
            title: n.title,
            message: n.message,
            classroomId: n.classroomId,
            classroomTitle: n.classroomTitle,
            sessionId: n.sessionId,
            isRead: true,
            createdAt: n.createdAt,
            readAt: DateTime.now(),
          );
        }
      });
      try {
        await LiveClassApi.notifications.markRead(n.id);
      } catch (_) {
        // Non-fatal — worst case it shows read locally but unread on
        // reload; not worth interrupting the tap for.
      }
    }
    if (n.classroomId != null && mounted) {
      Navigator.push(context, MaterialPageRoute(builder: (_) => ClassroomDetailScreen(classroomId: n.classroomId!)));
    }
  }

  Future<void> _deleteOne(AppNotification n) async {
    setState(() => _all.removeWhere((x) => x.id == n.id));
    try {
      await LiveClassApi.notifications.delete(n.id);
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Delete failed.');
      _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar(
        'Notifications',
        actions: [IconButton(tooltip: 'Mark all read', onPressed: _markAllRead, icon: const Icon(Icons.done_all_rounded))],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
            child: Align(
              alignment: Alignment.centerLeft,
              child: FilterChip(
                label: const Text('Unread only', style: TextStyle(fontSize: 12)),
                selected: _unreadOnly,
                onSelected: (v) {
                  setState(() => _unreadOnly = v);
                  _load();
                },
                selectedColor: LiveClassColors.navy.withValues(alpha: 0.1),
                checkmarkColor: LiveClassColors.navy,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
              ),
            ),
          ),
          Expanded(
            child: RefreshIndicator(
              color: LiveClassColors.navy,
              onRefresh: _load,
              child: _loading
                  ? const LiveClassLoading()
                  : _error != null
                      ? LiveClassErrorState(message: _error!, onRetry: _load)
                      : _all.isEmpty
                          ? const LiveClassEmptyState(icon: Icons.notifications_none_rounded, title: 'No notifications yet.')
                          : ListView.builder(
                              padding: const EdgeInsets.fromLTRB(12, 6, 12, 24),
                              itemCount: _all.length,
                              itemBuilder: (_, i) => _notifTile(_all[i]),
                            ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _notifTile(AppNotification n) {
    return Dismissible(
      key: ValueKey(n.id),
      direction: DismissDirection.endToStart,
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.only(right: 20),
        margin: const EdgeInsets.symmetric(vertical: 4),
        decoration: BoxDecoration(color: LiveClassColors.danger.withValues(alpha: 0.85), borderRadius: BorderRadius.circular(12)),
        child: const Icon(Icons.delete_outline, color: Colors.white),
      ),
      onDismissed: (_) => _deleteOne(n),
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4, horizontal: 4),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: n.isRead ? Colors.white : LiveClassColors.navy.withValues(alpha: 0.04),
          borderRadius: BorderRadius.circular(12),
          border: n.isRead ? null : Border.all(color: LiveClassColors.navy.withValues(alpha: 0.15)),
          boxShadow: const [LiveClassColors.cardShadow],
        ),
        child: InkWell(
          onTap: () => _onTapNotification(n),
          borderRadius: BorderRadius.circular(12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 36,
                height: 36,
                margin: const EdgeInsets.only(top: 2),
                decoration: BoxDecoration(color: LiveClassColors.navy.withValues(alpha: 0.08), shape: BoxShape.circle),
                alignment: Alignment.center,
                child: Icon(_notifIcon(n.notifType), size: 17, color: LiveClassColors.navy),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(n.title, style: TextStyle(fontWeight: n.isRead ? FontWeight.w600 : FontWeight.bold, fontSize: 13.5)),
                    if (n.message.isNotEmpty) ...[
                      const SizedBox(height: 2),
                      Text(n.message, style: TextStyle(fontSize: 12, color: Colors.grey.shade600), maxLines: 2, overflow: TextOverflow.ellipsis),
                    ],
                    const SizedBox(height: 4),
                    Text(_fmtRelative(n.createdAt), style: TextStyle(fontSize: 10.5, color: Colors.grey.shade400)),
                  ],
                ),
              ),
              if (!n.isRead)
                Container(
                  width: 8,
                  height: 8,
                  margin: const EdgeInsets.only(top: 4),
                  decoration: const BoxDecoration(color: LiveClassColors.navy, shape: BoxShape.circle),
                ),
            ],
          ),
        ),
      ),
    );
  }
}