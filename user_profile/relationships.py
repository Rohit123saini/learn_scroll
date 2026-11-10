# user_profile/relationships.py
"""
P8 - Relationship management (followers / following / requests / restricted / muted ...).

Ek jagah ke helpers, taaki views.py me followers/following ki do near-duplicate classes ek hi rule use karein:

  * follow_list_queryset()   followers / following list + ?q= search, ?sort=, ?filter=
  * bulk_resolve_requests()  follow requests ko ek saath accept / decline
  * relationship_counts()    "Relationships" hub ke counts (ek call)
  * paged_rows()             ?limit=&offset= paging, jaisa BlockedUsersView karta hai

Rules (sab jagah same):
  * sirf ACCEPTED follows list me aate hain; block (dono taraf) wale kabhi nahi.
  * `filter` HAMESHA VIEWER (request.user) ke relative hai, list ke owner ke relative nahi:
        all                 -> sab
        mutual              -> maine bhi follow kiya AUR unhone bhi mujhe follow kiya
        not_following_back  -> maine follow kiya, unhone mujhe follow NAHI kiya
    Isse kisi doosre ki list par bhi "mutuals" tab ka matlab \"aap dono ke beech follow dono taraf\" rehta hai
    aur koi nayi privacy information leak nahi hoti (sirf viewer ka apna relation).
  * sort: latest (follow kab hua, naya pehle - default) | oldest | name (username A-Z).
    Har sort me `id` tiebreaker hai, taaki page-number paging me row dobara/miss na ho.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Subquery
from django.db.models.functions import Lower

from .models import BlockUser, Follow, RestrictUser

User = get_user_model()

SORT_LATEST = "latest"
SORT_OLDEST = "oldest"
SORT_NAME = "name"
SORTS = (SORT_LATEST, SORT_OLDEST, SORT_NAME)

FILTER_ALL = "all"
FILTER_MUTUAL = "mutual"
FILTER_NOT_FOLLOWING_BACK = "not_following_back"
FILTERS = (FILTER_ALL, FILTER_MUTUAL, FILTER_NOT_FOLLOWING_BACK)

MAX_QUERY_LEN = 50
BULK_MAX = 100  # ek bulk call me max kitni requests (select_all me bhi) - baaki `remaining` me batata hai

ACTION_ACCEPT = "accept"
ACTION_DECLINE = "decline"
BULK_ACTIONS = (ACTION_ACCEPT, ACTION_DECLINE)


# --------------------------------------------------------------------------------------------------
# params
# --------------------------------------------------------------------------------------------------
def parse_list_params(query_params):
    """(q, sort, filter) from a request's query string. Unknown sort/filter fall back to the defaults
    (an old/new app build must never get a 400 for a value it invented)."""
    q = (query_params.get("q") or "").strip()[:MAX_QUERY_LEN]
    sort = (query_params.get("sort") or SORT_LATEST).strip().lower()
    if sort not in SORTS:
        sort = SORT_LATEST
    flt = (query_params.get("filter") or FILTER_ALL).strip().lower()
    if flt not in FILTERS:
        flt = FILTER_ALL
    return q, sort, flt


# --------------------------------------------------------------------------------------------------
# followers / following
# --------------------------------------------------------------------------------------------------
def _accepted(**kw):
    return Follow.objects.filter(status=Follow.Status.ACCEPTED, **kw)


def follow_list_queryset(target, viewer, *, followers, q="", sort=SORT_LATEST, flt=FILTER_ALL, exclude_ids=()):
    """Users in `target`'s followers (followers=True) or following (False) list, as seen by `viewer`.

    `exclude_ids` = ids to hide (the caller passes blocked_user_ids(viewer)). The queryset is annotated
    with `followed_at` (when that follow was made) and ordered per `sort`.
    """
    if followers:
        base = _accepted(following=target).values("follower_id")
        when = _accepted(following=target, follower=OuterRef("pk")).values("created_at")[:1]
    else:
        base = _accepted(follower=target).values("following_id")
        when = _accepted(follower=target, following=OuterRef("pk")).values("created_at")[:1]

    qs = User.objects.filter(id__in=base).annotate(followed_at=Subquery(when))
    if exclude_ids:
        qs = qs.exclude(id__in=list(exclude_ids))

    if q:
        qs = qs.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
        )

    if flt in (FILTER_MUTUAL, FILTER_NOT_FOLLOWING_BACK):
        i_follow = Exists(_accepted(follower=viewer, following=OuterRef("pk")))
        they_follow_me = Exists(_accepted(follower=OuterRef("pk"), following=viewer))
        qs = qs.annotate(_i_follow=i_follow, _they_follow_me=they_follow_me)
        if flt == FILTER_MUTUAL:
            qs = qs.filter(_i_follow=True, _they_follow_me=True)
        else:
            qs = qs.filter(_i_follow=True, _they_follow_me=False)
        qs = qs.exclude(id=viewer.id)

    if sort == SORT_OLDEST:
        return qs.order_by("followed_at", "id")
    if sort == SORT_NAME:
        return qs.order_by(Lower("username"), "id")
    return qs.order_by("-followed_at", "-id")


# --------------------------------------------------------------------------------------------------
# bulk follow requests
# --------------------------------------------------------------------------------------------------
def pending_count(owner):
    return Follow.objects.filter(following=owner, status=Follow.Status.PENDING).count()


def bulk_resolve_requests(owner, *, action, follow_ids=None, select_all=False):
    """Accept / decline many PENDING requests addressed to `owner` in one go.

    * Sirf `owner` ko aayi PENDING requests chhui jaati hain - baaki ids (kisi aur ki request, pehle se
      accept/delete ho chuki, galat id) `skipped` me wapas aati hain, error nahi.
    * Ek call me max BULK_MAX; `select_all=True` me sabse nayi BULK_MAX. App `remaining > 0` dekh kar dobara call kare.
    * Accept: har row `save()` hota hai taaki Follow ka post_save signal counts theek kare, aur har requester ko
      wahi "request accepted" notification jaati hai jo single accept me jaati hai. Decline: chup-chaap delete
      (requester ko kuch nahi batata - single reject jaisa).

    Returns dict(action, processed, ids, skipped, remaining).
    """
    if action not in BULK_ACTIONS:
        raise ValueError("bad action")
    follow_ids = [int(i) for i in (follow_ids or [])]

    qs = Follow.objects.filter(following=owner, status=Follow.Status.PENDING).select_related("follower")
    if not select_all:
        qs = qs.filter(id__in=follow_ids[:BULK_MAX])
    rows = list(qs.order_by("-id")[:BULK_MAX])
    done_ids = [r.id for r in rows]

    if action == ACTION_ACCEPT:
        with transaction.atomic():
            for row in rows:
                row.status = Follow.Status.ACCEPTED
                row.save(update_fields=["status"])
        _notify_accepted(owner, rows)
    else:
        if done_ids:
            # QuerySet.delete() har row par post_delete bhejta hai (signal receiver registered hai).
            Follow.objects.filter(id__in=done_ids, following=owner, status=Follow.Status.PENDING).delete()

    done_set = set(done_ids)
    skipped = [] if select_all else [i for i in follow_ids[:BULK_MAX] if i not in done_set]
    return {
        "action": action,
        "processed": len(done_ids),
        "ids": done_ids,
        "skipped": skipped,
        "remaining": pending_count(owner),
    }


def _notify_accepted(owner, rows):
    """Single AcceptFollowRequestView jaisi notification, har accepted requester ko. Kabhi raise nahi karta."""
    try:
        from core.models import Notification

        from .services import _notify

        for row in rows:
            _notify(
                row.follower_id,
                Notification.NotifType.FOLLOW_REQUEST_ACCEPTED,
                "Follow request accepted",
                f"{owner.username} accepted your follow request.",
                actor=owner,
            )
    except Exception:  # pragma: no cover - accept ho chuka hai, notification ki wajah se 500 nahi
        import logging

        logging.getLogger(__name__).exception("bulk accept notify failed (owner=%s)", getattr(owner, "id", None))


# --------------------------------------------------------------------------------------------------
# hub counts
# --------------------------------------------------------------------------------------------------
def relationship_counts(user):
    """Counts for the \"Relationships\" hub screen - ek dict, ek API call.

    followers / following   accepted follows (block wale rows block par delete ho jaate hain)
    requests                incoming PENDING follow requests
    not_following_back      jinhe main follow karta hu par wo mujhe follow nahi karte
    close_friends / restricted / blocked / muted   meri apni lists ka size
    """
    from post.models import CloseFriend, MutedAccount

    i_follow = _accepted(follower=user)
    return {
        "followers": _accepted(following=user).count(),
        "following": i_follow.count(),
        "requests": pending_count(user),
        "not_following_back": i_follow.exclude(
            following_id__in=_accepted(following=user).values("follower_id")
        ).count(),
        "close_friends": CloseFriend.objects.filter(owner=user).count(),
        "restricted": RestrictUser.objects.filter(user=user).count(),
        "blocked": BlockUser.objects.filter(blocker=user).count(),
        "muted": MutedAccount.objects.filter(user=user).count(),
    }


# --------------------------------------------------------------------------------------------------
# paging (BlockedUsersView jaisa limit/offset - purane app builds ke liye `limit` na ho to poori list, cap 500)
# --------------------------------------------------------------------------------------------------
def paged_rows(query_params, qs, *, cap=500):
    """(rows, has_more, next_offset). `limit` (1..100) + `offset` optional."""
    try:
        limit = min(max(int(query_params.get("limit", "")), 1), 100)
    except (TypeError, ValueError):
        limit = None
    try:
        offset = max(int(query_params.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    if limit is None:
        return list(qs[:cap]), False, None
    rows = list(qs[offset:offset + limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return rows, has_more, (offset + len(rows)) if has_more else None
