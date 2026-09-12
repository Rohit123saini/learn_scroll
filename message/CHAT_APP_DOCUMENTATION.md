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

> **Reconciliation pass (latest — parent-mode G-6 + Task-16 cross-app batch)**: the
> remaining large/core files were reviewed for the first time this pass —
> `views.py`, `views_ai.py`, `views_parent.py`, `push_utils.py`, `serializers.py`,
> `tasks.py`, `offline_queue.py`, `permissions.py`, `routing.py`, `scheduled_messages.py`,
> `search_utils.py`, `services.py`, `tests.py`, `throttles.py`, `translation_service.py`,
> `upload_view.py`, `urls.py`, `user_display.py`. The large majority confirmed this doc's
> existing content exactly as written (`push_utils.py`, `tasks.py`, `throttles.py`,
> `translation_service.py`, `upload_view.py`, `offline_queue.py`, `search_utils.py`,
> `scheduled_messages.py`, `user_display.py`, `routing.py`, `tests.py` needed **no**
> corrections). Two genuinely new findings, both cross-referenced from §2/§6/§9/§10:
> (1) **G-6 mutual-consent (`ParentToken.status`) — model-field half now resolved, a
> NEW routing gap found in its place.** `views_parent.py` confirms `ParentToken.status`/
> `Status`/`approved_at`/`generate_token()` are real and wired through two new views
> (`ParentCodeTokenApproveView`, `ParentPendingRequestsView`) — but `urls.py`, reviewed in
> this exact same batch, still doesn't import or route either one, so a student still has
> **zero reachable way** to approve a pending parent device end-to-end; see §9.4 item 22
> (fully rewritten), §2 `ParentToken`, §6 Parent Dashboard. (2) **`DoubtQuestion.
> context_type`/`context_id` (Task 16) has a confirmed real caller — and it's an entirely
> new app, `testseries`.** `services.answer_doubt_question()` (new function, documented in
> §10) is built to answer both group and context-pointer doubts; its own docstring names
> `testseries/bridge.py::answer_query_on_series()` as the actual caller. `message`'s own
> `DoubtQuestionViewSet.answer()` does **not** call this shared function — it duplicates
> the same logic inline, the same anti-pattern `services.py` (task 27) already fixed once
> for group actions. A second, smaller gap is flagged inline in `services.py` itself: an
> unconfirmed `core.models.Notification.NotifType.TESTSERIES_QUERY_ANSWERED` enum member.
> See §2 `DoubtQuestion`, §9.4 item 23 (rewritten), §10 `services.py`.
>
> **Previous reconciliation pass — management-commands batch**: four
> `management/commands/*.py` files were reviewed for the first time that pass —
> `send_scheduled_messages.py`, `cleanup_expired_messages.py`,
> `expire_stale_parent_access.py`, `apply_doubtquestion_context_fields.py` — none of
> which had previously been part of any file batch, even though `tasks.py`/`models.py`
> comments already referenced two of them by name. All four are documented in a
> **§10 "Management Commands" subsection**, with File Map rows (§1) and cross-references
> from the models/features they touch. Headline findings: (1) `send_scheduled_messages`
> and `cleanup_expired_messages` are **manual/backup triggers that duplicate logic
> `tasks.py`'s Celery tasks already run on a schedule** (§9.4 items 4/24 — not bugs, but
> worth confirming neither is *also* cron-scheduled in production, which would be
> redundant and, for the scheduled-messages one specifically, was a real double-send risk
> before this file's own locking fix); (2) `expire_stale_parent_access` is a genuinely new
> periodic-hygiene job (`ParentToken`/`ParentAccessCode`) that is **not yet registered
> anywhere** (no `CELERY_BEAT_SCHEDULE` entry, no external cron confirmed) — see §9.4 item
> 25; (3) `apply_doubtquestion_context_fields` reveals a **`DoubtQuestion` schema change
> not yet reflected in this doc's §2/§6** — `group`/`conversation` become nullable and a
> new generic `context_type`/`context_id` pointer + `CheckConstraint` are added, but
> `DoubtQuestionViewSet` (§6) still only exposes the group-scoped
> `/message/groups/<group_id>/doubts/` route, so it's unconfirmed whether any view code
> actually writes/reads the new context columns yet — see §2 `DoubtQuestion` and §9.4 item
> 23. §2's `DoubtQuestion` entry and §9.4 have been updated accordingly; no other section
> needed a factual correction this pass.
>
> **Previous reconciliation pass — TASK 21, call recording**: `models.py`, `consumers.py`,
> `admin.py`, `ai_service.py`, `apps.py`, `attendance_utils.py`, `cache_utils.py`,
> `constants.py`, `group_rules.py`, `link_preview.py`, `livekit_utils.py`,
> `media_utils.py`, `mentions.py`, `Middleware.py`, and `models_focus.py` were
> re-checked against this doc. One genuinely new/undocumented piece of logic was found
> and is now covered: **LiveKit Egress call recording (TASK 21)** —
> `livekit_utils.start_room_recording`/`stop_room_recording`/`EgressError`, and
> `CallSession`'s five recording fields (`is_recording`, `recording_egress_id`,
> `recording_started_at`, `recording_output_path`, `recording_url`), previously flagged
> in this doc (old §9.4 item 2) as dead fields with no trigger code anywhere — see §2
> `CallSession`, §6 Calls, §7.24, §9.0 item 21, §10, §13. Also corrected in the same pass:
> `CallSession.token` (legacy Agora field) no longer exists — dropped in an earlier
> migration, mistakenly still listed as present — and the `models_focus.py` file-map
> entry now notes `FocusSession` is confirmed **merged** into `models.py` itself, not
> still a separate pending-merge file. A new §2 entry was added for `message`'s own
> `Assignment`/`AssignmentSubmission` models (distinct from `liveclass.Assignment`),
> which existed in `models.py` and were already referenced elsewhere in this doc (Parent
> Dashboard §6) but had no dedicated model-section entry. Everything else checked came
> back an exact match to what was already written — no other changes were needed.

---

## 1. File Map

| File | Purpose |
|---|---|
| `models.py` | All DB models |
| `models_focus.py` *(merge-in file, not a standalone app module — **now merged**)* | `FocusSession` model for Feature 12 (Smart DND) — originally shipped as a separate file with its own docstring instructing it be pasted into the end of `models.py`. **Confirmed done**: `models.py` itself now carries `FocusSession` directly (with a `🔧 FIX (merged from models_focus.py)` comment noting `views_focus.py`'s `from .models import FocusSession` was raising `ImportError` until this merge landed), so `models_focus.py` is now historical/reference-only — its content is superseded by, not additional to, what's in `models.py`. Also documents the one-field addition `Message.is_announcement` needed for Feature 11 — see §2/§7.17/§7.18 |
| `serializers.py` | DRF serializers. Includes `DoubtQuestionSerializer`/`DoubtCreateSerializer`/`DoubtAnswerSerializer` *(NEW — Doubt Queue, see §7.20)*, `ConversationWallpaperSerializer` *(NEW — per-chat wallpaper, see §6)*, and an N+1 fix in `ConversationListSerializer` (see §9.0) |
| `views.py` | REST views/viewsets (the bulk of the app logic) |
| `consumers.py` | Django Channels WebSocket consumers |
| `routing.py` | WS URL patterns |
| `Middleware.py` | JWT auth for WebSocket connections. No longer carries its own copy of the logic — re-exports `JWTAuthMiddleware`/`get_user_from_token` from project-level `LearnScroll/ws_auth.py`, which `liveclass` also now imports from *(fix — see §9.0)* |
| `permissions.py` | DRF permission classes. `IsGroupAdminOrModerator` now confirmed on `group_rules.is_group_admin_or_mod` (cached single source of truth) instead of its own raw query. `HasValidParentToken` *(Feature 8 — Parent Mode, see §7.16)* — header-token auth (`X-Parent-Token`) for parent-facing read-only views, deliberately not touching `request.user`. **🔧 GAP FIX (G-6, mutual consent) — NEW this session**: a verified-but-not-yet-`status=APPROVED` token is now rejected here just like an expired/revoked one — closes old §9.4 item 10, see there and §2/§6 |
| `group_rules.py` | Group access-control (message/call/study-room permission, daily limit) |
| `services.py` *(NEW — task 27)* | `GroupViewSet.create`/`add_members`/`update_member`'s core logic extracted into plain functions (`create_group`, `add_members_to_group`, `remove_group_member`, `update_group_member_role`) — decoupled from DRF so `core/classroom_chat_bridge.py` can reuse the exact same "create group / add member / change role" logic without going through an HTTP request/response cycle. Raises plain `ValueError`/`PermissionError` (not DRF exceptions) so it stays importable from non-DRF code; the caller converts them. Also re-homes `add_or_reactivate_participant` (moved here from `views.py`, which now imports it from here — single source, also reused by `offline_queue.py`) — **and both this and the `GroupViewSet` delegation are now confirmed actually wired in `views.py`**, see §5/§9.4 item 21. `create_bell_rows_for_push` (task 44) **used to live here but has been removed** — see §7.22/§10 `push_utils.py` for where bell-row creation actually happens. **`answer_doubt_question` *(NEW this batch — Task 16)*** — shared answer-path for both group and context-pointer `DoubtQuestion`s, confirmed called by an **external `testseries` app** (`testseries/bridge.py::answer_query_on_series()`), **not** by `message`'s own `DoubtQuestionViewSet` — see §2 `DoubtQuestion`, §9.4 item 23, §10 |
| `mentions.py` | Shared `@mention` text-parsing helper (REST + WS) |
| `translation_service.py` *(NEW this batch — Feature 9: real-time message translate)* | `translate_text(text, target_lang, source_lang=None)` — pluggable provider wrapper, default implementation calls Google Cloud Translate v2 REST API (plain API key via `settings.GOOGLE_TRANSLATE_API_KEY`, no SDK/service-account needed). Raises `TranslationServiceUnavailable` (not-configured / unreachable, → clean 503) or `TranslationError` (bad lang code / bad response, → 4xx) rather than ever surfacing as a raw 500. Also exposes `SUPPORTED_LANGUAGES` (the 10 languages in the app's language picker — en/hi/mr/ta/te/kn/bn/gu/pa/ur — kept in sync with `language_picker_sheet.dart` per its own comment, not an enforced server-side allow-list). Consumed by `MessageViewSet.translate` (referenced in this file's own docstring) — see §7.21 |
| `push_utils.py` | Firebase Cloud Messaging (FCM) push helpers. Firebase init is **lazy** (only runs the first time a push is actually sent, not at import time) and reads either `FIREBASE_CREDENTIALS_PATH` or `settings.FCM_SERVICE_ACCOUNT_JSON_PATH` *(fix — see §9.0)*. Chat-push batching was **rewritten this batch to be WhatsApp-style immediate** — no more delayed Celery flush, see §7.13. Also filters recipients through active Focus Mode sessions (Feature 12, §7.18) and tags announcement pushes (Feature 11, §7.17) differently from normal chat pushes |
| `livekit_utils.py` | LiveKit JWT token generation (calls + study rooms). Credential check (`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`) is now **lazy** — only fires when a token is actually requested, not at module-import time *(fix — see §9.0)*. Per-call `ttl` param: calls default 2h, `StudyRoomJoinView` overrides to 8h. **Egress (call recording) added — TASK 21 *(NEW)***: `start_room_recording(room_name)` / `stop_room_recording(egress_id, output_filepath=None)`, async-under-the-hood LiveKit Egress REST calls bridged with `asyncio.run()` since the Egress/Room service client is async-only (unlike the plain-JWT `generate_livekit_token` above, which needs no event loop at all); raises the dedicated `EgressError` (LiveKit rejected/failed the call → 502-able) separately from the existing lazy-credential `RuntimeError` (not configured → 503-able), so `CallRecordingView` can tell the two apart without string-matching. See §2 `CallSession`, §6 Calls, §7.24, §10, §13 |
| `user_display.py` | Shared display-name / profile-photo-URL helper (REST + WS) |
| `upload_view.py` | Generic file-upload endpoint (returns a URL to attach to a message) |
| `ai_service.py` | Gemini calls, all cached 24h by content hash: `generate_summary`, `generate_quiz`, `transcribe_audio`, `generate_reply_suggestions`, `generate_classroom_answer` *(NEW)*, `generate_revision_deck` *(NEW — see §7.14/§7.15)*. Cache-hit check now uses a `_CACHE_MISS` sentinel instead of a truthy check, and `generate_summary` now guards against caching an empty result *(fix — see §9.0)* |
| `views_ai.py` | REST endpoints wrapping `ai_service.py`: `AiStudyRoomView` (summary/quiz), `VoiceTranscribeView`, `SmartReplySuggestionsView`, `ClassTranscriptChunkUploadView`/`ClassTranscriptSearchView`, `ClassroomCopilotView`, and `RevisionDeckView` (`GET`/`GET ?history=true`/`GET ?deck_id=`/`DELETE ?deck_id=`/`POST` — list/fetch/delete/generate, see §6/§7.15) |
| `media_utils.py` | `create_group_media_for_message(message)` — populates the `GroupMedia` gallery table; shared by REST + WS message-send |
| `constants.py` | Single shared source for cross-file constants (currently `MAX_PINNED_PER_CONVERSATION`) |
| `search_utils.py` *(NEW this session)* | `MIN_QUERY_LENGTH` (2), `apply_structured_filters(qs, query_params)` (sender/date_from/date_to/has_media/media_type), `search_messages(qs, query)` — Postgres-backed ranked full-text + trigram-typo search, non-Postgres fallback to plain `icontains`. `views.py`'s `ConversationViewSet.search`/`.search_all` already imported and called this module; the file itself didn't exist, so both search endpoints raised `NameError` on every call until this session. See §7.1 |
| `cache_utils.py` | Django-cache helpers: group-role cache (`get_group_role_cached`/`invalidate_group_role_cache`, 60s TTL) behind `group_rules.is_group_admin_or_mod`, and presence cache (`get_presence_cached`/`set_presence_cache`, 15s TTL) behind `UserPresenceView` + `ChatConsumer` |
| `throttles.py` | DRF `UserRateThrottle` subclasses for REST writes (`MessageSendThrottle`, `CallInitiateThrottle`, `GroupCreateThrottle`, `ReactionThrottle`, plus `TranslateThrottle` *(NEW this batch — Feature 9, see §7.21)*) + per-IP safety-net throttles (`MessageSendIPThrottle`, `CallInitiateIPThrottle`, both `SimpleRateThrottle` subclasses via shared `ScopedIPThrottle`) *(NEW this session)* + `ParentCodeVerifyThrottle` *(NEW this batch — per-IP brute-force guard on `ParentVerifyCodeView`, the one `AllowAny`/unauthenticated endpoint in the whole app, since there's no `request.user` to key a normal `UserRateThrottle` on at that point in the flow — see §7.16/§9.4)* + `WSMessageRateLimiter` (cache-backed sliding window) for the WS `message` event, since DRF throttles don't apply to Channels consumers |
| `scheduled_messages.py` | "Send later" delivery half — `finalize_scheduled_message(message)`, called by the `message.send_scheduled_messages` Celery task (`tasks.py`, confirmed registered in `CELERY_BEAT_SCHEDULE`, runs every minute). Now also enqueues the link-preview/transcription tasks below for scheduled messages, same as the two live-send paths *(NEW this session)* |
| `offline_queue.py` *(NEW — task 49, offline-first chat queue, delivery half)* | `flush_offline_queue(conversation, sender, queued_messages)` — a client reconnecting after being offline POSTs its locally-queued messages (each with a client-generated `client_id`) in one batch; this delivers them in order and returns a per-item `created`/`duplicate`/`error` result so the client can reconcile its local queue. Deliberately **duplicates** rather than shares `scheduled_messages.py`'s delivery code (same broadcast/unread-count/mentions/media/push steps) — this codebase's established convention is one delivery function per entry-point, not a shared mega-function (see the file's own docstring). Idempotency (never double-creating a message on a retried POST) is enforced at the DB level via the existing `client_id` unique constraint (§2 `Message.client_id`) + an `IntegrityError` catch, not by `get_or_create` alone, which isn't race-safe under concurrent duplicate submissions |
| `tasks.py` | Celery tasks: `send_scheduled_messages` (delivers due "send later" messages, every minute — beat-scheduled), `cleanup_expired_messages` (hard-deletes disappearing messages past `expires_at`, every 15 min — beat-scheduled, now via a `Message.all_objects` manager — see §9.4), `generate_link_preview_task` (background OpenGraph fetch, one-shot via `.delay()`), `transcribe_voice_message_task` (background voice-note transcription, one-shot via `.delay()`), `transcribe_class_chunk_task` *(NEW — Feature: class transcript, see §7.19)* (background per-chunk classroom-audio transcription, one-shot via `.delay()`, writes to `ClassTranscriptSegment.text` and broadcasts `transcript_segment_ready`) — plus the shared `_broadcast_meta_update()` helper the link-preview/transcription tasks use to push their result live over WS. **`flush_chat_push_digest` has been removed** — chat-push batching no longer uses a delayed task, see §7.13 |
| `link_preview.py` *(NEW this session)* | `extract_first_url(text)` + `fetch_link_preview(url)` — SSRF-safe OpenGraph fetcher (blocks private/loopback/link-local IPs, manually re-checks every redirect hop, size/time-bounded fetch, 7-day negative+positive cache) used by `generate_link_preview_task`. Pins the validated IP for the actual connection so `requests` can't be tricked into re-resolving to a different (internal) IP via DNS rebinding between the safety check and the real request *(fix — see §9.0 and §7.5)*. OG-tag/title parsing now uses `BeautifulSoup` instead of hand-rolled regexes *(fix — see §9.4 item 6)* |
| `admin.py` | Django admin registrations. `GroupJoinRequest`, `DeviceToken`, `StudyRoomState` models existed but were never registered — ops/support had no admin UI to inspect pending private-group join requests, debug a user's push tokens, or view a study room's saved whiteboard state without a raw DB query. Now registered like every other model *(fix — see §9.0)*. Also adds a `SoftDeleteAdmin` for `Conversation`/`Group` *(NEW)* — see §9.0/§9.4 item 1 and its own §10 entry below for why the default admin registration alone wasn't enough once group-delete became a soft-delete |
| `attendance_utils.py` *(NEW)* | `compute_attendance_stats(conversation, user)` — single source of truth for Study Room attendance-streak math (current streak, longest streak, total classes attended, last attended date), backed by `StudyRoomAttendance` (see §2). Used by `StudyRoomStreakView` (student's own view, single classroom). Also `compute_attendance_stats_bulk(conversation_ids, user)` *(NEW — N+1 fix)* — same math, one query for however many classrooms instead of one query per classroom; `ParentDashboardView` (Parent Mode) now uses this batched variant instead of calling the single-conversation function in a loop, see §7.16 |
| `apps.py` | App config (`name = 'message'`) |
| `tests.py` | Empty Django default stub — no tests written yet *(confirmed this batch)* |
| `management/commands/send_scheduled_messages.py` *(NEW this batch)* | Manual/backup CLI trigger for "Send Later" delivery — **not** the production path; `tasks.send_scheduled_messages` (Celery beat, every minute) is canonical, see §10 `tasks.py`. Now uses the identical `select_for_update(skip_locked=True)` + 200/batch pattern as the Celery task specifically so the two are safe to run concurrently without double-sending. See §9.4 item 4, §10 |
| `management/commands/cleanup_expired_messages.py` *(NEW this batch)* | Manual/backup CLI trigger for the disappearing-messages hard-delete sweep, with `--batch-size`/`--dry-run` flags. Duplicates (does not replace) `tasks.cleanup_expired_messages`'s already-scheduled Celery-beat sweep (every 15 min) — its own docstring assumed hard-delete was unimplemented, which this doc's §10/§9.1 item 3 shows is not the case. See §9.4 item 24, §10 |
| `management/commands/expire_stale_parent_access.py` *(NEW this batch)* | Periodic DB-hygiene only (**not** security-load-bearing — `HasValidParentToken` already rejects expired tokens/codes live on every request regardless of this ever running). Hard-deletes `ParentToken`s stale past their rolling TTL + a grace period, and deactivates long-expired never-renewed `ParentAccessCode`s. **Not yet registered in `CELERY_BEAT_SCHEDULE` or any confirmed cron** — see §9.4 item 25, §2 `ParentAccessCode`/`ParentToken`, §10 |
| `management/commands/apply_doubtquestion_context_fields.py` *(NEW this batch — Task 16)* | One-off, idempotent raw-SQL schema command (Postgres-only) making `DoubtQuestion.group`/`.conversation` nullable and adding `context_type`/`context_id` + a supporting index + `CheckConstraint` (must have a `group` OR a full context pointer). Applied outside Django's migration history by design — `makemigrations` will still want to generate a matching no-op migration afterward to sync state. See §2 `DoubtQuestion`, §9.4 item 23, §10 |

**Note on `urls.py`:** an earlier upload of this file was accidentally a duplicate of
`Middleware.py`, so this doc used to assume the router/paths rather than confirm them.
The real `urls.py` has since been reconstructed from `views.py`'s actual class/action
names — router-registered ViewSets (`ConversationViewSet`, `MessageViewSet`,
`GroupViewSet`, `BlockedUserViewSet`, `CallHistoryViewSet`) plus plain `path()` entries
for `UserPresenceView`, `CallInitiateView`, `CallActionView`, `StudyRoomJoinView`,
`StudyRoomStateView`, `DeviceTokenView`, `AiStudyRoomView`, `VoiceTranscribeView`, and
`MessageUploadAPIView`. All endpoint paths below are confirmed against it. **Confirmed
gap (this batch)**: `views_parent.py`'s `ParentCodeTokenApproveView` and
`ParentPendingRequestsView` are fully coded but **not** in this file's `views_parent`
import block or `urlpatterns` — see §6 Parent Dashboard, §9.4 item 22.

---

## 2. Data Models (`models.py`)

### `BaseModel` (abstract)
Every model inherits this.
- `id` — UUID, primary key
- `created_at`, `updated_at` — auto timestamps
- `is_deleted` — soft-delete flag. **No longer a dead field** *(fix — see §9.0/§9.4 item
  1)*: **now directly confirmed in `models.py`** — `SoftDeleteManager.get_queryset()`
  filters `.filter(is_deleted=False)`, and it's wired as `objects` on `BaseModel` itself
  (not just `Conversation`/`Group` — this applies to **every** model in the app, since all
  of them inherit `BaseModel`), so `is_deleted=True` rows disappear from every normal
  queryset automatically with zero per-call `.exclude(...)` needed anywhere. `all_objects =
  models.Manager()` (also on `BaseModel`, unfiltered) is what `admin.py` and `tasks.py`'s
  `cleanup_expired_messages` use to see soft-deleted rows too. `BaseModel.soft_delete()`/
  `.restore()` just flip the flag and save — called by `GroupViewSet.destroy()` (§5) via
  `group.soft_delete()`/`conversation.soft_delete()`.
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
- `wallpaper_url` *(NEW)* — per-user, per-chat background image, set via its own
  `GET`/`PATCH /conversations/<id>/wallpaper/` endpoint (`ConversationWallpaperSerializer`
  — deliberately its own single-field endpoint, not folded into the combined
  mute/archive/pin/draft `settings` endpoint, per that serializer's own comment)
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
- `allow_anonymous_doubts` *(NEW — Doubt Queue per-classroom toggle, see §7.20)* —
  enforced by the same `IsGroupAdminOrModerator` permission the ViewSet already uses for
  `update`/`partial_update`; no new endpoint needed to change it
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
- **`is_announcement`** *(NEW — Feature 11, see §7.17)*, `BooleanField(default=False,
  db_index=True)` — set by `views.py` at message-create time (not client-writable),
  storage-only at the model level; the actual "teacher/staff message" behavior (distinct
  push channel/type, presumably a pinned/highlighted lane in the UI) lives in the views
  and `push_utils.py`, not in the model itself
- Manager: `Message` inherits `BaseModel`'s `objects = SoftDeleteManager()` /
  `all_objects = models.Manager()` like every other model (§2 `BaseModel`, now confirmed
  directly in `models.py`) — `Message.objects` excludes `is_deleted=True` rows,
  `Message.all_objects` doesn't. `tasks.cleanup_expired_messages` deliberately uses
  `Message.all_objects` for its hard-delete sweep so it can still catch an
  already-soft-deleted-and-expired message.
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
`file_type`, `file_size`, `thumbnail_url`. **Now populated** — see `media_utils.py`
(§1/§9.0/§9.4 item 3/§10) — `create_group_media_for_message()` is called from both the
REST send path (`ConversationViewSet.messages`) and the WS send path
(`ChatConsumer.save_message`), so the gallery stays in sync regardless of which path a
message came through. The historical "not auto-populated, gallery stays empty" gap this
note used to flag is resolved; kept only as a pointer to where the wiring lives.

### `MessageStatus` (delivery/read receipts)
- `message`, `user`, `is_delivered`, `is_read`, `delivered_at`, `read_at`
- `unique_together = ('message', 'user')`

### `MessageReaction`
- `message`, `user`, `emoji` — `unique_together = ('message', 'user')` (one reaction per
  user per message; sending a new emoji replaces the old one via `update_or_create`)

### `CallSession`
- `type` (audio/video), `status`, `is_group_call`
- `conversation` (nullable), `group` (nullable), `caller`
- `channel_name` (unique — also the LiveKit room name)
- **`token` (legacy Agora field) is GONE** — dropped in an earlier cleanup
  (migration `0903_remove_callsession_legacy_agora_fields`) once
  `livekit_utils.generate_livekit_token` took over generating join tokens
  on demand; LiveKit never needed a stored token the way Agora did, and
  it is not coming back.
- `started_at`, `connected_at`, `ended_at`, `duration_seconds`
- **Recording — TASK 21, now wired up** *(previously "fields exist; no
  recording-trigger code was found" — that gap is closed, see §7.24)*:
  `is_recording` (bool), `recording_egress_id` (LiveKit's id for the
  in-progress/most-recent Egress job on this call — needed because LiveKit
  has no "stop the recording for room X" call, only "stop egress job Y";
  kept around after stop rather than nulled, so support can still trace
  which job produced a given `recording_url`), `recording_started_at`,
  `recording_output_path` (the storage path `livekit_utils.
  start_room_recording()` chose at start time, needed again at stop time
  to resolve the finished file's public URL), `recording_url` (filled in
  from `stop_room_recording()`'s return value — best-effort/synchronous
  for now; see §10's `livekit_utils.py` entry for why a LiveKit
  `egress_ended` webhook, not yet built, would be the more correct
  long-term source of truth — can stay null after stop if egress hasn't
  finished muxing yet). See migration
  `0910_add_callsession_recording_fields`. `CallRecordingView` (`views.py`
  — not in this file batch, so its exact request/response shape is
  inferred from `livekit_utils.py`'s own docstrings, not independently
  confirmed here) drives these fields via `livekit_utils.
  start_room_recording`/`stop_room_recording`. Distinct from Class
  Transcript (§7.19) — this is one whole-room composite MP4 via LiveKit
  Egress, not per-participant searchable text; see that model's own
  design note (`models.py`) for why both exist side by side.

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

### `StudyRoomAttendance` *(NEW — Feature 6: consistency streak)*
- `conversation` (FK), `user` (FK), `session_id` (blank-ok text), `attended_date`
  (`DateField`, defaults to server-local today) — **a deliberate separate field from
  `created_at.date()`**, so the "attendance day" can later be backfilled/redefined
  independently of when the row itself was created (e.g. a class run in a different
  timezone than the server).
- `unique_together`-style constraint on `(conversation, user, attended_date)` — joining
  the same study room multiple times in one day (disconnect/reconnect, leave/rejoin)
  counts as **one** day of attendance, not multiple.
- Index on `(conversation, user, -attended_date)` — the shape the streak query needs.
- One row is created per join, in `StudyRoomJoinView.post()`. Before this model existed,
  study-room "current session" tracking was in-memory/cache-only — there was no permanent
  per-user join history to compute a streak from (`CallParticipant` was confirmed to only
  cover 1:1/group **calls**, not study-room joins, so it couldn't be reused for this).
- Consumed by `attendance_utils.compute_attendance_stats(conversation, user)` — see §10
  and §7.16 for the streak logic and the two views (student + parent) that call it.

### `RevisionDeck` *(NEW — Feature 5: persisted flashcards + quiz)*
- `conversation` (FK), `session_id` (blank-ok text — optionally scopes the deck to one
  study-room session rather than the whole conversation's combined history),
  `flashcards` (JSON list of `{"front", "back"}`), `quiz` (JSON list of MCQ dicts),
  `generated_by` (FK to `User`, nullable, `SET_NULL`).
- Unlike the plain `summary`/`quiz` AI responses (§6, throwaway — never saved), a
  Revision Deck is meant to be revisited by the student later while studying for an
  exam, so `views_ai.py` persists each generation as a **new** row (history is kept
  deliberately — an older, smaller-content deck can still be useful) rather than
  overwriting a single row per conversation. Default ordering (`BaseModel.Meta`,
  `-created_at`) means "give me the latest deck" is just the first row.
- Index on `(conversation, -created_at)` for that latest-deck lookup.
- Built from `ai_service.generate_revision_deck(content)` — see §7.15/§10.

### `FocusSession` *(NEW — Feature 12: Smart DND during focus/exam windows, shipped in
`models_focus.py`, meant to be merged into `models.py`)*
- `user` (FK), `starts_at` (`auto_now_add`), `ends_at` (indexed), `exception_rule`
  (`teachers_only` default, or `nobody` for full silence — hard exam mode), `cancelled_at`
  (nullable — set if the user turns it off early, distinct from just letting it expire;
  useful for analytics/UI to distinguish "cancelled early" vs "ran full duration").
- **One active session per user** — `start_for_user()` cancels any still-active session
  for that user before creating the new one (overwrite/extend semantics, not a stack).
- `is_active` property — `cancelled_at is None and ends_at > now`.
- `get_active_for_user(user_id)` / `start_for_user(user, duration_minutes,
  exception_rule=TEACHERS_ONLY)` — the two entry points other modules use (confirmed:
  `push_utils._filter_recipients_for_focus()` calls `FocusSession.objects.filter(...)`
  directly rather than these classmethods, for a bulk multi-user lookup in one query).
- `ExceptionRule` is a `TextChoices` enum specifically so a future "specific people"
  option can be added without a migration touching existing rows.
- REST endpoints (`FocusSessionView`, `FocusSessionHistoryView`, `views_focus.py`) are
  confirmed wired end-to-end — see §6/§7.18/§9.0 item 17.

### `DoubtQuestion` / `DoubtUpvote` *(NEW — Doubt Queue, a persistent upvotable
per-classroom question board; now directly confirmed in `models.py`)*
- `DoubtQuestion`: `group` (FK, `related_name='doubts'`), `conversation` (FK,
  `related_name='doubts'` — kept alongside `group` purely so the WS broadcast can reuse
  the existing `chat_{conversation_id}` room instead of a new WS group), `author` (FK to
  `User`, **always** the real asker regardless of `is_anonymous` — anonymity is
  display-only, never DB-level, so moderation/`reveal` always has the real identity),
  `text` (max 2000), `is_anonymous` (bool), `is_revealed` (bool, one-way — never flips
  back to `False`), `upvotes_count` (denormalized `PositiveIntegerField`, updated via `F()`
  by the upvote/un-upvote action rather than a live `.count()`), `is_answered`,
  `answer_text`, `answered_by` (FK), `answered_at`. Default ordering
  `['-upvotes_count', '-created_at']` — most-upvoted first, exactly how the Doubts tab
  lists them. Index on `(group, is_answered, -upvotes_count)`.
- `DoubtUpvote`: `doubt` (FK, `related_name='upvotes'`), `user` (FK) —
  `unique_together = ('doubt', 'user')`, one upvote per user per doubt; toggling off
  deletes the row. Nothing server-side blocks upvoting your own doubt.
- **Anonymity is hidden by default even from teachers** — `DoubtQuestionSerializer.
  get_author()` only reveals the asker's identity to (a) the asker themselves, or (b)
  everyone, once a teacher/admin/mod has explicitly used a `reveal` action to flip
  `is_revealed` true. This is the actual point of `Group.allow_anonymous_doubts` (§2) —
  the toggle controls whether "Ask Anonymously" is offered at all, not whether it's
  visible once asked.
- **🔧 SCHEMA CHANGE — Task 16, `management/commands/apply_doubtquestion_context_fields.py`
  (NEW this batch, not part of any prior file batch).** `group` and `conversation` are now
  **nullable**, and two new columns exist: `context_type` (`varchar(30)`, nullable) and
  `context_id` (`uuid`, nullable), backed by a lookup index on `(context_type,
  context_id)` and a `CheckConstraint` (`doubtquestion_has_group_or_context`) requiring
  every row to have **either** `group` **or** a full `(context_type, context_id)` pair —
  never neither. **The intended non-group context is now confirmed**: `services.
  answer_doubt_question()` (`services.py`, reviewed this batch) exists specifically to
  answer both shapes, and its own docstring names the real caller —
  **`testseries/bridge.py::answer_query_on_series()`**, a different app entirely,
  reusing `DoubtQuestion` as shared infrastructure for test-series Q&A rather than
  chat-group doubts. See §9.4 item 23 for the full detail, including a confirmed
  duplication gap (`message`'s own `DoubtQuestionViewSet.answer()` doesn't call this
  shared function) and an inline-flagged notification-enum gap
  (`core.models.Notification.NotifType.TESTSERIES_QUERY_ANSWERED`, unconfirmed to
  exist). Applied via
  raw SQL directly against Postgres (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, guarded
  `DO $$ ... $$` for the constraint), **not** a numbered Django migration — idempotent
  and safe to re-run, but it does not update `django_migrations`, so `makemigrations`
  will likely still generate a no-op migration afterward to sync state (expected, per the
  command's own docstring). `message`'s own REST surface
  (`DoubtQuestionViewSet`/`DoubtQuestionSerializer`/`DoubtCreateSerializer`, §6) still
  only exposes the group-scoped `/message/groups/<group_id>/doubts/` route — no
  context-based create/list path exists there; the context-pointer shape is populated
  entirely from the `testseries` side. See §9.4 item 23.

### `ParentAccessCode` / `ParentToken` *(NEW — Feature 8: Parent Mode auth; now directly
confirmed in `models.py`)*
- `ParentAccessCode`: `student` (FK, `related_name='parent_access_codes'`), `label`
  (cosmetic, student-only, never shown to the parent), `code` (8-char, unique, drawn from
  a 32-symbol alphabet that excludes `0/O/1/I` to avoid misread/mistyped shares),
  `is_active`, `last_used_at`, `expires_at` (absolute TTL, `DEFAULT_TTL_DAYS = 180`),
  `last_revealed_at`. `masked_code` property shows only the last 4 characters
  (`••••9QRT`) for the normal list view. `is_expired` property = `expires_at` in the past.
  `generate_for(student, label, ttl_days)` retries up to 5 times on a code collision.
  `renew(ttl_days)` gives the same code a fresh `expires_at` and re-activates it.
- `ParentToken`: `parent_access_code` (FK, `related_name='tokens'`), `token` (64-char,
  unique, `secrets.token_urlsafe(32)`, generated via a `generate_token()` classmethod —
  confirmed this batch via `views_parent.ParentVerifyCodeView`'s
  `token=ParentToken.generate_token()` call), `last_seen_at`. `INACTIVITY_TTL_DAYS = 30` — a
  **rolling** expiry independent of the code's own `expires_at`; `is_expired` compares
  `now - (last_seen_at or created_at)` against that TTL, so one idle device auto-expires
  without touching the code or any other device. `touch()` bumps `last_seen_at` — called
  by `HasValidParentToken` on every authenticated parent request so the rolling TTL stays
  accurate. **`status`/`Status` (`PENDING`/`APPROVED`/`REJECTED`) and `approved_at` are
  now CONFIRMED to exist on the model — resolves the model-field gap old §9.4 item 22
  flagged.** Confirmed this batch purely from usage in `views_parent.py` (not from
  `models.py` itself, still not part of any file batch) — `ParentVerifyCodeView` creates
  every new token `status=ParentToken.Status.PENDING`; `ParentCodeTokenApproveView`
  flips exactly one to `status=ParentToken.Status.APPROVED` + stamps `approved_at`;
  `ParentCodeTokensView`'s per-device list now also returns `status`/`approved_at` per
  token. See the new §9.4 item 22 update for what's still open (routing).
- Deliberately decoupled: a lost/replaced parent phone just re-verifies the same code for
  a new `ParentToken`, no new code needed from the student; revoking
  `ParentAccessCode.is_active` invalidates every token issued against that code at once.
- **DB hygiene (NEW this batch, not security-critical)** —
  `management/commands/expire_stale_parent_access.py` periodically hard-deletes
  `ParentToken`s that have been past their own `INACTIVITY_TTL_DAYS` for a further
  14-day grace period (so a just-expired device doesn't vanish from a "devices" list the
  instant it crosses the line), and deactivates `ParentAccessCode`s that expired more
  than 30 days ago and were never renewed. Purely cosmetic/storage cleanup —
  `HasValidParentToken` already rejects expired codes/tokens live on every request
  regardless of whether this command has ever run. **Not yet wired into
  `CELERY_BEAT_SCHEDULE` or any confirmed cron** — see §9.4 item 25, §1 File Map.
- **Deliberately does not use `request.user`** — `HasValidParentToken` attaches
  `request.parent_access_code` and `request.parent_student` instead, specifically so no
  other permission/view could accidentally treat an authenticated parent as if they were
  the student themselves (a parent has no `User` row at all in this model).
- Parent↔student linking is a direct `ParentAccessCode.student` FK, set by the student
  themselves when they generate the code.
- **🔧 GAP FIX (G-6, mutual consent) — supersedes "no separate
  invite/approval flow" from earlier revisions of this doc.** `permissions.py`'s
  `HasValidParentToken` filters on `status=ParentToken.Status.APPROVED` in addition to
  the existing `is_active`/expiry checks — a token that has merely completed
  `ParentVerifyCodeView` (verified the code) but has not yet been approved by the student
  is rejected exactly like an expired/revoked one (same generic denial message,
  deliberately not distinguishing "pending" from "invalid", so a guessed/leaked code can't
  be used to probe whether it's real-but-unapproved vs. simply wrong). Every new
  `ParentToken` starts `status=PENDING`, flipped to `APPROVED` only by the student, via
  `views_parent.ParentCodeTokenApproveView`. This closes the trust-model gap flagged in
  old §9.4 item 10 (a leaked/screenshotted code previously granted immediately-live access
  with the student never in the loop) — see §6/§9.4 item 10 for the product-level framing.
  **UPDATE this batch — the model-field half of old §9.4 item 22 is now resolved**:
  `views_parent.py` (reviewed for the first time this batch) confirms `ParentToken.status`/
  `Status`/`approved_at` are real and in active use (see this entry's own `ParentToken`
  paragraph above), and two new views exist to drive them — `ParentCodeTokenApproveView`
  (student approves one PENDING device) and `ParentPendingRequestsView` (student sees
  every PENDING device across all their codes in one list). **The routing half of that
  same gap is still open, though**: `urls.py`, reviewed in this exact same batch, still
  only imports `ParentAccessCodeView`, `ParentAccessCodeRenewView`,
  `ParentAccessCodeRevealView`, `ParentCodeTokensView`, `ParentCodeTokenDetailView`,
  `ParentDashboardView`, `ParentVerifyCodeView` from `views_parent` — neither
  `ParentCodeTokenApproveView` nor `ParentPendingRequestsView` is imported or routed
  anywhere. So end-to-end, a student today still has **no reachable way** to ever approve
  a pending parent-device request: the code exists, it's just not wired to a URL. See
  §9.4 item 22 (updated) for the full current status, and §6 Parent Dashboard for the
  request/response shapes of the two new views.

### `Assignment` / `AssignmentSubmission` (`message`'s own — distinct from
`liveclass.Assignment`)
- `Assignment`: `group` (FK, `related_name='message_assignments'`), `title`,
  `description` (blank ok), `due_at` (nullable), `created_by` (`SET_NULL`,
  `related_name='+'`). Index on `(group, due_at)`.
- `AssignmentSubmission`: `assignment` (FK, `related_name='submissions'`),
  `student` (FK, `related_name='message_assignment_submissions'`),
  `is_submitted`, `submitted_at`. `unique_together = ('assignment',
  'student')` — one submission row per student per assignment. Index on
  `(student, is_submitted)`.
- **Deliberately renamed `related_name`s** (`assignments` →
  `message_assignments`, `assignment_submissions` →
  `message_assignment_submissions`) — `liveclass` has its own, separately-
  built `Assignment`/`AssignmentSubmission` models sharing the same
  `Group`/`User` targets; Django can't register two identical reverse
  accessors on the same target model, so `makemigrations` failed
  (`fields.E304`/`E305`) until these were made unique. **Not yet resolved
  which one is authoritative** — see §6 Parent Dashboard's "Gap 1" note:
  if `liveclass.Assignment` is meant to be the same concept as this one
  (not a genuinely different feature that happens to share a name), the
  cleaner long-term fix is deleting this duplicate pair and pointing
  parent-dashboard code at `liveclass.Assignment` instead; that's a
  bigger structural call than this pass makes unilaterally, so both
  models currently coexist, unmerged.

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
| `GET /<id>/export/` *(NEW — TASK 29)* | `export` | `?type=chat\|media` (default `chat`), optional `?since=`/`?until=` (ISO datetimes). Returns one JSON payload (`Content-Disposition: attachment`) of metadata + URLs for every still-visible message, capped at `EXPORT_MAX_MESSAGES=5000` with `has_more` for paging via `until`. See §7.25 |
| `POST /<id>/offline-queue/` *(NEW — task 49, now wired)* | `offline_queue_flush` | Body `{"messages": [...]}` (max 100/batch), each item shaped per `offline_queue.flush_offline_queue`'s docstring. Same block/permission/daily-limit gating as `messages` POST, throttled the same way (`MessageSendThrottle`/IP throttle). See §7.23 |

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
| `DELETE /<id>/` | `destroy` | **Admin role only** (not moderator). Broadcasts `group_deleted` *before* deleting (handled by `ChatConsumer.group_deleted`, see §8), then **soft-deletes** (`group.soft_delete()` + `conversation.soft_delete()`) rather than hard-`.delete()`-ing *(fix — see §9.0/§9.4 item 1)* — see the note right below this table |
| `POST /<id>/members/` | `add_members` | Body `{"user_ids": [...]}`. Public group: any member can add. Private group: admin/mod only |
| `POST /join/` | `join` | Body `{"invite_code": "..."}`. Public: instant join. Private: creates/resets a `GroupJoinRequest` (pending) |
| `GET /<id>/join-requests/` | `join_requests` | Admin/mod only. Lists pending requests |
| `POST /<id>/join-requests/<request_id>/approve/` | `approve_join_request` | Admin/mod only |
| `POST /<id>/join-requests/<request_id>/reject/` | `reject_join_request` | Admin/mod only |
| `PATCH /<id>/members/<user_id>/` | `update_member` | Admin/mod only (unless acting on self). Body: any of `role`, `is_muted`, `is_banned`. Banning/unbanning also toggles `ConversationParticipant.left_at` |
| `DELETE /<id>/members/<user_id>/` | `update_member` | Self-leave allowed; removing someone else requires admin/mod |
| `GET /<id>/media/` | `media` | `GroupMedia` gallery, optional `?type=image/video/...` filter, paginated (`StandardPagination`). ⚠️ see `GroupMedia` note in §2 |

**✅ RESOLVED this batch — `create`/`add_members`/`update_member` now actually call
`services.py` (task 27)**, confirmed against the current `views.py`: it now imports
`add_or_reactivate_participant, create_group, add_members_to_group, remove_group_member,
update_group_member_role` from `.services`, and the ViewSet's own module-level duplicate
of `add_or_reactivate_participant` has been removed (a comment left in its place points
at `.services` as the single source). `GroupViewSet.create()` now just validates via
`GroupCreateSerializer` and calls `services.create_group(...)`; `add_members()` calls
`services.add_members_to_group(...)`, converting its plain `PermissionError`/`ValueError`
to `PermissionDenied`/400; `update_member`/`remove_group_member`'s DELETE branch likewise
now delegate to `services.remove_group_member`/`update_group_member_role`. The
previously-flagged risk of two independently-drifting copies of
`add_or_reactivate_participant` is gone — there is now exactly one, in `services.py`. See
§7.22/§9.4 item 21 (now marked resolved).

**Group delete is now soft-delete, not a hard cascade** *(fix — see §9.0/§9.4 item 1)*:
previously `destroy()` ran the `ModelViewSet` default `DestroyModelMixin` behavior
unrestricted (**any** member, including a plain `member` role, could hard-delete the
whole group), and it called `conversation.delete()` directly — an unrecoverable CASCADE
that wiped every message/media/call/doubt in one accidental tap. Now: (1) `destroy()`
explicitly re-checks the caller has `GroupMember.role == ADMIN` and `is_banned=False`,
raising `PermissionDenied` otherwise — `get_object()`'s queryset restricting to members
was never enough on its own to stop a member-level hard-delete; (2) the `group_deleted`
WS broadcast still fires first, same as before; (3) instead of `.delete()`, it now calls
`group.soft_delete()` + `conversation.soft_delete()`, which flips `is_deleted=True` and
relies on the new `SoftDeleteManager` default manager (§2) to make the group vanish from
every normal queryset immediately, without an unrecoverable hard-delete. The row is kept
for `GROUP_SOFT_DELETE_GRACE_DAYS` before a `purge_soft_deleted_conversations` task
(`tasks.py`, **now confirmed present** — `purge_soft_deleted_conversations()`, filters on
`is_deleted=True` + a `GROUP_SOFT_DELETE_GRACE_DAYS`-day cutoff, see §10) actually
CASCADE-hard-deletes it — during that window an accidentally-deleted group can be
recovered via `.restore()` from the Django admin (see `admin.py`, §2/§10) instead of
being gone the instant someone taps delete.

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
- **`CallRecordingView` — call recording, TASK 21 *(NEW)*, see §2 `CallSession`/§7.24.**
  Not in this file batch (`views.py` itself wasn't re-uploaded), so the exact route/
  method/body shape below is inferred from `livekit_utils.py`'s own docstrings, not
  independently confirmed — treat it as high-confidence, not as-built-verified the way
  the rest of this table is. Start path calls `livekit_utils.start_room_recording
  (room_name)`, stores the returned `(egress_id, output_filepath)` on `CallSession.
  recording_egress_id`/`recording_output_path`, flips `is_recording=True` and sets
  `recording_started_at`. Stop path calls `livekit_utils.stop_room_recording(egress_id,
  output_filepath)`, sets `recording_url` from its return value (may be `None` if
  `LIVEKIT_EGRESS_PUBLIC_BASE_URL` isn't configured or egress hasn't finished muxing
  yet), flips `is_recording=False`. Should catch both `RuntimeError` (LiveKit not
  configured → 503) and `EgressError` (LiveKit rejected/failed the call → 502) from
  `livekit_utils.py` rather than letting either surface as a raw 500.

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

### Study Room Attendance / Streak (`StudyRoomStreakView`) *(NEW — Feature 6, route now
confirmed via `views.py`)*
- `GET /message/study-room/<conversation_id>/streak/` — returns the requesting user's
  own attendance stats for a study-room conversation via
  `attendance_utils.compute_attendance_stats(conversation, user)` (§2/§10):
  `current_streak`, `longest_streak`, `total_classes_attended`, `last_attended`. 404 if
  the requester isn't a member of the conversation.
- `StudyRoomJoinView.post()` is what actually writes a `StudyRoomAttendance` row on each
  join (`get_or_create` keyed on conversation/user/calendar-day, best-effort — a failure
  here is logged and swallowed, never blocks the join itself) — the streak view itself
  only reads.

### Parent Dashboard (`ParentDashboardView`, `views_parent.py`) *(REDESIGNED this batch —
Gap 2/Gap 3, supersedes the Group-primary shape described in earlier revisions of this doc)*
- `GET /message/parent/dashboard/` (header `X-Parent-Token: <token>`, gated by
  `HasValidParentToken` — §10/§12) — response shape changed from earlier revisions:
  ```
  {
    "student_name": "...",
    "classrooms": [
      {
        "classroom_id": 41,
        "classroom_title": "Physics Batch A",
        "attendance_percent": 92.5,
        "homework": {"pending": 1, "submitted": 6, "total": 7},
        "latest_report_card": {
          "period_label": "Term 1", "attendance_percent": 92.5,
          "homework_completion_percent": 85.71, "average_marks": "78.50",
          "teacher_remark": "..."
        },
        "chat_group": {
          "group_name": "Physics Batch A",
          "assignments": {"pending": 2, "submitted": 5, "total": 7}
        }
      }
    ]
  }
  ```
- **🔧 GAP FIX (Gap 2) — `liveclass` `Classroom` is now the PRIMARY, always-present
  source**, not the message app's own `Group`. Earlier, this view looped over the
  student's chat `Group`s first — meaning a fully active classroom (homework, marks,
  report cards, attendance) was silently invisible here whenever chat-group linking
  hadn't happened for it (`chat_group_enabled=False` — teacher opted out, or an older
  classroom predating the feature), with no way for a parent to tell that was even
  happening. `classrooms` is now built from every `liveclass.Classroom` the student has
  an active-or-lapsed pass for (`PassPurchase.status=SUCCESS, is_active=True`) — the
  same enrollment breadth `Classroom.is_enrolled()`/`ClassroomParentCodeGenerateView`/
  `ReportCardViewSet` already use. `chat_group` is now the OPTIONAL part: present only
  when `core.classroom_chat_bridge.get_groups_for_classrooms()` *(a `core`-app bridge
  function, outside this doc's `message`-app scope — see the `liveclass`/`core` app docs
  for its implementation; only its usage contract matters here)* finds a real linked `Group` **and** the student is still an unbanned member
  of it; otherwise `chat_group: null` — the classroom itself still shows up in full.
- **🔧 GAP FIX (Gap 3) — attendance is now 100% liveclass-sourced.** Both
  `attendance_percent` (top-level) and the one inside `latest_report_card` come
  exclusively from `liveclass`'s own session-attendance record (`ClassSession`/
  `SessionParticipant`, via `liveclass.models.compute_attendance_percent_bulk`). The
  message app's own `StudyRoomAttendance` self-check-in streak widget
  (`attendance_utils.compute_attendance_stats_bulk`, §7.16/§10) is **no longer used
  anywhere in this view** — per product decision, since `message` has no
  teacher/student/classroom concept of its own, only chat groups/group-study; that
  helper's own §10 entry and §7.16 remain accurate for what it's still used for
  (`StudyRoomStreakView`), just not here anymore. Consequently `chat_group` carries only
  `group_name` + `assignments`, never an attendance field.
- **`homework` vs `chat_group.assignments` are still never summed (Gap 1, unchanged)** —
  two independently-sourced datasets from two different apps' same-named models
  (`liveclass.Assignment`/`AssignmentSubmission` vs this app's own), imported here under
  a `Liveclass*` alias specifically so no reference is ever ambiguous about which one it
  means. `Assignment.group`'s/`AssignmentSubmission.student`'s `related_name`s were
  separately renamed in `models.py` (`assignments`→`message_assignments`,
  `assignment_submissions`→`message_assignment_submissions`) to resolve the reverse-
  accessor clash between the two apps' models sharing the same `Group`/`User` — doesn't
  affect this view since every query here goes forward through the FK, never through the
  renamed reverse accessor.
- **`latest_report_card` is new this batch** — `liveclass.StudentReportCard`, most recent
  per classroom (`order_by('-id')`, first one kept per classroom in a single pass over
  one query — not one query per classroom). `null` if the student has no report card yet
  for that classroom.
- Still strictly scoped, unchanged from earlier revisions: `views_parent.py`'s own module
  docstring is explicit that this view must never return message text, media, contact
  info, or anything beyond display name / attendance / homework / assignment counts /
  report-card summary — "would this be fine on a report-card-style summary?" is the test
  before adding any new field here.
- `POST /message/parent/verify/` (`ParentVerifyCodeView`, `AllowAny`, throttled by
  `ParentCodeVerifyThrottle` — see §14 for its own confirmed-missing settings scope) —
  body `{"code": "7F3K9QRT"}` (case-insensitive, normalized to uppercase server-side).
  Looks up an active `ParentAccessCode`, bumps its `last_used_at`, mints a fresh
  `ParentToken` (`status=PENDING`, `token=ParentToken.generate_token()`), returns
  `{"parent_token", "student_name", "label", "approval_status"}` — **`approval_status`
  confirmed this batch**, so the parent-side app can show a "waiting for approval"
  screen immediately instead of assuming the dashboard is ready. Distinguishes two
  failure cases: `404` if no active code matches at all (doesn't leak whether the code
  ever existed), vs. a separate `410 Gone` if the code matches but
  `access_code.is_expired` — deliberately split so the parent-side app can show "wrong
  code" vs. "expired, ask the student for a new/renewed one" as different messages,
  rather than one generic "invalid" for both. **Confirmed gap flagged in the view's own
  code, not this doc's inference**: no notification (push/in-app) tells the student a
  new pending request exists — a `TODO(notifications)` comment right there says exactly
  that, and no notification service was available in this file batch to wire it into.
  **🔧 GAP FIX (G-6, mutual consent)**: the minted `ParentToken` starts un-approved
  (`status=PENDING`) rather than immediately usable — see §2/§10/§12 `HasValidParentToken`
  for the enforcement side. Two student-side endpoints now drive the approval, both
  **confirmed coded this batch (`views_parent.py`) but confirmed NOT routed
  (`urls.py`, same batch)** — see §9.4 item 22 for the full status:
  - `GET /message/parent/pending-requests/` (`ParentPendingRequestsView`) — every
    `PENDING` `ParentToken` across **all** of the student's active codes, newest first,
    so the app can show a single "N new parent device(s) want access" badge without
    opening each code's own token list.
  - `POST /message/parent/codes/<code_id>/tokens/<token_id>/approve/`
    (`ParentCodeTokenApproveView`) — flips exactly one `PENDING` token to `APPROVED`
    + stamps `approved_at`; `400` if the token isn't currently `PENDING` (e.g. already
    approved/rejected); `404` if the token doesn't belong to one of the requesting
    student's own codes. Only after this does that device's `parent_token` pass
    `HasValidParentToken`.
  - `ParentCodeTokensView`'s per-device list (below) now also returns each token's
    `status` (`"pending"`/`"approved"`/`"rejected"`) and `approved_at`, so the student's
    app can visually flag a waiting device ("Dad's phone — needs your approval")
    differently from an already-live one — and `DELETE
    /message/parent/codes/<code_id>/tokens/<token_id>/` (`ParentCodeTokenDetailView`,
    already routed) now doubles as "reject" for a `PENDING` token as well as "revoke"
    for an already-`APPROVED` one.
- **Student-side management** (`ParentAccessCodeView`, `IsAuthenticated`,
  `/message/parent/codes/`):
  - `GET` lists the student's own active codes: `id`, `label`, `masked_code` (e.g.
    `"••••9QRT"` — **never the raw code**, see the reveal-once note below), `last_used_at`,
    `created_at`, `expires_at`, `is_expired`, `active_devices` (annotated `Count('tokens')`
    — how many `ParentToken`s/devices are currently verified against this code), and
    `last_revealed_at` (so the student can see, in their own UI, the last time the full
    code was shown again).
  - `POST` generates a new one (`{"label": "Mom"}`, capped at `MAX_ACTIVE_CODES = 5`
    active codes per student — a 400 past that: "Max 5 active parent codes allowed. Pehle
    koi purana revoke karo"). This is the **only** place the raw `code` is ever returned
    in a response body without going through the throttled reveal endpoint below —
    deliberate, since generation is itself a one-time, student-initiated action, not a
    repeatable `GET`. Also stamps `last_revealed_at` at generation time (the student did,
    after all, just see the code).
  - `DELETE` (`{"id": "<uuid>"}`) soft-revokes (`is_active=False`) and cleans up any
    `ParentToken`s tied to that code (cleanup only — `HasValidParentToken` already filters
    on `is_active=True`, so the tokens would stop working regardless).
- **TTL / renewal / reveal-once / per-device controls** (unchanged this batch):
  - `POST /message/parent/codes/<id>/renew/` (`ParentAccessCodeRenewView`) — gives the
    **same** code a fresh TTL (`ParentAccessCode.renew()`, default
    `ParentAccessCode.DEFAULT_TTL_DAYS`) and re-activates it if it had auto-expired.
    Every `ParentToken` already verified against this code (every device) becomes valid
    again immediately — no re-verification, no new code to re-share with every
    parent/guardian on it. Deliberately separate from `POST /parent/codes/` (which issues
    a brand-new code that every device must re-verify). Returns `{"id", "expires_at",
    "is_expired"}`. 404 if the code doesn't belong to the requesting student.
  - `POST /message/parent/codes/<id>/reveal/` (`ParentAccessCodeRevealView`, throttled by
    `ParentCodeRevealThrottle`) — the **only** way to see the full plaintext code again
    once the list (`GET /parent/codes/`) started masking it. Deliberately its own
    throttled/audited action rather than a query-param on the list, so every reveal is a
    distinct, rate-limited event, not a silent side-effect of opening the screen. Returns
    `{"code", "last_revealed_at"}` and updates `last_revealed_at`. 404 if not found/not
    the student's own/inactive.
  - `GET /message/parent/codes/<id>/tokens/` (`ParentCodeTokensView`) — lists every
    individual device/`ParentToken` currently verified against **that one code**
    (`[{"id", "status", "created_at", "approved_at", "last_seen_at"}, ...]` — `status`/
    `approved_at` confirmed added this batch, see §2/§6/§9.4 item 22), so the student can
    tell devices on a shared code apart (e.g. "3 devices on the 'Mom' code") instead of
    only being able to see/manage the code as a whole, and tell a still-`PENDING` device
    apart from an already-`APPROVED` one. 404 if the code isn't the student's own or isn't
    active.
  - `DELETE /message/parent/codes/<id>/tokens/<token_id>/` (`ParentCodeTokenDetailView`)
    — revokes exactly **one** device; the code itself and every other device tied to it
    stay untouched. Now doubles as "reject" for a `PENDING` device, same endpoint. Ownership is checked through `parent_access_code__student`, not a
    bare token-id lookup, so a student can never revoke a token hanging off a code that
    isn't theirs even by guessing a UUID. **This is the actual gap fix these two token
    endpoints exist for**: previously the only way to cut off one lost/stolen device was
    `DELETE /parent/codes/` on the *whole* code, which killed every other device that had
    ever verified it too (e.g. Mom's phone AND Dad's phone both verifying the same
    "Parents" code) — now a single device can be revoked without disturbing the rest.

### Conversation Wallpaper (`ConversationViewSet.wallpaper`) *(NEW)*
- `GET`/`PATCH /conversations/<id>/wallpaper/` — `ConversationWallpaperSerializer`,
  single field `wallpaper_url` on `ConversationParticipant` (§2). Deliberately its own
  endpoint rather than folded into the combined `settings` endpoint (mute/archive/pin/
  draft) — per that serializer's own comment, the frontend/doc contract treats wallpaper
  as its own single-field resource.

### Doubt Queue (`DoubtQuestionViewSet`, routes now fully confirmed via `views.py`)
*(NEW — persistent, upvotable per-classroom question board, see §7.20)*
- `GET/POST /message/groups/<group_id>/doubts/` — list (paginated,
  `StandardPagination`, 20/page) with an optional `?status=answered|unanswered` filter,
  or create (`DoubtCreateSerializer` — `{"text", "is_anonymous"}`). Creating an
  anonymous doubt when `Group.allow_anonymous_doubts` is off → `403`. Membership
  (`GroupMember`, not banned) is required for every action in this ViewSet — enforced
  once in the shared `get_group()` helper.
- `POST/DELETE /message/groups/<group_id>/doubts/<id>/upvote/` — idempotent both ways
  (`get_or_create`/`filter().delete()` against `DoubtUpvote`'s `unique_together`), keeps
  a denormalized `upvotes_count` in sync via `F()` updates rather than counting on every
  read.
- `POST /message/groups/<group_id>/doubts/<id>/answer/` — teacher/admin/mod only
  (`is_group_admin_or_mod`, the same single-source-of-truth check as everywhere else in
  this app), body `{"answer_text"}` (`DoubtAnswerSerializer`), sets `is_answered`,
  `answered_by`, `answered_at`.
- `POST /message/groups/<group_id>/doubts/<id>/reveal/` — teacher/admin/mod only,
  one-way flip of `is_revealed` (a no-op response, no re-broadcast, if already revealed
  or not anonymous) — the only way an anonymous asker's identity becomes visible to
  anyone else.
- **Live delivery**: every action broadcasts over WS to the group's existing
  `chat_{conversation_id}` room (reusing `ChatConsumer`'s existing connection, no new WS
  group) — event `type: 'doubt_broadcast'`, with an inner `event` field distinguishing
  `doubt_created`/`doubt_upvoted`/`doubt_answered`/`doubt_revealed`. **Not confirmed**:
  whether `consumers.py` (not part of this file batch) actually has a `doubt_broadcast`
  handler wired up to receive and forward this — same open question as `meta_update`/
  `transcript_segment_ready` in §7.7/§7.19. Flagged in §9.4.

### Message Translation (`MessageViewSet.translate`) *(NEW — Feature 9, route now
confirmed, see §7.21 for full detail and a **critical, confirmed-broken** config gap)*
- `POST /message/messages/<id>/translate/` — body `{"target_lang": "hi"}`. Delegates to
  `translation_service.translate_text(text, target_lang)` (Google Cloud Translate v2,
  default provider). Response `{"message_id", "target_lang", "source_text",
  "translated_text"}`; results cached a week, keyed to also bust on message edits.
- **This endpoint currently cannot work at all** — the `translate` throttle scope has no
  `DEFAULT_THROTTLE_RATES` entry (`ImproperlyConfigured` on the very first call) *and*
  `GOOGLE_TRANSLATE_API_KEY` isn't set in `settings.py` either (would 503 even past the
  throttle crash). See §7.21/§9.4/§14 for the full writeup — this is a confirmed bug,
  not a documentation gap.

### Focus Mode / Smart DND (`FocusSessionView`, `views_focus.py` — **now wired into
`urls.py`**, confirmed this batch) *(NEW — Feature 12, see §7.18)*
- `POST`/`GET`/`DELETE /message/focus-session/` (single path, method-differentiated,
  `IsAuthenticated`) — start/check/cancel the caller's own focus session. Full
  request/response shapes confirmed — see §7.18 for the writeup.
- `GET /message/focus-session/history/?limit=20` (`FocusSessionHistoryView`) — the
  student's own past focus sessions, newest first, capped by `?limit=` (default 20,
  max 100).
- **Now reachable** *(fix — see §9.0)*: `urls.py` (this batch) imports both
  `FocusSessionView` and `FocusSessionHistoryView` from `views_focus.py` and registers
  `path('focus-session/', ...)` + `path('focus-session/history/', ...)`, exactly per
  `views_focus.py`'s own header-comment snippet. This closes the gap previously tracked
  in §9.4 item 18 — the model, push-side enforcement (§7.13/§7.18), and both REST
  endpoints are now fully connected end-to-end.

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
  `ai_smart_reply` — confirmed present in `settings.py` this batch, `30/min`).
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

### Classroom Copilot (`views_ai.py` → `ClassroomCopilotView`) *(NEW — route now fully
confirmed via `views_ai.py`)*
- `POST /message/ai/classroom-copilot/` — body `{"conversation_id", "question",
  "board_content"}` (`board_content` optional — blank if called from plain chat rather
  than the study room screen). 400 if `conversation_id`/`question` missing, or if
  `question` is over 500 chars. 404 (not 403) if the requester isn't an active member.
- Assembles up to 3 context sections server-side and joins them with `"\n\n---\n\n"`:
  (1) last 30 plain-text messages (same exclusion filters as Smart-Reply, §above), (2)
  the client-supplied `board_content` (truncated to 3000 chars), (3) up to 40
  `ClassTranscriptSegment` rows (`STATUS_DONE` only) whose text contains any of the
  question's own keywords (words >3 chars, first 8 — a naive overlap match, not
  embeddings-based). If literally none of the 3 sections has anything, 400s rather than
  asking Gemini with empty context.
- Delegates to `ai_service.generate_classroom_answer(question, context_text,
  conversation_id)` — answers a student's question using only the classroom context the
  view assembles server-side. Answers in the student's question language, capped ~150
  words unless the question needs more, and is instructed to say plainly when the given
  context doesn't have enough to answer rather than guessing.
- Cached 24h the same way as the rest of `ai_service.py`, keyed on
  `conversation_id:question:context_text` — the `conversation_id` in the key exists for
  the same reason it's in `generate_reply_suggestions`'s key (see §7.12): prevents two
  different conversations that happen to produce byte-identical
  question+context from ever sharing a cached answer.
- Throttled `15/min` (`ClassroomCopilotThrottle`, scope `ai_classroom_copilot`) —
  **but this scope has no `DEFAULT_THROTTLE_RATES` entry in `settings.py`**
  (`ImproperlyConfigured` on first call — see §9.4/§14). 503 if `AI_ENABLED` is False,
  500 with a generic message (`logger.exception`'d) on any other failure.
- **Note on stale assumptions**: this view's own header comment says no
  Assignment/StudyMaterial model exists in this codebase "confirmed against
  `CHAT_APP_DOCUMENTATION.md`" — that's now outdated, since `views_parent.py` (this
  batch) confirms `Assignment`/`AssignmentSubmission` do exist and are queried
  elsewhere (§ Parent Dashboard above). Worth revisiting whether Classroom Copilot
  should add assignment context now that the model is confirmed present — not done as
  of this batch.

### Revision Deck (`views_ai.py` → `RevisionDeckView`) *(NEW — route now fully confirmed
via `views_ai.py`)*
- `GET /message/study-room/<conversation_id>/revision-deck/` — returns the most
  recently generated `RevisionDeck` for this conversation (`{"id", "flashcards", "quiz",
  "created_at"}`, or `{"flashcards": [], "quiz": [], "created_at": null}` if none yet)
  **without** calling Gemini again — a pure read, safe to call as often as the client
  wants for an offline-friendly "open and revise" screen. 404 if not a member.
- `GET ... ?history=true` *(NEW this session — gap fix)* — `RevisionDeck` was already
  designed to keep every generation as its own row (per the model's own docstring: each
  "Generate" tap makes a new row, an older deck can still be useful), but the only read
  path ever exposed was "latest deck only" — a student could never actually get back to
  an older deck once a newer one existed, even though the data was there. This lists
  every deck for the conversation, newest first, capped at 50, lightweight (no
  flashcards/quiz payload, just enough to pick one): `{"decks": [{"id", "session_id",
  "flashcard_count", "quiz_count", "created_at", "generated_by"}, ...]}`.
- `GET ... ?deck_id=<id>` *(NEW this session)* — fetches one specific (not necessarily
  latest) deck's full content, for after picking one from the `?history=true` list.
- `DELETE ... ?deck_id=<id>` *(NEW this session — gap fix)* — removes one specific deck
  (e.g. one generated too early, before enough of the class had happened, that's now
  just clutter). Restricted to whoever generated that specific deck
  (`deck.generated_by_id != request.user.id` → `PermissionDenied`) — a shared study room
  can have several members generating decks off the same class, so one member's "clean
  up my attempt" can't delete another member's. 400 if `deck_id` is missing from the
  query params, 404 if not found. **This resolves §9.4 item 11**, which an earlier
  revision of this doc marked as a confirmed-absent, still-open product gap — it's since
  been built.
- `POST` (same path) — body `{"board_content", "session_id"}` (both optional).
  `session_id` picks which class's transcript to draw from; omitted → the most recent
  session's segments are used (revising "the last class", the common case per the
  client's own comment). Assembles context from the same 3-source pattern as Classroom
  Copilot (last 50 chat messages / whiteboard / up to 80 transcript segments, no
  keyword filtering here since a revision deck wants broad coverage, not a targeted
  answer), then calls `ai_service.generate_revision_deck(content)` (8–12 flashcards + a
  5-question quiz) and **persists** the result as a new `RevisionDeck` row (§2) — decks
  are kept as history, never overwritten (see the `?history=true`/`DELETE` entries above
  for how a student actually gets back to or removes an older one).
- Cached 24h by content hash like the rest of `ai_service.py` — a cache hit still
  creates a fresh `RevisionDeck` row from the cached data (saves the Gemini call, not
  the row-creation) — see §9.4 item 12 for why this is noted as a possibly-unintended
  interaction rather than a bug.
- Throttled `10/min` (`RevisionDeckThrottle`, scope `ai_revision_deck` — tightest of the
  AI throttles in this file, since this is the single costliest call: full chat +
  transcript + board content in one prompt) — **but, same bug class as Classroom
  Copilot above, this scope has no `DEFAULT_THROTTLE_RATES` entry either** (§9.4/§14).
  503 if `AI_ENABLED` is False, 500 with a generic message on any other failure.

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
- **Clear vote entirely** *(NEW — TASK 29)*: `DELETE /message/messages/<id>/poll/vote/`
  (same route as the vote POST, method-differentiated) removes every `PollVote` row this
  user has on the poll, regardless of single/multi-choice, and broadcasts the same
  `poll_update` event as a normal vote. Added because `option_ids: []` already worked for
  "no opinion" on a multi-choice poll, but `PollVoteSerializer` requires at least one
  option for single-choice, so there was previously no way back to "no vote" without
  picking some other option first.
- **Forwarding a poll** *(NEW — TASK 29)*: `MessageViewSet.forward` no longer excludes
  `type=poll`. The `Poll` + `PollOption` rows are explicitly cloned onto the new message
  (plain field-copy isn't enough since poll data lives in separate tables) — the forwarded
  copy always starts **open** and carries **no votes**, regardless of the source poll's
  state, so the new audience's votes are never mixed with the original chat's and closing
  the source poll doesn't close the forward. A poll message whose `Poll` row is somehow
  missing is silently dropped from the forward batch rather than producing a
  client-crashing empty poll bubble. The live WS `chat_message` event for a forwarded poll
  now includes the nested `poll` object too, same as a brand-new poll send.
- **Not supported yet**: voting via WebSocket (REST-only, same as `schedule_message`).
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
- Poll messages (`type=poll`) **are now forwardable** *(TASK 29 — previously excluded,
  see §7.8)*: a poll's own `text` (the question) is always non-empty, so the caption is
  automatically ignored for a forwarded poll, same as for any other message that already
  had its own text.

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

### 7.13 Chat Push Notification Batching / Digest *(rewritten this batch — see history
note at the end)*
- **Current design (this batch): immediate send, WhatsApp-style — no delayed task.**
  `push_utils.send_chat_message_push` sends a push for **every** message right away.
  What varies is single-message vs. digest: a per-`(user, conversation)` rolling "unread
  streak" counter (`chatpush:count:{user}:{conv}`, TTL `CHAT_PUSH_SESSION_SECONDS`,
  default 300s) tracks whether the recipient has an unread streak going for that chat.
  `count == 1` (first message of a fresh streak) → normal single-message push
  (`_send_single_chat_push`). `count > 1` → a digest push (`send_chat_digest_push`, "X
  sent N messages", no `message_id`) sent **immediately**, not after waiting to
  accumulate more — this matches how WhatsApp's own notification tray actually behaves
  (each new message updates/replaces the tray notification for that chat immediately,
  it doesn't hold N messages and send once).
- Counter mechanics: `cache.add(key, 0, TTL)` then `cache.incr(key)` (race-safe — `add`
  is a no-op if the streak is already running, `incr` bumps it), then `cache.touch(key,
  TTL)` to refresh the TTL on every message so the streak stays alive while messages keep
  arriving and expires naturally once they stop (a gap of `CHAT_PUSH_SESSION_SECONDS`
  with no new message resets it, so the next message after a gap is treated as a fresh
  "1 message" push, not a continuation of the old count). `cache.touch()` is wrapped in
  `try/except AttributeError` for cache backends that don't support it — non-fatal, just
  means that one call's TTL refresh is skipped.
- `CHAT_PUSH_DEBOUNCE_SECONDS` env var is still read (back-compat with any existing
  deployment `.env`), but its **meaning changed**: it used to be a wait time, now it's an
  alias for `CHAT_PUSH_SESSION_SECONDS` (the streak TTL) when the newer var isn't set.
- `CHAT_PUSH_DIGEST_ENABLED` *(NEW)* env var (default `True`) — an escape hatch. When
  `False`, all counter/streak logic is skipped entirely and every message gets its own
  plain, ungrouped push via `_send_single_chat_push` — for teams that don't want any
  WhatsApp-style merging at all.
- Mentions still bypass this entirely — `send_mention_push` is always immediate/priority,
  even if the chat is muted (§7.3), and (as of Feature 12, §7.18) even overrides Focus
  Mode's exception rule the same way an announcement does.
- **History — do not reintroduce the old design.** An earlier version of this feature
  (documented in a previous review of this doc) held every message for a fixed
  `CHAT_PUSH_DEBOUNCE_SECONDS` (30s) wait and flushed via a `countdown`-scheduled Celery
  task, `tasks.flush_chat_push_digest`. That task has been **removed entirely** from
  `tasks.py` this batch — nothing schedules it anymore, and it's gone from
  `CELERY_BEAT_SCHEDULE` considerations (§14) since it was never beat-scheduled to begin
  with (one-shot, `.apply_async(countdown=...)`). The bug that older design's own
  introduction closed (`flush_chat_push_digest` didn't exist yet when
  `send_chat_message_push` was already calling it, breaking every ordinary chat push via
  `ImportError`) is now moot — there's no delayed task to be missing.

### 7.17 Announcements *(NEW this batch — Feature 11; both push-side and message-send
activation now confirmed working, see fix below)*
- Teacher/staff (group admin/moderator) messages are treated as a distinct
  "announcement" lane, both visually and at the push layer, via `Message.is_announcement`
  (§2).
- Push side (`push_utils.py`, confirmed): when `is_announcement=True` is passed,
  it flows correctly through `send_chat_message_push`/`send_mention_push`/
  `_send_single_chat_push`/`send_chat_digest_push`, changing both the FCM data payload
  `type` (`"announcement"`/`"announcement_digest"` vs. `"chat_message"`/`"chat_digest"`)
  and the Android notification `channel_id` (`'announcements'` vs. `'chat_messages'`) —
  the Flutter client needs its own `AndroidNotificationChannel` named `'announcements'`
  for this to actually render distinctly, per `_send_multicast`'s own comment.
- `is_announcement=True` also interacts with Focus Mode (§7.18): a `teachers_only`
  focus session still lets announcement pushes through, since that's the entire point of
  the "teachers only" exception rule.
- **Message-send activation — fixed this session** (an earlier revision of this doc
  found the REST send flow never actually computed/passed the flag, defaulting every
  message to `False` regardless of sender role; that gap is now closed on both send
  paths):
  - **REST** (`ConversationViewSet.messages`, POST, `views.py`) — the same
    `is_group_admin_or_mod(group, request.user.id)` check already run for the
    `message_permission` gate is now captured into an `is_announcement` variable
    (private chats always `False`, no group), reused with no extra query, and threaded
    through to both `serializer.save(..., is_announcement=is_announcement)` and the push
    call(s).
  - **WS** (`ChatConsumer.save_message`, `consumers.py`) — same fix, same pattern:
    `is_group_admin_or_mod(group, sender_id)` computed once before
    `Message.objects.create()`, passed to `is_announcement=` on the row and threaded
    through to `send_push_for_message`/`send_mention_push_notification`.
  - See §9.4 item 17 for the full before/after — both call-sites' own comments describe
    the identical prior bug (flag computed for the permission gate, never stored/reused).
- **Non-push behavior (a UI-level "pinned/highlighted announcement lane", if one exists)
  was not part of this file batch** — only the model field and the push-side handling
  are confirmed from this app's side; the client-side rendering isn't.

### 7.18 Focus Mode / Smart DND *(NEW this batch — Feature 12; REST endpoints now
confirmed via `views_focus.py`, but NOT wired into `urls.py`)*
- `FocusSession` (§2, `models_focus.py`) — a student can start a time-boxed "focus
  window" (`start_for_user(user, duration_minutes, exception_rule)`) during which chat
  pushes are suppressed except per an `exception_rule`: `teachers_only` (default — only
  announcement-flagged pushes, §7.17, still get through **in theory** — see §7.17's
  correction, since nothing currently sets that flag) or `nobody` (hard silence, even
  from teachers — meant for exam windows).
- Enforcement is a **single choke point in `push_utils.py`**
  (`_filter_recipients_for_focus`), called at the very top of both
  `send_chat_message_push` and `send_mention_push` before any digest-counting or
  actual send happens — no extra check needed anywhere in `views.py`/`consumers.py`.
  Filtered-out recipients also **skip the unread-streak counter entirely** (§7.13), so
  when their focus session ends they get a fresh "1 message" push rather than a
  digest reflecting every message that arrived while they were focused — intentional,
  since they never received any individual push for those messages to begin with.
- One active session per user — starting a new one cancels any still-running one
  (`FocusSession.start_for_user`'s own transaction-less `update()` + `create()`), not a
  stack of sessions.
- **REST endpoints — now confirmed** (`views_focus.py`, `FocusSessionView`, a plain
  `APIView` like `UserPresenceView`/`DeviceTokenView`, not a ViewSet): single path,
  method-differentiated —
  - `POST /message/focus-session/` — body `{"duration_minutes": 5–480, "exception_rule":
    "teachers_only"|"nobody"}` (`exception_rule` optional, defaults `teachers_only`;
    `duration_minutes` hard-capped 5–480 in `StartFocusSessionSerializer` specifically so
    a fat-fingered duration can't lock a student out for days). Calls
    `FocusSession.start_for_user(...)`, returns the new session + `seconds_remaining`,
    `201`.
  - `GET /message/focus-session/` — `{"active": false}` if none, else the session data
    (`id`, `starts_at`, `ends_at`, `exception_rule`, `cancelled_at`,
    `seconds_remaining`) + `"active": true`.
  - `DELETE /message/focus-session/` — cancels the current active session early
    (sets `cancelled_at`), returns `{"active": false}`; a no-op-shaped `{"active":
    false}` if nothing was active.
  - **Now wired into `urls.py`** *(fix — see §9.0)* — `path('focus-session/',
    FocusSessionView.as_view(), ...)` and `path('focus-session/history/',
    FocusSessionHistoryView.as_view(), ...)` are both registered, per
    `views_focus.py`'s own header-comment snippet. Both endpoints are reachable now.
  - **`FocusSessionThrottle` now wired in** *(Gap Fix #2, this batch)* —
    `FocusSessionView.throttle_classes = [FocusSessionThrottle]`. Previously only
    *suggested* in the file's own header comment (and `settings.py` had the
    `focus_session: 20/min` rate sitting ready-but-unused, per that file's own note —
    see §14); now actually applied, closing the rapid-fire start/cancel spam gap (each
    call is a DB write — start cancels the previous session *and* creates a new one,
    delete is a write too). Own-account-only, no fan-out, so this was never a
    crash-risk the way the throttle-scope gaps in §9.4/§14 are — just unenforced abuse
    prevention until now.
  - **`FocusSessionHistoryView` — new this batch (Gap Fix #3)**:
    `GET /message/focus-session/history/?limit=20` (`IsAuthenticated`, default limit 20,
    max 100). Before this, only the *current* active session was ever visible — a
    student had no way to see how often they'd used Focus Mode, how long they actually
    studied, or how often they ended a session early. The data already existed
    (`FocusSession` rows are never deleted; `start_for_user` "closes" the previous one
    by setting `cancelled_at` rather than removing it), so this is a pure read endpoint
    over `FocusSession.objects.filter(user=request.user).order_by('-starts_at')`. Each
    entry returns `id`, `starts_at`, `ends_at`, `exception_rule`, `ended_early` (whether
    `cancelled_at` is set), and `duration_minutes` — computed as the **actual** elapsed
    time (`cancelled_at or ends_at` minus `starts_at`), not the originally-planned
    duration, so an early-ended session reports how long it really ran. Same
    `focus-session/` URL prefix as the main endpoint, so it needs the same `urls.py`
    wiring described above before it's reachable.

### 7.19 Class Transcript (chunked classroom-audio transcription) *(NEW this batch)*
- A third Gemini-backed transcription flow, alongside voice-message auto-transcription
  (§7.6) — this one is for **live class audio**, uploaded and transcribed in rolling
  chunks rather than one full recording. `transcribe_class_chunk_task` (`tasks.py`)
  reuses the exact same `ai_service.transcribe_audio()` call as voice-note transcription,
  just with a different destination: `ClassTranscriptSegment.text` instead of
  `Message.meta["transcript"]`, and a `mime_type="audio/mp4"` default (chunks are
  recorded client-side as AAC-LC `.m4a`, per the task's own comment — reusing the same
  encoder the study-room call recording path already uses).
- Enqueued by `ClassTranscriptChunkUploadView` (`views_ai.py`, now confirmed — see §6) on
  each new chunk upload.
- Failure handling: on any exception, or if `AI_ENABLED` is `False`, the segment's
  `status` is set to `STATUS_FAILED` and the task returns — no exception propagates, no
  retry beyond the task's own `max_retries=2`. Both search and the Classroom Copilot
  (§7.14) are expected to filter to `STATUS_DONE` segments only, so a failed chunk is
  silently missing from the transcript rather than surfacing an error to the student.
- Live delivery: broadcasts a new WS event, `transcript_segment_ready` (see §8), the same
  plain-passthrough pattern as `meta_update` — an open "class recap" screen sees new
  segments arrive live instead of needing a refresh.
- **`ClassTranscriptSegment` model — now directly confirmed in `models.py`**: `conversation`
  (FK, `related_name='transcript_segments'`), `session_id` (indexed), `speaker` (FK to
  `User`, nullable/`SET_NULL`), `start_offset_seconds`/`end_offset_seconds` (`FloatField`,
  offsets from session start — not wall-clock, so "jump to timestamp" stays correct
  regardless of any one participant's clock skew), `audio_file_url`, `text`, `status`
  (`STATUS_PENDING`/`STATUS_DONE`/`STATUS_FAILED`). Ordered
  `['session_id', 'start_offset_seconds']`; indexes on `(conversation, session_id,
  start_offset_seconds)` and `(conversation, status)`. Search is currently plain
  `text__icontains` (fine at current per-session scale — usually a few hundred segments);
  the model's own comment notes the same `SearchVectorField`+`GinIndex` pattern `Message`
  already uses as the scale-up path if needed later.
  `ClassTranscriptChunkUploadView`/`ClassTranscriptSearchView` themselves are confirmed —
  see §6.

### 7.20 Doubt Queue *(NEW this batch — persistent, upvotable per-classroom question
board)*
- A student posts a question (optionally anonymously) to their group's doubt board;
  classmates upvote; a teacher/admin/mod answers and can `reveal` an anonymous asker's
  identity. See §2 (`DoubtQuestion`) and §6 (`DoubtQuestionViewSet`) for confirmed field-
  and endpoint-level detail.
- Anonymity is **hidden by default from everyone but the asker**, including teachers —
  `is_revealed` must be explicitly flipped (via a `reveal` action) before anyone else's
  serialized response includes the asker's identity. This is the entire point of
  `Group.allow_anonymous_doubts` (§2): it's an opt-in per classroom, not an always-on
  feature, presumably so a teacher who doesn't want anonymous questions in their class
  can turn it off.
- Mirrors the `Poll` (§2/§7.8) pattern closely: a per-item upvote/vote relation, a
  `*_by_me`-style serializer field computed either from a prefetch (`upvotes.all()`, zero
  extra queries when `DoubtQuestionViewSet.get_queryset()` prefetches it) or a live query
  fallback.
- **Views/routes/permission wiring now fully confirmed via `views.py`'s
  `DoubtQuestionViewSet`** — see §6. `DoubtQuestion`'s own migration/constraints
  (`models.py` itself) still weren't part of this file batch.

### 7.21 Message Translation *(NEW this batch — Feature 9)*
- `translation_service.translate_text(text, target_lang, source_lang=None)`
  (`translation_service.py`) — real-time single-message translate, wired (per that
  module's own docstring) to `MessageViewSet.translate`. Default provider is Google Cloud
  Translate's v2 REST API, called with a plain API key
  (`settings.GOOGLE_TRANSLATE_API_KEY`) rather than the SDK/service-account JSON path —
  deliberately the simplest integration, and swappable for another provider (Azure
  Translator, on-device ML Kit relay, etc.) without touching `views.py`, since callers
  only ever import `translate_text`.
- **Fails clean, never as a raw 500**: no API key configured →
  `TranslationServiceUnavailable` (intended to map to a 503, "not configured yet" rather
  than looking like a crash); provider reachable but rejects the request (bad language
  code, etc.) → `TranslationError` (intended to map to a 4xx); provider unreachable/
  timeout (8s) → also `TranslationServiceUnavailable`, logged at `warning`.
- `SUPPORTED_LANGUAGES` — the 10-language picker the Flutter client currently offers
  (English, Hindi, Marathi, Tamil, Telugu, Kannada, Bengali, Gujarati, Punjabi, Urdu),
  kept in sync with `language_picker_sheet.dart` per the module's own comment.
  **Now server-enforced** *(TASK 29 — previously it wasn't)*: `translate_text` raises a
  new `UnsupportedLanguageError` (a `TranslationError` subclass) if `target_lang` (or a
  `source_lang`, if a future caller passes one) isn't in `SUPPORTED_LANGUAGES`, checked
  before the network call. Previously Google's API would silently accept far more ISO
  codes than these 10 and hand back a translation the rest of the product (RTL handling,
  font fallback, UI strings) was never built to support — that gap is now closed.
  `MessageViewSet.translate` also re-checks `target_lang` itself, before even hitting the
  cache, purely so it can return a 400 with the full `supported_languages` list without
  parsing that back out of the exception string; `UnsupportedLanguageError` is still
  caught defensively for the `source_lang` path.
- Throttled by `TranslateThrottle` (`throttles.py`, scope `translate`, `30/min` —
  tighter than plain message-send since every call is a billed external API hit, and a
  user could otherwise translate-spam an entire scroll-back).
- **Route/behavior now confirmed** (`views.py`, `MessageViewSet.translate`):
  `POST /message/messages/<id>/translate/` (router-auto-generated `@action`, same
  pattern as `react`/`pin`/`star`) — body `{"target_lang": "hi"}` (required; 400 if
  missing/blank). Permission is whatever `get_permissions()`'s default branch resolves
  to (`translate` isn't in any of that method's explicit action lists) — same gate as
  every other single-message action, i.e. you can only translate a message you could
  already read. Guards: only `MessageType.TEXT` messages with non-blank text (400
  otherwise); 404 if the message was deleted-for-everyone or deleted-for-you; **400 with
  `{"detail", "supported_languages"}` if `target_lang` isn't in `SUPPORTED_LANGUAGES`**
  *(TASK 29 — checked before the cache lookup, so an unsupported code never produces a
  cached "success" entry)*. Caches the
  translated result for a week, keyed on `message.id` + `int(message.updated_at
  .timestamp())` + `target_lang` — editing the message (`partial_update`, which bumps
  `updated_at`) automatically busts the cache with no separate invalidation step.
  Response: `{"message_id", "target_lang", "source_text", "translated_text"}`.
  `TranslationServiceUnavailable` → `503`; `TranslationError` → `502` (not a generic
  4xx as previously guessed — confirmed `HTTP_502_BAD_GATEWAY`); `UnsupportedLanguageError`
  → `400` (caught defensively even though the explicit check above already covers the
  normal `target_lang` path).
- **🔴 CRITICAL — CONFIRMED (not just "not yet checked") this batch: this endpoint will
  crash on its very first real call, twice over:**
  1. **`translate` has no `DEFAULT_THROTTLE_RATES` entry in `settings.py`.**
     `TranslateThrottle` is a real `UserRateThrottle(scope='translate')` and IS
     attached (via `views.py`'s import), but `settings.py`'s `DEFAULT_THROTTLE_RATES`
     dict — reviewed in full this batch — has no `"translate"` key. Same failure mode
     as every other entry in that dict's own extensive comment history:
     `ImproperlyConfigured("No default throttle rate set for 'translate' scope")` on
     the very first `POST .../translate/`, i.e. a guaranteed 500, not a rare edge case.
  2. **`GOOGLE_TRANSLATE_API_KEY` is not set anywhere in `settings.py`.** Even past the
     throttle crash, `translate_text` would immediately raise
     `TranslationServiceUnavailable` (→ 503) for every single call, since
     `translation_service.py`'s own `getattr(settings, 'GOOGLE_TRANSLATE_API_KEY',
     None)` has nothing to find. The feature is fully coded end-to-end but
     **completely non-functional as currently configured** — needs both a
     `"translate": "30/min"` entry added to `DEFAULT_THROTTLE_RATES` and a real
     `GOOGLE_TRANSLATE_API_KEY` value before it can work at all. See §9.4/§13/§14.

### 7.14 Classroom Copilot *(NEW this batch — Q&A over class context)*
- `ai_service.generate_classroom_answer(question, context_text, conversation_id)` — a
  student asks a question, the caller (`views_ai.py` → `ClassroomCopilotView`) assembles
  classroom context (recent chat + whiteboard notes + transcript excerpts) server-side
  and hands it in; Gemini answers using **only** that context, in the question's own
  language, and is explicitly instructed to say so plainly rather than guess if the
  context isn't enough.
- **This function didn't exist when `views_ai.py` was already importing it** — see §9.0.
  A missing name in a `from .ai_service import (...)` line is a module-load-time
  `ImportError`, which took down **every** view in `views_ai.py` (summary/quiz,
  transcribe, smart-replies, and copilot alike), not just the copilot endpoint. Adding
  the function restored the whole file, not just this one feature.
- Same 24h content-hash cache pattern as the rest of `ai_service.py`, with
  `conversation_id` folded into the key for the same cross-conversation-leak reason as
  `generate_reply_suggestions` (§7.12) — a `question`+`context_text` pair that happens to
  be byte-identical across two different conversations should never share a cached
  answer.
- **Route/permission confirmed via `views_ai.py`** — `POST /message/ai/classroom-copilot/`,
  `IsAuthenticated`, throttled 15/min (`ClassroomCopilotThrottle`, scope
  `ai_classroom_copilot` — **confirmed missing from `settings.py`**, a real bug, see
  §6/§9.4/§14). Full request/response shape and context-assembly detail in §6.

### 7.15 Revision Deck *(NEW this batch — Feature 5, persisted flashcards + quiz)*
- `ai_service.generate_revision_deck(content)` — the natural next step after the plain
  one-off `summary`/`quiz` (§6): combines everything a student has for a class
  (whiteboard notes + recent chat + transcript excerpts, merged server-side by the
  caller) into a self-revision pack — 8–12 flashcards for quick recall plus a 5-question
  quiz for practice — meant to be revisited later rather than read once, which is why the
  result is persisted as a new `RevisionDeck` row (§2) instead of being thrown away like
  the plain summary/quiz responses.
- Same 24h content-hash cache as the rest of the module, so re-tapping "Generate" against
  unchanged content doesn't re-hit Gemini — the persisted `RevisionDeck` row is still
  created by the caller each time regardless of cache hit/miss (see §9.4 for the
  resulting nuance: the cache mostly guards against duplicate/retry taps, not genuine
  reuse, since each real generation is meant to produce its own row).
- **Route confirmed via `views_ai.py`**: `GET`/`POST /message/study-room/
  <conversation_id>/revision-deck/`, throttled 10/min (`RevisionDeckThrottle`, scope
  `ai_revision_deck` — **confirmed missing from `settings.py`**, see §6/§9.4/§14).
  **`GET ?history=true` (list past decks), `GET ?deck_id=<id>` (fetch a specific one),
  and `DELETE ?deck_id=<id>` (owner-only) now all exist** *(fix, this session — an
  earlier revision of this doc said these were "confirmed absent" as a still-open
  product gap; they've since been added — see §6/§9.4 item 11)*. Full request/response
  shape in §6.

### 7.16 Study Room Attendance / Streak + Parent Mode *(NEW this batch — Feature 6)*
- `StudyRoomAttendance` (§2) — one row per study-room join per user per day
  (`(conversation, user, attended_date)` unique), written by `StudyRoomJoinView.post()`.
  Before this, study-room "current session" state was in-memory/cache-only with no
  permanent join history, so a streak had nothing to compute from — confirmed
  `CallParticipant` doesn't cover this (it's calls-only).
- `attendance_utils.compute_attendance_stats(conversation, user)` — the one place
  study-room streak math lives (current streak, longest streak, total classes attended,
  last attended date), used by `StudyRoomStreakView` (student's own view). Pulled out
  into its own module specifically to avoid re-creating a bug pattern this codebase
  already fixed once — `permissions.py`'s `IsGroupAdminOrModerator` docstring documents
  4 independent copies of the same "admin/mod, not banned" rule having drifted out of
  sync before they were consolidated.
  **⚠️ No longer used by `ParentDashboardView`** *(Gap 3, see §6)* — that view's
  attendance now comes exclusively from `liveclass`'s own `ClassSession`/
  `SessionParticipant` data, per product decision (this app has no
  teacher/student/classroom concept of its own). `compute_attendance_stats_bulk` (the
  bulk variant, still documented in §10) is therefore currently only reachable from
  `StudyRoomStreakView`-adjacent code, not the parent dashboard — an earlier revision of
  this doc described it as shared between the two; that's no longer accurate.
- Current-streak logic walks backward day-by-day from today (or yesterday, if today's
  class hasn't happened/been joined yet) while consecutive attended dates continue;
  longest-streak walks the full sorted date list once, tracking the longest run of
  consecutive days ever seen.
- **Both endpoints' routes/permissions/parent-student linking now confirmed** — see §6
  for full detail (`StudyRoomStreakView` in `views.py`, `ParentDashboardView`/
  `ParentAccessCodeView`/`ParentVerifyCodeView` in `views_parent.py`). Parent-student
  linking is via `ParentAccessCode.student` (a direct FK, generated by the student
  themselves via `ParentAccessCodeView.post`) — not a separate invite/linked-account
  model as previously guessed; see §9.4 for what (if anything) is still genuinely open
  here.

### 7.22 Group Management Service Layer *(tasks 27; NOW CONFIRMED WIRED this batch — see
§5/§9.4 item 21)* + Bell-Row Notifications *(task 44; now confirmed to live in
`push_utils.py`, not `services.py` — see §9.4 items 19/22)*
- `services.py` pulls `GroupViewSet.create`/`add_members`/`update_member`'s logic out into
  plain functions (`create_group`, `add_members_to_group`, `remove_group_member`,
  `update_group_member_role`), decoupled from DRF (raises `ValueError`/`PermissionError`,
  never `PermissionDenied`/`ValidationError`) specifically so `core/classroom_chat_bridge.py`
  can drive "create a group" / "add a member" / "change someone's role" the same way
  `GroupViewSet` does, without going through an HTTP request/response cycle. An
  `actor=None` convention lets an internal/system caller (like the classroom bridge) skip
  the admin/mod permission check — the check assumes a real acting user when one is given.
  **`GroupViewSet` now actually calls these** (confirmed this batch, see §5) — this is a
  completed refactor, not parallel/dead code.
- `add_or_reactivate_participant(conversation, user)` also now lives here (moved from
  `views.py`, and the `views.py` copy has since been removed — single source confirmed) —
  creates a `ConversationParticipant` row, or un-sets `left_at` if the user
  had previously left, so a re-added member reliably shows up in the chat again. Reused by
  `services.py` itself (member add), and by `offline_queue.py` (§7.23, sender might have
  left+rejoined while their device was offline).
- `generate_group_invite_code()` — also moved here from wherever it previously lived,
  generates a unique `secrets.token_urlsafe` code, retrying on collision.
- **Bell-row notifications** *(task 44)* — **do NOT live in `services.py`.** A
  `create_bell_rows_for_push(recipient_ids, notif_type, title, message, data=None)`
  function used to be documented here, but it has since been **removed from `services.py`
  entirely** (confirmed via that file's own "REMOVED — was dead code" comment this
  batch): it had zero callers anywhere, because `push_utils.py` was already doing the
  same job a different way — calling `core.services.create_notification()` directly,
  inline, at each of its three push call-sites (`send_chat_message_push`,
  `send_incoming_call_push`, `send_mention_push`), one `Notification` row per recipient
  per event, rather than through a shared batch helper. **Bell-row notifications are
  fully wired and working** — see §10 `push_utils.py` for the confirmed details. This
  item is resolved, not open — see §9.4 items 19/22.

### 7.23 Offline Message Queue *(NEW this batch — task 49, delivery half)*
- `offline_queue.flush_offline_queue(conversation, sender, queued_messages)` — the backend
  half of an offline-first send flow: the Flutter client is expected to queue unsent
  messages locally (with a client-generated `client_id` per message) while offline, then
  POST the whole queue, in composed order, to a new endpoint once connectivity returns.
  This function processes each queued item and returns a per-item result — `{"client_id",
  "status": "created"|"duplicate"|"error", "message_id"?}` — in the same order, so the
  client can reconcile its local queue: drop `created`/`duplicate` items, keep `error`
  items queued for a later retry.
- **Idempotency is the actual backend contract here.** "Local queue + retry-on-reconnect"
  is mostly a client concern — the one thing the backend must guarantee is that retrying
  the same queued message (e.g. the first attempt actually succeeded server-side but the
  response was lost before the client saw it) never creates a duplicate message. This
  relies on the existing `Message.client_id` unique constraint (`unique_message_client_id`,
  §2) plus an `IntegrityError` catch in `_get_or_create_queued_message` — not
  `get_or_create()` alone, which the module's own docstring notes isn't race-safe under
  genuinely concurrent duplicate submissions.
- **Deliberately duplicates `scheduled_messages.py`'s delivery logic** (broadcast +
  unread-count bump + `MessageStatus` rows + @mentions + `GroupMedia` gallery + push) in
  its own `_deliver_queued_message`, rather than sharing one function — per the module's
  own docstring, this codebase's established convention is one delivery function per
  entry-point (REST send, WS send, scheduled-send, and now offline-queue-flush each have
  their own), not a single function every path funnels through.
- One bad item in the batch never aborts the rest — same fail-safe-per-row discipline
  `scheduled_messages.py`'s own caller already uses, so one malformed queued message
  (e.g. a `reply_to` pointing at a message that no longer exists) reports as `"error"`
  for that item only, without losing the rest of the batch.
- A delivery-side failure (broadcast/push) after the `Message` row is already created is
  logged and still reported as `"created"` to the client — treating it as a send failure
  and letting the client retry could otherwise create a second row if the unique
  constraint's race window is ever hit oddly.
- **✅ RESOLVED this batch — REST endpoint is now wired**: `POST
  /message/conversations/<id>/offline-queue/` (`ConversationViewSet.offline_queue_flush`,
  router-based `@action`, no `urls.py` change needed). Body `{"messages": [...]}`, capped
  at `MAX_OFFLINE_QUEUE_BATCH=100` items/request (400 if exceeded). Runs the exact same
  block-check / group `message_permission` / `daily_message_limit` gates as the live
  `messages()` POST — `daily_message_limit` is checked once for the whole batch, not per
  item, same as the live path checks it once per request. Throttled the same way as a
  live send (`MessageSendThrottle` + `MessageSendIPThrottle`, via `get_throttles()`
  treating `offline_queue_flush` as equivalent to `messages` POST). Response:
  `{"results": [...]}` — the per-item list `flush_offline_queue` returns. See §3 table.
  **Still not independently confirmed**: the Flutter-side queue/retry implementation
  itself (no frontend code in this batch). The module's own docstring documents the
  frontend contract it expects (reuse the same `client_id` on every retry of a given
  logical message — a fresh `client_id` per retry defeats the whole idempotency
  guarantee).

### 7.24 Call Recording via LiveKit Egress *(NEW this batch — TASK 21)*
- Closes a gap this doc previously flagged as open (old §9.4 item 2: `CallSession.
  is_recording`/`recording_url` "exist on the model but... recording start/stop code was
  not found anywhere"). That code now exists, in `livekit_utils.py`.
- **What it does**: server-side room-composite recording — every participant's
  audio/video mixed into one file — via LiveKit's Egress REST API, not a client-side
  screen-record. `start_room_recording(room_name)` starts an
  `EncodedFileOutput`/`RoomCompositeEgressRequest` (MP4, `layout="speaker"`), uploading to
  S3 if `LIVEKIT_EGRESS_S3_BUCKET` is configured (falls back to local disk on the egress
  worker otherwise — fine for dev, not for prod) and returns `(egress_id,
  output_filepath)`. `stop_room_recording(egress_id, output_filepath)` stops that job and
  best-effort-builds a public `recording_url` from `LIVEKIT_EGRESS_PUBLIC_BASE_URL` +
  `output_filepath`, or `None` if that base URL isn't configured.
- **Async bridged into sync**: LiveKit's Egress/Room service client is async-only
  (`httpx.AsyncClient` under the hood) — unlike `generate_livekit_token`, which just signs
  a JWT locally and needs no event loop at all. Every caller here is a synchronous DRF
  view, so `_run_async()` bridges with a plain `asyncio.run()` per call rather than
  pushing async/await onto the view layer for what's otherwise one blocking HTTP
  round-trip.
- **Two distinct failure modes, two distinct exceptions** — `RuntimeError` (missing
  `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`, same lazy check `generate_livekit_token` already
  uses) vs. the new `EgressError` (LiveKit itself rejected/failed the start/stop call).
  Deliberately separate so a caller can tell "not configured on this deployment" (503)
  apart from "LiveKit rejected the request, might be worth a retry" (502) without
  string-matching an error message.
- **⚠️ `stop_room_recording`'s `recording_url` is best-effort, not authoritative** — a
  successful `stop_egress` call means LiveKit *accepted* the stop request, not that the
  file has finished uploading/muxing on the egress worker. The correct long-term source of
  truth would be LiveKit's `egress_ended` webhook, which is **not wired into this app** —
  no webhook endpoint exists for it anywhere in this file batch. Until that's added, a
  `recording_url` handed back right after "stop" may 404 for a few seconds.
- `LIVEKIT_HTTP_URL` (the Egress API's base URL) defaults to deriving itself from
  `LIVEKIT_WS_URL` (`ws://`→`http://`, `wss://`→`https://`) rather than requiring a
  second env var for every deployment — only set `LIVEKIT_URL` explicitly if egress
  genuinely sits behind a different ingress than the media server. See §13.
- **Not independently confirmed this batch**: `CallRecordingView` itself (`views.py`
  wasn't re-uploaded) — its route/method/body shape in §6 is inferred from
  `livekit_utils.py`'s own docstrings, not directly read from the view. See §9.4.

### 7.25 Chat / Media Export *(NEW this batch — TASK 29; moved out of §15 "Suggested Next
Facilities" now that it's implemented)*
- `GET /message/conversations/<id>/export/` (`ConversationViewSet.export`) —
  `?type=chat` (default, full text transcript) or `?type=media` (only messages carrying
  a file, detected by `file_url`/`file_urls` being set — not a hardcoded `MessageType`
  list). Optional `?since=`/`?until=` ISO-datetime bounds. Same "still visible to me"
  rule as everywhere else: skips messages deleted-for-everyone or deleted-for-this-user.
  Response is one JSON payload with a `Content-Disposition: attachment; filename="chat_
  export_<conversation_id>_<type>.json"` header, so hitting the URL downloads a file
  instead of rendering an in-app API response — `{"conversation_id", "exported_by",
  "exported_at", "type", "count", "has_more", "messages": [{"message_id", "type",
  "sender_id", "sender_username", "text", "file_url", "file_urls", "thumbnail_url",
  "is_forwarded", "is_edited", "created_at"}, ...]}`.
- **Bundles metadata + URLs, not the media bytes themselves** — deliberately, per the
  view's own comment: actually fetching and zipping every file inside a synchronous GET
  would be slow, memory-heavy work that belongs in a background Celery job (this app
  already runs Celery for other async work, see §10 `tasks.py`), and the desired bundle
  format (zip? one archive per media type?) wasn't specified. The client gets direct file
  URLs today and can fetch/zip them itself; this can become a real async export job later
  once that format is confirmed.
- **Hard-capped, not unbounded**: `EXPORT_MAX_MESSAGES = 5000` per call, with `has_more`
  in the response so exporting a multi-year group chat can't turn into one giant blocking
  request. A caller that needs the rest pages forward by setting `until` to the oldest
  `created_at` it already has.
- No dedicated throttle class for this action — falls through to whatever
  `get_throttles()`'s default branch resolves to for `ConversationViewSet` (not
  `MessageSendThrottle`, since `export` isn't a POST to `messages`/`offline_queue_flush`).

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

**Server → Client, sent only right after `connect()`** (not in response to any client message):

| type | Notes |
|---|---|
| `sync` *(NEW)* | `{conversation_id, count, messages: [...]}` — see "Missed-event replay" below |

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

**🔥 NEW — missed-event replay ("sync") on reconnect.** Previously, any message sent
while a user was offline/backgrounded could only be recovered via a full REST
history refetch once they reopened the chat. `connect()` now also calls
`send_missed_events_sync()`, which sends a `sync` event (see table above) — but
**only to this one new connection**, never broadcast to the room — containing every
message created after the user's `last_read_message` (or `joined_at`, if they've
never read anything in this chat) that they didn't send themselves. Bounded to 200
messages so a very long offline gap (days/weeks) doesn't turn a reconnect into one
heavy query — the client is expected to fall back to normal paginated REST history
for a gap that large; this is purely a small-gap UX smoothing, not a replacement for
history pagination.

**🔥 NEW — presence broadcast to direct chat partners, not just the open room.**
`presence_update` previously only reached users who had this specific conversation's
room open (`chat_{conversation_id}`) — a contact-list/chat-list screen that hadn't
opened this exact chat had no way to learn a partner just came online except polling
`UserPresenceView` (REST, 15s cache TTL — see `cache_utils.py`). `connect()`/
`disconnect()` now also call `broadcast_presence_to_partners()`, which pushes the
same `presence_update` payload straight to every ACTIVE 1-1 (non-group) chat
partner's own `user_{id}` inbox group (`InboxConsumer`, already used for
notifications) — so a contact-list screen updates instantly instead of waiting up to
15s. Deliberately excludes group conversations (a group could have hundreds of
members; pushing presence to all of them on every connect/disconnect would be very
noisy for a feature real chat apps typically only show for direct contacts anyway).
Wrapped in a 3s timeout on `connect()` (logged and swallowed on timeout/failure,
never blocks the connection itself from completing).

**🔧 GAP FIX — presence race condition (multi-device).** `set_presence()`'s
`active_connections` counter update used to be an unlocked read-modify-write
(`get_or_create` + `.save()`). Two connect/disconnect events for the same user
landing at nearly the same time (multi-device, or a flaky network doing a rapid
disconnect→reconnect) could lose an update to the counter, and — more visibly —
whichever write committed to the DB *last* also won in the cache, even if it wasn't
actually the more recent event chronologically. That's the real cause behind a fast
offline→online flip sometimes appearing stuck "offline" until the next presence
event or the 15s cache TTL expired — not something a TTL tweak alone could fix. Now
wrapped in `select_for_update()` so concurrent presence writes for the same user are
serialized and always apply in a consistent, correct order.

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
   chat-message pushes.** *(Historical — superseded this batch, see §7.13.)* At the time,
   `push_utils.send_chat_message_push` already called this task unconditionally as part
   of a debounced batching design, and its absence meant every normal message push (REST,
   WS, and scheduled-delivery paths alike) raised an `ImportError`. The task was added
   back then to fix it — but the debounced-batching design itself has since been replaced
   entirely with an immediate-send design (§7.13), and `flush_chat_push_digest` has now
   been **removed** from `tasks.py` rather than kept. Left here for history; don't
   reintroduce a delayed flush task without re-reading §7.13's "do not reintroduce" note.
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
6. **`admin.py` was missing registrations for `GroupJoinRequest`, `DeviceToken`, and
   `StudyRoomState`.** All three models existed and were in active use, but ops/support
   had no admin UI to inspect a pending private-group join request, debug a user's
   push-notification tokens, or view a study room's saved whiteboard state without
   dropping into a raw DB query. Registered the same way every other model already was.
7. **`livekit_utils.py` used to raise `RuntimeError` at *module import time*** if
   `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` were unset. Since `views.py` (imported at
   Django startup via `urls.py`) imports `livekit_utils`, a missing LiveKit config used
   to crash the **entire process** — plain text chat included — over a config gap in an
   unrelated integration (calls/study-rooms). Same class of bug §9.0 item 2 documents for
   `push_utils.py`. The check is now lazy (`_get_livekit_credentials()`), firing only when
   a token is actually requested (call initiate / study-room join), and raising a clean
   error the calling view can turn into a 503 instead of taking the whole app down.
8. **`ai_service.py`'s cache-hit check used a truthy test (`if cached := cache.get(key)`),
   not a genuine "is this cached at all" test.** `cache.get()` returns `None` on both "not
   cached" and (with the old code) "cached, but the value itself is falsy" — so if an
   empty-but-legitimate result (empty string/list) were ever cached, every future lookup
   for that same content-hash would misread it as a miss and re-call Gemini every time,
   defeating the 24h cache for that key permanently. Fixed with a `_CACHE_MISS` sentinel
   object distinct from `None`, so only a genuine absence of the key counts as a miss.
   `generate_summary` also gained the empty-result guard the other functions already had
   (raise instead of caching whitespace-only output) — it was the one function in the
   file missing it, which is what made the truthy-check bug actually reachable there.
9. **`ai_service.generate_classroom_answer` was imported by `views_ai.py`
   (`ClassroomCopilotView`) but was never defined anywhere in `ai_service.py`.** A
   missing name in a `from .ai_service import (...)` line raises `ImportError` at module
   load time — this took down **every** view in `views_ai.py` (summary/quiz, transcribe,
   smart-replies, copilot), not just the copilot endpoint. Adding the function (§7.14)
   fixed the import for the whole file.
10. **`link_preview.py` had a DNS-rebinding (TOCTOU) SSRF bypass.** The safety check
    resolved the target hostname and rejected private/internal IPs, but then handed the
    **original hostname** (not the validated IP) to `requests.get()` — which resolves DNS
    again itself at connect time. An attacker controlling their own domain's DNS could
    return a safe public IP for the first (check-time) resolution and a private/internal
    IP for the second (connect-time) resolution moments later, passing the check while
    the actual connection went to the internal network. Fixed by resolving the hostname
    exactly once and "pinning" that validated IP for the duration of the request (a
    scoped, lock-guarded `socket.getaddrinfo` override), so `requests` can no longer
    re-resolve to a different address. Safe under this app's Celery prefork workers;
    flagged in the file's own comments as needing a different approach if ever switched
    to a thread/greenlet-based Celery pool.
11. **`ai_service.generate_reply_suggestions`'s cache key was content-only**, unlike the
    conversation-scoped design its own private-chat use case needs. Study-room
    summary/quiz intentionally share a cache across users by content hash (same board =
    same result), but for private-chat smart replies that's a real (if narrow) leak risk:
    two different conversations with byte-identical short context (common first names +
    generic short messages like "ok"/"haan") could otherwise return one user suggestions
    generated from someone else's private conversation. Fixed by folding
    `conversation_id` into the cache key, closing that path. `generate_classroom_answer`
    (§7.14, added new this batch) was built with `conversation_id` in its key from the
    start for the same reason.
12. **`Middleware.py` carried its own independent copy of the WS JWT-auth logic**, and
    `liveclass` (a separate, unrelated app) had a near-identical copy that had never been
    diffed against this one. Since `message` has no dependency on `liveclass` (or vice
    versa), neither app should depend on the other directly — the tested logic (JWT
    `AccessToken` + manual `is_active` check) was moved to a neutral, project-level
    `LearnScroll/ws_auth.py` (alongside `settings.py`/`asgi.py`), and both apps now import
    `JWTAuthMiddleware`/`get_user_from_token` from there. One copy to update going
    forward instead of two silently drifting apart.
13. **`push_utils.py`'s notification batching was redesigned from delayed-debounce to
    immediate-send** *(NEW — see §7.13 for the full before/after)*. Not a bug fix in the
    strict sense (the debounced version worked once `flush_chat_push_digest` existed),
    but a deliberate behavior change: every chat push used to wait up to 30s
    (`CHAT_PUSH_DEBOUNCE_SECONDS`) before being sent at all, even for a lone message with
    no burst — this closes the open concern §9.4 previously raised about that trade-off.
    `flush_chat_push_digest` is now removed from `tasks.py` entirely.
14. **`ConversationListSerializer._membership()` ran a fresh DB query every call, and was
    called twice per row** (`get_unread_count` + `get_my_settings`) — for a paginated
    20-conversation list that's up to 40 extra queries just for this, on top of
    `get_other_participant`'s own per-row query for private chats. Fixed by having
    `ConversationViewSet.get_queryset()` prefetch each conversation's "my membership" row
    as `my_membership_list` (a `Prefetch` with `to_attr`, filtered to `request.user` —
    0 or 1 row per conversation regardless of group size); `_membership()` now uses that
    directly when present (zero extra queries for the whole page), falling back to a live
    query only where the prefetch isn't set up (e.g. a single-object `retrieve`).
15. **`MessageSerializer.get_is_read_by_me`/`get_is_starred` had the same per-row query
    problem as item 14, one level down** (message list, not conversation list). Fixed the
    same way: when the view sets up `with_message_list_prefetch()`, `my_read_status`/
    `my_star` (`Prefetch` `to_attr`s) are already plain Python lists on each message
    object, so both serializer methods become a zero-query emptiness check; the live-query
    fallback still runs correctly for single-message responses that don't set up that
    prefetch.
16. **`IsGroupAdminOrModerator` confirmed migrated onto `group_rules.is_group_admin_or_mod`**
    (the cached single source of truth) instead of its own raw `GroupMember` query — this
    directly confirms, from `permissions.py` itself, the "4 independent copies" fix
    `attendance_utils.py`'s docstring had already described secondhand (see §7.16).
17. **`FocusSessionView`/`FocusSessionHistoryView` (§7.18, Feature 12) are now wired
    into `urls.py`** *(NEW this batch, CONFIRMED)* — `urls.py` now imports both from
    `views_focus.py` and registers `path('focus-session/', ...)` and
    `path('focus-session/history/', ...)`, exactly per `views_focus.py`'s own
    header-comment snippet. This resolves the previously-tracked gap (old §9.4 item 18)
    — both endpoints, the model, and the push-side enforcement (§7.13) are now fully
    reachable end-to-end. `FocusSessionThrottle` (§7.18 Gap Fix #2) was already wired
    onto `FocusSessionView.throttle_classes` before this fix; only the URL route itself
    was still missing.
18. **Parent Mode (Feature 8) gained TTL/renewal/per-device controls, now confirmed
    routed** *(NEW this batch, CONFIRMED via `permissions.py` + `urls.py`)* — previously
    a `ParentAccessCode` had no expiry at all (`is_active=True` was the only check), so a
    lost parent phone kept working indefinitely until the student manually revoked it.
    `HasValidParentToken` (`permissions.py`) now enforces two independent expiries:
    `ParentAccessCode.expires_at`/`is_expired` (an absolute expiry on the *code* itself —
    crossing it blocks every device sharing that code, until the student calls
    `POST /message/parent/codes/<id>/renew/`, `ParentAccessCodeRenewView`) and
    `ParentToken.INACTIVITY_TTL_DAYS`/`is_expired` (a rolling 30-day-inactivity expiry
    on *one device's* token only — a stale phone quietly stops working without
    affecting the code or any other device). Also newly routed: per-device management
    (`GET /message/parent/codes/<id>/tokens/` list, `DELETE
    /message/parent/codes/<id>/tokens/<token_id>/` revoke a single device without
    killing the whole code) and a reveal-once endpoint (`POST
    /message/parent/codes/<id>/reveal/`, `ParentAccessCodeRevealView` — the student's
    code list now shows a masked code, and this is the only way to see the full
    plaintext again). `views_parent.py` itself wasn't in this file batch, so the exact
    request/response shapes for these four new endpoints aren't confirmed beyond what
    `permissions.py`'s and `urls.py`'s own comments describe — see §7.16/Parent
    Dashboard section.
19. **Two `urls.py` frontend/doc-contract mismatches fixed, both additive aliases onto
    already-existing ViewSet actions** *(NEW this batch, CONFIRMED)*:
    - Scheduled messages: `message_api_service.dart`/`PROJECT_ARCHITECTURE.md` describe
      `GET`/`POST /message/conversations/<id>/scheduled/` and
      `PATCH`/`DELETE /message/scheduled/<id>/`, but the actual `@action` names on
      `ConversationViewSet`/`MessageViewSet` are `schedule-message`/`scheduled-messages`
      and a nested `messages/<id>/schedule/` — none of which match, so every
      scheduled-message call from the documented contract 404'd. Fixed by registering
      the documented paths as extra routes pointing at the same existing view methods —
      no new logic, both the old and new paths now work.
    - Group photo removal: `GroupViewSet.remove_photo` existed as an action but had no
      matching route; added `DELETE /message/groups/<id>/photo/`.
20. **🔴 `ParentCodeRevealThrottle` added** (`throttles.py`, scope `parent_code_reveal`,
    for `ParentAccessCodeRevealView`, item 18 above) — bounds how often the full plaintext
    of a parent code can be re-revealed (10/hour suggested), so a compromised student
    session/device can't be used to bulk-scrape every active code's plaintext at will.
    **Now confirmed missing** (upgraded from "not confirmed" — `settings.py` has been
    reviewed in full this session and has no `parent_code_reveal` entry) — same bug class
    as item 15: `ParentAccessCodeRevealView`'s first call will raise
    `ImproperlyConfigured`. 30-second fix: add `"parent_code_reveal": "10/hour"` to
    `DEFAULT_THROTTLE_RATES`. This is now the **only** remaining missing-rate gap of this
    kind in the app. See §14's throttle-rates table.
21. **Call recording (TASK 21) wired up end-to-end** *(NEW this batch, CONFIRMED)* —
    closes old §9.4 item 2, which flagged `CallSession.is_recording`/`recording_url` as
    dead fields with no recording-trigger code anywhere. `livekit_utils.py` now has
    `start_room_recording`/`stop_room_recording` (LiveKit Egress REST calls, bridged
    async→sync via `asyncio.run()`) and a dedicated `EgressError`; `models.py` brought
    `is_recording`/`recording_url` back plus three new fields
    (`recording_egress_id`/`recording_started_at`/`recording_output_path`) needed to
    actually drive a start→stop lifecycle across two separate HTTP requests. See §2
    `CallSession`, §6 Calls, §7.24, §10, §13. `CallRecordingView` itself (the view that
    calls these) is not in this file batch, so its route/shape is inferred, not
    independently confirmed — see §9.4.

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

1. ~~**`is_deleted` on `BaseModel`** exists but nothing in the read code ever sets it or
   filters by it — either dead field or a soft-delete feature that was never finished.~~
   **Resolved** — no longer a dead field, and now directly confirmed in `models.py`:
   `SoftDeleteManager.get_queryset()` filters `is_deleted=False`, wired as `objects` on
   `BaseModel` itself (so it applies to **every** model in the app, not just
   `Conversation`/`Group`), with `all_objects = models.Manager()` alongside it for
   unfiltered access. `GroupViewSet.destroy()` sets it via `group.soft_delete()`/
   `conversation.soft_delete()` instead of hard-deleting (§5), `admin.py`'s
   `SoftDeleteAdmin` (§10) exposes soft-deleted rows + a restore action via
   `.all_objects`, and `tasks.purge_soft_deleted_conversations` (§10) is the scheduled
   sweep that reclaims storage after the grace window. See §5 and the
   `admin.py`/`BaseModel` entries in §2/§10 for the full mechanism.
2. ~~**`CallSession.token` / Agora fields** (`is_recording`, `recording_url`) exist on the
   model but the app has fully moved to LiveKit — `token` looks unused;
   recording start/stop code was not found anywhere.~~
   **Resolved.** `token` is confirmed gone (dropped in migration
   `0903_remove_callsession_legacy_agora_fields`, not coming back) and recording is now
   fully wired — TASK 21, see §2 `CallSession`, §6 Calls, §7.24, §9.0 item 21, §10, §13.
   The only remaining unconfirmed piece is `CallRecordingView` itself, not in this file
   batch — see the note at the end of this list.
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
   **Update this batch**: a `management/commands/send_scheduled_messages.py` **does**
   now exist too, but it's explicitly a manual/backup trigger (its own docstring is
   emphatic about this), not meant to also be cron-scheduled alongside Celery beat in
   normal operation — the two together would be redundant. It now shares the identical
   `select_for_update(skip_locked=True)` + 200/batch locking pattern as the Celery task
   specifically so running both at once (e.g. someone cron'd it "just in case") is safe
   rather than a double-send risk like it would have been before this fix. No action
   needed unless ops actually has this cron'd somewhere — worth a quick check that it
   isn't, since redundant-but-safe is still redundant.
5. **`STORAGES["default"]` is still local `FileSystemStorage`** *(NEW note)* —
   `upload_view.py` uses `default_storage`, so switching the backend (e.g. to S3 via
   `django-storages`) needs zero code changes, only a `settings.py` change. Not urgent
   for a single-server deployment, but local disk means uploaded files don't survive a
   redeploy/scale-out to multiple app servers. **Confirmed this batch**:
   `MessageUploadAPIView.post()` already built its response URL the storage-agnostic way
   (`default_storage.url(saved_path)`, only falling back to
   `request.build_absolute_uri(...)` when that isn't already an absolute `http(s)` URL —
   i.e. only for local `FileSystemStorage`, where it's needed). This avoids a
   double-scheme bug an earlier, more naive version would have hit under S3: unlike local
   storage, `default_storage.url()` under S3 already returns a full
   `https://bucket.s3...`/CDN URL, and running that through `build_absolute_uri()` a
   second time would have produced a broken, doubled-up URL. So the switch described in
   this item really is a `settings.py`-only change today — `upload_view.py` doesn't need
   a matching code change when it happens.
6. ~~**`link_preview.py`'s OG-tag parser is regex-based, not a real HTML parser**~~
   **Resolved this batch** — the two hand-rolled regexes (`_OG_TAG_RE`/`_TITLE_TAG_RE`)
   have been replaced with a real HTML parser (`BeautifulSoup`, `html.parser` backend —
   stdlib, only adds the `beautifulsoup4` dependency). This was exactly the failure mode
   flagged here: attribute order flipped, `name=` used instead of `property=` for OG tags
   (now explicitly accepted as a fallback, first match per property wins), quoting/
   whitespace variance — all previously silent "no preview" misses on otherwise normal
   pages, now handled for free by parsing real DOM structure instead of guessing at it
   via string patterns. Only the parsing step changed; the SSRF-safe fetch (DNS-pin,
   private-IP block, size/time caps, §9.0 item 10) is untouched. A malformed/truncated
   `<head>` (expected sometimes, since the fetch stops at `_MAX_BYTES` or the first
   `</head>`) is still treated as a soft "no preview found", same as every other
   fail-path in this function — never crashes.
7. ~~**`ai_smart_reply` throttle scope needs a `DEFAULT_THROTTLE_RATES` entry**~~
   **Resolved/confirmed present this batch** — `settings.py` (reviewed in full this
   batch) has `"ai_smart_reply": "30/min"`. No action needed.
8. ~~**`CHAT_PUSH_DEBOUNCE_SECONDS` means every ordinary chat push is delayed by
   design**~~ **Resolved this batch — see §9.0 item 13 / §7.13.** The debounced-wait
   design has been replaced with immediate-send; `CHAT_PUSH_DEBOUNCE_SECONDS` is now a
   back-compat alias for `CHAT_PUSH_SESSION_SECONDS` (the unread-streak TTL, not a wait
   time) and no push is deliberately delayed anymore.
9. ~~**Classroom Copilot, Revision Deck, and Study Room Attendance/Parent Mode
   (§7.14–§7.16) are only confirmed from the `message`-app side**~~ **Resolved this
   batch** — `views.py`, `views_ai.py`, and `views_parent.py` have all now been reviewed.
   Routes, request/response shapes, and permission classes for all four are confirmed —
   see §6. Throttle *scopes* are confirmed to exist in code for all four, but 2 of the 4
   (`ai_classroom_copilot`, `ai_revision_deck`) are **confirmed missing** from
   `settings.py` — see item 15 below (this is now a definite bug, not an open question).
10. ~~**No confirmed parent↔student linking model.**~~ **Resolved this batch** —
    `ParentAccessCode.student` is a direct FK the student themselves sets by generating
    the code (`ParentAccessCodeView.post`, `views_parent.py`).
    ~~There's no separate invite/approval flow; whoever holds a valid, unexpired code the
    student generated can link to that student's data. This is a simpler trust model than
    a mutual-consent link — worth a product-level gut-check.~~ **The mutual-consent gap
    itself is now resolved this session (G-6)** — `permissions.py`'s `HasValidParentToken`
    requires `status=ParentToken.Status.APPROVED` in addition to the existing checks, and
    a merely-verified token sits at `status=PENDING` until the student approves it via
    (per `permissions.py`'s own comments) `views_parent.ParentCodeTokenApproveView`. A
    leaked/screenshotted code alone no longer grants live access — see §2/§6/§12. **This
    does introduce a new confirmed gap, tracked below**, since the `status` field itself
    isn't in `models.py` yet.
11. ~~**`RevisionDeck` has no confirmed "list past decks" or "delete a deck" endpoint.**~~
    **Resolved this session** — `RevisionDeckView` (`views_ai.py`) now defines
    `GET ?history=true` (list every deck for the conversation, newest first, capped at
    50, lightweight — no flashcards/quiz payload), `GET ?deck_id=<id>` (fetch one
    specific deck's full content), and `delete` (`?deck_id=<id>`, restricted to whoever
    generated that specific deck). See §6/§7.15 for full shapes. The product gap this
    item used to flag (students needing to browse older decks before an exam) is closed.
12. **`generate_revision_deck`'s 24h content-hash cache sits a bit awkwardly against the
    model's "always create a new persisted row" design** — a cache hit still means the
    caller creates a fresh `RevisionDeck` row from the cached data, so the cache saves a
    Gemini call on a duplicate/retry tap but doesn't prevent duplicate rows from being
    created. Worth confirming this is the intended behavior (vs., say, returning the
    existing recent `RevisionDeck` row instead of both hitting cache *and* inserting).
13. **`transcribe_audio`'s cache key is a hash of `file_url`, not of the audio bytes
    themselves** — called out in the file's own comment as an intentional, accepted
    trade-off (a content-based key would require downloading the audio before the
    cache-check could even run, defeating the point of caching), not a bug. Noted here
    only so it isn't mistaken for one: a forwarded voice message that lands at a new
    storage path will be re-transcribed even though the audio content is identical.
14. ~~**Message Translation (§7.21, Feature 9) is only confirmed from the
    service-layer side**~~ **Upgraded from "unconfirmed" to "confirmed broken" this
    batch** — see item 15 below for the specifics; short version: `views.py`'s
    `MessageViewSet.translate` is fully coded and correctly wired to
    `translation_service.py`, but it cannot currently serve a single successful request
    (missing throttle-rate entry + missing API key, both confirmed absent from
    `settings.py`).
15. ~~**🔴 Six throttle scopes are wired to real views via `throttle_classes`/
    `get_throttles()` but have NO `DEFAULT_THROTTLE_RATES` entry in `settings.py`**~~
    **Resolved this session** — `settings.py` now has all six: `"translate": "30/min"`,
    `"parent_code_verify_ip": "10/min"`, `"ai_class_transcript_chunk": "30/min"`,
    `"ai_class_transcript_search": "60/min"`, `"ai_classroom_copilot": "15/min"`,
    `"ai_revision_deck": "10/min"` — each matching its throttle class's own intended rate.
    `"focus_session": "20/min"` is present too. Feature 9 (translate), Parent Mode's
    unauthenticated verify endpoint, and all 4 of the newest AI endpoints (transcript
    chunk upload, transcript search, classroom copilot, revision deck) no longer 500 on
    first use. See §14's throttle-rates table for the current per-scope status.
16. ~~**🔴 `GOOGLE_TRANSLATE_API_KEY` is not set anywhere in `settings.py`**~~
    **Resolved this session** — `settings.py` now sets
    `GOOGLE_TRANSLATE_API_KEY = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "")`. Feature 9
    still needs the actual env var populated at deploy time (an empty string is falsy, so
    `translate_text` still 503s until `GOOGLE_TRANSLATE_API_KEY` is actually set in the
    environment) — but the code-side wiring gap (item 15 + this item) is fully closed.
    See §13/§14.
17. ~~**🔴 Announcements (§7.17, Feature 11) does not actually activate via the REST
    message-send path.**~~ **Resolved this session** — `ConversationViewSet.messages`
    (REST) now captures `is_announcement = is_group_admin_or_mod(group, request.user.id)`
    at the same point it already checks `message_permission` (reusing that result, no
    extra query), and passes it both into `serializer.save(..., is_announcement=
    is_announcement)` and through to the push calls. `consumers.py` (WS send path, now
    confirmed in this batch — previously flagged as unconfirmed here) has the identical
    fix: `save_message()` computes the same `is_group_admin_or_mod(group, sender_id)`
    check before `Message.objects.create()` and threads `is_announcement` through to
    `send_push_for_message`/`send_mention_push_notification` too. Both call-sites'
    fix comments describe the exact same prior gap this item used to flag (the flag was
    computed for the permission gate but never stored/reused, so it silently defaulted
    to `False` on every message regardless of sender role) — the model field and
    push-side handling (`push_utils.py`) were already correct; only the REST and WS
    send paths needed to actually compute and pass the value, and now both do.
18. ~~**`FocusSessionView` (§7.18, Feature 12) is fully coded but not wired into
    `urls.py`**~~ — **RESOLVED this batch**, see §9.0.
19. ~~**`services.create_bell_rows_for_push` (§7.22, task 44) is confirmed NOT called
    anywhere.**~~ — **RESOLVED this batch, but not the way it sounds**: this function has
    since been **removed from `services.py` entirely** (confirmed via that file's own
    "REMOVED — was dead code" comment). It's not that `views.py` needs to start calling
    it — it's that `push_utils.py` was **already** creating bell rows a different way the
    whole time: `send_chat_message_push`, `send_incoming_call_push`, and
    `send_mention_push` each call `core.services.create_notification()` directly, inline,
    one row per recipient per event. Bell/notification-center rows for chat messages,
    calls, and mentions are all confirmed being created today. See §10 `push_utils.py`
    and §7.22.
20. ~~**`offline_queue.flush_offline_queue` (§7.23, task 49) is confirmed to have no
    REST endpoint anywhere.**~~ — **RESOLVED this batch**: `views.py` now has
    `ConversationViewSet.offline_queue_flush` (`POST
    /conversations/<id>/offline-queue/`, router-based, no `urls.py` change needed),
    importing and calling `flush_offline_queue` directly, with the same block/permission/
    daily-limit gating and throttling as a live send. See §3/§7.23.
21. ~~**`services.py`'s `create_group`/`add_members_to_group`/`remove_group_member`/
    `update_group_member_role` are confirmed NOT used by `GroupViewSet`.**~~ —
    **RESOLVED this batch**: `views.py` now imports `add_or_reactivate_participant,
    create_group, add_members_to_group, remove_group_member, update_group_member_role`
    from `.services`, and `GroupViewSet.create`/`add_members`/`update_member` each call
    into those functions instead of duplicating the logic inline (converting the plain
    `PermissionError`/`ValueError` they raise to `PermissionDenied`/400 at the DRF
    boundary). The module-level duplicate of `add_or_reactivate_participant` that used to
    live in `views.py` has been removed — `services.py` is now the single source, exactly
    as its own docstring always claimed. See §5/§7.22.
22. **`ParentToken.status`/`Status`/`approved_at` (G-6 mutual-consent gap) — model-field
    half now RESOLVED, routing half still open.** Previously flagged as a confirmed
    missing model field (an `AttributeError`/500 waiting to happen). This batch's
    `views_parent.py` review resolves that: `status`, `Status.PENDING`/`APPROVED`/
    `REJECTED`, `approved_at`, and a `generate_token()` classmethod are all confirmed in
    active use — `ParentVerifyCodeView` creates tokens `status=PENDING`,
    `ParentCodeTokenApproveView` flips one to `APPROVED` + stamps `approved_at`,
    `ParentCodeTokensView`'s per-device list returns both fields. `models.py` itself
    still isn't part of any file batch, so the model source can't be read directly, but
    three independent call-sites agreeing on the same field/method names is about as
    confirmed as this doc can get without it. **What's still open**: `urls.py`, reviewed
    in this exact same batch as `views_parent.py`, still only imports
    `ParentAccessCodeView`, `ParentAccessCodeRenewView`, `ParentAccessCodeRevealView`,
    `ParentCodeTokensView`, `ParentCodeTokenDetailView`, `ParentDashboardView`,
    `ParentVerifyCodeView` — **not** `ParentCodeTokenApproveView` or
    `ParentPendingRequestsView`, both of which are fully coded and ready. So today, a
    student still has zero *reachable* way to approve a pending parent-device request —
    the exact same end-user-facing outcome as before (every token effectively stuck
    unusable), just for a different underlying reason now (missing route, not missing
    model field). Two `path()` entries need adding to `urls.py`: something like
    `POST /message/parent/codes/<id>/tokens/<token_id>/approve/` and
    `GET /message/parent/pending-requests/` — see §6/§10 for the two views' confirmed
    request/response shapes. **Also flagged in `ParentVerifyCodeView`'s own code (not
    this doc's inference)**: no push/in-app notification tells the student a new pending
    parent-device request exists at all — `ParentPendingRequestsView` (once routed)
    would be the only way to discover one, and only if the student thinks to poll it.
23. **`DoubtQuestion.context_type`/`context_id` (Task 16, see §2) — reader/writer now
    partially confirmed, and it's an EXTERNAL app, not `message` itself.**
    `management/commands/apply_doubtquestion_context_fields.py` makes `group`/
    `conversation` nullable and adds the new generic context columns + index +
    constraint. This batch's `services.py` review resolves who actually uses them:
    `services.answer_doubt_question(*, doubt, actor, answer_text, answered_by=None)`
    *(new function, not previously documented)* is a generalized answer-path built to
    handle **both** shapes of `DoubtQuestion` — group doubts (existing
    `doubt.group` set, permission-checked via `is_group_admin_or_mod`) and
    context-pointer doubts (`doubt.context_type`/`context_id` set, `actor=None`,
    permission already checked by the caller). Its own docstring names the actual
    caller: **`testseries/bridge.py::answer_query_on_series()`** — a completely
    different, previously-unmentioned-in-this-doc app (`testseries`) that creates/answers
    `DoubtQuestion` rows scoped to a test-series query rather than a chat group, reusing
    this app's `DoubtQuestion` model as shared infrastructure. This is a new confirmed
    cross-app dependency in the same family as the existing `liveclass`/`core` ones (§6
    Parent Dashboard, §7.16) — worth remembering that `message.DoubtQuestion` now has
    **two** independent consumers with different data shapes sharing one table.
    **However — `message`'s own `DoubtQuestionViewSet.answer()` (views.py, §6/§7.20)
    does NOT call `services.answer_doubt_question()` at all**: it hand-rolls the
    identical set/save steps inline instead (`doubt.answer_text = ...`, `doubt.
    is_answered = True`, etc., duplicated verbatim). That's the same
    duplicate-logic-drifts-out-of-sync anti-pattern this codebase has already paid for
    once (`group_rules.is_group_admin_or_mod`'s docstring: 4 independent copies of one
    permission rule) and fixed for group actions via `services.py` (task 27, §9.4 item
    21) — `answer_doubt_question` looks like it exists specifically to be that same fix
    for doubt-answering, but the REST view was never switched over to call it. `message`'s
    own REST route (`/message/groups/<group_id>/doubts/<id>/answer/`) still only ever
    creates/reads group-scoped doubts — no context-based create/list path exists in
    `views.py`/`serializers.py` for the `message` app's own API surface; the
    context-pointer shape is created/answered entirely from the `testseries` side via the
    shared model + `services.answer_doubt_question`, not through any `message`-app
    endpoint. **A second, smaller gap flagged inline in `services.py` itself (not this
    doc's own inference)**: when `doubt.context_type == 'testseries_attempt'`,
    `answer_doubt_question` tries to fire a
    `core.models.Notification.NotifType.TESTSERIES_QUERY_ANSWERED` notification — that
    enum member is **not confirmed to exist** on `core.models.Notification` yet (`core`
    app not in this file batch), so answering a testseries doubt today would raise
    `AttributeError` at the notify step specifically (the answer itself — `is_answered`,
    `answer_text`, `answered_by`, `answered_at` — still saves successfully first, since
    the notify call comes after `doubt.save()`).
24. **`management/commands/cleanup_expired_messages.py` (NEW this batch) duplicates
    logic `tasks.cleanup_expired_messages` (Celery beat, every 15 min, §10) already
    covers in production** — not a gap it fills. The command's own docstring assumes
    hard-delete was never implemented ("disappearing messages ka sirf half implement
    tha"); this doc's §9.1 item 3 / §10 `tasks.py` entry confirms otherwise — the Celery
    task already hard-deletes via `Message.all_objects` on a schedule. Not a bug — a
    `--dry-run`-capable manual CLI trigger for the same sweep is a reasonable ops tool —
    but worth (a) confirming this command isn't *also* cron-scheduled alongside the
    Celery beat entry (redundant, though harmless either way since both just delete rows
    matching the same filter), and (b) reconciling the batch-size default mismatch (this
    command: 1000/batch; the Celery task: 500/batch) if both are meant to be
    interchangeable.
25. **`management/commands/expire_stale_parent_access.py` (NEW this batch) is not
    registered anywhere** — no `CELERY_BEAT_SCHEDULE` entry in `settings.py`, no
    confirmed external cron. Low urgency (explicitly DB hygiene only, per its own
    docstring — `HasValidParentToken` enforces expiry live regardless), but until it's
    scheduled somewhere, stale `ParentToken`/`ParentAccessCode` rows simply accumulate
    indefinitely instead of being cleaned up. See §2 `ParentAccessCode`/`ParentToken`,
    §1 File Map.

---

## 10. Helper Modules Reference

### `group_rules.py`
- `is_group_admin_or_mod(group, user_id) -> bool`
- `check_group_permission(group, user_id, permission_field) -> (allowed, reason)` —
  `permission_field` ∈ `{'message_permission','call_permission','study_room_permission'}`
- `check_daily_message_limit(group, user, conversation) -> (allowed, reason)` — admin/mod
  exempt; simple day-boundary `Message.count()` query (no extra table)

### `services.py` *(NEW — task 27, confirmed wired into `GroupViewSet` this batch, see
§5/§7.22; the task-44 bell-row helper that used to be documented here has been removed —
see §10 `push_utils.py`)*
- `create_group(created_by, name, description='', photo_url=None, is_private=False,
  member_ids=()) -> Group` — creates the `Conversation` + `Group` in one transaction,
  creator as `ADMIN`, given `member_ids` as plain `MEMBER` (creator auto-excluded from
  that list if present, never double-added)
- `add_members_to_group(group, actor, user_ids) -> list[User]` — `actor=None` skips the
  private-group admin/mod check (internal/system callers, e.g. `classroom_chat_bridge`);
  already-member users are silently skipped, not an error. Returns only the
  newly-added users
- `remove_group_member(group, actor, user_id) -> None` — self-remove needs no permission
  check; removing someone else requires admin/mod (skipped if `actor=None`)
- `update_group_member_role(group, actor, user_id, data) -> GroupMember` — `data` may set
  any of `role`/`is_muted`/`is_banned`; banning/unbanning also toggles the matching
  `ConversationParticipant.left_at`
- `require_group_admin_or_mod(group, user) -> None` — raises plain `PermissionError` (not
  DRF's) if `user` isn't admin/mod; caller converts to whatever error shape it needs
- `add_or_reactivate_participant(conversation, user)` — moved here from `views.py`;
  creates a `ConversationParticipant` or un-sets `left_at` on an existing one. Reused by
  `offline_queue.py` (§7.23)
- `generate_group_invite_code() -> str` — unique `secrets.token_urlsafe` code, retries on
  collision
- ~~`create_bell_rows_for_push(...)`~~ — **removed from this file** (confirmed dead code,
  zero callers — `push_utils.py` already writes bell rows inline at each push call-site
  via `core.services.create_notification()`, see §10 `push_utils.py` and §7.22).
- **Now confirmed actually used by `GroupViewSet`** (§5) — `create_group`,
  `add_members_to_group`, `remove_group_member`, `update_group_member_role`, and
  `add_or_reactivate_participant` are all called from `views.py`, not parallel/dead code.
- `answer_doubt_question(*, doubt, actor, answer_text, answered_by=None) -> DoubtQuestion`
  *(NEW this batch — Task 16)* — shared answer-path for **both** shapes of
  `DoubtQuestion` (§2): group doubts (`doubt.group` set, checks `actor` is admin/mod via
  `require_group_admin_or_mod` — skipped if `doubt.group_id is None` or `actor=None`) and
  context-pointer doubts (`doubt.context_type`/`context_id` set, no group to
  permission-check against, so callers that already authorized the action themselves
  pass `actor=None`). `answered_by` is tracked separately from `actor` specifically so an
  `actor=None` caller can still correctly record who answered. Raises `ValueError` if
  already answered, or if neither `actor` nor `answered_by` is given. **Confirmed caller:
  `testseries/bridge.py::answer_query_on_series()`** — a different app, not `message`
  itself — which passes `actor=None` (having already verified `teacher ==
  series.creator` on its own side) and its own verified `teacher` as `answered_by`. **Not
  called by `message`'s own `DoubtQuestionViewSet.answer()`** (views.py) — that action
  still duplicates the same set/save steps inline rather than calling this function; see
  §9.4 item 23 for why that's worth fixing. Also contains an inline-flagged, not-yet-
  confirmed dependency: fires a `core.models.Notification.NotifType.
  TESTSERIES_QUERY_ANSWERED` notification when `doubt.context_type ==
  'testseries_attempt'`, an enum member not confirmed to exist on `core.models.
  Notification` (that app isn't in any file batch so far) — the answer itself still
  saves fine either way, only that one notify call is at risk of `AttributeError`.

### `offline_queue.py` *(NEW — task 49, see §7.23)*
- `flush_offline_queue(conversation, sender, queued_messages) -> list[dict]` — processes a
  batch of locally-queued messages (each with a required `client_id`), in order, returning
  one `{"client_id", "status": "created"|"duplicate"|"error", "message_id"?, "detail"?}`
  per item so the client can reconcile its local queue
- `_get_or_create_queued_message(conversation, sender, item) -> (Message, created: bool)`
  — idempotent create backed by the DB-level `unique_message_client_id` constraint
  (§2) + an `IntegrityError` catch, not `get_or_create()` alone (not race-safe under
  genuinely concurrent duplicate submissions)
- `_deliver_queued_message(message)` — same 4 things `finalize_scheduled_message`
  (`scheduled_messages.py`) does for scheduled messages and the live REST/WS send paths
  do for a real-time send: denormalized conversation last-message fields, unread-count
  bump, `MessageStatus` rows, @mentions, `GroupMedia` gallery, WS broadcast
  (`chat_message` + `inbox_update`), and push (mute/mention/Focus-Mode-aware, same as
  every other send path) — tagged with `delivered_from_offline_queue: True` in the WS
  payload so the client can distinguish it from a live send if it wants to
- Kept as its own delivery function rather than sharing code with
  `scheduled_messages.finalize_scheduled_message` — deliberate, per this codebase's
  established one-function-per-entry-point convention (see the file's own docstring)

### `link_preview.py` *(NEW this session — see §7.5, File Map)*
- `extract_first_url(text) -> str | None` — first `http(s)://` URL found in message text
- `fetch_link_preview(url) -> dict | None` — returns `{"url", "title", "description",
  "image"}`, or `None` on any failure (unsafe URL, timeout, non-200, non-HTML, too many
  redirects) — every failure mode is a silent `None`, never an exception, since a preview
  is a nice-to-have that must never affect message delivery
- SSRF-safe by construction: only `http`/`https`; hostname resolved and every candidate
  IP checked against private/loopback/link-local/multicast/reserved/unspecified ranges
  (`_resolve_safe_ip`) — an attacker can't hide a malicious IP behind a multi-A-record
  hostname, since ANY private IP in the set fails the whole hostname; redirects are
  followed manually (not via `requests`' own `allow_redirects`) so each hop gets the same
  IP check, not just the original URL
- **DNS-rebinding (TOCTOU) fix**: `_pinned_dns()` is a scoped context manager that
  monkey-patches `socket.getaddrinfo` for the duration of one fetch, so the IP address
  `requests` actually connects to is guaranteed to be the exact same IP `_resolve_safe_ip`
  already validated — without this, `requests` would re-resolve the hostname itself at
  connect time, and an attacker controlling their own domain's DNS (TTL=0) could return a
  safe public IP for the check and a private/internal IP moments later for the real
  connection. Guarded by a module-level lock (`_dns_pin_lock`) so overlapping fetches in
  the same process can't clobber each other's patch — safe under Celery's default prefork
  worker pool (each worker is its own process); would need a per-thread/per-greenlet
  resolver override instead if ever switched to a threaded/gevent pool.
- OG-tag/title parsing uses `BeautifulSoup` (`html.parser` backend), not hand-rolled
  regexes — the previous regex approach silently produced "no preview" for attribute
  order variations, `name=` instead of `property=` on OG tags, quote style, etc., with no
  way to tell a real "no preview available" apart from "the parser just didn't recognize
  this valid HTML". Accepts both `property=` (spec) and `name=` (common non-standard
  variant) for OG tags.
- Bounded fetch: 4s timeout, 300KB / stops at `</head>` (whichever first) — only the
  `<head>` is ever needed, not the full page
- 7-day cache by URL, including negative results (`{}` for "no preview found") — a
  permanently-dead or non-HTML URL isn't refetched on every subsequent message

### `mentions.py` *(NEW)*
- `extract_mentioned_user_ids(text, conversation) -> list[int]`

### `translation_service.py` *(NEW this batch — Feature 9, see §7.21)*
- `translate_text(text, target_lang, source_lang=None) -> str` — default provider is
  Google Cloud Translate v2 REST (plain API key, `settings.GOOGLE_TRANSLATE_API_KEY`).
  Raises `TranslationServiceUnavailable` (not configured / provider unreachable, 8s
  timeout), `TranslationError` (provider reachable but rejected the request/returned
  something unparseable), or `UnsupportedLanguageError` *(NEW — TASK 29, a
  `TranslationError` subclass)* if `target_lang`/`source_lang` isn't in
  `SUPPORTED_LANGUAGES`, checked before the network call — designed so
  `MessageViewSet.translate` can map these to a clean 503 / 502 / 400 respectively
  instead of a raw 500. Kept as a `TranslationError` subclass so any existing
  `except TranslationError` still catches it unchanged.
- `SUPPORTED_LANGUAGES` — dict of the 10 ISO codes the Flutter language picker offers
  (en/hi/mr/ta/te/kn/bn/gu/pa/ur); documentation only, not a server-side allow-list.
- `GOOGLE_TRANSLATE_ENDPOINT` — the v2 REST URL, module-level constant.

### `push_utils.py` (Firebase Admin SDK, `FIREBASE_CREDENTIALS_PATH` **or**
`settings.FCM_SERVICE_ACCOUNT_JSON_PATH` — lazy init, see §9.0 items 2–3)
- `send_push_to_users(recipient_ids, title, body, data=None)` — generic, WITH visible
  notification (only used for non-chat pushes)
- `send_chat_message_push(recipient_ids, sender_name, message_text, message_type,
  conversation_id, message_id, is_announcement=False)` — sends **immediately** for every
  message (redesigned this batch — see §7.13). Filters recipients through Focus Mode
  first (`_filter_recipients_for_focus`, §7.18); if `CHAT_PUSH_DIGEST_ENABLED` is
  `False`, calls `_send_single_chat_push` directly for everyone; otherwise runs the
  per-recipient unread-streak counter (`cache.add`+`incr`+`touch`,
  `CHAT_PUSH_SESSION_SECONDS` TTL) and sends single (`count==1`) or digest (`count>1`)
  push per recipient, immediately either way
- `_filter_recipients_for_focus(recipient_ids, *, is_announcement)` *(NEW — Feature 12,
  §7.18, internal)* — single choke point all chat/mention pushes pass through; drops
  recipients with an active `FocusSession` unless the session's `exception_rule` allows
  this specific push through (`teachers_only` + `is_announcement=True`, or no active
  session at all)
- `_send_single_chat_push(recipient_ids, sender_name, body, conversation_id, message_id,
  is_announcement=False)` — the actual single-message FCM call, data-only;
  `is_announcement` changes both the payload `type` and the Android `channel_id` (§7.17)
- `send_chat_digest_push(recipient_id, conversation_id, sender_name, count,
  is_announcement=False)` — batched "X sent N messages" push, data-only, no `message_id`;
  same `is_announcement` handling as above
- `send_incoming_call_push(...)` — data-only, `type: incoming_call`
- `send_call_cancelled_push(...)` — data-only, `type: call_cancelled`
- `send_mention_push(recipient_ids, sender_name, message_text, conversation_id,
  message_id, is_announcement=False)` — data-only, `type: mention`, bypasses mute
  **and bypasses the digest/streak counter above** — always immediate, same as calls.
  Also filtered through Focus Mode (§7.18) — a teacher's mention gets through a
  `teachers_only` focus session, a student's doesn't
- All multicast sends clean up `DeviceToken`s that FCM reports as `UnregisteredError`
- **Removed this batch:** the old debounced-flush design (a cache window + one delayed
  Celery task per burst) — see §7.13's history note and §9.0 item 13. There is no
  `flush_chat_push_digest` in this file or in `tasks.py` anymore.
- **Bell-row notifications (task 44) — now confirmed, and they live HERE, not in
  `services.py`**: this module imports `core.services.create_notification` and
  `core.models.Notification` directly (module docstring: `create_notification()` never
  sends a push itself, only writes a DB row, so calling it from here carries no
  double-push risk). Three call-sites, each writing one `Notification` row **per
  recipient**:
  - `send_chat_message_push` — one `NotifType.CHAT_MESSAGE` row per recipient, for
    *every* incoming message, deliberately outside/independent of the digest-counting
    branch below it — the bell/notification-center list should show one entry per
    message ("5 unread" = 5 rows) even though the push tray itself collapses multiple
    messages into one digest push (WhatsApp-style). So a message that ends up as a
    digest push still gets its own bell row.
  - `send_incoming_call_push` — one `NotifType.INCOMING_CALL` row per recipient, so a
    missed/unanswered call still shows up later in the notification list, not just as a
    CallKit popup that vanished. Deliberately **not** filtered through Focus Mode (the
    push call above it isn't either) — bell rows go to the same recipients as the push.
  - `send_mention_push` — one `NotifType.MENTION` row per recipient, kept as its own
    `notif_type` distinct from `CHAT_MESSAGE` so a mention reads as "X mentioned you" in
    the notification list, not a generic "new message".
  - This **supersedes** `services.py`'s own `create_bell_rows_for_push` — that function
    has since been **removed from `services.py` entirely** as dead code once `push_utils.
    py`'s real content was available to check: it had zero callers anywhere, and this
    file was already doing the same job a different way (calling `core.services.
    create_notification()` inline, per event, rather than through a shared batch
    helper). Bell-row notifications are **not** an open gap — see §7.22/§9.4 items
    19/22 (resolved).

### `livekit_utils.py` (env: `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, plus egress env
vars below — TASK 21)
- `generate_livekit_token(room_name, user_id, user_name, ttl=timedelta(hours=2)) -> str`
  — calls use the 2h default; `StudyRoomJoinView` overrides to 8h. Local JWT signing only
  — never talks to the LiveKit server, needs no event loop.
- `_get_livekit_credentials()` *(fix — see §9.0)* — lazily validates the two env vars are
  set, called only from inside `generate_livekit_token()` (i.e. only when a token is
  actually about to be generated). Previously this check ran at **module import time**,
  meaning a missing LiveKit config crashed the whole Django process at boot (`views.py`
  imports this module), not just the call/study-room features that actually need it.
  **Now also the credential check for the egress functions below** — same lazy pattern,
  same `RuntimeError` if unset.
- **Egress (call recording) — `start_room_recording`/`stop_room_recording`** *(NEW —
  TASK 21, see §2/§6/§7.24/§9.0 item 21)*:
  - `start_room_recording(room_name) -> (egress_id, output_filepath)` — starts a LiveKit
    `RoomCompositeEgressRequest` (all participants mixed into one MP4, `layout="speaker"`),
    uploading to S3 if `LIVEKIT_EGRESS_S3_BUCKET` is set. `output_filepath` is
    `recordings/{room_name}-{uuid4().hex}.mp4`, generated here (not by LiveKit) so it's
    known immediately, before the egress job finishes.
  - `stop_room_recording(egress_id, output_filepath=None) -> recording_url | None` — stops
    the egress job; builds a best-effort public URL from
    `LIVEKIT_EGRESS_PUBLIC_BASE_URL` + `output_filepath` if both are available, else
    returns `None`. **Not authoritative** — a successful stop means LiveKit *accepted*
    the request, not that the file finished uploading; the correct long-term source of
    truth (LiveKit's `egress_ended` webhook) isn't wired into this app anywhere.
  - `EgressError(RuntimeError)` — raised when the Egress start/stop HTTP call itself
    fails (bad response, network error, room not found), kept deliberately distinct from
    the plain `RuntimeError` `_get_livekit_credentials()` raises for "not configured at
    all" — callers can tell "not configured" (503) apart from "LiveKit rejected/failed
    the request" (502) without string-matching.
  - `_run_async(coro)` — LiveKit's Egress/Room service client is async-only
    (`httpx.AsyncClient`); every caller here is a synchronous DRF view, so this bridges
    with a plain `asyncio.run()` per call rather than pushing async/await onto the view
    layer for what's otherwise one blocking HTTP round-trip.
  - `LIVEKIT_HTTP_URL` — the Egress API's base URL. Defaults to deriving itself from
    `LIVEKIT_WS_URL` (`ws://`→`http://`, `wss://`→`https://`, both stripped/replaced) so
    most deployments (egress + media server behind the same host) need no extra env var;
    `LIVEKIT_URL` overrides this only if egress genuinely sits behind different ingress.
  - `LIVEKIT_EGRESS_S3_BUCKET` / `LIVEKIT_EGRESS_PUBLIC_BASE_URL` — see §13. Neither is
    required for recording to *work* (egress falls back to local disk / `recording_url`
    just comes back `None`), only for it to produce a usable public URL in production.

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
- *(fix — see §9.0)* The actual implementation now lives in project-level
  `LearnScroll/ws_auth.py`; this file just re-exports `JWTAuthMiddleware` and
  `get_user_from_token` so existing imports (`from .Middleware import ...`) keep working.
  `liveclass` (which used to carry a near-identical independent copy) imports from the
  same shared location now — one implementation instead of two that could drift apart.
- **`LearnScroll/ws_auth.py`'s own docstring confirms the exact `asgi.py` wiring**
  (reviewed directly this batch, not inferred): both apps' `websocket_urlpatterns` are
  combined into one `URLRouter`, wrapped in `JWTAuthMiddleware`, wrapped in
  `AllowedHostsOriginValidator` —
  ```python
  from liveclass.routing import websocket_urlpatterns as liveclass_ws
  from message.routing import websocket_urlpatterns as message_ws
  from LearnScroll.ws_auth import JWTAuthMiddleware

  application = ProtocolTypeRouter({
      "http": django_asgi_app,
      "websocket": AllowedHostsOriginValidator(
          JWTAuthMiddleware(URLRouter(liveclass_ws + message_ws))
      ),
  })
  ```
  Client connects as `wss://yourdomain.com/ws/<path>/?token=<JWT_ACCESS_TOKEN>`.
  `get_user_from_token()` catches every failure mode (bad signature, expired, malformed,
  deleted/deactivated user) and degrades to `AnonymousUser` rather than raising — a bad
  token can never crash the WS handshake, it just connects unauthenticated, same as no
  token at all.

### `ai_service.py`
- Gemini client (`google-genai`), model configurable via `GEMINI_MODEL` env
  (default `gemini-2.5-flash`; note `gemini-2.0-flash` was retired June 1 2026 — this is
  why the env var was made configurable instead of hardcoded. `gemini-2.5-flash` itself
  is scheduled to retire Oct 16 2026 — when that happens, only `GEMINI_MODEL` needs to
  change, not code)
- Cache key format: `study_ai:{mode}:{model}:{content_hash}` — the model name is folded
  into every key so switching `GEMINI_MODEL` (e.g. on the next retirement) can never mix
  a stale cached output from the old model with the new one; a model switch naturally
  produces fresh cache keys with no manual flush needed.
- Cache-hit check uses a `_CACHE_MISS` sentinel object, not a truthy check *(fix — see
  §9.0)* — distinguishes "never cached" from "cached, but the value itself happens to be
  falsy" (empty string/list), which a plain `if cache.get(key):` cannot.
- `generate_summary(content) -> str` — cached 24h by `sha256(content)`; raises if Gemini
  returns an empty/whitespace-only result instead of caching it *(empty-result guard
  added this batch — was previously missing here while present elsewhere, see §9.0)*
- `generate_quiz(content) -> list[dict]` — 5 MCQs, cached 24h by content hash
- `transcribe_audio(file_url, mime_type="audio/ogg") -> str` *(NEW)* — downloads the
  audio bytes (Gemini needs bytes, not a URL) and transcribes them; used by both
  `VoiceTranscribeView` (§6/§7.6) and the auto-transcription Celery task. Cached 24h,
  keyed by a hash of `file_url` — **not** the audio content itself, an intentional
  trade-off (see §9.4 item 13): a content-based key would require downloading first,
  which defeats the point of checking the cache before downloading.
- `generate_reply_suggestions(context_text, conversation_id) -> list[str]` *(NEW)* — 3
  short tap-to-send quick-reply suggestions (§7.12/§6). Cache key includes
  `conversation_id` *(fix — see §9.0)* to prevent a cross-conversation cache leak when two
  different conversations produce byte-identical short context text.
- `generate_classroom_answer(question, context_text, conversation_id) -> str` *(NEW —
  see §7.14)* — was imported by `views_ai.py` before it existed here, breaking every view
  in that file via `ImportError` until added *(fix — see §9.0)*. Cache key also includes
  `conversation_id`, same reasoning as `generate_reply_suggestions`.
- `generate_revision_deck(content) -> dict` *(NEW — see §7.15)* — returns
  `{"flashcards": [...], "quiz": [...]}` (8–12 flashcards + 5 MCQs); cached 24h by
  content hash. The caller (`views_ai.py`) persists the result as a `RevisionDeck` row
  (§2) rather than treating it as throwaway like the plain summary/quiz.
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

### `attendance_utils.py` *(NEW — see §2/§7.16)*
- `compute_attendance_stats(conversation, user) -> dict` — the single source of truth for
  attendance-streak math. Used by `StudyRoomStreakView` (single classroom, student's own
  view — see §7.16 for why a shared helper mattered here specifically, given this
  codebase's history of the same rule drifting across copies).
- Returns `{"current_streak", "longest_streak", "total_classes_attended",
  "last_attended"}` (the last as an ISO date string, or `None` if the user has never
  attended). Reads distinct `attended_date`s from `StudyRoomAttendance`, descending.
- Current streak: walks backward day-by-day from today (falling back to yesterday if
  today's date isn't in the set yet — i.e. today's class hasn't happened/been joined).
  Longest streak: single pass over the full sorted date list, tracking the longest run
  of consecutive days. Both the single and bulk variant below funnel through one shared
  private helper (`_stats_from_sorted_dates`) so this math can never drift between the
  two entry points.
- `compute_attendance_stats_bulk(conversation_ids, user) -> {conversation_id: dict}`
  *(NEW — N+1 fix)* — `ParentDashboardView` previously called `compute_attendance_stats`
  once **per classroom** the student is in (1 query per classroom). This variant fetches
  every `StudyRoomAttendance` row for all the given `conversation_ids` in a single query,
  groups the dates by conversation in Python, then runs each group through the same
  `_stats_from_sorted_dates` helper the single-conversation function uses — so the result
  is guaranteed identical to calling `compute_attendance_stats` per classroom, just at a
  fixed ~1 query total regardless of how many classrooms the student is in. Every
  requested `conversation_id` is guaranteed a key in the result (an all-zero dict if the
  student never attended that classroom), so `ParentDashboardView` doesn't need any
  missing-key handling. See §7.16/§6 (Parent Dashboard) for where this is now used.

### `admin.py` *(fix — see §9.0)*
- Registers every model in `models.py`, including `GroupJoinRequest`, `DeviceToken`, and
  `StudyRoomState` — previously unregistered despite being in active use, leaving
  ops/support without an admin UI for pending join requests, push tokens, or saved
  whiteboard state.
- `SoftDeleteAdmin(admin.ModelAdmin)` *(NEW — used for `Conversation` and `Group`)* —
  plain `admin.site.register()` uses a model's default manager (`.objects`) for its
  changelist queryset, and `Conversation`/`Group` now default to a `SoftDeleteManager`
  (§2/§5) that filters out `is_deleted=True` rows — so without this, a soft-deleted group
  (`GroupViewSet.destroy()`, §5) would simply vanish from the admin too, with no way for
  ops/support to find it, inspect it, or undo the delete during the
  `GROUP_SOFT_DELETE_GRACE_DAYS` window before the hard-delete purge task runs. Instead,
  `SoftDeleteAdmin.get_queryset()` points the changelist at `self.model.all_objects.all()`
  so soft-deleted rows stay visible, adds an `is_deleted` column/filter to the changelist,
  and adds a bulk **"Restore selected (undo soft-delete)"** admin action
  (`queryset.update(is_deleted=False)`) so a group deleted by accident can be recovered
  without a raw DB query.

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
  every connect/disconnect. `invalidate_presence_cache(user_id)` also exists (same
  `cache.delete()` pattern as `invalidate_group_role_cache`) but **isn't imported or
  called anywhere in `consumers.py`/`views.py` this batch** — presence currently relies
  entirely on the 15s TTL expiring rather than an explicit invalidate-on-write, so this
  helper is available but effectively unused right now.

### `throttles.py`
- REST (DRF `UserRateThrottle` subclasses, per-authenticated-user, cache-backed):
  `MessageSendThrottle` (60/min, `ConversationViewSet.messages` POST),
  `CallInitiateThrottle` (10/min — calls are costlier: an FCM push + a LiveKit room
  each), `GroupCreateThrottle` (5/min), `ReactionThrottle` (120/min,
  `MessageViewSet.react`), `FocusSessionThrottle` (20/min, scope `focus_session`,
  wired onto `FocusSessionView` this batch — see §7.18 Gap Fix #2),
  `ParentCodeRevealThrottle` (10/hour suggested, scope `parent_code_reveal`, *NEW this
  batch* — guards `ParentAccessCodeRevealView`, see §9.0 item 20).
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
  messages past `expires_at` (bounded 500/batch loop). Uses `Message.all_objects` (not
  `.objects`) — since `BaseModel`'s default `objects = SoftDeleteManager()` filters
  `is_deleted=True` out of every model including `Message`, this sweep deliberately opts
  into the unfiltered manager so it can still hard-delete an already-soft-deleted-and-
  expired message.
- `purge_soft_deleted_conversations` — **now directly confirmed in `tasks.py` +
  `settings.py`**: beat-scheduled daily at 03:30 (`"message-purge-soft-deleted-
  conversations"` in `CELERY_BEAT_SCHEDULE`). Queries `Conversation.all_objects.filter(
  is_deleted=True, updated_at__lte=cutoff)` (bounded 200/run batch), where `cutoff = now -
  GROUP_SOFT_DELETE_GRACE_DAYS` (`settings.py`, default `7`, env-overridable). Re-filters
  on the actual delete (`is_deleted=True, updated_at__lte=cutoff` again) so a `.restore()`
  that lands in the gap between the initial fetch and the delete isn't clobbered by a
  stale read. This is the other half of `GroupViewSet.destroy()`'s soft-delete (§5): the
  soft-delete makes a group-delete recoverable for the grace window, this sweep actually
  reclaims storage (CASCADE hard-delete of the `Conversation`) once that window passes.
- `generate_link_preview_task` / `transcribe_voice_message_task` — one-shot,
  `.delay(message_id)`-triggered right after a message is created (REST/WS/scheduled),
  not beat-scheduled. See §7.5/§7.6. Share `_broadcast_meta_update()` to push their
  result live over WS once done.
- `transcribe_class_chunk_task(segment_id)` *(NEW — see §7.19)* — one-shot,
  `.delay(segment_id)`-triggered per uploaded classroom-audio chunk (caller not
  confirmed this batch). Reuses `ai_service.transcribe_audio()` (mime type
  `audio/mp4`), writes to `ClassTranscriptSegment.text`/`status`, and broadcasts
  `transcript_segment_ready` (§8) — a different WS event from `_broadcast_meta_update`'s
  `meta_update`, since this isn't updating a `Message.meta` at all.
- **Removed this batch:** `flush_chat_push_digest` — the debounced chat-push design it
  supported has been replaced with immediate-send (§7.13/§9.0 item 13). Nothing in
  `tasks.py` schedules a delayed push flush anymore.

### `LearnScroll/celery.py` *(project-level, NEW this batch — confirmed via direct review)*
- Not a `message`-app file, but `message/tasks.py`'s two beat-scheduled tasks
  (`send_scheduled_messages`, `cleanup_expired_messages`, §10 `tasks.py` entry) and every
  `.delay()` call in this app (`generate_link_preview_task`,
  `transcribe_voice_message_task`) run through the Celery app this file defines, so it's
  documented here for completeness.
- `app = Celery("LearnScroll")`, `app.config_from_object("django.conf:settings",
  namespace="CELERY")` (reads every `CELERY_*` setting from `settings.py`),
  `app.autodiscover_tasks()` (auto-discovers a `tasks.py` in every `INSTALLED_APPS` app —
  `message/tasks.py` and `liveclass/tasks.py` both included, no manual registration
  needed).
- **One-time wiring, confirmed required and not itself part of this file:**
  1. `LearnScroll/__init__.py` needs `from .celery import app as celery_app` +
     `__all__ = ("celery_app",)` — this is what makes `@shared_task` in `message/tasks.py`
     (and every other app) pick up this Celery app automatically.
  2. `pip install celery[redis] redis`.
  3. A running Redis instance (broker + result backend) — same `REDIS_URL`/
     `CELERY_BROKER_URL` already required for `CHANNEL_LAYERS`/`CACHES` (§14), so this is
     one Redis to run, not a separate one.
  4. **Both** of the following must run as separate long-lived processes in production —
     neither is optional, and the file's own comment is explicit that this is a common
     footgun:
     - `celery -A LearnScroll worker -l info` (executes tasks)
     - `celery -A LearnScroll beat -l info` (fires periodic tasks — **without this
       process running, `message-send-scheduled-messages` and
       `message-cleanup-expired-messages` never fire on their own**, even though both are
       correctly registered in `CELERY_BEAT_SCHEDULE`, settings.py). A worker with no
       beat means nothing self-triggers; beat with no worker means tasks queue up in
       Redis but never execute.
- Confirms (from the file's own docstring) that the same worker+beat pair also drives
  four `liveclass`-app periodic tasks that were previously inert DB rows with no runner
  (session generation from `ClassSchedule`, `ClassReminder` sending, waitlist-promotion
  notification, and auto-ending sessions a teacher forgot to `/end/`) — out of scope for
  this doc's detail, but relevant context: this app's own two Celery entries share
  infrastructure with, and depend on the same worker/beat pair as, a much larger set of
  `liveclass` periodic jobs. If beat/worker are ever debugged or restarted for a
  `liveclass` issue, `message`'s scheduled-messages/disappearing-messages sweeps are
  affected too, and vice versa.

### `scheduled_messages.py`
- `finalize_scheduled_message(message)` — the delivery half of "Send Later". A scheduled
  `Message` row (created by `ConversationViewSet.schedule_message`) sits invisible to
  everyone but the sender until this runs: flips `is_scheduled=False`, recomputes
  disappearing-message `expires_at` and `created_at` off the *current* moment (not the
  original schedule time, so it lands in the right spot in the chat-list order),
  updates conversation denorm fields/unread counts/`MessageStatus` rows/@mentions/
  `GroupMedia`, then broadcasts `chat_message` + `inbox_update` and sends the same
  mute/mention-aware pushes a normal send does.
- Called by the `message.send_scheduled_messages` **Celery task** (`tasks.py` — the
  canonical, beat-scheduled production path), registered in `CELERY_BEAT_SCHEDULE`
  (`settings.py`) to run every minute. **Confirmed present and scheduled — see §9.1
  item 3** (this was flagged as unconfirmed in an earlier review, before `settings.py`
  had been reviewed). **A `management/commands/send_scheduled_messages.py` also now
  exists as a manual/backup trigger for this same function — see the new "Management
  Commands" subsection right below, and §9.4 item 4.**
- Now also enqueues `generate_link_preview_task`/`transcribe_voice_message_task` after
  finalizing a scheduled message, same as the two live-send paths *(NEW this session,
  see §7.5/§7.6)*.

### Management Commands (`management/commands/`) *(NEW subsection this batch — four
files reviewed for the first time, none previously part of any file batch)*

- **`send_scheduled_messages.py`** — manual/backup CLI trigger:
  `python manage.py send_scheduled_messages [--batch-size N]` (default 200, same bound
  as the Celery task). Calls the exact same `scheduled_messages.finalize_scheduled_message`
  as the Celery task above, one message at a time, logging + skipping (not aborting) on a
  per-message exception. Its own docstring is explicit that `tasks.send_scheduled_messages`
  (Celery beat, every minute) is the **confirmed production path** and this command is
  **not meant to also be cron-scheduled alongside it** in normal operation — it exists for
  a one-off "flush the queue right now" ops need, or as a fallback if Celery/Beat is ever
  down. **Fix baked into this version**: uses the identical `select_for_update
  (skip_locked=True)` row-locking as the Celery task (previously — per this file's own
  changelog comment — it had none), so if it *were* ever run concurrently with the Celery
  beat tick, the two can no longer pick up and double-process the same due message
  (double-send, double unread-count increment, double push). Whichever process gets the
  row lock first wins; the other skips it via `skip_locked=True`. See §9.4 item 4.
- **`cleanup_expired_messages.py`** — manual/backup CLI trigger:
  `python manage.py cleanup_expired_messages [--batch-size N] [--dry-run]` (default
  batch size 1000). Deletes `Message` rows where `expires_at` is set and in the past, in
  bounded batches (fetch a page of ids, delete that page, repeat) to avoid holding a
  long-running lock on a potentially huge table in one shot. `--dry-run` reports the
  count without deleting anything. **Duplicates, does not introduce**, the hard-delete
  behavior `tasks.cleanup_expired_messages` (Celery beat, every 15 min, batch size 500)
  already provides in production — the command's own docstring assumed this sweep had
  never been implemented, which §9.1 item 3 / this file's own `tasks.py` entry above
  shows is incorrect. Safe to have as an extra manual/dry-run-capable ops tool; not safe
  to assume it's the *only* thing doing this cleanup. See §9.4 item 24.
- **`expire_stale_parent_access.py`** — periodic DB-hygiene command (intended to be run
  via cron/celery-beat, e.g. daily — `python manage.py expire_stale_parent_access`, no
  arguments). Explicitly **not load-bearing for security**: `HasValidParentToken`
  already rejects expired parent codes/tokens live, on every request, independent of
  whether this command has ever run. Two things it does: (1) hard-deletes `ParentToken`
  rows inactive past `ParentToken.INACTIVITY_TTL_DAYS` (30) **plus** a further 14-day
  grace period (`TOKEN_DELETE_GRACE_DAYS`) — the grace window means a device that just
  crossed its TTL doesn't instantly vanish from a "devices" list; the student can still
  see it existed for a couple more weeks. Falls back to `created_at` when `last_seen_at`
  is null (never used after verify), matching `ParentToken.is_expired`'s own fallback
  logic. (2) deactivates (`is_active=False`, not deleted) `ParentAccessCode`s that
  expired more than 30 days ago (`CODE_DEACTIVATE_GRACE_DAYS`) and were never renewed —
  keeps the student's "Manage parent access" list from accumulating ancient dead codes
  forever. **Not yet registered in `CELERY_BEAT_SCHEDULE` or confirmed via external
  cron** — see §9.4 item 25, §2 `ParentAccessCode`/`ParentToken`.
- **`apply_doubtquestion_context_fields.py`** *(Task 16)* — one-off, idempotent,
  Postgres-only raw-SQL schema command: `python manage.py
  apply_doubtquestion_context_fields`, no arguments. Makes `DoubtQuestion.group_id`/
  `.conversation_id` nullable, adds `context_type` (`varchar(30)`, nullable) and
  `context_id` (`uuid`, nullable) columns, a lookup index on
  `(context_type, context_id)`, and a `doubtquestion_has_group_or_context`
  `CheckConstraint` requiring every row to have `group_id` **or** a full
  `(context_type, context_id)` pair. Every step uses `IF NOT EXISTS`/a guarded
  `DO $$ ... EXCEPTION ... END $$` block, so re-running the command is a no-op past the
  first run. Deliberately applied outside Django's own migration history (no
  `django_migrations` row) — the command's own docstring flags that `makemigrations`
  will likely still want to generate a matching migration afterward, and that this is
  expected: it'll be a no-op migration (columns already exist) that just brings
  migration *state* in sync with what the DB already has, not something that tries to
  re-create anything. Assumes Postgres (this project already uses
  `django.contrib.postgres` elsewhere, e.g. `GinIndex`/`SearchVectorField` on `Message`)
  and the default Django table/column naming for `app_label='message'`,
  `model='DoubtQuestion'` — will not work as-is on MySQL/SQLite, or if `Meta.db_table`
  has been overridden. See §2 `DoubtQuestion`, §9.4 item 23 for the open question of
  whether any view code actually uses the new columns yet.

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
- `HasValidParentToken` *(Feature 8 — Parent Mode)* — reads `X-Parent-Token` header,
  requires a matching `ParentToken` where the parent `ParentAccessCode.is_active=True`,
  **and now (G-6, mutual consent — NEW this session) `status=ParentToken.Status.APPROVED`**
  in addition to the pre-existing `ParentAccessCode.is_expired`/`ParentToken.is_expired`
  (30-day rolling inactivity) checks — all four must pass, any failure returns the same
  generic denial (`request.user` untouched throughout; see §2/§9.4 for the confirmed
  model-field gap this introduces).

---

## 13. Environment Variables Required

| Var | Used by | Notes |
|---|---|---|
| `GEMINI_API_KEY` | `ai_service.py` | Required for AI features to init |
| `GEMINI_MODEL` | `ai_service.py` | Default `gemini-2.5-flash`. Scheduled to retire Oct 16 2026 — change this env var when that happens, no code/deploy needed (see §10) |
| `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | `livekit_utils.py` | **No longer raises at import time** *(fix — see §9.0)* — the check is now lazy, firing only when `generate_livekit_token()` is actually called (call initiate / study-room join), so a missing config no longer crashes the whole process at boot |
| `LIVEKIT_WS_URL` | `views.py`, `livekit_utils.py` | Default `ws://10.93.221.189:7880` — looks like a dev/internal IP, confirm for prod. Also the fallback source `livekit_utils.py` derives `LIVEKIT_HTTP_URL` from (`ws://`→`http://`, `wss://`→`https://`) for Egress/recording calls *(NEW — TASK 21, see §7.24/§10)* |
| `LIVEKIT_URL` *(NEW — TASK 21)* | `livekit_utils.py` | Only needed if the Egress/recording API sits behind different ingress than the media server clients join via `LIVEKIT_WS_URL` — otherwise `LIVEKIT_HTTP_URL` is derived automatically, no extra var required |
| `LIVEKIT_EGRESS_S3_BUCKET` *(NEW — TASK 21)* | `livekit_utils.py` | Where a finished call recording gets uploaded. If unset, egress falls back to writing to local disk on the egress worker — fine for dev, not for prod |
| `LIVEKIT_EGRESS_PUBLIC_BASE_URL` *(NEW — TASK 21)* | `livekit_utils.py` | Public base URL (e.g. a CloudFront domain in front of the S3 bucket above) used to build `CallSession.recording_url` after a recording stops. If unset, `stop_room_recording()` returns `None` instead of guessing a URL that would 404 |
| `FIREBASE_CREDENTIALS_PATH` | `push_utils.py` | Path to Firebase service-account JSON. **Lazy init as of this session (see §9.0)** — no longer raises at import/process-startup time; only raised (and logged, not crashed) the first time a push is actually sent with no path configured. Falls back to `settings.FCM_SERVICE_ACCOUNT_JSON_PATH` if unset |
| `CHAT_PUSH_SESSION_SECONDS` *(NEW this batch)* | `push_utils.py` | Default `300`. TTL of the per-`(user,conversation)` "unread streak" counter that decides single-push vs. digest-push — **not a wait/delay**, every push still sends immediately (see §7.13). Falls back to `CHAT_PUSH_DEBOUNCE_SECONDS` if this newer var isn't set |
| `CHAT_PUSH_DEBOUNCE_SECONDS` | `push_utils.py` | Back-compat alias, read only if `CHAT_PUSH_SESSION_SECONDS` is unset. **Meaning changed this batch** — used to be a genuine push-delay wait time (old debounced-flush design); now, if read at all, it's used as the streak TTL like `CHAT_PUSH_SESSION_SECONDS` above (see §7.13's "history" note) |
| `CHAT_PUSH_DIGEST_ENABLED` *(NEW this batch)* | `push_utils.py` | Default `True`. Set `False` to disable all push grouping — every message gets its own plain push via `_send_single_chat_push`, no streak counter involved at all |
| `MEDIA_ABSOLUTE_BASE_URL` (Django setting, not env strictly) | `user_display.py` | Used only when no `request` context is available (WS payloads) |
| `REDIS_URL` (or `CELERY_BROKER_URL` as fallback) | `settings.py` → `CHANNEL_LAYERS`, `CACHES`, Celery | **Not `message`-specific**, but this app's realtime broadcast, all its caches, and its Celery tasks all depend on it being set in production — see §14 |
| `GOOGLE_TRANSLATE_API_KEY` | `translation_service.py` (Feature 9) | **Now wired in `settings.py`** *(fix — see §9.4 item 16)* — `GOOGLE_TRANSLATE_API_KEY = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "")`. Code-side gap is closed; the actual key still needs to be set in the deploy environment, or `translate_text()` will 503 (`TranslationServiceUnavailable`) on an empty string same as before |

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
- The Celery **app itself** (`LearnScroll/celery.py`, project-level, confirmed reviewed
  this batch) is what actually turns these settings into running processes — see the new
  `LearnScroll/celery.py` entry in §10 for its one-time wiring requirements
  (`LearnScroll/__init__.py` import, `pip install celery[redis] redis`, and **running
  both** a `celery worker` **and** a `celery beat` process). Without the `beat` process
  specifically, nothing in `CELERY_BEAT_SCHEDULE` below ever fires on its own, no matter
  how correctly it's configured here.
- `CELERY_BEAT_SCHEDULE` — full schedule reviewed this batch (project-wide, shared with
  `liveclass`). The `message` app's 2 periodic entries, confirmed present:
  - `message-send-scheduled-messages` → `message.send_scheduled_messages`, every minute
    (matches `remind_at`/`scheduled_for` minute-precision — cheap indexed query, bounded
    200/run batch)
  - `message-cleanup-expired-messages` → `message.cleanup_expired_messages`, every 15 min
    (shortest disappearing-message duration is 1 month, so a 15-min sweep lag is
    invisible to users)
  - `generate_link_preview_task`/`transcribe_voice_message_task`/
    `flush_chat_push_digest` *(latter NEW this session)* are **not** in this schedule and
    don't need to be — they're one-shot, triggered directly via `.delay()`/
    `.apply_async(countdown=...)` right after a message is created or a push window
    opens (§7.5/§7.6/§7.13), not a periodic sweep.
  - **Not `message`-specific, but confirmed present in the same schedule dict**: 10
    `liveclass`-app periodic entries (session generation from recurring `ClassSchedule`
    rows, auto-completing overdue sessions, sending due `ClassReminder`s, refreshing
    stale enrolled-counts, expiring/refunding lapsed passes, cleaning up abandoned
    chunked uploads, reconciling stuck coin purchases, running pass auto-renewals,
    expiring unclaimed pass gifts, and sending notification digests) — out of this doc's
    scope in detail, but relevant because they share the exact same worker/beat
    processes `message`'s 2 entries depend on. Every lookback-window-based entry in the
    full schedule (both apps) runs **more frequently than its own lookback/timeout
    window**, specifically so a slow/delayed beat tick can never let a batch of expiries
    fall in the gap between two runs — the same pattern `message`'s 15-min
    disappearing-message sweep follows.

### REST throttle rates (`REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`)
`settings.py` re-reviewed in full this session. 15 of the 16 `message`-relevant custom
throttle scopes are **confirmed present**, including all 6 that were confirmed missing in
the previous review (`translate`, `parent_code_verify_ip`, `ai_class_transcript_chunk`,
`ai_class_transcript_search`, `ai_classroom_copilot`, `ai_revision_deck`) plus
`focus_session` — see §9.4 items 15/16 (resolved). **Exactly 1 scope wired to a real view
is still confirmed MISSING**: `parent_code_reveal` — see §9.0 item 20.

| Scope | Rate | Throttle class | Guards | Status |
|---|---|---|---|---|
| `message_send` | 60/min | `MessageSendThrottle` | `ConversationViewSet.messages` POST | ✅ present |
| `message_send_ip` | 120/min | `MessageSendIPThrottle` | same endpoint, per-IP | ✅ present |
| `call_initiate` | 10/min | `CallInitiateThrottle` | `CallInitiateView` | ✅ present |
| `call_initiate_ip` | 20/min | `CallInitiateIPThrottle` | same endpoint, per-IP | ✅ present |
| `group_create` | 5/min | `GroupCreateThrottle` | `GroupViewSet.create` | ✅ present |
| `reaction` | 120/min | `ReactionThrottle` | `MessageViewSet.react` | ✅ present |
| `ai_study` | 20/min | `AiStudyThrottle` | `AiStudyRoomView` | ✅ present |
| `ai_transcribe` | 15/min | `AiTranscribeThrottle` | `VoiceTranscribeView` | ✅ present |
| `ai_smart_reply` | 30/min | `SmartReplyThrottle` | `SmartReplySuggestionsView` | ✅ present |
| `translate` | 30/min | `TranslateThrottle` | `MessageViewSet.translate` | ✅ present *(fixed this session — was missing, §9.4 item 15)* |
| `parent_code_verify_ip` | 10/min | `ParentCodeVerifyThrottle` | `ParentVerifyCodeView`, per-IP | ✅ present *(fixed this session)* |
| `ai_class_transcript_chunk` | 30/min | `ClassTranscriptChunkThrottle` | `ClassTranscriptChunkUploadView` | ✅ present *(fixed this session)* |
| `ai_class_transcript_search` | 60/min | `ClassTranscriptSearchThrottle` | `ClassTranscriptSearchView` | ✅ present *(fixed this session)* |
| `ai_classroom_copilot` | 15/min | `ClassroomCopilotThrottle` | `ClassroomCopilotView` | ✅ present *(fixed this session)* |
| `ai_revision_deck` | 10/min | `RevisionDeckThrottle` | `RevisionDeckView` | ✅ present *(fixed this session)* |
| `focus_session` | 20/min | `FocusSessionThrottle` | `FocusSessionView` | ✅ present |
| `parent_code_reveal` | 10/hour (intended) | `ParentCodeRevealThrottle` | `ParentAccessCodeRevealView` | 🔴 **MISSING** — confirmed this session; see §9.0 item 20 |

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

*(TASK 29, this batch, implemented the three items previously listed here — chat/media
export, poll "clear my vote entirely", and poll forwarding — see §7.8/§7.9/§7.25. None
currently pending.)*

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

*(Classroom Copilot, Revision Deck, and Study Room Attendance/Streak + Parent Mode were
not previously tracked in this doc at all — added as §7.14–§7.16/§2/§10, from
`ai_service.py`, `models.py`, and `attendance_utils.py`. Their REST endpoints
(`views.py`/`views_ai.py`/`views_parent.py`/`urls.py`) are now confirmed — see §6 and
§9.4 items 9–12 (all resolved).)*

*(Announcements, Focus Mode/Smart DND, Class Transcript, Doubt Queue, and Message
Translation were also not previously tracked in this doc — added as §7.17–§7.21/§2/§6/§10,
from `models_focus.py`, `permissions.py`, `push_utils.py`, `tasks.py`, `serializers.py`,
`throttles.py`, `translation_service.py`, and now `models.py`/`settings.py`. All five are
confirmed end-to-end: model, service/push layer, and route (`POST
/message/messages/<id>/translate/` for Translation — see §6/§9.1 item — plus the matching
`DEFAULT_THROTTLE_RATES` entries, §9.4 items 15/16, and `GOOGLE_TRANSLATE_API_KEY`, §13).
The one remaining gap in this group is unrelated to routing: `parent_code_reveal`'s
throttle rate is still missing from `settings.py` — see §9.0 item 20.)*

*(The Group Management Service Layer, Bell-Row Notifications, and Offline Message Queue
were also not previously tracked in this doc — added as §5/§7.22–§7.23/§10, from
`services.py` (task 27) and `offline_queue.py` (task 49). With `views.py`/`urls.py`
now reviewed, all three are **confirmed resolved**, though not all in the way originally
expected: `GroupViewSet.create`/`add_members`/`update_member` now actually call
`services.py`'s functions (§5/§9.4 item 21), `offline_queue.flush_offline_queue` now has
a real REST endpoint (§7.23/§9.4 item 20), and bell-row notifications turned out to
already be fully working all along — just implemented directly in `push_utils.py`
(`core.services.create_notification()` calls inline at each push call-site) rather than
through the `services.create_bell_rows_for_push` helper this doc used to describe, which
has since been removed from `services.py` as dead code (§9.4 item 19, §10 `push_utils.
py`).)*