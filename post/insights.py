"""
post/insights.py - creator insights computed from the analytics the app already sends.

Sources (nothing new is collected):
  * PostEvent (impression / dwell / tap / skip, per surface) - written by `POST /post/events/`, kept for
    POST_EVENT_RETENTION_DAYS (30) by `post.tasks.prune_old_post_events`. So per-window numbers that
    come from events are only trustworthy for the last 30 days (`events_retention_days` in the payload).
  * PostView (one row per (post, viewer)) - `viewed_at`, and for videos `watch_seconds` (the FURTHEST
    position a viewer reached) + `video_duration_seconds`.
  * Post counters (views_count, likes_count, ...) - all-time totals.

Definitions (shown to the creator in the app):
  views            distinct people who opened / were counted on the post in the window (PostView, is_counted)
  reach            distinct people who had the post on screen in the window (impression events)
  impressions      impression events in the window
  avg_dwell_s      mean on-screen time per dwell event
  dwell_total_s    total on-screen time (sum of dwell events) - what "watch time" means for ANY post type
  video_watch_s    for videos: sum of the furthest-watched seconds per viewer
  completion_rate  for videos: mean(watch_seconds / video_duration), 0..1

The author's own activity is never counted (it would inflate every number).
Pure helpers (`clamp_days`, `ratio`, `fill_days`) hold the arithmetic so tests need no database.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Avg, Count, F, FloatField, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import Post, PostEvent, PostView

DEFAULT_DAYS = 7
MAX_DAYS = 90
TOP_POSTS = 5


def retention_days():
    return int(getattr(settings, "POST_EVENT_RETENTION_DAYS", 30))


def clamp_days(raw, default=DEFAULT_DAYS):
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return default
    return min(MAX_DAYS, max(1, days))


def ratio(num, den, digits=4):
    return round(float(num) / float(den), digits) if den else 0.0


def fill_days(start_date, days, rows, keys):
    """[{date, <key>: n}] for EVERY day in the window (missing days = 0). `rows` = {date: {key: n}}."""
    out = []
    for i in range(days):
        d = start_date + timedelta(days=i)
        row = rows.get(d, {})
        item = {"date": d.isoformat()}
        for k in keys:
            item[k] = row.get(k, 0)
        out.append(item)
    return out


def _window(days):
    end = timezone.now()
    start = end - timedelta(days=days)
    return start, end


def _events(posts_qs, owner, start):
    """Events on `posts_qs` since `start`, excluding the owner's own."""
    return PostEvent.objects.filter(post__in=posts_qs, created_at__gte=start).exclude(user=owner)


def _views(posts_qs, owner, start):
    return PostView.objects.filter(post__in=posts_qs, viewed_at__gte=start, is_counted=True).exclude(user=owner)


def _event_totals(events):
    agg = events.aggregate(
        impressions=Count("id", filter=Q(event_type=PostEvent.EventType.IMPRESSION)),
        reach=Count("user", filter=Q(event_type=PostEvent.EventType.IMPRESSION), distinct=True),
        taps=Count("id", filter=Q(event_type=PostEvent.EventType.TAP)),
        skips=Count("id", filter=Q(event_type=PostEvent.EventType.SKIP)),
        dwell_events=Count("id", filter=Q(event_type=PostEvent.EventType.DWELL)),
        dwell_ms=Sum("dwell_ms", filter=Q(event_type=PostEvent.EventType.DWELL)),
    )
    dwell_s = (agg["dwell_ms"] or 0) / 1000.0
    return {
        "impressions": agg["impressions"] or 0,
        "reach": agg["reach"] or 0,
        "taps": agg["taps"] or 0,
        "skips": agg["skips"] or 0,
        "dwell_total_s": round(dwell_s, 1),
        "avg_dwell_s": round(dwell_s / agg["dwell_events"], 2) if agg["dwell_events"] else 0.0,
    }


def _video_totals(views):
    agg = views.filter(watch_seconds__isnull=False, video_duration_seconds__gt=0).aggregate(
        watched=Sum("watch_seconds"),
        n=Count("id"),
        completion=Avg(F("watch_seconds") / F("video_duration_seconds"), output_field=FloatField()),
    )
    return {
        "video_watch_s": round(agg["watched"] or 0.0, 1),
        "video_viewers": agg["n"] or 0,
        "completion_rate": round(min(1.0, max(0.0, agg["completion"] or 0.0)), 4),
    }


def _daily(events, views, start, days):
    start_date = timezone.localdate(start)
    rows = {}
    for r in (
        events.filter(event_type=PostEvent.EventType.IMPRESSION)
        .annotate(d=TruncDate("created_at")).values("d")
        .annotate(impressions=Count("id"), reach=Count("user", distinct=True))
    ):
        rows.setdefault(r["d"], {}).update(impressions=r["impressions"], reach=r["reach"])
    for r in views.annotate(d=TruncDate("viewed_at")).values("d").annotate(views=Count("id")):
        rows.setdefault(r["d"], {}).update(views=r["views"])
    return fill_days(start_date, days, rows, ("impressions", "reach", "views"))


def _by_surface(events):
    out = {}
    for r in events.filter(event_type=PostEvent.EventType.IMPRESSION).values("surface").annotate(
        impressions=Count("id"), reach=Count("user", distinct=True),
    ):
        out[r["surface"]] = {"impressions": r["impressions"], "reach": r["reach"]}
    return out


def _counters(posts_qs):
    agg = posts_qs.aggregate(
        likes=Sum("likes_count"), comments=Sum("comments_count"), shares=Sum("shares_count"),
        saves=Sum("saves_count"), views=Sum("views_count"),
    )
    return {k: int(v or 0) for k, v in agg.items()}


def post_insights(post, days=DEFAULT_DAYS):
    """Insights of ONE post for its owner (the caller checks permission)."""
    days = clamp_days(days)
    start, end = _window(days)
    posts_qs = Post.objects.filter(pk=post.pk)
    events = _events(posts_qs, post.user, start)
    views = _views(posts_qs, post.user, start)
    totals = _event_totals(events)
    totals["views"] = views.count()
    if post.post_type == "video":
        totals.update(_video_totals(views))
    return {
        "post_id": str(post.pk),
        "post_type": post.post_type,
        "days": days,
        "events_retention_days": retention_days(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "totals": totals,
        "all_time": _counters(posts_qs),
        "by_surface": _by_surface(events),
        "daily": _daily(events, views, start, days),
    }


def creator_overview(user, days=DEFAULT_DAYS):
    """All of `user`'s live posts together + the top posts by reach."""
    days = clamp_days(days)
    start, end = _window(days)
    posts_qs = Post.objects.filter(user=user, is_deleted=False)
    events = _events(posts_qs, user, start)
    views = _views(posts_qs, user, start)
    totals = _event_totals(events)
    totals["views"] = views.count()
    totals.update(_video_totals(views.filter(post__post_type="video")))

    top = []
    ranked = (
        events.filter(event_type=PostEvent.EventType.IMPRESSION)
        .values("post_id").annotate(reach=Count("user", distinct=True), impressions=Count("id"))
        .order_by("-reach", "-impressions")[:TOP_POSTS]
    )
    ids = [r["post_id"] for r in ranked]
    meta = {p.pk: p for p in Post.objects.filter(pk__in=ids).prefetch_related("media")}
    for r in ranked:
        p = meta.get(r["post_id"])
        if p is None:
            continue
        thumb = None
        first = next(iter(p.media.all()), None)
        if first is not None:
            f = first.thumbnail or first.file
            try:
                thumb = f.url if f else None
            except ValueError:
                thumb = None
        top.append({
            "post_id": str(p.pk), "post_type": p.post_type, "caption": (p.content or "")[:120],
            "thumbnail": thumb, "reach": r["reach"], "impressions": r["impressions"],
            "created_at": p.created_at.isoformat(),
        })
    return {
        "days": days,
        "events_retention_days": retention_days(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "posts_count": posts_qs.count(),
        "totals": totals,
        "all_time": _counters(posts_qs),
        "by_surface": _by_surface(events),
        "daily": _daily(events, views, start, days),
        "top_posts": top,
    }
