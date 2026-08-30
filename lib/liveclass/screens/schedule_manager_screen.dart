// lib/liveclass/screens/schedule_manager_screen.dart
//
// Screen 4 — Schedule Manager (see LIVECLASS_SCREEN_ARCHITECTURE.md §4).
// Teacher-facing CRUD over `schedules/` (ClassScheduleViewSet — gated
// server-side to the classroom's own teacher for create/update/delete).
//
// One list + one bottom-sheet form reused for both Add and Edit. Recurrence
// type drives which extra fields the form shows:
//   - weekly  -> days_of_week multi-select is mandatory
//   - monthly -> day_of_month is mandatory
//   - everything else (specific_date/daily/weekday/weekend/yearly) needs
//     neither.
//
// [canManage] defaults to true (this screen is normally reached from the
// teacher's manage panel) but is exposed so a read-only viewer could reuse
// the same list — mirrors the canManage pattern already used by the
// embedded _ScheduleTab in classroom_detail_screen.dart.

import 'package:flutter/material.dart';

import '../models/liveclass_models.dart';
import '../services/liveclass_api_service.dart';
import '../theme/liveclass_theme.dart';
import '../utils/liveclass_datetime.dart';

// FIX (design-system drift — production readiness audit): aliased to the
// shared tokens instead of locally-duplicated hex literals — see the
// matching fix in wishlist_screen.dart / waitlist_screen.dart. This file
// already imports theme/liveclass_theme.dart, so there's no reason for its
// own copy to drift out of sync with LiveClassColors. Zero visual change.
const _kNavy = LiveClassColors.navy;
const _kBg = LiveClassColors.bg;
const _kGradient = LiveClassColors.gradient;

const List<String> _kDaysOfWeek = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const List<String> _kTimezones = ['Asia/Kolkata', 'Asia/Dubai', 'Asia/Karachi', 'UTC', 'America/New_York', 'Europe/London'];

// FIX (timezone bug): this used to be a hardcoded English month array with
// no `.toLocal()` call before reading `.day` off a UTC-parsed DateTime from
// the API — a schedule's start/end date could show shifted by a day for a
// user outside UTC. Delegates to the shared locale + `.toLocal()`-aware
// helper now (same fix already applied to doubts/holidays/submission-
// grading elsewhere in this module).
String _fmtDate(DateTime d) => liveClassFmtDate(d);

String _recurrenceLabel(String type) {
  switch (type) {
    case RecurrenceType.specificDate:
      return 'One-time (specific date)';
    case RecurrenceType.daily:
      return 'Daily';
    case RecurrenceType.weekday:
      return 'Weekdays (Mon–Fri)';
    case RecurrenceType.weekend:
      return 'Weekends (Sat–Sun)';
    case RecurrenceType.weekly:
      return 'Weekly';
    case RecurrenceType.monthly:
      return 'Monthly';
    case RecurrenceType.yearly:
      return 'Yearly';
    default:
      return type;
  }
}

InputDecoration _decoration(String label) => InputDecoration(
      labelText: label,
      filled: true,
      fillColor: _kBg,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
    );

// ===========================================================================
// SCREEN
// ===========================================================================
class ScheduleManagerScreen extends StatefulWidget {
  final int classroomId;
  final bool canManage;
  const ScheduleManagerScreen({super.key, required this.classroomId, this.canManage = true});

  @override
  State<ScheduleManagerScreen> createState() => _ScheduleManagerScreenState();
}

class _ScheduleManagerScreenState extends State<ScheduleManagerScreen> {
  List<ClassSchedule> _schedules = [];
  bool _loading = true;
  String? _error;
  final Set<int> _busyIds = {}; // per-row busy state (pause/resume/delete)

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
      final page = await LiveClassApi.schedules.list(classroomId: widget.classroomId);
      if (!mounted) return;
      setState(() {
        _schedules = page.results;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is LiveClassApiException ? e.message : 'Could not load schedules.';
      });
    }
  }

  // -------------------------------------------------------------------
  // Add / Edit
  // -------------------------------------------------------------------
  Future<void> _openForm({ClassSchedule? existing}) async {
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => _ScheduleFormSheet(classroomId: widget.classroomId, existing: existing),
    );
    if (saved == true) _load();
  }

  // -------------------------------------------------------------------
  // Pause / resume (is_active toggle — full-object PATCH, same pattern the
  // rest of this codebase uses for update())
  // -------------------------------------------------------------------
  Future<void> _toggleActive(ClassSchedule s) async {
    setState(() => _busyIds.add(s.id));
    final updated = ClassSchedule(
      id: s.id,
      classroomId: s.classroomId,
      recurrenceType: s.recurrenceType,
      daysOfWeek: s.daysOfWeek,
      dayOfMonth: s.dayOfMonth,
      startDate: s.startDate,
      endDate: s.endDate,
      startTime: s.startTime,
      durationMinutes: s.durationMinutes,
      timezone: s.timezone,
      isActive: !s.isActive,
    );
    try {
      await LiveClassApi.schedules.update(s.id, updated);
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Update failed.');
    } finally {
      if (mounted) setState(() => _busyIds.remove(s.id));
    }
  }

  Future<void> _confirmDelete(ClassSchedule s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Delete Schedule?'),
        content: const Text('This recurring pattern will be removed. Already-generated sessions will not be affected.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete', style: TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _busyIds.add(s.id));
    try {
      await LiveClassApi.schedules.delete(s.id);
      _load();
    } catch (e) {
      _snack(e is LiveClassApiException ? e.message : 'Delete failed.');
      if (mounted) setState(() => _busyIds.remove(s.id));
    }
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      appBar: AppBar(
        backgroundColor: Colors.white,
        foregroundColor: _kNavy,
        elevation: 0.5,
        title: const Text('Schedule Manager', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: _kNavy))
          : _error != null
              ? _ErrorState(message: _error!, onRetry: _load)
              : RefreshIndicator(
                  color: _kNavy,
                  onRefresh: _load,
                  child: _schedules.isEmpty
                      ? ListView(
                          physics: const AlwaysScrollableScrollPhysics(),
                          children: [
                            Padding(
                              padding: const EdgeInsets.only(top: 100),
                              child: Center(
                                child: Column(
                                  children: [
                                    Icon(Icons.event_repeat_rounded, size: 44, color: Colors.grey.shade400),
                                    const SizedBox(height: 12),
                                    Text('No schedules created yet.', style: TextStyle(color: Colors.grey.shade600)),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        )
                      : ListView.builder(
                          physics: const AlwaysScrollableScrollPhysics(),
                          padding: const EdgeInsets.fromLTRB(16, 16, 16, 90),
                          itemCount: _schedules.length,
                          itemBuilder: (_, i) => _scheduleCard(_schedules[i]),
                        ),
                ),
      floatingActionButton: widget.canManage
          ? FloatingActionButton.extended(
              backgroundColor: _kNavy,
              onPressed: () => _openForm(),
              icon: const Icon(Icons.add_rounded, color: Colors.white),
              label: const Text('Add Schedule', style: TextStyle(color: Colors.white)),
            )
          : null,
    );
  }

  Widget _scheduleCard(ClassSchedule s) {
    final busy = _busyIds.contains(s.id);
    // FIX (i18n / timezone audit — see utils/liveclass_datetime.dart):
    // this used to show the schedule's raw wall-clock startTime + raw
    // timezone name side by side ("18:00 · Asia/Kolkata"), leaving every
    // viewer to convert it to their own time by hand — exactly the gap
    // liveclass_datetime.dart's LiveClassDateTime.scheduleTimeLabel() was
    // written to close (classroom_detail_screen.dart's schedule tab
    // already uses it; this dedicated Schedule Manager screen — the
    // primary place a teacher/co-teacher actually reads these times from —
    // had been missed). Now shows the resolved local time, with the
    // original wall-clock + zone kept as a parenthetical only when the
    // conversion actually changes what's displayed.
    final subtitleParts = <String>[
      LiveClassDateTime.of(context).scheduleTimeLabel(s),
      '${s.durationMinutes} min',
    ];
    String detail;
    switch (s.recurrenceType) {
      case RecurrenceType.weekly:
        detail = s.daysOfWeek.isEmpty ? 'No days selected' : s.daysOfWeek.join(', ');
        break;
      case RecurrenceType.monthly:
        detail = 'Monthly on day ${s.dayOfMonth ?? '-'}';
        break;
      case RecurrenceType.specificDate:
        detail = _fmtDate(s.startDate);
        break;
      default:
        detail = _fmtDate(s.startDate) + (s.endDate != null ? ' – ${_fmtDate(s.endDate!)}' : ' se aage');
    }

    return Opacity(
      opacity: s.isActive ? 1 : 0.55,
      child: Container(
        margin: const EdgeInsets.only(bottom: 12),
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
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(color: _kBg, borderRadius: BorderRadius.circular(10)),
                  child: const Icon(Icons.repeat_rounded, color: _kNavy, size: 18),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(_recurrenceLabel(s.recurrenceType), style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13.5)),
                      const SizedBox(height: 2),
                      Text(detail, style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
                    ],
                  ),
                ),
                if (!s.isActive)
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    margin: const EdgeInsets.only(right: 4),
                    decoration: BoxDecoration(color: Colors.grey.shade200, borderRadius: BorderRadius.circular(10)),
                    child: const Text('Paused', style: TextStyle(fontSize: 10, fontWeight: FontWeight.w600)),
                  ),
              ],
            ),
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: subtitleParts
                  .map((t) => Container(
                        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
                        decoration: BoxDecoration(color: _kBg, borderRadius: BorderRadius.circular(20)),
                        child: Text(t, style: const TextStyle(fontSize: 11, color: _kNavy, fontWeight: FontWeight.w600)),
                      ))
                  .toList(),
            ),
            if (widget.canManage) ...[
              const Divider(height: 22),
              Row(
                children: [
                  Expanded(
                    child: TextButton.icon(
                      onPressed: busy ? null : () => _openForm(existing: s),
                      icon: const Icon(Icons.edit_outlined, size: 16),
                      label: const Text('Edit'),
                    ),
                  ),
                  Expanded(
                    child: TextButton.icon(
                      onPressed: busy ? null : () => _toggleActive(s),
                      icon: Icon(s.isActive ? Icons.pause_circle_outline_rounded : Icons.play_circle_outline_rounded, size: 16),
                      label: Text(s.isActive ? 'Pause' : 'Resume'),
                    ),
                  ),
                  IconButton(
                    onPressed: busy ? null : () => _confirmDelete(s),
                    icon: busy
                        ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.delete_outline_rounded, color: Colors.red, size: 20),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

// ===========================================================================
// Add / Edit bottom sheet
// ===========================================================================
class _ScheduleFormSheet extends StatefulWidget {
  final int classroomId;
  final ClassSchedule? existing;
  const _ScheduleFormSheet({required this.classroomId, this.existing});

  @override
  State<_ScheduleFormSheet> createState() => _ScheduleFormSheetState();
}

class _ScheduleFormSheetState extends State<_ScheduleFormSheet> {
  String _recurrenceType = RecurrenceType.weekly;
  final Set<String> _selectedDays = {};
  int? _dayOfMonth;
  DateTime _startDate = DateTime.now();
  DateTime? _endDate;
  TimeOfDay _startTime = const TimeOfDay(hour: 18, minute: 0);
  final _durationCtrl = TextEditingController(text: '60');
  String _timezone = 'Asia/Kolkata';
  bool _isActive = true;

  bool _saving = false;
  String? _error;

  bool get _isEdit => widget.existing != null;

  @override
  void initState() {
    super.initState();
    final s = widget.existing;
    if (s != null) {
      _recurrenceType = s.recurrenceType;
      _selectedDays.addAll(s.daysOfWeek);
      _dayOfMonth = s.dayOfMonth;
      _startDate = s.startDate;
      _endDate = s.endDate;
      _durationCtrl.text = s.durationMinutes.toString();
      _timezone = _kTimezones.contains(s.timezone) ? s.timezone : 'Asia/Kolkata';
      _isActive = s.isActive;
      final parts = s.startTime.split(':');
      if (parts.length >= 2) {
        _startTime = TimeOfDay(hour: int.tryParse(parts[0]) ?? 18, minute: int.tryParse(parts[1]) ?? 0);
      }
    }
  }

  @override
  void dispose() {
    _durationCtrl.dispose();
    super.dispose();
  }

  Future<void> _pickStartDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _startDate,
      firstDate: DateTime.now().subtract(const Duration(days: 1)),
      lastDate: DateTime.now().add(const Duration(days: 365 * 3)),
    );
    if (!mounted) return;
    if (picked != null) setState(() => _startDate = picked);
  }

  Future<void> _pickEndDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _endDate ?? _startDate,
      firstDate: _startDate,
      lastDate: DateTime.now().add(const Duration(days: 365 * 3)),
    );
    if (!mounted) return;
    if (picked != null) setState(() => _endDate = picked);
  }

  Future<void> _pickStartTime() async {
    final picked = await showTimePicker(context: context, initialTime: _startTime);
    if (!mounted) return;
    if (picked != null) setState(() => _startTime = picked);
  }

  String get _startTimeFormatted =>
      '${_startTime.hour.toString().padLeft(2, '0')}:${_startTime.minute.toString().padLeft(2, '0')}:00';

  Future<void> _submit() async {
    setState(() => _error = null);

    if (_recurrenceType == RecurrenceType.weekly && _selectedDays.isEmpty) {
      setState(() => _error = 'Select at least one day for a weekly schedule.');
      return;
    }
    if (_recurrenceType == RecurrenceType.monthly && (_dayOfMonth == null || _dayOfMonth! < 1 || _dayOfMonth! > 31)) {
      setState(() => _error = 'Give a valid date between 1-31 for monthly.');
      return;
    }
    final duration = int.tryParse(_durationCtrl.text.trim());
    if (duration == null || duration < 1) {
      setState(() => _error = 'Duration must be a valid number.');
      return;
    }
    if (_endDate != null && _endDate!.isBefore(_startDate)) {
      setState(() => _error = 'End date cannot be before start date.');
      return;
    }

    setState(() => _saving = true);
    final draft = ClassSchedule(
      id: widget.existing?.id ?? 0,
      classroomId: widget.classroomId,
      recurrenceType: _recurrenceType,
      daysOfWeek: _recurrenceType == RecurrenceType.weekly ? _selectedDays.toList() : const [],
      dayOfMonth: _recurrenceType == RecurrenceType.monthly ? _dayOfMonth : null,
      startDate: _startDate,
      endDate: _endDate,
      startTime: _startTimeFormatted,
      durationMinutes: duration,
      timezone: _timezone,
      isActive: _isActive,
    );

    try {
      if (_isEdit) {
        await LiveClassApi.schedules.update(widget.existing!.id, draft);
      } else {
        await LiveClassApi.schedules.create(draft);
      }
      if (!mounted) return;
      Navigator.pop(context, true);
    } on LiveClassApiException catch (e) {
      setState(() {
        _saving = false;
        _error = e.message;
      });
    } catch (_) {
      setState(() {
        _saving = false;
        _error = 'Save failed — please try again.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: DraggableScrollableSheet(
        initialChildSize: 0.85,
        minChildSize: 0.5,
        maxChildSize: 0.95,
        expand: false,
        builder: (_, scrollCtrl) => Padding(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
          child: ListView(
            controller: scrollCtrl,
            children: [
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  margin: const EdgeInsets.only(bottom: 14),
                  decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(4)),
                ),
              ),
              Text(_isEdit ? 'Edit Schedule' : 'New Schedule', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 17)),
              const SizedBox(height: 16),

              const Text('Recurrence', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
              const SizedBox(height: 8),
              DropdownButtonFormField<String>(
                value: _recurrenceType,
                decoration: _decoration(''),
                items: const [
                  RecurrenceType.specificDate,
                  RecurrenceType.daily,
                  RecurrenceType.weekday,
                  RecurrenceType.weekend,
                  RecurrenceType.weekly,
                  RecurrenceType.monthly,
                  RecurrenceType.yearly,
                ].map((t) => DropdownMenuItem(value: t, child: Text(_recurrenceLabel(t)))).toList(),
                onChanged: (v) => setState(() => _recurrenceType = v ?? _recurrenceType),
              ),
              const SizedBox(height: 16),

              if (_recurrenceType == RecurrenceType.weekly) ...[
                const Text('Days of Week', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: _kDaysOfWeek.map((d) {
                    final selected = _selectedDays.contains(d);
                    return ChoiceChip(
                      label: Text(d),
                      selected: selected,
                      selectedColor: _kNavy,
                      labelStyle: TextStyle(color: selected ? Colors.white : Colors.black87, fontSize: 12.5),
                      onSelected: (_) => setState(() => selected ? _selectedDays.remove(d) : _selectedDays.add(d)),
                    );
                  }).toList(),
                ),
                const SizedBox(height: 16),
              ],

              if (_recurrenceType == RecurrenceType.monthly) ...[
                const Text('Day of Month', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                const SizedBox(height: 8),
                TextFormField(
                  initialValue: _dayOfMonth?.toString(),
                  keyboardType: TextInputType.number,
                  decoration: _decoration('1–31'),
                  onChanged: (v) => _dayOfMonth = int.tryParse(v.trim()),
                ),
                const SizedBox(height: 16),
              ],

              Row(
                children: [
                  Expanded(
                    child: _pickerField(
                      label: _recurrenceType == RecurrenceType.specificDate ? 'Date' : 'Start Date',
                      value: _fmtDate(_startDate),
                      onTap: _pickStartDate,
                    ),
                  ),
                  if (_recurrenceType != RecurrenceType.specificDate) ...[
                    const SizedBox(width: 10),
                    Expanded(
                      child: _pickerField(
                        label: 'End Date (optional)',
                        value: _endDate != null ? _fmtDate(_endDate!) : '—',
                        onTap: _pickEndDate,
                      ),
                    ),
                  ],
                ],
              ),
              const SizedBox(height: 16),

              Row(
                children: [
                  Expanded(
                    child: _pickerField(label: 'Start Time', value: _startTime.format(context), onTap: _pickStartTime),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: TextFormField(
                      controller: _durationCtrl,
                      keyboardType: TextInputType.number,
                      decoration: _decoration('Duration (min)'),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),

              DropdownButtonFormField<String>(
                value: _timezone,
                decoration: _decoration('Timezone'),
                items: _kTimezones.map((t) => DropdownMenuItem(value: t, child: Text(t))).toList(),
                onChanged: (v) => setState(() => _timezone = v ?? _timezone),
              ),
              const SizedBox(height: 8),

              SwitchListTile(
                value: _isActive,
                onChanged: (v) => setState(() => _isActive = v),
                activeThumbColor: _kNavy,
                contentPadding: EdgeInsets.zero,
                title: const Text('Active', style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w500)),
              ),

              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 4, bottom: 8),
                  child: Text(_error!, style: const TextStyle(color: Colors.red, fontSize: 12.5)),
                ),
              const SizedBox(height: 12),

              SizedBox(
                width: double.infinity,
                height: 48,
                child: DecoratedBox(
                  decoration: BoxDecoration(gradient: _kGradient, borderRadius: BorderRadius.circular(12)),
                  child: Material(
                    color: Colors.transparent,
                    child: InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: _saving ? null : _submit,
                      child: Center(
                        child: _saving
                            ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2))
                            : Text(_isEdit ? 'Save Changes' : 'Create Schedule',
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

  Widget _pickerField({required String label, required String value, required VoidCallback onTap}) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: InputDecorator(
        decoration: _decoration(label),
        child: Text(value, style: const TextStyle(fontSize: 13.5)),
      ),
    );
  }
}

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