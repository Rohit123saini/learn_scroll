import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'call_api_service.dart';

/// Study Room whiteboard (jo text/sticky-notes likhe gaye hain) se AI
/// summary notes aur quiz banwane ke liye. Same pattern jaisa
/// CallApiService use karta hai — same baseUrl, same auth-token header —
/// taaki backend ka ek hi JWT sab jagah kaam kare.
class AiStudyService {
  // TESTING/PRODUCTION baseUrl CallApiService se hi liya hai — dono
  // services ek hi backend host pe hit karte hain, alag rakhne ki
  // zaroorat nahi.
  static const String _baseUrl = CallApiService.baseUrl;

  static Future<Map<String, String>> _getHeaders() async {
    final prefs = await SharedPreferences.getInstance();
    final token = prefs.getString('access_token') ?? "";
    return {
      "Content-Type": "application/json",
      "Authorization": "Bearer $token",
    };
  }

  /// Backend me naya endpoint add karna hoga (jaise calls/initiate/ hai
  /// waise hi):
  ///   POST $baseUrl/message/study-room/ai-tools/
  ///   body:     { "mode": "summary" | "quiz", "content": "<board text>" }
  ///   response (summary): { "summary": "..." }
  ///   response (quiz):    { "questions": [ { "question": "...", "options": ["..."], "answer": "..." }, ... ] }
  ///
  /// AI provider (Anthropic API waghera) ko backend se hi call karna —
  /// app ke andar koi AI key kabhi hardcode mat karna.
  static Future<Map<String, dynamic>> generate({
    required String mode,
    required String content,
  }) async {
    try {
      final res = await http.post(
        Uri.parse("$_baseUrl/message/study-room/ai-tools/"),
        headers: await _getHeaders(),
        body: jsonEncode({
          "mode": mode,
          "content": content,
        }),
      );

      final data = jsonDecode(res.body);
      if (res.statusCode == 200 || res.statusCode == 201) {
        return data as Map<String, dynamic>;
      } else {
        throw Exception(data['error'] ?? "Failed to generate $mode");
      }
    } catch (e) {
      throw Exception("AI $mode generation error: $e");
    }
  }
}