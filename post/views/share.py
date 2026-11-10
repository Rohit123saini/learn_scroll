# post/views/share.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from ..models import Post
from ..serializers import PostShareRequestSerializer
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from django.shortcuts import get_object_or_404
from rest_framework.decorators import permission_classes
from django.conf import settings
from ..services import share_post_to_conversation
from rest_framework import status


# ===================== POST SHARE (internal "forward to chat") =====================
# NEW — checklist item 61. The actual send/broadcast logic lives in
# `share_post_to_conversation` (services.py, now fixed — see its comment
# for the ImportError bug this replaces); this view is just the missing
# URL-facing half: auth, post visibility check, request validation, and
# turning the service's exceptions into the right HTTP status. This is
# NOT the OS share sheet (`Share.share(...)` in singlepost.dart, which
# never touches the backend) — this is forwarding a post into an
# in-app chat as a message.
def _share_guard(request, post):
    """Shared by PostShareAPIView (send in chat) and PostShareLinkAPIView (share a link):
    `None` when `request.user` may share `post`, else the ready-made error Response
    (never reveals more than the post being "not found" / private)."""
    from ..moderation_guard import post_hidden_for

    if post_hidden_for(request.user, post):
        return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
    if post.visibility == "private" and post.user_id != request.user.id:
        return Response({"success": False, "message": "Post is private"}, status=status.HTTP_403_FORBIDDEN)
    if post.visibility in ("close_friends", "class_campus") and post.user_id != request.user.id:
        from ..audience import can_view_post
        if not can_view_post(post, request.user):
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
    if post.user_id != request.user.id:
        from user_profile.views import is_blocked_between
        if is_blocked_between(request.user, post.user):
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
    # S4b: the author switched resharing off (send in chat + share link both come through here).
    from ..post_options import RESHARE_OFF_CODE, RESHARE_OFF_TEXT, can_reshare_post
    if not can_reshare_post(post, request.user):
        return Response({"success": False, "code": RESHARE_OFF_CODE, "message": RESHARE_OFF_TEXT},
                        status=status.HTTP_403_FORBIDDEN)
    return None


class PostShareAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Forward a post into a chat conversation", request=PostShareRequestSerializer, tags=["Post"])
    def post(self, request, id):
        from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

        from message.models import Conversation

        post = get_object_or_404(Post, id=id, is_deleted=False)
        denied = _share_guard(request, post)
        if denied is not None:
            return denied

        body = PostShareRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        try:
            message = share_post_to_conversation(post, request.user, body.validated_data["conversation_id"])
        except Conversation.DoesNotExist:
            return Response({"success": False, "message": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)
        except DjangoPermissionDenied as exc:
            return Response({"success": False, "message": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        return Response(
            {
                "success": True,
                "message": "Post shared",
                "data": {"message_id": str(message.id), "conversation_id": str(message.conversation_id)},
            },
            status=status.HTTP_201_CREATED,
        )


class PostShareLinkAPIView(APIView):
    """POST /post/<id>/share-link/ - the app is about to hand the post's DEEP LINK to the OS share sheet
    (WhatsApp, Instagram, ...). Records one `PostShare` per (post, user) so `shares_count` is right
    (idempotent) and answers with the canonical link built from `settings.APP_WEB_BASE_URL`:
    `<base>/p/<post_id>` (+ `?v=reel` for videos, which the app opens in Reels). The raw media file
    URL is never part of a share."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Record a link share and get the post's deep link", request=None, tags=["Post"])
    def post(self, request, id):
        from ..models import PostShare

        post = get_object_or_404(Post, id=id, is_deleted=False, is_archived=False)
        denied = _share_guard(request, post)
        if denied is not None:
            return denied
        PostShare.objects.get_or_create(post=post, user=request.user)
        post.refresh_from_db(fields=["shares_count"])
        base = str(getattr(settings, "APP_WEB_BASE_URL", "") or "").rstrip("/")
        url = f"{base}/p/{post.id}" + ("?v=reel" if post.post_type == "video" else "") if base else ""
        return Response({"success": True, "data": {"url": url, "shares_count": post.shares_count}})
