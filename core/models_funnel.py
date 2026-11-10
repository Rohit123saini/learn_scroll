"""
T3.8 -- lifecycle funnel events (install -> signup -> onboarding -> first doubt/test -> D1/D7).

Separate from `post.PostEvent` (per-post impressions/dwell): this table has ONE row per lifecycle
milestone (or one `app_open` per user/device/day), so it stays small and cheap to aggregate.
`dedupe_key` (unique) makes every write idempotent -- retries and double taps never double count.
"""
from django.db import models
from django.utils import timezone

from login.models import User


class FunnelEvent(models.Model):
    class Event(models.TextChoices):
        INSTALL = "install", "Install (first app launch on a device)"
        APP_OPEN = "app_open", "App open (max one per user/device/day)"
        SIGNUP = "signup", "Signup (account created)"
        ONBOARDING_COMPLETED = "onboarding_completed", "Onboarding finished or skipped"
        FIRST_DOUBT = "first_doubt", "First doubt asked"
        FIRST_TEST = "first_test", "First test attempt started"
        # H17 cold-start: set by the server only (never by the client)
        INTERESTS_SAVED = "interests_saved", "Class/exam/interests saved (first time)"
        FIRST_FEED = "first_feed", "First personalised feed served"
        RETENTION_D1 = "retention_d1", "Opened the app on day 1 after signup"
        RETENTION_D3 = "retention_d3", "Opened the app on day 3 after signup"
        RETENTION_D7 = "retention_d7", "Opened the app on day 7 after signup"
        # P-F1: one row per (user, onboarding step, action) - reported by the app, validated by core/onboarding_steps.py
        ONBOARDING_STEP = "onboarding_step", "Onboarding step viewed / completed / skipped"

    # events the mobile client may report itself on the PUBLIC endpoint; everything else is recorded server-side only.
    # (ONBOARDING_STEP has its own authenticated endpoint with an allow-list - see core/onboarding_steps.py.)
    CLIENT_EVENTS = (Event.INSTALL, Event.APP_OPEN)

    event = models.CharField(max_length=32, choices=Event.choices)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="funnel_events")
    device_id = models.CharField(max_length=64, blank=True, default="")
    platform = models.CharField(max_length=16, blank=True, default="")
    app_version = models.CharField(max_length=32, blank=True, default="")
    props = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=120, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "core_funnel_event"
        indexes = [
            models.Index(fields=["event", "created_at"], name="funnel_event_created_idx"),
            models.Index(fields=["user", "event"], name="funnel_user_event_idx"),
            models.Index(fields=["device_id", "event"], name="funnel_device_event_idx"),
        ]

    def __str__(self):
        return f"{self.event} user={self.user_id} device={self.device_id[:8]}"
