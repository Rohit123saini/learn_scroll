# campus/admission_views.py
"""
ADMISSION API  —  `/campus/admissions/`   (rules: `campus.admission`)

    GET   admissions/                list   (?campus= ?session= ?status= ?class= ?q= ?follow_up=due|today
                                              ?source= ?ordering=newest|follow_up)
    POST  admissions/                new enquiry / application
                                     {campus, applicant_name, guardian_name?, guardian_phone?, guardian_email?,
                                      school_class?, session?, date_of_birth?, previous_school?, address?,
                                      source?, follow_up_on?, remarks?, force?}
    GET   admissions/{id}/           retrieve (+ `seats` for the wanted class)
    POST  admissions/{id}/edit/      send only what changes
    POST  admissions/{id}/move/      {to, note?, interview_on?, section?}
                                     to = applied | interview | waitlisted | offered | rejected | withdrawn
                                     (rejected needs `note`; offered needs a free seat; rejected/withdrawn -> applied reopens)
    POST  admissions/{id}/enroll/    {student, section?, roll_number?, enrollment_no?, override_capacity?}
                                     offered -> enrolled: links the student account, enrols, creates admission-fee invoices
    GET   admissions/summary/        ?campus= [&session=]  funnel counts, conversion, follow-ups due
    GET   admissions/export/         ?campus= [&session= &status= &class=]  CSV
    POST  admissions/import/         {campus, session?, file (CSV) | csv (text), commit?}  preview unless commit=true

Who: campus admin / principal of THAT campus only. Everyone else gets an empty list / 403 / 404 —
admission data is never shown to teachers, students or parents.

Errors carry a stable machine `code` (`duplicate_application`, `no_seats`, `bad_transition`, ...).
Push goes out after commit via Celery (`campus.admission_push`).
"""
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.async_utils import dispatch_after_commit

from . import admission as svc
from . import tasks as campus_tasks
from .audit import log_action
from .models import AdmissionApplication, Campus, StaffProfile
from .permissions import is_campus_admin_or_principal
from .throttles import CampusAdmissionWriteThrottle

logger = logging.getLogger(__name__)

_APPROVER_ROLES = (StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD)


class AdmissionAPIError(APIException):
    """4xx with a stable machine `code`; the project's exception handler puts `machine_code` in the envelope."""

    status_code = 400

    def __init__(self, err):
        super().__init__(detail=err.message)
        self.machine_code = err.code
        self.status_code = err.status


# ------------------------------------------------------------------ serializers

class AdmissionApplySerializer(serializers.Serializer):
    campus = serializers.UUIDField()
    session = serializers.UUIDField(required=False, allow_null=True)
    school_class = serializers.UUIDField(required=False, allow_null=True)
    applicant_name = serializers.CharField(max_length=150)
    guardian_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    guardian_phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    guardian_email = serializers.EmailField(required=False, allow_blank=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    previous_school = serializers.CharField(required=False, allow_blank=True, max_length=150)
    address = serializers.CharField(required=False, allow_blank=True, max_length=300)
    source = serializers.ChoiceField(choices=AdmissionApplication.Source.choices, required=False)
    follow_up_on = serializers.DateField(required=False, allow_null=True)
    remarks = serializers.CharField(required=False, allow_blank=True, max_length=500)
    force = serializers.BooleanField(required=False, default=False)


class AdmissionEditSerializer(serializers.Serializer):
    school_class = serializers.UUIDField(required=False, allow_null=True)
    section = serializers.UUIDField(required=False, allow_null=True)
    applicant_name = serializers.CharField(required=False, max_length=150)
    guardian_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    guardian_phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    guardian_email = serializers.EmailField(required=False, allow_blank=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    previous_school = serializers.CharField(required=False, allow_blank=True, max_length=150)
    address = serializers.CharField(required=False, allow_blank=True, max_length=300)
    source = serializers.ChoiceField(choices=AdmissionApplication.Source.choices, required=False)
    follow_up_on = serializers.DateField(required=False, allow_null=True)
    remarks = serializers.CharField(required=False, allow_blank=True, max_length=500)
    force = serializers.BooleanField(required=False, default=False)


class AdmissionMoveSerializer(serializers.Serializer):
    to = serializers.CharField(max_length=12)
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)
    interview_on = serializers.DateTimeField(required=False, allow_null=True)
    section = serializers.UUIDField(required=False, allow_null=True)


class AdmissionEnrollSerializer(serializers.Serializer):
    student = serializers.CharField(max_length=150)
    section = serializers.UUIDField(required=False, allow_null=True)
    roll_number = serializers.CharField(required=False, allow_blank=True, max_length=30)
    enrollment_no = serializers.CharField(required=False, allow_blank=True, max_length=30)
    override_capacity = serializers.BooleanField(required=False, default=False)


class AdmissionSerializer(serializers.ModelSerializer):
    """Output only. `allowed_moves` tells the app which buttons to show (the server re-checks)."""

    status_label = serializers.CharField(source="get_status_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    class_name = serializers.CharField(source="school_class.name", read_only=True, default=None)
    section_name = serializers.CharField(source="section.name", read_only=True, default=None)
    session_name = serializers.CharField(source="session.name", read_only=True)
    student_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    allowed_moves = serializers.SerializerMethodField()
    can_enroll = serializers.SerializerMethodField()
    follow_up_due = serializers.SerializerMethodField()

    class Meta:
        model = AdmissionApplication
        fields = [
            "id", "campus", "session", "session_name", "school_class", "class_name", "section", "section_name",
            "application_no", "applicant_name", "date_of_birth", "guardian_name", "guardian_phone",
            "guardian_email", "previous_school", "address", "source", "source_label", "status", "status_label",
            "follow_up_on", "follow_up_due", "interview_on", "remarks", "decision_note", "decided_by_name",
            "decided_at", "student", "student_name", "enrollment", "created_at", "updated_at",
            "allowed_moves", "can_enroll",
        ]
        read_only_fields = fields

    def get_student_name(self, obj):
        return svc.display_name(obj.student) if obj.student_id else None

    def get_decided_by_name(self, obj):
        return svc.display_name(obj.decided_by) if obj.decided_by_id else None

    def get_allowed_moves(self, obj):
        moves = svc.TRANSITIONS.get(AdmissionApplication.Status(obj.status), set())
        return sorted(str(m.value) for m in moves)

    def get_can_enroll(self, obj):
        return obj.status == AdmissionApplication.Status.OFFERED

    def get_follow_up_due(self, obj):
        return bool(
            obj.follow_up_on and obj.status in svc.OPEN_STATUSES and obj.follow_up_on <= timezone.localdate()
        )


# ------------------------------------------------------------------ viewset

def _truthy(value):
    return str(value or "").lower() in ("1", "true", "yes", "on")


class AdmissionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = AdmissionSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_throttles(self):
        if self.action in ("create", "edit", "move", "enroll", "import_csv"):
            return [CampusAdmissionWriteThrottle()]
        return super().get_throttles()

    # ---- who am I here ------------------------------------------------------
    def _admin_campus_ids(self):
        cached = getattr(self, "_admin_ids", None)
        if cached is None:
            cached = set(
                StaffProfile.objects.filter(
                    user=self.request.user, is_active=True, role__in=_APPROVER_ROLES
                ).values_list("campus_id", flat=True)
            )
            self._admin_ids = cached
        return cached

    def _campus_or_error(self, campus_id):
        """The Campus the caller administers, else 403 (404 when it doesn't exist)."""
        try:
            campus = Campus.objects.filter(pk=campus_id).first() if campus_id else None
        except (ValueError, TypeError, DjangoValidationError):
            campus = None
        if not campus_id:
            raise ValidationError({"campus": "Campus is required."})
        if campus is None:
            raise AdmissionAPIError(svc.AdmissionError("campus_not_found", "Campus not found.", 404))
        if not is_campus_admin_or_principal(self.request.user, campus.pk):
            raise PermissionDenied("Only the campus admin or principal manages admissions.")
        return campus

    # ---- queryset -----------------------------------------------------------
    def get_queryset(self):
        qs = AdmissionApplication.objects.select_related(
            "campus", "session", "school_class", "section", "student", "decided_by"
        ).filter(campus_id__in=self._admin_campus_ids())
        return self._filtered(qs, self.request.query_params) if self.action in ("list", "export") else qs

    def _filtered(self, qs, params):
        try:
            if params.get("campus"):
                qs = qs.filter(campus_id=params["campus"])
            if params.get("session"):
                qs = qs.filter(session_id=params["session"])
            if params.get("class"):
                qs = qs.filter(school_class_id=params["class"])
        except (ValueError, TypeError, DjangoValidationError):
            raise ValidationError({"detail": "Invalid id in filter."})
        if params.get("status"):
            wanted = [s for s in params["status"].lower().split(",") if s]
            bad = [s for s in wanted if s not in AdmissionApplication.Status.values]
            if bad:
                raise ValidationError({"status": f"One of: {', '.join(AdmissionApplication.Status.values)}."})
            qs = qs.filter(status__in=wanted)
        if params.get("source"):
            if params["source"] not in AdmissionApplication.Source.values:
                raise ValidationError({"source": f"One of: {', '.join(AdmissionApplication.Source.values)}."})
            qs = qs.filter(source=params["source"])
        follow = (params.get("follow_up") or "").lower()
        if follow:
            today = timezone.localdate()
            qs = qs.filter(status__in=svc.OPEN_STATUSES, follow_up_on__isnull=False)
            if follow == "due":
                qs = qs.filter(follow_up_on__lte=today)
            elif follow == "today":
                qs = qs.filter(follow_up_on=today)
            else:
                raise ValidationError({"follow_up": "Use due or today."})
        text = (params.get("q") or "").strip()
        if text:
            from django.db.models import Q

            qs = qs.filter(
                Q(applicant_name__icontains=text) | Q(guardian_name__icontains=text)
                | Q(guardian_phone__icontains=text) | Q(application_no__icontains=text)
            )
        if params.get("ordering") == "follow_up":
            qs = qs.order_by("follow_up_on", "-created_at")
        return qs

    def retrieve(self, request, *args, **kwargs):
        app = self.get_object()
        data = self.get_serializer(app).data
        data["seats"] = svc.seats_info(app.school_class, app.session, exclude_app_id=app.pk) if app.school_class_id else None
        return Response(data)

    # ---- create -------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        ser = AdmissionApplySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        campus = self._campus_or_error(data.pop("campus"))
        force = data.pop("force", False)
        session_id = data.pop("session", None)
        if data.get("school_class"):
            data["school_class"] = str(data["school_class"])
        session_id = str(session_id) if session_id else None
        try:
            with transaction.atomic():
                app = svc.create_application(
                    campus=campus, actor=request.user, session_id=session_id, force=force, **data
                )
                log_action(
                    campus.pk, request.user, "admission.create", target=app,
                    summary=f"admission {app.application_no} opened: {app.applicant_name}",
                )
        except svc.AdmissionError as exc:
            raise AdmissionAPIError(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=app.pk)).data, status=status.HTTP_201_CREATED)

    # ---- edit ---------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="edit")
    def edit(self, request, pk=None):
        app = self.get_object()  # 404 for a campus the caller doesn't administer
        ser = AdmissionEditSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = {k: (str(v) if k in ("school_class", "section") and v else v) for k, v in ser.validated_data.items()}
        force = data.pop("force", False)
        try:
            with transaction.atomic():
                app = svc.edit_application(app_id=app.pk, force=force, **data)
                log_action(
                    app.campus_id, request.user, "admission.edit", target=app,
                    summary=f"admission {app.application_no} edited", fields=sorted(data.keys()),
                )
        except svc.AdmissionError as exc:
            raise AdmissionAPIError(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=app.pk)).data)

    # ---- move ---------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="move")
    def move(self, request, pk=None):
        app = self.get_object()
        ser = AdmissionMoveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        before = app.status
        try:
            with transaction.atomic():
                app = svc.move(
                    app_id=app.pk, to=d["to"], actor=request.user, note=d.get("note", ""),
                    interview_on=d.get("interview_on"), section_id=str(d["section"]) if d.get("section") else None,
                )
                if before != app.status:
                    log_action(
                        app.campus_id, request.user, f"admission.{app.status}", target=app,
                        summary=f"admission {app.application_no}: {before} -> {app.status}",
                    )
        except svc.AdmissionError as exc:
            raise AdmissionAPIError(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=app.pk)).data)

    # ---- enroll -------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="enroll")
    def enroll(self, request, pk=None):
        app = self.get_object()
        ser = AdmissionEnrollSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        try:
            with transaction.atomic():
                app, enrollment, invoices = svc.enroll(
                    app_id=app.pk, actor=request.user, student=d["student"],
                    section_id=str(d["section"]) if d.get("section") else None,
                    roll_number=d.get("roll_number", ""), enrollment_no=d.get("enrollment_no", ""),
                    override_capacity=d.get("override_capacity", False),
                )
                log_action(
                    app.campus_id, request.user, "admission.enroll", target=app,
                    summary=f"admission {app.application_no} enrolled: {app.applicant_name}",
                    enrollment=str(enrollment.pk), invoices=invoices,
                )
                dispatch_after_commit(campus_tasks.send_admission_notifications, str(app.pk), "enrolled")
        except svc.AdmissionError as exc:
            raise AdmissionAPIError(exc)
        data = self.get_serializer(self.get_queryset().get(pk=app.pk)).data
        data["invoices_created"] = invoices
        data["enrollment_id"] = str(enrollment.pk)
        return Response(data)

    # ---- summary / export / import -------------------------------------------
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        campus = self._campus_or_error(request.query_params.get("campus"))
        try:
            session = svc.resolve_session(campus, request.query_params.get("session"))
        except svc.AdmissionError as exc:
            raise AdmissionAPIError(exc)
        return Response(svc.summary(campus, session))

    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request):
        campus = self._campus_or_error(request.query_params.get("campus"))
        qs = self.get_queryset().filter(campus=campus)
        if not request.query_params.get("session"):
            try:
                qs = qs.filter(session=svc.current_session(campus))
            except svc.AdmissionError as exc:
                raise AdmissionAPIError(exc)
        body = svc.export_csv(qs.order_by("application_no", "created_at"))
        resp = HttpResponse(body, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="admissions.csv"'
        log_action(campus.pk, request.user, "admission.export", target_type="AdmissionApplication", summary="admissions exported")
        return resp

    @action(detail=False, methods=["post"], url_path="import")
    def import_csv(self, request):
        campus = self._campus_or_error(request.data.get("campus"))
        upload = request.FILES.get("file")
        if upload is not None:
            if upload.size > svc.MAX_IMPORT_BYTES:
                raise AdmissionAPIError(svc.AdmissionError("file_too_large", "CSV is too large (max 300 KB)."))
            try:
                text = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                raise AdmissionAPIError(svc.AdmissionError("bad_encoding", "Save the CSV as UTF-8 and try again."))
        else:
            text = str(request.data.get("csv") or "")
        if not text.strip():
            raise AdmissionAPIError(svc.AdmissionError("empty_file", "Send the CSV as `file` or `csv`."))
        commit = _truthy(request.data.get("commit"))
        try:
            with transaction.atomic():
                out = svc.import_applications(
                    campus=campus, actor=request.user, text=text,
                    session_id=request.data.get("session") or None, commit=commit,
                )
                if commit:
                    log_action(
                        campus.pk, request.user, "admission.import", target_type="AdmissionApplication",
                        summary=f"{out['created']} admission enquiries imported", skipped=len(out["skipped"]),
                    )
        except svc.AdmissionError as exc:
            raise AdmissionAPIError(exc)
        return Response(out, status=status.HTTP_201_CREATED if commit and out["created"] else status.HTTP_200_OK)
