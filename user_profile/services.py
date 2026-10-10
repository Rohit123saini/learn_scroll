"""
user_profile/services.py

TASK 2 — thin `_notify(...)` wrapper around
`core.services.create_notification` for the Follow feature
(FollowAPIView / AcceptFollowRequestView in this app's views.py).

No `assigments`/`testseries` services.py actually exists to copy
verbatim, so this follows the *pattern* those apps' docstrings point at
instead: `core.services.create_notification` is lazy-imported, never at
module top. That matters here specifically because the dependency
already runs the other way too — `create_notification` itself
lazy-imports `user_profile.views.is_restricted_between` for the
restrict check (see core/services.py's own docstring) — so a top-level
`from core.services import ...` here would risk a real circular import
depending on Django's app-loading order. Centralizing the lazy import
in this one helper means FollowAPIView/AcceptFollowRequestView don't
each need to repeat that boilerplate.

Same contract as create_notification itself: never raises, never sends
a push/email/sms/whatsapp — in-app bell row only.
"""
import logging

logger = logging.getLogger(__name__)


def blocked_user_ids(user):
    """Ids of everyone in a block relationship with `user`, EITHER direction
    (I blocked them, or they blocked me). `user` may be a User or a raw id.
    The one helper other apps (core, post, message) use instead of each
    querying BlockUser their own way. Never raises."""
    from .models import BlockUser

    uid = getattr(user, "id", user)
    if uid is None:
        return set()
    try:
        ids = set(BlockUser.objects.filter(blocker_id=uid).values_list("blocked_id", flat=True))
        ids |= set(BlockUser.objects.filter(blocked_id=uid).values_list("blocker_id", flat=True))
        return ids
    except Exception:
        logger.exception("blocked_user_ids failed (user=%s)", uid)
        return set()


def is_blocked_pair(user_a, user_b) -> bool:
    """True if either user blocked the other (ids or User objects)."""
    from .models import BlockUser
    from django.db.models import Q

    a, b = getattr(user_a, "id", user_a), getattr(user_b, "id", user_b)
    if a is None or b is None:
        return False
    return BlockUser.objects.filter(
        Q(blocker_id=a, blocked_id=b) | Q(blocker_id=b, blocked_id=a)
    ).exists()


class ReportRateLimited(Exception):
    """Too many reports from one account in the last hour."""


REPORTS_PER_HOUR = 30


def file_report(reporter, target_type, target_id, reason, details=""):
    """Create (or find, idempotently) a ContentReport.

    target_type: user | post | comment | story. `target_id` is a user id or a
    post/comment/story uuid. Returns (report, created).
    Raises LookupError (target doesn't exist), ValueError (own content) or
    ReportRateLimited. The reported person is never told anything.
    """
    from datetime import timedelta

    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError
    from django.utils import timezone

    from .models import ContentReport

    T = ContentReport.TargetType
    User = get_user_model()
    target_id = str(target_id)
    reported_user = None
    try:
        if target_type == T.USER:
            reported_user = User.objects.filter(id=int(target_id), is_active=True).first()
        elif target_type == T.POST:
            from post.models import Post

            obj = Post.objects.filter(id=target_id, is_deleted=False).select_related("user").first()
            reported_user = obj.user if obj else None
        elif target_type == T.COMMENT:
            from post.models import PostComment

            obj = PostComment.objects.filter(id=target_id, is_deleted=False).select_related("user").first()
            reported_user = obj.user if obj else None
        elif target_type == T.STORY:
            from post.models import Story

            obj = Story.objects.filter(id=target_id, is_deleted=False).select_related("user").first()
            reported_user = obj.user if obj else None
    except (ValueError, TypeError, ValidationError):
        reported_user = None
    if reported_user is None:
        raise LookupError("Nothing to report here.")
    if reported_user.id == reporter.id:
        raise ValueError("You can't report your own account or content.")

    existing = ContentReport.objects.filter(reporter=reporter, target_type=target_type, target_id=target_id).first()
    if existing is not None:
        return existing, False

    recent = ContentReport.objects.filter(
        reporter=reporter, created_at__gte=timezone.now() - timedelta(hours=1)
    ).count()
    if recent >= REPORTS_PER_HOUR:
        raise ReportRateLimited()

    report = ContentReport.objects.create(
        reporter=reporter, reported_user=reported_user, target_type=target_type,
        target_id=target_id, reason=reason, details=(details or "")[:1000],
    )
    # AUTOMATION: exact reported_count + auto-hold at the thresholds (never raises).
    from .report_automation import on_report_created

    on_report_created(report)
    return report, True


def block_new_accounts_of(previous_owner, new_user, max_age_days=30):
    """The push token of `previous_owner` just moved to `new_user` (same
    phone, new account). Everyone who blocked `previous_owner` with
    "also block new accounts" now blocks `new_user` too — but only if
    `new_user` really is a new account (<= max_age_days old), so a friend
    briefly logging in on a shared phone is not caught. Chain-safe: the new
    block carries the flag, so the NEXT new account is caught as well.
    Returns the number of blocks created. Never raises."""
    from datetime import timedelta

    from django.utils import timezone

    from .models import BlockUser

    try:
        if previous_owner is None or new_user is None or previous_owner.id == new_user.id:
            return 0
        if new_user.date_joined < timezone.now() - timedelta(days=max_age_days):
            return 0
        blockers = list(
            BlockUser.objects.filter(blocked_id=previous_owner.id, block_new_accounts=True)
            .values_list("blocker_id", flat=True)
        )
        created_count = 0
        for blocker_id in blockers:
            if blocker_id == new_user.id:
                continue
            _, created = BlockUser.objects.get_or_create(
                blocker_id=blocker_id, blocked_id=new_user.id, defaults={"block_new_accounts": True},
            )
            created_count += int(created)
        return created_count
    except Exception:
        logger.exception("block_new_accounts_of failed (%s -> %s)", getattr(previous_owner, "id", None), getattr(new_user, "id", None))
        return 0


def push_for_row(notification):
    """Rich push for a saved core.Notification row (prefs / muted types / quiet
    hours are honoured inside). Never raises; None is a no-op."""
    if notification is None:
        return
    try:
        from message.push_utils import send_push_for_notification

        send_push_for_notification(notification)
    except Exception:
        logger.exception("follow push failed (notification=%s)", getattr(notification, "id", None))


def _notify(recipient, notif_type, title, message="", *, actor=None, data=None, push=False):
    """Fire-and-forget in-app notification for a Follow event.

    `recipient` / `actor` may be User instances or raw ids — both pass
    straight through to create_notification, which accepts either.
    """
    # Truly fire-and-forget, as the docstring promises: the follow/accept
    # itself has already succeeded, so a notification problem (the lazy
    # `core` import failing, or create_notification raising) must be logged
    # and swallowed — never turned into a 500 for the user who just
    # followed someone. (The import is lazy only to avoid an import cycle at
    # app-load time; it is resolved fresh on each call.)
    try:
        from core.services import create_notification

        row = create_notification(
            recipient,
            notif_type,
            title,
            message,
            data=data or {},
            actor=actor,
        )
        if push:
            push_for_row(row)
        return row
    except Exception:
        logger.exception("follow notification failed (recipient=%s, type=%s)", recipient, notif_type)
        return None


# ---------------------------------------------------------------------------
# Issue #2 — restrict lookups for OTHER apps (post / message / core).
#
# `RestrictUser(user=A, restricted=B)` = "A restricted B". These helpers are
# the one place other apps ask that question, so they don't each query the
# table their own way. All of them lazy-import the model (same circular-
# import reason as `_notify` above) and never raise.
#
# Direction cheat-sheet (A restricted B):
#   - B's comments on A's posts are hidden from everyone except B
#   - B's chat/mention pushes + bell rows for A are suppressed
#   - A's read receipts and online/last-seen are hidden FROM B
# B must never be able to detect any of this.
# ---------------------------------------------------------------------------
def restricted_ids_by(user_id):
    """Set of user ids that `user_id` has restricted."""
    from .models import RestrictUser

    if user_id is None:
        return set()
    return set(
        RestrictUser.objects.filter(user_id=user_id).values_list("restricted_id", flat=True)
    )


def restrictor_ids_of(restricted_id, among=None):
    """Set of user ids who have restricted `restricted_id`. Pass `among`
    (an iterable of ids) to look at only those candidates — one indexed
    query however many recipients there are."""
    from .models import RestrictUser

    if restricted_id is None:
        return set()
    qs = RestrictUser.objects.filter(restricted_id=restricted_id)
    if among is not None:
        among = {a for a in among if a is not None}
        if not among:
            return set()
        qs = qs.filter(user_id__in=among)
    return set(qs.values_list("user_id", flat=True))


def has_restricted(restrictor_id, restricted_id):
    """True if `restrictor_id` has restricted `restricted_id` (one-way)."""
    from .models import RestrictUser

    if restrictor_id is None or restricted_id is None:
        return False
    return RestrictUser.objects.filter(user_id=restrictor_id, restricted_id=restricted_id).exists()


# ---------------------------------------------------------------------------
# P11-BE — remove follower + pending follow-request list.
#
# Both lazy-import the model (same circular-import reason as `_notify`).
# ---------------------------------------------------------------------------
def remove_follower(owner, follower_id):
    """Make `follower_id` stop following `owner` (Instagram's "Remove
    follower"). Returns True if an ACCEPTED follow row was removed, False if
    that user wasn't a follower (nothing to do — a still-PENDING request is
    NOT a follower; that's what reject-request/ is for).

    * Counters: the Follow post_delete signal (user_profile/signals.py)
      recounts followers_count/following_count for both users, exactly as
      FollowAPIView's unfollow and BlockedUsersView do — nothing is touched
      by hand here.
    * Notifications: deliberately NONE. `_notify` is not called, so the
      removed user is never told (and the owner's own "X started following
      you" bell row is left alone). They can follow again later; on a
      private account that becomes a fresh PENDING request.

    One DELETE statement filtered on (follower, owner, ACCEPTED) — no
    read-then-delete race, and a double-tap just returns False the 2nd time.
    (QuerySet.delete() still emits post_delete per row since a receiver is
    registered.)
    """
    from .models import Follow

    deleted, _ = Follow.objects.filter(
        follower_id=follower_id,
        following=owner,
        status=Follow.Status.ACCEPTED,
    ).delete()
    return deleted > 0


def pending_follow_requests_for(user):
    """Queryset of PENDING Follow rows addressed TO `user` (incoming
    requests), newest first, with the requester pre-fetched."""
    from .models import Follow

    return (
        Follow.objects.filter(following=user, status=Follow.Status.PENDING)
        .select_related("follower")
        .order_by("-id")
    )


# ---------------------------------------------------------------------------
# P13-BE — Achievements / badges
#
# `award_badge()` is the ONE write path for UserBadge (signals.py and
# tasks.py both call it). Same contract as `_notify`: never raises. Returns
# the new UserBadge when it was awarded just now, None when the user already
# had it / the badge is inactive / something failed — so callers can count
# "newly awarded" without a second query.
# ---------------------------------------------------------------------------
BADGE_DEFAULTS = {
    "streak_7": {
        "title": "7-Day Streak", "icon": "🔥", "sort_order": 10,
        "description": "Opened LearnScroll 7 days in a row.",
    },
    "streak_30": {
        "title": "30-Day Streak", "icon": "🏅", "sort_order": 20,
        "description": "Opened LearnScroll 30 days in a row.",
    },
    "weekly_top10": {
        "title": "Weekly Top 10", "icon": "🏆", "sort_order": 30,
        "description": "Finished in the top 10 of a weekly leaderboard.",
    },
    "first_test": {
        "title": "First Test", "icon": "📝", "sort_order": 40,
        "description": "Completed your first test.",
    },
    "teacher_verified": {
        "title": "Verified Teacher", "icon": "✅", "sort_order": 50,
        "description": "Verified as a teacher by LearnScroll.",
    },
}


def _get_badge(code):
    """Active Badge row for `code`, self-seeding the built-in five."""
    from .models import Badge

    badge = Badge.objects.filter(code=code).first()
    if badge is None:
        default = BADGE_DEFAULTS.get(code)
        if default is None:
            return None
        badge, _ = Badge.objects.get_or_create(
            code=code, defaults={**default, "rule_type": code},
        )
    return badge if badge.is_active else None


def award_badge(user, code, *, context=None, notify=True):
    from .models import UserBadge

    user_id = getattr(user, "pk", user)
    if user_id is None:
        return None
    try:
        badge = _get_badge(code)
        if badge is None:
            return None
        # get_or_create is race-safe (unique_user_badge): of two concurrent
        # callers exactly one gets created=True, so only one notifies.
        user_badge, created = UserBadge.objects.get_or_create(
            user_id=user_id, badge=badge, defaults={"context": context or {}},
        )
    except Exception:
        logger.exception("award_badge failed (user=%s, code=%s)", user_id, code)
        return None

    if not created:
        return None
    if notify:
        _notify_badge_earned(user, user_id, badge)
    return user_badge


def _notify_badge_earned(user, user_id, badge):
    """In-app `core` bell row. `BADGE_EARNED` has to exist on
    core.Notification.NotifType (one line to add there); until it does the
    plain string is used so a missing choice can't break the award."""
    try:
        from core.models import Notification

        notif_type = getattr(Notification.NotifType, "BADGE_EARNED", "badge_earned")
        username = getattr(user, "username", None)
        if username is None:
            from django.contrib.auth import get_user_model

            username = (
                get_user_model().objects.filter(pk=user_id).values_list("username", flat=True).first()
            )
        _notify(
            user_id,
            notif_type,
            f"New badge: {badge.title} {badge.icon}".strip(),
            badge.description,
            # `username` + `badge_code` = what the client needs to deep-link
            # to /profile/<username>/badges/.
            data={"type": "badge_earned", "badge_code": badge.code, "username": username},
        )
    except Exception:
        logger.exception("badge notification failed (user=%s, badge=%s)", user_id, badge.code)


def teacher_verified_field():
    """Name of the boolean User field that means 'teacher-verified'."""
    from django.conf import settings

    return getattr(settings, "BADGE_TEACHER_VERIFIED_FIELD", "is_verified")


def backfill_badges():
    """One-off catch-up for users who already qualify from before this
    feature existed (signals only see FUTURE events). Awards silently — no
    notification burst for old achievements. Idempotent; safe to re-run."""
    from django.contrib.auth import get_user_model

    from .models import Streak

    counts = {"streak_7": 0, "streak_30": 0, "first_test": 0, "teacher_verified": 0}

    # longest_streak, not current: someone who hit 30 last month earned it.
    for user_id, longest in Streak.objects.filter(longest_streak__gte=7).values_list("user_id", "longest_streak").iterator():
        if award_badge(user_id, "streak_7", notify=False):
            counts["streak_7"] += 1
        if longest >= 30 and award_badge(user_id, "streak_30", notify=False):
            counts["streak_30"] += 1

    try:
        from testseries.models import TestAttempt

        student_ids = (
            TestAttempt.objects.filter(status=TestAttempt.Status.CHECKED)
            .values_list("student_id", flat=True).distinct()
        )
        for student_id in student_ids.iterator():
            if award_badge(student_id, "first_test", notify=False):
                counts["first_test"] += 1
    except Exception:
        logger.exception("backfill_badges: first_test pass failed")

    User = get_user_model()
    field = teacher_verified_field()
    if hasattr(User, field):
        for user_id in User.objects.filter(**{field: True}).values_list("pk", flat=True).iterator():
            if award_badge(user_id, "teacher_verified", notify=False):
                counts["teacher_verified"] += 1

    return counts
