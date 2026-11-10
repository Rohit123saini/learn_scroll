# post/views/doubts.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from rest_framework.permissions import (
    AllowAny,
    IsAuthenticated,
)
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import PermissionDenied
from ..models import (
    Post,
    PostAnswer,
)
from ..serializers import (
    PostAnswerCreateSerializer,
    PostAnswerSerializer,
)
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from drf_spectacular.utils import extend_schema
from rest_framework import (
    generics,
    parsers,
    status,
)
from django.shortcuts import get_object_or_404
from ..services import notify_post_answered
from rest_framework.decorators import (
    parser_classes,
    permission_classes,
)


class PostAnswerListCreateAPIView(generics.ListCreateAPIView):
    """Answers on a 'doubt' (Ask-a-doubt) post. Listing is public (matches
    PostReactionAPIView's GET-is-AllowAny pattern); posting an answer
    needs auth. Best answer first, then most-liked, then oldest-first
    (PostAnswer.Meta.ordering)."""
    serializer_class = PostAnswerSerializer
    pagination_class = PageNumberPagination
    # Doubt post upgrade: an answer may carry a step-wise solution, pictures and a video (multipart); JSON still works.
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]
    throttle_scope = "post_doubt_answer"

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_throttles(self):
        if self.request.method == "POST":
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        from ..moderation_guard import post_hidden_for
        from user_profile.services import blocked_user_ids as _blocked_ids

        post = Post.objects.filter(id=self.kwargs["post_id"], is_deleted=False).select_related("user").first()
        if post is None or post_hidden_for(self.request.user, post):
            return PostAnswer.objects.none()
        qs = PostAnswer.objects.filter(post_id=post.id).select_related("user").prefetch_related("steps", "attachments")
        user = self.request.user
        if user.is_authenticated:
            blocked = _blocked_ids(user)
            if post.user_id in blocked:  # blocked with the post's author: same as "no such post"
                return PostAnswer.objects.none()
            qs = qs.exclude(user_id__in=blocked)
        return qs

    @extend_schema(summary="List answers on a doubt post", tags=["Post Doubts"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        summary="Answer a doubt post",
        request=PostAnswerCreateSerializer,
        responses={201: PostAnswerSerializer},
        tags=["Post Doubts"],
    )
    def create(self, request, *args, **kwargs):
        post = get_object_or_404(Post, id=self.kwargs["post_id"], is_deleted=False)
        from ..moderation_guard import post_hidden_for
        if post_hidden_for(request.user, post):
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        if post.user_id != request.user.id:
            from user_profile.views import is_blocked_between
            if is_blocked_between(request.user, post.user):
                return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        if post.post_type != "doubt":
            return Response(
                {"detail": "Answers are only for 'Ask a doubt' posts."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from .. import doubts

        doubts.check_answer_quota(post, request.user)
        input_serializer = PostAnswerCreateSerializer(data=request.data, context={"request": request})
        input_serializer.is_valid(raise_exception=True)
        cleaned = input_serializer.validated_data

        # H23 publish gate on the answer (already sanitised by doubts.clean_answer_input): spam rate limit, abusive /
        # NSFW text (an answer has no hold state, so it is refused 422), duplicate text / file, audio-music flag.
        from .. import publish_gate

        gate_files = [s["image"] for s in cleaned["steps"] if s.get("image") is not None]
        gate_files += list(cleaned["images"])
        gate_types = ["image"] * len(gate_files)
        if cleaned["video"] is not None:
            gate_files.append(cleaned["video"])
            gate_types.append("video")
        gate_texts = [t for t in [cleaned["content"]] + [s["text"] for s in cleaned["steps"]] if t]
        verdict = publish_gate.evaluate(
            request.user, "answer", texts=gate_texts, files=gate_files, media_types=gate_types,
        )
        if not verdict.ok:
            return verdict.response()

        answer = doubts.create_answer(post, request.user, cleaned)
        publish_gate.after_create(verdict, request.user, "answer", answer)
        notify_post_answered(post, answer)
        out = PostAnswerSerializer(answer, context={"request": request})
        return Response(out.data, status=status.HTTP_201_CREATED)


class PostAnswerMarkBestAPIView(APIView):
    """Pin/unpin the best answer on a doubt post. Only the post's own
    author can pin (they're the one who asked); pinning an already-pinned
    answer again unpins it. Exactly one best answer per post — pinning a
    new one clears any previous pin first.

    Doubt post upgrade: pinning also marks the doubt SOLVED (`doubt.is_solved`, the "Solved" badge) and pays a held
    coin bounty to the answerer (post/doubts.toggle_best_answer; the coins were taken from the asker when the doubt
    was posted). Once a bounty is paid the best answer is locked: 409 `bounty_paid`. Wallet busy: 503 + Retry-After."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark/unmark an answer as the best answer", tags=["Post Doubts"])
    def post(self, request, answer_id):
        from user_profile.models import CoinLedgerBusy

        from .. import doubts

        answer = get_object_or_404(PostAnswer.objects.select_related("post", "user"), id=answer_id)
        post = answer.post
        if post.is_deleted:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        if post.user_id != request.user.id:
            raise PermissionDenied("Only the person who asked can mark the best answer.")

        try:
            result = doubts.toggle_best_answer(answer)
        except doubts.BestAnswerLocked as exc:
            return Response({"detail": str(exc), "code": "bounty_paid"}, status=status.HTTP_409_CONFLICT)
        except CoinLedgerBusy:
            resp = Response({"detail": "Wallet busy hai, thodi der baad dobara try karo.", "code": "wallet_busy"},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
            resp["Retry-After"] = "2"
            return resp

        if result["state"] == "pinned":
            doubts.notify_best_answer(post, result["answer"], result)

        return Response({
            "status": result["state"],
            "answer": PostAnswerSerializer(result["answer"], context={"request": request}).data,
            "is_solved": result["is_solved"],
            "bounty_coins": result["bounty_coins"],
            "bounty_paid": result["bounty_paid"],
            "bounty_refunded": result["bounty_refunded"],
            "wallet_balance": doubts._wallet_balance(request.user),
        })


class DoubtConfigAPIView(APIView):
    """GET /post/doubts/config/ - limits for the doubt composer / answer sheet (bounty range, steps, files) and the
    caller's wallet balance, so the app never hard-codes them."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Doubt composer limits + wallet balance", tags=["Post Doubts"])
    def get(self, request):
        from .. import doubts

        return Response(doubts.doubt_config(request.user))


class DoubtSimilarAPIView(APIView):
    """GET /post/doubts/similar/?q=<question or OCR text>&subject=<optional>&limit=5

    "Is this doubt already asked?" - the composer calls it (debounced) while the student types / after the photo
    is read, and shows the matches (solved ones first, with their best answer) before posting, so duplicates are
    not created. Returns {"results": [...], "min_chars": n}; a query shorter than `min_chars` gives no results."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "post_doubt_similar"

    @extend_schema(summary="Similar existing doubts (duplicate check)", tags=["Post Doubts"])
    def get(self, request):
        from .. import doubts

        query = doubts.normalize_query(request.query_params.get("q"))
        results = []
        if len(query) >= doubts.SIMILAR_MIN_CHARS:
            results = doubts.find_similar(
                request.user, query, subject=request.query_params.get("subject", ""),
                limit=doubts.clamp_limit(request.query_params.get("limit")), request=request)
        return Response({"results": results, "min_chars": doubts.SIMILAR_MIN_CHARS})
