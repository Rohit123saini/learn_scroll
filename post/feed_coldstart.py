"""
post/feed_coldstart.py - H17: a brand-new user NEVER sees an empty Home feed.

WHY: the normal pools (following / recommended / trending, post/feed_mix.py) can still all come back empty for
someone with no follows and no history: the recommended + trending pools only look at a recent window, skip
posts the caller already saw, and a young platform may simply have little fresh content. The H10 fallback has
the same 14-day window. A new user's first impression is then a blank screen.

WHAT: `backfill(...)` is the LAST line of defence, used only when the page-1 pools of a real (unfiltered,
non-Exam-Mode) request are completely empty. It fills the TRENDING pool with the best public posts of ALL TIME,
ordered by

    1. the user's chosen interests first (UserInterest categories, then subjects / class-exam hashtags),
    2. engagement (likes, comments, shares, views - the same weights as post.guest_views.with_engagement_score),
    3. newest.

It uses the caller's own "safe to show" queryset (`HomeFeedView._base_qs`: blocks, mutes, hidden posts, age and
moderation rules already applied), public + non-private authors only, own posts last. Nothing is stored; the
result is frozen into the normal snapshot, so page 2+ stay stable.

NOT used when: a chip filter is active (an empty chip is an honest answer), Exam Mode is on (a promise:
"distraction free"), or the ranking itself is healthy and returned something. Never raises.
"""
import logging
from typing import Dict, Optional

from django.conf import settings
from django.db.models import Case, F, FloatField, IntegerField, Value, When
from django.db.models.expressions import ExpressionWrapper

logger = logging.getLogger(__name__)

DEFAULTS = {
    "enabled": True,
    "pool_cap": 60,          # ids put into the backfill pool
}


def get_config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(getattr(settings, "FEED_COLD_START", None) or {})
    return cfg


def is_enabled() -> bool:
    return bool(get_config()["enabled"])


def user_interest_categories(user) -> list:
    try:
        from .models import UserInterest

        return list(UserInterest.objects.filter(user=user).values_list("category", flat=True))
    except Exception:  # noqa: BLE001
        logger.warning("feed_coldstart: interests lookup failed", exc_info=True)
        return []


def is_personalised(user) -> bool:
    """True when the user told us something (interests, class/exam or subjects) - used for the first_feed event."""
    if user_interest_categories(user):
        return True
    try:
        from . import feed_exam

        return feed_exam.has_profile(feed_exam.load_profile(user))
    except Exception:  # noqa: BLE001
        return False


def backfill(user, base_qs, following_ids: set, pools: Dict[str, list], meta: Optional[dict] = None) -> Dict[str, list]:
    """If every pool in `pools` is empty, return pools with the TRENDING pool filled; otherwise `pools` unchanged."""
    if not is_enabled() or any(pools.get(src) for src in pools):
        return pools
    try:
        from . import feed_mix

        cfg = get_config()
        cats = user_interest_categories(user)
        qs = base_qs.filter(visibility="public", user__is_private=False)

        interest_rank = (
            Case(When(category__in=cats, then=Value(1)), default=Value(0), output_field=IntegerField())
            if cats else Value(0, output_field=IntegerField())
        )
        score = ExpressionWrapper(
            F("likes_count") * 3.0 + F("comments_count") * 5.0 + F("shares_count") * 10.0 + F("views_count") * 0.1,
            output_field=FloatField(),
        )
        ranked = qs.annotate(_cs_interest=interest_rank, _cs_score=score).order_by(
            "-_cs_interest", "-_cs_score", "-created_at", "-id"
        )
        ids = list(ranked.exclude(user=user).values_list("id", flat=True)[: int(cfg["pool_cap"])])
        if not ids:  # nothing from anybody else at all: the user's own public posts beat a blank screen
            ids = list(ranked.values_list("id", flat=True)[: int(cfg["pool_cap"])])
        if not ids:
            return pools
        out = {src: [] for src in pools}
        out[feed_mix.SOURCE_TRENDING] = ids
        if meta is not None:
            meta["cold_start_backfill"] = True
        return out
    except Exception:  # noqa: BLE001 - the backfill must never turn a 200 into a 500
        logger.warning("feed_coldstart: backfill failed", exc_info=True)
        return pools
