// lib/accessibility_service.dart
//
// App-wide text size (accessibility). Same shape as theme_service.dart:
// singleton + ValueNotifier, persisted in SharedPreferences (first-frame
// safe, offline safe) and best-effort synced to the account through
// `UserPreference.font_scale` (PATCH /profile/preferences/me/), so the
// choice follows the user to a new device.
//
// The chosen step MULTIPLIES the device's own font-size setting (see the
// MaterialApp `builder` in main.dart) — someone who already set a large
// system font keeps it, and this adds on top, within a safe clamp.

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'services/user_preferences_api.dart';

enum FontScaleStep { small, normal, large, xlarge }

extension FontScaleStepX on FontScaleStep {
  /// Wire value — must match `UserPreference.FontScale` on the backend.
  String get key => name;

  double get factor {
    switch (this) {
      case FontScaleStep.small:
        return 0.9;
      case FontScaleStep.normal:
        return 1.0;
      case FontScaleStep.large:
        return 1.15;
      case FontScaleStep.xlarge:
        return 1.3;
    }
  }

  static FontScaleStep? parse(String? value) {
    for (final s in FontScaleStep.values) {
      if (s.name == value) return s;
    }
    return null;
  }
}

class AccessibilityService {
  AccessibilityService._();
  static final AccessibilityService instance = AccessibilityService._();

  static const String _prefsKey = 'font_scale';

  /// Upper bound for (device scale x our step). Beyond this most layouts
  /// overflow; the device setting alone is still honoured up to here.
  static const double maxEffectiveScale = 1.8;
  static const double minEffectiveScale = 0.8;

  final ValueNotifier<FontScaleStep> fontScale = ValueNotifier<FontScaleStep>(FontScaleStep.normal);

  /// Same stale-response guard as ThemeService: once the user changes it
  /// themselves, a slow backend pull must not overwrite that choice.
  bool _acceptBackendSync = true;

  Future<void> init() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      fontScale.value = FontScaleStepX.parse(prefs.getString(_prefsKey)) ?? FontScaleStep.normal;
    } catch (_) {
      fontScale.value = FontScaleStep.normal;
    }
    _syncFromBackend(); // fire-and-forget
  }

  Future<void> resyncFromBackend() {
    _acceptBackendSync = true;
    return _syncFromBackend();
  }

  Future<void> _syncFromBackend() async {
    final data = await UserPreferencesApi.fetch();
    if (!_acceptBackendSync) return;
    final server = FontScaleStepX.parse(data?['font_scale'] as String?);
    if (server == null || server == fontScale.value) return;
    fontScale.value = server;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_prefsKey, server.key);
    } catch (_) {}
  }

  Future<void> setFontScale(FontScaleStep step) async {
    _acceptBackendSync = false;
    fontScale.value = step;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_prefsKey, step.key);
    } catch (_) {}
    UserPreferencesApi.update({'font_scale': step.key});
  }

  /// device scale x chosen step, clamped.
  double effectiveScale(double deviceScale) =>
      (deviceScale * fontScale.value.factor).clamp(minEffectiveScale, maxEffectiveScale).toDouble();
}
