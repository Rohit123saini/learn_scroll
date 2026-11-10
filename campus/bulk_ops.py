"""
Teacher 2-tap flows — server side.

Do bulk operations, ek-ek HTTP call ke bajaye:

  * ``bulk_mark_attendance`` — "sab present, sirf exceptions bhejo".
    Pehle app 60 students ke liye 60 alag POST karta tha (aur har POST ka
    apna permission check + throttle hit). Ab ek request, ek transaction.
    Idempotent hai: usi din dobara save karne par rows UPDATE hoti hain,
    duplicate nahi bante.

  * ``bulk_upsert_results`` — ek table me saare students ke marks.
    All-or-nothing: agar koi row galat hai to kuch bhi save nahi hota aur
    response me row-wise errors aate hain, taaki teacher ek hi baar me
    theek karke dobara save kar sake. Draft (``publish=False``) ya publish
    (default), aur LOCKED sheet par koi write nahi (naye rows bhi nahi).
  * ``import_results_csv`` — CSV preview / commit (campus.results_csv).

Attendance galti-sudhaar: pehli baar mark karna free hai, lekin mark ho chuki
attendance badalne / clear karne par edit window + reason + audit log lagta hai
(``campus/attendance_edit.py``). Bina asli badlaav ke dobara save = no-op.

Permission check views me hota hai (``can_manage_section_subject``);
yahan sirf data validation + write.
"""
from decimal import Decimal, InvalidOperation

import uuid

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import attendance_edit, grading, results_csv
from .models import (
    Attendance,
    ExamTerm,
    ResultEntry,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
)

VALID_ATTENDANCE_STATUSES = {c for c, _ in Attendance.Status.choices}
MAX_ROWS = 500  # ek section me isse zyada students nahi hone chahiye


def _active_roster(section):
    return list(
        StudentEnrollment.objects.filter(
            section=section, status=StudentEnrollment.Status.ACTIVE
        ).select_related("student")
    )


def _is_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def resolve_section(section_id):
    section = (
        Section.objects.select_related("school_class")
        .filter(pk=section_id)
        .first()
        if section_id and _is_uuid(section_id)
        else None
    )
    if not section:
        raise ValidationError({"section": "Section not found."})
    return section


def resolve_subject(subject_id, campus_id, *, required=False):
    if not subject_id:
        if required:
            raise ValidationError({"subject": "This field is required."})
        return None
    subject = (
        Subject.objects.filter(pk=subject_id, campus_id=campus_id).first() if _is_uuid(subject_id) else None
    )
    if not subject:
        raise ValidationError({"subject": "Subject not found in this campus."})
    return subject


# ------------------------------------------------------------------
# Attendance
# ------------------------------------------------------------------

def attendance_day_snapshot(qs, *, section_id, date, subject_id, user=None, campus=None):
    """Ek din ka poora section attendance — ek query. ``qs`` pehle se
    viewer ke campus-scope tak narrow hona chahiye.

    ``user`` + ``campus`` diye jaaye to response me ``edit`` bhi aata hai
    (edit window / reason / override ka haal — ``attendance_edit.edit_info``).
    """
    qs = qs.filter(enrollment__section_id=section_id, date=date)
    qs = qs.filter(subject_id=subject_id) if subject_id else qs.filter(subject__isnull=True)
    rows = list(qs.only("enrollment_id", "status", "marked_at", "last_edited_at", "edit_count", "source"))
    records = {str(r.enrollment_id): r.status for r in rows}
    out = {"marked": bool(records), "records": records}
    # Rows filled in automatically by a live class (campus/live_class.py) — the app
    # tags them "Auto"; the first teacher edit flips a row to manual and drops it here.
    out["auto"] = [str(r.enrollment_id) for r in rows if r.source == "auto"]
    if user is not None and campus is not None:
        out["edit"] = attendance_edit.edit_info(rows, user=user, campus=campus)
    return out


@transaction.atomic
def bulk_mark_attendance(*, user, section, date, subject, default_status, overrides, reason=""):
    """
    ``default_status`` poore active roster ko lagta hai, ``overrides``
    ({enrollment_id: status}) usse upar jaate hain. Existing rows update,
    baaki create.

    NB: ``subject=None`` (poore din ki attendance) ke liye DB ka unique
    constraint NULL ko equal nahi maanta (Postgres), isliye duplicate se
    bachne ke liye hum khud isnull-filter se existing rows dhoondhte hain.

    Galti-sudhaar: agar koi MARK HO CHUKI row ka status sach me badal raha hai
    to ``attendance_edit.check_edit`` chalta hai — edit window (403
    ``edit_window_closed``), ``reason`` (400 ``reason_required``) — aur har
    badli row ka ``AttendanceEditLog`` likha jaata hai. Naye students /
    pehli baar ki marking par kuch nahi lagta.
    """
    if default_status not in VALID_ATTENDANCE_STATUSES:
        raise ValidationError({"default_status": f"Must be one of {sorted(VALID_ATTENDANCE_STATUSES)}."})
    if date > timezone.localdate():
        raise ValidationError({"date": "Cannot mark attendance for a future date."})

    roster = _active_roster(section)
    if not roster:
        raise ValidationError({"section": "This section has no active students."})
    if len(roster) > MAX_ROWS:
        raise ValidationError({"section": "Section too large for a single bulk save."})
    roster_ids = {str(e.id): e for e in roster}

    status_by_id = {eid: default_status for eid in roster_ids}
    errors = {}
    for eid, st in (overrides or {}).items():
        eid = str(eid)
        if eid not in roster_ids:
            errors[eid] = "Not an active student of this section."
        elif st not in VALID_ATTENDANCE_STATUSES:
            errors[eid] = f"Invalid status '{st}'."
        else:
            status_by_id[eid] = st
    if errors:
        raise ValidationError({"overrides": errors})

    campus = section.school_class.campus
    existing_qs = Attendance.objects.select_for_update().select_related("enrollment__student").filter(
        enrollment_id__in=list(roster_ids), date=date
    )
    existing_qs = (
        existing_qs.filter(subject=subject) if subject else existing_qs.filter(subject__isnull=True)
    )
    existing = {str(a.enrollment_id): a for a in existing_qs}

    to_create, to_update = [], []
    # (row, old_status, new_status) for rows whose status REALLY changes — the
    # edits that need window + reason + audit.
    changes = []
    # Enrollments that are NEW or CHANGED to absent in this save — the only ones
    # whose parents get a push (a re-save of an unchanged register notifies nobody).
    newly_absent = []
    for eid, st in status_by_id.items():
        row = existing.get(eid)
        if row is None:
            to_create.append(
                Attendance(
                    enrollment_id=eid, date=date, subject=subject, status=st, marked_by=user
                )
            )
            if st == Attendance.Status.ABSENT:
                newly_absent.append(eid)
        elif row.status != st:
            # Unchanged rows are left completely alone (incl. `marked_by`), so a
            # different teacher re-opening the register never rewrites history.
            changes.append((row, row.status, st))
            if st == Attendance.Status.ABSENT:
                newly_absent.append(eid)

    # Window + reason gate (raises -> whole save rolls back, nothing written).
    clean_reason, override = attendance_edit.check_edit(
        user=user, campus=campus, rows=[c[0] for c in changes], reason=reason
    )

    now = timezone.now()
    for row, _old, st in changes:
        row.status = st
        row.marked_by = user
        row.last_edited_at = now
        row.edit_count = (row.edit_count or 0) + 1
        # An automatic (live class) row the teacher changes becomes manual for good:
        # the live-class rebuild never overwrites a teacher's word.
        row.source = Attendance.Source.MANUAL
        to_update.append(row)

    if to_create:
        Attendance.objects.bulk_create(to_create)
    if to_update:
        Attendance.objects.bulk_update(to_update, ["status", "marked_by", "last_edited_at", "edit_count", "source"])
    attendance_edit.record_edits(
        user=user, campus=campus, section=section, date=date, subject=subject,
        changes=changes, reason=clean_reason, override=override, now=now,
    )

    # Rows that were ABSENT and no longer are: parents who already got the absent
    # push get a correction (see attendance_push.notify_parents_of_corrections).
    corrected = [
        str(row.enrollment_id) for row, old_status, st in changes
        if old_status == Attendance.Status.ABSENT and st != Attendance.Status.ABSENT
    ]
    if corrected:
        def _push_corrections(ids=tuple(corrected), day=date, subj=subject):
            from .attendance_push import notify_parents_of_corrections

            notify_parents_of_corrections(enrollment_ids=ids, date=day, subject=subj)

        transaction.on_commit(_push_corrections)

    if newly_absent:
        # Push to the absent children's parents only after the register is
        # really committed (a rolled-back save must not notify anyone).
        # Lazy import: attendance_push -> bridge -> core/message apps.
        def _push_parents(ids=tuple(newly_absent), day=date, subj=subject):
            from .attendance_push import notify_parents_of_absences

            notify_parents_of_absences(enrollment_ids=ids, date=day, subject=subj)

        transaction.on_commit(_push_parents)

    counts = {c: 0 for c in VALID_ATTENDANCE_STATUSES}
    for st in status_by_id.values():
        counts[st] += 1
    return {
        "date": date.isoformat(),
        "total": len(status_by_id),
        "created": len(to_create),
        "updated": len(to_update),
        "edit_override": override,
        **counts,
    }


@transaction.atomic
def clear_attendance_day(*, section, date, subject, user, reason=""):
    """"Undo" ke liye: us din (+subject) ki poori section attendance hatao —
    yaani din wapas \"mark nahi hua\" ho jaata hai. Sirf is section ke
    enrollments ki rows delete hoti hain.

    Mark ho chuki attendance hatana bhi edit hai: edit window + ``reason`` +
    audit log (``attendance_edit.check_edit``). App ka Undo ``reason="Undo"``
    bhejta hai."""
    campus = section.school_class.campus
    qs = Attendance.objects.select_for_update().select_related("enrollment__student").filter(
        enrollment__section=section, date=date
    )
    qs = qs.filter(subject=subject) if subject else qs.filter(subject__isnull=True)
    rows = list(qs)
    clean_reason, override = attendance_edit.check_edit(user=user, campus=campus, rows=rows, reason=reason)
    changes = [(r, r.status, None) for r in rows]
    # Log first (needs the rows' data), then delete — same transaction.
    attendance_edit.record_edits(
        user=user, campus=campus, section=section, date=date, subject=subject,
        changes=changes, reason=clean_reason, override=override,
    )
    cleared_absent = [str(r.enrollment_id) for r in rows if r.status == Attendance.Status.ABSENT]
    deleted, _ = Attendance.objects.filter(pk__in=[r.pk for r in rows]).delete()
    if cleared_absent:
        def _push_corrections(ids=tuple(cleared_absent), day=date, subj=subject):
            from .attendance_push import notify_parents_of_corrections

            notify_parents_of_corrections(enrollment_ids=ids, date=day, subject=subj)

        transaction.on_commit(_push_corrections)
    return {"date": date.isoformat(), "cleared": deleted, "edit_override": override}


# ------------------------------------------------------------------
# Results
# ------------------------------------------------------------------

def _dec(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("Not a number.")
    if not d.is_finite() or d < 0:
        raise ValueError("Must be 0 or more.")
    if d >= Decimal("10000"):  # DecimalField(max_digits=6, decimal_places=2)
        raise ValueError("Too large.")
    return d.quantize(Decimal("0.01"))


def resolve_exam_term(exam_term_id, campus_id):
    term = (
        ExamTerm.objects.select_related("session").filter(pk=exam_term_id).first()
        if exam_term_id and _is_uuid(exam_term_id)
        else None
    )
    if not term or term.session.campus_id != campus_id:
        raise ValidationError({"exam_term": "Exam term not found."})
    return term


def sheet_entries(section, subject, exam_term):
    """Poori sheet (section + subject + exam term) ke ResultEntry rows."""
    return ResultEntry.objects.filter(
        enrollment__section=section, subject=subject, exam_term=exam_term
    )


def sheet_locked(section, subject, exam_term):
    """Sheet LOCKED tab hai jab uski koi bhi row locked ho. Isi se "naye rows
    bhi add nahi ho sakte" rule bhi nikalta hai (lock ke baad roster me
    aaya naya student bhi sheet me add nahi hota — pehle unlock)."""
    return sheet_entries(section, subject, exam_term).filter(locked_at__isnull=False).exists()


def _assert_unlocked(section, subject, exam_term):
    if sheet_locked(section, subject, exam_term):
        raise ValidationError({"detail": "This sheet is locked. Ask an admin or principal to unlock it first."})


def _display_name(user):
    full = f"{user.first_name} {user.last_name}".strip()
    return full or user.username


def build_sheet(section, subject, exam_term, campus):
    """Roster + saved marks + state + stats, ek response. Staff-only data
    (draft rows included) — viewer ka staff hona view check karta hai."""
    roster = _active_roster(section)
    roster.sort(key=lambda e: (
        (0, int(e.roll_number), "") if e.roll_number.isdigit() else (1, 0, e.roll_number.lower()),
        _display_name(e.student).lower(),
    ))
    entries = {str(x.enrollment_id): x for x in sheet_entries(section, subject, exam_term)}
    scale, pass_percent = grading.campus_scale(campus), campus.pass_percent
    rows, pcts = [], []
    drafts = published = 0
    for e in roster:
        x = entries.get(str(e.id))
        row = {
            "enrollment": str(e.id), "student": str(e.student_id), "name": _display_name(e.student),
            "username": e.student.username, "roll_number": e.roll_number,
            "enrollment_no": e.enrollment_no, "entry": None,
        }
        if x is not None:
            pct = grading.percent_of(x.marks_obtained, x.max_marks)
            row["entry"] = {
                "id": str(x.id), "marks_obtained": float(x.marks_obtained), "max_marks": float(x.max_marks),
                "remarks": x.remarks, "status": x.status,
                "grade": grading.grade_for_percent(pct, scale),
                "passed": grading.is_pass(pct, pass_percent),
            }
            if pct is not None:
                pcts.append(float(pct))
            if x.status == ResultEntry.Status.DRAFT:
                drafts += 1
            else:
                published += 1
        rows.append(row)
    locked_row = next((x for x in entries.values() if x.locked_at), None)
    return {
        "section": str(section.id), "subject": str(subject.id), "exam_term": str(exam_term.id),
        "locked": locked_row is not None,
        "locked_at": locked_row.locked_at.isoformat() if locked_row else None,
        "grade_scale": grading.get_scale(campus.grade_scale), "pass_percent": float(pass_percent),
        "grading_system": getattr(campus, "grading_system", "cbse"),
        "rows": rows,
        "stats": {
            "students": len(roster), "entered": len(entries), "drafts": drafts, "published": published,
            "missing": len(roster) - len(entries),
            "average_percent": round(sum(pcts) / len(pcts), 2) if pcts else None,
            "highest_percent": round(max(pcts), 2) if pcts else None,
            "lowest_percent": round(min(pcts), 2) if pcts else None,
            "pass_count": sum(1 for p in pcts if grading.is_pass(p, pass_percent)),
        },
    }


@transaction.atomic
def bulk_upsert_results(*, user, section, subject, exam_term, default_max, rows, publish=True, dry_run=False):
    """
    ``rows``: ``[{enrollment, marks_obtained, max_marks?, remarks?}]``.
    ``marks_obtained`` khaali/null => wo student skip (marks abhi nahi
    aaye).

    ``publish=True`` (default, purana behaviour): rows PUBLISHED. ``False``:
    naye rows DRAFT; maujooda rows apna status rakhti hain (published row
    ko edit se chupaya nahi jaata — uske liye ``unpublish``). LOCKED sheet par
    koi write nahi. ``dry_run=True``: sab validation, koi write nahi.

    Returns ``(summary, notify_entries)`` — ``notify_entries`` sirf wo
    PUBLISHED rows jinke liye student ko batana chahiye (nayi, badli, ya
    abhi draft se publish hui).
    """
    if exam_term.session.campus_id != section.school_class.campus_id:
        raise ValidationError({"exam_term": "Exam term belongs to a different campus."})
    if len(rows) > MAX_ROWS:
        raise ValidationError({"rows": "Too many rows."})
    _assert_unlocked(section, subject, exam_term)

    try:
        default_max_d = _dec(default_max) if default_max not in (None, "") else Decimal("100.00")
    except ValueError as e:
        raise ValidationError({"max_marks": str(e)})
    if default_max_d is None or default_max_d <= 0:
        raise ValidationError({"max_marks": "Must be greater than 0."})

    roster = {str(e.id): e for e in _active_roster(section)}
    errors, parsed = {}, []
    seen = set()
    for r in rows:
        eid = str(r.get("enrollment", ""))
        if eid in seen:
            errors[eid] = "Duplicate row."
            continue
        seen.add(eid)
        if eid not in roster:
            errors[eid] = "Not an active student of this section."
            continue
        try:
            obtained = _dec(r.get("marks_obtained"))
            max_marks = _dec(r.get("max_marks")) if r.get("max_marks") not in (None, "") else default_max_d
        except ValueError as e:
            errors[eid] = str(e)
            continue
        if obtained is None:
            continue  # skip — not entered
        if max_marks is None or max_marks <= 0:
            errors[eid] = "Max marks must be greater than 0."
            continue
        if obtained > max_marks:
            errors[eid] = "Marks cannot exceed max marks."
            continue
        parsed.append((roster[eid], obtained, max_marks, str(r.get("remarks") or "")[:255]))
    if errors:
        raise ValidationError({"rows": errors})

    staff = StaffProfile.objects.filter(
        campus_id=section.school_class.campus_id, user=user, is_active=True
    ).first()
    now = timezone.now()
    new_status = ResultEntry.Status.PUBLISHED if publish else ResultEntry.Status.DRAFT

    existing = {
        str(x.enrollment_id): x
        for x in ResultEntry.objects.select_for_update().filter(
            enrollment_id__in=[e.id for e, *_ in parsed], subject=subject, exam_term=exam_term
        )
    }
    created = updated = unchanged = live_edits = 0
    notify_entries = []
    for enrollment, obtained, max_marks, remarks in parsed:
        row = existing.get(str(enrollment.id))
        if row is None:
            created += 1
            if dry_run:
                continue
            row = ResultEntry.objects.create(
                enrollment=enrollment, subject=subject, exam_term=exam_term,
                marks_obtained=obtained, max_marks=max_marks, remarks=remarks, entered_by=staff,
                status=new_status, published_at=now if publish else None,
            )
            if publish:
                notify_entries.append(row)
            continue
        marks_changed = (row.marks_obtained, row.max_marks, row.remarks) != (obtained, max_marks, remarks)
        promote = publish and row.status != ResultEntry.Status.PUBLISHED
        if not marks_changed and not promote:
            unchanged += 1
            continue
        updated += 1
        if marks_changed and row.status == ResultEntry.Status.PUBLISHED and not publish:
            live_edits += 1
        if dry_run:
            continue
        fields = []
        if marks_changed:
            row.marks_obtained, row.max_marks, row.remarks = obtained, max_marks, remarks
            row.entered_by = staff
            fields += ["marks_obtained", "max_marks", "remarks", "entered_by"]
        if promote:
            row.status, row.published_at = ResultEntry.Status.PUBLISHED, now
            fields += ["status", "published_at"]
        row.save(update_fields=fields)
        if row.status == ResultEntry.Status.PUBLISHED and (marks_changed and publish or promote):
            notify_entries.append(row)
    return (
        {
            "created": created, "updated": updated, "unchanged": unchanged,
            "skipped": len(rows) - len(parsed), "published": bool(publish), "live_edits": live_edits,
        },
        notify_entries,
    )


@transaction.atomic
def set_sheet_status(*, section, subject, exam_term, publish):
    """Poori sheet publish / unpublish. LOCKED sheet par nahi. Returns the
    rows that actually changed (publish ke liye notify list)."""
    _assert_unlocked(section, subject, exam_term)
    qs = sheet_entries(section, subject, exam_term).select_for_update()
    if publish:
        rows = list(qs.filter(status=ResultEntry.Status.DRAFT))
        if not rows:
            raise ValidationError({"detail": "There are no draft marks to publish."})
        now = timezone.now()
        ResultEntry.objects.filter(pk__in=[r.pk for r in rows]).update(
            status=ResultEntry.Status.PUBLISHED, published_at=now
        )
        for r in rows:
            r.status, r.published_at = ResultEntry.Status.PUBLISHED, now
        return rows
    rows = list(qs.filter(status=ResultEntry.Status.PUBLISHED))
    if not rows:
        raise ValidationError({"detail": "Nothing is published on this sheet."})
    ResultEntry.objects.filter(pk__in=[r.pk for r in rows]).update(
        status=ResultEntry.Status.DRAFT, published_at=None
    )
    return rows


@transaction.atomic
def set_sheet_lock(*, user, section, subject, exam_term, lock):
    qs = sheet_entries(section, subject, exam_term).select_for_update()
    if lock:
        n = qs.filter(locked_at__isnull=True).update(locked_at=timezone.now(), locked_by=user)
        if not qs.exists():
            raise ValidationError({"detail": "There are no marks on this sheet to lock."})
        return n
    return qs.filter(locked_at__isnull=False).update(locked_at=None, locked_by=None)


def _key_maps(section):
    """Roster lookups for CSV import. Duplicate key = ambiguous = None."""
    maps = {"roll_number": {}, "enrollment_no": {}, "username": {}}
    for e in _active_roster(section):
        for kind, val in (
            ("roll_number", e.roll_number), ("enrollment_no", e.enrollment_no), ("username", e.student.username)
        ):
            val = (val or "").strip().lower()
            if not val:
                continue
            maps[kind][val] = None if val in maps[kind] else e
    return maps


def import_results_csv(
    *, user, section, exam_term, text, campus, subject=None, default_max=None, can_manage_subject,
    publish=False, commit=False,
):
    """
    CSV preview (``commit=False``) ya commit. ``subject`` diya ho to single-
    subject layout (``marks`` column) bhi chalta hai; warna subject-wise
    columns. ``can_manage_subject(subject_id) -> bool`` har subject ke liye
    teacher ki permission check karta hai.

    Preview kabhi kuch save nahi karta. Commit all-or-nothing: koi bhi error
    (CSV ya DB-level) to kuch save nahi hota.
    Returns ``(report, notify_entries)``.
    """
    campus_id = section.school_class.campus_id
    subjects = {s.name: s for s in Subject.objects.filter(campus_id=campus_id)}
    parsed = results_csv.parse_results_csv(
        text, subject_names=list(subjects), single_subject=subject.name if subject else None
    )
    errors = list(parsed["errors"])
    maps = _key_maps(section)
    by_subject, preview = {}, []
    scale, pass_percent = grading.campus_scale(campus), campus.pass_percent

    for c in parsed["cells"]:
        subj = subject if (subject and c["subject"] == subject.name) else subjects.get(c["subject"])
        if subj is None:
            errors.append({"line": c["line"], "message": f"Subject '{c['subject']}' not found."})
            continue
        if not can_manage_subject(subj.id):
            errors.append({"line": c["line"], "message": f"You cannot enter marks for {subj.name}."})
            continue
        enrollment = maps[c["key_type"]].get(c["key"].strip().lower(), "missing")
        if enrollment == "missing":
            errors.append({"line": c["line"], "message": f"No active student with {c['key_type'].replace('_', ' ')} '{c['key']}'."})
            continue
        if enrollment is None:
            errors.append({"line": c["line"], "message": f"{c['key_type'].replace('_', ' ')} '{c['key']}' matches more than one student."})
            continue
        if c["marks"] is None:
            continue
        mx = c["max_marks"]
        if mx is None:
            try:
                mx = _dec(default_max) if default_max not in (None, "") else Decimal("100.00")
            except ValueError as e:
                errors.append({"line": c["line"], "message": f"max_marks — {e}"})
                continue
            if mx is None or mx <= 0:
                errors.append({"line": c["line"], "message": "max_marks must be greater than 0."})
                continue
            if c["marks"] > mx:
                errors.append({"line": c["line"], "message": f"{subj.name}: marks {c['marks']} exceed max {mx}."})
                continue
        by_subject.setdefault(subj.id, (subj, []))[1].append({
            "enrollment": str(enrollment.id), "marks_obtained": c["marks"], "max_marks": mx, "remarks": c["remarks"],
        })
        pct = grading.percent_of(c["marks"], mx)
        preview.append({
            "line": c["line"], "roll_number": enrollment.roll_number, "name": _display_name(enrollment.student),
            "subject": subj.name, "marks_obtained": float(c["marks"]), "max_marks": float(mx),
            "grade": grading.grade_for_percent(pct, scale), "passed": grading.is_pass(pct, pass_percent),
        })

    report = {
        "layout": parsed["layout"], "errors": errors, "warnings": parsed["warnings"],
        "ok_rows": len(preview), "preview": preview[:200], "committed": False,
    }
    if not by_subject and not errors:
        report["errors"].append({"line": 0, "message": "No marks found in the file."})
        errors = report["errors"]
    if errors:
        return report, []
    if commit and not user:
        raise ValidationError({"detail": "Cannot commit without a user."})

    notify, per_subject = [], []
    with transaction.atomic():
        for subj, rows in by_subject.values():
            try:
                summary, changed = bulk_upsert_results(
                    user=user, section=section, subject=subj, exam_term=exam_term, default_max=default_max,
                    rows=rows, publish=publish, dry_run=not commit,
                )
            except ValidationError as exc:
                detail = exc.detail
                msg = detail.get("detail") if isinstance(detail, dict) else None
                if msg is None:
                    msg = "; ".join(f"{k}: {v}" for k, v in (detail.items() if isinstance(detail, dict) else []))
                report["errors"].append({"line": 0, "message": f"{subj.name}: {msg or 'Invalid data.'}"})
                transaction.set_rollback(True)
                return report, []
            per_subject.append({"subject": subj.name, **summary})
            notify += changed
    report["summary"] = per_subject
    report["committed"] = bool(commit)
    return report, notify
