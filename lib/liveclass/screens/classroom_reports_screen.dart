// lib/liveclass/screens/classroom_reports_screen.dart
//
// Screen 22 — Classroom Reports (see LIVECLASS_SCREEN_ARCHITECTURE.md §22).
//
// Platform-staff-only queue. The student-side "file a report" action lives
// in Classroom Detail (_openReportDialog) — this screen is purely the
// review side: pending queue by default, with a status filter, and a
// review action per report (`POST classroom-reports/{id}/review/`,
// status: reviewed/action_taken/dismissed + optional admin note). 3+
// pending reports on the same classroom auto-flags it and hides it from
// Explore — this screen doesn't do that itself, it's a backend signal.
//
// API: `GET classroom-reports/` (all reports, staff sees every classroom's;
// filterable by ?status=), `POST classroom-reports/{id}/review/`.
//
// NEW (Pass 14 frontend catch-up §1.4) — second tab: per-message chat
// reports (`ChatMessageReport`, filed from live_session_screen.dart's chat
// long-press menu by any participant, not just staff). Went with the
// frontend doc's recommended "option 1" — a second tab in this same
// screen rather than a sibling `chat_reports_screen.dart` — since this
// screen is already platform-staff-only and role-gated identically, and
// the message-report review flow (status + optional admin note) is the
// same shape as the classroom one, just scoped to a message instead of a
// classroom. ⚠️ `ChatMessageReport`'s exact fields/endpoint are an
// architecture skeleton (frontend doc §1.4/Pass 14 caveat) — confirm
// against `ChatMessageReportSerializer` before shipping.
//
// API (messages tab): `GET chat-message-reports/` (?status=),
// `POST chat-message-reports/{id}/review/`.
//
// Entry point: LiveClassHomeScreen's "My Learning" tab shows a flag icon
// into this screen, gated behind LiveClassHomeScreen.isPlatformStaff (see
// liveclass_home_screen.dart) — that's the platform-staff flag this file
// doesn't have visibility into on its own. This screen itself still takes
// no role param and does no gating of its own; whoever pushes it is
// responsible for the check, same as before.

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';

// FIX (i18n / timezone audit — production readiness pass): this was the
// last screen in the module still on the pre-fix pattern the rest of the
// codebase already moved off of (see doubts_screen.dart, holidays_screen.dart,
// join_requests_screen.dart, classroom_purchases_screen.dart, etc.) — it
// read `.day`/`.hour`/`.year` straight off `d` with no `.toLocal()` call
// first, so a report's `createdAt`/`reviewedAt` (UTC-parsed ISO-8601 from
// the API — see liveclass_models.dart) rendered in the *server's* clock
// time instead of the reviewing staff member's own. It also hand-indexed
// the English-only `kLiveClassMonths` array, which liveclass_theme.dart
// explicitly marks `@Deprecated('... use liveClassFmtDate/DateFormat
// instead')` for exactly this reason. Delegates to the shared, locale +
// timezone-safe helper now, same as every other screen in the module.
String _fmtDateTime(DateTime d, [BuildContext? context]) => liveClassFmtDateTime(d, context);

String _reasonLabel(String reason) {
  switch (reason) {
    case ReportReason.scam:
      return 'Scam';
    case ReportReason.notDelivering:
      return 'Not delivering as promised';
    case ReportReason.inappropriate:
      return 'Inappropriate content';
    default:
      return 'Other';
  }
}

String _statusLabel(String s) {
  switch (s) {
    case ReportStatus.reviewed:
      return 'Reviewed';
    case ReportStatus.actionTaken:
      return 'Action Taken';
    case ReportStatus.dismissed:
      return 'Dismissed';
    default:
      return 'Pending';
  }
}

Color _statusColor(String s) {
  switch (s) {
    case ReportStatus.reviewed:
      return const Color(0xFF1565C0);
    case ReportStatus.actionTaken:
      return const Color(0xFFC62828);
    case ReportStatus.dismissed:
      return Colors.grey.shade600;
    default:
      return Colors.orange.shade800;
  }
}

// ===========================================================================
// SCREEN
// ===========================================================================
class ClassroomReportsScreen extends StatefulWidget {
  const ClassroomReportsScreen({super.key});

  @override
  State<ClassroomReportsScreen> createState() => _ClassroomReportsScreenState();
}

class _ClassroomReportsScreenState extends State<ClassroomReportsScreen> with SingleTickerProviderStateMixin {
  late final TabController _tabCtrl = TabController(length: 2, vsync: this);

  // -- Tab 1: classroom-level reports (unchanged from before the tab split) --
  List<ClassroomReport> _reports = [];
  bool _loading = true;
  String? _error;
  // null == pending (default landing filter — that's the actual queue);
  // other values switch to a status-scoped history view.
  String? _statusFilter = ReportStatus.pending;

  // -- Tab 2: per-message reports (NEW, §1.4) --------------------------------
  List<ChatMessageReport> _msgReports = [];
  bool _msgLoading = true;
  String? _msgError;
  String? _msgStatusFilter = ReportStatus.pending;

  @override
  void initState() {
    super.initState();
    _load();
    _loadMessages();
  }

  @override
  void dispose() {
    _tabCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await LiveClassApi.classroomReports.list(status: _statusFilter);
      final items = res.results.toList()..sort((a, b) => b.createdAt.compareTo(a.createdAt));
      if (!mounted) return;
      setState(() {
        _reports = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load reports.';
      });
    }
  }

  Future<void> _loadMessages() async {
    setState(() {
      _msgLoading = true;
      _msgError = null;
    });
    try {
      final res = await LiveClassApi.chatMessageReports.list(status: _msgStatusFilter);
      final items = res.results.toList()..sort((a, b) => b.createdAt.compareTo(a.createdAt));
      if (!mounted) return;
      setState(() {
        _msgReports = items;
        _msgLoading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _msgLoading = false;
        _msgError = e is LiveClassApiException ? e.message : 'Could not load message reports.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  // Groups same-classroom pending reports so staff can see the 3-strike
  // auto-flag signal coming before it happens.
  Map<int, int> get _pendingCountByClassroom {
    final map = <int, int>{};
    for (final r in _reports) {
      if (r.status == ReportStatus.pending) map[r.classroomId] = (map[r.classroomId] ?? 0) + 1;
    }
    return map;
  }

  Future<void> _openReviewSheet(ClassroomReport r) async {
    // FIX (memory leak): controller was created here and never disposed —
    // every open+close of this sheet leaked one TextEditingController for
    // the app's lifetime. try/finally guarantees disposal on every exit
    // path (submitted, cancelled, or dismissed).
    final noteCtrl = TextEditingController();
    try {
      await _showReviewSheet(r, noteCtrl);
    } finally {
      noteCtrl.dispose();
    }
  }

  Future<void> _showReviewSheet(ClassroomReport r, TextEditingController noteCtrl) async {
    String status = ReportStatus.reviewed;
    bool submitting = false;

    final done = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          Future<void> submit() async {
            setSheetState(() => submitting = true);
            try {
              await LiveClassApi.classroomReports.review(r.id, status: status, adminNote: noteCtrl.text.trim());
              if (!mounted) return;
              Navigator.pop(ctx, true);
            } on LiveClassApiException catch (e) {
              setSheetState(() => submitting = false);
              _snack(e.message);
            } catch (_) {
              setSheetState(() => submitting = false);
              _snack('Could not submit review — please try again.');
            }
          }

          return Padding(
            padding: EdgeInsets.only(left: 20, right: 20, top: 20, bottom: MediaQuery.of(ctx).viewInsets.bottom + 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Review Report', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const SizedBox(height: 4),
                Text(r.classroomTitle.isNotEmpty ? r.classroomTitle : 'Classroom #${r.classroomId}',
                    style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600)),
                const SizedBox(height: 14),
                Text('Decision', style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: Colors.grey.shade700)),
                const SizedBox(height: 6),
                DropdownButtonFormField<String>(
                  value: status,
                  decoration: liveClassInputDecoration(''),
                  items: const {
                    ReportStatus.reviewed: 'Reviewed (no action needed)',
                    ReportStatus.actionTaken: 'Action Taken',
                    ReportStatus.dismissed: 'Dismissed',
                  }.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
                  onChanged: (v) => setSheetState(() => status = v!),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: noteCtrl,
                  maxLines: 3,
                  decoration: liveClassInputDecoration('Admin note (optional) — internal, visible to staff only'),
                ),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(backgroundColor: LiveClassColors.navy, foregroundColor: Colors.white),
                    onPressed: submitting ? null : submit,
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 12),
                      child: submitting
                          ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                          : const Text('Submit Review'),
                    ),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );
    if (done == true) {
      _snack('Report reviewed.');
      _load();
    }
  }

  // NEW (§1.4) — same review flow as _openReviewSheet/_showReviewSheet
  // above, scoped to a ChatMessageReport instead of a ClassroomReport.
  Future<void> _openMessageReviewSheet(ChatMessageReport r) async {
    final noteCtrl = TextEditingController();
    try {
      await _showMessageReviewSheet(r, noteCtrl);
    } finally {
      noteCtrl.dispose();
    }
  }

  Future<void> _showMessageReviewSheet(ChatMessageReport r, TextEditingController noteCtrl) async {
    String status = ReportStatus.reviewed;
    bool submitting = false;

    final done = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          Future<void> submit() async {
            setSheetState(() => submitting = true);
            try {
              await LiveClassApi.chatMessageReports.review(r.id, status: status, adminNote: noteCtrl.text.trim());
              if (!mounted) return;
              Navigator.pop(ctx, true);
            } on LiveClassApiException catch (e) {
              setSheetState(() => submitting = false);
              _snack(e.message);
            } catch (_) {
              setSheetState(() => submitting = false);
              _snack('Could not submit review — please try again.');
            }
          }

          return Padding(
            padding: EdgeInsets.only(left: 20, right: 20, top: 20, bottom: MediaQuery.of(ctx).viewInsets.bottom + 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Review Message Report', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const SizedBox(height: 4),
                if (r.messagePreview.isNotEmpty)
                  Text('"${r.messagePreview}"',
                      maxLines: 2, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12.5, color: Colors.grey.shade600)),
                const SizedBox(height: 14),
                Text('Decision', style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: Colors.grey.shade700)),
                const SizedBox(height: 6),
                DropdownButtonFormField<String>(
                  value: status,
                  decoration: liveClassInputDecoration(''),
                  items: const {
                    ReportStatus.reviewed: 'Reviewed (no action needed)',
                    ReportStatus.actionTaken: 'Action Taken',
                    ReportStatus.dismissed: 'Dismissed',
                  }.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
                  onChanged: (v) => setSheetState(() => status = v!),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: noteCtrl,
                  maxLines: 3,
                  decoration: liveClassInputDecoration('Admin note (optional) — internal, visible to staff only'),
                ),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(backgroundColor: LiveClassColors.navy, foregroundColor: Colors.white),
                    onPressed: submitting ? null : submit,
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 12),
                      child: submitting
                          ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                          : const Text('Submit Review'),
                    ),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );
    if (done == true) {
      _snack('Report reviewed.');
      _loadMessages();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: LiveClassColors.bg,
      appBar: liveClassAppBar('Reports'),
      body: Column(
        children: [
          Material(
            color: Colors.white,
            child: TabBar(
              controller: _tabCtrl,
              labelColor: LiveClassColors.navy,
              unselectedLabelColor: Colors.grey.shade500,
              indicatorColor: LiveClassColors.navy,
              tabs: const [
                Tab(text: 'Classrooms'),
                Tab(text: 'Messages'),
              ],
            ),
          ),
          Expanded(
            child: TabBarView(
              controller: _tabCtrl,
              children: [
                _classroomsTab(),
                _messagesTab(),
              ],
            ),
          ),
        ],
      ),
    );
  }

  // ---------------------------------------------------------------------
  // TAB 1 — classroom reports
  // ---------------------------------------------------------------------
  Widget _classroomsTab() {
    final pendingByClassroom = _pendingCountByClassroom;
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _filterChip('Pending', ReportStatus.pending, _statusFilter, (v) {
                  setState(() => _statusFilter = v);
                  _load();
                }),
                _filterChip('Reviewed', ReportStatus.reviewed, _statusFilter, (v) {
                  setState(() => _statusFilter = v);
                  _load();
                }),
                _filterChip('Action Taken', ReportStatus.actionTaken, _statusFilter, (v) {
                  setState(() => _statusFilter = v);
                  _load();
                }),
                _filterChip('Dismissed', ReportStatus.dismissed, _statusFilter, (v) {
                  setState(() => _statusFilter = v);
                  _load();
                }),
                _filterChip('All', null, _statusFilter, (v) {
                  setState(() => _statusFilter = v);
                  _load();
                }),
              ],
            ),
          ),
        ),
        Expanded(
          child: _loading
              ? const LiveClassLoading()
              : _error != null
                  ? LiveClassErrorState(message: _error!, onRetry: _load)
                  : RefreshIndicator(
                      color: LiveClassColors.navy,
                      onRefresh: _load,
                      child: _reports.isEmpty
                          ? const LiveClassEmptyState(icon: Icons.flag_outlined, title: 'No reports found.')
                          : ListView.builder(
                              padding: const EdgeInsets.fromLTRB(16, 10, 16, 24),
                              itemCount: _reports.length,
                              itemBuilder: (_, i) => _reportCard(_reports[i], pendingByClassroom[_reports[i].classroomId] ?? 0),
                            ),
                    ),
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------
  // TAB 2 — per-message reports (NEW, §1.4)
  // ---------------------------------------------------------------------
  Widget _messagesTab() {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _filterChip('Pending', ReportStatus.pending, _msgStatusFilter, (v) {
                  setState(() => _msgStatusFilter = v);
                  _loadMessages();
                }),
                _filterChip('Reviewed', ReportStatus.reviewed, _msgStatusFilter, (v) {
                  setState(() => _msgStatusFilter = v);
                  _loadMessages();
                }),
                _filterChip('Action Taken', ReportStatus.actionTaken, _msgStatusFilter, (v) {
                  setState(() => _msgStatusFilter = v);
                  _loadMessages();
                }),
                _filterChip('Dismissed', ReportStatus.dismissed, _msgStatusFilter, (v) {
                  setState(() => _msgStatusFilter = v);
                  _loadMessages();
                }),
                _filterChip('All', null, _msgStatusFilter, (v) {
                  setState(() => _msgStatusFilter = v);
                  _loadMessages();
                }),
              ],
            ),
          ),
        ),
        Expanded(
          child: _msgLoading
              ? const LiveClassLoading()
              : _msgError != null
                  ? LiveClassErrorState(message: _msgError!, onRetry: _loadMessages)
                  : RefreshIndicator(
                      color: LiveClassColors.navy,
                      onRefresh: _loadMessages,
                      child: _msgReports.isEmpty
                          ? const LiveClassEmptyState(icon: Icons.forum_outlined, title: 'No message reports found.')
                          : ListView.builder(
                              padding: const EdgeInsets.fromLTRB(16, 10, 16, 24),
                              itemCount: _msgReports.length,
                              itemBuilder: (_, i) => _msgReportCard(_msgReports[i]),
                            ),
                    ),
        ),
      ],
    );
  }

  Widget _filterChip(String label, String? value, String? current, ValueChanged<String?> onSelect) {
    final selected = current == value;
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: ChoiceChip(
        label: Text(label, style: TextStyle(fontSize: 11.5, color: selected ? Colors.white : Colors.black87)),
        selected: selected,
        onSelected: (_) => onSelect(value),
        selectedColor: LiveClassColors.navy,
        backgroundColor: Colors.white,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        side: BorderSide(color: Colors.grey.shade200),
      ),
    );
  }

  Widget _reportCard(ClassroomReport r, int pendingCountForClassroom) {
    final color = _statusColor(r.status);
    final nearAutoFlag = r.status == ReportStatus.pending && pendingCountForClassroom >= 3;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.04), blurRadius: 8, offset: const Offset(0, 2))],
        border: nearAutoFlag ? Border.all(color: Colors.red.shade300) : null,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(r.classroomTitle.isNotEmpty ? r.classroomTitle : 'Classroom #${r.classroomId}',
                        style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14), maxLines: 1, overflow: TextOverflow.ellipsis),
                    const SizedBox(height: 2),
                    Text('by ${r.reportedBy.fullName.isNotEmpty ? r.reportedBy.fullName : r.reportedBy.username}',
                        style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                decoration: BoxDecoration(color: color.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(20)),
                child: Text(_statusLabel(r.status).toUpperCase(), style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: color)),
              ),
            ],
          ),
          if (nearAutoFlag) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                Icon(Icons.flag_rounded, size: 14, color: Colors.red.shade700),
                const SizedBox(width: 6),
                Expanded(
                  child: Text('$pendingCountForClassroom pending reports on this classroom — may cross the auto-flag threshold.',
                      style: TextStyle(fontSize: 11, color: Colors.red.shade700, fontWeight: FontWeight.w600)),
                ),
              ],
            ),
          ],
          const Divider(height: 20),
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(color: LiveClassColors.bg, borderRadius: BorderRadius.circular(20)),
                child: Text(_reasonLabel(r.reason), style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: LiveClassColors.navy)),
              ),
              const Spacer(),
              Text(_fmtDateTime(r.createdAt, context), style: TextStyle(fontSize: 10.5, color: Colors.grey.shade400)),
            ],
          ),
          if (r.description.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(r.description, style: const TextStyle(fontSize: 12.5, height: 1.35)),
          ],
          if (r.status != ReportStatus.pending) ...[
            const SizedBox(height: 10),
            if (r.adminNote.isNotEmpty)
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(color: LiveClassColors.bg, borderRadius: BorderRadius.circular(10)),
                child: Text('Admin note: ${r.adminNote}', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade700)),
              ),
            if (r.reviewedAt != null) ...[
              const SizedBox(height: 6),
              Text('Reviewed ${_fmtDateTime(r.reviewedAt!, context)}', style: TextStyle(fontSize: 10.5, color: Colors.grey.shade400)),
            ],
          ],
          if (r.status == ReportStatus.pending) ...[
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                onPressed: () => _openReviewSheet(r),
                style: OutlinedButton.styleFrom(foregroundColor: LiveClassColors.navy, side: const BorderSide(color: LiveClassColors.navy)),
                child: const Text('Review', style: TextStyle(fontSize: 12.5)),
              ),
            ),
          ],
        ],
      ),
    );
  }

  // NEW (§1.4) — message-report card. Same shape as _reportCard above,
  // minus the classroom-level "near auto-flag" grouping (that signal is
  // classroom-scoped, not defined for individual messages) and swapping
  // the classroom title/reason chip for a message preview.
  Widget _msgReportCard(ChatMessageReport r) {
    final color = _statusColor(r.status);
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.04), blurRadius: 8, offset: const Offset(0, 2))],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Message #${r.messageId}', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
                    const SizedBox(height: 2),
                    Text('reported by ${r.reportedBy.fullName.isNotEmpty ? r.reportedBy.fullName : r.reportedBy.username}',
                        style: TextStyle(fontSize: 11.5, color: Colors.grey.shade500)),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                decoration: BoxDecoration(color: color.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(20)),
                child: Text(_statusLabel(r.status).toUpperCase(), style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: color)),
              ),
            ],
          ),
          const Divider(height: 20),
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(color: LiveClassColors.bg, borderRadius: BorderRadius.circular(20)),
                child: Text(_reasonLabel(r.reason), style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: LiveClassColors.navy)),
              ),
              const Spacer(),
              Text(_fmtDateTime(r.createdAt, context), style: TextStyle(fontSize: 10.5, color: Colors.grey.shade400)),
            ],
          ),
          if (r.messagePreview.isNotEmpty) ...[
            const SizedBox(height: 8),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(color: LiveClassColors.bg, borderRadius: BorderRadius.circular(10)),
              child: Text('"${r.messagePreview}"', style: const TextStyle(fontSize: 12.5, height: 1.35, fontStyle: FontStyle.italic)),
            ),
          ],
          if (r.description.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(r.description, style: const TextStyle(fontSize: 12.5, height: 1.35)),
          ],
          if (r.status != ReportStatus.pending) ...[
            const SizedBox(height: 10),
            if (r.adminNote.isNotEmpty)
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(color: LiveClassColors.bg, borderRadius: BorderRadius.circular(10)),
                child: Text('Admin note: ${r.adminNote}', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade700)),
              ),
            if (r.reviewedAt != null) ...[
              const SizedBox(height: 6),
              Text('Reviewed ${_fmtDateTime(r.reviewedAt!, context)}', style: TextStyle(fontSize: 10.5, color: Colors.grey.shade400)),
            ],
          ],
          if (r.status == ReportStatus.pending) ...[
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                onPressed: () => _openMessageReviewSheet(r),
                style: OutlinedButton.styleFrom(foregroundColor: LiveClassColors.navy, side: const BorderSide(color: LiveClassColors.navy)),
                child: const Text('Review', style: TextStyle(fontSize: 12.5)),
              ),
            ),
          ],
        ],
      ),
    );
  }
}