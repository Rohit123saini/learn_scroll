# message/cache_utils.py
#
# 🔥 NAYI FILE — Poori app me `ai_service.py` ke alawa kahin caching nahi thi.
# `is_group_admin_or_mod()` (group_rules.py) sabse zyada-hit query hai: har
# pin, message, call, study-room, member-management action pe chalta hai —
# aur ye ek chhota, slow-changing lookup hai (koi role rarely change hota
# hai) — textbook cache candidate.
#
# Django's default cache backend use karte hain (jo `ai_service.py` already
# use kar raha hai — production me ye Redis hona chahiye, settings.py check
# karo). Cache short-TTL + explicit invalidate-on-write hai, taaki role
# change hote hi turant reflect ho (stale-role se koi security gap na bane).
#
# ---------------------------------------------------------------
# SETUP:
#   group_rules.py already patched to use `get_group_role_cached` /
#   `invalidate_group_role_cache` (see that file's diff).
#
#   Jahan bhi `GroupMember.role` / `is_banned` update hota hai (views.py:
#   `GroupViewSet.update_member`, `approve_join_request`, `add_members`,
#   member-remove, etc.) — wahan is function ko bhi call karo:
#
#       from .cache_utils import invalidate_group_role_cache
#       invalidate_group_role_cache(group_id, user_id)
#
#   Presence ke liye — `UserPresenceView` aur `ChatConsumer`'s presence
#   update dono jagah:
#
#       from .cache_utils import get_presence_cached, invalidate_presence_cache
# ---------------------------------------------------------------

from django.core.cache import cache

GROUP_ROLE_TTL = 60          # seconds — role rarely changes; short TTL is enough
PRESENCE_TTL = 15            # seconds — presence changes often, keep it short


def _group_role_key(group_id, user_id) -> str:
    return f"grp_role:{group_id}:{user_id}"


def get_group_role_cached(group_id, user_id):
    """
    Returns a dict {"role": str, "is_banned": bool} or None if the user
    isn't a member at all. Caches the DB miss too (as a sentinel), so a
    non-member repeatedly probing an admin-only action doesn't hit the DB
    every single time either.
    """
    key = _group_role_key(group_id, user_id)
    cached = cache.get(key)
    if cached is not None:
        return None if cached == "__none__" else cached

    from .models import GroupMember  # local import — avoid circular import

    member = (
        GroupMember.objects.filter(group_id=group_id, user_id=user_id)
        .values("role", "is_banned")
        .first()
    )
    cache.set(key, member if member else "__none__", GROUP_ROLE_TTL)
    return member


def invalidate_group_role_cache(group_id, user_id):
    cache.delete(_group_role_key(group_id, user_id))


def _presence_key(user_id) -> str:
    return f"presence:{user_id}"


def get_presence_cached(user_id):
    """Returns {"is_online": bool, "last_seen_at": iso str|None} or None on cache miss."""
    return cache.get(_presence_key(user_id))


def set_presence_cache(user_id, is_online: bool, last_seen_at):
    cache.set(
        _presence_key(user_id),
        {
            "is_online": is_online,
            "last_seen_at": last_seen_at.isoformat() if last_seen_at else None,
        },
        PRESENCE_TTL,
    )


def invalidate_presence_cache(user_id):
    cache.delete(_presence_key(user_id))