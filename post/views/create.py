# post/views/create.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from ..serializers import PostCreateSerializer
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from ..idempotency import idempotent
from rest_framework.decorators import (
    parser_classes,
    permission_classes,
)
from rest_framework import (
    parsers,
    status,
)
from django.db import transaction
from ._common import (
    _first_error_text,
    logger,
)


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
    @idempotent("post_create")  # H20: header `Idempotency-Key` = one post however often the request repeats
    @transaction.atomic
    def post(self, request):
        # Copyright repeat-infringer policy: 2 active strikes block uploads (copyrights app).
        from copyrights.services import can_upload, precheck_upload
        allowed, why = can_upload(request.user)
        if not allowed:
            return Response({"success": False, "code": "copyright_upload_blocked", "message": why},
                            status=status.HTTP_403_FORBIDDEN)
        # Refuse an exact copy of a file that was removed for copyright (similar copies are held after saving).
        # Poll option images (`poll_option_image_<i>`) are uploads too: they go through the same copyright pre-check
        # and publish gate as the media files (appended AFTER them, so `media_types` still lines up with media_files).
        from ..polls import option_image_files
        media_files = list(request.FILES.getlist("media_files")) + option_image_files(request.FILES)
        ok, code, why = precheck_upload(request.user, files=media_files)
        if not ok:
            return Response({"success": False, "code": code, "message": why}, status=status.HTTP_403_FORBIDDEN)
        # H23 publish gate: spam rate-limit, abusive/NSFW text, duplicates, audio-copyright flag, minor privacy.
        from .. import publish_gate
        verdict = publish_gate.evaluate(
            request.user, "post",
            texts=publish_gate.texts_from_request("post", request.data),
            files=media_files, media_types=request.data.get("media_types"),
        )
        if not verdict.ok:
            return verdict.response()
        serializer = PostCreateSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            try:
                save_extra = {"moderation_status": verdict.moderation_status}  # 'flagged' = held for staff review
                if not verdict.location_allowed:
                    save_extra["location"] = {}  # minor accounts never publish a location
                post = serializer.save(user=request.user, **save_extra)
                publish_gate.after_create(verdict, request.user, "post", post)
                # 🔥 FIX: posts_count increment moved to signals.py
                # (increment_posts_count_on_create, post_save on Post).
                # Doing it here too would double-count — see signals.py's
                # module docstring for why the signal is now the single
                # source of truth for this counter.
                logger.info(f'Post created: {post.id} by {request.user.id}')
                output_serializer = PostCreateSerializer(post, context={'request': request})
                return Response({"success": True, "message": "Post created successfully","data": output_serializer.data,
                                 **verdict.held_payload()}, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.error(f'Post creation failed: {e}', exc_info=True)
                return Response({"success": False, "message": "Failed to create post"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({"success": False, "message": _first_error_text(serializer.errors) or "Validation failed","errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
