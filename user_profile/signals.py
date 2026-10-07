"""
user_profile/signals.py

Issue #4 (denormalized counter drift) — the ROOT-CAUSE fix.

`User.followers_count` / `User.following_count` are denormalized copies
of "how many ACCEPTED `Follow` rows point at / away from this user".
Before this module they were kept in sync by hand: FollowAPIView,
AcceptFollowRequestView and BlockedUsersView each did their own
`F("followers_count") + 1` / `- 1` next to the Follow write. That is
correct only for writes that go through those three code paths. It
silently drifts for everything else:

  - a Follow row deleted from /admin/ or a shell
  - `user.delete()` cascading away every Follow row the user was in
  - a status flip done outside the views
  - a follower/following re-pointed by hand

Now the counters are a pure function of the Follow table, recomputed by
these receivers on EVERY Follow write — so the views no longer touch the
counters at all (doing both would double count).

How the recount works
---------------------
`recompute_follow_counts(user_ids)` issues ONE `UPDATE ... SET
followers_count = (SELECT COUNT(*) ...), following_count = (SELECT
COUNT(*) ...)` per call. The count is derived from real rows inside the
database, in a single statement — there is no Python read-modify-write, so
it cannot lose an update the way `user.followers_count += 1; user.save()`
would.

Why it runs TWICE inside a transaction (and once outside one)
------------------------------------------------------------
  1. immediately — so the counters already reflect the change for the
     rest of the same request/transaction (and for TestCase-based tests,
     where `on_commit` callbacks never fire);
  2. again via `transaction.on_commit()` — the authoritative one. Two
     concurrent transactions that each recount before committing can each
     miss the other's not-yet-visible row (PostgreSQL re-checks the row
     after the lock wait but keeps the statement's original snapshot for
     the COUNT subquery), so the later writer could store a stale number.
     A recount after commit sees every committed Follow row and repairs
     that. Recounting is idempotent, so a double-tapped "accept" can no
     longer double count either (the old F() +1 could).
Outside an atomic block (autocommit) there is nothing to wait for, so the
recount runs just once.

Not covered (by design, Django limitation): `QuerySet.update()` and
`bulk_create()` never send signals. `tasks.reconcile_follow_counts` is
kept as a rarely-run safety net for exactly that, not as the primary
mechanism any more (see settings.CELERY_BEAT_SCHEDULE).
"""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import Signal, receiver

from .models import Follow, Streak

logger = logging.getLogger(__name__)

# Issue #20 — sent AFTER followers_count/following_count were rewritten for a
# set of users, by EVERY code path that does it (the Follow receivers below and
# the reconcile_follow_counts task). Those writes are single-statement
# `QuerySet.update()`s, which — like `bulk_update()` — never fire the User
# model's post_save. So anything that caches a user's profile/counters (a Redis
# profile cache, a search index, ...) must subscribe to THIS instead:
#
#     @receiver(follow_counts_recomputed)
#     def drop_cached_profiles(sender, user_ids, **kwargs):
#         cache.delete_many([f"profile:{uid}" for uid in user_ids])
#
# Nothing in this codebase caches those counters today, so no receiver is
# registered here; this is the single, stable hook for when one is added.
# Receivers run via send_robust() — a failing subscriber is logged and can
# never break a follow — and may be called more than once per change (inline,
# then again after commit), so they must be cheap and idempotent.
follow_counts_recomputed = Signal()

_RECOUNT_CHUNK = 1000  # user ids per UPDATE statement


def recompute_follow_counts(user_ids):
    """Set followers_count/following_count of every user in `user_ids`
    from the real ACCEPTED Follow rows, in a single UPDATE statement.
    Users that no longer exist (hard-deleted) simply match zero rows."""
    ids = sorted({uid for uid in user_ids if uid is not None})
    if not ids:
        return

    User = get_user_model()
    accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED).order_by()

    followers_sq = (
        accepted.filter(following_id=OuterRef("pk"))
        .values("following_id")
        .annotate(c=Count("pk"))
        .values("c")
    )
    following_sq = (
        accepted.filter(follower_id=OuterRef("pk"))
        .values("follower_id")
        .annotate(c=Count("pk"))
        .values("c")
    )

    # Chunked (sorted ids -> a stable row-lock order across concurrent
    # recounts, so two of them can't deadlock each other) so a huge id set
    # never becomes one giant IN (...) statement.
    for start in range(0, len(ids), _RECOUNT_CHUNK):
        User.objects.filter(pk__in=ids[start:start + _RECOUNT_CHUNK]).update(
            followers_count=Coalesce(Subquery(followers_sq, output_field=IntegerField()), 0),
            following_count=Coalesce(Subquery(following_sq, output_field=IntegerField()), 0),
        )

    for receiver_fn, response in follow_counts_recomputed.send_robust(
        sender=Follow, user_ids=frozenset(ids),
    ):
        if isinstance(response, Exception):
            logger.error("follow_counts_recomputed receiver %r failed: %r", receiver_fn, response)


def _schedule_recount(user_ids):
    ids = frozenset(uid for uid in user_ids if uid is not None)
    if not ids:
        return
    recompute_follow_counts(ids)
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(lambda: recompute_follow_counts(ids))


@receiver(pre_save, sender=Follow, dispatch_uid="user_profile.follow_capture_old_pair")
def _follow_capture_old_pair(sender, instance, raw=False, update_fields=None, **kwargs):
    """Remember which users this row used to connect, so an edit that
    re-points `follower`/`following` (only possible from admin/shell)
    recounts the OLD users as well as the new ones. Skipped for new rows
    and for saves that only touch `status` (what the accept view does),
    so the hot paths pay no extra query."""
    if raw or instance.pk is None:
        return
    if update_fields is not None and not ({"follower", "following"} & set(update_fields)):
        instance._old_follow_pair = None
        return
    instance._old_follow_pair = (
        Follow.objects.filter(pk=instance.pk)
        .values_list("follower_id", "following_id")
        .first()
    )


@receiver(post_save, sender=Follow, dispatch_uid="user_profile.follow_saved_recount")
def _follow_saved(sender, instance, created, raw=False, **kwargs):
    if raw:  # loaddata fixtures: counters come from the fixture itself
        return
    # A brand-new PENDING request changes no count.
    if created and instance.status != Follow.Status.ACCEPTED:
        return
    ids = {instance.follower_id, instance.following_id}
    old_pair = getattr(instance, "_old_follow_pair", None)
    if old_pair:
        ids.update(old_pair)
    _schedule_recount(ids)


@receiver(post_delete, sender=Follow, dispatch_uid="user_profile.follow_deleted_recount")
def _follow_deleted(sender, instance, **kwargs):
    # Deleting a PENDING request never counted toward anything.
    if instance.status != Follow.Status.ACCEPTED:
        return
    # A hard user delete cascades away EVERY Follow row that user was in; the
    # two receivers below recount the affected users ONCE, in a few chunked
    # UPDATEs, instead of two statements (x2 with on_commit) per cascaded row.
    if isinstance(kwargs.get("origin"), get_user_model()):
        return
    _schedule_recount({instance.follower_id, instance.following_id})


@receiver(pre_delete, sender=get_user_model(), dispatch_uid="user_profile.user_predelete_collect_follow_ids")
def _user_pre_delete(sender, instance, **kwargs):
    """Runs BEFORE the cascade removes this user's Follow rows: remember which
    other users' counters those rows were feeding (ids only — a set of ints)."""
    others = set()
    rows = (
        Follow.objects.filter(status=Follow.Status.ACCEPTED)
        .filter(Q(follower_id=instance.pk) | Q(following_id=instance.pk))
        .values_list("follower_id", "following_id")
        .iterator(chunk_size=5000)
    )
    for follower_id, following_id in rows:
        others.add(follower_id)
        others.add(following_id)
    others.discard(instance.pk)
    instance._follow_recount_ids = others


@receiver(post_delete, sender=get_user_model(), dispatch_uid="user_profile.user_postdelete_recount_follow")
def _user_post_delete(sender, instance, **kwargs):
    others = getattr(instance, "_follow_recount_ids", None)
    if others:
        _schedule_recount(others)


# ---------------------------------------------------------------------------
# Mutual-follow -> auto-create conversation
# ---------------------------------------------------------------------------
# When A follows B AND B follows A (both rows ACCEPTED), the two should get
# an empty conversation/thread in their inbox — WITHOUT either side sending a
# real message. This can become true from either direction's save():
#   - a brand-new Follow row created directly ACCEPTED (public account,
#     instant follow) — `_follow_saved` above already returns early for a
#     new PENDING row, but a new ACCEPTED row still reaches this receiver;
#   - `AcceptFollowRequestView` flipping a PENDING row to ACCEPTED (private
#     account, request accepted).
# Kept as its own receiver (not folded into `_follow_saved`) because it is a
# completely different concern (cross-app side effect vs. this user's own
# denormalized counters) with its own dependency on `message.services` —
# mixing the two would make `_follow_saved` harder to reason about and tie
# the counter logic to message/ needlessly.
def _maybe_link_mutual_conversation(follower_id, following_id):
    """If `follower_id` <-> `following_id` now mutually follow each other
    (both directions ACCEPTED), get-or-create their private conversation.
    Idempotent — safe to call more than once for the same pair (called both
    inline and again via `on_commit`, same as `_schedule_recount` does for
    counts, and `message.services.get_or_create_conversation` itself is
    race-safe via `Conversation.get_or_create_private`'s unique constraint)."""
    if follower_id == following_id or follower_id is None or following_id is None:
        return
    reverse_exists = Follow.objects.filter(
        follower_id=following_id,
        following_id=follower_id,
        status=Follow.Status.ACCEPTED,
    ).exists()
    if not reverse_exists:
        return
    # Lazy import — same reason as the `core` imports in views.py: user_profile
    # must not hard-depend on message/ at module-import time.
    from message.services import get_or_create_conversation
    get_or_create_conversation(follower_id, following_id)


@receiver(post_save, sender=Follow, dispatch_uid="user_profile.follow_saved_mutual_conversation")
def _follow_saved_mutual_conversation(sender, instance, created, raw=False, **kwargs):
    if raw or instance.status != Follow.Status.ACCEPTED:
        return
    follower_id, following_id = instance.follower_id, instance.following_id

    def _check():
        _maybe_link_mutual_conversation(follower_id, following_id)

    # Same dual-invocation reasoning as `_schedule_recount`: run once now
    # (so TestCase-based tests, where `on_commit` never fires, still see the
    # conversation), and again after commit (the authoritative check — a
    # concurrent transaction creating the reverse row at the same moment
    # could otherwise be missed by both sides' pre-commit snapshot).
    _check()
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(_check)


# ---------------------------------------------------------------------------
# P13-BE — Achievements / badges (event-driven rules)
#
# Streak (7/30), first test completed and teacher-verified are all awarded
# from here the moment the underlying row is written. The remaining rule —
# top-10 weekly leaderboard — only makes sense once a week is over, so it is
# a Celery task (tasks.award_weekly_leaderboard_badges).
#
# Every award is deferred with transaction.on_commit() when inside a
# transaction, so a rolled-back streak/attempt never leaves a badge behind.
# award_badge() is idempotent, so a repeat firing is harmless. In
# TestCase-based tests on_commit callbacks only run under
# `self.captureOnCommitCallbacks(execute=True)`.
# ---------------------------------------------------------------------------
def _run_after_commit(fn):
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(fn)
    else:
        fn()


@receiver(post_save, sender=Streak, dispatch_uid="user_profile.streak_saved_badges")
def _streak_badges(sender, instance, raw=False, **kwargs):
    if raw:
        return
    streak_days, user_id = instance.current_streak, instance.user_id
    if streak_days < 7:
        return

    def _award():
        from .services import award_badge

        award_badge(user_id, "streak_7", context={"streak_days": streak_days})
        if streak_days >= 30:
            award_badge(user_id, "streak_30", context={"streak_days": streak_days})

    _run_after_commit(_award)


# String sender: user_profile must not import testseries at load time (same
# lazy-dependency rule as the `message.services` import above); Django
# resolves "app_label.Model" the moment that model class is loaded.
@receiver(post_save, sender="testseries.TestAttempt", dispatch_uid="user_profile.attempt_saved_first_test_badge")
def _first_test_badge(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from testseries.models import TestAttempt

    # "Completed" = fully checked, the same state leaderboard/ treats as a
    # settled attempt (an in-progress / partially-checked one doesn't count).
    if instance.status != TestAttempt.Status.CHECKED:
        return
    student_id = instance.student_id

    def _award():
        from .services import award_badge

        award_badge(student_id, "first_test", context={"attempt_id": str(instance.pk)})

    _run_after_commit(_award)


@receiver(post_save, sender=get_user_model(), dispatch_uid="user_profile.user_saved_teacher_verified_badge")
def _teacher_verified_badge(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    from .services import award_badge, teacher_verified_field

    field = teacher_verified_field()
    # Cheap exits first: this fires on EVERY user save (last_login etc.).
    if update_fields is not None and field not in update_fields:
        return
    if not getattr(instance, field, False):
        return
    user_id = instance.pk
    _run_after_commit(lambda: award_badge(user_id, "teacher_verified"))


# ======================================================================
# BLOCK SYNC — user_profile.BlockUser  <->  message.BlockedUser
# ======================================================================
# Two tables hold "A blocked B":
#   * user_profile.BlockUser  — written by `/profile/blocked-users/`, which is
#     the ONLY endpoint the Flutter app calls (profile, settings AND chat).
#   * message.BlockedUser     — read by chat enforcement (websocket consumer,
#     send/schedule/poll views, user notes, message search).
# Nothing connected them, so blocking from the app never actually stopped
# chat delivery. These receivers keep the two tables identical in both
# directions (get_or_create / filter().delete() make every handler idempotent,
# so the two sides can't ping-pong forever).
from django.db.models import Q as _Q


def _chat_block_model():
    from message.models import BlockedUser
    return BlockedUser


def _profile_block_model():
    from .models import BlockUser
    return BlockUser


@receiver(post_save, sender="user_profile.BlockUser", dispatch_uid="block_sync_profile_save")
def _mirror_profile_block_to_chat(sender, instance, created, **kwargs):
    if not created:
        return
    from .models import Follow

    from .block_live import on_block_changed

    on_block_changed(instance.blocker_id, instance.blocked_id, True)
    chat_row, was_created = _chat_block_model().all_objects.get_or_create(
        blocker_id=instance.blocker_id, blocked_id=instance.blocked_id,
    )
    if not was_created and chat_row.is_deleted:  # soft-deleted leftover -> revive
        chat_row.is_deleted = False
        chat_row.save(update_fields=["is_deleted", "updated_at"])
    # Blocking ends any follow in either direction no matter which API
    # created the block (counters are fixed by the Follow post_delete receiver).
    Follow.objects.filter(
        _Q(follower_id=instance.blocker_id, following_id=instance.blocked_id)
        | _Q(follower_id=instance.blocked_id, following_id=instance.blocker_id)
    ).delete()


@receiver(post_delete, sender="user_profile.BlockUser", dispatch_uid="block_sync_profile_delete")
def _mirror_profile_unblock_to_chat(sender, instance, **kwargs):
    from .block_live import on_block_changed

    on_block_changed(instance.blocker_id, instance.blocked_id, False)
    _chat_block_model().all_objects.filter(
        blocker_id=instance.blocker_id, blocked_id=instance.blocked_id,
    ).delete()


@receiver(post_save, sender="message.BlockedUser", dispatch_uid="block_sync_chat_save")
def _mirror_chat_block_to_profile(sender, instance, created, **kwargs):
    if not created or instance.blocker_id == instance.blocked_id:
        return
    _profile_block_model().objects.get_or_create(
        blocker_id=instance.blocker_id, blocked_id=instance.blocked_id,
    )


@receiver(post_delete, sender="message.BlockedUser", dispatch_uid="block_sync_chat_delete")
def _mirror_chat_unblock_to_profile(sender, instance, **kwargs):
    _profile_block_model().objects.filter(
        blocker_id=instance.blocker_id, blocked_id=instance.blocked_id,
    ).delete()
