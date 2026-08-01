# message/upload_view.py
#
# 🔥 NAYI FILE — is app me abhi tak koi generic "sirf file upload karo aur
# URL wapas do" wala endpoint nahi tha. PostCreateAPIView poora Post bana
# deta hai, aur chunked upload seedha Comment se juda hai — dono chat ke
# liye use nahi ho sakte.
#
# Ye view sirf ek file leta hai, media/ me save karta hai, aur URL + type
# wapas deta hai. Koi Message row yahan nahi banta — frontend pehle ye URL
# leta hai, phir usi URL ko `POST /message/conversations/<id>/messages/`
# (REST) ya websocket `message` event me bhejta hai.
#
# ---------------------------------------------------------------
# SETUP:
# 1. Is file ko apne `message` app folder me `upload_view.py` naam se rakho.
# 2. `message/urls.py` me is tarah import + path add karo:
#
#     from .upload_view import MessageUploadAPIView
#     ...
#     urlpatterns = [
#         ...
#         path('upload/', MessageUploadAPIView.as_view(), name='message-upload'),
#     ]
#
# Final endpoint: POST /message/upload/   (multipart/form-data, field: "file")
# ---------------------------------------------------------------

import os
import uuid

from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def get_chat_media_type(f):
    """Backend ke MessageType choices (text/image/video/audio/file/...) ke
    hisaab se file ka type guess karta hai."""
    content_type = getattr(f, 'content_type', '') or ''
    name = (getattr(f, 'name', '') or '').lower()

    if content_type.startswith('image/') or name.endswith(
        ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.heic')
    ):
        return 'image'
    if content_type.startswith('video/') or name.endswith(
        ('.mp4', '.mov', '.avi', '.mkv', '.webm')
    ):
        return 'video'
    if content_type.startswith('audio/') or name.endswith(
        ('.mp3', '.wav', '.m4a', '.ogg', '.aac', '.opus')
    ):
        return 'audio'
    if name.endswith(('.ppt', '.pptx')):
        return 'presentation'
    return 'file'


class MessageUploadAPIView(APIView):
    """
    POST /message/upload/   (multipart, field: "file")

    Response (201):
        {
            "file_url": "https://yourdomain.com/media/chat_media/image/2026/07/<uuid>.jpg",
            "file_type": "image",       # image|video|audio|presentation|file
            "file_size": 204800,
            "file_name": "photo.jpg",
            "mime_type": "image/jpeg"
        }
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    # Chat attachments ke liye safety cap — chahe to badha/ghata sakte ho.
    MAX_UPLOAD_SIZE = 200 * 1024 * 1024  # 200 MB

    def post(self, request):
        f = request.FILES.get('file')
        if not f:
            return Response(
                {"detail": "'file' field required hai."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if f.size > self.MAX_UPLOAD_SIZE:
            return Response(
                {"detail": "File 200MB se bada nahi ho sakta."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        media_type = get_chat_media_type(f)
        today = timezone.now()
        ext = os.path.splitext(f.name)[1]
        filename = f"{uuid.uuid4()}{ext}"

        relative_path = os.path.join(
            'chat_media', media_type, str(today.year), f"{today.month:02d}", filename
        )

        saved_path = default_storage.save(relative_path, f)
        file_url = request.build_absolute_uri(
            settings.MEDIA_URL + saved_path.replace('\\', '/')
        )

        return Response(
            {
                "file_url": file_url,
                "file_type": media_type,
                "file_size": f.size,
                "file_name": f.name,
                "mime_type": getattr(f, 'content_type', ''),
            },
            status=status.HTTP_201_CREATED,
        )