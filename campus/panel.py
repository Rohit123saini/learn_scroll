# campus/panel.py
"""
[T4 §B] Campus Control Panel back-end: overview counts, guided-setup status,
teacher x class/subject assignment matrix (+ bulk direct-assign with conflict
warnings) and CSV bulk import (dry-run + per-row error report).

Every function here is plain (no request/response objects) so it is unit-
testable; `campus/views.py` only does permission checks + JSON.
"""
import csv
import io
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from login.models import User

from . import bridge
from .audit import log_action
from .bridge import NotifTypes
from .models import (
    AcademicSession,
    CampusLiveSession,
    CampusParentLink,
    ClassTeacherassigments,
    Department,
    Notice,
    SchoolClass,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
    SubjectTeacherassigments,
    TimetableEntry,
)
from .roster import RosterError, active_count, enroll_student, transfer_student

TEACHING_ROLES = (
    StaffProfile.Role.CLASS_TEACHER,
    StaffProfile.Role.SUBJECT_TEACHER,
    StaffProfile.Role.MODERATOR,
)
ASSIGNABLE_ROLES = TEACHING_ROLES + (StaffProfile.Role.PRINCIPAL_HOD, StaffProfile.Role.ADMIN)

MAX_IMPORT_ROWS = 5000
MAX_IMPORT_BYTES = 2 * 1024 * 1024


def _warn_sections():
    return int(getattr(settings, "CAMPUS_TEACHER_WARN_SECTIONS", 6))


def current_session(campus):
    return AcademicSession.objects.filter(campus=campus, is_current=True).first()


# ---------------------------------------------------------------------------
# Setup wizard status + overview
# ---------------------------------------------------------------------------
def setup_status(campus):
    """Ordered wizard steps: session -> departments -> classes -> sections ->
    subjects -> staff -> assignments -> timetable. `done` is derived from the
    data (never stored), so the wizard resumes wherever the admin left off."""
    session = current_session(campus)
    classes = SchoolClass.objects.filter(campus=campus, session=session) if session else SchoolClass.objects.none()
    sections = Section.objects.filter(school_class__in=classes)
    staff = StaffProfile.objects.filter(campus=campus, is_active=True).exclude(role=StaffProfile.Role.ADMIN)
    assigned = ClassTeacherassigments.objects.filter(section__in=sections).count() + (
        SubjectTeacherassigments.objects.filter(
            section__in=sections, status=SubjectTeacherassigments.Status.APPROVED
        ).count()
    )
    timetable = TimetableEntry.objects.filter(session=session).count() if session else 0
    counts = [
        ("session", 1 if session else 0, False),
        ("departments", Department.objects.filter(campus=campus).count(), True),  # optional (school has none)
        ("classes", classes.count(), False),
        ("sections", sections.count(), False),
        ("subjects", Subject.objects.filter(campus=campus).count(), False),
        ("staff", staff.count(), False),
        ("assignments", assigned, False),
        ("timetable", timetable, False),
    ]
    steps = [{"key": k, "count": n, "done": n > 0, "optional": opt} for k, n, opt in counts]
    required = [s for s in steps if not s["optional"]]
    next_step = next((s["key"] for s in required if not s["done"]), None)
    return {
        "steps": steps,
        "completed": sum(1 for s in required if s["done"]),
        "total": len(required),
        "is_complete": next_step is None,
        "next_step": next_step,
    }


def overview(campus):
    session = current_session(campus)
    sections = (
        Section.objects.filter(school_class__campus=campus, school_class__session=session)
        if session else Section.objects.none()
    )
    per_section = {}
    if session:
        for row in (
            StudentEnrollment.objects.filter(
                section__in=sections, session=session, status=StudentEnrollment.Status.ACTIVE
            ).values("section_id").annotate(n=Count("id"))
        ):
            per_section[row["section_id"]] = row["n"]
    full = near = 0
    for sec in sections.only("id", "capacity"):
        if sec.capacity:
            n = per_section.get(sec.id, 0)
            if n >= sec.capacity:
                full += 1
            elif n * 10 >= sec.capacity * 9:
                near += 1
    staff_by_role = {r.value: 0 for r in StaffProfile.Role}
    for row in StaffProfile.objects.filter(campus=campus, is_active=True).values("role").annotate(n=Count("id")):
        staff_by_role[row["role"]] = row["n"]
    now = timezone.now()
    return {
        "campus": str(campus.id),
        "session": {"id": str(session.id), "name": session.name} if session else None,
        "counts": {
            "departments": Department.objects.filter(campus=campus).count(),
            "classes": SchoolClass.objects.filter(campus=campus, session=session).count() if session else 0,
            "sections": sections.count(),
            "subjects": Subject.objects.filter(campus=campus).count(),
            "students": sum(per_section.values()),
            "parents": CampusParentLink.objects.filter(campus=campus).count(),
            "staff": staff_by_role,
        },
        "attention": {
            "pending_subject_requests": SubjectTeacherassigments.objects.filter(
                section__in=sections, status=SubjectTeacherassigments.Status.PENDING
            ).count(),
            "sections_without_class_teacher": sections.filter(class_teacher_assigments__isnull=True).count(),
            "sections_full": full,
            "sections_near_capacity": near,
        },
        "activity": {
            "live_sessions_next_7_days": CampusLiveSession.objects.filter(
                section__in=sections, scheduled_at__gte=now, scheduled_at__lt=now + timedelta(days=7)
            ).count(),
            "notices_last_30_days": Notice.objects.filter(
                campus=campus, created_at__gte=now - timedelta(days=30)
            ).count() if hasattr(Notice, "created_at") else None,
        },
        "setup": setup_status(campus),
    }


# ---------------------------------------------------------------------------
# Assignment matrix
# ---------------------------------------------------------------------------
def _section_scope_q(hod_department_id):
    return Q(school_class__department_id=hod_department_id) if hod_department_id else Q()


def assignment_matrix(campus, *, hod_department_id=None):
    session = current_session(campus)
    sections = (
        Section.objects.filter(school_class__campus=campus, school_class__session=session)
        .filter(_section_scope_q(hod_department_id)).select_related("school_class").order_by("school_class__name", "name")
        if session else Section.objects.none()
    )
    section_ids = [s.id for s in sections]
    classes = {}
    for sec in sections:
        sc = sec.school_class
        entry = classes.setdefault(
            str(sc.id), {"id": str(sc.id), "name": sc.name,
                         "department": str(sc.department_id) if sc.department_id else None, "sections": []},
        )
        entry["sections"].append({"id": str(sec.id), "name": sec.name})
    subjects = Subject.objects.filter(campus=campus)
    if hod_department_id:
        subjects = subjects.filter(Q(department_id=hod_department_id) | Q(department__isnull=True))

    staff = (
        StaffProfile.objects.filter(campus=campus, is_active=True, role__in=ASSIGNABLE_ROLES)
        .select_related("user", "department")
        .order_by("role", "user__username")
    )
    ct = {}
    for a in ClassTeacherassigments.objects.filter(section_id__in=section_ids):
        ct.setdefault(a.staff_id, []).append(str(a.section_id))
    sub = {}
    for a in SubjectTeacherassigments.objects.filter(section_id__in=section_ids).exclude(
        status=SubjectTeacherassigments.Status.REJECTED
    ):
        sub.setdefault(a.staff_id, []).append(
            {"assignment": str(a.id), "section": str(a.section_id), "subject": str(a.subject_id), "status": a.status}
        )
    periods = {
        row["staff_id"]: row["n"]
        for row in TimetableEntry.objects.filter(session=session).values("staff_id").annotate(n=Count("id"))
    } if session else {}

    rows = []
    for sp in staff:
        mine_ct = ct.get(sp.id, [])
        mine_sub = sub.get(sp.id, [])
        distinct_sections = set(mine_ct) | {x["section"] for x in mine_sub if x["status"] == "approved"}
        warnings = []
        if len(mine_ct) > 1:
            warnings.append("class_teacher_of_multiple_sections")
        if len(distinct_sections) > _warn_sections():
            warnings.append("heavy_load")
        if hod_department_id and sp.department_id not in (None, hod_department_id) and not (mine_ct or mine_sub):
            continue
        rows.append({
            "staff": str(sp.id),
            "user": {"id": sp.user_id, "username": sp.user.username,
                     "first_name": sp.user.first_name, "last_name": sp.user.last_name},
            "role": sp.role,
            "department": str(sp.department_id) if sp.department_id else None,
            "class_teacher_of": mine_ct,
            "subjects": mine_sub,
            "load": {"sections": len(distinct_sections), "timetable_periods": periods.get(sp.id, 0)},
            "warnings": warnings,
        })
    return {
        "campus": str(campus.id),
        "session": str(session.id) if session else None,
        "columns": {
            "classes": list(classes.values()),
            "subjects": [{"id": str(s.id), "name": s.name, "code": s.code,
                          "department": str(s.department_id) if s.department_id else None} for s in subjects],
        },
        "rows": rows,
    }


def bulk_assign(campus, actor, items, *, dry_run=False, replace=False, hod_department_id=None):
    """Admin/principal direct assignment (approval skipped). `items` =
    [{"staff", "section", "kind": "class_teacher"|"subject", "subject"?}].
    Per item status: created | updated | unchanged | conflict | error. Valid
    items are applied even when others conflict (reported per item). With
    dry_run=True nothing is written."""
    results = []
    touched_sections = {}
    planned_ct = {}       # section_id -> staff_id (class teacher decided earlier in this batch)
    planned_subject = {}  # (section_id, subject_id) -> staff_id
    actor_staff = StaffProfile.objects.filter(campus=campus, user=actor, is_active=True).first()

    for idx, item in enumerate(items):
        res = {"index": idx, "status": "error", "detail": "", "warnings": []}
        results.append(res)
        kind = (item.get("kind") or "").strip()
        staff = StaffProfile.objects.filter(pk=item.get("staff"), campus=campus, is_active=True).select_related(
            "user"
        ).first() if item.get("staff") else None
        section = Section.objects.filter(
            pk=item.get("section"), school_class__campus=campus
        ).select_related("school_class").first() if item.get("section") else None
        if kind not in ("class_teacher", "subject"):
            res["detail"] = "kind must be 'class_teacher' or 'subject'."
            continue
        if staff is None:
            res["detail"] = "Staff not found in this campus."
            continue
        if section is None:
            res["detail"] = "Section not found in this campus."
            continue
        if staff.role not in ASSIGNABLE_ROLES:
            res["detail"] = f"A {staff.role} can't be given a teaching assignment."
            continue
        if hod_department_id and section.school_class.department_id != hod_department_id:
            res["status"], res["detail"] = "conflict", "Section is outside your department."
            continue

        if kind == "class_teacher":
            existing = ClassTeacherassigments.objects.filter(section=section).first()
            batch_prev = planned_ct.get(section.pk)
            if batch_prev is not None and batch_prev != staff.pk:
                res["status"], res["detail"] = "conflict", "Another row in this request already sets this section's class teacher."
                continue
            others = ClassTeacherassigments.objects.filter(staff=staff).exclude(section=section).count()
            if others:
                res["warnings"].append("class_teacher_of_multiple_sections")
            if existing and existing.staff_id == staff.id:
                res["status"] = "unchanged"
                continue
            if existing and not replace:
                res["status"], res["detail"] = "conflict", "Section already has a class teacher (use replace)."
                continue
            res["status"] = "updated" if existing else "created"
            planned_ct[section.pk] = staff.pk
            if not dry_run:
                with transaction.atomic():
                    if existing:
                        existing.staff = staff
                        existing.save(update_fields=["staff"])
                    else:
                        ClassTeacherassigments.objects.create(section=section, staff=staff)
                touched_sections[section.pk] = (section, staff)
            continue

        subject = Subject.objects.filter(pk=item.get("subject"), campus=campus).first() if item.get("subject") else None
        if subject is None:
            res["detail"] = "Subject not found in this campus."
            continue
        taken = SubjectTeacherassigments.objects.filter(
            section=section, subject=subject, status=SubjectTeacherassigments.Status.APPROVED
        ).exclude(staff=staff).first()
        mine = SubjectTeacherassigments.objects.filter(section=section, subject=subject, staff=staff).first()
        batch_prev = planned_subject.get((section.pk, subject.pk))
        if batch_prev is not None and batch_prev != staff.pk:
            res["status"] = "conflict"
            res["detail"] = f"Another row in this request already assigns {subject.name} in {section}."
            continue
        if taken and not replace:
            res["status"] = "conflict"
            res["detail"] = f"{subject.name} in {section} already has teacher {taken.staff.user.username} (use replace)."
            continue
        load = len({
            *SubjectTeacherassigments.objects.filter(
                staff=staff, status=SubjectTeacherassigments.Status.APPROVED
            ).values_list("section_id", flat=True),
            *ClassTeacherassigments.objects.filter(staff=staff).values_list("section_id", flat=True),
            section.pk,
        })
        if load > _warn_sections():
            res["warnings"].append("heavy_load")
        if mine and mine.status == SubjectTeacherassigments.Status.APPROVED and not taken:
            res["status"] = "unchanged"
            continue
        res["status"] = "updated" if (mine or taken) else "created"
        planned_subject[(section.pk, subject.pk)] = staff.pk
        if dry_run:
            continue
        with transaction.atomic():
            if taken:
                taken.status = SubjectTeacherassigments.Status.REJECTED
                taken.responded_at = timezone.now()
                taken.approved_by = actor_staff
                taken.save(update_fields=["status", "responded_at", "approved_by", "updated_at"])
            if mine:
                mine.status = SubjectTeacherassigments.Status.APPROVED
                mine.approved_by = actor_staff
                mine.responded_at = timezone.now()
                mine.save(update_fields=["status", "approved_by", "responded_at", "updated_at"])
            else:
                SubjectTeacherassigments.objects.create(
                    section=section, subject=subject, staff=staff,
                    status=SubjectTeacherassigments.Status.APPROVED,
                    approved_by=actor_staff, responded_at=timezone.now(),
                )
        touched_sections[section.pk] = (section, staff)
        bridge.notify(
            users=[staff.user], notif_type=NotifTypes.STAFF_assigments_APPROVED,
            title="Subject assigned",
            body=f"You've been assigned {subject.name} for {section}.",
        )

    if not dry_run:
        for section, staff in touched_sections.values():
            ct = ClassTeacherassigments.objects.filter(section=section).select_related("staff__user").first()
            if ct is not None:
                try:  # idempotent: first class teacher -> create the section group
                    bridge.create_section_group(section, actor=ct.staff.user)
                except ValueError:
                    pass
            bridge.sync_section_group(section)
        summary = {s: sum(1 for r in results if r["status"] == s) for s in ("created", "updated", "unchanged", "conflict", "error")}
        log_action(campus.id, actor, "assignment.bulk", summary=f"bulk assign: {summary}", **summary)
    return {
        "dry_run": dry_run,
        "results": results,
        "summary": {s: sum(1 for r in results if r["status"] == s) for s in ("created", "updated", "unchanged", "conflict", "error")},
    }


# ---------------------------------------------------------------------------
# Bulk import (CSV)
# ---------------------------------------------------------------------------
IMPORT_KINDS = ("staff", "students", "subjects", "enrollments")
IMPORT_COLUMNS = {
    "staff": ["username", "role", "department"],
    "students": ["username", "class", "section", "roll_number", "enrollment_no"],
    "subjects": ["name", "code", "department"],
    "enrollments": ["username", "class", "section"],
}


def _read_csv(uploaded):
    raw = uploaded.read()
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("File too large (max 2 MB).")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("File must be UTF-8 encoded CSV.")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV is empty.")
    reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames]
    rows = []
    for i, row in enumerate(reader, start=2):  # row 1 = header
        rows.append((i, {k: (v or "").strip() for k, v in row.items() if k}))
        if len(rows) > MAX_IMPORT_ROWS:
            raise ValueError(f"Too many rows (max {MAX_IMPORT_ROWS}).")
    return reader.fieldnames, rows


def bulk_import(campus, actor, kind, uploaded, *, dry_run=True, override_capacity=False):
    """CSV import. Default dry_run=True (validate only). Valid rows are applied
    on a real run; invalid rows are reported with their CSV row number."""
    if kind not in IMPORT_KINDS:
        raise ValueError(f"kind must be one of {', '.join(IMPORT_KINDS)}.")
    header, rows = _read_csv(uploaded)
    needed = IMPORT_COLUMNS[kind][: 1 if kind in ("staff", "subjects") else 3]
    missing = [c for c in needed if c not in header]
    if missing:
        raise ValueError(f"Missing column(s): {', '.join(missing)}. Expected: {', '.join(IMPORT_COLUMNS[kind])}.")

    session = current_session(campus)
    depts = {d.name.lower(): d for d in Department.objects.filter(campus=campus)}
    sections = {}
    if session:
        for sec in Section.objects.filter(school_class__campus=campus, school_class__session=session).select_related(
            "school_class"
        ):
            sections[(sec.school_class.name.lower(), sec.name.lower())] = sec
    planned = {}  # section_id -> extra seats consumed by this dry-run
    report_rows, created, skipped, errors = [], 0, 0, []

    def err(rown, msg):
        errors.append({"row": rown, "error": msg})
        report_rows.append({"row": rown, "status": "error", "detail": msg})

    for rown, row in rows:
        try:
            if kind == "subjects":
                name = row.get("name", "")
                if not name:
                    err(rown, "name is required."); continue
                dept = None
                if row.get("department"):
                    dept = depts.get(row["department"].lower())
                    if dept is None:
                        err(rown, f"Unknown department '{row['department']}'."); continue
                if Subject.objects.filter(campus=campus, name__iexact=name).exists():
                    skipped += 1
                    report_rows.append({"row": rown, "status": "skipped", "detail": "subject exists"}); continue
                if not dry_run:
                    Subject.objects.create(campus=campus, name=name, code=row.get("code", ""), department=dept)
                created += 1
                report_rows.append({"row": rown, "status": "created", "detail": name}); continue

            user = User.objects.filter(username__iexact=row.get("username", "")).first() if row.get("username") else None
            if user is None:
                err(rown, f"User '{row.get('username', '')}' not found."); continue

            if kind == "staff":
                role = (row.get("role") or "").strip().lower()
                allowed = [r.value for r in StaffProfile.Role if r != StaffProfile.Role.ADMIN]
                if role not in allowed:
                    err(rown, f"role must be one of {', '.join(allowed)}."); continue
                dept = None
                if row.get("department"):
                    dept = depts.get(row["department"].lower())
                    if dept is None:
                        err(rown, f"Unknown department '{row['department']}'."); continue
                if StaffProfile.objects.filter(campus=campus, user=user).exists():
                    skipped += 1
                    report_rows.append({"row": rown, "status": "skipped", "detail": "already staff"}); continue
                if not dry_run:
                    StaffProfile.objects.create(campus=campus, user=user, role=role, department=dept)
                created += 1
                report_rows.append({"row": rown, "status": "created", "detail": f"{user.username} as {role}"}); continue

            # students / enrollments -> need class + section
            if not session:
                err(rown, "Campus has no current academic session."); continue
            sec = sections.get((row.get("class", "").lower(), row.get("section", "").lower()))
            if sec is None:
                err(rown, f"Section '{row.get('class', '')} / {row.get('section', '')}' not found in current session."); continue

            if kind == "students":
                existing = StudentEnrollment.objects.filter(student=user, section=sec, session=session).first()
                if existing and existing.status == StudentEnrollment.Status.ACTIVE:
                    skipped += 1
                    report_rows.append({"row": rown, "status": "skipped", "detail": "already enrolled"}); continue
                if dry_run:
                    cap = sec.capacity
                    used = active_count(sec, session) + planned.get(sec.id, 0)
                    if cap is not None and used >= cap and not override_capacity:
                        err(rown, f"Section {sec.name} is full ({used}/{cap})."); continue
                    planned[sec.id] = planned.get(sec.id, 0) + 1
                else:
                    enroll_student(
                        section=sec, student=user, session=session, roll_number=row.get("roll_number", ""),
                        enrollment_no=row.get("enrollment_no", ""), actor=actor, override_capacity=override_capacity,
                    )
                created += 1
                report_rows.append({"row": rown, "status": "created", "detail": f"{user.username} -> {sec}"}); continue

            # enrollments: move an ACTIVE student into the given section
            cur = StudentEnrollment.objects.filter(
                student=user, session=session, status=StudentEnrollment.Status.ACTIVE,
                section__school_class__campus=campus,
            ).select_related("section").first()
            if cur is None:
                err(rown, "Student has no active enrollment (use kind=students)."); continue
            if cur.section_id == sec.id:
                skipped += 1
                report_rows.append({"row": rown, "status": "skipped", "detail": "already in section"}); continue
            if dry_run:
                cap = sec.capacity
                used = active_count(sec, session) + planned.get(sec.id, 0)
                if cap is not None and used >= cap and not override_capacity:
                    err(rown, f"Section {sec.name} is full ({used}/{cap})."); continue
                planned[sec.id] = planned.get(sec.id, 0) + 1
            else:
                transfer_student(cur, sec, actor=actor, override_capacity=override_capacity)
            created += 1
            report_rows.append({"row": rown, "status": "created", "detail": f"{user.username}: {cur.section} -> {sec}"})
        except RosterError as exc:
            err(rown, exc.message)
        except Exception as exc:  # noqa: BLE001 - one bad row never aborts the file
            err(rown, f"Unexpected error: {exc}")

    out = {
        "kind": kind, "dry_run": dry_run, "total": len(rows), "created": created, "skipped": skipped,
        "error_count": len(errors), "errors": errors[:500], "rows": report_rows[:500],
    }
    if not dry_run:
        log_action(
            campus.id, actor, f"import.{kind}", summary=f"bulk import {kind}: +{created} skipped {skipped} errors {len(errors)}",
            created=created, skipped=skipped, errors=len(errors), total=len(rows),
        )
    return out
