"""
post/reel_manage_views.py - managing a reel (any post) AFTER it was created, owner only.

    GET   /post/scheduled/?type=video   my scheduled posts, soonest first (type=video -> reels only)
    PATCH /post/<id>/schedule/          reschedule a scheduled post   {scheduled_at, scheduled_timezone?}
    POST  /post/<id>/publish-now/       publish a scheduled post right away
    POST  /post/<id>/reel-cover/        replace the cover of a published (or scheduled) reel - multipart `reel_cover`

Caption / hashtag edit after publishing already exists (`PATCH /post/<id>/edit/`, stamps the "Edited" mark);
archive / restore (`/post/<id>/archive/`) and delete (`/post/<id>/delete/`) too - the app reuses them.
Nothing here needs a migration (a cover is just PostMedia.thumbnail).
"""
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Post
from .moderation_guard import post_locked_by_copyright
from .serializers import PostListSerializer, _ScheduleDateTimeField
from .views import StandardResultsSetPagination


def _own_post(request, post_id):
    post = get_object_or_404(Post.objects.select_for_update(), id=post_id, is_deleted=False)
    if post.user_id != request.user.id:
        return None, Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
    if post_locked_by_copyright(post):
        return None, Response({"success": False, "message": "This post is under a copyright review and can't be changed."},
                              status=status.HTTP_403_FORBIDDEN)
    return post, None


def _schedule_state(post):
    return {
        "id": str(post.id),
        "is_scheduled": post.is_scheduled,
        "scheduled_at": post.published_at if post.is_scheduled else None,
        "scheduled_timezone": post.scheduled_timezone if post.is_scheduled else "",
    }


class ScheduledPostsListAPIView(generics.ListAPIView):
    """GET /post/scheduled/ - my not-yet-published posts, the one that goes live first on top."""
    permission_classes = [IsAuthenticated]
    serializer_class = PostListSerializer
    pagination_class = StandardResultsSetPagination

    def get_serializer_context(self):
        return {"request": self.request}

    @extend_schema(summary="My scheduled posts / reels", tags=["Post"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        qs = (
            Post.objects.select_related("user", "original_post__user")
            .prefetch_related("media", "original_post__media")
            .filter(user=self.request.user, is_deleted=False, is_scheduled=True)
        )
        if self.request.query_params.get("type") == "video":
            qs = qs.filter(post_type="video")
        return qs.order_by("published_at", "created_at")


class _RescheduleSerializer(serializers.Serializer):
    scheduled_at = _ScheduleDateTimeField()
    scheduled_timezone = serializers.CharField(required=False, allow_blank=True, max_length=64)

    def validate_scheduled_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("Scheduled time must be in the future.")
        return value

    def validate_scheduled_timezone(self, value):
        from .post_options import scheduled_zone_label
        return scheduled_zone_label(value)


class PostScheduleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Change the publish time of one of my scheduled posts", request=_RescheduleSerializer,
                   tags=["Post"])
    @transaction.atomic
    def patch(self, request, post_id):
        post, err = _own_post(request, post_id)
        if err is not None:
            return err
        if not post.is_scheduled:
            return Response({"success": False, "code": "not_scheduled", "message": "This post is already published."},
                            status=status.HTTP_400_BAD_REQUEST)
        body = _RescheduleSerializer(data=request.data)
        if not body.is_valid():
            first = next(iter(body.errors.values()))
            msg = first[0] if isinstance(first, (list, tuple)) and first else "Validation failed"
            return Response({"success": False, "message": str(msg), "errors": body.errors}, status=status.HTTP_400_BAD_REQUEST)
        post.published_at = body.validated_data["scheduled_at"]
        post.scheduled_timezone = body.validated_data.get("scheduled_timezone", "")
        post.save(update_fields=["published_at", "scheduled_timezone"])
        return Response({"success": True, "data": _schedule_state(post)})


class PostPublishNowAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Publish one of my scheduled posts right now", tags=["Post"])
    def post(self, request, post_id):
        with transaction.atomic():
            post, err = _own_post(request, post_id)
            if err is not None:
                return err
            if not post.is_scheduled:
                return Response({"success": True, "data": _schedule_state(post)})  # idempotent
            post.published_at = timezone.now()
            post.save(update_fields=["published_at"])
        # Same code path as the every-minute beat job (claims atomically, fans out notifications once).
        from .tasks import publish_scheduled_posts
        publish_scheduled_posts()
        post.refresh_from_db()
        return Response({"success": True, "data": _schedule_state(post)})


class ReelCoverAPIView(APIView):
    """POST /post/<id>/reel-cover/ (multipart `reel_cover`: jpg / png / webp, <= 5 MB) - new cover of my video post.
    The old cover file is removed from storage only after the new one is saved."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(summary="Replace the cover of one of my reels", tags=["Post"])
    @transaction.atomic
    def post(self, request, post_id):
        post, err = _own_post(request, post_id)
        if err is not None:
            return err
        if post.post_type != "video":
            return Response({"success": False, "code": "not_a_reel", "message": "Only a reel has a cover."},
                            status=status.HTTP_400_BAD_REQUEST)
        cover = request.FILES.get("reel_cover")
        if cover is None:
            return Response({"success": False, "code": "cover_missing", "message": "reel_cover file is required."},
                            status=status.HTTP_400_BAD_REQUEST)
        media = post.media.filter(media_type="video").order_by("display_order").first()
        old_name = media.thumbnail.name if media is not None and media.thumbnail else None
        from .services import attach_reel_cover
        if not attach_reel_cover(post, cover):
            return Response({"success": False, "code": "cover_invalid",
                             "message": "Use a JPG, PNG or WebP image up to 5 MB."}, status=status.HTTP_400_BAD_REQUEST)
        media.refresh_from_db()
        if old_name and old_name != media.thumbnail.name:
            try:
                media.thumbnail.storage.delete(old_name)
            except Exception:  # an orphaned old file is harmless; never fail the edit over it
                pass
        # A cover change is not a caption edit: no "Edited" mark, but bump updated_at so clients refetch.
        Post.objects.filter(pk=post.pk).update(updated_at=timezone.now())
        try:
            url = request.build_absolute_uri(media.thumbnail.url)
        except ValueError:
            url = None
        return Response({"success": True, "data": {"id": str(post.id), "thumbnail": url}})
