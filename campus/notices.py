# campus/notices.py
"""
COMMUNICATION — who a notice reaches and how it goes out.

  * `recipients_for(notice)`  -> student / parent / staff user ids for the notice's scope + audience
  * `deliver(notice)`         -> push + bell rows through `bridge.notify` (best-effort, never raises)
  * `publish_due(now)`        -> the scheduler: publish every scheduled notice whose time has come
  * `publish_now(notice)`     -> "send now" for a scheduled notice

The rules (audience flags, schedule limits, emergency text) live in `notice_rules.py` so they can
be tested without Django. Everything here is ORM + `bridge.notify`.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import bridge, notice_rules as rules
from .bridge import NotifTypes
from .models import (
    ClassTeacherassigments,
    CampusParentLink,
    Notice,
    StaffProfile,
    StudentEnrollment,
    SubjectTeacherassigments,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ recipients
def _scope_enrollment_filter(notice: Notice) -> dict:
    """The same scope rule the notice list uses: section > class > department > whole campus."""
    if notice.section_id:
        return {"section_id": notice.section_id}
    if notice.school_class_id:
        return {"section__school_class_id": notice.school_class_id}
    if notice.department_id:
        return {"section__school_class__department_id": notice.department_id}
    return {}


def _student_ids(notice: Notice) -> set:
    return set(
        StudentEnrollment.objects.filter(
            status=StudentEnrollment.Status.ACTIVE,
            section__school_class__campus_id=notice.campus_id,
            **_scope_enrollment_filter(notice),
        ).values_list("student_id", flat=True).distinct()
    )


def _parent_ids(notice: Notice, student_ids: set) -> set:
    if not student_ids:
        return set()
    return set(
        CampusParentLink.objects.filter(campus_id=notice.campus_id, student_id__in=student_ids)
        .values_list("parent_id", flat=True).distinct()
    )


def _staff_ids(notice: Notice) -> set:
    """Active staff the notice is relevant to: management of the campus always; for a narrower
    scope the class teacher + approved subject teachers of the sections in it; for a department
    the staff whose home department it is."""
    staff = StaffProfile.objects.filter(campus_id=notice.campus_id, is_active=True)
    management = Q(role__in=[StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD])
    if not (notice.section_id or notice.school_class_id or notice.department_id):
        return set(staff.values_list("user_id", flat=True))

    ids = set(staff.filter(management).values_list("user_id", flat=True))
    if notice.department_id:
        ids |= set(staff.filter(department_id=notice.department_id).values_list("user_id", flat=True))
        section_q = {"section__school_class__department_id": notice.department_id}
    elif notice.school_class_id:
        section_q = {"section__school_class_id": notice.school_class_id}
    else:
        section_q = {"section_id": notice.section_id}
    ids |= set(
        ClassTeacherassigments.objects.filter(staff__in=staff, **section_q).values_list("staff__user_id", flat=True)
    )
    ids |= set(
        SubjectTeacherassigments.objects.filter(
            staff__in=staff, status=SubjectTeacherassigments.Status.APPROVED, **section_q
        ).values_list("staff__user_id", flat=True)
    )
    return ids


def recipients_for(notice: Notice) -> dict:
    """-> {"students": set, "parents": set, "staff": set} of user ids. The poster is never in it
    (nobody needs a push for what they just wrote)."""
    want_students, want_parents, want_staff = rules.audience_flags(notice.audience)
    students = _student_ids(notice) if (want_students or want_parents) else set()
    out = {
        "students": students if want_students else set(),
        "parents": _parent_ids(notice, students) if want_parents else set(),
        "staff": _staff_ids(notice) if want_staff else set(),
    }
    if notice.posted_by_id:
        for key in out:
            out[key].discard(notice.posted_by_id)
    return out


# ------------------------------------------------------------------ delivery
def deliver(notice: Notice) -> int:
    """Push + bell row to everyone `recipients_for` says. Returns how many users were addressed.
    Best-effort: a push problem must never fail the post / the scheduler, but it is logged."""
    try:
        groups = recipients_for(notice)
        everyone = sorted(set().union(*groups.values()))
        emergency = notice.kind == Notice.Kind.EMERGENCY
        notif_type = NotifTypes.CAMPUS_EMERGENCY if emergency else NotifTypes.NOTICE_POSTED
        data = {
            "type": notif_type,
            "notice_id": str(notice.id),
            "campus_id": str(notice.campus_id),
        }
        title = rules.push_title(notice.kind, notice.title)
        body = rules.push_body(notice.title, notice.body)
        for chunk in rules.chunked(everyone):
            bridge.notify(users=chunk, notif_type=notif_type, title=title, body=body, data=data, push=True)
        Notice.objects.filter(pk=notice.pk).update(recipient_count=len(everyone))
        notice.recipient_count = len(everyone)
        return len(everyone)
    except Exception:
        logger.exception("Campus notice delivery failed (notice=%s).", notice.pk)
        return 0


# ------------------------------------------------------------------ publish / schedule
def publish_now(notice: Notice, *, now=None) -> bool:
    """Atomically claim a SCHEDULED notice (so a double tap or the scheduler racing a manual
    "send now" can never push twice) and deliver it. True when THIS call published it."""
    now = now or timezone.now()
    with transaction.atomic():
        claimed = Notice.objects.filter(pk=notice.pk, status=Notice.Status.SCHEDULED).update(
            status=Notice.Status.PUBLISHED, published_at=now, scheduled_for=None
        )
    if not claimed:
        return False
    notice.refresh_from_db()
    deliver(notice)
    return True


def publish_due(now=None) -> int:
    """Scheduler entry: publish every scheduled notice whose time has come. Returns how many."""
    now = now or timezone.now()
    due_ids = list(
        Notice.objects.filter(status=Notice.Status.SCHEDULED, scheduled_for__lte=now)
        .order_by("scheduled_for").values_list("pk", flat=True)[:200]
    )
    published = 0
    for pk in due_ids:
        notice = Notice.objects.filter(pk=pk).select_related("posted_by").first()
        if notice and publish_now(notice, now=now):
            published += 1
    return published
