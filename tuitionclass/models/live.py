# tuitionclass/models/live.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from login.models import User
from django.db import models
from .schedules import ClassSession


# ---------------------------------------------------------------------------
# 6B. BREAKOUT ROOM (host splits a live session into smaller numbered rooms)
#
# FEATURE (backend for live_session_screen.dart's breakout-rooms UI): the
# Flutter side already assumed this exact shape (see that file's header
# comment) — this is the Django side of it. A BreakoutRoom row only exists
# while a breakout is actually running; ClassSessionViewSet.breakout_close
# deletes every row for the session to return everyone to the main room in
# one shot (SessionParticipant.breakout_room below is SET_NULL, so that
# single delete also clears every participant's assigments for free).
# ---------------------------------------------------------------------------
class BreakoutRoom(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="breakout_rooms")
    room_number = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["room_number"]
        unique_together = ("session", "room_number")

    def __str__(self):
        return f"Room {self.room_number} — {self.session}"


# ---------------------------------------------------------------------------
# 6. SESSION PARTICIPANT (attendance / who joined which live session)
# ---------------------------------------------------------------------------
class SessionParticipant(models.Model):
    class Role(models.TextChoices):
        HOST = "host", "Host/Teacher"
        STUDENT = "student", "Student"

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="session_participations")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STUDENT)

    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    # NOTE (fix — moderation had no teeth): kick() in views.py used to only
    # remove the participant from the LiveKit room + mark left_at — nothing
    # stopped that same user from calling /join/ or /token/ again a second
    # later and walking straight back into the room. A teacher removing a
    # disruptive student had no way to actually keep them out. Set once by
    # kick(); checked by join()/token() to refuse re-entry to THIS session
    # (doesn't affect other sessions of the same classroom/schedule).
    kicked_at = models.DateTimeField(null=True, blank=True)

    # FEATURE (trial/demo access): True for a row created by
    # ClassroomViewSet.trial_join (see ClassroomTrialAccess). Trial
    # attendees hold a seat (they count against max_participants) but are
    # NOT enrolled students, so attendance % / report cards / the
    # engagement report's attendee stats skip them.
    is_trial = models.BooleanField(default=False)

    # FEATURE: hand-raise. The only "I have something to say right now"
    # signal in the whole app used to be the async ClassQuery (a written
    # doubt, meant to be answered whenever) — nothing captured the live,
    # in-the-moment "wait, let me ask" gesture every video-call app has.
    # Set/cleared by the student themselves (sessions/{id}/hand/), or
    # cleared by the host after acknowledging
    # (sessions/{id}/hand/{user_id}/lower/). Null = hand down; a timestamp
    # both marks "raised" and gives the host a natural raised-longest-first
    # ordering for the queue.
    hand_raised_at = models.DateTimeField(null=True, blank=True)

    # LIVE GUEST / STAGE: "" = ordinary participant, "invited" = host sent an invite the person has
    # not answered yet, "on_stage" = accepted and currently a guest. Set only through the
    # guest/* actions on ClassSessionViewSet. It is a room state (badge, spotlight, ordering) - the
    # LiveKit token grants are NOT changed by it (every student can already publish).
    class GuestState(models.TextChoices):
        NONE = "", "None"
        INVITED = "invited", "Invited"
        ON_STAGE = "on_stage", "On stage"

    guest_state = models.CharField(max_length=10, choices=GuestState.choices, default="", blank=True)
    guest_at = models.DateTimeField(null=True, blank=True)
    # A guest who is ON STAGE can be made a session co-host by a real moderator (ClassSessionViewSet.guest_cohost):
    # chat delete/pin/unpin, mute, lower hand, slow-mode exempt - see views/access.py::_can_cohost_moderate.
    # NOT end session / kick / record / invite / breakouts. Cleared when the guest leaves the stage.
    guest_cohost = models.BooleanField(default=False)

    # X12 SAFETY: the participant saw the "this class is being recorded" notice (POST sessions/<id>/recording-notice/ack/).
    # Stamped once; a rejoin keeps it. A MINOR who has not acknowledged cannot publish mic/camera while a recording runs
    # (live_safety.recording_blocks_publish).
    recording_notice_ack_at = models.DateTimeField(null=True, blank=True)
    # X12 SAFETY: a moderator took this person's CHAT voice away until this time (they can still watch and hear).
    # Checked in ChatMessageViewSet.perform_create. Auto-set by the strike rule in live_abuse.py as well.
    chat_muted_until = models.DateTimeField(null=True, blank=True)
    # FEATURE: breakout rooms. Null = currently in the main room (either no
    # breakout is running at all, or this participant hasn't been assigned
    # to a sub-room yet). Set/cleared by
    # ClassSessionViewSet.breakout_assign; also cleared in bulk whenever the
    # BreakoutRoom it points to is deleted (breakout_close, or the session
    # itself being torn down) since that's on_delete=SET_NULL.
    breakout_room = models.ForeignKey(
        BreakoutRoom, on_delete=models.SET_NULL, null=True, blank=True, related_name="participants"
    )

    class Meta:
        unique_together = ("session", "user", "joined_at")
        ordering = ["joined_at"]

    def __str__(self):
        return f"{self.user} in {self.session} ({self.role})"


# ---------------------------------------------------------------------------
# X12 SAFETY — LIVE MODERATION LOG. Append-only trail of everything safety-related that happens in ONE live session:
# a report filed from the room, a warning, a chat mute, a kick, a classroom ban, a word-filter block, an automatic
# strike mute, a minor-safety guard, the recording notice. It is the classroom moderators' queue (kind=report) AND the
# audit trail (who did what to whom, when). Platform-wide reports stay in user_profile.ContentReport - a report row
# here only points at it (`content_report_id`, a plain integer so the two apps stay decoupled).
# ---------------------------------------------------------------------------
class LiveModerationEvent(models.Model):
    class Kind(models.TextChoices):
        REPORT = "report", "Report filed"
        WARN = "warn", "Warning"
        CHAT_MUTE = "chat_mute", "Chat muted"
        CHAT_UNMUTE = "chat_unmute", "Chat unmuted"
        KICK = "kick", "Removed from session"
        BAN = "ban", "Banned from classroom"
        FILTER_BLOCK = "filter_block", "Message blocked by word filter"
        FILTER_MASK = "filter_mask", "Message masked by word filter"
        AUTO_MUTE = "auto_mute", "Automatic chat mute (strikes)"
        MINOR_GUARD = "minor_guard", "Minor-safety rule applied"
        DISMISS = "dismiss", "Report dismissed"

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="moderation_events")
    kind = models.CharField(max_length=14, choices=Kind.choices, db_index=True)
    # Who did it: the reporter (report), the moderator (warn / mute / kick / ban / dismiss), NULL = the system.
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # Who it is about. NULL for a report on the session as a whole.
    target_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="live_moderation_events"
    )
    # Short machine word (a report reason like "harassment", a filter reason like "profanity", a rule like "minor_contact").
    reason = models.CharField(max_length=40, blank=True, default="")
    detail = models.CharField(max_length=255, blank=True, default="")
    chat_message_id = models.PositiveIntegerField(null=True, blank=True)
    content_report_id = models.PositiveIntegerField(null=True, blank=True)
    # Only meaningful for kind=report: the moderators' to-do state.
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    resolution = models.CharField(max_length=14, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["session", "-created_at"], name="tc_livemod_session_idx"),
            models.Index(fields=["session", "kind", "resolved_at"], name="tc_livemod_queue_idx"),
            models.Index(fields=["target_user", "kind"], name="tc_livemod_target_idx"),
        ]

    def __str__(self):
        return f"{self.kind} @ session {self.session_id} -> {self.target_user_id}"
