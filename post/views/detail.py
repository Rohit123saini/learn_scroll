# post/views/detail.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from django.db.models import (
    F,
    Q,
)
from user_profile.models import Follow
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from ..models import (
    Post,
    PostView,
)
from ..serializers import (
    PostDetailSerializer,
    PostListSerializer,
)
from rest_framework.response import Response
from rest_framework import (
    filters,
    generics,
    status,
)
from rest_framework.decorators import permission_classes
from ._common import (
    StandardResultsSetPagination,
    User,
)
from .feed import _without_superseded_reposts


# ===================== USER POSTS LIST =====================
class PostListAPIView(generics.ListAPIView):
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'content', 'hashtags']
    ordering_fields = ['created_at', 'likes_count', 'views_count']
    ordering = ['-is_pinned', '-created_at']  # P3: pinned posts first; an explicit ?ordering= overrides this
    def get_serializer_context(self):
        return {'request': self.request}
    def filter_queryset(self, queryset):
        # P2: ?sort=latest|popular - pinned posts stay on top either way; an explicit ?ordering= still wins.
        qs = super().filter_queryset(queryset)
        if self.request.query_params.get('ordering'):
            return qs
        from ..profile_tabs import order_profile_posts
        return order_profile_posts(qs, self.request.query_params)
    @extend_schema(
        parameters=[
            OpenApiParameter(name='target_user_id', type=str, description='User UUID'),
            OpenApiParameter(name='category', type=str, description='Filter by category'),
            OpenApiParameter(name='post_type', type=str, description='Filter by type'),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
    def get_queryset(self):
        request_user = self.request.user
        target_user_id = self.request.query_params.get('target_user_id')
        base_qs = _without_superseded_reposts(Post.objects.select_related('user', 'original_post__user').prefetch_related('media', 'original_post__media').filter(is_deleted=False,is_archived=False,moderation_status='approved',is_scheduled=False))
        from ..profile_tabs import filter_profile_posts  # P2
        if not target_user_id or str(request_user.id) == target_user_id:
            return filter_profile_posts(base_qs.filter(user=request_user), self.request.query_params).order_by('-created_at')
        try:
            target_user = User.objects.get(id=target_user_id)
        except User.DoesNotExist:
            return Post.objects.none()
        # Block (either direction) => the grid is empty, same as "no such user".
        from user_profile.views import is_blocked_between
        if is_blocked_between(request_user, target_user):
            return Post.objects.none()
        is_following = Follow.objects.filter(follower=request_user,following=target_user,status=Follow.Status.ACCEPTED).exists()
        if target_user.is_private and not is_following:
            return Post.objects.none()
        queryset = filter_profile_posts(base_qs.filter(user=target_user), self.request.query_params)  # P2: post_type list / kind / category
        if is_following:
            return queryset.filter(Q(visibility='public') | Q(visibility='connections')).order_by('-created_at')
        return queryset.filter(visibility='public').order_by('-created_at')

# ===================== POST DETAIL =====================
class PostDetailAPIView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PostDetailSerializer
    queryset = Post.objects.select_related('user', 'original_post__user').prefetch_related('media','comments__user','comments__replies','original_post__media')
    lookup_field = 'id'
    def get_serializer_context(self):
        return {'request': self.request}
    @extend_schema(description='Get single post with comments and view tracking')
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        from ..moderation_guard import post_hidden_for
        if post_hidden_for(request.user, instance):
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        if instance.user_id != request.user.id:
            from user_profile.views import is_blocked_between
            if is_blocked_between(request.user, instance.user):
                return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        if instance.visibility == 'private' and instance.user!= request.user:
            return Response({"success": False, "message": "Post is private"}, status=status.HTTP_403_FORBIDDEN)
        if instance.visibility == 'connections':
            is_following = Follow.objects.filter(follower=request.user,following=instance.user,status=Follow.Status.ACCEPTED).exists()
            if not is_following and instance.user!= request.user:
                return Response({"success": False, "message": "Only connections can view"}, status=status.HTTP_403_FORBIDDEN)
        if instance.visibility in ('close_friends', 'class_campus') and instance.user_id != request.user.id:
            # P1 audiences. 404 (not 403) so the post's existence is not leaked.
            from ..audience import can_view_post
            if not can_view_post(instance, request.user):
                return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        # FIX (post_app.md §14 issue #1): `views_count` used to increment
        # unconditionally on every request, even repeat visits by the same
        # user, even though `PostView` itself was already correctly deduped
        # via get_or_create. Now the counter only moves on a genuinely new
        # (post, user) pair — "unique viewers" semantics, matching what
        # `PostView`'s own uniqueness already implies.
        #
        # A row may already exist because the feed reported the post as
        # "seen" (PostSeenBatchAPIView, `is_counted=False`). The first real
        # open still has to count once, so promote that row atomically —
        # the conditional UPDATE guarantees only one concurrent request
        # wins the promotion, so views_count can't double-increment.
        view, is_new_view = PostView.objects.get_or_create(post=instance, user=request.user)
        if is_new_view:
            should_count = True
        else:
            should_count = PostView.objects.filter(pk=view.pk, is_counted=False).update(is_counted=True) > 0
        if should_count:
            Post.objects.filter(id=instance.id).update(views_count=F('views_count') + 1)
        serializer = self.get_serializer(instance)
        return Response({"success": True,"data": serializer.data})
