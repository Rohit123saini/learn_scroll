from.comment_serializers import *
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .serializers import *
from .models import PostComment, CommentMedia, Post



def get_media_type(file):
    content_type = getattr(file, 'content_type', '') or ''
    name = file.name.lower()

    if content_type.startswith('image/') or name.endswith(('.png','.jpg','.jpeg','.webp','.gif','.heic')):
        return 'image'
    if content_type.startswith('video/') or name.endswith(('.mp4','.mov','.avi','.mkv','.webm')):
        return 'video'
    if content_type.startswith('audio/') or name.endswith(('.mp3','.wav','.m4a','.ogg','.aac','.opus','.webm')):
        return 'audio'
    if name.endswith(('.pdf','.doc','.docx','.xls','.xlsx','.ppt','.zip','.txt','.csv')):
        return 'document'
    return 'other'



from drf_spectacular.utils import extend_schema, OpenApiExample
from drf_spectacular.types import OpenApiTypes
from rest_framework import permissions
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.views import APIView


class CommentCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @transaction.atomic
    def post(self, request):
        serializer = CreateCommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        parent = None
        if data.get('parent_id'):
            parent = get_object_or_404(PostComment, id=data['parent_id'], is_deleted=False)
            post = parent.post
            if parent.parent is not None:
                return Response({"error": "Only 1 level nesting allowed"}, status=400)
        else:
            post = get_object_or_404(Post, id=data['post_id'])

        if post.is_comments_disabled:
            return Response({"error": "Comments disabled on this post"}, status=403)

        comment = PostComment.objects.create(
            post=post, user=request.user, parent=parent,
            content=data.get('content', '').strip()
        )

        # 🔥 FIX: koi bhi file - text only bhi chalega, file only bhi
        files = request.FILES.getlist('files')
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

#
# class CommentCreateAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     parser_classes = [MultiPartParser, FormParser, JSONParser]
#
#     @transaction.atomic
#     def post(self, request):
#         serializer = CreateCommentSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)
#         data = serializer.validated_data
#
#         user = request.user
#         post = None
#         parent = None
#
#         if data.get('parent_id'):
#             parent = get_object_or_404(PostComment, id=data['parent_id'], is_deleted=False)
#             post = parent.post
#             if parent.parent is not None:
#                 return Response({"error": "You can only reply to top level comment (1 level nesting)"}, status=400)
#         else:
#             post = get_object_or_404(Post, id=data['post_id'])
#
#         # Create comment
#         comment = PostComment.objects.create(
#             post=post,
#             user=user,
#             parent=parent,
#             content=data.get('content', '').strip()
#         )
#
#         # Handle Media Upload
#         files = request.FILES.getlist('files') or data.get('files', [])
#         for f in files:
#             CommentMedia.objects.create(
#                 comment=comment,
#                 media_type=get_media_type(f),
#                 file=f,
#                 file_size=f.size
#             )
#
#         # Count Update
#         if parent:
#             PostComment.objects.filter(id=parent.id).update(replies_count=F('replies_count') + 1)
#         else:
#             Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)
#
#         return Response(PostCommentSerializer(comment).data, status=201)


class CommentUpdateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @transaction.atomic
    def patch(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        if comment.user != request.user:
            return Response({"error": "Not allowed"}, status=403)

        content = request.data.get('content')
        remove_media_ids = request.data.getlist('remove_media_ids')  # front se jo media delete karna hai

        if content is not None:
            comment.content = content.strip()
            comment.is_edited = True
            comment.save(update_fields=['content', 'is_edited', 'updated_at'])

        if remove_media_ids:
            CommentMedia.objects.filter(comment=comment, id__in=remove_media_ids).delete()

        new_files = request.FILES.getlist('files')
        for f in new_files:
            CommentMedia.objects.create(comment=comment, media_type=get_media_type(f), file=f, file_size=f.size)

        return Response(PostCommentSerializer(comment).data)


class CommentDeleteAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def delete(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        if comment.user != request.user and not request.user.is_staff:
            return Response({"error": "Not allowed"}, status=403)

        # Soft delete
        comment.is_deleted = True
        comment.deleted_at = timezone.now()
        comment.save(update_fields=['is_deleted', 'deleted_at'])

        # Count minus
        if comment.parent_id:
            PostComment.objects.filter(id=comment.parent_id).update(replies_count=F('replies_count') - 1)
        else:
            Post.objects.filter(id=comment.post_id).update(comments_count=F('comments_count') - 1)

        # Agar ye parent comment hai to uske replies bhi soft delete? - Production me parent delete pe child rehte hain
        # Agar chahe to child bhi delete kar de:
        # PostComment.objects.filter(parent=comment).update(is_deleted=True, deleted_at=timezone.now())

        return Response({"message": "Comment deleted"}, status=200)


class CommentListAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, post_id):
        post = get_object_or_404(Post, id=post_id)
        qs = PostComment.objects.filter(post=post, parent__isnull=True, is_deleted=False).select_related(
            'user').prefetch_related('media')

        # Agar user post owner nahi hai to hidden comments mat dikhao
        if not request.user.is_authenticated or request.user.id != post.user_id:
            qs = qs.filter(is_hidden=False)

        comments = qs.order_by('-is_pinned', '-created_at')[:30]
        return Response(PostCommentSerializer(comments, many=True).data)


class CommentRepliesAPIView(APIView):
    def get(self, request, comment_id):
        parent = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        replies = PostComment.objects.filter(parent=parent, is_deleted=False).select_related('user').prefetch_related(
            'media')
        return Response(PostCommentSerializer(replies, many=True).data)



class CommentHideAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        post = comment.post

        # Kaun hide kar sakta hai? Post Owner ya Staff
        if request.user.id != post.user_id and not request.user.is_staff:
            return Response({"error": "Only post owner can hide comment"}, status=403)

        # Toggle hide
        comment.is_hidden = not comment.is_hidden
        if comment.is_hidden:
            comment.hidden_by = request.user
            comment.hidden_at = timezone.now()
            # count kam karo jab hide ho
            if comment.parent_id is None:
                Post.objects.filter(id=post.id).update(comments_count=F('comments_count') - 1)
        else:
            comment.hidden_by = None
            comment.hidden_at = None
            if comment.parent_id is None:
                Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)

        comment.save(update_fields=['is_hidden', 'hidden_by', 'hidden_at'])

        return Response(
            {"message": "Comment hidden" if comment.is_hidden else "Comment unhidden", "is_hidden": comment.is_hidden})