"""
post/tagging.py  (P5a-BE)

"Tag people in a post" helpers shared by the create / edit serializers, the
tagged-tab view and the scheduled-post publisher.

  parse_tags(raw, author, visibility) -> [{"user": User, "x": float|None, "y": float|None}]
  sync_post_tags(post, parsed)        -> replaces the post's tag set (keeps is_hidden / notified of kept rows)
  notify_post_tags(post)              -> POST_TAG notification, idempotent, skipped while the post is scheduled
  tagged_users_payload(post, request) -> list for the `tagged_users` output field
"""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction

logger = logging.getLogger(__name__)

MAX_TAGS_PER_POST = 20


class TagError(ValueError):
    """Message is safe to show to the client."""


def _to_unit_float(value, name):
    if isinstance(value, bool):
        raise TagError(f"Tag position '{name}' must be a number between 0 and 1.")
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise TagError(f"Tag position '{name}' must be a number between 0 and 1.")
    if not (0.0 <= f <= 1.0) or f != f:
        raise TagError(f"Tag position '{name}' must be between 0 and 1.")
    return f


def parse_tags(raw, author, visibility):
    """Validate the `tags` request value. Raises TagError. [] is valid (clears all tags)."""
    User = get_user_model()
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise TagError("tags must be a list.")
    if len(raw) > MAX_TAGS_PER_POST:
        raise TagError(f"You can tag at most {MAX_TAGS_PER_POST} people in one post.")

    entries = {}
    for item in raw:
        if not isinstance(item, dict):
            raise TagError("Each tag must be an object like {\"user_id\": 5, \"x\": 0.3, \"y\": 0.4}.")
        uid = item.get("user_id")
        if uid is None or isinstance(uid, bool):
            raise TagError("Each tag needs a user_id.")
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            raise TagError("Invalid user_id in tags.")
        x, y = item.get("x"), item.get("y")
        if (x is None) != (y is None):
            raise TagError("A tag position needs both x and y.")
        if x is not None:
            x, y = _to_unit_float(x, "x"), _to_unit_float(y, "y")
        entries[uid] = (x, y)  # a repeated user keeps the last entry

    if not entries:
        return []
    if visibility == "private":
        raise TagError("You can't tag people in a private post.")

    users = {u.pk: u for u in User.objects.filter(pk__in=list(entries), is_active=True)}
    missing = [uid for uid in entries if uid not in users]
    if missing:
        raise TagError("One or more tagged users don't exist.")

    from user_profile.services import blocked_user_ids
    blocked = blocked_user_ids(author)
    for uid in entries:
        if uid != author.pk and uid in blocked:
            raise TagError("You can't tag this person.")
    # S4: "who can tag me" -- same message as a block, so the reason is not revealed.
    from user_profile import privacy as privacy_rules
    allowed = privacy_rules.allowed_owner_ids(author, list(entries), "tag_from")
    for uid in entries:
        if uid not in allowed:
            raise TagError("You can't tag this person.")

    return [{"user": users[uid], "x": x, "y": y} for uid, (x, y) in entries.items()]


def sync_post_tags(post, parsed):
    """Make the post's PostTag rows equal `parsed`. Rows that stay keep is_hidden / notified."""
    from .models import PostTag

    wanted = {t["user"].pk: t for t in parsed}
    existing = {t.tagged_user_id: t for t in PostTag.objects.filter(post=post)}
    stale = [pk for uid, t in existing.items() if uid not in wanted for pk in (t.pk,)]
    if stale:
        PostTag.objects.filter(pk__in=stale).delete()
    for uid, t in wanted.items():
        row = existing.get(uid)
        if row is None:
            PostTag.objects.create(post=post, tagged_user=t["user"], x=t["x"], y=t["y"])
        elif (row.x, row.y) != (t["x"], t["y"]):
            PostTag.objects.filter(pk=row.pk).update(x=t["x"], y=t["y"])


def notify_post_tags(post):
    """POST_TAG notification for every tag that has not been notified yet.
    No-op while the post is scheduled / deleted / private; safe to call repeatedly."""
    from .models import Post, PostTag
    from .services import _Notification, _create_notification_row, _post_preview_media

    post = Post.objects.select_related("user").filter(pk=post.pk).first()
    if post is None or post.is_scheduled or post.is_deleted or post.visibility == "private":
        return 0
    sent = 0
    actor = post.user
    actor_name = actor.get_full_name() or actor.username
    for tag in PostTag.objects.filter(post=post, notified=False).select_related("tagged_user"):
        # claim first -> two concurrent callers can never both notify
        if not PostTag.objects.filter(pk=tag.pk, notified=False).update(notified=True):
            continue
        if tag.tagged_user_id == post.user_id or _Notification is None:
            continue
        try:
            media_url, media_type = _post_preview_media(post)
            _create_notification_row(
                tag.tagged_user,
                _Notification.NotifType.POST_TAG,
                f"{actor_name} tagged you in a post",
                (post.content or "")[:200],
                data={"post_id": str(post.id), "media_url": media_url, "media_type": media_type},
                actor=actor,
            )
            sent += 1
        except Exception:  # a notification must never break the post flow
            logger.exception("notify_post_tags failed (post=%s tag=%s)", post.pk, tag.pk)
    return sent


def notify_post_tags_on_commit(post):
    transaction.on_commit(lambda: notify_post_tags(post))


def tagged_users_payload(post, request=None):
    from .serializers import get_profile_pic_url

    rows = list(post.post_tags.all())  # prefetch_related('post_tags__tagged_user') makes this query-free
    out = []
    for t in rows:
        pic = get_profile_pic_url(t.tagged_user, request)
        out.append({
            # shape the Flutter PostTagView.listFrom() reads: {user:{id,username,profile_picture}, x, y}
            "user": {"id": str(t.tagged_user_id), "username": t.tagged_user.username, "profile_picture": pic},
            "id": str(t.tagged_user_id),
            "username": t.tagged_user.username,
            "profile_picture": pic,
            "x": t.x,
            "y": t.y,
        })
    return out
