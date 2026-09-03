// lib/liveclass/screens/waitlist_screen.dart
//
// Screen 20 — Waitlist (see LIVECLASS_SCREEN_ARCHITECTURE.md §20).
//
// Two modes, driven by [sessionId]:
//   - Omitted → "My Waitlist" (student, own view): every session the
//     caller is currently waitlisted on, across classrooms. Each entry is
//     enriched with the session's classroom/time via `sessions/{id}/`
//     (the waitlist entry itself only carries a bare session id). Leave
//     (`DELETE waitlist/{id}/`) frees the spot; if the session has since
//     opened up, tapping the card retries `sessions/{id}/join/` directly.
//   - Provided (+ [classroomTitle], [canManage]) → teacher/staff "who's
//     waiting" view scoped to that one session, oldest-first, with a
//     Promote action (`POST waitlist/{id}/promote/`) per entry. Reached
//     from Sessions List / Live Session Room when a session is full.

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import 'live_session_screen.dart';

// FIX (design-system drift): these used to be locally-duplicated literal
// color values, independent of liveclass_theme.dart — exactly the
// per-screen drift that theme file was introduced to prevent (see its
// header comment). Now aliased straight to the shared tokens so this
// screen can never fall out of sync with the rest of the module if the
// palette changes. Kept as local names (rather than rewriting every call
// site to LiveClassColors.xxx) to keep this a zero-risk, behavior-
// preserving fix.
const _kNavy = LiveClassColors.navy;
const _kBg = LiveClassColors.bg;
const _kGradient = LiveClassColors.gradient;

// FIX (timezone bug): this used to be a hardcoded English month array with
// no `.toLocal()` call before reading `.day`/`.hour` off a UTC-parsed
// DateTime from the API — a student outside UTC would see their session
// time shifted from what they'd actually see in-app elsewhere. Delegates to
// the shared locale + `.toLocal()`-aware helper now (same fix already
// applied to doubts/holidays/submission-grading elsewhere in this module).
String _fmtDateTime(DateTime d) => liveClassFmtDateTime(d);

String _fmtRelative(DateTime d) {
  final diff = DateTime.now().difference(d);
  if (diff.inMinutes < 1) return 'Just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  return '${diff.inDays}d ago';
}

// ===========================================================================
// SCREEN
// ===========================================================================
class WaitlistScreen extends StatefulWidget {
  /// Provide for the teacher/staff "who's waiting for this session" view.
  /// Omit for the student's own "My Waitlist" view.
  final int? sessionId;
  final String classroomTitle;
  final bool canManage;
  const WaitlistScreen({super.key, this.sessionId, this.classroomTitle = '', this.canManage = false});

  @override
  State<WaitlistScreen> createState() => _WaitlistScreenState();
}

class _WaitlistScreenState extends State<WaitlistScreen> {
  bool get _isManageView => widget.sessionId != null && widget.canManage;

  List<SessionWaitlistEntry> _entries = [];
  // Student view only — session details keyed by session id, fetched
  // alongside the entries since SessionWaitlistEntry only carries a bare id.
  final Map<int, ClassSession> _sessionCache = {};
  bool _loading = true;
  String? _error;
  final Set<int> _busy = {}; // entry ids mid-action (leave/promote/rejoin)

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
      final res = await LiveClassApi.waitlist.myEntries(sessionId: widget.sessionId);
      final entries = res.results.toList()..sort((a, b) => a.joinedAt.compareTo(b.joinedAt));
      if (!_isManageView) {
        // Enrich each entry with its session's classroom/time for display.
        final ids = entries.map((e) => e.sessionId).toSet();
        await Future.wait(ids.map((id) async {
          try {
            _sessionCache[id] = await LiveClassApi.sessions.detail(id);
          } catch (_) {
            // Missing detail just falls back to "Session #id" — non-fatal.
          }
        }));
      }
      if (!mounted) return;
      setState(() {
        _entries = entries;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load waitlist.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _leave(SessionWaitlistEntry e) async {
    setState(() => _busy.add(e.id));
    try {
      await LiveClassApi.waitlist.leave(e.id);
      if (!mounted) return;
      setState(() => _entries.removeWhere((x) => x.id == e.id));
    } catch (err) {
      _snack(err is LiveClassApiException ? err.message : 'Could not leave waitlist.');
    } finally {
      if (mounted) setState(() => _busy.remove(e.id));
    }
  }

  Future<void> _promote(SessionWaitlistEntry e) async {
    setState(() => _busy.add(e.id));
    try {
      await LiveClassApi.waitlist.promote(e.id);
      if (!mounted) return;
      setState(() => _entries.removeWhere((x) => x.id == e.id));
      _snack('${e.student.fullName.isNotEmpty ? e.student.fullName : e.student.username} has been promoted.');
    } catch (err) {
      _snack(err is LiveClassApiException ? err.message : 'Could not promote.');
      setState(() => _busy.remove(e.id));
    }
  }

  Future<void> _tryRejoin(SessionWaitlistEntry e) async {
    setState(() => _busy.add(e.id));
    try {
      final result = await LiveClassApi.sessions.join(e.sessionId);
      if (!mounted) return;
      if (result.waitlisted) {
        setState(() => _busy.remove(e.id));
        _snack("No seat has opened up yet — you'll stay on the waitlist.");
        return;
      }
      Navigator.push(context, MaterialPageRoute(builder: (_) => LiveSessionScreen(sessionId: e.sessionId, initialResult: result)));
    } catch (err) {
      if (!mounted) return;
      setState(() => _busy.remove(e.id));
      _snack(err is LiveClassApiException ? err.message : 'Could not join.');
    }
  }

  @override
  Widget build(BuildContext context) {
    final title = _isManageView
        ? (widget.classroomTitle.isNotEmpty ? 'Waitlist — ${widget.classroomTitle}' : 'Waitlist')
        : 'My Waitlist';
    return Scaffold(
      backgroundColor: _kBg,
      appBar: liveClassAppBar(title),
      body: _loading
          ? const LiveClassLoading()
          : _error != null
              ? LiveClassErrorState(message: _error!, onRetry: _load)
              : RefreshIndicator(
                  color: _kNavy,
                  onRefresh: _load,
                  child: _entries.isEmpty
                      ? ListView(
                          children: [
                            Padding(
                              padding: const EdgeInsets.only(top: 100),
                              child: Center(
                                child: Text(
                                  _isManageView ? 'No one is on the waitlist for this session.' : "You're not on any waitlist.",
                                  style: const TextStyle(color: Colors.black45),
                                ),
                              ),
                            ),
                          ],
                        )
                      : ListView.builder(
                          padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
                          itemCount: _entries.length,
                          itemBuilder: (_, i) => _isManageView ? _manageCard(_entries[i], i) : _studentCard(_entries[i]),
                        ),
                ),
    );
  }

  // -------------------------------------------------------------------
  // Teacher/staff — who's waiting for this session
  // -------------------------------------------------------------------
  Widget _manageCard(SessionWaitlistEntry e, int position) {
    final busy = _busy.contains(e.id);
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.03), blurRadius: 6, offset: const Offset(0, 2))],
      ),
      child: Row(
        children: [
          Container(
            width: 30,
            height: 30,
            decoration: BoxDecoration(color: _kNavy.withOpacity(0.08), shape: BoxShape.circle),
            alignment: Alignment.center,
            child: Text('${position + 1}', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12.5, color: _kNavy)),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(e.student.fullName.isNotEmpty ? e.student.fullName : e.student.username,
                    style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
                const SizedBox(height: 2),
                Row(children: [
                  Text('Waiting since ${_fmtRelative(e.joinedAt)}', style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
                  if (e.notified) ...[
                    const SizedBox(width: 6),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                      decoration: BoxDecoration(color: Colors.green.shade50, borderRadius: BorderRadius.circular(20)),
                      child: Text('Notified', style: TextStyle(fontSize: 9.5, color: Colors.green.shade700, fontWeight: FontWeight.bold)),
                    ),
                  ],
                ]),
              ],
            ),
          ),
          SizedBox(
            height: 32,
            child: ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor: _kNavy,
                foregroundColor: Colors.white,
                padding: const EdgeInsets.symmetric(horizontal: 12),
              ),
              onPressed: busy ? null : () => _promote(e),
              child: busy
                  ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Text('Promote', style: TextStyle(fontSize: 12)),
            ),
          ),
        ],
      ),
    );
  }

  // -------------------------------------------------------------------
  // Student — my own waitlist entries
  // -------------------------------------------------------------------
  Widget _studentCard(SessionWaitlistEntry e) {
    final busy = _busy.contains(e.id);
    final session = _sessionCache[e.sessionId];
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.04), blurRadius: 8, offset: const Offset(0, 2))],
        border: e.notified ? Border.all(color: Colors.green.shade300) : null,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(gradient: _kGradient, borderRadius: BorderRadius.circular(10)),
                child: const Icon(Icons.hourglass_top_rounded, color: Colors.white, size: 19),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(session?.classroomTitle.isNotEmpty == true ? session!.classroomTitle : 'Session #${e.sessionId}',
                        style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13.5), maxLines: 1, overflow: TextOverflow.ellipsis),
                    if (session != null) ...[
                      const SizedBox(height: 2),
                      Text(_fmtDateTime(session.scheduledStart), style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
                    ],
                  ],
                ),
              ),
              if (e.notified)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(color: Colors.green.shade50, borderRadius: BorderRadius.circular(20)),
                  child: Text('Seat opened!', style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: Colors.green.shade700)),
                ),
            ],
          ),
          const SizedBox(height: 10),
          Text('Waiting since ${_fmtRelative(e.joinedAt)}', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: busy ? null : () => _leave(e),
                  style: OutlinedButton.styleFrom(foregroundColor: Colors.red, side: BorderSide(color: Colors.red.shade200)),
                  child: const Text('Leave Waitlist', style: TextStyle(fontSize: 12.5)),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(backgroundColor: _kNavy, foregroundColor: Colors.white),
                  onPressed: busy ? null : () => _tryRejoin(e),
                  child: busy
                      ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Text('Try Enter', style: TextStyle(fontSize: 12.5)),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}