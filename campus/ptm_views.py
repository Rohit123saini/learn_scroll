# campus/ptm_views.py
"""
PTM (PARENT-TEACHER MEETING) API

    Events   — /campus/ptm/events/
      GET    ptm/events/                     list   (?campus= ?status= ?upcoming=1)
      POST   ptm/events/                     create a DRAFT (admin/principal)
      GET    ptm/events/{id}/                retrieve
      PATCH  ptm/events/{id}/                edit (admin/principal; date/time/slot length only while a draft with no slots)
      POST   ptm/events/{id}/publish/        DRAFT -> PUBLISHED, tells the invited parents (admin/principal)
      POST   ptm/events/{id}/close/          stop NEW bookings; existing ones stand (admin/principal)
      POST   ptm/events/{id}/cancel/         the day is off: every booking cancelled, parents told (admin/principal)
      GET    ptm/events/{id}/overview/       per-teacher slot/booking counts (admin/principal)
      GET    ptm/events/{id}/my-slots/       a teacher's own slots + who booked (admin may pass ?staff=)
      POST   ptm/events/{id}/open-slots/     {start_time?, end_time?, location?, staff?}  generate slots
      POST   ptm/events/{id}/clear-slots/    {start_time?, end_time?, staff?}  remove UNBOOKED slots
      GET    ptm/events/{id}/board/?student= what a PARENT sees when booking for one child

    Slots    — POST ptm/slots/{id}/block/    {blocked: bool}   (the teacher, or admin/principal)

    Bookings — /campus/ptm/bookings/
      GET    ptm/bookings/                   mine (parent: my bookings; teacher: meetings with me;
                                             admin: ?campus= shows the whole campus)
                                             filters ?event= ?staff= ?status=live|booked|cancelled|completed|no_show|all
                                             ?upcoming=1
      POST   ptm/bookings/                   {slot, student, topic?}   parent books
      GET    ptm/bookings/{id}/
      POST   ptm/bookings/{id}/cancel/       {reason?}
      POST   ptm/bookings/{id}/reschedule/   {slot}   same teacher, same event
      POST   ptm/bookings/{id}/outcome/      {outcome: completed|no_show, summary?, private_note?}  (teacher/admin)

    Rules live in `campus.ptm` (+ `campus.parent_comms`); business-rule
    failures come back as 400 with a stable machine `code` (see
    `parent_comms_api.CommsAPIError`). The booking's `private_note` is serialised
    for staff only. Push fans out through Celery after commit
    (`campus.tasks.send_ptm_*`).
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status as http, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.async_utils import dispatch_after_commit

from . import parent_comms as pc
from . import ptm
from . import tasks as campus_tasks
from .audit import log_action
from .models import ClassMode, PTMBooking, PTMEvent, PTMSlot, StaffProfile
from .parent_comms import CommsError
from .parent_comms_api import (
    CommsAPIError,
    api_error,
    booking_json,
    campus_role,
    event_json,
    hhmm,
    my_campus_ids,
    parse_date_value,
    parse_datetime_value,
    parse_time_value,
    require_role,
    resolve_campus,
    section_labels,
    sections_in_campus,
    slot_json,
    student_or_404,
    truthy,
    user_json,
    uuid_or_none,
)
from .permissions import get_staff_profile, is_campus_admin_or_principal
from .throttles import CampusPTMBookThrottle, CampusPTMManageThrottle

logger = logging.getLogger(__name__)

LIVE = ptm.LIVE_STATUSES
LIST_LIMIT = 300


# ============================================================
# helpers
# ============================================================

def _child_section_ids(user, campus_id) -> set:
    out: set = set()
    for _student, sections in pc.children_of_parent(user, campus_id):
        out.update(sections)
    return out


def _parent_may_see(user, event) -> bool:
    if event.status == PTMEvent.Status.DRAFT:
        return False
    return bool(_child_section_ids(user, event.campus_id) & ptm.invited_section_ids(event))


def _event_qs():
    return PTMEvent.objects.select_related("created_by").prefetch_related("sections__school_class")


def _load_event(request, pk):
    """`(event, role)` or a 404 (also for an event the caller may not see)."""
    eid = uuid_or_none(pk)
    event = _event_qs().filter(pk=eid).first() if eid else None
    role = campus_role(request.user, event.campus_id) if event is not None else None
    if event is None or role is None or (role == "parent" and not _parent_may_see(request.user, event)):
        raise api_error("event_not_found", "Meeting not found.")
    return event, role


def _staff_for(request, event, role):
    """The teacher whose slots the call is about: the caller's own staff row, or
    (admin/principal only) the one named by `staff`."""
    if role not in ("admin", "staff"):
        raise api_error("not_allowed", "Only teachers can manage slots.")
    raw = request.query_params.get("staff") or (request.data.get("staff") if hasattr(request, "data") else None)
    if raw:
        if role != "admin":
            raise api_error("not_allowed", "Only the school admin can act for another teacher.")
        sid = uuid_or_none(raw)
        staff = StaffProfile.objects.filter(pk=sid, campus_id=event.campus_id, is_active=True).select_related("user").first() if sid else None
        if staff is None:
            raise api_error("invalid_teacher", "Teacher not found.")
        return staff
    staff = get_staff_profile(request.user, event.campus_id)
    if staff is None:
        raise api_error("not_allowed", "You are not staff at this campus.")
    return staff


def _bookings_qs():
    return PTMBooking.objects.select_related("event", "slot", "staff__user", "student", "parent")


def _can_change(b, viewer_role, settings, now) -> bool:
    if b.status != PTMBooking.Status.BOOKED or b.event.status == PTMEvent.Status.CANCELLED:
        return False
    if viewer_role == "staff":
        return ptm.slot_end(b.slot) >= now
    from datetime import timedelta

    return ptm.booking_start(b) - now >= timedelta(minutes=settings.ptm_cancel_cutoff_minutes)


def _bookings_payload(request, rows):
    """JSON for bookings, each from the viewer's own point of view."""
    rows = list(rows)
    if not rows:
        return []
    now = timezone.now()
    labels, settings_by_campus, admin_campuses, out = {}, {}, set(), []
    for campus_id in {b.campus_id for b in rows}:
        labels.update(section_labels({b.student_id for b in rows if b.campus_id == campus_id}, campus_id))
        settings_by_campus[campus_id] = pc.get_settings(campus_id)
        if is_campus_admin_or_principal(request.user, campus_id):
            admin_campuses.add(campus_id)
    for b in rows:
        # same answer as `ptm.role_for`, without a query per row
        if b.parent_id == request.user.pk:
            viewer = "parent"
        elif b.staff.user_id == request.user.pk or b.campus_id in admin_campuses:
            viewer = "staff"
        else:
            continue
        out.append(
            booking_json(
                b,
                role=viewer,
                section_label=labels.get(b.student_id, ""),
                can_change=_can_change(b, viewer, settings_by_campus[b.campus_id], now),
            )
        )
    return out


def _fail(exc: CommsError):
    raise CommsAPIError(exc)


# ============================================================
# Events
# ============================================================
class PTMEventViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        if getattr(self, "action", None) in ("create", "publish", "cancel", "open_slots"):
            return [CampusPTMManageThrottle()]
        return super().get_throttles()

    # ---- list / retrieve ---------------------------------------------------
    def list(self, request):
        campus_id = resolve_campus(request)
        campuses = [campus_id] if campus_id else sorted(my_campus_ids(request.user))
        params = request.query_params
        results = []
        for cid in campuses:
            role = campus_role(request.user, cid)
            if role is None:
                continue
            qs = _event_qs().filter(campus_id=cid)
            if role == "parent":
                qs = qs.exclude(status=PTMEvent.Status.DRAFT)
            want = (params.get("status") or "").lower()
            if want in {c for c, _ in PTMEvent.Status.choices}:
                qs = qs.filter(status=want)
            if truthy(params.get("upcoming", "")):
                qs = qs.filter(date__gte=timezone.localdate()).exclude(status=PTMEvent.Status.CANCELLED)
            if role in ("admin", "staff"):
                qs = qs.annotate(
                    _slots=Count("slots", filter=Q(slots__is_blocked=False), distinct=True),
                    _live=Count("bookings", filter=Q(bookings__status__in=LIVE), distinct=True),
                )
                events = list(qs[:LIST_LIMIT])
                results += [event_json(e, role=role, slots_total=e._slots, bookings_live=e._live) for e in events]
            else:
                events = [e for e in qs[:LIST_LIMIT] if _parent_may_see(request.user, e)]
                mine = dict(
                    PTMBooking.objects.filter(parent=request.user, event__in=events, status__in=LIVE)
                    .order_by()
                    .values_list("event_id")
                    .annotate(n=Count("pk"))
                )
                results += [event_json(e, role="parent", my_bookings=mine.get(e.pk, 0)) for e in events]
        # upcoming -> soonest first; otherwise newest first
        results.sort(key=lambda e: (e["date"], e["start_time"]), reverse=not truthy(params.get("upcoming", "")))
        return Response({"count": len(results), "results": results})

    def retrieve(self, request, pk=None):
        event, role = _load_event(request, pk)
        if role == "parent":
            mine = PTMBooking.objects.filter(parent=request.user, event=event, status__in=LIVE).count()
            return Response(event_json(event, role="parent", my_bookings=mine))
        counts = PTMEvent.objects.filter(pk=event.pk).aggregate(
            s=Count("slots", filter=Q(slots__is_blocked=False), distinct=True),
            b=Count("bookings", filter=Q(bookings__status__in=LIVE), distinct=True),
        )
        return Response(event_json(event, role=role, slots_total=counts["s"], bookings_live=counts["b"]))

    # ---- create / edit -------------------------------------------------------
    @transaction.atomic
    def create(self, request):
        d = request.data
        cid = uuid_or_none(d.get("campus"))
        if cid is None or cid not in my_campus_ids(request.user):
            raise api_error("campus_not_found", "Campus not found.")
        require_role(request.user, cid, "admin")
        if not pc.get_settings(cid).ptm_enabled:
            raise api_error("ptm_disabled", "Parent-teacher meetings are switched off for this campus.")
        title = str(d.get("title") or "").strip()[:120]
        if not title:
            raise ValidationError({"title": "A title is required."})
        date = parse_date_value(d.get("date"), "date")
        start, end = parse_time_value(d.get("start_time"), "start_time"), parse_time_value(d.get("end_time"), "end_time")
        try:
            slot_minutes = int(d.get("slot_minutes") or 10)
        except (TypeError, ValueError):
            raise ValidationError({"slot_minutes": "Enter a number of minutes."})
        mode = str(d.get("mode") or ClassMode.OFFLINE)
        if mode not in ClassMode.values:
            raise ValidationError({"mode": "Invalid mode."})
        try:
            ptm.validate_event_fields(date=date, start_time=start, end_time=end, slot_minutes=slot_minutes)
        except CommsError as exc:
            _fail(exc)
        all_sections = truthy(d.get("all_sections", True))
        sections = [] if all_sections else sections_in_campus(d.get("sections"), cid)
        if not all_sections and not sections:
            raise ValidationError({"sections": "Pick at least one class, or invite the whole school."})
        event = PTMEvent.objects.create(
            campus_id=cid, title=title, description=str(d.get("description") or "").strip()[:500],
            date=date, start_time=start, end_time=end, slot_minutes=slot_minutes, mode=mode,
            venue=str(d.get("venue") or "").strip()[:200], all_sections=all_sections,
            booking_opens_at=parse_datetime_value(d.get("booking_opens_at"), "booking_opens_at"),
            created_by=request.user,
        )
        if sections:
            event.sections.set(sections)
        log_action(cid, request.user, "ptm.event.create", target=event, summary=f"PTM draft: {title} {date}")
        event = _event_qs().get(pk=event.pk)
        return Response(event_json(event, role="admin", slots_total=0, bookings_live=0), status=http.HTTP_201_CREATED)

    @transaction.atomic
    def partial_update(self, request, pk=None):
        event, role = _load_event(request, pk)
        require_role(request.user, event.campus_id, "admin")
        d = request.data
        if event.status in (PTMEvent.Status.CLOSED, PTMEvent.Status.CANCELLED):
            raise api_error("event_locked", "This meeting can't be edited any more.")
        schedule_keys = {"date", "start_time", "end_time", "slot_minutes", "all_sections", "sections"}
        if schedule_keys & set(d.keys()):
            if event.status != PTMEvent.Status.DRAFT:
                raise api_error("event_locked", "Date, time and classes can't change after publishing. Cancel and create a new meeting.")
            if PTMSlot.objects.filter(event=event).exists():
                raise api_error("has_slots", "Teachers already opened slots. Ask them to clear the slots first.")
        if "title" in d:
            title = str(d.get("title") or "").strip()[:120]
            if not title:
                raise ValidationError({"title": "A title is required."})
            event.title = title
        if "description" in d:
            event.description = str(d.get("description") or "").strip()[:500]
        if "venue" in d:
            event.venue = str(d.get("venue") or "").strip()[:200]
        if "mode" in d:
            if str(d["mode"]) not in ClassMode.values:
                raise ValidationError({"mode": "Invalid mode."})
            event.mode = str(d["mode"])
        if "booking_opens_at" in d:
            event.booking_opens_at = parse_datetime_value(d.get("booking_opens_at"), "booking_opens_at")
        sections = None
        if schedule_keys & set(d.keys()):
            date = parse_date_value(d.get("date", event.date.isoformat()), "date")
            start = parse_time_value(d.get("start_time", hhmm(event.start_time)), "start_time")
            end = parse_time_value(d.get("end_time", hhmm(event.end_time)), "end_time")
            try:
                slot_minutes = int(d.get("slot_minutes", event.slot_minutes))
            except (TypeError, ValueError):
                raise ValidationError({"slot_minutes": "Enter a number of minutes."})
            try:
                ptm.validate_event_fields(date=date, start_time=start, end_time=end, slot_minutes=slot_minutes)
            except CommsError as exc:
                _fail(exc)
            event.date, event.start_time, event.end_time, event.slot_minutes = date, start, end, slot_minutes
            if "all_sections" in d or "sections" in d:
                event.all_sections = truthy(d.get("all_sections", event.all_sections))
                sections = [] if event.all_sections else sections_in_campus(d.get("sections"), event.campus_id)
                if not event.all_sections and not sections:
                    raise ValidationError({"sections": "Pick at least one class, or invite the whole school."})
        event.save()
        if sections is not None:
            event.sections.set(sections)
        log_action(event.campus_id, request.user, "ptm.event.update", target=event, summary=f"PTM edited: {event.title}")
        event = _event_qs().get(pk=event.pk)
        return Response(event_json(event, role="admin"))

    # ---- lifecycle -------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="publish")
    def publish(self, request, pk=None):
        event, _ = _load_event(request, pk)
        require_role(request.user, event.campus_id, "admin")
        if not pc.get_settings(event.campus_id).ptm_enabled:
            raise api_error("ptm_disabled", "Parent-teacher meetings are switched off for this campus.")
        try:
            with transaction.atomic():
                changed = ptm.publish_event(event)
                if changed:
                    dispatch_after_commit(campus_tasks.send_ptm_event_notifications, str(event.pk), "published")
                    log_action(event.campus_id, request.user, "ptm.event.publish", target=event, summary=f"PTM published: {event.title}")
        except CommsError as exc:
            _fail(exc)
        return Response(event_json(_event_qs().get(pk=event.pk), role="admin"))

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        event, _ = _load_event(request, pk)
        require_role(request.user, event.campus_id, "admin")
        try:
            if ptm.close_event(event):
                log_action(event.campus_id, request.user, "ptm.event.close", target=event, summary=f"PTM booking closed: {event.title}")
        except CommsError as exc:
            _fail(exc)
        return Response(event_json(_event_qs().get(pk=event.pk), role="admin"))

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        event, _ = _load_event(request, pk)
        require_role(request.user, event.campus_id, "admin")
        was_live = event.status != PTMEvent.Status.CANCELLED
        was_draft = event.status == PTMEvent.Status.DRAFT
        with transaction.atomic():
            cancelled = ptm.cancel_event(event)
            if was_live:
                if not was_draft:  # a draft was never announced: nobody to tell
                    dispatch_after_commit(campus_tasks.send_ptm_event_notifications, str(event.pk), "cancelled")
                log_action(
                    event.campus_id, request.user, "ptm.event.cancel", target=event,
                    summary=f"PTM cancelled: {event.title} ({len(cancelled)} booking(s) cancelled)",
                )
        data = event_json(_event_qs().get(pk=event.pk), role="admin")
        data["bookings_cancelled"] = len(cancelled)
        return Response(data)

    # ---- admin overview ----------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="overview")
    def overview(self, request, pk=None):
        event, _ = _load_event(request, pk)
        require_role(request.user, event.campus_id, "admin")
        rows = ptm.event_teacher_rows(event)
        slot_counts = {
            r["staff_id"]: r["n"]
            for r in PTMSlot.objects.filter(event=event, is_blocked=False).order_by().values("staff_id").annotate(n=Count("pk"))
        }
        booking_counts: dict = {}
        for r in PTMBooking.objects.filter(event=event).order_by().values("staff_id", "status").annotate(n=Count("pk")):
            booking_counts.setdefault(r["staff_id"], {})[r["status"]] = r["n"]
        teachers = []
        for staff_id, row in rows.items():
            c = booking_counts.get(staff_id, {})
            teachers.append(
                {
                    "staff": str(staff_id),
                    "name": pc.display_name(row["staff"].user),
                    "class_teacher": row["class_teacher"],
                    "subjects": row["subjects"],
                    "slots": slot_counts.get(staff_id, 0),
                    "booked": c.get("booked", 0),
                    "completed": c.get("completed", 0),
                    "no_show": c.get("no_show", 0),
                    "cancelled": c.get("cancelled", 0),
                }
            )
        teachers.sort(key=lambda t: (t["slots"] > 0, t["name"].lower()))  # teachers with NO slots first
        return Response(
            {
                "event": event_json(event, role="admin"),
                "teachers": teachers,
                "teachers_without_slots": sum(1 for t in teachers if t["slots"] == 0),
            }
        )

    # ---- teacher: own slots ------------------------------------------------------
    def _my_slots_payload(self, request, event, staff, role):
        slots = list(PTMSlot.objects.filter(event=event, staff=staff).select_related("event").order_by("start_time"))
        live = {
            b.slot_id: b
            for b in _bookings_qs().filter(slot__in=slots, status__in=LIVE)
        }
        return {
            "event": event_json(event, role=role),
            "staff": {"id": str(staff.pk), "user": str(staff.user_id), "name": pc.display_name(staff.user)},
            "can_open": staff.pk in ptm.event_teacher_rows(event),
            "slots": [slot_json(s, booking=live.get(s.pk), event=event) for s in slots],
        }

    @action(detail=True, methods=["get"], url_path="my-slots")
    def my_slots(self, request, pk=None):
        event, role = _load_event(request, pk)
        staff = _staff_for(request, event, role)
        return Response(self._my_slots_payload(request, event, staff, role))

    @action(detail=True, methods=["post"], url_path="open-slots")
    def open_slots(self, request, pk=None):
        event, role = _load_event(request, pk)
        staff = _staff_for(request, event, role)
        d = request.data
        start = parse_time_value(d["start_time"], "start_time") if d.get("start_time") else None
        end = parse_time_value(d["end_time"], "end_time") if d.get("end_time") else None
        try:
            created, total = ptm.open_slots(event, staff, start, end, str(d.get("location") or ""))
        except CommsError as exc:
            _fail(exc)
        data = self._my_slots_payload(request, event, staff, role)
        data.update({"created": created, "total": total})
        return Response(data)

    @action(detail=True, methods=["post"], url_path="clear-slots")
    def clear_slots(self, request, pk=None):
        event, role = _load_event(request, pk)
        staff = _staff_for(request, event, role)
        d = request.data
        start = parse_time_value(d["start_time"], "start_time") if d.get("start_time") else None
        end = parse_time_value(d["end_time"], "end_time") if d.get("end_time") else None
        try:
            deleted, kept = ptm.clear_slots(event, staff, start, end)
        except CommsError as exc:
            _fail(exc)
        data = self._my_slots_payload(request, event, staff, role)
        data.update({"deleted": deleted, "kept_with_bookings": kept})
        return Response(data)

    # ---- parent: the board -------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="board")
    def board(self, request, pk=None):
        event, role = _load_event(request, pk)
        if role != "parent":
            raise api_error("not_allowed", "Only parents book slots.")
        student = student_or_404(request.query_params.get("student"))
        try:
            teachers = ptm.parent_slot_board(event, student, request.user)
        except CommsError as exc:
            _fail(exc)
        settings = pc.get_settings(event.campus_id)
        return Response(
            {
                "event": event_json(event, role="parent"),
                "student": user_json(student),
                "teachers": teachers,
                "can_book": event.status == PTMEvent.Status.PUBLISHED and settings.ptm_enabled,
                "rules": {
                    "booking_cutoff_minutes": settings.ptm_booking_cutoff_minutes,
                    "cancel_cutoff_minutes": settings.ptm_cancel_cutoff_minutes,
                    "max_bookings": settings.ptm_max_bookings_per_parent,
                },
            }
        )


# ============================================================
# Slots
# ============================================================
class PTMSlotViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["post"], url_path="block")
    def block(self, request, pk=None):
        sid = uuid_or_none(pk)
        slot = PTMSlot.objects.select_related("event", "staff__user").filter(pk=sid).first() if sid else None
        if slot is None:
            raise api_error("slot_not_found", "Slot not found.")
        cid = slot.event.campus_id
        role = campus_role(request.user, cid)
        if role not in ("admin", "staff"):
            raise api_error("slot_not_found", "Slot not found.")
        if slot.staff.user_id != request.user.pk and role != "admin":
            raise api_error("not_allowed", "That slot belongs to another teacher.")
        if slot.event.status not in (PTMEvent.Status.DRAFT, PTMEvent.Status.PUBLISHED):
            raise api_error("event_locked", "This meeting no longer accepts slot changes.")
        blocked = truthy(request.data.get("blocked", True))
        try:
            ptm.set_slot_blocked(slot, blocked)
        except CommsError as exc:
            _fail(exc)
        return Response(slot_json(slot, event=slot.event))


# ============================================================
# Bookings
# ============================================================
class PTMBookingViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        if getattr(self, "action", None) in ("create", "reschedule"):
            return [CampusPTMBookThrottle()]
        return super().get_throttles()

    def _load(self, request, pk):
        bid = uuid_or_none(pk)
        booking = _bookings_qs().filter(pk=bid).first() if bid else None
        if booking is None or ptm.role_for(request.user, booking) is None:
            raise api_error("booking_not_found", "Booking not found.")
        return booking

    def list(self, request):
        campus_id = resolve_campus(request)
        params = request.query_params
        qs = _bookings_qs()
        if campus_id:
            qs = qs.filter(campus_id=campus_id)
            if campus_role(request.user, campus_id) == "admin" and not truthy(params.get("mine", "")):
                pass  # the whole campus
            else:
                qs = qs.filter(Q(parent=request.user) | Q(staff__user=request.user))
        else:
            qs = qs.filter(campus_id__in=my_campus_ids(request.user)).filter(
                Q(parent=request.user) | Q(staff__user=request.user)
            )
        if params.get("event"):
            qs = qs.filter(event_id=uuid_or_none(params["event"]) or None)
        if params.get("staff"):
            qs = qs.filter(staff_id=uuid_or_none(params["staff"]) or None)
        want = (params.get("status") or "live").lower()
        if want == "live":
            qs = qs.filter(status__in=LIVE)
        elif want != "all":
            qs = qs.filter(status=want)
        if truthy(params.get("upcoming", "")):
            qs = qs.filter(event__date__gte=timezone.localdate()).exclude(event__status=PTMEvent.Status.CANCELLED)
        rows = list(qs.order_by("event__date", "slot__start_time")[:LIST_LIMIT])
        results = _bookings_payload(request, rows)
        return Response({"count": len(results), "results": results})

    def retrieve(self, request, pk=None):
        booking = self._load(request, pk)
        return Response(_bookings_payload(request, [booking])[0])

    def create(self, request):
        d = request.data
        sid = uuid_or_none(d.get("slot"))
        if sid is None:
            raise api_error("slot_not_found", "Slot not found.")
        student = student_or_404(d.get("student"))
        try:
            with transaction.atomic():
                booking = ptm.book_slot(sid, student, request.user, str(d.get("topic") or ""))
                dispatch_after_commit(campus_tasks.send_ptm_booking_notifications, str(booking.pk), "booked")
        except CommsError as exc:
            _fail(exc)
        booking = _bookings_qs().get(pk=booking.pk)
        return Response(_bookings_payload(request, [booking])[0], status=http.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        booking = self._load(request, pk)
        try:
            with transaction.atomic():
                changed = ptm.cancel_booking(booking, request.user, str(request.data.get("reason") or ""))
                if changed:
                    dispatch_after_commit(campus_tasks.send_ptm_booking_notifications, str(booking.pk), "cancelled")
                    if ptm.role_for(request.user, booking) == "staff":
                        log_action(
                            booking.campus_id, request.user, "ptm.booking.cancel", target=booking,
                            summary=f"PTM booking cancelled by staff for {booking.event.date}",
                        )
        except CommsError as exc:
            _fail(exc)
        booking = _bookings_qs().get(pk=booking.pk)
        return Response(_bookings_payload(request, [booking])[0])

    @action(detail=True, methods=["post"], url_path="reschedule")
    def reschedule(self, request, pk=None):
        booking = self._load(request, pk)
        sid = uuid_or_none(request.data.get("slot"))
        if sid is None:
            raise api_error("slot_not_found", "Slot not found.")
        try:
            with transaction.atomic():
                new, old = ptm.reschedule_booking(booking, sid, request.user)
                dispatch_after_commit(
                    campus_tasks.send_ptm_booking_notifications, str(new.pk), "rescheduled", str(old.pk)
                )
        except CommsError as exc:
            _fail(exc)
        new = _bookings_qs().get(pk=new.pk)
        return Response(_bookings_payload(request, [new])[0])

    @action(detail=True, methods=["post"], url_path="outcome")
    def outcome(self, request, pk=None):
        booking = self._load(request, pk)
        d = request.data
        had_summary = bool((booking.summary_for_parent or "").strip())
        try:
            with transaction.atomic():
                ptm.record_outcome(
                    booking, request.user, str(d.get("outcome") or ""),
                    summary=d.get("summary") if "summary" in d else None,
                    private_note=d.get("private_note") if "private_note" in d else None,
                )
                if booking.summary_for_parent.strip() and not had_summary:
                    dispatch_after_commit(campus_tasks.send_ptm_booking_notifications, str(booking.pk), "outcome")
        except CommsError as exc:
            _fail(exc)
        booking = _bookings_qs().get(pk=booking.pk)
        return Response(_bookings_payload(request, [booking])[0])
