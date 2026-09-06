# LearnScroll — Flutter Messaging + Calls + Study Room
## Architecture & Process Reference (living doc)

> Ye file hi ab "source of truth" hai. Isse aage koi bhi kaam isi doc ke
> against hoga — agar future me naya file mile ya kuch update ho, isी
> file ko edit/extend karna hai (naya section jodo, purana update karo),
> puri file dobara mat likho jab tak zaroorat na ho.
>
> App ka naam CallKit params me "LearnScroll" milta hai — isliye yahi
> project name treat kar rahe hain.

---

## 0. High-Level Summary

Ek Flutter chat app (WhatsApp/Instagram-DM jaisa) jisme:
- 1:1 aur group text messaging (realtime via WebSocket + REST fallback)
- Media messages (image/video/audio/file/presentation/location/sticker)
- Voice/video calls (1:1 + group, LiveKit SFU based) with native
  CallKit-style incoming call UI
- "Study Room" — Google-Meet-style multi-user video + collaborative
  whiteboard (drawing, shapes, text, sticky notes, PDF/image paging,
  AI summary/quiz tools, pomodoro timer)
- Polls, scheduled messages, pinned messages, forwarding, reactions,
  disappearing messages, per-chat wallpaper, block/unblock
- Push notifications (FCM) with foreground/background/killed handling,
  missed-call detection on reconnect

Backend: Django REST Framework + Django Channels (WebSocket) + LiveKit
(SFU for audio/video/screen-share). Media stored on backend server,
accessed via absolute URLs. Auth: JWT Bearer tokens.

---

## 1. Tech Stack / Key Packages (from imports seen so far)

- State: plain `StatefulWidget` + `ChangeNotifier` singletons (no
  Provider/Riverpod/Bloc seen) — `CallManager`, `InboxSocketService`,
  `MissedCallWatcher`, `CallKitService` are all singletons
  (`instance` pattern).
- Networking: `http` (most REST calls) + `dio` (file upload/download
  with progress — `uploadFile`, `MediaDownloadService`)
- Realtime: `web_socket_channel` (raw WS, 2 separate connections —
  see §5.5/§5.6)
- Calls/Video: `livekit_client`, `flutter_webrtc` (screen share
  helper), `wakelock_plus`, `flutter_background` (foreground service
  for screen-share only), `permission_handler`
- Native incoming call UI: `flutter_callkit_incoming` (**pinned to
  `2.5.0+2`** — see call_kit_service.dart header, older versions break
  full-screen-intent on Android 14+)
- Push: `firebase_core`, `firebase_messaging`, `flutter_local_notifications`
- Storage: `shared_preferences` (tokens, cache, missed-call watermark)
- Media: `image_picker`, `file_selector`, `cached_network_image`,
  `video_player`, `audioplayers`, `record` (voice notes),
  `path_provider`, `gal` (gallery save), `open_filex`
- PDF/whiteboard: `syncfusion_flutter_pdfviewer`, `printing`, `pdf`
- Misc: `geolocator` (location messages), `connectivity_plus`
  (missed-call watcher), `device_info_plus`, `uuid`, `timeago`,
  `url_launcher`
- 🔥 NAYA (Phase 8): `flutter_tts` (~^4.x — client-only text-to-speech,
  `tts_service.dart`, Feature 10)

---

## 2. Directory / File Map (as uploaded so far)

```
message/
  models/
    message_models.dart        — chat/conversation/poll/pin/scheduled DTOs
    study_room_models.dart     — whiteboard DTOs (strokes/shapes/text/sticky/timer)
  services/
    message_api_service.dart   — ALL chat/group/poll/pin/schedule REST calls
    call_api_service.dart      — call + study-room-join + missed-calls REST calls
    ai_study_service.dart      — AI summary/quiz REST call (study room)
    chat_socket_service.dart   — per-conversation WebSocket (ws/chat/<id>/)
    inbox_socket_service.dart  — global singleton WebSocket (ws/inbox/)
    call_manager.dart          — singleton: 1:1/group CALL lifecycle (LiveKit)
    study_room_call_manager.dart — per-screen instance: Study Room media (LiveKit)
    call_kit_service.dart      — native incoming-call popup (flutter_callkit_incoming)
    push_notification_service.dart — FCM + local notifications, all push routing
    missed_call_watcher.dart   — connectivity-triggered missed-call check
    media_download_service.dart — download to gallery / Downloads folder
    message_cache_service.dart — SharedPreferences cache (conversations + messages)
    doubts_api_service.dart   — 🔥 NAYA (Phase 8): "Doubt Queue" REST client
      (group Q&A: ask/upvote/answer/reveal-anonymous) — see §4.13
    parent_service.dart       — 🔥 NAYA (Phase 8, Feature 8): Parent/Guardian
      Mode REST client — ⚠️ placeholder `_baseUrl`, not wired to `Api.baseUrl`
      yet, see §4.14
    translate_service.dart    — 🔥 NAYA (Phase 8, Feature 9): per-message
      translate REST client — ⚠️ placeholder `_baseUrl`, see §4.15
    tts_service.dart          — 🔥 NAYA (Phase 8, Feature 10): client-only
      text-to-speech for message bubbles (`flutter_tts`, no backend call)
      — see §4.16
  screens/
    conversations_screen.dart      — MAIN chat list (search/pin/rename/select/delete/draft-preview + global message-search entry)
    chat_screen.dart           — the big one: message thread UI (5000+ lines)
    call_screen.dart           — in-call UI (1:1 + group grid, controls)
    incoming_call_screen.dart  — full-screen ringing UI (swipe accept/reject)
    study_room_screen.dart     — whiteboard + video screen (4600+ lines)
    group_profile_screen.dart  — group info/settings/members/roles/invite
    create_group_screen.dart   — new group wizard (pick members, name)
    forward_message_screen.dart — pick chat(s) to forward message(s) into (+ optional caption, poll-exclude)
    media_viewer_screen.dart   — fullscreen swipeable/zoomable image viewer
    app_bottom_nav.dart        — shared bottom nav bar (Home/Search/Chats/Profile)
    message_search_screen.dart — 🔥 NAYA (Phase 4): in-chat + global message search, with filters
    read_receipt_privacy_screen.dart — 🔥 NAYA (Phase 4): mutual read-receipt toggle
    conversations_list_screen.dart — ⚠️ RE-UPLOADED (Phase 5): re-appeared this
      session after being marked deleted in Phase 4 — see §5.1 for status
    class_transcript_screen.dart — 🔥 NAYA (Phase 7): Feature 3, searchable
      timestamped class transcript + per-segment audio playback (§5.13)
    doubts_screen.dart — 🔥 NAYA (Phase 10): Doubt Queue UI, pushed from
      chat_screen.dart's "Doubts" AppBar icon (group chats only, §5.14)
    focus_mode_screen.dart — 🔥 NAYA (Phase 11): Feature 12, smart DND
      during exam/study windows, pushed from conversations_screen.dart's
      bolt icon (§5.1, §5.15) — ⚠️ backend methods missing, build blocker
    group_media_screen.dart — 🔥 NAYA (Phase 11): shared media/links/docs
      gallery, pushed from group_profile_screen.dart's new card (§5.6, §5.16)
    revision_deck_screen.dart — 🔥 NAYA (Phase 12): Feature 5, flashcards +
      quiz generated from a class's chat/whiteboard/transcript content,
      pushed from study_room_screen.dart's AI Tools sheet ("Generate
      Revision Deck") — confirmed wired, see §5.17
    message_info_screen.dart — 🔥 NAYA (Phase 12): WhatsApp-style "seen by"
      / message-info screen, resolves the §17.4 frontend-gap flag — ⚠️ no
      confirmed caller anywhere uploaded yet, and the model/API method it
      needs aren't confirmed present either (see §5.18, §10)
    parent_code_entry_screen.dart — 🔥 NAYA (Phase 12), Feature 8: PARENT-side
      entry point (redeem a code from `parent_service.dart`) — first UI
      confirmed for this feature, see §5.19
    parent_dashboard_screen.dart — 🔥 NAYA (Phase 12), Feature 8: PARENT-side
      read-only attendance/assignment dashboard, pushed from
      `parent_code_entry_screen.dart` — see §5.19
    manage_parent_access_screen.dart — 🔥 NAYA (Phase 12), Feature 8:
      STUDENT-side screen to generate/revoke parent codes — ⚠️ its own
      standalone `http` calls, NOT `parent_service.dart`, own placeholder
      `_baseUrl`, no confirmed entry point yet — see §5.20
    focus_session_history_screen.dart — 🔥 NAYA (Phase 13): Feature 12
      gap fix, past focus-session history list (duration/rule/ended-early
      per row) — ⚠️ its own standalone `http`+`shared_preferences` calls
      (same pattern as `manage_parent_access_screen.dart`), own
      placeholder `_baseUrl`, hits an undocumented endpoint, and has NO
      confirmed entry point anywhere uploaded (not even from
      `focus_mode_screen.dart`, which was re-uploaded this round and
      still has no history icon) — see §5.21, §10
  widgets/
    minimized_call_bar.dart    — minimized-call pill (the current/correct implementation)
    whiteboard_painter.dart    — CustomPainter for strokes/shapes on canvas
    mention_suggestions_overlay.dart — 🔥 NAYA (Phase 4): @mention autocomplete widget + `extractMentionQuery`/`insertMention` helpers
    language_picker_sheet.dart — 🔥 NAYA (Phase 9): Feature 9 language-picker bottom sheet + preferred-lang persistence (§6.4)
    translatable_message_widgets.dart — 🔥 NAYA (Phase 9): `ListenButton` (Feature 10) + `TranslateToggle` (Feature 9) — drop into message bubble (§6.5)
    ~~floating_call_bar.dart~~  — 🗑️ DELETED (confirmed unwired, §6.1)
```

> **Phase 4 update (this session)**: `chat_screen_pagination_fix.dart`
> patch is confirmed already merged into `chat_screen.dart` (404-on-
> out-of-range-page handled, no stray `print()`s) — the standalone
> patch file no longer needs tracking separately. See §12 for the full
> list of this session's changes.

> **Phase 7 update (this session)**: Feature 3 (class transcript) +
> Feature 4 (classroom copilot) implemented. Modified:
> `study_room_models.dart`, `ai_study_service.dart`,
> `study_room_call_manager.dart`, `study_room_screen.dart`,
> `call_api_service.dart` (doc-only). New: `class_transcript_screen.dart`.
> Backend: `models.py` (`ClassTranscriptSegment`), `views_ai.py` (3 new
> views), `tasks.py` (1 new Celery task), `urls.py` (3 new routes). Full
> details in §18.

> **Phase 12 update (this session)**: 15 files re-uploaded (mix of
> re-checks and genuinely new). 5 files are new to this doc:
> `revision_deck_screen.dart`, `message_info_screen.dart`,
> `parent_code_entry_screen.dart`, `parent_dashboard_screen.dart`,
> `manage_parent_access_screen.dart`. The other 10
> (`focus_mode_screen.dart`, `forward_message_screen.dart`,
> `group_media_screen.dart`, `group_profile_screen.dart`,
> `incoming_call_screen.dart`, `media_viewer_screen.dart`,
> `message_search_screen.dart`, `read_receipt_privacy_screen.dart`,
> `study_room_screen.dart`, this doc itself) were spot-checked against
> existing §5.x claims — see §22 for the full changelog.

Outside `message/` (referenced but **still not uploaded** — treat as
external/unknown until shared):
- `../../utils/api.dart` → `Api.baseUrl` (single source of backend base URL)
- `../../services/auth_service.dart` → `AuthService.getToken()` / login/logout
- `../../profile/screens/target_profile.dart`, `../../profile/api_service.dart` (as `ProfileApi`)
- `../../widgets/sticker_picker_sheet.dart`
- `app_bottom_nav.dart` (inside `message/screens/`, imported by
  `conversations_screen.dart` — content not yet reviewed, only its
  existence/usage inferred)

✅ **Shared this session**: `main.dart` (`MaterialApp`, mounts only
`MinimizedCallBar()` — see §6.1/§7.1) and `home.dart` (`HomeScreen`,
`IndexedStack`: Home/Search/Profile — pushes `ConversationsScreen`
separately for the Chats tab — see §5.1).

---

## 3. Data Models

### 3.1 `message_models.dart`
Mirrors Django serializers 1:1 (per file header comment). Key classes:

- **`MessageType`** — string constants: text, image, video, audio,
  file, presentation, location, system, `study_room` (🔥 custom
  invite-card type), `poll`.
- **`UserMini`** — id, username, first/last name, displayName,
  `profilePhoto` (nullable absolute URL).
- **`ConversationSettings`** — isArchived, isMuted, isPinned (per-user).
- **`GroupMini`** — id, name, photoUrl, membersCount (list-view summary).
- **`ConversationModel`** — id, type ('private'|'group'), otherParticipant,
  group, lastMessage*, unreadCount, mySettings, createdAt.
  - `displayTitle` / `displayPhoto` getters unify private-vs-group logic.
- **`MessageReactionModel`**, **`ReplyPreviewModel`**
- **`MessageModel`** — the core message DTO. Has 3 factories:
  - `fromJson` (REST)
  - `fromSocketEvent` (WS `chat_message` event — **different key names**,
    e.g. `sender_id`/`sender_name`/`sender_profile_photo`,
    `message_type` not `type`, `conversation_id` not `conversation`)
  - Local-only fields NOT from server: `isSending`, `sendFailed`,
    `uploadProgress`, `localFilePath`, `localFilePaths` — used for
    optimistic UI / upload progress, stripped from `toJson()` (cache).
- **`PollOptionModel`**, **`PollModel`** — poll data travels inside
  `MessageModel.meta['poll']`, NOT as separate top-level fields. History
  (paginated) messages of type=poll come back WITHOUT poll data — must
  be fetched fresh via `getPoll()` or arrive live via socket.
- **`PinnedMessageModel`** — wraps a MessageModel + who pinned + when.
- **`ScheduledMessageModel`** — future-send messages (draft state incl.
  type/text/fileUrl/meta/replyTo/scheduledFor/isSent/isCancelled).
- **`SmartReplyModel`** — `{suggestions: List<String>}`, backs
  `MessageApiService.getSmartReplies()` (§17.4 — implemented,
  call-site not yet confirmed).
- 🔥 NAYA (Phase 9, not previously documented) — **`DoubtQuestionModel`**
  — backs `doubts_api_service.dart` (§4.13). Fields: id, groupId,
  `author` (nullable `UserMini` — null while anonymous+unrevealed),
  text, isAnonymous, isRevealed, upvotesCount, upvotedByMe, isMine,
  isAnswered, answerText, answeredBy, answeredAt, createdAt.
  `displayName(myUserId)` getter centralizes the "Anonymous" vs "You" vs
  real name display logic (own anonymous doubts always show as "You" to
  the asker; everyone else sees "Anonymous" until `reveal()` is called).
  Has `copyWith` for optimistic local updates (upvote count, answer,
  reveal) without refetching. This closes the §4.13 "⚠️ needs a
  DoubtQuestionModel" gap — it now exists.
- 🔥 NAYA (Phase 14 — `message_models.dart` re-uploaded for the first
  time since Phase 8, genuinely new, not previously documented):
  - **`LinkPreviewModel`** — url, title?, description?, image?. Backs
    link-preview cards for URLs shared in chat.
  - **`SearchFilterModel`** — sender/dateFrom/dateTo/hasMedia/mediaType,
    `isEmpty` getter, `toQueryParams()` (→ `sender`, `date_from`,
    `date_to`, `has_media`, `media_type`), `copyWith` with explicit
    `clearX` flags per field (so a filter can be reset to `null`, not
    just left unchanged) — backs `message_search_screen.dart`'s (§5.11)
    filter UI.
  - **`ConversationPreviewModel`** — id/type/name/photoUrl, a small
    "which chat did this match come from" preview attached to each
    global-search result.
  - **`SearchResultModel`** — wraps a `MessageModel` +
    `conversationPreview` (nullable — only populated for global
    search). `MessageApiService.searchAllMessages()` (§4.1) now returns
    `List<SearchResultModel>`, not `List<MessageModel>`; plain in-chat
    search (`searchMessages()`) still returns `List<MessageModel>`
    since it doesn't need a cross-chat preview.
  - **`FocusSessionStatus`** — active, endsAt, exceptionRule
    ('teachers_only'|'nobody'), secondsRemaining. 🔧 **Confirms the
    Phase 13 relocation** (§23.2): this DTO previously lived in
    `focus_mode_screen.dart`; with `message_models.dart` re-uploaded
    the move is now directly confirmed (not just inferred), and
    `message_api_service.dart`'s new focus-session methods (§4.1) share
    this exact definition — see §10 for the now-fully-resolved build
    blocker. Still carries a `static FocusSessionStatus? inactive() =>
    null` factory (dead — always returns `null`; harmless, not worth a
    fix, just noting it's the same shape as before).

### 3.2 `study_room_models.dart`
- **`ToolType`** enum: marker, paint, eraser, highlighter, rectangle,
  circle, line, arrowLine, text. `isShapeTool()` helper.
- **`DrawingPoint`** — one point of a freehand stroke (offset+paint+tool).
- **`ShapeElement`** — 2-point shapes (rect/circle/line/arrow), has id+userId
  (userId needed for per-user undo).
- **`TextElement`** — floating text (no background box), editable/draggable.
- **`StickyNoteModel`** — text + position + color.
- **`UserProfileWindowModel`** — floating avatar window per participant
  (position/size/zIndex — draggable UI element on the board).
- **`WhiteboardPage`** — ONE page's full state: strokes, shapes, texts,
  stickyNotes, optional `fileUrl`/`fileType` (loaded PDF/image).
  `userStrokeIndices` is LOCAL-ONLY (undo bookkeeping), excluded from
  toJson/fromJson.
- **`StudyTimerState`** — Pomodoro sync: isRunning, isBreak,
  focusMinutes, breakMinutes, `endAt` (absolute timestamp, NOT a
  countdown int — avoids drift across devices/network lag).
- **`TranscriptSegmentModel`** — 🔥 NAYA (Phase 7, Feature 3): one
  transcribed audio chunk (id, speakerName, startOffsetSeconds,
  endOffsetSeconds, text, audioFileUrl). `timeLabel` getter formats
  offset as "m:ss". Offsets are session-relative (NOT wall-clock) — see
  §7.19 for why.
- 🔥 NAYA (Phase 9, not previously documented — closes the §4.3
  "needs a `RevisionDeckModel`" flag) — **`FlashcardModel`** (`front`,
  `back` — no `isFlipped` state here, that's UI-only, screen-side per
  card) and **`RevisionDeckModel`** (`flashcards: List<FlashcardModel>`,
  `quiz: List<Map<String,dynamic>>`, `createdAt`, `isEmpty` getter).
  `quiz` is deliberately kept as raw `Map` (not a typed model) because
  it matches the shape `_QuizQuestionCard` in `study_room_screen.dart`
  already expects from the older `ai_study_service.generate(mode:
  'quiz')` path — reuses that widget instead of forking a parallel quiz
  UI for revision decks. Backs `AiStudyService.generateRevisionDeck()`/
  `getSavedRevisionDeck()` (§4.3).
- 🔥 NAYA (Phase 9, not previously documented — closes the §4.2 "needs
  a `StudyStreakModel`" gap) — **`StudyStreakModel`** — currentStreak,
  longestStreak, totalClassesAttended, `lastAttended` (nullable
  `DateTime`). Backs `CallApiService.getStudyRoomStreak()` (§4.2).

---

## 4. Services Layer

### 4.1 `message_api_service.dart` — REST endpoint catalog
Base: `Api.baseUrl + "/message"` (most), `Api.baseUrl + "/profile"`
(user search, block/unblock — different Django app).
All calls: Bearer token header via `AuthService.getToken()`.
Errors → `MessageApiException(message, statusCode, code)` — `code` is
used for group send-permission errors specifically.

| Area | Method → Endpoint | Notes |
|---|---|---|
| Upload | POST `/message/upload/` (multipart) | via Dio for real progress %. Returns `UploadedFileResult` (fileUrl/type/size/name/mime). |
| Conversations | GET `/message/conversations/` | list |
| | GET `/message/conversations/<id>/` | detail |
| | POST `/message/conversations/start_private/` `{user_id}` | get-or-create 1:1 |
| | PATCH `/message/conversations/<id>/settings/` | mute/archive/pin |
| | PATCH `/message/conversations/<id>/label/` `{label}` | per-user custom chat nickname |
| | GET/PATCH `/message/conversations/<id>/disappearing_messages/` | 'none'\|'1_month'\|'6_months'\|'1_year'; group→admin/mod only (403 else) |
| | POST `/message/conversations/bulk_delete/` `{conversation_ids}` | delete-for-me only |
| | GET `/message/conversations/<id>/` → `my_settings.is_muted` | mute status read |
| Messages | GET `/message/conversations/<id>/messages/?page&page_size` | paginated history |
| | POST `/message/conversations/<id>/messages/` | REST fallback for text + ALL media/location sends (socket `message` event only carries plain text) |
| | POST `/message/conversations/<id>/read_all/` | mark all read |
| | PATCH `/message/messages/<id>/` `{text}` | edit (sender only) |
| | DELETE `/message/messages/<id>/?for_everyone=bool` | delete |
| | POST/DELETE `/message/messages/<id>/react/` `{emoji}` | react/unreact |
| | POST `/message/messages/<id>/read/` | mark one read |
| | GET `/message/messages/<id>/read-status/` | 🔥 NAYA (Phase 14, `getReadStatus`) — "seen by" list, → `List<MessageReadStatusModel>`. ⚠️ **`MessageReadStatusModel` is NOT defined anywhere in the re-uploaded `message_models.dart`** — genuine undefined-symbol build blocker for `message_info_screen.dart` (§5.18), see §10 |
| | POST `/message/messages/forward/` `{message_ids, conversation_ids, caption?}` | forward N msgs → N chats in one call. 🔧 **Phase 14**: `caption` param confirmed present now (`forward_message_screen.dart` needs it — see §10, previously an unconfirmed/likely mismatch risk, now resolved) |
| Pins | GET `/message/conversations/<id>/pinned/` | list pins (🔧 Phase 6: corrected, was documented as `/pins/`) |
| | POST/DELETE `/message/messages/<id>/pin/` | pin/unpin — message-level action, NOT under `conversations/<id>/`; max 3 pinned/conversation (backend-enforced) |
| Polls | POST `/message/conversations/<id>/poll/` | create. 🔧 **Phase 14 correction**: singular `/poll/`, NOT `/polls/` as previously documented — corrected against the re-uploaded `message_api_service.dart` |
| | ~~GET `/message/polls/<id>/`~~ | 🔧 **Phase 14: removed from the code, not just the doc.** File's own comment: this standalone-by-poll-id endpoint never existed backend-side; `getPoll(pollId)` has been deleted entirely. Poll data now only arrives via the message payload (`MessageSerializer.poll`) or the `poll_update` socket event — see §7.5, §8.1 |
| | POST `/message/messages/<id>/poll/vote/` `{option_ids}` | 🔧 **Phase 14 correction**: keyed by **message id**, not poll id (`votePoll(messageId, optionIds)` — param renamed from `pollId`). Re-vote = auto-switch |
| | POST `/message/messages/<id>/poll/close/` | 🔧 **Phase 14 correction**: message id, not poll id. Creator/admin/mod only |
| Scheduled | GET/POST `/message/conversations/<id>/scheduled/` | list / create (future time validated) |
| | PATCH `/message/scheduled/<id>/` | reschedule |
| | DELETE `/message/scheduled/<id>/` | cancel (soft delete) |
| Wallpaper | GET/PATCH `/message/conversations/<id>/wallpaper/` `{wallpaper_url}` | per-user, whole-screen bg |
| User search | GET `/profile/chat-search/?search=` | only mutual-follow users; for add-members |
| Groups | POST `/message/groups/` `{name,description,photo_url,is_private,member_ids}` | create |
| | GET/PATCH `/message/groups/<id>/` | detail / update (admin/mod) |
| | POST `/message/groups/<id>/members/` `{user_ids}` | add (admin/mod) |
| | PATCH `/message/groups/<id>/members/<user_id>/` | role/mute/ban |
| | DELETE `/message/groups/<id>/members/<user_id>/` | remove/leave |
| | DELETE `/message/groups/<id>/photo/` | remove photo (admin/mod) |
| | DELETE `/message/groups/<id>/` | delete whole group (ADMIN ONLY) |
| | GET `/message/groups/<id>/media/` | shared media list |
| | POST `/message/groups/join/` `{invite_code}` | public→'joined', private→'pending' |
| | GET `/message/groups/<id>/join-requests/` (admin/mod) | pending list |
| | POST `/message/groups/<id>/join-requests/<req_id>/approve\|reject/` | |
| Blocking | GET/POST/DELETE `/profile/blocked-users/` | block/unblock/list — `profile` app, not `message` |
| Presence | GET `/message/users/<user_id>/presence/` | online/last-seen |
| Calls | GET `/message/calls/history/` , GET `/message/calls/history/<id>/` | history/detail (🔧 Phase 6: corrected, was missing `history/` segment — see §17.1). NOTE: initiate/action live in `call_api_service.dart` at the bare `/calls/` prefix (different, plain-path views — that part was already correct); missed/addable-participants/add-participant also live in `call_api_service.dart`, also under `calls/history/` |
| Search | GET `/message/conversations/<id>/search/` | in-chat search → `List<MessageModel>` |
| | GET `/message/conversations/search_all/` | global search (🔧 Phase 6: corrected, was `/message/search_all/` — see §17.1). 🔧 **Phase 14**: return type confirmed as `List<SearchResultModel>` (message + optional `ConversationPreviewModel`), not `List<MessageModel>` — see §3.1 |
| Participants | POST `/message/conversations/<id>/participants/` `{user_id}` | add to existing conversation |
| Study room state | PUT/GET/DELETE `/message/study-room/<id>/state/` `{pages}` | persist/restore/clear whiteboard pages. 🔧 **Phase 14 correction**: path confirmed as `/message/study-room/<id>/state/`, NOT `/message/conversations/<id>/study-room-state/` as previously documented (`saveStudyRoomState`/`getStudyRoomState`/`endStudyRoomState`) |
| Focus session | GET/POST/DELETE `/message/focus-session/` | 🔥 NAYA (Phase 14, confirmed present — closes the Phase 11 build blocker, see §10): `getFocusStatus()` (GET, `{"active":false}` → `null`), `startFocusSession({durationMinutes, exceptionRule})` (POST `{duration_minutes, exception_rule}`), `cancelFocusSession()` (DELETE). Return type `FocusSessionStatus` (§3.1) |

> 🔥 **Phase 5 finding**: `getOrCreateConversation(targetUserId)` at the
> bottom of the file hits the exact same endpoint as `startPrivateChat`
> (`POST /message/conversations/start_private/`), just with its own
> inline `http.post` instead of reusing `_headers()`/`_decode()`, and
> throws a plain `Exception` instead of `MessageApiException`. Looks
> like a leftover/duplicate — no call-site for it was found in any file
> uploaded so far. Flagged in §10; don't assume which one is "the" one
> to call without checking where each is actually used.

### 4.2 `call_api_service.dart` — call + study-room-join REST
🔧 **FIX (Phase 8, this session)**: the separate hardcoded LAN-IP
`baseUrl` constant is GONE — `CallApiService.baseUrl` is now a getter
returning `Api.baseUrl`, same single source of truth as every other
service. `ai_study_service.dart` still reuses `CallApiService.baseUrl`
(now correctly the shared one, not a stale duplicate).

- `initiateCall(conversationId, type)` → POST `/message/calls/initiate/`
- `callAction(callId, action)` → POST `/message/calls/<id>/action/`
  (`action`: accept/reject/end). `end` never throws to caller (returns
  `{"status":"ended"}` on failure) — call teardown must never crash.
- `getCallStatus(callId)` → GET `/message/calls/history/<id>/` (🔧 Phase 6
  fix, `history/` segment confirmed present in current code — was
  404ing before, see §17.1) — polled every 2s by caller while ringing
  (§8.10 reject-detection)
- `getAddableParticipants(callId)` → GET
  `/message/calls/history/<id>/addable-participants/` (same `history/`
  fix confirmed in code)
- `addParticipant(callId, userId)` → POST
  `/message/calls/history/<id>/add-participant/` (same fix confirmed)
- `getMissedCalls({since})` → GET `/message/calls/history/missed/?since=`
  (same fix confirmed) — best-effort, swallows errors, returns `[]`
- 🔥 NAYA (Feature 6, not previously documented) —
  `getStudyRoomStreak(conversationId)` → GET
  `/message/study-room/<conversationId>/streak/` → `StudyStreakModel?`
  (`currentStreak`, `longestStreak`, `totalClassesAttended`,
  `lastAttended`). Best-effort like `getMissedCalls` — returns `null` on
  any error instead of throwing, so a streak badge failing to load never
  blocks the study room from opening. Backend logs one attendance row per
  (conversation, user, calendar day) inside `StudyRoomJoinView.post()`;
  this just reads it back. No confirmed UI call-site yet in any uploaded
  screen — flagged like §17.4's other unwired-but-implemented endpoints.
- `joinStudyRoom(conversationId, {newSession})` → POST
  `/message/study-room/<conversationId>/join/` `{new_session: bool}` —
  returns `{livekit_url, livekit_token}`. No ringing/accept — silent
  connect like a Meet link.
  - 🔥 NAYA (Phase 7, Feature 3 dependency): response ab `session_id` bhi
    return karna hoga (backend `StudyRoomJoinView` — is session me
    upload nahi hui, isliye ye sirf CONTRACT assumption hai, backend dev
    ko confirm/add karna hai). Suggestion: `CallSession.channel_name`
    hi reuse karo (§7 me already documented hai ki channel_name "doubles
    as join key for both calls and study rooms") — naya scheme na banao.
    `StudyRoomScreen` isi id ko `StudyRoomCallManager.
    startTranscriptRecording(sessionId: ...)` me pass karta hai. Agar
    field missing aaye, transcript recording silently skip ho jaati hai
    (rest of study room normally kaam karta hai) — see §7.19.

### 4.3 `ai_study_service.dart`
- `generate({mode, content})` → POST `/message/study-room/ai-tools/`
  `{mode: 'summary'|'quiz', content}` → `{summary}` or `{questions:[...]}`.
  Backend calls the actual AI provider — app never holds an AI API key.
- 🔥 NAYA (Phase 7, Feature 3) — `registerTranscriptChunk({conversationId,
  sessionId, audioFileUrl, startOffset, endOffset})` → POST
  `/message/study-room/<conversationId>/transcript-chunk/`. Fire-and-
  forget from caller's perspective — transcription happens async
  (Celery), this just registers the chunk.
- 🔥 NAYA (Phase 7, Feature 3) — `searchTranscript({conversationId,
  sessionId, query})` → GET
  `/message/study-room/<conversationId>/transcript/?session_id=&q=` →
  `List<TranscriptSegmentModel>`. `sessionId` omitted = backend returns
  the most recent session for that conversation.
- 🔥 NAYA (Phase 7, Feature 4) — `askClassroomCopilot({conversationId,
  question, boardContent})` → POST `/message/ai/classroom-copilot/`
  `{conversation_id, question, board_content}` → `{answer}`. Backend
  assembles context from recent chat + whiteboard + matching transcript
  segments before calling the AI provider — see §7.16.
- 🔥 NAYA (Phase 8, Feature 5 — "Revision Deck", not previously
  documented) — `generateRevisionDeck({conversationId, boardContent,
  sessionId})` → POST `/message/study-room/<conversationId>/revision-deck/`
  `{board_content, session_id?}` → `RevisionDeckModel` (`flashcards:
  [{front, back}]`, `quiz: [{question, options, answer}]`, `createdAt`).
  Combines chat + whiteboard + transcript server-side (same inputs as
  the copilot). Throttled 10/min/user server-side
  (`RevisionDeckThrottle`) — surfaces as an `Exception` with a Hinglish
  message on 429.
  - `getSavedRevisionDeck(conversationId)` → GET same URL → last
    generated deck WITHOUT re-hitting the AI provider — lets "open Study
    Room → revise what I already made" skip a fresh generate. Empty
    `flashcards`/`quiz` means none exist yet.
  - Both need `RevisionDeckModel` in `study_room_models.dart` — 🔧
    **confirmed present (Phase 9)**: `RevisionDeckModel` +
    `FlashcardModel` now exist there, see §3.2. Flag resolved, no longer
    a build blocker.
  - 🔧 **Screen confirmed present (Phase 12)**: `revision_deck_screen.dart`
    calls both methods and is wired in from `study_room_screen.dart`'s
    AI Tools sheet — Feature 5 is no longer "service+model layer only",
    it's fully end-to-end. See §5.17.
- 🔥 NAYA (Phase 8, `transcribe()`, not previously documented — this is
  the §17.4-flagged `POST /message/ai/transcribe/` endpoint, now
  confirmed to have a frontend method) — `transcribe({fileUrl,
  mimeType})` → POST `/message/ai/transcribe/` `{file_url, mime_type}` →
  transcript string. Explicitly a **manual fallback button**, not the
  primary path — the backend already auto-transcribes every voice note
  in the background (§7.19); this exists for when that auto-transcript
  never arrived (feature was off at send-time, or a voice note predates
  the feature). `mimeType` defaults via `_guessAudioMimeType` (app
  records `.m4a`/AAC-LC via `record` package → `audio/mp4`). Throttled
  15/min/user server-side (`AiTranscribeThrottle`) — 429/503 get
  specific Hinglish error messages. Still **no confirmed UI call-site**
  in any uploaded screen (the flag from §17.4 stands — a "Transcribe"
  button presumably lives in `chat_screen.dart`'s `_AudioBubble`, not
  yet verified).

### 4.4 `chat_socket_service.dart` — per-conversation WebSocket
Connects: `ws(s)://<host>/ws/chat/<conversation_id>/?token=<JWT>`
(derived from `Api.baseUrl`). One instance per open `ChatScreen`,
**and** per open `StudyRoomScreen` (study room events are a passthrough
on the same socket, not a separate connection), **and** (🔥 NAYA, Phase
10) per open `DoubtsScreen` (§5.14) — so up to **three** concurrent
`ChatSocketService` connections to the same conversation's WS room can
exist at once (chat thread + study room + doubts board, all open on top
of each other). Each is its own independent `ChatSocketService()`
instance with its own reconnect state (§4.4 below) — none of them share
a socket object, they just all point at the same backend room.

Outgoing (client→server) methods:
- `sendMessage({text, messageType, clientId, replyTo, fileUrl, fileUrls,
  thumbnailUrl, meta})` → `{type:'message', ...}` — 🔧 **FIX (Phase 4)**:
  the four trailing params are new; previously only plain text could go
  over the socket and media had to fall back to REST. Backend
  `ChatConsumer.handle_new_message`/`save_message` now reads these too
  — still upload via Dio first to get a `fileUrl`/`fileUrls`, then send.
- `sendPin(messageId, bool pin)` → `{type:'pin', message_id, pin}` — 🔥
  **NAYA (Phase 4)**: optional socket-side pin/unpin, mirrors the REST
  call; server broadcasts `pin_event` either way.
- `sendTyping(bool)` → `{type:'typing', is_typing}`
- `sendReadReceipt(messageId)` → `{type:'read', message_id}`
- `sendDelete(messageId, {forEveryone})` → `{type:'delete', ...}`
- `sendReaction(messageId, emoji)` → `{type:'reaction', ...}`
- `sendStudyRoomEvent(action, data)` → `{type:'study_room_event', action, data}`
  — generic passthrough for ALL whiteboard/timer/quick-chat/sticker events

Incoming: `events` broadcast Stream of raw `{type, ...}` maps — screens
have a big switch on `type` (see §9).

✅ **Phase 4**: the stray `print()` debug logging (connect/send/receive/
onDone/onError, all marked "hata dena baad me") has been removed —
socket layer is now clean of debug noise.

🔥 **NAYA (Phase 8, this session — real behaviour change, not just
docs)**: `chat_socket_service.dart` previously had **no reconnect logic
at all** — `onDone`/`onError` just flipped `_isConnected = false` and
stopped; nothing ever called `connect()` again, so a token expiry or an
ordinary network blip left the chat socket dead until the screen was
reopened. Now matches `inbox_socket_service.dart`'s pattern:
- `connect(conversationId)` stores the id and resets reconnect state;
  actual connecting happens in a private `_connectInternal()` so it can
  be re-invoked.
- Before every (re)connect attempt, pulls a token via
  `AuthService.getValidToken()` (expiry-check + auto-refresh) instead of
  a possibly-stale cached token. `null` (refresh failed / not logged in)
  → schedules a retry rather than connecting with a bad token.
- `onDone`/`onError` both call `_scheduleReconnect()` unless
  `disconnect()` was called explicitly (`_manuallyDisconnected` flag) —
  so **explicit hangup/screen-close never triggers a reconnect**, only
  unexpected drops do.
- Backoff: `2 * attempts` seconds, clamped to **2s–30s** (not fixed-
  interval) — resets to 0 on a successful connect. Prevents a tight
  infinite retry loop against a persistently-down server or a
  permanently-invalid refresh token.
- `disconnect()` (call from `ChatScreen`/`StudyRoomScreen` `dispose()`)
  sets `_manuallyDisconnected = true` and cancels the reconnect timer —
  same as before, still mandatory to call or the loop keeps running
  after the screen closes.

### 4.5 `inbox_socket_service.dart` — GLOBAL singleton WebSocket
Connects: `ws(s)://<host>/ws/inbox/?token=<JWT>`. Singleton
(`InboxSocketService.instance`), idempotent `connect()`. Meant to be
alive for the WHOLE app session (ideally connected right after login),
currently connected lazily from `ConversationsScreen.initState()`.
Purpose: lets the chat LIST screen know about new messages/updates in
ANY conversation without having that specific `ChatScreen` open. Event
shape: `inbox_update` (consumed by `ConversationsScreen._onInboxUpdate`).

🔧 **FIX (Phase 8, this session)**: reconnect was previously **fixed 4s**
— if the token itself was expired, the server would 4001-close
immediately, `onDone` would fire, the same expired token would be
reused 4s later, 4001 again → a silent, forever tight loop with no
user-visible error. Now:
- Uses `AuthService.getValidToken()` (expiry-check + auto-refresh)
  instead of a raw `getToken()`, same as `chat_socket_service.dart`.
- Backoff is now `4 * attempts` seconds, clamped to **4s–60s**, reset to
  0 on successful connect — no more tight infinite loop on persistent
  failure (server down, or a refresh-token that's itself invalid, in
  which case `AuthService` will have already fired `onForceLogout`
  separately).

### 4.6 `call_manager.dart` — SINGLETON, 1:1/Group CALL lifecycle
`CallManager.instance` (extends `ChangeNotifier`). Owns the LiveKit
`Room` for a phone-style call independent of which screen is on top
(so `FloatingCallBar`/`MinimizedCallBar` can show it from anywhere).

Key state groups:
- Identity: callId, conversationId, isVideo, isCaller, peerName/Avatar
- UI/lifecycle: isActive, isMinimized
- LiveKit: room, remoteVideoTrack, localVideoTrack, remoteScreenTrack,
  localScreenTrack
- **Group call tiles**: `remoteTiles` (Map<identity, RemoteParticipant>),
  `remoteTileVideoTracks` — `isGroupCall` = tiles.length > 1. Old
  singular fields (`remoteVideoTrack`/`remoteConnected`) kept working
  for 1:1 UI (always reflect the FIRST remote participant).
- Controls: muted, videoOff, speakerOn, isFrontCamera
- **Hold**: `onHold`/`peerOnHold` — simulated (LiveKit has no native
  SIP-hold): mutes own mic/cam (remembering prior state) + locally
  disables remote audio tracks + sends `{'type':'call_hold', hold}`
  data-channel message so peer's UI can show "On hold".
- **Local video filters**: `VideoFilterType` enum + `localSoftBlur` —
  render-only (ImageFiltered/ColorFiltered) on THIS device; the track
  sent to peer is always unprocessed. (LiveKit Flutter SDK has no
  native background-segmentation — noted as upstream limitation.)
- **Call waiting**: `waitingCallId/...` — if a call comes in while
  already `isActive`, does NOT push `IncomingCallScreen`; instead shows
  a banner in `CallScreen`. Accepting it means: end current call → start
  new one (NOT true hold-and-switch, documented as a deliberate
  simplification vs WhatsApp).
- Timers: `_callTimer` (elapsed duration + drives
  `PushNotificationService.showOngoingCallNotification`),
  `_noAnswerTimer` (30s auto-end if callee never answers),
  `_callStatusPollTimer` (2s poll — see reject-detection below),
  `_reconnectTimer` (grace period on unexpected drop).

Entry point: `startCallIfNeeded({callId, conversationId, isVideo,
isCaller, livekitUrl, livekitToken, peerName, peerAvatar})` — if
already on this exact call, just unminimize; if on a DIFFERENT call,
cleans up old one first.

Room event wiring (`_initCall`):
- `TrackSubscribedEvent`/`TrackUnsubscribedEvent` → populate
  video/audio track fields + tile maps, flips `remoteConnected=true`,
  starts call timer, stops ringtone
- `ParticipantConnectedEvent` → adds to `remoteTiles`; **this is also
  the caller's "answered" moment** — caller's mic is deliberately
  enabled ONLY here (`_micPendingForCaller` flag), not at room-connect
  time, because enabling mic earlier switches Android audio mode to
  `MODE_IN_COMMUNICATION` and silently ducks the outgoing ringtone (a
  separate audio stream) — **documented root-cause fix**.
- `ParticipantDisconnectedEvent` → removes from tile maps; only treats
  the WHOLE call as disconnected if `remoteTiles` becomes empty (so one
  person leaving a group call ≠ call over)
- `RoomDisconnectedEvent` → **authoritative** signal, cleans up
  immediately (no reconnect-wait) — this was itself a fix (previously
  waited on `isReconnecting` even for explicit call-end)
- `DataReceivedEvent` → decodes `{'type':'call_hold'|'call_end', ...}`
  data-channel messages (used to get instant peer-hangup / hold-state
  without waiting for LiveKit's own disconnect grace period)

**Documented root-cause bugs & fixes** (important context, kept
verbatim intent):
1. *"Call properly cut nahi hoti"* — receiving side used to wait
   through a 2s reconnect grace period even on explicit hangup. Fixed
   by the caller/end-er explicitly publishing `{'type':'call_end'}`
   over the data channel BEFORE calling backend `end`, so the other
   side reacts instantly (`_peerEndedCall` flag suppresses the
   redundant reconnect-countdown).
2. *"Receiver reject kare to bhi caller ringing dikhta rehta"* — a
   rejected call never joins the LiveKit room at all, so no RoomEvent
   ever fires for the caller. Fixed via `_startCallStatusPoll()`:
   caller polls `getCallStatus()` every 2s while ringing; on
   rejected/declined/busy status → `_endAsLineBusy()` (shows "Line
   busy" ~1.6s then cleans up), independent of the 30s no-answer
   fallback.
3. Battery-optimization system popup was removed: `_enableBackgroundExecution()`
   (foreground service) is now called ONLY around screen-share, not for
   the whole call — tradeoff explicitly documented: aggressive-OEM
   phones (Xiaomi/Oppo/Vivo) MAY suspend mic if app is backgrounded
   during a normal call.

`toggleScreenShare()`, `switchCamera()`, `addParticipant(userId)` (group
call invite — reuses the normal incoming-call push flow, no special
handling needed once accepted), `endCall()` / `_cleanup()`.

### 4.7 `study_room_call_manager.dart` — PER-INSTANCE (not singleton)
Each `StudyRoomScreen` creates its own `StudyRoomCallManager()` and
must call `leaveRoom()` in `dispose()`. Fundamentally different from
`CallManager`: **no ringing/accept/reject** — `joinRoom({livekitUrl,
livekitToken})` connects immediately like opening a Meet link.

- Camera/mic **auto-ON on join** (changed from an earlier Meet-style
  "default off" — explicit product decision documented in comments) —
  OS permission popup appears once, then user can toggle off manually.
- `remoteVideoTracks`/`remoteMicOn` keyed by LiveKit identity (multi
  participant, no 1:1 special-casing needed).
- **Screen share / "present"**: any participant can share; drawing on
  top of the shared screen is handled by the whiteboard layer itself
  (same page/strokes), this manager only tracks the video track.
  `activePresentationTrack`/`activePresenterId` getters (local-first,
  then first remote presenter). Android needs a foreground service
  during capture (`flutter_background`); iOS needs a native Broadcast
  Extension target (not covered by Dart code — separate Xcode work
  flagged as still-needed).
- `onParticipantLeft` callback lets `StudyRoomScreen` remove that
  user's floating profile window when they disconnect.
- Explicit `_requestMicPermission()`/`_requestCameraPermission()` were
  a **fix** — previously study room skipped explicit
  `permission_handler` requests (worked in `CallScreen` but silently
  failed on some OEMs in study room because LiveKit's own
  auto-permission-prompt isn't reliable everywhere).
- 🔥 NAYA (Phase 7, Feature 3) — `startTranscriptRecording({conversationId,
  sessionId})` / `stopTranscriptRecording()`. Chunked LOCAL mic recording
  (`record` package, same encoder as chat voice-notes) — NOT full LiveKit
  room recording/egress (that server-side infra doesn't exist in this
  stack, see §7.19 for the full design rationale). Rotates a chunk every
  45s (`_transcriptChunkDuration`), uploads via `MessageApiService.
  uploadFile` then registers via `AiStudyService.registerTranscriptChunk`.
  Offsets tracked relative to `_transcriptSessionStart`, not wall-clock.
  Started right after `joinRoom()` succeeds (from `StudyRoomScreen.
  _joinStudyRoomMedia`), stopped automatically inside `leaveRoom()` —
  caller doesn't need to remember to stop it separately.
  Mic-permission failure here is silent (feature skip), never blocks
  joining the room.

### 4.8 `call_kit_service.dart` — native incoming-call UI
Wraps `flutter_callkit_incoming` (**hard pin to 2.5.0+2** — required
for Android 14+ full-screen-intent APIs; older resolved versions
silently lack `canUseFullScreenIntent()`/`requestFullIntentPermission()`).
Requires uninstall+reinstall after upgrading (Android notification
channels are immutable once created — channel names are versioned,
currently "Incoming Calls v4"/"Missed Calls v4", bump the suffix if
channel settings ever need to change again).

- `init(navigatorKey)` — requests notification permission, checks/
  requests full-screen-intent permission (`_ensureFullScreenIntentPermission`,
  opens system Settings if not granted — cannot be silently granted by
  code, Android policy), subscribes to `FlutterCallkitIncoming.onEvent`.
- `showIncomingCall(data)` — static, called from FCM background handler
  AND would be redundant if called in foreground (foreground uses the
  Flutter `IncomingCallScreen` instead — see push service).
- `_onCallKitEvent` → `actionCallAccept` → `_acceptAndNavigate` (calls
  backend accept, then pushes `CallScreen` via the global navigatorKey,
  with a bounded retry-wait `_waitForNavigator()` for cold-start races);
  `actionCallDecline`/`actionCallTimeout` → backend reject;
  `actionCallEnded` → backend end.
- `endCallUiByCallId(callId)` — force-dismiss the native popup when the
  call is cancelled/ended from the other side (used by
  `firebaseBackgroundHandler` on `type: 'call_cancelled'` push).

### 4.9 `push_notification_service.dart` — all push/notification routing
Central hub. Background isolate entry: `firebaseBackgroundHandler`
(`@pragma('vm:entry-point')`) — **must** call
`WidgetsFlutterBinding.ensureInitialized()` + `Firebase.initializeApp()`
first, or every plugin call (shared_preferences etc.) hangs forever
silently (**documented root-cause fix** for "reply from notification
never sends").

⚠️ **Backend contract**: FCM payload must be **data-only** (`data: {...}`),
never include a top-level `notification` key — if it does, Android
shows its own default notification and SKIPS invoking the background
handler until the user taps, breaking CallKit/reply/ringtone in
background/killed state.

Push `data.type` routing (background handler):
- `incoming_call` → `CallKitService.showIncomingCall(data)`
- `call_cancelled` → `CallKitService.endCallUiByCallId(callId)`
- `reaction` → `_showBackgroundReactionNotification`
- `mention` → 🔥 **NAYA (Phase 4)** `_showBackgroundMentionNotification`
  — own `mentions` channel (Importance.max); backend sends this push
  bypassing normal chat-mute suppression, so it must stand out even in
  a muted chat
- `chat_digest` → 🔥 **NAYA (Phase 4)** `_showBackgroundDigestNotification`
  — backend's pre-formatted "X sent N messages" text shown as-is, fixed
  per-conversation notification `id` (re-digest updates in place instead
  of stacking), no per-message Reply action (it's a batch summary, not
  one message)
- anything else (chat message) → `_showBackgroundChatNotification`
  (has its own Reply action + own `FlutterLocalNotificationsPlugin`
  instance since it's a separate isolate)

Both new types reuse the SAME generic tap-handler
(`_handleNotificationResponse`) as everything else — it only ever reads
`data.conversation_id` from the payload, so no special-case tap logic
was needed; tapping either just opens the conversation (no jump-to-
message for `chat_digest`, since a digest has no single `message_id`).

`init()` (foreground):
- Requests permissions, creates Android channels: `chat_messages`,
  `downloads`, `reactions`, `ongoing_call` (Importance.low, no sound/
  vibration — updates every second via chronometer, would spam as
  heads-up otherwise), `mentions` (🔥 NAYA Phase 4, Importance.max)
- `FirebaseMessaging.onMessage` (foreground) routing:
  - `incoming_call` + `CallManager.instance.isActive` → **call
    waiting** path: `CallManager.setWaitingCall(...)`, no screen push
  - `incoming_call` (no active call) → pushes Flutter
    `IncomingCallScreen` directly via global navigatorKey — **native
    CallKit is intentionally NOT invoked here** to avoid a double
    popup (native reserved for background/killed only)
  - `call_cancelled` → dismiss CallKit UI + clear waiting-call state or
    pop `IncomingCallScreen`
  - `reaction` → `_showReactionNotification`
  - `mention` → 🔥 **NAYA (Phase 4)** `_showMentionNotification` —
    jaan-bujh kar `currentOpenConversationId` suppression check NAHI
    lagaya; a mention should show even if that chat happens to be open
  - `chat_digest` → 🔥 **NAYA (Phase 4)** `_showDigestNotification`, but
    THIS one DOES still respect the `conversation_id ==
    currentOpenConversationId` suppression (already looking at that chat
    → digest is redundant)
  - chat message where `conversation_id == currentOpenConversationId`
    → suppressed (already looking at it)
  - else → `_showLocalNotification`
- `onMessageOpenedApp` / `getInitialMessage()` → `onNotificationTap`
  callback (app-level wiring, presumably to `Navigator` in main.dart —
  not yet shared)
- **Root-cause fix documented**: `registerToken()` used to run ONLY on
  `onTokenRefresh`, which Firebase may not fire for months — so a
  device's FCM token could never reach the backend after first
  install/login. Now called explicitly once in `init()` too.
- Wires `MissedCallWatcher.instance.onMissedCallTap` to the same
  `onNotificationTap` and calls `MissedCallWatcher.instance.start()`.

Other public methods: `showOngoingCallNotification({peerName,
connectedAt, callId, isVideo})` (chronometer-based, fixed id → updates
in place, no duplicate spam), `cancelOngoingCallNotification()`,
`showDownloadCompleteNotification(...)`, `registerToken()`/
`unregisterToken()` → POST/DELETE `/message/devices/register/`.

### 4.10 `missed_call_watcher.dart`
Singleton. Listens to `connectivity_plus` transitions. On
**offline→online** transition specifically: calls
`CallApiService.getMissedCalls(since: <last-known-online timestamp>)`
and fires a local notification per missed call (channel `missed_calls`,
tap → `onMissedCallTap(conversationId)`). Persists "last online"
timestamp in SharedPreferences (`missed_call_watcher_last_online_at`)
so it survives app kill/restart. Started from
`PushNotificationService.init()` — no separate call-site needed.

### 4.11 `media_download_service.dart`
- Android storage strategy: SDK ≥30 needs `MANAGE_EXTERNAL_STORAGE`
  (same reason WhatsApp asks for "All files access") to write to a
  public `Download/LearnScroll` folder; SDK <30 uses legacy
  `Permission.storage`. Falls back to app-sandboxed
  `ApplicationDocumentsDirectory/Downloads` if permission denied (so
  download never outright fails, just isn't visible in Files app).
- Images/videos → device gallery via `Gal` (album "LearnScroll").
- Other file types (pdf/doc/audio/presentation) → public Downloads
  folder; `alreadyDownloadedPath(fileName)` does a **deterministic path
  check** (no DB/network call) to decide "Open" vs "Download" state —
  only reliable for non-media (image/video don't have a fixed
  app-accessible path, so `ChatScreen` tracks their downloaded-state
  itself in-session).
- `download({url, kind, fileName, onProgress})` uses Dio for progress.
- `openFile(path)` via `OpenFilex`.

### 4.12 `message_cache_service.dart`
SharedPreferences-backed, best-effort (never throws to caller).
- Conversations: last 30 only, overwritten on every fresh fetch; when
  a conversation falls out of the cached-30, its cached messages are
  also purged (`clearMessagesForConversations`) to avoid orphan data.
- Messages: last 50 per conversation, **7-day TTL** (auto-expires and
  returns empty past that — stale chat data must never surface).
- Pattern used by screens: show cache instantly → fetch network in
  background → overwrite UI + re-save cache. Network failure with
  cache already shown ⇒ don't show an error.
- `clearAll()` for a settings "Clear cache" button (not yet wired to
  any uploaded screen). Logout doesn't need a separate cache-clear
  call since `AuthService.logout()` presumably does `prefs.clear()`
  (per comment — `auth_service.dart` not yet shared).

---

## 5. Screens Layer

### 4.13 🔥 NAYA (Phase 8) `doubts_api_service.dart` — "Doubt Queue" REST
Group Q&A feature: students post doubts (optionally anonymous), peers
upvote, teacher/admin/mod answers and can reveal who asked an anonymous
one. Backend: `DoubtQuestionViewSet`, `urls.py` under
`/message/groups/<group_id>/doubts/...`.

Deliberately a **standalone** service (own `_headers()`/`_u()`
boilerplate, same `Api.baseUrl` + `AuthService.getValidToken()` pattern
as `chat_socket_service.dart`) rather than added to
`message_api_service.dart` — per its own header comment, that was a
practical choice because `message_api_service.dart` wasn't available to
edit directly in the session that created this file. **If/when
`message_api_service.dart` is touched again, consider folding these
methods in** to avoid two REST client patterns for what's really one
feature area — endpoints/shapes would stay identical, just the HTTP
boilerplate would move.

- `getGroup(groupId)` → GET `/message/groups/<id>/` — reuses the
  existing group-detail endpoint (no new backend route); read for
  `allow_anonymous_doubts` + role (teacher/admin/mod) to drive UI
  (Ask-Anonymously checkbox, Answer/Reveal buttons)
- `getDoubts(groupId, {status, pageUrl})` → GET
  `/message/groups/<id>/doubts/?status=` (`status`: null/`answered`/
  `unanswered`) → paginated `(doubts, nextPage)`. Server-ordered
  `-upvotes_count, -created_at` — client must NOT re-sort.
- `createDoubt(groupId, {text, isAnonymous})` → POST
  `/message/groups/<id>/doubts/` `{text, is_anonymous}`
- `upvote(groupId, doubtId)` / `removeUpvote(groupId, doubtId)` → POST /
  DELETE `/message/groups/<id>/doubts/<id>/upvote/`
- `answer(groupId, doubtId, answerText)` → POST
  `/message/groups/<id>/doubts/<id>/answer/` `{answer_text}` —
  teacher/admin/mod only, 403 for others
- `reveal(groupId, doubtId)` → POST
  `/message/groups/<id>/doubts/<id>/reveal/` — teacher/admin/mod only,
  reveals who asked an anonymous doubt

⚠️ Needs a `DoubtQuestionModel` in `message_models.dart` — 🔧 **confirmed
present (Phase 9)**: `DoubtQuestionModel` now exists there (id, groupId,
author, text, isAnonymous, isRevealed, upvotesCount, upvotedByMe,
isMine, isAnswered, answerText, answeredBy, answeredAt, createdAt,
`displayName()` helper, `copyWith`) — see §3.1. Flag resolved. Still no
screen for this feature uploaded yet — service+model layer only so far,
same "implemented, not wired to a screen" status as §17.4's list.

### 4.14 🔥 NAYA (Phase 8, Feature 8) `parent_service.dart` — Parent/Guardian Mode
Lets a parent redeem a code shared by the student to view a read-only
dashboard (per-classroom attendance streak + assignment pending/
submitted counts). Singleton (`ParentService.instance`).

⚠️ **Not wired to the app's real networking yet** — `_baseUrl` is a
literal placeholder (`'https://YOUR_API_HOST/message'`), by the file's
own header comment, because no `api_client.dart`/base-URL constant was
available when this file was written. **Must be pointed at `Api.baseUrl`
before this can work.**

Storage: intentionally its OWN SharedPreferences keys (`parent_token`,
`parent_student_name`, `parent_label`) — never touches `access_token`,
so a device can hold a normal student login AND a parent session
simultaneously (e.g. a parent who's also a student viewing a sibling's
progress) without either overwriting the other.

- `verifyCode(code)` → POST `/parent/verify/` `{code}` (uppercased,
  trimmed) → stores `parent_token`/`student_name`/`label` locally.
  404 → invalid/expired code; 429 → rate-limited (Hinglish messages)
- `hasActiveSession()` → local check, `parent_token` non-empty
- `fetchDashboard()` → GET `/parent/dashboard/` header `X-Parent-Token`
  → `ParentDashboard{studentName, classrooms: [{groupName,
  attendance:{currentStreak, longestStreak, totalClassesAttended,
  lastAttended}, assignments:{pending, submitted, total}}]}`. 401/403 →
  auto `signOut()` + throws "access revoked" (student must issue a new
  code)
- `cachedStudentName()` — local read
- `signOut()` — clears all 3 parent-mode keys

No screen uploaded for this yet — service-layer only.

- 🔧 **Screens confirmed present (Phase 12)**: `parent_code_entry_screen.dart`
  (calls `verifyCode`) and `parent_dashboard_screen.dart` (calls
  `fetchDashboard`/`signOut`) are the first confirmed UI for this
  service — call shapes match exactly what's documented above. Feature 8
  is no longer service-layer-only on the PARENT side. See §5.19.
  ⚠️ Still no confirmed entry point INTO `parent_code_entry_screen.dart`
  — its own header comment says to add a button from `LoginScreen`
  (not uploaded), so it's reachable in code but not yet reachable by a
  user tapping through the app.
- ⚠️ **New, separate STUDENT-side screen (Phase 12)**:
  `manage_parent_access_screen.dart` generates/lists/revokes the codes
  that `verifyCode` above redeems — but it does NOT call
  `parent_service.dart`. It has its own inline `http` calls, its own
  placeholder `_baseUrl = 'https://YOUR_API_HOST/message'`, and hits
  `/parent/codes/` (GET list, POST generate, DELETE revoke) with the
  student's normal `access_token` — a different auth story from
  `parent_service.dart`'s separate `parent_token`. These endpoints
  (`/parent/codes/`) aren't documented anywhere else in this doc and
  weren't in the backend contract cross-check (§17) — flag for backend
  confirmation. See §5.20, §10.

### 4.15 🔥 NAYA (Phase 8, Feature 9) `translate_service.dart` — message translate
Singleton (`TranslateService.instance`). Per-message on-demand
translate, with an **in-memory session cache** keyed
`<messageId>:<targetLang>` purely to skip repeat network round-trips
when a user toggles a translated bubble off/on — the backend also
caches server-side (keyed off `updated_at`, per the file's comment), so
this is a pure UX optimization, not the source of correctness.

⚠️ Same wiring gap as `parent_service.dart` — placeholder
`_baseUrl = 'https://YOUR_API_HOST/message'`, needs pointing at
`Api.baseUrl`. Auth header built inline (`access_token` from
SharedPreferences) rather than via `AuthService.getValidToken()` — worth
aligning with the rest of the app's pattern when this gets wired up.

- `translate({messageId, targetLang})` → POST
  `/message/messages/<messageId>/translate/` `{target_lang}` →
  `TranslationResult{sourceText, translatedText, targetLang}`. 503 →
  "not available right now"; 429 → rate-limited (Hinglish messages)
- `invalidate(messageId)` — drops all cached target-langs for that
  message; call this if the message gets edited on this device (the
  cache key doesn't itself know about edits)

No screen call-site uploaded yet — service-layer only.

### 4.16 🔥 NAYA (Phase 8, Feature 10) `tts_service.dart` — text-to-speech
Client-only, **no backend call at all** — wraps the device's own system
TTS engine via `flutter_tts`. Singleton (`TtsService.instance`).

- One active "speaking" message at a time — starting a new `speak()`
  stops whatever was already playing (matches how the app's own voice-
  note bubble behaves).
- `currentlySpeakingId` — `ValueNotifier<String?>`, the message id
  currently being read aloud (or `null`). Bubble UI listens to this to
  show pause-icon on exactly the one speaking bubble, play-icon
  elsewhere.
- `speak(messageId, text, {languageCode})` — tapping the SAME bubble
  again while it's speaking = stop/pause toggle (checks
  `currentlySpeakingId.value == messageId` first). `languageCode`
  (e.g. `'hi-IN'`) is best-effort — if the device lacks that voice,
  `setLanguage` failure is swallowed and playback falls back to the
  device default rather than blocking.
- `stop()` — stops + resets `currentlySpeakingId` to `null`.
- Completion/cancel/error handlers all reset `currentlySpeakingId` to
  `null` so the UI never gets stuck showing a pause icon on a bubble
  that's no longer actually playing.

No screen call-site uploaded yet — service-layer only, but this one
needs none (client-side feature, just needs a button wired in
`chat_screen.dart`'s message bubble).

### 5.1 ⚠️ RE-OPENED (Phase 5): `conversations_list_screen.dart` re-uploaded
Phase 4 had marked this file **deleted** (dead code, confirmed via
`home.dart`/`main.dart` routing — see the resolution reasoning below,
still valid as far as routing goes). This session the file was
re-uploaded as part of the current file set, contradicting that
"deleted" status. Nothing else in this session's uploads (`home.dart`/
`main.dart` were NOT re-shared this session, so routing can't be
re-confirmed either way) references `ConversationsListScreen` by name.

**Current call**: treated as present-but-unwired-until-confirmed. Not
re-marked as the live screen — `conversations_screen.dart` is still the
one every other file's imports/pushes point at (`create_group_screen.dart`,
`chat_screen.dart` navigation, `app_bottom_nav.dart`'s only known
consumer, etc.). This is flagged in §10 as something to confirm with
the user rather than silently re-deleting or silently re-adopting it.

Original Phase 4 resolution (kept for history): confirmed via
`home.dart` (imports + `Navigator.push` both pointed ONLY at
`ConversationsScreen`, line 29/159) and `main.dart` (no reference to
either screen — routing lived entirely in `home.dart`).
`conversations_list_screen.dart` was an earlier/simpler iteration with
delete-only multi-select, no search, no pin/rename, no inbox socket.

`conversations_screen.dart` (the sole survivor) has: user search
(start new chat), long-press multi-select (delete/pin/rename), local
pin/label overrides, `mySettings.draftText` → "Draft: ..." preview per
row, connects to `InboxSocketService`, has `AppBottomNav`, and (🔥
**NAYA, Phase 4**) a separate "Search messages" icon in the app bar →
pushes `MessageSearchScreen()` (global mode) — kept as a DISTINCT icon
from the existing "search users to start a chat" one, so the two don't
get confused.

Pushes `ChatScreen(conversation: ...)` on tap, `CreateGroupScreen` from
the "new group" action.

🔥 NAYA (Phase 11, Feature 12 — not previously documented) — **Focus
Mode wiring** lives entirely in this screen, not in
`focus_mode_screen.dart` itself (§5.15):
- `initState()` calls `_loadFocusStatus()` →
  `MessageApiService.getFocusStatus()` on every screen open (so app
  kill/restart still shows the correct banner state, not a stale
  client-only guess).
- While active, a `Timer.periodic(30s)` (`_focusTicker`) just triggers a
  rebuild every 30s to refresh the displayed "Xh Ym left" text and
  auto-clears `_focusStatus` locally once `endsAt` has passed — this is
  a CLIENT-SIDE optimistic expiry only, not a re-fetch from the server,
  so if the backend's own expiry logic differs even slightly the banner
  could disagree with server truth for one 30s tick.
- AppBar bolt icon + an inline banner row (tap either → same
  `_openFocusModeScreen()` → pushes `FocusModeScreen(current:
  _focusStatus)` → awaits the popped `FocusSessionStatus?` → stores it
  directly as the new `_focusStatus`, restarts the ticker). All actual
  start/stop network calls happen inside `FocusModeScreen`, never here
  — this screen only reflects whatever comes back.
- ⚠️ Same missing-backend-methods gap as §5.15: `getFocusStatus` isn't
  in the currently-uploaded `message_api_service.dart` either — this
  screen's `_loadFocusStatus()` call-site has the identical build-blocker
  problem, not just `FocusModeScreen`'s start/stop calls.

### 5.2 `chat_screen.dart` (~5000 lines) — main thread UI
State: `_ChatScreenState` (huge — owns `_messages`, `_socket`
(`ChatSocketService` instance), pagination (`_currentPage`,
`_hasMoreMessages`, `_isLoadingMore` — see pagination fix below),
reply/selection-mode, group role/permissions, mute/block/disappearing/
wallpaper toggles, recording state, etc.

Responsibilities (from method scan):
- Init: `_init()` → `_loadHistory()` (page 1, REST) → cache-then-network
  pattern via `MessageCacheService` (implied) → `_connectSocket()`
  (`ChatSocketService.connect(conversation.id)` then
  `.events.listen(_handleSocketEvent)`)
- `_loadMoreMessages()` — infinite-scroll-up pagination; **see
  `chat_screen_pagination_fix.dart`** patch (§6 below)
- Big switch in `_handleSocketEvent` on event `type`: `chat_message`,
  `typing`, `read`, `delete`, `reaction`, `poll_created`, `poll_voted`,
  `pin_event` (🔧 **doc self-correction, Phase 10**: this paragraph
  previously still said `message_pinned`/`message_unpinned` even though
  §8.1 had already corrected the actual event name to `pin_event` in an
  earlier pass — the code was never wrong, only this bullet's wording
  was stale; now matches §8.1), `conversation_wallpaper_updated`,
  `presence`, `disappearing_messages_updated`, `group_deleted`,
  `call_event`/`incoming_call`, `error`
- Sending: `_sendMessage()` (text, optimistic insert + socket if
  connected else REST fallback), `_pickAndSendAttachment(...)`,
  `_uploadAndSendFile(...)`, `_uploadAndSendMultipleImages(...)`,
  `_sendSticker(...)`, `_startRecording()/_stopRecordingAndSend()/
  _cancelRecording()` (voice notes via `record` package),
  `_sendLocation()` (via `geolocator`)
- Reactions: `_toggleReaction` (optimistic local update + `socket.
  sendReaction`)
- Message actions: `_showMessageActions` → edit/delete
  (`_deleteMessage`, sends both REST delete AND `socket.sendDelete`),
  `_pinMessage`/`_unpinMessage`, forward
  (`_forwardOne`/`_forwardSelected` → pushes
  `ForwardMessageScreen(messageIds: [...])`), multi-select mode
- Polls: `_showCreatePollSheet`, `_votePoll`, `_insertPollMessage`,
  handles `poll_created`/`poll_voted` socket events
- Scheduled messages: `_showScheduleMessageSheet`,
  `_showManageScheduledSheet`
- Calls: `_startCall(type)` → `CallApiService.initiateCall()` → push
  `CallScreen(isCaller: true, ...)`; `_handleCallEvent` for
  socket-delivered call signaling on this screen
- **Study Room integration** (§8.13):
  - `_openStudyRoom()` — icon tap: sends an invite CARD message (type
    `study_room`) via `_sendStudyRoomInvite()`, THEN
    `_enterStudyRoom(startNewSession: true)` (fresh session)
  - `_enterStudyRoom({startNewSession=false})` — pushes
    `StudyRoomScreen(conversationId, currentUserId, peerName,
    peerAvatar, startNewSession)`; tapping a received/sent invite CARD
    calls this same method with `startNewSession: false` (joins the
    existing session instead of starting a new one)
  - `initialParticipants` is passed as `const []` with a `// TODO` to
    actually map group participants → `UserProfileWindowModel` list
- Group management shortcuts from chat: mute toggle, block toggle,
  disappearing-messages sheet, access-control sheet (message/call/
  study-room permission + daily limit — group only), join-requests
  sheet, leave/delete group, `_openGroupProfile()` → pushes
  `GroupProfileScreen`
- 🔥 NAYA (Phase 10, not previously documented) — **"Doubts" tab entry
  point**: an AppBar icon (`Icons.help_outline_rounded`, tooltip
  "Doubts") that only renders `if (widget.conversation.isGroup)` — the
  Doubt Queue is a classroom/group concept, no equivalent for 1:1 chats.
  Tap → `Navigator.push(DoubtsScreen(groupId: conversation.group!.id,
  conversationId: conversation.id, groupName: conversation.group!.name))`.
  Guarded with an early `return` if `group == null` (defensive — should
  be unreachable given the `isGroup` gate, but avoids a null-deref if
  that ever drifts). See §5.14 for the pushed screen.
- Wallpaper: `_loadWallpaper`/`_pickChatWallpaper`/`_removeChatWallpaper`
- Downloads: `_scanAlreadyDownloaded()`, `_downloadMedia`/
  `_downloadMediaUrl` (uses `MediaDownloadService`)
- Filters: text/media/docs/links/all message-type filter sheet
- Navigation to profile: `_goToProfile(username)` → decodes JWT
  (dart:convert) presumably for own-vs-other check, pushes
  `TargetProfile` (own profile → routes into `HomeScreen`'s Profile tab
  instead)
- Internal helper widgets defined in the same file: `_LinkifiedText`
  (clickable links in message text), `_SwipeToReply`, `_DateSeparator`,
  `_TypingBubble`, `_ChatWallpaperPainter`, `_MessageBubble` (the big
  one, renders every message type), `_PollBubbleContent`,
  `_MediaPreviewScreen` (pre-send preview, multi-image reorder/remove
  via `_removeAt`/`_addMore`/`_send`), `_AudioBubble` (inline voice-note
  player), `_VideoPlayerScreen` (fullscreen video w/ landscape rotation,
  double-tap-seek, controls auto-hide)

⚠️ **Still not wired (Phase 9/10 gap, unchanged as of Phase 13)**:
`translatable_message_widgets.dart`'s `ListenButton`/`TranslateToggle`
(§6.5) are NOT called anywhere in `_MessageBubble` or any other part of
this file — a `grep` for both class names across every uploaded file
turns up nothing outside their own definition file. Features 9
(translate) and 10 (TTS) therefore remain fully built (service,
model-free since both use plain result classes, and standalone widgets)
but **not user-reachable** — the natural integration point is inside
`_MessageBubble`'s per-message action row (next to the reaction/reply/
star icons), per that widget file's own header comment, but that edit
hasn't happened yet in any uploaded version of `chat_screen.dart`.

✅ **RESOLVED (Phase 13)**: §10/§22.3 previously flagged
`message_info_screen.dart` (§5.18) as having "no confirmed caller
anywhere uploaded". This re-upload of `chat_screen.dart` closes that —
`_showMessageActions`'s per-message bottom sheet now has an "Info"
`ListTile` (`Icons.info_outline_rounded`), shown **only when `isMe`**
(you can't see who read someone else's message), which pushes
`MessageInfoScreen(messageId, messagePreview: msg.text)`. The screen's
own dependency on `MessageApiService.getReadStatus`/
`MessageReadStatusModel` is still unconfirmed from the service/model
side — `message_api_service.dart`/`message_models.dart` weren't
re-uploaded this round either — so treat as "caller confirmed, backend
contract still unconfirmed" (§10).

File also grew from ~5000 to 5676 lines this round; beyond the Info
entry above, the rest of the growth wasn't diffed line-by-line this
pass.

### 5.3 `call_screen.dart` — active call UI
`CallScreen(callId, conversationId, isVideo, isCaller, livekitUrl,
livekitToken, peerName?, peerAvatar?)`. Listens to `CallManager.instance`
(ChangeNotifier). Own local UI-only state: PiP offset (`_pipOffset`,
`_mainIsLocal`), pulsing-ring animation while ringing, outgoing-ring +
call-waiting-tone `AudioPlayer`s (separate from `CallManager`'s own
ringtone player — this one seems to duplicate the ring, worth
reconciling), controls-auto-hide-after-3s + tap-to-show, own 35s
`_noAnswerTimer` (⚠️ **note**: `CallManager` ALSO has a 30s
`_noAnswerTimer` internally — two independent timers with different
durations may both be running; worth reconciling later, don't assume
they're the same one). Has an inner `_AddParticipantSheet` widget
(group-call add-participant picker, calls
`CallApiService.getAddableParticipants`/`addParticipant`).

### 5.4 `incoming_call_screen.dart` — ringing/incoming UI
`IncomingCallScreen.showIfNeeded(navigatorState, {callId, callType,
callerName, callerAvatar, conversationId})` — static entry used by
`push_notification_service.dart` (foreground path). Swipe-to-
accept/reject gesture (`_onDragUpdate`/`_onDragEnd`/`_springBack`/
`_completeSwipe`), haptic feedback, own ringtone via `AudioPlayer`
(`_startRinging`/`_stopRinging`). `_accept()` → `CallApiService.
callAction(callId,'accept')` → get livekit creds → push `CallScreen`.
`_reject()` → `CallApiService.callAction(callId,'reject')`.

### 5.5 `study_room_screen.dart` (~4600 lines) — whiteboard + video
`StudyRoomScreen({conversationId, currentUserId, initialParticipants,
peerName?, peerAvatar?, startNewSession})`.

Owns: `ChatSocketService _socket` (own connection, same
`conversation_id` channel as `ChatScreen` — realtime whiteboard events
ride the SAME websocket used for chat, via the generic `study_room_event`
passthrough), `StudyRoomCallManager` instance (media), AND listens to
the global `CallManager.instance` too (`_onCallManagerChanged` —
likely to coordinate/avoid conflict if a normal 1:1 call is also active).

Local model classes defined in-file: `_BoardAction` (undo stack entry),
`_RoomChatMessage` (in-room quick-chat bubble), `_StickerEvent`
(floating emoji reaction).

Key flows (method scan):
- Join: `_connectSocket()` → `_joinStudyRoomMedia()`
  (`CallApiService.joinStudyRoom(conversationId, newSession:
  startNewSession)` → `StudyRoomCallManager.joinRoom(livekitUrl,
  livekitToken)`), `_announceSelfJoined()`/`_applyServerParticipants()`
  /`_ensureSelfWindow()` — floating profile windows setup,
  `_announceParticipantJoined(name)` (toast), `_onRemoteParticipantLeft`
  (removes their floating window)
- Realtime board sync — `_handleRoomEvent` switch on `action` (all
  arrive wrapped from `ChatSocketService`'s `study_room_event` passthrough,
  see §9): `draw_point`, `undo_user_stroke`, `clear_board`,
  `clear_board_keep_text`, `undo_user_shape`, `undo_user_text`,
  `undo_user_shape` (dup?), `undo_user_sticky`, `add_sticky_note`,
  `update_window` (floating window drag sync), `ruled_lines` (notebook
  paper toggle+style), `user_joined`, `add_shape`, `add_text`,
  `add_page`, `remove_page`, `load_page_file` (PDF/image someone loaded,
  broadcast so all participants can download+render it),
  `presentation_started`/`presentation_stopped` (screen-share
  page-switch), `timer_update` (Pomodoro sync), `quick_chat` (in-room
  chat bubble), `sticker` (floating emoji)
- Sending own actions: `_sendRoomEvent(action, data)` →
  `_socket.sendStudyRoomEvent(action, data)` — single funnel used by
  ALL the drawing/toolbar handlers
- Drawing input: `_onPanStart`/`_onPanUpdate`/`_onPanEnd` (freehand +
  shape-drag-preview via `previewShape` in `WhiteboardPainter`),
  `_onCanvasTapForText` (place text element), `_editTextElement`
- Undo: `_recordMyAction(type, refId)` (local action-log per user) +
  `_undoLastAction()` + `_undoUserLastStroke` — **per-user undo**
  (can't undo someone else's stroke), server just relays
  `undo_user_*` events so everyone's canvas stays in sync
- Pages: `_addPage`/`_removeCurrentPage`/
  `_switchToPresentationPage(createIfMissing)`
- File loading: `_pickAndLoadFile()`/`_captureFromCamera()`→
  `_loadPickedOrCapturedFile(File)` → if PDF: `_loadPdfAsPages(File)`
  (via `syncfusion_flutter_pdfviewer` + `printing`/`pdf` to rasterize
  each page as its own whiteboard page) → uploads via
  `MessageApiService.uploadFile` presumably, then broadcasts
  `load_page_file` so others fetch+render without re-uploading
  (`_downloadAndCachePageFile`)
- Export/download: `_exportAndDownloadAnnotatedFile()` (current page +
  annotations flattened), `_exportAllPagesAsPdf()` (multi-page PDF via
  `pw` widgets), `_downloadOriginalFile()` — all show
  `PushNotificationService` download-complete notifications like
  `ChatScreen` does
- Persistence: `_startAutoSave()` (periodic), `_saveBoardState()` /
  `_restoreBoardState()` → `MessageApiService.saveStudyRoomState`/
  `getStudyRoomState` (PUT/GET `.../study-room-state/`)
- Session lifecycle: `_leaveSession()` (this user only),
  `_endSessionForEveryone()` (broadcasts session-end, presumably
  `DELETE .../study-room-state/` via `endStudyRoomState`),
  `_handleSessionEndedByRemote()`, `_leaveRoomAfterSessionEnd(showMessage)`
- Timer: `_restartTimerTicker`/`_tickTimer`/`_broadcastTimer`
  (`timer_update`)/`_toggleTimer`/`_resetTimer`/`_configureTimer`
- Room call controls: `_startRoomCall(type)`, `_toggleScreenShare()`,
  `_switchCamera()`, `_shareRoomLink()`, `_showAddUserDialog()`→`_addUser(userId)`
- AI tools: `_openAiToolsSheet()`→`_runAiGeneration(mode)`
  (`AiStudyService.generate`) →`_showAiResultSheet(mode, result)`;
  `_saveTextAsStickyNote(text)` (turn AI summary bullet into a sticky
  note on the board)
  - 🔥 NAYA (Phase 7, Feature 4): `_openAiToolsSheet()` ab ek teesra
    option deta hai — "Ask About This Class" → `_openClassroomCopilotSheet()`
    → `_runClassroomCopilot(question)` (`AiStudyService.
    askClassroomCopilot`, `boardContent: _collectBoardTextContent()`) →
    `_showClassroomCopilotAnswerSheet(question, answer)`.
  - 🔥 NAYA (Phase 7, Feature 3): naya circle-button icon
    (`Icons.subtitles_outlined`, "Class Transcript") next to the "Ask AI"
    button → `_openClassTranscriptScreen()` → pushes
    `ClassTranscriptScreen(conversationId)` (§5.13).
  - 🔥 NAYA (Phase 7, Feature 3): `_joinStudyRoomMedia()` ab join ke
    turant baad, agar backend response me `session_id` mile,
    `_roomCall.startTranscriptRecording(conversationId, sessionId)` bhi
    call karta hai (fire-and-forget, `unawaited`) — see §7.19.
- Misc UI: `_addStickyNote`, `_openColorAndSizePicker`,
  `_openStickerPicker`, `_sendQuickChat`, `_sendSticker(emoji)` +
  `_scheduleStickerRemoval(id)` (floating emoji auto-fade), ruled-paper
  toggle/style (`_toggleRuledLines`/`_setRuledLineStyle`/
  `_showRuledLineStyleSheet`), toolbar menu switch (`share`, `add_user`,
  `attach`, `camera`, `download`, `download_all_pdf`,
  `download_original`, `timer`, `ruled_lines`, `ruled_lines_style`,
  `leave_session`, `end_session`)

Also defines: `_RuledLineStyle`/`_RuledPaperPainter` (notebook-lines
background), `_StickerBubble`, `_JoinToast`, `_QuizQuestionCard` (AI
quiz UI).

### 5.6 `group_profile_screen.dart` (~1400+ lines)
`GroupProfileScreen(groupId)`. Loads via `getGroup`, then all edits are
individual `updateGroup(groupId, {field: value})` PATCH calls:
name, description, `is_private` (togglePrivacy), `message_permission`,
`daily_message_limit`, `call_permission`, `study_room_permission`,
photo (`_changeGroupPhoto`→upload then patch `photo_url`,
`_removeGroupPhoto`). Also: invite-link copy (`_copyInviteLink`),
add-members flow (`_openAddMembers` → `_AddMembersSheet` inner widget →
`searchUsers` + `addGroupMembers`), per-member actions
(`_showMemberActions` switch: make_admin/demote_admin/make_mod/
demote_mod/remove/ban → `updateGroupMember`/`removeGroupMember`),
join-requests approve/reject, `_leaveGroup`/`_deleteGroup`.

🔥 NAYA (Phase 11, not previously documented) — two more settings/entry
points, both closing gaps flagged in earlier phases:
- **`allow_anonymous_doubts` toggle** (`_updateAllowAnonymousDoubts`) —
  same `updateGroup(groupId, {'allow_anonymous_doubts': value})` PATCH
  pattern as every other permission toggle here, optimistic-update +
  rollback-on-error. This IS the "Permissions card" that
  `doubts_screen.dart`'s own header comment (§5.14) pointed at without
  it being confirmed/documented — confirmed now. It's the single
  source of truth `DoubtsScreen` reads back via `getGroup()` to decide
  whether to show the anonymous-ask checkbox at all.
- **"Media, links and docs" card** → `Navigator.push(GroupMediaScreen(
  groupId: widget.groupId))` — see §5.16. Per this file's own comment,
  the backend endpoint and `MessageApiService.getGroupMedia` already
  existed with no screen calling them; this card is what finally
  surfaces that data.

### 5.7 `create_group_screen.dart`
Simple wizard: user search (`searchUsers`) + multi-select + name input
→ `_createGroup()` → `MessageApiService.createGroup(...)`.

### 5.8 `forward_message_screen.dart`
`ForwardMessageScreen(messages: [...])` — 🔧 **note (Phase 4)**: takes
full `MessageModel` objects now, not just `messageIds` (needed to know
each selected message's `type` for the caption-UI logic below; this was
already the case when reviewed this session, no change made). Loads
conversations, local search filter, multi-select target chats.

- Optional caption `TextField`, shown only when `_captionApplicable` is
  true (at least one selected message is non-text — media/location/etc.
  — since backend doesn't overwrite a text message's own text with a
  caption, showing the field for all-text selections would be confusing)
- `_send()` defensively excludes any `MessageType.poll` from the
  forwarded ids (backend silently drops polls anyway; this is just
  belt-and-suspenders in case the caller didn't already block poll
  selection)
- `MessageApiService.forwardMessages(messageIds, conversationIds,
  caption)`, pops `true` on success (caller shows confirmation snackbar)

### 5.9 `media_viewer_screen.dart`
`MediaViewerScreen(urls, initialIndex, onDownload?, isDownloaded?)`.
Fullscreen swipeable (`PageView`) + pinch-zoom (`InteractiveViewer`
per-page, own `TransformationController` per index) + **double-tap
zoom** (zooms toward tap point via matrix translate+scale, or resets if
already zoomed) + auto-hiding top bar/thumbnail-strip (3s timer,
zoom>1 hides immediately, tap toggles). Thumbnail strip
auto-centers/scrolls to current index.

### 5.10 `app_bottom_nav.dart`
Shared 4-tab bar: Home/Search/Chats/Profile. `AppTab` enum
{home, search, chats, profile}. Tapping Home/Search/Profile does
`pushAndRemoveUntil(HomeScreen(initialIndex: N))` (HomeScreen's own
`IndexedStack` only has 3 tabs — Chats is a separately-pushed screen,
mapped: index2→home(0), index3→home(2)). Used by `conversations_screen.dart`
(only *confirmed* consumer — see §5.1 for the Phase 5 caveat about
`conversations_list_screen.dart` re-appearing).

### 5.11 🔥 NAYA (Phase 4) `message_search_screen.dart`
`MessageSearchScreen({conversationId})` — one screen, two modes:
- `conversationId` given → in-chat search
  (`MessageApiService.searchMessages`); tapping a result pops the
  screen with just the `message.id` — `ChatScreen` (already wired,
  pre-existing) does the actual scroll+highlight via
  `jumpToMessageId`/`_tryJumpToMessageId`.
- `conversationId` null → global search across all conversations
  (`MessageApiService.searchAllMessages`); each result carries a
  `conversation_preview`. Tapping fetches the full `ConversationModel`
  via `getConversation(preview.id)` then `pushReplacement`s straight
  into `ChatScreen(conversation, jumpToMessageId: result.message.id)`.

Debounced (400ms) client-side search, 2-char minimum before firing
(matches backend's 400-on-short-query behaviour). Filters (`sender`,
`dateFrom`/`dateTo`, `mediaType`, `hasMedia`) live in a bottom sheet
(`_SearchFiltersSheet`) — sender field is a live autocomplete against
`MessageApiService.searchUsers`. Entry points: `conversations_screen.dart`
app bar icon (global mode) and `chat_screen.dart`'s own search icon
(in-chat mode, pre-existing wiring — this file was the missing piece).

### 5.12 🔥 NAYA (Phase 4) `read_receipt_privacy_screen.dart`
Standalone settings screen — one `Switch` bound to
`MessageApiService.getReadReceiptSetting()`/`setReadReceiptSetting()`.
Optimistic toggle with rollback on failure. Explicitly calls out (in
the UI copy) that this is a **mutual** setting — turning it off hides
your read receipts from others AND hides others' read receipts from
you, same wording as the backend's own contract.

⚠️ **Not yet wired to any entry point** — no other screen currently
navigates to it (by design; the doc flagged this same gap before it was
built). Needs a link from wherever makes sense product-wise: a real
Settings/Privacy screen (not yet uploaded), `group_profile_screen.dart`,
or a `chat_screen.dart` 3-dot menu item.

### 5.13 🔥 NAYA (Phase 7) `class_transcript_screen.dart` — Feature 3
`ClassTranscriptScreen({conversationId, sessionId?})` — pushed from
`study_room_screen.dart`'s new transcript icon (§5.5).

Debounced (400ms) search box → `AiStudyService.searchTranscript(
conversationId, sessionId, query)`. `sessionId` omitted from the push
call currently (`_openClassTranscriptScreen()` only passes
`conversationId`) → backend defaults to the conversation's most recent
session, which is the right behaviour for "revise the last class"; a
session-picker (for revising an OLDER class, not just the latest) is
NOT built yet — flagged in §10.

Each result row = one `TranscriptSegmentModel`: `timeLabel` badge
(mm:ss), speaker name, transcribed text, tap-to-play via `audioplayers`
(`AudioPlayer.play(UrlSource(segment.audioFileUrl))`). ⚠️ **Important**:
tapping a result plays THAT SEGMENT'S own short audio chunk, not a seek
into one continuous class recording — see §7.19 for why (no LiveKit
egress in this stack). Empty-state copy explicitly tells the user
transcripts appear progressively in the background, not instantly.

### 5.14 🔥 NAYA (Phase 10) `doubts_screen.dart` — Doubt Queue UI
`DoubtsScreen({groupId, conversationId, groupName})` — pushed from
`chat_screen.dart`'s new AppBar "Doubts" icon (§5.2, group chats only).
First and only screen for the Doubt Queue feature — closes the "service
+ model only, no UI" gap that §4.13/§20.2 had been tracking.

**Realtime, not just REST-on-open**: `initState()` both loads the
initial list via `DoubtsApiService.getDoubts()` AND opens its OWN
`ChatSocketService()` instance on the SAME `conversationId` the parent
`ChatScreen` is already connected to — this is a **third concurrent
socket connection** to that conversation's `ws/chat/<id>/` room
whenever a Doubts screen is open on top of a chat (first is
`ChatScreen`, second is `StudyRoomScreen` if also open, per §4.4's
existing note — update that note to say "up to three", not "two").
Listens for a new server→client WS event, `doubt_event`, whose
payload wraps a full `{doubt: {...DoubtQuestionSerializer shape...}}` —
add this to §8.1's event table. On receipt: upsert-or-remove from the
in-memory `_doubts` list depending on whether the incoming doubt still
matches the active status filter (e.g. a doubt just answered while the
"Unanswered" filter is active gets removed from view, not just updated
in place), then a stable re-sort (upvotes desc, then newest-first) —
mirrors the backend's own `Meta.ordering` so REST-loaded and
socket-updated positions never disagree.

**Role detection**: no separate "am I admin" endpoint — reuses
`DoubtsApiService.getGroup(groupId)` (existing `GroupSerializer`
detail), scans `members` client-side for the entry whose
`user.id == myUserId` (from `AuthService.getUserId()`), reads its
`role`. `role == 'admin' || role == 'moderator'` → `_isTeacher = true`,
which gates both the "Answer" button and the "Reveal who asked" action
on anonymous doubts. `allow_anonymous_doubts` (same group-detail
response) gates whether the ask-sheet shows an anonymous checkbox at
all.

**UI structure**:
- 3-way `ChoiceChip` filter row (All/Unanswered/Answered) —
  `_changeFilter()` triggers a full `_load()` (fresh REST page 1), not a
  local filter of already-loaded data, so switching filters always
  reflects current server state.
- Infinite-scroll pagination (`_loadMore()` at 200px from bottom,
  cursor-based via the `nextPage` URL `DoubtsApiService.getDoubts`
  already returns — no page-number math client-side, unlike
  `chat_screen.dart`'s message pagination).
- Each `_buildDoubtCard`: upvote pill (arrow + count, tinted when
  `upvotedByMe`) with **optimistic update + rollback-on-error**
  (`_toggleUpvote` flips the local model immediately via `copyWith`,
  calls the REST upvote/remove-upvote, reverts to the pre-optimistic
  copy on any exception) — same pattern as `chat_screen.dart`'s
  reaction toggle. Author line uses `DoubtQuestionModel.displayName()`
  (§3.1) so the "You"/"Anonymous"/real-name logic lives in one place,
  not duplicated in this screen. "Answered" chip + inline answer block
  when answered; "Answer" button (teacher-only, opens an inline
  text-entry flow — not detailed further here) when not.
- `FloatingActionButton.extended` ("Ask a doubt") → a bottom-sheet ask
  form (text + optional anonymous checkbox, gated by
  `_allowAnonymousDoubts`) → `DoubtsApiService.createDoubt()` → the
  created doubt is inserted locally immediately (not waiting for the
  `doubt_event` echo) if it matches the current filter — avoids a
  visible round-trip delay for the asker's own doubt.

No pull-down attendance/streak or other cross-feature wiring here —
this screen is scoped purely to the Doubt Queue.

### 5.15 🔥 NAYA (Phase 11) `focus_mode_screen.dart` — Feature 12
`FocusModeScreen({current: FocusSessionStatus?})` — pushed from
`conversations_screen.dart`'s AppBar bolt icon (`_openFocusModeScreen`,
tooltip "Focus mode"; icon fills/tints `_kAnnouncement` orange when a
session is active). "Smart do-not-disturb during exam/study windows" —
student picks a duration + who can still reach them, backend presumably
suppresses push notifications for everyone else during the window.

- **`FocusSessionStatus`** DTO — 🔧 **MOVED (Phase 13)**: this file's
  own header comment now says the class lives in `message_models.dart`,
  not in this file anymore. Stated reason: `message_api_service.dart`'s
  `getFocusStatus()`/`startFocusSession()` need the same return type,
  and `message_api_service.dart` ↔ `focus_mode_screen.dart` can't import
  each other (circular import), so the DTO moved to the shared models
  file where every other DTO (`ConversationModel` etc.) already lives —
  pattern-consistent per the file's comment. Fields as before: `active`,
  `endsAt` (parsed `.toLocal()`), `exceptionRule`
  (`'teachers_only'` | `'nobody'`), `secondsRemaining`. The previously-
  flagged dead `static inactive()` helper (§10) is **no longer present**
  in this file — either removed or moved without it; not confirmed which
  since `message_models.dart` itself wasn't re-uploaded this round.
  ⚠️ This is a one-sided claim: `message_api_service.dart` and
  `message_models.dart` were NOT re-uploaded in this batch either, so
  the move can't be independently verified from the other side — treat
  as "screen-side half confirmed" only, same caution as the build-blocker
  note below.
- Duration UI: 4 presets (30m/1h/2h/3h, coaching-context durations —
  "one period / two periods / a full exam block" per the file's own
  comment) via `ChoiceChip`s, plus a "Custom duration" bottom sheet
  (hour/minute steppers, minutes step by 15, clamped 5–480 min).
- Exception rule: 2 radio tiles — "Only teachers & staff" (admin/mod
  messages+calls still come through) vs "Nobody — full silence" (exam
  mode, not even teachers).
- `_start()` → `MessageApiService.startFocusSession(durationMinutes,
  exceptionRule)` → pops with the returned `FocusSessionStatus`.
  `_stop()` → `MessageApiService.cancelFocusSession()` → pops with
  `null`. Caller (`conversations_screen.dart`) does nothing but store
  whatever comes back as its own `_focusStatus` — all the actual
  start/stop API work happens inside this screen, not the caller.
- When `current?.active == true`: shows a live-ish "Xh Ym left" banner
  (computed once per rebuild from `endsAt.difference(DateTime.now())`,
  not its own ticking timer inside this screen — the ticking countdown
  lives in the CALLER, see §5.1 update below) and an "Update duration"
  label instead of "Start", plus a red "End focus mode now" button.

⚠️ **Backend/frontend contract gap, partially addressed (Phase 13)**:
Phase 11 flagged `MessageApiService.getFocusStatus`/`startFocusSession`/
`cancelFocusSession` as missing from `message_api_service.dart`. This
file no longer carries the "need to be added" header note it used to —
the `FocusSessionStatus` DTO relocation above (moved specifically so
`message_api_service.dart` could share the type) is indirect evidence
those methods now exist there. **Still not directly confirmed** —
`message_api_service.dart` itself has not been re-uploaded since Phase
8, so this remains a build-blocker assumption, just a more optimistic
one than before. Don't treat as resolved until that file is shared
again (§10).

⚠️ **No entry point added for the new history screen (Phase 13)**: this
file was re-uploaded this round with the DTO change above, but did
**not** get the AppBar history icon that `focus_session_history_screen.
dart`'s own header comment asks for (§5.21). The history screen exists
but nothing in the uploaded codebase pushes it.

### 5.16 🔥 NAYA (Phase 11) `group_media_screen.dart` — shared media gallery
`GroupMediaScreen({groupId})` — pushed from `group_profile_screen.dart`'s
"Media, links and docs" card (§5.6). Per this file's own header
comment: the backend endpoint (`GET /message/groups/<id>/media/?type=`)
and `MessageApiService.getGroupMedia` already existed (§4.1) with **no
screen calling them at all** until this file — closes that gap.

- 4-tab `TabBar` (All/Photos/Videos/Files) — `type` query param sent as
  `null` for "All", else `'image'`/`'video'`/`'file'`.
- **Per-tab result cache** (`Map<String, List<dynamic>> _cache`,
  keyed by filter) — switching tabs after the first load never re-hits
  the network; `RefreshIndicator` pull-to-refresh explicitly clears the
  current tab's cache entry first (`forceRefresh: true`) to force a
  re-fetch.
- Raw `dynamic`/`Map` items throughout (no typed `GroupMediaModel`) —
  a `_str(item, key)` helper does ad-hoc field access
  (`file_type`/`file_url`/`thumbnail_url`/`file_size`). Worth a typed
  model if this screen grows, but functional as-is.
- "All" tab content splits client-side into a 3-column image/video
  GRID (`visual` — `file_type` image or video) plus a scrollable file
  LIST below it (`files` — everything else: audio/presentation/other).
- Tapping an image in the grid → `MediaViewerScreen` (the same
  fullscreen swipeable viewer `chat_screen.dart` uses — §5.9), seeded
  with just the OTHER images from the same grid (not videos, not
  files) so swiping stays within "photos in this group". Tapping a
  video or a file-list row → `url_launcher`'s `launchUrl(...,
  mode: externalApplication)` instead — **no inline video player or
  in-app file preview**, unlike `chat_screen.dart`'s own `_AudioBubble`/
  `_VideoPlayerScreen` which are private classes to that file and can't
  be reused here.
- No download-tracking (`isDownloaded` callback passed to
  `MediaViewerScreen` is hardcoded `(u) => false`) — every open here is
  effectively a fresh view/download, unlike `chat_screen.dart`'s
  `MediaDownloadService.alreadyDownloadedPath` integration (§4.11).

### 5.17 🔥 NAYA (Phase 12) `revision_deck_screen.dart` — Feature 5 UI
`RevisionDeckScreen({conversationId, boardContentBuilder})` — pushed
from `study_room_screen.dart`'s AI Tools bottom sheet ("Generate
Revision Deck", alongside Summary Notes/Quiz/Classroom Copilot).
**Confirmed wired**, closing the "service+model layer only" status
Phase 8/9 had flagged for Feature 5 (§4.3).

- `boardContentBuilder` is a **function reference**, not a pre-computed
  string — `study_room_screen.dart` passes its own
  `_collectBoardTextContent` method so that tapping "Regenerate" later
  collects the LATEST board text at that moment, not stale content
  captured when the screen first opened.
- Flow: on open, tries `AiStudyService.getSavedRevisionDeck()` first
  (silently swallows failure — no error shown, just falls through to
  the empty/"Generate" state) so a previously-generated deck reopens
  instantly without re-hitting the AI provider. Only `_generate()` (the
  explicit button, or the AppBar refresh icon once a deck exists) shows
  a `RevisionDeckThrottle` 429 error to the user.
- Two-tab `DefaultTabController` once a deck exists: **Flashcards**
  (swipeable `PageView`, tap-to-flip `AnimatedSwitcher` card per
  `FlashcardModel`) and **Quiz** (list of reveal-on-tap question cards).
- Quiz questions are read as raw `Map<String, dynamic>` (`question`/
  `answer`/`options`), not a typed model — `RevisionDeckModel.quiz` is
  `List<Map<String, dynamic>>` per §3.2, so this matches the model as
  documented, it's just untyped by design at this layer.
- Quiz card UI is a private `_RevisionQuizCard`, explicitly NOT reusing
  `study_room_screen.dart`'s own private `_QuizQuestionCard` (per this
  file's header comment) — same "can't reuse a private class across
  files" pattern already seen with `_AudioBubble`/video player in
  §5.16.

### 5.18 🔥 NAYA (Phase 12) `message_info_screen.dart` — "seen by" / message info
`MessageInfoScreen({messageId, messagePreview})` — the WhatsApp-style
long-press-a-sent-message → "Info" screen. See §17.4 and §10 for the
unconfirmed-model/method and unconfirmed-caller flags; this subsection
covers what the screen itself does, taking its inputs as given.

🔧 **Phase 14 update**: `message_api_service.dart` and
`message_models.dart` are both re-uploaded now, narrowing this
screen's backend dependency to a single concrete finding —
`getReadStatus(messageId)` DOES exist (§4.1), but `MessageReadStatusModel`
does NOT exist anywhere in `message_models.dart`. This is now a
**confirmed build blocker** (undefined class), not just an unconfirmed
one — see §10.

- Calls `MessageApiService.getReadStatus(messageId)` →
  `List<MessageReadStatusModel>`, then buckets client-side into three
  sections by `isRead`/`isDelivered`: **Read by** (sorted most-recent
  `readAt` first), **Delivered to** (not read, but delivered), **Not
  yet delivered** (neither).
- Read-receipt privacy is enforced **entirely server-side** per this
  file's own header comment — the screen just renders whatever
  `read_at` values the backend chooses to include/null out; it doesn't
  re-implement the §5.12 mutual-toggle logic itself.
- Each row: avatar + display name + `timeago`-formatted timestamp
  (`readAt` for the Read section, `deliveredAt` for Delivered, no
  timestamp for Not-yet-delivered rows).
- `messagePreview` is an optional plain string shown in a small card at
  the top so the user can confirm which message this is — deliberately
  NOT a full re-render of the actual message bubble.

### 5.19 🔥 NAYA (Phase 12), Feature 8 (PARENT side) `parent_code_entry_screen.dart` + `parent_dashboard_screen.dart`
First confirmed UI for `parent_service.dart` (§4.14) — closes that
service's "no screen uploaded for this yet" gap on the parent-facing
half of Feature 8.

- `ParentCodeEntryScreen` — no student login involved. A single code
  field (`TextCapitalization.characters`) → `ParentService.instance.
  verifyCode(code)` → on success, `pushReplacement` to
  `ParentDashboardScreen`. `ParentModeException` messages (already
  Hinglish per §4.14) are shown inline as the field's `errorText`.
  ⚠️ No confirmed entry point into this screen — its own header comment
  says to add a `TextButton` from `LoginScreen` (not uploaded).
- `ParentDashboardScreen` — `FutureBuilder<ParentDashboard>` over
  `ParentService.instance.fetchDashboard()`. Renders student name +
  one card per classroom with 4 stat tiles (current streak, total
  classes attended, assignments pending — highlighted orange if > 0,
  submitted/total). Pull-to-refresh re-calls `fetchDashboard()`.
  `signOut()` (AppBar logout icon) → `ParentService.instance.signOut()`
  → back to `ParentCodeEntryScreen`.
- Both screens' data shapes match `parent_service.dart`'s documented
  `ParentDashboard`/`ParentClassroomSummary` contract (§4.14) exactly —
  no drift found.
- Explicitly, deliberately **chat-content-free** — both screens'
  copy says so directly ("chat content yahan kabhi nahi dikhega"),
  consistent with `parent_service.dart`'s own scope.

### 5.20 🔥 NAYA (Phase 12), Feature 8 (STUDENT side) `manage_parent_access_screen.dart`
The other half of Feature 8 — where a student generates the code a
parent redeems in §5.19. **Does not use `parent_service.dart`** — see
the §4.14 callout and §10 flag for the backend-contract implications.

- Own inline `http` calls with a separate placeholder
  `_baseUrl = 'https://YOUR_API_HOST/message'` (same unwired-to-
  `Api.baseUrl` situation as `parent_service.dart`/`translate_service.dart`,
  §4.14/§4.15) and its own `_authHeaders()` reading `access_token` from
  `SharedPreferences` (the STUDENT's normal login token — not a
  `parent_token`).
- `GET /parent/codes/` → list of `ParentCodeEntry{id, label, code}` on
  screen load and after any mutation.
- Generate: dialog asks for a label ("e.g. Mom, Papa") → `POST
  /parent/codes/ {label}` → shows the returned code in a copyable
  dialog (`Clipboard.setData`).
- Revoke: confirm dialog → `DELETE /parent/codes/ {id}` (id in the
  request body, not the URL path) → refetches the list.
- ⚠️ No confirmed entry point either — header comment suggests a
  `ListTile` from wherever account/privacy settings live (not
  uploaded).

### 5.21 🔥 NAYA (Phase 13) `focus_session_history_screen.dart` — Feature 12 gap fix
Past-sessions list for Focus Mode. The file's own header comment frames
it precisely: §5.15 only ever showed the CURRENT active session; there
was no way to see how many times a student had used it, for how long,
or how often they ended it early — even though the backend supposedly
never deletes `FocusSession` rows.

- `FocusSessionHistoryEntry{id, startsAt, endsAt, exceptionRule,
  endedEarly, durationMinutes}` — local model defined in this same file
  (not `message_models.dart`), parsed from `GET
  /focus-session/history/?limit=50`.
- Same networking pattern as `manage_parent_access_screen.dart` (§5.20),
  **not** `MessageApiService`/`message_api_service.dart`: own inline
  `http` calls, own placeholder `_baseUrl = 'https://YOUR_API_HOST/
  message'`, own `_authHeaders()` reading `access_token` from
  `SharedPreferences`. This is now the THIRD screen in this doc using
  this standalone bypass-`MessageApiService` pattern instead of adding
  a method to the shared service (see `parent_service.dart`/
  `manage_parent_access_screen.dart`, §4.14) — worth flagging as a
  recurring pattern, not a one-off, next time `message_api_service.dart`
  is touched.
- UI: pull-to-refresh `ListView`, one card per session — date/time
  (`_dateLabel`), duration (`_durationLabel`), exception rule label
  ("Full silence" / "Teachers only"), and a check-circle (completed) or
  stop-circle (ended early) leading icon per row. Empty/loading/error
  states handled inline, no special cases beyond that.
- ⚠️ **Orphan screen, same as `message_info_screen.dart` was in
  Phase 12**: this file's own header comment gives the intended wiring
  (an `Icons.history` AppBar action on `focus_mode_screen.dart`) but
  says explicitly it couldn't add it because `focus_mode_screen.dart`
  wasn't uploaded that session. `focus_mode_screen.dart` WAS uploaded
  THIS round (§5.15) and still has no history icon — so unlike
  `message_info_screen.dart` (whose caller got confirmed this same
  batch, see §5.2), this one's wiring gap is still open. See §10.
- ⚠️ **Backend contract unconfirmed**: `GET /focus-session/history/`
  is not documented anywhere else in this doc (§4.6's Focus/DND
  coverage is all client-side `CallManager` stuff, unrelated; §17's
  backend cross-check predates this feature). Don't assume it exists
  server-side until confirmed, same caution as `manage_parent_access_
  screen.dart`'s `/parent/codes/` (§5.20, §10).

---

## 6. Widgets Layer

### 6.1 ✅ RESOLVED (Phase 4): only `minimized_call_bar.dart` remains
Was previously flagged as two competing widgets — now confirmed via
`main.dart`'s `MaterialApp.builder` (mounts ONLY `MinimizedCallBar()`)
and a full grep across every uploaded file (`FloatingCallBar` appears
nowhere except its own now-deleted definition).

**`floating_call_bar.dart` has been deleted** (superseded — older
version, took `navigatorKey` as a constructor param, reopened
`CallScreen` with placeholder `livekitUrl:''`/`livekitToken:''`).

`minimized_call_bar.dart` (`MinimizedCallBar()`, no params) is the
current/correct one: uses the GLOBAL `CallKitService.navigatorKey`
instead of a locally-passed one, because this widget sits in
`MaterialApp.builder`'s `Stack` as a SIBLING of `child` (i.e. OUTSIDE
the `Navigator`), so `Navigator.of(context)` from its own
`BuildContext` doesn't work — documented root-cause of a "back karne
ke baad fullscreen wapas nahi aati" bug. Listens to `CallManager.instance`,
shows only when `isActive && isMinimized`.

### 6.2 `whiteboard_painter.dart`
`WhiteboardPainter(strokes, shapes, previewShape?)` — `CustomPainter`.
Draws freehand strokes (marker/paint normal; eraser = white line;
highlighter = 35%-opacity + 2.2x width + square cap), finalized shapes
(`_drawShape`: rect/circle/line/arrowLine w/ custom arrowhead math),
and a translucent `previewShape` while a shape is being dragged (before
finalized/broadcast). `shouldRepaint` always returns `true` (no diffing
— fine for a whiteboard, repaints are cheap relative to draw
frequency).

### 6.3 🔥 NAYA (Phase 4) `mention_suggestions_overlay.dart`
Two pure top-level functions + one widget, all consumed by
`chat_screen.dart` (which already imported/called them before this file
existed — this was the missing piece, not new wiring in `chat_screen.dart`):
- `extractMentionQuery(text, cursorPosition)` → `String?` — null if no
  active `@query` at the cursor (checks: nearest `@` before cursor, no
  space/newline between it and cursor, and `@` itself is either
  string-start or preceded by whitespace — avoids false-triggering on
  things like `email@domain`).
- `insertMention(TextEditingValue, UserMini)` → `TextEditingValue` —
  replaces the active `@query` with `@username ` (trailing space) and
  moves the cursor past it.
- `MentionSuggestionsOverlay({members, query, onSelected})` — floating
  card above the compose box, filters `members` (always the caller-
  supplied group member list, e.g. `_groupMembers` — never a global user
  search, matching the backend's own active-members-only matching),
  username-prefix matches sorted first.

### 6.4 🔥 NAYA (Phase 9) `language_picker_sheet.dart` — Feature 9 support
Not a message widget itself — a shared bottom-sheet + small persistence
helper backing the translate feature's language choice.
- `kTranslateLanguages` — hardcoded `Map<String,String>` (code→display
  name), 10 languages (en/hi/mr/ta/te/kn/bn/gu/pa/ur). File header flags
  this **must stay in sync with backend `translation_service.py`'s
  `SUPPORTED_LANGUAGES`** — a manual sync point, not fetched from the
  server, so a backend-side language addition needs a matching edit here.
- `getPreferredTranslateLang()` / `setPreferredTranslateLang(code)` —
  SharedPreferences-backed (`preferred_translate_lang`), so once a user
  picks a target language once, later translate taps skip straight to
  the API call.
- `showLanguagePickerSheet(context)` → `Future<String?>` — modal bottom
  sheet, checkmarks the current preferred language, persists the new
  choice on selection, returns `null` if dismissed without picking.

### 6.5 🔥 NAYA (Phase 9) `translatable_message_widgets.dart` — Features 9 & 10 UI
The first actual UI wiring for `translate_service.dart` (§4.15) and
`tts_service.dart` (§4.16) — previously both were "service-layer only,
no screen call-site". Two self-contained widgets, meant to be dropped
into the (not-yet-uploaded) message bubble widget's action row, next to
existing reply/react/star icons:
- **`ListenButton({messageId, text, languageCode?})`** — Feature 10.
  Thin wrapper around `TtsService.instance` — `ValueListenableBuilder`
  on `currentlySpeakingId` swaps its own icon between
  play (`volume_up_outlined`) and pause (`pause_circle_outline`)
  depending on whether THIS message is the one currently speaking.
  Tapping always calls `TtsService.instance.speak(...)`, which itself
  handles the pause-if-already-speaking / switch-if-different-bubble
  logic (§4.16) — the button has no state of its own.
- **`TranslateToggle({messageId, text})`** — Feature 9, stateful. First
  tap: uses the saved preferred language if set, else opens
  `showLanguagePickerSheet` first; then calls
  `TranslateService.instance.translate(...)` and expands an inline
  translated block below the original text (no navigation, same
  bubble). Second tap on the "Translate"/"Hide translation" label
  toggles the expanded block closed without re-fetching. Inside the
  expanded block: target-language name, a "Change language" link
  (re-opens the picker, re-fetches), and its own `ListenButton` scoped to
  the TRANSLATED text (keyed `'${messageId}_translated'` — deliberately
  a different key from the original message's `ListenButton`, so reading
  the translation aloud doesn't fight over the same
  `currentlySpeakingId` slot as reading the original aloud, and both can
  show independent play/pause state). Errors (503/429 from
  `TranslateException`) render inline in red rather than a snackbar/dialog.

⚠️ Neither widget is yet confirmed wired into the actual chat bubble
widget (that file — the real message-bubble UI — hasn't been uploaded).
Building on this: the bubble needs a conditional block for
`MessageType.text` messages only (per this file's own header comment)
adding both buttons to its action row.

---

## 7. End-to-End Flows

### 7.1 App startup (partially confirmed via `main.dart`, Phase 4)
Presumed sequence based on cross-references: Firebase init →
`PushNotificationService.instance.init()` (sets background handler,
requests permissions, registers FCM token if logged in, starts
`MissedCallWatcher`) → `CallKitService.instance.init(navigatorKey)` →
login (via not-yet-shared `AuthService`) → on success,
`PushNotificationService.instance.registerToken()` (must be called
manually right after login too, since token isn't registered until a
user is authenticated) → `ConversationsScreen` connects
`InboxSocketService`. ✅ **Confirmed (Phase 4)**: `main.dart` was shared
— `MaterialApp.builder`'s Stack mounts ONLY `MinimizedCallBar()`
(`floating_call_bar.dart` is unused/now deleted, see §6.1); `home.dart`
was also shared — its `IndexedStack` is Home/Search/Profile (3 tabs)
and it separately imports+pushes `ConversationsScreen` for the Chats
tab (see §5.1), confirming the `app_bottom_nav.dart` index-mapping
note in §5.10.

### 7.2 Sending a text message
1. `ChatScreen._sendMessage()` → optimistic `MessageModel` (isSending:
   true) inserted into `_messages`, UI scrolls to bottom
2. If `_isSocketConnected` → `_socket.sendMessage(text, clientId,
   replyTo)` (fire-and-forget over WS)
3. Else → REST fallback `MessageApiService.sendMessageRest(...)`
4. Server echoes back over the SAME socket as a `chat_message` event
   (`MessageModel.fromSocketEvent`) — client matches on `clientId` to
   replace the optimistic entry (`_onIncomingMessage`)
5. Receiving side (other participant's open `ChatScreen`) also gets the
   `chat_message` event, appends it, immediately sends a read receipt
   (`_socket.sendReadReceipt(incoming.id)`) if the chat is open

### 7.3 Sending media (image/video/audio/file/presentation/location)
**Always goes through REST**, never the socket `message` event (socket
only carries plain text): `MessageApiService.uploadFile(file,
onProgress)` first → get `fileUrl` → `sendMessageRest(type, fileUrl,
meta, clientId)`. Delivery to the OTHER participant happens via the
backend broadcasting a `chat_message` socket event server-side (per
`PATCH_views_realtime_broadcast.md` comment referenced in
`message_models.dart` — a backend doc not seen, but implied to exist).
Multi-image sends batch through `_uploadAndSendMultipleImages`.

### 7.4 Message pin/unpin/edit/delete/forward
- Pin/unpin: REST (`pinMessage`/`unpinMessage`) and/or 🔥 NAYA (Phase 4)
  socket (`ChatSocketService.sendPin(messageId, pin)`) — either path,
  other side learns via the `pin_event` socket broadcast (🔧 corrected
  name, see §8.1)
- Edit: `editMessage` REST PATCH, sender-only
- Delete: BOTH REST (`deleteMessage`, `for_everyone` flag) AND socket
  (`_socket.sendDelete`) are fired together from `_deleteMessage()` —
  redundant-looking but likely intentional (REST = durable state
  change, socket = instant UI removal on other side without waiting
  for a server broadcast round-trip)
- Forward: single REST call `forwardMessages(messageIds,
  conversationIds, caption)` handles 1-to-many and many-to-many in one
  shot; `caption` is optional and only sent when the selection includes
  a non-text message (see §5.8); target-chat delivery is server-side
  realtime (not client-driven)

### 7.5 Polls
Create → `createPoll()` (POST `/message/conversations/<id>/poll/`,
singular — see §4.1) → `_insertPollMessage(poll)` locally. Vote →
`votePoll(messageId, optionIds)` (re-vote auto-replaces prior vote
server-side). Both create and vote also arrive as `poll_created`/
`poll_voted` socket events for OTHER participants
(`_onPollCreatedEvent`/`_onPollVotedEvent`). 🔧 **Phase 14 correction**:
`votePoll`/`closePoll` are keyed by the poll's underlying **message
id**, not a separate poll id (`POST /message/messages/<id>/poll/vote|
close/`) — the param was previously (incorrectly) documented as
`pollId`. **Gotcha**: historical (paginated/scrolled-to) poll messages
come back from `getMessages()`/history WITHOUT poll data (`meta`
empty) — only live socket events populate options/votes now. 🔧
**Phase 14**: the standalone `getPoll(pollId)` fallback documented
here previously has been **removed from the code entirely** (per the
file's own comment, that backend endpoint never existed) — a
scrolled-up old poll renders read-only "📊 question" until a live
socket event refreshes it; there is no explicit re-fetch path anymore.

### 7.6 Scheduled messages
Draft-only client feature: `scheduleMessage()` creates a
`ScheduledMessageModel` server-side for a future `scheduledFor` time
(backend validates it's in the future). `getScheduledMessages`
lists pending ones, `rescheduleMessage`/`cancelScheduledMessage` edit/
cancel. Actual send-at-time presumably happens server-side (a cron/
celery task not visible from Flutter code) — Flutter never "sends" it
itself at the scheduled moment.

### 7.7 Group lifecycle
Create (`create_group_screen.dart`) → `createGroup()`. Public groups:
`joinGroupByInviteCode()` joins immediately (status `'joined'`).
Private groups: same call creates a `GroupJoinRequest`
(status `'pending'`) — admin/mod later `approveJoinRequest`/
`rejectJoinRequest` from `group_profile_screen.dart`'s join-requests
sheet or `chat_screen.dart`'s own join-requests shortcut. Roles
(member/moderator/admin) managed via `updateGroupMember`. Admin-only:
`deleteGroup` (cascades all messages/media/members server-side).
Access-control (who can send messages/calls/study-room, daily message
limit) is a group-level setting surfaced in BOTH `chat_screen.dart`'s
quick sheet and `group_profile_screen.dart`'s full settings.

### 7.8 Block/unblock
Lives under the `profile` Django app, not `message` — API calls hit
`/profile/blocked-users/` not `/message/...`. `isUserBlocked(userId)`
fetches the WHOLE block list and searches locally (no dedicated
"check one user" endpoint) — fine for small lists, would need
revisiting if block lists grow large.

### 7.9 1:1 Call — outgoing (caller side)
1. `ChatScreen._startCall(type)` → `CallApiService.initiateCall(
   conversationId, type)` → gets `call_id` + `livekit_url/token`
2. Push `CallScreen(isCaller: true, ...)` → `CallManager.
   startCallIfNeeded(...)` → `_initCall()`:
   - requests mic (+camera if video) permission
   - `WakelockPlus.enable()`, sets speakerphone on
   - plays outgoing ringtone, starts 30s `_noAnswerTimer`, starts 2s
     `_startCallStatusPoll()` (reject-detection)
   - connects to LiveKit room, publishes video track if video call —
     **mic is deliberately NOT enabled yet** (`_micPendingForCaller`)
3. Backend pushes `incoming_call` FCM data message to callee
4. On `ParticipantConnectedEvent` (callee actually joined the room) →
   cancel no-answer timer, NOW enable caller's mic, status→"Joined"
5. On first `TrackSubscribedEvent` (audio or video) →
   `remoteConnected=true`, stop ringtone, start call duration timer +
   ongoing-call system notification
6. If callee rejects before joining the room: no RoomEvent ever fires
   for caller → the 2s status-poll detects `rejected/declined/busy` →
   `_endAsLineBusy()` shows "Line busy" ~1.6s then cleans up
7. If nobody answers in 30s: `_noAnswerTimer` fires → `endCall()`

### 7.10 1:1 Call — incoming (callee side)
- **Foreground**: `push_notification_service.dart`'s
  `FirebaseMessaging.onMessage` listener → if `CallManager.isActive`
  already → `setWaitingCall(...)` (banner in `CallScreen`, no new
  screen); else → `IncomingCallScreen.showIfNeeded(...)` pushed
  directly (Flutter screen, NOT native CallKit, to avoid double-popup)
- **Background/killed**: `firebaseBackgroundHandler` → data-only FCM
  required → `CallKitService.showIncomingCall(data)` → native
  full-screen popup (works even if app process is dead, as long as the
  data message reaches the device)
- Accept (either path) → `CallApiService.callAction(callId,'accept')`
  → get livekit creds → push `CallScreen(isCaller: false, ...)` →
  `CallManager.startCallIfNeeded(...)` → mic enabled IMMEDIATELY
  (callee already tapped accept, no ringtone-ducking concern)
- Reject → `CallApiService.callAction(callId,'reject')`, never joins
  the LiveKit room
- Caller-cancels-before-answer → backend sends `call_cancelled` push →
  `CallKitService.endCallUiByCallId` (dismiss native popup) + clear
  waiting-call state or pop `IncomingCallScreen`

### 7.11 Group call — add participant mid-call
`CallScreen`'s `_AddParticipantSheet` → `CallApiService.
getAddableParticipants(callId)` (conversation members not yet in call)
→ pick one → `addParticipant(callId, userId)` → backend sends them a
normal `incoming_call` push with the SAME `callId` → on their accept,
they join the same LiveKit room → `ParticipantConnectedEvent` fires
for everyone already in the room → `remoteTiles` grows →
`isGroupCall` flips true once 2+ remote participants → UI switches
from 1:1 layout to grid (implied — grid rendering itself is in
`call_screen.dart`, only scanned at a high level here).

### 7.12 Hold
`CallManager.toggleHold()` → mutes own mic+camera (remembers prior
on/off state to restore exactly on resume), locally disables received
remote audio tracks (so holder hears nothing), sends
`{'type':'call_hold', hold: bool}` over the data channel → peer's
`CallManager` sets `peerOnHold` from the SAME `DataReceivedEvent`
handler that also handles `call_end`.

### 7.13 Missed-call detection (offline→online)
`MissedCallWatcher` listens to `connectivity_plus`. Tracks `_wasOffline`.
On transition false→true (net restored): reads persisted
`last_online_at` from SharedPreferences → `CallApiService.
getMissedCalls(since: lastOnline)` → local notification per missed
call (channel `missed_calls`) → tap → same `onNotificationTap`
callback as ordinary chat notifications → opens that conversation.
Watermark updates on every offline transition too (so a kill+restart
while offline still resumes from the correct point).

### 7.14 Study Room — join (Meet-style, no ringing)
1. `ChatScreen._openStudyRoom()` (icon) → sends an invite CARD message
   (type `study_room`) so the OTHER participant sees a tappable card in
   chat → `_enterStudyRoom(startNewSession: true)`
2. `StudyRoomScreen` opens → `_connectSocket()` (reuses the
   conversation's chat WS channel) → `_joinStudyRoomMedia()` →
   `CallApiService.joinStudyRoom(conversationId, newSession: true)` →
   backend mints a FRESH LiveKit room (new room name derived from
   conversationId + session id) → `StudyRoomCallManager.joinRoom(...)`
   → camera+mic auto-ON
3. Tapping the invite card (sender OR receiver, any time later) calls
   the SAME `_enterStudyRoom(startNewSession: false)` → joins the
   EXISTING active session's room instead of minting a new one
4. On join: `_announceSelfJoined()` broadcasts a `user_joined`
   study-room event, `_applyServerParticipants()` syncs floating
   profile windows for everyone already present, `_restoreBoardState()`
   pulls persisted whiteboard pages via `getStudyRoomState()`

### 7.15 Study Room — realtime whiteboard collaboration
Every draw/shape/text/sticky/page/timer/chat/sticker action follows
the same shape: local optimistic render → `_sendRoomEvent(action,
data)` → `ChatSocketService.sendStudyRoomEvent(action, data)` →
`{'type':'study_room_event', action, data}` over the SAME per-
conversation WS used for chat → backend rebroadcasts to all other
participants in that conversation's socket group → their
`_handleRoomEvent` switch applies the matching action locally. Undo is
per-user (`undo_user_stroke`/`undo_user_shape`/`undo_user_text`/
`undo_user_sticky` — each user can only undo their own contributions,
tracked via `userStrokeIndices`/`_recordMyAction`). Loading a PDF/image
is upload-once-broadcast-many: one participant uploads + rasterizes
locally, then broadcasts `load_page_file` with the resulting URL so
everyone else just downloads+caches it (`_downloadAndCachePageFile`)
instead of re-uploading.

### 7.16 Study Room — AI tools
`_openAiToolsSheet()` → pick mode (`summary`|`quiz`) → gather board
text content → `AiStudyService.generate(mode, content)` → backend
calls the real AI provider (never exposed client-side) →
`_showAiResultSheet(mode, result)` → summary bullets can be
individually saved back onto the board as sticky notes
(`_saveTextAsStickyNote`); quiz results render via
`_QuizQuestionCard`.

🔥 NAYA (Phase 7, Feature 4) — third option in the same sheet, "Ask
About This Class": `_openClassroomCopilotSheet()` (question input) →
`_runClassroomCopilot(question)` → `AiStudyService.askClassroomCopilot(
conversationId, question, boardContent: _collectBoardTextContent())`.
Backend (`views_ai.ClassroomCopilotView`) assembles context from THREE
sources before calling Gemini — recent chat messages (same query shape
as `SmartReplySuggestionsView`), the whiteboard text the client just
sent, and transcript segments (§7.19) whose text keyword-matches the
question (falls back to the most recent segments if no keyword match) —
then answers grounded in that, explicitly instructed to say "not enough
context" rather than invent an answer. This is what makes it different
from `generate()` above: that one only ever sees current board text,
this one sees the class's actual history. No Assignment/StudyMaterial
model exists yet in the backend (confirmed absent) — that's a 4th
context source to add later, not included here.

### 7.17 Push notification routing summary
See §4.9 for full detail — the short version: **all push decisions
hinge on `data.type`**, and background/killed states MUST rely on
data-only FCM payloads + `firebaseBackgroundHandler` (native CallKit
for calls, local notifications with actions for everything else),
while foreground routing goes through
`FirebaseMessaging.onMessage` and pushes Flutter screens directly
(never native CallKit while foreground, to avoid double UI).

### 7.18 Offline-first list/thread loading
Both `ConversationsScreen` and `ChatScreen` follow: show cached data
(`MessageCacheService`) instantly on screen open → fetch fresh data
from network in the background → overwrite state + re-save cache. A
network failure while cached data is already showing is silent (no
error UI) — only a network failure with NO cache to fall back on shows
an error/retry state.

### 7.19 🔥 NAYA (Phase 7) Study Room — class transcript (Feature 3)
**Design constraint that shaped everything below**: `CallSession.
is_recording`/`recording_url` (backend `models.py`) exist as columns but
no LiveKit egress/recording-trigger code exists anywhere in this stack
(pre-existing gap, confirmed against backend doc). So there is no single
continuous recording of a class to seek into. What's built instead:

1. Right after `StudyRoomCallManager.joinRoom()` succeeds,
   `study_room_screen.dart` calls `startTranscriptRecording(
   conversationId, sessionId)` — `sessionId` comes from the
   `joinStudyRoom` REST response (§4.2 — backend needs to add this
   field; suggested to reuse `CallSession.channel_name`).
2. `StudyRoomCallManager` records the LOCAL participant's own mic in
   ~45s rotating chunks (`record` package, same AAC-LC encoder as chat
   voice-notes) — every participant does this independently, there's no
   central "class recording", just N parallel per-speaker chunk streams.
3. Each finished chunk uploads via the existing
   `MessageApiService.uploadFile` (same endpoint chat media uses), then
   registers via `AiStudyService.registerTranscriptChunk` → backend
   `ClassTranscriptChunkUploadView` → creates a `ClassTranscriptSegment`
   row (`status=pending`) → enqueues `tasks.
   transcribe_class_chunk_task` (Celery, reuses the same
   `ai_service.transcribe_audio()` the voice-note auto-transcription
   feature already uses) → segment gets its `text` + `status=done`.
4. `class_transcript_screen.dart` (§5.13) queries
   `ClassTranscriptSearchView`, sorted by `session_id` +
   `start_offset_seconds` — segments from different participants
   naturally interleave into one time-ordered transcript. Tapping a
   result plays that segment's own short audio file (not a scrub bar
   into a long recording — there isn't one).
5. `stopTranscriptRecording()` is called automatically from
   `StudyRoomCallManager.leaveRoom()` — screen code doesn't need to
   remember to stop it on dispose separately.

⚠️ Two backend pieces this depends on that were NOT in any uploaded
file this session, so are assumptions, not confirmed contract (flagged
again in §10/§11): (a) `StudyRoomJoinView` returning `session_id`, (b)
a WS `transcript_segment_ready` event in `consumers.py` (same
plain-passthrough shape as the existing `meta_update` event) so an
already-open transcript screen updates live instead of only on
re-search.

### 7.20 🔥 NAYA (Phase 10) Doubt Queue — ask/upvote/answer/reveal
1. `ChatScreen`'s "Doubts" AppBar icon (group chats only, §5.2) →
   `DoubtsScreen(groupId, conversationId, groupName)` (§5.14).
2. On open: parallel-ish REST — `DoubtsApiService.getGroup(groupId)`
   (role + `allow_anonymous_doubts`) + `getDoubts(groupId, status)`
   (initial page) — then a fresh `ChatSocketService()` connects to the
   SAME `conversation_id` the parent `ChatScreen` is already on (§4.4's
   "up to three connections" note).
3. **Ask**: FAB → bottom sheet (text + optional anonymous checkbox) →
   `createDoubt()` → inserted locally immediately (optimistic,
   independent of the `doubt_event` echo that will also arrive).
4. **Upvote**: tap → optimistic local flip (`copyWith`) → REST
   upvote/remove-upvote → rollback on failure.
5. **Answer** (teacher/admin/mod only, `_isTeacher` from the role scan
   in step 2): inline flow → `answer()` → server broadcasts
   `doubt_event` with the updated doubt (now `is_answered: true`) to
   every open `DoubtsScreen` on that conversation, including the
   answerer's own (so no separate local-optimistic-answer path is
   needed the way ask/upvote have one).
6. **Reveal** (teacher-only, anonymous+unrevealed doubts only): tap
   "Reveal who asked" → `reveal()` → broadcasts `doubt_event` with
   `author` now populated and `is_revealed: true` — every open
   `DoubtsScreen` (not just the revealer's) sees the real name from
   that point on.
7. Any `doubt_event` arriving while a filter other than "All" is active
   may cause a doubt to appear/disappear from the visible list without
   a page reload (e.g. answering removes it from an open "Unanswered"
   filter) — this is intentional, not a bug, per `_matchesFilter` logic
   in §5.14.

---

## 8. Socket Event Catalog

### 8.1 `ws/chat/<conversation_id>/?token=<JWT>` (`ChatSocketService`)
Client → Server:
| type | payload |
|---|---|
| `message` | `{client_id, message_type, text, reply_to, file_url?, file_urls?, thumbnail_url?, meta?}` — 🔧 last 4 fields NAYA (Phase 4), previously text-only |
| `typing` | `{is_typing}` |
| `read` | `{message_id}` |
| `delete` | `{message_id, for_everyone}` |
| `reaction` | `{message_id, emoji}` |
| `pin` | `{message_id, pin}` — 🔥 NAYA (Phase 4), optional; REST pin/unpin still works standalone |
| `study_room_event` | `{action, data}` (generic passthrough) |

Server → Client (`type` field, consumed by `ChatScreen._handleSocketEvent`):
`chat_message`, `typing`, `read`, `delete`, `reaction`, `poll_created`,
`poll_voted`, `pin_event` (🔧 corrected this session — the actual
`case` in `chat_screen.dart` is `'pin_event'`, not the previously-noted
`message_pinned`/`message_unpinned`; covers both pin AND unpin),
`conversation_wallpaper_updated`, `presence`,
`disappearing_messages_updated`, `group_deleted`, `call_event`,
`incoming_call`, `error`, `doubt_event` (🔥 NAYA, Phase 10 — `{doubt:
{...DoubtQuestionSerializer shape}}`, consumed by `DoubtsScreen`'s own
socket listener, §5.14 — NOT handled by `ChatScreen._handleSocketEvent`
itself; only relevant while a `DoubtsScreen` is open on the same
conversation, backend broadcasts via `ChatConsumer.doubt_broadcast`)

Server → Client, `study_room_event` sub-actions (consumed by
`StudyRoomScreen._handleRoomEvent`): `draw_point`, `undo_user_stroke`,
`clear_board`, `clear_board_keep_text`, `undo_user_shape`,
`undo_user_text`, `undo_user_sticky`, `add_sticky_note`,
`update_window`, `ruled_lines`, `user_joined`, `add_shape`, `add_text`,
`add_page`, `remove_page`, `load_page_file`, `presentation_started`,
`presentation_stopped`, `timer_update`, `quick_chat`, `sticker`

### 8.2 `ws/inbox/?token=<JWT>` (`InboxSocketService`, global singleton)
Server → Client: `inbox_update` (new-message/unread-count notification
for ANY conversation, consumed by `ConversationsScreen` to refresh the
list without opening each chat)

---

## 9. Documented Bugs Fixed (compiled from 🔥 FIX / root-cause comments)
Kept here because they encode WHY the current code looks the way it
does — reverting these patterns will likely reintroduce the same bugs.

1. Ringtone silenced on caller side → fixed by deferring
   `setMicrophoneEnabled(true)` until the callee actually joins
   (`ParticipantConnectedEvent`), not at room-connect time.
2. Call didn't hang up promptly for the other side → explicit
   `{'type':'call_end'}` data-channel signal sent before backend
   notify, bypassing LiveKit's reconnect grace period.
3. Caller kept ringing after callee rejected (reject never joins the
   room, so no RoomEvent fires) → 2s `getCallStatus()` poll while
   ringing detects rejected/declined/busy.
4. `RoomDisconnectedEvent` used to defer to a reconnect-wait even on
   explicit hangup → now treated as authoritative, immediate cleanup.
5. `MessageModel.fromSocketEvent` avatar field mismatch:
   backend sends `sender_profile_photo`, code used to read
   `sender_avatar` → sender avatar never loaded on realtime/REST-
   broadcast messages.
6. `is_forwarded` missing from `fromSocketEvent` factory → forwarded
   badge only appeared after a reload, not on live delivery.
7. Chat list private-chat photo always null → `displayPhoto` getter
   fixed to also read `otherParticipant.profilePhoto`, not just group
   photo.
8. `chat_screen_pagination_fix.dart`: DRF's `PageNumberPagination`
   returns 404 on an out-of-range page — this is NOT a real error, it
   means "no more history"; previously this caused `_hasMoreMessages`
   to stay wrongly `true` and the same failing request to retry
   forever. Fixed: catch `MessageApiException` with `statusCode==404`
   → set `_hasMoreMessages=false` silently (no snackbar); any OTHER
   status code still shows the "Failed to load older messages" snackbar.
9. `uploadFile` progress callback was dead code under
   `http.MultipartRequest` (no progress API) → switched to Dio's
   `onSendProgress`.
10. FCM background isolate never initialized Flutter bindings/Firebase
    → every plugin call (shared_preferences etc.) hung forever →
    notification-Reply and background call handling silently broken.
    Fixed by `WidgetsFlutterBinding.ensureInitialized()` +
    `Firebase.initializeApp()` at the top of
    `firebaseBackgroundHandler`.
11. FCM token registration only happened on `onTokenRefresh` (may not
    fire for months) → device never got pushes after first install.
    Fixed: also call `registerToken()` explicitly in `init()`.
12. Android 14+ full-screen-intent for incoming calls requires
    `flutter_callkit_incoming >= 2.5.0` AND explicit user grant via
    Settings (`requestFullIntentPermission()`) — code alone cannot
    silently grant it.
13. `MinimizedCallBar` (vs the older `FloatingCallBar`) — reopening the
    call from the minimized pill failed because the widget lives
    OUTSIDE the Navigator (sibling of `MaterialApp.builder`'s `child`
    in the Stack) so its own `BuildContext` has no Navigator. Fixed by
    reusing the app-global `CallKitService.navigatorKey` instead.
14. Study Room mic/camera silently failed to enable on some Android
    OEMs (Xiaomi/Oppo/Vivo) because `StudyRoomCallManager` skipped an
    explicit `permission_handler` request that `CallManager` (1:1
    calls) already had — added `_requestMicPermission`/
    `_requestCameraPermission`.
15. Foreground-service "disable battery optimization" popup removed
    from normal calls entirely (`_enableBackgroundExecution()` no
    longer called in `_initCall`) per explicit product request; kept
    ONLY around screen-share, where Android mandates an active
    foreground service with `mediaProjection` type or capture crashes.

---

## 10. Known Ambiguities / Things To Confirm Before Building On Top

- ⚠️ **Two conversation-list screens, re-opened (Phase 5)** — Phase 4
  had this resolved (`conversations_list_screen.dart` deleted, see
  §5.1 history), but the file was re-uploaded this session. `main.dart`
  and `home.dart` were not re-shared this session so routing can't be
  re-verified either way. **Ask the user**: is this file meant to come
  back (replace/coexist with `conversations_screen.dart`), or was it
  uploaded by mistake / for reference only? Don't wire it in or delete
  it from the doc without that answer.
- 🔥 **NEW (Phase 5)**: `message_api_service.dart` has two methods
  hitting the same `start_private/` endpoint — `startPrivateChat` (used
  by `conversations_screen.dart`) and `getOrCreateConversation` (no
  known caller in anything uploaded so far). See the callout in §4.1.
  Possibly dead code, possibly used by an unshared screen (e.g. profile
  → "message this user").
- ~~Two minimized-call-bar widgets~~ — ✅ RESOLVED Phase 4, see §6.1.
  `floating_call_bar.dart` deleted.
- `call_screen.dart` has its OWN outgoing-ring/call-waiting-tone
  `AudioPlayer`s in addition to `CallManager`'s internal ringtone
  player — potential double-audio, not yet confirmed either way.
- `call_screen.dart`'s local `_noAnswerTimer` is 35s while
  `CallManager`'s internal one is 30s — two independent timers,
  purpose of the screen-level one not fully traced yet.
- ~~`CallApiService.baseUrl` separate hardcoded constant~~ — ✅
  RESOLVED (Phase 8, §4.2) — now a getter delegating to `Api.baseUrl`,
  same single source as every other service. (This bullet was left
  stale here through Phases 9/10; corrected now, Phase 11.)
- ✅ **Build blocker RESOLVED (Phase 11 → downgraded Phase 13 → closed
  Phase 14)** — `focus_mode_screen.dart` (Feature 12, §5.15) and its
  caller `conversations_screen.dart` (§5.1) call `MessageApiService.
  getFocusStatus()`/`startFocusSession()`/`cancelFocusSession()`.
  `message_api_service.dart` is now re-uploaded (Phase 14) and directly
  confirmed to define all 3 methods (GET/POST/DELETE
  `/message/focus-session/`, §4.1), sharing `FocusSessionStatus` from
  `message_models.dart` (also re-uploaded and confirmed, §3.1). Safe to
  build on top of Focus Mode now.
- `chat_screen.dart`'s `_enterStudyRoom` passes `initialParticipants:
  const []` with a `// TODO` to actually populate it from the group's
  member list.
- `FocusSessionStatus.inactive()` static factory — 🔧 **Phase 14
  confirmed**: it DID move along with the DTO into `message_models.dart`
  (§3.1) and still just returns `null`. Harmless dead code (nothing in
  `message_api_service.dart`'s re-uploaded focus-session methods calls
  it — `getFocusStatus()` returns `null` directly instead), not worth
  fixing.
- 🔥 **NEW (Phase 14)** — `message_api_service.dart`'s poll endpoints
  (`createPoll`/`votePoll`/`closePoll`) and the study-room-state
  endpoints (`saveStudyRoomState`/`getStudyRoomState`/
  `endStudyRoomState`) hit different URL shapes than this doc previously
  recorded (singular `/poll/` not `/polls/`; message-id-keyed vote/close
  not poll-id-keyed; `/study-room/<id>/state/` not
  `/conversations/<id>/study-room-state/`). Doc corrected in place —
  see §4.1, §7.5. Since these are corrections to a service file that
  wasn't re-uploaded between Phase 8 and now, treat the OLD paths as
  never having been accurate for whatever build this doc was written
  against — not a regression, a doc error now fixed.
- `getOrCreateConversation` duplicate-endpoint dead code (§4.1's Phase 5
  callout) — 🔧 **Phase 14**: still present, unchanged, in the
  re-uploaded `message_api_service.dart`. Still no confirmed caller
  anywhere uploaded. Flag stands.
- 🔴 **Confirmed build blocker (Phase 12 → narrowed Phase 13 → CONFIRMED
  Phase 14)** — `message_info_screen.dart` calls `MessageApiService.
  getReadStatus(messageId)` expecting a `MessageReadStatusModel`. Both
  files are now re-uploaded: `getReadStatus()` DOES exist in
  `message_api_service.dart` (GET `/message/messages/<id>/read-status/`,
  §4.1) and returns `List<MessageReadStatusModel>` — but
  `MessageReadStatusModel` is **not defined anywhere** in the
  re-uploaded `message_models.dart` (grepped the full class list, §3.1
  — it's simply missing). This is a genuine undefined-symbol compile
  error, not a doc gap. `message_info_screen.dart` (§5.2's "Info" entry
  point is already wired) will not build until this class is added.
- 🔥 **NEW, unconfirmed (Phase 12)** — `manage_parent_access_screen.dart`
  hits `/parent/codes/` (GET/POST/DELETE) directly via its own `http`
  calls — a backend contract not documented anywhere else in this doc
  (separate from `parent_service.dart`'s `/parent/verify/` and
  `/parent/dashboard/`). Ask backend to confirm this route exists before
  wiring an entry point to this screen.
- 🔥 **NEW (Phase 12, one item resolved in Phase 13)** — Three of Phase
  12's five new screens (`parent_code_entry_screen.dart`,
  `manage_parent_access_screen.dart`, `message_info_screen.dart`) had no
  confirmed navigation entry point. `message_info_screen.dart` is now
  ✅ resolved (§5.2's "Info" `ListTile` in `chat_screen.dart`). The other
  two remain open — each still says so in its own header comment
  (`LoginScreen`, and account/privacy settings, respectively — neither
  caller has been uploaded). Same category Phase 10 flagged for
  `translatable_message_widgets.dart` (§21.4).
- 🔥 **NEW orphan screen (Phase 13)** — `focus_session_history_screen.
  dart` (§5.21) has the exact same "built but unreachable" problem
  `message_info_screen.dart` had in Phase 12: its own header comment
  names the intended entry point (an `Icons.history` icon on
  `focus_mode_screen.dart`'s AppBar) but that icon isn't there, even
  though `focus_mode_screen.dart` WAS re-uploaded this same round. Also
  hits an undocumented `GET /focus-session/history/` endpoint, and uses
  its own standalone `http`/`_baseUrl` pattern instead of
  `MessageApiService` — now the third screen doing this (alongside
  `manage_parent_access_screen.dart`), worth a decision on whether to
  consolidate next time `message_api_service.dart` is touched.
- Files referenced but still not yet shared: `utils/api.dart`
  (`Api.baseUrl`), `services/auth_service.dart` (`AuthService`),
  `profile/screens/target_profile.dart`, `profile/api_service.dart`,
  `widgets/sticker_picker_sheet.dart`. Don't assume their internals —
  ask/wait if a task needs their exact behavior. ✅ `main.dart` and
  `home.dart` WERE shared earlier (see §7.1, §5.1, §6.1); ✅
  `app_bottom_nav.dart` WAS shared and reviewed (§5.10) — both no
  longer unknowns (this bullet was stale re: `app_bottom_nav.dart`
  through Phase 10; corrected now).

---

## 11. Backend Assumptions Recap (Django side, inferred only)
- REST: Django REST Framework, JWT bearer auth, DRF's
  `PageNumberPagination` (404 on out-of-range page = end of history,
  not an error)
- Realtime: Django Channels, two consumers — `ChatConsumer`
  (`ws/chat/<conversation_id>/`) and `InboxConsumer` (`ws/inbox/`)
- Calls/media: LiveKit SFU, backend mints room tokens on
  initiate/accept/join-study-room
- Push: Firebase Cloud Messaging, **must send data-only payloads**
  (no top-level `notification` key) for calls/chat to work correctly
  in background/killed states
- AI: backend proxies to an actual AI provider for
  `/message/study-room/ai-tools/` — app never holds a provider key

---

## 12. 🔥 Phase 4 Changelog (this session)

Cross-checked against `FRONTEND_INTEGRATION_ARCHITECTURE.md`'s §1/§2/§4
checklist. Full detail is inline in the sections above (§2, §4.4, §4.9,
§5.1, §5.8, §5.11, §5.12, §6.1, §6.3, §7.4, §8.1, §10) — this is just
the flat summary.

**New files created:**
- `message/screens/message_search_screen.dart` (§5.11)
- `message/widgets/mention_suggestions_overlay.dart` (§6.3)
- `message/screens/read_receipt_privacy_screen.dart` (§5.12, not yet
  wired to an entry point — see that section)

**Existing files edited:**
- `chat_socket_service.dart` — removed 5 stray `print()` debug lines
  (§4.4). `sendMessage()`'s extra params and `sendPin()` were already
  present when reviewed, not newly added.
- `push_notification_service.dart` — added `mention` + `chat_digest`
  push-type handling, foreground AND background, plus a new `mentions`
  Android channel (§4.9).
- `conversations_screen.dart` — added a distinct "search messages" app
  bar icon → `MessageSearchScreen()` global mode (§5.1). Draft-preview
  and everything else here was already complete when reviewed.

**Reviewed, found already complete, NOT changed:**
- `forward_message_screen.dart` (caption + poll-exclude already done)
- `message_cache_service.dart` (model round-trip already handles all
  new fields, no code change needed)
- `chat_screen.dart`, `message_api_service.dart`, `message_models.dart`
  (fully wired/Phase-3-complete before this session started)

**Deleted (confirmed safe via `main.dart` + `home.dart` + full grep
across every uploaded file):**
- `conversations_list_screen.dart` (§5.1)
- `floating_call_bar.dart` (§6.1)

**Doc corrections made along the way** (stale info fixed, not new
behaviour): the pin/unpin socket event is actually `pin_event`, not
`message_pinned`/`message_unpinned` (§7.4, §8.1) — `chat_screen.dart`'s
real `case` was checked directly.

---

## 13. 🔥 Phase 5 Changelog (this session)

This session re-uploaded a batch of already-documented service files
(`call_manager.dart`, `message_api_service.dart`,
`push_notification_service.dart`, `study_room_call_manager.dart`,
`ai_study_service.dart`, `call_api_service.dart`, `call_kit_service.dart`,
`chat_socket_service.dart`, `inbox_socket_service.dart`,
`media_download_service.dart`, `message_cache_service.dart`,
`missed_call_watcher.dart`, `mention_suggestions_overlay.dart`,
`minimized_call_bar.dart`, `whiteboard_painter.dart`) plus this doc
itself. Purpose: diff current file contents against what the doc
claimed, per user request ("code me changes hue hain, unhe doc me
maintain karo; jo files delete hui unhe doc se hatao").

**Result: no code-behaviour drift found** in any of the 15 re-uploaded
service/widget files — method signatures, event names, endpoint paths,
and the documented root-cause fixes (§8.x, §4.6, §4.9) all still match
what's actually in the files. Nothing in §3, §4, §6, §8, §9, §11 needed
correcting.

**Real findings, both flagged above instead of silently resolved:**
1. **`conversations_list_screen.dart` re-uploaded** — Phase 4 marked it
   deleted; it came back in this session's file set. Doc updated (§2,
   §5.1, §5.10, §10) to flag this as unresolved rather than re-asserting
   either "deleted" or "active" — needs the user to clarify intent.
2. **`getOrCreateConversation` duplicate found** in
   `message_api_service.dart` — same endpoint as `startPrivateChat`, no
   known caller yet. Added to §4.1 and §10.

**Not re-uploaded this session** (so not re-verified, still resting on
Phase 4's read): `main.dart`, `home.dart`, `chat_screen.dart`,
`study_room_screen.dart`, `call_screen.dart`, `incoming_call_screen.dart`,
`group_profile_screen.dart`, `create_group_screen.dart`,
`message_search_screen.dart`, `forward_message_screen.dart`,
`media_viewer_screen.dart`, `read_receipt_privacy_screen.dart`,
`app_bottom_nav.dart`, `message_models.dart`, `study_room_models.dart`,
`conversations_screen.dart`. These were re-uploaded in the *prior*
message this session and were spot-checked then (no drift found), but
weren't line-by-line re-diffed against this doc as part of this Phase 5
pass specifically.

**Going forward**: per the user, no more files will be shared — this
doc is now the sole reference for all future work on this project.
Treat every section above as current/authoritative unless a section
explicitly says otherwise (§5.1 and §10's two Phase 5 flags above).

---

## 16. Backend Contract Source of Truth

`CHAT_APP_DOCUMENTATION.md` (the Django `message` app's own self-sufficient
doc — models/views/consumers/settings, ~1279 lines) is now available and
is the **authoritative** source for every REST path, WS event name, and
model field this frontend talks to. Where anything above (§4, §8, §11)
disagrees with that doc, the backend doc wins — treat §4/§8/§11 as this
project's *understanding* of the contract, not the contract itself.

---

## 17. 🔥 Phase 6 Changelog — Backend Contract Cross-Check

Full pass: every REST endpoint path this frontend calls, checked against
`CHAT_APP_DOCUMENTATION.md`'s §3–§6 (REST) and §8 (WebSocket) tables.

### 17.1 Real bugs found & fixed (frontend called a path the backend doesn't serve)

1. **`searchAllMessages` (`message_api_service.dart`)** — was
   `GET /message/search_all/`. Backend doc §7.1 confirms global search is
   an **action on `ConversationViewSet`**, so it's namespaced under that
   viewset like every other action: `GET /message/conversations/search_all/`.
   The old path would 404 — global message search (`message_search_screen.dart`'s
   `conversationId == null` mode) was broken end-to-end. **Fixed.**
2. **Call-history family, 4 methods across 2 files** — `CallHistoryViewSet`
   is router-registered at `calls/history/` (confirmed repeatedly in
   backend doc §6 "Calls": `GET /calls/history/`, `GET /calls/history/missed/`,
   `GET /calls/history/<id>/addable-participants/`,
   `POST /calls/history/<id>/add-participant/`). The bare `calls/` prefix
   is reserved for the two plain-`path()` views, `CallInitiateView`
   (`/calls/initiate/`) and `CallActionView` (`/calls/<id>/action/`) —
   those two were already correct. The 4 that were wrong (missing
   `history/`), all now fixed:
   - `call_api_service.dart` → `getCallStatus(callId)` — used by
     `CallManager._startCallStatusPoll()`'s 2s reject-detection poll
     (§8.10/§4.6 above) — this was **always 404ing**, meaning "receiver
     rejects → caller keeps ringing until the 30s no-answer timeout"
     instead of the documented ~1.6s "Line busy" fast-path. Real
     user-facing bug, not just a doc mismatch.
   - `call_api_service.dart` → `getAddableParticipants(callId)` — group-call
     "Add participant" picker always 404'd, sheet would show empty/error.
   - `call_api_service.dart` → `addParticipant(callId, userId)` — same.
   - `call_api_service.dart` → `getMissedCalls({since})` — silently
     swallowed the 404 (it's wrapped in try/catch returning `[]` by
     design, §4.10), so `MissedCallWatcher` never actually showed a
     missed-call notification on reconnect, but never *errored* either —
     the quiet-failure design masked this one.
   - `message_api_service.dart` → `getCallHistory()` / `getCallDetail(id)`
     — same fix, not yet confirmed to have an active caller in any
     uploaded screen (no call-history list screen has been shared), so
     impact unconfirmed but path corrected regardless.

   Files: `call_api_service.dart`, `message_api_service.dart` (both
   provided as corrected downloads this turn — see chat).

### 17.2 Checked and confirmed already correct (no change needed)

- **Message pin/unpin** (`message_api_service.dart`) — already fixed in
  an earlier, unseen session (`🔧 FIX (backend mismatch)` comments already
  present in the file): `POST`/`DELETE /message/messages/<id>/pin/`
  (message-level action), `GET /message/conversations/<id>/pinned/` for
  the list. Matches backend doc §4/§7.2 exactly. **This doc's own §4.1
  table (written in Phase 4/5, before this cross-check) still showed the
  stale `/conversations/<id>/pins/` shape — that table entry is corrected
  below, §17.3.**
- **`pin_event` WS event name** — double-checked against backend doc §7.2
  (loose prose: `type: "pin"`) vs §8's precise table (`type: "pin_event"`,
  method `pin_event`). §8's table is the authoritative one (it's the
  literal client-handler-method mapping) — `chat_screen.dart`'s
  `case 'pin_event':` is correct as-is. §7.2's wording is just a looser
  paraphrase elsewhere in the same backend doc, not a second real event.
- **`mentioned_users` / `mentioned_user_ids`** (`message_models.dart`) —
  `fromJson` (REST) reads nested `mentioned_users` (full `UserMini` list),
  `fromSocketEvent` (WS `chat_message`) reads `mentioned_user_ids`
  (id-only) — matches backend doc §7.3/§8 exactly (different shape per
  path, by design).
- **Poll fields** (`message_models.dart`) — `voted_by_me`, `votes_count`,
  `total_votes`, `is_closed` (via `closedAt != null`) all present and
  correctly named against backend doc §7.8's `PollSerializer` shape.
- **WS `message` event fields** (`chat_socket_service.dart`) —
  `client_id`/`message_type`/`text`/`reply_to`/`file_url`/`file_urls`/
  `thumbnail_url`/`meta` — exact match to backend doc §8's client→server
  table.
- **Draft auto-save** (`message_models.dart`'s `ConversationSettings`) —
  `draftText`/`draftUpdatedAt` via the shared `PATCH .../settings/`
  endpoint — matches backend doc §7.10 (no separate endpoint, server-set
  timestamp) exactly.
- **Disappearing messages** (`message_api_service.dart`) —
  `PATCH /message/conversations/<id>/disappearing_messages/` matches
  backend doc §3 exactly.

### 17.3 Doc corrections (this file, §4.1 table — was stale, not the code)

The Pins row in §4.1's endpoint table above still read the pre-fix shape.
Corrected to: `POST`/`DELETE /message/messages/<id>/pin/` (message-level,
not conversation-level) + `GET /message/conversations/<id>/pinned/` for
the list (not `/pins/`). *(Applied directly to §4.1 above — this
subsection is the changelog record of that edit, not a duplicate table.)*

### 17.4 Backend features confirmed to exist server-side with NO frontend surface yet

Not bugs — the backend doc marks these as already implemented and
documents their contract, but nothing in any uploaded frontend file calls
them. Flagging rather than building blind, since none of these have an
obvious existing UI slot confirmed from what's been shared:

- **`GET /message/messages/<id>/read-status/`** ("seen by" / per-user
  read receipt list, respects the read-receipt privacy toggle) — no
  frontend model or API method for this at all. Would need a
  `MessageReadStatusModel` + API method + some "message info" UI (long-press
  → "Info", WhatsApp-style) — none of that exists in any uploaded screen.
  - 🔧 **Partially resolved (Phase 12)**: `message_info_screen.dart` is
    now that UI — it calls `MessageApiService.getReadStatus(messageId)`
    expecting `List<MessageReadStatusModel>` with `isRead`/`isDelivered`/
    `readAt`/`deliveredAt`/`user` fields. **But** neither
    `message_models.dart` nor `message_api_service.dart` was re-uploaded
    this round, so the model and method's existence still can't be
    confirmed — same "not silently stubbed, flagging" situation as
    `focus_mode_screen.dart` in Phase 11 (§10). Also no confirmed
    long-press → "Info" caller anywhere in `chat_screen.dart` (not
    re-uploaded this round either) — so it's UI-complete but unverified
    both upstream (model/method) and downstream (entry point).
- **`POST /message/ai/transcribe/`** (voice-note transcription,
  `AiTranscribeThrottle`) — `ai_study_service.dart` only wraps the study-room
  summary/quiz endpoint (`generate()` → `/message/study-room/ai-tools/`,
  which itself is a naming mismatch — see §17.5). No call-site anywhere
  for voice transcription, even though `chat_screen.dart`'s `_AudioBubble`
  plays voice notes.
- **`POST /message/ai/smart-replies/`** — `MessageApiService.getSmartReplies()`
  already exists and matches the backend path/body/response shape
  exactly (§4.1 above already documents it) — but no grep hit in
  `chat_screen.dart` for a call-site was confirmed in this pass (not
  re-searched this session, flagged for follow-up, not asserted as
  definitely unwired).

### 17.5 Naming mismatch worth flagging (not fixed — endpoint doesn't exist as named on either side yet)

`ai_study_service.dart`'s `generate({mode, content})` calls
`POST /message/study-room/ai-tools/`, with an in-code comment saying "abhi
backend me ye endpoint add karna hoga" (still needs to be added). Backend
doc confirms the *real*, already-implemented endpoint for this exact
summary/quiz functionality is `POST /message/ai-study-room/`
(`AiStudyRoomView`, §6) — different path, and it's a top-level path, not
nested under `study-room/`. Two options, need a product call, not a
silent fix:
(a) point `ai_study_service.dart` at the real `/message/ai-study-room/`
endpoint (it already exists server-side, per the backend doc — this would
make Study Room AI tools work today), or
(b) if `/message/study-room/ai-tools/` was intentionally meant to be a
*different*, study-room-scoped endpoint the backend hasn't built yet,
leave as a TODO but stop referring to it as if it might already exist.
**Not changed this turn** — (a) looks clearly right (same feature, same
request/response shape: `{mode, content}` → `{summary}`/`{questions}`),
but this is a bigger behavioral change than the pure path-typo fixes in
§17.1, so flagging for explicit confirmation rather than silently
redirecting a working call-shape to a different host endpoint.

---

---

## 19. 🔥 Phase 8 Changelog (this session)

16 files re-uploaded: `call_manager.dart`, `message_api_service.dart`,
`push_notification_service.dart`, `study_room_call_manager.dart`,
`ai_study_service.dart`, `call_api_service.dart`, `call_kit_service.dart`,
`chat_socket_service.dart`, `doubts_api_service.dart`,
`inbox_socket_service.dart`, `media_download_service.dart`,
`message_cache_service.dart`, `missed_call_watcher.dart`,
`parent_service.dart`, `translate_service.dart`, `tts_service.dart`, plus
this doc. Purpose (per user): bring the doc fully in line with actual
current file logic so **all future work is driven off this doc**, not
re-reads of the code.

### 19.1 Genuinely new files/features (zero prior doc coverage — now documented)
- `doubts_api_service.dart` — Doubt Queue feature, entirely new. §4.13,
  §2.
- `parent_service.dart` — Feature 8, Parent/Guardian Mode. §4.14, §2.
- `translate_service.dart` — Feature 9, message translate. §4.15, §2.
- `tts_service.dart` — Feature 10, client-only TTS. §4.16, §2, §1.
- `ai_study_service.dart`: `generateRevisionDeck`/`getSavedRevisionDeck`
  (Feature 5, "Revision Deck") and `transcribe()` (manual voice-note
  transcription fallback, the endpoint §17.4 had flagged as backend-only)
  — neither was in the doc before. §4.3.
- `call_api_service.dart`: `getStudyRoomStreak` (Feature 6, attendance
  streak) — not in the doc before. §4.2.

### 19.2 Real behaviour drift found between doc and code (not just missing docs)
1. **`call_api_service.dart` baseUrl** — doc still described a separate
   hardcoded LAN-IP constant (`"http://10.224.54.189:8000"`). Current
   code has this already fixed: `baseUrl` is a getter delegating to
   `Api.baseUrl`. Doc corrected, §4.2.
2. **`call_api_service.dart` endpoint paths in §4.2 itself** — the
   `history/` segment fix was recorded in §17.1's changelog narrative
   but §4.2's own method list still showed the pre-fix bare-`calls/`
   paths. Corrected to match what's actually in the code
   (`getCallStatus`, `getAddableParticipants`, `addParticipant`,
   `getMissedCalls` all now show `calls/history/...`).
3. **`chat_socket_service.dart` reconnect** — doc had no reconnect
   behaviour documented at all for this file (it only described
   `inbox_socket_service.dart` as having reconnect). Code now has full
   reconnect logic (2–30s capped backoff, `AuthService.getValidToken()`,
   explicit-disconnect vs. unexpected-drop distinction) — per the file's
   own header comment this was **added this session** (previously there
   was none at all, a real bug: token expiry or network blips left the
   chat socket permanently dead). Documented, §4.4.
4. **`inbox_socket_service.dart` reconnect** — doc said "auto-reconnect
   every 4s". Code now uses capped exponential backoff (4–60s) plus
   `AuthService.getValidToken()` instead of a raw token getter — fixes a
   silent tight-infinite-loop failure mode on token expiry that existed
   before. Documented, §4.5.

### 19.3 Confirmed unchanged (spot-checked, no drift)
`call_kit_service.dart` (full-screen-intent handling, event routing,
`_waitForNavigator` retry logic), `missed_call_watcher.dart`
(connectivity-transition detection, `since`-timestamp persistence),
`media_download_service.dart` (Android 11+ `MANAGE_EXTERNAL_STORAGE`
gating, gallery vs. public-Downloads split), `message_cache_service.dart`
(30-conversation cap, 7-day message TTL, dropped-conversation cache
cleanup) — all match what §4.8/§4.10/§4.11/§4.12 already documented.
Not re-diffed line-by-line this pass: `call_manager.dart`,
`message_api_service.dart`, `push_notification_service.dart`,
`study_room_call_manager.dart` — these 4 were re-uploaded but their full
content wasn't available for this pass; only spot-checked (grep) for
call-sites of the Phase 8 features above, and none were found (Doubt
Queue, Parent Mode, Translate, TTS, Revision Deck, and Streak all remain
**service-layer only, with no confirmed screen/manager call-site yet**).
Treat these 4 files' existing §4.1/§4.6/§4.7/§4.9 documentation as
resting on Phase 5's prior full read, not re-verified this session.

### 19.4 Going forward
Per the user, this doc is the **sole basis for all future work** —
new logic/process gets built against what's written here, and any doc
edit going forward should be an update/extend of the relevant section
(matching what §19.1–§19.3 just did), not a full rewrite.

---

## 20. 🔥 Phase 9 Changelog (this session)

7 files re-uploaded: `message_models.dart`, `language_picker_sheet.dart`
(new), `mention_suggestions_overlay.dart`, `minimized_call_bar.dart`,
`translatable_message_widgets.dart` (new), `whiteboard_painter.dart`,
`study_room_models.dart`. Same goal as Phase 8: close doc/code gaps,
this doc stays the sole reference.

### 20.1 Genuinely new files (zero prior doc coverage — now documented)
- `language_picker_sheet.dart` — Feature 9 support (language list +
  preferred-lang persistence + picker sheet). §6.4, §2.
- `translatable_message_widgets.dart` — `ListenButton` + `TranslateToggle`,
  the first actual UI for Features 9/10 (previously service-layer only
  per §19.1). §6.5, §2.

### 20.2 Previously-flagged model gaps, now confirmed resolved
Two build-blocker flags from Phase 8 are now closed — the models exist:
1. **`DoubtQuestionModel`** (§4.13's flag) — confirmed present in
   `message_models.dart`. Fields + `displayName()`/`copyWith` documented
   in §3.1.
2. **`RevisionDeckModel` / `FlashcardModel`** (§4.3's flag) — confirmed
   present in `study_room_models.dart`. Documented in §3.2. Also found
   (not previously flagged, since §4.2 didn't call it out explicitly):
   **`StudyStreakModel`** is present too, backing `getStudyRoomStreak()`
   — documented in §3.2.

None of the 4 files spot-checked-but-not-fully-read in Phase 8
(`call_manager.dart`, `message_api_service.dart`,
`push_notification_service.dart`, `study_room_call_manager.dart`) were
re-uploaded this round either, so their call-site status for Doubt
Queue / Parent Mode / Translate / TTS / Revision Deck / Streak is
**still unconfirmed** — those 6 features remain service+model layer
only until one of those 4 files (or the actual message-bubble widget,
also still not uploaded) is shared.

### 20.3 Confirmed unchanged (spot-checked, no drift)
`whiteboard_painter.dart` (freehand/highlighter/eraser rendering,
shape drawing incl. arrowhead math, `previewShape` live-drag preview),
`mention_suggestions_overlay.dart` (`extractMentionQuery`/
`insertMention` logic, prefix-sorted suggestion list),
`minimized_call_bar.dart` (global-`navigatorKey` fix, `isActive &&
isMinimized` visibility gate) — all match §3.2/§6.1/§6.2/§6.3 exactly as
already documented. `study_room_models.dart`'s previously-documented
classes (`ToolType`, `DrawingPoint`, `ShapeElement`, `TextElement`,
`StickyNoteModel`, `UserProfileWindowModel`, `WhiteboardPage`,
`StudyTimerState`, `TranscriptSegmentModel`) also all unchanged.
`message_models.dart`'s previously-documented classes (§3.1) unchanged
too — this pass only added the new `DoubtQuestionModel` section and a
one-line `SmartReplyModel` entry that existed in code but had no §3.1
entry before (it was only mentioned narratively in §17.4).

---

*(End of current doc — extend sections above as new files/behaviour
are confirmed. Don't restart numbering; append sub-sections like §5.13,
§7.19, §17.6, §19.5, §20.4, §21.2 etc. as needed.)*

---

## 21. 🔥 Phase 10 Changelog (this session)

8 files re-uploaded: `call_screen.dart`, `chat_screen.dart`,
`conversations_screen.dart`, `create_group_screen.dart`,
`doubts_screen.dart` (new), `app_bottom_nav.dart`,
`chat_screen_pagination_fix.dart`, `class_transcript_screen.dart`.
User also explicitly asked to re-check **screen-to-screen wiring**
(navigation/push call-sites), not just per-file internals.

### 21.1 Genuinely new (zero prior doc coverage — now documented)
- `doubts_screen.dart` — the Doubt Queue's first and only screen. Closes
  the "service+model only" gap §4.13/§20.2 had flagged. Full writeup:
  §5.14. Pushed from `chat_screen.dart`'s new AppBar icon: §5.2, §7.20.
- `doubt_event` WS event (server→client, `ws/chat/<id>/`) — added to
  §8.1. Not previously documented at all.
- `ChatSocketService` "up to three concurrent connections per
  conversation" fact (chat + study room + now doubts) — §4.4 updated
  from "two" to "three" and to name all three call-sites.

### 21.2 Confirmed unchanged (spot-checked against doc claims, no drift)
- `chat_screen_pagination_fix.dart` — the `_loadMoreMessages()` catch
  block in the re-uploaded `chat_screen.dart` matches this patch file
  **exactly**, line-for-line logic (404→`_hasMoreMessages=false`,
  non-404→snackbar). §12/§5.2's "already merged, Phase 4" claim holds;
  nothing to change.
- `class_transcript_screen.dart` — matches §5.13 exactly (debounce
  timing, `searchTranscript` call shape, `timeLabel` badge,
  tap-to-play-segment behaviour, empty-state copy). No drift.
- `app_bottom_nav.dart` — matches §5.10 exactly (4-tab bar, `AppTab`
  enum, index-remapping logic for the Chats-is-a-separate-push case).
  No drift.
- `call_screen.dart` — spot-checked the specific claims §5.3 makes
  (`_pipOffset`, `_noAnswerTimer` at 35s, `_AddParticipantSheet`) — all
  present, unchanged. Not re-read line-by-line beyond those claims.
- `conversations_screen.dart` — spot-checked §5.1's claims
  (`InboxSocketService.instance.connect()`, `AppBottomNav(current:
  AppTab.chats)`, `MessageSearchScreen()` push, `CreateGroupScreen()`
  push, `mySettings.draftText` usage) — all present, unchanged.
- `create_group_screen.dart` — spot-checked §5.7's claims
  (`MessageApiService.searchUsers`, `_createGroup()` →
  `MessageApiService.createGroup(...)`) — present, unchanged.

### 21.3 Real doc-internal drift found and fixed (not code drift — the doc contradicted itself)
`chat_screen.dart`'s §5.2 write-up still listed the socket-event names
`message_pinned`/`message_unpinned` in its `_handleSocketEvent` switch
summary, even though §8.1 had already been corrected (an earlier
session) to record the actual event name as `pin_event`. The code was
never wrong — only §5.2's prose hadn't been updated to match §8.1's
correction. Fixed so both sections now agree.

### 21.4 Confirmed still-open gaps (unchanged from before, re-verified this pass)
- `translatable_message_widgets.dart`'s `ListenButton`/`TranslateToggle`
  are **still not called anywhere** in the re-uploaded `chat_screen.dart`
  — grepped both class names across all 8 files this round, zero hits
  outside their own definition file. Features 9/10 remain built but not
  user-reachable. Explicitly noted in §5.2 now (previously this silence
  wasn't called out at all).
- `conversations_list_screen.dart`'s deleted-vs-active status (§5.1) —
  not re-uploaded this round either way, still unresolved, still
  flagged in §10.
- The 4 files from Phase 8 that were spot-checked-only
  (`call_manager.dart`, `message_api_service.dart`,
  `push_notification_service.dart`, `study_room_call_manager.dart`) were
  not re-uploaded this round either — their Doubt Queue / Parent Mode /
  Translate / TTS / Revision Deck / Streak call-site status is still
  unconfirmed.

---

## 22. 🔥 Phase 12 Changelog (this session)

15 files uploaded (incl. this doc): `focus_mode_screen.dart`,
`forward_message_screen.dart`, `group_media_screen.dart`,
`group_profile_screen.dart`, `incoming_call_screen.dart`,
`manage_parent_access_screen.dart` (new), `media_viewer_screen.dart`,
`message_info_screen.dart` (new), `message_search_screen.dart`,
`parent_code_entry_screen.dart` (new), `parent_dashboard_screen.dart`
(new), `read_receipt_privacy_screen.dart`, `revision_deck_screen.dart`
(new), `study_room_screen.dart`, `PROJECT_ARCHITECTURE.md` itself. Same
instruction as
Phases 8–11: this doc stays the sole reference — new logic gets
documented here as it's found, existing sections get extended/corrected
in place rather than rewritten, and every future task builds off what's
written here rather than off memory of the code.

### 22.1 Genuinely new files (zero prior doc coverage — now documented)
- `revision_deck_screen.dart` — Feature 5 UI (flashcards + quiz),
  confirmed wired from `study_room_screen.dart`'s AI Tools sheet. §5.17,
  §2, §4.3 (resolution note added inline).
- `message_info_screen.dart` — "seen by" / message-info UI, the first
  frontend surface for the read-status endpoint §17.4 flagged as
  backend-only. §5.18, §2, §17.4 (resolution note added inline).
- `parent_code_entry_screen.dart` + `parent_dashboard_screen.dart` —
  first confirmed UI for `parent_service.dart` (Feature 8, parent side).
  §5.19, §2, §4.14 (resolution note added inline).
- `manage_parent_access_screen.dart` — Feature 8, student side (code
  generation/revocation). Genuinely separate code path from
  `parent_service.dart` — own `http` calls, own placeholder base URL,
  own endpoint family (`/parent/codes/`) not previously documented
  anywhere in this doc. §5.20, §2, §4.14, §10.

### 22.2 Previously-flagged gaps, now confirmed resolved (fully)
1. **Feature 5 "Revision Deck" screen** — Phase 8/9 (§19.3/§20.2) had
   this as "service+model layer only, no confirmed screen/manager
   call-site". `revision_deck_screen.dart` + its wiring from
   `study_room_screen.dart` close this completely: model ✅
   (Phase 9), service methods ✅ (Phase 8), screen ✅ (Phase 12),
   caller ✅ (Phase 12, same file this round). Feature 5 is now
   end-to-end confirmed.
2. **Feature 8 "Parent Mode", parent-facing half** — `parent_service.dart`
   (Phase 8, §4.14) previously had "no screen uploaded for this yet".
   `parent_code_entry_screen.dart`/`parent_dashboard_screen.dart` close
   that half. The student-facing half (generating a code) is now ALSO
   covered by `manage_parent_access_screen.dart`, but see §22.3 — it
   raises a new question rather than fully closing the loop.

### 22.3 Previously-flagged gaps, now partially resolved (new questions raised)
- **"Message info" UI** (§17.4's first bullet) — UI now exists
  (`message_info_screen.dart`), but the model (`MessageReadStatusModel`)
  and method (`MessageApiService.getReadStatus`) it depends on weren't
  re-confirmed this round (`message_models.dart`/`message_api_service.dart`
  not re-uploaded), and no caller (`chat_screen.dart` long-press → "Info")
  was re-confirmed either. Treat as a potential build blocker exactly
  like Phase 11's `focus_mode_screen.dart` situation until those files
  are shared again. See §10, §17.4.
- **Feature 8, student side** — a screen to generate parent codes now
  exists, but it doesn't reuse `parent_service.dart` and hits a route
  (`/parent/codes/`) never documented before in this doc's backend
  contract cross-check (§17). Needs backend confirmation, and ideally a
  decision on whether `manage_parent_access_screen.dart` should be
  refactored to share `parent_service.dart`'s networking pattern instead
  of its own separate `http`/`_baseUrl`/auth-header code. See §4.14,
  §10.

### 22.4 Confirmed unchanged (spot-checked, no drift)
- `forward_message_screen.dart`, `media_viewer_screen.dart`,
  `read_receipt_privacy_screen.dart`, `focus_mode_screen.dart`,
  `group_media_screen.dart` — all read in full this round; match §5.8,
  §5.9, §5.12, §5.15, §5.16 exactly, no drift.
- `incoming_call_screen.dart` — spot-checked the specific method names
  §5.4 claims (`showIfNeeded`, `_onDragUpdate`/`_onDragEnd`/
  `_springBack`/`_completeSwipe`, `_startRinging`/`_stopRinging`,
  `_accept`/`_reject` → `CallApiService.callAction`) — all present,
  unchanged.
- `group_profile_screen.dart` — spot-checked §5.6/§5.16's claims
  (`GroupMediaScreen` push from the "Media, links and docs" card,
  `rejectJoinRequest`) — present, unchanged. Grew from ~1400+ to 1600
  lines; not re-read line-by-line beyond the spot-checked claims.
- `message_search_screen.dart` — not re-diffed line-by-line this pass;
  no contradicting evidence found, §5.11 claims left standing.
- `study_room_screen.dart` — confirmed the new `RevisionDeckScreen`
  wiring (import, `_openRevisionDeckScreen`, AI Tools sheet entry,
  `_collectBoardTextContent` reused as the `boardContentBuilder`
  callback per that file's own header comment) — this is the one real
  *addition* found in an already-documented file this round, now
  reflected in §5.17/§4.3. Grew from ~4600 to 5034 lines; the rest of
  the file wasn't re-diffed beyond this addition and the areas §5.5/§7
  already cite.

### 22.5 Confirmed still-open gaps (unchanged from before, re-verified this pass)
- `focus_mode_screen.dart`'s missing `message_api_service.dart` methods
  (§10's Phase 11 build blocker) — `message_api_service.dart` still not
  re-uploaded, still unconfirmed either way.
- `conversations_list_screen.dart`'s deleted-vs-active status (§5.1,
  §10) — still unresolved.
- The 4 files spot-checked-only since Phase 8 (`call_manager.dart`,
  `message_api_service.dart`, `push_notification_service.dart`,
  `study_room_call_manager.dart`) — still not re-uploaded. Doubt Queue /
  Translate / TTS / Streak call-site status still unconfirmed (Parent
  Mode and Revision Deck are no longer on this list — see §22.2).

### 22.6 Going forward
Unchanged from §19.4: this doc is the sole basis for all future work.
Extend the relevant section when new files/behaviour show up; append a
new `§23 Phase 13 Changelog` (don't renumber or restart) when that
happens.

---

## 23. 🔥 Phase 13 Changelog (this session)

19 files uploaded (incl. this doc): `app_bottom_nav.dart`,
`call_screen.dart`, `chat_screen.dart`, `class_transcript_screen.dart`,
`conversations_screen.dart`, `create_group_screen.dart`,
`doubts_screen.dart`, `focus_mode_screen.dart`,
`focus_session_history_screen.dart` (new), `forward_message_screen.dart`,
`group_media_screen.dart`, `group_profile_screen.dart`,
`incoming_call_screen.dart`, `manage_parent_access_screen.dart`,
`media_viewer_screen.dart`, `message_info_screen.dart`,
`message_search_screen.dart`, `parent_code_entry_screen.dart`,
`parent_dashboard_screen.dart`, `PROJECT_ARCHITECTURE.md` itself. Same
instruction as every prior phase: this doc stays the sole reference —
new logic gets documented here as it's found, existing sections get
extended/corrected in place rather than rewritten.

### 23.1 Genuinely new file (zero prior doc coverage — now documented)
- `focus_session_history_screen.dart` — Feature 12 gap fix, past
  focus-session history list. §5.21, §2, §10 (orphan-screen flag added
  inline).

### 23.2 Real logic changes found in re-uploaded files
- `focus_mode_screen.dart` — the `FocusSessionStatus` DTO **moved** out
  of this file into `message_models.dart` (stated reason: shared return
  type for `message_api_service.dart`'s focus-session methods, avoiding
  a circular import). This is a genuine code change, not just doc
  drift — §5.15 updated in place, and the previously-flagged dead
  `inactive()` factory (§10) is no longer present in this file. See
  §5.15, §10.
- `chat_screen.dart` — `_showMessageActions`'s bottom sheet now has an
  "Info" `ListTile` (own messages only) that pushes
  `MessageInfoScreen(messageId, messagePreview)`. This resolves the
  Phase 12 "no confirmed caller" gap for `message_info_screen.dart`
  (§5.2, §10, §22.3). File grew ~5000 → 5676 lines; only this addition
  was diffed in, rest not re-checked line-by-line this pass.

### 23.3 Previously-flagged gaps, now confirmed resolved
1. **`message_info_screen.dart` navigation entry point** (§22.3/§10) —
   closed by the `chat_screen.dart` change in §23.2 above. The
   screen's own backend dependency (`getReadStatus`/
   `MessageReadStatusModel`) remains unconfirmed since
   `message_api_service.dart`/`message_models.dart` weren't
   re-uploaded — narrowed, not fully closed. See §10.

### 23.4 Previously-flagged gaps, downgraded but not closed
- **Focus Mode build blocker** (§10, Phase 11) — the DTO relocation in
  §23.2 is indirect evidence `message_api_service.dart` now has
  `getFocusStatus`/`startFocusSession`/`cancelFocusSession`, but that
  file itself hasn't been re-uploaded since Phase 8. Downgraded from
  "confirmed missing" to "probably added, unconfirmed" — still don't
  build on top of it without seeing the file.

### 23.5 New gap raised this session
- **`focus_session_history_screen.dart` is an orphan** — same shape as
  Phase 12's `message_info_screen.dart` gap, except NOT resolved this
  round: `focus_mode_screen.dart` (its intended caller, per the new
  file's own header comment) was re-uploaded this same session and
  still has no history-icon entry point. Also introduces a third
  screen (`manage_parent_access_screen.dart` was the second) using its
  own standalone `http`/`_baseUrl` pattern instead of
  `MessageApiService`, and hits an endpoint (`GET
  /focus-session/history/`) undocumented anywhere else in this doc.
  See §5.21, §10.

### 23.6 Confirmed unchanged / not re-diffed this pass
- `app_bottom_nav.dart`, `media_viewer_screen.dart`,
  `forward_message_screen.dart`, `group_media_screen.dart` — spot-
  checked, match §5.10, §5.9, §5.8, §5.16 respectively, no drift found.
- `call_screen.dart`, `conversations_screen.dart`, `create_group_screen.dart`,
  `doubts_screen.dart`, `group_profile_screen.dart`,
  `incoming_call_screen.dart`, `manage_parent_access_screen.dart`,
  `message_search_screen.dart`, `class_transcript_screen.dart`,
  `parent_code_entry_screen.dart`, `parent_dashboard_screen.dart` —
  spot-checked for markers relevant to the two changes in §23.2 (none
  found), but **not** re-diffed line-by-line against their existing
  sections (§5.3, §5.1, §5.7, §5.14, §5.6, §5.4, §5.20, §5.11, §5.13,
  §5.19) this round. Treat those sections as still accurate but not
  freshly re-verified in full.

### 23.7 Going forward
Unchanged from prior phases: this doc is the sole basis for all future
work. Extend the relevant section when new files/behaviour show up;
append a new `§24 Phase 14 Changelog` (don't renumber or restart) when
that happens.

---

## 24. 🔥 Phase 14 Changelog (this session)

16 files uploaded (incl. this doc): `PROJECT_ARCHITECTURE.md` itself,
`ai_study_service.dart`, `call_api_service.dart`, `call_kit_service.dart`,
`call_manager.dart`, `chat_socket_service.dart`,
`language_picker_sheet.dart`, `mention_suggestions_overlay.dart`,
`message_api_service.dart`, `message_models.dart`,
`minimized_call_bar.dart`, `parent_service.dart`,
`study_room_models.dart`, `translatable_message_widgets.dart`,
`whiteboard_painter.dart`, `whiteboard_painter.dart` (dup listing,
single file). Same instruction as every prior phase: this doc stays
the sole reference.

### 24.1 Headline: the two long-standing "spot-checked-only since
Phase 8" files are finally re-uploaded
`call_manager.dart` and `message_api_service.dart` (§22.5/§23.6's
recurring "still not re-uploaded" bullet) are both back this round,
along with `message_models.dart`. This is what actually moved the
doc forward this pass — the other 12 re-uploaded files
(`ai_study_service.dart`, `call_api_service.dart`,
`call_kit_service.dart`, `chat_socket_service.dart`,
`language_picker_sheet.dart`, `mention_suggestions_overlay.dart`,
`minimized_call_bar.dart`, `parent_service.dart`,
`study_room_models.dart`, `translatable_message_widgets.dart`,
`whiteboard_painter.dart`) were diffed against their existing sections
(§3.2, §4.2, §4.3, §4.4, §4.8, §4.14, §6.1–§6.5) and matched exactly —
**no drift found in any of them**, all still accurate as documented.

### 24.2 Real logic/contract changes found (not just doc catch-up)
- **`message_api_service.dart` — poll endpoints changed shape.**
  `createPoll` now hits `POST /message/conversations/<id>/poll/`
  (singular) instead of the previously-documented `/polls/`.
  `votePoll`/`closePoll` are now keyed by the poll's **message id**
  (`POST /message/messages/<id>/poll/vote|close/`), not a separate poll
  id — `votePoll`'s first param was renamed `messageId` accordingly.
  The old standalone `getPoll(pollId)` method has been **deleted from
  the code**, with a comment explaining that endpoint never existed
  backend-side. See §4.1, §7.5, §10.
- **`message_api_service.dart` — study-room-state endpoint path
  changed.** `saveStudyRoomState`/`getStudyRoomState`/
  `endStudyRoomState` now hit `/message/study-room/<id>/state/`, not
  the previously-documented `/message/conversations/<id>/
  study-room-state/`. See §4.1, §10.
- **`message_api_service.dart` — `forwardMessages` gained a `caption`
  param.** Resolves what would otherwise be a build-time mismatch with
  `forward_message_screen.dart` (which the file's own comment says was
  already calling it with `caption`). See §4.1, §10.
- **`message_api_service.dart` — Focus Mode methods confirmed present
  and correct.** `getFocusStatus()`/`startFocusSession()`/
  `cancelFocusSession()` all exist, hitting GET/POST/DELETE
  `/message/focus-session/`, sharing `FocusSessionStatus` from
  `message_models.dart`. **Closes the Phase 11 build blocker for good**
  (was "probably added, unconfirmed" since Phase 13). See §4.1, §10.
- **`message_api_service.dart` — new `getReadStatus(messageId)`
  method**, GET `/message/messages/<id>/read-status/` →
  `List<MessageReadStatusModel>`. This is the method
  `message_info_screen.dart` (§5.18) needs — but see the new gap below.
- **`message_models.dart` — several genuinely new models**, not
  previously documented anywhere in this doc: `LinkPreviewModel`,
  `SearchFilterModel`, `ConversationPreviewModel`, `SearchResultModel`.
  `searchAllMessages()` now returns `List<SearchResultModel>` (message
  + optional cross-chat preview), not `List<MessageModel>`. See §3.1,
  §4.1.
- **`message_models.dart` — `FocusSessionStatus` relocation directly
  confirmed.** Phase 13 could only infer this from
  `focus_mode_screen.dart`'s side; now that `message_models.dart` is
  re-uploaded, the DTO (and its dead `inactive()` factory) is confirmed
  present there. See §3.1, §10.

### 24.3 New build blocker found (narrows, but doesn't close, a
previous flag)
- 🔴 **`MessageReadStatusModel` is referenced but never defined.**
  `message_api_service.dart`'s new `getReadStatus()` (§24.2 above)
  returns `List<MessageReadStatusModel>` and calls
  `MessageReadStatusModel.fromJson(...)`, but a full grep of the
  re-uploaded `message_models.dart`'s class list shows no such class
  anywhere. `message_info_screen.dart` (§5.18, whose "Info" entry point
  in `chat_screen.dart` was already confirmed wired in Phase 13) will
  not compile until this class is added. This was previously an
  "unconfirmed, both files missing" flag (§10) — now it's a confirmed,
  specific, single-class gap. See §3.1, §4.1, §5.18, §10.

### 24.4 Confirmed unchanged (spot-checked, no drift)
- `ai_study_service.dart` — matches §4.3 exactly (`generate`,
  `registerTranscriptChunk`, `searchTranscript`,
  `askClassroomCopilot`, `generateRevisionDeck`/`getSavedRevisionDeck`,
  `transcribe`).
- `call_api_service.dart` — matches §4.2 exactly (`initiateCall`,
  `callAction`, `getCallStatus`, `getAddableParticipants`,
  `addParticipant`, `getMissedCalls`, `getStudyRoomStreak`,
  `joinStudyRoom`).
- `call_kit_service.dart` — matches §4.8 exactly (`init`,
  `_ensureFullScreenIntentPermission`, `showIncomingCall`,
  `_onCallKitEvent`, `_acceptAndNavigate`/`_waitForNavigator`,
  `endCallUiByCallId`).
- `call_manager.dart` — full method list checked against §4.6's
  summary (state groups, hold, waiting-call, reconnect countdown,
  no-answer timer, call-status poll, screen share, add participant,
  end/cleanup) — no new methods or removed methods found; treat §4.6
  as still accurate.
- `chat_socket_service.dart` — matches §4.4 exactly, including the
  Phase 8 reconnect-logic rewrite (backoff 2s–30s,
  `AuthService.getValidToken()`, `_manuallyDisconnected` flag).
- `parent_service.dart` — matches §4.14 exactly, **including** the
  still-unresolved placeholder `_baseUrl =
  'https://YOUR_API_HOST/message'` (still not wired to `Api.baseUrl` —
  flag stands).
- `study_room_models.dart` — matches §3.2 exactly, all classes present
  and unchanged (`ToolType`, `DrawingPoint`, `ShapeElement`,
  `TextElement`, `StickyNoteModel`, `UserProfileWindowModel`,
  `WhiteboardPage`, `TranscriptSegmentModel`, `FlashcardModel`,
  `RevisionDeckModel`, `StudyStreakModel`, `StudyTimerState`).
- `whiteboard_painter.dart`, `mention_suggestions_overlay.dart`,
  `language_picker_sheet.dart`, `minimized_call_bar.dart`,
  `translatable_message_widgets.dart` — all match §6.2–§6.5/§6.1
  exactly, no drift.

### 24.5 Going forward
Unchanged from every prior phase: this doc is the sole basis for all
future work. Extend the relevant section when new files/behaviour show
up; append a new `§25 Phase 15 Changelog` (don't renumber or restart)
when that happens. Priority for next session: get
`MessageReadStatusModel` added to `message_models.dart` (§24.3) so
`message_info_screen.dart` actually compiles, and get
`message_search_screen.dart`/`focus_mode_screen.dart`/
`conversations_screen.dart` re-uploaded to confirm they've adapted to
the `SearchResultModel`/`FocusSessionStatus` contract now that the
service/model layer is confirmed.