"""
post/feed_explore.py - T1 Part 3: exploration slots (cold-start + fairness).

WHY: ranking by engagement alone is a rich-get-richer loop - a brand-new
creator (or a brand-new post) has no likes, so it never reaches anybody, so it
never gets likes. This stage reserves a small share of the recommended slots
for posts the ranking would not have surfaced yet:

* NEW-CREATOR pool: public posts of authors who joined recently or have only a
  few posts (`new_creator_days` / `new_creator_max_posts`).
* TEST AUDIENCE: a post's first `test_window_minutes` it is shown to a stable
  `test_audience_pct` % of viewers (hash of viewer + post, so a viewer's answer
  never flips between requests).
* SCALE OR DROP: once the test window is over, the post's measured performance
  (PostEvent impressions vs taps / long dwells + likes / comments / shares)
  decides: enough impressions and a good rate -> "scale" (shown to everyone for
  up to `scale_window_hours`), poor rate -> dropped from the exploration pool
  (it still competes in the normal pools), too few impressions -> keeps testing.
* ORDER by Thompson sampling (Beta(1+positives, 1+misses) draw - uncertain
  posts get a fair chance) or epsilon-greedy (`mode`).
* NEW VIEWERS (young account / follows almost nobody) get a bigger share
  (`new_viewer_share`) because the normal signals are empty for them.

The pure functions (phase, thompson/epsilon ordering, weave, share) don't touch
Django and are unit-tested alone. `build_explore_ids` is the DB part. The
exploration ids are WOVEN INTO the recommended pool (never added as a 4th
source) so pagination, cursors and snapshots keep working unchanged; the set of
woven ids is stored next to the snapshot so the app can label them.
"""
from __future__ import annotations

import hashlib
import random
from datetime import timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

DEFAULT_EXPLORE = {
    "enabled": True,  # env FEED_EXPLORE_ENABLED=0 switches the stage off
    "share": 0.08,  # fraction of the WHOLE page reserved for exploration
    "new_viewer_share": 0.15,  # same, for brand-new viewers (cold start)
    "new_viewer_days": 7,  # account younger than this = new viewer ...
    "new_viewer_max_following": 3,  # ... or following fewer accounts than this
    "new_creator_days": 14,  # author account younger than this = new creator ...
    "new_creator_max_posts": 5,  # ... or with at most this many posts
    "candidate_max_age_days": 7,  # older posts never get exploration slots
    "test_window_minutes": 60,  # first hour: show to a test audience only
    "test_audience_pct": 20,  # % of viewers inside the test audience
    "scale_window_hours": 24,  # a post that passed the test stays boosted this long
    "min_impressions": 20,  # impressions needed before a post is judged
    "scale_min_rate": 0.08,  # positives / impressions needed to scale
    "positive_dwell_ms": 3000,  # a dwell this long counts as positive
    "mode": "thompson",  # "thompson" | "epsilon"
    "epsilon": 0.15,  # epsilon-greedy: chance of a random pick
    "pool_cap": 60,  # max exploration ids per request
    "scan_cap": 300,  # newest candidate posts looked at
}

PHASE_TEST = "test"
PHASE_SCALE = "scale"
PHASE_DROP = "drop"


def get_config(overrides: dict | None = None) -> dict:
    from django.conf import settings

    cfg = dict(DEFAULT_EXPLORE)
    cfg.update(getattr(settings, "FEED_EXPLORE", None) or {})
    cfg.update(overrides or {})
    return cfg


# --------------------------------------------------------------------------
# Pure logic
# --------------------------------------------------------------------------
def explore_share(cfg: dict, new_viewer: bool) -> float:
    """Fraction of the page reserved for exploration (0 when disabled)."""
    if not cfg.get("enabled"):
        return 0.0
    try:
        value = float(cfg["new_viewer_share"] if new_viewer else cfg["share"])
    except (TypeError, ValueError):
        return 0.0
    return min(0.5, max(0.0, value))


def in_test_audience(viewer_id, post_id, pct: float) -> bool:
    """Stable per (viewer, post): the same viewer is always in / out for a post."""
    if pct >= 100:
        return True
    if pct <= 0:
        return False
    h = int(hashlib.md5(f"{viewer_id}:{post_id}".encode()).hexdigest()[:8], 16) % 100
    return h < pct


def phase(age_minutes: float, impressions: int, positives: int, cfg: dict) -> str:
    """test | scale | drop for one candidate post."""
    if age_minutes < float(cfg["test_window_minutes"]):
        return PHASE_TEST
    if age_minutes > float(cfg["scale_window_hours"]) * 60.0:
        return PHASE_DROP
    if impressions < int(cfg["min_impressions"]):
        return PHASE_TEST  # not enough evidence yet - keep testing, don't judge
    rate = positives / impressions if impressions else 0.0
    return PHASE_SCALE if rate >= float(cfg["scale_min_rate"]) else PHASE_DROP


def thompson_draw(impressions: int, positives: int, rng: random.Random) -> float:
    positives = max(0, min(positives, impressions))
    return rng.betavariate(1 + positives, 1 + impressions - positives)


def epsilon_draw(impressions: int, positives: int, rng: random.Random, epsilon: float) -> float:
    """Mean rate (exploit) most of the time, a random number (explore) with
    probability epsilon. Unseen posts get the optimistic prior 0.5."""
    if rng.random() < epsilon:
        return rng.random()
    return (positives / impressions) if impressions > 0 else 0.5


def order_candidates(
    stats: Sequence[Tuple[str, int, int]], cfg: dict, rng: random.Random
) -> List[str]:
    """[(post_id, impressions, positives)] -> post ids, best draw first."""
    scored = []
    for pid, imp, pos in stats:
        if cfg.get("mode") == "epsilon":
            s = epsilon_draw(imp, pos, rng, float(cfg["epsilon"]))
        else:
            s = thompson_draw(imp, pos, rng)
        scored.append((s, str(pid), pid))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [pid for _, _, pid in scored]


def weave(main_ids: Sequence, explore_ids: Sequence, fraction: float) -> Tuple[list, list]:
    """Spread `explore_ids` through `main_ids` so that ~`fraction` of the result
    is exploration, the first one at index 1 (so it is on page 1). Ids in both
    lists keep their exploration slot and vanish from the main flow (no
    duplicates). Returns (merged, woven_explore_ids). Never drops a main id."""
    explore = list(dict.fromkeys(explore_ids))
    if fraction <= 0 or not explore:
        return list(main_ids), []
    fraction = min(0.5, fraction)
    ex_set = set(explore)
    main = [m for m in main_ids if m not in ex_set]
    out: list = []
    woven: list = []
    credit = 0.6
    mi = ei = 0
    while mi < len(main) or ei < len(explore):
        credit += fraction
        if ei < len(explore) and (credit >= 1.0 or mi >= len(main)):
            out.append(explore[ei])
            woven.append(explore[ei])
            ei += 1
            credit = max(0.0, credit - 1.0)
        elif mi < len(main):
            out.append(main[mi])
            mi += 1
        else:  # pragma: no cover - loop guard
            break
    return out, woven


# --------------------------------------------------------------------------
# DB part
# --------------------------------------------------------------------------
def is_new_viewer(user, following_count: int, now, cfg: dict) -> bool:
    joined = getattr(user, "date_joined", None)
    young = bool(joined) and (now - joined) <= timedelta(days=int(cfg["new_viewer_days"]))
    return young or following_count < int(cfg["new_viewer_max_following"])


def load_post_stats(post_ids: Iterable, cfg: dict, now) -> Dict[str, Tuple[int, int]]:
    """{post_id: (impressions, positives)}. Impressions + taps + long dwells
    come from PostEvent (feed / explore surfaces); likes / comments / shares
    counters add positives (capped at the impressions, so an old viral post
    can't look better than its exposure justifies)."""
    from django.db.models import Count, Q

    from .models import Post, PostEvent

    ids = list(post_ids)
    if not ids:
        return {}
    rows = (
        PostEvent.objects.filter(
            post_id__in=ids, surface__in=["feed", "explore"],
            created_at__gte=now - timedelta(days=int(cfg["candidate_max_age_days"])),
        )
        .values("post_id")
        .annotate(
            imp=Count("id", filter=Q(event_type="impression")),
            pos=Count(
                "id",
                filter=Q(event_type="tap") | Q(event_type="dwell", dwell_ms__gte=int(cfg["positive_dwell_ms"])),
            ),
        )
    )
    ev = {r["post_id"]: (r["imp"], r["pos"]) for r in rows}
    counters = {
        pid: lk + cm + sh
        for pid, lk, cm, sh in Post.objects.filter(id__in=ids).values_list(
            "id", "likes_count", "comments_count", "shares_count"
        )
    }
    out = {}
    for pid in ids:
        imp, pos = ev.get(pid, (0, 0))
        if imp > 0:
            pos = min(imp, pos + counters.get(pid, 0))
        out[pid] = (imp, pos)
    return out


def build_explore_ids(
    user, base_qs, following_ids: set, exclude_ids: Set, cfg: dict, now, rng: Optional[random.Random] = None
) -> Tuple[list, Dict[str, str]]:
    """Ordered exploration post ids (<= pool_cap) + {id: phase}.

    `base_qs` is the shared "safe to show" queryset (blocked / muted / hidden /
    unapproved already removed), so nothing here can leak a blocked author.
    `exclude_ids` = ids already placed in the following / trending pools."""
    from django.db.models import Count, Q

    if not cfg.get("enabled") or explore_share(cfg, False) <= 0 and explore_share(cfg, True) <= 0:
        return [], {}
    rng = rng or random.Random()
    horizon = now - timedelta(days=int(cfg["candidate_max_age_days"]))
    new_since = now - timedelta(days=int(cfg["new_creator_days"]))
    qs = (
        base_qs.filter(visibility="public", user__is_private=False, created_at__gte=horizon)
        .exclude(user_id__in=following_ids)
        .exclude(id__in=list(exclude_ids)[:2000])
        .annotate(_author_posts=Count("user__posts", filter=Q(user__posts__is_deleted=False)))
        .filter(Q(user__date_joined__gte=new_since) | Q(_author_posts__lte=int(cfg["new_creator_max_posts"])))
        .order_by("-created_at", "-id")
        .values_list("id", "user_id", "created_at")[: int(cfg["scan_cap"])]
    )
    rows = list(qs)
    if not rows:
        return [], {}
    stats = load_post_stats([r[0] for r in rows], cfg, now)
    phases: Dict[str, str] = {}
    keep: List[Tuple[str, int, int]] = []
    author_of: Dict = {}
    for pid, author_id, created_at in rows:
        imp, pos = stats.get(pid, (0, 0))
        ph = phase((now - created_at).total_seconds() / 60.0, imp, pos, cfg)
        if ph == PHASE_DROP:
            continue
        if ph == PHASE_TEST and not in_test_audience(user.pk, pid, float(cfg["test_audience_pct"])):
            continue
        phases[str(pid)] = ph
        keep.append((pid, imp, pos))
        author_of[pid] = author_id
    ordered = order_candidates(keep, cfg, rng)
    # one exploration post per author per request: the point is to try MANY creators
    seen_authors: Set = set()
    result = []
    for pid in ordered:
        a = author_of[pid]
        if a in seen_authors:
            continue
        seen_authors.add(a)
        result.append(pid)
        if len(result) >= int(cfg["pool_cap"]):
            break
    return result, {str(p): phases[str(p)] for p in result}
