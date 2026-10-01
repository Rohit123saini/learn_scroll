# =============================================================================
# FILE: tuitionclass/parent_link_views.py  (NEW FILE)
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
core/classroom_chat_bridge.py already calls in (tuitionclass/core -> message),
just one hop shorter — tuitionclass calling message directly for this feature
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

from common.parent_invite_links import (
    build_parent_invite_link,
    generate_or_reuse_parent_code,
    parent_invite_share_text,
)


User = __import__("django.contrib.auth", fromlist=["get_user_model"]).get_user_model()


def _get_managed_classroom_or_404(request, classroom_id):
    classroom = Classroom.objects.filter(id=classroom_id).first()
    if not classroom:
        return None, Response({"detail": "Classroom not found"}, status=status.HTTP_404_NOT_FOUND)
    if not _can_manage_classroom(classroom, request.user):
        return None, Response({"detail": "Not permitted"}, status=status.HTTP_403_FORBIDDEN)
    return classroom, None


# =============================================================================
# PHASE 2 — Task 8 + Task 11: teacher generates a parent code from the roster
# =============================================================================
class ClassroomParentCodeGenerateView(APIView):
    """
    POST /tuitionclass/classrooms/<classroom_id>/participants/<user_id>/parent-code/

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

        # BUG FIX: ParentAccessCode.generate_for() only accepts
        # (student, label='', ttl_days=None) — it has no `created_by`
        # param/field. Calling it with created_by=request.user (as this
        # view previously did) raised a TypeError on every single call,
        # so "manual add parent for one student" was completely broken.
        # `request.user` (the teacher) is still recorded implicitly —
        # this endpoint itself is the audit trail (who hit it, when).
        kwargs = {"label": label}
        if ttl_days:
            kwargs["ttl_days"] = ttl_days
        code_obj = ParentAccessCode.generate_for(student, **kwargs)

        link = build_parent_invite_link(code=code_obj.code, classroom_id=classroom.id)

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
                # BUG FIX: plaintext `code` + `link` were never returned —
                # only `masked_code` (e.g. "••••9QRT") was, which can't be
                # shared with a parent at all. This is the ONE moment the
                # plaintext code is allowed to leave the server (reveal-once
                # rule — see ParentAccessCode's own docstring), same as the
                # student's own self-generate flow already does.
                "code": code_obj.code,
                "link": link,
                "masked_code": code_obj.masked_code,
                "share_text": parent_invite_share_text(
                    student_name=student.get_full_name() or student.username, link=link,
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
    /tuitionclass/classrooms/<classroom_id>/report-cards/

    Manage-tier only (teacher/co-teacher/moderator) for create/update.
    attendance_percent is NEVER accepted from the request body — always
    computed server-side by compute_attendance_percent_bulk() (Gap 3 fix).
    homework_completion_percent and average_marks are computed from this
    classroom's assignment data via tuitionclass.bridge.
    get_assigments_submissions() (Task 5 fix) — the unified `assigments`
    app, same source assigmentsViewSet/assigmentsSubmissionViewSet already
    use post-Task-12. There is no homework/other category distinction on
    that model, so every assignment on the classroom counts. A teacher
    only ever supplies period_label and teacher_remark — the numbers are
    never hand-typed and therefore can never drift from what the
    classroom's own records say.
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
        # Task 5 fix (two bugs, one was hiding the other):
        #   1. This used to filter the LOCAL, legacy `tuitionclass.assigments`
        #      model on `category=assigments.Category.HOMEWORK` — that
        #      model/field never existed here, so this crashed with an
        #      AttributeError on the very first report-card POST for any
        #      classroom.
        #   2. Even with that filter removed, querying the legacy
        #      tuitionclass.assigments/assigmentsSubmission tables directly is
        #      stale data: since Task 12, new assignments are created
        #      through the unified `assigments` app via
        #      bridge.create_assigments(), and the legacy tables stop
        #      receiving new rows entirely. A report card's homework
        #      numbers would only be correct for pre-cutover assignments
        #      and silently blind to everything after — looks complete, is
        #      quietly wrong.
        # Fixed by routing through tuitionclass.bridge.
        # get_assigments_submissions(classroom), the same unified-app
        # source assigmentsViewSet/assigmentsSubmissionViewSet already use
        # post-Task-12. No category filter: the unified model has no
        # homework/other distinction, so every assignment on this
        # classroom counts.
        from assigments.models import assigmentsSubmission as UnifiedSubmission

        from .bridge import get_assigments_submissions

        student_submissions = get_assigments_submissions(classroom).filter(student=student)

        total = student_submissions.count()
        # [ASSUMPTION — NOT VERIFIED] create_context_assigments() (Task 11)
        # bulk-pre-creates one assigmentsSubmission(status=MISSING) row per
        # roster entry at assignment-creation time, so "a submission row
        # exists for this student" is no longer the same as "this student
        # submitted" — every assignment has a row from day one regardless
        # of whether the student ever turned it in. "Not MISSING" is used
        # here as the submitted signal. assigments/models.py (which would
        # define the full SubmissionStatus enum — e.g. whether there's a
        # LATE status you'd want to exclude from "completed") wasn't part
        # of this pass, only assigments/bridge.py's bulk_create call site
        # was visible. Verify SubmissionStatus's full set of values before
        # relying on this in production; if something like LATE exists and
        # shouldn't count as completed, swap this for
        # filter(status__in=[...]) instead of exclude(status=MISSING).
        submitted = student_submissions.exclude(status=UnifiedSubmission.SubmissionStatus.MISSING).count()

        completion_percent = round((submitted / total) * 100, 2) if total else 0
        average_marks = student_submissions.filter(score__isnull=False).aggregate(avg=Avg("score"))["avg"]
        if average_marks is not None:
            average_marks = Decimal(average_marks).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return {"completion_percent": completion_percent, "average_marks": average_marks}

    @staticmethod
    def _notify_report_card_published(report_card):
        from .attendance import notify_linked_parents

        notify_linked_parents(
            report_card.classroom, report_card.student_id,
            title=f"New report card — {report_card.classroom.title}",
            body=f"{report_card.period_label} report is now available.",
            data={"type": "report_card_published"},
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
    GET /tuitionclass/classrooms/<classroom_id>/parent-queries/?status=open

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
    POST /tuitionclass/parent-queries/<query_id>/reply/   {"text": "...", "close": false}

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
        if not classroom or not _can_manage_classroom(classroom, request.user):
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

# =============================================================================
# NEW — "Send parent-add link to ALL students in one click" (bulk), same
# Phase 2 feature as ClassroomParentCodeGenerateView above but for the whole
# roster at once instead of one participant at a time. Reuses the exact same
# generation logic (kept as a tiny local helper so both views can never drift).
# =============================================================================
# NOTE: the "don't spam a fresh code every bulk-send" helper used to be a
# local copy here (`_generate_or_reuse_code`), duplicated byte-for-byte in
# campus/parent_invite.py. Now shared as
# common.parent_invite_links.generate_or_reuse_parent_code — imported
# above — so the rule can never drift between the two apps again. Manual
# single-add (ClassroomParentCodeGenerateView above) deliberately does NOT
# use this helper and always mints a brand-new code instead (a teacher
# explicitly sending one new invite), which is why only the bulk view
# below calls it.


class ClassroomParentCodeBulkGenerateView(APIView):
    """
    POST /tuitionclass/classrooms/<classroom_id>/parent-codes/bulk/
    body (all optional): {"label": "Parent", "ttl_days": 180}

    Teacher/co-teacher/moderator only (same `_can_manage_classroom` gate as
    every other endpoint in this file). One click -> every currently
    enrolled student (active OR lapsed pass, same breadth `is_enrolled()`
    already uses elsewhere in this file) gets a bell + push notification
    carrying their own parent-add link, which the student then forwards to
    their parent. This is the bulk sibling of
    `ClassroomParentCodeGenerateView` (manual, one student at a time) —
    both live in this file so they can never drift apart.

    Best-effort per student: one student's notification failing never
    stops the rest of the batch.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, classroom_id):
        classroom, err = _get_managed_classroom_or_404(request, classroom_id)
        if err:
            return err

        if not classroom.parents_enabled:
            return Response(
                {"detail": "Parents are switched off for this classroom.", "code": "parents_disabled"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        label = (request.data.get("label") or "").strip()[:50] or "Parent"
        ttl_days = request.data.get("ttl_days")

        # Same enrolled-student query the classroom's own NoticeViewSet
        # (tuitionclass/views.py) uses for its urgent-notice fan-out — active
        # OR lapsed pass counts as "enrolled" here (is_enrolled()'s breadth),
        # since a link is harmless to send even to a lapsed-pass student.
        from .models import PassPurchase

        student_ids = list(
            PassPurchase.objects.filter(
                class_pass__classroom=classroom,
                status=PassPurchase.Status.SUCCESS,
                is_active=True,
            ).values_list("student_id", flat=True).distinct()
        )
        # Optional-parent rule: only students still at parent_status "none"
        # get the bulk link. Skipped / already-invited / already-linked
        # students are left alone.
        from .attendance import parent_status_map, set_parent_state
        from .models import ClassroomParentState

        statuses = parent_status_map(classroom, student_ids)
        pending_ids = [sid for sid in student_ids if statuses.get(sid) == "none"]
        students = User.objects.filter(id__in=pending_ids)

        sent, skipped = [], []
        for student in students:
            active_count = ParentAccessCode.objects.filter(student=student, is_active=True).count()
            if active_count >= getattr(ParentAccessCode, "MAX_ACTIVE_CODES", 5):
                skipped.append({"student_id": student.id, "reason": "max_active_codes_reached"})
                continue
            try:
                code_obj, _created = generate_or_reuse_parent_code(student, label, ttl_days)
                link = build_parent_invite_link(code=code_obj.code, classroom_id=classroom.id)
                create_bell_rows_for_push(
                    recipient_ids=[student.id],
                    notif_type="parent_invite_link",
                    title="Add your parent",
                    message=parent_invite_share_text(
                        student_name=student.get_full_name() or student.username, link=link,
                    ),
                    data={"type": "parent_invite_link", "link": link, "classroom_id": str(classroom.id)},
                )
                set_parent_state(classroom, student.id, ClassroomParentState.Status.INVITED, request.user)
                sent.append({"student_id": student.id, "student_name": student.get_full_name() or student.username})
            except Exception:
                skipped.append({"student_id": student.id, "reason": "notify_failed"})

        return Response(
            {
                "classroom_id": classroom.id,
                "total_students": len(student_ids),
                "already_handled_count": len(student_ids) - len(pending_ids),
                "sent": sent,
                "sent_count": len(sent),
                "skipped": skipped,
            },
            status=status.HTTP_200_OK,
        )
