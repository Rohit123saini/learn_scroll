# post/views/post_manage.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from user_profile.models import Follow
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.types import OpenApiTypes
from ..models import Post
from ..serializers import (
    PostDetailSerializer,
    PostEditSerializer,
    PostListSerializer,
    PostVisibilitySerializer,
    RepostRequestSerializer,
)
from django.db.models import Q
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from .. import content_defaults as _content_defaults
from ..signals import decrement_posts_count_on_soft_delete
from drf_spectacular.utils import extend_schema
from rest_framework import (
    generics,
    status,
)
from django.shortcuts import get_object_or_404
from ..services import (
    notify_post_reposted,
    unnotify_post_reposted,
)
from rest_framework.decorators import permission_classes
from django.utils import timezone
from django.db import transaction
from ._common import (
    StandardResultsSetPagination,
    User,
    _first_error_text,
)


# ===================== POST REPOST =====================
# Instagram/Twitter-style repost. A repost is a REAL `Post` row
# (`post_type='repost'`, `original_post` -> the post being reposted), so
# feeds, profile lists, likes, comments, delete and `posts_count` all work
# on it with no special casing. `Post.reposts_count` on the original is
# kept in sync by `update_reposts_count` (models.py signal), not here.
#
# Policy decisions (all easy to flip in one place):
#   * Chains are flattened: reposting a repost points the new row at the
#     ROOT original, so the embedded preview is always one level deep.
#   * Duplicates ARE allowed (same user, same original, any number of
#     times) — but `_without_superseded_reposts` (above) makes feeds show
#     only the latest one. To forbid instead, add an `.exists()` check on
#     (user, original_post, is_deleted=False) below and return 409.
#   * Only public, approved, already-published posts can be reposted, and
#     not from a private account the reposter doesn't follow, and not
#     across a block — a repost re-publishes the content to the
#     reposter's own audience, so it must not widen who can see it.
#   * Undo = the existing `DELETE /post/<repost_id>/delete/` (soft-delete;
#     the signal recounts `reposts_count` without it).
class PostRepostAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Repost a post (optionally with a caption)",
        description="Creates a new Post owned by the caller with `original_post` "
                    "set to the target (or to the target's root original if the "
                    "target is itself a repost). Body is optional: "
                    "`{\"repost_caption\": \"...\"}` (max 500 chars).",
        request=RepostRequestSerializer,
        responses={201: OpenApiTypes.OBJECT},
        tags=["Post Repost"],
    )
    @transaction.atomic
    def post(self, request, post_id):
        from user_profile.views import is_blocked_between  # lazy, same as services.py's cross-app imports

        source = get_object_or_404(
            Post.objects.select_related('user', 'original_post__user'), id=post_id, is_deleted=False,
        )
        original = source.original_post if source.original_post_id else source

        if original.is_deleted or original.is_archived or original.moderation_status != 'approved' or original.is_scheduled:
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        if original.visibility != 'public':
            return Response({"success": False, "message": "Only public posts can be reposted"}, status=status.HTTP_403_FORBIDDEN)
        from ..post_options import RESHARE_OFF_CODE, RESHARE_OFF_TEXT, can_reshare_post  # S4b
        if not can_reshare_post(original, request.user):
            return Response({"success": False, "code": RESHARE_OFF_CODE, "message": RESHARE_OFF_TEXT},
                            status=status.HTTP_403_FORBIDDEN)
        if original.user_id != request.user.id:
            if is_blocked_between(request.user, original.user):
                return Response({"success": False, "message": "You can't repost this post"}, status=status.HTTP_403_FORBIDDEN)
            if original.user.is_private and not Follow.objects.filter(
                follower=request.user, following=original.user, status=Follow.Status.ACCEPTED,
            ).exists():
                return Response({"success": False, "message": "This account is private"}, status=status.HTTP_403_FORBIDDEN)

        body = RepostRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        repost = Post.objects.create(
            user=request.user,
            post_type='repost',
            original_post=original,
            repost_caption=body.validated_data.get('repost_caption'),
            # Keeps category-filtered lists working on the repost row; it
            # carries no content/media/hashtags of its own.
            category=original.category,
            subcategory=original.subcategory,
            # S4b: a repost starts from the author's account-level audience (public unless they chose otherwise).
            visibility=_content_defaults.post_visibility_default(request.user, None),
        )

        # Task 3.4: tell the original's owner (batched, never raises).
        notify_post_reposted(repost)

        # `update_reposts_count` (signal) already recounted on create.
        original.refresh_from_db(fields=['reposts_count'])
        return Response({
            "success": True,
            "data": PostListSerializer(repost, context={'request': request}).data,
            "original": {
                "id": str(original.id),
                "reposts_count": original.reposts_count,
                "is_reposted_by_me": True,
            },
        }, status=status.HTTP_201_CREATED)


# ===================== SHARE A CLASS / TEST SERIES TO THE FEED =====================
# POST /post/share-item/  {"type": "classroom"|"testseries", "id": "<uuid>", "caption": "..."}
# Creates an ordinary public Post (post_type='text') that carries the item card.
# Commission is NOT decided here: the card's `ref_code` (see shared_items.py)
# is only present when the item's owner allows commission, and is applied
# through the normal link attribution when someone opens the card.
class PostShareItemAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "post_create"

    @transaction.atomic
    def post(self, request):
        from ..shared_items import SHARE_TYPES, can_share

        shared_type = str(request.data.get("type", "")).strip().lower()
        shared_id = str(request.data.get("id", "")).strip()
        caption = str(request.data.get("caption", "") or "").strip()[:500]
        if shared_type not in SHARE_TYPES or not shared_id:
            return Response({"success": False, "message": "Invalid item."}, status=status.HTTP_400_BAD_REQUEST)
        if not can_share(shared_type, shared_id):
            return Response({"success": False, "message": "This item can't be shared right now."},
                            status=status.HTTP_404_NOT_FOUND)
        post = Post.objects.create(
            user=request.user, post_type='text', content=caption or None, category='education',
            visibility=_content_defaults.post_visibility_default(request.user, None),  # S4b account default
            shared_type=shared_type, shared_id=shared_id,
        )
        return Response(
            {"success": True, "data": PostListSerializer(post, context={'request': request}).data},
            status=status.HTTP_201_CREATED,
        )


# ===================== POST DELETE =====================
# NEW — checklist item 57 ("create/list/delete Post") mentioned delete but
# no such endpoint existed anywhere in urls.py/views.py. Soft-delete only,
# author-or-staff, mirroring CommentDeleteAPIView's pattern exactly
# (is_deleted/deleted_at, never a real row removal).
class PostDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Soft-delete a post", tags=["Post"])
    @transaction.atomic
    def delete(self, request, id):
        from django.shortcuts import get_object_or_404

        post = get_object_or_404(Post, id=id, is_deleted=False)
        if post.user_id != request.user.id and not request.user.is_staff:
            return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)

        post.is_deleted = True
        post.deleted_at = timezone.now()
        post.save(update_fields=["is_deleted", "deleted_at"])
        decrement_posts_count_on_soft_delete(post)
        from ..hashtags import release_post_hashtags   # P1: keep Hashtag.posts_count honest
        release_post_hashtags(post)
        if post.post_type == "repost" and post.original_post_id:
            unnotify_post_reposted(post)  # Task 3.4: undo repost drops it from the open batch

        return Response({"success": True, "message": "Post deleted"}, status=status.HTTP_204_NO_CONTENT)


# ===================== POST EDIT =====================
# NEW — no PATCH/edit endpoint existed anywhere in this app before this
# (urls.py had create/list/detail/delete/repost and nothing else touching
# a post's own fields), and singlepost.dart had no edit UI either. Text/
# category fields only — see PostEditSerializer's docstring for exactly
# what's editable and why media/visibility/post_type are excluded.
class PostEditAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Edit a post's own text/category fields", request=PostEditSerializer, tags=["Post"])
    @transaction.atomic
    def patch(self, request, id):
        post = get_object_or_404(Post, id=id, is_deleted=False)
        if post.user_id != request.user.id:
            return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        from ..moderation_guard import post_locked_by_copyright
        if post_locked_by_copyright(post):
            return Response({"success": False, "message": "This post is under a copyright review and can't be changed."},
                            status=status.HTTP_403_FORBIDDEN)
        if post.post_type == "repost":
            # A repost row carries no content/media of its own to edit —
            # the embedded preview always reflects `original_post` live.
            return Response({"success": False, "message": "Reposts can't be edited"}, status=status.HTTP_400_BAD_REQUEST)

        serializer = PostEditSerializer(post, data=request.data, partial=True, context={"request": request})
        if not serializer.is_valid():
            return Response(
                {"success": False, "message": _first_error_text(serializer.errors) or "Validation failed", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not serializer.validated_data:
            return Response({"success": False, "message": "Nothing to update"}, status=status.HTTP_400_BAD_REQUEST)

        from ..minor_privacy import location_blocked
        if location_blocked(request.user) and "location" in serializer.validated_data:
            serializer.validated_data["location"] = {}  # H23: minors never store a location
        serializer.save()
        return Response({"success": True, "message": "Post updated", "data": PostDetailSerializer(post, context={"request": request}).data})


# ===================== POST VISIBILITY =====================
# NEW — `Post.visibility` was previously set once, at create time
# (PostCreateSerializer), with no way to change it afterwards. Kept as
# its own endpoint rather than folded into PostEditAPIView above: a
# privacy change is a different action from a content edit (doesn't set
# `is_edited` / show an "edited" label — same distinction Instagram makes)
# and has no other field it needs to travel with.
class PostVisibilityAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Change a post's visibility", request=PostVisibilitySerializer, tags=["Post"])
    @transaction.atomic
    def patch(self, request, id):
        post = get_object_or_404(Post, id=id, is_deleted=False)
        if post.user_id != request.user.id:
            return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        from ..moderation_guard import post_locked_by_copyright
        if post_locked_by_copyright(post):
            return Response({"success": False, "message": "This post is under a copyright review and can't be changed."},
                            status=status.HTTP_403_FORBIDDEN)
        if post.post_type == "repost":
            return Response({"success": False, "message": "Reposts can't be edited"}, status=status.HTTP_400_BAD_REQUEST)

        body = PostVisibilitySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        new_visibility = body.validated_data["visibility"]
        from ..audience import class_recording_widen_error
        widen = class_recording_widen_error(post, request.user, new_visibility)
        if widen:
            return Response({"success": False, "code": "class_recording_locked", "message": widen},
                            status=status.HTTP_403_FORBIDDEN)
        if new_visibility != post.visibility:
            post.visibility = new_visibility
            post.save(update_fields=["visibility"])

        return Response({"success": True, "message": "Visibility updated", "data": {"id": str(post.id), "visibility": post.visibility}})


# ===================== PIN / UNPIN (P3-BE) =====================
MAX_PINNED_POSTS = 3


def _pin_state(user, post):
    return {
        "is_pinned": post.is_pinned,
        "pinned_count": Post.objects.filter(user=user, is_pinned=True, is_deleted=False).count(),
        "max_pinned": MAX_PINNED_POSTS,
    }


class PostPinAPIView(APIView):
    """POST /post/<id>/pin/ pins one of MY posts to the top of my profile grid (max 3, idempotent);
    DELETE unpins (idempotent). Owner only."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Pin a post to my profile", tags=["Post"])
    @transaction.atomic
    def post(self, request, post_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        if post.user_id != request.user.id:
            return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        if post.moderation_status != "approved" or post.is_scheduled or post.is_archived:
            return Response({"success": False, "code": "post_not_pinnable",
                             "message": "Only published, approved posts can be pinned."},
                            status=status.HTTP_400_BAD_REQUEST)
        # serialise concurrent pins of the same user so the limit can't be raced past
        User.objects.select_for_update().filter(pk=request.user.pk).first()
        if not post.is_pinned:
            if Post.objects.filter(user=request.user, is_pinned=True, is_deleted=False).count() >= MAX_PINNED_POSTS:
                return Response({"success": False, "code": "pin_limit_reached", "max_pinned": MAX_PINNED_POSTS,
                                 "message": f"You can pin up to {MAX_PINNED_POSTS} posts. Unpin one first."},
                                status=status.HTTP_400_BAD_REQUEST)
            post.is_pinned = True
            post.save(update_fields=["is_pinned"])
        return Response({"success": True, "data": _pin_state(request.user, post)})

    @extend_schema(summary="Unpin a post from my profile", tags=["Post"])
    @transaction.atomic
    def delete(self, request, post_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        if post.user_id != request.user.id:
            return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        if post.is_pinned:
            post.is_pinned = False
            post.save(update_fields=["is_pinned"])
        return Response({"success": True, "data": _pin_state(request.user, post)})


# ===================== POST ARCHIVE (feed card ⋮ menu) =====================
# Archive = "hide it from everyone, keep it". Unlike delete it is reversible, so the app calls it
# straight away and offers Undo (DELETE below). Owner only; idempotent both ways.
#   POST   /post/<id>/archive/   archive (also unpins; leaves posts_count + hashtag counts)
#   DELETE /post/<id>/archive/   restore (counts come back; it is NOT re-pinned)
#   GET    /post/archived/       my archived posts, most recently archived first
# Where archived posts are hidden: home feed, explore, reels, hashtag, profile grid, saved, tagged tab,
# direct link / react / comment (post_hidden_for), reposts of it, leaderboard, doubts.
def _archive_state(post):
    return {"id": str(post.id), "is_archived": post.is_archived, "archived_at": post.archived_at}


class PostArchiveAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_own(self, request, post_id):
        post = get_object_or_404(Post.objects.select_for_update(), id=post_id, is_deleted=False)
        if post.user_id != request.user.id:
            return None, Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        return post, None

    @extend_schema(summary="Archive one of my posts", tags=["Post"])
    @transaction.atomic
    def post(self, request, post_id):
        post, err = self._get_own(request, post_id)
        if err is not None:
            return err
        from ..moderation_guard import post_locked_by_copyright
        if post_locked_by_copyright(post):
            return Response({"success": False, "message": "This post is under a copyright review and can't be changed."},
                            status=status.HTTP_403_FORBIDDEN)
        if post.is_scheduled:
            return Response({"success": False, "code": "post_not_archivable",
                             "message": "A scheduled post can't be archived. Edit or delete the schedule instead."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not post.is_archived:
            post.is_archived = True
            post.archived_at = timezone.now()
            post.is_pinned = False
            post.save(update_fields=["is_archived", "archived_at", "is_pinned"])
            decrement_posts_count_on_soft_delete(post)
            from ..hashtags import release_post_hashtags
            release_post_hashtags(post)
        return Response({"success": True, "data": _archive_state(post)})

    @extend_schema(summary="Restore (unarchive) one of my posts", tags=["Post"])
    @transaction.atomic
    def delete(self, request, post_id):
        post, err = self._get_own(request, post_id)
        if err is not None:
            return err
        if post.is_archived:
            post.is_archived = False
            post.archived_at = None
            post.save(update_fields=["is_archived", "archived_at"])
            from ..signals import increment_posts_count_on_unarchive
            increment_posts_count_on_unarchive(post)
            from ..hashtags import sync_post_hashtags
            sync_post_hashtags(post, old_tags=[])
        return Response({"success": True, "data": _archive_state(post)})


class ArchivedPostsListAPIView(generics.ListAPIView):
    """GET /post/archived/ - my own archived posts, newest archive first (paginated)."""
    permission_classes = [IsAuthenticated]
    serializer_class = PostListSerializer
    pagination_class = StandardResultsSetPagination

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(summary="My archived posts", tags=["Post"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return (
            Post.objects.select_related('user', 'original_post__user')
            .prefetch_related('media', 'original_post__media')
            .filter(user=self.request.user, is_deleted=False, is_archived=True)
            .order_by('-archived_at', '-created_at')
        )


# ===================== TAGGED TAB + TAG MANAGEMENT (P5a-BE) =====================
class PostTaggedAPIView(generics.ListAPIView):
    """GET /post/tagged/<username>/ - posts `username` is tagged in (newest tag first), paginated.
    Each row is a normal post row plus `tag: {x, y}`; `is_hidden` is added for the tab's owner only.
    ?include_hidden=1 (owner only) also lists posts the owner hid from their tab."""
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def list(self, request, username):
        from user_profile.services import blocked_user_ids as _blocked_ids
        from ..audience import filter_posts_visible_to
        from ..models import PostTag

        owner = User.objects.filter(username__iexact=username, is_active=True).first()
        if owner is None:
            return Response({"success": False, "message": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        viewer = request.user
        is_owner = owner.pk == viewer.pk
        following_ids = set(Follow.objects.filter(
            follower=viewer, status=Follow.Status.ACCEPTED).values_list("following_id", flat=True))

        qs = PostTag.objects.none()
        if is_owner or not (owner.is_private and owner.pk not in following_ids) and owner.pk not in _blocked_ids(viewer):
            visible = filter_posts_visible_to(Post.objects.all(), viewer).values("pk")
            qs = PostTag.objects.filter(
                tagged_user=owner, post__in=visible,
                post__is_deleted=False, post__is_archived=False, post__moderation_status="approved", post__is_scheduled=False,
            ).exclude(
                Q(post__user__is_private=True) & ~Q(post__user_id__in=following_ids) & ~Q(post__user_id=viewer.pk)
            ).exclude(post__user_id__in=_blocked_ids(viewer))
            if not (is_owner and request.query_params.get("include_hidden") in ("1", "true", "True")):
                qs = qs.filter(is_hidden=False)
        qs = qs.select_related("post__user", "post__original_post__user").prefetch_related(
            "post__media", "post__original_post__media",
        ).order_by("-created_at", "-id")

        page = self.paginate_queryset(qs)
        tags = list(page) if page is not None else list(qs)
        data = PostListSerializer([t.post for t in tags], many=True, context={"request": request}).data
        rows = []
        for tag, row in zip(tags, data):
            row = dict(row)
            row["tag"] = {"x": tag.x, "y": tag.y}
            if is_owner:
                row["tag"]["is_hidden"] = tag.is_hidden
            rows.append(row)
        if page is not None:
            return self.get_paginated_response(rows)
        return Response(rows)


class PostTagAPIView(APIView):
    """PATCH /post/<id>/tag/ {"hidden": bool} - the tagged user hides/unhides the post on THEIR Tagged tab.
    DELETE /post/<id>/tag/ - remove MY tag (idempotent); the post's author (or staff) may pass ?user_id=<n>
    to remove someone else's."""
    permission_classes = [IsAuthenticated]

    def patch(self, request, post_id):
        from ..models import PostTag
        hidden = request.data.get("hidden")
        if not isinstance(hidden, bool):
            return Response({"success": False, "message": "'hidden' must be true or false."},
                            status=status.HTTP_400_BAD_REQUEST)
        tag = PostTag.objects.filter(post_id=post_id, post__is_deleted=False, tagged_user=request.user).first()
        if tag is None:
            return Response({"success": False, "message": "You are not tagged in this post."},
                            status=status.HTTP_404_NOT_FOUND)
        if tag.is_hidden != hidden:
            tag.is_hidden = hidden
            tag.save(update_fields=["is_hidden"])
        return Response({"success": True, "data": {"hidden": tag.is_hidden}})

    @transaction.atomic
    def delete(self, request, post_id):
        from ..models import PostTag
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        target_id = request.user.pk
        raw = request.query_params.get("user_id")
        if raw not in (None, ""):
            try:
                target_id = int(raw)
            except (TypeError, ValueError):
                return Response({"success": False, "message": "Invalid user_id"}, status=status.HTTP_400_BAD_REQUEST)
            if target_id != request.user.pk and post.user_id != request.user.pk and not request.user.is_staff:
                return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        deleted, _ = PostTag.objects.filter(post=post, tagged_user_id=target_id).delete()
        return Response({"success": True, "data": {"removed": bool(deleted)}})
