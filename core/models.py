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

4. Added 10 new NotifType choices for the testseries, assigments, and
   campus-gamification apps: TESTSERIES_POSTED, TESTSERIES_CHECKED,
   TESTSERIES_PAYOUT_RELEASED, assigments_POSTED, assigments_GRADED,
   assigments_DUE_SOON, CAMPUS_REWARD_EARNED, TESTSERIES_REVIEW_RECEIVED,
   TESTSERIES_QUERY_RECEIVED, TESTSERIES_QUERY_ANSWERED. Pure addition —
   no existing choice was renamed or removed, and choices-only changes
   need no migration. NOTE: assigments_POSTED and assigments_GRADED
   already existed above under the task-44/46 block (assigments_POSTED,
   assigments_GRADED) — reused rather than duplicated with a new string,
   since NotifType.values must stay a set of unique choice values. Only
   assigments_DUE_SOON was actually new for that pair
   (assigments_DUE_REMINDER already exists under the campus block and is
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
       X" fan-out, one value per content type (post / tuitionclass
       classroom / testseries), same one-type-per-source-app pattern
       already used for TESTSERIES_POSTED vs assigments_POSTED vs
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

6. G-6 (this pass) — CHAT_APP_DOCUMENTATION.md §9.4 item 22's still-open
   half: a new `ParentToken` landing on `status=PENDING`
   (`message/views_parent.py`'s `ParentVerifyCodeView`) previously
   notified the student in no way at all — `ParentPendingRequestsView`
   was the only way to discover one, and only by polling it. Added
   `PARENT_DEVICE_PENDING` — no existing value fit (it's neither a
   join/follow/assigments event nor any tuitionclass type), so this is a
   new choice, not a reuse, same reasoning already applied above for
   assigments_DUE_SOON vs assigments_DUE_REMINDER. Grouped under the
   task-44 message-app block above (Parent Mode lives in `message`, per
   that view file's own module docstring) and added to
   `MESSAGE_APP_TYPES` alongside CHAT_MESSAGE/MENTION/INCOMING_CALL for
   the same reason. `"parent_device_pending"` is 22 characters — well
   under the `max_length=30` ceiling item 5 above flagged as
   zero-headroom at 30. Same migration story as every other choices-only
   addition in this file: no schema change, but this codebase's
   convention is still to generate a state-only `AlterField` migration
   so `makemigrations --check` doesn't flag drift in CI — that migration
   file wasn't part of this pass's upload, so it still needs to be
   generated against this change before it lands.

Everything else (fields, db_table, choices, on_delete choices, the
task-44/46 comments) is unchanged from the original — it was already
correct.
"""
import logging
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import models
from django.utils import timezone

from login.models import User

logger = logging.getLogger(__name__)


class NotificationQuerySet(models.QuerySet):
    def for_user(self, user):
        qs = self.filter(recipient=user)
        # Hide rows triggered by anyone in a block relationship with `user`.
        # Hidden, not deleted -> they come back if the block is lifted.
        # (Positive sub-select, not exclude() on a JSON key: avoids NULL
        # semantics for rows that have no actor_id at all.)
        try:
            from user_profile.services import blocked_user_ids

            blocked = blocked_user_ids(user)
        except Exception:
            blocked = set()
        if blocked:
            ids = list(blocked) + [str(i) for i in blocked]
            qs = qs.exclude(
                pk__in=self.model.objects.filter(recipient=user, data__actor_id__in=ids).values("pk")
            )
        return qs

    def unread(self):
        return self.filter(is_read=False)


class Notification(models.Model):
    class NotifType(models.TextChoices):
        # --- Confirmed values, carried over verbatim from the original
        # tuitionclass.Notification.NotifType (nothing dropped — a migration
        # with a narrower enum than what's already in the DB would leave
        # existing rows with an "invalid" notif_type). ---
        JOIN_REQUEST_RECEIVED = "join_request_received", "Join Request Received"
        JOIN_REQUEST_ACCEPTED = "join_request_accepted", "Join Request Accepted"
        JOIN_REQUEST_REJECTED = "join_request_rejected", "Join Request Rejected"
        PASS_REFUNDED = "pass_refunded", "Pass Refunded"
        SESSION_REMINDER = "session_reminder", "Session Reminder"
        assigments_GRADED = "assigments_graded", "assigments Graded"
        QUERY_ANSWERED = "query_answered", "Doubt Answered"
        CERTIFICATE_ISSUED = "certificate_issued", "Certificate Issued"
        WAITLIST_PROMOTED = "waitlist_promoted", "Waitlist Promoted"
        CLASSROOM_FLAGGED = "classroom_flagged", "Classroom Flagged"
        NOTICE_POSTED = "notice_posted", "Notice Posted"
        SESSION_LIVE = "session_live", "Class Started"
        SESSION_CANCELLED = "session_cancelled", "Session Cancelled"
        assigments_POSTED = "assigments_posted", "New assigments"
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

        # --- G-6 (this pass) — Parent Mode mutual-consent gap,
        # CHAT_APP_DOCUMENTATION.md §9.4 item 22's still-open half: a new
        # ParentToken landing on status=PENDING (message/views_parent.py
        # ParentVerifyCodeView) previously told the student nothing —
        # ParentPendingRequestsView was the only way to find out, and
        # only if they thought to poll it. No existing value fit ("new
        # parent device wants access" isn't a join/follow/assigments
        # event), so this is a new choice, not a reuse. Grouped with the
        # other message-app types above (Parent Mode lives in `message`,
        # per views_parent.py's own module docstring) — added to
        # MESSAGE_APP_TYPES below for the same reason. ---
        PARENT_DEVICE_PENDING = "parent_device_pending", "Parent Device Pending Approval"

        # --- task 11 (post app) — new types for post/services.py's
        # notify_post_liked()/notify_post_commented(). These are neither
        # a tuitionclass type nor a message-app type (see MESSAGE_APP_TYPES
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
        assigments_POSTED_CAMPUS = "assigments_posted_campus", "New Campus assigments"
        assigments_DUE_REMINDER = "assigments_due_reminder", "assigments Due Reminder"
        RESULT_PUBLISHED = "result_published", "Result Published"
        FEE_DUE_REMINDER = "fee_due_reminder", "Fee Due Reminder"
        STAFF_assigments_APPROVED = "staff_assigments_approved", "Staff assigments Approved"
        STAFF_assigments_REJECTED = "staff_assigments_rejected", "Staff assigments Rejected"

        # --- testseries / assigments / campus-gamification apps — new
        # types added in this pass. TESTSERIES_* cover posting, checking
        # (grading), and payout-release notifications for the testseries
        # app; TESTSERIES_REVIEW_RECEIVED / TESTSERIES_QUERY_RECEIVED /
        # TESTSERIES_QUERY_ANSWERED cover the review/doubt-query flow on
        # test series. assigments_DUE_SOON is the assigments-app deadline
        # reminder (distinct from campus's own assigments_DUE_REMINDER
        # above — two different apps' reminder events, not aliases).
        # CAMPUS_REWARD_EARNED covers campus gamification payouts/rewards.
        # assigments_POSTED / assigments_GRADED already exist above (task
        # 44/46 block) and are reused as-is rather than duplicated. ---
        TESTSERIES_POSTED = "testseries_posted", "New Test Series"
        TESTSERIES_CHECKED = "testseries_checked", "Test Series Checked"
        TESTSERIES_PAYOUT_RELEASED = "testseries_payout_released", "Test Series Payout Released"
        assigments_DUE_SOON = "assigments_due_soon", "assigments Due Soon"
        CAMPUS_REWARD_EARNED = "campus_reward_earned", "Campus Reward Earned"
        # T4 §G/§F — online-class "starts in 5 minutes" reminder, and the
        # two doubt events (student posts -> subject teacher; teacher/
        # student replies -> the other side). Must match
        # campus.bridge.NotifTypes verbatim.
        CAMPUS_CLASS_STARTING = "campus_class_starting", "Class Starting Soon"
        CAMPUS_DOUBT_POSTED = "campus_doubt_posted", "New Class Doubt"
        CAMPUS_DOUBT_REPLIED = "campus_doubt_replied", "Class Doubt Reply"
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

        # --- TASK G1 (growth_and_feature_tasks.md — Streaks) —
        # user_profile.models.Streak / user_profile.tasks.
        # send_streak_risk_reminders. Two values, not one
        # "streak_status_changed" type, for the same reason
        # FOLLOW_REQUEST_RECEIVED/FOLLOW_REQUEST_ACCEPTED are kept
        # separate above — a "you hit a milestone" push and a "you're
        # about to lose your streak" push need different copy/urgency on
        # the client, not a shared type with a flag. Both well under the
        # max_length=30 ceiling (24 and 15 chars). ---
        STREAK_MILESTONE_REACHED = "streak_milestone_reached", "Streak Milestone Reached"
        STREAK_AT_RISK = "streak_at_risk", "Streak At Risk"

        # --- TASK G2 (growth_and_feature_tasks.md — recap screen). Choices-
        # only addition, same "no schema change but generate the state-only
        # AlterField migration anyway" convention every other NotifType
        # addition in this file follows. "weekly_recap_ready" is 19
        # characters — well under max_length=30. ---
        WEEKLY_RECAP_READY = "weekly_recap_ready", "Your Week Is Ready"

        # --- FEE-6 follow-up (feature: Fee Reminder Notifications) —
        # `FEE_DUE_REMINDER` above already covers "due today" and every
        # day it stays unpaid after that; this is the separate, more
        # urgent copy for an invoice that has actually gone past its
        # due date, distinct enough from a same-day reminder that a
        # client wants to style/badge it differently (e.g. red vs
        # amber). See campus/tasks.py::send_fee_due_reminders for where
        # this is actually fired, and campus/bridge.py's NotifTypes
        # mirror class for the matching string constant campus imports
        # instead of this enum directly (golden rule — see that
        # module's docstring). 19 characters, well under max_length=30.
        FEE_OVERDUE_REMINDER = "fee_overdue_reminder", "Fee Overdue"

        # --- STORIES UPGRADE, PART 2a - "X mentioned you in their story".
        # Created by post/services.py::notify_story_mentions; `data.story_id`
        # is what the client opens. Deliberately NOT reusing MENTION above:
        # that one belongs to the message app (MESSAGE_APP_TYPES) and its
        # rows carry a conversation_id. 13 characters, well under
        # max_length=30. Choices-only change; see migration core/0002. ---
        STORY_MENTION = "story_mention", "Story Mention"

        # --- P5a-BE — "X tagged you in a post". Fired by post/serializers.py
        # (notify_post_tags) when a PostTag row is created for someone other
        # than the post's author; `data.post_id` is what the client opens.
        # 8 characters, well under max_length=30. Choices-only change: no DB
        # schema change, but generate the state-only AlterField migration for
        # `notif_type` (same convention as every addition above). ---
        POST_TAG = "post_tag", "Tagged In Post"

        # --- TASK 3.4 — events that had no bell row. STORY_REACTION: "X reacted
        # to your story" (batched per story; post/services.py::notify_story_reacted
        # existed but was never called and the type was missing). POST_REPOSTED:
        # "X reposted your post" (batched per original post). Both go to the
        # "other" category like POST_LIKED / POST_COMMENTED. Choices-only
        # change -> core/migrations/0003. ---
        STORY_REACTION = "story_reaction", "Story Reaction"
        POST_REPOSTED = "post_reposted", "Post Reposted"

        # --- Help & feedback: LearnScroll Support replied to the user's ticket
        # (`data.ticket_id`). Fired by support/services.py::add_staff_reply.
        # 13 chars, within max_length=30. Choices-only change -> core/migrations/0005. ---
        SUPPORT_REPLY = "support_reply", "Support Reply"

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    # max_length=30 kept as-is — the longest current NotifType value
    # ("testseries_payout_released", 27 chars) still fits comfortably.
    # Revisit only if a future notif_type value exceeds 30.
    notif_type = models.CharField(
        max_length=30, choices=NotifType.choices, default=NotifType.GENERIC, db_index=True
    )

    title = models.CharField(max_length=150)
    # (task 44 — widened from CharField(255)): message-app chat text isn't
    # length-capped the way the original tuitionclass notification copy was,
    # so a CharField(255) column would hard-fail on Postgres for any
    # longer message. TextField has no such limit.
    message = models.TextField(blank=True)

    # Optional deep-link targets — whichever applies to notif_type. Both
    # SET_NULL (not CASCADE): a classroom/session being deleted later
    # shouldn't wipe out a user's notification history, just orphan the
    # link. `core` stays app-agnostic everywhere else in this file, but
    # these two FKs are the one place it points at `tuitionclass` directly —
    # acceptable because they're nullable/optional and only tuitionclass-
    # sourced notif_types ever populate them (message-app types use
    # `data` below instead).
    classroom = models.ForeignKey(
        "tuitionclass.Classroom", on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications"
    )
    session = models.ForeignKey(
        "tuitionclass.ClassSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications"
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
        db_table = "tuitionclass_notification"
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
    #: than tuitionclass. Lives here (not in views.py/serializers.py) so
    #: both can import the same set instead of maintaining two copies.
    #: Keep in sync with NotifType above whenever a new message-app type
    #: is added. PARENT_DEVICE_PENDING (G-6, this pass) added here too —
    #: Parent Mode is part of `message`, same as CHAT_MESSAGE/MENTION/
    #: INCOMING_CALL above.
    MESSAGE_APP_TYPES = frozenset({
        NotifType.CHAT_MESSAGE, NotifType.MENTION, NotifType.INCOMING_CALL,
        NotifType.PARENT_DEVICE_PENDING,
    })

    #: campus app — same "which NotifType values came from app X" pattern
    #: as MESSAGE_APP_TYPES above, for whatever views.py/serializers.py
    #: routing campus's own notification list ends up needing. NOTICE_POSTED
    #: deliberately excluded — it predates campus and is shared/generic,
    #: not campus-exclusive, matching the same reasoning MESSAGE_APP_TYPES
    #: already applies to types it doesn't claim.
    CAMPUS_APP_TYPES = frozenset({
        NotifType.CAMPUS_SESSION_SCHEDULED, NotifType.CAMPUS_SESSION_LIVE,
        NotifType.LOW_ATTENDANCE_ALERT, NotifType.assigments_POSTED_CAMPUS,
        NotifType.assigments_DUE_REMINDER, NotifType.RESULT_PUBLISHED,
        NotifType.FEE_DUE_REMINDER, NotifType.STAFF_assigments_APPROVED,
        NotifType.STAFF_assigments_REJECTED,
        NotifType.CAMPUS_CLASS_STARTING, NotifType.CAMPUS_DOUBT_POSTED,
        NotifType.CAMPUS_DOUBT_REPLIED,
    })

    #: testseries / assigments-app / campus-gamification — same
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

    #: N3-BE — notification-screen filter chips. ONE place that maps every
    #: notif_type to a client-facing category: mentions | follows |
    #: classroom | tests | other. Anything not listed here (including any
    #: NotifType added later) falls into "other" automatically, so a new
    #: type never needs a code change just to avoid crashing the serializer
    #: or disappearing from the "All" tab. Add it to a set below only when
    #: it deserves its own chip.
    CATEGORY_MENTIONS = "mentions"
    CATEGORY_FOLLOWS = "follows"
    CATEGORY_CLASSROOM = "classroom"
    CATEGORY_TESTS = "tests"
    CATEGORY_OTHER = "other"
    CATEGORIES = ("mentions", "follows", "classroom", "tests", "other")

    CATEGORY_TYPES = {
        # message-app MENTION is intentionally here too: it is still "someone
        # mentioned/tagged me", whatever app fired it.
        "mentions": frozenset({
            NotifType.MENTION, NotifType.STORY_MENTION, NotifType.POST_TAG,
        }),
        "follows": FOLLOW_APP_TYPES,
        "tests": TESTSERIES_APP_TYPES,
        # tuitionclass + campus + assignments: everything classroom/campus
        # side. (Chat messages, calls, likes/comments, streaks, recap,
        # generic... stay in "other".)
        "classroom": CAMPUS_APP_TYPES | frozenset({
            NotifType.JOIN_REQUEST_RECEIVED, NotifType.JOIN_REQUEST_ACCEPTED,
            NotifType.JOIN_REQUEST_REJECTED, NotifType.PASS_REFUNDED,
            NotifType.SESSION_REMINDER, NotifType.SESSION_LIVE,
            NotifType.SESSION_CANCELLED, NotifType.assigments_GRADED,
            NotifType.assigments_POSTED, NotifType.assigments_DUE_SOON,
            NotifType.QUERY_ANSWERED, NotifType.CERTIFICATE_ISSUED,
            NotifType.WAITLIST_PROMOTED, NotifType.CLASSROOM_FLAGGED,
            NotifType.NOTICE_POSTED, NotifType.SUBMISSION_RECEIVED,
            NotifType.STAFF_ADDED, NotifType.REVIEW_POSTED,
            NotifType.REPORT_REVIEWED, NotifType.WITHDRAWAL_APPROVED,
            NotifType.WITHDRAWAL_REJECTED, NotifType.WITHDRAWAL_PAID,
            NotifType.CLASSROOM_SHARED, NotifType.PASS_GIFT_RECEIVED,
            NotifType.PASS_GIFT_CLAIMED, NotifType.PASS_AUTO_RENEWED,
            NotifType.AUTO_RENEW_FAILED, NotifType.PASS_GIFT_EXPIRED,
            NotifType.FEE_OVERDUE_REMINDER, NotifType.CAMPUS_REWARD_EARNED,
        }),
    }

    @classmethod
    def category_for(cls, notif_type) -> str:
        """notif_type -> "mentions" | "follows" | "classroom" | "tests" |
        "other". Unknown/future types -> "other"."""
        for category, types in cls.CATEGORY_TYPES.items():
            if notif_type in types:
                return category
        return cls.CATEGORY_OTHER

    @classmethod
    def filter_by_category(cls, qs, category):
        """Shared by the list and unread-count endpoints (same pattern as
        the ?source= helper in views.py). An unknown/empty category is
        ignored (returns qs untouched) so a stale or typo'd client param
        never turns into an empty screen or a 400."""
        if category in ("mentions", "follows", "classroom", "tests"):
            return qs.filter(notif_type__in=cls.CATEGORY_TYPES[category])
        if category == cls.CATEGORY_OTHER:
            listed = frozenset().union(*cls.CATEGORY_TYPES.values())
            return qs.exclude(notif_type__in=listed)
        return qs

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

    # --- N9-BE — quiet hours / Do Not Disturb -------------------------
    # Daily recurring window, interpreted in `timezone` (an IANA name such
    # as "Asia/Kolkata", sent by the client). Both NULL = quiet hours off;
    # exactly one set is rejected by the serializer. start > end means the
    # window wraps past midnight (e.g. 22:00 -> 07:00).
    quiet_start = models.TimeField(null=True, blank=True)
    quiet_end = models.TimeField(null=True, blank=True)
    # NOTE: this field name shadows the module-level `django.utils.timezone`
    # import ONLY inside this class body; methods still see the module
    # global, so `timezone.now()` below is fine.
    timezone = models.CharField(max_length=64, default="UTC")
    # One-off "pause everything until X" (the 1h / 8h / 24h chips).
    dnd_until = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    #: Types that ignore quiet hours / DND (a missed call or a pending
    #: parent-device approval is useless once it's hours old). A type the
    #: user explicitly MUTED still stays muted — see allowed_channels_for.
    #: There is no dedicated security NotifType yet; add it here when one
    #: exists.
    QUIET_BYPASS_TYPES = frozenset({
        Notification.NotifType.INCOMING_CALL,
        Notification.NotifType.PARENT_DEVICE_PENDING,
    })
    #: Interruptive channels that quiet hours silence. Email is left alone:
    #: it doesn't buzz the phone and the user reads it whenever they like.
    QUIET_SILENCED_CHANNELS = frozenset({"push", "sms", "whatsapp"})

    class Meta:
        db_table = "tuitionclass_notificationpreference"

    def __str__(self):
        return f"Notification prefs for {self.user}"

    def _tzinfo(self):
        try:
            return ZoneInfo(self.timezone or "UTC")
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return dt_timezone.utc  # bad/legacy value must never crash a send

    def is_quiet_now(self, now=None) -> bool:
        """True while a DND pause or the daily quiet window is active."""
        now = now or timezone.now()
        if self.dnd_until and now < self.dnd_until:
            return True
        start, end = self.quiet_start, self.quiet_end
        if start is None or end is None or start == end:
            return False
        local = now.astimezone(self._tzinfo()).time()
        if start < end:
            return start <= local < end
        return local >= start or local < end  # wraps past midnight

    def allowed_channels_for(self, notif_type: str, now=None) -> list[str]:
        """What create_notification()'s callers should actually try for
        this (user, notif_type) — an empty list means "in-app bell row
        only, no push/email/sms/whatsapp send at all", which is what a
        muted type collapses to (the Notification row itself is still
        created — a user muting reminders shouldn't lose the in-app
        history, just the interruption).

        N9-BE: during quiet hours / DND, push/sms/whatsapp are dropped
        (email stays) unless `notif_type` is in QUIET_BYPASS_TYPES. The
        bell row is still created either way."""
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
        if notif_type not in self.QUIET_BYPASS_TYPES and self.is_quiet_now(now):
            allowed = [c for c in allowed if c not in self.QUIET_SILENCED_CHANNELS]
        return allowed

    @classmethod
    def for_user(cls, user) -> "NotificationPreference":
        """Every user gets sane defaults (all-on push/email, opt-in
        sms/whatsapp, no mutes) without needing a migration data-load or
        a signal on User creation — get_or_create on first touch."""
        pref, _ = cls.objects.get_or_create(user=user)
        return pref

class NotificationMute(models.Model):
    """N6-BE — `user` has muted `muted_actor`: nothing `muted_actor` does
    (like, comment, follow, mention...) ever produces an in-app
    notification row for `user`. Checked at write time in
    core.services.create_notification and
    core.notification_batching.create_batched_notification (same spot as
    the RestrictUser check), so a muted actor leaves no row at all, not a
    hidden one. Invisible to the muted actor.

    Not retroactive: rows that already exist are left alone — the user
    clears those with DELETE notifications/{id}/ if they want them gone.
    Unlike `NotificationPreference.muted_types` (mutes a TYPE for
    everyone, keeps the bell row), this mutes a PERSON and drops the row.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notification_mutes")
    muted_actor = models.ForeignKey(User, on_delete=models.CASCADE, related_name="muted_by_notification_mutes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_notification_mute"
        constraints = [
            models.UniqueConstraint(fields=["user", "muted_actor"], name="uniq_notification_mute_pair"),
            models.CheckConstraint(condition=~models.Q(user=models.F("muted_actor")), name="notification_mute_not_self"),
        ]

    def __str__(self):
        return f"{self.user_id} muted {self.muted_actor_id}"

    @classmethod
    def is_muted(cls, recipient_id, actor_id) -> bool:
        """True if `recipient_id` has muted `actor_id`. Takes raw ids (no
        User instances needed) — one indexed EXISTS via the unique index."""
        if recipient_id is None or actor_id is None:
            return False
        return cls.objects.filter(user_id=recipient_id, muted_actor_id=actor_id).exists()


# ============================================================
# TASK G18 (growth_and_feature_tasks.md — Empty states & first-time-user
# onboarding).
#
# Tracks whether a brand-new user has been through (or explicitly
# skipped) the post-signup onboarding flow: pick interests
# (post.UserInterest — already exists, TASK 3) -> suggested
# people/campuses to follow (user_profile.Follow / campus.Campus) ->
# try one sample test (testseries.TestSeries). Lives here in `core`,
# not on `login.User` itself, for the same reason `NoticeBoardView`/
# `SearchView` live here instead of being forked into each owning app
# (see this module's own docstring, and core/views.py's SearchView /
# NoticeBoardView docstrings): the flow is a cross-app concern by
# nature (it reads from post, user_profile, campus and testseries all
# at once), so it doesn't belong bolted onto any single one of them,
# and `login.User` shouldn't grow a field per feature that happens to
# run right after signup.
class OnboardingProgress(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="onboarding_progress"
    )

    # Set True only by the explicit "Finish" tap at the end of the flow
    # (OnboardingCompleteView, core/views.py) — reaching the last step
    # without tapping Finish does NOT set this; a user who closes the
    # app mid-flow sees onboarding again next open, same as any
    # unfinished setup wizard.
    completed = models.BooleanField(default=False)

    # Set True if the user tapped "Skip" instead of finishing — kept
    # distinct from `completed` so it's answerable later (e.g. a growth
    # metric on skip vs. completion rate) without being conflated with
    # an actual finish. Either flag being True means "don't show the
    # onboarding flow again."
    skipped = models.BooleanField(default=False)

    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_onboarding_progress"

    def __str__(self):
        state = "done" if self.completed else ("skipped" if self.skipped else "pending")
        return f"{self.user.username} onboarding={state}"

    @property
    def is_finished(self) -> bool:
        return self.completed or self.skipped
