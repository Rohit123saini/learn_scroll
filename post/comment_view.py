# from .comment_serializers import *
# from rest_framework.views import APIView
# from rest_framework.response import Response
# from rest_framework import status, permissions
# from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
# from django.db import transaction
# from django.db.models import F
# from django.shortcuts import get_object_or_404
# from django.utils import timezone
# from .models import PostComment, CommentMedia, Post
# from drf_spectacular.utils import extend_schema, OpenApiExample, OpenApiParameter
# from drf_spectacular.types import OpenApiTypes
#
# def get_media_type(file):
#     content_type = getattr(file, 'content_type', '') or ''
#     name = file.name.lower()
#     if content_type.startswith('image/') or name.endswith(('.png','.jpg','.jpeg','.webp','.gif','.heic')):
#         return 'image'
#     if content_type.startswith('video/') or name.endswith(('.mp4','.mov','.avi','.mkv','.webm')):
#         return 'video'
#     if content_type.startswith('audio/') or name.endswith(('.mp3','.wav','.m4a','.ogg','.aac','.opus')):
#         return 'audio'
#     if name.endswith(('.pdf','.doc','.docx','.xls','.xlsx','.ppt','.zip','.txt','.csv')):
#         return 'document'
#     return 'other'
#
# class CommentCreateAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     parser_classes = [MultiPartParser, FormParser, JSONParser]
#
#     @extend_schema(
#         summary="Create Comment or Reply - Any file allowed",
#         description="Top level comment ke liye `post_id` bhejo, reply ke liye `parent_id`. Text only, media only, ya dono bhej sakte ho. Voice, Video, PDF sab allowed.",
#         request={
#             'multipart/form-data': {
#                 'type': 'object',
#                 'properties': {
#                     'post_id': {'type': 'string', 'format': 'uuid', 'description': 'Post ID for top-level comment'},
#                     'parent_id': {'type': 'string', 'format': 'uuid', 'description': 'Comment ID for reply'},
#                     'content': {'type': 'string', 'description': 'Text content'},
#                     'files': {
#                         'type': 'array',
#                         'items': {'type': 'string', 'format': 'binary'},
#                         'description': 'Max 5 files - image, video, audio, doc, any file',
#                     },
#                 }
#             }
#         },
#         tags=['Comments']
#     )
#     @transaction.atomic
#     def post(self, request):
#         serializer = CreateCommentSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)
#         data = serializer.validated_data
#
#         parent = None
#         if data.get('parent_id'):
#             parent = get_object_or_404(PostComment, id=data['parent_id'], is_deleted=False)
#             post = parent.post
#             if parent.parent is not None:
#                 return Response({"error": "Only 1 level nesting allowed"}, status=400)
#         else:
#             post = get_object_or_404(Post, id=data['post_id'])
#
#         if post.is_comments_disabled:
#             return Response({"error": "Comments disabled"}, status=403)
#
#         comment = PostComment.objects.create(
#             post=post, user=request.user, parent=parent,
#             content=data.get('content', '').strip()
#         )
#
#         files = request.FILES.getlist('files')
#         for f in files[:5]:
#             CommentMedia.objects.create(
#                 comment=comment,
#                 media_type=get_media_type(f),
#                 file=f,
#                 file_size=f.size,
#                 mime_type=getattr(f, 'content_type', '')
#             )
#
#         if parent:
#             PostComment.objects.filter(id=parent.id).update(replies_count=F('replies_count') + 1)
#         else:
#             Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)
#
#         return Response(PostCommentSerializer(comment).data, status=201)
#
# class CommentUpdateAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     parser_classes = [MultiPartParser, FormParser, JSONParser]
#
#     @extend_schema(
#         summary="Edit Comment",
#         description="Apna comment edit kar sakta hai, media add/remove kar sakta hai",
#         request={
#             'multipart/form-data': {
#                 'type': 'object',
#                 'properties': {
#                     'content': {'type': 'string'},
#                     'files': {'type': 'array', 'items': {'type': 'string', 'format': 'binary'}},
#                     'remove_media_ids': {'type': 'array', 'items': {'type': 'string', 'format': 'uuid'}},
#                 }
#             }
#         },
#         tags=['Comments']
#     )
#     @transaction.atomic
#     def patch(self, request, comment_id):
#         comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
#         if comment.user != request.user:
#             return Response({"error": "Not allowed"}, status=403)
#
#         content = request.data.get('content')
#         remove_media_ids = request.data.getlist('remove_media_ids')
#
#         if content is not None:
#             comment.content = content.strip()
#             comment.is_edited = True
#             comment.save(update_fields=['content', 'is_edited', 'updated_at'])
#
#         if remove_media_ids:
#             CommentMedia.objects.filter(comment=comment, id__in=remove_media_ids).delete()
#
#         new_files = request.FILES.getlist('files')
#         for f in new_files[:5]:
#             CommentMedia.objects.create(
#                 comment=comment,
#                 media_type=get_media_type(f),
#                 file=f,
#                 file_size=f.size,
#                 mime_type=getattr(f, 'content_type', '')
#             )
#
#         return Response(PostCommentSerializer(comment).data)
#
# class CommentDeleteAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#
#     @extend_schema(summary="Delete Comment (Soft Delete)", tags=['Comments'])
#     @transaction.atomic
#     def delete(self, request, comment_id):
#         comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
#         if comment.user != request.user and not request.user.is_staff:
#             return Response({"error": "Not allowed"}, status=403)
#
#         comment.is_deleted = True
#         comment.deleted_at = timezone.now()
#         comment.save(update_fields=['is_deleted', 'deleted_at'])
#
#         if comment.parent_id:
#             PostComment.objects.filter(id=comment.parent_id).update(replies_count=F('replies_count') - 1)
#         else:
#             Post.objects.filter(id=comment.post_id).update(comments_count=F('comments_count') - 1)
#
#         return Response({"message": "Comment deleted"}, status=200)
#
# class CommentListAPIView(APIView):
#     permission_classes = [permissions.AllowAny]
#
#     @extend_schema(
#         summary="List Top Level Comments of a Post",
#         parameters=[OpenApiParameter(name='post_id', type=OpenApiTypes.UUID, location=OpenApiParameter.PATH)],
#         tags=['Comments']
#     )
#     def get(self, request, post_id):
#         post = get_object_or_404(Post, id=post_id)
#         qs = PostComment.objects.filter(post=post, parent__isnull=True, is_deleted=False).select_related('user').prefetch_related('media')
#         if not request.user.is_authenticated or request.user.id != post.user_id:
#             qs = qs.filter(is_hidden=False)
#         comments = qs.order_by('-is_pinned', '-created_at')[:30]
#         return Response(PostCommentSerializer(comments, many=True).data)
#
# class CommentRepliesAPIView(APIView):
#     permission_classes = [permissions.AllowAny]
#
#     @extend_schema(
#         summary="List Replies of a Comment",
#         parameters=[OpenApiParameter(name='comment_id', type=OpenApiTypes.UUID, location=OpenApiParameter.PATH)],
#         tags=['Comments']
#     )
#     def get(self, request, comment_id):
#         parent = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
#         replies = PostComment.objects.filter(parent=parent, is_deleted=False, is_hidden=False).select_related('user').prefetch_related('media').order_by('created_at')
#         return Response(PostCommentSerializer(replies, many=True).data)
#
# class CommentHideAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#
#     @extend_schema(
#         summary="Hide / Unhide Comment (Post Owner Only)",
#         description="Toggle hide. Post owner hi hide kar sakta hai. Hide hone par count kam ho jayega.",
#         parameters=[OpenApiParameter(name='comment_id', type=OpenApiTypes.UUID, location=OpenApiParameter.PATH)],
#         tags=['Comments']
#     )
#     @transaction.atomic
#     def post(self, request, comment_id):
#         comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
#         post = comment.post
#
#         if request.user.id != post.user_id and not request.user.is_staff:
#             return Response({"error": "Only post owner can hide"}, status=403)
#
#         comment.is_hidden = not comment.is_hidden
#         if comment.is_hidden:
#             comment.hidden_by = request.user
#             comment.hidden_at = timezone.now()
#             if comment.parent_id is None:
#                 Post.objects.filter(id=post.id).update(comments_count=F('comments_count') - 1)
#         else:
#             comment.hidden_by = None
#             comment.hidden_at = None
#             if comment.parent_id is None:
#                 Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)
#
#         comment.save(update_fields=['is_hidden', 'hidden_by', 'hidden_at'])
#         return Response({"message": "Hidden" if comment.is_hidden else "Unhidden", "is_hidden": comment.is_hidden})


from .comment_serializers import *
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import PostComment, CommentMedia, Post
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes


def get_media_type(file):
    content_type = getattr(file, 'content_type', '') or ''
    name = file.name.lower()
    if content_type.startswith('image/') or name.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.heic')):
        return 'image'
    if content_type.startswith('video/') or name.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
        return 'video'
    if content_type.startswith('audio/') or name.endswith(('.mp3', '.wav', '.m4a', '.ogg', '.aac', '.opus')):
        return 'audio'
    if name.endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.zip', '.txt', '.csv')):
        return 'document'
    return 'other'


class CommentCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        summary="Create Comment or Reply - Any file allowed",
        description="Top level ke liye post_id, reply ke liye parent_id. Text only, File only, ya dono allowed. Voice/Video sab chalega.",
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'post_id': {'type': 'string', 'format': 'uuid', 'description': 'Post UUID'},
                    'parent_id': {'type': 'string', 'format': 'uuid', 'description': 'Parent Comment UUID (for reply)'},
                    'content': {'type': 'string', 'description': 'Text'},
                    'files': {'type': 'array', 'items': {'type': 'string', 'format': 'binary'},
                              'description': 'Max 5 files'},
                }
            }
        },
        tags=['Comments']
    )
    @transaction.atomic
    def post(self, request):
        # Swagger empty string fix
        data_copy = request.data.copy()
        if data_copy.get('parent_id') in ['', 'string']:
            data_copy['parent_id'] = None

        serializer = CreateCommentSerializer(data=data_copy)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        parent = None
        if data.get('parent_id'):
            parent = get_object_or_404(PostComment, id=data['parent_id'], is_deleted=False)
            post = parent.post
            if parent.parent_id is not None:
                return Response({"error": "Only 1 level nesting allowed"}, status=400)
        else:
            post = get_object_or_404(Post, id=data['post_id'])

        if post.is_comments_disabled:
            return Response({"error": "Comments disabled"}, status=403)

        # Text or File me se ek hona chahiye
        files = [f for f in request.FILES.getlist('files') if hasattr(f, 'size')]
        content = data.get('content', '').strip()
        if not content and not files:
            return Response({"error": "Content or file is required"}, status=400)

        comment = PostComment.objects.create(
            post=post, user=request.user, parent=parent, content=content
        )

        for f in files[:5]:
            CommentMedia.objects.create(
                comment=comment,
                media_type=get_media_type(f),
                file=f,
                file_size=f.size,
                mime_type=getattr(f, 'content_type', '')
            )

        if parent:
            PostComment.objects.filter(id=parent.id).update(replies_count=F('replies_count') + 1)
        else:
            Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)

        return Response(PostCommentSerializer(comment).data, status=201)


class CommentUpdateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        summary="Edit Comment",
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'content': {'type': 'string'},
                    'files': {'type': 'array', 'items': {'type': 'string', 'format': 'binary'}},
                    'remove_media_ids': {'type': 'array', 'items': {'type': 'string', 'format': 'uuid'}},
                }
            }
        },
        tags=['Comments']
    )
    @transaction.atomic
    def patch(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        if comment.user != request.user:
            return Response({"error": "Not allowed"}, status=403)

        content = request.data.get('content')
        if content is not None:
            comment.content = content.strip()
            comment.is_edited = True
            comment.save(update_fields=['content', 'is_edited', 'updated_at'])

        remove_ids = request.data.getlist('remove_media_ids')
        if remove_ids:
            CommentMedia.objects.filter(comment=comment, id__in=remove_ids).delete()

        for f in request.FILES.getlist('files')[:5]:
            CommentMedia.objects.create(comment=comment, media_type=get_media_type(f), file=f, file_size=f.size,
                                        mime_type=getattr(f, 'content_type', ''))

        return Response(PostCommentSerializer(comment).data)


class CommentDeleteAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(summary="Delete Comment", tags=['Comments'])
    @transaction.atomic
    def delete(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        if comment.user != request.user and not request.user.is_staff:
            return Response({"error": "Not allowed"}, status=403)
        comment.is_deleted = True
        comment.deleted_at = timezone.now()
        comment.save(update_fields=['is_deleted', 'deleted_at'])
        if comment.parent_id:
            PostComment.objects.filter(id=comment.parent_id).update(replies_count=F('replies_count') - 1)
        else:
            Post.objects.filter(id=comment.post_id).update(comments_count=F('comments_count') - 1)
        return Response({"message": "Comment deleted"}, status=200)


class CommentListAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(summary="List Top Comments", parameters=[
        OpenApiParameter(name='post_id', type=OpenApiTypes.UUID, location=OpenApiParameter.PATH)], tags=['Comments'])
    def get(self, request, post_id):
        post = get_object_or_404(Post, id=post_id)
        qs = PostComment.objects.filter(post=post, parent__isnull=True, is_deleted=False).select_related(
            'user').prefetch_related('media')
        if not request.user.is_authenticated or request.user.id != post.user_id:
            qs = qs.filter(is_hidden=False)
        comments = qs.order_by('-is_pinned', '-created_at')[:30]
        return Response(PostCommentSerializer(comments, many=True).data)


class CommentRepliesAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(summary="List Replies", parameters=[
        OpenApiParameter(name='comment_id', type=OpenApiTypes.UUID, location=OpenApiParameter.PATH)], tags=['Comments'])
    def get(self, request, comment_id):
        parent = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        replies = PostComment.objects.filter(parent=parent, is_deleted=False, is_hidden=False).select_related(
            'user').prefetch_related('media').order_by('created_at')
        return Response(PostCommentSerializer(replies, many=True).data)


class CommentHideAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(summary="Hide/Unhide Comment (Post Owner)", parameters=[
        OpenApiParameter(name='comment_id', type=OpenApiTypes.UUID, location=OpenApiParameter.PATH)], tags=['Comments'])
    @transaction.atomic
    def post(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        post = comment.post
        if request.user.id != post.user_id and not request.user.is_staff:
            return Response({"error": "Only post owner can hide"}, status=403)
        comment.is_hidden = not comment.is_hidden
        if comment.is_hidden:
            comment.hidden_by = request.user
            comment.hidden_at = timezone.now()
            if comment.parent_id is None:
                Post.objects.filter(id=post.id).update(comments_count=F('comments_count') - 1)
        else:
            comment.hidden_by = None
            comment.hidden_at = None
            if comment.parent_id is None:
                Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)
        comment.save(update_fields=['is_hidden', 'hidden_by', 'hidden_at'])
        return Response({"message": "Hidden" if comment.is_hidden else "Unhidden", "is_hidden": comment.is_hidden})