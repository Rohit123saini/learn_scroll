"""
post/reply_reels.py - "reply to a comment with a reel".

A reply (PostComment with a parent) may carry ONE video post of the replier's own (`PostComment.reply_reel`).
  resolve_reply_reel(user, reel_id)   validates + returns the Post (raises ReplyReelError)
  reply_reel_payload(post, request)   the card the comment list shows (or an "unavailable" stub)

Rules: only the replier's OWN, published (approved, not scheduled / archived / deleted), public video post can be
attached - a followers-only / private reel would leak to everybody who can read the thread. The card never exposes
more than the thumbnail, a short caption, the duration and the author; tapping it opens the normal reel viewer, which
applies its own visibility rules. If the reel is later archived / deleted / hidden, the card turns into
{"id", "available": false} instead of disappearing, so the thread still reads naturally.
"""
from .models import Post


class ReplyReelError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def resolve_reply_reel(user, reel_id):
    post = Post.objects.filter(pk=reel_id).first()
    if post is None or post.is_deleted:
        raise ReplyReelError("reel_not_found", "Reel not found", 404)
    if post.user_id != user.id:
        raise ReplyReelError("reel_not_own", "You can only reply with your own reel", 403)
    if post.post_type != "video":
        raise ReplyReelError("not_a_reel", "Only a video post can be attached", 400)
    if post.original_post_id:
        raise ReplyReelError("not_a_reel", "A repost can't be attached, use the original reel", 400)
    if post.is_archived or post.is_scheduled or post.moderation_status != "approved":
        raise ReplyReelError("reel_not_published", "Only a published reel can be attached", 400)
    if post.visibility != "public":
        raise ReplyReelError("reel_not_public", "Only a public reel can be attached to a reply", 400)
    return post


def _abs(request, f):
    if not f:
        return None
    try:
        url = f.url
    except ValueError:
        return None
    return request.build_absolute_uri(url) if request else url


def reply_reel_payload(post, request=None):
    """Card for a comment. Never raises."""
    if post is None:
        return None
    viewer = getattr(request, "user", None)
    from .moderation_guard import post_hidden_for
    if post_hidden_for(viewer, post) or (post.visibility != "public" and (viewer is None or viewer.id != post.user_id)):
        return {"id": str(post.pk), "available": False}
    media = next((m for m in sorted(post.media.all(), key=lambda m: m.display_order) if m.media_type == "video"), None)
    return {
        "id": str(post.pk),
        "available": True,
        "thumbnail": _abs(request, media.thumbnail) if media is not None else None,
        "duration": media.duration_seconds if media is not None else None,
        "caption": (post.content or "")[:120],
        "author": {"id": post.user_id, "username": getattr(post.user, "username", "")},
    }
