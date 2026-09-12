# `campus` App — Design + AS-BUILT Reference (single source of truth)

> **Is version se aage:** ab se sirf yehi doc diya jayega — koi `models.py`/
> `views.py`/`serializers.py`/`tests.py` etc. dobara upload nahi hoga. Isliye
> ye ab sirf ek "plan" nahi hai — jo bhi Phase 1-8 me actually code ho chuka
> hai (models, admin, permissions, serializers, views, urls, services,
> tasks, bridge), uska **exact as-built shape** (fields, constraints,
> endpoints, permission rules, edge-cases jo tests.py confirm karta hai)
> yahan capture kar diya gaya hai. **Age ka sara code aur sari logic isi doc
> ke against likha/badla jayega.** Jahan kuch abhi tak sirf "planned but not
> built" hai, wahan explicitly `[NOT YET BUILT]` likha hai — baaki sab
> **already implemented** hai, matlab naya kaam usi par extend hoga, usse
> contradict nahi karega, jab tak koi explicit decision na ho.

> **Reconciliation pass (latest — this update)**: is baar sab 13 source
> files firse upload huye (exception se — normally sirf ye doc chalta hai,
> lekin real code drift ho chuka tha isliye ek baar phir se source-verify
> kiya gaya) aur **is baar real mismatches mile**, pichli pass ke ulat.
> Doc ab neeche diye gaye changes ke saath update kar diya gaya hai:
> - **G-2 (Campus verification)**: `Campus` par `verification_status`/
>   `verified_by`/`verified_at` naye fields, `CampusViewSet.approve`/
>   `.reject` (platform-admin only), aur `permissions.is_campus_approved`
>   ab `StaffProfileViewSet`/`StudentEnrollmentViewSet`/
>   `ParentLinkVerifyView` par gate karta hai. §1, §13, §14, §15, §16, §19,
>   §20 update kiye.
> - **G-1 (Notice scope)**: `permissions.can_post_notice` ab role-to-scope
>   restrict karta hai (A/P kahin bhi; class-teacher sirf apni section;
>   baaki koi nahi) — pehle "koi bhi active staff, kisi bhi scope pe" tha.
>   §2, §15, §20 update kiye.
> - **FEE-2/FEE-3/FEE-4/FEE-6 (fee module overhaul)**: fee ab Razorpay ki
>   jagah `user_profile.CoinLedger`-backed `User.coin` wallet se pay hoti
>   hai — `FeePayment.Mode.ONLINE`+`confirm` action hata diye gaye,
>   `Mode.WALLET` naya hai; whole-coin-amount guard, insufficient-balance
>   `402`, aur `refund()`/`POST /fee-payments/{id}/refund/` naye hain;
>   `send_fee_due_reminders` task ab `FEE_DUE_REMINDER` bhejta hai
>   (pehle koi emitter nahi tha). §7, §9, §12, §14, §19, §20, §22 update
>   kiye — Razorpay references hata diye gaye.
> - **Admin registrations**: `admin.py` mein ab **sab 28 models** register
>   hain (pehle sirf Phase 1-3 + Notice thi); `FeeInvoice`/`FeePayment`/
>   `CampusAnalyticsSnapshot` `ReadOnlyAdminMixin` ke saath (edit/delete
>   disabled — sirf model methods se hi likhe jaate hain). §0, §16 update
>   kiye.
> - **F-3 (gamification/streak rewards) — naya, partially built**:
>   `tasks.py` mein `check_attendance_streak_rewards`/
>   `check_assignment_ontime_streak_rewards` naye tasks hain jo
>   `CoinLedger` bonus dete hain — lekin ye do REAL gaps ke saath aaye hain
>   jo guess nahi kiye gaye, sirf flag kiye: (1) `services.py` mein
>   `compute_attendance_streak`/`compute_assignment_ontime_streak` functions
>   **exist hi nahi karte** jinhe ye tasks import karte hain — ye tasks
>   `ImportError` denge jab bhi chalenge; (2) `bridge.NotifTypes` mein
>   `CAMPUS_REWARD_EARNED` **define nahi hai** jise dono naye tasks
>   reference karte hain — `AttributeError` denge. Naya §7a + §16 mein
>   dono gaps flag kiye.
> - **Test coverage**: ab 54 tests hain (pehle ~46/~50) — lekin naye G-1/
>   G-2/streak-reward behaviour ke liye **koi test nahi hai**, sirf fee
>   wallet/refund flow ke liye hai. §17 mein ye gap explicitly note kiya.
> - **B-4 (per-action rate limiting) — naya `throttles.py`, is doc me ab
>   tak bilkul missing tha**: `views.py` me `NoticeViewSet.create`,
>   `CampusLiveSessionViewSet.start`, `FeePaymentViewSet.pay`/`.record`/
>   `.refund`, aur `ParentLinkVerifyView.post` — in sab par ab
>   `throttles.py` ke 4 `ScopedRateThrottle` subclasses
>   (`CampusNoticePostThrottle`, `CampusLiveSessionJoinThrottle`,
>   `CampusFeePaymentThrottle`, `CampusParentLinkVerifyThrottle`) lage
>   hain — pehle is app me koi `throttle_scope`/`ScopedRateThrottle` use
>   hi nahi tha. Naya **§12a** isko poora document karta hai; §0 aur §22
>   (project-level `DEFAULT_THROTTLE_RATES` requirement, jiske bina in
>   4 scopes wali pehli hi request `ImproperlyConfigured` degi) bhi
>   update kiye.
> - **§22 ka stale Razorpay paragraph fix**: pichli pass ne khud upar
>   claim kiya tha ki §7/§9/§12/§14/§19/§20/§22 se Razorpay references
>   hata di gayi hain, lekin **§22 me asal me reh gaya tha** — abhi bhi
>   purane `pay`/`confirm` gateway-stand-in ka description tha jabki
>   `confirm` action FEE-2 me hi hat chuka hai. Ab fix kiya: current
>   wallet-debit (`CoinLedger.record_transaction`, `FeePaymentViewSet.pay`)
>   flow likh diya.
>
> **Age se sach me sirf ye doc hi chalega** — koi bhi naya kaam isi doc ko
> padhkar shuru karo aur isi doc ko update karke khatam karo; source files
> dobara upload karne ki zaroorat nahi hai jab tak koi naya structural
> drift na ho (jaisa is baar hua).

> Naam finalize: **`Campus`** (Django app: `campus`). LearnScroll ka 7th app —
> ek poora **Smart Virtual School/College/Institute OS**. Maksad sirf "records
> maintain karna" nahi — school iss par aane ke baad **apna workload kam
> mahsoos kare, smart mahsoos kare**, aur physical building band ho bhi jaye
> to school yahin se bina rukawat chal sake.

**Confirmed constraints (unchanged, enforced in code):**
- Campus-side classes/features **student ke liye free** hain — `liveclass`
  app ka coin/pass/escrow marketplace **isme kabhi involve nahi hoga**
  (`CampusLiveSession` me koi coin/pass/escrow field hi nahi hai — structurally
  impossible).
- `campus` kabhi `liveclass`/`message`/`core` ke models seedha import nahi
  karta — sab `campus/bridge.py` ke through jaata hai (poore project ka golden
  rule). Ye already isi tarah implement hai (see §10 below).
- Har feature "continuity by design" follow karta hai (§9) — koi physical/
  in-person hard dependency nahi.
- Har model ka primary key **UUID** hai (`CampusBaseModel`, `id =
  models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`) —
  sequential-int enumeration attack se bachne ke liye. Har naya model isi
  base class se extend hoga.

---

## 0. File map (as-built)

| File | Kya hai isme |
|---|---|
| `models.py` | Sab models — §2 se §8 tak, poora as-built schema neeche hai |
| `admin.py` | Django admin registrations — **ab sab 28 models registered hain** (Phase 1-8 poora). `FeeInvoice`, `FeePayment`, aur `CampusAnalyticsSnapshot` `ReadOnlyAdminMixin` ke saath registered hain (list/search/filter allowed, add/change/delete disabled from `/admin/` — inka har row sirf model methods se hi likha jaata hai, jo ek doosra invariant bhi sync rakhte hain, jaise `FeePayment.mark_success()`/`.refund()` `FeeInvoice.status` recompute karte hain; ek raw admin edit us invariant ko desync kar sakta hai). `FeeStructure` normal editable hai (sirf ek DEFINITION hai, koi derived invariant nahi carry karta). |
| `permissions.py` | Role-check helper functions + 2 DRF `BasePermission` classes — §13 |
| `serializers.py` | Har model ka `ModelSerializer` + cross-field `validate()` — inline har model section me |
| `views.py` | Har model ka ViewSet + custom `@action`s — §14 |
| `urls.py` | `DefaultRouter` registrations + 1 plain `path()` — §14 |
| `throttles.py` | **B-4, new** — 4 `ScopedRateThrottle` subclasses, each with its own fixed `scope` (not `view.throttle_scope`, since several apply to different `@action`s on the SAME ViewSet): `CampusFeePaymentThrottle` (`FeePaymentViewSet.pay`/`.record`/`.refund`), `CampusLiveSessionJoinThrottle` (`CampusLiveSessionViewSet.start`), `CampusNoticePostThrottle` (`NoticeViewSet.create` only, via `get_throttles()`), `CampusParentLinkVerifyThrottle` (`ParentLinkVerifyView`, class-level). §12a |
| `bridge.py` | `campus` → `core`/`message`/`liveclass` ka **ONLY** door — §10 |
| `services.py` | `compute_attendance_summary`, `generate_report_card_data` — §11 |
| `tasks.py` | 7 Celery tasks (`rollover_session`, `check_low_attendance`, `check_attendance_streak_rewards`, `send_assignment_due_reminders`, `check_assignment_ontime_streak_rewards`, `send_fee_due_reminders`, `refresh_analytics_snapshot`) — §12. **Two of these (`check_attendance_streak_rewards`, `check_assignment_ontime_streak_rewards`) currently crash on real data — see §16 gaps.** |
| `tests.py` | 54 tests covering most flows below — §17 lists what's locked-in, and what's still untested |
| `apps.py`, `__init__.py` | Standard Django boilerplate, nothing app-specific |

---

## 1. Structural hierarchy — models (Phase 1-2, DONE)

All FKs use `on_delete=models.CASCADE` unless noted.

### `Campus`
- `created_by` → `login.User`, CASCADE, `related_name="campuses_created"`
- `name` CharField(200)
- `type` CharField(20), choices `CampusType`: `school` / `college` / `coaching`
- `attendance_alert_threshold_percent` PositiveSmallIntegerField, **default 75**
- `is_active` BooleanField, default `True` — soft toggle set by creator,
  independent of verification below (a creator can deactivate their own
  APPROVED campus; (de)activation is not itself an approval signal)
- **`verification_status`** CharField(10), choices `VerificationStatus`:
  `pending`/`approved`/`rejected`, default `pending`, `db_index=True` —
  **G-2 fix, resolves what used to be open question §16.2**. A campus is
  created `PENDING` and stays that way until a platform admin approves/
  rejects it via `CampusViewSet.approve`/`.reject`. `PENDING`/`REJECTED`
  does **not** block the creator from setting up structure on their own
  campus (sessions/classes/sections/subjects/rooms) — what it gates is
  **other people being pulled into it**: `StaffProfileViewSet.perform_create`,
  `StudentEnrollmentViewSet.perform_create`, and `ParentLinkVerifyView` all
  now call `permissions.is_campus_approved(campus_id)` and raise/403 if the
  campus isn't `APPROVED` yet.
- `verified_by` FK → `login.User`, `SET_NULL`, nullable — the platform admin
  who called `approve`/`reject`
- `verified_at` DateTimeField, null/blank
- `fee_module_enabled` BooleanField, default `False` — per-campus opt-in for
  the whole fee module (§7); nothing else in the model layer checks this flag,
  it's enforced at the view layer (`FeeStructureViewSet.perform_create`/
  `.generate_invoices`, `FeeInvoiceViewSet.perform_create`,
  `FeePaymentViewSet._get_invoice_and_check` — FEE-1 gates every fee-writing
  entry point now, not just structure-creation)
- `created_at` auto_now_add
- `Meta.ordering = ["name"]`

**`CampusViewSet.approve`/`.reject`** — `POST /campuses/{id}/approve/` and
`.../reject/`, platform-admin only (`permissions.IsPlatformAdmin` —
Django `is_staff`/`is_superuser`, NOT a campus-scoped `StaffProfile` role;
see `permissions.is_platform_admin`'s docstring for why a campus's own
self-appointed ADMIN must never be treated as equivalent to this). Both
bypass `get_object()`/the requester's-own-campuses `get_queryset()` scoping
and look the campus up directly, since a platform admin needs to see/act on
ANY pending campus, not just ones they're already a member of. Sets
`verification_status`, `verified_by=request.user`, `verified_at=now()`.

### `AcademicSession`
- `campus` FK → `Campus`, `related_name="sessions"`
- `name` CharField(20) — e.g. `"2026-27"`
- `start_date`, `end_date` DateField
- `is_current` BooleanField, default `False`
- `updated_at` auto_now
- **DB constraint**: `UniqueConstraint(fields=["campus"], condition=Q(is_current=True), name="one_current_session_per_campus")`
- **`save()` override**: if `is_current=True`, flips every OTHER session of the
  same campus to `is_current=False` first, inside `transaction.atomic()`,
  *before* writing this row — so even a plain
  `AcademicSession.objects.create(..., is_current=True)` from a shell/bulk-import
  never violates the unique constraint, not just the `set-current` API action.
  **Locked in by test** `test_plain_orm_create_does_not_violate_unique_constraint`.
- `Meta.ordering = ["-start_date"]`

### `Department` (optional, college-level, NOT session-scoped)
- `campus` FK → `Campus`, `related_name="departments"`
- `name` CharField(150)

### `SchoolClass` (named this, not `Class`, to avoid shadowing the keyword)
- `campus` FK → `Campus`, `related_name="classes"`
- `department` FK → `Department`, **nullable**, `on_delete=SET_NULL`, `related_name="classes"`
- `session` FK → `AcademicSession`, CASCADE, `related_name="classes"`
- `name` CharField(100) — `"Class 10"`, `"B.Sc 2nd Year"`
- `indexes = [Index(fields=["session", "campus"])]`
- **Serializer-level cross-check** (`SchoolClassSerializer.validate`): if
  `department` is set, `department.campus_id` must equal `campus.id`;
  `session.campus_id` must equal `campus.id`. Both raise 400, field-scoped.

### `Section`
- `school_class` FK → `SchoolClass`, `related_name="sections"`
- `name` CharField(20) — `"A"`, `"B"`
- **DB constraint**: `unique_section_per_class` on `(school_class, name)`
- **Side effect on create** (`SectionViewSet.perform_create`): calls
  `bridge.create_section_group(section, actor=request.user)` — auto-creates the
  `message.Group`+`Conversation` via `core.classroom_chat_bridge`. **[NOT YET
  WIRED — degrades to a logged no-op until `core.classroom_chat_bridge.
  create_section_group` exists; section creation itself never fails because of
  this]**. Locked in by tests `test_creating_section_calls_bridge_with_the_new_section`
  and `test_bridge_not_being_wired_up_yet_does_not_break_section_creation`.

### `Subject`
- `campus` FK → `Campus`, `related_name="subjects"`
- `department` FK → `Department`, nullable, `SET_NULL`, `related_name="subjects"`
- `name` CharField(150), `code` CharField(30, blank)
- Cross-check: `department.campus_id == campus.id` if department set.

### `Room` (optional per campus — clash-detection only, §4)
- `campus` FK → `Campus`, `related_name="rooms"`
- `name` CharField(100), `is_virtual` BooleanField default `False`

### `StaffProfile`
- `campus` FK → `Campus`, `related_name="staff_profiles"`
- `user` FK → `login.User`, `related_name="campus_staff_profiles"`
- `role` CharField(20), choices `Role`: `admin` / `principal_hod` /
  `class_teacher` / `subject_teacher` / `non_teaching`
- `is_active` BooleanField default `True`
- **DB constraint**: `unique_staff_profile_per_campus` on `(campus, user)` — one
  role-row per (campus, user); a user CAN hold a `StaffProfile` at more than
  one campus.
- `indexes = [Index(fields=["campus", "role"])]`
- **No self-signup** — a `StaffProfile` is always created by an existing
  admin/principal targeting some other user id (`StaffProfileViewSet`), EXCEPT
  the very first one: `CampusViewSet.perform_create` auto-creates the creator
  as `ADMIN` in the same transaction as campus creation.

### `ClassTeacherAssignment`
- `section` **OneToOne** → `Section`, `related_name="class_teacher_assignment"`
  (one class-teacher per section, enforced at DB level via OneToOne)
- `staff` FK → `StaffProfile`, `related_name="class_teacher_of"`
- Cross-check: `staff.campus_id == section.school_class.campus_id`

### `SubjectTeacherAssignment`
- `section` FK, `subject` FK, `staff` FK (all CASCADE)
- `approved_by` FK → `StaffProfile`, nullable, `SET_NULL`, `related_name="approvals_made"`
- `status` CharField(10), choices `pending`/`approved`/`rejected`, default
  `pending`, `db_index=True`
- `responded_at` DateTimeField, null/blank — set when approved/rejected
- `updated_at` auto_now
- **DB constraint**: `unique_subject_teacher_assignment` on `(section, subject, staff)`
- **Flow (design doc's "class-teacher subject-teacher ko allow karega")**:
  - Create: **any authenticated user** can request (no special role needed) —
    `SubjectTeacherAssignmentViewSet.permission_classes = [IsAuthenticated]`
    (overridden from the campus-scoped default). Lands `PENDING`.
  - `POST /subject-teacher-assignments/{id}/approve/` and `.../reject/`:
    only that section's `ClassTeacherAssignment` holder, OR a campus
    admin/principal-HOD (fallback for when no class-teacher assigned yet), can
    decide (`_can_decide()`). Sets `status`, `approved_by` (the deciding
    staff's own `StaffProfile` row), `responded_at=now()`. Fires
    `bridge.notify(...)` with `STAFF_ASSIGNMENT_APPROVED` /
    `STAFF_ASSIGNMENT_REJECTED` to the requesting staff's user.
  - Cross-check on create: `subject.campus_id` and `staff.campus_id` must both
    equal `section.school_class.campus_id`.
  - Locked in by tests: `test_class_teacher_can_approve`,
    `test_class_teacher_can_reject`, `test_unrelated_subject_teacher_cannot_approve`,
    `test_campus_admin_can_also_approve_as_fallback`.

### `StudentEnrollment`
- `student` FK → `login.User`, `related_name="campus_enrollments"`
- `section` FK, `session` FK (both CASCADE)
- `roll_number` CharField(30, blank)
- `status` CharField(15), choices `active`/`transferred`/`graduated`, default
  `active`, `db_index=True`
- **DB constraint**: `unique_enrollment_per_section_session` on `(student,
  section, session)` — one row per student per section per session; new year
  = new row, old row stays read-only history.
- **Deliberately NOT unique across campuses/sessions overall** — a student CAN
  currently be enrolled in more than one campus (open question §16.1, still
  unresolved — see below).
- `indexes = [Index(fields=["session", "status"])]`
- Serializer cross-check: `section.school_class.session_id == session.id`
  (i.e. you can't enroll into a section under a different session than the one
  you specified). Locked in by `test_enrolling_with_mismatched_session_is_rejected`.

### `CampusParentLink`
- `campus`, `student` (→ `login.User`), `parent` (→ `login.User`), all CASCADE
- `created_at` auto_now_add
- **DB constraint**: `unique_campus_parent_link` on `(campus, student, parent)`
- **Read-only from the API** — the ONLY write path is `POST
  /parent-links/verify/` (`ParentLinkVerifyView`, a plain `APIView`, NOT part
  of the router — deliberately registered in `urls.py` BEFORE `router.urls` so
  it doesn't get shadowed by the router's own `parent-links/<pk>/` detail
  pattern treating `"verify"` as a pk and 405ing). Body: `{"campus": <id>,
  "token": "<parent access token>"}`. Internally calls
  `bridge.resolve_parent_from_token(token)` — verification itself lives
  entirely in `message`'s existing `ParentAccessCode`/`ParentToken` flow,
  never touched directly from here. `get_or_create`s the link on success. On
  failure (bad/expired token, OR bridge not wired up yet — both cases treated
  identically) → `400 {"detail": "Invalid or expired token."}`.
  `CampusParentLinkViewSet` itself is `ReadOnlyModelViewSet`.

---

## 2. Notices — models (Phase 3, DONE)

### `Notice`
- `campus` FK (required), `department`/`school_class`/`section` FK — **all
  three nullable**, whichever combination is set defines the scope (one model
  covers every scope level instead of four near-duplicate ones)
- `session` FK → `AcademicSession` (required)
- `posted_by` FK → `login.User`, `SET_NULL`, nullable
- `title` CharField(200), `body` TextField, `pin_until` DateTimeField null/blank
- `created_at` auto_now_add
- `indexes = [Index(fields=["campus", "-created_at"])]`
- **Permission**: **G-1 fix — role-to-scope now enforced, resolves what used
  to be open question §16.5**. `NoticeViewSet.perform_create` calls
  `permissions.can_post_notice(user, campus_id, department_id, school_class_id,
  section_id)` instead of the old blanket "any active staff, any scope"
  check:
  - Campus admin/principal-HOD: can post at ANY scope (campus-wide,
    department, class, or a single section).
  - Class-teacher: can post ONLY when the notice is scoped to a single
    `section` AND they're that section's class-teacher. A campus-wide/
    department-wide/class-wide notice from a class-teacher is denied
    regardless of `section_id`.
  - Subject-teacher / non-teaching staff: **cannot post a notice at all**
    yet — `Notice` has no `subject` field, so a subject-teacher doesn't own
    a notice-scope of their own; this is deliberately left unbuilt, not a
    bug.
  `IsAuthenticated` only at `permission_classes` level; `can_post_notice`
  failing raises `PermissionDenied` inside `perform_create` (still a
  standard DRF `403`).
- Cross-check (`NoticeSerializer.validate`): whichever of
  `department`/`school_class`/`section` is set, its own campus (via
  `.campus_id`, or `.school_class.campus_id` for `section`) must equal the
  given `campus`.
- Section-group message send on `CampusLiveSession` scheduling/`Assignment`
  posting also creates a `Notice` (see below) — `Notice` isn't only
  manually posted.

---

## 3. Live classes — coin-free (Phase 4, DONE)

### `CampusLiveSession`
- **Deliberately has NO coin/pass/escrow field at all** — structurally
  impossible to become a second marketplace.
- `section` FK, `subject` FK (both CASCADE)
- `teacher` FK → `StaffProfile`, `related_name="live_sessions_taught"`
- `scheduled_at` DateTimeField
- `status` CharField(10), choices `scheduled`/`live`/`ended`/`cancelled`,
  default `scheduled`, `db_index=True`
- `room_id` CharField(150, blank) — video token/room id, **server-controlled
  only**, never client-writable (`read_only_fields` in serializer)
- `indexes = [Index(fields=["section", "scheduled_at"])]`

**Permission**: `IsAuthenticated` + `IsSectionSubjectStaffOrReadOnly` →
create/update needs `can_manage_section_subject(user, campus_id, section_id,
subject_id)` true, i.e. the section's class-teacher, campus admin/principal,
or an APPROVED subject-teacher for that exact section+subject.

**On create (`perform_create`, `@transaction.atomic`)**:
1. `bridge.provision_video_room(live_session, actor=user)` — sets `room_id` if
   a non-`None` value comes back. **[NOT YET WIRED — no-ops to `None`/blank
   room_id if `core.classroom_chat_bridge.provision_video_room` doesn't exist
   yet; scheduling still succeeds]**.
2. Auto-creates a `Notice` (scoped to the section) titled `"Class scheduled:
   {subject.name}"`.
3. `bridge.notify(...)` with `CAMPUS_SESSION_SCHEDULED` to every ACTIVE
   enrollment's student in that section.

**Custom actions** (status transitions are server-controlled — never PATCH
`status`/`room_id` directly):
- `POST /live-sessions/{id}/start/` — only from `SCHEDULED` (else 400) →
  `LIVE`, notifies all active enrollees with `CAMPUS_SESSION_LIVE`.
- `POST /live-sessions/{id}/end/` — → `ENDED`, no validation on prior status.
- `POST /live-sessions/{id}/cancel/` — → `CANCELLED`, no validation on prior status.

Locked in by tests: `test_approved_subject_teacher_can_schedule_session`,
`test_unrelated_subject_teacher_cannot_schedule`,
`test_start_action_flips_status_and_notifies`.

---

## 4. Timetable + attendance automation — models (Phase 5, DONE)

### `TimeSlot`
- `campus` FK, `day_of_week` PositiveSmallIntegerField (choices `Day`, 1=Monday..7=Sunday)
- `start_time`, `end_time` TimeField, `label` CharField(50, blank) — `"Period 3"`
- Serializer validation: `end_time` must be after `start_time`.

### `TimetableEntry` — **clash-detection lives at the MODEL layer, not just the serializer**
- `section`, `subject`, `staff`, `time_slot` FK (all CASCADE), `room` FK
  nullable `SET_NULL`, `session` FK CASCADE
- `indexes = [Index(fields=["session", "time_slot"])]`
- **`clean()`** (called unconditionally via a `save()` override that calls
  `self.full_clean()` first — so admin/shell/bulk-import can't bypass it
  either):
  - same `staff` + same `time_slot` + same `session`, excluding self → reject
    (`"This staff member already has an entry in this time slot."`)
  - same `section` + same `time_slot` (same session, implicit via the
    `.filter(session=..., time_slot=...)` base queryset) → reject
    (`"This section already has an entry in this time slot."`)
  - if `room` is set: same `room` + same `time_slot` → reject
    (`"This room is already booked in this time slot."`)
- At the API layer, `TimetableEntrySerializer` uses
  `DjangoCleanValidationMixin` — this catches the raw
  `django.core.exceptions.ValidationError` from `full_clean()` (which DRF does
  NOT auto-convert) and re-raises as `rest_framework.exceptions.ValidationError`,
  so a clash comes back as a clean 400, not a 500.
- Permission: `IsCampusAdminOrPrincipal` only (not subject-teacher-manageable).
- Locked in by `test_first_entry_succeeds`, `test_same_staff_same_slot_clash_is_rejected`.

### `Attendance`
- `enrollment` FK CASCADE, `date` DateField
- `subject` FK, **nullable** — `None` = daily mark, not period-wise
- `status` CharField(10), choices `present`/`absent`/`late`/`leave` (no default —
  must be supplied)
- `marked_by` FK → `login.User`, `SET_NULL`, nullable — **always
  server-set to the requesting user**, never client-supplied (`read_only_fields`)
- **DB constraint**: `unique_attendance_per_day_subject` on `(enrollment, date, subject)`
- `indexes = [Index(fields=["enrollment", "date"])]`
- **Permission rule (important, easy to get wrong)**: when `subject` is
  `None` (daily mark), ONLY the section's class-teacher or campus
  admin/principal may mark — a subject-teacher with no subject specified is
  **NOT** automatically allowed even if approved for some subject in that
  section. When `subject` IS set, an APPROVED subject-teacher for that exact
  section+subject may also mark. This lives in
  `permissions.can_manage_section_subject()`.
- No %-age field is stored anywhere on this model or a summary model — see
  §11 `compute_attendance_summary`.
- `GET /attendance/summary/?enrollment=<id>&subject=<id optional>` — visible
  to: any active staff of that campus, OR the enrollment's own student, OR a
  linked parent of that student. Anyone else → `404` (not `403` — deliberately
  indistinguishable from "doesn't exist"). Returns
  `compute_attendance_summary()`'s dict, recomputed live, never cached.
- Locked in by `test_subject_teacher_can_mark_attendance`,
  `test_outsider_cannot_mark_attendance`, `test_summary_percent_reflects_marked_records`.

---

## 5. Assignments + syllabus tracker — models (Phase 6, DONE)

### `Assignment`
- `section`, `subject` FK CASCADE, `posted_by` FK → `StaffProfile`, `SET_NULL`,
  nullable — **server-set from the requesting user's StaffProfile at that
  campus**, never client-supplied
- `title` CharField(200), `description` TextField(blank),
  `attachment` FileField(`upload_to="campus/assignments/"`, null/blank)
- `due_date` DateField, `session` FK CASCADE
- `indexes = [Index(fields=["section", "due_date"])]`

**On create (`perform_create`, `@transaction.atomic`)**:
1. `posted_by` resolved from the requester's `StaffProfile` at that campus.
2. **Bulk pre-creates an `AssignmentSubmission` row (status `MISSING`) for
   every ACTIVE enrollment in that section**, `ignore_conflicts=True` — so
   grading always has a full roster; students only ever PATCH their own
   pre-existing row.
3. Auto-creates a `Notice` (`"New assignment: {title}"`, body `"Due
   {due_date}."`) scoped to the section.
4. `bridge.notify(...)` with `ASSIGNMENT_POSTED_CAMPUS` to every recipient
   student.

### `AssignmentSubmission`
- `assignment` FK CASCADE, `student` FK → `login.User` CASCADE
- `submitted_at` DateTimeField null/blank, `file` FileField
  (`upload_to="campus/submissions/"`, null/blank)
- `status` CharField(10), choices `submitted`/`late`/`missing`, default
  `missing`, `db_index=True`
- `grade` CharField(10, blank), `feedback` TextField(blank)
- **DB constraint**: `unique_submission_per_student` on `(assignment, student)`
- **ViewSet is `http_method_names = ["get", "post", "patch", "head", "options"]`
  — no DELETE, no PUT.**
- **`get_object()` is deliberately NOT scoped by `get_queryset()`** — it looks
  up from the unrestricted queryset and raises `PermissionDenied` (403)
  explicitly if the requester is neither the submission's own student nor
  active staff at that campus, so an unrelated user gets a 403 ("can't do
  this") rather than a 404 ("doesn't exist") for a row that does exist.
  `get_queryset()` (used for `list`) DOES filter to `student=self` OR
  `staff at that campus` — that's just to keep `list` from leaking other
  students' rows, it's a different concern from `get_object()`.
- **`perform_create`** only covers the edge case of a student enrolled
  *after* the assignment was posted (normal case = pre-created in bulk
  above) — a student may only ever create their own row (`student` forced to
  `request.user`; if body sets a different `student`, `PermissionDenied`).
- **`perform_update` branches by who's calling**, never both from one request:
  - Requester IS the submission's own student → this is the "submit" path:
    server sets `submitted_at=now()`, `status` = `LATE` if
    `today > assignment.due_date` else `SUBMITTED`. `student`, `submitted_at`,
    `status` are all `read_only_fields` at the serializer level, so a student
    can't self-grade or backdate.
  - Requester is NOT the student → this is the "grade" path: requires
    `can_manage_section_subject(user, campus_id, section_id, subject_id)` for
    that assignment's section+subject, else `PermissionDenied`. Free to set
    `grade`/`feedback` (not `status`/`submitted_at`, still read-only).
- Locked in by `test_posting_assignment_precreates_submissions_and_notifies`,
  `test_student_can_submit_own_assignment`, `test_teacher_can_grade_submission`,
  `test_unrelated_user_cannot_grade_submission`.

### `SyllabusUnit`
- `subject`, `section`, `session` FK (all CASCADE), `title` CharField(200),
  `order` PositiveSmallIntegerField default `0`
- **On create** (`SyllabusUnitViewSet.perform_create`): auto `get_or_create`s
  a matching `SyllabusProgress` row.

### `SyllabusProgress`
- `syllabus_unit` **OneToOne** → `SyllabusUnit`, `related_name="progress"`
- `covered_on` DateField null/blank — `None` = not yet covered
- `covered_by` FK → `StaffProfile`, `SET_NULL`, nullable
- **ViewSet is read + `mark-covered` action only** — `mixins.ListModelMixin,
  mixins.RetrieveModelMixin, GenericViewSet` (no generic create/update/delete;
  rows only ever come from `SyllabusUnitViewSet.perform_create` above and only
  ever change via the action below).
- `POST /syllabus-progress/{id}/mark-covered/` — sets `covered_on=today()`,
  `covered_by` = requester's `StaffProfile` at that campus. Permission:
  `IsSectionSubjectStaffOrReadOnly` (class-teacher/admin/approved
  subject-teacher for that unit's section+subject).
- Locked in by `test_creating_unit_auto_creates_progress_row`,
  `test_subject_teacher_can_mark_covered`.

---

## 6. Results (Phase 7, DONE)

### `ExamTerm`
- `session` FK CASCADE, `name` CharField(100) — `"Mid-Term"`, `"Final"`,
  `start_date`/`end_date` DateField
- Permission: `IsCampusAdminOrPrincipal` only.

### `ResultEntry`
- `enrollment`, `subject`, `exam_term` FK CASCADE
- `marks_obtained`, `max_marks` DecimalField(max_digits=6, decimal_places=2)
- `remarks` CharField(255, blank)
- `entered_by` FK → `StaffProfile`, `SET_NULL`, nullable — **always
  server-set**, never client-supplied
- **DB constraint**: `unique_result_per_exam` on `(enrollment, subject, exam_term)`
- Serializer validation: `marks_obtained` cannot exceed `max_marks` (400 if it
  does). Locked in by `test_marks_obtained_cannot_exceed_max_marks`.
- **Create/update = immediate publish** — there is NO draft/publish flag on
  this model in the current schema. `perform_create` (`@transaction.atomic`)
  immediately notifies the student **and every linked parent**
  (`CampusParentLink` rows for that student, campus-agnostic lookup — not
  filtered to the same campus) with `RESULT_PUBLISHED`.
  **`[FLAGGED]`** — if a draft/review step before parents see marks is ever
  wanted, that needs a new field + migration, not a view-layer flag.
- `GET /results/report-card/?enrollment=<id>&exam_term=<id>` — visible to:
  active staff of that campus, the enrollment's own student, or a linked
  parent (campus-scoped this time, via `is_linked_parent_of_student(...,
  campus_id)`). Anyone else → `404`. Returns
  `generate_report_card_data()` (see §11) — **no separate `ReportCard` model
  exists or is planned; `ResultEntry` stays the single source of truth.**
- Locked in by `test_publishing_result_notifies_student`,
  `test_report_card_aggregates_entries`.

---

## 7. Optional / future-ready modules (Phase 8, DONE — including the fee module, which the design doc originally left as an open question)

### `DigitalIDCard`
- `user` FK → `login.User`, `campus` FK, `qr_token` CharField(100, **unique**),
  `issued_at` auto_now_add, `valid_until` DateField null/blank
- `qr_token` is **always server-generated** (`uuid.uuid4().hex`), never
  client-writable.
- Permission: a user can request their OWN card; only campus admin/principal
  can issue one for someone else (`PermissionDenied` otherwise).
- Scanning-hardware integration is explicitly future work — this model just
  needs to exist and generate a stable unique token for now.
- Locked in by `test_student_can_issue_own_card`,
  `test_student_cannot_issue_card_for_someone_else`,
  `test_admin_can_issue_card_for_a_student`.

### Fee module (`FeeStructure` / `FeeInvoice` / `FeePayment`) — **fully built, now PAID FROM the shared coin wallet (FEE-2 — reverses the earlier "decoupled end-to-end" decision)**

**FEE-2 — product decision changed mid-flight, flagged so nobody re-derives
the old design**: the previous version of this doc said fee was "decoupled
from `liveclass` coins end-to-end" and paid through a real Razorpay gateway
(`FeePayment.Mode.ONLINE` + a `confirm` action standing in for a webhook).
That is **no longer true**. Campus fee is now paid FROM the same
`user_profile.CoinLedger`-backed `User.coin` wallet that `liveclass` already
uses for classes/passes:
- `FeePayment.Mode.ONLINE` and the `POST /fee-payments/{id}/confirm/` action
  are **gone** (removed as dead code, not left in place unreachable) —
  replaced by `Mode.WALLET`.
- `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` are untouched in settings.py
  because `liveclass.CoinPurchase` still uses them to top the wallet up in
  the first place — fee itself no longer talks to any gateway, it just
  spends what's already in the wallet.
- The actual debit happens at the view layer
  (`FeePaymentViewSet.pay`, `@transaction.atomic` via
  `CoinLedger.objects.record_transaction()`'s own row lock) — `models.py`
  itself never imports `user_profile`; only `FeePayment.refund()` (FEE-4,
  below) does, for the matching credit-back.
- **Unit mismatch, flagged not guessed at**: `FeePayment.amount`/
  `FeeStructure.amount` are `DecimalField(decimal_places=2)` (real currency,
  paise-capable), but `CoinLedger.amount`/`User.coin` are plain whole-number
  integers — there is no fractional coin. **This pass's decision: wallet fee
  payments must be a whole number of coins**, enforced at the view layer
  (`FeePaymentViewSet._resolve_whole_coin_amount` rejects a fractional
  amount with `400` before ever calling `record_transaction`), never
  silently rounded. If paisa-level fee genuinely needs to work over the
  wallet, that needs a coin-side schema change (`User.coin`/`CoinLedger`
  becoming Decimal) — out of scope here, same "flag, don't guess" posture
  as `RestrictUser`/§16 elsewhere.

Why it's still shaped the way it is otherwise: this fee module reuses the
*pattern* `CoinLedger` proved out (signed, append-only, self-auditing money
trail with an idempotency key so a retried payment can never double-apply)
— `FeePayment.gateway_reference` is now the idempotency key for the wallet
path specifically (still populated via `get_or_create`, same as before),
and `CoinLedger.record_transaction`'s own `reference` (`f"fee-payment-
{payment.id}"`, derived from the already-deduplicated `FeePayment` row, not
a fresh random value) gives a **second, independent idempotency guarantee**
at the wallet layer itself.

**`FeeStructure`** — a fee DEFINITION, not a payment:
- `campus` FK, `school_class` FK **nullable** (`None` = campus-wide fee, not
  tied to one class), `session` FK, `title` CharField(150)
- `amount` **DecimalField** (max_digits=10, decimal_places=2) — deliberately
  not an int/float, unlike `User.coin`, because this is real currency
- `due_date` DateField, `is_active` BooleanField default `True`
- `indexes = [Index(fields=["campus", "session"])]`
- **Gated on `Campus.fee_module_enabled`** — `FeeStructureViewSet.perform_create`
  raises `PermissionDenied` if the campus hasn't opted in. Locked in by
  `test_fee_structure_rejected_when_module_disabled`.
- Permission: `IsCampusAdminOrPrincipal` only.
- `POST /fee-structures/{id}/generate-invoices/` — bulk-creates a
  `FeeInvoice` for every currently-ACTIVE enrollment this structure applies to
  (campus-wide if `school_class` is null, else just that class, matched on
  `session` too). **Idempotent** via the `unique_invoice_per_structure`
  constraint + `get_or_create` — calling twice never double-invoices. Returns
  `{"invoices_created": N, "already_existed": M}`.
  **FEE-1 fix**: this action used to be reachable even after a campus had
  since turned `fee_module_enabled` back OFF (the gate only ran once, at
  `FeeStructure`-creation time) — now re-checks `_require_fee_module_enabled`
  on every call. Locked in by
  `test_generate_invoices_rejected_when_module_disabled_after_structure_created`.
  **FEE-1 also applies to `FeeInvoiceViewSet.perform_create`** — it's a full
  `ModelViewSet`, so a direct `POST /fee-invoices/` bypassing
  `generate-invoices` needed the same gate, and now has it.

**`FeeInvoice`** — one student's obligation against one `FeeStructure`:
- `enrollment` FK, `fee_structure` FK (both CASCADE)
- `amount_due` DecimalField(10,2)
- `status` CharField(10), choices `pending`/`partial`/`paid`/`overdue`/`waived`,
  default `pending`, `db_index=True` — **NEVER hand-set from a view, always
  via `recompute_status()`**
- **DB constraint**: `unique_invoice_per_structure` on `(enrollment, fee_structure)`
- `indexes = [Index(fields=["enrollment", "status"])]`
- `amount_paid` — a `@property`, NOT a stored column: sums `payments.filter
  (status=SUCCESS)`. Deliberately recomputed, not cached, to avoid
  read-modify-write drift (same reasoning flagged on `User.followers_count`/
  `coin` elsewhere in the codebase). If a dashboard ever needs this in bulk,
  that's an `.annotate()` at the queryset level, not a new column.
- `recompute_status()` — idempotent (only writes if the derived status
  actually changed): if `paid < amount_due` → `OVERDUE` (if past
  `fee_structure.due_date`) else `PENDING`, upgraded to `PARTIAL` if
  `0 < paid < amount_due`; else `PAID`. Called right after a `FeePayment` is
  marked `SUCCESS`.

**`FeePayment`** — one money-movement event against an invoice, **two
first-class write paths** (neither bolted onto the other):
- `invoice` FK CASCADE, `amount` DecimalField(10,2)
- `paid_by` FK → `login.User`, `SET_NULL`, nullable — who actually paid
  (deliberately separate from `invoice.enrollment.student`, since a linked
  parent can pay a child's fee)
- `payer_role` CharField(10), choices `student`/`parent`/`admin`
- `payment_mode` CharField(15), choices **`wallet`**/`cash`/`cheque`/
  `bank_transfer`/`other` — **FEE-2: was `online`/... (a real Razorpay
  gateway path); `wallet` replaces it.** The amount is debited directly
  from the payer's `CoinLedger`-backed `User.coin` balance instead of any
  external gateway.
- `status` CharField(10), choices `pending`/`success`/`failed`/`refunded`,
  default `pending`, `db_index=True`
- `gateway_reference` CharField(150, blank, `db_index=True`) — **idempotency
  key, only ever populated for the WALLET path now** (FEE-2 — previously an
  actual payment-gateway reference for the removed Razorpay integration;
  `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` in settings.py are still used
  elsewhere, by `liveclass.CoinPurchase`, to top the wallet up — just not
  by fee anymore). Callers on the wallet path
  `get_or_create(gateway_reference=..., defaults={...})` so a retried
  request can never double-apply a payment (mirrors `CoinLedger.reference`'s
  pattern).
- `recorded_by` FK → `login.User`, `SET_NULL`, nullable — staff who manually
  entered a cash/cheque/bank-transfer payment at the counter; **null for the
  wallet self-serve path**
- `notes` CharField(255, blank)
- `indexes = [Index(fields=["invoice", "-created_at"])]`
- **Constraints**: `CheckConstraint(amount__gt=0)`; `UniqueConstraint` on
  `gateway_reference` but **only when non-blank** (`condition=~Q(gateway_reference=""))`)
  so the manual/cash path's blank rows never collide with each other.
- `mark_success()` — idempotent (same pattern as `core.Notification.mark_read()`):
  only flips `status=SUCCESS` and calls `invoice.recompute_status()` if not
  already `SUCCESS`; calling twice is a safe no-op.
- **`refund()` — new, FEE-4**: reverses a WALLET payment (e.g. student paid
  the wrong invoice) by crediting the coins back via
  `CoinLedger.objects.record_transaction(transaction_type=REFUND, reference=
  f"fee-refund-{id}", ...)`, inside `transaction.atomic()`, then flips
  `status=REFUNDED` and calls `invoice.recompute_status()`. Idempotent, same
  shape as `mark_success()` — a re-call on an already-REFUNDED row is a
  no-op because the raise below stops it. **Raises `ValueError`** if called
  on a non-SUCCESS payment (nothing to refund), a non-WALLET payment
  (nothing was ever debited to credit back — cash/cheque/bank-transfer
  refunds stay a manual counter-side matter, same as before wallet payments
  existed), or a WALLET payment with no `paid_by` on record.
- **ViewSet is read + 3 actions, NO generic create/update/delete** — the
  state machine can't be bypassed by a plain PATCH. **FEE-2: the old
  `PENDING`-until-webhook `confirm` action is GONE** — `pay` now resolves
  synchronously to SUCCESS or a clean `402` in the same request, so there is
  no pending window left to confirm:
  - `POST /fee-payments/pay/` — self-serve, requires `invoice` in body +
    caller is the invoice's own student OR a linked parent
    (`is_linked_parent_of_student`), else `403`. Also `403` if the campus's
    `fee_module_enabled` is off (FEE-1). Validates the amount is a whole
    number of coins (`_resolve_whole_coin_amount`, `400` if fractional/
    non-numeric/≤0). `get_or_create`s the `FeePayment` on `gateway_reference`
    (client-supplied or a fresh `uuid4().hex`) — a retried call with the
    same reference returns the existing row (`200`), never double-debits.
    On a fresh row: debits the wallet via `CoinLedger.record_transaction`
    inside the same flow; **FEE-3** — if that raises `ValueError`
    (insufficient balance), the payment is kept as `FAILED` (audit trail,
    not deleted; `gateway_reference` stays reserved) and the response is
    `402` with `{"detail": ..., "current_balance", "required",
    "coins_needed", "action": "top_up_coins"}` (a generic frontend routing
    flag, not a hardcoded `liveclass` URL — campus never imports
    `liveclass`). On success: `mark_success()`, `201`.
  - `POST /fee-payments/record/` — office-staff path, requires
    `is_campus_admin_or_principal` OR `is_any_active_staff` at that campus.
    Rejects `payment_mode=WALLET` with `400` ("office staff can't trigger a
    wallet debit on someone else's behalf — use `pay` for that"). Otherwise
    immediately `status=SUCCESS`, `recorded_by=request.user`, then
    explicitly calls `payment.invoice.recompute_status()`.
  - **`POST /fee-payments/{id}/refund/` — new, FEE-4.** Office/admin only
    (same permission shape as `record`, not self-serve like `pay`). Calls
    `payment.refund()`; a `ValueError` from that becomes `400`.
- Locked in by `test_student_pay_debits_wallet_exact_balance_and_marks_invoice_paid`,
  `test_pay_rejects_fractional_amount`, `test_pay_rejected_when_module_disabled`,
  `test_record_rejects_wallet_mode`, `test_office_can_record_cash_payment`,
  `test_refund_credits_wallet_back_and_unmarks_invoice_paid`,
  `test_refund_rejects_non_success_payment`.

### `CampusAnalyticsSnapshot`
- `campus`, `session` FK CASCADE, `computed_at` auto_now_add,
  `data` JSONField (default `dict`) — attendance-trend, subject-wise avg
  marks, teacher workload, syllabus-completion %
- `indexes = [Index(fields=["campus", "-computed_at"])]`
- **Read-only ViewSet** (`ReadOnlyModelViewSet`) — rows are written EXCLUSIVELY
  by the `refresh_analytics_snapshot` Celery task (§12), never from a request.
- `GET /analytics-snapshots/latest/?campus=<id>` — most recent snapshot for
  that campus, `404` if none yet.

---

## 7a. Gamification / streak rewards (F-3) — **new this pass, PARTIALLY BUILT, currently broken end-to-end**

`tasks.py` gained two new Celery tasks that pay a `CoinLedger` bonus for
good engagement streaks, sibling in shape to `check_low_attendance`/
`send_assignment_due_reminders`:

- **`check_attendance_streak_rewards`** — for every ACTIVE enrollment,
  recomputes the current daily-attendance streak via
  `services.compute_attendance_streak(enrollment)` and pays a bonus every
  time it crosses a fresh multiple of
  `settings.CAMPUS_ATTENDANCE_STREAK_DAYS` (assumed default 7).
- **`check_assignment_ontime_streak_rewards`** — for every ACTIVE
  enrollment, recomputes the on-time-submission streak via
  `services.compute_assignment_ontime_streak(student, section)` and pays a
  bonus every time it crosses a fresh multiple of
  `settings.CAMPUS_ASSIGNMENT_STREAK_COUNT` (assumed default 5).

Both pay via `CoinLedger.objects.record_transaction(transaction_type=
CoinLedger.TransactionType.CAMPUS_REWARD, ...)`, imported **directly** from
`user_profile.models` — **deliberately NOT through `bridge.py`**. This is
not an inconsistency with the `campus` → `core`/`message` golden rule:
`CoinLedger.record_transaction()` is documented (per `tasks.py`'s own
module docstring) as the shared, cross-app coin-write path every app is
meant to call directly, unlike `core.Notification`/`message` internals
which `campus` is deliberately walled off from.

**Idempotency**: neither task adds a new model/column to track "already
rewarded" — the `CoinLedger` `reference` is built from
`(enrollment id, streak length, the date/due_date the streak reached that
length)`, which can only happen once ever (dates don't repeat), so
`record_transaction`'s own reference check is the real double-credit guard.
Both are safe to re-run more than once a day for the same reason
`send_assignment_due_reminders` is — no state of their own, only reads.

**`[FLAGGED — TWO REAL GAPS, NOT GUESSED AT]`** — this reconciliation pass
found both new tasks **will crash the moment either one actually runs**,
because two things they depend on don't exist anywhere in this upload:
1. `services.py` has only `compute_attendance_summary` and
   `generate_report_card_data` (§11) — **`compute_attendance_streak` and
   `compute_assignment_ontime_streak` are not defined anywhere**. Both new
   tasks `from .services import ...` one of these and will raise
   `ImportError` the first time Celery (or a test) actually invokes them.
2. `bridge.py`'s `NotifTypes` (§9) has no `CAMPUS_REWARD_EARNED` constant,
   but both tasks call `bridge.notify(notif_type=NotifTypes.CAMPUS_REWARD_EARNED,
   ...)` after a successful reward — this will raise `AttributeError`
   (attribute doesn't exist on the class) even if gap #1 above were fixed.

Neither gap is guessed at here — writing `compute_attendance_streak`'s
actual definition (consecutive-day logic, what breaks a streak, timezone
handling) or picking a `CAMPUS_REWARD_EARNED` string value is a real design
decision this doc won't make on its own. Whoever picks this up needs to:
add the two missing `services.py` functions, add
`CAMPUS_REWARD_EARNED = "campus_reward_earned"` (or whatever value is
agreed) to `bridge.NotifTypes` (and note it in §9's table), and confirm
`settings.CAMPUS_ATTENDANCE_STREAK_DAYS` /
`CAMPUS_ATTENDANCE_STREAK_BONUS_COINS` / `CAMPUS_ASSIGNMENT_STREAK_COUNT` /
`CAMPUS_ASSIGNMENT_STREAK_BONUS_COINS` actually exist in the project's
settings.py (not part of this upload, so unconfirmed — see §22) and that
`CoinLedger.TransactionType.CAMPUS_REWARD` exists on the real
`user_profile.models.CoinLedger` (also not part of this upload).
**No test in `tests.py` exercises either task** — see §17.

---

## 8. Continuity-by-design principle (unchanged, architectural — applies to every model above)

- Koi bhi model/flow "must happen in person" pe hardcoded dependency nahi
  rakhega — attendance bhi teacher live-session se hi mark kar sakta hai,
  physical roll-call zaroori nahi; notices/results/fee sab digital-first.
- `CampusLiveSession` hi primary teaching mode ban sakta hai agar zaroorat
  pade — koi alag "emergency remote mode" nahi.
- Sab kuch `AcademicSession`-scoped hai, isliye school kabhi bhi naya session
  start karke seamlessly continue kar sakta hai (`rollover_session` task, §12).

---

## 9. Notification types (as-built — `campus.bridge.NotifTypes`)

`NotifTypes` in `bridge.py` mirrors what needs to land on
`core.models.NotifType` **once that enum exists there** — kept as plain
string constants for now so `campus` never imports `core.models` just to
reference a type:

```
CAMPUS_SESSION_SCHEDULED = "campus_session_scheduled"
CAMPUS_SESSION_LIVE = "campus_session_live"
LOW_ATTENDANCE_ALERT = "low_attendance_alert"
ASSIGNMENT_POSTED_CAMPUS = "assignment_posted_campus"
ASSIGNMENT_DUE_REMINDER = "assignment_due_reminder"
RESULT_PUBLISHED = "result_published"
FEE_DUE_REMINDER = "fee_due_reminder"          # constant exists; no emitter wired to it yet — see §16.3
STAFF_ASSIGNMENT_APPROVED = "staff_assignment_approved"
STAFF_ASSIGNMENT_REJECTED = "staff_assignment_rejected"
NOTICE_POSTED = "notice_posted"                # reused as-is from core.models.NotifType, not redefined
```

**Every value here must match, verbatim, whatever gets added to
`core.models.NotifType`** once that lands — don't rename these casually.

**`[GAP — see §7a]`**: `tasks.check_attendance_streak_rewards` and
`tasks.check_assignment_ontime_streak_rewards` (new this pass, F-3) both
reference `NotifTypes.CAMPUS_REWARD_EARNED`, which **does not exist in the
list above**. This is not a documentation lag — the constant is genuinely
missing from `bridge.py` as uploaded, so both tasks will `AttributeError`
the moment they run. Flagged, not silently added here, since the actual
string value is a decision for whoever wires up gap #1 in §7a too.

---

## 10. `bridge.py` — the golden-rule enforcement point `[PARTIALLY WIRED — see per-function status]`

`campus/bridge.py` is `campus`'s **ONLY** door into `core`/`message`/
`liveclass`. Every function lazy-imports the real target and degrades to a
logged no-op instead of a hard crash if that target doesn't exist yet — so
`campus` stays installable/migratable/testable standalone. **This degrade
behaviour is intentional and tested — do not "fix" it into a hard failure
without a product decision.**

| Function | Target (once wired) | Current status | Degrade behaviour |
|---|---|---|---|
| `create_section_group(section, actor)` | `core.classroom_chat_bridge.create_section_group` | `[NOT YET WIRED]` | Logs a warning, returns `None`; section creation still succeeds |
| `notify(*, users, notif_type, title, body='', data=None)` | `core.models.Notification` | `[NOT YET WIRED]` | Logs a warning per call, returns `[]` |
| `provision_video_room(live_session, actor)` | `core.classroom_chat_bridge.provision_video_room` | `[NOT YET WIRED]` | Logs a warning, returns `None`; session scheduling still succeeds with blank `room_id` |
| `resolve_parent_from_token(token)` | `core.classroom_chat_bridge.resolve_parent_from_token` | `[NOT YET WIRED]` | Logs a warning, returns `(None, None)`; `ParentLinkVerifyView` turns this into the same 400 as an actually-invalid token |

**When these DO get wired up** (i.e. `core/models.py` +
`core/classroom_chat_bridge.py` land): every `except ImportError` branch in
`bridge.py` should be deleted, the `try` bodies are already the real,
finished integration code — nothing else in `campus` needs to change, every
call site already calls `bridge.X(...)`, never the target module directly.

---

## 11. `services.py` — as-built (never store what you can cheaply recompute)

### `compute_attendance_summary(enrollment, subject=None)`
- `subject=None` means "every attendance record for this enrollment
  regardless of subject" (daily + subject-wise together) — **NOT** "only the
  daily marks". Pass an actual `Subject` to scope to just that subject.
- `ATTENDED_STATUSES = (PRESENT, LATE)` — LATE counts as attended,
  ABSENT/LEAVE don't. **This is the single place that definition lives** —
  the `check_low_attendance` Celery task calls this same function, so they
  can never drift on what "percent" means.
- Returns a plain dict, never persisted: `{enrollment, subject, total,
  present, absent, late, leave, percent}` — `percent` rounded to 2 decimals,
  `0.0` if `total == 0`.

### `generate_report_card_data(enrollment, exam_term)`
- Aggregates every `ResultEntry` for one `(enrollment, exam_term)` pair.
  Returns `{enrollment, exam_term, subjects: [...], total_obtained,
  total_max, percentage}`. `ResultEntry` stays the only source of truth — no
  separate storage.

**`[GAP — see §7a]`**: `tasks.py` (F-3, new this pass) imports
`compute_attendance_streak` and `compute_assignment_ontime_streak` from
this module — **neither function is defined here**. `services.py` as
uploaded still only has the two functions above. Whoever picks up §7a's
gap needs to add both here, following the same "plain function, returns a
plain dict/tuple, never persisted" convention the two existing functions
use.

---

## 12. `tasks.py` — Celery tasks, as-built (7 tasks — grew from 4 this pass, F-3)

`shared_task` degrades to a plain-function decorator if Celery isn't
installed, so importing this module never hard-fails. Callers that do
`.delay(...)` wrap it in `try/except Exception` and fall back to calling the
task synchronously if `.delay` isn't available (see `rollover` action below)
— this ALSO means the plain-function fallback (no `.delay` attribute) works
correctly with that same try/except, since a bare function call still runs.

| Task | Registered against (beat schedule — **defined at project level, NOT in this file**) | What it does |
|---|---|---|
| `rollover_session(campus_id, new_session_id)` | Called on-demand via `AcademicSessionViewSet.rollover` action, not on a schedule | Carries forward ACTIVE enrollments from the campus's OTHER sessions into the new one, matching by **same class name + same section name** in the new session. No match → skipped and counted, not guessed at. Returns `{carried_forward, skipped}` or `{"detail": "Session not found...", carried_forward: 0, skipped: 0}` if the session doesn't belong to that campus. Whole operation wrapped in `transaction.atomic()`. |
| `check_low_attendance()` | `campus-daily-attendance-check` — **daily** | For every ACTIVE enrollment of every active `Campus`, recomputes overall %-age via `compute_attendance_summary` (skips if `total==0`); if `< campus.attendance_alert_threshold_percent`, fires `LOW_ATTENDANCE_ALERT` to the student + every `CampusParentLink` parent for that campus. Returns `{alerted: N}`. |
| **`check_attendance_streak_rewards()`** — new (F-3) | Suggested against the same daily schedule as `check_low_attendance` | For every ACTIVE enrollment of every active `Campus`, recomputes the daily-attendance streak via `services.compute_attendance_streak(enrollment)` and pays a `CoinLedger` bonus (`settings.CAMPUS_ATTENDANCE_STREAK_BONUS_COINS`) every time it crosses a fresh multiple of `settings.CAMPUS_ATTENDANCE_STREAK_DAYS`. Idempotency key: `campus_attendance_streak:{enrollment_id}:{streak}:{last_date}` — see §7a. Returns `{rewarded: N}`. **`[BROKEN — see §7a/§9]`: crashes on the missing `services.compute_attendance_streak` and `NotifTypes.CAMPUS_REWARD_EARNED`.** |
| `send_assignment_due_reminders()` | Daily, but **safe to run more than once a day** (re-run only re-notifies students still `MISSING`, no double-notify for ones who've since submitted, since it holds no state of its own) | For every `Assignment` with `due_date == today`, notifies every student whose `AssignmentSubmission.status == MISSING` with `ASSIGNMENT_DUE_REMINDER`. Returns `{reminded: N}`. |
| **`check_assignment_ontime_streak_rewards()`** — new (F-3) | Suggested against the same daily schedule as `send_assignment_due_reminders` | For every ACTIVE enrollment, recomputes the on-time-submission streak via `services.compute_assignment_ontime_streak(student, section)` and pays a `CoinLedger` bonus (`settings.CAMPUS_ASSIGNMENT_STREAK_BONUS_COINS`) every time it crosses a fresh multiple of `settings.CAMPUS_ASSIGNMENT_STREAK_COUNT`. Idempotency key: `campus_assignment_streak:{enrollment_id}:{streak}:{last_due_date}` — see §7a. Returns `{rewarded: N}`. **`[BROKEN — see §7a/§9]`: same two missing dependencies as the attendance-streak task above.** |
| **`send_fee_due_reminders()`** — new (FEE-6, resolves what used to be open question §16.3) | Suggested daily | For every `FeeInvoice` whose `status` is still `PENDING`/`PARTIAL`/`OVERDUE` and whose `fee_structure.due_date` is today or past, notifies the student + linked parents with `FEE_DUE_REMINDER`. Since fee now debits the wallet (FEE-2), also appends a shortfall hint to the notification body when the **student's own** `User.coin` balance is short of what's still owed (never checks a linked parent's balance — a parent might pay from their own wallet, so this only names the shortfall it can state without ambiguity). Safe to re-run — reads `FeeInvoice.status`/`due_date` only, no state of its own. Returns `{reminded: N}`. |
| `refresh_analytics_snapshot(campus_id, session_id)` | `campus-refresh-analytics-snapshot` — hourly/daily | Computes `avg_attendance_percent` (via `compute_attendance_summary` per active enrollment), `avg_marks_obtained` (DB `Avg` over `ResultEntry`), `syllabus_completion_percent` (`covered_units / total_units` over `SyllabusProgress`), `active_enrollments` count. Creates and returns the new `CampusAnalyticsSnapshot`'s id. **Deliberately minimal** — teacher-workload etc. from the original design-doc wishlist are NOT computed yet; this is a follow-up, not a guess at a shape nothing has asked for. |

**F-3 note on `CoinLedger` imports**: `check_attendance_streak_rewards`/
`check_assignment_ontime_streak_rewards` import
`user_profile.models.CoinLedger` **directly**, not through `bridge.py`.
Deliberate, not an inconsistency with the `campus` → `core`/`message`
golden rule — see §7a for why.

---

## 12a. `throttles.py` — as-built (B-4, new this pass)

Before this pass `campus` had **zero** `throttle_scope`/`ScopedRateThrottle`
usage anywhere, unlike `liveclass`/`message` which already throttle every
abuse-prone action. Four `ScopedRateThrottle` subclasses now cover the same
class of endpoint here — money movement, an unauthenticated-adjacent
token-verification surface, session-start fan-out, and a public-facing post
to a whole roster:

| Throttle class | `scope` | Applied to | Why |
|---|---|---|---|
| `CampusFeePaymentThrottle` | `campus_fee_payment` | `FeePaymentViewSet.pay` / `.record` / `.refund` (all three, same scope) | Every hit moves real coin balance one way or another — `pay` debits via `CoinLedger.record_transaction`, `refund` credits back, `record` writes a `SUCCESS` payment straight onto an invoice. Rated like the existing `coin_withdrawal`/`coin_purchase` scopes for the same reason: a script hammering this is a wallet-balance/invoice-desync risk, not just noise. |
| `CampusLiveSessionJoinThrottle` | `campus_live_session_join` | `CampusLiveSessionViewSet.start` | `campus` has no separate student "join" endpoint (unlike `liveclass.ClassSessionViewSet.join`, which this scope name is modeled on) — `start` is the closest analogue: it flips a session `LIVE` and fires the `CAMPUS_SESSION_LIVE` notification fan-out to every active enrollment in the section. Stops a teacher account (compromised, scripted, or double-tapping) from re-triggering that fan-out in a loop. |
| `CampusNoticePostThrottle` | `campus_notice_post` | `NoticeViewSet.create` **only** — via a `get_throttles()` override, so list/retrieve/update/delete keep the project-wide default throttle | Any active staff member can post a notice at some scope (see §2/G-1); without a limit, one compromised/scripted staff account can spam every student/parent in a campus. Stands in for the still-missing finer-grained role-to-scope restriction. |
| `CampusParentLinkVerifyThrottle` | `campus_parent_link_verify` | `ParentLinkVerifyView` — class-level `throttle_classes` (the whole view is a single `POST` action, so no `get_throttles()` override is needed here, unlike the three ViewSets above which have other unrelated actions to leave unthrottled) | Takes a raw `token` from the request body and resolves it via `bridge.resolve_parent_from_token` — without a rate limit this is a token-guessing surface: an authenticated user could brute-force someone else's parent-access token and get linked to (read access to) an arbitrary student's campus data. Rated tight, for the abuse case (guessing), not for legitimate retry traffic (a parent verifies their link once, not repeatedly) — same posture as `message`'s `parent_code_reveal`/`parent_code_verify_ip` scopes. |

**Infra requirement (see §22)**: each of the 4 scopes above needs a matching
entry in the **project's** `DEFAULT_THROTTLE_RATES` — a `ScopedRateThrottle`
whose scope has no rate entry raises `ImproperlyConfigured` on the very
first request to that action (the same bug class already fixed repeatedly
elsewhere in `settings.py` for `session_join`, `coin_withdrawal`,
`chat_reaction`, etc.). None of the 4 entries exist inside `campus` itself
— nothing in this app can add them.

---

## 13. `permissions.py` — as-built role logic

Plain functions (reusable from a serializer's `validate()` too, not just a
DRF `BasePermission`) plus **3** `BasePermission` classes (grew from 2 this
pass, G-2):

- `get_staff_profile(user, campus_id)` — the user's ACTIVE `StaffProfile` at
  that campus, or `None`.
- `is_campus_admin(user, campus_id)` / `is_campus_admin_or_principal(...)` —
  role checks off that profile. **Campus-scoped only** — `StaffProfile.Role.ADMIN`
  has no relationship to Django's `user.is_staff`/`user.is_superuser` and
  must never stand in for them (see `is_platform_admin` below for the
  actual platform-level check, and why the two are kept deliberately
  separate).
- `is_any_active_staff(user, campus_id)` — any active staff row at all.
- `is_class_teacher_of_section(user, section_id)`.
- `is_linked_parent_of_student(user, student_id, campus_id=None)` — `campus_id`
  optional: pass it to also confirm the link was made at THAT campus (used
  for report cards, fee payment); omit for a campus-agnostic "is this a
  parent anywhere" check (used for the attendance summary endpoint, which
  isn't itself campus-scoped).
- `can_manage_section_subject(user, campus_id, section_id, subject_id=None)` —
  the central "can this user touch this section's content" check, true if
  ANY of: class-teacher of the section; campus admin/principal-HOD; OR (only
  if `subject_id` given) an APPROVED `SubjectTeacherAssignment` for that
  exact section+subject. **When `subject_id` is `None`, the subject-teacher
  path never applies — only class-teacher/admin can act.**
- **`can_post_notice(user, campus_id, department_id=None, school_class_id=None, section_id=None)`**
  — **new, G-1 fix, resolves what used to be open question §16.5**. True if
  campus admin/principal-HOD (any scope), OR the notice is scoped to a
  single `section` AND `user` is that section's class-teacher. Any broader
  scope (department/class-wide) from anyone but admin/principal is always
  denied, regardless of `section_id`. A subject-teacher/non-teaching staff
  member currently has no scope of their own and can never post — `Notice`
  has no `subject` field, so this isn't guessed at.
- **`is_platform_admin(user)`** — **new, G-2 fix**. `bool(user.is_staff or
  user.is_superuser)` — genuinely platform-level, independent of any
  campus's own `StaffProfile` rows (a campus that hasn't been approved yet
  has no admin/principal `StaffProfile` whose authority could be trusted
  anyway, since the self-appointed creator's own ADMIN row is exactly the
  thing this gate exists to not blindly trust).
- **`is_campus_approved(campus_id)`** — **new, G-2 fix**. True only once
  `Campus.verification_status == APPROVED`. Gates the membership-growth
  endpoints (`StaffProfileViewSet.perform_create`,
  `StudentEnrollmentViewSet.perform_create`, `ParentLinkVerifyView`) — i.e.
  pulling OTHER people into a campus — but deliberately **not** the
  creator's own structural setup (sessions/classes/sections/subjects/rooms),
  since `CampusViewSet.perform_create` must always leave the creator able
  to manage their own campus.
- `IsCampusAdminOrPrincipal(BasePermission)` — safe methods (`GET`/`HEAD`/
  `OPTIONS`) always allowed; unsafe methods require
  `is_campus_admin_or_principal` for the campus the view resolves via
  `view.get_campus_id_for_permission_check(request)`. `None` campus id →
  always deny.
- `IsSectionSubjectStaffOrReadOnly(BasePermission)` — same shape but resolves
  `(campus_id, section_id, subject_id)` via
  `view.get_section_subject_for_permission_check(request)` and calls
  `can_manage_section_subject`. `None` campus/section → always deny.
- **`IsPlatformAdmin(BasePermission)`** — **new, G-2 fix**. Wraps
  `is_platform_admin(request.user)`; used ONLY on `CampusViewSet.approve`/
  `.reject`.

**`CampusMemberScopedMixin`** (in `views.py`, not `permissions.py`, but
governs every viewset's visibility) — `get_my_campus_ids(user)` is the single
place that defines "every campus this user has ANY legitimate reason to see
rows from": active staff, OR enrolled student (via
`section__school_class__campus_id`), OR linked parent. Every viewset's
queryset filters through this. **Any new membership route (e.g. a future
guardian type) must be added here, in one place, or every viewset silently
goes stale.**

---

## 14. Full API surface (as-built `urls.py`, router-based, all under whatever project-level prefix `campus.urls` is mounted at)

**Rate limiting (B-4, new — see §12a):** four actions in the table below
carry a scoped throttle on top of the project-wide default —
`NoticeViewSet.create`, `CampusLiveSessionViewSet.start`,
`FeePaymentViewSet.pay`/`.record`/`.refund`, and `ParentLinkVerifyView.post`.
Everything else (list/retrieve/update/delete, and every other custom
action) stays on the project-wide default throttle only.

| Router basename | Path prefix | ViewSet | Notable custom actions |
|---|---|---|---|
| `campus` | `campuses/` | `CampusViewSet` | `POST {id}/approve/`, `POST {id}/reject/` — **new (G-2), platform-admin only** |
| `academic-session` | `sessions/` | `AcademicSessionViewSet` | `POST {id}/set-current/`, `POST {id}/rollover/` |
| `department` | `departments/` | `DepartmentViewSet` | — |
| `school-class` | `classes/` | `SchoolClassViewSet` | — |
| `section` | `sections/` | `SectionViewSet` | — |
| `subject` | `subjects/` | `SubjectViewSet` | — |
| `room` | `rooms/` | `RoomViewSet` | — |
| `staff-profile` | `staff/` | `StaffProfileViewSet` | — |
| `class-teacher-assignment` | `class-teacher-assignments/` | `ClassTeacherAssignmentViewSet` | — |
| `subject-teacher-assignment` | `subject-teacher-assignments/` | `SubjectTeacherAssignmentViewSet` | `POST {id}/approve/`, `POST {id}/reject/` |
| `student-enrollment` | `enrollments/` | `StudentEnrollmentViewSet` | — |
| `campus-parent-link` | `parent-links/` | `CampusParentLinkViewSet` (read-only) | plus standalone `POST parent-links/verify/` → `ParentLinkVerifyView` |
| `notice` | `notices/` | `NoticeViewSet` | — |
| `campus-live-session` | `live-sessions/` | `CampusLiveSessionViewSet` | `POST {id}/start/`, `POST {id}/end/`, `POST {id}/cancel/` |
| `time-slot` | `time-slots/` | `TimeSlotViewSet` | — |
| `timetable-entry` | `timetable-entries/` | `TimetableEntryViewSet` | — |
| `attendance` | `attendance/` | `AttendanceViewSet` | `GET summary/?enrollment=&subject=` |
| `assignment` | `assignments/` | `AssignmentViewSet` | — |
| `assignment-submission` | `assignment-submissions/` | `AssignmentSubmissionViewSet` | GET/POST/PATCH only, no PUT/DELETE |
| `syllabus-unit` | `syllabus-units/` | `SyllabusUnitViewSet` | — |
| `syllabus-progress` | `syllabus-progress/` | `SyllabusProgressViewSet` | list/retrieve + `POST {id}/mark-covered/` only |
| `exam-term` | `exam-terms/` | `ExamTermViewSet` | — |
| `result-entry` | `results/` | `ResultEntryViewSet` | `GET report-card/?enrollment=&exam_term=` |
| `digital-id-card` | `digital-id-cards/` | `DigitalIDCardViewSet` | — |
| `fee-structure` | `fee-structures/` | `FeeStructureViewSet` | `POST {id}/generate-invoices/` |
| `fee-invoice` | `fee-invoices/` | `FeeInvoiceViewSet` | — |
| `fee-payment` | `fee-payments/` | `FeePaymentViewSet` | list/retrieve + `POST pay/`, `POST record/`, `POST {id}/refund/` only — **`confirm` REMOVED (FEE-2), `refund` NEW (FEE-4)** |
| `campus-analytics-snapshot` | `analytics-snapshots/` | `CampusAnalyticsSnapshotViewSet` (read-only) | `GET latest/?campus=` |

**Important ordering note preserved from `urls.py`**: `parent-links/verify/`
is registered BEFORE `router.urls` in `urlpatterns` — don't reorder this,
the router's own `parent-links/<pk>/` pattern would otherwise greedily match
`"verify"` as a pk first.

---

## 15. Role-based access (as-built summary — matches §13's actual code)

| Role | Access |
|---|---|
| Platform Admin (`is_staff`/`is_superuser`) | **New (G-2)** — approve/reject a pending `Campus` (`verification_status`). Independent of any campus's own `StaffProfile`. |
| Campus Admin | Full campus — structural setup (departments/classes/sections/subjects/rooms/staff, gated on the campus being platform-APPROVED for staff/enrollment — see §13's `is_campus_approved`), session set-current/rollover, exam terms, timetable, fee-structure setup (if enabled), analytics, a fallback approver for subject-teacher requests, and can post a `Notice` at any scope |
| Principal/HOD | Same permission level as Admin everywhere in the current code (`is_campus_admin_or_principal` never distinguishes the two) — `[FLAGGED]` if a real distinction is ever needed, it isn't there yet |
| Class Teacher | Approve/reject subject-teacher requests for their own section; mark daily attendance for their section; manage (attendance/assignments/live-sessions/syllabus/results) anything scoped to their section regardless of subject; **can post a `Notice`, but ONLY scoped to their own section (G-1)** |
| Subject Teacher (APPROVED only) | Only their approved section+subject: live sessions, subject-wise attendance, assignments, syllabus progress, results — never a daily (subject-less) attendance mark; **cannot post a `Notice` at all (G-1)** |
| Non-teaching staff | Same as subject-teacher for notices: **cannot post one yet (G-1)** — no notice-scope of their own |
| Student | Own schedule/notice/attendance/assignment/result/fee/ID card; submit own assignment; pay own fee invoice **from their own coin wallet (FEE-2)**; sees only their own `AssignmentSubmission` rows |
| Parent (via verified `CampusParentLink`) | Read-only attendance/result/notice; can pay a linked child's fee invoice **from their own coin wallet (FEE-2)**; gets `LOW_ATTENDANCE_ALERT`/`RESULT_PUBLISHED`/`FEE_DUE_REMINDER` notifications |

---

## 16. Open questions — STILL genuinely unresolved (do not guess at these; flag again if they come up)

1. **Single-campus vs multi-campus student enrollment** — `StudentEnrollment`
   has NO cross-campus uniqueness constraint, only per-section-per-session.
   Currently UNRESTRICTED (a student can enroll at more than one campus).
   Tightening this later is a migration, not a code guess.
2. **Principal/HOD vs Admin distinction** — the code currently treats them
   identically everywhere (`is_campus_admin_or_principal`). If they ever need
   different permissions, that function (and every call site) needs
   revisiting.
3. **`bridge.py`'s 4 functions are all still unwired** (§10) — nothing in
   `campus` breaks because of this today (everything degrades gracefully),
   but no section-group chat, no real video rooms, no real notifications, and
   no parent-token verification actually happen end-to-end until
   `core/models.py` + `core/classroom_chat_bridge.py` exist and are wired in.
4. **`confirm`'s old GOTCHA is now moot** — removed along with the action
   itself (FEE-2), not fixed. Noting this so nobody re-derives the "any
   campus member can confirm anyone's payment" gap this used to flag; it no
   longer applies because there's nothing left to confirm.
5. **`[NEW — F-3]` Streak-reward tasks are broken as uploaded** — see §7a/§9/§11/§12
   for the full detail. In short: `services.compute_attendance_streak`/
   `compute_assignment_ontime_streak` don't exist, and
   `bridge.NotifTypes.CAMPUS_REWARD_EARNED` doesn't exist, so both new
   Celery tasks will raise on first real invocation. Not guessed at here —
   needs both added deliberately, plus confirmation that
   `CoinLedger.TransactionType.CAMPUS_REWARD` and the four
   `settings.CAMPUS_*_STREAK_*` constants actually exist in the wider
   project (neither `user_profile/models.py` nor `settings.py` were part of
   this upload, so unconfirmed).
6. **`[NEW]` No test coverage for G-1, G-2, or F-3** — `tests.py` (54 tests)
   still only exercises the pre-existing flows plus the new fee-wallet/
   refund paths. Nothing tests: a class-teacher being denied a campus-wide
   notice or allowed their own section (G-1); a `PENDING` campus blocking
   staff-invite/enrollment/parent-link-verify, or `IsPlatformAdmin` gating
   `approve`/`reject` (G-2); or either streak-reward task (F-3 — arguably
   moot until gap #5 above is fixed, since they'd currently error out
   rather than produce a meaningful assertion). Flagged so this isn't
   mistaken for "tested, therefore safe to build on."

**Resolved this pass** (kept here as a changelog, not as open items
anymore): the old #2 "campus creation gate" question → **G-2**, `Campus.
VerificationStatus` (§1); the old #3 "`FEE_DUE_REMINDER` has no emitter"
question → **FEE-6**, `tasks.send_fee_due_reminders` (§12); the old #5
"notice posting not role-scoped" question → **G-1**, `permissions.
can_post_notice` (§2, §13); the old #6 "admin registrations incomplete"
question → **all 28 models now registered** (§0).

---

## 17. Test coverage map (`tests.py`, 54 tests) — treat these as LOCKED-IN behaviour

Any future change that would break one of these needs a deliberate decision,
not an accidental regression:

- **Campus creation**: auto-provisions creator as Admin; non-members can't
  see others' campuses; enrollment alone is enough to see a campus.
- **AcademicSession**: only one `is_current` per campus, enforced even via
  plain `.objects.create()` (not just the API action); `set-current` flips
  the old one off; only admin/principal can call `set-current`.
- **Structural scoping**: `SchoolClass` rejects a `session` from a different
  campus; accepts matching campus+session.
- **Section-group bridge**: creating a section calls `bridge.create_section_group`
  with the new section; the bridge not being wired up yet does NOT break
  section creation.
- **Subject-teacher approval flow**: class-teacher can approve/reject; an
  unrelated subject-teacher cannot approve; campus admin can approve as a
  fallback.
- **Notices**: staff (admin) can post campus-wide; non-staff cannot post; a
  notice citing a section from a different campus is rejected.
  **`[GAP]`** no test exercises the G-1 restriction itself — a class-teacher
  posting outside their own section, or a subject-teacher/non-teaching
  staff member being denied entirely, is untested (see §16.6).
- **Enrollment**: mismatched session (section's session ≠ given session) is
  rejected; a valid enrollment succeeds. **`[GAP]`** no test covers the G-2
  `is_campus_approved` gate (a `PENDING` campus's enrollment attempt).
- **Live sessions**: an approved subject-teacher can schedule; an unrelated
  subject-teacher cannot; `start` flips status and notifies.
- **Timetable**: first entry succeeds; same staff + same slot clash is
  rejected.
- **Attendance**: subject-teacher can mark; an outsider cannot; summary %-age
  reflects marked records.
- **Assignments**: posting pre-creates submissions + notifies; a student can
  submit their own; a teacher can grade; an unrelated user cannot grade.
- **Syllabus**: creating a unit auto-creates its progress row; subject-teacher
  can mark covered.
- **Results**: `marks_obtained` can't exceed `max_marks`; publishing notifies
  the student; report-card correctly aggregates entries.
- **Fee module** (rewritten this pass for FEE-1/2/3/4 — old `pay→confirm`
  tests are gone along with the `confirm` action):
  - `FeeStructure` creation rejected when the module is disabled for that
    campus (`test_fee_structure_rejected_when_module_disabled`).
  - `generate-invoices` rejected if the module was disabled AFTER the
    structure was created (`test_generate_invoices_rejected_when_module_disabled_after_structure_created`
    — FEE-1).
  - Student `pay` debits the wallet for the exact balance and marks the
    invoice paid (`test_student_pay_debits_wallet_exact_balance_and_marks_invoice_paid`).
  - `pay` rejects a fractional coin amount (`test_pay_rejects_fractional_amount`)
    and a disabled module (`test_pay_rejected_when_module_disabled`).
  - `record` rejects `payment_mode=WALLET` (`test_record_rejects_wallet_mode`).
  - `refund` credits the wallet back and un-marks the invoice as paid
    (`test_refund_credits_wallet_back_and_unmarks_invoice_paid`), and rejects
    a non-SUCCESS payment (`test_refund_rejects_non_success_payment`).
  **`[GAP]`** no test covers: the `402` insufficient-balance response from
  `pay`, `record` gated by staff role, or `refund` gated by staff role.
- **Digital ID cards**: student can issue their own; cannot issue for
  someone else; admin can issue for a student.
- **Parent link verification**: valid token creates the link; invalid token
  is rejected. **`[GAP]`** no test covers the G-2 `is_campus_approved` gate
  on this endpoint either.
- **Rollover regression**: a plain ORM `.create(is_current=True)` does not
  violate the one-current-session-per-campus constraint.

**`[GAP — see §16.6]`** entirely untested this pass: `CampusViewSet.approve`/
`.reject` (`IsPlatformAdmin` gating, `verification_status` transitions), and
both new streak-reward Celery tasks (`check_attendance_streak_rewards`,
`check_assignment_ontime_streak_rewards` — arguably can't be meaningfully
tested yet given §7a/§9's missing dependencies).

---

## 18. Phased build status

| Phase | Scope | Status |
|---|---|---|
| 1 | `Campus`, `AcademicSession`, `Department`, `SchoolClass`, `Section`, `Subject`, `Room`, `StaffProfile` | **DONE** |
| 2 | `ClassTeacherAssignment`, `SubjectTeacherAssignment` (approval flow), `StudentEnrollment` | **DONE** |
| 3 | Section-group auto-creation, `Notice` | **DONE (bridge call unwired, degrades gracefully; posting now role-scoped per G-1)** |
| 4 | `CampusLiveSession` (coin-free) + notifications | **DONE (video provisioning + notify unwired, degrades gracefully)** |
| 5 | `TimeSlot`, `TimetableEntry` (clash-detection) | **DONE** |
| 6 | `Attendance` + auto low-attendance alert task | **DONE (notify unwired, degrades gracefully)** |
| 7 | `Assignment`, `AssignmentSubmission` + auto-reminder task | **DONE (notify unwired, degrades gracefully)** |
| 8 | `SyllabusUnit`, `SyllabusProgress` | **DONE** |
| 9 | `ExamTerm`, `ResultEntry` | **DONE** |
| 10 | `CampusParentLink` wiring (reuse `message.ParentAccessCode`) | **DONE at the `campus` end; `resolve_parent_from_token` itself unwired in `core`; write path now also gated on campus verification (G-2)** |
| 11 | `CampusAnalyticsSnapshot` + admin/HOD dashboard endpoints | **DONE (minimal metric set — see §12 caveat)** |
| 12 | `DigitalIDCard`, `FeeStructure`/`FeeInvoice`/`FeePayment` | **DONE — fee payment now wallet-based (FEE-2), not Razorpay** |
| 13 | Session rollover automation (Celery task) | **DONE** |
| 14 | Campus platform-verification gate (G-2) | **DONE** |
| 15 | Gamification / streak rewards (F-3) | **PARTIALLY BUILT — tasks exist but crash on missing `services.py` functions + missing `NotifType`, see §7a** |

**Everything above is implemented and tested except the four `bridge.py`
integration points (§10/§16.3), and Phase 15 (F-3), which is neither
functional nor tested yet — see §7a/§16.5/§16.6.**
**Any future work should build ON this as-built shape — extend models with
new fields/migrations, add new viewset actions, or wire up `core`'s side of
`bridge.py` — rather than re-deriving anything documented above from
scratch.**

---
---

# PART II — Implementation-grade appendix

> Everything below exists so that **new code for this app can be written
> straight from this document, with zero need to re-derive a decision that
> was already made.** Part I told you *what* exists; Part II tells you the
> exact *shape* of every request/response, every permission decision per
> endpoint, the exact algorithms behind the non-trivial methods, the infra
> this app assumes exists, the order migrations must apply in, and the
> checklist to follow when adding anything new — so a new feature is
> consistent with everything already built instead of quietly diverging
> from it.

---

## 19. Exact request/response contracts

Convention used everywhere below: `R` = present in response, `W` = accepted
in request body (create and/or update), `R/W` = both, `—` = never appears
either direction (server-internal only, e.g. FKs resolved from URL/other
fields). Unless stated otherwise, list/retrieve responses are the plain
`ModelSerializer` field set — **no custom pagination class is configured
anywhere in this app**, so list endpoints return a plain JSON array (whatever
DRF's project-wide default pagination setting is applies uniformly; if the
project has none, expect an unpaginated array). No endpoint in this app
defines custom `filter_backends`/`ordering_fields` — the only filtering
available is the `?campus=<id>` query param every `CampusMemberScopedMixin`
subclass honors, plus the specific query params called out per endpoint
below. Every endpoint requires `IsAuthenticated` at minimum (enforced at the
`permission_classes` level; an unauthenticated request gets DRF's standard
`401`).

### `POST /campuses/`
Request: `{name: str W, type: "school"|"college"|"coaching" W,
attendance_alert_threshold_percent: int W (default 75), fee_module_enabled: bool W (default false)}`
Response `201`: adds `id R, is_active: true R, created_by: {id, username,
first_name, last_name} R, created_at R`, **plus (G-2, new)
`verification_status: "pending" R, verified_by: null R, verified_at: null
R`** — read-only, never settable via this or a plain `PATCH`. **Side
effect**: a `StaffProfile` row (`role=ADMIN`) is created for the requester
in the same transaction — this is not reflected in the `Campus` response
body; a separate `GET /staff/?campus=<id>` call is needed to see it.
`GET /campuses/` → only campuses in `get_my_campus_ids(request.user)`.
`is_active` and `fee_module_enabled` ARE writable via `PATCH` by anyone who
can reach the object (i.e. after passing `IsCampusAdminOrPrincipal` — see
§20) — there is no separate action for toggling these; a plain `PATCH
/campuses/{id}/ {"fee_module_enabled": true}` is how a campus opts into the
fee module. `verification_status`/`verified_by`/`verified_at` are NOT
`PATCH`-able by anyone — the only way to change them is the two actions
below.

**`POST /campuses/{id}/approve/`** and **`.../reject/`** — **new, G-2**. No
body. Platform-admin only (`IsPlatformAdmin` — `403 {"detail": "..."}` for
anyone else, including a campus's own ADMIN `StaffProfile`). `404
{"detail": "Not found."}` if the campus doesn't exist (these two bypass
`get_queryset()`'s own-campuses scoping deliberately, since a platform
admin needs to see/act on ANY pending campus). `200` with the full
serialized campus on success — `verification_status` now `"approved"`/
`"rejected"`, `verified_by`/`verified_at` set.

### `POST /sessions/`
Request: `{campus: uuid W, name: str W, start_date: date W, end_date: date W,
is_current: bool W (default false)}`
Validation: `end_date` must be strictly after `start_date` → `400
{"non_field_errors": ["end_date must be after start_date."]}` (this is a
`serializers.ValidationError` raised as a bare string in `validate()`, so DRF
nests it under `non_field_errors`, not a per-field key).
`POST /sessions/{id}/set-current/` — no body. `403 {"detail": "Not allowed."}`
if not admin/principal of that session's campus. `200` with the full
serialized session (now `is_current: true`) on success.
`POST /sessions/{id}/rollover/` — no body. `403` same shape if not
admin/principal. On success: `202 {"detail": "Rollover queued.", "task_id":
"<celery-task-id>"}` if Celery's `.delay()` succeeds, else `200
{"carried_forward": N, "skipped": M}` (synchronous fallback — **note the
different status code and shape depending on whether Celery is actually
configured**; callers must handle both).

### `POST /classes/` (`SchoolClassViewSet`)
Request: `{campus: uuid W, department: uuid|null W, session: uuid W, name: str W}`
Validation errors (both field-scoped 400s):
`{"department": ["Doesn't belong to this campus."]}`,
`{"session": ["Doesn't belong to this campus."]}`.

### `POST /sections/`
Request: `{school_class: uuid W, name: str W}`.
**Permission resolution quirk**: the campus for the permission check is
derived from `school_class.campus_id`, NOT a `campus` field in the body (there
is no `campus` field on `Section` at all) — `IsCampusAdminOrPrincipal` calls
`SectionViewSet.get_campus_id_for_permission_check`, which looks up
`school_class` from the body.
Response `201`: also triggers (fire-and-forget, doesn't affect the response
body or status) `bridge.create_section_group`.

### `POST /subjects/`
Request: `{campus: uuid W, department: uuid|null W, name: str W, code: str W (blank ok)}`.

### `POST /rooms/`
Request: `{campus: uuid W, name: str W, is_virtual: bool W (default false)}`.

### `POST /staff/`
Request: `{campus: uuid W, user: uuid W, role: "admin"|"principal_hod"|
"class_teacher"|"subject_teacher"|"non_teaching" W, is_active: bool W (default true)}`.
Response adds `user_detail: {id, username, first_name, last_name} R`.
Only reachable by an existing admin/principal of that campus
(`IsCampusAdminOrPrincipal`) — there is no self-signup path. **`403
PermissionDenied("This campus is pending platform verification and can't
add staff yet.")` — new, G-2** — if `campus.verification_status !=
APPROVED`. Does not apply to the campus's own creator, whose first
`StaffProfile` row is created directly in `CampusViewSet.perform_create`,
bypassing this check entirely.

### `POST /class-teacher-assignments/`
Request: `{section: uuid W, staff: uuid W}`.
Validation: `400 "This staff member doesn't belong to this section's
campus."` (bare string → `non_field_errors`) if `staff.campus_id !=
section.school_class.campus_id`.
**DB-level**: a second `POST` for the same `section` (different `staff`) will
raise an unhandled `IntegrityError` (→ `500`) since `section` is a
`OneToOneField` and there is **no serializer-level pre-check for this** —
`[GOTCHA]` any future work touching this endpoint should add a friendlier
`validate()` check for "this section already has a class-teacher" before it
reaches the DB.

### `POST /subject-teacher-assignments/`
Request: `{section: uuid W, subject: uuid W, staff: uuid W}`. Server ignores
any client-supplied `status`/`approved_by`/`responded_at` (all
`read_only_fields`). Lands `status: "pending"`.
Response fields: `id, section, subject, staff, staff_detail
{StaffProfileSerializer}, subject_detail {SubjectSerializer}, approved_by,
status, responded_at`.
`POST /subject-teacher-assignments/{id}/approve/` and `.../reject/` — no
body. `403 {"detail": "Not allowed."}` if requester is neither that section's
class-teacher nor campus admin/principal. `200` with the updated row on
success — `approved_by` is now the deciding user's own `StaffProfile.id`.

### `POST /enrollments/`
Request: `{student: uuid W, section: uuid W, session: uuid W, roll_number: str W (blank ok), status: "active"|"transferred"|"graduated" W (default active)}`.
Validation: `400 {"session": ["This section doesn't belong to the given session."]}`
if `section.school_class.session_id != session.id`. **`403
PermissionDenied("This campus is pending platform verification and can't
enroll students yet.")` — new, G-2** — if the section's campus isn't
`verification_status=APPROVED`.
Response adds `student_detail R`.

### `GET /parent-links/` (read-only)
Visible rows: where `request.user` is the `parent`, OR the `student`, OR an
active staff member of that `campus`. No create/update/delete on this
endpoint at all (405 on any write verb).

### `POST /parent-links/verify/`
Request: `{campus: uuid W, token: str W}` (plain dict, not a serializer).
`400 {"detail": "token and campus are required."}` if either missing.
`404 {"detail": "Campus not found."}` if `campus` doesn't exist. **`403
{"detail": "This campus is pending platform verification and can't link
parents yet."}` — new, G-2** — if the campus isn't `verification_status=
APPROVED` (checked before the token is even resolved). `400 {"detail":
"Invalid or expired token."}` if `bridge.resolve_parent_from_token`
returns `(None, None)` — **identical response whether the token is actually
invalid or the bridge just isn't wired up yet** (§10). `201` with the full
`CampusParentLinkSerializer` payload on success.

### `POST /notices/`
Request: `{campus: uuid W, department: uuid|null W, school_class: uuid|null W,
section: uuid|null W, session: uuid W, title: str W, body: str W, pin_until:
datetime|null W}`. `posted_by` is always server-set.
`403 PermissionDenied("Only campus staff can post notices.")` if requester
has no active `StaffProfile` at that campus — note this is a DRF
`PermissionDenied` raised inside `perform_create`, not a `permission_classes`
rejection, so it still returns the standard DRF `403 {"detail": "Only campus
staff can post notices."}` shape.
Validation (400, field-scoped): whichever of `department`/`school_class`/
`section` is set, `"Doesn't belong to the given campus."` if its own campus
doesn't match.

### `POST /live-sessions/`
Request: `{section: uuid W, subject: uuid W, teacher: uuid W, scheduled_at: datetime W}`.
`status`/`room_id` are `read_only_fields` — supplying them in the body is
silently ignored, not rejected.
Validation (400, field-scoped): `subject`/`teacher` must belong to
`section.school_class.campus_id`.
Response `201`: `room_id` may be populated if `bridge.provision_video_room`
returned something — otherwise `""`.
`POST /live-sessions/{id}/start/` — `400 {"detail": "Only a scheduled session
can be started."}` if `status != "scheduled"`. `200` on success.
`POST /live-sessions/{id}/end/`, `.../cancel/` — no precondition, always `200`.

### `POST /time-slots/`
Request: `{campus: uuid W, day_of_week: int(1-7) W, start_time: time W,
end_time: time W, label: str W (blank ok)}`.
Validation: `400 "end_time must be after start_time."` (`non_field_errors`).

### `POST /timetable-entries/`
Request: `{section: uuid W, subject: uuid W, staff: uuid W, time_slot: uuid W,
room: uuid|null W, session: uuid W}`.
On a clash, response is `400` with the DRF-converted `message_dict` shape
from Django's `ValidationError` — i.e. `{"__all__": ["This staff member
already has an entry in this time slot."]}` (Django's `ValidationError`
raised with no field name lands under the `__all__` key, NOT
`non_field_errors` — **this is different from every other bare-string
`serializers.ValidationError` in this app**, because it goes through
`DjangoCleanValidationMixin` which re-raises `e.message_dict` verbatim,
whereas a native DRF `serializers.ValidationError("...")` string lands under
`non_field_errors`). Any future serializer touching model-level `clean()`
validation should expect this `__all__` shape, not assume `non_field_errors`.

### `POST /attendance/`
Request: `{enrollment: uuid W, date: date W, subject: uuid|null W, status:
"present"|"absent"|"late"|"leave" W}`. `marked_by` always server-set.
DB-level: a second `POST` for the same `(enrollment, date, subject)` →
unhandled `IntegrityError` (`500`) — **no serializer-level duplicate check
exists**, same gotcha class as `ClassTeacherAssignment` above. `[GOTCHA]`
`GET /attendance/summary/?enrollment=<id>&subject=<id, optional>` — `400
{"detail": "enrollment query param is required."}` if missing. `404
{"detail": "Not found."}` if the enrollment doesn't exist or the requester
isn't staff/the student/a linked parent. `200` body:
`{enrollment: uuid, subject: uuid|null, total: int, present: int, absent: int,
late: int, leave: int, percent: float}`.

### `POST /assignments/`
Request: `{section: uuid W, subject: uuid W, title: str W, description: str W
(blank ok), attachment: file|null W, due_date: date W, session: uuid W}`.
`posted_by` server-resolved from the requester's `StaffProfile` at that
campus — **can be `null` in the DB if the requester somehow has no
`StaffProfile` there**, since `.first()` on no match is `None` and the FK is
`SET_NULL`-nullable; `IsSectionSubjectStaffOrReadOnly` should normally prevent
this, but it's not double-enforced at the model layer. `[GOTCHA — same class
as above, not currently guarded twice]`
Response `201` also silently bulk-creates `AssignmentSubmission` rows (not
visible in this response) + a `Notice` + notifications — none of those appear
in the `Assignment` response body itself; a separate `GET
/assignment-submissions/?assignment=<id>` shows the roster.

### `GET/POST/PATCH /assignment-submissions/`
`POST` request: `{assignment: uuid W, student: uuid|omit W}` — `student`
should normally be omitted (server forces it to `request.user`); supplying a
different `student` → `403 PermissionDenied("You can only create your own submission.")`.
`PATCH` request (student/self path): `{file: file W}` (or nothing) — server
sets `submitted_at`/`status`, both read-only from the client's point of view.
`PATCH` request (staff/grade path): `{grade: str W, feedback: str W}`.
`403 PermissionDenied("You don't have access to this submission.")` if
neither the owning student nor staff of that campus. `403 PermissionDenied
("Only the student or their subject teacher/admin can update this
submission.")` if staff but not authorized for that section+subject. `PUT`
and `DELETE` → `405` (not in `http_method_names`).

### `POST /syllabus-units/`
Request: `{subject: uuid W, section: uuid W, session: uuid W, title: str W,
order: int W (default 0)}`. Response `201` also silently
`get_or_create`s a `SyllabusProgress` row (not shown in this response).

### `GET /syllabus-progress/` + `POST /syllabus-progress/{id}/mark-covered/`
No generic create/update/delete on the base route (`405`). `mark-covered` —
no body, `200` with `covered_on: today`, `covered_by: <requester's
StaffProfile id>`.

### `POST /exam-terms/`
Request: `{session: uuid W, name: str W, start_date: date W, end_date: date W}`.

### `POST /results/`
Request: `{enrollment: uuid W, subject: uuid W, exam_term: uuid W,
marks_obtained: decimal W, max_marks: decimal W, remarks: str W (blank ok)}`.
`entered_by` server-set. Validation: `400 {"marks_obtained": ["Cannot exceed
max_marks."]}`.
`GET /results/report-card/?enrollment=<id>&exam_term=<id>` — `400` if either
param missing, `404 {"detail": "Not found."}` if either id doesn't resolve or
requester isn't authorized. `200` body: `{enrollment: uuid, exam_term: uuid,
subjects: [{subject: uuid, subject_name: str, marks_obtained: float,
max_marks: float, remarks: str}, ...], total_obtained: float, total_max:
float, percentage: float}`.

### `POST /digital-id-cards/`
Request: `{user: uuid W, campus: uuid W, valid_until: date|null W}`.
`qr_token`/`issued_at` always server-set. `403 PermissionDenied("Only an
admin/principal can issue a card for someone else.")` if `user != request.user`
and requester isn't admin/principal there.

### `POST /fee-structures/`
Request: `{campus: uuid W, school_class: uuid|null W, session: uuid W, title:
str W, amount: decimal W, due_date: date W, is_active: bool W (default true)}`.
`403 PermissionDenied("The fee module is not enabled for this campus (design
doc §8 — opt-in only).")` if `campus.fee_module_enabled` is `False`.
`POST /fee-structures/{id}/generate-invoices/` — no body. **FEE-1**:
re-checks `fee_module_enabled` at call time (not just at
`FeeStructure`-creation time) — `403` with the same detail message if the
campus has since disabled the module. `200
{"invoices_created": int, "already_existed": int}` on success.

### `GET/POST/PATCH/... /fee-invoices/`
Note: `FeeInvoiceViewSet` is a **full** `ModelViewSet` (not restricted to
custom actions the way `FeePayment` is) — `status` is `read_only_fields`
(never client-writable, only `recompute_status()` changes it), but
`amount_due` IS plain-writable via `PATCH` by an admin/principal — there is
no guard preventing an admin from editing `amount_due` after payments already
exist against it. `[GOTCHA]` `amount_paid` is `ReadOnlyField` (computed
property). **FEE-1**: `perform_create` now also gated on
`fee_module_enabled` (previously only `FeeStructureViewSet`/
`.generate-invoices` were, so a direct `POST` here bypassed the gate — fixed).

### `POST /fee-payments/pay/` `[FEE-2 — REWRITTEN: no more ONLINE/confirm]`
Request: `{invoice: uuid W, amount: decimal W (default = invoice.amount_due,
must be a whole number of coins), gateway_reference: str W (default = fresh
uuid4 hex)}`.
`400 {"detail": "invoice is required."}` if missing/not found. `403 {"detail":
"The fee module is not enabled for this campus."}` if disabled (FEE-1).
`403 {"detail": "Not allowed."}` if requester is neither the invoice's
student nor a linked parent. `400 {"detail": "amount must be a valid
number."}` / `{"detail": "amount must be greater than zero."}` / `{"detail":
"Wallet payments must be a whole number of coins (no paise)."}` from
`_resolve_whole_coin_amount`. If `gateway_reference` matches an existing
`FeePayment`, returns that row unchanged, `200` (idempotent replay, no
second debit). Otherwise: on success, `201` with the full
`FeePaymentSerializer` payload, `status: "success"`, `payment_mode:
"wallet"`. **`402` insufficient balance (FEE-3)** — `{"detail": "Insufficient
coin balance to pay this fee.", "current_balance": int, "required": int,
"coins_needed": int, "action": "top_up_coins"}`; the `FeePayment` row is
kept as `status: "failed"` (not deleted — audit trail) with the same
`gateway_reference` still reserved, so a genuine client retry with that
reference collides cleanly rather than silently succeeding at a stale
amount.
`POST /fee-payments/record/` — Request: `{invoice: uuid W, amount: decimal W
(default = amount_due, must be a whole number of coins), payment_mode:
"cash"|"cheque"|"bank_transfer"|"other" W (default cash), notes: str W
(blank ok)}`. `403` if requester isn't staff/admin at that campus. `400
{"detail": "Use the pay action for wallet payments; record is for cash/
cheque/bank-transfer only."}` if `payment_mode: "wallet"` is supplied.
`201`, `status: "success"` immediately, `payer_role: "admin"`,
`recorded_by: request.user`.
**`POST /fee-payments/{id}/refund/` — new, FEE-4.** No body. `403` if
requester isn't staff/admin at that campus (same shape as `record`, not
self-serve like `pay`). `400 {"detail": "<ValueError message>"}` if the
payment isn't `SUCCESS`, isn't `payment_mode=WALLET`, or has no `paid_by`
on record. `200` with the full `FeePaymentSerializer` payload on success —
`status: "refunded"`, and the linked `FeeInvoice.recompute_status()` has
already run (so `GET`ing the invoice right after reflects the reversal).
**`GET`/`retrieve` on `fee-payments/` is allowed (`ListModelMixin`,
`RetrieveModelMixin`) — only create/update/delete are restricted to the 3
named actions (`pay`/`record`/`refund`).**

### `GET /analytics-snapshots/` + `.../latest/?campus=<id>`
Read-only throughout. `latest` — `400` if `campus` param missing, `404
{"detail": "No snapshot yet."}` if none exist for that campus, else `200`
with `{id, campus, session, computed_at, data: {avg_attendance_percent,
avg_marks_obtained, syllabus_completion_percent, active_enrollments}}`.

---

## 20. Full permission matrix (endpoint × role)

Legend: ✅ = allowed, ❌ = denied, **Any** = any authenticated user
(including non-members, before campus-scoping even applies — the row simply
won't appear in `list`, and `retrieve`/write on someone else's campus 404s or
403s per §19 above), `Staff*` = any active `StaffProfile` regardless of role
at that campus, `CT` = class-teacher of the specific section in question,
`ST(appr)` = APPROVED subject-teacher of the specific section+subject, `A/P`
= campus Admin or Principal-HOD (treated identically everywhere), `PA` =
**Platform Admin** (`is_staff`/`is_superuser` — new, G-2, independent of any
campus-scoped role).

| Endpoint (write ops) | Any | Student (self) | Parent (linked) | Staff* (unrelated role) | `CT` | `ST(appr)` | `A/P` | `PA` |
|---|---|---|---|---|---|---|---|---|
| `POST /campuses/` | ✅ (becomes A/P of it, `verification_status=pending`) | — | — | — | — | — | — | — |
| `PATCH /campuses/{id}/` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ (see `approve`/`reject` instead — `verification_status`/`verified_by`/`verified_at` are read-only even to A/P via PATCH) |
| **`.../approve/`, `.../reject/`** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **✅ — new (G-2), platform-admin ONLY, even a campus's own A/P is denied** |
| `POST /sessions/`, `/departments/`, `/classes/`, `/subjects/`, `/rooms/`, `/time-slots/`, `/timetable-entries/`, `/exam-terms/`, `/fee-structures/` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | — |
| `POST /staff/` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (**also requires campus `verification_status=approved` — G-2**, except the creator's own first row, set directly by `CampusViewSet.perform_create`) | — |
| `POST /sessions/{id}/set-current/`, `.../rollover/` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | — |
| `POST /sections/` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | — |
| `POST /class-teacher-assignments/` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | — |
| `POST /subject-teacher-assignments/` (create/request) | ✅ (any campus member incl. self) | — | — | ✅ | ✅ | ✅ | ✅ | — |
| `.../approve/`, `.../reject/` | ❌ | ❌ | ❌ | ❌ | ✅ (own section) | ❌ | ✅ (fallback) | — |
| `POST /enrollments/` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (**also requires campus `verification_status=approved` — G-2**) | — |
| `POST /parent-links/verify/` | ✅ (any authenticated, subject to a valid token **and** the campus being `verification_status=approved` — G-2) | — | — | — | — | — | — | — |
| `POST /notices/` (campus-wide / department / class scope) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | — |
| `POST /notices/` (single-section scope) | ❌ | ❌ | ❌ | ❌ | ✅ (own section only — **G-1**) | ❌ | ✅ | — |
| `POST /live-sessions/` | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ (own section+subject) | ✅ | — |
| `.../start/`, `.../end/`, `.../cancel/` | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ | — |
| `POST /attendance/` (`subject` given) | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ (own section+subject) | ✅ | — |
| `POST /attendance/` (`subject=null`, daily) | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ (never, even if approved elsewhere) | ✅ | — |
| `GET /attendance/summary/` | ❌ (404) | ✅ (own enrollment only) | ✅ (linked child only) | ✅ | ✅ | ✅ | ✅ | — |
| `POST /assignments/` | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ (own section+subject) | ✅ | — |
| `POST /assignment-submissions/` (own row, edge-case only) | ❌ | ✅ (self only) | ❌ | — | — | — | — | — |
| `PATCH /assignment-submissions/{id}/` (submit) | ❌ | ✅ (own row only) | ❌ | ❌ | ❌ | ❌ | ❌ | — |
| `PATCH /assignment-submissions/{id}/` (grade) | ❌ | ❌ | ❌ | ❌ | ✅ (own section) | ✅ (own section+subject) | ✅ | — |
| `POST /syllabus-units/` | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ (own section+subject) | ✅ | — |
| `.../mark-covered/` | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ (own section+subject) | ✅ | — |
| `POST /results/` | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ (own section+subject) | ✅ | — |
| `GET /results/report-card/` | ❌ (404) | ✅ (own only) | ✅ (linked, campus-scoped) | ✅ | ✅ | ✅ | ✅ | — |
| `POST /digital-id-cards/` (own) | ✅ (any campus member, own card) | — | — | — | — | — | — | — |
| `POST /digital-id-cards/` (someone else's) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | — |
| `POST /fee-payments/pay/` | ❌ | ✅ (own invoice; whole-coin amount only, `402` on insufficient balance — **FEE-2/FEE-3**) | ✅ (linked child's invoice, same rules) | ❌ | ❌ | ❌ | ✅ (also passes the `allow_self_pay` OR-branch as staff) | — |
| `POST /fee-payments/record/` | ❌ | ❌ | ❌ | ✅ (any active staff) | ✅ | ✅ | ✅ | — |
| **`POST /fee-payments/{id}/refund/`** | ❌ | ❌ | ❌ | **✅ — new (FEE-4), any active staff** | ✅ | ✅ | ✅ | — |
| Read (`GET list`/`retrieve`) on everything | Own-scope only per `get_my_campus_ids` | ✅ (rows tied to self) | ✅ (rows tied to linked child) | ✅ (campus-wide, for that campus) | ✅ | ✅ | ✅ | ✅ (campuses table only, via `approve`/`reject`'s own lookup — `PA` has no special read access anywhere else) |

**`confirm`'s old GOTCHA no longer applies** — the action was removed
entirely along with the Razorpay-style pending window (FEE-2), so there is
nothing left to "confirm" on someone else's behalf. Kept as a note in §16
so nobody re-derives the old gap thinking it's still live.

---

## 21. Algorithm pseudocode (zero-ambiguity reference for the non-trivial logic)

### `AcademicSession.save()`
```
def save(self, *args, **kwargs):
    if self.is_current:
        atomic:
            AcademicSession.objects.filter(campus_id=self.campus_id, is_current=True)
                .exclude(pk=self.pk)
                .update(is_current=False)
            super().save(*args, **kwargs)
    else:
        super().save(*args, **kwargs)
```
Runs on EVERY save (create or update), not just via an API action — this is
what makes the DB constraint unbreakable from a shell/bulk-import too.

### `TimetableEntry.clean()` (called from `save()` via `full_clean()`)
```
def clean(self):
    clashing = TimetableEntry.objects.filter(
        session=self.session, time_slot=self.time_slot
    ).exclude(pk=self.pk)
    if clashing.filter(staff=self.staff).exists():
        raise ValidationError("This staff member already has an entry in this time slot.")
    if clashing.filter(section=self.section).exists():
        raise ValidationError("This section already has an entry in this time slot.")
    if self.room_id and clashing.filter(room=self.room).exists():
        raise ValidationError("This room is already booked in this time slot.")
```
Order matters for which message a client sees when multiple clashes exist
simultaneously: staff-clash is checked first, then section-clash, then
room-clash — only the FIRST match raises (Django's `ValidationError` stops at
the first `raise`).

### `FeeInvoice.recompute_status()`
```
def recompute_status(self):
    paid = self.amount_paid                      # property: Sum(payments where status=SUCCESS)
    if fee_structure.due_date and paid < amount_due:
        new_status = OVERDUE if today() > fee_structure.due_date else PENDING
    else:
        new_status = PAID
    if paid > 0 and paid < amount_due:
        new_status = PARTIAL                      # overrides the OVERDUE/PENDING branch above
    if new_status != self.status:
        self.status = new_status
        self.save(update_fields=["status"])
```
**Read this precedence carefully when extending it**: `PARTIAL` is checked
LAST and unconditionally overrides whatever the first `if` computed, as long
as `0 < paid < amount_due` — so a genuinely overdue-but-partially-paid
invoice ends up `PARTIAL`, never `OVERDUE`. If "overdue AND partial" ever
needs to be distinguishable, that's a new status value, not a reordering of
this function (reordering would change already-tested behaviour).

### `FeePayment.mark_success()`
```
def mark_success(self):
    if self.status != SUCCESS:
        self.status = SUCCESS
        self.save(update_fields=["status"])
        self.invoice.recompute_status()
```
Idempotent by construction — a second call is a no-op because the `if` guard
fails.

### `compute_attendance_summary(enrollment, subject=None)`
```
def compute_attendance_summary(enrollment, subject=None):
    qs = Attendance.objects.filter(enrollment=enrollment)
    if subject is not None:
        qs = qs.filter(subject=subject)
    total = qs.count()
    counts = {status: 0 for status in [PRESENT, ABSENT, LATE, LEAVE]}
    for status in qs.values_list("status", flat=True):
        counts[status] += 1
    attended = counts[PRESENT] + counts[LATE]
    percent = round(attended / total * 100, 2) if total else 0.0
    return {enrollment: id, subject: id_or_null, total, present, absent, late, leave, percent}
```
`subject=None` scans ALL attendance rows for the enrollment (daily + every
subject's period-wise marks combined) — this is the one place "what counts
as attended" is defined; never re-derive this elsewhere (`check_low_attendance`
calls this exact function, doesn't reimplement the %-age).

### `tasks.rollover_session(campus_id, new_session_id)`
```
def rollover_session(campus_id, new_session_id):
    new_session = AcademicSession.objects.filter(pk=new_session_id, campus_id=campus_id).first()
    if not new_session:
        return {"detail": "Session not found for this campus.", carried_forward: 0, skipped: 0}
    old_enrollments = StudentEnrollment.objects.filter(
        section__school_class__campus_id=campus_id, status=ACTIVE
    ).exclude(session_id=new_session_id)
    atomic:
        for enrollment in old_enrollments:
            target_section = Section.objects.filter(
                school_class__campus_id=campus_id,
                school_class__session_id=new_session_id,
                school_class__name=enrollment.section.school_class.name,   # exact string match
                name=enrollment.section.name,                              # exact string match
            ).first()
            if not target_section:
                skipped += 1
                continue
            _, created = StudentEnrollment.objects.get_or_create(
                student=enrollment.student, section=target_section, session=new_session,
                defaults={roll_number: enrollment.roll_number, status: ACTIVE},
            )
            carried_forward += int(created)
    return {carried_forward, skipped}
```
**Matching is by exact `name` string equality on BOTH `SchoolClass.name` and
`Section.name`** — a rename between sessions (e.g. `"Class 10"` →
`"Grade 10"`) will silently show up as `skipped`, not an error. Any future
"smarter" rollover matching (fuzzy match, an explicit mapping table) is new
work, not a tweak to this function's existing contract — the existing
behaviour is locked in by `test_plain_orm_create_does_not_violate_unique_constraint`
and the rollover action's own test coverage.

### `tasks.check_low_attendance()`
```
def check_low_attendance():
    for campus in Campus.objects.filter(is_active=True):
        for enrollment in StudentEnrollment.objects.filter(
            section__school_class__campus=campus, status=ACTIVE
        ):
            summary = compute_attendance_summary(enrollment)   # subject=None — OVERALL percent only
            if summary.total == 0:
                continue                                        # never alerts on zero-record enrollments
            if summary.percent < campus.attendance_alert_threshold_percent:
                recipients = [enrollment.student] + CampusParentLink.objects.filter(
                    student=enrollment.student, campus=campus
                ).values_list("parent")
                bridge.notify(recipients, LOW_ATTENDANCE_ALERT, ...)
    return {alerted: count}
```
Runs against **overall** attendance (`subject=None`), never per-subject — a
student failing only one subject's attendance won't trigger this unless
their combined percentage also drops below threshold. A per-subject variant
would be new work.

---

## 22. Infra / settings this app assumes exist (must be true for the above to run)

- `INSTALLED_APPS` includes `"campus"`, and the project's root `urls.py`
  `include()`s `campus.urls` at some prefix (this doc never assumes a
  specific mount path — every path above is relative to wherever that
  `include()` puts it).
- `login.User` is the project's `AUTH_USER_MODEL` (or at least importable as
  `from login.models import User`) — every cross-app FK in `campus/models.py`
  points here.
- Django REST Framework is installed and configured; this app relies on
  whatever project-wide `DEFAULT_PAGINATION_CLASS` / `DEFAULT_PERMISSION_CLASSES`
  / `DEFAULT_AUTHENTICATION_CLASSES` are set globally — nothing in `campus`
  overrides pagination, and `IsAuthenticated` is applied per-view here (not
  assumed to be a project-wide default).
- **File storage**: `Assignment.attachment` uploads to
  `campus/assignments/`, `AssignmentSubmission.file` to
  `campus/submissions/` — both relative to whatever `MEDIA_ROOT`/default
  storage backend the project has configured (local disk, S3, etc.); nothing
  campus-specific is configured here.
- **Celery**: `campus/tasks.py` degrades gracefully if Celery isn't
  installed at all, but for `check_low_attendance`,
  `send_assignment_due_reminders`, and `refresh_analytics_snapshot` to
  actually run on a schedule, the **project's** `CELERY_BEAT_SCHEDULE` needs
  entries for them — none exist inside `campus` itself. Suggested (not yet
  added anywhere) entries:
  ```python
  CELERY_BEAT_SCHEDULE = {
      "campus-daily-attendance-check": {
          "task": "campus.tasks.check_low_attendance",
          "schedule": crontab(hour=6, minute=0),   # once daily, before school hours
      },
      "campus-assignment-due-reminders": {
          "task": "campus.tasks.send_assignment_due_reminders",
          "schedule": crontab(hour=7, minute=0),
      },
      # refresh_analytics_snapshot takes (campus_id, session_id) args, so it
      # can't be a single flat beat entry without a wrapper task that loops
      # active campuses/current sessions — that wrapper does not exist yet.
  }
  ```
- **Fee wallet (FEE-2, corrects a stale claim in an earlier pass of this
  doc)**: `campus` fee is paid FROM the same `user_profile.CoinLedger`-backed
  `User.coin` wallet `liveclass` already uses — there is no Razorpay (or any
  other) gateway call anywhere in `FeePaymentViewSet.pay` today; the debit
  happens directly via `CoinLedger.objects.record_transaction()`, and the
  `Mode.ONLINE`/`POST /fee-payments/{id}/confirm/` gateway stand-in this
  paragraph used to describe has been **removed** (see §7's Fee module
  section and §19's `POST /fee-payments/pay/` contract for the current
  shape). `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` remain in settings.py, but
  only for `liveclass.CoinPurchase` to top the wallet up in the first
  place — `campus` never reads those settings.
- **Throttle rates (B-4, new — see §12a)**: the project's
  `DEFAULT_THROTTLE_RATES` needs an entry for each of the 4 scopes
  `throttles.py` defines — none exist inside `campus` itself, and a
  `ScopedRateThrottle` with an unregistered scope raises
  `ImproperlyConfigured` on the very first request to that action:
  ```python
  DEFAULT_THROTTLE_RATES = {
      # ...existing scopes (session_join, coin_withdrawal, etc.)...
      "campus_fee_payment": "20/hour",
      "campus_live_session_join": "30/hour",
      "campus_notice_post": "10/hour",
      "campus_parent_link_verify": "5/hour",
  }
  ```
  (Rates above are suggested, matching the tightness of the nearest existing
  `liveclass`/`message` scope each was modeled on in §12a — not yet actually
  added anywhere; tune to real traffic once this ships.)
- `core.classroom_chat_bridge` and `core.models.Notification` must exist
  with the exact signatures `bridge.py` expects (§10) before any of the 4
  bridge functions do real work.

---

## 23. Migration dependency graph

If regenerating migrations from scratch, models must be created in an order
where every FK target already exists. A valid single-migration-per-model
order (or a valid ordering within one big initial migration) is:

```
1. Campus                         (FK: login.User)
2. AcademicSession                (FK: Campus)
3. Department                     (FK: Campus)
4. SchoolClass                    (FK: Campus, Department, AcademicSession)
5. Section                        (FK: SchoolClass)
6. Subject                        (FK: Campus, Department)
7. Room                           (FK: Campus)
8. StaffProfile                   (FK: Campus, login.User)
9. ClassTeacherAssignment         (FK: Section [OneToOne], StaffProfile)
10. SubjectTeacherAssignment      (FK: Section, Subject, StaffProfile ×2)
11. StudentEnrollment             (FK: login.User, Section, AcademicSession)
12. CampusParentLink              (FK: Campus, login.User ×2)
13. Notice                        (FK: Campus, Department, SchoolClass, Section, AcademicSession, login.User)
14. CampusLiveSession             (FK: Section, Subject, StaffProfile)
15. TimeSlot                      (FK: Campus)
16. TimetableEntry                (FK: Section, Subject, StaffProfile, TimeSlot, Room, AcademicSession)
17. Attendance                    (FK: StudentEnrollment, Subject, login.User)
18. Assignment                    (FK: Section, Subject, StaffProfile, AcademicSession)
19. AssignmentSubmission          (FK: Assignment, login.User)
20. SyllabusUnit                  (FK: Subject, Section, AcademicSession)
21. SyllabusProgress              (FK: SyllabusUnit [OneToOne], StaffProfile)
22. ExamTerm                      (FK: AcademicSession)
23. ResultEntry                   (FK: StudentEnrollment, Subject, ExamTerm, StaffProfile)
24. DigitalIDCard                 (FK: login.User, Campus)
25. FeeStructure                  (FK: Campus, SchoolClass, AcademicSession)
26. FeeInvoice                    (FK: StudentEnrollment, FeeStructure)
27. FeePayment                    (FK: FeeInvoice, login.User ×2)
28. CampusAnalyticsSnapshot       (FK: Campus, AcademicSession)
```
This is the actual dependency order already baked into the codebase (every
model only ever references a model defined above it in `models.py`) — any
new model should slot in at the point its own FK targets are satisfied, not
be appended blindly to the end of the file.

---

## 24. Checklist for adding anything new to this app

Every existing endpoint in this app follows the same handful of
conventions. When adding a new model/endpoint, go through this list —
skipping a step is how a new feature quietly diverges from everything
already built:

1. **Base model**: extend `CampusBaseModel` (UUID pk) unless there's a
   specific reason not to (there isn't one anywhere in this app so far).
2. **Campus-scoping field**: decide the `__`-lookup path from the new model
   to `Campus` (direct FK vs. through `section__school_class__campus` etc.)
   — this becomes the new ViewSet's `campus_field_path`.
3. **Cross-FK campus consistency**: if the model has more than one FK that
   each imply a campus (e.g. `subject` + `section` both eventually point at
   a `Campus`), add a serializer `validate()` that checks they agree —
   every existing model with >1 such FK does this (`SchoolClass`, `Subject`,
   `ClassTeacherAssignment`, `SubjectTeacherAssignment`, `Notice`,
   `CampusLiveSession`).
4. **Permission class**: pick the closest existing fit —
   `IsCampusAdminOrPrincipal` (structural/admin-only),
   `IsSectionSubjectStaffOrReadOnly` (section+subject content),
   `IsAuthenticated`-only + a manual `is_any_active_staff`/role check inside
   `perform_create` (like `Notice`/`SubjectTeacherAssignment`'s create path),
   or a bespoke check (like the fee-module gate). Don't invent a new
   `BasePermission` subclass unless none of the two existing ones fit even
   with a custom `get_campus_id_for_permission_check`/
   `get_section_subject_for_permission_check` override.
5. **Server-set fields**: any field that records "who did this"
   (`marked_by`, `posted_by`, `entered_by`, `approved_by`, `recorded_by`,
   `qr_token`, `covered_by`, `status` transitions) is `read_only_fields` at
   the serializer level AND resolved server-side in `perform_create`/
   `perform_update`/a custom action — never trust a client-supplied value
   for these, matching every existing instance of this pattern.
6. **Notify?**: if the new flow is something a student/parent/staff member
   would want to know about, add a `NotifTypes` constant in `bridge.py` (also
   noting it in this doc's §9 table) and call `bridge.notify(...)` — never
   import `core.models.Notification` directly.
7. **DB-level duplicate/uniqueness guard**: if "one row per X" is a business
   rule, add the `UniqueConstraint` — don't rely on serializer-level checks
   alone (see the `ClassTeacherAssignment`/`Attendance` gotchas in §19 for
   what happens when this is skipped: an ugly `500` instead of a clean `400`
   the one time it collides). If you can't add the DB constraint for some
   reason, at minimum add the serializer-level `validate()` check so it's a
   `400`, and note the gap explicitly in this doc the way §19 does.
8. **Test it the way `tests.py` already does**: one test for the happy
   path, one for "an unrelated user is rejected", one for any
   uniqueness/clash rule, one for any auto-triggered side effect (notice
   creation, notification, bulk pre-create). Add the new test names to §17
   so they stay part of the locked-in-behaviour record.
9. **Update this document in the same change**: a new model/endpoint/task
   that isn't reflected in Part I (§1-18) and Part II (§19-24) breaks the
   "single source of truth" premise this whole doc exists for. Update the
   relevant section(s) — including the migration order in §23 and the
   permission matrix row in §20 — as part of landing the feature, not as a
   follow-up.