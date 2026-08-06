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

  // 🔥 NAYA — Block/Unblock ka model (`BlockUser`) profile app me hai,
  // isliye uska API bhi `message/` se nahi, `profile/` se hit hota hai.
  static String get _profileBase => "${Api.baseUrl}/profile";

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

  /// PATCH /message/conversations/<id>/label/  {"label"}
  ///
  /// 🔥 NAYA — Is chat ko apna custom nickname/label dena (sirf tumhare
  /// account ke liye — dusre participant/group members ko nahi dikhega).
  /// Empty string bhejo to label clear ho jaayega aur wapas default naam
  /// (participant/group ka naam) dikhne lagega. Success pe backend jo
  /// (trimmed/cleared) label save hua wahi wapas deta hai.
  static Future<String?> updateConversationLabel(
    String conversationId,
    String label,
  ) async {
    final res = await http.patch(
      Uri.parse("$_base/conversations/$conversationId/label/"),
      headers: await _headers(),
      body: jsonEncode({"label": label}),
    );
    final data = _decode(res);
    final value = data is Map ? data['label'] : null;
    return value is String && value.isNotEmpty ? value : null;
  }

  /// GET /message/conversations/<id>/ -> `disappearing_messages_duration`
  /// nikal ke deta hai. ('none' | '1_month' | '6_months' | '1_year')
  ///
  /// 🔥 NAYA — Temporary chat: chat screen khulte hi current setting
  /// jaanne ke liye (menu me sahi label dikhane ke liye). Raw JSON se
  /// seedha padha hai (isConversationMuted jaisa hi pattern) taaki
  /// `ConversationModel` me field ka naam/wajood kuch bhi ho, isse koi
  /// farak na pade — fail ho jaaye to chup-chaap 'none' maan lo.
  static Future<String> getDisappearingDuration(String conversationId) async {
    try {
      final res = await http.get(
        Uri.parse("$_base/conversations/$conversationId/"),
        headers: await _headers(),
      );
      final data = _decode(res);
      final value = data is Map ? data['disappearing_messages_duration'] : null;
      return value is String && value.isNotEmpty ? value : 'none';
    } catch (_) {
      return 'none';
    }
  }

  /// PATCH /message/conversations/<id>/disappearing_messages/  {"duration"}
  ///
  /// 🔥 NAYA — Temporary chat (disappearing messages) on/off ya duration
  /// change karne ke liye. `duration` in me se ek hona chahiye:
  /// 'none' | '1_month' | '6_months' | '1_year'. Group chat me backend
  /// sirf admin/moderator ko allow karta hai — non-admin call kare to
  /// 403 aayega, jo caller ko catch karke user ko dikhana hoga.
  static Future<void> setDisappearingMessages(
    String conversationId,
    String duration,
  ) async {
    final res = await http.patch(
      Uri.parse("$_base/conversations/$conversationId/disappearing_messages/"),
      headers: await _headers(),
      body: jsonEncode({"duration": duration}),
    );
    _decode(res); // non-2xx pe _decode khud exception throw karta hai
  }

  /// POST /message/conversations/bulk_delete/  {"conversation_ids": [...]}
  ///
  /// 🔥 NAYA — chat list se ek ya multiple chats select karke delete
  /// karne ke liye. Ye sirf tumhare (current user ke) liye chat hide
  /// karta hai — dusre participant/group members ki chat waisi hi rehti
  /// hai. Screen se select mode ke saare selected `conversation.id`
  /// yahan bhejo, delete hone ke baad wo IDs local list se bhi remove
  /// kar dena.
  static Future<int> bulkDeleteConversations(
      List<String> conversationIds) async {
    final res = await http.post(
      Uri.parse("$_base/conversations/bulk_delete/"),
      headers: await _headers(),
      body: jsonEncode({"conversation_ids": conversationIds}),
    );
    final data = _decode(res);
    final count = data is Map ? data['deleted_count'] : null;
    return count is int ? count : conversationIds.length;
  }

  /// GET /message/conversations/<id>/  -> `my_settings.is_muted` nikal
  /// ke deta hai.
  ///
  /// 🔥 NAYA — chat screen khulte hi 3-dot menu me "Mute"/"Unmute" ka
  /// sahi label dikhane ke liye current mute status jaanna zaroori hai.
  /// Raw JSON se seedha padha isliye taaki `ConversationModel` me field
  /// ka naam kuch bhi ho, isse koi farak na pade.
  static Future<bool> isConversationMuted(String conversationId) async {
    final res = await http.get(
      Uri.parse("$_base/conversations/$conversationId/"),
      headers: await _headers(),
    );
    final data = _decode(res);
    final settings = data is Map ? data['my_settings'] : null;
    return settings is Map && settings['is_muted'] == true;
  }

  /// GET /message/conversations/<id>/messages/?page=1&page_size=20  (paginated history)
  ///
  /// 🔥 NAYA — `pageSize` optional param add kiya (backend
  /// `MessagePagination.page_size_query_param = 'page_size'` already
  /// support karta hai) taaki chat screen initial load par sirf
  /// 10-20 messages mangwa sake, baaki top pe scroll karne par
  /// (older pages) load ho.
  static Future<List<MessageModel>> getMessages(
    String conversationId, {
    int page = 1,
    int? pageSize,
  }) async {
    final query = {
      'page': '$page',
      if (pageSize != null) 'page_size': '$pageSize',
    };
    final uri = Uri.parse("$_base/conversations/$conversationId/messages/")
        .replace(queryParameters: query);
    final res = await http.get(uri, headers: await _headers());
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

  /// POST /message/messages/forward/
  /// body: {"message_ids": [...], "conversation_ids": [...]}
  ///
  /// Forwards one or many messages (any type: text/image/video/audio/
  /// file/location) into one or many target chats in a single call.
  /// Each forwarded copy becomes a brand new message (sender = you,
  /// isForwarded = true) in every target conversation — it's a copy,
  /// not a reference, so editing/deleting the original later doesn't
  /// touch the forwarded copies. Real-time delivery to the target
  /// chats happens over the socket on the backend side; this call just
  /// confirms success/failure.
  static Future<void> forwardMessages({
    required List<String> messageIds,
    required List<String> conversationIds,
  }) async {
    final res = await http.post(
      Uri.parse("$_base/messages/forward/"),
      headers: await _headers(),
      body: jsonEncode({
        "message_ids": messageIds,
        "conversation_ids": conversationIds,
      }),
    );
    _decode(res);
  }

  // ==================================================================
  // USER SEARCH — group members select karne ke liye
  // ==================================================================

  /// GET /profile/chat-search/?search=<query>
  ///
  /// 🔥 NAYA — "Add members" step (naya group banate waqt) ke liye user
  /// search. Ye endpoint `message` app me NAHI, `user_profile` app me hai
  /// isliye `Api.baseUrl` ke saath seedha "/profile/..." use kiya hai, na
  /// ki `$_base` (jo "/message" prefix laga deta). Sirf unhi logo ko
  /// dhoondta hai jinko tumne follow kiya hai ya jinhone tumhe follow
  /// kiya hai — response me id/username/first_name/last_name/mutual_friends.
  static Future<List<Map<String, dynamic>>> searchUsers(String query) async {
    if (query.trim().isEmpty) return [];
    final res = await http.get(
      Uri.parse("${Api.baseUrl}/profile/chat-search/?search=${Uri.encodeComponent(query.trim())}"),
      headers: await _headers(),
    );
    final decoded = _decode(res);
    final data = decoded is Map ? decoded['data'] : decoded;
    if (data is List) return data.cast<Map<String, dynamic>>();
    return [];
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

  /// DELETE /message/groups/<id>/  (ADMIN ONLY)
  ///
  /// 🔥 NAYA — poora group permanently delete karta hai (saare messages,
  /// media, members sab backend pe CASCADE se saath hi delete ho jaate
  /// hain). Backend 403 dega agar caller group ka admin nahi hai (yahan
  /// koi client-side role check nahi hai, wo caller — UI — ki
  /// zimmedari hai ki button hi admin ko dikhaye).
  static Future<void> deleteGroup(String groupId) async {
    final res = await http.delete(
      Uri.parse("$_base/groups/$groupId/"),
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

  // ------------------------------------------------------------------
  // INVITE LINK / JOIN-REQUESTS
  //
  // Backend (`GroupViewSet` — views.py) ka rule: PUBLIC group me
  // `join()` turant member bana deta hai (201 "joined"). PRIVATE group
  // me wahi call ek `GroupJoinRequest` PENDING bana deta hai (202
  // "pending") — us request ko group ka admin/moderator baad me
  // `approveJoinRequest` / `rejectJoinRequest` se handle karta hai.
  // ------------------------------------------------------------------

  /// POST /message/groups/join/  {"invite_code"}
  ///
  /// Invite-link/code se group join karna. Return value me hamesha
  /// `status` key hoga: 'joined' (public — turant member) ya 'pending'
  /// (private — request bhej di, admin approve karega). Already-member
  /// ya invalid/banned case backend se 400/403/404 ke saath aata hai,
  /// jo `_decode()` khud `MessageApiException` throw kar dega.
  static Future<Map<String, dynamic>> joinGroupByInviteCode(String inviteCode) async {
    final res = await http.post(
      Uri.parse("$_base/groups/join/"),
      headers: await _headers(),
      body: jsonEncode({"invite_code": inviteCode}),
    );
    return _decode(res);
  }

  /// GET /message/groups/<id>/join-requests/  (admin/moderator only)
  ///
  /// Private group ki pending join-requests ki list — har entry me
  /// requester ka user info + request `id` hota hai, jo neeche
  /// approve/reject me chahiye.
  static Future<List<dynamic>> getJoinRequests(String groupId) async {
    final res = await http.get(
      Uri.parse("$_base/groups/$groupId/join-requests/"),
      headers: await _headers(),
    );
    final data = _decode(res);
    return data is List ? data : (data is Map && data['results'] is List ? data['results'] : []);
  }

  /// POST /message/groups/<id>/join-requests/<request_id>/approve/
  /// (admin/moderator only) — requester turant `GroupMember` ban jaata
  /// hai, `addGroupMembers` jaisa hi effect.
  static Future<Map<String, dynamic>> approveJoinRequest(String groupId, String requestId) async {
    final res = await http.post(
      Uri.parse("$_base/groups/$groupId/join-requests/$requestId/approve/"),
      headers: await _headers(),
    );
    return _decode(res);
  }

  /// POST /message/groups/<id>/join-requests/<request_id>/reject/
  /// (admin/moderator only)
  static Future<void> rejectJoinRequest(String groupId, String requestId) async {
    final res = await http.post(
      Uri.parse("$_base/groups/$groupId/join-requests/$requestId/reject/"),
      headers: await _headers(),
    );
    _decode(res);
  }

  // ==================================================================
  // BLOCKED USERS
  // ==================================================================

  /// GET /profile/blocked-users/
  /// 🔥 NAYA — profile app se hit hota hai (model wahi hai), message app se nahi.
  static Future<List<dynamic>> getBlockedUsers() async {
    final res = await http.get(Uri.parse("$_profileBase/blocked-users/"),
        headers: await _headers());
    final data = _decode(res);
    return data is Map && data.containsKey('data') ? data['data'] : data;
  }

  /// POST /profile/blocked-users/  {"blocked": "<user_id>"}
  static Future<void> blockUser(String userId) async {
    final res = await http.post(
      Uri.parse("$_profileBase/blocked-users/"),
      headers: await _headers(),
      body: jsonEncode({"blocked": userId}),
    );
    _decode(res);
  }

  /// DELETE /profile/blocked-users/<id>/
  ///
  /// Backend `<id>` ko do tarike se accept karta hai — ya to `BlockUser`
  /// record ki apni id, ya seedha target USER ki id. Isliye yahan seedha
  /// `userId` pass karna hi kaafi hai, alag se record-id track karne ki
  /// zaroorat nahi.
  static Future<void> unblockUser(String userId) async {
    final res = await http.delete(
      Uri.parse("$_profileBase/blocked-users/$userId/"),
      headers: await _headers(),
    );
    _decode(res);
  }

  /// GET /profile/blocked-users/  -> current user ne diye gaye `userId`
  /// ko block kiya hua hai ya nahi, ye check karta hai.
  ///
  /// 🔥 NAYA — chat screen khulte hi AppBar ke 3-dot menu me "Block"
  /// ya "Unblock" ka sahi label dikhane ke liye. Poori block-list fetch
  /// karke local search karta hai (list generally chhoti hoti hai);
  /// koi bhi error/network-fail case me chup-chaap `false` (not blocked)
  /// maan leta hai, taaki chat screen crash na ho.
  static Future<bool> isUserBlocked(String userId) async {
    try {
      final list = await getBlockedUsers();
      for (final item in list) {
        if (item is Map) {
          final blockedId = (item['blocked'] ?? item['blocked_detail']?['id'])?.toString();
          if (blockedId == userId) return true;
        }
      }
      return false;
    } catch (_) {
      return false;
    }
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