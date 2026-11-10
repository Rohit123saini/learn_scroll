# tuitionclass/models/schedules.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

import logging
import uuid
import zoneinfo
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
)
from datetime import datetime as _datetime
from datetime import timezone as _dt_timezone
from datetime import timedelta as _timedelta
from django.db import models
from django.utils import timezone
from .classroom import Classroom


# ---------------------------------------------------------------------------
# 2. SCHEDULE (recurrence rule -> generates ClassSession rows)
# ---------------------------------------------------------------------------
class ClassSchedule(models.Model):
    class RecurrenceType(models.TextChoices):
        SPECIFIC_DATE = "specific_date", "Specific Date"
        DAILY = "daily", "Daily"
        WEEKDAY = "weekday", "Weekdays (Mon-Fri)"
        WEEKEND = "weekend", "Weekends (Sat-Sun)"
        WEEKLY = "weekly", "Weekly (custom days)"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="schedules")

    recurrence_type = models.CharField(max_length=20, choices=RecurrenceType.choices)

    # Canonical, lowercase 3-letter weekday codes — the ONLY values
    # days_of_week is allowed to contain. Single source of truth shared by
    # ClassScheduleSerializer.validate() (normalizes + validates incoming
    # data) and tasks.py's _WEEKDAY_CODE lookup (matches recurrence dates
    # against it). Both sides MUST agree on this exact casing/format, or a
    # WEEKLY schedule silently generates zero sessions forever with no
    # error anywhere (see tasks.py NOTE (fix) on _dates_for_schedule) —
    # that's what having two independently-typed copies of this list
    # previously caused.
    WEEKDAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

    # used when recurrence_type == WEEKLY (custom day picker), e.g. ["mon","wed","fri"]
    days_of_week = models.JSONField(default=list, blank=True)

    # used when recurrence_type == MONTHLY, e.g. day-of-month 1-31
    day_of_month = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(31)]
    )

    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)  # null = runs indefinitely

    start_time = models.TimeField()
    duration_minutes = models.PositiveIntegerField(default=60)

    timezone = models.CharField(max_length=50, default="Asia/Kolkata")

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date", "start_time"]

    def __str__(self):
        return f"{self.classroom.title} - {self.get_recurrence_type_display()}"

    # ------------------------------------------------------------------
    # TASK 10.1 — DST-safe wall-clock -> absolute-instant conversion.
    #
    # A schedule is "18:00 on this date IN `self.timezone`", i.e. a wall
    # clock time, not an instant. Everything that turns it into a
    # ClassSession (tasks.generate_upcoming_sessions) or into a "next
    # class" instant for clients (next_occurrence_start below) goes
    # through these two helpers so there is exactly one place that knows
    # how to do it correctly:
    #   * the zone is resolved via zoneinfo (real IANA/DST rules), with a
    #     fallback to the project default for a corrupt/legacy name;
    #   * the instant is converted to UTC BEFORE any duration is added.
    #     Adding a timedelta to a zone-aware datetime is wall-clock
    #     arithmetic in Python, so a 60-min class that straddles a DST
    #     change used to come out 0 or 120 min long.
    #   * a wall time that does not exist (spring-forward gap) resolves to
    #     the same absolute instant zoneinfo picks for it (fold=0), which
    #     is the usual "shift forward by the gap" behaviour; an ambiguous
    #     time (fall-back) resolves to its first occurrence.
    # ------------------------------------------------------------------
    def get_tzinfo(self):
        try:
            return zoneinfo.ZoneInfo(self.timezone)
        except Exception:
            logging.getLogger("tuitionclass.models").warning(
                "ClassSchedule %s has invalid timezone %r — falling back to project default.",
                self.pk, self.timezone,
            )
            return timezone.get_default_timezone()

    def occurrence_bounds(self, d):
        """(start_utc, end_utc) — both timezone-aware UTC datetimes — for
        the occurrence of this schedule on calendar date `d` (a date in
        `self.timezone`)."""
        tz = self.get_tzinfo()
        local_start = _datetime.combine(d, self.start_time).replace(tzinfo=tz)
        start_utc = local_start.astimezone(_dt_timezone.utc)
        end_utc = start_utc + _timedelta(minutes=self.duration_minutes)
        return start_utc, end_utc

    def today_in_schedule_tz(self, now=None):
        now = now or timezone.now()
        return now.astimezone(self.get_tzinfo()).date()

    def next_occurrence_start(self, now=None):
        """UTC instant of the next not-yet-started occurrence (holidays
        skipped), or None for an inactive schedule / nothing upcoming.
        Used so clients never have to re-implement recurrence + DST."""
        if not self.is_active:
            return None
        from ..tasks import _dates_for_schedule  # local: tasks imports models lazily too

        now = now or timezone.now()
        first_day = self.today_in_schedule_tz(now)
        horizon = 400 if self.recurrence_type == self.RecurrenceType.YEARLY else 62
        for d in _dates_for_schedule(self, first_day, first_day + _timedelta(days=horizon)):
            start_utc, _end = self.occurrence_bounds(d)
            if start_utc <= now:
                continue
            if self.is_off_on(d):
                continue
            return start_utc
        return None

    def is_off_on(self, date) -> bool:
        """True if `date` is marked as a holiday for this schedule specifically,
        or classroom-wide (ClassHoliday.schedule is null = applies to every
        schedule). Whatever job generates ClassSession rows from this
        recurrence rule should call this and skip the date if True."""
        return ClassHoliday.objects.filter(classroom_id=self.classroom_id, date=date).filter(
            models.Q(schedule_id=self.id) | models.Q(schedule__isnull=True)
        ).exists()


# ---------------------------------------------------------------------------
# 3. SESSION (an actual, joinable tuition-class occurrence)
# ---------------------------------------------------------------------------
class ClassSession(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        LIVE = "live", "Live"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="sessions")
    schedule = models.ForeignKey(
        ClassSchedule, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions"
    )

    room_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()

    actual_start = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)

    recording_url = models.URLField(blank=True)
    # FEATURE: recording engine wiring. `recording_url` above already
    # existed as a place to STORE the final link, and Classroom.recording_enabled
    # already exists as a per-classroom toggle — but nothing anywhere ever
    # called LiveKit's Egress API, so recording_url could never actually get
    # populated; the toggle was a setting with no engine behind it. This
    # holds the in-flight LiveKit egress job's ID between start_recording()
    # and stop_recording() (see views.py) — needed to call stop_egress()
    # later, and to match an incoming `egress_ended` webhook (see
    # LiveKitWebhookView) back to the right session so recording_url gets
    # filled in once the file finishes uploading. Blank = not currently
    # recording.
    egress_id = models.CharField(max_length=64, blank=True)
    # X12: the egress that produced / is producing `recording_url`. `egress_id` above is CLEARED the moment a teacher presses
    # Stop (so the REC badge goes off), which left the `egress_ended` webhook with nothing to match - the file's link and
    # length never reached the session after a manual stop. This one is never cleared; the webhook matches on either.
    last_egress_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    # X12: when the CURRENT recording began (stamped by start-recording / the auto-start / the LiveKit
    # `egress_started` webhook). Every SessionChapter offset, replay-chat offset and the "peak viewers at" time are
    # measured from here, so they line up with the video's own timeline. Null = no recording was ever started.
    recording_started_at = models.DateTimeField(null=True, blank=True)
    # X12: stamped the first time the auto-start tried (success or not) so a webhook retry / every later
    # `participant_joined` can never start a second egress, and a teacher who stopped the recording by hand
    # is not recorded again behind their back.
    auto_record_attempted_at = models.DateTimeField(null=True, blank=True)
    # X12: length of the finished file in seconds (from the egress_ended webhook; 0/null = unknown -> the save screen
    # falls back to actual_end - recording_started_at). Used to bound the trim range of "Save as lecture/reel/post".
    recording_duration_seconds = models.PositiveIntegerField(null=True, blank=True)

    # NOTE (fix — whiteboard/spotlight persistence): whiteboard_snapshot
    # already existed on this model but nothing ever read/wrote it — the
    # whiteboard lived only in each connected client's memory, synced
    # peer-to-peer over the LiveKit data channel. That's fine for a
    # brand-new joiner (they get caught up peer-to-peer by whoever's
    # already in the room), but breaks the moment nobody left in the room
    # still holds the strokes — e.g. everyone disconnects and the host
    # reconnects alone, or a student joins before anyone with the current
    # board has (re)joined. spotlight_identity is the same story for the
    # host's pinned/spotlighted tile. Both are now written by dedicated
    # actions on ClassSessionViewSet (whiteboard()/spotlight() in
    # views.py) instead of the generic PATCH, so a non-host participant
    # can still autosave whiteboard strokes without needing full
    # classroom-manage permissions. See those actions' docstrings for the
    # permission split.
    whiteboard_snapshot = models.JSONField(null=True, blank=True)
    spotlight_identity = models.CharField(max_length=64, blank=True)  # LiveKit identity (str(user_id)) of the pinned tile, "" = none

    # FEATURE (captions — see tasks.transcribe_recording/poll_transcription_job):
    # once recording_url is filled in by the egress webhook above, and
    # Classroom.captions_enabled is on, a transcription job is queued
    # against that file. This holds the job's state/result — provider is
    # AWS Transcribe (see tasks.py); transcription_job_name below tracks
    # the specific in-flight job.
    class CaptionStatus(models.TextChoices):
        NONE = "none", "Not Requested"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    caption_status = models.CharField(max_length=12, choices=CaptionStatus.choices, default=CaptionStatus.NONE)
    caption_url = models.URLField(blank=True)  # points at a .vtt/.srt file once READY
    # NOTE (fix — captions wiring, item 3): AWS Transcribe job name for
    # the in-flight transcription job started by tasks.transcribe_recording
    # — needed so the periodic tasks.poll_transcription_job can look up
    # job status later (job name includes a random suffix, so it can't be
    # re-derived from session_id alone once the job's already running).
    # Blank = no transcription job currently in flight for this session.
    transcription_job_name = models.CharField(max_length=128, blank=True)

    # NEW (Pass 14 — post-session engagement report). Computed once by
    # tasks.build_engagement_report (queued from cleanup_on_session_end's
    # on_commit hook in signals.py, alongside the existing LiveKit-teardown/
    # poll-close/participant-checkout steps) the moment a session reaches
    # COMPLETED, then just read back by ClassSessionViewSet.engagement_report
    # — never recomputed per-request, so a teacher re-opening the report a
    # dozen times doesn't re-run five aggregate queries a dozen times.
    # Null until that task runs (or for a session that never went LIVE).
    # See ClassSession.compute_engagement_report() below for the shape.
    engagement_report = models.JSONField(null=True, blank=True)

    # LV1 (Live in story tray) — presence + notify guard.
    # `live_heartbeat_at`: stamped when the session goes LIVE, on every host
    # /join/ + /token/, and by the host's app every ~20s via
    # ClassSessionViewSet.heartbeat(). A LIVE session whose heartbeat is older
    # than LIVE_HEARTBEAT_TTL_SECONDS (tuitionclass/live_presence.py) is
    # treated as NOT live by the story tray / live-now row, so a crashed host
    # app can't leave a ghost "LIVE" ring behind. NULL = never heartbeated.
    live_heartbeat_at = models.DateTimeField(null=True, blank=True)
    # `live_notified_at`: set atomically by notify_session_live — the guard
    # that makes "X is live" go out ONCE per session, however many times the
    # host re-joins / the task is retried.
    live_notified_at = models.DateTimeField(null=True, blank=True)

    # LIVE-ROOM TOOLS (slow mode): minimum gap in seconds between two chat messages from the same
    # NON-moderator participant. 0 = off. Set only through ClassSessionViewSet.slow_mode (moderator
    # gate) and enforced in ChatMessageViewSet.perform_create (views/live_chat.py).
    chat_slow_mode_seconds = models.PositiveSmallIntegerField(default=0)

    # X12 SAFETY (live profanity filter): how this session's chat treats a message that trips the word filter.
    #   "" = use settings.LIVE_CHAT_FILTER_DEFAULT ("mask"), "off" = never touch, "flag" = post as-is but queue for review
    #   (the old behaviour), "mask" = swear words become ****, "block" = message refused. Set only through
    #   live_moderation_views.SessionChatFilterView (host tier).
    class ChatFilterMode(models.TextChoices):
        DEFAULT = "", "Default"
        OFF = "off", "Off"
        FLAG = "flag", "Flag only"
        MASK = "mask", "Mask bad words"
        BLOCK = "block", "Block message"

    chat_filter_mode = models.CharField(max_length=8, choices=ChatFilterMode.choices, default="", blank=True)

    # LIVE-ROOM TOOLS (stage-only / listener mode): when True a plain student's LiveKit token has
    # can_publish=False - only the host tier and guests who are ON STAGE can send mic/camera.
    # Default False = old behaviour (every student can publish). Set only through
    # ClassSessionViewSet.stage_only; guest accept/remove flips the live permission of that guest.
    stage_only = models.BooleanField(default=False)

    # LIVE-ROOM TOOLS (present PDF/slides): the material the host is showing the whole room right
    # now (None = nothing presented), the page everyone should be on, and the page count the host's
    # device measured when it started presenting. Written only through ClassSessionViewSet.present /
    # present_page / present_stop (moderator gate) so a late joiner can read them off the session GET.
    presented_material = models.ForeignKey(
        "tuitionclass.ClassMaterial", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    presented_page = models.PositiveIntegerField(default=1)
    presented_page_count = models.PositiveIntegerField(default=0)

    # X12 L-C (standalone "Go Live" - tuitionclass/standalone_live.py). A normal ClassSession of the host's hidden
    # personal classroom, plus what the go-live sheet asked for. `live_standalone=False` = an ordinary class session
    # (every field below stays at its default and nothing reads it).
    class LiveAudience(models.TextChoices):
        CLASS = "class", "Class members only"
        FOLLOWERS = "followers", "Followers"
        PUBLIC = "public", "Public"
        PASS = "pass", "Pass holders"

    live_standalone = models.BooleanField(default=False, db_index=True)
    # True = "Go live now" (created the moment the host opened the green room). Such a row that was never started is
    # not an announcement: it stays out of the upcoming feed and is swept when the host starts the next one.
    live_go_now = models.BooleanField(default=False)
    live_audience = models.CharField(max_length=10, choices=LiveAudience.choices, default="class")
    live_title = models.CharField(max_length=150, blank=True, default="")
    live_topic = models.CharField(max_length=120, blank=True, default="")  # subject / topic line under the title
    live_thumbnail = models.ImageField(upload_to="live_thumbnails/", null=True, blank=True)
    live_timezone = models.CharField(max_length=50, default="Asia/Kolkata")  # the host's zone, for display only
    # Set once by standalone_live.send_due_reminders (conditional UPDATE) -> "starts in N minutes" goes out once.
    reminder_sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scheduled_start"]
        indexes = [
            models.Index(fields=["classroom", "status", "scheduled_start"]),
            models.Index(fields=["status", "live_heartbeat_at"], name="cs_live_hb_idx"),
        ]

    def __str__(self):
        return f"{self.classroom.title} @ {self.scheduled_start:%d-%b-%Y %H:%M}"

    def is_joinable(self, is_host: bool = False) -> bool:
        """Allow joining as long as the session itself is still a live
        occurrence (not explicitly ended/cancelled).

        NOTE (fix — time-window entry restriction removed): this used to
        block a student from entering more than 10 minutes before
        scheduled_start or after scheduled_end, and only exempted the
        host/staff from that window. Product decision: everyone should be
        able to open the room at any time while the session is still
        SCHEDULED/LIVE — a student arriving early or a bit late should
        still get in, not a "not joinable right now" wall. The actual
        scheduled_start/scheduled_end are still sent to the client on
        every session payload (ClassSessionSerializer), so the frontend
        can show "Session starts at <local time>" / "Scheduled to end at
        <local time>" as an informational banner instead of a hard block.
        `is_host` is kept as a parameter (unused now) so existing call
        sites (_perform_join, ClassSessionViewSet.join()) don't need to
        change their calls.
        """
        return self.status in (self.Status.SCHEDULED, self.Status.LIVE)

    def compute_engagement_report(self) -> dict:
        """Aggregate, don't join-explode: each metric is its own small
        query against a table indexed on session_id (participants/
        chat_messages/live_polls all already carry that index — see each
        model's Meta) rather than one giant multi-table join, so this
        stays cheap even for a long-running session with thousands of
        chat rows. Pure computation, no save — see build_engagement_report
        in tasks.py for who calls this and persists the result onto
        `engagement_report`.
        """
        participants = list(
            self.participants.filter(role=SessionParticipant.Role.STUDENT, is_trial=False)
        )
        attendee_count = len(participants)
        trial_attendee_count = self.participants.filter(is_trial=True).values("user_id").distinct().count()

        durations = []
        for p in participants:
            ended = p.left_at or self.actual_end or timezone.now()
            if p.joined_at and ended > p.joined_at:
                durations.append((ended - p.joined_at).total_seconds())
        avg_watch_seconds = int(sum(durations) / len(durations)) if durations else 0

        scheduled_seconds = max((self.scheduled_end - self.scheduled_start).total_seconds(), 1)

        chat_qs = self.chat_messages.filter(is_deleted=False)
        chat_message_count = chat_qs.count()
        distinct_chatters = chat_qs.values("sender_id").distinct().count()

        polls = list(self.polls.all())
        poll_count = len(polls)
        total_poll_responses = PollResponse.objects.filter(poll__session=self).count()
        avg_responses_per_poll = round(total_poll_responses / poll_count, 1) if poll_count else 0

        hands_raised = SessionParticipant.objects.filter(session=self, hand_raised_at__isnull=False).count()

        return {
            "attendee_count": attendee_count,
            "trial_attendee_count": trial_attendee_count,
            "avg_watch_seconds": avg_watch_seconds,
            "avg_watch_percent": round(min(avg_watch_seconds / scheduled_seconds, 1.0) * 100, 1),
            "chat_message_count": chat_message_count,
            "distinct_chatters": distinct_chatters,
            "poll_count": poll_count,
            "total_poll_responses": total_poll_responses,
            "avg_responses_per_poll": avg_responses_per_poll,
            "hands_raised_count": hands_raised,
            "computed_at": timezone.now().isoformat(),
        }



# Forward references: used only inside method/function bodies, defined in a LATER module. Bound by models/__init__.py
# once every module is loaded (see _link_forward_refs there). `None` here only keeps linters quiet.
ClassHoliday = None  # -> .notices_queries
PollResponse = None  # -> .polls
SessionParticipant = None  # -> .live
