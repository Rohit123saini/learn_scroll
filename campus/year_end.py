# campus/year_end.py
"""
Year-end: set up the NEW academic session and PROMOTE students into it.

Three steps, each its own function (plain, no request objects -> unit-testable;
`campus/views.py` only does permission checks + JSON):

  copy_structure()  new session starts empty -> copy last year's classes +
                    sections (and, optionally, class-teacher / approved
                    subject-teacher assignments). Idempotent: existing
                    classes / sections / assignments are left alone.
  plan()            read-only preview: for every old class the SUGGESTED next
                    class ("Class 9" -> "Class 10", "B.Sc 2nd Year" ->
                    "B.Sc 3rd Year", last class -> graduate), student counts,
                    sections that don't exist in the target class yet.
  promote()         moves every ACTIVE student of the old session:
                      promote   -> new enrollment in the target class (same
                                   section name), old row = PROMOTED
                      repeat    -> new enrollment in the SAME class of the new
                                   session (detained), old row = PROMOTED
                      graduate  -> old row = GRADUATED, no new row
                      withdraw  -> old row = WITHDRAWN, no new row
                      skip      -> left untouched (decide later)
                    `dry_run=True` computes the exact same report without
                    writing anything.

Rules worth knowing:
* Nothing is ever deleted — old enrollments stay as history, only their status
  changes (so old attendance / results / fees stay attached to the old year).
* Every student is processed in his/her own savepoint: one failure (section
  full, roll clash ...) is reported in `errors` and that student simply stays
  ACTIVE in the old session, so the admin can fix and run again. Re-running is
  safe: a student who already has an ACTIVE enrollment in the new session is
  reported as `already_done` and only the old row is closed.
* Seats are respected (`Section.capacity`); `override_capacity` lets the admin
  go past it on purpose. Roll numbers are re-assigned by `roster.enroll_student`.
* Missing target sections: with `create_missing_sections` they're created (same
  name + capacity), otherwise that student is reported as `no_section`.
"""
import re
from collections import defaultdict

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from . import bridge
from .audit import log_action
from .models import (
    AcademicSession,
    ClassTeacherassigments,
    Section,
    SchoolClass,
    StudentEnrollment,
    SubjectTeacherassigments,
)
from .roster import RosterError, active_count, enroll_student

PROMOTE, REPEAT, GRADUATE, WITHDRAW, SKIP = "promote", "repeat", "graduate", "withdraw", "skip"
ACTIONS = (PROMOTE, REPEAT, GRADUATE, WITHDRAW, SKIP)


class YearEndError(Exception):
    """Business-rule failure with a stable `code` the API turns into a 400."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def check_sessions(campus, from_session, to_session):
    if from_session.campus_id != campus.id or to_session.campus_id != campus.id:
        raise YearEndError("other_campus", "Both sessions must belong to this campus.")
    if from_session.pk == to_session.pk:
        raise YearEndError("same_session", "Choose two different sessions.")
    if to_session.start_date <= from_session.start_date:
        raise YearEndError("order", "The new session must start after the old one.")


_NUM = re.compile(r"(\d+)(st|nd|rd|th)?", re.I)


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def next_class_name(name):
    """'Class 9' -> 'Class 10', 'B.Sc 2nd Year' -> 'B.Sc 3rd Year'. None if the
    name has no number to bump."""
    matches = list(_NUM.finditer(name or ""))
    if not matches:
        return None
    m = matches[0]
    n = int(m.group(1)) + 1
    suffix = _ordinal(n) if m.group(2) else ""
    if m.group(2) and m.group(2).isupper():
        suffix = suffix.upper()
    return f"{name[:m.start()]}{n}{suffix}{name[m.end():]}"


def _stem(name):
    """Name with its first number removed — 'Class 9' / 'Class 10' share a stem."""
    return _norm(_NUM.sub("#", name or "", count=1))


def _class_no(name):
    m = _NUM.search(name or "")
    return int(m.group(1)) if m else None


def suggest_mapping(old_classes, new_classes):
    """{old_class_id: {"action", "to_class"|None, "reason"}} — only a SUGGESTION
    the admin confirms/changes in the UI."""
    by_name = defaultdict(list)
    for c in new_classes:
        by_name[_norm(c.name)].append(c)
    highest = {}
    for c in old_classes:
        n = _class_no(c.name)
        if n is not None:
            highest[_stem(c.name)] = max(highest.get(_stem(c.name), 0), n)

    out = {}
    for c in old_classes:
        nxt = next_class_name(c.name)
        cands = by_name.get(_norm(nxt), []) if nxt else []
        if len(cands) > 1:  # same name in two departments -> prefer the same department
            same = [x for x in cands if x.department_id == c.department_id]
            cands = same or cands
        if cands:
            out[c.id] = {"action": PROMOTE, "to_class": cands[0].id, "reason": "next_class"}
        elif _class_no(c.name) is not None and _class_no(c.name) == highest.get(_stem(c.name)):
            out[c.id] = {"action": GRADUATE, "to_class": None, "reason": "last_class"}
        else:
            out[c.id] = {"action": SKIP, "to_class": None, "reason": "no_match"}
    return out


def _new_section(new_class, name, capacity, actor):
    sec = Section.objects.create(school_class=new_class, name=name, capacity=capacity)
    try:
        bridge.create_section_group(sec, actor=actor)
    except Exception:  # expected until a class-teacher exists (same as SectionViewSet)
        pass
    return sec


# ---------------------------------------------------------------------------
# 1. copy structure
# ---------------------------------------------------------------------------
@transaction.atomic
def copy_structure(campus, actor, from_session, to_session, *, copy_teachers=False):
    check_sessions(campus, from_session, to_session)
    classes_new = sections_new = ct_new = st_new = 0
    old_classes = SchoolClass.objects.filter(campus=campus, session=from_session).prefetch_related("sections")
    for oc in old_classes:
        nc, made = SchoolClass.objects.get_or_create(
            campus=campus, session=to_session, name=oc.name, defaults={"department_id": oc.department_id}
        )
        classes_new += int(made)
        for osec in oc.sections.all():
            ns = Section.objects.filter(school_class=nc, name=osec.name).first()
            if ns is None:
                ns = _new_section(nc, osec.name, osec.capacity, actor)
                sections_new += 1
            if not copy_teachers:
                continue
            ct = ClassTeacherassigments.objects.filter(section=osec, staff__is_active=True).first()
            if ct and not ClassTeacherassigments.objects.filter(section=ns).exists():
                ClassTeacherassigments.objects.create(section=ns, staff=ct.staff)
                ct_new += 1
                try:
                    bridge.create_section_group(ns, actor=ct.staff.user)
                except Exception:
                    pass
            for st in SubjectTeacherassigments.objects.filter(
                section=osec, status=SubjectTeacherassigments.Status.APPROVED, staff__is_active=True
            ):
                _, made_st = SubjectTeacherassigments.objects.get_or_create(
                    section=ns, subject_id=st.subject_id, staff=st.staff,
                    defaults={"status": SubjectTeacherassigments.Status.APPROVED, "responded_at": timezone.now()},
                )
                st_new += int(made_st)
    log_action(
        campus.id, actor, "session.copy_structure", target=to_session,
        summary=f"copied structure {from_session.name} -> {to_session.name}",
        classes=classes_new, sections=sections_new, class_teachers=ct_new, subject_teachers=st_new,
    )
    return {"classes_created": classes_new, "sections_created": sections_new,
            "class_teachers_copied": ct_new, "subject_teachers_copied": st_new}


# ---------------------------------------------------------------------------
# 2. plan (read-only preview of the class mapping)
# ---------------------------------------------------------------------------
def plan(campus, from_session, to_session):
    check_sessions(campus, from_session, to_session)
    old_classes = list(
        SchoolClass.objects.filter(campus=campus, session=from_session).order_by("name").prefetch_related("sections")
    )
    new_classes = list(
        SchoolClass.objects.filter(campus=campus, session=to_session).order_by("name").prefetch_related("sections")
    )
    sugg = suggest_mapping(old_classes, new_classes)
    counts = {
        r["section__school_class_id"]: r["n"]
        for r in StudentEnrollment.objects.filter(
            session=from_session, status=StudentEnrollment.Status.ACTIVE, section__school_class__campus=campus
        ).values("section__school_class_id").annotate(n=Count("id"))
    }
    new_by_id = {c.id: c for c in new_classes}
    rows = []
    for oc in old_classes:
        s = sugg[oc.id]
        target = new_by_id.get(s["to_class"])
        target_sections = {x.name for x in target.sections.all()} if target else set()
        rows.append({
            "from_class": str(oc.id),
            "from_class_name": oc.name,
            "students": counts.get(oc.id, 0),
            "sections": [x.name for x in oc.sections.all()],
            "suggested_action": s["action"],
            "suggested_to_class": str(s["to_class"]) if s["to_class"] else None,
            "reason": s["reason"],
            "missing_sections": sorted({x.name for x in oc.sections.all()} - target_sections) if target else [],
        })
    return {
        "from_session": {"id": str(from_session.id), "name": from_session.name},
        "to_session": {"id": str(to_session.id), "name": to_session.name},
        "classes": rows,
        "target_classes": [{"id": str(c.id), "name": c.name} for c in new_classes],
        "total_students": sum(counts.values()),
        "new_session_has_classes": bool(new_classes),
    }


# ---------------------------------------------------------------------------
# 3. promote
# ---------------------------------------------------------------------------
def _clean_mapping(mapping, campus, to_session):
    """-> {from_class_id(str): (action, to_class|None)}; validates every row."""
    out = {}
    for row in mapping or []:
        fc = str(row.get("from_class") or "")
        action = (row.get("action") or "").strip().lower()
        if action not in ACTIONS:
            raise YearEndError("bad_action", f"Unknown action '{action}' for class {fc}.")
        to_class = None
        if action in (PROMOTE, REPEAT):
            if action == PROMOTE:
                tid = row.get("to_class")
                to_class = SchoolClass.objects.filter(pk=tid, campus=campus, session=to_session).first() if tid else None
                if to_class is None:
                    raise YearEndError("bad_target", f"Class {fc}: pick a target class of the new session.")
        out[fc] = (action, to_class)
    return out


def _find_target_section(old_section, target_class, cache):
    key = (old_section.id, target_class.id)
    if key not in cache:
        secs = {s.name: s for s in Section.objects.filter(school_class=target_class)}
        sec = secs.get(old_section.name)
        if sec is None and len(secs) == 1:
            sec = next(iter(secs.values()))
        cache[key] = sec
    return cache[key]


def promote(campus, actor, from_session, to_session, mapping, *, overrides=None, dry_run=True,
            create_missing_sections=False, override_capacity=False, set_current=False):
    """See module docstring. `overrides`: [{"student": <user id>, "action": ...,
    "to_section": <section id, optional>}] — per-student exceptions (a detained
    / left / separately placed student)."""
    check_sessions(campus, from_session, to_session)
    cmap = _clean_mapping(mapping, campus, to_session)
    ov = {}
    for row in overrides or []:
        action = (row.get("action") or "").strip().lower()
        if action not in ACTIONS:
            raise YearEndError("bad_action", f"Unknown action '{action}' for a student override.")
        ov[str(row.get("student"))] = (action, row.get("to_section"))

    olds = list(
        StudentEnrollment.objects.filter(
            session=from_session, status=StudentEnrollment.Status.ACTIVE, section__school_class__campus=campus
        ).select_related("student", "section__school_class").order_by("section__school_class__name", "section__name", "roll_number")
    )
    already_new = set(
        StudentEnrollment.objects.filter(
            session=to_session, status=StudentEnrollment.Status.ACTIVE, student_id__in=[e.student_id for e in olds]
        ).values_list("student_id", flat=True)
    )
    repeat_class_cache = {}
    sec_cache = {}
    seats = {}  # dry-run seat simulation: section_id -> seats left (None = unlimited)
    sections_to_sync = set()
    summary = defaultdict(int)
    by_class = defaultdict(lambda: defaultdict(int))
    errors, rows = [], []
    missing_sections = set()

    def seats_left(sec):
        if sec.id not in seats:
            seats[sec.id] = None if sec.capacity is None else max(sec.capacity - active_count(sec, to_session), 0)
        return seats[sec.id]

    for en in olds:
        old_class = en.section.school_class
        uname = en.student.username
        action, to_class = cmap.get(str(old_class.id), (None, None))
        forced_section = None
        if str(en.student_id) in ov:
            action, forced = ov[str(en.student_id)]
            if forced:
                forced_section = Section.objects.filter(
                    pk=forced, school_class__campus=campus, school_class__session=to_session
                ).first()
                if forced_section is None:
                    errors.append({"student": uname, "code": "bad_section", "message": "Override section is not in the new session."})
                    summary["errors"] += 1
                    continue
                to_class = forced_section.school_class
        if action is None:
            errors.append({"student": uname, "code": "no_mapping",
                           "message": f"No action chosen for class {old_class.name}."})
            summary["errors"] += 1
            continue
        if action == SKIP:
            summary["skipped"] += 1
            by_class[old_class.name]["skipped"] += 1
            continue

        status_for_old = {
            PROMOTE: StudentEnrollment.Status.PROMOTED, REPEAT: StudentEnrollment.Status.PROMOTED,
            GRADUATE: StudentEnrollment.Status.GRADUATED, WITHDRAW: StudentEnrollment.Status.WITHDRAWN,
        }[action]

        target = None
        will_create_section = False
        if action in (PROMOTE, REPEAT):
            if en.student_id in already_new:
                summary["already_done"] += 1
                by_class[old_class.name]["already_done"] += 1
                if not dry_run:
                    _close_old(en, status_for_old, actor)
                    sections_to_sync.add(en.section_id)
                continue
            if action == REPEAT:
                tc = repeat_class_cache.get(old_class.id)
                if tc is None:
                    tc = SchoolClass.objects.filter(
                        campus=campus, session=to_session, name=old_class.name
                    ).first()
                    repeat_class_cache[old_class.id] = tc or False
                to_class = tc or None
                if not to_class:
                    errors.append({"student": uname, "code": "no_repeat_class",
                                   "message": f"{old_class.name} doesn't exist in {to_session.name}. Copy the structure first."})
                    summary["errors"] += 1
                    continue
            target = forced_section or _find_target_section(en.section, to_class, sec_cache)
            if target is None:
                if create_missing_sections:
                    will_create_section = True
                else:
                    errors.append({"student": uname, "code": "no_section",
                                   "message": f"{to_class.name} has no section '{en.section.name}'."})
                    summary["errors"] += 1
                    continue
            elif not override_capacity:
                left = seats_left(target)
                if left is not None and left <= 0:
                    errors.append({"student": uname, "code": "section_full",
                                   "message": f"{target} is full."})
                    summary["errors"] += 1
                    continue

        # ---- the action is valid: count it / do it
        if dry_run:
            if target is not None and not override_capacity and seats.get(target.id) is not None:
                seats[target.id] -= 1
            summary[action] += 1
            by_class[old_class.name][action] += 1
            if will_create_section:
                missing_sections.add((to_class.id, en.section.name))
            rows.append({"student": uname, "from": str(en.section), "action": action,
                         "to": (str(target) if target else (f"{to_class.name} {en.section.name} (new)" if will_create_section else None))})
            continue

        try:
            with transaction.atomic():
                if action in (PROMOTE, REPEAT):
                    if will_create_section:
                        target = sec_cache.get((en.section_id, to_class.id)) or Section.objects.filter(
                            school_class=to_class, name=en.section.name).first() or _new_section(
                            to_class, en.section.name, en.section.capacity, actor)
                        sec_cache[(en.section_id, to_class.id)] = target
                    enroll_student(section=target, student=en.student, session=to_session, actor=actor,
                                   enrollment_no=en.enrollment_no, override_capacity=override_capacity)
                _close_old(en, status_for_old, actor)
            sections_to_sync.add(en.section_id)
            summary[action] += 1
            by_class[old_class.name][action] += 1
        except RosterError as exc:
            errors.append({"student": uname, "code": exc.code, "message": exc.message})
            summary["errors"] += 1

    if dry_run:
        summary["sections_to_create"] = len(missing_sections)
    else:
        for sec in Section.objects.filter(pk__in=sections_to_sync):
            bridge.sync_section_group(sec)
        if set_current and not summary.get("errors"):
            to_session.is_current = True
            to_session.save(update_fields=["is_current", "updated_at"])
            summary["session_set_current"] = 1
        log_action(
            campus.id, actor, "session.promote", target=to_session,
            summary=f"year-end {from_session.name} -> {to_session.name}: "
                    f"{summary['promote']} promoted, {summary['repeat']} repeat, {summary['graduate']} graduated",
            **{k: v for k, v in summary.items()},
        )

    return {
        "dry_run": bool(dry_run),
        "total": len(olds),
        "summary": {k: summary.get(k, 0) for k in (PROMOTE, REPEAT, GRADUATE, WITHDRAW, "skipped", "already_done", "errors", "sections_to_create", "session_set_current")},
        "by_class": {k: dict(v) for k, v in by_class.items()},
        "errors": errors[:500],
        "error_count": len(errors),
        "preview": rows[:300] if dry_run else [],
    }


def _close_old(enrollment, status, actor):
    enrollment.status = status
    enrollment.left_at = timezone.now()
    enrollment.save(update_fields=["status", "left_at"])
    log_action(
        enrollment.section.school_class.campus_id, actor, f"enrollment.{status}", target=enrollment,
        summary=f"{status}: {enrollment.student.username} ({enrollment.section})",
        section=str(enrollment.section_id),
    )
