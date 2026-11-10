# campus/views/doubts.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from ..models import (
    CampusDoubt,
    CampusDoubtReply,
    ClassTeacherassigments,
    Section,
    StaffProfile,
    StudentEnrollment,
    SubjectTeacherassigments,
)
from ..serializers import (
    CampusDoubtDetailSerializer,
    CampusDoubtReplySerializer,
    CampusDoubtSerializer,
)
from ..throttles import CampusDoubtPostThrottle
from django.db.models import (
    Count,
    Prefetch,
    Q,
)
from rest_framework.permissions import IsAuthenticated
from ..bridge import NotifTypes
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.decorators import action
from .. import bridge
from ..permissions import can_manage_section_subject
from ..visibility import get_visibility
from django.utils import timezone
from django.db import transaction
from rest_framework import viewsets
from ._common import logger


# ============================================================
# T4 §F — doubts
# ============================================================
class CampusDoubtViewSet(viewsets.ModelViewSet):
    """
    Doubts inside one subject-class (decision D4 — private by default).

    Who sees a doubt: its author; a linked parent (child's doubts); that
    subject's APPROVED teacher(s) and the section's class-teacher;
    campus admin/principal; and — only when the campus enabled
    `doubts_public_allowed` AND the doubt is `is_public` — the other
    students of that section (read-only). Everyone else gets 404.

    Only an enrolled student of the section can POST a doubt; only the
    author or that class's staff can reply. A staff reply flips the doubt
    to `answered`, the author's follow-up flips it back to `open`.
    Delete = soft delete (`is_active=False`).
    """

    serializer_class = CampusDoubtSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return CampusDoubtDetailSerializer
        return CampusDoubtSerializer

    def get_throttles(self):
        if self.action in ("create", "reply"):
            return [CampusDoubtPostThrottle()]
        return super().get_throttles()

    # ---- helpers
    def _is_class_staff(self, doubt):
        return can_manage_section_subject(
            self.request.user, doubt.campus_id, doubt.section_id, doubt.subject_id
        )

    def get_queryset(self):
        from django.db.models import Exists, OuterRef

        user = self.request.user
        vis = get_visibility(user, self.request)
        qs = (
            CampusDoubt.objects.filter(is_active=True, campus_id__in=vis.member_campus_ids)
            .select_related("author", "subject", "campus")
            .annotate(
                replies_count=Count("replies", filter=Q(replies__is_active=True), distinct=True),
                _teaches=Exists(
                    SubjectTeacherassigments.objects.filter(
                        section_id=OuterRef("section_id"), subject_id=OuterRef("subject_id"),
                        staff__user=user, staff__is_active=True,
                        status=SubjectTeacherassigments.Status.APPROVED,
                    )
                ),
            )
        )
        admin_campuses = StaffProfile.objects.filter(
            user=user, is_active=True,
            role__in=[StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD],
        ).values_list("campus_id", flat=True)
        class_teacher_sections = ClassTeacherassigments.objects.filter(
            staff__user=user, staff__is_active=True
        ).values_list("section_id", flat=True)
        q = (
            Q(author=user)
            | Q(author_id__in=vis.visible_student_ids, campus_id__in=vis.nonstaff_campus_ids)
            | Q(campus_id__in=admin_campuses)
            | Q(section_id__in=class_teacher_sections)
            | Q(_teaches=True)
            | Q(is_public=True, campus__doubts_public_allowed=True, section_id__in=vis.visible_section_ids)
        )
        qs = qs.filter(q)
        # Block: another student's doubt/answer is hidden from me when we block each other
        # (my own doubts, and teacher/admin visibility, are never affected).
        from user_profile.services import blocked_user_ids

        _hidden = blocked_user_ids(user)
        if _hidden:
            qs = qs.exclude(Q(author_id__in=_hidden) & ~Q(author=user) & Q(_teaches=False))
        p = self.request.query_params
        for key in ("section", "subject", "status", "campus"):
            if p.get(key):
                qs = qs.filter(**{f"{key}_id" if key != "status" else "status": p[key]})
        if p.get("mine") in ("1", "true", "True"):
            qs = qs.filter(author=user)
        if self.action == "retrieve":
            qs = qs.prefetch_related(Prefetch("replies", queryset=CampusDoubtReply.objects.select_related("author")))
        return qs

    # ---- create
    @transaction.atomic
    def perform_create(self, serializer):
        user = self.request.user
        section = serializer.validated_data["section"]
        subject = serializer.validated_data["subject"]
        campus = section.school_class.campus
        if not campus.doubts_enabled:
            raise PermissionDenied("Doubts are turned off for this campus.")
        vis = get_visibility(user, self.request)
        enrollment = StudentEnrollment.objects.filter(
            student=user, section=section, status=StudentEnrollment.Status.ACTIVE,
            section_id__in=vis.own_section_ids,
        ).select_related("session").first()
        if not enrollment:
            raise PermissionDenied("Only a student enrolled in this section can post a doubt.")
        teacher_staff = list(
            SubjectTeacherassigments.objects.filter(
                section=section, subject=subject, status=SubjectTeacherassigments.Status.APPROVED
            ).select_related("staff__user")
        )
        if not teacher_staff:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"subject": "This subject has no approved teacher in your section."})
        is_public = bool(serializer.validated_data.get("is_public"))
        if is_public and not campus.doubts_public_allowed:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"is_public": "Public doubts are not enabled for this campus."})
        doubt = serializer.save(author=user, campus=campus, session=enrollment.session)
        recipients = {a.staff.user_id for a in teacher_staff if a.staff.is_active}
        recipients.discard(user.id)
        bridge.notify(
            users=list(recipients),
            notif_type=NotifTypes.CAMPUS_DOUBT_POSTED,
            title=f"New doubt: {subject.name}",
            body=(doubt.text[:120]),
            data={"type": NotifTypes.CAMPUS_DOUBT_POSTED, "doubt_id": str(doubt.id),
                  "section_id": str(section.id), "subject_id": str(subject.id)},
        )

    # ---- edit / delete (author, while still unanswered)
    def partial_update(self, request, *args, **kwargs):
        doubt = self.get_object()
        if doubt.author_id != request.user.id:
            raise PermissionDenied("Only the author can edit a doubt.")
        if doubt.status != CampusDoubt.Status.OPEN or doubt.replies.filter(is_active=True).exists():
            return Response({"detail": "A doubt can't be edited once it has a reply."}, status=400)
        ser = self.get_serializer(doubt, data={k: v for k, v in request.data.items() if k in ("text", "attachment", "is_public")}, partial=True)
        ser.is_valid(raise_exception=True)
        if ser.validated_data.get("is_public") and not doubt.campus.doubts_public_allowed:
            return Response({"is_public": "Public doubts are not enabled for this campus."}, status=400)
        ser.save()
        return Response(ser.data)

    def perform_destroy(self, instance):
        if instance.author_id != self.request.user.id and not self._is_class_staff(instance):
            raise PermissionDenied("Only the author or that class's teacher can delete a doubt.")
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])

    # ---- where can I ask? (teacher + class group)
    @action(detail=False, methods=["get"], url_path="targets")
    def targets(self, request):
        """`GET /doubts/targets/?section=<id>[&subject=<id>]` — everything the Doubts screen
        needs to offer BOTH ways of asking for one subject-class:

          * the TEACHER (a private campus doubt: `POST /doubts/`) — `can_ask_teacher`,
            `teachers` (the approved teacher(s) of the subject, or of every subject when
            no `subject` is given);
          * the CLASS GROUP (the section's chat group; its doubt queue goes through the
            doubt hub: `POST /doubt-hub/ask/` with `source=classroom_chat` and
            `target_id=group_id`, the list with `source=classroom_chat&parent_id=group_id`)
            — `group_id` is null when the section has no working group yet.

        `can_answer` is true for the subject's teacher / the class teacher / campus
        admin (with `subject`). Any member of the section's campus may call it; a
        section the caller can't see is a 404."""
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        section_id = request.query_params.get("section")
        if not section_id:
            raise ValidationError({"section": "A section is required."})
        try:
            section = Section.objects.select_related("school_class__campus").filter(pk=section_id).first()
        except (ValueError, TypeError, DjangoValidationError):
            raise ValidationError({"section": "Invalid id."})
        if section is None:
            return Response({"detail": "Section not found."}, status=404)
        campus = section.school_class.campus
        vis = get_visibility(request.user, request)
        if campus.id not in vis.staff_campus_ids and section.id not in vis.visible_section_ids:
            return Response({"detail": "Section not found."}, status=404)

        subject_id = request.query_params.get("subject") or None
        approved = SubjectTeacherassigments.objects.filter(
            section=section, status=SubjectTeacherassigments.Status.APPROVED, staff__is_active=True
        ).select_related("staff__user", "subject")
        if subject_id:
            try:
                approved = approved.filter(subject_id=subject_id)
            except (ValueError, TypeError, DjangoValidationError):
                raise ValidationError({"subject": "Invalid id."})
        teachers = []
        for row in approved.order_by("subject__name")[:100]:
            user = row.staff.user
            teachers.append({
                "subject": str(row.subject_id),
                "subject_name": row.subject.name,
                "user_id": user.id,
                "name": (f"{user.first_name} {user.last_name}").strip() or user.username,
            })

        is_student = StudentEnrollment.objects.filter(
            student=request.user, section=section, status=StudentEnrollment.Status.ACTIVE
        ).exists()
        try:
            group_id = bridge.get_section_group_id(section)
        except Exception:  # a missing / broken group must never break the screen
            logger.exception("Could not resolve the chat group of section %s.", section.pk)
            group_id = None
        can_answer = bool(
            subject_id and can_manage_section_subject(request.user, campus.id, section.id, subject_id)
        )
        return Response({
            "section": str(section.id),
            "campus": str(campus.id),
            "subject": str(subject_id) if subject_id else None,
            "doubts_enabled": campus.doubts_enabled,
            "doubts_public_allowed": campus.doubts_public_allowed,
            "can_ask_teacher": bool(is_student and campus.doubts_enabled and teachers),
            "teachers": teachers,
            "group_id": group_id,
            "can_ask_group": bool(is_student and group_id),
            "can_answer": can_answer,
        })

    # ---- actions
    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        doubt = self.get_object()
        if not doubt.campus.doubts_enabled:
            raise PermissionDenied("Doubts are turned off for this campus.")
        is_author = doubt.author_id == request.user.id
        is_staff = self._is_class_staff(doubt)
        if not (is_author or is_staff):
            raise PermissionDenied("Only the author or that class's teacher can reply.")
        ser = CampusDoubtReplySerializer(data=request.data, context=self.get_serializer_context())
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            reply = ser.save(doubt=doubt, author=request.user, is_staff_reply=is_staff and not is_author)
            if reply.is_staff_reply and doubt.status == CampusDoubt.Status.OPEN:
                doubt.status = CampusDoubt.Status.ANSWERED
            elif is_author and doubt.status in (CampusDoubt.Status.ANSWERED, CampusDoubt.Status.RESOLVED):
                doubt.status = CampusDoubt.Status.OPEN
                doubt.resolved_by, doubt.resolved_at = None, None
            doubt.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])
        if reply.is_staff_reply:
            recipients = [doubt.author_id]
        else:
            recipients = list(
                SubjectTeacherassigments.objects.filter(
                    section_id=doubt.section_id, subject_id=doubt.subject_id,
                    status=SubjectTeacherassigments.Status.APPROVED, staff__is_active=True,
                ).values_list("staff__user_id", flat=True)
            )
        recipients = [u for u in set(recipients) if u != request.user.id]
        bridge.notify(
            users=recipients,
            notif_type=NotifTypes.CAMPUS_DOUBT_REPLIED,
            title=f"Reply on your doubt: {doubt.subject.name}" if reply.is_staff_reply else f"Student replied: {doubt.subject.name}",
            body=reply.text[:120],
            data={"type": NotifTypes.CAMPUS_DOUBT_REPLIED, "doubt_id": str(doubt.id)},
        )
        return Response(CampusDoubtReplySerializer(reply, context=self.get_serializer_context()).data, status=201)

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        doubt = self.get_object()
        if doubt.author_id != request.user.id and not self._is_class_staff(doubt):
            raise PermissionDenied("Only the author or that class's teacher can resolve a doubt.")
        doubt.status = CampusDoubt.Status.RESOLVED
        doubt.resolved_by, doubt.resolved_at = request.user, timezone.now()
        doubt.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])
        return Response(CampusDoubtSerializer(doubt, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        doubt = self.get_object()
        if doubt.author_id != request.user.id and not self._is_class_staff(doubt):
            raise PermissionDenied("Only the author or that class's teacher can reopen a doubt.")
        doubt.status = CampusDoubt.Status.OPEN
        doubt.resolved_by, doubt.resolved_at = None, None
        doubt.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])
        return Response(CampusDoubtSerializer(doubt, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], url_path="set-public")
    def set_public(self, request, pk=None):
        doubt = self.get_object()
        if doubt.author_id != request.user.id and not self._is_class_staff(doubt):
            raise PermissionDenied("Only the author or that class's teacher can change this.")
        if not doubt.campus.doubts_public_allowed:
            return Response({"detail": "Public doubts are not enabled for this campus."}, status=400)
        doubt.is_public = bool(request.data.get("is_public", True))
        doubt.save(update_fields=["is_public", "updated_at"])
        return Response(CampusDoubtSerializer(doubt, context=self.get_serializer_context()).data)
