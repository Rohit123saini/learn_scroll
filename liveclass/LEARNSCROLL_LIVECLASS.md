# LearnScroll — `liveclass` App: Full Architecture Reference

> **Purpose of this file:** single source of truth for the entire `liveclass`
> Django app (an online live-class marketplace bolted onto an existing
> `login`/`message` Django project called **LearnScroll**). Share only this
> file in future chats — it tells you what every file does, what every
> model/endpoint/task/socket event is, how money/coins flow, exact URL
> paths + HTTP verbs, and where the known gotchas are, without needing the
> raw source again. Written so that new features/bugfixes can be reasoned
> about and coded purely from this file.

---

## 0. One-paragraph summary

Any authenticated user can create a **Classroom** (becomes its teacher),
define a recurring **ClassSchedule**, and sell access via **ClassPass**es
priced in **coins** (in-app currency, not direct money — coins themselves
are top-upped via Razorpay). Other users don't buy directly — they raise a
**ClassJoinRequest**, and only when the teacher/staff **accepts** it is a
**PassPurchase** created and coins debited. A valid purchase gates access to
live **ClassSession**s (video via **LiveKit**), chat, materials, polls,
assignments, breakout rooms, etc. Coins are released to the teacher **per
completed day taught** (escrow model), not all at once, with an optional
ongoing referral commission carved out of the teacher's own cut. Realtime
(chat, hand-raise, presence, notifications) runs over **Django Channels**
WebSockets backed by **Redis**. Scheduled jobs (session generation,
reminders, escrow charge catch-up, refunds, digestion, transcription) run
via **Celery beat + worker**.

> **Two cross-app migrations to know about before touching money or assignments:**
> coin top-up/withdrawal **request workflows** now live in the `user_profile` app
> (`liveclass.CoinPurchase`/`CoinWithdrawal` are read-only history — §6e), and new
> classroom **assignments** now route through the unified, project-wide `assignment`
> app via `liveclass/bridge.py` (`liveclass.Assignment`/`AssignmentSubmission` are
> read-only history — §6d). `User.coin` and `CoinTransaction` (the wallet + its
> ledger) are untouched by the coin migration — only the request/approval flow moved.

---

## 1. File map (what lives where)

| File | Lines | Role |
|---|---|---|
| `models.py` | ~3460 | All DB models (~45 models) + model-level business logic + cache-version signals |
| `serializers.py` | ~1640 | DRF serializers — 55 classes, one (or a few) per model/action |
| `views.py` | ~6510 | DRF ViewSets/APIViews — all HTTP endpoints, permission logic, orchestration. **+TASK 4** (new): `ClassroomViewSet.perform_create` now enqueues `notify_followers_new_classroom` via `_safe_delay` — see §4/§9. |
| `urls.py` | ~429 | DRF router registrations + a few plain `path()`s for non-ViewSet views, incl. the two new `create_group/`/`group/` paths (tasks 29/30, §5/§6b). **Its module docstring is itself the canonical endpoint reference** — reproduced in full in §5 below. |
| `admin.py` | 607 | Django admin registrations (inlines, list filters, bulk actions) — 33 `ModelAdmin`s (not 40+ — `NotificationAdmin`/`NotificationPreferenceAdmin` moved to `core/admin.py` per task 42, since `Notification` itself now lives in `core/models.py`; see §14). **Gap**: imports `ParentMessageTemplate`/`ParentTeacherMessage` from `.models` but never registers a `ModelAdmin` for either — see §14. |
| `signals.py` | ~620 | `@receiver`s for session-end cleanup, waitlist FCFS promotion, attendance credit, **+ 6 NEW classroom↔chat-group sync receivers (tasks 29–40, §6b)** (registered via `apps.py`) |
| `apps.py` | 33 | `AppConfig.ready()` — the thing that actually makes `signals.py` load |
| `tasks.py` | ~1845 | All Celery tasks — periodic sweeps + async `notify_*` senders (✅ recounted this pass — exactly **38** `@shared_task`s: 13 periodic/sweep + 25 `notify_*`, incl. **TASK 4**'s `notify_followers_new_classroom` — see §9) |
| `LearnScroll/celery.py` | 57 | **Project-level** Celery app bootstrap (sits next to `settings.py`, not part of the `liveclass` app itself). Creates the `Celery("LearnScroll")` instance, loads every `CELERY_*` setting from Django settings via `config_from_object(..., namespace="CELERY")`, and `autodiscover_tasks()`s a `tasks.py` in every `INSTALLED_APPS` app (`liveclass/tasks.py` included) — no manual per-task registration needed. Docstring spells out the one-time wiring: `LearnScroll/__init__.py` must do `from .celery import app as celery_app`; worker (`celery -A LearnScroll worker`) and beat (`celery -A LearnScroll beat`) are two separate long-running processes, both required. |
| `consumers.py` | ~577 | Django Channels WebSocket consumers (`SessionConsumer`, `UserConsumer`, `ClassroomConsumer` — new, see §6) |
| `routing.py` | 30 | WebSocket URL patterns (separate from `urls.py`, wired into ASGI, not WSGI) — 3 routes: session/user/classroom |
| `liveclass/ws_auth.py` | 53 | JWT-over-WebSocket auth middleware — **thin re-export only** (`from LearnScroll.ws_auth import JWTAuthMiddleware, get_user_from_token`), kept so every existing `from liveclass.ws_auth import JWTAuthMiddleware` call (including `asgi.py`) keeps working unchanged after the consolidation below. |
| `LearnScroll/ws_auth.py` | ~55 | **Project-level**, real implementation (moved here — was two independent, never-compared copies: `message/Middleware.py` vs the old `liveclass/ws_auth.py`). Pulls the JWT out of the `?token=` query string (a WS handshake can't carry a custom `Authorization` header), validates it with `rest_framework_simplejwt.tokens.AccessToken`, resolves it to a user via `get_user_from_token()` — every failure mode (bad signature, expired, malformed, deleted/deactivated user) degrades to `AnonymousUser` rather than raising, so a bad token can never crash the socket, it just connects unauthenticated. Both the `message` app (chat/calls/study-rooms) and `liveclass` import from this one file now, so the two can never silently drift out of sync again. |
| `realtime.py` | ~560 | Low-level pub/sub helpers: Redis-backed broadcast (session/user/classroom), presence, missed-event history, connect rate-limit |
| `livekit_utils.py` | ~434 | LiveKit token generation, room API client, webhook signature verification |
| `moderation.py` | 137 | Lightweight rule-based chat moderation (`screen_message`) — profanity/spam/caps detector |
| `notifications.py` | ~260 | Single fan-out point for push/email/SMS/WhatsApp — provider-agnostic |
| `exceptions.py` | 184 | DRF custom exception handler — normalises every error response to one JSON shape |
| `chunked_upload_views.py` | ~607 | Chunked file upload (init/chunk/complete/abort) for large files (cover images, recordings, materials) |
| `bridge.py` | 141 | **NEW (Task 12)** — `liveclass`'s only door into the unified, project-wide `assignment` app: `create_assignment()` / `get_assignment_submissions()`. Mirrors `campus/bridge.py`'s Task 11 pair field-for-field. Golden rule: `assignment` never imports `liveclass.*`, and `liveclass` never imports `assignment.models.Assignment`/`AssignmentSubmission` directly anywhere outside this module (or the one-off backfill command below). See §6d. |
| `management/commands/migrate_liveclass_assignments_to_unified.py` | 258 | **NEW (Task 12)** — one-off, idempotent, rollback-logged data migration: copies every legacy `liveclass.Assignment`/`AssignmentSubmission` row into the unified `assignment` app so history survives the cutover to `bridge.py`. See §6d. |
| `management/commands/migrate_liveclass_coin_models.py` | 348 | **NEW (Task 6)** — one-off, idempotent, rollback-logged backfill of `liveclass.CoinPurchase`/`CoinWithdrawal` history into `user_profile`'s `CoinPurchaseRequest`/`CoinWithdrawalRequest` + `CoinLedger`. Never touches `User.coin` — writes audit-trail rows only, via direct `.objects.create()`, bypassing `user_profile`'s live-write managers on purpose (those apply a balance delta; the balance has already moved once). See §6e. |
| `classroom_chat_views.py` | 95 | **NEW (tasks 29/30)** — `ClassroomCreateGroupView` (teacher-only "haan" confirm, POST) + `ClassroomGroupStatusView` (manager-tier read) for the classroom↔chat-group bridge. Deliberately its own file/own explicit `path()`s rather than `ClassroomViewSet` actions — see §6b. **✅ (Task 3) manager-tier check now reuses the real `_can_manage_classroom()` from `views.py`** instead of a local hand-rolled duplicate — see §6b known-gaps. |
| `core/classroom_chat_bridge.py` | 396 | **NEW, outside this app** (lives in the `core` app). ✅ **Source now actually seen** — not directly (still not uploaded under its own name), but the file uploaded under the `test_classroom_chat_bridge.py` name contains it verbatim (see next row's mismatch note) and has been read as such. **9 functions**, not 6: `create_classroom_group(classroom, actor)` (idempotent, teacher-only, raises `ValueError` for a non-teacher), `get_groups_for_classrooms(classrooms)` (🔧 Gap 2 fix — bulk read-only lookup, `{classroom_id: Group}`, 2 fixed queries regardless of count, silently omits any classroom with no linked group), `sync_membership_on_join_accept(classroom, student)`, `sync_membership_on_removal(classroom, student, reason="")`, `promote_to_moderator(classroom, user)`, `sync_group_metadata(classroom)`, `archive_group_on_classroom_close(classroom)` (posts a system message, broadcasts `group_deleted` over the channel layer, then soft-deletes both `Group` and `Conversation`), `post_welcome_message(classroom)`, `post_session_live_announcement(session)`. Referenced by `classroom_chat_views.py`, `signals.py` (6 call sites — see §13), `tasks.notify_session_live` (`post_session_live_announcement`), and (per its own docstring) `liveclass/views.py`. Every function except `create_classroom_group` is a silent no-op if the classroom has no linked group (`chat_group_enabled=False` or `linked_conversation_id` missing) — see §6b. Still carries a couple of `⚠️ ASSUMPTION` markers in its own docstring about `ClassJoinRequest`/`ClassroomStaff` field shapes, inferred from `tests.py`/`signals.py` rather than `models.py` directly — both shapes are independently confirmed correct elsewhere in this doc (§3), so treat those markers as resolved, just not yet edited out of the source comment itself. |
| `test_classroom_chat_bridge.py` | 396 (uploaded) / ~267 (documented test suite, unseen) | **NEW (task 40)** — *should be* regression tests for `core/classroom_chat_bridge.py` (create/accept/kick/refund/metadata-sync/archive), its own `test_*.py` module (Django auto-discovers `test*.py` per app), reusing `LiveClassTestBase` + the same `LIVEKIT_PATCH`/`SAFE_DELAY_PATCH` convention as `tests.py`. **🚨 CONTENT MISMATCH — confirmed again on this (second) upload, unchanged from before**: the file at this path is still `core/classroom_chat_bridge.py` **source code**, not test classes. Same failure shape as the historical `notification_batching.py`/`test_notification_batching.py` mix-up. Upside of this repeat mismatch: the bridge source itself is now confirmed (used to write the row above). **The actual test suite for this module still has not been seen** — §16's description of its 6 test classes remains last-confirmed history, not verified-current, across two uploads now. |
| `throttles.py` | — | **NEW (task 9)** — app-specific `SimpleRateThrottle` subclasses that don't fit the everyday `ScopedRateThrottle` (user-keyed) pattern. Currently one class: `ParentJoinIPThrottle` (scope `session_parent_join_ip`) — see §6c. Same single-purpose-throttles-file convention `message/throttles.py` already uses. |
| `permissions.py` | — | **ADDITIVE SNIPPET, not a full file** — the real `liveclass/permissions.py` (with `IsClassroomManager`/etc.) wasn't supplied to this task; this is one class (`HasValidParentSessionToken`) written to be pasted alongside what's already there. Not currently used as a `permission_class` anywhere in this app's uploaded files (`parent_join` resolves the parent token manually in its body instead, for throttle-ordering reasons) — see §6c. |
| `parent_link_views.py` | ~230 | **NEW FILE, ✅ now wired (Task 10 fix)** — teacher-managed `ParentAccessCode` endpoints: Phase 2 (`ClassroomParentCodeGenerateView` — teacher generates a code for a student), Phase 4 (`ReportCardViewSet` — server-computed report cards), Phase 5 teacher-side (`ClassroomParentQueryListView` + `ParentQueryReplyView` — parent-mode query threads). Cross-app: imports `ParentAccessCode`/`ParentModeQuery`/`ParentModeQueryMessage` from `message.models` and `create_bell_rows_for_push`/`send_parent_push` from `message.services`/`message.push_utils` — the same "liveclass calls into message, never the reverse" direction as `core/classroom_chat_bridge.py`. All four views now have real `path()`/`router.register()` entries in `urls.py`, and the `StudentReportCard` model they depend on is now defined in `models.py` — see §6c for the full picture (including the parent_join() consolidation onto this same `ParentAccessCode` mechanism). |
| `tests.py` | ~2610 | Test suite (Django `TestCase`), 25 test classes organized by feature area — see §16 for the confirmed gap (no `StudentProgressTests`) |
| `settings.py` | ~890 | **Project-level** Django settings (not liveclass-only — shared with `login`/`message` apps) |

**Wiring dependencies to remember (all previously-broken gaps, now fixed):**
- `signals.py` does nothing unless `apps.py` exists and `INSTALLED_APPS` points at `liveclass.apps.LiveclassConfig`.
- Celery **worker** (`celery -A LearnScroll worker`) and **beat** (`celery -A LearnScroll beat`) are two separate long-running processes — both required, neither optional. A task can be perfectly written and still never run if it isn't also registered in `CELERY_BEAT_SCHEDULE` (this bug happened at least 4 times historically — see §16).
- `LearnScroll/__init__.py` must do `from .celery import app as celery_app`.
- ASGI app (project-level `asgi.py`) wires `liveclass.routing.websocket_urlpatterns` through `liveclass.ws_auth.JWTAuthMiddleware`.
- `settings.py` → `REST_FRAMEWORK["EXCEPTION_HANDLER"] = "liveclass.exceptions.liveclass_exception_handler"`.
- `TeacherEarningsView`, `StudentProgressView`, `HealthCheckView` are plain `APIView`s, **not** ViewSets — `router.register()` never auto-wires them; each needs its own explicit `path()` in `urls.py` (this was missing at least 3 times historically). (`NotificationPreferenceView` used to be in this list too, but per task 42 it — and `NotificationViewSet` — moved to `core/views.py`; see the corrected rows in §4/§5.)
- **✅ RESOLVED (classroom↔chat bridge, tasks 29–40)**: `Classroom.chat_group_enabled` (`BooleanField`, default `False`) and `Classroom.linked_conversation_id` (`UUIDField`, null/blank) are now both defined on `Classroom` in `models.py` — `models_PATCH_apply_to_Classroom.md` has landed. Every code path that assumes these two attributes (`classroom_chat_views.py`, the 6 chat-sync signal receivers in `signals.py`) is safe to run now. See §6b.
- **✅ RESOLVED (parent access — Phases 2/4/5, `parent_link_views.py`, Task 10 fix)**: `ClassroomParentCodeGenerateView`, `ReportCardViewSet`, `ClassroomParentQueryListView`, `ParentQueryReplyView` all now have `path()`/router registration in `urls.py`. `StudentReportCard` is now a real model in `models.py` (§3), computed server-side via `compute_attendance_percent_bulk()` — `ReportCardViewSet.create()` no longer raises `ImportError` at module load. **Additionally**, `ClassSessionViewSet.parent_join` — the other, previously-independent parent-facing mechanism — has been consolidated onto this same `ParentAccessCode`/`resolve_parent_from_token()` base, so there's now exactly one parent-auth mechanism in this app, not two. See §6c for the full picture, including the now-dead-code left behind by that consolidation (`PARENT_JOIN_TOKEN_SALT`, `generate_parent_join_token`, `ParentJoinSerializer`).

---

## 2. Domain model (business flow)

```
User (login.User, AbstractUser — no fixed teacher/student role)
 └─ creates Classroom (becomes teacher for THIS classroom only)
     ├─ ClassSchedule (recurrence: daily/weekly/monthly/yearly/specific date/weekday/weekend)
     │    └─ generates → ClassSession (actual joinable occurrence, via Celery task)
     │         ├─ SessionParticipant (attendance log)
     │         ├─ ChatMessage / ChatReaction / ChatMessageReport / SessionReadState
     │         ├─ LivePoll / PollResponse / PollTemplate
     │         ├─ BreakoutRoom
     │         ├─ SessionWaitlist (capacity overflow, FCFS promotion)
     │         └─ ClassReminder (pre-session ping)
     ├─ ClassPass (daily/weekly/monthly/yearly/free — priced in coins)
     │    └─ any OTHER user raises → ClassJoinRequest (NOT a direct purchase)
     │         └─ teacher/co-teacher/moderator **accepts** →
     │              → PassPurchase created + coins debited (only if balance sufficient at accept-time)
     │              → optional Referral credit if a referral code was attached
     ├─ ClassMaterial, Assignment → AssignmentSubmission → grade
     ├─ ClassroomReview, ClassroomWishlist, ClassroomShare, ClassroomReport
     ├─ ClassHoliday, Notice, ClassQuery (student Q&A)
     ├─ ClassroomStaff (co-teacher/moderator roles)
     ├─ ClassroomBan
     └─ Certificate (issued to students)

Coin economy (platform-wide, not per-classroom):
 User.coin balance
   ← CoinPurchase (Razorpay top-up, INR → coins)
   ← CoinWithdrawal (coins → payout, admin-approved) — cancel/reject refunds coins
   ← Referral (referrer bonus, flat, one-time, on new-user signup)
   ← PassPurchase.reverse()/refund (money back on cancel/close/ban/expiry)
   ← PassGift.refund_to_gifter() (on cancel/expiry)
   → PassPurchase (debit on join-request accept, or gift claim)
   → PassGift (gift a pass to someone else — coins leave gifter's wallet immediately on send)
   → CoinWithdrawal (debited immediately on request, not on approval)
   every movement logged as CoinTransaction (append-only audit ledger)
```

### Access control levels (`views.py` helper functions)
- `_org_staff_role(classroom, user)` — resolves a user's `ClassroomStaff` role (manager/moderator) on a classroom, or `None`.
- `_can_manage_classroom(classroom, user)` — teacher or `ClassroomStaff` with manager-level role.
- `_can_moderate_session(session, user)` — manage-level **or** moderator-level staff.
- `_can_view_classroom_internals(classroom, user)` — has a valid `PassPurchase` **or** is staff/teacher.
- `_has_room_access_no_pass(classroom, user)` / `_has_room_access(classroom, user)` — LiveKit room join gate (the "no_pass" variant is used for staff/teacher who don't need a purchase).
- `_resolve_session_roles(session, user)` — single place that figures out host/moderator/participant role for a session.
- `AccessLevel` (class with string constants) + `_access_level(classroom, user)` — single canonical resolver used across viewsets so access logic doesn't drift between endpoints.
- `_accessible_classroom_ids(user)` — used to scope querysets (which classroom IDs can this user even see) in one place instead of repeating the filter logic per viewset.
- `Classroom.has_access(user)` — model-level version of the same gate (used outside request context too, e.g. in tasks/signals). **Fixed bug:** now also excludes purchases that hit their `max_classes` cap (previously an "N-class pack" pass let a student join unlimited sessions because this check stopped at status/expiry and never looked at the cap) and excludes banned students explicitly (defence-in-depth alongside the ban's own refund).
- No purchase → user only sees public listing info (title/description/subject/language/cover) + reviews.

---

## 3. Models (`models.py`, ~3200 lines, ~45 models)

### Cache versioning (perf pattern used twice)
- **Global**: `CLASSROOM_LIST_CACHE_VERSION_KEY = "liveclass:classroom_list:cache_version"`. `get_classroom_list_cache_version()` / `bump_classroom_list_cache_version()`. Bumped on every `Classroom` `post_save`/`post_delete` — covers direct edits AND internal saves from `refresh_rating()`/`refresh_enrolled_count()`/`sync_flag_status()` for free, since those all call `self.save(update_fields=[...])`, which fires the same post_save signal. Invalidates the whole Explore/search cache in O(1) instead of per-row tracking or a flat TTL (which would show stale ratings/counts for up to the TTL window — bad for a live marketplace).
- **Per-classroom**: `_notice_list_cache_version_key(classroom_id)` → `"liveclass:notice_list:cache_version:{classroom_id}"`. Same idea, scoped to one classroom's Notice board — bumping one classroom's notices must not invalidate every other classroom's cached page.

### File upload safety
- `MaxFileSizeValidator` (`@deconstructible`, so it survives migration serialization) — every FileField/ImageField has a size cap (`cover_image`: 5MB).
- `DOCUMENT_MEDIA_EXTENSIONS = ["pdf","doc","docx","ppt","pptx","xls","xlsx","png","jpg","jpeg","gif","webp","mp4","mov","webm","zip"]` — the safelist used by the four plain `FileField`s (material, assignment attachment, assignment submission, certificate). **Safelist, not blocklist** — a brand-new dangerous extension can't slip through just because nobody thought to blocklist it. Closes a renamed-`.exe`/`.php`/`.js`/`.sh` upload vector (stored-malware / drive-by risk, since the uploaded file gets served back to every other student/teacher who opens it). `cover_image` is inherently safe for free: Django's `ImageField` Pillow-decodes the upload to confirm it's a real image — a renamed `.exe` fails outright, and SVG is rejected too (Pillow can't decode it), which also closes the classic SVG/XSS vector as a side effect.

### `Classroom` (full field list)
- `classroom_type` (`individual`/`organisation`, default individual), `organisation_name` (required if organisation).
- `teacher` (FK → `User`, `CASCADE`, `related_name="classrooms_teaching"`) — **no role restriction**; any user becomes a teacher just by owning a `Classroom` row.
- `title`, `subject`, `description`, `cover_image` (`ImageField`, 5MB cap).
- `language` (default `"English"`) — search/filter facet.
- `whiteboard_enabled`, `screen_share_enabled`, `chat_enabled`, `recording_enabled` (all default `True`, frontend feature toggles).
- `captions_enabled` (default **False**, opt-in) — per-classroom speech-to-text captions gate; `LiveKitWebhookView`'s `egress_ended` handler checks this before queueing `transcribe_recording.delay()`, so transcription cost is only ever incurred when explicitly requested.
- `max_participants` (default 100, min 1).
- **Cached/denormalized stats** (kept in sync by signal receivers, so list/search never runs a per-row aggregate): `rating_avg` (decimal 3,2), `rating_count`, `enrolled_count` (help text: "distinct students currently holding an active, unexpired pass"), `share_count`.
- **Refer & earn (per-classroom, distinct from the flat signup `Referral`)**: `referral_enabled` (default False, opt-in), `referral_commission_percent` (0–100, default 0) — "% of each day's released class-earning charge that gets paid to whoever referred the student — deducted OUT OF the teacher's own share, never added on top by the platform." Combines with `referral_code_for_user()`/`referral_code_to_user_id()` (the same reversible-encoding helpers the signup-referral feature uses) via `ClassroomViewSet.refer_link`. See `ClassJoinRequest.referred_by` for attribution and `PassPurchase.charge_for_session` for the actual payout split.
- `is_active` (default True).
- **Anti-abuse deletion guard** (fixed exploit: teacher used to collect coins then immediately hard-delete the classroom, leaving students with zero recourse):
  - `is_deleted`, `deleted_at` — real delete is now **soft** (`ClassroomViewSet.perform_destroy`); row + full purchase/report history stays intact for dispute resolution.
  - `is_flagged` — auto-set once enough pending `ClassroomReport`s accumulate (`_auto_flag_classroom` signal, threshold = `AUTO_FLAG_THRESHOLD`); drops out of public Explore, teacher keeps their own access.
  - `MIN_AGE_BEFORE_DELETE_DAYS = 30` — `can_be_deleted()` blocks DELETE until the classroom is ≥30 days old **and** no student holds a paid, unexpired, un-refunded pass. To shut down early, teacher must use `/close/` instead, which refunds every active purchase first.
- `created_at`, `updated_at`.
- **`chat_group_enabled`** (bool, default False), **`linked_conversation_id`** (UUID, nullable) — ✅ **now defined on `Classroom`** (§1, §6b — landed as a `🔧 GAP FIX (Gap 2 prerequisite)` right in the model; migration still required via `manage.py makemigrations liveclass` before it takes effect). Gates whether any of `core/classroom_chat_bridge.py`'s sync functions do anything at all; `linked_conversation_id` points at the `message` app's `Conversation` backing the linked `Group` (plain `UUIDField`, not a FK — keeps `liveclass` decoupled from `message`'s models).
- **Meta**: `ordering = ["-created_at"]`; indexes on `(teacher, is_active)`, `-rating_avg`, `language`, `classroom_type`, `is_deleted`; plus **trigram GIN indexes** on `title`/`subject`/`description` (Postgres `pg_trgm` extension required — silently ignored on other DB backends, safe to leave in `Meta.indexes` regardless) so `?search=` `icontains` filters use an index scan instead of a sequential scan.

**Key methods:**
- `has_access(user)` — the tight gate for room entry / `enrolled_count`. Teacher always passes. Excludes banned students. Excludes purchases past `max_classes` cap (bug fix, see §2). Single indexed query, not a Python `is_valid()` loop.
- `is_enrolled(user)` — has this user *ever* successfully purchased (looser than `has_access`).
- `can_be_deleted() -> (bool, reason_str)`.
- `sync_flag_status()`, `refresh_rating()`, `refresh_enrolled_count()` — all self-save, all fire the cache-bump signal for free.
- `weekly_timing_summary()`, `upcoming_holidays(days_ahead=30)`, `record_share()`, `share_urls()`, `referral_urls(referrer)`.

### `ClassSchedule`
Recurrence rule (daily/weekly/monthly/yearly/specific-date/weekday/weekend). `is_off_on(date)` — holiday-aware (checks against `ClassHoliday`). Feeds `tasks._dates_for_schedule()` → `generate_upcoming_sessions`.

### `ClassSession`
The actual joinable occurrence. Key fields include status (with a `Status` enum whose terminal members are `COMPLETED`/`CANCELLED`), `scheduled_start`, `actual_end`, LiveKit room bookkeeping, and (per the captions-wiring note) an AWS Transcribe job-name field.
- `is_joinable(is_host=False)` — **time-window entry restriction was removed** (fixed — previously blocked joining outside a strict window, which was too rigid for real classroom flow).
- `compute_engagement_report()` — attendance + chat activity + poll-result summary, computed once on transition into `COMPLETED`, cached, then just read back by `sessions/{id}/engagement-report/`. Heavy version is offloaded to `tasks.build_engagement_report(session_id)`.
- **`whiteboard_snapshot` (JSONField, null) / `spotlight_identity` (CharField, blank="" = none) — NEW (whiteboard/spotlight persistence fix)**: both fields already existed but nothing read/wrote them — the collaborative whiteboard and the host's pinned/spotlighted tile lived only in each connected client's memory, synced peer-to-peer over the LiveKit data channel. Fine for a joiner while someone else in the room still holds the state, but breaks the moment the room empties and refills (host reconnects alone, or a student joins before anyone with the current board has (re)joined). Now written by dedicated actions on `ClassSessionViewSet` (`whiteboard()`/`spotlight()`, see §4) instead of the generic PATCH, so a non-host participant can autosave whiteboard strokes without needing full classroom-manage permissions — live peer-to-peer sync over the LiveKit data channel is unchanged; this is only the fallback a reconnect/late-joiner reads.

### `ClassPass`
Pricing tiers (daily/weekly/monthly/yearly/free), scoped to one classroom, teacher-owned.

### `PassPurchase` — the money-critical model
"Pay only for classes actually held" redesign: a pass used to pay the teacher the full price upfront; now it's escrowed and released **per day actually taught**.
- `is_valid()`, `remaining_balance()`, `referral_total_amount()`, `referral_remaining_balance()`.
- **`charge_for_session(session) -> PassDailyCharge | None`** — the core escrow release. Creates one `PassDailyCharge` row per (purchase, date), unique-constrained so charging is **idempotent** — calling it twice for the same session never double-charges. Splits the released amount between teacher and referrer (if `Classroom.referral_enabled` and a `ClassJoinRequest.referred_by` exists) per `referral_commission_percent`, deducted out of the teacher's cut, never added on top.
- `sync_missed_charges() -> int` — catch-up safety net if the `ClassSession` post_save signal missed a session (e.g. a status transition that happened outside a normal `.save()` path).
- `reverse(notify=True)` — refunds **remaining, un-taught balance only**, never more than what's actually left in escrow. Used by cancel/close/ban/expiry paths.
- `renew() -> PassPurchase | None` — auto-renewal chain; creates a new linked `PassPurchase`. **Fixed bug**: a coupon slot could get permanently burned by an unused/cancelled pass — renew logic now accounts for that (see NOTE at line ~1233).

### `PassDailyCharge`
One row per (purchase, date) charged. Unique constraint = idempotency guarantee for `charge_for_session`.

### `PassGift`
Gift a pass to another user. Coins leave the **gifter's** wallet immediately on send (not on claim). `refund_to_gifter()` on expiry/cancel. `CLAIM_WINDOW_DAYS = 7` — unclaimed gifts past this are swept by `tasks.expire_unclaimed_gifts`.

### `ClassJoinRequest`
The pending ask before a purchase exists. Carries `referred_by` (attribution for the per-classroom referral commission).

### `BreakoutRoom`, `SessionParticipant`, `ClassMaterial`
`SessionParticipant` — attendance log; **has no FK back to `PassPurchase`** (documented gap — see `signals.py` notes below: attendance-credit re-derives "the currently valid capped purchase" the same way `Classroom.has_access()` does, which can misattribute if a student somehow holds >1 active capped purchase for the same classroom — fine for the common case, exact tracking would need adding that FK).

### `ChatMessage`, `ChatMessageReport`, `ChatReaction`, `ChatMessageRead`, `SessionReadState`
- **Moderation had no teeth (fixed)**: `kick()` in `views.py` used to only remove someone from the LiveKit room without any DB-side ban/block record — now backed by real moderation flow (`screen_message`, `ChatMessageReport.review`).
- `SessionReadState` — unread-message tracking per (session, user), backs `sessions/{id}/unread/` + `/mark-read/`.
- **`ChatMessageRead` — NEW (read receipts)**: one row per (message, user), WhatsApp-style "seen by" for live-session chat. `unique_together = ("message", "user")` gives "one receipt per user per message" at the DB level instead of an app-level check-then-write race; append/upsert-only — a receipt never gets un-set. **Distinct from `SessionReadState.last_read_chat_message_id` above**: that field is a single per-user *watermark* driving only the unread-count badge on the sessions list and has no idea who else read a given message; `ChatMessageRead` is the other direction — given ONE message, who (and when) actually saw it, same idea as a Slack/WhatsApp per-message seen-by list. Written by `ChatMessageViewSet.read`/`mark_read`/`read_receipts` (see §4). `ChatMessageSerializer` exposes `read_count` (cheap "N people have seen this") and `seen_by_me` as `SerializerMethodField`s so the list payload stays light; the full seen-by list (who + when) is only fetched on demand via `read-receipts/`.

### `SessionReaction`, `SessionCaption` — NEW (in-session persistence fixes)
- **`SessionReaction`** — durable, append-only log (no `unique_together`, one row per tap) for the in-room emoji reactions (👍❤️😂👏🎉🙌) fired during a live session. Previously a *pure* LiveKit data-channel broadcast with nothing ever written to the DB — the moment every participant left the room the whole reaction history vanished, and a client reconnecting mid-session restarted its running reaction-total badge at zero. Live delivery to already-connected peers is **unchanged** (still the LiveKit data channel, for lowest latency); this table is the missing persistence layer underneath it, written by `ClassSessionViewSet.reactions()` (GET/POST, see §4) alongside — not instead of — the existing broadcast.
- **`SessionCaption`** — durable transcript line for the live on-device-STT caption feature. Each device still only ever recognizes its **own** mic (unchanged, on-device-STT limitation), but every device's own finished line is now *also* POSTed to the server and kept as a row here, so it survives the session ending and a client that joins mid-session or reconnects can fetch the transcript-so-far instead of seeing nothing (previously the on-screen feed was capped to the last 3 lines and self-expired after 6 seconds, with nothing persisted). Written by `ClassSessionViewSet.captions()` (GET/POST, see §4). This is separate from the async whole-recording transcription path (`Classroom.captions_enabled` / `tasks.transcribe_recording`), which covers the mixed recording after the session ends — a genuinely cross-participant *live* transcript would need real server-side STT on each track, a bigger separate change.

### `LivePoll`, `PollResponse`, `PollTemplate`
`PollTemplate` — reusable poll presets a teacher can `quick-create` from.

### `Assignment`, `AssignmentSubmission` — ⚠️ **local models frozen/legacy as of Task 12**
`AssignmentSubmission.is_late()`. Schema unchanged (no migration), but as of Task 12 these two models are **no longer the live write/read path for new assignments** — `liveclass/bridge.py`'s `create_assignment()`/`get_assignment_submissions()` routes new classroom assignments through the project-wide unified `assignment` app instead (see §6d). These rows still exist for **historical data only**, backfilled into the unified app one-time via `migrate_liveclass_assignments_to_unified` (§6d) — `AssignmentAdmin`/`AssignmentSubmissionAdmin` (§14) stay registered so that history stays visible in Django admin, but no new code path should create rows here going forward.

### `ClassroomReview`, `ClassroomWishlist`, `ClassroomShare`
`ClassroomShare` backs `share_count` and `share-stats`/`my-shares`.

### `Coupon`
`is_valid()`. Percent or flat discount, optionally scoped to one classroom or usable across one teacher's classrooms. Dry-run checkable via `coupons/validate/`.

### `CoinTransaction`
Append-only ledger — every coin movement (purchase, debit, refund, withdrawal, gift, referral) logged here. Read via `coin-transactions/` (own only) and `coin-transactions/balance/` (real `User.coin`). `Reason` choices now include `CLASS_REFERRAL_JOIN_BONUS` *(NEW — task 65)*, see below.

### `CoinPurchase` — ⚠️ **READ-ONLY as of Task 6**
Razorpay top-up. `mark_success(gateway_payment_id, gateway_signature)` / `mark_failed(reason="")` now **both raise `RuntimeError`** — kept as raising stubs (not deleted) so any call site this migration didn't find fails loudly instead of quietly re-crediting a wallet through a dead path. Coin top-ups now go through `user_profile.CoinPurchaseRequest` (`start_purchase()`/`confirm_success()`/`mark_failed()`), which writes through the shared `user_profile.CoinLedger.objects.record_transaction()` instead of touching `User.coin` from here. `CoinPurchaseViewSet.initiate`/`verify`/`retry` (`views.py`) were turned into 410-Gone stubs in the same pass. Existing rows (and this app's own `CoinTransaction` log) remain freely **readable** — history, admin, and `migrate_liveclass_coin_models` (§6e) all still read every field here; only the two write methods are blocked. Previously: stuck-`PENDING` rows (client crashed before `/verify/`, webhook lost) became retryable via `tasks.reconcile_stuck_coin_purchases` after `COIN_PURCHASE_PENDING_TIMEOUT` (2h) — that sweep's target write path is now also disabled by this change; check whether it's been decommissioned or repointed at `user_profile` before assuming it still does anything.

### `Referral`, `referral_code_for_user(user_id)`, `referral_code_to_user_id(code)`
Flat, one-time signup bonus — **distinct** from the per-classroom `referral_enabled`/`referral_commission_percent` mechanism on `Classroom`. Reversible encoding (not a DB lookup) — `referral_code_for_user`/`referral_code_to_user_id` are inverse functions. Referrer earns a bonus + ongoing per-session commission, **capped at total purchase amount**.

### Classroom Refer & Earn — dashboards + student join-bonus *(NEW — task 65)*
Builds on the existing per-classroom `referral_enabled`/`referral_commission_percent` mechanism (§3 `Classroom`, `ClassJoinRequest.referred_by`, `PassPurchase.charge_for_session`) with two read-only "aapne itne log invite kiye" dashboards plus a new one-time student-side bonus — none of this changes how the existing referrer commission itself is computed or paid.
- **`CoinTransaction.Reason.CLASS_REFERRAL_JOIN_BONUS`** *(new enum value)* — a one-time, **platform-funded** bonus credited to the **student** (not the referrer) when they join a classroom via someone else's referral link with `referral_enabled=True` at accept-time. Deliberately distinct from `CLASS_REFERRAL_COMMISSION` (the referrer's ongoing per-day cut, deducted out of the teacher's share) and from the flat signup `REFERRAL_BONUS` — this is a top-up funded by the platform itself, amount configured via `settings.CLASSROOM_REFERRAL_JOIN_BONUS_COINS`. Awarded exactly once per `PassPurchase`, in `_charge_and_create_purchase` (`views.py`), the same moment `referred_by` is attributed — fires even for a free pass (`coins_spent == 0`), since that code path re-locks the student row itself rather than reusing a `user` variable from the (skipped) wallet-debit block. `reference_id` is `class_referral_join:<purchase_id>`.
- **`ClassroomViewSet.referral_dashboard`** (`GET classrooms/{id}/referral-dashboard/`) — how the caller's own refer-link (`refer_link`, same view) has performed for **this one classroom**: `referred_count` (distinct students), `commission_earned`/`commission_pending` (summed/derived from `PassPurchase.referral_coins_released`/`referral_remaining_balance` where `referred_by=request.user` for this classroom's passes), and the caller's own `referral_code`. Open to the same audience as `refer_link` (anyone who can see the classroom) — a zero-referral dashboard for a classroom you haven't shared yet is a valid answer, not gated.
- **`ReferralViewSet.class_referral_summary`** (`GET referrals/class-referral-summary/`) — the **global** counterpart: aggregates the same `PassPurchase.referred_by` attribution across **every** classroom the caller has ever referred a student into, both a running total (`total_students_referred`, `total_commission_earned`, `total_commission_pending`) and a `by_classroom` breakdown (`classroom_id`/`title`/`referred_count`/`commission_earned`, ordered by commission earned descending). Lives on `ReferralViewSet` (not `ClassroomViewSet`) since it reads the same referral-attribution data `my_code`/`redeem` already deal with, just for the class-level program instead of the signup-level one.
- Both dashboards reuse `commission_earned`/`commission_pending` math already established by `charge_for_session`/`PassPurchase` (§3) — neither introduces new payout logic, they're read-only views over existing escrow data.

### `CoinWithdrawal` — ⚠️ **READ-ONLY as of Task 6**
Payout request lifecycle. Coins used to be debited **immediately on request**, not on approval — that whole lifecycle is now **frozen**: `create_request()`, `approve()`, `reject()`, `cancel()`, `mark_paid()`, `_refund_coins()` **all raise `RuntimeError`** ("... is disabled (Task 6) — use `user_profile.CoinWithdrawalRequest.objects.<equivalent>()`"). `CoinWithdrawalViewSet`'s create/cancel/approve/reject/mark-paid actions (`views.py`) were turned into 410-Gone stubs in the same pass. Withdrawals now go through `user_profile.CoinWithdrawalRequest` (`request_withdrawal()`/`mark_processing()`/`confirm_success()`/`reject()`), same shared `CoinLedger` ledger as the purchase side above. Existing rows stay readable (list/retrieve in `CoinWithdrawalViewSet`, admin, `migrate_liveclass_coin_models`, §6e) — only the six write methods are blocked. Historical fields worth remembering for the read side: `MIN_WITHDRAWAL_COINS = 100`, `COIN_TO_INR_RATE = 1`, `payout_details` (JSON: bank = `{account_holder, account_number, ifsc}`, UPI = `{upi_id}`), `Status.CANCELLED` had no equivalent in the new model (folded into `REJECTED` on migration — see §6e).

### `ClassroomBan`, `ClassroomStaff`, `SessionWaitlist`, `ClassroomReport`
- `ClassroomBan` — issuing a ban refunds the banned student's active pass and rejects their pending join requests (see `ClassroomViewSet.ban`); `Classroom.has_access()` also independently excludes banned students as defence-in-depth.
- `ClassroomStaff` — co-teacher/moderator roles (manager-level vs moderator-level, checked by `_org_staff_role`/`_can_manage_classroom`/`_can_moderate_session`). `test_classroom_chat_bridge.py` also exercises a `ClassroomStaff.Role.CO_TEACHER` choice + a `post_save`(created) signal that promotes the new staff user to `GroupMember.MODERATOR` in the linked chat group (§6b) — that exact shape (`ClassroomStaff(classroom, user, role)`) is **✅ now confirmed** against the real `models.py` definition (`classroom_chat_views.py`'s own docstring was updated: "VERIFIED (Task 2 gap-fix pass) ... no changes needed there"), no longer an unconfirmed assumption.
- `SessionWaitlist` — capacity overflow queue; FCFS promotion is compare-and-swap (`filter(notified=False).update(notified=True)`, single query — not read-then-save, which had a race where two students leaving at once could double-promote the same waitlisted student).
- `ClassroomReport` — auto-flags the classroom at `AUTO_FLAG_THRESHOLD` pending reports (`_auto_flag_classroom` signal, `post_save`), lazy-imports `tasks.notify_classroom_flagged` so `models.py` stays importable even without Celery wired.

### `Certificate`, `ClassReminder`, `ClassHoliday`, `Notice`, `ClassQuery`
`Notice.is_expired()`. `Notice` list is the per-classroom cached read (§ cache versioning above).

### `StudentReportCard` — ✅ **now a real model (Task 10 fix, was a gap)**
Previously just a comment (`GAP FIX (Gap 3)`) marking where its `attendance_percent` should be sourced
from — `message/views_parent.py` already imported and used `StudentReportCard as
LiveclassStudentReportCard` (parent dashboard "latest report card" block), so that import was failing
with `ImportError` at Django startup until this landed. Fields: `classroom` (FK, `related_name=
"report_cards"`), `student` (FK), `period_label` (free-text, e.g. "October 2026"/"Term 1"/"Week 12" —
teacher-defined, not a structured date range, since reporting cadence varies per classroom/subject),
`attendance_percent`/`homework_completion_percent` (`DecimalField(max_digits=5, decimal_places=2,
default=0)`), `average_marks` (same, nullable), `teacher_remark` (`TextField`, blank ok), `created_at`/
`updated_at`. `Meta.indexes = [Index(fields=["classroom", "student"])]` — matches the exact query shape
`message/views_parent.py` uses (`filter(classroom_id__in=..., student=...).order_by('classroom_id',
'-id')`). Written exclusively by `ReportCardViewSet` (§6c) — `attendance_percent`/
`homework_completion_percent`/`average_marks` are always server-computed, never accepted from a request
body.

### `Notification`, `NotificationPreference`, `create_notification()`, `create_bulk_notifications()`
- `Notification.mark_read()`.
- `NotificationPreference.allowed_channels_for(notif_type)`, `.for_user(cls, user)` (classmethod, get-or-create-like accessor). Fields include `push_enabled`/`email_enabled`/`sms_enabled`/`whatsapp_enabled`, `muted_types`, `digest_frequency` (off/daily/weekly), `last_digest_sent_at`.
- **"Production notification coverage audit" NOTE** (line ~2779): six specific notification types were previously missing coverage — now all wired (see the full `notify_*` task list in §9).
- **(TASK 4, new)** `tasks.notify_followers_new_classroom` (§9) references `Notification.NotifType.CLASSROOM_CREATED_BY_FOLLOWED` and passes a `classroom=classroom` kwarg into `create_bulk_notifications(...)`, per that task's own docstring claiming a `classroom` FK already exists on `Notification`. **Neither is independently verified against `models.py` this pass** (`models.py` wasn't part of this upload) — `models.py`'s own `Notification` definition should be checked for both the enum member and the FK before relying on this in production. Flagged the same way `NEW_POST_FROM_FOLLOWED` was flagged unconfirmed in `post_app.md` §22 / `TESTSERIES_CREATED_BY_FOLLOWED` in `testseries_app_reference.md` §3.9 — this is the third app in the codebase to add an identically-shaped "notify my followers when I create X" feature, and all three now share this same unconfirmed-enum caveat.

### `ChunkedUpload`
Tracks large multi-chunk uploads in progress (see §8). Indexed on `(user, status)` and `(status, created_at)` — the latter is what makes `tasks.cleanup_stale_chunked_uploads` cheap.

### Model-level signals (bottom of `models.py`, lines ~3078–3200)
- `_bump_classroom_list_cache(sender, instance, **kwargs)` — `Classroom` post_save/post_delete → bump global list cache version.
- `_bump_notice_list_cache(sender, instance, **kwargs)` — `Notice` post_save/post_delete → bump per-classroom notice cache version.
- `_sync_classroom_rating(sender, instance, **kwargs)` — `ClassroomReview` post_save/post_delete → `refresh_rating()`.
- `_sync_classroom_enrolled_count(sender, instance, **kwargs)` — `PassPurchase` post_save → `refresh_enrolled_count()`.
- `_stash_previous_session_status(sender, instance, **kwargs)` — `ClassSession` pre_save → stash previous status (so post_save can detect a **transition into** COMPLETED, not just any edit).
- `_charge_passes_for_completed_session(sender, instance, created, **kwargs)` — `ClassSession` post_save → **"the other half of the per-day escrow design"**: on transition into COMPLETED, loops every active successful `PassPurchase` for that classroom and calls `charge_for_session()` under `select_for_update()`. One purchase failing never blocks the rest or rolls back the session save; `sync_missed_charges()` is the safety net if this signal itself is ever bypassed.
- `_auto_flag_classroom(sender, instance, created, **kwargs)` — `ClassroomReport` post_save → auto-flag + queue `notify_classroom_flagged` (lazy import). Also: teacher previously had no way to find out their classroom got auto-flagged — now notified (line ~3186 NOTE).

---

## 4. Views / API (`views.py`, ~6320 lines, DRF ViewSets)

### Permission/role helper functions (module-level, reused everywhere — don't duplicate this logic in a new spot)
`_org_staff_role`, `_has_room_access_no_pass`, `_has_room_access`, `_resolve_session_roles`, `_can_moderate_session`, `_can_manage_classroom`, `_can_view_classroom_internals`, `AccessLevel` (class) + `_access_level`, `_accessible_classroom_ids`.

### Cross-cutting bits
- `LiveClassPagination(pagination.PageNumberPagination)` — shared pagination class for all list endpoints.
- `_safe_delay(task, *args, **kwargs)` — wraps `.delay()` calls so a broker hiccup never crashes the request that triggered the notification.
- `_safe_broadcast_to_user(user_id, event_type, data)` — same defensive wrapper around `realtime.broadcast_to_user`.
- `_is_truthy(value)` — query-param boolean parsing helper (`?mine=true` etc.).

### ViewSets and every `@action` (module line numbers as of this audit)

⚠️ Line numbers below were re-confirmed for `ClassroomViewSet` only (the
one TASK 4 touches, this pass). `views.py` grew from ~6320 to ~6508
total lines since the rest of this table was last fully re-walked, so
every other row's line number below may have drifted further and
should be treated as approximate — same "trust the code over a stale
line reference" caveat §17 item 25 already gives for `urls.py`'s
docstring.

| ViewSet | `get_queryset`/`perform_*` | `@action`s |
|---|---|---|
| `ClassroomViewSet` (276) | yes, all 4 — **`perform_create` (TASK 4, NEW)**: after `serializer.save(teacher=...)`, enqueues `tasks.notify_followers_new_classroom(classroom.id)` via `_safe_delay` (see §4 cross-cutting bits) so the teacher's followers get notified off the request path — same fan-out shape as `post.tasks.notify_followers_new_post`/`testseries.tasks.notify_followers_new_testseries`, this app's siblings for the identical feature. See §9, §3. | `close`, `has_access`, `my_pass` (url `my-pass`), `start-or-join`, `stats` (GET), `share` (POST), `share-stats` (GET), `my-shares` (GET, detail=False), `refer-link` (GET), `referral-dashboard` (GET) *(NEW — task 65, §3)*, `recommended` (GET, detail=False, ?limit=), `ban` (POST), `bans` (GET), `unban/(?P<student_id>...)` (POST), `recordings` (GET). Custom `list()` uses the version-based cache. |
| `ClassroomReportViewSet` (1059) | yes | `review` (POST, staff-only decision on a report) |
| `ClassScheduleViewSet` (1162) | yes, all 4 | standard CRUD, scoped to own classrooms |
| `ClassSessionViewSet` (1392) | yes, create/update/destroy | `join` (POST, throttled `session_join` scope), `parent-join` (POST, `AllowAny` + `ParentJoinIPThrottle` — unauthenticated observer join for a parent holding a `ParentAccessCode`-issued token, resolved via `resolve_parent_from_token()`, see §6c), `end` (POST), `engagement-report` (GET), `token` (POST, throttled `session_token` scope — fresh LiveKit token, no participant row, for reconnect/testing), `kick/(?P<user_id>...)` (POST), `mute/(?P<user_id>...)` (POST, body `{"muted": true|false}`), `hand` (POST, raise/lower **own** hand), `hand/(?P<user_id>...)/lower` (POST, lower **someone else's** hand), `whiteboard` (POST, `{"snapshot": {...}|null}`, `_has_room_access` gate, not manage-only — see whiteboard persistence note under `ClassSession` in §3), `spotlight` (POST, `{"identity": "<livekit id>"|null}`, `_can_moderate_session` gate, re-broadcasts `spotlight` over the session's realtime channel), `reactions` (GET+POST, `{"reaction": "heart"}`, throttled `session_reaction` scope, `_has_room_access` gate — durable log behind `SessionReaction`, see §3), `captions` (GET+POST, `{"text": "..."}`, throttled `session_caption` scope, `_has_room_access` gate — durable transcript behind `SessionCaption`, see §3), `unread` (GET, Pass 13), `mark-read` (POST, Pass 13), `start-recording` (POST), `stop-recording` (POST), `breakout` (GET+POST), `breakout/assign` (POST), `breakout/close` (POST). |
| `LiveKitWebhookView` (`APIView`, 2489) | — | receives LiveKit room/participant/egress_ended webhooks (server-to-server only) |
| `ClassPassViewSet` (2578) | yes, all 4 | CRUD (teacher-owned). `perform_destroy`/`perform_update` enforce the "can't shrink what active holders paid for" rule (see §5 `passes/{id}/`). |
| `PassPurchaseViewSet` (2732) | yes | `refund` (POST), `cancel` (POST), `toggle-auto-renew` (POST, body `{"auto_renew": bool}` — Pass 15/16), `referral-earnings` (GET, detail=False). `_charge_and_create_purchase` (module-level) is the **shared** "debit coins + create purchase" logic used by both join-request-accept and gift-claim flows — single source of truth for the money-moving step. |
| `ClassJoinRequestViewSet` (3067) | yes, create | `accept` (POST — **this is where money actually moves**, via `_charge_and_create_purchase`), `reject` (POST, no charge), `cancel` (POST, requester-only, pending-only) |
| `PassGiftViewSet` (3437) | yes, create | `claim` (POST, recipient-only — creates the `PassPurchase`), `cancel` (POST, gifter-only, pending-only — refunds gifter) — Pass 14 |
| `SessionParticipantViewSet` (3623) | yes | `leave` (POST) |
| `ClassMaterialViewSet` (3666) | yes, all 4 | standard CRUD |
| `ChatMessageViewSet` (3734) | yes, create/destroy | `react` (POST+DELETE), `pin` (POST, Pass 13), `unpin` (POST, Pass 13), `read` (POST — mark one message read/seen by the caller, idempotent, only broadcasts `chat.read` on an actual new receipt, Pass 13), `mark-read` (POST, detail=False — bulk version, body `{"session": <id>, "up_to": <message_id>}`, excludes the caller's own messages, Pass 13), `read-receipts` (GET — full oldest-first "seen by" list for one message, kept out of the main list payload on purpose, Pass 13). `perform_create` runs `moderation.screen_message()` **after** save (Pass 14) — a false positive must never block a real send, only add to a moderator review queue. Read/mark-read/read-receipts are looked up directly (not via `get_queryset()`) for the same reason `react`/`pin` are — the `?session=` filter only applies to `list`. |
| `ChatMessageReportViewSet` (4098) | yes, create | `review` (POST, moderator — soft-deletes the reported message when actioned) — Pass 14 |
| `LivePollViewSet` (4200) | yes, all 4 | `vote` (POST), `close` (POST), `quick-create` (POST, detail=False, from a `PollTemplate` — Pass 13) |
| `PollTemplateViewSet` (4378) | yes, all 4 | CRUD (reusable poll presets) — Pass 13 |
| `AssignmentViewSet` (4704) | ⚠️ **no longer accurate — see §6d** | — |
| `AssignmentSubmissionViewSet` (4815) | ⚠️ **no longer accurate — see §6d** | — |
| `ClassroomReviewViewSet` (4673) | yes, all 4 (`perform_update`/`perform_destroy` ownership-gated — Pass 19/21 fix, see §17 item 19) | — |
| `ClassroomWishlistViewSet` (4757) | yes, create/destroy | — |
| `CouponViewSet` (4794) | yes, all 4 | `validate` (GET, detail=False, `?code=`, throttled `coupon_validate` scope — dry-run check without spending) |
| `CoinTransactionViewSet` (4909, list-only `GenericViewSet`) | yes | `balance` (GET, detail=False) |
| `CoinWithdrawalViewSet` (4940) | yes, create | `cancel` (POST), `approve` (POST, staff), `reject` (POST, staff), `mark-paid` (POST, staff) |
| `_create_gateway_order(amount_inr, receipt)` / `_verify_gateway_signature(order_id, payment_id, signature)` (5084, module-level) | — | Razorpay order-create + HMAC signature verify, shared by `CoinPurchaseViewSet` |
| `CoinPurchaseViewSet` (5118) | yes | `initiate` (POST, detail=False, body `{"coins": N}`), `verify` (POST — gateway checkout callback payload), `retry` (POST — re-attempt a FAILED purchase) |
| `ReferralViewSet` (5219, list-only `GenericViewSet`) | yes | `my-code` (GET, detail=False), `redeem` (POST, detail=False, body `{"code": "R..."}`), `class-referral-summary` (GET, detail=False) *(NEW — task 65, §3)* |
| `TeacherEarningsView` (`APIView`, 5346) | — | dashboard, not a ViewSet — explicit `path()` in urls.py |
| `StudentProgressView` (`APIView`, 5413) | — | dashboard, not a ViewSet — explicit `path()` in urls.py |
| `ClassroomStaffViewSet` (5487) | yes, all 4 | manage co-teacher/moderator roles |
| `SessionWaitlistViewSet` (5584) | yes | `promote` (POST — manual override of FCFS auto-promotion) |
| `CertificateViewSet` (5692) | yes, create | issue (via `perform_create`) |
| `ClassReminderViewSet` (5758) | yes, create | — |
| `ClassHolidayViewSet` (5786) | yes, all 4 (`perform_update` ownership-gated — Pass 19/21 fix, see §17 item 19) | — |
| `NoticeViewSet` (5854) | yes, all 4 | `pin` (POST). Custom cached `list()` (per-classroom notice cache version). |
| `ClassQueryViewSet` (6000) | yes, create/update (`perform_update` ownership-gated, frozen once `ANSWERED` — Pass 19/21 fix, see §17 item 19) | `answer` (POST, teacher/co-teacher/moderator) |
| ✅ **CORRECTED** — `NotificationViewSet`/`NotificationPreferenceView` are **no longer in this file** | — | Per task 42 (core-app migration), both moved to `core/views.py`, wired via `core/urls.py` under the `core/` prefix — `liveclass/urls.py` no longer registers `notifications/` or `notification-preferences/me/` at all (confirmed by `views.py`'s own module note just above `MyDashboardView`). Don't look for these two here. |
| `ParentMessageTemplateViewSet` (6247, list/retrieve-only `GenericViewSet`) | yes (`get_queryset` only) | **NOT PREVIOUSLY DOCUMENTED — task 69.** Read-only, platform-curated catalog of message templates (like `PollTemplate` but platform-authored, not per-teacher); not classroom-scoped, same list for every authenticated user. Optional `?category=<ParentMessageTemplate.Category>` filter. No pagination (small fixed catalog). |
| `ParentTeacherMessageViewSet` (6268, list/retrieve/create `GenericViewSet`) | yes, create | **NOT PREVIOUSLY DOCUMENTED — task 69.** `reply` (POST, manage-tier via `_can_manage_classroom`, 400 if already `RESPONDED`). A parent/student with valid classroom access (`_can_view_classroom_internals`) sends one of the fixed templates to the teacher; `perform_create` resolves the template text and writes a bell `Notification` (`PARENT_MESSAGE_RECEIVED`) — but **no `_safe_delay(notify_*, ...)` push/digest queue yet**, unlike every sibling action in this file (confirmed: `tasks.py` has no `notify_parent_message_received` task at all — this is a real, still-open gap, not just an omission from this doc). `reply()` similarly writes a `PARENT_MESSAGE_REPLIED` bell row only. No update/destroy at all — a sent message is a fixed audit record. `get_throttles` scopes `create` only to `parent_message_create` (needs a matching `DEFAULT_THROTTLE_RATES` entry in `settings.py` or it raises `ImproperlyConfigured` on the first real message — same pattern as `session_join`/`coupon_validate`). Visibility mirrors `ClassQueryViewSet`: `?classroom=<id>` shows everything to a manager, else only the caller's own sent messages; no filter = "my sent messages" across every classroom. |
| `MyDashboardView` (`APIView`, 6395) | — | single-call home-screen summary |
| `HealthCheckView` (`APIView`, 6453) | — | unauthenticated DB+cache liveness probe |

---

## 5. URLs (`urls.py`) — full endpoint reference

One `DefaultRouter` with ~28 `router.register(...)` entries, mounted at project level as
`path("liveclass/", include("liveclass.urls"))`. All paths below are prefixed with `/liveclass/`.

**Non-router `APIView`s (each needs its own explicit `path()` — a router only auto-wires ViewSets):**

| Path | Method | View |
|---|---|---|
| `dashboard/` | GET | `MyDashboardView` — single-call home-screen summary |
| `my-earnings/` | GET | `TeacherEarningsView` — teacher-only earnings summary; optional `?classroom=<id>` |
| `my-progress/` | GET | `StudentProgressView` — caller's own attendance/assignment/certificate stats + streak |
| ~~`notification-preferences/me/`~~ | — | ✅ CORRECTED — **no longer registered here**. `NotificationPreferenceView` moved to `core` (task 42); this path is now served under the `core/` prefix in the root urlconf, not `liveclass/urls.py`. |
| `classrooms/<uuid:classroom_id>/create_group/` | POST | `classroom_chat_views.ClassroomCreateGroupView` — NEW, tasks 29/30, see §6b |
| `classrooms/<uuid:classroom_id>/group/` | GET | `classroom_chat_views.ClassroomGroupStatusView` — NEW, tasks 29/30, see §6b |
| `livekit-webhook/` | POST | `LiveKitWebhookView` — server-to-server only, not for client/app use |
| `healthz/` | GET | `HealthCheckView` — unauthenticated; 200 = healthy, 503 = a dependency is down |
| `uploads/chunked/init/` | POST | `chunked_upload_views.chunked_upload_init` |
| `uploads/chunked/chunk/` | POST | `chunked_upload_views.chunked_upload_chunk` |
| `uploads/chunked/complete/` | POST | `chunked_upload_views.chunked_upload_complete` |
| `uploads/chunked/abort/` | POST | `chunked_upload_views.chunked_upload_abort` |
| `schema/`, `schema/docs/` | GET | drf-spectacular OpenAPI schema + Swagger UI — only present if the package is installed (wrapped in try/except so `urls.py` still imports cleanly without it) |

**Router-registered ViewSet endpoints:**

```
classrooms/                          GET, POST         (?search=, ?language=, ?mine= supported on GET)
classrooms/{id}/                     GET, PUT, PATCH, DELETE  (DELETE only once 30+ days old AND no
                                                        active paid pass outstanding — see
                                                        Classroom.can_be_deleted(); otherwise 400, use
                                                        .../close/ instead. Successful DELETE = soft delete.)
classrooms/{id}/close/               POST              (teacher only — refunds every active paid pass,
                                                        then deactivates the classroom)
classrooms/{id}/has-access/          GET
classrooms/{id}/my-pass/             GET               (owner/active/expired/none + expires_at)
classrooms/{id}/stats/               GET
classrooms/{id}/share/               POST              (in-app if to_user_id given, else a web/deep link)
classrooms/{id}/share-stats/         GET               (teacher/co-teacher/moderator)
classrooms/{id}/refer-link/          GET               (own shareable per-classroom referral link; 404s
                                                        via ValidationError if referral_enabled=False)
classrooms/{id}/referral-dashboard/  GET               (NEW — task 65, "aapne itne log invite kiye" scoped
                                                        to this one classroom: referred_count, commission
                                                        earned/pending, own referral_code — see §3)
classrooms/recommended/              GET               (?limit= — personalized by purchase/wishlist
                                                        history, falls back to rating/enrollment)
classrooms/{id}/ban/                 POST
classrooms/{id}/bans/                GET
classrooms/{id}/unban/{student_id}/  POST
classrooms/{id}/recordings/          GET
classrooms/{id}/create_group/        POST              (NEW, tasks 29/30 — teacher only, explicit confirm.
                                                        Plain APIView (classroom_chat_views.py), not a
                                                        ClassroomViewSet action — own explicit path() below
                                                        the router block. 400 if chat_group_enabled already
                                                        True. See §6b.)
classrooms/{id}/group/               GET               (NEW, tasks 29/30 — manager-tier read: teacher/
                                                        co-teacher/moderator only, not students — see §6b.
                                                        Also a plain APIView, own explicit path().)

coin-purchases/                      GET only          ⚠️ STALE per this urls.py docstring — POST/create
                                                          was never a route on this viewset even before
                                                          Task 6 (list/retrieve mixins only); own top-up
                                                          HISTORY, read-only.
coin-purchases/initiate/             410 GONE          ⚠️ STALE — Task 6 turned this into a fixed 410
                                                          response pointing at
                                                          /api/user-profile/coin-purchases/initiate/. See §6e.
coin-purchases/{id}/verify/          410 GONE          ⚠️ STALE — points at /api/user-profile/coin-purchases/.
coin-purchases/{id}/retry/           410 GONE          ⚠️ STALE — points at
                                                          /api/user-profile/coin-purchases/initiate/.

schedules/                           GET, POST
schedules/{id}/                      GET, PUT, PATCH, DELETE

sessions/                            GET, POST
sessions/{id}/                       GET, PUT, PATCH, DELETE
sessions/{id}/join/                  POST
sessions/{id}/token/                 POST              (fresh token, no participant row — reconnect/testing)
sessions/{id}/parent-join/           POST              (UNAUTHENTICATED, AllowAny — body {"parent_token":
                                                          "..."}; ✅ CORRECTED — resolved via the SAME
                                                          ParentAccessCode/ParentToken mechanism as every
                                                          other parent-facing endpoint
                                                          (core.classroom_chat_bridge.resolve_parent_from_
                                                          token()), NOT a signed django.core.signing token
                                                          (that old scheme is dead code — see §6c). Observer-
                                                          role LiveKit token issued if the resolved student
                                                          has valid access to this classroom. No participant
                                                          row created. IP-throttled via ParentJoinIPThrottle,
                                                          not user-throttled — resolved in the view body
                                                          rather than via a permission class so a bad-token
                                                          guess still counts against the throttle. See §6c.
                                                          🐛 Confirmed currently broken: both call sites pass
                                                          `role=ParticipantRole.OBSERVER`, which doesn't
                                                          exist (`livekit_utils.py` only defines
                                                          `PARENT_OBSERVER`) — AttributeError on every real
                                                          call. See §6c/§17 item 24.)
sessions/{id}/end/                   POST              (teacher/co-teacher/moderator)
sessions/{id}/kick/{user_id}/        POST              (teacher/co-teacher/moderator)
sessions/{id}/mute/{user_id}/        POST              (force-mutes mic without removing; body
                                                        {"muted": true|false}, defaults to true)
sessions/{id}/hand/                  POST              (any active participant — raise/lower OWN hand;
                                                        body {"raised": true|false}, defaults to true)
sessions/{id}/hand/{user_id}/lower/  POST              (teacher/co-teacher/moderator — lower someone
                                                        else's raised hand)
sessions/{id}/start-recording/       POST              (400 if Classroom.recording_enabled is off or a
                                                        recording already running)
sessions/{id}/stop-recording/        POST              (recording_url fills in async via LiveKit webhook)
sessions/{id}/breakout/              GET, POST         (GET: anyone with room access — current breakout
                                                        layout, [] = none running. POST (manage/moderate
                                                        only): {"room_count": int} creates that many
                                                        empty numbered rooms; 400 if already running)
sessions/{id}/breakout/assign/       POST              ({"participant_id": int, "room": int|null})
sessions/{id}/breakout/close/        POST              (deletes all breakout rooms, everyone back in main)
sessions/{id}/whiteboard/            POST              (NEW — persistence fix. Any room participant,
                                                        `_has_room_access` not manage-only; body
                                                        {"snapshot": {strokeId: {...}, ...} | null} —
                                                        always a full overwrite, same last-write-wins
                                                        contract as any other autosave. Live sync between
                                                        connected clients stays peer-to-peer over LiveKit's
                                                        data channel; this is only the reconnect/late-
                                                        joiner fallback — see ClassSession in models.py)
sessions/{id}/spotlight/             POST              (NEW — persistence fix. Host/co-teacher/moderator
                                                        only (`_can_moderate_session`); body
                                                        {"identity": "<livekit identity>" | null}. Persists
                                                        the pin so a late joiner reads it off the session
                                                        GET, and re-broadcasts a "spotlight" event over the
                                                        session's realtime channel to already-connected
                                                        peers)
sessions/{id}/reactions/             GET, POST         (NEW — persistence fix, throttled `session_reaction`
                                                        scope. GET: {"total": int, "counts": {emoji: int}}
                                                        for the session. POST: {"reaction": "heart"} logs
                                                        one emoji tap and returns {"total": int}; live
                                                        delivery to connected peers is still the LiveKit
                                                        data channel — this is the durable log underneath
                                                        it, see SessionReaction in models.py)
sessions/{id}/captions/               GET, POST        (NEW — persistence fix, throttled `session_caption`
                                                        scope. GET: the session's live-caption transcript
                                                        so far, oldest-first, capped to the last 200 lines.
                                                        POST: {"text": "..."} appends the caller's own
                                                        just-finished on-device-STT line (speaker is always
                                                        request.user) — see SessionCaption in models.py)
sessions/{id}/engagement-report/     GET               (teacher/co-teacher/moderator only)
sessions/{id}/unread/                GET
sessions/{id}/mark-read/             POST

passes/                              GET, POST
passes/{id}/                         GET, PUT, PATCH, DELETE  (teacher only. Once ever purchased: DELETE
                                                        refused outright — use PATCH is_active=false to
                                                        pause instead (existing holders keep access,
                                                        history intact); PATCH refused for any change
                                                        that would retroactively shrink what active
                                                        holders paid for — price can't rise, and
                                                        validity_days/max_classes/pass_type can't be
                                                        reduced/changed — while ≥1 active paid purchase
                                                        is outstanding.)

join-requests/                       GET, POST         (POST: request to join, on a pass; GET: own
                                                        requests, or ?classroom= as teacher/co-teacher/
                                                        moderator for every request on that classroom,
                                                        optionally + ?status=pending/accepted/rejected/
                                                        cancelled)
join-requests/{id}/                  GET
join-requests/{id}/accept/           POST              (teacher/co-teacher/moderator — charges coins,
                                                        creates the PassPurchase)
join-requests/{id}/reject/           POST              (teacher/co-teacher/moderator — no charge)
join-requests/{id}/cancel/           POST              (requesting student only, while still pending)

pass-purchases/                      GET               (own purchases only by default; ?classroom=<id>
                                                        as that classroom's teacher/co-teacher/moderator
                                                        lists every purchase on it instead)
pass-purchases/{id}/                 GET
pass-purchases/{id}/refund/          POST              (classroom's teacher/co-teacher/moderator, or
                                                        platform staff, only — coins back to the
                                                        student, clawed back from the teacher)
pass-purchases/{id}/toggle-auto-renew/ POST            (body {"auto_renew": true|false})
pass-purchases/referral-earnings/    GET

pass-gifts/                          GET, POST         (GET: own gifts, sent AND received; POST: send —
                                                        body {"recipient_id", "class_pass",
                                                        "gift_message"} — coins leave gifter's wallet
                                                        immediately)
pass-gifts/{id}/                     GET
pass-gifts/{id}/claim/                POST             (recipient only, pending + not expired — this
                                                        creates the PassPurchase and starts validity)
pass-gifts/{id}/cancel/               POST             (gifter only, while still pending — refunds gifter)

participants/                        GET
participants/{id}/                   GET
participants/{id}/leave/             POST

materials/                           GET, POST
materials/{id}/                      GET, PUT, PATCH, DELETE

chat-messages/                       GET, POST         (POST body may include "reply_to": <message id> to
                                                        quote an earlier message in the same session; GET
                                                        supports &search=<text> to filter session chat)
chat-messages/{id}/                  DELETE            (soft delete)
chat-messages/{id}/react/            POST, DELETE
chat-messages/{id}/pin/              POST
chat-messages/{id}/unpin/            POST
chat-messages/{id}/read/             POST              (NEW — read receipts. Mark this one message as
                                                        seen by the caller; idempotent — re-marking an
                                                        already-read message is a no-op, not an error;
                                                        broadcasts "chat.read" only on an actual new receipt)
chat-messages/mark-read/             POST              (NEW — read receipts, bulk version. Body
                                                        {"session": <id>, "up_to": <message id>} marks
                                                        every not-yet-read message up to and including
                                                        that id as seen — excludes the caller's own messages)
chat-messages/{id}/read-receipts/    GET               (NEW — read receipts. Full "seen by" (who + when)
                                                        list for one message, oldest-first; kept out of the
                                                        main list payload — see ChatMessageRead in models.py)

chat-message-reports/                GET, POST         (POST: report a message, body {"message",
                                                        "reason", "note"} — re-filing against the same
                                                        message updates it rather than duplicating;
                                                        GET: own filed reports, or ?session=<id> as that
                                                        session's teacher/co-teacher/moderator/staff for
                                                        its whole queue)
chat-message-reports/{id}/review/     POST             (body {"status": "actioned"|"dismissed"};
                                                        actioning also soft-deletes the reported message)

polls/                               GET, POST
polls/{id}/                          GET, PUT, PATCH, DELETE
polls/{id}/vote/                     POST
polls/{id}/close/                    POST
polls/quick-create/                  POST              (from a PollTemplate)
poll-templates/                      GET, POST
poll-templates/{id}/                 GET, PUT, PATCH, DELETE

assignments/                         GET, POST         ⚠️ STALE per this urls.py docstring — see §6d/§17.
                                                          As of Task 12, AssignmentViewSet is a plain
                                                          viewsets.ViewSet with ONLY list/create; no
                                                          retrieve/update/destroy exist any more (both
                                                          proxy to bridge.py -> the unified `assignment`
                                                          app). GET filters by required ?classroom=.
assignments/{id}/                    ❌ REMOVED (Task 12) — retrieve/update/destroy are gone; the unified
                                                          assignment.views.AssignmentViewSet doesn't serve
                                                          context-sourced (campus/liveclass) assignments
                                                          either, so there is currently NO way to edit or
                                                          delete an already-posted classroom assignment
                                                          anywhere in the system — flagged in bridge.py/
                                                          views.py as a gap in the unified app, not patched
                                                          around locally. See §6d.
submissions/                         GET only          ⚠️ STALE per this urls.py docstring — POST/grade
                                                          are gone (Task 12). List-only proxy: teacher's
                                                          classroom-wide queue (?classroom= required,
                                                          optional ?assignment=) or a student's own rows.
submissions/{id}/                    ❌ REMOVED (Task 12)
submissions/{id}/grade/              ❌ REMOVED (Task 12) — grading now happens directly against the
                                                          unified app's own submission endpoint
                                                          (`/assignment/submissions/{id}/...`, not part of
                                                          this app). See §6d.

reviews/                             GET, POST
reviews/{id}/                        GET, PUT, PATCH, DELETE

wishlist-classrooms/                 GET, POST         (own saved classrooms; POST {"classroom_id": id})
wishlist-classrooms/{id}/            DELETE            (remove from wishlist, own entry only)

coupons/                             GET, POST
coupons/{id}/                        GET, PUT, PATCH, DELETE
coupons/validate/                    GET               (?code= required — checks without spending)

coin-transactions/                   GET               (own ledger only)
coin-transactions/balance/           GET               (real User.coin balance)

withdrawals/                         GET only          ⚠️ STALE per this urls.py docstring — POST/create
                                                          is now a fixed `@action` that returns 410 Gone
                                                          (Task 6), not real create. GET: own requests, or
                                                          every request (?status= filter) for platform staff.
withdrawals/{id}/                    GET
withdrawals/{id}/cancel/             410 GONE          ⚠️ STALE — points at /api/user-profile/coin-withdrawals/.
withdrawals/{id}/approve/            410 GONE          ⚠️ STALE — same target.
withdrawals/{id}/reject/             410 GONE          ⚠️ STALE — same target.
withdrawals/{id}/mark-paid/          410 GONE          ⚠️ STALE — same target. All five 410 bodies share the
                                                          shape {"detail": "...", "new_endpoint": "..."} — see
                                                          §6e's `_deprecated_write_response()`.

staff/                               GET, POST
staff/{id}/                          GET, PUT, PATCH, DELETE

waitlist/                            GET               (own waitlist entries)
waitlist/{id}/                       DELETE            (leave the waitlist, own entry only)
waitlist/{id}/promote/               POST              (teacher/co-teacher/moderator)

certificates/                        GET, POST         (GET: own, or ?classroom= as teacher/co-teacher/
                                                        moderator for all issued there; POST: issue,
                                                        teacher/co-teacher/moderator only)
certificates/{id}/                   GET

reminders/                           GET, POST
reminders/{id}/                      GET, PUT, PATCH, DELETE

holidays/                            GET, POST         (?classroom= required for GET)
holidays/{id}/                       GET, PUT, PATCH, DELETE

notices/                             GET, POST         (?classroom= required for GET)
notices/{id}/                        GET, PUT, PATCH, DELETE
notices/{id}/pin/                    POST              (teacher/co-teacher/moderator)

queries/                             GET, POST         (student asks a doubt; ?classroom= scopes list)
queries/{id}/                        GET, PUT, PATCH, DELETE
queries/{id}/answer/                 POST              (teacher/co-teacher/moderator)

classroom-reports/                   GET, POST         (POST: file a report on a classroom; GET: own
                                                        reports, or every report — filterable by
                                                        ?classroom= / ?status= — for platform staff)
classroom-reports/{id}/              GET
classroom-reports/{id}/review/       POST              (platform staff only)

~~notifications/ (+ sub-paths)~~             ✅ CORRECTED — **not registered in `liveclass/urls.py` at all
                                              any more**. Moved to `core` (task 42) — served under the
                                              `core/` prefix in the root urlconf now, via `core/urls.py`.

parent-message-templates/            GET               (NEW, NOT PREVIOUSLY DOCUMENTED — task 69.
                                                        `ParentMessageTemplateViewSet`, read-only,
                                                        platform-curated catalog, ?category= filter)
parent-message-templates/{id}/       GET
parent-messages/                     GET, POST         (NEW, NOT PREVIOUSLY DOCUMENTED — task 69.
                                                        `ParentTeacherMessageViewSet`; POST sends a
                                                        template to the classroom's teacher, throttled
                                                        `parent_message_create` scope; GET ?classroom=<id>
                                                        for a manager, else own sent messages only)
parent-messages/{id}/                GET               (no PUT/PATCH/DELETE — fixed audit record)
parent-messages/{id}/reply/          POST              (manage-tier only; 400 if already responded)

referrals/                           GET               (people the caller has successfully referred)
referrals/my-code/                   GET               (own referral code + redemption tally)
referrals/redeem/                    POST              (redeem someone else's code, once, new-account-
                                                        only; body {"code": "R..."})
referrals/class-referral-summary/    GET               (NEW — task 65, global "aapne itne log invite kiye"
                                                        dashboard: per-classroom referral commission
                                                        earned/pending, across every classroom the caller
                                                        has referred a student into — see §3)
```

**✅ Now wired (Task 10 fix, was previously a gap):** `parent_link_views.py`'s four views —
`ClassroomParentCodeGenerateView`, `ReportCardViewSet`, `ClassroomParentQueryListView`,
`ParentQueryReplyView` — all now have real `path()`/`router.register()` entries in `urls.py`:
`classrooms/<uuid:classroom_id>/participants/<int:user_id>/parent-code/` (POST),
`report-cards/` (GET/POST, router-registered flat, not nested — `ReportCardViewSet` scopes by
classroom via the request body/`?classroom=` query param instead of a URL kwarg),
`classrooms/<uuid:classroom_id>/parent-queries/` (GET), and
`parent-queries/<str:query_id>/reply/` (POST). See §6c.

---

## 6. Realtime layer

### WebSocket endpoints (`routing.py`)
```python
websocket_urlpatterns = [
    re_path(r"^ws/liveclass/session/(?P<session_id>\d+)/$", consumers.SessionConsumer.as_asgi()),
    re_path(r"^ws/liveclass/user/$", consumers.UserConsumer.as_asgi()),
    re_path(r"^ws/liveclass/classroom/(?P<classroom_id>\d+)/$", consumers.ClassroomConsumer.as_asgi()),
]
```
- `ws/liveclass/session/<session_id>/` → `SessionConsumer` — per-session realtime (chat, hand-raise, presence, polls, breakout events).
- `ws/liveclass/user/` → `UserConsumer` — per-user channel (personal notifications/pushes not scoped to one session).
- `ws/liveclass/classroom/<classroom_id>/` → `ClassroomConsumer` — **NEW (classroom-stats realtime fix)**: per-classroom channel for `rating_avg`/`rating_count`/`enrolled_count` pushes. See `ClassroomConsumer` below.

### `consumers.py`
**Read-only by design**: this app accepts exactly two inbound client message types on `SessionConsumer` — `"ping"` (keepalive/RTT) and `"typing"` — and just `"ping"` on `UserConsumer`. Every real write (chat send, poll vote, hand raise) already has a validated/permission-checked/throttled REST endpoint (`ChatMessageViewSet.create`, `LivePollViewSet.vote`, `ClassSessionViewSet.hand`, etc.) — duplicating that here would mean two code paths to keep in sync, with no free `ScopedRateThrottle`/serializer-validation equivalent on a socket. Anything else a client sends gets echoed back as an `"error"` event telling it which REST endpoint to use.

**Close codes** (app-defined, mirror REST status semantics): `4401` unauthenticated, `4403` no room access / kicked, `4404` session not found, `4408` idle timeout, `4429` WS-connect rate-limited.

- `_session_and_access(session_id, user)` (module-level) — one `database_sync_to_async` query mirroring `ChatMessageViewSet._session_or_none` + `_has_room_access` together (a consumer only needs the boolean). Imports `_has_room_access` from `views.py` directly rather than re-deriving the rule, so REST and WebSocket access can never drift apart.

**`SessionConsumer(AsyncJsonWebsocketConsumer)`** — one client ↔ `ws/session/<id>/` ↔ that session's Channels group (`session.<id>`). Delivers everything `realtime.broadcast_to_session()` sends for that session: `chat.message`/`chat.message_deleted`, `poll.created`/`updated`/`closed`, `hand.raised`/`lowered`, `recording.started`/`stopped`/`ready`, `waitlist.promoted`, `participant.kicked`, `presence.joined`/`left`/`snapshot`, plus the socket-only `chat.typing`.
- `connect()`: auth check (4401) → **rate limit** (Pass 12, `check_connect_rate_limit(user.id)`, checked *before* the DB lookup so a throttled attempt only costs a Redis round-trip, closes 4429) → session/access lookup (4404/4403) → joins the group, accepts, sends `connection.ack` with a `server_time` watermark → **catch-up replay** (Pass 10: reads `?since=<unix_ts>` from the query string, replays anything missed via `get_missed_events()`, each entry flagged `"replayed": true` so the client skips re-animating it) → **presence** (Pass 11: `mark_present()`, broadcasts `presence.joined` only on this user's 0→1 transition, then sends a `presence.snapshot` of everyone already there — snapshotted *after* marking self present, so the client always sees its own id) → starts the **idle-watchdog** task (Pass 12).
- `_idle_watchdog()` (Pass 12): wakes every `_IDLE_CHECK_INTERVAL_SECONDS` = **15s**; if no `"ping"` in `_IDLE_TIMEOUT_SECONDS` = **90s**, closes the socket itself with code `4408`, re-entering the normal `disconnect()` teardown (not a second/parallel disconnect path). Answers a gap Pass 11 flagged in its own docstring: an airplane-mode phone or killed app never sends a close frame, so without this, presence only cleared on the ASGI server's own transport timeout (minutes, not seconds). Reuses the existing ping/pong keepalive — no new inbound message type needed.
- `disconnect(close_code)`: cancels the idle-watchdog task, leaves the group, then `mark_absent()` — broadcasts `presence.left` only on the 1→0 transition (`left_for_good`). Group-leave happens *before* the broadcast, so a disconnecting client never receives its own `presence.left` echo.
- `receive_json(content, **kwargs)`: any inbound message bumps `_last_seen_at` (proves the connection's alive, not just a `"ping"` specifically). `"ping"` → `pong`. `"typing"` (Pass 12) → direct `group_send` (**not** `broadcast_to_session()` — deliberately skipped so a reconnecting client never replays a stale "is typing"), tagged with `sender_channel` so the sender excludes its own echo; no explicit "stopped typing" event, client auto-clears the indicator a few seconds after the last one seen (standard chat-UI debounce). Anything else → an `"error"` event pointing at the right REST endpoint.
- `session_event(message)` (server→client, dispatched via `channel_layer.group_send(..., {"type": "session.event", ...})`): skips delivery if `message["sender_channel"] == self.channel_name` (the typing-echo exclusion above); otherwise forwards `{event, payload, ts}`. **Fixed gap**: `participant.kicked` now actually closes the kicked user's own live socket (code 4403) if their `user_id` matches the payload — previously `kick()`/`ban()` only blocked *future* REST `join()`/`token()` calls, so an already-open socket kept receiving session events indefinitely.

**`UserConsumer(AsyncJsonWebsocketConsumer)`** — `ws/liveclass/user/`, no session id in the path; auth alone (no DB lookup) gates entry into group `user.<id>`. Delivers everything `realtime.broadcast_to_user()` sends for *this* user: `join_request.created` (teacher's pending-count badge), `join_request.decided` (student's own request flipping), `staff.added`. Deliberately thinner than `SessionConsumer`: no presence tracking, no missed-event replay buffer (every event here already has a REST-backed source of truth a normal reopen re-derives — see `broadcast_to_user()`'s own docstring), no idle-watchdog. Still answers `"ping"`/`pong` (same keepalive purpose) but has no `"typing"` (session-scoped concept only). Shares `SessionConsumer`'s same per-user rate limiter (`check_connect_rate_limit`, keyed by `user_id` not `session_id`) — a reconnect loop on either socket burns the same shared budget, the correct scope for connect-loop protection.

**`ClassroomConsumer(AsyncJsonWebsocketConsumer)`** — `ws/liveclass/classroom/<classroom_id>/` (**NEW — classroom-stats realtime fix**). Auth-only gate, no further per-object permission check (classroom `rating_avg`/`rating_count`/`enrolled_count` is public marketplace info any current viewer cares about — matches `ClassroomViewSet`'s own `IsAuthenticated`-only permission). Mirrors `UserConsumer` almost exactly (same thin shape: no presence, no replay buffer, no idle-watchdog, shares the same per-user WS-connect rate limiter), plus one addition `UserConsumer` doesn't need: a DB existence check on connect (`_classroom_exists`) — a client can pass an arbitrary/stale classroom id in the URL and gets a real `4404` instead of silently joining a group nothing will ever publish to. `connect()` order: auth (4401) → rate limit (4429) → existence check (4404) → group `classroom.<id>` → accept → `connection.ack`. Only inbound message handled is `"ping"`; anything else gets the same read-only `"error"` reply pattern as the other two consumers. `classroom_event(message)` is the server→client dispatch target for `realtime.broadcast_to_classroom()`.

**Why this exists**: `classroom_detail_screen.dart`'s `LiveClassClassroomSocket` already connected to exactly this route with a "NEEDS A BACKEND COUNTERPART" comment — previously nothing backed it, so every connection attempt failed and the screen silently fell back to its (much longer) backstop poll. `models.py`'s `_broadcast_classroom_stats()` (called from `Classroom.refresh_rating()` and `refresh_enrolled_count()`) already tried `from .realtime import broadcast_to_classroom`, but that name didn't exist in `realtime.py` either — both the consumer and the broadcast function were missing at the same time, so the whole path was a silent `ImportError` swallowed by `_broadcast_classroom_stats`'s own try/except (the rating/enrollment recompute itself always succeeded; only the realtime push was silently dropped).

### `realtime.py` (Redis-backed helpers used by both consumers and `views.py`/`tasks.py`)
Why it exists: `views.py` has called `from .realtime import broadcast_to_session` from a dozen call sites (chat create/delete, poll create/vote/close, hand raise/lower, recording start/stop) since before this file existed — a guaranteed `ImportError` at app-boot until this module was added. `broadcast_to_user()` closed a second, quieter version of the same gap: `views.py`'s `_safe_broadcast_to_user()` (`ClassJoinRequestViewSet` create/accept/reject, `ClassroomStaffViewSet.perform_create`) already called it, but wrapped in a try/except that only logs — so it never crashed, it just silently no-op'd every join-request badge push and staff-promotion push.

**Message flow**: `views.py: broadcast_to_session(42, "chat.message", {...})` → `channel_layer.group_send("session.42", {"type": "session.event", ...})` → `consumers.py: SessionConsumer.session_event()` fans it out to every socket in group `session.42` as `{"event": "chat.message", "payload": {...}}`.

**Group naming**: `"session.<id>"` / `"user.<id>"` — dots, not colons (Channels group names only allow ASCII alphanumerics, hyphens, underscores, periods).

- `_group_name(session_id)` → `"session.<id>"`, `_user_group_name(user_id)` → `"user.<id>"`.
- `_history_key`/`_presence_key(session_id)` → `liveclass:session_history:<id>` / `liveclass:session_presence:<id>`. `_raw_redis_client(write)` → `cache.client.get_client(write=...)`, `None` on any non-django-redis backend (e.g. `LocMemCache` in local dev) or Redis error — the single shared "is raw Redis available" check every helper below relies on.
- **Missed-event catch-up (Pass 10)**: a Channels group only delivers to sockets connected at the moment `group_send()` fires — a reconnecting student (flaky mobile data is the normal case here, not the edge case) used to just permanently miss whatever happened while offline, with no REST endpoint able to answer "what did I miss since X". `broadcast_to_session()` now also appends every event (RPUSH+LTRIM+EXPIRE in one pipeline, so a concurrent append can't race the trim) to a per-session replay buffer: last `_HISTORY_MAX_EVENTS` = **50** events, `_HISTORY_TTL_SECONDS` = **15 min**. `get_missed_events(session_id, since)` returns buffered events with `ts > since` (or everything buffered if `since is None`); `consumers.py` calls this on connect, keyed by a client-remembered `?since=<unix ts>`. Comfort feature, not a guarantee — both functions return `False`/`[]` on any Redis error, never fail the connect/send.
- **Live presence (Pass 11)**: `mark_present`/`mark_absent(session_id, user_id)` keep a per-session Redis HASH of `user_id -> open-connection count` via atomic `HINCRBY` (a count, not a flag, so the same student on phone + browser tab doesn't get marked "left" when only one closes). Each returns `True` only on the 0→1 / 1→0 transition — `consumers.py` broadcasts `presence.joined`/`presence.left` only then, not on every extra tab. `get_present_user_ids(session_id)` answers "who's here already" for a freshly-connecting client's `presence.snapshot`. `_PRESENCE_TTL_SECONDS` = **6h**, refreshed on every mark call, self-cleans if a session's sockets all die uncleanly.
- **WS-connect rate limiting (Pass 12)**: `check_connect_rate_limit(user_id)` — fixed-window counter (`INCR`, `EXPIRE` set only on the window's first hit so it can't keep pushing the window back), `_WS_CONNECT_LIMIT` = **20** connects per `_WS_CONNECT_WINDOW_SECONDS` = **60s**, per-user (not per-session). Meant to be called from `consumers.py`'s `connect()` *before* the DB session/access lookup, so a rate-limited attempt only costs a Redis round-trip. Deliberately a fixed-window cap, not a token bucket — a WS connect is bursty by nature (page load, or a network drop causing simultaneous reconnects across tabs), so this caps a reconnect *loop* without punishing a reconnect *burst*.
- **Fail-open contract, applies to every function above**: no raw Redis client, or any Redis exception, degrades to the safe default (`True`/allow for the rate limiter, `False`/`[]` for history/presence reads) — realtime/abuse-guard side channels must never be the reason a chat message fails to send or a connection fails to open. Same principle `notifications.send_notification()` follows elsewhere in this app.
- `broadcast_to_session(session_id, event_type, payload) -> bool` and `broadcast_to_user(user_id, event_type, payload) -> bool` — the two public fan-out functions; both best-effort (never raise), both log-and-return-`False` on failure. Known `event_type` values in use: `chat.message`, `chat.message_deleted`, `poll.created`, `poll.updated`, `poll.closed`, `hand.raised`, `hand.lowered`, `recording.started`, `recording.stopped`, `recording.ready`, `participant.kicked`, `presence.joined`, `presence.left` (session-scoped); `join_request.created`, `join_request.decided`, `staff.added` (user-scoped). `broadcast_to_user()` deliberately does **not** write to the replay buffer — `UserConsumer` has no `?since=` catch-up, since every event it pushes already has a REST-backed source of truth a normal reopen/`_load()` re-derives correctly.
- `broadcast_to_classroom(classroom_id, event_type, payload) -> bool` (**NEW — classroom-stats realtime fix**) — per-classroom counterpart to `broadcast_to_user()`, same relationship `broadcast_to_session()` has to it, just grouped by `classroom_id` (`_classroom_group_name` → `"classroom.<id>"`) instead of `user_id`/`session_id`. Same best-effort/never-raises contract; also has **no** replay/history buffer, same reasoning as `broadcast_to_user()` — a reconnecting client just re-fetches the classroom-detail REST endpoint, which already has the current stats. Only `event_type` in use today: `"classroom.stats"`, payload `{classroom_id, rating_avg, rating_count, enrolled_count}` — see `models.py`'s `_broadcast_classroom_stats()`, called from `Classroom.refresh_rating()`/`refresh_enrolled_count()`. Dispatched to `ClassroomConsumer.classroom_event()` in `consumers.py`.

### `ws_auth.py`
JWT-over-WebSocket auth. Token read from `?token=` query string (a browser/mobile WS client can't set a custom `Authorization` header on the handshake). Same short-lived lifetime as any other API call (`SIMPLE_JWT.ACCESS_TOKEN_LIFETIME`), `wss://` in production. Real implementation lives at **project-level** `LearnScroll/ws_auth.py` (neutral to both `liveclass` and `message` apps, since they must never import each other's auth middleware); `liveclass/ws_auth.py` just re-exports `JWTAuthMiddleware`/`get_user_from_token` so existing imports keep working.

**Fixed (consolidation)**: `message` (chat/calls/study-rooms) and `liveclass` (classroom realtime) each used to carry their own independent, never-compared-line-by-line copy of this middleware (`message/Middleware.py` vs the old `liveclass/ws_auth.py`). Both now import from the single project-level `LearnScroll/ws_auth.py`, so a future edit to the auth logic can't update one app's copy and silently leave the other mismatched.

Wire into project `asgi.py`:
```python
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
import django

django.setup()
django_asgi_app = get_asgi_application()

from liveclass.routing import websocket_urlpatterns
from liveclass.ws_auth import JWTAuthMiddleware

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(
        JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
    ),
})
```

---

## 6b. Classroom ↔ chat-group bridge (tasks 29–40) — **NEW, in progress**

Links a `Classroom` to a `message`-app `Group`/`Conversation`, so a classroom's teacher/students get an actual group chat outside of `liveclass`'s own `ChatMessage` (which is scoped to a single `ClassSession`, not persistent across the classroom). All logic lives in `core/classroom_chat_bridge.py` (a different Django app — `core`) — `liveclass` only calls into it, `core` never imports `liveclass`/`message` back. ✅ Its actual source has now been seen (see §1's file-map rows) — 9 functions, full list there; the two not previously documented are `get_groups_for_classrooms()` (bulk read-only helper for e.g. a parent dashboard looping over many classrooms) and `post_welcome_message()`/`post_session_live_announcement()` (system-message posters, called right after group creation and from `tasks.notify_session_live` respectively).

⚠️ **Suspected URL-routing bug, worth confirming against real `settings.py`/`Classroom` migrations**: `urls.py` wires `classrooms/<uuid:classroom_id>/create_group/`, `.../group/`, `.../participants/<int:user_id>/parent-code/`, and `.../parent-queries/` with Django's `uuid` path converter for `classroom_id`. `Classroom` (§3) has no explicit `id` field — it's a plain auto-incrementing integer `AutoField`, not a UUID pk (unlike `ClassSession.room_id`/`Certificate.certificate_id`/`ChunkedUpload.upload_id`, which genuinely are UUIDs). Django's `uuid` converter only matches a UUID-formatted string, so a request to any of these four paths with a real (integer) classroom id would **fail to match the URL pattern at all** — a 404 before the view even runs, not a 403/permission error. Either `Classroom`'s pk was meant to become a UUID (not migrated here) or these four `path()` entries should use `<int:classroom_id>` like every other `classrooms/<...>/` route in the same file. Flagging rather than silently "fixing" — confirm which is intended before changing either side.

**Files touched:**
- `classroom_chat_views.py` (new) — the two HTTP entry points (§5): `ClassroomCreateGroupView` (POST, teacher-only explicit confirm — "haan" in the docstring, i.e. deliberately opt-in per classroom, not auto-created) and `ClassroomGroupStatusView` (GET, manager-tier: teacher/co-teacher/moderator, **not** students — students discover the group through their own `message` app group list once added). Both plain `APIView`s with their own explicit `path()`, same pattern this app already uses for `dashboard/`/`my-earnings/`/etc.
- `urls.py` — wires the two paths (§5).
- `signals.py` — 6 receivers, all lazy-importing `core.classroom_chat_bridge` inside a `transaction.on_commit()` callback (same production-hardening contract as every other signal in this file, §13) and all wrapped in their own try/except (a broken chat-sync must never break the state change that triggered it):
  1. `sync_chat_group_on_join_accept` (`post_save`, `ClassJoinRequest`) — fresh transition into `ACCEPTED` → `sync_membership_on_join_accept`.
  2. Waitlist FCFS promotion (inside `on_participant_left`, §13) — same `sync_membership_on_join_accept` call, for a promoted-from-waitlist student.
  3. `sync_chat_group_on_staff_add` (`post_save` created, `ClassroomStaff`) → `promote_to_moderator`.
  4. `sync_chat_group_on_classroom_change` (`post_save`, `Classroom`, paired with `stash_previous_classroom_snapshot` `pre_save`) — `is_active True→False` **or** `is_deleted→True` → `archive_group_on_classroom_close` (checked first; an archived classroom's metadata is not also synced). Otherwise, if `title`/`cover_image`/`description` changed → `sync_group_metadata`.
  5. `sync_chat_group_on_ban` (`post_save` created, `ClassroomBan`) → `sync_membership_on_removal(..., reason="kick")`.
  6. `sync_chat_group_on_purchase_refund` (`post_save`, `PassPurchase`, paired with `stash_previous_purchase_status` `pre_save`) — fresh transition into `REFUNDED` → `sync_membership_on_removal(..., reason="refund")`.
  - **Deliberately NOT wired**: a session-level "kick" (`SessionParticipant.kicked_at`) — that's a temporary single-session removal, not a classroom-wide ban, and must not touch chat-group membership; only a full `ClassroomBan` does.
- `test_classroom_chat_bridge.py` (§1, §16) — `core/classroom_chat_bridge.py`'s own regression suite, **as last confirmed**: create/idempotency/non-teacher-rejected, accept (direct call + signal-fired), removal (direct/ban-signal/refund-signal, all no-op safely without a group), metadata sync, archive-on-close (+ idempotent double-archive), and staff→moderator promotion. 🚨 **The latest upload under this filename is stale/wrong** (bridge source code, not tests — see §1's file-map row) — treat this bullet as history until a real test file is re-uploaded and re-confirmed.

**Every `core/classroom_chat_bridge.py` function is a no-op if the classroom has no linked group** (`chat_group_enabled=False` / `linked_conversation_id=None`) — so none of the above signal receivers need their own "is this classroom chat-enabled" check; the bridge module owns that gate centrally.

**✅ Both known gaps from earlier audits are now resolved:**
1. ~~`models.py` doesn't define `Classroom.chat_group_enabled`/`Classroom.linked_conversation_id` yet~~ — **resolved.** Both fields are now defined directly on `Classroom` in `models.py` (`chat_group_enabled = BooleanField(default=False)`, `linked_conversation_id = UUIDField(null=True, blank=True)`), landed as a `🔧 GAP FIX (Gap 2 prerequisite)` right in the model, with a migration (`manage.py makemigrations liveclass`) still required before it takes effect. `linked_conversation_id` is deliberately a plain `UUIDField`, not a FK — keeps `liveclass` decoupled from `message`'s models, same invariant the bridge module itself exists to enforce; type matches `message.Conversation`'s UUID PK without a DB-level FK constraint enforcing it. Consistent with §1's wiring-dependencies note, which already flagged this as resolved.
2. ~~`classroom_chat_views.py` hand-rolls its own `_is_classroom_manager()`~~ — **resolved.** The local duplicate has been removed; the file now imports and uses the real `_can_manage_classroom()` from `liveclass/views.py` directly (`from .views import _can_manage_classroom`), so `ClassroomGroupStatusView`'s manager-tier check can no longer drift out of sync with the rest of the app's teacher/co-teacher/moderator/org-staff logic. `ClassroomCreateGroupView` is unaffected — it was always a plain `classroom.teacher_id != request.user.id` check (teacher-only, deliberately narrower than manager-tier — creating the group is a one-time classroom-level decision, not routine moderation), not something `_is_classroom_manager()` ever gated.

---

## 6c. Parent-facing features — **now a single reconciled mechanism** — ✅ RESOLVED (post-Task-9/11 consolidation)

Two separate "let a parent see something" systems used to exist here, built at different times, sharing
no code, permission class, or token format. **That gap is now closed.** `ClassSessionViewSet.parent_join`
has been switched onto the same `ParentAccessCode`/`ParentToken` resolution every other parent-facing
endpoint already used — one parent identity, one revocable code, works for every parent-facing feature.
This section now documents the single mechanism, then the dead code left behind by the merge.

### The mechanism — `ParentAccessCode`/`ParentToken`, resolved via `core.classroom_chat_bridge.resolve_parent_from_token()`

A durable, DB-backed code a teacher explicitly generates for one student (`message` app's
`ParentAccessCode`/`ParentToken` models), resolved everywhere in this app through the same one function —
`core.classroom_chat_bridge.resolve_parent_from_token()` (§6b's bridge module, a *second* cross-app
dependency on it alongside the chat-group sync). No mechanism in this app mints or checks a bespoke
signed token any more.

- **`ClassroomParentCodeGenerateView`** (POST `classrooms/{id}/participants/{user_id}/parent-code/`,
  manage-tier via `_can_manage_classroom` — teacher/co-teacher/moderator, imported directly from
  `views.py`, not re-implemented) — teacher generates a code on a student's behalf via
  `ParentAccessCode.generate_for(student, created_by=request.user, ...)`, capped at
  `ParentAccessCode.MAX_ACTIVE_CODES` (default 5) active codes per student. Fires a best-effort bell
  notification to the student (`create_bell_rows_for_push`) so they know a code was created behind their
  back — never blocks the 201 if that notify fails. One-time-reveal: the raw `code` only appears in this
  response's `share_text`, never again. **✅ Now wired** — see §5/§1 (Task 10 fix).
- **`ClassSessionViewSet.parent_join`** (POST `sessions/{id}/parent-join/`, `AllowAny` +
  `ParentJoinIPThrottle`) — a parent presents the `parent_token` their `ParentAccessCode`/`ParentToken`
  was issued, resolved via `resolve_parent_from_token()` **called directly in the view body**, not via
  `HasValidParentSessionToken` as a `permission_class` — DRF runs `check_permissions()` before
  `check_throttles()`, so gating via a permission class would let a bad-token guess skip
  `ParentJoinIPThrottle`'s counter entirely; resolving in the body keeps "throttle counts every attempt,
  valid or not" true. Only if the resolved student currently `has_access()` to the session's classroom,
  the session `is_joinable()`, and the student isn't `kicked_at` does the parent get an observer-role
  LiveKit token (`ParticipantRole.OBSERVER`, identity `parent-{student.id}` — deliberately distinct from
  the student's own so a simultaneously-connected parent+child never collide in the room). **Never
  creates a `SessionParticipant` row** — same reasoning `token()` already uses for skipping
  participant-row creation.
  - 🐛 **Confirmed bug, not yet fixed**: the code at both call sites (`generate_livekit_token(...,
    role=ParticipantRole.OBSERVER)` and the response's `"livekit_role": ParticipantRole.OBSERVER`)
    references `ParticipantRole.OBSERVER`. `livekit_utils.py`'s `ParticipantRole` class (§7) defines
    exactly `HOST`, `CO_HOST`, `STUDENT`, and `PARENT_OBSERVER` — there is **no `OBSERVER` attribute**.
    This raises `AttributeError: type object 'ParticipantRole' has no attribute 'OBSERVER'` on the very
    first real call to `parent_join()`, i.e. this endpoint is currently **broken end-to-end**, not just
    imprecisely typed. Fix is presumably `role=ParticipantRole.PARENT_OBSERVER` in both spots — confirm
    against the intended grant (§7's `_grants_for_role` already has a `PARENT_OBSERVER` branch with
    exactly the subscribe-only/hidden semantics `parent_join`'s own docstring describes) before shipping.
- `kick()` (§4) disconnects a kicked student's linked parent too, best-effort, via the same
  `parent-{student_id}` LiveKit identity convention — separately wrapped so a missing/already-gone parent
  connection never turns a successful student-kick into a 503.
- **`ReportCardViewSet`** (manage-tier for create/update) — `attendance_percent` is **never** accepted
  from the request body, always computed server-side via `compute_attendance_percent_bulk()` (§3 —
  deliberately built off this app's own `ClassSession`/`SessionParticipant` data, explicitly **not** the
  unrelated `message` app's `StudyRoomAttendance` self-check-in streak, per that function's own module
  comment — the two would silently disagree, and a report card built off the wrong one would contradict
  what the teacher sees in their own classroom's attendance log). `homework_completion_percent`/
  `average_marks` are computed by `_homework_stats()` — a teacher only ever supplies `period_label` +
  `teacher_remark`. Publishing/updating a card best-effort parent-pushes every `ParentAccessCode` linked
  to that student via `send_parent_push`. **✅ Now wired**, and `StudentReportCard` (§3) is now a real
  model — see §1/§5 (Task 10 fix).
  - 🐛 **Two confirmed bugs in `_homework_stats()`, both currently make report-card creation crash or
    lie**: (1) it filters `Assignment.objects.filter(classroom=classroom, category=Assignment.Category.
    HOMEWORK)` — but `Assignment` (§3) has **no `category` field and no `Category` enum at all** (see the
    model's full field list). This raises `AttributeError` on the very first `POST report-cards/` for
    any classroom, every time — not a lookup that returns zero rows, a hard crash. (2) Even once that's
    fixed, this method reads the **local, legacy `liveclass.Assignment`/`AssignmentSubmission` models
    directly** (§3) — but per Task 12 (§6d), new assignments no longer get created there at all; they go
    through the unified `assignment` app via `bridge.create_assignment()`. Left as-is, a report card's
    homework numbers would be correct only for assignments created *before* the Task 12 cutover and
    silently blind to every assignment posted after it — a report card that looks complete but is
    quietly stale. Fixing bug (1) alone would hide bug (2), since a working-but-wrong query stops
    throwing and starts just returning `0`/`None` for every classroom's post-cutover homework. Whoever
    fixes this should route `_homework_stats()` through `bridge.get_assignment_submissions(classroom)`
    (§6d) instead, the same source `AssignmentViewSet`/`AssignmentSubmissionViewSet` (`views.py`) already
    use post-Task-12.
- **`ClassroomParentQueryListView`** / **`ParentQueryReplyView`** — teacher side of parent-initiated query
  threads (`ParentModeQuery`/`ParentModeQueryMessage`, `message` app models — deliberately a
  **different**, token-scoped-to-Classroom model from the existing `ClassQuery`/`ParentTeacherMessage`
  models already in this app, left untouched). Reply sets `status=answered`, or `status=closed` if the
  teacher explicitly passes `{"close": true}`, and best-effort parent-pushes the reply. **✅ Now wired** —
  see §5/§1 (Task 10 fix).
- **`HasValidParentSessionToken`** (`permissions.py`, additive snippet) — reads an `X-Parent-Token`
  header, resolves it via the same `resolve_parent_from_token()`. On success attaches
  `request.parent_access_code` / `request.parent_student` (never touches `request.user` — a parent has no
  `login.User` row). Mirrors the `message` app's own `HasValidParentToken` contract exactly (same
  attribute names), but is its own class here since `liveclass` never imports `message.models` directly.
  **Scope limit called out in its own docstring**: proves the token belongs to *some* legitimate parent,
  platform-wide — does **not** check `request.parent_student` has access to whatever classroom/session/
  report the view is about; every view using it must add that second check itself. **Still not actually
  used as a `permission_class` anywhere in this app's uploaded files** — `parent_join()` resolves the
  token manually in its body instead (throttle-ordering reason above), and the teacher-side
  `parent_link_views.py` views are all `IsAuthenticated` + `_can_manage_classroom` (the teacher is the
  caller, not the parent). Likely intended for a parent-facing *read* endpoint (e.g. a parent fetching
  their own linked student's report card/query thread) that isn't part of this app's uploaded file set —
  `message/views_parent.py` is where that read path currently lives (see the `StudentReportCard` note in
  §3), outside this audit's scope.

### Dead code left behind by the merge — flagged for cleanup, not deleted blind

`parent_join()` no longer needs a bespoke signed token, so three pieces are now unused but were left in
place because this pass's visibility couldn't confirm nothing else in the codebase imports them:

- `PARENT_JOIN_TOKEN_SALT` / `generate_parent_join_token(student)` (`views.py`) — the old
  `django.core.signing`-based token minter. Docstring now says `DEPRECATED` in the code itself.
- `ParentJoinSerializer` (`serializers.py`, §20b) — the old validator for that signed token, also marked
  `DEPRECATED` in its own module comment.

**Recommended follow-up** (called out in the code's own comments): grep the rest of the codebase for
`generate_parent_join_token`, `PARENT_JOIN_TOKEN_SALT`, and `ParentJoinSerializer` project-wide, and
delete all three once confirmed unused — so a future reader doesn't mistake any of them for a second,
still-live parent-auth path.

### Remaining gap

1. **`ParentJoinIPThrottle`'s scope (`session_parent_join_ip`) still needs its `DEFAULT_THROTTLE_RATES`
   entry cross-checked** — same recurring bug class as every other throttle scope in this app, see
   §15/§17 item 2b; not confirmed present in the uploaded `settings.py` excerpt. This is the only item
   left open from the original 4-item gap list — the other three (urls.py wiring, `StudentReportCard`
   model, and the two-mechanism split) are all resolved as of this audit.

---

## 6d. `liveclass` ↔ `assignment` bridge (Task 12) — **NEW**

Same shape as §6b/§6c: a project-wide feature (here, a **unified cross-app `assignment` app**, shared
with at least the `campus` app — Task 11's `campus/bridge.py` is its sibling/reference implementation)
gets exactly one door into `liveclass`, and `liveclass`'s own local `Assignment`/`AssignmentSubmission`
models (§3) step back to read-only history.

**The rule (same golden rule `campus/bridge.py` and `assignment/models.py`'s own docstring both state):**
`assignment` never imports `liveclass.*`. `liveclass` never imports `assignment.models.Assignment` /
`AssignmentSubmission` directly anywhere **outside `bridge.py`** — with one deliberate, audited, one-off
exception: `migrate_liveclass_assignments_to_unified` (below) talks to `assignment.models` directly,
because it needs parameters (`submitted_at`, `file`, `score`, `feedback`, `graded_at` per student) that
the always-fresh-roster `bridge.py` functions have no slot for.

### `bridge.py` — the two functions

- **`create_assignment(*, classroom, posted_by, title, description="", attachment=None, due_date=None)`**
  — delegates to `assignment.bridge.create_context_assignment(source=AssignmentSource.LIVECLASS,
  context_type="classroom", context_id=classroom.id, ..., roster=[...])`. `liveclass`-side code (this
  bridge + `views.py`'s thin proxy) is the only thing that ever turns `context_id` back into a real
  `Classroom` — `assignment` stores it opaquely.
  - **Roster source**: `PassPurchase(status=SUCCESS, is_active=True, expires_at__gt=now)` against the
    classroom's passes — the **exact same filter** `AssignmentViewSet.perform_create` (old
    `liveclass/views.py`) already used to fan out `ASSIGNMENT_POSTED` notifications, not a new query.
    `SessionParticipant` was considered and **rejected** as the roster source: it's per-`ClassSession`
    attendance, but an `Assignment` here is classroom-scoped (no per-session `context_type` on the
    unified model — it's exactly `"section" | "classroom" | ""`), so session-level would be the wrong
    grain.
  - **Paid/unpaid is never asked, structurally** — the unified `assignment.models.Assignment` has no
    `is_paid`/`price` field at all, same as `campus.CampusLiveSession`. This satisfies the "paid/unpaid
    kabhi nahi poocha jaata" requirement by construction, not by a check added here.
  - **`due_date` gotcha**: the unified `Assignment.due_date` is a `DateField`; the old local
    `liveclass.Assignment.due_date` was a `DateTimeField`. `create_assignment()` does **not** silently
    `.date()` a datetime passed in — a caller passing a full `datetime` gets whatever `assignment`'s own
    field validation does with it, so a time-of-day component isn't dropped without a trace.
    `views.py`'s thin proxy owns deciding what "due date" means going forward and must pass a `date`.
  - **⚠️ Open assumption, flagged not guessed around**: `assignment.bridge.create_context_assignment()`'s
    `roster` parameter shape was only ever confirmed via `campus/bridge.py`'s call site, which sends
    `{"user_id", "roll_number", "enrollment_no"}` per entry — `liveclass` has no roll-number/
    enrollment-number concept, so its roster entries are `{"user_id": student_id}` only. This assumes
    `create_context_assignment()` treats a missing key the same way `campus`'s own docs already note for
    a blank `enrollment_no` (defaults to `""`, not `KeyError`). **Needed to close this**:
    `assignment/bridge.py` itself (not part of this upload — verify before this ships).
- **`get_assignment_submissions(classroom)`** — delegates to `assignment.bridge.
  get_submissions_for_context(context_type="classroom", context_id=classroom.id)`. **Unfiltered by
  permission**, same contract `campus.bridge.get_assignment_submissions()` documents — the caller
  (`views.py`) owns any further teacher/student-scoped narrowing (mirror the old
  `AssignmentSubmissionViewSet.get_queryset`'s `_can_manage_classroom` vs. `student=user` split).

### `migrate_liveclass_assignments_to_unified` (management command)

One-off **data** migration (no `makemigrations` involved) — backfills every historical
`liveclass.Assignment`/`AssignmentSubmission` row into the unified `assignment` app so old assignments
survive the cutover. Deliberately does **not** reuse `bridge.create_assignment()` (that always
bulk-pre-creates fresh `MISSING` rows for the *current* roster — wrong for a backfill, which needs each
student's *real* historical `submitted_at`/`file`/`score`/`feedback`/`graded_at`).

- **Roster for a backfilled assignment** = whoever actually submitted historically **UNION** whoever
  currently holds an active pass — so a student who submitted but has since let their pass lapse still
  keeps their real submission, and a current student with no historical submission still gets a
  `MISSING` placeholder (matching what `bridge.create_assignment()` would produce for them going
  forward).
- **Idempotent**: every migrated `Assignment` is stamped with `data["legacy_liveclass_assignment_id"] =
  str(old.id)`; re-running skips any old assignment whose new row already has that stamp (a Postgres
  `jsonb` lookup, `data__legacy_liveclass_assignment_id` — matches the `GinIndex`-on-Postgres assumption
  already made elsewhere in `models.py`). Submissions are additionally protected via
  `bulk_create(..., ignore_conflicts=True)` against `unique_submission_per_student`.
- **Free-text `grade` field**: `f"{old_sub.score}/{old.max_score}"` — the unified model has no separate
  "max_score" slot, so this is how the original score *and its scale* both survive the migration.
- **Rollback**: every successful migration appends one JSON line (`old_assignment_id`,
  `new_assignment_id`, `old_submission_ids`, `new_submission_ids`) to `--log-file` (default
  `liveclass_assignment_migration_log.jsonl`); `--rollback <path>` reads it back and deletes exactly
  those new rows (submissions first, then assignments — FK order).
- **Usage**: `python manage.py migrate_liveclass_assignments_to_unified [--dry-run]
  [--log-file PATH] [--rollback LOG_FILE]`.

---

## 6e. `liveclass` ↔ `user_profile` coin bridge (Task 6) — **NEW**

**Coin top-up and withdrawal management has moved to the `user_profile` app.** `liveclass.CoinPurchase`
and `liveclass.CoinWithdrawal` (§3) are now **read-only, history-only models** — every write method on
both (`CoinPurchase.mark_success`/`mark_failed`; `CoinWithdrawal.create_request`/`approve`/`reject`/
`cancel`/`mark_paid`/`_refund_coins`) raises `RuntimeError` naming the `user_profile` equivalent to call
instead. `CoinPurchaseViewSet.initiate`/`verify`/`retry` and `CoinWithdrawalViewSet`'s create/cancel/
approve/reject/mark-paid actions (`views.py` — not part of this upload, verify directly) were turned
into 410-Gone stubs in the same pass. **`User.coin` itself, and `liveclass.CoinTransaction`, are
untouched by this** — the wallet balance and its append-only ledger stay exactly where they were; only
the *request/approval workflow* for topping up or cashing out moved apps.

Live write path now: `user_profile.CoinPurchaseRequest` (`start_purchase()` / `confirm_success()` /
`mark_failed()`) and `user_profile.CoinWithdrawalRequest` (`request_withdrawal()` / `mark_processing()`
/ `confirm_success()` / `reject()`), both writing through the shared `user_profile.CoinLedger.objects.
record_transaction()`.

### ✅ Confirmed directly in `views.py` (this upload)

Every retired action on `CoinPurchaseViewSet`/`CoinWithdrawalViewSet` now returns a **fixed HTTP 410
Gone** via a shared helper, `_deprecated_write_response(new_path)`:
```json
{"detail": "This action has moved. Coin purchases and withdrawals are now handled at <new_path> — this endpoint is read-only history going forward.", "new_endpoint": "<new_path>"}
```
- `coin-purchases/initiate/` → `/api/user-profile/coin-purchases/initiate/`
- `coin-purchases/{id}/verify/` → `/api/user-profile/coin-purchases/`
- `coin-purchases/{id}/retry/` → `/api/user-profile/coin-purchases/initiate/`
- `withdrawals/` POST (create), `withdrawals/{id}/cancel|approve|reject|mark-paid/` → all five point at
  `/api/user-profile/coin-withdrawals/`

`CoinWithdrawalViewSet`'s `create` is deliberately kept as a real `@action(detail=False,
methods=["post"])` (not deleted) purely so the route itself stays registered — an old client POSTing
`withdrawals/` gets this clear 410 + redirect instead of a bare 404. `list`/`retrieve` on both viewsets
stay real `Model`/`GenericViewSet` mixins reading straight off the local (frozen) models — no change
needed there for history to keep working.

`_create_gateway_order()`/`_verify_gateway_signature()` (Razorpay order-create + HMAC-signature verify)
are **no longer called from `views.py`** now that `initiate`/`verify`/`retry` are all 410 stubs — left in
place, not deleted, because `tasks.reconcile_stuck_coin_purchases` (referenced by name, not part of this
upload) may still import and use them against pre-migration `PENDING` rows. Confirm that task's own
current status (still scheduled? repointed at `user_profile`? decommissioned?) before deleting either
function.

⚠️ **`urls.py`'s own module-docstring endpoint reference was not updated for Task 6 or Task 12** — it
still describes `coin-purchases/initiate/`, `withdrawals/` POST/cancel/approve/reject/mark-paid, and the
full assignments/submissions CRUD as if they were live, ordinary endpoints. Per this audit's direct read
of `views.py`, all of the coin ones are 410 Gone and most of the assignment ones don't exist at all (§5,
§6d). Treat `urls.py`'s docstring as aspirational/historical wherever it disagrees with what a ViewSet's
own methods actually do — this doc's §5 has been corrected against `views.py`, not against that
docstring.

### `migrate_liveclass_coin_models` (management command)

One-off backfill of `liveclass.CoinPurchase`/`CoinWithdrawal` **history** into `user_profile`'s
`CoinPurchaseRequest`/`CoinWithdrawalRequest` + `CoinLedger`, so `user_profile` becomes the single place
this history lives going forward.

- **Never touches `User.coin`** — the balance already reflects every historical purchase/withdrawal.
  This command only writes audit-trail rows that *describe* that history; it deliberately bypasses
  `user_profile`'s live-write managers (`start_purchase`/`confirm_success`/`request_withdrawal`, etc.)
  because those apply a balance delta as a side effect of the call — reusing them here would re-debit/
  re-credit coins that already moved once. Every row is written with a direct `.objects.create()`.
- **`balance_after` comes from `liveclass.CoinTransaction`**, not reconstructed: `CoinTransaction`
  already snapshotted the real wallet balance at the moment of every purchase (`reason=TOPUP,
  reference_id=f"coinpurchase:{id}"`), withdrawal-request (`reason=WITHDRAWAL,
  reference_id=f"withdrawal:{id}"`), and withdrawal-refund (`reason=WITHDRAWAL_REVERSED`, same
  reference) event. A source row whose matching `CoinTransaction` is missing (a pre-existing data bug)
  is logged and **skipped**, not guessed — the whole batch still completes via per-row savepoints.
- **Status mapping** (`liveclass.CoinWithdrawal.Status` → `user_profile.CoinWithdrawalRequest.Status`):
  `PENDING→PENDING`, `APPROVED→PROCESSING`, `PAID→SUCCESS`, `REJECTED→REJECTED`,
  `CANCELLED→REJECTED` — the new model has no separate terminal state for "user cancelled it
  themselves" vs. "admin rejected it"; that distinction survives in `failure_reason`/`description`
  instead of a status value.
- **Idempotent**: purchases keyed on `CoinPurchaseRequest.gateway_reference` (= `CoinPurchase.order_id`);
  withdrawals keyed on `CoinLedger.reference == f"liveclass_coin_withdrawal_debit:{id}"`.
- **⚠️ Documented limitation, not silently papered over**: if a `CoinWithdrawal` is migrated while still
  `PENDING`/`APPROVED` and *later* resolves in `liveclass` (shouldn't happen once `views.py`/`models.py`
  are read-only, but could if this command runs **before** that deploy lands), re-running will **not**
  retroactively add the missing refund/completion side — the "already migrated" check is per-withdrawal,
  not per-state. **Run this command only after the read-only deploy**, once no in-flight `liveclass`
  withdrawal can change state again.
- **Rollback log**: one JSON line per row created, appended to `--rollback-log`
  (default `migrate_liveclass_coin_models.rollback.jsonl`); rollback = delete every logged
  `user_profile.CoinLedger`/`CoinPurchaseRequest`/`CoinWithdrawalRequest` row by id — none of them ever
  touched `User.coin`, so no balance repair is needed either.
- **Usage**: `python manage.py migrate_liveclass_coin_models [--dry-run] [--rollback-log PATH]`.

---

## 7. LiveKit (video) integration (`livekit_utils.py`)

- `_check_credentials()` (75) / `_check_egress_credentials()` (117) — fail-fast config checks.
- `LiveKitError(APIException)` (138) — **fixed from being a bare `Exception`** — now flows through `exceptions.py`'s handler and gets a proper `"code": "livekit_error"` instead of every `except LiveKitError` call site hand-building its own `Response(...)`.
- `ParticipantRole` (178), `_grants_for_role(role)` (187) — maps app-level role (host/moderator/participant) → LiveKit `VideoGrants`. Includes `ParticipantRole.PARENT_OBSERVER` (Task 8, Phase 2 "parent as one-to-one live-session observer") — the **most restricted** grant in the file: `can_publish=False` (no camera/mic, can never be seen/heard), `can_publish_data=False` (no chat/data-channel), `can_update_own_metadata=False`, `room_admin=False`/`room_record=False` (no moderation power), `hidden=True` (excluded from the room's own participant list/tiles — the teacher/student see a normal class, not an extra tile per watching parent; same LiveKit mechanism used for recording bots). Issued via the `core.classroom_chat_bridge.resolve_parent_from_token()` / `HasValidParentSessionToken` path — see §6c.
- `generate_livekit_token(...)` (201) — issued via `ClassSessionViewSet.token` action.
- `_client()` (233) — LiveKit API client factory.
- `verify_webhook_event(body: bytes, auth_header: str)` (408) — validates inbound LiveKit webhooks (consumed by `LiveKitWebhookView`; also feeds `egress_ended` → conditionally queues `tasks.transcribe_recording.delay()` when `Classroom.captions_enabled`).
- `end_room` — imported by `signals.py` for LiveKit room teardown on session end (best-effort, logged and swallowed on failure — DB state is the source of truth, LiveKit is a downstream effect kept best-effort in sync).

---

## 8. Chunked upload pipeline (`chunked_upload_views.py` + `ChunkedUpload` model)

Flow: `init` → many `chunk` calls → `complete` (assembles + validates + finalizes) → or `abort` at any point.

- `_tmp_root()` (95), `_upload_tmp_dir(upload_id)` (107), `_safe_extension(file_name)` (111), `_error(message, code=400)` (119) — filesystem/response safety helpers.
- `chunked_upload_init(request)` (130) — creates a `ChunkedUpload` row; enforces `MAX_IN_PROGRESS_UPLOADS_PER_USER` (indexed on `(user, status)`).
- `_validate_and_build_extra_data(purpose, data, user)` (207) — purpose-specific validation. `purpose` drives what `extra_data` JSON must contain: `material` needs `classroom_id`/`material_type`/optional `session_id`; `assignment_attachment`/`submission_file` need `assignment_id`.
- `chunked_upload_chunk(request)` (309) — appends one chunk to the temp file.
- `chunked_upload_complete(request)` (368) → `_assemble_chunks(upload)` (431) → `_finalize_purpose(upload, final_path, final_size, request)` (461, routes to the right model field based on `purpose`) → `_assert_is_real_image(path)` (572, Pillow-verifies anything claiming to be an image — same anti-spoofing logic as the model-level `ImageField`).
- `chunked_upload_abort(request)` (597) / `_cleanup_tmp_dir(upload_id)` (585) — cleanup on cancel.
- `_AssemblyError(Exception)` (421) / `_FinalizeError(Exception)` (425) — purpose-built exceptions so failures map to clean 4xx responses instead of bare 500s.
- Stale/abandoned uploads (client crashed mid-upload) swept by `tasks.cleanup_stale_chunked_uploads` — 6h staleness window, hourly sweep.

---

## 9. Celery tasks (`tasks.py`, ~1845 lines) — what runs on its own, and how often

App bootstrap is `LearnScroll/celery.py` (project-level — see §1 file map): builds the `Celery("LearnScroll")` instance, loads `CELERY_*` settings, `autodiscover_tasks()`s `liveclass/tasks.py` automatically. Two separate long-running processes required in production, neither optional: `celery -A LearnScroll worker` (executes tasks) and `celery -A LearnScroll beat` (fires periodic ones on schedule) — a worker with no beat means nothing self-triggers, beat with no worker means tasks queue up but never run.

### Registered in `settings.py` → `CELERY_BEAT_SCHEDULE` (source of truth for cadence — cross-check here if timings ever change)

| Task | Cron | What it fixes / does |
|---|---|---|
| `liveclass.generate_upcoming_sessions` | `crontab(minute=0)` — hourly | Turns `ClassSchedule` recurrence rules into real `ClassSession` rows, 14 days ahead. Fully idempotent, safe to re-run at any frequency. |
| `liveclass.auto_complete_overdue_sessions` | `crontab(minute="*/5")` | A session a teacher forgot to `/end/` gets force-completed (tears down LiveKit room, finalizes attendance) instead of staying LIVE forever. |
| `liveclass.send_due_reminders` | `crontab(minute="*")` | Flips `ClassReminder.is_sent` and sends it — minute-precision `remind_at`, so needs to run every minute; query is cheap (indexed on `is_sent`/`remind_at`). |
| `liveclass.refresh_stale_enrolled_counts` | `crontab(minute="*/15")` | Without this, `Classroom.enrolled_count` only ever goes **up** — nothing else decrements it when a pass ages out with no renewal. Recomputes for classrooms with recently-expired passes. Interval matches `REFRESH_ENROLLED_COUNT_LOOKBACK_MINUTES` (60min) with a shorter run cadence than the lookback, so a slow tick can't let a batch fall in the gap. |
| `liveclass.expire_and_refund_passes` | `crontab(minute="*/15")` | The per-day escrow design already stops a quiet classroom draining a pass all at once, but nothing called `reverse()` for what's **left** in escrow once `expires_at` passed with no manual cancel — this auto-refunds it (was previously a permanent-loss bug). Interval matches `EXPIRE_REFUND_LOOKBACK_MINUTES` (60min), same shorter-cadence-than-lookback reasoning. |
| `liveclass.reconcile_stuck_coin_purchases` | `crontab(minute="*/30")` | A `CoinPurchase` stuck PENDING (client crashed before `/verify/`, webhook lost) becomes retry-able again. Timeout: `COIN_PURCHASE_PENDING_TIMEOUT` (2h). **Was previously written but never registered here** — stuck top-ups sat invisible forever until this entry was added. |
| `liveclass.run_auto_renewals` | `crontab(minute="*/30")` | Sweeps `PassPurchase.auto_renew=True` rows past `expires_at`, chains a new purchase via `renew()`. Self-cleaning (renew() always clears `auto_renew` on the row it processes, success or fail) — short frequent cadence is safe. **Also previously written-but-unregistered.** |
| `liveclass.expire_unclaimed_gifts` | `crontab(minute="*/30")` | Refunds `PassGift`s past `CLAIM_WINDOW_DAYS` (7d) unclaimed. Same self-cleaning-query reasoning as auto-renewals. **Also previously unregistered.** |
| `liveclass.send_notification_digests` | `crontab(minute=0)` — hourly | Reads `NotificationPreference.digest_frequency`/`last_digest_sent_at` and batches a daily/weekly digest email for anyone due. Hourly keeps it close to on-time; cheap no-op for anyone not due yet. |
| `liveclass.cleanup_stale_chunked_uploads` | `crontab(minute=0)` — hourly | Sweeps abandoned chunked uploads (6h staleness window) + reclaims temp disk. |
| `message.send_scheduled_messages` (other app, same beat) | `crontab(minute="*")` | Minute-precision `scheduled_for`; cheap indexed query, bounded 200/run batch. |
| `message.cleanup_expired_messages` (other app, same beat) | `crontab(minute="*/15")` | Shortest disappearing-message duration is 1 month, so a 15-min sweep lag is invisible to users. |

**Not currently in `CELERY_BEAT_SCHEDULE`** (triggered another way, or worth double-checking if a captions/transcript bug is ever reported): `transcribe_recording` (queued via `.delay()` from `LiveKitWebhookView`'s `egress_ended` handler when `Classroom.captions_enabled`), `poll_transcription_jobs` (polls transcription job status — check whether this is chained/queued elsewhere or genuinely needs a beat entry).

### Every `notify_*` fire-and-forget task (queued via `.delay()`, never called synchronously in the request path)
`notify_waitlist_promotion(student_id, session_id)`, `notify_classroom_shared(share_id)`, `notify_purchase_refunded(purchase_id)`, `notify_pass_auto_renewed(purchase_id)`, `notify_auto_renew_failed(purchase_id)`, `notify_gift_expired(gift_id)`, `notify_pass_gift_received(gift_id)`, `notify_pass_gift_claimed(gift_id)`, `notify_classroom_flagged(classroom_id)`, `notify_session_auto_completed(session_id)`, `notify_join_request_received(join_request_id)`, `notify_join_request_accepted(join_request_id)`, `notify_join_request_rejected(join_request_id)`, `notify_assignment_graded(submission_id)`, `notify_certificate_issued(certificate_id)`, `notify_notice_posted(notice_id, student_ids)`, `notify_session_live(session_id, exclude_user_id=None)`, `notify_session_cancelled(classroom_id, classroom_title, session_id, scheduled_start_iso, exclude_user_id=None)`, `notify_assignment_posted(assignment_id, student_ids)`, `notify_submission_received(submission_id)`, `notify_staff_added(staff_id)`, `notify_review_posted(review_id)`, `notify_report_reviewed(report_id)`, `notify_query_answered(query_id)`, **`notify_followers_new_classroom(classroom_id)` (TASK 4, NEW)**.

#### `notify_followers_new_classroom(classroom_id)` (TASK 4, NEW — line ~1791)

Enqueued via `_safe_delay(notify_followers_new_classroom, classroom.id)`
from `ClassroomViewSet.perform_create()` (§4) — the same `_safe_delay`
wrapper every other `notify_*` call site in `views.py` already uses,
not a special case. Notifies every `ACCEPTED` follower
(`user_profile.models.Follow`) of the classroom's teacher that a new
classroom was just created, excluding any follower who has restricted
the teacher (`user_profile.models.RestrictUser` — one bulk exclusion
query, same shape `post.tasks.notify_followers_new_post` and
`testseries.tasks.notify_followers_new_testseries` already use for
their own identical feature in their own apps). Re-fetches the
`Classroom` itself (handles it having been deleted/closed between
enqueue and run — logs and returns `False`, no error). Uses
`core.services.create_bulk_notifications()` — one bulk INSERT, not a
per-follower loop — with `Notification.NotifType.
CLASSROOM_CREATED_BY_FOLLOWED` and a `classroom=classroom` kwarg so a
client can deep-link straight into the new classroom from the
notification row. **Both the enum member and the `classroom` FK on
`Notification` are unconfirmed this pass** — see §3's Notification
section for the full caveat.

No `CELERY_BEAT_SCHEDULE` entry needed — like `transcribe_recording`
and every other enqueue-only task in this file, it only ever runs
triggered, never on a schedule.

### Everything else
`build_engagement_report(session_id)` — heavier analytics computation offloaded from the request/response cycle (backs `sessions/{id}/engagement-report/`). `_dates_for_schedule(schedule, window_start, window_end)` (96) — recurrence-rule expansion helper for `generate_upcoming_sessions`. `_chunked_upload_dir_size(path)` (1563). `transcribe_recording(session_id)` (1574), `poll_transcription_jobs()` (1646) — recording → transcript pipeline (uses `GEMINI_API_KEY`).

---

## 10. Notifications (`notifications.py`) — channel status

Single fan-out function: `send_notification(user, title, message, channel="push", data=None) -> bool` — **never raises**; unconfigured/failed channel = logged warning + `False` return, never a broken transaction. `data` (push-only, ignored by other channels): merge in a `type` key (e.g. `"class_reminder"`, `"waitlist_seat_open"`) + deep-link ids so the Flutter client routes it the same way it routes chat/call pushes.

| Channel | Status | Implementation |
|---|---|---|
| push | ✅ wired | Reuses **existing** `message.push_utils.send_push_to_users` (Firebase). `liveclass` does **not** init its own Firebase app — `message`'s module already guards double-init via `if not firebase_admin._apps`. Lazy-imported inside `_send_push` so a missing/renamed `message` module degrades to a logged warning (liveclass still works, push just no-ops) instead of breaking Django app-loading (this import chain runs very early: `signals.py` → `tasks.py` → this module). `send_push_to_users` doesn't return a per-call success count — returning `True` here means "handed off to the pipeline", not "confirmed delivered". |
| email | ✅ wired | Plain `send_mail()`, existing SMTP settings. No-ops (logged info) if the user has no email on file. |
| sms | ✅ wired | MSG91 plain HTTP API (`https://control.msg91.com/api/v5/flow/`), India-focused, no SDK dependency (just `requests`). Needs `MSG91_AUTH_KEY` + `MSG91_SMS_SENDER_ID` (DLT-registered 6-char sender id, mandatory for Indian transactional SMS). No-ops + logged warning if unconfigured. **Implementation note**: uses MSG91's `v5/flow` endpoint (needs a pre-registered Flow template) — swap to their legacy `/api/sendhttp.php` if the account isn't set up with a Flow template; only this one function needs to change. |
| whatsapp | ✅ wired | MSG91 WhatsApp Business API (`.../whatsapp-outbound-message/bulk/`). **WhatsApp platform rule, not an MSG91 limitation**: business-initiated messages outside a 24h user-initiated window **must** use a pre-approved template (`message` is passed as the template's single body variable — a multi-placeholder template needs `to_and_components` restructured). Needs `MSG91_AUTH_KEY` (shared with SMS) + `MSG91_WHATSAPP_INTEGRATED_NUMBER` + `MSG91_WHATSAPP_TEMPLATE_NAME`. No-ops + logged warning if any missing. |

`_phone_for(user) -> str | None` — single shared `phone_number`/`mobile` attribute fallback lookup, used by **both** SMS and WhatsApp, so the two channels can never silently drift on which field they trust (previously `_send_sms` had this inline and a hypothetical new `_send_whatsapp` would've had to duplicate it).

`_SENDERS = {"push": _send_push, "email": _send_email, "sms": _send_sms, "whatsapp": _send_whatsapp}` — dispatch table; unknown channel = logged warning + `False`.

---

## 11. Moderation (`moderation.py`)

`screen_message(text) -> (is_flagged: bool, reason: str)` — fast, dependency-free, **never raises** (fails safe = unflagged; try/except wraps the whole body). Runs **after** a `ChatMessage` is saved — a false positive must never block a real send, it only ever adds to a moderator review queue (per `ChatMessageViewSet.perform_create`'s contract).

**What it is**: a first-pass filter to keep a moderator's flagged-chat queue non-empty on day one. **What it is not**: a substitute for a real moderation service — will miss creative evasions beyond basic leetspeak, new slang, non-English profanity outside the configured word list, and will occasionally false-positive (cheap, by design). Swap `screen_message()`'s body for a real moderation API (Perspective API, AWS Comprehend, an LLM endpoint) for production-grade coverage — the `(is_flagged, reason)` contract is deliberately provider-agnostic.

**Detection, in order:**
1. **Profanity** — `DEFAULT_PROFANITY_WORDS` (English + Hindi/Hinglish slurs/profanity), overridable via `settings.LIVECLASS_PROFANITY_WORDS` (so the list can be tuned per-market/language without a code deploy). Whole-word boundary match only (`\bword\b`) — avoids the "Scunthorpe problem" (innocent word containing a bad substring). Basic leetspeak normalization first (`_LEET_MAP`: `0→o,1→i,3→e,4→a,5→s,7→t,@→a,$→s`) — catches `sh1t`/`fu(k`-style swaps, deliberately does *not* try to catch `fuuuck`/`f u c k`/`f*ck` (fuzzier matching a lightweight filter shouldn't own).
2. **Spam link** — any `http(s)://` or `www.` (`_URL_RE`).
3. **Spam contact info** — phone number regex (`_PHONE_RE`, loose international-ish 9-13 digit) combined with a contact-app mention (`_CONTACT_APP_RE`: whatsapp/telegram/instagram/snapchat) is a **much stronger signal together** than either alone (a student innocently asking "does anyone use WhatsApp?" isn't flagged just for naming the app) — but a bare phone number alone is still flagged regardless.
4. **Repeated chars** — `(.)\1{5,}` (e.g. "aaaaaaa"/"!!!!!!!").
5. **Repeated words** — same word 4+ times in a row.
6. **Wall-of-caps** — only for messages with ≥12 letters and >80% uppercase (short caps like "OK"/"LOL" are normal chat, not flagged).

**Why the abuse pattern here is different from generic spam**: a live-class chat is a captive audience — the recurring problem isn't "advertising a product", it's students/outsiders routing people **off-platform** (another tutor's WhatsApp/Telegram, a bare phone number, a "DM me for cheaper classes" pitch) or flooding the room.

---

## 12. Error handling (`exceptions.py`)

Every API error, regardless of source, normalizes via `liveclass_exception_handler(exc, context)` (wired at `REST_FRAMEWORK["EXCEPTION_HANDLER"]`) to:
```json
{"detail": "Human-readable message.", "code": "validation_error", "errors": {"field": ["..."]}}
```
`errors` key only present for field-level validation errors.

**Why this exists**: before this handler, different failure paths returned different JSON shapes — `PermissionDenied()` → `{"detail": "..."}`, `ValidationError("string")` → a **bare list** `["..."]`, `ValidationError({"field": "..."})` → `{"field": ["..."]}`, an unhandled `IntegrityError` → DRF's generic 500 (shape depends on `DEBUG`). The Flutter client had to special-case every shape just to show one error message.

**How it works:**
- `_CODE_BY_EXC` — maps exception class → stable machine-matchable `code` string (`validation_error`, `permission_denied`, `not_found`, `authentication_failed`, `not_authenticated`, `throttled`, `method_not_allowed`, `parse_error`, `livekit_error`). `_code_for(exc)` looks this up, defaults to `"error"`.
- `_first_message(value, field_name=None)` — **recurses** through dict/list nesting of arbitrary depth to find the first human-readable leaf message. **Fixed bug**: this used to be a flat `data[first_field][0]` index, which broke (`KeyError`/`TypeError`, *inside the exception handler itself* → raw unhandled 500) on a nested writable serializer field (`{"user": {"email": [...]}}`) or a `many=True` `ListSerializer` (`[{}, {"title": [...]}]`). `non_field_errors` (DRF's internal bookkeeping name) is never prefixed onto the message; every other field name is kept as useful context (`"email: This field is required."`).
- `liveclass_exception_handler(exc, context)` — translates Django's raw `Http404`/`PermissionDenied` to DRF equivalents first, then calls `drf_default_handler`. If that returns `None` (not a DRF-recognised exception): an `IntegrityError` gets logged (with the view's class name, traceable) and converted to a clean `409 {"detail": "...", "code": "conflict"}` instead of a bare 500 — the intended fix is to add an explicit pre-check for whatever constraint it was, not to rely on this fallback long-term. Anything else truly unexpected (a bug) is left to propagate so Django's own error reporting (logging/Sentry) still fires.
- `LiveKitError` is registered here as a proper DRF `APIException` (fixed from a bare `Exception` — see §7) — `code: "livekit_error"`.

---

## 13. Signals (`signals.py`, loaded via `apps.py`)

Purpose: side-effects that must fire **regardless of which code path** changed the state — the `end` API action, Django admin, a management command, or `auto_complete_overdue_sessions`. Centralizing here means "session ended" or "a seat opened up" always does the right thing without every call site remembering to do it.

- **`stash_previous_session_status`** (`pre_save`, `ClassSession`) — stashes the pre-save status, since `post_save` doesn't receive the old row (standard Django diff-a-transition pattern).
- **`cleanup_on_session_end`** (`post_save`, `ClassSession`) — on a **fresh transition** into `COMPLETED`/`CANCELLED` (`_TERMINAL_STATUSES`): tears down the LiveKit room, closes any still-open polls, force-checks-out any still-"in room" participants, clears leftover waitlist entries (a seat opening up after the session is over is moot), stamps `actual_end` if unset.
- **`cleanup_on_session_delete`** (`post_delete`, `ClassSession`) — best-effort LiveKit room teardown if a session row is deleted outright (e.g. Django admin).
- **`stash_previous_left_at`** (`pre_save`, `SessionParticipant`) + **`on_participant_left`** (`post_save`) — on `left_at` transitioning `None → timestamp` (a student/host actually left):
  1. Promotes the next student off `SessionWaitlist` **FCFS**, via `.filter(notified=False).update(notified=True)` — a single conditional UPDATE, not read-then-save. **Fixed race**: two participants leaving near-simultaneously used to both read the same `notified=False` row before either saved, and could both promote the same student.
  2. Best-effort `+1` to `classes_attended` on the `PassPurchase` that granted access, **for capped ("N-class pack") passes only**. `SessionParticipant` has no FK back to the granting `PassPurchase`, so this re-derives "the currently valid capped purchase for this classroom" the same way `Classroom.has_access()` does — fine for the common case; if a student somehow holds >1 active capped purchase for the same classroom, credits the most recently purchased one. For exact tracking, add a `pass_purchase` FK to `SessionParticipant` at join time.

**Production-hardening design decisions baked into this file (important — don't undo these in a future edit):**
1. **Every step is its own try/except.** If a step here (a bad LiveKit response, a stale FK, a raising query) bubbled uncaught, since these signals fire **synchronously inside** `ClassSession.save()`/`SessionParticipant.save()`, it would raise out of the `.save()` call itself — the whole view/task (session end, `/join/`, `/leave/`, auto-complete, admin edit...) blows up mid-request with a 500, even though the actual state change the caller cared about was already valid. Isolating each step means one bad step is logged-and-skipped while everything else (and the request) completes normally.
2. **Everything not needed to make *this* `.save()` correct is deferred to `transaction.on_commit()`** — LiveKit calls, notification dispatch, cross-row cleanup. Two reasons: (a) if the enclosing request runs inside `transaction.atomic()`, code here runs *before* commit — a `.delay()`'d Celery task could start on a worker before the row it needs has actually landed in the DB; `on_commit()` guarantees the task is only queued once durable. (b) if the outer transaction rolls back for an unrelated reason, work already sent to LiveKit/Celery from inside the signal can't be undone — `on_commit()` ensures it never fires for a change that didn't stick.
3. **LiveKit failures are logged and swallowed, never raised** — a LiveKit-side hiccup must never prevent the DB from correctly recording that a session ended; DB is the source of truth, LiveKit room state is a best-effort downstream mirror.
4. **Waitlist "seat opened up" notification — fixed bug**: this used to import a `notify_waitlist_seat_open` from `liveclass.notifications` that **was never defined anywhere** — every promotion silently raised `ImportError`, swallowed by the try/except, logged-and-forgotten. No student was ever notified. Now correctly wired to two call sites from the same `on_commit()` callback: `create_notification(...)` (models.py, writes the bell-icon row — same helper every other notification uses) **and** `tasks.notify_waitlist_promotion.delay(...)` (queues the actual push, NOT called inline, so a slow/failing push provider never delays the `on_commit` callback itself) — mirrors the exact pattern `views._refund_purchase` uses for `notify_purchase_refunded.delay(...)`.

**NEW — classroom↔chat-group sync (tasks 29–40, see §6b for the full picture):** 6 additional receivers, same try/except-per-step + `transaction.on_commit()` discipline as everything above, all lazy-importing `core.classroom_chat_bridge` so this app stays importable even if that module/app isn't present: `sync_chat_group_on_join_accept` (`ClassJoinRequest` → `ACCEPTED`), the waitlist-promotion path inside `on_participant_left` (item 1 above), `sync_chat_group_on_staff_add` (`ClassroomStaff` created → promote to moderator), `sync_chat_group_on_classroom_change` (`Classroom` close/soft-delete → archive, else title/cover/description change → metadata sync — paired with a `stash_previous_classroom_snapshot` `pre_save`), `sync_chat_group_on_ban` (`ClassroomBan` created → remove, `reason="kick"`), `sync_chat_group_on_purchase_refund` (`PassPurchase` → `REFUNDED` → remove, `reason="refund"`, paired with `stash_previous_purchase_status` `pre_save`). **✅ Safe to run** — the `Classroom.chat_group_enabled`/`linked_conversation_id` fields these depend on are now defined in `models.py` (§1, §3, §6b), subject only to running the migration.

---

## 14. Admin (`admin.py`, 607 lines, 33 `ModelAdmin`s)

- A patch near the top adjusts whatever `ModelAdmin` Django/another app already registered for `User` (via `@admin.register(_User)`) — auto-detects if `User` is already registered (adds `search_fields` only if missing, without replacing the class) vs. registers a minimal fallback admin if it isn't registered at all yet, so every `autocomplete_fields = [..., "student"/"teacher"/...]` below has something to search regardless of registration order between apps.
- **Inlines**: `ClassScheduleInline`, `ClassPassInline`, `ClassroomStaffInline`, `PassDailyChargeInline` (`TabularInline`) — surfaced on their parent model's admin page (e.g. schedules/passes/staff nested under `ClassroomAdmin`; daily charges nested under `PassPurchaseAdmin`).
- **Registered `ModelAdmin`s** (one per model, list filters/search/bulk actions tuned per model): `ClassroomAdmin`, `ClassScheduleAdmin`, `ClassSessionAdmin`, `ClassPassAdmin`, `PassPurchaseAdmin`, `PassGiftAdmin`, `PassDailyChargeAdmin`, `ClassJoinRequestAdmin`, `BreakoutRoomAdmin`, `SessionParticipantAdmin`, `ClassMaterialAdmin`, `ChatMessageAdmin`, `ChatMessageReportAdmin`, `LivePollAdmin`, `PollResponseAdmin`, `AssignmentAdmin`, `AssignmentSubmissionAdmin`, `ClassroomReviewAdmin`, `ClassroomWishlistAdmin`, `CouponAdmin`, `CoinTransactionAdmin`, `CoinPurchaseAdmin`, `CoinWithdrawalAdmin`, `ClassroomStaffAdmin`, `SessionWaitlistAdmin`, `ClassroomReportAdmin`, `CertificateAdmin`, `ClassReminderAdmin`, `ClassHolidayAdmin`, `NoticeAdmin`, `ClassQueryAdmin`, `ReferralAdmin`, `ClassroomBanAdmin`. That's 33, **not** the 40+ this file previously claimed — see the correction below.
- **✅ Correction (was previously mis-documented here as `NotificationAdmin` still living in this file)**: `admin.py`'s own module comment (task 42, "core-app migration") confirms `Notification` now lives in `core/models.py`, and its admin registration moved to `core/admin.py` accordingly — importing a cross-app model into this file would have been the wrong direction (same "liveclass calls into other apps, not the reverse" invariant as §6b/§6c). `NotificationAdmin`/`NotificationPreferenceAdmin` are **not** in this file; look in `core/admin.py` (not uploaded/audited here) for them.
- **⚠️ Gap — imported but never registered**: `ParentMessageTemplate` and `ParentTeacherMessage` (task 69's structured parent/student→teacher messaging, §5 `parent-message-templates/`/`parent-messages/`) are both imported at the top of this file from `.models`, but neither has an `@admin.register(...)`/`ModelAdmin` class anywhere in it — the import is currently dead weight, and platform staff have no admin-panel visibility into either model. Same shape of gap as the historical `PassGift`/`ChatMessageReport`/`Referral`/`ClassroomBan` fixes already called out in this file's own comments (§17-style "existed since Pass N, no admin registration" pattern) — worth the same fix.
- Use this section as the map for "which model has admin visibility" when debugging data issues via Django admin rather than the API.

---

## 15. Settings highlights (`settings.py`, ~890 lines, **project-level** — shared with `login`/`message`)

- `AUTH_USER_MODEL = "login.User"` — custom user model lives in a separate `login` app.
- `SECRET_KEY`, `DEBUG` — env-driven (`os.getenv`).
- **Security headers** (prod-only, gated on `not DEBUG`): `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`, `SECURE_HSTS_SECONDS = 30 days`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SECURE_CONTENT_TYPE_NOSNIFF = True`, `X_FRAME_OPTIONS = "DENY"`.
- `SENTRY_DSN` — error tracking, env-driven.
- DB: `DATABASE_URL` env-driven (falls back to a local config block).
- Redis: `REDIS_URL` (or `CELERY_BROKER_URL` as fallback) drives both `CHANNEL_LAYERS` (Channels) and Celery broker/result backend.
- `TIME_ZONE = "Asia/Kolkata"`, `USE_TZ = True`, `LANGUAGE_CODE = "en-us"`.
- `MEDIA_URL`/`MEDIA_ROOT`, `STATIC_URL`/`STATIC_ROOT`, `STORAGES` (static via WhiteNoise).
- `CHUNKED_UPLOAD_TMP_ROOT = BASE_DIR / 'tmp' / 'chunked_uploads'`.
- `GEMINI_API_KEY` — powers `tasks.transcribe_recording`/`poll_transcription_jobs`.
- `REST_FRAMEWORK` block — includes `EXCEPTION_HANDLER = "liveclass.exceptions.liveclass_exception_handler"` (see §12).
  - `DEFAULT_AUTHENTICATION_CLASSES = ("rest_framework_simplejwt.authentication.JWTAuthentication",)`.
  - **`DEFAULT_PERMISSION_CLASSES = ["rest_framework.permissions.IsAuthenticated"]` — fix (defense in depth)**: every current ViewSet/APIView already sets its own `permission_classes` explicitly, so this doesn't change today's behavior. It matters for the *next* view added: DRF's own built-in default is `AllowAny`, so a future view that forgets to set `permission_classes` would silently be open to the public instead of failing closed. With this floor set, a forgotten `permission_classes` is a 401, not a leak.
  - **`DEFAULT_THROTTLE_RATES` — fix (CRITICAL, recurring bug class)**: every `ScopedRateThrottle`/custom throttle used anywhere in the project looks its rate up from this one dict by `scope` name (`DEFAULT_THROTTLE_RATES[self.scope]`) — a scope with no matching entry raises `ImproperlyConfigured` on the *very first request* that hits it, not a rare edge case. This has happened repeatedly as new throttled actions were added to `views.py` without a matching entry added here. Liveclass-relevant scopes and their rates:

    | Scope | Rate | Used by |
    |---|---|---|
    | `session_join` | 20/min | `ClassSessionViewSet.join` |
    | `session_token` | 30/min | `ClassSessionViewSet.token` |
    | `session_parent_join_ip` | **not confirmed in the uploaded `settings.py` excerpt** | `ParentJoinIPThrottle` (`throttles.py`) → `ClassSessionViewSet.parent_join` — IP-keyed, not user-keyed, since a parent has no `request.user` (see §6c); verify this entry exists before deploying `parent_join`, same recurring gap class as every row below |
    | `coupon_validate` | 20/min | `CouponViewSet.validate` |
    | `chat_message_create` | 20/min | `ChatMessageViewSet.create` |
    | `chat_reaction` | 60/min | `ChatMessageViewSet.react` |
    | `classroom_share` | 100/day | `ClassroomViewSet.share` — flat daily cap (product decision), not a burst cap; DRF's `ScopedRateThrottle` resets on a rolling 24h window per user, not a calendar-day boundary |
    | `chunked_upload_init` | 20/min | `chunked_upload_views.py` init |
    | `chunked_upload_chunk` | 180/min | `chunked_upload_views.py` chunk (one real upload fires this dozens of times) |
    | `chunked_upload_complete` | 20/min | `chunked_upload_views.py` complete |
    | `coin_withdrawal` | 10/min | `CoinWithdrawalViewSet` — money-movement, rated tighter |
    | `coin_purchase` | 10/min | `CoinPurchaseViewSet` — money-movement, rated tighter |

    (Also shares this same dict with several `message`-app-only scopes — `message_send`, `call_initiate`, `group_create`, `reaction`, `ai_transcribe`, `ai_smart_reply` — not liveclass's concern but worth knowing they live in the same table, same bug class if a new one is added there without a rate.) Plain (non-scoped) floor: `"user": "100/min"`, `"anon": "20/min"`.
- `SIMPLE_JWT` — JWT auth config (also what `ws_auth.py` validates against for WebSockets).
- **`CORS_ALLOWED_ORIGINS` / `CORS_ALLOW_ALL_ORIGINS` — fix (security)**: `CORS_ALLOW_ALL_ORIGINS=True` outright means any website can call this API using a logged-in user's browser session/cookies. Locked to an explicit allowlist read from `CORS_ALLOWED_ORIGINS` (env, comma-separated); `CORS_ALLOW_ALL_ORIGINS = DEBUG and not CORS_ALLOWED_ORIGINS` — allow-all only ever kicks in for local dev (`DEBUG=True`) with no allowlist configured, never in production.
- `SPECTACULAR_SETTINGS` — OpenAPI schema gen (dev-time, see `urls.py` §5).
- **`DATA_UPLOAD_MAX_MEMORY_SIZE = 10MB` — fix (DoS vector, was 500MB)**: this governs total size of *non-file* request data (plain text/JSON fields) — Django excludes actual uploaded file bytes from this check (those are bounded per-field instead by `MaxFileSizeValidator` in `models.py`). At the old 500MB setting, any endpoint taking a plain text/JSON field (a classroom description, a chat message, a review comment) would accept up to 500MB of non-file body before Django even rejected it — cheap to send, expensive to parse/hold in memory, a DoS lever with no file involved at all. 10MB is generous for anything this app's serializers actually accept as plain text. Also: `FILE_UPLOAD_MAX_MEMORY_SIZE = 500KB`, `FILE_UPLOAD_PERMISSIONS = 0o644`, `DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000`.
- `FCM_SERVICE_ACCOUNT_JSON_PATH` — push notifications (owned by the `message` app, reused here — see §10).
- `GOOGLE_CLIENT_ID`, `FREESOUND_API_KEY` — other-app config living in the same settings file.
- `REFERRAL_BONUS_COINS` (default 50), `REFERRAL_REDEEM_WINDOW_DAYS` (default 7) — signup-referral economy knobs (env-overridable).
- `CLASSROOM_REFERRAL_JOIN_BONUS_COINS` *(NEW — task 65)* — one-time platform-funded bonus paid to a student who joins a classroom via a referral link, see §3 "Classroom Refer & Earn". Not confirmed present in the uploaded `settings.py` excerpt this session — if it's genuinely absent, `_charge_and_create_purchase` would raise `AttributeError` the first time a referred join is charged (same bug class as the other missing-setting items in §17/this list), so worth a direct confirm.
- `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` — coin top-up payment gateway.
- `EMAIL_BACKEND`/`EMAIL_HOST`/`EMAIL_PORT`/`EMAIL_USE_TLS`/`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`/`DEFAULT_FROM_EMAIL` — SMTP.
- `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` (Redis), `CELERY_ACCEPT_CONTENT=["json"]`, `CELERY_TASK_SERIALIZER`/`CELERY_RESULT_SERIALIZER="json"`, `CELERY_TIMEZONE = TIME_ZONE`, `CELERY_TASK_ACKS_LATE = True`, `CELERY_TASK_REJECT_ON_WORKER_LOST = True`.
- `CELERY_BEAT_SCHEDULE` — **the actual source of truth for task cadence**, full table reproduced in §9. Includes both `liveclass.*` and `message.*` entries in one shared schedule dict.
- `MSG91_*` — SMS/WhatsApp provider config (see §10): `MSG91_AUTH_KEY`, `MSG91_SMS_SENDER_ID`, `MSG91_WHATSAPP_INTEGRATED_NUMBER`, `MSG91_WHATSAPP_TEMPLATE_NAME` — all env-driven via `os.environ.get(..., "")`, so a missing one degrades to `notifications.py`'s logged-warning no-op rather than crashing. No `LIVECLASS_PROFANITY_WORDS` override configured here — `moderation.py` runs on its built-in `DEFAULT_PROFANITY_WORDS` list.

---

## 16. Testing (`tests.py`, ~2610 lines, 25 test classes — confirmed by direct count)

All extend `LiveClassTestBase(TestCase)` (line 120 — shared fixtures: a teacher, a student, an active classroom, and a priced non-free pass on it, plus `make_session()`). `_apply_common_patches` (line 111, called from `setUp`) confirms the mocking: `LIVEKIT_PATCH` (`patch.multiple` on `liveclass.views` — `ensure_room`, `generate_livekit_token` → `"fake-token"`, `remove_participant`, all no-op'd) and `SAFE_DELAY_PATCH` (`liveclass.views._safe_delay` no-op'd) — so no test in the file ever touches the real LiveKit network or dispatches a real Celery task.

| Test class | Covers |
|---|---|
| `CouponValidityTests` | `Coupon.is_valid()` — percent/flat, scoping, expiry |
| `ChargeAndCreatePurchaseTests` | `_charge_and_create_purchase` (rounding rules, coupon stacking, cross-classroom scoping) |
| `EscrowChargeForSessionTests` | `PassPurchase.charge_for_session` — idempotency, partial windows, rounding remainder on the final day |
| `ClassReferralCommissionTests` | Per-classroom referral commission (capping, idempotency, disabled-at-accept-time edge case) |
| `ReverseRefundTests` | `PassPurchase.reverse()` — remaining-balance-only refund correctness |
| `JoinRequestAcceptViewTests` | `ClassJoinRequestViewSet.accept` end-to-end |
| `PassPurchaseCancelRefundViewTests` | `cancel`/`refund` actions |
| `ClassroomCloseTests` | `/close/` refunds every active purchase |
| `WaitlistTests` | Capacity overflow, FCFS promotion order, kicked-student exclusion, manual `/promote/` |
| `ClassroomBanTests` | Ban → refund + reject-pending-requests |
| `ReferralRedeemTests` | Signup-referral redeem window/validity |
| `TeacherEarningsTests` | Earnings dashboard scoping (no cross-teacher leakage) |
| `ClassroomRecordingsTests` | Recordings visibility — teacher vs enrolled student vs no-access |
| `ClassroomPriceRatingFilterTests` | Explore list filters (price range, min rating) |
| `CoinWithdrawalTests` | Min/max, UPI/bank validation, approve/reject/cancel/mark_paid |
| `CoinPurchaseTests` | Initiate/verify (signature check, retry semantics, fail-closed if gateway secret missing) |
| `ClassroomShareTests` | Outside-app vs in-app, self-share rejection |
| `ReviewHolidayQueryOwnershipTests` | Can't edit someone else's review/holiday/query |
| `PassGiftFlowTests` | Send/claim/cancel/expiry |
| `AutoRenewTests` | Chaining, fail-closed on insufficient balance |
| `ChatMessageReportTests` | Report flow (file → review → soft-delete) |
| `ChatModerationScreenMessageTests` (plain `TestCase`, not `LiveClassTestBase`) | `screen_message()` unit tests — profanity, leetspeak, links, phone numbers, caps, malformed input never raises |
| `NotificationPreferenceTests` | Preferences GET/PATCH, `allowed_channels_for` |
| `NotificationDigestTaskTests` | `send_notification_digests` batching logic |
| `SessionEngagementReportTests` | `compute_engagement_report()` / `build_engagement_report` correctness |
| — | **Confirmed gap**: no `StudentProgressTests` class exists anywhere in `tests.py` (verified — only 26 test classes total, none covering `StudentProgressView`). `StudentProgressView` (§1, §4) is currently untested; add a test class here before relying on it in production. |

**Sibling test module**: `test_classroom_chat_bridge.py` (§1, §6b) is a **separate** file, discovered automatically alongside `tests.py` by Django's `test*.py` convention — `python manage.py test liveclass` runs both without extra config. **As last confirmed** (now stale — see the 🚨 content-mismatch warning in §1's file-map row, the latest upload under this filename contains bridge source code, not tests) it reused `tests.py`'s `LIVEKIT_PATCH`/`SAFE_DELAY_PATCH` (imported directly from `.tests`) and added 6 test classes: `CreateClassroomGroupTests`, `JoinAcceptChatSyncTests`, `RemovalChatSyncTests`, `MetadataSyncTests`, `ArchiveOnCloseTests`, `ClassroomStaffChatSyncTests` (the last one used to self-skip via `self.skipTest(...)` if `ClassroomStaff` wasn't importable — that gate is now moot either way, since `ClassroomStaff`'s shape and `chat_group_enabled`/`linked_conversation_id` are both confirmed/landed, see §6b). **Re-upload this file to confirm these 6 classes still match reality** before relying on this description.

---

## 17. Known "fix" notes baked into the code (worth remembering — don't re-break these)

1. `apps.py` must exist and be wired, or `signals.py` is dead code.
2. `CELERY_BEAT_SCHEDULE` must actually register a task, or it silently never runs even if fully implemented — `refresh_stale_enrolled_counts`, `reconcile_stuck_coin_purchases`, `run_auto_renewals`, `expire_unclaimed_gifts`, `send_notification_digests` **all had this exact bug once** (written, tested-looking, never scheduled).
2b. `DEFAULT_THROTTLE_RATES` (settings.py) must have a matching entry for every `ScopedRateThrottle`/custom-throttle `scope` used anywhere in `views.py` — a missing one isn't a soft degrade, it's `ImproperlyConfigured` (an unhandled 500) on the very first request that hits that endpoint. `session_join`, `session_token`, `coupon_validate`, `chat_message_create`, `chat_reaction`, `classroom_share`, `chunked_upload_init/chunk/complete`, and `coin_withdrawal`/`coin_purchase` **all had this exact bug at least once** — same "written correctly but not wired everywhere it needs to be" shape as item 2 above, just at the settings layer instead of Celery's.
3. `TeacherEarningsView`/`StudentProgressView` are plain `APIView`s — `router.register()` never auto-wires a non-ViewSet; each needs its own explicit `path()` in `urls.py` (this was missing at least once historically). (`NotificationPreferenceView` no longer lives in this app at all — see §1/§4/§5 corrections.)
4. `LiveKitError` must subclass DRF's `APIException` (not bare `Exception`) or it bypasses `exceptions.py` entirely and every call site has to hand-build its own error response.
5. All File/ImageFields need `MaxFileSizeValidator`; the four plain FileFields (material, assignment attachment, assignment submission, certificate) also need a **safelist** `FileExtensionValidator` — `cover_image` is safe for free via Pillow decoding.
6. `_first_message()` in `exceptions.py` must recurse (dict/list nesting) — flat `data[field][0]` indexing breaks on nested/list-of-dict serializer errors, and breaks *inside the exception handler itself*, producing a raw 500 instead of a clean error.
7. `PassPurchase.charge_for_session()` existing isn't enough — it needs `ClassSession`'s post_save signal (`_charge_passes_for_completed_session`) to actually call it on transition into COMPLETED, and `sync_missed_charges()` as the catch-up safety net if that signal is ever bypassed.
8. `_phone_for(user)` must be the **one** shared lookup for SMS/WhatsApp, or the two channels can silently disagree on which field they trust.
9. WhatsApp messages **must** use a pre-approved template outside the 24h user-initiated window — not an MSG91 limitation, a WhatsApp platform rule.
10. `Classroom.has_access()` must exclude purchases past `max_classes` cap — historically it stopped at status/expiry only, silently letting an "N-class pack" pass grant unlimited joins.
11. Waitlist FCFS promotion must be a single conditional `.filter(notified=False).update(notified=True)` (compare-and-swap), never read-then-save — two participants leaving near-simultaneously can otherwise double-promote the same student.
12. Signal-handler side-effects that aren't needed for the `.save()` itself to be correct (LiveKit calls, `.delay()` calls, cross-row cleanup) must go inside `transaction.on_commit()`, not run inline — otherwise a task can fire on a worker before its row has committed, or fire for a change that later rolls back.
13. Every step inside a `post_save`/`pre_save` signal handler needs its own try/except — an uncaught exception there raises out of the `.save()` call itself, turning an unrelated view/task into a 500 even though the state change the caller wanted was already valid.
14. Deleting a `Classroom` must be a soft delete (`is_deleted`/`deleted_at`) gated by `can_be_deleted()` (30-day minimum age + no active paid pass outstanding) — a bare `instance.delete()` let a teacher collect coins and vanish with zero recourse for students; `/close/` is the correct "shut down early" path since it refunds first.
15. `ClassPassViewSet` must refuse DELETE outright once a pass has ever been purchased (use `is_active=False` to pause) and must refuse any PATCH that would retroactively shrink what an active holder already paid for (price up, or `validity_days`/`max_classes`/`pass_type` reduced/changed) while an active paid purchase is outstanding.
16. `notify_waitlist_promotion` must be dispatched from `signals.py`'s `on_participant_left` via `tasks.notify_waitlist_promotion.delay(...)` **and** a `create_notification(...)` bell row — a prior version imported a function (`notify_waitlist_seat_open`) that never existed anywhere in the codebase, silently swallowed by the surrounding try/except, so no student was ever notified of a freed seat.
17. WebSocket JWT auth must live in exactly **one** place (`LearnScroll/ws_auth.py`, project-level) — `message` and `liveclass` each used to keep their own independent copy (`message/Middleware.py` vs the old `liveclass/ws_auth.py`) that was never diffed against the other, risking silent mismatch. Both apps now import the same file; `liveclass/ws_auth.py` is a re-export shim only, kept so existing `from liveclass.ws_auth import JWTAuthMiddleware` call sites don't need to change.
18. **A `broadcast_to_*` call site and its receiving consumer must be added together, or the failure is silent.** `models.py`'s `_broadcast_classroom_stats()` called `from .realtime import broadcast_to_classroom` long before that function — or `ClassroomConsumer`/the `ws/liveclass/classroom/<id>/` route it needs — actually existed anywhere in `realtime.py`/`consumers.py`/`routing.py`. Every call was a plain `ImportError`, caught by that function's own broad try/except (by design — a realtime push must never break the rating/enrollment recompute it's attached to), so nothing ever crashed and nothing ever alerted anyone; the classroom-detail screen's socket just always failed to connect and fell back to its slower backstop poll. The general lesson: a broadcast helper's own try/except is the right fail-open contract for *runtime* Redis/channel-layer errors, but it also hides a genuinely missing counterpart at the code level — grep for the consumer/group name whenever adding a new `broadcast_to_*` call, don't assume the receiving side already exists just because the sending side compiles.
19. **`ClassroomReviewViewSet`/`ClassHolidayViewSet`/`ClassQueryViewSet` must have their own `perform_update`/`perform_destroy` ownership checks (Pass 19, re-verified Pass 21)** — all three used to be plain `ModelViewSet`s with no override at all. Because their `classroom`/`asked_by`/etc. fields are writable on the serializer and `get_queryset()` only enforces a *read*-tier check (or none), this let any authenticated user PATCH/DELETE another student's review, a manager PATCH another classroom's holiday by guessing its id, or a manager overwrite a student's own query just by sending `?classroom=<id>` — a write reachable through a read-only boundary. Fixed shape, same in all three: `perform_update` re-checks the real ownership/manage-tier rule on `serializer.instance` (not just on the incoming payload), `perform_destroy` does the same, and any writable `classroom` field is explicitly blocked from being reassigned mid-update (mirrors the same guard already on `Assignment`/`Notice`/`LivePoll`). `ClassQueryViewSet.perform_update` additionally freezes a query once it's `ANSWERED`. Regression-covered in `tests.py`'s `ReviewHolidayQueryOwnershipTests` (§16, class 17) — if a new viewset in this file skips its own `perform_update`/`perform_destroy`, it has this exact hole by default.
20. **A cross-app bridge module (`core/classroom_chat_bridge.py`) must ship its model dependencies alongside its call sites, or every caller is dead code.** Tasks 29–40 wired `classroom_chat_views.py`, 6 signal receivers in `signals.py`, and 2 new `urls.py` paths — all against `Classroom.chat_group_enabled`/`Classroom.linked_conversation_id`. **✅ Resolved as of this audit** — both fields are now defined in `models.py` (§1, §3). Lesson stands for the next feature that spans an app boundary: confirm the model fields it depends on are actually migrated before wiring the views/signals/urls that assume them, not after.
21. ✅ **RESOLVED (Task 10 fix) — a new file with real logic isn't the same as a *wired* feature.** `parent_link_views.py` (Phases 2/4/5) was fully written — permission checks, viewset, notification calls, ownership checks — but had zero `path()`/`router.register()` entries in `urls.py` and imported a `StudentReportCard` model that didn't exist in `models.py`. Same lesson as item 20, one layer earlier: a file existing in the codebase says nothing about whether it's reachable. **Both now fixed**: all four views are wired (§1/§5), `StudentReportCard` is a real model (§3). The second point from this lesson — **a new parent-facing feature was built without checking whether one already existed** (`ClassSessionViewSet.parent_join`, live since an earlier task) — is **also now resolved**: `parent_join()` has been consolidated onto the same `ParentAccessCode` mechanism `parent_link_views.py` already used, rather than staying a second, unreconciled parent-auth path. See §6c for the full before/after.
22. **A model whose write methods are disabled must raise loudly, not silently no-op.** Task 12 (assignment) and Task 6 (coin models) both retire a local model's write path in favor of a cross-app one — in both cases every retired method is a `RuntimeError` stub naming its replacement, not a quiet `pass`/`return None`. Follow this same shape for any future "the real write path moved to another app" migration: a silent no-op here would look like success to the caller while doing nothing, which is far worse than a loud, traceable crash.
23. **A cross-app bridge's roster/payload shape copied from a sibling bridge is an assumption until the sibling's own source is read, not a fact.** `liveclass/bridge.py`'s `roster` dict shape (`{"user_id": ...}` only, no `roll_number`/`enrollment_no`) was inferred from `campus/bridge.py`'s call site, not from `assignment/bridge.py` itself (not part of this audit) — flagged explicitly in the code rather than padded with guessed placeholder values. Don't quietly "fix" this into certainty; confirm against `assignment/bridge.py` first.
24. 🐛 **Three concrete, currently-live bugs confirmed by direct code read this pass — not theoretical, worth fixing before anything else touches these areas:**
    - `ClassSessionViewSet.parent_join` (`views.py`) calls `ParticipantRole.OBSERVER`, which doesn't exist (`livekit_utils.py` only defines `PARENT_OBSERVER`) — `AttributeError` on every real call. See §6c.
    - `urls.py` wires 4 `classrooms/<...>/` paths (`create_group/`, `group/`, `participants/<id>/parent-code/`, `parent-queries/`) with the `<uuid:classroom_id>` converter, but `Classroom`'s pk is a plain integer `AutoField`, not a UUID — these 4 paths 404 for every real classroom id before the view even runs. See §6b.
    - `ReportCardViewSet._homework_stats()` (`parent_link_views.py`) filters on `Assignment.Category.HOMEWORK`, a field/enum `Assignment` doesn't have — `AttributeError` on every `POST report-cards/`. Separately, once that's fixed, it should read through `bridge.get_assignment_submissions()` (§6d) rather than the local, Task-12-frozen `Assignment`/`AssignmentSubmission` models directly, or it'll silently miss every assignment posted after the Task 12 cutover. See §6c.
25. **A module's own docstring (or a whole file's module-level docstring, like `urls.py`'s endpoint reference) can go stale the moment the code beneath it changes, and nothing forces it to be re-synced.** `urls.py`'s docstring still describes `coin-purchases/initiate/`, full `withdrawals/` CRUD, and full `assignments/`/`submissions/` CRUD as live — Task 6 and Task 12 changed the actual view behavior underneath without anyone updating that comment block. Trust the ViewSet's own method bodies over a docstring describing them, especially in a codebase with this much "as of Task N" churn — see §5/§6d/§6e for what's actually true as of this audit.
26. **(NEW, TASK 4) A "notify my followers when I create X" fan-out is now a repeated, cross-app pattern — and each copy carries its own unconfirmed enum until checked.** `liveclass.tasks.notify_followers_new_classroom` is the third instance of this exact shape in the codebase (`post.tasks.notify_followers_new_post`, `testseries.tasks.notify_followers_new_testseries` are the other two) — same `Follow`/`RestrictUser` bulk-query pattern, same `core.services.create_bulk_notifications()` call, each referencing its own new `Notification.NotifType` member (`CLASSROOM_CREATED_BY_FOLLOWED` here) with no independent confirmation that `core` actually defines it. Don't assume this one is safe just because the pattern is now proven elsewhere — each app's enum member is a separate, individually-unverified claim. See §3, §9.

---

## 18. Suggested "always check this" checklist for future work

When continuing work in a new chat, paste this file and say what you want changed. If a change touches:
- **Money/coins** → check `PassPurchase` (`charge_for_session`, `reverse`, `renew`, `sync_missed_charges`), `_charge_and_create_purchase` (views.py), `CoinTransaction`, the escrow-charge signal (`_charge_passes_for_completed_session`), and the related Celery sweeps (`expire_and_refund_passes`, `reconcile_stuck_coin_purchases`, `run_auto_renewals`, `expire_unclaimed_gifts`). Never let a new money-moving code path bypass `CoinTransaction` logging.
- **Realtime** → check `consumers.py`, `realtime.py`, `routing.py`, and whichever `views.py`/`models.py` code broadcasts the event (`broadcast_to_session`/`broadcast_to_user`/`broadcast_to_classroom`). Adding a new `broadcast_to_*` call site? Confirm the matching consumer + group name + `routing.py` route all already exist — see §17 item 18 for what happens when they don't.
- **Notifications** → check `notifications.py` (channel implementation), the specific `notify_*` task in `tasks.py`, and `NotificationPreference.allowed_channels_for` — and make sure any new push also creates a `create_notification()` bell row if it should be visible in-app, not just pushed.
- **Access control** → check the `_can_*`/`_access_level`/`has_access` helpers in `views.py` and `models.py` — keep them consistent, don't duplicate the logic in a new spot. Remember the `max_classes` cap and ban checks live inside `Classroom.has_access()`.
- **New Celery task** → it does nothing on a schedule until it's **also** added to `CELERY_BEAT_SCHEDULE` in `settings.py` (this exact bug has recurred at least 5 times in this codebase's history — see §17 item 2).
- **New `ScopedRateThrottle` / custom throttle scope** → it crashes on its very first hit until its `scope` name also has a matching entry in `DEFAULT_THROTTLE_RATES` in `settings.py` (recurred at least 10 times across `liveclass`/`message` — see §17 item 2b).
- **New plain `APIView`** (not a ViewSet) → it's unreachable until it also gets an explicit `path()` in `urls.py` (recurred at least 3 times — see §17 item 3).
- **New signal handler on `ClassSession`/`SessionParticipant`** → wrap each step in its own try/except, and defer anything that isn't needed for the `.save()` itself (LiveKit calls, `.delay()`, cross-row cleanup) into `transaction.on_commit()`.
- **File uploads** → new FileField needs both `MaxFileSizeValidator` and, if it's a plain `FileField` (not `ImageField`), a safelist `FileExtensionValidator`.
- **Error responses** → any new exception type raised in a view should either already be a DRF `APIException` (flows through `exceptions.py` automatically) or get an entry in `_CODE_BY_EXC` if it needs a specific machine-readable `code`.
- **Parent-facing work** → check §6c first — parent auth now runs through exactly one mechanism (`ParentAccessCode`/`ParentToken`, resolved via `core.classroom_chat_bridge.resolve_parent_from_token()`), used by `ClassroomParentCodeGenerateView`, `ReportCardViewSet`, `ClassroomParentQueryListView`, `ParentQueryReplyView`, and `ClassSessionViewSet.parent_join` alike. Don't resurrect the old signed-token path (`PARENT_JOIN_TOKEN_SALT`/`generate_parent_join_token`/`ParentJoinSerializer`) — it's dead code kept only pending a project-wide grep-and-delete, see §6c.
- **Assignments** → check §6d first. New assignments never touch `liveclass.Assignment`/`AssignmentSubmission` directly — go through `bridge.create_assignment()`/`get_assignment_submissions()`, which delegate to `assignment.bridge.*`. The local models are read-only history now; the only sanctioned exception to "liveclass never imports `assignment.models` outside `bridge.py`" is the one-off `migrate_liveclass_assignments_to_unified` command. If `assignment/bridge.py`'s real `create_context_assignment()` signature requires `roll_number`/`enrollment_no` keys unconditionally, `bridge.py`'s roster dict needs a one-line fix — this is a flagged, unconfirmed assumption, not settled fact (see §6d).
- **Coin purchases/withdrawals** → check §6e first. `CoinPurchase.mark_success`/`mark_failed` and all six `CoinWithdrawal` write methods now raise `RuntimeError` by design — this is not a bug to "fix" by removing the stub, it's Task 6's intended read-only freeze. Any new money-moving code for top-ups/payouts belongs in `user_profile` (`CoinPurchaseRequest`/`CoinWithdrawalRequest`), writing through `user_profile.CoinLedger`, never back through these two `liveclass` models. `User.coin` and `liveclass.CoinTransaction` are still live/authoritative for the wallet balance itself.
- **Follower fan-out on create (TASK 4)** → before relying on `notify_followers_new_classroom` (or copying its shape for a fourth app), confirm `Notification.NotifType.CLASSROOM_CREATED_BY_FOLLOWED` and a `classroom` FK on `Notification` actually exist in `models.py` (§3, §17 item 26) — both are referenced directly with no fallback, so a missing one fails the task silently (async, after the classroom-create request has already returned 201).