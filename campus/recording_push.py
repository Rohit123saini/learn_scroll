# campus/recording_push.py
"""
Push for RECORDED CLASSES and CLASS NOTES:

    * "recording ready"  — a live class recording finished processing and can be watched.
    * "new note"         — a teacher posted a (published) note for the section.

Audience = exactly the Homework Diary / Class Resources audience
(`homework_diary_push.homework_audience`): every ACTIVE student of the section plus
their linked parents — real `User` parents through the normal bell + push pipeline
(NotificationPreference honoured) AND loginless Parent Mode devices through
`bridge.push_to_parent_devices`. The poster / the teacher is never notified about
their own post.

Runs in a Celery worker (`tasks.send_recording_ready` / `tasks.send_class_note_posted`),
never in the request path.

Anti-spam for notes: a teacher posting 10 notes in a row must not buzz 60 families 10
times. `is_note_burst()` lets the task stay quiet once the same poster already pushed
`CAMPUS_NOTE_PUSH_BURST_LIMIT` (default 3) notes into the same (section, subject) within
`CAMPUS_NOTE_PUSH_BURST_MINUTES` (default 10). The notes stay visible in the app.
A recording push is never throttled — it is one per class.

A recording hidden by the teacher is never pushed (students could not open it).
"""
import logging
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.utils import timezone

from . import bridge
from .bridge import NotifTypes
from .homework_diary_push import homework_audience
from .models import ClassNote

logger = logging.getLogger(__name__)


def _recipients(students, parents, exclude_user_id=None):
    recipients = {u.pk: u for u in students}
    recipients.update({p.pk: p for p in parents})
    if exclude_user_id:
        recipients.pop(exclude_user_id, None)
    return list(recipients.values())


def _send(*, students, parents, exclude_user_id, notif_type, title, body, data):
    sent = 0
    users = _recipients(students, parents, exclude_user_id)
    if users:
        bridge.notify(users=users, notif_type=notif_type, title=title, body=body, data=data, push=True)
        sent += len(users)
    for student in students:
        try:
            sent += bridge.push_to_parent_devices(student=student, title=title, body=body, data=data)
        except Exception:  # pragma: no cover - the helper already swallows
            logger.exception("campus recording/note push to parent devices failed (student=%s).", student.pk)
    return sent


# --------------------------------------------------------------------------
# recording ready
# --------------------------------------------------------------------------

def notify_recording_ready(live) -> int:
    """Recording READY -> section students + parents. Returns pushes attempted."""
    section = live.section
    campus_id = section.school_class.campus_id
    # `homework_audience` only needs `.section_id` + `.campus_id`
    students, parents = homework_audience(SimpleNamespace(section_id=live.section_id, campus_id=campus_id))
    exclude = live.teacher.user_id if live.teacher_id else None
    data = {
        "type": NotifTypes.CAMPUS_RECORDING_READY,
        "recording_id": str(live.pk),
        "live_session_id": str(live.pk),
        "section_id": str(live.section_id),
        "subject_id": str(live.subject_id),
        "campus_id": str(campus_id),
    }
    return _send(
        students=students, parents=parents, exclude_user_id=exclude,
        notif_type=NotifTypes.CAMPUS_RECORDING_READY,
        title=f"Class recording: {live.subject.name}",
        body="Today's class recording is ready to watch.",
        data=data,
    )


# --------------------------------------------------------------------------
# new note
# --------------------------------------------------------------------------

def is_note_burst(note) -> bool:
    """True if this poster already pushed enough notes into this (section, subject)
    recently. Call it BEFORE stamping `notified_at` on `note`."""
    limit = max(1, int(getattr(settings, "CAMPUS_NOTE_PUSH_BURST_LIMIT", 3)))
    minutes = max(1, int(getattr(settings, "CAMPUS_NOTE_PUSH_BURST_MINUTES", 10)))
    since = timezone.now() - timedelta(minutes=minutes)
    recent = ClassNote.objects.filter(
        section_id=note.section_id,
        subject_id=note.subject_id,
        posted_by_id=note.posted_by_id,
        notified_at__gte=since,
    ).exclude(pk=note.pk)
    return recent.count() >= limit


def notify_note_posted(note) -> int:
    """New published note -> section students + parents. Returns pushes attempted."""
    students, parents = homework_audience(note)  # needs .section_id + .campus_id
    data = {
        "type": NotifTypes.CAMPUS_NOTE_POSTED,
        "note_id": str(note.pk),
        "section_id": str(note.section_id),
        "subject_id": str(note.subject_id),
        "campus_id": str(note.campus_id),
        "live_session_id": str(note.live_session_id) if note.live_session_id else "",
    }
    return _send(
        students=students, parents=parents, exclude_user_id=note.posted_by_id,
        notif_type=NotifTypes.CAMPUS_NOTE_POSTED,
        title=f"New note: {note.subject.name}",
        body=(note.title or "")[:140],
        data=data,
    )
