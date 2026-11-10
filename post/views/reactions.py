# post/views/reactions.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from rest_framework.permissions import (
    AllowAny,
    IsAuthenticated,
)
from drf_spectacular.types import OpenApiTypes
from ..models import (
    Post,
    PostLike,
)
from ..serializers import ReactionRequestSerializer
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from django.shortcuts import get_object_or_404
from ..services import notify_post_liked
from rest_framework.decorators import permission_classes
from rest_framework import status


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
        from ..moderation_guard import post_hidden_for
        if post_hidden_for(request.user, post):
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        if post.user_id != request.user.id:
            from user_profile.views import is_blocked_between
            if is_blocked_between(request.user, post.user):
                return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = ReactionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reaction_type = serializer.validated_data['reaction']
        user = request.user
        # H5 - optional explicit intent so a client can REPLAY a queued offline tap safely:
        #   desired="set"   -> make sure I have THIS reaction (never toggles it off)
        #   desired="clear" -> make sure I do NOT have THIS reaction (never adds one)
        # Absent / anything else = the old toggle behaviour (older app builds keep working).
        desired = str(request.data.get('desired', '')).lower()
        existing = PostLike.objects.filter(post=post, user=user).first()
        if desired == 'clear' and (existing is None or existing.reaction_type != reaction_type):
            status_msg = "unchanged"
            my_reaction = existing.reaction_type if existing else None
        elif desired == 'set' and existing is not None and existing.reaction_type == reaction_type:
            status_msg = "unchanged"
            my_reaction = reaction_type
        elif existing:
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
        from ..moderation_guard import post_hidden_for
        if post_hidden_for(request.user, post):
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        # P-B3: same block rule as post() above (a blocked pair sees "no such post" on the public GET too).
        if request.user.is_authenticated and post.user_id != request.user.id:
            from user_profile.views import is_blocked_between
            if is_blocked_between(request.user, post.user):
                return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
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


# ---------------------------------------------------------------------------
# TASK G6 (growth_and_feature_tasks.md) — Polls & Q&A post types.
#
# Poll CREATION already existed (PostCreateSerializer's `poll_options` +
# PostPoll/PostPollOption in models.py) but nothing let a viewer actually
# cast a vote — PostPollSerializer's own docstring flagged this as "a
# separate endpoint (not in scope of this task's files)". This is that
# endpoint. "Ask a doubt" (question + answers + best-answer pin) is
# entirely new: PostAnswer (models.py) + this view for listing/creating
# answers + PostAnswerMarkBestAPIView for pinning one.
# ---------------------------------------------------------------------------
# The poll / quiz endpoints (vote, results, CSV export, leaderboard, convert-to-test-series) now live in
# post/poll_views.py; this name stays importable from here for older imports.
from ..poll_views import PostPollVoteAPIView  # noqa: E402,F401
