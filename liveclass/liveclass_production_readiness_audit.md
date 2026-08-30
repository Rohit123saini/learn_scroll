# LiveClass App — Complete Reference & Production Readiness Doc

**Purpose of this file:** this is meant to be the ONLY document needed to
work on this app going forward — every model, every endpoint, every piece
of business logic, every fix already made, and every open item, so that
future work can start from this file alone without re-reading all 13
source files first. Whenever the code changes, this file should be
updated in the same pass (see "Maintenance rule" at the very bottom).

**App:** `liveclass` — Django + DRF backend for an online live-class
marketplace (LiveKit video, a coin-based wallet economy, Celery for
scheduled jobs). Consumed by a Flutter app.

**Files in scope:** `models.py`, `views.py`, `serializers.py`,
`urls.py`, `admin.py`, `signals.py`, `tasks.py`, `notifications.py`,
`exceptions.py`, `apps.py`, `livekit_utils.py`, `tests.py`,
`__init__.py`, **`chunked_upload_views.py`** (added in Pass 3 — see
§11). (`settings.py` is referenced throughout but was never part of
this upload — see §8.)

---

## Table of contents
1. [Architecture at a glance](#1-architecture-at-a-glance)
2. [Data model reference](#2-data-model-reference) — every model, its fields, its business-logic methods
3. [API surface](#3-api-surface) — every endpoint, grouped by resource
4. [Access-control model](#4-access-control-model) — the 4 user tiers and the helper functions that gate everything
5. [Money flow: coins, passes, escrow, refunds](#5-money-flow-coins-passes-escrow-refunds)
6. [Signals — automatic side effects](#6-signals--automatic-side-effects)
7. [Celery tasks](#7-celery-tasks)
8. [Cross-cutting infra](#8-cross-cutting-infra) — exceptions, notifications, admin, apps.py
9. [Test coverage map](#9-test-coverage-map)
10. [Settings this app requires](#10-settings-this-app-requires-not-in-this-upload)
11. [Change log](#11-change-log) — every fix made, in order, with rationale
12. [Open action items before deploy](#12-open-action-items-before-deploy)
13. [Verification methodology](#13-verification-methodology--what-was-and-wasnt-actually-run)

---

## 1. Architecture at a glance

```
Flutter app
   │  HTTPS (JWT via djangorestframework-simplejwt, presumed)
   ▼
urls.py ──router──▶ views.py (27 ViewSets + 4 plain APIViews)
   │                    │
   │                    ├─▶ serializers.py (validation + shape)
   │                    ├─▶ models.py (business logic lives ON models:
   │                    │      has_access(), charge_for_session(), reverse(),
   │                    │      is_valid(), can_be_deleted(), etc.)
   │                    ├─▶ livekit_utils.py (LiveKit room/token/egress calls)
   │                    └─▶ exceptions.py (liveclass_exception_handler —
   │                           EVERY error response gets normalized to
   │                           {"detail", "code", ["errors"]})
   │
signals.py (post_save/pre_save/post_delete on ClassSession,
   SessionParticipant, PassPurchase, Classroom, ClassroomReport)
   │  fires synchronously inside .save(), each step isolated in its own
   │  try/except, side effects deferred to transaction.on_commit()
   ▼
tasks.py (22 @shared_task; 5 are CELERY_BEAT_SCHEDULE cron jobs, 17 are
   fire-and-forget .delay() notification-dispatch tasks)
   │
   ▼
notifications.py (send_notification() — push via message.push_utils,
   email via Django SMTP, sms is a documented no-op) — NEVER raises.
```

**Payment model:** no card/UPI. Everything is an internal coin wallet
(`User.coin`, on the `login` app's custom User model — not in this
upload). A pass is bought with coins; a teacher is paid out of those
coins one calendar day at a time as classes actually happen (escrow —
see §5).

**Registered app:** `liveclass.apps.LiveclassConfig`, which imports
`liveclass.signals` in `.ready()` — this is what makes every
`@receiver` in `signals.py` actually run (Django never auto-imports
`signals.py` just because it exists).

---

## 2. Data model reference

For every model below: fields (name:type), then business-logic methods
with what they do. `__str__` omitted throughout (cosmetic only).
Standard `TextChoices` enums are named but not always expanded — see
model source for the display-label text if needed.

### `Classroom`
`classroom_type, organisation_name, teacher(FK User), title, subject,
description, cover_image(Image), language, whiteboard_enabled,
screen_share_enabled, chat_enabled, recording_enabled, max_participants,
rating_avg(Decimal), rating_count, enrolled_count, share_count, is_active,
is_deleted, deleted_at, is_flagged, created_at, updated_at`
- `ClassroomType`: `INDIVIDUAL` / `ORGANISATION` — org type unlocks the
  co-teacher/moderator/TA staff bypass everywhere (see §4).
- **`has_access(user) -> bool`** — the TIGHT gate: does this user hold a
  currently valid (unexpired, under `max_classes` cap) pass right now?
  Teacher always True. **Checks `ClassroomBan` first** (banned → False
  even if a stray active purchase slipped through). This is what
  actually lets someone into a live session.
- **`is_enrolled(user) -> bool`** — the BROAD gate: has this user EVER
  held a successful purchase (active OR expired)? Unlocks general
  classroom content (materials/notices/holidays/assignments/etc.) but
  NOT live-session entry.
- **`can_be_deleted() -> (bool, reason)`** — hard DELETE only allowed if
  (a) classroom is `MIN_AGE_BEFORE_DELETE_DAYS`+ old, AND (b) no student
  holds an active, unexpired, paid (`coins_spent > 0`) pass. Otherwise
  use `.../close/` (refunds everyone, then deactivates).
- **`sync_flag_status()`** — recomputes `is_flagged` from the CURRENT
  count of pending `ClassroomReport`s vs `AUTO_FLAG_THRESHOLD`. Can flip
  back to `False` (fixed a bug where it could only ever turn True).
  Called from `ClassroomReportViewSet.review()` after every status change.
- **`refresh_rating()`** — recompute `rating_avg`/`rating_count` from
  `ClassroomReview` rows. Auto-wired via signal; also callable manually.
- **`refresh_enrolled_count()`** — recompute distinct-students-with-a-
  valid-pass. Auto-wired on every `PassPurchase` save; goes stale on
  pure *expiry* (no save happens) — that's what
  `tasks.refresh_stale_enrolled_counts` sweeps for.
- **`weekly_timing_summary()`** — human string like "Mon, Wed, Fri 6:00
  PM (60 min)" built from active `ClassSchedule` rows.
- **`upcoming_holidays(days_ahead=30)`** — off-days in the next N days.
- **`record_share()`** *(Pass 6)* — bumps `share_count` via a race-safe
  `F()`-expression `.update()` (not read-modify-write), then
  `refresh_from_db()`s just that field. Called once per `ClassroomShare`
  row created, any channel.
- **`share_urls() -> (web_url, deep_link)`** *(Pass 6)* — builds the
  shareable web link and in-app deep link for this classroom from
  `getattr(settings, "LIVECLASS_WEB_BASE_URL"/"LIVECLASS_DEEP_LINK_SCHEME",
  <default>)` — see §10.

### `ClassSchedule` (recurrence rule → generates `ClassSession` rows)
`classroom(FK), recurrence_type, days_of_week(JSON), day_of_month,
start_date, end_date, start_time, duration_minutes, timezone, is_active,
created_at`
- `RecurrenceType`: weekly (uses `days_of_week`) / monthly (uses
  `day_of_month`) / etc. — see model source.
- **`is_off_on(date) -> bool`** — True if `date` is a holiday for this
  schedule specifically, OR classroom-wide (`ClassHoliday.schedule=None`
  applies to every schedule under that classroom).

### `ClassSession`
`classroom(FK), schedule(FK, nullable), room_id(UUID), scheduled_start,
scheduled_end, actual_start, actual_end, status, recording_url,
egress_id, whiteboard_snapshot(JSON), created_at`
- `Status`: `SCHEDULED / LIVE / COMPLETED / CANCELLED`
- **`is_joinable(is_host=False) -> bool`** — time-window check. Students
  can join a bit before start till scheduled end; the window check is
  explicitly scoped to non-hosts (host/staff exempt).

### `ClassPass`
`classroom(FK), pass_type, title, price(Decimal), validity_days,
max_classes(nullable — null = unlimited), is_active, created_at`
- `PassType`: e.g. `MONTHLY`, `DAILY`, others — see model source.

### `PassPurchase` — the core money object
`student(FK), class_pass(FK), coupon(FK, nullable), payment_method,
amount_paid(Decimal), coins_spent, transaction_id, status, purchased_at,
expires_at, classes_attended, is_active, per_day_rate(Decimal),
coins_released, last_charge_date, created_at`
- `Status`: `PENDING / SUCCESS / FAILED / REFUNDED`
- `PaymentMethod`: `COIN_WALLET` (mandatory coin debit) / `FREE` (price
  0 after coupon — no debit). **No card/UPI path exists in this app.**
- **`is_valid() -> bool`** — SUCCESS + active + unexpired + under
  `max_classes` cap (the Python-level equivalent of what
  `Classroom.has_access()` checks via a single query).
- **`remaining_balance -> int`** (property) — `coins_spent -
  coins_released`. What's still in escrow, un-paid-to-teacher.
  Exactly what `reverse()` refunds.
- **`charge_for_session(session) -> PassDailyCharge | None`** — releases
  ONE day's worth of escrow to the teacher, for the calendar date
  `session.actual_end` (or `scheduled_start`) falls on. Idempotent via
  `PassDailyCharge`'s unique `(purchase, date)` constraint +
  `get_or_create`. No-ops if: purchase not SUCCESS/active, session's
  classroom doesn't match, date outside `[purchased_at, expires_at]`, or
  `remaining_balance <= 0`. **Must be called with the purchase row
  already `select_for_update()`-locked, inside `transaction.atomic()`.**
  Last chargeable day gets the full remainder (handles rounding).
- **`sync_missed_charges() -> int`** — catch-up sweep: charges this
  purchase for every COMPLETED session in its window not yet charged.
  Normally the `ClassSession` post_save signal does this in real time;
  this is the backstop for a session that changed status while a signal
  step failed, or a purchase created after sessions already happened.
- **`reverse(notify=True)`** — cancels the pass, refunds
  `remaining_balance` (never the full `coins_spent` — the teacher keeps
  what they already legitimately earned for days actually taught). Sets
  `is_active=False`, status → `REFUNDED`. Releases the coupon's
  `used_count` back ONLY if `coins_released == 0` (pass never paid the
  teacher a single day — can't cancel-and-reuse a coupon after a real
  transaction happened on it).

### `PassDailyCharge` — escrow release ledger
`purchase(FK), session(FK, SET_NULL), date, amount, created_at`
- `UniqueConstraint(purchase, date)` — the actual idempotency guard, not
  just an audit log.

### `ClassJoinRequest` — the ONLY door into a classroom for a non-owner
`classroom(FK), class_pass(FK), student(FK), coupon_code, message,
status, pass_purchase(OneToOne, nullable), decided_by(FK), decision_note,
decided_at, requested_at`
- `Status`: `PENDING / ACCEPTED / REJECTED / CANCELLED`
- A student never buys a pass directly — they raise a request; the
  teacher/co-teacher/moderator `accept()`s it (which is what actually
  debits coins and creates the `PassPurchase`) or `reject()`s it (no
  charge). The student can `cancel()` their own still-pending request.

### `BreakoutRoom`
`session(FK), room_number, created_at`

### `SessionParticipant`
`session(FK), user(FK), role, joined_at, left_at, kicked_at,
hand_raised_at, breakout_room(FK, SET_NULL)`
- `Role`: `HOST / CO_HOST / STUDENT` (moderator-tier roles resolve to
  host-equivalent — see `_resolve_session_roles` in views.py).
- No FK back to the `PassPurchase` that granted access (documented known
  limitation — attendance-credit re-derives "the currently valid capped
  purchase" heuristically; fine for one-active-pass-per-classroom, would
  need a schema addition for multiple concurrent passes).

### `ClassMaterial`
`classroom(FK), session(FK, nullable), uploaded_by(FK), title,
material_type, file(File), external_link(URL), uploaded_at`

### `ChatMessage`
`session(FK), sender(FK), message(Text), sent_at, is_deleted` (soft
delete only)

### `LivePoll` / `PollResponse`
`LivePoll: session(FK), created_by(FK), question, options(JSON),
is_active, created_at, closed_at`
`PollResponse: poll(FK), student(FK), selected_option_index, answered_at`

### `Assignment` / `AssignmentSubmission`
`Assignment: classroom(FK), session(FK, nullable), title, description,
attachment(File), due_date, max_score, created_at`
`AssignmentSubmission: assignment(FK), student(FK), file(File),
submitted_at, score, feedback, graded_at`
- **`AssignmentSubmission.is_late`** (property) — `submitted_at >
  assignment.due_date`.

### `ClassroomReview` / `ClassroomWishlist`
`ClassroomReview: classroom(FK), student(FK), rating(1–5), comment,
created_at`
`ClassroomWishlist: user(FK), classroom(FK), created_at`

### `ClassroomShare` *(Pass 6 — see §11)*
`classroom(FK), shared_by(FK User), shared_with(FK User, nullable),
channel, created_at`
- `Channel`: `IN_APP / WHATSAPP / SMS / EMAIL / COPY_LINK / OTHER`
- One row per share attempt, whatever the channel — the audit trail
  behind `Classroom.share_count`. `shared_with` is only ever set for an
  `IN_APP` share (a specific platform user was the target); every
  outside-the-app channel leaves it `None` since the actual send happens
  on the user's device, outside this app.
- Written and its parent `share_count` bumped from
  `ClassroomViewSet.share`; read back (aggregated by channel + a recent
  slice) from `ClassroomViewSet.share_stats`.

### `Coupon`
`classroom(FK), created_by(FK), code, discount_percent, discount_amount,
valid_from, valid_until, max_uses, used_count, is_active`
- **`is_valid() -> bool`** — active, within date window, under
  `max_uses`. Percent and amount discounts can stack, floored at 0 (see
  `_charge_and_create_purchase` in views.py for the actual math + the
  round-half-up rule, e.g. 49.6 → 50 not 49).

### `CoinTransaction` — read-only ledger, never mutated after creation
`user(FK), txn_type, reason, amount, balance_after, reference_id,
created_at`
- `TxnType`: `CREDIT / DEBIT`
- `Reason`: `PASS_PURCHASE / CLASS_EARNING / REFERRAL_BONUS / REFUND /
  ADMIN_ADJUSTMENT / TOPUP`

### `Referral`
`referrer(FK User), referred(OneToOne User), bonus_amount, created_at`
- `referred` is OneToOne (not plain FK) — a user can redeem AT MOST ONCE
  ever; that's the actual anti-farming guard. No cap on `referrer` — one
  user can refer many people.
- Module-level helpers: **`referral_code_for_user(user_id) -> str`** /
  **`referral_code_to_user_id(code) -> int|None`** — a reversible
  encode/decode scheme, no DB column needed for the code itself.

### `ClassroomBan` — permanent, classroom-wide (distinct from
`SessionParticipant.kicked_at`, which only blocks re-entry to ONE session)
`classroom(FK), student(FK), banned_by(FK, SET_NULL), reason,
created_at`
- `unique_together(classroom, student)`
- Checked by: `ClassJoinRequestViewSet.perform_create`,
  `_perform_join` (views.py), and `Classroom.has_access()`.
- Creating a ban (via `ClassroomViewSet.ban`) ALSO best-effort: kicks the
  student from any live session, cancels any pending join request, and
  reverses/refunds any active `PassPurchase` for that classroom — a ban
  means "gone", not "banned but still holding a pass they can't use".

### `ClassroomStaff`
`classroom(FK), user(FK), role, added_at`
- `Role`: `CO_TEACHER / MODERATOR / TA`

### `SessionWaitlist`
`session(FK), student(FK), joined_at, notified`
- FCFS queue for a session at capacity. Promotion is compare-and-swap
  (`.filter(notified=False).update(notified=True)`), not read-then-save
  — the actual race guard (see §6).

### `ClassroomReport`
`classroom(FK), reported_by(FK), reason, description, status,
reviewed_by(FK), admin_note, reviewed_at, created_at`
- `Status`: `PENDING / REVIEWED / ACTION_TAKEN / DISMISSED`
- `AUTO_FLAG_THRESHOLD` pending reports → `Classroom.is_flagged=True`
  (see `sync_flag_status`) → hidden from Explore while under review.

### `Certificate`
`classroom(FK), student(FK), certificate_id(UUID), certificate_file(File),
issued_at`

### `ChunkedUpload` — large-file uploads assembled in pieces (Pass 3)
`upload_id(UUID, unique), user(FK), purpose, original_file_name,
file_extension, total_chunks, total_size, extra_data(JSON), status,
error_message, created_at, updated_at`
- **Why it exists:** `DATA_UPLOAD_MAX_MEMORY_SIZE` is 10MB (see §10), but
  `ClassMaterial.file` allows up to 100MB and `Assignment.attachment` /
  `AssignmentSubmission.file` allow up to 50MB — all three would be
  rejected outright as a single request. A client splits the file into
  small pieces, uploads them one at a time via
  `chunked_upload_views.py`, and the server assembles + validates the
  final file only once every piece has arrived.
- `Purpose`: `cover_image` (updates `Classroom.cover_image`) /
  `material` (creates a `ClassMaterial` row) / `assignment_attachment`
  (updates `Assignment.attachment`) / `submission_file` (creates an
  `AssignmentSubmission` row).
- `Status`: `IN_PROGRESS → PROCESSING → COMPLETED`, or `→ FAILED` /
  `ABORTED` / `EXPIRED`. `PROCESSING` is a short-lived atomic-claim
  state (`chunked_upload_complete` conditionally
  `UPDATE ... WHERE status=IN_PROGRESS` before doing any file I/O) that
  stops two concurrent `complete()` calls for the same `upload_id` from
  double-assembling the file or double-creating the target row.
- `extra_data` (JSON) — carries whatever the target model needs at
  completion time (e.g. `{"classroom_id": 5}` for `cover_image`,
  `{"assignment_id": 9}` for `submission_file`) — captured once at
  `init()` and permission-checked again at `complete()` (access can
  legitimately change mid-upload, e.g. a pass expiring).
- The assembled file is re-validated through the SAME
  `MaxFileSizeValidator`/`FileExtensionValidator` the target field
  already declares (via `full_clean()`) — chunking never bypasses
  those, it only spreads one upload across many small requests.
  `cover_image` additionally gets a Pillow decode check at completion,
  since assembly writes straight to disk and bypasses `ImageField`'s
  normal upload-time image validation.
- Temp chunks live under `settings.CHUNKED_UPLOAD_TMP_ROOT` —
  deliberately outside `MEDIA_ROOT` so a half-uploaded, not-yet-
  validated file can never become reachable via the `/media/` URL
  mid-upload.
- Swept by `tasks.cleanup_stale_chunked_uploads` (see §7) — see that
  entry for the staleness/retention rules.

### `ClassReminder`
`session(FK), user(FK), remind_at, channel, is_sent`
- `Channel`: `push / email / sms`

### `ClassHoliday`
`classroom(FK), schedule(FK, nullable — null = classroom-wide), date,
reason, created_by(FK), created_at`

### `Notice`
`classroom(FK), posted_by(FK), title, message, priority, is_pinned,
created_at, expires_at`
- `Priority`: e.g. `LOW/NORMAL/HIGH` — see model source.
- **`is_expired`** (property).

### `ClassQuery` — student doubts
`classroom(FK), session(FK, nullable), asked_by(FK), question, status,
answer, answered_by(FK), answered_at, created_at`
- `Status`: `PENDING / ANSWERED` (approx — see model source)

### `Notification` — in-app bell row
`recipient(FK), notif_type, title, message, classroom(FK, SET_NULL),
session(FK, SET_NULL), is_read, created_at, read_at`
- `NotifType` (23 values total) — corrected here to match the actual
  model source, which had drifted from this list even before this pass:
  `JOIN_REQUEST_RECEIVED/ACCEPTED/REJECTED, PASS_REFUNDED,
  SESSION_REMINDER, ASSIGNMENT_GRADED, QUERY_ANSWERED,
  CERTIFICATE_ISSUED, WAITLIST_PROMOTED, CLASSROOM_FLAGGED,
  NOTICE_POSTED, SESSION_LIVE, SESSION_CANCELLED, ASSIGNMENT_POSTED,
  SUBMISSION_RECEIVED, STAFF_ADDED, REVIEW_POSTED, REPORT_REVIEWED,
  WITHDRAWAL_APPROVED, WITHDRAWAL_REJECTED, WITHDRAWAL_PAID` (these
  three were added in Pass 5 but this list was never updated to match —
  fixed as part of this pass), **`CLASSROOM_SHARED`** *(new, Pass 6)*,
  `GENERIC`
- **`mark_read()`** — sets `is_read=True` + `read_at=now()`.
- Module helpers: **`create_notification(...)`** (single row) /
  **`create_bulk_notifications(...)`** (many recipients at once) — every
  notification in the app goes through one of these two.

### Module-level signal receivers defined directly in `models.py`
(separate from `signals.py` — these run unconditionally the moment
`models.py` is imported, no `apps.py` wiring needed):
`_bump_classroom_list_cache`, `_sync_classroom_rating`,
`_sync_classroom_enrolled_count`, `_stash_previous_session_status`,
`_charge_passes_for_completed_session` (the real-time trigger for
`PassPurchase.charge_for_session`), `_auto_flag_classroom`.

---

## 3. API surface

All routes prefixed `/liveclass/` (see project's root `urls.py`).
Router basenames in `urls.py` map 1:1 to the ViewSets in §2/§ViewSet
list below. Full per-endpoint detail already lives as the docstring at
the top of `urls.py` — this is the condensed index; **read `urls.py`'s
own header comment for the authoritative, always-in-sync version**
(drf-spectacular, if installed, also serves this live at
`schema/docs/`).

| Resource | Base path | Notable actions |
|---|---|---|
| Classrooms | `classrooms/` | `close/`, `has-access/`, `my-pass/`, `start-or-join/`, `stats/`, `ban/`, `bans/`, `unban/{student_id}/`, `recordings/`, `share/`, `share-stats/` |
| Classroom reports | `classroom-reports/` | `{id}/review/` (staff) |
| Schedules | `schedules/` | — |
| Sessions | `sessions/` | `join/`, `token/`, `end/`, `kick/{user_id}/`, `mute/{user_id}/`, `hand/`, `hand/{user_id}/lower/`, `start-recording/`, `stop-recording/`, `breakout/` (GET+POST), `breakout/assign/`, `breakout/close/` |
| Passes | `passes/` | teacher-only edit/delete rules (see §5) |
| Join requests | `join-requests/` | `{id}/accept/`, `{id}/reject/`, `{id}/cancel/` |
| Pass purchases | `pass-purchases/` | `{id}/refund/` (teacher/staff), `{id}/cancel/` (student self-service) |
| Participants | `participants/` | `{id}/leave/` |
| Materials | `materials/` | — |
| Chat messages | `chat-messages/` | soft-delete only |
| Polls | `polls/` | `{id}/vote/`, `{id}/close/` |
| Assignments / Submissions | `assignments/`, `submissions/` | `submissions/{id}/grade/` |
| Reviews | `reviews/` | — |
| Wishlist | `wishlist-classrooms/` | — |
| Coupons | `coupons/` | `validate/` (?code=, checks without spending) |
| Coin transactions | `coin-transactions/` | `balance/` (real `User.coin`) |
| Staff | `staff/` | co-teacher/moderator/TA management |
| Waitlist | `waitlist/` | `{id}/promote/` (host) |
| Certificates | `certificates/` | — |
| Reminders | `reminders/` | — |
| Holidays | `holidays/` | — |
| Notices | `notices/` | `{id}/pin/` |
| Queries (doubts) | `queries/` | `{id}/answer/` |
| Notifications | `notifications/` | `unread-count/`, `{id}/mark-read/`, `mark-all-read/` |
| **Referrals** | `referrals/` | `my-code/`, `redeem/` |
| Dashboard | `dashboard/` (plain path) | single-call home-screen summary |
| **Teacher earnings** | `my-earnings/` (plain path) | `?classroom=<id>` scoping |
| LiveKit webhook | `livekit-webhook/` (plain path) | unauthenticated, own signature check |
| Health check | `healthz/` (plain path) | unauthenticated, DB+cache probe |
| Schema/docs | `schema/`, `schema/docs/` | only if `drf-spectacular` installed |
| **Chunked upload** (Pass 3) | `uploads/chunked/` (plain paths, not a router basename — see `chunked_upload_views.py`) | `init/`, `chunk/`, `complete/`, `abort/` — see §2 `ChunkedUpload` for the 4 supported `purpose` values |

**Throttle scopes in use** (must have matching rates in
`DEFAULT_THROTTLE_RATES`): `session_join`, `session_token`,
`coupon_validate`, `chat_message_create`, and (Pass 3)
`chunked_upload_init`, `chunked_upload_chunk`, `chunked_upload_complete`.

**Explore/listing query params** on `classrooms/` (GET, list): `?search=`
(free text across title/subject/description/teacher name), `?language=`,
`?subject=` (exact facet), `?min_rating=`, `?min_price=`/`?max_price=`
(matches if classroom has ANY active pass in range — `.distinct()`
applied), `?mine=` (own classrooms). Malformed `min_rating`/price values
are silently ignored, not 400'd.

**`classrooms/{id}/share/`** (POST, Pass 6) — one endpoint, two modes,
picked by whether `to_user_id` is present in the body:
- **In-app** (`to_user_id` given): creates an in-app `Notification`
  (`CLASSROOM_SHARED`) synchronously + queues a push via
  `notify_classroom_shared`. `channel` is forced to `in_app` regardless
  of what was sent.
- **Outside the app** (`to_user_id` omitted): nothing is sent from the
  server — returns `web_url`/`deep_link`/`share_text` for the client to
  hand to the OS's native share sheet (WhatsApp/SMS/email/etc.).
  `channel` here is just an analytics label, not something the endpoint
  acts on.

Either mode logs one `ClassroomShare` row and bumps `Classroom.
share_count`. Gated by the same visibility rule as `retrieve()` (via
`get_object()`) — if you can't see the classroom, you can't share it.
Self-share (`to_user_id == request.user.id`) is rejected. See §11 Pass 6.

**`classrooms/{id}/share-stats/`** (GET, Pass 6) — teacher/co-teacher/
moderator only (`_can_manage_classroom`). Returns `share_count`, a
`by_channel` breakdown, and the 50 most recent `ClassroomShare` rows.
Not paginated — deliberately a small summary payload, not a bare list.

---

## 4. Access-control model

Four tiers, from most to least privileged (`AccessLevel` in views.py) —
used by `my-pass/` and everywhere else that needs one authoritative
answer to "who is this user, for this classroom":
- **ADMIN** — teacher, or co-teacher/moderator/org-staff — full manage rights
- **ACTIVE** — currently valid (unexpired) pass — full access, can enter a live session
- (two more narrower tiers below that — expired-pass/enrolled-only, and no-access)

Core helper functions (views.py):
- **`_can_manage_classroom(classroom, user)`** — teacher, OR
  co-teacher/moderator staff role, OR (for ORGANISATION-type classrooms
  only) any org staff role at all. Gates: notices, off-days, ban/unban,
  session end/kick/mute/recording/breakout control.
- **`_can_view_classroom_internals(classroom, user)`** —
  `_can_manage_classroom` OR `classroom.is_enrolled(user)` (ever held ANY
  pass, active or expired). Gates: schedule, sessions list, materials,
  notices, doubts, assignments, recordings. Deliberately broader than
  room-entry access.
- **`_has_room_access`** (tighter, used by join()/token()/chat/polls) —
  wraps `Classroom.has_access()`.

**`ClassroomBan` enforcement points** (all three independently check —
belt and suspenders): `ClassJoinRequestViewSet.perform_create`,
`_perform_join`, `Classroom.has_access()`.

**`classrooms/{id}/share/`** *(Pass 6)* deliberately introduces no new
tier — it reuses `get_object()`'s existing visibility rule (the same
queryset `retrieve()` uses), so "can you share it" is always exactly
"can you already see it". `share-stats` is gated by the existing
`_can_manage_classroom`.

---

## 5. Money flow: coins, passes, escrow, refunds

1. Student raises a `ClassJoinRequest` against a specific `ClassPass`
   (no charge yet).
2. Teacher/co-teacher/moderator `accept()`s it →
   `_charge_and_create_purchase(student, class_pass, coupon_code)` runs
   INSIDE `transaction.atomic()` with `select_for_update()` on the
   coupon and the student's wallet row:
   - Balance checked (`user.coin < coins_spent`) BEFORE debiting — can
     never go negative via this path.
   - `payment_method` is derived server-side from final price (never
     accepted from the client) — 0 → `FREE`, else → `COIN_WALLET`.
   - `PassPurchase` row created only if the coin debit actually
     succeeds.
3. As each session happens, `ClassSession` post_save signal (in
   `models.py`) calls `PassPurchase.charge_for_session(session)` for
   every purchase on that classroom — releases ONE day's worth of
   escrow to the teacher (idempotent, see §2).
4. Ending a pass early — three paths, all converge on
   `PassPurchase.reverse()`:
   - Teacher `classrooms/{id}/close/` — refunds EVERY active purchase on
     that classroom, then deactivates it.
   - Teacher/staff `pass-purchases/{id}/refund/` — one purchase.
   - Student `pass-purchases/{id}/cancel/` — self-service, own purchase
     only.
   - `ClassroomViewSet.ban` — refunds as a side effect of banning.
   - `tasks.expire_and_refund_passes` — sweeps purchases whose
     `expires_at` has passed and still have `remaining_balance > 0`
     (nothing else ever touches an expired row otherwise — this is what
     stops a student quietly losing unclaimed escrow coins forever).
   - `reverse()` never claws back what the teacher already earned
     (`coins_released`), and only refunds the coupon's `used_count` if
     `coins_released == 0` (no real transaction happened on that coupon
     redemption yet).
5. **Every money-mutating path is wrapped in `transaction.atomic()` +
   `select_for_update()`** on the right rows (purchase, teacher,
   student, coupon, classroom) — concurrency-safe by design, not by luck.
6. `ClassPass` edit/delete rules (once ever purchased): DELETE refused
   outright (use `is_active=False` to pause instead — existing holders
   keep access); PATCH refused for any change that would retroactively
   shrink what active holders paid for (price can't rise, validity/
   max_classes/pass_type can't shrink) while an active paid purchase is
   outstanding.

**Referral bonus flow** (separate coin flow, not pass-related):
`ReferralViewSet.redeem` locks BOTH wallets in a fixed, sorted order
(`sorted([referrer.id, user.id])`) before crediting either — this fixed
lock ordering is what prevents a lock-ordering deadlock between two
concurrent redeems touching the same two users in opposite order.
Credits `REFERRAL_BONUS_COINS` to both referrer and redeemer, only once
per redeemer ever (`Referral.referred` OneToOne), only within
`REFERRAL_REDEEM_WINDOW_DAYS` of the redeemer's signup.

---

## 6. Signals — automatic side effects

`signals.py` (needs `apps.py`'s `ready()` import to actually run —
see §8) wires 4 receivers:

1. **`ClassSession` pre_save** — stashes the pre-save status (post_save
   doesn't get the old row; standard Django diffing pattern).
2. **`ClassSession` post_save** — on a FRESH transition into
   `COMPLETED`/`CANCELLED`: tears down the LiveKit room, closes any open
   polls, force-checks-out remaining participants, clears stale waitlist
   entries, stamps `actual_end` if unset.
3. **`ClassSession` post_delete** — best-effort LiveKit teardown if a
   session row is deleted outright (e.g. from Django admin).
4. **`SessionParticipant` pre_save/post_save** — on `left_at` going
   `None → timestamp`: promotes the next waitlisted student (FCFS,
   compare-and-swap update, not read-then-save — race-safe for two
   simultaneous leaves), and best-effort +1's `classes_attended` on the
   matching capped `PassPurchase`.

**Hardening pattern used throughout:** every step is its own
try/except (one bad step — a flaky LiveKit call, a stale FK — is logged
and skipped, never bubbles out of `.save()` and 500s an unrelated
request). Anything not needed for the `.save()` call itself to be
correct (LiveKit calls, Celery dispatch) is deferred to
`transaction.on_commit()` — avoids a worker racing ahead of the DB
commit, and avoids side effects surviving a rolled-back transaction.

---

## 7. Celery tasks

24 tasks total in `tasks.py`, all `@shared_task(name="liveclass....")`.
**6 are on `CELERY_BEAT_SCHEDULE`** (cron jobs); **18 are fire-and-forget
`.delay()` notification dispatchers** called from views.py/signals.py.

### Scheduled (beat) jobs
| Task | What it does |
|---|---|
| `generate_upcoming_sessions` | Turns each active `ClassSchedule`'s recurrence rule into real `ClassSession` rows for the next window. Safe to re-run (skips existing/holiday dates). |
| `auto_complete_overdue_sessions` | Force-completes any `SCHEDULED`/`LIVE` session well past `scheduled_end` — catches a teacher who forgot `/end/`. Saves rows individually (not `.update()`) so the post_save signal still fires. |
| `send_due_reminders` | Fires every due, unsent `ClassReminder`. Marks `is_sent` regardless of delivery success — never double-processes a row. |
| `refresh_stale_enrolled_counts` | Sweeps classrooms whose `enrolled_count` has drifted stale from passes quietly *expiring* (expiry never triggers a save, so the normal signal never fires). |
| `expire_and_refund_passes` | The actual "student loses money" gap-closer — sweeps purchases past `expires_at` with leftover `remaining_balance` and reverses them (nothing else ever calls `reverse()` on a purely-expired row). |
| `cleanup_stale_chunked_uploads` (Pass 3) | Sweeps `ChunkedUpload` rows stuck `IN_PROGRESS`/`PROCESSING` with no chunk activity for 6h (`CHUNKED_UPLOAD_STALE_AFTER`) → deletes their temp chunk dir, marks `EXPIRED`. Also purges terminal-status rows older than 14 days (`CHUNKED_UPLOAD_ROW_RETENTION_DAYS`), and defensively removes any orphan directory under `CHUNKED_UPLOAD_TMP_ROOT` with no matching DB row at all. Runs hourly. |

All 6 have intervals shorter than their own lookback/staleness windows
(no gap where a batch could be missed).

### Notification dispatchers (all `.delay()`'d, never called inline)
`notify_waitlist_promotion`, `notify_classroom_shared` *(Pass 6)*,
`notify_purchase_refunded`,
`notify_classroom_flagged`, `notify_session_auto_completed`,
`notify_join_request_received/accepted/rejected`,
`notify_assignment_graded`, `notify_certificate_issued`,
`notify_notice_posted`, `notify_session_live`,
`notify_session_cancelled`, `notify_assignment_posted`,
`notify_submission_received`, `notify_staff_added`,
`notify_review_posted`, `notify_report_reviewed`,
`notify_query_answered` — each pairs with a `Notification.NotifType`
value and calls `notifications.send_notification()`. (Withdrawal
notifications are in-app only, via `create_notification()` directly in
`views.py` — no matching Celery push task exists for those three.)

---

## 8. Cross-cutting infra

### `exceptions.py` — `liveclass_exception_handler`
Registered via `REST_FRAMEWORK["EXCEPTION_HANDLER"]` in `settings.py`
(referenced at line ~681, per original audit — settings.py not in this
upload). Normalizes EVERY error response to:
```json
{"detail": "Human-readable message.", "code": "validation_error", "errors": {"field": ["..."]}}
```
`errors` key present only for field-level validation errors. Handles:
`PermissionDenied`, `ValidationError` (string/list/dict/nested-dict/
list-of-dicts — recurses arbitrary depth via `_first_message`), Django's
own `Http404`/`PermissionDenied`, unhandled `IntegrityError` (→ clean
409, not a raw 500), and `LiveKitError` (a real `rest_framework.
exceptions.APIException` subclass, not a bare `Exception` — flows
through this handler like any other DRF exception instead of being
hand-built as a `Response` at each call site).

Stable `code` values a frontend can switch on: `validation_error,
permission_denied, not_found, authentication_failed, not_authenticated,
throttled, method_not_allowed, parse_error, livekit_error, conflict,
error` (fallback).

### `notifications.py` — `send_notification(user, title, message, channel="push", data=None)`
**Never raises** — a push/email/SMS failure can never break a DB
transaction or crash a Celery task; returns `True`/`False`.
- `push` — delegates to the existing `message` app's Firebase wiring
  (`message.push_utils.send_push_to_users`) via a LAZY import — a
  missing/renamed module degrades to a logged warning, not a startup
  crash (this import chain runs very early, during app loading).
- `email` — fully wired, uses `django.core.mail.send_mail` +
  `settings.DEFAULT_FROM_EMAIL`.
- `sms` — **documented no-op.** Logs and returns `False`. No provider
  wired. Pick one (Twilio/MSG91/etc.) and fill in `_send_sms` when
  needed — same function signature, no call-site changes required.

### `admin.py` — Django admin registrations
All models registered with `list_display`/`search_fields`/
`autocomplete_fields`/`readonly_fields` (and `date_hierarchy` where a
primary date field exists). `ClassroomBan` and `Referral` were added in
this pass (see §11) — `ReferralAdmin` is read-mostly since its rows are
tied 1:1 to already-created `CoinTransaction` entries.

### `apps.py` — `LiveclassConfig.ready()`
Imports `liveclass.signals` — the ONLY thing that makes every
`@receiver` in `signals.py` actually fire in a real deployment. The
signals defined directly at the bottom of `models.py` (rating sync,
enrolled_count sync, auto-flag) don't need this — they register the
moment `models.py` itself is imported, which Django always does.

### `livekit_utils.py`
Wraps the `livekit-api` package: `ensure_room`, `generate_livekit_token`,
`remove_participant`, `end_room`, egress start/stop, webhook signature
verification (`verify_webhook_event`). `LiveKitError` is an
`APIException` subclass (see `exceptions.py` above). Messages returned
to clients are generic ("service temporarily unavailable") — internal
`.env` values are never leaked in the API response, only logged
server-side.

---

## 9. Test coverage map

`tests.py` — `python manage.py test liveclass`. Shared fixtures in
`LiveClassTestBase` (`self.teacher`, `self.student`, `self.other_teacher`,
`self.classroom` (max_participants=2), `self.other_classroom`,
`self.class_pass` (10-day, 100 coins, per_day_rate=10 exactly)).
LiveKit calls and `_safe_delay` (Celery dispatch) are patched module-wide
— no test touches the network or a broker.

| # | Test class | Covers |
|---|---|---|
| 1 | `CouponValidityTests` | `Coupon.is_valid()` + classroom scoping |
| 2 | `ChargeAndCreatePurchaseTests` | `_charge_and_create_purchase` — discount math/rounding, insufficient balance, free passes, coupon bookkeeping |
| 3 | `EscrowChargeForSessionTests` | `charge_for_session()` — per-day release, idempotency, validity-window edges |
| 4 | `ReverseRefundTests` | `reverse()` — refund math, no teacher clawback, coupon-slot release rule |
| 5 | `JoinRequestAcceptViewTests` | `join-requests/{id}/accept/` HTTP entrypoint |
| 6 | `PassPurchaseCancelRefundViewTests` | `pass-purchases/{id}/cancel/` and `/refund/` |
| 7 | `ClassroomCloseTests` | `classrooms/{id}/close/` |
| 8 | `WaitlistTests` | overflow-to-waitlist, idempotent join, auto-promote on leave, FCFS order, kicked-student-never-promoted, manual promote() perms |
| 9 | **`ClassroomBanTests`** *(added this pass)* | ban/unban permissions, self-ban block, idempotent ban, refund+reject-pending-request on ban, banned-student blocked from join-request AND live-session join, bans-list manager-only, unban-missing-row 404 |
| 10 | **`ReferralRedeemTests`** *(added this pass)* | valid redeem credits both wallets, invalid code, self-redeem block, double-redeem block, redeem-window expiry, `my-code` tally, own-ledger-only privacy |
| 11 | **`TeacherEarningsTests`** *(added this pass)* | scoping to caller's own classrooms, no cross-teacher leakage, 403 on unowned `?classroom=`, 30-day daily-breakdown window |
| 12 | **`ClassroomRecordingsTests`** *(added this pass)* | recorded-only filtering, teacher/enrolled-student access, 403 for no access |
| 13 | **`ClassroomPriceRatingFilterTests`** *(added this pass)* | `min_rating`, `min_price`, `max_price`, range matching, malformed-param graceful handling |
| 14 | `CoinWithdrawalTests` *(Pass 5 — row was missing from this table until now)* | debit-at-request, minimum-amount rejection, insufficient-balance rejection, payout_details validation (bank/UPI), self-cancel + refund, cross-user cancel forbidden, non-staff approve forbidden, full approve→mark-paid happy path, reject-requires-a-reason + refund, cannot cancel an already-approved request, staff-sees-all vs self-sees-own |
| 15 | **`ClassroomShareTests`** *(Pass 6)* | outside-the-app share returns link + bumps `share_count` + notifies nobody, in-app share notifies target + forces `channel="in_app"`, self-share rejected, `channel="in_app"` without `to_user_id` rejected, sharing a classroom you can't see 404s, `share-stats` visible to teacher / 403 for a student |

**Deliberately NOT covered** (per `tests.py`'s own module docstring):
scheduling/recurrence generation, chat, polls, assignments grading flow
end-to-end.

---

## 10. Settings this app requires (NOT in this upload)

Referenced throughout the code but `settings.py` was never uploaded —
confirm these exist with real values before deploy:

- `REST_FRAMEWORK["EXCEPTION_HANDLER"] = "liveclass.exceptions.liveclass_exception_handler"`
- `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` — needs `session_join`,
  `session_token`, `coupon_validate`, `chat_message_create`, and
  (Pass 3) `chunked_upload_init`, `chunked_upload_chunk`,
  `chunked_upload_complete` entries, and (Pass 5 — see §11)
  `coin_withdrawal` (a low rate, e.g. `5/day`, is enough — this is a
  real-money-adjacent action, not a browsing endpoint)
- `LIVECLASS_WEB_BASE_URL` *(Pass 6, optional)* — base web URL used by
  `Classroom.share_urls()` to build `.../classroom/{id}` links; falls
  back to a placeholder (`https://app.example.com`) via `getattr` if
  unset, so this works out of the box in dev but MUST be set to the real
  domain before any share link reaches an actual user.
- `LIVECLASS_DEEP_LINK_SCHEME` *(Pass 6, optional)* — URL scheme used for
  the in-app deep link half of `share_urls()` (e.g. `liveclass://...`);
  falls back to `"liveclass"` via `getattr` if unset — should match
  whatever custom scheme the Flutter app actually registers.
- `CHUNKED_UPLOAD_TMP_ROOT` (Pass 3) — filesystem path for in-progress
  chunk assembly, deliberately outside `MEDIA_ROOT` (see §2
  `ChunkedUpload`). Local-disk only as written; a multi-instance
  deployment needs this on a shared volume or sticky-routed to one
  instance per `upload_id` — see §12 item 9.
- `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = ["IsAuthenticated"]`
  (fail-closed floor)
- `REST_FRAMEWORK["PAGE_SIZE"] = 20` (via `LiveClassPagination`)
- `REFERRAL_BONUS_COINS`, `REFERRAL_REDEEM_WINDOW_DAYS`
- `MIN_AGE_BEFORE_DELETE_DAYS` (constant on `Classroom`, may be
  settings-driven or hardcoded — verify)
- `AUTO_FLAG_THRESHOLD` (constant on `ClassroomReport`)
- `SESSION_GENERATION_WINDOW_DAYS` (used by `generate_upcoming_sessions`)
- `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_WS_URL`
- `CELERY_BEAT_SCHEDULE` — 6 entries matching the tasks in §7, each
  interval shorter than its own lookback/staleness window
- `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL` (Postgres — SQLite has no
  real concurrent-write story for this multi-user platform), `REDIS_URL`
  (Channels + Celery broker + cache), `CORS_ALLOWED_ORIGINS`,
  `CSRF_TRUSTED_ORIGINS`, `SENTRY_DSN` (optional, recommended,
  `traces_sample_rate` defaults to `1.0` — turn down to ~0.1–0.2 once
  real traffic arrives), SMTP vars, `FCM_SERVICE_ACCOUNT_JSON_PATH`
- HTTPS hardening (`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`,
  `SECURE_HSTS_*`, `SECURE_PROXY_SSL_HEADER`) gated on `not DEBUG`
- `DATA_UPLOAD_MAX_MEMORY_SIZE` — should be 10MB, not Django's 500MB
  default (DoS vector on plain text/JSON fields; actual file uploads are
  separately bounded by `MaxFileSizeValidator`). This is exactly why
  `ChunkedUpload` (Pass 3) exists — `ClassMaterial.file` (100MB) and
  the 50MB attachment/submission fields would be rejected outright as
  a single request under this cap.

**Packages required** (no `requirements.txt` in this upload):
`psycopg2-binary`, `channels_redis`, `django-redis`, `sentry-sdk`,
`drf-spectacular`, `livekit-api`, `celery`, `django-filter`,
`django-cors-headers`, `whitenoise`, `daphne`,
`djangorestframework-simplejwt`, `python-dotenv`.

**Other apps this app depends on, not in this upload:** `login` (custom
`User` model with `.coin`, `.phone_number`/`.mobile`, standard auth
fields), `message` (`push_utils.send_push_to_users` — Firebase/FCM
already wired there; `liveclass` deliberately does NOT double-initialize
`firebase_admin`).

---

## 11. Change log

Every fix made across both passes, in the order they matter, with the
reasoning. (Earlier fixes are inline `NOTE (fix — ...)` comments
throughout the source — this is the index; read the cited file/function
for the full reasoning if needed.)

### Pass 1 (original hardening — already in the uploaded source)
- **`apps.py` created** — without it, every `@receiver` in `signals.py`
  (LiveKit teardown, waitlist promotion, attendance credit) was dead
  code, correctly written but never actually running.
- **`exceptions.py` created** — unified error envelope; also fixed a
  latent crash where a nested/writable serializer's error shape
  (`{"user": {"email": [...]}}` or a list-of-dicts from `many=True`)
  would have raised `KeyError`/`TypeError` INSIDE the handler itself
  (`_first_message` now recurses arbitrary depth).
- **`LiveKitError` promoted from bare `Exception` to `APIException`
  subclass** — previously bypassed `exceptions.py` entirely (hand-built
  `Response` at each call site, no `code` field).
- **`Classroom.has_access()` — `max_classes` cap fix** — a "5-class
  pack" pass previously let a student join unlimited sessions; the
  actual room-entry gate never checked the cap, only a narrower parallel
  copy in `PassPurchase.is_valid()` did.
- **`Classroom.sync_flag_status()` — permanent-hide fix** — the
  old auto-flag signal could only ever set `is_flagged=True`; a
  classroom that crossed the report threshold once stayed hidden from
  Explore forever, even after every report was dismissed. Now
  recomputed from the live pending count on every review.
- **`ClassroomBan` had no teeth (3 separate fixes)** — the model
  existed but nothing checked it: added checks in
  `Classroom.has_access()`, `_perform_join`, and
  `ClassJoinRequestViewSet.perform_create` (a banned student could
  otherwise raise a fresh join request against a new pass immediately
  after being banned).
- **`liveclass.signals.notify_waitlist_seat_open` fixed** — this name
  was imported but never defined anywhere; every promotion silently
  `ImportError`'d and was swallowed. Now correctly points at
  `tasks.notify_waitlist_promotion`; the in-app bell row was added too
  (previously only the host-triggered manual `/waitlist/{id}/promote/`
  path created one).
- **`ClassSessionViewSet.join()` time-window fix** — `is_joinable()`'s
  window check was accidentally blocking hosts/staff too; scoped to
  students only.
- **`tasks.refresh_stale_enrolled_counts` created** — `enrolled_count`
  only re-synced on `PassPurchase` save; a purchase silently *expiring*
  never saves anything, so the counter drifted upward forever without
  this sweep.
- **`tasks.expire_and_refund_passes` created** — the actual
  "student loses money" gap: once `expires_at` passed,
  `charge_for_session()` correctly refused to release more, but nothing
  ever called `reverse()` on the leftover either — coins sat
  permanently un-refunded. Now swept and refunded.
- **6 new `Notification.NotifType` values + matching tasks** —
  `SESSION_LIVE`, `SESSION_CANCELLED`, `ASSIGNMENT_POSTED`,
  `SUBMISSION_RECEIVED`, `STAFF_ADDED`, `REVIEW_POSTED` had real events
  with zero notification (not even the bell row) before.
- **`ClassSessionViewSet.kick()` fix** — previously only called
  `remove_participant()` + marked `left_at`; didn't set `kicked_at`, so
  a kicked student could immediately rejoin the same session.
- **`ClassSessionViewSet.mute()` added** — `kick()` was too blunt for
  routine moderation (force-mute without removing).
- **`raise_hand`/`lower_hand` added** — previously the only "I want to
  say something" signal was `ClassQuery`, a written doubt meant to be
  answered later, not a live real-time hand-raise.
- **File upload hardening** — every `FileField` got both
  `MaxFileSizeValidator` and a `FileExtensionValidator` safelist;
  `DATA_UPLOAD_MAX_MEMORY_SIZE` reduced 500MB → 10MB.
- **`PassPurchaseViewSet.cancel()` added** — student self-service
  cancel didn't exist at all before; only teacher/staff `refund()` did.
- **Security settings** — `DEBUG` defaults `False`, `ALLOWED_HOSTS` safe
  default (wildcard only with `DEBUG=True` and no env var set),
  `CORS`/`CSRF` env-driven allowlists (not `ALLOW_ALL`), HTTPS hardening
  gated on `not DEBUG`.

### Pass 2 (this document's own follow-up pass)
- **`urls.py`** — `TeacherEarningsView` (a plain `APIView`) was
  imported and fully implemented in `views.py` but had no `path()`
  entry — `router.register()` only auto-wires `ViewSet`s, not plain
  `APIView`s, so it was silently unreachable at any URL. Added
  `path("my-earnings/", TeacherEarningsView.as_view(),
  name="teacher-earnings")`. Also documented the `referrals/` endpoints
  in the module docstring — live but previously undocumented there.
- **`admin.py`** — registered `ClassroomBan` (`ClassroomBanAdmin`) and
  `Referral` (`ReferralAdmin`, read-mostly since rows tie 1:1 to
  already-created `CoinTransaction` entries) — both fully functional via
  the API with zero admin-panel visibility before this.
- **`tests.py`** — added `ClassroomBanTests`, `ReferralRedeemTests`,
  `TeacherEarningsTests`, `ClassroomRecordingsTests`,
  `ClassroomPriceRatingFilterTests` (~40 new test methods total) — see
  §9 for exact coverage.
- **This reference doc created/redesigned** — consolidating everything
  above into one file.

### Pass 3 — chunked upload for large files
- **New file `chunked_upload_views.py`** — `chunked_upload_init` /
  `chunked_upload_chunk` / `chunked_upload_complete` /
  `chunked_upload_abort`, function-based views wired at
  `uploads/chunked/{init,chunk,complete,abort}/` in `urls.py`. Built
  from scratch for this app (not copied from another project's
  reference implementation) — see §2 `ChunkedUpload` for the full
  design (purpose-based permission checks at both init and complete,
  atomic PROCESSING claim against double-completion, re-validation
  through the target field's own `MaxFileSizeValidator`/
  `FileExtensionValidator`, Pillow decode check for `cover_image`,
  temp storage outside `MEDIA_ROOT`).
- **`models.py`** — new `ChunkedUpload` model (see §2), inserted just
  above the SIGNALS section at the bottom of the file. No changes to
  `Classroom`/`ClassMaterial`/`Assignment`/`AssignmentSubmission`
  themselves — chunked upload is additive; the old single-request
  upload path for small files still works exactly as before.
- **`tasks.py`** — new `cleanup_stale_chunked_uploads` cron task (see
  §7), plus two new module-level constants,
  `CHUNKED_UPLOAD_STALE_AFTER` (6h) and
  `CHUNKED_UPLOAD_ROW_RETENTION_DAYS` (14d).
- **`urls.py`** — 4 new plain `path()` entries (not router-registered —
  these aren't a ViewSet) plus the `chunked_upload_views` import.
- **`settings.py`** — `CHUNKED_UPLOAD_TMP_ROOT` added, 3 new throttle
  scopes added to `DEFAULT_THROTTLE_RATES`, 1 new
  `CELERY_BEAT_SCHEDULE` entry added.
- **Not yet done (see §12 items 9–10):** no migration generated/run
  against a real project (no live Django project in this environment,
  same limitation as the rest of this doc — see §13), and no test
  coverage added for the new endpoints (`tests.py` untouched this
  pass).

### Pass 4 — performance follow-up (search index + notice-board caching)
- **`models.py`** — added 3 Postgres trigram (`GinIndex`, `gin_trgm_ops`)
  indexes on `Classroom.title` / `subject` / `description`. The
  `?search=` filter in `ClassroomViewSet.get_queryset` does
  `__icontains` across all three — a sequential scan today, fine at
  hundreds of rows, a real cost once the platform has thousands. Also
  added a new per-classroom notice-list cache-version helper pair
  (`get_notice_list_cache_version` / `bump_notice_list_cache_version`,
  same O(1) invalidation pattern as the existing classroom-list
  versioning) and a `Notice` post_save/post_delete receiver that bumps
  it.
- **New migration `migrations/0XXX_trigram_search_indexes.py`** —
  **file name is a placeholder**: rename `0XXX` to the next sequence
  number after this project's actual latest `liveclass` migration, and
  fill in `dependencies`. Runs `TrigramExtension()` (enables Postgres's
  `pg_trgm`) then adds the 3 indexes above. Postgres-only — will fail
  `migrate` on any other DB backend; see in-file note re: RDS vs
  self-managed Postgres extension privileges. **Not yet generated
  against a real project or run** — same environment limitation as the
  rest of this doc (§13): no live Django project here to run
  `makemigrations`/`migrate` against, so this was hand-written to match
  what `makemigrations` would produce for these 3 `Meta.indexes`
  entries, not machine-generated.
- **`views.py`** — `NoticeViewSet.list()` now caches per classroom,
  per user, per query-string (60s TTL + version bump on any `Notice`
  write for that classroom), mirroring `ClassroomViewSet.list()`'s
  existing pattern. Scoped **per user** (not just per classroom) because
  `_can_view_classroom_internals` can return a different result per
  caller (teacher/staff vs enrolled vs no-access), so a shared
  per-classroom cache key would risk leaking one user's page to
  another. `NotificationViewSet` (private, per-user, already small) and
  `NotificationViewSet`'s own list were deliberately left uncached —
  same reasoning `ClassroomViewSet.list` already gives for skipping
  `?mine=` results: low-cardinality, already fast, caching would just
  multiply cache keys for near-zero benefit.
- **Not done this pass (recommended, larger scope — see below):** DB
  connection pooling (`CONN_MAX_AGE` / PgBouncer), CDN in front of
  recording/media URLs, WebSocket/Channels layer for chat/raise-hand/
  live-poll (currently REST), short-TTL caching on
  `Classroom.has_access()`/`is_enrolled()` (skipped deliberately —
  correctness risk on a money/access-gating path outweighs the read
  savings without a carefully designed invalidation story), APM/slow-
  query monitoring (Sentry / django-silk), waitlist-offer auto-expiry
  timeout.

### Pass 5 — coin withdrawal / payout (the missing coins-to-cash off-ramp)

- **`models.py`** — new `CoinWithdrawal` model (§13C). A user requests a
  payout of their real, earned coin balance to a bank account or UPI id.
  Coins are debited **at request time**, inside
  `CoinWithdrawal.create_request()` (row-locked, atomic — the same
  race-safety pattern `PassPurchase.charge_for_session`/`reverse` already
  use), not on admin approval — this is what stops a user submitting two
  overlapping requests for the same coins without needing a separate
  "coins on hold" field. `reject()`/`cancel()` refund the coins;
  `approve()`/`mark_paid()` move the request through its status only —
  the actual bank/UPI transfer happens OUTSIDE this app (finance/admin
  does it manually via the bank's own portal, or a payout API integrated
  later), and `mark_paid()` just records the external reference (UTR/UPI
  txn id) for audit/reconciliation. `COIN_TO_INR_RATE` and
  `MIN_WITHDRAWAL_COINS` are the two tunables — adjust `COIN_TO_INR_RATE`
  to match whatever real-money rate coins were actually sold/priced at
  elsewhere in the platform. Two new `CoinTransaction.Reason` values
  (`WITHDRAWAL`, `WITHDRAWAL_REVERSED`) and three new
  `Notification.NotifType` values (`WITHDRAWAL_APPROVED`,
  `WITHDRAWAL_REJECTED`, `WITHDRAWAL_PAID`) were added alongside it.
  **Deliberately out of scope (product decision):** coin TOP-UP, i.e.
  buying coins with real money — this pass only closes the withdrawal
  (coins → cash) direction.
- **`serializers.py`** — new `CoinWithdrawalSerializer`. Validates
  `payout_details` against `payout_method` (`bank_transfer` requires
  `account_holder`/`account_number`/`ifsc`; `upi` requires `upi_id`) so a
  malformed payload never reaches the model layer, and enforces
  `MIN_WITHDRAWAL_COINS` at the field level for a clean 400 before any
  DB work happens.
- **`views.py`** — new `CoinWithdrawalViewSet`. `create`/`list`/`retrieve`
  are self-service (own requests only); `cancel` is self-service (own
  request, only while still `PENDING`); `approve`/`reject`/`mark_paid`
  are `is_staff`-only. Staff additionally see every request across every
  user via `get_queryset` (optionally filtered by `?status=`), the same
  "escalated queryset for the party who actually needs the wider view"
  shape `PassPurchaseViewSet.get_queryset`'s `?classroom=` already uses.
  Every model-raised `django.core.exceptions.ValidationError` (from
  `create_request`/`approve`/`reject`/`cancel`/`mark_paid`) is caught at
  the view boundary and re-raised as DRF's own `ValidationError` — this
  is a NEW pattern in this file (every other model method that needs to
  reject work either returns `None`/`False` or the view pre-validates
  before calling it); documented here since it's not yet used anywhere
  else in views.py, and any future model method that raises validation
  errors directly should follow the same catch to keep getting the
  clean `{"detail", "code"}` envelope instead of an unhandled 500 (see
  `exceptions.py` — Django's plain `ValidationError` is NOT one of the
  types `liveclass_exception_handler` normalises today, only
  `Http404`/`DjangoPermissionDenied`/`IntegrityError`).
- **`urls.py`** — new `withdrawals/` route (`CoinWithdrawalViewSet`,
  basename `coinwithdrawal`), fully documented in the header comment.
- **`tests.py`** — new `CoinWithdrawalTests` covering: debit-at-request,
  minimum-amount rejection, insufficient-balance rejection,
  payout_details validation for both methods, self-cancel + refund,
  cross-user cancel forbidden, non-staff approve forbidden, the full
  approve→mark-paid happy path (asserting coins move ONCE, at request
  time, never again), reject-requires-a-reason + refund, cannot
  cancel an already-approved request, and the staff-sees-all vs
  self-sees-own queryset split.
- **New migration `migrations/0XXX_coinwithdrawal.py`** — **placeholder
  name**, same caveat as Pass 4's trigram migration: rename `0XXX` to the
  next real sequence number and fill in `dependencies` against this
  project's actual latest `liveclass` migration. Adds the `CoinWithdrawal`
  table (all fields above) plus its `Meta.indexes` entry
  (`status`, `requested_at`). **Not generated against a real project or
  run** — no live Django project in this sandbox (see §13); hand-written
  to match what `makemigrations` would produce for this one new model.
- **Settings needed (added to §10 below):** `coin_withdrawal` throttle
  scope in `DEFAULT_THROTTLE_RATES` — a low rate (e.g. `5/day`) is
  appropriate; this is a real-money-adjacent action, not a browsing
  endpoint.
- **Not done this pass (recommended, out of scope per this pass's
  request):** coin top-up (buying coins with real money) — the reverse
  direction, deliberately excluded. Also not done: an admin-facing bulk
  payout export/CSV for finance to action many `APPROVED` requests at
  once, and a Celery task to auto-remind staff of stale `PENDING`
  requests — both reasonable small follow-ups if the manual approve/
  reject/mark-paid flow above turns out to be too slow at volume.

### Pass 6 — share a classroom (in-app + outside-the-app)

- **`models.py`** — new `ClassroomShare` model (§2, "11C"): one row per
  share attempt, whatever the channel (`IN_APP / WHATSAPP / SMS / EMAIL /
  COPY_LINK / OTHER`); `shared_with` is only ever set for `IN_APP`. New
  `Classroom.share_count` field (denormalized, same pattern as
  `rating_avg`/`enrolled_count`), bumped by the new race-safe
  `Classroom.record_share()` (`F()`-expression update, not
  read-modify-write — two shares landing at once can't stomp on each
  other). New `Classroom.share_urls()` builds the web link + in-app deep
  link from the two new `LIVECLASS_WEB_BASE_URL`/
  `LIVECLASS_DEEP_LINK_SCHEME` settings (see §10), `getattr`-defaulted so
  it works out of the box in dev. New `Notification.NotifType.
  CLASSROOM_SHARED` value.
  **While touching this list, also corrected a pre-existing drift**: §2's
  `NotifType` value list and §7's dispatcher-task list had never been
  updated when Pass 5 added the three `WITHDRAWAL_*` notification types —
  both are now accurate again (see those sections).
- **`serializers.py`** — `share_count` added to `ClassroomSerializer`
  (read-only) and `ClassroomStatsSerializer`. New
  `ClassroomShareSerializer` (request validation — `to_user_id` optional,
  `channel` optional; `validate()` forces `channel="in_app"` whenever
  `to_user_id` is given, and rejects `channel="in_app"` with no
  `to_user_id` as a 400), `ClassroomShareResultSerializer` (response
  shape: `share_id`/`web_url`/`deep_link`/`share_text`/`shared_with`/
  `share_count`), and `ClassroomShareLogSerializer` (read-only history
  row, used by `share-stats`).
- **`views.py`** — two new `ClassroomViewSet` actions:
  - `share` (POST) — `get_object()` already applies the viewset's normal
    visibility rule, so a classroom you can't otherwise see 404s here
    too, no separate check needed. Rejects self-share
    (`to_user_id == request.user.id`). Always creates one `ClassroomShare`
    row and calls `record_share()`. When `to_user_id` is given: calls
    `create_notification()` synchronously (same as every other
    `create_notification` call site in this file) for the in-app bell
    row, then queues `notify_classroom_shared` via the existing
    `_safe_delay` helper for the push half — never calls `.delay()`
    unguarded, same discipline as every other `notify_*` call site.
  - `share-stats` (GET) — gated by `_can_manage_classroom` (teacher/
    co-teacher/moderator, same tier as `bans`/`ban`/`unban`). Returns
    `share_count`, a channel breakdown (`.values("channel").annotate
    (count=Count("id"))`), and the 50 most recent rows. Deliberately NOT
    run through `pagination_class` — this is a small aggregate summary,
    not a bare list, so DRF's page/results envelope doesn't fit it; a
    dedicated paginated full-history endpoint can be split out later if
    that's ever actually needed.
  - `stats` (existing action) updated to include `share_count` in its
    response, matching the model/serializer change.
- **`tasks.py`** — new `notify_classroom_shared` task, mirroring
  `notify_waitlist_promotion`'s exact pattern: takes `share_id` (not a
  tuple of ids passed across the broker) and re-looks the row up from the
  DB, so it always reflects exactly what the view created rather than
  trusting values that could race against a caller mutating/deleting
  rows — same reasoning documented on `notify_waitlist_promotion`'s own
  NOTE (fix).
- **`urls.py`** — no change; `share`/`share-stats` are `@action`s on the
  already-registered `ClassroomViewSet`, so the router picks them up
  automatically (`classroom-share`, `classroom-share-stats`).
- **`tests.py`** — new `ClassroomShareTests`: outside-the-app share
  returns a link containing the classroom id, bumps `share_count`, and
  creates zero `Notification` rows; in-app share notifies the target and
  forces `channel="in_app"` regardless of what was sent; self-share is
  rejected (400); `channel="in_app"` with no `to_user_id` is rejected
  (400); sharing a classroom outside your visibility (inactive, not your
  own) 404s; `share-stats` returns correct counts/breakdown for the
  teacher and 403s for a student.
- **New migration needed** — one new field (`Classroom.share_count`) +
  one new model (`ClassroomShare`). Not generated/run in this pass —
  same reason as every other migration note in this doc (no live Django
  project in this sandbox, see §13): run
  `python manage.py makemigrations liveclass && python manage.py migrate`
  against the real project.
- **Settings needed (added to §10 above):** `LIVECLASS_WEB_BASE_URL`,
  `LIVECLASS_DEEP_LINK_SCHEME` — both optional with working defaults, but
  the web-base-URL default is a placeholder domain that must be
  overridden with the real one before shipping.
- **Not done this pass (reasonable follow-ups, out of scope per this
  pass's request):** a per-user daily share-rate cap (the `ClassroomShare`
  model was deliberately shaped to make this easy to add later without a
  redesign — see its docstring in `models.py`); a "my shares" self-service
  history endpoint (today only the teacher-facing `share-stats` exists);
  a `share_count`-aware sort option on the Explore listing's
  `OrderingFilter` (`ordering_fields` currently stops at `created_at`/
  `rating_avg`/`enrolled_count`/`title`).

---

## 12. Open action items before deploy

Config/deployment only — **no known code defects remain** (see §13 for
what "known" means here).

1. **Fill real `.env` values** — see §10 for the full list; the code
   already supports every one of these via env vars, they just need
   real production values.
2. **Install the packages listed in §10** — none shipped as a
   `requirements.txt` in this upload.
3. **Confirm `message.push_utils.send_push_to_users` import path** —
   `notifications.py._send_push` degrades gracefully (logs a warning,
   never crashes) if wrong, but push silently no-ops until corrected.
4. **SMS channel unimplemented** — `_send_sms` always no-ops; fine
   unless the product needs it, but don't assume
   `send_notification(..., channel="sms")` does anything until a
   provider is wired in.
5. **Celery worker + beat must both actually run as long-lived
   processes** — the 5 scheduled jobs in §7 are inert DB config
   otherwise.
6. **Run `python manage.py check --deploy`** once real `.env` values
   are set — flags anything environment-specific a static read can't.
7. **Run `python manage.py makemigrations --check`** — this
   audit read source only, never migration files.
8. **Run `python manage.py test liveclass` for real** — see §13; the
   new tests from Pass 2 were verified by static cross-reference and by
   resolving the DRF router's actual generated URL names, but never
   actually executed, since this environment has no `settings.py` or
   the `login`/`message` apps.
9. **Rename and run the Pass 4 trigram migration** — see Pass 4 above;
   requires Postgres and `CREATE EXTENSION` privilege (or a DBA to run
   `CREATE EXTENSION IF NOT EXISTS pg_trgm;` once beforehand on a
   self-managed instance — RDS's master user can run it directly).
10. **Rename and run the Pass 5 `CoinWithdrawal` migration** — see Pass 5
    above; plain table + index, no extension needed, safe on any DB
    backend.
11. **Set `CoinWithdrawal.COIN_TO_INR_RATE` correctly** (Pass 5) — the
    placeholder value of `1` is almost certainly wrong; set it to match
    whatever real-money rate coins are actually sold at elsewhere in the
    platform before any withdrawal is approved for real.
12. **Generate and run the Pass 6 `ClassroomShare`/`share_count`
    migration** — see Pass 6 above; plain new field + new table, no
    extension needed, safe on any DB backend.
13. **Set `LIVECLASS_WEB_BASE_URL` to the real domain** (Pass 6) — the
    `getattr` default is a placeholder (`https://app.example.com`) that
    will silently produce dead/wrong links in every share response until
    overridden.
14. **Consider, but not yet built:** DB connection pooling
    (`CONN_MAX_AGE`/PgBouncer), a CDN in front of media/recording URLs,
    and a Channels/WebSocket layer for chat/raise-hand/live-poll if
    REST-polling latency becomes a real user complaint — all flagged in
    Pass 4 as bigger-scope follow-ups, not done in this pass. Coin
    top-up (buying coins with real money) — flagged in Pass 5,
    deliberately excluded from that pass. Per-user share-rate limiting,
    a "my shares" endpoint, and `share_count` as an `Explore` sort
    option — flagged in Pass 6, deliberately excluded from that pass.

---

## 13. Verification methodology — what was and wasn't actually run

**Could verify in this environment (no full Django project available):**
- `ast.parse()` syntax check — all 13 `.py` files, pass.
- `pyflakes` static analysis — all 13 files; zero new warnings from
  Pass 2's changes (two pre-existing "assigned but never used" `coupon`
  local-variable flags in `tests.py` predate this pass — the variable is
  created for its DB side effect, not a bug).
- Cross-file import resolution — every name Pass 2 imports from
  `.models` into `admin.py`/`tests.py`, from `.models` into `views.py`,
  and from `.views` into `urls.py`, checked against actual class/
  function definitions by name. Zero misses.
- **DRF router URL-name generation** — installed Django 6.1 + DRF in
  this sandbox, built equivalent `@action`-decorated stub `ViewSet`s
  registered on a real `DefaultRouter`, and printed the actual generated
  URL names/patterns. Confirmed `classroom-ban`, `classroom-bans`,
  `classroom-unban` (and its two-positional-arg `(pk, student_id)`
  shape), `classroom-recordings`, `referral-my-code`, `referral-redeem`
  all match exactly what the new tests in `tests.py` call via
  `reverse(...)`. This is real router behavior, not an assumption.

**Could NOT verify (needs the real project):**
- Actually running `python manage.py test liveclass` — this upload
  never included `settings.py`, the `login` app (custom `User` model),
  the `message` app (`push_utils`), or third-party deps (`livekit-api`,
  `celery`, `channels`, etc.), so a live Django test run was not
  possible in this sandbox.
- `python manage.py check --deploy` / `makemigrations --check` — same
  reason.
- Anything that depends on actual `.env` values, a real Postgres/Redis
  instance, or a live LiveKit project.

**Bottom line:** no unresolved logic bugs, unwired dead code, unhandled
money-path race conditions, or open permission gaps were found in the
code itself across either pass. What remains is standard pre-launch
deployment hygiene (§12) plus actually running the test suite against
the real project once it's assembled.

---

## Maintenance rule for this file

Any future code change to this app should update the relevant section
above IN THE SAME PASS — new model/field → §2, new endpoint → §3, new
permission rule → §4, new signal/task → §6/§7, new test class → §9, new
fix → §11 (append, don't rewrite history), new open item → §12. This
file is meant to be handed over on its own, without the source files, so
it needs to stay a complete and accurate mirror of the code at all
times — not just a snapshot from whenever it was last written.