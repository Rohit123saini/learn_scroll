# tuitionclass/bridge.py
"""
`tuitionclass`'s ONLY door into the unified `assigments` app (Task 12) —
mirrors `campus/bridge.py`'s Task 11 `create_assigments()` /
`get_assigments_submissions()` pair field-for-field. Same golden rule as
that file and as `assigments/models.py`'s own docstring: `assigments`
never imports `tuitionclass.*`, and `tuitionclass` never imports
`assigments.models.assigments`/`assigmentsSubmission` directly anywhere
outside this module.

`assigments` is a confirmed, fully-built sibling app (Tasks 6-10) — same
position `campus/bridge.py` treats it in (top-level `try`/`except
ImportError`-free imports inside each function, no lazy-degrade). If
`assigments` genuinely isn't installed, `tuitionclass`'s own assigments
feature has nothing to fall back to, so a hard import error is the
honest failure mode here too.

ROSTER SOURCE (this task's acceptance checklist item — "verify ho chuka
hai real model se"): campus resolves its roster from
`StudentEnrollment(status=ACTIVE)`. tuitionclass has no enrollment table —
"who currently has access to this classroom" is already a defined,
single concept here: `PassPurchase(status=SUCCESS, is_active=True,
expires_at__gt=now)` against the classroom's passes. This is the EXACT
filter `assigmentsViewSet.perform_create` (old tuitionclass/views.py) was
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
those two extra fields to snapshot, `tuitionclass` genuinely does not (no
roll-number/enrollment-number concept in this marketplace). Both
`assigmentsSubmission.roll_number`/`enrollment_no` are `blank=True`, so
this assumes `create_context_assigments()` treats a missing key the same
way `campus`'s own §5 GAP note already documents for blank
`enrollment_no` (defaults to `""`, not a `KeyError`). If `assigments/
bridge.py`'s actual signature requires those keys unconditionally, this
needs a one-line fix (`roster` dict below gains `"roll_number": ""`,
`"enrollment_no": ""`) — flagging here rather than padding in blind
placeholder values that would misrepresent what tuitionclass actually has.

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
`tuitionclass` already established for `create_assigments()` above:
  - Roster source is `PassPurchase`, not `StudentEnrollment` — same
    query `create_assigments()` above uses, for the same reason (no
    enrollment table here).
  - No `subject` concept, no `testseries_paid_allowed`-equivalent gate:
    `tuitionclass` has no per-classroom "is paid testseries even allowed"
    flag the way `Campus.testseries_paid_allowed` gates campus (§16/
    Task 19 in campus_app_design.md) — confirmed by
    testseries_app_reference.md §3.6 itself ("No server-side force on
    `is_paid` for tuitionclass (unlike campus)"). So `is_paid`/
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
    """[Task 12] Creates a tuitionclass assigments via the unified
    `assigments` app instead of the now-deprecated `tuitionclass.assigments`
    model. Delegates row creation + roster bulk-pre-create to
    `assigments.bridge.create_context_assigments()` — the same entry
    point `campus.bridge.create_assigments()` (Task 11) already uses.

    `context_type="classroom"` / `context_id=classroom.id` is the
    (context_type, context_id) shape `assigments` stores opaquely;
    `tuitionclass`-side code (this bridge, and `views.py`'s thin proxy) is
    the only thing that ever turns `context_id` back into a real
    `Classroom`.

    `due_date`: the unified `assigments.due_date` is a `DateField`
    (old `tuitionclass.assigments.due_date` was a `DateTimeField`). This
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
        source=assigmentsSource.TUITIONCLASS,
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
    posted against a tuitionclass `classroom`, via `assigments.bridge.
    get_submissions_for_context()` — never a direct
    `assigments.models.assigmentsSubmission` import from `tuitionclass`
    code outside this bridge module.

    Unfiltered by permission, same contract `campus.bridge.
    get_assigments_submissions()` documents for that function: the
    caller (`views.py`) is responsible for any further teacher/student-
    scoped narrowing — the exact same split
    `assigmentsSubmissionViewSet.get_queryset` (old tuitionclass/views.py)
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
    questions=None,
    draft=False,
):
    """[Task 12 / testseries_app_reference.md §3.6] Creates a tuitionclass
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
    has no subject-shaped slot at all, and `tuitionclass` has no `Subject`
    model to attach one from in the first place.

    `is_paid`/`price_coins`: TASK 9.1 — a class test is ALWAYS free. Whatever
    the caller passes is ignored and forced to `False`/`0` here (and again in
    `testseries.bridge.create_context_testseries()` and `TestSeries.save()`,
    all driven by `testseries.policy.ALWAYS_FREE_SOURCES`). The parameters stay
    in the signature only so existing callers keep working. A class already
    sells access through its pass; the tests inside it are never charged twice.

    `questions`: passed straight through, uninspected, to
    `create_context_testseries()` — shape/validation is entirely
    `testseries`'s own `Question.full_clean()` contract (same as campus's
    version documents).

    Roster / announcement (TASK 9.2): this function no longer hands a roster to
    `create_context_testseries()`. Once the series is committed, `testseries`
    calls back into `on_testseries_published()` below (registered through
    `settings.TESTSERIES_PUBLISH_HOOKS`), which posts the notice-board entry and
    notifies the pass holders — one place, exactly once, for BOTH this path and
    the viewset's `publish` action.

    Returns the created `testseries.models.TestSeries` instance.
    """
    from testseries.bridge import create_context_testseries
    from testseries.models import TestSeries

    # TASK 9.1: always free for students, whatever the caller sent.
    is_paid, price_coins = False, 0

    return create_context_testseries(
        source=TestSeries.Source.TUITIONCLASS,
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
        roster=None,  # TASK 9.2: announced by on_testseries_published() instead
        draft=draft,  # [T2] draft=True: questions optional, added later by the teaching staff
    )


# ---------------------------------------------------------------------------
# TASK 9.2 — "a test was published in this class" -> notice board + notification.
#
# Called by `testseries.bridge.announce_series_published()` (never directly by
# tuitionclass views) through `settings.TESTSERIES_PUBLISH_HOOKS["classroom"]`,
# at most once per series (`TestSeries.announced_at` is claimed first). The hook
# receives a plain dict, never a `TestSeries` — tuitionclass still imports no
# testseries model for this.
#
# Idempotent on its own as well: the notice is created with
# get_or_create(classroom, source_type="testseries", source_id=<series id>),
# backed by a partial UNIQUE constraint, and the notification / push only go out
# when THIS call created the notice — so a retry after a half-failed run can
# never produce a second notice or a second round of notifications.
# ---------------------------------------------------------------------------
NOTICE_SOURCE_TESTSERIES = "testseries"


def _classroom_from_context_id(context_id):
    """`TestSeries.context_id` is a UUID; an integer classroom pk is stored as
    `uuid.UUID(int=pk)`, so `.int` is the pk again."""
    import uuid

    from .models import Classroom

    try:
        pk = context_id.int if isinstance(context_id, uuid.UUID) else int(context_id)
    except (TypeError, ValueError, AttributeError):
        return None
    return Classroom.objects.filter(pk=pk).first()


def _testseries_notice_message(payload) -> str:
    from django.utils import timezone as dj_tz

    lines = []
    description = (payload.get("description") or "").strip()
    if description:
        lines.append(description[:300])
    starts_at, ends_at = payload.get("starts_at"), payload.get("ends_at")
    fmt = "%d %b %Y, %I:%M %p"
    if starts_at:
        lines.append(f"Starts: {dj_tz.localtime(starts_at).strftime(fmt)}")
    if ends_at:
        lines.append(f"Ends: {dj_tz.localtime(ends_at).strftime(fmt)}")
    facts = []
    if payload.get("duration_minutes"):
        facts.append(f"{payload['duration_minutes']} min")
    if payload.get("total_marks"):
        facts.append(f"{payload['total_marks']} marks")
    if facts:
        lines.append("Duration / marks: " + " · ".join(facts))
    lines.append("Free for everyone in this class. Open the Tests tab to start.")
    return "\n".join(lines)


def on_testseries_published(payload):
    """Post the notice-board entry for a newly published class test and notify
    the class. See the section comment above for the contract."""
    import uuid

    from django.db import IntegrityError, transaction

    from . import notifications
    from .models import Notice, PassPurchase

    classroom = _classroom_from_context_id(payload.get("context_id"))
    if classroom is None:
        logger.warning("on_testseries_published: classroom for context %r not found", payload.get("context_id"))
        return None

    series_id = uuid.UUID(str(payload["series_id"]))
    title = f"New test: {payload['title']}"[:150]
    message = _testseries_notice_message(payload)
    defaults = {
        "posted_by_id": payload["creator_id"],
        "title": title,
        "message": message,
        "priority": Notice.Priority.NORMAL,
        # The notice stops being relevant when the test window closes.
        "expires_at": payload.get("ends_at"),
    }
    try:
        with transaction.atomic():
            notice, created = Notice.objects.get_or_create(
                classroom=classroom, source_type=NOTICE_SOURCE_TESTSERIES, source_id=series_id, defaults=defaults,
            )
    except IntegrityError:  # lost a race with a concurrent announce: the winner's row is the notice
        notice = Notice.objects.get(classroom=classroom, source_type=NOTICE_SOURCE_TESTSERIES, source_id=series_id)
        created = False

    if not created or not payload.get("notify_roster", True):
        return notice

    student_ids = list(
        PassPurchase.objects.filter(
            class_pass__classroom=classroom,
            status=PassPurchase.Status.SUCCESS,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .exclude(student_id=payload["creator_id"])
        .values_list("student_id", flat=True)
        .distinct()
    )
    if not student_ids:
        return notice

    body = f"{classroom.title}: {payload['title']}"[:200]
    notifications.notify_testseries_published(
        classroom=classroom, series_id=series_id, notice_id=notice.id,
        title="New test in your class", body=body, user_ids=student_ids,
    )
    # Push is queued (one task for the whole class). A broker problem must never
    # fail the publish that already succeeded.
    try:
        from .tasks import notify_testseries_published_push

        notify_testseries_published_push.delay(
            student_ids, "New test in your class", body,
            notifications.testseries_notification_data(
                classroom_id=classroom.id, series_id=series_id, notice_id=notice.id,
            ),
        )
    except Exception:
        logger.exception("could not queue testseries push for classroom %s", classroom.pk)
    return notice


def user_accessible_testseries_context_ids(*, user, context_type):
    """[ADVANCED test series — access control] Which tuition classrooms may
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


# ---------------------------------------------------------------------------
# TASK 12 — Refer & Earn hooks consumed by `testseries` (see
# settings.TESTSERIES_REFERRAL_HOOKS and testseries/access.py). All keyword-
# only, all return plain values (ids / ints / str) — no model instances cross
# the app boundary.
# ---------------------------------------------------------------------------
def referral_code_for_user(*, user):
    """The user's permanent random referral code."""
    from .models import referral_code_for_user as _code

    return _code(user.id)


def referral_resolve_referrer(*, buyer):
    """Id of the user who should earn commission on `buyer`'s purchase right
    now, or None. Requires an ACTIVE (unexpired) attribution and a clean
    attribution-time fraud check (self / circular / inactive referrer)."""
    from user_profile.fraud import check_referral_abuse

    from .models import ReferralAttribution

    attribution = ReferralAttribution.active_for(buyer)
    if attribution is None:
        return None
    ok, _reason = check_referral_abuse(attribution.referrer, buyer, coins=0)
    return attribution.referrer_id if ok else None


def referral_pay_testseries_commission(
    *, referrer_id, referee_id, purchase_id, series_id, gross_coins, percent, coins
):
    """Credit `coins` to the referrer for a released test-series purchase and
    write the ReferralCommission ledger row. Returns coins paid (0 when
    blocked). Idempotent on `testseries_referral:<purchase_id>`; velocity /
    circularity rules re-checked HERE (payout time), not just at attribution
    time, because the escrow can sit for days."""
    from django.contrib.auth import get_user_model
    from django.db import IntegrityError, transaction

    from core.models import Notification
    from core.services import create_notification
    from user_profile.fraud import check_referral_abuse
    from user_profile.models import CoinLedger

    from .models import ReferralAttribution, ReferralCommission

    reference = f"testseries_referral:{purchase_id}"
    existing = ReferralCommission.objects.filter(reference=reference).first()
    if existing is not None:
        return existing.commission_coins if existing.status == ReferralCommission.Status.PAID else 0

    User = get_user_model()
    referrer = User.objects.filter(pk=referrer_id).first()
    referee = User.objects.filter(pk=referee_id).first()
    base = {
        "referrer_id": referrer_id,
        "referee_id": referee_id,
        "kind": ReferralCommission.Kind.TESTSERIES,
        "source_id": str(series_id),
        "gross_coins": gross_coins,
        "percent": percent,
        "commission_coins": coins,
    }

    ok, reason = check_referral_abuse(referrer, referee, coins=coins)
    if not ok:
        try:
            ReferralCommission.objects.create(
                reference=reference, status=ReferralCommission.Status.BLOCKED, block_reason=reason, **base
            )
        except IntegrityError:
            pass
        return 0

    try:
        with transaction.atomic():
            CoinLedger.objects.record_transaction(
                user=referrer,
                transaction_type=CoinLedger.TransactionType.REFERRAL_COMMISSION,
                amount=coins,
                reference=reference,
            )
            ReferralCommission.objects.create(reference=reference, status=ReferralCommission.Status.PAID, **base)
    except IntegrityError:
        # Concurrent release already paid it — report what that row says.
        row = ReferralCommission.objects.filter(reference=reference).first()
        return row.commission_coins if row and row.status == ReferralCommission.Status.PAID else 0

    ReferralAttribution.mark_converted(referee)
    try:
        create_notification(
            recipient=referrer,
            notif_type=Notification.NotifType.GENERIC,
            title="Referral commission earned",
            message=f"You earned {coins} coins because someone you referred bought a test series.",
            data={"kind": "referral_commission", "coins": coins},
        )
    except Exception:  # noqa: BLE001 — a notification must never undo a payout
        pass
    return coins


def user_editable_testseries_context_ids(*, user, context_type):
    """[T2 — question management] Which tuition classrooms may `user` EDIT the
    questions of a class test series for (besides the series' own creator)?

    The classroom's teacher, plus its co-teachers and moderators. Teaching
    assistants (`ClassroomStaff.Role.TA`), students and pass holders are never
    editors. Returned as UUIDs because `TestSeries.context_id` is a UUIDField
    (an integer classroom pk is stored as `uuid.UUID(int=pk)`).
    """
    import uuid

    if context_type != "classroom" or not getattr(user, "is_authenticated", False):
        return set()

    from .models import Classroom, ClassroomStaff

    pks = set(Classroom.objects.filter(teacher=user).values_list("id", flat=True))
    pks |= set(
        ClassroomStaff.objects.filter(
            user=user, role__in=(ClassroomStaff.Role.CO_TEACHER, ClassroomStaff.Role.MODERATOR),
        ).values_list("classroom_id", flat=True)
    )
    return {pk if isinstance(pk, uuid.UUID) else uuid.UUID(int=int(pk)) for pk in pks}
