"""
T3.7 -- operational health: shared check functions + the public probe endpoints.

  GET /healthz/        liveness  -- process is up (no dependencies; for restart decisions)
  GET /readyz/         readiness -- DB + cache round-trip (for load balancer / UptimeRobot)
  GET /healthz/deep/   needs HEALTHCHECK_TOKEN (header X-Health-Token or ?token=); adds Celery
                       worker ping, backup freshness, last restore drill, disk space.
                       404 when HEALTHCHECK_TOKEN is unset, so nothing leaks by default.

The same functions back `manage.py check_system_health` (systemd timer, Sentry alert).
Error details are logged, never returned to anonymous callers.
"""
import hmac
import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import Http404, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)

# ---- thresholds (override in settings.py / env) ----------------------------------------------
BACKUP_MAX_AGE_HOURS = float(os.getenv("BACKUP_MAX_AGE_HOURS", "26"))
DRILL_MAX_AGE_DAYS = float(os.getenv("DRILL_MAX_AGE_DAYS", "8"))
DISK_MIN_FREE_PCT = float(os.getenv("DISK_MIN_FREE_PCT", "10"))


def backup_dir() -> Path:
    return Path(os.getenv("BACKUP_DIR") or getattr(settings, "BACKUP_DIR", "/var/backups/learnscroll"))


# ---- individual checks: each returns (ok: bool, detail: str) ---------------------------------
def check_database():
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        logger.error("health: database check failed: %r", exc)
        return False, "database unreachable"


def check_cache():
    try:
        key = "ops:health:probe"
        cache.set(key, "1", timeout=5)
        if cache.get(key) != "1":
            raise RuntimeError("cache round-trip mismatch")
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        logger.error("health: cache check failed: %r", exc)
        return False, "cache unreachable"


def check_celery(timeout: float = 3.0):
    try:
        from LearnScroll.celery import app

        replies = app.control.ping(timeout=timeout) or []
    except Exception as exc:  # noqa: BLE001
        logger.error("health: celery broker check failed: %r", exc)
        return False, "broker unreachable"
    if not replies:
        return False, "no Celery worker answered a ping"
    return True, f"{len(replies)} worker(s) online"


def check_disk(paths=None):
    problems = []
    for p in paths or [backup_dir(), Path(settings.MEDIA_ROOT)]:
        try:
            if not Path(p).exists():
                continue
            u = shutil.disk_usage(p)
            free_pct = 100.0 * u.free / u.total
            if free_pct < DISK_MIN_FREE_PCT:
                problems.append(f"{p}: only {free_pct:.1f}% free")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{p}: {exc!r}")
    return (not problems), ("ok" if not problems else "; ".join(problems))


def _read_marker(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def check_backups(now=None):
    """Postgres + media backups fresh and successful, restore drill recent and passing."""
    now = now or datetime.now(dt_timezone.utc)
    root = backup_dir()
    problems = []

    def fresh(marker, label, max_age):
        if not marker:
            problems.append(f"{label}: no marker found in {root} (never ran?)")
            return
        if marker.get("status") != "ok":
            problems.append(f"{label}: last run status={marker.get('status')!r}")
        ts = _parse_ts(marker.get("finished_at"))
        if ts is None:
            problems.append(f"{label}: marker has no finished_at")
        elif now - ts > max_age:
            hours = (now - ts).total_seconds() / 3600
            problems.append(f"{label}: last success {hours:.1f}h ago (limit {max_age.total_seconds() / 3600:.0f}h)")

    fresh(_read_marker(root / "postgres" / "LATEST.json"), "postgres backup", timedelta(hours=BACKUP_MAX_AGE_HOURS))
    media = _read_marker(root / "media" / "LATEST.json")
    fresh(media, "media backup", timedelta(hours=BACKUP_MAX_AGE_HOURS))
    drill = _read_marker(root / "drills" / "LAST_DRILL.json")
    fresh(drill, "restore drill", timedelta(days=DRILL_MAX_AGE_DAYS))
    pg = _read_marker(root / "postgres" / "LATEST.json")
    if pg and pg.get("remote_uploaded") is False and os.getenv("BACKUP_REMOTE_REQUIRED", "true").lower() == "true":
        problems.append("postgres backup: not uploaded off-site (BACKUP_REMOTE unset or upload failed)")
    return (not problems), ("ok" if not problems else "; ".join(problems))


# ---- HTTP views ------------------------------------------------------------------------------
@never_cache
@require_GET
def healthz(request):
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def readyz(request):
    checks, healthy = {}, True
    for name, fn in (("database", check_database), ("cache", check_cache)):
        ok, _ = fn()
        checks[name] = "ok" if ok else "error"
        healthy = healthy and ok
    return JsonResponse({"status": "ok" if healthy else "error", "checks": checks}, status=200 if healthy else 503)


@never_cache
@require_GET
def healthz_deep(request):
    expected = getattr(settings, "HEALTHCHECK_TOKEN", "") or os.getenv("HEALTHCHECK_TOKEN", "")
    if not expected:
        raise Http404()
    given = request.headers.get("X-Health-Token") or request.GET.get("token") or ""
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise Http404()
    checks, healthy = {}, True
    for name, fn in (("database", check_database), ("cache", check_cache), ("celery", check_celery),
                     ("disk", check_disk), ("backups", check_backups)):
        ok, detail = fn()
        checks[name] = {"ok": ok, "detail": detail}
        healthy = healthy and ok
    return JsonResponse({"status": "ok" if healthy else "error", "checks": checks}, status=200 if healthy else 503)
