// message/services/message_api_service.dart
//
// Tere Django `urls.py` ke saare REST endpoints yahan cover hain.
// Pattern wahi hai jo tera `ProfileApi.ApiService` use karta hai:
// Api.baseUrl + AuthService.getToken() -> Bearer header.
//
// pubspec.yaml me ye dependency chahiye (agar already nahi hai):
//   http: ^1.2.0

import 'dart:convert';
import 'package:http/http.dart' as http;

import '../../utils/api.dart';
import '../../services/auth_service.dart';
import '../models/message_models.dart';

class MessageApiException implements Exception {
  final String message;
  final int? statusCode;
  MessageApiException(this.message, {this.statusCode});
  @override
  String toString() => message;
}

class MessageApiService {
  // Sab REST routes `message/` prefix ke andar hain (router.register).
  static String get _base => "${Api.baseUrl}/message";

  static Future<Map<String, String>> _headers({bool json = true}) async {
    final token = await AuthService.getToken();
    return {
      if (json) "Content-Type": "application/json",
      if (token != null && token.isNotEmpty) "Authorization": "Bearer $token",
    };
  }

  static dynamic _decode(http.Response res) {
    if (res.statusCode >= 200 && res.statusCode < 300) {
      if (res.body.isEmpty) return null;
      return jsonDecode(utf8.decode(res.bodyBytes));
    }
    String msg = "Request failed (${res.statusCode})";
    try {
      final body = jsonDecode(utf8.decode(res.bodyBytes));
      if (body is Map && body.isNotEmpty) {
        msg = body.values.first is List
            ? body.values.first.first.toString()
            : body.values.first.toString();
      }
    } catch (_) {}
    throw MessageApiException(msg, statusCode: res.statusCode);
  }

  // ==================================================================
  // CONVERSATIONS
  // ==================================================================

  /// GET /message/conversations/
  static Future<List<ConversationModel>> getConversations() async {
    final res = await http.get(Uri.parse("$_base/conversations/"),
        headers: await _headers());
    final data = _decode(res);
    final List list = data is Map && data.containsKey('results')
        ? data['results']
        : data as List;
    return list.map((e) => ConversationModel.fromJson(e)).toList();
  }

  /// GET /message/conversations/<id>/
  static Future<ConversationModel> getConversation(String id) async {
    final res = await http.get(Uri.parse("$_base/conversations/$id/"),
        headers: await _headers());
    return ConversationModel.fromJson(_decode(res));
  }

  /// POST /message/conversations/start_private/  {"user_id"}
  static Future<ConversationModel> startPrivateChat(String userId) async {
    final res = await http.post(
      Uri.parse("$_base/conversations/start_private/"),
      headers: await _headers(),
      body: jsonEncode({"user_id": userId}),
    );
    return ConversationModel.fromJson(_decode(res));
  }

  /// PATCH /message/conversations/<id>/settings/
  static Future<ConversationSettings> updateSettings(
    String conversationId, {
    bool? isMuted,
    bool? isArchived,
    bool? isPinned,
  }) async {
    final body = <String, dynamic>{};
    if (isMuted != null) body['is_muted'] = isMuted;
    if (isArchived != null) body['is_archived'] = isArchived;
    if (isPinned != null) body['is_pinned'] = isPinned;
    final res = await http.patch(
      Uri.parse("$_base/conversations/$conversationId/settings/"),
      headers: await _headers(),
      body: jsonEncode(body),
    );
    return ConversationSettings.fromJson(_decode(res));
  }

  /// GET /message/conversations/<id>/messages/?page=1  (paginated history)
  static Future<List<MessageModel>> getMessages(
    String conversationId, {
    int page = 1,
  }) async {
    final res = await http.get(
      Uri.parse("$_base/conversations/$conversationId/messages/?page=$page"),
      headers: await _headers(),
    );
    final data = _decode(res);
    final List list = data is Map && data.containsKey('results')
        ? data['results']
        : data as List;
    return list.map((e) => MessageModel.fromJson(e)).toList();
  }

  /// POST /message/conversations/<id>/messages/  (REST fallback send —
  /// normally realtime send WebSocket se hoti hai, ye sirf backup hai jab
  /// socket connect na ho paye)
  static Future<MessageModel> sendMessageRest(
    String conversationId, {
    required String type,
    String? text,
    String? fileUrl,
    List<String>? fileUrls,
    String? thumbnailUrl,
    Map<String, dynamic>? meta,
    String? replyTo,
    String? clientId,
  }) async {
    final res = await http.post(
      Uri.parse("$_base/conversations/$conversationId/messages/"),
      headers: await _headers(),
      body: jsonEncode({
        "type": type,
        if (text != null) "text": text,
        if (fileUrl != null) "file_url": fileUrl,
        if (fileUrls != null) "file_urls": fileUrls,
        if (thumbnailUrl != null) "thumbnail_url": thumbnailUrl,
        if (meta != null) "meta": meta,
        if (replyTo != null) "reply_to": replyTo,
        if (clientId != null) "client_id": clientId,
      }),
    );
    return MessageModel.fromJson(_decode(res));
  }

  /// POST /message/conversations/<id>/read_all/
  static Future<void> readAll(String conversationId) async {
    final res = await http.post(
      Uri.parse("$_base/conversations/$conversationId/read_all/"),
      headers: await _headers(),
    );
    _decode(res);
  }

  // ==================================================================
  // MESSAGES (detail actions)
  // ==================================================================

  /// PATCH /message/messages/<id>/  {"text"}  — sender hi edit kar sakta hai
  static Future<MessageModel> editMessage(String messageId, String text) async {
    final res = await http.patch(
      Uri.parse("$_base/messages/$messageId/"),
      headers: await _headers(),
      body: jsonEncode({"text": text}),
    );
    return MessageModel.fromJson(_decode(res));
  }

  /// DELETE /message/messages/<id>/?for_everyone=true|false
  static Future<void> deleteMessage(String messageId,
      {bool forEveryone = false}) async {
    final res = await http.delete(
      Uri.parse(
          "$_base/messages/$messageId/?for_everyone=${forEveryone ? 'true' : 'false'}"),
      headers: await _headers(),
    );
    _decode(res);
  }

  /// POST /message/messages/<id>/react/  {"emoji"}
  static Future<void> reactToMessage(String messageId, String emoji) async {
    final res = await http.post(
      Uri.parse("$_base/messages/$messageId/react/"),
      headers: await _headers(),
      body: jsonEncode({"emoji": emoji}),
    );
    _decode(res);
  }

  /// DELETE /message/messages/<id>/react/
  static Future<void> removeReaction(String messageId) async {
    final res = await http.delete(
      Uri.parse("$_base/messages/$messageId/react/"),
      headers: await _headers(),
    );
    _decode(res);
  }

  /// POST /message/messages/<id>/read/
  static Future<void> markRead(String messageId) async {
    final res = await http.post(
      Uri.parse("$_base/messages/$messageId/read/"),
      headers: await _headers(),
    );
    _decode(res);
  }

  // ==================================================================
  // GROUPS
  // ==================================================================

  /// POST /message/groups/  {"name","description","photo_url","is_private","member_ids"}
  static Future<Map<String, dynamic>> createGroup({
    required String name,
    String description = '',
    String? photoUrl,
    bool isPrivate = false,
    List<String> memberIds = const [],
  }) async {
    final res = await http.post(
      Uri.parse("$_base/groups/"),
      headers: await _headers(),
      body: jsonEncode({
        "name": name,
        "description": description,
        if (photoUrl != null) "photo_url": photoUrl,
        "is_private": isPrivate,
        "member_ids": memberIds,
      }),
    );
    return _decode(res);
  }

  /// GET /message/groups/<id>/
  static Future<Map<String, dynamic>> getGroup(String groupId) async {
    final res = await http.get(Uri.parse("$_base/groups/$groupId/"),
        headers: await _headers());
    return _decode(res);
  }

  /// PATCH /message/groups/<id>/  (admin/mod only)
  static Future<Map<String, dynamic>> updateGroup(
      String groupId, Map<String, dynamic> updates) async {
    final res = await http.patch(
      Uri.parse("$_base/groups/$groupId/"),
      headers: await _headers(),
      body: jsonEncode(updates),
    );
    return _decode(res);
  }

  /// POST /message/groups/<id>/members/  {"user_ids"}  (admin/mod only)
  static Future<void> addGroupMembers(
      String groupId, List<String> userIds) async {
    final res = await http.post(
      Uri.parse("$_base/groups/$groupId/members/"),
      headers: await _headers(),
      body: jsonEncode({"user_ids": userIds}),
    );
    _decode(res);
  }

  /// PATCH /message/groups/<id>/members/<user_id>/  (role/mute/ban)
  static Future<void> updateGroupMember(
    String groupId,
    String userId,
    Map<String, dynamic> updates,
  ) async {
    final res = await http.patch(
      Uri.parse("$_base/groups/$groupId/members/$userId/"),
      headers: await _headers(),
      body: jsonEncode(updates),
    );
    _decode(res);
  }

  /// DELETE /message/groups/<id>/members/<user_id>/  (remove / leave)
  static Future<void> removeGroupMember(String groupId, String userId) async {
    final res = await http.delete(
      Uri.parse("$_base/groups/$groupId/members/$userId/"),
      headers: await _headers(),
    );
    _decode(res);
  }

  /// GET /message/groups/<id>/media/
  static Future<List<dynamic>> getGroupMedia(String groupId) async {
    final res = await http.get(Uri.parse("$_base/groups/$groupId/media/"),
        headers: await _headers());
    final data = _decode(res);
    return data is Map && data.containsKey('results') ? data['results'] : data;
  }

  // ==================================================================
  // BLOCKED USERS
  // ==================================================================

  /// GET /message/blocked-users/
  static Future<List<dynamic>> getBlockedUsers() async {
    final res = await http.get(Uri.parse("$_base/blocked-users/"),
        headers: await _headers());
    final data = _decode(res);
    return data is Map && data.containsKey('results') ? data['results'] : data;
  }

  /// POST /message/blocked-users/  {"blocked": "<user_id>"}
  static Future<void> blockUser(String userId) async {
    final res = await http.post(
      Uri.parse("$_base/blocked-users/"),
      headers: await _headers(),
      body: jsonEncode({"blocked": userId}),
    );
    _decode(res);
  }

  /// DELETE /message/blocked-users/<id>/
  static Future<void> unblockUser(String blockedUserRecordId) async {
    final res = await http.delete(
      Uri.parse("$_base/blocked-users/$blockedUserRecordId/"),
      headers: await _headers(),
    );
    _decode(res);
  }

  // ==================================================================
  // PRESENCE
  // ==================================================================

  /// GET /message/users/<user_id>/presence/
  static Future<Map<String, dynamic>> getUserPresence(String userId) async {
    final res = await http.get(
      Uri.parse("$_base/users/$userId/presence/"),
      headers: await _headers(),
    );
    return _decode(res);
  }

  // ==================================================================
  // CALLS
  // ==================================================================

  /// GET /message/calls/
  static Future<List<dynamic>> getCallHistory() async {
    final res =
        await http.get(Uri.parse("$_base/calls/"), headers: await _headers());
    final data = _decode(res);
    return data is Map && data.containsKey('results') ? data['results'] : data;
  }

  /// GET /message/calls/<id>/
  static Future<Map<String, dynamic>> getCallDetail(String callId) async {
    final res = await http.get(Uri.parse("$_base/calls/$callId/"),
        headers: await _headers());
    return _decode(res);
  }
}