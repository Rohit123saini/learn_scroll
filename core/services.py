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
from . import notification_rich as rich  # N7 - image / avatar urls, Mark as read + Join now buttons
from .notification_routes import route_for  # N2 - one deep-link table for push + list

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

        # Block (either direction): nothing the other person does may reach
        # my bell. Silent no-op like restrict/mute above.
        from user_profile.services import is_blocked_pair

        if is_blocked_pair(recipient_id, actor_id):
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

    # N1 - the user switched the In-app chip off for this type: no bell row at all (silent no-op, like mute/restrict).
    if not in_app_allowed(recipient_id, notif_type):
        return None

    # N9b - the user muted this class / chat / post / campus section WITH "hide from the bell too": no row at all.
    from . import notification_center as _center

    if _center.bell_hidden_user_ids(
        [recipient_id], notif_type, payload, classroom_id=getattr(classroom, "id", classroom)
    ):
        return None

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


def unread_badge_count(user_id) -> int:
    """Total unread bell rows of one user (all sources) - the number the
    live `notification_badge` socket event carries."""
    return Notification.objects.for_user(user_id).unread().count()


def publish_unread_badge(user_id) -> None:
    """Task 3.3 - push the user's fresh unread total to the `user_<id>` inbox
    socket group (message/consumers.py::InboxConsumer.notification_badge), so
    the bell badge updates live. Runs via transaction.on_commit: the count is
    taken AFTER the row is really saved, and nothing is sent for a rolled-back
    action. Never raises (no channel layer / Redis down = badge refreshes on
    the next app resume instead)."""

    def _send():
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            layer = get_channel_layer()
            if layer is None:
                return
            async_to_sync(layer.group_send)(
                f"user_{user_id}",
                {"type": "notification_badge", "unread_count": unread_badge_count(user_id)},
            )
        except Exception:
            logger.exception("publish_unread_badge failed (user=%s)", user_id)

    try:
        transaction.on_commit(_send)
    except Exception:  # pragma: no cover
        logger.exception("publish_unread_badge could not schedule (user=%s)", user_id)


def in_app_allowed(recipient, notif_type: str) -> bool:
    """N1 - may a bell row be created for (recipient, notif_type)? False only when the user switched the "In-app"
    chip off for that type (locked / safety types always True). One light query; any error => True (never lose a row
    because of a settings glitch)."""
    recipient_id = getattr(recipient, "id", recipient)
    try:
        overrides = (
            NotificationPreference.objects.filter(user_id=recipient_id)
            .values_list("channel_overrides", flat=True).first()
        )
        if not overrides:
            return True
        if notif_type in NotificationPreference.NEVER_MUTE_TYPES:
            return True
        ov = overrides.get(str(notif_type)) if isinstance(overrides, dict) else None
        return not (isinstance(ov, dict) and ov.get("in_app", True) is False)
    except Exception:  # pragma: no cover
        logger.exception("in_app_allowed failed (user=%s)", recipient_id)
        return True


def in_app_blocked_ids(recipient_ids, notif_type: str) -> set:
    """N1b - the subset of `recipient_ids` who switched the "In-app" chip off for `notif_type`, found with ONE query
    (a fan-out to thousands of followers must not cost one query per recipient). Locked types are never blocked; any
    error => empty set (never lose a row because of a settings glitch)."""
    ids = {rid for rid in recipient_ids if rid is not None}
    if not ids or notif_type in NotificationPreference.NEVER_MUTE_TYPES:
        return set()
    try:
        blocked = set()
        rows = NotificationPreference.objects.filter(user_id__in=ids).values_list("user_id", "channel_overrides")
        for user_id, overrides in rows:
            ov = overrides.get(str(notif_type)) if isinstance(overrides, dict) else None
            if isinstance(ov, dict) and ov.get("in_app", True) is False:
                blocked.add(user_id)
        return blocked
    except Exception:  # pragma: no cover
        logger.exception("in_app_blocked_ids failed")
        return set()


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
    "claim_id",  # copyrights: opens the notice / claim screen
    # T1.2 doubt_answered: tap -> that group's Doubts screen (doubt_id lets
    # the client scroll to / open the doubt, answer_id highlights the answer).
    "doubt_id", "answer_id", "group_id",
    # T2.9 — streak-at-risk + class-starting reminder deep links.
    "type", "streak_days", "mode", "live_session_id", "timetable_entry_id",
    "section_id", "subject_id", "starts_at",
    # Push for notice / absent-to-parent / fee due / assignment due: ids a tap can
    # deep-link with (campus notice, which child, which day).
    "notice_id", "student_id", "attendance_date", "campus_id",
    # Parents se baat (PTM + teacher<->parent chat): a tap opens that chat /
    # meeting. `kind` tells the client which flavour (flagged / digest /
    # published / booked / cancelled / reminder_24h ...).
    "thread_id", "event_id", "booking_id", "kind",
    # Class resources: a tap opens that folder / the resource (subject_id +
    # section_id are already in the list above).
    "resource_id", "folder_id",
    # C3 fee-paid push: a tap opens that child's fees.
    "invoice_id", "payment_id",
    # N2 - keys core/notification_routes.py needs for a tap (without these the push tap lands on the fallback):
    # support_reply -> ticket, recorded class -> recording, staff leave -> leave, post_tag + collab_invite -> invites.
    "ticket_id", "recording_id", "leave_id", "collab_invite", "note_id",
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


# N6 - every reminder type gets "Snooze" + "Turn off" buttons (core/notification_reminders.py). A type that already has
# its own buttons keeps them (setdefault); locked types never get Turn off (reminder_types() excludes them).
from . import notification_reminders as _reminders  # noqa: E402  (after PUSH_ACTIONS, needs Notification above)

for _rtype in _reminders.reminder_types():
    PUSH_ACTIONS.setdefault(_rtype, _reminders.REMINDER_ACTIONS)


def _public_image_url(value) -> str | None:
    """FCM/APNs fetch the image themselves, so it must be an absolute
    http(s) URL. N7: a root-relative path (\"/media/x.jpg\", what
    post.services._absolute_media_url returns when MEDIA_ABSOLUTE_BASE_URL is
    unset) is now joined to MEDIA_ABSOLUTE_BASE_URL / SITE_URL instead of being
    dropped; with neither configured it is still dropped rather than sent broken."""
    return rich.absolute_url(value)


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
    # N2 - server-decided deep link ({route, target_type, target_id}); the app still has its own table, this lets an
    # older app route a type it does not know and gives analytics one name per destination. Strings only (FCM data).
    data.update(
        {k: str(v) for k, v in route_for(
            notification.notif_type, raw, classroom_id=notification.classroom_id, session_id=notification.session_id,
        ).as_dict().items()}
    )

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

    # N7 - "Join now" (live class pushes, only when the N2 route really is a class / live room) + "Mark as read"
    # (reply / informational pushes), capped at 3 buttons (Android).
    actions = rich.finalize_actions(actions, notification.notif_type, data)

    return {
        "recipient_id": notification.recipient_id,
        "title": notification.title,
        "body": notification.message or notification.title,
        # N7 - big picture: first usable of media_url / thumbnail_url / cover_url ... (absolute); small round avatar =
        # the actor's photo (shown as the large icon next to / instead of the picture).
        "image_url": rich.thumbnail_url(raw),
        "avatar_url": rich.avatar_url(raw, actor_id=raw.get("actor_id")),
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
    if recipient_ids:  # N1 - drop people who switched the In-app chip off for this type
        recipient_ids -= in_app_blocked_ids(recipient_ids, notif_type)  # one query, not one per recipient
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
        for rid in recipient_ids:  # bulk_create sends no post_save -> publish by hand
            publish_unread_badge(rid)
    except Exception:
        logger.exception(
            "Failed to bulk-create in-app notifications (%s) for %d recipients", notif_type, len(recipient_ids)
        )