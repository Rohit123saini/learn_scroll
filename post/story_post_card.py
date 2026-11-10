"""
post/story_post_card.py  (F3-AUD)

"Share a post / reel to my story" = a `post_card` sticker. The stored sticker only holds {post_id, post_kind}; what
the viewer sees (thumbnail, title, author) is built HERE at read time, per viewer, so:
  * a post that was deleted / archived / made private later shows as `available: false` (no thumbnail, no text),
  * a viewer who may not see the post (audience rules of post/audience.py) never gets its preview through a story.

Pure helpers - no request object is needed except to make the thumbnail URL absolute.
"""
import uuid

from .models import Post

PREVIEW_TEXT_MAX = 90
KIND_POST = "post"
KIND_REEL = "reel"
VALID_KINDS = (KIND_POST, KIND_REEL)


def guess_post_kind(post):
    """A short video is a reel; everything else is a normal post card."""
    return KIND_REEL if post.post_type == "video" else KIND_POST


def parse_post_id(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def can_share_post_to_story(post, user):
    """The post must be live and visible to `user`; somebody else's post must also be public (a followers-only post
    must not be re-published to a wider audience by a follower). Returns (ok, reason)."""
    from .audience import can_view_post

    if post is None or post.is_deleted or post.is_archived or post.is_scheduled:
        return False, "That post is not available."
    if post.user_id == user.id:
        return True, ""
    if post.moderation_status != "approved" or post.is_sensitive:
        return False, "That post is not available."
    if not can_view_post(post, user):
        return False, "That post is not available."
    if post.visibility != "public":
        return False, "Only public posts can be shared to a story."
    from .post_options import RESHARE_OFF_TEXT, can_reshare_post  # S4b: the author may switch resharing off

    if not can_reshare_post(post, user):
        return False, RESHARE_OFF_TEXT
    return True, ""


def _first_media_thumb(post, request):
    media = next(iter(post.media.all()), None)
    if media is None:
        return None
    f = None
    if media.media_type in ("image", "gif"):
        f = media.thumb_320 or media.medium_720 or media.file
    elif media.media_type == "video":
        f = media.thumbnail
    if not f:
        return None
    try:
        return request.build_absolute_uri(f.url) if request else f.url
    except ValueError:
        return None


def build_preview(data, viewer, request, profile_pic_url):
    """`data` = the stored sticker data. Returns the dict the client renders."""
    data = data or {}
    out = {
        "post_id": data.get("post_id"),
        "post_kind": data.get("post_kind") or KIND_POST,
        "available": False,
        "title": "",
        "thumb": None,
        "author": None,
    }
    pid = parse_post_id(data.get("post_id"))
    if pid is None:
        return out
    from .audience import can_view_post

    post = (
        Post.objects.filter(id=pid, is_deleted=False, is_archived=False, is_scheduled=False)
        .select_related("user").prefetch_related("media").first()
    )
    if post is None or viewer is None or not getattr(viewer, "is_authenticated", False):
        return out
    if post.user_id != viewer.id and (post.moderation_status != "approved" or not can_view_post(post, viewer)):
        return out
    text = (post.title or post.content or "").strip().replace("\n", " ")
    out.update({
        "available": True,
        "post_kind": data.get("post_kind") or guess_post_kind(post),
        "title": text[:PREVIEW_TEXT_MAX],
        "thumb": _first_media_thumb(post, request),
        "author": {
            "id": str(post.user.id),
            "username": post.user.username,
            "profile_picture": profile_pic_url(post.user, request),
        },
    })
    return out
