# campus/parent_chat_views.py
"""
TEACHER <-> PARENT CHAT API  (rules live in `campus.parent_chat`; shapes in
`campus.parent_comms_api`)

    Threads  — /campus/parent-chat/threads/
      GET    threads/                    my chats (?campus= ?status=open|closed|all)
                                         admin/principal: ?flagged=1 -> reported chats of their campus
      POST   threads/open/               start (or reopen the existing) chat
                                         parent  -> {campus?, student, teacher}   (teacher = StaffProfile id)
                                         teacher -> {campus?, student, parent}    (parent  = User id)
      GET    threads/{id}/               one chat + whether I can send right now
      GET    threads/{id}/messages/      oldest-first page (?before=ISO  ?after=ISO  ?limit=)
      POST   threads/{id}/send/          {body}
      POST   threads/{id}/read/          mark everything read
      POST   threads/{id}/mute/          {muted: bool}
      POST   threads/{id}/close/         {reason?}        teacher, or admin/principal (flagged chats)
      POST   threads/{id}/reopen/                         same
      POST   threads/{id}/flag/          {reason?}        either side reports the chat
      POST   threads/{id}/resolve-flag/                   admin/principal ends the review

    Other
      GET    parent-chat/contacts/?campus=&as=parent|teacher&section=&q=   who can I start a chat with
      GET    parent-chat/unread/?campus=                                    badge numbers
      GET    PATCH parent-comms/settings/?campus=                           chat + PTM rules (PATCH: admin/principal)

Privacy posture: a chat is visible to its two participants ONLY. Admin/principal
see a chat only after one side REPORTED it (`flagged`), and every such read is
written to the campus audit log.
"""
from __future__ import annotations

import logging

from django.db import transaction
from rest_framework import status as http, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.async_utils import dispatch_after_commit

from . import parent_chat
from . import parent_comms as pc
from . import tasks as campus_tasks
from .audit import log_action
from .models import ParentChatThread, StaffProfile
from .parent_comms import CommsError
from .parent_comms_api import (
    CommsAPIError,
    api_error,
    campus_role,
    hhmm,
    message_json,
    my_campus_ids,
    parse_datetime_value,
    parse_time_value,
    require_role,
    resolve_campus,
    section_labels,
    settings_json,
    staff_json,
    student_or_404,
    thread_json,
    truthy,
    user_json,
    uuid_or_none,
)
from .permissions import get_staff_profile, is_campus_admin_or_principal
from .throttles import CampusParentChatSendThrottle

logger = logging.getLogger(__name__)

LIST_LIMIT = 200


# ============================================================
# helpers
# ============================================================

def _fail(exc: CommsError):
    raise CommsAPIError(exc)


def _thread_qs():
    return ParentChatThread.objects.select_related("student", "parent", "teacher__user", "campus")


def _load_thread(request, pk):
    """`(thread, side)` or a 404. `side` is `parent` | `teacher` | `admin` (admin
    only for a FLAGGED chat). Anyone else - and a deactivated teacher - gets a
    404, never a hint that the chat exists."""
    tid = uuid_or_none(pk)
    thread = _thread_qs().filter(pk=tid).first() if tid else None
    side = parent_chat.role_for(request.user, thread) if thread is not None else None
    if side == "teacher" and not thread.teacher.is_active:
        side = None
    if side is None:
        raise api_error("thread_not_found", "Chat not found.")
    return thread, side


def _unread_of(thread, side) -> int:
    if side not in ("parent", "teacher"):
        return 0
    row = parent_chat.annotate_unread(ParentChatThread.objects.filter(pk=thread.pk), side).first()
    return int(row.unread) if row is not None else 0


def _thread_payload(thread, side, *, section_label="") -> dict:
    block = parent_chat.send_block(thread, side) if side in ("parent", "teacher") else None
    data = thread_json(thread, side=side, block=block, unread=_unread_of(thread, side), exact=True)
    data["section_label"] = section_label or section_labels([thread.student_id], thread.campus_id).get(thread.student_id, "")
    data["rules"] = _rules_json(thread.campus_id)
    return data


def _rules_json(campus_id) -> dict:
    s = pc.get_settings(campus_id)
    return {
        "chat_start_time": hhmm(s.chat_start_time),
        "chat_end_time": hhmm(s.chat_end_time),
        "window_label": parent_chat.window_label(s),
        "max_unanswered": parent_chat.max_unanswered(s),
        "max_chars": parent_chat.max_chars(s),
    }


def _mini_thread(t) -> dict | None:
    if t is None:
        return None
    return {
        "id": str(t.pk),
        "status": t.status,
        "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
    }


def _resolve_campus_required(request, *, body=False) -> str:
    cid = resolve_campus(request, body=body)
    if cid is None:
        raise api_error("campus_required", "Pick a campus first.")
    return cid


# ============================================================
# Threads
# ============================================================
class ParentChatThreadViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        if getattr(self, "action", None) == "send":
            return [CampusParentChatSendThrottle()]
        return super().get_throttles()

    # ---- list ---------------------------------------------------------------
    def list(self, request):
        campus_id = resolve_campus(request)
        params = request.query_params
        want = (params.get("status") or "open").lower()
        rows = []  # (thread, side)

        if truthy(params.get("flagged", "")):
            campuses = [campus_id] if campus_id else sorted(my_campus_ids(request.user))
            for cid in campuses:
                if not is_campus_admin_or_principal(request.user, cid):
                    continue
                qs = _thread_qs().filter(campus_id=cid, flagged_at__isnull=False).order_by("-flagged_at")
                rows += [(t, "admin") for t in qs[:LIST_LIMIT]]
        else:
            as_teacher, as_parent = parent_chat.threads_for(request.user, campus_id)
            for qs, side in ((as_teacher, "teacher"), (as_parent, "parent")):
                if want in (ParentChatThread.Status.OPEN, ParentChatThread.Status.CLOSED):
                    qs = qs.filter(status=want)
                for t in parent_chat.annotate_unread(qs, side)[:LIST_LIMIT]:
                    rows.append((t, side))

        # newest conversation first; a chat with no message yet sorts by creation
        rows.sort(key=lambda r: r[0].last_message_at or r[0].created_at, reverse=True)
        rows = rows[:LIST_LIMIT]

        labels: dict = {}
        for cid in {t.campus_id for t, _ in rows}:
            labels.update(section_labels({t.student_id for t, _ in rows if t.campus_id == cid}, cid))
        results = []
        for t, side in rows:
            item = thread_json(t, side=side, unread=getattr(t, "unread", 0), exact=False)
            item["section_label"] = labels.get(t.student_id, "")
            results.append(item)
        return Response({"count": len(results), "results": results})

    def retrieve(self, request, pk=None):
        thread, side = _load_thread(request, pk)
        return Response(_thread_payload(thread, side))

    # ---- start a chat --------------------------------------------------------
    @action(detail=False, methods=["post"], url_path="open")
    def open_thread(self, request):
        d = request.data
        cid = _resolve_campus_required(request, body=True)
        student = student_or_404(d.get("student"))
        me = request.user
        if d.get("teacher"):
            # a parent writes to one of their child's teachers
            sid = uuid_or_none(d.get("teacher"))
            teacher = (
                StaffProfile.objects.filter(pk=sid, campus_id=cid, is_active=True).select_related("user").first()
                if sid else None
            )
            if teacher is None:
                raise api_error("invalid_teacher", "Teacher not found.")
            parent = me
        elif d.get("parent"):
            # a teacher writes to one of their students' parents
            teacher = get_staff_profile(me, cid)
            if teacher is None:
                raise api_error("not_allowed", "Only teachers can start a chat with a parent.")
            pid = uuid_or_none(d.get("parent"))
            parent = pc_user_or_404(pid)
        else:
            raise ValidationError({"teacher": "Say who you want to talk to."})
        try:
            thread, created = parent_chat.open_thread(
                campus_id=cid, student=student, parent=parent, teacher=teacher, actor=me,
            )
        except CommsError as exc:
            _fail(exc)
        thread = _thread_qs().get(pk=thread.pk)
        side = parent_chat.side_of(me, thread)
        return Response(
            _thread_payload(thread, side),
            status=http.HTTP_201_CREATED if created else http.HTTP_200_OK,
        )

    # ---- messages ------------------------------------------------------------
    @action(detail=True, methods=["get"], url_path="messages")
    def messages(self, request, pk=None):
        thread, side = _load_thread(request, pk)
        params = request.query_params
        before = parse_datetime_value(params.get("before"), "before")
        after = parse_datetime_value(params.get("after"), "after")
        try:
            limit = int(params.get("limit") or parent_chat.PAGE_DEFAULT)
        except (TypeError, ValueError):
            raise ValidationError({"limit": "Enter a number."})
        rows, more = parent_chat.messages_page(thread, before=before, after=after, limit=limit)
        if side == "admin" and before is None and after is None:
            log_action(
                thread.campus_id, request.user, "parentchat.admin_read", target=thread,
                summary="Admin opened a reported parent-teacher chat",
            )
        return Response(
            {
                "thread": _thread_payload(thread, side),
                "messages": [message_json(m, viewer_user_id=request.user.pk) for m in rows],
                "has_more": more,
            }
        )

    @action(detail=True, methods=["post"], url_path="send")
    def send(self, request, pk=None):
        thread, side = _load_thread(request, pk)
        if side not in ("parent", "teacher"):
            raise api_error("not_allowed", "You can't write in this chat.")
        try:
            with transaction.atomic():
                msg = parent_chat.send_message(thread, request.user, request.data.get("body"))
                dispatch_after_commit(campus_tasks.send_parent_chat_notification, str(msg.pk))
        except CommsError as exc:
            _fail(exc)
        thread = _thread_qs().get(pk=thread.pk)
        return Response(
            {
                "message": message_json(msg, viewer_user_id=request.user.pk),
                "thread": _thread_payload(thread, side),
            },
            status=http.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, pk=None):
        thread, side = _load_thread(request, pk)
        parent_chat.mark_read(thread, request.user)
        return Response({"ok": True, "unread": 0})

    @action(detail=True, methods=["post"], url_path="mute")
    def mute(self, request, pk=None):
        thread, side = _load_thread(request, pk)
        muted = truthy(request.data.get("muted", True))
        try:
            parent_chat.set_muted(thread, request.user, muted)
        except CommsError as exc:
            _fail(exc)
        return Response(_thread_payload(thread, side))

    # ---- control ----------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        thread, side = _load_thread(request, pk)
        try:
            changed = parent_chat.close_thread(thread, request.user, str(request.data.get("reason") or ""))
        except CommsError as exc:
            _fail(exc)
        if changed and side == "admin":
            log_action(thread.campus_id, request.user, "parentchat.close", target=thread, summary="Admin closed a reported chat")
        return Response(_thread_payload(thread, side))

    @action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        thread, side = _load_thread(request, pk)
        try:
            changed = parent_chat.reopen_thread(thread, request.user)
        except CommsError as exc:
            _fail(exc)
        if changed and side == "admin":
            log_action(thread.campus_id, request.user, "parentchat.reopen", target=thread, summary="Admin reopened a chat")
        return Response(_thread_payload(thread, side))

    @action(detail=True, methods=["post"], url_path="flag")
    def flag(self, request, pk=None):
        thread, side = _load_thread(request, pk)
        try:
            with transaction.atomic():
                newly = parent_chat.flag_thread(thread, request.user, str(request.data.get("reason") or ""))
                if newly:
                    log_action(
                        thread.campus_id, request.user, "parentchat.flag", target=thread,
                        summary=f"Chat reported by the {side}",
                    )
                    dispatch_after_commit(campus_tasks.send_parent_chat_flag_notification, str(thread.pk))
        except CommsError as exc:
            _fail(exc)
        thread = _thread_qs().get(pk=thread.pk)
        return Response(_thread_payload(thread, side))

    @action(detail=True, methods=["post"], url_path="resolve-flag")
    def resolve_flag(self, request, pk=None):
        thread, side = _load_thread(request, pk)
        try:
            changed = parent_chat.resolve_flag(thread, request.user)
        except CommsError as exc:
            _fail(exc)
        if changed:
            log_action(thread.campus_id, request.user, "parentchat.resolve_flag", target=thread, summary="Report resolved")
        # after resolving, an admin no longer has access: answer with a plain ack
        return Response({"ok": True, "resolved": bool(changed)})


def pc_user_or_404(user_id):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id).first() if user_id else None
    if user is None:
        raise api_error("parent_not_found", "Parent not found.")
    return user


# ============================================================
# contacts / unread
# ============================================================
class ParentChatContactsView(APIView):
    """Who can I start a chat with?  `as=parent` (default unless the caller is
    staff) -> my children and their teachers;  `as=teacher` -> my students and
    their parents."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        cid = _resolve_campus_required(request)
        params = request.query_params
        staff = get_staff_profile(request.user, cid)
        as_ = (params.get("as") or ("teacher" if staff else "parent")).lower()
        settings = pc.get_settings(cid)
        base = {"campus": str(cid), "chat_enabled": settings.chat_enabled, "as": as_}

        if as_ == "teacher":
            if staff is None:
                raise api_error("not_allowed", "Only teachers can start a chat with a parent.")
            rows = parent_chat.contacts_for_teacher(
                staff, cid, section_id=uuid_or_none(params.get("section")), query=params.get("q") or "",
            )
            out = [
                {
                    "student": user_json(r["student"]),
                    "section": {
                        "id": str(r["section"].pk),
                        "label": f"{r['section'].school_class.name} - {r['section'].name}",
                    },
                    "parents": [
                        {"user": user_json(p["user"]), "thread": _mini_thread(p["thread"])} for p in r["parents"]
                    ],
                }
                for r in rows
            ]
            return Response({**base, "students": out})

        if as_ != "parent":
            raise ValidationError({"as": "Use parent or teacher."})
        if campus_role(request.user, cid) is None:
            raise api_error("not_allowed", "You can't do this.")
        rows = parent_chat.contacts_for_parent(request.user, cid)
        labels = section_labels({r["student"].pk for r in rows}, cid)
        out = [
            {
                "student": user_json(r["student"]),
                "section_label": labels.get(r["student"].pk, ""),
                "teachers": [
                    {
                        "staff": staff_json(t["staff"]),
                        "class_teacher": t["class_teacher"],
                        "subjects": t["subjects"],
                        "thread": _mini_thread(t["thread"]),
                    }
                    for t in r["teachers"]
                ],
            }
            for r in rows
        ]
        return Response({**base, "children": out})


class ParentChatUnreadView(APIView):
    """Badge numbers: unread messages across my open chats (+ reported chats
    waiting for an admin)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        campus_id = resolve_campus(request)
        flagged = 0
        campuses = [campus_id] if campus_id else sorted(my_campus_ids(request.user))
        for cid in campuses:
            if is_campus_admin_or_principal(request.user, cid):
                flagged += ParentChatThread.objects.filter(campus_id=cid, flagged_at__isnull=False).count()
        return Response({"unread": parent_chat.total_unread(request.user, campus_id), "flagged": flagged})


# ============================================================
# settings (chat + PTM rules)
# ============================================================
_INT_FIELDS = {
    # field: (min, max)
    "chat_max_unanswered": (1, pc.MAX_UNANSWERED_CEILING),
    "chat_message_max_chars": (50, pc.MAX_CHAT_CHARS_CEILING),
    "ptm_booking_cutoff_minutes": (0, 7 * 24 * 60),
    "ptm_cancel_cutoff_minutes": (0, 7 * 24 * 60),
    "ptm_max_bookings_per_parent": (1, 30),
}


class ParentCommsSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cid = _resolve_campus_required(request)
        require_role(request.user, cid, "admin", "staff", "parent")
        return Response(settings_json(pc.get_settings(cid)))

    @transaction.atomic
    def patch(self, request):
        cid = _resolve_campus_required(request, body=True)
        require_role(request.user, cid, "admin")
        d = request.data
        s = pc.get_settings(cid)
        for key in ("chat_enabled", "ptm_enabled"):
            if key in d:
                setattr(s, key, truthy(d.get(key)))
        for key in ("chat_start_time", "chat_end_time"):
            if key in d:
                setattr(s, key, parse_time_value(d.get(key), key))
        for key, (lo, hi) in _INT_FIELDS.items():
            if key in d:
                try:
                    value = int(d.get(key))
                except (TypeError, ValueError):
                    raise ValidationError({key: "Enter a whole number."})
                if not lo <= value <= hi:
                    raise ValidationError({key: f"Use a number between {lo} and {hi}."})
                setattr(s, key, value)
        s.save()
        log_action(cid, request.user, "parentcomms.settings", target=s, summary="Parent-teacher settings changed")
        return Response(settings_json(s))
