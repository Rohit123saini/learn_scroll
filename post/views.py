from rest_framework import status, parsers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes
from.models import Post
from.serializers import *
from django.db.models import F
class PostCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    serializer_class = PostCreateSerializer # Swagger ko batane ke liye

    @extend_schema(
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string'},
                    'content': {'type': 'string'},
                    'category': {'type': 'string', 'enum': ['general', 'tech', 'jobs', 'news', 'education', 'business', 'entertainment', 'sports', 'lifestyle', 'other']},
                    'post_type': {'type': 'string', 'enum': ['text', 'image', 'video', 'document', 'poll', 'article', 'carousel', 'link']},
                    'visibility': {'type': 'string', 'enum': ['public', 'connections', 'private']},
                    'hashtags': {'type': 'array', 'items': {'type': 'string'}},
                    'location': {'type': 'object'},
                    'media': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'media_type': {'type': 'string', 'enum': ['image', 'video', 'document', 'audio', 'gif']},
                                'file': {'type': 'string', 'format': 'binary'},
                            }
                        }
                    }
                }
            }
        },
        examples=[
            OpenApiExample(
                'Text Post',
                value={
                    'title': 'My First Post',
                    'content': 'Hello world #flutter',
                    'category': 'tech',
                    'post_type': 'text',
                    'visibility': 'public'
                },
                request_only=True,
            ),
        ],
        responses={201: PostCreateSerializer},
        description='Create a new post with optional media files'
    )
    def post(self, request):
        serializer = PostCreateSerializer(data=request.data, context={'request': request})

        if serializer.is_valid():
            post = serializer.save(user=request.user)
            User.objects.filter(id=request.user.id).update(posts_count=F('posts_count') + 1)
            return Response(
                {
                    "success": True,
                    "message": "Post created successfully",
                    "data": serializer.data
                },
                status=status.HTTP_201_CREATED
            )

        return Response(
            {
                "success": False,
                "message": "Validation failed",
                "errors": serializer.errors
            },
            status=status.HTTP_400_BAD_REQUEST
        )







from rest_framework import generics, filters
from django.db.models import Q
from rest_framework.pagination import PageNumberPagination


class PostPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50


class PostListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PostListSerializer
    pagination_class = PostPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'content', 'hashtags']
    ordering_fields = ['created_at', 'likes_count', 'views_count']
    ordering = ['-created_at']
    @extend_schema(
        parameters=[
            OpenApiParameter(name='category', type=str, description='Filter by category'),
            OpenApiParameter(name='post_type', type=str, description='Filter by post type'),
            OpenApiParameter(name='user_id', type=str, description='Get posts by specific user'),
            OpenApiParameter(name='my_posts', type=bool, description='Get only my posts'),
        ],
        description='Get posts list - feed or user posts'
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        queryset = Post.objects.filter(is_deleted=False, moderation_status='approved')

        # Filters
        category = self.request.query_params.get('category')
        post_type = self.request.query_params.get('post_type')
        user_id = self.request.query_params.get('user_id')
        my_posts = self.request.query_params.get('my_posts')

        if category:
            queryset = queryset.filter(category=category)
        if post_type:
            queryset = queryset.filter(post_type=post_type)

        if my_posts == 'true':
            queryset = queryset.filter(user=user)
        elif user_id:
            queryset = queryset.filter(user_id=user_id)
        else:
            # Feed logic - public + connections posts
            queryset = queryset.filter(
                Q(visibility='public') |
                Q(visibility='connections', user__in=user.connections.all()) |
                Q(user=user)
            ).distinct()

        return queryset.select_related('user').prefetch_related('media')


class PostDetailAPIView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PostDetailSerializer
    queryset = Post.objects.filter(is_deleted=False)
    lookup_field = 'id'

    @extend_schema(description='Get single post detail with comments')
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()

        # View count badhao
        PostView.objects.create(post=instance, user=request.user)
        Post.objects.filter(id=instance.id).update(views_count=models.F('views_count') + 1)

        serializer = self.get_serializer(instance)
        return Response({
            "success": True,
            "data": serializer.data
        })



#----------------------------------------------
import os
import re
import mimetypes
from django.http import StreamingHttpResponse, Http404


def serve_media_with_range(request, path):
    """
    Ekdum robust custom streaming view jo MP4 videos ke range request (seeking)
    aur documents (PDF, Docs) ke unique headers dono sahi se treat karegi.
    """
    from django.conf import settings

    file_path = os.path.join(settings.MEDIA_ROOT, path)
    if not os.path.exists(file_path):
        raise Http404("Media file not found")

    # 1. Content type track karo file extension se
    content_type, encoding = mimetypes.guess_type(file_path)
    content_type = content_type or 'application/octet-stream'

    file_size = os.path.getsize(file_path)
    range_header = request.META.get('HTTP_RANGE', '').strip()
    range_match = re.match(r'bytes=(\d+)-(\d*)', range_header) if range_header else None

    # Agar request Video/Audio streaming (HTML5 video player) ke Range headers ke saath hai
    if range_match:
        first_byte, last_byte = range_match.groups()
        first_byte = int(first_byte)
        last_byte = int(last_byte) if last_byte else file_size - 1

        if first_byte >= file_size:
            return StreamingHttpResponse(status=416)  # Range Not Satisfiable

        chunk_size = (last_byte - first_byte) + 1

        def file_iterator(offset, length):
            with open(file_path, 'rb') as f:
                f.seek(offset)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(remaining, 8192))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)

        response = StreamingHttpResponse(
            file_iterator(first_byte, chunk_size),
            status=206,
            content_type=content_type
        )
        response['Content-Range'] = f'bytes {first_byte}-{last_byte}/{file_size}'
        response['Accept-Ranges'] = 'bytes'
        response['Content-Length'] = str(chunk_size)

    # Normal files like PDF, Docs, Images ya normal direct download requests ke liye
    else:
        def simple_iterator():
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    yield chunk

        response = StreamingHttpResponse(simple_iterator(), content_type=content_type)
        response['Content-Length'] = str(file_size)

    # 2. Documents file download behavior set karne ke liye (Browser force download)
    if not content_type.startswith(('video/', 'audio/', 'image/')):
        file_name = os.path.basename(file_path)
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'

    return response