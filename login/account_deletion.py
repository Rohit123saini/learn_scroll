"""
T0.8 (R1) — in-app account deletion (Google Play "account deletion" requirement).

DESIGN: *anonymise + erase*, not `user.delete()`.
  A cascading hard delete would also wipe CoinLedger / purchases / withdrawal rows, which must be
  kept for accounting & legal reasons, and could orphan other people's classes/chats. So:

  ERASED immediately
    * every session (AuthToken rows -> the sliding-session auth rejects the old tokens) and push
      device tokens
    * the user's posts, stories, comments, reactions/likes, saved posts, comment likes
    * (S5b) study activity -- streak, time-in-app per day, weekly recaps, badges -- and the person's app / privacy /
      notification settings. These are behavioural data about one person and are part of the "download my data" ZIP,
      so they are erased with the account (the account row alone being anonymised is not enough for them).
    * follow relations (both directions; the other side's counters are corrected)
    * profile photo file
  ANONYMISED in place (row kept so FK-linked financial / class records stay consistent)
    * username -> deleted_<id>; email, phone, names, bio, links, pronouns, DOB, study/exam fields cleared
    * is_active=False, unusable password, staff/superuser flags dropped
  RETAINED (documented in the privacy policy): coin ledger, purchases, withdrawals, payment refs,
    moderation / copyright records — no personal identifiers remain on the user row. The login audit
    trail (AuthAuditLog) is kept for its retention period with identifier / hash / User-Agent blanked.

Refuses (409) while money is in flight: a PENDING/PROCESSING coin withdrawal.
Pure-ish: all DB work in one transaction; safe to call twice (second call is a no-op).
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import F, Q
from django.db.models.functions import Greatest

logger = logging.getLogger(__name__)

# related_name on User -> things to delete outright (guarded: missing names are skipped)
CONTENT_RELATIONS = (
    "posts", "stories", "post_comments", "post_likes", "saved_posts",
    "comment_likes", "post_poll_votes", "post_answers",
)
SESSION_RELATIONS = ("auth_tokens", "device_tokens")

# S5b -- (app label, model name) of per-person rows deleted with the account: study activity + settings.
# Each model has a `user` FK / one-to-one. A model that does not exist in this build is skipped (LookupError only --
# a real database error must still roll the whole erase back).
PERSONAL_ACTIVITY_MODELS = (
    ("user_profile", "Streak"),
    ("user_profile", "DailyUsage"),
    ("user_profile", "WeeklyRecap"),
    ("user_profile", "UserBadge"),
    ("user_profile", "UserPreference"),
    ("user_profile", "PrivacySettings"),
    ("core", "NotificationPreference"),
)


def _erase_personal_activity(user) -> dict:
    from django.apps import apps

    erased = {}
    for app_label, model_name in PERSONAL_ACTIVITY_MODELS:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue
        erased[model_name] = model.objects.filter(user=user).delete()[0]
    return erased


def is_deleted(user) -> bool:
    return (not user.is_active) and user.username.startswith("deleted_")


def blocking_reasons(user) -> list[str]:
    reasons = []
    try:
        from user_profile.models import CoinWithdrawalRequest

        if CoinWithdrawalRequest.objects.filter(
            user=user,
            status__in=[CoinWithdrawalRequest.Status.PENDING, CoinWithdrawalRequest.Status.PROCESSING],
        ).exists():
            reasons.append(
                "You have a coin withdrawal in progress. Wait until it is paid or rejected, then delete your account."
            )
    except Exception:  # pragma: no cover - never let a guard crash the preflight
        logger.exception("account deletion: withdrawal guard failed")
    return reasons


def _erase_follows(user) -> int:
    from user_profile.models import Follow

    User = type(user)
    accepted = Follow.Status.ACCEPTED
    # people WE followed lose a follower; people who followed US lose a following.
    i_followed = list(Follow.objects.filter(follower=user, status=accepted).values_list("following_id", flat=True))
    followed_me = list(Follow.objects.filter(following=user, status=accepted).values_list("follower_id", flat=True))
    if i_followed:
        User.objects.filter(id__in=i_followed).update(followers_count=Greatest(F("followers_count") - 1, 0))
    if followed_me:
        User.objects.filter(id__in=followed_me).update(following_count=Greatest(F("following_count") - 1, 0))
    deleted, _ = Follow.objects.filter(Q(follower=user) | Q(following=user)).delete()
    return deleted


def delete_account(user) -> dict:
    """Irreversible. Caller has already authenticated the user (password / explicit confirm)."""
    if is_deleted(user):
        return {"already_deleted": True}

    summary = {}
    with transaction.atomic():
        # 1. sessions first, so the account is unusable even if a later step fails midway.
        for rel in SESSION_RELATIONS:
            mgr = getattr(user, rel, None)
            if mgr is not None:
                summary[rel] = mgr.all().delete()[0]

        # 2. user-generated content
        for rel in CONTENT_RELATIONS:
            mgr = getattr(user, rel, None)
            if mgr is not None:
                summary[rel] = mgr.all().delete()[0]

        # 2b. S5b: study activity + settings (see module docstring)
        summary["personal_activity"] = _erase_personal_activity(user)

        # 3. social graph
        summary["follows"] = _erase_follows(user)

        # 3b. L1 gap 19: the audit trail of this account's logins stays (security history), but the
        #     masked email/phone, its hash and the User-Agent on those rows go. queryset.update() on
        #     purpose -- AuthAuditLog rows are append-only and refuse save().
        from .models import AuthAuditLog

        summary["audit_scrubbed"] = AuthAuditLog.objects.filter(user=user).update(
            identifier_masked="", identifier_hash="", user_agent=""
        )

        # 4. anonymise the row (photo first: User's pre_save signal deletes the old file)
        user.profile_photo = None
        user.cover_photo = None  # P1 (pre_save signal deletes the file)
        user.location = ""  # P1
        user.verified_reason = ""  # P1
        user.username = f"deleted_{user.pk}"
        user.email = None
        user.phone = None
        user.first_name = ""
        user.last_name = ""
        user.bio = ""
        user.pronouns = ""
        user.category_label = ""
        user.links = []
        user.date_of_birth = None
        user.study_class = ""
        user.target_exam = ""
        user.is_private = True
        user.posts_count = 0
        user.is_verified = False
        user.is_active = False
        user.is_staff = False
        user.is_superuser = False
        user.set_unusable_password()
        user.save()

    logger.info("account deleted user_id=%s erased=%s", user.pk, summary)
    return {"already_deleted": False, "erased": summary}
