"""
social_learning/models.py  (P-F5 - Social learning)

Three features, one app, no change to existing tables:

  1. Study-with-me focus rooms  : PomodoroTimer / PomodoroRound / PomodoroCompletion
       - the timer is owned by the SERVER (one row per study-room conversation). Clients only render it from
         `phase_ends_at` + server time, so a late joiner / a reconnect / a phone with a wrong clock all see the same
         countdown. Presence itself already exists (message.StudyRoomPresence, S5) - credit for a pomodoro is checked
         against it.
  2. Accountability partner     : AccountabilityPair / AccountabilityNudge / AccountabilityMilestone / ManualStudyLog
  3. Leaderboard seasons        : StudySeason / SeasonStanding / SeasonReward  (rewards are NON-CASH: badge + streak freeze)

  SocialPrefs = the per-user switches (hide me from seasons, no partner requests, no partner reminders).
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class SocialPrefs(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="social_prefs")
    season_opt_out = models.BooleanField(default=False)          # not shown on any season board, earns no season reward
    accepts_partner_requests = models.BooleanField(default=True)
    partner_reminders = models.BooleanField(default=True)        # evening "your partner is done" push
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"SocialPrefs({self.user_id})"


# ======================================================================================================================
# 1. Pomodoro
# ======================================================================================================================
class PomodoroTimer(models.Model):
    class Phase(models.TextChoices):
        IDLE = "idle", "Idle"
        FOCUS = "focus", "Focus"
        SHORT_BREAK = "short_break", "Short break"
        LONG_BREAK = "long_break", "Long break"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.OneToOneField("message.Conversation", on_delete=models.CASCADE, related_name="pomodoro_timer")
    phase = models.CharField(max_length=12, choices=Phase.choices, default=Phase.IDLE)
    is_running = models.BooleanField(default=False, db_index=True)
    phase_started_at = models.DateTimeField(null=True, blank=True)
    phase_ends_at = models.DateTimeField(null=True, blank=True)
    # set while paused (is_running False, phase != idle): resume => phase_ends_at = now + this
    paused_remaining_seconds = models.PositiveIntegerField(null=True, blank=True)
    focus_minutes = models.PositiveSmallIntegerField(default=25)
    short_break_minutes = models.PositiveSmallIntegerField(default=5)
    long_break_minutes = models.PositiveSmallIntegerField(default=15)
    cycles_before_long = models.PositiveSmallIntegerField(default=4)
    cycle_index = models.PositiveSmallIntegerField(default=0)    # focus rounds finished in the current set
    version = models.PositiveIntegerField(default=0)             # +1 on every change; clients ignore older versions
    current_round = models.ForeignKey("PomodoroRound", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PomodoroTimer({self.conversation_id}, {self.phase}, running={self.is_running})"


class PomodoroRound(models.Model):
    """One phase (a focus block or a break) that really started. A focus round that reached its natural end
    (`completed=True`) is what members claim credit for."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timer = models.ForeignKey(PomodoroTimer, on_delete=models.CASCADE, related_name="rounds")
    kind = models.CharField(max_length=12, choices=PomodoroTimer.Phase.choices)
    started_at = models.DateTimeField()
    planned_seconds = models.PositiveIntegerField()
    ended_at = models.DateTimeField(null=True, blank=True)
    completed = models.BooleanField(default=False)               # reached its planned end (not skipped / stopped)

    class Meta:
        indexes = [models.Index(fields=["timer", "-started_at"], name="sl_round_timer_started_idx")]

    def __str__(self):
        return f"{self.kind} {self.started_at:%Y-%m-%d %H:%M} ({'done' if self.completed else 'open/ended early'})"


class PomodoroCompletion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pomodoro_completions")
    round = models.ForeignKey(PomodoroRound, on_delete=models.CASCADE, related_name="completions")
    conversation = models.ForeignKey("message.Conversation", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    minutes = models.PositiveSmallIntegerField()
    completed_at = models.DateTimeField(default=timezone.now)
    local_date = models.DateField(db_index=True)                 # IST day it was credited on

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "round"], name="sl_pomodoro_once_per_round")]
        indexes = [models.Index(fields=["user", "local_date"], name="sl_pomo_user_date_idx")]


# ======================================================================================================================
# 2. Accountability partner
# ======================================================================================================================
class AccountabilityPair(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        DECLINED = "declined", "Declined"
        ENDED = "ended", "Ended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="partner_requests_sent")
    partner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="partner_requests_received")
    # "<smaller user id>:<larger user id>" - one live pair per two people whoever asked first
    pair_key = models.CharField(max_length=80, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    daily_goal_minutes = models.PositiveSmallIntegerField(default=30)
    created_at = models.DateTimeField(default=timezone.now)
    responded_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    best_streak = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["pair_key"],
                condition=Q(status__in=["pending", "active"]),
                name="sl_one_live_pair_per_two_users",
            ),
        ]
        indexes = [
            models.Index(fields=["requester", "status"], name="sl_pair_req_status_idx"),
            models.Index(fields=["partner", "status"], name="sl_pair_par_status_idx"),
        ]

    def other_user_id(self, user_id):
        return self.partner_id if str(self.requester_id) == str(user_id) else self.requester_id

    def includes(self, user_id) -> bool:
        return str(user_id) in (str(self.requester_id), str(self.partner_id))


class ManualStudyLog(models.Model):
    """"I studied offline today" minutes (capped per day). Counts for the partner goal only - never for seasons,
    because nothing verifies it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="manual_study_logs")
    date = models.DateField()
    minutes = models.PositiveSmallIntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="sl_manual_log_per_day")]


class AccountabilityNudge(models.Model):
    """A fixed-template reminder (no free text, so it cannot be used to harass)."""

    class Kind(models.TextChoices):
        MANUAL = "manual", "Manual"
        AUTO = "auto", "Auto evening reminder"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pair = models.ForeignKey(AccountabilityPair, on_delete=models.CASCADE, related_name="nudges")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.MANUAL)
    template = models.CharField(max_length=24, default="keep_going")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["pair", "kind", "created_at"], name="sl_nudge_pair_kind_idx")]


class AccountabilityMilestone(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pair = models.ForeignKey(AccountabilityPair, on_delete=models.CASCADE, related_name="milestones")
    days = models.PositiveSmallIntegerField()
    granted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pair", "days"], name="sl_pair_milestone_once")]


# ======================================================================================================================
# 3. Seasons
# ======================================================================================================================
class StudySeason(models.Model):
    class Status(models.TextChoices):
        UPCOMING = "upcoming", "Upcoming"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.PositiveIntegerField(unique=True)
    title = models.CharField(max_length=80)
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UPCOMING, db_index=True)
    # scoring / reward overrides, see seasons.DEFAULT_CONFIG (unknown keys are ignored)
    config = models.JSONField(default=dict, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    # Incremental scoring cache (seasons.py): days BEFORE `rollup_through` are already summed into SeasonUserTotal, so a
    # refresh only scans the last day or two of attendance. `rollup_sig` = the scoring numbers the totals were built with;
    # if config changes mid-season the cache is dropped and rebuilt.
    rollup_through = models.DateField(null=True, blank=True)
    rollup_sig = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        ordering = ["-starts_at"]
        constraints = [models.CheckConstraint(condition=Q(ends_at__gt=models.F("starts_at")), name="sl_season_ends_after_start")]

    def __str__(self):
        return f"Season {self.number} ({self.status})"


class SeasonUserTotal(models.Model):
    """Cache: one user's counted numbers for the days of a season that are already frozen (see StudySeason.rollup_through).
    Derived data - safe to delete (it is rebuilt from attendance); the final standings never depend on it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    season = models.ForeignKey(StudySeason, on_delete=models.CASCADE, related_name="rollup_totals")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    minutes = models.PositiveIntegerField(default=0)             # counted minutes (after the per-day cap)
    pomodoros = models.PositiveIntegerField(default=0)
    active_days = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["season", "user"], name="sl_one_rollup_per_user")]


class SeasonStanding(models.Model):
    """One row per (season, user). Provisional while the season runs (rewritten every refresh), frozen when it closes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    season = models.ForeignKey(StudySeason, on_delete=models.CASCADE, related_name="standings")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="season_standings")
    score = models.PositiveIntegerField(default=0)
    rank = models.PositiveIntegerField()
    minutes = models.PositiveIntegerField(default=0)             # counted minutes (after the per-day cap)
    pomodoros = models.PositiveIntegerField(default=0)
    active_days = models.PositiveSmallIntegerField(default=0)
    is_final = models.BooleanField(default=False)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["season", "user"], name="sl_one_standing_per_user")]
        indexes = [models.Index(fields=["season", "rank"], name="sl_standing_rank_idx")]
        ordering = ["rank"]


class SeasonReward(models.Model):
    """What a user got when a season closed. NON-CASH by design: a badge and/or streak-freeze tokens."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    season = models.ForeignKey(StudySeason, on_delete=models.CASCADE, related_name="rewards")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="season_rewards")
    tier = models.CharField(max_length=16)
    rank = models.PositiveIntegerField()
    score = models.PositiveIntegerField(default=0)
    badge_code = models.CharField(max_length=50, blank=True, default="")
    freezes_requested = models.PositiveSmallIntegerField(default=0)
    freezes_granted = models.PositiveSmallIntegerField(default=0)   # less than requested when the user is at the freeze cap
    granted_at = models.DateTimeField(default=timezone.now)
    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["season", "user"], name="sl_one_reward_per_season_user")]
        indexes = [models.Index(fields=["user", "-granted_at"], name="sl_reward_user_idx")]
