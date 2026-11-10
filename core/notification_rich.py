"""
core/notification_rich.py - N7: rich notifications (image preview, avatar, buttons, grouping, icon badge).

What N8-BE (core/services.py::build_push_payload) already gave a push: one image (`data.media_url`), Accept / Decline /
Follow back / Reply buttons, Snooze / Turn off on reminders. N7 adds the missing pieces, all PURE helpers here so the
rules sit in one place and can be tested without Firebase:

  * image preview   - `thumbnail_url(data)` (post thumbnail, story, cover ...) + `avatar_url(data)` (who did it). Both are
                      made ABSOLUTE: `post.services._absolute_media_url` falls back to a bare "/media/x.jpg" when
                      MEDIA_ABSOLUTE_BASE_URL is unset, and the old `_public_image_url` then silently dropped the image.
  * buttons         - "Mark as read" on conversational / informational pushes, "Join now" on live-class pushes.
                      Android shows at most 3 buttons: `finalize_actions` drops "Mark as read" first.
  * iOS categories  - iOS has no server-sent buttons; the app registers categories by NAME. `APNS_CATEGORIES` is the single
                      table (mirrored in Flutter `kPushIosCategories`, parity is tested).
  * grouping        - `group_key(channel)` -> same key on the server (iOS thread / data) and the Android summary notification.
  * icon badge      - `badge_count_for(user_id)` = unread bell rows of the user (same number the in-app bell + Chats badge add up to).

Nothing here sends a push (core never sends - see core/services.py). message/push_utils.py calls these.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------------------------------------
# action ids / specs (ids are a contract with Flutter `_handleRichAction`)
# --------------------------------------------------------------------------------------------------------------------
ACTION_MARK_READ = "mark_read"
ACTION_JOIN_NOW = "join_now"

MARK_READ_ACTION = {"id": ACTION_MARK_READ, "label": "Mark as read"}
JOIN_NOW_ACTION = {"id": ACTION_JOIN_NOW, "label": "Join now"}

MAX_ACTIONS = 3  # Android collapses everything after 3 buttons (the Flutter side also `take(3)`s)

# Types that get "Join now": by member NAME (resolved lazily, a renamed member just drops out - same trick as
# notification_priority). The button only appears when the server-decided route really is a live / class screen.
JOIN_NOW_TYPE_NAMES = ("SESSION_REMINDER", "SESSION_LIVE", "CAMPUS_SESSION_LIVE")
JOIN_NOW_ROUTES = frozenset({"classroom", "campusLive"})

# A push that already has one of these buttons may ALSO get "Mark as read" (a reply box + "I've seen it"). Every other
# button set (Accept / Decline, Follow back, Snooze / Turn off) is a decision of its own - adding a 3rd, unrelated button
# would only push the real ones out of the collapsed view.
_MARK_READ_COMPANIONS = frozenset({"reply_message", "reply_comment", ACTION_JOIN_NOW})


def mark_read_button_enabled() -> bool:
    """Ops switch (env via settings): NOTIF_PUSH_MARK_READ_BUTTON, default ON."""
    return bool(getattr(settings, "NOTIF_PUSH_MARK_READ_BUTTON", True))


def join_now_types() -> frozenset:
    from .models import Notification

    out = set()
    for name in JOIN_NOW_TYPE_NAMES:
        member = getattr(Notification.NotifType, name, None)
        if member is not None:
            out.add(member.value)
    return frozenset(out)


def join_now_eligible(notif_type, data) -> bool:
    """\"Join now\" only when the server-decided deep link (N2) is a live class / classroom. A row whose target is gone
    (route fell back to `unavailable`) must not offer a button that leads nowhere."""
    if str(notif_type) not in join_now_types():
        return False
    route = (data or {}).get("route")
    return str(route) in JOIN_NOW_ROUTES


def mark_read_eligible(notif_type, action_ids) -> bool:
    """Should this push carry \"Mark as read\"? Not for reminders (Snooze / Turn off already), not for locked / safety types
    (those must stay in the tray until the user opens them), and only next to reply / join buttons or alone."""
    if not mark_read_button_enabled():
        return False
    from .models import Notification
    from . import notification_reminders as reminders

    nt = str(notif_type)
    if reminders.is_reminder(nt) or nt in Notification.locked_types():
        return False
    ids = set(action_ids)
    if ACTION_MARK_READ in ids:
        return False
    return ids <= _MARK_READ_COMPANIONS  # empty set is a subset: informational push -> just "Mark as read"


def finalize_actions(actions, notif_type, data) -> list:
    """`actions` = [{id, label, input?}] from PUSH_ACTIONS. Adds Join now (first = primary) and Mark as read (last = first to
    be dropped), then caps at MAX_ACTIONS. Pure; the input list is not modified."""
    out = list(actions or [])
    if join_now_eligible(notif_type, data) and not any(a.get("id") == ACTION_JOIN_NOW for a in out):
        out.insert(0, dict(JOIN_NOW_ACTION))
    if mark_read_eligible(notif_type, [a.get("id") for a in out]):
        out.append(dict(MARK_READ_ACTION))
    return out[:MAX_ACTIONS]


# --------------------------------------------------------------------------------------------------------------------
# iOS categories - ONE table. action id -> category (first action of a push decides), category -> its buttons.
# Mirror: lib/notifications/notification_rich_logic.dart `kPushIosCategories` (parity test in tests_notification_rich.py).
# --------------------------------------------------------------------------------------------------------------------
CATEGORY_BY_ACTION_ID = {
    "confirm_follow": "FOLLOW_REQUEST",
    "delete_follow": "FOLLOW_REQUEST",
    "follow_back": "FOLLOW_BACK",
    "reply_comment": "COMMENT_REPLY",
    "reply_message": "MESSAGE_REPLY",
    "snooze_reminder": "REMINDER",
    "turn_off_type": "REMINDER",
    "open_reminder": "REMINDER",  # R9
    ACTION_JOIN_NOW: "JOIN_NOW",
    ACTION_MARK_READ: "MARK_READ",
}

APNS_CATEGORIES = {
    "FOLLOW_REQUEST": ("confirm_follow", "delete_follow"),
    "FOLLOW_BACK": ("follow_back",),
    "COMMENT_REPLY": ("reply_comment", ACTION_MARK_READ),
    "MESSAGE_REPLY": ("reply_message", ACTION_MARK_READ),
    "REMINDER": ("snooze_reminder", "turn_off_type", "open_reminder"),
    "JOIN_NOW": (ACTION_JOIN_NOW, ACTION_MARK_READ),
    "MARK_READ": (ACTION_MARK_READ,),
}


def category_for_actions(actions):
    """APNs category for a push = the category of its FIRST button that has one (None = no buttons)."""
    for a in actions or ():
        cat = CATEGORY_BY_ACTION_ID.get(a.get("id"))
        if cat:
            return cat
    return None


# --------------------------------------------------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------------------------------------------------
_THUMB_KEYS = ("media_url", "thumbnail_url", "thumb_url", "image_url", "cover_url")
_AVATAR_KEYS = ("actor_photo", "actor_avatar", "actor_avatar_url")


def _base_url() -> str:
    return (getattr(settings, "MEDIA_ABSOLUTE_BASE_URL", "") or getattr(settings, "SITE_URL", "") or "").rstrip("/")


def absolute_url(value):
    """http(s) URL as is; a root-relative \"/media/x.jpg\" is joined to MEDIA_ABSOLUTE_BASE_URL (else SITE_URL); anything
    else (None, \"\", bare name, other scheme) -> None. FCM / the phone fetch the image themselves, so it must be absolute."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith(("https://", "http://")):
        return value
    if value.startswith("/") and not value.startswith("//"):
        base = _base_url()
        if base:
            return base + value
    return None


def thumbnail_url(raw):
    """Big picture of the push (post / story / cover thumbnail). First usable key wins; None if there is none."""
    if not isinstance(raw, dict):
        return None
    for key in _THUMB_KEYS:
        url = absolute_url(raw.get(key))
        if url:
            return url
    return None


def avatar_url(raw, actor_id=None):
    """Small round picture = the person who did it. Order: a ready URL in the row data -> first of a batched row's
    `actors_preview` (N1-BE, already absolute) -> the actor's profile photo (ONE indexed query, only when an actor id is
    known). Never raises."""
    raw = raw if isinstance(raw, dict) else {}
    for key in _AVATAR_KEYS:
        url = absolute_url(raw.get(key))
        if url:
            return url
    preview = raw.get("actors_preview")
    if isinstance(preview, list) and preview and isinstance(preview[0], dict):
        url = absolute_url(preview[0].get("photo"))
        if url:
            return url
    if actor_id in (None, ""):
        return None
    try:
        from login.models import User

        user = User.objects.only("id", "profile_photo").filter(pk=actor_id).first()
        field = getattr(user, "profile_photo", None) if user else None
        return absolute_url(field.url) if field else None
    except Exception:
        logger.debug("avatar lookup failed for actor %s", actor_id, exc_info=True)
        return None


# --------------------------------------------------------------------------------------------------------------------
# grouping + badge
# --------------------------------------------------------------------------------------------------------------------
GROUP_PREFIX = "ls.group."


def group_key(channel_id) -> str:
    """Android bundle key / iOS thread: one group per notification channel (social, classes, tests, wallet ...)."""
    cid = str(channel_id or "").strip() or "notifications"
    return f"{GROUP_PREFIX}{cid}"[:64]


BADGE_MAX = 99999


def clamp_badge(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(n, BADGE_MAX))


def badge_count_for(user_id):
    """Unread bell rows of one user (all sources) - what the app icon should show. None if it cannot be computed (the push
    is then sent without a badge change instead of badge 0, which would wipe a correct number)."""
    try:
        from .services import unread_badge_count

        return clamp_badge(unread_badge_count(user_id))
    except Exception:
        logger.exception("badge count failed (user=%s)", user_id)
        return None
