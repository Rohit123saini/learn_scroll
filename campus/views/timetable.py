# campus/views/timetable.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from ..models import (
    AcademicSession,
    ClassMode,
    Section,
    TimeSlot,
    TimetableEntry,
)
from ..throttles import CampusLiveSessionJoinThrottle
from ..serializers import (
    CampusLiveSessionSerializer,
    TimeSlotSerializer,
    TimetableConflictCheckSerializer,
    TimetableEntrySerializer,
)
from rest_framework.permissions import IsAuthenticated
from ..permissions import (
    IsSectionSubjectStaffOrReadOnly,
    is_campus_admin_or_principal,
)
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError,
)
from django.db.models import Q
from rest_framework.response import Response
from ..soft_delete_views import SoftDeleteViewSetMixin
from rest_framework.decorators import action
from .. import live_class
from rest_framework import (
    status,
    viewsets,
)
from django.db import transaction
from ._common import (
    CampusMemberScopedMixin,
    logger,
)


# ============================================================
# Phase 5 — timetable & attendance
# ============================================================
class TimeSlotViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = TimeSlotSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        # students only ever learn the slots of ONLINE periods (§G)
        return Q(pk__in=TimetableEntry.objects.filter(
            section_id__in=vis.visible_section_ids, mode=ClassMode.ONLINE
        ).values("time_slot_id"))

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(TimeSlot.objects.all(), self.request)

    def perform_update(self, serializer):
        # Changing a slot's day/time moves every entry using it: refuse if that would
        # double-book a teacher / room / section against another slot's entries.
        from .. import timetable_conflicts as tc

        d = serializer.validated_data
        slot = serializer.instance
        day = d.get("day_of_week", slot.day_of_week)
        start = d.get("start_time", slot.start_time)
        end = d.get("end_time", slot.end_time)
        if (day, start, end) != (slot.day_of_week, slot.start_time, slot.end_time):
            found = []
            for e in TimetableEntry.objects.filter(time_slot=slot):
                found += tc.find_conflicts(
                    session_id=e.session_id, day_of_week=day, start_time=start, end_time=end,
                    section_id=e.section_id, staff_id=e.staff_id, room_id=e.room_id,
                    exclude_pk=e.pk, exclude_slot_id=slot.pk,
                )
            if found:
                raise tc.conflict_validation_error(found)
        super().perform_update(serializer)


class TimetableEntryViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    Clash-detection is enforced in `TimetableEntry.clean()`/`save()` at
    the model layer (design doc §5) — the serializer's
    `DjangoCleanValidationMixin` turns that into a normal 400 here.
    """
    serializer_class = TimetableEntrySerializer
    campus_field_path = "section__school_class__campus"
    member_scope = "section"
    member_section_path = "section"
    # [T4 §C] admin/principal anywhere; class-teacher / approved subject-teacher
    # (for that subject) / moderator ONLY for their own section.
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(
            TimetableEntry.objects.select_related("section__school_class", "time_slot"), self.request
        )

    def get_section_subject_for_permission_check(self, request):
        section_id = request.data.get("section")
        subject_id = request.data.get("subject")
        if not section_id and self.kwargs.get("pk"):
            obj = self.get_queryset().filter(pk=self.kwargs["pk"]).first()
            if obj:
                return obj.section.school_class.campus_id, obj.section_id, obj.subject_id
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id, section.id, subject_id
        return None, None, None

    def get_campus_id_for_permission_check(self, request):
        section_id = request.data.get("section")
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)

    def get_permissions(self):
        # `go-live` authorizes inline (the period's teacher, an assigned teacher,
        # a manager AND today's substitute teacher) — see live_class.can_go_live_for_entry.
        if self.action == "go_live":
            return [IsAuthenticated()]
        return super().get_permissions()

    # ---- conflict detection (see campus/timetable_conflicts.py) ----------------
    def _raise_if_conflicting(self, serializer):
        from .. import timetable_conflicts as tc

        d, inst = serializer.validated_data, serializer.instance
        pick = lambda n: d[n] if n in d else getattr(inst, n, None)
        section, staff, slot, session, room = (pick(n) for n in ("section", "staff", "time_slot", "session", "room"))
        if not (section and staff and slot and session):
            return
        campus_id = section.school_class.campus_id
        parts = [staff.campus_id, slot.campus_id, session.campus_id] + ([room.campus_id] if room else [])
        if any(c != campus_id for c in parts):
            # never describe another campus's timetable in an error message
            raise ValidationError({"detail": "Section, staff, time slot, room and session must belong to the same campus.",
                                   "code": "campus_mismatch"})
        conflicts = tc.find_conflicts_for_values(
            session=session, time_slot=slot, section=section, staff=staff, room=room,
            exclude_pk=inst.pk if inst else None,
        )
        if conflicts:
            raise tc.conflict_validation_error(conflicts)

    def perform_create(self, serializer):
        self._raise_if_conflicting(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._raise_if_conflicting(serializer)
        super().perform_update(serializer)

    @action(detail=False, methods=["post"], url_path="check-conflicts")
    def check_conflicts(self, request):
        """Pre-check without saving. Body: section, staff, time_slot, session, [room],
        [entry] (id being edited), [subject] (lets an approved subject teacher use it).
        200 {"has_conflicts": bool, "conflicts": [{type, message, entry}]}"""
        from .. import timetable_conflicts as tc

        ser = TimetableConflictCheckSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data
        conflicts = tc.find_conflicts_for_values(
            session=v["session"], time_slot=v["time_slot"], section=v["section"], staff=v["staff"],
            room=v.get("room"), exclude_pk=v["entry"].pk if v.get("entry") else None,
        )
        return Response({"has_conflicts": bool(conflicts), "conflicts": conflicts})

    @action(detail=False, methods=["get"], url_path="conflicts")
    def conflicts(self, request):
        """Admin/principal audit of clashes already saved. ?campus=<id> [&session=<id>]
        (default: the campus's current session)."""
        from .. import timetable_conflicts as tc

        campus_id = request.query_params.get("campus")
        if not campus_id:
            raise ValidationError({"campus": "This query parameter is required."})
        if not is_campus_admin_or_principal(request.user, campus_id):
            raise PermissionDenied("Only an admin or principal can audit the timetable.")
        sessions = AcademicSession.objects.filter(campus_id=campus_id)
        sid = request.query_params.get("session")
        session = sessions.filter(pk=sid).first() if sid else sessions.filter(is_current=True).first()
        if session is None:
            raise ValidationError({"session": "No such session (and the campus has no current one)."})
        entries = list(TimetableEntry.objects.filter(session=session, section__school_class__campus_id=campus_id)
                       .select_related("section__school_class", "subject", "staff__user", "room", "time_slot"))
        out = []
        for kind, a, b in tc.scan_entries(entries):
            da, db = tc.describe_entry(a), tc.describe_entry(b)
            out.append({"type": kind, "message": tc._message(kind, db) , "entries": [da, db]})
        return Response({"campus": str(campus_id), "session": str(session.pk), "count": len(out), "conflicts": out})

    @action(detail=True, methods=["post"], url_path="go-live", throttle_classes=[CampusLiveSessionJoinThrottle])
    def go_live(self, request, pk=None):
        """
        ONE TAP: `POST /timetable/{id}/go-live/` takes today's occurrence of this
        online period live — creates (or resumes / adopts) the live session, makes
        sure the video room exists, tells the section's students "class is live"
        and returns the teacher's own join token, so the app can open the room
        straight away. Idempotent: tapping again just resumes the same room.

        201 when a new session was created, else 200:
            {"session": {...}, "room_name": "...", "token": "...", "livekit_url": "wss://...",
             "created": bool, "resumed": bool, "reopened": bool}
        Errors are `{"detail", "code"}`: not_allowed 403 · offline_class / not_today /
        outside_session / too_early / slot_over 400 · already_ended 409 ·
        video_unavailable / no_room 503 (the start is rolled back — nobody is notified).
        Students' attendance is then built automatically when the class ends.
        """
        entry = self.get_object()
        try:
            with transaction.atomic():
                result = live_class.go_live_from_entry(entry=entry, user=request.user)
                token = live_class.mint_join_token(result.session, request.user)
                live_class.record_join(result.session, request.user)
        except live_class.LiveClassError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)
        if result.announce:
            # Best-effort and AFTER commit: a push problem must not undo a class that did start.
            try:
                live_class.announce_live(result.session)
            except Exception:
                logger.exception("Class-is-live push failed for live session %s.", result.session.pk)
        serializer = CampusLiveSessionSerializer(result.session, context=self.get_serializer_context())
        return Response(
            {
                "session": serializer.data,
                "room_name": result.session.room_id,
                "token": token,
                "livekit_url": live_class.livekit_url(),
                "created": result.created,
                "resumed": result.resumed,
                "reopened": result.reopened,
            },
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )
