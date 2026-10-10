"""
tuitionclass/moderation.py

Thin compatibility wrapper. The real engine now lives in
`common/moderation.py` so posts, comments, stories, bios and DMs share it.
`screen_message()` keeps its exact old contract — (is_flagged, reason) —
and the "chat" surface runs every check the old module ran, so
ChatMessageViewSet and its tests are untouched.

Still honours settings.TUITIONCLASS_PROFANITY_WORDS (see common/moderation.py
for the lookup order).
"""

from common.moderation import DEFAULT_PROFANITY_WORDS, screen_text  # noqa: F401


def screen_message(text: str):
    """Screen one classroom chat message. Never raises. See common/moderation.py."""
    return screen_text(text, surface="chat")
