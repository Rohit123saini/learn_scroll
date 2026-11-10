# campus/homework_diary.py
"""
HOMEWORK DIARY API — `/campus/homework-diary/`

"Homework roz kaise de": a teacher posts one diary line per
`(section, subject)` per day — title/details, a due date and an optional
attachment (worksheet photo / PDF). Students of that section and their linked
parents read it class-wise; the section's staff get a "which subjects still
have no homework today?" helper.

    GET    homework-diary/                    list (scoped, see below)
    POST   homework-diary/                    create   (multipart or JSON)
    GET    homework-diary/{id}/               retrieve
    PATCH  homework-diary/{id}/               edit     (multipart or JSON)
    DELETE homework-diary/{id}/               soft delete (is_active=False)
    GET    homework-diary/day/?section=&date= one day, class-wise + `missing_subjects`

List filters: ?section= ?subject= ?campus= ?student= (a child's / own section)
?date=YYYY-MM-DD (diary day) ?date_from= ?date_to= ?due=YYYY-MM-DD
?upcoming=1 (due today or later). Use ?page_size=100 for bounded ranges.

Who sees what (same rules as every section-scoped campus viewset):
  * ACTIVE staff of the campus: every section's diary.
  * Student: only their own current section(s). Parent: the linked child's.
Who may write: `can_manage_section_subject` — admin/principal anywhere, the
section's class-teacher for any subject, an APPROVED subject-teacher for
their own subject only (a moderator is section-wide). Students/parents can
never write (403).

Push: creating an entry fans a push out to the section's students + parents
(Celery, after commit — `tasks.send_homework_diary_posted`); a daily beat task
sends the "due tomorrow" reminder (`tasks.send_homework_diary_due_reminders`).

Deliberately separate from `campus.assigmentss/` (the unified `assignments`
app): a diary line has no submissions, grading or plagiarism machinery.
"""
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.async_utils import dispatch_after_commit

from . import tasks as campus_tasks
from .audit import log_action
from .models import (
    HomeworkDiaryEntry,
    Section,
    StudentEnrollment,
    SubjectTeacherassigments,
)
from .permissions import (
    IsSectionSubjectStaffOrReadOnly,
    can_manage_section_subject,
    is_campus_admin_or_principal,
    is_class_teacher_of_section,
)
from .serializers import HomeworkDiaryEntrySerializer
from .throttles import CampusHomeworkDiaryPostThrottle
from .views import CampusMemberScopedMixin
from .visibility import get_visibility

logger = logging.getLogger(__name__)


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _section_or_none(section_id):
    """`Section` for a client-supplied id, `None` for a missing/garbage id
    (a malformed UUID must be a clean 403/400, never a 500)."""
    if not section_id:
        return None
    try:
        return Section.objects.select_related("school_class").filter(pk=section_id).first()
    except (ValueError, TypeError, DjangoValidationError):
        return None


class HomeworkDiaryViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = HomeworkDiaryEntrySerializer
    campus_field_path = "campus"
    member_scope = "section"
    member_section_path = "section"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    # No PUT: a partial edit is the only edit (section is immutable anyway).
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    # ---- throttling ------------------------------------------------------
    def get_throttles(self):
        if self.action == "create":
            return [CampusHomeworkDiaryPostThrottle()]
        return super().get_throttles()

    # ---- permission input --------------------------------------------------
    def get_section_subject_for_permission_check(self, request):
        """Edit/delete are judged by the STORED row (never by ids in the body,
        which a client could point at a section it does manage). Create is
        judged by the posted `(section, subject)`."""
        pk = self.kwargs.get("pk")
        if pk:
            try:
                obj = self.get_queryset().filter(pk=pk).first()
            except (ValueError, TypeError, DjangoValidationError):
                obj = None
            if obj:
                return obj.campus_id, obj.section_id, obj.subject_id
            return None, None, None
        section = _section_or_none(request.data.get("section"))
        if section is None:
            return None, None, None
        return section.school_class.campus_id, section.id, request.data.get("subject") or None

    # ---- queryset ----------------------------------------------------------
    def _date_param(self, name):
        raw = self.request.query_params.get(name)
        if not raw:
            return None
        try:
            value = parse_date(raw)
        except ValueError:
            value = None
        if value is None:
            raise ValidationError({name: "Use the format YYYY-MM-DD."})
        return value

    def get_queryset(self):
        qs = HomeworkDiaryEntry.objects.filter(is_active=True).select_related(
            "section__school_class", "subject", "posted_by"
        )
        qs = self.filter_queryset_to_my_campuses(qs, self.request)
        params = self.request.query_params

        for key in ("section", "subject"):
            if params.get(key):
                try:
                    qs = qs.filter(**{f"{key}_id": params[key]})
                except (ValueError, TypeError, DjangoValidationError):
                    raise ValidationError({key: "Invalid id."})

        student_id = params.get("student")
        if student_id:
            # A parent asks for "my child's diary" by child id; a student can
            # pass their own. Anything outside what the caller may see -> empty.
            vis = get_visibility(self.request.user, self.request)
            enrollments = StudentEnrollment.objects.filter(
                student_id=student_id, status=StudentEnrollment.Status.ACTIVE
            )
            if str(student_id) not in {str(pk) for pk in vis.visible_student_ids}:
                enrollments = enrollments.filter(section__school_class__campus_id__in=vis.staff_campus_ids)
            qs = qs.filter(section_id__in=enrollments.values("section_id"))

        day = self._date_param("date")
        if day:
            qs = qs.filter(diary_date=day)
        date_from, date_to = self._date_param("date_from"), self._date_param("date_to")
        if date_from:
            qs = qs.filter(diary_date__gte=date_from)
        if date_to:
            qs = qs.filter(diary_date__lte=date_to)
        due = self._date_param("due")
        if due:
            qs = qs.filter(due_date=due)
        if _truthy(params.get("upcoming")):
            qs = qs.filter(due_date__gte=timezone.localdate()).order_by("due_date", "-created_at")
        return qs

    # ---- writes ------------------------------------------------------------
    @transaction.atomic
    def perform_create(self, serializer):
        section = serializer.validated_data["section"]
        campus = section.school_class.campus
        entry = serializer.save(
            campus=campus,
            session=section.school_class.session,
            posted_by=self.request.user,
        )
        # Push fan-out runs in a worker once this row is committed — never in
        # the request path (a section can be 60+ students plus parents).
        dispatch_after_commit(campus_tasks.send_homework_diary_posted, str(entry.pk))
        log_action(
            campus.id, self.request.user, "homework_diary.create", target=entry,
            summary=f"homework diary: {entry.subject.name} / {entry.section_id} / {entry.diary_date}",
        )

    @transaction.atomic
    def perform_update(self, serializer):
        entry = serializer.instance
        new_subject = serializer.validated_data.get("subject")
        if new_subject is not None and new_subject.pk != entry.subject_id:
            # The permission class judged the OLD subject; changing it needs
            # rights over the NEW one too.
            if not can_manage_section_subject(self.request.user, entry.campus_id, entry.section_id, new_subject.pk):
                raise PermissionDenied("You can't post homework for that subject in this section.")
        extra = {}
        new_due = serializer.validated_data.get("due_date")
        if new_due is not None and new_due != entry.due_date:
            extra["due_reminder_sent_at"] = None  # new deadline -> eligible for a fresh reminder
        entry = serializer.save(**extra)
        log_action(
            entry.campus_id, self.request.user, "homework_diary.update", target=entry,
            summary=f"homework diary edited: {entry.pk}", fields=sorted(serializer.validated_data.keys()),
        )

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        log_action(
            instance.campus_id, self.request.user, "homework_diary.delete", target=instance,
            summary=f"homework diary removed: {instance.pk}",
        )

    # ---- one day, class-wise ---------------------------------------------------
    @action(detail=False, methods=["get"], url_path="day")
    def day(self, request):
        """The diary of ONE section for ONE day (default: today).

        `missing_subjects` (staff only) = subjects that have an approved
        teacher in this section but no entry yet that day — all of them for
        the class-teacher / admin, only their own for a subject-teacher.
        Students and parents always get `[]`.
        """
        section_id = request.query_params.get("section")
        if not section_id:
            raise ValidationError({"section": "This query parameter is required."})
        section = _section_or_none(section_id)
        if section is None:
            return Response({"detail": "Section not found."}, status=404)
        day = self._date_param("date") or timezone.localdate()

        entries = list(self.get_queryset().filter(section_id=section.pk, diary_date=day))
        data = self.get_serializer(entries, many=True).data

        user = request.user
        campus_id = section.school_class.campus_id
        sees_all = is_class_teacher_of_section(user, section.pk) or is_campus_admin_or_principal(user, campus_id)
        approved = SubjectTeacherassigments.objects.filter(
            section_id=section.pk, status=SubjectTeacherassigments.Status.APPROVED, staff__is_active=True
        ).select_related("subject")
        if not sees_all:
            approved = approved.filter(staff__user=user)
        done = {e.subject_id for e in entries}
        missing, seen = [], set()
        for assignment in approved.order_by("subject__name"):
            subject = assignment.subject
            if subject.pk in done or subject.pk in seen:
                continue
            seen.add(subject.pk)
            missing.append({"id": str(subject.pk), "name": subject.name})
        return Response(
            {
                "date": day.isoformat(),
                "section": str(section.pk),
                "entries": data,
                "missing_subjects": missing,
            }
        )
