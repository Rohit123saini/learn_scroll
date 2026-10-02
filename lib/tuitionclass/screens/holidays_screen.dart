// lib/tuitionclass/screens/holidays_screen.dart
//
// Screen 15 — Holidays (see TUITIONCLASS_SCREEN_ARCHITECTURE.md §15).
// Reached from Classroom Detail's manage sheet (or Schedule Manager),
// owner/admin only.
//
// API: `holidays/?classroom=` CRUD.
//   - Create: POST holidays/ {classroom, schedule?, date, reason}. Leaving
//     `schedule` null marks the date off across every schedule in the
//     classroom; picking a specific schedule scopes the off-day to just
//     that recurring slot.
//   - Delete: DELETE holidays/{id}/.
// The session-generation job on the backend skips these dates automatically
// — this screen only manages the list, it doesn't touch sessions directly.
// Schedules for the dropdown come from `schedules/?classroom=` (read-only
// here, Schedule Manager owns editing them).
//
// Now on the shared TuitionClass design system (tuitionclass_theme.dart).

import 'package:flutter/material.dart';

import '../models/tuitionclass_models.dart';
import '../services/tuitionclass_api_service.dart';
import '../theme/tuitionclass_theme.dart';

String _scheduleLabel(ClassSchedule s) {
  final time = s.startTime.length >= 5 ? s.startTime.substring(0, 5) : s.startTime;
  switch (s.recurrenceType) {
    case RecurrenceType.daily:
      return 'Daily · $time';
    case RecurrenceType.weekday:
      return 'Weekdays · $time';
    case RecurrenceType.weekend:
      return 'Weekends · $time';
    case RecurrenceType.weekly:
      return '${s.daysOfWeek.join(', ')} · $time';
    case RecurrenceType.monthly:
      return 'Monthly (day ${s.dayOfMonth}) · $time';
    case RecurrenceType.yearly:
      return 'Yearly · $time';
    case RecurrenceType.specificDate:
      return 'One-time · $time';
    default:
      return time;
  }
}

// ===========================================================================
// SCREEN
// ===========================================================================
class HolidaysScreen extends StatefulWidget {
  final int classroomId;
  final String classroomTitle;
  const HolidaysScreen({super.key, required this.classroomId, this.classroomTitle = ''});

  @override
  State<HolidaysScreen> createState() => _HolidaysScreenState();
}

class _HolidaysScreenState extends State<HolidaysScreen> {
  List<ClassHoliday> _holidays = [];
  List<ClassSchedule> _schedules = [];
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
      final results = await Future.wait([
        TuitionClassApi.holidays.list(widget.classroomId),
        TuitionClassApi.schedules.list(classroomId: widget.classroomId),
      ]);
      final holidays = (results[0] as PaginatedList<ClassHoliday>).results.toList()..sort((a, b) => a.date.compareTo(b.date));
      final schedules = (results[1] as PaginatedList<ClassSchedule>).results;
      if (!mounted) return;
      setState(() {
        _holidays = holidays;
        _schedules = schedules;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is TuitionClassApiException ? e.message : 'Could not load holidays.';
      });
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _openAddSheet() async {
    // FIX (memory leak): controller was created here and never disposed —
    // every "Mark Holiday" open+close leaked one TextEditingController for
    // the app's lifetime. try/finally guarantees disposal on every exit
    // path (submitted, cancelled, or dismissed).
    final reasonCtrl = TextEditingController();
    try {
      return await _showAddSheet(reasonCtrl);
    } finally {
      reasonCtrl.dispose();
    }
  }

  Future<void> _showAddSheet(TextEditingController reasonCtrl) async {
    DateTime? picked;
    int? scheduleId; // null = off across every schedule
    bool submitting = false;

    final added = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(TuitionClassRadius.sheet))),
      builder: (ctx) {
        return StatefulBuilder(builder: (ctx, setSheetState) {
          Future<void> pickDate() async {
            final now = DateTime.now();
            final result = await showDatePicker(
              context: ctx,
              initialDate: picked ?? now,
              firstDate: DateTime(now.year - 1),
              lastDate: DateTime(now.year + 3),
            );
            if (result != null) setSheetState(() => picked = result);
          }

          Future<void> submit() async {
            if (picked == null) {
              _snack('Select a date.');
              return;
            }
            setSheetState(() => submitting = true);
            try {
              await TuitionClassApi.holidays.create(ClassHoliday(
                id: 0,
                classroomId: widget.classroomId,
                scheduleId: scheduleId,
                date: picked!,
                reason: reasonCtrl.text.trim(),
              ));
              if (!mounted) return;
              Navigator.pop(ctx, true);
            } on TuitionClassApiException catch (e) {
              setSheetState(() => submitting = false);
              _snack(e.message);
            } catch (_) {
              setSheetState(() => submitting = false);
              _snack('Add failed — please try again.');
            }
          }

          return Padding(
            padding: EdgeInsets.only(left: 20, right: 20, top: 20, bottom: MediaQuery.of(ctx).viewInsets.bottom + 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Center(
                  child: Container(
                    width: 36,
                    height: 4,
                    margin: const EdgeInsets.only(bottom: 16),
                    decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(2)),
                  ),
                ),
                const Text('Mark Holiday', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: TuitionClassColors.navy)),
                const SizedBox(height: 4),
                Text('The session-generation job will automatically skip classes on this date.', style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600)),
                const SizedBox(height: 14),
                OutlinedButton.icon(
                  onPressed: pickDate,
                  style: OutlinedButton.styleFrom(
                    alignment: Alignment.centerLeft,
                    padding: const EdgeInsets.symmetric(vertical: 13, horizontal: 14),
                    side: BorderSide(color: Colors.grey.shade300),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(TuitionClassRadius.chip)),
                  ),
                  icon: Icon(Icons.calendar_today_rounded, size: 16, color: picked == null ? Colors.grey.shade600 : TuitionClassColors.navy),
                  label: Text(
                    picked != null ? tuitionClassFmtDateWeekday(picked!) : 'Select date',
                    style: TextStyle(color: picked == null ? Colors.grey.shade600 : TuitionClassColors.navy, fontWeight: FontWeight.w600),
                  ),
                ),
                const SizedBox(height: 12),
                Text('Scope', style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w600, color: Colors.grey.shade700)),
                const SizedBox(height: 6),
                DropdownButtonFormField<int?>(
                  value: scheduleId,
                  decoration: tuitionClassInputDecoration(''),
                  items: [
                    const DropdownMenuItem<int?>(value: null, child: Text('Entire classroom (all schedules)')),
                    ..._schedules.map((s) => DropdownMenuItem<int?>(value: s.id, child: Text(_scheduleLabel(s), overflow: TextOverflow.ellipsis))),
                  ],
                  onChanged: (v) => setSheetState(() => scheduleId = v),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: reasonCtrl,
                  decoration: tuitionClassInputDecoration('Reason (optional) — e.g. Public Holiday'),
                ),
                const SizedBox(height: 16),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: TuitionClassColors.navy,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(TuitionClassRadius.chip)),
                    ),
                    onPressed: submitting ? null : submit,
                    child: submitting
                        ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : const Text('Mark Holiday'),
                  ),
                ),
              ],
            ),
          );
        });
      },
    );
    if (added == true) {
      _snack('Holiday added.');
      _load();
    }
  }

  Future<void> _confirmDelete(ClassHoliday h) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Remove Holiday?'),
        content: Text('${tuitionClassFmtDateWeekday(h.date)} will be restored to the normal schedule.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            style: TextButton.styleFrom(foregroundColor: TuitionClassColors.danger),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    final previous = List<ClassHoliday>.from(_holidays);
    setState(() => _holidays.removeWhere((x) => x.id == h.id));
    try {
      await TuitionClassApi.holidays.delete(h.id);
    } catch (e) {
      if (!mounted) return;
      setState(() => _holidays = previous);
      _snack(e is TuitionClassApiException ? e.message : 'Could not remove.');
    }
  }

  String _scopeLabel(ClassHoliday h) {
    if (h.scheduleId == null) return 'Entire classroom';
    final match = _schedules.where((s) => s.id == h.scheduleId).toList();
    return match.isNotEmpty ? _scheduleLabel(match.first) : 'One schedule';
  }

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final upcoming = _holidays.where((h) => !h.date.isBefore(today)).toList();
    final past = _holidays.where((h) => h.date.isBefore(today)).toList().reversed.toList();

    return Scaffold(
      backgroundColor: TuitionClassColors.bg,
      appBar: tuitionClassAppBar(widget.classroomTitle.isNotEmpty ? 'Holidays — ${widget.classroomTitle}' : 'Holidays'),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: TuitionClassColors.navy,
        onPressed: _openAddSheet,
        icon: const Icon(Icons.event_busy_rounded),
        label: const Text('Mark Holiday'),
      ),
      body: RefreshIndicator(
        color: TuitionClassColors.navy,
        onRefresh: _load,
        child: _loading
            ? const TuitionClassLoading()
            : _error != null
                ? TuitionClassErrorState(message: _error!, onRetry: _load)
                : _holidays.isEmpty
                    ? const TuitionClassEmptyState(
                        icon: Icons.event_busy_outlined,
                        title: 'No holidays marked yet.',
                        subtitle: 'Off-days you mark here are skipped automatically.',
                      )
                    : ListView(
                        padding: const EdgeInsets.fromLTRB(16, 14, 16, 90),
                        children: [
                          if (upcoming.isNotEmpty) ..._section('Upcoming', upcoming),
                          if (past.isNotEmpty) ..._section('Past', past, faded: true),
                        ],
                      ),
      ),
    );
  }

  List<Widget> _section(String title, List<ClassHoliday> items, {bool faded = false}) {
    return [
      Padding(
        padding: const EdgeInsets.only(bottom: 8, left: 2),
        child: Text(title, style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.bold, color: Colors.grey.shade600)),
      ),
      ...items.map((h) => _holidayCard(h, faded: faded)),
      const SizedBox(height: 16),
    ];
  }

  Widget _holidayCard(ClassHoliday h, {bool faded = false}) {
    return Opacity(
      opacity: faded ? 0.55 : 1,
      child: TuitionClassCard(
        child: Row(
          children: [
            const TuitionClassIconBadge(icon: Icons.event_busy_rounded, size: 40),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(tuitionClassFmtDateWeekday(h.date), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13.5)),
                  const SizedBox(height: 2),
                  Text(_scopeLabel(h), style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
                  if (h.reason.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(h.reason, style: TextStyle(fontSize: 11.5, color: Colors.grey.shade700), maxLines: 1, overflow: TextOverflow.ellipsis),
                  ],
                ],
              ),
            ),
            IconButton(
              onPressed: () => _confirmDelete(h),
              icon: const Icon(Icons.delete_outline_rounded, color: TuitionClassColors.danger),
              tooltip: 'Remove',
            ),
          ],
        ),
      ),
    );
  }
}