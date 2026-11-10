# post/views/feedback.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from ..models import (
    FeedFeedback,
    MutedAccount,
    Post,
    PostHide,
)
from ..serializers import (
    FeedFeedbackSerializer,
    MuteAccountRequestSerializer,
    MuteScopeSerializer,
    MutedAccountSerializer,
    NotInterestedRequestSerializer,
    PostHideSerializer,
    ShowFewerRequestSerializer,
    WhyResponseSerializer,
)
from user_profile.models import Follow
from django.http import Http404
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from drf_spectacular.types import OpenApiTypes
from django.db.models import Q
from rest_framework.response import Response
from ..services import (
    ShowFewerError,
    apply_show_fewer,
)
from rest_framework import (
    generics,
    status,
)
from rest_framework.decorators import permission_classes
from datetime import timedelta
from django.utils import timezone
from ._common import (
    StandardResultsSetPagination,
    User,
    _first_error_text,
)
from .feed import (
    _home_base_qs,
    _video_and_velocity_boost,
)


# ===================== FEED FEEDBACK CONTROLS - PART 1 =====================
# "Not interested" (PostHide) and "Mute this account" (MutedAccount).
# Private, silent, per-user; they only change what the CALLER's feeds show
# (Home / Explore / Hashtag - see exclude_hidden_and_muted in services.py).
# Part 2 (below): "Show fewer like this" (FeedFeedback ranking signal) + "Why am I seeing this".
def _visible_post_or_404(user, post_id):
    """A post the caller could legitimately have in a feed (so we don't leak
    the existence of private / connections-only posts they can't see)."""
    post = Post.objects.filter(id=post_id, is_deleted=False, is_archived=False, moderation_status='approved', is_scheduled=False).select_related('user').first()
    if post is None:
        raise Http404
    if post.user_id != user.id:
        if post.visibility == 'private':
            raise Http404
        if post.visibility == 'connections' and not Follow.objects.filter(
            follower=user, following_id=post.user_id, status=Follow.Status.ACCEPTED,
        ).exists():
            raise Http404
        if post.visibility in ('close_friends', 'class_campus'):
            from ..audience import can_view_post
            if not can_view_post(post, user):
                raise Http404
    return post


class NotInterestedAPIView(APIView):
    """POST   /post/<post_id>/not-interested/  {"reason": "not_interested"|"not_relevant"|"seen_too_often"|"other"}
    DELETE /post/<post_id>/not-interested/  -> undo (snackbar "Undo")

    Idempotent: first call 201, repeats 200 (a new `reason` overwrites the old one).
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Not interested (hide this post from my feeds)",
        request=NotInterestedRequestSerializer,
        responses={201: PostHideSerializer, 200: PostHideSerializer},
        tags=["Feed Feedback"],
    )
    def post(self, request, post_id):
        post = _visible_post_or_404(request.user, post_id)
        if post.user_id == request.user.id:
            return Response({"success": False, "message": "You can't hide your own post."},
                            status=status.HTTP_400_BAD_REQUEST)
        serializer = NotInterestedRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "message": _first_error_text(serializer.errors) or "Validation failed",
                             "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        reason = serializer.validated_data['reason']
        hide, created = PostHide.objects.get_or_create(user=request.user, post=post, defaults={'reason': reason})
        if not created and hide.reason != reason:
            hide.reason = reason
            hide.save(update_fields=['reason'])
        from .. import feed_cache
        feed_cache.invalidate(request.user.pk)  # T1 Part 5
        return Response({
            "success": True,
            "message": "We'll show you less like this." if created else "Already hidden.",
            "data": PostHideSerializer(hide).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @extend_schema(summary="Undo 'Not interested'", tags=["Feed Feedback"])
    def delete(self, request, post_id):
        removed, _ = PostHide.objects.filter(user=request.user, post_id=post_id).delete()
        from .. import feed_cache
        feed_cache.invalidate(request.user.pk)  # T1 Part 5
        return Response({"success": True, "removed": bool(removed),
                         "message": "Post restored." if removed else "Post was not hidden."},
                        status=status.HTTP_200_OK)


class NotInterestedListAPIView(generics.ListAPIView):
    """GET /post/not-interested/ - posts I hid (for a 'manage' screen). Newest first."""
    permission_classes = [IsAuthenticated]
    serializer_class = PostHideSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return PostHide.objects.filter(user=self.request.user)

    @extend_schema(summary="List posts I marked 'Not interested'", tags=["Feed Feedback"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class MutedAccountsAPIView(APIView):
    """GET  /post/muted-accounts/            -> accounts I muted
    POST /post/muted-accounts/ {"user_id"} -> mute (idempotent: 201 first time, 200 after)

    Mute != block: the follow relationship, profile access, DMs and search are
    untouched; the account's posts (and reposts of them) just stop appearing
    in MY Home / Explore / Hashtag feeds, and its stories drop out of MY story tray.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Muted accounts", responses={200: MutedAccountSerializer(many=True)}, tags=["Feed Feedback"])
    def get(self, request):
        qs = MutedAccount.objects.filter(user=request.user).select_related('muted_user')
        # P8: ?q= searches my muted list by username / name.
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(
                Q(muted_user__username__icontains=q)
                | Q(muted_user__first_name__icontains=q)
                | Q(muted_user__last_name__icontains=q)
            )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(MutedAccountSerializer(page, many=True, context={'request': request}).data)

    @extend_schema(summary="Mute an account", request=MuteAccountRequestSerializer,
                   responses={201: MutedAccountSerializer, 200: MutedAccountSerializer}, tags=["Feed Feedback"])
    def post(self, request):
        serializer = MuteAccountRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "message": _first_error_text(serializer.errors) or "Validation failed",
                             "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        target_id = serializer.validated_data['user_id']
        if target_id == request.user.id:
            return Response({"success": False, "message": "You can't mute yourself."},
                            status=status.HTTP_400_BAD_REQUEST)
        target = User.objects.filter(id=target_id).first()
        if target is None:
            return Response({"success": False, "message": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        # P8: what to mute. Nothing sent = everything (old contract). If the app sends only one of
        # posts / stories, the other one is OFF ("posts only" must not also hide stories).
        sent = serializer.validated_data
        if 'posts' in sent or 'stories' in sent:
            want_posts, want_stories = bool(sent.get('posts', False)), bool(sent.get('stories', False))
        else:
            want_posts, want_stories = True, True
        mute, created = MutedAccount.objects.get_or_create(
            user=request.user, muted_user=target,
            defaults={'mute_posts': want_posts, 'mute_stories': want_stories},
        )
        changed = False
        if not created and (mute.mute_posts != want_posts or mute.mute_stories != want_stories):
            mute.mute_posts, mute.mute_stories = want_posts, want_stories
            mute.save(update_fields=['mute_posts', 'mute_stories'])
            changed = True
        from .. import feed_cache
        feed_cache.invalidate(request.user.pk)  # T1 Part 5
        return Response({
            "success": True,
            "message": "Account muted." if created else ("Mute updated." if changed else "Account already muted."),
            "data": MutedAccountSerializer(mute, context={'request': request}).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class UnmuteAccountAPIView(APIView):
    """DELETE /post/muted-accounts/<user_id>/ - unmute (idempotent).

    PATCH  /post/muted-accounts/<user_id>/ {"posts": bool, "stories": bool}  (P8)
        change WHAT is muted without unmuting. A key you leave out keeps its current value. If the result
        has both off the account is simply unmuted (`removed: true`). 404 when the account is not muted.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Change what is muted (posts / stories)", request=MuteScopeSerializer,
                   responses={200: MutedAccountSerializer}, tags=["Feed Feedback"])
    def patch(self, request, user_id):
        serializer = MuteScopeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "message": _first_error_text(serializer.errors) or "Validation failed",
                             "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        mute = MutedAccount.objects.filter(user=request.user, muted_user_id=user_id).select_related('muted_user').first()
        if mute is None:
            return Response({"success": False, "message": "Account is not muted."}, status=status.HTTP_404_NOT_FOUND)
        sent = serializer.validated_data
        posts = bool(sent.get('posts', mute.mute_posts))
        stories = bool(sent.get('stories', mute.mute_stories))
        from .. import feed_cache
        if not posts and not stories:
            mute.delete()
            feed_cache.invalidate(request.user.pk)
            return Response({"success": True, "removed": True, "message": "Account unmuted."},
                            status=status.HTTP_200_OK)
        if (posts, stories) != (mute.mute_posts, mute.mute_stories):
            mute.mute_posts, mute.mute_stories = posts, stories
            mute.save(update_fields=['mute_posts', 'mute_stories'])
            feed_cache.invalidate(request.user.pk)
        return Response({"success": True, "removed": False, "message": "Mute updated.",
                         "data": MutedAccountSerializer(mute, context={'request': request}).data},
                        status=status.HTTP_200_OK)

    @extend_schema(summary="Unmute an account", tags=["Feed Feedback"])
    def delete(self, request, user_id):
        removed, _ = MutedAccount.objects.filter(user=request.user, muted_user_id=user_id).delete()
        from .. import feed_cache
        feed_cache.invalidate(request.user.pk)  # T1 Part 5
        return Response({"success": True, "removed": bool(removed),
                         "message": "Account unmuted." if removed else "Account was not muted."},
                        status=status.HTTP_200_OK)


# ===================== FEED FEEDBACK CONTROLS - PART 2 =====================
# "Show fewer like this" + "Why am I seeing this". Details / contract:
# post/FEED_FEEDBACK_CONTROLS_TASK.md. Ranking lives in post/feed_mix.py
# (penalty_expression), the explanation in post/feed_explain.py.
class ShowFewerAPIView(APIView):
    """POST /post/<post_id>/show-fewer/
    {"targets": [{"kind": "category"}, {"kind": "hashtag", "key": "python"}, {"kind": "author"}],
     "reason": "not_interested"}            # reason optional (same choices as Not interested)

    = "Not interested" (the post is hidden at once) + a decaying negative
    ranking signal for each chosen target. `kind=category|author` are derived
    from the post; `kind=hashtag` needs `key` = one of the post's hashtags.
    Tapping again adds another step (capped). 201 when the post was hidden
    just now, 200 when it already was (the signals are still added).
    Undo: DELETE /post/<id>/not-interested/ + DELETE /post/feedback/<id>/ for
    every item of `data.feedback`.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Show fewer like this (hide + dampen category / hashtag / author)",
        request=ShowFewerRequestSerializer,
        responses={201: OpenApiTypes.OBJECT, 200: OpenApiTypes.OBJECT},
        tags=["Feed Feedback"],
    )
    def post(self, request, post_id):
        post = _visible_post_or_404(request.user, post_id)
        if post.user_id == request.user.id:
            return Response({"success": False, "message": "You can't hide your own post."},
                            status=status.HTTP_400_BAD_REQUEST)
        serializer = ShowFewerRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "message": _first_error_text(serializer.errors) or "Validation failed",
                             "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        try:
            hide, hide_created, rows = apply_show_fewer(
                request.user, post, [(t['kind'], t.get('key')) for t in data['targets']], data['reason'],
            )
        except ShowFewerError as exc:
            return Response({"success": False, "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        context = {'request': request, 'author_labels': {str(post.user_id): post.user.username}}
        return Response({
            "success": True,
            "message": "We'll show you fewer posts like this.",
            "data": {
                "hidden": PostHideSerializer(hide).data,
                "feedback": FeedFeedbackSerializer(rows, many=True, context=context).data,
            },
        }, status=status.HTTP_201_CREATED if hide_created else status.HTTP_200_OK)


class FeedFeedbackListAPIView(generics.ListAPIView):
    """GET /post/feedback/[?kind=category|hashtag|author] - what I asked to see
    fewer of (for a "manage" screen), strongest/most recent first. Rows that
    decayed away completely are not listed."""
    permission_classes = [IsAuthenticated]
    serializer_class = FeedFeedbackSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        from .. import feed_mix

        qs = FeedFeedback.objects.filter(user=self.request.user)
        kind = self.request.query_params.get('kind')
        if kind in FeedFeedback.Kind.values:
            qs = qs.filter(kind=kind)
        horizon = feed_mix.feedback_horizon_days(feed_mix.get_feedback_config())
        if horizon is not None:
            qs = qs.filter(updated_at__gte=timezone.now() - timedelta(days=horizon))
        return qs

    @extend_schema(
        summary="List my 'Show fewer' preferences",
        parameters=[OpenApiParameter(name='kind', type=str, required=False,
                                     description="category | hashtag | author")],
        tags=["Feed Feedback"],
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else list(queryset)
        author_ids = []
        for row in rows:
            if row.kind == FeedFeedback.Kind.AUTHOR:
                try:
                    # User primary keys are integers (BigAutoField), NOT UUIDs. Parsing as uuid.UUID raised
                    # ValueError for every row, so author preferences were listed with label=None.
                    author_ids.append(int(row.key))
                except (TypeError, ValueError):
                    continue
        labels = {str(pk): name for pk, name in User.objects.filter(id__in=author_ids).values_list('id', 'username')} if author_ids else {}
        serializer = FeedFeedbackSerializer(rows, many=True, context={'request': request, 'author_labels': labels})
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


class FeedFeedbackDeleteAPIView(APIView):
    """DELETE /post/feedback/<feedback_id>/ - stop dampening that category /
    hashtag / author (also the undo of one 'Show fewer' item). Idempotent."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Remove one 'Show fewer' preference", tags=["Feed Feedback"])
    def delete(self, request, feedback_id):
        removed, _ = FeedFeedback.objects.filter(user=request.user, id=feedback_id).delete()
        from .. import feed_cache
        feed_cache.invalidate(request.user.pk)  # T1 Part 5
        return Response({"success": True, "removed": bool(removed),
                         "message": "We'll show you this again." if removed else "Nothing to remove."},
                        status=status.HTTP_200_OK)


class WhyAmISeeingThisAPIView(APIView):
    """GET /post/<post_id>/why/ - the reasons this post is in MY feed, taken
    from the same signals the ranking uses (post/feed_explain.py):
    following, interest_category, liked_category, friend_of_follow, trending
    (+ popular / own_post / not_in_feed). `reasons[0]` is the headline.
    `dampened` lists my active 'Show fewer' rows that match this post.
    Same visibility rules as Not interested (404 for private / connections-
    only-not-followed / deleted posts)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Why am I seeing this post?",
        responses={200: WhyResponseSerializer},
        tags=["Feed Feedback"],
    )
    def get(self, request, post_id):
        from .. import feed_explain

        post = _visible_post_or_404(request.user, post_id)
        following_ids = set(
            Follow.objects.filter(follower=request.user, status=Follow.Status.ACCEPTED)
            .values_list('following_id', flat=True)
        )
        payload = feed_explain.explain_post(
            request.user, post, _home_base_qs(request.user), following_ids, _video_and_velocity_boost,
        )
        return Response({"success": True, "data": payload}, status=status.HTTP_200_OK)
