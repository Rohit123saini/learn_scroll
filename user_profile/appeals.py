"""
user_profile/appeals.py - notice + appeal for content that was auto-held or hidden
because of user reports (audit item 17). Copyright takedowns are NOT handled here
(they have counter-notices in the copyrights app); child-safety holds are not
appealable in-app (see child_safety.py, point 5).

  GET  /profile/notices/            what was done to MY content (ModerationAction rows)
  POST /profile/appeals/            {"notice_id": 12, "message": "..."}
  GET  /profile/appeals/            my appeals + their outcome

Rules: only the content owner; only for the LATEST visible decision on that content;
one open appeal per content, one appeal per decision; at most APPEALS_PER_DAY a day.
Staff decide in Django admin ("Moderation appeals" -> accept / reject) - a staff member
can't decide an appeal against their own decision (four-eyes).
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import moderation_log as mlog
from . import report_targets as rt

logger = logging.getLogger(__name__)

APPEALABLE = ("auto_hold", "hide", "photo_removed")


class AppealError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _cfg(name, default):
    return getattr(settings, name, default)


def latest_visible_ids(user):
    """Ids of the newest owner-visible ModerationAction per target (only these can be appealed)."""
    from .models import ModerationAction

    seen, ids = set(), set()
    for pk, tt, ti in ModerationAction.objects.filter(target_owner=user, owner_visible=True).order_by("-created_at", "-id") \
            .values_list("id", "target_type", "target_id")[:500]:
        if (tt, ti) not in seen:
            seen.add((tt, ti))
            ids.add(pk)
    return ids


def can_appeal(user, action_row):
    return bool(
        action_row.target_owner_id == user.id and action_row.owner_visible and action_row.can_appeal
        and action_row.action in APPEALABLE and action_row.pk in latest_visible_ids(user)
        and not action_row.appeals.exists()
    )


def _is_copyright_held(target_type, target_id):
    if target_type != "post":
        return False
    from post.models import Post

    return Post.objects.filter(pk=target_id, moderation_status__in=rt.COPYRIGHT_STATES).exists()


@transaction.atomic
def file_appeal(user, notice_id, message):
    from .models import ChildSafetyCase, ModerationAction, ModerationAppeal

    message = (message or "").strip()
    if len(message) < 10:
        raise AppealError("incomplete", "Please explain in at least a few words why this is a mistake.")
    row = ModerationAction.objects.filter(pk=notice_id, target_owner=user, owner_visible=True).first()
    if row is None:
        raise AppealError("not_found", "Notice not found.")
    if ChildSafetyCase.objects.filter(target_type=row.target_type, target_id=row.target_id).exists() or not row.can_appeal:
        raise AppealError("not_appealable", "This decision can't be appealed here. Please contact the Grievance Officer.")
    if _is_copyright_held(row.target_type, row.target_id):
        raise AppealError("copyright", "This is a copyright matter - use the counter-notice instead.")
    if row.action not in APPEALABLE or row.pk not in latest_visible_ids(user):
        raise AppealError("stale", "This notice has been replaced by a newer decision.")
    if row.appeals.exists():
        raise AppealError("duplicate", "You already appealed this decision.")
    since = timezone.now() - timedelta(days=1)
    if ModerationAppeal.objects.filter(user=user, created_at__gte=since).count() >= _cfg("APPEALS_PER_DAY", 5):
        raise AppealError("rate_limited", "Too many appeals today. Please try again tomorrow.")
    try:
        appeal = ModerationAppeal.objects.create(
            user=user, action=row, target_type=row.target_type, target_id=row.target_id, message=message[:1000])
    except IntegrityError:
        raise AppealError("duplicate", "You already have an open appeal for this.")
    mlog.log_action(
        ModerationAction.Action.APPEAL_FILED, target_type=row.target_type, target_id=row.target_id,
        owner=user, actor=user, reason=row.reason, note=message[:300],
    )
    return appeal


@transaction.atomic
def decide_appeal(appeal, actor, accept, note=""):
    """Staff decision. accept=True restores the content (a story cannot be restored: the decision is
    reversed and the owner is told to post again)."""
    from .models import AutoModerationFlag, ChildSafetyCase, ContentReport, ModerationAction, ModerationAppeal

    if not (actor and actor.is_authenticated and actor.is_active and actor.is_staff):
        raise AppealError("forbidden", "Only staff can decide appeals.")
    appeal = ModerationAppeal.objects.select_for_update().get(pk=appeal.pk)
    if appeal.status != ModerationAppeal.Status.OPEN:
        raise AppealError("bad_state", "This appeal is already decided.")
    if appeal.action_id and appeal.action.actor_id == actor.pk:
        raise AppealError("four_eyes", "Another reviewer must decide an appeal against your own decision.")
    if ChildSafetyCase.objects.filter(target_type=appeal.target_type, target_id=appeal.target_id).exists():
        from . import child_safety

        if not child_safety.can_handle(actor):
            raise AppealError("forbidden", "Child-safety content can only be decided by child-safety staff.")
    note = (note or "").strip()
    if not accept and not note:
        raise AppealError("incomplete", "Give a reason when rejecting an appeal.")

    A = ModerationAction.Action
    appeal.decided_by, appeal.decided_at, appeal.decision_note = actor, timezone.now(), note[:500]
    owner = appeal.user
    if accept:
        quarantine = ""
        if appeal.action_id and "quarantine:" in (appeal.action.note or ""):
            quarantine = appeal.action.note.split("quarantine:", 1)[1].strip()
        restored = rt.restore(appeal.target_type, appeal.target_id, quarantine_path=quarantine)
        undone = list(ContentReport.objects.filter(
            target_type=appeal.target_type, target_id=appeal.target_id, status="actioned").select_related("reporter"))
        still_open = list(ContentReport.objects.filter(
            target_type=appeal.target_type, target_id=appeal.target_id, status__in=("open", "reviewed")).select_related("reporter"))
        ContentReport.objects.filter(target_type=appeal.target_type, target_id=appeal.target_id).exclude(
            status="dismissed").update(status=ContentReport.Status.DISMISSED)
        from .report_automation import notify_reporters  # H15: reporters hear that the decision changed

        notify_reporters(undone, "reversed")
        notify_reporters(still_open, "dismissed")
        AutoModerationFlag.objects.filter(target_type=appeal.target_type, target_id=appeal.target_id, status="open").update(
            status=AutoModerationFlag.Status.DISMISSED)
        appeal.status = ModerationAppeal.Status.ACCEPTED
        mlog.log_action(A.APPEAL_ACCEPTED, target_type=appeal.target_type, target_id=appeal.target_id,
                        actor=actor, owner=owner, note=note or "appeal accepted")
        mlog.log_action(A.RESTORE, target_type=appeal.target_type, target_id=appeal.target_id,
                        actor=actor, owner=owner,
                        note="restored after appeal" if restored else "decision reversed after review (story cannot be re-published)",
                        owner_visible=True, can_appeal=False)
        if appeal.target_type == "story" and not restored:
            # P-LEFT: a removed story can't be brought back (is_deleted is also how ended stories are stored).
            # The review request still counts: the decision is reversed and the reporters are told.
            mlog.notify(owner, "We reviewed your story again",
                        "We agree the removal was a mistake and cleared it from your record. A story can't be "
                        "put back once it is removed, so please post it again if you like. Sorry for the trouble.",
                        data={"target_type": appeal.target_type, "target_id": appeal.target_id, "can_appeal": False,
                              "story_restorable": False})
        else:
            mlog.notify(owner, f"Your {mlog.what(appeal.target_type)} is back",
                        "We looked at your appeal again and restored it. Sorry for the trouble.",
                        data={"target_type": appeal.target_type, "target_id": appeal.target_id, "can_appeal": False})
    else:
        appeal.status = ModerationAppeal.Status.REJECTED
        mlog.log_action(A.APPEAL_REJECTED, target_type=appeal.target_type, target_id=appeal.target_id,
                        actor=actor, owner=owner, note=note)
        mlog.notify(owner, "We reviewed your appeal",
                    "We checked again and the decision stands. If you still disagree, you can write to the "
                    "Grievance Officer (see the Help screen).",
                    data={"target_type": appeal.target_type, "target_id": appeal.target_id, "can_appeal": False})
    appeal.save()
    return appeal
