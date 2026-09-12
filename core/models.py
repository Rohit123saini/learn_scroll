"""
core/models.py

Notification and NotificationPreference. Original file's task-42/44/46
history is preserved verbatim below (db_table pinning, id kept as
integer PK, MESSAGE_APP_TYPES, etc.) — that reasoning was already sound
and load-bearing, so nothing about the migration story changes here.

WHAT'S NEW in this pass (production hardening only, no schema-breaking
changes beyond two additive indexes):

1. Added `Notification.objects` = a small custom manager/queryset with
   `.unread()` and `.for_user()`. Every view/serializer that needs "unread
   count for the bell icon" was presumably re-writing
   `Notification.objects.filter(recipient=u, is_read=False).count()`
   inline; centralizing it means the composite index below is the ONLY
   place that query shape needs to be tuned.

2. Added `models.Index(fields=["recipient", "-created_at"])` alongside
   the existing `(recipient, is_read, -created_at)` index. Reason: the
   existing composite index only gives Postgres a fully-sorted run for
   queries that also filter on `is_read`. The very common "show me all
   my notifications, newest first" (no is_read filter — the combined
   feed, not just the unread badge) can't use that index for the ORDER
   BY across different is_read values. This second index covers that
   query shape directly. Two indexes on an append-mostly, read-heavy
   table like this is a normal and cheap trade-off (small write-cost
   increase, no read fallback to seq scan).

3. Added `db_index=True` on `notif_type`. Ops/admin dashboards filtering
   "how many SESSION_LIVE notifications went out today" across all
   recipients weren't covered by the recipient-scoped composite index at
   all — that's a from-scratch index for a different query shape, not a
   duplicate of anything above.

4. Added 10 new NotifType choices for the testseries, assignment, and
   campus-gamification apps: TESTSERIES_POSTED, TESTSERIES_CHECKED,
   TESTSERIES_PAYOUT_RELEASED, ASSIGNMENT_POSTED, ASSIGNMENT_GRADED,
   ASSIGNMENT_DUE_SOON, CAMPUS_REWARD_EARNED, TESTSERIES_REVIEW_RECEIVED,
   TESTSERIES_QUERY_RECEIVED, TESTSERIES_QUERY_ANSWERED. Pure addition —
   no existing choice was renamed or removed, and choices-only changes
   need no migration. NOTE: ASSIGNMENT_POSTED and ASSIGNMENT_GRADED
   already existed above under the task-44/46 block (ASSIGNMENT_POSTED,
   ASSIGNMENT_GRADED) — reused rather than duplicated with a new string,
   since NotifType.values must stay a set of unique choice values. Only
   ASSIGNMENT_DUE_SOON was actually new for that pair
   (ASSIGNMENT_DUE_REMINDER already exists under the campus block and is
   a distinct value/label — kept both since they're two different apps'
   reminder events, not aliases).

5. TASK 1 (this pass) — `user_profile`'s Follow feature + a "someone you
   follow just posted/created something" cross-app family. Two things
   confirmed already present and unchanged (no action needed):
   POST_LIKED / POST_COMMENTED (task 11, post app — see the block below
   this docstring). Five new choices added:
     - FOLLOW_REQUEST_RECEIVED / FOLLOW_REQUEST_ACCEPTED — fired by
       `user_profile`'s private-account follow-request flow
       (`Follow.Status.PENDING` → `ACCEPTED`, see
       `user_profile/models.py`'s `Follow` model and
       `AcceptFollowRequestView` in that app's `views.py`). Two separate
       values (not one "follow_status_changed" type) because a received
       request and an accepted request need different copy/deep-links on
       the recipient's side, matching how this enum already keeps
       JOIN_REQUEST_RECEIVED / JOIN_REQUEST_ACCEPTED distinct rather than
       folding them into one type with a status field.
     - NEW_POST_FROM_FOLLOWED / CLASSROOM_CREATED_BY_FOLLOWED /
       TESTSERIES_CREATED_BY_FOLLOWED — "someone I follow just created
       X" fan-out, one value per content type (post / liveclass
       classroom / testseries), same one-type-per-source-app pattern
       already used for TESTSERIES_POSTED vs ASSIGNMENT_POSTED vs
       CAMPUS_SESSION_SCHEDULED above rather than a single generic
       "new_content_from_followed" type — each source app's caller
       fires only its own value, and a client can route/deep-link on
       notif_type alone without inspecting `data`.
   Added the same "which NotifType values came from feature X" frozenset
   this file already keeps for MESSAGE_APP_TYPES/CAMPUS_APP_TYPES/
   TESTSERIES_APP_TYPES: see `FOLLOW_APP_TYPES` below `MESSAGE_APP_TYPES`.
   Pure choices-only addition — no migration needed for the enum values
   themselves, but see the module's own migration note below: this
   codebase does still generate a state-only `AlterField` migration for
   `notif_type` whenever `choices=` changes, purely so
   `makemigrations --check` doesn't flag drift in CI — that migration
   carries no database operation (CharField already has no CHECK
   constraint tied to `choices`). ⚠️ `TESTSERIES_CREATED_BY_FOLLOWED` is
   30 characters — exactly at the `max_length=30` ceiling below, with
   zero headroom left. The next NotifType value longer than 30 chars
   will need `max_length` bumped in the same migration that adds it.

Everything else (fields, db_table, choices, on_delete choices, the
task-44/46 comments) is unchanged from the original — it was already
correct.
"""
import logging

from django.db import models
from django.utils import timezone

from login.models import User

logger = logging.getLogger(__name__)


class NotificationQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(recipient=user)

    def unread(self):
        return self.filter(is_read=False)


class Notification(models.Model):
    class NotifType(models.TextChoices):
        # --- Confirmed values, carried over verbatim from the original
        # liveclass.Notification.NotifType (nothing dropped — a migration
        # with a narrower enum than what's already in the DB would leave
        # existing rows with an "invalid" notif_type). ---
        JOIN_REQUEST_RECEIVED = "join_request_received", "Join Request Received"
        JOIN_REQUEST_ACCEPTED = "join_request_accepted", "Join Request Accepted"
        JOIN_REQUEST_REJECTED = "join_request_rejected", "Join Request Rejected"
        PASS_REFUNDED = "pass_refunded", "Pass Refunded"
        SESSION_REMINDER = "session_reminder", "Session Reminder"
        ASSIGNMENT_GRADED = "assignment_graded", "Assignment Graded"
        QUERY_ANSWERED = "query_answered", "Doubt Answered"
        CERTIFICATE_ISSUED = "certificate_issued", "Certificate Issued"
        WAITLIST_PROMOTED = "waitlist_promoted", "Waitlist Promoted"
        CLASSROOM_FLAGGED = "classroom_flagged", "Classroom Flagged"
        NOTICE_POSTED = "notice_posted", "Notice Posted"
        SESSION_LIVE = "session_live", "Class Started"
        SESSION_CANCELLED = "session_cancelled", "Session Cancelled"
        ASSIGNMENT_POSTED = "assignment_posted", "New Assignment"
        SUBMISSION_RECEIVED = "submission_received", "New Submission"
        STAFF_ADDED = "staff_added", "Added As Staff"
        REVIEW_POSTED = "review_posted", "New Review"
        REPORT_REVIEWED = "report_reviewed", "Report Reviewed"
        WITHDRAWAL_APPROVED = "withdrawal_approved", "Withdrawal Approved"
        WITHDRAWAL_REJECTED = "withdrawal_rejected", "Withdrawal Rejected"
        WITHDRAWAL_PAID = "withdrawal_paid", "Withdrawal Paid"
        CLASSROOM_SHARED = "classroom_shared", "Classroom Shared With You"
        PASS_GIFT_RECEIVED = "pass_gift_received", "Pass Gift Received"
        PASS_GIFT_CLAIMED = "pass_gift_claimed", "Pass Gift Claimed"
        PASS_AUTO_RENEWED = "pass_auto_renewed", "Pass Auto-Renewed"
        AUTO_RENEW_FAILED = "auto_renew_failed", "Auto-Renewal Failed"
        PASS_GIFT_EXPIRED = "pass_gift_expired", "Gift Expired & Refunded"
        GENERIC = "generic", "Generic"

        # --- task 44 — new types for the message app's FCM-only push
        # system, now routed through create_notification() so they also
        # get a bell row. Adding choices is NOT a schema change (no
        # migration needed for the enum itself), but see
        # _MESSAGE_APP_TYPES in views.py — that mapping has to stay in
        # sync with this list. ---
        CHAT_MESSAGE = "chat_message", "New Message"
        MENTION = "mention", "You Were Mentioned"
        INCOMING_CALL = "incoming_call", "Incoming Call"

        # --- task 11 (post app) — new types for post/services.py's
        # notify_post_liked()/notify_post_commented(). These are neither
        # a liveclass type nor a message-app type (see MESSAGE_APP_TYPES
        # below, which stays unchanged — post-app types are a third,
        # separate source, not folded into that set). Adding choices is
        # not a schema change (no migration needed for the enum itself).
        # TASK 1 (this pass): CONFIRMED still present, unchanged — post
        # app already references both of these; nothing to add here.
        POST_LIKED = "post_liked", "Post Liked"
        POST_COMMENTED = "post_commented", "Post Commented"

        # --- campus app (campus_app_design.md §10) — new types for
        # campus/bridge.py's notify(). NOTICE_POSTED is intentionally NOT
        # redefined here — campus.bridge.NotifTypes.NOTICE_POSTED already
        # points at the existing NOTICE_POSTED value above, per that
        # class's own docstring, so campus reuses it instead of adding a
        # duplicate choice with the same string. Every value below must
        # match campus.bridge.NotifTypes verbatim (that mirror class is
        # what campus imports instead of this enum — see this app's
        # golden rule against campus importing core.models directly).
        CAMPUS_SESSION_SCHEDULED = "campus_session_scheduled", "Campus Session Scheduled"
        CAMPUS_SESSION_LIVE = "campus_session_live", "Campus Session Live"
        LOW_ATTENDANCE_ALERT = "low_attendance_alert", "Low Attendance Alert"
        ASSIGNMENT_POSTED_CAMPUS = "assignment_posted_campus", "New Campus Assignment"
        ASSIGNMENT_DUE_REMINDER = "assignment_due_reminder", "Assignment Due Reminder"
        RESULT_PUBLISHED = "result_published", "Result Published"
        FEE_DUE_REMINDER = "fee_due_reminder", "Fee Due Reminder"
        STAFF_ASSIGNMENT_APPROVED = "staff_assignment_approved", "Staff Assignment Approved"
        STAFF_ASSIGNMENT_REJECTED = "staff_assignment_rejected", "Staff Assignment Rejected"

        # --- testseries / assignment / campus-gamification apps — new
        # types added in this pass. TESTSERIES_* cover posting, checking
        # (grading), and payout-release notifications for the testseries
        # app; TESTSERIES_REVIEW_RECEIVED / TESTSERIES_QUERY_RECEIVED /
        # TESTSERIES_QUERY_ANSWERED cover the review/doubt-query flow on
        # test series. ASSIGNMENT_DUE_SOON is the assignment-app deadline
        # reminder (distinct from campus's own ASSIGNMENT_DUE_REMINDER
        # above — two different apps' reminder events, not aliases).
        # CAMPUS_REWARD_EARNED covers campus gamification payouts/rewards.
        # ASSIGNMENT_POSTED / ASSIGNMENT_GRADED already exist above (task
        # 44/46 block) and are reused as-is rather than duplicated. ---
        TESTSERIES_POSTED = "testseries_posted", "New Test Series"
        TESTSERIES_CHECKED = "testseries_checked", "Test Series Checked"
        TESTSERIES_PAYOUT_RELEASED = "testseries_payout_released", "Test Series Payout Released"
        ASSIGNMENT_DUE_SOON = "assignment_due_soon", "Assignment Due Soon"
        CAMPUS_REWARD_EARNED = "campus_reward_earned", "Campus Reward Earned"
        TESTSERIES_REVIEW_RECEIVED = "testseries_review_received", "New Test Series Review"
        TESTSERIES_QUERY_RECEIVED = "testseries_query_received", "New Test Series Query"
        TESTSERIES_QUERY_ANSWERED = "testseries_query_answered", "Test Series Query Answered"

        # --- TASK 1 (this pass) — user_profile's Follow feature (private-
        # account follow requests) plus the "someone you follow just
        # created something" cross-app fan-out. See FOLLOW_APP_TYPES
        # below (next to MESSAGE_APP_TYPES/CAMPUS_APP_TYPES/
        # TESTSERIES_APP_TYPES) for the matching "which values belong to
        # this feature" set. ---
        FOLLOW_REQUEST_RECEIVED = "follow_request_received", "Follow Request Received"
        FOLLOW_REQUEST_ACCEPTED = "follow_request_accepted", "Follow Request Accepted"
        NEW_POST_FROM_FOLLOWED = "new_post_from_followed", "New Post From Someone You Follow"
        CLASSROOM_CREATED_BY_FOLLOWED = (
            "classroom_created_by_followed", "New Classroom From Someone You Follow"
        )
        # ⚠️ 30 chars — exactly at max_length below, zero headroom left.
        TESTSERIES_CREATED_BY_FOLLOWED = (
            "testseries_created_by_followed", "New Test Series From Someone You Follow"
        )

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    # max_length=30 kept as-is — the longest current NotifType value
    # ("testseries_payout_released", 27 chars) still fits comfortably.
    # Revisit only if a future notif_type value exceeds 30.
    notif_type = models.CharField(
        max_length=30, choices=NotifType.choices, default=NotifType.GENERIC, db_index=True
    )

    title = models.CharField(max_length=150)
    # (task 44 — widened from CharField(255)): message-app chat text isn't
    # length-capped the way the original liveclass notification copy was,
    # so a CharField(255) column would hard-fail on Postgres for any
    # longer message. TextField has no such limit.
    message = models.TextField(blank=True)

    # Optional deep-link targets — whichever applies to notif_type. Both
    # SET_NULL (not CASCADE): a classroom/session being deleted later
    # shouldn't wipe out a user's notification history, just orphan the
    # link. `core` stays app-agnostic everywhere else in this file, but
    # these two FKs are the one place it points at `liveclass` directly —
    # acceptable because they're nullable/optional and only liveclass-
    # sourced notif_types ever populate them (message-app types use
    # `data` below instead).
    classroom = models.ForeignKey(
        "liveclass.Classroom", on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications"
    )
    session = models.ForeignKey(
        "liveclass.ClassSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications"
    )

    # (task 44 — new column): generic free-form context for notification
    # types that don't fit the classroom/session FKs above — e.g.
    # CHAT_MESSAGE/MENTION/INCOMING_CALL carry conversation_id /
    # message_id / call_id here instead.
    data = models.JSONField(default=dict, blank=True)

    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        db_table = "liveclass_notification"
        ordering = ["-created_at"]
        indexes = [
            # Unread-badge / unread-list queries (recipient + is_read, sorted).
            models.Index(fields=["recipient", "is_read", "-created_at"]),
            # Combined feed queries (recipient, all statuses, sorted) — see
            # module docstring point 2 for why this is a separate index
            # rather than redundant with the one above.
            models.Index(fields=["recipient", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.recipient} - {self.title}"

    #: task 46 — which NotifType values came from the message app rather
    #: than liveclass. Lives here (not in views.py/serializers.py) so
    #: both can import the same set instead of maintaining two copies.
    #: Keep in sync with NotifType above whenever a new message-app type
    #: is added.
    MESSAGE_APP_TYPES = frozenset({NotifType.CHAT_MESSAGE, NotifType.MENTION, NotifType.INCOMING_CALL})

    #: campus app — same "which NotifType values came from app X" pattern
    #: as MESSAGE_APP_TYPES above, for whatever views.py/serializers.py
    #: routing campus's own notification list ends up needing. NOTICE_POSTED
    #: deliberately excluded — it predates campus and is shared/generic,
    #: not campus-exclusive, matching the same reasoning MESSAGE_APP_TYPES
    #: already applies to types it doesn't claim.
    CAMPUS_APP_TYPES = frozenset({
        NotifType.CAMPUS_SESSION_SCHEDULED, NotifType.CAMPUS_SESSION_LIVE,
        NotifType.LOW_ATTENDANCE_ALERT, NotifType.ASSIGNMENT_POSTED_CAMPUS,
        NotifType.ASSIGNMENT_DUE_REMINDER, NotifType.RESULT_PUBLISHED,
        NotifType.FEE_DUE_REMINDER, NotifType.STAFF_ASSIGNMENT_APPROVED,
        NotifType.STAFF_ASSIGNMENT_REJECTED,
    })

    #: testseries / assignment-app / campus-gamification — same
    #: "which NotifType values came from app X" pattern as the two sets
    #: above, for testseries's own notification-list routing.
    TESTSERIES_APP_TYPES = frozenset({
        NotifType.TESTSERIES_POSTED, NotifType.TESTSERIES_CHECKED,
        NotifType.TESTSERIES_PAYOUT_RELEASED, NotifType.TESTSERIES_REVIEW_RECEIVED,
        NotifType.TESTSERIES_QUERY_RECEIVED, NotifType.TESTSERIES_QUERY_ANSWERED,
    })

    #: TASK 1 (this pass) — user_profile's Follow feature, same "which
    #: NotifType values came from app/feature X" pattern as the three
    #: sets above. POST_LIKED/POST_COMMENTED are deliberately NOT in here
    #: — they're post-app types (task 11), not follow-app types, even
    #: though a "someone you follow" feed could plausibly surface both;
    #: this set stays scoped to Follow-relationship-driven notifications
    #: only, matching how CAMPUS_APP_TYPES doesn't reach into NOTICE_POSTED.
    FOLLOW_APP_TYPES = frozenset({
        NotifType.FOLLOW_REQUEST_RECEIVED, NotifType.FOLLOW_REQUEST_ACCEPTED,
        NotifType.NEW_POST_FROM_FOLLOWED, NotifType.CLASSROOM_CREATED_BY_FOLLOWED,
        NotifType.TESTSERIES_CREATED_BY_FOLLOWED,
    })

    def mark_read(self):
        """Idempotent — only writes (and only touches these two columns)
        when the row wasn't already read, so calling this on an
        already-read notification is a cheap no-op, not a redundant
        UPDATE."""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=["is_read", "read_at"])


class NotificationPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="notification_preference")

    push_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=False)  # opt-IN: SMS costs real money per send
    whatsapp_enabled = models.BooleanField(default=False)  # opt-IN, same reasoning as sms_enabled

    muted_types = models.JSONField(default=list, blank=True, help_text="List of Notification.NotifType values.")

    class DigestFrequency(models.TextChoices):
        OFF = "off", "Off"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"

    digest_frequency = models.CharField(max_length=10, choices=DigestFrequency.choices, default=DigestFrequency.OFF)
    last_digest_sent_at = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "liveclass_notificationpreference"

    def __str__(self):
        return f"Notification prefs for {self.user}"

    def allowed_channels_for(self, notif_type: str) -> list[str]:
        """What create_notification()'s callers should actually try for
        this (user, notif_type) — an empty list means "in-app bell row
        only, no push/email/sms/whatsapp send at all", which is what a
        muted type collapses to (the Notification row itself is still
        created — a user muting reminders shouldn't lose the in-app
        history, just the interruption)."""
        if notif_type in (self.muted_types or []):
            return []
        allowed = []
        if self.push_enabled:
            allowed.append("push")
        if self.email_enabled:
            allowed.append("email")
        if self.sms_enabled:
            allowed.append("sms")
        if self.whatsapp_enabled:
            allowed.append("whatsapp")
        return allowed

    @classmethod
    def for_user(cls, user) -> "NotificationPreference":
        """Every user gets sane defaults (all-on push/email, opt-in
        sms/whatsapp, no mutes) without needing a migration data-load or
        a signal on User creation — get_or_create on first touch."""
        pref, _ = cls.objects.get_or_create(user=user)
        return pref