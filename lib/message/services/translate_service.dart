// message/services/translate_service.dart
//
// Feature 9 — Real-time message translate, Flutter side.
//
// ⚠️ WIRING NOTE: same as parent_service.dart earlier — no api_client.dart
// was in the uploaded files, so `_baseUrl` + auth-header below are
// placeholders. Swap for the app's real HTTP client / access_token
// storage so this stays consistent with every other network call.

import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../../utils/api.dart';
import '../../services/auth_service.dart';

class TranslateException implements Exception {
  final String message;
  TranslateException(this.message);
  @override
  String toString() => message;
}

class TranslationResult {
  final String sourceText;
  final String translatedText;
  final String targetLang;
  TranslationResult({
    required this.sourceText,
    required this.translatedText,
    required this.targetLang,
  });

  factory TranslationResult.fromJson(Map<String, dynamic> json) => TranslationResult(
        sourceText: json['source_text'] ?? '',
        translatedText: json['translated_text'] ?? '',
        targetLang: json['target_lang'] ?? '',
      );
}

class TranslateService {
  TranslateService._();
  static final TranslateService instance = TranslateService._();

  static String get _baseUrl => '${Api.baseUrl}/message';

  // ============================================================
  // Task 6 — "Translate" as a chat-level on/off permission.
  //
  // Previously the per-message `TranslateToggle` button was always
  // shown. Now it's gated behind a switch in the chat screen's 3-dot
  // menu (see `chat_screen.dart`'s PopupMenuButton). This is a
  // per-device preference (same storage pattern as
  // `getPreferredTranslateLang()` in `language_picker_sheet.dart`),
  // not per-conversation — one switch controls the feature everywhere
  // the app shows translate.
  //
  // Default is ON: the feature already behaved as "always on" before
  // this change, so existing users see no behaviour change until they
  // explicitly turn it off, and new users get translate available by
  // default too.
  static const _kTranslatePermissionKey = 'translate_permission_enabled';
  static const _kListenPermissionKey = 'listen_permission_enabled';
  static const _kTranscribePermissionKey = 'transcribe_permission_enabled';

  // Task 7.1 — Translate, Listen aur Transcribe teeno ab chat 3-dot menu
  // ke on/off toggles hain. Teeno ka default OFF hai (naye users ko
  // bubbles saaf dikhein), aur value per-device persist hoti hai.
  // Pehle se saved value (agar user ne translate ON/OFF kiya tha) waisi
  // hi rehti hai — default sirf tab lagta hai jab kuch saved na ho.
  //
  // Widgets in notifiers ko sunte hain, isliye toggle flip hote hi
  // saare visible bubbles turant update ho jaate hain.
  final ValueNotifier<bool> translateEnabled = ValueNotifier<bool>(false);
  final ValueNotifier<bool> listenEnabled = ValueNotifier<bool>(false);
  final ValueNotifier<bool> transcribeEnabled = ValueNotifier<bool>(false);

  bool _permissionLoaded = false;

  /// Saved preferences disk se load karta hai (teeno toggles). Multiple
  /// baar call karna safe hai — pehli successful load ke baad no-op.
  Future<void> loadTranslatePermission() async {
    if (_permissionLoaded) return;
    try {
      final prefs = await SharedPreferences.getInstance();
      translateEnabled.value = prefs.getBool(_kTranslatePermissionKey) ?? false;
      listenEnabled.value = prefs.getBool(_kListenPermissionKey) ?? false;
      transcribeEnabled.value = prefs.getBool(_kTranscribePermissionKey) ?? false;
      _permissionLoaded = true;
    } catch (_) {
      // Default (false) hi rehne do agar prefs read na ho paaye.
    }
  }

  Future<void> _persist(String key, ValueNotifier<bool> notifier, bool value) async {
    notifier.value = value;
    _permissionLoaded = true;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(key, value);
    } catch (_) {
      // Best-effort — in-memory value is session me phir bhi lagu rehti hai.
    }
  }

  Future<void> setTranslateEnabled(bool value) =>
      _persist(_kTranslatePermissionKey, translateEnabled, value);

  Future<void> setListenEnabled(bool value) =>
      _persist(_kListenPermissionKey, listenEnabled, value);

  Future<void> setTranscribeEnabled(bool value) =>
      _persist(_kTranscribePermissionKey, transcribeEnabled, value);

  // In-memory cache for this app session — avoids re-hitting the network
  // if the user toggles a translated bubble off/on repeatedly. The
  // backend also caches (see `views.py MessageViewSet.translate`), this
  // is just to skip the network round-trip entirely on repeat taps.
  final Map<String, TranslationResult> _cache = {};

  String _cacheKey(String messageId, String targetLang) => '$messageId:$targetLang';

  Future<Map<String, String>> _authHeaders() async {
    final token = await AuthService.getValidToken() ?? '';
    return {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer $token',
    };
  }

  Future<TranslationResult> translate({
    required String messageId,
    required String targetLang,
  }) async {
    final key = _cacheKey(messageId, targetLang);
    final cached = _cache[key];
    if (cached != null) return cached;

    final res = await http.post(
      Uri.parse('$_baseUrl/messages/$messageId/translate/'),
      headers: await _authHeaders(),
      body: jsonEncode({'target_lang': targetLang}),
    );

    if (res.statusCode == 503) {
      throw TranslateException('Translation abhi available nahi hai.');
    }
    if (res.statusCode == 429) {
      throw TranslateException('Bahut fast translate kar rahe ho, thoda ruko.');
    }
    if (res.statusCode != 200) {
      throw TranslateException('Translate nahi ho paaya. Dobara try karo.');
    }

    final result = TranslationResult.fromJson(jsonDecode(res.body));
    _cache[key] = result;
    return result;
  }

  /// Clear a message's cached translation — call this if a message gets
  /// edited on this device (the backend also auto-invalidates via
  /// `updated_at` in its cache key, but the in-memory cache here doesn't
  /// know about edits unless told).
  void invalidate(String messageId) {
    _cache.removeWhere((key, _) => key.startsWith('$messageId:'));
  }
}