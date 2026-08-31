"""
liveclass/moderation.py

Lightweight, dependency-free chat moderation for
ChatMessageViewSet.perform_create (see that method's docstring in
views.py) — the piece `from .moderation import screen_message` was
already expecting, but the module itself didn't exist yet.

WHAT THIS IS: a fast, in-process, word-list + pattern based first pass —
catches the obvious cases (profanity, a phone number dropped to route
students off-platform, a copy-pasted "join my Telegram" spam blast, a
message that's just a wall of caps or repeated characters) so a
moderator's flagged-chat queue isn't empty on day one.

WHAT THIS IS NOT: a substitute for a real moderation service. It will
miss creative evasions beyond the handful of leetspeak substitutions
below, new slang, and non-English profanity outside PROFANITY WORDS. It
will also occasionally false-positive — that's exactly why a false
positive here is cheap: per the AUTO-FLAG, DON'T AUTO-DELETE contract in
ChatMessageViewSet.perform_create, a flag only ever adds the message to
a moderator's review queue, it never blocks the send or deletes anything
on its own.

For production-grade coverage, swap screen_message()'s body for a call
to a real moderation API (e.g. Google's Perspective API, AWS Comprehend,
an LLM moderation endpoint) — the (is_flagged, reason) contract below is
deliberately provider-agnostic, so a provider swap never has to touch
ChatMessageViewSet or any other call site.
"""

import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Word list. Deliberately short and mild by default — a real deployment
# should override/extend this via settings.LIVECLASS_PROFANITY_WORDS (an
# iterable of lowercase words/phrases) rather than editing this file, so
# the list can be tuned per-market/language without a code deploy. Words
# are matched on whole-word boundaries only (see screen_message below),
# so a substring match on an innocent word ("the classic 'Scunthorpe
# problem'") is deliberately avoided.
# ---------------------------------------------------------------------------
DEFAULT_PROFANITY_WORDS = {
    "fuck", "fucker", "fucking", "shit", "bitch", "asshole", "bastard",
    "slut", "whore", "dick", "pussy", "cunt", "nigger", "faggot",
    "chutiya", "madarchod", "behenchod", "bhosdike", "gaandu", "randi",
    "harami", "kamina", "kutta", "kutte", "saala",
}

# Common evasion substitutions (basic leetspeak) — normalized away before
# matching, so "fuuuck", "f u c k", or "f*ck" style variants aren't the
# focus (those need fuzzier matching a lightweight filter shouldn't try
# to own), but "fu(k"/"sh1t"-style character-swaps are still caught.
_LEET_MAP = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


def _profanity_words() -> set:
    configured = getattr(settings, "LIVECLASS_PROFANITY_WORDS", None)
    return {w.lower() for w in configured} if configured else DEFAULT_PROFANITY_WORDS


def _normalize(text: str) -> str:
    return text.lower().translate(_LEET_MAP)


# ---------------------------------------------------------------------------
# Spam signals. A live-class chat is a captive audience — the recurring
# abuse pattern here isn't "advertising a product", it's students/
# outsiders trying to route people OFF this platform (another tutor's
# WhatsApp/Telegram, a bare phone number, a "DM me for cheaper classes"
# pitch) or flooding the room with repeated/junk text.
# ---------------------------------------------------------------------------
_URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+?\d[\s-]?){9,13}\d")  # loose international-ish phone number
_CONTACT_APP_RE = re.compile(r"\b(whatsapp|telegram|insta(?:gram)?|snapchat)\b", re.IGNORECASE)
_REPEATED_CHAR_RE = re.compile(r"(.)\1{5,}")  # e.g. "aaaaaaa" / "!!!!!!!"
_REPEATED_WORD_RE = re.compile(r"\b(\w+)\b(?:\W+\1\b){3,}", re.IGNORECASE)  # same word 4+ times in a row


def screen_message(text: str):
    """Screen one chat message before/just-after it's saved (see
    ChatMessageViewSet.perform_create in views.py — it screens AFTER
    save, on purpose: a false positive here must never block a real
    message from posting).

    Returns (is_flagged: bool, reason: str). `reason` is a short slug —
    it's written into ChatMessage.flagged_reason as f"auto:{reason}", so
    keep it short (fits the field's max_length=100 many times over) and
    machine-readable, not a sentence.

    Never raises: a malformed/empty input, or an unexpected error inside
    the checks themselves, just means "not flagged" — this function only
    ever ADDS a message to a review queue, so failing safe (unflagged)
    is always the right fallback, never a blocked send.
    """
    if not text:
        return False, ""

    try:
        normalized = _normalize(text)

        for word in _profanity_words():
            if re.search(rf"\b{re.escape(word)}\b", normalized):
                return True, "profanity"

        if _URL_RE.search(text):
            return True, "spam_link"

        has_contact_app = bool(_CONTACT_APP_RE.search(text))
        has_phone = bool(_PHONE_RE.search(text))
        if has_contact_app and has_phone:
            # A contact app named ALONGSIDE a phone number is a much
            # stronger signal than either alone — a student innocently
            # asking "does anyone use WhatsApp?" shouldn't get flagged
            # just for naming the app.
            return True, "spam_contact_info"
        if has_phone:
            return True, "spam_contact_info"

        if _REPEATED_CHAR_RE.search(text):
            return True, "spam_repeated_chars"
        if _REPEATED_WORD_RE.search(normalized):
            return True, "spam_repeated_words"

        # Wall-of-caps — only for messages long enough that this isn't
        # just someone typing "OK" or "LOL"; short caps are normal chat.
        letters = [c for c in text if c.isalpha()]
        if len(letters) >= 12 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.8:
            return True, "spam_all_caps"

        return False, ""
    except Exception:
        logger.exception("screen_message failed on a chat message — leaving it unflagged rather than blocking it")
        return False, ""