"""
user_profile/profile_insights.py - P9 "Profile insights" (creator / teacher). NOT RUN.

What the owner sees (GET /profile/insights/?days=7|30|90, see profile_insights_views.py):
  visits         profile opens in the window + unique visitors + the same numbers for the PREVIOUS window + a daily series
  followers      followers now, NEW followers in the window (+ previous window, daily series)
  content        reach / impressions / views of the owner's posts and the top posts - straight from post/insights.py
                 (so a post's numbers here and on its own insights screen can never disagree)

Honest limits (also sent to the app as `notes`):
  * lost followers are counted from the day FollowerLoss existed (an unfollow deletes the Follow row, so earlier ones are
    gone); only a per-day NUMBER is kept - never who left;
  * reach / impressions exist for the last `events_retention_days` days only (post.insights);
  * visits are kept PROFILE_VISIT_RETENTION_DAYS (default 90) days.

Privacy:
  * only COUNTS are ever returned - never who visited (nothing in this module returns a viewer id / name);
  * a visit is NOT recorded when: it is your own profile, either side blocked the other, the viewer is under 18
    (no tracking rows about minors), or the profile owner is not active;
  * recording can never break opening a profile: `record_visit` swallows its own errors.
Pure helpers (`clamp_days`, `change_pct`, `window_dates`) hold the arithmetic so tests need no database.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 7
ALLOWED_DAYS = (7, 30, 90)
MAX_DAYS = 90


def retention_days() -> int:
    return int(getattr(settings, "PROFILE_VISIT_RETENTION_DAYS", 90))


def clamp_days(raw, default=DEFAULT_DAYS) -> int:
    """Any integer 1..90; a bad value falls back to [default] (never a 400)."""
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return default
    return min(MAX_DAYS, max(1, days))


def change_pct(current, previous):
    """% change vs the previous window, 1 decimal. None when there is nothing to compare with (previous == 0)."""
    if not previous:
        return None
    return round((float(current) - float(previous)) / float(previous) * 100.0, 1)


def window_dates(today, days):
    """(start, prev_start): the window is the last `days` days INCLUDING today; the previous window is the `days` before it."""
    start = today - timedelta(days=days - 1)
    return start, start - timedelta(days=days)


def _fill(start, days, rows, keys):
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        row = rows.get(d, {})
        item = {"date": d.isoformat()}
        for k in keys:
            item[k] = row.get(k, 0)
        out.append(item)
    return out


# --------------------------------------------------------------------------- recording

def record_visit(profile_user, viewer, blocks_checked=False) -> bool:
    """Count one open of [profile_user]'s profile by [viewer]. True when something was recorded. Never raises.

    [blocks_checked]: the caller already refused blocked pairs (the profile view does), so skip the two block queries."""
    try:
        if viewer is None or profile_user is None or not getattr(viewer, "is_authenticated", False):
            return False
        if viewer.pk == profile_user.pk or not getattr(profile_user, "is_active", True):
            return False

        from .models import BlockUser, ProfileVisit
        from .privacy import is_minor_user

        if not blocks_checked:
            if BlockUser.objects.filter(blocker=profile_user, blocked=viewer).exists():
                return False
            if BlockUser.objects.filter(blocker=viewer, blocked=profile_user).exists():
                return False
        if is_minor_user(viewer):
            return False

        today = timezone.localdate()
        with transaction.atomic():
            updated = ProfileVisit.objects.filter(profile=profile_user, viewer=viewer, day=today).update(
                visits=F("visits") + 1, updated_at=timezone.now()
            )
            if not updated:
                try:
                    with transaction.atomic():
                        ProfileVisit.objects.create(profile=profile_user, viewer=viewer, day=today, visits=1)
                except IntegrityError:  # two requests raced to create the row -> the other one won, count ours on it
                    ProfileVisit.objects.filter(profile=profile_user, viewer=viewer, day=today).update(
                        visits=F("visits") + 1, updated_at=timezone.now()
                    )
        return True
    except Exception:
        logger.warning("profile visit not recorded", exc_info=True)
        return False


def record_loss(profile_user, count=1) -> bool:
    """P9b: [profile_user] lost [count] accepted follower(s) today (unfollow / "Remove follower"). Only a counter - the
    follower is not stored. True when recorded. Never raises (and its own savepoint keeps a failure from breaking the
    caller's transaction)."""
    try:
        if profile_user is None or count < 1 or not getattr(profile_user, "pk", None):
            return False
        from .models import FollowerLoss

        today = timezone.localdate()
        with transaction.atomic():
            updated = FollowerLoss.objects.filter(profile=profile_user, day=today).update(
                lost=F("lost") + count, updated_at=timezone.now()
            )
            if not updated:
                try:
                    with transaction.atomic():
                        FollowerLoss.objects.create(profile=profile_user, day=today, lost=count)
                except IntegrityError:  # raced with another request that created today's row
                    FollowerLoss.objects.filter(profile=profile_user, day=today).update(
                        lost=F("lost") + count, updated_at=timezone.now()
                    )
        return True
    except Exception:
        logger.warning("follower loss not recorded", exc_info=True)
        return False


def prune_old_visits(now=None) -> int:
    """Delete visit rows (and lost-follower counters) older than the retention window. Returns how many rows went."""
    from .models import FollowerLoss, ProfileVisit

    today = timezone.localdate(now) if now else timezone.localdate()
    cutoff = today - timedelta(days=retention_days())
    deleted, _ = ProfileVisit.objects.filter(day__lt=cutoff).delete()
    lost_deleted, _ = FollowerLoss.objects.filter(day__lt=cutoff).delete()
    return int(deleted) + int(lost_deleted)


# --------------------------------------------------------------------------- reading

def _visits_block(user, start, days, prev_start):
    from .models import ProfileVisit

    qs = ProfileVisit.objects.filter(profile=user)
    cur = qs.filter(day__gte=start)
    prev = qs.filter(day__gte=prev_start, day__lt=start)

    cur_agg = cur.aggregate(total=Sum("visits"), unique=Count("viewer", distinct=True))
    prev_agg = prev.aggregate(total=Sum("visits"), unique=Count("viewer", distinct=True))
    rows = {}
    for r in cur.values("day").annotate(visits=Sum("visits"), unique=Count("viewer", distinct=True)):
        rows[r["day"]] = {"visits": int(r["visits"] or 0), "unique": int(r["unique"] or 0)}

    total, unique = int(cur_agg["total"] or 0), int(cur_agg["unique"] or 0)
    prev_total, prev_unique = int(prev_agg["total"] or 0), int(prev_agg["unique"] or 0)
    # The previous window can only be trusted when the visit table reaches back that far.
    return {
        "total": total,
        "unique": unique,
        "prev_total": prev_total,
        "prev_unique": prev_unique,
        "change_pct": change_pct(total, prev_total),
        "daily": _fill(start, days, rows, ("visits", "unique")),
    }


def _followers_block(user, start, days, prev_start):
    from .models import Follow, FollowerLoss

    qs = Follow.objects.filter(following=user, status=Follow.Status.ACCEPTED)
    now_count = qs.count()
    cur = qs.filter(created_at__date__gte=start)
    gained = cur.count()
    prev_gained = qs.filter(created_at__date__gte=prev_start, created_at__date__lt=start).count()
    rows = {}
    for r in cur.annotate(d=TruncDate("created_at")).values("d").annotate(n=Count("id")):
        rows[r["d"]] = {"gained": int(r["n"])}

    # P9b: followers who left (unfollow / removed) - counters only
    losses = FollowerLoss.objects.filter(profile=user)
    lost = int(losses.filter(day__gte=start).aggregate(t=Sum("lost"))["t"] or 0)
    prev_lost = int(losses.filter(day__gte=prev_start, day__lt=start).aggregate(t=Sum("lost"))["t"] or 0)
    for r in losses.filter(day__gte=start).values("day", "lost"):
        rows.setdefault(r["day"], {})["lost"] = int(r["lost"])
    return {
        "now": now_count,
        "gained": gained,
        "prev_gained": prev_gained,
        "change_pct": change_pct(gained, prev_gained),
        "lost": lost,
        "prev_lost": prev_lost,
        "net": gained - lost,
        "daily": _fill(start, days, rows, ("gained", "lost")),
    }


def _content_block(user, days):
    """Reach / impressions / views + top posts, from the existing post insights (one source of truth)."""
    try:
        from post import insights as post_insights

        o = post_insights.creator_overview(user, days)
    except Exception:
        logger.warning("profile insights: content block failed", exc_info=True)
        return {"available": False, "events_retention_days": 30, "reach": 0, "impressions": 0, "views": 0,
                "posts_count": 0, "top_posts": []}
    t = o.get("totals", {})
    return {
        "available": True,
        "events_retention_days": o.get("events_retention_days", 30),
        "posts_count": o.get("posts_count", 0),
        "reach": t.get("reach", 0),
        "impressions": t.get("impressions", 0),
        "views": t.get("views", 0),
        "top_posts": o.get("top_posts", []),
    }


def build(user, days=DEFAULT_DAYS) -> dict:
    """The whole report for [user]'s own profile. The caller guarantees `user` is the logged-in user."""
    days = clamp_days(days)
    today = timezone.localdate()
    start, prev_start = window_dates(today, days)
    content = _content_block(user, days)
    return {
        "days": days,
        "window_start": start.isoformat(),
        "window_end": today.isoformat(),
        "visits": _visits_block(user, start, days, prev_start),
        "followers": _followers_block(user, start, days, prev_start),
        "content": content,
        "privacy": {
            "visitors_hidden": True,  # only counts - nobody, including you, can see who visited
            "visits_retention_days": retention_days(),
            "minors_not_counted": True,
        },
        "notes": {
            "followers_gained_only": False,  # P9b: lost followers are counted (from when FollowerLoss started)
            "events_retention_days": content["events_retention_days"],
        },
    }
