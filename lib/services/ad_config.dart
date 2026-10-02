// lib/services/ad_config.dart
//
// Task 15 — Meta Audience Network native-ad placement ID.
//
// This used to be a hardcoded placeholder constant
// (`_prodNativeAdPlacementId = 'REPLACE_WITH_PROD_PLACEMENT_ID'`) inside
// `home.dart` itself. The actual real placement ID isn't something that
// can be filled in from the files in this project — it only exists on
// the Meta Audience Network dashboard for this specific app, which
// nobody but the app's own Meta Business account holder can read. So
// this pass does the part that *is* fixable without dashboard access:
// the ID is no longer hardcoded in source at all, using the same
// `String.fromEnvironment` build-time-config pattern this project
// already uses elsewhere for per-environment values.
//
// This means the real ID never has to be committed to source or ship in
// a new app-store release just because it changed — set it per build:
//   flutter run   --dart-define=META_NATIVE_AD_PLACEMENT_ID=1234567890_1234567890
//   flutter build apk --dart-define=META_NATIVE_AD_PLACEMENT_ID=1234567890_1234567890
// (or the equivalent `--dart-define-from-file=config.json`.)
//
// Debug builds are unaffected — `home.dart` still uses
// `NativeAd.testPlacementId` whenever `kDebugMode` is true, regardless
// of whether this is configured, exactly like before.
//
// ⚠️ Before shipping a release build: get the real placement ID from the
// Meta dashboard (your app → Monetization Manager → Placements → the
// native-ad placement for this surface) and pass it via
// `--dart-define=META_NATIVE_AD_PLACEMENT_ID=...` in the release build
// command / CI config. If it's left unset, `AdConfig.isConfigured` is
// false and `home.dart` falls back to the test placement even in release
// mode instead of crashing or passing an empty string to the native SDK
// — see the fallback logic + `debugPrint` warning at the `_buildAd` call
// site.
class AdConfig {
  AdConfig._();

  static const String metaNativeAdPlacementId = String.fromEnvironment(
    'META_NATIVE_AD_PLACEMENT_ID',
    defaultValue: '',
  );

  static bool get isConfigured => metaNativeAdPlacementId.isNotEmpty;
}
