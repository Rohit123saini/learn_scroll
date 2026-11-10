# campus/views/live_sessions.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from ..models import (
    CampusLiveSession,
    ClassMode,
    Notice,
    Section,
    StudentEnrollment,
)
from ..throttles import (
    CampusLiveSessionJoinThrottle,
    CampusRecordingControlThrottle,
)
from ..serializers import CampusLiveSessionSerializer
from rest_framework.permissions import IsAuthenticated
from ..permissions import IsSectionSubjectStaffOrReadOnly
from ..bridge import NotifTypes
from rest_framework.exceptions import PermissionDenied
from django.db.models import Q
from rest_framework.response import Response
from ..soft_delete_views import SoftDeleteViewSetMixin
from rest_framework.decorators import action
from .. import bridge
from .. import live_class
from django.utils.dateparse import parse_date
from .. import recordings
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
# Phase 4 — tuition classes (coin-free)
# ============================================================
class CampusLiveSessionViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    Scheduling auto-fires a `Notice` + `core.Notification`
    (`CAMPUS_SESSION_SCHEDULED`) to the section, and attempts video-room
    provisioning through `bridge.provision_video_room` — both go through
    `campus.bridge`, never a direct `core`/`message`/`tuitionclass` import
    (design doc §4). `status`/`room_id` stay server-controlled; a
    teacher moves the session forward via the `start`/`end` actions
    below rather than PATCHing those fields directly.
    """
    serializer_class = CampusLiveSessionSerializer
    campus_field_path = "section__school_class__campus"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        qs = self.filter_queryset_to_my_campuses(
            CampusLiveSession.objects.select_related("section__school_class", "subject", "teacher__user"), self.request
        )
        # Optional list filters (additive; the app's "Live now" banner and the
        # timetable's Go live / Join buttons need only TODAY's sessions, not a
        # whole year): ?section=<id> ?status=live,ended ?timetable_entry=<id>
        # ?date=YYYY-MM-DD (the day the class ran / was planned for).
        p = self.request.query_params
        if p.get("section"):
            qs = qs.filter(section_id=p["section"])
        if p.get("timetable_entry"):
            qs = qs.filter(timetable_entry_id=p["timetable_entry"])
        wanted = [s for s in (p.get("status") or "").split(",") if s in CampusLiveSession.Status.values]
        if wanted:
            qs = qs.filter(status__in=wanted)
        day = parse_date(p.get("date") or "")
        if day:
            qs = qs.filter(Q(occurrence_date=day) | Q(occurrence_date__isnull=True, scheduled_at__date=day))
        return qs

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

    # TASK 14 — `join` (below) is a POST action a plain enrolled student
    # must be able to call, but the class-level `IsSectionSubjectStaffOrReadOnly`
    # only allows writes from that section/subject's staff (that's correct
    # for `start`/`end`/`cancel`/create, which stay on the default here).
    # `join` does its own, more specific authorization inline instead
    # (enrolled student OR that section/subject's staff OR campus admin —
    # see its own docstring), so it only needs `IsAuthenticated` at the
    # DRF-permission layer.
    def get_permissions(self):
        # `join` (any enrolled student) and the live-class actions below do their
        # own, more specific authorization inline — a substitute teacher is the
        # session's teacher but usually NOT an approved subject teacher, so the
        # class-level IsSectionSubjectStaffOrReadOnly would wrongly refuse them.
        if self.action in ("join", "end", "heartbeat", "leave", "attendance", "start_recording", "stop_recording"):
            return [IsAuthenticated()]
        return super().get_permissions()

    @transaction.atomic
    def perform_create(self, serializer):
        live_session = serializer.save()
        campus = live_session.section.school_class.campus
        # Best-effort: video-room provisioning (core/message/LiveKit) being
        # down must not block scheduling — `join` already returns a clean
        # 503 for a session with no room_id.
        is_offline = live_session.mode == ClassMode.OFFLINE
        room_id = None
        if not is_offline:  # T4 §G — an offline class has no video room
            try:
                with transaction.atomic():
                    room_id = bridge.provision_video_room(live_session, actor=self.request.user)
            except Exception:
                logger.exception("Video room provisioning failed for live session %s.", live_session.pk)
                room_id = None
        if room_id:
            live_session.room_id = room_id
            live_session.save(update_fields=["room_id"])
        recipients = StudentEnrollment.objects.filter(
            section=live_session.section, status=StudentEnrollment.Status.ACTIVE
        ).values_list("student", flat=True)
        Notice.objects.create(
            campus=campus,
            section=live_session.section,
            session=getattr(live_session.section.school_class, "session", None),
            posted_by=self.request.user,
            title=f"Class scheduled: {live_session.subject.name}",
            # T4 §G — offline classes never leak their time to students.
            body=(
                f"An offline class for {live_session.subject.name} has been scheduled."
                if is_offline
                else f"A live session for {live_session.subject.name} is scheduled at {live_session.scheduled_at}."
            ),
        )
        bridge.notify(
            users=list(recipients),
            notif_type=NotifTypes.CAMPUS_SESSION_SCHEDULED,
            title="Class scheduled",
            body=(
                f"{live_session.subject.name}: offline class scheduled."
                if is_offline
                else f"{live_session.subject.name} scheduled at {live_session.scheduled_at}."
            ),
        )

    def perform_update(self, serializer):
        old = serializer.instance
        changed = (
            ("scheduled_at" in serializer.validated_data and serializer.validated_data["scheduled_at"] != old.scheduled_at)
            or ("mode" in serializer.validated_data and serializer.validated_data["mode"] != old.mode)
        )
        instance = serializer.save()
        if changed and instance.reminder_sent_at:
            # rescheduled / mode changed -> the 5-minute reminder is due again
            CampusLiveSession.objects.filter(pk=instance.pk).update(reminder_sent_at=None)

    # B-4 fix — see CampusLiveSessionJoinThrottle's docstring in
    # throttles.py for why `start` (not a separate join endpoint, which
    # this app doesn't have) is the one scoped here: it's what fires
    # the CAMPUS_SESSION_LIVE notification fan-out below.
    @action(detail=True, methods=["post"], throttle_classes=[CampusLiveSessionJoinThrottle])
    def start(self, request, pk=None):
        live_session = self.get_object()
        if live_session.status != CampusLiveSession.Status.SCHEDULED:
            return Response({"detail": "Only a scheduled session can be started."}, status=status.HTTP_400_BAD_REQUEST)
        # Live class: status -> LIVE (+ real `started_at`), room provisioning retry
        # (scheduling-time provisioning is best-effort) and the time-critical
        # push=True "class is live" notification all live in campus/live_class.py
        # so this action and the one-tap Go live behave identically.
        live_class.start_session(live_session, request.user)
        return Response(self.get_serializer(live_session).data)

    # TASK 14 — closes the gap `core.classroom_chat_bridge.provision_video_room()`'s
    # own docstring flags: that function only names the room at scheduling
    # time (`perform_create` above), it deliberately never mints a token.
    # This is the actual "join this live session" endpoint — mints a
    # fresh, per-participant LiveKit token via
    # `core.classroom_chat_bridge.generate_campus_session_token()`
    # (added this same pass, see that function's own docstring).
    #
    # Called directly, NOT through `campus.bridge` — unlike every other
    # cross-app call in this file (`bridge.provision_video_room`,
    # `bridge.create_section_group`, ...). `core.classroom_chat_bridge`'s
    # own module docstring flags why: `campus/bridge.py`'s current
    # contents weren't available in the TASK 14 pass that added
    # `generate_campus_session_token()`, so no wrapper for it could be
    # verified there either. If `campus/bridge.py` later grows a thin
    # `generate_session_token()` wrapper for consistency with the other
    # bridge calls in this file, swap this one call over to it — nothing
    # else here would need to change.
    #
    # Reuses `CampusLiveSessionJoinThrottle` (already imported above) —
    # its own docstring in throttles.py explains it was scoped to `start`
    # only because this app had no separate join endpoint yet; now that
    # one exists, it belongs here too, in addition to `start` (kept there
    # for the notification-fan-out reason that throttle's docstring
    # separately documents).
    @action(detail=True, methods=["post"], throttle_classes=[CampusLiveSessionJoinThrottle])
    def join(self, request, pk=None):
        """
        `POST /campus-live-sessions/{id}/join/` -> `{"room_name": ..., "token": ...}`.

        Only once the session is actually `LIVE` (a student hitting this
        before the teacher's `start` call has nothing to join — LiveKit
        would accept the token but the room itself may not exist yet on
        the media server, since `provision_video_room()` only ever names
        it, never creates it server-side).

        Authorization (checked here, not by a permission class — see
        `get_permissions()` above): the requesting user must be either
        an ACTIVE `StudentEnrollment` in this session's section, or able
        to manage that section/subject (`can_manage_section_subject` —
        assigned subject-teacher, class-teacher, or campus admin/
        principal, the same staff check `IsSectionSubjectStaffOrReadOnly`
        itself is built on, per this file's other usages of that
        helper). Minting a token is not itself that check —
        `generate_campus_session_token()`'s own docstring is explicit
        that the caller owns this, so it happens here.
        """
        live_session = self.get_object()

        if live_session.mode == ClassMode.OFFLINE:
            return Response({"detail": "This is an offline class — there is no video room."}, status=status.HTTP_400_BAD_REQUEST)
        if live_session.status != CampusLiveSession.Status.LIVE:
            return Response(
                {"detail": "This session isn't live yet."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not live_session.room_id:
            # Shouldn't normally happen (perform_create provisions it),
            # but provisioning is itself best-effort (see
            # provision_video_room()/bridge's own handling) — surface a
            # clean 503 rather than minting a token for a room that was
            # never named.
            return Response(
                {"detail": "This session has no video room provisioned."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        section = live_session.section
        campus_id = section.school_class.campus_id
        is_enrolled_student = StudentEnrollment.objects.filter(
            section=section,
            student=request.user,
            status=StudentEnrollment.Status.ACTIVE,
        ).exists()
        if not is_enrolled_student and not live_class.can_run_session(request.user, live_session):
            raise PermissionDenied("You aren't enrolled in this section or assigned to teach it.")

        # Local import — campus never imports `core`/`message` directly
        # except through a single door; see this action's own docstring
        # above for why that door is `core.classroom_chat_bridge`
        # directly here rather than `campus.bridge`.
        from core.classroom_chat_bridge import generate_campus_session_token

        try:
            token = generate_campus_session_token(live_session.room_id, request.user)
        except RuntimeError:
            logger.exception(
                "LiveKit not configured — cannot mint join token for session %s", live_session.pk,
            )
            return Response(
                {"detail": "Live video isn't configured on this deployment."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Live class: this is the moment the student is "in the room" — the raw
        # data the automatic attendance is computed from.
        live_class.record_join(live_session, request.user)
        return Response(
            {"room_name": live_session.room_id, "token": token, "livekit_url": live_class.livekit_url()}
        )

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        live_session = self.get_object()
        if not live_class.can_run_session(request.user, live_session):
            raise PermissionDenied("Only this class's teacher (or a manager of the section) can end it.")
        # -> ENDED, open stays closed, subject-wise attendance built (source=auto).
        live_session, _changed = live_class.end_session(live_session)
        return Response(self.get_serializer(live_session).data)

    @action(detail=True, methods=["post"])
    def heartbeat(self, request, pk=None):
        """`POST /live-sessions/{id}/heartbeat/` — the app pings this every ~20-30 s
        while it is inside the room (students AND the teacher). Keeps the stay's
        `last_seen_at` fresh; the teacher's ping also keeps the session alive (a
        silent teacher app gets the class auto-ended by the sweeper). Always 200:
        `{"live": false, "status": "ended"}` tells a student's app to leave."""
        live_session = self.get_object()
        if not self._may_be_in_room(request.user, live_session):
            raise PermissionDenied("You aren't enrolled in this section or assigned to teach it.")
        return Response(live_class.heartbeat(live_session, request.user))

    @action(detail=True, methods=["post"])
    def leave(self, request, pk=None):
        """`POST /live-sessions/{id}/leave/` — the user left the room (idempotent)."""
        live_session = self.get_object()
        live_class.record_leave(live_session, request.user)
        return Response({"left": True})

    @action(detail=True, methods=["get"])
    def attendance(self, request, pk=None):
        """`GET /live-sessions/{id}/attendance/` — who came: every active student of
        the section with status / minutes / source (`auto` or `manual`) / join times,
        plus counts. Teacher / manager of the class only."""
        live_session = self.get_object()
        if not live_class.can_run_session(request.user, live_session):
            raise PermissionDenied("Only this class's teacher (or a manager of the section) can see this.")
        return Response(live_class.attendance_report(live_session))

    # ---- recorded classes (rules: campus/recordings.py) -----------------------------
    @action(detail=True, methods=["post"], url_path="start-recording", throttle_classes=[CampusRecordingControlThrottle])
    def start_recording(self, request, pk=None):
        """`POST /live-sessions/{id}/start-recording/` — the teacher's manual Record button
        (the automatic start happens on the teacher's first heartbeat when the campus
        turned `class_recording_auto` on). Refusals come back as
        `{"detail": ..., "code": ...}` with the code of `recordings.RecordingError`
        (`recording_off`, `not_live`, `already_recording`, `already_recorded`, ...)."""
        live_session = self.get_object()
        if not recordings.can_manage_recording(request.user, live_session):
            raise PermissionDenied("Only this class's teacher (or a manager of the section) can record it.")
        try:
            live_session = recordings.start_recording(live_session, request.user)
        except recordings.RecordingError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)
        return Response(self.get_serializer(live_session).data)

    @action(detail=True, methods=["post"], url_path="stop-recording", throttle_classes=[CampusRecordingControlThrottle])
    def stop_recording(self, request, pk=None):
        """`POST /live-sessions/{id}/stop-recording/` — stop the film but keep the class
        running (ending the class stops it too). 409 `not_recording` when none is on."""
        live_session = self.get_object()
        if not recordings.can_manage_recording(request.user, live_session):
            raise PermissionDenied("Only this class's teacher (or a manager of the section) can stop the recording.")
        try:
            live_session, _changed = recordings.stop_recording(live_session, request.user, strict=True)
        except recordings.RecordingError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)
        return Response(self.get_serializer(live_session).data)

    @staticmethod
    def _may_be_in_room(user, live_session):
        return StudentEnrollment.objects.filter(
            section=live_session.section, student=user, status=StudentEnrollment.Status.ACTIVE
        ).exists() or live_class.can_run_session(user, live_session)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        live_session = self.get_object()
        live_session.status = CampusLiveSession.Status.CANCELLED
        live_session.save(update_fields=["status"])
        return Response(self.get_serializer(live_session).data)
