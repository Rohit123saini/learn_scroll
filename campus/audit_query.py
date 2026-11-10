# campus/audit_query.py
"""
AUDIT LOG — filter + search + facets + CSV export for `CampusAuditLog` (the log itself, `campus/audit.py`,
is unchanged and still append-only).

  GET /campus/campuses/<id>/audit-log/          list (paginated, newest first)
  GET /campus/campuses/<id>/audit-log/facets/   what to offer in the filter pickers (actions, categories, people)
  GET /campus/campuses/<id>/audit-log/export/   the same filters as a CSV file (max 10 000 rows)

Filters (all optional, combine with AND):
  q            search: summary, action, target type / id, and the actor's username / first / last name
  action       one or more comma separated action PREFIXES  (`fee` matches `fee_waive`, `fee.refund`; `section.` all of section)
  category     the part before the first dot  (`section`, `enrollment`, `safety`, `attendance` ...); comma separated
  actor        user id
  target_type  exact (`Section`, `StudentEnrollment` ...)
  target_id    exact
  since/until  `YYYY-MM-DD` (until = END of that day) or an ISO date-time
  order        `desc` (default, newest first) | `asc`
Access: `audit.view` (admin / principal) — the view checks it; nothing here does.
"""
from __future__ import annotations

import csv
import io
from collections import Counter

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from login.models import User

from . import safety_rules as rules
from .substitutes import display_name

CSV_COLUMNS = ("time", "actor", "actor_name", "action", "target_type", "target_id", "summary")


def _aware(naive):
    return timezone.make_aware(naive) if naive is not None and timezone.is_naive(naive) else naive


def _split(raw):
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def filter_audit(qs, params):
    """Apply the query-string filters to a `CampusAuditLog` queryset. 400 on bad input."""
    errors = {}

    prefixes = _split(params.get("action"))
    if prefixes:
        cond = Q()
        for prefix in prefixes:
            cond |= Q(action__startswith=prefix[:60])
        qs = qs.filter(cond)

    categories = _split(params.get("category"))
    if categories:
        cond = Q()
        for cat in categories:
            cond |= Q(action=cat[:60]) | Q(action__startswith=cat[:50] + ".")
        qs = qs.filter(cond)

    actor = params.get("actor")
    if actor:
        try:
            qs = qs.filter(actor_id=User._meta.pk.to_python(actor))
        except Exception:
            errors["actor"] = "Invalid user id."

    if params.get("target_type"):
        qs = qs.filter(target_type=params["target_type"][:40])
    if params.get("target_id"):
        qs = qs.filter(target_id=str(params["target_id"])[:64])

    since = until = None
    try:
        since = rules.parse_audit_bound(params.get("since"))
    except ValueError:
        errors["since"] = "Use YYYY-MM-DD or an ISO date-time."
    try:
        until = rules.parse_audit_bound(params.get("until"), end=True)
    except ValueError:
        errors["until"] = "Use YYYY-MM-DD or an ISO date-time."
    if not errors:
        try:
            rules.check_range(since, until)
        except ValueError as exc:
            errors["since"] = str(exc)
    if errors:
        raise ValidationError(errors)
    if since is not None:
        qs = qs.filter(created_at__gte=_aware(since))
    if until is not None:
        qs = qs.filter(created_at__lte=_aware(until))

    text = rules.clean_search(params.get("q"))
    if text:
        qs = qs.filter(
            Q(summary__icontains=text)
            | Q(action__icontains=text)
            | Q(target_type__icontains=text)
            | Q(target_id__icontains=text)
            | Q(actor__username__icontains=text)
            | Q(actor__first_name__icontains=text)
            | Q(actor__last_name__icontains=text)
        )

    order = (params.get("order") or "desc").lower()
    if order not in ("asc", "desc"):
        raise ValidationError({"order": "Use asc or desc."})
    return qs.order_by("created_at" if order == "asc" else "-created_at", "-id" if order == "desc" else "id")


def row_dict(r) -> dict:
    actor = None
    if r.actor_id:
        actor = {"id": r.actor_id, "username": r.actor.username, "name": display_name(r.actor)}
    return {
        "id": str(r.id),
        "action": r.action,
        "category": rules.audit_category(r.action),
        "target_type": r.target_type,
        "target_id": r.target_id,
        "summary": r.summary,
        "metadata": r.metadata,
        "created_at": r.created_at,
        "actor": actor,
    }


def facets(campus_qs) -> dict:
    """Filter-picker content for the whole campus log (NOT narrowed by the current filters, so the pickers stay stable)."""
    actions = list(campus_qs.values("action").annotate(n=Count("id")).order_by("-n", "action")[:200])
    cats = Counter()
    for row in actions:
        cats[rules.audit_category(row["action"])] += row["n"]
    people = list(
        campus_qs.filter(actor__isnull=False)
        .values("actor_id", "actor__username", "actor__first_name", "actor__last_name")
        .annotate(n=Count("id"))
        .order_by("-n", "actor__username")[:100]
    )
    return {
        "total": sum(row["n"] for row in actions),
        "categories": [{"category": c, "count": n} for c, n in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))],
        "actions": [{"action": row["action"], "count": row["n"]} for row in actions],
        "actors": [
            {
                "id": row["actor_id"],
                "username": row["actor__username"],
                "name": (f'{row["actor__first_name"]} {row["actor__last_name"]}'.strip() or row["actor__username"]),
                "count": row["n"],
            }
            for row in people
        ],
    }


def build_csv(qs) -> tuple[bytes, int, bool]:
    """-> (bytes with a UTF-8 BOM so Excel shows Hindi names right, rows written, truncated?)."""
    limit = rules.AUDIT_EXPORT_MAX_ROWS
    rows = list(qs.select_related("actor")[: limit + 1])
    truncated = len(rows) > limit
    rows = rows[:limit]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        when = timezone.localtime(r.created_at).strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([
            when,
            rules.safe_cell(r.actor.username if r.actor_id else ""),
            rules.safe_cell(display_name(r.actor) if r.actor_id else ""),
            rules.safe_cell(r.action),
            rules.safe_cell(r.target_type),
            rules.safe_cell(r.target_id),
            rules.safe_cell(r.summary),
        ])
    return ("\ufeff" + buf.getvalue()).encode("utf-8"), len(rows), truncated
