import 'dart:ui' show PlatformDispatcher;

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:timeago/timeago.dart' as timeago;

/// App-wide language state — `theme_service.dart` jaisa hi pattern:
/// singleton `.instance` + `SharedPreferences` me persist. UI side pe
/// `ValueListenableBuilder<Locale>` (dekhein `main.dart`) `locale`
/// notifier ko sunta hai, isliye `setLocale()` call karte hi poori app
/// turant naye language me rebuild ho jaati hai.
class LanguageService {
  LanguageService._internal();
  static final LanguageService instance = LanguageService._internal();

  static const String _prefsKey = 'app_locale';

  /// Filahal sirf English + Hindi. Naya language add karna ho to:
  /// 1) `lib/l10n/app_<code>.arb` banao (app_en.arb copy karke translate karo)
  /// 2) yahan is list me `Locale('<code>')` add karo
  /// 3) `flutter gen-l10n` (ya `flutter run`) chalao
  static const List<Locale> supportedLocales = [
    Locale('en'),
    Locale('hi'),
  ];

  /// `MaterialApp.locale` isi notifier se bind hota hai (dekhein
  /// main.dart ka `ValueListenableBuilder<Locale>`).
  final ValueNotifier<Locale> locale = ValueNotifier<Locale>(const Locale('en'));

  /// App start hote hi ek baar call karo (main() me, runApp se pehle)
  /// taaki saved preference load ho jaaye aur timeago locale messages
  /// register ho jaayein. Agar kabhi call na ho ya load fail ho jaaye,
  /// default English rahega — app kabhi crash nahi karega isi wajah se.
  Future<void> init() async {
    _registerTimeagoLocales();

    try {
      final prefs = await SharedPreferences.getInstance();
      final String? saved = prefs.getString(_prefsKey);

      if (saved != null) {
        locale.value = _fromCode(saved) ?? const Locale('en');
      } else {
        // Pehli baar app khuli hai — device ki language try karo,
        // agar supported hai to wahi use karo, warna English.
        final deviceCode = PlatformDispatcher.instance.locale.languageCode;
        locale.value = _fromCode(deviceCode) ?? const Locale('en');
      }
    } catch (e) {
      locale.value = const Locale('en');
    }
  }

  Future<void> setLocale(Locale newLocale) async {
    locale.value = newLocale;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_prefsKey, newLocale.languageCode);
    } catch (e) {
      // Persist fail ho to bhi in-memory state to already updated hai —
      // agli app-open pe wapas default pe chala jaayega, crash nahi hoga.
    }
  }

  bool get isHindi => locale.value.languageCode == 'hi';

  /// `timeago` package sirf `en`/`es` by default load karta hai
  /// (dekhein package docs). Hindi built-in messages available hain
  /// (`timeago.HiMessages()`), bas explicitly register karni padti hai —
  /// isliye ye yahan `init()` ke andar, sabse pehle call ho raha hai.
  void _registerTimeagoLocales() {
    timeago.setLocaleMessages('hi', timeago.HiMessages());
  }

  Locale? _fromCode(String? code) {
    for (final l in supportedLocales) {
      if (l.languageCode == code) return l;
    }
    return null;
  }
}