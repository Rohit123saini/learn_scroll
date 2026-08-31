// lib/liveclass/screens/sessions_list_screen.dart
//
// Screen 5 — Sessions List / Calendar (see LIVECLASS_SCREEN_ARCHITECTURE.md §5).
// API: GET sessions/?classroom=&status=  (+ POST/PATCH/DELETE for teacher
// ad-hoc sessions, POST .../join/, .../end/).
//
// This is the classroom's full session history + upcoming queue — distinct
// from the "next session" summary already shown inline on Classroom Detail.
// Reached from Classroom Detail's manage panel (teacher) or "Schedule" area
// (student, view-only).
//
// Layout: a horizontal date strip acts as the "calendar" (14 days, scrolled
// to today, dots mark days that have a session) sitting above a status-
// filtered list — avoids pulling in a calendar package while still giving
// the day-picking affordance the architecture doc asks for.
//
// [canManage] mirrors the pattern already used across the module
// (_ScheduleTab, ScheduleManagerScreen, etc.) — true unlocks create/edit/
// delete/end, false gives a read-only list with just "Enter Class".
//
// FIX (screen-architecture audit): ClassReminder had full backend + API-
// service support (ReminderApi.list/create/delete, see
// liveclass_api_service.dart §17) but no UI ever called it — there was no
// way for a student (or teacher) to actually set a reminder for an
// upcoming session. Added a bell toggle on every upcoming (scheduled)
// session card here — the natural place a user picks a session from —
// that opens a bottom sheet to choose an offset + channel and POSTs a
// ClassReminder. See also MyRemindersScreen (My Learning tab) for the
// list/cancel side of the same gap.

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import 'live_session_screen.dart';
import 'waitlist_screen.dart';

// FIX (design-system drift — production readiness audit): aliased to the
// shared tokens instead of locally-duplicated hex literals — see the
// matching fix in wishlist_screen.dart / waitlist_screen.dart. This file
// already imports theme/liveclass_theme.dart, so there's no reason for its
// own copy to drift out of sync with LiveClassColors. Zero visual change.
const _kNavy = LiveClassColors.navy;
const _kBg = LiveClassColors.bg;
const _kGradient = LiveClassColors.gradient;

// FIX (i18n / timezone audit — see utils/liveclass_datetime.dart for the
// full writeup): both helpers used to be hand-rolled with a hardcoded
// English month/weekday array and, worse, never called `.toLocal()`
// before reading `.hour`/`.day`/`.month` off `scheduledStart`/
// `scheduledEnd` — which are UTC-parsed straight from the API. Every
// session time on this screen (the calendar strip, list, reminder sheet,
// ad-hoc session form) was rendering in English and in UTC, regardless of
// the viewer's own locale/timezone.
String _fmtDate(DateTime d, [BuildContext? context]) => liveClassFmtDate(d, context);
String _fmtTime(DateTime d, [BuildContext? context]) => DateFormat.jm(context == null ? null : Localizations.maybeLocaleOf(context)?.toString()).format(d.toLocal());
String _fmtWeekdayShort(DateTime d, [BuildContext? context]) =>
    DateFormat.E(context == null ? null : Localizations.maybeLocaleOf(context)?.toString()).format(d);

String _statusLabel(String s) {
  switch (s) {
    case SessionStatus.live:
      return 'Live';
    case SessionStatus.completed:
      return 'Completed';
    case SessionStatus.cancelled:
      return 'Cancelled';
    default:
      return 'Scheduled';
  }
}

Color _statusColor(String s) {
  switch (s) {
    case SessionStatus.live:
      return const Color(0xFFE53935);
    case SessionStatus.completed:
      return Colors.grey.shade600;
    case SessionStatus.cancelled:
      return Colors.grey.shade400;
    default:
      return _kNavy;
  }
}

bool _sameDay(DateTime a, DateTime b) => a.year == b.year && a.month == b.month && a.day == b.day;

// ===========================================================================
// SCREEN
// ===========================================================================
class SessionsListScreen extends StatefulWidget {
  final int classroomId;
  final String classroomTitle;
  final bool canManage;

  const SessionsListScreen({
    super.key,
    required this.classroomId,
    this.classroomTitle = '',
    this.canManage = false,
  });

  @override
  State<SessionsListScreen> createState() => _SessionsListScreenState();
}

class _SessionsListScreenState extends State<SessionsListScreen> {
  final ScrollController _dateStripCtrl = ScrollController();

  List<ClassSession> _all = [];
  bool _loading = true;
  String? _error;

  // sessionId -> the user's active (not-yet-sent) reminder for that session.
  // Loaded best-effort alongside sessions — non-fatal on failure, same
  // pattern as the unread-notifications bell on LiveClassHomeScreen.
  final Map<int, ClassReminder> _reminders = {};

  // NEW (Pass 13 frontend catch-up §1.10) — chat/poll unread counts per
  // session, `SessionUnreadCount` (`{"chat": N, "polls": N}`). Only
  // fetched for sessions the user could plausibly have activity in
  // (live or completed — a still-scheduled/never-joined session has
  // nothing to be unread), and only best-effort: a failure here just
  // means no badge shows, same non-fatal spirit as _loadReminders below.
  final Map<int, SessionUnreadCount> _unread = {};

  String? _statusFilter; // null == All
  DateTime? _selectedDate; // null == no date filter (show all matching status)

  late final List<DateTime> _dateStrip; // today-3 .. today+10

  @override
  void initState() {
    super.initState();
    final today = DateTime.now();
    final start = DateTime(today.year, today.month, today.day).subtract(const Duration(days: 3));
    _dateStrip = List.generate(14, (i) => start.add(Duration(days: i)));
    _load();
  }

  @override
  void dispose() {
    _dateStripCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      // NOTE: ClassSessionViewSet.get_queryset() only reads `?classroom=` —
      // it never reads `?status=` (see liveclass/views.py), so a
      // server-side status filter would silently be a no-op. Fetch once
      // per classroom and filter status client-side instead (same
      // approach already used for the date-strip filter below).
      final res = await LiveClassApi.sessions.list(classroomId: widget.classroomId);
      final items = res.results.toList()..sort((a, b) => a.scheduledStart.compareTo(b.scheduledStart));
      if (!mounted) return;
      setState(() {
        _all = items;
        _loading = false;
      });
      _loadReminders();
      _loadUnread();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load sessions.';
      });
    }
  }

  // Best-effort: reminders/ has no classroom filter, so pull the caller's
  // full list and keep only the ones matching a session shown on this
  // screen. A failure here shouldn't block the session list itself, so
  // errors are swallowed the same way _loadUnread() does on the home tab.
  Future<void> _loadReminders() async {
    try {
      final res = await LiveClassApi.reminders.list();
      final ids = _all.map((s) => s.id).toSet();
      final map = <int, ClassReminder>{};
      for (final r in res.results) {
        if (ids.contains(r.sessionId) && !r.isSent) map[r.sessionId] = r;
      }
      if (!mounted) return;
      setState(() {
        _reminders
          ..clear()
          ..addAll(map);
      });
    } catch (_) {
      // Non-fatal — bell just falls back to "no reminder set" state.
    }
  }

  // NEW (Pass 13 §1.10) — best-effort, one call per live/completed session
  // currently shown. Not paginated/batched since sessions/{id}/unread/ has
  // no bulk variant documented — fine at this screen's usual list sizes.
  Future<void> _loadUnread() async {
    final targets = _all.where((s) => s.status == SessionStatus.live || s.status == SessionStatus.completed).toList();
    for (final s in targets) {
      try {
        final res = await LiveClassApi.sessions.unread(s.id);
        if (!mounted) return;
        setState(() => _unread[s.id] = res);
      } catch (_) {
        // Non-fatal — that session's card just shows no badge.
      }
    }
  }

  List<ClassSession> get _visible {
    var list = _all;
    if (_statusFilter != null) list = list.where((s) => s.status == _statusFilter).toList();
    if (_selectedDate != null) list = list.where((s) => _sameDay(s.scheduledStart, _selectedDate!)).toList();
    return list;
  }

  Set<DateTime> get _datesWithSessions =>
      _all.map((s) => DateTime(s.scheduledStart.year, s.scheduledStart.month, s.scheduledStart.day)).toSet();

  void _setFilter(String? status) {
    setState(() => _statusFilter = status); // client-side only — see _load()
  }

  void _toggleDate(DateTime d) {
    setState(() => _selectedDate = (_selectedDate != null && _sameDay(_selectedDate!, d)) ? null : d);
  }

  void _snack(String msg) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));

  // -------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------
  Future<void> _enterClass(ClassSession s) async {
    Navigator.push(context, MaterialPageRoute(builder: (_) => LiveSessionScreen(sessionId: s.id, session: s)));
  }

  Future<void> _openForm({ClassSession? existing}) async {
    final result = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _SessionFormSheet(classroomId: widget.classroomId, existing: existing),
    );
    if (result == true) _load();
  }

  Future<void> _deleteSession(ClassSession s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Delete Session?'),
        content: Text('The session on ${_fmtDate(s.scheduledStart, context)} · ${_fmtTime(s.scheduledStart, context)} will be removed.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Wapas')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await LiveClassApi.sessions.delete(s.id);
      if (!mounted) return;
      setState(() => _all.removeWhere((x) => x.id == s.id));
      _snack('Session deleted.');
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not delete.');
    }
  }

  Future<void> _endSession(ClassSession s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('End Session?'),
        content: const Text('All participants will be disconnected from the room.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Wapas')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('End Session', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await LiveClassApi.sessions.end(s.id);
      _snack('Session ended.');
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Could not end.');
    }
  }

  void _openWaitlist(ClassSession s) {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => WaitlistScreen(sessionId: s.id, classroomTitle: widget.classroomTitle, canManage: true),
      ),
    );
  }

  Future<void> _openReminderSheet(ClassSession s) async {
    final result = await showModalBottomSheet<Object?>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _ReminderSheet(session: s, existing: _reminders[s.id]),
    );
    if (!mounted || result == null) return;
    if (result is ClassReminder) {
      setState(() => _reminders[s.id] = result);
      _snack('Reminder set.');
    } else if (result == _ReminderSheet.removed) {
      setState(() => _reminders.remove(s.id));
      _snack('Reminder removed.');
    }
  }

  // -------------------------------------------------------------------
  // Build
  // -------------------------------------------------------------------
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      appBar: AppBar(
        backgroundColor: Colors.white,
        foregroundColor: _kNavy,
        elevation: 0.5,
        title: Text(
          widget.classroomTitle.isNotEmpty ? widget.classroomTitle : 'Sessions',
          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
        ),
      ),
      floatingActionButton: widget.canManage
          ? DecoratedBox(
              decoration: BoxDecoration(gradient: _kGradient, borderRadius: BorderRadius.circular(28)),
              child: FloatingActionButton.extended(
                backgroundColor: Colors.transparent,
                elevation: 0,
                onPressed: () => _openForm(),
                icon: const Icon(Icons.add_rounded, color: Colors.white),
                label: const Text('New Session', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
              ),
            )
          : null,
      body: Column(
        children: [
          _dateStripBar(),
          _statusChips(),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator(color: _kNavy))
                : _error != null
                    ? _ErrorState(message: _error!, onRetry: _load)
                    : RefreshIndicator(
                        color: _kNavy,
                        onRefresh: _load,
                        child: _visible.isEmpty
                            ? ListView(
                                children: const [
                                  Padding(
                                    padding: EdgeInsets.only(top: 100),
                                    child: Center(child: Text('No sessions found.', style: TextStyle(color: Colors.black45))),
                                  ),
                                ],
                              )
                            : ListView.builder(
                                padding: const EdgeInsets.fromLTRB(16, 12, 16, 90),
                                itemCount: _visible.length,
                                itemBuilder: (_, i) => _sessionCard(_visible[i]),
                              ),
                      ),
          ),
        ],
      ),
    );
  }

  Widget _dateStripBar() {
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: SizedBox(
        height: 62,
        child: ListView.builder(
          controller: _dateStripCtrl,
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          itemCount: _dateStrip.length,
          itemBuilder: (_, i) {
            final d = _dateStrip[i];
            final isToday = _sameDay(d, DateTime.now());
            final isSelected = _selectedDate != null && _sameDay(d, _selectedDate!);
            final hasSession = _datesWithSessions.any((x) => _sameDay(x, d));
            return GestureDetector(
              onTap: () => _toggleDate(d),
              child: Container(
                width: 46,
                margin: const EdgeInsets.symmetric(horizontal: 4),
                decoration: BoxDecoration(
                  gradient: isSelected ? _kGradient : null,
                  color: isSelected ? null : (isToday ? _kBg : Colors.transparent),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: isSelected ? Colors.transparent : Colors.grey.shade200),
                ),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Text(_fmtWeekdayShort(d, context),
                        style: TextStyle(
                            fontSize: 10, color: isSelected ? Colors.white70 : Colors.black45, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 2),
                    Text('${d.day}',
                        style: TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.bold,
                            color: isSelected ? Colors.white : _kNavy)),
                    const SizedBox(height: 3),
                    Container(
                      width: 4,
                      height: 4,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: hasSession ? (isSelected ? Colors.white : Colors.orange) : Colors.transparent,
                      ),
                    ),
                  ],
                ),
              ),
            );
          },
        ),
      ),
    );
  }

  Widget _statusChips() {
    final options = <String?, String>{
      null: 'All',
      SessionStatus.scheduled: 'Upcoming',
      SessionStatus.live: 'Live',
      SessionStatus.completed: 'Completed',
      SessionStatus.cancelled: 'Cancelled',
    };
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.only(bottom: 10),
      child: SizedBox(
        height: 34,
        child: ListView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          children: options.entries.map((e) {
            final selected = _statusFilter == e.key;
            return Padding(
              padding: const EdgeInsets.symmetric(horizontal: 4),
              child: ChoiceChip(
                label: Text(e.value, style: TextStyle(fontSize: 12, color: selected ? Colors.white : Colors.black87)),
                selected: selected,
                onSelected: (_) => _setFilter(e.key),
                selectedColor: _kNavy,
                backgroundColor: _kBg,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                side: BorderSide.none,
              ),
            );
          }).toList(),
        ),
      ),
    );
  }

  Widget _sessionCard(ClassSession s) {
    final color = _statusColor(s.status);
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.04), blurRadius: 8, offset: const Offset(0, 2))],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(color: color.withOpacity(0.12), borderRadius: BorderRadius.circular(20)),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (s.status == SessionStatus.live)
                      Padding(
                        padding: const EdgeInsets.only(right: 5),
                        child: Container(width: 6, height: 6, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
                      ),
                    Text(_statusLabel(s.status).toUpperCase(),
                        style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: color)),
                  ],
                ),
              ),
              const Spacer(),
              // NEW (Pass 13 §1.10) — unread chat/poll badges.
              if (_unread[s.id] != null) _unreadBadges(_unread[s.id]!),
              if (s.status == SessionStatus.scheduled) _reminderBell(s),
              if (widget.canManage && s.status == SessionStatus.scheduled)
                PopupMenuButton<String>(
                  icon: const Icon(Icons.more_vert_rounded, size: 18, color: Colors.black45),
                  onSelected: (v) {
                    if (v == 'edit') _openForm(existing: s);
                    if (v == 'delete') _deleteSession(s);
                    if (v == 'waitlist') _openWaitlist(s);
                  },
                  itemBuilder: (_) => const [
                    PopupMenuItem(value: 'edit', child: Text('Edit')),
                    PopupMenuItem(value: 'waitlist', child: Text('View Waitlist')),
                    PopupMenuItem(value: 'delete', child: Text('Delete', style: TextStyle(color: Colors.red))),
                  ],
                ),
            ],
          ),
          const SizedBox(height: 10),
          Text(_fmtDate(s.scheduledStart, context), style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
          const SizedBox(height: 3),
          Text('${_fmtTime(s.scheduledStart, context)} – ${_fmtTime(s.scheduledEnd, context)}',
              style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600)),
          if (s.roomId.isNotEmpty) ...[
            const SizedBox(height: 3),
            Text('Room: ${s.roomId}', style: TextStyle(fontSize: 11, color: Colors.grey.shade400)),
          ],
          const SizedBox(height: 12),
          Row(
            children: [
              // NOTE (fix): `isJoinable` is a server-computed window meant to
              // stop STUDENTS joining before the teacher has started —
              // but the host is the one who starts the class, so gating
              // them on the same flag left a teacher with an ad-hoc/
              // just-created scheduled session and literally no way to
              // ever enter it (nothing else here flips status to live).
              // A manager can always attempt to enter; the backend still
              // has the final say (errors surface inside LiveSessionScreen).
              if (s.status == SessionStatus.live || (s.status == SessionStatus.scheduled && (s.isJoinable || widget.canManage)))
                Expanded(
                  child: SizedBox(
                    height: 38,
                    child: DecoratedBox(
                      decoration: BoxDecoration(gradient: _kGradient, borderRadius: BorderRadius.circular(10)),
                      child: Material(
                        color: Colors.transparent,
                        child: InkWell(
                          borderRadius: BorderRadius.circular(10),
                          onTap: () => _enterClass(s),
                          child: Center(
                            child: Text(
                              s.status == SessionStatus.scheduled && !s.isJoinable ? 'Start Class' : 'Enter Class',
                              style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 13),
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                )
              else if (s.status == SessionStatus.scheduled)
                Expanded(
                  child: SizedBox(
                    height: 38,
                    child: OutlinedButton(
                      onPressed: null,
                      style: OutlinedButton.styleFrom(disabledForegroundColor: Colors.black38),
                      child: const Text('Not Joinable Yet', style: TextStyle(fontSize: 12)),
                    ),
                  ),
                )
              else if (s.recordingUrl.isNotEmpty)
                Expanded(
                  child: SizedBox(
                    height: 38,
                    child: OutlinedButton.icon(
                      onPressed: () => _snack('Recording: ${s.recordingUrl}'),
                      icon: const Icon(Icons.play_circle_outline_rounded, size: 16),
                      label: const Text('Recording', style: TextStyle(fontSize: 12)),
                    ),
                  ),
                ),
              // NEW (Pass 15 frontend catch-up §1.7) — post-session
              // engagement report entry point. Host/co-teacher/moderator
              // only, same canManage threading as every other management
              // action on this screen.
              if (widget.canManage && s.status == SessionStatus.completed) ...[
                const SizedBox(width: 8),
                SizedBox(
                  height: 38,
                  child: OutlinedButton.icon(
                    onPressed: () => Navigator.push(
                      context,
                      MaterialPageRoute(builder: (_) => SessionEngagementReportScreen(sessionId: s.id, classroomTitle: widget.classroomTitle)),
                    ),
                    icon: const Icon(Icons.insights_rounded, size: 16),
                    label: const Text('View Report', style: TextStyle(fontSize: 12)),
                  ),
                ),
              ],
              if (widget.canManage && s.status == SessionStatus.live) ...[
                const SizedBox(width: 8),
                SizedBox(
                  height: 38,
                  child: OutlinedButton(
                    onPressed: () => _openWaitlist(s),
                    child: const Text('Waitlist', style: TextStyle(fontSize: 12)),
                  ),
                ),
                const SizedBox(width: 8),
                SizedBox(
                  height: 38,
                  child: OutlinedButton(
                    onPressed: () => _endSession(s),
                    style: OutlinedButton.styleFrom(foregroundColor: Colors.red, side: const BorderSide(color: Colors.red)),
                    child: const Text('End', style: TextStyle(fontSize: 12)),
                  ),
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }

  Widget _reminderBell(ClassSession s) {
    final active = _reminders.containsKey(s.id);
    return IconButton(
      onPressed: () => _openReminderSheet(s),
      tooltip: active ? 'Reminder set' : 'Remind me',
      icon: Icon(
        active ? Icons.notifications_active_rounded : Icons.notifications_none_rounded,
        size: 20,
        color: active ? const Color(0xFFEE0979) : Colors.black45,
      ),
    );
  }
}

// ===========================================================================
// Set / cancel a reminder — bottom sheet
// ===========================================================================
class _ReminderSheet extends StatefulWidget {
  final ClassSession session;
  final ClassReminder? existing;
  const _ReminderSheet({required this.session, this.existing});

  /// Sentinel returned via Navigator.pop when the existing reminder was
  /// deleted, so the caller can tell "removed" apart from "cancelled" (null).
  static const removed = #reminder_removed;

  @override
  State<_ReminderSheet> createState() => _ReminderSheetState();
}

class _ReminderSheetState extends State<_ReminderSheet> {
  // Offsets before session start the user can pick from. Filtered down to
  // whichever still land in the future for this particular session.
  static const _offsets = <Duration>[
    Duration(minutes: 10),
    Duration(minutes: 30),
    Duration(hours: 1),
    Duration(hours: 3),
    Duration(days: 1),
  ];

  static String _offsetLabel(Duration d) {
    if (d.inDays >= 1) return '${d.inDays} din pehle';
    if (d.inHours >= 1) return '${d.inHours} ghante pehle';
    return '${d.inMinutes} min pehle';
  }

  static String _channelLabel(String c) {
    switch (c) {
      case ReminderChannel.sms:
        return 'SMS';
      case ReminderChannel.email:
        return 'Email';
      default:
        return 'Push Notification';
    }
  }

  static IconData _channelIcon(String c) {
    switch (c) {
      case ReminderChannel.sms:
        return Icons.sms_outlined;
      case ReminderChannel.email:
        return Icons.mail_outline_rounded;
      default:
        return Icons.notifications_none_rounded;
    }
  }

  late Duration _offset;
  late String _channel;
  bool _saving = false;
  String? _error;

  bool get _isEdit => widget.existing != null;

  List<Duration> get _availableOffsets =>
      _offsets.where((d) => widget.session.scheduledStart.subtract(d).isAfter(DateTime.now())).toList();

  @override
  void initState() {
    super.initState();
    final avail = _availableOffsets;
    _offset = avail.isNotEmpty ? avail.first : const Duration(minutes: 10);
    _channel = widget.existing?.channel ?? ReminderChannel.push;
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final remindAt = widget.session.scheduledStart.subtract(_offset);
      final reminder = ClassReminder(
        id: 0,
        sessionId: widget.session.id,
        user: UserMini(id: 0, username: '', fullName: ''),
        remindAt: remindAt,
        channel: _channel,
      );
      final created = await LiveClassApi.reminders.create(reminder);
      if (!mounted) return;
      Navigator.pop(context, created);
    } catch (e) {
      setState(() {
        _saving = false;
        _error = e is LiveClassApiException ? e.message : 'Could not set reminder.';
      });
    }
  }

  Future<void> _remove() async {
    final existing = widget.existing;
    if (existing == null) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await LiveClassApi.reminders.delete(existing.id);
      if (!mounted) return;
      Navigator.pop(context, _ReminderSheet.removed);
    } catch (e) {
      setState(() {
        _saving = false;
        _error = e is LiveClassApiException ? e.message : 'Could not remove reminder.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final avail = _availableOffsets;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: Container(
        decoration: const BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        padding: const EdgeInsets.fromLTRB(20, 14, 20, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(2)),
              ),
            ),
            const SizedBox(height: 16),
            Text(_isEdit ? 'Reminder Set' : 'Set Reminder',
                style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 17)),
            const SizedBox(height: 4),
            Text(
              'For the session on ${_fmtDate(widget.session.scheduledStart, context)} · ${_fmtTime(widget.session.scheduledStart, context)}.',
              style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600),
            ),
            const SizedBox(height: 20),
            if (_error != null) ...[
              Text(_error!, style: const TextStyle(color: Colors.red, fontSize: 12.5)),
              const SizedBox(height: 10),
            ],
            if (_isEdit) ...[
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(color: _kBg, borderRadius: BorderRadius.circular(12)),
                child: Row(
                  children: [
                    Icon(_channelIcon(widget.existing!.channel), size: 18, color: _kNavy),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        '${_fmtDate(widget.existing!.remindAt, context)} · ${_fmtTime(widget.existing!.remindAt, context)}  ·  ${_channelLabel(widget.existing!.channel)}',
                        style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                height: 46,
                child: OutlinedButton.icon(
                  onPressed: _saving ? null : _remove,
                  style: OutlinedButton.styleFrom(foregroundColor: Colors.red, side: const BorderSide(color: Colors.red)),
                  icon: const Icon(Icons.notifications_off_outlined, size: 18),
                  label: Text(_saving ? 'Removing…' : 'Reminder Hataayein'),
                ),
              ),
            ] else ...[
              Text('Kitni der pehle?', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.grey.shade700)),
              const SizedBox(height: 8),
              if (avail.isEmpty)
                Text('There is no longer enough time to set a reminder for this session.',
                    style: TextStyle(fontSize: 12.5, color: Colors.grey.shade500))
              else
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: avail.map((d) {
                    final selected = _offset == d;
                    return ChoiceChip(
                      label: Text(_offsetLabel(d), style: TextStyle(color: selected ? Colors.white : Colors.black87, fontSize: 12)),
                      selected: selected,
                      onSelected: (_) => setState(() => _offset = d),
                      selectedColor: _kNavy,
                      backgroundColor: _kBg,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                      side: BorderSide.none,
                    );
                  }).toList(),
                ),
              const SizedBox(height: 18),
              Text('Kaise batayein?', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.grey.shade700)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [ReminderChannel.push, ReminderChannel.sms, ReminderChannel.email].map((c) {
                  final selected = _channel == c;
                  return ChoiceChip(
                    avatar: Icon(_channelIcon(c), size: 15, color: selected ? Colors.white : _kNavy),
                    label: Text(_channelLabel(c), style: TextStyle(color: selected ? Colors.white : Colors.black87, fontSize: 12)),
                    selected: selected,
                    onSelected: (_) => setState(() => _channel = c),
                    selectedColor: _kNavy,
                    backgroundColor: _kBg,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                    side: BorderSide.none,
                  );
                }).toList(),
              ),
              const SizedBox(height: 24),
              SizedBox(
                width: double.infinity,
                height: 48,
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    gradient: avail.isEmpty ? null : _kGradient,
                    color: avail.isEmpty ? Colors.grey.shade300 : null,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Material(
                    color: Colors.transparent,
                    child: InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: (_saving || avail.isEmpty) ? null : _save,
                      child: Center(
                        child: _saving
                            ? const SizedBox(
                                width: 20, height: 20, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))
                            : const Text('Set Reminder', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15)),
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

// ===========================================================================
// Create / Edit ad-hoc session — bottom sheet form
// ===========================================================================
class _SessionFormSheet extends StatefulWidget {
  final int classroomId;
  final ClassSession? existing;
  const _SessionFormSheet({required this.classroomId, this.existing});

  @override
  State<_SessionFormSheet> createState() => _SessionFormSheetState();
}

class _SessionFormSheetState extends State<_SessionFormSheet> {
  late DateTime _startDate;
  late TimeOfDay _startTime;
  late int _durationMinutes;
  late String _status;
  bool _saving = false;
  String? _error;

  bool get _isEdit => widget.existing != null;

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    // FIX (timezone audit): `e.scheduledStart` is UTC-aware (parsed
    // straight from the API). Without `.toLocal()` here, editing an
    // existing session pre-filled the date/time pickers with the UTC
    // clock values — for a session near midnight local time, this could
    // land the picker on the WRONG CALENDAR DAY entirely, not just the
    // wrong hour.
    final base = (e?.scheduledStart ?? DateTime.now().add(const Duration(hours: 1))).toLocal();
    _startDate = DateTime(base.year, base.month, base.day);
    _startTime = TimeOfDay(hour: base.hour, minute: base.minute);
    _durationMinutes = e != null ? e.scheduledEnd.difference(e.scheduledStart).inMinutes : 60;
    if (_durationMinutes <= 0) _durationMinutes = 60;
    _status = e?.status ?? SessionStatus.scheduled;
  }

  DateTime get _scheduledStart =>
      DateTime(_startDate.year, _startDate.month, _startDate.day, _startTime.hour, _startTime.minute);
  DateTime get _scheduledEnd => _scheduledStart.add(Duration(minutes: _durationMinutes));

  Future<void> _pickDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _startDate,
      firstDate: DateTime.now().subtract(const Duration(days: 365)),
      lastDate: DateTime.now().add(const Duration(days: 730)),
    );
    if (!mounted) return;
    if (picked != null) setState(() => _startDate = picked);
  }

  Future<void> _pickTime() async {
    final picked = await showTimePicker(context: context, initialTime: _startTime);
    if (!mounted) return;
    if (picked != null) setState(() => _startTime = picked);
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final session = ClassSession(
        id: widget.existing?.id ?? 0,
        classroomId: widget.classroomId,
        scheduledStart: _scheduledStart,
        scheduledEnd: _scheduledEnd,
        status: _status,
      );
      if (_isEdit) {
        await LiveClassApi.sessions.update(widget.existing!.id, session);
      } else {
        await LiveClassApi.sessions.create(session);
      }
      if (!mounted) return;
      Navigator.pop(context, true);
    } catch (e) {
      setState(() {
        _saving = false;
        _error = e is LiveClassApiException ? e.message : 'Could not save.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: DraggableScrollableSheet(
        initialChildSize: 0.62,
        minChildSize: 0.4,
        maxChildSize: 0.92,
        expand: false,
        builder: (_, scrollCtrl) => Container(
          decoration: const BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
          ),
          child: ListView(
            controller: scrollCtrl,
            padding: const EdgeInsets.fromLTRB(20, 14, 20, 24),
            children: [
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(2)),
                ),
              ),
              const SizedBox(height: 16),
              Text(_isEdit ? 'Edit Session' : 'New Ad-hoc Session',
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 17)),
              const SizedBox(height: 4),
              Text('For an extra class outside the recurring schedule.',
                  style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600)),
              const SizedBox(height: 20),
              if (_error != null) ...[
                Text(_error!, style: const TextStyle(color: Colors.red, fontSize: 12.5)),
                const SizedBox(height: 10),
              ],
              Row(
                children: [
                  Expanded(
                    child: _pickerTile(
                      icon: Icons.calendar_today_rounded,
                      label: _fmtDate(_startDate, context),
                      onTap: _pickDate,
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: _pickerTile(
                      icon: Icons.access_time_rounded,
                      label: _startTime.format(context),
                      onTap: _pickTime,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Text('Duration', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.grey.shade700)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: [30, 45, 60, 90, 120].map((m) {
                  final selected = _durationMinutes == m;
                  return ChoiceChip(
                    label: Text('${m}m', style: TextStyle(color: selected ? Colors.white : Colors.black87, fontSize: 12)),
                    selected: selected,
                    onSelected: (_) => setState(() => _durationMinutes = m),
                    selectedColor: _kNavy,
                    backgroundColor: _kBg,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                    side: BorderSide.none,
                  );
                }).toList(),
              ),
              Text('Ends at ${_fmtTime(_scheduledEnd, context)}', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
              if (_isEdit) ...[
                const SizedBox(height: 16),
                Text('Status', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.grey.shade700)),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  children: [SessionStatus.scheduled, SessionStatus.live, SessionStatus.cancelled, SessionStatus.completed].map((st) {
                    final selected = _status == st;
                    return ChoiceChip(
                      label: Text(_statusLabel(st), style: TextStyle(color: selected ? Colors.white : Colors.black87, fontSize: 12)),
                      selected: selected,
                      onSelected: (_) => setState(() => _status = st),
                      selectedColor: _kNavy,
                      backgroundColor: _kBg,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                      side: BorderSide.none,
                    );
                  }).toList(),
                ),
              ],
              const SizedBox(height: 24),
              SizedBox(
                width: double.infinity,
                height: 48,
                child: DecoratedBox(
                  decoration: BoxDecoration(gradient: _kGradient, borderRadius: BorderRadius.circular(12)),
                  child: Material(
                    color: Colors.transparent,
                    child: InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: _saving ? null : _save,
                      child: Center(
                        child: _saving
                            ? const SizedBox(
                                width: 20, height: 20, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))
                            : Text(_isEdit ? 'Save Changes' : 'Create Session',
                                style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15)),
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _pickerTile({required IconData icon, required String label, required VoidCallback onTap}) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 14),
        decoration: BoxDecoration(color: _kBg, borderRadius: BorderRadius.circular(12)),
        child: Row(
          children: [
            Icon(icon, size: 17, color: _kNavy),
            const SizedBox(width: 8),
            Expanded(child: Text(label, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600))),
          ],
        ),
      ),
    );
  }
}

// ===========================================================================
// Shared small widgets
// ===========================================================================
class _ErrorState extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _ErrorState({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline_rounded, size: 40, color: Colors.black38),
            const SizedBox(height: 12),
            Text(message, textAlign: TextAlign.center, style: const TextStyle(color: Colors.black54)),
            const SizedBox(height: 16),
            ElevatedButton(
              style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
              onPressed: onRetry,
              child: const Text('Retry'),
            ),
          ],
        ),
      ),
    );
  }
}