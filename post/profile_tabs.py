"""
post/profile_tabs.py - P2 (profile tabs).

1. Profile grid filters + sort, shared by every tab of GET /post/list/:
     ?post_type=document,pdf   (comma list)      ?kind=resource | reel | post | doubt | poll
     ?category=...                               ?sort=latest (default) | popular
   Pinned posts (max 3) stay on top in BOTH sorts - "popular" only re-orders the rest.

2. Saved collections ("folders" inside my saved posts) with privacy:
     GET/POST  /post/saved/collections/                     list (with counts) / create
     PATCH/DELETE /post/saved/collections/<id>/             rename + privacy / delete (saves go back to "default")
     PUT       /post/saved/<post_id>/collection/            move one saved post to a folder ("default" = no folder)
     GET       /post/collections/of/<username>/             someone's PUBLIC folders (never the private ones)
     GET       /post/collections/of/<username>/<name>/posts/   the posts of one public folder

   `PostSave.collection_name` stays the link (plain string), so old saves keep working. A private folder (the
   default) is visible to its owner only. A public folder shows only the posts the VIEWER may see anyway (author's
   private account / audience / blocks are re-checked per post), so making a folder public can never leak a post.

NOT RUN. Tests: post/tests_profile_tabs.py
"""
import re

from django.db import IntegrityError, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Post, PostSave, SavedCollection

DEFAULT_COLLECTION = "default"
MAX_COLLECTIONS = 30
MAX_COLLECTION_NAME = 40
RESERVED_NAMES = {"default", "all"}
VALID_KINDS = {k for k, _ in Post.KIND_CHOICES}
VALID_SORTS = ("latest", "popular")


# ---------------------------------------------------------------- profile grid filters / sort
def filter_profile_posts(qs, params):
    """Narrow a profile post queryset by ?post_type / ?kind / ?category. Unknown values give an empty page, never a 500."""
    post_type = (params.get("post_type") or "").strip()
    if post_type:
        types = [t.strip() for t in post_type.split(",") if t.strip()][:12]
        qs = qs.filter(post_type__in=types)
    kind = (params.get("kind") or "").strip()
    if kind:
        qs = qs.filter(kind=kind) if kind in VALID_KINDS else qs.none()
    category = (params.get("category") or "").strip()
    if category:
        qs = qs.filter(category=category)
    return qs


def sort_value(params) -> str:
    v = (params.get("sort") or "latest").strip().lower()
    return v if v in VALID_SORTS else "latest"


def order_profile_posts(qs, params):
    """Pinned first, then latest (default) or popular. `-id` makes page boundaries stable when two rows tie."""
    if sort_value(params) == "popular":
        return qs.order_by("-is_pinned", "-likes_count", "-views_count", "-created_at", "-id")
    return qs.order_by("-is_pinned", "-created_at", "-id")


# ---------------------------------------------------------------- collection helpers
def normalize_collection_name(raw) -> str:
    return " ".join(str(raw or "").split())


def collection_name_error(name: str):
    """(code, message) or None."""
    if not name:
        return "empty", "Give the folder a name."
    if len(name) > MAX_COLLECTION_NAME:
        return "too_long", f"At most {MAX_COLLECTION_NAME} characters."
    if re.search(r"[<>]", name):
        return "bad_chars", "Folder names cannot contain < or >."
    if name.lower() in RESERVED_NAMES:
        return "reserved", "This name is reserved."
    return None


def _err(code, message, http=status.HTTP_400_BAD_REQUEST, **extra):
    return Response({"success": False, "code": code, "message": message, **extra}, status=http)


def _blocked_ids(user):
    from user_profile.services import blocked_user_ids

    return blocked_user_ids(user)


def visible_saves(user):
    """MY saves that still show up: live, approved, not scheduled / archived, author not blocked. One definition for
    the Saved tab, the folder counts and the public folder view, so the numbers always match the grid."""
    return PostSave.objects.filter(
        user=user,
        post__is_deleted=False,
        post__is_archived=False,
        post__moderation_status="approved",
        post__is_scheduled=False,
    ).exclude(post__user_id__in=_blocked_ids(user))


def saved_posts_queryset(user, collection_name=None):
    """Posts I saved, newest save first. `collection_name` None = everything; 'default' = the un-foldered ones.
    (Built from PostSave, not from a join on Post.saved_by: that join also matched OTHER people's saves of the same
    post whenever a collection filter was added.)"""
    saves = visible_saves(user)
    if collection_name:
        saves = saves.filter(collection_name=collection_name)
    saved_at = Subquery(saves.filter(post=OuterRef("pk")).values("created_at")[:1])
    return (
        Post.objects.select_related("user", "original_post__user")
        .prefetch_related("media", "original_post__media")
        .filter(id__in=saves.values("post_id"))
        .annotate(saved_at=saved_at)
        .order_by("-saved_at", "-id")
    )


def collection_exists(user, name: str) -> bool:
    return name == DEFAULT_COLLECTION or SavedCollection.objects.filter(user=user, name=name).exists()


def _collection_row(c, count):
    return {
        "id": str(c.id),
        "name": c.name,
        "is_private": c.is_private,
        "count": count,
        "created_at": c.created_at.isoformat(),
    }


# ---------------------------------------------------------------- my folders
class SavedCollectionListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        counts = dict(
            visible_saves(request.user).values_list("collection_name").annotate(n=Count("id")).values_list("collection_name", "n")
        )
        rows = list(SavedCollection.objects.filter(user=request.user))
        data = [_collection_row(c, counts.get(c.name, 0)) for c in rows]
        known = {c.name for c in rows}
        # Old saves may carry a free-text collection_name from before folders existed: count them in "default"
        # so nothing disappears from the "All" total.
        default_count = counts.get(DEFAULT_COLLECTION, 0) + sum(n for name, n in counts.items() if name not in known and name != DEFAULT_COLLECTION)
        return Response({
            "success": True,
            "data": {
                "collections": data,
                "default_count": default_count,
                "total": sum(counts.values()),
                "max": MAX_COLLECTIONS,
            },
        })

    def post(self, request):
        name = normalize_collection_name(request.data.get("name"))
        bad = collection_name_error(name)
        if bad:
            return _err(*bad)
        is_private = _as_bool(request.data.get("is_private"), default=True)
        with transaction.atomic():
            mine = SavedCollection.objects.select_for_update().filter(user=request.user)
            if mine.count() >= MAX_COLLECTIONS:
                return _err("limit_reached", f"You can have up to {MAX_COLLECTIONS} folders.", max=MAX_COLLECTIONS)
            if mine.filter(name__iexact=name).exists():
                return _err("exists", "You already have a folder with this name.", status.HTTP_409_CONFLICT)
            try:
                c = SavedCollection.objects.create(user=request.user, name=name, is_private=is_private)
            except IntegrityError:
                return _err("exists", "You already have a folder with this name.", status.HTTP_409_CONFLICT)
        return Response({"success": True, "data": _collection_row(c, 0)}, status=status.HTTP_201_CREATED)


class SavedCollectionDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, request, pk):
        return get_object_or_404(SavedCollection, pk=pk, user=request.user)

    def patch(self, request, pk):
        c = self._get(request, pk)
        old_name = c.name
        with transaction.atomic():
            if "name" in request.data:
                name = normalize_collection_name(request.data.get("name"))
                bad = collection_name_error(name)
                if bad:
                    return _err(*bad)
                if name.lower() != old_name.lower() and SavedCollection.objects.filter(user=request.user, name__iexact=name).exists():
                    return _err("exists", "You already have a folder with this name.", status.HTTP_409_CONFLICT)
                c.name = name
            if "is_private" in request.data:
                c.is_private = _as_bool(request.data.get("is_private"), default=c.is_private)
            c.save()
            if c.name != old_name:
                PostSave.objects.filter(user=request.user, collection_name=old_name).update(collection_name=c.name)
        count = visible_saves(request.user).filter(collection_name=c.name).count()
        return Response({"success": True, "data": _collection_row(c, count)})

    def delete(self, request, pk):
        """Deleting a folder NEVER deletes saved posts - they go back to 'default'."""
        c = self._get(request, pk)
        with transaction.atomic():
            moved = PostSave.objects.filter(user=request.user, collection_name=c.name).update(collection_name=DEFAULT_COLLECTION)
            c.delete()
        return Response({"success": True, "data": {"moved": moved}})


class SavedPostMoveAPIView(APIView):
    """PUT /post/saved/<post_id>/collection/  {collection_name}  - move one saved post to a folder."""

    permission_classes = [IsAuthenticated]

    def put(self, request, post_id):
        name = normalize_collection_name(request.data.get("collection_name")) or DEFAULT_COLLECTION
        if not collection_exists(request.user, name):
            return _err("collection_not_found", "That folder does not exist.", status.HTTP_404_NOT_FOUND)
        updated = PostSave.objects.filter(user=request.user, post_id=post_id).update(collection_name=name)
        if not updated:
            return _err("not_saved", "Save the post first.", status.HTTP_404_NOT_FOUND)
        return Response({"success": True, "data": {"post_id": str(post_id), "collection_name": name}})


# ---------------------------------------------------------------- someone else's public folders
def _profile_gate(viewer, owner):
    """None = the viewer may look; otherwise a code. Blocked is reported exactly like 'no such user'."""
    from user_profile.models import Follow
    from user_profile.views import is_blocked_between

    if viewer.id == owner.id:
        return None
    if is_blocked_between(viewer, owner):
        return "not_found"
    if owner.is_private and not Follow.objects.filter(follower=viewer, following=owner, status=Follow.Status.ACCEPTED).exists():
        return "private_account"
    return None


class UserPublicCollectionsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        from django.contrib.auth import get_user_model

        owner = get_user_model().objects.filter(username__iexact=username, is_active=True).first()
        if owner is None:
            return _err("not_found", "No such user.", status.HTTP_404_NOT_FOUND)
        gate = _profile_gate(request.user, owner)
        if gate == "not_found":
            return _err("not_found", "No such user.", status.HTTP_404_NOT_FOUND)
        if gate == "private_account":
            return Response({"success": True, "data": {"collections": [], "locked": True}})
        rows = SavedCollection.objects.filter(user=owner, is_private=False)
        out = []
        for c in rows:
            n = _public_collection_posts(request.user, owner, c.name).count()
            if n:  # an empty / fully-hidden public folder is not worth a chip
                out.append(_collection_row(c, n))
        for row in out:
            row.pop("is_private", None)  # visitors only ever see public ones
        return Response({"success": True, "data": {"collections": out, "locked": False}})


def _public_collection_posts(viewer, owner, name):
    """The owner's saved posts in folder `name`, cut down to what `viewer` may see. Per post: not blocked either
    way, author's account not private (unless viewer follows / is the author), audience public or connections."""
    from user_profile.models import Follow

    qs = saved_posts_queryset(owner, name).exclude(user_id__in=_blocked_ids(viewer))
    followed = Follow.objects.filter(follower=viewer, status=Follow.Status.ACCEPTED).values("following_id")
    qs = qs.filter(Q(user=viewer) | Q(user__is_private=False) | Q(user_id__in=followed))
    qs = qs.filter(
        Q(user=viewer) | Q(visibility="public") | (Q(visibility="connections") & Q(user_id__in=followed))
    )
    return qs


class _Pagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class UserPublicCollectionPostsAPIView(ListAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = _Pagination

    def get_serializer_class(self):
        from .serializers import PostListSerializer

        return PostListSerializer

    def get_serializer_context(self):
        return {"request": self.request}

    def get_queryset(self):
        from django.contrib.auth import get_user_model
        from rest_framework.exceptions import NotFound

        owner = get_user_model().objects.filter(username__iexact=self.kwargs["username"], is_active=True).first()
        if owner is None or _profile_gate(self.request.user, owner) is not None:
            return Post.objects.none()
        name = normalize_collection_name(self.kwargs["name"])
        folder = SavedCollection.objects.filter(user=owner, name=name).first()
        if folder is None or (folder.is_private and owner.id != self.request.user.id):
            raise NotFound("No such folder.")  # private looks the same as missing
        return _public_collection_posts(self.request.user, owner, folder.name)


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")
