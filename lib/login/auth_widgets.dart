import 'package:flutter/material.dart';
import '../language_service.dart';

// ============================================================
// AUTH SCREENS — SHARED WIDGETS
//
// Login / Signup / Forgot-Password / Complete-Profile — chaaron auth
// screens ab dark mode + i18n support karte hain (home.dart jaisa hi
// pattern — dekho theme_service.dart / language_service.dart). Ye chhota
// language toggle button un chaaron me common hai, isliye ek hi jagah
// rakha hai taaki duplicate na ho.
// ============================================================

/// Chhota EN/हिं toggle chip — home.dart ke `_buildLanguageToggleButton()`
/// jaisa hi behavior: tap karte hi `LanguageService.instance.setLocale()`
/// call hota hai aur poori app (is screen samet) turant naye language me
/// rebuild ho jaati hai — koi setState/context lookup yahan manually nahi
/// karna padta, `ValueListenableBuilder` khud sunta hai.
class AuthLanguageToggle extends StatelessWidget {
  const AuthLanguageToggle({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<Locale>(
      valueListenable: LanguageService.instance.locale,
      builder: (context, locale, _) {
        final cs = Theme.of(context).colorScheme;
        final isHindi = locale.languageCode == 'hi';
        return Material(
          color: Colors.transparent,
          child: InkWell(
            borderRadius: BorderRadius.circular(20),
            onTap: () =>
                LanguageService.instance.setLocale(Locale(isHindi ? 'en' : 'hi')),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: cs.surfaceVariant,
                borderRadius: BorderRadius.circular(20),
                border: Border.all(color: cs.outlineVariant),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.language_rounded, size: 15, color: cs.onSurfaceVariant),
                  const SizedBox(width: 5),
                  Text(
                    isHindi ? 'हिं' : 'EN',
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                      color: cs.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

/// Fixed brand-green success color — reaction colors (home.dart) ki tarah
/// jaan-boojh kar theme-independent hai: "success" ka matlab hamesha
/// green hona chahiye, chahe light mode ho ya dark.
const Color kAuthSuccessColor = Color(0xFF16A34A);