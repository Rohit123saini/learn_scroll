"""
tuitionclass/views/dashboard.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

from core.models import Notification
from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from ..models import (
    Certificate,
    ClassJoinRequest,
    ClassSession,
    Classroom,
    ClassroomWishlist,
    PassPurchase,
)
from ..serializers import ClassSessionSerializer

from .access import _accessible_classroom_ids


# ---------------------------------------------------------------------------
# HOME DASHBOARD — single-call summary for the app's home/landing screen.
#
# WHY THIS EXISTS: without it, a mobile home screen needing "what's next"
# has to fire N separate requests (upcoming sessions, my classrooms,
# certificates earned, wishlist, pending requests) and stitch them together
# client-side — slower first paint, more battery/data on mobile, and N
# separate chances for one of them to fail. This is the same "one call for
# the whole screen" pattern classrooms/{id}/stats/ already uses for the
# classroom detail page, applied to the app's home screen instead.
# Everything here is a cheap COUNT/LIMIT query — no N+1, nothing here
# duplicates another endpoint's full detail (the client still hits
# /sessions/, /certificates/, etc. for full lists — this is a summary only).
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# NOTE (task 42 — core-app migration): NotificationViewSet and
# NotificationPreferenceView used to live here. They have moved to
# core/views.py and are wired via core/urls.py (see core_app_documentation.md).
# `tuitionclass/urls.py` no longer registers `notifications/` or
# `notification-preferences/me/` — those paths are now served under the
# `core/` prefix wired in the root urlconf.
# ---------------------------------------------------------------------------

class MyDashboardView(APIView):
    """GET /tuitionclass/dashboard/ — the logged-in user's home-screen summary:
    next few upcoming sessions they can join, how many classrooms they
    teach vs are currently enrolled in, certificates earned, wishlist size,
    and any join requests still awaiting a teacher's decision."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        now = timezone.now()
        accessible_ids = _accessible_classroom_ids(user)

        upcoming_sessions = (
            ClassSession.objects.filter(
                classroom_id__in=accessible_ids,
                status__in=[ClassSession.Status.SCHEDULED, ClassSession.Status.LIVE],
                scheduled_start__lte=now + timezone.timedelta(days=7),
                scheduled_end__gte=now,
            )
            .select_related("classroom")
            .order_by("scheduled_start")[:5]
        )

        teaching_count = Classroom.objects.filter(teacher=user, is_deleted=False).count()

        enrolled_count = (
            PassPurchase.objects.filter(
                student=user,
                status=PassPurchase.Status.SUCCESS,
                is_active=True,
                expires_at__gt=now,
            )
            .values("class_pass__classroom_id")
            .distinct()
            .count()
        )

        return Response(
            {
                # TASK 10.1 — one authoritative clock for the whole payload
                # (the home "Next class" card uses it for its countdown).
                "server_now": now.isoformat(),
                "upcoming_sessions": ClassSessionSerializer(
                    upcoming_sessions, many=True, context={"request": request}
                ).data,
                "teaching_classrooms_count": teaching_count,
                "enrolled_classrooms_count": enrolled_count,
                "certificates_count": Certificate.objects.filter(student=user).count(),
                "wishlist_count": ClassroomWishlist.objects.filter(user=user).count(),
                "pending_join_requests_count": ClassJoinRequest.objects.filter(
                    student=user, status=ClassJoinRequest.Status.PENDING
                ).count(),
                "unread_notifications_count": Notification.objects.filter(
                    recipient=user, is_read=False
                ).count(),
            }
        )


# ---------------------------------------------------------------------------
# HEALTH CHECK (load balancer / uptime monitoring)
# ---------------------------------------------------------------------------
class HealthCheckView(APIView):
    """GET /tuitionclass/healthz/ — unauthenticated liveness/readiness probe for
    a load balancer or uptime monitor (Render health check, UptimeRobot, a
    k8s readiness probe, etc.).

    Deliberately OUTSIDE IsAuthenticated: infra that has no credentials
    can't hit an authenticated endpoint, so a health check that requires
    auth is useless to the thing that's supposed to be checking it.

    Each dependency below is checked independently and wrapped in its own
    try/except so one broken dependency reports its own status instead of
    an unhandled 500 taking the whole endpoint down — that distinction
    ("the app process itself is dead" vs "the app is up but its DB/cache
    is unreachable") is exactly the thing a status page/alert needs to be
    able to tell apart.

    Checks:
        database — a trivial `SELECT 1` against the primary DB connection.
        cache    — a round-trip set/get against Django's configured cache
                   backend (Redis in production — see settings.py).

    The HTTP status code is what the load balancer/monitor actually acts
    on: 200 only if every check passes, 503 if any fails. The JSON body
    (which check failed, and why) is for a human looking at a dashboard or
    debugging an alert — never assume the caller parses it.
    """

    permission_classes = []
    authentication_classes = []

    def get(self, request):
        checks = {}
        healthy = True

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            checks["database"] = "ok"
        except Exception as exc:
            healthy = False
            checks["database"] = f"error: {exc}"

        try:
            probe_key = "tuitionclass:healthz:probe"
            cache.set(probe_key, "1", timeout=5)
            if cache.get(probe_key) != "1":
                raise RuntimeError("cache round-trip returned an unexpected value")
            checks["cache"] = "ok"
        except Exception as exc:
            healthy = False
            checks["cache"] = f"error: {exc}"

        return Response(
            {"status": "ok" if healthy else "error", "checks": checks},
            status=200 if healthy else 503,
        )
