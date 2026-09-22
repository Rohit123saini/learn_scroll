// message/services/translate_service.dart
//
// Feature 9 — Real-time message translate, Flutter side.
//
// ⚠️ WIRING NOTE: same as parent_service.dart earlier — no api_client.dart
// was in the uploaded files, so `_baseUrl` + auth-header below are
// placeholders. Swap for the app's real HTTP client / access_token
// storage so this stays consistent with every other network call.

import 'dart:convert';
import 'package:http/http.dart' as http;
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