# assigments/bridge.py
"""
The one and only door into this app for `campus`/`liveclass` (§1, §3).
`campus/bridge.py` and `liveclass/bridge.py` (not in this file — they live
in their own apps) call the two functions below instead of ever touching
`assigments.models` directly; this app calls back out to
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

from .models import assigments, assigmentsSource, assigmentsSubmission

logger = logging.getLogger(__name__)


def create_context_assigments(
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
) -> assigments:
    """§3. `roster = [{"user_id": ..., "roll_number": "...",
    "enrollment_no": "..."}, ...]` — already resolved by the caller
    (campus/liveclass bridge) from whatever roster source is correct for
    that app (e.g. `campus.StudentEnrollment`, or liveclass's
    `SessionParticipant`/`PassPurchase` holders — see design doc §6,
    [NOT YET VERIFIED] on the liveclass side).

    Creates the `assigments` row, then bulk pre-creates one
    `assigmentsSubmission(status=MISSING)` per roster entry with the
    roll_number/enrollment_no snapshot already in place — so "who hasn't
    submitted yet" is always a plain query against existing rows, never a
    roster-diff computed on read.

    `source` must be `assigmentsSource.CAMPUS` or `assigmentsSource.
    LIVECLASS` here — `assigmentsSource.PERSONAL` assigmentss are created
    directly via the API (no roster, no bridge involved; see design doc
    §7 permissions), not through this function.

    [FIX] — this invariant was previously only stated in this docstring
    and never actually checked in code (`assigmentsSource` was imported
    but unused). `IsPersonalSourceOnly`/`assigmentsViewSet.perform_create`
    already close the public-API side of "personal is the only
    API-created source" (§7) — but nothing stopped a caller of *this*
    function, the app's other creation path, from passing
    `source=assigmentsSource.PERSONAL` and silently creating a
    bridge-originated "personal" assigments with a roster attached,
    which breaks the personal flow's own assumption (no roster, no
    bridge — see `assigmentsSubmissionViewSet.perform_create`'s
    docstring) elsewhere in this app. Enforced here now, at the one
    other place an `assigments` can come into existence.

    [ADDED — Task 11] `extra_data`: an optional dict merged into `data`
    alongside `context_type`/`context_id`, additive-only (defaults to
    `None`, so every existing caller is unaffected). Added because
    `campus.bridge.create_assigments()` needs `assigments.subject`
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
    if source not in (assigmentsSource.CAMPUS, assigmentsSource.LIVECLASS):
        raise ValueError(
            f"create_context_assigments() only accepts source=campus or source=liveclass, got {source!r}. "
            "Personal assigmentss are created directly via the public API, never through this bridge."
        )
    data = dict(extra_data or {})
    data["context_type"] = context_type
    data["context_id"] = str(context_id) if context_id else None
    with transaction.atomic():
        assigments = assigments.objects.create(
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
        assigmentsSubmission.objects.bulk_create(
            [
                assigmentsSubmission(
                    assigments=assigments,
                    student_id=entry["user_id"],
                    roll_number=entry.get("roll_number", ""),
                    enrollment_no=entry.get("enrollment_no", ""),
                    status=assigmentsSubmission.SubmissionStatus.MISSING,
                )
                for entry in roster
            ],
            ignore_conflicts=True,
        )
    # [HARDENING] — one line per bulk post, so a large campus/liveclass
    # roster assigments is traceable in logs without a DB query. INFO,
    # not DEBUG: this is a meaningful business event (a teacher publishing
    # work to a whole roster), not noise.
    logger.info(
        "assigments.created id=%s source=%s context_type=%s context_id=%s roster_size=%d",
        assigments.id, source, context_type, context_id, len(roster),
    )
    return assigments


def get_submissions_for_context(context_type: str, context_id):
    """§3. Returns an unfiltered-by-permission queryset scoped only to
    "which context" — the caller (campus/liveclass) is responsible for
    any further roster/permission-based narrowing, since this app has no
    concept of who's allowed to see what in campus or liveclass terms.
    """
    return assigmentsSubmission.objects.filter(
        assigments__context_type=context_type, assigments__context_id=context_id
    ).select_related("assigments", "student")


def notify_submission_received(submission: assigmentsSubmission) -> None:
    """§3 — `assigments` calls `core.services.create_notification`
    directly (same precedent as `message`), so a submission event can
    surface in campus's Notice feed or liveclass's classroom feed without
    this app ever importing either app's models. The notification's
    `data` carries the assigments's own `context_type`/`context_id` so
    the client can deep-link back into whichever context page is
    relevant.
    """
    assigments = submission.assigments
    if not assigments.posted_by_id:
        return
    create_notification(
        recipient=assigments.posted_by,
        notif_type="submission_received",
        title="New Submission",
        message=f"{submission.student} submitted \"{assigments.title}\".",
        data={"context_type": assigments.context_type, "context_id": str(assigments.context_id or "")},
    )