"""
post/post_options_views.py  (round 5)

  GET    /post/<id>/history/                       author: the edit history (newest first)
  GET    /post/<id>/collaborators/                 author: every invite (pending / accepted / declined); others: accepted only
  POST   /post/<id>/collaborators/                 author: {"user_ids": [..]} invite co-authors
  POST   /post/<id>/collaborators/respond/         invitee: {"accept": true|false}
  DELETE /post/<id>/collaborators/<user_id>/       author removes anyone / a co-author leaves
  GET    /post/collab-invites/                     my pending invites (post preview + who invited me)
"""
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import collaborators as collab
from .audience import can_view_post
from .models import Post, PostCollaborator
from .post_options import edit_history_payload


def _err(code, message, http=status.HTTP_400_BAD_REQUEST):
    return Response({"success": False, "code": code, "message": message}, status=http)


def _own_post(request, post_id):
    post = get_object_or_404(Post, id=post_id, is_deleted=False)
    if post.user_id != request.user.id:
        return None, _err("not_owner", "Not allowed", status.HTTP_403_FORBIDDEN)
    return post, None


class PostHistoryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Edit history of one of my posts", tags=["Post"])
    def get(self, request, post_id):
        post, err = _own_post(request, post_id)
        if err is not None:
            return err
        return Response({"success": True, "data": edit_history_payload(post)})


class PostCollaboratorsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Co-authors of a post", tags=["Post"])
    def get(self, request, post_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        if post.user_id == request.user.id:
            return Response({"success": True, "data": collab.owner_payload(post, request), "max": collab.MAX_COLLABORATORS})
        if not can_view_post(post, request.user):
            return _err("not_found", "Post not found", status.HTTP_404_NOT_FOUND)
        return Response({"success": True, "data": collab.payload(post, request), "max": collab.MAX_COLLABORATORS})

    @extend_schema(summary="Invite co-authors to one of my posts", tags=["Post"])
    def post(self, request, post_id):
        post, err = _own_post(request, post_id)
        if err is not None:
            return err
        try:
            result = collab.invite(post, request.user, collab.parse_ids(request.data.get("user_ids")))
        except collab.CollabError as exc:
            return _err(exc.code, exc.message, exc.status)
        return Response({"success": True, "data": dict(result, collaborators=collab.owner_payload(post, request))})


class PostCollaboratorRespondAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Accept or decline a co-author invite", tags=["Post"])
    def post(self, request, post_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        raw = request.data.get("accept")
        accept = raw is True or str(raw).lower() in ("1", "true", "yes")
        try:
            row = collab.respond(post, request.user, accept)
        except collab.CollabError as exc:
            return _err(exc.code, exc.message, exc.status)
        return Response({"success": True, "data": {"status": row.status}})


class PostCollaboratorRemoveAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Remove a co-author / leave a post", tags=["Post"])
    def delete(self, request, post_id, user_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        try:
            collab.remove(post, request.user, int(user_id))
        except collab.CollabError as exc:
            return _err(exc.code, exc.message, exc.status)
        return Response({"success": True})


class MyCollabInvitesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="My pending co-author invites", tags=["Post"])
    def get(self, request):
        rows = (PostCollaborator.objects
                .filter(user=request.user, status=PostCollaborator.STATUS_PENDING, post__is_deleted=False, post__is_archived=False)
                .select_related("post", "invited_by").order_by("-created_at")[:50])
        data = [{
            "post_id": str(r.post_id),
            "preview": (r.post.title or r.post.content or "")[:140],
            "invited_by": {"id": r.invited_by_id, "username": r.invited_by.username},
            "created_at": r.created_at.isoformat(),
        } for r in rows]
        return Response({"success": True, "data": data})
