# `testseries` App — Implementation Reference (v1, as built + production-hardening pass)

> Ye woh single doc hai jisse **sara kaam ho sakta hai** — settings wiring,
> prerequisite migrations, API integration (campus/liveclass bridge calls
> ho ya frontend), permissions samajhna, ya sirf "ye field kya karta hai"
> lookup. `testseries_app_design.md` (original proposal) ab **superseded**
> hai is doc se — jo bhi differ karta hai, ye doc (as-built) authoritative
> hai. Code files khud (`models.py` etc.) source-of-truth rehte hain, lekin
> unhe padhne ki zaroorat tabhi hai jab actual line-by-line implementation
> dekhni ho — is doc me har cheez already extract ki hui hai.

---

## 0. File structure (delivered)

```
testseries/
├── __init__.py          # (khud add karna — empty file, Python package marker)
├── apps.py              # AppConfig
├── models.py            # TestSeries, Question, QuestionResponse, TestSeriesPurchase, TestAttempt
├── serializers.py        # DRF serializers, answer-key hiding
├── permissions.py        # IsSeriesCreatorOrReadOnly, user_can_review_attempt()
├── views.py              # TestSeriesViewSet, QuestionViewSet, TestAttemptViewSet
├── urls.py                # router + nested question routes
├── admin.py               # Django admin registration
├── bridge.py              # create_context_testseries() — campus/liveclass entry point
├── tasks.py               # 2 Celery tasks (§7 safety nets)
└── migrations/            # (khud generate karna: `manage.py makemigrations testseries`)
```

**Missing on purpose, not forgotten:** koi `serializers.py`-level `views.py`
me campus/liveclass-specific viewset nahi hai — us wiring ka apna
proxy-endpoint `campus/views.py` / `liveclass/views.py` me banega (jaisa
`assignment` app ke liye tha), jo `testseries.bridge.
create_context_testseries()` ko call karega. **Is app ke andar wo proxy
file nahi hai** — golden rule (§1 neeche) ke hisaab se `testseries` khud
campus/liveclass ka URL-space nahi define karta.

---

## 1. Golden rules (non-negotiable, sab jagah enforce hui hain)

1. **`testseries` kabhi `campus`/`liveclass` models seedha import nahi
   karta.** Context `context_type` (CharField) + `context_id` (UUID) se
   opaque reference hota hai. Resolve karna caller (bridge) ka kaam hai.
2. **`user_profile.CoinLedger` seedha use hota hai** (bridge ke bina) —
   `_record_coin_transaction()` helper (`models.py`) lazy-imports it.
3. **Cross-app enum dependencies kabhi guess nahi ki jaatin** — jahan
   `user_profile`/`core` ka apna enum extend karna padta, wahan ek
   explicit GAP-bridging shim (`_TransactionTypeGap`, `_NotifTypeGap` in
   `models.py`) rakha gaya hai jo safe default (string literal ya `False`)
   deta hai jab tak real enum land na ho jaaye. §4 me poori checklist hai.
4. **Escrow release sirf full `checked` status pe** — partial-review pe
   kabhi payout nahi (koi platform-fee cut bhi abhi nahi, §11 open item).

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

# Optional overrides (defaults shown — see models.py's attachment
# validators, used by both Question.attachment and
# QuestionResponse.answer_attachment):
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
        "schedule": crontab(hour=9, minute=0),   # daily, per §7
    },
    "testseries-refund-unchecked-paid-attempts": {
        "task": "testseries.tasks.refund_unchecked_paid_attempts",
        "schedule": crontab(hour=9, minute=30),  # daily, per §7
    },
}
```

---

## 3. Prerequisite migrations — **MUST land before this app is usable**

These live in **other** apps' owner-scope (`user_profile`, `core`), not
`testseries`'s own migrations. Until they land, `testseries` still runs
(no `TextChoices` DB-level enforcement in Django), but admin/reporting
will show unlabeled raw strings and analytics filtering on those choices
lists will silently miss testseries rows.

### 3.1 `user_profile/models.py` — `CoinLedger.TransactionType`
```python
class TransactionType(models.TextChoices):
    ...  # existing choices unchanged
    TESTSERIES_PURCHASE = "testseries_purchase", "Test Series Purchase"
    TESTSERIES_PAYOUT   = "testseries_payout", "Test Series Payout"
    # REFUND already exists — reused as-is, no new choice needed for refunds.
```
Once added: in `testseries/models.py`, replace every
`_TransactionTypeGap.X` usage with `CoinLedger.TransactionType.X` and
delete the `_TransactionTypeGap` shim class.

### 3.2 `core/models.py` — `Notification.NotifType`
```python
class NotifType(models.TextChoices):
    ...  # existing choices unchanged
    TESTSERIES_POSTED          = "testseries_posted", "New Test Series"
    TESTSERIES_CHECKED         = "testseries_checked", "Test Checked"
    TESTSERIES_PAYOUT_RELEASED = "testseries_payout_released", "Test Series Payout Released"
```
Once added: replace every `_NotifTypeGap.X` usage with
`Notification.NotifType.X` and delete the `_NotifTypeGap` shim class.

### 3.3 `campus/bridge.py` — new function (permission resolver)
```python
def can_review_testseries_attempt(*, user, context_type: str, context_id) -> bool:
    """Return True iff `user` is the subject-teacher/staff allowed to
    review testseries attempts for this campus context (section/subject).
    Called from testseries/permissions.py::user_can_review_attempt() —
    until this exists, campus reviewers get a hard `False` (safe
    default) from that function."""
```

### 3.4 `campus/bridge.py` — new function (creation entry point)
```python
def create_testseries(*, section, subject, staff, title, description="",
                       questions, roster) -> "TestSeries":
    """Calls testseries.bridge.create_context_testseries(source="campus",
    context_type="section", context_id=section.id, is_paid=False,
    creator=staff, questions=questions, roster=roster, ...). staff's
    permission to post for this section/subject is checked HERE, before
    this function calls into testseries at all — testseries.bridge trusts
    the caller."""
```
Needs its own `campus/urls.py` + `views.py` proxy `TestSeriesViewSet`
(same proxy pattern as `assignment` app, per original design doc §6).

### 3.5 `liveclass/bridge.py` — new function
```python
def create_testseries(*, classroom, teacher, title, description="",
                       is_paid, price_coins, questions) -> "TestSeries":
    """Calls testseries.bridge.create_context_testseries(source="liveclass",
    context_type="classroom", context_id=classroom.id, creator=teacher,
    is_paid=is_paid, price_coins=price_coins, questions=questions,
    roster=classroom.enrolled_students(), ...). is_paid/price_coins come
    from the teacher's own "paid ya unpaid?" UI step — no force here,
    unlike campus."""
```

**Checklist before writing any campus/liveclass-side code against this
app:** §3.1 and §3.2 land first (cross-app owner-scope, `testseries`
itself can't migrate those tables). §3.3–§3.5 can follow once §3.1/§3.2
are in.

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
| `is_paid` | BooleanField, default `False` | **force-`False`** for `source="campus"` in `save()` — defence-in-depth on top of §5.3 serializer check and the campus bridge's own force |
| `price_coins` | PositiveIntegerField, default `0` | force-`0` in `save()` whenever `is_paid=False` |
| `duration_minutes` | PositiveIntegerField, nullable | |
| `total_marks` | PositiveIntegerField, default `0` | denormalized sum of `Question.marks`; call `series.recompute_total_marks()` after any question change (views already do this) |
| `status` | choices: `draft`/`published`/`archived` | question set locked once non-`draft` |
| `attempts_allowed` | PositiveIntegerField, default `1` | multi-attempt (>1) is **not fully implemented** — see §11 item 2 |
| `created_at`, `updated_at` | auto | |

Indexes: `(source, context_type, context_id)`, `(creator, status)`.

**`save()`** — normalizes `is_paid`/`price_coins` per the rules above.
**`recompute_total_marks(save=True)`** — `Sum("questions__marks")`.

### 4.3 `Question`
| Field | Type | Notes |
|---|---|---|
| `series` | FK CASCADE | |
| `order` | PositiveIntegerField | `unique_together=("series","order")`, `ordering=["order"]` |
| `question_type` | choices: `text`/`mcq`/`msq`/`list` | `db_index=True` |
| `text` | TextField | question statement |
| `attachment` | FileField(`upload_to="testseries/questions/"`), nullable | extension + size validated — see §12 |
| `marks` | PositiveIntegerField | max marks for this question |
| `options` | JSONField, default `list` | shape depends on `question_type` — table below |
| `correct_answer` | JSONField, default `dict` | shape depends on `question_type` — table below |

**Shape per `question_type`:**

| Type | `options` | `correct_answer` | Auto-graded? |
|---|---|---|---|
| `text` | `[]` (force-emptied) | `{}` (force-emptied) | No — always manual review |
| `mcq` | `[{"id": "a", "text": "..."}]` | `{"option_id": "a"}` | Yes — exact match |
| `msq` | same as `mcq` | `{"option_ids": ["a","c"]}` | Yes — **exact-set** match (no partial credit, §11 item 4) |
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
clean `ValidationError` instead of a raw DB `IntegrityError`.
**`auto_grade(answer_data) -> (is_correct, marks_awarded)`** — returns
`(None, None)` for `text`.

### 4.4 `QuestionResponse`
| Field | Type | Notes |
|---|---|---|
| `attempt`, `question` | FK CASCADE each | `UniqueConstraint(attempt, question)` name `unique_response_per_attempt_question` |
| `answer_data` | JSONField, default `dict` | same shape as `Question.correct_answer` for that type |
| `answer_attachment` | FileField(`upload_to="testseries/answers/"`), nullable | a student's photo/file answer — only ever set for `text`-type responses; extension + size validated — see §12 |
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
| `attempt_number` | PositiveIntegerField, default `1` | groundwork only — see §11 item 2 |
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

---

## 5. API reference

Base path: whatever `testseries.urls` is mounted at (example above:
`/api/`). All endpoints require `IsAuthenticated`.

### 5.1 `TestSeries` — `/testseries/`
| Method | Path | Who | Notes |
|---|---|---|---|
| `GET` | `/testseries/` | any auth user | `?source=individual\|campus\|liveclass` filter. Returns published series + your own drafts. |
| `GET` | `/testseries/{id}/` | any auth user | |
| `POST` | `/testseries/` | any auth user | **Always creates `source="individual"`** — campus/liveclass series never go through this, see `bridge.py` §6 below. Body: `title`, `description`, `is_paid`, `price_coins`, `duration_minutes`, `attempts_allowed`. |
| `PUT`/`PATCH` | `/testseries/{id}/` | creator only | |
| `DELETE` | `/testseries/{id}/` | creator only | |
| `POST` | `/testseries/{id}/publish/` | creator only | 400 if not `draft` or has zero questions. Recomputes `total_marks`, sets `status="published"`. |

### 5.2 `Question` — `/testseries/{series_id}/questions/`
| Method | Path | Who | Notes |
|---|---|---|---|
| `GET` | `.../questions/` | any auth user | `correct_answer` hidden unless you're the series creator |
| `GET` | `.../questions/{id}/` | any auth user | same hiding rule |
| `POST` | `.../questions/` | series creator, **draft only** | body per §4.3 shape table |
| `PUT`/`PATCH` | `.../questions/{id}/` | series creator, **draft only** | |
| `DELETE` | `.../questions/{id}/` | series creator, **draft only** | |

Every write recomputes `series.total_marks`.

### 5.3 `TestAttempt` — `/attempts/`
| Method | Path | Who | Notes |
|---|---|---|---|
| `GET` | `/attempts/` | student sees own; creator sees attempts on own series | |
| `GET` | `/attempts/{id}/` | same | |
| `POST` | `/attempts/start/{series_id}/` | any auth user | Idempotent — existing attempt (incl. paid retry) returned as-is, never double-charged. Body: `roll_number`, `enrollment_no` (campus/liveclass only, coerced to string and clipped to 30 chars). **402** if insufficient coins for a paid series. A concurrent double-`start()` (same series+student, two in-flight requests) is caught as an `IntegrityError` against `unique_attempt_per_student_per_series` — the losing request gets the winner's attempt back instead of a 500; a losing paid attempt's coin debit is rolled back automatically (`purchase_and_start_attempt` is `@transaction.atomic`). |
| `POST` | `/attempts/{id}/submit/` | attempt's own student only | Body: `{"answers": {question_id: answer_data, ...}}` as JSON, **or** `multipart/form-data` with `answers` as a JSON-encoded string field plus one file per attached answer under `answer_<question_id>` (image/file answers, `text` questions only). 400 if not `in_progress`, or if an uploaded file fails the extension/size check (§12). |
| `POST` | `/attempts/{id}/answer/{question_id}/review/` | series creator, or campus reviewer per `user_can_review_attempt()` | Body: `{"marks_awarded": N, "feedback": "..."}`. 400 if that response isn't `text`-type (auto-graded already resolved), if `marks_awarded` is missing/non-integer/negative, or exceeds the question's marks. |

### 5.4 Example payloads

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
`answers` is JSON-encoded into one string field (multipart can't carry
nested JSON); the file field name is `answer_` + the question's UUID.
Only meaningful for a `text`-type question — a file sent for an
auto-graded question is simply ignored (see `TestAttempt.submit()`).

**Review a text answer:**
```json
POST /attempts/{id}/answer/{question_id}/review/
{ "marks_awarded": 3, "feedback": "Good structure, missing one example." }
```

---

## 6. Serializers — visibility rules

- `QuestionSerializer` — `correct_answer` stripped from the response
  unless `request.user` is `series.creator`. `validate()` mirrors
  `Question.clean()` so a bad shape returns a clean DRF 400.
- `QuestionResponseSerializer` — a student's own still-ungraded `text`
  response has `reviewer_feedback` blanked out until it's actually
  reviewed (nothing to leak for auto-graded ones — those resolve
  instantly at submit-time).
- `TestSeriesSerializer.validate()` — enforces `price_coins=0` when
  `is_paid=False`, and rejects `is_paid=True` for `source="campus"` at
  the API layer too (model `save()` already normalizes this silently;
  the serializer turns it into an honest 400 instead).
- `TestSeriesSerializer` — `source`, `context_type`, `context_id`, and
  `status` are `read_only_fields`, not just `is_paid`-adjacent
  validation. These are provenance/workflow fields set once at creation
  (or via `publish()`, for `status`) — none of the four are ever
  client-writable through `PATCH`/`PUT` on `/testseries/{id}/`, so a
  creator can't rewrite their own individual series into impersonating
  a campus/liveclass context, or force-publish a draft and skip
  `publish()`'s zero-question check + `total_marks` recompute.
- `TestAttemptSerializer` — `series`, `roll_number`, `enrollment_no`
  are also `read_only_fields` (alongside the fields already listed in
  §4.6), defence-in-depth: `TestAttemptViewSet` has no create/update
  action wired to this serializer today, but if one is ever added,
  these three should still only ever be set via `start()`'s own
  snapshot, never patched directly.

---

## 7. Permissions matrix

| Action | Who's allowed |
|---|---|
| Create individual series | any authenticated user |
| Create campus series | **not via this app's API** — only `campus`'s own staff/teacher-checked proxy endpoint, via `bridge.create_context_testseries()` |
| Create liveclass series | **not via this app's API** — only `liveclass`'s own teacher-checked proxy endpoint, via `bridge.create_context_testseries()` |
| Edit/delete a series | `series.creator` only |
| Add/edit/delete questions | `series.creator` only, and only while `series.status="draft"` |
| Start/submit an attempt | the student themself |
| Review a `text` response | `series.creator` (covers individual + liveclass) **or** campus subject-teacher, resolved via `campus.bridge.can_review_testseries_attempt()` (§3.3 — until that lands, campus reviewers get `False`) |

---

## 8. Admin

- `TestSeriesAdmin` — list/filter by `source`/`status`/`is_paid`,
  inline `Question` editing.
- `TestSeriesPurchaseAdmin` — **fully read-only** (`has_add_permission`
  → `False`, all fields readonly) — state changes only ever happen
  through `release()`/`refund()` so the CoinLedger side-effect is never
  skipped by an admin edit.
- `TestAttemptAdmin` — inline `QuestionResponse` (read-only on the
  auto-graded fields; `answer_attachment` shown so a reviewer can open a
  student's photo/file answer directly from admin).

---

## 9. Celery tasks (`tasks.py`)

| Task | Schedule | What |
|---|---|---|
| `send_pending_check_reminders` | daily | Notifies creators with `submitted`/`partially_checked` attempts older than `TESTSERIES_REMINDER_DAYS`. Uses `NotifType.GENERIC` — no dedicated reminder type exists in the confirmed enum list (§3.2), not guessed. |
| `refund_unchecked_paid_attempts` | daily | Auto-refunds any `escrowed` purchase older than `TESTSERIES_AUTO_REFUND_DAYS` whose attempt never reached `checked`. This is the exploit-guard: without it, a creator could hold a paid attempt forever without reviewing it. |

---

## 10. End-to-end flows

### 10.1 Individual, paid series
1. Creator: `POST /testseries/` (`is_paid=true`) → `draft`.
2. Creator: `POST .../questions/` × N.
3. Creator: `POST /testseries/{id}/publish/` → `published`, question set locked.
4. Student: `POST /attempts/start/{series_id}/` → `TestSeriesPurchase.
   purchase_and_start_attempt()` debits coins to escrow, creates
   `TestAttempt(in_progress)`.
5. Student: `POST /attempts/{id}/submit/` → all `mcq`/`msq`/`list`
   auto-graded; if series has any `text` question →
   `partially_checked`; else → `checked` + escrow released immediately +
   `TESTSERIES_CHECKED` sent.
6. (If `partially_checked`) Creator: `POST .../answer/{qid}/review/` per
   `text` question. Last one triggers finalize → `checked`, escrow
   `release()` → creator paid + `TESTSERIES_PAYOUT_RELEASED` sent to
   creator, `TESTSERIES_CHECKED` sent to student.
7. Safety net: if step 6 never happens within
   `TESTSERIES_AUTO_REFUND_DAYS`, `refund_unchecked_paid_attempts`
   auto-refunds the student.

### 10.2 Campus (always free)
1. Teacher/staff, via `campus`'s own proxy endpoint (already permission
   checked by `campus`) → `campus.bridge.create_testseries(...)` →
   `testseries.bridge.create_context_testseries(source="campus",
   is_paid=False, ..., roster=[...])`.
2. Series is created **already `published`** (bridge path skips the
   draft/publish two-step — questions arrive complete from the caller).
3. Roster gets `TESTSERIES_POSTED` notification, fanned out by the
   bridge function itself.
4. Students: `start` → no `TestSeriesPurchase` at all (unpaid path,
   `is_paid=False` guard in `submit()`/finalize skips escrow entirely).
5. Review: subject-teacher via `campus.bridge.
   can_review_testseries_attempt()` (§3.3, prerequisite).

### 10.3 LiveClass (teacher chooses paid/unpaid)
Same as campus flow, except `liveclass.bridge.create_testseries(...)`
passes teacher-chosen `is_paid`/`price_coins` through unmodified — no
server-side force (that force only applies to `source="campus"`). If
`is_paid=True`, the paid flow (§10.1 steps 4–7) applies; reviewer is
always `series.creator` (the teacher) since liveclass doesn't have a
separate "subject-teacher" concept campus does.

---

## 11. Production-hardening pass (fixed post-v1)

Five real bugs found and fixed in a follow-up audit of the as-built
code above. All five are already reflected in the sections above and
in the code itself — this section is just the changelog.

1. **`TestSeriesSerializer` write-access gap (the important one).**
   `source`, `context_type`, `context_id`, and `status` were writable
   via `PATCH`/`PUT /testseries/{id}/` by any series creator. A creator
   could rewrite their own `individual` series to `source="campus"`
   (with a made-up `context_id`), or `PATCH {"status": "published"}`
   directly and skip `publish()`'s "must have ≥1 question" check and
   `total_marks` recompute entirely. Fixed: all four are now
   `read_only_fields` (§6).
2. **`Question.save()`'s `full_clean(exclude=...)` was inverted.** It
   excluded every field *except* `options`/`correct_answer` from
   validation — meaning required-field checks (`text`, `marks`,
   `question_type`, `order`) and the `(series, order)` uniqueness
   constraint were silently skipped, surfacing as a raw `IntegrityError`
   instead of a clean `ValidationError` when violated. Fixed: exclude
   list is now just `["options", "correct_answer"]` (§4.3).
3. **Negative `marks_awarded` wasn't rejected.** Both
   `views.py::review_answer` and `QuestionResponse.mark_answer()`
   checked the upper bound (`> question.marks`) but not the lower one —
   a negative value would have hit `PositiveIntegerField`'s DB-level
   `CHECK` constraint and 500'd instead of a clean 400. Fixed at both
   layers (§4.4, §5.3).
4. **`TestAttemptViewSet.start()` had dead code + a real race gap.**
   The old paid-branch's separate `TestSeriesPurchase` lookup
   (`if purchase and purchase.attempt_id: return ...`) was unreachable —
   the top-of-method `existing` lookup (by `series`+`student`) already
   covers idempotency for both paid and unpaid attempts, since
   `unique_attempt_per_student_per_series` guarantees at most one
   `TestAttempt` per pair. Removed the dead branch, and added real
   `IntegrityError` handling for the genuine race case (two concurrent
   `start()` calls for the same series+student): the losing request now
   gets the winner's attempt back instead of a 500 (§5.3).
5. **`start()`'s `roll_number`/`enrollment_no` weren't type/length
   guarded.** Read straight off `request.data` and assigned to a
   `CharField(max_length=30)` — an unexpected type (list, int) or an
   over-length string from a misbehaving client would 500 at the model
   layer. Fixed: coerced to `str(...)[:30]` before use (§5.3).

---

## 12. Answer & question attachments (image/file support)

Two separate attachment points exist, both `FileField`, both validated
by the same shared rules:

| Field | Purpose | Set by |
|---|---|---|
| `Question.attachment` | The question's own image/PDF (e.g. a diagram a question refers to) | series creator, via `QuestionSerializer` (`POST`/`PATCH .../questions/`) |
| `QuestionResponse.answer_attachment` | A student's photo/file answer (e.g. a photo of handwritten work) | student, via `TestAttempt.submit()` — **not** exposed as a writable serializer field, only ever set from `request.FILES` inside the `submit` action |

**Validation (defence-in-depth, three layers):**
1. `FileField(validators=[attachment_extension_validator, validate_attachment_size])`
   on both model fields (`models.py`) — enforced whenever `full_clean()`
   runs (`Question.save()`).
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
attachment) or `multipart/form-data` (to attach a file): see §5.4 for
both example payloads. The file-field naming convention is
`answer_<question_id>` (the question's UUID); a file sent against an
auto-graded (`mcq`/`msq`/`list`) question is silently ignored —
attachments only ever apply to `text`-type answers.

**Not handled by this app:** actual file storage backend (local disk
vs. S3/GCS/etc.) is whatever the project's `DEFAULT_FILE_STORAGE` /
`STORAGES` setting already points at — same as `Question.attachment`
already relied on before this pass. Nothing `testseries`-specific to
configure there.

---

## 13. Open items (carried over from the original design doc, still valid)

1. **Platform fee / cut** — 100% of `price_coins` goes to the creator,
   no commission. Separate pricing decision, out of scope here.
2. **`attempts_allowed > 1`** — `attempt_number` field exists as
   groundwork, but the real `(series, student, attempt_number)`
   uniqueness shape + serializer count-check is **not implemented** —
   MVP (`attempts_allowed=1`) is complete via the current unique
   constraint. Multi-attempt is a follow-up.
3. **`msq`/`list` partial credit is a non-goal** in this version —
   exact-match, all-or-nothing only. A separate explicit decision needed
   if partial credit is ever wanted.
4. **Unpaid attempts never create a `TestSeriesPurchase` row** — the
   `if series.is_paid and purchase` guard in both `submit()` and
   `mark_answer_and_maybe_finalize()` is intentional, not an oversight.
5. **`QuestionResponse.reviewed_by`/`reviewed_at`** only meaningfully set
   for `text` responses — UI should distinguish "auto-graded" vs
   "reviewed by X" badges, or it'll look like MCQs are mysteriously
   never "reviewed."

---

## 14. Pre-flight checklist (before first real deploy)

- [ ] §3.1 — `CoinLedger.TransactionType` enum landed, `_TransactionTypeGap` swapped out
- [ ] §3.2 — `Notification.NotifType` enum landed, `_NotifTypeGap` swapped out
- [ ] §3.3 — `campus.bridge.can_review_testseries_attempt()` implemented
- [ ] §3.4 — `campus.bridge.create_testseries()` + campus proxy endpoint implemented
- [ ] §3.5 — `liveclass.bridge.create_testseries()` implemented
- [ ] `testseries/migrations/` generated and applied
- [ ] `testseries.urls` mounted in project `urls.py`
- [ ] Celery beat schedule entries added (§2)
- [ ] `TESTSERIES_AUTO_REFUND_DAYS`/`TESTSERIES_REMINDER_DAYS` reviewed (defaults: 14 / 3)
- [ ] `TESTSERIES_ATTACHMENT_EXTENSIONS`/`TESTSERIES_ATTACHMENT_MAX_MB` reviewed (defaults: pdf/jpg/jpeg/png/webp, 10MB) — and confirm the project's file storage backend (local/S3/etc.) is configured, since `testseries` doesn't set one itself (§12)