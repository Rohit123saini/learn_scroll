# campus/timetable_conflicts.py
"""
Timetable conflict detection (teacher / room / section double booking).

ONE place that owns the rule, used by:
  * TimetableEntry.clean()                (unconditional safety net: admin/shell/bulk scripts)
  * TimetableEntryViewSet create/update   (structured 400, code="timetable_conflict")
  * POST /timetable-entries/check-conflicts/   (pre-check for the UI, no write)
  * GET  /timetable-entries/conflicts/         (audit of already-saved clashes)
  * TimeSlotViewSet.perform_update        (editing a slot's time must not create clashes)

Old rule: only the *exact same* TimeSlot row clashed. New rule: any two entries of
the same session on the same weekday whose time ranges OVERLAP clash when they
share the same staff, the same section or the same room (09:00-09:45 vs 09:30-10:15
is a clash; back-to-back 09:00-09:45 / 09:45-10:30 is not). The exact-same-slot
check is kept too, so nothing that used to be rejected is now allowed.
"""
from collections import defaultdict

from django.db.models import Q
from rest_framework.exceptions import ValidationError as DRFValidationError

KIND_STAFF, KIND_SECTION, KIND_ROOM = "staff", "section", "room"


def times_overlap(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _staff_name(staff) -> str:
    user = staff.user
    return (user.get_full_name() or user.username).strip()


def describe_entry(e) -> dict:
    slot = e.time_slot
    return {
        "id": str(e.id),
        "section_id": str(e.section_id),
        "section": str(e.section),
        "subject": e.subject.name,
        "staff_id": str(e.staff_id),
        "staff": _staff_name(e.staff),
        "room_id": str(e.room_id) if e.room_id else None,
        "room": e.room.name if e.room_id else None,
        "time_slot_id": str(slot.id),
        "day_of_week": str(slot.day_of_week),
        "start_time": slot.start_time.strftime("%H:%M"),
        "end_time": slot.end_time.strftime("%H:%M"),
        "label": slot.label,
    }


def _when(d) -> str:
    return f"{d['start_time']}-{d['end_time']}"


def _message(kind, d) -> str:
    if kind == KIND_STAFF:
        return (f"{d['staff']} is already teaching {d['subject']} to {d['section']} "
                f"at {_when(d)} (overlaps this period).")
    if kind == KIND_SECTION:
        return (f"{d['section']} already has {d['subject']} with {d['staff']} "
                f"at {_when(d)} (overlaps this period).")
    return (f"Room {d['room']} is already booked for {d['section']} ({d['subject']}) "
            f"at {_when(d)} (overlaps this period).")


def find_conflicts(*, session_id, day_of_week, start_time, end_time, section_id, staff_id,
                   room_id=None, time_slot_id=None, exclude_pk=None, exclude_slot_id=None):
    """Existing entries that clash with a (hypothetical) entry. Returns a list of
    `{"type", "message", "entry": {...}}`; one row per (entry, kind)."""
    from .models import TimetableEntry

    when = Q(time_slot__day_of_week=day_of_week,
             time_slot__start_time__lt=end_time, time_slot__end_time__gt=start_time)
    if time_slot_id:
        when |= Q(time_slot_id=time_slot_id)  # legacy exact-slot rule, always kept
    who = Q(staff_id=staff_id) | Q(section_id=section_id)
    if room_id:
        who |= Q(room_id=room_id)
    qs = (TimetableEntry.objects.filter(session_id=session_id).filter(when).filter(who)
          .select_related("section__school_class", "subject", "staff__user", "room", "time_slot")
          .order_by("time_slot__start_time", "id"))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    if exclude_slot_id:
        qs = qs.exclude(time_slot_id=exclude_slot_id)
    out = []
    for e in qs:
        d = describe_entry(e)
        kinds = []
        if e.staff_id == staff_id:
            kinds.append(KIND_STAFF)
        if e.section_id == section_id:
            kinds.append(KIND_SECTION)
        if room_id and e.room_id == room_id:
            kinds.append(KIND_ROOM)
        for k in kinds:
            out.append({"type": k, "message": _message(k, d), "entry": d})
    return out


def find_conflicts_for_values(*, session, time_slot, section, staff, room=None, exclude_pk=None):
    return find_conflicts(
        session_id=session.pk, day_of_week=time_slot.day_of_week,
        start_time=time_slot.start_time, end_time=time_slot.end_time,
        section_id=section.pk, staff_id=staff.pk, room_id=room.pk if room else None,
        time_slot_id=time_slot.pk, exclude_pk=exclude_pk,
    )


def find_conflicts_for_entry(entry):
    """Model-level helper (uses `_id`s so a half-filled instance never raises)."""
    if not (entry.session_id and entry.time_slot_id and entry.section_id and entry.staff_id):
        return []
    return find_conflicts_for_values(
        session=entry.session, time_slot=entry.time_slot, section=entry.section,
        staff=entry.staff, room=entry.room if entry.room_id else None,
        exclude_pk=entry.pk,
    )


def conflict_validation_error(conflicts) -> DRFValidationError:
    """400 body: {"detail", "code": "timetable_conflict", "conflicts": [...]} (the
    project's exception handler keeps `code`/extras at the top level)."""
    seen, msgs = set(), []
    for c in conflicts:
        if c["message"] not in seen:
            seen.add(c["message"])
            msgs.append(c["message"])
    return DRFValidationError({
        "detail": msgs[0] if len(msgs) == 1 else " ".join(msgs),
        "code": "timetable_conflict",
        "conflicts": conflicts,
    })


def scan_entries(entries):
    """Audit: find clashes among already-saved entries (needs `.time_slot` loaded).
    Returns [(kind, a, b)] — each clashing pair once per kind."""
    found = []
    for kind, key in ((KIND_STAFF, lambda e: e.staff_id), (KIND_SECTION, lambda e: e.section_id),
                      (KIND_ROOM, lambda e: e.room_id)):
        groups = defaultdict(list)
        for e in entries:
            k = key(e)
            if k is not None:
                groups[k].append(e)
        for items in groups.values():
            items.sort(key=lambda e: (e.time_slot.day_of_week, e.time_slot.start_time, e.time_slot.end_time))
            for i, a in enumerate(items):
                for b in items[i + 1:]:
                    sa, sb = a.time_slot, b.time_slot
                    same = a.time_slot_id == b.time_slot_id
                    if not same and (sb.day_of_week != sa.day_of_week or sb.start_time >= sa.end_time):
                        break
                    found.append((kind, a, b))
    return found
