"""
post/feed_metrics.py - T1 Part 5: feed quality metrics from PostEvent.

    python manage.py feed_metrics --days 7            # table
    python manage.py feed_metrics --days 1 --json
    GET /post/feed/metrics/?days=7                    # staff only, same numbers

Numbers (per surface, default `feed`):
  impressions / taps / CTR (taps per impression)
  avg dwell (ms) over `dwell` events, long-dwell rate (dwell >= 3 s per impression)
  quick-skip rate (skip events per impression)
  sessions + avg session length (a new session starts after `gap_minutes` idle)
  show-fewer rate (PostHide rows created + FeedFeedback "show fewer" rows touched in the range,
                   per 1000 impressions)
  diversity: unique-author ratio and same-author-back-to-back rate of the
             impression sequence inside sessions (1.0 / 0.0 = perfectly varied)
and the same block per experiment variant (post/feed_experiment.py), so an A/B
test reads straight off `by_variant`.

`summarize()` is pure (rows in, dict out) and unit-tested; `compute_metrics`
streams the rows from the DB with a hard row cap (`truncated: true` when hit).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Callable, Dict, Iterable, Optional, Tuple

LONG_DWELL_MS = 3000
DEFAULT_GAP_MINUTES = 30
DEFAULT_MAX_ROWS = 500_000


class _Acc:
    __slots__ = (
        "users", "impressions", "taps", "dwell_n", "dwell_sum", "long_dwell", "skips",
        "sessions", "session_seconds", "seq", "same_back_to_back", "unique_authors", "hides",
    )

    def __init__(self):
        self.users = set()
        self.impressions = self.taps = self.dwell_n = self.dwell_sum = 0
        self.long_dwell = self.skips = self.sessions = self.same_back_to_back = 0
        self.session_seconds = 0.0
        self.seq = 0  # impressions that have a predecessor in the same session
        self.unique_authors = 0.0  # sum over sessions of unique/total
        self.hides = 0

    def as_dict(self) -> dict:
        imp = self.impressions

        def ratio(a, b):
            return round(a / b, 4) if b else 0.0

        sessions = self.sessions
        return {
            "users": len(self.users),
            "impressions": imp,
            "taps": self.taps,
            "ctr": ratio(self.taps, imp),
            "avg_dwell_ms": round(self.dwell_sum / self.dwell_n) if self.dwell_n else 0,
            "long_dwell_rate": ratio(self.long_dwell, imp),
            "quick_skip_rate": ratio(self.skips, imp),
            "sessions": sessions,
            "avg_session_seconds": round(self.session_seconds / sessions, 1) if sessions else 0.0,
            "impressions_per_user": round(imp / len(self.users), 1) if self.users else 0.0,
            "show_fewer": self.hides,
            "show_fewer_per_1000": round(self.hides * 1000.0 / imp, 2) if imp else 0.0,
            "diversity": {
                "unique_author_ratio": round(self.unique_authors / sessions, 4) if sessions else 0.0,
                "same_author_back_to_back_rate": ratio(self.same_back_to_back, self.seq),
            },
        }


def summarize(
    rows: Iterable[Tuple],
    hides_by_user: Optional[Dict] = None,
    gap_minutes: int = DEFAULT_GAP_MINUTES,
    variant_of: Optional[Callable] = None,
) -> dict:
    """rows = (user_id, author_id, event_type, dwell_ms, ts) ordered by (user_id, ts)."""
    gap = timedelta(minutes=gap_minutes)
    total = _Acc()
    per_variant: Dict[str, _Acc] = {}
    hides_by_user = hides_by_user or {}
    seen_hide_users = set()

    def accs(user_id):
        out = [total]
        if variant_of is not None:
            name = variant_of(user_id)
            out.append(per_variant.setdefault(name, _Acc()))
        return out

    cur_user = object()
    session_start = last_ts = None
    session_authors: list = []
    cur_accs: list = []

    def close_session():
        nonlocal session_start, last_ts, session_authors
        if session_start is None:
            return
        for a in cur_accs:
            a.sessions += 1
            a.session_seconds += max(0.0, (last_ts - session_start).total_seconds())
            if session_authors:
                a.unique_authors += len(set(session_authors)) / len(session_authors)
        session_start = last_ts = None
        session_authors = []

    for user_id, author_id, etype, dwell_ms, ts in rows:
        if user_id != cur_user:
            close_session()
            cur_user = user_id
            cur_accs = accs(user_id)
            for a in cur_accs:
                a.users.add(user_id)
                if user_id in hides_by_user and (id(a), user_id) not in seen_hide_users:
                    seen_hide_users.add((id(a), user_id))
                    a.hides += hides_by_user[user_id]
        if session_start is not None and ts - last_ts > gap:
            close_session()
        if session_start is None:
            session_start = ts
        last_ts = ts
        if etype == "impression":
            for a in cur_accs:
                a.impressions += 1
                if session_authors and session_authors[-1] == author_id:
                    a.same_back_to_back += 1
                if session_authors:
                    a.seq += 1
            session_authors.append(author_id)
        elif etype == "tap":
            for a in cur_accs:
                a.taps += 1
        elif etype == "dwell":
            for a in cur_accs:
                a.dwell_n += 1
                a.dwell_sum += dwell_ms
                if dwell_ms >= LONG_DWELL_MS:
                    a.long_dwell += 1
        elif etype == "skip":
            for a in cur_accs:
                a.skips += 1
    close_session()

    # users who hid something but produced no events in range still count hides
    for user_id, n in hides_by_user.items():
        if (id(total), user_id) not in seen_hide_users:
            total.hides += n
    result = total.as_dict()
    if variant_of is not None:
        result["by_variant"] = {name: acc.as_dict() for name, acc in sorted(per_variant.items())}
    return result


def compute_metrics(start, end, surface: str = "feed", gap_minutes: int = DEFAULT_GAP_MINUTES,
                    max_rows: int = DEFAULT_MAX_ROWS) -> dict:
    from django.db.models import Count

    from . import feed_experiment
    from .models import FeedFeedback, PostEvent, PostHide

    qs = (
        PostEvent.objects.filter(created_at__gte=start, created_at__lt=end, surface=surface)
        .order_by("user_id", "created_at")
        .values_list("user_id", "post__user_id", "event_type", "dwell_ms", "created_at")
    )
    rows = []
    truncated = False
    for i, row in enumerate(qs.iterator(chunk_size=5000)):
        if i >= max_rows:
            truncated = True
            break
        rows.append(row)
    hides = dict(
        PostHide.objects.filter(created_at__gte=start, created_at__lt=end)
        .values_list("user_id").annotate(n=Count("id")).values_list("user_id", "n")
    )
    # T1 item 9: "show fewer like this" rows (FeedFeedback has no created_at - a row touched in range counts)
    for user_id, n in (
        FeedFeedback.objects.filter(updated_at__gte=start, updated_at__lt=end)
        .values_list("user_id").annotate(n=Count("id")).values_list("user_id", "n")
    ):
        hides[user_id] = hides.get(user_id, 0) + n
    variants: Dict = {}

    def variant_of(user_id):
        if user_id not in variants:
            variants[user_id] = feed_experiment.resolve(user_id)["variant"]
        return variants[user_id]

    out = summarize(rows, hides, gap_minutes, variant_of)
    out["range"] = {"start": start.isoformat(), "end": end.isoformat(), "surface": surface}
    out["truncated"] = truncated
    out["experiment"] = feed_experiment.get_config()["name"]
    return out
