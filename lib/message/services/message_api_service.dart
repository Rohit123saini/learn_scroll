// message/services/message_api_service.dart
//
// Tere Django `urls.py` ke saare REST endpoints yahan cover hain.
// Pattern wahi hai jo tera `ProfileApi.ApiService` use karta hai:
// Api.baseUrl + AuthService.getToken() -> Bearer header.
//
// pubspec.yaml me ye dependencies chahiye:
//   http: ^1.2.0

import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:dio/dio.dart'; // 🔥 NAYA — real upload progress ke liye

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

/// Upload ka result — attachment message bhejne ke liye zaroori sab kuch.
class UploadedFileResult {
  final String fileUrl;
  final String fileType; // image/video/audio/presentation/file
  final int fileSize;
  final String fileName;
  final String mimeType;

  UploadedFileResult({
    required this.fileUrl,
    required this.fileType,
    required this.fileSize,
    required this.fileName,
    required this.mimeType,
  });

  factory UploadedFileResult.fromJson(Map<String, dynamic> json) {
    return UploadedFileResult(
      fileUrl: json['file_url']?.toString() ?? '',
      fileType: json['file_type']?.toString() ?? 'file',
      fileSize: json['file_size'] is int
          ? json['file_size']
          : int.tryParse(json['file_size']?.toString() ?? '') ?? 0,
      fileName: json['file_name']?.toString() ?? '',
      mimeType: json['mime_type']?.toString() ?? '',
    );
  }
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
  // CHAT MEDIA UPLOAD
  // ==================================================================

  /// POST /message/upload/  (multipart "file")
  ///
  /// File pehle yahan upload karo, jo `file_url` milta hai wahi phir
  /// `sendMessageRest(...)` me pass karo taaki image/video/audio/file
  /// message ban jaaye. `onProgress` upload % dikhane ke liye use karo
  /// (0.0 - 1.0).
  ///
  /// 🔥 FIX: pehle ye `http.MultipartRequest` use karta tha, jiske paas
  /// upload-progress report karne ka koi tarika hi nahi hai — isliye
  /// `onProgress` parameter accept toh hota tha lekin KABHI call nahi
  /// hota tha (dead code), aur UI me "kitna upload hua" kabhi dikhta hi
  /// nahi tha. Ab `Dio` ke `onSendProgress` se real byte-level progress
  /// milta hai.
  static Future<UploadedFileResult> uploadFile(
    File file, {
    void Function(double progress)? onProgress,
  }) async {
    final token = await AuthService.getToken();
    final dio = Dio();

    final formData = FormData.fromMap({
      'file': await MultipartFile.fromFile(file.path, filename: file.path.split('/').last),
    });

    final response = await dio.post(
      "$_base/upload/",
      data: formData,
      options: Options(headers: {
        if (token != null && token.isNotEmpty) "Authorization": "Bearer $token",
      }),
      onSendProgress: (sent, total) {
        if (total > 0) onProgress?.call(sent / total);
      },
    );

    if (response.statusCode == null || response.statusCode! < 200 || response.statusCode! >= 300) {
      throw MessageApiException("Upload failed (${response.statusCode})", statusCode: response.statusCode);
    }
    final data = response.data is String ? jsonDecode(response.data) : response.data;
    return UploadedFileResult.fromJson(data);
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

  /// POST /message/conversations/<id>/messages/
  ///
  /// Text message REST fallback ke liye (jab socket down ho) AUR har media
  /// message (image/video/audio/file/presentation/location) yahi se jaata
  /// hai, kyunki websocket ka `message` event sirf plain text handle karta
  /// hai. Backend patch (PATCH_views_realtime_broadcast.md) ke baad ye
  /// dusre participant tak bhi realtime deliver ho jaata hai.
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

  // ==================================================================
  // STUDY ROOM — ADD PARTICIPANT
  // ==================================================================

  /// POST /message/conversations/<id>/participants/  {"user_id"}
  ///
  /// 🔥 NAYA — Study Room me "Add User" button ke liye. Abhi tumhare
  /// Django backend me ye endpoint maujood NAHI hai — pehle wahan bhi
  /// banana hoga. Idea: private (1-to-1) conversation me jab teesra
  /// participant add ho, backend usko internally group me convert kar de
  /// (ya seedha ek `participants` M2M list me user add kar de) aur naye
  /// user ko usi conversation ke WebSocket group me bhi join kara de,
  /// taaki wo study room ke realtime events (draw_point, update_window,
  /// waghera) turant receive karne lage.
  static Future<void> addParticipantToConversation(
    String conversationId,
    String userId,
  ) async {
    final res = await http.post(
      Uri.parse("$_base/conversations/$conversationId/participants/"),
      headers: await _headers(),
      body: jsonEncode({"user_id": userId}),
    );
    _decode(res);
  }

  // ==================================================================
  // STUDY ROOM — WHITEBOARD AUTO-SAVE
  // ==================================================================

  /// PUT /message/conversations/<id>/study-room-state/  {"pages": [...]}
  ///
  /// 🔥 NAYA — poora whiteboard (saari pages: strokes/shapes/text/sticky
  /// notes) periodically yahan save hota hai taaki app band karke wapas
  /// aane par board wahi se dikhe jahan chhoda tha. Backend me abhi ye
  /// endpoint maujood NAHI hai — banana hoga (ek simple JSONField wala
  /// model jo conversation se linked ho, poora board JSON store kare).
  static Future<void> saveStudyRoomState(
    String conversationId,
    Map<String, dynamic> state,
  ) async {
    final res = await http.put(
      Uri.parse("$_base/conversations/$conversationId/study-room-state/"),
      headers: await _headers(),
      body: jsonEncode(state),
    );
    _decode(res);
  }

  /// GET /message/conversations/<id>/study-room-state/
  ///
  /// 🔥 NAYA — study room khulte hi last-saved board wapas load karne ke
  /// liye. Endpoint na milne (404) ya koi bhi error case me caller
  /// (StudyRoomScreen) gracefully khali board se hi shuru kar deta hai.
  static Future<Map<String, dynamic>?> getStudyRoomState(String conversationId) async {
    final res = await http.get(
      Uri.parse("$_base/conversations/$conversationId/study-room-state/"),
      headers: await _headers(),
    );
    final data = _decode(res);
    return data is Map<String, dynamic> ? data : null;
  }

  static Future<ConversationModel> getOrCreateConversation(
      String targetUserId) async {
    final token = await AuthService.getToken();
    final url = Uri.parse("${Api.baseUrl}/message/conversations/start_private/");

    final response = await http.post(
      url,
      headers: {
        "Authorization": "Bearer $token",
        "Content-Type": "application/json",
      },
      body: jsonEncode({"user_id": targetUserId}),
    );

    if (response.statusCode == 200 || response.statusCode == 201) {
      return ConversationModel.fromJson(jsonDecode(response.body));
    } else {
      throw Exception('Failed to start conversation: ${response.body}');
    }
  }
}