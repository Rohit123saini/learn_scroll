# login/data_rights_views.py
"""
S5 -- DPDP data rights API (all under /login/auth/data/, login required).

  GET   overview/                      one call for the "Data & privacy" screen: latest export, deletion status,
                                       current consents, what is erased / kept, grievance-officer path
  GET   export/                        latest export (or null)
  POST  export/                        queue a new "Download my data" job            -> 202
  GET   export/<id>/download/          the ZIP (owner only, until expires_at)         -> 200 / 404 / 410
  GET   deletion/                      {pending, scheduled_for, days_left} + erased / kept text
  POST  deletion/cancel/               cancel a scheduled deletion                    -> 200 / 404
  GET   consents/                      current state per purpose + history
  POST  consents/ {purpose, granted}   append a consent row (withdraw marketing = SMS / WhatsApp / digest off)

Scheduling a deletion itself is POST /login/auth/delete-account/ (login/views.py::DeleteAccountView).

Errors use the login envelope (login/errors.py): {detail, code, message, ...}. Codes are listed in ERROR_CODES.md.
Written by reading the code only -- nothing was compiled or run.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import audit, data_rights
from .errors import UnifiedErrorsMixin, api_error, retry_error
from .models import DataExportRequest

logger = logging.getLogger(__name__)

GRIEVANCE_PATH = "/profile/grievance-officer/"


def _enqueue_export(export_id: int) -> None:
    """Hand the job to Celery after the row is committed. If the broker is down the row simply stays `queued`
    and the 30-minute sweeper (login.sweep_data_exports) builds it -- the request never fails because of that."""

    def _send():
        try:
            from .tasks import build_data_export

            build_data_export.delay(export_id)
        except Exception:
            logger.exception("data export %s: could not queue the build task (sweeper will pick it up)", export_id)

    transaction.on_commit(_send)


def _overview(user) -> dict:
    nxt = data_rights.next_export_allowed_at(user)
    return {
        "status": True,
        "export": data_rights.export_payload(data_rights.latest_export(user)),
        "can_request_export_at": nxt.isoformat() if nxt else None,
        "export_expiry_days": data_rights.export_expiry_days(),
        "deletion": data_rights.deletion_status(user),
        "consents": data_rights.current_consents(user),
        "erasure": data_rights.erasure_summary(),
        "grievance_path": GRIEVANCE_PATH,
        "notice_version": data_rights.notice_version(),
    }


class DataRightsOverviewView(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_overview(request.user))


class DataExportView(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "data_rights_write"

    def get_throttles(self):
        # reading the status is cheap and polled by the screen; only the POST is rate limited
        if self.request.method == "GET":
            return []
        return super().get_throttles()

    def get(self, request):
        nxt = data_rights.next_export_allowed_at(request.user)
        return Response(
            {
                "status": True,
                "export": data_rights.export_payload(data_rights.latest_export(request.user)),
                "can_request_export_at": nxt.isoformat() if nxt else None,
            }
        )

    def post(self, request):
        user = request.user
        try:
            with transaction.atomic():
                exp = data_rights.request_export(user)
                _enqueue_export(exp.pk)
        except data_rights.ExportRefused as e:
            if e.retry_after:
                return retry_error(str(e), e.code, e.retry_after, status.HTTP_429_TOO_MANY_REQUESTS)
            return api_error(str(e), e.code, status.HTTP_409_CONFLICT)
        audit.record(audit.Event.DATA_EXPORT_REQUESTED, request, user=user)
        return Response(
            {
                "status": True,
                "message": "We are preparing your data. You will see the download button here when it is ready.",
                "export": data_rights.export_payload(exp),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class DataExportDownloadView(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "data_rights_download"

    def get(self, request, pk):
        # owner only: somebody else's id is the same "not found" as a missing one
        exp = DataExportRequest.objects.filter(pk=pk, user=request.user).first()
        if exp is None:
            return api_error("This export does not exist.", "export_not_found", status.HTTP_404_NOT_FOUND)
        if exp.status in (DataExportRequest.STATUS_QUEUED, DataExportRequest.STATUS_PROCESSING):
            return api_error("Your export is still being prepared.", "export_not_ready", status.HTTP_409_CONFLICT)
        handle = data_rights.open_export_file(exp)
        if handle is None:
            return api_error(
                "This export has expired. Request a new one.", "export_expired", status.HTTP_410_GONE
            )
        audit.record(audit.Event.DATA_EXPORT_DOWNLOADED, request, user=request.user)
        stamp = timezone.now().strftime("%Y%m%d")
        response = FileResponse(
            handle, as_attachment=True, filename=f"learnscroll-my-data-{stamp}.zip", content_type="application/zip"
        )
        response["Cache-Control"] = "no-store"
        return response


class DeletionStatusView(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        body = {"status": True, "deletion": data_rights.deletion_status(request.user)}
        body["erasure"] = data_rights.erasure_summary()
        return Response(body)


class DeletionCancelView(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "data_rights_write"

    def post(self, request):
        if not data_rights.cancel_deletion(request.user):
            return api_error(
                "No account deletion is scheduled.", "deletion_not_scheduled", status.HTTP_404_NOT_FOUND
            )
        audit.record(audit.Event.ACCOUNT_DELETION_CANCELLED, request, user=request.user)
        return Response(
            {
                "status": True,
                "message": "Deletion cancelled. Your account stays as it is.",
                "deletion": data_rights.deletion_status(request.user),
            }
        )


class ConsentView(UnifiedErrorsMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "data_rights_write"

    def get_throttles(self):
        if self.request.method == "GET":
            return []
        return super().get_throttles()

    def get(self, request):
        return Response(
            {
                "status": True,
                "consents": data_rights.current_consents(request.user),
                "history": data_rights.consent_history(request.user),
                "notice_version": data_rights.notice_version(),
            }
        )

    def post(self, request):
        purpose = str(request.data.get("purpose", "")).strip().lower()
        granted = request.data.get("granted")
        if purpose not in data_rights.CONSENT_PURPOSES:
            return api_error("Unknown consent purpose.", "consent_purpose_invalid", status.HTTP_400_BAD_REQUEST)
        if isinstance(granted, str):
            granted = {"true": True, "1": True, "false": False, "0": False}.get(granted.strip().lower())
        if not isinstance(granted, bool):
            return api_error("`granted` must be true or false.", "consent_value_invalid", status.HTTP_400_BAD_REQUEST)

        current = {c["purpose"]: c["granted"] for c in data_rights.current_consents(request.user)}
        # same answer as the one on record -> nothing to write (keeps the history free of double taps)
        if current.get(purpose) is granted:
            return Response(
                {"status": True, "changed": False, "consents": data_rights.current_consents(request.user)}
            )
        # X3b: the first-run card says where the answer came from (consent history shows it); anything else = "app"
        source = str(request.data.get("source", "")).strip().lower()
        if source not in data_rights.CONSENT_SOURCES:
            source = "app"
        data_rights.record_consent(request.user, purpose, granted, source=source)
        audit.record(
            audit.Event.CONSENT_CHANGED, request, user=request.user, purpose=purpose, granted=granted
        )
        return Response(
            {
                "status": True,
                "changed": True,
                "consents": data_rights.current_consents(request.user),
                "history": data_rights.consent_history(request.user, limit=20),
            }
        )
