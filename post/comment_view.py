# import os
# import uuid
# import shutil
# from django.conf import settings
# from django.db import transaction
# from django.db.models import F
# from django.shortcuts import get_object_or_404
# from django.utils import timezone
#
# from rest_framework.views import APIView
# from rest_framework.decorators import api_view, permission_classes, parser_classes
# from rest_framework.permissions import IsAuthenticated, AllowAny
# from rest_framework.response import Response
# from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
# from drf_spectacular.utils import extend_schema
# from drf_spectacular.types import OpenApiTypes
# from drf_spectacular.utils import OpenApiParameter
#
# from.models import Post, PostComment, CommentMedia, ChunkedUpload
# from.comment_serializers import CreateCommentSerializer, PostCommentSerializer
#
# def get_media_type(file):
#     content_type = getattr(file, 'content_type', '') or ''
#     name = (getattr(file, 'name', '') or '').lower()
#     if content_type.startswith('image/') or name.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.heic')):
#         return 'image'
#     if content_type.startswith('video/') or name.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
#         return 'video'
#     if content_type.startswith('audio/') or name.endswith(('.mp3', '.wav', '.m4a', '.ogg', '.aac', '.opus')):
#         return 'audio'
#     if name.endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.zip', '.txt', '.csv')):
#         return 'document'
#     return 'other'
#
# class CommentCreateAPIView(APIView):
#     permission_classes = [IsAuthenticated]
#     parser_classes = [MultiPartParser, FormParser, JSONParser]
#
#     @extend_schema(summary="Create Comment or Reply - Any file upto 4GB", tags=['Comments'])
#     @transaction.atomic
#     def post(self, request):
#         data_dict = {}
#         for key in request.data.keys():
#             if key!= 'files':
#                 data_dict[key] = request.data.get(key)
#
#         if data_dict.get('parent_id') in ['', 'string', 'null', 'None', None]:
#             data_dict['parent_id'] = None
#         if data_dict.get('post_id') in ['', 'string', 'null', None]:
#             data_dict['post_id'] = None
#
#         serializer = CreateCommentSerializer(data=data_dict)
#         serializer.is_valid(raise_exception=True)
#         data = serializer.validated_data
#
#         parent = None
#         if data.get('parent_id'):
#             parent = get_object_or_404(PostComment, id=data['parent_id'], is_deleted=False)
#             post = parent.post
#             if parent.parent_id is not None:
#                 return Response({"error": "Only 1 level nesting allowed"}, status=400)
#         else:
#             post = get_object_or_404(Post, id=data['post_id'])
#
#         if post.is_comments_disabled:
#             return Response({"error": "Comments disabled"}, status=403)
#
#         files = [f for f in request.FILES.getlist('files') if hasattr(f, 'size')]
#         if not files:
#             files = [f for f in request.FILES.getlist('file') if hasattr(f, 'size')]
#
#         content = data.get('content', '').strip()
#         if not content and not files:
#             return Response({"error": "Content or file is required"}, status=400)
#
#         comment = PostComment.objects.create(post=post, user=request.user, parent=parent, content=content)
#
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
#         return Response(PostCommentSerializer(comment, context={'request': request}).data, status=201)
#
# # ================= 4GB CHUNKED UPLOAD - FIXED =================
#
# @api_view(['POST'])
# @permission_classes([IsAuthenticated])
# @parser_classes([JSONParser, MultiPartParser, FormParser])
# def chunked_upload_init(request):
#     try:
#         print("INIT DATA:", request.data)
#         file_name = request.data.get('file_name')
#         total_chunks = int(request.data.get('total_chunks', 0))
#         total_size = int(request.data.get('total_size', 0))
#         post_id = request.data.get('post_id')
#         parent_id = request.data.get('parent_id')
#
#         if parent_id in ['', 'null', 'None']:
#             parent_id = None
#
#         # FIXED: 4GB = 4294967296
#         if total_size > 4294967296:
#             return Response({"error": "File too large. Max 4GB allowed"}, status=400)
#
#         if not file_name or (not post_id and not parent_id) or total_chunks == 0:
#             return Response({"error": "file_name, post_id, total_chunks required"}, status=400)
#
#         upload_id = str(uuid.uuid4())
#         ChunkedUpload.objects.create(
#             upload_id=upload_id,
#             file_name=file_name,
#             total_chunks=total_chunks,
#             total_size=total_size,
#             post_id=post_id if post_id else None,
#             parent_id=parent_id,
#             content=request.data.get('content', ''),
#             user=request.user
#         )
#         os.makedirs(os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id), exist_ok=True)
#         return Response({"upload_id": upload_id, "message": "Ready for chunks"}, status=200)
#     except Exception as e:
#         import traceback
#         traceback.print_exc()
#         return Response({"error": str(e)}, status=400)
#
# @api_view(['POST'])
# @permission_classes([IsAuthenticated])
# @parser_classes([MultiPartParser])
# def chunked_upload_chunk(request):
#     try:
#         upload_id = request.data.get('upload_id')
#         chunk_index = request.data.get('chunk_index')
#         chunk_file = request.FILES.get('chunk')
#
#         if not upload_id or chunk_file is None:
#             return Response({"error": "upload_id and chunk file required"}, status=400)
#
#         try:
#             chunk_index = int(chunk_index)
#         except:
#             return Response({"error": "chunk_index must be int"}, status=400)
#
#         upload = get_object_or_404(ChunkedUpload, upload_id=upload_id, user=request.user)
#
#         chunk_dir = os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id)
#         os.makedirs(chunk_dir, exist_ok=True)
#         chunk_path = os.path.join(chunk_dir, f'chunk_{chunk_index}')
#
#         with open(chunk_path, 'wb') as f:
#             for c in chunk_file.chunks():
#                 f.write(c)
#
#         progress = int(((chunk_index + 1) / upload.total_chunks) * 100)
#         return Response({"received": chunk_index, "progress": progress}, status=200)
#     except Exception as e:
#         import traceback
#         traceback.print_exc()
#         return Response({"error": str(e)}, status=400)
#
# @api_view(['POST'])
# @permission_classes([IsAuthenticated])
# @parser_classes([JSONParser, MultiPartParser, FormParser])
# @transaction.atomic
# def chunked_upload_complete(request):
#     try:
#         upload_id = request.data.get('upload_id')
#         if not upload_id:
#             return Response({"error": "upload_id required"}, status=400)
#
#         upload = get_object_or_404(ChunkedUpload, upload_id=upload_id, user=request.user)
#         temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id)
#         final_dir = os.path.join(settings.MEDIA_ROOT, 'comment_media', str(timezone.now().year), f"{timezone.now().month:02d}", f"{timezone.now().day:02d}")
#         os.makedirs(final_dir, exist_ok=True)
#
#         final_file_name = f"{uuid.uuid4()}_{upload.file_name}"
#         final_path = os.path.join(final_dir, final_file_name)
#
#         # Check all chunks exist
#         for i in range(upload.total_chunks):
#             if not os.path.exists(os.path.join(temp_dir, f'chunk_{i}')):
#                 return Response({"error": f"Missing chunk {i}"}, status=400)
#
#         with open(final_path, 'wb') as final_file:
#             for i in range(upload.total_chunks):
#                 chunk_path = os.path.join(temp_dir, f'chunk_{i}')
#                 with open(chunk_path, 'rb') as cf:
#                     shutil.copyfileobj(cf, final_file, length=1024*1024)
#                 os.remove(chunk_path)
#
#         try:
#             os.rmdir(temp_dir)
#         except:
#             pass
#
#         post = None
#         parent = None
#         if upload.parent_id:
#             parent = get_object_or_404(PostComment, id=upload.parent_id)
#             post = parent.post
#         else:
#             post = get_object_or_404(Post, id=upload.post_id)
#
#         comment = PostComment.objects.create(
#             post=post, user=request.user, parent=parent, content=upload.content or ""
#         )
#
#         relative_path = os.path.relpath(final_path, settings.MEDIA_ROOT)
#
#         # FIX: proper mime detection
#         dummy = type('obj', (object,), {'name': final_file_name, 'content_type': 'video/mp4'})()
#         media_type = get_media_type(dummy)
#         # file extension se bhi check
#         if final_file_name.lower().endswith(('.mp4','.mov','.mkv')):
#             media_type = 'video'
#
#         CommentMedia.objects.create(
#             comment=comment,
#             media_type=media_type,
#             file=relative_path,
#             file_size=upload.total_size,
#             mime_type='video/mp4' if media_type=='video' else 'application/octet-stream'
#         )
#
#         if parent:
#             PostComment.objects.filter(id=parent.id).update(replies_count=F('replies_count') + 1)
#         else:
#             Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)
#
#         upload.is_completed = True
#         upload.save(update_fields=['is_completed'])
#
#         return Response(PostCommentSerializer(comment, context={'request': request}).data, status=201)
#
#     except Exception as e:
#         import traceback
#         traceback.print_exc()
#         return Response({"error": str(e)}, status=500)
#
# # ================= BAKI VIEWS SAME =================
# class CommentUpdateAPIView(APIView):
#     permission_classes = [IsAuthenticated]
#     parser_classes = [MultiPartParser, FormParser, JSONParser]
#     @transaction.atomic
#     def patch(self, request, comment_id):
#         comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
#         if comment.user!= request.user:
#             return Response({"error": "Not allowed"}, status=403)
#         content = request.data.get('content')
#         if content is not None:
#             comment.content = content.strip()
#             comment.is_edited = True
#             comment.save(update_fields=['content', 'is_edited', 'updated_at'])
#         remove_ids = request.data.getlist('remove_media_ids')
#         if remove_ids:
#             CommentMedia.objects.filter(comment=comment, id__in=remove_ids).delete()
#         for f in request.FILES.getlist('files')[:5]:
#             CommentMedia.objects.create(comment=comment, media_type=get_media_type(f), file=f, file_size=f.size, mime_type=getattr(f, 'content_type', ''))
#         return Response(PostCommentSerializer(comment, context={'request': request}).data)
#
# class CommentDeleteAPIView(APIView):
#     permission_classes = [IsAuthenticated]
#     @transaction.atomic
#     def delete(self, request, comment_id):
#         comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
#         if comment.user!= request.user and not request.user.is_staff:
#             return Response({"error": "Not allowed"}, status=403)
#         comment.is_deleted = True
#         comment.deleted_at = timezone.now()
#         comment.save(update_fields=['is_deleted', 'deleted_at'])
#         if comment.parent_id:
#             PostComment.objects.filter(id=comment.parent_id).update(replies_count=F('replies_count') - 1)
#         else:
#             Post.objects.filter(id=comment.post_id).update(comments_count=F('comments_count') - 1)
#         return Response({"message": "Comment deleted"}, status=200)
#
# class CommentListAPIView(APIView):
#     permission_classes = [AllowAny]
#     def get(self, request, post_id):
#         post = get_object_or_404(Post, id=post_id)
#         qs = PostComment.objects.filter(post=post, parent__isnull=True, is_deleted=False).select_related('user').prefetch_related('media')
#         if not request.user.is_authenticated or request.user.id!= post.user_id:
#             qs = qs.filter(is_hidden=False)
#         comments = qs.order_by('-is_pinned', '-created_at')[:30]
#         serializer = PostCommentSerializer(comments, many=True, context={'request': request})
#         return Response(serializer.data)
#
# class CommentRepliesAPIView(APIView):
#     permission_classes = [AllowAny]
#     def get(self, request, comment_id):
#         parent = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
#         replies = PostComment.objects.filter(parent=parent, is_deleted=False, is_hidden=False).select_related('user').prefetch_related('media').order_by('created_at')
#         serializer = PostCommentSerializer(replies, many=True, context={'request': request})
#         return Response(serializer.data)
#
# class CommentHideAPIView(APIView):
#     permission_classes = [IsAuthenticated]
#     @transaction.atomic
#     def post(self, request, comment_id):
#         comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
#         post = comment.post
#         if request.user.id!= post.user_id and not request.user.is_staff:
#             return Response({"error": "Only post owner can hide"}, status=403)
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
#         comment.save(update_fields=['is_hidden', 'hidden_by', 'hidden_at'])
#         return Response({"message": "Hidden" if comment.is_hidden else "Unhidden", "is_hidden": comment.is_hidden})




















import os
import uuid
import shutil
from django.conf import settings
from django.db import transaction
from django.db.models import F, Count
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from drf_spectacular.utils import extend_schema

from.models import Post, PostComment, CommentMedia, ChunkedUpload, CommentLike
from.comment_serializers import CreateCommentSerializer, PostCommentSerializer

def get_media_type(file):
    content_type = getattr(file, 'content_type', '') or ''
    name = (getattr(file, 'name', '') or '').lower()
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
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(summary="Create Comment or Reply - Any file upto 4GB", tags=['Comments'])
    @transaction.atomic
    def post(self, request):
        data_dict = {}
        for key in request.data.keys():
            if key!= 'files':
                data_dict[key] = request.data.get(key)

        if data_dict.get('parent_id') in ['', 'string', 'null', 'None', None]:
            data_dict['parent_id'] = None
        if data_dict.get('post_id') in ['', 'string', 'null', None]:
            data_dict['post_id'] = None

        serializer = CreateCommentSerializer(data=data_dict)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        parent = None
        if data.get('parent_id'):
            parent = get_object_or_404(PostComment, id=data['parent_id'], is_deleted=False)
            post = parent.post
            # FIX 2 - Multiple nesting allow kar diya, ye block hata diya
            # if parent.parent_id is not None:
            # return Response({"error": "Only 1 level nesting allowed"}, status=400)
        else:
            post = get_object_or_404(Post, id=data['post_id'])

        if post.is_comments_disabled:
            return Response({"error": "Comments disabled"}, status=403)

        files = [f for f in request.FILES.getlist('files') if hasattr(f, 'size')]
        if not files:
            files = [f for f in request.FILES.getlist('file') if hasattr(f, 'size')]

        content = data.get('content', '').strip()
        if not content and not files:
            return Response({"error": "Content or file is required"}, status=400)

        comment = PostComment.objects.create(post=post, user=request.user, parent=parent, content=content)

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

        return Response(PostCommentSerializer(comment, context={'request': request}).data, status=201)

# ================= 4GB CHUNKED UPLOAD =================
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser, MultiPartParser, FormParser])
def chunked_upload_init(request):
    try:
        file_name = request.data.get('file_name')
        total_chunks = int(request.data.get('total_chunks', 0))
        total_size = int(request.data.get('total_size', 0))
        post_id = request.data.get('post_id')
        parent_id = request.data.get('parent_id')

        if parent_id in ['', 'null', 'None']:
            parent_id = None

        if total_size > 4294967296:
            return Response({"error": "File too large. Max 4GB allowed"}, status=400)

        if not file_name or (not post_id and not parent_id) or total_chunks == 0:
            return Response({"error": "file_name, post_id, total_chunks required"}, status=400)

        upload_id = str(uuid.uuid4())
        ChunkedUpload.objects.create(
            upload_id=upload_id,
            file_name=file_name,
            total_chunks=total_chunks,
            total_size=total_size,
            post_id=post_id if post_id else None,
            parent_id=parent_id,
            content=request.data.get('content', ''),
            user=request.user
        )
        os.makedirs(os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id), exist_ok=True)
        return Response({"upload_id": upload_id, "message": "Ready for chunks"}, status=200)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"error": str(e)}, status=400)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def chunked_upload_chunk(request):
    try:
        upload_id = request.data.get('upload_id')
        chunk_index = request.data.get('chunk_index')
        chunk_file = request.FILES.get('chunk')

        if not upload_id or chunk_file is None:
            return Response({"error": "upload_id and chunk file required"}, status=400)

        try:
            chunk_index = int(chunk_index)
        except:
            return Response({"error": "chunk_index must be int"}, status=400)

        upload = get_object_or_404(ChunkedUpload, upload_id=upload_id, user=request.user)

        chunk_dir = os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id)
        os.makedirs(chunk_dir, exist_ok=True)
        chunk_path = os.path.join(chunk_dir, f'chunk_{chunk_index}')

        with open(chunk_path, 'wb') as f:
            for c in chunk_file.chunks():
                f.write(c)

        progress = int(((chunk_index + 1) / upload.total_chunks) * 100)
        return Response({"received": chunk_index, "progress": progress}, status=200)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"error": str(e)}, status=400)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser, MultiPartParser, FormParser])
@transaction.atomic
def chunked_upload_complete(request):
    try:
        upload_id = request.data.get('upload_id')
        if not upload_id:
            return Response({"error": "upload_id required"}, status=400)

        upload = get_object_or_404(ChunkedUpload, upload_id=upload_id, user=request.user)
        temp_dir = os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id)
        final_dir = os.path.join(settings.MEDIA_ROOT, 'comment_media', str(timezone.now().year), f"{timezone.now().month:02d}", f"{timezone.now().day:02d}")
        os.makedirs(final_dir, exist_ok=True)

        final_file_name = f"{uuid.uuid4()}_{upload.file_name}"
        final_path = os.path.join(final_dir, final_file_name)

        for i in range(upload.total_chunks):
            if not os.path.exists(os.path.join(temp_dir, f'chunk_{i}')):
                return Response({"error": f"Missing chunk {i}"}, status=400)

        with open(final_path, 'wb') as final_file:
            for i in range(upload.total_chunks):
                chunk_path = os.path.join(temp_dir, f'chunk_{i}')
                with open(chunk_path, 'rb') as cf:
                    shutil.copyfileobj(cf, final_file, length=1024*1024)
                os.remove(chunk_path)

        try:
            os.rmdir(temp_dir)
        except:
            pass

        post = None
        parent = None
        if upload.parent_id:
            parent = get_object_or_404(PostComment, id=upload.parent_id)
            post = parent.post
        else:
            post = get_object_or_404(Post, id=upload.post_id)

        comment = PostComment.objects.create(
            post=post, user=request.user, parent=parent, content=upload.content or ""
        )

        relative_path = os.path.relpath(final_path, settings.MEDIA_ROOT)
        dummy = type('obj', (object,), {'name': final_file_name, 'content_type': 'video/mp4'})()
        media_type = get_media_type(dummy)
        if final_file_name.lower().endswith(('.mp4','.mov','.mkv')):
            media_type = 'video'

        CommentMedia.objects.create(
            comment=comment,
            media_type=media_type,
            file=relative_path,
            file_size=upload.total_size,
            mime_type='video/mp4' if media_type=='video' else 'application/octet-stream'
        )

        if parent:
            PostComment.objects.filter(id=parent.id).update(replies_count=F('replies_count') + 1)
        else:
            Post.objects.filter(id=post.id).update(comments_count=F('comments_count') + 1)

        upload.is_completed = True
        upload.save(update_fields=['is_completed'])

        return Response(PostCommentSerializer(comment, context={'request': request}).data, status=201)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"error": str(e)}, status=500)

# ================= COMMENT REACTION - 5 TYPES =================
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def comment_react(request, comment_id):
    comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
    reaction = request.data.get('reaction', 'like')
    if reaction not in ['like','confuse','wrong','imp','explain']:
        return Response({"error":"Invalid reaction"}, status=400)

    like, created = CommentLike.objects.get_or_create(
        comment=comment, user=request.user,
        defaults={'reaction_type': reaction}
    )
    if not created:
        if like.reaction_type == reaction:
            like.delete()
            my_reaction = None
        else:
            like.reaction_type = reaction
            like.save()
            my_reaction = reaction
    else:
        my_reaction = reaction

    qs = CommentLike.objects.filter(comment=comment).values('reaction_type').annotate(c=Count('id'))
    counts = {r['reaction_type']: r['c'] for r in qs}
    data = {
        'like': counts.get('like',0),
        'confuse': counts.get('confuse',0),
        'wrong': counts.get('wrong',0),
        'imp': counts.get('imp',0),
        'explain': counts.get('explain',0),
        'total': sum(counts.values())
    }
    return Response({"my_reaction": my_reaction, "myReaction": my_reaction, "counts": data, "reaction_counts": data})

# ================= UPDATE - FIXED FOR EDIT =================
class CommentUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(summary="Edit Comment", tags=['Comments'])
    @transaction.atomic
    def patch(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        if comment.user!= request.user:
            return Response({"error": "Not allowed"}, status=403)

        # FIX 1 - JSON aur FormData dono se content lo
        content = request.data.get('content', None)
        if content is None:
            content = request.data.get('content', '')

        if content is not None and str(content).strip()!= "":
            comment.content = str(content).strip()
            comment.is_edited = True
            comment.save(update_fields=['content', 'is_edited', 'updated_at'])
        elif content is not None and str(content).strip() == "":
            # agar empty bheja to bhi edited mark karo
            comment.is_edited = True
            comment.save(update_fields=['is_edited', 'updated_at'])

        # getlist safe handling
        remove_ids = []
        if hasattr(request.data, 'getlist'):
            remove_ids = request.data.getlist('remove_media_ids')
        if remove_ids:
            CommentMedia.objects.filter(comment=comment, id__in=remove_ids).delete()

        for f in request.FILES.getlist('files')[:5]:
            CommentMedia.objects.create(
                comment=comment,
                media_type=get_media_type(f),
                file=f,
                file_size=f.size,
                mime_type=getattr(f, 'content_type', '')
            )

        return Response(PostCommentSerializer(comment, context={'request': request}).data)

class CommentDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]
    @transaction.atomic
    def delete(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        if comment.user!= request.user and not request.user.is_staff:
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
    permission_classes = [AllowAny]
    def get(self, request, post_id):
        post = get_object_or_404(Post, id=post_id)
        qs = PostComment.objects.filter(post=post, parent__isnull=True, is_deleted=False).select_related('user').prefetch_related('media')
        if not request.user.is_authenticated or request.user.id!= post.user_id:
            qs = qs.filter(is_hidden=False)
        comments = qs.order_by('-is_pinned', '-created_at')[:50]
        serializer = PostCommentSerializer(comments, many=True, context={'request': request})
        return Response(serializer.data)

class CommentRepliesAPIView(APIView):
    permission_classes = [AllowAny]
    def get(self, request, comment_id):
        parent = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        # FIX 2 - nested replies ke liye sab reply laayenge
        replies = PostComment.objects.filter(parent=parent, is_deleted=False, is_hidden=False).select_related('user').prefetch_related('media').order_by('created_at')
        serializer = PostCommentSerializer(replies, many=True, context={'request': request})
        return Response(serializer.data)

class CommentHideAPIView(APIView):
    permission_classes = [IsAuthenticated]
    @transaction.atomic
    def post(self, request, comment_id):
        comment = get_object_or_404(PostComment, id=comment_id, is_deleted=False)
        post = comment.post
        if request.user.id!= post.user_id and not request.user.is_staff:
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