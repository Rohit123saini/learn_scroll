# post/views/uploads.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

import os
import re
import uuid
from ..services import (
    CHUNK_UPLOAD_MAX_SIZE,
    assemble_chunks,
    list_received_chunks,
)
from rest_framework.permissions import IsAuthenticated
from ..models import (
    Post,
    PostChunkedUpload,
    PostMedia,
)
from ..serializers import (
    PostCreateSerializer,
    category_error_message,
    normalize_category,
)
from rest_framework.response import Response
from .. import content_defaults as _content_defaults
from rest_framework.decorators import (
    api_view,
    parser_classes,
    permission_classes,
)
from django.shortcuts import get_object_or_404
from rest_framework import (
    parsers,
    status,
)
from django.conf import settings
from django.utils.text import slugify
from django.utils import timezone
from django.db import transaction
from ._common import logger


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
    from ..legacy_chunked import legacy_chunked_gate
    _legacy = legacy_chunked_gate(request, 'chunked/init')
    if _legacy is not None:
        return _legacy
    try:
        file_name = request.data.get('file_name')
        total_chunks = int(request.data.get('total_chunks', 0))
        total_size = int(request.data.get('total_size', 0))

        if not file_name or total_chunks == 0 or total_size == 0:
            return Response({"error": "file_name, total_chunks, total_size required"}, status=400)

        if total_size > CHUNK_UPLOAD_MAX_SIZE:
            return Response({"error": "File too large. Max 4GB allowed"}, status=400)

        # Polls / quizzes are not carried by this legacy path (it holds ONE video file and no poll rows, option
        # images or poll_settings). Say so instead of silently creating a video post without its poll - the app's
        # upload queue (/post/uploads/...) and POST /post/create/ both support polls.
        if (request.data.get('post_type') == 'poll' or request.data.get('poll_options')
                or request.data.get('poll_settings')):
            return Response(
                {"error": "Polls cannot be created with a chunked upload. Use POST /post/create/.",
                 "code": "poll_not_supported_chunked"},
                status=400,
            )

        hashtags = request.data.get('hashtags')
        if not isinstance(hashtags, list):
            hashtags = []
        location = request.data.get('location')
        if not isinstance(location, dict):
            location = {}
        from ..minor_privacy import scrub_location
        location = scrub_location(request.user, location)  # H23: minors never store a location

        # Same category rule as PostCreateSerializer — this path used to
        # store any string the client sent (e.g. "null"), which then landed
        # on the final Post unvalidated in post_chunked_upload_complete.
        raw_category = request.data.get('category')
        if raw_category in (None, ''):
            category = 'general'
        else:
            category = normalize_category(raw_category)
            if category is None:
                message = category_error_message(raw_category)
                return Response(
                    {"success": False, "message": message, "error": message,
                     "errors": {"category": [message]}},
                    status=400,
                )

        upload_id = str(uuid.uuid4())
        PostChunkedUpload.objects.create(
            upload_id=upload_id,
            file_name=file_name,
            total_chunks=total_chunks,
            total_size=total_size,
            user=request.user,
            title=request.data.get('title') or '',
            content=request.data.get('content') or '',
            category=category,
            subcategory=(str(request.data.get('subcategory') or '').strip() or None),
            post_type=request.data.get('post_type') or 'video',
            visibility=_content_defaults.post_visibility_default(request.user, request.data.get('visibility')),  # S4b
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
    from ..legacy_chunked import legacy_chunked_gate
    _legacy = legacy_chunked_gate(request, 'chunked/status')
    if _legacy is not None:
        return _legacy
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
    from ..legacy_chunked import legacy_chunked_gate
    _legacy = legacy_chunked_gate(request, 'chunked/complete')
    if _legacy is not None:
        return _legacy
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

        # The chunked path used to skip the copyright repeat-infringer gate that /post/create/ and
        # stories apply - a blocked account could still publish a video this way.
        from copyrights.services import can_upload, precheck_upload
        allowed, why = can_upload(request.user)
        if not allowed:
            return Response({"success": False, "code": "copyright_upload_blocked", "message": why},
                            status=status.HTTP_403_FORBIDDEN)

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

        ok, code, why = precheck_upload(request.user, paths=[final_path])
        if not ok:
            try:
                os.remove(final_path)  # nothing references it yet
            except OSError:
                logger.warning("could not delete blocked chunked upload %s", final_path, exc_info=True)
            return Response({"success": False, "code": code, "message": why}, status=status.HTTP_403_FORBIDDEN)

        # H23 publish gate (same checks as POST /post/create/; the chunked path used to skip all of them).
        from .. import minor_privacy, publish_gate
        verdict = publish_gate.evaluate(
            request.user, "post",
            texts=[t for t in (upload.title, upload.content) if t],
            paths=[final_path], media_types=[upload.media_type or "video"],
        )
        if not verdict.ok:
            try:
                os.remove(final_path)  # nothing references it yet
            except OSError:
                logger.warning("could not delete refused chunked upload %s", final_path, exc_info=True)
            return verdict.response()

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
            subcategory=upload.subcategory or None,
            post_type=upload.post_type or 'video',
            visibility=upload.visibility or 'public',
            hashtags=hashtags,
            location=minor_privacy.scrub_location(request.user, upload.location),
            slug=slug,
            moderation_status=verdict.moderation_status,
            **_content_defaults.post_extra_fill(request.user, {}),  # S4b: comments limit / reshare / save defaults
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
        publish_gate.after_create(verdict, request.user, "post", post)

        logger.info(f'Post created via chunked upload: {post.id} by {request.user.id}')
        output_serializer = PostCreateSerializer(post, context={'request': request})
        return Response(
            {"success": True, "message": "Post created successfully", "data": output_serializer.data,
             **verdict.held_payload()},
            status=status.HTTP_201_CREATED,
        )
    except Exception as e:
        logger.error(f'post_chunked_upload_complete failed: {e}', exc_info=True)
        return Response({"success": False, "message": "Failed to create post"}, status=500)
