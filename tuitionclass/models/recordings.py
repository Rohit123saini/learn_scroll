# tuitionclass/models/recordings.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from login.models import User
from django.db import models
from django.utils import timezone
from .schedules import ClassSession


# ---------------------------------------------------------------------------
# X12 L-C - "Remind me" on a scheduled standalone live (see standalone_live.py).
# ---------------------------------------------------------------------------
class LiveReminderSub(models.Model):
    """One person asked to be reminded about one scheduled live. They get the "starts in N minutes" push (N9 quiet
    hours respected) and the "is live now" push even when they do not follow the host."""

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="live_reminder_subs")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="live_reminder_subs")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session", "user"], name="uniq_live_reminder_session_user")]
        indexes = [models.Index(fields=["user", "-created_at"], name="tc_livereminder_user_idx")]

    def __str__(self):  # pragma: no cover
        return f"LiveReminderSub(session={self.session_id}, user={self.user_id})"


# ---------------------------------------------------------------------------
# X12 - AUTO RECORDING: chapters, replay watch tracking, notes on a recording, "Save as lecture / reel / post".
# All of it hangs off ClassSession (one recording per session - recording_url); nothing here changes how a session
# is joined, recorded or ended. See recording_auto.py / recording_tools.py / recording_save.py / recording_views.py.
# ---------------------------------------------------------------------------
class SessionChapter(models.Model):
    """A marker on the recording timeline ("Class started", "Presenting: Chapter 3.pdf", "Poll: ..."). Created by the
    server the moment a teacher presents a material / fires a poll WHILE a recording runs (recording_tools.add_chapter),
    or by hand. `offset_seconds` is measured from ClassSession.recording_started_at, i.e. from the start of the video."""

    class Kind(models.TextChoices):
        START = "start", "Class started"
        PRESENT = "present", "Presentation"
        POLL = "poll", "Poll"
        QUIZ = "quiz", "Quiz"
        MANUAL = "manual", "Added by teacher"

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="chapters")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.MANUAL)
    title = models.CharField(max_length=120)
    offset_seconds = models.PositiveIntegerField(default=0)
    # What the marker points at ("material" / "poll") - opaque, never a hard FK, so deleting a poll keeps its marker.
    ref_type = models.CharField(max_length=12, blank=True, default="")
    ref_id = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["offset_seconds", "id"]
        indexes = [models.Index(fields=["session", "offset_seconds"], name="tc_chapter_sess_off_idx")]

    def __str__(self):
        return f"{self.offset_seconds}s {self.title} (session {self.session_id})"


class RecordingWatch(models.Model):
    """One row per (session, viewer): how much of the RECORDING this person has watched. Written by the replay screen's
    ping (recording_views.RecordingWatchView). Feeds the viewer list and the insights' watch time / completion."""

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="recording_watches")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recording_watches")
    position_seconds = models.PositiveIntegerField(default=0)      # where they are / stopped (resume)
    watched_seconds = models.PositiveIntegerField(default=0)       # total seconds actually played (server-clamped sum)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    completed = models.BooleanField(default=False)
    first_watched_at = models.DateTimeField(auto_now_add=True)
    last_watched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = (("session", "user"),)
        indexes = [models.Index(fields=["session", "-last_watched_at"], name="tc_recwatch_sess_last_idx")]

    def __str__(self):
        return f"{self.user_id} watched {self.watched_seconds}s of session {self.session_id}"


class RecordingNote(models.Model):
    """A class material (notes PDF / slides / link) the teacher attached to a session's recording. Shown under the
    replay player (optionally at a timestamp). Visible to exactly those who can open the recording."""

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="recording_notes")
    material = models.ForeignKey("ClassMaterial", on_delete=models.CASCADE, related_name="recording_notes")
    at_seconds = models.PositiveIntegerField(null=True, blank=True)   # null = whole recording
    attached_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("session", "material"),)
        ordering = ["at_seconds", "id"]

    def __str__(self):
        return f"note {self.material_id} on recording of session {self.session_id}"


class RecordingSave(models.Model):
    """"Save as lecture / reel / post": a trimmed copy of the recording (ffmpeg, recording_save.py) published as a post.
    The row is the JOB (pending -> processing -> ready | failed) the Flutter save screen polls; `post_id` is the result.
    visibility defaults to class_only so a paid class never becomes public by accident."""

    class Kind(models.TextChoices):
        LECTURE = "lecture", "Lecture"
        REEL = "reel", "Reel"
        POST = "post", "Post"

    class Status(models.TextChoices):
        PENDING = "pending", "Queued"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    class Visibility(models.TextChoices):
        CLASS_ONLY = "class_only", "This class only"
        FOLLOWERS = "followers", "Followers"
        PUBLIC = "public", "Public"

    MAX_REEL_SECONDS = 120

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="recording_saves")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recording_saves")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    start_seconds = models.PositiveIntegerField(default=0)
    end_seconds = models.PositiveIntegerField()
    visibility = models.CharField(max_length=12, choices=Visibility.choices, default=Visibility.CLASS_ONLY)
    include_chapters = models.BooleanField(default=True)
    # Lecture only: add the new post to this VideoSeries (opaque uuid string; ownership checked on create).
    series_id = models.CharField(max_length=40, blank=True, default="")
    # Result: the Post's uuid (opaque - tuitionclass never hard-links the post app).
    post_id = models.CharField(max_length=40, blank=True, default="")
    error = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["session", "-created_at"], name="tc_recsave_sess_idx")]

    def __str__(self):
        return f"{self.kind} save of session {self.session_id} ({self.status})"
