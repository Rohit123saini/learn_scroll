"""
user_profile/child_safety.py - the separate, urgent workflow for reports about
children (audit item 18).

What happens when someone files a report with reason "child_safety":
  1. the report is marked URGENT (sorts first in Django admin: "CHILD SAFETY queue");
  2. the content is HELD at once, on the very first report (reversible; no wait for
     3 / 8 reporters - the cost of a wrong hold is small, of a wrong "wait" is not);
  3. a ChildSafetyCase is opened with a FROZEN snapshot (text, media urls, owner,
     times) so the evidence survives even if the owner deletes / edits the content;
  4. staff who hold `user_profile.handle_child_safety` (and superusers) are notified,
     and an hourly task warns again if the case is still open after the SLA
     (settings.CHILD_SAFETY_SLA_HOURS, default 24 h);
  5. the owner gets a plain notice WITHOUT the reason and WITHOUT an appeal button
     (no tipping-off); they can write to the Grievance Officer.
  6. staff record the report to the authorities on the case (who, when, FIR /
     acknowledgement number) - `mark_reported()`.

POCSO (Protection of Children from Sexual Offences Act, 2012) - summary for the
operator, NOT legal advice, confirm with your lawyer: ss.19-21 expect a person who
knows or apprehends such an offence to report it to the Special Juvenile Police Unit
(SJPU) or local police, and s.20 specifically names service providers who come across
child sexual abuse material (CSAM) - they must report it and hand over the material
and the source. This app therefore (a) never hard-deletes child-safety evidence before
`preserve_until` (CHILD_SAFETY_PRESERVE_DAYS, default 180 days, the same period the IT
Rules ask intermediaries to keep removed content), and (b) never forwards material by
chat/email - staff report through the official channels and write the reference here.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import moderation_log as mlog
from . import report_targets as rt

logger = logging.getLogger(__name__)

PERM = "user_profile.handle_child_safety"

# Shown to the person who files a child-safety report (and by GET /profile/child-safety/help/).
# Overridable from settings (CHILD_SAFETY_HELP) - numbers/portals can change.
DEFAULT_HELP = {
    "message": (
        "Thank you. We have hidden this and our team will review it urgently. "
        "If a child is in danger right now, call Childline 1098 or the police (112) immediately."
    ),
    "childline": "1098",
    "police": "112",
    "online_reporting": [
        {"name": "National Cyber Crime Reporting Portal", "url": "https://cybercrime.gov.in"},
        {"name": "NCPCR POCSO e-Box", "url": "https://ncpcr.gov.in"},
    ],
    "do_not_share": "Please do not forward or screenshot the material to anyone - that can itself be an offence.",
}


def help_info():
    info = dict(DEFAULT_HELP)
    info.update(getattr(settings, "CHILD_SAFETY_HELP", {}) or {})
    return info


def _cfg(name, default):
    return getattr(settings, name, default)


def can_handle(user):
    try:
        return bool(user and user.is_authenticated and user.is_active and user.is_staff
                    and (user.is_superuser or user.has_perm(PERM)))
    except Exception:
        return False


def _staff():
    from django.contrib.auth import get_user_model

    return [u for u in get_user_model().objects.filter(is_active=True, is_staff=True) if can_handle(u)]


def _notify_staff(title, message, data=None):
    for u in _staff():
        mlog.notify(u, title, message, data={"kind": "child_safety_staff", **(data or {})})


# --------------------------------------------------------------------------
def snapshot(target_type, target_id, owner):
    """A frozen copy of what was reported. Never raises."""
    snap = {
        "taken_at": timezone.now().isoformat(),
        "owner_id": getattr(owner, "pk", None),
        "owner_username": getattr(owner, "username", None),
    }
    try:
        if target_type == "post":
            from post.models import Post

            p = Post.objects.filter(pk=target_id).first()
            if p:
                snap.update(title=p.title, content=(p.content or "")[:2000], created_at=str(p.created_at))
                snap["media"] = [str(getattr(m, "file", "") or getattr(m, "url", "") or "") for m in p.media.all()[:20]] \
                    if hasattr(p, "media") else []
        elif target_type == "comment":
            from post.models import PostComment

            c = PostComment.objects.filter(pk=target_id).first()
            if c:
                snap.update(content=(c.content or "")[:2000], created_at=str(c.created_at))
        elif target_type == "message":
            from message.models import Message

            m = Message.objects.filter(pk=target_id).first()
            if m:
                snap.update(text=(m.text or "")[:2000], file_url=m.file_url, type=m.type,
                            conversation_id=str(m.conversation_id), created_at=str(m.created_at))
        elif target_type == "class_chat":
            from tuitionclass.models import ChatMessage

            m = ChatMessage.objects.filter(pk=int(target_id)).first()
            if m:
                snap.update(text=(m.message or "")[:2000], session_id=m.session_id, created_at=str(m.sent_at))
        elif target_type == "story":
            from post.models import Story

            s = Story.objects.filter(pk=target_id).first()
            if s:
                snap.update(created_at=str(getattr(s, "created_at", "")),
                            media=str(getattr(s, "media", "") or getattr(s, "file", "") or ""))
        elif target_type == "profile_photo":
            snap.update(photo=str(getattr(owner, "profile_photo", "") or ""))
    except Exception:
        logger.exception("child-safety snapshot failed (%s:%s)", target_type, target_id)
    return snap


def handle_report(report):
    """Called by report_automation.on_report_created for reason == child_safety.
    Returns {"held": bool, "case": ChildSafetyCase|None}. Never raises."""
    from .models import ChildSafetyCase, ContentReport, ModerationAction

    out = {"held": False, "case": None}
    try:
        ContentReport.objects.filter(pk=report.pk).update(priority=ContentReport.Priority.URGENT)
        now = timezone.now()
        owner = report.reported_user
        case, created = ChildSafetyCase.objects.get_or_create(
            target_type=report.target_type, target_id=str(report.target_id),
            defaults={
                "reported_user": owner,
                "snapshot": snapshot(report.target_type, report.target_id, owner),
                "first_report_at": now,
                "due_at": now + timedelta(hours=_cfg("CHILD_SAFETY_SLA_HOURS", 24)),
                "preserve_until": now + timedelta(days=_cfg("CHILD_SAFETY_PRESERVE_DAYS", 180)),
            },
        )
        out["case"] = case
        changed, extra = rt.hide(report.target_type, report.target_id, mode="hold")
        out["held"] = changed
        if changed and extra:  # profile photo: remember where the copy is
            snap = dict(case.snapshot or {})
            snap["quarantine_path"] = extra
            ChildSafetyCase.objects.filter(pk=case.pk).update(snapshot=snap)

        if created:
            mlog.log_action(
                ModerationAction.Action.CHILD_CASE_OPENED, target_type=report.target_type, target_id=report.target_id,
                owner=owner, reason="child_safety", count=1, note=f"case {case.pk}, held={changed}",
            )
            _notify_staff(
                "URGENT: child-safety report",
                f"A {mlog.what(report.target_type)} was reported for child safety. Review within "
                f"{_cfg('CHILD_SAFETY_SLA_HOURS', 24)} h.",
                {"case_id": case.pk},
            )
        if changed:
            row = mlog.log_action(
                ModerationAction.Action.AUTO_HOLD_CHILD, target_type=report.target_type, target_id=report.target_id,
                owner=owner, reason="child_safety", count=1,
                note="held on first child-safety report", owner_visible=True, can_appeal=False,
            )
            # plain notice: no reason, no appeal button (see module docstring, point 5)
            mlog.tell_owner(owner, row, "We hid your " + mlog.what(report.target_type),
                            "We have hidden it while our team reviews it. If you think this is a mistake, "
                            "write to the Grievance Officer (see the Help screen).")
    except Exception:
        logger.exception("child-safety handling failed (report=%s)", getattr(report, "pk", None))
    return out


# --------------------------------------------------------------------------
# staff actions (Django admin) - every one is permission-checked and audited
# --------------------------------------------------------------------------
class ChildSafetyError(Exception):
    pass


def _need_staff(actor):
    if not can_handle(actor):
        raise ChildSafetyError("You need the 'handle child safety' permission for this.")


def mark_reported(case, actor, authority_name, reference, note=""):
    """Record that staff reported this case to SJPU / police / NCPCR."""
    from .models import ChildSafetyCase, ContentReport, ModerationAction

    _need_staff(actor)
    authority_name = (authority_name or "").strip()
    reference = (reference or "").strip()
    if not authority_name or not reference:
        raise ChildSafetyError("Authority name and the acknowledgement / FIR reference are both required.")
    case.status = ChildSafetyCase.Status.REPORTED
    case.authority_name = authority_name[:200]
    case.authority_reference = reference[:120]
    case.reported_to_authority_at = timezone.now()
    case.reported_by = actor
    if note:
        case.staff_note = (case.staff_note + "\n" + note).strip()
    case.save()
    # content stays down; evidence stays preserved
    rt.hide(case.target_type, case.target_id, mode="staff")
    pending = list(ContentReport.objects.filter(
        target_type=case.target_type, target_id=case.target_id, status__in=("open", "reviewed")).select_related("reporter"))
    ContentReport.objects.filter(target_type=case.target_type, target_id=case.target_id).exclude(
        status="dismissed").update(status=ContentReport.Status.ACTIONED)
    from .report_automation import notify_reporters  # H15: tell the reporter (only reports that were still undecided)

    notify_reporters(pending, "actioned")
    mlog.log_action(
        ModerationAction.Action.CHILD_REPORTED_AUTHORITY, target_type=case.target_type, target_id=case.target_id,
        actor=actor, owner=case.reported_user, reason="child_safety",
        note=f"{authority_name} / ref {reference}",
    )
    return case


def close_case(case, actor, note):
    """Not a child-safety issue after review: dismiss reports, bring the content back."""
    from .models import ChildSafetyCase, ContentReport, ModerationAction

    _need_staff(actor)
    if case.status == ChildSafetyCase.Status.REPORTED:
        raise ChildSafetyError("A case already reported to the authorities can't be closed as 'no action'.")
    if not (note or "").strip():
        raise ChildSafetyError("A note is required.")
    case.status = ChildSafetyCase.Status.CLOSED_NO_ACTION
    case.staff_note = ((case.staff_note or "") + "\n" + note).strip()
    case.save()
    rt.restore(case.target_type, case.target_id, quarantine_path=(case.snapshot or {}).get("quarantine_path", ""))
    pending = list(ContentReport.objects.filter(
        target_type=case.target_type, target_id=case.target_id, status__in=("open", "reviewed")).select_related("reporter"))
    ContentReport.objects.filter(target_type=case.target_type, target_id=case.target_id, status__in=("open", "reviewed")).update(
        status=ContentReport.Status.DISMISSED)
    from .report_automation import notify_reporters  # H15

    notify_reporters(pending, "dismissed")
    mlog.log_action(
        ModerationAction.Action.CHILD_CASE_CLOSED, target_type=case.target_type, target_id=case.target_id,
        actor=actor, owner=case.reported_user, reason="child_safety", note=note,
    )
    return case


def warn_overdue_cases(now=None):
    """Hourly task: cases still New / Under review after their SLA -> warn staff once."""
    from .models import ChildSafetyCase

    now = now or timezone.now()
    n = 0
    for c in ChildSafetyCase.objects.filter(
        status__in=("new", "under_review"), due_at__lte=now, sla_warned_at__isnull=True,
    ):
        _notify_staff("OVERDUE child-safety case", f"Case {c.pk} is past its {_cfg('CHILD_SAFETY_SLA_HOURS', 24)} h review time.",
                      {"case_id": c.pk})
        ChildSafetyCase.objects.filter(pk=c.pk).update(sla_warned_at=now)
        n += 1
    return n


def evidence_must_be_kept(target_type, target_id):
    """Call this before ANY hard delete / purge of content. True = keep it."""
    from .models import ChildSafetyCase

    return ChildSafetyCase.objects.filter(
        target_type=target_type, target_id=str(target_id), preserve_until__gt=timezone.now(),
    ).exists()
