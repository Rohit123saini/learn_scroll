# campus/views/notices.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from rest_framework.exceptions import (
    APIException,
    PermissionDenied,
    ValidationError,
)
from ..models import (
    AcademicSession,
    Campus,
    Notice,
    SchoolClass,
    Section,
    StaffProfile,
)
from ..throttles import (
    CampusEmergencyBroadcastThrottle,
    CampusNoticePostThrottle,
)
from rest_framework.permissions import IsAuthenticated
from ..serializers import NoticeSerializer
from django.db.models import Q
from rest_framework.response import Response
from ..soft_delete_views import SoftDeleteViewSetMixin
from rest_framework.decorators import action
from ..permissions import (
    can_post_notice,
    is_campus_admin_or_principal,
)
from ..visibility import get_visibility
from ..audit import log_action
from .. import notice_rules
from .. import notices as notices_service
from rest_framework import (
    status,
    viewsets,
)
from django.utils import timezone
from ._common import CampusMemberScopedMixin


class NoticeAPIError(APIException):
    """A `notice_rules.NoticeRuleError` as an HTTP error: human `detail` + stable machine `code`
    (the project's exception handler puts `machine_code` into the envelope's `code`)."""

    status_code = 400
    _STATUS_BY_CODE = {"notice_already_sent": 409, "not_scheduled": 409, "duplicate_emergency": 409}

    def __init__(self, err):
        super().__init__(detail=err.message)
        self.machine_code = err.code
        self.status_code = self._STATUS_BY_CODE.get(err.code, 400)


class NoticeViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    G-1 fix: who may post at a given scope is decided by `permissions.can_post_notice` (campus
    admin/principal-HOD at any scope; a class-teacher only to their own section; nobody else).

    COMMUNICATION (rules in `notice_rules.py`, delivery in `notices.py`):
      * scope  — whole school (no scope FK) / department / class / section, as before.
      * audience — `everyone` (default) | `students` | `parents` | `staff`: who gets the push.
      * scheduled notice — send `scheduled_for` (1 min .. 90 days ahead): it is stored as
        `status=scheduled`, invisible to students/parents, and published by the every-minute task
        `campus.tasks.publish_due_notices`. Until then the poster / an admin may edit it, cancel it
        (`POST notices/{id}/cancel/`) or send it right away (`POST notices/{id}/send-now/`).
      * emergency broadcast — `POST notices/emergency/` (admin / principal only): goes out
        immediately, pinned for 24 h, push bypasses quiet hours and cannot be muted.
    `GET notices/?campus=` lists PUBLISHED notices (what the app always showed);
    `?status=scheduled|cancelled|all` is for the poster / admins (others get nothing extra).
    """
    serializer_class = NoticeSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        return (
            Q(department__isnull=True, school_class__isnull=True, section__isnull=True)
            | Q(department_id__in=vis.visible_department_ids, school_class__isnull=True, section__isnull=True)
            | Q(school_class_id__in=vis.visible_class_ids, section__isnull=True)
            | Q(section_id__in=vis.visible_section_ids)
        )
    permission_classes = [IsAuthenticated]

    # ------------------------------------------------------------------ visibility
    def _admin_campus_ids(self):
        return set(
            StaffProfile.objects.filter(
                user=self.request.user, is_active=True,
                role__in=[StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD],
            ).values_list("campus_id", flat=True)
        )

    def get_queryset(self):
        qs = self.filter_queryset_to_my_campuses(Notice.objects.select_related("posted_by"), self.request)  # [PERF] posted_by shown per row
        user = self.request.user
        vis = get_visibility(user, self.request)
        if vis.nonstaff_campus_ids:
            # a staff-only notice is not for students / parents
            qs = qs.exclude(audience=Notice.Audience.STAFF, campus_id__in=vis.nonstaff_campus_ids)
        # Scheduled / cancelled notices: only their poster and the campus admin / principal see them.
        qs = qs.filter(
            Q(status=Notice.Status.PUBLISHED) | Q(posted_by=user) | Q(campus_id__in=self._admin_campus_ids())
        )
        if self.action == "list":
            wanted = (self.request.query_params.get("status") or Notice.Status.PUBLISHED).lower()
            if wanted != "all":
                qs = qs.filter(status=wanted if wanted in notice_rules.STATUSES else Notice.Status.PUBLISHED)
        return qs

    def get_throttles(self):
        # B-4 fix — only the abuse-prone actions (posting fans out to a whole campus/section) get a
        # scoped limit; list/retrieve/update/delete keep the project-wide default.
        if self.action == "create":
            return [CampusNoticePostThrottle()]
        if self.action == "emergency":
            return [CampusEmergencyBroadcastThrottle()]
        return super().get_throttles()

    # ------------------------------------------------------------------ helpers
    def _check_scope_allowed(self, campus, department, school_class, section):
        if not can_post_notice(
            self.request.user,
            campus.id,
            department_id=department.id if department else None,
            school_class_id=school_class.id if school_class else None,
            section_id=section.id if section else None,
        ):
            raise PermissionDenied("You're not allowed to post a notice at this scope.")

    def _check_can_manage(self, notice):
        """Edit / cancel / delete: the poster, or the campus admin / principal."""
        user = self.request.user
        if notice.posted_by_id == user.id or is_campus_admin_or_principal(user, notice.campus_id):
            return
        raise PermissionDenied("Only the person who posted this notice, or the campus admin, can change it.")

    def _rule_error(self, exc):
        return NoticeAPIError(exc)

    # ------------------------------------------------------------------ create / update / delete
    def perform_create(self, serializer):
        data = serializer.validated_data
        campus = data["campus"]
        self._check_scope_allowed(campus, data.get("department"), data.get("school_class"), data.get("section"))
        now = timezone.now()
        if data.get("scheduled_for"):
            notice = serializer.save(posted_by=self.request.user, status=Notice.Status.SCHEDULED)
            return  # nothing goes out yet — `publish_due_notices` (or "send now") delivers it
        notice = serializer.save(
            posted_by=self.request.user, status=Notice.Status.PUBLISHED, published_at=now, scheduled_for=None
        )
        # Best-effort push (never blocks the 201), but no longer silent when it fails.
        notices_service.deliver(notice)

    def perform_update(self, serializer):
        notice = serializer.instance
        self._check_can_manage(notice)
        if not notice_rules.can_edit(notice.status):
            raise self._rule_error(notice_rules.NoticeRuleError(
                "notice_already_sent", "This notice has already gone out and can no longer be edited."))
        data = serializer.validated_data
        self._check_scope_allowed(
            data.get("campus", notice.campus),
            data.get("department", notice.department),
            data.get("school_class", notice.school_class),
            data.get("section", notice.section),
        )
        serializer.save()

    def perform_destroy(self, instance):
        self._check_can_manage(instance)
        self.soft_delete_instance(instance)

    # ------------------------------------------------------------------ scheduled: cancel / send now
    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """Cancel a scheduled notice (it will never be pushed)."""
        notice = self.get_object()
        self._check_can_manage(notice)
        if not notice_rules.can_cancel(notice.status):
            raise self._rule_error(notice_rules.NoticeRuleError(
                "not_scheduled", "Only a scheduled notice can be cancelled."))
        updated = Notice.objects.filter(pk=notice.pk, status=Notice.Status.SCHEDULED).update(
            status=Notice.Status.CANCELLED)
        if not updated:  # the scheduler published it a moment ago
            raise self._rule_error(notice_rules.NoticeRuleError(
                "not_scheduled", "This notice has just been sent."))
        notice.refresh_from_db()
        log_action(notice.campus_id, request.user, "notice.cancel", target=notice, summary=notice.title)
        return Response(self.get_serializer(notice).data)

    @action(detail=True, methods=["post"], url_path="send-now")
    def send_now(self, request, pk=None):
        """Publish a scheduled notice right away (instead of waiting for its time)."""
        notice = self.get_object()
        self._check_can_manage(notice)
        if not notice_rules.can_cancel(notice.status):
            raise self._rule_error(notice_rules.NoticeRuleError(
                "not_scheduled", "Only a scheduled notice can be sent now."))
        if not notices_service.publish_now(notice):
            raise self._rule_error(notice_rules.NoticeRuleError(
                "not_scheduled", "This notice has already been sent."))
        notice.refresh_from_db()
        log_action(notice.campus_id, request.user, "notice.send_now", target=notice, summary=notice.title)
        return Response(self.get_serializer(notice).data)

    # ------------------------------------------------------------------ emergency broadcast
    @action(detail=False, methods=["post"], url_path="emergency")
    def emergency(self, request):
        """Emergency broadcast — admin / principal of the campus only.

        Body: `campus`, `title`, `body`, optional ONE scope (`school_class` or `section`; none = the
        whole school) and optional `audience` (default everyone), `session` (default: the campus's
        current session). Sent immediately and pinned 24 h; the push bypasses quiet hours and the
        user's mute for this type. Audit-logged. A second identical alert from the same person within
        2 minutes is refused (a double tap is not a second emergency).
        """
        campus_id = request.data.get("campus")
        campus = Campus.objects.filter(pk=campus_id).first() if campus_id else None
        if campus is None:
            raise ValidationError({"campus": "Campus is required."})
        if not is_campus_admin_or_principal(request.user, campus.id):
            raise PermissionDenied("Only the campus admin or principal can send an emergency broadcast.")
        try:
            title, body = notice_rules.validate_emergency(request.data.get("title"), request.data.get("body"))
        except notice_rules.NoticeRuleError as exc:
            raise self._rule_error(exc)

        school_class = section = None
        if request.data.get("section"):
            section = Section.objects.filter(
                pk=request.data["section"], school_class__campus_id=campus.id).select_related("school_class").first()
            if section is None:
                raise ValidationError({"section": "Doesn't belong to the given campus."})
        elif request.data.get("school_class"):
            school_class = SchoolClass.objects.filter(pk=request.data["school_class"], campus_id=campus.id).first()
            if school_class is None:
                raise ValidationError({"school_class": "Doesn't belong to the given campus."})
        audience = request.data.get("audience") or Notice.Audience.EVERYONE
        if audience not in notice_rules.AUDIENCES:
            raise ValidationError({"audience": "Unknown audience."})

        session = None
        if request.data.get("session"):
            session = AcademicSession.objects.filter(pk=request.data["session"], campus_id=campus.id).first()
        if session is None:
            session = AcademicSession.objects.filter(campus_id=campus.id, is_current=True).first()
        if session is None:
            raise self._rule_error(notice_rules.NoticeRuleError(
                "no_session", "Set up the current academic session before sending a broadcast."))

        now = timezone.now()
        last = Notice.objects.filter(
            campus_id=campus.id, posted_by=request.user, kind=Notice.Kind.EMERGENCY, title=title
        ).order_by("-created_at").values_list("created_at", flat=True).first()
        if notice_rules.is_duplicate_emergency(last, now):
            raise self._rule_error(notice_rules.NoticeRuleError(
                "duplicate_emergency", "This alert was just sent. Wait a couple of minutes to send it again."))

        notice = Notice.objects.create(
            campus=campus, session=session, school_class=school_class, section=section,
            posted_by=request.user, title=title, body=body, kind=Notice.Kind.EMERGENCY,
            audience=audience, status=Notice.Status.PUBLISHED, published_at=now,
            pin_until=notice_rules.emergency_pin_until(now),
        )
        sent = notices_service.deliver(notice)
        log_action(
            campus.id, request.user, "notice.emergency", target=notice, summary=title,
            audience=audience, recipients=sent,
            scope="section" if section else "class" if school_class else "campus",
        )
        notice.refresh_from_db()
        return Response(self.get_serializer(notice).data, status=status.HTTP_201_CREATED)
