"""
post/highlights.py

STORIES UPGRADE - PART 3b (Highlights): the rules and read-side helpers shared
by post/highlight_views.py. A highlight is a named, permanent collection of the
owner's past stories (see `Highlight` / `HighlightItem` in models.py).

Which stories can go into a highlight ("the archive")
-----------------------------------------------------
Any story of the owner's that is still live OR that ended by itself (expired):

    is_deleted = False                              -> live, or expired but not swept yet
    is_deleted = True AND deleted_at >= expires_at  -> the `expire_old_stories` sweep got it

A story the owner removed BEFORE it expired (`deleted_at < expires_at`) is gone
for good and can be neither added nor shown. Stories that are in a highlight
are never hard-deleted (post/tasks.py). Everything else drops out of the
archive 30 days after it expired (`hard_delete_ancient_stories`).

Who can see a highlight
-----------------------
* the owner: everything;
* anyone else: not if there is a block in either direction, not if the owner
  is inactive, not if the owner is private and the viewer is not an accepted
  follower. Then per ITEM the story's own audience applies (a Close Friends
  story only shows to Close Friends - post/story_visibility.py). A highlight
  with no item the viewer may see does not exist for them (404 / left out of
  the list), so a Close Friends-only highlight is not leaked.

Highlight stories are READ-ONLY for viewers: they are past their 24 h, so the
`view` / `react` / `reply` / poll-vote / question-answer endpoints correctly
answer 404 for them. The client must not call those from a highlight.
"""
import re

from django.conf import settings
from django.db.models import F, Q

from user_profile.models import Follow

from .close_friends_views import blocked_user_ids
from .models import HighlightItem, Story
from .story_visibility import filter_stories_visible_to

DEFAULT_TITLE = "Highlights"
MAX_TITLE_LENGTH = 30
DEFAULT_MAX_HIGHLIGHTS = 50      # per user
DEFAULT_MAX_ITEMS = 100          # stories per highlight

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_SPACES = re.compile(r"\s+")


class HighlightError(Exception):
    """A rule was broken. Turned into a 400 by the views."""

    def __init__(self, message, code="invalid", field=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.field = field


def max_highlights():
    return int(getattr(settings, "STORY_MAX_HIGHLIGHTS", DEFAULT_MAX_HIGHLIGHTS))


def max_items():
    return int(getattr(settings, "STORY_MAX_HIGHLIGHT_ITEMS", DEFAULT_MAX_ITEMS))


def clean_title(raw, *, required):
    """Strip control characters, collapse whitespace, cap the length. A blank
    title becomes "Highlights" on create and is an error on update."""
    text = _SPACES.sub(" ", _CONTROL_CHARS.sub("", raw or "")).strip()
    if not text:
        if required:
            raise HighlightError("Title cannot be empty.", code="blank_title", field="title")
        return DEFAULT_TITLE
    if len(text) > MAX_TITLE_LENGTH:
        raise HighlightError(
            f"Title can be at most {MAX_TITLE_LENGTH} characters.", code="title_too_long", field="title",
        )
    return text


# --------------------------------------------------------------------------
# Archive / visibility
# --------------------------------------------------------------------------
def highlightable_stories_q():
    """See the module docstring: live, or ended by itself."""
    return Q(is_deleted=False) | Q(is_deleted=True, deleted_at__gte=F("expires_at"))


def archive_queryset(owner):
    """The owner's stories that can go into a highlight (newest first)."""
    return Story.objects.filter(user=owner).filter(highlightable_stories_q()).order_by("-created_at")


def visible_story_ids(viewer):
    """Subquery: highlight-eligible stories `viewer` may see (audience rules)."""
    return filter_stories_visible_to(
        Story.objects.filter(highlightable_stories_q()), viewer,
    ).values("pk")


def can_view_highlights_of(owner, viewer):
    """Profile-level gate (blocks / inactive / private). Item audience is
    applied separately, per story."""
    if owner.id == viewer.id:
        return True
    if not owner.is_active:
        return False
    if owner.id in blocked_user_ids(viewer):
        return False
    if getattr(owner, "is_private", False):
        return Follow.objects.filter(
            follower=viewer, following=owner, status=Follow.Status.ACCEPTED,
        ).exists()
    return True


# --------------------------------------------------------------------------
# Row builders (list endpoint + the header of the detail endpoint)
# --------------------------------------------------------------------------
def media_url(story, request):
    if not story.media:
        return None
    try:
        return request.build_absolute_uri(story.media.url) if request else story.media.url
    except ValueError:
        return None


def _owner_payload(user, request):
    from .serializers import get_profile_pic_url  # local: serializers imports a lot

    return {
        "id": str(user.id),
        "username": user.username,
        "profile_picture": get_profile_pic_url(user, request),
    }


def pick_cover_item(highlight, visible_items):
    """`visible_items` = list of (item_id, story) in position order. The chosen
    cover if it is a visible photo, else the first visible photo, else None
    (an all-video highlight - the client shows a placeholder)."""
    if highlight.cover_item_id:
        for item_id, story in visible_items:
            if item_id == highlight.cover_item_id and story.media_type == "image":
                return story
    for _item_id, story in visible_items:
        if story.media_type == "image":
            return story
    return None


def build_highlight_rows(highlights, viewer, request, *, hide_empty):
    """One dict per highlight: {id, title, cover_url, cover_story_id,
    items_count, created_at, updated_at, user}. `items_count` counts only the
    items `viewer` may see. With `hide_empty`, highlights with no visible item
    are dropped (what everybody but the owner gets)."""
    highlights = list(highlights)
    if not highlights:
        return []
    rows_by_highlight = {h.id: [] for h in highlights}
    item_qs = (
        HighlightItem.objects.filter(
            highlight_id__in=list(rows_by_highlight), story_id__in=visible_story_ids(viewer),
        )
        .select_related("story")
        .order_by("position", "created_at")
    )
    for item in item_qs:
        rows_by_highlight[item.highlight_id].append((item.id, item.story))

    out = []
    for h in highlights:
        visible = rows_by_highlight[h.id]
        if hide_empty and not visible:
            continue
        cover = pick_cover_item(h, visible)
        out.append({
            "id": str(h.id),
            "title": h.title,
            "cover_url": media_url(cover, request) if cover else None,
            "cover_story_id": str(cover.id) if cover else None,
            "items_count": len(visible),
            "created_at": h.created_at,
            "updated_at": h.updated_at,
            "user": _owner_payload(h.user, request),
        })
    return out
