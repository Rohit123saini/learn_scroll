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
  widgets/
    minimized_call_bar.dart    — minimized-call pill (the current/correct implementation)
    whiteboard_painter.dart    — CustomPainter for strokes/shapes on canvas
    mention_suggestions_overlay.dart — 🔥 NAYA (Phase 4): @mention autocomplete widget + `extractMentionQuery`/`insertMention` helpers
    ~~floating_call_bar.dart~~  — 🗑️ DELETED (confirmed unwired, §6.1)
```

> **Phase 4 update (this session)**: `chat_screen_pagination_fix.dart`
> patch is confirmed already merged into `chat_screen.dart` (404-on-
> out-of-range-page handled, no stray `print()`s) — the standalone
> patch file no longer needs tracking separately. See §12 for the full
> list of this session's changes.

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
| | POST `/message/messages/forward/` `{message_ids, conversation_ids}` | forward N msgs → N chats in one call |
| Pins | GET `/message/conversations/<id>/pinned/` | list pins (🔧 Phase 6: corrected, was documented as `/pins/`) |
| | POST/DELETE `/message/messages/<id>/pin/` | pin/unpin — message-level action, NOT under `conversations/<id>/`; max 3 pinned/conversation (backend-enforced) |
| Polls | POST `/message/conversations/<id>/polls/` | create |
| | GET `/message/polls/<id>/` | live results |
| | POST `/message/polls/<id>/vote/` `{option_ids}` | re-vote = auto-switch |
| | POST `/message/polls/<id>/close/` | creator/admin/mod only |
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
| Search | GET `/message/conversations/<id>/search/` | in-chat search |
| | GET `/message/conversations/search_all/` | global search (🔧 Phase 6: corrected, was `/message/search_all/` — see §17.1) |
| Participants | POST `/message/conversations/<id>/participants/` `{user_id}` | add to existing conversation |
| Study room state | PUT/GET/DELETE `/message/conversations/<id>/study-room-state/` `{pages}` | persist/restore/clear whiteboard pages |

> 🔥 **Phase 5 finding**: `getOrCreateConversation(targetUserId)` at the
> bottom of the file hits the exact same endpoint as `startPrivateChat`
> (`POST /message/conversations/start_private/`), just with its own
> inline `http.post` instead of reusing `_headers()`/`_decode()`, and
> throws a plain `Exception` instead of `MessageApiException`. Looks
> like a leftover/duplicate — no call-site for it was found in any file
> uploaded so far. Flagged in §10; don't assume which one is "the" one
> to call without checking where each is actually used.

### 4.2 `call_api_service.dart` — call + study-room-join REST
Base URL hardcoded here (⚠️ **NOT** shared with `Api.baseUrl` — separate
constant `CallApiService.baseUrl = "http://10.224.54.189:8000"`, a LAN
IP for testing). `ai_study_service.dart` reuses this same constant.

- `initiateCall(conversationId, type)` → POST `/message/calls/initiate/`
- `callAction(callId, action)` → POST `/message/calls/<id>/action/`
  (`action`: accept/reject/end). `end` never throws to caller (returns
  `{"status":"ended"}` on failure) — call teardown must never crash.
- `getCallStatus(callId)` → GET `/message/calls/<id>/` — polled every 2s
  by caller while ringing (see §8.10 reject-detection)
- `getAddableParticipants(callId)` → GET `/message/calls/<id>/addable-participants/`
- `addParticipant(callId, userId)` → POST `/message/calls/<id>/add-participant/`
- `getMissedCalls({since})` → GET `/message/calls/missed/?since=` —
  best-effort, swallows errors, returns `[]`
- `joinStudyRoom(conversationId, {newSession})` → POST
  `/message/study-room/<conversationId>/join/` `{new_session: bool}` —
  returns `{livekit_url, livekit_token}`. No ringing/accept — silent
  connect like a Meet link.

### 4.3 `ai_study_service.dart`
- `generate({mode, content})` → POST `/message/study-room/ai-tools/`
  `{mode: 'summary'|'quiz', content}` → `{summary}` or `{questions:[...]}`.
  Backend calls the actual AI provider — app never holds an AI API key.

### 4.4 `chat_socket_service.dart` — per-conversation WebSocket
Connects: `ws(s)://<host>/ws/chat/<conversation_id>/?token=<JWT>`
(derived from `Api.baseUrl`). One instance per open `ChatScreen`
**and** per open `StudyRoomScreen` (both use the same conversation's
socket channel — study room events are a passthrough on the same
socket, not a separate connection).

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

### 4.5 `inbox_socket_service.dart` — GLOBAL singleton WebSocket
Connects: `ws(s)://<host>/ws/inbox/?token=<JWT>`. Singleton
(`InboxSocketService.instance`), idempotent `connect()`, auto-reconnect
every 4s on drop. Meant to be alive for the WHOLE app session (ideally
connected right after login), currently connected lazily from
`ConversationsScreen.initState()`. Purpose: lets the chat LIST screen
know about new messages/updates in ANY conversation without having
that specific `ChatScreen` open. Event shape: `inbox_update` (consumed
by `ConversationsScreen._onInboxUpdate`).

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
  `message_pinned`/`message_unpinned`, `conversation_wallpaper_updated`,
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
Create → `createPoll()` → `_insertPollMessage(poll)` locally. Vote →
`votePoll(pollId, optionIds)` (re-vote auto-replaces prior vote
server-side). Both create and vote also arrive as `poll_created`/
`poll_voted` socket events for OTHER participants
(`_onPollCreatedEvent`/`_onPollVotedEvent`). **Gotcha**: historical
(paginated/scrolled-to) poll messages come back from
`getMessages()`/history WITHOUT poll data (`meta` empty) — only live
socket events or an explicit `getPoll(pollId)` call populate
options/votes; a scrolled-up old poll renders read-only "📊 question"
until refreshed.

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
`incoming_call`, `error`

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
- `CallApiService.baseUrl` is a **separate hardcoded constant** (LAN IP
  `10.224.54.189:8000`) from `Api.baseUrl` (used by
  `message_api_service.dart`, `chat_socket_service.dart`, etc.) — these
  need to point at the same backend; worth unifying into one config
  source when moving past local testing.
- `chat_screen.dart`'s `_enterStudyRoom` passes `initialParticipants:
  const []` with a `// TODO` to actually populate it from the group's
  member list.
- Files referenced but still not yet shared: `utils/api.dart`
  (`Api.baseUrl`), `services/auth_service.dart` (`AuthService`),
  `profile/screens/target_profile.dart`, `profile/api_service.dart`,
  `widgets/sticker_picker_sheet.dart`, `app_bottom_nav.dart` (referenced
  by `conversations_screen.dart`, content not yet reviewed). Don't
  assume their internals — ask/wait if a task needs their exact
  behavior. ✅ `main.dart` and `home.dart` WERE shared this session (see
  §7.1, §5.1, §6.1) — no longer unknowns.

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

*(End of current doc — extend sections above as new files/behaviour
are confirmed. Don't restart numbering; append sub-sections like §5.13,
§7.19, §17.6 etc. as needed.)*