"""
copyrights/services.py - ALL copyright state changes live here.

Views, Django admin and Celery tasks only call these functions, so the rules
(levels, hold/restore, strikes, notifications, audit log) cannot drift apart.

Settings (all optional, env-overridable in settings.py):
  COPYRIGHT_AUTO_HOLD            True   hide content as soon as a COMPLETE notice arrives
  COPYRIGHT_COUNTER_WAIT_DAYS    10     wait before auto-restoring after a counter-notice
  COPYRIGHT_STRIKE_DAYS          180    a strike counts for this long
  COPYRIGHT_RESTRICT_AT          2      active strikes that block uploads
  COPYRIGHT_REVIEW_AT            3      active strikes that open a termination review
  COPYRIGHT_SLA_HOURS            48     untouched claims are escalated after this
  COPYRIGHT_NEEDS_INFO_DAYS      14     claim closed if the claimant never answers
  COPYRIGHT_CLAIMS_PER_DAY       10     per claimant
  COPYRIGHT_BAD_FAITH_LIMIT      3      bad-faith claims (365 days) before filing is blocked
  COPYRIGHT_HIGH_REACH_FOLLOWERS 10000  claims against such accounts need an L2 decision
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from . import permissions as perms
from .models import (
    CopyrightAuditLog,
    CopyrightClaim,
    CopyrightCounterNotice,
    CopyrightStanding,
    CopyrightStrike,
    CopyrightTakedown,
)

logger = logging.getLogger(__name__)

C = CopyrightClaim
T = CopyrightTakedown

POST_HOLD = "copyright_hold"
POST_REMOVED = "copyright_removed"
COPYRIGHT_POST_STATES = (POST_HOLD, POST_REMOVED)


class ClaimError(Exception):
    """Business-rule failure. `code` is machine readable for the API / app."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _cfg(name, default):
    return getattr(settings, name, default)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _log(claim, action, *, actor=None, label=None, note="", user=None):
    try:
        CopyrightAuditLog.objects.create(
            claim=claim,
            user=user or (claim.content_owner if claim is not None else None),
            actor=actor if (actor is not None and getattr(actor, "pk", None)) else None,
            actor_label=label or (getattr(actor, "username", None) or "system")[:40],
            action=action,
            note=(note or "")[:500],
        )
    except Exception:
        logger.exception("copyright audit log failed (%s)", action)


def _notify(user, title, message="", *, data=None):
    """In-app notification via core's single choke point. Never raises."""
    if user is None:
        return
    try:
        from core.services import create_notification

        payload = {"kind": "copyright"}
        payload.update(data or {})
        create_notification(user, "generic", title, message, data=payload)
    except Exception:
        logger.exception("copyright notification failed")


def _notify_staff(level, title, message="", *, data=None):
    try:
        for u in perms.staff_at_least(level):
            _notify(u, title, message, data=data)
    except Exception:
        logger.exception("copyright staff notification failed")


def _load_target(target_type, target_id):
    """(obj, owner) for a post/story that still exists, else (None, None)."""
    try:
        if target_type == C.TargetType.POST:
            from post.models import Post

            obj = Post.objects.filter(id=target_id, is_deleted=False).select_related("user").first()
        elif target_type == C.TargetType.STORY:
            from post.models import Story

            obj = Story.objects.filter(id=target_id, is_deleted=False).select_related("user").first()
        else:
            return None, None
    except (ValueError, TypeError, ValidationError):
        return None, None
    return (obj, obj.user) if obj is not None else (None, None)


def _snapshot(target_type, obj):
    text = ""
    if target_type == C.TargetType.POST:
        text = (getattr(obj, "title", "") or getattr(obj, "content", "") or "")
    else:
        text = getattr(obj, "caption", "") or ""
    return text.strip()[:300]


# --------------------------------------------------------------------------
# content hold / restore (the ONLY place that touches Post / Story visibility)
# --------------------------------------------------------------------------
def _other_active_takedowns(takedown):
    return T.objects.filter(
        target_type=takedown.target_type, target_id=takedown.target_id,
        state__in=(T.State.HELD, T.State.REMOVED),
    ).exclude(pk=takedown.pk)


def _has_real_state(prev):
    return bool(prev) and not prev.get("shared")


def _hide_content(takedown, removed=False):
    """Hide the content (idempotent). Remembers the previous state for the undo."""
    if takedown.target_type == C.TargetType.POST:
        from post.models import Post

        post = Post.objects.filter(pk=takedown.target_id).first()
        if post is None:
            return
        if post.moderation_status in COPYRIGHT_POST_STATES:
            # Already hidden by another claim: share, don't overwrite the real undo data.
            if not _has_real_state(takedown.previous_state):
                takedown.previous_state = {"shared": True}
        else:
            takedown.previous_state = {"moderation_status": post.moderation_status}
        Post.objects.filter(pk=post.pk).update(moderation_status=POST_REMOVED if removed else POST_HOLD)
    else:
        from post.models import Story

        story = Story.objects.filter(pk=takedown.target_id).first()
        if story is None:
            return
        if story.is_deleted and not _has_real_state(takedown.previous_state):
            takedown.previous_state = {"shared": True}
        elif not story.is_deleted:
            takedown.previous_state = {"is_deleted": False}
        Story.objects.filter(pk=story.pk).update(is_deleted=True, deleted_at=timezone.now())
    takedown.save(update_fields=["previous_state"])


def _restore_content(takedown):
    """Undo the hide - unless another claim still has the same content down."""
    others = list(_other_active_takedowns(takedown))
    if others:
        # Pass the real undo data on so the LAST claim to close restores correctly.
        if _has_real_state(takedown.previous_state):
            for o in others:
                if not _has_real_state(o.previous_state):
                    o.previous_state = takedown.previous_state
                    o.save(update_fields=["previous_state"])
                    break
        return
    prev = takedown.previous_state if _has_real_state(takedown.previous_state) else {}
    if takedown.target_type == C.TargetType.POST:
        from post.models import Post

        Post.objects.filter(pk=takedown.target_id, moderation_status__in=COPYRIGHT_POST_STATES).update(
            moderation_status=prev.get("moderation_status") or "approved"
        )
    else:
        from post.models import Story

        # An expired story stays gone - there is nothing left to bring back.
        Story.objects.filter(pk=takedown.target_id, expires_at__gt=timezone.now()).update(
            is_deleted=False, deleted_at=None
        )


def _ensure_takedown(claim, *, auto):
    td, created = T.objects.get_or_create(
        claim=claim, defaults={"target_type": claim.target_type, "target_id": claim.target_id, "auto_applied": auto},
    )
    return td, created


def _hold(claim, *, auto):
    td, _ = _ensure_takedown(claim, auto=auto)
    td.state = T.State.HELD
    td.restored_at = None
    td.save(update_fields=["state", "restored_at"])
    _hide_content(td, removed=False)
    return td


# --------------------------------------------------------------------------
# claimant side
# --------------------------------------------------------------------------
def claimant_can_file(claimant):
    """Raise ClaimError if this account may not file a (new) notice."""
    since_day = timezone.now() - timedelta(days=1)
    if C.objects.filter(claimant=claimant, created_at__gte=since_day).count() >= _cfg("COPYRIGHT_CLAIMS_PER_DAY", 10):
        raise ClaimError("rate_limited", "Too many copyright notices today. Please try again tomorrow.")
    since_year = timezone.now() - timedelta(days=365)
    bad = C.objects.filter(claimant=claimant, bad_faith=True, created_at__gte=since_year).count()
    if bad >= _cfg("COPYRIGHT_BAD_FAITH_LIMIT", 3):
        raise ClaimError(
            "claimant_blocked",
            "You can't file copyright notices because earlier notices were found to be false.",
        )


@transaction.atomic
def submit_claim(claimant, data, *, source=C.Source.USER):
    """File a formal copyright notice. Returns (claim, created).

    `data` keys: target_type, target_id, claimant_name, claimant_email, organisation,
    is_rights_owner, work_description, original_work_url, infringement_details,
    good_faith_statement, accuracy_statement, signature.
    """
    if not data.get("good_faith_statement") or not data.get("accuracy_statement"):
        raise ClaimError("incomplete", "Both statements (good faith and accuracy) must be accepted.")
    signature = (data.get("signature") or "").strip()
    if len(signature) < 3:
        raise ClaimError("incomplete", "Please sign the notice with your full name.")

    claimant_can_file(claimant)

    target_type = data["target_type"]
    target_id = str(data["target_id"])
    obj, owner = _load_target(target_type, target_id)
    if obj is None or owner is None:
        raise ClaimError("not_found", "This content doesn't exist any more.")
    if owner.id == claimant.id:
        raise ClaimError("own_content", "This is your own content - you can simply delete it.")

    existing = C.objects.filter(
        claimant=claimant, target_type=target_type, target_id=target_id, status__in=C.OPEN_STATUSES
    ).first()
    if existing is not None:
        return existing, False

    # Same content already claimed by someone else -> link, don't double-handle.
    first_open = (
        C.objects.filter(target_type=target_type, target_id=target_id, status__in=C.OPEN_STATUSES, duplicate_of=None)
        .order_by("created_at").first()
    )

    high_reach = (owner.followers_count or 0) >= _cfg("COPYRIGHT_HIGH_REACH_FOLLOWERS", 10000)
    needs_senior = high_reach or bool(getattr(owner, "is_verified", False)) or active_strike_count(owner) >= 1

    claim = C.objects.create(
        claimant=claimant,
        claimant_name=(data.get("claimant_name") or signature)[:120],
        claimant_email=data["claimant_email"],
        organisation=(data.get("organisation") or "")[:120],
        is_rights_owner=bool(data.get("is_rights_owner", True)),
        target_type=target_type,
        target_id=target_id,
        content_owner=owner,
        content_snapshot=_snapshot(target_type, obj),
        work_description=data["work_description"],
        original_work_url=data.get("original_work_url", "") or "",
        infringement_details=data.get("infringement_details", "") or "",
        good_faith_statement=True,
        accuracy_statement=True,
        signature=signature,
        source=source,
        required_level=2 if needs_senior else 1,
        priority=C.Priority.HIGH if needs_senior else C.Priority.NORMAL,
        duplicate_of=first_open,
    )
    _log(claim, "claim_submitted", actor=claimant, label="claimant", note=f"{target_type}:{target_id}")

    # AUTOMATION: a complete notice hides the content right away (reversible).
    held = False
    if _cfg("COPYRIGHT_AUTO_HOLD", True):
        _hold(claim, auto=True)
        held = True
        _log(claim, "auto_hold", label="automation", note="complete notice -> content on hold")

    _notify(
        claimant, "Copyright notice received",
        "We received your notice and will review it.", data={"claim_id": str(claim.id), "role": "claimant"},
    )
    if owner is not None:
        _notify(
            owner, "Copyright complaint about your content",
            ("We hid it while we review. " if held else "")
            + "A rights holder says it uses their work. You can file a counter-notice if this is a mistake.",
            data={"claim_id": str(claim.id), "role": "owner", "target_type": target_type, "target_id": target_id},
        )
    _notify_staff(
        claim.required_level, "New copyright claim",
        f"{claim.target_type} claim from {claim.claimant_name}", data={"claim_id": str(claim.id), "role": "staff"},
    )
    return claim, True


@transaction.atomic
def withdraw_claim(claim, claimant):
    claim = C.objects.select_for_update().get(pk=claim.pk)
    if claim.claimant_id != getattr(claimant, "id", None):
        raise PermissionDenied("Not your claim.")
    if claim.status not in C.OPEN_STATUSES + (C.Status.UPHELD,):
        raise ClaimError("bad_state", "This claim can't be withdrawn any more.")
    _close_and_restore(claim, status=C.Status.WITHDRAWN, actor=claimant, label="claimant",
                       note="withdrawn by claimant", revoke_strike_reason="claim withdrawn")
    _notify(claim.content_owner, "Copyright complaint withdrawn",
            "The complaint was withdrawn - your content is back.", data={"claim_id": str(claim.id), "role": "owner"})
    return claim


@transaction.atomic
def provide_info(claim, claimant, text):
    """Claimant answers a 'we need more information' request."""
    claim = C.objects.select_for_update().get(pk=claim.pk)
    if claim.claimant_id != getattr(claimant, "id", None):
        raise PermissionDenied("Not your claim.")
    if claim.status != C.Status.NEEDS_INFO:
        raise ClaimError("bad_state", "No information was requested for this claim.")
    text = (text or "").strip()
    if not text:
        raise ClaimError("incomplete", "Please write your answer.")
    claim.infringement_details = (claim.infringement_details + "\n\n[claimant update] " + text)[:2000]
    claim.status = C.Status.UNDER_REVIEW
    claim.info_requested_at = None
    claim.save(update_fields=["infringement_details", "status", "info_requested_at", "updated_at"])
    _log(claim, "info_provided", actor=claimant, label="claimant")
    return claim


# --------------------------------------------------------------------------
# staff decisions (levels enforced here)
# --------------------------------------------------------------------------
def _decide_level(claim, base):
    """Claims flagged `required_level=2` (high reach / verified / repeat) need L2 to decide."""
    return max(base, claim.required_level if claim.required_level >= 2 else base)


@transaction.atomic
def take_for_review(claim, actor):
    perms.require_level(actor, perms.L1, "reviewing claims")
    claim = C.objects.select_for_update().get(pk=claim.pk)
    if claim.status not in (C.Status.SUBMITTED, C.Status.UNDER_REVIEW):
        raise ClaimError("bad_state", "Claim is not waiting for review.")
    claim.status = C.Status.UNDER_REVIEW
    claim.reviewer = actor
    claim.save(update_fields=["status", "reviewer", "updated_at"])
    _log(claim, "review_started", actor=actor)
    return claim


@transaction.atomic
def request_info(claim, actor, message):
    perms.require_level(actor, perms.L1, "asking the claimant for information")
    message = (message or "").strip()
    if not message:
        raise ClaimError("incomplete", "Write what information is missing.")
    claim = C.objects.select_for_update().get(pk=claim.pk)
    if claim.status not in (C.Status.SUBMITTED, C.Status.UNDER_REVIEW):
        raise ClaimError("bad_state", "Claim is not waiting for review.")
    claim.status = C.Status.NEEDS_INFO
    claim.needs_info_message = message[:500]
    claim.info_requested_at = timezone.now()
    claim.reviewer = claim.reviewer or actor
    claim.save(update_fields=["status", "needs_info_message", "info_requested_at", "reviewer", "updated_at"])
    _log(claim, "info_requested", actor=actor, note=message)
    _notify(claim.claimant, "More information needed for your copyright notice", message,
            data={"claim_id": str(claim.id), "role": "claimant"})
    return claim


def _close_and_restore(claim, *, status, actor=None, label=None, note="", bad_faith=False,
                       revoke_strike_reason=None, revoked_by=None):
    """Close a claim, put the content back, drop its strike (if it had one)."""
    td = T.objects.filter(claim=claim).first()
    CopyrightCounterNotice.objects.filter(claim=claim, status__in=("waiting", "court_action")).update(
        status=CopyrightCounterNotice.Status.RESTORED, resolved_at=timezone.now())
    if td is not None and td.state != T.State.RESTORED:
        _restore_content(td)
        td.state = T.State.RESTORED
        td.restored_at = timezone.now()
        td.save(update_fields=["state", "restored_at"])
    strike = CopyrightStrike.objects.filter(claim=claim, revoked_at=None).first()
    if strike is not None and revoke_strike_reason:
        strike.revoked_at = timezone.now()
        strike.revoked_by = revoked_by
        strike.revoke_reason = revoke_strike_reason[:255]
        strike.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
        recompute_standing(strike.user)
    claim.status = status
    claim.bad_faith = bad_faith or claim.bad_faith
    claim.decided_at = timezone.now()
    claim.decision_note = (note or claim.decision_note)[:2000]
    if actor is not None and getattr(actor, "is_staff", False):
        claim.reviewer = actor
    claim.save(update_fields=["status", "bad_faith", "decided_at", "decision_note", "reviewer", "updated_at"])
    _log(claim, f"claim_{status}", actor=actor, label=label, note=note)


@transaction.atomic
def reject_claim(claim, actor, note, *, bad_faith=False):
    need = perms.L2 if (bad_faith or claim.required_level >= 2) else perms.L1
    perms.require_level(actor, need, "rejecting this claim")
    note = (note or "").strip()
    if not note:
        raise ClaimError("incomplete", "A reason is required.")
    claim = C.objects.select_for_update().get(pk=claim.pk)
    if claim.status not in C.OPEN_STATUSES:
        raise ClaimError("bad_state", "Only open claims can be rejected.")
    _close_and_restore(claim, status=C.Status.REJECTED, actor=actor, note=note, bad_faith=bad_faith)
    for d in claim.duplicates.filter(status__in=C.OPEN_STATUSES):
        _close_duplicate(d, C.Status.REJECTED, claim, actor)
    _notify(claim.claimant, "Copyright notice rejected", note, data={"claim_id": str(claim.id), "role": "claimant"})
    _notify(claim.content_owner, "Copyright complaint dismissed",
            "Your content is available again.", data={"claim_id": str(claim.id), "role": "owner"})
    return claim


def _close_duplicate(dup, status, parent, actor):
    """A duplicate claim follows its parent's decision - no second strike."""
    td = T.objects.filter(claim=dup).first()
    if td is not None:
        td.state = T.State.REMOVED if status == C.Status.UPHELD else T.State.RESTORED
        td.restored_at = None if status == C.Status.UPHELD else timezone.now()
        td.save(update_fields=["state", "restored_at"])
    dup.status = status
    dup.decided_at = timezone.now()
    dup.decision_note = f"Decided together with claim {parent.id}."
    dup.save(update_fields=["status", "decided_at", "decision_note", "updated_at"])
    _log(dup, f"claim_{status}", actor=actor, note=f"duplicate of {parent.id}")
    _notify(dup.claimant, "Copyright notice decided", f"Handled together with an earlier notice ({status}).",
            data={"claim_id": str(dup.id), "role": "claimant"})


@transaction.atomic
def uphold_claim(claim, actor, note="", *, issue_strike=True):
    perms.require_level(actor, perms.L2, "upholding a claim")
    claim = C.objects.select_for_update().get(pk=claim.pk)
    if claim.status not in (C.Status.SUBMITTED, C.Status.UNDER_REVIEW, C.Status.COUNTERED):
        raise ClaimError("bad_state", "This claim can't be upheld in its current state.")
    if claim.status == C.Status.COUNTERED:
        # counter-notice in flight: upholding again = rejecting the counter-notice
        CopyrightCounterNotice.objects.filter(claim=claim).update(
            status=CopyrightCounterNotice.Status.REJECTED, resolved_at=timezone.now())

    td = _hold(claim, auto=False)  # make sure it IS hidden, then mark as final
    td.state = T.State.REMOVED
    td.save(update_fields=["state"])
    _hide_content(td, removed=True)

    claim.status = C.Status.UPHELD
    claim.reviewer = actor
    claim.decided_at = timezone.now()
    claim.decision_note = (note or "")[:2000]
    claim.save(update_fields=["status", "reviewer", "decided_at", "decision_note", "updated_at"])
    _log(claim, "claim_upheld", actor=actor, note=note)

    owner = claim.content_owner
    if issue_strike and owner is not None and not CopyrightStrike.objects.filter(claim=claim).exists():
        CopyrightStrike.objects.create(
            user=owner, claim=claim, issued_by=actor,
            expires_at=timezone.now() + timedelta(days=_cfg("COPYRIGHT_STRIKE_DAYS", 180)),
        )
        _log(claim, "strike_issued", actor=actor)
    if owner is not None:
        recompute_standing(owner)

    for d in claim.duplicates.filter(status__in=C.OPEN_STATUSES):
        _close_duplicate(d, C.Status.UPHELD, claim, actor)

    _notify(claim.claimant, "Copyright notice accepted", "The content was removed.",
            data={"claim_id": str(claim.id), "role": "claimant"})
    _notify(owner, "Your content was removed (copyright)",
            "After review, we removed it. You can file a counter-notice if you believe this was a mistake."
            + (" This counts as a copyright strike." if issue_strike else ""),
            data={"claim_id": str(claim.id), "role": "owner", "target_type": claim.target_type,
                  "target_id": claim.target_id})
    return claim


@transaction.atomic
def restore_claim(claim, actor, note):
    """Put the content back. Open/held claims: L2. Reversing an UPHELD decision: L3."""
    claim = C.objects.select_for_update().get(pk=claim.pk)
    reversing = claim.status == C.Status.UPHELD
    perms.require_level(actor, perms.L3 if reversing else perms.L2,
                        "reversing an upheld decision" if reversing else "restoring content")
    note = (note or "").strip()
    if not note:
        raise ClaimError("incomplete", "A reason is required.")
    if claim.status in (C.Status.REJECTED, C.Status.WITHDRAWN, C.Status.RESTORED):
        raise ClaimError("bad_state", "Nothing to restore for this claim.")
    CopyrightCounterNotice.objects.filter(claim=claim, status__in=("waiting", "court_action")).update(
        status=CopyrightCounterNotice.Status.RESTORED, resolved_at=timezone.now())
    _close_and_restore(claim, status=C.Status.RESTORED, actor=actor, note=note,
                       revoke_strike_reason="decision reversed: " + note, revoked_by=actor)
    _notify(claim.content_owner, "Your content is back", note, data={"claim_id": str(claim.id), "role": "owner"})
    _notify(claim.claimant, "Copyright claim reversed", "The content was restored after review.",
            data={"claim_id": str(claim.id), "role": "claimant"})
    return claim


# --------------------------------------------------------------------------
# counter-notice
# --------------------------------------------------------------------------
@transaction.atomic
def file_counter_notice(owner, claim, data):
    claim = C.objects.select_for_update().get(pk=claim.pk)
    if claim.content_owner_id != getattr(owner, "id", None):
        raise PermissionDenied("Not your content.")
    if claim.status not in (C.Status.SUBMITTED, C.Status.UNDER_REVIEW, C.Status.NEEDS_INFO, C.Status.UPHELD):
        raise ClaimError("bad_state", "A counter-notice can't be filed for this claim.")
    if CopyrightCounterNotice.objects.filter(claim=claim).exists():
        raise ClaimError("duplicate", "You already filed a counter-notice for this claim.")
    st = CopyrightStanding.objects.filter(user=owner).first()
    if st is not None and st.level == CopyrightStanding.Level.TERMINATED:
        raise ClaimError("terminated", "This account was terminated for repeat infringement.")
    if not data.get("good_faith_statement") or not data.get("jurisdiction_consent"):
        raise ClaimError("incomplete", "Both statements must be accepted.")
    signature = (data.get("signature") or "").strip()
    if len(signature) < 3:
        raise ClaimError("incomplete", "Please sign with your full name.")
    explanation = (data.get("explanation") or "").strip()
    if not explanation:
        raise ClaimError("incomplete", "Explain why you believe this was a mistake.")

    wait = _cfg("COPYRIGHT_COUNTER_WAIT_DAYS", 10)
    counter = CopyrightCounterNotice.objects.create(
        claim=claim, user=owner, explanation=explanation[:2000],
        good_faith_statement=True, jurisdiction_consent=True, signature=signature[:120],
        restore_after=timezone.now() + timedelta(days=wait),
    )
    claim.status = C.Status.COUNTERED
    claim.save(update_fields=["status", "updated_at"])
    _log(claim, "counter_notice_filed", actor=owner, label="owner", note=f"restore after {wait} days")
    _notify(claim.claimant, "Counter-notice filed",
            f"The content owner disputes your notice. If you don't report court action within {wait} days, "
            "the content will be restored.", data={"claim_id": str(claim.id), "role": "claimant"})
    _notify(owner, "Counter-notice received",
            f"If the claimant doesn't take legal action within {wait} days, your content comes back automatically.",
            data={"claim_id": str(claim.id), "role": "owner"})
    _notify_staff(perms.L2, "Counter-notice filed", "A content owner disputed a copyright claim.",
                  data={"claim_id": str(claim.id), "role": "staff"})
    return counter


@transaction.atomic
def report_court_action(claim, claimant, note):
    claim = C.objects.select_for_update().get(pk=claim.pk)
    if claim.claimant_id != getattr(claimant, "id", None):
        raise PermissionDenied("Not your claim.")
    counter = CopyrightCounterNotice.objects.filter(claim=claim, status="waiting").first()
    if counter is None:
        raise ClaimError("bad_state", "There is no running counter-notice period for this claim.")
    note = (note or "").strip()
    if len(note) < 10:
        raise ClaimError("incomplete", "Describe the court action (court, case number, date).")
    counter.status = CopyrightCounterNotice.Status.COURT_ACTION
    counter.court_action_note = note[:1000]
    counter.court_action_reported_at = timezone.now()
    counter.save(update_fields=["status", "court_action_note", "court_action_reported_at"])
    _log(claim, "court_action_reported", actor=claimant, label="claimant", note=note)
    _notify_staff(perms.L2, "Court action reported", "Auto-restore stopped - needs a senior decision.",
                  data={"claim_id": str(claim.id), "role": "staff"})
    return counter


# --------------------------------------------------------------------------
# strikes, standing, accounts
# --------------------------------------------------------------------------
def active_strike_count(user):
    return CopyrightStrike.objects.filter(
        user=user, revoked_at=None, expires_at__gt=timezone.now()).count()


def _level_for_strikes(n):
    if n >= _cfg("COPYRIGHT_REVIEW_AT", 3):
        return CopyrightStanding.Level.REVIEW
    if n >= _cfg("COPYRIGHT_RESTRICT_AT", 2):
        return CopyrightStanding.Level.RESTRICTED
    if n >= 1:
        return CopyrightStanding.Level.WARNING
    return CopyrightStanding.Level.GOOD


def recompute_standing(user):
    """Rebuild the cached standing from the strikes. Terminated stays terminated
    (only an L3 admin can reinstate). Notifies the user / L3 staff on a worse level."""
    L = CopyrightStanding.Level
    order = [L.GOOD, L.WARNING, L.RESTRICTED, L.REVIEW, L.TERMINATED]
    standing, _ = CopyrightStanding.objects.get_or_create(user=user)
    n = active_strike_count(user)
    standing.active_strikes = n
    if standing.level == L.TERMINATED:
        standing.save(update_fields=["active_strikes", "updated_at"])
        return standing
    old, new = standing.level, _level_for_strikes(n)
    standing.level = new
    standing.save(update_fields=["active_strikes", "level", "updated_at"])
    if order.index(new) > order.index(old):
        msgs = {
            L.WARNING: "You have 1 copyright strike. More strikes limit what you can upload.",
            L.RESTRICTED: "Uploads are blocked because of repeated copyright strikes. Strikes expire over time.",
            L.REVIEW: "Your account is under review for repeated copyright infringement.",
        }
        _notify(user, "Copyright standing changed", msgs.get(new, ""), data={"role": "owner", "standing": new})
        if new == L.REVIEW:
            _notify_staff(perms.L3, "Termination review needed",
                          f"{user} reached {n} active copyright strikes.", data={"role": "staff", "user_id": user.id})
    return standing


def can_upload(user):
    """(allowed, message). Hooked into post / story creation."""
    try:
        st = CopyrightStanding.objects.filter(user=user).first()
        if st is not None and st.uploads_blocked:
            if st.level == CopyrightStanding.Level.TERMINATED:
                return False, "This account was closed for repeated copyright infringement."
            return False, ("You can't upload right now because of repeated copyright strikes. "
                           "Strikes expire after a while.")
    except Exception:
        logger.exception("copyright can_upload failed")
    return True, ""


@transaction.atomic
def revoke_strike(strike, actor, reason):
    perms.require_level(actor, perms.L3, "revoking a strike")
    reason = (reason or "").strip()
    if not reason:
        raise ClaimError("incomplete", "A reason is required.")
    strike = CopyrightStrike.objects.select_for_update().get(pk=strike.pk)
    if strike.revoked_at is not None:
        return strike
    strike.revoked_at = timezone.now()
    strike.revoked_by = actor
    strike.revoke_reason = reason[:255]
    strike.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
    _log(strike.claim, "strike_revoked", actor=actor, note=reason, user=strike.user)
    recompute_standing(strike.user)
    _notify(strike.user, "A copyright strike was removed", reason, data={"role": "owner"})
    return strike


@transaction.atomic
def terminate_account(user, actor, note=""):
    perms.require_level(actor, perms.L3, "terminating an account")
    if user.is_staff or user.is_superuser:
        raise ClaimError("protected", "Staff accounts can't be terminated here.")
    standing, _ = CopyrightStanding.objects.get_or_create(user=user)
    standing.level = CopyrightStanding.Level.TERMINATED
    standing.terminated_at = timezone.now()
    standing.terminated_by = actor
    standing.note = (note or "repeat copyright infringement")[:255]
    standing.save()
    type(user).objects.filter(pk=user.pk).update(is_active=False)
    _log(None, "account_terminated", actor=actor, note=note, user=user)
    return standing


@transaction.atomic
def reinstate_account(user, actor, note=""):
    perms.require_level(actor, perms.L3, "reinstating an account")
    standing, _ = CopyrightStanding.objects.get_or_create(user=user)
    standing.level = CopyrightStanding.Level.GOOD
    standing.terminated_at = None
    standing.terminated_by = None
    standing.note = (note or "")[:255]
    standing.save()
    type(user).objects.filter(pk=user.pk).update(is_active=True)
    recompute_standing(user)
    _log(None, "account_reinstated", actor=actor, note=note, user=user)
    return standing


# --------------------------------------------------------------------------
# AUTOMATIONS (called from tasks.py; each is safe to run repeatedly)
# --------------------------------------------------------------------------
def auto_restore_due_counter_notices(now=None):
    """Counter-notice waiting period over, no court action -> content comes back,
    the strike is removed (the claim did not stand)."""
    now = now or timezone.now()
    n = 0
    due = CopyrightCounterNotice.objects.filter(
        status=CopyrightCounterNotice.Status.WAITING, restore_after__lte=now).select_related("claim")
    for counter in due:
        try:
            with transaction.atomic():
                claim = C.objects.select_for_update().get(pk=counter.claim_id)
                if claim.status != C.Status.COUNTERED:
                    continue
                counter.status = CopyrightCounterNotice.Status.RESTORED
                counter.resolved_at = now
                counter.save(update_fields=["status", "resolved_at"])
                _close_and_restore(claim, status=C.Status.RESTORED, label="automation",
                                   note="counter-notice period ended without court action",
                                   revoke_strike_reason="counter-notice: no court action")
                for d in claim.duplicates.filter(status__in=C.OPEN_STATUSES):
                    _close_duplicate(d, C.Status.RESTORED, claim, None)
                _notify(claim.content_owner, "Your content is back",
                        "The claimant did not take legal action in time.",
                        data={"claim_id": str(claim.id), "role": "owner"})
                _notify(claim.claimant, "Content restored",
                        "No court action was reported within the counter-notice period.",
                        data={"claim_id": str(claim.id), "role": "claimant"})
                n += 1
        except Exception:
            logger.exception("auto-restore failed (counter=%s)", counter.pk)
    return n


def expire_strikes(now=None):
    """Recompute standing for everyone whose cached strike count is stale."""
    n = 0
    for st in CopyrightStanding.objects.exclude(active_strikes=0).exclude(
            level=CopyrightStanding.Level.TERMINATED).select_related("user"):
        before = st.level
        st = recompute_standing(st.user)
        if st.level != before:
            n += 1
    return n


def escalate_stale_claims(now=None):
    """Claims nobody touched for COPYRIGHT_SLA_HOURS go to the senior queue, marked urgent."""
    now = now or timezone.now()
    cutoff = now - timedelta(hours=_cfg("COPYRIGHT_SLA_HOURS", 48))
    stale = C.objects.filter(status__in=(C.Status.SUBMITTED, C.Status.UNDER_REVIEW),
                             escalated_at=None, created_at__lte=cutoff)
    n = 0
    for claim in stale:
        claim.escalated_at = now
        claim.priority = C.Priority.URGENT
        claim.required_level = max(claim.required_level, 2)
        claim.save(update_fields=["escalated_at", "priority", "required_level", "updated_at"])
        _log(claim, "auto_escalated", label="automation", note="SLA exceeded")
        n += 1
    if n:
        _notify_staff(perms.L2, "Copyright claims overdue", f"{n} claim(s) passed the review deadline.",
                      data={"role": "staff"})
    return n


def expire_needs_info(now=None):
    """Claimant never answered the information request -> close the claim, restore the content."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=_cfg("COPYRIGHT_NEEDS_INFO_DAYS", 14))
    n = 0
    for claim in C.objects.filter(status=C.Status.NEEDS_INFO, info_requested_at__lte=cutoff):
        try:
            with transaction.atomic():
                claim = C.objects.select_for_update().get(pk=claim.pk)
                if claim.status != C.Status.NEEDS_INFO:
                    continue
                _close_and_restore(claim, status=C.Status.REJECTED, label="automation",
                                   note="no answer to the information request")
                _notify(claim.claimant, "Copyright notice closed",
                        "We didn't get the information we asked for.", data={"claim_id": str(claim.id)})
                _notify(claim.content_owner, "Copyright complaint dismissed",
                        "Your content is available again.", data={"claim_id": str(claim.id), "role": "owner"})
                n += 1
        except Exception:
            logger.exception("expire_needs_info failed (%s)", claim.pk)
    return n
