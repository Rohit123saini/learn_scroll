"""
support/models.py — Help & feedback.

Three independent things, one app:
  1. SupportTicket + SupportMessage   in-app support chat (user <-> LearnScroll staff)
  2. BugReport                        bug report with optional screenshot + device info
  3. FeatureRequest + FeatureVote     "I want this feature" board with one vote per user

Staff answer tickets from Django admin (support/admin.py), which goes through
support/services.py so the user is notified and the ticket status stays right.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import UniqueConstraint


class SupportTicket(models.Model):
    class Category(models.TextChoices):
        ACCOUNT = "account", "Account / login"
        PAYMENT = "payment", "Coins / payments"
        CLASS = "class", "Classes"
        TEST = "test", "Tests / assignments"
        SAFETY = "safety", "Safety / abuse"
        OTHER = "other", "Something else"

    class Status(models.TextChoices):
        OPEN = "open", "Open"            # waiting for staff
        ANSWERED = "answered", "Answered"  # staff replied, waiting for the user
        RESOLVED = "resolved", "Resolved"  # staff marked it done (user reply reopens)
        CLOSED = "closed", "Closed"        # the user closed it (no more replies)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_tickets")
    subject = models.CharField(max_length=120)
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.OTHER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    # True from the moment staff reply until the user opens the ticket.
    has_unread_reply = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_message_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-last_message_at"]
        indexes = [models.Index(fields=["user", "-last_message_at"], name="support_ticket_user_idx")]

    def __str__(self):
        return f"[{self.status}] {self.subject}"


class SupportMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    # NULL sender = the author's account was deleted.
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="support_messages",
    )
    is_staff = models.BooleanField(default=False)
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{'staff' if self.is_staff else 'user'}: {self.body[:40]}"


class BugReport(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        TRIAGED = "triaged", "Triaged"
        FIXED = "fixed", "Fixed"
        WONT_FIX = "wont_fix", "Won't fix"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="bug_reports",
    )
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=4000)
    # Filled in by the app so staff can reproduce without a back-and-forth.
    screen = models.CharField(max_length=80, blank=True, default="")
    app_version = models.CharField(max_length=40, blank=True, default="")
    platform = models.CharField(max_length=20, blank=True, default="")
    device_info = models.CharField(max_length=120, blank=True, default="")
    screenshot = models.ImageField(upload_to="support/bugs/%Y/%m/", null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    admin_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.status}] {self.title}"


class FeatureRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open for votes"
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In progress"
        SHIPPED = "shipped", "Shipped"
        DECLINED = "declined", "Not planned"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="feature_requests",
    )
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=1000, blank=True, default="")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    # Denormalised; kept exact by support/signals.py from the FeatureVote rows.
    votes_count = models.PositiveIntegerField(default=0, db_index=True)
    # Moderation switch: hidden requests vanish from the board but stay in admin.
    is_hidden = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-votes_count", "-created_at"]

    def __str__(self):
        return f"{self.title} ({self.votes_count})"


class FeatureVote(models.Model):
    request = models.ForeignKey(FeatureRequest, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="feature_votes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [UniqueConstraint(fields=["request", "user"], name="unique_feature_vote")]
