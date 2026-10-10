"""
post/feed_signals.py - T1 Part 4: richer behavioural signals.

Until now the feed learned from likes / comments (author affinity) and the
video completion boost. This module adds what a viewer DOES while scrolling:

  positive   long dwell (>= min_dwell_ms; extra at strong_dwell_ms), tap,
             save (PostSave), share (PostShare)
  negative   quick scroll-past: a `skip` event shorter than quick_skip_ms

Each interaction is weighted, time-decayed (`half_life_days`, config) and
summed per AUTHOR and per CATEGORY over `window_days`:

    points = clamp(score * points_per_unit, min_points, max_points)

Points are ADDITIVE (like author affinity): they re-order, never hide a post.
Authors with an active "show fewer" row get nothing (show fewer wins).

NOT BUILT (no data source yet): replay (needs a client replay event) and
profile-visit-after-view (needs a client "opened author profile from post"
event). Both are a one-line addition here once the client sends them.

`score_events` is pure (no Django) and unit-tested; `load_behaviour_signals`
does the queries; `signal_expression` turns the result into one SQL CASE.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Dict, Iterable, Tuple

DEFAULT_SIGNALS = {
    "enabled": True,  # env FEED_SIGNALS_ENABLED=0 switches it off
    "window_days": 14,
    "half_life_days": 7.0,  # an interaction loses half its weight every N days (<= 0 = no decay)
    "min_dwell_ms": 3000,  # dwell at least this long = positive
    "strong_dwell_ms": 10000,  # ... and this long = extra positive
    "quick_skip_ms": 1000,  # a skip at most this long = scrolled straight past
    "weights": {
        "dwell": 1.0, "dwell_strong": 1.0, "tap": 0.5,
        "save": 3.0, "share": 4.0, "skip": -0.7,
    },
    "points_per_unit": 2.0,
    "max_points": 12.0,
    "min_points": -8.0,
    "category_scale": 0.5,  # category points = author-style points * this (a broader signal)
    "max_authors": 40,
    "max_categories": 6,
    "row_cap": 2000,  # most recent rows read per source
}


def get_config(overrides: dict | None = None) -> dict:
    from django.conf import settings

    cfg = dict(DEFAULT_SIGNALS)
    user_cfg = dict(getattr(settings, "FEED_SIGNALS", None) or {})
    weights = dict(DEFAULT_SIGNALS["weights"])
    weights.update(user_cfg.pop("weights", None) or {})
    cfg.update(user_cfg)
    cfg.update(overrides or {})
    if overrides and "weights" in overrides:
        weights.update(overrides["weights"])
    cfg["weights"] = weights
    return cfg


def decay(weight: float, age_days: float, half_life_days) -> float:
    try:
        hl = float(half_life_days)
    except (TypeError, ValueError):
        return weight
    if hl <= 0 or age_days <= 0:
        return weight
    return weight * 0.5 ** (age_days / hl)


def clamp_points(score: float, cfg: dict, scale: float = 1.0) -> float:
    pts = score * float(cfg["points_per_unit"]) * scale
    return max(float(cfg["min_points"]) * scale, min(float(cfg["max_points"]) * scale, pts))


def event_weight(event_type: str, dwell_ms: int, cfg: dict) -> float:
    """Raw weight of ONE PostEvent row (0 = not a signal)."""
    w = cfg["weights"]
    if event_type == "dwell":
        if dwell_ms < int(cfg["min_dwell_ms"]):
            return 0.0
        return w["dwell"] + (w["dwell_strong"] if dwell_ms >= int(cfg["strong_dwell_ms"]) else 0.0)
    if event_type == "tap":
        return w["tap"]
    if event_type == "skip":
        return w["skip"] if dwell_ms <= int(cfg["quick_skip_ms"]) else 0.0
    return 0.0


def score_events(rows: Iterable[Tuple[str, str, float, float]], cfg: dict) -> Tuple[Dict[str, float], Dict[str, float]]:
    """rows = (author_id, category, weight, age_days) -> (author_score, category_score)."""
    authors: Dict[str, float] = {}
    cats: Dict[str, float] = {}
    for author_id, category, weight, age_days in rows:
        if not weight:
            continue
        v = decay(weight, age_days, cfg["half_life_days"])
        authors[str(author_id)] = authors.get(str(author_id), 0.0) + v
        if category:
            cats[category] = cats.get(category, 0.0) + v
    return authors, cats


def load_behaviour_signals(user, now=None, exclude_authors=None, cfg: dict | None = None) -> dict:
    """{"authors": {author_id: points}, "categories": {category: points}}.
    Empty dicts when disabled / nothing recorded."""
    from django.utils import timezone

    from . import feed_mix
    from .models import PostEvent, PostSave, PostShare

    cfg = cfg or get_config()
    empty = {"authors": {}, "categories": {}}
    if not cfg["enabled"] or not user or not getattr(user, "pk", None) or int(cfg["window_days"]) <= 0:
        return empty
    now = now or timezone.now()
    since = now - timedelta(days=int(cfg["window_days"]))
    cap = max(0, int(cfg["row_cap"]))
    if exclude_authors is None:
        exclude_authors = set(feed_mix.load_feedback_effective(user, now)["author"])

    def age(ts):
        return (now - ts).total_seconds() / 86400.0

    rows = []
    events = (
        PostEvent.objects.filter(user=user, created_at__gte=since, surface__in=["feed", "explore"])
        .exclude(post__user=user)
        .exclude(event_type="impression")
        .order_by("-created_at")
        .values_list("post__user_id", "post__category", "event_type", "dwell_ms", "created_at")[:cap]
    )
    for author_id, category, etype, dwell_ms, created_at in events:
        rows.append((author_id, category, event_weight(etype, dwell_ms, cfg), age(created_at)))
    saves = (
        PostSave.objects.filter(user=user, created_at__gte=since).exclude(post__user=user)
        .order_by("-created_at").values_list("post__user_id", "post__category", "created_at")[:cap]
    )
    for author_id, category, created_at in saves:
        rows.append((author_id, category, cfg["weights"]["save"], age(created_at)))
    shares = (
        PostShare.objects.filter(user=user, created_at__gte=since).exclude(post__user=user)
        .order_by("-created_at").values_list("post__user_id", "post__category", "created_at")[:cap]
    )
    for author_id, category, created_at in shares:
        rows.append((author_id, category, cfg["weights"]["share"], age(created_at)))

    a_score, c_score = score_events(rows, cfg)
    authors = {
        a: clamp_points(s, cfg) for a, s in a_score.items() if a not in exclude_authors
    }
    authors = {a: p for a, p in authors.items() if abs(p) >= 0.05}
    strongest = sorted(authors.items(), key=lambda kv: (-abs(kv[1]), kv[0]))[: int(cfg["max_authors"])]
    cats = {c: clamp_points(s, cfg, float(cfg["category_scale"])) for c, s in c_score.items()}
    cats = {c: p for c, p in cats.items() if abs(p) >= 0.05}
    cat_top = sorted(cats.items(), key=lambda kv: (-abs(kv[1]), kv[0]))[: int(cfg["max_categories"])]
    return {"authors": dict(strongest), "categories": dict(cat_top)}


def signal_expression(signals: dict):
    """SQL expression (can be negative) = author points + category points, or None."""
    from django.db.models import Case, FloatField, Value, When

    from .feed_mix import coerce_user_pk

    parts = []
    a_whens = []
    for key, pts in (signals.get("authors") or {}).items():
        try:
            a_whens.append(When(user_id=coerce_user_pk(key), then=Value(float(pts))))
        except (ValueError, AttributeError):
            continue
    if a_whens:
        parts.append(Case(*a_whens, default=Value(0.0), output_field=FloatField()))
    c_whens = [When(category=c, then=Value(float(p))) for c, p in (signals.get("categories") or {}).items()]
    if c_whens:
        parts.append(Case(*c_whens, default=Value(0.0), output_field=FloatField()))
    if not parts:
        return None
    total = parts[0]
    for p in parts[1:]:
        total = total + p
    return total
