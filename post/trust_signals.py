"""
post/trust_signals.py  (H15 - trust + quality signals on feed cards)

What a card can now show next to the content, and where the number comes from:

  author badge        `user.badge` on the card's author (and on a doubt answer's author):
                        "verified_teacher"  verified account (`user_profile.services.teacher_verified_field()`, default
                                            `User.is_verified`) AND the account chose the teacher role in onboarding
                        "verified"          verified account, any other role
                        null                everything else
                      The onboarding role alone grants NOTHING (anyone can pick "teacher" - see login.User.onboarding_role),
                      so a badge always needs the verification flag. Read off the already-loaded author row: no query.
  answered by teacher `answered_by_teacher` on a doubt card: at least one answer was written by a `verified_teacher`.
                      One query per feed PAGE (not per card), only when the page has doubt posts.
  helpful for N       `helpful_count` / `is_helpful_by_me` on a Notes / Resource card, from `PostHelpful` rows
                      (POST /post/<id>/helpful/). One grouped query per page, only when the page has resource posts.

`stamp_trust(items, user)` is called from `serializers.ViewerStateListSerializer._stamp` (the batch place that already
exists for the viewer state), and the serializer methods fall back to a per-object query only for a SINGLE post
(detail endpoint), never inside a list.
"""
import logging

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Post, PostAnswer, PostHelpful
from .resource_views import _err, _visible_resources

logger = logging.getLogger(__name__)

BADGE_VERIFIED_TEACHER = "verified_teacher"
BADGE_VERIFIED = "verified"


def _verified_field():
    try:
        from user_profile.services import teacher_verified_field

        return teacher_verified_field()
    except Exception:  # noqa: BLE001 - a badge must never break a feed page
        return "is_verified"


def author_trust(user, field=None):
    """{is_verified, is_teacher, badge} for one account row (no query)."""
    if user is None:
        return {"is_verified": False, "is_teacher": False, "badge": None}
    verified = bool(getattr(user, field or _verified_field(), False))
    teacher = verified and getattr(user, "onboarding_role", "") == "teacher"
    return {
        "is_verified": verified,
        "is_teacher": teacher,
        "badge": BADGE_VERIFIED_TEACHER if teacher else (BADGE_VERIFIED if verified else None),
    }


def teacher_answered_post_ids(post_ids):
    """Subset of `post_ids` that has at least one answer by a verified teacher (ONE query)."""
    if not post_ids:
        return set()
    field = _verified_field()
    try:
        return set(
            PostAnswer.objects.filter(
                post_id__in=list(post_ids), user__onboarding_role="teacher", **{f"user__{field}": True}
            ).values_list("post_id", flat=True).distinct()
        )
    except Exception:  # noqa: BLE001
        logger.warning("teacher_answered_post_ids failed", exc_info=True)
        return set()


def helpful_counts(post_ids, user):
    """{post_id: (count, mine)} for the given resource posts (ONE grouped query)."""
    if not post_ids:
        return {}
    rows = (
        PostHelpful.objects.filter(post_id__in=list(post_ids))
        .values("post_id")
        .annotate(n=Count("id"), mine=Count("id", filter=Q(user=user)))
    )
    return {r["post_id"]: (r["n"], r["mine"] > 0) for r in rows}


def stamp_trust(items, user):
    """Batch for one feed page: sets `helpful_prefetched` on resource posts and `teacher_answered_prefetched` on
    doubts. Called from ViewerStateListSerializer._stamp; a failure leaves the fields at their safe defaults."""
    try:
        resources = [p for p in items if getattr(p, "kind", "") == "resource"]
        counts = helpful_counts([p.pk for p in resources], user)
        for p in resources:
            p.helpful_prefetched = counts.get(p.pk, (0, False))
        doubts = [p for p in items if getattr(p, "post_type", "") == "doubt"]
        answered = teacher_answered_post_ids([p.pk for p in doubts])
        for p in doubts:
            p.teacher_answered_prefetched = p.pk in answered
    except Exception:  # noqa: BLE001
        logger.warning("stamp_trust failed", exc_info=True)


def helpful_for(post, user):
    """(count, mine) of ONE resource post - the prefetched value, else one query (detail endpoint only)."""
    got = getattr(post, "helpful_prefetched", None)
    if got is not None:
        return got
    if getattr(post, "kind", "") != "resource" or user is None or not getattr(user, "is_authenticated", False):
        return (0, False)
    return helpful_counts([post.pk], user).get(post.pk, (0, False))


def teacher_answered_for(post):
    got = getattr(post, "teacher_answered_prefetched", None)
    if got is not None:
        return got
    if getattr(post, "post_type", "") != "doubt":
        return False
    return post.pk in teacher_answered_post_ids([post.pk])


class PostHelpfulAPIView(APIView):
    """POST /post/<id>/helpful/  {desired: true|false}  -> {helpful, count}.  Idempotent: the same `desired` twice is the
    same result. Resource posts only; the author cannot mark their own post."""
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, id):
        post = get_object_or_404(_visible_resources(request.user), pk=id)
        if post.user_id == request.user.id:
            return _err("own_post", "You cannot mark your own post as helpful.", status.HTTP_403_FORBIDDEN)
        raw = request.data.get("desired", True)
        desired = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes", "on")
        if desired:
            PostHelpful.objects.get_or_create(post=post, user=request.user)
        else:
            PostHelpful.objects.filter(post=post, user=request.user).delete()
        count = PostHelpful.objects.filter(post=post).count()
        return Response({"success": True, "data": {"helpful": desired, "count": count}})
