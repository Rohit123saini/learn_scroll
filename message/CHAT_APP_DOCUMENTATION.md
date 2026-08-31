# `message` App — Full System Documentation

Django + Django REST Framework + Django Channels chat backend (WhatsApp/Instagram-style).
1-1 & group chat, audio/video calls (LiveKit), persistent Study Rooms (whiteboard +
LiveKit), AI whiteboard summary/quiz (Gemini), FCM push notifications.

This file is meant to be **self-sufficient** — everything about models, REST endpoints,
WebSocket events, permissions, and helper functions is captured here so future work can
continue from this document alone, without re-reading the source files.

Auth model: `AUTH_USER_MODEL` is a **custom `User`** (app `login`), primary key is an
**integer** (not UUID). Fields used across this app: `id`, `username`, `first_name`,
`last_name`, `profile_photo` (ImageField), `is_active`.

---

## 1. File Map

| File | Purpose |
|---|---|
| `models.py` | All DB models |
| `serializers.py` | DRF serializers |
| `views.py` | REST views/viewsets (the bulk of the app logic) |
| `consumers.py` | Django Channels WebSocket consumers |
| `routing.py` | WS URL patterns |
| `Middleware.py` | JWT auth for WebSocket connections |
| `permissions.py` | DRF permission classes |
| `group_rules.py` | Group access-control (message/call/study-room permission, daily limit) |
| `mentions.py` | Shared `@mention` text-parsing helper (REST + WS) |
| `push_utils.py` | Firebase Cloud Messaging (FCM) push helpers. Firebase init is now **lazy** (only runs the first time a push is actually sent, not at import time) and reads either `FIREBASE_CREDENTIALS_PATH` or `settings.FCM_SERVICE_ACCOUNT_JSON_PATH` *(fix — see §9.0)*. Also now does **notification batching/digest** for normal chat messages — see §7.13 |
| `livekit_utils.py` | LiveKit JWT token generation (calls + study rooms) |
| `user_display.py` | Shared display-name / profile-photo-URL helper (REST + WS) |
| `upload_view.py` | Generic file-upload endpoint (returns a URL to attach to a message) |
| `ai_service.py` | Gemini calls for whiteboard summary/quiz (with caching) |
| `views_ai.py` | REST endpoints wrapping `ai_service.py` (`AiStudyRoomView`) + voice-message transcription (`VoiceTranscribeView`) + smart-reply suggestions (`SmartReplySuggestionsView`, *NEW*) |
| `media_utils.py` | `create_group_media_for_message(message)` — populates the `GroupMedia` gallery table; shared by REST + WS message-send |
| `constants.py` | Single shared source for cross-file constants (currently `MAX_PINNED_PER_CONVERSATION`) |
| `search_utils.py` *(NEW this session)* | `MIN_QUERY_LENGTH` (2), `apply_structured_filters(qs, query_params)` (sender/date_from/date_to/has_media/media_type), `search_messages(qs, query)` — Postgres-backed ranked full-text + trigram-typo search, non-Postgres fallback to plain `icontains`. `views.py`'s `ConversationViewSet.search`/`.search_all` already imported and called this module; the file itself didn't exist, so both search endpoints raised `NameError` on every call until this session. See §7.1 |
| `cache_utils.py` | Django-cache helpers: group-role cache (`get_group_role_cached`/`invalidate_group_role_cache`, 60s TTL) behind `group_rules.is_group_admin_or_mod`, and presence cache (`get_presence_cached`/`set_presence_cache`, 15s TTL) behind `UserPresenceView` + `ChatConsumer` |
| `throttles.py` | DRF `UserRateThrottle` subclasses for REST writes (`MessageSendThrottle`, `CallInitiateThrottle`, `GroupCreateThrottle`, `ReactionThrottle`) + per-IP safety-net throttles (`MessageSendIPThrottle`, `CallInitiateIPThrottle`, both `SimpleRateThrottle` subclasses via shared `ScopedIPThrottle`) *(NEW this session)* + `WSMessageRateLimiter` (cache-backed sliding window) for the WS `message` event, since DRF throttles don't apply to Channels consumers |
| `scheduled_messages.py` | "Send later" delivery half — `finalize_scheduled_message(message)`, called by the `message.send_scheduled_messages` Celery task (`tasks.py`, confirmed registered in `CELERY_BEAT_SCHEDULE`, runs every minute). Now also enqueues the link-preview/transcription tasks below for scheduled messages, same as the two live-send paths *(NEW this session)* |
| `tasks.py` | Celery tasks: `send_scheduled_messages` (delivers due "send later" messages, every minute — beat-scheduled), `cleanup_expired_messages` (hard-deletes disappearing messages past `expires_at`, every 15 min — beat-scheduled), `generate_link_preview_task` (background OpenGraph fetch, one-shot via `.delay()`), `transcribe_voice_message_task` (background voice-note transcription, one-shot via `.delay()`), `flush_chat_push_digest` *(NEW)* (one-shot, `countdown`-scheduled by `push_utils.send_chat_message_push` — flushes the debounced chat-push digest window, see §7.13) — plus the shared `_broadcast_meta_update()` helper the link-preview/transcription tasks use to push their result live over WS |
| `link_preview.py` *(NEW this session)* | `extract_first_url(text)` + `fetch_link_preview(url)` — SSRF-safe OpenGraph fetcher (blocks private/loopback/link-local IPs, manually re-checks every redirect hop, size/time-bounded fetch, 7-day negative+positive cache) used by `generate_link_preview_task` |
| `admin.py` | Django admin registrations |
| `apps.py` | App config (`name = 'message'`) |

**Note on `urls.py`:** an earlier upload of this file was accidentally a duplicate of
`Middleware.py`, so this doc used to assume the router/paths rather than confirm them.
The real `urls.py` has since been reconstructed from `views.py`'s actual class/action
names — router-registered ViewSets (`ConversationViewSet`, `MessageViewSet`,
`GroupViewSet`, `BlockedUserViewSet`, `CallHistoryViewSet`) plus plain `path()` entries
for `UserPresenceView`, `CallInitiateView`, `CallActionView`, `StudyRoomJoinView`,
`StudyRoomStateView`, `DeviceTokenView`, `AiStudyRoomView`, `VoiceTranscribeView`, and
`MessageUploadAPIView`. All endpoint paths below are confirmed against it.

---

## 2. Data Models (`models.py`)

### `BaseModel` (abstract)
Every model inherits this.
- `id` — UUID, primary key
- `created_at`, `updated_at` — auto timestamps
- `is_deleted` — soft-delete flag (present but not actively used anywhere yet — no
  queryset currently filters `is_deleted=False` by default)
- `Meta.ordering = ['-created_at']`

### Enums
- `MessageType`: text, image, video, audio, file, presentation, location, system,
  study_room, **poll** *(NEW — see `Poll`/`PollOption`/`PollVote` below)*
- `ConversationType`: private, group
- `DisappearingDuration`: none, 1_month, 6_months, 1_year (mapped to `timedelta` via
  `DISAPPEARING_DURATION_TIMEDELTA`)
- `CallType`: audio, video
- `CallStatus`: initiated, ringing, ongoing, ended, missed, rejected, busy

### `Conversation`
- `type` (private/group)
- `participants` — M2M to User **through** `ConversationParticipant`
- `private_key` — unique hash of sorted `(user1_id, user2_id)`; NULL for groups. Prevents
  duplicate 1-1 conversations (race-safe via `get_or_create_private()`)
- Denormalized last-message fields: `last_message_text`, `last_message_at`,
  `last_message_sender`, `last_message_type` (used for fast chat-list rendering)
- `disappearing_messages_duration` (default `6_months`) — one setting per conversation,
  shared by all participants
- `get_disappearing_timedelta()` → timedelta or None
- `Conversation.get_or_create_private(user_1, user_2)` — classmethod, race-safe

### `ConversationParticipant` (per-user chat settings — through model)
- `conversation`, `user`
- `is_archived`, `is_muted`, `is_pinned` (**conversation-level pin** — pins the *chat* in
  the list, different from message-pin, see below)
- `label` — custom nickname for this chat, visible only to this user
- `draft_text`, `draft_updated_at` *(NEW — server-side draft auto-save)* — half-typed
  compose-box text, per user per conversation, so it carries across devices. Saved
  through the existing `PATCH /conversations/<id>/settings/` endpoint (no new
  endpoint), same as mute/pin/archive. `draft_updated_at` is server-set (not
  client-writable) whenever `draft_text` changes — client should debounce saves
  (e.g. 1–2s after typing stops) rather than PATCH on every keystroke
- `unread_count`, `last_read_message`, `last_read_at`
- `joined_at`, `left_at` (soft-leave; **every membership check in the app filters on
  `left_at__isnull=True`** — this is the actual "is this user in the chat" source of truth)
- `unique_together = ('conversation', 'user')`

### `Group`
- `conversation` (OneToOne)
- `name`, `description`, `photo_url`
- `created_by`
- `invite_code` (unique, 8-char `secrets.token_urlsafe`)
- `is_private`
- `PermissionLevel` choices: `everyone` / `admins_only`
  - `message_permission`, `call_permission`, `study_room_permission` (each independently
    `everyone`/`admins_only`)
- `daily_message_limit` (null = no limit; admins/mods exempt)
- `members_count`, `messages_count` (denormalized counters, updated manually wherever
  membership changes)

### `GroupMember`
- `group`, `user`, `role` (admin/moderator/member), `added_by`
- `is_muted`, `is_banned`
- `unique_together = ('group', 'user')`
- **Important:** banning someone here does NOT by itself revoke access — `views.py`'s
  `update_member()` action explicitly also sets `ConversationParticipant.left_at` when
  `is_banned` flips true/false, because access checks key off `left_at`, not `is_banned`.

### `GroupJoinRequest` (private-group join flow)
- `group`, `user`, `status` (pending/approved/rejected)
- `responded_by`, `responded_at`
- `unique_together = ('group', 'user')` — re-requesting after rejection resets the same
  row to `pending` rather than creating a new one (history preserved)

### `Message`
- `conversation`, `sender`, `type`, `text`
- Media: `file_url`, `file_urls` (JSON list, for multi-image), `thumbnail_url`
- `meta` (JSON — size/duration/width/height/file_name/pages etc. **Also now used for**
  `link_preview` (`{url, title, description, image}`, TEXT messages only — see §7.5) and
  `transcript` (string, AUDIO messages only — see §7.6), both written asynchronously by
  Celery *after* the message is already sent/visible, never blocking the send itself)
- `reply_to` (self-FK)
- Flags: `is_edited`, `is_forwarded`, `is_system_message`
- Deletion: `deleted_for_everyone` (bool), `deleted_for_users` (M2M — "delete for me")
- `client_id` — idempotency key for offline-retry (unique per `conversation+sender`, DB
  constraint `unique_message_client_id`)
- `expires_at` — disappearing-messages snapshot, computed at send-time from the
  conversation's *current* duration setting; never recalculated later
- **`is_pinned`, `pinned_at`, `pinned_by`** *(NEW — message pin, see §7)*
- **`mentioned_users`** *(NEW — M2M, @mentions, see §7)*
- Indexes: `(conversation, -created_at)`, `(sender, -created_at)`, `(type)`,
  `(reply_to)`, `(expires_at)`, `(conversation, is_pinned)` *(new)*

### `Poll` / `PollOption` / `PollVote` *(NEW — WhatsApp-style poll messages)*
- A poll is a normal `Message` row with `type=poll` and `text=question` — reply/pin/
  star/search/disappearing-expiry/forward-block(see below) all work on it through the
  same `Message` machinery as any other message type. The poll-specific data lives in
  its own relational tables (not `meta` JSON) so votes can be uniquely constrained and
  counted efficiently:
  - `Poll` — `message` (OneToOne), `question`, `allow_multiple_answers` (bool,
    single-choice vs multi-choice), `is_closed`, `closed_at`, `closed_by`
  - `PollOption` — `poll` (FK), `text`, `order`
  - `PollVote` — `option` (FK), `user` (FK), `unique_together = ('option', 'user')` (a
    user can't vote the same option twice; the "only 1 option" rule for single-choice
    polls is enforced at the view level, not a DB constraint — see §4)
- Cascades: deleting the `Message` (e.g. `cleanup_expired_messages` hard-delete sweep
  for disappearing messages) cascades through `Poll` → `PollOption` → `PollVote`
  automatically.
- **Poll forwarding is not supported** — `MessageViewSet.forward` explicitly excludes
  `type=poll` messages from its source-message queryset, because forward there is a
  plain field-copy (text/file_url/meta) and has no logic to clone a `Poll` + its
  `PollOption`s. Attempting to include a poll's id in a forward request silently drops
  that one message (same as forwarding a message from a chat you're not in).

### `Presentation`
PPT/PDF/DOC metadata attached 1-1 to a `Message` — `file_url`, `file_name`, `file_size`,
`total_pages`, `file_type`, `cover_thumbnail`, optional `group` FK.

### `GroupMedia`
Denormalized gallery table (so gallery queries don't scan `Message`) — one row per
media message: `group`, `conversation`, `message` (OneToOne), `sender`, `file_url`,
`file_type`, `file_size`, `thumbnail_url`.
⚠️ **Not currently auto-populated anywhere in `views.py`/`consumers.py`** that was read —
no code creates a `GroupMedia` row when a media message is sent. The `GroupViewSet.media`
action reads from it, but unless something else populates it, the gallery will stay empty.
Worth checking / wiring up as a follow-up.

### `MessageStatus` (delivery/read receipts)
- `message`, `user`, `is_delivered`, `is_read`, `delivered_at`, `read_at`
- `unique_together = ('message', 'user')`

### `MessageReaction`
- `message`, `user`, `emoji` — `unique_together = ('message', 'user')` (one reaction per
  user per message; sending a new emoji replaces the old one via `update_or_create`)

### `CallSession`
- `type` (audio/video), `status`, `is_group_call`
- `conversation` (nullable), `group` (nullable), `caller`
- `channel_name` (unique — also the LiveKit room name), `token` (legacy Agora field,
  unused now that LiveKit is used)
- `started_at`, `connected_at`, `ended_at`, `duration_seconds`
- `is_recording`, `recording_url` (fields exist; no recording-trigger code was found)

### `CallParticipant`
- `call`, `user`, `joined_at`, `left_at`
- `is_muted`, `is_video_off`, `is_screen_sharing`, `is_deafened`
- `status` (defaults to `ringing`)
- `unique_together = ('call', 'user')`

### `UserPresence`
- `user` (OneToOne), `is_online`, `active_connections` (multi-device counter —
  online only when count > 0), `last_seen_at`

### `BlockedUser`
- `blocker`, `blocked` — `unique_together`

### `DeviceToken`
- `user`, `token` (unique), `platform` (android/ios/web)

### `StudyRoomState`
- `conversation` (OneToOne), `state` (JSON — `{"pages": [...]}`, whole whiteboard),
  `updated_by`

---

## 3. REST API — Conversations (`ConversationViewSet`, `ReadOnlyModelViewSet`)

Base queryset: conversations where the requesting user has an active
(`left_at__isnull=True`) `ConversationParticipant` row. `StandardPagination` (20/page,
max 50).

| Method & path (relative to viewset root) | Action | Notes |
|---|---|---|
| `GET /` | list | Ordered by `-last_message_at, -created_at` |
| `GET /<id>/` | retrieve | |
| `POST /start_private/` | `start_private` | body `{"user_id": ...}`. Creates or returns existing 1-1 conversation. 403 if either side has blocked the other. |
| `PATCH /<id>/settings/` | `update_settings` | Per-user mute/archive/pin/draft. Body = subset of `{is_archived, is_muted, is_pinned, draft_text}` *(`draft_text` NEW — see `ConversationParticipant` in §2; `draft_updated_at` is returned but server-set, not client-writable)* |
| `PATCH /<id>/disappearing_messages/` | `disappearing_messages` | Body `{"duration": "..."}`. Group: admin/mod only. Broadcasts `disappearing_messages_updated` to the room (handled by `ChatConsumer.disappearing_messages_updated`, see §8) |
| `PATCH /<id>/label/` | `update_label` | Per-user custom chat nickname. Empty string clears it. Max 100 chars |
| `POST /bulk_delete/` | `bulk_delete` | Body `{"conversation_ids": [...]}`. "Delete chat" for the requesting user only (sets `left_at`) |
| `POST /<id>/participants/` | `add_participant_to_conversation` | Groups only. Body `{"user_id": ...}`. Private group: admin/mod only |
| `GET /<id>/messages/` | `messages` (GET) | Paginated (`MessagePagination`, 30/page, max 100), excludes expired-disappearing messages |
| `POST /<id>/messages/` | `messages` (POST) | Send a message. Throttled 60/min/user (`MessageSendThrottle`). See §6 "Message send flow" below |
| `POST /<id>/poll/` *(NEW)* | `create_poll` | Body `{"question": "...", "options": ["A","B",...] (2–10), "allow_multiple_answers": false}`. Same block-check/group-permission/daily-limit rules as a normal message. Creates a `Message` (`type=poll`) + `Poll` + `PollOption` rows. See §7.8 |
| `POST /<id>/read_all/` | `read_all` | Bulk-mark all messages read + `unread_count = 0` |
| `GET /<id>/search/` *(NEW)* | `search` | `?q=...` (min 2 chars, `search_utils.MIN_QUERY_LENGTH`) + optional structured filters `sender`, `date_from`, `date_to`, `has_media`, `media_type`. Ranked full-text + typo-tolerant search within this conversation — see §7.1 |
| `GET /search_all/` *(NEW)* | `search_all` | Same `q` + filter params as above. Global search across every conversation the user is active in. Returns `MessageSearchResultSerializer` (adds `conversation_preview`) |
| `GET /<id>/pinned/` *(NEW)* | `pinned` | List of currently pinned messages in this conversation |

### Message-send flow (`ConversationViewSet.messages`, POST) — step by step
1. If group: enforce `message_permission` + `daily_message_limit` (`group_rules.py`)
2. If private: block-check both directions (`is_blocked_pair`)
3. Validate via `MessageCreateSerializer` (media types **require** `file_url` or
   `file_urls`; text requires non-empty `text`)
4. `client_id` idempotency check — if a message with the same `client_id` from this
   sender already exists in this conversation, return it as-is (no duplicate)
5. Compute `expires_at` from the conversation's *current* disappearing-duration
6. Inside `transaction.atomic()`: create `Message`, update conversation's denormalized
   last-message fields, increment `unread_count` for every other participant
7. **Resolve `@mentions`** *(NEW)* — `extract_mentioned_user_ids(text, conversation)`,
   excluding the sender, `.set()` onto `message.mentioned_users`
8. **Populate group gallery** *(FIXED this session)* — `create_group_media_for_message
   (message)` (`media_utils.py`). No-op unless the conversation is a group AND the
   message type is a gallery type (image/video/audio/file/presentation) AND it carries
   a `file_url` (or the first of `file_urls`). Creates the `GroupMedia` row that
   `GroupViewSet.media` reads from.
9. Broadcast `chat_message` event to `chat_{conversation_id}` channel group (includes
   `mentioned_user_ids` *(NEW field)*)
10. Broadcast lightweight `inbox_update` to every other participant's `user_{id}` group
    (drives the chat-list UI without a full refetch)
11. Push notifications: muted users AND mentioned users are excluded from the normal
    `send_chat_message_push`; mentioned users instead get `send_mention_push` *(NEW —
    bypasses mute, like WhatsApp)*
12. **Enqueue link preview** *(NEW this session)* — if `type == TEXT` and `text` contains
    a URL: `generate_link_preview_task.delay(message.id)`. Fully async, after the
    transaction commits (not inside step 6's `atomic()` block) so the worker is
    guaranteed a committed row.
13. **Enqueue voice transcription** *(NEW this session)* — if `type == AUDIO`:
    `transcribe_voice_message_task.delay(message.id)`. Same async/post-commit timing as
    step 12. See §7.5/§7.6 for what each task does.

---

## 4. REST API — Messages (`MessageViewSet`)

`GenericViewSet` + Retrieve/Update/Destroy mixins. `queryset =
Message.objects.select_related('conversation', 'sender')`.

Permissions (`get_permissions`):
- `update`/`partial_update`/`destroy` → `IsConversationParticipant` + `IsMessageSender`
- `forward` → `IsAuthenticated` only (checks membership manually per source/target)
- everything else (including `pin`, `react`, `read`, `poll_vote`, `poll_close`) →
  `IsConversationParticipant`

| Method & path | Action | Notes |
|---|---|---|
| `GET /<id>/` | retrieve | |
| `PATCH /<id>/` | `partial_update` (edit) | Text messages only, not `deleted_for_everyone`. Sets `is_edited=True` |
| `DELETE /<id>/?for_everyone=true\|false` | `destroy` | `for_everyone=true`: sender-only, blanks text/file_url, sets `deleted_for_everyone`. Else: adds requester to `deleted_for_users` ("delete for me") |
| `POST /<id>/react/` `DELETE /<id>/react/` | `react` | Body `{"emoji": "..."}`. One reaction per user (upsert). Throttled 120/min/user (`ReactionThrottle`) |
| `POST /<id>/read/` | `mark_read` | Marks read + decrements `unread_count` |
| `GET /<id>/read-status/` *(existed, undocumented until now)* | `read_status` | "Seen by" / message-info list — full `MessageStatus` per user (`MessageReadStatusSerializer`). Respects the read-receipt privacy toggle (§6/§7.11): a user with `show_read_receipts=False` has their own `read_at` hidden from this response, and if the *requester* has it off too, everyone else's `read_at` is hidden from what they see |
| `POST /messages/forward/` | `forward` | Body `{"message_ids": [...], "conversation_ids": [...], "caption": "..."}`. Creates NEW `Message` rows (copy, not pointer) with `is_forwarded=True`. Silently drops any message/conversation the user isn't a member of. Excludes expired-disappearing, deleted, **and poll** (`type=poll`) messages. `caption` *(NEW)* is optional — applied as the forwarded copy's text only when the source message had no text of its own (media/location); a text message's own text is never overwritten by it |
| `POST /<id>/pin/` `DELETE /<id>/pin/` *(NEW)* | `pin` | Group: admin/mod only (via `group_rules.is_group_admin_or_mod`). Private: either participant. Max **3** pinned per conversation (`MAX_PINNED_PER_CONVERSATION`). Broadcasts `pin_event` to the chat room |
| `POST /<id>/poll/vote/` *(NEW)* | `poll_vote` | Body `{"option_ids": [...]}`. Send the *full* set of options you want recorded — a fresh call replaces all of this user's previous vote(s) in that poll (so un-ticking one option in a multi-choice poll = resend the list without it). Single-choice polls reject more than 1 `option_id` with 400. 400 if the poll is closed. Broadcasts `poll_update` to the chat room. See §7.8 |
| `POST /<id>/poll/close/` *(NEW)* | `poll_close` | Poll creator, or group admin/mod, freezes the poll (further votes get 400; existing results stay visible). Broadcasts `poll_update`. See §7.8 |

---

## 5. REST API — Groups (`GroupViewSet`, `ModelViewSet`)

Queryset: groups where the user has a non-banned `GroupMember` row.

| Method & path | Action | Notes |
|---|---|---|
| `POST /` | `create` | Uses `GroupCreateSerializer`. Creates `Conversation` + `Group` + creator as admin + given `member_ids` (integers, not UUIDs). Throttled 5/min/user (`GroupCreateThrottle`) |
| `GET /`, `GET /<id>/` | list/retrieve | |
| `PATCH /<id>/` | `partial_update` | Admin/mod only (`IsGroupAdminOrModerator`) |
| `DELETE /<id>/` | `destroy` | **Admin role only** (not moderator). Broadcasts `group_deleted` *before* deleting (handled by `ChatConsumer.group_deleted`, see §8), then cascades delete via `Conversation.delete()` |
| `POST /<id>/members/` | `add_members` | Body `{"user_ids": [...]}`. Public group: any member can add. Private group: admin/mod only |
| `POST /join/` | `join` | Body `{"invite_code": "..."}`. Public: instant join. Private: creates/resets a `GroupJoinRequest` (pending) |
| `GET /<id>/join-requests/` | `join_requests` | Admin/mod only. Lists pending requests |
| `POST /<id>/join-requests/<request_id>/approve/` | `approve_join_request` | Admin/mod only |
| `POST /<id>/join-requests/<request_id>/reject/` | `reject_join_request` | Admin/mod only |
| `PATCH /<id>/members/<user_id>/` | `update_member` | Admin/mod only (unless acting on self). Body: any of `role`, `is_muted`, `is_banned`. Banning/unbanning also toggles `ConversationParticipant.left_at` |
| `DELETE /<id>/members/<user_id>/` | `update_member` | Self-leave allowed; removing someone else requires admin/mod |
| `GET /<id>/media/` | `media` | `GroupMedia` gallery, optional `?type=image/video/...` filter, paginated (`StandardPagination`). ⚠️ see `GroupMedia` note in §2 |

---

## 6. REST API — Other Endpoints

### `BlockedUserViewSet` (List/Create/Destroy)
- `GET /blocked-users/` — my block list
- `POST /blocked-users/` — body `{"blocked": "<user_id>"}`. Idempotent (`get_or_create`)
- `DELETE /blocked-users/<lookup>/` — lookup accepts EITHER the `BlockedUser` record id
  OR the target user's id directly

### `UserPresenceView` (Retrieve)
- `GET /presence/<user_id>/` — online/offline + last_seen. Read-through cached
  (`cache_utils.get_presence_cached`, 15s TTL) — auto-creates a `UserPresence` row only
  on a cache miss.

### `ReadReceiptSettingsView` — read-receipt privacy toggle
*(Existed in code already; documenting here for the first time — see §7.11.)*
- `GET /presence/read-receipts/` → `{"show_read_receipts": true|false}`
- `PATCH /presence/read-receipts/` → body `{"show_read_receipts": false}`
- Backed by `UserPresence.show_read_receipts` (default `True`). Mutual switch, WhatsApp-
  style: turning it off hides your `read_at` from everyone (including group members),
  AND — because it's mutual — you also stop seeing anyone else's `read_at` while it's
  off, even people who left theirs on. `is_delivered`/`delivered_at` (the single
  "delivered" tick) is never affected by this, only the "read"/blue-tick visibility.
  Enforced inside `MessageViewSet.read_status` (the "seen by" / message-info action) —
  see §4.

### Calls
- `POST /calls/initiate/` (`CallInitiateView`) — body `{"conversation_id", "type":
  "audio"|"video"}`. Throttled 10/min/user (`CallInitiateThrottle` — calls are costlier
  than a message: an FCM push + a LiveKit room each). Block-check (private) /
  `call_permission` check (group). Creates
  `CallSession` + `CallParticipant` rows (caller=ongoing, others=ringing). Broadcasts
  `incoming_call` `call_event` + sends `send_incoming_call_push`. Returns caller's
  LiveKit token immediately.
- `POST /calls/<call_id>/action/` (`CallActionView`) — body `{"action": "accept"|
  "reject"|"end"}`. Validates the caller is an invited `CallParticipant`.
  - `accept` → status=ongoing, mints & returns LiveKit token
  - `reject` → this participant rejected; non-group call ends entirely
  - `end` → any never-answered (`RINGING`) participants become `MISSED` +
    `send_call_cancelled_push`; call fully ends only once no participant is `ONGOING`
  - Broadcasts `call_<action>` to both `call_{id}` and `chat_{conversation_id}` groups
- `GET /calls/history/` (`CallHistoryViewSet.list`) — calls the user was caller/
  participant/group-member of
- `GET /calls/history/missed/?since=<iso>` — `missed` action — calls where this user's
  `CallParticipant.status == MISSED`
- `GET /calls/history/<call_id>/addable-participants/` — `addable_participants` — group/
  conversation members not yet in the call
- `POST /calls/history/<call_id>/add-participant/` — `add_participant` — adds someone to
  an ongoing group call; sends `incoming_call` push+event with the SAME `call_id`

### Study Room (`StudyRoomJoinView`, `StudyRoomStateView`)
- `POST /study-room/<conversation_id>/join/` — group: `study_room_permission` check.
  Room name = `study_<conversation_id>` (one persistent LiveKit room per conversation,
  Meet-style — no ringing/CallSession involved). Token TTL = 8 hours (vs. 2h default for
  calls, since study sessions run long). Returns `livekit_url`, `livekit_token`,
  `room_name`, and the full member list (names+photos) so the UI doesn't need to wait for
  the WS `user_joined` handshake.
- `GET /study-room/<conversation_id>/state/` — returns saved whiteboard
  `{"pages": [...]}` (or `{"pages": []}` if never saved — never 404s)
- `PUT /study-room/<conversation_id>/state/` — overwrite whole whiteboard state (client
  auto-saves periodically)
- `DELETE /study-room/<conversation_id>/state/` — clears the saved state (room "end")

### Device tokens (`DeviceTokenView`)
- `POST /device-token/` — body `{"token", "platform"}` — upsert by token
- `DELETE /device-token/` — body `{"token"}` — unregister

### File upload (`upload_view.py` → `MessageUploadAPIView`)
- `POST /message/upload/` (multipart, field `"file"`, max 200MB) — saves to
  `media/chat_media/<type>/<year>/<month>/<uuid>.<ext>`, returns `{file_url, file_type,
  file_size, file_name, mime_type}`. **No `Message` row is created here** — frontend gets
  the URL, then POSTs it via the normal `messages` endpoint or WS `message` event.
  `get_chat_media_type()` classifies by content-type/extension into
  image/video/audio/presentation/file.

### AI Study Room (`views_ai.py` → `AiStudyRoomView`)
- `POST /ai-study-room/` — body `{"mode": "summary"|"quiz", "content": "<whiteboard
  text, 20–10000 chars>"}`. Throttled 20/min/user (`AiStudyThrottle`). Delegates to
  `ai_service.py` (Gemini, cached 24h by content hash). Returns `{"summary": "..."}` or
  `{"questions": [...]}`. 503 if `AI_ENABLED` is False (Gemini client failed to init),
  500 with a generic message on any other failure (real error is `logger.exception`'d).

### AI Voice-message transcription (`views_ai.py` → `VoiceTranscribeView`)
- `POST /ai/transcribe/` — body `{"file_url": "<voice message file_url>", "mime_type":
  "audio/ogg"}`. Throttled 15/min/user (`AiTranscribeThrottle` — tighter than the study
  room's, since it downloads audio bytes before calling Gemini). Returns
  `{"transcript": "..."}`. `file_url` is whatever `upload_view.py` returned for an audio
  upload, or an already-sent voice message's `Message.file_url`. Stateless — doesn't
  write the transcript back onto the `Message` itself (caller can save it into
  `Message.meta["transcript"]` if it wants to avoid re-transcribing). 503 if
  `AI_ENABLED` is False, 500 with a generic message on any other failure.

### AI Smart-Reply Suggestions (`views_ai.py` → `SmartReplySuggestionsView`) *(NEW)*
- `POST /ai/smart-replies/` — body `{"conversation_id": "<uuid>"}`. Returns
  `{"suggestions": ["...", "...", "..."]}` (3 short tap-to-send quick-reply chips,
  Gmail/WhatsApp-Business-style). Throttled 30/min/user (`SmartReplyThrottle`, scope
  `ai_smart_reply` — looser than the study room's since a client may reasonably call
  this on every incoming message, but still bounded).
- Membership is checked the same way search/messages are (`ConversationParticipant`
  active row) and a non-member gets a plain 404, not 403, so existence of the
  conversation isn't leaked.
- Builds its prompt from the last 10 plain-text messages (`type='text'`, excludes
  deleted-for-everyone / deleted-for-me / scheduled), oldest-first, labelled `Me:`/
  `<first_name or "Them">:`. 400s if there's no usable context yet, or if the most
  recent message is the requester's own (nothing to reply to).
- Delegates to `ai_service.generate_reply_suggestions()`, which reuses the same 24h
  content-hash cache pattern as `generate_summary`/`generate_quiz`/`transcribe_audio` —
  repeat calls against the same conversation state don't re-hit Gemini.
- 503 if `AI_ENABLED` is False, 500 with a generic message (real error
  `logger.exception`'d) on any other failure — same pattern as the other two AI views.

---

## 7. Feature History (search / pin / mentions / link previews / auto-transcription /
polls / draft / read-receipt toggle)

*Subsections below are individually dated — "this session" in 7.1–7.3 refers to an
earlier review; 7.5–7.7 an older AI-features review; 7.8–7.10 are from the current
session (poll messages, forward-with-caption, server-side draft, read-receipt privacy
toggle). Kept as originally written rather than renumbered, so old references
elsewhere in this doc still point at the right item.*

### 7.1 Message Search *(implementation rewritten this session — see `search_utils.py`)*
- `GET /message/conversations/<id>/search/?q=...` — search within one conversation
- `GET /message/conversations/search_all/?q=...` — global search across all the user's
  active conversations, results include `conversation_preview` (name/photo/type of the
  chat each hit came from)
- **`search_utils.py` was a missing file** — `views.py`'s `ConversationViewSet.search`/
  `.search_all` already called `search_utils.MIN_QUERY_LENGTH` /
  `apply_structured_filters()` / `search_messages()`, but the module didn't exist, so
  both endpoints raised `NameError` on every request. Now implemented:
  - `MIN_QUERY_LENGTH = 2` — `q` shorter than this is rejected by the view before
    reaching `search_utils` (1-char search is noisy/expensive at scale, same reasoning
    WhatsApp/Instagram use)
  - **Postgres path** (`connection.vendor == 'postgresql'`): two matches OR'd together —
    (1) stemmed/ranked match against `Message.search_vector` (a `SearchVectorField`,
    auto-populated by a DB trigger — see migration `0900_message_search_vector.py`) via
    `SearchQuery`/`SearchRank`, so "running" also matches "run" and stop-words are
    ignored; (2) typo-tolerant match via `TrigramSimilarity` directly on the `text`
    column (threshold `0.25`, slightly looser than Postgres's own `0.3` default since
    chat messages are short), catching typos step (1) can't (e.g. "helo" vs "hello").
    Ordered `-rank, -similarity, -created_at` — strongest match first.
  - **Non-Postgres fallback** (sqlite, common in local dev/tests, where
    `SearchVectorField`/`pg_trgm` don't exist): unranked `text__icontains`, ordered
    `-created_at`. Degrades gracefully instead of crashing.
  - `apply_structured_filters(qs, query_params)` — independent of the text query, applied
    first to shrink the row set before the (more expensive) text search runs:
    `sender` (user id), `date_from`/`date_to` (`YYYY-MM-DD`, validated —
    `date_from > date_to` is a 400), `has_media` (`true`/`false`), `media_type` (must be
    one of `search_utils.MEDIA_TYPES` = image/video/audio/file/presentation). Returns
    `(filtered_qs, error_message_or_None)`; both `search` and `search_all` build their
    own 400 response from the error string so error formatting stays consistent between
    the two endpoints.
  - Excludes expired-disappearing, `deleted_for_everyone`, and "deleted for me"
    messages, same as the normal message list — unchanged from before this session.

### 7.2 Message Pin (WhatsApp-style)
- `Message.is_pinned` / `pinned_at` / `pinned_by`
- REST: `POST` / `DELETE` `/message/messages/<id>/pin/` (`MessageViewSet.pin`)
- WS: client sends `{"type": "pin", "message_id": "...", "pin": true|false}`; server
  broadcasts `{"type": "pin", "event": "pinned"|"unpinned", "message_id", ...}` to
  everyone in the chat room
- Permission: group → admin/moderator only (`group_rules.is_group_admin_or_mod`);
  private chat → either participant
- **Limit: 3 pinned messages per conversation** (`MAX_PINNED_PER_CONVERSATION`, now a
  single shared constant in `constants.py`, imported by both `MessageViewSet` and
  `ChatConsumer` — previously duplicated/manually kept in sync, see §9.3 item 4)
- `GET /message/conversations/<id>/pinned/` lists current pins

### 7.3 @Mentions
- New shared helper `mentions.py` → `extract_mentioned_user_ids(text, conversation)` —
  regex `@(\w+)` matched case-insensitively against the conversation's **active**
  members' usernames only (never any random user)
- `Message.mentioned_users` (M2M) set on every message create, both REST
  (`ConversationViewSet.messages`) and WS (`ChatConsumer.save_message`)
- Mentioned users are **excluded** from the normal `send_chat_message_push` and instead
  get `send_mention_push` (`push_utils.py`) — a distinct `type: "mention"` data-only
  push that is sent **even if the chat is muted** (intentional — matches WhatsApp
  behavior)
- `MessageSerializer.mentioned_users` — nested `UserMiniSerializer` list in every message
  response
- WS `chat_message` payload now includes `mentioned_user_ids: [str, ...]`

### 7.4 Already existed before this session (for completeness)
- **Message edit** — `PATCH /message/messages/<id>/` (`MessageViewSet.partial_update`)
- **Message forward** — `POST /message/messages/forward/` (`MessageViewSet.forward`) —
  now also accepts an optional `caption` (see §7.9)

### 7.5 Link Previews *(NEW this session)*
- Text messages containing a URL get an OpenGraph-style preview card (`title`,
  `description`, `image`) fetched **asynchronously** and stored at
  `Message.meta["link_preview"]` — no migration needed (`meta` is already `JSONField`).
- Flow: `views.py` (REST) / `consumers.py` (WS) / `scheduled_messages.py` (send-later) all
  call `generate_link_preview_task.delay(message.id)` right after the message row is
  committed. The Celery task (`tasks.py`) calls `link_preview.extract_first_url()` +
  `link_preview.fetch_link_preview()`, writes the result into `meta`, then broadcasts a
  `meta_update` WS event (see §8) so an already-open chat screen updates live instead of
  needing a refresh.
- **SSRF protection** (`link_preview.py`): only `http`/`https` allowed; hostname is
  resolved and rejected if the IP is private/loopback/link-local/multicast/reserved;
  every redirect hop is re-checked the same way (not just the original URL); fetch is
  time-boxed (4s) and size-boxed (300KB, stops at `</head>`); results (including
  negative "no preview found" results) are cached 7 days by URL.
- Fails silently and safely: dead link, timeout, non-HTML response, or an unsafe/internal
  URL all just mean no preview is added — the message itself was already sent and
  visible before this task even started, so a preview failure is invisible to the user.

### 7.6 Auto Voice-Message Transcription *(NEW this session)*
- `VoiceTranscribeView` (§6) already existed but was **client-triggered only** — a user
  had to tap "View transcript" to even find out a transcript was possible. This makes it
  automatic: every AUDIO message gets transcribed in the background the moment it's sent,
  reusing the exact same `ai_service.transcribe_audio()` call `VoiceTranscribeView` uses.
- Flow: same three call-sites as §7.5 (REST/WS/scheduled), `transcribe_voice_message_task.
  delay(message.id)`. Result is written to `Message.meta["transcript"]`, then broadcasts
  the same `meta_update` WS event as the link-preview task.
- No-ops cleanly if `ai_service.AI_ENABLED` is `False` (Gemini not configured) or the
  message has no `file_url` — a voice message is never blocked or delayed waiting on
  this; transcription is purely additive, arrives after the message is already visible.
- `VoiceTranscribeView` itself is unchanged and still works standalone (e.g. to
  re-transcribe, or for any voice note sent before this feature existed).

### 7.7 `meta_update` WebSocket event *(NEW this session, supports 7.5 & 7.6)*
- New server→client WS event type. Plain passthrough handler on `ChatConsumer`, same
  pattern as `disappearing_messages_updated`/`edit_event`. Payload: `{"type":
  "meta_update", "message_id": "...", "meta": {...full updated meta dict...}}`.
- Only ever sent by `tasks._broadcast_meta_update()` (`tasks.py`), i.e. only from the two
  background tasks above — nothing else broadcasts this event yet, but it's a generic
  enough shape that a future "any background meta change" feature could reuse it instead
  of inventing a new event type.

### 7.8 Poll Messages *(NEW this session)*
- WhatsApp-style poll: `MessageType.POLL` + new `Poll`/`PollOption`/`PollVote` models
  (see §2). A poll is a real `Message` row (`type=poll`, `text=question`), so it
  automatically gets reply/pin/star/search/disappearing-expiry the same as any other
  message — only voting and results are poll-specific.
- Create: `POST /message/conversations/<id>/poll/` (`ConversationViewSet.create_poll`).
  Same permission/block/throttle path as a normal message send (group
  `message_permission` + `daily_message_limit`, private-chat block-check), then creates
  `Message` + `Poll` + `PollOption` rows inside one `transaction.atomic()` and broadcasts
  a normal `chat_message` WS event (with a nested `poll` object) + `inbox_update` +
  `send_chat_message_push` (preview text `"📊 <question>"`) — same fan-out as a normal
  text message, no separate code path for group members to "discover" a new poll.
- Vote: `POST /message/messages/<id>/poll/vote/` (`MessageViewSet.poll_vote`). Body
  `{"option_ids": [...]}` is the requester's **full** desired vote set for that poll — a
  fresh call deletes their previous `PollVote` row(s) for that poll and re-creates from
  the new list. Single-choice polls (`allow_multiple_answers=False`) 400 if more than 1
  `option_id` is sent. Voting on a closed poll 400s.
- Close: `POST /message/messages/<id>/poll/close/` (`MessageViewSet.poll_close`) — poll
  creator, or group admin/moderator, can close (further votes rejected, existing results
  stay visible forever). Private chat: only the creator (no "admin" concept there).
- Live updates: both `poll_vote` and `poll_close` broadcast a new `poll_update` WS event
  (plain passthrough handler, same pattern as `meta_update`) carrying the full updated
  `Poll` (all options + current vote counts) — an open chat screen sees vote counts
  change live without a refresh.
- **Not supported yet**: voting via WebSocket (REST-only, same as `schedule_message`);
  forwarding a poll (`MessageViewSet.forward` explicitly excludes `type=poll` — see §2);
  a dedicated "clear my vote entirely" call (`option_ids` requires at least 1 — to fully
  un-vote today, resend the vote list without the option, or the client just doesn't
  call vote until the user picks something).
- `MessageSerializer.poll` — new field, a nested `PollSerializer` (question, options
  with per-option `votes_count`/`voted_by_me`, `total_voters`, `is_closed`) — `null` for
  every non-poll message type.

### 7.9 Forward with Caption *(NEW this session)*
- `POST /message/messages/forward/` now accepts an optional `"caption"` string in the
  body alongside the existing `message_ids`/`conversation_ids`.
- Applies **only** to forwarded copies whose source message had no text of its own
  (media/location messages) — a text message's own text is never silently overwritten
  or appended to. If the caption is set and the source was e.g. an image with no
  caption, the forwarded copy's `text` becomes the caption (WhatsApp-style "add a note
  while forwarding").
- Poll messages (`type=poll`) are now excluded from `forward` entirely (see §2/§7.8) —
  this was a pre-existing gap made visible while adding polls, not something the
  caption change itself introduced.

### 7.10 Server-Side Draft Auto-Save *(NEW this session)*
- `ConversationParticipant.draft_text` + `draft_updated_at` (see §2) — reuses the
  existing `PATCH /message/conversations/<id>/settings/` endpoint (no new endpoint;
  `ConversationSettingsSerializer` just gained 2 fields alongside
  `is_archived`/`is_muted`/`is_pinned`).
- `draft_updated_at` is server-set on every write where `draft_text` is present in the
  request (not client-writable) — a simple last-write-wins signal for multi-device
  clients, no server-side merge logic.
- Client responsibility: debounce writes (e.g. save 1–2s after the user stops typing),
  not a PATCH per keystroke — the endpoint has no draft-specific rate limit, only the
  project-wide `user: 100/min` DRF default (§14).
- Surfaced in `ConversationListSerializer.my_settings` too (same
  `ConversationSettingsSerializer`), so the chat list can restore/show a draft without a
  second request.

### 7.11 Read-Receipt Privacy Toggle
- **Already existed** before this session — `UserPresence.show_read_receipts` (default
  `True`) + `ReadReceiptSettingsView` (`GET`/`PATCH /message/presence/read-receipts/`).
  Included here only so this doc's feature list stays accurate; no code changed for it
  this session. See §2 (`UserPresence`) and §6 for the full behavior (mutual switch —
  turning it off hides your `read_at` from others AND hides others' `read_at` from you;
  `is_delivered`/delivery double-tick is unaffected).

### 7.12 Smart-Reply Suggestions *(NEW this session)*
- `POST /message/ai/smart-replies/` (`views_ai.py` → `SmartReplySuggestionsView`) — see
  §6 for the full endpoint contract.
- Reuses the already-connected Gemini client (`ai_service.py`) that summary/quiz/
  transcription use, via a new `generate_reply_suggestions(context_text)` function —
  same 24h content-hash caching convention as the rest of that module.
- New throttle scope `ai_smart_reply` (30/min, `SmartReplyThrottle`) — **needs a
  `DEFAULT_THROTTLE_RATES["ai_smart_reply"]` entry in `settings.py`**, same failure mode
  already documented in §9.1 item 1 for the other 7 scopes (a missing rate entry raises
  `ImproperlyConfigured` on the very first call). `settings.py` wasn't part of this
  review, so this isn't confirmed present or absent — flagged in §9.4.

### 7.13 Chat Push Notification Batching / Digest *(NEW this session)*
- `push_utils.send_chat_message_push` no longer sends an FCM push immediately for every
  message. It now accumulates a per-`(user, conversation)` counter + "most recent
  message" snapshot in cache for a debounce window (`CHAT_PUSH_DEBOUNCE_SECONDS`, default
  30s, env-overridable), and schedules **one** Celery task
  (`tasks.flush_chat_push_digest`, `countdown=CHAT_PUSH_DEBOUNCE_SECONDS`) to flush it —
  WhatsApp-style: if the user doesn't open the app for a while and several messages land
  in the same window, they get a single "X sent N messages" push instead of N separate
  ones.
- Race-safety: the "have I already scheduled a flush for this window" flag is set with
  `cache.add` (not `cache.set`), so only the *first* message in a burst actually enqueues
  `flush_chat_push_digest` — the rest just increment the counter.
- `flush_chat_push_digest` (`tasks.py`) reads back the accumulated count + last-message
  snapshot, explicitly clears the three cache keys (not just left to TTL, so a message
  arriving mid-flush cleanly starts a *new* window instead of folding into one already
  in flight), then sends either a normal single-message push (`count == 1`, via the
  existing `_send_single_chat_push`) or a batched digest push (`count > 1`, via
  `send_chat_digest_push`, new `type: "chat_digest"` data-only payload — no
  `message_id`, since a digest isn't about one specific message).
- **This closes a real bug, not just an enhancement — see §9.1.** `send_chat_message_push`
  already unconditionally imported and called `tasks.flush_chat_push_digest` before this
  task existed anywhere in the codebase; every call to it (i.e. every ordinary chat
  message push, from all three send paths — REST, WS, and scheduled-message delivery)
  would raise an `ImportError` the instant it tried to schedule the flush. Mentions and
  calls were unaffected (`send_mention_push`/`send_incoming_call_push` bypass this path
  entirely and push immediately), but **all ordinary chat-message push notifications were
  silently broken end-to-end** until `flush_chat_push_digest` was added.
- Mentions still bypass batching entirely — `send_mention_push` is unchanged, always
  immediate/priority, even if the chat is muted (§7.3).

---

## 8. WebSocket API (`consumers.py`, `routing.py`)

Auth: `Middleware.py` → `JWTAuthMiddleware`. No cookie/session auth — client connects
with `?token=<JWT_ACCESS_TOKEN>` in the query string; token is verified via
`rest_framework_simplejwt.AccessToken`, resolving `scope['user']`.

| URL | Consumer | Purpose |
|---|---|---|
| `wss://.../ws/chat/<conversation_id>/?token=...` | `ChatConsumer` | Per-conversation realtime chat |
| `wss://.../ws/call/<call_id>/?token=...` | `CallConsumer` | WebRTC/LiveKit signaling relay for one call |
| `wss://.../ws/inbox/?token=...` | `InboxConsumer` | One global connection per session — drives the chat-list screen |

### `ChatConsumer`
`connect()`: rejects unauthenticated (`4001`) and non-members (`4003`); joins
`chat_{conversation_id}` channel group; marks presence online; marks any undelivered
messages as delivered.

`disconnect()`: every step (`group_discard`, presence update) wrapped in
`asyncio.wait_for(..., timeout=3)` so a slow/unreachable Redis/DB can't hang the
disconnect indefinitely (Daphne force-kills stuck disconnects otherwise).

**Client → Server** (`{"type": ..., ...}`):

| type | Fields | Handler |
|---|---|---|
| `message` | `client_id`, `message_type`, `text`, `reply_to`, `file_url`, `file_urls`, `thumbnail_url`, `meta` *(last 4 NEW this session — see below)* | `handle_new_message` |
| `typing` | `is_typing` | `handle_typing` |
| `read` | `message_id` | `handle_read_receipt` |
| `delete` | `message_id`, `for_everyone` | `handle_delete_message` |
| `reaction` | `message_id`, `emoji` | `handle_reaction` |
| `pin` *(NEW)* | `message_id`, `pin` (bool) | `handle_pin_message` |
| `study_room_event` | `action`, `data` | `handle_study_room_event` (generic passthrough, nothing persisted server-side) |

**Server → Client** (channel-group event `type` → consumer method):

| type | Method | Notes |
|---|---|---|
| `chat_message` | `chat_message` | New message. Includes `mentioned_user_ids` *(NEW)* |
| `typing_event` | `typing_event` | |
| `read_event` | `read_event` | |
| `delete_event` | `delete_event` | |
| `reaction_event` | `reaction_event` | |
| `pin_event` *(NEW)* | `pin_event` | `{event: "pinned"|"unpinned", message_id, conversation_id, actor_id}` |
| `presence_update` | `presence_update` | |
| `call_event` | `call_event` | Relayed call notifications (incoming/accept/reject/end) |
| `study_room_broadcast` | `study_room_broadcast` | Echoes back to everyone except the sender's own channel |
| `disappearing_messages_updated` | `disappearing_messages_updated` | `{conversation_id, duration, updated_by}` — matches `ConversationViewSet.disappearing_messages`'s `group_send()` |
| `group_deleted` | `group_deleted` | `{group_id, conversation_id, deleted_by}` — matches `GroupViewSet.destroy`'s `group_send()`, sent *before* the cascade delete |
| `meta_update` *(NEW)* | `meta_update` | `{message_id, meta}` — sent only by the background link-preview/voice-transcription Celery tasks (`tasks._broadcast_meta_update`), see §7.5/§7.6/§7.7 |
| `poll_update` *(NEW)* | `poll_update` | `{message_id, poll: {...full updated Poll...}, voted_by \| closed_by}` — sent by `MessageViewSet.poll_vote`/`poll_close` (REST-only, no WS trigger). Plain passthrough, same pattern as `meta_update`. See §7.8 |

**⚠️→✅ FIXED this session — WS media messages.** `handle_new_message`/`save_message`
previously only accepted `text`; there was no way to send an image/video/audio/file/
presentation message over the WebSocket at all (`file_url` etc. weren't read from the
client payload, and `Message.objects.create()` never set them) — media sends had to go
through REST. Both now accept and persist `file_url`, `file_urls`, `thumbnail_url`,
`meta`, and the server validates that a media `message_type` carries at least one of
`file_url`/`file_urls` (mirrors REST's `MessageCreateSerializer`). The broadcast
`chat_message` payload now includes these fields too (previously only `text` was
broadcast, so a WS-sent media message wouldn't show the file to other members even if
it had been saved).

**Key server-side helpers on `ChatConsumer`:**
- `save_message()` — creates the `Message` (now including media fields — see above),
  updates conversation denorm fields, bulk-creates `MessageStatus` for other members,
  increments their `unread_count`, resolves and sets `mentioned_users` *(NEW)*, calls
  `create_group_media_for_message()` *(see §9.3)*, returns
  `{'id', 'created_at', 'mentioned_ids'}`
- `pin_or_unpin_message()` *(NEW)* — mirrors `MessageViewSet.pin`'s permission + limit
  logic for the WS path
- `check_group_message_rules()` — mirrors REST's `group_rules` checks
- `is_blocked_in_conversation()` — mirrors REST's block check
- `send_push_for_message(..., exclude_ids=...)` — normal chat push, now accepts
  `exclude_ids` *(NEW)* to skip mentioned users
- `send_mention_push_notification()` *(NEW)*

### `InboxConsumer`
Read-only fan-out channel. One per logged-in session, joins only `user_{user_id}`. Every
place a message is created (REST or WS) also sends a light `inbox_update` event to each
recipient's `user_{id}` group so the conversations-list screen updates without a chat
being open.

### `CallConsumer`
Pure signaling relay (SDP/ICE) — no media flows through Django; that's LiveKit's job.

**Client → Server:** `signal` (relays `payload` to the other peer, excluding echo to
self via `sender_channel_name`), `mute`/`video_off` (updates `CallParticipant` flag +
relays), `leave` (closes socket).

**Server → Client:** `call_signal` → sent as raw `event['data']` (not wrapped) — i.e.
client receives `{"event": "signal"|"user_joined"|"user_left"|"mute"|"video_off",
...}` directly.

`connect()` also has a reverse-join safety net: if the peer already joined and is
`ONGOING` before this user's `user_joined` broadcast could reach them, this user
proactively receives a synthetic `user_joined` for that peer.

`disconnect()` marks the participant left; if that was the last `ONGOING` participant,
computes `duration_seconds` and marks the whole `CallSession` `ENDED`.

---

## 9. Known Issues / Follow-ups

### 9.0 Fixed in this session

1. **`flush_chat_push_digest` Celery task was missing entirely, breaking all ordinary
   chat-message pushes.** See §7.13 for the full explanation — `push_utils.
   send_chat_message_push` already called it unconditionally as part of the new
   batching/digest logic, and its absence meant every normal message push (REST, WS,
   and scheduled-delivery paths alike) raised an `ImportError`. Added the task to
   `tasks.py`.
2. **`push_utils.py` used to raise at import time if `FIREBASE_CREDENTIALS_PATH` was
   unset** (see the now-stale §13 note this replaces) — since `push_utils` is imported
   by `views.py` at Django startup, a missing credentials path used to crash the entire
   process, including plain REST/chat endpoints that never touch push at all (same class
   of bug §9.3 documents for `livekit_utils.py`). Firebase init is now **lazy**: it only
   runs the first time a push actually needs to be sent, and any failure there is logged
   + swallowed by `_send_multicast`'s own try/except instead of taking the app down.
3. **`FIREBASE_CREDENTIALS_PATH` vs. `settings.FCM_SERVICE_ACCOUNT_JSON_PATH` mismatch
   reconciled** — previously flagged (old §14 note) as `push_utils.py` reading a
   different env var than the one `settings.py` defines. It now accepts either,
   preferring the env var (back-compat) and falling back to the Django setting, so
   neither convention needs a `settings.py`/deploy change to work.
4. **`search_utils.py` was a missing file, breaking both search endpoints on every
   call.** `views.py`'s `ConversationViewSet.search`/`.search_all` already imported and
   called `search_utils.MIN_QUERY_LENGTH` / `apply_structured_filters()` /
   `search_messages()`, but the module itself was never uploaded/created — both
   endpoints raised `NameError` 100% of the time. Now implemented with ranked Postgres
   full-text search (`SearchVector`/`SearchRank`) OR'd with `TrigramSimilarity` for
   typo tolerance, structured filters (sender/date range/media), and a plain
   `icontains` fallback for non-Postgres databases. See §7.1 and the `search_utils.py`
   row in §1.
5. **`GroupMedia.file_size` could silently stay `null`.** `media_utils.py`'s
   `_resolve_file_size()` previously only trusted `message.meta.get("size")`, which
   depended on every client (REST + WS, every attachment type) round-tripping the
   `file_size` the upload endpoint returned back into `meta.size` at message-send time —
   any path that missed this left the gallery's file size blank with no error. Now falls
   back to asking the storage backend for the real size (`default_storage.size(...)`,
   works for local disk and S3-backed storage alike) whenever the client didn't supply
   one. See the (now-resolved) §9.4 item 3 for the full before/after.

### 9.1 Fixed in this review

1. **`settings.py` was missing `DEFAULT_THROTTLE_RATES` entries for 7 of the `message`
   app's own throttle scopes** — `message_send`, `call_initiate`, `group_create`,
   `reaction` (all `throttles.py`), `ai_transcribe` (`views_ai.py`), and the two new
   `message_send_ip`/`call_initiate_ip` scopes (§9.1 item 2 below). Same failure mode
   `settings.py` already documents (and had already fixed) for several `liveclass`
   scopes: DRF's `UserRateThrottle`/`SimpleRateThrottle` look up their rate via
   `DEFAULT_THROTTLE_RATES[self.scope]` exactly like `ScopedRateThrottle` does — a
   missing entry raises `ImproperlyConfigured` on the **very first** hit, not a rare
   edge case. This meant the very first message sent, call initiated, group created,
   reaction added, or voice note transcribed would 500. Added all 7 rates.
2. **Per-IP throttling added** (`throttles.py`) — `MessageSendIPThrottle` (120/min) and
   `CallInitiateIPThrottle` (20/min), both `SimpleRateThrottle` subclasses via a shared
   `ScopedIPThrottle` base. All existing throttles were per-**authenticated-user**
   only — fine against normal abuse, but bypassable by anyone with multiple
   accounts/leaked tokens from one IP. These are a safety-net layer *alongside* the
   per-user throttles (both apply — DRF checks every throttle in the list), wired into
   `ConversationViewSet.get_throttles()` (messages POST) and `CallInitiateView.
   throttle_classes`.
3. **`CHANNEL_LAYERS`/`CACHES` Redis backends and the `message` app's
   `CELERY_BEAT_SCHEDULE` entries — confirmed present and correctly env-driven** in
   `settings.py` (this is the first review pass where `settings.py` itself was
   available). `REDIS_URL` (falls back to `CELERY_BROKER_URL`) drives both the Channels
   layer and the cache backend, with a safe `InMemoryChannelLayer`/`LocMemCache`
   fallback for local dev when neither is set. `message.send_scheduled_messages`
   (every minute) and `message.cleanup_expired_messages` (every 15 min) are both
   registered in `CELERY_BEAT_SCHEDULE` — this **resolves §9.4 item 4** below, which was
   flagged as unconfirmed in an earlier review.

### 9.2 Fixed in a previous review

1. **`throttles.py` was never actually wired into `views.py`.** The file itself already
   had full setup instructions in its own docstring, but no view ever imported
   `MessageSendThrottle` / `CallInitiateThrottle` / `GroupCreateThrottle` /
   `ReactionThrottle` — every REST write those were meant to guard (message send, call
   initiate, group create, react) was unthrottled; only the WS message path
   (`WSMessageRateLimiter`) was actually protected. Added `get_throttles()` to
   `ConversationViewSet` (messages POST), `MessageViewSet` (react), `GroupViewSet`
   (create), and `throttle_classes` to `CallInitiateView` — exactly per the file's own
   setup comment.
2. **Presence caching was written but never read from or written to.**
   `cache_utils.get_presence_cached` / `set_presence_cache` existed with a docstring
   naming `UserPresenceView` and `ChatConsumer`'s presence update as the two intended
   call-sites, but both still hit the DB directly on every check. Wired both up:
   `ChatConsumer.set_presence()` now refreshes the cache on every connect/disconnect;
   `UserPresenceView.get_object()` now reads the cache first and only falls back to
   `UserPresence.objects.get_or_create(...)` on a miss (warming the cache after).
3. **`VoiceTranscribeView` was fully implemented but never routed.** `views_ai.py` had a
   complete, throttled endpoint whose own docstring documents its intended route
   (`POST /message/ai/transcribe/`), but `urls.py` only ever imported `AiStudyRoomView`
   — the transcribe endpoint 404'd. Added the import and a `path('ai/transcribe/', ...)`
   entry.
4. **Group-role cache invalidation gaps at `add_members` / `approve_join_request` /
   `join`.** `cache_utils.py`'s own setup docstring explicitly names `add_members` and
   `approve_join_request` (alongside `update_member`, which *was* already doing it) as
   required `invalidate_group_role_cache` call-sites — neither of those two, nor the
   public-group instant-`join` path, actually called it. Low-severity in practice (a
   newly added regular member reading as "not yet a member" for up to the 60s TTL
   doesn't change what they're allowed to do), but now consistent with the documented
   contract. All three sites now invalidate the cache for the newly (re)added user(s).

### 9.3 Fixed in an earlier session

1. **`media_utils.py` was missing entirely.** `views.py` line 69 already did
   `from .media_utils import create_group_media_for_message`, but the file itself was
   never created/uploaded — this was an import-time crash waiting to happen (the whole
   app would fail to boot the moment `views.py` got imported, since a missing module on
   a top-level `from .x import y` is a hard `ModuleNotFoundError`, not something that
   fails gracefully at request time). **Root cause of §9.3 item 2 below** — this is why
   the gallery was "never populated": the function that was supposed to do it didn't
   exist as a file. Created `media_utils.py` with `create_group_media_for_message
   (message)`: no-ops unless conversation is a group AND message type is a gallery type
   (image/video/audio/file/presentation) AND a `file_url` (or first of `file_urls`) is
   present; uses `get_or_create` on the OneToOne `message` field so a duplicate call
   never raises; wraps everything in try/except + `logger.exception` so a gallery-write
   failure can never fail the message-send itself.
2. **`GroupMedia` gallery table was never populated** — direct consequence of #1.
   `create_group_media_for_message()` is now called from **both** message-creation
   paths: REST (`ConversationViewSet.messages`, was already calling it — it just had
   nowhere to call *into*) and WS (`ChatConsumer.save_message`, newly wired that
   session). `/groups/<id>/media/` should now populate correctly regardless of which
   path the media message came in through.
3. **WS could not actually send media messages at all** (found while fixing #2 — to
   wire `GroupMedia` into the WS path, the WS path needed to *have* file data first,
   and it didn't). `handle_new_message`/`save_message` previously only read/persisted
   `text` — no `file_url`/`file_urls`/`thumbnail_url`/`meta` fields existed on the
   socket payload or the `Message.objects.create()` call, and the `chat_message`
   broadcast didn't carry them either. All fixed — see §8 note above the WS helpers
   list for the full before/after.
4. **`MAX_PINNED_PER_CONVERSATION = 3`** was duplicated as a class constant in both
   `MessageViewSet` (views.py) and `ChatConsumer` (consumers.py), manually kept in sync.
   Moved to a new shared `constants.py`; both files now import the single module-level
   constant instead of redefining it.
5. **Missing WS handlers for two server-sent event types** — *(already fixed in the
   code before that session; documented for the record)*. `ChatConsumer.
   disappearing_messages_updated` and `ChatConsumer.group_deleted` both exist as plain
   passthrough methods (same pattern as `presence_update`), matching the `group_send()`
   calls in `views.py`'s `ConversationViewSet.disappearing_messages` and
   `GroupViewSet.destroy`.
6. **`urls.py` was a duplicate of `Middleware.py`** — the real routing file didn't
   exist, so nothing past the ViewSet defaults (search, pin, star, schedule, media,
   ...) actually resolved. Reconstructed from `views.py`'s confirmed class/action names
   — see the §1 file-map note.

### 9.4 Still open

1. **`is_deleted` on `BaseModel`** exists but nothing in the read code ever sets it or
   filters by it — either dead field or a soft-delete feature that was never finished.
2. **`CallSession.token` / Agora fields** (`is_recording`, `recording_url`) exist on the
   model but the app has fully moved to LiveKit — `token` looks unused;
   recording start/stop code was not found anywhere.
3. ~~**`media_utils.py`'s `file_size`** is read from `message.meta.get("size")`...~~
   **Resolved this session** — `_resolve_file_size(message, file_url)` now falls back to
   asking the storage backend directly (`default_storage.size(relative_path)`, works for
   local `FileSystemStorage` and S3-via-`django-storages` alike) whenever
   `meta.get("size")` is missing/unparseable, by stripping `settings.MEDIA_URL` off
   `file_url` to get the storage-relative path. Client-supplied `meta.size` is still
   preferred when present (never overridden); the storage lookup is best-effort only —
   any failure (bad URL, file not found, storage error) is swallowed and logged at
   `debug`, never blocks the message-send or the `GroupMedia` row from being created.
4. ~~No management-command file for `send_scheduled_messages` was seen...~~ **Resolved,
   see §9.1 item 3** — `tasks.py` (Celery, not a management command) plus its
   `CELERY_BEAT_SCHEDULE` registration in `settings.py` are both now confirmed present.
5. **`STORAGES["default"]` is still local `FileSystemStorage`** *(NEW note)* —
   `upload_view.py` uses `default_storage`, so switching the backend (e.g. to S3 via
   `django-storages`) needs zero code changes, only a `settings.py` change. Not urgent
   for a single-server deployment, but local disk means uploaded files don't survive a
   redeploy/scale-out to multiple app servers.
6. **`link_preview.py`'s OG-tag parser is regex-based, not a real HTML parser**
   *(NEW)* — works for the vast majority of real-world `<meta property="og:...">` tags
   but could miss unusual attribute ordering/quoting on some sites. Fine for a
   best-effort preview feature (fails closed to "no preview" on a parse miss, never
   crashes); swap for `BeautifulSoup` if preview accuracy becomes a complaint.
7. **`ai_smart_reply` throttle scope needs a `DEFAULT_THROTTLE_RATES` entry** *(NEW)* —
   same failure mode as §9.1 item 1 (missing scope → `ImproperlyConfigured` on first
   call). `settings.py` wasn't re-reviewed this session, so unlike the other 7 scopes
   this one isn't confirmed present or absent yet — check before `SmartReplySuggestionsView`
   goes live.
8. **`CHAT_PUSH_DEBOUNCE_SECONDS` means every ordinary chat push is delayed by design**
   *(NEW)* — up to 30s (default) between a message being sent and its push notification
   arriving, even for a single message with no burst. Worth confirming this trade-off
   (fewer, better-grouped notifications vs. push latency) is the intended UX — see §7.13.

---

## 10. Helper Modules Reference

### `group_rules.py`
- `is_group_admin_or_mod(group, user_id) -> bool`
- `check_group_permission(group, user_id, permission_field) -> (allowed, reason)` —
  `permission_field` ∈ `{'message_permission','call_permission','study_room_permission'}`
- `check_daily_message_limit(group, user, conversation) -> (allowed, reason)` — admin/mod
  exempt; simple day-boundary `Message.count()` query (no extra table)

### `mentions.py` *(NEW)*
- `extract_mentioned_user_ids(text, conversation) -> list[int]`

### `push_utils.py` (Firebase Admin SDK, `FIREBASE_CREDENTIALS_PATH` **or**
`settings.FCM_SERVICE_ACCOUNT_JSON_PATH` — lazy init, see §9.0 items 2–3)
- `send_push_to_users(recipient_ids, title, body, data=None)` — generic, WITH visible
  notification (only used for non-chat pushes)
- `send_chat_message_push(...)` — **no longer sends FCM directly.** Accumulates a debounce
  window per `(user, conversation)` in cache and schedules `tasks.flush_chat_push_digest`
  once per window — see §7.13 for the full batching/digest flow
- `_send_single_chat_push(...)` *(NEW, internal)* — the actual single-message FCM call,
  data-only; called by `flush_chat_push_digest` when a window only accumulated 1 message
- `send_chat_digest_push(recipient_id, conversation_id, sender_name, count)` *(NEW)* —
  batched "X sent N messages" push, data-only `type: "chat_digest"`, no `message_id`
- `send_incoming_call_push(...)` — data-only, `type: incoming_call`
- `send_call_cancelled_push(...)` — data-only, `type: call_cancelled`
- `send_mention_push(...)` — data-only, `type: mention`, bypasses mute **and bypasses the
  chat-push digest/batching above** — always immediate, same as calls
- All multicast sends clean up `DeviceToken`s that FCM reports as `UnregisteredError`

### `livekit_utils.py` (env: `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`)
- `generate_livekit_token(room_name, user_id, user_name, ttl=timedelta(hours=2)) -> str`
  — calls use the 2h default; `StudyRoomJoinView` overrides to 8h

### `user_display.py`
- `get_display_name(user)` — "First Last" → username → `str(user)`, never blank
- `get_profile_photo_url(user, request=None)` — absolute URL via `request` if given,
  else `settings.MEDIA_ABSOLUTE_BASE_URL` prefix, else relative URL, else `None`
- `build_user_mini(user, request=None) -> dict` — the canonical shape used by both
  `UserMiniSerializer` (REST) and every WS payload (`sender_*` fields), so the Flutter
  side can parse one model class from either source

### `Middleware.py`
- `JWTAuthMiddleware` — parses `?token=` from the WS query string, validates via
  `rest_framework_simplejwt.AccessToken`, resolves `scope['user']` (or `AnonymousUser`)

### `ai_service.py`
- Gemini client (`google-genai`), model configurable via `GEMINI_MODEL` env
  (default `gemini-2.5-flash`; note `gemini-2.0-flash` was retired June 1 2026 — this is
  why the env var was made configurable instead of hardcoded)
- `generate_summary(content) -> str`, `generate_quiz(content) -> list[dict]` — both
  cached 24h by `sha256(content)` via Django cache
- Failures logged at `CRITICAL` (so a retired/invalid model doesn't die silently for
  months — this happened once already, see the file's own comments)

### `media_utils.py`
- `create_group_media_for_message(message) -> GroupMedia | None`
- Called from both `ConversationViewSet.messages` (REST) and `ChatConsumer.save_message`
  (WS) right after a `Message` is created. No-ops for private chats, non-media message
  types, or messages with no file. See §9.3 for why this file didn't exist before.
- `_resolve_file_size(message, file_url)` *(NEW this session)* — internal helper for the
  `GroupMedia.file_size` field. Prefers `message.meta["size"]` (client-supplied) when
  present; otherwise strips `settings.MEDIA_URL` off `file_url` to get a
  storage-relative path and asks `default_storage.size(...)` directly (works for local
  `FileSystemStorage` and S3-via-`django-storages` alike). Best-effort only — any
  failure is caught and logged at `debug`, never blocks the `GroupMedia` row or the
  message-send. See §9.0 item 5.

### `constants.py`
- `MAX_PINNED_PER_CONVERSATION = 3` — single shared source, imported by both
  `MessageViewSet.pin` (views.py) and `ChatConsumer.pin_or_unpin_message` (consumers.py).

### `search_utils.py` *(NEW this session — see §7.1, §9.0 item 4)*
- `MIN_QUERY_LENGTH = 2` — queries shorter than this should be rejected by the caller
  before reaching this module
- `TRIGRAM_SIMILARITY_THRESHOLD = 0.25` — looser than Postgres's own `0.3` default,
  tuned for short chat messages
- `MEDIA_TYPES = {'image','video','audio','file','presentation'}` — valid values for the
  `media_type` filter / what `has_media=true` matches against
- `apply_structured_filters(qs, query_params) -> (qs, error_message_or_None)` — `sender`,
  `date_from`/`date_to` (`YYYY-MM-DD`), `has_media` (`true`/`false`), `media_type`
  filters; independent of the text query, applied first
- `search_messages(qs, query) -> qs` — Postgres: `SearchRank`-ordered `search_vector`
  match OR'd with `TrigramSimilarity` on `text`, ordered `-rank, -similarity,
  -created_at`. Non-Postgres (e.g. sqlite in local dev/tests): unranked
  `text__icontains`, ordered `-created_at`
- Depends on `Message.search_vector` (a `SearchVectorField`, auto-populated by a DB
  trigger — see migration `0900_message_search_vector.py`) and the Postgres `pg_trgm`
  extension for `TrigramSimilarity`; both are Postgres-only, hence the `connection.vendor`
  branch

### `cache_utils.py`
- Django's default cache backend (should be Redis in production — check `settings.py`;
  it's the same backend `ai_service.py` already uses for its 24h summary/quiz cache).
- Group-role cache: `get_group_role_cached(group_id, user_id)` / `invalidate_group_role_cache
  (group_id, user_id)` — 60s TTL, sits behind `group_rules.is_group_admin_or_mod` (by far
  the highest-hit query in the app — runs on every pin/message/call/study-room/
  member-management action). Also caches the "not a member" case as a sentinel, so a
  non-member repeatedly probing an admin-only action doesn't hit the DB every time
  either. Invalidated on every `GroupMember.role`/`is_banned` write:
  `GroupViewSet.update_member`, `add_members`, `approve_join_request`, and the public
  instant-`join` path.
- Presence cache: `get_presence_cached(user_id)` / `set_presence_cache(user_id,
  is_online, last_seen_at)` — 15s TTL (presence changes far more often than a role
  does). Read from `UserPresenceView`, written from `ChatConsumer.set_presence()` on
  every connect/disconnect.

### `throttles.py`
- REST (DRF `UserRateThrottle` subclasses, per-authenticated-user, cache-backed):
  `MessageSendThrottle` (60/min, `ConversationViewSet.messages` POST),
  `CallInitiateThrottle` (10/min — calls are costlier: an FCM push + a LiveKit room
  each), `GroupCreateThrottle` (5/min), `ReactionThrottle` (120/min,
  `MessageViewSet.react`).
- WS: `WSMessageRateLimiter` — DRF throttles don't apply to Channels consumers, so this
  is a small dependency-free fixed-window counter (60 messages/60s per user) on the same
  cache backend, used in `ChatConsumer.handle_new_message`. Not billing-grade precision,
  just abuse-prevention.

### `tasks.py`
- `send_scheduled_messages` — beat-scheduled every minute; delivers due "send later"
  messages via `scheduled_messages.finalize_scheduled_message`. `select_for_update
  (skip_locked=True)` + bounded 200/run batch so overlapping beat ticks can't
  double-send. See §10's `scheduled_messages.py` entry below.
- `cleanup_expired_messages` — beat-scheduled every 15 min; hard-deletes disappearing
  messages past `expires_at` (bounded 500/batch loop).
- `generate_link_preview_task` / `transcribe_voice_message_task` — one-shot,
  `.delay(message_id)`-triggered right after a message is created (REST/WS/scheduled),
  not beat-scheduled. See §7.5/§7.6. Share `_broadcast_meta_update()` to push their
  result live over WS once done.
- `flush_chat_push_digest(user_id, conversation_id)` *(NEW)* — one-shot,
  `countdown`-scheduled by `push_utils.send_chat_message_push` (not beat-scheduled).
  Flushes one debounce window into a single push. See §7.13.

### `scheduled_messages.py`
- `finalize_scheduled_message(message)` — the delivery half of "Send Later". A scheduled
  `Message` row (created by `ConversationViewSet.schedule_message`) sits invisible to
  everyone but the sender until this runs: flips `is_scheduled=False`, recomputes
  disappearing-message `expires_at` and `created_at` off the *current* moment (not the
  original schedule time, so it lands in the right spot in the chat-list order),
  updates conversation denorm fields/unread counts/`MessageStatus` rows/@mentions/
  `GroupMedia`, then broadcasts `chat_message` + `inbox_update` and sends the same
  mute/mention-aware pushes a normal send does.
- Called by the `message.send_scheduled_messages` **Celery task** (`tasks.py`, not a
  management command — see §10's `tasks.py` entry), registered in `CELERY_BEAT_SCHEDULE`
  (`settings.py`) to run every minute. **Confirmed present and scheduled — see §9.1
  item 3** (this was flagged as unconfirmed in an earlier review, before `settings.py`
  had been reviewed).
- Now also enqueues `generate_link_preview_task`/`transcribe_voice_message_task` after
  finalizing a scheduled message, same as the two live-send paths *(NEW this session,
  see §7.5/§7.6)*.

---

## 11. Serializers Reference (`serializers.py`)

- `UserMiniSerializer` — id, username, first_name, last_name, display_name (computed),
  profile_photo (computed, absolute URL)
- `ConversationSettingsSerializer` — is_archived/is_muted/is_pinned (per-user), plus
  `draft_text`/`draft_updated_at` *(NEW — `draft_updated_at` is read-only, server sets
  it whenever `draft_text` is written; see §7.10)*
- `GroupMiniSerializer` — id, name, photo_url, members_count
- `ConversationListSerializer` — full chat-list row shape (other_participant OR group,
  last-message fields, unread_count, my_settings)
- `ReplyPreviewSerializer` — minimal shape for `reply_to_detail`
- `MessageReactionSerializer`
- `PollOptionSerializer` *(NEW)* — id, text, order, `votes_count`, `voted_by_me`
  (per-requester)
- `PollSerializer` *(NEW)* — question, allow_multiple_answers, is_closed, closed_at,
  closed_by (nested), options (nested list), total_voters (distinct voters, not votes)
- `PollCreateSerializer` *(NEW, plain `Serializer` not `ModelSerializer`)* — input for
  `create_poll`; validates 2–10 non-blank unique options
- `PollVoteSerializer` *(NEW, plain `Serializer`)* — input for `poll_vote`; `option_ids`
  list, min 1
- `MessageSerializer` — the full message shape (GET responses). Includes
  `is_pinned`, `pinned_at`, `pinned_by` (nested), `mentioned_users` (nested list), and
  now **`poll`** *(NEW — nested `PollSerializer`, `null` for non-poll messages)*.
  `to_representation` blanks `text`/`file_url` and adds `deleted_for_me: true`
  when the requester is in `deleted_for_users`
- `MessageSearchResultSerializer` — extends `MessageSerializer`, adds
  `conversation_preview` (`{type, name, photo_url}`) — used only by `search_all`
- `MessageCreateSerializer` — input for message send; validates media-type messages
  carry `file_url`/`file_urls`
- `GroupMemberSerializer`, `GroupJoinRequestSerializer`, `GroupSerializer` (includes the
  4 access-control fields), `GroupCreateSerializer` (`member_ids` = `IntegerField` list —
  NOT UUIDs, matches the custom `User` model's integer PK)
- `PresentationSerializer`, `GroupMediaSerializer`
- `UserPresenceSerializer`, `BlockedUserSerializer`, `CallSessionSerializer`

---

## 12. Permissions Reference (`permissions.py`)

- `IsConversationParticipant` — object-level: requester has an active
  `ConversationParticipant` row for `obj.conversation` (or `obj` itself if it *is* a
  conversation)
- `IsMessageSender` — `obj.sender_id == request.user.id`
- `IsGroupAdminOrModerator` — view-level: requester is admin/mod (non-banned) of the
  `group_id`/`pk` in `view.kwargs`

---

## 13. Environment Variables Required

| Var | Used by | Notes |
|---|---|---|
| `GEMINI_API_KEY` | `ai_service.py` | Required for AI features to init |
| `GEMINI_MODEL` | `ai_service.py` | Default `gemini-2.5-flash` |
| `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | `livekit_utils.py` | **Raises at import time** if missing |
| `LIVEKIT_WS_URL` | `views.py` | Default `ws://10.93.221.189:7880` — looks like a dev/internal IP, confirm for prod |
| `FIREBASE_CREDENTIALS_PATH` | `push_utils.py` | Path to Firebase service-account JSON. **Lazy init as of this session (see §9.0)** — no longer raises at import/process-startup time; only raised (and logged, not crashed) the first time a push is actually sent with no path configured. Falls back to `settings.FCM_SERVICE_ACCOUNT_JSON_PATH` if unset |
| `CHAT_PUSH_DEBOUNCE_SECONDS` *(NEW)* | `push_utils.py` | Default `30`. Debounce window for the chat-message push digest/batching — see §7.13 |
| `MEDIA_ABSOLUTE_BASE_URL` (Django setting, not env strictly) | `user_display.py` | Used only when no `request` context is available (WS payloads) |
| `REDIS_URL` (or `CELERY_BROKER_URL` as fallback) | `settings.py` → `CHANNEL_LAYERS`, `CACHES`, Celery | **Not `message`-specific**, but this app's realtime broadcast, all its caches, and its 5 Celery tasks all depend on it being set in production — see §14 |

---

## 14. Project Settings (`settings.py`) — Infra This App Depends On

This app doesn't ship its own settings — everything below lives in the project-level
`settings.py` (project `LearnScroll`, shared with `login`, `user_profile`, `post`, and
`liveclass`). Documented here because `message`'s realtime (§8) and background-task
(§7.5/§7.6, `tasks.py`) features are directly load-bearing on these.

### Channels / WebSocket transport
- `CHANNEL_LAYERS` → `channels_redis.core.RedisChannelLayer` when `REDIS_URL` (or its
  fallback `CELERY_BROKER_URL`) is set, else `InMemoryChannelLayer`. **The in-memory
  fallback only works correctly with a single worker process** — with 2+ Daphne/Uvicorn
  workers (any real deployment), users on different workers silently stop seeing each
  other's `chat_message`/`meta_update`/etc. broadcasts. Confirm `REDIS_URL` is actually
  set in production `.env`.

### Cache backend
- `CACHES` → `django_redis.cache.RedisCache` (same `REDIS_URL`) when set, else
  `LocMemCache`. Backs: `cache_utils.py`'s group-role (60s TTL) and presence (15s TTL)
  caches, `ai_service.py`'s 24h summary/quiz cache, `WSMessageRateLimiter` and every
  `throttles.py` rate counter, and `link_preview.py`'s 7-day preview cache *(NEW)*. Same
  multi-worker caveat as `CHANNEL_LAYERS` — `LocMemCache` is per-process, so rate limits
  and cached previews would be inconsistent across workers without Redis.

### Celery — broker, beat schedule
- `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` — default to `redis://localhost:6379/0`
  if unset (dev-friendly, but production should set `REDIS_URL`/these explicitly).
- `CELERY_TASK_ACKS_LATE = True` + `CELERY_TASK_REJECT_ON_WORKER_LOST = True` — a task
  killed mid-run (worker crash/restart) gets redelivered instead of silently lost.
  Relevant to all 5 of `message/tasks.py`'s tasks.
- `CELERY_BEAT_SCHEDULE` — the `message` app's 2 periodic entries, confirmed present:
  - `message-send-scheduled-messages` → `message.send_scheduled_messages`, every minute
  - `message-cleanup-expired-messages` → `message.cleanup_expired_messages`, every 15 min
  - `generate_link_preview_task`/`transcribe_voice_message_task`/
    `flush_chat_push_digest` *(latter NEW this session)* are **not** in this schedule and
    don't need to be — they're one-shot, triggered directly via `.delay()`/
    `.apply_async(countdown=...)` right after a message is created or a push window
    opens (§7.5/§7.6/§7.13), not a periodic sweep.

### REST throttle rates (`REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`)
All 7 of the `message` app's custom throttle scopes, confirmed present *(the
`message_send`/`call_initiate`/`group_create`/`reaction`/`ai_transcribe`/
`message_send_ip`/`call_initiate_ip` rows were the §9.1 item 1 fix — missing before this
review)*, **plus 1 new scope this session that is NOT yet confirmed** (see §9.4 item 7):

| Scope | Rate | Throttle class | Guards |
|---|---|---|---|
| `message_send` | 60/min | `MessageSendThrottle` | `ConversationViewSet.messages` POST |
| `message_send_ip` | 120/min | `MessageSendIPThrottle` | same endpoint, per-IP |
| `call_initiate` | 10/min | `CallInitiateThrottle` | `CallInitiateView` |
| `call_initiate_ip` | 20/min | `CallInitiateIPThrottle` | same endpoint, per-IP |
| `group_create` | 5/min | `GroupCreateThrottle` | `GroupViewSet.create` |
| `reaction` | 120/min | `ReactionThrottle` | `MessageViewSet.react` |
| `ai_study` | 20/min | `AiStudyThrottle` | `AiStudyRoomView` |
| `ai_transcribe` | 15/min | `AiTranscribeThrottle` | `VoiceTranscribeView` |
| `ai_smart_reply` *(NEW — not confirmed in `settings.py`)* | 30/min | `SmartReplyThrottle` | `SmartReplySuggestionsView` |

Project-wide floor (applies to `message`'s views too, on top of the above where set):
`DEFAULT_THROTTLE_CLASSES = [UserRateThrottle, AnonRateThrottle]`, rates `user: 100/min`,
`anon: 20/min`. `DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]` (fail-closed default —
every `message` view already sets its own `permission_classes` explicitly, so this
doesn't change current behavior, only protects a future view that forgets to).

### Other settings touching this app
- `AUTH_USER_MODEL = "login.User"` — confirms the integer-PK custom user model assumed
  throughout this doc (§ intro).
- `DATA_UPLOAD_MAX_MEMORY_SIZE = 10MB` — caps non-file JSON/form body size (e.g. a very
  long pasted `text` message); actual file uploads are bounded separately by
  `upload_view.py`'s own 200MB cap.
- `SIMPLE_JWT` — `ACCESS_TOKEN_LIFETIME = 1 day`. Relevant to `Middleware.py`'s WS auth
  (`?token=...`) — a socket connection made with a token near expiry will still work for
  the life of that connection (JWT is only checked at `connect()`), but a reconnect after
  expiry needs a refreshed token from the client.
- `FCM_SERVICE_ACCOUNT_JSON_PATH` is defined here — **as of this session `push_utils.py`
  reads either this setting or the `FIREBASE_CREDENTIALS_PATH` env var** (env var takes
  precedence for back-compat). Previously flagged as a mismatch (§9.0 item 3) — now
  resolved, no `settings.py` change needed.
- `SENTRY_DSN` — if set, `logger.exception()`/`logger.error()` calls throughout
  `message` (e.g. `media_utils.py`'s swallowed gallery-write failures,
  `push_utils.py`'s swallowed FCM failures, the new `generate_link_preview_task`/
  `transcribe_voice_message_task` failure logs) are captured as Sentry events instead of
  console-only. `send_default_pii=False` — chat text/message content is not sent to
  Sentry by default.
- `STORAGES["default"]` — plain `FileSystemStorage` (local disk). See §9.4 item 5.

---

## 15. Suggested Next Facilities (not yet implemented)

- Chat/media export
- Poll "clear my vote entirely" action (currently `option_ids` requires min 1 — see §7.8)
- Poll forwarding (currently excluded from `forward`, see §2/§7.8/§7.9)

*(Scheduled messages, voice-message transcription, link previews, per-IP throttling,
poll messages, forward-with-caption, server-side draft auto-save, and the read-receipt
privacy toggle were previously listed here as "not yet implemented" — all now exist in
code: `scheduled_messages.py` + `tasks.py` for the first, `VoiceTranscribeView` +
`transcribe_voice_message_task` for the second, `link_preview.py` +
`generate_link_preview_task` for the third, `ScopedIPThrottle` subclasses for the
fourth, `Poll`/`PollOption`/`PollVote` + `ConversationViewSet.create_poll` +
`MessageViewSet.poll_vote`/`poll_close` for the fifth (§7.8), the `caption` param on
`MessageViewSet.forward` for the sixth (§7.9), `ConversationParticipant.draft_text` for
the seventh (§7.10), and `UserPresence.show_read_receipts` +
`ReadReceiptSettingsView` for the eighth (§7.11 — this one existed in code even before
this session, just wasn't documented until now). This list is corrected as of this
review — see §9.1/§9.4 for what's still genuinely open.)*

Plus the "Still open" items in §9.4 — smaller than the above, but worth clearing before
new facilities are stacked on top.