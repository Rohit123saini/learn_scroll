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

from .models import Follow

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
