// lib/services/crash_reporting_service.dart
//
// Task 14 — every place that used to just call bare `print("... error:
// $e")` (`services/api_service.dart`, `services/home_api_model_service.dart`,
// `profile/api_service.dart`, `search/api_service.dart`, and the one
// already-fixed call in `profile/screens/profile.dart` — see that file's
// own header comment) now goes through `CrashReportingService.logError()`
// instead. Those were all non-fatal errors — the operation each one sits
// in already has its own fallback (cache read failed so it falls back to
// a fresh fetch, a background refresh failed so the UI just keeps the
// stale-but-still-correct data it already had, etc.) — so none of these
// are "the app is now broken", but a bare `print()` meant they were also
// completely invisible in a release build (print output isn't captured
// anywhere in release) and not tracked anywhere even in debug. This gives
// them one shared, findable destination instead — the same shape as
// `main.dart`'s `_reportCrash()` for FATAL errors (`FlutterError.onError`
// / `PlatformDispatcher.instance.onError` / `runZonedGuarded`), just for
// the non-fatal, already-handled ones.
//
// ⚠️ Same constraint as `main.dart`'s `_reportCrash()`: this project's
// upload doesn't include a `pubspec.yaml` (only `lib/` + `backend/`), so a
// `sentry_flutter`/`firebase_crashlytics` import here would not compile
// against what was actually exported. `logError()` logs locally via
// `developer.log` for now — structured (has a `context` label + the
// error + stack trace, so it's at least filterable/greppable in debug
// output, unlike a scattered `print("some error: $e")` with no common
// prefix) rather than reaching an actual dashboard. Once the package is
// added (see `main.dart`'s header comment for the exact dependency line),
// wire BOTH this function's body AND `main.dart`'s `_reportCrash()` to it
// — that's the one place either needs to change; no call site below
// needs touching again.
import 'dart:developer' as developer;

class CrashReportingService {
  CrashReportingService._();

  /// Non-fatal, already-handled error — log it so it's visible/trackable
  /// instead of silently disappearing into a `print()` that release
  /// builds never show anywhere.
  ///
  /// [context] is a short, stable label for where this happened (e.g.
  /// `"HomeApiModelService.feedCache"`) — NOT the interpolated message
  /// that used to be the whole `print()` call, so the same failure from
  /// different call sites stays groupable once this does reach a real
  /// crash-reporting dashboard.
  static void logError(String context, Object error, {StackTrace? stackTrace}) {
    // TODO: once sentry_flutter / firebase_crashlytics is added (see
    // main.dart's header comment), replace this body with e.g.:
    //   Sentry.captureException(error, stackTrace: stackTrace, hint: Hint.withMap({'context': context}));
    // or
    //   FirebaseCrashlytics.instance.recordError(error, stackTrace, reason: context, fatal: false);
    developer.log(
      context,
      error: error,
      stackTrace: stackTrace,
      level: 900, // below _reportCrash's fatal-crash level (1000) in main.dart — non-fatal
    );
  }
}
