"""
user_profile/moderation_log.py - ONE place that (a) writes the staff / automation
audit trail (ModerationAction) and (b) tells the content owner what happened
(in-app notification + the "Notices" screen). Audit items 17 and 20.

Everything here is best-effort: a failed log or notification must never break the
moderation action itself, so every function swallows and logs its own errors.
"""

import logging

logger = logging.getLogger(__name__)

# What the owner reads. {what} = "post" / "comment" ...
_WHAT = {
    "post": "post", "comment": "comment", "story": "story", "message": "message",
    "class_chat": "class chat message", "live_session": "live class", "profile_photo": "profile photo",
    "user": "account",
}


def what(target_type):
    return _WHAT.get(target_type, "content")


def log_action(action, *, target_type, target_id, actor=None, owner=None, reason="", count=0,
               note="", owner_visible=False, can_appeal=False):
    """Append one ModerationAction row. Returns it, or None if writing failed."""
    from .models import ModerationAction

    try:
        has_actor = actor is not None and getattr(actor, "pk", None)
        return ModerationAction.objects.create(
            action=action,
            actor=actor if has_actor else None,
            actor_label=((getattr(actor, "username", None) if has_actor else None) or "system")[:40],
            target_type=target_type,
            target_id=str(target_id),
            target_owner=owner if (owner is not None and getattr(owner, "pk", None)) else None,
            reason=(reason or "")[:20],
            report_count=int(count or 0),
            note=(note or "")[:500],
            owner_visible=bool(owner_visible),
            can_appeal=bool(can_appeal),
        )
    except Exception:
        logger.exception("moderation audit log failed (%s %s:%s)", action, target_type, target_id)
        return None


def notify(user, title, message="", *, data=None):
    """In-app notification through core's single choke point. Never raises."""
    if user is None:
        return
    try:
        from core.services import create_notification

        payload = {"kind": "moderation_notice"}
        payload.update(data or {})
        create_notification(user, "generic", title, message, data=payload)
    except Exception:
        logger.exception("moderation notification failed")


def tell_owner(owner, action_row, title, message):
    """Notify the owner and point the notification at the audit row so the app
    can open the Notices screen / appeal sheet straight from the push."""
    if owner is None or action_row is None:
        return
    notify(owner, title, message, data={
        "notice_id": action_row.pk,
        "target_type": action_row.target_type,
        "target_id": action_row.target_id,
        "can_appeal": bool(action_row.can_appeal),
    })
