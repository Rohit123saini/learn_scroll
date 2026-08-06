// message/models/message_models.dart
//
// Ye models tere Django `serializers.py` ke fields se exactly match karte
// hain (UserMiniSerializer, ConversationListSerializer, MessageSerializer,
// GroupSerializer, etc.) — taaki JSON parsing me kabhi mismatch na ho.

// ======================================================================
// MESSAGE TYPE CONSTANTS — backend `MessageType` choices se match
// ======================================================================
class MessageType {
  static const text = 'text';
  static const image = 'image';
  static const video = 'video';
  static const audio = 'audio';
  static const file = 'file';
  static const presentation = 'presentation';
  static const location = 'location';
  static const system = 'system';
  // 🔥 NAYA — chat me ek clickable "Study Room" invite card bhejne ke
  // liye. Backend me is naye MessageType choice ko allow karna hoga
  // (Django `MessageType` choices me 'study_room' add karo).
  static const studyRoom = 'study_room';
}

// ======================================================================
// USER (MINI)
// ======================================================================
class UserMini {
  final String id;
  final String username;
  final String firstName;
  final String lastName;
  final String displayName;
  // 🔥 NAYA — backend `UserMiniSerializer` ab `profile_photo` bhejta hai
  // (absolute URL, ya null agar photo set hi nahi hai). Jahan bhi is
  // user ka avatar dikhana ho (conversation list, message sender, group
  // members, reactions, call history) sab yahin se milega.
  final String? profilePhoto;

  UserMini({
    required this.id,
    this.username = '',
    this.firstName = '',
    this.lastName = '',
    required this.displayName,
    this.profilePhoto,
  });

  factory UserMini.fromJson(Map<String, dynamic> json) {
    return UserMini(
      id: json['id']?.toString() ?? '',
      username: json['username']?.toString() ?? '',
      firstName: json['first_name']?.toString() ?? '',
      lastName: json['last_name']?.toString() ?? '',
      displayName: json['display_name']?.toString() ?? 'Unknown',
      profilePhoto: json['profile_photo']?.toString(),
    );
  }

  // 🔥 NAYA — cache me save karne ke liye (SharedPreferences me sirf
  // JSON string ja sakti hai, isliye har model ko wapas Map me todna
  // padta hai; fromJson isi shape ko expect karta hai).
  Map<String, dynamic> toJson() => {
        'id': id,
        'username': username,
        'first_name': firstName,
        'last_name': lastName,
        'display_name': displayName,
        'profile_photo': profilePhoto,
      };
}

// ======================================================================
// CONVERSATION SETTINGS (per-user mute/archive/pin)
// ======================================================================
class ConversationSettings {
  final bool isArchived;
  final bool isMuted;
  final bool isPinned;

  ConversationSettings({
    this.isArchived = false,
    this.isMuted = false,
    this.isPinned = false,
  });

  factory ConversationSettings.fromJson(Map<String, dynamic>? json) {
    if (json == null) return ConversationSettings();
    return ConversationSettings(
      isArchived: json['is_archived'] ?? false,
      isMuted: json['is_muted'] ?? false,
      isPinned: json['is_pinned'] ?? false,
    );
  }

  Map<String, dynamic> toJson() => {
        'is_archived': isArchived,
        'is_muted': isMuted,
        'is_pinned': isPinned,
      };
}

// ======================================================================
// GROUP (MINI) — conversation list ke andar dikhne wala
// ======================================================================
class GroupMini {
  final String id;
  final String name;
  final String? photoUrl;
  final int membersCount;

  GroupMini({
    required this.id,
    required this.name,
    this.photoUrl,
    this.membersCount = 0,
  });

  factory GroupMini.fromJson(Map<String, dynamic> json) {
    return GroupMini(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      photoUrl: json['photo_url']?.toString(),
      membersCount: json['members_count'] ?? 0,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'photo_url': photoUrl,
        'members_count': membersCount,
      };
}

// ======================================================================
// CONVERSATION — GET /message/conversations/
// ======================================================================
class ConversationModel {
  final String id;
  final String type; // "private" | "group"
  final UserMini? otherParticipant; // private chat ke liye
  final GroupMini? group; // group chat ke liye
  String? lastMessageText;
  DateTime? lastMessageAt;
  UserMini? lastMessageSender;
  String? lastMessageType;
  int unreadCount;
  ConversationSettings mySettings;
  final DateTime createdAt;

  ConversationModel({
    required this.id,
    required this.type,
    this.otherParticipant,
    this.group,
    this.lastMessageText,
    this.lastMessageAt,
    this.lastMessageSender,
    this.lastMessageType,
    this.unreadCount = 0,
    required this.mySettings,
    required this.createdAt,
  });

  bool get isGroup => type == 'group';

  // 🔥 List me dikhane ke liye title/photo — private ho ya group, single
  // jagah se nikal lo, UI me if/else likhne ki zaroorat nahi.
  String get displayTitle {
    if (isGroup) return group?.name ?? 'Group';
    return otherParticipant?.displayName ?? 'Unknown';
  }

  // 🔥 FIX — pehle private chat ke liye hamesha `null` return hota tha
  // (sirf group photo dikhta tha). Ab backend `other_participant` ke
  // andar `profile_photo` bhejta hai, isliye private chat list me bhi
  // us insaan ki asli photo dikhegi (na milne par UI apne aap fallback
  // icon/initials dikha deti hai).
  String? get displayPhoto => isGroup ? group?.photoUrl : otherParticipant?.profilePhoto;

  factory ConversationModel.fromJson(Map<String, dynamic> json) {
    return ConversationModel(
      id: json['id']?.toString() ?? '',
      type: json['type']?.toString() ?? 'private',
      otherParticipant: json['other_participant'] != null
          ? UserMini.fromJson(json['other_participant'])
          : null,
      group: json['group'] != null ? GroupMini.fromJson(json['group']) : null,
      lastMessageText: json['last_message_text']?.toString(),
      lastMessageAt: json['last_message_at'] != null
          ? DateTime.tryParse(json['last_message_at'].toString())
          : null,
      lastMessageSender: json['last_message_sender'] != null
          ? UserMini.fromJson(json['last_message_sender'])
          : null,
      lastMessageType: json['last_message_type']?.toString(),
      unreadCount: json['unread_count'] ?? 0,
      mySettings: ConversationSettings.fromJson(json['my_settings']),
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? '') ??
          DateTime.now(),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'type': type,
        'other_participant': otherParticipant?.toJson(),
        'group': group?.toJson(),
        'last_message_text': lastMessageText,
        'last_message_at': lastMessageAt?.toIso8601String(),
        'last_message_sender': lastMessageSender?.toJson(),
        'last_message_type': lastMessageType,
        'unread_count': unreadCount,
        'my_settings': mySettings.toJson(),
        'created_at': createdAt.toIso8601String(),
      };
}

// ======================================================================
// MESSAGE REACTION
// ======================================================================
class MessageReactionModel {
  final String id;
  final UserMini user;
  final String emoji;
  final DateTime createdAt;

  MessageReactionModel({
    required this.id,
    required this.user,
    required this.emoji,
    required this.createdAt,
  });

  factory MessageReactionModel.fromJson(Map<String, dynamic> json) {
    return MessageReactionModel(
      id: json['id']?.toString() ?? '',
      user: UserMini.fromJson(json['user'] ?? {}),
      emoji: json['emoji']?.toString() ?? '',
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? '') ??
          DateTime.now(),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'user': user.toJson(),
        'emoji': emoji,
        'created_at': createdAt.toIso8601String(),
      };
}

// ======================================================================
// REPLY PREVIEW (message.reply_to_detail)
// ======================================================================
class ReplyPreviewModel {
  final String id;
  final String type;
  final String? text;
  final UserMini sender;

  ReplyPreviewModel({
    required this.id,
    required this.type,
    this.text,
    required this.sender,
  });

  factory ReplyPreviewModel.fromJson(Map<String, dynamic> json) {
    return ReplyPreviewModel(
      id: json['id']?.toString() ?? '',
      type: json['type']?.toString() ?? 'text',
      text: json['text']?.toString(),
      sender: UserMini.fromJson(json['sender'] ?? {}),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'type': type,
        'text': text,
        'sender': sender.toJson(),
      };
}

// ======================================================================
// MESSAGE — GET /message/conversations/<id>/messages/  +  WebSocket events
// ======================================================================
class MessageModel {
  final String id;
  final String conversationId;
  final UserMini? sender;
  String type; // text/image/video/audio/file/presentation/location/system
  String? text;
  String? fileUrl;
  List<dynamic>? fileUrls;
  String? thumbnailUrl;
  Map<String, dynamic>? meta;
  final String? replyTo;
  final ReplyPreviewModel? replyToDetail;
  bool isEdited;
  final bool isForwarded;
  final bool isSystemMessage;
  bool deletedForEveryone;
  bool deletedForMe;
  final String? clientId;
  List<MessageReactionModel> reactions;
  bool isReadByMe;
  final DateTime createdAt;
  DateTime? updatedAt;

  // Local-only UI state (offline retry / upload progress ke liye) — server
  // se nahi aata, sirf frontend ke andar use hota hai.
  bool isSending;
  bool sendFailed;
  double? uploadProgress; // 0.0 - 1.0, media upload ke dauraan
  String? localFilePath; // upload complete hone tak local preview ke liye
  List<String>? localFilePaths; // 🔥 NAYA — ek saath bheje gaye multiple images ka local preview (fileUrls[] complete hone tak)

  MessageModel({
    required this.id,
    required this.conversationId,
    this.sender,
    required this.type,
    this.text,
    this.fileUrl,
    this.fileUrls,
    this.thumbnailUrl,
    this.meta,
    this.replyTo,
    this.replyToDetail,
    this.isEdited = false,
    this.isForwarded = false,
    this.isSystemMessage = false,
    this.deletedForEveryone = false,
    this.deletedForMe = false,
    this.clientId,
    List<MessageReactionModel>? reactions,
    this.isReadByMe = false,
    required this.createdAt,
    this.updatedAt,
    this.isSending = false,
    this.sendFailed = false,
    this.uploadProgress,
    this.localFilePath,
    this.localFilePaths,
  }) : reactions = reactions ?? [];

  /// Mera hi reaction (agar hai to) nikaalne ke liye — reaction picker me
  /// currently selected emoji highlight karna ho to kaam aata hai.
  String? myReaction(String myUserId) {
    for (final r in reactions) {
      if (r.user.id == myUserId) return r.emoji;
    }
    return null;
  }

  factory MessageModel.fromJson(Map<String, dynamic> json) {
    return MessageModel(
      id: json['id']?.toString() ?? '',
      conversationId: json['conversation']?.toString() ?? '',
      sender: json['sender'] != null ? UserMini.fromJson(json['sender']) : null,
      type: json['type']?.toString() ?? 'text',
      text: json['text']?.toString(),
      fileUrl: json['file_url']?.toString(),
      fileUrls: json['file_urls'],
      thumbnailUrl: json['thumbnail_url']?.toString(),
      meta: json['meta'] is Map<String, dynamic> ? json['meta'] : null,
      replyTo: json['reply_to']?.toString(),
      replyToDetail: json['reply_to_detail'] != null
          ? ReplyPreviewModel.fromJson(json['reply_to_detail'])
          : null,
      isEdited: json['is_edited'] ?? false,
      isForwarded: json['is_forwarded'] ?? false,
      isSystemMessage: json['is_system_message'] ?? false,
      deletedForEveryone: json['deleted_for_everyone'] ?? false,
      deletedForMe: json['deleted_for_me'] ?? false,
      clientId: json['client_id']?.toString(),
      reactions: (json['reactions'] as List<dynamic>? ?? [])
          .map((e) => MessageReactionModel.fromJson(e))
          .toList(),
      isReadByMe: json['is_read_by_me'] ?? false,
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? '') ??
          DateTime.now(),
      updatedAt: json['updated_at'] != null
          ? DateTime.tryParse(json['updated_at'].toString())
          : null,
    );
  }

  // 🔥 WebSocket "chat_message" event ka payload — ab text ke alawa media
  // fields (file_url/file_urls/thumbnail_url/meta) bhi carry karta hai,
  // kyunki backend `messages` REST action ab isi shape me broadcast karta
  // hai jab media message bheja jaata hai (dekho PATCH_views_realtime_broadcast.md).
  factory MessageModel.fromSocketEvent(Map<String, dynamic> json) {
    return MessageModel(
      id: json['id']?.toString() ?? '',
      conversationId: json['conversation_id']?.toString() ?? '',
      sender: UserMini(
        id: json['sender_id']?.toString() ?? '',
        username: json['sender_username']?.toString() ?? '',
        firstName: json['sender_first_name']?.toString() ?? '',
        lastName: json['sender_last_name']?.toString() ?? '',
        displayName: json['sender_name']?.toString() ?? 'Unknown',
        // FIX: backend (both ChatConsumer.handle_new_message and the
        // REST conversations/<id>/messages/ POST handler) sends this key
        // as `sender_profile_photo`, not `sender_avatar` — the old key
        // name here never matched, so the sender's avatar never loaded
        // on real-time/REST-broadcast messages.
        profilePhoto: json['sender_profile_photo']?.toString(),
      ),
      type: json['message_type']?.toString() ?? 'text',
      text: json['text']?.toString(),
      fileUrl: json['file_url']?.toString(),
      fileUrls: json['file_urls'],
      thumbnailUrl: json['thumbnail_url']?.toString(),
      meta: json['meta'] is Map<String, dynamic> ? json['meta'] : null,
      replyTo: json['reply_to']?.toString(),
      clientId: json['client_id']?.toString(),
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? '') ??
          DateTime.now(),
    );
  }

  // 🔥 NAYA — cache me save karne ke liye. Sirf server-confirmed fields
  // save karte hain; local-only UI state (isSending/sendFailed/
  // uploadProgress/localFilePath*) jaan-bujh kar SKIP kiya hai — cache se
  // reload hone par purana "sending..." ya "failed" spinner dobara nahi
  // dikhna chahiye, wo sirf current session ke liye valid hota hai.
  Map<String, dynamic> toJson() => {
        'id': id,
        'conversation': conversationId,
        'sender': sender?.toJson(),
        'type': type,
        'text': text,
        'file_url': fileUrl,
        'file_urls': fileUrls,
        'thumbnail_url': thumbnailUrl,
        'meta': meta,
        'reply_to': replyTo,
        'reply_to_detail': replyToDetail?.toJson(),
        'is_edited': isEdited,
        'is_forwarded': isForwarded,
        'is_system_message': isSystemMessage,
        'deleted_for_everyone': deletedForEveryone,
        'deleted_for_me': deletedForMe,
        'client_id': clientId,
        'reactions': reactions.map((r) => r.toJson()).toList(),
        'is_read_by_me': isReadByMe,
        'created_at': createdAt.toIso8601String(),
        'updated_at': updatedAt?.toIso8601String(),
      };
}