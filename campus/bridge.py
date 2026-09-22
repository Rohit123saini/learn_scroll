# campus/bridge.py
"""
`campus`'s ONLY door into `core` / `message` — the project's golden rule
(`campus_app_design.md`, top of file) is that `campus` never imports
those apps' models directly, the same way `liveclass` already routes
through `core` instead of touching `message` internals.

STATUS (this pass — all three gaps this file previously flagged as
blocked are now resolved):
  - ✅ DONE — `core.models.NotifType` now has every value
    `campus_app_design.md` §10 asks for (verified against the real
    `core/models.py` upload). `NotifTypes` below is deliberately KEPT
    as a plain-string mirror rather than switched to a direct
    `core.models.NotifType` reference — that would reintroduce the
    exact `campus` → `core.models` import this module exists to avoid.
    Every value in `NotifTypes` below has been checked
    character-for-character against `core.models.NotifType` and
    matches.
  - ✅ FIXED — `notify()`'s `Notification.objects.create(...)` call was
    passing `user=`/`body=`, but the real model's fields are
    `recipient=`/`message=`. Left as-is this would have raised
    `TypeError` on every single call once `core.models` became
    importable — i.e. this path was never exercised against the real
    model. Fixed below.
  - ✅ WIRED (this pass) — `core.classroom_chat_bridge.
    create_section_group`/`...provision_video_room` now exist for real
    (added this same pass — see that module's functions 10/11). Both
    are confirmed, established dependencies now, the same position
    `create_assigments()`/`get_assigments_submissions()` below are
    already in re: `assigments` — so both import at module level, no
    lazy import, no `except ImportError` degrade.
    ⚠️ CORRECTION (this pass) — a prior revision of this file removed
    the `except ImportError` wrapper but left the `from core.
    classroom_chat_bridge import ...` / `from assigments.bridge import
    ...` / `from testseries.bridge import ...` lines as LOCAL imports
    inside each function body, contradicting this very docstring's
    "import at module level" claim. Functionally the difference matters:
    a local import only raises on the *first call* to that function, so
    a broken/missing dependency would pass Django startup and any health
    check, then 500 the first time a real user hits it. Moved to actual
    module-level imports below (top of file) to match what this
    docstring always claimed — a missing `core`/`assigments`/`testseries`
    now fails at Django startup (import time), same as any other hard
    dependency, not silently deferred to first request. Verified this
    doesn't introduce a cycle: `core.classroom_chat_bridge`'s own
    cross-app imports (`campus.models`, `message.models`) are
    deliberately local/deferred on ITS end specifically so apps like this
    one CAN import it at module level without a circular-import error —
    see that module's own docstring, item 2. `assigments/bridge.py`'s
    top-level imports don't reach back into `campus` either, so the same
    holds there. `testseries.bridge`'s import graph wasn't part of this
    pass (no `testseries/bridge.py` upload this time) — carried over the
    same assumption since `create_testseries()`'s own docstring already
    treats it as a confirmed, non-circular sibling dependency; re-verify
    against that file if `testseries` ever gains a reason to import
    `campus`.
    `campus.models.Section` also gained the `chat_group_enabled`/
    `linked_conversation_id` pair `create_section_group()` needs to
    persist its result — a migration is required for that field (see
    its own comment in models.py; run `manage.py makemigrations campus`,
    this pass can't generate that migration file without the live
    project state).
  - ✅ RESOLVED (this pass) — `resolve_parent_from_token`'s shape
    mismatch. `CampusParentLink.parent` is a real `login.User` FK, but
    `core.classroom_chat_bridge.resolve_parent_from_token` (unchanged,
    still correct for its own contract) returns a `ParentTokenResolution`
    with no `parent_user` — that flow's parents never get a real login.
    Decision taken: auto-create (and reuse) a lightweight, non-loginable
    "shadow" `User` per `ParentAccessCode`, the first time that code is
    ever verified — every device/session verifying the SAME code is the
    same real-world parent sharing it (message/views_parent.py: one
    `ParentAccessCode` per student+label, many `ParentToken` devices
    under it), so keying the shadow user off `parent_access_code.id`
    keeps this idempotent — re-verifying the same code never creates a
    second shadow user. See `_get_or_create_shadow_parent_user()` below.
    NOTE: this uses a `username` naming convention
    (`parent_shadow_<access_code_id>`) to mark shadow rows rather than a
    dedicated `is_shadow_parent` field on `login.User`, since
    `login/models.py` wasn't part of this pass — flagging this as the
    one open follow-up: add a real boolean field there and swap the
    lookup below to use it once that file is available, instead of a
    naming-convention check.

TASK 11 ADDITION: `create_assigments()` / `get_assigments_submissions()`
below are a DIFFERENT kind of bridge call than `notify()`/`resolve_
parent_from_token()`/`create_section_group()`/`provision_video_room()`
above. `assigments` is a confirmed, fully-built sibling app (Tasks
6-10), the same kind of established dependency `assigments/bridge.py`
itself treats `core.services` as (a top-level import, no degrade) —
same treatment the other four functions above now get too. So these two
import `assigments.bridge`/`assigments.models` directly at module
level, not lazily, and do not degrade to a no-op on `ImportError` — if
`assigments` genuinely isn't installed, campus's own assigments feature
has nothing to fall back to anyway, so a hard import error at startup
is the honest failure mode, not a silently-neutered feature.
"""
import logging

from assigments.bridge import create_context_assigments, get_submissions_for_context
from assigments.models import assigmentsSource
from core.classroom_chat_bridge import create_section_group as _create_section_group
from core.classroom_chat_bridge import provision_video_room as _provision_video_room
from core.classroom_chat_bridge import resolve_parent_from_token as _resolve_parent_from_token
from core.async_utils import dispatch_after_commit, in_celery_worker
from core.models import Notification
from core.tasks import create_notification_rows, create_notifications
from testseries.bridge import create_context_testseries, get_attempts_for_context
from testseries.models import TestSeries

logger = logging.getLogger(__name__)


class NotifTypes:
    """
    Mirrors the `NotifType` additions `campus_app_design.md` §10 asks
    for on `core.models.NotifType`. Kept as plain string constants here
    (not a real `TextChoices`/enum tied to `core`) so `campus` never
    imports `core.models` just to reference a notification type — the
    golden rule this whole module exists to enforce. Every value here
    must match, verbatim, whatever gets added to `core.models.NotifType`
    once that lands, so `bridge.notify(notif_type=NotifTypes.X, ...)`
    keeps working unchanged after the TODO above is done.
    """

    CAMPUS_SESSION_SCHEDULED = "campus_session_scheduled"
    CAMPUS_SESSION_LIVE = "campus_session_live"
    LOW_ATTENDANCE_ALERT = "low_attendance_alert"
    assigments_POSTED_CAMPUS = "assigments_posted_campus"
    assigments_DUE_REMINDER = "assigments_due_reminder"
    RESULT_PUBLISHED = "result_published"
    FEE_DUE_REMINDER = "fee_due_reminder"
    STAFF_assigments_APPROVED = "staff_assigments_approved"
    STAFF_assigments_REJECTED = "staff_assigments_rejected"
    # NOTICE_POSTED already exists on core.models.NotifType per the
    # design doc — reused as-is, not redefined here.
    NOTICE_POSTED = "notice_posted"

    # F-3 (Task 14) — ADDED. `tasks.check_attendance_streak_rewards()`
    # and `check_assigments_ontime_streak_rewards()` already referenced
    # `NotifTypes.CAMPUS_REWARD_EARNED`, but it did not exist on this
    # class — every call to `bridge.notify(notif_type=NotifTypes.
    # CAMPUS_REWARD_EARNED, ...)` in either task raised `AttributeError`
    # before this fix, on top of the separate `ImportError` those same
    # tasks hit from `campus/services.py` missing the two streak
    # functions (see that file's own F-3 note). Same "must match
    # core.models.NotifType verbatim once added there" contract every
    # other value on this class already documents above.
    CAMPUS_REWARD_EARNED = "campus_reward_earned"


def create_section_group(section, actor):
    """
    Create the `message.Group` (+ its `Conversation`) for a `Section`,
    the same pattern `liveclass.create_classroom_group()` already uses
    for its own classrooms — via `core.classroom_chat_bridge`, never a
    direct `message` import from this app.

    `core.classroom_chat_bridge.create_section_group` is now a
    confirmed, wired dependency (see module STATUS above) — no more
    `except ImportError` degrade; a missing `core` here is a real
    startup failure, same as `assigments` below, not a silently
    neutered feature.

    Returns the created (or, if one already exists for this section,
    the existing) `message.Group` instance. Raises `ValueError` if
    `actor` isn't this section's assigned class-teacher — see
    `core.classroom_chat_bridge.create_section_group()`'s own docstring.
    """
    return _create_section_group(section, actor)


def notify(*, users, notif_type, title, body='', data=None):
    """
    Single entry point for `campus` to push a `core.Notification`.
    `users` is an iterable of user instances/ids. `notif_type` should be
    one of the `NotifType` values `campus_app_design.md` §10 lists.

    `core.models.Notification` is now a confirmed, wired dependency
    (see module STATUS above) — no more `except ImportError` degrade.

    Fire-and-forget, same "never raises" contract as
    `core.services.create_notification`: from a request, the rows are
    created by a Celery worker (`core.create_notifications`, with retries)
    once the surrounding transaction commits; from inside a Celery task
    they're created directly (no extra hop). A failed bell row can never
    fail the campus action that triggered it. Returns `[]` — callers never
    use the created rows.
    """
    recipient_ids = [getattr(user, "pk", user) for user in users]
    if not recipient_ids:
        return []
    try:
        if in_celery_worker():
            failed = create_notification_rows(recipient_ids, notif_type, title, body, data)
            if failed:
                logger.error("campus.bridge.notify: %d notification(s) failed (type=%s).", len(failed), notif_type)
        else:
            dispatch_after_commit(create_notifications, recipient_ids, notif_type, title, body, data)
    except Exception:
        logger.exception("campus.bridge.notify failed (type=%s).", notif_type)
    return []


def provision_video_room(live_session, actor):
    """
    Provision a video room for a `CampusLiveSession`, reusing whatever
    LiveKit/WebRTC infra `liveclass`/`message` already have — via
    `core.classroom_chat_bridge.provision_video_room`, never a direct
    `liveclass`/`message` import from this app (design doc §4).

    `core.classroom_chat_bridge.provision_video_room` is now a
    confirmed, wired dependency (see module STATUS above) — no more
    `except ImportError` degrade.

    Returns the room-name string that bridge function hands back
    (stored directly into `CampusLiveSession.room_id`). See that
    function's own docstring for why it returns a stable room name
    rather than a LiveKit token — per-participant tokens are minted
    separately, at actual join time, not here at scheduling time.
    """
    return _provision_video_room(live_session, actor=actor)


def _get_or_create_shadow_parent_user(parent_access_code):
    """
    `campus.CampusParentLink.parent` is a real `login.User` FK, but the
    underlying `message.ParentAccessCode`/`ParentToken` flow has no
    concept of a real parent `User` at all — parents there authenticate
    by token+code, never a login (see module docstring STATUS).

    Creates (once) and reuses a lightweight, non-loginable "shadow"
    `User` row per `ParentAccessCode` — every device (`ParentToken`)
    that verifies under the SAME code is the same real-world parent/
    family sharing that one code (one `ParentAccessCode` per
    student+label, many devices under it), so keying the shadow user
    off `parent_access_code.id` keeps this idempotent: re-verifying the
    same code, from any device, at any time, always resolves to the
    same shadow user — never creates a duplicate.

    Marked via a `username` naming convention
    (`parent_shadow_<access_code_id>`) rather than a dedicated
    `is_shadow_parent` field on `login.User`, since `login/models.py`
    wasn't available to add one this pass (see module STATUS) — treat
    this as the interim identification method, not the permanent one.

    The shadow row is deliberately unusable for normal login
    (`set_unusable_password()` — no password ever validates for it).
    """
    from login.models import User  # local import — cross-app, same reasoning as message.models below

    username = f"parent_shadow_{parent_access_code.id}"
    user, created = User.objects.get_or_create(username=username)
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


def resolve_parent_from_token(token):
    """
    Verify a parent-access token against `message`'s existing
    ParentAccessCode/ParentToken flow — via `core.classroom_chat_bridge.
    resolve_parent_from_token`, never a direct `message` import from
    this app (see `CampusParentLink`'s model docstring).

    `core.classroom_chat_bridge.resolve_parent_from_token` is now a
    confirmed, wired dependency (see module STATUS above) — no more
    `except ImportError` degrade.

    Returns a `(parent_user, student_user)` tuple on success, or
    `(None, None)` if the token is invalid/expired — `ParentLinkVerifyView`
    treats this as a 400, "Invalid or expired token".

    `parent_user` is a shadow `User` (see
    `_get_or_create_shadow_parent_user()` above), not a real login —
    that's the resolved answer to this module's former SHAPE MISMATCH
    note: the underlying token flow has no real parent identity to
    hand back, so one is synthesized here, deterministically, per
    `ParentAccessCode`.
    """
    resolution = _resolve_parent_from_token(token)
    if resolution is None:
        return None, None

    parent_user = _get_or_create_shadow_parent_user(resolution.parent_access_code)
    return parent_user, resolution.student


def create_assigments(*, section, subject, posted_by, title, description="", attachment=None, due_date=None):
    """[Task 11] Creates a campus assigments via the unified `assigments`
    app instead of the now-deprecated `campus.assigments` model (see that
    model's own docstring in models.py). Delegates the actual row
    creation + roster bulk-pre-create to `assigments.bridge.
    create_context_assigments()` — the same entry point `liveclass` is
    expected to use too (§1, §3 of assigments_app_design.md). `campus`
    resolves its own roster (`StudentEnrollment`) here, since
    `assigments` itself has no concept of what a `Section` or an
    enrollment is (opaque `context_id`, per that app's golden rule).

    `context_type="section"` / `context_id=section.id` is the
    (context_type, context_id) shape `assigments` stores opaquely;
    `campus`-side code (this bridge, and views.py's thin proxy) is the
    only thing that ever turns `context_id` back into a real `Section`.

    `subject` has nowhere to live on the unified `assigments` model — a
    `Section` spans multiple subjects, so unlike `session` (always
    derivable from `section.school_class.session_id`, so the caller
    never needs to pass or store it separately) `subject_id` genuinely
    needs its own slot. Passed via `extra_data` into `assigments.data`
    (see that parameter's own docstring on `create_context_assigments`)
    rather than inventing a new model field on the unified app for a
    campus-only concept — `views.py`'s thin-proxy serializer reads it
    back out of `assigments.data["subject_id"]` to reconstruct the old
    API's `subject` field.

    Returns the created `assigments.models.assigments` instance.
    """
    from .models import StudentEnrollment

    roster = [
        {
            "user_id": enrollment.student_id,
            "roll_number": enrollment.roll_number,
            "enrollment_no": enrollment.enrollment_no,
        }
        for enrollment in StudentEnrollment.objects.filter(
            section=section, status=StudentEnrollment.Status.ACTIVE
        )
    ]
    return create_context_assigments(
        source=assigmentsSource.CAMPUS,
        context_type="section",
        context_id=section.id,
        posted_by=posted_by,
        title=title,
        description=description,
        attachment=attachment,
        due_date=due_date,
        roster=roster,
        extra_data={"subject_id": str(subject.id)},
    )


def get_assigments_submissions(section):
    """[Task 11] Returns every `assigments.assigmentsSubmission` row for
    a campus `section`, via `assigments.bridge.
    get_submissions_for_context()` — never a direct
    `assigments.models.assigmentsSubmission` import from campus code
    outside this bridge module. Unfiltered by permission, same contract
    `get_submissions_for_context()` itself documents: the caller (views.py)
    is responsible for any further staff/student-scoped narrowing.
    """
    return get_submissions_for_context(context_type="section", context_id=section.id)


def create_testseries(*, section, creator, title, description="", duration_minutes=None,
                       attempts_allowed=1, questions, is_paid=False, price_coins=0):
    """[Task 13] Creates a campus test series via the unified
    `testseries` app — the same "one function is the app boundary"
    pattern `create_assigments()` above already uses for `assigments`,
    and the same pattern `testseries/bridge.py`'s own module docstring
    says it exists for (`campus`/`liveclass` bridge modules call
    `create_context_testseries()`, never `testseries` models directly).
    `testseries` is a confirmed, fully-built sibling app for this task —
    its own bridge module docstring spells out exactly this calling
    contract for `source="campus"` — not an unverified dependency, so
    same as `create_assigments()`'s own Task 11 addition reasoning, this
    imports `testseries.bridge`/`testseries.models` as hard imports
    below, no lazy-import/`ImportError` degrade.

    `is_paid`/`price_coins` — [Task 19 — ORG_VS_INDIVIDUAL_MATRIX] WIRED
    this pass. Both default to `False`/`0`, so every existing caller
    (positional-kwargs-only, so nothing breaks) keeps today's
    always-free behavior unchanged. Gated on `Campus.
    testseries_paid_allowed` (campus/models.py), resolved via `section.
    school_class.campus` — the same relation `SectionViewSet.
    get_campus_id_for_permission_check()` (views.py) already walks. If
    that flag is `False` on this section's campus, `is_paid`/
    `price_coins` are force-reset to `False`/`0` here regardless of what
    the caller passed — this function does not trust the caller on this
    point.

    This is now THE enforcement point for "can this campus run paid test
    series" — `TestSeries.save()` (testseries/models.py) no longer
    re-checks `source == CAMPUS` at all, and can't: `testseries` never
    imports `campus` models (golden rule), so it has no way to see this
    flag. `campus.views.TestSeriesViewSet.create()` also gates on the
    same flag before ever calling this function, so a caller asking for
    a paid series against a `testseries_paid_allowed=False` campus gets
    a clear 403 there rather than reaching this silent downgrade — but
    this function re-checks unconditionally anyway rather than trusting
    that the view already did, same "checked before the bridge call,
    bridge doesn't blindly trust it either" posture `create_section_
    group()`'s class-teacher check above takes.

    No `subject` parameter, unlike `create_assigments()` above.
    `create_context_testseries()`'s full kwarg list (confirmed against
    this pass's `testseries/bridge.py` upload) is `source, context_type,
    context_id, creator, title, description, is_paid, price_coins,
    duration_minutes, attempts_allowed, questions, roster` — there's no
    `extra_data`-shaped slot the way `create_context_assigments()` has,
    so there's nowhere to persist a `subject_id` even if this function
    accepted one. A campus test series is therefore scoped to a
    `Section` only, not a `Section`+`Subject` pair, until `testseries`
    grows somewhere to put that. Any subject-level restriction on WHO
    may call this (e.g. "only that section/subject's staff") is
    entirely `views.py`'s job, checked BEFORE this function is ever
    called (Task 13 checklist: "Staff permission check bridge call se
    pehle hota hai, testseries khud trust karta hai caller ko" — the
    same golden rule `create_assigments()` operates under) — this
    function itself does not accept or check a `subject` at all, so
    there's no way for it to enforce that even if it wanted to.

    `questions` is passed straight through uninspected; shape/validation
    (each dict `full_clean()`-ed as a `Question`) is entirely
    `create_context_testseries()`'s own contract — same "caller resolves
    context, testseries resolves everything about a Question" boundary
    `assigments` draws for its own roster dicts.

    Roster is every ACTIVE `StudentEnrollment` for `section`, passed as
    plain `login.User` instances (via `.student`) — `create_context_
    testseries()`'s own `roster` docstring asks for exactly that shape
    ("iterable of `login.User`, or `None`"), unlike `assigments`'s
    roster dicts (which additionally seed per-student roll_number/
    enrollment_no onto pre-created submission rows; `testseries` has no
    pre-created-attempt-row concept per its own bridge module docstring,
    so there's nothing here that needs those extra fields). This is also
    how the Task 13 checklist's "Roster fanout notification
    (TESTSERIES_POSTED) sab active enrolled students ko jaata hai" is
    satisfied — `create_context_testseries()` does that fan-out itself
    once handed this roster; `campus.bridge` doesn't (and can't, from
    outside `testseries`) fire that notification a second time.

    Returns the created `testseries.models.TestSeries` instance.
    """
    from .models import StudentEnrollment

    if not section.school_class.campus.testseries_paid_allowed:
        is_paid = False
        price_coins = 0
    if not is_paid:
        price_coins = 0

    roster = [
        enrollment.student
        for enrollment in StudentEnrollment.objects.filter(
            section=section, status=StudentEnrollment.Status.ACTIVE
        ).select_related("student")
    ]
    return create_context_testseries(
        source=TestSeries.Source.CAMPUS,
        context_type="section",
        context_id=section.id,
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


def can_review_testseries_attempt(*, user, context_type, context_id):
    """[Task 13] Permission check for a campus-sourced `TestSeries`
    attempt review — the exact function `testseries.permissions.
    user_can_review_attempt()` calls (this pass's `testseries/
    permissions.py` upload confirms the call site verbatim:
    `can_review_testseries_attempt(user=user, context_type=series.
    context_type, context_id=series.context_id)`).

    ⚠️ SIGNATURE FIX (this pass) — this function previously took
    `(user, attempt)` positionally, a shape guessed at before
    `testseries/permissions.py` was available. That guess was WRONG:
    the real caller passes `user`/`context_type`/`context_id` as
    keyword arguments, no `attempt` object at all. Left as `(user,
    attempt)`, this would have raised `TypeError` on every real call
    from `testseries` — a hard crash, not merely a wrong answer — the
    same "never exercised against the real caller" failure mode
    already found and fixed once this pass in `testseries/bridge.py`'s
    `_NotifTypeGap` import. Fixed below; both call sites in
    `campus/views.py`'s `TestAttemptViewSet` updated to match.

    `testseries.permissions.user_can_review_attempt()` already
    short-circuits `series.creator_id == user.id` before ever calling
    this, and only calls this at all for `source="campus"` — so this
    function doesn't need to re-check either of those itself; it only
    ever needs to answer "is `user` allowed to review a campus series
    at this `(context_type, context_id)`".

    Only `context_type="section"` is supported — the only shape
    `campus.bridge.create_testseries()` ever produces. Unlike
    `create_assigments()`'s subject-scoped `can_manage_section_
    subject()` check, a campus test series has no subject concept at
    all (see `create_testseries()`'s own docstring above — there's
    nowhere on `TestSeries` to even store one) — so this checks only
    "is `user` any active staff at this section's campus", the
    broadest permission `is_any_active_staff()` already expresses, not
    a narrower subject-specific one `testseries` has nowhere to record
    anyway.

    Returns `False` for any `context_type` other than `"section"`, for
    a `context_id` that no longer resolves to a real `Section` (e.g. a
    deleted one), and for a user with no active staff role at that
    campus. Returns `True` otherwise.
    """
    from .models import Section
    from .permissions import is_any_active_staff

    if context_type != "section":
        return False

    section = Section.objects.filter(pk=context_id).select_related("school_class").first()
    if section is None:
        logger.warning(
            "can_review_testseries_attempt(): context_id %s (context_type=%r) "
            "no longer resolves to a Section — denying review access rather "
            "than guessing at a campus.",
            context_id, context_type,
        )
        return False

    return is_any_active_staff(user, section.school_class.campus_id)


def get_testseries_attempts(section):
    """[Task 13] Returns every `testseries.TestAttempt` row for every
    campus test series attached to `section`, via `testseries.bridge.
    get_attempts_for_context()` — never a direct `testseries.models.
    TestAttempt` import from campus code outside this bridge module.
    Same "unfiltered by permission, caller narrows further" contract
    `get_assigments_submissions()` above documents for its own
    `assigments` analogue — `views.py`'s review endpoint still needs to
    check `can_review_testseries_attempt()` per-attempt (or gate the
    whole call on `is_any_active_staff()` for this section's campus)
    before showing anything back to a non-owning caller.
    """
    return get_attempts_for_context(context_type="section", context_id=section.id)


def user_accessible_testseries_context_ids(*, user, context_type):
    """[ADVANCED test series — access control] Which campus `Section` ids may
    `user` see and attempt test series for?

    Called by `testseries.access` (configured through
    `settings.TESTSERIES_CONTEXT_ACCESS`) so `testseries` never has to import
    campus models itself (golden rule). Answer = every section the user is
    ACTIVELY enrolled in as a student, plus every section of any campus where
    they hold an active `StaffProfile` (staff already have review rights over
    all of that campus's test series, see `can_review_testseries_attempt`).

    Before this existed, `TestSeriesViewSet` listed every published campus
    series to every logged-in user and `start()` let anyone attempt one.
    """
    if context_type != "section" or not getattr(user, "is_authenticated", False):
        return set()

    from .models import Section, StaffProfile, StudentEnrollment

    enrolled = set(
        StudentEnrollment.objects.filter(student=user, status=StudentEnrollment.Status.ACTIVE)
        .values_list("section_id", flat=True)
    )
    campus_ids = list(
        StaffProfile.objects.filter(user=user, is_active=True).values_list("campus_id", flat=True)
    )
    staff_sections = set()
    if campus_ids:
        staff_sections = set(
            Section.objects.filter(school_class__campus_id__in=campus_ids).values_list("id", flat=True)
        )
    return enrolled | staff_sections
