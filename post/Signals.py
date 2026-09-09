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
"""
import logging

from django.db.models import F
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Post

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