# campus/child_privacy.py
"""
PARENT / CHILD PRIVACY + MINORS' CONSENT — the database side (rules: `privacy_rules.py`, HTTP: `privacy_views.py`).

  * `get_policy()` / `save_policy()`   per-campus switches (`CampusPrivacyPolicy`), audit-logged
  * `compliance_rows()` / `compliance_summary()`   admin: which students are minors, who still has no guardian
                                       consent, who has withdrawn, whose date of birth is missing
  * `child_detail()`                   admin: one child, with EACH linked parent's answer (the access is audit-logged)
  * `my_children()`                    parent: each linked child with the combined decision and the parent's OWN
                                       answer; the other parent's identity / answer is never shown
  * `remind_parents()`                 admin: push to parents whose child still needs a (new) consent
  * `allowed()`                        the gate other features call before using a student's data for something
                                       optional (photo / media sharing, AI). With the campus switch OFF it always says yes.

Consent rows themselves are written by `consent.record()` (append-only `CampusConsentRecord`); this file only reads them.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from . import bridge
from . import consent as consent_svc
from . import privacy_rules as rules
from . import safety_rules
from .audit import log_action
from .bridge import NotifTypes
from .models import CampusAuditLog, CampusConsentRecord, CampusParentLink, CampusPrivacyPolicy, StudentEnrollment
from .substitutes import display_name


class PrivacyError(Exception):
    """`code` is a short machine code the app maps to a human message."""

    def __init__(self, code: str, status: int = 400, detail: str = ""):
        super().__init__(code)
        self.code, self.status, self.detail = code, status, detail


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------
def get_policy(campus) -> dict:
    row = CampusPrivacyPolicy.objects.filter(campus=campus).first()
    return {
        "enforce_guardian_consent": bool(row.enforce_guardian_consent) if row else False,
        "unknown_dob_is_minor": bool(row.unknown_dob_is_minor) if row else True,
        "updated_at": row.updated_at if row else None,
        "adult_age": rules.ADULT_AGE,
    }


def save_policy(campus, user, data) -> dict:
    """Both keys optional; each must be a real boolean (a string like "false" would be truthy, so it is refused)."""
    if not isinstance(data, dict):
        raise PrivacyError("invalid_body")
    values = {}
    for key in ("enforce_guardian_consent", "unknown_dob_is_minor"):
        if key in data:
            if not isinstance(data[key], bool):
                raise PrivacyError("must_be_boolean", detail=f"{key} must be true or false.")
            values[key] = data[key]
    if not values:
        raise PrivacyError("nothing_to_change")
    row, _ = CampusPrivacyPolicy.objects.get_or_create(campus=campus)
    for key, value in values.items():
        setattr(row, key, value)
    row.updated_by = user
    row.save()
    log_action(campus.id, user, "privacy.policy", target=campus, summary="Changed the child-privacy settings", **values)
    return get_policy(campus)


# ---------------------------------------------------------------------------
# reading the consent log for children
# ---------------------------------------------------------------------------
def _student_status(user, today=None) -> str:
    return rules.age_status(getattr(user, "date_of_birth", None), today or timezone.localdate())


def _decisions(campus, links, purposes=rules.GUARDIAN_PURPOSES):
    """
    links: iterable of (parent_id, student_id).
    -> {(student_id, purpose): {parent_id: {"status","version"} | None}}  (latest answer of every LINKED parent;
    an answer from someone who is no longer linked to that child does not count).
    """
    links = list(links)
    by_student = {}
    for parent_id, student_id in links:
        by_student.setdefault(student_id, []).append(parent_id)
    out = {(s, p): {parent: None for parent in parents} for s, parents in by_student.items() for p in purposes}
    if not links:
        return out
    qs = CampusConsentRecord.objects.filter(
        campus=campus, purpose__in=purposes, student_id__in=list(by_student), subject_id__in={p for p, _ in links},
    )
    latest = safety_rules.latest_per_key(consent_svc._rows_of(qs))
    for (subject, student, purpose), row in latest.items():
        slot = out.get((student, purpose))
        if slot is not None and subject in slot:
            slot[subject] = {"status": row["status"], "version": row["version"], "created_at": row["created_at"]}
    return out


def _combine(decisions_for_key, current_version) -> str:
    return rules.resolve_guardian(list(decisions_for_key.values()), current_version)


# ---------------------------------------------------------------------------
# admin: compliance list + summary
# ---------------------------------------------------------------------------
def _enrollments(campus, params):
    qs = StudentEnrollment.objects.filter(
        section__school_class__campus=campus, status=StudentEnrollment.Status.ACTIVE,
    ).select_related("student", "section", "section__school_class")
    if params.get("section"):
        qs = qs.filter(section_id=params["section"])
    if params.get("school_class"):
        qs = qs.filter(section__school_class_id=params["school_class"])
    text = " ".join(str(params.get("q") or "").split())[:100]
    if text:
        qs = qs.filter(
            Q(student__username__icontains=text) | Q(student__first_name__icontains=text)
            | Q(student__last_name__icontains=text) | Q(roll_number__icontains=text)
        )
    return qs


def _build_rows(campus, params, *, include_adults: bool) -> list:
    policy = get_policy(campus)
    unknown_minor = policy["unknown_dob_is_minor"]
    today = timezone.localdate()
    current = consent_svc.policy_version()

    seen, enrollments = set(), []
    for en in _enrollments(campus, params):
        if en.student_id not in seen:  # one row per student even if two enrollments are open
            seen.add(en.student_id)
            enrollments.append(en)
    links = list(
        CampusParentLink.objects.filter(campus=campus, student_id__in=seen).values_list("parent_id", "student_id")
    )
    parents_of = {}
    for parent_id, student_id in links:
        parents_of.setdefault(student_id, []).append(parent_id)
    decisions = _decisions(campus, links)

    rows = []
    for en in enrollments:
        student = en.student
        status = _student_status(student, today)
        minor = rules.treated_as_minor(status, unknown_minor)
        if not include_adults and status == rules.AGE_ADULT:
            continue
        parent_count = len(parents_of.get(student.pk, ()))
        consent = {}
        for purpose in rules.GUARDIAN_PURPOSES:
            slot = decisions.get((student.pk, purpose), {})
            consent[purpose] = _combine(slot, current) if parent_count else rules.DEC_MISSING
        issues = rules.child_issues(status, unknown_minor, parent_count, consent[rules.REQUIRED_GUARDIAN_PURPOSE])
        dob = getattr(student, "date_of_birth", None)
        rows.append({
            "student": {"id": student.pk, "username": student.username, "name": display_name(student)},
            "section": {"id": str(en.section_id), "name": str(en.section)},
            "roll_number": en.roll_number,
            "age_status": status,
            "age": rules.age_on(dob, today) if dob else None,
            "treated_as_minor": minor,
            "parent_count": parent_count,
            "consent": consent,
            "issues": issues,
        })
    rows.sort(key=lambda r: (r["student"]["name"].lower(), str(r["student"]["id"])))
    return rows


def compliance_rows(campus, params) -> dict:
    """Paginated list. Default = minors and students without a date of birth (adults are left out unless
    `include_adults=1`). Filters: `issue` (codes or `any`), `age_status`, `section`, `school_class`, `q`."""
    try:
        wanted = rules.clean_issue_filter(params.get("issue"))
    except ValueError:
        raise PrivacyError("invalid_issue")
    status_filter = str(params.get("age_status") or "").strip()
    if status_filter and status_filter not in rules.AGE_STATUSES:
        raise PrivacyError("invalid_age_status")
    include_adults = str(params.get("include_adults") or "").lower() in ("1", "true", "yes") or status_filter == rules.AGE_ADULT

    rows = _build_rows(campus, params, include_adults=include_adults)
    if status_filter:
        rows = [r for r in rows if r["age_status"] == status_filter]
    if wanted == ["any"]:
        rows = [r for r in rows if r["issues"]]
    elif wanted:
        rows = [r for r in rows if set(wanted) & set(r["issues"])]

    page, size = rules.clean_page(params)
    start = page * size - size
    chunk = rows[start:start + size]
    return {
        "count": len(rows), "page": page, "page_size": size,
        "next": page + 1 if start + size < len(rows) else None,
        "previous": page - 1 if page > 1 else None,
        "results": chunk,
    }


def compliance_summary(campus) -> dict:
    rows = _build_rows(campus, {}, include_adults=True)
    out = {
        "campus": str(campus.id), "policy": get_policy(campus), "generated_at": timezone.now(),
        "students": len(rows), "minors": 0, "adults": 0, "dob_missing": 0,
        "no_linked_parent": 0, "consent_missing": 0, "consent_withdrawn": 0, "consent_stale": 0, "covered": 0,
        "needs_attention": 0,
    }
    for r in rows:
        out["minors" if r["age_status"] == rules.AGE_MINOR else "adults" if r["age_status"] == rules.AGE_ADULT else "dob_missing"] += 1
        for code in r["issues"]:
            if code != rules.ISSUE_DOB_MISSING:
                out[code] += 1
        if r["issues"]:
            out["needs_attention"] += 1
        elif r["treated_as_minor"]:
            out["covered"] += 1
    return out


# ---------------------------------------------------------------------------
# admin: one child (who exactly answered) — accessing a minor's consent detail is itself audit-logged
# ---------------------------------------------------------------------------
def _active_enrollment(campus, student_id):
    return (
        StudentEnrollment.objects.filter(
            student_id=student_id, section__school_class__campus=campus, status=StudentEnrollment.Status.ACTIVE
        ).select_related("student", "section", "section__school_class").first()
    )


def child_detail(campus, student_id, actor) -> dict:
    en = _active_enrollment(campus, student_id)
    if en is None:
        raise PrivacyError("student_not_in_campus", 404)
    student = en.student
    policy = get_policy(campus)
    current = consent_svc.policy_version()
    parent_links = list(CampusParentLink.objects.filter(campus=campus, student=student).select_related("parent"))
    links = [(link.parent_id, student.pk) for link in parent_links]
    decisions = _decisions(campus, links)
    status = _student_status(student)
    per_purpose = []
    for purpose in rules.GUARDIAN_PURPOSES:
        slot = decisions.get((student.pk, purpose), {})
        per_purpose.append({
            "purpose": purpose,
            "decision": _combine(slot, current) if links else rules.DEC_MISSING,
            "parents": [
                {
                    "id": link.parent_id, "name": display_name(link.parent),
                    "status": (slot.get(link.parent_id) or {}).get("status"),
                    "policy_version": (slot.get(link.parent_id) or {}).get("version"),
                    "answered_at": (slot.get(link.parent_id) or {}).get("created_at"),
                }
                for link in parent_links
            ],
        })
    required = next(p["decision"] for p in per_purpose if p["purpose"] == rules.REQUIRED_GUARDIAN_PURPOSE)
    log_action(campus.id, actor, "privacy.child_view", target_type="User", target_id=student.pk,
               summary=f"Opened the consent detail of {display_name(student)}")
    return {
        "student": {"id": student.pk, "username": student.username, "name": display_name(student)},
        "section": {"id": str(en.section_id), "name": str(en.section)},
        "age_status": status,
        "treated_as_minor": rules.treated_as_minor(status, policy["unknown_dob_is_minor"]),
        "policy_version": current,
        "parent_count": len(parent_links),
        "purposes": per_purpose,
        "issues": rules.child_issues(status, policy["unknown_dob_is_minor"], len(parent_links), required),
    }


# ---------------------------------------------------------------------------
# parent: my children
# ---------------------------------------------------------------------------
def my_children(user, campus) -> dict:
    """
    For each child linked to the caller: the combined decision per purpose and the caller's OWN latest answer.
    `other_guardian_differs` only says that another linked parent's answer changes the result — it never names
    that parent or shows what they chose (parent <-> parent privacy).
    """
    policy = get_policy(campus)
    current = consent_svc.policy_version()
    my_links = list(CampusParentLink.objects.filter(campus=campus, parent=user).select_related("student"))
    student_ids = [link.student_id for link in my_links]
    all_links = list(
        CampusParentLink.objects.filter(campus=campus, student_id__in=student_ids).values_list("parent_id", "student_id")
    )
    decisions = _decisions(campus, all_links)
    out = []
    for link in my_links:
        student = link.student
        status = _student_status(student)
        items = []
        for purpose in rules.GUARDIAN_PURPOSES:
            slot = decisions.get((student.pk, purpose), {})
            combined = _combine(slot, current)
            mine = slot.get(user.pk)
            own = _combine({user.pk: mine}, current) if mine else rules.DEC_MISSING
            items.append({
                "purpose": purpose,
                "decision": combined,
                "my_answer": own,
                "other_guardian_differs": len(slot) > 1 and combined != own and combined != rules.DEC_MISSING,
            })
        out.append({
            "student": {"id": student.pk, "name": display_name(student)},
            "age_status": status,
            "treated_as_minor": rules.treated_as_minor(status, policy["unknown_dob_is_minor"]),
            "purposes": items,
        })
    return {"campus": str(campus.id), "policy_version": current, "children": out}


# ---------------------------------------------------------------------------
# admin: remind parents
# ---------------------------------------------------------------------------
def remind_parents(campus, actor) -> dict:
    """Notify parents whose child still has `consent_missing` / `consent_stale` (a withdrawal is respected, never nagged).
    At most once per REMINDER_COOLDOWN_HOURS per campus (read from the audit log, so it survives restarts)."""
    since = timezone.now() - timedelta(hours=rules.REMINDER_COOLDOWN_HOURS)
    if CampusAuditLog.objects.filter(campus=campus, action="privacy.remind", created_at__gte=since).exists():
        raise PrivacyError("reminder_too_soon", 429)
    rows = _build_rows(campus, {}, include_adults=False)
    targets = {r["student"]["id"] for r in rows if {rules.ISSUE_CONSENT_MISSING, rules.ISSUE_CONSENT_STALE} & set(r["issues"])}
    parent_ids = set(
        CampusParentLink.objects.filter(campus=campus, student_id__in=targets).values_list("parent_id", flat=True)
    )
    if parent_ids:
        bridge.notify(
            users=parent_ids, notif_type=NotifTypes.CAMPUS_SAFETY,
            title="Please confirm consent for your child",
            body="Open Privacy & my data to review how the school uses your child's data.",
            data={"type": NotifTypes.CAMPUS_SAFETY, "event": "consent_reminder", "campus_id": str(campus.id)},
            push=True,
        )
    log_action(campus.id, actor, "privacy.remind", target=campus,
               summary=f"Reminded {len(parent_ids)} parent(s) about consent for {len(targets)} child(ren)",
               parents=len(parent_ids), children=len(targets))
    return {"parents": len(parent_ids), "children": len(targets)}


# ---------------------------------------------------------------------------
# the gate other features call
# ---------------------------------------------------------------------------
def allowed(campus, student, purpose) -> bool:
    """
    May an OPTIONAL feature use `student`'s data for `purpose` (`photo_media` or `ai_features`)?

        if not child_privacy.allowed(campus, student, "photo_media"):
            ...leave the student's photo out...

    With the campus switch off this is always True. When on: a minor needs the linked parents' combined decision
    to be granted; an adult needs their own latest answer to be granted. A purpose that is not gated is True.
    """
    if purpose not in (rules.PURPOSE_PHOTO, rules.PURPOSE_AI):
        return True
    policy = get_policy(campus)
    if not policy["enforce_guardian_consent"]:
        return True
    status = _student_status(student)
    minor = rules.treated_as_minor(status, policy["unknown_dob_is_minor"])
    current = consent_svc.policy_version()
    if minor:
        guardian_purpose = rules.FEATURE_TO_GUARDIAN[purpose]
        links = list(CampusParentLink.objects.filter(campus=campus, student=student).values_list("parent_id", "student_id"))
        if not links:
            return False
        slot = _decisions(campus, links, purposes=(guardian_purpose,)).get((student.pk, guardian_purpose), {})
        return rules.feature_allowed(True, True, _combine(slot, current), None)
    row = (
        CampusConsentRecord.objects.filter(campus=campus, subject=student, student__isnull=True, purpose=purpose)
        .order_by("-created_at").values("status", "policy_version").first()
    )
    own = row["status"] if row else None
    return rules.feature_allowed(True, False, rules.DEC_MISSING, own)
