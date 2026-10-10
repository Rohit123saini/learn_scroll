# core/tasks.py
"""
Celery tasks for cross-app side effects. Enqueue them with
`core.async_utils.dispatch_after_commit(task, ...)` — never call `.delay()`
straight from a view/signal (that skips the after-commit guarantee).

Every task takes only JSON-safe primitives (ids, strings) and re-fetches what
it needs, so a task always acts on current DB state and never on a stale
pickled instance.
"""
import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.db import transaction

logger = logging.getLogger(__name__)


def _backoff(task) -> int:
    """30s, 60s, 120s ... for successive retries."""
    return 30 * (2 ** task.request.retries)


def create_notification_rows(recipient_ids, notif_type, title, message="", data=None) -> list:
    """Plain (non-Celery) worker for `create_notifications`: creates one
    `Notification` row per recipient, each in its own savepoint, and returns
    the recipient ids that FAILED. Never raises."""
    from .models import Notification

    failed = []
    for recipient_id in recipient_ids:
        try:
            with transaction.atomic():
                Notification.objects.create(
                    recipient_id=recipient_id,
                    notif_type=notif_type,
                    title=title,
                    message=message,
                    data=data or {},
                )
        except Exception:
            logger.exception("create_notification_rows failed for recipient %s (type=%s).", recipient_id, notif_type)
            failed.append(recipient_id)
    return failed


@shared_task(bind=True, name="core.create_notifications", max_retries=3, acks_late=True)
def create_notifications(self, recipient_ids, notif_type, title, message="", data=None):
    """Celery wrapper around `create_notification_rows`. Only the rows that
    failed are retried, so one bad row can't block the rest and a retry never
    duplicates a row that already succeeded."""
    failed = create_notification_rows(recipient_ids, notif_type, title, message, data)

    if failed:
        raise self.retry(
            args=[failed, notif_type, title, message, data],
            exc=RuntimeError(f"{len(failed)} notification(s) failed to create"),
            countdown=_backoff(self),
        )
    return len(recipient_ids)


CHAT_SYNC_ACTIONS = ("join_accept", "removal", "promote", "demote", "metadata", "archive", "create")

# `removal` reasons that must NOT remove a student who still holds another live
# pass (pass expired/refunded/left but a renewed or second pass is active).
# "kick" (ban) always removes.
_REMOVAL_REASONS_NEED_NO_ACCESS = ("refund", "lapse", "leave")


@shared_task(bind=True, name="core.chat_sync", max_retries=3, acks_late=True)
def chat_sync(self, action, classroom_id, user_id=None, reason=""):
    """Keep a tuitionclass classroom's linked chat group in step with the
    classroom (join accepted / student removed / co-teacher added / metadata
    changed / classroom closed). Thin wrapper over `core.classroom_chat_bridge`
    — all the actual group logic stays there, unchanged."""
    from tuitionclass.models import Classroom

    from . import classroom_chat_bridge as chat_bridge

    if action not in CHAT_SYNC_ACTIONS:
        logger.error("core.chat_sync: unknown action %r — dropping.", action)
        return

    classroom = Classroom.objects.filter(pk=classroom_id).first()
    if classroom is None:
        logger.warning("core.chat_sync(%s): classroom %s no longer exists — nothing to do.", action, classroom_id)
        return

    user = None
    if action in ("join_accept", "removal", "promote", "demote"):
        user = get_user_model().objects.filter(pk=user_id).first()
        if user is None:
            logger.warning("core.chat_sync(%s): user %s no longer exists — nothing to do.", action, user_id)
            return

    try:
        if action == "join_accept":
            chat_bridge.sync_membership_on_join_accept(classroom, user)
        elif action == "removal":
            if reason in _REMOVAL_REASONS_NEED_NO_ACCESS and chat_bridge.student_has_active_access(classroom, user.id):
                return
            chat_bridge.sync_membership_on_removal(classroom, user, reason=reason)
        elif action == "promote":
            chat_bridge.promote_to_moderator(classroom, user)
        elif action == "demote":
            chat_bridge.demote_from_moderator(classroom, user)
        elif action == "create":
            # T3: auto-create the group for a brand-new classroom. Real errors
            # propagate -> Celery retry (3x, backoff). The classroom itself is
            # already committed, so a failure here never un-creates it.
            chat_bridge.ensure_classroom_group(classroom)
        elif action == "metadata":
            chat_bridge.sync_group_metadata(classroom)
        elif action == "archive":
            chat_bridge.archive_group_on_classroom_close(classroom)
    except Exception as exc:
        logger.exception("core.chat_sync(%s) failed for classroom %s; will retry.", action, classroom_id)
        raise self.retry(exc=exc, countdown=_backoff(self))


@shared_task(name="core.reconcile_classroom_groups")
def reconcile_classroom_groups(classroom_id=None, dry_run=False, expired_within_minutes=None, only_enabled_missing=False):
    """T3 — compare each classroom's ACTUAL participants with its chat group's
    members; add the missing, remove the extras, fix roles (see
    `classroom_chat_bridge.reconcile_classroom_group`).

    classroom_id            -> just that classroom (on-demand).
    expired_within_minutes  -> only classrooms that had a pass expire in the
                               last N minutes (cheap frequent sweep, so an
                               expired student leaves the group within minutes).
    otherwise               -> every active, group-enabled classroom (daily).
    Returns a JSON-safe summary; one bad classroom never stops the sweep.
    """
    from datetime import timedelta

    from django.utils import timezone

    from tuitionclass.models import Classroom, PassPurchase

    from . import classroom_chat_bridge as chat_bridge

    qs = Classroom.objects.filter(is_active=True, is_deleted=False, chat_group_enabled=True)
    if classroom_id is not None:
        qs = qs.filter(pk=classroom_id)
    elif expired_within_minutes:
        now = timezone.now()
        ids = PassPurchase.objects.filter(
            expires_at__gte=now - timedelta(minutes=int(expired_within_minutes)), expires_at__lt=now,
        ).values_list("class_pass__classroom_id", flat=True).distinct()
        qs = qs.filter(pk__in=list(ids))
    if only_enabled_missing:
        qs = qs.filter(linked_conversation_id__isnull=True)

    summary = {"classrooms": 0, "created": 0, "added": 0, "removed": 0, "role_fixed": 0, "errors": 0, "reports": []}
    for classroom in qs.select_related("teacher").iterator(chunk_size=200):
        try:
            report = chat_bridge.reconcile_classroom_group(classroom, dry_run=dry_run)
        except Exception:
            logger.exception("reconcile_classroom_groups: classroom %s failed.", classroom.pk)
            summary["errors"] += 1
            continue
        summary["classrooms"] += 1
        summary["created"] += int(bool(report["created"] or (dry_run and report["group_missing"])))
        summary["added"] += len(report["added"])
        summary["removed"] += len(report["removed"])
        summary["role_fixed"] += len(report["role_fixed"])
        if (report["added"] or report["removed"] or report["role_fixed"] or report["group_missing"]) \
                and len(summary["reports"]) < 50:  # keep the Celery result small
            summary["reports"].append(report)
    return summary


@shared_task(bind=True, name="core.section_chat_sync", max_retries=3)
def section_chat_sync(self, section_id):
    """[T4 §D] Roster changed (enroll / transfer / withdraw / assignment) ->
    bring ONE section's chat group in line with its roster. Idempotent."""
    from campus.models import Section

    from . import classroom_chat_bridge as chat_bridge

    section = Section.objects.select_related("school_class").filter(pk=section_id).first()
    if section is None:
        return {"skipped": "missing"}
    try:
        return chat_bridge.reconcile_section_group(section)
    except Exception as exc:  # noqa: BLE001
        logger.exception("section_chat_sync: section %s failed.", section_id)
        raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1))


@shared_task(name="core.reconcile_section_groups")
def reconcile_section_groups(section_id=None, dry_run=False):
    """[T4 §D] Daily sweep (+ management command): every group-enabled section
    of an active campus. One bad section never stops the sweep."""
    from campus.models import Section

    from . import classroom_chat_bridge as chat_bridge

    qs = Section.objects.filter(chat_group_enabled=True, school_class__campus__is_active=True)
    if section_id is not None:
        qs = qs.filter(pk=section_id)
    summary = {"sections": 0, "created": 0, "added": 0, "removed": 0, "role_fixed": 0, "errors": 0, "reports": []}
    for section in qs.select_related("school_class").iterator(chunk_size=200):
        try:
            report = chat_bridge.reconcile_section_group(section, dry_run=dry_run)
        except Exception:
            logger.exception("reconcile_section_groups: section %s failed.", section.pk)
            summary["errors"] += 1
            continue
        summary["sections"] += 1
        summary["created"] += int(bool(report["created"] or (dry_run and report["group_missing"])))
        summary["added"] += len(report["added"])
        summary["removed"] += len(report["removed"])
        summary["role_fixed"] += len(report["role_fixed"])
        if (report["added"] or report["removed"] or report["role_fixed"] or report["group_missing"]) \
                and len(summary["reports"]) < 50:
            summary["reports"].append(report)
    return summary
