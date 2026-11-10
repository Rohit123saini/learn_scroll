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

# ======================================================================
# T0.1 — PRESENCE: online flag CACHE/REDIS me, DB me sirf kabhi-kabhi `last_seen`
# ----------------------------------------------------------------------
# Pehle har WS connect/disconnect `UserPresence` row pe DB write karta tha
# (select_for_update + save). Daphne ke kai threads ek saath likhte the to
# SQLite me "database is locked" aata tha (connect crash, 500s, slow POSTs).
#
# Ab:
#   * `presence_conn:<uid>`  — live connection COUNTER (multi-device safe),
#     cache.incr/decr se atomic (Redis me real atomic, LocMem me lock ke andar).
#     online  <=>  counter > 0.   TTL hai (PRESENCE_CONN_TTL) taaki crash ke baad
#     counter hamesha ke liye atka na rahe; consumer `presence_touch` se refresh karta hai.
#   * `presence_last_seen:<uid>` — last_seen ka cache copy (iso string).
#   * DB (`UserPresence.last_seen_at`) — sirf offline hone par, aur ek user ke liye
#     PRESENCE_DB_WRITE_INTERVAL (60s) me max ek baar likhte hain.
#     DB `is_online` ab source of truth NAHI hai (online sirf cache me hota hai).
# ======================================================================
import logging
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

_presence_logger = logging.getLogger(__name__)

PRESENCE_CONN_TTL = 60 * 60            # 1h — consumer 5 min me refresh karta hai
PRESENCE_LAST_SEEN_TTL = 60 * 60 * 24 * 30
PRESENCE_DB_WRITE_INTERVAL = 60        # seconds — per-user DB `last_seen` write throttle


def _conn_key(user_id) -> str:
    return f"presence_conn:{user_id}"


def _last_seen_key(user_id) -> str:
    return f"presence_last_seen:{user_id}"


def _db_gate_key(user_id) -> str:
    return f"presence_dbwrite:{user_id}"


def get_connection_count(user_id) -> int:
    try:
        return int(cache.get(_conn_key(user_id)) or 0)
    except Exception:
        _presence_logger.exception("presence: connection-count read failed user=%s", user_id)
        return 0


def is_user_online(user_id) -> bool:
    return get_connection_count(user_id) > 0


def get_last_seen_cached(user_id):
    """aware datetime ya None (cache miss / kabhi offline nahi hua)."""
    raw = cache.get(_last_seen_key(user_id))
    if not raw:
        return None
    try:
        return parse_datetime(raw)
    except (TypeError, ValueError):
        return None


def set_last_seen_cache(user_id, when: datetime):
    cache.set(_last_seen_key(user_id), when.isoformat(), PRESENCE_LAST_SEEN_TTL)


def presence_connect(user_id):
    """Connection +1 (cache only, ZERO DB). Returns (is_online=True, last_seen_at|None)."""
    key = _conn_key(user_id)
    cache.add(key, 0, PRESENCE_CONN_TTL)
    try:
        cache.incr(key)
    except ValueError:  # add aur incr ke beech key expire ho gayi
        cache.set(key, 1, PRESENCE_CONN_TTL)
    cache.touch(key, PRESENCE_CONN_TTL)
    return True, get_last_seen_cached(user_id)


def presence_touch(user_id):
    """Lambe-chalne connection ka counter-TTL refresh (key hi nahi hai to kuch nahi)."""
    cache.touch(_conn_key(user_id), PRESENCE_CONN_TTL)


def presence_disconnect(user_id):
    """Connection -1. Returns (still_online, last_seen_at|None).

    Counter 0 par pahunchne par `last_seen` = abhi, cache me turant; DB me sirf throttled.
    """
    key = _conn_key(user_id)
    try:
        remaining = cache.decr(key)
    except ValueError:  # key expire/flush ho chuki — matlab koi tracked connection nahi
        remaining = 0
    if remaining < 0:
        cache.set(key, 0, PRESENCE_CONN_TTL)
        remaining = 0
    if remaining > 0:
        return True, get_last_seen_cached(user_id)

    now = timezone.now()
    set_last_seen_cache(user_id, now)
    return False, now


def persist_last_seen_throttled(user_id, when: datetime) -> bool:
    """`UserPresence.last_seen_at` DB me likho — par ek user ke liye PRESENCE_DB_WRITE_INTERVAL
    me max ek baar. True = likha gaya. Kabhi raise nahi karta (presence best-effort hai).
    """
    try:
        if not cache.add(_db_gate_key(user_id), 1, PRESENCE_DB_WRITE_INTERVAL):
            return False  # abhi-abhi likha tha; cache me fresh value hai, DB agle write par pakad lega
    except Exception:
        _presence_logger.exception("presence: db-write gate failed user=%s", user_id)
        return False

    from common.db_retry import retry_on_db_lock
    from .models import UserPresence

    def _write():
        updated = UserPresence.objects.filter(user_id=user_id).update(
            last_seen_at=when, is_online=False, active_connections=0, updated_at=timezone.now(),
        )
        if not updated:
            UserPresence.objects.get_or_create(
                user_id=user_id,
                defaults={"last_seen_at": when, "is_online": False, "active_connections": 0},
            )

    try:
        retry_on_db_lock(_write)
        return True
    except Exception:
        _presence_logger.exception("presence: last_seen DB write failed user=%s", user_id)
        cache.delete(_db_gate_key(user_id))  # agli baar fir koshish karne do
        return False


def get_presence_snapshot(user_id):
    """{"is_online": bool, "last_seen_at": datetime|None} ya None (cache me kuch nahi pata)."""
    last_seen = get_last_seen_cached(user_id)
    if is_user_online(user_id):
        return {"is_online": True, "last_seen_at": last_seen}
    if last_seen is not None:
        return {"is_online": False, "last_seen_at": last_seen}
    return None

