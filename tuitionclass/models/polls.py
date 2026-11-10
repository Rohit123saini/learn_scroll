# tuitionclass/models/polls.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from login.models import User
from django.db import models
from .schedules import ClassSession
from .classroom import Classroom


# ---------------------------------------------------------------------------
# 9. LIVE POLLS (teacher engagement tool during a session)
# ---------------------------------------------------------------------------
class LivePoll(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="polls")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_polls")

    question = models.CharField(max_length=255)
    options = models.JSONField(default=list)  # e.g. ["Option A", "Option B", "Option C"]

    # QUIZ PUSH: when `correct_option_index` is set the poll is a quiz. It is NEVER sent to students
    # (REST or socket) until the host reveals it (`revealed_at` set by LivePollViewSet.reveal) -
    # see LivePollSerializer.to_representation. None = an ordinary opinion poll.
    correct_option_index = models.PositiveSmallIntegerField(null=True, blank=True)
    explanation = models.CharField(max_length=300, blank=True, default="")
    revealed_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.question


class PollResponse(models.Model):
    poll = models.ForeignKey(LivePoll, on_delete=models.CASCADE, related_name="responses")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="poll_responses")
    selected_option_index = models.PositiveSmallIntegerField()
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("poll", "student")

    def __str__(self):
        return f"{self.student} -> {self.poll} [{self.selected_option_index}]"


class PollTemplate(models.Model):
    """NEW (Pass 13) — quick-poll templates: a teacher who reuses the same
    "Samajh aaya? Yes/No"-style poll every session saves it once here and
    fires it in one tap via LivePollViewSet.quick_create() instead of
    retyping the question/options each time.

    Scoped to a classroom (not global/platform-wide) — a teacher's poll
    templates are their own classroom's shorthand, not a shared library
    across every classroom on the platform; `_can_manage_classroom` (the
    same boundary already used for assigments/Notice/ClassHoliday) gates
    create/update/delete in PollTemplateViewSet.
    """

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="poll_templates")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_poll_templates")

    question = models.CharField(max_length=255)
    options = models.JSONField(default=list)
    # Quiz template: when set, quick_create makes a QUIZ poll (same fields as LivePoll). null = plain poll.
    correct_option_index = models.PositiveSmallIntegerField(null=True, blank=True)
    explanation = models.CharField(max_length=300, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.question} ({self.classroom.title})"
