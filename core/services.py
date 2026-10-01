"""
core/services.py

create_notification() / create_bulk_notifications() — the single choke
point for writing an in-app notification (bell-row) record. Moved here
from tuitionclass/models.py (task 42).

⚠️ CONTRACT: this module NEVER sends a push, email, sms, or whatsapp
message. It only ever writes a Notification row. Every real call-site
that wants a push too makes TWO calls, side by side:

    create_notification(student, Notification.NotifType.PASS_AUTO_RENEWED, title, message, classroom=classroom)
    send_notification(student, title, message, channel="push", data={...})   # separate, tuitionclass.notifications

N9-BE (quiet hours / DND): "should I send, and on which channels?" is
answered by `channels_for()` below (-> NotificationPreference.
allowed_channels_for). Call-sites should gate their send_notification()
call on it; quiet hours only work if every send path asks.

See core/tests.py::test_never_sends_a_push_itself for the regression test
that guards this contract — if you're tempted to add a push/email send in
here, don't; add it at the call-site instead, exactly like every existing
call-site already does.
"""
import logging

from django.db import transaction

from login.models import User

from .models import Notification, NotificationMute, NotificationPreference

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
    and it must never raise. N6-BE: an actor the recipient has muted
    (core.NotificationMute) is skipped the same way.

    TASK 6 (production_readiness_tasks.md): `actor` used to be consulted
    ONLY for the restrict check above and then discarded — nothing on
    the saved row ever recorded who triggered it, so no client could
    render a tappable "go to that user" row or a quick-action button
    (Follow-back, etc.) without a second lookup it had no id for. Now
    `actor_id`/`actor_username` are folded into `data` automatically
    whenever an actor is given, for every actor-triggered notif_type at
    once (follow, post likes/comments, mentions, ...) rather than
    patching each call site individually. `data.setdefault(...)` so a
    caller's own `data` keys always win over this default.

    Swallows and logs its own errors rather than raising — a notification
    failing to save should never roll back or fail the request/
    transaction (a coin charge, a grade, an accept(), an incoming chat
    message) that triggered it. Returns None on failure instead of
    raising.
    """
    recipient_id = getattr(recipient, "id", recipient)
    payload = dict(data or {})

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

        # N6-BE — recipient muted this actor (NotificationMute): same
        # silent no-op as restrict above, no row at all. Runs for every
        # actor-triggered type at once, like the restrict check.
        if NotificationMute.is_muted(recipient_id, actor_id):
            return None

        payload.setdefault("actor_id", actor_id)
        actor_username = getattr(actor, "username", None)
        if actor_username is None and actor_id is not None:
            # `actor` was passed as a raw id rather than a hydrated User
            # (same calling convention `recipient` already supports
            # above) — one extra indexed lookup, only paid when a call
            # site doesn't already hold the User instance.
            actor_username = User.objects.filter(id=actor_id).values_list("username", flat=True).first()
        if actor_username:
            payload.setdefault("actor_username", actor_username)

    try:
        # Own savepoint: this is called from inside @transaction.atomic
        # views (follow/accept/comment...). Without it, a DB error here is
        # swallowed by the except below but leaves the CALLER's transaction
        # aborted on PostgreSQL, turning "notification failed" into a 500
        # for the real action.
        with transaction.atomic():
            return Notification.objects.create(
                recipient_id=recipient_id,
                notif_type=notif_type,
                title=title,
                message=message,
                classroom=classroom,
                session=session,
                data=payload,
            )
    except Exception:
        logger.exception(
            "Failed to create in-app notification (%s) for user %s", notif_type, recipient_id
        )
        return None


def channels_for(recipient, notif_type: str) -> list:
    """Channels a call-site may use for (recipient, notif_type) right now —
    mute + per-channel toggles + quiet hours/DND (urgent types bypass the
    latter). Empty list = bell row only. `recipient` is a User or raw id."""
    recipient_id = getattr(recipient, "id", recipient)
    pref, _ = NotificationPreference.objects.get_or_create(user_id=recipient_id)
    return pref.allowed_channels_for(notif_type)


# ---------------------------------------------------------------------------
# N8-BE — rich push payload (image + action buttons).
#
# PURE FUNCTION: builds the dict a push needs from an already-saved
# Notification row and NOTHING else. It never imports or calls the push
# layer, so the module CONTRACT above (core never sends) still holds. The
# actual send lives in message/push_utils.py::send_push_for_notification,
# which imports this builder (that direction only — push_utils -> core).
# ---------------------------------------------------------------------------

# Keys of Notification.data worth shipping inside the push so a tap (or an
# action button) can deep-link / call the API without a second lookup.
# Whitelist, not "copy everything": FCM data is capped at ~4KB and `data`
# may hold arbitrary per-type payloads.
PUSH_DATA_KEYS = (
    "post_id", "comment_id", "story_id", "series_id", "conversation_id",
    "message_id", "follow_id", "actor_id", "actor_username",
)

# notif_type -> action buttons. Each action: `id` (the client switches on
# it), `label`, and optional `requires` (a data key that must exist, else
# the button is dropped — e.g. old rows created before follow_id was
# stored). `requires` is stripped from the payload. Add a type here to
# give it buttons; types not listed get image/title/body only.
#
# `requires` may be one key or a tuple of keys (all must be present).
# `input: True` marks a free-text action (Android RemoteInput / iOS
# UNTextInputNotificationAction) — the client shows an inline reply box and
# sends the typed text to the comment / message API. Action ids stay stable
# (client switches on them): confirm_follow = Accept, delete_follow = Decline.
PUSH_ACTIONS = {
    Notification.NotifType.FOLLOW_REQUEST_RECEIVED: (
        {"id": "confirm_follow", "label": "Accept", "requires": "follow_id"},
        {"id": "delete_follow", "label": "Decline", "requires": "follow_id"},
    ),
    Notification.NotifType.FOLLOW_REQUEST_ACCEPTED: (
        {"id": "follow_back", "label": "Follow back", "requires": "actor_id"},
    ),
    # DM / mention: reply into the same conversation.
    Notification.NotifType.CHAT_MESSAGE: (
        {"id": "reply_message", "label": "Reply", "input": True, "requires": "conversation_id"},
    ),
    Notification.NotifType.MENTION: (
        {"id": "reply_message", "label": "Reply", "input": True, "requires": "conversation_id"},
    ),
}

# ⚠️ Comment notif_type names live in core/models.py (not visible when this
# was written) — adjust the candidates to your real NotifType members.
# hasattr-guarded so a wrong name just means "no Reply button", not a crash.
for _name in ("POST_COMMENT", "COMMENT", "POST_COMMENTED", "COMMENT_REPLY"):
    if hasattr(Notification.NotifType, _name):
        PUSH_ACTIONS[getattr(Notification.NotifType, _name)] = (
            {"id": "reply_comment", "label": "Reply", "input": True,
             "requires": ("post_id", "comment_id")},
        )


def _public_image_url(value) -> str | None:
    """FCM/APNs fetch the image themselves, so it must be an absolute
    http(s) URL. A relative path (e.g. a bare /media/... string) is
    dropped rather than sent broken."""
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return value
    return None


def build_push_payload(notification) -> dict:
    """Notification row -> {recipient_id, title, body, image_url, actions,
    data}. Never raises on odd `data` (non-dict, missing keys)."""
    raw = notification.data if isinstance(notification.data, dict) else {}
    data = {k: raw[k] for k in PUSH_DATA_KEYS if raw.get(k) is not None}
    data["notification_id"] = notification.id
    data["notif_type"] = notification.notif_type
    data["category"] = Notification.category_for(notification.notif_type)
    if notification.classroom_id:
        data["classroom_id"] = notification.classroom_id
    if notification.session_id:
        data["session_id"] = notification.session_id

    actions = []
    for spec in PUSH_ACTIONS.get(notification.notif_type, ()):
        needs = spec.get("requires") or ()
        if isinstance(needs, str):
            needs = (needs,)
        if any(data.get(k) is None for k in needs):
            continue
        action = {"id": spec["id"], "label": spec["label"]}
        if spec.get("input"):
            action["input"] = True
        actions.append(action)

    return {
        "recipient_id": notification.recipient_id,
        "title": notification.title,
        "body": notification.message or notification.title,
        "image_url": _public_image_url(raw.get("media_url")),
        "actions": actions,
        "data": data,
    }


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