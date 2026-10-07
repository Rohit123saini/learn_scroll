"""core/signals.py - live bell badge (Task 3.3).

Every create / read / delete of a Notification row publishes the owner's new
unread total on the socket (core.services.publish_unread_badge). Folding a new
actor into a batched row (`save(update_fields=[..., "is_read"])`) goes through
post_save too, so a new follower lights the badge up immediately.
"""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Notification


@receiver(post_save, sender=Notification, dispatch_uid="core_notification_badge_save")
def _badge_on_save(sender, instance, created, update_fields=None, **kwargs):
    # Only saves that can change the unread total: a new row, or one that touches is_read.
    if created or update_fields is None or "is_read" in update_fields:
        from .services import publish_unread_badge

        publish_unread_badge(instance.recipient_id)


@receiver(post_delete, sender=Notification, dispatch_uid="core_notification_badge_delete")
def _badge_on_delete(sender, instance, **kwargs):
    if not instance.is_read:
        from .services import publish_unread_badge

        publish_unread_badge(instance.recipient_id)
