# message/mentions.py
#
# 🔥 NAYA — @mentions ke liye SHARED helper. Message text ke andar
# "@username" likhne par us conversation ke ACTIVE members me se match
# karke user-ids resolve karte hain. Dono jagah (REST `ConversationViewSet
# .messages` POST aur WebSocket `ChatConsumer.handle_new_message`) yahi
# function reuse karte hain taaki mention-detection logic ek hi jagah rahe
# aur dono path pe same behave kare (jaisa `group_rules.py` group-permission
# checks ke liye already hai).
#
# Sirf CONVERSATION ke members hi mention ho sakte hain (koi bhi random
# @username nahi) — isse (a) galti se kisi outsider ko notify nahi hota,
# (b) query chhoti rehti hai (poore User table pe scan nahi karna padta).

import re

# @username — letters/digits/underscore, WhatsApp/Instagram jaisa.
MENTION_RE = re.compile(r'@(\w+)')


def extract_mentioned_user_ids(text, conversation):
    """
    `text` me se "@username" nikaal kar is `conversation` ke active
    members ke against match karta hai (case-insensitive). Match na hone
    par ya text khali hone par khali list.
    """
    if not text:
        return []

    mentioned_usernames = {m.lower() for m in MENTION_RE.findall(text)}
    if not mentioned_usernames:
        return []

    from .models import ConversationParticipant

    members = ConversationParticipant.objects.filter(
        conversation=conversation, left_at__isnull=True,
    ).exclude(user__username__isnull=True).exclude(user__username='').select_related('user')

    return [
        m.user_id for m in members
        if m.user.username.lower() in mentioned_usernames
    ]