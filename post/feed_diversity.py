"""
post/feed_diversity.py — T1 Part 1: diversity re-rank for the home feed page.

WHERE IT RUNS
-------------
`HomeFeedView._serialize_slices` builds one page (following / recommended /
trending chunks -> `feed_mix.interleave`). This module is the LAST step on
that page: it only REORDERS the posts of the page. It never drops, adds or
moves a post to another page, so pagination (offsets, snapshot cursor, legacy
page numbers, `count`) stays exactly as before.

WHAT IT FIXES
-------------
A strong author (or a run of the same kind of post) can fill half a page:
5 videos in a row, 3 posts from one account back to back, 4 "tech" cards
together. Rules (all soft, spacing-based):

    author_gap       same author should be >= N positions apart   (default 3)
    max_consecutive  at most N posts of the same post_type in a row (default 2)
    category_gap     same category should be >= N positions apart  (default 2)

HOW
---
Greedy slot filling. For every output slot we look at the first `lookahead`
unplaced posts (original order = original ranking), compute a weighted
violation cost for each and take the cheapest; on a tie the EARLIEST wins.
So a page that already satisfies the rules is returned unchanged, and a post
never moves further than `lookahead - 1` positions from where ranking put it.
If every candidate violates something (e.g. the page is 100% one author) the
head item is taken — a rule can bend, a post is never lost.

Pure Python, no Django import: unit-testable in isolation
(`post/tests_feed_diversity.py`). Settings: `settings.FEED_DIVERSITY`
merged over DEFAULT_DIVERSITY (env FEED_DIVERSITY_ENABLED=0 switches it off).

PART 2 (this file also holds it)
--------------------------------
* CROSS-PAGE MEMORY: `diversify(..., history=...)` takes the last few
  (author, type, category) keys of the PREVIOUS page, so page 2's first post is
  not the same author / a 3rd video in a row after page 1's tail. The keys
  travel inside the opaque snapshot cursor (`feed_snapshot.encode_cursor(...,
  tail=...)`); no cursor (first page / legacy page numbers) = no history.
  `page_tail()` produces them, `clean_history()` validates a decoded cursor's.
* AUTHOR CAP AT POOL LEVEL: `cap_pools()` runs when the ranked id pools are
  built (`feed_mix.apply_author_caps`). Posts of one author beyond
  `soft_cap` go to the TAIL of their pool (demoted, never dropped).
  recommended + trending share one counter (an author can't take 2 slots in
  each). `discovery_hard_cap` (default 0 = off) really DROPS the overflow of
  discovery pools; following is never dropped. Settings: `FEED_AUTHOR_CAP`.

NOT yet (later T1 parts): exploration slots, experiment buckets.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, TypeVar

T = TypeVar("T")

DEFAULT_DIVERSITY: Dict[str, float] = {
    "enabled": True,  # master switch: False -> page order untouched
    "author_gap": 3,  # min positions between two posts of one author (<= 1 = rule off)
    "max_consecutive": 2,  # max same post_type in a row (<= 0 = rule off)
    "category_gap": 2,  # min positions between two posts of one category (<= 1 = rule off)
    "lookahead": 8,  # how far down the page a post may be pulled up (<= 1 = no reorder)
    # violation weights: author clumping hurts most, same category least
    "author_weight": 3,
    "type_weight": 2,
    "category_weight": 1,
}


def merge_config(raw: Optional[dict]) -> Dict[str, float]:
    """settings dict over defaults; garbage values fall back to the default."""
    cfg = dict(DEFAULT_DIVERSITY)
    if not isinstance(raw, dict):
        return cfg
    for key, default in DEFAULT_DIVERSITY.items():
        if key not in raw:
            continue
        value = raw[key]
        if key == "enabled":
            cfg[key] = bool(value)
            continue
        try:
            cfg[key] = max(0, int(value))
        except (TypeError, ValueError):
            cfg[key] = default
    return cfg


def _s(value):
    return None if value is None else str(value)


def _key(item, author_of, type_of, category_of):
    return (_s(author_of(item)), _s(type_of(item)), _s(category_of(item)))


def _cost(key, placed: list, cfg) -> int:
    """Weighted rule violations of putting `key` after `placed` (keys)."""
    cost = 0
    author_gap = int(cfg["author_gap"])
    if author_gap > 1 and key[0] is not None:
        if key[0] in {p[0] for p in placed[-(author_gap - 1):]}:
            cost += int(cfg["author_weight"])

    max_run = int(cfg["max_consecutive"])
    if max_run > 0 and key[1] is not None and len(placed) >= max_run:
        if all(p[1] == key[1] for p in placed[-max_run:]):
            cost += int(cfg["type_weight"])

    cat_gap = int(cfg["category_gap"])
    if cat_gap > 1 and key[2] is not None:
        if key[2] in {p[2] for p in placed[-(cat_gap - 1):]}:
            cost += int(cfg["category_weight"])
    return cost


def clean_history(raw) -> List[tuple]:
    """Validate the tail carried by a cursor (client-controlled!). Anything
    odd -> [] (no history), never an error. Max 5 keys of 3 short strings."""
    if not isinstance(raw, (list, tuple)) or len(raw) > 5:
        return []
    out = []
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            return []
        parts = []
        for v in entry:
            if v is not None and (not isinstance(v, str) or len(v) > 64):
                return []
            parts.append(v)
        out.append(tuple(parts))
    return out


def diversify(
    items: Sequence[T],
    author_of: Callable[[T], object],
    type_of: Callable[[T], object],
    category_of: Callable[[T], object],
    config: Optional[dict] = None,
    history: Optional[Sequence] = None,
) -> List[T]:
    """Reorder `items` (best first) so clumps are spread out. Same elements
    in, same elements out; stable when nothing needs fixing. `history` =
    keys of the previous page's tail (see `clean_history`), used only as
    context for the first slots - never part of the output."""
    cfg = merge_config(config) if config is not None else dict(DEFAULT_DIVERSITY)
    items = list(items)
    lookahead = int(cfg["lookahead"])
    if not cfg["enabled"] or lookahead <= 1:
        return items
    if len(items) < 3 and not history:
        return items

    context: List[tuple] = list(clean_history(history)) if history else []
    remaining = [(it, _key(it, author_of, type_of, category_of)) for it in items]
    out: List[T] = []
    while remaining:
        window = remaining[:lookahead]
        best_idx, best_cost = 0, None
        for idx, (_, key) in enumerate(window):
            cost = _cost(key, context, cfg)
            if best_cost is None or cost < best_cost:  # strict < -> earliest wins ties
                best_idx, best_cost = idx, cost
                if cost == 0:
                    break
        item, key = remaining.pop(best_idx)
        out.append(item)
        context.append(key)
    return out


def tail_size(config: Optional[dict] = None) -> int:
    """How many trailing keys the next page needs to see (1..5)."""
    cfg = merge_config(config) if config is not None else dict(DEFAULT_DIVERSITY)
    need = max(int(cfg["author_gap"]) - 1, int(cfg["max_consecutive"]), int(cfg["category_gap"]) - 1, 1)
    return min(5, need)


def _post_fns():
    return (
        lambda sp: getattr(sp[1], "user_id", None),
        lambda sp: getattr(sp[1], "post_type", None),
        lambda sp: getattr(sp[1], "category", None),
    )


def diversify_posts(merged, config: Optional[dict] = None, history=None):
    """`merged` = [(source, Post), ...] from `feed_mix.interleave`. Same shape out."""
    a, t, c = _post_fns()
    return diversify(merged, a, t, c, config, history)


def page_tail(merged, config: Optional[dict] = None, history=None) -> List[list]:
    """Keys (JSON-friendly lists) of the last `tail_size` posts of the final
    page, topped up from `history` when the page is shorter than that."""
    a, t, c = _post_fns()
    keys = list(clean_history(history)) if history else []
    keys += [_key(sp, a, t, c) for sp in merged]
    return [list(k) for k in keys[-tail_size(config):]]


# --------------------------------------------------------------------------
# Part 2: author cap on the ranked id pools
# --------------------------------------------------------------------------
DEFAULT_AUTHOR_CAP: Dict[str, int] = {
    "enabled": True,
    "following_soft_cap": 5,  # beyond this, one author's posts go to the pool tail (<=0 off)
    "discovery_soft_cap": 2,  # same, for recommended + trending (shared counter)
    "discovery_hard_cap": 0,  # recommended + trending: drop beyond this (0 = off)
}


def merge_author_cap_config(raw: Optional[dict]) -> Dict[str, int]:
    cfg = dict(DEFAULT_AUTHOR_CAP)
    if not isinstance(raw, dict):
        return cfg
    for key, default in DEFAULT_AUTHOR_CAP.items():
        if key not in raw:
            continue
        if key == "enabled":
            cfg[key] = bool(raw[key])
            continue
        try:
            cfg[key] = max(0, int(raw[key]))
        except (TypeError, ValueError):
            cfg[key] = default
    return cfg


def cap_ids(ids, author_by_id: dict, soft_cap: int, hard_cap: int = 0, counts: Optional[dict] = None):
    """One pool. Order of kept posts is preserved; overflow (beyond soft_cap)
    is appended after them in its original order; beyond hard_cap it is
    dropped. `counts` may be shared between pools. Unknown author = untouched."""
    counts = {} if counts is None else counts
    kept, overflow = [], []
    for pid in ids:
        author = author_by_id.get(pid)
        if author is None:
            kept.append(pid)
            continue
        n = counts.get(author, 0) + 1
        counts[author] = n
        if hard_cap > 0 and n > hard_cap:
            continue
        if soft_cap > 0 and n > soft_cap:
            overflow.append(pid)
        else:
            kept.append(pid)
    return kept + overflow


def cap_pools(pools: Dict[str, list], author_by_id: dict, config: Optional[dict] = None) -> Dict[str, list]:
    """{source: ids} -> same shape. Keys are the source names used by
    feed_mix ("following" / "recommended" / "trending")."""
    cfg = merge_author_cap_config(config)
    if not cfg["enabled"]:
        return pools
    out = dict(pools)
    if "following" in out:
        out["following"] = cap_ids(out["following"], author_by_id, int(cfg["following_soft_cap"]))
    shared: dict = {}
    for src in ("trending", "recommended"):  # trending first: it is the stronger pool
        if src in out:
            out[src] = cap_ids(
                out[src], author_by_id, int(cfg["discovery_soft_cap"]), int(cfg["discovery_hard_cap"]), shared,
            )
    return out
