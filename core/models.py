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

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    # max_length=30 kept as-is — the longest current NotifType value
    # ("join_request_received", 22 chars) still fits comfortably.
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