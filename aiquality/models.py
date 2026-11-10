"""aiquality/models.py - P-F6: AI quality loop.

AiCallLog   one row per real model call (never per cache hit): feature, prompt version + hash, model, latency, tokens,
            estimated cost. Feeds the staff cost dashboard. Kept AI_CALL_LOG_RETENTION_DAYS (default 90).
AiFeedback  a student's 👍 / 👎 and "report wrong answer" on ONE AI output, with the prompt version that produced it.
            One row per (user, output): tapping again changes the row, it does not add one.
"""
import uuid

from django.conf import settings
from django.db import models


class AiCallLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    surface = models.CharField(max_length=24)
    feature = models.CharField(max_length=32)
    model = models.CharField(max_length=60, blank=True, default="")
    prompt_version = models.CharField(max_length=24, blank=True, default="")
    prompt_hash = models.CharField(max_length=12, blank=True, default="")
    ok = models.BooleanField(default=True)
    error_kind = models.CharField(max_length=40, blank=True, default="")
    has_image = models.BooleanField(default=False)
    latency_ms = models.PositiveIntegerField(default=0)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    tokens_estimated = models.BooleanField(default=False)  # True = chars/4 guess, the SDK gave no usage numbers
    cost_micro_usd = models.PositiveBigIntegerField(default=0)  # 1_000_000 = 1 USD

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["surface", "feature", "created_at"], name="aiq_call_feat_idx"),
            models.Index(fields=["user", "created_at"], name="aiq_call_user_idx"),
        ]

    def __str__(self):
        return f"{self.surface}.{self.feature} {self.prompt_version} {'ok' if self.ok else 'fail'}"


class AiFeedback(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        CONFIRMED_WRONG = "confirmed_wrong", "Confirmed wrong"
        FIXED = "fixed", "Fixed (prompt / data)"
        DISMISSED = "dismissed", "Dismissed (answer was fine)"

    class Reason(models.TextChoices):
        WRONG = "wrong_answer", "Wrong answer"
        UNCLEAR = "unclear", "Confusing / unclear"
        OFF_TOPIC = "off_topic", "Not about my question"
        WRONG_LANGUAGE = "wrong_language", "Wrong language"
        UNSAFE = "unsafe", "Unsafe / inappropriate"
        TOO_LONG = "too_long", "Too long / too short"
        OTHER = "other", "Something else"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_feedback")
    surface = models.CharField(max_length=24)
    feature = models.CharField(max_length=32)
    ref_id = models.CharField(max_length=80, blank=True, default="")      # e.g. PostAiStudy id; empty for one-off answers
    call = models.ForeignKey(AiCallLog, null=True, blank=True, on_delete=models.SET_NULL, related_name="feedback")
    prompt_version = models.CharField(max_length=24, blank=True, default="")
    model = models.CharField(max_length=60, blank=True, default="")
    rating = models.SmallIntegerField(default=0)                          # 1 = 👍, -1 = 👎
    reason = models.CharField(max_length=16, choices=Reason.choices, blank=True, default="")
    comment = models.CharField(max_length=500, blank=True, default="")
    reported_wrong = models.BooleanField(default=False, db_index=True)
    excerpt = models.CharField(max_length=800, blank=True, default="")    # the (shortened) AI output, as the student saw it
    dedupe_key = models.CharField(max_length=40)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW, db_index=True)
    staff_note = models.CharField(max_length=500, blank=True, default="")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "dedupe_key"], name="aiq_feedback_one_per_output"),
        ]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="aiq_fb_status_idx"),
            models.Index(fields=["surface", "feature", "prompt_version"], name="aiq_fb_prompt_idx"),
        ]

    def __str__(self):
        return f"{self.surface}.{self.feature} {self.rating:+d} {self.status}"
