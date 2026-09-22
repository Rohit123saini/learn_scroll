// ============================================================
// TEST SERIES — CONFIG
//
// Saare tunable numbers ek jagah. Screens/services me magic number
// nahi — kuch badalna ho to sirf yahin badlo.
// ============================================================

class TsConfig {
  TsConfig._();

  /// 🔧 CONFIRM WITH BACKEND — `testseries.urls` root URLconf me kahan mount
  /// hai. `attempts` router pe registered hai, isliye uska path
  /// `{mount}/attempts/` hai, `{mount}/testseries/attempts/` NAHI.
  static const String mount = '/testseries';

  // ---------------- network ----------------
  static const Duration getTimeout = Duration(seconds: 15);
  static const Duration postTimeout = Duration(seconds: 20);
  static const Duration submitTimeout = Duration(seconds: 30);
  static const Duration uploadTimeout = Duration(seconds: 180);

  /// Sirf idempotent GET retry hote hain (network / timeout / 5xx).
  static const int getRetries = 2;
  static const Duration retryBaseDelay = Duration(milliseconds: 600);

  /// DRF pagination follow karte waqt safety cap (infinite loop se bachne ke liye).
  static const int maxPages = 25;

  // ---------------- attempt player ----------------
  static const Duration draftSaveDebounce = Duration(milliseconds: 500);
  static const Duration draftMaxAge = Duration(days: 14);

  /// Itne minute bache to ek baar warning snack dikhta hai.
  static const List<int> timeWarningMinutes = [10, 5, 1];
  static const int lowTimeMinutes = 5;

  /// Time-up pe auto-submit fail ho (network) to itni baar backoff ke saath retry.
  static const int autoSubmitRetries = 3;

  static const int maxPhotoBytes = 10 * 1024 * 1024;
  static const double maxPhotoDimension = 2048;
  static const int photoQuality = 85;

  /// Text answer ki upper limit (UI-side guard; backend limit alag ho sakti hai).
  static const int maxTextAnswerChars = 10000;

  // ---------------- result ----------------
  /// Result screen pe green/red ka cutoff. Backend koi passing marks expose
  /// nahi karta, isliye ye sirf visual hint hai.
  static const double passFraction = 0.4;

  // ---------------- list / detail ----------------
  /// List me itne se kam card dikhein aur agla page baaki ho to auto-load.
  static const int viewportFillMinItems = 6;
  static const int reviewsPreviewCount = 3;
  static const Duration searchDebounce = Duration(milliseconds: 250);
}
