"""
user_profile/services.py

TASK 2 — thin `_notify(...)` wrapper around
`core.services.create_notification` for the Follow feature
(FollowAPIView / AcceptFollowRequestView in this app's views.py).

No `assignment`/`testseries` services.py actually exists to copy
verbatim, so this follows the *pattern* those apps' docstrings point at
instead: `core.services.create_notification` is lazy-imported, never at
module top. That matters here specifically because the dependency
already runs the other way too — `create_notification` itself
lazy-imports `user_profile.views.is_restricted_between` for the
restrict check (see core/services.py's own docstring) — so a top-level
`from core.services import ...` here would risk a real circular import
depending on Django's app-loading order. Centralizing the lazy import
in this one helper means FollowAPIView/AcceptFollowRequestView don't
each need to repeat that boilerplate.

Same contract as create_notification itself: never raises, never sends
a push/email/sms/whatsapp — in-app bell row only.
"""
import logging

logger = logging.getLogger(__name__)


def _notify(recipient, notif_type, title, message="", *, actor=None, data=None):
    """Fire-and-forget in-app notification for a Follow event.

    `recipient` / `actor` may be User instances or raw ids — both pass
    straight through to create_notification, which accepts either.
    """
    from core.services import create_notification

    return create_notification(
        recipient,
        notif_type,
        title,
        message,
        data=data or {},
        actor=actor,
    )