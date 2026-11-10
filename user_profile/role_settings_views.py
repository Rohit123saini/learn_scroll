"""
user_profile/role_settings_views.py - S12: role-aware settings API.

  GET/PATCH /profile/teacher/live-defaults/                  a teacher's defaults for new classrooms / live classes
  GET       /profile/parent/children/                        linked children: today's minutes + the limit
  PATCH     /profile/parent/children/<child_id>/limit/       {"daily_limit_minutes": 90 | null}  (null / 0 = remove)

Which sections the app shows comes from `GET /core/me/capabilities/` (core/capabilities.py). Envelope like the rest of
user_profile: {"status": bool, "message": str, "data": ...}.
"""
from __future__ import annotations

from rest_framework import status as http
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from . import role_settings as rs


def _ok(message, data=None, code=http.HTTP_200_OK):
    return Response({"status": True, "message": message, "data": data}, status=code)


def _err(message, code=http.HTTP_400_BAD_REQUEST, **extra):
    return Response({"status": False, "message": message, **extra}, status=code)


class TeacherLiveDefaultsView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "role_settings"

    def get(self, request):
        return _ok("Teacher defaults fetched.", rs.serialize_teacher_defaults(rs.get_teacher_defaults(request.user)))

    def patch(self, request):
        obj, errors = rs.update_teacher_defaults(request.user, request.data)
        if errors:
            return _err("Validation failed.", errors=errors)
        return _ok("Teacher defaults saved.", rs.serialize_teacher_defaults(obj))


class ParentChildrenView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "role_settings"

    def get(self, request):
        return _ok(
            "Children fetched.",
            {
                "children": rs.list_children(request.user, request),
                "min_minutes": rs.MIN_CHILD_LIMIT,
                "max_minutes": rs.MAX_CHILD_LIMIT,
            },
        )


class ParentChildPrivacyView(APIView):
    """P-F16: GET /profile/parent/children/<child_id>/privacy/ - read-only privacy + minor rules of a linked child."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "role_settings"

    def get(self, request, child_id):
        try:
            data = rs.child_privacy(request.user, child_id)
        except rs.ChildLimitError as exc:
            return _err(exc.message, exc.http_status, code=exc.code)
        return _ok("Child privacy fetched.", data)


class ParentChildLimitView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "role_settings"

    def patch(self, request, child_id):
        if "daily_limit_minutes" not in request.data:
            return _err("daily_limit_minutes is required (a number, or null to remove).", code="invalid_limit")
        try:
            row = rs.set_child_limit(request.user, child_id, request.data.get("daily_limit_minutes"), request)
        except rs.ChildLimitError as exc:
            return _err(exc.message, exc.http_status, code=exc.code)
        return _ok("Daily limit saved." if row["my_limit_minutes"] else "Limit removed.", row)
