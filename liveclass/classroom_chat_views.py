# liveclass/classroom_chat_views.py
"""
Tasks 29 & 30 — Classroom <-> chat-group endpoints. Plain `APIView`s (not
ModelViewSet actions on `ClassroomViewSet`) so they get their own explicit
`path()` in urls.py — same "APIView needs its own explicit path()" pattern
this app's urls.py already documents for `dashboard/`, `my-earnings/`,
`my-progress/`, and `notification-preferences/me/`. Kept in their own file
(rather than dropped into the main views.py) purely so this diff is a
clean additive file you can drop in without touching your real
`liveclass/views.py` at all — import + wire the 2 lines in urls.py (see
below) and you're done.

✅ VERIFIED (Task 2 gap-fix pass): `Classroom` and `ClassroomStaff(classroom,
user, role)` field names below are confirmed correct against the real
`liveclass/models.py` — no changes needed there.

✅ RESOLVED (Task 3): the local `_is_classroom_manager()` duplicate has been
removed. This file now imports and uses the real `_can_manage_classroom()`
from `liveclass/views.py` directly, so the two endpoints below share the
exact same teacher/co-teacher/moderator/org-staff logic as the rest of the
app instead of drifting out of sync with it.
"""

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.classroom_chat_bridge import create_classroom_group
from .models import Classroom  # CONFIRMED: liveclass.models.Classroom
from .views import _can_manage_classroom  # the real, single source of truth


class ClassroomCreateGroupView(APIView):
    """POST /liveclass/classrooms/<id>/create_group/ — teacher's explicit
    "haan" confirm. Teacher-only (not co-teacher/moderator — this is a
    one-time classroom-level decision, not routine chat moderation)."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'classroom_group_create'

    def post(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, pk=classroom_id)
        if classroom.teacher_id != request.user.id:
            raise PermissionDenied("Sirf classroom ka teacher hi chat group bana sakta hai.")

        if classroom.chat_group_enabled:
            return Response(
                {'detail': 'Is classroom ke liye chat group already ban chuka hai.'},
                status=400,
            )

        try:
            create_classroom_group(classroom, request.user)
        except ValueError as exc:
            raise ValidationError(str(exc))

        classroom.refresh_from_db()
        return Response(
            {
                'chat_group_enabled': classroom.chat_group_enabled,
                'linked_conversation_id': str(classroom.linked_conversation_id),
            },
            status=201,
        )


class ClassroomGroupStatusView(APIView):
    """GET /liveclass/classrooms/<id>/group/ — linked conversation status
    check. Any classroom manager (teacher/co-teacher/moderator, per
    `_can_manage_classroom`) can read this — students don't need it (they
    get the group via their own `message` app group list once added)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, pk=classroom_id)
        if not _can_manage_classroom(classroom, request.user):
            raise PermissionDenied("Sirf classroom teacher/co-teacher/moderator ye dekh sakte hain.")

        return Response({
            'chat_group_enabled': classroom.chat_group_enabled,
            'linked_conversation_id': (
                str(classroom.linked_conversation_id) if classroom.linked_conversation_id else None
            ),
        })