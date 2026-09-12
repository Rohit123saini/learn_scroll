"""
post/signals.py

⚠️ RENAMED from the uploaded `Signals.py` — apps.py does
`import post.signals` in lowercase, which fails to resolve on a
case-sensitive filesystem (Linux/prod) against a file literally named
`Signals.py`. Same rename applied to services.py / tasks.py.

⚠️ FIX — every function here used `instance.author_id`. `Post` has no
`author` field/FK — the real one is `Post.user` (see models.py). This
raised `AttributeError` the first time any of these receivers fired.

DECISION — `posts_count` is now kept in sync HERE, via
`post_save`/`post_delete`, instead of the manual
`User.objects.filter(...).update(posts_count=F('posts_count') + 1)` line
that used to live inline in `PostCreateAPIView.post()`. That manual line
has been removed from views.py to match (see views.py's own note at that
call site) — keeping both would double-count.

Signal-based wins for production: it's the single place this logic lives
no matter which code path creates/deletes a Post (the API view, the admin,
a management command, a data-migration script, a test calling
`Post.objects.create()` directly) — a future second entry point into post
creation can't silently forget to bump the counter, because it was never
its job to remember in the first place. This also matches what checklist
item 58 originally asked for ("Post.save() signal se posts_count update
karo").

KEPT — `decrement_posts_count_on_soft_delete`, called explicitly from the
new `PostDeleteAPIView.delete()` in views.py (soft-delete never fires
`post_delete`, so it can't be a signal). `decrement_posts_count_on_hard_delete`
is registered for whenever/if a genuine hard-delete path is ever added
(e.g. an admin purge command) — inert today, harmless to leave wired up.

🔥 TASK 23 — `PostLike` used to have two separate signal handlers
(`update_likes_count`, `update_reaction_counts`) both firing on every
PostLike save/delete and both writing `Post.likes_count` independently.
Beyond the redundant writes, that split was a correctness risk: a
*reaction change* (`PostReactionAPIView.post()` does `existing.
reaction_type = new_type; existing.save()` — same row, not a create or
delete) doesn't move the total (`likes_count`), only the per-type
breakdown (`like_count`/`confuse_count`/`wrong_count`/`imp_count`/
`explain_count`) — nothing guaranteed both handlers agreed on how to
treat that case, and an incremental `F(...) + 1`/`- 1` style counter
only even makes sense on create/delete in the first place.

Replaced both with `sync_post_reaction_counts` below: a single receiver
on PostLike's `post_save`/`post_delete` that recomputes every reaction
count directly from the actual `PostLike` rows via one aggregate query,
then writes all of them in one `UPDATE`. An aggregate recompute can't
drift out of sync the way two independent incremental counters can, and
it's naturally correct for create, delete, *and* the in-place reaction
change case, with no special-casing needed for any of the three.
"""
import logging

from django.db.models import Count, F, Q
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Post, PostLike, PostMedia

logger = logging.getLogger(__name__)


def _user_model():
    from django.contrib.auth import get_user_model

    return get_user_model()


@receiver(post_save, sender=Post)
def increment_posts_count_on_create(sender, instance, created, **kwargs):
    if not created:
        return
    User = _user_model()
    if not hasattr(User, "posts_count"):
        logger.warning("User model has no `posts_count` field — skipping sync.")
        return
    User.objects.filter(pk=instance.user_id).update(posts_count=F("posts_count") + 1)


@receiver(post_delete, sender=Post)
def decrement_posts_count_on_hard_delete(sender, instance, **kwargs):
    """Only fires on a genuine hard delete (a queryset/instance `.delete()`
    that actually removes the row) — the normal delete path in this app is
    the soft-delete below, which never triggers post_delete."""
    User = _user_model()
    if not hasattr(User, "posts_count"):
        return
    User.objects.filter(pk=instance.user_id).update(posts_count=F("posts_count") - 1)


def decrement_posts_count_on_soft_delete(post):
    """Not a Django signal — soft-delete is just a `.save()`/`.update()`
    under the hood and won't fire `post_delete`. Called explicitly from
    `PostDeleteAPIView.delete()` in views.py, mirroring the exact pattern
    `CommentDeleteAPIView` already uses for `PostComment` counters."""
    User = _user_model()
    if not hasattr(User, "posts_count"):
        return
    User.objects.filter(pk=post.user_id).update(posts_count=F("posts_count") - 1)


# ----------------------------------------------------------------------
# TASK 3 — "new post from someone you follow" fan-out, enqueue-only.
#
# Same reasoning as queue_video_thumbnail_on_create further down:
# `post_save` runs synchronously inside whatever request/transaction
# created this `Post` row (`PostCreateAPIView.post()`), and a popular
# account's follower list can run into the thousands — looping through
# even a cheap per-follower write inline here would make every single
# post-create request slow in direct proportion to that account's
# follower count. `.delay()` just enqueues
# `post.tasks.notify_followers_new_post` and returns immediately; the
# actual `Follow` table query and the notification fan-out itself happen
# there, off the request path.
#
# transaction.on_commit(...) (deliberately NOT a bare `.delay()` the way
# queue_video_thumbnail_on_create below still is): if
# PostCreateAPIView.post() ever wraps the Post creation in
# `@transaction.atomic` (as several views in this codebase already do —
# e.g. FollowAPIView.post()), a bare `.delay()` fired from inside that
# transaction could have the Celery worker pick up the task and query
# for this Post row before the transaction actually commits, raising
# Post.DoesNotExist in the task for a post that does, in fact, exist.
# on_commit() defers the enqueue until the surrounding transaction (if
# any) has successfully committed — and runs immediately, synchronously,
# if there's no open transaction at all (autocommit), so this is strictly
# safer with no downside either way.
#
# MVP scope (per the design doc): every ACCEPTED follower gets notified
# on every new post — no per-follower "bell" opt-in yet (Instagram-style,
# per-account). That's a deliberate, documented trade-off for a later
# pass, not something this receiver is trying to solve.
# ----------------------------------------------------------------------
@receiver(post_save, sender=Post)
def queue_new_post_notification_fanout(sender, instance, created, **kwargs):
    if not created:
        return
    from django.db import transaction

    from .tasks import notify_followers_new_post

    transaction.on_commit(lambda: notify_followers_new_post.delay(instance.id))


# ----------------------------------------------------------------------
# TASK 23 — PostLike reaction counts (see module docstring for why this
# replaces the old `update_likes_count` / `update_reaction_counts` pair).
# ----------------------------------------------------------------------
# Must stay in sync with `ReactionRequestSerializer.reaction`'s
# `choices` (serializers.py) — that's the only other place this set of
# reaction types is spelled out, and each entry here maps directly to a
# `Post.<type>_count` field.
REACTION_TYPES = ("like", "confuse", "wrong", "imp", "explain")


def sync_post_reaction_counts(post_id):
    """
    Single source of truth for a `Post`'s reaction counters. Recomputes
    every per-type count (`like_count`, `confuse_count`, `wrong_count`,
    `imp_count`, `explain_count`) plus the `likes_count` total straight
    from `PostLike` rows, in one aggregate query, then writes all of
    them (plus the auto-flag below, when it applies) in one `UPDATE` —
    so the two never disagree the way two separately-maintained
    incremental counters could.

    Not `@receiver`-decorated itself (that's `_on_save`/`_on_delete`
    below) so it can also be called directly wherever `PostLike` rows
    might be touched outside a normal save/delete — e.g. a future
    moderation bulk-remove or a data-migration backfill — the same way
    `decrement_posts_count_on_soft_delete` above is called explicitly
    for its own out-of-band case.

    Trade-off, noted deliberately: this is a full recompute (one
    `COUNT`-style aggregate) rather than an incremental +1/-1, which
    costs one extra query per like/unlike compared to the old approach.
    That's the right trade for a reaction feature — a post's total like
    count staying wrong is a worse bug than one more cheap indexed
    COUNT — but if a single post's `PostLike` volume ever gets large
    enough for this to matter, the field to revisit is scale on this
    query, not going back to incremental counters.

    FIX (B-5) — this used to be duplicated by a second receiver,
    `update_reaction_counts` in models.py, which independently
    recomputed the same counts AND carried its own 5+-`wrong`
    auto-flag-to-`flagged` check as a *second* `UPDATE` right after the
    first. Both receivers were registered on the same PostLike
    post_save/post_delete signals, so every like/unlike paid for two
    full aggregate-recompute + UPDATE round trips converging on
    identical numbers — pure waste on the app's hottest write path.
    `update_reaction_counts` has been deleted from models.py; its
    auto-flag check is folded in here instead, using the `wrong_count`
    already sitting in `counts` (no extra query needed), and merged
    into the same `UPDATE` as the counts themselves rather than firing
    a second one. Matches the old behavior exactly: it only ever sets
    `flagged`, never clears it back once `wrong_count` drops below 5.
    """
    counts = PostLike.objects.filter(post_id=post_id).aggregate(
        **{f"{rt}_count": Count("id", filter=Q(reaction_type=rt)) for rt in REACTION_TYPES},
        likes_count=Count("id"),
    )
    if counts["wrong_count"] >= 5:
        counts["moderation_status"] = "flagged"
    Post.objects.filter(pk=post_id).update(**counts)


@receiver(post_save, sender=PostLike)
def sync_post_reaction_counts_on_save(sender, instance, **kwargs):
    """
    Deliberately does NOT branch on `created` the way
    `increment_posts_count_on_create` above does — a reaction *change*
    (`PostReactionAPIView.post()`: `existing.reaction_type = new_type;
    existing.save()`) is a save with `created=False` that still needs
    the per-type breakdown recomputed (old type's count -1, new type's
    +1 — even though the `likes_count` total doesn't move). Since
    `sync_post_reaction_counts` recomputes from scratch rather than
    incrementing, running it unconditionally on every save handles
    create AND change identically and correctly, with no special case.
    """
    sync_post_reaction_counts(instance.post_id)


@receiver(post_delete, sender=PostLike)
def sync_post_reaction_counts_on_delete(sender, instance, **kwargs):
    sync_post_reaction_counts(instance.post_id)


# ----------------------------------------------------------------------
# TASK 27 — video thumbnail generation, enqueue-only.
#
# This receiver's ONLY job is to hand off to Celery
# (`post.tasks.generate_video_thumbnail`) — it deliberately does not call
# ffmpeg or touch storage itself. `post_save` runs synchronously inside
# whatever request/transaction created this `PostMedia` row
# (`PostCreateAPIView.post()`); running ffmpeg (a slow subprocess against
# a real video file) inline here would block that request's response for
# however long ffmpeg takes, on every single video upload. `.delay()`
# just enqueues and returns immediately.
# ----------------------------------------------------------------------
@receiver(post_save, sender=PostMedia)
def queue_video_thumbnail_on_create(sender, instance, created, **kwargs):
    if not created or instance.media_type != "video":
        return
    from .tasks import generate_video_thumbnail

    generate_video_thumbnail.delay(instance.id)