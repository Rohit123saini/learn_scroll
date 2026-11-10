"""
post/reply_reel_views.py - the picker list for "reply to a comment with a reel".

    GET /post/reels/mine/     my reels that CAN be attached to a reply, newest first (paginated, ?page= / ?page_size=)

Why a separate endpoint (and not `GET /post/list/`): the "own posts" branch of PostListAPIView ignores `?post_type=` and
returns every visibility (private / followers) and every kind of post, so the picker would have to filter on the phone,
page by page, and could still show a reel the server then refuses. This list applies EXACTLY the rules of
`reply_reels.resolve_reply_reel`, so whatever the picker shows is accepted by `POST /post/comment/create/`:
my own, video, not deleted / archived / scheduled, approved, public, and not a repost of somebody else's video.
Read-only, no migration.
"""
from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from .models import Post
from .serializers import PostListSerializer
from .views import StandardResultsSetPagination


class MyPublicReelsAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PostListSerializer
    pagination_class = StandardResultsSetPagination

    def get_serializer_context(self):
        return {"request": self.request}

    @extend_schema(summary="My public reels (picker for replying to a comment with a reel)", tags=["Post"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return (
            Post.objects.select_related("user", "original_post__user")
            .prefetch_related("media", "original_post__media")
            .filter(
                user=self.request.user,
                post_type="video",
                is_deleted=False,
                is_archived=False,
                is_scheduled=False,
                moderation_status="approved",
                visibility="public",
                original_post__isnull=True,
            )
            .order_by("-created_at")
        )
