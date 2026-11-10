"""
user_profile/report_sla.py - review deadlines (SLA) for user reports, and the staff queue that is sorted by them.
P-F15 (trust & safety), point 2.

  due_for(reason, now)          the deadline a NEW report gets (set by services.file_report)
  tighten(target_type, id)      a target that was auto-held / is being mass-reported must be looked at sooner
  sweep(now)                    hourly Celery task (user_profile.sweep_report_sla): stamps `sla_breached_at` ONCE on every open
                                report whose deadline has passed and sends staff ONE summary notification per run
  queue(...) / summary(...)     what the staff API (trust_views.StaffReportQueueView) returns

Deadlines (settings.py, env-tunable; hours):
  CHILD_SAFETY_SLA_HOURS     24   reason = child_safety (the case itself has its own SLA, see child_safety.py)
  REPORT_SLA_HOURS_SEVERE    24   nudity / violence / self_harm / hate / harassment, and anything that was auto-held
  REPORT_SLA_HOURS_NORMAL    72   everything else
  REPORT_SLA_SOON_HOURS       4   "due soon" bucket in the summary
Sort order = urgent first, then the earliest deadline (so an overdue report is always above a fresh one).
Copyright reports have their own SLA (COPYRIGHT_SLA_HOURS) and are not in this queue.
Everything here is best-effort and never raises into the caller (the sweep logs and carries on).
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import moderation_log as mlog

logger = logging.getLogger(__name__)

OPEN_STATES = ("open", "reviewed")
SEVERE_REASONS = ("nudity", "violence", "self_harm", "hate", "harassment", "child_safety")
MAX_ALERT_STAFF = 25


def _cfg(name, default):
    return getattr(settings, name, default)


def hours_for(reason):
    if reason == "child_safety":
        return float(_cfg("CHILD_SAFETY_SLA_HOURS", 24))
    if reason in SEVERE_REASONS:
        return float(_cfg("REPORT_SLA_HOURS_SEVERE", 24))
    return float(_cfg("REPORT_SLA_HOURS_NORMAL", 72))


def due_for(reason, now=None):
    return (now or timezone.now()) + timedelta(hours=hours_for(reason))


def tighten(target_type, target_id, now=None):
    """Pull every open report of this target down to the SEVERE deadline (never later than it already is).
    Called when the target was auto-held. Returns how many rows changed. Never raises."""
    from django.db.models import Q

    from .models import ContentReport

    try:
        now = now or timezone.now()
        cap = now + timedelta(hours=float(_cfg("REPORT_SLA_HOURS_SEVERE", 24)))
        return ContentReport.objects.filter(
            Q(sla_due_at__isnull=True) | Q(sla_due_at__gt=cap),
            target_type=target_type, target_id=str(target_id), status__in=OPEN_STATES,
        ).exclude(reason="copyright").update(sla_due_at=cap)
    except Exception:
        logger.exception("report sla: tighten failed (%s:%s)", target_type, target_id)
        return 0


def _staff_users():
    from django.contrib.auth import get_user_model

    return list(get_user_model().objects.filter(is_active=True, is_staff=True)[:MAX_ALERT_STAFF])


def sweep(now=None):
    """Hourly. -> number of reports newly marked as breached."""
    from .models import ContentReport

    now = now or timezone.now()
    try:
        late = ContentReport.objects.filter(
            status__in=OPEN_STATES, sla_due_at__lte=now, sla_breached_at__isnull=True,
        ).exclude(reason="copyright")
        ids = list(late.values_list("pk", flat=True)[:2000])
        if not ids:
            return 0
        ContentReport.objects.filter(pk__in=ids).update(sla_breached_at=now)
        rows = list(ContentReport.objects.filter(pk__in=ids).values_list("target_type", "target_id", "reason"))
        targets = {(t, i) for t, i, _r in rows}
        severe = sum(1 for _t, _i, r in rows if r in SEVERE_REASONS)
        title = "Reports past their review time"
        message = f"{len(targets)} reported item(s) are overdue" + (f", {severe} of them severe" if severe else "") + \
            ". Open the report queue (oldest first)."
        for u in _staff_users():
            mlog.notify(u, title, message, data={"kind": "report_sla_staff", "overdue_targets": len(targets)})
        logger.warning("report sla: %s report(s) on %s target(s) breached their deadline", len(ids), len(targets))
        return len(ids)
    except Exception:
        logger.exception("report sla: sweep failed")
        return 0


def _remaining_minutes(due, now):
    if due is None:
        return None
    return int((due - now).total_seconds() // 60)


def queue(*, include_child_safety=False, overdue_only=False, limit=50, now=None):
    """One row per reported target, most urgent first. Reporters are never exposed (counts and reasons only)."""
    from django.db.models import Count, Min

    from .models import ContentReport

    now = now or timezone.now()
    qs = ContentReport.objects.filter(status__in=OPEN_STATES).exclude(reason="copyright")
    if not include_child_safety:
        qs = qs.exclude(reason="child_safety")
    if overdue_only:
        qs = qs.filter(sla_due_at__lte=now)
    groups = (
        qs.values("target_type", "target_id")
        .annotate(n=Count("id"), due=Min("sla_due_at"), first=Min("created_at"))
        .order_by("due", "first")[: max(1, min(int(limit), 200))]
    )
    out = []
    for g in groups:
        rows = list(qs.filter(target_type=g["target_type"], target_id=g["target_id"])
                    .values_list("reason", "priority", "brigading_suspected", "reported_user_id"))
        by_reason = {}
        for reason, _p, _b, _u in rows:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        urgent = any(p == "urgent" for _r, p, _b, _u in rows)
        out.append({
            "target_type": g["target_type"], "target_id": g["target_id"],
            "reported_user_id": next((u for *_x, u in rows if u), None),
            "reports": g["n"], "by_reason": by_reason,
            "priority": "urgent" if urgent else "normal",
            "brigading_suspected": any(b for _r, _p, b, _u in rows),
            "first_reported_at": g["first"].isoformat() if g["first"] else None,
            "sla_due_at": g["due"].isoformat() if g["due"] else None,
            "sla_minutes_left": _remaining_minutes(g["due"], now),
            "overdue": bool(g["due"] and g["due"] <= now),
        })
    # urgent first, then the smallest time left (None = no deadline = last)
    out.sort(key=lambda r: (r["priority"] != "urgent",
                            r["sla_minutes_left"] if r["sla_minutes_left"] is not None else 10 ** 9))
    return out


def summary(*, include_child_safety=False, now=None):
    from .models import ContentReport

    now = now or timezone.now()
    qs = ContentReport.objects.filter(status__in=OPEN_STATES).exclude(reason="copyright")
    if not include_child_safety:
        qs = qs.exclude(reason="child_safety")
    soon = now + timedelta(hours=float(_cfg("REPORT_SLA_SOON_HOURS", 4)))
    oldest = qs.order_by("created_at").values_list("created_at", flat=True).first()
    return {
        "open": qs.count(),
        "overdue": qs.filter(sla_due_at__lte=now).count(),
        "due_soon": qs.filter(sla_due_at__gt=now, sla_due_at__lte=soon).count(),
        "urgent": qs.filter(priority="urgent").count(),
        "oldest_open_hours": round((now - oldest).total_seconds() / 3600, 1) if oldest else 0,
    }
