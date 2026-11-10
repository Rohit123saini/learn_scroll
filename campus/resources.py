# campus/resources.py
"""
CLASS RESOURCES API (content share) — subject-wise folders of PDF / video /
documents / links, per section.

    Folders  — `/campus/resource-folders/`
    GET    resource-folders/                    list  (?section= ?subject= ?parent=<id>|root ?campus=)
    POST   resource-folders/                    create {section, subject, name} or {parent, name}
    GET    resource-folders/{id}/               retrieve
    PATCH  resource-folders/{id}/               rename / reorder (`name`, `order`)
    DELETE resource-folders/{id}/               soft delete — also hides its sub-folders + resources
    GET    resource-folders/subjects/?section=  one row per subject of the section (counts + `can_post`)

    Resources — `/campus/class-resources/`
    GET    class-resources/                     list  (?folder= ?section= ?subject= ?kind= ?q= ?pinned=1 ?student=)
    POST   class-resources/                     create (multipart for a file, JSON for a link)
    GET    class-resources/{id}/                retrieve
    PATCH  class-resources/{id}/                edit / replace file / move folder / publish / pin
    DELETE class-resources/{id}/                soft delete (is_active=False)
    POST   class-resources/{id}/open/           "I opened it" — counts the view (students / parents only)
    GET    class-resources/{id}/viewers/        who opened it + who of the section hasn't (managers only)
    POST   class-resources/{id}/copy/           {"sections": [id, ...]} copy to other sections of the same
                                                subject (folders are found / created there) — see resource_copy.py

Who sees what (same rules as every section-scoped campus viewset):
  * ACTIVE staff of the campus: every section's folders and published resources.
  * Student: only their own current section(s). Parent: the linked child's.
  * A DRAFT (`is_published=False`) is visible only to its poster and to people
    who can manage that section/subject.
Who may write: `can_manage_section_subject` — admin/principal anywhere, the
section's class teacher for any subject, an APPROVED subject teacher for their
own subject only, a moderator section-wide. Students/parents never (403).

Push: a PUBLISHED resource fans a `campus_resource_posted` push out to the
section's students + parents (Celery, after commit — `tasks.send_class_resource_posted`);
a draft is pushed once, when it is published. See resource_push.py.
Never hard-deleted from the API; files stay in storage for audit.
"""
import logging
from functools import reduce
from operator import or_

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import BooleanField, Case, Count, Exists, F, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.async_utils import dispatch_after_commit

from . import tasks as campus_tasks
from .audit import log_action
from .models import (
    ClassResource,
    ClassResourceView,
    ClassTeacherassigments,
    ResourceFolder,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
    SubjectTeacherassigments,
)
from .permissions import IsSectionSubjectStaffOrReadOnly
from .resource_copy import MAX_COPY_SECTIONS, copy_resource_to_sections
from .resource_serializers import (
    MAX_FOLDERS_PER_SUBJECT,
    MAX_RESOURCES_PER_FOLDER,
    ClassResourceSerializer,
    ResourceFolderSerializer,
)
from .serializers import MinimalUserSerializer
from .throttles import CampusResourceFolderThrottle, CampusResourceUploadThrottle
from .views import CampusMemberScopedMixin
from .visibility import get_visibility

logger = logging.getLogger(__name__)

_LIST_CAP = 500  # viewers / not-opened lists are bounded, never unbounded


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _section_or_none(section_id):
    """`Section` for a client-supplied id, `None` for a missing / garbage id."""
    if not section_id:
        return None
    try:
        return Section.objects.select_related("school_class").filter(pk=section_id).first()
    except (ValueError, TypeError, DjangoValidationError):
        return None


def _folder_or_none(folder_id):
    """Active `ResourceFolder` for a client-supplied id, else `None`."""
    if not folder_id:
        return None
    try:
        return ResourceFolder.objects.filter(pk=folder_id, is_active=True).first()
    except (ValueError, TypeError, DjangoValidationError):
        return None


class ManageScope:
    """
    "Which (campus, section, subject) triples can this user post to" — loaded in
    4 small queries once per request, then answered in memory (`can`) or as one
    `Q` for querysets (`q`). Mirrors `permissions.can_manage_section_subject`
    exactly, so a list never shows a manage flag the write path would refuse:

      * admin / principal of a campus -> everything in that campus
      * class teacher of a section    -> every subject of that section
      * moderator (approved subject / class assignment) -> section-wide
      * approved subject teacher      -> only their own (section, subject)
    """

    def __init__(self, user):
        self.admin_campuses = set()
        self.section_wide = set()
        self.pairs = set()
        if not user or not getattr(user, "is_authenticated", False):
            return
        staff = StaffProfile.objects.filter(user=user, is_active=True)
        self.admin_campuses = {
            s.campus_id for s in staff if s.role in (StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD)
        }
        moderator = StaffProfile.Role.MODERATOR
        # class teacher of a section -> every subject of that section
        self.section_wide.update(
            ClassTeacherassigments.objects.filter(staff__user=user, staff__is_active=True)
            .values_list("section_id", flat=True)
        )
        for section_id, subject_id, role in SubjectTeacherassigments.objects.filter(
            staff__user=user, staff__is_active=True, status=SubjectTeacherassigments.Status.APPROVED
        ).values_list("section_id", "subject_id", "staff__role"):
            self.pairs.add((section_id, subject_id))
            if role == moderator:
                self.section_wide.add(section_id)

    def can(self, campus_id, section_id, subject_id=None):
        if campus_id in self.admin_campuses or section_id in self.section_wide:
            return True
        return subject_id is not None and (section_id, subject_id) in self.pairs

    def q(self):
        parts = []
        if self.admin_campuses:
            parts.append(Q(campus_id__in=self.admin_campuses))
        if self.section_wide:
            parts.append(Q(section_id__in=self.section_wide))
        for section_id, subject_id in sorted(self.pairs, key=str)[:_LIST_CAP]:
            parts.append(Q(section_id=section_id, subject_id=subject_id))
        return reduce(or_, parts) if parts else Q(pk__in=[])


class _ResourceScopedMixin(CampusMemberScopedMixin):
    """Membership scoping by section + the per-request `ManageScope`."""

    campus_field_path = "campus"
    member_scope = "section"
    member_section_path = "section"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    @property
    def manage_scope(self):
        scope = getattr(self, "_manage_scope", None)
        if scope is None:
            scope = self._manage_scope = ManageScope(self.request.user)
        return scope

    def _bad_id_guard(self, qs, **filters):
        try:
            return qs.filter(**filters)
        except (ValueError, TypeError, DjangoValidationError):
            raise ValidationError({key: "Invalid id." for key in filters})


# ===========================================================================
# Folders
# ===========================================================================

class ResourceFolderViewSet(_ResourceScopedMixin, viewsets.ModelViewSet):
    serializer_class = ResourceFolderSerializer

    def get_throttles(self):
        if self.action == "create":
            return [CampusResourceFolderThrottle()]
        return super().get_throttles()

    # ---- permission input --------------------------------------------------
    def get_section_subject_for_permission_check(self, request):
        """Edit / delete are judged by the STORED row, never by ids in the body.
        Create is judged by the parent folder (a sub-folder inherits its scope)
        or, for a root folder, by the posted `(section, subject)`."""
        pk = self.kwargs.get("pk")
        if pk:
            try:
                obj = self.get_queryset().filter(pk=pk).first()
            except (ValueError, TypeError, DjangoValidationError):
                obj = None
            if obj:
                return obj.campus_id, obj.section_id, obj.subject_id
            return None, None, None
        parent_id = request.data.get("parent")
        if parent_id:
            parent = _folder_or_none(parent_id)
            if parent is None:
                return None, None, None
            return parent.campus_id, parent.section_id, parent.subject_id
        section = _section_or_none(request.data.get("section"))
        if section is None:
            return None, None, None
        return section.school_class.campus_id, section.id, request.data.get("subject") or None

    # ---- queryset ----------------------------------------------------------
    def get_queryset(self):
        direct = (
            ClassResource.objects.filter(folder=OuterRef("pk"), is_active=True, is_published=True)
            .order_by().values("folder").annotate(c=Count("pk")).values("c")
        )
        nested = (
            ClassResource.objects.filter(
                Q(folder=OuterRef("pk")) | Q(folder__parent=OuterRef("pk")),
                is_active=True, is_published=True, folder__is_active=True,
            )
            .order_by().values("section_id").annotate(c=Count("pk")).values("c")
        )
        kids = (
            ResourceFolder.objects.filter(parent=OuterRef("pk"), is_active=True)
            .order_by().values("parent").annotate(c=Count("pk")).values("c")
        )
        manage_q = self.manage_scope.q()
        qs = (
            ResourceFolder.objects.filter(is_active=True)
            .filter(Q(parent__isnull=True) | Q(parent__is_active=True))
            .select_related("section__school_class", "subject", "created_by")
            .annotate(
                resource_count=Coalesce(Subquery(direct, output_field=IntegerField()), 0),
                total_resource_count=Coalesce(Subquery(nested, output_field=IntegerField()), 0),
                child_count=Coalesce(Subquery(kids, output_field=IntegerField()), 0),
                can_manage_flag=Case(When(manage_q, then=Value(True)), default=Value(False), output_field=BooleanField()),
            )
        )
        qs = self.filter_queryset_to_my_campuses(qs, self.request)
        params = self.request.query_params
        for key in ("section", "subject"):
            if params.get(key):
                qs = self._bad_id_guard(qs, **{f"{key}_id": params[key]})
        parent = params.get("parent")
        if parent:
            qs = qs.filter(parent__isnull=True) if parent == "root" else self._bad_id_guard(qs, parent_id=parent)
        return qs

    # ---- writes ------------------------------------------------------------
    def _refresh(self, serializer):
        """Re-read with the annotations so the response carries counts / flags."""
        serializer.instance = self.get_queryset().get(pk=serializer.instance.pk)

    @transaction.atomic
    def perform_create(self, serializer):
        folder = serializer.save(created_by=self.request.user)
        log_action(
            folder.campus_id, self.request.user, "resource_folder.create", target=folder,
            summary=f"resource folder: {folder.name} / {folder.subject_id} / {folder.section_id}",
        )
        self._refresh(serializer)

    @transaction.atomic
    def perform_update(self, serializer):
        folder = serializer.save()
        log_action(
            folder.campus_id, self.request.user, "resource_folder.update", target=folder,
            summary=f"resource folder edited: {folder.pk}", fields=sorted(serializer.validated_data.keys()),
        )
        self._refresh(serializer)

    @transaction.atomic
    def perform_destroy(self, instance):
        now = timezone.now()
        ids = [instance.pk, *instance.children.filter(is_active=True).values_list("pk", flat=True)]
        ClassResource.objects.filter(folder_id__in=ids, is_active=True).update(is_active=False, updated_at=now)
        ResourceFolder.objects.filter(pk__in=ids).update(is_active=False, updated_at=now)
        log_action(
            instance.campus_id, self.request.user, "resource_folder.delete", target=instance,
            summary=f"resource folder removed: {instance.pk}", folders=len(ids),
        )

    # ---- one row per subject of a section -------------------------------------
    @action(detail=False, methods=["get"], url_path="subjects")
    def subjects(self, request):
        """The landing screen: every subject of ONE section with its folder and
        published-resource counts, and `can_post` (can the caller add content
        to that subject). Subjects with an approved teacher show up even while
        empty, so students see the full subject list from day one."""
        section = _section_or_none(request.query_params.get("section"))
        if section is None:
            raise ValidationError({"section": "A valid section is required."})
        campus_id = section.school_class.campus_id
        vis = get_visibility(request.user, request)
        if campus_id not in vis.staff_campus_ids and section.pk not in vis.visible_section_ids:
            return Response({"detail": "Section not found."}, status=404)

        subject_ids = set(
            SubjectTeacherassigments.objects.filter(
                section=section, status=SubjectTeacherassigments.Status.APPROVED, staff__is_active=True
            ).values_list("subject_id", flat=True)
        ) | set(
            ResourceFolder.objects.filter(section=section, is_active=True).values_list("subject_id", flat=True)
        )
        folder_counts = dict(
            ResourceFolder.objects.filter(section=section, is_active=True, parent__isnull=True)
            .order_by().values_list("subject_id").annotate(c=Count("pk"))
        )
        resource_counts = dict(
            ClassResource.objects.filter(
                section=section, is_active=True, is_published=True, folder__is_active=True
            ).order_by().values_list("subject_id").annotate(c=Count("pk"))
        )
        scope = self.manage_scope
        rows = [
            {
                "id": str(subject.pk),
                "name": subject.name,
                "code": subject.code,
                "folder_count": folder_counts.get(subject.pk, 0),
                "resource_count": resource_counts.get(subject.pk, 0),
                "can_post": scope.can(campus_id, section.pk, subject.pk),
            }
            for subject in Subject.objects.filter(pk__in=subject_ids).order_by("name")
        ]
        return Response({"section": str(section.pk), "campus": str(campus_id), "subjects": rows})


# ===========================================================================
# Resources
# ===========================================================================

class ClassResourceViewSet(_ResourceScopedMixin, viewsets.ModelViewSet):
    serializer_class = ClassResourceSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    # ---- throttling / permissions ----------------------------------------------
    def get_throttles(self):
        if self.action in ("create", "copy_to_sections") or (
            self.action == "partial_update" and self.request is not None and self.request.FILES
        ):
            return [CampusResourceUploadThrottle()]
        return super().get_throttles()

    def get_permissions(self):
        # `open` is a POST (it records a view) but it is a READ for the caller:
        # visibility is already enforced by get_queryset() -> 404 if hidden.
        if self.action == "open":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_section_subject_for_permission_check(self, request):
        """Edit / delete are judged by the STORED row. Create is judged by the
        target folder's own (section, subject) — never by ids in the body."""
        pk = self.kwargs.get("pk")
        if pk:
            try:
                obj = self.get_queryset().filter(pk=pk).first()
            except (ValueError, TypeError, DjangoValidationError):
                obj = None
            if obj:
                return obj.campus_id, obj.section_id, obj.subject_id
            return None, None, None
        folder = _folder_or_none(request.data.get("folder"))
        if folder is None:
            return None, None, None
        return folder.campus_id, folder.section_id, folder.subject_id

    # ---- queryset ----------------------------------------------------------
    def get_queryset(self):
        user = self.request.user
        manage_q = self.manage_scope.q()
        seen = ClassResourceView.objects.filter(resource=OuterRef("pk"), user=user)
        qs = (
            ClassResource.objects.filter(is_active=True, folder__is_active=True)
            .filter(Q(folder__parent__isnull=True) | Q(folder__parent__is_active=True))
            .select_related("folder", "subject", "section__school_class", "posted_by")
            .annotate(
                can_manage_flag=Case(When(manage_q, then=Value(True)), default=Value(False), output_field=BooleanField()),
                viewed_by_me_flag=Exists(seen),
                opened_by_count_ann=Count("views", distinct=True),
            )
            # Explicit on purpose: `Count()` makes this a GROUP BY query and Django
            # then ignores `Meta.ordering`, which would leave the list unordered
            # (pinned not first) and the pagination unstable.
            .order_by("-is_pinned", "order", "-created_at", "pk")
        )
        qs = self.filter_queryset_to_my_campuses(qs, self.request)
        # Drafts: only the poster and people who can manage the section/subject.
        qs = qs.filter(Q(is_published=True) | Q(posted_by=user) | manage_q)

        params = self.request.query_params
        for key in ("folder", "section", "subject"):
            if params.get(key):
                qs = self._bad_id_guard(qs, **{f"{key}_id": params[key]})
        if params.get("kind"):
            qs = qs.filter(kind=params["kind"])
        if params.get("q"):
            qs = qs.filter(title__icontains=params["q"].strip())
        if _truthy(params.get("pinned")):
            qs = qs.filter(is_pinned=True)

        student_id = params.get("student")
        if student_id:
            # A parent asks for "my child's resources" by child id; a student can
            # pass their own. Anything outside what the caller may see -> empty.
            vis = get_visibility(self.request.user, self.request)
            try:
                enrollments = StudentEnrollment.objects.filter(
                    student_id=student_id, status=StudentEnrollment.Status.ACTIVE
                )
                if str(student_id) not in {str(pk) for pk in vis.visible_student_ids}:
                    enrollments = enrollments.filter(section__school_class__campus_id__in=vis.staff_campus_ids)
                qs = qs.filter(section_id__in=enrollments.values("section_id"))
            except (ValueError, TypeError, DjangoValidationError):
                raise ValidationError({"student": "Invalid id."})
        return qs

    # ---- writes ------------------------------------------------------------
    def _refresh(self, serializer):
        serializer.instance = self.get_queryset().get(pk=serializer.instance.pk)

    @transaction.atomic
    def perform_create(self, serializer):
        resource = serializer.save(posted_by=self.request.user)
        log_action(
            resource.campus_id, self.request.user, "class_resource.create", target=resource,
            summary=f"class resource: {resource.kind} / {resource.title[:60]} / {resource.section_id}",
        )
        if resource.is_published:
            # Push fan-out runs in a worker once this row is committed — never
            # in the request path (a section is 60+ students plus parents).
            dispatch_after_commit(campus_tasks.send_class_resource_posted, str(resource.pk))
        self._refresh(serializer)

    @transaction.atomic
    def perform_update(self, serializer):
        was_published = serializer.instance.is_published  # read BEFORE save() mutates the instance
        resource = serializer.save()
        log_action(
            resource.campus_id, self.request.user, "class_resource.update", target=resource,
            summary=f"class resource edited: {resource.pk}", fields=sorted(serializer.validated_data.keys()),
        )
        if resource.is_published and not was_published and resource.notified_at is None:
            # a draft going live -> its one and only push (the task claims it)
            dispatch_after_commit(campus_tasks.send_class_resource_posted, str(resource.pk))
        self._refresh(serializer)

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        log_action(
            instance.campus_id, self.request.user, "class_resource.delete", target=instance,
            summary=f"class resource removed: {instance.pk}",
        )

    # ---- "I opened it" ---------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="open")
    def open(self, request, pk=None):
        """Counts one view for a student / parent / other staff and returns the
        resource. People who manage the resource are never counted (a teacher
        previewing their own PDF must not inflate the numbers)."""
        resource = self.get_object()
        if not self.manage_scope.can(resource.campus_id, resource.section_id, resource.subject_id):
            self._record_view(resource, request.user)
        resource = self.get_queryset().get(pk=resource.pk)
        return Response(self.get_serializer(resource).data)

    @staticmethod
    def _record_view(resource, user):
        now = timezone.now()
        rows = ClassResourceView.objects.filter(resource=resource, user=user)
        if rows.update(view_count=F("view_count") + 1, last_viewed_at=now):
            return
        try:
            with transaction.atomic():
                ClassResourceView.objects.create(resource=resource, user=user, last_viewed_at=now)
        except IntegrityError:  # a parallel first open won the race — just count ours
            rows.update(view_count=F("view_count") + 1, last_viewed_at=now)

    # ---- copy to other sections ----------------------------------------------------
    @action(detail=True, methods=["post"], url_path="copy")
    def copy_to_sections(self, request, pk=None):
        """Body: `{"sections": ["<section id>", ...]}` (max `CAMPUS_RESOURCE_COPY_MAX_SECTIONS`,
        default 10). The caller must manage the source (permission class) and, per
        target, the SAME subject there. Returns 201 with `created` (full resource
        rows) and `skipped` (`{section, reason}`) — a partly successful copy is
        still a 201; 400 only when nothing usable was asked for."""
        source = self.get_object()
        data = request.data
        raw = data.getlist("sections") if hasattr(data, "getlist") else data.get("sections")
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple)) or not raw:
            raise ValidationError({"sections": "Choose at least one section."})
        section_ids = [str(item).strip() for item in raw if str(item).strip()][:50]
        if not section_ids:
            raise ValidationError({"sections": "Choose at least one section."})
        if len({s for s in section_ids}) > MAX_COPY_SECTIONS:
            raise ValidationError({"sections": f"You can copy to at most {MAX_COPY_SECTIONS} sections at a time."})

        with transaction.atomic():
            created, skipped = copy_resource_to_sections(
                source,
                section_ids,
                user=request.user,
                scope=self.manage_scope,
                max_resources_per_folder=MAX_RESOURCES_PER_FOLDER,
                max_folders=MAX_FOLDERS_PER_SUBJECT,
            )
            for copy in created:
                log_action(
                    copy.campus_id, request.user, "class_resource.copy", target=copy,
                    summary=f"class resource copied: {source.pk} -> {copy.pk} / {copy.section_id}",
                )
                if copy.is_published:
                    dispatch_after_commit(campus_tasks.send_class_resource_posted, str(copy.pk))

        rows = []
        if created:
            qs = self.get_queryset().filter(pk__in=[c.pk for c in created])
            rows = self.get_serializer(qs, many=True).data
        return Response({"created": rows, "skipped": skipped}, status=201)

    # ---- who opened it ----------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="viewers")
    def viewers(self, request, pk=None):
        """Managers only: people who opened it (most recent first) and the
        section's active students who haven't yet."""
        resource = self.get_object()
        if not self.manage_scope.can(resource.campus_id, resource.section_id, resource.subject_id):
            return Response({"detail": "You can't see who opened this."}, status=403)

        rows = list(
            ClassResourceView.objects.filter(resource=resource).select_related("user")[:_LIST_CAP]
        )
        opened_ids = {row.user_id for row in rows}
        pending = [
            enrollment.student
            for enrollment in StudentEnrollment.objects.filter(
                section_id=resource.section_id, status=StudentEnrollment.Status.ACTIVE
            )
            .exclude(student_id__in=opened_ids)
            .select_related("student")
            .order_by("roll_number", "student__username")[:_LIST_CAP]
        ]
        return Response(
            {
                "opened_count": len(rows),
                "opened": [
                    {
                        "user": MinimalUserSerializer(row.user).data,
                        "view_count": row.view_count,
                        "first_viewed_at": row.first_viewed_at,
                        "last_viewed_at": row.last_viewed_at,
                    }
                    for row in rows
                ],
                "not_opened_count": len(pending),
                "not_opened": MinimalUserSerializer(pending, many=True).data,
            }
        )
