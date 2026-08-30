# message/media_utils.py
#
# 🔥 NAYI FILE — `views.py` line 69 pehle se `from .media_utils import
# create_group_media_for_message` karta tha, par ye file kabhi upload/
# banayi hi nahi gayi thi. Matlab jaise hi `views.py` import hota (Django
# startup pe hi, ya first request pe agar lazy) — `ModuleNotFoundError:
# No module named 'message.media_utils'` aata, aur poora app crash ho
# jaata. Ye sabse pehle fix karne wali cheez thi, kyunki baaki sab kuch
# (GroupMedia gallery, group_deleted/disappearing_messages_updated WS
# handlers, MAX_PINNED_PER_CONVERSATION) is file ke bina test hi nahi ho
# sakta tha.
#
# Is function ka kaam: jab bhi ek MEDIA message (image/video/audio/file/
# presentation) kisi GROUP conversation me successfully create ho jaaye,
# uski ek corresponding `GroupMedia` row bana do — taaki
# `GroupViewSet.media` (gallery endpoint, `/groups/<id>/media/`) ko
# har baar poore `Message` table scan na karna pade.
#
# DONO jagah se call hota hai (REST aur WS), taaki gallery kisi bhi path
# se bheje gaye message ke liye consistently update ho:
#   1. views.py -> ConversationViewSet.messages (POST)   [already wired]
#   2. consumers.py -> ChatConsumer.save_message (WS)    [is session me wire kiya]

import logging

from .models import ConversationType, GroupMedia, MessageType

logger = logging.getLogger(__name__)

# Sirf ye types "media" ginte hain gallery ke liye — text/location/system/
# study_room isme nahi aate (wo gallery me dikhane layak content nahi hai).
_GALLERY_MESSAGE_TYPES = {
    MessageType.IMAGE,
    MessageType.VIDEO,
    MessageType.AUDIO,
    MessageType.FILE,
    MessageType.PRESENTATION,
}


def create_group_media_for_message(message) -> "GroupMedia | None":
    """
    `message` ke liye (agar applicable ho) ek `GroupMedia` row banata hai.

    Applicable tabhi hai jab:
      - conversation GROUP type ho (private chat ki gallery concept hi
        nahi hai is app me)
      - message.type ek media type ho (`_GALLERY_MESSAGE_TYPES`)
      - message ke paas koi file_url ho (ya `file_urls` list ka pehla item)

    `GroupMedia.message` OneToOne hai, isliye `get_or_create` use karte
    hain — agar kisi wajah se ye function ek hi message ke liye do baar
    call ho jaaye (retry, duplicate signal, waghera) to IntegrityError
    nahi aayega, bas existing row wapas mil jaayegi.

    Failure yahan kabhi bhi message-send ko fail nahi karni chahiye —
    gallery ek "nice to have" derived table hai, asli source of truth
    hamesha `Message` hi hai. Isliye saari exceptions yahin pakad ke sirf
    log karte hain, calling transaction ko todte nahi.
    """
    try:
        if message.type not in _GALLERY_MESSAGE_TYPES:
            return None

        conversation = message.conversation
        if conversation.type != ConversationType.GROUP:
            return None

        group = getattr(conversation, "group_detail", None)
        if group is None:
            # Data inconsistency (GROUP conversation but no Group row) —
            # ho hi nahi sakta normally, par crash karne se behtar hai
            # chup-chaap skip karna.
            logger.warning(
                "create_group_media_for_message: group conversation %s ke paas group_detail nahi hai",
                conversation.id,
            )
            return None

        # Multi-image message me `file_url` khali ho sakta hai aur asli
        # files `file_urls` (JSON list) me hoti hain — us case me gallery
        # thumbnail ke liye pehla image use kar lete hain.
        file_url = message.file_url or (message.file_urls[0] if message.file_urls else None)
        if not file_url:
            return None

        media, _created = GroupMedia.objects.get_or_create(
            message=message,
            defaults={
                "group": group,
                "conversation": conversation,
                "sender": message.sender,
                "file_url": file_url,
                "file_type": message.type,
                "file_size": (message.meta or {}).get("size"),
                "thumbnail_url": message.thumbnail_url or None,
            },
        )
        return media

    except Exception as e:
        # Gallery row banana fail ho jaaye to bhi message khud successfully
        # bhej diya gaya hai — user ko is failure ka pata bhi nahi chalna
        # chahiye. Sirf log karo taaki baad me investigate ho sake.
        logger.exception(
            "create_group_media_for_message failed for message=%s: %s",
            getattr(message, "id", None), e,
        )
        return None