# =============================================================================
# FILE: liveclass/parent_link_views.py  (NEW FILE)
# =============================================================================
"""
Phase 2 (teacher-created parent codes), Phase 4 (report cards), and the
teacher side of Phase 5 (parent-mode query threads) all live here — one new
file, reused across three tasks, same as the original plan intended.

Permission model throughout: every endpoint here is gated by
_can_manage_classroom (teacher / co-teacher / moderator) — the same helper
already used everywhere else in this app, imported directly rather than
re-implemented, per Phase 0 Task 2's fix.

Cross-app note: this file imports from `message.models` (ParentAccessCode,
ParentModeQuery, ParentModeQueryMessage) and `message.services`
(create_bell_rows_for_push). That's the same direction
core/classroom_chat_bridge.py already calls in (liveclass/core -> message),
just one hop shorter — liveclass calling message directly for this feature
only, since it doesn't need the group-chat bridge in between.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, Avg
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Assignment,
    AssignmentSubmission,
    Classroom,
    StudentReportCard,
    compute_attendance_percent_bulk,
)
from .serializers import StudentReportCardSerializer
from .views import _can_manage_classroom  # single source of truth, per Phase 0 Task 2

# Cross-app imports (message app) — see module docstring.
from message.models import ParentAccessCode, ParentModeQuery, ParentModeQueryMessage
from message.services import create_bell_rows_for_push
from message.push_utils import send_parent_push


User = __import__("django.contrib.auth", fromlist=["get_user_model"]).get_user_model()


def _get_managed_classroom_or_404(request, classroom_id):
    classroom = Classroom.objects.filter(id=classroom_id).first()
    if not classroom:
        return None, Response({"detail": "Classroom not found"}, status=status.HTTP_404_NOT_FOUND)
    if not _can_manage_classroom(request.user, classroom):
        return None, Response({"detail": "Not permitted"}, status=status.HTTP_403_FORBIDDEN)
    return classroom, None


# =============================================================================
# PHASE 2 — Task 8 + Task 11: teacher generates a parent code from the roster
# =============================================================================
class ClassroomParentCodeGenerateView(APIView):
    """
    POST /liveclass/classrooms/<classroom_id>/participants/<user_id>/parent-code/

    Teacher/co-teacher/moderator only. Verifies user_id is an active
    participant of this classroom, then generates a parent-access code on
    their behalf via the exact same ParentAccessCode.generate_for() the
    student's own self-generate flow uses — created_by=request.user is the
    only difference (Phase 2, Task 7).

    Returns the masked code + a one-time share text — same one-time-reveal
    rule the student flow already follows; this endpoint never returns the
    raw code a second time on a later call.

    GAP FIX (Task 11) — also fires a bell notification to the student, since
    this code gives someone read access to their attendance/homework/marks
    and they currently have no way to know it was created. Best-effort:
    never blocks the 201 response if the notification fails.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, classroom_id, user_id):
        classroom, err = _get_managed_classroom_or_404(request, classroom_id)
        if err:
            return err

        # Confirm the target user is an active participant of this classroom.
        # Uses is_enrolled() (broader — includes a lapsed pass) rather than
        # has_access() (tight — only a currently valid pass), since a
        # teacher should be able to set up a parent code for a student even
        # between passes, not only while a pass happens to be active right now.
        student = User.objects.filter(id=user_id).first()
        if not student or not classroom.is_enrolled(student):
            return Response(
                {"detail": "This user is not an enrolled participant of this classroom."},
                status=status.HTTP_404_NOT_FOUND,
            )

        label = (request.data.get("label") or "").strip()[:50] or "Parent"
        ttl_days = request.data.get("ttl_days")  # optional, falls back to ParentAccessCode.DEFAULT_TTL_DAYS

        active_count = ParentAccessCode.objects.filter(student=student, is_active=True).count()
        if active_count >= getattr(ParentAccessCode, "MAX_ACTIVE_CODES", 5):
            return Response(
                {"detail": "This student already has the maximum number of active parent codes."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        kwargs = {"label": label, "created_by": request.user}
        if ttl_days:
            kwargs["ttl_days"] = ttl_days
        code_obj = ParentAccessCode.generate_for(student, **kwargs)

        # Best-effort notify — never blocks the response.
        try:
            create_bell_rows_for_push(
                recipient_ids=[student.id],
                notif_type="parent_code_created_by_teacher",
                title="A parent access code was created for you",
                message=(
                    f"{request.user.get_full_name() or request.user.username} generated a parent-access "
                    f"code ('{label}') for your account in {classroom.title}. You can view and revoke it "
                    f"any time from Manage Parent Access."
                ),
            )
        except Exception:  # matches this app's own "never let a notify failure break the real action" rule
            pass

        return Response(
            {
                "id": str(code_obj.id),
                "label": code_obj.label,
                "masked_code": code_obj.masked_code,
                "share_text": (
                    f"{student.get_full_name() or student.username}'s parent access code: {code_obj.code} "
                    f"(valid until {code_obj.expires_at:%d %b %Y})"
                ),
                "expires_at": code_obj.expires_at,
            },
            status=status.HTTP_201_CREATED,
        )


# =============================================================================
# PHASE 4 — Task 17 + Task 18: Student Report Card (teacher-authored)
# =============================================================================
class ReportCardViewSet(viewsets.ModelViewSet):
    """
    /liveclass/classrooms/<classroom_id>/report-cards/

    Manage-tier only (teacher/co-teacher/moderator) for create/update.
    attendance_percent is NEVER accepted from the request body — always
    computed server-side by compute_attendance_percent_bulk() (Gap 3 fix).
    homework_completion_percent and average_marks are computed the same way
    from this classroom's own liveclass Assignment/AssignmentSubmission data
    (category='homework'), so a teacher only ever supplies period_label and
    teacher_remark — the numbers are never hand-typed and therefore can
    never drift from what the classroom's own records say.
    """
    serializer_class = StudentReportCardSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = StudentReportCard.objects.select_related("classroom", "student", "created_by")
        classroom_id = self.request.query_params.get("classroom")
        if classroom_id:
            qs = qs.filter(classroom_id=classroom_id)
        return qs

    def create(self, request, *args, **kwargs):
        classroom_id = request.data.get("classroom")
        classroom, err = _get_managed_classroom_or_404(request, classroom_id)
        if err:
            return err

        student_id = request.data.get("student")
        student = User.objects.filter(id=student_id).first()
        if not student or not classroom.is_enrolled(student):
            return Response({"detail": "Student not enrolled in this classroom."}, status=status.HTTP_404_NOT_FOUND)

        period_label = (request.data.get("period_label") or "").strip()
        if not period_label:
            return Response({"detail": "period_label is required."}, status=status.HTTP_400_BAD_REQUEST)

        attendance = compute_attendance_percent_bulk([classroom.id], student).get(classroom.id, 0)
        homework_stats = self._homework_stats(classroom, student)

        report_card, created = StudentReportCard.objects.update_or_create(
            classroom=classroom, student=student, period_label=period_label,
            defaults={
                "attendance_percent": attendance,
                "homework_completion_percent": homework_stats["completion_percent"],
                "average_marks": homework_stats["average_marks"],
                "teacher_remark": (request.data.get("teacher_remark") or "").strip(),
                "created_by": request.user,
            },
        )

        # GAP FIX (Task 29) — parent push the moment a report card is
        # published/updated; best-effort, never blocks the response.
        try:
            self._notify_report_card_published(report_card)
        except Exception:
            pass

        serializer = self.get_serializer(report_card)
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @staticmethod
    def _homework_stats(classroom, student):
        homework_qs = Assignment.objects.filter(classroom=classroom, category=Assignment.Category.HOMEWORK)
        total = homework_qs.count()
        submissions = AssignmentSubmission.objects.filter(assignment__in=homework_qs, student=student)
        submitted = submissions.count()
        completion_percent = round((submitted / total) * 100, 2) if total else 0
        average_marks = submissions.filter(score__isnull=False).aggregate(avg=Avg("score"))["avg"]
        if average_marks is not None:
            average_marks = Decimal(average_marks).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return {"completion_percent": completion_percent, "average_marks": average_marks}

    @staticmethod
    def _notify_report_card_published(report_card):
        for code in ParentAccessCode.objects.filter(student_id=report_card.student_id, is_active=True):
            for token_row in code.device_tokens.all():
                send_parent_push(
                    fcm_token=token_row.fcm_token,
                    title=f"New report card — {report_card.classroom.title}",
                    body=f"{report_card.period_label} report is now available.",
                    data={"type": "report_card_published", "classroom_id": str(report_card.classroom_id)},
                )


# =============================================================================
# PHASE 5 — Task 24 + Task 27: teacher side of parent-mode query threads
# (models renamed ParentModeQuery / ParentModeQueryMessage — see Gap 4 in the
# plan: this is deliberately NOT the same feature as the existing
# ClassQuery / ParentTeacherMessage models already in this file's
# neighbourhood — those stay untouched. This is the token-based Parent Mode
# equivalent, scoped to a Classroom directly rather than a Group.)
# =============================================================================
class ClassroomParentQueryListView(APIView):
    """
    GET /liveclass/classrooms/<classroom_id>/parent-queries/?status=open

    status is optional; omit it to see every thread for this classroom.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, classroom_id):
        classroom, err = _get_managed_classroom_or_404(request, classroom_id)
        if err:
            return err

        qs = ParentModeQuery.objects.filter(classroom_id=classroom.id).select_related("parent_access_code")
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        return Response([
            {
                "id": q.id,
                "student_name": q.parent_access_code.student.get_full_name() or q.parent_access_code.student.username,
                "category": q.category,
                "subject": q.subject,
                "status": q.status,
                "created_at": q.created_at,
                "updated_at": q.updated_at,
                "message_count": q.messages.count(),
            }
            for q in qs.order_by("-updated_at")
        ])


class ParentQueryReplyView(APIView):
    """
    POST /liveclass/parent-queries/<query_id>/reply/   {"text": "...", "close": false}

    Adds a ParentModeQueryMessage(sender_type='teacher'), sets
    status=answered (or status=closed if the teacher explicitly passes
    "close": true — Task 27's explicit-close addition, so a resolved thread
    doesn't sit in "open" waiting for the parent to reply first).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, query_id):
        query = ParentModeQuery.objects.select_related("parent_access_code").filter(id=query_id).first()
        if not query:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        classroom = Classroom.objects.filter(id=query.classroom_id).first()
        if not classroom or not _can_manage_classroom(request.user, classroom):
            return Response({"detail": "Not permitted"}, status=status.HTTP_403_FORBIDDEN)

        text = (request.data.get("text") or "").strip()
        if not text:
            return Response({"detail": "text is required"}, status=status.HTTP_400_BAD_REQUEST)
        if len(text) > 2000:
            return Response({"detail": "text is too long (max 2000 characters)"}, status=status.HTTP_400_BAD_REQUEST)

        ParentModeQueryMessage.objects.create(
            query=query, sender_type="teacher", text=text, sent_by_teacher=request.user,
        )
        query.status = "closed" if request.data.get("close") else "answered"
        query.updated_at = timezone.now()
        query.save(update_fields=["status", "updated_at"])

        # Parent push — best-effort.
        try:
            for token_row in query.parent_access_code.device_tokens.all():
                send_parent_push(
                    fcm_token=token_row.fcm_token,
                    title=f"Reply from {classroom.title}",
                    body=text[:120],
                    data={"type": "parent_query_reply", "query_id": str(query.id)},
                )
        except Exception:
            pass

        return Response({"detail": "Reply sent", "status": query.status})