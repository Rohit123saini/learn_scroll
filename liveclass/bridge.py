# liveclass/bridge.py
"""
`liveclass`'s ONLY door into the unified `assigments` app (Task 12) —
mirrors `campus/bridge.py`'s Task 11 `create_assigments()` /
`get_assigments_submissions()` pair field-for-field. Same golden rule as
that file and as `assigments/models.py`'s own docstring: `assigments`
never imports `liveclass.*`, and `liveclass` never imports
`assigments.models.assigments`/`assigmentsSubmission` directly anywhere
outside this module.

`assigments` is a confirmed, fully-built sibling app (Tasks 6-10) — same
position `campus/bridge.py` treats it in (top-level `try`/`except
ImportError`-free imports inside each function, no lazy-degrade). If
`assigments` genuinely isn't installed, `liveclass`'s own assigments
feature has nothing to fall back to, so a hard import error is the
honest failure mode here too.

ROSTER SOURCE (this task's acceptance checklist item — "verify ho chuka
hai real model se"): campus resolves its roster from
`StudentEnrollment(status=ACTIVE)`. liveclass has no enrollment table —
"who currently has access to this classroom" is already a defined,
single concept here: `PassPurchase(status=SUCCESS, is_active=True,
expires_at__gt=now)` against the classroom's passes. This is the EXACT
filter `assigmentsViewSet.perform_create` (old liveclass/views.py) was
already using to fan out `assigments_POSTED` notifications — not a new
query invented for this bridge, the same one, now confirmed against the
real `PassPurchase` model (fields: `class_pass__classroom`, `status`,
`is_active`, `expires_at`, `student`). `SessionParticipant` was
considered and rejected as the roster source: it's per-`ClassSession`
attendance, but an `assigments` here is classroom-scoped (there is no
per-session `context_type` on the unified model — `assigments/models.py`
enumerates exactly `"section" | "classroom" | ""`), so a session-level
roster would be the wrong grain.

PAID/UNPAID: this file never asks, and structurally cannot — the unified
`assigments.models.assigments` has no `is_paid`/`price` field at all
(assigments/models.py §4, "structurally impossible", same as
`campus.CampusLiveSession`). This satisfies this task's "paid/unpaid
kabhi nahi poocha jaata" checklist item by construction, not by a check
added here.

ASSUMPTION FLAGGED, NOT GUESSED AROUND: `assigments.bridge.
create_context_assigments()`'s `roster` parameter shape was only ever
observed via `campus/bridge.py`'s call site, which passes
`{"user_id", "roll_number", "enrollment_no"}` per entry — `campus` has
those two extra fields to snapshot, `liveclass` genuinely does not (no
roll-number/enrollment-number concept in this marketplace). Both
`assigmentsSubmission.roll_number`/`enrollment_no` are `blank=True`, so
this assumes `create_context_assigments()` treats a missing key the same
way `campus`'s own §5 GAP note already documents for blank
`enrollment_no` (defaults to `""`, not a `KeyError`). If `assigments/
bridge.py`'s actual signature requires those keys unconditionally, this
needs a one-line fix (`roster` dict below gains `"roll_number": ""`,
`"enrollment_no": ""`) — flagging here rather than padding in blind
placeholder values that would misrepresent what liveclass actually has.

NEEDED TO CLOSE THIS ASSUMPTION: `assigments/bridge.py` itself (not
provided this pass — only `assigments/models.py` and `campus/bridge.py`
were).

--------------------------------------------------------------------
ADDED THIS PASS — `create_testseries()` (testseries_app_reference.md
§3.6, was flagged "STILL OPEN"). Mirrors `campus.bridge.
create_testseries()`'s pattern (per that function's own description in
`campus_app_design.md` — the real `campus/bridge.py` source wasn't part
of this pass either, so this is written against that description, same
"flag the source, don't guess past it" posture the rest of this file
already uses for `assigments/bridge.py`) — with the two adaptations
`liveclass` already established for `create_assigments()` above:
  - Roster source is `PassPurchase`, not `StudentEnrollment` — same
    query `create_assigments()` above uses, for the same reason (no
    enrollment table here).
  - No `subject` concept, no `testseries_paid_allowed`-equivalent gate:
    `liveclass` has no per-classroom "is paid testseries even allowed"
    flag the way `Campus.testseries_paid_allowed` gates campus (§16/
    Task 19 in campus_app_design.md) — confirmed by
    testseries_app_reference.md §3.6 itself ("No server-side force on
    `is_paid` for liveclass (unlike campus)"). So `is_paid`/
    `price_coins` are passed straight through as the teacher's own
    choice at creation time, no force-reset.

ROSTER SHAPE DIFFERENCE FROM `create_assigments()`, WORTH FLAGGING:
`assigments.bridge.create_context_assigments()`'s `roster` param is a
list of plain dicts (`{"user_id": ...}`, see above) — but `testseries.
bridge.create_context_testseries()`'s own docstring says its `roster`
param is "iterable of `login.User`", i.e. real user instances, used only
to fan out the `TESTSERIES_POSTED` notification. These are genuinely
different shapes for the same-looking concept across the two sibling
bridges — not a typo in one of them, just two APIs that evolved
independently. Building the wrong shape for either would either crash
(`AttributeError`/`TypeError` server-side) or silently no-op the
notification fan-out (iterating dicts where `.email`/notification
plumbing expects a real user object) rather than raise anything obvious
— so this function deliberately resolves real `User` rows here, not the
`{"user_id": ...}` dicts `create_assigments()` above builds.
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def create_assigments(*, classroom, posted_by, title, description="", attachment=None, due_date=None):
    """[Task 12] Creates a liveclass assigments via the unified
    `assigments` app instead of the now-deprecated `liveclass.assigments`
    model. Delegates row creation + roster bulk-pre-create to
    `assigments.bridge.create_context_assigments()` — the same entry
    point `campus.bridge.create_assigments()` (Task 11) already uses.

    `context_type="classroom"` / `context_id=classroom.id` is the
    (context_type, context_id) shape `assigments` stores opaquely;
    `liveclass`-side code (this bridge, and `views.py`'s thin proxy) is
    the only thing that ever turns `context_id` back into a real
    `Classroom`.

    `due_date`: the unified `assigments.due_date` is a `DateField`
    (old `liveclass.assigments.due_date` was a `DateTimeField`). This
    function does NOT silently `.date()` a datetime passed in — a caller
    passing a `datetime` gets whatever `assigments`'s own field
    validation does with it, so a caller that meant to keep a
    time-of-day component finds out immediately instead of it being
    dropped here without a trace. `views.py`'s thin proxy is responsible
    for deciding what "due date" even means going forward (date-only,
    per the new model) and passing a `date`.

    Returns the created `assigments.models.assigments` instance.
    """
    from assigments.bridge import create_context_assigments
    from assigments.models import assigmentsSource

    from .models import PassPurchase

    roster = [
        {"user_id": student_id}
        for student_id in (
            PassPurchase.objects.filter(
                class_pass__classroom=classroom,
                status=PassPurchase.Status.SUCCESS,
                is_active=True,
                expires_at__gt=timezone.now(),
            )
            .values_list("student_id", flat=True)
            .distinct()
        )
    ]
    return create_context_assigments(
        source=assigmentsSource.LIVECLASS,
        context_type="classroom",
        context_id=classroom.id,
        posted_by=posted_by,
        title=title,
        description=description,
        attachment=attachment,
        due_date=due_date,
        roster=roster,
    )


def get_assigments_submissions(classroom):
    """[Task 12] Returns every `assigments.assigmentsSubmission` row
    posted against a liveclass `classroom`, via `assigments.bridge.
    get_submissions_for_context()` — never a direct
    `assigments.models.assigmentsSubmission` import from `liveclass`
    code outside this bridge module.

    Unfiltered by permission, same contract `campus.bridge.
    get_assigments_submissions()` documents for that function: the
    caller (`views.py`) is responsible for any further teacher/student-
    scoped narrowing — the exact same split
    `assigmentsSubmissionViewSet.get_queryset` (old liveclass/views.py)
    already enforced via `_can_manage_classroom` (full list for a
    classroom manager) vs. `student=user` (everyone else), which the
    thin proxy must replicate on top of this.
    """
    from assigments.bridge import get_submissions_for_context

    return get_submissions_for_context(context_type="classroom", context_id=classroom.id)


def create_testseries(
    *,
    classroom,
    creator,
    title,
    description="",
    is_paid=False,
    price_coins=0,
    duration_minutes=None,
    attempts_allowed=1,
    questions,
):
    """[Task 12 / testseries_app_reference.md §3.6] Creates a liveclass
    test series via the unified `testseries` app — the `create_testseries`
    counterpart to `create_assigments()` above, same "one function is the
    app boundary" pattern, delegating to `testseries.bridge.
    create_context_testseries()` the same way `campus.bridge.
    create_testseries()` already does for campus (source="campus").

    `context_type="classroom"` / `context_id=classroom.id` — same opaque
    pointer shape `create_assigments()` above already establishes for
    this app; `testseries` never resolves it back to a real `Classroom`
    itself (golden rule).

    No `subject` parameter — same reasoning `campus.bridge.
    create_testseries()` documents for itself: `create_context_testseries()`
    has no subject-shaped slot at all, and `liveclass` has no `Subject`
    model to attach one from in the first place.

    `is_paid`/`price_coins`: passed straight through as the teacher's own
    choice at creation time — UNLIKE `campus.bridge.create_testseries()`,
    there is no `testseries_paid_allowed`-equivalent flag anywhere in
    `liveclass` to force these to `False`/`0` against (confirmed absent —
    not just unwired), so no force-reset happens here. If `liveclass` ever
    wants a per-classroom or per-teacher paid/unpaid gate the way campus
    has one at the `Campus` level, that's a new field + a new check here,
    not something this function can silently infer today.

    `questions`: passed straight through, uninspected, to
    `create_context_testseries()` — shape/validation is entirely
    `testseries`'s own `Question.full_clean()` contract (same as campus's
    version documents).

    Roster: same `PassPurchase(status=SUCCESS, is_active=True,
    expires_at__gt=now)` source `create_assigments()` above uses — but
    unlike that function's `{"user_id": ...}` dict shape,
    `create_context_testseries()`'s own `roster` param wants an iterable
    of real `login.User` instances (used only to fan out the
    `TESTSERIES_POSTED` notification — `testseries` fires it itself once
    handed this roster, `liveclass` doesn't and can't fire it a second
    time). Resolved into actual `User` rows here, not left as bare ids,
    for exactly that reason — see this module's docstring for why the two
    bridges' roster shapes genuinely differ rather than one being a typo.

    Returns the created `testseries.models.TestSeries` instance.
    """
    from login.models import User
    from testseries.bridge import create_context_testseries
    from testseries.models import TestSeries

    from .models import PassPurchase

    student_ids = (
        PassPurchase.objects.filter(
            class_pass__classroom=classroom,
            status=PassPurchase.Status.SUCCESS,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .values_list("student_id", flat=True)
        .distinct()
    )
    roster = list(User.objects.filter(id__in=student_ids))

    return create_context_testseries(
        source=TestSeries.Source.LIVECLASS,
        context_type="classroom",
        context_id=classroom.id,
        creator=creator,
        title=title,
        description=description,
        is_paid=is_paid,
        price_coins=price_coins,
        duration_minutes=duration_minutes,
        attempts_allowed=attempts_allowed,
        questions=questions,
        roster=roster,
    )


def user_accessible_testseries_context_ids(*, user, context_type):
    """[ADVANCED test series — access control] Which live classrooms may
    `user` see and attempt test series for?

    Same semantics as `Classroom.is_enrolled()` / `_can_view_classroom_internals()`:
    the classroom's teacher, any `ClassroomStaff` row, or anyone who has EVER
    held a successful pass (active or lapsed). Returned as UUIDs because
    `TestSeries.context_id` is a `UUIDField` — an integer classroom pk is stored
    as `uuid.UUID(int=pk)` (that is what `create_testseries()` above does
    implicitly), so the same mapping is applied here.
    """
    import uuid

    if context_type != "classroom" or not getattr(user, "is_authenticated", False):
        return set()

    from .models import Classroom, ClassroomStaff, PassPurchase

    pks = set(Classroom.objects.filter(teacher=user).values_list("id", flat=True))
    pks |= set(ClassroomStaff.objects.filter(user=user).values_list("classroom_id", flat=True))
    pks |= set(
        PassPurchase.objects.filter(student=user, status=PassPurchase.Status.SUCCESS, is_active=True)
        .values_list("class_pass__classroom_id", flat=True)
    )
    return {pk if isinstance(pk, uuid.UUID) else uuid.UUID(int=int(pk)) for pk in pks}
