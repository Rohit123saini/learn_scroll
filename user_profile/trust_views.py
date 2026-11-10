"""
user_profile/trust_views.py - the person-facing side of reports (audit items 17, 18).

  GET  /profile/notices/              what was done to MY content (+ can I appeal)
  POST /profile/appeals/              appeal one notice
  GET  /profile/appeals/              my appeals and their outcome
  GET  /profile/child-safety/help/    Childline / police / online reporting (public of content, needs login)
"""

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from common.open_throttles import PublicReadThrottle  # P-B3

from . import appeals, child_safety
from .models import ModerationAction, ModerationAppeal
from .serializers import (
    ModerationAppealCreateSerializer, ModerationAppealSerializer, ModerationNoticeSerializer,
)

_HTTP = {"not_found": 404, "duplicate": 409, "stale": 409, "rate_limited": 429, "not_appealable": 403, "copyright": 409}


class NoticeListView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [UserRateThrottle]

    @extend_schema(responses=ModerationNoticeSerializer(many=True))
    def get(self, request):
        rows = list(
            ModerationAction.objects.filter(target_owner=request.user, owner_visible=True)
            .prefetch_related("appeals")[:100]
        )
        latest = appeals.latest_visible_ids(request.user)
        data = []
        for r in rows:
            ap = sorted(r.appeals.all(), key=lambda a: a.created_at)
            data.append({
                "id": r.pk, "action": r.action, "target_type": r.target_type, "target_id": r.target_id,
                "reason": r.reason, "note": "", "created_at": r.created_at,  # internal note is never shown to the owner
                "can_appeal": bool(r.can_appeal and r.action in appeals.APPEALABLE and r.pk in latest and not ap),
                "appeal_status": ap[-1].status if ap else None,
            })
        return Response({"status": True, "data": ModerationNoticeSerializer(data, many=True).data})


class AppealView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [UserRateThrottle]

    @extend_schema(responses=ModerationAppealSerializer(many=True))
    def get(self, request):
        qs = ModerationAppeal.objects.filter(user=request.user)[:100]
        return Response({"status": True, "data": ModerationAppealSerializer(qs, many=True).data})

    @extend_schema(request=ModerationAppealCreateSerializer, responses=ModerationAppealSerializer)
    def post(self, request):
        ser = ModerationAppealCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response({"status": False, "message": "Validation failed.", "errors": ser.errors}, status=400)
        try:
            appeal = appeals.file_appeal(request.user, ser.validated_data["notice_id"], ser.validated_data["message"])
        except appeals.AppealError as exc:
            return Response({"status": False, "code": exc.code, "message": exc.message},
                            status=_HTTP.get(exc.code, status.HTTP_400_BAD_REQUEST))
        return Response({"status": True, "message": "Appeal received. We will review it.",
                         "data": ModerationAppealSerializer(appeal).data}, status=status.HTTP_201_CREATED)


class ChildSafetyHelpView(APIView):
    """Shown right after a child-safety report is filed (and from Help)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"status": True, "data": child_safety.help_info()})


class GrievanceOfficerView(APIView):
    """Shown on the Help screen and linked from every notice. Re-uses the contact details the
    copyright policy page already prints (settings GRIEVANCE_OFFICER_*), so there is ONE source.
    `configured` is False until name + e-mail are filled in."""

    permission_classes = []  # public: a closed account must be able to read it too
    authentication_classes = []
    throttle_classes = [PublicReadThrottle]  # P-B3

    def get(self, request):
        from copyrights.policy import policy_context

        c = policy_context()
        info = dict(c["grievance_officer"])
        info["response_days"] = c["process"]["grievance_resolve_days"]
        info["ack_hours"] = c["process"]["grievance_ack_hours"]
        info["configured"] = c["configured"]
        return Response({"status": True, "data": info})


class StaffReportQueueView(APIView):
    """P-F15: the user-report queue for staff, most urgent first (priority, then the earliest review deadline).

      GET /profile/staff/report-queue/?overdue=1&limit=50     one row per reported item
      GET /profile/staff/report-queue/summary/                 open / overdue / due soon / urgent / oldest

    Reporters are never listed (counts and reasons only). Child-safety reports are only included for staff who hold
    the child-safety permission. Deciding a report still happens in Django admin (hide / dismiss, audited)."""

    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = [UserRateThrottle]

    @extend_schema(summary="Staff: user-report queue sorted by priority + SLA", tags=["Staff moderation"])
    def get(self, request, summary=False):
        from . import report_sla

        child = child_safety.can_handle(request.user)
        if summary:
            return Response({"status": True, "data": report_sla.summary(include_child_safety=child)})
        try:
            limit = int(request.query_params.get("limit") or 50)
        except (TypeError, ValueError):
            return Response({"status": False, "message": "limit must be a number."}, status=400)
        overdue = str(request.query_params.get("overdue") or "").lower() in ("1", "true", "yes")
        rows = report_sla.queue(include_child_safety=child, overdue_only=overdue, limit=limit)
        return Response({"status": True, "count": len(rows), "data": rows})
