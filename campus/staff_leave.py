# campus/staff_leave.py
"""
STAFF LEAVE — apply / approve / reject / cancel (rules; the API is in
`campus.staff_leave_views`).

Lifecycle
---------
    apply ──> pending ──approve──> approved ──cancel──> cancelled
                  │                    │
                  ├──reject──> rejected
                  └──cancel──> cancelled

Rules
-----
* Who applies: any ACTIVE staff member, for themself, at a campus where they
  hold a staff profile. (A user with staff rows at several campuses names the
  `campus`.) An admin / principal may also apply FOR another active staff member
  of their campus (`staff=<StaffProfile id>`): that leave is recorded as
  APPROVED straight away (`applied_by` = the admin) and the staff member is told.
* Dates: inclusive, at most `MAX_DAYS` calendar days, and not further back than
  `BACKDATE_DAYS` (sick leave is often applied for afterwards). Days that are
  the campus's weekly off (`Campus.weekly_off_days`) or a `CampusHoliday` don't
  count; a range with no counting day at all is refused (`no_working_days`).
  A weekday in `Campus.half_day_weekdays` (e.g. Saturday) counts 0.5.
* Half day: `is_half_day` needs a single date, counts 0.5 and says which half
  (`half_day_part` = first_half | second_half, default first_half). Approving it
  records an absence for that half only (see `TeacherAbsence.part`).
* Quota: `Campus.leave_quota` = days per calendar year per leave type (e.g.
  `{"casual": 12}`); pending + approved leave of that type counts, and an
  application that would go over is refused (`leave_quota_exceeded`). A type
  without an entry is unlimited.
* No overlap: a staff member can't have two pending/approved leaves that touch
  the same date (cancel or get the old one rejected first).
* Edit: the applicant may change a PENDING leave (type, dates, half day,
  reason); the same checks run again, the approvers are told it changed. An
  approved / decided leave is cancelled and re-applied instead.
* Who decides: a campus admin or principal/HOD of THAT campus, never on their
  own leave (`cannot_decide_own_leave` — another admin/principal approves it).
  Exception: if the campus has NO other active admin / principal, nobody else
  could ever decide it, so an admin / principal's own leave is approved
  automatically when they apply (and may be decided by them if it was left
  pending before the other approver went away). Rejecting needs a short note;
  approving may carry one.
* Approve -> if the leave isn't already in the past and no active
  `TeacherAbsence` of that staff member covers the dates, one is recorded so the
  substitute-teacher flow sees the leave. Its `reason` is only the leave type
  ("Leave: Sick"), never the applicant's own text.
* Cancel: the applicant may cancel a pending leave, or an approved leave that
  hasn't started; an admin/principal may cancel an approved leave until it ends.
  Cancelling an approved leave also cancels the absence this leave created, and
  the cover made for it (the caller notifies the released substitutes).
  Cancelling an already-cancelled leave is a no-op (idempotent).

Every failure raises `StaffLeaveError(code, message, status)` — the view turns
it into a 4xx with a stable machine `code` for the app to translate.
"""
from collections import defaultdict
from datetime import date, timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from . import substitutes as sub_svc
from .models import CampusHoliday, StaffLeave, StaffProfile, TeacherAbsence
from .permissions import is_campus_admin_or_principal

MAX_DAYS = 90
BACKDATE_DAYS = 7
REASON_MAX_LEN = 300
NOTE_MAX_LEN = 200
REJECT_NOTE_MIN_LEN = 3
LIVE_STATUSES = (StaffLeave.Status.PENDING, StaffLeave.Status.APPROVED)
APPROVER_ROLES = (StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD)
AUTO_APPROVE_NOTE = "Approved automatically: no other admin or principal at this campus."


class StaffLeaveError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _clean(text, limit, field):
    text = " ".join(str(text or "").split())
    if len(text) > limit:
        raise StaffLeaveError(f"{field}_too_long", f"Keep the {field} within {limit} characters.")
    return text


# --------------------------------------------------------------------------
# calendar: weekly off + holidays
# --------------------------------------------------------------------------
def weekly_off_set(campus) -> set:
    """ISO weekdays (Mon=1..Sun=7) the campus has off. Bad / missing data is
    ignored (nothing off) rather than breaking leave."""
    raw = getattr(campus, "weekly_off_days", None) or []
    out = set()
    if isinstance(raw, (list, tuple)):
        for value in raw:
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 7:
                out.add(value)
    return out


def holiday_dates(campus_id, start: date, end: date) -> set:
    return set(
        CampusHoliday.objects.filter(campus_id=campus_id, date__gte=start, date__lte=end).values_list(
            "date", flat=True
        )
    )


def half_day_set(campus) -> frozenset:
    """ISO weekdays (Mon=1..Sun=7) that are working days but only HALF a day (`Campus.half_day_weekdays`, e.g. Saturday).
    A weekly-off weekday never counts as half (off wins). Bad / missing data is ignored (nothing half)."""
    raw = getattr(campus, "half_day_weekdays", None) or []
    out = set()
    if isinstance(raw, (list, tuple)):
        for value in raw:
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 7:
                out.add(value)
    return frozenset(out - weekly_off_set(campus))


def _counting_days(start: date, end: date, off: set, holidays: set, half=frozenset()) -> float:
    """Leave days in `start..end`: 1 per working day, 0.5 per half-day weekday, 0 for weekly off / holiday."""
    n, day = 0.0, start
    while day <= end:
        wd = day.isoweekday()
        if wd not in off and day not in holidays:
            n += 0.5 if wd in half else 1.0
        day += timedelta(days=1)
    return n


def working_days_between(campus, start: date, end: date) -> float:
    """Leave days in `start..end` (inclusive): weekly off and holidays are 0, a half-day weekday is 0.5."""
    if end < start:
        return 0.0
    return _counting_days(start, end, weekly_off_set(campus), holiday_dates(campus.pk, start, end),
                          half_day_set(campus))


def leave_days(date_from: date, date_to: date, is_half_day: bool = False) -> float:
    """Plain calendar days (no campus calendar) — kept for callers that only
    have dates; the real cost of a leave is `StaffLeave.days`."""
    return 0.5 if is_half_day else float((date_to - date_from).days + 1)


def _days_within(*, is_half_day, date_from, date_to, lo, hi, off, holidays, half=frozenset()) -> float:
    """Days of one leave that fall inside `lo..hi`."""
    start, end = max(date_from, lo), min(date_to, hi)
    if start > end:
        return 0.0
    if is_half_day:
        counts = start.isoweekday() not in off and start not in holidays
        return 0.5 if counts else 0.0
    return float(_counting_days(start, end, off, holidays, half))


def _price(campus, date_from, date_to, is_half_day) -> float:
    days = _days_within(
        is_half_day=is_half_day, date_from=date_from, date_to=date_to, lo=date_from, hi=date_to,
        off=weekly_off_set(campus), holidays=holiday_dates(campus.pk, date_from, date_to),
        half=half_day_set(campus),
    )
    if days <= 0:
        raise StaffLeaveError(
            "no_working_days", "Those dates are all weekly off or holidays — there is no leave to apply for."
        )
    return days


# --------------------------------------------------------------------------
# who is applying
# --------------------------------------------------------------------------
def applicant_profile(user, campus_id=None) -> StaffProfile:
    """The user's own ACTIVE staff profile at `campus_id` (required only when
    they have more than one)."""
    qs = StaffProfile.objects.filter(user=user, is_active=True).select_related("campus", "user")
    if campus_id:
        try:
            qs = qs.filter(campus_id=campus_id)
        except (ValueError, TypeError):
            raise StaffLeaveError("invalid_campus", "Invalid campus.")
    profiles = list(qs[:2])
    if not profiles:
        raise StaffLeaveError("not_staff", "Only active staff of this campus can apply for leave.", 403)
    if len(profiles) > 1:
        raise StaffLeaveError("campus_required", "You work at more than one campus — say which one.")
    return profiles[0]


def _staff_by_id(staff_id) -> StaffProfile:
    try:
        staff = (
            StaffProfile.objects.filter(pk=staff_id, is_active=True).select_related("campus", "user").first()
        )
    except (ValueError, TypeError, DjangoValidationError):
        staff = None
    if staff is None:
        raise StaffLeaveError("not_found", "Staff member not found.", 404)
    return staff


def other_approver_exists(campus_id, exclude_user_id) -> bool:
    """Is there an ACTIVE admin / principal at the campus besides this user?"""
    return (
        StaffProfile.objects.filter(campus_id=campus_id, is_active=True, role__in=APPROVER_ROLES)
        .exclude(user_id=exclude_user_id)
        .exists()
    )


def is_sole_approver(user, campus_id) -> bool:
    """`user` is an active admin / principal of the campus and nobody else is."""
    return is_campus_admin_or_principal(user, campus_id) and not other_approver_exists(campus_id, user.pk)


# --------------------------------------------------------------------------
# shared validation (apply + edit)
# --------------------------------------------------------------------------
def _check_quota(staff, leave_type, date_from, date_to, is_half_day, exclude_pk=None):
    quota = (getattr(staff.campus, "leave_quota", None) or {}).get(leave_type)
    if quota is None:
        return
    try:
        quota = float(quota)
    except (TypeError, ValueError):
        return  # a malformed quota never blocks anyone
    off = weekly_off_set(staff.campus)
    half = half_day_set(staff.campus)
    span_lo, span_hi = date(date_from.year, 1, 1), date(date_to.year, 12, 31)
    holidays = holiday_dates(staff.campus_id, span_lo, span_hi)
    existing = list(
        StaffLeave.objects.filter(
            staff=staff, leave_type=leave_type, status__in=LIVE_STATUSES,
            date_from__lte=span_hi, date_to__gte=span_lo,
        ).exclude(pk=exclude_pk)
    )
    for year in range(date_from.year, date_to.year + 1):
        lo, hi = date(year, 1, 1), date(year, 12, 31)
        new = _days_within(
            is_half_day=is_half_day, date_from=date_from, date_to=date_to, lo=lo, hi=hi, off=off, holidays=holidays,
            half=half,
        )
        if new <= 0:
            continue
        used = sum(
            _days_within(
                is_half_day=lv.is_half_day, date_from=lv.date_from, date_to=lv.date_to,
                lo=lo, hi=hi, off=off, holidays=holidays, half=half,
            )
            for lv in existing
        )
        if used + new > quota:
            left = max(quota - used, 0)
            raise StaffLeaveError(
                "leave_quota_exceeded",
                f"{year}: only {left:g} of {quota:g} {leave_type} day(s) left, this leave needs {new:g}.",
                409,
            )


def _validate(*, staff, leave_type, date_from, date_to, is_half_day, half_day_part, reason, today, exclude_pk=None):
    """Every rule that applies to a new OR edited application. Returns
    `(date_to, half_day_part, working_days, reason)`."""
    if leave_type not in StaffLeave.LeaveType.values:
        raise StaffLeaveError("invalid_leave_type", f"Leave type must be one of: {', '.join(StaffLeave.LeaveType.values)}.")
    date_to = date_to or date_from
    if date_to < date_from:
        raise StaffLeaveError("invalid_range", "The end date can't be before the start date.")
    if is_half_day and date_to != date_from:
        raise StaffLeaveError("half_day_single_date", "A half-day leave must be a single date.")
    if (date_to - date_from).days + 1 > MAX_DAYS:
        raise StaffLeaveError("range_too_long", f"Apply for at most {MAX_DAYS} days at a time.")
    if date_from < today - timedelta(days=BACKDATE_DAYS):
        raise StaffLeaveError("too_far_back", f"Leave can't start more than {BACKDATE_DAYS} days in the past.")
    if is_half_day:
        half_day_part = half_day_part or StaffLeave.HalfDayPart.FIRST_HALF
        if half_day_part not in StaffLeave.HalfDayPart.values:
            raise StaffLeaveError(
                "invalid_half_day_part", f"Half day must be one of: {', '.join(StaffLeave.HalfDayPart.values)}."
            )
    else:
        half_day_part = ""
    reason = _clean(reason, REASON_MAX_LEN, "reason")
    if leave_type == StaffLeave.LeaveType.OTHER and len(reason) < REJECT_NOTE_MIN_LEN:
        raise StaffLeaveError("reason_required", "Please say the reason for this leave.")

    days = _price(staff.campus, date_from, date_to, is_half_day)

    clash = (
        StaffLeave.objects.filter(
            staff=staff, status__in=LIVE_STATUSES, date_from__lte=date_to, date_to__gte=date_from
        )
        .exclude(pk=exclude_pk)
        .order_by("date_from")
        .first()
    )
    if clash:
        raise StaffLeaveError(
            "leave_overlap",
            f"You already have a {clash.get_status_display().lower()} leave from "
            f"{clash.date_from.isoformat()} to {clash.date_to.isoformat()} that overlaps these dates.",
            409,
        )
    _check_quota(staff, leave_type, date_from, date_to, is_half_day, exclude_pk=exclude_pk)
    return date_to, half_day_part, days, reason


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------
@transaction.atomic
def apply_for_leave(
    *, user, campus_id, leave_type, date_from, date_to=None, is_half_day=False, half_day_part="",
    reason="", staff_id=None, today=None,
):
    """Apply for leave. `staff_id` = an admin / principal recording it FOR that
    staff member (recorded as approved at once). An admin / principal who is the
    campus's only approver gets their own leave approved automatically.
    Read `leave.status` to see which happened."""
    today = today or timezone.localdate()
    on_behalf = False
    if staff_id:
        staff = _staff_by_id(staff_id)
        if staff.user_id != user.pk:
            if not is_campus_admin_or_principal(user, staff.campus_id):
                raise StaffLeaveError(
                    "not_allowed", "Only a campus admin or principal can apply leave for someone else.", 403
                )
            on_behalf = True
    else:
        staff = applicant_profile(user, campus_id)
    # Serialise this person's applications: two taps (or two devices) can't both
    # pass the overlap / quota checks below.
    StaffProfile.objects.select_for_update().filter(pk=staff.pk).first()

    date_to, half_day_part, days, reason = _validate(
        staff=staff, leave_type=leave_type, date_from=date_from, date_to=date_to,
        is_half_day=is_half_day, half_day_part=half_day_part, reason=reason, today=today,
    )
    leave = StaffLeave.objects.create(
        campus_id=staff.campus_id,
        staff=staff,
        leave_type=leave_type,
        date_from=date_from,
        date_to=date_to,
        is_half_day=is_half_day,
        half_day_part=half_day_part,
        working_days=days,
        reason=reason,
        applied_by=user if on_behalf else None,
    )
    if on_behalf:
        _approve(leave, approver=user, note="Recorded by admin.", today=today)
    elif staff.user_id == user.pk and is_sole_approver(user, staff.campus_id):
        _approve(leave, approver=user, note=AUTO_APPROVE_NOTE, today=today)
    return leave


# --------------------------------------------------------------------------
# edit (pending only)
# --------------------------------------------------------------------------
_UNSET = object()


@transaction.atomic
def edit_leave(
    *, leave_id, actor, leave_type=_UNSET, date_from=_UNSET, date_to=_UNSET, is_half_day=_UNSET,
    half_day_part=_UNSET, reason=_UNSET, today=None,
):
    """Change a PENDING leave. Only the applicant. Whatever isn't passed keeps
    its value; every apply rule runs again on the result. Returns the leave."""
    today = today or timezone.localdate()
    leave = (
        StaffLeave.objects.select_for_update()
        .select_related("staff__user", "staff__campus", "campus")
        .filter(pk=leave_id)
        .first()
    )
    if leave is None:
        raise StaffLeaveError("not_found", "Leave application not found.", 404)
    if leave.staff.user_id != actor.pk:
        raise StaffLeaveError("not_allowed", "Only the person who applied can edit this leave.", 403)
    if leave.status != StaffLeave.Status.PENDING:
        raise StaffLeaveError(
            "leave_not_editable",
            f"This leave is already {leave.get_status_display().lower()} — cancel it and apply again.",
            409,
        )
    StaffProfile.objects.select_for_update().filter(pk=leave.staff_id).first()

    new_type = leave.leave_type if leave_type is _UNSET else leave_type
    new_from = leave.date_from if date_from is _UNSET else date_from
    new_half = leave.is_half_day if is_half_day is _UNSET else bool(is_half_day)
    new_part = leave.half_day_part if half_day_part is _UNSET else (half_day_part or "")
    new_reason = leave.reason if reason is _UNSET else reason
    if date_to is _UNSET:
        # A single-day leave follows its date; a range keeps its end; a leave
        # turned into a half day collapses to its start date.
        single = leave.date_from == leave.date_to
        new_to = new_from if (single or new_half) else leave.date_to
    else:
        new_to = date_to

    new_to, new_part, days, new_reason = _validate(
        staff=leave.staff, leave_type=new_type, date_from=new_from, date_to=new_to, is_half_day=new_half,
        half_day_part=new_part, reason=new_reason, today=today, exclude_pk=leave.pk,
    )
    leave.leave_type, leave.date_from, leave.date_to = new_type, new_from, new_to
    leave.is_half_day, leave.half_day_part, leave.working_days = new_half, new_part, days
    leave.reason = new_reason
    leave.edited_at = timezone.now()
    leave.save()
    return leave


# --------------------------------------------------------------------------
# approve / reject
# --------------------------------------------------------------------------
def _approve(leave, *, approver, note, today):
    """Mark `leave` approved and, if it isn't already over and no active full
    absence covers it, record the `TeacherAbsence` the substitute flow reads.
    A half day records an absence for that half only. Saves the leave."""
    leave.status = StaffLeave.Status.APPROVED
    leave.decided_by = approver
    leave.decided_at = timezone.now()
    leave.decision_note = note
    if leave.date_to >= today:
        part = leave.half_day_part if leave.is_half_day and leave.half_day_part else TeacherAbsence.Part.FULL
        # A full-day absence already covers any half; a half only covers that half.
        covering = {TeacherAbsence.Part.FULL, part}
        covered = TeacherAbsence.objects.filter(
            staff=leave.staff, is_active=True, date_from__lte=leave.date_from, date_to__gte=leave.date_to,
            part__in=covering,
        ).exists()
        if not covered:
            leave.absence = TeacherAbsence.objects.create(
                campus_id=leave.campus_id,
                staff=leave.staff,
                date_from=leave.date_from,
                date_to=leave.date_to,
                part=part,
                reason=f"Leave: {leave.get_leave_type_display()}",
                marked_by=approver,
            )
    leave.save()


@transaction.atomic
def decide(*, leave_id, approver, approve, note="", today=None):
    """Approve or reject a PENDING leave. Returns the refreshed leave."""
    today = today or timezone.localdate()
    leave = (
        StaffLeave.objects.select_for_update()
        .select_related("staff__user", "campus")
        .filter(pk=leave_id)
        .first()
    )
    if leave is None:
        raise StaffLeaveError("not_found", "Leave application not found.", 404)
    if not is_campus_admin_or_principal(approver, leave.campus_id):
        raise StaffLeaveError("not_allowed", "Only a campus admin or principal can decide leave.", 403)
    if leave.staff.user_id == approver.pk and other_approver_exists(leave.campus_id, approver.pk):
        # Someone else can decide it, so they must. (When the approver is the
        # campus's only admin / principal nobody else ever could — allowed.)
        raise StaffLeaveError(
            "cannot_decide_own_leave", "You can't decide your own leave — another admin or principal must.", 403
        )
    if leave.status != StaffLeave.Status.PENDING:
        raise StaffLeaveError(
            "leave_not_pending", f"This leave is already {leave.get_status_display().lower()}.", 409
        )
    note = _clean(note, NOTE_MAX_LEN, "note")
    if not approve and len(note) < REJECT_NOTE_MIN_LEN:
        raise StaffLeaveError("note_required", "Please give a short reason for rejecting this leave.")

    if approve:
        _approve(leave, approver=approver, note=note, today=today)
    else:
        leave.status = StaffLeave.Status.REJECTED
        leave.decided_by = approver
        leave.decided_at = timezone.now()
        leave.decision_note = note
        leave.save()
    return leave


# --------------------------------------------------------------------------
# cancel
# --------------------------------------------------------------------------
@transaction.atomic
def cancel(*, leave_id, actor, today=None):
    """Cancel a leave. Returns `(leave, released_substitutions, previous_status)`;
    the caller notifies each released substitute. Idempotent for an
    already-cancelled leave (`previous_status == "cancelled"`, nothing released)."""
    today = today or timezone.localdate()
    leave = (
        StaffLeave.objects.select_for_update()
        .select_related("staff__user", "campus", "absence")
        .filter(pk=leave_id)
        .first()
    )
    if leave is None:
        raise StaffLeaveError("not_found", "Leave application not found.", 404)
    is_owner = leave.staff.user_id == actor.pk
    is_admin = is_campus_admin_or_principal(actor, leave.campus_id)
    if not (is_owner or is_admin):
        raise StaffLeaveError("not_allowed", "You can't cancel this leave.", 403)
    if leave.status == StaffLeave.Status.CANCELLED:
        return leave, [], StaffLeave.Status.CANCELLED
    if leave.status == StaffLeave.Status.REJECTED:
        raise StaffLeaveError("leave_not_cancellable", "A rejected leave can't be cancelled.", 409)

    if leave.status == StaffLeave.Status.APPROVED:
        if leave.date_to < today:
            raise StaffLeaveError("leave_already_over", "This leave is already over.", 409)
        if not is_admin and leave.date_from <= today:
            raise StaffLeaveError(
                "leave_already_started",
                "This leave has already started — ask your admin or principal to cancel it.",
                409,
            )

    previous = leave.status
    released = []
    if leave.status == StaffLeave.Status.APPROVED and leave.absence_id:
        released = sub_svc.cancel_absence(leave.absence)
    leave.status = StaffLeave.Status.CANCELLED
    leave.cancelled_by = actor
    leave.cancelled_at = timezone.now()
    leave.save(update_fields=["status", "cancelled_by", "cancelled_at"])
    return leave, released, previous


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------
def summary_for_staff(staff: StaffProfile, year: int) -> dict:
    """Days of leave per type in calendar `year`: `approved` and `pending`
    (weekly-off days and holidays don't count). A leave that spans New Year only
    counts the days inside `year`. When the campus sets a quota, `quota` and
    `remaining` (quota - approved - pending, never below 0) are included."""
    start, end = date(year, 1, 1), date(year, 12, 31)
    off = weekly_off_set(staff.campus)
    half = half_day_set(staff.campus)
    holidays = holiday_dates(staff.campus_id, start, end)
    out = {
        "approved": defaultdict(float),
        "pending": defaultdict(float),
    }
    for leave in StaffLeave.objects.filter(
        staff=staff, status__in=LIVE_STATUSES, date_from__lte=end, date_to__gte=start
    ):
        days = _days_within(
            is_half_day=leave.is_half_day, date_from=leave.date_from, date_to=leave.date_to,
            lo=start, hi=end, off=off, holidays=holidays, half=half,
        )
        bucket = out["approved" if leave.status == StaffLeave.Status.APPROVED else "pending"]
        bucket[leave.leave_type] += days
    result = {
        "year": year,
        "approved": dict(out["approved"]),
        "pending": dict(out["pending"]),
        "approved_total": sum(out["approved"].values()),
        "pending_total": sum(out["pending"].values()),
    }
    quota = {}
    for leave_type, limit in (getattr(staff.campus, "leave_quota", None) or {}).items():
        if leave_type in StaffLeave.LeaveType.values:
            try:
                quota[leave_type] = float(limit)
            except (TypeError, ValueError):
                continue
    if quota:
        result["quota"] = quota
        result["remaining"] = {
            t: max(limit - out["approved"].get(t, 0.0) - out["pending"].get(t, 0.0), 0.0)
            for t, limit in quota.items()
        }
    return result
