"""
spaced_repetition/conf.py - every tunable of P-F8 in one place (settings.py / env override, safe defaults).

    SRS_ENABLED                 master switch (env, default on). Off => every endpoint 403 `feature_disabled`, reminders fall
                                back to the old "age of the saved item" rule.
    SRS_NEW_PER_DAY             how many NEW (never reviewed) deck items are introduced per day (10)
    SRS_NEW_MIN_AGE_HOURS       a deck item must be saved this long before it can be a new card in a session (0 = at once)
    SRS_MAX_QUEUE               cards returned per session request (30)
    SRS_MAX_INTERVAL_DAYS       the longest gap SM-2 may schedule (365)
    SRS_RELEARN_MINUTES         a card marked "Again" comes back after this many minutes (10)
"""
from django.conf import settings


def _get(name, default):
    return getattr(settings, name, default)


def enabled() -> bool:
    """env master switch AND the remote kill switch (admin `core.FeatureFlag` row key=spaced_repetition, or env
    FEATURE_FLAGS_OFF=spaced_repetition). The admin row can only switch it OFF. Never raises."""
    if not bool(_get("SRS_ENABLED", True)):
        return False
    try:
        from common import feature_flags

        return feature_flags.server_blocked(feature_flags.SPACED_REPETITION) is None
    except Exception:  # noqa: BLE001
        return True


def new_per_day() -> int:
    return max(0, int(_get("SRS_NEW_PER_DAY", 10)))


def new_min_age_hours() -> int:
    return max(0, int(_get("SRS_NEW_MIN_AGE_HOURS", 0)))


def max_queue() -> int:
    return max(1, min(100, int(_get("SRS_MAX_QUEUE", 30))))


def max_interval_days() -> int:
    return max(2, int(_get("SRS_MAX_INTERVAL_DAYS", 365)))


def relearn_minutes() -> int:
    return max(1, int(_get("SRS_RELEARN_MINUTES", 10)))
