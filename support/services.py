"""support/services.py — the one place that changes ticket state, so the API,
the admin and any future tooling can't drift apart."""
import logging

from django.db import transaction
from django.utils import timezone

from .models import SupportMessage, SupportTicket

logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_TICKET = 200


class TicketClosed(Exception):
    pass


class TicketFull(Exception):
    pass


def add_user_message(ticket: SupportTicket, user, body: str) -> SupportMessage:
    """A user reply. Closed tickets refuse; an answered/resolved ticket reopens."""
    with transaction.atomic():
        t = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
        if t.status == SupportTicket.Status.CLOSED:
            raise TicketClosed()
        if t.messages.count() >= MAX_MESSAGES_PER_TICKET:
            raise TicketFull()
        msg = SupportMessage.objects.create(ticket=t, sender=user, is_staff=False, body=body)
        t.status = SupportTicket.Status.OPEN
        t.last_message_at = msg.created_at
        t.save(update_fields=["status", "last_message_at", "updated_at"])
    ticket.refresh_from_db()
    return msg


def add_staff_reply(ticket: SupportTicket, staff_user, body: str, *, resolve: bool = False) -> SupportMessage:
    """Staff reply: marks the ticket answered (or resolved), flags it unread
    for the user and sends them a notification."""
    with transaction.atomic():
        t = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
        msg = SupportMessage.objects.create(ticket=t, sender=staff_user, is_staff=True, body=body)
        t.status = SupportTicket.Status.RESOLVED if resolve else SupportTicket.Status.ANSWERED
        t.has_unread_reply = True
        t.last_message_at = msg.created_at
        t.save(update_fields=["status", "has_unread_reply", "last_message_at", "updated_at"])
    _notify_user(t, body)
    return msg


def _notify_user(ticket: SupportTicket, body: str):
    """Best-effort bell notification; never breaks the reply."""
    try:
        from core.models import Notification
        from core.services import create_notification

        create_notification(
            ticket.user_id,
            Notification.NotifType.SUPPORT_REPLY,
            "LearnScroll Support replied",
            body[:140],
            data={"ticket_id": str(ticket.id)},
        )
    except Exception:
        logger.exception("support: could not notify user %s about ticket %s", ticket.user_id, ticket.pk)
