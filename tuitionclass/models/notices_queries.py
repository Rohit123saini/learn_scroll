# tuitionclass/models/notices_queries.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from login.models import User
from django.db import models
from django.utils import timezone
from .schedules import (
    ClassSchedule,
    ClassSession,
)
from .classroom import Classroom


# ---------------------------------------------------------------------------
# 17. CLASS REMINDERS (scheduled notifications before a session starts)
# ---------------------------------------------------------------------------
class ClassReminder(models.Model):
    class Channel(models.TextChoices):
        PUSH = "push", "Push Notification"
        SMS = "sms", "SMS"
        EMAIL = "email", "Email"
        # NOTE (Pass 7): WhatsApp is India's highest-open-rate channel —
        # added the moment notifications.py actually had a working
        # _send_whatsapp sender behind it (see that file). No migration
        # concern here beyond the usual new-choice-value one: existing
        # rows keep whatever channel they were created with.
        WHATSAPP = "whatsapp", "WhatsApp"

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="reminders")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="class_reminders")

    remind_at = models.DateTimeField()
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.PUSH)
    is_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ["remind_at"]
        indexes = [models.Index(fields=["remind_at", "is_sent"])]

    def __str__(self):
        return f"Reminder for {self.user} @ {self.remind_at}"


# ---------------------------------------------------------------------------
# 18. CLASS HOLIDAY / OFF-DAY (exception dates — festival, teacher leave, etc.)
# ---------------------------------------------------------------------------
class ClassHoliday(models.Model):
    """A specific calendar date on which class is OFF for a classroom.

    This is different from ClassSchedule.days_of_week (which defines the
    *regular* weekly pattern, e.g. "runs Mon/Wed/Fri"): ClassHoliday is a
    one-off exception on top of that pattern — e.g. Diwali, a teacher's sick
    leave, a public holiday — so the session-generation job can skip creating
    a ClassSession for that date without editing the recurrence rule itself.
    """

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="holidays")
    schedule = models.ForeignKey(
        ClassSchedule,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="holidays",
        help_text="Leave blank to mark this date off across every schedule in the classroom.",
    )

    date = models.DateField()
    reason = models.CharField(max_length=150, blank=True, help_text="e.g. 'Diwali', 'Teacher on leave'")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="holidays_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("classroom", "schedule", "date")
        ordering = ["date"]
        indexes = [models.Index(fields=["classroom", "date"])]

    def __str__(self):
        return f"{self.classroom.title} off on {self.date} ({self.reason or 'holiday'})"


# ---------------------------------------------------------------------------
# 19. NOTICE BOARD (announcements from teacher/staff to enrolled students)
# ---------------------------------------------------------------------------
class Notice(models.Model):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        URGENT = "urgent", "Urgent"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="notices")
    posted_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tuitionclass_notices_posted")

    title = models.CharField(max_length=150)
    message = models.TextField()
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    is_pinned = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Optional auto-hide time, e.g. for a one-off exam-date notice."
    )

    # TASK 9.2 — a notice the system posted on behalf of another feature, e.g.
    # "a test was published in this class" (source_type="testseries",
    # source_id=<TestSeries id>). Opaque on purpose (golden rule: tuitionclass
    # never imports testseries models). Blank/NULL for notices a teacher typed.
    # (classroom, source_type, source_id) is UNIQUE, which is what makes the
    # automatic notice idempotent: announcing the same test twice finds the
    # existing row instead of creating a second notice.
    source_type = models.CharField(max_length=30, blank=True, default="")
    source_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["classroom", "source_type", "source_id"],
                condition=models.Q(source_id__isnull=False),
                name="uniq_notice_per_source",
            ),
        ]
        indexes = [
            models.Index(fields=["classroom", "is_pinned", "-created_at"]),
            # NOTE (fix): backs seconds_until_next_notice_expiry()'s
            # per-classroom "soonest non-expired notice" lookup above —
            # without this, that query would fall back to a sequential
            # scan filtered by classroom_id alone.
            models.Index(fields=["classroom", "expires_at"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.classroom.title})"

    def is_expired(self) -> bool:
        return bool(self.expires_at and timezone.now() > self.expires_at)


# ---------------------------------------------------------------------------
# 20. CLASS QUERY / DOUBT (student asks a question, teacher/staff answers)
# ---------------------------------------------------------------------------
class ClassQuery(models.Model):
    """A student's question about a classroom (optionally tied to one
    specific session, e.g. a doubt from today's lecture). The classroom's
    teacher, co-teacher, or moderator can answer it — same permission split
    used for notices/holidays (see `_can_manage_classroom` in views.py)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ANSWERED = "answered", "Answered"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="queries")
    session = models.ForeignKey(
        ClassSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="queries",
        help_text="Optional — set if the doubt is about a specific live session.",
    )
    asked_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="queries_asked")

    question = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    answer = models.TextField(blank=True)
    answered_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="queries_answered"
    )
    answered_at = models.DateTimeField(null=True, blank=True)

    # LIVE Q&A QUEUE: denormalised count of ClassQueryUpvote rows (kept in step by
    # ClassQueryViewSet.upvote) so the queue sorts without a COUNT per row.
    upvote_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["classroom", "status", "-created_at"])]

    def __str__(self):
        return f"{self.asked_by} asked: {self.question[:40]} ({self.get_status_display()})"


class ClassQueryUpvote(models.Model):
    """One person's +1 on a live Q&A question (a person can't vote their own question)."""

    query = models.ForeignKey(ClassQuery, on_delete=models.CASCADE, related_name="upvotes")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="query_upvotes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("query", "user")

    def __str__(self):
        return f"{self.user} +1 on query {self.query_id}"
