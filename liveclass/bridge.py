# liveclass/bridge.py
"""
`liveclass`'s ONLY door into the unified `assignment` app (Task 12) —
mirrors `campus/bridge.py`'s Task 11 `create_assignment()` /
`get_assignment_submissions()` pair field-for-field. Same golden rule as
that file and as `assignment/models.py`'s own docstring: `assignment`
never imports `liveclass.*`, and `liveclass` never imports
`assignment.models.Assignment`/`AssignmentSubmission` directly anywhere
outside this module.

`assignment` is a confirmed, fully-built sibling app (Tasks 6-10) — same
position `campus/bridge.py` treats it in (top-level `try`/`except
ImportError`-free imports inside each function, no lazy-degrade). If
`assignment` genuinely isn't installed, `liveclass`'s own assignment
feature has nothing to fall back to, so a hard import error is the
honest failure mode here too.

ROSTER SOURCE (this task's acceptance checklist item — "verify ho chuka
hai real model se"): campus resolves its roster from
`StudentEnrollment(status=ACTIVE)`. liveclass has no enrollment table —
"who currently has access to this classroom" is already a defined,
single concept here: `PassPurchase(status=SUCCESS, is_active=True,
expires_at__gt=now)` against the classroom's passes. This is the EXACT
filter `AssignmentViewSet.perform_create` (old liveclass/views.py) was
already using to fan out `ASSIGNMENT_POSTED` notifications — not a new
query invented for this bridge, the same one, now confirmed against the
real `PassPurchase` model (fields: `class_pass__classroom`, `status`,
`is_active`, `expires_at`, `student`). `SessionParticipant` was
considered and rejected as the roster source: it's per-`ClassSession`
attendance, but an `Assignment` here is classroom-scoped (there is no
per-session `context_type` on the unified model — `assignment/models.py`
enumerates exactly `"section" | "classroom" | ""`), so a session-level
roster would be the wrong grain.

PAID/UNPAID: this file never asks, and structurally cannot — the unified
`assignment.models.Assignment` has no `is_paid`/`price` field at all
(assignment/models.py §4, "structurally impossible", same as
`campus.CampusLiveSession`). This satisfies this task's "paid/unpaid
kabhi nahi poocha jaata" checklist item by construction, not by a check
added here.

ASSUMPTION FLAGGED, NOT GUESSED AROUND: `assignment.bridge.
create_context_assignment()`'s `roster` parameter shape was only ever
observed via `campus/bridge.py`'s call site, which passes
`{"user_id", "roll_number", "enrollment_no"}` per entry — `campus` has
those two extra fields to snapshot, `liveclass` genuinely does not (no
roll-number/enrollment-number concept in this marketplace). Both
`AssignmentSubmission.roll_number`/`enrollment_no` are `blank=True`, so
this assumes `create_context_assignment()` treats a missing key the same
way `campus`'s own §5 GAP note already documents for blank
`enrollment_no` (defaults to `""`, not a `KeyError`). If `assignment/
bridge.py`'s actual signature requires those keys unconditionally, this
needs a one-line fix (`roster` dict below gains `"roll_number": ""`,
`"enrollment_no": ""`) — flagging here rather than padding in blind
placeholder values that would misrepresent what liveclass actually has.

NEEDED TO CLOSE THIS ASSUMPTION: `assignment/bridge.py` itself (not
provided this pass — only `assignment/models.py` and `campus/bridge.py`
were).
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def create_assignment(*, classroom, posted_by, title, description="", attachment=None, due_date=None):
    """[Task 12] Creates a liveclass assignment via the unified
    `assignment` app instead of the now-deprecated `liveclass.Assignment`
    model. Delegates row creation + roster bulk-pre-create to
    `assignment.bridge.create_context_assignment()` — the same entry
    point `campus.bridge.create_assignment()` (Task 11) already uses.

    `context_type="classroom"` / `context_id=classroom.id` is the
    (context_type, context_id) shape `assignment` stores opaquely;
    `liveclass`-side code (this bridge, and `views.py`'s thin proxy) is
    the only thing that ever turns `context_id` back into a real
    `Classroom`.

    `due_date`: the unified `Assignment.due_date` is a `DateField`
    (old `liveclass.Assignment.due_date` was a `DateTimeField`). This
    function does NOT silently `.date()` a datetime passed in — a caller
    passing a `datetime` gets whatever `assignment`'s own field
    validation does with it, so a caller that meant to keep a
    time-of-day component finds out immediately instead of it being
    dropped here without a trace. `views.py`'s thin proxy is responsible
    for deciding what "due date" even means going forward (date-only,
    per the new model) and passing a `date`.

    Returns the created `assignment.models.Assignment` instance.
    """
    from assignment.bridge import create_context_assignment
    from assignment.models import AssignmentSource

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
    return create_context_assignment(
        source=AssignmentSource.LIVECLASS,
        context_type="classroom",
        context_id=classroom.id,
        posted_by=posted_by,
        title=title,
        description=description,
        attachment=attachment,
        due_date=due_date,
        roster=roster,
    )


def get_assignment_submissions(classroom):
    """[Task 12] Returns every `assignment.AssignmentSubmission` row
    posted against a liveclass `classroom`, via `assignment.bridge.
    get_submissions_for_context()` — never a direct
    `assignment.models.AssignmentSubmission` import from `liveclass`
    code outside this bridge module.

    Unfiltered by permission, same contract `campus.bridge.
    get_assignment_submissions()` documents for that function: the
    caller (`views.py`) is responsible for any further teacher/student-
    scoped narrowing — the exact same split
    `AssignmentSubmissionViewSet.get_queryset` (old liveclass/views.py)
    already enforced via `_can_manage_classroom` (full list for a
    classroom manager) vs. `student=user` (everyone else), which the
    thin proxy must replicate on top of this.
    """
    from assignment.bridge import get_submissions_for_context

    return get_submissions_for_context(context_type="classroom", context_id=classroom.id)