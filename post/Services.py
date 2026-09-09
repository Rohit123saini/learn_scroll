"""
post/services.py

⚠️ RENAMED from the uploaded `Services.py` — apps.py does
`import post.signals` (lowercase), and this module gets imported the same
way. On a case-sensitive filesystem (Linux/prod) a capitalized
`Services.py` / `Signals.py` / `Tasks.py` is a DIFFERENT file to Python
than `services.py` / `signals.py` / `tasks.py` — the import would raise
`ModuleNotFoundError` at runtime. It only "worked" by accident on
case-insensitive dev filesystems (Windows/macOS default). Same rename
applied to signals.py and tasks.py.

⚠️ CRITICAL FIX — the uploaded file imported `Hashtag`, `PostHashtag`,
`Like`, `SavedPost`, `Comment` from `.models`. None of these exist.
Real models.py has: Post, PostMedia, PostLike, PostComment, CommentMedia,
PostShare, PostView, PostSave, ChunkedUpload, CommentLike.

- `attach_hashtags()` / the whole Hashtag/PostHashtag idea — REMOVED.
  Hashtags aren't a separate model here: `Post.hashtags` is a plain
  `JSONField(default=list)`, populated directly inside
  `PostCreateSerializer.create()` via `re.findall(r"#(\w+)", content)`
  (see serializers.py, and post_app.md §13.1). There's nothing left for a
  service function to do.
  ⚠️ serializers.py currently STILL has a dead/broken call —
      hashtags = validated_data.get("hashtags")
      if hashtags:
          from .services import attach_hashtags
          attach_hashtags(post, hashtags)
  left over from this same wrong assumption. That will raise
  ImportError/AttributeError the moment anyone creates a post with
  hashtags, since this function no longer exists (and shouldn't).
  Delete those lines from `PostCreateSerializer.create()` —
  `validated_data["hashtags"]` is already what gets saved on the row.
- `toggle_like()` / `toggle_save()` / `add_comment()` / `delete_comment()`
  — REMOVED. These duplicated logic already implemented, correctly,
  directly in views.py / comment_view.py against the real models
  (`PostLike` / `PostSave` / `PostComment`), with counters kept in sync by
  the `@receiver` signals already living in models.py
  (`update_reaction_counts`, `update_saves_count`, etc — see models.py's
  own "NOTE (fix...)" comment on why a second counter-update path is a
  correctness bug, not just redundant work). Nothing in this app calls
  `services.toggle_like` etc. today — keeping them as dead code against
  nonexistent models was the actual problem, not a missing feature.

What's kept below is genuinely additive — logic the views/signals don't
already provide:

- The `core.create_notification` soft-dependency probe (checklist item
  63 / Phase 3's hub) — fixed to use `post.user` (the real FK) instead of
  the nonexistent `post.author`, and to take a `PostComment` instance
  instead of the nonexistent `Comment`. Nothing calls
  `notify_post_liked` / `notify_post_commented` yet — wire them into
  `PostReactionAPIView.post()` (only the `status_msg == "liked"` branch)
  and `CommentCreateAPIView.post()` (only for new top-level comments)
  once `core.notifications` actually exists.
- `share_post_to_conversation()` (checklist item 61) — fixed to use
  `post.user` / `post.content` instead of `post.author` / `post.caption`,
  and `PostShare.objects.get_or_create` instead of `.create()` (the real
  model has `unique_together = ['post', 'user']`, so a second share by
  the same user would raise `IntegrityError`). Also stopped hand-rolling
  a `Post.share_count` F()-update against a field that doesn't exist —
  the real counter is `shares_count`, already kept in sync by
  `update_shares_count` in models.py whenever a `PostShare` row is
  created. Still unwired — no view/url calls this yet (see urls.py: no
  `/share/` route exists). Add one once the `message` app's real
  send-function path is confirmed.
"""
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Notification hookup (checklist item 63 / Phase 3's hub).
#
# `core.create_notification` doesn't exist yet. Probe once, fall back to a
# no-op + log line, so this app doesn't hard-crash every request until
# Phase 3 ships `core/notifications.py`. Once it exists with a matching
# signature, this file needs zero changes.
# ---------------------------------------------------------------------------
try:
    from core.notifications import create_notification  # type: ignore
except ImportError:  # pragma: no cover - expected until Phase 3 ships
    def create_notification(*, recipient, actor, verb, target_type, target_id, payload=None):
        logger.debug(
            "core.notifications not available yet — skipping notification "
            "(%s -> %s: %s on %s:%s)",
            actor, recipient, verb, target_type, target_id,
        )


def notify_post_liked(post, actor):
    """Call from PostReactionAPIView.post(), only on the branch where a new
    PostLike was just created (status_msg == 'liked') — not on unlike or
    reaction-change."""
    if post.user_id == actor.id:
        return  # don't notify yourself
    create_notification(
        recipient=post.user,
        actor=actor,
        verb="liked",
        target_type="post",
        target_id=str(post.id),
    )


def notify_post_commented(post, comment):
    """Call from CommentCreateAPIView.post() after a new top-level
    PostComment is created. `comment` is a PostComment instance."""
    if post.user_id == comment.user_id:
        return
    create_notification(
        recipient=post.user,
        actor=comment.user,
        verb="commented",
        target_type="post",
        target_id=str(post.id),
        payload={"comment_id": str(comment.id)},
    )


# ---------------------------------------------------------------------------
# Share a post into a chat conversation (checklist item 61).
#
# Reuses the `message` app's existing attachment-message flow rather than
# reimplementing message-sending. The exact function name/signature in
# `message` wasn't visible when this was written — the call below is a
# best-guess based on the message-app's documented flow
# (`MessageViewSet`, attachment upload via `upload_view.py`). ADJUST the
# import + call to match your actual `message/services.py` (or wherever
# send-message logic lives) once you wire this up — everything else here
# stays the same.
# ---------------------------------------------------------------------------
def share_post_to_conversation(post, sender, conversation_id):
    """Raises NotImplementedError with a clear message if the message app's
    send function isn't available yet, so this fails loudly instead of
    silently doing nothing."""
    from .models import PostShare

    try:
        from message.services import send_message  # ADJUST to your real path
    except ImportError as exc:
        raise NotImplementedError(
            "message.services.send_message not found — wire this to your "
            "actual message-sending function (see comment in post/services.py)."
        ) from exc

    first_media = post.media.first()
    attachment_url = first_media.file.url if first_media else None

    message = send_message(
        conversation_id=conversation_id,
        sender=sender,
        text=post.content or "",
        shared_post_id=str(post.id),
        attachment_url=attachment_url,
    )

    # unique_together=['post', 'user'] on PostShare, mirroring how
    # PostLike/PostSave behave — get_or_create so re-sharing the same post
    # doesn't raise IntegrityError. `shares_count` updates itself via the
    # `update_shares_count` signal in models.py; no manual F() needed here.
    PostShare.objects.get_or_create(post=post, user=sender)

    return message