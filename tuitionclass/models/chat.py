# tuitionclass/models/chat.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from login.models import User
from django.db import models
from .schedules import ClassSession


# ---------------------------------------------------------------------------
# 8. LIVE CHAT (persisted messages during a session)
# ---------------------------------------------------------------------------
class ChatMessage(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="chat_messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_messages")

    message = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)  # soft-delete for moderation

    # NEW (Pass 13) — pin a chat message/announcement so it doesn't get
    # lost in scroll. Deliberately ONE pinned message per session, not a
    # list: `ChatMessageViewSet.pin()` unpins any previously-pinned
    # message for the same session in the same call, so `is_pinned=True`
    # is never ambiguous about which one is "the" pinned message — a
    # `unique_together`/partial-unique-index couldn't express "at most
    # one True per session" portably across DB backends, so this is
    # enforced in the view instead (see its docstring).
    is_pinned = models.BooleanField(default=False)
    pinned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="pinned_chat_messages"
    )
    pinned_at = models.DateTimeField(null=True, blank=True)

    # NEW (Pass 14 — chat safety: reports + profanity filter). See
    # moderation.py's `screen_message()` for the actual word-list check —
    # kept out of this file so the model stays free of moderation policy.
    # `is_flagged` is set automatically the moment a message trips the
    # filter (ChatMessageViewSet.perform_create) OR the moment a report
    # against it is filed (ChatMessageReportViewSet.create, see below) —
    # either path makes the message show up in a moderator's flagged-chat
    # queue without a moderator having to know which trigger fired.
    is_flagged = models.BooleanField(default=False)
    flagged_reason = models.CharField(max_length=100, blank=True)

    # NEW (reply feature) — one-level "reply/quote" reference, same shape as
    # WhatsApp's reply-to rather than a full nested-thread tree: a message
    # can quote exactly one earlier message in the SAME session (enforced in
    # ChatMessageSerializer.validate, since that needs the in-flight
    # `session` value from attrs, not just `self.instance`). SET_NULL (not
    # CASCADE) so deleting/soft-deleting the original doesn't take the reply
    # down with it — the quote just renders as "original message deleted"
    # (see ChatMessageSerializer.get_reply_to_detail). `related_name="replies"`
    # lets a message look up everything that quoted it, if that's ever needed
    # for a "view thread" UI, without a separate join table.
    reply_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
    )

    class Meta:
        ordering = ["sent_at"]
        indexes = [models.Index(fields=["session", "sent_at"]), models.Index(fields=["is_flagged"])]

    def __str__(self):
        return f"{self.sender}: {self.message[:30]}"


# ---------------------------------------------------------------------------
# NEW (Pass 14 — per-message chat reports). One row per (message, reporter)
# report — a student flagging one message as abusive/spam/off-topic for a
# moderator to review, same "queue + review action" shape as
# ClassroomReport (see ClassroomReportViewSet.review in views.py), just
# scoped to a single chat message instead of a whole classroom.
# ---------------------------------------------------------------------------
class ChatMessageReport(models.Model):
    class Reason(models.TextChoices):
        ABUSIVE = "abusive", "Abusive / Harassment"
        SPAM = "spam", "Spam / Advertising"
        OFF_TOPIC = "off_topic", "Off-topic"
        INAPPROPRIATE = "inappropriate", "Inappropriate Content"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIONED = "actioned", "Actioned"  # moderator deleted the message / warned the sender
        DISMISSED = "dismissed", "Dismissed"

    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_reports_filed")
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.OTHER)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="chat_reports_reviewed"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # One report per (message, reporter) — same "can't spam-report the
        # same message to inflate the queue" reasoning as ChatReaction's
        # unique_together above, re-filing is a PATCH-style upsert, not a
        # new row (see ChatMessageReportViewSet.create).
        unique_together = ("message", "reporter")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"Report on message {self.message_id} by {self.reporter} ({self.get_reason_display()})"


class ChatReaction(models.Model):
    """NEW (Pass 12) — lightweight emoji reaction on a chat message. Kept
    deliberately separate from ChatMessage itself (not e.g. a JSONField
    counter on the message row) so a reaction can be added/changed/removed
    without ever touching — or needing a lock on — the message row, and so
    `unique_together` can cheaply enforce "one reaction per user per
    message" at the DB level instead of in application code.

    ONE reaction per (message, user), upsertable — matches the same
    "vote can be changed" shape as PollResponse/LivePollViewSet.vote()
    above (update_or_create), not a Slack-style "many different emoji per
    user" model. This app's chat is a lightweight engagement signal, not
    a full reaction system — deliberately the simplest version that still
    lets a student's tap reflect their latest reaction rather than
    silently failing on the unique constraint or stacking duplicates.
    """

    class Reaction(models.TextChoices):
        THUMBS_UP = "thumbs_up", "👍"
        HEART = "heart", "❤️"
        LAUGH = "laugh", "😂"

    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_reactions")
    reaction = models.CharField(max_length=10, choices=Reaction.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("message", "user")
        indexes = [models.Index(fields=["message"])]

    def __str__(self):
        return f"{self.user} reacted {self.reaction} to message {self.message_id}"


class ChatMessageRead(models.Model):
    """NEW (read receipts) — WhatsApp-style "seen by" for live-session chat.

    Deliberately its own row-per-(message, user) table, same reasoning as
    `ChatReaction` right above: a receipt can be recorded without touching or
    locking the `ChatMessage` row itself, and `unique_together` gives us "one
    receipt per user per message" for free at the DB level instead of an
    application-level check-then-write race.

    This is distinct from `SessionReadState.last_read_chat_message_id`
    elsewhere in this file: that field is a single per-user *watermark* used
    only to drive the unread-count BADGE on the sessions list (see
    `ClassSessionViewSet.unread_counts`/`mark_read`) — it has no idea who
    else has read a given message. This table is the other direction: given
    ONE message, who (and when) has actually seen it — what a sender taps a
    message to check, same as WhatsApp/Slack read receipts. The two features
    share the same underlying "user has seen up to here" idea but serve
    different UI (a badge vs. a per-message seen-by list) and are kept as
    separate tables so neither has to shoehorn the other's access pattern.

    Append/upsert-only — a read receipt never gets un-set (you can't
    "un-read" a message), so unlike `ChatReaction` there is no DELETE path.
    """

    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chat_message_reads")
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("message", "user")
        indexes = [models.Index(fields=["message"])]
        ordering = ["read_at"]

    def __str__(self):
        return f"{self.user} read message {self.message_id} at {self.read_at}"


class SessionReaction(models.Model):
    """NEW (persistence fix) — durable log for the in-session emoji
    reactions (👍❤️😂👏🎉🙌) fired from live_session_screen.dart's
    `_sendReaction`/`_addFloatingReaction`. Previously a PURE LiveKit
    data-channel broadcast (see that file's `_kSignalTopic` signaling) —
    nothing was ever written to the database, so the moment every
    participant left the room the entire reaction history for that
    session was gone, and a client reconnecting mid-session started its
    running `_reactionTotalCount` badge back at zero even though the
    class had already reacted a hundred times.

    Deliberately an APPEND-ONLY log (one row per tap), not a per-user
    upsert like `ChatReaction` above — the point of this feature is the
    burst/stream over time (and a running total), not "what is each
    user's current reaction", so there's no `unique_together` here.
    Live delivery to already-connected peers is UNCHANGED (still the
    LiveKit data channel, for lowest latency) — this table is the
    missing persistence layer underneath it, written by
    `ClassSessionViewSet.reactions()` (POST) in views.py alongside the
    existing data-channel broadcast, not instead of it.
    """

    class Reaction(models.TextChoices):
        THUMBS_UP = "thumbs_up", "👍"
        HEART = "heart", "❤️"
        LAUGH = "laugh", "😂"
        CLAP = "clap", "👏"
        PARTY = "party", "🎉"
        RAISED_HANDS = "raised_hands", "🙌"

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="reaction_log")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="session_reactions")
    reaction = models.CharField(max_length=20, choices=Reaction.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["session", "created_at"])]

    def __str__(self):
        return f"{self.user} reacted {self.reaction} in {self.session}"


class SessionCaption(models.Model):
    """NEW (persistence fix) — durable transcript line for the LIVE
    caption feature (`speech_to_text` on-device STT, see
    live_session_screen.dart's `_startCaptionListenBurst`/
    `_addCaptionLine`). Each device still only ever recognizes ITS OWN
    mic — that on-device-STT limitation is unchanged and is documented
    at length in that file's header (no per-frame hook into remote
    participants' decoded audio in Flutter/LiveKit) — but every device
    already produces its own finished lines and tags them with the
    speaker's name before broadcasting over the LiveKit data channel.
    Previously that broadcast was the ONLY place a line ever went:
    nothing was persisted, the on-screen feed was capped to the last 3
    lines and self-expired after 6 seconds (see `_addCaptionLine`), and
    a participant who joined mid-session or reconnected saw nothing
    that was said before they arrived.

    This table is that missing persistence layer: every device's own
    recognized line is now ALSO POSTed to the server
    (`ClassSessionViewSet.captions()` POST) and kept as a row here,
    which (a) survives the session ending, and (b) lets a client fetch
    the transcript-so-far (`captions()` GET) — so even though the
    recognition itself is still per-own-voice, the aggregate transcript
    the server accumulates covers EVERY participant's own speech, not
    just whoever's LiveKit data-channel packet happened to reach a
    given listener live. A genuinely server-side, cross-participant
    live transcript would need real server-side STT on each
    participant's audio track (a much bigger, separate change — see the
    existing async whole-recording transcription path instead,
    `Classroom.captions_enabled` / `ClassSession.caption_url` /
    `tasks.transcribe_recording`, which already covers the mixed
    recording after the session ends).
    """

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="caption_log")
    speaker = models.ForeignKey(User, on_delete=models.CASCADE, related_name="session_captions")
    text = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["session", "created_at"])]

    def __str__(self):
        return f"{self.speaker}: {self.text[:40]}"


class SessionReadState(models.Model):
    """NEW (Pass 13) — one row per (session, user): the watermark this
    user has "seen up to" for a session's chat and polls. Deliberately
    ONE row covering both, not two separate models — "I reopened this
    session's tab" naturally clears both unread badges together, and a
    single upsert (see ClassSessionViewSet.mark_read below) is cheaper
    than two.

    NULL on either field means "never seen any" (not "seen the first
    one") — so `id > NULL` semantics need an explicit `is None` branch
    in the unread-count query rather than relying on SQL's own NULL
    comparison, which is always UNKNOWN/false and would otherwise (very
    subtly) undercount a first-ever visit as zero unread instead of
    "all of them".
    """

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="read_states")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="session_read_states")
    last_read_chat_message_id = models.PositiveIntegerField(null=True, blank=True)
    last_seen_poll_id = models.PositiveIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("session", "user")

    def __str__(self):
        return f"{self.user} read-state for {self.session}"
