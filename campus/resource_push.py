# campus/resource_push.py
"""
Push for Class Resources (content share): "new PDF / video / document / link
in <subject>".

Audience = exactly the Homework Diary audience (`homework_diary_push.
homework_audience`): every ACTIVE student of the resource's section plus their
linked parents — real `User` parents through the normal bell + push pipeline
(NotificationPreference honoured) AND loginless Parent Mode devices through
`bridge.push_to_parent_devices`. The poster is never notified about their own
post. Drafts are never pushed (the task refuses them).

Runs in a Celery worker (`tasks.send_class_resource_posted`), never in the
request path.

Anti-spam: a teacher uploading 15 PDFs in a row must not buzz 60 families 15
times. `is_burst()` lets the task stay quiet once the same poster has already
pushed `CAMPUS_RESOURCE_PUSH_BURST_LIMIT` (default 3) resources into the same
folder within `CAMPUS_RESOURCE_PUSH_BURST_MINUTES` (default 10). The resources
are still visible in the app — only the extra pushes are skipped.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import bridge
from .bridge import NotifTypes
from .homework_diary_push import homework_audience
from .models import ClassResource

logger = logging.getLogger(__name__)

_KIND_LABEL = {
    ClassResource.Kind.PDF: "PDF",
    ClassResource.Kind.VIDEO: "Video",
    ClassResource.Kind.DOCUMENT: "Document",
    ClassResource.Kind.IMAGE: "Image",
    ClassResource.Kind.LINK: "Link",
}


def is_burst(resource) -> bool:
    """True if this poster already pushed enough resources into this folder
    recently. Call it BEFORE stamping `notified_at` on `resource`."""
    limit = max(1, int(getattr(settings, "CAMPUS_RESOURCE_PUSH_BURST_LIMIT", 3)))
    minutes = max(1, int(getattr(settings, "CAMPUS_RESOURCE_PUSH_BURST_MINUTES", 10)))
    since = timezone.now() - timedelta(minutes=minutes)
    recent = ClassResource.objects.filter(
        folder_id=resource.folder_id,
        posted_by_id=resource.posted_by_id,
        notified_at__gte=since,
    ).exclude(pk=resource.pk)
    return recent.count() >= limit


def _payload(resource):
    return {
        "type": NotifTypes.CAMPUS_RESOURCE_POSTED,
        "resource_id": str(resource.pk),
        "folder_id": str(resource.folder_id),
        "section_id": str(resource.section_id),
        "subject_id": str(resource.subject_id),
        "campus_id": str(resource.campus_id),
        "kind": resource.kind,
    }


def notify_resource_posted(resource) -> int:
    """New published resource -> section students + parents. Returns pushes attempted."""
    students, parents = homework_audience(resource)  # needs .section_id + .campus_id
    data = _payload(resource)

    recipients = {u.pk: u for u in students}
    recipients.update({p.pk: p for p in parents})
    if resource.posted_by_id:
        recipients.pop(resource.posted_by_id, None)

    kind_label = _KIND_LABEL.get(resource.kind, "Resource")
    title = f"New {kind_label}: {resource.subject.name}"
    body = f"{resource.title[:120]} · {resource.folder.name[:60]}"

    sent = 0
    if recipients:
        bridge.notify(
            users=list(recipients.values()),
            notif_type=NotifTypes.CAMPUS_RESOURCE_POSTED,
            title=title,
            body=body,
            data=data,
            push=True,
        )
        sent += len(recipients)

    for student in students:
        try:
            sent += bridge.push_to_parent_devices(student=student, title=title, body=body, data=data)
        except Exception:  # pragma: no cover - the helper already swallows
            logger.exception("campus resource push to parent devices failed (resource=%s).", resource.pk)
    return sent
