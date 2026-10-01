"""
post/story_visibility.py

STORIES UPGRADE - PART 1 (Close Friends): the single source of truth for
"can this viewer see this story?". Every story read path (list, view, react,
reply) goes through here so a `close_friends` story can never leak through a
code path that forgot the check.

Rules
-----
* The owner always sees their own stories.
* `audience == 'everyone'`  -> unchanged from before (no extra restriction).
* `audience == 'close_friends'` -> only if the owner has a CloseFriend row
  for the viewer.

A story the viewer may not see is reported as *not found* (404), never 403,
so the existence of a close-friends story is not leaked to outsiders.
"""
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import CloseFriend, Story


def filter_stories_visible_to(queryset, viewer):
    """Narrow a Story queryset to what `viewer` is allowed to see (audience
    only - follow/expiry filters stay in the caller)."""
    on_owners_list = Exists(
        CloseFriend.objects.filter(owner_id=OuterRef('user_id'), friend=viewer)
    )
    return queryset.annotate(_on_close_friends=on_owners_list).filter(
        Q(audience=Story.AUDIENCE_EVERYONE)
        | Q(user=viewer)
        | Q(audience=Story.AUDIENCE_CLOSE_FRIENDS, _on_close_friends=True)
    )


def can_view_story(story, viewer):
    if story.user_id == viewer.id:
        return True
    if story.audience != Story.AUDIENCE_CLOSE_FRIENDS:
        return True
    return CloseFriend.objects.filter(owner_id=story.user_id, friend=viewer).exists()


def get_visible_story_or_404(viewer, story_id):
    """`get_object_or_404(Story, id=..., is_deleted=False)` + audience check."""
    story = get_object_or_404(Story, id=story_id, is_deleted=False)
    if not can_view_story(story, viewer):
        raise Http404("Story not found.")
    return story
