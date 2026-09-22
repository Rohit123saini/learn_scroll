// ============================================================
// LIVECLASS SETTINGS — bridge to the app's real Language/Theme services
//
// This controller used to keep its OWN locale/theme (separate SharedPreferences
// keys), so the liveclass Settings tab changed nothing in the rest of the app
// and the app's own choice was ignored by it. It now delegates to the single
// source of truth — `LanguageService` and `ThemeService` (both already bound to
// `MaterialApp` in main.dart) — and only mirrors their notifiers so
// `settings_screen.dart` (which listens to this ChangeNotifier) still rebuilds.
//
// Public API is unchanged (`locale`, `themeMode`, `setLocale`, `setThemeMode`,
// `load`), so screens and `main_example.dart` keep working as-is.
// ============================================================

import 'package:flutter/material.dart';

import '../../language_service.dart';
import '../../theme_service.dart';

class LiveClassSettingsController extends ChangeNotifier {
  static const supportedLocales = [Locale('en'), Locale('hi')];

  LiveClassSettingsController() {
    LanguageService.instance.locale.addListener(_changed);
    ThemeService.instance.themeMode.addListener(_changed);
  }

  void _changed() => notifyListeners();

  /// The app always has a concrete locale (device language if supported, else
  /// English), so "follow system" is represented by the app's own resolved one.
  Locale? get locale => LanguageService.instance.locale.value;
  ThemeMode get themeMode => ThemeService.instance.themeMode.value;

  /// Nothing to load — both services are initialised in `main()`.
  Future<void> load() async => notifyListeners();

  Future<void> setLocale(Locale? locale) async {
    if (locale == null) {
      // "System": resolve the device language against what we support.
      final device = WidgetsBinding.instance.platformDispatcher.locale.languageCode;
      locale = supportedLocales.firstWhere(
        (l) => l.languageCode == device,
        orElse: () => supportedLocales.first,
      );
    }
    await LanguageService.instance.setLocale(locale);
  }

  Future<void> setThemeMode(ThemeMode mode) => ThemeService.instance.setThemeMode(mode);

  @override
  void dispose() {
    LanguageService.instance.locale.removeListener(_changed);
    ThemeService.instance.themeMode.removeListener(_changed);
    super.dispose();
  }
}
