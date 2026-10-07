// lib/tuitionclass/widgets/class_time_chip.dart
//
// TASK 10.2 — ClassTimeChip: shows a class's start time in the VIEWER's
// local time plus a live status pill:
//   "Starts in 2h 10m"  /  "Starting soon"  /  "Live now"  /  "Ended"
//
// Timing is driven by two shared pieces so 50 chips on a screen cost one
// timer, not 50:
//   * [ClassTicker]  — ONE process-wide 1-second ticker, ref-counted: it
//     only runs while at least one chip is mounted.
//   * [ServerClock]  — server-corrected "now" (see utils/server_clock.dart),
//     so a wrong device clock/timezone can't produce a wrong countdown.
// All text goes through AppLocalizations (EN + HI).

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../models/tuitionclass_models.dart';
import '../theme/tuitionclass_theme.dart';
import '../utils/server_clock.dart';
import '../utils/tuitionclass_datetime.dart';

/// One shared 1 Hz ticker for every [ClassTimeChip].
class ClassTicker {
  ClassTicker._();

  static final ValueNotifier<DateTime> _now = ValueNotifier<DateTime>(ServerClock.now());
  static Timer? _timer;
  static int _listeners = 0;

  /// Subscribe; returns the notifier. Call [release] when done.
  static ValueListenable<DateTime> acquire() {
    // Only refresh the value when nobody is listening yet. Writing it while
    // other chips are mounted would make their ValueListenableBuilders call
    // setState in the middle of this widget's initState (i.e. during build),
    // which Flutter rejects.
    if (_listeners == 0) _now.value = ServerClock.now();
    _listeners++;
    _timer ??= Timer.periodic(const Duration(seconds: 1), (_) => _now.value = ServerClock.now());
    return _now;
  }

  static void release() {
    _listeners = (_listeners - 1).clamp(0, 1 << 30);
    if (_listeners == 0) {
      _timer?.cancel();
      _timer = null;
    }
  }
}

enum ClassTimePhase { upcoming, startingSoon, live, ended, cancelled }

/// Pure phase computation — kept separate so it is trivially testable.
ClassTimePhase classTimePhase({
  required DateTime start,
  required DateTime end,
  required DateTime now,
  String? status,
}) {
  if (status == SessionStatus.cancelled) return ClassTimePhase.cancelled;
  if (status == SessionStatus.completed) return ClassTimePhase.ended;
  if (status == SessionStatus.live) return ClassTimePhase.live;
  final n = now.toUtc();
  if (!n.isBefore(end.toUtc())) return ClassTimePhase.ended;
  if (!n.isBefore(start.toUtc())) {
    // The class time has arrived. If the server still says "scheduled" the
    // teacher hasn't actually started yet, so never claim "Live now" —
    // only the server's `live` status (or, with no status at all, the clock
    // alone, e.g. for a recurring schedule's next occurrence) does that.
    return status == SessionStatus.scheduled ? ClassTimePhase.startingSoon : ClassTimePhase.live;
  }
  if (start.toUtc().difference(n) < const Duration(minutes: 1)) return ClassTimePhase.startingSoon;
  return ClassTimePhase.upcoming;
}

class ClassTimeChip extends StatefulWidget {
  final DateTime start;
  final DateTime end;

  /// Optional server status ('scheduled' / 'live' / 'completed' / 'cancelled').
  /// When given it wins over the clock for live/completed/cancelled.
  final String? status;

  /// Show the local date + time text next to the pill.
  final bool showTime;

  /// Smaller paddings/fonts for dense rows (cards, home strip).
  final bool compact;

  /// Stack the time text above the pill instead of side by side.
  final bool stacked;

  const ClassTimeChip({
    super.key,
    required this.start,
    required this.end,
    this.status,
    this.showTime = true,
    this.compact = false,
    this.stacked = false,
  });

  /// Convenience for a concrete session.
  factory ClassTimeChip.session(ClassSession s, {Key? key, bool showTime = true, bool compact = false, bool stacked = false}) =>
      ClassTimeChip(
        key: key,
        start: s.scheduledStart,
        end: s.scheduledEnd,
        status: s.status,
        showTime: showTime,
        compact: compact,
        stacked: stacked,
      );

  @override
  State<ClassTimeChip> createState() => _ClassTimeChipState();
}

class _ClassTimeChipState extends State<ClassTimeChip> {
  late final ValueListenable<DateTime> _ticker;

  @override
  void initState() {
    super.initState();
    _ticker = ClassTicker.acquire();
  }

  @override
  void dispose() {
    ClassTicker.release();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final fmt = TuitionClassDateTime.of(context);
    final fs = widget.compact ? 11.0 : 12.5;

    final timeText = widget.showTime
        ? Text(
            fmt.dateTime(widget.start),
            style: TextStyle(fontSize: fs, color: Colors.grey.shade700, fontWeight: FontWeight.w600),
            overflow: TextOverflow.ellipsis,
          )
        : null;

    final pill = ValueListenableBuilder<DateTime>(
      valueListenable: _ticker,
      builder: (_, now, __) {
        final phase = classTimePhase(start: widget.start, end: widget.end, now: now, status: widget.status);
        final (String label, Color color) = switch (phase) {
          ClassTimePhase.live => (l10n.classTimeLiveNow, Colors.red),
          ClassTimePhase.startingSoon => (l10n.classTimeStartingSoon, Colors.orange.shade800),
          ClassTimePhase.ended => (l10n.classTimeEnded, Colors.grey.shade600),
          ClassTimePhase.cancelled => (l10n.classTimeCancelled, Colors.grey.shade600),
          ClassTimePhase.upcoming => (
              l10n.classTimeStartsIn(fmt.shortDuration(widget.start.toUtc().difference(now.toUtc()), l10n)),
              TuitionClassColors.navy,
            ),
        };
        return Container(
          padding: EdgeInsets.symmetric(horizontal: widget.compact ? 7 : 9, vertical: widget.compact ? 2 : 3),
          decoration: BoxDecoration(
            color: color.withValues(alpha: 0.10),
            borderRadius: BorderRadius.circular(20),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            if (phase == ClassTimePhase.live) ...[
              Container(width: 6, height: 6, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
              const SizedBox(width: 4),
            ],
            Text(label, style: TextStyle(fontSize: widget.compact ? 10.5 : 11.5, fontWeight: FontWeight.bold, color: color)),
          ]),
        );
      },
    );

    if (timeText == null) return pill;
    if (widget.stacked) {
      return Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
        timeText,
        const SizedBox(height: 4),
        pill,
      ]);
    }
    return Wrap(spacing: 8, runSpacing: 4, crossAxisAlignment: WrapCrossAlignment.center, children: [timeText, pill]);
  }
}
