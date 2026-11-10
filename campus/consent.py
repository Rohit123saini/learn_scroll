# campus/consent.py
"""
CONSENT LOG — who agreed to what, when, under which policy text. Rules (purposes, scopes, versions) are in
`safety_rules.py`; this file is the DB side. Rows are append-only (`CampusConsentRecord`).

  * `record()`      one decision (grant / withdraw) by the person themselves, or typed in by staff from a paper form
  * `my_state()`    what the caller has answered so far (+ which answers are out of date and need re-asking)
  * `log_queryset()` / `row_dict()`   admin list with filters + search
  * `summary()`     counts per purpose for the admin hub (granted / withdrawn / still missing)

Withdrawing a consent is stored and shown; it does NOT switch a feature off by itself (that wiring is per
feature and is listed as open in STATUS_SAFETY.md).
"""
from __future__ import annotations

import csv
import io

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import privacy_rules
from . import safety_rules as rules
from .models import CampusConsentRecord, CampusParentLink, StaffProfile, StudentEnrollment
from .permissions import get_campus_role
from .substitutes import display_name


class ConsentError(Exception):
    """`code` is a short machine code the app maps to a human message."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def policy_version() -> str:
    return str(getattr(settings, "CAMPUS_CONSENT_POLICY_VERSION", rules.DEFAULT_POLICY_VERSION))


# ---------------------------------------------------------------------------
# who belongs to a campus
# ---------------------------------------------------------------------------
def member_ids(campus) -> set:
    """Active staff + actively enrolled students + linked parents of this campus."""
    ids = set(StaffProfile.objects.filter(campus=campus, is_active=True).values_list("user_id", flat=True))
    ids |= set(
        StudentEnrollment.objects.filter(
            section__school_class__campus=campus, status=StudentEnrollment.Status.ACTIVE
        ).values_list("student_id", flat=True)
    )
    ids |= set(CampusParentLink.objects.filter(campus=campus).values_list("parent_id", flat=True))
    return ids


def linked_children(user, campus) -> list:
    links = CampusParentLink.objects.filter(campus=campus, parent=user).select_related("student")
    return [link.student for link in links]


def _is_linked(parent, student_id, campus) -> bool:
    return CampusParentLink.objects.filter(campus=campus, parent=parent, student_id=student_id).exists()


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------
def record(campus, subject, purpose, granted, *, student=None, version=None, source=rules.SOURCE_APP,
           recorded_by=None, note="") -> CampusConsentRecord:
    """Append one consent decision. `subject` is the person the consent belongs to (the parent, for a child's)."""
    try:
        status, ver = rules.validate_consent(purpose, granted, student is not None, version, policy_version())
    except ValueError as exc:
        raise ConsentError(str(exc))
    if source not in rules.CONSENT_SOURCES:
        raise ConsentError("invalid_source")
    # PRIVACY: under-18 users cannot grant, for themselves, what only a parent may grant (`privacy_rules`). They may
    # always withdraw. A missing date of birth is not "minor" here (see login/age.py) — it is only flagged to the admin.
    if student is None and privacy_rules.minor_may_not_grant(
        privacy_rules.age_status(getattr(subject, "date_of_birth", None), timezone.localdate()), purpose, granted
    ):
        raise ConsentError("guardian_consent_required", 403)
    if get_campus_role(subject, campus.id) is None:
        raise ConsentError("not_a_member", 403)
    if student is not None and not _is_linked(subject, student.pk, campus):
        raise ConsentError("not_a_linked_parent", 403)
    return CampusConsentRecord.objects.create(
        campus=campus, subject=subject, student=student, purpose=purpose, status=status, policy_version=ver,
        source=source, recorded_by=recorded_by, note=(note or "")[: rules.NOTE_MAX_CONSENT],
    )


# ---------------------------------------------------------------------------
# read — the caller's own answers
# ---------------------------------------------------------------------------
def _rows_of(qs):
    return [
        {"subject": r["subject_id"], "student": r["student_id"], "purpose": r["purpose"], "status": r["status"],
         "version": r["policy_version"], "created_at": r["created_at"], "source": r["source"]}
        for r in qs.values("subject_id", "student_id", "purpose", "status", "policy_version", "created_at", "source")
    ]


def my_state(user, campus) -> dict:
    """One item per thing the caller can answer: self purposes once; child purposes once per linked child."""
    current = policy_version()
    latest = rules.latest_per_key(_rows_of(CampusConsentRecord.objects.filter(campus=campus, subject=user)))
    children = linked_children(user, campus)
    items = []

    def add(purpose, student):
        spec = rules.CONSENT_PURPOSES[purpose]
        row = latest.get((user.pk, student.pk if student else None, purpose))
        status = row["status"] if row else None
        items.append({
            "purpose": purpose,
            "required": spec["required"],
            "student": {"id": student.pk, "name": display_name(student)} if student else None,
            "status": status,
            "policy_version": row["version"] if row else None,
            "answered_at": row["created_at"] if row else None,
            "needs_update": bool(row) and rules.needs_reconsent(status, row["version"], current),
        })

    for purpose, spec in rules.CONSENT_PURPOSES.items():
        scope = spec["scope"]
        if scope == "self":
            add(purpose, None)
        elif scope == "child":
            for child in children:  # a person with no linked child has nothing to answer here
                add(purpose, child)
        else:  # either
            if children:
                for child in children:
                    add(purpose, child)
            else:
                add(purpose, None)
    return {"campus": str(campus.id), "policy_version": current, "items": items}


# ---------------------------------------------------------------------------
# read — admin
# ---------------------------------------------------------------------------
def log_queryset(campus, params):
    qs = CampusConsentRecord.objects.filter(campus=campus).select_related("subject", "student", "recorded_by")
    if params.get("purpose"):
        qs = qs.filter(purpose__in=[p for p in str(params["purpose"]).split(",") if p])
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("source"):
        qs = qs.filter(source=params["source"])
    for key, field in (("subject", "subject_id"), ("student", "student_id")):
        if params.get(key):
            qs = qs.filter(**{field: params[key]})
    text = rules.clean_search(params.get("q"))
    if text:
        qs = qs.filter(
            Q(subject__username__icontains=text) | Q(subject__first_name__icontains=text)
            | Q(subject__last_name__icontains=text) | Q(student__username__icontains=text)
            | Q(student__first_name__icontains=text) | Q(student__last_name__icontains=text)
            | Q(note__icontains=text)
        )
    return qs


def row_dict(r: CampusConsentRecord) -> dict:
    def person(u):
        return {"id": u.pk, "username": u.username, "name": display_name(u)} if u else None

    return {
        "id": str(r.id),
        "purpose": r.purpose,
        "status": r.status,
        "policy_version": r.policy_version,
        "source": r.source,
        "note": r.note,
        "created_at": r.created_at,
        "subject": person(r.subject),
        "student": person(r.student),
        "recorded_by": person(r.recorded_by),
    }


def summary(campus) -> dict:
    """Per purpose: how many people / children answered yes, no, or not at all (and how many answers are stale)."""
    current = policy_version()
    rows = _rows_of(CampusConsentRecord.objects.filter(campus=campus))
    latest = rules.latest_per_key(rows)
    members = member_ids(campus)
    pairs = list(CampusParentLink.objects.filter(campus=campus).values_list("parent_id", "student_id"))

    out = []
    for purpose, spec in rules.CONSENT_PURPOSES.items():
        granted = withdrawn = stale = 0
        for (subject, student, p), row in latest.items():
            if p != purpose:
                continue
            if row["status"] == rules.CONSENT_GRANTED:
                granted += 1
                stale += 1 if rules.needs_reconsent(row["status"], row["version"], current) else 0
            else:
                withdrawn += 1
        if spec["scope"] == "self":
            missing = sum(1 for uid in members if (uid, None, purpose) not in latest)
            asked = len(members)
        elif spec["scope"] == "child":
            missing = sum(1 for parent, student in pairs if (parent, student, purpose) not in latest)
            asked = len(pairs)
        else:
            missing = sum(1 for parent, student in pairs if (parent, student, purpose) not in latest)
            asked = len(pairs)
        out.append({
            "purpose": purpose, "required": spec["required"], "scope": spec["scope"], "asked": asked,
            "granted": granted, "withdrawn": withdrawn, "missing": missing, "needs_update": stale,
        })
    return {
        "campus": str(campus.id),
        "policy_version": current,
        "generated_at": timezone.now(),
        "purposes": out,
        "required_missing": sum(p["missing"] for p in out if p["required"]),
    }


CSV_COLUMNS = ["When", "Purpose", "Decision", "Policy version", "Source", "Person", "Person name", "Child", "Child name",
               "Entered by", "Note"]


def build_csv(qs) -> tuple[bytes, int, bool]:
    """-> (bytes with a UTF-8 BOM so Excel shows Hindi names right, rows written, truncated?). Newest first."""
    limit = rules.AUDIT_EXPORT_MAX_ROWS
    rows = list(qs.select_related("subject", "student", "recorded_by")[: limit + 1])
    truncated = len(rows) > limit
    rows = rows[:limit]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([
            timezone.localtime(r.created_at).strftime("%Y-%m-%d %H:%M:%S"),
            rules.safe_cell(r.purpose),
            rules.safe_cell(r.status),
            rules.safe_cell(r.policy_version),
            rules.safe_cell(r.source),
            rules.safe_cell(r.subject.username if r.subject_id else ""),
            rules.safe_cell(display_name(r.subject) if r.subject_id else ""),
            rules.safe_cell(r.student.username if r.student_id else ""),
            rules.safe_cell(display_name(r.student) if r.student_id else ""),
            rules.safe_cell(r.recorded_by.username if r.recorded_by_id else ""),
            rules.safe_cell(r.note),
        ])
    return ("\ufeff" + buf.getvalue()).encode("utf-8"), len(rows), truncated
