"""core/signals.py - live bell badge (Task 3.3).

Every create / read / delete of a Notification row publishes the owner's new
unread total on the socket (core.services.publish_unread_badge). Folding a new
actor into a batched row (`save(update_fields=[..., "is_read"])`) goes through
post_save too, so a new follower lights the badge up immediately.
"""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from login.models import User

from .models import FeatureFlag, Notification


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


# ---------------------------------------------------------------------------------------------------
# T3.8 -- funnel milestones are recorded server-side (cannot be spoofed / missed by the client).
# Lazy "app_label.Model" senders keep `core` free of module-level imports from message / testseries.
# ---------------------------------------------------------------------------------------------------
@receiver(post_save, sender=User, dispatch_uid="core_funnel_signup")
def _funnel_signup(sender, instance, created, **kwargs):
    if created:
        from . import funnel

        funnel.record_once_per_user(funnel.E.SIGNUP, instance)


@receiver(post_save, sender="message.DoubtQuestion", dispatch_uid="core_funnel_first_doubt")
def _funnel_first_doubt(sender, instance, created, **kwargs):
    if created and getattr(instance, "author_id", None):
        from . import funnel

        funnel.record_once_per_user(funnel.E.FIRST_DOUBT, instance.author)


@receiver(post_save, sender="testseries.TestAttempt", dispatch_uid="core_funnel_first_test")
def _funnel_first_test(sender, instance, created, **kwargs):
    if created and getattr(instance, "student_id", None):
        from . import funnel

        funnel.record_once_per_user(funnel.E.FIRST_TEST, instance.student)


# ---------------------------------------------------------------------------------------------------
# N5 / S8 -- Focus / exam mode (and a focus schedule / study reminder) needs a NotificationPreference row to be enforced: several push paths treat "no row" as
# "brand-new user, push everything". So the moment exam mode is switched on, make sure the row exists.
# ---------------------------------------------------------------------------------------------------
@receiver(post_save, sender="user_profile.UserPreference", dispatch_uid="core_focus_mode_pref_row")
def _ensure_notification_pref_for_focus(sender, instance, **kwargs):
    # S8: a focus-schedule window and the study reminder need the row too (same "no row = push everything" rule).
    if (getattr(instance, "exam_mode", False) or getattr(instance, "focus_schedule_enabled", False)
            or getattr(instance, "study_reminder_enabled", False)):
        try:
            from .models import NotificationPreference

            NotificationPreference.objects.get_or_create(user_id=instance.user_id)
        except Exception:  # noqa: BLE001 - never break saving the study profile
            import logging

            logging.getLogger(__name__).exception("focus / study reminder: could not ensure NotificationPreference row")


# P-J2 - a saved / deleted FeatureFlag row must reach every request at once (the resolver caches rows for ~30 s).
@receiver(post_save, sender=FeatureFlag, dispatch_uid="core_feature_flag_save")
@receiver(post_delete, sender=FeatureFlag, dispatch_uid="core_feature_flag_delete")
def _feature_flag_changed(sender, instance=None, **kwargs):
    from common.feature_flags import invalidate_cache

    invalidate_cache()

