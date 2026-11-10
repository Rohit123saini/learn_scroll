# campus/recording_views.py
"""
RECORDED CLASSES + CLASS NOTES + PRIVATE STUDENT NOTES — the HTTP layer.

Rules live in `campus/recordings.py` (recording life-cycle, who may watch) and
`campus/models.py` (`ClassNote`, `StudentNote`); the shapes in
`campus/recording_serializers.py`. This file only authenticates, scopes and delegates.

    Recordings — `/campus/recordings/`            (a CampusLiveSession seen as "a recorded class")
    GET    recordings/                     list  (?section= ?subject= ?status= ?from= ?to= ?mine=1 ?q= ?campus=)
    GET    recordings/{id}/                detail: + playable `url` (only when the caller may watch),
                                           the teacher's notes of that class, the caller's OWN private notes
    POST   recordings/{id}/hide/           pull a ready recording back from students (managers)
    POST   recordings/{id}/show/           put it back
    (start / stop live on the session itself: POST live-sessions/{id}/start-recording|stop-recording/
     — see CampusLiveSessionViewSet in views.py)

    Class notes — `/campus/class-notes/`          (teacher -> students, text and/or one file)
    GET    class-notes/                    list  (?section= ?subject= ?live_session= ?q= ?pinned=1)
    POST   class-notes/                    create (multipart for a file, JSON for text). Give `live_session`
                                           (section + subject then come from it) OR `section` + `subject`
    GET    class-notes/{id}/               retrieve
    PATCH  class-notes/{id}/               edit / publish / pin / replace file / `remove_file=true`
    DELETE class-notes/{id}/               soft delete

    My notes — `/campus/my-notes/`                (a student's PRIVATE scratch pad — author only)
    GET    my-notes/                       list  (?live_session= ?subject= ?section= ?q=)
    POST   my-notes/                       create {text, live_session | subject (+section), at_seconds?}
    PATCH  my-notes/{id}/                  edit text / time
    DELETE my-notes/{id}/                  soft delete

Who sees what:
  * Recordings: ACTIVE staff of the campus see every recording (ready, processing,
    failed, hidden). A student / linked parent sees only READY, un-hidden recordings of
    their own section(s).
  * Class notes: students / parents of the section + campus staff; a DRAFT only its
    poster and people who manage that subject.
  * Student notes: the author, nobody else — teachers, parents and admins get a 404.
Who may write: class notes — `can_manage_section_subject` (or, for a note tied to a
class, whoever may run that session, e.g. the substitute); student notes — an ACTIVE
student of the section only (never a parent).
"""
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import BooleanField, Case, Count, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_date
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response

from core.async_utils import dispatch_after_commit

from . import live_class, recordings
from . import tasks as campus_tasks
from .audit import log_action
from .models import (
    CampusLiveSession,
    ClassNote,
    StudentEnrollment,
    StudentNote,
    SubjectTeacherassigments,
)
from .permissions import can_manage_section_subject
from .recording_serializers import (
    ClassNoteSerializer,
    RecordingDetailSerializer,
    RecordingSerializer,
    StudentNoteSerializer,
)
from .resources import _ResourceScopedMixin, _section_or_none
from .throttles import CampusNoteWriteThrottle, CampusRecordingControlThrottle, CampusResourceUploadThrottle
from .visibility import get_visibility

logger = logging.getLogger(__name__)

RS = CampusLiveSession.RecordingStatus

# Abuse guards, not product limits: nobody writes 500 notes about one class.
MAX_STUDENT_NOTES_PER_CLASS = 500
MAX_STUDENT_NOTES_PER_SUBJECT = 2000


def _live_or_none(live_id):
    """`CampusLiveSession` for a client-supplied id, `None` for a missing / garbage id."""
    if not live_id:
        return None
    try:
        return (
            CampusLiveSession.objects.select_related("section__school_class", "subject", "teacher")
            .filter(pk=live_id)
            .first()
        )
    except (ValueError, TypeError, DjangoValidationError):
        return None


# ===========================================================================
# Recordings
# ===========================================================================

class RecordingViewSet(_ResourceScopedMixin, viewsets.ReadOnlyModelViewSet):
    """See the module docstring. A ReadOnly viewset plus two POST actions."""

    campus_field_path = "section__school_class__campus"
    member_section_path = "section"
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]
    serializer_class = RecordingSerializer

    def get_serializer_class(self):
        if self.action == "retrieve":
            return RecordingDetailSerializer
        return RecordingSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["manage_scope"] = self.manage_scope
        return context

    def get_queryset(self):
        user = self.request.user
        vis = get_visibility(user, self.request)
        notes = (
            ClassNote.objects.filter(live_session=OuterRef("pk"), is_active=True, is_published=True)
            .order_by().values("live_session").annotate(c=Count("pk")).values("c")
        )
        mine = (
            StudentNote.objects.filter(live_session=OuterRef("pk"), author=user, is_active=True)
            .order_by().values("live_session").annotate(c=Count("pk")).values("c")
        )
        qs = (
            CampusLiveSession.objects.exclude(recording_status=RS.NONE)
            .select_related("section__school_class", "subject", "teacher__user")
            .annotate(
                notes_count=Coalesce(Subquery(notes, output_field=IntegerField()), 0),
                my_notes_count=Coalesce(Subquery(mine, output_field=IntegerField()), 0),
            )
        )
        qs = self.filter_queryset_to_my_campuses(qs, self.request)
        # Staff of the campus see every state; students / parents only a READY, un-hidden one.
        qs = qs.filter(
            Q(section__school_class__campus_id__in=vis.staff_campus_ids)
            | Q(recording_status=RS.READY, recording_hidden=False)
        )

        params = self.request.query_params
        for key in ("section", "subject"):
            if params.get(key):
                qs = self._bad_id_guard(qs, **{f"{key}_id": params[key]})
        if params.get("status") in RS.values:
            qs = qs.filter(recording_status=params["status"])
        if params.get("q"):
            qs = qs.filter(subject__name__icontains=params["q"].strip())
        day_from = parse_date(params.get("from") or "")
        if day_from:
            qs = qs.filter(Q(occurrence_date__gte=day_from) | Q(occurrence_date__isnull=True, scheduled_at__date__gte=day_from))
        day_to = parse_date(params.get("to") or "")
        if day_to:
            qs = qs.filter(Q(occurrence_date__lte=day_to) | Q(occurrence_date__isnull=True, scheduled_at__date__lte=day_to))
        if str(params.get("mine", "")).lower() in ("1", "true", "yes"):
            qs = qs.filter(Q(teacher__user=user) | Q(started_by=user))
        # Explicit: newest class first, stable for pagination.
        return qs.order_by("-scheduled_at", "-pk")

    # ---- hide / show (managers) --------------------------------------------------
    @action(detail=True, methods=["post"], url_path="hide", throttle_classes=[CampusRecordingControlThrottle])
    def hide(self, request, pk=None):
        """Pull a recording back from students (the file is kept). Idempotent."""
        return self._set_hidden(request, True)

    @action(detail=True, methods=["post"], url_path="show", throttle_classes=[CampusRecordingControlThrottle])
    def show(self, request, pk=None):
        """Put a hidden recording back. If it was never announced, the "recording is
        ready" push goes out now (the task claims it, so never twice)."""
        return self._set_hidden(request, False)

    def _set_hidden(self, request, hidden: bool):
        live = self.get_object()
        if not recordings.can_manage_recording(request.user, live):
            raise PermissionDenied("Only this class's teacher (or a manager of the section) can do this.")
        if live.recording_hidden != hidden:
            live.recording_hidden = hidden
            live.save(update_fields=["recording_hidden"])
            log_action(
                live.section.school_class.campus_id, request.user,
                "class_recording.hide" if hidden else "class_recording.show",
                target=live, summary=f"recording {'hidden' if hidden else 'shown'}: {live.pk}",
            )
        if (
            not hidden
            and live.recording_status == RS.READY
            and live.recording_notified_at is None
        ):
            recordings.announce_ready(live.pk)
        fresh = self.get_queryset().get(pk=live.pk)
        return Response(RecordingDetailSerializer(fresh, context=self.get_serializer_context()).data)


# ===========================================================================
# Class notes (teacher)
# ===========================================================================

class CanWriteClassNote(BasePermission):
    """Create: whoever may manage the (section, subject) — or, for a note tied to a
    class, whoever may RUN that class (a substitute teacher usually isn't an approved
    subject teacher). Edit / delete: judged by the STORED row, never by ids in the body."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        if view.action == "create":
            live = _live_or_none(request.data.get("live_session"))
            if live is not None and live_class.can_run_session(request.user, live):
                return True
        campus_id, section_id, subject_id = view.get_section_subject_for_permission_check(request)
        if campus_id is None or section_id is None:
            return False
        return can_manage_section_subject(request.user, campus_id, section_id, subject_id)


class ClassNoteViewSet(_ResourceScopedMixin, viewsets.ModelViewSet):
    serializer_class = ClassNoteSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    permission_classes = [IsAuthenticated, CanWriteClassNote]

    def get_throttles(self):
        if self.request is not None and self.request.method not in SAFE_METHODS:
            throttles = [CampusNoteWriteThrottle()]
            if self.request.FILES:
                throttles.append(CampusResourceUploadThrottle())
            return throttles
        return super().get_throttles()

    # ---- permission input --------------------------------------------------------
    def get_section_subject_for_permission_check(self, request):
        pk = self.kwargs.get("pk")
        if pk:
            try:
                obj = self.get_queryset().filter(pk=pk).first()
            except (ValueError, TypeError, DjangoValidationError):
                obj = None
            if obj:
                return obj.campus_id, obj.section_id, obj.subject_id
            return None, None, None
        live = _live_or_none(request.data.get("live_session"))
        if live is not None:
            return live.section.school_class.campus_id, live.section_id, live.subject_id
        section = _section_or_none(request.data.get("section"))
        if section is None:
            return None, None, None
        return section.school_class.campus_id, section.id, request.data.get("subject") or None

    # ---- queryset ------------------------------------------------------------------
    def get_queryset(self):
        user = self.request.user
        manage_q = self.manage_scope.q()
        qs = (
            ClassNote.objects.filter(is_active=True)
            .select_related("subject", "section__school_class", "posted_by", "live_session")
            .annotate(
                can_manage_flag=Case(When(manage_q, then=Value(True)), default=Value(False), output_field=BooleanField()),
            )
        )
        qs = self.filter_queryset_to_my_campuses(qs, self.request)
        # Drafts: only the poster and people who can manage the section/subject.
        qs = qs.filter(Q(is_published=True) | Q(posted_by=user) | manage_q)

        params = self.request.query_params
        for key in ("section", "subject", "live_session"):
            if params.get(key):
                qs = self._bad_id_guard(qs, **{f"{key}_id": params[key]})
        if params.get("q"):
            qs = qs.filter(title__icontains=params["q"].strip())
        if str(params.get("pinned", "")).lower() in ("1", "true", "yes"):
            qs = qs.filter(is_pinned=True)
        return qs.order_by("-is_pinned", "-created_at", "pk")

    # ---- writes ----------------------------------------------------------------------
    def _refresh(self, serializer):
        serializer.instance = self.get_queryset().get(pk=serializer.instance.pk)

    @transaction.atomic
    def perform_create(self, serializer):
        note = serializer.save(posted_by=self.request.user)
        log_action(
            note.campus_id, self.request.user, "class_note.create", target=note,
            summary=f"class note: {note.title[:60]} / {note.section_id}",
        )
        if note.is_published:
            # fan-out runs in a worker once this row is committed
            dispatch_after_commit(campus_tasks.send_class_note_posted, str(note.pk))
        self._refresh(serializer)

    @transaction.atomic
    def perform_update(self, serializer):
        was_published = serializer.instance.is_published  # read BEFORE save() mutates the instance
        note = serializer.save()
        log_action(
            note.campus_id, self.request.user, "class_note.update", target=note,
            summary=f"class note edited: {note.pk}", fields=sorted(serializer.validated_data.keys()),
        )
        if note.is_published and not was_published and note.notified_at is None:
            dispatch_after_commit(campus_tasks.send_class_note_posted, str(note.pk))
        self._refresh(serializer)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        log_action(
            instance.campus_id, self.request.user, "class_note.delete", target=instance,
            summary=f"class note removed: {instance.pk}",
        )


# ===========================================================================
# Student notes (private)
# ===========================================================================

class StudentNoteViewSet(viewsets.ModelViewSet):
    """A student's PRIVATE notes. Everything is filtered to `author=request.user`, so a
    note of somebody else is a plain 404 — for teachers, parents and admins too."""

    serializer_class = StudentNoteSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_throttles(self):
        if self.request is not None and self.request.method not in SAFE_METHODS:
            return [CampusNoteWriteThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        qs = StudentNote.objects.filter(author=self.request.user, is_active=True).select_related("subject")
        params = self.request.query_params
        try:
            for key in ("live_session", "subject", "section", "campus"):
                if params.get(key):
                    qs = qs.filter(**{f"{key}_id": params[key]})
        except (ValueError, TypeError, DjangoValidationError):
            raise ValidationError({"detail": "Invalid id."})
        if params.get("q"):
            qs = qs.filter(text__icontains=params["q"].strip())
        # Inside one class the notes read in video order; elsewhere newest first.
        if params.get("live_session"):
            return qs.order_by("at_seconds", "created_at", "pk")
        return qs.order_by("-created_at", "pk")

    # ---- create ----------------------------------------------------------------------
    def _scope_for_create(self, user, data):
        """-> (section, subject, live). Only an ACTIVE student of the section may write."""
        active = StudentEnrollment.Status.ACTIVE
        live = data.get("live_session")
        if live is not None:
            if not StudentEnrollment.objects.filter(student=user, section_id=live.section_id, status=active).exists():
                raise PermissionDenied("Only a student enrolled in this section can write notes for its classes.")
            return live.section, live.subject, live

        subject = data.get("subject")
        enrollments = StudentEnrollment.objects.filter(
            student=user, status=active, section__school_class__campus_id=subject.campus_id
        ).select_related("section__school_class__campus")
        wanted = str(self.request.data.get("section") or "").strip()
        if wanted:
            try:
                enrollments = enrollments.filter(section_id=wanted)
            except (ValueError, TypeError, DjangoValidationError):
                raise ValidationError({"section": "Invalid id."})
        rows = list(enrollments[:20])
        if not rows:
            raise PermissionDenied("Only a student enrolled in this section can write notes.")
        if len(rows) > 1:
            taught = set(
                SubjectTeacherassigments.objects.filter(
                    section_id__in=[r.section_id for r in rows], subject=subject,
                    status=SubjectTeacherassigments.Status.APPROVED,
                ).values_list("section_id", flat=True)
            )
            for row in rows:
                if row.section_id in taught:
                    return row.section, subject, None
        return rows[0].section, subject, None

    @transaction.atomic
    def perform_create(self, serializer):
        user = self.request.user
        section, subject, live = self._scope_for_create(user, serializer.validated_data)
        mine = StudentNote.objects.filter(author=user, is_active=True)
        if live is not None and mine.filter(live_session=live).count() >= MAX_STUDENT_NOTES_PER_CLASS:
            raise ValidationError({"detail": "You have reached the note limit for this class."})
        if mine.filter(subject=subject).count() >= MAX_STUDENT_NOTES_PER_SUBJECT:
            raise ValidationError({"detail": "You have reached the note limit for this subject."})
        serializer.save(
            author=user,
            campus=section.school_class.campus,
            section=section,
            subject=subject,
            live_session=live,
        )

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
