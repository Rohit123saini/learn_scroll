"""
post/story_share.py  (X1)

"Send this story in a chat". There was no such endpoint before X1 - `Story.allow_reshare` was stored and shown in the
audience sheet but nothing read it. This is the one place a story leaves its owner's audience through the app, so
the `allow_reshare` check lives in the VIEW (StoryShareAPIView, calls `story_visibility.can_reshare_story`) and the
message building lives here, mirroring `services.share_post_to_conversation`.

The message is an ordinary `story_reply` message (same pipeline, no new MessageType and therefore no message-app
migration): `story` + `story_reply_snapshot` give the chat bubble its thumbnail, `meta.story_share = True` tells the
chat UI it is a SHARE (not a reply to the story's owner). The sender is not the owner's follower necessarily, so
the viewer-side audience rules still apply when the receiver taps the bubble (the story endpoints answer 404 for
someone who may not see it).
"""
from django.core.exceptions import PermissionDenied

SHARE_TEXT = "Shared a story / एक स्टोरी शेयर की"


def share_story_to_conversation(story, sender, conversation_id, request=None):
    """Forward `story` into an existing conversation. Raises `Conversation.DoesNotExist` for a bad id and
    `django.core.exceptions.PermissionDenied` when `sender` is not a participant (the view maps both to 404 / 403).
    The caller has ALREADY checked visibility + `can_reshare_story`."""
    from message.models import Conversation, ConversationParticipant, MessageType
    from message.services import create_message_and_broadcast

    conversation = Conversation.objects.get(id=conversation_id)
    if not ConversationParticipant.objects.filter(conversation=conversation, user=sender).exists():
        raise PermissionDenied("You're not a participant in this conversation.")

    try:
        snapshot_url = None
        if story.media:
            snapshot_url = request.build_absolute_uri(story.media.url) if request is not None else story.media.url
    except ValueError:
        snapshot_url = None

    return create_message_and_broadcast(
        conversation=conversation,
        sender=sender,
        message_type=MessageType.STORY_REPLY,
        text=SHARE_TEXT,
        thumbnail_url=snapshot_url,
        meta={
            "story_share": True,
            "shared_story_id": str(story.id),
            "shared_story_owner": story.user.username,
            "shared_story_media_type": story.media_type,
        },
        extra_fields={"story": story, "story_reply_snapshot": snapshot_url},
    )


def saveable_media(story, request=None):
    """The data the app needs to download the story's media: {url, media_type, filename}. `None` when the story has no
    file. Only called after `can_save_story` said yes."""
    if not story.media:
        return None
    try:
        url = request.build_absolute_uri(story.media.url) if request is not None else story.media.url
    except ValueError:
        return None
    name = (story.media.name or "").rsplit("/", 1)[-1] or f"story_{story.id}"
    return {"url": url, "media_type": story.media_type, "filename": name}
