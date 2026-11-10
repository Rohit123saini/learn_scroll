# campus/ptm_push.py
"""
Notifications for PTM (parent-teacher meeting) scheduling.

  published   -> every real parent of the invited sections (+ loginless Parent
                 Mode devices): "PTM on Sat 25 Oct - book your slot"      [push]
  cancelled   -> same audience + the teachers who opened slots: "PTM cancelled" [push]
  booked      -> the TEACHER (bell; push only if the meeting is within 24 h)
  rescheduled -> the other side of whoever moved it
  cancelled   -> booking cancelled by the parent -> teacher; by staff -> parent [push]
  outcome     -> the parent, only when the teacher wrote a summary for them
  reminder    -> parent + teacher, 24 h and 1 h before                    [push]

Rules worth knowing
  * the teacher's `private_note` is never put in any notification;
  * a parent who has cancelled is not reminded (only BOOKED rows are);
  * quiet hours / DND / muted types are honoured by `bridge.notify(push=True)`.

Runs inside Celery (`campus.tasks`), never in the request path: an event that
invites 800 parents is 800 rows. Everything cross-app goes through
`campus.bridge` (the campus golden rule).
"""
from __future__ import annotations

import logging

from django.utils import timezone

from . import bridge, parent_comms as pc, ptm
from .bridge import NotifTypes
from .models import CampusParentLink, StudentEnrollment

logger = logging.getLogger(__name__)


def _day(d) -> str:
    return f"{d:%a %d %b}"


def _time(t) -> str:
    return f"{t:%I:%M %p}".lstrip("0")


def _payload(notif_type, *, event, booking=None, kind=""):
    data = {
        "type": notif_type,
        "kind": kind,
        "event_id": str(event.pk),
        "campus_id": str(event.campus_id),
        "date": event.date.isoformat(),
    }
    if booking is not None:
        data["booking_id"] = str(booking.pk)
        data["student_id"] = str(booking.student_id)
    return data


def event_audience(event):
    """`(students, real_parents)` of every section the event invites."""
    sections = ptm.invited_section_ids(event)
    students, seen = [], set()
    if sections:
        qs = StudentEnrollment.objects.filter(
            section_id__in=sections, status=StudentEnrollment.Status.ACTIVE
        ).select_related("student")
        if pc.campus_has_current_session(event.campus_id):
            qs = qs.filter(session__is_current=True)
        for e in qs.order_by("id"):
            if e.student_id not in seen:
                seen.add(e.student_id)
                students.append(e.student)
    parents, seen_parent = [], set()
    if students:
        for link in CampusParentLink.objects.filter(
            campus_id=event.campus_id, student_id__in=seen
        ).select_related("parent"):
            p = link.parent
            if p.pk in seen_parent or pc.is_shadow_parent(p):
                continue
            seen_parent.add(p.pk)
            parents.append(p)
    return students, parents


def _event_line(event) -> str:
    where = f" · {event.venue}" if event.venue else ""
    return f"{_day(event.date)}, {_time(event.start_time)}-{_time(event.end_time)}{where}"


def _push_devices(students, title, body, data) -> int:
    sent = 0
    for student in students:
        try:
            sent += bridge.push_to_parent_devices(student=student, title=title, body=body, data=data)
        except Exception:  # pragma: no cover - helper already swallows
            logger.exception("ptm push to parent devices failed (student=%s).", student.pk)
    return sent


# --------------------------------------------------------------------------
# event level
# --------------------------------------------------------------------------

def notify_event_published(event) -> int:
    students, parents = event_audience(event)
    title = f"Parent-teacher meeting: {event.title}"
    body = f"{_event_line(event)}. Book your slot with your child's teachers."
    data = _payload(NotifTypes.CAMPUS_PTM_SCHEDULED, event=event, kind="published")
    sent = 0
    if parents:
        bridge.notify(
            users=parents, notif_type=NotifTypes.CAMPUS_PTM_SCHEDULED, title=title, body=body, data=data, push=True
        )
        sent += len(parents)
    # loginless Parent Mode devices can see the news but cannot book (no account)
    sent += _push_devices(students, title, body, data)
    return sent


def notify_event_cancelled(event) -> int:
    students, parents = event_audience(event)
    teacher_users = [row["staff"].user for row in _teachers_with_slots(event)]
    title = f"Parent-teacher meeting cancelled: {event.title}"
    body = f"{_day(event.date)} — this meeting is off. Any slot you booked is cancelled."
    data = _payload(NotifTypes.CAMPUS_PTM_CANCELLED, event=event, kind="event_cancelled")
    users = {u.pk: u for u in parents}
    users.update({u.pk: u for u in teacher_users})
    sent = 0
    if users:
        bridge.notify(
            users=list(users.values()), notif_type=NotifTypes.CAMPUS_PTM_CANCELLED,
            title=title, body=body, data=data, push=True,
        )
        sent += len(users)
    sent += _push_devices(students, title, body, data)
    return sent


def _teachers_with_slots(event):
    from .models import PTMSlot

    staff_ids = set(PTMSlot.objects.filter(event=event).values_list("staff_id", flat=True))
    rows = pc.teaching_staff_for_sections(ptm.invited_section_ids(event))
    return [r for sid, r in rows.items() if sid in staff_ids]


# --------------------------------------------------------------------------
# booking level
# --------------------------------------------------------------------------

def _booking_when(booking) -> str:
    return f"{_day(booking.event.date)}, {_time(booking.slot.start_time)}-{_time(booking.slot.end_time)}"


def _within_a_day(booking) -> bool:
    return 0 <= (ptm.booking_start(booking) - timezone.now()).total_seconds() <= 24 * 3600


def notify_booking_booked(booking) -> int:
    """Tell the teacher a parent booked."""
    child = pc.display_name(booking.student)
    topic = f" · “{booking.topic}”" if booking.topic else ""
    bridge.notify(
        users=[booking.staff.user],
        notif_type=NotifTypes.CAMPUS_PTM_BOOKED,
        title=f"PTM booked: {child}",
        body=f"{pc.display_name(booking.parent)} · {_booking_when(booking)}{topic}",
        data=_payload(NotifTypes.CAMPUS_PTM_BOOKED, event=booking.event, booking=booking, kind="booked"),
        push=_within_a_day(booking),
    )
    return 1


def notify_booking_rescheduled(new, old) -> int:
    """`new.cancelled_by_role` is empty; the mover is recorded on `old`."""
    child = pc.display_name(new.student)
    moved_by_parent = old.cancelled_by_role == "parent"
    recipient = new.staff.user if moved_by_parent else new.parent
    who = pc.display_name(new.parent) if moved_by_parent else pc.display_name(new.staff.user)
    bridge.notify(
        users=[recipient],
        notif_type=NotifTypes.CAMPUS_PTM_BOOKED,
        title=f"PTM moved: {child}",
        body=f"{who} moved the meeting to {_booking_when(new)}.",
        data=_payload(NotifTypes.CAMPUS_PTM_BOOKED, event=new.event, booking=new, kind="rescheduled"),
        push=True,
    )
    return 1


def notify_booking_cancelled(booking) -> int:
    """Cancelled by the parent -> teacher; by staff -> parent. (A whole-event
    cancel goes through `notify_event_cancelled`.)"""
    if booking.cancelled_by_role == "event":
        return 0
    child = pc.display_name(booking.student)
    by_parent = booking.cancelled_by_role == "parent"
    recipient = booking.staff.user if by_parent else booking.parent
    who = pc.display_name(booking.parent) if by_parent else pc.display_name(booking.staff.user)
    reason = f" Reason: {booking.cancel_reason}" if booking.cancel_reason else ""
    bridge.notify(
        users=[recipient],
        notif_type=NotifTypes.CAMPUS_PTM_CANCELLED,
        title=f"PTM cancelled: {child}",
        body=f"{who} cancelled the meeting on {_booking_when(booking)}.{reason}",
        data=_payload(NotifTypes.CAMPUS_PTM_CANCELLED, event=booking.event, booking=booking, kind="cancelled"),
        push=True,
    )
    return 1


def notify_outcome(booking) -> int:
    """Only when the teacher wrote something FOR the parent. Never the private note."""
    if not (booking.summary_for_parent or "").strip():
        return 0
    bridge.notify(
        users=[booking.parent],
        notif_type=NotifTypes.CAMPUS_PTM_BOOKED,
        title=f"Meeting summary: {pc.display_name(booking.student)}",
        body=f"{pc.display_name(booking.staff.user)} shared notes from your meeting.",
        data=_payload(NotifTypes.CAMPUS_PTM_BOOKED, event=booking.event, booking=booking, kind="outcome"),
        push=False,
    )
    return 1


def notify_reminder(booking, hours: int) -> int:
    when = f"in {hours} hour" if hours == 1 else "tomorrow"
    child = pc.display_name(booking.student)
    where = booking.slot.location or booking.event.venue
    where = f" · {where}" if where else ""
    data = _payload(NotifTypes.CAMPUS_PTM_REMINDER, event=booking.event, booking=booking, kind=f"reminder_{hours}h")
    bridge.notify(
        users=[booking.parent],
        notif_type=NotifTypes.CAMPUS_PTM_REMINDER,
        title=f"PTM {when}: {pc.display_name(booking.staff.user)}",
        body=f"{_booking_when(booking)}{where} · about {child}",
        data=data,
        push=True,
    )
    bridge.notify(
        users=[booking.staff.user],
        notif_type=NotifTypes.CAMPUS_PTM_REMINDER,
        title=f"PTM {when}: {pc.display_name(booking.parent)}",
        body=f"{_booking_when(booking)}{where} · about {child}",
        data=data,
        push=True,
    )
    return 2
