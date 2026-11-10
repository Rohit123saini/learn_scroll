"""S3 -- "new device signed in" alert.

A login from a device the account has never successfully signed in from (judged from the audit trail,
``AuthAuditLog`` rows with the same ``X-Device-Id``) writes a ``new_device_login`` audit row (shows in the Security
activity list) and, if the user has not turned alerts off (``UserPreference.login_alerts``), e-mails them.

Rules
  * The very first login of an account is never an alert (nothing to compare with).
  * No device id (old app / browser) -> fall back to User-Agent + platform.
  * Call it BEFORE the current login's own audit row is written, so "history" does not include this login.
  * Never raises: an alert must not be able to break a login.
  * Push (R5 / S3b): the same alert also goes out as a push + bell row of type ``NotifType.NEW_DEVICE_LOGIN`` (locked
    type, URGENT tier, tap -> Devices & sessions "Log out this device"). Both channels obey the one account switch
    ``login_alerts``. Sent after commit through Celery; one alert per (user, device) per 10 minutes, so a login that
    passes two checks (e.g. OTP then session start) never pings twice.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils import timezone

from . import audit
from .device_info import extract_device_info
from .models import AuthAuditLog

logger = logging.getLogger(__name__)

LOGIN_EVENTS = (audit.Event.LOGIN_SUCCESS, audit.Event.GOOGLE_LOGIN, audit.Event.OTP_LOGIN)


def is_new_device(user, request) -> bool:
    history = AuthAuditLog.objects.filter(user=user, outcome=audit.SUCCESS, event__in=LOGIN_EVENTS)
    if not history.exists():
        return False  # first ever login
    info = extract_device_info(request)
    if info.get("device_id"):
        return not history.filter(device_id=info["device_id"]).exists()
    ua, platform = info.get("user_agent", ""), info.get("platform", "")
    if not ua:
        return False  # nothing to compare -> stay quiet rather than cry wolf
    return not history.filter(user_agent=ua, platform=platform).exists()


_PUSH_DEDUPE_KEY = "s3b:newdev:{uid}:{dev}"
_PUSH_DEDUPE_SECONDS = 10 * 60


def build_push(device_name: str, ip_address: str, when_text: str) -> tuple[str, str]:
    """(title, body) of the new-device push. Pure."""
    device = (device_name or "").strip() or "an unknown device"
    title = "New sign-in to your account"
    body = f"Signed in from {device} at {when_text}. Not you? Tap to log that device out."
    return title[:140], body[:240]


def push_new_device(user, info: dict, *, method: str = "", when_text: str = "") -> bool:
    """Queue the push + bell row (after commit). True = queued. Never raises."""
    try:
        dev = (info.get("device_id") or info.get("user_agent") or "unknown")[:80]
        if not cache.add(_PUSH_DEDUPE_KEY.format(uid=user.pk, dev=dev), 1, timeout=_PUSH_DEDUPE_SECONDS):
            return False  # this device was just announced
        from core.async_utils import dispatch_after_commit
        from core.models import Notification
        from core.tasks import create_notifications

        title, body = build_push(info.get("device_name", ""), info.get("ip_address", ""), when_text)
        data = {
            "type": Notification.NotifType.NEW_DEVICE_LOGIN.value,
            "kind": "new_device",
            "device_name": info.get("device_name", "") or "",
            "ip_address": info.get("ip_address", "") or "",
            "method": method or "",
            "at": when_text,
        }
        dispatch_after_commit(
            create_notifications, [user.pk], Notification.NotifType.NEW_DEVICE_LOGIN.value, title, body, data, True,
        )
        return True
    except Exception:
        logger.exception("new-device push failed user_id=%s", getattr(user, "pk", None))
        return False


def alerts_enabled(user) -> bool:
    try:
        from user_profile.models import UserPreference

        return bool(UserPreference.for_user(user).login_alerts)
    except Exception:
        return True  # fail safe: alert


def check_new_device(request, user, *, method: str = "") -> bool:
    """Returns True when this login was flagged as a new device."""
    try:
        if not is_new_device(user, request):
            return False
        info = extract_device_info(request)
        audit.record(
            audit.Event.NEW_DEVICE_LOGIN, request, user=user,
            method=method, device_name=info.get("device_name", ""),
        )
        if not alerts_enabled(user):
            return True
        when = timezone.localtime().strftime("%d %b %Y, %H:%M")
        push_new_device(user, info, method=method, when_text=when)  # R5: push + bell row (own try/except)
        if getattr(user, "email", None):
            send_mail(
                subject="New sign-in to your LearnScroll account",
                message=(
                    f"Your account was just signed into from a new device.\n\n"
                    f"Device: {info.get('device_name') or 'Unknown device'}\n"
                    f"Time: {when}\n"
                    f"Approx. IP: {info.get('ip_address') or 'unknown'}\n\n"
                    "If this was you, nothing to do. If not: change your password, turn on two-step verification "
                    "and sign out other devices from Settings > Security."
                ),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[user.email],
                fail_silently=True,
            )
        return True
    except Exception:
        logger.exception("new-device alert failed user_id=%s", getattr(user, "pk", None))
        return False
