# campus/views.py
import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# FEE-2: fee is now paid out of the same wallet liveclass already uses,
# not a separate gateway — user_profile owns CoinLedger/record_transaction,
# campus only ever calls through it, never writes to CoinLedger directly
# (same boundary user_profile/models.py's own CoinLedger docstring lays out).
from user_profile.models import CoinLedger

# [Task 11] `AssignmentViewSet`/`AssignmentSubmissionViewSet` below are now
# thin proxies over the unified `assignment` app (see `campus.Assignment`'s
# own [DEPRECATED] docstring in models.py) rather than campus's own
# deprecated Assignment/AssignmentSubmission models — imported directly at
# module level, same reasoning as `campus.bridge`'s own Task 11 addition
# docstring gives: `assignment` is a confirmed, fully-built sibling app,
# not an unverified dependency that needs a lazy-import degrade. Aliased
# ("Unified...") so a reader never confuses these with campus's own,
# now-deprecated `Assignment`/`AssignmentSubmission` models — this file no
# longer imports those at all, since nothing here touches them any more;
# the one remaining reader of the old rows is the one-time
# `migrate_campus_assignments_to_unified` management command.
from assignment.models import Assignment as UnifiedAssignment
from assignment.models import AssignmentSource as UnifiedAssignmentSource
from assignment.models import AssignmentSubmission as UnifiedAssignmentSubmission

from . import bridge
from .bridge import NotifTypes
from .services import compute_attendance_summary, generate_report_card_data
# B-4 fix — see campus/throttles.py module docstring for why these are
# separate classes (each with its own fixed `scope`) rather than a
# shared `throttle_scope` attribute: several of them apply to different
# @action methods living on the same ViewSet.
from .throttles import (
    CampusFeePaymentThrottle,
    CampusLiveSessionJoinThrottle,
    CampusNoticePostThrottle,
    CampusParentLinkVerifyThrottle,
)
from .models import (
    AcademicSession,
    Attendance,
    Campus,
    CampusAnalyticsSnapshot,
    CampusLiveSession,
    CampusParentLink,
    ClassTeacherAssignment,
    Department,
    DigitalIDCard,
    ExamTerm,
    FeeInvoice,
    FeePayment,
    FeeStructure,
    Notice,
    ResultEntry,
    Room,
    SchoolClass,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
    SubjectTeacherAssignment,
    SyllabusProgress,
    SyllabusUnit,
    TimeSlot,
    TimetableEntry,
)
from .permissions import (
    IsCampusAdminOrPrincipal,
    IsPlatformAdmin,
    IsSectionSubjectStaffOrReadOnly,
    can_manage_section_subject,
    can_post_notice,
    is_any_active_staff,
    is_campus_admin_or_principal,
    is_campus_approved,
    is_class_teacher_of_section,
    is_linked_parent_of_student,
)
from .serializers import (
    AcademicSessionSerializer,
    AttendanceSerializer,
    CampusAnalyticsSnapshotSerializer,
    CampusLiveSessionSerializer,
    CampusParentLinkSerializer,
    CampusSerializer,
    ClassTeacherAssignmentSerializer,
    DepartmentSerializer,
    DigitalIDCardSerializer,
    ExamTermSerializer,
    FeeInvoiceSerializer,
    FeePaymentSerializer,
    FeeStructureSerializer,
    NoticeSerializer,
    ResultEntrySerializer,
    RoomSerializer,
    SchoolClassSerializer,
    SectionSerializer,
    StaffProfileSerializer,
    SyllabusProgressSerializer,
    SyllabusUnitSerializer,
    TimeSlotSerializer,
    TimetableEntrySerializer,
    StudentEnrollmentSerializer,
    SubjectSerializer,
    SubjectTeacherAssignmentSerializer,
)


def get_my_campus_ids(user):
    """
    Every campus a user has *some* legitimate reason to see rows from:
    as active staff, as an enrolled student, or as a linked parent
    (design doc §11 — Parent gets read-only access to a linked
    child's campus data). Centralized here so every viewset's
    campus-visibility check — including `CampusViewSet` itself — stays
    in sync as new membership routes (e.g. parent links) get added.
    """
    return set(
        StaffProfile.objects.filter(user=user, is_active=True).values_list("campus_id", flat=True)
    ) | set(
        StudentEnrollment.objects.filter(student=user).values_list(
            "section__school_class__campus_id", flat=True
        )
    ) | set(
        CampusParentLink.objects.filter(parent=user).values_list("campus_id", flat=True)
    )


class CampusMemberScopedMixin:
    """
    Shared "which campus does this row belong to" + "am I a member of
    that campus" plumbing for every viewset below. `campus_field_path`
    is a Django `__`-lookup from the model to its `Campus` FK (e.g.
    `''` for `Campus` itself, `'campus'` for a direct FK, `'school_
    class__campus'` for something scoped through `SchoolClass`).
    """
    campus_field_path = "campus"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_campus_id_for_permission_check(self, request):
        # POST/PATCH bodies carry the FK by whatever field the
        # serializer exposes as top-level `campus` (already true for
        # every model here except the ones scoped through `section`/
        # `school_class`, which override this method below), GET/
        # detail routes fall back to the object's own campus.
        campus_id = request.data.get("campus") or request.query_params.get("campus")
        if campus_id:
            return campus_id
        obj_id = self.kwargs.get("pk")
        if obj_id:
            obj = self.get_queryset().filter(pk=obj_id).first()
            if obj:
                return self._campus_id_from_instance(obj)
        return None

    def _campus_id_from_instance(self, obj):
        target = obj
        for part in self.campus_field_path.split("__"):
            target = getattr(target, part)
        return target.id

    def filter_queryset_to_my_campuses(self, qs, request):
        campus_id = request.query_params.get("campus")
        if campus_id:
            qs = qs.filter(**{f"{self.campus_field_path}__id": campus_id})
        return qs.filter(**{f"{self.campus_field_path}__id__in": get_my_campus_ids(request.user)})


class CampusViewSet(viewsets.ModelViewSet):
    """
    POST creates a new campus — still free/self-serve, and still
    auto-provisions the creator as that campus's ADMIN `StaffProfile`
    in the same transaction, so "create a campus" always leaves you
    able to manage it (no separate "make myself admin" step). What
    G-2 changes: the campus is created with `verification_status=
    PENDING` (the model default) rather than being implicitly
    "verified" just by existing — see `Campus.VerificationStatus`'s
    docstring and `permissions.is_campus_approved` for exactly what
    that does and doesn't restrict while pending. A platform admin
    (`permissions.IsPlatformAdmin`) moves it to APPROVED/REJECTED via
    the `approve`/`reject` actions below.
    """
    serializer_class = CampusSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Campus.objects.filter(id__in=get_my_campus_ids(self.request.user)).distinct()

    def get_permissions(self):
        if self.action in ("approve", "reject"):
            return [IsAuthenticated(), IsPlatformAdmin()]
        return super().get_permissions()

    @transaction.atomic
    def perform_create(self, serializer):
        campus = serializer.save(created_by=self.request.user)
        StaffProfile.objects.create(campus=campus, user=self.request.user, role=StaffProfile.Role.ADMIN)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """G-2 fix — platform-admin-only. `get_queryset` already scopes
        to the requester's own campuses, which would hide a pending
        campus from anyone but its creator; a platform admin needs to
        see and act on ANY campus regardless of membership, so this
        looks the campus up directly rather than via `get_object()`."""
        campus = Campus.objects.filter(pk=pk).first()
        if campus is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        campus.verification_status = Campus.VerificationStatus.APPROVED
        campus.verified_by = request.user
        campus.verified_at = timezone.now()
        campus.save(update_fields=["verification_status", "verified_by", "verified_at"])
        return Response(self.get_serializer(campus).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """G-2 fix — platform-admin-only. See `approve` above for why
        this bypasses `get_object()`."""
        campus = Campus.objects.filter(pk=pk).first()
        if campus is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        campus.verification_status = Campus.VerificationStatus.REJECTED
        campus.verified_by = request.user
        campus.verified_at = timezone.now()
        campus.save(update_fields=["verification_status", "verified_by", "verified_at"])
        return Response(self.get_serializer(campus).data)


class AcademicSessionViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
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
        from . import tasks

        new_session = self.get_object()
        if not is_campus_admin_or_principal(request.user, new_session.campus_id):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        try:
            async_result = tasks.rollover_session.delay(new_session.campus_id, new_session.id)
            return Response({"detail": "Rollover queued.", "task_id": async_result.id}, status=status.HTTP_202_ACCEPTED)
        except Exception:
            result = tasks.rollover_session(new_session.campus_id, new_session.id)
            return Response(result)


class DepartmentViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = DepartmentSerializer
    campus_field_path = "campus"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Department.objects.all(), self.request)


class SchoolClassViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = SchoolClassSerializer
    campus_field_path = "campus"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(SchoolClass.objects.all(), self.request)


class SectionViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = SectionSerializer
    campus_field_path = "school_class__campus"

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
        # TASK (design doc §3) — section-group auto-creation. Routed
        # through `campus.bridge` (never a direct `message` import) —
        # see that module's docstring for why this currently no-ops
        # with a logged warning until `core.classroom_chat_bridge`
        # actually exists.
        bridge.create_section_group(section, actor=self.request.user)


class SubjectViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = SubjectSerializer
    campus_field_path = "campus"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Subject.objects.all(), self.request)


class RoomViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = RoomSerializer
    campus_field_path = "campus"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Room.objects.all(), self.request)


class StaffProfileViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
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
    serializer_class = StaffProfileSerializer
    campus_field_path = "campus"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(StaffProfile.objects.all(), self.request)

    def perform_create(self, serializer):
        campus = serializer.validated_data["campus"]
        if not is_campus_approved(campus.id):
            raise PermissionDenied("This campus is pending platform verification and can't add staff yet.")
        serializer.save()


class ClassTeacherAssignmentViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = ClassTeacherAssignmentSerializer
    campus_field_path = "section__school_class__campus"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(ClassTeacherAssignment.objects.all(), self.request)

    def get_campus_id_for_permission_check(self, request):
        section_id = request.data.get("section")
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)


class SubjectTeacherAssignmentViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    Create leaves `status=PENDING` (model default) — the class-teacher
    of that section approves/rejects via the two actions below (design
    doc §2's "class-teacher subject-teacher ko allow karega" flow).
    Anyone who can see the section can request; only that section's
    `ClassTeacherAssignment` holder (or a campus admin/principal, as a
    fallback for when no class-teacher is assigned yet) can decide.
    """
    serializer_class = SubjectTeacherAssignmentSerializer
    campus_field_path = "section__school_class__campus"
    # Overridden below: creating a request needs no special role (any
    # campus member can ask to teach a subject), only approve/reject do.
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(SubjectTeacherAssignment.objects.all(), self.request)

    def _can_decide(self, user, assignment):
        if is_class_teacher_of_section(user, assignment.section_id):
            return True
        return is_campus_admin_or_principal(user, assignment.section.school_class.campus_id)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        assignment = self.get_object()
        if not self._can_decide(request.user, assignment):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        deciding_staff = StaffProfile.objects.filter(
            campus_id=assignment.section.school_class.campus_id, user=request.user, is_active=True
        ).first()
        assignment.status = SubjectTeacherAssignment.Status.APPROVED
        assignment.approved_by = deciding_staff
        assignment.responded_at = timezone.now()
        assignment.save(update_fields=["status", "approved_by", "responded_at", "updated_at"])
        bridge.notify(
            users=[assignment.staff.user],
            notif_type=NotifTypes.STAFF_ASSIGNMENT_APPROVED,
            title="Subject assignment approved",
            body=f"You're approved to teach {assignment.subject.name} for {assignment.section}.",
        )
        return Response(self.get_serializer(assignment).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        assignment = self.get_object()
        if not self._can_decide(request.user, assignment):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        deciding_staff = StaffProfile.objects.filter(
            campus_id=assignment.section.school_class.campus_id, user=request.user, is_active=True
        ).first()
        assignment.status = SubjectTeacherAssignment.Status.REJECTED
        assignment.approved_by = deciding_staff
        assignment.responded_at = timezone.now()
        assignment.save(update_fields=["status", "approved_by", "responded_at", "updated_at"])
        bridge.notify(
            users=[assignment.staff.user],
            notif_type=NotifTypes.STAFF_ASSIGNMENT_REJECTED,
            title="Subject assignment rejected",
            body=f"Your request to teach {assignment.subject.name} for {assignment.section} was rejected.",
        )
        return Response(self.get_serializer(assignment).data)


class StudentEnrollmentViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    G-2 fix: enrolling a student is a "pull someone else into this
    campus" action, same reasoning as `StaffProfileViewSet` above — it
    additionally requires the section's campus to be
    `verification_status=APPROVED` (see `permissions.is_campus_approved`).
    """
    serializer_class = StudentEnrollmentSerializer
    campus_field_path = "section__school_class__campus"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(StudentEnrollment.objects.all(), self.request)

    def get_campus_id_for_permission_check(self, request):
        section_id = request.data.get("section")
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)

    def perform_create(self, serializer):
        section = serializer.validated_data["section"]
        if not is_campus_approved(section.school_class.campus_id):
            raise PermissionDenied("This campus is pending platform verification and can't enroll students yet.")
        serializer.save()


class NoticeViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    G-1 fix: who may post at a given scope is now decided by
    `permissions.can_post_notice` (see its docstring) instead of "any
    active staff, any scope" — campus admin/principal-HOD can post at
    any scope; a class-teacher can post ONLY to their own section;
    everyone else (subject-teacher, non-teaching staff) can't post a
    notice at all yet, since they don't own a notice-scope of their
    own.
    """
    serializer_class = NoticeSerializer
    campus_field_path = "campus"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Notice.objects.all(), self.request)

    def get_throttles(self):
        # B-4 fix — only the abuse-prone action (posting, which fans
        # out to a whole campus/section) gets the scoped limit; list/
        # retrieve/update/delete keep the project-wide default
        # (UserRateThrottle/AnonRateThrottle from DEFAULT_THROTTLE_CLASSES).
        if self.action == "create":
            return [CampusNoticePostThrottle()]
        return super().get_throttles()

    def perform_create(self, serializer):
        campus = serializer.validated_data["campus"]
        department = serializer.validated_data.get("department")
        school_class = serializer.validated_data.get("school_class")
        section = serializer.validated_data.get("section")
        if not can_post_notice(
            self.request.user,
            campus.id,
            department_id=department.id if department else None,
            school_class_id=school_class.id if school_class else None,
            section_id=section.id if section else None,
        ):
            raise PermissionDenied("You're not allowed to post a notice at this scope.")
        serializer.save(posted_by=self.request.user)


# ============================================================
# Phase 4 — live classes (coin-free)
# ============================================================
class CampusLiveSessionViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    Scheduling auto-fires a `Notice` + `core.Notification`
    (`CAMPUS_SESSION_SCHEDULED`) to the section, and attempts video-room
    provisioning through `bridge.provision_video_room` — both go through
    `campus.bridge`, never a direct `core`/`message`/`liveclass` import
    (design doc §4). `status`/`room_id` stay server-controlled; a
    teacher moves the session forward via the `start`/`end` actions
    below rather than PATCHing those fields directly.
    """
    serializer_class = CampusLiveSessionSerializer
    campus_field_path = "section__school_class__campus"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(CampusLiveSession.objects.all(), self.request)

    def get_section_subject_for_permission_check(self, request):
        section_id = request.data.get("section")
        subject_id = request.data.get("subject")
        if not section_id and self.kwargs.get("pk"):
            obj = self.get_queryset().filter(pk=self.kwargs["pk"]).first()
            if obj:
                return obj.section.school_class.campus_id, obj.section_id, obj.subject_id
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id, section.id, subject_id
        return None, None, None

    @transaction.atomic
    def perform_create(self, serializer):
        live_session = serializer.save()
        campus = live_session.section.school_class.campus
        room_id = bridge.provision_video_room(live_session, actor=self.request.user)
        if room_id:
            live_session.room_id = room_id
            live_session.save(update_fields=["room_id"])
        recipients = StudentEnrollment.objects.filter(
            section=live_session.section, status=StudentEnrollment.Status.ACTIVE
        ).values_list("student", flat=True)
        Notice.objects.create(
            campus=campus,
            section=live_session.section,
            session=getattr(live_session.section.school_class, "session", None),
            posted_by=self.request.user,
            title=f"Class scheduled: {live_session.subject.name}",
            body=f"A live session for {live_session.subject.name} is scheduled at {live_session.scheduled_at}.",
        )
        bridge.notify(
            users=list(recipients),
            notif_type=NotifTypes.CAMPUS_SESSION_SCHEDULED,
            title="Class scheduled",
            body=f"{live_session.subject.name} scheduled at {live_session.scheduled_at}.",
        )

    # B-4 fix — see CampusLiveSessionJoinThrottle's docstring in
    # throttles.py for why `start` (not a separate join endpoint, which
    # this app doesn't have) is the one scoped here: it's what fires
    # the CAMPUS_SESSION_LIVE notification fan-out below.
    @action(detail=True, methods=["post"], throttle_classes=[CampusLiveSessionJoinThrottle])
    def start(self, request, pk=None):
        live_session = self.get_object()
        if live_session.status != CampusLiveSession.Status.SCHEDULED:
            return Response({"detail": "Only a scheduled session can be started."}, status=status.HTTP_400_BAD_REQUEST)
        live_session.status = CampusLiveSession.Status.LIVE
        live_session.save(update_fields=["status"])
        recipients = StudentEnrollment.objects.filter(
            section=live_session.section, status=StudentEnrollment.Status.ACTIVE
        ).values_list("student", flat=True)
        bridge.notify(
            users=list(recipients),
            notif_type=NotifTypes.CAMPUS_SESSION_LIVE,
            title="Class is live",
            body=f"{live_session.subject.name} is live now.",
        )
        return Response(self.get_serializer(live_session).data)

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        live_session = self.get_object()
        live_session.status = CampusLiveSession.Status.ENDED
        live_session.save(update_fields=["status"])
        return Response(self.get_serializer(live_session).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        live_session = self.get_object()
        live_session.status = CampusLiveSession.Status.CANCELLED
        live_session.save(update_fields=["status"])
        return Response(self.get_serializer(live_session).data)


# ============================================================
# Phase 5 — timetable & attendance
# ============================================================
class TimeSlotViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = TimeSlotSerializer
    campus_field_path = "campus"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(TimeSlot.objects.all(), self.request)


class TimetableEntryViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    Clash-detection is enforced in `TimetableEntry.clean()`/`save()` at
    the model layer (design doc §5) — the serializer's
    `DjangoCleanValidationMixin` turns that into a normal 400 here.
    """
    serializer_class = TimetableEntrySerializer
    campus_field_path = "section__school_class__campus"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(TimetableEntry.objects.all(), self.request)

    def get_campus_id_for_permission_check(self, request):
        section_id = request.data.get("section")
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)


class AttendanceViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    `subject=None` means a daily (not period-wise) mark, which only a
    class-teacher/admin can take; a subject-specific mark additionally
    allows that section+subject's approved subject-teacher (design doc
    §5/§11). `marked_by` is always the requesting user, never
    client-supplied.
    """
    serializer_class = AttendanceSerializer
    campus_field_path = "enrollment__section__school_class__campus"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Attendance.objects.all(), self.request)

    def get_section_subject_for_permission_check(self, request):
        enrollment_id = request.data.get("enrollment")
        subject_id = request.data.get("subject")
        if not enrollment_id and self.kwargs.get("pk"):
            obj = self.get_queryset().filter(pk=self.kwargs["pk"]).first()
            if obj:
                return obj.enrollment.section.school_class.campus_id, obj.enrollment.section_id, obj.subject_id
        if enrollment_id:
            enrollment = StudentEnrollment.objects.filter(pk=enrollment_id).first()
            if enrollment:
                return enrollment.section.school_class.campus_id, enrollment.section_id, subject_id
        return None, None, None

    def perform_create(self, serializer):
        serializer.save(marked_by=self.request.user)

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """`?enrollment=<id>&subject=<id optional>` -> computed %-age,
        the `compute_attendance_summary` service function (§5) —
        recomputed on demand, never stored."""
        enrollment_id = request.query_params.get("enrollment")
        if not enrollment_id:
            return Response({"detail": "enrollment query param is required."}, status=status.HTTP_400_BAD_REQUEST)
        enrollment = StudentEnrollment.objects.filter(pk=enrollment_id).first()
        if not enrollment or not is_any_active_staff(
            request.user, enrollment.section.school_class.campus_id
        ) and enrollment.student_id != request.user.id and not is_linked_parent_of_student(
            request.user, enrollment.student_id
        ):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        subject_id = request.query_params.get("subject")
        subject = Subject.objects.filter(pk=subject_id).first() if subject_id else None
        return Response(compute_attendance_summary(enrollment, subject=subject))


# ============================================================
# Phase 6 — assignments & syllabus
# ============================================================
def _my_section_ids_and_campus_map(user):
    """[Task 11] Every `Section` id this user has some legitimate reason
    to see campus assignments for (enrolled student, active staff, or
    linked parent — via `get_my_campus_ids()`'s own membership
    reasoning), plus a `{section_id: campus_id}` lookup for permission
    checks. Deliberately campus-wide, not narrowed to the user's own
    section(s) — this is a faithful port of the OLD `AssignmentViewSet.
    get_queryset()`'s actual scoping (`campus_field_path =
    "section__school_class__campus"`, filtered only by
    `campus_id__in=get_my_campus_ids(user)`), which already showed a
    student every section's assignments within their campus, not just
    their own section's. Not a new, broader grant introduced by this
    proxy.
    """
    campus_ids = get_my_campus_ids(user)
    sections = Section.objects.filter(school_class__campus_id__in=campus_ids).select_related("school_class")
    return (
        set(sections.values_list("id", flat=True)),
        {s.id: s.school_class.campus_id for s in sections},
    )


def _section_for_assignment(assignment):
    """[Task 11] `assignment.context_id` IS a `Section.id` for every
    `source="campus"` unified Assignment (see `campus.bridge.
    create_assignment()`) — this app never stores a real FK back to
    `Section` (golden rule, §1), so this is the one place that opaque id
    gets turned back into a real row, same posture `assignment.bridge`
    itself takes toward never doing this resolution on its own side.
    """
    return Section.objects.filter(pk=assignment.context_id).select_related("school_class").first()


def _serialize_campus_assignment(assignment):
    """[Task 11] Reconstructs the OLD `campus.Assignment` API shape
    (`id, section, subject, posted_by, title, description, attachment,
    due_date, session`) from a NEW `assignment.models.Assignment`
    instance, so `AssignmentViewSet`'s response keys stay
    frontend-compatible even though the backing model changed entirely
    (Task 11 acceptance: same JSON keys).

    - `section` = `assignment.context_id` directly.
    - `subject` = `assignment.data.get("subject_id")` — see
      `create_context_assignment()`'s `extra_data` parameter docstring
      (assignment/bridge.py) for why this couldn't be a real FK on the
      unified model.
    - `session` = derived fresh from the section's own
      `school_class.session_id` rather than stored anywhere on the
      unified model — a Section's session doesn't change after the
      fact, so this is always correct and avoids keeping a second,
      potentially-stale copy of a value `Section` already has.
    - `posted_by` = the `StaffProfile.id` for `assignment.posted_by` at
      this campus, looked up fresh — the unified model only stores the
      underlying `login.User`, not the campus-scoped `StaffProfile` row
      the old API exposed. [FLAGGED, not a bug]: if that staff member's
      `StaffProfile` for this campus is later deactivated, this now
      returns `None` where the old, permanently-stored FK would have
      kept returning the same id — a real, deliberate behavior
      difference from before.
    """
    section = _section_for_assignment(assignment)
    campus_id = section.school_class.campus_id if section else None
    posted_by_staff_id = None
    if assignment.posted_by_id and campus_id:
        posted_by_staff_id = (
            StaffProfile.objects.filter(user_id=assignment.posted_by_id, campus_id=campus_id, is_active=True)
            .values_list("id", flat=True)
            .first()
        )
    return {
        "id": assignment.id,
        "section": assignment.context_id,
        "subject": assignment.data.get("subject_id"),
        "posted_by": posted_by_staff_id,
        "title": assignment.title,
        "description": assignment.description,
        "attachment": assignment.attachment.url if assignment.attachment else None,
        "due_date": assignment.due_date,
        "session": section.school_class.session_id if section else None,
    }


def _serialize_campus_submission(submission):
    """[Task 11] Reconstructs the OLD `campus.AssignmentSubmission` API
    shape (`id, assignment, student, submitted_at, file, status, grade,
    feedback`) from a NEW `assignment.models.AssignmentSubmission`
    instance.

    `status` string values are used as-is — "submitted"/"late"/"missing"
    match the old 3-state model's spellings exactly. The unified model
    adds `"checked"`/`"partially_checked"`, states the old model never
    produced (grading there only ever set `grade`/`feedback` directly,
    never moved `status` again after submit). Keys are unchanged
    (satisfies the "same JSON keys" acceptance criterion); the *value*
    can now include those two new strings — a genuine, flagged behavior
    difference, not silently collapsed back into the old lossy 3-state
    enum, since that would throw away real grading-status information
    the new `grade_freeform()` flow now tracks.
    """
    return {
        "id": submission.id,
        "assignment": submission.assignment_id,
        "student": submission.student_id,
        "submitted_at": submission.submitted_at,
        "file": submission.file.url if submission.file else None,
        "status": submission.status,
        "grade": submission.grade,
        "feedback": submission.feedback,
    }


class AssignmentViewSet(viewsets.ViewSet):
    """[Task 11] Thin proxy over the unified `assignment` app —
    `campus.Assignment` (this app's own model) is deprecated (see its
    docstring in models.py); every assignment now actually lives on
    `assignment.models.Assignment` with `source="campus"`,
    `context_type="section"`, `context_id=<Section.id>`.

    Deliberately NOT a `ModelViewSet` (nor built on
    `CampusMemberScopedMixin`, whose `filter_queryset_to_my_campuses`/
    `_campus_id_from_instance` both assume a real Django FK chain from
    the model to `Campus` — `context_id` is an opaque `UUIDField`, not
    an FK, so that traversal can't work here) — list/retrieve/create are
    implemented directly against the unified model instead, with the
    OLD response shape (`id, section, subject, posted_by, title,
    description, attachment, due_date, session`) reconstructed by
    `_serialize_campus_assignment()` so existing frontend code keeps
    working unchanged (Task 11 acceptance: same JSON keys).

    update/partial_update/destroy are NOT implemented in this pass —
    the old `ModelViewSet` allowed arbitrary field PATCHes on a posted
    assignment, but the unified model's mutation surface is
    explicit-method-based (e.g. `has_structured_questions` immutability
    once a submission exists) and no design doc input covered what
    "edit a posted campus assignment" should mean against that surface.
    Flagged as an open item rather than guessed at, same as this
    codebase's established convention for genuine gaps (see e.g.
    `campus/bridge.py`'s own STATUS section).
    """

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        return [permission() for permission in self.permission_classes]

    def list(self, request):
        section_ids, _ = _my_section_ids_and_campus_map(request.user)
        qs = UnifiedAssignment.objects.filter(
            source=UnifiedAssignmentSource.CAMPUS, context_type="section", context_id__in=section_ids
        ).order_by("-due_date")
        section_filter = request.query_params.get("section")
        if section_filter:
            qs = qs.filter(context_id=section_filter)
        return Response([_serialize_campus_assignment(a) for a in qs])

    def retrieve(self, request, pk=None):
        assignment = get_object_or_404(UnifiedAssignment.objects.filter(source=UnifiedAssignmentSource.CAMPUS), pk=pk)
        section_ids, _ = _my_section_ids_and_campus_map(request.user)
        if assignment.context_id not in section_ids:
            raise PermissionDenied("You don't have access to this assignment.")
        return Response(_serialize_campus_assignment(assignment))

    @transaction.atomic
    def create(self, request):
        section_id = request.data.get("section")
        subject_id = request.data.get("subject")
        title = request.data.get("title")
        raw_due_date = request.data.get("due_date")
        if not (section_id and subject_id and title and raw_due_date):
            return Response(
                {"detail": "section, subject, title and due_date are all required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        due_date = parse_date(raw_due_date) if isinstance(raw_due_date, str) else raw_due_date
        if due_date is None:
            return Response({"due_date": "Must be a valid ISO date."}, status=status.HTTP_400_BAD_REQUEST)

        section = get_object_or_404(Section.objects.select_related("school_class"), pk=section_id)
        subject = get_object_or_404(Subject, pk=subject_id)
        campus_id = section.school_class.campus_id
        # [ASSUMPTION — NOT VERIFIED] mirrors the old `IsSectionSubjectStaffOrReadOnly`
        # permission class's likely check (its own source wasn't part of
        # this pass's upload) using the one already-available helper with
        # a matching name/shape — `can_manage_section_subject` is already
        # used for the equivalent grading-permission check further down
        # in `AssignmentSubmissionViewSet`.
        if not can_manage_section_subject(request.user, campus_id, section.id, subject_id):
            raise PermissionDenied("Only that section/subject's staff can post an assignment.")

        assignment = bridge.create_assignment(
            section=section,
            subject=subject,
            posted_by=request.user,
            title=title,
            description=request.data.get("description", ""),
            attachment=request.FILES.get("attachment"),
            due_date=due_date,
        )
        recipients = StudentEnrollment.objects.filter(section=section, status=StudentEnrollment.Status.ACTIVE)
        Notice.objects.create(
            campus=section.school_class.campus,
            section=section,
            session=section.school_class.session,
            posted_by=request.user,
            title=f"New assignment: {assignment.title}",
            body=f"Due {assignment.due_date}.",
        )
        bridge.notify(
            users=[e.student for e in recipients],
            notif_type=NotifTypes.ASSIGNMENT_POSTED_CAMPUS,
            title="New assignment posted",
            body=assignment.title,
        )
        return Response(_serialize_campus_assignment(assignment), status=status.HTTP_201_CREATED)


class AssignmentSubmissionViewSet(viewsets.ViewSet):
    """[Task 11] Thin proxy over `assignment.models.AssignmentSubmission`
    — see `AssignmentViewSet`'s own docstring above for why this isn't a
    `ModelViewSet`/`CampusMemberScopedMixin` subclass any more, and why
    `_serialize_campus_submission()` exists (old JSON shape:
    `id, assignment, student, submitted_at, file, status, grade,
    feedback` — preserved even though `status` can now additionally be
    `"checked"`/`"partially_checked"`, values the old 3-state model never
    produced; see that function's own docstring).

    Roster pre-create (bulk `MISSING` rows) behavior is unchanged — it
    still happens inside `assignment.bridge.create_context_assignment()`
    itself (called via `campus.bridge.create_assignment()` from
    `AssignmentViewSet.create()` above), not duplicated here.
    """

    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        return [permission() for permission in self.permission_classes]

    def _get_scoped_submission(self, request, pk):
        """Mirrors the old `get_object()`'s own reasoning verbatim: looked
        up unrestricted, gated by an explicit `PermissionDenied` (403,
        "you can't do this") rather than a queryset-filtered 404 ("this
        doesn't exist") for someone with no relationship to the row at
        all."""
        submission = get_object_or_404(UnifiedAssignmentSubmission.objects.select_related("assignment"), pk=pk)
        section = _section_for_assignment(submission.assignment)
        campus_id = section.school_class.campus_id if section else None
        user = request.user
        if submission.student_id != user.id and not (campus_id and is_any_active_staff(user, campus_id)):
            raise PermissionDenied("You don't have access to this submission.")
        return submission, section

    def list(self, request):
        section_ids, campus_by_section = _my_section_ids_and_campus_map(request.user)
        staff_campus_ids = set(
            StaffProfile.objects.filter(user=request.user, is_active=True).values_list("campus_id", flat=True)
        )
        qs = UnifiedAssignmentSubmission.objects.filter(
            assignment__source=UnifiedAssignmentSource.CAMPUS,
            assignment__context_type="section",
            assignment__context_id__in=section_ids,
        ).select_related("assignment")
        # Same breadth the old campus-wide (not section-scoped) filter had:
        # a student sees only their own rows; staff at the relevant campus
        # see every student's row for any section in that campus.
        rows = [
            s for s in qs
            if s.student_id == request.user.id or campus_by_section.get(s.assignment.context_id) in staff_campus_ids
        ]
        return Response([_serialize_campus_submission(s) for s in rows])

    def retrieve(self, request, pk=None):
        submission, _section = self._get_scoped_submission(request, pk)
        return Response(_serialize_campus_submission(submission))

    def create(self, request):
        """Edge case only — normal case is the bulk MISSING pre-create in
        `AssignmentViewSet.create()`. Covers a student enrolled *after*
        an assignment was already posted, who therefore has no
        pre-created row yet. Written directly against the unified model
        (not through `assignment`'s own public-API serializer/viewset,
        whose `validate_assignment()` rejects `create()` for any
        non-personal-source assignment) — this is the same kind of
        trusted, internal bridge-style write `assignment.bridge.
        create_context_assignment()` itself already makes, not a way
        around that public-API restriction.
        """
        assignment_id = request.data.get("assignment")
        assignment = get_object_or_404(
            UnifiedAssignment.objects.filter(source=UnifiedAssignmentSource.CAMPUS), pk=assignment_id
        )
        section = _section_for_assignment(assignment)
        if section is None:
            raise PermissionDenied("This assignment's section could not be resolved.")
        enrollment = StudentEnrollment.objects.filter(
            student=request.user, section=section, status=StudentEnrollment.Status.ACTIVE
        ).first()
        if enrollment is None:
            raise PermissionDenied("You can only create your own submission, for a section you're enrolled in.")
        submission, created = UnifiedAssignmentSubmission.objects.get_or_create(
            assignment=assignment,
            student=request.user,
            defaults={"roll_number": enrollment.roll_number, "enrollment_no": enrollment.enrollment_no},
        )
        return Response(
            _serialize_campus_submission(submission),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def partial_update(self, request, pk=None):
        submission, section = self._get_scoped_submission(request, pk)
        if submission.student_id == request.user.id:
            submission.submit_freeform(file=request.FILES.get("file"))
            return Response(_serialize_campus_submission(submission))

        campus_id = section.school_class.campus_id if section else None
        subject_id = submission.assignment.data.get("subject_id")
        if campus_id is None or not can_manage_section_subject(request.user, campus_id, section.id, subject_id):
            raise PermissionDenied("Only the student or their subject teacher/admin can update this submission.")
        submission.grade_freeform(grade=request.data.get("grade", ""), feedback=request.data.get("feedback", ""))
        return Response(_serialize_campus_submission(submission))


# ============================================================
# Phase 6 — syllabus tracker
# ============================================================
class SyllabusUnitViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = SyllabusUnitSerializer
    campus_field_path = "section__school_class__campus"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(SyllabusUnit.objects.all(), self.request)

    def get_section_subject_for_permission_check(self, request):
        section_id = request.data.get("section")
        subject_id = request.data.get("subject")
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id, section.id, subject_id
        return None, None, None

    def perform_create(self, serializer):
        unit = serializer.save()
        SyllabusProgress.objects.get_or_create(syllabus_unit=unit)


class SyllabusProgressViewSet(CampusMemberScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read + the `mark-covered` action only — there is no generic
    create/update/delete here, `SyllabusProgress` rows are created
    implicitly (a `SyllabusUnit`'s progress row should be provisioned
    when the unit is created; see `SyllabusUnitViewSet.perform_create`)
    and only ever change via `mark_covered`."""
    serializer_class = SyllabusProgressSerializer
    campus_field_path = "syllabus_unit__section__school_class__campus"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(SyllabusProgress.objects.all(), self.request)

    def get_section_subject_for_permission_check(self, request):
        obj_id = self.kwargs.get("pk")
        if obj_id:
            obj = self.get_queryset().filter(pk=obj_id).first()
            if obj:
                unit = obj.syllabus_unit
                return unit.section.school_class.campus_id, unit.section_id, unit.subject_id
        return None, None, None

    @action(detail=True, methods=["post"], url_path="mark-covered")
    def mark_covered(self, request, pk=None):
        progress = self.get_object()
        unit = progress.syllabus_unit
        staff = StaffProfile.objects.filter(
            campus_id=unit.section.school_class.campus_id, user=request.user, is_active=True
        ).first()
        progress.covered_on = timezone.now().date()
        progress.covered_by = staff
        progress.save(update_fields=["covered_on", "covered_by"])
        return Response(self.get_serializer(progress).data)


# ============================================================
# Phase 7 — results
# ============================================================
class ExamTermViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = ExamTermSerializer
    campus_field_path = "session__campus"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(ExamTerm.objects.all(), self.request)

    def get_campus_id_for_permission_check(self, request):
        session_id = request.data.get("session")
        if session_id:
            s = AcademicSession.objects.filter(pk=session_id).first()
            if s:
                return s.campus_id
        return super().get_campus_id_for_permission_check(request)


class ResultEntryViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    Creating/updating a `ResultEntry` is treated as immediate publish —
    student + linked parent(s) get `RESULT_PUBLISHED` right away, since
    the current schema (design doc §7) has no separate draft/publish
    flag on this model. `entered_by` is always the requesting staff
    member, never client-supplied.
    """
    serializer_class = ResultEntrySerializer
    campus_field_path = "enrollment__section__school_class__campus"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(ResultEntry.objects.all(), self.request)

    def get_section_subject_for_permission_check(self, request):
        enrollment_id = request.data.get("enrollment")
        subject_id = request.data.get("subject")
        if enrollment_id:
            enrollment = StudentEnrollment.objects.filter(pk=enrollment_id).first()
            if enrollment:
                return enrollment.section.school_class.campus_id, enrollment.section_id, subject_id
        if self.kwargs.get("pk"):
            obj = self.get_queryset().filter(pk=self.kwargs["pk"]).first()
            if obj:
                return obj.enrollment.section.school_class.campus_id, obj.enrollment.section_id, obj.subject_id
        return None, None, None

    @transaction.atomic
    def perform_create(self, serializer):
        staff = StaffProfile.objects.filter(
            campus_id=serializer.validated_data["enrollment"].section.school_class.campus_id,
            user=self.request.user,
            is_active=True,
        ).first()
        entry = serializer.save(entered_by=staff)
        recipients = [entry.enrollment.student]
        parent_ids = CampusParentLink.objects.filter(student=entry.enrollment.student).values_list("parent", flat=True)
        recipients += list(parent_ids)
        bridge.notify(
            users=recipients,
            notif_type=NotifTypes.RESULT_PUBLISHED,
            title="Result published",
            body=f"{entry.subject.name}: {entry.marks_obtained}/{entry.max_marks}",
        )

    @action(detail=False, methods=["get"], url_path="report-card")
    def report_card(self, request):
        enrollment_id = request.query_params.get("enrollment")
        exam_term_id = request.query_params.get("exam_term")
        if not enrollment_id or not exam_term_id:
            return Response(
                {"detail": "enrollment and exam_term query params are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        enrollment = StudentEnrollment.objects.filter(pk=enrollment_id).first()
        exam_term = ExamTerm.objects.filter(pk=exam_term_id).first()
        if not enrollment or not exam_term:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        campus_id = enrollment.section.school_class.campus_id
        allowed = (
            is_any_active_staff(request.user, campus_id)
            or enrollment.student_id == request.user.id
            or is_linked_parent_of_student(request.user, enrollment.student_id, campus_id)
        )
        if not allowed:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(generate_report_card_data(enrollment, exam_term))


# ============================================================
# Phase 8 — optional / future-ready modules
# ============================================================
class DigitalIDCardViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    """`qr_token` is always server-generated (never client-supplied) —
    a student can request their own card, an admin/principal can issue
    one for anyone at their campus."""
    serializer_class = DigitalIDCardSerializer
    campus_field_path = "campus"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(DigitalIDCard.objects.all(), self.request)

    def perform_create(self, serializer):
        target_user = serializer.validated_data.get("user")
        campus = serializer.validated_data["campus"]
        if target_user.id != self.request.user.id and not is_campus_admin_or_principal(
            self.request.user, campus.id
        ):
            raise PermissionDenied("Only an admin/principal can issue a card for someone else.")
        serializer.save(qr_token=uuid.uuid4().hex)


def _require_fee_module_enabled(campus):
    """FEE-1: single shared gate for every fee-writing entry point, so
    a campus that never opted in (`Campus.fee_module_enabled=False`)
    can't have fee structures, invoices, or payments created against
    it through ANY path — not just `FeeStructureViewSet.perform_create`,
    which was previously the only place this was checked."""
    if not campus.fee_module_enabled:
        raise PermissionDenied("The fee module is not enabled for this campus (design doc §8 — opt-in only).")


class FeeStructureViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = FeeStructureSerializer
    campus_field_path = "campus"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(FeeStructure.objects.all(), self.request)

    def perform_create(self, serializer):
        campus = serializer.validated_data["campus"]
        _require_fee_module_enabled(campus)
        serializer.save()

    @action(detail=True, methods=["post"], url_path="generate-invoices")
    def generate_invoices(self, request, pk=None):
        """Bulk-creates a `FeeInvoice` for every currently-active
        enrollment this structure applies to (campus-wide if
        `school_class` is null, else just that class) — idempotent via
        `unique_invoice_per_structure`, so calling this twice never
        double-invoices the same student.

        FEE-1: this used to be reachable even for a campus that had
        since turned `fee_module_enabled` off after the FeeStructure
        was created (the module-disabled check only ran once, at
        FeeStructure-creation time, in `perform_create` above) — gated
        the same way here now."""
        fee_structure = self.get_object()
        _require_fee_module_enabled(fee_structure.campus)
        enrollments = StudentEnrollment.objects.filter(
            section__school_class__campus=fee_structure.campus,
            session=fee_structure.session,
            status=StudentEnrollment.Status.ACTIVE,
        )
        if fee_structure.school_class_id:
            enrollments = enrollments.filter(section__school_class_id=fee_structure.school_class_id)
        created = 0
        for enrollment in enrollments:
            _, was_created = FeeInvoice.objects.get_or_create(
                enrollment=enrollment,
                fee_structure=fee_structure,
                defaults={"amount_due": fee_structure.amount},
            )
            created += int(was_created)
        return Response({"invoices_created": created, "already_existed": enrollments.count() - created})


class FeeInvoiceViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    FEE-1 (gap found, not in the original task list but the same class
    of bug): this is a full `ModelViewSet`, so before this pass a
    campus admin could `POST` a `FeeInvoice` directly here even for a
    campus with `fee_module_enabled=False` — only
    `FeeStructureViewSet.perform_create`/`.generate_invoices` were
    gated, and this viewset's normal create path was never routed
    through either of those. Gated below the same way, for consistency
    with "agar False chuna, to fee-related sab UI/endpoints hidden/403
    rahein" from the task.
    """
    serializer_class = FeeInvoiceSerializer
    campus_field_path = "enrollment__section__school_class__campus"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(FeeInvoice.objects.all(), self.request)

    def perform_create(self, serializer):
        enrollment = serializer.validated_data["enrollment"]
        _require_fee_module_enabled(enrollment.section.school_class.campus)
        serializer.save()

    def get_campus_id_for_permission_check(self, request):
        enrollment_id = request.data.get("enrollment")
        if enrollment_id:
            e = StudentEnrollment.objects.filter(pk=enrollment_id).first()
            if e:
                return e.section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)


class FeePaymentViewSet(CampusMemberScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    FEE-2: `pay` used to be `payment_mode=ONLINE`, landing `PENDING`
    until a separate `confirm` action was called (standing in for a
    Razorpay webhook). That online gateway + confirm step is gone —
    `pay` now debits the payer's `user_profile.CoinLedger`-backed
    wallet synchronously and atomically (`CoinLedger.record_transaction`
    holds a DB row lock for the whole operation), so there is no
    PENDING-until-webhook window to confirm and the `confirm` action
    has been removed as dead code, not left in place unreachable.

    Two write paths, both still first-class: `pay` — student/parent
    self-serve, `payment_mode=WALLET`, resolves straight to SUCCESS or
    a clean 402 in the same request; `record` — office staff logging a
    cash/cheque/bank-transfer payment (no wallet touched at all),
    marked `SUCCESS` immediately, same as before. No generic create/
    update/delete — every write goes through one of those two actions.
    """
    serializer_class = FeePaymentSerializer
    campus_field_path = "invoice__enrollment__section__school_class__campus"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(FeePayment.objects.all(), self.request)

    def _get_invoice_and_check(self, request, allow_self_pay):
        invoice_id = request.data.get("invoice")
        invoice = FeeInvoice.objects.filter(pk=invoice_id).first() if invoice_id else None
        if not invoice:
            return None, Response({"detail": "invoice is required."}, status=status.HTTP_400_BAD_REQUEST)
        campus = invoice.enrollment.section.school_class.campus
        # FEE-1: gate every fee write, not just FeeStructure creation —
        # a disabled module means no new invoices should be payable
        # either, even if the invoice row itself already exists.
        if not campus.fee_module_enabled:
            return None, Response(
                {"detail": "The fee module is not enabled for this campus."}, status=status.HTTP_403_FORBIDDEN
            )
        campus_id = campus.id
        if allow_self_pay:
            is_self = invoice.enrollment.student_id == request.user.id
            is_parent = is_linked_parent_of_student(request.user, invoice.enrollment.student_id, campus_id)
            if not (is_self or is_parent):
                return None, Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        elif not is_campus_admin_or_principal(request.user, campus_id) and not is_any_active_staff(request.user, campus_id):
            return None, Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        return invoice, None

    @staticmethod
    def _resolve_whole_coin_amount(raw_amount):
        """FEE-2 unit-mismatch guard (see models.py module docstring):
        FeePayment.amount is a Decimal (paise-capable), CoinLedger.amount/
        User.coin are whole integers. Returns `(amount_decimal, error_response)`
        — `error_response` is set instead of raising so callers can
        `return` it directly, matching this viewset's existing
        `(value, error)` convention (`_get_invoice_and_check` above)."""
        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError):
            return None, Response({"detail": "amount must be a valid number."}, status=status.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return None, Response({"detail": "amount must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)
        if amount != amount.to_integral_value():
            return None, Response(
                {"detail": "Wallet payments must be a whole number of coins (no paise)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return amount, None

    # B-4 fix — see CampusFeePaymentThrottle's docstring in throttles.py:
    # same scope reused across pay/record/refund below since all three
    # move money on an invoice, just through different roles/paths.
    @action(detail=False, methods=["post"], throttle_classes=[CampusFeePaymentThrottle])
    def pay(self, request):
        invoice, error = self._get_invoice_and_check(request, allow_self_pay=True)
        if error:
            return error
        amount, error = self._resolve_whole_coin_amount(request.data.get("amount", invoice.amount_due))
        if error:
            return error
        payer_role = (
            FeePayment.PayerRole.STUDENT
            if invoice.enrollment.student_id == request.user.id
            else FeePayment.PayerRole.PARENT
        )
        gateway_reference = request.data.get("gateway_reference") or uuid.uuid4().hex

        # get_or_create keyed on gateway_reference is the FeePayment-level
        # idempotency guard: a client retrying the SAME request (same
        # reference) gets back the row from its first attempt instead of
        # a second FeePayment (which unique_nonblank_gateway_reference
        # would reject anyway) and, critically, without a second coin debit.
        payment, created = FeePayment.objects.get_or_create(
            gateway_reference=gateway_reference,
            defaults=dict(
                invoice=invoice,
                amount=amount,
                paid_by=request.user,
                payer_role=payer_role,
                payment_mode=FeePayment.Mode.WALLET,
                status=FeePayment.Status.PENDING,
            ),
        )
        if not created:
            return Response(FeePaymentSerializer(payment).data, status=status.HTTP_200_OK)

        try:
            CoinLedger.objects.record_transaction(
                user=request.user,
                transaction_type=CoinLedger.TransactionType.SPEND,
                amount=-int(amount),
                # Derived from the already-deduplicated payment row's own
                # id, not a fresh random value per call — a second,
                # independent idempotency guarantee at the wallet layer
                # itself (see models.py module docstring), distinct from
                # the gateway_reference guard above.
                reference=f"fee-payment-{payment.id}",
                description=f"Fee paid for {invoice.fee_structure.title}",
                metadata={"invoice_id": str(invoice.id), "campus_id": str(invoice.enrollment.section.school_class.campus_id)},
            )
        except ValueError:
            # FEE-3: insufficient balance — clean, catchable, no exception
            # bubbling up as a 500. Payment row is kept as FAILED (not
            # deleted) so there's an audit trail of the attempt; the
            # gateway_reference stays reserved so a genuine retry with the
            # same client-side idempotency key doesn't collide with a NEW
            # attempt at a different amount.
            payment.status = FeePayment.Status.FAILED
            payment.save(update_fields=["status"])
            request.user.refresh_from_db(fields=["coin"])
            shortfall = int(amount) - request.user.coin
            return Response(
                {
                    "detail": "Insufficient coin balance to pay this fee.",
                    "current_balance": request.user.coin,
                    "required": int(amount),
                    "coins_needed": shortfall,
                    # FEE-3: no new coin-purchase gateway here — point the
                    # client at the existing liveclass top-up flow instead
                    # of rebuilding one. Campus deliberately never imports
                    # liveclass (see models.py GOLDEN RULE), so this is a
                    # generic flag for the frontend to route on, not a
                    # hardcoded liveclass URL.
                    "action": "top_up_coins",
                },
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        payment.mark_success()
        return Response(FeePaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], throttle_classes=[CampusFeePaymentThrottle])
    def record(self, request):
        invoice, error = self._get_invoice_and_check(request, allow_self_pay=False)
        if error:
            return error
        amount, error = self._resolve_whole_coin_amount(request.data.get("amount", invoice.amount_due))
        if error:
            return error
        payment_mode = request.data.get("payment_mode", FeePayment.Mode.CASH)
        if payment_mode == FeePayment.Mode.WALLET:
            # Office staff shouldn't be able to trigger a wallet debit
            # through the "manual counter payment" path — that's what
            # `pay` is for, and it's the payer's own wallet, not
            # something staff can spend on someone else's behalf here.
            return Response(
                {"detail": "Use the pay action for wallet payments; record is for cash/cheque/bank-transfer only."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payment = FeePayment.objects.create(
            invoice=invoice,
            amount=amount,
            payer_role=FeePayment.PayerRole.ADMIN,
            payment_mode=payment_mode,
            status=FeePayment.Status.SUCCESS,
            recorded_by=request.user,
            notes=request.data.get("notes", ""),
        )
        payment.invoice.recompute_status()
        return Response(FeePaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], throttle_classes=[CampusFeePaymentThrottle])
    def refund(self, request, pk=None):
        """FEE-4: reverses a wallet payment and credits the coins back
        via `FeePayment.refund()`. Office/admin action only — same
        permission shape as `record`, not self-serve like `pay`."""
        payment = self.get_object()
        campus_id = payment.invoice.enrollment.section.school_class.campus_id
        if not is_campus_admin_or_principal(request.user, campus_id) and not is_any_active_staff(request.user, campus_id):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        try:
            payment.refund()
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(FeePaymentSerializer(payment).data)


class CampusAnalyticsSnapshotViewSet(CampusMemberScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only — rows are written exclusively by the
    `campus-refresh-analytics-snapshot` Celery task (design doc §8),
    never from a request."""
    serializer_class = CampusAnalyticsSnapshotSerializer
    campus_field_path = "campus"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(CampusAnalyticsSnapshot.objects.all(), self.request)

    @action(detail=False, methods=["get"])
    def latest(self, request):
        campus_id = request.query_params.get("campus")
        if not campus_id:
            return Response({"detail": "campus query param is required."}, status=status.HTTP_400_BAD_REQUEST)
        snapshot = self.get_queryset().filter(campus_id=campus_id).order_by("-computed_at").first()
        if not snapshot:
            return Response({"detail": "No snapshot yet."}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(snapshot).data)


class CampusParentLinkViewSet(CampusMemberScopedMixin, viewsets.ReadOnlyModelViewSet):
    """
    Read-only — the only write path is `ParentLinkVerifyView` below,
    which requires a verified `message.ParentAccessCode`/token (via
    `bridge.resolve_parent_from_token`), never a plain POST here (see
    `CampusParentLink`'s model docstring).
    """
    serializer_class = CampusParentLinkSerializer
    campus_field_path = "campus"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.filter_queryset_to_my_campuses(CampusParentLink.objects.all(), self.request)
        user = self.request.user
        return qs.filter(Q(parent=user) | Q(student=user) | Q(campus__staff_profiles__user=user, campus__staff_profiles__is_active=True)).distinct()


class ParentLinkVerifyView(APIView):
    """
    `POST {"campus": <id>, "token": "<parent access token>"}` — the
    only way a `CampusParentLink` row is ever created. Verification
    itself happens in `message`'s existing ParentAccessCode/ParentToken
    flow via `bridge.resolve_parent_from_token`; this view never reads
    or writes `message` models directly.
    """
    permission_classes = [IsAuthenticated]
    # B-4 fix — see CampusParentLinkVerifyThrottle's docstring in
    # throttles.py: this whole view is a single POST action, so a
    # class-level throttle_classes (no get_throttles() override needed)
    # is enough, unlike NoticeViewSet/FeePaymentViewSet/
    # CampusLiveSessionViewSet above which each have other, unrelated
    # actions that should keep the project-wide default.
    throttle_classes = [CampusParentLinkVerifyThrottle]

    def post(self, request):
        token = request.data.get("token")
        campus_id = request.data.get("campus")
        if not token or not campus_id:
            return Response({"detail": "token and campus are required."}, status=status.HTTP_400_BAD_REQUEST)
        campus = Campus.objects.filter(pk=campus_id).first()
        if not campus:
            return Response({"detail": "Campus not found."}, status=status.HTTP_404_NOT_FOUND)
        # G-2 fix — linking a parent is another "pull someone else into
        # this campus" action (see `StaffProfileViewSet`'s docstring for
        # the shared reasoning).
        if not is_campus_approved(campus.id):
            return Response(
                {"detail": "This campus is pending platform verification and can't link parents yet."},
                status=status.HTTP_403_FORBIDDEN,
            )
        parent_user, student_user = bridge.resolve_parent_from_token(token)
        if not parent_user or not student_user:
            return Response({"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)
        link, _ = CampusParentLink.objects.get_or_create(
            campus=campus, student=student_user, parent=parent_user
        )
        return Response(CampusParentLinkSerializer(link).data, status=status.HTTP_201_CREATED)