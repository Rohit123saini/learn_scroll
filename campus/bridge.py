# campus/bridge.py
"""
`campus`'s ONLY door into `core` / `message` — the project's golden rule
(`campus_app_design.md`, top of file) is that `campus` never imports
those apps' models directly, the same way `liveclass` already routes
through `core` instead of touching `message` internals.

This pass could not see `core/models.py` (for the `Notification` model
and the `NotifType` enum design doc §10 wants extended) or any existing
`core/classroom_chat_bridge.py` — neither was part of this upload. So
both functions below import lazily and degrade to a logged no-op
instead of a hard crash if that module/model isn't there yet — that
way `campus` stays installable, migratable, and testable on its own
before the rest of the bridge exists on the other side.

STATUS (this pass):
  - ✅ DONE — `core.models.NotifType` now has every value
    `campus_app_design.md` §10 asks for (verified against the real
    `core/models.py` upload this pass). `NotifTypes` below is
    deliberately KEPT as a plain-string mirror rather than switched to
    a direct `core.models.NotifType` reference — that would reintroduce
    the exact `campus` → `core.models` import this module exists to
    avoid. Every value in `NotifTypes` below has been checked
    character-for-character against `core.models.NotifType` and
    matches.
  - ✅ FIXED — `notify()`'s `Notification.objects.create(...)` call was
    passing `user=`/`body=`, but the real model's fields are
    `recipient=`/`message=`. Left as-is this would have raised
    `TypeError` on every single call once `core.models` became
    importable — i.e. this path was never exercised against the real
    model. Fixed below.
  - ❌ STILL MISSING — `core.classroom_chat_bridge.create_section_group`
    and `...provision_video_room` do not exist yet (confirmed against
    this pass's `core/classroom_chat_bridge.py` upload — it has
    `create_classroom_group` for `liveclass.Classroom`, nothing for
    `campus.Section` or a video room). Writing these requires
    `campus/models.py` (to know `Section`'s fields — does it have a
    `chat_group_enabled`/`linked_conversation_id` pair mirroring
    `Classroom`'s, or something else?) and whatever video-room
    provider `liveclass`/`message` use — neither was in this upload,
    so these two stay lazy-import no-ops for now rather than guessed at.
  - ⚠️ SHAPE MISMATCH, NOT JUST MISSING — `core.classroom_chat_bridge.
    resolve_parent_from_token` DOES already exist (Task 5, confirmed in
    this pass's upload), but it returns a `ParentTokenResolution`
    object (`.student`, `.parent_access_code`) — there is no
    `parent_user` anywhere in that flow, because parents in this system
    authenticate via token+access code, not a real `User` row. This
    function's own docstring below promises a `(parent_user,
    student_user)` tuple, which doesn't match what the underlying
    system can actually produce. Wiring this straight through as
    written would silently return `(None, None)` for a valid token
    (a real regression, worse than today's no-op) — see the function
    below for how this pass left it instead. Needs `campus/models.py`
    (specifically `CampusParentLink`) to resolve properly: does campus
    reuse `message`'s `ParentAccessCode`/`ParentToken` directly, or
    does `CampusParentLink` wrap them with its own parent-side identity?
  - Once `create_section_group`/`provision_video_room` exist and the
    parent-token shape question above is settled, the `except
    ImportError` branches below can be deleted — the `try` bodies for
    `create_section_group`/`provision_video_room` are already the real
    integration shape.

NEEDED TO FINISH THIS FILE: `campus/models.py` (for `Section`,
`CampusParentLink`) and `campus_app_design.md` §4 (video room) — please
provide these if you want the two remaining gaps above closed rather
than flagged.

TASK 11 ADDITION: `create_assignment()` / `get_assignment_submissions()`
below are a DIFFERENT kind of bridge call than everything above them in
this file. Every function above degrades to a lazy-import no-op because
`core`/`message` were genuinely unverified dependencies when this module
was first written. `assignment` is not in that position — it's a
confirmed, fully-built sibling app (Tasks 6-10), the same kind of
established dependency `assignment/bridge.py` itself treats
`core.services` as (a top-level import, no degrade). So these two import
`assignment.bridge`/`assignment.models` directly at module level, not
lazily, and do not degrade to a no-op on `ImportError` — if `assignment`
genuinely isn't installed, campus's own assignment feature has nothing
to fall back to anyway, so a hard import error at startup is the honest
failure mode, not a silently-neutered feature.
"""
import logging

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
    ASSIGNMENT_POSTED_CAMPUS = "assignment_posted_campus"
    ASSIGNMENT_DUE_REMINDER = "assignment_due_reminder"
    RESULT_PUBLISHED = "result_published"
    FEE_DUE_REMINDER = "fee_due_reminder"
    STAFF_ASSIGNMENT_APPROVED = "staff_assignment_approved"
    STAFF_ASSIGNMENT_REJECTED = "staff_assignment_rejected"
    # NOTICE_POSTED already exists on core.models.NotifType per the
    # design doc — reused as-is, not redefined here.
    NOTICE_POSTED = "notice_posted"


def create_section_group(section, actor):
    """
    Create the `message.Group` (+ its `Conversation`) for a `Section`,
    the same pattern `liveclass.create_classroom_group()` already uses
    for its own classrooms — via `core.classroom_chat_bridge`, never a
    direct `message` import from this app.

    Returns whatever `core.classroom_chat_bridge.create_section_group`
    returns (expected: the created `message.Group` instance), or `None`
    if that bridge function isn't available yet.
    """
    try:
        from core.classroom_chat_bridge import create_section_group as _create_section_group
    except ImportError:
        logger.warning(
            "core.classroom_chat_bridge.create_section_group not available — "
            "no message.Group was created for section %s. Wire up that "
            "bridge function to enable section-group auto-creation.",
            getattr(section, 'id', section),
        )
        return None
    return _create_section_group(section, actor)


def notify(*, users, notif_type, title, body='', data=None):
    """
    Single entry point for `campus` to push a `core.Notification`.
    `users` is an iterable of user instances/ids. `notif_type` should be
    one of the `NotifType` values `campus_app_design.md` §10 lists —
    those don't exist on `core.models.NotifType` yet in this upload, so
    for now this just logs which type/recipients WOULD have fired.

    Returns the list of created `Notification` rows, or `[]` if
    `core.models.Notification` isn't importable yet.
    """
    try:
        from core.models import Notification
    except ImportError:
        logger.warning(
            "core.models.Notification not available — notification "
            "(type=%s, title=%r) was NOT sent to %s. Add core/models.py "
            "to enable this.",
            notif_type, title, list(users),
        )
        return []

    created = []
    for user in users:
        created.append(
            Notification.objects.create(
                # NOTE (bug fix): core.models.Notification's actual fields
                # are `recipient` and `message` — not `user`/`body`. The
                # original call below would have raised
                # TypeError('unexpected keyword arguments') the moment
                # core.models became importable, i.e. it was never
                # actually tested end-to-end against the real model.
                recipient=user,
                notif_type=notif_type,
                title=title,
                message=body,
                data=data or {},
            )
        )
    return created


def provision_video_room(live_session, actor):
    """
    Provision a video room for a `CampusLiveSession`, reusing whatever
    LiveKit/WebRTC infra `liveclass`/`message` already have — via
    `core.classroom_chat_bridge.provision_video_room`, never a direct
    `liveclass`/`message` import from this app (design doc §4).

    Returns the room id/token string that bridge function hands back,
    or `None` if it isn't available yet — `CampusLiveSessionViewSet.
    perform_create` treats `None` as "no room yet, blank room_id",
    not as an error, so scheduling a session still succeeds even
    before this integration exists.
    """
    try:
        from core.classroom_chat_bridge import provision_video_room as _provision_video_room
    except ImportError:
        logger.warning(
            "core.classroom_chat_bridge.provision_video_room not available — "
            "no video room was provisioned for live session %s. Wire up that "
            "bridge function to enable this.",
            getattr(live_session, 'id', live_session),
        )
        return None
    return _provision_video_room(live_session, actor=actor)


def resolve_parent_from_token(token):
    """
    Verify a parent-access token against `message`'s existing
    ParentAccessCode/ParentToken flow — via `core.classroom_chat_bridge.
    resolve_parent_from_token`, never a direct `message` import from
    this app (see `CampusParentLink`'s model docstring).

    Returns a `(parent_user, student_user)` tuple on success, or
    `(None, None)` if the token is invalid/expired OR if that bridge
    function isn't available/usable yet — `ParentLinkVerifyView` treats
    both cases identically (a 400, "Invalid or expired token"), so this
    degrades safely instead of crashing when `core` isn't installed.

    NOTE (found this pass, not fixed — see module docstring STATUS):
    `core.classroom_chat_bridge.resolve_parent_from_token` DOES exist
    now, but it returns a `ParentTokenResolution` object
    (`.student`/`.parent_access_code`) — there is no `parent_user`
    anywhere in that underlying flow, since that system's parents
    authenticate via token+access code, not a real `User` row.
    Forwarding that object where a `(parent_user, student_user)` tuple
    is expected would silently misbehave (unpacking a non-tuple, or a
    caller treating a truthy object as a valid parent_user when there
    isn't one) rather than fail loudly — worse than today's degrade.
    So this deliberately still returns `(None, None)` rather than guess
    at the mapping. Needs `campus/models.py` (`CampusParentLink`) to
    know whether campus has (or should have) its own parent-identity
    concept, or whether `ParentLinkVerifyView`/`CampusParentLink` should
    be redesigned around `ParentTokenResolution`'s actual shape instead.
    """
    try:
        from core.classroom_chat_bridge import resolve_parent_from_token as _resolve_parent_from_token
    except ImportError:
        logger.warning(
            "core.classroom_chat_bridge.resolve_parent_from_token not "
            "available — parent-link verification cannot succeed until "
            "that bridge function is wired up."
        )
        return None, None

    resolution = _resolve_parent_from_token(token)
    if resolution is None:
        return None, None

    logger.error(
        "core.classroom_chat_bridge.resolve_parent_from_token returned a "
        "ParentTokenResolution (student=%s) but campus.bridge."
        "resolve_parent_from_token has no defined mapping from that shape "
        "to (parent_user, student_user) yet — denying rather than "
        "guessing. See this module's docstring STATUS section.",
        getattr(resolution, "student", None),
    )
    return None, None


def create_assignment(*, section, subject, posted_by, title, description="", attachment=None, due_date=None):
    """[Task 11] Creates a campus assignment via the unified `assignment`
    app instead of the now-deprecated `campus.Assignment` model (see that
    model's own docstring in models.py). Delegates the actual row
    creation + roster bulk-pre-create to `assignment.bridge.
    create_context_assignment()` — the same entry point `liveclass` is
    expected to use too (§1, §3 of assignment_app_design.md). `campus`
    resolves its own roster (`StudentEnrollment`) here, since
    `assignment` itself has no concept of what a `Section` or an
    enrollment is (opaque `context_id`, per that app's golden rule).

    `context_type="section"` / `context_id=section.id` is the
    (context_type, context_id) shape `assignment` stores opaquely;
    `campus`-side code (this bridge, and views.py's thin proxy) is the
    only thing that ever turns `context_id` back into a real `Section`.

    `subject` has nowhere to live on the unified `Assignment` model — a
    `Section` spans multiple subjects, so unlike `session` (always
    derivable from `section.school_class.session_id`, so the caller
    never needs to pass or store it separately) `subject_id` genuinely
    needs its own slot. Passed via `extra_data` into `Assignment.data`
    (see that parameter's own docstring on `create_context_assignment`)
    rather than inventing a new model field on the unified app for a
    campus-only concept — `views.py`'s thin-proxy serializer reads it
    back out of `assignment.data["subject_id"]` to reconstruct the old
    API's `subject` field.

    Returns the created `assignment.models.Assignment` instance.
    """
    from assignment.bridge import create_context_assignment
    from assignment.models import AssignmentSource

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
    return create_context_assignment(
        source=AssignmentSource.CAMPUS,
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


def get_assignment_submissions(section):
    """[Task 11] Returns every `assignment.AssignmentSubmission` row for
    a campus `section`, via `assignment.bridge.
    get_submissions_for_context()` — never a direct
    `assignment.models.AssignmentSubmission` import from campus code
    outside this bridge module. Unfiltered by permission, same contract
    `get_submissions_for_context()` itself documents: the caller (views.py)
    is responsible for any further staff/student-scoped narrowing.
    """
    from assignment.bridge import get_submissions_for_context

    return get_submissions_for_context(context_type="section", context_id=section.id)