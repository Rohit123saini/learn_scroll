"""
user_profile/tasks.py

UPDATE (issue #4): the root-cause fix now exists —
`user_profile/signals.py` recomputes followers_count/following_count
from the real Follow rows on every Follow post_save/post_delete (incl.
admin deletes and user.delete() cascades), and the API views no longer
update the counters by hand. Everything below that says "the actual fix
would be a signal" is therefore historical.

This task is kept ONLY as a low-frequency safety net for the one thing
signals cannot see: `QuerySet.update()` / `bulk_create()` on Follow
(Django sends no signals for those) and raw SQL. It now runs weekly
(settings.CELERY_BEAT_SCHEDULE) instead of every 6 hours; a non-zero
"corrected" count in its log line means some code path is bypassing the
signals and should be found and fixed.

TASK 28 — followers_count/following_count drift reconciliation.

`followers_count`/`following_count` are kept in sync per-operation today
— whatever code path handles follow/accept/unfollow does its own
`F(...) + 1` / `- 1` right alongside the `Follow` row write, and that's
correct for every write that actually goes through those code paths.

The gap is writes that DON'T go through them:
  - an admin deleting a `Follow` row directly in /admin/
  - `user.delete()` CASCADE-deleting every `Follow` row the deleted user
    was party to (as follower AND as following) — nothing re-runs the
    counter update on the *other* side of each of those rows
  - a data migration, a bulk `.delete()`/`.update()` run from a shell,
    a one-off fixup script
None of those fire the same increment/decrement logic the API views use,
so the stored counters can silently drift from what `Follow` rows
actually say.

This task is a detect-and-correct safety net, not the root-cause fix —
the actual fix would be a `Follow` `post_save`/`post_delete` signal that
recomputes from real rows on every change (the same pattern
`post/models.py` already uses for `update_shares_count`/
`update_saves_count`/`update_story_views_count` — see that file), which
makes drift structurally impossible instead of periodically corrected.
That's a bigger change to `user_profile/models.py` than this task asked
for; this reconciliation job is the "note it, don't block on it" version,
matching the same trade-off this codebase already made for
`post.views.TrendingHashtagsAPIView` (bounded recompute now, revisit if
scale demands it later).

⚠️ ASSUMPTION — `user_profile/models.py` wasn't provided when this was
written. Field names below are taken from how `Follow` is already used
elsewhere in this codebase (`post/views.py`'s `HomeFeedView`/
`ExploreFeedAPIView`: `Follow.objects.filter(follower=..., status=
Follow.Status.ACCEPTED).values_list('following_id', ...)`), so
`follower`/`following`/`status`/`Follow.Status.ACCEPTED` are already
confirmed real. If your `User` model's counter fields aren't literally
named `followers_count`/`following_count`, adjust the two `hasattr`
checks and the `recompute_follow_counts` call below — everything else
stays the same.

Wire into settings.py CELERY_BEAT_SCHEDULE (already added):

    CELERY_BEAT_SCHEDULE = {
        ...
        "user-profile-reconcile-follow-counts": {
            "task": "user_profile.tasks.reconcile_follow_counts",
            "schedule": crontab(hour="*/6", minute=15),
        },
    }
"""
import logging

from celery import shared_task
from django.db.models import Count

logger = logging.getLogger(__name__)

# How many users are checked (and, if drifted, fixed) per round trip. Keeps the
# task's memory and per-query cost bounded on a large user table.
_CHUNK_SIZE = 1000


@shared_task
def reconcile_follow_counts():
    """Recompute every user's followers_count/following_count directly
    from `Follow` rows (status=ACCEPTED only — matches how "following" is
    defined everywhere else in this codebase) and correct any drift
    found. Only writes rows that are actually wrong; a run where nothing
    drifted issues zero UPDATEs beyond the two read queries below.

    Returns a summary dict so a manual `.delay()` call, a Flower
    dashboard, or an admin action can see whether drift is actually
    happening in practice. If this keeps finding real correction work on
    every scheduled run, that's a signal the root-cause fix (a `Follow`
    post_delete signal — see module docstring) is overdue, not that this
    task itself is misbehaving.
    """
    from django.contrib.auth import get_user_model

    from .models import Follow

    User = get_user_model()

    if not (hasattr(User, "followers_count") and hasattr(User, "following_count")):
        logger.warning(
            "reconcile_follow_counts: User model has no followers_count/"
            "following_count fields — nothing to reconcile."
        )
        return {"skipped": True}

    # Issue #20 / scalability: walk the user table in id-ordered CHUNKS
    # (keyset pagination) and, per chunk, ask the database for the true counts
    # of just those users with two grouped queries. Memory is bounded by the
    # chunk, not by the number of users (the old version held one dict entry
    # per user-with-follows for the whole table).
    #
    # Users found drifted are fixed with `recompute_follow_counts()` — the
    # SAME single-statement `UPDATE ... SET count = (SELECT COUNT(*) ...)` the
    # signals use — instead of `bulk_update()` of values read earlier. Two
    # advantages: a follow that lands between "read" and "write" is not
    # overwritten with a stale absolute number (the count is recomputed by
    # the database at write time), and it goes through the one code path that
    # also emits `follow_counts_recomputed` (signals.py), the hook a cache
    # layer subscribes to — `bulk_update()`/`update()` fire no model
    # post_save, so without that hook a cached copy would stay stale.
    from .signals import recompute_follow_counts

    accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED).order_by()
    checked = 0
    corrected_followers = 0
    corrected_following = 0
    last_id = 0

    while True:
        chunk = list(
            User.objects.filter(id__gt=last_id)
            .order_by("id")
            .values_list("id", "followers_count", "following_count")[:_CHUNK_SIZE]
        )
        if not chunk:
            break
        last_id = chunk[-1][0]
        ids = [row[0] for row in chunk]
        checked += len(chunk)

        # Every user in the chunk is checked, including ones that appear in
        # neither map below — a user whose real count just dropped to zero can
        # still carry a stale non-zero counter, and only walking all users
        # (not just map keys) catches that direction of drift.
        true_followers = dict(
            accepted.filter(following_id__in=ids)
            .values("following_id").annotate(c=Count("id")).values_list("following_id", "c")
        )
        true_following = dict(
            accepted.filter(follower_id__in=ids)
            .values("follower_id").annotate(c=Count("id")).values_list("follower_id", "c")
        )

        drifted = []
        for uid, followers_count, following_count in chunk:
            fix_followers = followers_count != true_followers.get(uid, 0)
            fix_following = following_count != true_following.get(uid, 0)
            if fix_followers or fix_following:
                drifted.append(uid)
                corrected_followers += fix_followers
                corrected_following += fix_following
        if drifted:
            recompute_follow_counts(drifted)

    total_corrected = corrected_followers + corrected_following
    if total_corrected:
        logger.warning(
            "reconcile_follow_counts: checked %s users, corrected "
            "followers_count on %s and following_count on %s — drift "
            "detected (expected occasionally from admin deletes/cascades; "
            "if this stays high on every run, the real fix is a Follow "
            "post_delete signal, not just this reconciliation).",
            checked, corrected_followers, corrected_following,
        )
    else:
        logger.info("reconcile_follow_counts: checked %s users, no drift found.", checked)

    return {
        "checked": checked,
        "corrected_followers_count": corrected_followers,
        "corrected_following_count": corrected_following,
    }