"""
user_profile/services.py

TASK 2 — thin `_notify(...)` wrapper around
`core.services.create_notification` for the Follow feature
(FollowAPIView / AcceptFollowRequestView in this app's views.py).

No `assigments`/`testseries` services.py actually exists to copy
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
    # Truly fire-and-forget, as the docstring promises: the follow/accept
    # itself has already succeeded, so a notification problem (the lazy
    # `core` import failing, or create_notification raising) must be logged
    # and swallowed — never turned into a 500 for the user who just
    # followed someone. (The import is lazy only to avoid an import cycle at
    # app-load time; it is resolved fresh on each call.)
    try:
        from core.services import create_notification

        return create_notification(
            recipient,
            notif_type,
            title,
            message,
            data=data or {},
            actor=actor,
        )
    except Exception:
        logger.exception("follow notification failed (recipient=%s, type=%s)", recipient, notif_type)
        return None


# ---------------------------------------------------------------------------
# Issue #2 — restrict lookups for OTHER apps (post / message / core).
#
# `RestrictUser(user=A, restricted=B)` = "A restricted B". These helpers are
# the one place other apps ask that question, so they don't each query the
# table their own way. All of them lazy-import the model (same circular-
# import reason as `_notify` above) and never raise.
#
# Direction cheat-sheet (A restricted B):
#   - B's comments on A's posts are hidden from everyone except B
#   - B's chat/mention pushes + bell rows for A are suppressed
#   - A's read receipts and online/last-seen are hidden FROM B
# B must never be able to detect any of this.
# ---------------------------------------------------------------------------
def restricted_ids_by(user_id):
    """Set of user ids that `user_id` has restricted."""
    from .models import RestrictUser

    if user_id is None:
        return set()
    return set(
        RestrictUser.objects.filter(user_id=user_id).values_list("restricted_id", flat=True)
    )


def restrictor_ids_of(restricted_id, among=None):
    """Set of user ids who have restricted `restricted_id`. Pass `among`
    (an iterable of ids) to look at only those candidates — one indexed
    query however many recipients there are."""
    from .models import RestrictUser

    if restricted_id is None:
        return set()
    qs = RestrictUser.objects.filter(restricted_id=restricted_id)
    if among is not None:
        among = {a for a in among if a is not None}
        if not among:
            return set()
        qs = qs.filter(user_id__in=among)
    return set(qs.values_list("user_id", flat=True))


def has_restricted(restrictor_id, restricted_id):
    """True if `restrictor_id` has restricted `restricted_id` (one-way)."""
    from .models import RestrictUser

    if restrictor_id is None or restricted_id is None:
        return False
    return RestrictUser.objects.filter(user_id=restrictor_id, restricted_id=restricted_id).exists()
