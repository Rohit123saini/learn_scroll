# campus/views/academics.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from ..models import (
    AcademicSession,
    ClassTeacherassigments,
    Department,
    Room,
    SchoolClass,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
    SubjectTeacherassigments,
)
from ..serializers import (
    AcademicSessionSerializer,
    ClassTeacherassigmentsSerializer,
    DepartmentSerializer,
    RoomSerializer,
    SchoolClassSerializer,
    SectionSerializer,
    StaffProfileSerializer,
    StudentEnrollmentSerializer,
    SubjectSerializer,
    SubjectTeacherassigmentsSerializer,
)
from ..audit import AuditedModelViewSetMixin
from rest_framework.permissions import IsAuthenticated
from ..bridge import NotifTypes
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.db.models import Q
from rest_framework.response import Response
from ..soft_delete_views import SoftDeleteViewSetMixin
from rest_framework.decorators import action
from .. import bridge
from django.core.cache import cache
from .. import roster as campus_roster
from ..permissions import (
    hod_department_id,
    is_campus_admin_or_principal,
    is_campus_approved,
    is_class_teacher_of_section,
    user_can,
)
from rest_framework import (
    status,
    viewsets,
)
from django.utils import timezone
from .. import year_end
from ._common import (
    CampusMemberScopedMixin,
    _truthy,
    logger,
)


class AcademicSessionViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = AcademicSessionSerializer
    campus_field_path = "campus"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(AcademicSession.objects.all(), self.request)

    @action(detail=True, methods=["post"], url_path="set-current")
    def set_current(self, request, pk=None):
        session = self.get_object()
        if not is_campus_admin_or_principal(request.user, session.campus_id):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        session.is_current = True
        session.save(update_fields=["is_current", "updated_at"])
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["post"])
    def rollover(self, request, pk=None):
        """Carries forward active enrollments from this campus's other
        sessions into this one (design doc §13). Queued via Celery so a
        large campus's rollover doesn't block the request; falls back
        to a synchronous call if Celery isn't configured in this
        deployment, so the endpoint still works either way."""
        from .. import tasks

        new_session = self.get_object()
        if not is_campus_admin_or_principal(request.user, new_session.campus_id):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        try:
            async_result = tasks.rollover_session.delay(new_session.campus_id, new_session.id)
            return Response({"detail": "Rollover queued.", "task_id": async_result.id}, status=status.HTTP_202_ACCEPTED)
        except Exception:
            result = tasks.rollover_session(new_session.campus_id, new_session.id)
            return Response(result)


    # ------------------------------------------------------------------
    # Year-end: copy structure -> plan -> promote (campus/year_end.py).
    # `self.get_object()` is the NEW session; `from_session` is the old one.
    # Admin / principal only, and a department-limited HOD may not run it.
    # ------------------------------------------------------------------
    def _year_end_context(self, request, data):
        new_session = self.get_object()
        campus = new_session.campus
        if not user_can(request.user, campus.id, "session.promote") or hod_department_id(request.user, campus.id):
            raise PermissionDenied("Only the campus admin / principal can run the year-end promotion.")
        from_id = data.get("from_session")
        old_session = AcademicSession.objects.filter(pk=from_id, campus=campus).first() if from_id else None
        if old_session is None:
            raise ValidationError({"from_session": "Pick the old session of this campus."})
        return campus, old_session, new_session

    @staticmethod
    def _year_end_call(fn, *args, **kwargs):
        try:
            return Response(fn(*args, **kwargs))
        except year_end.YearEndError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["get"], url_path="promotion-plan")
    def promotion_plan(self, request, pk=None):
        campus, old, new = self._year_end_context(request, request.query_params)
        return self._year_end_call(year_end.plan, campus, old, new)

    @action(detail=True, methods=["post"], url_path="copy-structure")
    def copy_structure(self, request, pk=None):
        campus, old, new = self._year_end_context(request, request.data)
        return self._year_end_call(
            year_end.copy_structure, campus, request.user, old, new,
            copy_teachers=_truthy(request.data.get("copy_teachers")),
        )

    @action(detail=True, methods=["post"], url_path="promote")
    def promote(self, request, pk=None):
        """Body: from_session, mapping [{from_class, action, to_class}], overrides
        [{student, action, to_section?}], dry_run (default TRUE), create_missing_sections,
        override_capacity, set_current. Nothing is written unless dry_run is false."""
        from django.core.cache import cache

        campus, old, new = self._year_end_context(request, request.data)
        d = request.data
        dry = _truthy(d.get("dry_run", True))
        lock = f"campus:year_end:{new.pk}"
        if not dry and not cache.add(lock, 1, timeout=900):
            return Response({"detail": "A promotion is already running for this session.", "code": "busy"},
                            status=status.HTTP_409_CONFLICT)
        try:
            return self._year_end_call(
                year_end.promote, campus, request.user, old, new, d.get("mapping") or [],
                overrides=d.get("overrides") or [], dry_run=dry,
                create_missing_sections=_truthy(d.get("create_missing_sections")),
                override_capacity=_truthy(d.get("override_capacity")),
                set_current=_truthy(d.get("set_current")),
            )
        finally:
            if not dry:
                cache.delete(lock)


class DepartmentViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "department"
    serializer_class = DepartmentSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        return Q(pk__in=vis.visible_department_ids)

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Department.objects.all(), self.request)


class SchoolClassViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "class"
    serializer_class = SchoolClassSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        return Q(pk__in=vis.visible_class_ids)

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(SchoolClass.objects.all(), self.request)


class SectionViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "section"
    serializer_class = SectionSerializer
    campus_field_path = "school_class__campus"
    member_scope = "section"
    member_section_path = "pk"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Section.objects.all(), self.request)

    def get_campus_id_for_permission_check(self, request):
        # Section's campus isn't a top-level `campus` field on the
        # request body — it comes from whichever `school_class` is
        # being pointed at.
        school_class_id = request.data.get("school_class")
        if school_class_id:
            sc = SchoolClass.objects.filter(pk=school_class_id).first()
            if sc:
                return sc.campus_id
        return super().get_campus_id_for_permission_check(request)

    def perform_create(self, serializer):
        section = serializer.save()
        self._audit("create", section)
        # TASK (design doc §3) — section-group auto-creation. Routed
        # through `campus.bridge` (never a direct `message` import).
        #
        # `bridge.create_section_group()` is now hard-wired (design doc
        # §10, Group A) straight through to
        # `core.classroom_chat_bridge.create_section_group()`, which
        # raises `ValueError` unless `actor` is already this section's
        # assigned class-teacher. A section **just created in this same
        # request never has one yet** — that's a separate step, done via
        # `ClassTeacherassigmentsViewSet` afterward — so this call is
        # *expected* to fail here on every normal creation, not just on
        # some edge case. We deliberately don't let that block section
        # creation (`serializer.save()` above has already committed the
        # row): the real, correct trigger for group creation is
        # `ClassTeacherassigmentsViewSet.perform_create()` below, once a
        # class-teacher genuinely exists to be the group's creator/ADMIN.
        # This call is kept here only as an idempotent no-op fast-path
        # for the rare case a class-teacher was somehow already assigned
        # before the section row existed (e.g. a fixture/migration
        # ordering quirk) — `create_section_group()` itself is idempotent,
        # so calling it twice (once here, once from the assigments
        # viewset) is always safe.
        try:
            bridge.create_section_group(section, actor=self.request.user)
        except ValueError:
            logger.info(
                "Skipped section-group creation for section %s: no class-teacher "
                "assigned yet (expected — will be created when one is).",
                section.pk,
            )


    def _require_section_dashboard(self, request, section):
        if not user_can(
            request.user, section.school_class.campus_id, "section.dashboard", section_id=section.id
        ):
            raise PermissionDenied("Only this section's class teacher or a campus admin can view this.")

    @action(detail=True, methods=["get"])
    def dashboard(self, request, pk=None):
        """[T4 §D] Roster + enrolled/capacity count + attendance snapshot +
        pending subject-teacher requests (+ doubts placeholder)."""
        section = self.get_object()
        self._require_section_dashboard(request, section)
        return Response(campus_roster.section_dashboard(section))

    @action(detail=True, methods=["get"])
    def roster(self, request, pk=None):
        """Paginated roster (default: ACTIVE students). `?q=` name/roll search,
        `?status=` active|transferred|graduated|promoted|withdrawn|all."""
        section = self.get_object()
        self._require_section_dashboard(request, section)
        wanted = request.query_params.get("status", "active")
        qs = StudentEnrollment.objects.filter(section=section, session=section.school_class.session)
        if wanted != "all":
            qs = qs.filter(status=wanted)
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(student__username__icontains=q) | Q(student__first_name__icontains=q)
                | Q(student__last_name__icontains=q) | Q(roll_number__iexact=q)
            )
        qs = qs.select_related("student").order_by("roll_number", "student__username")
        page = self.paginate_queryset(qs)
        data = StudentEnrollmentSerializer(page, many=True).data
        payload = self.get_paginated_response(data).data
        payload["capacity"] = campus_roster.capacity_info(section)
        return Response(payload)


class SubjectViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "subject"
    serializer_class = SubjectSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        return Q(pk__in=vis.subject_ids())

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Subject.objects.all(), self.request)


class RoomViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "room"
    serializer_class = RoomSerializer
    campus_field_path = "campus"
    member_scope = "none"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Room.objects.all(), self.request)


class StaffProfileViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """Admin/Principal-HOD invite staff (design doc §11). No self-signup
    — a `StaffProfile` is always created BY an existing admin/principal
    of that campus, targeting some other user id.

    G-2 fix: inviting staff is a "pull someone else into this campus"
    action, so it additionally requires the campus to be
    `verification_status=APPROVED` (see `permissions.is_campus_approved`'s
    docstring) — an unverified campus's self-appointed admin can still
    set up sessions/classes/sections/subjects, just not staff other than
    themselves (the creator's own ADMIN row was already created directly
    in `CampusViewSet.perform_create`, bypassing this check, per that
    view's docstring).
    """
    audit_prefix = "staff"
    serializer_class = StaffProfileSerializer
    campus_field_path = "campus"
    member_scope = "none"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(StaffProfile.objects.select_related("user"), self.request)  # [PERF] user_detail

    def perform_create(self, serializer):
        campus = serializer.validated_data["campus"]
        if not is_campus_approved(campus.id):
            raise PermissionDenied("This campus is pending platform verification and can't add staff yet.")
        self._audit("create", serializer.save())


class ClassTeacherassigmentsViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "class_teacher"
    serializer_class = ClassTeacherassigmentsSerializer
    campus_field_path = "section__school_class__campus"
    member_scope = "section"
    member_section_path = "section"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(ClassTeacherassigments.objects.select_related("staff__user"), self.request)  # [PERF] staff_detail

    def get_campus_id_for_permission_check(self, request):
        section_id = request.data.get("section")
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)

    def perform_create(self, serializer):
        assigments = serializer.save()
        self._audit("create", assigments)
        # This is the moment `bridge.create_section_group()`'s real
        # precondition (design doc §10) first becomes true: the section
        # now has an assigned class-teacher, who is the group's required
        # creator/ADMIN. `actor` must be *this assigments's* staff user
        # — not `self.request.user` — since an admin/principal (not the
        # teacher themself) is typically the one who creates this row.
        # `create_section_group()` is idempotent (returns the existing
        # group unchanged if one was already created — e.g. by
        # `SectionViewSet.perform_create()`'s own fast-path above), so
        # this is always safe to call, never a duplicate-create risk.
        try:
            bridge.create_section_group(assigments.section, actor=assigments.staff.user)
        except ValueError:
            # Shouldn't normally happen (we just made `assigments.staff`
            # this section's class-teacher), but kept as a defensive
            # backstop rather than letting an unexpected mismatch 500 a
            # class-teacher-assigments request — the section still gets
            # its group the next time this endpoint (or a retry) runs.
            logger.warning(
                "create_section_group() unexpectedly rejected staff %s as "
                "class-teacher for section %s right after assigning them.",
                assigments.staff_id, assigments.section_id,
            )


class SubjectTeacherassigmentsViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    Create leaves `status=PENDING` (model default) — the class-teacher
    of that section approves/rejects via the two actions below (design
    doc §2's "class-teacher subject-teacher ko allow karega" flow).
    Anyone who can see the section can request; only that section's
    `ClassTeacherassigments` holder (or a campus admin/principal, as a
    fallback for when no class-teacher is assigned yet) can decide.
    """
    audit_prefix = "subject_teacher"
    serializer_class = SubjectTeacherassigmentsSerializer
    campus_field_path = "section__school_class__campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        return Q(section_id__in=vis.visible_section_ids, status=SubjectTeacherassigments.Status.APPROVED)
    # Overridden below: creating a request needs no special role (any
    # campus member can ask to teach a subject), only approve/reject do.
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(SubjectTeacherassigments.objects.select_related("staff__user", "subject"), self.request)  # [PERF] staff_detail + subject_detail

    def perform_create(self, serializer):
        """[T4 §C] Request-approval flow is unchanged (PENDING). NEW: a campus
        admin/principal may pass `direct=true` to assign a teacher straight
        away (APPROVED, approval skipped). A department-limited HOD can only do
        this inside their own department."""
        section = serializer.validated_data["section"]
        campus_id = section.school_class.campus_id
        user = self.request.user
        if _truthy(self.request.data.get("direct")) and is_campus_admin_or_principal(user, campus_id):
            hod_dept = hod_department_id(user, campus_id)
            if hod_dept and section.school_class.department_id != hod_dept:
                raise PermissionDenied("This section is outside your department.")
            actor_staff = StaffProfile.objects.filter(campus_id=campus_id, user=user, is_active=True).first()
            obj = serializer.save(
                status=SubjectTeacherassigments.Status.APPROVED, approved_by=actor_staff, responded_at=timezone.now()
            )
            bridge.notify(
                users=[obj.staff.user], notif_type=NotifTypes.STAFF_assigments_APPROVED,
                title="Subject assigned", body=f"You've been assigned {obj.subject.name} for {obj.section}.",
            )
            bridge.sync_section_group(obj.section)
            self._audit("direct_assign", obj)
            return
        self._audit("request", serializer.save())

    def _can_decide(self, user, assigments):
        if is_class_teacher_of_section(user, assigments.section_id):
            return True
        return is_campus_admin_or_principal(user, assigments.section.school_class.campus_id)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        assigments = self.get_object()
        if not self._can_decide(request.user, assigments):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        deciding_staff = StaffProfile.objects.filter(
            campus_id=assigments.section.school_class.campus_id, user=request.user, is_active=True
        ).first()
        assigments.status = SubjectTeacherassigments.Status.APPROVED
        assigments.approved_by = deciding_staff
        assigments.responded_at = timezone.now()
        assigments.save(update_fields=["status", "approved_by", "responded_at", "updated_at"])
        self._audit("approve", assigments)
        bridge.sync_section_group(assigments.section)
        bridge.notify(
            users=[assigments.staff.user],
            notif_type=NotifTypes.STAFF_assigments_APPROVED,
            title="Subject assigments approved",
            body=f"You're approved to teach {assigments.subject.name} for {assigments.section}.",
        )
        return Response(self.get_serializer(assigments).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        assigments = self.get_object()
        if not self._can_decide(request.user, assigments):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        deciding_staff = StaffProfile.objects.filter(
            campus_id=assigments.section.school_class.campus_id, user=request.user, is_active=True
        ).first()
        assigments.status = SubjectTeacherassigments.Status.REJECTED
        assigments.approved_by = deciding_staff
        assigments.responded_at = timezone.now()
        assigments.save(update_fields=["status", "approved_by", "responded_at", "updated_at"])
        self._audit("reject", assigments)
        bridge.notify(
            users=[assigments.staff.user],
            notif_type=NotifTypes.STAFF_assigments_REJECTED,
            title="Subject assigments rejected",
            body=f"Your request to teach {assigments.subject.name} for {assigments.section} was rejected.",
        )
        return Response(self.get_serializer(assigments).data)
