"""
core/notification_priority.py - N5: priority TIERS for notification types.

    urgent  the user would be upset to miss it, even while studying: live class starting, test / assignment due,
            incoming call, safety + locked types, attendance / emergency alerts.
    normal  everything else (messages, replies, doubts answered, grades ...).
    low     nice-to-know: likes, reactions, reposts, "new post from someone you follow", milestones, rewards.

Used for two things (see NotificationPreference.push_block_reason):
  * Focus / exam mode  - while UserPreference.exam_mode is on, ONLY `urgent` types may push (bell rows still appear).
  * Low-priority roll-up - with `low_priority_rollup` on, `low` types never buzz; one daily summary push replaces them.

PURE: no DB access. Types are listed by member NAME and resolved lazily, so a renamed / removed NotifType member just
drops out of the set instead of breaking import (same trick as Notification.PREFERENCE_GROUP_NAMES).
A type that is not listed anywhere is `normal`, so a NotifType added later is never silently hidden.
"""
URGENT = "urgent"
NORMAL = "normal"
LOW = "low"
TIERS = (URGENT, NORMAL, LOW)

URGENT_TYPE_NAMES = (
    # live class / session starting
    "SESSION_LIVE", "SESSION_REMINDER", "CAMPUS_SESSION_LIVE", "CAMPUS_CLASS_STARTING",
    # tests + assignments + homework due
    "TESTSERIES_POSTED", "TESTSERIES_DUE_SOON", "assigments_DUE_SOON", "assigments_DUE_REMINDER", "HOMEWORK_DIARY_DUE",
    "RESULT_PUBLISHED",
    # calls + safety (also quiet-hour bypass types)
    "INCOMING_CALL", "PARENT_DEVICE_PENDING", "CAMPUS_EMERGENCY", "CAMPUS_SAFETY", "LOW_ATTENDANCE_ALERT",
    "CAMPUS_STUDENT_ABSENT",
    # S8: the reminder the student set up themselves in Settings > Learning (they asked for it, so Focus must not eat it)
    "STUDY_REMINDER",
    # money that is due / failed
    "FEE_DUE_REMINDER", "FEE_OVERDUE_REMINDER", "AUTO_RENEW_FAILED",
)

LOW_TYPE_NAMES = (
    "POST_LIKED", "STORY_REACTION", "POST_REPOSTED", "NEW_POST_FROM_FOLLOWED", "CLASSROOM_CREATED_BY_FOLLOWED",
    "TESTSERIES_CREATED_BY_FOLLOWED", "FOLLOW_REQUEST_ACCEPTED", "STREAK_MILESTONE_REACHED", "CAMPUS_REWARD_EARNED",
    "REFERRAL_REWARD", "CREATOR_FEEDBACK", "WEEKLY_RECAP_READY",
)


def _resolve(names) -> frozenset:
    from .models import Notification

    out = set()
    for name in names:
        member = getattr(Notification.NotifType, name, None)
        if member is not None:
            out.add(member.value)
    return frozenset(out)


def urgent_types() -> frozenset:
    """Urgent = the list above + every locked (never-mute) type + every quiet-hours-bypass type."""
    from .models import Notification, NotificationPreference

    return _resolve(URGENT_TYPE_NAMES) | Notification.locked_types() | frozenset(NotificationPreference.QUIET_BYPASS_TYPES)


def low_types() -> frozenset:
    """Low = the list above MINUS anything urgent (an urgent / locked type can never be rolled up)."""
    return _resolve(LOW_TYPE_NAMES) - urgent_types()


def priority_for(notif_type) -> str:
    notif_type = str(notif_type)
    if notif_type in urgent_types():
        return URGENT
    if notif_type in low_types():
        return LOW
    return NORMAL
