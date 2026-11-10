"""aiquality/views.py - P-F6: AI quality loop HTTP layer.

STUDENT
  POST /ai-quality/feedback/        {surface, feature, rating: up|down|none, wrong?: bool, reason?, comment?, ref_id?,
                                     call_id?, ai_meta?: {...echo of the answer's ai_meta}, excerpt?}
       -> {success, feedback: {id, rating, reported_wrong, reason} | null, cached_result_removed}
  GET  /ai-quality/feedback/mine/?surface=&feature=&ref_id=     -> {success, feedback: {...} | null}   (restore the bar)
STAFF (is_staff, else 403)
  GET   /ai-quality/staff/feedback/?status=new|...|all&surface=&feature=&prompt_version=&only=down|wrong|up&limit=&offset=
  PATCH /ai-quality/staff/feedback/<id>/   {status?, staff_note?, remove_cached?: bool}
  GET   /ai-quality/staff/summary/?days=30        👎 rate per feature x prompt version
  GET   /ai-quality/staff/cost/?days=7            cost guard dashboard

Error shape: {success: False, code, message}. The feedback endpoints are NOT behind the `ai_features` kill switch: a
student must always be able to report a bad answer, even when AI itself is switched off.
"""
import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from . import dashboard, prompts, services
from .models import AiFeedback

logger = logging.getLogger(__name__)


class AiFeedbackThrottle(UserRateThrottle):
    scope = "ai_feedback"


class AiFeedbackReportThrottle(UserRateThrottle):
    scope = "ai_feedback_report"


def _err(code, message, http=status.HTTP_400_BAD_REQUEST):
    return Response({"success": False, "code": code, "message": message}, status=http)


def _mine_json(fb):
    if fb is None:
        return None
    return {"id": str(fb.id), "rating": fb.rating, "reported_wrong": fb.reported_wrong, "reason": fb.reason,
            "status": fb.status}


class AiFeedbackAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        wrong = False
        try:
            wrong = services._as_bool(self.request.data.get("wrong"))
        except Exception:
            pass
        return [AiFeedbackThrottle()] + ([AiFeedbackReportThrottle()] if wrong else [])

    def post(self, request):
        data = request.data if hasattr(request.data, "get") else {}
        try:
            fb, removed = services.submit(request.user, data)
        except services.FeedbackError as e:
            return _err(e.code, e.message, e.http)
        return Response({"success": True, "feedback": _mine_json(fb), "cached_result_removed": removed})


class AiFeedbackMineAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        surface = (request.query_params.get("surface") or "").strip().lower()
        feature = (request.query_params.get("feature") or "").strip().lower()
        ref_id = request.query_params.get("ref_id") or ""
        if surface not in prompts.FEEDBACK_SURFACES or not prompts.known(surface, feature) or not ref_id:
            return Response({"success": True, "feedback": None})
        return Response({"success": True, "feedback": _mine_json(services.mine(request.user, surface, feature, ref_id))})


# ------------------------------------------------------------------------------------------------- staff
def _staff_row(fb):
    return {
        "id": str(fb.id),
        "created_at": fb.created_at.isoformat(),
        "surface": fb.surface, "feature": fb.feature, "ref_id": fb.ref_id,
        "prompt_version": fb.prompt_version or "?", "model": fb.model,
        "rating": fb.rating, "reported_wrong": fb.reported_wrong, "reason": fb.reason,
        "comment": fb.comment, "excerpt": fb.excerpt,
        "status": fb.status, "staff_note": fb.staff_note,
        "reviewed_at": fb.reviewed_at.isoformat() if fb.reviewed_at else None,
        "user_id": fb.user_id,
        "call_id": str(fb.call_id) if fb.call_id else None,
    }


class StaffAiFeedbackListAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        q = request.query_params
        qs = AiFeedback.objects.all()
        st = (q.get("status") or "new").strip().lower()
        if st != "all":
            if st not in services.STAFF_STATUSES:
                return _err("invalid_status", "Unknown status.")
            qs = qs.filter(status=st)
        only = (q.get("only") or "").strip().lower()
        if only == "down":
            qs = qs.filter(rating=-1)
        elif only == "wrong":
            qs = qs.filter(reported_wrong=True)
        elif only == "up":
            qs = qs.filter(rating=1)
        elif st == "new":
            qs = qs.filter(rating=-1)  # the default queue = things students were unhappy about
        for field in ("surface", "feature", "prompt_version"):
            v = (q.get(field) or "").strip()
            if v:
                qs = qs.filter(**{field: v})
        try:
            limit = max(1, min(int(q.get("limit") or 30), 100))
            offset = max(0, int(q.get("offset") or 0))
        except ValueError:
            limit, offset = 30, 0
        qs = qs.order_by("-reported_wrong", "created_at")
        rows = list(qs[offset:offset + limit + 1])
        has_more = len(rows) > limit
        return Response({"success": True, "results": [_staff_row(r) for r in rows[:limit]], "has_more": has_more,
                         "next_offset": offset + limit if has_more else None})


class StaffAiFeedbackDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]

    def patch(self, request, pk):
        fb = get_object_or_404(AiFeedback, pk=pk)
        d = request.data
        try:
            removed = services.review(
                fb, request.user,
                status=d.get("status") if "status" in d else None,
                note=d.get("staff_note") if "staff_note" in d else None,
                remove_cached=services._as_bool(d.get("remove_cached")))
        except services.FeedbackError as e:
            return _err(e.code, e.message, e.http)
        return Response({"success": True, "feedback": _staff_row(fb), "cached_result_removed": removed})


class StaffAiQualitySummaryAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        try:
            days = int(request.query_params.get("days") or 30)
        except ValueError:
            days = 30
        rows = services.quality_summary(days)
        open_total = AiFeedback.objects.filter(status=AiFeedback.Status.NEW).filter(
            rating=-1).count() + AiFeedback.objects.filter(status=AiFeedback.Status.NEW, rating=1, reported_wrong=True).count()
        return Response({"success": True, "days": max(1, min(days, 365)), "rows": rows, "open_total": open_total,
                         "needs_attention": sum(1 for r in rows if r["needs_attention"])})


class StaffAiCostAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        try:
            days = int(request.query_params.get("days") or 7)
        except ValueError:
            days = 7
        return Response({"success": True, **dashboard.cost_dashboard(days)})
