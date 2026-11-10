"""
post/creator_analytics.py - P-F9_F10 creator analytics in ONE cached call.  (written, NOT RUN)

    GET /post/creator-analytics/?days=7|30|90   (post/creator_analytics_views.py)

Adds, on top of post/insights.py + user_profile/profile_insights.py (nothing new is collected):
  * best_time      when the creator's audience is most active, hour x weekday, in the SERVER local time zone
                   (settings.TIME_ZONE). Engagement events only (dwell + tap) from OTHER people. Needs
                   MIN_EVENTS_FOR_BEST_TIME events, otherwise `enough_data` is False and the app shows a hint.
  * watch_time     daily series of on-screen seconds (dwell) + total + previous-window total + change %.
  * followers      gained / lost / net in the window (re-uses profile_insights, one source of truth).
  * top_posts      top posts by reach (re-uses post.insights.creator_overview).
  * totals         reach / impressions / views / watch seconds / completion.

Cached CACHE_SECONDS (10 min) per (user, days). A cache failure never breaks the endpoint.
Pure helpers (`weekday_hour_grid`, `top_slots`, `change_pct`) hold the arithmetic so tests need no database.
Event data only exists for `events_retention_days` (30) days; the payload says so.
"""
import logging
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractHour, ExtractWeekDay, TruncDate
from django.utils import timezone

from . import insights
from .models import Post, PostEvent

logger = logging.getLogger(__name__)

CACHE_SECONDS = 600
MIN_EVENTS_FOR_BEST_TIME = 20
TOP_SLOTS = 3
CACHE_VERSION = "v1"


def cache_key(user_id, days):
    return f"creator_analytics:{CACHE_VERSION}:{user_id}:{days}"


def change_pct(current, previous):
    """% change vs previous window, 1 decimal; None when there is nothing to compare with."""
    if not previous:
        return None
    return round((float(current) - float(previous)) / float(previous) * 100.0, 1)


def weekday_hour_grid(rows):
    """rows: iterable of (django_week_day 1=Sun..7=Sat, hour 0..23, count) -> 7x24 grid, index 0 = Monday."""
    grid = [[0] * 24 for _ in range(7)]
    for wd, hour, n in rows:
        if wd is None or hour is None:
            continue
        idx = (int(wd) + 5) % 7  # Django: Sun=1 -> 6, Mon=2 -> 0, ..., Sat=7 -> 5
        h = int(hour)
        if 0 <= h <= 23:
            grid[idx][h] += int(n or 0)
    return grid


def top_slots(grid, limit=TOP_SLOTS):
    """Best (weekday, hour) cells, highest first; ties -> earlier weekday / hour. Empty cells are never returned."""
    cells = [(grid[d][h], d, h) for d in range(7) for h in range(24) if grid[d][h] > 0]
    cells.sort(key=lambda c: (-c[0], c[1], c[2]))
    return [{"weekday": d, "hour": h, "score": s} for s, d, h in cells[:limit]]


def _best_time(user, start):
    events = (
        PostEvent.objects.filter(
            post__user=user, post__is_deleted=False, created_at__gte=start,
            event_type__in=(PostEvent.EventType.DWELL, PostEvent.EventType.TAP),
        ).exclude(user=user)
    )
    rows = (
        events.annotate(wd=ExtractWeekDay("created_at"), hr=ExtractHour("created_at"))
        .values_list("wd", "hr").annotate(n=Count("id"))
    )
    rows = [(wd, hr, n) for wd, hr, n in rows]
    grid = weekday_hour_grid(rows)
    total = sum(sum(r) for r in grid)
    return {
        "enough_data": total >= MIN_EVENTS_FOR_BEST_TIME,
        "events": total,
        "min_events": MIN_EVENTS_FOR_BEST_TIME,
        "weekday_zero_is": "monday",
        "timezone": str(timezone.get_current_timezone()),
        "top_slots": top_slots(grid) if total >= MIN_EVENTS_FOR_BEST_TIME else [],
        "grid": grid,
    }


def _watch_time(user, start, days):
    prev_start = start - timedelta(days=days)
    base = PostEvent.objects.filter(
        post__user=user, post__is_deleted=False, event_type=PostEvent.EventType.DWELL,
    ).exclude(user=user)
    cur = base.filter(created_at__gte=start)
    prev = base.filter(created_at__gte=prev_start, created_at__lt=start)
    total_ms = cur.aggregate(t=Sum("dwell_ms"))["t"] or 0
    prev_ms = prev.aggregate(t=Sum("dwell_ms"))["t"] or 0
    rows = {}
    for r in cur.annotate(d=TruncDate("created_at")).values("d").annotate(ms=Sum("dwell_ms")):
        rows[r["d"]] = {"seconds": round((r["ms"] or 0) / 1000.0, 1)}
    return {
        "total_s": round(total_ms / 1000.0, 1),
        "prev_total_s": round(prev_ms / 1000.0, 1),
        "change_pct": change_pct(total_ms, prev_ms),
        "daily": insights.fill_days(timezone.localdate(start), days, rows, ("seconds",)),
    }


def _followers(user, days):
    from user_profile import profile_insights as pi

    today = timezone.localdate()
    start, prev_start = pi.window_dates(today, days)
    block = pi._followers_block(user, start, days, prev_start)
    return {k: block[k] for k in ("now", "gained", "prev_gained", "change_pct", "lost", "net", "daily")}


def build(user, days=insights.DEFAULT_DAYS):
    days = insights.clamp_days(days)
    start, end = insights._window(days)
    overview = insights.creator_overview(user, days)
    return {
        "days": days,
        "generated_at": end.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "events_retention_days": insights.retention_days(),
        "posts_count": overview.get("posts_count", 0),
        "totals": overview.get("totals", {}),
        "reach_daily": overview.get("daily", []),
        "top_posts": overview.get("top_posts", []),
        "watch_time": _watch_time(user, start, days),
        "followers": _followers(user, days),
        "best_time": _best_time(user, start),
        "cache_seconds": CACHE_SECONDS,
    }


def get(user, days=insights.DEFAULT_DAYS, use_cache=True):
    days = insights.clamp_days(days)
    key = cache_key(user.pk, days)
    if use_cache:
        try:
            hit = cache.get(key)
            if hit is not None:
                return hit
        except Exception:
            logger.warning("creator analytics: cache read failed", exc_info=True)
    data = build(user, days)
    if use_cache:
        try:
            cache.set(key, data, CACHE_SECONDS)
        except Exception:
            logger.warning("creator analytics: cache write failed", exc_info=True)
    return data
