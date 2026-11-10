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

        # --- copyrights app: dedicated types so the app opens the right screen (data.claim_id) ---
        COPYRIGHT_NOTICE = "copyright_notice", "Copyright complaint about your content"
        COPYRIGHT_DECISION = "copyright_decision", "Copyright decision"
        COPYRIGHT_STANDING = "copyright_standing", "Copyright standing changed"
        COPYRIGHT_STAFF = "copyright_staff", "Copyright staff alert"

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

        # --- T1.2 — Doubt Queue: someone (teacher or classmate) answered the
        # user's classroom doubt. Fired by message/services.py::
        # notify_doubt_answered(); `data` carries doubt_id / answer_id /
        # group_id / conversation_id so a tap opens that group's Doubts
        # screen. Message-app type (MESSAGE_APP_TYPES) but listed under the
        # "classroom" notification chip. 14 chars, within max_length=30.
        # Choices-only change -> core/migrations/0008. ---
        DOUBT_ANSWERED = "doubt_answered", "Doubt Answered (Doubt Queue)"

        # --- T2.7 (Loop 7) — cost-free referral rewards (streak-freeze /
        # badge / founding member) for BOTH sides of a redeemed referral.
        # `data.reward` = {"streak_freeze": n, "badges": [codes]}. 15 chars,
        # within max_length=30. Choices-only change (add a core migration). ---
        REFERRAL_REWARD = "referral_reward", "Referral Reward"

        # --- Push: campus attendance. A student was marked ABSENT; fired ONLY
        # to that student's linked parents (never the student) by
        # campus/attendance_push.py. 21 chars, within max_length=30. Choices-only
        # change -> core/migrations/0012. ---
        CAMPUS_STUDENT_ABSENT = "campus_student_absent", "Student Absent"
        # Homework Diary (campus) — a teacher posted a diary line for the
        # student's section / the "due tomorrow" reminder. Must match
        # campus.bridge.NotifTypes verbatim. 21 and 18 chars (< 30 ceiling).
        HOMEWORK_DIARY_POSTED = "homework_diary_posted", "Homework Diary"
        HOMEWORK_DIARY_DUE = "homework_diary_due", "Homework Due"
        # Substitute teacher (campus) — a teacher was assigned to cover a
        # period / the students' class has a substitute that day / the cover
        # was cancelled. Must match campus.bridge.NotifTypes verbatim (core
        # migration 0014). 26 / 24 / 27 chars (< 30 ceiling).
        CAMPUS_SUBSTITUTE_ASSIGNED = "campus_substitute_assigned", "Substitute Duty"
        CAMPUS_CLASS_SUBSTITUTED = "campus_class_substituted", "Class Substitute"
        CAMPUS_SUBSTITUTE_CANCELLED = "campus_substitute_cancelled", "Substitute Duty Cancelled"
        # Staff leave (campus) — a staff member applied for leave (to the
        # approvers) / the application was decided or cancelled (to the
        # applicant). Must match campus.bridge.NotifTypes verbatim. 22 / 20
        # chars (< 30 ceiling).
        CAMPUS_LEAVE_REQUESTED = "campus_leave_requested", "Leave Requested"
        CAMPUS_LEAVE_DECIDED = "campus_leave_decided", "Leave Decision"
        # Parent-teacher meetings + teacher<->parent chat (campus). Must match
        # campus.bridge.NotifTypes verbatim (core migration 0015). 20 / 17 / 20 /
        # 19 / 21 chars (< 30 ceiling).
        CAMPUS_PTM_SCHEDULED = "campus_ptm_scheduled", "PTM Scheduled"
        CAMPUS_PTM_BOOKED = "campus_ptm_booked", "PTM Booking"
        CAMPUS_PTM_CANCELLED = "campus_ptm_cancelled", "PTM Cancelled"
        CAMPUS_PTM_REMINDER = "campus_ptm_reminder", "PTM Reminder"
        CAMPUS_PARENT_MESSAGE = "campus_parent_message", "Parent-Teacher Message"
        # Class resources (content share) — a teacher posted study material
        # for the student's section. Must match campus.bridge.NotifTypes
        # verbatim (core migration 0017). 22 chars (< 30 ceiling).
        CAMPUS_RESOURCE_POSTED = "campus_resource_posted", "New Class Resource"
        # Recorded classes + class notes (campus) — a live class recording is ready to
        # watch / a teacher posted a note for the section. Must match
        # campus.bridge.NotifTypes verbatim (core migration 0018). 22 / 18 chars (< 30).
        CAMPUS_RECORDING_READY = "campus_recording_ready", "Class Recording Ready"
        CAMPUS_NOTE_POSTED = "campus_note_posted", "New Class Note"
        # C3 — online fee payment succeeded: student + linked parents get a
        # "payment received" push (data.invoice_id / payment_id / student_id).
        # 15 characters, well under max_length=30. Choices-only change; see
        # migration core/0019.
        CAMPUS_FEE_PAID = "campus_fee_paid", "Fee Payment Received"

        # COMMUNICATION — emergency broadcast from a campus admin/principal. Bypasses quiet hours / DND
        # and cannot be muted (see NotificationPreference.QUIET_BYPASS_TYPES / NEVER_MUTE_TYPES).
        # 16 characters, well under max_length=30. Choices-only change; see migration core/0020.
        CAMPUS_EMERGENCY = "campus_emergency", "Emergency Alert"

        # SAFETY — data-deletion request updates (new request -> admins, approved / rejected / completed ->
        # the requester). 13 characters, well under max_length=30. Choices-only change; see migration core/0021.
        CAMPUS_SAFETY = "campus_safety", "Privacy & Data Update"
        # Admission — enrolled student welcome (core migration 0022).
        CAMPUS_ADMISSION = "campus_admission", "Admission Update"
        # tuitionclass parent/student -> teacher template messages (tuitionclass/views/parent.py).
        # These two members were referenced there but never defined -> AttributeError (HTTP 500)
        # right after the message row was saved. 23 / 22 chars, within max_length=30 (core migration 0023).
        PARENT_MESSAGE_RECEIVED = "parent_message_received", "Parent Message Received"
        PARENT_MESSAGE_REPLIED = "parent_message_replied", "Parent Message Replied"
        # H15 trust signals (core migration 0024). REPORT_OUTCOME: what happened to a post / comment / account the
        # user REPORTED ("action liya gaya" / "no rule violation") - fired by user_profile/report_automation.py
        # (NOT REPORT_REVIEWED: that one is the tuitionclass classroom-report type and its handler expects a
        # classroom_id). CREATOR_FEEDBACK: "your note was read by 120 students and saved by 30" - fired by
        # post/creator_feedback.py. 14 / 16 chars (< 30 ceiling). Both fall in the "other" category.
        REPORT_OUTCOME = "report_outcome", "Report Outcome"
        CREATOR_FEEDBACK = "creator_feedback", "Creator Feedback"
        # H18 study digest (core migration 0025): "Physics: 5 new notes - #kinematics: 3 new posts". Built by
        # core/content_digest.py, one row per user per period, push honours quiet hours. Falls in "other".
        CONTENT_DIGEST = "content_digest", "Study Digest"
        # Doubt post upgrade (core migration 0027): a verified teacher whose subject matches a new feed doubt.
        # Data: {"post_id", "subject", "is_urgent", "bounty_coins"}. 22 chars (< 30 ceiling). Falls in "other".
        FEED_DOUBT_FOR_TEACHER = "feed_doubt_for_teacher", "Doubt For Your Subject"
        # N6 reminders (core migration 0031): "your test window closes in 24h / 1h" for a student who started an
        # attempt and has not submitted it. Built by testseries/tasks.py::send_test_deadline_reminders (cache-deduped,
        # capped + snooze / turn-off actions via core/notification_reminders.py). Data: {"series_id", "attempt_id",
        # "window", "ends_at"}. 18 chars (< 30 ceiling). Falls in the "tests" inbox chip.
        TESTSERIES_DUE_SOON = "testseries_due_soon", "Test Closing Soon"
        # S8 learning preferences (core migration 0034): the student's own daily "time to study" reminder (time + days
        # chosen in Settings > Learning). Built by core/study_reminders.py, one row per user per local day. URGENT tier
        # (core/notification_priority.py) on purpose: it is a reminder the user asked for, so Focus mode / the N6 daily
        # cap must not eat it (quiet hours / DND still delay it). Data: {"kind": "study_reminder", "goal_minutes",
        # "days_to_exam"}. 14 chars (< 30 ceiling). Falls in the "learning" settings group.
        STUDY_REMINDER = "study_reminder", "Study Reminder"
        # R9 / N6b - receipt for a coin top-up / paid pass (core/notification_receipts.py). Locked (money moved), never
        # cap-gated, route -> wallet. 16 chars (< 30). Choices-only change; see migration core/0035.
        PURCHASE_RECEIPT = "purchase_receipt", "Purchase Receipt"
        # R5 / S3b - "a new device signed in to your account" (login/login_alerts.py). Locked (account safety, never
        # muted per type; the account-level `UserPreference.login_alerts` switch still decides whether it is sent at
        # all), URGENT tier, route -> sessions (Settings > Security > Devices, "Log out this device"). Choices-only
        # change; see migration core/0037. 16 chars (< 30). Data: {"kind": "new_device", "device_name", "ip_address",
        # "method", "at"}.
        NEW_DEVICE_LOGIN = "new_device_login", "New Device Sign-in"
        # P-F3_F4 - "N saved items are waiting in your Revision deck" (core/habit_loop.py, run_revision_reminders). A
        # NON-urgent reminder: counts against the N6 daily cap + same-type collapse, quiet hours / DND delay it, Focus
        # mode drops it, Snooze / Turn off buttons ride along. Route -> home (the Home habit card has the Revise button).
        # Choices-only change; see migration core/0040. 22 chars (< 30). Data: {"kind": "revision_deck", "deck_size",
        # "oldest_days"}.
        REVISION_DECK_REMINDER = "revision_deck_reminder", "Revision Deck Reminder"
        # --- P-F14 parent engagement: the weekly "how was the week" summary for a parent (campus/tasks.py
        # send_parent_weekly_summaries). 20 chars, within max_length=30. Choices-only change -> core/migrations/0043. ---
        CAMPUS_PARENT_WEEKLY = "campus_parent_weekly", "Weekly Parent Report"

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

    # N4 - inbox "archive" (POST notifications/bulk/ action=archive). Archived rows are hidden from the default
    # list, and archiving also marks the row read, so unread counts / badges never include them. Not deleted:
    # `?archived=1` lists them, `action=unarchive` brings them back.
    archived_at = models.DateTimeField(null=True, blank=True)

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
        # T1.2 — Doubt Queue answer alerts live in the message app too.
        NotifType.DOUBT_ANSWERED,
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
        NotifType.CAMPUS_STUDENT_ABSENT, NotifType.CAMPUS_PARENT_WEEKLY,
        NotifType.HOMEWORK_DIARY_POSTED, NotifType.HOMEWORK_DIARY_DUE,
        NotifType.CAMPUS_SUBSTITUTE_ASSIGNED, NotifType.CAMPUS_CLASS_SUBSTITUTED,
        NotifType.CAMPUS_SUBSTITUTE_CANCELLED,
        NotifType.CAMPUS_LEAVE_REQUESTED, NotifType.CAMPUS_LEAVE_DECIDED,
        NotifType.CAMPUS_PTM_SCHEDULED, NotifType.CAMPUS_PTM_BOOKED,
        NotifType.CAMPUS_PTM_CANCELLED, NotifType.CAMPUS_PTM_REMINDER,
        NotifType.CAMPUS_PARENT_MESSAGE, NotifType.CAMPUS_RESOURCE_POSTED,
        NotifType.CAMPUS_RECORDING_READY, NotifType.CAMPUS_NOTE_POSTED,
        NotifType.CAMPUS_FEE_PAID, NotifType.CAMPUS_EMERGENCY, NotifType.CAMPUS_SAFETY,
    })

    #: testseries / assigments-app / campus-gamification — same
    #: "which NotifType values came from app X" pattern as the two sets
    #: above, for testseries's own notification-list routing.
    TESTSERIES_APP_TYPES = frozenset({
        NotifType.TESTSERIES_POSTED, NotifType.TESTSERIES_CHECKED,
        NotifType.TESTSERIES_PAYOUT_RELEASED, NotifType.TESTSERIES_REVIEW_RECEIVED,
        NotifType.TESTSERIES_QUERY_RECEIVED, NotifType.TESTSERIES_QUERY_ANSWERED,
        NotifType.TESTSERIES_DUE_SOON,  # N6
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
    #: N4 - FILTER-ONLY chip ("Money" tab). Deliberately NOT in CATEGORY_TYPES: those sets are mutually exclusive
    #: (category_for() returns one value per row), while payments types already sit in "classroom". Its types =
    #: the N1 settings group "payments" (one catalogue, so the inbox chip and the settings matrix never drift).
    CATEGORY_MONEY = "money"
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
            NotifType.DOUBT_ANSWERED,
        }),
    }

    #: N1 - notification-SETTINGS groups. ONE catalogue (names of NotifType members, resolved safely) that covers
    #: every type; whatever is not listed lands in "other" so a type added later still has a row in the matrix.
    #: Different from CATEGORY_TYPES above (that one drives the inbox filter chips).
    PREFERENCE_GROUP_NAMES = {
        "classes": (
            "JOIN_REQUEST_RECEIVED", "JOIN_REQUEST_ACCEPTED", "JOIN_REQUEST_REJECTED", "SESSION_REMINDER",
            "SESSION_LIVE", "SESSION_CANCELLED", "WAITLIST_PROMOTED", "CLASSROOM_FLAGGED", "CLASSROOM_SHARED",
            "STAFF_ADDED", "CERTIFICATE_ISSUED", "REVIEW_POSTED", "REPORT_REVIEWED", "QUERY_ANSWERED",
            "CAMPUS_SESSION_SCHEDULED", "CAMPUS_SESSION_LIVE", "CAMPUS_CLASS_STARTING", "CAMPUS_RECORDING_READY",
        ),
        "assignments_tests": (
            "assigments_GRADED", "assigments_POSTED", "assigments_POSTED_CAMPUS", "assigments_DUE_REMINDER",
            "assigments_DUE_SOON", "SUBMISSION_RECEIVED", "STAFF_assigments_APPROVED", "STAFF_assigments_REJECTED",
            "TESTSERIES_POSTED", "TESTSERIES_CHECKED", "TESTSERIES_REVIEW_RECEIVED", "TESTSERIES_QUERY_RECEIVED",
            "TESTSERIES_QUERY_ANSWERED", "HOMEWORK_DIARY_POSTED", "HOMEWORK_DIARY_DUE", "RESULT_PUBLISHED",
            "TESTSERIES_DUE_SOON",
        ),
        "messages_calls": (
            "CHAT_MESSAGE", "MENTION", "INCOMING_CALL", "PARENT_DEVICE_PENDING", "DOUBT_ANSWERED",
            "FEED_DOUBT_FOR_TEACHER", "CAMPUS_DOUBT_POSTED", "CAMPUS_DOUBT_REPLIED", "PARENT_MESSAGE_RECEIVED",
            "PARENT_MESSAGE_REPLIED", "CAMPUS_PARENT_MESSAGE", "SUPPORT_REPLY",
        ),
        "social": (
            "POST_LIKED", "POST_COMMENTED", "FOLLOW_REQUEST_RECEIVED", "FOLLOW_REQUEST_ACCEPTED",
            "NEW_POST_FROM_FOLLOWED", "STORY_MENTION", "POST_TAG", "STORY_REACTION", "POST_REPOSTED",
            "CREATOR_FEEDBACK",
        ),
        "learning": (
            "STREAK_MILESTONE_REACHED", "STREAK_AT_RISK", "WEEKLY_RECAP_READY", "CONTENT_DIGEST",
            "CAMPUS_REWARD_EARNED", "REFERRAL_REWARD", "STUDY_REMINDER", "REVISION_DECK_REMINDER",
        ),
        "payments": (
            "PASS_REFUNDED", "PASS_GIFT_RECEIVED", "PASS_GIFT_CLAIMED", "PASS_AUTO_RENEWED", "AUTO_RENEW_FAILED",
            "PASS_GIFT_EXPIRED", "WITHDRAWAL_APPROVED", "WITHDRAWAL_REJECTED", "WITHDRAWAL_PAID",
            "TESTSERIES_PAYOUT_RELEASED", "CAMPUS_FEE_PAID", "FEE_DUE_REMINDER", "FEE_OVERDUE_REMINDER",
            "PURCHASE_RECEIPT",
        ),
        "campus": (
            "NOTICE_POSTED", "LOW_ATTENDANCE_ALERT", "CAMPUS_STUDENT_ABSENT", "CAMPUS_SUBSTITUTE_ASSIGNED",
            "CAMPUS_CLASS_SUBSTITUTED", "CAMPUS_SUBSTITUTE_CANCELLED", "CAMPUS_LEAVE_REQUESTED",
            "CAMPUS_LEAVE_DECIDED", "CAMPUS_PTM_SCHEDULED", "CAMPUS_PTM_BOOKED", "CAMPUS_PTM_CANCELLED",
            "CAMPUS_PTM_REMINDER", "CAMPUS_RESOURCE_POSTED", "CAMPUS_NOTE_POSTED", "CAMPUS_ADMISSION", "CAMPUS_PARENT_WEEKLY",
        ),
        "safety": (
            "CAMPUS_EMERGENCY", "CAMPUS_SAFETY", "COPYRIGHT_NOTICE", "COPYRIGHT_DECISION", "COPYRIGHT_STANDING",
            "COPYRIGHT_STAFF", "REPORT_OUTCOME", "NEW_DEVICE_LOGIN",
        ),
    }
    PREFERENCE_GROUP_ORDER = (
        "classes", "assignments_tests", "messages_calls", "social", "learning", "payments", "campus", "safety", "other",
    )

    #: N1 - types the user can NEVER switch off (push + bell row stay on; the master push toggle is still honoured):
    #: emergency / safety / copyright + moderation outcomes, a parent-device approval, money that left or failed.
    LOCKED_TYPE_NAMES = (
        "CAMPUS_EMERGENCY", "CAMPUS_SAFETY", "COPYRIGHT_NOTICE", "COPYRIGHT_DECISION", "COPYRIGHT_STANDING",
        "COPYRIGHT_STAFF", "REPORT_OUTCOME", "PARENT_DEVICE_PENDING", "WITHDRAWAL_APPROVED", "WITHDRAWAL_REJECTED",
        "WITHDRAWAL_PAID", "AUTO_RENEW_FAILED", "PURCHASE_RECEIPT", "NEW_DEVICE_LOGIN",
    )

    @classmethod
    def preference_groups(cls) -> dict:
        """{group_key: [notif_type value, ...]} for EVERY NotifType (unlisted -> "other"). Resolved lazily so a
        renamed / removed enum member just drops out instead of breaking import."""
        out = {k: [] for k in cls.PREFERENCE_GROUP_ORDER}
        seen = set()
        for key, names in cls.PREFERENCE_GROUP_NAMES.items():
            for name in names:
                member = getattr(cls.NotifType, name, None)
                if member is not None and member.value not in seen:
                    out[key].append(member.value)
                    seen.add(member.value)
        out["other"] = [v for v in cls.NotifType.values if v not in seen]
        return out

    @classmethod
    def locked_types(cls) -> frozenset:
        return frozenset(
            m.value for m in (getattr(cls.NotifType, n, None) for n in cls.LOCKED_TYPE_NAMES) if m is not None
        )

    @classmethod
    def category_for(cls, notif_type) -> str:
        """notif_type -> "mentions" | "follows" | "classroom" | "tests" |
        "other". Unknown/future types -> "other"."""
        for category, types in cls.CATEGORY_TYPES.items():
            if notif_type in types:
                return category
        return cls.CATEGORY_OTHER

    @classmethod
    def money_types(cls) -> frozenset:
        """N4 - NotifType values of the "Money" inbox tab (= the N1 "payments" settings group)."""
        return frozenset(cls.preference_groups().get("payments", ()))

    @classmethod
    def filter_by_category(cls, qs, category):
        """Shared by the list and unread-count endpoints (same pattern as
        the ?source= helper in views.py). An unknown/empty category is
        ignored (returns qs untouched) so a stale or typo'd client param
        never turns into an empty screen or a 400.

        N4: `classes` is accepted as an alias of `classroom`, and `money` is a filter-only chip (see
        CATEGORY_MONEY)."""
        if category == "classes":
            category = cls.CATEGORY_CLASSROOM
        if category == cls.CATEGORY_MONEY:
            return qs.filter(notif_type__in=cls.money_types())
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

    # N1 - per-type x per-channel switches. Stores ONLY deviations from "on":
    #   {"post_liked": {"push": false, "email": false}, "chat_message": {"in_app": false}}
    # channels: push | email | sms | whatsapp | in_app. The global toggles above are MASTER switches (an override can
    # only switch a channel OFF for one type, never on when the master is off). LOCKED types ignore push/in_app = false.
    channel_overrides = models.JSONField(default=dict, blank=True)

    # N9 - sound / vibration choice per Android channel (= notification category: chat_messages, classes, tests, social,
    # wallet, silent, announcements, calls, notifications). Stores ONLY deviations from the default:
    #   {"social": {"sound": "gentle", "vibrate": false}, "classes": {"sound": "silent"}}
    # sound: default | gentle (plays, but no heads-up pop-over) | silent. vibrate: bool. Validation + locked channels
    # (calls / notifications can never be fully silent) live in core/notification_center.py.
    sound_prefs = models.JSONField(default=dict, blank=True)

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

    # --- N5 - smart delivery -------------------------------------------------------------------------------
    # `timezone` above is kept in sync with the phone: "auto" = the app reports the device zone on login / app open
    # (core/notification_delivery.sync_timezone) and the server follows it (travel, new phone). "manual" = the user
    # picked a zone by hand; device reports are then ignored until they switch back to auto.
    class TimezoneMode(models.TextChoices):
        AUTO = "auto", "Auto"
        MANUAL = "manual", "Manual"

    timezone_mode = models.CharField(max_length=6, choices=TimezoneMode.choices, default=TimezoneMode.AUTO)
    timezone_synced_at = models.DateTimeField(null=True, blank=True)
    # Focus / exam mode (UserPreference.exam_mode): while it is active only URGENT-tier types may push (live class,
    # test / assignment due, calls, safety). Bell rows are untouched. Switch off to keep every push in exam mode.
    focus_priority_only = models.BooleanField(default=True)
    # Roll-up: LOW-tier types (likes, reactions, "new post from ...") never push; ONE daily summary push replaces
    # them (core/notification_delivery.send_rollups). Opt-in, so nobody silently loses like-pushes after a deploy.
    low_priority_rollup = models.BooleanField(default=False)
    last_rollup_at = models.DateTimeField(null=True, blank=True)

    # --- H18 — study digest ("Physics: 5 new notes", "3 new posts in a topic you follow") ---------------
    # Separate from `digest_frequency` above (that one is the EMAIL summary of missed bell rows). This is a
    # content digest: ONE bell row + push per period, sent at `content_digest_hour` in the user's `timezone`
    # (a default "UTC" falls back to the server time zone), never during quiet hours / DND (the push waits
    # for the window to end, see core/content_digest.py), skipped entirely when the type is muted or both
    # sections are switched off, and never sent empty.
    class ContentDigestFrequency(models.TextChoices):
        OFF = "off", "Off"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"

    content_digest_frequency = models.CharField(
        max_length=10, choices=ContentDigestFrequency.choices, default=ContentDigestFrequency.WEEKLY,
    )
    content_digest_hour = models.PositiveSmallIntegerField(default=18, help_text="Local hour 0-23 to send at.")
    content_digest_notes = models.BooleanField(default=True, help_text="Include 'N new notes in <subject>'.")
    content_digest_topics = models.BooleanField(default=True, help_text="Include 'N new posts in <followed topic>'.")
    last_content_digest_at = models.DateTimeField(null=True, blank=True)
    content_digest_checked_on = models.DateField(null=True, blank=True)  # last local day we looked (empty or sent)

    # --- N8 - e-mail verification -----------------------------------------------------------------------------
    # Per-event e-mail goes ONLY to an address the user proved they own (6-digit code, core/channel_policy.py). The
    # verified address is stored next to the proof; changing User.email makes it stale (email_verified == False) until
    # the new address is verified again. SMS / WhatsApp use User.phone_verified + a ChannelConsent row instead.
    email_verified_address = models.CharField(max_length=254, blank=True, default="")
    email_verified_at = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    #: Types that ignore quiet hours / DND (a missed call or a pending
    #: parent-device approval is useless once it's hours old). A type the
    #: user explicitly MUTED still stays muted — see allowed_channels_for.
    #: There is no dedicated security NotifType yet; add it here when one
    #: exists.
    QUIET_BYPASS_TYPES = frozenset({
        Notification.NotifType.INCOMING_CALL,
        Notification.NotifType.PARENT_DEVICE_PENDING,
        Notification.NotifType.CAMPUS_EMERGENCY,
    })
    #: Types a user cannot opt out of: an emergency alert must reach everyone in scope, so even a
    #: user who muted the type still gets it (their push toggle / email toggle are still honoured).
    NEVER_MUTE_TYPES = frozenset({
        Notification.NotifType.CAMPUS_EMERGENCY,
    }) | Notification.locked_types()  # N1: + safety / copyright / money-failed / parent-device (see LOCKED_TYPE_NAMES)
    #: N1 - every channel a per-type override may address.
    OVERRIDE_CHANNELS = ("push", "email", "sms", "whatsapp", "in_app")
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
        if notif_type in (self.muted_types or []) and notif_type not in self.NEVER_MUTE_TYPES:
            return []
        allowed = [c for c in ("push", "email", "sms", "whatsapp") if self.channel_allowed(notif_type, c)]
        if self.push_block_reason(notif_type, now):  # N5: focus mode / low-tier roll-up / DND / quiet hours
            allowed = [c for c in allowed if c not in self.QUIET_SILENCED_CHANNELS]
        return allowed

    # ---- N5 - why is an interruptive send (push / sms / whatsapp) held back right now? ------------------------
    def focus_active(self) -> bool:
        """Is the user's Focus / exam mode on (UserPreference.exam_mode, exam date not passed)? One light query,
        cached on the instance (use prime_focus() to fill the cache for many rows with ONE query)."""
        cached = getattr(self, "_focus_active_cache", None)
        if cached is not None:
            return cached
        try:
            from user_profile.models import UserPreference  # lazy: core must not import user_profile at load time

            up = UserPreference.objects.filter(user_id=self.user_id).first()
            value = bool(up and up.focus_now(tz=self._focus_tz()))  # S8: Exam Mode OR a focus-schedule window
        except Exception:  # noqa: BLE001 - a settings glitch must never stop a notification
            value = False
        self._focus_active_cache = value
        return value

    def _focus_tz(self):
        """S8: tzinfo used for the focus-schedule windows (None = server timezone, same rule as the study digest)."""
        from user_profile import learning_prefs

        return learning_prefs.tz_from_name(self.timezone)

    @classmethod
    def prime_focus(cls, prefs) -> None:
        """One query for a whole batch of preference rows (split_push_audience & co.)."""
        prefs = [p for p in prefs if p is not None]
        if not prefs:
            return
        try:
            from django.db.models import Q
            from user_profile.models import UserPreference

            rows = {
                up.user_id: up
                for up in UserPreference.objects.filter(
                    Q(exam_mode=True) | Q(focus_schedule_enabled=True), user_id__in=[p.user_id for p in prefs],
                )
            }
        except Exception:  # noqa: BLE001
            rows = {}
        for p in prefs:
            up = rows.get(p.user_id)
            try:
                p._focus_active_cache = bool(up and up.focus_now(tz=p._focus_tz()))  # S8: Exam Mode OR schedule window
            except Exception:  # noqa: BLE001
                p._focus_active_cache = False

    def push_block_reason(self, notif_type: str, now=None):
        """None (push may go out) or why it is held back:
             "focus" | "low"   PERMANENT for this row - the push is dropped, the bell row stays (callers: skip)
             "dnd"   | "quiet" TEMPORARY - callers that can wait (streak / study digest) retry after the window.
        Permanent reasons are checked first so nothing is deferred just to be dropped later. Quiet-bypass types
        (calls, emergencies, parent approvals) ignore dnd / quiet; urgent-tier types ignore focus mode; locked
        types are never LOW, so a safety alert can never be rolled up."""
        from . import notification_priority as prio

        now = now or timezone.now()
        tier = prio.priority_for(notif_type)
        if tier != prio.URGENT and self.focus_priority_only and self.focus_active():
            return "focus"
        if tier == prio.LOW and self.low_priority_rollup:
            return "low"
        if notif_type not in self.QUIET_BYPASS_TYPES and self.is_quiet_now(now):
            return "dnd" if (self.dnd_until and now < self.dnd_until) else "quiet"
        return None

    # ---- N1 - per-type x per-channel ------------------------------------------------------------------
    def override_for(self, notif_type: str) -> dict:
        ov = (self.channel_overrides or {}).get(str(notif_type))
        return ov if isinstance(ov, dict) else {}

    def channel_allowed(self, notif_type: str, channel: str) -> bool:
        """May `channel` (push | email | sms | whatsapp | in_app) be used for `notif_type`? Order: master toggle ->
        muted type -> per-type override. Locked types always keep push + in_app (push still needs the master)."""
        locked = notif_type in self.NEVER_MUTE_TYPES
        if channel == "in_app":
            return locked or self.override_for(notif_type).get("in_app", True) is not False
        master = {
            "push": self.push_enabled, "email": self.email_enabled,
            "sms": self.sms_enabled, "whatsapp": self.whatsapp_enabled,
        }.get(channel)
        if not master:
            return False
        if notif_type in (self.muted_types or []) and not locked:
            return False
        if locked and channel == "push":
            return True
        return self.override_for(notif_type).get(channel, True) is not False

    def in_app_allowed(self, notif_type: str) -> bool:
        return self.channel_allowed(notif_type, "in_app")

    def types_blocked_for(self, channel: str) -> list:
        """Every notif_type whose `channel` is switched off (muted types + per-type overrides) - for queries that must
        exclude them (e.g. the email digest)."""
        blocked = {t for t in (self.muted_types or []) if t not in self.NEVER_MUTE_TYPES}
        for t, ov in (self.channel_overrides or {}).items():
            if isinstance(ov, dict) and ov.get(channel, True) is False and t not in self.NEVER_MUTE_TYPES:
                blocked.add(t)
        return sorted(blocked)

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


class NotificationScopeMute(models.Model):
    """N9 - `user` silenced the PUSH of ONE class / chat / post, for a while or for good ("Is class ke notifications 8h ke
    liye mute").

    Different from the two mutes above (N9b: `hide_bell=True` also skips the bell row; `section` = a campus section):
      * NotificationMute (N6)        = a PERSON, drops the bell row too.
      * NotificationPreference types = a TYPE for everything.
      * this                         = one THING (class / conversation / post). Only the buzz (push + in-app banner) is
                                       dropped - the bell row stays, so nothing is lost (same contract as DND). Types the
                                       user can never silence (locked / safety / calls / emergencies) ignore it.
    `muted_until` NULL = until the user unmutes. Expired rows are inert at once (every check compares with now) and
    are deleted by the daily history task. `label` is the name the app showed ("Physics Batch A") so the "muted"
    list can be drawn without more lookups.
    """

    SCOPE_POST = "post"
    SCOPE_CLASS = "class"
    SCOPE_CONVERSATION = "conversation"
    SCOPE_SECTION = "section"
    SCOPE_CHOICES = (
        (SCOPE_POST, "Post / reel"),
        (SCOPE_CLASS, "Class"),
        (SCOPE_CONVERSATION, "Chat"),
        (SCOPE_SECTION, "Campus section"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notification_scope_mutes")
    scope_type = models.CharField(max_length=12, choices=SCOPE_CHOICES)
    scope_id = models.CharField(max_length=40)  # int pk (post / classroom) or UUID (conversation), always a string
    muted_until = models.DateTimeField(null=True, blank=True, db_index=True)
    label = models.CharField(max_length=120, blank=True, default="")
    # N9b - optional stronger mute: while it lasts the bell row is not written either (default = push-only, as before).
    hide_bell = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_notification_scope_mute"
        constraints = [
            models.UniqueConstraint(fields=["user", "scope_type", "scope_id"], name="uniq_notification_scope_mute"),
        ]

    def __str__(self):
        return f"{self.user_id} muted {self.scope_type}:{self.scope_id}"

    def is_active(self, now=None) -> bool:
        return self.muted_until is None or self.muted_until > (now or timezone.now())


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



# ======================================================================
# T1.8 (R8) — remote app config: force-update + maintenance switch.
# ----------------------------------------------------------------------
# Flutter app launch/resume pe public `GET /core/app-config/` call karta hai
# (core/views.py::AppConfigView). Pehle koi kill-switch nahi tha: ek buggy
# ya insecure build ko users se hatane ka tareeka sirf "umeed hai wo update
# kar lenge" tha. Ab ops admin me `min_supported_version` badha de to purane
# builds ko blocking "Update required" dialog milta hai — bina naye release
# ke. Blank field = "settings/env default use karo" (core/app_config.py).
# Ek platform = ek row.
# ======================================================================
class AppConfig(models.Model):
    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    platform = models.CharField(max_length=10, choices=Platform.choices, unique=True)

    # Dotted numeric versions, e.g. "1.4.2" (pubspec `version:` ka `+build`
    # hissa ignore hota hai).
    min_supported_version = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Isse purane builds ko blocking 'Update required' dialog milta hai. Blank = env default.",
    )
    latest_version = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Isse purane builds ko dismissable 'Update available' nudge milta hai. Blank = env default.",
    )
    store_url = models.URLField(
        blank=True, default="",
        help_text="Play Store / App Store link. Blank = PLAY_STORE_URL / APP_STORE_URL setting.",
    )
    update_message = models.CharField(max_length=300, blank=True, default="")
    update_message_hi = models.CharField(max_length=300, blank=True, default="")

    maintenance_mode = models.BooleanField(
        default=False,
        help_text="True => app har user ko blocking 'Maintenance' screen dikhata hai (retry button ke saath).",
    )
    maintenance_message = models.CharField(max_length=300, blank=True, default="")
    maintenance_message_hi = models.CharField(max_length=300, blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "core_app_config"
        verbose_name = "App config"
        verbose_name_plural = "App configs"

    def __str__(self):
        return f"AppConfig[{self.platform}] min={self.min_supported_version or '-'} latest={self.latest_version or '-'}"


class FeatureFlag(models.Model):
    """P-J2 - remote kill switch / staged rollout for one risky feature (live, reels_upload, coin_purchase, ai_features,
    cashout). Edited in Django admin, no release needed. Resolution + caching: common/feature_flags.py.

    No row = the default (ON, except `cashout` which is OFF unless COIN_CASHOUT_ENABLED is true in the environment).
    A row can only TIGHTEN the cashout switch - it can never switch money-out on by itself.
    """

    class Key(models.TextChoices):
        LIVE = "live", "Live classes / live streams"
        REELS_UPLOAD = "reels_upload", "Reels upload"
        COIN_PURCHASE = "coin_purchase", "Coin purchase (top-up)"
        AI_FEATURES = "ai_features", "AI features"
        CASHOUT = "cashout", "Coin cash-out (needs env COIN_CASHOUT_ENABLED=true too)"
        SOCIAL_LEARNING = "social_learning", "Social learning (study-room Pomodoro, study partner, seasons)"  # P-F5, server-side only
        SPACED_REPETITION = "spaced_repetition", "Spaced repetition (Revision deck SM-2 review)"  # P-F8, server-side only

    class Platform(models.TextChoices):
        ALL = "all", "All platforms"
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    key = models.CharField(max_length=32, choices=Key.choices)
    platform = models.CharField(max_length=10, choices=Platform.choices, default="all")
    enabled = models.BooleanField(
        default=True,
        help_text="OFF = kill switch: the app hides the feature AND the server refuses it (403 feature_disabled) for everyone.",
    )
    rollout_percent = models.PositiveSmallIntegerField(
        default=100,
        help_text="0-100. Staged rollout: share of devices that SEE the feature (stable per device). 100 = everyone. "
                  "Only the app hides it for the rest; the server refuses only when the switch above is OFF.",
    )
    min_app_version = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Optional, e.g. 1.5.0. Older builds do not get the feature (the app hides it).",
    )
    message = models.CharField(max_length=300, blank=True, default="", help_text="Shown instead of the feature when it is off (English).")
    message_hi = models.CharField(max_length=300, blank=True, default="", help_text="Same, Hindi.")
    note = models.CharField(max_length=300, blank=True, default="", help_text="Internal: why it was changed (incident / ticket).")
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "core_feature_flag"
        verbose_name = "Feature flag"
        verbose_name_plural = "Feature flags"
        constraints = [models.UniqueConstraint(fields=["key", "platform"], name="uniq_feature_flag_key_platform")]
        ordering = ["key", "platform"]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.rollout_percent is not None and not 0 <= int(self.rollout_percent) <= 100:
            raise ValidationError({"rollout_percent": "Must be between 0 and 100."})
        if self.min_app_version:
            from .app_config import parse_version

            if parse_version(self.min_app_version) is None:
                raise ValidationError({"min_app_version": "Use a dotted number like 1.5.0."})

    def __str__(self):
        state = "ON" if self.enabled else "OFF"
        return f"{self.key}[{self.platform}] {state} {self.rollout_percent}%"


# T3.8 -- lifecycle funnel events (kept in its own module, re-exported here so Django discovers it)
from .models_funnel import FunnelEvent  # noqa: E402,F401


class ChannelConsent(models.Model):
    """N8 - append-only consent ledger for the paid / personal channels (sms, whatsapp, email). DPDP Act 2023: we must be
    able to show WHO agreed to WHAT text, WHEN, from where - and when they took it back. Rows are never edited; the
    newest row per (user, channel) is the current state (granted True / False). `address` is the phone / e-mail the
    consent was given for, so a number change invalidates it (the user has to opt in again for the new number)."""

    class Channel(models.TextChoices):
        EMAIL = "email", "E-mail"
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="channel_consents")
    channel = models.CharField(max_length=10, choices=Channel.choices)
    granted = models.BooleanField()
    address = models.CharField(max_length=254, blank=True, default="")
    text_version = models.CharField(max_length=20, blank=True, default="")
    source = models.CharField(max_length=20, blank=True, default="app")  # app | unsubscribe_link | stop_reply | bounce | system
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_channel_consent"
        indexes = [models.Index(fields=["user", "channel", "-created_at"], name="chconsent_user_ch_idx")]
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.user_id} {self.channel} {'granted' if self.granted else 'withdrawn'}"


class ChannelSuppression(models.Model):
    """N8 - an address we must NOT send to on a channel any more: hard bounce, spam complaint, one-click unsubscribe,
    SMS STOP reply, invalid number. Keyed by the (normalised) address, not the user, so a second account with the same
    e-mail does not bring the mails back. Cleared only by the owner re-verifying / re-consenting (clear_suppression)."""

    class Reason(models.TextChoices):
        HARD_BOUNCE = "hard_bounce", "Hard bounce"
        SOFT_BOUNCE_LIMIT = "soft_bounce_limit", "Repeated soft bounces"
        COMPLAINT = "complaint", "Spam complaint"
        UNSUBSCRIBE = "unsubscribe", "Unsubscribed"
        STOP = "stop", "STOP reply"
        INVALID = "invalid", "Invalid address / number"

    channel = models.CharField(max_length=10)
    address = models.CharField(max_length=254)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    detail = models.CharField(max_length=200, blank=True, default="")
    soft_bounces = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_channel_suppression"
        constraints = [models.UniqueConstraint(fields=["channel", "address"], name="uniq_channel_suppression")]

    def __str__(self):
        return f"{self.channel}:{self.address} ({self.reason})"


class ChannelSendLog(models.Model):
    """N8 - one row per e-mail / SMS / WhatsApp actually handed to the provider. Drives the per-user daily cap, the
    global cost guard and 'how many did we send today' in the settings screen. Old rows are pruned by
    core.channel_policy.prune_send_log (Celery, daily)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="channel_send_logs")
    channel = models.CharField(max_length=10)
    notif_type = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "core_channel_send_log"
        indexes = [models.Index(fields=["user", "channel", "-created_at"], name="chsendlog_user_ch_idx")]

