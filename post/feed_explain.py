"""
post/feed_explain.py - "Why am I seeing this post?" (GET /post/<id>/why/).

The ranking in `feed_mix.build_pool_ids` melts every signal into ONE number
(engagement + 15 interest + 10 recent reactions + 12 friend-of-follow - the
"show fewer" penalty ...). A number can't be shown to a user, so this module
pulls the signals apart again and reports the ones that apply to ONE post as
reasons.

It deliberately MIRRORS the feed instead of guessing:

* the pool a post can come from is decided the same way `build_pool_ids`
  decides it:  followed author -> `following`;  else in the trending pool
  (`feed_mix.trending_rank` < `trending_pool_cap`, same queryset / score /
  tie-breaks) -> `trending`;  else a public post of a public account ->
  `recommended`.
* the recommended signals come from `feed_mix.load_taste_signals` and use the
  same point constants (`POINTS_INTEREST`, ...), i.e. the very queries the
  ranking runs.
* only signals that really scored in that pool are reported: a trending post
  gets the trending reason (its score has no interest bonus), a followed
  author's post `following` (the following pool ranks by recency/engagement,
  not taste). `author_affinity` is the one signal that scores in ALL pools, so
  it can be added to any of them.

Reason codes (stable - the Flutter app localises / picks icons by `code`):

    own_post           you wrote it
    following          you follow the author
    trending           in the platform-wide trending pool right now
    interest_category  its category is one of the interests you picked
    liked_category     you reacted to posts of that category in the last 30 days
    friend_of_follow   people you follow also follow the author
    author_affinity    you like / comment on this author's posts a lot (any pool)
    popular            recommended, but no personal signal applies (engagement/freshness only)
    not_in_feed        it would not be in your feed (opened via link / search / profile)

Everything here is read-only. The pure part (`PostSignals`, `build_reasons`)
does not import Django and is unit-tested in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import feed_mix

REASON_OWN_POST = "own_post"
REASON_FOLLOWING = "following"
REASON_TRENDING = "trending"
REASON_INTEREST = "interest_category"
REASON_LIKED = "liked_category"
REASON_FRIEND_OF_FOLLOW = "friend_of_follow"
REASON_AUTHOR_AFFINITY = "author_affinity"
REASON_POPULAR = "popular"
REASON_NOT_IN_FEED = "not_in_feed"

# how many "people you follow who also follow the author" names to return
FOF_NAMES = 3


# --------------------------------------------------------------------------
# Pure logic (no Django)
# --------------------------------------------------------------------------
@dataclass
class PostSignals:
    """Everything `build_reasons` needs to know about ONE post + ONE viewer."""

    is_own: bool = False
    following: bool = False
    author_id: str = ""
    author_username: str = ""
    trending: bool = False  # in the trending pool (rank < cap)
    discovery: bool = False  # eligible for the recommended pool
    category: str = ""
    category_label: str = ""
    interest_category: bool = False
    liked_category: bool = False
    friend_of_follow: bool = False
    fof_via: List[Dict[str, str]] = field(default_factory=list)  # [{"id", "username"}]
    # author affinity scores in EVERY pool (following / trending / recommended)
    author_affinity: bool = False
    affinity_likes: int = 0
    affinity_comments: int = 0


def _reason(code: str, text: str, **meta) -> Dict[str, object]:
    return {"code": code, "text": text, "meta": meta}


def _affinity_reason(sig: PostSignals) -> Dict[str, object]:
    parts = []
    if sig.affinity_likes:
        parts.append(f"liked {sig.affinity_likes} of")
    if sig.affinity_comments:
        parts.append(f"commented on {sig.affinity_comments} of")
    what = " and ".join(parts) if parts else "engaged with"
    return _reason(
        REASON_AUTHOR_AFFINITY,
        f"You've recently {what} @{sig.author_username}'s posts.",
        user_id=sig.author_id, username=sig.author_username,
        likes=sig.affinity_likes, comments=sig.affinity_comments,
    )


def build_reasons(sig: PostSignals) -> Tuple[Optional[str], List[Dict[str, object]]]:
    """(feed_source, reasons) for one post. `feed_source` is the pool the post
    belongs to ("following" | "recommended" | "trending") or None when it is
    not part of the viewer's feed. `reasons[0]` is the headline reason."""
    if sig.is_own:
        return None, [_reason(REASON_OWN_POST, "This is your own post.")]

    affinity = _affinity_reason(sig) if sig.author_affinity else None

    if sig.following:
        reasons = [
            _reason(
                REASON_FOLLOWING,
                f"You follow @{sig.author_username}.",
                user_id=sig.author_id, username=sig.author_username,
            )
        ]
        return feed_mix.SOURCE_FOLLOWING, reasons + ([affinity] if affinity else [])

    if sig.trending:
        reasons = [_reason(REASON_TRENDING, "This post is trending on LearnScroll right now.")]
        return feed_mix.SOURCE_TRENDING, reasons + ([affinity] if affinity else [])

    if sig.discovery:
        reasons = []
        if affinity:
            reasons.append(affinity)  # the most personal signal leads
        label = sig.category_label or sig.category
        if sig.interest_category:
            reasons.append(_reason(
                REASON_INTEREST, f"You picked {label} as one of your interests.",
                category=sig.category, label=label,
            ))
        if sig.liked_category:
            reasons.append(_reason(
                REASON_LIKED, f"You reacted to {label} posts recently.",
                category=sig.category, label=label,
            ))
        if sig.friend_of_follow:
            names = ", ".join(f"@{v['username']}" for v in sig.fof_via)
            text = (
                f"People you follow ({names}) also follow @{sig.author_username}."
                if names else f"People you follow also follow @{sig.author_username}."
            )
            reasons.append(_reason(REASON_FRIEND_OF_FOLLOW, text, via=list(sig.fof_via)))
        if not reasons:
            reasons.append(_reason(
                REASON_POPULAR, "Suggested because it is popular and fresh on LearnScroll.",
            ))
        return feed_mix.SOURCE_RECOMMENDED, reasons

    return None, [_reason(
        REASON_NOT_IN_FEED,
        "This post isn't part of your suggestions - you probably opened it from a link, search or a profile.",
    )]


# --------------------------------------------------------------------------
# DB-backed collection (Django imported lazily, like feed_mix)
# --------------------------------------------------------------------------
def _category_label(category: str) -> str:
    from .models import Post

    return dict(Post.CATEGORY_CHOICES).get(category, category or "")


def collect_signals(user, post, base_qs, following_ids: set, video_and_velocity_boost, now=None) -> PostSignals:
    """Evaluate the same conditions the feed pools use, for one `post`.

    `base_qs` is the caller's "safe to show" queryset (views._home_base_qs) -
    the same one HomeFeedView builds its pools from."""
    from django.utils import timezone

    from user_profile.models import Follow

    now = now or timezone.now()
    sig = PostSignals(
        author_id=str(post.user_id),
        author_username=post.user.username,
        category=post.category or "",
        category_label=_category_label(post.category),
    )
    if post.user_id == user.id:
        sig.is_own = True
        return sig
    # Author affinity scores in every pool, so it is looked up before the pool split.
    affinity = feed_mix.load_author_affinity(user, now).get(str(post.user_id))
    if affinity:
        sig.author_affinity = True
        sig.affinity_likes = int(affinity["likes"])
        sig.affinity_comments = int(affinity["comments"])
    if post.user_id in following_ids:
        sig.following = True
        return sig

    # Recommended-pool eligibility = discovery_qs of feed_mix (public post of a
    # public account the caller doesn't follow) on top of the shared base_qs
    # (approved, not sensitive, not hidden/muted, ...), and not a blocked author.
    hidden_authors = feed_mix._hidden_author_ids(user)
    sig.discovery = (
        post.user_id not in hidden_authors
        and base_qs.filter(pk=post.pk, visibility="public", user__is_private=False).exists()
    )
    if not sig.discovery:
        return sig

    seen_ids = feed_mix.get_seen_post_ids(user)
    rank = feed_mix.trending_rank(
        user, base_qs, following_ids, video_and_velocity_boost, post, now=now, seen_ids=seen_ids,
    )
    if rank is not None and rank < feed_mix.get_limits()["trending_pool_cap"]:
        sig.trending = True
        return sig  # trending score has no taste signals -> nothing else to report

    taste = feed_mix.load_taste_signals(user, following_ids, now)
    sig.interest_category = post.category in taste.explicit
    sig.liked_category = post.category in taste.liked_categories
    if post.user_id in taste.fof_authors:
        via = (
            Follow.objects.filter(
                follower_id__in=list(following_ids), following_id=post.user_id, status=Follow.Status.ACCEPTED,
            )
            .order_by("follower__username")
            .values_list("follower_id", "follower__username")[:FOF_NAMES]
        )
        sig.fof_via = [{"id": str(uid), "username": name} for uid, name in via]
        sig.friend_of_follow = True
    return sig


def dampened_by_user(user, post, now=None) -> List[Dict[str, object]]:
    """The viewer's active "show fewer" rows that match this post - so the app
    can say "You asked for fewer #python posts" next to the reasons. Each item:
    {"kind", "key", "strength"} (strength = decayed weight, 0..max_weight)."""
    effective = feed_mix.load_feedback_effective(user, now)
    out: List[Dict[str, object]] = []
    category = effective["category"].get(post.category)
    if category is not None:
        out.append({"kind": "category", "key": post.category, "strength": round(category, 2)})
    for tag in post.hashtags or []:
        tag = str(tag).strip().lstrip("#").lower()
        weight = effective["hashtag"].get(tag)
        if weight is not None:
            out.append({"kind": "hashtag", "key": tag, "strength": round(weight, 2)})
    author = effective["author"].get(str(post.user_id))
    if author is not None:
        out.append({"kind": "author", "key": str(post.user_id), "strength": round(author, 2)})
    return out


def explain_post(user, post, base_qs, following_ids: set, video_and_velocity_boost, now=None) -> Dict[str, object]:
    """The `GET /post/<id>/why/` payload for one post."""
    from django.utils import timezone

    now = now or timezone.now()
    sig = collect_signals(user, post, base_qs, following_ids, video_and_velocity_boost, now)
    feed_source, reasons = build_reasons(sig)
    return {
        "post_id": str(post.id),
        "feed_source": feed_source,
        "reasons": reasons,
        "dampened": dampened_by_user(user, post, now),
    }
