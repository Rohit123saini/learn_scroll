"""
post/story_limits.py

F2 - story limits, in ONE place (views / serializer call these, nothing re-implements them).

  * ACTIVE CAP      a person can have at most STORY_MAX_ACTIVE_PER_USER (default 30) live stories at the same time.
                    The 11th story in 10 minutes / 61st in a day is already stopped by post/publish_gate.py (spam
                    rate limit); this cap is the "shelf size": a user who keeps a huge pile of 24h stories is
                    refused with 429 `story_limit_active` + `retry_after` (seconds until the OLDEST live story
                    ends and frees a slot). Staff are exempt (same as the publish gate).
  * LIFETIME CLAMP  `expires_at` is writable on create (a shorter-lived story is allowed), so it is clamped here:
                    never longer than STORY_MAX_LIFETIME_HOURS (24) - a client could otherwise ask for a story that
                    never ends - and never shorter than STORY_MIN_LIFETIME_MINUTES (30).
  * STORY-ONLY LIMITS payload for clients (`limits_for`).

Written by reading the code only - nothing was compiled or run.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

CODE_ACTIVE_LIMIT = "story_limit_active"

DEFAULT_MAX_ACTIVE = 30
DEFAULT_MAX_LIFETIME_HOURS = 24
DEFAULT_MIN_LIFETIME_MINUTES = 30


def max_active():
    return max(1, int(getattr(settings, "STORY_MAX_ACTIVE_PER_USER", DEFAULT_MAX_ACTIVE)))


def max_lifetime():
    return timedelta(hours=max(1, int(getattr(settings, "STORY_MAX_LIFETIME_HOURS", DEFAULT_MAX_LIFETIME_HOURS))))


def min_lifetime():
    minutes = max(1, int(getattr(settings, "STORY_MIN_LIFETIME_MINUTES", DEFAULT_MIN_LIFETIME_MINUTES)))
    return timedelta(minutes=minutes)


def _active_qs(user, now=None):
    from .models import Story

    return Story.objects.filter(user=user, is_deleted=False, expires_at__gt=now or timezone.now())


def active_count(user, now=None):
    return _active_qs(user, now).count()


def is_exempt(user):
    return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))


def check_active_limit(user, now=None):
    """None = the user may post. Otherwise {"code", "message", "retry_after", "limit", "active"}."""
    if user is None or is_exempt(user):
        return None
    now = now or timezone.now()
    limit = max_active()
    qs = _active_qs(user, now)
    count = qs.count()
    if count < limit:
        return None
    oldest = qs.order_by("expires_at").values_list("expires_at", flat=True).first()
    retry_after = 60
    if oldest is not None:
        retry_after = max(60, int((oldest - now).total_seconds()) + 1)
    return {
        "code": CODE_ACTIVE_LIMIT,
        "message": f"You already have {count} stories live (the limit is {limit}). "
                   "Delete one, or wait for the oldest to end, then try again.",
        "retry_after": retry_after,
        "limit": limit,
        "active": count,
    }


def clean_expires_at(value, now=None):
    """Clamp a client-supplied `expires_at` into [now + min_lifetime, now + max_lifetime]. None stays None (the model
    default - 24h - applies)."""
    if value is None:
        return None
    now = now or timezone.now()
    lo = now + min_lifetime()
    hi = now + max_lifetime()
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def limits_for(user, now=None):
    """What a client may show: how many slots are left, how long a story lives."""
    now = now or timezone.now()
    count = active_count(user, now)
    limit = max_active()
    return {
        "max_active": limit,
        "active": count,
        "remaining": max(0, limit - count),
        "max_lifetime_hours": int(max_lifetime().total_seconds() // 3600),
        "min_lifetime_minutes": int(min_lifetime().total_seconds() // 60),
    }
