# `testseries` App — Implementation Reference (v2 — Tasks 15/16/17 synced)

> Ye woh single doc hai jisse **sara kaam ho sakta hai** — settings wiring,
> prerequisite migrations/gaps, API integration (campus/liveclass/message
> bridge calls ho ya frontend), permissions samajhna, ya sirf "ye field
> kya karta hai" lookup. Is baar ka pass code ki 12 files (`models.py`,
> `views.py`, `serializers.py`, `permissions.py`, `bridge.py`, `admin.py`,
> `apps.py`, `urls.py`, `tasks.py`, `tests.py`, `__init__.py`) ke against
> **line-by-line resync** kiya gaya hai — jo bhi is doc ke purane (v1)
> version me stale tha (Task 15 reviews, Task 16 doubt-queries, Task 17
> regression tests, `_TransactionTypeGap`/`_NotifTypeGap` shims ka removal)
> wo sab yahan update ho chuka hai. Code files khud source-of-truth rehte
> hain, lekin unhe padhne ki zaroorat tabhi hai jab actual line-by-line
> implementation dekhni ho — is doc me har cheez already extract ki hui hai.

---

## 0. File structure (delivered, as of this pass)

```
testseries/
├── __init__.py          # empty, Python package marker
├── apps.py              # AppConfig (TestseriesConfig)
├── models.py             # TestSeries, Question, QuestionResponse, TestSeriesPurchase,
│                         # TestAttempt, TestSeriesReview (Task 15)
├── serializers.py        # DRF serializers — answer-key hiding, review validation
├── permissions.py        # IsSeriesCreatorOrReadOnly, user_can_review_attempt(),
│                         # CanReviewCheckedAttempt (Task 15), CanAskQueryOnCheckedAttempt (Task 16)
├── views.py               # TestSeriesViewSet, QuestionViewSet, TestAttemptViewSet
│                         # (+ ask_query/answer_query actions), TestSeriesReviewViewSet (Task 15)
├── urls.py                # router + nested question/review routes
├── admin.py               # Django admin registration
├── bridge.py              # create_context_testseries(), get_attempts_for_context(),
│                         # ask_query_on_series() / answer_query_on_series() (Task 16)
├── tasks.py               # 2 Celery tasks (§11 safety nets)
├── tests.py               # RegressionLockTests (Task 17 — 3 sequencing-lock tests)
└── migrations/            # (khud generate karna: `manage.py makemigrations testseries`)
```

**Missing on purpose, not forgotten:** koi campus/liveclass-specific
viewset `testseries/views.py` me nahi hai — us wiring ka apna
proxy-endpoint `campus/views.py` / `liveclass/views.py` me banega
(jaisa `assignment` app ke liye tha), jo `testseries.bridge.
create_context_testseries()` ko call karega. Golden rule (§1) ke
hisaab se `testseries` khud campus/liveclass ka URL-space nahi
define karta.

---

## 1. Golden rules (non-negotiable, sab jagah enforce hui hain)

1. **`testseries` kabhi `campus`/`liveclass` models seedha import nahi
   karta.** Context `context_type` (CharField) + `context_id` (UUID) se
   opaque reference hota hai. Resolve karna caller (bridge) ka kaam hai.
   `bridge.get_attempts_for_context()` (Task 16 addition, see §5) isi
   rule ka doosra direction hai — `campus`/`liveclass` ko `TestAttempt`
   seedha query karne ki zaroorat na pade.
2. **`user_profile.CoinLedger` seedha use hota hai** (bridge ke bina) —
   `_record_coin_transaction()` helper (`models.py`) lazy-imports it.
3. **`message.DoubtQuestion` bhi ab isi tarah use hota hai (Task 16,
   NEW).** `testseries -> message` one-way — `bridge.py`'s
   `ask_query_on_series()`/`answer_query_on_series()` lazy-import
   `message.models.DoubtQuestion` / `message.services.
   answer_doubt_question()`. `message` khud `testseries` ko kabhi
   import nahi karta — `DoubtQuestion.context_type="testseries_attempt"`
   / `context_id=<attempt.id>` sirf ek opaque pointer hai `message` ke
   liye, `group=None`/`conversation=None` (ek testseries query ke paas
   dono nahi hote). Yehi golden rule ka pattern hai jo `campus`/
   `liveclass` ke saath already tha, ab `message` ke saath bhi.
4. **Cross-app enum dependencies kabhi guess nahi ki jaatin.**
   `user_profile.CoinLedger.TransactionType.TESTSERIES_PURCHASE`/
   `TESTSERIES_PAYOUT` aur `core.models.Notification.NotifType.
   TESTSERIES_POSTED`/`TESTSERIES_CHECKED`/`TESTSERIES_PAYOUT_RELEASED`
   — ye sab **ab RESOLVED** hain (old `_TransactionTypeGap`/
   `_NotifTypeGap` shim classes **hata di gayi hain**, har call site
   ab real enum directly reference karta hai, lazy-imported). **Ek NAYA
   gap khula hai (Task 15):** `Notification.NotifType.
   TESTSERIES_REVIEW_RECEIVED` abhi tak confirmed nahi hai — poori
   checklist §3 me hai.
5. **Escrow release sirf full `checked` status pe** — partial-review pe
   kabhi payout nahi (koi platform-fee cut bhi abhi nahi, §17 open item).
6. **Sequencing rules ab regression-locked hain (Task 17, NEW):**
   "review se pehle attempt checked hona chahiye", "query poochne se
   pehle attempt checked hona chahiye", "payout sirf checked pe release
   hota hai, partially_checked pe nahi" — teeno ab `tests.py`'s
   `RegressionLockTests` me test-locked hain (§14). Future me koi bhi
   in teen rules ko todta hai to CI fail hoga.

---

## 2. Settings / `INSTALLED_APPS` wiring

```python
# settings.py
INSTALLED_APPS = [
    ...
    "testseries.apps.TestseriesConfig",
]

# Optional overrides (defaults shown — see tasks.py):
TESTSERIES_AUTO_REFUND_DAYS = 14   # escrowed + unchecked -> auto-refund buyer
TESTSERIES_REMINDER_DAYS = 3       # proactive "please review" nudge to creator

# Optional overrides (defaults shown — see models.py's shared
# common.attachment_validators, used by both Question.attachment and
# QuestionResponse.answer_attachment; shared with `assignment` too):
TESTSERIES_ATTACHMENT_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "webp"]
TESTSERIES_ATTACHMENT_MAX_MB = 10
```

```python
# urls.py (project root)
urlpatterns = [
    ...
    path("api/", include("testseries.urls")),
]
```

```python
# celery.py / celerybeat schedule
CELERY_BEAT_SCHEDULE = {
    ...
    "testseries-send-pending-check-reminders": {
        "task": "testseries.tasks.send_pending_check_reminders",
        "schedule": crontab(hour=9, minute=0),   # daily, per §11
    },
    "testseries-refund-unchecked-paid-attempts": {
        "task": "testseries.tasks.refund_unchecked_paid_attempts",
        "schedule": crontab(hour=9, minute=30),  # daily, per §11
    },
}
```

---

## 3. Cross-app prerequisites — gaps still open (checklist)

Ye saare items **doosre apps** (`user_profile`, `core`, `campus`,
`liveclass`, `message`) ke owner-scope me hain, `testseries` ke andar
nahi. Jo already resolved ho chuke hain unko bhi list me rakha hai
(status ke saath), taaki poori history ek jagah dikhe.

| # | Item | Owner app | Status |
|---|---|---|---|
| 3.1 | `CoinLedger.TransactionType.TESTSERIES_PURCHASE`/`TESTSERIES_PAYOUT` | `user_profile` | ✅ **RESOLVED** — `models.py` ab directly `CoinLedger.TransactionType.X` use karta hai, koi shim nahi bacha |
| 3.2 | `Notification.NotifType.TESTSERIES_POSTED` / `TESTSERIES_CHECKED` / `TESTSERIES_PAYOUT_RELEASED` | `core` | ✅ **RESOLVED** — same, direct reference, koi shim nahi |
| 3.3 | `Notification.NotifType.TESTSERIES_REVIEW_RECEIVED` | `core` | ❌ **OPEN (NEW, Task 15)** — `TestSeriesReview.create_review()` (`models.py`) is enum member ko reference karta hai; jab tak `core` isse add nahi karta, review-create khud kaam karega (row + uniqueness + checked-status guard sab real hain) lekin notify-the-creator step pe `AttributeError` aayega. Add karo: `TESTSERIES_REVIEW_RECEIVED = "testseries_review_received", "New Test Series Review"` |
| 3.4 | `campus.bridge.can_review_testseries_attempt(user, context_type, context_id) -> bool` | `campus` | ❌ **STILL OPEN** — `permissions.py::user_can_review_attempt()` ImportError par safe-default `False` deta hai (deny). Campus subject-teacher tab tak review nahi kar payega |
| 3.5 | `campus.bridge.create_testseries(...)` + campus proxy endpoint | `campus` | ❌ **STILL OPEN** — calls `testseries.bridge.create_context_testseries(source="campus", is_paid=False, ...)` |
| 3.6 | `liveclass.bridge.create_testseries(...)` | `liveclass` | ❌ **STILL OPEN** — calls `create_context_testseries(source="liveclass", is_paid=<teacher's choice>, ...)` |
| 3.7 | `message.models.DoubtQuestion.context_type` / `.context_id` (generic opaque pointer fields) | `message` | ⚠️ **ASSUMED ADDED this pass** — `bridge.py`'s Task 16 comment says these were added to `DoubtQuestion` this pass so `testseries` can attach queries without a new Q&A model. `message/models.py`'s own source wasn't shared to `testseries` for direct verification — confirm the migration actually landed in `message` before relying on `ask_query_on_series()` in production. |
| 3.8 | `message.services.answer_doubt_question(doubt, actor, answer_text, answered_by=...)` — `answered_by` param | `message` | ⚠️ **ASSUMED ADDED this pass** — same Task 16 pass per `bridge.py`'s comment; `actor=None` skips `message`'s own group-admin/mod check (a testseries doubt has no group). Confirm signature in `message/services.py` before deploy. |

**Once §3.3 lands:** `TestSeriesReview.create_review()` needs no code
change — the enum reference already points at the real name, it just
currently doesn't exist on `core`'s side.

**Once §3.4 lands:** no `testseries`-side code change needed either —
`permissions.user_can_review_attempt()` already calls it correctly,
it's just wrapped in `try/except ImportError` as a safe default until
the function exists.

---

## 4. Models — field reference

### 4.1 `TestSeriesBaseModel` (abstract)
| Field | Type | Notes |
|---|---|---|
| `id` | UUIDField, PK | `default=uuid.uuid4` |

### 4.2 `TestSeries`
| Field | Type | Notes |
|---|---|---|
| `source` | choices: `individual`/`campus`/`liveclass` | `db_index=True` |
| `context_type`, `context_id` | CharField(20) / UUIDField, nullable | blank for `individual` |
| `creator` | FK → `login.User`, CASCADE | |
| `title` | CharField(200) | |
| `description` | TextField, blank | |
| `is_paid` | BooleanField, default `False` | **force-`False`** for `source="campus"` in `save()` — defence-in-depth on top of §8 serializer check and the campus bridge's own force |
| `price_coins` | PositiveIntegerField, default `0` | force-`0` in `save()` whenever `is_paid=False` |
| `duration_minutes` | PositiveIntegerField, nullable | |
| `total_marks` | PositiveIntegerField, default `0` | denormalized sum of `Question.marks`; call `series.recompute_total_marks()` after any question change (views already do this) |
| `status` | choices: `draft`/`published`/`archived` | question set locked once non-`draft` |
| `attempts_allowed` | PositiveIntegerField, default `1` | multi-attempt (>1) is **not fully implemented** — see §17 item 2 |
| `created_at`, `updated_at` | auto | |

Indexes: `(source, context_type, context_id)`, `(creator, status)`.

**`save()`** — normalizes `is_paid`/`price_coins` per the rules above.
**`recompute_total_marks(save=True)`** — `Sum("questions__marks")`.

**Properties (Task 15, NEW):**
- **`review_count`** → `int` — plain `self.reviews.count()`, not
  denormalized (not on a hot read path yet).
- **`avg_rating`** → `float | None` — `Avg("rating")` over `self.reviews`,
  rounded to 2 decimals; **`None`** (not `0`) when zero reviews exist —
  a "no reviews yet" series and a "rated straight 0s" series must stay
  distinguishable for a sort-by-rating browse view.

### 4.3 `Question`
| Field | Type | Notes |
|---|---|---|
| `series` | FK CASCADE | |
| `order` | PositiveIntegerField | `unique_together=("series","order")`, `ordering=["order"]` |
| `question_type` | choices: `text`/`mcq`/`msq`/`list` | `db_index=True` |
| `text` | TextField | question statement |
| `attachment` | FileField(`upload_to="testseries/questions/"`), nullable | extension + size validated — see §15 |
| `marks` | PositiveIntegerField | max marks for this question |
| `options` | JSONField, default `list` | shape depends on `question_type` — table below |
| `correct_answer` | JSONField, default `dict` | shape depends on `question_type` — table below |

**Shape per `question_type`:**

| Type | `options` | `correct_answer` | Auto-graded? |
|---|---|---|---|
| `text` | `[]` (force-emptied) | `{}` (force-emptied) | No — always manual review |
| `mcq` | `[{"id": "a", "text": "..."}]` | `{"option_id": "a"}` | Yes — exact match |
| `msq` | same as `mcq` | `{"option_ids": ["a","c"]}` | Yes — **exact-set** match (no partial credit, §17 item 3) |
| `list` / match | `{"left": [{"id":"l1","text":"..."}], "right": [{"id":"r1","text":"..."}]}` | `{"list_mode": "match", "pairs": {"l1":"r1", ...}}` | Yes — exact match |
| `list` / order | `[{"id":"s1","text":"..."}]` | `{"list_mode": "order", "sequence": ["s3","s1","s2"]}` | Yes — exact match |

**`clean()`** — validates the shape table above, raises
`ValidationError` on mismatch; force-empties `options`/`correct_answer`
for `text`. **`save()`** calls `full_clean(exclude=["options",
"correct_answer"])` — those two are excluded from `clean_fields()` only
because their shape is already checked by `clean()` above (which always
runs regardless of `exclude`); everything else — required `text`/
`marks`/`question_type`/`order`, and the `(series, order)`
`unique_together` — stays validated, so a bad value comes back as a
clean `ValidationError` instead of a raw DB `IntegrityError` (this was
a bug found + fixed in the earlier hardening pass, §16 item 2).
**`auto_grade(answer_data) -> (is_correct, marks_awarded)`** — thin
wrapper around **`common.question_grading.auto_grade()`** (shared with
`assignment`, so both apps grade mcq/msq/list identically instead of
duplicating logic); returns `(None, None)` for `text`.

### 4.4 `QuestionResponse`
| Field | Type | Notes |
|---|---|---|
| `attempt`, `question` | FK CASCADE each | `UniqueConstraint(attempt, question)` name `unique_response_per_attempt_question` |
| `answer_data` | JSONField, default `dict` | same shape as `Question.correct_answer` for that type |
| `answer_attachment` | FileField(`upload_to="testseries/answers/"`), nullable | a student's photo/file answer — only ever set for `text`-type responses; extension + size validated — see §15 |
| `is_auto_graded` | BooleanField | `True` for mcq/msq/list, `False` for text |
| `is_correct` | BooleanField, nullable | always `null` for `text` |
| `marks_awarded` | PositiveIntegerField, nullable | auto-graded: set at submit-time; text: `null` until reviewed |
| `reviewer_feedback` | TextField, blank | per-question comment, any type |
| `reviewed_by`, `reviewed_at` | FK/DateTime, nullable | |

**`mark_answer(marks_awarded, feedback="", reviewer=None)`** — raises
`ValueError` if `is_auto_graded=True` (double-grading guard), if
`marks_awarded < 0`, or if `marks_awarded > question.marks`. The API
layer (`views.py::review_answer`) already rejects negative/over-limit
values as a clean 400 before this ever fires — this is the model-level
backstop for any other caller.

### 4.5 `TestSeriesPurchase` (escrow)
| Field | Type | Notes |
|---|---|---|
| `series`, `buyer` | FK CASCADE | |
| `coins_spent` | PositiveIntegerField | |
| `status` | choices: `escrowed`/`released`/`refunded` | default `escrowed` |
| `attempt` | OneToOne, `SET_NULL`, nullable | |
| `created_at`, `released_at`, `refunded_at` | | |

- **`purchase_and_start_attempt(series, buyer, **attempt_kwargs)`**
  (classmethod, `@transaction.atomic`, `select_for_update()` on buyer) —
  debits coins via `_record_coin_transaction`, creates the escrow row +
  linked `TestAttempt`. Insufficient balance → `ValueError` (view maps
  this to HTTP 402).
- **`release()`** — pays `series.creator`, sets `status="released"`,
  **sends `TESTSERIES_PAYOUT_RELEASED` notification**.
- **`refund(reason_note="")`** — refunds `buyer` via `REFUND` type, sets
  `status="refunded"`.

### 4.6 `TestAttempt`
| Field | Type | Notes |
|---|---|---|
| `series`, `student` | FK CASCADE | |
| `attempt_number` | PositiveIntegerField, default `1` | groundwork only — see §17 item 2 |
| `auto_score` | PositiveIntegerField, default `0` | sum of graded auto responses |
| `final_score` | PositiveIntegerField, nullable | set only once **every** response reviewed |
| `status` | choices: `in_progress`/`submitted`/`partially_checked`/`checked` | `partially_checked` = auto-graded done, ≥1 `text` still pending |
| `checked_by` | FK, nullable | last reviewer to fully complete the attempt |
| `roll_number`, `enrollment_no` | CharField(30), blank | snapshot from caller's roster, campus/liveclass only |
| `submitted_at`, `checked_at` | DateTime, nullable | `checked_at` only set on `status="checked"` |

Constraint: `UniqueConstraint(series, student)` name
`unique_attempt_per_student_per_series` (MVP, `attempts_allowed=1` only).

- **`submit(answers: dict, files=None)`** (`@transaction.atomic`) —
  bulk-creates one `QuestionResponse` per question, auto-grades gradable
  types, computes `auto_score`. `files` (typically `request.FILES`) is
  checked for `answer_<question_id>` on `text`-type questions only — a
  photo/file answer. If **no** `text` questions exist: resolves straight
  to `checked`, releases escrow if paid, sends `TESTSERIES_CHECKED`.
  Otherwise: `status="partially_checked"`, no notification yet.
- **`mark_answer_and_maybe_finalize(question, marks_awarded, feedback,
  reviewer)`** (`@transaction.atomic`) — grades one `text` response; if
  it was the last pending one, finalizes (`final_score`, `status=
  checked`, escrow release if paid, `TESTSERIES_CHECKED` notification).
  Otherwise only that one response is saved, no side effects.

### 4.7 `TestSeriesReview` (Task 15, NEW)

A student's rating/review of a `TestSeries`, gated on their OWN
`TestAttempt` having actually reached `status="checked"` — reviewing
requires having seen a real result, not merely having attempted the
series.

| Field | Type | Notes |
|---|---|---|
| `series` | FK → `TestSeries`, CASCADE, `related_name="reviews"` | |
| `student` | FK → `login.User`, CASCADE, `related_name="testseries_reviews"` | |
| `attempt` | **OneToOneField** → `TestAttempt`, CASCADE, `related_name="review"` | one checked attempt backs at most one review |
| `rating` | PositiveSmallIntegerField, validators `[1,5]` | |
| `comment` | TextField, blank | |
| `created_at`, `updated_at` | auto | |

Constraint: `UniqueConstraint(series, student)` name
`unique_review_per_student_per_series` — **one review per (series,
student) EVER**, not per attempt. Even once `attempts_allowed > 1`
becomes real (§17 item 2), a student still only gets one say on a
series overall. `ordering = ["-created_at"]`.

- **`clean()`** — defence-in-depth (primary 400s live in
  `TestSeriesReviewSerializer.validate()`, §8): checks `attempt`
  belongs to the same `student` and `series`, and
  `attempt.status == CHECKED`.
- **`save()`** — calls `full_clean()` then `super().save()`.
- **`create_review(cls, *, attempt, rating, comment="")`** (classmethod,
  `@transaction.atomic`) — the **only** creation entrypoint (serializer
  calls this, never `.objects.create()` directly) so the
  notify-the-creator step (`TESTSERIES_REVIEW_RECEIVED`, §3.3 — **still
  a gap**) can never be forgotten at a call site. Same "validation +
  side effect together" shape as `TestSeriesPurchase.
  purchase_and_start_attempt()`.

---

## 5. `bridge.py` — cross-app entry points (full reference)

Golden rule restated: **`campus`/`liveclass` never import `testseries`
models**; they call these functions instead. `testseries` never
queries `campus.Section`/`liveclass.Classroom` back.

### 5.1 `create_context_testseries(...)`
Used by `campus`/`liveclass` bridges to create a series (existing,
unchanged this pass — see full signature in code). `source` must be
`"campus"` or `"liveclass"` (raises `ValueError` otherwise — individual
series go through `TestSeriesViewSet.create()` instead). Creates the
`TestSeries` + bulk-creates `Question`s (each `full_clean()`-ed
individually since `bulk_create()` skips `Model.save()`), then
`recompute_total_marks()`. If `roster` is passed, fans out
`TESTSERIES_POSTED` to every user in it (lazy-imports
`core.models.Notification`).

> **BUG FIXED this pass:** this function used to import a
> `_NotifTypeGap` placeholder from `.models` that no longer exists
> (removed once `core.models.Notification.NotifType.TESTSERIES_POSTED`
> landed for real) — meaning `bridge.py` could not have been imported
> at all, so `create_context_testseries()` was **never actually
> exercised**. Fixed: `_NotifTypeGap` import dropped, `Notification.
> NotifType.TESTSERIES_POSTED` now imported lazily (function-local),
> matching the same lazy-import convention `models.py`'s own
> `_notify()`/`_record_coin_transaction()` already use.

### 5.2 `get_attempts_for_context(*, context_type, context_id)` (NEW)
`assignment.bridge.get_submissions_for_context()` ka `testseries`
analogue. Returns every `TestAttempt` across every campus/liveclass
`TestSeries` in that `(context_type, context_id)` —
`.select_related("series", "student")`, **unfiltered by permission**
(same contract `get_submissions_for_context()` documents: caller —
e.g. `campus.bridge.can_review_testseries_attempt()`, or a campus
review endpoint — does its own staff/student-scoped narrowing).

### 5.3 `ask_query_on_series(*, attempt, student, text, is_anonymous=False)` (Task 16, NEW)
Student asks the series creator a doubt about their **own** attempt,
only once it's `checked`. Reuses `message.DoubtQuestion` (no new model)
via its generic `context_type`/`context_id` pointer (§3.7). Creates a
`DoubtQuestion` with `group=None`, `conversation=None`,
`context_type="testseries_attempt"`, `context_id=<attempt.id>`.

Raises plain `ValueError` (never a DRF exception — services stay
HTTP-decoupled, same convention `message/services.py` documents) for:
- `attempt.student_id != student.id` → "You can only ask a query about
  your own attempt."
- `attempt.status != CHECKED` → "You can only ask a query after your
  attempt has been fully checked."

`views.py::TestAttemptViewSet.ask_query` catches this and returns a
clean 400. The coarser "is this even your attempt" gate is
`permissions.CanAskQueryOnCheckedAttempt` (object-level), checked
*before* this function runs.

### 5.4 `answer_query_on_series(*, doubt_id, teacher, answer_text)` (Task 16, NEW)
Answer-side counterpart. **Not in Task 16's original file list** but
added anyway — without an entrypoint here that verifies "is this
teacher actually this series' creator", the "teacher answers -> student
gets `TESTSERIES_QUERY_ANSWERED`"-style flow has no route to fire
through, since `message` itself can never resolve a testseries-creator
relationship (golden rule: no `message -> testseries` import).

Resolves `doubt.context_id` → `TestAttempt` → `TestSeries`
testseries-side, confirms `teacher == series.creator`, THEN calls
`message.services.answer_doubt_question(actor=None, answered_by=
teacher, ...)` — `actor=None` skips `message`'s own group-admin/mod
check (a testseries doubt has no group to check against); `answered_by`
is passed explicitly so the already-verified creator gets recorded as
the answerer (a previous version of this function passed neither,
silently leaving `DoubtQuestion.answered_by` unset — fixed as part of
the same Task 16 pass that added `answered_by` as its own parameter to
`answer_doubt_question()`, §3.8).

Raises `ValueError` if the doubt doesn't exist / isn't a testseries
query; `PermissionError` if `teacher` isn't that series' creator.
`views.py::answer_query` maps these to 400 / 403 respectively.

---

## 6. `permissions.py` — full reference

| Class / function | Scope | Rule |
|---|---|---|
| `IsSeriesCreatorOrReadOnly` | `TestSeriesViewSet` object-level | SAFE_METHODS → anyone; write → `obj.creator_id == request.user.id` only. Only covers the direct individual/marketplace path — campus/liveclass creation is gated further upstream (§3.5/3.6). |
| `user_can_review_attempt(user, attempt) -> bool` | plain function, used by `views.py` | `True` if `series.creator_id == user.id` (covers individual + liveclass — liveclass's teacher IS the creator). For `source="campus"`, delegates to `campus.bridge.can_review_testseries_attempt()` — **`False` (safe default, deny) until §3.4 lands**, wrapped in `try/except ImportError`. |
| `CanReviewCheckedAttempt` (Task 15) | `TestSeriesReviewViewSet.create()` `has_permission` | **Deliberately coarse**: only checks the user has *some* `TestAttempt` on the series (403 = "you never even attempted this"). Whether that attempt reached `status="checked"` is a *business rule with a payload contract* (a 400 the client should show/retry) → lives in `TestSeriesReviewSerializer.validate()` instead, not here. |
| `CanAskQueryOnCheckedAttempt` (Task 16) | `TestAttemptViewSet.ask_query` `has_object_permission` | Same coarse/fine split as above: only confirms `obj.student_id == request.user.id` (403 = "not your attempt"). The `status != checked` business rule lives in `bridge.ask_query_on_series()` (`ValueError` → 400), not here. `has_permission` just requires auth — `get_object()` already resolves "own attempt OR permitted reviewer". |

---

## 7. `views.py` — viewset/action reference

### 7.1 `TestSeriesViewSet` (`ModelViewSet`)
- `get_queryset()` — published series everywhere + your own drafts;
  `?source=` filter. Campus/liveclass context-scoping is the calling
  bridge endpoint's job, not filtered here.
- `perform_create()` — always `source="individual"`; no bulk-notify
  here (that's roster-driven, only meaningful for campus/liveclass —
  see `bridge.create_context_testseries`).
- `@action publish` — creator-only, 400 if not `draft` or zero
  questions; recomputes `total_marks`, sets `published`.

### 7.2 `QuestionViewSet` (nested, `/testseries/{series_pk}/questions/`)
Creator-only, **draft-only** writes (`_check_draft_and_owner`). Every
create/update/destroy recomputes `series.total_marks`.

### 7.3 `TestAttemptViewSet` (`Retrieve` + `List` mixins, `GenericViewSet`)
- `get_permissions()` — `ask_query` action gets
  `CanAskQueryOnCheckedAttempt` added; everything else just
  `IsAuthenticated`.
- `get_queryset()` — **LIST only**: student's own attempts + attempts
  on series they created. Deliberately does NOT try to include
  "attempts a campus subject-teacher may review" (that would mean this
  app resolving campus context-membership itself — golden rule
  violation).
- `get_object()` — **detail access resolved independently of
  `get_queryset()`**: fetches unfiltered, then allows if
  `is_owner OR user_can_review_attempt(...)`. This is a **fix** for a
  previously-flagged gap where a non-creator campus subject-teacher
  could never reach the permission check at all (filtered out by the
  narrower list-scope queryset first) — does not widen who can review
  anything, just makes the existing check reachable.
- `@action start` (`POST /attempts/start/{series_id}/`) — idempotent
  (existing attempt returned as-is, never re-charged); handles paid
  (`purchase_and_start_attempt`, 402 on insufficient balance) and
  unpaid paths; `IntegrityError` race handling (loses the race →
  returns winner's attempt instead of 500); `roll_number`/
  `enrollment_no` coerced to `str(...)[:30]`.
- `@action submit` (`POST /attempts/{id}/submit/`) — own-student only,
  must be `in_progress`. Accepts JSON or `multipart/form-data` (JSON
  string field for `answers` + `answer_<question_id>` files). Validates
  every `request.FILES` entry starting with `answer_` against the
  shared attachment validators before calling `attempt.submit()`.
- `@action review_answer` (`POST /attempts/{id}/answer/{question_id}/
  review/`) — `user_can_review_attempt()` gate; `text`-type only (400
  otherwise); validates `marks_awarded` is present/int/non-negative/
  within question marks; calls `mark_answer_and_maybe_finalize()`.
- `@action ask_query` (`POST /attempts/{id}/ask-query/`) (Task 16, NEW)
  — `get_object()` already resolves "own OR permitted reviewer";
  `CanAskQueryOnCheckedAttempt` narrows to "own attempt only" for this
  action specifically. Validates `text` non-empty; calls
  `bridge.ask_query_on_series()`, `ValueError → 400`. Returns a minimal
  hand-rolled dict (`id`, `text`, `is_anonymous`, `context_type`,
  `context_id`) — **flagged as temporary**: swap for a real
  `DoubtQuestionSerializer` once `message`'s actual `DoubtQuestion`
  shape is fully confirmed.
- `@action answer_query` (`POST /attempts/{id}/answer-query/`) (Task
  16, NEW) — teacher-facing counterpart. Body: `doubt_id`,
  `answer_text`. `get_object()` resolves "own OR permitted reviewer"
  (a series creator always qualifies); `bridge.
  answer_query_on_series()` does the finer "are YOU actually the
  creator" check (`ValueError → 400`, `PermissionError → 403`). Extra
  belt-and-suspenders check: `doubt.context_id` must match the URL's
  attempt `pk`, else 400 (catches a client sending the wrong attempt id
  for a given `doubt_id` — never a security gap since
  `answer_query_on_series()` already independently verifies creator
  off the doubt itself).

### 7.4 `TestSeriesReviewViewSet` (Task 15, NEW — `Create` + `List` mixins)
Two access shapes, both wired manually in `urls.py` (no
`DefaultRouter`, same "`series_pk` explicit in URL" reasoning as
questions):
- **`/testseries/<series_pk>/reviews/`** (`list`, `create`) — `list` is
  a public read of one series' reviews; `create` gated by
  `CanReviewCheckedAttempt` (coarse) + `TestSeriesReviewSerializer.
  validate()` (checked-status / already-reviewed business rules).
  `perform_create()` catches `IntegrityError` (lost a create-race
  against `unique_review_per_student_per_series`) → clean 400 instead
  of 500.
- **`/testseries/reviews/my-view/`** (`my_view`, GET only, no
  `series_pk`) — creator-aggregate dashboard: every review across
  every series **this** user created (`series__creator=request.user`,
  never anyone else's), plus a per-series `{avg, count}` rollup
  (`TestSeries.annotate(Avg("reviews__rating"), Count("reviews"))`,
  filtered to `count__gt=0`).

---

## 8. Serializers — visibility & validation rules

- `QuestionSerializer` — `correct_answer` stripped from the response
  unless `request.user` is `series.creator`. `validate()` mirrors
  `Question.clean()` (builds a throwaway unsaved `Question` instance,
  not `self.instance.__dict__` — that dict carries internal Django
  state like `_state` which isn't a valid field kwarg) so a bad shape
  returns a clean DRF 400. `validate_attachment()` mirrors the
  model-level FileField validators.
- `QuestionResponseSerializer` — a student's own still-ungraded `text`
  response has `reviewer_feedback` blanked out until actually reviewed
  (nothing to leak for auto-graded ones — those resolve instantly at
  submit-time).
- `TestSeriesSerializer` — `avg_rating`/`review_count` (Task 15) are
  declared explicitly as `FloatField(read_only=True)`/
  `IntegerField(read_only=True)` — required because they're **model
  properties, not DB fields**, so `ModelSerializer` won't pick them up
  automatically otherwise. `validate()` enforces `price_coins=0` when
  `is_paid=False`, and rejects `is_paid=True` for `source="campus"` at
  the API layer too (model `save()` already normalizes this silently;
  the serializer turns it into an honest 400). `source`, `context_type`,
  `context_id`, `status` are `read_only_fields` — provenance/workflow
  fields set once at creation (or via `publish()`, for `status`); none
  are client-writable through `PATCH`/`PUT`.
- `TestAttemptSerializer` — `series`, `roll_number`, `enrollment_no`
  (alongside the others already listed in §4.6) are `read_only_fields`
  — defence-in-depth: no create/update action is wired to this
  serializer today, but if one ever is, these stay set only via
  `start()`'s own snapshot.
- `TestSeriesReviewSerializer` (Task 15, NEW) — `student`/`series` are
  `PrimaryKeyRelatedField(read_only=True)`; `rating` is declared
  explicitly as `IntegerField(min_value=1, max_value=5)` so an
  out-of-range value is a clean 400 at the field-validation layer,
  instead of surfacing later as a `DjangoValidationError` from
  `full_clean()` inside `create()`. `validate()`:
  1. Pulls `series` from `context` (only present on the nested
     `/testseries/{series_pk}/reviews/` route — the only route
     `create()` is reachable from). Missing → "Reviews must be created
     via a specific series' reviews endpoint."
  2. Finds the student's own attempt on that series
     (`order_by("-attempt_number").first()`) — none → "You must attempt
     this series before reviewing it."
  3. `attempt.status != CHECKED` → "You can only review this series
     after your attempt has been fully checked." **(this is the
     acceptance-checklist's core rule — see `permissions.
     CanReviewCheckedAttempt`'s docstring for why this lives here and
     not in a permission class.)**
  4. Already reviewed (`TestSeriesReview.objects.filter(series,
     student).exists()`) → "You have already reviewed this series."
  `create()` calls `TestSeriesReview.create_review(...)`, catching
  `DjangoValidationError` → `serializers.ValidationError` (same
  model-to-DRF conversion shape `QuestionSerializer.validate()` uses;
  only fires on a genuine race in the gap between `validate()` and
  `create()`).

---

## 9. Permissions matrix (full, updated)

| Action | Who's allowed |
|---|---|
| Create individual series | any authenticated user |
| Create campus series | **not via this app's API** — only `campus`'s own staff/teacher-checked proxy endpoint, via `bridge.create_context_testseries()` (§3.5, still open) |
| Create liveclass series | **not via this app's API** — only `liveclass`'s own teacher-checked proxy endpoint, via `bridge.create_context_testseries()` (§3.6, still open) |
| Edit/delete a series | `series.creator` only |
| Add/edit/delete questions | `series.creator` only, and only while `series.status="draft"` |
| Start/submit an attempt | the student themself |
| Review a `text` response | `series.creator` (covers individual + liveclass) **or** campus subject-teacher, resolved via `campus.bridge.can_review_testseries_attempt()` (§3.4 — until that lands, campus reviewers get `False`) |
| Review a series (Task 15) | any authenticated user who attempted it (coarse, `CanReviewCheckedAttempt`) — but only actually succeeds once their own attempt is `checked` (serializer-level business rule) |
| View a series' reviews | anyone (public list on `/testseries/{id}/reviews/`) |
| View creator review dashboard (`my-view`) | any authenticated user — but only ever sees **their own** created series' reviews |
| Ask creator a query (Task 16) | the attempt's own student, only once `checked` |
| Answer a query (Task 16) | only the series' actual `creator` — verified independently inside `bridge.answer_query_on_series()`, not by `get_object()`'s coarser "own or permitted reviewer" check alone |

---

## 10. Admin

- `TestSeriesAdmin` — list/filter by `source`/`status`/`is_paid`,
  inline `Question` editing.
- `TestSeriesPurchaseAdmin` — **fully read-only** (`has_add_permission`
  → `False`, all fields readonly) — state changes only ever happen
  through `release()`/`refund()` so the CoinLedger side-effect is never
  skipped by an admin edit.
- `TestAttemptAdmin` — inline `QuestionResponse` (read-only on the
  auto-graded fields; `answer_attachment` shown so a reviewer can open a
  student's photo/file answer directly from admin).
- **No `TestSeriesReviewAdmin` yet** — Task 15 didn't add one; reviews
  are currently only manageable via the API (`TestSeriesReviewViewSet`)
  or the Django shell/ORM. Add one if a support/moderation need for
  editing/deleting reviews from admin shows up later.

---

## 11. Celery tasks (`tasks.py`)

| Task | Schedule | What |
|---|---|---|
| `send_pending_check_reminders` | daily | Notifies creators with `submitted`/`partially_checked` attempts older than `TESTSERIES_REMINDER_DAYS`. Uses `NotifType.GENERIC` (string literal `"generic"`) — no dedicated reminder type exists in the confirmed enum list (§3), not guessed. |
| `refund_unchecked_paid_attempts` | daily | Auto-refunds any `escrowed` purchase older than `TESTSERIES_AUTO_REFUND_DAYS` whose attempt never reached `checked`. This is the exploit-guard: without it, a creator could hold a paid attempt forever without reviewing it. |

Neither task touches reviews or doubt-queries — no Celery follow-up
was added for Task 15/16 (e.g. no "nudge student to review after
checked" reminder exists yet — see §17 open items).

---

## 12. API reference (full, updated)

Base path: whatever `testseries.urls` is mounted at (example: `/api/`).
All endpoints require `IsAuthenticated`.

### 12.1 `TestSeries` — `/testseries/`
| Method | Path | Who | Notes |
|---|---|---|---|
| `GET` | `/testseries/` | any auth user | `?source=individual\|campus\|liveclass` filter. Returns published series + your own drafts. Response now includes `avg_rating`/`review_count` (Task 15). |
| `GET` | `/testseries/{id}/` | any auth user | same, single series |
| `POST` | `/testseries/` | any auth user | **Always creates `source="individual"`**. Body: `title`, `description`, `is_paid`, `price_coins`, `duration_minutes`, `attempts_allowed`. |
| `PUT`/`PATCH` | `/testseries/{id}/` | creator only | `source`/`context_type`/`context_id`/`status` are read-only, cannot be changed here |
| `DELETE` | `/testseries/{id}/` | creator only | |
| `POST` | `/testseries/{id}/publish/` | creator only | 400 if not `draft` or has zero questions. Recomputes `total_marks`, sets `status="published"`. |

### 12.2 `Question` — `/testseries/{series_id}/questions/`
| Method | Path | Who | Notes |
|---|---|---|---|
| `GET` | `.../questions/` | any auth user | `correct_answer` hidden unless you're the series creator |
| `GET` | `.../questions/{id}/` | any auth user | same hiding rule |
| `POST` | `.../questions/` | series creator, **draft only** | body per §4.3 shape table |
| `PUT`/`PATCH` | `.../questions/{id}/` | series creator, **draft only** | |
| `DELETE` | `.../questions/{id}/` | series creator, **draft only** | |

Every write recomputes `series.total_marks`.

### 12.3 `TestAttempt` — `/attempts/`
| Method | Path | Who | Notes |
|---|---|---|---|
| `GET` | `/attempts/` | student sees own; creator sees attempts on own series | |
| `GET` | `/attempts/{id}/` | same, plus a campus subject-teacher (§3.4) once that lands | |
| `POST` | `/attempts/start/{series_id}/` | any auth user | Idempotent — existing attempt (incl. paid retry) returned as-is, never double-charged. Body: `roll_number`, `enrollment_no` (campus/liveclass only, coerced to string, clipped to 30 chars). **402** if insufficient coins. Concurrent double-`start()` handled via `IntegrityError` → loser gets winner's attempt. |
| `POST` | `/attempts/{id}/submit/` | attempt's own student only | Body: `{"answers": {question_id: answer_data, ...}}` as JSON, **or** `multipart/form-data` (`answers` as JSON string + `answer_<question_id>` files, `text` questions only). 400 if not `in_progress`, or a file fails extension/size checks (§15). |
| `POST` | `/attempts/{id}/answer/{question_id}/review/` | series creator, or campus reviewer per `user_can_review_attempt()` | Body: `{"marks_awarded": N, "feedback": "..."}`. 400 if not `text`-type, missing/non-integer/negative, or exceeds question marks. |
| `POST` | `/attempts/{id}/ask-query/` | attempt's own student, **checked only** | (Task 16, NEW) Body: `{"text": "...", "is_anonymous": false}`. 400 if `text` empty or attempt not `checked` yet. Returns a minimal doubt dict. |
| `POST` | `/attempts/{id}/answer-query/` | series creator only | (Task 16, NEW) Body: `{"doubt_id": "...", "answer_text": "..."}`. 400 if fields missing, doubt not found, or `doubt_id` doesn't belong to this attempt's URL. 403 if you're not the series creator. |

### 12.4 `TestSeriesReview` (Task 15, NEW)
| Method | Path | Who | Notes |
|---|---|---|---|
| `GET` | `/testseries/{series_pk}/reviews/` | any auth user | public list of one series' reviews |
| `POST` | `/testseries/{series_pk}/reviews/` | anyone who attempted the series | 400 if attempt not `checked`, or already reviewed. Body: `{"rating": 1-5, "comment": "..."}`. |
| `GET` | `/testseries/reviews/my-view/` | any auth user | creator-aggregate dashboard — **only your own created series'** reviews + `{avg, count}` per series |

### 12.5 Example payloads

**Create individual paid series:**
```json
POST /testseries/
{ "title": "UPSC Prelims Mock 4", "is_paid": true, "price_coins": 50,
  "duration_minutes": 120, "attempts_allowed": 1 }
```

**Add an MSQ question:**
```json
POST /testseries/{id}/questions/
{ "order": 1, "question_type": "msq", "text": "Select all prime numbers.",
  "marks": 4,
  "options": [{"id":"a","text":"2"},{"id":"b","text":"4"},{"id":"c","text":"7"}],
  "correct_answer": {"option_ids": ["a","c"]} }
```

**Submit an attempt:**
```json
POST /attempts/{id}/submit/
{ "answers": {
    "<question_uuid_1>": {"option_id": "a"},
    "<question_uuid_2>": {"text": "My subjective answer..."}
} }
```

**Submit an attempt with a photo answer (multipart):**
```
POST /attempts/{id}/submit/
Content-Type: multipart/form-data

answers: '{"<question_uuid_1>": {"option_id": "a"}, "<question_uuid_2>": {}}'
answer_<question_uuid_2>: <file: handwritten_answer.jpg>
```

**Review a text answer:**
```json
POST /attempts/{id}/answer/{question_id}/review/
{ "marks_awarded": 3, "feedback": "Good structure, missing one example." }
```

**Leave a review (Task 15, NEW):**
```json
POST /testseries/{series_id}/reviews/
{ "rating": 5, "comment": "Great questions, tough but fair." }
```

**Ask a query on a checked attempt (Task 16, NEW):**
```json
POST /attempts/{id}/ask-query/
{ "text": "Why did I lose marks on Q3?", "is_anonymous": false }
```

**Answer a query (Task 16, NEW):**
```json
POST /attempts/{id}/answer-query/
{ "doubt_id": "<doubt_uuid>", "answer_text": "You missed the second condition." }
```

---

## 13. Cross-app interconnections — consolidated summary

Har jagah jahan `testseries` doosre apps ko chhoo raha hai:

| Other app | Direction | What's used | Notes / gaps |
|---|---|---|---|
| `login.User` | `testseries` imports directly | `creator`/`student`/`buyer`/`reviewed_by`/`checked_by` FKs | Not a bridge case — `User` is the shared identity model every app uses directly, same as everywhere else in the codebase. |
| `campus` | `campus -> testseries` only (never reverse) | `campus`'s own proxy endpoint calls `bridge.create_context_testseries(source="campus", ...)`; `testseries.permissions.user_can_review_attempt()` calls `campus.bridge.can_review_testseries_attempt()` (§3.4, still a gap) | Golden rule: `testseries` never imports `campus.Section` etc. Context is opaque (`context_type`/`context_id`). |
| `liveclass` | `liveclass -> testseries` only | Same shape as campus — `liveclass.bridge.create_testseries()` (§3.6, still a gap) calls `create_context_testseries(source="liveclass", ...)` with teacher-chosen `is_paid`/`price_coins` | No server-side force on `is_paid` for liveclass (unlike campus). |
| `user_profile` | `testseries -> user_profile` (lazy import) | `CoinLedger.record_transaction()` via `_record_coin_transaction()` — used for purchase debit, payout release, refund | Enum members `TESTSERIES_PURCHASE`/`TESTSERIES_PAYOUT` confirmed to exist (§3.1, resolved). `REFUND` type reused as-is for refunds. |
| `core` | `testseries -> core` (lazy import) | `core.services.create_notification()` via `_notify()` — `TESTSERIES_POSTED`, `TESTSERIES_CHECKED`, `TESTSERIES_PAYOUT_RELEASED` (all resolved, §3.2), `TESTSERIES_REVIEW_RECEIVED` (**still a gap, §3.3**), and a plain `"generic"` string for the reminder task | |
| `message` | `testseries -> message` (lazy import) **(Task 16, NEW)** | `message.models.DoubtQuestion` (created via `bridge.ask_query_on_series()`), `message.services.answer_doubt_question()` (via `bridge.answer_query_on_series()`) | Assumes `DoubtQuestion.context_type`/`context_id` generic pointer fields exist (§3.7) and `answer_doubt_question()` accepts an `answered_by` kwarg (§3.8) — **both assumed added this pass, not independently verified against `message`'s real source**. Confirm before relying on this in production. `message` never imports `testseries` back. |
| `assignment` | no direct link | `Question.auto_grade()` uses the **shared** `common.question_grading.auto_grade()` module — same grading logic as `assignment`, avoiding duplication. Attachment validators (`common.attachment_validators`) are also shared with `assignment` (Task 7). | Not a runtime cross-app call, just shared utility code both apps import from `common`. |

---

## 14. Tests (`tests.py`) — Task 17, NEW

`RegressionLockTests` (Django `TestCase`) locks in the three sequencing
rules introduced/hardened in Tasks 15/16 so a future change can't
silently regress them:

1. **`test_review_rejected_before_checked_status`** — an `in_progress`
   attempt cannot back a review; `TestSeriesReview.create_review()`
   raises `django.core.exceptions.ValidationError`, and no row gets
   created.
2. **`test_query_rejected_before_checked_status`** — an `in_progress`
   attempt cannot ask a query; `bridge.ask_query_on_series()` raises
   plain `ValueError`.
3. **`test_payout_only_releases_on_checked_not_partially_checked`** —
   builds a paid series with one auto-graded (`mcq`) + one manual
   (`text`) question, submits (→ `partially_checked`, escrow **must
   stay `escrowed`**, `CoinLedger` mock **not called**), then reviews
   the text question (→ finalizes to `checked`, escrow **releases**,
   `CoinLedger` mock called exactly once).

**Flagged assumptions (already noted in the test file's own docstring,
repeated here so they're not missed):**
- `_make_user()` is the **only** place a `User` is constructed — tries
  `create_user(username=..., password=...)` first, falls back to
  `email=...` on `TypeError`. If the real custom `User` model needs
  different required fields, fix this one helper only.
- The payout test **mocks** `testseries.models._record_coin_transaction`
  and `testseries.models._notify` rather than exercising real
  `CoinLedger`/`Notification` machinery — its actual subject is the
  **status state machine** (checked vs. partially_checked gating
  release), not ledger/notification correctness (assumed to have its
  own tests elsewhere). The enum members themselves (`CoinLedger.
  TransactionType.TESTSERIES_PAYOUT`, `Notification.NotifType.
  TESTSERIES_PAYOUT_RELEASED`) are still resolved for real inside the
  mocked functions — only the DB row-creation is stubbed.
- No existing `testseries/tests.py` was shared/found, so this is
  written as a fresh file — if one already existed in the real repo,
  merge `RegressionLockTests` into it rather than overwriting.

**Not covered by these tests (gap, not a regression risk yet since
nothing exercises them):** `ask-query`/`answer-query` **view-layer**
behavior (permissions, 400/403 mapping), `TestSeriesReviewViewSet`
(list/create/my-view), and the `already-reviewed`/`IntegrityError`
race-handling paths. Task 17 only locked the three sequencing rules
explicitly called out above — a fuller API-level test suite is still
open work.

---

## 15. Answer & question attachments (image/file support)

Two separate attachment points exist, both `FileField`, both validated
by the same shared rules:

| Field | Purpose | Set by |
|---|---|---|
| `Question.attachment` | The question's own image/PDF (e.g. a diagram a question refers to) | series creator, via `QuestionSerializer` (`POST`/`PATCH .../questions/`) |
| `QuestionResponse.answer_attachment` | A student's photo/file answer (e.g. a photo of handwritten work) | student, via `TestAttempt.submit()` — **not** exposed as a writable serializer field, only ever set from `request.FILES` inside the `submit` action |

**Validation (defence-in-depth, three layers), all via the shared
`common.attachment_validators` module (also used by `assignment`):**
1. `FileField(validators=[attachment_extension_validator,
   validate_attachment_size])` on both model fields (`models.py`) —
   enforced whenever `full_clean()` runs (`Question.save()`).
2. `QuestionSerializer.validate_attachment()` — same two checks, run at
   the DRF layer so a bad `Question.attachment` upload is a clean 400
   before the model is ever touched.
3. `views.py::TestAttemptViewSet.submit()` — same two checks run
   directly against every `request.FILES` entry whose key starts with
   `answer_`, since `answer_attachment` never goes through a serializer
   write path at all (see table above).

**Defaults** (overridable via settings, §2): extensions
`pdf`/`jpg`/`jpeg`/`png`/`webp`, max size `10MB`.

**`submit()` accepts two content types** — plain JSON (unchanged, no
attachment) or `multipart/form-data` (to attach a file): see §12.5 for
both example payloads. The file-field naming convention is
`answer_<question_id>` (the question's UUID); a file sent against an
auto-graded (`mcq`/`msq`/`list`) question is silently ignored —
attachments only ever apply to `text`-type answers.

**Not handled by this app:** actual file storage backend (local disk
vs. S3/GCS/etc.) is whatever the project's `DEFAULT_FILE_STORAGE` /
`STORAGES` setting already points at. Nothing `testseries`-specific to
configure there.

---

## 16. Production-hardening pass (fixed prior to this pass) — changelog

Historical record, kept for context — all already reflected in the
sections above and in the code itself.

1. **`TestSeriesSerializer` write-access gap.** `source`,
   `context_type`, `context_id`, `status` were writable via
   `PATCH`/`PUT`. Fixed: all four are `read_only_fields` (§8).
2. **`Question.save()`'s `full_clean(exclude=...)` was inverted** —
   excluded everything *except* `options`/`correct_answer`, silently
   skipping required-field + uniqueness checks. Fixed: exclude list is
   now just `["options", "correct_answer"]` (§4.3).
3. **Negative `marks_awarded` wasn't rejected** at either
   `views.py::review_answer` or `QuestionResponse.mark_answer()`.
   Fixed at both layers (§4.4, §12.3).
4. **`TestAttemptViewSet.start()` had dead code + a real race gap.**
   Removed dead branch; added `IntegrityError` handling — losing
   request gets the winner's attempt back (§12.3).
5. **`start()`'s `roll_number`/`enrollment_no` weren't type/length
   guarded.** Fixed: coerced to `str(...)[:30]` (§12.3).

**This pass's own fixes (Task 16), added to the changelog:**

6. **`bridge.py` had a fatal `ImportError` at module load** —
   `create_context_testseries()`'s notify block imported the
   already-removed `_NotifTypeGap` placeholder. Fixed: dropped the
   dead import, `Notification.NotifType.TESTSERIES_POSTED` now resolved
   lazily inside the function, same as every other `_notify()` call
   site in `models.py` (§5.1).
7. **`answer_query_on_series()` previously dropped `answered_by`
   entirely** when calling `message.services.answer_doubt_question()`,
   leaving `DoubtQuestion.answered_by` unset even after a successful
   answer. Fixed: `answered_by=teacher` now passed explicitly (§5.4,
   §3.8).

---

## 17. Open items (carried over, still valid — updated)

1. **Platform fee / cut** — 100% of `price_coins` goes to the creator,
   no commission. Separate pricing decision, out of scope here.
2. **`attempts_allowed > 1`** — `attempt_number` field exists as
   groundwork, but the real `(series, student, attempt_number)`
   uniqueness shape + serializer count-check is **not implemented**.
   This also caps `TestSeriesReview` at "one per series ever" rather
   than "one per attempt" (§4.7) — a deliberate choice, not a bug, but
   worth re-confirming once multi-attempt actually ships.
3. **`msq`/`list` partial credit is a non-goal** in this version —
   exact-match, all-or-nothing only.
4. **Unpaid attempts never create a `TestSeriesPurchase` row** — the
   `if series.is_paid and purchase` guard in both `submit()` and
   `mark_answer_and_maybe_finalize()` is intentional.
5. **`QuestionResponse.reviewed_by`/`reviewed_at`** only meaningfully
   set for `text` responses — UI should distinguish "auto-graded" vs
   "reviewed by X" badges.
6. **`TESTSERIES_REVIEW_RECEIVED` notify gap (NEW, §3.3)** — review
   creation works end-to-end today; only the creator-notify step will
   `AttributeError` until `core` adds the enum member.
7. **`campus.bridge.can_review_testseries_attempt()` still doesn't
   exist (NEW carry-over, §3.4)** — campus subject-teachers get a hard
   `False` (denied) on both attempt-review and, transitively, on
   `get_attempts_for_context()`-based review endpoints campus might
   build on top of it.
8. **No dedicated Celery reminder for "please leave a review" or
   "you have an unanswered doubt query"** — only the existing
   check-pending / auto-refund tasks exist (§11). Worth adding if
   review/query engagement turns out to matter for the product.
9. **`ask_query`/`answer_query` response shape is a hand-rolled dict**,
   not a real serializer (§7.3) — swap once `message.DoubtQuestion`'s
   confirmed shape is available.
10. **No `TestSeriesReviewAdmin`** (§10) — reviews aren't editable from
    Django admin yet.
11. **`message` cross-app assumptions unverified** (§3.7, §3.8) — the
    `DoubtQuestion.context_type`/`context_id` fields and
    `answer_doubt_question(answered_by=...)` signature are assumed
    landed this pass per `bridge.py`'s own comments, but `message`'s
    source wasn't available to cross-check directly against
    `testseries`. Confirm before relying on Task 16 in production.

---

## 18. Pre-flight checklist (before first real deploy)

- [x] §3.1 — `CoinLedger.TransactionType` enum landed, shim removed
- [x] §3.2 — `Notification.NotifType` (`TESTSERIES_POSTED`/`_CHECKED`/`_PAYOUT_RELEASED`) landed, shim removed
- [ ] §3.3 — `Notification.NotifType.TESTSERIES_REVIEW_RECEIVED` — **still open**
- [ ] §3.4 — `campus.bridge.can_review_testseries_attempt()` implemented — **still open**
- [ ] §3.5 — `campus.bridge.create_testseries()` + campus proxy endpoint implemented — **still open**
- [ ] §3.6 — `liveclass.bridge.create_testseries()` implemented — **still open**
- [ ] §3.7 — confirm `message.DoubtQuestion.context_type`/`context_id` fields actually exist/migrated — **verify, assumed only**
- [ ] §3.8 — confirm `message.services.answer_doubt_question()` accepts `answered_by=` — **verify, assumed only**
- [ ] `testseries/migrations/` generated and applied (incl. `TestSeriesReview` table, Task 15)
- [ ] `testseries.urls` mounted in project `urls.py` (incl. new review routes)
- [ ] Celery beat schedule entries added (§2)
- [ ] `TESTSERIES_AUTO_REFUND_DAYS`/`TESTSERIES_REMINDER_DAYS` reviewed (defaults: 14 / 3)
- [ ] `TESTSERIES_ATTACHMENT_EXTENSIONS`/`TESTSERIES_ATTACHMENT_MAX_MB` reviewed (defaults: pdf/jpg/jpeg/png/webp, 10MB) — confirm project's file storage backend is configured
- [ ] `testseries/tests.py::RegressionLockTests` passing against the real `User` model (verify `_make_user()`'s `create_user()` call shape matches your custom `User` — §14)
- [ ] Manually smoke-test `ask-query`/`answer-query` end-to-end once §3.7/§3.8 are confirmed — no automated test coverage exists for these yet (§17 item 11, §14)