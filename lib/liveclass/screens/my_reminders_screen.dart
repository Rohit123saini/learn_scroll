// lib/liveclass/screens/my_reminders_screen.dart
//
// "My Reminders" — Screen (screen-architecture audit fix).
// API: GET/DELETE reminders/  (ClassReminderViewSet, scoped to request.user
// server-side — see liveclass/views.py §17).
//
// FIX (screen-architecture audit): ClassReminder had a full backend +
// Flutter API-service layer (ReminderApi.list/create/delete) but nothing in
// the screen set ever called list() or delete() — a user could not see or
// cancel a reminder once set. This screen is the "manage" half of that gap;
// the "create" half is the bell on SessionsListScreen's session cards.
// Reached from LiveClassHomeScreen's "My Learning" tab, same as My Passes /
// Wishlist / My Certificates / My Waitlist.
//
// The reminders/ endpoint only returns the session id (not a nested
// session object — see ClassReminderSerializer fields), so each reminder's
// session is resolved with SessionApi.detail() and cached locally to avoid
// re-fetching the same session twice.

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import 'live_session_screen.dart';

class MyRemindersScreen extends StatefulWidget {
  const MyRemindersScreen({super.key});

  @override
  State<MyRemindersScreen> createState() => _MyRemindersScreenState();
}

class _MyRemindersScreenState extends State<MyRemindersScreen> {
  List<ClassReminder> _reminders = [];
  final Map<int, ClassSession> _sessions = {}; // sessionId -> session, cached
  bool _loading = true;
  String? _error;

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
      final res = await LiveClassApi.reminders.list();
      final items = res.results.toList()..sort((a, b) => a.remindAt.compareTo(b.remindAt));

      // Resolve each reminder's session (best-effort, in parallel). A
      // session that fails to load (e.g. deleted) just falls back to a
      // plain "Session #id" label instead of blocking the whole list.
      await Future.wait(items.map((r) async {
        if (_sessions.containsKey(r.sessionId)) return;
        try {
          final s = await LiveClassApi.sessions.detail(r.sessionId);
          _sessions[r.sessionId] = s;
        } catch (_) {
          // leave unresolved — handled at render time.
        }
      }));

      if (!mounted) return;
      setState(() {
        _reminders = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load reminders.';
      });
    }
  }

  void _snack(String msg) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));

  Future<void> _cancel(ClassReminder r) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Cancel Reminder?'),
        content: Text('The reminder for ${liveClassFmtDateTime(r.remindAt)} will be removed.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Go Back')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Cancel Reminder', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await LiveClassApi.reminders.delete(r.id);
      if (!mounted) return;
      setState(() => _reminders.removeWhere((x) => x.id == r.id));
      _snack('Reminder cancelled.');
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not cancel.');
    }
  }

  void _openSession(ClassReminder r) {
    final session = _sessions[r.sessionId];
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => LiveSessionScreen(sessionId: r.sessionId, session: session)),
    );
  }

  String _channelLabel(String c) {
    switch (c) {
      case ReminderChannel.sms:
        return 'SMS';
      case ReminderChannel.email:
        return 'Email';
      default:
        return 'Push';
    }
  }

  IconData _channelIcon(String c) {
    switch (c) {
      case ReminderChannel.sms:
        return Icons.sms_outlined;
      case ReminderChannel.email:
        return Icons.mail_outline_rounded;
      default:
        return Icons.notifications_none_rounded;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar('My Reminders'),
      body: _loading
          ? const LiveClassLoading()
          : _error != null
              ? LiveClassErrorState(message: _error!, onRetry: _load)
              : RefreshIndicator(
                  color: LiveClassColors.navy,
                  onRefresh: _load,
                  child: _reminders.isEmpty
                      ? const LiveClassEmptyState(
                          icon: Icons.notifications_none_rounded,
                          title: 'No reminders set.',
                          subtitle: 'Tap the bell icon on any upcoming session to set a reminder.',
                        )
                      : ListView.builder(
                          padding: const EdgeInsets.fromLTRB(14, 14, 14, 24),
                          itemCount: _reminders.length,
                          itemBuilder: (_, i) => _reminderCard(_reminders[i]),
                        ),
                ),
    );
  }

  Widget _reminderCard(ClassReminder r) {
    final session = _sessions[r.sessionId];
    final isPast = r.remindAt.isBefore(DateTime.now());
    return LiveClassCard(
      onTap: () => _openSession(r),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          LiveClassIconBadge(icon: _channelIcon(r.channel), size: 42),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  session != null && session.classroomTitle.isNotEmpty ? session.classroomTitle : 'Session #${r.sessionId}',
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: LiveClassColors.navy),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 3),
                if (session != null)
                  Text(
                    'Session: ${liveClassFmtDateTime(session.scheduledStart)}',
                    style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600),
                  ),
                const SizedBox(height: 6),
                Row(
                  children: [
                    Icon(Icons.alarm_rounded, size: 13, color: Colors.grey.shade500),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Text(
                        'Alert: ${liveClassFmtDateTime(r.remindAt)} · ${_channelLabel(r.channel)}',
                        style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                LiveClassStatusChip(
                  label: r.isSent ? 'SENT' : (isPast ? 'DUE' : 'UPCOMING'),
                  color: r.isSent ? LiveClassColors.success : (isPast ? LiveClassColors.warning : LiveClassColors.navy),
                  background: r.isSent ? LiveClassColors.successBg : (isPast ? LiveClassColors.warningBg : LiveClassColors.bg),
                ),
              ],
            ),
          ),
          if (!r.isSent)
            IconButton(
              onPressed: () => _cancel(r),
              icon: const Icon(Icons.close_rounded, size: 18, color: Colors.black38),
              tooltip: 'Cancel reminder',
            ),
        ],
      ),
    );
  }
}