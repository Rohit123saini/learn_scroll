# campus/safety_views.py
"""
SAFETY API  —  everything under  /campus/safety/<campus_id>/...      (rules: `campus.safety_rules`)

(The audit log filter / search / facets / CSV export stay on `campuses/<id>/audit-log...` — `campus/audit_query.py`.)

OVERVIEW
    GET  overview/                          safety.view   one card per area: last backup, open deletion requests
                                                          (+ overdue), consent still missing, audit rows (7 days)

BACKUP / RESTORE        (admin / principal; RESTORE = campus ADMIN only)
    GET  backups/                           list  (?kind= ?status=)
    POST backups/                           {groups?: ["setup","records"], note?}       -> 201 backup
    GET  backups/<id>/                      one backup
    DELETE backups/<id>/                    removes file + row
    GET  backups/<id>/download/             the .json.gz file (audit-logged)
    POST backups/<id>/restore/              {groups?, mode?: missing_only|overwrite, dry_run?: true, confirm?: "RESTORE"}
                                            dry_run defaults to TRUE; a real run needs confirm="RESTORE"

DATA DELETE REQUEST     (any member files; admin / principal decides)
    GET  deletion-requests/                 admin: all (?status= ?overdue=1 ?q=) · everyone else: their own
    POST deletion-requests/                 {kind: my_data|child_data, reason, student?, wants_account_deletion?}
    GET  deletion-requests/<id>/
    GET  deletion-requests/<id>/preview/    what executing would erase / keep          (admin)
    POST deletion-requests/<id>/approve/    {note?}                                    (admin)
    POST deletion-requests/<id>/reject/     {note}   required                          (admin)
    POST deletion-requests/<id>/execute/    erase (only an APPROVED request)           (admin)
    POST deletion-requests/<id>/cancel/     requester, while pending

CONSENT LOG
    GET  consent/me/                        what the caller has answered / still has to answer
    POST consent/me/                        {purpose, granted: bool, student?, policy_version?}
    GET  consent/log/                       safety.view; ?purpose= ?status= ?source= ?subject= ?student= ?q= (paginated)
    GET  consent/log/export/                same filters, CSV (audit-logged)
    GET  consent/summary/                   safety.view; per purpose: granted / withdrawn / missing / out of date
    POST consent/record/                    safety.manage; {subject, purpose, granted, student?, note?, policy_version?}
                                            staff typing in a paper / verbal consent (stored with source=admin_entry)

Errors carry a stable machine `code` (`request_already_open`, `confirm_required`, `backup_corrupt`, ...) in the
project's error envelope.
"""
import logging
import uuid

from django.contrib.auth import get_user_model
from django.http import FileResponse, Http404, HttpResponse
from django.utils import timezone
from rest_framework import status as http
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.pagination import StandardPagination

from . import backup as backup_svc
from . import consent as consent_svc
from . import data_deletion as deletion_svc
from . import safety_rules as rules
from .audit import log_action
from .models import (
    Campus,
    CampusAuditLog,
    CampusBackup,
    CampusConsentRecord,
    CampusDataDeletionRequest,
    StaffProfile,
)
from .permissions import get_campus_role, get_staff_profile, user_can
from .throttles import CampusDataRequestThrottle, CampusReportExportThrottle, CampusSafetyRestoreThrottle, CampusSafetyWriteThrottle

from datetime import timedelta

logger = logging.getLogger(__name__)
User = get_user_model()


class SafetyAPIError(APIException):
    """4xx with a stable machine `code` + a short human `detail`."""

    status_code = 400

    def __init__(self, err):
        super().__init__(detail=getattr(err, "detail", "") or err.code.replace("_", " "))
        self.machine_code = err.code
        self.status_code = getattr(err, "status", 400)


def _truthy(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def _campus(campus_id) -> Campus:
    try:
        uuid.UUID(str(campus_id))
    except ValueError:
        raise Http404
    try:
        return Campus.objects.get(pk=campus_id)
    except Campus.DoesNotExist:
        raise Http404


def _need(user, campus, action):
    if not user_can(user, campus.id, action):
        raise PermissionDenied("You don't have permission for this action in this campus.")


def _member(user, campus):
    if get_campus_role(user, campus.id) is None:
        raise PermissionDenied("You are not a member of this campus.")


def _paginate(view, request, queryset, to_dict):
    paginator = StandardPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response([to_dict(r) for r in page])


class _SafetyView(APIView):
    permission_classes = [IsAuthenticated]
    write_throttle = CampusSafetyWriteThrottle

    def get_throttles(self):
        if self.request.method in ("POST", "PUT", "PATCH", "DELETE"):
            return [self.write_throttle()]
        return super().get_throttles()


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------
class SafetyOverviewView(_SafetyView):
    def get(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.view")
        last = CampusBackup.objects.filter(campus=campus, status=CampusBackup.Status.READY).order_by("-created_at").first()
        week = timezone.now() - timedelta(days=7)
        consent = consent_svc.summary(campus)
        return Response({
            "campus": str(campus.id),
            "can_manage": user_can(request.user, campus.id, "safety.manage"),
            "can_restore": _is_campus_admin(request.user, campus),
            "backups": {
                "count": CampusBackup.objects.filter(campus=campus, status=CampusBackup.Status.READY).count(),
                "last": backup_svc.row_dict(last) if last else None,
            },
            "deletion_requests": deletion_svc.open_counts(campus),
            "consent": {
                "policy_version": consent["policy_version"],
                "required_missing": consent["required_missing"],
            },
            "audit": {"rows_last_7_days": CampusAuditLog.objects.filter(campus=campus, created_at__gte=week).count()},
        })


def _is_campus_admin(user, campus) -> bool:
    profile = get_staff_profile(user, campus.id)
    return bool(profile and profile.role == StaffProfile.Role.ADMIN)


# ---------------------------------------------------------------------------
# backups
# ---------------------------------------------------------------------------
class BackupListCreateView(_SafetyView):
    def get(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.view")
        qs = CampusBackup.objects.filter(campus=campus).select_related("created_by")
        if request.query_params.get("kind") in rules.BACKUP_KINDS:
            qs = qs.filter(kind=request.query_params["kind"])
        if request.query_params.get("status") in CampusBackup.Status.values:
            qs = qs.filter(status=request.query_params["status"])
        return _paginate(self, request, qs, backup_svc.row_dict)

    def post(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.manage")
        groups = request.data.get("groups") or [rules.GROUP_SETUP]
        if not isinstance(groups, list):
            raise ValidationError({"groups": "Send a list, e.g. [\"setup\", \"records\"]."})
        try:
            backup = backup_svc.create_backup(
                campus, groups=groups, kind=rules.KIND_MANUAL, user=request.user, note=request.data.get("note", ""))
        except backup_svc.BackupError as exc:
            raise SafetyAPIError(exc)
        log_action(campus.id, request.user, "backup.create", target=backup,
                   summary=f"Backup created ({', '.join(backup.groups)})", size_bytes=backup.size_bytes,
                   rows=sum(backup.row_counts.values()))
        backup_svc.prune_backups(campus)
        return Response(backup_svc.row_dict(backup), status=http.HTTP_201_CREATED)


def _get_backup(campus, backup_id) -> CampusBackup:
    try:
        uuid.UUID(str(backup_id))
    except ValueError:
        raise Http404
    try:
        return CampusBackup.objects.select_related("created_by").get(pk=backup_id, campus=campus)
    except CampusBackup.DoesNotExist:
        raise Http404


class BackupDetailView(_SafetyView):
    def get(self, request, campus_id, backup_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.view")
        return Response(backup_svc.row_dict(_get_backup(campus, backup_id)))

    def delete(self, request, campus_id, backup_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.manage")
        backup = _get_backup(campus, backup_id)
        label = f"{backup.kind} backup of {backup.created_at:%Y-%m-%d %H:%M}"
        pk = backup.pk
        backup_svc.delete_backup(backup)
        log_action(campus.id, request.user, "backup.delete", target_type="CampusBackup", target_id=pk,
                   summary=f"Deleted {label}")
        return Response(status=http.HTTP_204_NO_CONTENT)


class BackupDownloadView(_SafetyView):
    def get(self, request, campus_id, backup_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.manage")
        backup = _get_backup(campus, backup_id)
        if backup.status != CampusBackup.Status.READY or not backup.file:
            raise SafetyAPIError(backup_svc.BackupError("backup_not_ready", 409))
        try:
            handle = backup.file.open("rb")
        except Exception:
            raise SafetyAPIError(backup_svc.BackupError("backup_file_missing", 410))
        log_action(campus.id, request.user, "backup.download", target=backup,
                   summary=f"Downloaded backup of {backup.created_at:%Y-%m-%d %H:%M}")
        response = FileResponse(handle, content_type="application/gzip")
        response["Content-Disposition"] = (
            f'attachment; filename="campus-backup_{backup.created_at:%Y-%m-%d_%H%M}.json.gz"')
        response["Cache-Control"] = "no-store"
        response["X-Backup-SHA256"] = backup.sha256
        return response


class BackupRestoreView(_SafetyView):
    write_throttle = CampusSafetyRestoreThrottle

    def post(self, request, campus_id, backup_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.manage")
        if not _is_campus_admin(request.user, campus):
            raise PermissionDenied("Only the campus admin can restore a backup.")
        backup = _get_backup(campus, backup_id)
        dry_run = _truthy(request.data.get("dry_run"), default=True)
        groups = request.data.get("groups") or []
        if not isinstance(groups, list):
            raise ValidationError({"groups": "Send a list, e.g. [\"setup\"]."})
        try:
            report = backup_svc.restore_backup(
                backup, groups=groups, mode=request.data.get("mode") or rules.MODE_MISSING_ONLY,
                confirm=request.data.get("confirm", ""), dry_run=dry_run, user=request.user)
        except backup_svc.BackupError as exc:
            raise SafetyAPIError(exc)
        if not dry_run:
            totals = report["totals"]
            log_action(campus.id, request.user, "backup.restore", target=backup,
                       summary=(f"Restored backup of {backup.created_at:%Y-%m-%d %H:%M}: {totals['created']} created, "
                                f"{totals['updated']} updated, {totals['failed']} failed"),
                       mode=report["mode"], groups=report["groups"], totals=totals,
                       pre_restore_backup=report["pre_restore_backup"])
        return Response(report)


# ---------------------------------------------------------------------------
# data delete requests
# ---------------------------------------------------------------------------
def _get_request(campus, request_id) -> CampusDataDeletionRequest:
    try:
        uuid.UUID(str(request_id))
    except ValueError:
        raise Http404
    try:
        return CampusDataDeletionRequest.objects.select_related("requester", "subject", "reviewed_by").get(
            pk=request_id, campus=campus)
    except CampusDataDeletionRequest.DoesNotExist:
        raise Http404


def _can_see_request(user, campus, req) -> bool:
    return user_can(user, campus.id, "safety.view") or user.pk in (req.requester_id, req.subject_id)


class DeletionRequestListCreateView(_SafetyView):
    write_throttle = CampusDataRequestThrottle

    def get(self, request, campus_id):
        campus = _campus(campus_id)
        _member(request.user, campus)
        is_admin = user_can(request.user, campus.id, "safety.view")
        qs = deletion_svc.queryset_for(campus, request.user, request.query_params, is_admin=is_admin)
        now = timezone.now()
        return _paginate(self, request, qs, lambda r: deletion_svc.row_dict(r, for_admin=is_admin, now=now))

    def post(self, request, campus_id):
        campus = _campus(campus_id)
        _member(request.user, campus)
        student = None
        if request.data.get("student") not in (None, ""):
            student = User.objects.filter(pk=request.data.get("student")).first()
            if student is None:
                raise ValidationError({"student": "Unknown student."})
        try:
            req = deletion_svc.request_deletion(
                campus, request.user, kind=request.data.get("kind", ""), reason=request.data.get("reason", ""),
                student=student, wants_account_deletion=_truthy(request.data.get("wants_account_deletion")))
        except deletion_svc.DeletionError as exc:
            raise SafetyAPIError(exc)
        return Response(deletion_svc.row_dict(req, for_admin=False), status=http.HTTP_201_CREATED)


class DeletionRequestDetailView(_SafetyView):
    def get(self, request, campus_id, request_id):
        campus = _campus(campus_id)
        req = _get_request(campus, request_id)
        if not _can_see_request(request.user, campus, req):
            raise Http404
        return Response(deletion_svc.row_dict(req, for_admin=user_can(request.user, campus.id, "safety.view")))


class DeletionRequestPreviewView(_SafetyView):
    def get(self, request, campus_id, request_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.view")
        return Response(deletion_svc.preview(_get_request(campus, request_id)))


class DeletionRequestActionView(_SafetyView):
    """approve / reject / execute (admin) · cancel (requester)."""

    action = None
    write_throttle = CampusSafetyWriteThrottle

    def get_throttles(self):
        if self.action == "cancel":
            return [CampusDataRequestThrottle()]
        return super().get_throttles()

    def post(self, request, campus_id, request_id):
        campus = _campus(campus_id)
        req = _get_request(campus, request_id)
        user = request.user
        try:
            if self.action == "cancel":
                if not _can_see_request(user, campus, req):
                    raise Http404
                req = deletion_svc.cancel(req, user)
            else:
                _need(user, campus, "safety.manage")
                if self.action == "approve":
                    req = deletion_svc.approve(req, user, request.data.get("note", ""))
                elif self.action == "reject":
                    req = deletion_svc.reject(req, user, request.data.get("note", ""))
                elif self.action == "execute":
                    req = deletion_svc.execute(req, user)
                else:  # pragma: no cover - the URLconf only wires the four above
                    raise Http404
        except deletion_svc.DeletionError as exc:
            raise SafetyAPIError(exc)
        req = _get_request(campus, request_id)
        return Response(deletion_svc.row_dict(req, for_admin=user_can(user, campus.id, "safety.view")))


# ---------------------------------------------------------------------------
# consent log
# ---------------------------------------------------------------------------
def _student_param(raw):
    if raw in (None, ""):
        return None
    student = User.objects.filter(pk=raw).first()
    if student is None:
        raise ValidationError({"student": "Unknown student."})
    return student


class ConsentMeView(_SafetyView):
    write_throttle = CampusDataRequestThrottle

    def get(self, request, campus_id):
        campus = _campus(campus_id)
        _member(request.user, campus)
        return Response(consent_svc.my_state(request.user, campus))

    def post(self, request, campus_id):
        campus = _campus(campus_id)
        _member(request.user, campus)
        try:
            row = consent_svc.record(
                campus, request.user, request.data.get("purpose", ""), request.data.get("granted"),
                student=_student_param(request.data.get("student")), version=request.data.get("policy_version"),
                source=rules.SOURCE_APP, recorded_by=None)
        except consent_svc.ConsentError as exc:
            raise SafetyAPIError(exc)
        return Response(consent_svc.row_dict(row), status=http.HTTP_201_CREATED)


class ConsentLogView(_SafetyView):
    def get(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.view")
        return _paginate(self, request, consent_svc.log_queryset(campus, request.query_params), consent_svc.row_dict)


class ConsentLogExportView(_SafetyView):
    def get_throttles(self):
        return [CampusReportExportThrottle()]

    def get(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.view")
        body, rows, truncated = consent_svc.build_csv(consent_svc.log_queryset(campus, request.query_params))
        log_action(campus.id, request.user, "consent.export", target_type="Campus", target_id=campus.id,
                   summary=f"Exported {rows} consent log rows" + (" (cut at the row limit)" if truncated else ""),
                   rows=rows, truncated=truncated)
        response = HttpResponse(body, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="consent-log_{timezone.localdate():%Y-%m-%d}.csv"'
        response["Cache-Control"] = "no-store"
        response["X-Row-Count"] = str(rows)
        response["X-Truncated"] = "1" if truncated else "0"
        return response


class ConsentSummaryView(_SafetyView):
    def get(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.view")
        return Response(consent_svc.summary(campus))


class ConsentAdminRecordView(_SafetyView):
    def post(self, request, campus_id):
        campus = _campus(campus_id)
        _need(request.user, campus, "safety.manage")
        subject = User.objects.filter(pk=request.data.get("subject")).first() if request.data.get("subject") else None
        if subject is None:
            raise ValidationError({"subject": "Pick the person this consent belongs to."})
        try:
            row = consent_svc.record(
                campus, subject, request.data.get("purpose", ""), request.data.get("granted"),
                student=_student_param(request.data.get("student")), version=request.data.get("policy_version"),
                source=rules.SOURCE_ADMIN, recorded_by=request.user, note=request.data.get("note", ""))
        except consent_svc.ConsentError as exc:
            raise SafetyAPIError(exc)
        log_action(campus.id, request.user, "consent.record", target=row,
                   summary=f"Consent {row.status} ({row.purpose}) entered for {subject.username}",
                   purpose=row.purpose, status=row.status)
        return Response(consent_svc.row_dict(row), status=http.HTTP_201_CREATED)
