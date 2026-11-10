# campus/results_push.py
"""
Push for "result published": ONE push per student (not per subject) —
publishing a 6-subject sheet must not buzz a parent six times.

Audience per student: the student, their real linked parents (normal bell +
push pipeline, honours NotificationPreference) and loginless Parent Mode
devices (``bridge.push_to_parent_devices``), exactly like homework_diary_push.

Runs inside a Celery worker (``tasks.send_results_published``), never in the
request path. Everything cross-app goes through ``campus.bridge``.
"""
import logging
from collections import OrderedDict
from decimal import Decimal

from . import bridge
from .bridge import NotifTypes
from .models import CampusParentLink, ResultEntry

logger = logging.getLogger(__name__)

_SHADOW_PARENT_PREFIX = "parent_shadow_"


def _fmt(n):
    return format(Decimal(str(n)).normalize(), "f")


def notify_results_published(entry_ids) -> int:
    """Returns number of students notified."""
    entries = list(
        ResultEntry.objects.filter(pk__in=list(entry_ids), status=ResultEntry.Status.PUBLISHED)
        .select_related("enrollment__student", "enrollment__section__school_class", "subject", "exam_term")
        .order_by("subject__name")
    )
    if not entries:
        return 0

    by_student = OrderedDict()
    for e in entries:
        by_student.setdefault(e.enrollment.student_id, []).append(e)

    campus_id = entries[0].enrollment.section.school_class.campus_id
    parents_of = {}
    for sid, pid, uname in CampusParentLink.objects.filter(
        campus_id=campus_id, student_id__in=list(by_student)
    ).values_list("student_id", "parent_id", "parent__username"):
        if (uname or "").startswith(_SHADOW_PARENT_PREFIX):
            continue
        parents_of.setdefault(sid, []).append(pid)

    sent = 0
    for sid, rows in by_student.items():
        term = rows[0].exam_term.name
        if len(rows) == 1:
            r = rows[0]
            title = f"Result published: {r.subject.name}"
            body = f"{term} · {_fmt(r.marks_obtained)}/{_fmt(r.max_marks)}"
        else:
            title = f"Results published: {term}"
            body = ", ".join(f"{r.subject.name} {_fmt(r.marks_obtained)}/{_fmt(r.max_marks)}" for r in rows[:4])
            if len(rows) > 4:
                body += f" +{len(rows) - 4} more"
        data = {
            "type": NotifTypes.RESULT_PUBLISHED,
            "enrollment_id": str(rows[0].enrollment_id),
            "student_id": str(rows[0].enrollment.student_id),  # a parent tap opens THIS child
            "exam_term_id": str(rows[0].exam_term_id),
            "campus_id": str(campus_id),
        }
        try:
            bridge.notify(
                users=[sid] + parents_of.get(sid, []),
                notif_type=NotifTypes.RESULT_PUBLISHED,
                title=title, body=body[:240], data=data, push=True,
            )
            bridge.push_to_parent_devices(student=rows[0].enrollment.student, title=title, body=body[:240], data=data)
            sent += 1
        except Exception:
            logger.exception("campus results push failed (student=%s).", sid)
    return sent
