"""
user_profile/tasks.py

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
checks and the `bulk_update` field list below — everything else stays
the same.

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

# Caps how many (user, new_count) pairs sit in memory / go into a single
# bulk_update() UPDATE at once. Keeps this task's memory and per-query
# cost bounded on an app with a large user table, rather than building
# one unbounded list for the whole run.
_BULK_UPDATE_BATCH_SIZE = 500


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

    # Two aggregate GROUP BY queries cover every user's correct count in
    # one shot each — this is what keeps a 6-hourly run cheap regardless
    # of user count, instead of one query per user.
    correct_followers_by_user = dict(
        Follow.objects.filter(status=Follow.Status.ACCEPTED)
        .values("following_id")
        .annotate(c=Count("id"))
        .values_list("following_id", "c")
    )
    correct_following_by_user = dict(
        Follow.objects.filter(status=Follow.Status.ACCEPTED)
        .values("follower_id")
        .annotate(c=Count("id"))
        .values_list("follower_id", "c")
    )

    to_update = []
    checked = 0
    corrected_followers = 0
    corrected_following = 0

    # Walk every user, not just IDs that appear in the two maps above — a
    # user whose real accepted-follow count just dropped to zero (every
    # Follow row touching them got deleted) won't appear in either map at
    # all, but their stored counter could still be sitting on a stale
    # nonzero value. Checking map keys only would miss exactly that
    # direction of drift.
    queryset = User.objects.only("id", "followers_count", "following_count").iterator(chunk_size=1000)
    for user in queryset:
        checked += 1
        true_followers = correct_followers_by_user.get(user.id, 0)
        true_following = correct_following_by_user.get(user.id, 0)

        needs_followers_fix = user.followers_count != true_followers
        needs_following_fix = user.following_count != true_following
        if not (needs_followers_fix or needs_following_fix):
            continue

        if needs_followers_fix:
            corrected_followers += 1
        if needs_following_fix:
            corrected_following += 1

        user.followers_count = true_followers
        user.following_count = true_following
        to_update.append(user)

        if len(to_update) >= _BULK_UPDATE_BATCH_SIZE:
            User.objects.bulk_update(to_update, ["followers_count", "following_count"])
            to_update = []

    if to_update:
        User.objects.bulk_update(to_update, ["followers_count", "following_count"])

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