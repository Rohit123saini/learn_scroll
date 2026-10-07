// lib/tuitionclass/utils/server_clock.dart
//
// TASK 10.2 — single source of "what time is it, really?" for class
// countdowns.
//
// The backend now returns `server_now` (UTC ISO-8601) on sessions,
// schedules and the dashboard (Task 10.1). Every time one of those
// responses is parsed, [ServerClock.sync] stores
// `serverNow - deviceNow`. [ServerClock.now] then returns the device
// clock corrected by that offset, so a phone whose clock/timezone is wrong
// (or manually set) still shows a correct "Starts in 2h 10m" / "Live now".
//
// Deliberately dependency-free (no imports) so the models file can call it
// without creating an import cycle.

class ServerClock {
  ServerClock._();

  static Duration _offset = Duration.zero;

  /// Record the server's clock. Ignores anything unparsable.
  static void sync(dynamic serverNowIso) {
    if (serverNowIso is! String || serverNowIso.isEmpty) return;
    final parsed = DateTime.tryParse(serverNowIso);
    if (parsed == null) return;
    _offset = parsed.toUtc().difference(DateTime.now().toUtc());
  }

  /// Server-corrected current instant (UTC).
  static DateTime now() => DateTime.now().toUtc().add(_offset);

  /// Current correction, mostly for debugging/tests.
  static Duration get offset => _offset;
}
