"""
post/reel_insights.py - creator insights of ONE reel (video post): views, watch time, retention graph, shares,
follows gained.  `GET /post/<id>/reel-insights/?days=7` (post/reel_insights_views.py), owner only.

Everything is derived from rows the app already writes - no new tracking, no migration:
  views            distinct viewers (PostView, is_counted) whose first view falls in the window
  plays            same as views for videos (one PostView row per viewer; see PostView)
  watch_time_s     sum of the FURTHEST second each viewer reached (PostView.watch_seconds) - the best "watch time" the
                   app records; avg_watch_s = watch_time_s / viewers that reported progress
  completion_rate  mean(watch_seconds / video_duration_seconds), 0..1
  retention        `RETENTION_POINTS + 1` points [{t: 0..1 of the reel, pct: share of viewers who got at least that far}],
                   a non-increasing curve starting at 1.0 (pct 0 for every point when nobody reported progress yet)
  shares           PostShare rows in the window (one per sharer, see PostShare's unique_together)
  likes / comments reactions and top-level comments created in the window; saves = all-time counter
  follows_gained   people who followed the author within 24 h AFTER first viewing this reel (an attribution rule, not
                   a hard fact: a viewer may have followed for another reason). Follow requests still PENDING are
                   not counted. The author's own activity is excluded everywhere.
Pure helpers (`retention_curve`, `attribute_follows`, `fill_daily`) hold the arithmetic and need no database.
"""
from datetime import timedelta

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .insights import clamp_days, fill_days, ratio, retention_days
from .models import Post, PostComment, PostLike, PostShare, PostView

RETENTION_POINTS = 20          # 0%, 5%, ... 100% of the reel
FOLLOW_ATTRIBUTION_HOURS = 24
MAX_PROGRESS_ROWS = 20000      # safety cap for the in-Python curve / attribution


def retention_curve(ratios, points=RETENTION_POINTS):
    """`ratios` = per-viewer fraction of the reel watched (0..1). -> [{t, pct}] with points+1 entries."""
    clean = [min(1.0, max(0.0, float(r))) for r in ratios]
    n = len(clean)
    out = []
    for i in range(points + 1):
        t = i / points
        if not n:
            out.append({"t": round(t, 4), "pct": 0.0})
            continue
        # tiny epsilon so a viewer who finished (ratio 0.9999 from rounding) still counts at 100%
        reached = sum(1 for r in clean if r + 1e-6 >= t)
        out.append({"t": round(t, 4), "pct": round(reached / n, 4)})
    return out


def attribute_follows(views, follows, hours=FOLLOW_ATTRIBUTION_HOURS):
    """`views` = {user_id: viewed_at}; `follows` = [(follower_id, created_at)].
    -> list of follow datetimes that happened within `hours` AFTER that follower first viewed the reel."""
    window = timedelta(hours=hours)
    out = []
    for follower_id, created_at in follows:
        seen = views.get(follower_id)
        if seen is not None and created_at is not None and seen <= created_at <= seen + window:
            out.append(created_at)
    return out


def fill_daily(start_date, days, rows):
    return fill_days(start_date, days, rows, ("views", "watch_s", "shares", "follows"))


def reel_insights(post, days=None):
    """Insights payload for one video post (the caller checks ownership + that it is a video)."""
    days = clamp_days(days)
    end = timezone.now()
    start = end - timedelta(days=days)
    owner = post.user

    views_qs = PostView.objects.filter(post=post, viewed_at__gte=start, is_counted=True).exclude(user=owner)
    views_count = views_qs.count()

    prog = list(
        views_qs.filter(watch_seconds__isnull=False, video_duration_seconds__gt=0)
        .values_list("watch_seconds", "video_duration_seconds")[:MAX_PROGRESS_ROWS]
    )
    watch_time = sum(max(0.0, w) for w, _d in prog)
    ratios = [min(1.0, w / d) for w, d in prog if d]
    progress_viewers = len(ratios)

    # ---- shares / reactions in the window
    shares_qs = PostShare.objects.filter(post=post, created_at__gte=start).exclude(user=owner)
    likes = PostLike.objects.filter(post=post, created_at__gte=start).exclude(user=owner).count()
    comments = PostComment.objects.filter(
        post=post, parent__isnull=True, is_deleted=False, created_at__gte=start,
    ).exclude(user=owner).count()

    # ---- follows gained (attributed)
    follows_dt = []
    try:
        from user_profile.models import Follow
        viewers = dict(
            views_qs.exclude(user__isnull=True).values_list("user_id", "viewed_at")[:MAX_PROGRESS_ROWS]
        )
        if viewers:
            rows = Follow.objects.filter(
                following=owner, status="ACCEPTED", follower_id__in=list(viewers.keys()), created_at__gte=start,
            ).values_list("follower_id", "created_at")[:MAX_PROGRESS_ROWS]
            follows_dt = attribute_follows(viewers, list(rows))
    except Exception:  # insights must never 500 because of the follow graph
        follows_dt = []

    # ---- daily series
    start_date = timezone.localdate(start)
    daily_rows = {}
    for r in views_qs.annotate(d=TruncDate("viewed_at")).values("d").annotate(n=Count("id"), w=Sum("watch_seconds")):
        daily_rows.setdefault(r["d"], {}).update(views=r["n"], watch_s=round(r["w"] or 0.0, 1))
    for r in shares_qs.annotate(d=TruncDate("created_at")).values("d").annotate(n=Count("id")):
        daily_rows.setdefault(r["d"], {}).update(shares=r["n"])
    for dt in follows_dt:
        d = timezone.localtime(dt).date()
        row = daily_rows.setdefault(d, {})
        row["follows"] = row.get("follows", 0) + 1

    return {
        "post_id": str(post.pk),
        "days": days,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "events_retention_days": retention_days(),
        "duration_seconds": _duration(post),
        "totals": {
            "views": views_count,
            "watch_time_s": round(watch_time, 1),
            "avg_watch_s": round(watch_time / progress_viewers, 2) if progress_viewers else 0.0,
            "completion_rate": round(sum(ratios) / progress_viewers, 4) if progress_viewers else 0.0,
            "progress_viewers": progress_viewers,
            "shares": shares_qs.count(),
            "follows_gained": len(follows_dt),
            "likes": likes,
            "comments": comments,
        },
        "all_time": {
            "views": int(post.views_count or 0), "likes": int(post.likes_count or 0),
            "comments": int(post.comments_count or 0), "shares": int(post.shares_count or 0),
            "saves": int(post.saves_count or 0),
        },
        "retention": retention_curve(ratios),
        "daily": fill_daily(start_date, days, daily_rows),
        "follow_rate": ratio(len(follows_dt), views_count),
    }


def _duration(post):
    media = next((m for m in sorted(post.media.all(), key=lambda m: m.display_order) if m.media_type == "video"), None)
    return media.duration_seconds if media is not None else None
