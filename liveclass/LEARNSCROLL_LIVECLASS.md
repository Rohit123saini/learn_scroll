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

---

## 1. File map (what lives where)

| File | Lines | Role |
|---|---|---|
| `models.py` | ~3200 | All DB models (~45 models) + model-level business logic + cache-version signals |
| `serializers.py` | ~1460 | DRF serializers — 55 classes, one (or a few) per model/action |
| `views.py` | ~5990 | DRF ViewSets/APIViews — all HTTP endpoints, permission logic, orchestration |
| `urls.py` | ~385 | DRF router registrations + a few plain `path()`s for non-ViewSet views. **Its module docstring is itself the canonical endpoint reference** — reproduced in full in §5 below. |
| `admin.py` | ~611 | Django admin registrations (inlines, list filters, bulk actions) — 40+ `ModelAdmin`s |
| `signals.py` | ~375 | `@receiver`s for session-end cleanup, waitlist FCFS promotion, attendance credit (registered via `apps.py`) |
| `apps.py` | 33 | `AppConfig.ready()` — the thing that actually makes `signals.py` load |
| `tasks.py` | ~1700 | All Celery tasks — periodic sweeps + async `notify_*` senders (~40 `@shared_task`s) |
| `LearnScroll/celery.py` | 57 | **Project-level** Celery app bootstrap (sits next to `settings.py`, not part of the `liveclass` app itself). Creates the `Celery("LearnScroll")` instance, loads every `CELERY_*` setting from Django settings via `config_from_object(..., namespace="CELERY")`, and `autodiscover_tasks()`s a `tasks.py` in every `INSTALLED_APPS` app (`liveclass/tasks.py` included) — no manual per-task registration needed. Docstring spells out the one-time wiring: `LearnScroll/__init__.py` must do `from .celery import app as celery_app`; worker (`celery -A LearnScroll worker`) and beat (`celery -A LearnScroll beat`) are two separate long-running processes, both required. |
| `consumers.py` | ~475 | Django Channels WebSocket consumers (`SessionConsumer`, `UserConsumer`) |
| `routing.py` | 20 | WebSocket URL patterns (separate from `urls.py`, wired into ASGI, not WSGI) |
| `liveclass/ws_auth.py` | 53 | JWT-over-WebSocket auth middleware — **thin re-export only** (`from LearnScroll.ws_auth import JWTAuthMiddleware, get_user_from_token`), kept so every existing `from liveclass.ws_auth import JWTAuthMiddleware` call (including `asgi.py`) keeps working unchanged after the consolidation below. |
| `LearnScroll/ws_auth.py` | ~55 | **Project-level**, real implementation (moved here — was two independent, never-compared copies: `message/Middleware.py` vs the old `liveclass/ws_auth.py`). Pulls the JWT out of the `?token=` query string (a WS handshake can't carry a custom `Authorization` header), validates it with `rest_framework_simplejwt.tokens.AccessToken`, resolves it to a user via `get_user_from_token()` — every failure mode (bad signature, expired, malformed, deleted/deactivated user) degrades to `AnonymousUser` rather than raising, so a bad token can never crash the socket, it just connects unauthenticated. Both the `message` app (chat/calls/study-rooms) and `liveclass` import from this one file now, so the two can never silently drift out of sync again. |
| `realtime.py` | ~500 | Low-level pub/sub helpers: Redis-backed broadcast, presence, missed-event history, connect rate-limit |
| `livekit_utils.py` | ~434 | LiveKit token generation, room API client, webhook signature verification |
| `moderation.py` | 137 | Lightweight rule-based chat moderation (`screen_message`) — profanity/spam/caps detector |
| `notifications.py` | ~260 | Single fan-out point for push/email/SMS/WhatsApp — provider-agnostic |
| `exceptions.py` | 184 | DRF custom exception handler — normalises every error response to one JSON shape |
| `chunked_upload_views.py` | ~607 | Chunked file upload (init/chunk/complete/abort) for large files (cover images, recordings, materials) |
| `tests.py` | ~2610 | Test suite (Django `TestCase`), 25 test classes organized by feature area — see §16 for the confirmed gap (no `StudentProgressTests`) |
| `settings.py` | ~750 | **Project-level** Django settings (not liveclass-only — shared with `login`/`message` apps) |

**Wiring dependencies to remember (all previously-broken gaps, now fixed):**
- `signals.py` does nothing unless `apps.py` exists and `INSTALLED_APPS` points at `liveclass.apps.LiveclassConfig`.
- Celery **worker** (`celery -A LearnScroll worker`) and **beat** (`celery -A LearnScroll beat`) are two separate long-running processes — both required, neither optional. A task can be perfectly written and still never run if it isn't also registered in `CELERY_BEAT_SCHEDULE` (this bug happened at least 4 times historically — see §16).
- `LearnScroll/__init__.py` must do `from .celery import app as celery_app`.
- ASGI app (project-level `asgi.py`) wires `liveclass.routing.websocket_urlpatterns` through `liveclass.ws_auth.JWTAuthMiddleware`.
- `settings.py` → `REST_FRAMEWORK["EXCEPTION_HANDLER"] = "liveclass.exceptions.liveclass_exception_handler"`.
- `TeacherEarningsView`, `StudentProgressView`, `NotificationPreferenceView`, `HealthCheckView` are plain `APIView`s, **not** ViewSets — `router.register()` never auto-wires them; each needs its own explicit `path()` in `urls.py` (this was missing at least 3 times historically).

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

### `ChatMessage`, `ChatMessageReport`, `ChatReaction`, `SessionReadState`
- **Moderation had no teeth (fixed)**: `kick()` in `views.py` used to only remove someone from the LiveKit room without any DB-side ban/block record — now backed by real moderation flow (`screen_message`, `ChatMessageReport.review`).
- `SessionReadState` — unread-message tracking per (session, user), backs `sessions/{id}/unread/` + `/mark-read/`.

### `LivePoll`, `PollResponse`, `PollTemplate`
`PollTemplate` — reusable poll presets a teacher can `quick-create` from.

### `Assignment`, `AssignmentSubmission`
`AssignmentSubmission.is_late()`.

### `ClassroomReview`, `ClassroomWishlist`, `ClassroomShare`
`ClassroomShare` backs `share_count` and `share-stats`/`my-shares`.

### `Coupon`
`is_valid()`. Percent or flat discount, optionally scoped to one classroom or usable across one teacher's classrooms. Dry-run checkable via `coupons/validate/`.

### `CoinTransaction`
Append-only ledger — every coin movement (purchase, debit, refund, withdrawal, gift, referral) logged here. Read via `coin-transactions/` (own only) and `coin-transactions/balance/` (real `User.coin`).

### `CoinPurchase`
Razorpay top-up. `mark_success(gateway_payment_id, gateway_signature)` / `mark_failed(reason="")`. Stuck-`PENDING` rows (client crashed before `/verify/`, webhook lost) become retryable via `tasks.reconcile_stuck_coin_purchases` after `COIN_PURCHASE_PENDING_TIMEOUT` (2h).

### `Referral`, `referral_code_for_user(user_id)`, `referral_code_to_user_id(code)`
Flat, one-time signup bonus — **distinct** from the per-classroom `referral_enabled`/`referral_commission_percent` mechanism on `Classroom`. Reversible encoding (not a DB lookup) — `referral_code_for_user`/`referral_code_to_user_id` are inverse functions. Referrer earns a bonus + ongoing per-session commission, **capped at total purchase amount**.

### `CoinWithdrawal`
Payout request lifecycle. Coins are debited **immediately on request**, not on approval.
- `create_request(cls, user, coins, payout_method, payout_details) -> CoinWithdrawal` — classmethod constructor.
- `approve(admin_user)`, `reject(admin_user, reason)`, `cancel()`, `mark_paid(admin_user, external_reference)`, `_refund_coins(reason_note)` (internal, called by reject/cancel).
- Min/max validated (see tests: `CoinWithdrawalTests`), UPI/bank payout-detail validation.

### `ClassroomBan`, `ClassroomStaff`, `SessionWaitlist`, `ClassroomReport`
- `ClassroomBan` — issuing a ban refunds the banned student's active pass and rejects their pending join requests (see `ClassroomViewSet.ban`); `Classroom.has_access()` also independently excludes banned students as defence-in-depth.
- `ClassroomStaff` — co-teacher/moderator roles (manager-level vs moderator-level, checked by `_org_staff_role`/`_can_manage_classroom`/`_can_moderate_session`).
- `SessionWaitlist` — capacity overflow queue; FCFS promotion is compare-and-swap (`filter(notified=False).update(notified=True)`, single query — not read-then-save, which had a race where two students leaving at once could double-promote the same waitlisted student).
- `ClassroomReport` — auto-flags the classroom at `AUTO_FLAG_THRESHOLD` pending reports (`_auto_flag_classroom` signal, `post_save`), lazy-imports `tasks.notify_classroom_flagged` so `models.py` stays importable even without Celery wired.

### `Certificate`, `ClassReminder`, `ClassHoliday`, `Notice`, `ClassQuery`
`Notice.is_expired()`. `Notice` list is the per-classroom cached read (§ cache versioning above).

### `Notification`, `NotificationPreference`, `create_notification()`, `create_bulk_notifications()`
- `Notification.mark_read()`.
- `NotificationPreference.allowed_channels_for(notif_type)`, `.for_user(cls, user)` (classmethod, get-or-create-like accessor). Fields include `push_enabled`/`email_enabled`/`sms_enabled`/`whatsapp_enabled`, `muted_types`, `digest_frequency` (off/daily/weekly), `last_digest_sent_at`.
- **"Production notification coverage audit" NOTE** (line ~2779): six specific notification types were previously missing coverage — now all wired (see the full `notify_*` task list in §9).

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

## 4. Views / API (`views.py`, ~5990 lines, DRF ViewSets)

### Permission/role helper functions (module-level, reused everywhere — don't duplicate this logic in a new spot)
`_org_staff_role`, `_has_room_access_no_pass`, `_has_room_access`, `_resolve_session_roles`, `_can_moderate_session`, `_can_manage_classroom`, `_can_view_classroom_internals`, `AccessLevel` (class) + `_access_level`, `_accessible_classroom_ids`.

### Cross-cutting bits
- `LiveClassPagination(pagination.PageNumberPagination)` — shared pagination class for all list endpoints.
- `_safe_delay(task, *args, **kwargs)` — wraps `.delay()` calls so a broker hiccup never crashes the request that triggered the notification.
- `_safe_broadcast_to_user(user_id, event_type, data)` — same defensive wrapper around `realtime.broadcast_to_user`.
- `_is_truthy(value)` — query-param boolean parsing helper (`?mine=true` etc.).

### ViewSets and every `@action` (module line numbers as of this audit)

| ViewSet | `get_queryset`/`perform_*` | `@action`s |
|---|---|---|
| `ClassroomViewSet` (252) | yes, all 4 | `close`, `has_access`, `my_pass` (url `my-pass`), `start-or-join`, `stats` (GET, line 671), `share` (POST, 728), `share-stats` (GET), `my-shares` (GET, detail=False), `refer-link` (GET), `recommended` (GET, detail=False, ?limit=), `ban` (POST), `bans` (GET), `unban/(?P<student_id>...)` (POST), `recordings` (GET). Custom `list()` uses the version-based cache. |
| `ClassroomReportViewSet` (1052) | yes | `review` (POST, staff-only decision on a report) |
| `ClassScheduleViewSet` (1155) | yes, all 4 | standard CRUD, scoped to own classrooms |
| `ClassSessionViewSet` (1385) | yes, create/update/destroy | `join` (POST, throttled `session_join` scope), `end` (POST), `engagement-report` (GET), `token` (POST, throttled `session_token` scope — fresh LiveKit token, no participant row, for reconnect/testing), `kick/(?P<user_id>...)` (POST), `mute/(?P<user_id>...)` (POST, body `{"muted": true|false}`), `hand` (POST, raise/lower **own** hand), `hand/(?P<user_id>...)/lower` (POST, lower **someone else's** hand), `unread` (GET), `mark-read` (POST), `start-recording` (POST), `stop-recording` (POST), `breakout` (GET+POST), `breakout/assign` (POST), `breakout/close` (POST). Helper fns `_perform_join` (2064) and `_try_promote_from_waitlist` (2247) live at module level right after this viewset. |
| `LiveKitWebhookView` (`APIView`, 2319) | — | receives LiveKit room/participant/**egress_ended** webhooks (server-to-server only) |
| `ClassPassViewSet` (2408) | yes, all 4 | CRUD (teacher-owned). `perform_destroy`/`perform_update` enforce the "can't shrink what active holders paid for" rule (see §5 `passes/{id}/`). |
| `PassPurchaseViewSet` (2562) | yes | `refund` (POST), `cancel` (POST), `toggle-auto-renew` (POST, body `{"auto_renew": bool}`), `referral-earnings` (GET, detail=False). `_charge_and_create_purchase` (module-level, 2732) is the **shared** "debit coins + create purchase" logic used by both join-request-accept and gift-claim flows — single source of truth for the money-moving step. |
| `ClassJoinRequestViewSet` (2897) | yes, create | `accept` (POST — **this is where money actually moves**, via `_charge_and_create_purchase`), `reject` (POST, no charge), `cancel` (POST, requester-only, pending-only) |
| `PassGiftViewSet` (3267) | yes, create | `claim` (POST, recipient-only — creates the `PassPurchase`), `cancel` (POST, gifter-only, pending-only — refunds gifter) |
| `SessionParticipantViewSet` (3447) | yes | `leave` (POST) |
| `ClassMaterialViewSet` (3490) | yes, all 4 | standard CRUD |
| `ChatMessageViewSet` (3558) | yes, create/destroy | `react` (POST+DELETE), `pin` (POST), `unpin` (POST). `perform_create` runs `moderation.screen_message()` **after** save — a false positive must never block a real send, only add to a moderator review queue. |
| `ChatMessageReportViewSet` (3779) | yes, create | `review` (POST, moderator — soft-deletes the reported message when actioned) |
| `LivePollViewSet` (3881) | yes, all 4 | `vote` (POST), `close` (POST), `quick-create` (POST, detail=False, from a `PollTemplate`) |
| `PollTemplateViewSet` (4059) | yes, all 4 | CRUD (reusable poll presets) |
| `AssignmentViewSet` (4114) | yes, all 4 | — |
| `AssignmentSubmissionViewSet` (4213) | yes, all 4 | `grade` (POST, teacher-only) |
| `ClassroomReviewViewSet` (4354) | yes, all 4 | — |
| `ClassroomWishlistViewSet` (4438) | yes, create/destroy | — |
| `CouponViewSet` (4475) | yes, all 4 | `validate` (GET, detail=False, `?code=`, throttled `coupon_validate` scope — dry-run check without spending) |
| `CoinTransactionViewSet` (4590, list-only `GenericViewSet`) | yes | `balance` (GET, detail=False) |
| `CoinWithdrawalViewSet` (4621) | yes, create | `cancel` (POST), `approve` (POST, staff), `reject` (POST, staff), `mark-paid` (POST, staff) |
| `_create_gateway_order(amount_inr, receipt)` / `_verify_gateway_signature(order_id, payment_id, signature)` (4765/4778, module-level) | — | Razorpay order-create + HMAC signature verify, shared by `CoinPurchaseViewSet` |
| `CoinPurchaseViewSet` (4799) | yes | `initiate` (POST, detail=False, body `{"coins": N}`), `verify` (POST — gateway checkout callback payload), `retry` (POST — re-attempt a FAILED purchase) |
| `ReferralViewSet` (4900, list-only `GenericViewSet`) | yes | `my-code` (GET, detail=False), `redeem` (POST, detail=False, body `{"code": "R..."}`) |
| `TeacherEarningsView` (`APIView`, 5027) | — | dashboard, not a ViewSet — explicit `path()` in urls.py |
| `StudentProgressView` (`APIView`, 5094) | — | dashboard, not a ViewSet — explicit `path()` in urls.py |
| `ClassroomStaffViewSet` (5168) | yes, all 4 | manage co-teacher/moderator roles |
| `SessionWaitlistViewSet` (5265) | yes | `promote` (POST — manual override of FCFS auto-promotion) |
| `CertificateViewSet` (5373) | yes, create | issue (via `perform_create`) |
| `ClassReminderViewSet` (5439) | yes, create | — |
| `ClassHolidayViewSet` (5467) | yes, all 4 | — |
| `NoticeViewSet` (5535) | yes, all 4 | `pin` (POST). Custom cached `list()` (per-classroom notice cache version). |
| `ClassQueryViewSet` (5670) | yes, create/update | `answer` (POST, teacher/co-teacher/moderator) |
| `NotificationViewSet` (5787) | yes | `unread-count` (GET, detail=False), `mark-read` (POST), `mark-all-read` (POST, detail=False) |
| `NotificationPreferenceView` (`APIView`, 5854) | — | GET/PATCH own preferences row |
| `MyDashboardView` (`APIView`, 5873) | — | single-call home-screen summary |
| `HealthCheckView` (`APIView`, 5931) | — | unauthenticated DB+cache liveness probe |

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
| `notification-preferences/me/` | GET, PATCH | `NotificationPreferenceView` — always the caller's own row |
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
classrooms/recommended/              GET               (?limit= — personalized by purchase/wishlist
                                                        history, falls back to rating/enrollment)
classrooms/{id}/ban/                 POST
classrooms/{id}/bans/                GET
classrooms/{id}/unban/{student_id}/  POST
classrooms/{id}/recordings/          GET

coin-purchases/                      GET, POST         (own top-up history)
coin-purchases/initiate/             POST              (body {"coins": N} — starts a gateway order)
coin-purchases/{id}/verify/          POST              (gateway checkout callback payload)
coin-purchases/{id}/retry/           POST              (re-attempt a FAILED purchase)

schedules/                           GET, POST
schedules/{id}/                      GET, PUT, PATCH, DELETE

sessions/                            GET, POST
sessions/{id}/                       GET, PUT, PATCH, DELETE
sessions/{id}/join/                  POST
sessions/{id}/token/                 POST              (fresh token, no participant row — reconnect/testing)
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

chat-messages/                       GET, POST
chat-messages/{id}/                  DELETE            (soft delete)
chat-messages/{id}/react/            POST, DELETE
chat-messages/{id}/pin/              POST
chat-messages/{id}/unpin/            POST

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

assignments/                         GET, POST
assignments/{id}/                    GET, PUT, PATCH, DELETE
submissions/                         GET, POST
submissions/{id}/                    GET, PUT, PATCH, DELETE
submissions/{id}/grade/              POST              (teacher only)

reviews/                             GET, POST
reviews/{id}/                        GET, PUT, PATCH, DELETE

wishlist-classrooms/                 GET, POST         (own saved classrooms; POST {"classroom_id": id})
wishlist-classrooms/{id}/            DELETE            (remove from wishlist, own entry only)

coupons/                             GET, POST
coupons/{id}/                        GET, PUT, PATCH, DELETE
coupons/validate/                    GET               (?code= required — checks without spending)

coin-transactions/                   GET               (own ledger only)
coin-transactions/balance/           GET               (real User.coin balance)

withdrawals/                         GET, POST         (GET: own, or every request filterable by
                                                        ?status= for platform staff; POST: request a
                                                        payout, body {"coins","payout_method",
                                                        "payout_details"} — coins debited immediately)
withdrawals/{id}/                    GET
withdrawals/{id}/cancel/             POST              (own request, only while pending — refunds coins)
withdrawals/{id}/approve/            POST              (platform staff only)
withdrawals/{id}/reject/             POST              (platform staff only; body {"reason": "..."} —
                                                        refunds coins)
withdrawals/{id}/mark-paid/          POST              (platform staff only; body
                                                        {"external_reference": "<UTR/UPI txn id>"})

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

notifications/                       GET               (own, newest first; ?is_read=true/false)
notifications/{id}/                  GET, DELETE       (DELETE clears one, own only)
notifications/unread-count/          GET               (badge count for the bell icon)
notifications/{id}/mark-read/        POST
notifications/mark-all-read/         POST

referrals/                           GET               (people the caller has successfully referred)
referrals/my-code/                   GET               (own referral code + redemption tally)
referrals/redeem/                    POST              (redeem someone else's code, once, new-account-
                                                        only; body {"code": "R..."})
```

---

## 6. Realtime layer

### WebSocket endpoints (`routing.py`)
```python
websocket_urlpatterns = [
    re_path(r"^ws/liveclass/session/(?P<session_id>\d+)/$", consumers.SessionConsumer.as_asgi()),
    re_path(r"^ws/liveclass/user/$", consumers.UserConsumer.as_asgi()),
]
```
- `ws/liveclass/session/<session_id>/` → `SessionConsumer` — per-session realtime (chat, hand-raise, presence, polls, breakout events).
- `ws/liveclass/user/` → `UserConsumer` — per-user channel (personal notifications/pushes not scoped to one session).

### `consumers.py`
- `_session_and_access(session_id, user)` (module-level, line ~144 area) — shared access check before allowing a socket connection into a session group.
- **`SessionConsumer(AsyncJsonWebsocketConsumer)`**: `connect()` (145), `_idle_watchdog()` (253 — auto-disconnects idle sockets), `disconnect(close_code)` (281), `receive_json(content, **kwargs)` (305, client→server), `session_event(message)` (364, server→client, fired via `channel_layer.group_send(..., {"type": "session.event", ...})`).
- **`UserConsumer(AsyncJsonWebsocketConsumer)`**: mirrors the same shape — `connect()` (424), `disconnect(close_code)` (447), `receive_json()` (451), `user_event(message)` (475) — for the per-user group.

### `realtime.py` (Redis-backed helpers used by both consumers and `views.py`/`tasks.py`)
- `_group_name(session_id)`, `_user_group_name(user_id)` — Channels group naming.
- `_history_key(session_id)`, `_presence_key(session_id)`, `_raw_redis_client(write: bool)`.
- `_record_history(session_id, event_type, payload, ts)`, `get_missed_events(session_id, since)` — short replay buffer so a client that reconnects doesn't lose events fired while offline.
- `mark_present(session_id, user_id)`, `mark_absent(session_id, user_id)`, `get_present_user_ids(session_id)` — who's currently in a session.
- `_connect_rate_key(user_id)`, `check_connect_rate_limit(user_id)` — per-user WebSocket connect throttle (abuse/DoS guard).
- `broadcast_to_session(session_id, event_type, payload)`, `broadcast_to_user(user_id, event_type, payload)` — the two public fan-out functions everything else calls.

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

## 7. LiveKit (video) integration (`livekit_utils.py`)

- `_check_credentials()` (75) / `_check_egress_credentials()` (117) — fail-fast config checks.
- `LiveKitError(APIException)` (138) — **fixed from being a bare `Exception`** — now flows through `exceptions.py`'s handler and gets a proper `"code": "livekit_error"` instead of every `except LiveKitError` call site hand-building its own `Response(...)`.
- `ParticipantRole` (178), `_grants_for_role(role)` (187) — maps app-level role (host/moderator/participant) → LiveKit `VideoGrants`.
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

## 9. Celery tasks (`tasks.py`, ~1700 lines) — what runs on its own, and how often

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
`notify_waitlist_promotion(student_id, session_id)`, `notify_classroom_shared(share_id)`, `notify_purchase_refunded(purchase_id)`, `notify_pass_auto_renewed(purchase_id)`, `notify_auto_renew_failed(purchase_id)`, `notify_gift_expired(gift_id)`, `notify_pass_gift_received(gift_id)`, `notify_pass_gift_claimed(gift_id)`, `notify_classroom_flagged(classroom_id)`, `notify_session_auto_completed(session_id)`, `notify_join_request_received(join_request_id)`, `notify_join_request_accepted(join_request_id)`, `notify_join_request_rejected(join_request_id)`, `notify_assignment_graded(submission_id)`, `notify_certificate_issued(certificate_id)`, `notify_notice_posted(notice_id, student_ids)`, `notify_session_live(session_id, exclude_user_id=None)`, `notify_session_cancelled(classroom_id, classroom_title, session_id, scheduled_start_iso, exclude_user_id=None)`, `notify_assignment_posted(assignment_id, student_ids)`, `notify_submission_received(submission_id)`, `notify_staff_added(staff_id)`, `notify_review_posted(review_id)`, `notify_report_reviewed(report_id)`, `notify_query_answered(query_id)`.

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

---

## 14. Admin (`admin.py`, ~611 lines, 40+ `ModelAdmin`s)

- A patch near the top adjusts whatever `ModelAdmin` Django/another app already registered for `User` (via `@admin.register(_User)`).
- **Inlines**: `ClassScheduleInline`, `ClassPassInline`, `ClassroomStaffInline`, `PassDailyChargeInline` (`TabularInline`) — surfaced on their parent model's admin page (e.g. schedules/passes/staff nested under `ClassroomAdmin`; daily charges nested under `PassPurchaseAdmin`).
- **Registered `ModelAdmin`s** (one per model, list filters/search/bulk actions tuned per model): `ClassroomAdmin`, `ClassScheduleAdmin`, `ClassSessionAdmin`, `ClassPassAdmin`, `PassPurchaseAdmin`, `PassGiftAdmin`, `PassDailyChargeAdmin`, `ClassJoinRequestAdmin`, `BreakoutRoomAdmin`, `SessionParticipantAdmin`, `ClassMaterialAdmin`, `ChatMessageAdmin`, `ChatMessageReportAdmin`, `LivePollAdmin`, `PollResponseAdmin`, `AssignmentAdmin`, `AssignmentSubmissionAdmin`, `ClassroomReviewAdmin`, `ClassroomWishlistAdmin`, `CouponAdmin`, `CoinTransactionAdmin`, `CoinPurchaseAdmin`, `CoinWithdrawalAdmin`, `ClassroomStaffAdmin`, `SessionWaitlistAdmin`, `ClassroomReportAdmin`, `CertificateAdmin`, `ClassReminderAdmin`, `ClassHolidayAdmin`, `NoticeAdmin`, `ClassQueryAdmin`, `NotificationAdmin`, `ReferralAdmin`, `ClassroomBanAdmin`.
- Use this section as the map for "which model has admin visibility" when debugging data issues via Django admin rather than the API.

---

## 15. Settings highlights (`settings.py`, ~750 lines, **project-level** — shared with `login`/`message`)

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
- `SIMPLE_JWT` — JWT auth config (also what `ws_auth.py` validates against for WebSockets).
- `CORS_ALLOWED_ORIGINS` / `CORS_ALLOW_ALL_ORIGINS = DEBUG and not CORS_ALLOWED_ORIGINS`.
- `SPECTACULAR_SETTINGS` — OpenAPI schema gen (dev-time, see `urls.py` §5).
- `DATA_UPLOAD_MAX_MEMORY_SIZE = 10MB`, `FILE_UPLOAD_MAX_MEMORY_SIZE = 500KB`, `FILE_UPLOAD_PERMISSIONS = 0o644`, `DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000`.
- `FCM_SERVICE_ACCOUNT_JSON_PATH` — push notifications (owned by the `message` app, reused here — see §10).
- `GOOGLE_CLIENT_ID`, `FREESOUND_API_KEY` — other-app config living in the same settings file.
- `REFERRAL_BONUS_COINS` (default 50), `REFERRAL_REDEEM_WINDOW_DAYS` (default 7) — signup-referral economy knobs (env-overridable).
- `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` — coin top-up payment gateway.
- `EMAIL_BACKEND`/`EMAIL_HOST`/`EMAIL_PORT`/`EMAIL_USE_TLS`/`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`/`DEFAULT_FROM_EMAIL` — SMTP.
- `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` (Redis), `CELERY_ACCEPT_CONTENT=["json"]`, `CELERY_TASK_SERIALIZER`/`CELERY_RESULT_SERIALIZER="json"`, `CELERY_TIMEZONE = TIME_ZONE`, `CELERY_TASK_ACKS_LATE = True`, `CELERY_TASK_REJECT_ON_WORKER_LOST = True`.
- `CELERY_BEAT_SCHEDULE` — **the actual source of truth for task cadence**, full table reproduced in §9. Includes both `liveclass.*` and `message.*` entries in one shared schedule dict.
- `MSG91_*` — SMS/WhatsApp provider config (see §10): `MSG91_AUTH_KEY`, `MSG91_SMS_SENDER_ID`, `MSG91_WHATSAPP_INTEGRATED_NUMBER`, `MSG91_WHATSAPP_TEMPLATE_NAME`.

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

---

## 17. Known "fix" notes baked into the code (worth remembering — don't re-break these)

1. `apps.py` must exist and be wired, or `signals.py` is dead code.
2. `CELERY_BEAT_SCHEDULE` must actually register a task, or it silently never runs even if fully implemented — `refresh_stale_enrolled_counts`, `reconcile_stuck_coin_purchases`, `run_auto_renewals`, `expire_unclaimed_gifts`, `send_notification_digests` **all had this exact bug once** (written, tested-looking, never scheduled).
3. `TeacherEarningsView`/`StudentProgressView`/`NotificationPreferenceView` are plain `APIView`s — `router.register()` never auto-wires a non-ViewSet; each needs its own explicit `path()` in `urls.py` (all three were missing this at least once historically).
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

---

## 18. Suggested "always check this" checklist for future work

When continuing work in a new chat, paste this file and say what you want changed. If a change touches:
- **Money/coins** → check `PassPurchase` (`charge_for_session`, `reverse`, `renew`, `sync_missed_charges`), `_charge_and_create_purchase` (views.py), `CoinTransaction`, the escrow-charge signal (`_charge_passes_for_completed_session`), and the related Celery sweeps (`expire_and_refund_passes`, `reconcile_stuck_coin_purchases`, `run_auto_renewals`, `expire_unclaimed_gifts`). Never let a new money-moving code path bypass `CoinTransaction` logging.
- **Realtime** → check `consumers.py`, `realtime.py`, `routing.py`, and whichever `views.py` action broadcasts the event (`broadcast_to_session`/`broadcast_to_user`).
- **Notifications** → check `notifications.py` (channel implementation), the specific `notify_*` task in `tasks.py`, and `NotificationPreference.allowed_channels_for` — and make sure any new push also creates a `create_notification()` bell row if it should be visible in-app, not just pushed.
- **Access control** → check the `_can_*`/`_access_level`/`has_access` helpers in `views.py` and `models.py` — keep them consistent, don't duplicate the logic in a new spot. Remember the `max_classes` cap and ban checks live inside `Classroom.has_access()`.
- **New Celery task** → it does nothing on a schedule until it's **also** added to `CELERY_BEAT_SCHEDULE` in `settings.py` (this exact bug has recurred at least 5 times in this codebase's history — see §17 item 2).
- **New plain `APIView`** (not a ViewSet) → it's unreachable until it also gets an explicit `path()` in `urls.py` (recurred at least 3 times — see §17 item 3).
- **New signal handler on `ClassSession`/`SessionParticipant`** → wrap each step in its own try/except, and defer anything that isn't needed for the `.save()` itself (LiveKit calls, `.delay()`, cross-row cleanup) into `transaction.on_commit()`.
- **File uploads** → new FileField needs both `MaxFileSizeValidator` and, if it's a plain `FileField` (not `ImageField`), a safelist `FileExtensionValidator`.
- **Error responses** → any new exception type raised in a view should either already be a DRF `APIException` (flows through `exceptions.py` automatically) or get an entry in `_CODE_BY_EXC` if it needs a specific machine-readable `code`.