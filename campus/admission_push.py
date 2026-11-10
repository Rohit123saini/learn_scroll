# campus/admission_push.py
"""
Notifications for admission. Two events:

  enrolled          -> the STUDENT account that was just enrolled        [push]
  follow_up_digest  -> the campus's ADMINS / PRINCIPALS, once a day, only
                       when something is due ("3 admission follow-ups due") [push]

The guardian's phone, the applicant's details and staff `remarks` are NEVER put
in a push body (lock screens). Runs in a Celery worker
(`tasks.send_admission_notifications`, `tasks.send_admission_followup_digest`).
"""
import logging

from django.db.models import Count
from django.utils import timezone

from . import bridge
from .bridge import NotifTypes
from .models import AdmissionApplication, StaffProfile

logger = logging.getLogger(__name__)


def notify_enrolled(app) -> int:
    if app.student_id is None:
        return 0
    campus_name = app.campus.name
    section = f"{app.school_class.name} - {app.section.name}" if app.school_class and app.section else ""
    bridge.notify(
        users=[app.student],
        notif_type=NotifTypes.CAMPUS_ADMISSION,
        title=f"Welcome to {campus_name}!",
        body=f"Your admission is confirmed{(' for ' + section) if section else ''}.",
        data={
            "type": NotifTypes.CAMPUS_ADMISSION,
            "kind": "enrolled",
            "campus_id": str(app.campus_id),
            "application_id": str(app.pk),
        },
        push=True,
    )
    return 1


def admins_of(campus_id):
    rows = StaffProfile.objects.filter(
        campus_id=campus_id, is_active=True,
        role__in=(StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD),
    ).select_related("user")
    seen, users = set(), []
    for row in rows:
        if row.user_id not in seen:
            seen.add(row.user_id)
            users.append(row.user)
    return users


def notify_followup_digest(today=None) -> int:
    """One push per campus whose admins have follow-ups due today or earlier. Returns pushes sent (campuses)."""
    today = today or timezone.localdate()
    rows = (
        AdmissionApplication.objects.filter(
            status__in=(
                AdmissionApplication.Status.ENQUIRY, AdmissionApplication.Status.APPLIED,
                AdmissionApplication.Status.INTERVIEW, AdmissionApplication.Status.WAITLISTED,
                AdmissionApplication.Status.OFFERED,
            ),
            follow_up_on__isnull=False, follow_up_on__lte=today, campus__is_active=True,
        )
        .values("campus_id")
        .annotate(n=Count("id"))
    )
    sent = 0
    for row in rows:
        users = admins_of(row["campus_id"])
        if not users:
            continue
        n = row["n"]
        bridge.notify(
            users=users,
            notif_type=NotifTypes.CAMPUS_ADMISSION,
            title="Admission follow-ups due",
            body=f"{n} admission follow-up{'s' if n != 1 else ''} due today or earlier.",
            data={"type": NotifTypes.CAMPUS_ADMISSION, "kind": "follow_up_digest", "campus_id": str(row["campus_id"]), "count": n},
            push=True,
        )
        sent += 1
    return sent
