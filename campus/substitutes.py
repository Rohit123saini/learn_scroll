# campus/substitutes.py
"""
SUBSTITUTE TEACHER — the ONE place that owns the rules.

    absent teacher  ->  the periods that need cover  ->  a free teacher  ->
    an overlay on the timetable for that date (+ push)

Why an overlay and not an edit of `TimetableEntry`
--------------------------------------------------
The weekly timetable repeats forever; a teacher is absent on 14 Oct, not
"every Tuesday". A `SubstituteAssignment` is therefore a row keyed by
`(entry, date)` that sits ON TOP of the weekly entry. Nothing is ever written
to the timetable itself, so there is nothing to "undo" next week.

What lives here (and nowhere else, so API / push / reminders cannot drift):
  * which dates / periods an absence affects (`periods_for_absence`),
  * who is free to cover a period (`busy_staff_ids`, `suggest_substitutes`),
  * every validation on assigning (`validate_assignable` -> `SubstituteError`
    with a stable machine `code`),
  * assign / replace / cancel, idempotent and race-safe
    (`assign_substitute`, `cancel_substitution`, `cancel_absence`),
  * who actually teaches a period on a date (`effective_teacher_user_id`) —
    used by the "class starts in 5 min" reminder so the ABSENT teacher is not
    pinged and the substitute is,
  * the read model the timetable screen overlays (`overlay_for_section`).

Pure ORM + dates; no DRF, no Celery, no push. `campus.substitute_push` does the
notifications, `campus.substitute_views` the HTTP.
"""
from __future__ import annotations

import logging
from datetime import date, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    StaffProfile,
    SubjectTeacherassigments,
    SubstituteAssignment,
    TeacherAbsence,
    TimetableEntry,
)

logger = logging.getLogger(__name__)

# Longest absence / overlay window the API will expand. Bounds the work a
# single request can trigger (and a typo like year 2206 can't hang a worker).
MAX_RANGE_DAYS = 62

# A half-day absence (`TeacherAbsence.part`) covers the periods that START
# before this time (first half) or at / after it (second half).
HALF_DAY_SPLIT = time(12, 0)


class SubstituteError(Exception):
    """A business-rule failure with a stable machine `code` (the API turns it
    into a 400 whose `code` the app can switch on)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def display_name(user) -> str:
    if user is None:
        return ""
    full = f"{user.first_name} {user.last_name}".strip()
    return full or user.username


def daterange(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def clamp_range(start: date, end: date) -> tuple[date, date]:
    """Raise if the window is inverted or longer than `MAX_RANGE_DAYS`."""
    if end < start:
        raise SubstituteError("invalid_range", "End date can't be before the start date.")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise SubstituteError("range_too_long", f"Choose at most {MAX_RANGE_DAYS} days at a time.")
    return start, end


def _entry_runs_on(entry: TimetableEntry, on_date: date) -> bool:
    """Does this weekly entry actually happen on `on_date` (right weekday and
    inside its academic session)?"""
    if on_date.isoweekday() != entry.time_slot.day_of_week:
        return False
    session = entry.session
    return session.start_date <= on_date <= session.end_date


def _slot_bounds(entry: TimetableEntry):
    return entry.time_slot.start_time, entry.time_slot.end_time


# --------------------------------------------------------------------------
# absences
# --------------------------------------------------------------------------

def _part_q(at_time):
    """Which absences cover a period starting at `at_time`: a full-day one,
    plus the half-day one for that side of `HALF_DAY_SPLIT`. `None` = any."""
    if at_time is None:
        return Q()
    half = TeacherAbsence.Part.FIRST_HALF if at_time < HALF_DAY_SPLIT else TeacherAbsence.Part.SECOND_HALF
    return Q(part=TeacherAbsence.Part.FULL) | Q(part=half)


def part_covers(part, at_time) -> bool:
    """Does an absence of `part` cover a period starting at `at_time`?"""
    if part == TeacherAbsence.Part.FULL or at_time is None:
        return True
    if part == TeacherAbsence.Part.FIRST_HALF:
        return at_time < HALF_DAY_SPLIT
    return at_time >= HALF_DAY_SPLIT


def active_absences_on(campus_id, on_date: date, at_time=None):
    return TeacherAbsence.objects.filter(
        _part_q(at_time), campus_id=campus_id, is_active=True, date_from__lte=on_date, date_to__gte=on_date
    )


def absent_staff_ids_on(campus_id, on_date: date, at_time=None) -> set:
    return set(active_absences_on(campus_id, on_date, at_time).values_list("staff_id", flat=True))


def is_staff_absent(staff_id, campus_id, on_date: date, at_time=None) -> bool:
    return active_absences_on(campus_id, on_date, at_time).filter(staff_id=staff_id).exists()


def absence_covering(staff_id, campus_id, on_date: date, at_time=None):
    return (
        active_absences_on(campus_id, on_date, at_time).filter(staff_id=staff_id).order_by("-created_at").first()
    )


def overlapping_absence(staff_id, date_from: date, date_to: date, exclude_pk=None):
    """An ACTIVE absence of the same staff member that overlaps the window."""
    qs = TeacherAbsence.objects.filter(
        staff_id=staff_id, is_active=True, date_from__lte=date_to, date_to__gte=date_from
    )
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


# --------------------------------------------------------------------------
# substitutions (read helpers)
# --------------------------------------------------------------------------

def active_substitution(entry_id, on_date: date):
    return (
        SubstituteAssignment.objects.filter(
            entry_id=entry_id, date=on_date, status=SubstituteAssignment.Status.ACTIVE
        )
        .select_related("substitute__user", "original_staff__user")
        .first()
    )


def substitutions_map(entry_ids, date_from: date, date_to: date) -> dict:
    """`{(entry_id, date): SubstituteAssignment}` for every ACTIVE substitution
    in the window — one query, for building lists without N+1."""
    qs = SubstituteAssignment.objects.filter(
        entry_id__in=list(entry_ids),
        date__gte=date_from,
        date__lte=date_to,
        status=SubstituteAssignment.Status.ACTIVE,
    ).select_related("substitute__user", "original_staff__user")
    return {(a.entry_id, a.date): a for a in qs}


def effective_teacher_user_id(entry: TimetableEntry, on_date: date):
    """The `User` id of whoever actually takes this period on `on_date`:

      * the substitute, when one is assigned;
      * `None` when the timetable's teacher is absent and nobody is covering
        (there is no one to remind);
      * otherwise the timetable's own teacher.
    """
    sub = active_substitution(entry.pk, on_date)
    if sub is not None:
        return sub.substitute.user_id
    campus_id = entry.section.school_class.campus_id
    if is_staff_absent(entry.staff_id, campus_id, on_date, entry.time_slot.start_time):
        return None
    return entry.staff.user_id


# --------------------------------------------------------------------------
# which periods does an absence affect?
# --------------------------------------------------------------------------

def periods_for_absence(absence: TeacherAbsence) -> list[dict]:
    """Every period of the absent teacher that falls inside the absence,
    sorted by date then time, each with its current (active) substitution.

    One query for the teacher's weekly entries + one for the substitutions;
    the date expansion happens in Python (<= `MAX_RANGE_DAYS` days).
    """
    clamp_range(absence.date_from, absence.date_to)
    entries = list(
        TimetableEntry.objects.filter(
            staff_id=absence.staff_id,
            section__school_class__campus_id=absence.campus_id,
            session__start_date__lte=absence.date_to,
            session__end_date__gte=absence.date_from,
        ).select_related("time_slot", "session", "section__school_class", "subject", "room")
    )
    by_weekday: dict[int, list[TimetableEntry]] = {}
    for e in entries:
        by_weekday.setdefault(e.time_slot.day_of_week, []).append(e)

    subs = substitutions_map([e.pk for e in entries], absence.date_from, absence.date_to)
    rows: list[dict] = []
    for day in daterange(absence.date_from, absence.date_to):
        for e in by_weekday.get(day.isoweekday(), []):
            if not _entry_runs_on(e, day):
                continue
            if not part_covers(absence.part, e.time_slot.start_time):
                continue  # half-day absence: the other half of the day is taught normally
            rows.append({"entry": e, "date": day, "substitution": subs.get((e.pk, day))})
    rows.sort(key=lambda r: (r["date"], r["entry"].time_slot.start_time))
    return rows


# --------------------------------------------------------------------------
# who is free?
# --------------------------------------------------------------------------

def busy_staff_ids(entry: TimetableEntry, on_date: date) -> set:
    """Staff who cannot take `entry` on `on_date`: they are teaching (or
    already covering) a period whose time OVERLAPS it, or they are absent.

    Overlap, not slot equality: two sections may use differently-cut
    `TimeSlot` rows (09:00-09:45 vs 09:30-10:15) and a teacher still can't be
    in both. A teacher whose own period that day is being covered by someone
    else is FREE for the overlapping time (that's the point of cover).
    """
    campus_id = entry.section.school_class.campus_id
    start, end = _slot_bounds(entry)
    overlapping = list(
        TimetableEntry.objects.filter(
            section__school_class__campus_id=campus_id,
            time_slot__day_of_week=on_date.isoweekday(),
            time_slot__start_time__lt=end,
            time_slot__end_time__gt=start,
            session__start_date__lte=on_date,
            session__end_date__gte=on_date,
        )
        .exclude(pk=entry.pk)
        .values("pk", "staff_id")
    )
    subs = dict(
        SubstituteAssignment.objects.filter(
            entry_id__in=[o["pk"] for o in overlapping],
            date=on_date,
            status=SubstituteAssignment.Status.ACTIVE,
        ).values_list("entry_id", "substitute_id")
    )
    absent = absent_staff_ids_on(campus_id, on_date, start)
    busy = set(absent)
    for o in overlapping:
        teacher = subs.get(o["pk"], o["staff_id"])
        busy.add(teacher)
    return busy


def suggest_substitutes(entry: TimetableEntry, on_date: date) -> list[dict]:
    """Every active teaching-capable staff member of the campus, each flagged
    `free` / `teaches_subject` / `teaches_section`, best first:
    free + same subject, free + knows the section, free, then the busy ones
    (shown greyed-out so an admin can see WHY nobody obvious is available)."""
    campus_id = entry.section.school_class.campus_id
    busy = busy_staff_ids(entry, on_date)

    approved = SubjectTeacherassigments.objects.filter(
        section__school_class__campus_id=campus_id, status=SubjectTeacherassigments.Status.APPROVED
    ).values_list("staff_id", "section_id", "subject_id")
    same_subject, same_section = set(), set()
    for staff_id, section_id, subject_id in approved:
        if subject_id == entry.subject_id:
            same_subject.add(staff_id)
        if section_id == entry.section_id:
            same_section.add(staff_id)

    absent_ids = absent_staff_ids_on(campus_id, on_date, entry.time_slot.start_time)
    candidates = (
        StaffProfile.objects.filter(campus_id=campus_id, is_active=True)
        .exclude(role=StaffProfile.Role.NON_TEACHING)
        .exclude(pk=entry.staff_id)
        .select_related("user")
    )
    out = []
    for s in candidates:
        out.append(
            {
                "staff_id": str(s.pk),
                "user_id": s.user_id,
                "name": display_name(s.user),
                "role": s.role,
                "free": s.pk not in busy,
                "teaches_subject": s.pk in same_subject,
                "teaches_section": s.pk in same_section,
                "absent": s.pk in absent_ids,
            }
        )
    out.sort(
        key=lambda r: (
            not r["free"],
            not r["teaches_subject"],
            not r["teaches_section"],
            r["name"].lower(),
        )
    )
    return out


# --------------------------------------------------------------------------
# assign / cancel
# --------------------------------------------------------------------------

def validate_assignable(entry: TimetableEntry, on_date: date, substitute: StaffProfile) -> None:
    """Every rule for "`substitute` may cover `entry` on `on_date`". Raises
    `SubstituteError` (stable `code`) — checked server-side regardless of what
    the app's picker showed, so a stale list can't create a double booking."""
    campus_id = entry.section.school_class.campus_id
    if substitute.campus_id != campus_id or not substitute.is_active:
        raise SubstituteError("invalid_substitute", "That teacher isn't active staff of this campus.")
    if substitute.role == StaffProfile.Role.NON_TEACHING:
        raise SubstituteError("invalid_substitute", "Non-teaching staff can't be assigned as a substitute.")
    if substitute.pk == entry.staff_id:
        raise SubstituteError("same_teacher", "The substitute can't be the teacher who is being replaced.")
    if on_date.isoweekday() != entry.time_slot.day_of_week:
        raise SubstituteError("wrong_weekday", "That date isn't a day this period runs on.")
    if not (entry.session.start_date <= on_date <= entry.session.end_date):
        raise SubstituteError("outside_session", "That date is outside the academic session.")
    if on_date < timezone.localdate():
        raise SubstituteError("past_date", "You can't assign a substitute for a date that has passed.")
    if is_staff_absent(substitute.pk, campus_id, on_date, entry.time_slot.start_time):
        raise SubstituteError("substitute_absent", "That teacher is marked absent on this date.")
    if substitute.pk in busy_staff_ids(entry, on_date):
        raise SubstituteError("substitute_busy", "That teacher already has a class at this time.")


class AssignResult:
    """What `assign_substitute` did: `assignment` (the active row), `created`
    (False = it was already exactly this, nothing changed) and `replaced` (the
    previous active substitution that was cancelled in favour of this one)."""

    __slots__ = ("assignment", "created", "replaced")

    def __init__(self, assignment, created, replaced=None):
        self.assignment = assignment
        self.created = created
        self.replaced = replaced


def assign_substitute(
    *,
    entry: TimetableEntry,
    on_date: date,
    substitute: StaffProfile,
    assigned_by,
    note: str = "",
    absence: TeacherAbsence | None = None,
) -> AssignResult:
    """Cover `entry` on `on_date` with `substitute`.

      * Idempotent: asking for the substitute that is already assigned returns
        that row untouched (`created=False`) — a double-tap sends one push.
      * A different substitute REPLACES the current one (the old row is kept as
        `cancelled` history) in the same transaction.
      * Race-safe: the partial unique index `unique_active_substitute_per_period`
        is the final arbiter; losing the race is a clean `already_covered` error.
    """
    with transaction.atomic():
        # Serialise concurrent assigners for the same period (no-op on SQLite).
        TimetableEntry.objects.select_for_update().filter(pk=entry.pk).first()

        existing = (
            SubstituteAssignment.objects.select_for_update()
            .filter(entry_id=entry.pk, date=on_date, status=SubstituteAssignment.Status.ACTIVE)
            .first()
        )
        if existing is not None and existing.substitute_id == substitute.pk:
            return AssignResult(existing, created=False)

        validate_assignable(entry, on_date, substitute)

        replaced = None
        if existing is not None:
            existing.status = SubstituteAssignment.Status.CANCELLED
            existing.cancelled_at = timezone.now()
            existing.save(update_fields=["status", "cancelled_at"])
            replaced = existing

        campus_id = entry.section.school_class.campus_id
        if absence is None:
            absence = absence_covering(entry.staff_id, campus_id, on_date, entry.time_slot.start_time)
        try:
            with transaction.atomic():
                assignment = SubstituteAssignment.objects.create(
                    campus_id=campus_id,
                    entry=entry,
                    date=on_date,
                    substitute=substitute,
                    original_staff_id=entry.staff_id,
                    absence=absence,
                    assigned_by=assigned_by if getattr(assigned_by, "is_authenticated", False) else None,
                    note=(note or "")[:200],
                )
        except IntegrityError:
            raise SubstituteError("already_covered", "Someone else just assigned a substitute for this period.")
    return AssignResult(assignment, created=True, replaced=replaced)


def cancel_substitution(assignment: SubstituteAssignment) -> bool:
    """Cancel one substitution. Conditional UPDATE so two simultaneous cancels
    can't both report success (and so the "cancelled" push goes out once).
    Returns True only for the call that actually flipped it."""
    changed = SubstituteAssignment.objects.filter(
        pk=assignment.pk, status=SubstituteAssignment.Status.ACTIVE
    ).update(status=SubstituteAssignment.Status.CANCELLED, cancelled_at=timezone.now())
    if changed:
        assignment.status = SubstituteAssignment.Status.CANCELLED
    return bool(changed)


def cancel_absence(absence: TeacherAbsence) -> list[SubstituteAssignment]:
    """The teacher is not absent after all. Deactivate the absence and cancel
    every still-active substitution that was created for it. Returns the
    substitutions THIS call cancelled (so the caller can notify each one)."""
    cancelled: list[SubstituteAssignment] = []
    with transaction.atomic():
        flipped = TeacherAbsence.objects.filter(pk=absence.pk, is_active=True).update(
            is_active=False, cancelled_at=timezone.now()
        )
        if not flipped:
            return cancelled
        absence.is_active = False
        for sub in SubstituteAssignment.objects.filter(
            absence_id=absence.pk, status=SubstituteAssignment.Status.ACTIVE
        ).select_related("substitute__user", "original_staff__user", "entry__subject", "entry__section__school_class"):
            if cancel_substitution(sub):
                cancelled.append(sub)
    return cancelled


# --------------------------------------------------------------------------
# read model for the timetable screen
# --------------------------------------------------------------------------

def overlay_for_section(section_id, date_from: date, date_to: date) -> list[dict]:
    """What to draw on top of a section's weekly timetable between two dates.

    One item per `(entry, date)` that differs from the plain timetable:
      * `status="substituted"` — an ACTIVE substitution exists (carries who);
      * `status="uncovered"`   — the entry's teacher is absent that date and
        nobody has been assigned yet ("Teacher absent" badge).
    Entries nothing happens to are not listed. Never includes the absence
    `reason` — students and parents read this.
    """
    clamp_range(date_from, date_to)
    entries = list(
        TimetableEntry.objects.filter(
            section_id=section_id,
            session__start_date__lte=date_to,
            session__end_date__gte=date_from,
        ).select_related("time_slot", "session", "staff__user", "section__school_class")
    )
    if not entries:
        return []
    campus_id = entries[0].section.school_class.campus_id
    subs = substitutions_map([e.pk for e in entries], date_from, date_to)
    absences = list(
        TeacherAbsence.objects.filter(
            campus_id=campus_id,
            is_active=True,
            date_from__lte=date_to,
            date_to__gte=date_from,
            staff_id__in={e.staff_id for e in entries},
        ).values("staff_id", "date_from", "date_to")
    )

    def absent(staff_id, day):
        return any(a["staff_id"] == staff_id and a["date_from"] <= day <= a["date_to"] for a in absences)

    items: list[dict] = []
    for day in daterange(date_from, date_to):
        for e in entries:
            if not _entry_runs_on(e, day):
                continue
            sub = subs.get((e.pk, day))
            if sub is not None:
                items.append(
                    {
                        "entry": str(e.pk),
                        "date": day.isoformat(),
                        "status": "substituted",
                        "assignment": str(sub.pk),
                        "substitute": {"staff_id": str(sub.substitute_id), "name": display_name(sub.substitute.user)},
                        "original_teacher": {"staff_id": str(e.staff_id), "name": display_name(e.staff.user)},
                    }
                )
            elif absent(e.staff_id, day):
                items.append(
                    {
                        "entry": str(e.pk),
                        "date": day.isoformat(),
                        "status": "uncovered",
                        "assignment": None,
                        "substitute": None,
                        "original_teacher": {"staff_id": str(e.staff_id), "name": display_name(e.staff.user)},
                    }
                )
    return items
