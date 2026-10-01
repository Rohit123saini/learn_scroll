"""
user_profile/activity.py

P14-BE — helpers for the "Your activity" screen (GET /profile/activity/).

Everything that reads the `post` app lives here, behind a small adapter
(`_model()` / `_field()`), so the post models are touched in exactly one
place and imported lazily (same circular-import rule as the rest of this
app). `PostLike` / `PostComment` / `PostShare` are the names
`leaderboard/services.py` already imports; the saves model name and the
user/timestamp field names are looked up from a short candidate list
instead of hard-coded. If one can't be resolved the affected count/list is
returned empty and an ERROR is logged — it never 500s the whole screen.
Once you know the real names, trim the candidate tuples below.
"""
import logging
from datetime import datetime, time, timedelta

from django.apps import apps
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import serializers

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 50

# Candidate names — first one that exists wins.
_USER_FIELDS = ("user", "owner", "shared_by", "sender")
_TIME_FIELDS = ("created_at", "created", "timestamp", "saved_at", "shared_at")
_SAVE_MODELS = ("PostSave", "SavedPost", "Save", "PostBookmark", "Bookmark")


class HeartbeatSerializer(serializers.Serializer):
    # Foreground seconds since the previous heartbeat. The model manager
    # clamps this further (ACTIVITY_HEARTBEAT_MAX_SECONDS) and against
    # wall-clock time, so this bound is only a sanity check.
    seconds = serializers.IntegerField(min_value=0, max_value=3600)


class ActivityLimitSerializer(serializers.Serializer):
    # null / 0 = reminder off.
    daily_limit_minutes = serializers.IntegerField(
        min_value=0, max_value=1440, allow_null=True,
    )

    def validate_daily_limit_minutes(self, value):
        if value is None or value == 0:
            return None
        if value < 5:
            raise serializers.ValidationError("Choose at least 5 minutes, or turn the reminder off.")
        return value


# ---------------------------------------------------------------------------
# adapter
# ---------------------------------------------------------------------------
def _model(*names):
    for name in names:
        try:
            return apps.get_model("post", name)
        except LookupError:
            continue
    return None


def _field(model, candidates):
    if model is None:
        return None
    existing = {f.name for f in model._meta.get_fields()}
    for name in candidates:
        if name in existing:
            return name
    return None


def _resolve(label, names):
    """-> (model, user_field, time_field) or None (logged) if unusable."""
    model = _model(*names)
    user_f = _field(model, _USER_FIELDS)
    time_f = _field(model, _TIME_FIELDS)
    if model is None or user_f is None or time_f is None:
        logger.error(
            "activity: cannot resolve post %s model (model=%s user_field=%s time_field=%s) — "
            "adjust the candidate tuples in user_profile/activity.py",
            label, getattr(model, "__name__", None), user_f, time_f,
        )
        return None
    return model, user_f, time_f


def _post_not_deleted(model):
    """Q excluding rows whose post is soft-deleted (Post.is_deleted — the
    same flag leaderboard/services.py filters on)."""
    return Q(post__is_deleted=False) if "post" in {f.name for f in model._meta.get_fields()} else Q()


# ---------------------------------------------------------------------------
# window + counts
# ---------------------------------------------------------------------------
def window_dates(today=None):
    """Oldest -> newest: the last 7 local dates, ending today."""
    today = today or timezone.localdate()
    return [today - timedelta(days=i) for i in range(WINDOW_DAYS - 1, -1, -1)]


def _window_start(first_date):
    return timezone.make_aware(datetime.combine(first_date, time.min))


def _daily_counts(label, names, user, dates, *, extra=None):
    """{date: count} for rows this user created in the window, or None when
    the model can't be resolved (distinguishes "0 likes" from "unknown")."""
    resolved = _resolve(label, names)
    if resolved is None:
        return None
    model, user_f, time_f = resolved
    qs = model.objects.filter(**{user_f: user, f"{time_f}__gte": _window_start(dates[0])})
    qs = qs.filter(_post_not_deleted(model))
    if extra is not None:
        qs = qs.filter(extra)
    rows = qs.annotate(activity_day=TruncDate(time_f)).values("activity_day").annotate(c=Count("pk")).values_list("activity_day", "c")
    return {d: c for d, c in rows}


def build_week(user, usage_rows):
    """The 7 day rows + totals. `usage_rows` = {date: seconds}."""
    dates = window_dates()
    likes = _daily_counts("like", ("PostLike",), user, dates)
    comments = _daily_counts("comment", ("PostComment",), user, dates, extra=Q(is_deleted=False))
    shares = _daily_counts("share", ("PostShare",), user, dates)

    def pick(counts, d):
        return None if counts is None else counts.get(d, 0)

    days = [
        {
            "date": d.isoformat(),
            "seconds": usage_rows.get(d, 0),
            "likes": pick(likes, d),
            "comments": pick(comments, d),
            "shares": pick(shares, d),
        }
        for d in dates
    ]

    def total(key):
        vals = [row[key] for row in days]
        return None if any(v is None for v in vals) else sum(vals)

    seconds_total = sum(row["seconds"] for row in days)
    totals = {
        "seconds": seconds_total,
        "avg_seconds_per_day": seconds_total // WINDOW_DAYS,
        "likes": total("likes"),
        "comments": total("comments"),
        "shares": total("shares"),
    }
    return days, totals


# ---------------------------------------------------------------------------
# saved / liked lists
# ---------------------------------------------------------------------------
def _blocked_owner_ids(user):
    """Owners to hide from the lists: anyone blocked either way."""
    from .models import BlockUser

    ids = set()
    for blocker_id, blocked_id in BlockUser.objects.filter(
        Q(blocker=user) | Q(blocked=user)
    ).values_list("blocker_id", "blocked_id"):
        ids.update((blocker_id, blocked_id))
    ids.discard(user.pk)
    return ids


def _recent_posts(label, names, user, limit):
    resolved = _resolve(label, names)
    if resolved is None:
        return []
    model, user_f, time_f = resolved
    qs = (
        model.objects.filter(**{user_f: user})
        .filter(_post_not_deleted(model))
        .select_related("post", "post__user")
        .order_by(f"-{time_f}")
    )
    blocked = _blocked_owner_ids(user)
    if blocked:
        qs = qs.exclude(post__user_id__in=blocked)

    out = []
    for row in qs[:limit]:
        post = row.post
        owner = getattr(post, "user", None)
        out.append({
            "post_id": str(post.pk),
            "post_type": getattr(post, "post_type", None),
            "owner_id": getattr(owner, "pk", None),
            "owner_username": getattr(owner, "username", None),
            "post_created_at": getattr(post, "created_at", None),
            "at": getattr(row, time_f),
        })
    return out


def saved_posts(user, limit):
    return _recent_posts("save", _SAVE_MODELS, user, limit)


def liked_posts(user, limit):
    return _recent_posts("like", ("PostLike",), user, limit)
