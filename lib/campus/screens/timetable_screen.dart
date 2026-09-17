import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../widgets/error_widgets.dart';
import '../../widgets/ls_ui.dart';
import '../../widgets/skeletons.dart';
import '../models/campus_models.dart';
import '../services/campus_service.dart';

// ============================================================
// CAMPUS — TIMETABLE
//
// `TimetableEntry` sirf ids rakhta hai (section, subject, staff, time_slot,
// room) — koi nested detail nahi. Isliye screen ko `TimeSlot` alag se
// laana padta hai aur client side pe join karna padta hai.
//
// Din-wise tabs isliye, grid nahi: phone pe 7 columns × 8 rows ka grid
// padha hi nahi jaata. Ek din ek list = asli use case ("aaj kya hai").
// Default tab aaj ka din hai.
// ============================================================

class TimetableScreen extends StatefulWidget {
  final String sectionId;
  final String sectionLabel;
  final String campusId;
  final Map<String, Subject> subjects;

  const TimetableScreen({
    super.key,
    required this.sectionId,
    required this.sectionLabel,
    required this.campusId,
    this.subjects = const {},
  });

  @override
  State<TimetableScreen> createState() => _TimetableScreenState();
}

class _TimetableScreenState extends State<TimetableScreen> {
  bool _loading = true;
  String? _error;

  List<TimetableEntry> _entries = const [];
  Map<String, TimeSlot> _slotsById = const {};

  /// Django ka `weekday()` 0=Monday deta hai — `TimeSlot.dayOfWeek` bhi
  /// wahi convention follow karta hai, isliye direct match karta hai.
  late int _day = DateTime.now().weekday - 1;

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
        CampusService.timetable(sectionId: widget.sectionId),
        CampusService.timeSlots(widget.campusId),
      ]);
      if (!mounted) return;
      final entries = results[0] as List<TimetableEntry>;
      final slots = results[1] as List<TimeSlot>;
      setState(() {
        _entries = entries;
        _slotsById = {for (final s in slots) s.id: s};
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  /// Ek din ke periods, time se sorted.
  List<({TimetableEntry entry, TimeSlot slot})> _forDay(int day) {
    final rows = <({TimetableEntry entry, TimeSlot slot})>[];
    for (final e in _entries) {
      final slot = _slotsById[e.timeSlotId];
      if (slot == null) continue; // slot delete ho gaya — row skip
      if (slot.dayOfWeek != day) continue;
      rows.add((entry: e, slot: slot));
    }
    rows.sort((a, b) => a.slot.startTime.compareTo(b.slot.startTime));
    return rows;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;

    return Scaffold(
      appBar: AppBar(
        title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l10n.campusTimetableTitle, style: LsType.head(context, size: 15)),
          Text(widget.sectionLabel,
              style: TextStyle(fontSize: 11.5, color: cs.onSurfaceVariant)),
        ]),
      ),
      body: RefreshIndicator(onRefresh: _load, child: _body(cs, l10n)),
    );
  }

  Widget _body(ColorScheme cs, AppLocalizations l10n) {
    if (_loading) {
      return ListView(padding: const EdgeInsets.all(14), children: const [
        LsSkeletonBox(height: 40),
        SizedBox(height: 14),
        LsSkeletonBox(height: 66),
        SizedBox(height: 8),
        LsSkeletonBox(height: 66),
      ]);
    }
    if (_error != null) {
      return ListView(children: [
        const SizedBox(height: 60),
        ErrorStateWidget(
          title: l10n.campusTimetableLoadFailed,
          subtitle: _error,
          retryLabel: l10n.retry,
          onRetry: _load,
        ),
      ]);
    }

    final rows = _forDay(_day);

    return Column(children: [
      const SizedBox(height: 12),
      LsFilterChips(
        labels: l10n.localeName == 'hi'
            ? const ['सोम', 'मंगल', 'बुध', 'गुरु', 'शुक्र', 'शनि', 'रवि']
            : const ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
        selectedIndex: _day,
        onSelected: (i) => setState(() => _day = i),
      ),
      const SizedBox(height: 12),
      Expanded(
        child: rows.isEmpty
            ? EmptyStateWidget(
                icon: Icons.event_available_outlined,
                title: l10n.campusNoClassesToday,
              )
            : ListView.builder(
                padding: const EdgeInsets.fromLTRB(14, 0, 14, 24),
                itemCount: rows.length,
                itemBuilder: (_, i) {
                  final row = rows[i];
                  final subject = widget.subjects[row.entry.subjectId];
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: LsCard(
                      padding: const EdgeInsets.fromLTRB(12, 12, 12, 12),
                      child: Row(children: [
                        // Time column — fixed width taaki saare periods
                        // ek line me align rahein, chahe subject ka naam
                        // kitna bhi lamba ho.
                        SizedBox(
                          width: 64,
                          child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(row.slot.prettyStart,
                                    style: LsType.head(context, size: 12.5)),
                                const SizedBox(height: 2),
                                Text(row.slot.prettyEnd,
                                    style: TextStyle(
                                        fontSize: 11, color: cs.onSurfaceVariant)),
                              ]),
                        ),
                        Container(
                          width: 2,
                          height: 34,
                          margin: const EdgeInsets.symmetric(horizontal: 12),
                          decoration: BoxDecoration(
                            color: cs.primary,
                            borderRadius: BorderRadius.circular(1),
                          ),
                        ),
                        Expanded(
                          child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(subject?.name ?? l10n.campusUnknownSubject,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: LsType.head(context, size: 14)),
                                if (row.slot.label.isNotEmpty) ...[
                                  const SizedBox(height: 3),
                                  Text(row.slot.label,
                                      style: TextStyle(
                                          fontSize: 11.5, color: cs.onSurfaceVariant)),
                                ],
                              ]),
                        ),
                      ]),
                    ),
                  );
                },
              ),
      ),
    ]);
  }
}
