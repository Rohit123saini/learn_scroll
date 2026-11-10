# post/views/saves.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from drf_spectacular.types import OpenApiTypes
from ..models import (
    Post,
    PostSave,
)
from ..serializers import PostListSerializer
from rest_framework.response import Response
from rest_framework import (
    generics,
    status,
)
from django.shortcuts import get_object_or_404
from rest_framework.decorators import permission_classes
from ._common import StandardResultsSetPagination


class PostSaveToggleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Save / Unsave Post - Toggle",
        description="Pehli baar call -> Saved, Dubara call -> Unsaved",
        request=OpenApiTypes.OBJECT,
        tags=["Post Save"]
    )
    def post(self, request, post_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        collection_name = request.data.get('collection_name', 'default')
        from ..profile_tabs import collection_exists, normalize_collection_name  # P2
        collection_name = normalize_collection_name(collection_name) or 'default'
        if not collection_exists(request.user, collection_name):
            return Response({"success": False, "code": "collection_not_found", "message": "That folder does not exist."},
                            status=status.HTTP_404_NOT_FOUND)

        # Check pehle se saved hai kya?
        saved_obj = PostSave.objects.filter(post=post, user=request.user).first()

        # H5 - optional explicit intent (safe replay of a queued offline tap): desired="save" / "unsave" never
        # toggles; it only reports the state when the post is already in that state. Absent = old toggle.
        desired = str(request.data.get('desired', '')).lower()
        if desired == 'save' and saved_obj is not None:
            return Response({"status": "saved", "is_saved": True, "saves_count": post.saves_count},
                            status=status.HTTP_200_OK)
        if desired == 'unsave' and saved_obj is None:
            return Response({"status": "unsaved", "is_saved": False, "saves_count": post.saves_count},
                            status=status.HTTP_200_OK)

        # Saving a hidden post / a blocked person's post is refused; UN-saving is always allowed.
        if saved_obj is None:
            from ..moderation_guard import post_hidden_for
            hidden = post_hidden_for(request.user, post)
            if not hidden and post.user_id != request.user.id:
                from user_profile.views import is_blocked_between
                hidden = is_blocked_between(request.user, post.user)
            if hidden:
                return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
            # S4b: the author switched saving off (only ADDING a save is refused; removing one never is).
            from ..post_options import SAVE_OFF_CODE, SAVE_OFF_TEXT, can_save_post
            if not can_save_post(post, request.user):
                return Response({"success": False, "code": SAVE_OFF_CODE, "message": SAVE_OFF_TEXT},
                                status=status.HTTP_403_FORBIDDEN)

        if saved_obj:
            # Already saved hai -> ab unsave karo
            saved_obj.delete()
            post.refresh_from_db()
            return Response({
                "status": "unsaved",
                "is_saved": False,
                "saves_count": post.saves_count
            }, status=status.HTTP_200_OK)
        else:
            # Save karo
            PostSave.objects.create(
                post=post,
                user=request.user,
                collection_name=collection_name
            )
            post.refresh_from_db()
            return Response({
                "status": "saved",
                "is_saved": True,
                "saves_count": post.saves_count
            }, status=status.HTTP_201_CREATED)

# ===================== SAVED POSTS LIST =====================
class SavedPostsListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PostListSerializer
    pagination_class = StandardResultsSetPagination

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(
        summary="Mere Saved Posts ki List",
        parameters=[
            OpenApiParameter(name='collection_name', type=str, required=False, description='Filter by collection'),
        ],
        tags=["Post Save"]
    )
    def get_queryset(self):
        # P2: built from PostSave (profile_tabs.saved_posts_queryset) - the old `saved_by__...` joins could match
        # OTHER users' saves of the same post once a collection filter was added. Same visibility rules as before.
        from ..profile_tabs import saved_posts_queryset
        collection = self.request.query_params.get('collection') or self.request.query_params.get('collection_name')
        return saved_posts_queryset(self.request.user, collection or None)
