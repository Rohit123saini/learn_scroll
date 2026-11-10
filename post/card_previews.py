"""
post/card_previews.py - what a feed card shows UNDER the actions, fetched in ONE batched call.

    POST /post/previews/      {"ids": ["<post uuid>", ... up to 20]}
    -> {"results": {"<post id>": {
            "liked_by": {"names": ["asha", "ravi"], "others": 12},
            "comments": [{"id": "...", "username": "ravi", "text": "Great explanation"}]
       }}}

Why a separate call and not two more fields on PostListSerializer: the feed is the hottest read in the app and
these two lookups are per-post. Here they cost a FIXED number of queries however many cards are asked for
(one for liked-by candidates, one for top comments), and the app asks only for cards that are on screen.

Rules (nothing is shown that the viewer could not already see):
  * only posts the viewer may see (audience.filter_posts_visible_to), not deleted / archived;
  * liked_by: people the viewer FOLLOWS (accepted) who reacted, never someone in a block relationship, never
    the viewer; `others` = total reactions minus those named (and minus the viewer's own);
  * comments: top level, not deleted, not hidden, not by a blocked user; pinned first, then most liked, then
    newest; text is trimmed to 140 chars; comments with only media are skipped (no text to preview).
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .models import Post, PostComment, PostLike

MAX_IDS = 20
MAX_NAMES = 2
MAX_COMMENTS = 2
TEXT_MAX = 140
CANDIDATES_PER_POST = 8  # liked-by rows read per post before trimming to MAX_NAMES


class CardPreviewsThrottle(UserRateThrottle):
    scope = "card_previews"
    rate = "120/min"


def parse_ids(raw):
    """Valid distinct uuid strings from the request body, capped at MAX_IDS, order kept."""
    import uuid

    out = []
    if not isinstance(raw, (list, tuple)):
        return out
    for v in raw:
        try:
            s = str(uuid.UUID(str(v)))
        except (ValueError, AttributeError, TypeError):
            continue
        if s not in out:
            out.append(s)
        if len(out) >= MAX_IDS:
            break
    return out


def clip(text, limit=TEXT_MAX):
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


class CardPreviewsAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CardPreviewsThrottle]

    @extend_schema(summary="Feed card previews: liked-by + top comments (batched)", request=OpenApiTypes.OBJECT,
                   responses={200: OpenApiTypes.OBJECT}, tags=["Feed"])
    def post(self, request):
        from user_profile.models import Follow
        from user_profile.services import blocked_user_ids

        from .audience import filter_posts_visible_to

        ids = parse_ids(request.data.get("ids"))
        if not ids:
            return Response({"results": {}})
        viewer = request.user
        visible = list(
            filter_posts_visible_to(
                Post.objects.filter(pk__in=ids, is_deleted=False, is_archived=False), viewer
            ).values_list("pk", "likes_count")
        )
        if not visible:
            return Response({"results": {}})
        post_ids = [pk for pk, _ in visible]
        totals = {str(pk): int(n or 0) for pk, n in visible}
        blocked = blocked_user_ids(viewer)
        results = {str(pk): {"liked_by": {"names": [], "others": 0}, "comments": []} for pk in post_ids}

        # ---- liked by: people I follow who reacted (one query, trimmed in Python)
        following = set(
            Follow.objects.filter(follower=viewer, status=Follow.Status.ACCEPTED).values_list("following_id", flat=True)
        ) - blocked
        named = {str(pk): [] for pk in post_ids}
        if following:
            rows = (
                PostLike.objects.filter(post_id__in=post_ids, user_id__in=following)
                .select_related("user").order_by("post_id", "-created_at")
                .values_list("post_id", "user__username")[: len(post_ids) * CANDIDATES_PER_POST]
            )
            for pid, username in rows:
                lst = named[str(pid)]
                if len(lst) < MAX_NAMES and username:
                    lst.append(username)
        my_likes = set(str(p) for p in PostLike.objects.filter(post_id__in=post_ids, user=viewer).values_list("post_id", flat=True))
        for pid, names in named.items():
            mine = 1 if pid in my_likes else 0
            results[pid]["liked_by"] = {"names": names, "others": max(0, totals[pid] - len(names) - mine)}

        # ---- top comments (one query for all posts, newest/most-liked window, trimmed in Python)
        crows = (
            PostComment.objects.filter(post_id__in=post_ids, parent__isnull=True, is_deleted=False, is_hidden=False)
            .exclude(content="").exclude(user_id__in=blocked)
            .select_related("user").order_by("post_id", "-is_pinned", "-likes_count", "-created_at")
            .values_list("post_id", "id", "user__username", "content")[: len(post_ids) * 6]
        )
        for pid, cid, username, content in crows:
            lst = results[str(pid)]["comments"]
            text = clip(content)
            if len(lst) < MAX_COMMENTS and text:
                lst.append({"id": str(cid), "username": username, "text": text})
        return Response({"results": results})
