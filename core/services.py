"""
core/services.py

create_notification() / create_bulk_notifications() — the single choke
point for writing an in-app notification (bell-row) record. Moved here
from liveclass/models.py (task 42).

⚠️ CONTRACT: this module NEVER sends a push, email, sms, or whatsapp
message. It only ever writes a Notification row. Every real call-site
that wants a push too makes TWO calls, side by side:

    create_notification(student, Notification.NotifType.PASS_AUTO_RENEWED, title, message, classroom=classroom)
    send_notification(student, title, message, channel="push", data={...})   # separate, liveclass.notifications

See core/tests.py::test_never_sends_a_push_itself for the regression test
that guards this contract — if you're tempted to add a push/email send in
here, don't; add it at the call-site instead, exactly like every existing
call-site already does.
"""
import logging

from .models import Notification

logger = logging.getLogger(__name__)


def create_notification(
    recipient,
    notif_type: str,
    title: str,
    message: str = "",
    *,
    classroom=None,
    session=None,
    data: dict | None = None,
) -> "Notification | None":
    """Single choke point for writing an in-app notification row.

    `recipient` can be a User instance OR a raw user id — message/
    push_utils.py's call-sites only ever have recipient_ids (ints/strs),
    not hydrated User objects, so accepting either avoids an extra query
    per push at every call-site.

    Swallows and logs its own errors rather than raising — a notification
    failing to save should never roll back or fail the request/
    transaction (a coin charge, a grade, an accept(), an incoming chat
    message) that triggered it. Returns None on failure instead of
    raising.
    """
    recipient_id = getattr(recipient, "id", recipient)
    try:
        return Notification.objects.create(
            recipient_id=recipient_id,
            notif_type=notif_type,
            title=title,
            message=message,
            classroom=classroom,
            session=session,
            data=data or {},
        )
    except Exception:
        logger.exception(
            "Failed to create in-app notification (%s) for user %s", notif_type, recipient_id
        )
        return None


def create_bulk_notifications(
    recipients,
    notif_type: str,
    title: str,
    message: str = "",
    *,
    classroom=None,
    session=None,
    data: dict | None = None,
) -> None:
    """Bulk variant for fan-out cases (e.g. an urgent notice to every
    enrolled student) — one INSERT instead of N. recipients can be any
    iterable of User (or user id) — deduplicated defensively since a
    teacher/co-teacher could otherwise appear twice (once as staff, once
    as an enrolled student)."""
    recipient_ids = {getattr(r, "id", r) for r in recipients}
    recipient_ids.discard(None)
    if not recipient_ids:
        return
    try:
        Notification.objects.bulk_create(
            [
                Notification(
                    recipient_id=rid,
                    notif_type=notif_type,
                    title=title,
                    message=message,
                    classroom=classroom,
                    session=session,
                    data=data or {},
                )
                for rid in recipient_ids
            ],
            batch_size=500,
        )
    except Exception:
        logger.exception(
            "Failed to bulk-create in-app notifications (%s) for %d recipients", notif_type, len(recipient_ids)
        )