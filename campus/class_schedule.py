# campus/class_schedule.py
"""
T4 §G — online / offline classes: the ONE place that owns

  1. the visibility rule ("offline -> students get no time, only an
     'Offline class' label; online -> time shown; staff always see it"),
  2. resolving a weekly `TimetableEntry` into a real datetime for a given
     day (timezone-correct — `settings.TIME_ZONE`, the same wall-clock
     convention `TimeSlot` rows are authored in), and
  3. finding what is "starting soon" for the every-minute reminder task.

Keeping this out of serializers/views/tasks means the rule cannot drift
between the API response, the student's "my classes" cards and the push.
"""
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from .models import CampusLiveSession, ClassMode, StudentEnrollment, TimetableEntry

OFFLINE_LABEL = "Offline class"
# A weekly slot that already has an explicit live session around the same
# time is the SAME class — don't remind twice.
LIVE_SESSION_DEDUPE_WINDOW = timedelta(minutes=30)


def reminder_lead() -> timedelta:
    """How long before start the reminder goes out (default 5 min)."""
    return timedelta(minutes=int(getattr(settings, "CAMPUS_CLASS_REMINDER_LEAD_MINUTES", 5)))


# --------------------------------------------------------------------------
# visibility rule
# --------------------------------------------------------------------------

def time_visible_to(mode: str, viewer_is_staff: bool) -> bool:
    """Staff always see the time; everyone else only for ONLINE classes."""
    return bool(viewer_is_staff) or mode == ClassMode.ONLINE


def viewer_is_staff_for(request, campus_id) -> bool:
    """True when `request.user` is active staff of `campus_id`.

    `request` may be None (internal/trusted call, e.g. a Celery task or a
    shell) — that is treated as staff-level so nothing is hidden from
    code that is not serving a student.
    """
    if request is None:
        return True
    from .visibility import get_visibility

    return get_visibility(request.user, request).is_staff_in(campus_id)


# --------------------------------------------------------------------------
# weekly timetable -> concrete datetime
# --------------------------------------------------------------------------

def occurrence_start(entry: TimetableEntry, on_date) -> datetime:
    """Aware datetime at which `entry`'s weekly slot starts on `on_date`
    (caller guarantees `on_date.isoweekday() == slot.day_of_week`)."""
    tz = timezone.get_current_timezone()
    return datetime.combine(on_date, entry.time_slot.start_time, tzinfo=tz)


def next_occurrence(entry: TimetableEntry, now=None):
    """Next start (strictly after `now`) of this weekly entry within the
    entry's academic session, or None."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    session = entry.session
    for offset in range(0, 8):
        day = today + timedelta(days=offset)
        if day.isoweekday() != entry.time_slot.day_of_week:
            continue
        if session.start_date and day < session.start_date:
            continue
        if session.end_date and day > session.end_date:
            return None
        start = occurrence_start(entry, day)
        if start > now:
            return start
    return None


# --------------------------------------------------------------------------
# reminder discovery
# --------------------------------------------------------------------------

def due_live_sessions(now, window_end):
    """ONLINE, still-`scheduled` live sessions starting within
    `(now, window_end]` whose reminder hasn't gone out yet, at campuses
    that have reminders enabled."""
    return (
        CampusLiveSession.objects.filter(
            status=CampusLiveSession.Status.SCHEDULED,
            mode=ClassMode.ONLINE,
            reminder_sent_at__isnull=True,
            scheduled_at__gt=now,
            scheduled_at__lte=window_end,
            section__school_class__campus__is_active=True,
            section__school_class__campus__class_reminders_enabled=True,
        )
        .select_related("subject", "teacher__user", "section__school_class")
        .order_by("scheduled_at")
    )


def due_timetable_occurrences(now, window_end):
    """Yield `(entry, starts_at)` for every ONLINE weekly timetable entry
    whose next occurrence falls inside `(now, window_end]` (handles the
    window straddling midnight by checking both local dates)."""
    local_now = timezone.localtime(now)
    local_end = timezone.localtime(window_end)
    dates = {local_now.date(), local_end.date()}
    weekdays = {d.isoweekday() for d in dates}

    entries = (
        TimetableEntry.objects.filter(
            mode=ClassMode.ONLINE,
            time_slot__day_of_week__in=weekdays,
            session__start_date__lte=max(dates),
            session__end_date__gte=min(dates),
            section__school_class__campus__is_active=True,
            section__school_class__campus__class_reminders_enabled=True,
            staff__is_active=True,
        )
        .select_related("time_slot", "session", "subject", "staff__user", "section__school_class")
    )
    for entry in entries:
        for day in sorted(dates):
            if day.isoweekday() != entry.time_slot.day_of_week:
                continue
            starts_at = occurrence_start(entry, day)
            if now < starts_at <= window_end:
                yield entry, starts_at


def has_explicit_live_session(entry: TimetableEntry, starts_at) -> bool:
    """True when a (non-cancelled) online live session for the same
    section+subject sits within `LIVE_SESSION_DEDUPE_WINDOW` of this
    weekly occurrence — i.e. the teacher already scheduled this very
    class explicitly, and the live-session reminder covers it."""
    return CampusLiveSession.objects.filter(
        section_id=entry.section_id,
        subject_id=entry.subject_id,
        mode=ClassMode.ONLINE,
        status__in=[CampusLiveSession.Status.SCHEDULED, CampusLiveSession.Status.LIVE],
        scheduled_at__gte=starts_at - LIVE_SESSION_DEDUPE_WINDOW,
        scheduled_at__lte=starts_at + LIVE_SESSION_DEDUPE_WINDOW,
    ).exists()


def active_student_ids_for_section(section_id, session_id=None):
    qs = StudentEnrollment.objects.filter(section_id=section_id, status=StudentEnrollment.Status.ACTIVE)
    if session_id:
        qs = qs.filter(session_id=session_id)
    return list(qs.values_list("student_id", flat=True).distinct())
