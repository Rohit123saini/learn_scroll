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
    actor=None,
) -> "Notification | None":
    """Single choke point for writing an in-app notification row.

    `recipient` can be a User instance OR a raw user id — message/
    push_utils.py's call-sites only ever have recipient_ids (ints/strs),
    not hydrated User objects, so accepting either avoids an extra query
    per push at every call-site.

    `actor` (G-3, RestrictUser wiring): the user whose action triggered
    this notification — who commented, liked, followed, etc. — if any.
    System/admin-triggered notifications (pass auto-renewal, a broadcast)
    have no actor and should not pass one; the restrict check below is
    skipped for those. When `actor` IS given and `recipient` has
    restricted them (user_profile.RestrictUser), the notification row is
    silently skipped — restrict is defined to be invisible to the
    restricted user, so this must be a silent no-op, not a logged error,
    and it must never raise.

    Swallows and logs its own errors rather than raising — a notification
    failing to save should never roll back or fail the request/
    transaction (a coin charge, a grade, an accept(), an incoming chat
    message) that triggered it. Returns None on failure instead of
    raising.
    """
    recipient_id = getattr(recipient, "id", recipient)

    if actor is not None:
        actor_id = getattr(actor, "id", actor)
        # Lazy import (same pattern as campus/bridge.py): core must not
        # hard-depend on user_profile at module-import time, to avoid a
        # circular import between the two apps. Reuses the same
        # is_restricted_between() user_profile.views already exposes for
        # is_blocked_between()-style checks, rather than re-querying
        # RestrictUser directly here. Django FK filters accept a raw pk
        # in place of an instance, so passing the ids straight through
        # (instead of fetching User objects) costs no extra query.
        from user_profile.views import is_restricted_between

        if is_restricted_between(recipient_id, actor_id):
            return None

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