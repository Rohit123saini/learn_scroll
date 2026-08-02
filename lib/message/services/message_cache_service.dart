// message/services/message_cache_service.dart
//
// Local cache — do cheezein cache hoti hain:
//   1) Conversations list (jo ConversationsScreen dikhati hai) — sirf
//      latest 30 conversations save hote hain.
//   2) Har conversation ke messages — jitne last `getMessages(page: 1)`
//      se aaye the (thoda thoda, poori history nahi), 1 week tak valid.
//
// Storage `shared_preferences` se ho raha hai (AuthService jaisa hi
// pattern — value hamesha JSON-encoded string ke form me save hoti hai
// kyunki SharedPreferences sirf primitive types leta hai).
//
// Kaam kaise hota hai (dono screens me): pehle cache se turant dikhao
// (instant open feel, especially slow network pe), phir background me
// fresh network data aate hi list/messages overwrite ho jaate hain aur
// naya cache save ho jaata hai. Agar network fail ho jaaye aur cache
// already dikh rahi ho, to error dikhane ki zaroorat nahi.
//
// Logout pe alag se kuch clear karne ki zaroorat nahi — AuthService.logout()
// `prefs.clear()` karta hai jo saari SharedPreferences (isliye ye cache
// bhi) apne aap saaf kar deta hai.

import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/message_models.dart';

class MessageCacheService {
  // ---------------- Conversations list ----------------
  static const _conversationsKey = 'msg_cache_conversations';
  static const _conversationsSavedAtKey = 'msg_cache_conversations_saved_at';
  static const int maxCachedConversations = 30;

  // ---------------- Per-conversation messages ----------------
  static const _messagesKeyPrefix = 'msg_cache_messages_';
  static const _messagesSavedAtPrefix = 'msg_cache_messages_saved_at_';
  static const int maxCachedMessagesPerConversation = 50;
  static const Duration messagesCacheTtl = Duration(days: 7);

  // ======================================================================
  // CONVERSATIONS
  // ======================================================================

  /// Fresh conversations aane par cache overwrite karo — sirf latest 30
  /// save karte hain. Jo conversations is baar list se bahar ho gayi
  /// (30 se zyada purani), unke cached messages bhi saaf kar dete hain
  /// taaki SharedPreferences me bekar data jama na ho.
  static Future<void> saveConversations(
      List<ConversationModel> conversations) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final previousIds =
          (await getCachedConversations()).map((c) => c.id).toSet();

      final trimmed = conversations.take(maxCachedConversations).toList();
      final newIds = trimmed.map((c) => c.id).toSet();

      final droppedIds = previousIds.difference(newIds);
      if (droppedIds.isNotEmpty) {
        await clearMessagesForConversations(droppedIds);
      }

      final jsonList = trimmed.map((c) => c.toJson()).toList();
      await prefs.setString(_conversationsKey, jsonEncode(jsonList));
      await prefs.setInt(
          _conversationsSavedAtKey, DateTime.now().millisecondsSinceEpoch);
    } catch (_) {
      // Cache likhna fail ho to bhi app normally chalta rahe — cache sirf
      // ek convenience layer hai, source of truth hamesha server hi hai.
    }
  }

  /// Koi bhi expiry check nahi — conversations list backend se hamesha
  /// refresh hoti rehti hai, cache sirf "list turant dikhane" ke liye hai.
  static Future<List<ConversationModel>> getCachedConversations() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_conversationsKey);
      if (raw == null || raw.isEmpty) return [];
      final List list = jsonDecode(raw);
      return list
          .map((e) => ConversationModel.fromJson(e as Map<String, dynamic>))
          .toList();
    } catch (_) {
      return [];
    }
  }

  // ======================================================================
  // MESSAGES (per conversation)
  // ======================================================================

  /// `messages` list ko as-is (server order — latest pehle) save karta hai.
  /// Poori history nahi, sirf latest [maxCachedMessagesPerConversation]
  /// (jitna ek page me aata hai) — isliye storage halka rehta hai.
  static Future<void> saveMessages(
      String conversationId, List<MessageModel> messages) async {
    if (conversationId.isEmpty) return;
    try {
      final prefs = await SharedPreferences.getInstance();
      final trimmed = messages.length > maxCachedMessagesPerConversation
          ? messages.sublist(0, maxCachedMessagesPerConversation)
          : messages;
      final jsonList = trimmed.map((m) => m.toJson()).toList();
      await prefs.setString(
          '$_messagesKeyPrefix$conversationId', jsonEncode(jsonList));
      await prefs.setInt('$_messagesSavedAtPrefix$conversationId',
          DateTime.now().millisecondsSinceEpoch);
    } catch (_) {}
  }

  /// 1 week se purana cache mile to khud expire karke khaali list de deta
  /// hai — purana/stale chat data kabhi UI me nahi dikhna chahiye.
  static Future<List<MessageModel>> getCachedMessages(
      String conversationId) async {
    if (conversationId.isEmpty) return [];
    try {
      final prefs = await SharedPreferences.getInstance();
      final savedAtMs = prefs.getInt('$_messagesSavedAtPrefix$conversationId');
      if (savedAtMs == null) return [];

      final savedAt = DateTime.fromMillisecondsSinceEpoch(savedAtMs);
      if (DateTime.now().difference(savedAt) > messagesCacheTtl) {
        await _clearMessages(prefs, conversationId);
        return [];
      }

      final raw = prefs.getString('$_messagesKeyPrefix$conversationId');
      if (raw == null || raw.isEmpty) return [];
      final List list = jsonDecode(raw);
      return list
          .map((e) => MessageModel.fromJson(e as Map<String, dynamic>))
          .toList();
    } catch (_) {
      return [];
    }
  }

  static Future<void> _clearMessages(
      SharedPreferences prefs, String conversationId) async {
    await prefs.remove('$_messagesKeyPrefix$conversationId');
    await prefs.remove('$_messagesSavedAtPrefix$conversationId');
  }

  static Future<void> clearMessagesForConversations(
      Iterable<String> conversationIds) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      for (final id in conversationIds) {
        await _clearMessages(prefs, id);
      }
    } catch (_) {}
  }

  /// Debug/settings screen se "Clear cache" jaisa button chahiye ho to.
  static Future<void> clearAll() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final keys = prefs.getKeys().where((k) =>
          k == _conversationsKey ||
          k == _conversationsSavedAtKey ||
          k.startsWith(_messagesKeyPrefix) ||
          k.startsWith(_messagesSavedAtPrefix));
      for (final k in keys.toList()) {
        await prefs.remove(k);
      }
    } catch (_) {}
  }
}