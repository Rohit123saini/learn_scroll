
import os
import re
import logging
import mimetypes
from datetime import timedelta

from django.db import transaction
from django.db.models import Q, F, Case, When, IntegerField, FloatField, ExpressionWrapper
from django.utils import timezone
from django.http import StreamingHttpResponse, Http404
from django.conf import settings
from django.contrib.auth import get_user_model

from rest_framework import status, parsers, generics, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.pagination import PageNumberPagination

from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes

from.models import Post, PostMedia, PostView, PostLike, PostSave, PostComment
from.serializers import (
    PostCreateSerializer,
    PostListSerializer,
    PostDetailSerializer,
    PostMediaSerializer,
)
from user_profile.models import Follow

# Local import for reaction
from rest_framework import serializers as drf_serializers

User = get_user_model()
logger = logging.getLogger(__name__)

# ===================== PAGINATION =====================
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

class HomeFeedPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50

# ===================== POST CREATE =====================
class PostCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    serializer_class = PostCreateSerializer

    @extend_schema(
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string', 'maxLength': 255},
                    'content': {'type': 'string'},
                    'category': {
                        'type': 'string',
                        'enum': ['general', 'tech', 'jobs', 'news', 'education',
                                'business', 'entertainment', 'sports', 'lifestyle', 'other']
                    },
                    'post_type': {
                        'type': 'string',
                        'enum': ['text', 'image', 'video', 'document', 'poll', 'article', 'carousel', 'link']
                    },
                    'visibility': {
                        'type': 'string',
                        'enum': ['public', 'connections', 'private']
                    },
                    'hashtags': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Hashtags without #'
                    },
                    'mentioned_user_ids': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'UUIDs of mentioned users'
                    },
                    'metadata': {'type': 'object'},
                    'location': {'type': 'object'},
                    'media_files': {
                        'type': 'array',
                        'items': {'type': 'string', 'format': 'binary'},
                    },
                    'media_types': {
                        'type': 'array',
                        'items': {
                            'type': 'string',
                            'enum': ['image', 'video', 'document', 'audio', 'gif']
                        },
                    }
                },
                'required': ['post_type', 'category']
            }
        },
        responses={201: PostCreateSerializer},
        description='Create post with multiple media files. Max 10 files, 100MB each.'
    )
    @transaction.atomic
    def post(self, request):
        serializer = PostCreateSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            try:
                post = serializer.save(user=request.user)
                User.objects.filter(id=request.user.id).update(posts_count=F('posts_count') + 1)
                logger.info(f'Post created: {post.id} by {request.user.id}')
                output_serializer = PostCreateSerializer(post, context={'request': request})
                return Response({"success": True, "message": "Post created successfully","data": output_serializer.data}, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.error(f'Post creation failed: {e}', exc_info=True)
                return Response({"success": False, "message": "Failed to create post"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({"success": False, "message": "Validation failed","errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

# ===================== HOME FEED =====================
class HomeFeedView(generics.ListAPIView):
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = HomeFeedPagination
    def get_serializer_context(self):
        return {'request': self.request}
    def get_queryset(self):
        request_user = self.request.user
        following_ids = Follow.objects.filter(follower=request_user,status=Follow.Status.ACCEPTED).values_list('following_id', flat=True)
        seven_days_ago = timezone.now() - timedelta(days=7)
        base_qs = Post.objects.select_related('user').prefetch_related('media').filter(is_deleted=False,moderation_status='approved',is_sensitive=False).exclude(user=request_user)
        if following_ids.exists():
            following_posts = base_qs.filter(user_id__in=following_ids,visibility__in=['public', 'connections']).annotate(
                is_recent=Case(When(created_at__gte=seven_days_ago, then=1),default=0,output_field=IntegerField()),
                engagement_score=ExpressionWrapper(F('likes_count')*3.0+F('comments_count')*5.0+F('shares_count')*10.0+F('views_count')*0.1,output_field=FloatField())
            ).order_by('-is_recent', '-engagement_score', '-created_at')
            if following_posts.exists():
                return following_posts
        return base_qs.filter(visibility='public',created_at__gte=seven_days_ago).annotate(
            engagement_score=ExpressionWrapper(F('likes_count')*3.0+F('comments_count')*5.0+F('shares_count')*10.0,output_field=FloatField())
        ).order_by('-engagement_score', '-views_count', '-created_at')

# ===================== USER POSTS LIST =====================
class PostListAPIView(generics.ListAPIView):
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'content', 'hashtags']
    ordering_fields = ['created_at', 'likes_count', 'views_count']
    ordering = ['-created_at']
    def get_serializer_context(self):
        return {'request': self.request}
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
        base_qs = Post.objects.select_related('user').prefetch_related('media').filter(is_deleted=False,moderation_status='approved')
        if not target_user_id or str(request_user.id) == target_user_id:
            return base_qs.filter(user=request_user).order_by('-created_at')
        try:
            target_user = User.objects.get(id=target_user_id)
        except User.DoesNotExist:
            return Post.objects.none()
        is_following = Follow.objects.filter(follower=request_user,following=target_user,status=Follow.Status.ACCEPTED).exists()
        if target_user.is_private and not is_following:
            return Post.objects.none()
        queryset = base_qs.filter(user=target_user)
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
        post_type = self.request.query_params.get('post_type')
        if post_type:
            queryset = queryset.filter(post_type=post_type)
        if is_following:
            return queryset.filter(Q(visibility='public') | Q(visibility='connections')).order_by('-created_at')
        return queryset.filter(visibility='public').order_by('-created_at')

# ===================== POST DETAIL =====================
class PostDetailAPIView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PostDetailSerializer
    queryset = Post.objects.select_related('user').prefetch_related('media','comments__user','comments__replies')
    lookup_field = 'id'
    def get_serializer_context(self):
        return {'request': self.request}
    @extend_schema(description='Get single post with comments and view tracking')
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.is_deleted:
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        if instance.visibility == 'private' and instance.user!= request.user:
            return Response({"success": False, "message": "Post is private"}, status=status.HTTP_403_FORBIDDEN)
        if instance.visibility == 'connections':
            is_following = Follow.objects.filter(follower=request.user,following=instance.user,status=Follow.Status.ACCEPTED).exists()
            if not is_following and instance.user!= request.user:
                return Response({"success": False, "message": "Only connections can view"}, status=status.HTTP_403_FORBIDDEN)
        PostView.objects.get_or_create(post=instance, user=request.user)
        Post.objects.filter(id=instance.id).update(views_count=F('views_count') + 1)
        serializer = self.get_serializer(instance)
        return Response({"success": True,"data": serializer.data})

# ===================== MEDIA SERVE WITH RANGE =====================
def serve_media_with_range(request, path):
    file_path = os.path.join(settings.MEDIA_ROOT, path)
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise Http404("File not found")
    if '..' in path or path.startswith('/'):
        raise Http404("Invalid path")
    content_type, _ = mimetypes.guess_type(file_path)
    content_type = content_type or 'application/octet-stream'
    file_size = os.path.getsize(file_path)
    file_name = os.path.basename(file_path)
    range_header = request.META.get('HTTP_RANGE', '')
    if range_header and (content_type.startswith('video/') or content_type.startswith('audio/')):
        range_match = re.match(r'bytes=(\d+)-(\d*)', range_header)
        if range_match:
            first_byte = int(range_match.group(1))
            last_byte = int(range_match.group(2)) if range_match.group(2) else file_size - 1
            if first_byte >= file_size:
                return StreamingHttpResponse(status=416)
            length = last_byte - first_byte + 1
            def file_gen():
                with open(file_path, 'rb') as f:
                    f.seek(first_byte)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(remaining, 65536))
                        if not chunk:
                            break
                        yield chunk
                        remaining -= len(chunk)
            response = StreamingHttpResponse(file_gen(), status=206, content_type=content_type)
            response['Content-Range'] = f'bytes {first_byte}-{last_byte}/{file_size}'
            response['Content-Length'] = str(length)
            response['Accept-Ranges'] = 'bytes'
            response['Content-Disposition'] = f'inline; filename="{file_name}"'
            response['Cache-Control'] = 'public, max-age=31536000'
            return response
    def file_gen():
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                yield chunk
    response = StreamingHttpResponse(file_gen(), content_type=content_type)
    response['Content-Length'] = str(file_size)
    response['Accept-Ranges'] = 'bytes'
    doc_extensions = ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.rar', '.txt']
    is_doc = any(file_name.lower().endswith(ext) for ext in doc_extensions)
    if content_type == 'application/pdf':
        response['Content-Disposition'] = f'inline; filename="{file_name}"'
    elif is_doc:
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'
        response['Content-Type'] = 'application/octet-stream'
    elif content_type.startswith(('image/', 'video/', 'audio/')):
        response['Content-Disposition'] = f'inline; filename="{file_name}"'
        response['Cache-Control'] = 'public, max-age=31536000'
    else:
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response

# ===================== REACTION API - NEW FUNCTION ADDED =====================
class ReactionRequestSerializer(drf_serializers.Serializer):
    reaction = drf_serializers.ChoiceField(choices=['like','confuse','wrong','imp','explain'])

class PostReactionAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        summary="Toggle Reaction - Like / Unlike",
        description="Like: count +1, Same reaction dubara -> Unlike count -1, Alag reaction -> change",
        request=ReactionRequestSerializer,
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Reactions"]
    )
    def post(self, request, post_id):
        from django.shortcuts import get_object_or_404
        post = get_object_or_404(Post, id=post_id)
        serializer = ReactionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reaction_type = serializer.validated_data['reaction']
        user = request.user
        existing = PostLike.objects.filter(post=post, user=user).first()
        if existing:
            if existing.reaction_type == reaction_type:
                existing.delete() # UNLIKE -> count auto -1 by signal
                status_msg = "unliked"
                my_reaction = None
            else:
                existing.reaction_type = reaction_type
                existing.save() # CHANGE -> signal handle
                status_msg = "changed"
                my_reaction = reaction_type
        else:
            PostLike.objects.create(post=post, user=user, reaction_type=reaction_type) # LIKE -> +1
            status_msg = "liked"
            my_reaction = reaction_type

        post.refresh_from_db()
        return Response({
            "status": status_msg,
            "my_reaction": my_reaction,
            "counts": {
                "like": post.like_count,
                "confuse": post.confuse_count,
                "wrong": post.wrong_count,
                "imp": post.imp_count,
                "explain": post.explain_count,
                "total": post.likes_count,
            }
        })

    @extend_schema(summary="Get Reaction Counts", tags=["Post Reactions"])
    def get(self, request, post_id):
        from django.shortcuts import get_object_or_404
        post = get_object_or_404(Post, id=post_id)
        my_reaction = None
        if request.user.is_authenticated:
            obj = PostLike.objects.filter(post=post, user=request.user).first()
            if obj:
                my_reaction = obj.reaction_type
        return Response({
            "post_id": str(post.id),
            "counts": {
                "like": post.like_count,
                "confuse": post.confuse_count,
                "wrong": post.wrong_count,
                "imp": post.imp_count,
                "explain": post.explain_count,
                "total": post.likes_count,
            },
            "my_reaction": my_reaction
        })