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

/// M6-FE — ek outgoing message jo abhi server se confirm nahi hua (outbox).
/// Har send (text / media / location / study-room) pehle yahan persist hota
/// hai, success par hata diya jaata hai. Isse app band/kill hone ya network
/// jaane par bhi message bachta hai aur reopen par auto-retry ho sakta hai.
class PendingMessage {
  final String clientId;
  final String conversationId;
  final String type; // MessageType.* string
  final String? text;
  final String? replyTo;
  // Optimistic bubble + send dono ke liye base meta (location: lat/lng,
  // file: file_name + extra, multi-image: count).
  final Map<String, dynamic>? meta;
  // Jo local files upload karni hain (1 = single file, 2+ = multi-image).
  final List<String> localPaths;
  // Upload ho chuki files: localPath -> {file_url, file_name, size, mime_type}.
  // Send fail hone par Resend me dobara upload nahi hota.
  final Map<String, dynamic> uploaded;
  final DateTime createdAt;
  final int attempts;
  // true = 4xx jaisa error jo khud-ba-khud retry se theek nahi hoga
  // (group blocked, file missing...). Auto-retry nahi hota, sirf manual Resend.
  final bool permanentFailure;

  const PendingMessage({
    required this.clientId,
    required this.conversationId,
    required this.type,
    this.text,
    this.replyTo,
    this.meta,
    this.localPaths = const [],
    this.uploaded = const {},
    required this.createdAt,
    this.attempts = 0,
    this.permanentFailure = false,
  });

  static const int maxAutoAttempts = 5;
  static const Duration maxAutoRetryAge = Duration(hours: 24);

  /// Reopen par auto-retry sirf tab: transient failure, 5 se kam attempts,
  /// aur 24h se purana nahi (purana message achanak bhejna confusing hota hai).
  bool get canAutoRetry =>
      !permanentFailure &&
      attempts < maxAutoAttempts &&
      DateTime.now().difference(createdAt) < maxAutoRetryAge;

  PendingMessage copyWith({
    Map<String, dynamic>? uploaded,
    int? attempts,
    bool? permanentFailure,
  }) =>
      PendingMessage(
        clientId: clientId,
        conversationId: conversationId,
        type: type,
        text: text,
        replyTo: replyTo,
        meta: meta,
        localPaths: localPaths,
        uploaded: uploaded ?? this.uploaded,
        createdAt: createdAt,
        attempts: attempts ?? this.attempts,
        permanentFailure: permanentFailure ?? this.permanentFailure,
      );

  Map<String, dynamic> toJson() => {
        'client_id': clientId,
        'conversation_id': conversationId,
        'type': type,
        if (text != null) 'text': text,
        if (replyTo != null) 'reply_to': replyTo,
        if (meta != null) 'meta': meta,
        'local_paths': localPaths,
        'uploaded': uploaded,
        'created_at': createdAt.millisecondsSinceEpoch,
        'attempts': attempts,
        'permanent_failure': permanentFailure,
      };

  factory PendingMessage.fromJson(Map<String, dynamic> j) => PendingMessage(
        clientId: j['client_id'].toString(),
        conversationId: j['conversation_id'].toString(),
        type: j['type'].toString(),
        text: j['text']?.toString(),
        replyTo: j['reply_to']?.toString(),
        meta: j['meta'] is Map ? Map<String, dynamic>.from(j['meta'] as Map) : null,
        localPaths: (j['local_paths'] as List? ?? const []).map((e) => e.toString()).toList(),
        uploaded: j['uploaded'] is Map ? Map<String, dynamic>.from(j['uploaded'] as Map) : <String, dynamic>{},
        createdAt: DateTime.fromMillisecondsSinceEpoch((j['created_at'] as num?)?.toInt() ?? 0),
        attempts: (j['attempts'] as num?)?.toInt() ?? 0,
        permanentFailure: j['permanent_failure'] == true,
      );
}

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


  // ======================================================================
  // 🔥 NAYA (M6-FE) — OUTBOX (unsent / failed outgoing messages)
  // ======================================================================
  //
  // Alag key me rakha hai: messages cache TTL (7 din) aur `clearAll()`
  // se unsent messages ka koi lena-dena nahi — "Clear cache" se user ke
  // na-bheje message gayab nahi hone chahiye. Logout pe
  // `AuthService.logout()` ka `prefs.clear()` ise bhi saaf kar deta hai
  // (dusre account me purane user ke message nahi jaane chahiye).

  static const _outboxKey = 'msg_outbox';
  static const int maxOutbox = 100;

  // SharedPreferences read-modify-write race se bachne ke liye saare outbox
  // operations ek line me chalte hain.
  static Future<void> _outboxTail = Future.value();
  static Future<T> _serial<T>(Future<T> Function() action) {
    final run = _outboxTail.then((_) => action());
    _outboxTail = run.then((_) {}, onError: (_) {});
    return run;
  }

  static List<PendingMessage> _readOutbox(SharedPreferences prefs) {
    final raw = prefs.getString(_outboxKey);
    if (raw == null || raw.isEmpty) return [];
    final out = <PendingMessage>[];
    try {
      for (final e in (jsonDecode(raw) as List)) {
        try {
          out.add(PendingMessage.fromJson(Map<String, dynamic>.from(e as Map)));
        } catch (_) {} // ek kharab entry baaki outbox na bigaade
      }
    } catch (_) {}
    return out;
  }

  static Future<void> _writeOutbox(
      SharedPreferences prefs, List<PendingMessage> items) async {
    final trimmed = items.length > maxOutbox
        ? items.sublist(items.length - maxOutbox)
        : items;
    await prefs.setString(
        _outboxKey, jsonEncode(trimmed.map((p) => p.toJson()).toList()));
  }

  /// Saare pending messages, purane pehle (send order).
  static Future<List<PendingMessage>> getAllPending() => _serial(() async {
        try {
          final prefs = await SharedPreferences.getInstance();
          final list = _readOutbox(prefs);
          list.sort((a, b) => a.createdAt.compareTo(b.createdAt));
          return list;
        } catch (_) {
          return <PendingMessage>[];
        }
      });

  static Future<List<PendingMessage>> getPending(String conversationId) async {
    final all = await getAllPending();
    return all.where((p) => p.conversationId == conversationId).toList();
  }

  static Future<void> upsertPending(PendingMessage p) => _serial(() async {
        try {
          final prefs = await SharedPreferences.getInstance();
          final list = _readOutbox(prefs);
          final i = list.indexWhere((x) => x.clientId == p.clientId);
          if (i == -1) {
            list.add(p);
          } else {
            list[i] = p;
          }
          await _writeOutbox(prefs, list);
        } catch (_) {}
      });

  /// Entry mile to `change` se naya version bana ke save karta hai.
  static Future<void> updatePending(
          String clientId, PendingMessage Function(PendingMessage) change) =>
      _serial(() async {
        try {
          final prefs = await SharedPreferences.getInstance();
          final list = _readOutbox(prefs);
          final i = list.indexWhere((x) => x.clientId == clientId);
          if (i == -1) return;
          list[i] = change(list[i]);
          await _writeOutbox(prefs, list);
        } catch (_) {}
      });

  static Future<void> removePending(String clientId) => _serial(() async {
        try {
          final prefs = await SharedPreferences.getInstance();
          final list = _readOutbox(prefs);
          final before = list.length;
          list.removeWhere((x) => x.clientId == clientId);
          if (list.length != before) await _writeOutbox(prefs, list);
        } catch (_) {}
      });
}