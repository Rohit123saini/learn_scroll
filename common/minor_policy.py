"""
common/minor_policy.py  (P-F16 -- minor privacy in every feature)

ONE place that answers "is this person a minor?" and "what may a minor do / be shown to?" for the whole project.
Before this file there were six private copies of the age rule (post/minor_privacy.py, user_profile/privacy.py,
tuitionclass/live_safety.py, user_profile/wallet_settings.py, campus/privacy_rules.py and the `User.is_minor`
property) and they disagreed on an account with NO date of birth. New code calls THIS module; the old helpers
stay (nothing deleted) and still work, so existing imports and tests are untouched.

Who is a minor
    A date of birth is on file and the age is under ADULT_AGE (login/age.py, default 18).
    No date of birth  ->  NOT a minor by default (same as login/age.py: "unknown = neither minor nor adult"; the
    public app has many old accounts without a DOB). Set `MINOR_POLICY_UNKNOWN_DOB_IS_MINOR=1` to be strict.
    (Campus keeps its own stricter per-campus switch `unknown_dob_is_minor`; school data is a different case.)

Public API
    is_minor(user_or_id)                    -> bool      never raises; None / anonymous -> False
    minor_ids_among(user_ids)               -> set       ONE query
    feature_enabled(feature)                -> bool      the rule for `feature` is switched on (env, default on)
    blocked(user, feature)                  -> bool      `user` is a minor AND the rule for `feature` is on
    exclude_minors_for_stranger(qs, viewer) -> qs        people-search / suggestion querysets (see below)
    can_see_minor_identity(viewer, minor_id, *, linked_ids=None) -> bool
    masked_person(user)                     -> dict      {"username", "full_name", "profile_picture"} for a hidden minor
    policy_for(user)                        -> dict      what the app / a parent screen can show about the rules
    FEATURES                                -> tuple     every feature name this module knows

Features (each has an env switch `MINOR_POLICY_<NAME>`, default ON; "0"/"false"/"off" turns one rule off)
    DM              new 1-1 chat / call from an adult stranger              (message/minor_safety.py, existing)
    LIVE_GUEST      a minor is never made a live co-host; stage invite is re-checked when the minor accepts
    GROUP_DISCOVER  public groups created by a minor are not listed to adult strangers
    REFERRAL        a minor cannot earn referral commission; referred minors are shown masked
    TAGGING         tag / mention / group-add never open to everyone  (user_profile/privacy.py, existing)
    LOCATION        post / story / profile location                  (post/minor_privacy.py, existing)
    SEARCH          adult / unknown-age strangers do not find a minor in people search
    SUGGESTIONS     "people you may know" / onboarding suggestions never offer a minor to a non-minor
    LEADERBOARD     a minor's name / photo are masked on public boards (campus-section boards are exempt: classmates)

"Stranger" = a viewer who is not the minor, is not a minor themself, and has no ACCEPTED follow link with the
minor in either direction (a minor's profile is private, so an accepted follow is the minor's own opt-in).
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

DM = "dm"
LIVE_GUEST = "live_guest"
GROUP_DISCOVER = "group_discover"
REFERRAL = "referral"
TAGGING = "tagging"
LOCATION = "location"
SEARCH = "search"
SUGGESTIONS = "suggestions"
LEADERBOARD = "leaderboard"

FEATURES = (DM, LIVE_GUEST, GROUP_DISCOVER, REFERRAL, TAGGING, LOCATION, SEARCH, SUGGESTIONS, LEADERBOARD)

# Machine codes + English fallbacks the app can translate.
MINOR_BLOCKED_CODE = "minor_privacy_blocked"
MINOR_COHOST_TEXT = "Students under 18 cannot be made a co-host."
MINOR_STAGE_TEXT = "Students under 18 cannot join the stage in this class."
MINOR_REFERRER_REASON = "minor_referrer"  # stored in ReferralCommission.block_reason / returned by attribute()

_OFF = ("0", "false", "off", "no")


# ----------------------------------------------------------------------------------------------
# switches
# ----------------------------------------------------------------------------------------------
def _env_flag(name: str, default: bool) -> bool:
    value = getattr(settings, name, None)
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in _OFF
    return bool(value)


def unknown_dob_is_minor() -> bool:
    return _env_flag("MINOR_POLICY_UNKNOWN_DOB_IS_MINOR", False)


def feature_enabled(feature: str) -> bool:
    """True when the minor rule of `feature` is switched on (default True)."""
    return _env_flag(f"MINOR_POLICY_{str(feature).upper()}", True)


# ----------------------------------------------------------------------------------------------
# who is a minor
# ----------------------------------------------------------------------------------------------
def _dob_is_minor(dob) -> bool:
    if dob is None:
        return unknown_dob_is_minor()
    try:
        from login.age import is_minor_dob

        return bool(is_minor_dob(dob))
    except Exception:  # an age-rule problem must never take a page down
        logger.warning("minor_policy: age check failed", exc_info=True)
        return False


def _uid(user_or_id):
    uid = getattr(user_or_id, "pk", user_or_id)
    try:
        return int(uid) if uid is not None else None
    except (TypeError, ValueError):
        return None


def is_minor(user_or_id) -> bool:
    """The ONE age rule. Accepts a user object or a user id. Never raises; None / anonymous -> False."""
    if user_or_id is None or getattr(user_or_id, "is_authenticated", True) is False:
        return False
    if hasattr(user_or_id, "pk"):
        return _dob_is_minor(getattr(user_or_id, "date_of_birth", None))
    uid = _uid(user_or_id)
    if uid is None:
        return False
    try:
        from django.contrib.auth import get_user_model

        row = get_user_model().objects.filter(pk=uid).values_list("date_of_birth", flat=True)[:1]
        rows = list(row)
    except Exception:
        logger.warning("minor_policy: could not load user %s", uid, exc_info=True)
        return False
    return _dob_is_minor(rows[0]) if rows else False


def minor_ids_among(user_ids) -> set:
    """Ids (from `user_ids`) that are minors. ONE query. Not behind any feature switch."""
    ids = {i for i in (_uid(x) for x in (user_ids or ())) if i is not None}
    if not ids:
        return set()
    try:
        from django.contrib.auth import get_user_model

        rows = get_user_model().objects.filter(pk__in=ids).values_list("pk", "date_of_birth")
        return {pk for pk, dob in rows if _dob_is_minor(dob)}
    except Exception:
        logger.warning("minor_policy: minor_ids_among failed", exc_info=True)
        return set()


def blocked(user, feature: str) -> bool:
    """`user` is a minor AND the rule of `feature` is on."""
    return feature_enabled(feature) and is_minor(user)


# ----------------------------------------------------------------------------------------------
# querysets (people search / suggestions)
# ----------------------------------------------------------------------------------------------
def exclude_minors_for_stranger(qs, viewer, *, feature: str = SEARCH, id_field: str = "id"):
    """Drop minors from a User queryset shown to `viewer`, unless `viewer` is a minor themself or has an ACCEPTED
    follow link with that minor (either direction). A minor viewer sees everyone (peers + teachers) as before.
    No-op when the rule of `feature` is off. Stays a QuerySet (subqueries only)."""
    if not feature_enabled(feature) or is_minor(viewer):
        return qs
    try:
        from django.db.models import Q

        from login.age import minor_cutoff_date
        from user_profile.models import Follow

        cutoff = minor_cutoff_date()
        minor_q = Q(**{f"{id_field}__in": _minor_user_ids_subquery(cutoff)})
        v = _uid(viewer)
        if v is None:
            return qs.exclude(minor_q)
        accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED)
        i_follow = accepted.filter(follower_id=v).values("following_id")
        follow_me = accepted.filter(following_id=v).values("follower_id")
        return qs.exclude(
            minor_q
            & ~Q(**{f"{id_field}__in": i_follow})
            & ~Q(**{f"{id_field}__in": follow_me})
            & ~Q(**{id_field: v})
        )
    except Exception:  # never take search down; fail open here is the OLD behaviour, but log it
        logger.exception("minor_policy: exclude_minors_for_stranger failed (feature=%s)", feature)
        return qs


def _minor_user_ids_subquery(cutoff):
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    cond = Q(date_of_birth__gt=cutoff)
    if unknown_dob_is_minor():
        cond = cond | Q(date_of_birth__isnull=True)
    return get_user_model().objects.filter(cond).values("id")


# ----------------------------------------------------------------------------------------------
# identity masking (boards, referral lists)
# ----------------------------------------------------------------------------------------------
def masked_name(user) -> str:
    """First name only (or a short username stub) - same idea as post.minor_privacy.masked_display_name."""
    first = (getattr(user, "first_name", "") or "").strip()
    if first:
        return first.split()[0]
    username = (getattr(user, "username", "") or "").strip()
    if username:
        return username[:3] + "***" if len(username) > 3 else username
    return "Student"


def masked_person(user) -> dict:
    name = masked_name(user)
    return {"username": name, "full_name": name, "profile_picture": None}


def linked_user_ids(viewer, user_ids) -> set:
    """Of `user_ids`, the ones with an ACCEPTED follow link to `viewer` (either direction). One query."""
    v = _uid(viewer)
    ids = {i for i in (_uid(x) for x in (user_ids or ())) if i is not None}
    if v is None or not ids:
        return set()
    from django.db.models import Q

    from user_profile.models import Follow

    rows = Follow.objects.filter(status=Follow.Status.ACCEPTED).filter(
        Q(follower_id=v, following_id__in=ids) | Q(following_id=v, follower_id__in=ids)
    ).values_list("follower_id", "following_id")
    out = set()
    for a, b in rows:
        out.add(b if a == v else a)
    return out


def masked_ids_for_viewer(viewer, user_ids, *, feature: str) -> set:
    """Of `user_ids`, the minors whose identity `viewer` must NOT see (stranger rule). Empty when the rule is off,
    when `viewer` is a minor, or for the viewer's own id. Two queries at most."""
    if not feature_enabled(feature) or is_minor(viewer):
        return set()
    minors = minor_ids_among(user_ids)
    v = _uid(viewer)
    minors.discard(v)
    if not minors:
        return set()
    return minors - linked_user_ids(viewer, minors)


def can_see_minor_identity(viewer, minor_id, *, feature: str = LEADERBOARD) -> bool:
    return _uid(minor_id) not in masked_ids_for_viewer(viewer, [minor_id], feature=feature)


# ----------------------------------------------------------------------------------------------
# what the app / a parent screen can show
# ----------------------------------------------------------------------------------------------
def policy_for(user) -> dict:
    """Read-only summary of the rules that apply to `user`. Safe to show to the account owner or a linked parent."""
    minor = is_minor(user)
    return {
        "is_minor": minor,
        "rules": {
            "dm_from_adult_strangers": "blocked" if (minor and feature_enabled(DM)) else "normal",
            "live_cohost": "blocked" if (minor and feature_enabled(LIVE_GUEST)) else "normal",
            "found_in_search_by_strangers": not (minor and feature_enabled(SEARCH)),
            "suggested_to_adults": not (minor and feature_enabled(SUGGESTIONS)),
            "group_discover_listing": not (minor and feature_enabled(GROUP_DISCOVER)),
            "referral_earnings": not (minor and feature_enabled(REFERRAL)),
            "leaderboard_identity_masked": bool(minor and feature_enabled(LEADERBOARD)),
            "location_shared": not (minor and feature_enabled(LOCATION)),
            "tag_mention_group_add_open_to_everyone": not (minor and feature_enabled(TAGGING)),
        },
    }
