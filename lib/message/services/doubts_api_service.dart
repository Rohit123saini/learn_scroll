// message/services/doubts_api_service.dart
//
// 🔥 NAYA — REST client for the "Doubt Queue" feature
// (backend: `DoubtQuestionViewSet`, urls.py `/message/groups/<group_id>/
// doubts/...`).
//
// NOTE: `message_api_service.dart` (jahan `updateGroup`/`getGroupMedia`
// jaisi baaki saari calls hain) upload nahi hui thi is review me, isliye
// maine ye ek chhota standalone service banaya hai jo `chat_socket_service.
// dart` jaisa hi `Api.baseUrl` + `AuthService.getValidToken()` pattern use
// karta hai. Agar tumhare `MessageApiService` ka apna alag http/dio client
// hai to in methods ko seedha usi class me copy-paste kar dena — endpoints
// aur shapes bilkul yahi rahenge, sirf HTTP-call ka boilerplate badlega.

import 'dart:convert';
import 'package:http/http.dart' as http;

import '../../utils/api.dart';
import '../../services/auth_service.dart';
import '../models/message_models.dart';

class DoubtsApiService {
  static Future<Map<String, String>> _headers() async {
    final token = await AuthService.getValidToken();
    return {
      'Content-Type': 'application/json',
      if (token != null) 'Authorization': 'Bearer $token',
    };
  }

  static Uri _u(String path) => Uri.parse('${Api.baseUrl}$path');

  static Never _throwOnError(http.Response res) {
    Map<String, dynamic>? body;
    try {
      body = jsonDecode(res.body) as Map<String, dynamic>;
    } catch (_) {}
    throw Exception(body?['detail']?.toString() ?? 'Request failed (${res.statusCode})');
  }

  /// Group detail (`GroupSerializer`) — DoubtsScreen isse `allow_anonymous_
  /// doubts` aur (members list se) "kya main teacher/admin/mod hoon" nikalta
  /// hai, taaki UI (Ask Anonymously checkbox, Answer/Reveal buttons) sahi
  /// dikhe. Existing `GET /message/groups/<id>/` (router-generated retrieve)
  /// reuse karta hai — koi naya endpoint nahi.
  static Future<Map<String, dynamic>> getGroup(String groupId) async {
    final res = await http.get(_u('/message/groups/$groupId/'), headers: await _headers());
    if (res.statusCode != 200) _throwOnError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  /// `status`: null (sab), 'answered', ya 'unanswered'.
  /// Backend already `-upvotes_count, -created_at` order me deta hai
  /// (sabse-upvoted, phir sabse-naya) — client ko dobara sort nahi karna.
  static Future<({List<DoubtQuestionModel> doubts, String? nextPage})> getDoubts(
    String groupId, {
    String? status,
    String? pageUrl,
  }) async {
    final uri = pageUrl != null
        ? Uri.parse(pageUrl)
        : _u('/message/groups/$groupId/doubts/${status != null ? '?status=$status' : ''}');
    final res = await http.get(uri, headers: await _headers());
    if (res.statusCode != 200) _throwOnError(res);
    final data = jsonDecode(res.body) as Map<String, dynamic>;
    final results = (data['results'] as List? ?? [])
        .map((e) => DoubtQuestionModel.fromJson(e as Map<String, dynamic>))
        .toList();
    return (doubts: results, nextPage: data['next']?.toString());
  }

  static Future<DoubtQuestionModel> createDoubt(
    String groupId, {
    required String text,
    bool isAnonymous = false,
  }) async {
    final res = await http.post(
      _u('/message/groups/$groupId/doubts/'),
      headers: await _headers(),
      body: jsonEncode({'text': text, 'is_anonymous': isAnonymous}),
    );
    if (res.statusCode != 201) _throwOnError(res);
    return DoubtQuestionModel.fromJson(jsonDecode(res.body));
  }

  static Future<DoubtQuestionModel> upvote(String groupId, String doubtId) async {
    final res = await http.post(
      _u('/message/groups/$groupId/doubts/$doubtId/upvote/'),
      headers: await _headers(),
    );
    if (res.statusCode != 200) _throwOnError(res);
    return DoubtQuestionModel.fromJson(jsonDecode(res.body));
  }

  static Future<DoubtQuestionModel> removeUpvote(String groupId, String doubtId) async {
    final res = await http.delete(
      _u('/message/groups/$groupId/doubts/$doubtId/upvote/'),
      headers: await _headers(),
    );
    if (res.statusCode != 200) _throwOnError(res);
    return DoubtQuestionModel.fromJson(jsonDecode(res.body));
  }

  /// Teacher (admin/moderator) only — backend 403 dega baaki sabke liye.
  static Future<DoubtQuestionModel> answer(
    String groupId, String doubtId, String answerText,
  ) async {
    final res = await http.post(
      _u('/message/groups/$groupId/doubts/$doubtId/answer/'),
      headers: await _headers(),
      body: jsonEncode({'answer_text': answerText}),
    );
    if (res.statusCode != 200) _throwOnError(res);
    return DoubtQuestionModel.fromJson(jsonDecode(res.body));
  }

  /// Teacher (admin/moderator) only — "reveal who asked this anonymous doubt".
  static Future<DoubtQuestionModel> reveal(String groupId, String doubtId) async {
    final res = await http.post(
      _u('/message/groups/$groupId/doubts/$doubtId/reveal/'),
      headers: await _headers(),
    );
    if (res.statusCode != 200) _throwOnError(res);
    return DoubtQuestionModel.fromJson(jsonDecode(res.body));
  }
}