# login/tasks.py
"""Housekeeping for sessions / refresh tokens / the auth audit log
(beat entries: "login-cleanup-stale-sessions", "login-purge-auth-audit-logs")."""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from .models import AuthAuditLog, AuthToken

logger = logging.getLogger(__name__)


@shared_task(name="login.cleanup_stale_sessions")
def cleanup_stale_sessions():
    """Delete sessions nobody touched for SLIDING_EXPIRY (they are dead already --
    SlidingSessionAuthentication would reject them) and flush expired rows from the
    refresh-token blacklist tables so they don't grow forever."""
    cutoff = timezone.now() - AuthToken.SLIDING_EXPIRY
    deleted, _ = AuthToken.objects.filter(last_used_at__lt=cutoff).delete()
    try:
        call_command("flushexpiredtokens")
    except Exception:
        logger.exception("flushexpiredtokens failed")
    return {"stale_sessions_deleted": deleted}


@shared_task(name="login.purge_auth_audit_logs")
def purge_auth_audit_logs():
    """L1 gap 19 -- delete audit rows older than AUTH_AUDIT_RETENTION_DAYS (default 180).

    ``0`` (or a negative value) keeps everything. Deletes in batches so a big backlog
    never holds one long lock on the table.
    """
    days = int(getattr(settings, "AUTH_AUDIT_RETENTION_DAYS", 180))
    if days <= 0:
        return {"audit_logs_deleted": 0, "skipped": "retention disabled"}
    cutoff = timezone.now() - timedelta(days=days)
    total = 0
    while True:
        ids = list(AuthAuditLog.objects.filter(created_at__lt=cutoff).values_list("pk", flat=True)[:5000])
        if not ids:
            break
        deleted, _ = AuthAuditLog.objects.filter(pk__in=ids).delete()
        total += deleted
    return {"audit_logs_deleted": total}


# ---------------------------------------------------------------------------
# S5 -- data rights (DPDP). Bodies live in login/data_rights.py.
# beat entries: "login-sweep-data-exports", "login-run-due-account-deletions"
# ---------------------------------------------------------------------------
@shared_task(name="login.build_data_export", soft_time_limit=1800, time_limit=1900)  # P-C3: big ZIP
def build_data_export(export_id):
    """Build the ZIP for one queued "Download my data" request (queued by the API right after it is created)."""
    from . import data_rights
    from .models import DataExportRequest

    exp = DataExportRequest.objects.select_related("user").filter(pk=export_id).first()
    if exp is None:
        return {"skipped": "missing"}
    if exp.status != DataExportRequest.STATUS_QUEUED:
        return {"skipped": exp.status}  # already built / being built by another worker
    data_rights.build_export(exp)
    exp.refresh_from_db(fields=["status"])
    return {"export_id": exp.pk, "status": exp.status}


@shared_task(name="login.sweep_data_exports")
def sweep_data_exports():
    """Every 30 min: expire old ZIPs, fail stuck jobs, and build requests whose `.delay()` never reached a worker."""
    from . import data_rights
    from .models import DataExportRequest

    out = data_rights.sweep_exports()
    stale = timezone.now() - timedelta(minutes=5)
    built = 0
    for exp in DataExportRequest.objects.select_related("user").filter(
        status=DataExportRequest.STATUS_QUEUED, requested_at__lt=stale
    )[:20]:
        data_rights.build_export(exp)
        built += 1
    out["built_late"] = built
    return out


@shared_task(name="login.run_due_account_deletions")
def run_due_account_deletions():
    """Daily: erase accounts whose 30-day grace ended + send the "3 days left" reminder once."""
    from . import data_rights

    return data_rights.run_due_deletions()


# ---------------------------------------------------------------------------
# S10 -- connected accounts. Body lives in login/connected_accounts.py.
# beat entry: "login-purge-old-qr-requests"
# ---------------------------------------------------------------------------
@shared_task(name="login.purge_old_qr_requests")
def purge_old_qr_requests():
    """Hourly: drop QR sign-in requests that can no longer do anything (expired / denied / used a day ago).
    Consumed rows whose session is still alive stay -- they feed the "Linked apps" list."""
    from . import connected_accounts

    return {"qr_requests_deleted": connected_accounts.purge_old_qr_requests()}
