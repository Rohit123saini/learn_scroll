# campus/parent_chat.py
"""
TEACHER <-> PARENT CHAT — the ONE place that owns the rules.

This is deliberately NOT a free-form DM. One thread = ONE linked parent x ONE
teacher x ONE child. Everything below is judged here, never in a view, so the
API, the push layer and the tests agree.

Rules (every number is tunable per campus in `ParentTeacherSettings`)
-------------------------------------------------------------------
  WHO
  * only a REAL linked parent (never a Parent-Mode shadow user) and a teacher who
    actually teaches that child (class teacher / approved subject teacher /
    moderator of the child's current section) can talk. Principal/admin are NOT
    silent participants: they can only read a thread that somebody FLAGGED
    (oversight, not surveillance) and can close it.
  * the thread stays readable forever but becomes read-only the moment either
    side stops being eligible (parent unlinked, child moved sections, teacher
    no longer teaches the child, campus switched chat off).

  WHEN
  * a TEACHER can only SEND inside the campus chat window (default 08:00-20:00).
    A PARENT can write any time; outside the window the message is stored but
    nobody is buzzed (`after_hours`) - the teacher gets one digest push when the
    window opens (`campus.tasks.send_parent_chat_morning_digest`).

  HOW MUCH
  * text only: no media, no links (kept on-platform and safe for minors' data);
  * at most `chat_message_max_chars` per message (hard ceiling 2000);
  * at most `chat_max_unanswered` messages in a row from one side without a reply
    from the other (default 3) - stops a 30-message pile-up.

  CONTROL
  * either side can mute (no push, bell row still arrives) and flag/report;
  * the teacher (or admin/principal) can close / reopen a thread; a parent
    cannot close the teacher's thread (they can mute or flag).

Pure ORM; no DRF, no Celery, no push (`campus.parent_chat_push` does that).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.db.models import DateTimeField, F, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models import Count
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import parent_comms as pc
from .models import ParentChatMessage, ParentChatThread, ParentTeacherSettings, StaffProfile
from .parent_comms import CommsError

logger = logging.getLogger(__name__)

PARENT = ParentChatMessage.Role.PARENT
TEACHER = ParentChatMessage.Role.TEACHER

PREVIEW_CHARS = 120
FLAG_REASON_MAX = 300
CLOSE_REASON_MAX = 200
PAGE_DEFAULT, PAGE_MAX = 40, 100

# http(s):// , www. , well-known shorteners / messengers, and bare domains on the
# common TLDs. Deliberately NOT a bare ".in" / ".co": "ok.in the evening" must pass.
_LINK_RE = re.compile(
    r"(https?://|www\.|\bt\.me/|\bwa\.me/|\bbit\.ly/|\b[a-z0-9][a-z0-9-]*\.(?:com|org|net|io|app|xyz|info|link)\b)",
    re.IGNORECASE,
)
_EPOCH = datetime(2000, 1, 1, tzinfo=dt_timezone.utc)


# --------------------------------------------------------------------------
# settings -> effective limits
# --------------------------------------------------------------------------

def max_chars(settings: ParentTeacherSettings) -> int:
    return max(1, min(int(settings.chat_message_max_chars or 1000), pc.MAX_CHAT_CHARS_CEILING))


def max_unanswered(settings: ParentTeacherSettings) -> int:
    return max(1, min(int(settings.chat_max_unanswered or 3), pc.MAX_UNANSWERED_CEILING))


def validate_body(body, settings: ParentTeacherSettings) -> str:
    """Trim + judge a message body. Returns the clean text."""
    text = (body or "")
    if not isinstance(text, str):
        raise CommsError("empty_message", "Write a message first.")
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise CommsError("empty_message", "Write a message first.")
    limit = max_chars(settings)
    if len(text) > limit:
        raise CommsError("message_too_long", f"Keep it under {limit} characters.")
    if _LINK_RE.search(text):
        raise CommsError("links_not_allowed", "Links aren't allowed in this chat. Please describe it in words.")
    return text


def window_label(settings: ParentTeacherSettings) -> str:
    return f"{settings.chat_start_time:%I:%M %p}-{settings.chat_end_time:%I:%M %p}".replace("-0", "-").lstrip("0")


# --------------------------------------------------------------------------
# who is who
# --------------------------------------------------------------------------

def side_of(user, thread):
    """`"parent"` / `"teacher"` for the two participants, else None."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if thread.parent_id == user.pk:
        return "parent"
    if thread.teacher.user_id == user.pk:
        return "teacher"
    return None


def is_overseer(user, thread) -> bool:
    from .permissions import is_campus_admin_or_principal

    return bool(user and is_campus_admin_or_principal(user, thread.campus_id))


def role_for(user, thread):
    """`parent` | `teacher` | `admin` (admin/principal - only for a FLAGGED
    thread) | None. None means the caller must get a 404."""
    side = side_of(user, thread)
    if side:
        return side
    if thread.flagged_at is not None and is_overseer(user, thread):
        return "admin"
    return None


# --------------------------------------------------------------------------
# eligibility
# --------------------------------------------------------------------------

def eligibility_problem(thread, settings=None):
    """`None` if both sides may still talk, else a `CommsError`-style tuple
    `(code, message)`. Cheap enough to call per message."""
    settings = settings or pc.get_settings(thread.campus_id)
    if not settings.chat_enabled:
        return "chat_disabled", "Parent-teacher chat is switched off for this campus."
    if not pc.is_real_linked_parent(thread.parent, thread.student_id, thread.campus_id):
        return "not_a_parent", "This parent is no longer linked to the student."
    if not pc.teaches_student(thread.teacher, thread.student_id, thread.campus_id):
        return "teacher_not_for_student", "This teacher no longer teaches the student."
    return None


def send_block(thread, role, settings=None, now=None):
    """Why `role` can't send right now: `(code, message)` or None. Used both to
    reject a send and to tell the app what to grey out."""
    settings = settings or pc.get_settings(thread.campus_id)
    if role not in ("parent", "teacher"):
        return "not_allowed", "You can't write in this chat."
    if thread.status == ParentChatThread.Status.CLOSED:
        return "thread_closed", "This chat was closed."
    problem = eligibility_problem(thread, settings)
    if problem:
        return problem
    if role == "teacher" and not pc.in_chat_window(settings, now):
        return "outside_chat_hours", f"Teachers can reply between {window_label(settings)}."
    if _unanswered_run(thread, role, settings) >= max_unanswered(settings):
        return (
            "too_many_unanswered",
            "You've sent a few messages already. Please wait for a reply before writing more.",
        )
    return None


def _unanswered_run(thread, role, settings) -> int:
    """How many messages in a row `role` has sent since the other side last spoke
    (capped at the limit, which is all the rule needs)."""
    cap = max_unanswered(settings)
    recent = list(thread.messages.order_by("-created_at").values_list("sender_role", flat=True)[:cap])
    run = 0
    for sender_role in recent:
        if sender_role != role:
            break
        run += 1
    return run


# --------------------------------------------------------------------------
# threads
# --------------------------------------------------------------------------

def open_thread(*, campus_id, student, parent, teacher: StaffProfile, actor):
    """Find or create the thread for (student, parent, teacher). Either the
    parent (towards one of their child's teachers) or the teacher (towards one
    of their students' parents) can start it. Returns `(thread, created)`."""
    settings = pc.get_settings(campus_id)
    if not settings.chat_enabled:
        raise CommsError("chat_disabled", "Parent-teacher chat is switched off for this campus.")
    if not pc.is_real_linked_parent(parent, student.pk, campus_id):
        raise CommsError("not_a_parent", "This parent isn't linked to this student.")
    if not pc.teaches_student(teacher, student.pk, campus_id):
        raise CommsError("teacher_not_for_student", "This teacher doesn't teach this student.")
    if actor.pk not in (parent.pk, teacher.user_id):
        raise CommsError("not_allowed", "You can't start this chat.")
    thread, created = ParentChatThread.objects.get_or_create(
        campus_id=campus_id,
        student=student,
        parent=parent,
        teacher=teacher,
        defaults={"created_by": actor},
    )
    return thread, created


def send_message(thread, user, body, *, now=None):
    """Judge + store one message. Returns the `ParentChatMessage`. Race-safe: the
    thread row is locked so two quick sends can't both slip past the
    unanswered-in-a-row limit."""
    now = now or timezone.now()
    settings = pc.get_settings(thread.campus_id)
    with transaction.atomic():
        locked = (
            ParentChatThread.objects.select_for_update()
            .select_related("parent", "teacher__user", "student")
            .get(pk=thread.pk)
        )
        role = side_of(user, locked)
        if role is None:
            raise CommsError("not_allowed", "You can't write in this chat.")
        block = send_block(locked, role, settings, timezone.localtime(now))
        if block:
            raise CommsError(*block)
        text = validate_body(body, settings)
        after_hours = role == "parent" and not pc.in_chat_window(settings, timezone.localtime(now))
        msg = ParentChatMessage.objects.create(
            thread=locked, sender=user, sender_role=role, body=text, after_hours=after_hours,
        )
        fields = {
            "last_message_at": msg.created_at,
            "last_message_preview": text.replace("\n", " ")[:PREVIEW_CHARS],
            "last_sender_role": role,
        }
        # writing is implicit "I've read everything so far" for the writer
        fields["parent_last_read_at" if role == "parent" else "teacher_last_read_at"] = msg.created_at
        for k, v in fields.items():
            setattr(locked, k, v)
        locked.save(update_fields=list(fields))
    thread.refresh_from_db()
    return msg


def messages_page(thread, *, before=None, after=None, limit=PAGE_DEFAULT):
    """Oldest-first page. `before` (datetime) pages back in time; `after`
    (datetime) polls for newer ones. Returns `(messages, has_more_older)`."""
    limit = max(1, min(int(limit or PAGE_DEFAULT), PAGE_MAX))
    qs = thread.messages.all()
    if after is not None:
        rows = list(qs.filter(created_at__gt=after).order_by("created_at")[:limit])
        return rows, False
    if before is not None:
        qs = qs.filter(created_at__lt=before)
    rows = list(qs.order_by("-created_at")[: limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    return rows, more


def mark_read(thread, user):
    side = side_of(user, thread)
    if side is None:
        return False
    field = "parent_last_read_at" if side == "parent" else "teacher_last_read_at"
    setattr(thread, field, timezone.now())
    thread.save(update_fields=[field])
    return True


def set_muted(thread, user, muted: bool):
    side = side_of(user, thread)
    if side is None:
        raise CommsError("not_allowed", "You can't change this chat.")
    field = "parent_muted" if side == "parent" else "teacher_muted"
    setattr(thread, field, bool(muted))
    thread.save(update_fields=[field])
    return thread


def close_thread(thread, user, reason="") -> bool:
    """Teacher of the thread, or admin/principal. Returns True if it changed."""
    allowed = side_of(user, thread) == "teacher" or is_overseer(user, thread)
    if not allowed:
        raise CommsError("not_allowed", "Only the teacher or the school can close this chat.")
    if thread.status == ParentChatThread.Status.CLOSED:
        return False
    thread.status = ParentChatThread.Status.CLOSED
    thread.closed_by = user
    thread.closed_at = timezone.now()
    thread.close_reason = (reason or "").strip()[:CLOSE_REASON_MAX]
    thread.save(update_fields=["status", "closed_by", "closed_at", "close_reason"])
    return True


def reopen_thread(thread, user) -> bool:
    allowed = side_of(user, thread) == "teacher" or is_overseer(user, thread)
    if not allowed:
        raise CommsError("not_allowed", "Only the teacher or the school can reopen this chat.")
    if thread.status == ParentChatThread.Status.OPEN:
        return False
    problem = eligibility_problem(thread)
    if problem:
        raise CommsError(*problem)
    thread.status = ParentChatThread.Status.OPEN
    thread.closed_by = None
    thread.closed_at = None
    thread.close_reason = ""
    thread.save(update_fields=["status", "closed_by", "closed_at", "close_reason"])
    return True


def flag_thread(thread, user, reason="") -> bool:
    """Either side reports the conversation; admin/principal can then read it.
    Returns True if it newly flagged (False = already flagged)."""
    if side_of(user, thread) is None:
        raise CommsError("not_allowed", "You can't report this chat.")
    if thread.flagged_at is not None:
        return False
    thread.flagged_at = timezone.now()
    thread.flagged_by = user
    thread.flag_reason = (reason or "").strip()[:FLAG_REASON_MAX]
    thread.save(update_fields=["flagged_at", "flagged_by", "flag_reason"])
    return True


def resolve_flag(thread, user) -> bool:
    """Admin/principal closes the review (the thread goes back to being private)."""
    if not is_overseer(user, thread):
        raise CommsError("not_allowed", "Only the school admin can resolve a report.")
    if thread.flagged_at is None:
        return False
    thread.flagged_at = None
    thread.flagged_by = None
    thread.flag_reason = ""
    thread.save(update_fields=["flagged_at", "flagged_by", "flag_reason"])
    return True


# --------------------------------------------------------------------------
# lists (unread counts in ONE query)
# --------------------------------------------------------------------------

def annotate_unread(qs, side):
    """Adds `.unread` (messages from the OTHER side after this side's last read)."""
    other = "teacher" if side == "parent" else "parent"
    read_field = "parent_last_read_at" if side == "parent" else "teacher_last_read_at"
    counts = (
        ParentChatMessage.objects.filter(
            thread=OuterRef("pk"), sender_role=other, created_at__gt=OuterRef("_read_marker")
        )
        .order_by()
        .values("thread")
        .annotate(c=Count("pk"))
        .values("c")
    )
    return qs.annotate(
        _read_marker=Coalesce(F(read_field), Value(_EPOCH, output_field=DateTimeField()))
    ).annotate(unread=Coalesce(Subquery(counts, output_field=IntegerField()), 0))


def threads_for(user, campus_id=None):
    """`(queryset, side)` of the caller's threads (their OWN, never others')."""
    base = ParentChatThread.objects.select_related("student", "parent", "teacher__user", "campus")
    if campus_id:
        base = base.filter(campus_id=campus_id)
    as_teacher = base.filter(teacher__user=user, teacher__is_active=True)
    as_parent = base.filter(parent=user)
    return as_teacher, as_parent


def total_unread(user, campus_id=None) -> int:
    total = 0
    as_teacher, as_parent = threads_for(user, campus_id)
    for qs, side in ((as_teacher, "teacher"), (as_parent, "parent")):
        total += sum(t.unread for t in annotate_unread(qs.filter(status=ParentChatThread.Status.OPEN), side))
    return total


# --------------------------------------------------------------------------
# who can I start a chat with?
# --------------------------------------------------------------------------

def contacts_for_parent(parent, campus_id):
    """`[{student, teachers:[row...]}]` - each child and the teachers they may
    message (class teacher first), with any existing thread id."""
    if not pc.get_settings(campus_id).chat_enabled:
        return []
    threads = {
        (t.student_id, t.teacher_id): t
        for t in ParentChatThread.objects.filter(campus_id=campus_id, parent=parent)
    }
    out = []
    for student, _sections in pc.children_of_parent(parent, campus_id):
        if pc.is_shadow_parent(parent):
            continue
        rows = []
        for row in pc.teachers_of_student(student.pk, campus_id):
            t = threads.get((student.pk, row["staff"].pk))
            rows.append({**row, "thread": t})
        out.append({"student": student, "teachers": rows})
    return out


def contacts_for_teacher(staff: StaffProfile, campus_id, *, section_id=None, query="", limit=200):
    """Students of the sections `staff` teaches, each with their real linked
    parents (and any existing thread). Bounded: a 60-student section x 2 parents
    is fine, a whole school is not."""
    from .models import CampusParentLink, StudentEnrollment

    if not pc.get_settings(campus_id).chat_enabled:
        return []
    sections = pc.taught_section_ids(staff.user, campus_id)
    if section_id:
        sections = {s for s in sections if str(s) == str(section_id)}
    if not sections:
        return []
    enr = StudentEnrollment.objects.filter(
        section_id__in=sections, status=StudentEnrollment.Status.ACTIVE
    ).select_related("student", "section__school_class")
    if pc.campus_has_current_session(campus_id):
        enr = enr.filter(session__is_current=True)
    q = (query or "").strip()
    if q:
        enr = enr.filter(
            Q(student__first_name__icontains=q) | Q(student__last_name__icontains=q) | Q(student__username__icontains=q)
        )
    enr = list(enr.order_by("section__school_class__name", "section__name", "roll_number")[:limit])
    student_ids = [e.student_id for e in enr]
    links = {}
    for link in CampusParentLink.objects.filter(campus_id=campus_id, student_id__in=student_ids).select_related("parent"):
        if pc.is_shadow_parent(link.parent):
            continue
        links.setdefault(link.student_id, []).append(link.parent)
    threads = {
        (t.student_id, t.parent_id): t
        for t in ParentChatThread.objects.filter(campus_id=campus_id, teacher=staff, student_id__in=student_ids)
    }
    out, seen = [], set()
    for e in enr:
        if e.student_id in seen:
            continue
        seen.add(e.student_id)
        parents = links.get(e.student_id, [])
        out.append(
            {
                "student": e.student,
                "section": e.section,
                "parents": [{"user": p, "thread": threads.get((e.student_id, p.pk))} for p in parents],
            }
        )
    return out
