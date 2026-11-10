"""
user_profile/privacy.py  (S4 -- Privacy controls)

The ONE place that answers "may `viewer` do X to `owner`?" for the controls in `PrivacySettings`. Every enforcing
app (message, post, user_profile views) calls these helpers and never reads the model itself, so a rule is changed
here and nowhere else.

    allows(owner, viewer, "message_from")        -> bool      (one pair)
    allowed_owner_ids(viewer, owner_ids, field)  -> set       (many owners, 2 queries at most)
    exclude_hidden_from_search(qs, viewer)       -> qs        (users who chose "hide me from search")
    level_of(owner, field)                       -> "everyone" | "connections" | "nobody"

Rules
  * A person is never limited from themselves: `owner == viewer` is always allowed.
  * `everyone` = allowed, `nobody` = refused, `connections` = an ACCEPTED follow in either direction
    (a pending follow request is not a connection).
  * No row = every default (everyone / not hidden / discoverable). Rows are created only when somebody saves.
  * Blocks, restrict and the minor-safety DM rule are separate and stay exactly as they were; this module only
    adds a further "no".
  * Minors (a date of birth on file, under ADULT_AGE; unknown DOB = not a minor, same as login/age.py) cannot open
    message / call / tag / mention / group-add / online-status to `everyone`: the effective level is at least
    `connections`, and find-by-phone / find-by-email are off. The stored choice is NOT rewritten (it applies again
    at 18) and the switch `PRIVACY_MINOR_LOCKS` (default True) turns the whole rule off. `locks_for()` tells the
    Settings screen which controls to show as locked.
  * S4b -- account-level DEFAULTS for new content (`default_story_audience`, `default_post_visibility`,
    `default_comments_limit`, `default_allow_reshare`, `default_allow_save`). `content_defaults_for(user)` answers
    "what should the next post / story start with?" and `fill_content_defaults(data, user, kind)` fills ONLY the keys
    the composer did not send (an explicit value always wins). A minor's defaults have a safe floor (story: not
    `everyone`, post: not `public`, comments: not `everyone`); the floor is applied to the DEFAULT, never to a value
    the composer sends on purpose (that path already has its own minor rules in post/minor_privacy.py).
  * Settings are cached per user (`PRIVACY_CACHE_SECONDS`, default 30s) and the cache is dropped on every save, so
    a change is felt at once on the server that took it and within the TTL on the others.
"""
import logging

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q

logger = logging.getLogger(__name__)

EVERYONE = "everyone"
CONNECTIONS = "connections"
NOBODY = "nobody"
AUDIENCES = (EVERYONE, CONNECTIONS, NOBODY)

# Who-can controls (audience levels). Order = order on the Settings screen.
AUDIENCE_FIELDS = (
    "message_from",
    "call_from",
    "tag_from",
    "mention_from",
    "group_add_from",
    "online_status_from",
    "followers_list_from",
)
BOOLEAN_FIELDS = ("hide_from_search", "find_by_phone", "find_by_email")

# S4b -- defaults for new posts / stories. Each choice field lists its levels from OPEN to SAFE.
STORY_AUDIENCES = ("everyone", "followers", "close_friends")
POST_VISIBILITIES = ("public", "connections", "close_friends")
COMMENT_LIMITS = ("everyone", "followers")
CONTENT_CHOICE_FIELDS = {
    "default_story_audience": STORY_AUDIENCES,
    "default_post_visibility": POST_VISIBILITIES,
    "default_comments_limit": COMMENT_LIMITS,
}
CONTENT_BOOLEAN_FIELDS = ("default_allow_reshare", "default_allow_save")
CONTENT_DEFAULT_FIELDS = tuple(CONTENT_CHOICE_FIELDS) + CONTENT_BOOLEAN_FIELDS
# open value -> value a minor's DEFAULT is raised to
MINOR_CONTENT_FLOOR = {
    "default_story_audience": ("everyone", "followers"),
    "default_post_visibility": ("public", "connections"),
    "default_comments_limit": ("everyone", "followers"),
}
ALL_FIELDS = AUDIENCE_FIELDS + BOOLEAN_FIELDS + CONTENT_DEFAULT_FIELDS

# Minor locks: these may never be `everyone` for a minor (floor = connections) ...
MINOR_FLOOR_FIELDS = (
    "message_from",
    "call_from",
    "tag_from",
    "mention_from",
    "group_add_from",
    "online_status_from",
)
# ... and these are forced off.
MINOR_OFF_FIELDS = ("find_by_phone", "find_by_email")
MINOR_LOCKED_FIELDS = MINOR_FLOOR_FIELDS + MINOR_OFF_FIELDS + tuple(MINOR_CONTENT_FLOOR)
MINOR_LOCKED_CODE = "privacy_minor_locked"
MINOR_LOCKED_TEXT = "For your safety this setting is limited while you are under 18."

DEFAULTS = {
    **{name: EVERYONE for name in AUDIENCE_FIELDS},
    "hide_from_search": False,
    "find_by_phone": True,
    "find_by_email": True,
    "default_story_audience": "everyone",
    "default_post_visibility": "public",
    "default_comments_limit": "everyone",
    "default_allow_reshare": True,
    "default_allow_save": True,
}

_CACHE_PREFIX = "privacy:v1:"


def _ttl() -> int:
    try:
        return max(0, int(getattr(settings, "PRIVACY_CACHE_SECONDS", 30)))
    except (TypeError, ValueError):
        return 30


def _uid(user_or_id):
    uid = getattr(user_or_id, "pk", user_or_id)
    try:
        return int(uid) if uid is not None else None
    except (TypeError, ValueError):
        return None


as_user_id = _uid  # public name for callers that need to compare ids the same way


# --------------------------------------------------------------------------------------------------
# minors
# --------------------------------------------------------------------------------------------------
def minor_locks_enabled() -> bool:
    return bool(getattr(settings, "PRIVACY_MINOR_LOCKS", True))


def _dob_is_minor(dob) -> bool:
    if dob is None:
        return False
    try:
        from login.age import is_minor_dob

        return bool(is_minor_dob(dob))
    except Exception:  # an age-rule problem must never take privacy checks down
        logger.warning("privacy: age check failed", exc_info=True)
        return False


def is_minor_user(user_or_id) -> bool:
    """True when the minor locks are on and the user has a DOB on file that makes them under ADULT_AGE."""
    if not minor_locks_enabled():
        return False
    uid = _uid(user_or_id)
    if uid is None:
        return False
    dob = getattr(user_or_id, "date_of_birth", None) if hasattr(user_or_id, "pk") else None
    if dob is None:
        from django.contrib.auth import get_user_model

        dob = get_user_model().objects.filter(pk=uid).values_list("date_of_birth", flat=True).first()
    return _dob_is_minor(dob)


def minor_ids_among(user_ids) -> set:
    """Of `user_ids`, the ones that are minors (ONE query). Empty when the locks are off."""
    ids = {i for i in (_uid(x) for x in user_ids) if i is not None}
    if not ids or not minor_locks_enabled():
        return set()
    from django.contrib.auth import get_user_model

    rows = get_user_model().objects.filter(pk__in=ids, date_of_birth__isnull=False).values_list("pk", "date_of_birth")
    return {pk for pk, dob in rows if _dob_is_minor(dob)}


def apply_minor_floor(data: dict) -> dict:
    """A copy of a settings dict with the minor locks applied (everyone -> connections, find-by-* off)."""
    out = dict(data)
    for name in MINOR_FLOOR_FIELDS:
        if out.get(name) == EVERYONE:
            out[name] = CONNECTIONS
    for name in MINOR_OFF_FIELDS:
        if name in out:
            out[name] = False
    for name, (open_value, safe_value) in MINOR_CONTENT_FLOOR.items():
        if out.get(name) == open_value:
            out[name] = safe_value
    return out


def locks_for(user_or_id) -> dict:
    """What the Settings screen needs: `{"is_minor": bool, "locked_fields": [...]}` (empty list for an adult)."""
    minor = is_minor_user(user_or_id)
    return {"is_minor": minor, "locked_fields": list(MINOR_LOCKED_FIELDS) if minor else []}


def minor_violations(user_or_id, changes: dict) -> dict:
    """For a PATCH: `{field: message}` for every change that a minor may not make (open to everyone / turn find-by on).
    Empty for an adult. Setting a locked control to a SAFER value is always fine."""
    if not changes or not is_minor_user(user_or_id):
        return {}
    bad = {}
    for name in MINOR_FLOOR_FIELDS:
        if str(changes.get(name)).strip().lower() == EVERYONE:
            bad[name] = MINOR_LOCKED_TEXT
    for name in MINOR_OFF_FIELDS:
        value = changes.get(name)
        if value is True or str(value).strip().lower() in ("true", "1", "yes", "on"):  # JSON bool or form text
            bad[name] = MINOR_LOCKED_TEXT
    for name, (open_value, _safe) in MINOR_CONTENT_FLOOR.items():
        if str(changes.get(name)).strip().lower() == open_value:
            bad[name] = MINOR_LOCKED_TEXT
    return bad


# --------------------------------------------------------------------------------------------------
# settings of one user (cached)
# --------------------------------------------------------------------------------------------------
def invalidate(user_id) -> None:
    uid = _uid(user_id)
    if uid is None:
        return
    try:
        cache.delete(f"{_CACHE_PREFIX}{uid}")
    except Exception:  # a cache outage must never break a save
        logger.warning("privacy cache invalidate failed (user=%s)", uid, exc_info=True)


def get_settings(user_or_id) -> dict:
    """Every control of one user as a plain dict (defaults filled in)."""
    from .models import PrivacySettings

    uid = _uid(user_or_id)
    if uid is None:
        return dict(DEFAULTS)
    key = f"{_CACHE_PREFIX}{uid}"
    try:
        cached = cache.get(key)
    except Exception:
        cached = None
    if isinstance(cached, dict):
        return cached

    row = PrivacySettings.objects.filter(user_id=uid).values(*ALL_FIELDS).first()
    data = dict(DEFAULTS)
    if row:
        data.update(row)
    # a value that is not a known level (hand-edited row) falls back to the default
    for name in AUDIENCE_FIELDS:
        if data.get(name) not in AUDIENCES:
            data[name] = DEFAULTS[name]
    for name, levels in CONTENT_CHOICE_FIELDS.items():
        if data.get(name) not in levels:
            data[name] = DEFAULTS[name]
    for name in CONTENT_BOOLEAN_FIELDS:
        data[name] = bool(data.get(name, DEFAULTS[name]))
    # minors: the effective value (the stored choice is left alone)
    if is_minor_user(uid):
        data = apply_minor_floor(data)
    try:
        ttl = _ttl()
        if ttl:
            cache.set(key, data, ttl)
    except Exception:
        logger.warning("privacy cache set failed (user=%s)", uid, exc_info=True)
    return data


def level_of(owner, field: str) -> str:
    if field not in AUDIENCE_FIELDS:
        raise ValueError(f"unknown privacy control: {field}")
    return get_settings(owner)[field]


def content_defaults_for(user_or_id) -> dict:
    """S4b -- what the user's NEXT post / story starts with, minor floor already applied:
    `{"story_audience", "post_visibility", "comments_limit", "allow_reshare", "allow_save"}`."""
    data = get_settings(user_or_id)
    return {
        "story_audience": data["default_story_audience"],
        "post_visibility": data["default_post_visibility"],
        "comments_limit": data["default_comments_limit"],
        "allow_reshare": bool(data["default_allow_reshare"]),
        "allow_save": bool(data["default_allow_save"]),
    }


# --------------------------------------------------------------------------------------------------
# connections
# --------------------------------------------------------------------------------------------------
def is_connected(user_a, user_b) -> bool:
    """True if an ACCEPTED follow exists between the two, in either direction."""
    from .models import Follow

    a, b = _uid(user_a), _uid(user_b)
    if a is None or b is None:
        return False
    return Follow.objects.filter(
        Q(follower_id=a, following_id=b) | Q(follower_id=b, following_id=a),
        status=Follow.Status.ACCEPTED,
    ).exists()


def connected_ids(viewer, among_ids) -> set:
    """Of `among_ids`, the ones with an accepted follow to / from `viewer` (one query)."""
    from .models import Follow

    v = _uid(viewer)
    ids = [i for i in (_uid(x) for x in among_ids) if i is not None]
    if v is None or not ids:
        return set()
    rows = Follow.objects.filter(status=Follow.Status.ACCEPTED).filter(
        Q(follower_id=v, following_id__in=ids) | Q(following_id=v, follower_id__in=ids)
    ).values_list("follower_id", "following_id")
    out = set()
    for follower_id, following_id in rows:
        out.add(following_id if follower_id == v else follower_id)
    return out


# --------------------------------------------------------------------------------------------------
# may `viewer` ... `owner`?
# --------------------------------------------------------------------------------------------------
def allows(owner, viewer, field: str) -> bool:
    """May `viewer` use control `field` on `owner`? (`message_from`, `call_from`, ...)."""
    o, v = _uid(owner), _uid(viewer)
    if o is None or v is None:
        return False
    if o == v:
        return True
    level = level_of(o, field)
    if level == EVERYONE:
        return True
    if level == NOBODY:
        return False
    return is_connected(o, v)


def allowed_owner_ids(viewer, owner_ids, field: str) -> set:
    """Of `owner_ids`, the owners whose `field` lets `viewer` through. At most four queries, however many owners."""
    from .models import PrivacySettings

    if field not in AUDIENCE_FIELDS:
        raise ValueError(f"unknown privacy control: {field}")
    v = _uid(viewer)
    ids = {i for i in (_uid(x) for x in owner_ids) if i is not None}
    if v is None or not ids:
        return set()

    levels = {i: DEFAULTS[field] for i in ids}
    for uid, level in PrivacySettings.objects.filter(user_id__in=ids).values_list("user_id", field):
        levels[uid] = level if level in AUDIENCES else DEFAULTS[field]
    # minors: `everyone` counts as `connections` for the controls that have a floor
    if field in MINOR_FLOOR_FIELDS:
        open_ids = {i for i, lvl in levels.items() if i != v and lvl == EVERYONE}
        for i in minor_ids_among(open_ids):
            levels[i] = CONNECTIONS

    allowed = {i for i, lvl in levels.items() if i == v or lvl == EVERYONE}
    need_link = {i for i, lvl in levels.items() if i != v and lvl == CONNECTIONS}
    if need_link:
        allowed |= connected_ids(v, need_link)
    return allowed


# --------------------------------------------------------------------------------------------------
# named shortcuts (what the call sites read like)
# --------------------------------------------------------------------------------------------------
def can_start_chat(owner, viewer) -> bool:
    return allows(owner, viewer, "message_from")


def can_call(owner, viewer) -> bool:
    return allows(owner, viewer, "call_from")


def can_tag(owner, viewer) -> bool:
    return allows(owner, viewer, "tag_from")


def can_mention(owner, viewer) -> bool:
    return allows(owner, viewer, "mention_from")


def can_add_to_group(owner, viewer) -> bool:
    return allows(owner, viewer, "group_add_from")


def can_see_online_status(owner, viewer) -> bool:
    return allows(owner, viewer, "online_status_from")


def can_see_follow_lists(owner, viewer) -> bool:
    return allows(owner, viewer, "followers_list_from")


# Texts + machine codes of the refusals (the app maps `code` to its own Hindi / English line, see ERROR_CODES.md).
MESSAGE_BLOCKED_CODE = "privacy_message_blocked"
MESSAGE_BLOCKED_TEXT = "This person doesn't accept new chats from you."
CALL_BLOCKED_CODE = "privacy_call_blocked"
CALL_BLOCKED_TEXT = "This person doesn't accept calls from you."


def new_chat_blocked(sender, receiver) -> bool:
    """True when `receiver` limited who may START a chat with them, `sender` is outside that circle and the two have
    no chat yet. A chat that already exists is never cut off (same rule as the minor-safety DM check)."""
    if allows(receiver, sender, "message_from"):
        return False
    from message.models import Conversation

    s, r = _uid(sender), _uid(receiver)
    if s is None or r is None:
        return True
    return not Conversation.objects.filter(private_key=Conversation.make_private_key(s, r)).exists()


def is_hidden_from_search(user) -> bool:
    return bool(get_settings(user)["hide_from_search"])


def discoverable_by_phone(user) -> bool:
    """Contact discovery (user_profile/contact_discovery.py): False => never match this person by phone number."""
    return bool(get_settings(user)["find_by_phone"])


def discoverable_by_email(user) -> bool:
    return bool(get_settings(user)["find_by_email"])


def discoverable_owner_ids(owner_ids, kind: str) -> set:
    """Of `owner_ids`, the people who may be matched by `kind` ("phone" | "email"): not switched off and not a minor
    (minors are never found by contact details). Two queries at most, however many people."""
    from .models import PrivacySettings

    field = {"phone": "find_by_phone", "email": "find_by_email"}.get(kind)
    if field is None:
        raise ValueError(f"unknown contact discovery kind: {kind}")
    ids = {i for i in (_uid(x) for x in owner_ids) if i is not None}
    if not ids:
        return set()
    switched_off = set(PrivacySettings.objects.filter(user_id__in=ids, **{field: False}).values_list("user_id", flat=True))
    ok = ids - switched_off
    return ok - minor_ids_among(ok)


# --------------------------------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------------------------------
def exclude_hidden_from_search(qs, viewer, id_field: str = "id"):
    """Drop people who chose "hide me from search" from a User queryset -- unless `viewer` has an accepted follow
    link with them (so friends still find each other). `viewer` is never dropped from their own results here
    (callers already exclude themselves)."""
    from .models import Follow, PrivacySettings

    v = _uid(viewer)
    hidden = PrivacySettings.objects.filter(hide_from_search=True).values("user_id")
    if v is None:
        return qs.exclude(**{f"{id_field}__in": hidden})
    accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED)
    i_follow = accepted.filter(follower_id=v).values("following_id")
    follow_me = accepted.filter(following_id=v).values("follower_id")
    return qs.exclude(
        Q(**{f"{id_field}__in": hidden})
        & ~Q(**{f"{id_field}__in": i_follow})
        & ~Q(**{f"{id_field}__in": follow_me})
        & ~Q(**{id_field: v})
    )
