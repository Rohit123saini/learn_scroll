"""
post/feed_quality.py - T1 item 6: quality / safety gates for the Home feed.

WHAT IT DOES
------------
Three layers, cheapest first:

1. HARD SAFETY (SQL, every pool incl. following)   `hard_exclude_q()` / `exclude_reported_heavy()`
   Posts with `reported_count >= reported_hard` never enter any pool - not even
   from an account the viewer follows (a heavily reported post is a safety
   problem, not a taste problem). Applied inside `views._home_base_qs`, so the
   feed AND `GET /post/<id>/why/` see the same "safe to show" set.

2. DISCOVERY QUALITY (Python, recommended + trending pools only)   `filter_pools()`
   For the (at most ~500) discovery candidates ONE extra query loads the few
   columns the rules need and `assess()` gives each post a verdict:
       ok       untouched
       demote   moved to the TAIL of its pool (never dropped)
       drop     removed from the pool
   Rules (all thresholds in `settings.FEED_QUALITY`):
       reported_heavy   reported_count >= reported_hard                    -> drop
       reported         reported_count >= reported_soft                    -> demote
       spam             too many links / char-run ("heyyyyyyyy") / SHOUTING /
                        hashtag stuffing / one word repeated over and over -> spam_action
       repeated_text    same author posted the same text > duplicate_max
                        times inside duplicate_window_days                 -> spam_action
       tiny_text        plain text post, no media, shorter than
                        min_text_chars                                     -> demote
   FOLLOWING is never touched by layer 2: following an account is an explicit
   choice (same policy as "show fewer", see feed_mix module docstring).

3. BLOCKED / MUTED / HIDDEN - already enforced (feed_mix `_hidden_author_ids`
   for pool building, `services.exclude_hidden_and_muted` on EVERY hydration,
   i.e. also when a frozen snapshot or cached candidates are re-served). This module adds nothing
   there; `user_profile/block_live.py` additionally calls `feed_cache.invalidate()` for both
   people, so a block does not even show up as a short page from cached candidates.

The pure part (`PostFacts`, `assess`, `text_signals`, `normalize_text`,
`apply_verdicts`) imports no Django and is unit-tested in isolation
(post/tests_feed_quality.py).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

VERDICT_OK = "ok"
VERDICT_DEMOTE = "demote"
VERDICT_DROP = "drop"

REASON_REPORTED_HEAVY = "reported_heavy"
REASON_REPORTED = "reported"
REASON_SPAM = "spam"
REASON_REPEATED_TEXT = "repeated_text"
REASON_TINY_TEXT = "tiny_text"

DEFAULT_QUALITY: Dict[str, object] = {
    "enabled": True,  # master switch (env FEED_QUALITY_ENABLED=0 switches every gate off)
    "reported_hard": 10,  # reported_count >= this -> out of EVERY pool (0 = off)
    "reported_soft": 3,  # reported_count >= this -> demoted in discovery pools (0 = off)
    "min_text_chars": 15,  # plain text post, no media, shorter than this -> demoted (0 = off)
    "max_links": 3,  # more http(s) links than this -> spam
    "max_char_run": 8,  # the same character repeated more than this in a row -> spam
    "max_caps_ratio": 0.7,  # share of UPPER-case letters above this -> spam ...
    "caps_min_letters": 12,  # ... but only when the text has at least this many letters
    "max_hashtags": 15,  # hashtag stuffing -> spam
    "repeat_word_ratio": 0.6,  # share taken by ONE word above this -> spam ...
    "repeat_word_min_words": 8,  # ... but only for texts with at least this many words
    "duplicate_window_days": 7,  # look-back for "same author, same text"
    "duplicate_max": 2,  # more copies than this (the post itself included) -> repeated_text
    "spam_action": VERDICT_DROP,  # what spam / repeated_text become: "drop" | "demote"
    "pool_check_cap": 600,  # at most this many discovery ids are inspected per request
    "duplicate_row_cap": 5000,  # at most this many recent posts are read for the duplicate check
}

# Post types that legitimately carry (almost) no text of their own.
_TEXTLESS_TYPES = {"image", "video", "carousel", "document", "poll", "repost", "doubt", "link", "article"}

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


# --------------------------------------------------------------------------
# Pure logic (no Django)
# --------------------------------------------------------------------------
def merge_config(raw: Optional[Mapping]) -> Dict[str, object]:
    """`raw` (settings.FEED_QUALITY / an experiment override) merged over the defaults."""
    cfg = dict(DEFAULT_QUALITY)
    if raw:
        cfg.update(dict(raw))
    return cfg


def normalize_text(text: Optional[str]) -> str:
    """Lower-case, URLs removed, every run of non-word chars collapsed to one
    space. Two posts that differ only in case / punctuation / spacing / links
    normalise to the same string (the duplicate check compares these)."""
    if not text:
        return ""
    text = _URL_RE.sub(" ", str(text).lower())
    return " ".join(_WORD_RE.findall(text))


def text_signals(text: Optional[str]) -> Dict[str, float]:
    """Cheap spam features of one text."""
    raw = str(text or "")
    links = len(_URL_RE.findall(raw))
    longest_run = 0
    run = 0
    prev = ""
    for ch in raw:
        if ch == prev and not ch.isspace():
            run += 1
        else:
            run = 1
            prev = ch
        if run > longest_run:
            longest_run = run
    letters = [c for c in raw if c.isalpha()]
    upper = sum(1 for c in letters if c.isupper())
    words = _WORD_RE.findall(_URL_RE.sub(" ", raw.lower()))
    top_share = 0.0
    if words:
        counts: Dict[str, int] = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
        top_share = max(counts.values()) / len(words)
    return {
        "links": links,
        "max_char_run": longest_run,
        "letters": len(letters),
        "caps_ratio": (upper / len(letters)) if letters else 0.0,
        "words": len(words),
        "top_word_share": top_share,
    }


@dataclass
class PostFacts:
    """Everything `assess` needs about ONE post."""

    post_id: object = None
    author_id: object = None
    post_type: str = "text"
    text: str = ""  # title + content (spam rules look at this)
    content: str = ""  # content only (the duplicate check compares this)
    hashtag_count: int = 0
    media_count: int = 0
    reported_count: int = 0
    duplicate_count: int = 1  # same author + same normalised text inside the window, this post included


@dataclass
class Verdict:
    level: str = VERDICT_OK
    reasons: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.level == VERDICT_OK


def _spam_action(cfg: Mapping) -> str:
    return VERDICT_DEMOTE if str(cfg.get("spam_action", VERDICT_DROP)).lower() == VERDICT_DEMOTE else VERDICT_DROP


def assess(facts: PostFacts, cfg: Optional[Mapping] = None) -> Verdict:
    """Quality verdict of ONE post. `drop` beats `demote` beats `ok`."""
    cfg = merge_config(cfg)
    if not cfg["enabled"]:
        return Verdict()
    level = VERDICT_OK
    reasons: List[str] = []

    def flag(new_level: str, reason: str) -> None:
        nonlocal level
        reasons.append(reason)
        if new_level == VERDICT_DROP or (new_level == VERDICT_DEMOTE and level == VERDICT_OK):
            level = new_level

    hard, soft = int(cfg["reported_hard"] or 0), int(cfg["reported_soft"] or 0)
    if hard > 0 and facts.reported_count >= hard:
        flag(VERDICT_DROP, REASON_REPORTED_HEAVY)
    elif soft > 0 and facts.reported_count >= soft:
        flag(VERDICT_DEMOTE, REASON_REPORTED)

    sig = text_signals(facts.text)
    spam = (
        sig["links"] > int(cfg["max_links"])
        or sig["max_char_run"] > int(cfg["max_char_run"])
        or (sig["letters"] >= int(cfg["caps_min_letters"]) and sig["caps_ratio"] > float(cfg["max_caps_ratio"]))
        or facts.hashtag_count > int(cfg["max_hashtags"])
        or (sig["words"] >= int(cfg["repeat_word_min_words"]) and sig["top_word_share"] > float(cfg["repeat_word_ratio"]))
    )
    if spam:
        flag(_spam_action(cfg), REASON_SPAM)
    if facts.duplicate_count > int(cfg["duplicate_max"]):
        flag(_spam_action(cfg), REASON_REPEATED_TEXT)

    min_chars = int(cfg["min_text_chars"] or 0)
    if (
        min_chars > 0
        and facts.media_count <= 0
        and facts.post_type not in _TEXTLESS_TYPES
        and len(normalize_text(facts.text)) < min_chars
    ):
        flag(VERDICT_DEMOTE, REASON_TINY_TEXT)
    return Verdict(level, reasons)


def apply_verdicts(ids: Sequence, verdicts: Mapping) -> Tuple[list, Dict[str, int]]:
    """Order-preserving: `drop` ids vanish, `demote` ids move to the tail (their
    relative order kept). Ids without a verdict count as ok. Returns
    (new_ids, {"dropped": n, "demoted": n})."""
    kept, tail = [], []
    stats = {"dropped": 0, "demoted": 0}
    for pid in ids:
        level = getattr(verdicts.get(pid), "level", VERDICT_OK)
        if level == VERDICT_DROP:
            stats["dropped"] += 1
        elif level == VERDICT_DEMOTE:
            stats["demoted"] += 1
            tail.append(pid)
        else:
            kept.append(pid)
    return kept + tail, stats


def duplicate_counts(rows: Iterable[Tuple[object, str]]) -> Dict[Tuple[object, str], int]:
    """[(author_id, text), ...] -> {(author_id, normalised_text): copies}. Empty texts are skipped."""
    out: Dict[Tuple[object, str], int] = {}
    for author_id, text in rows:
        norm = normalize_text(text)
        if not norm:
            continue
        key = (author_id, norm)
        out[key] = out.get(key, 0) + 1
    return out


# --------------------------------------------------------------------------
# Django glue (imported lazily, like feed_mix)
# --------------------------------------------------------------------------
def get_config() -> Dict[str, object]:
    from django.conf import settings

    return merge_config(getattr(settings, "FEED_QUALITY", None))


def config_for(override: Optional[Mapping] = None) -> Dict[str, object]:
    """settings.FEED_QUALITY + the viewer's A/B-variant override (`overrides["quality"]`, see
    post/feed_experiment.py) merged over the defaults."""
    from django.conf import settings

    raw = dict(getattr(settings, "FEED_QUALITY", None) or {})
    raw.update(dict(override or {}))
    return merge_config(raw)


def filter_ids(ids: Sequence, cfg: Optional[Mapping] = None) -> list:
    """Exploration candidates (new creators / test audience) must be clean: every post whose verdict is not
    `ok` (spam, repeated text, tiny text, reported) is removed. Never raises - failure = ids unchanged."""
    cfg = merge_config(cfg) if cfg is not None else get_config()
    ids = list(ids)
    if not cfg["enabled"] or not ids:
        return ids
    try:
        facts = load_facts(ids, cfg)
        return [pid for pid in ids if pid not in facts or assess(facts[pid], cfg).ok]
    except Exception:  # pragma: no cover - defensive
        logger.warning("feed quality filter_ids failed, ids unchanged", exc_info=True)
        return ids


def hard_exclude_q(cfg: Optional[Mapping] = None):
    """Q matching posts the HARD gate removes everywhere, or None when the gate is off."""
    from django.db.models import Q

    cfg = merge_config(cfg) if cfg is not None else get_config()
    hard = int(cfg["reported_hard"] or 0)
    if not cfg["enabled"] or hard <= 0:
        return None
    return Q(reported_count__gte=hard)


def exclude_reported_heavy(qs, cfg: Optional[Mapping] = None):
    """`qs` minus heavily reported posts (layer 1). No-op when the gate is off."""
    q = hard_exclude_q(cfg)
    return qs if q is None else qs.exclude(q)


def load_facts(post_ids: Sequence, cfg: Optional[Mapping] = None) -> Dict[object, PostFacts]:
    """{post_id: PostFacts} for `post_ids`: ONE query for the posts (+ media count)
    and ONE for the authors' recent texts (duplicate check). Rows are capped by
    `pool_check_cap` / `duplicate_row_cap`."""
    from datetime import timedelta

    from django.db.models import Count
    from django.utils import timezone

    from .models import Post

    cfg = merge_config(cfg) if cfg is not None else get_config()
    ids = list(post_ids)[: max(0, int(cfg["pool_check_cap"]))]
    if not ids:
        return {}
    rows = list(
        Post.objects.filter(id__in=ids)
        .annotate(media_n=Count("media", distinct=True))
        .values("id", "user_id", "post_type", "title", "content", "hashtags", "reported_count", "media_n")
    )
    facts: Dict[object, PostFacts] = {}
    for r in rows:
        text = " ".join(x for x in (r["title"], r["content"]) if x)
        tags = r["hashtags"] if isinstance(r["hashtags"], list) else []
        facts[r["id"]] = PostFacts(
            post_id=r["id"], author_id=r["user_id"], post_type=r["post_type"] or "text", text=text,
            content=r["content"] or "",
            hashtag_count=len(tags), media_count=int(r["media_n"] or 0), reported_count=int(r["reported_count"] or 0),
        )

    window = int(cfg["duplicate_window_days"] or 0)
    if window > 0 and facts:
        authors = {f.author_id for f in facts.values()}
        since = timezone.now() - timedelta(days=window)
        recent = (
            Post.objects.filter(user_id__in=authors, created_at__gte=since, is_deleted=False)
            .exclude(content__isnull=True)
            .values_list("user_id", "content")[: max(0, int(cfg["duplicate_row_cap"]))]
        )
        copies = duplicate_counts(recent)
        for f in facts.values():
            # the duplicate check compares CONTENT only (a shared title alone is not spam)
            content_norm = normalize_text(f.content)
            if content_norm:
                f.duplicate_count = max(1, copies.get((f.author_id, content_norm), 1))
    return facts


def filter_pools(pools: Dict[str, list], cfg: Optional[Mapping] = None) -> Tuple[Dict[str, list], Dict[str, Dict[str, int]]]:
    """Layer 2: apply `assess` to the DISCOVERY pools (recommended, trending).
    Returns (pools, {source: {"dropped", "demoted"}}). FOLLOWING is passed
    through untouched. Never raises: any failure returns the pools unchanged."""
    from .feed_mix import SOURCE_RECOMMENDED, SOURCE_TRENDING

    cfg = merge_config(cfg) if cfg is not None else get_config()
    stats: Dict[str, Dict[str, int]] = {}
    if not cfg["enabled"]:
        return pools, stats
    discovery = (SOURCE_RECOMMENDED, SOURCE_TRENDING)
    ids = [pid for src in discovery for pid in pools.get(src, [])]
    if not ids:
        return pools, stats
    try:
        facts = load_facts(ids, cfg)
        verdicts = {pid: assess(f, cfg) for pid, f in facts.items()}
        out = dict(pools)
        for src in discovery:
            new_ids, st = apply_verdicts(pools.get(src, []), verdicts)
            out[src] = new_ids
            stats[src] = st
        return out, stats
    except Exception:  # pragma: no cover - defensive: ranking must never 500 the feed
        logger.warning("feed quality gate failed, using ungated pools", exc_info=True)
        return pools, {}


def explain_post_quality(post, cfg: Optional[Mapping] = None) -> Dict[str, object]:
    """{"verdict", "reasons"} for ONE post - used by feed_explain (`stages.quality`)."""
    cfg = merge_config(cfg) if cfg is not None else get_config()
    facts = load_facts([post.pk], cfg).get(post.pk)
    if facts is None:
        return {"verdict": VERDICT_OK, "reasons": []}
    v = assess(facts, cfg)
    return {"verdict": v.level, "reasons": list(v.reasons)}
