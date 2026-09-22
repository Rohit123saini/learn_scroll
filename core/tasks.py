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


CHAT_SYNC_ACTIONS = ("join_accept", "removal", "promote", "metadata", "archive")


@shared_task(bind=True, name="core.chat_sync", max_retries=3, acks_late=True)
def chat_sync(self, action, classroom_id, user_id=None, reason=""):
    """Keep a liveclass classroom's linked chat group in step with the
    classroom (join accepted / student removed / co-teacher added / metadata
    changed / classroom closed). Thin wrapper over `core.classroom_chat_bridge`
    — all the actual group logic stays there, unchanged."""
    from liveclass.models import Classroom

    from . import classroom_chat_bridge as chat_bridge

    if action not in CHAT_SYNC_ACTIONS:
        logger.error("core.chat_sync: unknown action %r — dropping.", action)
        return

    classroom = Classroom.objects.filter(pk=classroom_id).first()
    if classroom is None:
        logger.warning("core.chat_sync(%s): classroom %s no longer exists — nothing to do.", action, classroom_id)
        return

    user = None
    if action in ("join_accept", "removal", "promote"):
        user = get_user_model().objects.filter(pk=user_id).first()
        if user is None:
            logger.warning("core.chat_sync(%s): user %s no longer exists — nothing to do.", action, user_id)
            return

    try:
        if action == "join_accept":
            chat_bridge.sync_membership_on_join_accept(classroom, user)
        elif action == "removal":
            chat_bridge.sync_membership_on_removal(classroom, user, reason=reason)
        elif action == "promote":
            chat_bridge.promote_to_moderator(classroom, user)
        elif action == "metadata":
            chat_bridge.sync_group_metadata(classroom)
        elif action == "archive":
            chat_bridge.archive_group_on_classroom_close(classroom)
    except Exception as exc:
        logger.exception("core.chat_sync(%s) failed for classroom %s; will retry.", action, classroom_id)
        raise self.retry(exc=exc, countdown=_backoff(self))
