// lib/liveclass/utils/liveclass_datetime.dart
//
// Shared locale-aware date/time formatting + IANA-timezone conversion for
// the LiveClass module.
//
// FIX (i18n / timezone audit — replaces ad-hoc formatting scattered across
// the module):
//
//  1. classroom_detail_screen.dart, classroom_purchases_screen.dart,
//     join_requests_screen.dart, sessions_list_screen.dart and
//     liveclass_theme.dart each hand-rolled their own `_kMonths` English
//     month-name array and built date strings by hand
//     (`'${d.day} ${_kMonths[d.month]} ${d.year}'`). Every date in the
//     module rendered in English no matter the device's language.
//     Replaced with `intl`'s locale-aware `DateFormat`, which produces
//     the right month/weekday names, ordering and separators for
//     whatever locale the device (or the app) is running in.
//
//  2. None of those helpers ever called `.toLocal()` before reading
//     `.hour` / `.day` / `.month` off a `DateTime` that came from the
//     API's UTC ISO-8601 timestamps (`scheduled_start`, `created_at`,
//     etc. — see `_dt()` / `DateTime.parse()` in liveclass_models.dart,
//     which parses the 'Z'-suffixed strings Django returns into
//     UTC-aware `DateTime`s). Reading `.hour` straight off those prints
//     the *UTC* clock time — a class scheduled for 6:00 PM IST rendered
//     as 12:30 PM for every single user, regardless of where they were.
//     Every formatting helper below normalises to the device's local
//     time first.
//
//  3. `ClassSchedule` (the *recurring* definition, as opposed to a
//     concrete `ClassSession` instance) has no absolute instant at all —
//     just a wall-clock `startTime` ("HH:mm:ss") plus the IANA zone name
//     the teacher picked when creating it (`timezone`, e.g.
//     "Asia/Kolkata"). The old UI printed that raw pair directly
//     ("10:00:00 · Asia/Kolkata"), leaving a student in Dubai or New
//     York to convert it themselves. Turning "10:00 in a named zone"
//     into "what that means on my device" needs real IANA tz-database
//     rules (DST, historical offset changes, etc.) that core Dart's
//     `DateTime` simply doesn't know — hence `package:timezone` below.
//
// New pubspec.yaml dependencies this file needs (not part of this
// screen-only file set, so add them where the app's pubspec lives):
//   intl: ^0.19.0
//   timezone: ^0.9.4
//
// Nothing here requires touching main.dart: `LiveClassDateTime.of(context)`
// reads the live app/device locale straight from `Localizations` on every
// call, so it stays correct even through in-app language switches. If the
// app also wants `intl`'s OWN default locale set globally (e.g. for other
// modules), that's a separate one-time `Intl.defaultLocale = ...` call in
// main.dart — optional, not required for anything in this file.

import 'package:flutter/widgets.dart';
import 'package:intl/intl.dart';
import 'package:timezone/timezone.dart' as tz;
import 'package:timezone/data/latest_all.dart' as tz_data;

import '../models/liveclass_models.dart';

class LiveClassDateTime {
  LiveClassDateTime._(this._locale);

  final String? _locale;
  static bool _tzReady = false;

  /// Build a formatter bound to the current app/device locale. Cheap to
  /// call from a `build()` method — it does no I/O after the first call
  /// (which lazily loads the IANA tz database once, process-wide).
  factory LiveClassDateTime.of(BuildContext context) {
    _ensureTzData();
    return LiveClassDateTime._(Localizations.maybeLocaleOf(context)?.toString());
  }

  static void _ensureTzData() {
    if (_tzReady) return;
    tz_data.initializeTimeZones();
    _tzReady = true;
  }

  // ---------------------------------------------------------------------
  // Absolute instants (ClassSession.scheduledStart, createdAt, etc.) —
  // these already carry a real UTC instant from the API, so formatting
  // them is just "convert to local, then format for this locale".
  // ---------------------------------------------------------------------

  /// e.g. "29 Aug 2026" / "29 août 2026" / "2026年8月29日" depending on locale.
  String date(DateTime d) => DateFormat.yMMMd(_locale).format(d.toLocal());

  /// e.g. "Sat, 29 Aug 2026".
  String dateWeekday(DateTime d) => DateFormat('EEE, d MMM y', _locale).format(d.toLocal());

  /// e.g. "6:00 PM" (12h or 24h chosen per-locale by intl, matching
  /// platform convention).
  String time(DateTime d) => DateFormat.jm(_locale).format(d.toLocal());

  String dateTime(DateTime d) => '${date(d)} · ${time(d)}';

  /// Short weekday label for calendar strips, e.g. "Mon" / "lun." / "月".
  String weekdayShort(DateTime d) => DateFormat.E(_locale).format(d);

  // ---------------------------------------------------------------------
  // ClassSchedule — a wall-clock time in a *named* zone, not an instant.
  // ---------------------------------------------------------------------

  /// Resolves a recurring [ClassSchedule]'s wall-clock `startTime` (set in
  /// the teacher's chosen `schedule.timezone`) to a real instant, then
  /// returns it as the device's local time — for the given calendar date
  /// (defaults to the schedule's own `startDate`).
  ///
  /// Falls back to treating the stored time as already device-local if
  /// the zone name is missing or unrecognised, matching the fallback the
  /// backend itself already uses for a bad `schedule.timezone` (see
  /// tasks.py's occurrence builder) — so a corrupt/legacy zone name
  /// degrades gracefully instead of crashing the screen.
  DateTime? resolveScheduleInstant(ClassSchedule s, {DateTime? onDate}) {
    final day = onDate ?? s.startDate;
    final parts = s.startTime.split(':');
    if (parts.isEmpty) return null;
    final hour = int.tryParse(parts[0]) ?? 0;
    final minute = parts.length > 1 ? (int.tryParse(parts[1]) ?? 0) : 0;
    final second = parts.length > 2 ? (int.tryParse(parts[2]) ?? 0) : 0;

    try {
      final location = tz.getLocation(s.timezone);
      final zoned = tz.TZDateTime(location, day.year, day.month, day.day, hour, minute, second);
      return zoned.toLocal();
    } catch (_) {
      return DateTime(day.year, day.month, day.day, hour, minute, second);
    }
  }

  /// "6:00 PM your time" — with a short "(set as 10:00 AM Asia/Kolkata)"
  /// note appended only when the conversion actually changes the clock
  /// time shown, so a viewer who happens to share the teacher's zone
  /// isn't shown redundant/confusing noise.
  String scheduleTimeLabel(ClassSchedule s) {
    final local = resolveScheduleInstant(s);
    if (local == null) return s.startTime;

    final localLabel = time(local);
    final rawLabel = _rawWallClockLabel(s.startTime);
    if (rawLabel == null || rawLabel == localLabel) {
      return '$localLabel your time';
    }
    return '$localLabel your time (set as $rawLabel ${s.timezone})';
  }

  /// Formats the schedule's stored "HH:mm:ss" exactly as written by the
  /// teacher, with no zone conversion — used only as the parenthetical
  /// "originally set as ..." note above.
  String? _rawWallClockLabel(String startTime) {
    final parts = startTime.split(':');
    if (parts.length < 2) return null;
    final hour = int.tryParse(parts[0]);
    final minute = int.tryParse(parts[1]);
    if (hour == null || minute == null) return null;
    final probe = DateTime(2000, 1, 1, hour, minute);
    return DateFormat.jm(_locale).format(probe);
  }
}