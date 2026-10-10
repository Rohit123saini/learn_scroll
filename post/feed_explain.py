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
# T1 Parts 3-4
REASON_NEW_CREATOR = "new_creator"  # exploration slot
REASON_EDU_TOPIC = "educational_topic"  # matches a subject the viewer studies
REASON_STUDY_TIME = "study_time"  # education post during study hours
REASON_CAMPUS = "campus_context"
REASON_CLASS = "class_context"
REASON_ENGAGED = "engaged_author"  # dwell / save / share behaviour

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
    # T1 Part 3 - exploration
    new_creator: bool = False
    explore_phase: str = ""  # test | scale (only when new_creator)
    # T1 Part 4 - behaviour + educational lens
    engaged_points: float = 0.0  # >0: viewer dwelled / saved / shared this author
    edu_subject: str = ""  # the studied subject this post matches
    study_time: bool = False
    campus_context: bool = False
    class_context: bool = False
    # per-signal points (stage "score" of the explanation)
    points: Dict[str, float] = field(default_factory=dict)


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


def _context_reasons(sig: PostSignals) -> List[Dict[str, object]]:
    """T1 Parts 3-4 reasons that can apply in any non-following pool."""
    out: List[Dict[str, object]] = []
    if sig.class_context:
        out.append(_reason(
            REASON_CLASS, f"@{sig.author_username} teaches a class you joined.",
            user_id=sig.author_id, username=sig.author_username,
        ))
    if sig.campus_context:
        out.append(_reason(
            REASON_CAMPUS, f"@{sig.author_username} is from your campus.",
            user_id=sig.author_id, username=sig.author_username,
        ))
    if sig.edu_subject:
        out.append(_reason(
            REASON_EDU_TOPIC, f"It matches {sig.edu_subject}, a subject you study.", subject=sig.edu_subject,
        ))
    if sig.study_time:
        out.append(_reason(REASON_STUDY_TIME, "It's study time - learning posts are lifted right now."))
    if sig.engaged_points >= 2.0:
        out.append(_reason(
            REASON_ENGAGED, f"You usually spend time on @{sig.author_username}'s posts.",
            user_id=sig.author_id, username=sig.author_username,
        ))
    if sig.new_creator:
        out.append(_reason(
            REASON_NEW_CREATOR,
            f"@{sig.author_username} is new on LearnScroll - we show new creators a chance.",
            user_id=sig.author_id, username=sig.author_username, phase=sig.explore_phase,
        ))
    return out


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
        return feed_mix.SOURCE_TRENDING, reasons + ([affinity] if affinity else []) + _context_reasons(sig)

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
        # exploration leads when that is the only reason the post is here
        reasons = _context_reasons(sig) + reasons if sig.new_creator else reasons + _context_reasons(sig)
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
    if affinity:
        sig.points["author_affinity"] = float(affinity.get("points", 0.0) or 0.0)
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

    _collect_extra_signals(sig, user, post, following_ids, now)

    seen_ids = feed_mix.get_seen_post_ids(user)
    rank = feed_mix.trending_rank(
        user, base_qs, following_ids, video_and_velocity_boost, post, now=now, seen_ids=seen_ids,
    )
    if rank is not None and rank < feed_mix.get_limits()["trending_pool_cap"]:
        sig.trending = True
        return sig  # trending score has no taste signals -> nothing else to report

    taste = feed_mix.load_taste_signals(user, following_ids, now)
    sig.points["interest_category"] = feed_mix.POINTS_INTEREST if post.category in taste.explicit else 0.0
    sig.points["liked_category"] = feed_mix.POINTS_TASTE if post.category in taste.liked_categories else 0.0
    sig.points["friend_of_follow"] = (
        feed_mix.POINTS_FRIEND_OF_FOLLOW if post.user_id in taste.fof_authors else 0.0
    )
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


def _collect_extra_signals(sig: PostSignals, user, post, following_ids: set, now) -> None:
    """T1 Parts 3-4: the exploration / behaviour / educational-lens facts for
    ONE post. Same helpers the ranking uses; every part is best effort (a
    failure just means "no extra reason")."""
    import logging

    from django.utils import timezone

    from . import feed_context, feed_experiment, feed_explore, feed_signals

    log = logging.getLogger(__name__)
    exp = feed_experiment.resolve(user.pk)
    try:  # behaviour (dwell / tap / save / share / quick-skip)
        behaviour = feed_signals.load_behaviour_signals(
            user, now, cfg=feed_signals.get_config(exp["overrides"].get("signals")),
        )
        author_pts = float(behaviour["authors"].get(str(post.user_id), 0.0))
        cat_pts = float(behaviour["categories"].get(post.category, 0.0))
        sig.engaged_points = author_pts
        if author_pts or cat_pts:
            sig.points["behaviour"] = round(author_pts + cat_pts, 2)
    except Exception:  # pragma: no cover
        log.warning("explain: behaviour signals failed", exc_info=True)
    try:  # educational lens
        ctx_cfg = feed_context.get_config(exp["overrides"].get("context"))
        ctx = feed_context.load_context(user, ctx_cfg)
        subject = feed_context.matching_subject(ctx, post)
        if subject:
            sig.edu_subject = subject
            sig.points["educational_topic"] = float(ctx_cfg["points_subject"])
        if ctx.is_learner and post.category == "education":
            hour = timezone.localtime(now).hour
            studying = feed_context.is_study_time(hour, ctx_cfg["study_hours"])
            sig.study_time = studying
            sig.points["study_time"] = float(ctx_cfg["points_study" if studying else "points_study_off"])
        if post.user_id in ctx.class_authors:
            sig.class_context = True
            sig.points["class_context"] = float(ctx_cfg["points_context"])
        elif post.user_id in ctx.campus_authors:
            sig.campus_context = True
            sig.points["campus_context"] = float(ctx_cfg["points_context"])
    except Exception:  # pragma: no cover
        log.warning("explain: educational context failed", exc_info=True)
    try:  # exploration (new creator)
        cfg = feed_explore.get_config(exp["overrides"].get("explore"))
        if cfg["enabled"]:
            author = post.user
            joined = getattr(author, "date_joined", None)
            from datetime import timedelta

            is_new = bool(joined) and (now - joined) <= timedelta(days=int(cfg["new_creator_days"]))
            if not is_new:
                from .models import Post

                is_new = Post.objects.filter(user_id=post.user_id, is_deleted=False).count() <= int(
                    cfg["new_creator_max_posts"]
                )
            if is_new and (now - post.created_at) <= timedelta(days=int(cfg["candidate_max_age_days"])):
                imp, pos = feed_explore.load_post_stats([post.id], cfg, now).get(post.id, (0, 0))
                ph = feed_explore.phase((now - post.created_at).total_seconds() / 60.0, imp, pos, cfg)
                if ph != feed_explore.PHASE_DROP:
                    sig.new_creator = True
                    sig.explore_phase = ph
    except Exception:  # pragma: no cover
        log.warning("explain: exploration check failed", exc_info=True)


def build_stages(sig: PostSignals, feed_source: Optional[str], exp: dict) -> List[Dict[str, object]]:
    """T1 Part 3: stage-wise explanation - WHERE the post came from (candidate),
    HOW it was scored (score) and WHAT re-ordered it (rerank). Pure."""
    if feed_source is None:
        return [{"stage": "candidate", "source": None, "note": "not part of the viewer's feed"}]
    points = {k: round(v, 2) for k, v in sig.points.items() if v}
    candidate_via = "exploration" if sig.new_creator and feed_source == feed_mix.SOURCE_RECOMMENDED else feed_source
    rerank = ["author_cap", "diversity_spacing"]
    if sig.new_creator:
        rerank.insert(0, "exploration_slot")
    return [
        {"stage": "candidate", "source": feed_source, "via": candidate_via,
         "explore_phase": sig.explore_phase or None},
        {"stage": "score", "points": points, "total_points": round(sum(points.values()), 2)},
        {"stage": "rerank", "rules": rerank},
    ]


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
    from . import feed_experiment

    exp = feed_experiment.resolve(user.pk)
    stages = build_stages(sig, feed_source, exp)
    # T1 item 6/7: quality-gate verdict for discovery posts (following is never gated).
    if feed_source is not None and feed_source != feed_mix.SOURCE_FOLLOWING:
        from . import feed_quality

        try:
            q_cfg = feed_quality.config_for(exp["overrides"].get("quality"))
            if q_cfg["enabled"]:  # gate off -> no stage (keeps the old candidate / score / rerank trail)
                stages.append({"stage": "quality", **feed_quality.explain_post_quality(post, q_cfg)})
        except Exception:  # pragma: no cover - explain must never 500
            pass
    payload = {
        "post_id": str(post.id),
        "feed_source": feed_source,
        "reasons": reasons,
        "dampened": dampened_by_user(user, post, now),
        # T1 Part 3: which A/B bucket the viewer is in + the stage-wise trail
        "experiment": {"name": exp["experiment"], "bucket": exp["bucket"], "variant": exp["variant"]},
        "stages": stages,
    }
    if getattr(user, "is_staff", False):  # T1 item 7: the exact numbers in force, staff only
        from . import feed_config

        payload["config"] = feed_config.effective(user.pk)
    return payload
