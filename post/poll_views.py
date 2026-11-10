"""
post/poll_views.py - Poll + Quiz endpoints (all under /post/...).

  POST /post/<post_id>/poll/vote/                  cast / change a vote (double-vote guarded)
  GET  /post/<post_id>/poll/results/               creator: counts + who picked what (public polls) 
  GET  /post/<post_id>/poll/results/export/        creator: CSV   (?view=summary for per-option totals only)
  GET  /post/<post_id>/poll/leaderboard/           quiz: ranked correct answerers + my rank
  GET  /post/quiz/my-score/                        my quiz totals (answered / correct / accuracy)
  POST /post/<post_id>/poll/convert-to-question/   creator: add this poll/quiz as a Test Series question

Every business-rule failure is `{"detail": "...", "code": "..."}` with the right status; the rules themselves live
in post/polls.py so they are unit-testable without HTTP.
"""
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import polls
from .models import Post, PostPoll
from .moderation_guard import post_hidden_for
from .serializers import PollVoteRequestSerializer, PostPollSerializer


def _error(exc):
    return Response({"detail": exc.message, "code": exc.code}, status=exc.status)


def _post_and_poll(request, post_id):
    """The post + its poll, 404 when the viewer may not see the post or it has no poll."""
    post = get_object_or_404(Post, id=post_id, is_deleted=False)
    if post_hidden_for(request.user, post):
        raise Http404("Post not found.")
    try:
        poll = PostPoll.objects.select_related("post").prefetch_related("options").get(post=post)
    except PostPoll.DoesNotExist:
        raise Http404("This post has no poll.")
    return post, poll


def _owner_poll(request, post_id):
    post, poll = _post_and_poll(request, post_id)
    if post.user_id != request.user.id:
        return None
    return poll


def _not_owner_response():
    return Response(
        {"detail": "Only the poll's creator can do this.", "code": "not_owner"}, status=status.HTTP_403_FORBIDDEN)


class PostPollVoteAPIView(APIView):
    """Cast / change a vote. Returns the poll's fresh viewer-aware state (see `PostPollSerializer`) so the client
    shows the new numbers - or, on a quiz, the verdict + explanation - without a second request.

    Double-vote guard (details in `polls.cast_vote`): row lock + `UniqueConstraint(poll, user, option)`; a quiz
    answer is final (409), the same selection sent twice is a harmless 200, a normal poll lets you change your vote.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "post_poll_vote"

    @extend_schema(
        summary="Vote on a poll / answer a quiz",
        request=PollVoteRequestSerializer,
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Polls"],
    )
    def post(self, request, post_id):
        post, poll = _post_and_poll(request, post_id)
        serializer = PollVoteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            poll = polls.cast_vote(poll.id, request.user, serializer.validated_data["option_ids"])
        except polls.PollError as exc:
            return _error(exc)
        return Response(PostPollSerializer(poll, context={"request": request}).data)


class PostPollResultsAPIView(APIView):
    """Creator-only results screen: per-option counts / % and, for a PUBLIC poll, who chose what (paginated by
    voter: `?limit=50&offset=0`). An anonymous poll returns `voters: null` - no one is ever identified."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Poll results (creator)", responses={200: OpenApiTypes.OBJECT}, tags=["Post Polls"])
    def get(self, request, post_id):
        poll = _owner_poll(request, post_id)
        if poll is None:
            return _not_owner_response()
        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 200))
            offset = max(0, int(request.query_params.get("offset", 0)))
        except ValueError:
            return Response({"detail": "limit/offset must be numbers.", "code": "bad_page"}, status=400)
        return Response(polls.build_results(poll, request=request, limit=limit, offset=offset))


class PostPollResultsExportAPIView(APIView):
    """Creator-only CSV. Public poll: one row per voter (username, name, chosen options, verdict for a quiz, time).
    Anonymous poll - or `?view=summary` - per-option totals only."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Export poll results as CSV (creator)", responses={200: OpenApiTypes.BINARY}, tags=["Post Polls"])
    def get(self, request, post_id):
        poll = _owner_poll(request, post_id)
        if poll is None:
            return _not_owner_response()
        summary = request.query_params.get("view") == "summary"
        body = polls.build_csv(poll, summary=summary)
        response = HttpResponse(body, content_type="text/csv; charset=utf-8")
        name = f"poll-{str(poll.post_id)[:8]}-{'summary' if (summary or poll.is_anonymous) else 'votes'}.csv"
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["Cache-Control"] = "no-store"
        return response


class PostPollLeaderboardAPIView(APIView):
    """Quiz leaderboard: correct answerers, first-correct-first, plus the viewer's own rank. Needs the viewer to
    have answered (or be the creator / the quiz to have ended) - the list itself would give the answer away."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Quiz leaderboard", responses={200: OpenApiTypes.OBJECT}, tags=["Post Polls"])
    def get(self, request, post_id):
        post, poll = _post_and_poll(request, post_id)
        try:
            return Response(polls.build_leaderboard(poll, request.user, request=request))
        except polls.PollError as exc:
            return _error(exc)


class PostQuizMyScoreAPIView(APIView):
    """`{attempted, correct, accuracy_percent}` over every quiz the caller has answered."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="My quiz score", responses={200: OpenApiTypes.OBJECT}, tags=["Post Polls"])
    def get(self, request):
        return Response(polls.my_quiz_score(request.user))


class PollConvertRequestSerializer(serializers.Serializer):
    series_id = serializers.UUIDField()
    marks = serializers.IntegerField(min_value=1, max_value=100, required=False, default=1)
    negative_marks = serializers.IntegerField(min_value=0, max_value=100, required=False, default=0)
    # Required for a normal poll (a quiz already knows its answers).
    correct_option_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=False, max_length=6)
    question_text = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    topic = serializers.CharField(required=False, allow_blank=True, max_length=80)
    difficulty = serializers.ChoiceField(choices=["", "easy", "medium", "hard"], required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs.get("negative_marks", 0) > attrs.get("marks", 1):
            raise serializers.ValidationError({"negative_marks": "negative_marks cannot be more than marks."})
        return attrs


class PostPollConvertToQuestionAPIView(APIView):
    """Creator-only: append this poll / quiz to one of their DRAFT test series as an MCQ (one right answer) or MSQ
    (several). Uses the testseries bridge, so the series' own rules (creator / staff only, draft only) apply."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Convert a poll/quiz into a Test Series question",
        request=PollConvertRequestSerializer,
        responses={201: OpenApiTypes.OBJECT},
        tags=["Post Polls"],
    )
    def post(self, request, post_id):
        poll = _owner_poll(request, post_id)
        if poll is None:
            return _not_owner_response()
        body = PollConvertRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        try:
            result = polls.convert_to_question(
                poll, request.user, data["series_id"], request=request,
                marks=data["marks"], negative_marks=data["negative_marks"],
                correct_option_ids=data.get("correct_option_ids"),
                question_text=data.get("question_text", ""),
                topic=data.get("topic", ""), difficulty=data.get("difficulty", ""),
            )
        except polls.PollError as exc:
            return _error(exc)
        return Response(result, status=status.HTTP_201_CREATED)
