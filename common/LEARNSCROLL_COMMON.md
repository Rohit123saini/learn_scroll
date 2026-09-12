# LearnScroll — `common/` Shared Utilities: Reference

> **Purpose of this file:** single source of truth for the `common` folder —
> **not a Django app of its own** (no `models.py`/`admin.py`/`apps.py`, not in
> `INSTALLED_APPS`), just a place for small, import-free, cross-app utility
> modules that more than one real app (`testseries`, `assignment`, and
> potentially others — `liveclass`, `campus`) needs and shouldn't each
> redefine independently. Share only this file (plus the relevant app's own
> doc, e.g. `LEARNSCROLL_LIVECLASS.md`) in a future chat when touching
> anything that imports from `common.*`.

---

## 0. What `common/` is (and isn't)

- **Is**: a grab-bag of small, dependency-light helper modules — pure
  functions and validator objects that any app can import without pulling
  in that app's models, views, or other app-specific baggage.
- **Isn't**: a Django app. No `AppConfig`, no `INSTALLED_APPS` entry needed
  (unlike `liveclass` — see that app's `apps.py`/§13 gotcha about
  `signals.py` needing `AppConfig.ready()`). `common/` has no signals, no
  models, nothing that needs Django's app registry to wire up — it's just
  importable Python.
- **Origin pattern to remember**: everything seen in `common/` so far was
  **moved out of `testseries/models.py`**, where it started life as
  app-specific code (a model method, inline validators) before a second app
  (`assignment`) needed the exact same logic and the choice was "duplicate
  it" vs. "extract it to a shared, import-free module". Both files below
  followed the second path. **When a new file shows up in `common/`, expect
  the same story**: check its docstring for "originally lived in
  `<app>/models.py`" — that's the pattern, not the exception, and it tells
  you which app's behavior must stay byte-for-byte unchanged after the move.

---

## 1. File map

| File | Moved from | Used by (confirmed) | Role |
|---|---|---|---|
| `attachment_validators.py` | `testseries/models.py` (validated `Question.attachment` / `QuestionResponse.answer_attachment`) | `testseries` (original caller, unchanged), `assignment` (reuses as-is, not redefined) | File-upload validation: extension safelist + max-size check, for any app's `FileField` that accepts a user-uploaded document/image. |
| `question_grading.py` | `testseries/models.py` (`Question.auto_grade()`, now a thin wrapper around this) | `testseries` (via its `Question.auto_grade()` wrapper), `assignment` (**Task 7 — wiring described but not yet confirmed done**, see §4) | Pure auto-grading logic for `mcq`/`msq`/`list`(`match`/`order`) question types; `text` always routes to manual review. |

**Both files share the same design contract**: plain functions/values only,
**zero Django-model imports**, so a file in `common/` never secretly depends
on one app's schema. `question_grading.py`'s docstring states this
explicitly (deliberately takes `question_type`/`options`/`correct_answer`
as plain strings/list/dict rather than a `Question` instance) — treat that
as the standing rule for anything added to `common/` in future, not a
one-off design note for this one file.

---

## 2. `attachment_validators.py`

```python
ATTACHMENT_ALLOWED_EXTENSIONS = settings.TESTSERIES_ATTACHMENT_EXTENSIONS  # default: ["pdf","jpg","jpeg","png","webp"]
ATTACHMENT_MAX_SIZE_MB          = settings.TESTSERIES_ATTACHMENT_MAX_MB     # default: 10

attachment_extension_validator  = FileExtensionValidator(allowed_extensions=ATTACHMENT_ALLOWED_EXTENSIONS)
validate_attachment_size(file)  # raises django.core.exceptions.ValidationError if file.size > max_bytes
```

- **Settings keys are still named `TESTSERIES_*`**, even though `assignment`
  (and potentially other apps) now reuse this module — the move to
  `common/` was explicitly a **pure move, behavior unchanged**: "same
  settings keys, same defaults, same error message". This means every app
  reusing this module shares **one global** extensions/size policy, keyed
  under `testseries`'s original settings names. There is currently no
  per-app override (e.g. no `ASSIGNMENT_ATTACHMENT_MAX_MB`) — if
  `assignment` (or anything else) ever needs a *different* limit than
  `testseries`, that's a real feature gap, not a bug, and should be raised
  explicitly rather than assumed to already work.
- **Safelist, not blocklist** — `FileExtensionValidator(allowed_extensions=...)`
  only accepts what's on the list (`pdf`/`jpg`/`jpeg`/`png`/`webp` by
  default). This is the same safelist-over-blocklist posture
  `liveclass/models.py`'s own `DOCUMENT_MEDIA_EXTENSIONS` safelist takes for
  its own plain `FileField`s (material/assignment attachment/assignment
  submission/certificate — see `LEARNSCROLL_LIVECLASS.md` §3) — **but it is
  a separate, independent safelist**, not the same one. Don't assume
  `liveclass`'s `MaxFileSizeValidator`/`DOCUMENT_MEDIA_EXTENSIONS` and this
  module's `attachment_extension_validator`/`validate_attachment_size` are
  interchangeable or share config — they're two different validators with
  two different settings keys, maintained in two different places, that
  happen to solve the same category of problem for two different apps.
- **Three enforcement layers, all mentioned explicitly in the module
  docstring — a caller should pick the ones that apply to its upload path,
  not assume one covers all cases**:
  1. `FileField(validators=[attachment_extension_validator, ...])` —
     enforced by Django's `full_clean()`. Only fires if something actually
     calls `full_clean()`/`.full_clean()` on the model instance — a bare
     `.save()` does **not** run validators automatically (standard Django
     behavior, not specific to this module) — see the gotcha in §5.
  2. A serializer's `validate_attachment()` hook — for anything going
     through DRF, giving a clean 400 instead of a raw `IntegrityError`/
     `ValidationError` leaking out.
  3. A view-level check on an upload read straight off `request.FILES`
     (never touches a serializer or a model field directly) — same clean-400
     treatment, checked **before** any DB write happens.
- `validate_attachment_size(file)` reads `file.size` — works for anything
  exposing that attribute (`UploadedFile`, `InMemoryUploadedFile`,
  `TemporaryUploadedFile`, or a saved `FieldFile`), so it's usable both at
  upload-validation time and (in principle) against an already-saved file.

---

## 3. `question_grading.py`

```python
auto_grade(*, question_type: str, options, correct_answer: dict, answer_data: dict, marks: int) -> tuple[bool | None, int | None]
```

Returns `(is_correct, marks_awarded)`.

| `question_type` | Grading rule | Result if correct | Result if wrong |
|---|---|---|---|
| `"text"` | **Never auto-graded** — always manual review | `(None, None)` | `(None, None)` |
| `"mcq"` | `answer_data["option_id"] == correct_answer["option_id"]` | `(True, marks)` | `(False, 0)` |
| `"msq"` | `set(answer_data["option_ids"]) == set(correct_answer["option_ids"])` (order-independent, dedupes) | `(True, marks)` | `(False, 0)` |
| `"list"`, `correct_answer["list_mode"] == "match"` | `answer_data["pairs"] == correct_answer["pairs"]` (exact equality — **order-sensitive**, unlike `msq`) | `(True, marks)` | `(False, 0)` |
| `"list"`, any other `list_mode` (treated as `"order"`) | `answer_data["sequence"] == correct_answer["sequence"]` (exact equality, order-sensitive) | `(True, marks)` | `(False, 0)` |
| anything else | `raise ValueError(f"Unknown question_type: {question_type!r}")` | — | — |

- **Deliberately import-free of Django and of any app's models** — takes
  `question_type` as the *raw DB string* (`"text"`/`"mcq"`/`"msq"`/`"list"`),
  not `Question.QuestionType` the enum, and `options`/`correct_answer`/
  `answer_data` as plain `list`/`dict`. This is what keeps `common/`
  genuinely shared rather than secretly importing `testseries.models` —
  **a caller is responsible for unwrapping its own model instance into
  these plain values before calling `auto_grade()`**, this function will
  never do that unwrapping for you.
- `options` **is accepted as a parameter but never actually read** inside
  the function body shown — every branch only touches `correct_answer`/
  `answer_data`/`marks`. Worth confirming with whoever maintains this file
  whether `options` is dead weight (kept only so the call signature can
  eventually validate `answer_data` against the legal option set, e.g.
  reject an `option_id` that isn't even in `options`) or a genuine
  leftover — as documented, it currently does nothing.
- **`marks` is the question's own full-marks value, supplied by the
  caller** — this function does not look it up. The module docstring
  explicitly calls this out for whoever wires `assignment` in: the original
  `Question.auto_grade(self, answer_data)` read `self.marks` off the model
  instance itself; this standalone version has no instance to read from, so
  `testseries`'s own wrapper (`Question.auto_grade()` in
  `testseries/models.py` — not part of this upload, referenced only) is
  presumably now a thin shim that calls `auto_grade(..., marks=self.marks)`.
  **`assignment` must do the exact same** — pass its own question's marks
  value explicitly at the call site. This is flagged as a to-do
  ("NOTE for whoever wires this into `assignment` (Task 7)"), **not
  confirmed done** — see §4.
- **`list`/`match` vs `list`/`order` both use exact equality, no partial
  credit** — a `match` answer with 3 of 4 pairs correct, or an `order`
  answer with 2 of 5 items swapped, scores `(False, 0)` just like a
  completely wrong answer. If partial credit is ever wanted for `list`
  questions, it doesn't exist here and would need new logic, not a
  parameter this function already supports.
- **No bounds/shape validation on `answer_data`** — e.g. `mcq` does
  `answer_data.get("option_id")`, so a malformed payload missing
  `"option_id"` compares `None == correct_answer.get("option_id")` and
  quietly grades as wrong rather than raising. Only a genuinely unknown
  `question_type` string raises. Callers that want to distinguish
  "wrong answer" from "malformed answer" need their own check before
  calling this.

---

## 4. Integration status — what's confirmed vs. still a to-do

- **`testseries`**: both modules originated here and are the
  fully-confirmed, original callers — `attachment_validators.py` backs
  `Question.attachment`/`QuestionResponse.answer_attachment`;
  `question_grading.py` backs `Question.auto_grade()`. Neither module's
  *behavior* changed in the move to `common/` — same settings keys, same
  defaults, same error message, same grading rules.
- **`assignment`**: confirmed to **reuse** `attachment_validators.py`
  as-is (per that module's own docstring: "`assignment` reuses the exact
  same rules instead of redefining them"). `question_grading.py`'s wiring
  into `assignment` is **Task 7**, described in the module's own docstring
  as a note *for whoever does that wiring* — i.e. **written as guidance for
  a not-yet-done integration, not confirmation that it's done**. Before
  relying on `assignment` auto-grading anything, confirm `assignment`'s own
  code (`assignment/models.py`/`assignment/bridge.py` — see
  `LEARNSCROLL_LIVECLASS.md` §6d for what's been read of that app so far)
  actually calls `common.question_grading.auto_grade(..., marks=<the
  question's own marks>)` the way the docstring instructs, rather than
  assuming the note alone means it's wired.
- **`liveclass`**: no evidence in anything audited so far
  (`LEARNSCROLL_LIVECLASS.md`) that `liveclass` imports from `common/` —
  its own file-upload validation is a separate, independently-maintained
  safelist (`DOCUMENT_MEDIA_EXTENSIONS` + `MaxFileSizeValidator`, see §2
  above and that doc's §3). `liveclass` also has no auto-graded
  MCQ/MSQ-style question model — its `Assignment`/`AssignmentSubmission`
  (legacy) and the unified `assignment` app's submissions are file-upload +
  manual-score based, not option-based, so `question_grading.py` has no
  obvious call site there. Treat `liveclass` as **not currently a consumer**
  of either `common/` module unless/until a future upload shows otherwise.

---

## 5. Gotchas — worth remembering if this file grows

1. **Model-level `FileField` validators only fire on `full_clean()`, not on
   a bare `.save()`.** This is ordinary Django behavior, not a bug in
   `attachment_validators.py`, but it's an easy trap for a new call site: a
   view or management command that does `instance.save()` directly (never
   going through a `ModelForm`/DRF serializer, which call `full_clean()`
   for you) silently skips `attachment_extension_validator` entirely. Any
   new caller writing straight to a model with one of these validators
   needs one of the other two enforcement layers (§2) — a serializer
   `validate_*` hook or an explicit view-level check before save — not just
   the bare model field.
2. **`TESTSERIES_ATTACHMENT_EXTENSIONS`/`TESTSERIES_ATTACHMENT_MAX_MB` are
   global, not per-app**, despite the settings names now governing more
   than just `testseries`. A future app importing this module inherits
   whatever `testseries` has configured — if that's ever wrong for a new
   consumer, the fix is a real per-app settings key (a small feature), not
   a one-line tweak.
3. **`common/` having no `AppConfig`/`INSTALLED_APPS` entry means there's
   nothing here that "loads" at Django startup** — no signals, no app-ready
   hook, unlike `liveclass.apps.LiveclassConfig` (see
   `LEARNSCROLL_LIVECLASS.md` §13/§17 item 1 for what happens when an app
   *does* need that wiring and it's missing). Nothing in `common/` needs
   the equivalent — don't add one unless a future file in this folder
   genuinely needs Django's app registry (at which point it likely
   shouldn't be in `common/` at all, but its own small app).
4. **`question_grading.auto_grade()`'s `options` parameter is currently
   unused inside the function** (§3) — don't assume passing a malformed or
   empty `options` list has any effect today; it doesn't validate anything
   yet.
5. **Exact-equality grading for `list` questions (both `match` and
   `order`) means the caller's `answer_data`/`correct_answer` shapes must
   match byte-for-byte** (same dict key order doesn't matter for `pairs`
   since it's `==` on the value, but the *values themselves* — e.g. a list
   of `[a, b, c]` vs `[a, c, b]` for `order` — must be truly identical to
   grade correct). Any client-side answer serialization that reorders or
   reformats before sending would silently fail otherwise-correct answers.

---

## 6. Checklist for future work touching `common/`

- **Adding a new shared utility** → follow the established pattern: plain
  functions/values, no Django-model imports, docstring stating which app it
  was moved from (if any) and which apps currently call it. Keep behavior
  identical to the original if this is a move, not a rewrite — call that
  out explicitly in the docstring the way both existing files do.
- **Changing `attachment_validators.py`'s defaults or settings keys** →
  remember every consumer (`testseries` confirmed, `assignment` confirmed)
  shares the same global settings keys — a change here is a change for
  every app importing it, not just the one you're currently working on.
- **Wiring `question_grading.auto_grade()` into a new app** → pass the
  question's own `marks` explicitly (this function never looks it up
  itself), pass `question_type` as the raw DB string not an enum member,
  and confirm your `answer_data`/`correct_answer` shapes line up
  key-for-key with the table in §3 before assuming grading will work.
- **Confirming Task 7 (`assignment` × `question_grading.py`)** → check
  `assignment/models.py`/`assignment/bridge.py` directly for an actual call
  to `common.question_grading.auto_grade(...)` before documenting it as
  done anywhere else — as of this file, it's a documented *intention*, not
  a confirmed integration (§4).
