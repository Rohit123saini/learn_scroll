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
    conversations_screen.dart      — MAIN chat list (search/pin/rename/select/delete)
    conversations_list_screen.dart — ALT/older chat list (simpler, bulk-delete only)
    chat_screen.dart           — the big one: message thread UI (5000+ lines)
    call_screen.dart           — in-call UI (1:1 + group grid, controls)
    incoming_call_screen.dart  — full-screen ringing UI (swipe accept/reject)
    study_room_screen.dart     — whiteboard + video screen (4600+ lines)
    group_profile_screen.dart  — group info/settings/members/roles/invite
    create_group_screen.dart   — new group wizard (pick members, name)
    forward_message_screen.dart — pick chat(s) to forward message(s) into
    media_viewer_screen.dart   — fullscreen swipeable/zoomable image viewer
    app_bottom_nav.dart        — shared bottom nav bar (Home/Search/Chats/Profile)
  widgets/
    floating_call_bar.dart     — minimized-call pill (ALT implementation)
    minimized_call_bar.dart    — minimized-call pill (ALT implementation, FIX'd version)
    whiteboard_painter.dart    — CustomPainter for strokes/shapes on canvas
  patches/ (not real folder, just loose files given separately)
    chat_screen_pagination_fix.dart — patch for _loadMoreMessages() 404 handling
```

Outside `message/` (referenced but **not uploaded yet** — treat as
external/unknown until shared):
- `../../utils/api.dart` → `Api.baseUrl` (single source of backend base URL)
- `../../services/auth_service.dart` → `AuthService.getToken()` / login/logout
- `../../profile/screens/target_profile.dart`, `../../profile/api_service.dart` (as `ProfileApi`)
- `../../home.dart` → `HomeScreen(initialIndex: ...)` (IndexedStack: Home/Search/Profile)
- `../../widgets/sticker_picker_sheet.dart`

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
| Pins | GET/POST `/message/conversations/<id>/pins/` | max 3 pinned/conversation (backend-enforced) |
| | DELETE `/message/conversations/<id>/pins/<message_id>/` | unpin |
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
| Calls | GET `/message/calls/` , GET `/message/calls/<id>/` | history/detail (NOTE: initiate/action/missed live in `call_api_service.dart`) |
| Participants | POST `/message/conversations/<id>/participants/` `{user_id}` | add to existing conversation |
| Study room state | PUT/GET/DELETE `/message/conversations/<id>/study-room-state/` `{pages}` | persist/restore/clear whiteboard pages |

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
- `sendMessage({text, messageType, clientId, replyTo})` → `{type:'message', ...}`
- `sendTyping(bool)` → `{type:'typing', is_typing}`
- `sendReadReceipt(messageId)` → `{type:'read', message_id}`
- `sendDelete(messageId, {forEveryone})` → `{type:'delete', ...}`
- `sendReaction(messageId, emoji)` → `{type:'reaction', ...}`
- `sendStudyRoomEvent(action, data)` → `{type:'study_room_event', action, data}`
  — generic passthrough for ALL whiteboard/timer/quick-chat/sticker events

Incoming: `events` broadcast Stream of raw `{type, ...}` maps — screens
have a big switch on `type` (see §9).

Has raw `print()` debug logging still in place (marked "hata dena baad
me" = remove later) — noise in production logs, not removed yet.

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
- anything else (chat message) → `_showBackgroundChatNotification`
  (has its own Reply action + own `FlutterLocalNotificationsPlugin`
  instance since it's a separate isolate)

`init()` (foreground):
- Requests permissions, creates Android channels: `chat_messages`,
  `downloads`, `reactions`, `ongoing_call` (Importance.low, no sound/
  vibration — updates every second via chronometer, would spam as
  heads-up otherwise)
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

### 5.1 Two competing conversation-list screens ⚠️
Both exist and both say "REDESIGN" in their header comments:
- **`conversations_screen.dart`** — the fuller one: search (user
  search + start new chat), long-press multi-select (delete/pin/
  rename), local pin/label overrides, connects to
  `InboxSocketService`, has `AppBottomNav`.
- **`conversations_list_screen.dart`** — simpler: long-press
  multi-select delete ONLY (no search, no pin/rename, no inbox
  socket), also has `AppBottomNav`.

**Not yet confirmed which one main.dart actually routes to** — treat
`conversations_screen.dart` as the primary/current one (richer feature
set, matches what `chat_screen.dart` and others assume exists) unless
told otherwise. `conversations_list_screen.dart` may be dead code or an
earlier iteration — flag before deleting/ignoring either.

Both push `ChatScreen(conversation: ...)` on tap, `CreateGroupScreen`
from a FAB/action (only wired in `conversations_screen.dart`'s search
flow explicitly seen so far).

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
`ForwardMessageScreen(messageIds: [...])`. Loads conversations, local
search filter, multi-select target chats, `_send()` →
`MessageApiService.forwardMessages(messageIds, conversationIds)`, pops
`true` on success (caller shows confirmation snackbar).

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
mapped: index2→home(0), index3→home(2)). Reused by both
`conversations_screen.dart` and `conversations_list_screen.dart`.

---

## 6. Widgets Layer

### 6.1 Minimized call bar — TWO implementations ⚠️
- **`floating_call_bar.dart`** (`FloatingCallBar(navigatorKey)`) —
  older version, takes `navigatorKey` as a constructor param, reopens
  `CallScreen` with (incorrectly) fresh `livekitUrl:''`/`livekitToken:''`
  placeholders (relies on `CallManager` already being connected/reused).
- **`minimized_call_bar.dart`** (`MinimizedCallBar()`, no params) —
  the FIXED version per its own header comment: uses the GLOBAL
  `CallKitService.navigatorKey` instead of a locally-passed one,
  because this widget sits in `MaterialApp.builder`'s `Stack` as a
  SIBLING of `child` (i.e. OUTSIDE the `Navigator`), so
  `Navigator.of(context)` from its own `BuildContext` doesn't work —
  documented root-cause of a "back karne ke baad fullscreen wapas nahi
  aati" bug.

**Treat `minimized_call_bar.dart` as the current/correct one** unless
told otherwise; `floating_call_bar.dart` is superseded (same bug it
fixes). Both listen to `CallManager.instance` and show only when
`isActive && isMinimized`.

### 6.2 `whiteboard_painter.dart`
`WhiteboardPainter(strokes, shapes, previewShape?)` — `CustomPainter`.
Draws freehand strokes (marker/paint normal; eraser = white line;
highlighter = 35%-opacity + 2.2x width + square cap), finalized shapes
(`_drawShape`: rect/circle/line/arrowLine w/ custom arrowhead math),
and a translucent `previewShape` while a shape is being dragged (before
finalized/broadcast). `shouldRepaint` always returns `true` (no diffing
— fine for a whiteboard, repaints are cheap relative to draw
frequency).

---

## 7. End-to-End Flows

### 7.1 App startup (inferred, main.dart not yet shared)
Presumed sequence based on cross-references: Firebase init →
`PushNotificationService.instance.init()` (sets background handler,
requests permissions, registers FCM token if logged in, starts
`MissedCallWatcher`) → `CallKitService.instance.init(navigatorKey)` →
login (via not-yet-shared `AuthService`) → on success,
`PushNotificationService.instance.registerToken()` (must be called
manually right after login too, since token isn't registered until a
user is authenticated) → `ConversationsScreen` connects
`InboxSocketService` → `MinimizedCallBar` + presumably a
`FloatingCallBar`-equivalent sit in `MaterialApp.builder`'s Stack.

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
- Pin/unpin: REST only (`pinMessage`/`unpinMessage`), other side learns
  via `message_pinned`/`message_unpinned` socket events (implies
  backend also broadcasts these — not just REST response)
- Edit: `editMessage` REST PATCH, sender-only
- Delete: BOTH REST (`deleteMessage`, `for_everyone` flag) AND socket
  (`_socket.sendDelete`) are fired together from `_deleteMessage()` —
  redundant-looking but likely intentional (REST = durable state
  change, socket = instant UI removal on other side without waiting
  for a server broadcast round-trip)
- Forward: single REST call `forwardMessages(messageIds,
  conversationIds)` handles 1-to-many and many-to-many in one shot;
  target-chat delivery is server-side realtime (not client-driven)

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
| `message` | `{client_id, message_type, text, reply_to}` |
| `typing` | `{is_typing}` |
| `read` | `{message_id}` |
| `delete` | `{message_id, for_everyone}` |
| `reaction` | `{message_id, emoji}` |
| `study_room_event` | `{action, data}` (generic passthrough) |

Server → Client (`type` field, consumed by `ChatScreen._handleSocketEvent`):
`chat_message`, `typing`, `read`, `delete`, `reaction`, `poll_created`,
`poll_voted`, `message_pinned`, `message_unpinned`,
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

- **Two conversation-list screens** (`conversations_screen.dart` vs
  `conversations_list_screen.dart`) — confirm which is actually routed
  to from `main.dart`/`home.dart` before editing either; assume
  `conversations_screen.dart` is current (§5.1).
- **Two minimized-call-bar widgets** (`floating_call_bar.dart` vs
  `minimized_call_bar.dart`) — `minimized_call_bar.dart` is the fixed
  one per its own comments (§6.1); confirm `floating_call_bar.dart`
  isn't still wired anywhere before deleting it.
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
- Files referenced but not yet shared: `utils/api.dart` (`Api.baseUrl`),
  `services/auth_service.dart` (`AuthService`), `home.dart`
  (`HomeScreen`), `profile/screens/target_profile.dart`,
  `profile/api_service.dart`, `widgets/sticker_picker_sheet.dart`,
  and presumably `main.dart` itself. Don't assume their internals —
  ask/wait if a task needs their exact behavior.

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

*(End of current doc — extend sections above as new files/behaviour
are confirmed. Don't restart numbering; append sub-sections like §5.11,
§7.19 etc. as needed.)*
