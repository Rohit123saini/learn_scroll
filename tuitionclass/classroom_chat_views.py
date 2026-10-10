# tuitionclass/classroom_chat_views.py
"""
Tasks 29 & 30 — Classroom <-> chat-group endpoints. Plain `APIView`s (not
ModelViewSet actions on `ClassroomViewSet`) so they get their own explicit
`path()` in urls.py — same "APIView needs its own explicit path()" pattern
this app's urls.py already documents for `dashboard/`, `my-earnings/`,
`my-progress/`, and `notification-preferences/me/`. Kept in their own file
(rather than dropped into the main views.py) purely so this diff is a
clean additive file you can drop in without touching your real
`tuitionclass/views.py` at all — import + wire the 2 lines in urls.py (see
below) and you're done.

✅ VERIFIED (Task 2 gap-fix pass): `Classroom` and `ClassroomStaff(classroom,
user, role)` field names below are confirmed correct against the real
`tuitionclass/models.py` — no changes needed there.

✅ RESOLVED (Task 3): the local `_is_classroom_manager()` duplicate has been
removed. This file now imports and uses the real `_can_manage_classroom()`
from `tuitionclass/views.py` directly, so the two endpoints below share the
exact same teacher/co-teacher/moderator/org-staff logic as the rest of the
app instead of drifting out of sync with it.
"""

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.classroom_chat_bridge import (
    create_classroom_group, ensure_classroom_group, group_status, reconcile_classroom_group, set_group_enabled,
    student_has_active_access, _get_group_for_classroom,
)
from .models import Classroom  # CONFIRMED: tuitionclass.models.Classroom
from .views import _can_manage_classroom  # the real, single source of truth


class ClassroomCreateGroupView(APIView):
    """POST /tuitionclass/classrooms/<id>/create_group/ — teacher's explicit
    "haan" confirm. Teacher-only (not co-teacher/moderator — this is a
    one-time classroom-level decision, not routine chat moderation)."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'classroom_group_create'

    def post(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, pk=classroom_id)
        if classroom.teacher_id != request.user.id:
            raise PermissionDenied("Sirf classroom ka teacher hi chat group bana sakta hai.")

        # T3: `chat_group_enabled` is now True by default, so it no longer
        # means "group exists" — check the real linked group instead.
        if _get_group_for_classroom(classroom) is not None:
            return Response(
                {'detail': 'Is classroom ke liye chat group already ban chuka hai.'},
                status=400,
            )

        try:
            if not classroom.chat_group_enabled:
                set_group_enabled(classroom, True)  # also restores an archived group
            else:
                ensure_classroom_group(classroom)
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
    """GET /tuitionclass/classrooms/<id>/group/ — linked conversation status
    check. Any classroom manager (teacher/co-teacher/moderator, per
    `_can_manage_classroom`) can read this — students don't need it (they
    get the group via their own `message` app group list once added)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, pk=classroom_id)
        if not _can_manage_classroom(classroom, request.user):
            raise PermissionDenied("Sirf classroom teacher/co-teacher/moderator ye dekh sakte hain.")

        return Response(group_status(classroom))


class ClassroomGroupOpenView(APIView):
    """GET /tuitionclass/classrooms/<id>/group/open/ — for a STUDENT (or any
    participant): the conversation id to open ("Open group" button). Returns
    `linked_conversation_id: null` when the group isn't ready or the caller is
    not a participant — never leaks the id to outsiders."""
    permission_classes = [IsAuthenticated]

    def get(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, pk=classroom_id)
        group = _get_group_for_classroom(classroom)
        allowed = group is not None and (
            _can_manage_classroom(classroom, request.user)
            or student_has_active_access(classroom, request.user.id)
        )
        return Response({
            'chat_group_enabled': classroom.chat_group_enabled,
            'group_ready': group is not None,
            'linked_conversation_id': str(group.conversation_id) if allowed else None,
        })


class ClassroomGroupRetryView(APIView):
    """POST /tuitionclass/classrooms/<id>/group/retry/ — "group nahi bana?"
    retry button. Teacher/co-teacher/moderator. Idempotent: creates the group
    when missing, then reconciles members to the class participants."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'classroom_group_create'

    def post(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, pk=classroom_id)
        if not _can_manage_classroom(classroom, request.user):
            raise PermissionDenied("Sirf classroom teacher/co-teacher/moderator ye kar sakte hain.")
        if not classroom.chat_group_enabled:
            raise ValidationError("Chat group band hai — pehle use on karo.")
        try:
            ensure_classroom_group(classroom)
            report = reconcile_classroom_group(classroom)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("group retry failed for classroom %s", classroom.pk)
            return Response({'detail': 'Group abhi nahi ban paya, thodi der baad dobara try karo.'}, status=503)
        return Response({**group_status(classroom), 'added': len(report['added']), 'removed': len(report['removed'])})


class ClassroomGroupToggleView(APIView):
    """POST /tuitionclass/classrooms/<id>/group/toggle/ {"enabled": bool} —
    teacher only. Off = group ARCHIVED (history kept); on = restored."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'classroom_group_create'

    def post(self, request, classroom_id):
        classroom = get_object_or_404(Classroom, pk=classroom_id)
        if classroom.teacher_id != request.user.id:
            raise PermissionDenied("Sirf classroom ka teacher chat group on/off kar sakta hai.")
        enabled = request.data.get('enabled')
        if not isinstance(enabled, bool):
            raise ValidationError({'enabled': 'true ya false bhejo.'})
        set_group_enabled(classroom, enabled)
        classroom.refresh_from_db()
        return Response(group_status(classroom))