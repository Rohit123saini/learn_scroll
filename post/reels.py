"""
post/reels.py - the Reels (vertical short-video) feed: candidates + ranking.

    GET /post/reels/                      first page of a new scrolling session
    GET /post/reels/?start=<post_id>      same, but that video is FIRST
    GET /post/reels/?cursor=<opaque>      next page (follow the `next` URL)

CANDIDATES
----------
`post_type='video'` (so no reposts), approved, public, not sensitive, not
deleted, not the caller's own, author not blocked (either direction),
"Not interested" posts / muted accounts removed (`exclude_hidden_and_muted`,
already part of the Home base queryset). Public posts of PRIVATE accounts are
only candidates for people who follow that account - same rule as everywhere
else in the app.

On top of that a video must be *playable as a reel*:
  * it has a video media row with a known `duration_seconds` > 0
    (`POST /post/<id>/video-progress/` needs it and answers 409 without it),
  * that duration is <= `FEED_REELS["max_duration_seconds"]` (default 180),
  * it is vertical-friendly: `height / width >= FEED_REELS["min_aspect"]` (1.2).
    Rows with unknown dimensions (width / height NULL or width 0) are allowed.

RANKING (ONE pool, all pieces are the Home ones from post/feed_mix.py)
--------------------------------------------------------------------
    engagement  + video_watch_boost + velocity          (quality / "hot now")
    + interest (+15) + taste (+10) + friend-of-follow (+12)
    + author affinity (likes / comments on the author)
    + following_bonus (default +10) for authors the caller follows
    - "show fewer" penalty (category / hashtag / author)

The following bonus is deliberately small: people you follow show up more
often, but they do not own the feed (one strong video from a stranger still
beats a weak one from a followed account). Everything is additive - nothing
is hidden by a score. As with Home, an author with an active "show fewer" row
gets no affinity (show fewer wins).

SEEN: videos the caller already saw (`PostView`, incl. the batch
`POST /post/feed/seen/`) are excluded BEFORE the pool cap; if the pool ends up
smaller than `FEED_SEEN_LIMITS["fill_min"]` it is topped up with seen videos at
the tail (`feed_mix._pool_ids`), so Reels is never empty just because the user
watched everything.

PAGINATION: the first request ranks ONCE and freezes the ordered id list in
the cache (namespace `reels`, post/feed_snapshot.py); every later page is a
slice of that list addressed by the cursor, so the order does not move while
the user swipes. Cache miss -> the pool is rebuilt with the cursor's own
`seen_cutoff` and the stored offset is applied (best effort, never an error).

`?start=<post_id>`: that video goes to position 0 (tap a video on a profile /
Home and continue in Reels). It only needs to be a video the caller may watch
(approved, not deleted, not sensitive, not blocked / hidden, visible to them -
their OWN video is fine here); it does NOT have to pass the aspect / duration
rules, because the user explicitly chose it. A start id that is not watchable
is ignored silently (no error, nothing about its existence is leaked). It is
removed from the rest of the pool so it never repeats.
"""
from __future__ import annotations

import uuid
from typing import Iterable, List, Optional

NAMESPACE = "reels"

DEFAULT_REELS = {
    "enabled": True,  # master switch: False -> GET /post/reels/ returns an empty page
    "min_aspect": 1.2,  # height / width must be >= this (<= 0 switches the check off)
    "max_duration_seconds": 180,  # longer videos are not reels (<= 0 = no cap)
    "following_bonus": 10.0,  # extra points for authors the caller follows
    "pool_cap": 300,  # max ranked ids frozen per scrolling session
    "page_size": 10,  # default page size (?page_size=)
    "max_page_size": 30,
}


def get_config() -> dict:
    """`settings.FEED_REELS` merged over DEFAULT_REELS; garbage values fall back."""
    from django.conf import settings

    cfg = dict(DEFAULT_REELS)
    cfg.update(getattr(settings, "FEED_REELS", None) or {})
    for key, cast in (
        ("min_aspect", float), ("max_duration_seconds", int), ("following_bonus", float),
        ("pool_cap", int), ("page_size", int), ("max_page_size", int),
    ):
        try:
            cfg[key] = cast(cfg[key])
        except (TypeError, ValueError):
            cfg[key] = DEFAULT_REELS[key]
    cfg["pool_cap"] = max(1, cfg["pool_cap"])
    cfg["max_page_size"] = max(1, cfg["max_page_size"])
    cfg["page_size"] = max(1, min(cfg["page_size"], cfg["max_page_size"]))
    return cfg


def is_enabled() -> bool:
    return bool(get_config()["enabled"])


# --------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------
def playable_media_queryset(cfg: Optional[dict] = None):
    """PostMedia rows that make a video usable as a reel (duration known and
    within the cap, vertical-friendly or dimensions unknown). Used as an
    `Exists(...)` correlated on `post`."""
    from django.db.models import ExpressionWrapper, F, FloatField, Q, Value

    from .models import PostMedia

    cfg = cfg or get_config()
    qs = PostMedia.objects.filter(media_type="video", duration_seconds__gt=0)
    if cfg["max_duration_seconds"] > 0:
        qs = qs.filter(duration_seconds__lte=cfg["max_duration_seconds"])
    if cfg["min_aspect"] > 0:
        min_height = ExpressionWrapper(F("width") * Value(float(cfg["min_aspect"])), output_field=FloatField())
        qs = qs.filter(
            Q(width__isnull=True) | Q(height__isnull=True) | Q(width=0) | Q(height__gte=min_height)
        )
    return qs


def _followed_annotation(user):
    """`Exists(...)`: the caller follows (accepted) the post's author."""
    from django.db.models import Exists, OuterRef

    from user_profile.models import Follow

    return Exists(Follow.objects.filter(
        follower=user, following_id=OuterRef("user_id"), status=Follow.Status.ACCEPTED,
    ))


def candidate_queryset(user, base_qs, cfg: Optional[dict] = None):
    """Reel candidates for `user`, annotated with `is_followed`.

    `base_qs` is the shared Home "safe to show" queryset (not deleted,
    approved, not sensitive, not own, superseded reposts removed, hidden posts
    / muted accounts removed) - see views._home_base_qs."""
    from django.db.models import Exists, OuterRef, Q

    from .feed_mix import _hidden_author_ids

    cfg = cfg or get_config()
    qs = (
        base_qs.filter(post_type="video", visibility="public")
        .annotate(is_followed=_followed_annotation(user))
        .filter(Q(user__is_private=False) | Q(is_followed=True))
        .filter(Exists(playable_media_queryset(cfg).filter(post_id=OuterRef("pk"))))
    )
    hidden = _hidden_author_ids(user)
    if hidden:
        qs = qs.exclude(user_id__in=hidden)
    return qs


# --------------------------------------------------------------------------
# ranking
# --------------------------------------------------------------------------
def build_pool_ids(user, base_qs, video_and_velocity_boost, seen_ids: Optional[set] = None, now=None) -> List:
    """The ONE ranked pool: ordered list of post ids (best first), at most
    `pool_cap` long. See the module docstring for the formula."""
    from django.db.models import Case, ExpressionWrapper, FloatField, Q, Value, When
    from django.utils import timezone

    from . import feed_mix

    cfg = get_config()
    now = now or timezone.now()
    if seen_ids is None:
        seen_ids = feed_mix.get_seen_post_ids(user)
    fill_min = int(feed_mix.get_seen_limits()["fill_min"])

    video_boost, velocity_boost = video_and_velocity_boost()
    penalties = feed_mix.load_feedback_penalties(user, now)
    penalty = feed_mix.penalty_expression(penalties)
    affinity = feed_mix.affinity_expression(
        feed_mix.load_author_affinity(user, now, exclude_authors=set(penalties["author"]))
    )
    # following_ids only feeds the friend-of-follow signal (authors followed by
    # someone the caller follows); the "is followed" test itself is a subquery.
    from user_profile.models import Follow

    following_ids = set(
        Follow.objects.filter(follower=user, status=Follow.Status.ACCEPTED).values_list("following_id", flat=True)
    )
    signals = feed_mix.load_taste_signals(user, following_ids, now)

    def _bonus(cond, points):
        return Case(When(cond, then=Value(float(points))), default=Value(0.0), output_field=FloatField())

    score = feed_mix._engagement_expression() + video_boost + velocity_boost
    if signals.explicit:
        score = score + _bonus(Q(category__in=signals.explicit), feed_mix.POINTS_INTEREST)
    if signals.liked_categories:
        score = score + _bonus(Q(category__in=signals.liked_categories), feed_mix.POINTS_TASTE)
    if signals.fof_authors:
        score = score + _bonus(Q(user_id__in=signals.fof_authors), feed_mix.POINTS_FRIEND_OF_FOLLOW)
    if affinity is not None:
        score = score + affinity
    if cfg["following_bonus"]:
        score = score + _bonus(Q(is_followed=True), cfg["following_bonus"])
    if penalty is not None:
        score = score - penalty

    ranked = (
        candidate_queryset(user, base_qs, cfg)
        .annotate(score=ExpressionWrapper(score, output_field=FloatField()))
        .order_by("-score", "-created_at", "-id")
    )
    return feed_mix._pool_ids(ranked, seen_ids, cfg["pool_cap"], fill_min)


# --------------------------------------------------------------------------
# ?start=<post_id>
# --------------------------------------------------------------------------
def parse_start(raw) -> Optional[uuid.UUID]:
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def watchable_queryset(user):
    """Videos `user` may watch in Reels right now (used to validate `start` and
    to re-fetch the frozen ids of every page, so a video deleted / made private /
    blocked AFTER the snapshot was taken silently disappears). Own videos are
    allowed here; the aspect / duration rules are NOT applied (see docstring)."""
    from django.db.models import Q

    from .feed_mix import _hidden_author_ids
    from .models import Post
    from .services import exclude_hidden_and_muted

    qs = (
        Post.objects.select_related("user", "original_post__user")
        .prefetch_related("media", "original_post__media")
        .filter(post_type="video", is_deleted=False, moderation_status="approved", is_sensitive=False)
        .annotate(is_followed=_followed_annotation(user))
        .filter(
            Q(user=user)
            | Q(visibility="public", user__is_private=False)
            | Q(visibility="public", is_followed=True)
        )
    )
    qs = exclude_hidden_and_muted(qs, user)
    hidden = _hidden_author_ids(user)
    if hidden:
        qs = qs.exclude(user_id__in=hidden)
    return qs


def page_queryset(user):
    """`watchable_queryset` trimmed for serving a page: a reel is never a repost, so the
    repost joins / prefetches are dropped - only `user` (author) and `media` are loaded."""
    return (
        watchable_queryset(user)
        .select_related(None).select_related("user")
        .prefetch_related(None).prefetch_related("media")
    )


def viewer_state(user, post_ids):
    """Batched viewer state for one page (2 queries, whatever the page size):
    ({post_id: reaction_type}, {saved post ids}) -> ReelSerializer context."""
    from .models import PostLike, PostSave

    post_ids = list(post_ids)
    if not post_ids:
        return {}, set()
    reactions = dict(
        PostLike.objects.filter(user=user, post_id__in=post_ids).values_list("post_id", "reaction_type")
    )
    saved = set(PostSave.objects.filter(user=user, post_id__in=post_ids).values_list("post_id", flat=True))
    return reactions, saved


def resolve_start(user, raw) -> Optional[uuid.UUID]:
    """The `?start=` id if the caller may watch it, else None."""
    post_id = parse_start(raw)
    if post_id is None:
        return None
    return post_id if watchable_queryset(user).filter(pk=post_id).exists() else None


def with_start_first(ids: Iterable, start_id: Optional[uuid.UUID]) -> List:
    """`ids` with `start_id` moved (or added) to position 0, never twice."""
    rest = [pid for pid in ids if pid != start_id]
    return ([start_id] + rest) if start_id is not None else rest
