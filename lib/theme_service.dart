import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// App-wide theme state — Dark/Light mode ke liye reactive service.
///
/// Pattern `auth_service.dart` jaisa hi hai: singleton `.instance` +
/// `SharedPreferences` me persist. UI side pe `ValueListenableBuilder`
/// `themeMode` notifier ko sunta hai, isliye `setThemeMode()` / `toggle()`
/// call karte hi poori app turant rebuild ho jaati hai.
class ThemeService {
  ThemeService._internal();
  static final ThemeService instance = ThemeService._internal();

  static const String _prefsKey = 'theme_mode';

  final ValueNotifier<ThemeMode> themeMode = ValueNotifier<ThemeMode>(ThemeMode.dark);

  Future<void> init() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final String? saved = prefs.getString(_prefsKey);
      themeMode.value = _fromString(saved) ?? ThemeMode.dark;
    } catch (e) {
      themeMode.value = ThemeMode.dark;
    }
  }

  Future<void> toggle() async {
    final next = themeMode.value == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark;
    await setThemeMode(next);
  }

  Future<void> setThemeMode(ThemeMode mode) async {
    themeMode.value = mode;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_prefsKey, _toString(mode));
    } catch (e) {
      // Persist fail ho to bhi in-memory state already updated hai.
    }
  }

  bool get isDark => themeMode.value == ThemeMode.dark;

  ThemeMode? _fromString(String? value) {
    switch (value) {
      case 'light':
        return ThemeMode.light;
      case 'dark':
        return ThemeMode.dark;
      case 'system':
        return ThemeMode.system;
      default:
        return null;
    }
  }

  String _toString(ThemeMode mode) {
    switch (mode) {
      case ThemeMode.light:
        return 'light';
      case ThemeMode.dark:
        return 'dark';
      case ThemeMode.system:
        return 'system';
    }
  }
}

/// ---------------------------------------------------------------------
/// DESIGN TOKENS — `learnscroll_home_final.html` ke CSS variables ka
/// 1:1 Dart port. Poori app (home, assignments, test series) sirf
/// `Theme.of(context).colorScheme` se colors leti hai, isliye har token
/// yahan explicitly set hona ZAROORI hai.
///
/// ⚠️ Pehle `ColorScheme(...)` me sirf primary/secondary/surface/onSurface
/// diye gaye the. Baaki tokens ke Material fallbacks aise the:
///     background       -> surface        (bg aur card ka color same!)
///     surfaceVariant   -> surface        (tinted panels invisible)
///     outlineVariant   -> onSurface      (1px border = solid dark navy)
///     onSurfaceVariant -> onSurface      (muted text = full-contrast ink)
/// Yaani har border black dikhti, muted text ink jaisa dikhta aur cards
/// background me ghul jaate. Isliye neeche poora set explicitly diya hai.
/// ---------------------------------------------------------------------
class AppColors {
  AppColors._();

  // --- light (.screen[data-theme="light"]) ---
  static const Color lightBg = Color(0xFFFFFFFF); // --bg
  static const Color lightSurface = Color(0xFFF6F4FF); // --surface
  static const Color lightSurface2 = Color(0xFFEDE8FF); // --surface2
  static const Color lightPrimary = Color(0xFF5B3DF6); // --primary
  static const Color lightPrimaryInk = Color(0xFFFFFFFF); // --primary-ink
  static const Color lightAccent = Color(0xFFFF6B4A); // --accent
  static const Color lightInk = Color(0xFF1A1625); // --ink
  static const Color lightMuted = Color(0xFF847FA0); // --muted
  static const Color lightBorder = Color(0xFFECE9FB); // --border

  // --- dark (.screen[data-theme="dark"]) ---
  static const Color darkBg = Color(0xFF0F0D18);
  static const Color darkSurface = Color(0xFF1B1730);
  static const Color darkSurface2 = Color(0xFF241F3D);
  static const Color darkPrimary = Color(0xFF8B7CFF);
  static const Color darkPrimaryInk = Color(0xFF0F0D18);
  static const Color darkAccent = Color(0xFFFF8A68);
  static const Color darkInk = Color(0xFFF3F1FF);
  static const Color darkMuted = Color(0xFF9891B8);
  static const Color darkBorder = Color(0xFF2A244A);

  // Status colors — assignments/test-series ke status chips ke liye.
  // Ye brand palette se bahar hain (semantic hain, decorative nahi):
  // "checked = green" har theme me green hi rehna chahiye. Dono modes ke
  // liye alag shade, taaki dark bg pe contrast bana rahe.
  static const Color successLight = Color(0xFF1F9D6C);
  static const Color successDark = Color(0xFF3FD69A);
  static const Color warningLight = Color(0xFFC99A44);
  static const Color warningDark = Color(0xFFF0BE63);
  static const Color dangerLight = Color(0xFFDC2626);
  static const Color dangerDark = Color(0xFFFF6B6B);
  static const Color infoLight = Color(0xFF2563EB);
  static const Color infoDark = Color(0xFF6BA3FF);

  // Legacy aliases — purana code (jo `AppColors.violet` / `.coral` use
  // karta tha) na toote isliye rakhe hain.
  static const Color violet = lightPrimary;
  static const Color coral = lightAccent;
}

class AppTheme {
  AppTheme._();

  static ThemeData get light => _buildTheme(
        brightness: Brightness.light,
        bg: AppColors.lightBg,
        surface: AppColors.lightSurface,
        surface2: AppColors.lightSurface2,
        primary: AppColors.lightPrimary,
        primaryInk: AppColors.lightPrimaryInk,
        accent: AppColors.lightAccent,
        ink: AppColors.lightInk,
        muted: AppColors.lightMuted,
        border: AppColors.lightBorder,
        success: AppColors.successLight,
        warning: AppColors.warningLight,
        danger: AppColors.dangerLight,
        info: AppColors.infoLight,
      );

  static ThemeData get dark => _buildTheme(
        brightness: Brightness.dark,
        bg: AppColors.darkBg,
        surface: AppColors.darkSurface,
        surface2: AppColors.darkSurface2,
        primary: AppColors.darkPrimary,
        primaryInk: AppColors.darkPrimaryInk,
        accent: AppColors.darkAccent,
        ink: AppColors.darkInk,
        muted: AppColors.darkMuted,
        border: AppColors.darkBorder,
        success: AppColors.successDark,
        warning: AppColors.warningDark,
        danger: AppColors.dangerDark,
        info: AppColors.infoDark,
      );

  static ThemeData _buildTheme({
    required Brightness brightness,
    required Color bg,
    required Color surface,
    required Color surface2,
    required Color primary,
    required Color primaryInk,
    required Color accent,
    required Color ink,
    required Color muted,
    required Color border,
    required Color success,
    required Color warning,
    required Color danger,
    required Color info,
  }) {
    final colorScheme = ColorScheme(
      brightness: brightness,
      // --primary / --primary-ink
      primary: primary,
      onPrimary: primaryInk,
      primaryContainer: surface2,
      onPrimaryContainer: ink,
      // --accent
      secondary: accent,
      onSecondary: brightness == Brightness.light ? Colors.white : const Color(0xFF0F0D18),
      secondaryContainer: surface2,
      onSecondaryContainer: ink,
      // errors
      error: danger,
      onError: Colors.white,
      errorContainer: surface2,
      onErrorContainer: ink,
      // --surface / --ink
      surface: surface,
      onSurface: ink,
      // --surface2 / --muted  → tinted panels aur muted text
      surfaceVariant: surface2,
      onSurfaceVariant: muted,
      surfaceContainerHighest: surface2,
      // --border
      outline: muted,
      outlineVariant: border,
      // ⚠️ --bg yahan NAHI jaata: `ColorScheme.background`/`onBackground`
      // Flutter 3.18 me deprecate ho chuke hain aur naye SDKs me nikal
      // diye gaye hain. Page background do jagah se milta hai —
      // `scaffoldBackgroundColor` (neeche) aur `AppThemeTokens.background`
      // (widgets ke liye, `lsBg(context)` helper se).
      shadow: Colors.black,
      scrim: Colors.black,
      inverseSurface: ink,
      onInverseSurface: bg,
      inversePrimary: primaryInk,
    );

    // HTML: --font-body: Inter, --font-head: Sora.
    // google_fonts already pubspec me hai, isliye font assets bundle karne
    // ki zaroorat nahi. Production build me network fetch band karna ho to
    // dekho is file ke sabse neeche wala note.
    final baseTextTheme = ThemeData(brightness: brightness).textTheme;
    final textTheme = GoogleFonts.interTextTheme(baseTextTheme).apply(
      bodyColor: ink,
      displayColor: ink,
    );

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: colorScheme,
      scaffoldBackgroundColor: bg,
      canvasColor: bg,
      dividerColor: border,
      textTheme: textTheme,
      appBarTheme: AppBarTheme(
        backgroundColor: bg,
        foregroundColor: ink,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        titleTextStyle: GoogleFonts.sora(fontSize: 16, fontWeight: FontWeight.w700, color: ink),
      ),
      dividerTheme: DividerThemeData(color: border, thickness: 1, space: 1),
      cardTheme: CardThemeData(
        color: surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(18),
          side: BorderSide(color: border),
        ),
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        modalBackgroundColor: surface,
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: surface,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: GoogleFonts.sora(fontSize: 16, fontWeight: FontWeight.w700, color: ink),
        contentTextStyle: GoogleFonts.inter(fontSize: 13.5, height: 1.45, color: ink),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: brightness == Brightness.light ? ink : surface2,
        contentTextStyle: GoogleFonts.inter(
            fontSize: 13, color: brightness == Brightness.light ? Colors.white : ink),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surface,
        hintStyle: TextStyle(color: muted, fontSize: 13),
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: BorderSide(color: border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: BorderSide(color: border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: BorderSide(color: primary, width: 1.5),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: BorderSide(color: danger),
        ),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(color: primary, linearTrackColor: surface2),
      chipTheme: ChipThemeData(
        backgroundColor: surface,
        side: BorderSide(color: border),
        labelStyle: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: ink),
      ),
      tabBarTheme: TabBarThemeData(
        labelColor: primary,
        unselectedLabelColor: muted,
        indicatorColor: primary,
        dividerColor: Colors.transparent,
        labelStyle: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700),
        unselectedLabelStyle: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600),
      ),
      extensions: [
        AppThemeTokens(
          background: bg,
          surface: surface,
          surface2: surface2,
          violet: primary,
          coral: accent,
          success: success,
          warning: warning,
          danger: danger,
          info: info,
        ),
      ],
    );
  }
}

/// `ThemeExtension` — wo tokens jinke liye `ColorScheme` me koi honest slot
/// nahi hai (success/warning/info status colors). Widgets me:
/// `Theme.of(context).extension<AppThemeTokens>()!.success`
@immutable
class AppThemeTokens extends ThemeExtension<AppThemeTokens> {
  final Color background;
  final Color surface;
  final Color surface2;
  final Color violet;
  final Color coral;
  final Color success;
  final Color warning;
  final Color danger;
  final Color info;

  const AppThemeTokens({
    required this.background,
    required this.surface,
    required this.surface2,
    required this.violet,
    required this.coral,
    required this.success,
    required this.warning,
    required this.danger,
    required this.info,
  });

  /// Shortcut — har screen me `.extension<AppThemeTokens>()!` likhne se
  /// bachne ke liye. Theme me extension na ho (koi custom ThemeData) to
  /// light tokens fallback me milte hain, crash nahi hota.
  static AppThemeTokens of(BuildContext context) =>
      Theme.of(context).extension<AppThemeTokens>() ?? _fallback;

  static const AppThemeTokens _fallback = AppThemeTokens(
    background: AppColors.lightBg,
    surface: AppColors.lightSurface,
    surface2: AppColors.lightSurface2,
    violet: AppColors.lightPrimary,
    coral: AppColors.lightAccent,
    success: AppColors.successLight,
    warning: AppColors.warningLight,
    danger: AppColors.dangerLight,
    info: AppColors.infoLight,
  );

  @override
  AppThemeTokens copyWith({
    Color? background,
    Color? surface,
    Color? surface2,
    Color? violet,
    Color? coral,
    Color? success,
    Color? warning,
    Color? danger,
    Color? info,
  }) {
    return AppThemeTokens(
      background: background ?? this.background,
      surface: surface ?? this.surface,
      surface2: surface2 ?? this.surface2,
      violet: violet ?? this.violet,
      coral: coral ?? this.coral,
      success: success ?? this.success,
      warning: warning ?? this.warning,
      danger: danger ?? this.danger,
      info: info ?? this.info,
    );
  }

  @override
  AppThemeTokens lerp(ThemeExtension<AppThemeTokens>? other, double t) {
    if (other is! AppThemeTokens) return this;
    return AppThemeTokens(
      background: Color.lerp(background, other.background, t)!,
      surface: Color.lerp(surface, other.surface, t)!,
      surface2: Color.lerp(surface2, other.surface2, t)!,
      violet: Color.lerp(violet, other.violet, t)!,
      coral: Color.lerp(coral, other.coral, t)!,
      success: Color.lerp(success, other.success, t)!,
      warning: Color.lerp(warning, other.warning, t)!,
      danger: Color.lerp(danger, other.danger, t)!,
      info: Color.lerp(info, other.info, t)!,
    );
  }
}

// ---------------------------------------------------------------------
// 🔧 PRODUCTION NOTE — google_fonts pehli baar font ko network se fetch
// karke cache karta hai. Offline-first build chahiye to:
//   1) Sora + Inter ke .ttf `assets/fonts/` me daalo aur pubspec me
//      register karo (google_fonts unhi ko pick kar lega), aur
//   2) main() me: GoogleFonts.config.allowRuntimeFetching = false;
// Bina iske pehle frame pe system font dikhega, phir font swap hoga —
// crash kabhi nahi hota, bas ek chhota flash aata hai.
// ---------------------------------------------------------------------