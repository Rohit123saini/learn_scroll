# tuitionclass/notifications.py
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
      already handled). tuitionclass does NOT initialize its own Firebase app
      — two firebase_admin.initialize_app() calls in the same process is
      redundant, and the message app's module already guards against
      double-init via `if not firebase_admin._apps`. If your push module
      ever moves to a different path/name than `message.push_utils`,
      fix the import inside _send_push below — that's the only place it's
      used.
    - email: fully wired — settings.py already has real SMTP config
      (EMAIL_HOST/EMAIL_HOST_USER/etc.), this just calls send_mail().
    - sms: WIRED (Pass 7) — MSG91's plain HTTP SMS API (India-focused;
      no SDK dependency, just requests + an API key). Needs
      MSG91_AUTH_KEY and MSG91_SMS_SENDER_ID in settings/.env — see
      _send_sms below and §10 of the audit doc. Degrades to a logged
      warning + no-op if either is unset, same as every other
      unconfigured-channel path in this file.
    - whatsapp: WIRED (Pass 7) — MSG91's WhatsApp Business API (same
      provider as SMS, one fewer vendor relationship to manage; swap for
      Twilio/Gupshup/etc. by editing _send_whatsapp only, same "this file
      is the one place that knows about a transport" contract as
      everything else here). Needs MSG91_AUTH_KEY (shared with SMS) and
      MSG91_WHATSAPP_INTEGRATED_NUMBER + a pre-approved
      MSG91_WHATSAPP_TEMPLATE_NAME (WhatsApp Business requires an
      approved template for any business-initiated message — you cannot
      send free-form text outside a 24h user-initiated session window,
      this is a WhatsApp platform rule, not an MSG91 limitation). Also
      degrades to a logged warning + no-op if unconfigured.

Both new channels use INR-cheap, India-first providers, and both share
the same `user.phone_number`/`user.mobile` lookup — see _phone_for below,
the one place that attribute-name fallback lives so sms/whatsapp never
drift out of sync with each other on it.
"""

import logging

import requests
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

MSG91_BASE_URL = "https://control.msg91.com/api/v5"


def _phone_for(user) -> str | None:
    """Single lookup shared by SMS and WhatsApp so the two channels can
    never drift on which attribute they trust — NOTE (fix): the old
    _send_sms had this fallback inline and _send_whatsapp (new in this
    pass) would otherwise have had to duplicate it, with an easy chance
    of the two silently disagreeing after a future edit to one but not
    the other."""
    return getattr(user, "phone_number", None) or getattr(user, "mobile", None)


def _send_push(user, title: str, message: str, data: dict | None = None) -> bool:
    """Delegates to the message app's existing multicast+cleanup logic
    instead of re-implementing Firebase send/token-lookup here. Lazy
    import so a missing/renamed message-app module degrades to a logged
    warning (tuitionclass still works, push just no-ops) instead of breaking
    Django's app loading — signals.py imports tasks.py which imports this
    module, and that chain runs very early during app startup.
    """
    try:
        from message.push_utils import send_push_to_users
    except ImportError:
        logger.warning(
            "Could not import send_push_to_users from message.push_utils "
            "— confirm the actual module path in your project and fix the "
            "import inside tuitionclass/notifications.py._send_push. "
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
    """MSG91 plain HTTP SMS API. `title` is unused (SMS has no subject) —
    kept in the signature only so every _SENDERS callable has the exact
    same shape and send_notification() never needs a channel-specific
    branch to call one.

    Needs MSG91_AUTH_KEY + MSG91_SMS_SENDER_ID (a DLT-registered 6-char
    sender id, mandatory for transactional SMS to Indian numbers) in
    settings/.env — see §10 of the audit doc. No account = logged
    warning + no-op, same degrade-gracefully contract as _send_push above
    when the message app's push module can't be imported.
    """
    phone = _phone_for(user)
    if not phone:
        logger.info("No phone number on file for user %s — skipping SMS notification.", user.id)
        return False

    auth_key = getattr(settings, "MSG91_AUTH_KEY", None)
    sender_id = getattr(settings, "MSG91_SMS_SENDER_ID", None)
    if not auth_key or not sender_id:
        logger.warning(
            "MSG91_AUTH_KEY/MSG91_SMS_SENDER_ID not configured — SMS "
            "no-op'd for user %s (phone=%s).", user.id, phone,
        )
        return False

    try:
        resp = requests.post(
            f"{MSG91_BASE_URL}/flow/",
            # NOTE: MSG91's simplest send shape (no pre-registered DLT
            # flow/template id) is their legacy `/api/sendhttp.php`
            # endpoint, not v5/flow — swap this call for that endpoint
            # if your MSG91 account isn't set up with a Flow template.
            # Left as v5/flow here since it's MSG91's currently
            # recommended integration path; either way this is the only
            # function that needs to change.
            headers={"authkey": auth_key, "Content-Type": "application/json"},
            json={
                "sender": sender_id,
                "route": "4",  # transactional route
                "country": "91",
                "sms": [{"message": message, "to": [phone]}],
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException:
        logger.exception("SMS send failed for user %s (phone=%s)", user.id, phone)
        return False


def _send_whatsapp(user, title: str, message: str, data: dict | None = None) -> bool:
    """MSG91 WhatsApp Business API.

    IMPORTANT — WhatsApp platform rule, not an MSG91 limitation: a
    business can only send a FREE-FORM message inside a 24-hour window
    after the user last messaged the business's WhatsApp number.
    Outside that window (which covers every tuitionclass reminder/alert —
    the user never DMs the classroom's WhatsApp number first), the
    message MUST use a pre-approved template. `message` here is passed
    as the template's single body variable — if your approved template
    has more than one placeholder, this needs to build a list instead;
    see MSG91's WhatsApp docs for the exact `to_and_components` shape
    once you have a real template approved.

    Needs MSG91_AUTH_KEY (shared with SMS above) +
    MSG91_WHATSAPP_INTEGRATED_NUMBER + MSG91_WHATSAPP_TEMPLATE_NAME —
    see §10. No-ops with a logged warning if any are missing.
    """
    phone = _phone_for(user)
    if not phone:
        logger.info("No phone number on file for user %s — skipping WhatsApp notification.", user.id)
        return False

    auth_key = getattr(settings, "MSG91_AUTH_KEY", None)
    integrated_number = getattr(settings, "MSG91_WHATSAPP_INTEGRATED_NUMBER", None)
    template_name = getattr(settings, "MSG91_WHATSAPP_TEMPLATE_NAME", None)
    if not auth_key or not integrated_number or not template_name:
        logger.warning(
            "MSG91_AUTH_KEY/MSG91_WHATSAPP_INTEGRATED_NUMBER/"
            "MSG91_WHATSAPP_TEMPLATE_NAME not fully configured — WhatsApp "
            "no-op'd for user %s (phone=%s).", user.id, phone,
        )
        return False

    try:
        resp = requests.post(
            f"{MSG91_BASE_URL}/whatsapp/whatsapp-outbound-message/bulk/",
            headers={"authkey": auth_key, "Content-Type": "application/json"},
            json={
                "integrated_number": integrated_number,
                "content_type": "template",
                "payload": {
                    "messaging_product": "whatsapp",
                    "type": "template",
                    "template": {
                        "name": template_name,
                        "language": {"code": "en", "policy": "deterministic"},
                        "to_and_components": [
                            {"to": [phone], "components": {"body_1": {"type": "text", "value": message}}}
                        ],
                    },
                },
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException:
        logger.exception("WhatsApp send failed for user %s (phone=%s)", user.id, phone)
        return False


_SENDERS = {"push": _send_push, "email": _send_email, "sms": _send_sms, "whatsapp": _send_whatsapp}


def send_notification(user, title: str, message: str, channel: str = "push", data: dict | None = None) -> bool:
    """Best-effort — NEVER raises. Returns True/False so a caller can log or
    decide whether to retry, but a failed/unconfigured channel must never
    break the DB transaction or task that triggered it (a LiveKit teardown,
    a waitlist promotion, a reminder firing all matter more than a push
    notification succeeding).

    `data`: optional dict merged into the push payload's data field (push
    channel only — ignored by email/sms/whatsapp) — same pattern the
    message app already uses for `type`/`conversation_id`/etc. so the
    Flutter client can route a tuitionclass push the same way it routes a
    chat/call one. Pass a `type` key (e.g. "class_reminder",
    "waitlist_seat_open") plus whatever ids the client needs to deep-link.
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


# ---------------------------------------------------------------------------
# TASK 9.2 — "a test was published in your class".
#
# Two separate steps, mirroring the rest of this app (bell row first, push is a
# SEPARATE best-effort call — core.services never sends a push itself):
#   notify_testseries_published()  one bulk INSERT of bell rows (cheap, inline)
#   push_testseries_published()    FCM push, only to users whose preferences
#                                  allow it right now (mute / push toggle /
#                                  quiet hours via core.services.channels_for);
#                                  run from a Celery task so a big class never
#                                  slows the publish request down.
# ---------------------------------------------------------------------------
def testseries_notification_data(*, classroom_id, series_id, notice_id=None) -> dict:
    """Payload shared by the bell row and the push, so a tap on either opens the
    class (classroom_id) and knows which test to show (series_id)."""
    data = {
        "type": "testseries_posted",
        "series_id": str(series_id),
        "classroom_id": str(classroom_id),
        "context_type": "classroom",
    }
    if notice_id is not None:
        data["notice_id"] = str(notice_id)
    return data


def notify_testseries_published(*, classroom, series_id, notice_id, title, body, user_ids) -> int:
    """Bell rows (Notification TESTSERIES_POSTED, linked to the classroom) for
    `user_ids`. Returns how many distinct users it was created for."""
    ids = [uid for uid in dict.fromkeys(user_ids) if uid is not None]
    if not ids:
        return 0
    from core.models import Notification
    from core.services import create_bulk_notifications

    data = testseries_notification_data(classroom_id=classroom.id, series_id=series_id, notice_id=notice_id)
    # `classroom_id` in `data` is a string for the push; the bell row keeps the
    # integer FK on `classroom=` (that is what the app routes on).
    create_bulk_notifications(
        ids, Notification.NotifType.TESTSERIES_POSTED, title, body, classroom=classroom, data=data,
    )
    return len(ids)


def push_testseries_published(*, user_ids, title, body, data) -> int:
    """Push to every user in `user_ids` whose notification preferences allow a
    push for TESTSERIES_POSTED right now. Never raises. Returns the number of
    users it was handed to the push pipeline for."""
    try:
        from core.models import Notification
        from core.services import channels_for
    except ImportError:  # pragma: no cover - core is a required app
        logger.warning("core not importable - testseries push skipped")
        return 0

    allowed = []
    for uid in dict.fromkeys(user_ids):
        try:
            if "push" in channels_for(uid, Notification.NotifType.TESTSERIES_POSTED):
                allowed.append(uid)
        except Exception:
            logger.exception("could not read notification preferences of user %s - skipping push", uid)
    if not allowed:
        return 0
    try:
        from message.push_utils import send_push_to_users

        send_push_to_users(allowed, title, body, data=data)
    except Exception:
        logger.exception("testseries push failed for %d user(s)", len(allowed))
        return 0
    return len(allowed)
