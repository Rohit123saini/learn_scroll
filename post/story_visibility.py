"""
post/story_visibility.py

The single source of truth for "can this viewer see / reply to this story?". Every story read path (list, view,
react, reply, detail, highlights, sticker owner checks) goes through here so a restricted story can never leak
through a code path that forgot the check.

Audiences (Story.audience)
--------------------------
* The owner always sees their own stories.
* `StoryHiddenFrom` (custom "hide from") wins over every audience: a hidden person gets 404.
* `everyone`      -> no extra restriction (it reaches the tray of the owner's followers; blocks are checked by callers).
* `followers`     -> only accepted followers of the owner.
* `close_friends` -> only people on the owner's CloseFriend list.
* `class`         -> only members of `audience_classroom_id` (its teacher, or a successful active pass holder - the
                     same rule post/audience.classroom_ids_of uses for class posts).

A story the viewer may not see is reported as *not found* (404), never 403, so its existence is not leaked.

Replies (Story.reply_setting): `everyone` / `followers` (accepted followers, or the owner) / `off` (nobody).
`can_reply_to_story` covers quick reactions as well as text replies.

Share / save (Story.allow_reshare / Story.allow_save) - X1
----------------------------------------------------------
`can_reshare_story` (send the story into a chat) and `can_save_story` (download its media to the device) are the
single source of truth for those two switches. The owner can ALWAYS do both with their own story. For everybody else
`False` -> the endpoint answers 403 with code `STORY_RESHARE_OFF` / `STORY_SAVE_OFF` (the story itself is still
visible, so unlike an audience miss this is a 403, not a 404). The serializer also exposes the answer per viewer
(`can_reshare` / `can_save`) so the app can hide the buttons.
"""
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import CloseFriend, Story, StoryHiddenFrom


def _follow_model():
    from user_profile.models import Follow
    return Follow


def _classroom_ids(viewer):
    from .audience import classroom_ids_of
    try:
        return set(classroom_ids_of(viewer))
    except Exception:  # tuitionclass not importable: class stories are simply invisible
        return set()


def filter_stories_visible_to(queryset, viewer):
    """Narrow a Story queryset to what `viewer` is allowed to see (audience + hide-from only - follow / expiry /
    mute filters stay in the caller)."""
    Follow = _follow_model()
    on_owners_list = Exists(CloseFriend.objects.filter(owner_id=OuterRef('user_id'), friend=viewer))
    follows_owner = Exists(Follow.objects.filter(
        follower=viewer, following_id=OuterRef('user_id'), status=Follow.Status.ACCEPTED,
    ))
    hidden_from_me = Exists(StoryHiddenFrom.objects.filter(story_id=OuterRef('pk'), user=viewer))
    my_classrooms = list(_classroom_ids(viewer))
    allowed = (
        Q(audience=Story.AUDIENCE_EVERYONE)
        | Q(audience=Story.AUDIENCE_FOLLOWERS, _follows_owner=True)
        | Q(audience=Story.AUDIENCE_CLOSE_FRIENDS, _on_close_friends=True)
        | Q(audience=Story.AUDIENCE_CLASS, audience_classroom_id__in=my_classrooms)
    )
    return (
        queryset.annotate(
            _on_close_friends=on_owners_list, _follows_owner=follows_owner, _hidden_from_me=hidden_from_me,
        )
        .filter(Q(user=viewer) | (Q(_hidden_from_me=False) & allowed))
    )


def can_view_story(story, viewer):
    if story.user_id == viewer.id:
        return True
    if StoryHiddenFrom.objects.filter(story=story, user=viewer).exists():
        return False
    audience = story.audience
    if audience == Story.AUDIENCE_EVERYONE:
        return True
    if audience == Story.AUDIENCE_FOLLOWERS:
        Follow = _follow_model()
        return Follow.objects.filter(
            follower=viewer, following_id=story.user_id, status=Follow.Status.ACCEPTED,
        ).exists()
    if audience == Story.AUDIENCE_CLOSE_FRIENDS:
        return CloseFriend.objects.filter(owner_id=story.user_id, friend=viewer).exists()
    if audience == Story.AUDIENCE_CLASS:
        return story.audience_classroom_id in _classroom_ids(viewer)
    return False  # unknown audience value: closed, never open


def can_reply_to_story(story, viewer):
    """True when `viewer` may reply / react. The caller has already checked `can_view_story`."""
    if story.user_id == viewer.id:
        return True
    setting = story.reply_setting
    if setting == Story.REPLY_OFF:
        return False
    if setting == Story.REPLY_FOLLOWERS:
        Follow = _follow_model()
        return Follow.objects.filter(
            follower=viewer, following_id=story.user_id, status=Follow.Status.ACCEPTED,
        ).exists()
    return True


# X1: machine-readable codes the app switches on (kept in one place so views, tests and the app agree).
STORY_RESHARE_OFF = "story_reshare_off"
STORY_SAVE_OFF = "story_save_off"
STORY_RESHARE_OFF_MESSAGE = "The owner has turned off sharing for this story. / मालिक ने इस स्टोरी का शेयर बंद किया है।"
STORY_SAVE_OFF_MESSAGE = "The owner has turned off saving for this story. / मालिक ने इस स्टोरी का सेव बंद किया है।"


def _is_owner(story, viewer):
    return viewer is not None and getattr(viewer, "is_authenticated", False) and story.user_id == viewer.id


def can_reshare_story(story, viewer):
    """True when `viewer` may send this story into a chat. The caller has already checked `can_view_story`.
    Owner: always. Anonymous: never. Everybody else: `Story.allow_reshare`."""
    if viewer is None or not getattr(viewer, "is_authenticated", False):
        return False
    if _is_owner(story, viewer):
        return True
    return bool(story.allow_reshare)


def can_save_story(story, viewer):
    """True when `viewer` may download this story's media. Owner: always. Anonymous: never. Else `Story.allow_save`."""
    if viewer is None or not getattr(viewer, "is_authenticated", False):
        return False
    if _is_owner(story, viewer):
        return True
    return bool(story.allow_save)


def get_visible_story_or_404(viewer, story_id):
    """`get_object_or_404(Story, id=..., is_deleted=False)` + audience check."""
    story = get_object_or_404(Story, id=story_id, is_deleted=False)
    if not can_view_story(story, viewer):
        raise Http404("Story not found.")
    return story
