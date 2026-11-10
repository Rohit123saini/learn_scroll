# campus/parent_chat_push.py
"""
Notifications for the teacher <-> parent chat.

  message       -> the OTHER side of the thread (bell row always; push unless
                   the recipient muted the thread, or the message was written
                   after hours - then it waits silently for the morning)
  morning digest-> each teacher with unread after-hours parent messages gets
                   ONE push when the campus chat window opens

The body carries a short preview of the text, never more: lock-screen previews
are visible to anyone holding the phone, so it is cut to PREVIEW_CHARS.
"""
from __future__ import annotations

import logging

from . import bridge, parent_comms as pc
from .bridge import NotifTypes
from .models import ParentChatMessage, ParentChatThread

logger = logging.getLogger(__name__)

PREVIEW_CHARS = 90


def _preview(text: str) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= PREVIEW_CHARS else flat[: PREVIEW_CHARS - 1].rstrip() + "…"


def _payload(thread, message=None):
    data = {
        "type": NotifTypes.CAMPUS_PARENT_MESSAGE,
        "thread_id": str(thread.pk),
        "campus_id": str(thread.campus_id),
        "student_id": str(thread.student_id),
    }
    if message is not None:
        data["message_id"] = str(message.pk)
    return data


def notify_parent_message(message) -> int:
    """Tell the other side. Returns 1 if a notification was attempted, else 0."""
    thread = message.thread
    child = pc.display_name(thread.student)
    if message.sender_role == ParentChatMessage.Role.PARENT:
        recipient = thread.teacher.user
        muted = thread.teacher_muted
        title = f"{pc.display_name(thread.parent)} (parent of {child})"
    else:
        recipient = thread.parent
        muted = thread.parent_muted
        title = f"{pc.display_name(thread.teacher.user)} · about {child}"
    push = not muted and not message.after_hours
    bridge.notify(
        users=[recipient],
        notif_type=NotifTypes.CAMPUS_PARENT_MESSAGE,
        title=title,
        body=_preview(message.body),
        data=_payload(thread, message),
        push=push,
    )
    return 1


def notify_morning_digest(campus_id) -> int:
    """One push per teacher who has unread AFTER-HOURS parent messages. Returns
    the number of teachers told."""
    waiting: dict = {}
    qs = ParentChatMessage.objects.filter(
        thread__campus_id=campus_id,
        thread__status=ParentChatThread.Status.OPEN,
        thread__teacher_muted=False,
        sender_role=ParentChatMessage.Role.PARENT,
        after_hours=True,
    ).select_related("thread__teacher__user")
    for m in qs.order_by("created_at"):
        read = m.thread.teacher_last_read_at
        if read is not None and read >= m.created_at:
            continue
        row = waiting.setdefault(m.thread.teacher.user_id, {"user": m.thread.teacher.user, "threads": set(), "n": 0})
        row["threads"].add(m.thread_id)
        row["n"] += 1
    for row in waiting.values():
        n, chats = row["n"], len(row["threads"])
        bridge.notify(
            users=[row["user"]],
            notif_type=NotifTypes.CAMPUS_PARENT_MESSAGE,
            title="Parent messages waiting",
            body=f"{n} message{'s' if n != 1 else ''} from {chats} parent{'s' if chats != 1 else ''} came in after hours.",
            data={"type": NotifTypes.CAMPUS_PARENT_MESSAGE, "kind": "digest", "campus_id": str(campus_id)},
            push=True,
        )
    return len(waiting)


def notify_thread_flagged(thread) -> int:
    """Tell the campus admin/principal that a chat was reported. Deliberately
    carries NO message text and no reason - the reviewer opens the flagged chat
    in the app (which is audit-logged). Returns the number of admins told."""
    from .models import StaffProfile

    admins = [
        s.user
        for s in StaffProfile.objects.filter(
            campus_id=thread.campus_id,
            is_active=True,
            role__in=(StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD),
        ).select_related("user")
    ]
    # the reporter doesn't need to be told about their own report
    admins = [u for u in admins if u.pk != thread.flagged_by_id]
    if not admins:
        return 0
    bridge.notify(
        users=admins,
        notif_type=NotifTypes.CAMPUS_PARENT_MESSAGE,
        title="Chat reported",
        body="A parent-teacher chat was reported and is waiting for your review.",
        data={
            "type": NotifTypes.CAMPUS_PARENT_MESSAGE,
            "kind": "flagged",
            "thread_id": str(thread.pk),
            "campus_id": str(thread.campus_id),
            "student_id": str(thread.student_id),
        },
        push=True,
    )
    return len(admins)
