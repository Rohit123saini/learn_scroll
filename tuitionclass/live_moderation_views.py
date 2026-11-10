"""
tuitionclass/live_moderation_views.py  -  X12 LIVE MODERATION ENDPOINTS

Plain APIViews (same style as recording_views.py), all under /tuitionclass/. The router's `sessions/<pk>/<action>/` routes
never collide with these (`moderation` and `recording-notice` are not ClassSessionViewSet actions). The rules and the
actions themselves live in live_moderation.py / live_abuse.py / live_safety.py / recording_consent.py - nothing here
decides anything, it only checks who is asking, calls those, and shapes the answer. Errors use the app envelope
{detail, code}.

  REPORT        (a participant tells the moderators about a person, one chat line, or the class)
    POST sessions/<id>/moderation/report/                 room access   {user_id? | chat_message_id?, reason, note?}
                                                          -> 201 {created: true} / 200 {created: false} (same open report again)
                                                          neither user_id nor chat_message_id = the class itself
  QUEUE         (the moderators' to-do list; never shows who reported)
    GET  sessions/<id>/moderation/queue/?resolved=0|1&limit=&offset=       moderator   {count, open_count, results:[...]}
    POST sessions/<id>/moderation/queue/<event_id>/resolve/                moderator   {resolution: dismissed|actioned}
  ACT           (warn / chat_mute / chat_unmute / kick / ban)
    POST sessions/<id>/moderation/act/                    moderator     {action, user_id, minutes?, note?, report_id?}
  CHAT FILTER   (word filter mode of THIS session; "" = follow the platform default)
    GET  sessions/<id>/moderation/chat-filter/            room access   {mode, effective, modes}
    POST sessions/<id>/moderation/chat-filter/            moderator     {mode: ""|off|flag|mask|block}
  AUDIT LOG     (every action, system action and report intake - reporters are never named)
    GET  sessions/<id>/moderation/audit/?kind=&target=&limit=&offset=      moderator   {count, results:[...]}
  RECORDING NOTICE
    GET  sessions/<id>/recording-notice/                  room access   the notice payload (same as in the join reply)
    POST sessions/<id>/recording-notice/ack/              room access   acknowledge -> the fresh payload

"moderator" = teacher / co-teacher / moderator / org staff of the classroom, or platform staff (live_moderation.
ensure_moderator). Platform staff additionally see the reports about the classroom's own teacher / staff and about the
class itself; the classroom's staff never read reports about themselves.
"""
import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import live_moderation, live_push, live_safety, recording_consent
from .models import ChatMessage, ClassSession, LiveModerationEvent
from .realtime import broadcast_to_session
from .views.access import _has_room_access

logger = logging.getLogger(__name__)

QUEUE_DEFAULT_LIMIT = 50
QUEUE_MAX_LIMIT = 200
AUDIT_DEFAULT_LIMIT = 100
AUDIT_MAX_LIMIT = 300


# ----------------------------------------------------------------------------------------------------- helpers
def _session_or_404(session_id):
    return get_object_or_404(ClassSession.objects.select_related("classroom"), pk=session_id)


def _room_session(request, session_id):
    """The session, after checking the caller may be inside its room (teacher / staff / valid pass / running trial)."""
    session = _session_or_404(session_id)
    if not _has_room_access(session.classroom, request.user):
        raise PermissionDenied("You need to be part of this class to do this.")
    return session


def _moderator_session(request, session_id):
    session = _session_or_404(session_id)
    live_moderation.ensure_moderator(session, request.user)
    return session


def _page(request, default, maximum):
    def _int(name, fallback, low, high):
        raw = request.query_params.get(name)
        if raw in (None, ""):
            return fallback
        try:
            return max(low, min(int(raw), high))
        except (TypeError, ValueError):
            raise ValidationError({name: "Must be a whole number."})

    return _int("limit", default, 1, maximum), _int("offset", 0, 0, 1_000_000)


class _LiveView(APIView):
    permission_classes = [IsAuthenticated]


# ----------------------------------------------------------------------------------------------------- report
class SessionReportView(_LiveView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "live_report"

    def post(self, request, session_id):
        session = _room_session(request, session_id)
        data = request.data

        target_user = None
        chat_message = None
        if data.get("chat_message_id") not in (None, ""):
            try:
                chat_message = ChatMessage.objects.select_related("sender").filter(pk=int(data["chat_message_id"])).first()
            except (TypeError, ValueError):
                raise ValidationError({"chat_message_id": "Must be a message id."})
            if chat_message is None or chat_message.session_id != session.id:
                raise NotFound("That message was not found in this class.")
        elif data.get("user_id") not in (None, ""):
            from django.contrib.auth import get_user_model

            try:
                target_user = get_user_model().objects.filter(pk=int(data["user_id"])).first()
            except (TypeError, ValueError):
                raise ValidationError({"user_id": "Must be a user id."})
            if target_user is None:
                raise NotFound("That person was not found.")

        event, created = live_moderation.file_report(
            session, request.user, target_user=target_user, chat_message=chat_message,
            reason=data.get("reason") or "other", note=data.get("note") or "",
        )
        # Deliberately small: the reporter gets a thank-you and nothing about what happens next (no queue position, no
        # outcome, no name of whoever handles it).
        return Response(
            {"detail": "Thanks. A moderator will look at this.", "created": created},
            status=http.HTTP_201_CREATED if created else http.HTTP_200_OK,
        )


# ----------------------------------------------------------------------------------------------------- queue
class SessionModerationQueueView(_LiveView):
    def get(self, request, session_id):
        session = _moderator_session(request, session_id)
        resolved = str(request.query_params.get("resolved") or "0").lower() in ("1", "true", "yes")
        limit, offset = _page(request, QUEUE_DEFAULT_LIMIT, QUEUE_MAX_LIMIT)
        qs = live_moderation.moderation_queue(session, request.user, resolved=resolved)
        total = qs.count()
        open_count = total if not resolved else live_moderation.moderation_queue(
            session, request.user, resolved=False).count()
        rows = [live_moderation.queue_item(e) for e in qs[offset:offset + limit]]
        return Response({"count": total, "open_count": open_count, "results": rows})


class SessionModerationResolveView(_LiveView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "live_mod_action"

    def post(self, request, session_id, event_id):
        session = _moderator_session(request, session_id)
        # Looked up THROUGH the queue so a classroom moderator cannot reach a report they are not allowed to see
        # (about the teacher / staff / the class itself) by guessing an id.
        event = live_moderation.moderation_queue(session, request.user, resolved=False).filter(pk=event_id).first()
        if event is None:
            raise NotFound("That report was not found, or it is already closed.")
        live_moderation.resolve_report(event, request.user, str(request.data.get("resolution") or "").strip().lower(),
                                       detail=str(request.data.get("note") or "")[:255])
        return Response({"detail": "Report closed.", "id": event.id, "resolution": event.resolution})


# ----------------------------------------------------------------------------------------------------- act
class SessionModerationActView(_LiveView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "live_mod_action"

    def post(self, request, session_id):
        session = _session_or_404(session_id)
        data = request.data
        action = str(data.get("action") or "").strip().lower()
        if data.get("user_id") in (None, ""):
            raise ValidationError({"user_id": "This field is required."})
        report_id = data.get("report_id")
        if report_id in (None, ""):
            report_id = None
        else:
            try:
                report_id = int(report_id)
            except (TypeError, ValueError):
                raise ValidationError({"report_id": "Must be a report id."})
        out = live_moderation.moderate(
            session, request.user, action, data.get("user_id"),
            minutes=data.get("minutes"), note=data.get("note") or "", report_id=report_id,
        )
        return Response({"detail": "Done.", **out})


# ----------------------------------------------------------------------------------------------------- chat filter
class SessionChatFilterView(_LiveView):
    def _payload(self, session):
        return {
            "mode": session.chat_filter_mode or "",
            "effective": live_safety.effective_filter_mode(session),
            "modes": list(live_safety.FILTER_MODES),
        }

    def get(self, request, session_id):
        return Response(self._payload(_room_session(request, session_id)))

    def post(self, request, session_id):
        session = _moderator_session(request, session_id)
        mode = str(request.data.get("mode") if request.data.get("mode") is not None else "").strip().lower()
        if mode and mode not in live_safety.FILTER_MODES:
            raise ValidationError({"mode": f"Must be empty (platform default) or one of {list(live_safety.FILTER_MODES)}."})
        if (session.chat_filter_mode or "") != mode:
            session.chat_filter_mode = mode
            session.save(update_fields=["chat_filter_mode"])
            broadcast_to_session(session.id, "chat.filter_mode", {"mode": mode,
                                                                  "effective": live_safety.effective_filter_mode(session)})
        return Response({"detail": "Chat filter updated.", **self._payload(session)})

    def get_throttles(self):
        # Only the write is a moderator action worth its own budget; the read is a cheap room-access lookup.
        if self.request.method == "POST":
            self.throttle_scope = "live_mod_action"
            return [ScopedRateThrottle()]
        return super().get_throttles()


# ----------------------------------------------------------------------------------------------------- audit log
class SessionModerationAuditView(_LiveView):
    def get(self, request, session_id):
        session = _moderator_session(request, session_id)
        limit, offset = _page(request, AUDIT_DEFAULT_LIMIT, AUDIT_MAX_LIMIT)
        qs = LiveModerationEvent.objects.filter(session=session).select_related("actor", "target_user")
        if not request.user.is_staff:
            # The classroom's own staff never read reports about themselves or about the class (platform queue only).
            host_ids = set(live_push.moderator_ids(session)) | {request.user.id}
            qs = qs.exclude(
                Q(kind__in=("report", "dismiss"))
                & (Q(target_user__isnull=True) | Q(target_user_id__in=host_ids))
            )
        kind = str(request.query_params.get("kind") or "").strip().lower()
        if kind:
            if kind not in LiveModerationEvent.Kind.values:
                raise ValidationError({"kind": f"Must be one of {list(LiveModerationEvent.Kind.values)}."})
            qs = qs.filter(kind=kind)
        target = request.query_params.get("target")
        if target not in (None, ""):
            try:
                qs = qs.filter(target_user_id=int(target))
            except (TypeError, ValueError):
                raise ValidationError({"target": "Must be a user id."})
        total = qs.count()
        rows = [live_moderation.audit_item(e) for e in qs.order_by("-created_at", "-id")[offset:offset + limit]]
        return Response({"count": total, "results": rows})


# ----------------------------------------------------------------------------------------------------- recording notice
class SessionRecordingNoticeView(_LiveView):
    def get(self, request, session_id):
        session = _room_session(request, session_id)
        return Response(recording_consent.notice_for(session, request.user))


class SessionRecordingNoticeAckView(_LiveView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "live_notice_ack"

    def post(self, request, session_id):
        session = _room_session(request, session_id)
        return Response(recording_consent.acknowledge(session, request.user))
