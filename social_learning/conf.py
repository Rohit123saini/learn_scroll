"""
social_learning/conf.py - every tunable of P-F5 in one place (settings.py / env override, safe defaults).

    SOCIAL_LEARNING_ENABLED                    master kill switch (env, default on). Off => every endpoint 403 `feature_disabled`.
                                               Also switched off by the admin FeatureFlag row `social_learning` (enabled=False)
                                               or FEATURE_FLAGS_OFF=social_learning.
    SOCIAL_POMODORO_PARTICIPANTS_CAN_CONTROL   group rooms: may a plain participant start/stop the shared timer? (default False;
                                               in a 1:1 / non-group room both people may always control it)
    SOCIAL_POMODORO_MIN_PRESENCE_FRACTION      share of a focus round the user must have been in the room to claim it (0.8)
    SOCIAL_POMODORO_DAILY_CREDIT_CAP           max credited pomodoros per user per day (16)
    SOCIAL_PARTNER_MAX_ACTIVE                  active accountability partners per user (3)
    SOCIAL_PARTNER_MAX_PENDING_OUT             open requests a user may have sent (5)
    SOCIAL_PARTNER_MANUAL_MAX_MINUTES          max minutes of "I studied offline" per day (120)
    SOCIAL_PARTNER_AUTO_NUDGE                  evening reminder push to the partner who is behind (default on)
    SOCIAL_SEASON_DAYS                         length of one season (28 = 4 weeks, Monday 00:00 IST to Monday 00:00 IST)
    SOCIAL_SEASON_AUTO_CREATE                  create the next season automatically (default on)
"""
from django.conf import settings


def _get(name, default):
    return getattr(settings, name, default)


def enabled() -> bool:
    """env master switch AND the remote kill switch (admin `core.FeatureFlag` row key=social_learning, or env
    FEATURE_FLAGS_OFF=social_learning). The admin row can only switch it OFF. Never raises: a flag lookup problem keeps
    the feature as the env says."""
    if not bool(_get("SOCIAL_LEARNING_ENABLED", True)):
        return False
    try:
        from common import feature_flags

        return feature_flags.server_blocked(feature_flags.SOCIAL_LEARNING) is None
    except Exception:
        return True


def pomodoro_participants_can_control() -> bool:
    return bool(_get("SOCIAL_POMODORO_PARTICIPANTS_CAN_CONTROL", False))


def pomodoro_min_presence_fraction() -> float:
    return float(_get("SOCIAL_POMODORO_MIN_PRESENCE_FRACTION", 0.8))


def pomodoro_daily_credit_cap() -> int:
    return int(_get("SOCIAL_POMODORO_DAILY_CREDIT_CAP", 16))


def partner_max_active() -> int:
    return int(_get("SOCIAL_PARTNER_MAX_ACTIVE", 3))


def partner_max_pending_out() -> int:
    return int(_get("SOCIAL_PARTNER_MAX_PENDING_OUT", 5))


def partner_manual_max_minutes() -> int:
    return int(_get("SOCIAL_PARTNER_MANUAL_MAX_MINUTES", 120))


def partner_auto_nudge() -> bool:
    return bool(_get("SOCIAL_PARTNER_AUTO_NUDGE", True))


def season_days() -> int:
    return max(7, int(_get("SOCIAL_SEASON_DAYS", 28)))


def season_auto_create() -> bool:
    return bool(_get("SOCIAL_SEASON_AUTO_CREATE", True))
