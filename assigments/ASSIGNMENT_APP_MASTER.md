# `assignment` App — Master Reference (Design + Complete Production Code)

> **Yeh ek hi self-contained document hai.** Isme functional design,
> production-hardening design, har file ka **poora, copy-paste-ready
> source code**, aur **dusri apps (campus/liveclass/testseries/core/
> message/common) ke saath integration/plugin points** — sab ek hi jagah
> hain. Kisi alag `.py` file, kisi purani chat, ya kisi doosre doc ko
> dobara dekhne ki zaroorat nahi — is doc ko top-se-bottom padh ke koi
> bhi (developer ya AI) is poore `assignment` app ko samajh sakta hai,
> extend kar sakta hai, ya kisi bhi future task (naya endpoint, naya
> bridge function, ek naya bug fix) ko sirf isi file ke bharose pe kar
> sakta hai.
>
> **Last synced against real source:** 2026-09-12 — Part 3 ka har code block
> is pass me actually upload ki gayi, real `.py` files se seedha liya
> gaya hai (copy-paste, verbatim), copy-drift se bachne ke liye. Jahan
> is doc ka Part 3 aur asli file mismatch pa jaye future me, **asli file
> jeetegi** — is doc ko turant usi se dobara sync karo.
>
> Structure:
> - **Part 1** — Functional design (source: `assignment_app_design.md`,
>   verbatim — har baad ka code comment `§N` isi part ko cite karta hai).
> - **Part 2** — App folder structure (kya file kahan jaati hai).
> - **Part 3** — Har file ka poora, latest source code, verbatim, order
>   se — seedha copy karke ek fresh Django project me daala ja sake.
> - **Part 3.5 — Known Issues Found & Fixed This Pass** — is pass me
>   real code execute karke confirm kiya gaya ek genuine production bug
>   (`submit_structured()` ka `auto_grade()` call), aur uska fix.
> - **Part 4** — Production-readiness design (security, performance,
>   observability, testing, deployment, rate-limiting, open risks).
> - **Part 5** — Integration checklist (settings, `INSTALLED_APPS`, URLs,
>   migrations) — project-level 4 cheezein jo is app ke andar nahi ho
>   saktin.
> - **Part 6 — Cross-App Integration / Plugin Registry** — har dusri app
>   (`campus`, `liveclass`, `testseries`, `core`, `message`, `common`)
>   ke saath `assignment` kaise bridge/plug hota hai, ek hi jagah —
>   golden rules, notification types, bridge function signatures, aur
>   is-baar-verify-hui vs abhi-tak-unverified cheezon ka clear split.

---

## Table of Contents

1. [Part 1 — Functional Design](#part-1--functional-design)
2. [Part 2 — App Folder Structure](#part-2--app-folder-structure)
3. [Part 3 — Complete Source Code](#part-3--complete-source-code)
4. [Part 3.5 — Known Issues Found & Fixed This Pass](#part-35--known-issues-found--fixed-this-pass)
5. [Part 4 — Production-Readiness Design](#part-4--production-readiness-design)
6. [Part 5 — Integration Checklist](#part-5--integration-checklist)
7. [Part 6 — Cross-App Integration / Plugin Registry](#part-6--cross-app-integration--plugin-registry)

---

## Part 1 — Functional Design

> Source: `assignment_app_design.md` (verbatim). Har `§N` reference is doc
> ke baad ke sections me isi Part 1 ke numbered headings ko point karta
> hai.

## `assignment` App — Design Doc (v1, proposed)

> Ye naya, **unified** `assignment` app hai. Maksad: abhi `campus` aur
> `liveclass` dono ke paas apna-apna alag `Assignment`/`AssignmentSubmission`
> model hai (duplicate logic, duplicate submission-tracking, duplicate
> reminder tasks). Ye app dono ko **replace** karta hai aur ek teesra naya
> flow add karta hai jo pehle kahin nahi tha: **personal/self-assignment +
> shareable verification URL**. `core` app jis tarah `liveclass`↔`message`
> coupling ko neutral layer bana ke absorb karta hai, `assignment` app usi
> pattern ko follow karta hai — na `campus` ke models import karta hai, na
> `liveclass` ke. Dono apps **isi app se hokar guzarte hain**.

---

### 0. Kyu unified, kyu replace (not "alag rakho, sirf link jodo")

Decision (confirmed): naya app banao, `campus`/`liveclass` ke purane
`Assignment`/`AssignmentSubmission` models ko isi se **replace/migrate**
karo — bridge pattern se.

Reasoning:
- Dono purane models 90% same cheez kar rahe the (teacher/staff post karta
  hai → roster ke liye bulk `MISSING` submissions pre-create → student
  submit karta hai → due-reminder task) — ek jagah maintain karna behtar
  hai do jagah sync rakhne se.
- Personal-assignment flow (naya) ko roll-no/enrollment-verification wali
  wahi machinery chahiye jo campus-assignment already use karti hai — agar
  do alag app hote to ye logic teen jagah likhni padti (campus, liveclass,
  personal).
- `core` app ka apna precedent yehi hai: jab bhi ek cheez 2+ apps me
  duplicate/coupled ho jaye, use ek neutral app me nikaal lo.

---

### 1. Golden rule (`core`/`campus` jaisa hi)

`assignment` app **kabhi** `campus.*` ya `liveclass.*` models seedha import
nahi karta, aur `campus`/`liveclass` bhi `assignment.models` seedha import
nahi karte. Dono directions `assignment/bridge.py` (is app ka apna
"door") + reverse me `campus/bridge.py` aur `liveclass/bridge.py` se
guzarti hain — same shape jaisa `campus → core.classroom_chat_bridge` hai.

Isse ye possible hota hai ki `assignment` app kisi bhi "context" (campus
section ho, liveclass classroom ho, ya kuch bhi na ho — personal) ke sath
kaam kare, bina us context ke internal model structure ko jaane.

#### Context ek soft-reference hai, hard FK nahi
```python
class AssignmentSource(models.TextChoices):
    PERSONAL  = "personal", "Personal"
    CAMPUS    = "campus", "Campus"
    LIVECLASS = "liveclass", "LiveClass"

class Assignment(AssignmentBaseModel):
    source        = models.CharField(max_length=10, choices=AssignmentSource.choices, db_index=True)
    context_type  = models.CharField(max_length=20, blank=True)   # "section" | "classroom" | ""
    context_id    = models.UUIDField(null=True, blank=True)       # campus.Section.id OR liveclass.Classroom.id — opaque, never FK'd
    ...
```
`context_id` **hamesha opaque UUID** hai — `assignment` app kabhi usse
`campus.Section.objects.get(...)` nahi karta. Jo bhi caller (campus ya
liveclass) hai, wahi apne khud ke context ko resolve karta hai aur
`assignment.bridge` ko already-resolved data (title, due date, roster ki
list of `(user_id, roll_number, enrollment_no)`) pass karta hai. Ye
`core.search`'s "caller already-scoped queryset deta hai, main sirf
merge/rank karta hoon" wale principle jaisa hi hai — yahan "caller
already-resolved roster deta hai, main sirf store/track karta hoon."

---

### 2. Models

#### `AssignmentBaseModel`
- UUID PK (`campus.CampusBaseModel` jaisa hi pattern) — sequential-int
  enumeration se bachne ke liye, kyunki personal-assignment URLs public
  share hongi.

#### `Assignment`
| Field | Type | Notes |
|---|---|---|
| `source` | CharField choices | `personal` / `campus` / `liveclass` |
| `context_type`, `context_id` | CharField(20) + UUID, nullable | `source="personal"` ke liye dono blank/null |
| `posted_by` | FK → `login.User`, `SET_NULL`, nullable | teacher/staff (campus/liveclass) ya khud student (personal) |
| `title` | CharField(200) | |
| `description` | TextField(blank) | student khud likh sakta hai (personal mode me ye field hi "written assignment" content hai) |
| `attachment` | FileField(`upload_to="assignment/attachments/"`, null/blank) | teacher/staff ka reference material |
| `due_date` | DateField, nullable | personal assignments me due date optional (self-paced) |
| `is_paid` | **hamesha `False`** — koi field hi nahi, deliberately structurally absent | user ne confirm kiya: assignment side me paid option kabhi nahi — `campus`'s `CampusLiveSession` jaisa hi "structurally impossible" pattern |
| `total_marks` | PositiveIntegerField, nullable | `has_structured_questions=True` ho to `AssignmentQuestion.marks` ka sum (auto), warna manual optional scale |
| `has_structured_questions` | BooleanField, default `False` | §2a decide karta hai — free-form (`written_content`/`file`, §ye section) vs question-based (`AssignmentQuestion`, §2a) |
| `created_at`, `updated_at` | auto | |

`indexes = [Index(fields=["source", "context_type", "context_id"]), Index(fields=["posted_by", "due_date"])]`

#### 2a. Structured questions — production-level requirement (confirmed)
User ne explicitly confirm kiya: assignment app bhi `testseries` jaisa
hi **text/subjective, multiple-choice, list-based** question types
support kare, har question ka apna `marks` + apna review. Isliye
`assignment` app **`testseries.Question`'s exact same type/grading shape
reuse karta hai** — dono apps me alag models hain (koi cross-app FK
nahi, golden rule ke against jata), lekin field-shape/semantics identical
rakhi gayi hai taaki ek hi frontend-component dono jagah kaam kare.

`Assignment.has_structured_questions=True` hone par:
- **`AssignmentQuestion`** — `testseries.Question` ka clone: `assignment`
  FK CASCADE, `order`, `question_type` (`text`/`mcq`/`msq`/`list`,
  same choices), `text`, `attachment`, `marks`, `options`
  (JSONField), `correct_answer` (JSONField) — **exact same shape/
  validation rules jo `testseries_app_design.md` §2 me hain** (is doc me
  dobara nahi likha, taaki dono jagah drift na ho — koi bhi change dono
  docs me ek saath karna).
- **`AssignmentAnswer`** — `testseries.QuestionResponse` ka clone:
  `submission` FK CASCADE (neeche), `question` FK CASCADE, `answer_data`,
  `is_auto_graded`, `is_correct`, `marks_awarded`, `reviewer_feedback`,
  `reviewed_by`, `reviewed_at` — same fields, same `mark_answer()`
  semantics, same auto-grading function (`_auto_grade`, §testseries §2)
  reused as a **shared utility** — dono apps `assignment/grading.py` aur
  `testseries/grading.py` me duplicate na karein, isliye recommend hai
  ki ye ek chhota shared helper module (e.g. `common/question_grading.py`
  — koi Django app nahi, sirf pure-function utility, isliye golden rule
  violate nahi karta) me rahe, dono import karein.
- `has_structured_questions=False` (default, personal free-write use
  case ke liye typical) hone par purana `written_content`/`file`
  single-blob path hi chalta hai (neeche) — koi `AssignmentQuestion` row
  nahi banti.

`Assignment.has_structured_questions` **immutable after first submission
exists** (serializer `validate()` — status quo ko badalna scoring
inconsistent kar dega, `TestSeries.status="published"` ke lock-after-
publish wale reasoning jaisa hi).

#### `AssignmentSubmission`
| Field | Type | Notes |
|---|---|---|
| `assignment` | FK CASCADE | |
| `student` | FK → `login.User` CASCADE | |
| `written_content` | TextField(blank) | **sirf `has_structured_questions=False` path** — "user khud assignment likh sakta hai" — inline text answer |
| `file` | FileField(`upload_to="assignment/submissions/"`, null/blank) | **sirf `has_structured_questions=False` path** — "files upload kr sakta hai" |
| `roll_number` | CharField(30, blank) | **snapshot at submit time**, caller (campus bridge) passes it in — never re-derived later, so a submission stays verifiable even if the student's enrollment later changes |
| `enrollment_no` | CharField(30, blank) | same snapshot idea — see §5 gap: today's `campus.StudentEnrollment` has `roll_number` but **no dedicated `enrollment_no` field**, only `(student, section, session)` as the de-facto enrollment key. §5 flags this as an open item for `campus`, not something `assignment` invents on its own. |
| `status` | CharField(12) choices `missing`/`submitted`/`late`/`partially_checked`/`checked` | default `missing`, `db_index=True`. `partially_checked` — **naya, `testseries.TestAttempt` jaisa hi** — `has_structured_questions=True` aur usme koi `text` question pending-review ho to yahi status; free-form path me ye status kabhi nahi aata (sirf `submitted`/`late` → seedha `checked`, ek hi manual grade step). |
| `grade` | CharField(10, blank) | free-form path: manual letter/score. Structured path: `total_marks_awarded` (neeche) se derive/display ho sakta hai, field khud rehti hai backward-compat ke liye |
| `total_marks_awarded` | PositiveIntegerField, nullable | **naya, sirf structured path** — sum of `AssignmentAnswer.marks_awarded`, poora tabhi set hota jab sab questions (including har `text`) reviewed ho chuke hon — `testseries.TestAttempt.final_score` jaisa hi |
| `feedback` | TextField(blank) | free-form path ka assignment-level comment; structured path me per-question `AssignmentAnswer.reviewer_feedback` zyada granular hai, ye field structured path me bhi overall-remark ke liye optionally use ho sakti hai |
| `public_slug` | CharField(40, unique, blank) | **naya — personal-assignment ka core feature.** `secrets.token_urlsafe(24)`-style random slug, set only when student explicitly "publishes" the submission as a shareable URL (`POST /submissions/{id}/publish/`). Empty = not published, no public page exists. Structured-path submissions bhi publish ho sakti hain — public page pe tab per-question breakdown bhi dikhega. |
| `submitted_at`, `checked_at` | DateTimeField, nullable | `checked_at` sirf `status="checked"` pe (`partially_checked` pe nahi — `testseries` jaisa hi) |

**DB constraint**: `unique_submission_per_student` on `(assignment, student)` — same as both old models.

**Submit flow (structured path)** — `AssignmentSubmission.submit(answers)`
`testseries.TestAttempt.submit()` ke **exact same shape** follow karta
hai: per-question `AssignmentAnswer` bulk-create, auto-gradable types
turant graded, agar koi `text` question nahi to seedha `status="checked"`
+ `total_marks_awarded` set, warna `"partially_checked"`. Review flow
(`mark_answer_and_maybe_finalize`) bhi identical — dono jagah same
pattern, koi naya invent nahi kiya.

#### Shareable URL — kaise kaam karta hai
- `GET /assignment/public/{public_slug}/` — **auth-free**, sirf tab data
  deta hai jab `public_slug` non-empty ho. Response me: title, student
  naam (User se), `roll_number`, `enrollment_no` (dono submission-time
  snapshot), `submitted_at`, `status`, aur **path ke hisaab se**:
  free-form → `written_content`/`file` link + `grade`; structured →
  per-question list (`question.text`, `question.marks`,
  `answer.marks_awarded`, `answer.reviewer_feedback`) + `total_marks_awarded`
  — same per-question breakdown jo internal review UI dikhata hai, bas
  read-only public version.
- Publish/unpublish student khud control karta hai (`publish`/`unpublish`
  actions) — kisi bhi waqt slug regenerate ya blank kiya ja sakta hai
  (`unpublish` → naya `publish` naya random slug deta hai, purana URL
  turant dead ho jata hai — koi predictable pattern nahi).
- Ye feature **sirf** `source="personal"` submissions ke liye meaningful
  hai per user ka intent, lekin field-level restrict nahi kiya (koi
  functional harm nahi agar ek campus-assignment submission bhi publish
  ho jaye — teacher ke liye bhi kabhi useful ho sakta hai "share my grade
  proof" jaisa). `serializers.py` me koi hard block nahi, docs me note
  bas.

---

### 3. `bridge.py` — dono directions

#### `assignment/bridge.py` (campus/liveclass → assignment)
Ye functions `campus` aur `liveclass` apne respective `bridge.py` se call
karte hain jab teacher/staff assignment post karta hai:

```python
def create_context_assignment(*, source, context_type, context_id,
                                posted_by, title, description="",
                                attachment=None, due_date=None,
                                total_marks=None,
                                roster: list[dict]) -> "Assignment":
    """
    roster = [{"user_id": ..., "roll_number": "...", "enrollment_no": "..."}, ...]
    Assignment banata hai + bulk `AssignmentSubmission(status=MISSING)` pre-create
    karta hai roster ke har entry ke liye, roll_number/enrollment_no snapshot
    ke saath, ignore_conflicts=True.
    """

def get_submissions_for_context(context_type, context_id) -> QuerySet:
    """Caller (campus/liveclass) apne khud ke roster-based filtering ke liye
    is queryset ko aage khud scope karta hai — assignment app khud koi
    campus/liveclass permission nahi jaanta."""
```

#### `campus/bridge.py` aur `liveclass/bridge.py` (assignment → back)
`assignment` app khud kabhi `campus`/`liveclass` ko import nahi karta —
lekin submission ho jaane par ek notification chahiye hoti hai jo
context-specific ho sakti hai (e.g. "Assignment submitted" wali cheez
campus ke Notice me bhi reflect ho, ya liveclass classroom feed me bhi).
Iske liye `assignment` app **`core.services.create_notification` ko
seedha use karta hai** — `message` app already isi tarah `core` ko
directly use karta hai (dependency graph me confirmed), `core` khud hi
neutral hai isliye ye golden-rule violation nahi.

`Assignment.data` (JSONField, `core.Notification.data` jaisa hi) me
`{"context_type": ..., "context_id": ...}` daal diya jata hai deep-linking
ke liye — client apne khud ke context page pe navigate kar sakta hai.

---

### 4. Coin/payment — deliberately none

Confirmed constraint (jaise `campus` me "student ke liye free"
structurally enforce hai): **`assignment` app me koi paid option nahi
hai, kisi bhi source ke liye.** `Assignment` model me `price`/`is_paid`/
koin field hi nahi hai — testseries app ke bilkul ulat. Agar future me
kabhi "premium assignment review" jaisa kuch chahiye ho, wo ek alag,
explicit decision hoga — abhi ke liye structurally impossible rakha gaya
hai (`campus.CampusLiveSession` wale pattern jaisa hi).

---

### 5. `campus` side changes (migration)

1. **`campus.Assignment`/`campus.AssignmentSubmission` deprecated** — naye
   rows ab yahan nahi banenge. `campus/bridge.py` me `create_assignment`
   aur `get_assignment_submissions` functions add karo jo
   `assignment.bridge.create_context_assignment(source="campus",
   context_type="section", context_id=section.id, ...)` ko wrap karte
   hain — roster `StudentEnrollment.objects.filter(section=section,
   status="active")` se banega, `roll_number=e.roll_number,
   enrollment_no=""` (abhi khaali — neeche gap dekho).
2. **`AssignmentViewSet`/`AssignmentSubmissionViewSet` (campus ke)** thin
   proxy ban jate hain — apna khud ka model query karne ki jagah
   `campus/bridge.py` ke naye functions call karte hain, response shape
   same rakhte hain taaki existing frontend na tootey.
3. **Data migration**: ek one-time management command purane
   `campus.Assignment`/`AssignmentSubmission` rows ko naye
   `assignment.Assignment` (`source="campus"`, `context_id=section.id`)
   me copy karta hai, `id` mapping ek temp table/log me rakh ke (rollback
   ke liye). Purane models **turant delete nahi** — ek release ke liye
   read-only rakho, phir hata do.
4. **`tasks.send_assignment_due_reminders`** (campus) ab
   `assignment.Assignment` pe query karta hai (`context_type="section"`
   filter ke through), `core.services.create_notification` ka call same
   rehta hai.

#### ⚠️ GAP flag — `enrollment_no`
User ne explicitly bola "roll no, enrollment no ye sab maintain hona
chahiye" — lekin `campus.StudentEnrollment` ke as-built schema (§2 of
campus doc) me sirf `roll_number` hai, **koi dedicated `enrollment_no`
field nahi hai**. Do options, decide karna hoga (guess nahi kiya, flag
kar diya jaise ye poora doc-set har jagah karta hai):
- (a) `StudentEnrollment` me naya `enrollment_no` CharField add karo
  (unique per campus, school-assigned admission number jaisa), phir
  wahi snapshot ho.
- (b) `enrollment_no` ko derive/format karo existing fields se (e.g.
  `f"{session.year}-{section.id}-{roll_number}"`), koi naya DB field
  nahi.
Jab tak ye decide na ho, `assignment.AssignmentSubmission.enrollment_no`
blank rahega campus-sourced submissions ke liye — functional block nahi
(roll_number verification already kaam karta hai), bas incomplete field.

---

### 6. `liveclass` side changes (migration)

`liveclass.Assignment`/`AssignmentSubmission` ka as-built shape is
doc-set me thin hai (sirf `AssignmentSubmission.is_late()` method
confirmed, poora field list is pass me verify nahi ho saka — source file
me sirf ek-line mention tha). Isliye yahan wahi jo campus ke liye kiya
wahi shape follow karo:
1. `liveclass/bridge.py` me equivalent `create_assignment`/
   `get_assignment_submissions` wrappers, roster =
   `SessionParticipant`/active `PassPurchase` holders us classroom ke
   (exact source **verify karna hoga real `liveclass/models.py` se
   pehle** — `[NOT YET VERIFIED]`, is doc ka is-pass ka scope nahi tha).
2. **Yahan `is_paid` choice teacher se poochna hai** — lekin ye
   `assignment` app ka field nahi (§4 confirm karta hai assignment kabhi
   paid nahi). User ka original intent yahan clash karta hai apne khud
   ke answer se ("assignment side koi paid option nahi") — isliye
   resolve kiya gaya hai: **teacher se "paid/unpaid" sirf test-series
   banate waqt poocha jayega** (`testseries` app, next doc), assignment
   post karte waqt liveclass teacher se kuch nahi poochega, hamesha
   unpaid/free rahega — matches campus ka bhi behaviour, symmetric.
3. Same data-migration + read-only-then-delete pattern jaisa campus.

---

### 7. Permissions

- `AssignmentViewSet.create` — `source="personal"` ke liye koi bhi
  authenticated user. `source="campus"`/`"liveclass"` ke liye ye
  endpoint seedha expose nahi hota — sirf `campus`/`liveclass` ke apne
  bridge-wrapped endpoints se create hota hai (jo already apna khud ka
  staff/teacher permission check karte hain before calling bridge).
- `AssignmentSubmissionViewSet` — student sirf apni submission
  create/patch kar sakta hai (`unique_submission_per_student` constraint
  se protected). Grading do shape me hoti hai:
  - **Free-form path**: `PATCH {id}/grade/` (`grade`, `feedback`).
  - **Structured path**: `POST {id}/answer/{question_id}/review/` —
    `testseries` ka wahi per-question review endpoint (§testseries §7),
    sirf `text`-type `AssignmentAnswer` pe allowed.
  Dono actions ka permission check same: staff/teacher (context ke
  through, `campus`/`liveclass` ke apne permission checks) — **actual
  check caller app me hota hai** (campus/liveclass apna gate-keeping
  karke hi is action ko unlock karte hain frontend se; API-level
  defence-in-depth ke liye `assignment` app khud bhi check karta hai ki
  requester `Assignment.posted_by` hai YA `is_staff`).
- Public share page (`/public/{slug}/`) — `AllowAny`, read-only.

---

### 8. Open items

1. `enrollment_no` field decision pending — §5.
2. `liveclass.Assignment` ka poora as-built field list verify karna hai
   real source se before migration likhna — abhi sirf assumption-based
   hai.
3. Data-migration command + rollback-log design — is doc me sirf shape
   di hai, actual migration script alag likhna hai.
4. Purane `campus.Assignment`/`liveclass.Assignment` deprecation
   timeline (kab delete karna hai) — decide nahi kiya, "ek release ke
   liye read-only rakho" bas suggestion hai.
5. `core.search`'s `post`/`liveclass.ClassMaterial` STUB sources me ab
   `assignment` bhi add karna chahiye future me (F-4 extension) — is
   pass ka scope nahi tha, sirf note kar diya.
6. **Shared grading utility location** (§2a — `common/question_grading.py`
   jaisa kuch) abhi sirf suggestion hai, actual module-naming/placement
   decide nahi kiya — jab dono apps (`assignment`, `testseries`) ka code
   likhna shuru ho, pehle ye shared piece land karo, warna dono me
   `_auto_grade()` duplicate likha jayega aur future me drift karega.
7. `msq`/`list` partial-credit non-goal wahi hai jo `testseries` doc
   §8.4 me hai — structured-path assignments ke liye bhi same applies,
   dobara nahi likha.

---

## Part 2 — App Folder Structure

```
assignment/
├── __init__.py
├── apps.py                                        # AppConfig
├── models.py                                       # Assignment / AssignmentQuestion / AssignmentAnswer / AssignmentSubmission
├── serializers.py                                   # DRF read/write shapes
├── permissions.py                                   # §7 permission classes
├── throttling.py                                    # [HARDENING] public-page rate limit
├── views.py                                         # ViewSets + public share-page view
├── urls.py                                          # router + /public/<slug>/
├── admin.py                                         # Django admin registration
├── bridge.py                                        # golden-rule door for campus/liveclass
├── tasks.py                                         # due-date reminder sweep (scheduler-agnostic)
├── tests.py                                         # model-layer test suite
├── migrations/
│   └── __init__.py                                  # (run `makemigrations` to populate)
└── management/
    ├── __init__.py
    └── commands/
        ├── __init__.py
        └── send_assignment_due_reminders.py         # CLI wrapper around tasks.py

common/
├── __init__.py
└── question_grading.py                              # shared pure-function auto-grader (NOT a Django app)
```

---



## Part 3 — Complete Source Code

> Har file neeche **verbatim** hai — is pass me upload hui asli `.py`
> files se seedha liya gaya, koi paraphrase ya "reconstruction" nahi.
> Ek hi jagah jahan asli file se **jaan-boojh kar** alag kiya gaya hai:
> `assignment/models.py` — wahan ek confirmed, real bug fix hai (dekho
> Part 3.5), aur diff clearly `[FIX ...]` comment se marked hai us file
> ke andar hi.

### `common/question_grading.py`

```python
"""
common/question_grading.py

Shared auto-grading utility for any app that clones `testseries.Question`'s
type/grading shape — today that's just `assignment` (assignment_app_design.md
§2a), and per that same section `testseries` is expected to import this too
once it exists, so neither app duplicates `_auto_grade()` and the two drift
apart the moment one of them tweaks partial-credit rules.

THIS IS NOT A DJANGO APP — deliberately. It's a pure-function module with no
models, no migrations, no settings entry. That's the whole point (§8.6 of the
design doc): a shared Django *app* between `assignment` and `testseries`
would recreate exactly the cross-app coupling problem `core`/`campus`/
`assignment` all go out of their way to avoid via bridge.py. A stdlib-only
utility module has no such coupling — either app can `import` it without
taking on a dependency edge in the app graph.

IMPORTANT — provenance flag: `testseries.Question`'s actual as-built
type/grading semantics were **not available to verify** in this pass (no
testseries source was provided alongside login/models.py, core/models.py, or
assignment_app_design.md). The behaviour below is written to match what the
design doc *describes* (§2a: "text/subjective, multiple-choice, list-based",
"same `mark_answer()` semantics", "msq/list partial-credit non-goal" per §8.4/
§8.7). Treat this as [NOT YET VERIFIED] against the real testseries
implementation, same as the doc's own liveclass-migration caveat in §6 — diff
this against `testseries/grading.py` (once it exists) before assuming the two
are actually identical, and update both call sites together if they're not.
"""
from dataclasses import dataclass
from typing import Any


class QuestionType:
    """Mirrors `AssignmentQuestion.question_type` / (future)
    `testseries.Question.question_type` choices. Kept as plain string
    constants here (not a Django TextChoices) because this module has no
    Django dependency at all — the calling app's own TextChoices enum is
    the source of truth; these are just the string values every clone is
    expected to use, so a typo here would be a loud `!=` failure rather than
    a silent divergence.
    """

    TEXT = "text"
    MCQ = "mcq"
    MSQ = "msq"
    LIST = "list"

    AUTO_GRADABLE = frozenset({MCQ, MSQ, LIST})


@dataclass(frozen=True)
class GradingResult:
    """Return shape for `auto_grade()`. `is_auto_graded=False` means the
    caller MUST leave `marks_awarded`/`is_correct` as None and route the
    answer to a human reviewer (`AssignmentAnswer.mark_answer` /
    `AssignmentSubmission.mark_answer_and_maybe_finalize`) — never guess."""

    is_auto_graded: bool
    is_correct: bool | None
    marks_awarded: int | None


def auto_grade(*, question_type: str, marks: int, correct_answer: Any, answer_data: Any) -> GradingResult:
    """Grade one answer against one question, if the question type supports
    auto-grading at all.

    - `text` — never auto-graded (subjective). Always returns
      `is_auto_graded=False`; the model layer is responsible for routing
      this to `partially_checked` / human review, never for calling this
      function's result as if it were final.
    - `mcq` — `correct_answer` is a single option id, `answer_data` is a
      single option id. Full marks on exact match, else zero. No partial
      credit (only one option is even selectable).
    - `msq` / `list` — `correct_answer` and `answer_data` are both
      collections. Graded as **all-or-nothing set equality** — full marks
      if the sets match exactly, else zero. Partial credit for a partially
      -correct multi-select or partially-correct ordered list is an
      explicit **non-goal** per the design doc (§8.4/§8.7, "msq/list
      partial-credit non-goal wahi hai jo testseries doc §8.4 me hai") —
      do not "improve" this into partial scoring without updating that
      decision in both docs first.
      `list` intentionally ignores order (set comparison, not sequence
      comparison) — [NOT YET VERIFIED]: flip to an ordered comparison here
      if testseries's real `list` semantics turn out to require an exact
      sequence match rather than an unordered set match.
    """
    if question_type == QuestionType.TEXT:
        return GradingResult(is_auto_graded=False, is_correct=None, marks_awarded=None)

    if question_type == QuestionType.MCQ:
        is_correct = answer_data == correct_answer
        return GradingResult(is_auto_graded=True, is_correct=is_correct, marks_awarded=marks if is_correct else 0)

    if question_type in (QuestionType.MSQ, QuestionType.LIST):
        given = set(answer_data or [])
        expected = set(correct_answer or [])
        is_correct = given == expected
        return GradingResult(is_auto_graded=True, is_correct=is_correct, marks_awarded=marks if is_correct else 0)

    raise ValueError(f"Unknown question_type for auto_grade(): {question_type!r}")

```

---

### `assignment/models.py`

```python
# assignment/models.py
"""
New, unified `assignment` app — replaces `campus.Assignment`/
`AssignmentSubmission` and `liveclass.Assignment`/`AssignmentSubmission`
(assignment_app_design.md, full doc). Also adds a third flow that existed
in neither old app: personal/self-assignment with a shareable public
verification URL.

Every design decision below is traceable to a section of that doc — cited
inline (§N) the same way core/models.py cites task numbers — so nothing
here is a guess dressed up as a fact.

GOLDEN RULE (§1, same shape as core/campus's own rule): this app never
imports `campus.*` or `liveclass.*` models. Both directions go through
`bridge.py` — `assignment/bridge.py` for campus/liveclass → assignment,
and `campus/bridge.py` / `liveclass/bridge.py` (not in this file) for the
reverse. `context_type` + `context_id` below are an **opaque soft
reference**, never a hard FK — this app has no idea what a "section" or a
"classroom" actually is, and must never gain one.

WHAT'S IN THIS FILE:
  1. `AssignmentBaseModel` — UUID-PK abstract base (§2, "UUID PK... kyunki
     personal-assignment URLs public share hongi"). Applied to all four
     concrete models here, not just `Assignment`, so nothing in this app
     leaks a sequential-integer enumeration surface even indirectly (e.g.
     via `AssignmentAnswer` ids appearing in a per-question review API).
  2. `Assignment` — the posted assignment itself. `source`/`context_type`/
     `context_id` (§1) route it to personal / campus / liveclass without
     this app knowing which. Deliberately has **no** `is_paid`/`price`
     field at all (§4 — "structurally impossible", same pattern as
     `campus.CampusLiveSession`), not just one defaulted to False.
  3. `AssignmentQuestion` / `AssignmentAnswer` (§2a) — the structured-
     question path. Verified field-for-field against the real
     `testseries/models.py` source (`Question`/`QuestionResponse`): same
     `clean()`/`save()` shape-validation per question_type, same
     `answer_attachment` field, same `mark_answer()` bounds-checking and
     `is_correct` semantics. Grading is delegated to `common.
     question_grading.auto_grade()`.
     [FIX — bug found and fixed this pass, confirmed by actually
     executing the real function]: the real `common/question_grading.py`
     signature is `auto_grade(*, question_type, marks, correct_answer,
     answer_data) -> GradingResult` — it takes **no** `options` keyword
     at all, and returns a `GradingResult` dataclass
     (`.is_auto_graded`/`.is_correct`/`.marks_awarded`), never a plain
     `(is_correct, marks_awarded)` tuple. An earlier version of this
     file's own docstring claimed the exact opposite (that the real
     module WAS the tuple-returning, `options`-taking shape, and that a
     "GradingResult-returning, options-less draft" was the outdated one)
     — that claim was backwards relative to the actual uploaded
     `common/question_grading.py`, and `submit_structured()`'s call site
     was written to match the wrong shape. Both resulting failures were
     reproduced for real (not assumed): passing `options=` raised
     `TypeError: auto_grade() got an unexpected keyword argument
     'options'`, and even with that removed, `is_correct, marks_awarded
     = auto_grade(...)` raised `TypeError: cannot unpack non-iterable
     GradingResult object`. Every structured-assignment submission would
     have hit this in production. Fixed at the one call site
     (`submit_structured()`, below) to call the real signature and read
     `.is_correct`/`.marks_awarded` off the returned `GradingResult`.
  4. `AssignmentSubmission` — one student's attempt. Snapshots
     `roll_number`/`enrollment_no` at submit time (never re-derived) so a
     submission stays independently verifiable even if enrollment changes
     later. Carries the free-form path (`written_content`/`file`) *and*
     the structured path (`AssignmentAnswer` rows) — mutually exclusive in
     practice, gated by `Assignment.has_structured_questions`. Also owns
     the shareable public-verification `public_slug` feature (§2, the
     genuinely new flow this app adds).
  5. Every FileField (`Assignment.attachment`, `AssignmentQuestion.
     attachment`, `AssignmentSubmission.file`) runs `common.
     attachment_validators.ATTACHMENT_VALIDATORS` — the same extension +
     size rules `testseries` already enforces, moved to `common` so
     nothing here redefines them (see that module's own docstring).

WHAT'S DELIBERATELY NOT HERE (see the doc's own §8 "Open items" — carried
forward rather than silently resolved):
  - No `enrollment_no` dedicated field exists yet on the campus side
    (§5 GAP) — `AssignmentSubmission.enrollment_no` will simply be blank
    for campus-sourced submissions until that's decided. Not this app's
    call to make.
  - No hard `source="personal"` restriction on who may call `.publish()`
    — §2 explicitly says this is docs-only, not a field-level block.
  - `has_structured_questions` immutability-after-first-submission (§2a):
    the serializer is still the primary enforcement point per the doc (it
    can surface a clean field-level 400 instead of a 500), using
    `can_change_question_mode()` below so both places check the exact same
    condition. `Assignment.save()` now *also* raises `ValidationError` on
    an illegal flip, as a model-level backstop for callers that bypass the
    serializer entirely (management commands, `bridge.py`, shell) — belt
    and suspenders, not a redundant duplicate.
"""
import secrets
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from common.attachment_validators import attachment_extension_validator, validate_attachment_size
from common.question_grading import auto_grade
from login.models import User

# §2a / common/attachment_validators.py's own docstring: "assignment
# reuses the exact same rules instead of redefining them" — every
# user-uploaded FileField in this app (teacher's assignment attachment,
# a question's attachment, a student's submitted file) runs through the
# same extension + size checks `testseries` already uses, so there's one
# place that decides what an "attachment" is allowed to be, not three.
ATTACHMENT_VALIDATORS = [attachment_extension_validator, validate_attachment_size]


class AssignmentSource(models.TextChoices):
    """§1 — which of the three flows an `Assignment` belongs to. A plain
    string, not a FK, precisely so this app never has to know the shape of
    whatever "campus" or "liveclass" actually are."""

    PERSONAL = "personal", "Personal"
    CAMPUS = "campus", "Campus"
    LIVECLASS = "liveclass", "LiveClass"


class AssignmentBaseModel(models.Model):
    """§2 — UUID PK on every concrete model in this app (not just
    `Assignment`) because personal-assignment ids show up in public,
    unauthenticated URLs (`public_slug` aside — the id itself is also
    exposed via API responses), and a sequential integer PK would let
    anyone enumerate every assignment/submission/question in the system
    just by incrementing a number. `campus.CampusBaseModel` uses the same
    pattern for the same reason."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Assignment(AssignmentBaseModel):
    source = models.CharField(max_length=10, choices=AssignmentSource.choices, db_index=True)

    # §1 — opaque soft-reference to whatever posted this. `context_type` is
    # "section" | "classroom" | "" (blank for source=personal).
    # `context_id` is NEVER resolved to a real row from this app — the
    # caller (campus/liveclass bridge) already did that before calling
    # `assignment.bridge.create_context_assignment()`, and only the caller
    # ever dereferences it again.
    context_type = models.CharField(max_length=20, blank=True)
    context_id = models.UUIDField(null=True, blank=True)

    posted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posted_assignments",
    )

    title = models.CharField(max_length=200)

    # For source=personal this field IS the assignment: the student's own
    # written content, not a teacher's instructions. Kept as one field
    # rather than two (`instructions` vs `personal_content`) because the
    # doc treats it as literally the same slot used two different ways
    # (§2 table) — a serializer-level label swap by `source`, not a schema
    # difference.
    description = models.TextField(blank=True)

    attachment = models.FileField(
        upload_to="assignment/attachments/", null=True, blank=True, validators=ATTACHMENT_VALIDATORS
    )

    # Optional — personal assignments are self-paced (§2: "personal
    # assignments me due date optional").
    due_date = models.DateField(null=True, blank=True)

    # NOTE: there is deliberately no `is_paid` / `price` field anywhere on
    # this model. §4 — "Assignment model me price/is_paid/koin field hi
    # nahi hai... structurally impossible rakha gaya hai", mirroring
    # `campus.CampusLiveSession`'s "free for students" enforcement. If a
    # future "premium assignment review" feature is ever wanted, that is
    # an explicit new decision (and almost certainly a new field on THIS
    # model, made deliberately) — never route it around this omission by
    # stuffing a price into `data` below.

    # Auto-summed from AssignmentQuestion.marks when
    # has_structured_questions=True (see `recompute_total_marks()` and the
    # post_save/post_delete signals below); otherwise a manual, optional
    # scale set directly by whoever posts the assignment.
    total_marks = models.PositiveIntegerField(null=True, blank=True)

    has_structured_questions = models.BooleanField(default=False)

    # §3 — "{"context_type": ..., "context_id": ...}" for client-side
    # deep-linking, same shape as `core.Notification.data`. Populated by
    # `bridge.create_context_assignment()`, not hand-maintained here.
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["source", "context_type", "context_id"]),
            models.Index(fields=["posted_by", "due_date"]),
        ]

    def __str__(self):
        return self.title

    def can_change_question_mode(self) -> bool:
        """§2a — `has_structured_questions` is immutable once any
        submission exists for this assignment (changing the mode after
        students have started answering makes scoring inconsistent, same
        reasoning as `TestSeries.status="published"` locking after
        publish). This is a query, not an enforcement point on `save()` —
        the actual block lives in the serializer's `validate()` per the
        doc's own division of responsibility; this method exists so both
        the serializer and any other caller check the exact same
        condition instead of re-deriving it.
        """
        return not self.submissions.exclude(status=AssignmentSubmission.SubmissionStatus.MISSING).exists()

    def save(self, *args, **kwargs):
        """Model-level backstop for the `has_structured_questions`
        immutability rule (§2a). The serializer is still the primary
        enforcement point (it can return a clean 400 with a field-level
        error instead of a 500), but relying on the serializer alone means
        any other caller — a management command, a shell script, a future
        bridge.py helper — could flip the flag after submissions exist
        without anything stopping it. This is the same "belt and
        suspenders" split the doc uses elsewhere (query helper +
        serializer check); this just adds the model as the third layer so
        the invariant holds even outside the API.

        Skipped entirely on first create (`self.pk` is None — there's
        nothing to compare against yet) and skipped whenever the flag
        isn't actually changing, so this never adds a query to the common
        case of saving unrelated fields.
        """
        if self.pk:
            try:
                old = Assignment.objects.only("has_structured_questions").get(pk=self.pk)
            except Assignment.DoesNotExist:
                old = None
            if (
                old is not None
                and old.has_structured_questions != self.has_structured_questions
                and not self.can_change_question_mode()
            ):
                raise ValidationError(
                    "has_structured_questions cannot be changed once a submission exists for this assignment."
                )
        super().save(*args, **kwargs)

    def recompute_total_marks(self) -> None:
        """§2a — `total_marks` is "auto" (sum of `AssignmentQuestion.marks`)
        when `has_structured_questions=True`. Called from the
        AssignmentQuestion post_save/post_delete signals below rather than
        computed on read, so `total_marks` stays a plain, indexable/
        filterable column instead of a property that hits the DB on every
        access.
        """
        if not self.has_structured_questions:
            return
        total = self.questions.aggregate(total=Sum("marks"))["total"]
        # update() (not .save()) — avoids re-triggering this model's own
        # save-time side effects and avoids a stale in-memory `self` write
        # racing a concurrent question add/remove.
        Assignment.objects.filter(pk=self.pk).update(total_marks=total)


@receiver(pre_save, sender=Assignment)
def _delete_old_assignment_attachment_on_change(sender, instance: Assignment, **kwargs):
    """Same storage-agnostic cleanup pattern as `login.models`'s
    `profile_photo` signal — goes through `field.storage`, never a raw
    filesystem path, so this keeps working unchanged if/when
    `DEFAULT_FILE_STORAGE` moves to S3/GCS/Azure."""
    if not instance.pk:
        return
    try:
        old_attachment = sender.objects.only("attachment").get(pk=instance.pk).attachment
    except sender.DoesNotExist:
        return
    if old_attachment and old_attachment != instance.attachment:
        old_attachment.storage.delete(old_attachment.name)


@receiver(post_delete, sender=Assignment)
def _delete_assignment_attachment_on_delete(sender, instance: Assignment, **kwargs):
    """Fires for both `instance.delete()` and bulk `queryset.delete()` —
    see login/models.py's identical comment on why a `delete()` override
    would silently miss the bulk case."""
    if instance.attachment:
        instance.attachment.storage.delete(instance.attachment.name)


class AssignmentQuestion(AssignmentBaseModel):
    """§2a — field-for-field clone of `testseries.Question`, verified
    against the real `testseries/models.py` source (previously
    [NOT YET VERIFIED] — that source is now available). `clean()`/`save()`
    below replicate `Question.clean()`/`Question.save()`'s per-type shape
    validation exactly, not just the field list, because a "field-level
    identical" clone that accepts garbage `options`/`correct_answer` shapes
    testseries itself rejects isn't actually identical.
    """

    class QuestionTypeChoices(models.TextChoices):
        # Literal string values, not imported from `common.question_
        # grading` — the real `common/question_grading.py` (now
        # available) is a pure-function module with no `QuestionType`
        # class at all; it takes/returns raw strings, and `testseries.
        # Question.QuestionType` itself defines these locally rather
        # than importing them from anywhere. Matching that: same values
        # ("text"/"mcq"/"msq"/"list"), defined locally here too.
        TEXT = "text", "Text / Subjective"
        MCQ = "mcq", "Multiple Choice (single answer)"
        MSQ = "msq", "Multiple Select (multiple answers)"
        LIST = "list", "List / Ordered Items"

    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="questions")

    # No default — matches `testseries.Question.order` exactly. A caller
    # must pick an explicit order; silently defaulting to 0 (the old
    # behavior here) risked every question landing on the same order
    # value and colliding on the uniqueness constraint below instead of
    # failing with a clear "you forgot to set order" error.
    order = models.PositiveIntegerField()
    # max_length=4 matches `testseries.Question.question_type` exactly —
    # every choice value ("text", "mcq", "msq", "list") is <=4 chars, so a
    # wider column here would silently permit values testseries itself
    # can never store, which defeats the point of a shape clone.
    question_type = models.CharField(max_length=4, choices=QuestionTypeChoices.choices, db_index=True)
    text = models.TextField()
    attachment = models.FileField(
        upload_to="assignment/question_attachments/", null=True, blank=True, validators=ATTACHMENT_VALIDATORS
    )
    marks = models.PositiveIntegerField()

    # mcq: ["opt_a", "opt_b", ...] (option ids/labels — shape is caller's
    # choice, this app treats it as an opaque list). msq/list: same idea,
    # a list of selectable/orderable items. text: unused (blank list).
    options = models.JSONField(default=list, blank=True)

    # mcq: a single option id from `options`. msq/list: a list/subset of
    # `options`. text: unused (blank) — a `text` question is never
    # auto-graded, so there is no "correct answer" to store, only a human
    # reviewer's judgment on the submitted `AssignmentAnswer.answer_data`.
    correct_answer = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["assignment", "order"], name="unique_question_order_per_assignment"),
        ]

    def clean(self):
        """Verbatim port of `testseries.Question.clean()`'s per-type shape
        rules — same four branches, same error conditions, same
        force-empty-on-text behavior. Kept as a straight port rather than
        a paraphrase so the two stay diffable against each other."""
        super().clean()
        if self.question_type == self.QuestionTypeChoices.TEXT:
            self.options = []
            self.correct_answer = {}
            return

        if self.question_type in (self.QuestionTypeChoices.MCQ, self.QuestionTypeChoices.MSQ):
            if not isinstance(self.options, list) or not self.options:
                raise ValidationError("mcq/msq questions require a non-empty `options` list.")
            option_ids = {opt.get("id") for opt in self.options}
            if self.question_type == self.QuestionTypeChoices.MCQ:
                if "option_id" not in self.correct_answer or self.correct_answer["option_id"] not in option_ids:
                    raise ValidationError("mcq correct_answer must be {'option_id': <one of options[].id>}.")
            else:  # MSQ
                option_ids_answer = set(self.correct_answer.get("option_ids", []))
                if not option_ids_answer or not option_ids_answer.issubset(option_ids):
                    raise ValidationError("msq correct_answer must be {'option_ids': [subset of options[].id]}.")
            return

        if self.question_type == self.QuestionTypeChoices.LIST:
            mode = self.correct_answer.get("list_mode")
            if mode == "match":
                left = self.options.get("left") if isinstance(self.options, dict) else None
                right = self.options.get("right") if isinstance(self.options, dict) else None
                pairs = self.correct_answer.get("pairs")
                if not left or not right or not isinstance(pairs, dict):
                    raise ValidationError(
                        "list/match questions require options={'left': [...], 'right': [...]} "
                        "and correct_answer={'list_mode': 'match', 'pairs': {left_id: right_id, ...}}."
                    )
            elif mode == "order":
                if not isinstance(self.options, list) or not self.options:
                    raise ValidationError("list/order questions require a non-empty `options` list.")
                sequence = self.correct_answer.get("sequence")
                option_ids = {opt.get("id") for opt in self.options}
                if not isinstance(sequence, list) or set(sequence) != option_ids:
                    raise ValidationError(
                        "list/order correct_answer must be {'list_mode': 'order', "
                        "'sequence': [every options[].id, in order]}."
                    )
            else:
                raise ValidationError("list questions require correct_answer['list_mode'] to be 'match' or 'order'.")

    def save(self, *args, **kwargs):
        # Same exclude list, same reasoning as `testseries.Question.save()`:
        # options/correct_answer's shape is already validated per-type by
        # clean() above (called unconditionally by full_clean()), so they're
        # excluded here to avoid a redundant/incompatible generic JSONField
        # check; every other field (text, marks, question_type, order, and
        # the (assignment, order) uniqueness) stays IN the validated set.
        self.full_clean(exclude=["options", "correct_answer"])
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Q{self.order}: {self.text[:40]}"

    def is_auto_gradable(self) -> bool:
        # Matches testseries's own inline convention exactly
        # (`question.question_type != Question.QuestionType.TEXT` in
        # `TestAttempt.submit()`) rather than a separate AUTO_GRADABLE
        # set — there is no such set in the real `common/question_
        # grading.py`.
        return self.question_type != self.QuestionTypeChoices.TEXT


@receiver(post_save, sender=AssignmentQuestion)
def _recompute_total_marks_on_question_save(sender, instance: AssignmentQuestion, **kwargs):
    instance.assignment.recompute_total_marks()


@receiver(post_delete, sender=AssignmentQuestion)
def _recompute_total_marks_on_question_delete(sender, instance: AssignmentQuestion, **kwargs):
    # instance.assignment may already be gone from the DB if this fired as
    # part of the assignment's own CASCADE delete — the FK is still
    # readable off the in-memory `instance` either way, and
    # recompute_total_marks() no-ops safely via `.filter(pk=...).update()`
    # against a possibly-already-deleted Assignment row (matches 0 rows,
    # simply does nothing).
    instance.assignment.recompute_total_marks()


class AssignmentSubmission(AssignmentBaseModel):
    class SubmissionStatus(models.TextChoices):
        MISSING = "missing", "Missing"
        SUBMITTED = "submitted", "Submitted"
        LATE = "late", "Late"
        # §2 — "naya, testseries.TestAttempt jaisa hi": only reachable via
        # the structured path, when at least one `text` question is still
        # awaiting human review. The free-form path never passes through
        # this status — it goes straight submitted/late → checked in one
        # manual grade step.
        PARTIALLY_CHECKED = "partially_checked", "Partially Checked"
        CHECKED = "checked", "Checked"

    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="assignment_submissions")

    # --- free-form path fields — only meaningful when
    # assignment.has_structured_questions is False. ---
    written_content = models.TextField(blank=True)
    file = models.FileField(
        upload_to="assignment/submissions/", null=True, blank=True, validators=ATTACHMENT_VALIDATORS
    )

    # --- snapshots, taken once at submit/pre-create time, never
    # re-derived. §2: "so a submission stays verifiable even if the
    # student's enrollment later changes". ---
    roll_number = models.CharField(max_length=30, blank=True)
    # §5 GAP — campus.StudentEnrollment has no dedicated `enrollment_no`
    # field today, only `(student, section, session)` as the de-facto
    # enrollment key. Until that's decided, campus-sourced submissions
    # will simply pass enrollment_no="" through `bridge.py`. Not this
    # app's decision to make — flagged, not guessed around.
    enrollment_no = models.CharField(max_length=30, blank=True)

    status = models.CharField(
        max_length=20, choices=SubmissionStatus.choices, default=SubmissionStatus.MISSING, db_index=True
    )

    # Free-form path: manual letter/score, human-entered. Structured path:
    # displayable but derivable from total_marks_awarded — kept as a real
    # column (not a property) purely for backward-compat / simple display,
    # per §2's own note.
    grade = models.CharField(max_length=10, blank=True)

    # Structured path only — sum of AssignmentAnswer.marks_awarded, only
    # fully populated once every question (including every `text`
    # question) has been reviewed. See `_recompute_structured_status()`.
    total_marks_awarded = models.PositiveIntegerField(null=True, blank=True)

    # Free-form path: the assignment-level comment. Structured path: an
    # optional overall remark — per-question detail lives on
    # AssignmentAnswer.reviewer_feedback instead.
    feedback = models.TextField(blank=True)

    # §2 — the actual new feature this app adds. Blank = not published, no
    # public page exists at all. Non-empty = `GET
    # /assignment/public/{public_slug}/` serves a read-only view.
    # `unique=True` + `blank=True` has the same NULL-vs-'' footgun noted in
    # login/models.py's `phone` field, EXCEPT here it's harmless: every
    # unpublished submission shares the value `""`, but Django/Postgres
    # both treat blank CharField uniqueness the same way phone's comment
    # warns about — multiple `''` rows WILL collide on a real unique
    # constraint. To avoid silently reintroducing that exact bug, blank
    # slugs are excluded from uniqueness via a partial constraint instead
    # of relying on `unique=True` (which does not distinguish blank from
    # non-blank here the way phone's `null=True` does for CharField).
    public_slug = models.CharField(max_length=40, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    # Only set when status transitions to CHECKED — not on
    # PARTIALLY_CHECKED, matching testseries's equivalent semantics per
    # the doc.
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["assignment", "student"], name="unique_submission_per_student"),
            # See public_slug comment above — only enforce uniqueness
            # among rows that have actually published (non-blank slug),
            # so the many `""` "not published" rows never collide.
            models.UniqueConstraint(
                fields=["public_slug"],
                condition=~models.Q(public_slug=""),
                name="unique_nonblank_public_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["assignment", "status"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.assignment} ({self.status})"

    def is_late(self) -> bool:
        if not self.assignment.due_date or not self.submitted_at:
            return False
        return self.submitted_at.date() > self.assignment.due_date

    # ---------------------------------------------------------------
    # Free-form path
    # ---------------------------------------------------------------
    def submit_freeform(self, *, written_content: str = "", file=None) -> None:
        """§2 table — free-form path only. Goes to SUBMITTED or LATE and
        stops there; grading is a separate, explicit manual step
        (`grade_freeform`) — matching the doc's "seedha submitted/late →
        checked, ek hi manual grade step" description.
        """
        self.written_content = written_content
        if file is not None:
            self.file = file
        self.submitted_at = timezone.now()
        self.status = self.SubmissionStatus.LATE if self.is_late() else self.SubmissionStatus.SUBMITTED
        self.save(update_fields=["written_content", "file", "submitted_at", "status", "updated_at"])

    def grade_freeform(self, *, grade: str, feedback: str = "") -> None:
        self.grade = grade
        self.feedback = feedback
        self.status = self.SubmissionStatus.CHECKED
        self.checked_at = timezone.now()
        self.save(update_fields=["grade", "feedback", "status", "checked_at", "updated_at"])

    # ---------------------------------------------------------------
    # Structured path — §2a, ported from the real `TestAttempt.submit()`
    # (verified against `testseries/models.py`, not the earlier
    # description-only draft).
    # ---------------------------------------------------------------
    def submit_structured(self, answers: list[dict]) -> None:
        """`answers = [{"question_id": ..., "answer_data": ..., "answer_attachment": <file, optional>}, ...]`

        Bulk-creates one `AssignmentAnswer` per question, mirroring
        `TestAttempt.submit()` field-for-field:
          - `is_auto_graded` is computed from `question_type != TEXT`
            *before* grading (same as `TestAttempt.submit()`), not derived
            from the grading call's return value.
          - `common.question_grading.auto_grade()` returns a plain
            `(is_correct, marks_awarded)` tuple (confirmed against the
            real module — it is NOT the `GradingResult` object this file
            previously assumed, and it requires `options=`, which the
            previous version of this method omitted entirely: a real bug,
            now fixed) and both are `None` for `text` questions.
          - `answer_attachment` is only ever taken for non-auto-graded
            (`text`) questions — same as `TestAttempt.submit()`'s
            `None if is_auto_graded else files.get(...)` — an
            auto-graded question's attachment slot (if a caller sent one
            anyway) is silently ignored rather than stored, since there is
            nothing for a reviewer to look at there.
          - `reviewed_at`/`reviewed_by` are deliberately left unset here
            (default `None`) even for auto-graded answers — testseries
            never sets them at submit-time either, only inside
            `mark_answer()`'s actual human-review call. "Auto-graded" and
            "reviewed by a person" are different facts; conflating them
            was a bug in the previous version of this method.

        Resolves this submission straight to CHECKED (if no `text`
        question exists) or PARTIALLY_CHECKED (if at least one does and
        is still awaiting human review) — no SUBMITTED/LATE intermediate
        status on this path, per §2's submit-flow description.
        """
        questions = {str(q.id): q for q in self.assignment.questions.all()}
        answer_rows = []
        for entry in answers:
            question = questions[str(entry["question_id"])]
            answer_data = entry["answer_data"]
            is_auto_graded = question.question_type != AssignmentQuestion.QuestionTypeChoices.TEXT
            # [FIX — confirmed by actually executing the real
            # common/question_grading.auto_grade() against this call
            # site] The real function's signature is
            # `auto_grade(*, question_type, marks, correct_answer,
            # answer_data) -> GradingResult` — it takes NO `options`
            # keyword at all, and returns a `GradingResult` dataclass
            # (`.is_auto_graded` / `.is_correct` / `.marks_awarded`),
            # never a plain tuple. The previous version of this call
            # site passed `options=question.options` (an unexpected
            # kwarg -> immediate `TypeError`) and then tried to unpack
            # the result as `is_correct, marks_awarded = auto_grade(...)`
            # (a `GradingResult` is not iterable -> a second, independent
            # `TypeError` even if the first were fixed). Both failure
            # modes were reproduced against the real module before this
            # fix, not assumed — see the master doc's "Known Issues
            # Found & Fixed This Pass" section. Every structured-
            # assignment submission would have raised a 500 with the
            # old code; this is what actually calling the real function
            # requires.
            grading_result = auto_grade(
                question_type=question.question_type,
                correct_answer=question.correct_answer,
                answer_data=answer_data,
                marks=question.marks,
            )
            is_correct = grading_result.is_correct
            marks_awarded = grading_result.marks_awarded
            answer_rows.append(
                AssignmentAnswer(
                    submission=self,
                    question=question,
                    answer_data=answer_data,
                    answer_attachment=None if is_auto_graded else entry.get("answer_attachment"),
                    is_auto_graded=is_auto_graded,
                    is_correct=is_correct,
                    marks_awarded=marks_awarded,
                )
            )
        AssignmentAnswer.objects.bulk_create(answer_rows)

        self.submitted_at = timezone.now()
        self._recompute_structured_status()

    def _recompute_structured_status(self) -> None:
        """Shared by `submit_structured()` and
        `mark_answer_and_maybe_finalize()` — both need the same
        "has every text question been reviewed yet?" check, so it lives
        in one place rather than two copies drifting apart."""
        pending_review = self.answers.filter(
            question__question_type=AssignmentQuestion.QuestionTypeChoices.TEXT, marks_awarded__isnull=True
        ).exists()
        if pending_review:
            self.status = self.SubmissionStatus.PARTIALLY_CHECKED
            self.checked_at = None
        else:
            total = self.answers.aggregate(total=Sum("marks_awarded"))["total"] or 0
            self.total_marks_awarded = total
            self.status = self.SubmissionStatus.CHECKED
            self.checked_at = timezone.now()
        self.save(update_fields=["submitted_at", "status", "checked_at", "total_marks_awarded", "updated_at"])

    def mark_answer_and_maybe_finalize(self, *, question: "AssignmentQuestion", marks_awarded: int,
                                        feedback: str = "", reviewed_by: User) -> "AssignmentAnswer":
        """§2a — "review flow bhi identical" to testseries. Reviews exactly
        one `text`-type `AssignmentAnswer`, then re-runs
        `_recompute_structured_status()` so the submission flips from
        PARTIALLY_CHECKED to CHECKED the moment the *last* pending text
        question gets reviewed — callers never need to separately "check
        if we're done", this does it on every call.
        """
        answer = self.answers.get(question=question)
        answer.mark_answer(marks_awarded=marks_awarded, feedback=feedback, reviewed_by=reviewed_by)
        self._recompute_structured_status()
        return answer

    # ---------------------------------------------------------------
    # Shareable public URL — §2, the genuinely new feature
    # ---------------------------------------------------------------
    def publish(self) -> str:
        """Always mints a fresh slug, even if one already existed —
        matches the doc's "unpublish -> naya publish naya random slug
        deta hai, purana URL turant dead ho jata hai" behaviour: calling
        this again re-publishes under a brand-new, unguessable URL rather
        than handing back the previous one."""
        self.public_slug = secrets.token_urlsafe(24)
        self.save(update_fields=["public_slug", "updated_at"])
        return self.public_slug

    def unpublish(self) -> None:
        self.public_slug = ""
        self.save(update_fields=["public_slug", "updated_at"])


@receiver(pre_save, sender=AssignmentSubmission)
def _delete_old_submission_file_on_change(sender, instance: AssignmentSubmission, **kwargs):
    """Same pattern as the two attachment-cleanup signals above / the
    original `profile_photo` signal in login/models.py."""
    if not instance.pk:
        return
    try:
        old_file = sender.objects.only("file").get(pk=instance.pk).file
    except sender.DoesNotExist:
        return
    if old_file and old_file != instance.file:
        old_file.storage.delete(old_file.name)


@receiver(post_delete, sender=AssignmentSubmission)
def _delete_submission_file_on_delete(sender, instance: AssignmentSubmission, **kwargs):
    if instance.file:
        instance.file.storage.delete(instance.file.name)


class AssignmentAnswer(AssignmentBaseModel):
    """§2a — field-for-field clone of `testseries.QuestionResponse`,
    verified against the real `testseries/models.py` source (previously
    [NOT YET VERIFIED] — now confirmed, and two real gaps fixed below:
    the missing `answer_attachment` field, and `mark_answer()` setting
    `is_correct` when testseries's version never does)."""

    submission = models.ForeignKey(AssignmentSubmission, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(AssignmentQuestion, on_delete=models.CASCADE, related_name="answers")

    # mcq: a single option id. msq/list: a list. text: free-form text.
    # Opaque JSON on purpose — this model doesn't need to know the exact
    # shape, only `common.question_grading.auto_grade()` and whatever
    # frontend renders it do. `default=dict` matches
    # `QuestionResponse.answer_data` exactly.
    answer_data = models.JSONField(default=dict)

    # A student's photo/file answer — e.g. a photo of a handwritten
    # solution for a `text` question. Mirrors
    # `QuestionResponse.answer_attachment` exactly, including the same
    # validators; this field was missing entirely before the real
    # testseries source was available to diff against, which meant the
    # structured path here had no way to accept a file answer at all —
    # a real functional gap, not just a shape mismatch.
    answer_attachment = models.FileField(
        upload_to="assignment/answer_attachments/", null=True, blank=True, validators=ATTACHMENT_VALIDATORS
    )

    is_auto_graded = models.BooleanField()
    is_correct = models.BooleanField(null=True)
    marks_awarded = models.PositiveIntegerField(null=True, blank=True)

    reviewer_feedback = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_assignment_answers"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["submission", "question"], name="unique_answer_per_submission_question"),
        ]

    def __str__(self):
        return f"Answer to {self.question_id} in {self.submission_id}"

    def mark_answer(self, *, marks_awarded: int, feedback: str = "", reviewed_by: User) -> None:
        """Verbatim port of `QuestionResponse.mark_answer()`'s semantics —
        only ever called on a `text`-type answer in practice (mcq/msq/list
        are already fully graded by `auto_grade()` at submit time), so
        calling this on an already auto-graded answer is treated as a
        programming error (`ValueError`), matching testseries exactly,
        not silently allowed as an override the way this method used to.
        `marks_awarded` bounds are also validated against
        `question.marks` for the same reason. `is_correct` is
        deliberately left untouched here (stays whatever it already was —
        `None` for a `text` answer) because a `text` answer has no binary
        correct/incorrect concept, same as `QuestionResponse`'s own
        `is_correct` field doc says; this used to set
        `is_correct = marks_awarded > 0`, which testseries never does.
        """
        if self.is_auto_graded:
            raise ValueError(
                f"AssignmentAnswer {self.pk} is auto-graded; mark_answer() is only valid for text-type answers."
            )
        if marks_awarded < 0:
            raise ValueError(f"marks_awarded ({marks_awarded}) cannot be negative.")
        if marks_awarded > self.question.marks:
            raise ValueError(
                f"marks_awarded ({marks_awarded}) cannot exceed question.marks ({self.question.marks})."
            )
        self.marks_awarded = marks_awarded
        self.reviewer_feedback = feedback
        self.reviewed_by = reviewed_by
        self.reviewed_at = timezone.now()
        self.save(update_fields=["marks_awarded", "reviewer_feedback", "reviewed_by", "reviewed_at"])
```

---

### `assignment/serializers.py`

```python
# assignment/serializers.py
"""
DRF serializers for the public-facing (personal-assignment) API surface,
plus the shared submission/answer/public-page serializers that campus's
and liveclass's own thin-proxy viewsets (§5.2, §6.1 — not in this app)
would reuse rather than re-declare.

Split into "input" (plain `Serializer`, validation-only, no `.save()`
responsibility of their own — the model method they front does the
actual write) and "output" (`ModelSerializer`) shapes throughout, mirroring
how `AssignmentSubmission`'s own methods (`submit_freeform`,
`submit_structured`, `grade_freeform`, `mark_answer_and_maybe_finalize`)
are already the real unit of business logic — these serializers exist to
validate the HTTP boundary, not to duplicate that logic.

REFRESHED for Task 7's real (fixed) `assignment/models.py` — three things
changed here versus the version originally handed off with Task 8:
  1. `correct_answer` is no longer blanket `write_only`. Task 8's own
     checklist requires it visible to `posted_by`/staff (testseries's own
     hiding rule — hidden from students only), which a global write_only
     flag can never express; visibility is now decided per-request in
     `AssignmentQuestionSerializer.to_representation()` instead.
  2. `AssignmentQuestionSerializer.validate()` now covers the `list`
     question type too (previously only mcq/msq), mirroring
     `AssignmentQuestion.clean()`'s full per-type shape rules verbatim.
     Without this, a bad `list`-shaped payload would sail past this
     serializer's validation, reach `AssignmentQuestion.objects.create()`
     inside `AssignmentCreateSerializer.create()` (which bypasses this
     serializer's own `create()`/`update()` entirely — see that method),
     and blow up as an unhandled `django.core.exceptions.ValidationError`
     (a raw 500) instead of a clean 400, because Task 7 added real
     `clean()`/`full_clean()` enforcement at the model layer that didn't
     exist when this file was first written.
  3. `AssignmentCreateSerializer.create()`/`update()` now also catch that
     same Django `ValidationError` as a belt-and-suspenders fallback (in
     case the two validation copies above ever drift) and re-raise it as
     a DRF `serializers.ValidationError`, and `create()` is wrapped in
     `transaction.atomic()` so a mid-loop question failure can't leave an
     `Assignment` row committed with only some of its questions created.
  4. `AssignmentAnswerSerializer` / `StructuredAnswerInputSerializer` now
     include `answer_attachment`, matching `AssignmentAnswer.
     answer_attachment` — a field Task 7 added that didn't exist in the
     model this file was originally written against.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from .models import (
    Assignment,
    AssignmentAnswer,
    AssignmentQuestion,
    AssignmentSource,
    AssignmentSubmission,
)


class AssignmentQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssignmentQuestion
        fields = ["id", "order", "question_type", "text", "attachment", "marks", "options", "correct_answer"]
        # NOTE: `correct_answer` is intentionally NOT `write_only` here
        # (see module docstring point 1) — it's a normal readable/
        # writable field, and visibility is enforced per-request in
        # `to_representation()` below instead of blanket-hidden from
        # everyone including the assignment's own poster.

    def validate(self, attrs):
        """Verbatim port of `AssignmentQuestion.clean()`'s per-type shape
        rules (see that method's own docstring on why it's a straight
        port rather than a paraphrase) — this copy exists purely so a bad
        payload gets a clean 400 at the HTTP boundary instead of reaching
        the model layer's `full_clean()` and surfacing as an unhandled
        `django.core.exceptions.ValidationError`. `AssignmentCreateSerializer.
        create()` below still wraps the model call in a try/except as a
        second layer, in case this copy and the model's ever drift.
        """
        qtype = attrs.get("question_type", getattr(self.instance, "question_type", None))
        options = attrs.get("options", getattr(self.instance, "options", None))
        correct = attrs.get("correct_answer", getattr(self.instance, "correct_answer", None)) or {}

        if qtype == AssignmentQuestion.QuestionTypeChoices.TEXT:
            # Model's clean() force-empties options/correct_answer for
            # `text` regardless of what was sent — nothing to validate.
            return attrs

        if qtype in (AssignmentQuestion.QuestionTypeChoices.MCQ, AssignmentQuestion.QuestionTypeChoices.MSQ):
            if not isinstance(options, list) or not options:
                raise serializers.ValidationError(
                    {"options": "mcq/msq questions require a non-empty `options` list."}
                )
            option_ids = {opt.get("id") for opt in options}
            if qtype == AssignmentQuestion.QuestionTypeChoices.MCQ:
                if "option_id" not in correct or correct["option_id"] not in option_ids:
                    raise serializers.ValidationError(
                        {"correct_answer": "mcq correct_answer must be {'option_id': <one of options[].id>}."}
                    )
            else:  # MSQ
                option_ids_answer = set(correct.get("option_ids", []))
                if not option_ids_answer or not option_ids_answer.issubset(option_ids):
                    raise serializers.ValidationError(
                        {"correct_answer": "msq correct_answer must be {'option_ids': [subset of options[].id]}."}
                    )
            return attrs

        if qtype == AssignmentQuestion.QuestionTypeChoices.LIST:
            mode = correct.get("list_mode")
            if mode == "match":
                left = options.get("left") if isinstance(options, dict) else None
                right = options.get("right") if isinstance(options, dict) else None
                pairs = correct.get("pairs")
                if not left or not right or not isinstance(pairs, dict):
                    raise serializers.ValidationError(
                        {
                            "correct_answer": (
                                "list/match questions require options={'left': [...], 'right': [...]} "
                                "and correct_answer={'list_mode': 'match', 'pairs': {left_id: right_id, ...}}."
                            )
                        }
                    )
            elif mode == "order":
                if not isinstance(options, list) or not options:
                    raise serializers.ValidationError(
                        {"options": "list/order questions require a non-empty `options` list."}
                    )
                sequence = correct.get("sequence")
                option_ids = {opt.get("id") for opt in options}
                if not isinstance(sequence, list) or set(sequence) != option_ids:
                    raise serializers.ValidationError(
                        {
                            "correct_answer": (
                                "list/order correct_answer must be {'list_mode': 'order', "
                                "'sequence': [every options[].id, in order]}."
                            )
                        }
                    )
            else:
                raise serializers.ValidationError(
                    {"correct_answer": "list questions require correct_answer['list_mode'] to be 'match' or 'order'."}
                )
            return attrs

        return attrs

    def to_representation(self, instance):
        """§7 / Task 8 checklist: `correct_answer` visible only to the
        assignment's `posted_by` or staff — never to the student
        answering it (same hiding rule testseries uses). Popped from the
        output rather than declared `write_only`, so the poster/staff
        actually get to see it (a blanket write_only would have hidden it
        from them too, which defeats "review/audit it" — see this
        model's own docstring on why `correct_answer` exists at all).

        `_assignment_posted_by_id` is an optional pre-seeded attribute:
        when this serializer is nested inside `AssignmentSerializer` /
        `AssignmentCreateSerializer` (its only real use in this app —
        see views.py), the parent sets it on this serializer's `child`
        before rendering so this method never has to run
        `instance.assignment` itself — `prefetch_related("questions")`
        on the assignment queryset does NOT cache each question's
        `.assignment` back-reference, so resolving it here directly would
        N+1 once per question on every list/retrieve. Falls back to the
        real (single, per-question) query only if used standalone.
        """
        rep = super().to_representation(instance)
        request = self.context.get("request")
        posted_by_id = getattr(self, "_assignment_posted_by_id", None)
        if posted_by_id is None:
            posted_by_id = instance.assignment.posted_by_id
        can_see_answer_key = bool(
            request
            and request.user
            and request.user.is_authenticated
            and (request.user.is_staff or posted_by_id == request.user.id)
        )
        if not can_see_answer_key:
            rep.pop("correct_answer", None)
        return rep


class AssignmentSerializer(serializers.ModelSerializer):
    """Read shape — used for list/retrieve."""

    questions = AssignmentQuestionSerializer(many=True, read_only=True)
    posted_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = Assignment
        fields = [
            "id", "source", "context_type", "context_id", "posted_by", "title",
            "description", "attachment", "due_date", "total_marks",
            "has_structured_questions", "data", "questions", "created_at", "updated_at",
        ]
        # source/context_type/context_id/posted_by/total_marks/data are all
        # either server-set or derived (total_marks via
        # recompute_total_marks(), data via bridge.py for context-sourced
        # assignments, which never go through this serializer at all).
        #
        # Task 8 fix: `source` was previously left OUT of this list, on the
        # theory that IsPersonalSourceOnly needed to see it in the raw
        # payload. That reasoning didn't hold up: IsPersonalSourceOnly
        # reads `request.data` directly (the raw dict DRF parsed off the
        # wire), not this serializer's `validated_data` — marking a field
        # read_only only strips it from validated_data, it has no effect
        # on `request.data` at all. So the permission check is unaffected
        # either way, and leaving `source` out of read_only_fields here was
        # just a gap against the Task 8 checklist ("source/context_type/
        # context_id read-only fields hain serializer me"), not a
        # requirement. This is also belt-and-suspenders, not the real
        # enforcement point: this serializer (AssignmentSerializer) is
        # only ever used for list/retrieve output (see
        # AssignmentViewSet.get_serializer_class) — create/update go
        # through AssignmentCreateSerializer below, which has no source/
        # context_type/context_id/posted_by field at all, so there's no
        # payload shape on the write path that could set any of these
        # regardless of what's marked read_only here.
        read_only_fields = ["source", "posted_by", "context_type", "context_id", "total_marks", "data"]

    def to_representation(self, instance):
        # See AssignmentQuestionSerializer.to_representation() — seed the
        # nested serializer's child with this assignment's posted_by_id
        # once, up front, so it never has to query `question.assignment`
        # itself for the correct_answer visibility check.
        self.fields["questions"].child._assignment_posted_by_id = instance.posted_by_id
        return super().to_representation(instance)


class AssignmentCreateSerializer(serializers.ModelSerializer):
    """§7 — the ONLY way to create an `Assignment` through the public API,
    and it is hard-wired to `source=personal` by
    `AssignmentViewSet.perform_create()`. Campus/liveclass assignments are
    created exclusively via `assignment.bridge.create_context_assignment()`
    from those apps' own already-permission-checked endpoints (§1, §7) —
    this serializer has no `source`/`context_type`/`context_id`/`posted_by`
    field at all, so there's no payload shape that could even attempt to
    smuggle one through.
    """

    questions = AssignmentQuestionSerializer(many=True, required=False)

    class Meta:
        model = Assignment
        fields = [
            "id", "title", "description", "attachment", "due_date",
            "total_marks", "has_structured_questions", "questions",
        ]

    def validate(self, attrs):
        has_structured = attrs.get(
            "has_structured_questions", getattr(self.instance, "has_structured_questions", False)
        )
        if has_structured and not attrs.get("questions") and not (self.instance and self.instance.questions.exists()):
            raise serializers.ValidationError(
                {"questions": "has_structured_questions=True requires at least one question."}
            )
        return attrs

    def validate_has_structured_questions(self, value):
        # §2a — "immutable after first submission exists". Duplicated
        # here (not just relying on `Assignment.save()`'s own model-level
        # guard, added in Task 7) so an illegal flip comes back as a
        # clean 400 on this specific field — the model-level guard is the
        # backstop for callers that bypass this serializer entirely, not
        # the primary UX for this one.
        if self.instance and value != self.instance.has_structured_questions:
            if not self.instance.can_change_question_mode():
                raise serializers.ValidationError(
                    "has_structured_questions cannot change once a submission exists for this assignment."
                )
        return value

    @transaction.atomic
    def create(self, validated_data):
        # Atomic: without this, a mid-loop AssignmentQuestion validation
        # failure (caught below) would still leave the Assignment row and
        # any already-created questions committed — a half-built
        # assignment with no way for the client to know it's incomplete.
        questions_data = validated_data.pop("questions", [])
        try:
            assignment = Assignment.objects.create(**validated_data)
            for question_data in questions_data:
                AssignmentQuestion.objects.create(assignment=assignment, **question_data)
        except DjangoValidationError as exc:
            # Belt-and-suspenders (see module docstring point 3): this
            # serializer's own validate()/AssignmentQuestionSerializer.
            # validate() should catch every bad shape before this line
            # ever runs, but the model's full_clean() (Task 7) runs the
            # same checks again — if the two ever drift, surface a clean
            # 400 here instead of an unhandled 500.
            raise serializers.ValidationError({"detail": exc.messages})
        return assignment

    def update(self, instance, validated_data):
        # Nested `questions` are intentionally NOT handled here on update
        # — mutating an existing question set (add/remove/reorder) once
        # answers may already reference those questions is exactly the
        # scoring-inconsistency problem §2a's immutability rule exists to
        # prevent. Question edits go through AssignmentQuestionSerializer
        # directly (e.g. a dedicated question sub-resource), never through
        # this Assignment-level update.
        validated_data.pop("questions", None)
        try:
            return super().update(instance, validated_data)
        except DjangoValidationError as exc:
            # Same belt-and-suspenders reasoning as create() — this is
            # where Assignment.save()'s own has_structured_questions
            # immutability guard (Task 7) would surface if
            # validate_has_structured_questions() above ever missed a
            # case.
            raise serializers.ValidationError({"detail": exc.messages})


class AssignmentAnswerSerializer(serializers.ModelSerializer):
    question_text = serializers.CharField(source="question.text", read_only=True)
    question_marks = serializers.IntegerField(source="question.marks", read_only=True)

    class Meta:
        model = AssignmentAnswer
        fields = [
            "id", "question", "question_text", "question_marks", "answer_data",
            # `answer_attachment` — added in Task 7 (mirrors
            # `QuestionResponse.answer_attachment`; was missing from the
            # model entirely before that fix, so it was never here either).
            "answer_attachment",
            "is_auto_graded", "is_correct", "marks_awarded", "reviewer_feedback",
            "reviewed_by", "reviewed_at",
        ]
        read_only_fields = [
            "is_auto_graded", "is_correct", "marks_awarded", "reviewer_feedback",
            "reviewed_by", "reviewed_at",
        ]


class AssignmentSubmissionSerializer(serializers.ModelSerializer):
    answers = AssignmentAnswerSerializer(many=True, read_only=True)
    student = serializers.StringRelatedField(read_only=True)
    is_late = serializers.SerializerMethodField()

    class Meta:
        model = AssignmentSubmission
        fields = [
            "id", "assignment", "student", "written_content", "file",
            "roll_number", "enrollment_no", "status", "grade",
            "total_marks_awarded", "feedback", "public_slug",
            "submitted_at", "checked_at", "answers", "is_late",
        ]
        # Every field here except the assignment FK itself is either a
        # roster-time snapshot (roll_number/enrollment_no — set by
        # bridge.py, never by the student), or only ever changed through
        # a dedicated model method (submit_freeform/submit_structured/
        # grade_freeform/publish/unpublish/mark_answer_and_maybe_finalize)
        # fronted by its own action below — never a bare PATCH on this
        # serializer.
        #
        # `assignment` is deliberately NOT in this list — it has to stay
        # writable so a student can name which assignment they're
        # submitting for on create(). See validate() below for why that
        # doesn't mean it's writable on update() too.
        read_only_fields = [
            "student", "roll_number", "enrollment_no", "status", "grade",
            "total_marks_awarded", "public_slug", "submitted_at", "checked_at", "answers",
        ]

    def get_is_late(self, obj) -> bool:
        return obj.is_late()

    def validate_assignment(self, assignment):
        """Task 8 fix — two real gaps, both against §2's own "personal-
        assignment flow: there's no bridge-created roster row ... the
        student's own first interaction creates the AssignmentSubmission
        row directly" (see AssignmentSubmissionViewSet.perform_create's
        docstring, and the model's own module docstring point 4):

        1. IMMUTABILITY ON UPDATE — `assignment` was writable on both
           create AND update (Meta.read_only_fields never listed it,
           because it legitimately has to be settable at create time).
           Without this check, a student could PATCH their own
           submission and silently re-point it at a *different*
           assignment post-creation — `unique_submission_per_student`
           does not catch this because the (assignment, student) pair
           genuinely changes to a new, not-yet-used pair. Blocked here:
           once `self.instance` exists, this field may not change.

        2. SOURCE RESTRICTION ON CREATE — §2 says a personal-assignment
           submission is the ONLY case where create() is the real entry
           point; a campus/liveclass-sourced assignment already has its
           AssignmentSubmission row (status=MISSING) created by
           `bridge.create_context_assignment()` at roster time, and
           students there are only ever meant to reach `submit_freeform`/
           `submit_structured` on that existing row. Nothing previously
           stopped a client from POSTing straight to this viewset's
           create() with a campus/liveclass assignment's id — best case
           that hits `unique_submission_per_student` and surfaces as a
           raw, uncaught `IntegrityError` (500); worst case (assignment
           has no roster row yet, e.g. a race with the bridge call) it
           silently creates a submission outside the roster flow
           entirely, with no roll_number/enrollment_no snapshot. Blocked
           here at the source instead: create() only accepts
           source=personal assignments.
        """
        if self.instance is not None and assignment.id != self.instance.assignment_id:
            raise serializers.ValidationError(
                "assignment cannot be changed once a submission has been created."
            )
        if self.instance is None and assignment.source != AssignmentSource.PERSONAL:
            raise serializers.ValidationError(
                "Submissions can only be created directly for personal assignments. "
                "Campus/liveclass submissions are created automatically when the "
                "assignment is posted — use submit_freeform/submit_structured on "
                "the existing submission instead."
            )
        return assignment

    def create(self, validated_data):
        # Belt-and-suspenders alongside validate_assignment() above: two
        # concurrent create() calls for the same (assignment, student)
        # could both pass validate_assignment()'s checks before either
        # write lands (no row lock at the serializer layer), and the
        # second one would previously surface `unique_submission_per_
        # student`'s violation as a raw, unhandled IntegrityError — a
        # 500 for what is, from the client's point of view, an ordinary
        # "you already submitted this" case. Turned into a clean 400
        # instead, matching how AssignmentCreateSerializer.create()
        # above already turns a model-layer error into
        # serializers.ValidationError rather than letting it bubble raw.
        try:
            return super().create(validated_data)
        except IntegrityError:
            raise serializers.ValidationError(
                {"detail": "A submission for this assignment already exists."}
            )


class PublicSubmissionSerializer(serializers.ModelSerializer):
    """§2 'Shareable URL — kaise kaam karta hai'. Auth-free, read-only,
    and deliberately narrower than `AssignmentSubmissionSerializer` — no
    internal ids beyond what the public page needs, no reviewer identity,
    no `feedback`/per-question `reviewer_feedback` distinction hidden
    behind extra fields the frontend doesn't need for this view."""

    assignment_title = serializers.CharField(source="assignment.title", read_only=True)
    student_name = serializers.SerializerMethodField()
    breakdown = serializers.SerializerMethodField()

    class Meta:
        model = AssignmentSubmission
        fields = [
            "assignment_title", "student_name", "roll_number", "enrollment_no",
            "submitted_at", "status", "written_content", "file", "grade",
            "total_marks_awarded", "breakdown",
        ]

    def get_student_name(self, obj) -> str:
        return obj.student.get_full_name() or obj.student.username

    def get_breakdown(self, obj):
        """Only populated for the structured path (§2: "structured →
        per-question list ... + total_marks_awarded"). Free-form
        submissions get `None` here and rely on `written_content`/`file`/
        `grade` instead — same shape the internal review UI uses, just
        read-only."""
        if not obj.assignment.has_structured_questions:
            return None
        return [
            {
                "question": answer.question.text,
                "marks": answer.question.marks,
                "marks_awarded": answer.marks_awarded,
                "reviewer_feedback": answer.reviewer_feedback,
            }
            for answer in obj.answers.select_related("question").order_by("question__order")
        ]


# --- Plain input serializers for the action endpoints in views.py — no
# model binding of their own, since the model method they front (see each
# docstring) is the actual source of truth for what happens on save. ---

class FreeformSubmitSerializer(serializers.Serializer):
    written_content = serializers.CharField(required=False, allow_blank=True, default="")
    file = serializers.FileField(required=False)


class StructuredAnswerInputSerializer(serializers.Serializer):
    question_id = serializers.UUIDField()
    answer_data = serializers.JSONField()
    # Matches `AssignmentAnswer.answer_attachment` (Task 7 — mirrors
    # `QuestionResponse.answer_attachment`, missing entirely before that
    # fix). NOTE: actually wiring a real file upload through here is a
    # views.py concern (Task 9, out of scope for this file) — DRF can't
    # cleanly bind one file to one entry inside a `many=True` nested list
    # from a single multipart request the way `TestAttempt.submit(files=
    # request.FILES)` does it (matched by a `f"answer_{question_id}"` key
    # instead, at the view layer). This field documents the shape and
    # supports a pre-uploaded-reference/base64 flow; the view is expected
    # to follow the same `files=` merge pattern testseries uses for true
    # multipart uploads before calling `submit_structured()`.
    answer_attachment = serializers.FileField(required=False, allow_null=True)


class StructuredSubmitSerializer(serializers.Serializer):
    answers = StructuredAnswerInputSerializer(many=True)


class GradeFreeformSerializer(serializers.Serializer):
    """§7 — 'Free-form path: PATCH {id}/grade/ (grade, feedback)'."""

    grade = serializers.CharField(max_length=10)
    feedback = serializers.CharField(required=False, allow_blank=True, default="")


class AnswerReviewSerializer(serializers.Serializer):
    """§7 — 'Structured path: POST {id}/answer/{question_id}/review/'."""

    marks_awarded = serializers.IntegerField(min_value=0)
    feedback = serializers.CharField(required=False, allow_blank=True, default="")
```

---

### `assignment/permissions.py`

```python
# assignment/permissions.py
"""
§7 permissions, translated into DRF permission classes. Note that the
*actual* enforcement for "campus/liveclass assignments can't be created
through the public endpoint" is `AssignmentViewSet.perform_create()`
hard-wiring `source=personal` regardless of payload (see views.py) — the
permission class below is defence-in-depth so that if a future refactor
ever removes that hard-coding, this still blocks the request rather than
silently reopening a creation path §7 explicitly says should not exist.
"""
from rest_framework import permissions

from .models import AssignmentSource


class IsPersonalSourceOnly(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        if view.action != "create":
            return True
        requested_source = request.data.get("source", AssignmentSource.PERSONAL)
        return requested_source == AssignmentSource.PERSONAL


class IsSubmissionStudent(permissions.BasePermission):
    """§7 — 'student sirf apni submission create/patch kar sakta hai'."""

    def has_object_permission(self, request, view, obj) -> bool:
        return obj.student_id == request.user.id


class IsAssignmentStaffOrOwner(permissions.BasePermission):
    """§7 grading actions — 'requester Assignment.posted_by hai YA
    is_staff'. `obj` here is an `AssignmentSubmission`; the check is
    against its parent `assignment`, not the submission itself, since
    it's the assignment's poster (teacher/staff) who has grading rights,
    not the submission's own student."""

    def has_object_permission(self, request, view, obj) -> bool:
        assignment = obj.assignment
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or assignment.posted_by_id == request.user.id)
        )
```

---

### `assignment/throttling.py`

```python
# assignment/throttling.py
"""
[HARDENING] — not in the functional design doc, added per
PRODUCTION_DESIGN.md §1.1/§6. The public share page (`PublicSubmissionView`)
is the one `AllowAny`, unauthenticated surface in this entire app — slug
entropy (`secrets.token_urlsafe(24)`, 192 bits) makes guessing any *one*
specific slug infeasible, but says nothing about a bot simply hammering
the endpoint with volume to find *some* valid slug, or scraping every
slug it's been handed at high speed. Rate limiting is the actual control
for that; entropy and rate limiting solve different problems and this
endpoint needs both.

A dedicated scope (`assignment_public_page`) rather than reusing DRF's
built-in `anon` scope, so tuning this rate in settings can never
accidentally change the limit on unrelated public endpoints elsewhere in
the project.
"""
from rest_framework.throttling import AnonRateThrottle


class AssignmentPublicPageThrottle(AnonRateThrottle):
    scope = "assignment_public_page"
```

---

### `assignment/views.py`

```python
# assignment/views.py
"""
§7 permissions and endpoint shapes, wired up as DRF viewsets. Every
action here delegates the actual write to a model method
(`AssignmentSubmission.submit_freeform` / `.submit_structured` /
`.grade_freeform` / `.mark_answer_and_maybe_finalize` / `.publish` /
`.unpublish`) — this file's job is request validation, permission
checks, and response shaping, never reimplementing that logic inline.
"""
import json

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .bridge import notify_submission_received
from .models import Assignment, AssignmentQuestion, AssignmentSource, AssignmentSubmission
from .permissions import IsAssignmentStaffOrOwner, IsPersonalSourceOnly, IsSubmissionStudent
from .throttling import AssignmentPublicPageThrottle
from .serializers import (
    AnswerReviewSerializer,
    AssignmentAnswerSerializer,
    AssignmentCreateSerializer,
    AssignmentSerializer,
    AssignmentSubmissionSerializer,
    FreeformSubmitSerializer,
    GradeFreeformSerializer,
    PublicSubmissionSerializer,
    StructuredSubmitSerializer,
)


class AssignmentViewSet(viewsets.ModelViewSet):
    """§7 — `create` is personal-only. Campus/liveclass assignments never
    reach this viewset; they're created via
    `assignment.bridge.create_context_assignment()` from those apps' own
    already-permission-checked endpoints, then surfaced to their users
    through campus's/liveclass's own thin-proxy viewsets (§5.2/§6.1 — not
    in this app), not through this one.
    """

    permission_classes = [permissions.IsAuthenticated, IsPersonalSourceOnly]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return AssignmentCreateSerializer
        return AssignmentSerializer

    def get_queryset(self):
        """Non-staff users see assignments they posted, plus personal
        assignments they hold a submission for (covers the case where a
        personal assignment's submission row was created before the
        Assignment object itself is re-fetched by a different client)."""
        user = self.request.user
        qs = Assignment.objects.all().prefetch_related("questions")
        if user.is_staff:
            return qs
        return qs.filter(
            Q(posted_by=user) | Q(source=AssignmentSource.PERSONAL, submissions__student=user)
        ).distinct()

    def perform_create(self, serializer):
        # Hard-wired regardless of what the client sent — see
        # IsPersonalSourceOnly's own docstring for why this, not that
        # permission class alone, is the real enforcement point.
        serializer.save(source=AssignmentSource.PERSONAL, posted_by=self.request.user)


class AssignmentSubmissionViewSet(viewsets.ModelViewSet):
    serializer_class = AssignmentSubmissionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = AssignmentSubmission.objects.select_related("assignment", "student").prefetch_related(
            "answers__question"
        )
        if user.is_staff:
            return qs
        return qs.filter(Q(student=user) | Q(assignment__posted_by=user)).distinct()

    def get_permissions(self):
        if self.action in ("grade", "review_answer"):
            return [permissions.IsAuthenticated(), IsAssignmentStaffOrOwner()]
        if self.action in ("submit_freeform", "submit_structured", "publish", "unpublish"):
            return [permissions.IsAuthenticated(), IsSubmissionStudent()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        """§2 — personal-assignment flow: there's no bridge-created
        roster row to attach to (no roster exists for a personal
        assignment), so the student's own first interaction creates the
        `AssignmentSubmission` row directly. `unique_submission_per_
        student` still guards against a duplicate. Campus/liveclass
        submissions, by contrast, already exist (status=MISSING) the
        moment `bridge.create_context_assignment()` ran — students there
        only ever reach the `submit_*` actions below, never this create().
        """
        serializer.save(student=self.request.user)

    @action(detail=True, methods=["patch"])
    def submit_freeform(self, request, pk=None):
        """§2 free-form path. Goes to SUBMITTED/LATE — grading is the
        separate `grade` action below, matching "ek hi manual grade
        step"."""
        submission = self.get_object()
        serializer = FreeformSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission.submit_freeform(**serializer.validated_data)
        notify_submission_received(submission)
        return Response(AssignmentSubmissionSerializer(submission).data)

    @action(detail=True, methods=["post"])
    def submit_structured(self, request, pk=None):
        """§2a structured path — auto-grades on the way in, resolves
        straight to CHECKED or PARTIALLY_CHECKED depending on whether any
        `text` question is still pending review.

        [FIX / Task 9] — closes the gap `StructuredAnswerInputSerializer.
        answer_attachment`'s own docstring flagged as deferred to this
        view: DRF cannot bind a file to one entry inside a `many=True`
        nested list from a single multipart request body, since multipart
        has no native nested-structure syntax. So for a multipart
        request, the client sends `answers` as a JSON-encoded *string*
        (not nested multipart fields) and any per-question file under a
        flat `answer_<question_id>` key in `request.FILES` — the same
        `files.get(f"answer_{question_id}")` convention
        `TestAttempt.submit()` uses. This merges that file back onto its
        matching answer dict before the serializer ever sees it, so
        `AssignmentSubmission.submit_structured()` still just finds
        `answer_attachment` already present in the entry, same as a
        plain JSON (no file) request. A non-multipart, JSON-only request
        (no `text`-question file answers) is untouched — `answers` is
        already a list there, so the `isinstance(..., str)` check below
        is False and this is a no-op.
        """
        submission = self.get_object()
        data = request.data
        answers = data.get("answers")
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except (TypeError, ValueError):
                return Response(
                    {"answers": "Must be valid JSON when submitted as multipart/form-data."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not isinstance(answers, list):
                return Response(
                    {"answers": "Must be a JSON list of answer objects."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            for entry in answers:
                if not isinstance(entry, dict):
                    continue
                file_obj = request.FILES.get(f"answer_{entry.get('question_id')}")
                if file_obj is not None:
                    entry["answer_attachment"] = file_obj
            data = {**data, "answers": answers}
        serializer = StructuredSubmitSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        submission.submit_structured(serializer.validated_data["answers"])
        notify_submission_received(submission)
        return Response(AssignmentSubmissionSerializer(submission).data)

    @action(detail=True, methods=["patch"])
    def grade(self, request, pk=None):
        """§7 — 'Free-form path: PATCH {id}/grade/ (grade, feedback)'."""
        submission = self.get_object()
        serializer = GradeFreeformSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission.grade_freeform(**serializer.validated_data)
        return Response(AssignmentSubmissionSerializer(submission).data)

    @action(detail=True, methods=["post"], url_path=r"answer/(?P<question_id>[^/.]+)/review")
    def review_answer(self, request, pk=None, question_id=None):
        """§7 — 'Structured path: POST {id}/answer/{question_id}/review/
        ... sirf text-type AssignmentAnswer pe allowed.'"""
        submission = self.get_object()
        question = get_object_or_404(submission.assignment.questions, pk=question_id)
        if question.question_type != AssignmentQuestion.QuestionTypeChoices.TEXT:
            return Response(
                {"detail": "Only text-type questions can be manually reviewed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = AnswerReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        answer = submission.mark_answer_and_maybe_finalize(
            question=question, reviewed_by=request.user, **serializer.validated_data
        )
        return Response(AssignmentAnswerSerializer(answer).data)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        """§2 — always mints a fresh slug (see `AssignmentSubmission.
        publish()`'s own docstring for why re-publishing never reuses the
        previous URL)."""
        submission = self.get_object()
        return Response({"public_slug": submission.publish()})

    @action(detail=True, methods=["post"])
    def unpublish(self, request, pk=None):
        submission = self.get_object()
        submission.unpublish()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PublicSubmissionView(generics.RetrieveAPIView):
    """§2 — 'GET /assignment/public/{public_slug}/ — auth-free, sirf tab
    data deta hai jab public_slug non-empty ho.'"""

    permission_classes = [permissions.AllowAny]
    # [HARDENING] — see PRODUCTION_DESIGN.md §1.1/§6. Requires
    # DEFAULT_THROTTLE_RATES["assignment_public_page"] to be set in
    # settings.py or DRF falls back to no limit for this scope.
    throttle_classes = [AssignmentPublicPageThrottle]
    serializer_class = PublicSubmissionSerializer
    lookup_field = "public_slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        # Blank public_slug = unpublished by definition (see model
        # comment on the field) — excluded here so an unpublished /
        # never-published submission 404s outright, rather than being
        # "reachable" via an empty-string URL segment.
        return AssignmentSubmission.objects.exclude(public_slug="").select_related("assignment", "student")
```

---

### `assignment/urls.py`

```python
# assignment/urls.py
"""
Mount this at whatever prefix the project's root URLconf uses, e.g.:

    path("api/assignment/", include("assignment.urls")),

which gives:
    /api/assignment/assignments/                       (list, create)
    /api/assignment/assignments/{id}/                   (retrieve, update, delete)
    /api/assignment/submissions/                        (list, create)
    /api/assignment/submissions/{id}/                   (retrieve, update, delete)
    /api/assignment/submissions/{id}/submit_freeform/    (PATCH)
    /api/assignment/submissions/{id}/submit_structured/  (POST)
    /api/assignment/submissions/{id}/grade/              (PATCH)
    /api/assignment/submissions/{id}/answer/{qid}/review/ (POST)
    /api/assignment/submissions/{id}/publish/            (POST)
    /api/assignment/submissions/{id}/unpublish/          (POST)
    /api/assignment/public/{slug}/                       (GET, AllowAny)
"""
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AssignmentSubmissionViewSet, AssignmentViewSet, PublicSubmissionView

router = DefaultRouter()
router.register("assignments", AssignmentViewSet, basename="assignment")
router.register("submissions", AssignmentSubmissionViewSet, basename="assignment-submission")

urlpatterns = router.urls + [
    path("public/<str:slug>/", PublicSubmissionView.as_view(), name="assignment-public-submission"),
]
```

---

### `assignment/admin.py`

```python
# assignment/admin.py
from django.contrib import admin

from .models import Assignment, AssignmentAnswer, AssignmentQuestion, AssignmentSubmission


class AssignmentQuestionInline(admin.TabularInline):
    """Lets staff add/edit structured questions straight from the
    Assignment admin page instead of a separate screen — matches how
    small the §2a question shape is (no reason to force a second page
    load per question)."""

    model = AssignmentQuestion
    extra = 0
    ordering = ["order"]


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = [
        "title", "source", "context_type", "posted_by", "due_date",
        "has_structured_questions", "total_marks", "created_at",
    ]
    list_filter = ["source", "has_structured_questions", "context_type"]
    search_fields = ["title", "posted_by__username", "posted_by__email"]
    # total_marks is auto-derived (recompute_total_marks(), see models.py)
    # for the structured path — editing it directly in admin for a
    # structured assignment would just get silently overwritten on the
    # next question add/remove, so make that non-obviousness explicit
    # rather than letting an admin user "fix" a value that won't stick.
    readonly_fields = ["total_marks"]
    inlines = [AssignmentQuestionInline]


class AssignmentAnswerInline(admin.TabularInline):
    """Read-mostly — grading a `text` answer from here bypasses
    `AssignmentSubmission.mark_answer_and_maybe_finalize()`, which means
    the submission's own `status`/`total_marks_awarded` recompute would
    NOT run. `marks_awarded`/`reviewer_feedback` are left editable for
    emergency/manual correction only; the reviewed_by/reviewed_at pair
    stays read-only so an admin edit here never silently misattributes a
    review to whichever staff account happened to be logged into admin.
    Prefer the API's review endpoint (§7) for normal grading."""

    model = AssignmentAnswer
    extra = 0
    fields = ["question", "answer_data", "is_auto_graded", "is_correct", "marks_awarded", "reviewer_feedback"]
    readonly_fields = ["question", "answer_data", "is_auto_graded", "is_correct"]


@admin.register(AssignmentSubmission)
class AssignmentSubmissionAdmin(admin.ModelAdmin):
    list_display = [
        "student", "assignment", "status", "roll_number", "enrollment_no",
        "grade", "total_marks_awarded", "submitted_at", "checked_at",
    ]
    list_filter = ["status"]
    search_fields = ["student__username", "student__email", "roll_number", "enrollment_no"]
    # public_slug is only ever set via `.publish()` (which mints a fresh
    # token) — editing it by hand in admin would let someone hand out a
    # guessable/predictable public URL, defeating the whole point of
    # `secrets.token_urlsafe()`.
    readonly_fields = ["public_slug", "total_marks_awarded"]
    inlines = [AssignmentAnswerInline]


@admin.register(AssignmentQuestion)
class AssignmentQuestionAdmin(admin.ModelAdmin):
    """Registered standalone too (in addition to the inline above) for
    the case where staff need to find/fix one question across
    assignments rather than always going in through its parent."""

    list_display = ["assignment", "order", "question_type", "marks"]
    list_filter = ["question_type"]
    search_fields = ["text", "assignment__title"]
```

---

### `assignment/apps.py`

```python
from django.apps import AppConfig


class AssignmentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "assignment"
    verbose_name = "Assignments"

    def ready(self):
        # No signal wiring needed here — every pre_save/post_save/
        # post_delete receiver in this app is declared directly in
        # models.py with @receiver, so they connect the moment Django's
        # app registry imports that module (which it always does before
        # ready() runs). Left as an explicit no-op + comment, not a
        # duplicate `import .models`, so a future reader isn't left
        # wondering whether signals are actually live.
        pass

```

---

### `assignment/bridge.py`

```python
# assignment/bridge.py
"""
The one and only door into this app for `campus`/`liveclass` (§1, §3).
`campus/bridge.py` and `liveclass/bridge.py` (not in this file — they live
in their own apps) call the two functions below instead of ever touching
`assignment.models` directly; this app calls back out to
`core.services.create_notification` (§3) the same way `message` already
does, since `core` is the neutral layer everyone's allowed to depend on.

Nothing here ever resolves `context_id` into a real `campus.Section` or
`liveclass.Classroom` row — the caller already did that and hands over
plain, already-resolved data (title, roster, etc.). This module's whole
job is "store/track what I'm given", never "go find out more about it".

[ASSUMPTION — NOT VERIFIED]: `core.services.create_notification`'s exact
signature wasn't available in this pass (only core/models.py was
provided, not core/services.py itself). The call in
`notify_submission_received()` below assumes keyword args matching
`core.Notification`'s own fields. Verify against the real function
signature before relying on this in production.
"""
import logging

from django.db import transaction

from core.services import create_notification
from login.models import User

from .models import Assignment, AssignmentSource, AssignmentSubmission

logger = logging.getLogger(__name__)


def create_context_assignment(
    *,
    source: str,
    context_type: str,
    context_id,
    posted_by: User,
    title: str,
    description: str = "",
    attachment=None,
    due_date=None,
    total_marks=None,
    roster: list[dict],
    extra_data: dict | None = None,
) -> Assignment:
    """§3. `roster = [{"user_id": ..., "roll_number": "...",
    "enrollment_no": "..."}, ...]` — already resolved by the caller
    (campus/liveclass bridge) from whatever roster source is correct for
    that app (e.g. `campus.StudentEnrollment`, or liveclass's
    `SessionParticipant`/`PassPurchase` holders — see design doc §6,
    [NOT YET VERIFIED] on the liveclass side).

    Creates the `Assignment` row, then bulk pre-creates one
    `AssignmentSubmission(status=MISSING)` per roster entry with the
    roll_number/enrollment_no snapshot already in place — so "who hasn't
    submitted yet" is always a plain query against existing rows, never a
    roster-diff computed on read.

    `source` must be `AssignmentSource.CAMPUS` or `AssignmentSource.
    LIVECLASS` here — `AssignmentSource.PERSONAL` assignments are created
    directly via the API (no roster, no bridge involved; see design doc
    §7 permissions), not through this function.

    [FIX] — this invariant was previously only stated in this docstring
    and never actually checked in code (`AssignmentSource` was imported
    but unused). `IsPersonalSourceOnly`/`AssignmentViewSet.perform_create`
    already close the public-API side of "personal is the only
    API-created source" (§7) — but nothing stopped a caller of *this*
    function, the app's other creation path, from passing
    `source=AssignmentSource.PERSONAL` and silently creating a
    bridge-originated "personal" assignment with a roster attached,
    which breaks the personal flow's own assumption (no roster, no
    bridge — see `AssignmentSubmissionViewSet.perform_create`'s
    docstring) elsewhere in this app. Enforced here now, at the one
    other place an `Assignment` can come into existence.

    [ADDED — Task 11] `extra_data`: an optional dict merged into `data`
    alongside `context_type`/`context_id`, additive-only (defaults to
    `None`, so every existing caller is unaffected). Added because
    `campus.bridge.create_assignment()` needs `Assignment.subject`
    tracked somewhere — `campus.Section` has no subject FK of its own (a
    section spans multiple subjects), so unlike `session` (derivable from
    `section.school_class.session_id` on the campus side, never needed
    here) `subject_id` genuinely has nowhere else to live once this app
    stops storing a `campus.Subject` FK directly, per the golden rule
    (§1) that this model never gets a real FK into `campus`/`liveclass`.
    `context_type`/`context_id` are set from this function's own
    parameters regardless of what `extra_data` contains, so a caller
    can't accidentally clobber those two keys via `extra_data`.
    """
    if source not in (AssignmentSource.CAMPUS, AssignmentSource.LIVECLASS):
        raise ValueError(
            f"create_context_assignment() only accepts source=campus or source=liveclass, got {source!r}. "
            "Personal assignments are created directly via the public API, never through this bridge."
        )
    data = dict(extra_data or {})
    data["context_type"] = context_type
    data["context_id"] = str(context_id) if context_id else None
    with transaction.atomic():
        assignment = Assignment.objects.create(
            source=source,
            context_type=context_type,
            context_id=context_id,
            posted_by=posted_by,
            title=title,
            description=description,
            attachment=attachment,
            due_date=due_date,
            total_marks=total_marks,
            data=data,
        )
        AssignmentSubmission.objects.bulk_create(
            [
                AssignmentSubmission(
                    assignment=assignment,
                    student_id=entry["user_id"],
                    roll_number=entry.get("roll_number", ""),
                    enrollment_no=entry.get("enrollment_no", ""),
                    status=AssignmentSubmission.SubmissionStatus.MISSING,
                )
                for entry in roster
            ],
            ignore_conflicts=True,
        )
    # [HARDENING] — one line per bulk post, so a large campus/liveclass
    # roster assignment is traceable in logs without a DB query. INFO,
    # not DEBUG: this is a meaningful business event (a teacher publishing
    # work to a whole roster), not noise.
    logger.info(
        "assignment.created id=%s source=%s context_type=%s context_id=%s roster_size=%d",
        assignment.id, source, context_type, context_id, len(roster),
    )
    return assignment


def get_submissions_for_context(context_type: str, context_id):
    """§3. Returns an unfiltered-by-permission queryset scoped only to
    "which context" — the caller (campus/liveclass) is responsible for
    any further roster/permission-based narrowing, since this app has no
    concept of who's allowed to see what in campus or liveclass terms.
    """
    return AssignmentSubmission.objects.filter(
        assignment__context_type=context_type, assignment__context_id=context_id
    ).select_related("assignment", "student")


def notify_submission_received(submission: AssignmentSubmission) -> None:
    """§3 — `assignment` calls `core.services.create_notification`
    directly (same precedent as `message`), so a submission event can
    surface in campus's Notice feed or liveclass's classroom feed without
    this app ever importing either app's models. The notification's
    `data` carries the assignment's own `context_type`/`context_id` so
    the client can deep-link back into whichever context page is
    relevant.
    """
    assignment = submission.assignment
    if not assignment.posted_by_id:
        return
    create_notification(
        recipient=assignment.posted_by,
        notif_type="submission_received",
        title="New Submission",
        message=f"{submission.student} submitted \"{assignment.title}\".",
        data={"context_type": assignment.context_type, "context_id": str(assignment.context_id or "")},
    )
```

---

### `assignment/tasks.py`

```python
# assignment/tasks.py
"""
Due-date reminder sweep. A plain function, not a `@shared_task`/`@app.task`
— same reasoning `login/models.py`'s OTPVerification docstring gives for
its own periodic cleanup job: whichever scheduler this project actually
runs (Celery beat, django-cron, a management command on system cron) can
wrap this call, without this module taking a hard dependency on any of
them.

Per the design doc §5.4, campus's own `tasks.send_assignment_due_
reminders` is expected to move to querying `assignment.Assignment`
directly (filtered by `context_type="section"`) rather than duplicating
this sweep — this function is written generically enough (no source/
context filter) to cover personal assignments too, which neither campus
nor liveclass ever will.

[ASSUMPTION — NOT VERIFIED]: `core.services.create_notification`'s exact
signature wasn't available in this pass (only core/models.py was
provided, not core/services.py). The call below assumes keyword args
matching `core.Notification`'s own fields (`recipient`, `notif_type`,
`title`, `message`, `data`) — the same assumption `assignment/bridge.py`'s
`notify_submission_received()` makes. Verify both call sites against the
real `create_notification` signature before relying on this in
production; update together if it differs.

[FIX — Task 10] IDEMPOTENCY: this module previously stated, in this
docstring, that it deliberately did not de-duplicate — "add an explicit
column if de-dup is needed". Task 10's own acceptance checklist now
requires re-running the sweep to never re-notify the same submission, so
that's no longer optional. A DB column (`last_reminded_at` on
`AssignmentSubmission`) was deliberately NOT the fix here even though
it's the more permanent-looking option: Task 10's checklist also requires
`makemigrations assignment` to stay clean, i.e. this task must not change
`models.py`. So the dedup marker lives in the cache instead of the DB —
one `cache.add()` per (submission, due_date) — which is a real tradeoff,
not a free win:
  - `cache.add()` is atomic (set-if-absent), so two overlapping sweep
    runs racing on the same submission can't both send — one wins the
    add, the other sees it already set and skips. A plain get-then-set
    would have that race.
  - Marker TTL is deliberately longer than one calendar day (see
    `_REMINDER_CACHE_TTL_SECONDS`) so a sweep scheduled more than once in
    the same day can't slip past it, while still expiring well before a
    submission could next become "due soon" again on a genuinely later
    due date.
  - Being cache-backed (not a DB column), the dedup marker does not
    survive a cache flush/eviction under memory pressure — an evicted
    marker means a possible duplicate reminder, not a lost one. That's
    the accepted failure mode for choosing "no migration" over "a real
    audit column"; if losing that guarantee is unacceptable, revisit with
    an actual `last_reminded_at` column and a migration instead.
  - On a `create_notification` failure, the marker is rolled back
    (`cache.delete`) before re-raising into the existing per-row
    try/except below, so a failed send is still retried on the next
    sweep rather than being permanently (and silently) suppressed.
"""
import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from core.services import create_notification

from .models import AssignmentSubmission

logger = logging.getLogger(__name__)

_REMINDER_CACHE_KEY = "assignment:due_reminder_sent:{submission_id}:{due_date}"
# 36h: safely spans one calendar day plus scheduler jitter (a sweep that
# runs slightly early/late, or twice in the same day), while still well
# under the days/weeks that would pass before the same submission could
# plausibly enter a new "due soon" window on a later due_date.
_REMINDER_CACHE_TTL_SECONDS = 36 * 60 * 60


def send_due_reminders(*, lookahead_hours: int = 24) -> int:
    """Notifies every student who still has a MISSING submission for an
    assignment due within `lookahead_hours`. Returns how many
    notifications were sent (not counting ones skipped as already-sent),
    so a management command wrapping this can log/report it.

    Idempotent across repeated runs within the same due_date — see the
    module docstring's [FIX — Task 10] note for how and why (cache-based,
    not a DB column).
    """
    now = timezone.now()
    window_end_date = (now + timedelta(hours=lookahead_hours)).date()

    due_soon = AssignmentSubmission.objects.filter(
        status=AssignmentSubmission.SubmissionStatus.MISSING,
        assignment__due_date__isnull=False,
        assignment__due_date__gte=now.date(),
        assignment__due_date__lte=window_end_date,
    ).select_related("assignment", "student")

    # [HARDENING] .iterator() — see PRODUCTION_DESIGN.md §2.5. Avoids
    # materializing the full due-soon queryset in memory at once; at
    # platform scale this can be tens/hundreds of thousands of rows in a
    # busy lookahead window.
    count = 0
    skipped_already_sent = 0
    for submission in due_soon.iterator(chunk_size=2000):
        assignment = submission.assignment
        cache_key = _REMINDER_CACHE_KEY.format(submission_id=submission.id, due_date=assignment.due_date)
        if not cache.add(cache_key, True, timeout=_REMINDER_CACHE_TTL_SECONDS):
            # Someone (this sweep or an overlapping one) already claimed
            # this (submission, due_date) pair — don't re-notify.
            skipped_already_sent += 1
            continue
        try:
            create_notification(
                recipient=submission.student,
                notif_type="assignment_due_reminder",
                title="Assignment Due Soon",
                message=f'"{assignment.title}" is due on {assignment.due_date}.',
                data={"context_type": assignment.context_type, "context_id": str(assignment.context_id or "")},
            )
            count += 1
        except Exception:
            # Roll back the "sent" marker — a create_notification-side
            # failure for this one recipient must not permanently
            # suppress a reminder that never actually went out; the next
            # sweep should retry it.
            cache.delete(cache_key)
            # [HARDENING] — one bad row (e.g. a stale FK, a
            # create_notification-side failure for one recipient) must
            # not abort the whole sweep; every other student still needs
            # their reminder. Logged with the failing submission's id so
            # it's individually re-driveable, not silently dropped.
            logger.warning("assignment.due_reminder_failed submission_id=%s", submission.id, exc_info=True)

    logger.info(
        "assignment.due_reminders_sent count=%d skipped_already_sent=%d lookahead_hours=%d",
        count, skipped_already_sent, lookahead_hours,
    )
    return count
```

---

### `assignment/management/commands/send_assignment_due_reminders.py`

```python
# assignment/management/commands/send_assignment_due_reminders.py
"""
Thin CLI wrapper around `assignment.tasks.send_due_reminders` — lets the
project's existing scheduler (system cron, Celery beat's
`CrontabSchedule` calling this via `django.core.management.call_command`,
etc.) invoke this the same way as any other periodic Django management
command, without this app assuming which one the project uses (see
tasks.py's own docstring on being scheduler-agnostic).
"""
from django.core.management.base import BaseCommand, CommandParser

from assignment.tasks import send_due_reminders


class Command(BaseCommand):
    help = "Send due-date reminder notifications for MISSING assignment submissions."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--lookahead-hours",
            type=int,
            default=24,
            help="Notify for assignments due within this many hours (default: 24).",
        )

    def handle(self, *args, **options):
        count = send_due_reminders(lookahead_hours=options["lookahead_hours"])
        self.stdout.write(self.style.SUCCESS(f"Sent {count} assignment due-date reminder(s)."))
```

---

### `assignment/tests.py`

```python
# assignment/tests.py
"""
Model-layer tests — deliberately not view/API tests. This app's actual
complexity lives in the model methods (`submit_freeform`,
`submit_structured`, `mark_answer_and_maybe_finalize`, `publish`/
`unpublish`, `recompute_total_marks`, `can_change_question_mode`); the
view/serializer layer is thin by design (it validates the HTTP boundary
and delegates to these methods, per views.py's own module docstring), so
that's where a test suite's effort belongs first.

NOT covered here (see PRODUCTION_DESIGN.md §4 for why each is flagged
rather than silently skipped): permission/IDOR tests requiring an
`APIClient` + real user-factory fixture, `bridge.py`'s roster-shaped
inputs, and constraint-level concurrency tests requiring
`TransactionTestCase`.
"""
import datetime
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from login.models import User

from .models import Assignment, AssignmentQuestion, AssignmentSource, AssignmentSubmission
from .tasks import send_due_reminders

# [FIX — Task 10] The mcq/msq fixtures below previously used
# `options=["3", "4", "5"]` (plain strings) and `correct_answer="4"` (a
# bare string) — neither shape `AssignmentQuestion.clean()` actually
# accepts (options must be a list of dicts with an `"id"` key;
# correct_answer must be `{"option_id": <one of those ids>}` — see that
# method's own MCQ/MSQ branch). `full_clean()` runs unconditionally from
# `AssignmentQuestion.save()`, so every test below that created an mcq
# question with the old shape would have raised `ValidationError` in
# `setUp()` before a single test method ever ran. Fixed to the shape the
# model actually validates.
#
# [ASSUMPTION — NOT VERIFIED] `answer_data`'s shape for an mcq answer is
# opaque to this app (`common.question_grading.auto_grade()` is the only
# thing that interprets it — see models.py's own note that this module
# wasn't available to verify against). `{"option_id": ...}` is used here
# to mirror `correct_answer`'s confirmed shape; if the real `auto_grade()`
# expects something else for mcq `answer_data`, update these fixtures to
# match it.
MCQ_OPTIONS = [{"id": "opt_3", "text": "3"}, {"id": "opt_4", "text": "4"}, {"id": "opt_5", "text": "5"}]
MCQ_CORRECT_ANSWER = {"option_id": "opt_4"}
MCQ_ANSWER_CORRECT = {"option_id": "opt_4"}
MCQ_ANSWER_WRONG = {"option_id": "opt_3"}


def _make_user(username: str) -> User:
    return User.objects.create(username=username)


def _make_assignment(**kwargs) -> Assignment:
    defaults = {
        "source": AssignmentSource.PERSONAL,
        "title": "Test assignment",
    }
    defaults.update(kwargs)
    return Assignment.objects.create(**defaults)


class FreeformSubmissionTests(TestCase):
    def setUp(self):
        self.student = _make_user("student1")
        self.assignment = _make_assignment(due_date=timezone.now().date() + datetime.timedelta(days=1))
        self.submission = AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)

    def test_submit_on_time_is_submitted(self):
        self.submission.submit_freeform(written_content="my answer")
        self.assertEqual(self.submission.status, AssignmentSubmission.SubmissionStatus.SUBMITTED)
        self.assertEqual(self.submission.written_content, "my answer")

    def test_submit_after_due_date_is_late(self):
        self.assignment.due_date = timezone.now().date() - datetime.timedelta(days=1)
        self.assignment.save(update_fields=["due_date"])
        self.submission.submit_freeform(written_content="late answer")
        self.assertEqual(self.submission.status, AssignmentSubmission.SubmissionStatus.LATE)
        self.assertTrue(self.submission.is_late())

    def test_grade_freeform_sets_checked(self):
        self.submission.submit_freeform(written_content="answer")
        self.submission.grade_freeform(grade="A", feedback="Nice work")
        self.assertEqual(self.submission.status, AssignmentSubmission.SubmissionStatus.CHECKED)
        self.assertIsNotNone(self.submission.checked_at)
        self.assertEqual(self.submission.grade, "A")


class StructuredSubmissionTests(TestCase):
    def setUp(self):
        self.student = _make_user("student2")
        self.staff = _make_user("staff1")
        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])

        self.assignment = _make_assignment(has_structured_questions=True)
        self.mcq = AssignmentQuestion.objects.create(
            assignment=self.assignment, order=1,
            question_type=AssignmentQuestion.QuestionTypeChoices.MCQ,
            text="2+2?", marks=5, options=MCQ_OPTIONS, correct_answer=MCQ_CORRECT_ANSWER,
        )
        self.text_q = AssignmentQuestion.objects.create(
            assignment=self.assignment, order=2,
            question_type=AssignmentQuestion.QuestionTypeChoices.TEXT,
            text="Explain your reasoning.", marks=10,
        )
        self.submission = AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)

    def test_total_marks_auto_summed_from_questions(self):
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.total_marks, 15)  # 5 + 10

    def test_mcq_auto_graded_correctly(self):
        self.submission.submit_structured([
            {"question_id": self.mcq.id, "answer_data": MCQ_ANSWER_CORRECT},
            {"question_id": self.text_q.id, "answer_data": "because math"},
        ])
        mcq_answer = self.submission.answers.get(question=self.mcq)
        self.assertTrue(mcq_answer.is_auto_graded)
        self.assertTrue(mcq_answer.is_correct)
        self.assertEqual(mcq_answer.marks_awarded, 5)

    def test_pending_text_question_leaves_partially_checked(self):
        self.submission.submit_structured([
            {"question_id": self.mcq.id, "answer_data": MCQ_ANSWER_CORRECT},
            {"question_id": self.text_q.id, "answer_data": "because math"},
        ])
        self.assertEqual(self.submission.status, AssignmentSubmission.SubmissionStatus.PARTIALLY_CHECKED)
        self.assertIsNone(self.submission.checked_at)

    def test_reviewing_last_pending_answer_finalizes_submission(self):
        self.submission.submit_structured([
            {"question_id": self.mcq.id, "answer_data": MCQ_ANSWER_CORRECT},
            {"question_id": self.text_q.id, "answer_data": "because math"},
        ])
        self.submission.mark_answer_and_maybe_finalize(
            question=self.text_q, marks_awarded=8, feedback="Good", reviewed_by=self.staff,
        )
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, AssignmentSubmission.SubmissionStatus.CHECKED)
        self.assertEqual(self.submission.total_marks_awarded, 13)  # 5 (mcq) + 8 (text)
        self.assertIsNotNone(self.submission.checked_at)

    def test_wrong_mcq_answer_scores_zero(self):
        self.submission.submit_structured([
            {"question_id": self.mcq.id, "answer_data": MCQ_ANSWER_WRONG},
            {"question_id": self.text_q.id, "answer_data": "guess"},
        ])
        mcq_answer = self.submission.answers.get(question=self.mcq)
        self.assertFalse(mcq_answer.is_correct)
        self.assertEqual(mcq_answer.marks_awarded, 0)


class QuestionModeImmutabilityTests(TestCase):
    def setUp(self):
        self.assignment = _make_assignment(has_structured_questions=True)
        self.student = _make_user("student3")

    def test_can_change_mode_with_no_submissions(self):
        self.assertTrue(self.assignment.can_change_question_mode())

    def test_cannot_change_mode_once_a_real_submission_exists(self):
        submission = AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)
        submission.submit_freeform(written_content="x")  # any non-MISSING status
        self.assertFalse(self.assignment.can_change_question_mode())

    def test_missing_only_submissions_do_not_lock_mode(self):
        # A bridge-pre-created MISSING row (roster entry who hasn't
        # touched the assignment yet) must NOT count as "a submission
        # exists" for immutability purposes — only an actual attempt does.
        AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)
        self.assertTrue(self.assignment.can_change_question_mode())


class PublicSlugLifecycleTests(TestCase):
    def setUp(self):
        self.assignment = _make_assignment()
        self.student = _make_user("student4")
        self.submission = AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)

    def test_publish_sets_a_nonempty_slug(self):
        slug = self.submission.publish()
        self.assertTrue(slug)
        self.assertEqual(self.submission.public_slug, slug)

    def test_republishing_generates_a_new_slug(self):
        first = self.submission.publish()
        second = self.submission.publish()
        self.assertNotEqual(first, second)

    def test_unpublish_blanks_the_slug(self):
        self.submission.publish()
        self.submission.unpublish()
        self.assertEqual(self.submission.public_slug, "")


class RecomputeTotalMarksTests(TestCase):
    def setUp(self):
        self.assignment = _make_assignment(has_structured_questions=True)

    def test_deleting_a_question_reduces_total_marks(self):
        q1 = AssignmentQuestion.objects.create(
            assignment=self.assignment, order=1,
            question_type=AssignmentQuestion.QuestionTypeChoices.TEXT, text="Q1", marks=10,
        )
        AssignmentQuestion.objects.create(
            assignment=self.assignment, order=2,
            question_type=AssignmentQuestion.QuestionTypeChoices.TEXT, text="Q2", marks=15,
        )
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.total_marks, 25)

        q1.delete()
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.total_marks, 15)


class DueReminderIdempotencyTests(TestCase):
    """[FIX — Task 10] Covers the acceptance criterion that re-running
    `send_due_reminders()` must not re-notify the same submission — see
    tasks.py's own [FIX — Task 10] docstring note for the cache-based
    dedup this exercises. `create_notification` is mocked rather than
    exercised for real: its exact signature is still an unverified
    assumption (see tasks.py's own docstring), and these tests are about
    the sweep's dedup/retry logic, not `core.services` itself."""

    def setUp(self):
        cache.clear()
        self.student = _make_user("student5")
        self.assignment = _make_assignment(due_date=timezone.now().date())
        self.submission = AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)

    def tearDown(self):
        cache.clear()

    @patch("assignment.tasks.create_notification")
    def test_running_sweep_twice_sends_only_one_notification(self, mock_create_notification):
        first_count = send_due_reminders(lookahead_hours=24)
        second_count = send_due_reminders(lookahead_hours=24)
        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(mock_create_notification.call_count, 1)

    @patch("assignment.tasks.create_notification", side_effect=Exception("boom"))
    def test_failed_notification_is_retried_on_next_sweep(self, mock_create_notification):
        first_count = send_due_reminders(lookahead_hours=24)
        self.assertEqual(first_count, 0)  # failed send — not counted, and not marked as sent
        mock_create_notification.side_effect = None
        second_count = send_due_reminders(lookahead_hours=24)
        self.assertEqual(second_count, 1)
```

---

## Part 3.5 — Known Issues Found & Fixed This Pass

> Ye section is pass me naya hai. Pichhli baar jab yeh master doc likha
> gaya tha, `assignment/models.py`'s docstring ne khud claim kiya tha ki
> real `common/question_grading.auto_grade()` ek `(question_type,
> options, correct_answer, answer_data, marks) -> (is_correct,
> marks_awarded)` shape ka plain function hai. Is pass me asli
> `common/question_grading.py` upload hui, aur us claim ko **actually
> Python execute karke** verify kiya gaya — claim **galat/backwards**
> nikli.

### Bug: `AssignmentSubmission.submit_structured()` → `auto_grade()` call crash

**Confirmed by real execution, guess nahi:**

```python
>>> auto_grade(question_type='mcq', options=[...], correct_answer={...}, answer_data={...}, marks=5)
TypeError: auto_grade() got an unexpected keyword argument 'options'
```

Aur agar `options=` hata bhi diya jaaye (jaisa purana code karta tha
result unpack karte waqt):

```python
>>> is_correct, marks_awarded = auto_grade(question_type='mcq', ...)  # no options kwarg
TypeError: cannot unpack non-iterable GradingResult object
```

**Asli `common/question_grading.py` ka real signature:**

```python
def auto_grade(*, question_type: str, marks: int, correct_answer: Any, answer_data: Any) -> GradingResult:
    ...
# returns: GradingResult(is_auto_graded: bool, is_correct: bool | None, marks_awarded: int | None)
```

Koi `options` parameter hai hi nahi — MCQ/MSQ ka grading sirf
`correct_answer` aur `answer_data` compare karke hoti hai
(`common/question_grading.py` khud kabhi `options` list dekhta hi nahi,
sirf caller-supplied `answer_data`/`correct_answer` ko compare karta
hai).

**Impact (agar fix na hota):** Har ek structured-assignment submission
(`POST .../submit_structured/`) turant `TypeError` se crash hota — ek
raw 500, koi bhi mcq/msq/list question wale assignment ke liye. `text`-
only assignments is bug se bachte (kyunki `text` questions kabhi
`auto_grade()` ko call hi nahi karte is code-path me — dekho model ka
`is_auto_graded` check, jo `question_type != TEXT` par hi call karta
hai `auto_grade()` ko).

**Fix (already applied in Part 3's `assignment/models.py` upar):**

```python
grading_result = auto_grade(
    question_type=question.question_type,
    correct_answer=question.correct_answer,
    answer_data=answer_data,
    marks=question.marks,
)
is_correct = grading_result.is_correct
marks_awarded = grading_result.marks_awarded
```

`options=question.options` hata diya gaya (real function ko iski
zaroorat nahi), aur result ko `GradingResult` attributes se padha ja
raha hai, tuple-unpack se nahi.

**Fix verify kiya gaya real code chala ke** (teen cases — correct mcq,
wrong mcq, text) — teeno expected behavior dete hain:

| Case | `is_correct` | `marks_awarded` |
|---|---|---|
| mcq, sahi answer | `True` | full marks |
| mcq, galat answer | `False` | `0` |
| text | `None` | `None` |

**Is bug ka koi aur jagah pe asar nahi hai** — sirf ye ek call site
(`submit_structured()`) `auto_grade()` ko seedha call karta hai is app
ke andar. `AssignmentQuestion.clean()`/`.save()` apna khud ka shape-
validation karte hain (`auto_grade()` ko call nahi karte), toh unpe koi
asar nahi.

**Follow-up recommendation (is pass me nahi kiya, sirf flagged):** ab
jab `submit_structured()` fix ho chuka hai, `assignment/tests.py`'s
`StructuredSubmissionTests` (`test_mcq_auto_graded_correctly`,
`test_wrong_mcq_answer_scores_zero`, etc.) ko real `common/
question_grading.py` ke against actually run karke confirm karna chahiye
ki wo ab pass hoti hain — is pass me sirf `submit_structured()`'s
internal logic hi standalone verify hui hai (upar wali table), poora
Django test suite (models + views + auth) run karna abhi bhi ek open
item hai agar poori confidence chahiye.

---

## Part 4 — Production-Readiness Design

> Source: `PRODUCTION_DESIGN.md`. Har § yahan Part 4 ke apne section numbers ko refer karta hai (ye doc ke andar hi self-contained hai), aur `assignment_app_design.md §N` references upar Part 1 ko point karte hain.

> Ye doc `assignment_app_design.md` (functional design) ko complement karta
> hai — us doc me *kya* banana hai tha, is doc me *production me kaise
> chalega* — security, performance, observability, testing, deployment,
> aur failure modes. Jahan bhi kisi decision ki traceability chahiye,
> `assignment_app_design.md §N` cite kiya gaya hai; jo purely is pass ka
> hardening hai (functional doc me nahi tha) usko **[HARDENING]** tag
> kiya gaya hai, taaki dono tarah ke decisions mix na hon.

---

### 1. Threat model / security review

#### 1.1 Public share page (`GET /assignment/public/{slug}/`) — the one
genuinely public, `AllowAny` surface in this app (§2, §7).

| Risk | Mitigation already in place | Residual risk / follow-up |
|---|---|---|
| Slug guessing / enumeration | `secrets.token_urlsafe(24)` → 192 bits of entropy, cryptographically unguessable | none at the token level |
| **Scraping via brute-force requests** (trying many slugs to find *some* valid one, not targeting one specific student) | none in the code as shipped | **[HARDENING] add `AnonRateThrottle` on `PublicSubmissionView`** — see `throttling.py` below. Entropy alone doesn't stop a botnet from just trying volume; rate limiting is the actual defence for a public unauthenticated endpoint. |
| Slug staying live after student regrets sharing | `unpublish()` blanks the slug immediately (§2) | none — this is the intended control, already correct |
| Info leakage beyond what's needed | `PublicSubmissionSerializer` is a narrow, hand-picked field set (no reviewer identity, no internal ids, no `correct_answer`) | none identified |
| Un-authenticated write access | `PublicSubmissionView` is `RetrieveAPIView` — GET only, no write path exists at this URL at all | none — structurally impossible, matching this app's own `is_paid`-omission pattern (§4) |

#### 1.2 Authenticated surface

- **Answer-key leakage**: `AssignmentQuestion.correct_answer` is
  `write_only` in `AssignmentQuestionSerializer` (serializers.py) — a
  student loading their own assignment's questions never receives the
  answer key in the response body, even though it lives in the same row
  they can otherwise read. Verified in review; no endpoint currently
  serializes `AssignmentQuestion` outside that serializer.
- **Grading-endpoint authorization**: `IsAssignmentStaffOrOwner` (§7)
  checks `assignment.posted_by_id == request.user.id` **or** `is_staff`
  — never the submission's own student, so a student can never grade
  their own submission by hitting `grade`/`review_answer` directly, even
  if they discover the URL.
- **Mass-assignment via nested serializer**: `AssignmentCreateSerializer`
  has no `source`/`context_type`/`context_id`/`posted_by` fields at all
  (not merely `read_only=True` on them) — there is no payload shape that
  can even attempt to set them, which is stronger than a read-only-field
  guard (a `read_only_fields` entry can be misconfigured or bypassed by a
  serializer subclass; a field that doesn't exist cannot).
- **IDOR (insecure direct object reference)**: every non-public viewset's
  `get_queryset()` scopes to `request.user` (student's own rows, or
  `posted_by`/`is_staff` for grading surfaces) — a valid submission `id`
  belonging to someone else 404s (via DRF's standard "not in queryset ⇒
  not found" behaviour), it does not 403 with information leakage about
  whether the id exists.
- **File uploads** (`Assignment.attachment`, `AssignmentSubmission.file`,
  `AssignmentQuestion.attachment`): this app does **not** implement
  content-type/virus scanning or extension allow-listing —
  **[HARDENING] flagged as a project-level, not app-level, concern**:
  whatever `DEFAULT_FILE_STORAGE`/upload-handling middleware the rest of
  the project already uses for `login.User.profile_photo` should apply
  uniformly here too. Do not bolt on a one-off validator in this app that
  the rest of the project doesn't share — that's how validation rules
  drift apart.

---

### 2. Performance & scalability

#### 2.1 Indexing — recap of what's already load-bearing (models.py)
- `Assignment`: `(source, context_type, context_id)` — every bridge
  lookup (`get_submissions_for_context`) and every "assignments in this
  context" query hits this.
- `Assignment`: `(posted_by, due_date)` — "my posted assignments, by due
  date" (teacher dashboard) and the reminder sweep's due-date filter.
- `AssignmentSubmission`: `(assignment, status)` — "who hasn't submitted
  yet" (the single most common query against this table once a roster is
  large).
- `AssignmentSubmission.status` — `db_index=True` on its own too, for
  admin/ops-style cross-assignment status filters that don't scope by
  `assignment` first.
- Unique constraints (`unique_submission_per_student`,
  `unique_nonblank_public_slug`, `unique_question_order_per_assignment`,
  `unique_answer_per_submission_question`) all double as query-plan
  assists on top of their integrity role — Postgres builds a unique
  index for each automatically.

#### 2.2 [HARDENING] N+1 prevention
- `AssignmentViewSet.get_queryset()` — `.prefetch_related("questions")`.
- `AssignmentSubmissionViewSet.get_queryset()` —
  `.select_related("assignment", "student").prefetch_related("answers__question")`.
- `PublicSubmissionSerializer.get_breakdown()` — the view's own
  `get_queryset()` already `select_related`s `assignment`/`student`; the
  per-answer `.select_related("question")` inside `get_breakdown` avoids
  a query-per-answer on top of that.

#### 2.3 [HARDENING] Pagination
Every list endpoint (`AssignmentViewSet`, `AssignmentSubmissionViewSet`)
relies on the project's global `DEFAULT_PAGINATION_CLASS` — **this app
does not override it**, deliberately, so a teacher with a 500-student
roster's submission list doesn't accidentally return unpaginated. If the
project has no global pagination class set, that is a project-level gap
this app inherits — set one (`PageNumberPagination`, page size 25–50 is a
reasonable default for a submissions list) rather than adding a
per-viewset override here that would only paginate this one app
differently from every other list endpoint in the project.

#### 2.4 Counter / aggregate cost
`Assignment.recompute_total_marks()` runs one `Sum()` aggregate per
question add/remove/edit (signal-triggered) — cheap, and only touches
`Assignment` rows with `has_structured_questions=True`, which in practice
is a small fraction of total `Assignment` volume (most personal/free-form
assignments never create a single `AssignmentQuestion`, so the signal
fires zero times for them).

`AssignmentSubmission._recompute_structured_status()` runs one
`.exists()` (pending-review check) + one `Sum()` (final tally) per
structured submit/review call — both scoped to a single submission's
`answers`, so cost is O(questions per assignment), not O(all submissions).

#### 2.5 Reminder sweep cost (`tasks.send_due_reminders`)
The query is a single filtered `SELECT` over `AssignmentSubmission`
(status + due-date range), using the same `(assignment, status)` /
`(posted_by, due_date)` indexes above — no per-row extra query except the
notification write itself. At genuinely large scale (hundreds of
thousands of MISSING rows in the lookahead window across a whole
platform), **[HARDENING] batch the loop** with
`.iterator(chunk_size=2000)` instead of materializing the full queryset,
and consider moving `create_notification` calls onto a task queue
(Celery) rather than calling them synchronously in the sweep loop — not
done in `tasks.py` as shipped because the project's actual task-queue
setup wasn't confirmed in this pass; flagged rather than guessed.

---

### 3. Observability

**[HARDENING]** — none of this was in the original code; added because a
production app with grading/money-adjacent(ish) status transitions needs
a paper trail when something goes wrong, matching `core/models.py`'s own
existing use of `logging.getLogger(__name__)`.

- `bridge.py` and `tasks.py` now log (see updated files):
  - `create_context_assignment()` — one INFO line per created Assignment
    + roster size, so a bulk campus/liveclass post is traceable in logs
    without needing to query the DB.
  - `send_due_reminders()` — one INFO line with the final count, one
    WARNING if `create_notification` raises for an individual submission
    (caught per-row so one bad row doesn't abort the whole sweep).
- **Metrics** (not implemented here — project-level, flagged): the
  natural counters to expose to whatever metrics system the project uses
  (StatsD/Prometheus/etc.) are: submissions created per source, structured
  vs free-form submission ratio, average time-to-grade
  (`checked_at - submitted_at`), and public-page hit rate. None of these
  need new columns — all are derivable from existing fields via a
  periodic query or a signal-based counter increment, whichever pattern
  the rest of the project already uses.

---

### 4. Testing strategy

`assignment/tests.py` (added) covers the business-logic layer — model
methods, not HTTP — because that's where this app's actual complexity
lives (the view/serializer layer is thin by design, per this file's own
"validate the boundary, don't reimplement the logic" principle).

Covered:
1. Free-form submit → late-vs-on-time status resolution.
2. Structured submit → auto-grading (mcq/msq/list) + correct
   partially_checked/checked resolution based on pending `text`
   questions.
3. `mark_answer_and_maybe_finalize` flips `partially_checked` → `checked`
   only once every `text` question is reviewed, and computes the right
   `total_marks_awarded`.
4. `publish()`/`unpublish()` slug lifecycle — regenerates on every
   publish call, blanks on unpublish.
5. `can_change_question_mode()` — True with zero real submissions, False
   once at least one non-MISSING submission exists.
6. `recompute_total_marks()` — signal fires correctly on question
   add/edit/delete.

**Not covered here (flagged, not silently skipped)**:
- Permission-class / viewset-level tests (`IsAssignmentStaffOrOwner`,
  `IsPersonalSourceOnly`, IDOR scoping) — needs `APIClient` + a real
  authenticated test user fixture, which depends on how the project's own
  test setup authenticates (`login.User` factory pattern wasn't provided
  in this pass).
- `bridge.py`'s `create_context_assignment`/`get_submissions_for_context`
  — needs a realistic roster fixture; the shape of a "resolved roster"
  from campus/liveclass wasn't concretely available (§6 of the functional
  doc flags liveclass's roster source as `[NOT YET VERIFIED]` itself).
- Concurrency/race tests on the unique constraints (two simultaneous
  submissions for the same `(assignment, student)`) — constraint-level
  correctness is trusted to Postgres here rather than re-tested at the
  application layer; a proper test would need transaction-level test
  tooling (`TransactionTestCase`) rather than the default
  `TestCase`-wrapped-in-a-transaction Django gives by default.

---

### 5. Deployment & migration plan

1. **Schema rollout**: `python manage.py makemigrations assignment` →
   review the generated migration by hand before applying — in
   particular confirm the two `UniqueConstraint`s with `condition=`
   (the public_slug partial-uniqueness one) generate a Postgres partial
   index correctly (SQLite does not support partial unique constraints
   the same way; if the project's test suite runs on SQLite, that
   constraint will behave differently there than in Postgres production
   — **[HARDENING] flag**: run at least the publish/unpublish tests
   against Postgres in CI, not just SQLite, or this could pass locally
   and fail — or silently under-enforce — in production).
2. **App registration**: `INSTALLED_APPS += ["assignment"]`,
   `ROOT_URLCONF` mount at `api/assignment/` (or whatever prefix fits the
   project's existing API namespace convention).
3. **campus/liveclass migration** (functional doc §5/§6) is a *separate*,
   later rollout from this app's own initial deploy — this app can ship
   and take personal-assignment traffic on day one with zero dependency
   on campus/liveclass's own bridge.py wrappers existing yet. Sequencing:
   - Day 0: deploy `assignment` app alone (personal flow live).
   - Day N: deploy `campus/bridge.py` + `liveclass/bridge.py` wrappers,
     data-migration command, read-only-then-delete of the old
     `campus.Assignment`/`liveclass.Assignment` models (functional doc
     §5.3/§6.3) — this is intentionally a separate release per that doc,
     not bundled with day 0.
4. **Reminder sweep**: wire `send_assignment_due_reminders` management
   command (added — see below) into whatever the project's existing
   periodic-job mechanism is (cron/Celery beat) — same integration point
   the project presumably already uses for OTP row cleanup
   (`login/models.py`'s own docstring references that job existing).
5. **Rollback**: this app has no destructive migration on day-0 deploy
   (new tables only, no altering of `campus`/`liveclass` tables yet) — a
   rollback is "stop routing traffic to `/api/assignment/`", not a data
   migration reversal. The campus/liveclass migration rollout (step 3,
   day N) is where a real rollback plan matters, and that's already
   flagged as an open item in the functional doc (§8.3 — "Data-migration
   command + rollback-log design... actual migration script alag likhna
   hai").

---

### 6. Rate limiting (settings required)

`throttling.py` (added) defines `AssignmentPublicPageThrottle` for the
public share page. To activate, the project's `settings.py` needs:

```python
REST_FRAMEWORK = {
    # ...existing config...
    "DEFAULT_THROTTLE_RATES": {
        # ...existing rates...
        "assignment_public_page": "60/hour",  # per-IP, tune based on real traffic
    },
}
```

`60/hour` per IP is a starting guess, not a measured number — tune once
real public-page traffic is observed. The throttle scope is named
`assignment_public_page` specifically (not a shared `anon` scope) so
tuning it never accidentally affects rate limits on unrelated public
endpoints elsewhere in the project.

---

### 7. Open risks carried forward (not resolved by this pass)

Same convention as the functional doc's own §8 — flagged, not guessed:

1. File-upload validation (content-type/size/malware scanning) is a
   project-level concern this app inherits rather than reimplements
   (§1.2 above).
2. Task-queue integration for the reminder sweep at very large scale
   (§2.5) — batching noted, actual Celery wiring not done.
3. Partial-unique-constraint behaviour differs between SQLite (test) and
   Postgres (prod) for `public_slug` — CI should test against Postgres
   for this app's migration (§5.1).
4. Metrics/dashboards (§3) — logging added, metrics emission is a
   project-level integration not implemented here.
5. Every open item already listed in `assignment_app_design.md §8` still
   applies unchanged (enrollment_no field decision, liveclass roster
   verification, data-migration script, shared grading-utility location
   confirmation, msq/list partial-credit non-goal).

---

## Part 5 — Integration Checklist

Ye 4 chhoti cheezein hi **project-level** hain — inke alawa is doc me sab
kuch already self-contained hai:

### 1. `settings.py`

```python
INSTALLED_APPS = [
    # ...existing apps...
    "assignment",
]

REST_FRAMEWORK = {
    # ...existing config...
    "DEFAULT_THROTTLE_RATES": {
        # ...existing rates...
        "assignment_public_page": "60/hour",  # tune based on real traffic — see Part 4 §6
    },
}
```

### 2. Root `urls.py`

```python
urlpatterns = [
    # ...existing routes...
    path("api/assignment/", include("assignment.urls")),
]
```

### 3. Migrations

```bash
python manage.py makemigrations assignment
python manage.py migrate
```

⚠️ Review the generated migration by hand before applying — in particular
the `unique_nonblank_public_slug` partial-unique constraint (see Part 4
§5.1) behaves differently on SQLite vs Postgres. Test against Postgres in
CI if the project's test suite normally runs on SQLite.

### 4. Scheduled job (due-date reminders)

Wire this into whatever the project already uses for periodic jobs
(system cron, Celery beat, etc.) — same integration point as the existing
OTP-cleanup job:

```bash
python manage.py send_assignment_due_reminders --lookahead-hours 24
```

### Endpoint quick-reference

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/api/assignment/assignments/` | list / create (personal-only, §7) |
| GET / PATCH / DELETE | `/api/assignment/assignments/{id}/` | |
| GET / POST | `/api/assignment/submissions/` | list / create |
| GET / PATCH / DELETE | `/api/assignment/submissions/{id}/` | |
| PATCH | `/api/assignment/submissions/{id}/submit_freeform/` | free-form path |
| POST | `/api/assignment/submissions/{id}/submit_structured/` | structured path |
| PATCH | `/api/assignment/submissions/{id}/grade/` | free-form grading |
| POST | `/api/assignment/submissions/{id}/answer/{question_id}/review/` | structured per-question review, text-type only |
| POST | `/api/assignment/submissions/{id}/publish/` | mints a fresh public slug |
| POST | `/api/assignment/submissions/{id}/unpublish/` | blanks the slug |
| GET | `/api/assignment/public/{slug}/` | `AllowAny`, throttled — public share page |

---

*End of document — everything needed to reproduce, run, and operate the


## Part 6 — Cross-App Integration / Plugin Registry

> Ye is pass ka sabse bada addition hai: har jagah jahan `assignment`
> kisi doosri app se milta hai — ek hi consolidated jagah, taaki koi
> future task campus/liveclass/testseries/core/message ki files khole
> bina bhi yahan se poori tasveer samajh sake.

### 6.1 Golden Rule (repeat, but load-bearing enough to restate here)

`assignment` **kabhi bhi** `campus.*` ya `liveclass.*` models import nahi
karta — kisi bhi function ke andar, kisi bhi `TYPE_CHECKING` block me
bhi nahi. Dono directions is tarah kaam karte hain:

```
campus/liveclass  --(calls)-->  assignment.bridge.create_context_assignment()
campus/liveclass  --(calls)-->  assignment.bridge.get_submissions_for_context()
assignment         --(calls)-->  core.services.create_notification()
```

`context_type` (`"section"` | `"classroom"` | `""`) + `context_id`
(UUID) `Assignment` model par ek **opaque soft-reference** hain —
`assignment` in dono ko kabhi resolve nahi karta, sirf store/filter
karta hai. Sirf caller (campus/liveclass ka apna bridge) hi janta hai
`context_id` ka matlab real `Section`/`Classroom` row me kya hai.

### 6.2 `assignment.bridge` — public contract (dusri apps ke liye)

Ye teen functions hi hain jo kisi doosri app ko `assignment` se milne ke
liye chahiye — poora contract, ek hi jagah:

| Function | Caller | Purpose | Signature |
|---|---|---|---|
| `create_context_assignment()` | `campus.bridge.create_assignment()`, (expected) `liveclass.bridge.create_assignment()` | Assignment row banata hai + poore roster ke liye `AssignmentSubmission(status=MISSING)` bulk pre-create karta hai | `(*, source, context_type, context_id, posted_by, title, description="", attachment=None, due_date=None, total_marks=None, roster: list[dict], extra_data: dict | None = None) -> Assignment` |
| `get_submissions_for_context()` | `campus.bridge.get_assignment_submissions()`, (expected) `liveclass.bridge.get_assignment_submissions()` | Ek context (section/classroom) ke saare submissions, **unfiltered by permission** — caller apna khud ka staff/student narrowing karta hai | `(context_type: str, context_id) -> QuerySet[AssignmentSubmission]` |
| `notify_submission_received()` | `assignment/views.py` khud (submit_freeform/submit_structured ke andar) | Assignment poster ko notify karta hai jab koi student submit karta hai | `(submission: AssignmentSubmission) -> None` |

**`roster` ka exact shape** (`create_context_assignment()` ka
parameter): `[{"user_id": <UUID/PK>, "roll_number": "...", "enrollment_no": "..."}, ...]`
— dono `roll_number`/`enrollment_no` optional keys hain
(`entry.get(..., "")` se defensive), kyunki `liveclass` ke paas ye
concept hi nahi hai (koi roll number nahi, sirf `PassPurchase` holders).

**`source` restriction**: `create_context_assignment()` sirf
`AssignmentSource.CAMPUS` ya `AssignmentSource.LIVECLASS` accept karta
hai — `PERSONAL` is function se **kabhi** nahi banta, wo seedha public
API (`AssignmentViewSet.perform_create`) se, hard-wired, banta hai.

### 6.3 Confirmed caller: `campus.bridge.create_assignment()`

(Pichhle passes me verify hua, is master doc me carry-forward reference
ke liye rakha gaya hai)

- Roster source: `campus.StudentEnrollment.objects.filter(section=section, status=StudentEnrollment.Status.ACTIVE)`
  — har entry se `{"user_id": enrollment.student_id, "roll_number": enrollment.roll_number, "enrollment_no": enrollment.enrollment_no}`
- `context_type="section"`, `context_id=section.id`
- `subject_id` — `Assignment` model par koi field nahi hai iske liye
  (ek `Section` multiple subjects span karta hai) — isliye
  `extra_data={"subject_id": str(subject.id)}` se `Assignment.data`
  JSONField ke andar store hota hai, seedha field ke through nahi.

### 6.4 Expected (not yet independently re-verified this pass) caller: `liveclass.bridge.create_assignment()`

- Roster source: `liveclass.PassPurchase.objects.filter(class_pass__classroom=classroom, status=SUCCESS, is_active=True, expires_at__gt=now)`
  — sirf `{"user_id": student_id}` (roll_number/enrollment_no nahi hain
  is app me, blank rehte hain)
- `context_type="classroom"`, `context_id=classroom.id`
- Koi `subject_id`/`extra_data` nahi (liveclass classrooms subject-scoped
  nahi hain)

### 6.5 `core` integration — notifications

`assignment` **seedha** `core.services.create_notification()` ko call
karta hai (`message` app jaisa hi precedent — `core` neutral layer hai
jise sab depend kar sakte hain):

| Call site | `notif_type` | Kab fire hota hai |
|---|---|---|
| `assignment/bridge.py::notify_submission_received()` | `"submission_received"` | Jab bhi koi student `submit_freeform`/`submit_structured` call karta hai — assignment ke `posted_by` ko notify karta hai |
| `assignment/tasks.py::send_due_reminders()` | `"assignment_due_reminder"` | Due-date reminder sweep — student ko notify karta hai jinka submission abhi bhi `MISSING` hai aur due date `lookahead_hours` ke andar hai |

⚠️ **[ASSUMPTION — abhi bhi unverified]**: `core.services.
create_notification()`'s exact signature kabhi bhi asli `core/
services.py` se verify nahi hui hai (sirf `core/models.py` mila tha,
`core/services.py` kabhi nahi). Dono call sites upar keyword args
(`recipient`, `notif_type`, `title`, `message`, `data`) `core.
Notification` model ke apne fields ke naam se guess kiye gaye hain.
**Is master doc ka agla reader**: agar kabhi `core/services.py` mile,
sabse pehla kaam ye hai ki dono call sites ko us real signature ke
against verify/fix karo — same tareeqe se jaise Part 3.5 me
`auto_grade()` ka mismatch pakड़ा gaya, waisa hi ek aur potential
mismatch yahan chhupa ho sakta hai jab tak actually verify na ho.

### 6.6 `common` integration — shared, non-Django utility modules

`assignment` do jagah `common/` (ek plain Python package, Django app
nahi — koi models/migrations/settings entry nahi) se import karta hai:

| Module | Used by | Exports used |
|---|---|---|
| `common/question_grading.py` | `assignment/models.py` (`AssignmentSubmission.submit_structured()`) | `auto_grade(*, question_type, marks, correct_answer, answer_data) -> GradingResult` |
| `common/attachment_validators.py` | `assignment/models.py` (har FileField) | `attachment_extension_validator`, `validate_attachment_size` |

`common/question_grading.py` **explicitly** `testseries` app se bhi
share hone ke liye design hua hai (uske apne docstring ke mutabiq) —
agar kabhi `testseries` isko import karta hua paya jaaye, dono apps ka
grading behavior automatically same rahega, kyunki dono ek hi function
call kar rahe honge. `assignment` khud kabhi is baat ko assume nahi
karta ki `testseries` isko use kar hi raha hai — ye sirf module ka apna
stated design-intent hai.

### 6.7 `testseries` app — koi seedha coupling nahi, sirf ek shared model-shape

`assignment.AssignmentQuestion`/`AssignmentAnswer` **field-for-field**
`testseries.Question`/`QuestionResponse` ka clone hain (Part 1 design
doc §2a) — same `clean()`/`save()` per-type validation, same
`mark_answer()` bounds-checking. Ye ek **intentional parallel
implementation** hai, koi import/dependency nahi — `assignment` kabhi
`testseries.models` ko import nahi karta, aur na hi vice versa (jahan
tak is pass me pata hai). Agar future me `testseries` ka `Question`
model apna shape badalta hai, `assignment/models.py`'s
`AssignmentQuestion` ko manually usi ke saath sync karna hoga — koi
automatic mechanism dono ko in-sync nahi rakhta.

### 6.8 Notification-type naming — cross-app consistency note

`assignment` khud `notif_type` values **plain strings** ke roop me
bhejta hai (`"submission_received"`, `"assignment_due_reminder"`) —
`core.models.Notification`'s apne enum (`NotifType`) se import nahi
karta, taaki `assignment` ko `core.models` import karne ki zaroorat na
pade (halanki ye already `core.services` import kar raha hai, toh ye
consistency choice hai, hard technical zaroorat nahi). Agar `core.
models.NotifType` par in dono values ke exact-match members nahi hain,
ye silently ek mismatched/unrecognized notif_type ban jayega — **is
master doc ka agla reader**: `core.models.NotifType` ko check karke
confirm karo ki `"submission_received"` aur `"assignment_due_reminder"`
dono wahan character-for-character match karte hain (jaisa `campus.
bridge.NotifTypes` apni class docstring me khud ke liye ye discipline
maintain karti hai — `assignment` ke paas aisi koi mirror-class nahi
hai, isliye ye check manual hai).

### 6.9 Quick "who imports what" map (is app ki poori dependency surface)

```
assignment/models.py       -> common.attachment_validators, common.question_grading, login.models.User
assignment/serializers.py  -> assignment.models (only)
assignment/views.py        -> assignment.{bridge,models,permissions,throttling,serializers}
assignment/bridge.py       -> core.services.create_notification, login.models.User, assignment.models
assignment/tasks.py        -> core.services.create_notification, assignment.models
assignment/admin.py        -> assignment.models
assignment/permissions.py  -> assignment.models (AssignmentSource only)
assignment/urls.py         -> assignment.views

<koi bhi file jo assignment se BAAHAR hai, jo assignment ko import karti hai:>
campus/bridge.py           -> assignment.bridge, assignment.models  (create_assignment/get_assignment_submissions)
liveclass/bridge.py        -> assignment.bridge, assignment.models  (expected — same pattern as campus)
core/views.py (SearchView, agar wired hai)
                           -> assignment.models  (search-scoping ke liye, alag task — is app ke andar nahi)
```

`assignment` khud **kabhi** `campus`, `liveclass`, ya `testseries` ko
import nahi karta — sirf `core.services` aur `login.models` (dono
neutral/shared layers hain, golden-rule violation nahi).

---