"""
post/minor_privacy.py - privacy DEFAULTS for minor accounts on the public side of the app
(posts / stories / live). One place, no DB writes, easy to unit test.

Who is a minor: same rule as STATUS_PRIVACY_MINORS.md / campus/privacy_rules.py / login/age.py -
a date of birth is on file and the age is under ADULT_AGE (18). A user with NO date of birth:
the campus policy uses `unknown_dob_is_minor` (default True) because campus data is school data.
The public feed has millions of older accounts that never set a DOB, and login/age.py says
"unknown = neither minor nor verified adult", so here the default is the OTHER way round
(`MINOR_PRIVACY_UNKNOWN_DOB_IS_MINOR = False`). Flip that setting to be strict.

What a minor gets switched OFF (each one has its own kill switch in settings, default True = on):

    MINOR_BLOCK_PUBLIC_LIVE   public live: a minor who hosts a live class is visible (and joinable)
                              only to the people of that class - never to followers / the story ring
                              of strangers.            -> post/story_live.py
    MINOR_BLOCK_LOCATION      location: the `location` of a post is dropped when a minor publishes it
                              (create, chunked upload, edit) and is hidden when an older post of a
                              minor is read.            -> post/publish_gate.py, post/serializers.py
    MINOR_BLOCK_DM_STICKER    DM sticker: the story QUESTION box (it is the one sticker that opens a
                              private channel to the owner - every answer lands in their inbox from
                              anybody who can see the story). A minor cannot add it.
                                                        -> post/story_stickers.py
    MINOR_BLOCK_LOCATION      (the same switch as the post location) also covers the story LOCATION sticker (F3):
                              a minor cannot add one, and an old one is not shown once the author is a minor.
                                                        -> post/story_stickers.py, post/serializers.py

    MINOR_PUBLIC_ROOM_MASK_IDENTITY   S8 - public group study room / member list: a minor is shown to ADULT
                              strangers (adult, not group admin/mod) with first name only + no photo.
                              Admin/mod of the group and other minors still see the real name (moderation).
                                                        -> message/study_room_safety.py
    MINOR_PUBLIC_GROUP_JOIN_APPROVAL  S8 - a minor who joins a PUBLIC group from /groups/discover/ (or its invite
                              link) does not get in instantly: a join request is created and a group admin/mod
                              approves it (same GroupJoinRequest flow as private groups).
                                                        -> message/views/groups.py
    MINOR_PUBLIC_ROOM_CAMERA_OFF      S8 - public-group study room: the app starts a minor's camera OFF (hint in the
                              join response `privacy.camera_default_off`; the minor can still turn it on).
                                                        -> message/views/study_room.py + Flutter

Existing rows are not rewritten: switching a rule on only changes what is created / shown from now on.
"""
from django.conf import settings

try:  # login.age is the single age rule of the project
    from login.age import is_minor_dob
except Exception:  # pragma: no cover - keeps this module importable in isolation
    def is_minor_dob(dob, today=None):
        return False

KIND_QUESTION = "question"  # StorySticker.KIND_QUESTION (string here: no model import)
KIND_LOCATION = "location"  # StorySticker.KIND_LOCATION (F3: a place on a story is the same risk as a post location)


def _flag(name, default=True):
    return bool(getattr(settings, name, default))


def unknown_dob_is_minor():
    return _flag("MINOR_PRIVACY_UNKNOWN_DOB_IS_MINOR", False)


def treated_as_minor(user) -> bool:
    """True when `user` must get the minor defaults. Never raises; anonymous / None = False."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    dob = getattr(user, "date_of_birth", None)
    if dob is None:
        return unknown_dob_is_minor()
    try:
        return bool(is_minor_dob(dob))
    except Exception:
        return False


def public_live_blocked(user) -> bool:
    return _flag("MINOR_BLOCK_PUBLIC_LIVE") and treated_as_minor(user)


def location_blocked(user) -> bool:
    return _flag("MINOR_BLOCK_LOCATION") and treated_as_minor(user)


def dm_sticker_blocked(user) -> bool:
    return _flag("MINOR_BLOCK_DM_STICKER") and treated_as_minor(user)


def blocked_sticker_kinds(user) -> set:
    """Story sticker kinds `user` may not add: the question box (DM-like) and the location sticker."""
    blocked = set()
    if dm_sticker_blocked(user):
        blocked.add(KIND_QUESTION)
    if location_blocked(user):
        blocked.add(KIND_LOCATION)
    return blocked


def scrub_location(user, location):
    """The location to store: `{}` for a minor, otherwise `location` as it came (a dict) or `{}`."""
    if location_blocked(user):
        return {}
    return location if isinstance(location, dict) else {}


def hide_location_for_author(author) -> bool:
    """Read side: an OLD post by an account that is a minor now must not show its location either."""
    return location_blocked(author)


def minor_ids_among(user_ids) -> set:
    """Ids (from `user_ids`) of users treated as minors - ONE query. Used by the live ring."""
    ids = {i for i in user_ids if i is not None}
    if not ids or not _flag("MINOR_BLOCK_PUBLIC_LIVE"):
        return set()
    from django.contrib.auth import get_user_model

    out = set()
    for pk, dob in get_user_model().objects.filter(pk__in=ids).values_list("pk", "date_of_birth"):
        if (dob is None and unknown_dob_is_minor()) or (dob is not None and is_minor_dob(dob)):
            out.add(pk)
    return out


def minors_among(user_ids) -> set:
    """Ids (from `user_ids`) of users treated as minors - ONE query. Unlike `minor_ids_among` this is NOT
    behind a MINOR_BLOCK_* switch: it answers "is this author a minor?" for staff screens (moderation queue)."""
    ids = {i for i in user_ids if i is not None}
    if not ids:
        return set()
    from django.contrib.auth import get_user_model

    out = set()
    for pk, dob in get_user_model().objects.filter(pk__in=ids).values_list("pk", "date_of_birth"):
        if (dob is None and unknown_dob_is_minor()) or (dob is not None and is_minor_dob(dob)):
            out.add(pk)
    return out


# ----------------------------------------------------------------------------------------------
# S8 - discovery -> study room safety (public groups)
# ----------------------------------------------------------------------------------------------
def public_room_mask_enabled() -> bool:
    return _flag("MINOR_PUBLIC_ROOM_MASK_IDENTITY")


def public_group_join_needs_approval(user) -> bool:
    """True when `user` (a minor) must go through the admin approval flow to join a PUBLIC group."""
    return _flag("MINOR_PUBLIC_GROUP_JOIN_APPROVAL") and treated_as_minor(user)


def public_room_camera_off(user) -> bool:
    return _flag("MINOR_PUBLIC_ROOM_CAMERA_OFF") and treated_as_minor(user)


def masked_display_name(user) -> str:
    """Minor ka naam adult strangers ko: sirf first name (ya username ka pehla hissa) - last name / full name nahi."""
    first = (getattr(user, "first_name", "") or "").strip()
    if first:
        return first.split()[0]
    username = (getattr(user, "username", "") or "").strip()
    if username:
        return username[:3] + "***" if len(username) > 3 else username
    return "Student"


def viewer_sees_masked_minor(viewer, target_is_minor: bool, viewer_is_group_staff: bool) -> bool:
    """Masking rule: target minor hai, viewer ADULT hai (minor nahi) aur group admin/mod nahi.

    Viewer = anonymous / None => mask (safe side). Minor viewers (apne jaise) aur group staff ko real naam
    milta hai (moderation ke liye staff ko pehchaan chahiye)."""
    if not public_room_mask_enabled() or not target_is_minor:
        return False
    if viewer_is_group_staff:
        return False
    if viewer is None or not getattr(viewer, "is_authenticated", False):
        return True
    return not treated_as_minor(viewer)


def room_privacy_for(user) -> dict:
    """Join response / `GET /message/study-room/<id>/rules/` ke liye client hints (apne baare me)."""
    minor = treated_as_minor(user)
    return {
        "is_minor": minor,
        "mask_identity": bool(minor and public_room_mask_enabled()),
        "camera_default_off": bool(minor and _flag("MINOR_PUBLIC_ROOM_CAMERA_OFF")),
        "join_needs_approval": bool(minor and _flag("MINOR_PUBLIC_GROUP_JOIN_APPROVAL")),
    }


def privacy_defaults_for(user) -> dict:
    """What the app needs to grey out its controls (GET /post/minor-privacy/)."""
    minor = treated_as_minor(user)
    return {
        "is_minor": minor,
        "public_live_allowed": not (minor and _flag("MINOR_BLOCK_PUBLIC_LIVE")),
        "location_allowed": not (minor and _flag("MINOR_BLOCK_LOCATION")),
        "dm_sticker_allowed": not (minor and _flag("MINOR_BLOCK_DM_STICKER")),
        "blocked_sticker_kinds": sorted(blocked_sticker_kinds(user)),
        # S8 - public group / study room
        "public_room_identity_masked": bool(minor and public_room_mask_enabled()),
        "public_group_join_needs_approval": bool(minor and _flag("MINOR_PUBLIC_GROUP_JOIN_APPROVAL")),
        "public_room_camera_default_off": bool(minor and _flag("MINOR_PUBLIC_ROOM_CAMERA_OFF")),
    }
