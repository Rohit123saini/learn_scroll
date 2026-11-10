"""
copyrights/appeals.py - appeals against a copyright PENALTY (audit item 21).

Two different things, do not mix them up:
  counter-notice  (services.file_counter_notice)  contests the CLAIM; if the claimant
                  does not go to court the content comes back.
  appeal          (this file)                     contests what happened to the ACCOUNT:
                    - a STRIKE  ("this strike should not count")
                    - a TERMINATION ("my account should not have been ended")

Strike appeal rules   only the owner; strike still active; within COPYRIGHT_APPEAL_DAYS of
                      issue; one appeal per strike (a rejected one is final); not while a
                      counter-notice on the same claim is still running (that already decides it).
Termination appeal    a terminated account can't log in, so it is filed from outside the app
                      with username-or-email + a contact e-mail. The answer is always the same
                      generic text (we never confirm whether an account exists). After a rejected
                      appeal the person must wait COPYRIGHT_REAPPEAL_DAYS. Staff check identity by
                      replying to the e-mail on file for the account.
Decisions             level-3 only. Whoever issued the strike / terminated the account can't
                      decide the appeal against their own decision.
Everything is written to CopyrightAuditLog.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import permissions as perms
from . import services
from .models import CopyrightAppeal, CopyrightCounterNotice, CopyrightStanding, CopyrightStrike

logger = logging.getLogger(__name__)
A = CopyrightAppeal


def _days(name, default):
    return int(getattr(settings, name, default))


def strike_can_appeal(strike):
    """(bool, reason_if_not)."""
    if strike.revoked_at is not None or strike.expires_at <= timezone.now():
        return False, "This strike is no longer active."
    if strike.issued_at < timezone.now() - timedelta(days=_days("COPYRIGHT_APPEAL_DAYS", 30)):
        return False, "The time to appeal this strike has passed."
    if strike.appeals.exists():
        return False, "This strike was already appealed."
    cn = CopyrightCounterNotice.objects.filter(claim_id=strike.claim_id).first()
    if cn is not None and cn.status in (CopyrightCounterNotice.Status.WAITING, CopyrightCounterNotice.Status.COURT_ACTION):
        return False, "Your counter-notice on this claim is still being processed."
    return True, ""


@transaction.atomic
def file_strike_appeal(user, strike_id, message):
    message = (message or "").strip()
    if len(message) < 10:
        raise services.ClaimError("incomplete", "Please explain why this strike is a mistake.")
    strike = CopyrightStrike.objects.select_related("claim").filter(pk=strike_id, user=user).first()
    if strike is None:
        raise services.ClaimError("not_found", "Strike not found.")
    ok, why = strike_can_appeal(strike)
    if not ok:
        raise services.ClaimError("bad_state", why)
    try:
        appeal = A.objects.create(user=user, kind=A.Kind.STRIKE, strike=strike, message=message[:2000])
    except IntegrityError:
        raise services.ClaimError("duplicate", "You already have an open appeal for this strike.")
    services._log(strike.claim, "strike_appeal_filed", label="owner", note=message[:300], user=user)
    services._notify_staff(perms.L3, "Strike appeal", f"{user} appealed a copyright strike.",
                           data={"role": "staff", "appeal_id": appeal.pk})
    return appeal


GENERIC_TERMINATION_REPLY = (
    "If this account was ended for repeated copyright infringement, your appeal has been sent to our "
    "team. We will reply to the e-mail address you gave."
)


def file_termination_appeal(identifier, contact_email, message):
    """Public entry (no login). ALWAYS returns None to the caller; the view shows the generic reply."""
    from django.contrib.auth import get_user_model

    message = (message or "").strip()
    identifier = (identifier or "").strip()
    if len(message) < 10 or not identifier or not contact_email:
        return None
    User = get_user_model()
    user = User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier)).first()
    if user is None:
        return None
    standing = CopyrightStanding.objects.filter(user=user, level=CopyrightStanding.Level.TERMINATED).first()
    if standing is None:
        return None
    last = A.objects.filter(user=user, kind=A.Kind.TERMINATION, status=A.Status.REJECTED).order_by("-decided_at").first()
    if last and last.decided_at and last.decided_at > timezone.now() - timedelta(days=_days("COPYRIGHT_REAPPEAL_DAYS", 30)):
        services._log(None, "termination_appeal_ignored", label="owner", note="re-appeal too early", user=user)
        return None
    try:
        with transaction.atomic():
            appeal = A.objects.create(user=user, kind=A.Kind.TERMINATION, message=message[:2000],
                                      contact_email=contact_email[:254])
    except IntegrityError:
        return None  # one is already open
    services._log(None, "termination_appeal_filed", label="owner", note=message[:300], user=user)
    services._notify_staff(perms.L3, "Termination appeal", f"{user} appealed a copyright termination.",
                           data={"role": "staff", "appeal_id": appeal.pk})
    return appeal


@transaction.atomic
def decide_appeal(appeal, actor, accept, note=""):
    perms.require_level(actor, perms.L3, "deciding an appeal")
    appeal = A.objects.select_for_update().select_related("strike", "strike__claim", "user").get(pk=appeal.pk)
    if appeal.status != A.Status.OPEN:
        raise services.ClaimError("bad_state", "This appeal is already decided.")
    note = (note or "").strip()
    if not accept and not note:
        raise services.ClaimError("incomplete", "Give a reason when rejecting an appeal.")

    if appeal.kind == A.Kind.STRIKE:
        if appeal.strike.issued_by_id == actor.pk:
            raise services.ClaimError("four_eyes", "Another L3 reviewer must decide an appeal against your own strike.")
        if accept:
            services.revoke_strike(appeal.strike, actor, note or "appeal accepted")
        action = "strike_appeal_accepted" if accept else "strike_appeal_rejected"
        claim = appeal.strike.claim
    else:
        standing = CopyrightStanding.objects.filter(user=appeal.user).first()
        if standing is not None and standing.terminated_by_id == actor.pk:
            raise services.ClaimError("four_eyes", "Another L3 reviewer must decide an appeal against your own termination.")
        if accept:
            services.reinstate_account(appeal.user, actor, note or "appeal accepted")
        action = "termination_appeal_accepted" if accept else "termination_appeal_rejected"
        claim = None

    appeal.status = A.Status.ACCEPTED if accept else A.Status.REJECTED
    appeal.decided_by, appeal.decided_at, appeal.decision_note = actor, timezone.now(), note[:500]
    appeal.save()
    services._log(claim, action, actor=actor, note=note, user=appeal.user)

    if appeal.kind == A.Kind.STRIKE:
        services._notify(
            appeal.user,
            "Your strike appeal was accepted" if accept else "Your strike appeal was rejected",
            "The strike was removed." if accept else (note or "The strike stands."),
            data={"role": "owner", "appeal_id": appeal.pk},
        )
    else:
        _email_termination_decision(appeal, accept)
    return appeal


def _email_termination_decision(appeal, accept):
    """The account is closed (can't see in-app notices), so the answer goes by e-mail. Never raises."""
    try:
        from django.core.mail import send_mail

        to = appeal.contact_email or appeal.user.email
        if not to:
            return
        body = (
            "Your appeal was accepted and your account is active again. Please follow the copyright policy from now on."
            if accept else
            "We reviewed your appeal and the termination stands. " + (appeal.decision_note or "")
            + "\nIf you still disagree you can write to our Grievance Officer (see the Copyright Policy page)."
        )
        send_mail("Your copyright appeal", body, getattr(settings, "DEFAULT_FROM_EMAIL", None), [to], fail_silently=True)
    except Exception:
        logger.exception("termination appeal e-mail failed")
