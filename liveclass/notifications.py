# liveclass/notifications.py
"""
Single place every "tell a user something happened" call goes through,
regardless of channel (push / email / sms). Two things this buys us:

    1. Callers (signals.py, tasks.py) never touch a provider SDK directly —
       swap providers, or plug in a real SMS provider, in ONE file, without
       touching call sites.
    2. A missing/misconfigured provider degrades to a logged warning
       instead of raising — a waitlist seat opening up, or a reminder
       firing, should never fail (and roll back a DB transaction, or crash
       a Celery task) just because a channel isn't fully configured.

STATUS OF EACH CHANNEL RIGHT NOW:
    - push: reuses the `message` app's EXISTING Firebase wiring
      (`message.push_utils.send_push_to_users` — DeviceToken model,
      firebase_admin already initialized there, invalid-token cleanup
      already handled). liveclass does NOT initialize its own Firebase app
      — two firebase_admin.initialize_app() calls in the same process is
      redundant, and the message app's module already guards against
      double-init via `if not firebase_admin._apps`. If your push module
      ever moves to a different path/name than `message.push_utils`,
      fix the import inside _send_push below — that's the only place it's
      used.
    - email: fully wired — settings.py already has real SMTP config
      (EMAIL_HOST/EMAIL_HOST_USER/etc.), this just calls send_mail().
    - sms: no provider integrated yet — pick one (Twilio / MSG91 / etc.),
      add its credentials to settings.py + .env, and fill in _send_sms
      with the actual API call. Logs and no-ops until then.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _send_push(user, title: str, message: str, data: dict | None = None) -> bool:
    """Delegates to the message app's existing multicast+cleanup logic
    instead of re-implementing Firebase send/token-lookup here. Lazy
    import so a missing/renamed message-app module degrades to a logged
    warning (liveclass still works, push just no-ops) instead of breaking
    Django's app loading — signals.py imports tasks.py which imports this
    module, and that chain runs very early during app startup.
    """
    try:
        from message.push_utils import send_push_to_users
    except ImportError:
        logger.warning(
            "Could not import send_push_to_users from message.push_utils "
            "— confirm the actual module path in your project and fix the "
            "import inside liveclass/notifications.py._send_push. "
            "Push no-op'd for user %s.", user.id,
        )
        return False

    try:
        # NOTE: send_push_to_users (message app) doesn't return a per-call
        # success/failure count today — it logs its own FCM failures and
        # cleans up invalid tokens internally. Returning True here means
        # "handed off to the push pipeline", not "confirmed delivered" —
        # good enough for is_sent bookkeeping in tasks.send_due_reminders.
        send_push_to_users([user.id], title, message, data=data or {})
        return True
    except Exception:
        logger.exception("Push send failed for user %s", user.id)
        return False


def _send_email(user, title: str, message: str, data: dict | None = None) -> bool:
    if not getattr(user, "email", None):
        logger.info("No email on file for user %s — skipping email notification.", user.id)
        return False
    try:
        send_mail(
            subject=title,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("Email send failed for user %s", user.id)
        return False


def _send_sms(user, title: str, message: str, data: dict | None = None) -> bool:
    # No SMS provider wired yet — pick one and implement the real API call
    # here. Keep the same signature so tasks.py / signals.py never need to
    # change when this fills in.
    phone = getattr(user, "phone_number", None) or getattr(user, "mobile", None)
    logger.warning(
        "SMS notification requested for user %s (phone=%s) but no SMS "
        "provider is configured yet — no-op.", user.id, phone,
    )
    return False


_SENDERS = {"push": _send_push, "email": _send_email, "sms": _send_sms}


def send_notification(user, title: str, message: str, channel: str = "push", data: dict | None = None) -> bool:
    """Best-effort — NEVER raises. Returns True/False so a caller can log or
    decide whether to retry, but a failed/unconfigured channel must never
    break the DB transaction or task that triggered it (a LiveKit teardown,
    a waitlist promotion, a reminder firing all matter more than a push
    notification succeeding).

    `data`: optional dict merged into the push payload's data field (push
    channel only — ignored by email/sms) — same pattern the message app
    already uses for `type`/`conversation_id`/etc. so the Flutter client
    can route a liveclass push the same way it routes a chat/call one.
    Pass a `type` key (e.g. "class_reminder", "waitlist_seat_open") plus
    whatever ids the client needs to deep-link.
    """
    sender = _SENDERS.get(channel)
    if sender is None:
        logger.warning("Unknown notification channel %r for user %s", channel, user.id)
        return False
    try:
        return sender(user, title, message, data=data)
    except Exception:
        logger.exception("Notification send crashed unexpectedly (channel=%s, user=%s)", channel, user.id)
        return False