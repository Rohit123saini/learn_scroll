#post/views.py
import os
import re
import uuid
import logging
import mimetypes
from collections import Counter
from datetime import timedelta

from django.db import transaction
from django.db.models import Q, F, Case, When, IntegerField, FloatField, ExpressionWrapper
from django.utils import timezone
from django.utils.text import slugify
from django.http import StreamingHttpResponse, Http404
from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404

from rest_framework import status, parsers, generics, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.pagination import PageNumberPagination

from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes

from.models import Post, PostMedia, PostView, PostLike, PostSave, PostComment, Story, StoryView, PostChunkedUpload
from.serializers import (
    PostCreateSerializer,
    PostListSerializer,
    PostDetailSerializer,
    PostMediaSerializer,
    PostSaveSerializer,
    StoryCreateSerializer,
    StorySerializer,
    ReactionRequestSerializer,
)
from.signals import decrement_posts_count_on_soft_delete
from .services import notify_post_liked, save_uploaded_chunk, assemble_chunks, list_received_chunks, CHUNK_UPLOAD_MAX_SIZE
from user_profile.models import Follow

# TASK 4 — freesound_music_search's HTTP client. Guarded the same way
# Services.py guards its `core` app import: if `requests` genuinely isn't
# installed in some environment, the music-search endpoint degrades to a
# clean 503 instead of an ImportError crashing this whole module (and
# every other view in it) at import time.
try:
    import requests
except ImportError:  # pragma: no cover - requests should be a real dependency
    requests = None

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
                # 🔥 FIX: posts_count increment moved to signals.py
                # (increment_posts_count_on_create, post_save on Post).
                # Doing it here too would double-count — see signals.py's
                # module docstring for why the signal is now the single
                # source of truth for this counter.
                logger.info(f'Post created: {post.id} by {request.user.id}')
                output_serializer = PostCreateSerializer(post, context={'request': request})
                return Response({"success": True, "message": "Post created successfully","data": output_serializer.data}, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.error(f'Post creation failed: {e}', exc_info=True)
                return Response({"success": False, "message": "Failed to create post"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({"success": False, "message": "Validation failed","errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

# ===================== CATEGORY TAXONOMY (TASK 4) =====================
# GET /post/categories/ — ApiService.getCategoryTaxonomy() (api_service.dart)
# was already calling this exact path and reading response['data']; nothing
# backing it existed, so both composers' category pickers 404'd on load.
@api_view(['GET'])
@permission_classes([AllowAny])
@extend_schema(summary="Category / subcategory taxonomy", tags=["Post"])
def category_taxonomy(request):
    """Returns `Post.CATEGORY_CHOICES` (models.py) as JSON — the only
    category taxonomy that actually exists in this app today, so this is
    a straight passthrough, not a new source of truth.

    ⚠️ Subcategories: the Flutter client (createPost / initPostChunkedUpload
    in api_service.dart, and this function's own name) already sends and
    expects a `subcategory` value plus a category→subcategory map, but
    there is NO subcategory model, choices constant, or fixture anywhere
    in this app — `Post` has no `subcategory` field at all (models.py).
    This endpoint can't hand back a taxonomy that doesn't exist
    server-side, so `subcategories` below is an empty-per-category stub,
    shaped the way the client expects so it doesn't crash, not real data
    — every category picker's subcategory dropdown will just show no
    options until product decides the actual subcategory list and a real
    `Post.subcategory` field (+ choices) gets added. That's the same gap
    already flagged in models.py's PostChunkedUpload docstring and
    serializers.py's PostCreateSerializer — one missing feature, not
    three separate bugs — so wire all three up together.
    """
    categories = [{"value": value, "label": label} for value, label in Post.CATEGORY_CHOICES]
    subcategories = {value: [] for value, _ in Post.CATEGORY_CHOICES}
    return Response({
        "success": True,
        "data": {
            "categories": categories,
            "subcategories": subcategories,
        },
    })


# ===================== FREESOUND MUSIC SEARCH (TASK 4) =====================
# GET /post/music/search/?q=&page= — ApiService.searchFreesoundMusic()
# (api_service.dart) was already calling this exact path; nothing backing
# it existed, so the Music tab in media_edit_screen.dart/auto_edit_screen.dart
# couldn't search. Thin server-side proxy so settings.FREESOUND_API_KEY
# (already configured via the FREESOUND_API_KEY env var — settings.py)
# never has to ship inside the app.
FREESOUND_SEARCH_URL = "https://freesound.org/apiv2/search/text/"


@api_view(['GET'])
@permission_classes([AllowAny])
@extend_schema(
    summary="Search Freesound for CC0 music",
    parameters=[
        OpenApiParameter(name="q", type=OpenApiTypes.STR, required=True),
        OpenApiParameter(name="page", type=OpenApiTypes.INT, required=False),
    ],
    tags=["Post"],
)
def freesound_music_search(request):
    """Matches searchFreesoundMusic()'s contract exactly:
    {"success", "data": {"results": [...]}}, each result
    {id, name, artist, duration, preview_url, license, tags}.

    Only CC0 ("Creative Commons 0" / public-domain) results are requested
    from Freesound — per that Dart method's own comment ("sirf
    CC0-licensed (copyright-free) results deta hai"). CC0 needs no
    attribution, so it's the only license this app's Music tab can safely
    let someone attach to a post without a credit-line feature to go
    with it — anything else (CC-BY etc.) would need attribution UI this
    app doesn't have yet.
    """
    if requests is None:
        logger.error("Freesound search unavailable: the 'requests' package isn't installed")
        return Response({"success": False, "message": "Music search unavailable"}, status=503)

    api_key = getattr(settings, 'FREESOUND_API_KEY', None)
    if not api_key:
        return Response({"success": False, "message": "Music search is not configured"}, status=503)

    query = (request.query_params.get('q') or '').strip()
    if not query:
        return Response({"success": False, "message": "q is required"}, status=400)

    try:
        page = int(request.query_params.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)

    try:
        resp = requests.get(
            FREESOUND_SEARCH_URL,
            params={
                'query': query,
                'token': api_key,
                'page': page,
                # CC0 only — see docstring above for why.
                'filter': 'license:"Creative Commons 0"',
                'fields': 'id,name,username,duration,previews,license,tags',
            },
            timeout=8,
        )
    except requests.RequestException as e:
        logger.error(f'Freesound search request failed: {e}')
        return Response({"success": False, "message": "Music search unavailable"}, status=503)

    if resp.status_code != 200:
        logger.error(f'Freesound returned {resp.status_code}: {resp.text[:300]}')
        return Response({"success": False, "message": "Music search failed"}, status=502)

    try:
        payload = resp.json()
    except ValueError:
        logger.error('Freesound returned non-JSON response')
        return Response({"success": False, "message": "Music search failed"}, status=502)

    results = []
    for item in payload.get('results', []):
        previews = item.get('previews') or {}
        preview_url = previews.get('preview-hq-mp3') or previews.get('preview-lq-mp3')
        results.append({
            "id": item.get('id'),
            "name": item.get('name'),
            "artist": item.get('username'),
            "duration": item.get('duration'),
            "preview_url": preview_url,
            "license": "CC0",
            "tags": item.get('tags') or [],
        })

    return Response({
        "success": True,
        "data": {
            "results": results,
            "count": payload.get('count', len(results)),
            # Freesound's own `next` is a full API URL (with the token in
            # it) — never pass that through to the client. Just tell it
            # whether another page exists; it already knows how to ask
            # for `page + 1` itself (searchFreesoundMusic's `page` param).
            "next_page": (page + 1) if payload.get('next') else None,
        },
    })


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
        # FIX (post_app.md §14 issue #1): `views_count` used to increment
        # unconditionally on every request, even repeat visits by the same
        # user, even though `PostView` itself was already correctly deduped
        # via get_or_create. Now the counter only moves on a genuinely new
        # (post, user) pair — "unique viewers" semantics, matching what
        # `PostView`'s own uniqueness already implies.
        _, is_new_view = PostView.objects.get_or_create(post=instance, user=request.user)
        if is_new_view:
            Post.objects.filter(id=instance.id).update(views_count=F('views_count') + 1)
        serializer = self.get_serializer(instance)
        return Response({"success": True,"data": serializer.data})

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

        return Response({"success": True, "message": "Post deleted"}, status=status.HTTP_204_NO_CONTENT)


# ===================== CHUNKED UPLOAD — POST (TASK 3 / TASK 4) =====================
# Fixes the 404: api_service.dart's initPostChunkedUpload/
# completePostChunkedUpload hit /post/chunked/init/ and /post/chunked/
# complete/, and getChunkedUploadStatus hits /post/chunked/status/<id>/ —
# none of these routes existed before this (see urls.py). This is Option B
# from the task: dedicated post routes/views, backed by their own
# `PostChunkedUpload` model (models.py) rather than overloading the
# comment-shaped `ChunkedUpload`, sharing the actual chunk-storage
# mechanics with the comment flow via Services.save_uploaded_chunk /
# assemble_chunks / list_received_chunks / CHUNK_UPLOAD_MAX_SIZE.
#
# The chunk-upload step itself is NOT duplicated here on purpose:
# api_service.dart's uploadPostChunk() deliberately posts to the existing
# /post/comment/chunked/chunk/ route (comment_view.py's
# chunked_upload_chunk, now updated to look up either ChunkedUpload or
# PostChunkedUpload by upload_id) — see that file's own comment on why.
# Only init/complete/status are post-specific, because only they touch
# post-creation fields.

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser])
def post_chunked_upload_init(request):
    """Start a chunked (large-video) post upload. Stores the post-creation
    payload (title/content/category/... — same fields api_service.dart's
    non-chunked createPost() sends) on a PostChunkedUpload row, to be
    applied once the chunks are assembled in post_chunked_upload_complete.
    """
    try:
        file_name = request.data.get('file_name')
        total_chunks = int(request.data.get('total_chunks', 0))
        total_size = int(request.data.get('total_size', 0))

        if not file_name or total_chunks == 0 or total_size == 0:
            return Response({"error": "file_name, total_chunks, total_size required"}, status=400)

        if total_size > CHUNK_UPLOAD_MAX_SIZE:
            return Response({"error": "File too large. Max 4GB allowed"}, status=400)

        hashtags = request.data.get('hashtags')
        if not isinstance(hashtags, list):
            hashtags = []
        location = request.data.get('location')
        if not isinstance(location, dict):
            location = {}

        upload_id = str(uuid.uuid4())
        PostChunkedUpload.objects.create(
            upload_id=upload_id,
            file_name=file_name,
            total_chunks=total_chunks,
            total_size=total_size,
            user=request.user,
            title=request.data.get('title') or '',
            content=request.data.get('content') or '',
            category=request.data.get('category') or 'general',
            post_type=request.data.get('post_type') or 'video',
            visibility=request.data.get('visibility') or 'public',
            hashtags=hashtags,
            location=location,
            media_caption=request.data.get('media_caption') or '',
            media_type=request.data.get('media_type') or '',
        )
        os.makedirs(os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id), exist_ok=True)
        return Response({"upload_id": upload_id, "message": "Ready for chunks"}, status=200)
    except Exception as e:
        logger.error(f'post_chunked_upload_init failed: {e}', exc_info=True)
        return Response({"error": str(e)}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def post_chunked_upload_status(request, upload_id):
    """TASK 4 — tells the client which chunks the server already has, so
    ApiService.createPostWithChunkedUpload can resume an interrupted
    upload by only re-sending the missing ones instead of starting over.
    """
    upload = get_object_or_404(PostChunkedUpload, upload_id=upload_id, user=request.user)
    return Response({
        "upload_id": upload.upload_id,
        "is_completed": upload.is_completed,
        "total_chunks": upload.total_chunks,
        "received_chunks": list_received_chunks(upload.upload_id),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser])
@transaction.atomic
def post_chunked_upload_complete(request):
    """Assemble the uploaded chunks into the final video file and create
    the Post + its single PostMedia row from the payload captured at
    init() time. Response shape matches PostCreateAPIView.post()
    ({"success", "message", "data"}, 201) since
    ApiService.completePostChunkedUpload reads `data['message']` on
    failure the same way the non-chunked create path's error does.
    """
    try:
        upload_id = request.data.get('upload_id')
        if not upload_id:
            return Response({"success": False, "message": "upload_id required"}, status=400)

        upload = get_object_or_404(PostChunkedUpload, upload_id=upload_id, user=request.user)
        if upload.is_completed:
            # Guards against a double-tap/retry re-creating a second Post
            # for the same upload — the client's own resume logic checks
            # is_completed via the status endpoint first, but that's a
            # separate request/race, not a guarantee.
            return Response({"success": False, "message": "Upload already completed"}, status=400)

        final_dir = os.path.join(
            settings.MEDIA_ROOT, 'posts',
            str(timezone.now().year), f"{timezone.now().month:02d}", f"{timezone.now().day:02d}",
        )
        final_file_name = f"{uuid.uuid4()}_{upload.file_name}"
        try:
            final_path = assemble_chunks(upload.upload_id, upload.total_chunks, final_dir, final_file_name)
        except FileNotFoundError as e:
            return Response({"success": False, "message": str(e)}, status=400)

        relative_path = os.path.relpath(final_path, settings.MEDIA_ROOT)

        # Same hashtag extraction + normalization PostCreateSerializer.create()
        # does for the non-chunked path (serializers.py) — kept identical so
        # a chunked video post's tags are searchable the same way a regular
        # post's are (HashtagPostsAPIView / TrendingHashtagsAPIView both
        # match on normalized, lowercase, no-'#' tags).
        hashtags = upload.hashtags or []
        if not hashtags and upload.content:
            hashtags = re.findall(r"#(\w+)", upload.content)
        seen = []
        for tag in hashtags:
            normalized = tag.strip().lstrip('#').lower()
            if normalized and normalized not in seen:
                seen.append(normalized)
        hashtags = seen

        base_text = upload.title or (upload.content[:50] if upload.content else '') or str(uuid.uuid4())[:8]
        slug = slugify(base_text)[:200] or uuid.uuid4().hex[:8]
        if Post.objects.filter(slug=slug).exists():
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"

        post = Post.objects.create(
            user=request.user,
            title=upload.title or None,
            content=upload.content,
            category=upload.category or 'general',
            post_type=upload.post_type or 'video',
            visibility=upload.visibility or 'public',
            hashtags=hashtags,
            location=upload.location or {},
            slug=slug,
        )

        media_type = upload.media_type or 'video'
        if media_type not in dict(PostMedia.MEDIA_TYPE_CHOICES):
            media_type = 'video'
        mime_type = 'video/mp4' if media_type == 'video' else 'application/octet-stream'

        PostMedia.objects.create(
            post=post,
            media_type=media_type,
            file=relative_path,
            file_name=upload.file_name,
            file_size_bytes=upload.total_size,
            mime_type=mime_type,
            display_order=0,
        )

        upload.is_completed = True
        upload.save(update_fields=['is_completed'])

        logger.info(f'Post created via chunked upload: {post.id} by {request.user.id}')
        output_serializer = PostCreateSerializer(post, context={'request': request})
        return Response(
            {"success": True, "message": "Post created successfully", "data": output_serializer.data},
            status=status.HTTP_201_CREATED,
        )
    except Exception as e:
        logger.error(f'post_chunked_upload_complete failed: {e}', exc_info=True)
        return Response({"success": False, "message": "Failed to create post"}, status=500)


# ===================== STORIES =====================
# NEW — checklist items 54/55/57/60. Story model added in models.py.
class StoryCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Create a Story (24h auto-expiry)", tags=["Stories"])
    def post(self, request):
        serializer = StoryCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        story = serializer.save()
        return Response(StorySerializer(story, context={"request": request}).data, status=status.HTTP_201_CREATED)


class StoryListAPIView(generics.ListAPIView):
    """Active (non-expired, non-deleted) stories from people the requester
    follows, plus their own. Expiry is enforced here in real time — the
    `expire_old_stories` celery task (tasks.py) only does housekeeping
    (soft-deleting rows so they don't pile up), it isn't what makes an
    expired story stop showing up."""
    serializer_class = StorySerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_context(self):
        return {"request": self.request}

    def get_queryset(self):
        request_user = self.request.user
        following_ids = Follow.objects.filter(
            follower=request_user, status=Follow.Status.ACCEPTED
        ).values_list("following_id", flat=True)
        return (
            Story.objects.select_related("user")
            .filter(is_deleted=False, expires_at__gt=timezone.now())
            .filter(Q(user_id__in=following_ids) | Q(user=request_user))
            .order_by("user_id", "-created_at")
        )


class StoryViewAPIView(APIView):
    """Records a view (deduped per user via unique_together) and returns
    the current view count — mirrors PostDetailAPIView's PostView tracking."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark a story as viewed", tags=["Stories"])
    def post(self, request, story_id):
        from django.shortcuts import get_object_or_404

        story = get_object_or_404(Story, id=story_id, is_deleted=False)
        if story.is_expired:
            return Response({"success": False, "message": "Story expired"}, status=status.HTTP_404_NOT_FOUND)

        StoryView.objects.get_or_create(story=story, user=request.user)
        story.refresh_from_db(fields=["views_count"])
        return Response({"success": True, "views_count": story.views_count})


# ===================== MEDIA SERVE WITH RANGE =====================
# TASK 25 — dev-only fallback. Only ever mounted when
# `settings.SERVE_MEDIA_VIA_DJANGO` is True (see post/urls.py and
# settings.py's SERVE_MEDIA_VIA_DJANGO comment) — in production, media is
# served by nginx straight off disk (deploy/nginx.conf) or by S3/CloudFront
# (deploy/S3_CLOUDFRONT_SETUP.md), never through this view.
def serve_media_with_range(request, path):
    if settings.USE_S3_STORAGE:
        # Shouldn't be reachable — settings.py raises ImproperlyConfigured
        # at startup if SERVE_MEDIA_VIA_DJANGO=true and USE_S3_STORAGE=true
        # together. Fail loudly instead of a confusing "file not found" if
        # someone still manages to wire this route in anyway (e.g. a
        # `re_path` added by hand elsewhere): with S3 storage active,
        # MEDIA_ROOT is not where uploaded files live.
        raise Http404("Media is served from S3/CloudFront when USE_S3_STORAGE=True, not local disk.")
    # 🔒 FIX — path-traversal check moved BEFORE any filesystem access.
    # It previously ran *after* `os.path.exists()`/`os.path.isfile()` on
    # the unvalidated path, which let an attacker use response timing/
    # behavior as an existence oracle for arbitrary paths (e.g.
    # `../../.env`) before the traversal guard ever fired. Reject first,
    # touch disk second.
    if '..' in path or path.startswith('/'):
        raise Http404("Invalid path")
    file_path = os.path.join(settings.MEDIA_ROOT, path)
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise Http404("File not found")
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
# 🔥 TASK 22 — consolidated. This file used to define its own local
# `ReactionRequestSerializer` (identical `choices` list to the one in
# `serializers.py`, just single- vs double-quoted — no actual drift, so
# safe to collapse) instead of importing the canonical one. Now imported
# from `.serializers` above, like every other serializer this view uses.
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
            # Task 11 fix — only a genuinely new like notifies; a
            # reaction *change* (the `existing.reaction_type != reaction_type`
            # branch above) intentionally does not, same as unlike doesn't.
            notify_post_liked(post, user)

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

from django.shortcuts import get_object_or_404

# ===================== BULK POST COUNTS (polling) =====================
# NEW — SujhaavFayda1 item 2 ("feed zinda feel"). PostReactionAPIView's GET
# above returns counts for ONE post; the Flutter feed polls this instead so
# a screenful of ~10-20 visible posts costs one request, not one-per-post.
#
# NOTE: if this project's `message` app already has Django Channels set up
# (asgi.py routing + consumers.py), a WebSocket push would be strictly
# better than polling here — this was built as REST polling because no
# Channels routing was present in the files reviewed for this pass. Share
# the message app's consumers.py/routing.py and this can be swapped for a
# real subscription instead.
class PostCountsAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Bulk post reaction/comment counts (polling)",
        description="Comma-separated `ids` query param — returns just the "
                     "reaction + comment counts for those posts, capped at "
                     "100 ids per call.",
        parameters=[OpenApiParameter(name="ids", type=OpenApiTypes.STR, required=True)],
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Reactions"],
    )
    def get(self, request):
        raw_ids = request.query_params.get('ids', '')
        ids = [i.strip() for i in raw_ids.split(',') if i.strip()]
        if not ids:
            return Response({"results": []})
        # This is a lightweight polling endpoint, not a feed replacement —
        # cap it so a misbehaving client can't turn it into one.
        ids = ids[:100]
        posts = Post.objects.filter(id__in=ids).values(
            'id', 'comments_count', 'likes_count',
            'like_count', 'confuse_count', 'wrong_count', 'imp_count', 'explain_count',
        )
        results = [
            {
                "id": str(p['id']),
                "comments_count": p['comments_count'],
                "counts": {
                    "like": p['like_count'],
                    "confuse": p['confuse_count'],
                    "wrong": p['wrong_count'],
                    "imp": p['imp_count'],
                    "explain": p['explain_count'],
                    "total": p['likes_count'],
                },
            }
            for p in posts
        ]
        return Response({"results": results})


# ===================== FEED AD/INTERSTITIAL CONFIG =====================
# NEW — SujhaavFayda1 item 3. kAdEveryPosts / kInterstitialEveryPosts used
# to be hardcoded Dart consts — changing the cadence meant an app release.
# The client now fetches this once per session and falls back to its own
# hardcoded defaults if the call fails.
#
# Reads from settings for now (override per-environment via env vars,
# still needs a process restart to change — NOT true hot A/B testing). For
# real without-a-release A/B testing, swap the body of get() for a lookup
# against an admin-editable model, django-waffle, or whatever feature-flag
# service this project standardizes on — no such model was in the files
# reviewed for this pass, so it wasn't guessed at here.
class FeedAdConfigAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Feed ad/interstitial cadence config",
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Feed"],
    )
    def get(self, request):
        return Response({
            "ad_every_posts": getattr(settings, "FEED_AD_EVERY_POSTS", 5),
            "interstitial_every_posts": getattr(settings, "FEED_INTERSTITIAL_EVERY_POSTS", 18),
        })


class PostSaveToggleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Save / Unsave Post - Toggle",
        description="Pehli baar call -> Saved, Dubara call -> Unsaved",
        request=OpenApiTypes.OBJECT,
        tags=["Post Save"]
    )
    def post(self, request, post_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        collection_name = request.data.get('collection_name', 'default')

        # Check pehle se saved hai kya?
        saved_obj = PostSave.objects.filter(post=post, user=request.user).first()

        if saved_obj:
            # Already saved hai -> ab unsave karo
            saved_obj.delete()
            post.refresh_from_db()
            return Response({
                "status": "unsaved",
                "is_saved": False,
                "saves_count": post.saves_count
            }, status=status.HTTP_200_OK)
        else:
            # Save karo
            PostSave.objects.create(
                post=post,
                user=request.user,
                collection_name=collection_name
            )
            post.refresh_from_db()
            return Response({
                "status": "saved",
                "is_saved": True,
                "saves_count": post.saves_count
            }, status=status.HTTP_201_CREATED)

# ===================== SAVED POSTS LIST =====================
class SavedPostsListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PostListSerializer
    pagination_class = StandardResultsSetPagination

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(
        summary="Mere Saved Posts ki List",
        parameters=[
            OpenApiParameter(name='collection_name', type=str, required=False, description='Filter by collection'),
        ],
        tags=["Post Save"]
    )
    def get_queryset(self):
        user = self.request.user
        qs = Post.objects.select_related('user').prefetch_related('media').filter(
            saved_by__user=user,
            is_deleted=False
        ).order_by('-saved_by__created_at')

        collection = self.request.query_params.get('collection_name')
        if collection:
            qs = qs.filter(saved_by__collection_name=collection)
        return qs

# ===================== HASHTAG DISCOVERY (checklist: "Hashtag") =====================
# post_app.md's overview table lists Hashtag as one of this app's core
# responsibilities, but until now `Post.hashtags` (models.py) was
# write-only — populated on create, never queried back out. These two
# views are what actually make it a discovery feature instead of just
# metadata sitting on the row.
class HashtagPostsAPIView(generics.ListAPIView):
    """GET /post/hashtag/<tag>/ — public posts carrying that hashtag.

    Relies on PostCreateSerializer normalizing hashtags to lowercase at
    write time (serializers.py fix) — without that, `hashtags__contains`
    below would miss posts whose tag casing didn't happen to match.

    `hashtags__contains=[tag]` is a JSONField containment lookup:
    native on Postgres (jsonb `@>`), and supported on SQLite via the
    JSON1 extension on Django ≥3.1 — if this app ends up on an older
    SQLite without JSON1, swap this for a `Q(hashtags__icontains=tag)`
    fallback (less precise — can substring-match inside a longer tag).
    """
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(
        summary="Posts by hashtag",
        parameters=[OpenApiParameter(name='tag', type=str, location=OpenApiParameter.PATH)],
        tags=["Hashtag"],
    )
    def get_queryset(self):
        tag = self.kwargs['tag'].strip().lstrip('#').lower()
        return Post.objects.select_related('user').prefetch_related('media').filter(
            is_deleted=False, moderation_status='approved', visibility='public',
            hashtags__contains=[tag],
        ).annotate(
            engagement_score=ExpressionWrapper(
                F('likes_count') * 3.0 + F('comments_count') * 5.0 + F('shares_count') * 10.0,
                output_field=FloatField(),
            )
        ).order_by('-engagement_score', '-created_at')


class TrendingHashtagsAPIView(APIView):
    """GET /post/hashtags/trending/?days=7&limit=20

    ⚠️ Application-level counting over a bounded recent sample, not a
    real SQL aggregation — JSONField list elements aren't natively
    GROUP-BY-able portably across Postgres/SQLite. Same "note it, don't
    block on it" call as checklist item 60 made for feed fan-out: fine
    at this app's current scale (bounded to the most recent 2000 public
    posts in the window), replace with a Redis sorted set incremented
    in PostCreateSerializer.create() once volume actually demands it.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Trending hashtags",
        parameters=[
            OpenApiParameter(name='days', type=int, required=False, description='Lookback window, default 7'),
            OpenApiParameter(name='limit', type=int, required=False, description='Max tags returned, default 20'),
        ],
        tags=["Hashtag"],
    )
    def get(self, request):
        days = int(request.query_params.get('days', 7))
        limit = min(int(request.query_params.get('limit', 20)), 50)
        since = timezone.now() - timedelta(days=days)

        recent_hashtag_lists = Post.objects.filter(
            is_deleted=False, moderation_status='approved', visibility='public',
            created_at__gte=since,
        ).exclude(hashtags=[]).order_by('-created_at').values_list('hashtags', flat=True)[:2000]

        counts = Counter()
        for tags in recent_hashtag_lists:
            counts.update(tags)

        results = [{"hashtag": tag, "count": count} for tag, count in counts.most_common(limit)]
        return Response({"success": True, "days": days, "results": results})


# ===================== EXPLORE / DISCOVER (checklist: "Explore-content") =====================
class ExploreFeedAPIView(generics.ListAPIView):
    """GET /post/explore/?category=...

    HomeFeedView (above) already has a public-posts fallback branch for
    when a user follows nobody / their follows haven't posted recently
    — but that only ever surfaces as a fallback *inside* the following
    feed. There was no standalone discovery surface a user could open
    any time regardless of who they follow (the "Explore" grid pattern)
    — this is that endpoint.

    Deliberately excludes the requester's own posts AND posts from
    accounts they already follow, so Explore stays genuinely about
    finding new accounts rather than duplicating the home feed.
    """
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = HomeFeedPagination

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(
        summary="Explore / discover public posts",
        parameters=[OpenApiParameter(name='category', type=str, required=False)],
        tags=["Explore"],
    )
    def get_queryset(self):
        request_user = self.request.user
        following_ids = Follow.objects.filter(
            follower=request_user, status=Follow.Status.ACCEPTED
        ).values_list('following_id', flat=True)

        thirty_days_ago = timezone.now() - timedelta(days=30)
        qs = Post.objects.select_related('user').prefetch_related('media').filter(
            is_deleted=False, moderation_status='approved', is_sensitive=False, visibility='public',
        ).exclude(user=request_user).exclude(user_id__in=following_ids)

        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)

        recent_qs = qs.filter(created_at__gte=thirty_days_ago).annotate(
            engagement_score=ExpressionWrapper(
                F('likes_count') * 3.0 + F('comments_count') * 5.0 + F('shares_count') * 10.0 + F('views_count') * 0.1,
                output_field=FloatField(),
            )
        ).order_by('-engagement_score', '-created_at')

        # Same defensive fallback pattern as HomeFeedView: a brand-new
        # platform / a narrow category filter can easily have zero posts
        # in the last 30 days — fall back to all-time top public posts
        # rather than showing an empty grid.
        if recent_qs.exists():
            return recent_qs
        return qs.annotate(
            engagement_score=ExpressionWrapper(
                F('likes_count') * 3.0 + F('comments_count') * 5.0 + F('shares_count') * 10.0 + F('views_count') * 0.1,
                output_field=FloatField(),
            )
        ).order_by('-engagement_score', '-created_at')