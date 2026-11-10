"""
tuitionclass/recording_views.py  (X12 - auto recording)

Endpoints (all under /tuitionclass/). "viewer" = the classroom's teacher / staff, or anyone who EVER held a pass (same tier
as the recordings list: ClassroomViewSet.recordings). "manager" = teacher / co-teacher / moderator / org staff
(views.access._can_manage_classroom). A banned student is not a viewer. Errors use the app envelope {detail, code}.

  CHAPTERS     (markers on the recording timeline; offset_seconds = seconds from the start of the video)
    GET    sessions/<id>/chapters/                         viewer   {has_recording, duration_seconds, chapters:[...]}
    POST   sessions/<id>/chapters/                         manager  {title, offset_seconds?}  (no offset = "now" while recording)
    PATCH  sessions/<id>/chapters/<chapter_id>/            manager  {title?, offset_seconds?}
    DELETE sessions/<id>/chapters/<chapter_id>/            manager  (the "Class started" marker cannot be deleted)

  REPLAY CHAT  (what was said in the live chat, with its place on the video timeline)
    GET    sessions/<id>/replay-chat/?from=<s>&to=<s>&after_id=<id>&limit=<n>     viewer

  VIEWERS      (who attended live / watched the replay)
    GET    sessions/<id>/viewers/?q=<name>&filter=live|replay|both&limit=&offset=  manager

  INSIGHTS     (peak viewers, watch time, completion, questions, polls)
    GET    sessions/<id>/insights/                         manager

  WATCH PING   (replay progress; feeds resume + the viewer list + insights; managers are not counted)
    GET    sessions/<id>/watch/                            viewer   own resume state
    POST   sessions/<id>/watch/                            viewer   {position, delta, duration?}

  NOTES        (a class material attached to the recording, optionally at a timestamp)
    GET    sessions/<id>/recording-notes/                  viewer
    POST   sessions/<id>/recording-notes/                  manager  {material, at_seconds?}   (same material again = move it)
    DELETE sessions/<id>/recording-notes/<note_id>/        manager

  SAVE AS LECTURE / REEL / POST   (job rows, see recording_save.py)
    GET    sessions/<id>/recording-saves/                  manager  jobs of this session
    POST   sessions/<id>/recording-saves/                  manager  {kind, title?, description?, start_seconds, end_seconds,
                                                                    visibility?, include_chapters?, series_id?}  -> 202
    GET    recording-saves/<id>/                           the job's creator or a manager of its class  (poll this)
    POST   recording-saves/<id>/retry/                     same, only for a failed job

Nothing here changes how a session is joined, recorded or ended.
"""
import logging

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework import status as http
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from . import recording_save as rs
from . import recording_tools as rt
from .models import (
    ChatMessage, ClassMaterial, ClassSession, RecordingNote, RecordingSave, RecordingWatch, SessionChapter,
)
from .serializers import ClassMaterialSerializer, UserMiniSerializer
from .views.access import _can_manage_classroom

logger = logging.getLogger(__name__)

REPLAY_CHAT_DEFAULT_LIMIT = 200
REPLAY_CHAT_MAX_LIMIT = 500
VIEWERS_DEFAULT_LIMIT = 100
VIEWERS_MAX_LIMIT = 300
MAX_NOTES_PER_SESSION = 30
INSIGHTS_CACHE_SECONDS = 30


# ----------------------------------------------------------------------------------------------------- helpers
class _WatchThrottle(UserRateThrottle):
    scope = "recording_watch"
    THROTTLE_RATES = {"recording_watch": "40/min"}


class _SaveThrottle(UserRateThrottle):
    scope = "recording_save"
    THROTTLE_RATES = {"recording_save": "30/hour"}


def _err(code, message, status=http.HTTP_400_BAD_REQUEST):
    return Response({"detail": message, "code": code}, status=status)


def _load(session_id):
    return get_object_or_404(ClassSession.objects.select_related("classroom"), pk=session_id)


def _is_manager(session, user):
    return _can_manage_classroom(session.classroom, user)


def _is_viewer(session, user):
    """Teacher / staff, or anyone who ever held a pass (lapsed passes may still watch recordings - same as the
    recordings list). A student banned from the class never."""
    classroom = session.classroom
    if _can_manage_classroom(classroom, user):
        return True
    if not classroom.is_enrolled(user):
        return False
    return not classroom.bans.filter(student_id=user.id).exists()


def _gate_viewer(session, user):
    """None when `user` may open this recording's extras, else the error Response. A student also needs a recording that is
    still there (a copyright take-down blanks `recording_url`); managers always pass."""
    if not _is_viewer(session, user):
        return _err("permission_denied", "You need access to this classroom to open its recordings.", http.HTTP_403_FORBIDDEN)
    if not session.recording_url and not _is_manager(session, user):
        return _err("no_recording", "This recording is not available.", http.HTTP_404_NOT_FOUND)
    return None


def _gate_manager(session, user):
    if not _is_manager(session, user):
        return _err("permission_denied", "Only the class teacher or staff can do this.", http.HTTP_403_FORBIDDEN)
    return None


def _int(value, default=None, lo=None, hi=None):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    if lo is not None and number < lo:
        number = lo
    if hi is not None and number > hi:
        number = hi
    return number


def _body(request):
    data = request.data
    return data if isinstance(data, dict) or hasattr(data, "get") else {}


def _duration(session):
    return rt.estimated_duration(session.recording_duration_seconds, session.recording_started_at, session.actual_end)


def _person(user, request):
    return UserMiniSerializer(user, context={"request": request}).data


def chapter_payload(ch):
    return {
        "id": ch.id, "kind": ch.kind, "title": ch.title, "offset_seconds": ch.offset_seconds,
        "ref_type": ch.ref_type or None, "ref_id": ch.ref_id,
    }


# ----------------------------------------------------------------------------------------------------- chapters
class SessionChaptersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = _load(session_id)
        denied = _gate_viewer(session, request.user)
        if denied:
            return denied
        rows = SessionChapter.objects.filter(session=session).order_by("offset_seconds", "id")[: rt.MAX_CHAPTERS_PER_SESSION]
        return Response({
            "session": session.id,
            "has_recording": bool(session.recording_url),
            "recording_started_at": session.recording_started_at.isoformat() if session.recording_started_at else None,
            "duration_seconds": _duration(session),
            "chapters": [chapter_payload(c) for c in rows],
        })

    def post(self, request, session_id):
        session = _load(session_id)
        denied = _gate_manager(session, request.user)
        if denied:
            return denied
        body = _body(request)
        title = rt.clean_title(body.get("title"))
        if not title:
            return _err("title_required", "Give the chapter a title.")
        if session.recording_started_at is None:
            return _err("no_timeline", "This class was never recorded, so it has no timeline.")
        raw_offset = body.get("offset_seconds", body.get("offset"))
        if raw_offset in (None, ""):
            # "mark this moment": only meaningful while the recording is running
            chapter = rt.add_chapter(session, SessionChapter.Kind.MANUAL, title, user=request.user)
            if chapter is None:
                return _err("not_recording", "Send offset_seconds, or add the marker while the recording is running.")
            return Response(chapter_payload(chapter), status=http.HTTP_201_CREATED)
        offset = _int(raw_offset, lo=-1)
        duration = _duration(session)
        if offset is None or offset < 0 or (duration and offset > duration + rt.DURATION_SLACK_SECONDS):
            return _err("invalid_offset", "That time is outside the recording.")
        with transaction.atomic():
            qs = SessionChapter.objects.select_for_update().filter(session=session)
            if qs.count() >= rt.MAX_CHAPTERS_PER_SESSION:
                return _err("too_many_chapters", "This recording already has the maximum number of chapters.")
            chapter = SessionChapter.objects.create(
                session=session, kind=SessionChapter.Kind.MANUAL, title=title, offset_seconds=offset, created_by=request.user,
            )
        return Response(chapter_payload(chapter), status=http.HTTP_201_CREATED)


class SessionChapterDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _chapter(self, session, chapter_id):
        return get_object_or_404(SessionChapter, pk=chapter_id, session=session)

    def patch(self, request, session_id, chapter_id):
        session = _load(session_id)
        denied = _gate_manager(session, request.user)
        if denied:
            return denied
        chapter = self._chapter(session, chapter_id)
        body = _body(request)
        update = []
        if "title" in body:
            title = rt.clean_title(body.get("title"))
            if not title:
                return _err("title_required", "A chapter needs a title.")
            chapter.title = title
            update.append("title")
        if "offset_seconds" in body:
            if chapter.kind == SessionChapter.Kind.START:
                return _err("start_fixed", "The first marker always stays at 0:00.")
            offset = _int(body.get("offset_seconds"), lo=-1)
            duration = _duration(session)
            if offset is None or offset < 0 or (duration and offset > duration + rt.DURATION_SLACK_SECONDS):
                return _err("invalid_offset", "That time is outside the recording.")
            chapter.offset_seconds = offset
            update.append("offset_seconds")
        if update:
            chapter.save(update_fields=update)
        return Response(chapter_payload(chapter))

    def delete(self, request, session_id, chapter_id):
        session = _load(session_id)
        denied = _gate_manager(session, request.user)
        if denied:
            return denied
        chapter = self._chapter(session, chapter_id)
        if chapter.kind == SessionChapter.Kind.START:
            return _err("start_fixed", "The first marker cannot be deleted.")
        chapter.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------------------------------- replay chat
class SessionReplayChatView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = _load(session_id)
        denied = _gate_viewer(session, request.user)
        if denied:
            return denied
        t0 = rt.timeline_start(session)
        if t0 is None:
            return Response({"session": session.id, "messages": [], "next_after_id": None, "has_more": False})
        manager = _is_manager(session, request.user)
        q = request.query_params
        start = _int(q.get("from"), 0, 0)
        end = _int(q.get("to"), None, 0)
        after_id = _int(q.get("after_id"), None, 0)
        limit = _int(q.get("limit"), REPLAY_CHAT_DEFAULT_LIMIT, 1, REPLAY_CHAT_MAX_LIMIT)

        from datetime import timedelta

        qs = ChatMessage.objects.filter(session=session, is_deleted=False, sent_at__gte=t0 + timedelta(seconds=start))
        if end is not None:
            qs = qs.filter(sent_at__lt=t0 + timedelta(seconds=end))
        if not manager:
            qs = qs.filter(is_flagged=False)    # a message that tripped the filter / was reported is not replayed to students
        if after_id:
            qs = qs.filter(id__gt=after_id)
        rows = list(qs.select_related("sender").order_by("sent_at", "id")[: limit + 1])
        has_more = len(rows) > limit
        rows = rows[:limit]
        teacher_id = session.classroom.teacher_id
        messages = [
            {
                "id": m.id,
                "offset_seconds": max(int((m.sent_at - t0).total_seconds()), 0),
                "message": m.message,
                "sender": {"id": m.sender_id, "username": m.sender.username,
                           "full_name": m.sender.get_full_name() or m.sender.username},
                "is_teacher": m.sender_id == teacher_id,
                "is_pinned": m.is_pinned,
                "reply_to": m.reply_to_id,
            }
            for m in rows
        ]
        return Response({
            "session": session.id, "from": start, "to": end,
            "messages": messages,
            "next_after_id": rows[-1].id if (rows and has_more) else None,
            "has_more": has_more,
        })


# ----------------------------------------------------------------------------------------------------- viewers
class SessionViewersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = _load(session_id)
        denied = _gate_manager(session, request.user)
        if denied:
            return denied
        q = request.query_params
        rows = rt.viewer_rows(session)
        needle = (q.get("q") or "").strip().lower()
        if needle:
            rows = [r for r in rows if needle in r["user"].username.lower() or needle in (r["user"].get_full_name() or "").lower()]
        which = (q.get("filter") or "both").strip().lower()
        if which == "live":
            rows = [r for r in rows if r["live_seconds"] > 0 or r["joined_at"] is not None]
        elif which == "replay":
            rows = [r for r in rows if r["replay_watched_seconds"] > 0 or r["replay_last_watched_at"] is not None]
        total = len(rows)
        limit = _int(q.get("limit"), VIEWERS_DEFAULT_LIMIT, 1, VIEWERS_MAX_LIMIT)
        offset = _int(q.get("offset"), 0, 0)
        page = rows[offset: offset + limit]

        def _dt(value):
            return value.isoformat() if value else None

        return Response({
            "session": session.id,
            "total": total,
            "live_count": sum(1 for r in rows if r["joined_at"] is not None),
            "replay_count": sum(1 for r in rows if r["replay_last_watched_at"] is not None),
            "limit": limit, "offset": offset,
            "viewers": [
                {
                    "user": _person(r["user"], request),
                    "is_trial": r["is_trial"],
                    "attendance_status": r["attendance_status"] or None,
                    "live_seconds": r["live_seconds"],
                    "joined_at": _dt(r["joined_at"]),
                    "replay_watched_seconds": r["replay_watched_seconds"],
                    "replay_completed": r["replay_completed"],
                    "replay_last_watched_at": _dt(r["replay_last_watched_at"]),
                }
                for r in page
            ],
        })


# ----------------------------------------------------------------------------------------------------- insights
class SessionInsightsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = _load(session_id)
        denied = _gate_manager(session, request.user)
        if denied:
            return denied
        key = f"tc:recording-insights:{session.pk}"
        data = None
        try:
            data = cache.get(key)
        except Exception:
            data = None
        if data is None:
            data = rt.build_insights(session)
            try:
                cache.set(key, data, INSIGHTS_CACHE_SECONDS)
            except Exception:
                logger.warning("insights cache write failed for session %s", session.pk, exc_info=True)
        return Response(data)


# ----------------------------------------------------------------------------------------------------- watch ping
def _watch_payload(row):
    if row is None:
        return {"position_seconds": 0, "watched_seconds": 0, "duration_seconds": None, "completed": False, "last_watched_at": None}
    return {
        "position_seconds": row.position_seconds, "watched_seconds": row.watched_seconds,
        "duration_seconds": row.duration_seconds, "completed": row.completed,
        "last_watched_at": row.last_watched_at.isoformat() if row.last_watched_at else None,
    }


class SessionWatchView(APIView):
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        return [_WatchThrottle()] if self.request.method == "POST" else []

    def get(self, request, session_id):
        session = _load(session_id)
        denied = _gate_viewer(session, request.user)
        if denied:
            return denied
        row = RecordingWatch.objects.filter(session=session, user=request.user).first()
        return Response(_watch_payload(row))

    def post(self, request, session_id):
        session = _load(session_id)
        denied = _gate_viewer(session, request.user)
        if denied:
            return denied
        if _is_manager(session, request.user):
            # the teacher re-watching their own class is not "a student watched it"
            return Response({"tracked": False, **_watch_payload(None)})
        body = _body(request)
        known = _duration(session)
        sent = _int(body.get("duration"), None, 1, rt.MAX_CLIP_SECONDS)
        duration = known or sent
        if known and sent and abs(sent - known) > max(30, known // 10):
            duration = known          # a wildly different client number is ignored
        position = _int(body.get("position"), 0, 0, (duration or rt.MAX_CLIP_SECONDS))
        now = timezone.now()
        try:
            with transaction.atomic():
                row, created = RecordingWatch.objects.select_for_update().get_or_create(
                    session=session, user=request.user, defaults={"last_watched_at": now},
                )
                delta = rt.clamp_watch_delta(body.get("delta"), None if created else row.last_watched_at, now, first=created)
                row.watched_seconds += delta
                row.position_seconds = position
                if duration:
                    row.duration_seconds = duration
                if not row.completed and rt.watch_completed(position, row.duration_seconds):
                    row.completed = True
                row.last_watched_at = now
                row.save(update_fields=["watched_seconds", "position_seconds", "duration_seconds", "completed", "last_watched_at"])
        except IntegrityError:        # two first pings raced: the other one created the row, this ping is simply dropped
            row = RecordingWatch.objects.filter(session=session, user=request.user).first()
        return Response({"tracked": True, **_watch_payload(row)})


# ----------------------------------------------------------------------------------------------------- notes
def _note_payload(note, request):
    return {
        "id": note.id, "at_seconds": note.at_seconds,
        "material": ClassMaterialSerializer(note.material, context={"request": request}).data,
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }


class SessionRecordingNotesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = _load(session_id)
        denied = _gate_viewer(session, request.user)
        if denied:
            return denied
        notes = RecordingNote.objects.filter(session=session).select_related("material", "material__uploaded_by")
        return Response({"session": session.id, "notes": [_note_payload(n, request) for n in notes]})

    def post(self, request, session_id):
        session = _load(session_id)
        denied = _gate_manager(session, request.user)
        if denied:
            return denied
        body = _body(request)
        material_id = _int(body.get("material"), None, 1)
        if material_id is None:
            return _err("material_required", "Choose a class material to attach.")
        material = ClassMaterial.objects.filter(pk=material_id, classroom_id=session.classroom_id).first()
        if material is None:
            return _err("material_not_found", "That material does not belong to this class.", http.HTTP_404_NOT_FOUND)
        at = None
        if body.get("at_seconds") not in (None, ""):
            at = _int(body.get("at_seconds"), None, 0)
            duration = _duration(session)
            if at is None or (duration and at > duration + rt.DURATION_SLACK_SECONDS):
                return _err("invalid_offset", "That time is outside the recording.")
        with transaction.atomic():
            existing = RecordingNote.objects.select_for_update().filter(session=session, material=material).first()
            if existing is not None:
                existing.at_seconds = at
                existing.save(update_fields=["at_seconds"])
                return Response(_note_payload(existing, request))
            if RecordingNote.objects.filter(session=session).count() >= MAX_NOTES_PER_SESSION:
                return _err("too_many_notes", f"At most {MAX_NOTES_PER_SESSION} materials can be attached to one recording.")
            note = RecordingNote.objects.create(session=session, material=material, at_seconds=at, attached_by=request.user)
        return Response(_note_payload(note, request), status=http.HTTP_201_CREATED)


class SessionRecordingNoteDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, session_id, note_id):
        session = _load(session_id)
        denied = _gate_manager(session, request.user)
        if denied:
            return denied
        get_object_or_404(RecordingNote, pk=note_id, session=session).delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------------------------------- save as ...
def _save_payload(save, request):
    data = rs.status_payload(save)
    try:
        data["status_url"] = request.build_absolute_uri(reverse("recording-save-detail", args=[save.pk]))
    except Exception:
        data["status_url"] = None
    return data


class SessionRecordingSavesView(APIView):
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        return [_SaveThrottle()] if self.request.method == "POST" else []

    def get(self, request, session_id):
        session = _load(session_id)
        denied = _gate_manager(session, request.user)
        if denied:
            return denied
        rows = RecordingSave.objects.filter(session=session).order_by("-created_at")[:50]
        return Response({
            "session": session.id,
            "recording_ready": bool(session.recording_url) and not session.egress_id,
            "duration_seconds": _duration(session),
            "max_reel_seconds": rt.MAX_REEL_SECONDS,
            "saves": [_save_payload(s, request) for s in rows],
        })

    def post(self, request, session_id):
        session = _load(session_id)
        denied = _gate_manager(session, request.user)
        if denied:
            return denied
        data = request.data.dict() if hasattr(request.data, "dict") else request.data
        try:
            save, created = rs.create_save_job(session, request.user, data)
        except rs.SaveError as exc:
            return _err(exc.code, exc.message, exc.http)
        if created:
            rs.enqueue(save)
        payload = _save_payload(save, request)
        payload["created"] = created
        return Response(payload, status=http.HTTP_202_ACCEPTED if created else http.HTTP_200_OK)


def _save_for(request, save_id):
    """(save, None) when the caller made the job or manages its class, else (None, error Response). 404 for a stranger."""
    save = RecordingSave.objects.select_related("session__classroom").filter(pk=save_id).first()
    if save is None:
        return None, _err("not_found", "Not found.", http.HTTP_404_NOT_FOUND)
    if save.created_by_id != request.user.id and not _can_manage_classroom(save.session.classroom, request.user):
        return None, _err("not_found", "Not found.", http.HTTP_404_NOT_FOUND)
    return save, None


class RecordingSaveDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, save_id):
        save, resp = _save_for(request, save_id)
        if resp is not None:
            return resp
        return Response(_save_payload(save, request))


class RecordingSaveRetryView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [_SaveThrottle]

    def post(self, request, save_id):
        save, resp = _save_for(request, save_id)
        if resp is not None:
            return resp
        # only the person who made it, still allowed to manage the class (a removed co-teacher cannot retry)
        if not _can_manage_classroom(save.session.classroom, request.user):
            return _err("permission_denied", "Only the class teacher or staff can do this.", http.HTTP_403_FORBIDDEN)
        if save.status != RecordingSave.Status.FAILED:
            return _err("not_failed", "Only a failed clip can be retried.")
        session = save.session
        if not session.recording_url or session.egress_id:
            return _err("no_recording", "This class has no finished recording.")
        if save.visibility != RecordingSave.Visibility.CLASS_ONLY and request.user.id != session.classroom.teacher_id:
            return _err("teacher_only_audience", "Only the class teacher can publish a recording outside the class.", http.HTTP_403_FORBIDDEN)
        if rs.active_jobs(user=request.user).count() >= rs.MAX_ACTIVE_JOBS_PER_USER:
            return _err("too_many_jobs", "You already have clips being processed. Wait for them to finish.", http.HTTP_429_TOO_MANY_REQUESTS)
        claimed = RecordingSave.objects.filter(pk=save.pk, status=RecordingSave.Status.FAILED).update(
            status=RecordingSave.Status.PENDING, error="", finished_at=None, created_at=timezone.now(),
        )
        if not claimed:
            return _err("not_failed", "Only a failed clip can be retried.")
        save.refresh_from_db()
        rs.enqueue(save)
        return Response(_save_payload(save, request), status=http.HTTP_202_ACCEPTED)
