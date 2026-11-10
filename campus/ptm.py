# campus/ptm.py
"""
PTM (parent-teacher meeting) SCHEDULING — the ONE place that owns the rules.

    admin creates an event (a day + time window + slot length)
      -> teachers open their slots inside that window
      -> admin publishes (parents of the invited sections are told)
      -> a parent books ONE slot per child per teacher
      -> reschedule / cancel (parent until a cut-off, staff any time)
      -> reminders (24 h + 1 h) -> teacher records the outcome

Rules (all tunable per campus in `ParentTeacherSettings`, see `parent_comms`):
  * only a REAL linked parent books, only for their own child, only with a teacher
    who actually teaches that child, only in an event that invites the child's
    section;
  * a slot holds one live booking (DB-enforced, so two parents racing cannot both
    win); one live booking per child per teacher per event; a parent cannot hold
    two overlapping meetings, nor more than `ptm_max_bookings_per_parent`;
  * booking closes `ptm_booking_cutoff_minutes` before the slot; a parent can't
    cancel/reschedule within `ptm_cancel_cutoff_minutes` of the start (staff can);
  * reschedule = move to ANOTHER slot of the SAME teacher in the SAME event, atomic
    (the old slot is freed in the same transaction the new one is taken);
  * the teacher's `private_note` never leaves the staff side.

Pure ORM + dates; no DRF, no Celery, no push. `campus.ptm_push` does the
notifications, `campus.ptm_views` the HTTP.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import parent_comms as pc
from .models import PTMBooking, PTMEvent, PTMSlot, StaffProfile
from .parent_comms import CommsError

logger = logging.getLogger(__name__)

LIVE_STATUSES = (
    PTMBooking.Status.BOOKED,
    PTMBooking.Status.COMPLETED,
    PTMBooking.Status.NO_SHOW,
)
MAX_SLOTS_PER_OPEN = 120  # one request can't create an unbounded number of rows
MIN_SLOT_MINUTES, MAX_SLOT_MINUTES = 5, 60


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------

def start_dt(event, t):
    """Aware datetime of time `t` on the event's date (campus tz)."""
    return timezone.make_aware(datetime.combine(event.date, t))


def slot_start(slot):
    return start_dt(slot.event, slot.start_time)


def slot_end(slot):
    return start_dt(slot.event, slot.end_time)


def booking_start(booking):
    return slot_start(booking.slot)


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


# --------------------------------------------------------------------------
# audience
# --------------------------------------------------------------------------

def invited_section_ids(event) -> set:
    if event.all_sections:
        return pc.campus_section_ids(event.campus_id)
    return set(event.sections.values_list("pk", flat=True))


def event_teacher_rows(event) -> dict:
    """`teaching_staff_for_sections` for the event's invited sections."""
    return pc.teaching_staff_for_sections(invited_section_ids(event))


# --------------------------------------------------------------------------
# event lifecycle
# --------------------------------------------------------------------------

def validate_event_fields(*, date, start_time, end_time, slot_minutes):
    if end_time <= start_time:
        raise CommsError("invalid_window", "End time must be after the start time.")
    if not (MIN_SLOT_MINUTES <= slot_minutes <= MAX_SLOT_MINUTES):
        raise CommsError(
            "invalid_slot_length", f"Slot length must be {MIN_SLOT_MINUTES}-{MAX_SLOT_MINUTES} minutes."
        )
    if date < timezone.localdate():
        raise CommsError("past_date", "The meeting date is already over.")
    span = (
        datetime.combine(date, end_time) - datetime.combine(date, start_time)
    ).total_seconds() / 60
    if span < slot_minutes:
        raise CommsError("invalid_window", "The window is shorter than one slot.")


def publish_event(event) -> bool:
    """DRAFT -> PUBLISHED. Returns True only on the real transition (so the
    parents are told exactly once)."""
    with transaction.atomic():
        locked = PTMEvent.objects.select_for_update().get(pk=event.pk)
        if locked.status == PTMEvent.Status.PUBLISHED:
            return False
        if locked.status != PTMEvent.Status.DRAFT:
            raise CommsError("event_not_draft", "Only a draft meeting can be published.")
        if locked.date < timezone.localdate():
            raise CommsError("past_date", "The meeting date is already over.")
        if not PTMSlot.objects.filter(event=locked, is_blocked=False).exists():
            raise CommsError("no_slots", "No teacher has opened any slot yet.")
        locked.status = PTMEvent.Status.PUBLISHED
        locked.published_at = timezone.now()
        locked.save(update_fields=["status", "published_at"])
    event.refresh_from_db()
    return True


def close_event(event) -> bool:
    """PUBLISHED -> CLOSED (no new bookings; existing ones stand). Idempotent."""
    with transaction.atomic():
        locked = PTMEvent.objects.select_for_update().get(pk=event.pk)
        if locked.status == PTMEvent.Status.CLOSED:
            return False
        if locked.status != PTMEvent.Status.PUBLISHED:
            raise CommsError("event_not_published", "Only a published meeting can be closed.")
        locked.status = PTMEvent.Status.CLOSED
        locked.closed_at = timezone.now()
        locked.save(update_fields=["status", "closed_at"])
    event.refresh_from_db()
    return True


def cancel_event(event):
    """Any state -> CANCELLED; every live booking is cancelled with it. Returns the
    list of bookings that were just cancelled (the caller tells their parents).
    Idempotent: a second call returns `[]`."""
    with transaction.atomic():
        locked = PTMEvent.objects.select_for_update().get(pk=event.pk)
        if locked.status == PTMEvent.Status.CANCELLED:
            return []
        now = timezone.now()
        bookings = list(
            PTMBooking.objects.select_for_update()
            .filter(event=locked, status=PTMBooking.Status.BOOKED)
            .select_related("slot", "staff__user", "student", "parent")
        )
        for b in bookings:
            b.status = PTMBooking.Status.CANCELLED
            b.cancelled_by_role = "event"
            b.cancel_reason = "Meeting cancelled by the school"
            b.cancelled_at = now
            b.save(update_fields=["status", "cancelled_by_role", "cancel_reason", "cancelled_at", "updated_at"])
        locked.status = PTMEvent.Status.CANCELLED
        locked.closed_at = now
        locked.save(update_fields=["status", "closed_at"])
    event.refresh_from_db()
    return bookings


# --------------------------------------------------------------------------
# slots
# --------------------------------------------------------------------------

def open_slots(event, staff, start_time, end_time, location=""):
    """Create the bookable slots of `staff` inside [start_time, end_time] (default
    the whole event window), `event.slot_minutes` each. Existing slots are kept.
    Returns `(created_count, total_in_range)`."""
    if event.status not in (PTMEvent.Status.DRAFT, PTMEvent.Status.PUBLISHED):
        raise CommsError("event_locked", "This meeting no longer accepts new slots.")
    if staff.campus_id != event.campus_id or not staff.is_active:
        raise CommsError("invalid_teacher", "This teacher doesn't belong to this campus.")
    if staff.pk not in event_teacher_rows(event):
        raise CommsError("not_a_teacher_of_event", "This teacher doesn't teach any invited class.")
    start_time = start_time or event.start_time
    end_time = end_time or event.end_time
    if end_time <= start_time:
        raise CommsError("invalid_window", "End time must be after the start time.")
    if start_time < event.start_time or end_time > event.end_time:
        raise CommsError(
            "outside_event_window",
            f"Slots must be between {event.start_time:%H:%M} and {event.end_time:%H:%M}.",
        )
    step = timedelta(minutes=event.slot_minutes)
    cursor = datetime.combine(event.date, start_time)
    limit = datetime.combine(event.date, end_time)
    wanted = []
    while cursor + step <= limit:
        wanted.append((cursor.time(), (cursor + step).time()))
        cursor += step
    if not wanted:
        raise CommsError("invalid_window", "The window is shorter than one slot.")
    if len(wanted) > MAX_SLOTS_PER_OPEN:
        raise CommsError("too_many_slots", f"At most {MAX_SLOTS_PER_OPEN} slots at once.")
    with transaction.atomic():
        existing = set(
            PTMSlot.objects.filter(event=event, staff=staff).values_list("start_time", flat=True)
        )
        rows = [
            PTMSlot(event=event, staff=staff, start_time=s, end_time=e, location=(location or "")[:200])
            for s, e in wanted
            if s not in existing
        ]
        if rows:
            PTMSlot.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows), len(wanted)


def clear_slots(event, staff, start_time=None, end_time=None):
    """Remove the teacher's UNBOOKED slots in the range (default: all). Slots with a
    live booking are kept. Returns `(deleted, kept_with_bookings)`."""
    if event.status not in (PTMEvent.Status.DRAFT, PTMEvent.Status.PUBLISHED):
        raise CommsError("event_locked", "This meeting no longer accepts slot changes.")
    qs = PTMSlot.objects.filter(event=event, staff=staff)
    if start_time is not None:
        qs = qs.filter(start_time__gte=start_time)
    if end_time is not None:
        qs = qs.filter(end_time__lte=end_time)
    booked_ids = set(
        PTMBooking.objects.filter(slot__in=qs, status__in=LIVE_STATUSES).values_list("slot_id", flat=True)
    )
    to_delete = qs.exclude(pk__in=booked_ids)
    deleted = to_delete.count()
    to_delete.delete()
    return deleted, len(booked_ids)


def set_slot_blocked(slot, blocked: bool):
    """Block / unblock one slot. A slot with a live booking can't be blocked (cancel
    the booking first so the parent is told)."""
    if blocked and PTMBooking.objects.filter(slot=slot, status__in=LIVE_STATUSES).exists():
        raise CommsError("slot_has_booking", "This slot is booked. Cancel the booking first.")
    if slot.is_blocked != blocked:
        slot.is_blocked = blocked
        slot.save(update_fields=["is_blocked"])
    return slot


# --------------------------------------------------------------------------
# booking
# --------------------------------------------------------------------------

def _live_bookings_of_parent(event_id, parent_id, exclude_id=None):
    qs = PTMBooking.objects.filter(
        event_id=event_id, parent_id=parent_id, status__in=LIVE_STATUSES
    ).select_related("slot")
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return qs


def validate_bookable(slot, student, parent, *, settings=None, now=None, exclude_booking=None):
    """Every booking rule, in one place. Raises `CommsError`; returns the
    `ParentTeacherSettings` used. `exclude_booking` = the booking being moved
    (so it doesn't clash with itself)."""
    event = slot.event
    now = now or timezone.now()
    settings = settings or pc.get_settings(event.campus_id)
    exclude_id = exclude_booking.pk if exclude_booking is not None else None

    if not settings.ptm_enabled:
        raise CommsError("ptm_disabled", "Parent-teacher meetings are switched off for this campus.")
    if event.status != PTMEvent.Status.PUBLISHED:
        raise CommsError("event_not_open", "Booking is not open for this meeting.")
    if event.booking_opens_at and now < event.booking_opens_at:
        raise CommsError("booking_not_open_yet", "Booking has not opened yet.")
    if slot.is_blocked:
        raise CommsError("slot_blocked", "This slot isn't available.")
    cutoff = timedelta(minutes=settings.ptm_booking_cutoff_minutes)
    if slot_start(slot) - now < cutoff:
        raise CommsError("booking_closed_for_slot", "Booking for this slot has closed.")

    if not pc.is_real_linked_parent(parent, student.pk, event.campus_id):
        raise CommsError("not_a_parent", "You are not linked as this student's parent.")
    student_sections = pc.student_section_ids(student.pk, event.campus_id)
    if not student_sections or not (student_sections & invited_section_ids(event)):
        raise CommsError("not_invited", "This child's class isn't invited to this meeting.")
    if slot.staff_id not in pc.teaching_staff_for_sections(student_sections):
        raise CommsError("teacher_not_for_student", "This teacher doesn't teach your child.")

    dup = PTMBooking.objects.filter(
        event=event, student=student, staff_id=slot.staff_id, status__in=LIVE_STATUSES
    )
    if exclude_id:
        dup = dup.exclude(pk=exclude_id)
    if dup.exists():
        raise CommsError("already_booked_with_teacher", "You already have a slot with this teacher for this child.")

    mine = list(_live_bookings_of_parent(event.pk, parent.pk, exclude_id))
    if len(mine) >= settings.ptm_max_bookings_per_parent:
        raise CommsError("too_many_bookings", "You've reached the booking limit for this meeting.")
    s, e = slot_start(slot), slot_end(slot)
    for other in mine:
        if other.status == PTMBooking.Status.BOOKED and _overlaps(s, e, slot_start(other.slot), slot_end(other.slot)):
            raise CommsError("parent_time_clash", "You already have another meeting at this time.")
    taken = PTMBooking.objects.filter(slot=slot, status__in=LIVE_STATUSES)
    if exclude_id:
        taken = taken.exclude(pk=exclude_id)
    if taken.exists():
        raise CommsError("slot_taken", "Someone just booked this slot. Please pick another.")
    return settings


def book_slot(slot_id, student, parent, topic="", *, now=None):
    """Book. Race-safe: the slot row is locked and the DB constraint is the final
    judge. Returns the new `PTMBooking`."""
    now = now or timezone.now()
    try:
        with transaction.atomic():
            slot = PTMSlot.objects.select_for_update().select_related("event", "staff__user").get(pk=slot_id)
            validate_bookable(slot, student, parent, now=now)
            booking = PTMBooking.objects.create(
                campus_id=slot.event.campus_id,
                event=slot.event,
                slot=slot,
                staff=slot.staff,
                student=student,
                parent=parent,
                topic=(topic or "").strip()[:300],
                # booked inside the 24h window -> the "tomorrow" reminder would be noise
                reminder_24h_sent_at=now if slot_start(slot) - now <= timedelta(hours=24) else None,
            )
    except (PTMSlot.DoesNotExist, DjangoValidationError, ValueError, TypeError):
        raise CommsError("slot_not_found", "Slot not found.")
    except IntegrityError:
        raise CommsError("slot_taken", "Someone just booked this slot. Please pick another.")
    return booking


def _staff_may_manage(user, booking) -> bool:
    from .permissions import is_campus_admin_or_principal

    return booking.staff.user_id == user.pk or is_campus_admin_or_principal(user, booking.campus_id)


def role_for(user, booking):
    """`"parent"` (the booker), `"staff"` (the teacher / admin / principal) or None."""
    if booking.parent_id == user.pk:
        return "parent"
    if _staff_may_manage(user, booking):
        return "staff"
    return None


def _check_parent_cut_off(booking, settings, now):
    cutoff = timedelta(minutes=settings.ptm_cancel_cutoff_minutes)
    if booking_start(booking) - now < cutoff:
        raise CommsError(
            "too_late_to_change",
            "It's too close to the meeting to change it here. Please message the teacher.",
        )


def cancel_booking(booking, user, reason="", *, now=None) -> bool:
    """Cancel a BOOKED booking. Returns True if it changed (False = already
    cancelled: idempotent, nothing to notify). Parents are bound by the cut-off;
    staff are not (but can't touch a meeting that already finished)."""
    now = now or timezone.now()
    role = role_for(user, booking)
    if role is None:
        raise CommsError("not_allowed", "You can't change this booking.")
    with transaction.atomic():
        locked = PTMBooking.objects.select_for_update().select_related("slot__event").get(pk=booking.pk)
        if locked.status == PTMBooking.Status.CANCELLED:
            return False
        if locked.status != PTMBooking.Status.BOOKED:
            raise CommsError("already_finished", "This meeting is already marked as done.")
        if role == "parent":
            _check_parent_cut_off(locked, pc.get_settings(locked.campus_id), now)
        elif slot_end(locked.slot) < now:
            raise CommsError("already_finished", "This meeting is already over.")
        locked.status = PTMBooking.Status.CANCELLED
        locked.cancelled_by = user
        locked.cancelled_by_role = role
        locked.cancel_reason = (reason or "").strip()[:200]
        locked.cancelled_at = now
        locked.save(
            update_fields=["status", "cancelled_by", "cancelled_by_role", "cancel_reason", "cancelled_at", "updated_at"]
        )
    booking.refresh_from_db()
    return True


def reschedule_booking(booking, new_slot_id, user, *, now=None):
    """Move a BOOKED booking to another slot of the SAME teacher in the SAME event.
    Atomic. Returns `(new_booking, old_booking)`."""
    now = now or timezone.now()
    role = role_for(user, booking)
    if role is None:
        raise CommsError("not_allowed", "You can't change this booking.")
    try:
        with transaction.atomic():
            old = PTMBooking.objects.select_for_update().select_related("slot__event").get(pk=booking.pk)
            if old.status != PTMBooking.Status.BOOKED:
                raise CommsError("not_booked", "Only an active booking can be rescheduled.")
            new_slot = PTMSlot.objects.select_for_update().select_related("event", "staff__user").get(pk=new_slot_id)
            if new_slot.event_id != old.event_id or new_slot.staff_id != old.staff_id:
                raise CommsError("same_teacher_only", "You can only move to another slot of the same teacher.")
            if new_slot.pk == old.slot_id:
                raise CommsError("same_slot", "That is already your slot.")
            settings = pc.get_settings(old.campus_id)
            if role == "parent":
                _check_parent_cut_off(old, settings, now)
            # judge the new slot as if `old` weren't there
            validate_bookable(new_slot, old.student, old.parent, settings=settings, now=now, exclude_booking=old)
            old.status = PTMBooking.Status.CANCELLED
            old.cancelled_by = user
            old.cancelled_by_role = role
            old.cancel_reason = "Rescheduled"
            old.cancelled_at = now
            old.save(
                update_fields=["status", "cancelled_by", "cancelled_by_role", "cancel_reason", "cancelled_at", "updated_at"]
            )
            new = PTMBooking.objects.create(
                campus_id=old.campus_id,
                event=old.event,
                slot=new_slot,
                staff=old.staff,
                student=old.student,
                parent=old.parent,
                topic=old.topic,
                rescheduled_from=old,
                reminder_24h_sent_at=now if slot_start(new_slot) - now <= timedelta(hours=24) else None,
            )
    except (PTMSlot.DoesNotExist, DjangoValidationError, ValueError, TypeError):
        raise CommsError("slot_not_found", "Slot not found.")
    except IntegrityError:
        raise CommsError("slot_taken", "Someone just booked this slot. Please pick another.")
    return new, old


def record_outcome(booking, user, outcome, summary=None, private_note=None, *, now=None):
    """Teacher (or admin/principal) records how the meeting went. `outcome` is
    `completed` or `no_show`. Can be edited again later. Not before the slot
    starts, never on a cancelled booking. `summary` / `private_note` = None
    means "leave what is stored as it is" (so correcting only the outcome never
    wipes the teacher's notes); an empty string clears it."""
    now = now or timezone.now()
    if role_for(user, booking) != "staff":
        raise CommsError("not_allowed", "Only the teacher can record the outcome.")
    if outcome not in (PTMBooking.Status.COMPLETED, PTMBooking.Status.NO_SHOW):
        raise CommsError("invalid_outcome", "Outcome must be completed or no_show.")
    if booking.status == PTMBooking.Status.CANCELLED:
        raise CommsError("booking_cancelled", "This booking was cancelled.")
    if booking_start(booking) > now:
        raise CommsError("too_early", "The meeting hasn't started yet.")
    booking.status = outcome
    if summary is not None:
        booking.summary_for_parent = str(summary).strip()[:2000]
    if private_note is not None:
        booking.private_note = str(private_note).strip()[:2000]
    booking.completed_at = booking.completed_at or now
    booking.save(update_fields=["status", "summary_for_parent", "private_note", "completed_at", "updated_at"])
    return booking


# --------------------------------------------------------------------------
# read models
# --------------------------------------------------------------------------

def parent_slot_board(event, student, parent):
    """What a parent sees when booking for ONE child: every teacher of the child
    who has slots in the event, each slot marked available / taken / mine. Never
    reveals WHO booked a taken slot. Raises `CommsError` if the parent may not see
    it (not their child / not invited)."""
    if not pc.is_real_linked_parent(parent, student.pk, event.campus_id):
        raise CommsError("not_a_parent", "You are not linked as this student's parent.")
    sections = pc.student_section_ids(student.pk, event.campus_id)
    if not sections or not (sections & invited_section_ids(event)):
        raise CommsError("not_invited", "This child's class isn't invited to this meeting.")
    teachers = pc.teaching_staff_for_sections(sections)
    settings = pc.get_settings(event.campus_id)
    now = timezone.now()
    cutoff = timedelta(minutes=settings.ptm_booking_cutoff_minutes)

    slots = list(
        PTMSlot.objects.filter(event=event, staff_id__in=list(teachers.keys()), is_blocked=False)
        .order_by("start_time")
    )
    live = {
        b.slot_id: b
        for b in PTMBooking.objects.filter(slot__in=slots, status__in=LIVE_STATUSES)
    }
    my_with_teacher = {
        b.staff_id: b
        for b in PTMBooking.objects.filter(
            event=event, student=student, parent=parent, status__in=LIVE_STATUSES
        )
    }
    out = []
    for staff_id, row in sorted(
        teachers.items(), key=lambda kv: (not kv[1]["class_teacher"], pc.display_name(kv[1]["staff"].user).lower())
    ):
        t_slots = [s for s in slots if s.staff_id == staff_id]
        if not t_slots:
            continue
        mine = my_with_teacher.get(staff_id)
        items = []
        for s in t_slots:
            booking = live.get(s.pk)
            items.append(
                {
                    "id": str(s.pk),
                    "start_time": s.start_time.strftime("%H:%M"),
                    "end_time": s.end_time.strftime("%H:%M"),
                    "location": s.location or event.venue,
                    "state": (
                        "mine"
                        if booking is not None and booking.parent_id == parent.pk and booking.student_id == student.pk
                        else "taken"
                        if booking is not None
                        else "closed"
                        if start_dt(event, s.start_time) - now < cutoff
                        else "available"
                    ),
                }
            )
        out.append(
            {
                "staff": str(staff_id),
                "name": pc.display_name(row["staff"].user),
                "class_teacher": row["class_teacher"],
                "subjects": row["subjects"],
                "my_booking": str(mine.pk) if mine is not None else None,
                "slots": items,
            }
        )
    return out
