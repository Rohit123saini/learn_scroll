"""
copyrights/notify.py - in-app + push + e-mail for every copyright event.

* in-app row: core.Notification with a DEDICATED type, so the app can open the right screen
  (data.claim_id is also copied into the push payload - see core.services.PUSH_DATA_KEYS):
      copyright_notice    a complaint about YOUR content        (role=owner)
      copyright_decision  outcome for a rights holder           (role=claimant)
      copyright_standing  strikes / uploads blocked             (data.standing)
      copyright_staff     alerts for moderators                 (role=staff)
* push: message.push_utils.send_push_for_notification (honours the user's push settings / muted types)
* e-mail: sent for owner / claimant events regardless of push settings - these are legal notices.
  Goes to the claimant's VERIFIED notice address, or the account e-mail of the content owner.
  Sent after the DB commit, through Celery (inline when COPYRIGHT_MEDIA_ASYNC=False).
Never raises.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction

logger = logging.getLogger(__name__)


def _type_for(data):
    from core.models import Notification

    T = Notification.NotifType
    if (data or {}).get("role") == "staff":
        return T.COPYRIGHT_STAFF
    if (data or {}).get("standing"):
        return T.COPYRIGHT_STANDING
    if (data or {}).get("role") == "owner":
        return T.COPYRIGHT_NOTICE
    return T.COPYRIGHT_DECISION


def _email_for(user, data):
    if (data or {}).get("role") == "claimant" and (data or {}).get("claim_id"):
        from .models import CopyrightClaim

        row = CopyrightClaim.objects.filter(pk=data["claim_id"]).values_list(
            "claimant_email", "pii_erased_at").first()
        if row and row[0] and row[1] is None:      # erased claim: the placeholder address must never be mailed
            return row[0]
    return getattr(user, "email", "") or ""


def send_email_now(to, subject, body):
    try:
        send_mail(subject, body, getattr(settings, "DEFAULT_FROM_EMAIL", None), [to], fail_silently=False)
        return True
    except Exception:
        logger.exception("copyright e-mail to %s failed", to)
        return False


def _email_later(to, subject, body):
    def go():
        if not getattr(settings, "COPYRIGHT_MEDIA_ASYNC", True):
            send_email_now(to, subject, body)
            return
        try:
            from celery import current_app

            current_app.send_task("copyrights.send_email", args=[to, subject, body])
        except Exception:
            logger.exception("could not queue e-mail - sending inline")
            send_email_now(to, subject, body)

    transaction.on_commit(go)


def _email_later_sync(to, subject, body):
    """Verification codes: no footer, same async rules as every other copyright e-mail."""
    if not getattr(settings, "COPYRIGHT_MEDIA_ASYNC", True):
        return send_email_now(to, subject, body)
    try:
        from celery import current_app

        current_app.send_task("copyrights.send_email", args=[to, subject, body])
    except Exception:
        logger.exception("could not queue verification e-mail - sending inline")
        return send_email_now(to, subject, body)


def _footer():
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    link = f"{base}/legal/copyright-policy/" if base else "the Copyright Policy in the app"
    return f"\n\n-- {getattr(settings, 'APP_NAME', 'LearnScroll')}\nCopyright policy: {link}"


def send(user, title, message="", *, data=None):
    if user is None:
        return None
    data = dict(data or {})
    row = None
    try:
        from core.services import create_notification

        payload = {"kind": "copyright"}
        payload.update(data)
        row = create_notification(user, _type_for(data), title, message, data=payload)
    except Exception:
        logger.exception("copyright notification failed")
    try:
        from message.push_utils import send_push_for_notification

        send_push_for_notification(row)
    except Exception:
        logger.exception("copyright push failed")
    if data.get("role") in ("owner", "claimant") and getattr(settings, "COPYRIGHT_EMAILS_ENABLED", True):
        to = _email_for(user, data)
        if to:
            _email_later(to, title, (message or title) + _footer())
    return row
