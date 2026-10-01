"""
post/feed_mix.py — Discovery mix for the home feed (Instagram-style).

PROBLEM (before): `HomeFeedView` returned ONLY posts from accounts the
caller follows. The public "discovery" posts showed up only as a fallback
when the caller followed nobody / their follows had no posts. So a user who
follows even one active account never saw anything new.

NOW: every page of `GET /post/feed/` is a blend of three sources —

    following    posts from accounts the caller follows      (default 60%)
    recommended  personalised posts from accounts they don't (default 30%)
                 follow: interest categories, categories they
                 reacted to lately, friends-of-following, quality
    trending     what is hot platform-wide right now         (default 10%)

Ratios come from `settings.FEED_MIX_RATIOS` (see settings.py) so product can
tune 60/30/10 without touching code.

DESIGN NOTES
------------
* The three pools are built as *ordered id lists* (capped), never sliced with
  per-page queries. A page is then a pure function of
  (pool sizes, ratios, page, page_size) -> which ids. That makes paging
  deterministic: no post repeats on a later page, and if one pool runs dry
  (e.g. user follows nobody, or follows have no posts) its unused slots are
  handed to the other pools automatically. A brand-new user therefore gets a
  100% recommended/trending feed, an old user with a big following gets ~60%
  following, and nobody ever sees an empty feed.
* The pools are disjoint by construction: following vs. everyone-else is a
  hard split, and `recommended` excludes the trending pool's ids.
* Pure functions (`compute_quotas`, `allocate_page`, `interleave`) do not
  import Django, so they are unit-testable in isolation
  (`post/tests_feed_mix.py`).
* SEEN LOGIC: posts the caller already saw (`PostView` rows, incl. the
  batch `POST /post/feed/seen/` ones) in the last `FEED_SEEN_LIMITS["window_days"]` days are
  handled while the pools are built — i.e. BEFORE the pool caps apply:
    - recommended / trending: seen posts are excluded completely;
    - following: seen posts are NOT removed, only pushed down
      (`-is_recent, is_seen, -score`), so an old seen post from a followed
      account can still show up at the very end;
    - if an excluded recommended/trending pool ends up tiny
      (< `FEED_SEEN_LIMITS["fill_min"]`), it is topped up with seen posts as a last
      resort, so the feed never goes empty.
* PAGING STABILITY: the feed is stateless (pools are rebuilt per request),
  so seen posts that get marked *while the user scrolls* would shrink the
  pools and shift page 2/3. `HomeFeedView` therefore fixes a `seen_cutoff`
  timestamp on page 1, forwards it in `next`/`previous`, and only PostView
  rows with `viewed_at <= seen_cutoff` count as seen for that session
  (`get_seen_post_ids(..., until=cutoff)`). Everything seen after the cutoff
  only affects the NEXT session's page 1.
* SHOW FEWER (negative signal, "Feed feedback controls" Part 2): the
  caller's `FeedFeedback` rows (category | hashtag | author) become MINUS
  points in the recommended + trending scores - the mirror image of the
  +15 `UserInterest` bonus. Weights decay with a slow half-life (default 30
  days) at read time. The FOLLOWING pool is never touched: following someone
  is an explicit choice, "show fewer" only shapes discovery. Like every other
  signal it is additive - it pushes a post down, it never drops it.
* WATCH TIME (video ranking, "Part 1" of the watch-time / author-affinity
  task): the video boost used to be `video_completion_rate * 30` - a plain
  average, so ONE viewer who finished a video gave it the full +30, and
  finishing a 5 s loop counted like finishing a 10 min lesson. It is now
  `confidence * (completion_rate * 30 + min(avg_watch_s, 60) / 60 * 10)` with
  `confidence = n / (n + 5)` (n = viewers with watch data, stored in
  `Post.video_watch_count`). Unproven videos earn nothing, well-watched ones
  approach +40; still additive, never demotes. Tunable / switch-off-able via
  `settings.FEED_WATCH_TIME` (`enabled=False` restores the old formula).
  Shared by Home (all three pools), Explore and the "why" trending rank
  because they all get it from `views._video_and_velocity_boost`.
* AUTHOR AFFINITY ("Part 2" of the watch-time / author-affinity task): authors
  the caller likes / comments on a lot rank higher. Likes weigh 1 (a "wrong"
  reaction doesn't count), comments 3, each decayed with a 14-day half-life
  over a 30-day window; `points = min(20, score * 2)` per author (top 50
  authors). Added to the score of ALL THREE pools - in following it only
  re-orders posts INSIDE the existing `-is_recent, is_seen` bands, it never
  lifts an old/seen post over a recent/unseen one. An author with an active
  "show fewer" (FeedFeedback) row gets no affinity at all - show fewer wins.
  `settings.FEED_AUTHOR_AFFINITY`; shared with post/feed_explain.py.
* WHY AM I SEEING THIS: the signal points and loaders below (`POINTS_*`,
  `load_taste_signals`, `trending_rank`) are shared with `post/feed_explain.py`,
  which reports the same signals as human-readable reasons instead of one
  summed score - so the explanation cannot drift from the ranking.
* Response shape of the view is unchanged (`count/next/previous/results`),
  plus one additive per-post field: `feed_source`
  ("following" | "recommended" | "trending") so the app can label cards.
"""
from __future__ import annotations

import json
import math
from datetime import timedelta
from typing import Dict, List, NamedTuple, Sequence, Tuple

SOURCE_FOLLOWING = "following"
SOURCE_RECOMMENDED = "recommended"
SOURCE_TRENDING = "trending"
SOURCES: Tuple[str, ...] = (SOURCE_FOLLOWING, SOURCE_RECOMMENDED, SOURCE_TRENDING)

DEFAULT_RATIOS: Dict[str, float] = {
    SOURCE_FOLLOWING: 0.6,
    SOURCE_RECOMMENDED: 0.3,
    SOURCE_TRENDING: 0.1,
}
DEFAULT_LIMITS = {
    # Max ids fetched per pool per request. Bounds memory/latency; deep
    # pages beyond this simply end the feed (same idea as Instagram's
    # "you're all caught up").
    "following_pool_cap": 600,
    "recommended_pool_cap": 400,
    "trending_pool_cap": 100,
    "trending_window_days": 7,
    "recommended_window_days": 30,
}
# Seen-post handling has its own settings block (`settings.FEED_SEEN_LIMITS`).
DEFAULT_SEEN_LIMITS = {
    "enabled": True,  # master switch: False -> the feed ignores "seen" entirely
    "window_days": 30,  # look back this many days (<= 0 also switches it off)
    "cap": 2000,  # max most-recent seen ids loaded per request (<= 0 also off)
    # If recommended/trending has fewer than this many posts left AFTER
    # excluding seen ones, top it up with seen posts (last resort).
    "fill_min": 20,
    # Paging stability: a `seen_cutoff` older than this (a stale/cached
    # `next` link from a finished session) is ignored, a fresh one is used.
    "cutoff_max_age_minutes": 180,
}

# Ranking points of the recommended pool (shared with post/feed_explain.py).
POINTS_INTEREST = 15.0  # category the user explicitly picked (UserInterest)
POINTS_TASTE = 10.0  # category the user reacted to in the last 30 days
POINTS_FRIEND_OF_FOLLOW = 12.0  # author is followed by someone the caller follows

# Watch-time ranking (videos) - settings.FEED_WATCH_TIME is merged over this.
DEFAULT_WATCH_TIME = {
    "enabled": True,  # False -> legacy formula: video_completion_rate * completion_points, no confidence
    "completion_points": 30.0,  # points at completion rate 1.0 with full confidence (as before)
    "watch_seconds_points": 10.0,  # extra points once the average watch reaches `watch_seconds_cap`
    "watch_seconds_cap": 60.0,  # average watch time (s) that earns the full extra points
    # Evidence needed: confidence = n / (n + k). k = 5 -> 1 viewer 17%, 5 viewers 50%, 45 viewers 90%.
    "confidence_k": 5.0,
}

# Author affinity - settings.FEED_AUTHOR_AFFINITY is merged over this.
DEFAULT_AFFINITY = {
    "enabled": True,  # False -> no affinity points anywhere
    "window_days": 30,  # look-back over the caller's likes / comments
    "half_life_days": 14.0,  # an interaction loses half its weight every N days (<= 0 = no decay)
    "like_weight": 1.0,  # ("wrong" reactions never count)
    "comment_weight": 3.0,  # top-level comments and replies; deleted / hidden ones don't count
    "points_per_unit": 2.0,  # points per unit of (decayed) weight ...
    "max_points": 20.0,  # ... capped here: below the 25 of one "show fewer" tap, near the +15/+12 signals
    "max_authors": 50,  # strongest N authors become SQL CASE branches
    "row_cap": 1000,  # most recent likes / comments read per request (each)
}

# "Show fewer like this" (FeedFeedback) - settings.FEED_FEEDBACK is merged over this.
FEEDBACK_KINDS: Tuple[str, ...] = ("category", "hashtag", "author")
DEFAULT_FEEDBACK = {
    "enabled": True,  # master switch: False -> feedback rows are kept but ignored in ranking
    # Weight halves every N days ("slow decay"). <= 0 means PERMANENT (no decay).
    "half_life_days": 30.0,
    "step": 1.0,  # weight added by one "show fewer" tap
    "max_weight": 3.0,  # repeated taps stop adding beyond this
    "min_effective": 0.05,  # decayed below this -> ignored (and pruned on the next write)
    # Minus points at weight 1.0 (they scale linearly with the weight). Category
    # mirrors UserInterest's +15 (one tap cancels the interest bonus); an
    # author is the most specific signal, a single hashtag the weakest.
    "points": {"category": 15.0, "hashtag": 8.0, "author": 25.0},
    # Max feedback rows per kind turned into SQL CASE branches (strongest first).
    "load_cap": {"category": 30, "hashtag": 60, "author": 60},
}


# --------------------------------------------------------------------------
# Pure logic (no Django)
# --------------------------------------------------------------------------
def normalize_ratios(raw: Dict[str, float] | None) -> Dict[str, float]:
    """Validate/normalise a ratio dict so it always sums to 1.0.

    Unknown keys ignored, negative/garbage values treated as 0. If nothing
    usable is left, fall back to the 60/30/10 default."""
    ratios: Dict[str, float] = {}
    for src in SOURCES:
        try:
            value = float((raw or {}).get(src, 0.0))
        except (TypeError, ValueError):
            value = 0.0
        ratios[src] = value if value > 0 else 0.0
    total = sum(ratios.values())
    if total <= 0:
        return dict(DEFAULT_RATIOS)
    return {src: value / total for src, value in ratios.items()}


def compute_quotas(page_size: int, ratios: Dict[str, float]) -> Dict[str, int]:
    """Split `page_size` slots between the sources (largest-remainder
    method, so 20 slots @ 60/30/10 -> 12/6/2 and the total is always exactly
    `page_size`). A source with ratio 0 never gets a slot."""
    ratios = normalize_ratios(ratios)
    exact = {src: page_size * ratios[src] for src in SOURCES}
    quotas = {src: int(exact[src]) for src in SOURCES}
    remainder = page_size - sum(quotas.values())
    # hand leftover slots to the biggest fractional parts (ties -> SOURCES order)
    order = sorted(
        (s for s in SOURCES if ratios[s] > 0),
        key=lambda s: (-(exact[s] - quotas[s]), SOURCES.index(s)),
    )
    i = 0
    while remainder > 0 and order:
        quotas[order[i % len(order)]] += 1
        remainder -= 1
        i += 1
    return quotas


def _take_step(
    consumed: Dict[str, int],
    page_size: int,
    pool_sizes: Dict[str, int],
    ratios: Dict[str, float],
    base_quotas: Dict[str, int],
) -> Dict[str, int]:
    """ONE page worth of allocation: how many items each pool gives, given
    how many of them were already consumed. Shared by `allocate_page`
    (stateless: replays pages 1..N) and `allocate_next` (cursor: one step
    from stored offsets), so both always agree."""
    take = {src: 0 for src in SOURCES}
    remaining = {src: max(0, pool_sizes.get(src, 0) - consumed[src]) for src in SOURCES}
    slots_left = page_size
    # 1) everyone takes up to their own quota
    for src in SOURCES:
        take[src] = min(base_quotas[src], remaining[src])
        slots_left -= take[src]
    # 2) hand the unused slots to pools that still have stock, biggest
    #    ratio first (following > recommended > trending by default)
    by_priority = sorted(SOURCES, key=lambda s: (-ratios[s], SOURCES.index(s)))
    while slots_left > 0:
        progressed = False
        for src in by_priority:
            if slots_left <= 0:
                break
            if ratios[src] <= 0 and any(
                remaining[s] - take[s] > 0 for s in SOURCES if ratios[s] > 0
            ):
                # zero-ratio source only used as a last resort
                continue
            if remaining[src] - take[src] > 0:
                take[src] += 1
                slots_left -= 1
                progressed = True
        if not progressed:
            break
    return take


def allocate_page(
    page: int,
    page_size: int,
    pool_sizes: Dict[str, int],
    ratios: Dict[str, float],
) -> Dict[str, Tuple[int, int]]:
    """Return {source: (start, end)} slice of each pool for `page` (1-based).

    Simulates pages 1..page so the result is fully deterministic and
    stateless: same inputs -> same slices, and slices of consecutive pages
    never overlap. When a pool has fewer items left than its quota, the
    shortfall is redistributed to the other pools (in ratio order), so a
    page is only short when *every* pool is exhausted."""
    page = max(1, int(page))
    ratios = normalize_ratios(ratios)
    base_quotas = compute_quotas(page_size, ratios)
    consumed = {src: 0 for src in SOURCES}
    result: Dict[str, Tuple[int, int]] = {src: (0, 0) for src in SOURCES}

    for current in range(1, page + 1):
        take = _take_step(consumed, page_size, pool_sizes, ratios, base_quotas)
        if current == page:
            for src in SOURCES:
                result[src] = (consumed[src], consumed[src] + take[src])
        for src in SOURCES:
            consumed[src] += take[src]
    return result


def allocate_next(
    consumed: Dict[str, int],
    page_size: int,
    pool_sizes: Dict[str, int],
    ratios: Dict[str, float],
) -> Dict[str, Tuple[int, int]]:
    """Cursor flavour of `allocate_page`: ONE step from per-pool offsets
    (`consumed`, e.g. {"following": 12, ...}) instead of replaying pages
    1..N. Returns {source: (start, end)}; `end` is the offset to store in
    the next cursor. Stepping this from zero offsets gives exactly the
    slices `allocate_page(1), allocate_page(2), ...` would (same
    `_take_step`), but works for any page_size per request."""
    ratios = normalize_ratios(ratios)
    base_quotas = compute_quotas(page_size, ratios)
    start = {src: max(0, int(consumed.get(src, 0) or 0)) for src in SOURCES}
    take = _take_step(start, page_size, pool_sizes, ratios, base_quotas)
    return {src: (start[src], start[src] + take[src]) for src in SOURCES}


def interleave(chunks: Dict[str, Sequence], ratios: Dict[str, float]) -> List[Tuple[str, object]]:
    """Merge per-source lists into ONE list, spread out evenly (smooth
    weighted round-robin) instead of 12 following posts then 6 suggested
    ones — a suggested/trending card lands every few posts, like Instagram.
    Returns [(source, item), ...]."""
    ratios = normalize_ratios(ratios)
    queues = {src: list(chunks.get(src, [])) for src in SOURCES}
    credit = {src: 0.0 for src in SOURCES}
    out: List[Tuple[str, object]] = []
    total = sum(len(q) for q in queues.values())
    while len(out) < total:
        live = [s for s in SOURCES if queues[s]]
        # weights among live sources; if only zero-ratio sources are left,
        # give them equal weight so they still drain.
        weights = {s: ratios[s] for s in live}
        if sum(weights.values()) <= 0:
            weights = {s: 1.0 for s in live}
        wsum = sum(weights.values())
        for s in live:
            credit[s] += weights[s] / wsum
        pick = max(live, key=lambda s: (credit[s], -SOURCES.index(s)))
        credit[pick] -= 1.0
        out.append((pick, queues[pick].pop(0)))
    return out


def affinity_points(score: float, cfg: dict) -> float:
    """Ranking points for an author's summed (decayed) interaction weight."""
    return min(max(0.0, float(cfg["max_points"])), max(0.0, float(score)) * max(0.0, float(cfg["points_per_unit"])))


def watch_confidence(watch_count: float, k: float) -> float:
    """How much to trust a video's watch stats: n / (n + k), 0..1.
    No viewers -> 0. k <= 0 turns the damping off (any viewer = full trust)."""
    n = max(0.0, float(watch_count or 0))
    if n <= 0:
        return 0.0
    if k <= 0:
        return 1.0
    return n / (n + float(k))


def watch_boost_points(completion_rate: float, watch_count: float, avg_watch_seconds: float, cfg: dict) -> float:
    """Ranking points of ONE video's watch data (pure twin of
    `video_watch_boost()`'s SQL expression - keep the two in step):

        confidence * (rate * completion_points + min(avg_s, cap) / cap * watch_seconds_points)

    `enabled=False` -> the legacy `rate * completion_points`."""
    rate = min(1.0, max(0.0, float(completion_rate or 0)))
    if not cfg["enabled"]:
        return rate * float(cfg["completion_points"])
    cap = float(cfg["watch_seconds_cap"])
    seconds_part = 0.0
    if cap > 0:
        seconds_part = min(max(0.0, float(avg_watch_seconds or 0)), cap) / cap * float(cfg["watch_seconds_points"])
    return watch_confidence(watch_count, cfg["confidence_k"]) * (rate * float(cfg["completion_points"]) + seconds_part)


def decay_weight(weight: float, age_days: float, half_life_days: float | None) -> float:
    """Exponential decay of a stored feedback weight: it halves every
    `half_life_days`. `half_life_days` <= 0 / None -> no decay (permanent).
    Never negative, never raises on a negative age (clock skew -> age 0)."""
    weight = float(weight or 0.0)
    if weight <= 0:
        return 0.0
    if not half_life_days or half_life_days <= 0:
        return weight
    return weight * 0.5 ** (max(0.0, float(age_days)) / float(half_life_days))


def bump_weight(effective_weight: float, step: float, max_weight: float) -> float:
    """New stored weight after ONE more "show fewer" tap: the already-decayed
    weight plus `step`, capped at `max_weight`."""
    return min(float(max_weight), max(0.0, float(effective_weight)) + float(step))


def feedback_points(kind: str, effective_weight: float, points: Dict[str, float]) -> float:
    """Minus points (returned POSITIVE; the caller subtracts) for one row."""
    return max(0.0, float(points.get(kind, 0.0))) * max(0.0, float(effective_weight))


def feedback_horizon_days(cfg: dict) -> float | None:
    """Rows not touched for longer than this can no longer reach
    `min_effective`, even from `max_weight` -> safe to skip/prune. None when
    there is no decay (permanent) or nothing can ever be ignored."""
    half_life = float(cfg.get("half_life_days") or 0)
    if half_life <= 0:
        return None
    max_weight, min_eff = float(cfg["max_weight"]), float(cfg["min_effective"])
    if min_eff <= 0:
        return None
    ratio = max_weight / min_eff
    return half_life * max(1.0, math.log2(ratio)) if ratio > 1 else half_life


# --------------------------------------------------------------------------
# DB-backed pool builders (Django imported lazily)
# --------------------------------------------------------------------------
def get_ratios() -> Dict[str, float]:
    from django.conf import settings

    return normalize_ratios(getattr(settings, "FEED_MIX_RATIOS", None) or DEFAULT_RATIOS)


def get_seen_limits() -> dict:
    """`settings.FEED_SEEN_LIMITS` merged over DEFAULT_SEEN_LIMITS."""
    from django.conf import settings

    limits = dict(DEFAULT_SEEN_LIMITS)
    limits.update(getattr(settings, "FEED_SEEN_LIMITS", None) or {})
    return limits


def get_limits() -> Dict[str, int]:
    from django.conf import settings

    limits = dict(DEFAULT_LIMITS)
    limits.update(getattr(settings, "FEED_MIX_LIMITS", None) or {})
    return limits


def get_affinity_config() -> dict:
    """`settings.FEED_AUTHOR_AFFINITY` merged over DEFAULT_AFFINITY."""
    from django.conf import settings

    cfg = dict(DEFAULT_AFFINITY)
    cfg.update(getattr(settings, "FEED_AUTHOR_AFFINITY", None) or {})
    return cfg


def load_author_affinity(user, now=None, exclude_authors=None) -> Dict[str, Dict[str, float]]:
    """{author_id (str): {"points", "score", "likes", "comments"}} for the
    authors `user` likes / comments on, strongest first (at most `max_authors`).

    `score` = sum of decayed interaction weights, `points` = what ranking adds,
    `likes` / `comments` = raw counts inside the window (for the "why" text).
    Own posts don't count. `exclude_authors` (ids as str) are skipped - the
    ranking passes the authors with an active "show fewer" row; None = load
    those here. Disabled / anonymous -> {}."""
    from django.utils import timezone

    from .models import PostComment, PostLike

    cfg = get_affinity_config()
    if not cfg["enabled"] or not user or not getattr(user, "pk", None) or int(cfg["window_days"]) <= 0:
        return {}
    now = now or timezone.now()
    since = now - timedelta(days=int(cfg["window_days"]))
    cap = max(0, int(cfg["row_cap"]))
    half_life = cfg["half_life_days"]
    if exclude_authors is None:
        exclude_authors = set(load_feedback_effective(user, now)["author"])

    likes = (
        PostLike.objects.filter(user=user, created_at__gte=since)
        .exclude(reaction_type="wrong")
        .exclude(post__user=user)
        .order_by("-created_at")
        .values_list("post__user_id", "created_at")[:cap]
    )
    comments = (
        PostComment.objects.filter(user=user, created_at__gte=since, is_deleted=False, is_hidden=False)
        .exclude(post__user=user)
        .order_by("-created_at")
        .values_list("post__user_id", "created_at")[:cap]
    )
    acc: Dict[str, Dict[str, float]] = {}
    for rows, weight, counter in ((likes, cfg["like_weight"], "likes"), (comments, cfg["comment_weight"], "comments")):
        for author_id, created_at in rows:
            key = str(author_id)
            if key in exclude_authors:
                continue
            age_days = (now - created_at).total_seconds() / 86400.0
            entry = acc.setdefault(key, {"points": 0.0, "score": 0.0, "likes": 0, "comments": 0})
            entry["score"] += decay_weight(weight, age_days, half_life)
            entry[counter] += 1
    for entry in acc.values():
        entry["points"] = affinity_points(entry["score"], cfg)
    strongest = sorted(acc.items(), key=lambda kv: (-kv[1]["points"], -kv[1]["score"], kv[0]))
    strongest = [(k, v) for k, v in strongest if v["points"] > 0][: max(0, int(cfg["max_authors"]))]
    return dict(strongest)


def affinity_expression(affinity: Dict[str, Dict[str, float]]):
    """SQL expression (>= 0) with the author-affinity points of a post, or
    None when there is nothing to add."""
    import uuid

    from django.db.models import Case, FloatField, Value, When

    whens = []
    for key, entry in affinity.items():
        try:
            whens.append(When(user_id=uuid.UUID(str(key)), then=Value(float(entry["points"]))))
        except (ValueError, AttributeError):
            continue
    if not whens:
        return None
    return Case(*whens, default=Value(0.0), output_field=FloatField())


def get_watch_time_config() -> dict:
    """`settings.FEED_WATCH_TIME` merged over DEFAULT_WATCH_TIME."""
    from django.conf import settings

    cfg = dict(DEFAULT_WATCH_TIME)
    cfg.update(getattr(settings, "FEED_WATCH_TIME", None) or {})
    return cfg


def video_watch_boost():
    """SQL expression (>= 0) with the watch-time points of a post - 0.0 for
    every non-video post and for videos nobody has watched yet. This is the
    `video_boost` term of every score (Home pools, Explore); see the module
    docstring ("WATCH TIME") for the formula and `watch_boost_points` for its
    pure twin used by the tests."""
    from django.db.models import Case, ExpressionWrapper, F, FloatField, Value, When
    from django.db.models.functions import Least

    cfg = get_watch_time_config()
    completion = F("video_completion_rate") * float(cfg["completion_points"])
    if not cfg["enabled"]:
        return Case(
            When(post_type="video", then=ExpressionWrapper(completion, output_field=FloatField())),
            default=Value(0.0), output_field=FloatField(),
        )

    cap = float(cfg["watch_seconds_cap"])
    body = completion
    if cap > 0:
        seconds = Least(F("video_avg_watch_seconds"), Value(cap, output_field=FloatField()), output_field=FloatField())
        body = body + seconds * (float(cfg["watch_seconds_points"]) / cap)
    k = float(cfg["confidence_k"])
    count = F("video_watch_count") * 1.0  # *1.0: never integer-divide on SQLite/PostgreSQL
    confidence = (count / (count + k)) if k > 0 else Value(1.0, output_field=FloatField())
    return Case(
        When(post_type="video", video_watch_count__gt=0,
             then=ExpressionWrapper(body * confidence, output_field=FloatField())),
        default=Value(0.0), output_field=FloatField(),
    )


def get_feedback_config() -> dict:
    """`settings.FEED_FEEDBACK` merged over DEFAULT_FEEDBACK (nested `points` /
    `load_cap` dicts are merged key by key, so a partial override is fine)."""
    from django.conf import settings

    cfg = dict(DEFAULT_FEEDBACK)
    cfg["points"] = dict(DEFAULT_FEEDBACK["points"])
    cfg["load_cap"] = dict(DEFAULT_FEEDBACK["load_cap"])
    for key, value in (getattr(settings, "FEED_FEEDBACK", None) or {}).items():
        if key in ("points", "load_cap") and isinstance(value, dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


def load_feedback_effective(user, now=None) -> Dict[str, Dict[str, float]]:
    """The caller's "show fewer" rows as {kind: {key: effective_weight}}.

    Weights are decayed to `now` (see `decay_weight`), rows that decayed below
    `min_effective` are dropped, and each kind keeps its strongest
    `load_cap[kind]` rows (dict order = strongest first). Nothing is written.
    Feedback switched off / anonymous user -> empty dicts."""
    from django.utils import timezone

    from .models import FeedFeedback

    cfg = get_feedback_config()
    out: Dict[str, Dict[str, float]] = {kind: {} for kind in FEEDBACK_KINDS}
    if not cfg["enabled"] or not user or not getattr(user, "pk", None):
        return out
    now = now or timezone.now()

    rows = FeedFeedback.objects.filter(user=user)
    horizon = feedback_horizon_days(cfg)
    if horizon is not None:
        rows = rows.filter(updated_at__gte=now - timedelta(days=horizon))

    half_life = cfg["half_life_days"]
    max_weight = float(cfg["max_weight"])
    min_effective = float(cfg["min_effective"])
    for kind, key, weight, updated_at in rows.values_list("kind", "key", "weight", "updated_at"):
        if kind not in out:
            continue
        age_days = (now - updated_at).total_seconds() / 86400.0
        effective = min(max_weight, decay_weight(weight, age_days, half_life))
        if effective >= min_effective:
            out[kind][key] = effective

    for kind in FEEDBACK_KINDS:
        cap = max(0, int(cfg["load_cap"].get(kind, 0)))
        strongest = sorted(out[kind].items(), key=lambda kv: (-kv[1], kv[0]))[:cap]
        out[kind] = dict(strongest)
    return out


def load_feedback_penalties(user, now=None) -> Dict[str, Dict[str, float]]:
    """{kind: {key: minus_points}} - `load_feedback_effective` x the per-kind
    points from settings. Values are POSITIVE numbers that get subtracted."""
    points = get_feedback_config()["points"]
    effective = load_feedback_effective(user, now)
    return {
        kind: {key: feedback_points(kind, weight, points) for key, weight in rows.items()}
        for kind, rows in effective.items()
    }


def hashtag_match_q(tag: str):
    """Q: `Post.hashtags` (a JSON list of lower-case strings) contains `tag`.

    `hashtags__contains` is PostgreSQL-only (the JSON containment operator);
    ranking must also run on the SQLite dev/test DB, so this matches the
    JSON-encoded, quoted tag as text instead - `"py"` never matches inside
    `"python"`. Both the backslash-u escaped (SQLite) and the raw unicode
    (PostgreSQL jsonb::text) spellings are tried for non-ASCII tags."""
    from django.db.models import Q

    q = Q()
    for needle in {json.dumps(tag), json.dumps(tag, ensure_ascii=False)}:
        q |= Q(hashtags__icontains=needle)
    return q


def penalty_expression(penalties: Dict[str, Dict[str, float]], category_filtered: bool = False):
    """SQL expression (>= 0) with the total minus points of a post, or None
    when there is nothing to subtract (so callers skip it entirely).

    category + hashtag + author penalties add up; within `hashtag` only the
    STRONGEST matching tag counts (CASE takes the first hit, rows are strongest
    first) so a post with ten disliked tags is not punished ten times.
    `category_filtered`: a `?category=` request makes the category constant for
    every post, so its penalty is skipped (same as the interest bonus)."""
    import uuid

    from django.db.models import Case, FloatField, Value, When

    def _case(whens):
        return Case(*whens, default=Value(0.0), output_field=FloatField())

    parts = []
    categories = {} if category_filtered else penalties.get("category", {})
    if categories:
        parts.append(_case([When(category=key, then=Value(pts)) for key, pts in categories.items()]))
    if penalties.get("hashtag"):
        parts.append(_case([When(hashtag_match_q(key), then=Value(pts)) for key, pts in penalties["hashtag"].items()]))
    author_whens = []
    for key, pts in (penalties.get("author") or {}).items():
        try:
            author_whens.append(When(user_id=uuid.UUID(str(key)), then=Value(pts)))
        except (ValueError, AttributeError):
            continue  # garbage key: ignore rather than break the whole feed
    if author_whens:
        parts.append(_case(author_whens))
    if not parts:
        return None
    total = parts[0]
    for part in parts[1:]:
        total = total + part
    return total


class TasteSignals(NamedTuple):
    """The three additive signals of the recommended pool (see build_pool_ids)."""

    explicit: list  # categories picked as interests            (+POINTS_INTEREST)
    liked_categories: list  # categories reacted to lately        (+POINTS_TASTE)
    fof_authors: set  # authors followed by someone I follow      (+POINTS_FRIEND_OF_FOLLOW)


def load_taste_signals(request_user, following_ids: set, now=None) -> TasteSignals:
    """Load the recommended-pool signals. Used by `build_pool_ids` (ranking)
    AND `post/feed_explain.py` ("why am I seeing this") - one source of truth."""
    from django.utils import timezone

    from .models import PostLike, UserInterest
    from user_profile.models import Follow

    now = now or timezone.now()
    explicit = list(UserInterest.objects.filter(user=request_user).values_list("category", flat=True))
    liked_categories = list(
        PostLike.objects.filter(user=request_user, created_at__gte=now - timedelta(days=30))
        .exclude(post__category__isnull=True)
        .values_list("post__category", flat=True)
        .distinct()[:10]
    )
    fof_authors: set = set()
    if following_ids:
        fof_authors = set(
            Follow.objects.filter(follower_id__in=list(following_ids)[:300], status=Follow.Status.ACCEPTED)
            .exclude(following_id__in=following_ids)
            .exclude(following=request_user)
            .values_list("following_id", flat=True)[:500]
        )
    return TasteSignals(explicit, liked_categories, fof_authors)


def _engagement_expression():
    from django.db.models import F

    return F("likes_count") * 3.0 + F("comments_count") * 5.0 + F("shares_count") * 10.0 + F("views_count") * 0.1


def _discovery_querysets(base_qs, following_ids, now, limits, video_boost, velocity_boost, penalty=None, bonus=None):
    """(discovery_qs, trending_qs) - shared by `build_pool_ids` and
    `trending_rank`. Discovery = public posts of public accounts the caller
    does NOT follow (following already has its own slot). Trending adds the
    time window and the score, velocity weighted x2 so "hot right now" beats
    "big but old", minus the caller's "show fewer" penalty."""
    from django.db.models import ExpressionWrapper, FloatField

    discovery_qs = base_qs.filter(visibility="public", user__is_private=False).exclude(user_id__in=following_ids)
    score = _engagement_expression() + video_boost + velocity_boost * 2.0
    if bonus is not None:
        score = score + bonus
    if penalty is not None:
        score = score - penalty
    trending_qs = (
        discovery_qs.filter(created_at__gte=now - timedelta(days=limits["trending_window_days"]))
        .annotate(score=ExpressionWrapper(score, output_field=FloatField()))
        .order_by("-score", "-created_at", "-id")
    )
    return discovery_qs, trending_qs


def trending_rank(request_user, base_qs, following_ids: set, video_and_velocity_boost, post, now=None, seen_ids=None):
    """0-based position of `post` in the trending pool's ordering, or None if
    the post is not eligible for trending at all (followed author, private,
    outside the trending window, not in `base_qs`, ...). The post is in the
    trending POOL when the rank is < `trending_pool_cap`. Same querysets,
    score and tie-breaks as `build_pool_ids`; `seen_ids` posts are skipped
    when counting (they are removed from the pool before its cap)."""
    from django.db.models import Q
    from django.utils import timezone

    now = now or timezone.now()
    limits = get_limits()
    hidden = _hidden_author_ids(request_user)
    if hidden:
        base_qs = base_qs.exclude(user_id__in=hidden)
    video_boost, velocity_boost = video_and_velocity_boost()
    penalties = load_feedback_penalties(request_user, now)
    penalty = penalty_expression(penalties)
    bonus = affinity_expression(load_author_affinity(request_user, now, exclude_authors=set(penalties["author"])))
    _, trending_qs = _discovery_querysets(base_qs, following_ids, now, limits, video_boost, velocity_boost, penalty, bonus)

    score = trending_qs.filter(pk=post.pk).values_list("score", flat=True).first()
    if score is None:
        return None
    ahead = trending_qs.filter(
        Q(score__gt=score)
        | Q(score=score, created_at__gt=post.created_at)
        | Q(score=score, created_at=post.created_at, id__gt=post.id)
    )
    if seen_ids:
        ahead = ahead.exclude(id__in=seen_ids)
    return ahead.count()


def _hidden_author_ids(user) -> set:
    """Authors the caller must never see suggestions from: anyone they
    blocked or who blocked them."""
    from user_profile.models import BlockUser

    hidden = set(BlockUser.objects.filter(blocker=user).values_list("blocked_id", flat=True))
    hidden |= set(BlockUser.objects.filter(blocked=user).values_list("blocker_id", flat=True))
    return hidden


def get_seen_post_ids(user, window_days: int | None = None, cap: int | None = None, until=None) -> set:
    """Ids of posts `user` has already seen recently.

    A post counts as seen when the user has a `PostView` row for it — created
    either by opening the post (PostDetailAPIView) or by the feed's batch
    `POST /post/feed/seen/` (`is_counted=False`). Only rows viewed within the
    last `window_days` (default 30) count, and at most `cap` (default 2000)
    ids are returned — the MOST RECENTLY seen ones, so the bound never drops
    the posts the user is most likely to see again.

    `until` (aware datetime, optional) is the paging-stability cutoff: only
    rows with `viewed_at <= until` count, and the look-back window is
    measured from `until` instead of "now" — so the same cutoff always
    yields the same set, no matter what gets seen afterwards.

    Defaults come from `settings.FEED_SEEN_LIMITS` (`window_days`, `cap`).
    `enabled=False`, `window_days <= 0` or `cap <= 0` -> empty set (off).
    """
    from django.utils import timezone

    from .models import PostView

    limits = get_seen_limits()
    if not limits["enabled"]:
        return set()
    window_days = limits["window_days"] if window_days is None else window_days
    cap = limits["cap"] if cap is None else cap
    if not user or not getattr(user, "pk", None) or window_days <= 0 or cap <= 0:
        return set()

    anchor = until or timezone.now()
    qs = PostView.objects.filter(user=user, viewed_at__gte=anchor - timedelta(days=window_days))
    if until is not None:
        qs = qs.filter(viewed_at__lte=until)
    return set(qs.order_by("-viewed_at").values_list("post_id", flat=True)[:cap])


# --------------------------------------------------------------------------
# seen_cutoff (paging stability)
# --------------------------------------------------------------------------
def format_seen_cutoff(cutoff) -> str:
    """aware datetime -> URL-safe UTC string, e.g. 2026-09-29T12:00:00.123456Z
    (no '+' sign, so it survives query-string round trips)."""
    from datetime import timezone as dt_timezone

    return cutoff.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def resolve_seen_cutoff(raw, now=None):
    """Turn the `seen_cutoff` query param into an aware datetime.

    Missing / unparsable / (clearly) in the future / older than
    `cutoff_max_age_minutes` -> a fresh cutoff (`now`), i.e. behave like
    the first page of a new session. Never raises."""
    from datetime import timezone as dt_timezone

    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    now = now or timezone.now()
    if not raw:
        return now
    try:
        parsed = parse_datetime(str(raw).strip().replace(" ", "+"))
    except (ValueError, TypeError):
        parsed = None
    if parsed is None:
        return now
    if timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    max_age = timedelta(minutes=int(get_seen_limits()["cutoff_max_age_minutes"]))
    # small tolerance for clock skew between app servers
    if parsed > now + timedelta(seconds=60) or now - parsed > max_age:
        return now
    return parsed


def _pool_ids(qs, seen_ids: set, cap: int, fill_min: int = 0) -> list:
    """Ordered id list for a discovery pool (recommended / trending).

    1. `qs` minus every seen post, first `cap` ids (qs keeps its own order).
    2. Last resort: if fewer than `fill_min` ids survived, top up with seen
       posts from the same `qs` (same ordering) until `fill_min` — unseen
       ones always stay in front, seen ones only ever go at the tail.
    """
    if not seen_ids:
        return list(qs.values_list("id", flat=True)[:cap])
    ids = list(qs.exclude(id__in=seen_ids).values_list("id", flat=True)[:cap])
    target = min(cap, fill_min)
    if len(ids) < target:
        ids += list(qs.filter(id__in=seen_ids).values_list("id", flat=True)[: target - len(ids)])
    return ids


def build_pool_ids(
    request_user,
    base_qs,
    following_ids: set,
    video_and_velocity_boost,
    category: str | None = None,
    seen_ids: set | None = None,
):
    """Build the three ordered id lists.

    `base_qs` is the shared "safe to show" queryset from HomeFeedView
    (not deleted, approved, not sensitive, no own posts, superseded
    reposts removed, hidden posts / muted accounts removed). `seen_ids` is the
    caller's already-fetched seen set (HomeFeedView passes one computed against
    its `seen_cutoff`); when None it is loaded here with the default window
    (no cutoff). Returns {source: [post_id, ...]}."""
    from django.db.models import Case, ExpressionWrapper, FloatField, IntegerField, Q, Value, When
    from django.utils import timezone

    limits = get_limits()
    now = timezone.now()
    hidden = _hidden_author_ids(request_user)
    video_boost, velocity_boost = video_and_velocity_boost()
    # Seen posts of the last N days — fetched once (by the caller, or here
    # as a fallback), applied to all pools BEFORE their caps (see module
    # docstring, "SEEN LOGIC").
    if seen_ids is None:
        seen_ids = get_seen_post_ids(request_user)
    fill_min = int(get_seen_limits()["fill_min"])

    if category:
        base_qs = base_qs.filter(category=category)
    if hidden:
        base_qs = base_qs.exclude(user_id__in=hidden)

    engagement = _engagement_expression()

    # "Show fewer like this" (FeedFeedback): minus points for the discovery
    # pools only - trending + recommended below. None = nothing to subtract.
    penalties = load_feedback_penalties(request_user, now)
    penalty = penalty_expression(penalties, category_filtered=bool(category))
    # Author affinity (+points for authors the caller likes / comments on),
    # ALL pools; authors with a "show fewer" row are excluded (they win).
    affinity = affinity_expression(load_author_affinity(request_user, now, exclude_authors=set(penalties["author"])))

    # ---- 1) FOLLOWING ------------------------------------------------------
    # NOT touched by "show fewer": following an account is an explicit choice.
    # Author affinity only re-orders INSIDE the (-is_recent, is_seen) bands.
    following_score = engagement + video_boost + velocity_boost
    if affinity is not None:
        following_score = following_score + affinity
    seven_days_ago = now - timedelta(days=7)
    following_qs = (
        base_qs.filter(user_id__in=following_ids, visibility__in=["public", "connections"])
        .annotate(
            is_recent=Case(When(created_at__gte=seven_days_ago, then=1), default=0, output_field=IntegerField()),
            # Seen posts are pushed DOWN, never dropped: followed accounts'
            # older seen posts stay reachable at the end of their band.
            is_seen=(
                Case(When(id__in=seen_ids, then=1), default=0, output_field=IntegerField())
                if seen_ids
                else Value(0, output_field=IntegerField())
            ),
            score=ExpressionWrapper(following_score, output_field=FloatField()),
        )
        .order_by("-is_recent", "is_seen", "-score", "-created_at", "-id")
    )
    following_ids_list = list(following_qs.values_list("id", flat=True)[: limits["following_pool_cap"]]) if following_ids else []

    # ---- 2) TRENDING -------------------------------------------------------
    # Public posts from people the caller does NOT follow (following already
    # has its own slot), from public accounts only. Velocity is weighted x2
    # so "hot right now" beats "big but old". Minus the show-fewer penalty.
    discovery_qs, trending_qs = _discovery_querysets(
        base_qs, following_ids, now, limits, video_boost, velocity_boost, penalty, affinity,
    )
    # Seen posts are excluded before the cap; tiny leftover -> seen top-up.
    trending_ids_list = _pool_ids(trending_qs, seen_ids, limits["trending_pool_cap"], fill_min)

    # ---- 3) RECOMMENDED ----------------------------------------------------
    # Signals (all additive, none can hide a post — same policy as Task 3/G4):
    #   +15 category the user explicitly picked as an interest   POINTS_INTEREST
    #   +10 category the user reacted to in the last 30 days     POINTS_TASTE
    #   +12 author is followed by someone the caller follows     POINTS_FRIEND_OF_FOLLOW
    #   + engagement / video completion / freshness (quality)
    #   - "show fewer" penalty (category / hashtag / author)     FeedFeedback
    #   + author affinity (likes / comments on the author)       up to max_points
    signals = load_taste_signals(request_user, following_ids, now)

    def _bonus(cond, points):
        return Case(When(cond, then=Value(points)), default=Value(0.0), output_field=FloatField())

    interest_bonus = (
        _bonus(Q(category__in=signals.explicit), POINTS_INTEREST)
        if signals.explicit and not category else Value(0.0, output_field=FloatField())
    )
    taste_bonus = (
        _bonus(Q(category__in=signals.liked_categories), POINTS_TASTE)
        if signals.liked_categories and not category else Value(0.0, output_field=FloatField())
    )
    fof_bonus = (
        _bonus(Q(user_id__in=signals.fof_authors), POINTS_FRIEND_OF_FOLLOW)
        if signals.fof_authors else Value(0.0, output_field=FloatField())
    )

    recommended_score = engagement + video_boost + velocity_boost + interest_bonus + taste_bonus + fof_bonus
    if affinity is not None:
        recommended_score = recommended_score + affinity
    if penalty is not None:
        recommended_score = recommended_score - penalty
    recommended_qs = (
        discovery_qs.filter(created_at__gte=now - timedelta(days=limits["recommended_window_days"]))
        .exclude(id__in=trending_ids_list)
        .annotate(score=ExpressionWrapper(recommended_score, output_field=FloatField()))
        .order_by("-score", "-created_at", "-id")
    )
    recommended_ids_list = _pool_ids(recommended_qs, seen_ids, limits["recommended_pool_cap"], fill_min)

    # Thin platform (nothing in the last 30 days) -> older public posts
    # rather than an empty discovery slot.
    if not recommended_ids_list and not trending_ids_list:
        older_score = engagement + video_boost
        if affinity is not None:
            older_score = older_score + affinity
        if penalty is not None:
            older_score = older_score - penalty
        older = (
            discovery_qs.annotate(score=ExpressionWrapper(older_score, output_field=FloatField()))
            .order_by("-score", "-created_at", "-id")
        )
        recommended_ids_list = _pool_ids(older, seen_ids, limits["recommended_pool_cap"], fill_min)

    return {
        SOURCE_FOLLOWING: following_ids_list,
        SOURCE_RECOMMENDED: recommended_ids_list,
        SOURCE_TRENDING: trending_ids_list,
    }
