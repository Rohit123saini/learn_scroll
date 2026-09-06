// message/widgets/language_picker_sheet.dart
//
// Feature 9 — language picker. Keep this list in sync with
// `translation_service.py`'s `SUPPORTED_LANGUAGES` on the backend.

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

const Map<String, String> kTranslateLanguages = {
  'en': 'English',
  'hi': 'हिन्दी (Hindi)',
  'mr': 'मराठी (Marathi)',
  'ta': 'தமிழ் (Tamil)',
  'te': 'తెలుగు (Telugu)',
  'kn': 'ಕನ್ನಡ (Kannada)',
  'bn': 'বাংলা (Bengali)',
  'gu': 'ગુજરાતી (Gujarati)',
  'pa': 'ਪੰਜਾਬੀ (Punjabi)',
  'ur': 'اردو (Urdu)',
};

const _kPrefKey = 'preferred_translate_lang';

Future<String?> getPreferredTranslateLang() async {
  final prefs = await SharedPreferences.getInstance();
  return prefs.getString(_kPrefKey);
}

Future<void> setPreferredTranslateLang(String code) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.setString(_kPrefKey, code);
}

/// Shows the picker and returns the chosen language code, or null if
/// dismissed. Also persists the choice as the new default (so future
/// translate taps skip straight to the API call — see
/// `translatable_message_widgets.dart`'s `MessageTranslateAction`).
Future<String?> showLanguagePickerSheet(BuildContext context) async {
  final current = await getPreferredTranslateLang();

  final chosen = await showModalBottomSheet<String>(
    context: context,
    backgroundColor: const Color(0xFF17171A),
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (context) {
      return SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(20, 16, 20, 8),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  'Translate to...',
                  style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.bold),
                ),
              ),
            ),
            Flexible(
              child: ListView(
                shrinkWrap: true,
                children: kTranslateLanguages.entries.map((entry) {
                  final isSelected = entry.key == current;
                  return ListTile(
                    title: Text(entry.value, style: const TextStyle(color: Colors.white)),
                    trailing: isSelected
                        ? const Icon(Icons.check, color: Colors.greenAccent)
                        : null,
                    onTap: () => Navigator.pop(context, entry.key),
                  );
                }).toList(),
              ),
            ),
            const SizedBox(height: 8),
          ],
        ),
      );
    },
  );

  if (chosen != null) {
    await setPreferredTranslateLang(chosen);
  }
  return chosen;
}