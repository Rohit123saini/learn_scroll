"""
spaced_repetition/views.py - REST API of P-F8, mounted at /revision/ (see urls.py). Login required; behind SRS_ENABLED
(off => 403 {"code": "feature_disabled", "feature": "spaced_repetition"}). Error body: {"detail", "code"}.

  GET  /revision/due/?limit=      today's session: {"cards": [...], "counts": {...}, "enabled": true}
  POST /revision/review/          {"post_id", "grade": again|hard|good|easy, "duration_ms"?} -> new schedule + counts
  GET  /revision/stats/?days=7    counts + per-day reviews + retention + learned/learning + streak
  GET  /revision/config/          {"enabled", "new_per_day", "max_queue", "relearn_minutes"} 
"""
import logging
import uuid

from django.utils import timezone
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from post.audience import can_view_post

from . import conf, services
from .throttles import RevisionReadThrottle, RevisionReviewThrottle

logger = logging.getLogger(__name__)


class FeatureDisabled(APIException):
    status_code = 403
    default_detail = {"detail": "Spaced repetition is switched off right now.", "code": "feature_disabled",
                      "feature": "spaced_repetition"}


def _err(detail, code, status=400):
    return Response({"detail": detail, "code": code}, status=status)


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


class SrsView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [RevisionReadThrottle]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not conf.enabled():
            raise FeatureDisabled()


def _snippet(text, limit=600):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _thumb(post, request):
    for m in post.media.all():
        f = getattr(m, "thumbnail", None) or (m.file if m.media_type == "image" else None)
        if f:
            try:
                return request.build_absolute_uri(f.url)
            except Exception:  # noqa: BLE001 - storage without a URL must not break a card
                continue
    return None


def serialize_card(post, card, request, is_new: bool) -> dict:
    return {
        "post_id": str(post.id),
        "title": (post.title or "").strip(),
        "content": _snippet(post.content),
        "subject": post.subject or "",
        "category": post.category or "",
        "post_type": post.post_type,
        "topics": list(post.topics or [])[:6],
        "thumbnail": _thumb(post, request),
        "is_new": is_new,
        "state": services.serialize_card_state(card) if card is not None else None,
        "preview": services.preview_for(card),
    }


class RevisionDueView(SrsView):
    def get(self, request):
        now = timezone.now()
        limit = _int(request.query_params.get("limit"), conf.max_queue(), 1, conf.max_queue())
        queue = services.build_queue(request.user, now, limit)
        cards = []
        for card in queue["due"]:
            if can_view_post(card.post, request.user):
                cards.append(serialize_card(card.post, card, request, False))
        for save in queue["new"]:
            if can_view_post(save.post, request.user):
                cards.append(serialize_card(save.post, None, request, True))
        return Response({"enabled": True, "cards": cards, "counts": services.counts(request.user, now)})


class RevisionReviewView(SrsView):
    throttle_classes = [RevisionReviewThrottle]

    def post(self, request):
        post_id = _uuid_or_none(request.data.get("post_id"))
        if post_id is None:
            return _err("post_id is required.", "invalid_post_id")
        try:
            result = services.submit_review(
                request.user, post_id, request.data.get("grade"), timezone.now(), request.data.get("duration_ms"),
            )
        except services.SrsError as exc:
            return _err(exc.detail, exc.code, exc.status)
        return Response({"success": True, **result})


class RevisionStatsView(SrsView):
    def get(self, request):
        days = _int(request.query_params.get("days"), 7, 1, 30)
        return Response(services.stats(request.user, timezone.now(), days))


class RevisionConfigView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [RevisionReadThrottle]

    def get(self, request):
        return Response({
            "enabled": conf.enabled(), "new_per_day": conf.new_per_day(), "max_queue": conf.max_queue(),
            "relearn_minutes": conf.relearn_minutes(),
        })
