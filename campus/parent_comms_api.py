# campus/parent_comms_api.py
"""
Shared plumbing for the PTM API (`campus.ptm_views`) and the chat API
(`campus.parent_chat_views`): the error type, campus/role resolution, tolerant
query-param parsing, and the JSON shape of every object that crosses the wire.

JSON is built by hand (not DRF serializers) on purpose: each object has TWO
shapes - what the parent may see and what staff may see (the booking's
`private_note` exists only in the staff shape) - and building the shapes in one
reviewed place is how the private note stays private.
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dt_time
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.exceptions import APIException, ValidationError

from . import parent_comms as pc
from .models import CampusParentLink, Section, StaffProfile, StudentEnrollment
from .parent_comms import CommsError
from .permissions import get_staff_profile, is_campus_admin_or_principal
from . import visibility as vis_mod

logger = logging.getLogger(__name__)

_STATUS_BY_CODE = {
    "not_allowed": 403,
    "not_a_parent": 403,
    "chat_disabled": 403,
    "ptm_disabled": 403,
    "slot_not_found": 404,
    "event_not_found": 404,
    "thread_not_found": 404,
    "booking_not_found": 404,
    "campus_not_found": 404,
    "student_not_found": 404,
    "slot_taken": 409,
    "parent_time_clash": 409,
    "already_booked_with_teacher": 409,
}


class CommsAPIError(APIException):
    """A `CommsError` as an HTTP error: stable machine `code` (the app translates
    it) + human `detail`. The project's exception handler puts `machine_code`
    into the envelope's `code`."""

    status_code = 400

    def __init__(self, err):
        super().__init__(detail=err.message)
        self.machine_code = err.code
        self.status_code = _STATUS_BY_CODE.get(err.code, 400)


def api_error(code, message):
    return CommsAPIError(CommsError(code, message))


# --------------------------------------------------------------------------
# tolerant parsing
# --------------------------------------------------------------------------

def uuid_or_none(value):
    if not value:
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def parse_time_value(raw, field):
    """`HH:MM` / `HH:MM:SS` -> `datetime.time` (400 on garbage)."""
    if isinstance(raw, dt_time):
        return raw
    text = str(raw or "").strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValidationError({field: "Use the format HH:MM."})


def parse_date_value(raw, field):
    try:
        value = parse_date(str(raw or "").strip())
    except ValueError:
        value = None
    if value is None:
        raise ValidationError({field: "Use the format YYYY-MM-DD."})
    return value


def parse_datetime_value(raw, field):
    if raw in (None, ""):
        return None
    try:
        value = parse_datetime(str(raw).strip())
    except ValueError:
        value = None
    if value is None:
        raise ValidationError({field: "Use an ISO date-time."})
    from django.utils import timezone

    return timezone.make_aware(value) if timezone.is_naive(value) else value


def hhmm(t) -> str:
    return t.strftime("%H:%M") if t else ""


# --------------------------------------------------------------------------
# campus + role
# --------------------------------------------------------------------------

def my_campus_ids(user) -> set:
    return {str(c) for c in vis_mod.get_my_campus_ids(user)}


def resolve_campus(request, *, body=False):
    """Campus id the call is about. `?campus=` (or `campus` in the body when
    `body=True`). A campus the caller has no tie to is a 404 (never reveal it
    exists). With no param and exactly one campus, that one; else None."""
    raw = request.query_params.get("campus")
    if not raw and body:
        raw = request.data.get("campus")
    mine = my_campus_ids(request.user)
    if raw:
        cid = uuid_or_none(raw)
        if cid is None or cid not in mine:
            raise api_error("campus_not_found", "Campus not found.")
        return cid
    if len(mine) == 1:
        return next(iter(mine))
    return None


def campus_role(user, campus_id) -> str | None:
    """`admin` (admin/principal) | `staff` | `parent` (a real linked parent) | None."""
    if is_campus_admin_or_principal(user, campus_id):
        return "admin"
    if get_staff_profile(user, campus_id) is not None:
        return "staff"
    if not pc.is_shadow_parent(user) and CampusParentLink.objects.filter(parent=user, campus_id=campus_id).exists():
        return "parent"
    return None


def require_role(user, campus_id, *allowed):
    role = campus_role(user, campus_id)
    if role not in allowed:
        raise api_error("not_allowed", "You can't do this.")
    return role


def student_or_404(student_id):
    from django.contrib.auth import get_user_model

    sid = uuid_or_none(student_id) or student_id
    try:
        user = get_user_model().objects.filter(pk=sid).first()
    except (ValueError, TypeError, DjangoValidationError):
        user = None
    if user is None:
        raise api_error("student_not_found", "Student not found.")
    return user


def section_labels(student_ids, campus_id) -> dict:
    """`{student_id: "10 - A"}` for the students' current section at the campus."""
    qs = StudentEnrollment.objects.filter(
        student_id__in=list(student_ids),
        status=StudentEnrollment.Status.ACTIVE,
        section__school_class__campus_id=campus_id,
    ).select_related("section__school_class")
    if pc.campus_has_current_session(campus_id):
        qs = qs.filter(session__is_current=True)
    out = {}
    for e in qs:
        out.setdefault(e.student_id, f"{e.section.school_class.name} - {e.section.name}")
    return out


# --------------------------------------------------------------------------
# JSON shapes
# --------------------------------------------------------------------------

def user_json(user) -> dict:
    return {"id": str(user.pk), "name": pc.display_name(user)}


def staff_json(staff: StaffProfile) -> dict:
    return {"id": str(staff.pk), "user": str(staff.user_id), "name": pc.display_name(staff.user)}


def settings_json(s) -> dict:
    from . import parent_chat

    return {
        "campus": str(s.campus_id),
        "chat_enabled": s.chat_enabled,
        "chat_start_time": hhmm(s.chat_start_time),
        "chat_end_time": hhmm(s.chat_end_time),
        "chat_max_unanswered": parent_chat.max_unanswered(s),
        "chat_message_max_chars": parent_chat.max_chars(s),
        "ptm_enabled": s.ptm_enabled,
        "ptm_booking_cutoff_minutes": s.ptm_booking_cutoff_minutes,
        "ptm_cancel_cutoff_minutes": s.ptm_cancel_cutoff_minutes,
        "ptm_max_bookings_per_parent": s.ptm_max_bookings_per_parent,
    }


def event_json(event, *, role="parent", slots_total=None, bookings_live=None, my_bookings=None) -> dict:
    sections = []
    if not event.all_sections:
        for sec in event.sections.all():
            sections.append({"id": str(sec.pk), "label": f"{sec.school_class.name} - {sec.name}"})
    data = {
        "id": str(event.pk),
        "campus": str(event.campus_id),
        "title": event.title,
        "description": event.description,
        "date": event.date.isoformat(),
        "start_time": hhmm(event.start_time),
        "end_time": hhmm(event.end_time),
        "slot_minutes": event.slot_minutes,
        "mode": event.mode,
        "venue": event.venue,
        "all_sections": event.all_sections,
        "sections": sections,
        "booking_opens_at": event.booking_opens_at.isoformat() if event.booking_opens_at else None,
        "status": event.status,
        "published_at": event.published_at.isoformat() if event.published_at else None,
    }
    if role in ("admin", "staff"):
        data["slots_total"] = slots_total
        data["bookings_live"] = bookings_live
        data["created_by_name"] = pc.display_name(event.created_by) if event.created_by_id else ""
    if my_bookings is not None:
        data["my_bookings"] = my_bookings
    return data


def slot_json(slot, *, booking=None, role="staff", event=None) -> dict:
    event = event or slot.event
    data = {
        "id": str(slot.pk),
        "event": str(slot.event_id),
        "staff": str(slot.staff_id),
        "start_time": hhmm(slot.start_time),
        "end_time": hhmm(slot.end_time),
        "location": slot.location or event.venue,
        "is_blocked": slot.is_blocked,
        "booking": booking_json(booking, role="staff") if booking is not None else None,
    }
    return data


def booking_json(b, *, role, section_label="", can_change=None) -> dict:
    """`role` is the VIEWER's role on this booking: `parent` or `staff`. The
    private note is added for `staff` only."""
    ev, slot = b.event, b.slot
    data = {
        "id": str(b.pk),
        "campus": str(b.campus_id),
        "event": {
            "id": str(ev.pk), "title": ev.title, "date": ev.date.isoformat(), "mode": ev.mode,
            "venue": ev.venue, "status": ev.status,
        },
        "slot": {
            "id": str(slot.pk), "start_time": hhmm(slot.start_time), "end_time": hhmm(slot.end_time),
            "location": slot.location or ev.venue,
        },
        "staff": staff_json(b.staff),
        "student": user_json(b.student),
        "section_label": section_label,
        "parent": user_json(b.parent),
        "topic": b.topic,
        "status": b.status,
        "cancelled_by_role": b.cancelled_by_role,
        "cancel_reason": b.cancel_reason,
        "cancelled_at": b.cancelled_at.isoformat() if b.cancelled_at else None,
        "summary_for_parent": b.summary_for_parent,
        "completed_at": b.completed_at.isoformat() if b.completed_at else None,
        "rescheduled": b.rescheduled_from_id is not None,
    }
    if can_change is not None:
        data["can_change"] = can_change
    if role == "staff":
        data["private_note"] = b.private_note
    return data


def thread_json(t, *, side, block=None, unread=0, exact=True) -> dict:
    """`side`: `parent` | `teacher` | `admin` (flagged-thread oversight).

    `exact=False` (list rows) skips the per-thread eligibility check: `can_send`
    is then only "the thread is open" - the thread screen asks again with
    `exact=True` before the user can type."""
    from . import parent_chat

    other_name = (
        pc.display_name(t.teacher.user) if side == "parent" else pc.display_name(t.parent)
    )
    data = {
        "id": str(t.pk),
        "campus": str(t.campus_id),
        "student": user_json(t.student),
        "parent": user_json(t.parent),
        "teacher": staff_json(t.teacher),
        "counterpart_name": other_name,
        "status": t.status,
        "close_reason": t.close_reason,
        "flagged": t.flagged_at is not None,
        "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
        "last_message_preview": t.last_message_preview,
        "last_sender_role": t.last_sender_role,
        "unread": int(unread or 0),
        "side": side,
        "muted": t.parent_muted if side == "parent" else t.teacher_muted if side == "teacher" else False,
        "can_send": (
            side in ("parent", "teacher")
            and (block is None if exact else t.status == "open")
        ),
        "send_block": None if block is None else {"code": block[0], "message": block[1]},
    }
    if side == "admin" or t.flagged_at is not None:
        data["flag_reason"] = t.flag_reason
        data["flagged_at"] = t.flagged_at.isoformat() if t.flagged_at else None
    return data


def message_json(m, *, viewer_user_id) -> dict:
    return {
        "id": str(m.pk),
        "thread": str(m.thread_id),
        "sender_role": m.sender_role,
        "mine": str(m.sender_id) == str(viewer_user_id),
        "body": m.body,
        "after_hours": m.after_hours,
        "created_at": m.created_at.isoformat(),
    }


def sections_in_campus(section_ids, campus_id):
    """Validate client-supplied section ids: all must exist in `campus_id`."""
    ids = []
    for raw in section_ids or []:
        sid = uuid_or_none(raw)
        if sid is None:
            raise ValidationError({"sections": "Invalid section id."})
        ids.append(sid)
    found = list(Section.objects.filter(pk__in=ids, school_class__campus_id=campus_id))
    if len(found) != len(set(ids)):
        raise ValidationError({"sections": "One or more sections don't belong to this campus."})
    return found
