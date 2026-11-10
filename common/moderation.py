"""
common/moderation.py

ONE shared text-screening engine for every user-generated surface (posts,
comments, stories, bios, chat/DMs). It was extracted from
`tuitionclass/moderation.py`, which only ever screened the classroom chat;
that module is now a thin wrapper around this one so its behaviour (and
its tests) are unchanged.

CONTRACT (provider-agnostic, same as before):
    screen_text(text, surface) -> (is_flagged: bool, reason: str)

* It only ever FLAGS (adds to a moderator review queue) — it never blocks a
  save and never deletes anything. A false positive is therefore cheap.
* It never raises: any internal error means "not flagged".
* `surface` decides WHICH checks run, because the same text is fine in one
  place and a problem in another (a link in a post caption is normal; a
  link in a comment or a class chat is usually spam):

      chat     tuition classroom chat   -> everything (original behaviour)
      comment  post comments            -> profanity, link, contact, repeats
      dm       private messages         -> profanity + flood only
      post     post title/body          -> profanity + flood only
      story    story caption            -> profanity + flood only
      bio      profile bio              -> profanity + flood only

Word list: settings.MODERATION_PROFANITY_WORDS, falling back to the old
settings.TUITIONCLASS_PROFANITY_WORDS, falling back to DEFAULT below.

The cheap check here is the first pass. The optional AI second pass lives in
common/moderation_ai.py and is run from a Celery task, never inline.
"""

import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

SURFACES = ("chat", "comment", "dm", "post", "story", "bio")

DEFAULT_PROFANITY_WORDS = {
    "fuck", "fucker", "fucking", "shit", "bitch", "asshole", "bastard",
    "slut", "whore", "dick", "pussy", "cunt", "nigger", "faggot",
    "chutiya", "madarchod", "behenchod", "bhosdike", "gaandu", "randi",
    "harami", "kamina", "kutta", "kutte", "saala",
}

_LEET_MAP = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})

_URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+?\d[\s-]?){9,13}\d")
_CONTACT_APP_RE = re.compile(r"\b(whatsapp|telegram|insta(?:gram)?|snapchat)\b", re.IGNORECASE)
_REPEATED_CHAR_RE = re.compile(r"(.)\1{5,}")
_REPEATED_WORD_RE = re.compile(r"\b(\w+)\b(?:\W+\1\b){3,}", re.IGNORECASE)

# NSFW / sexual-content words (publish gate, post/publish_gate.py). Kept apart from the
# profanity list on purpose: swearing is "abusive", this is "adult content" and the
# review queue shows them as two different reasons. Override: settings.MODERATION_NSFW_WORDS.
DEFAULT_NSFW_WORDS = {
    # Deliberately NOT here: "sex" (sex chromosomes, sex ratio), "naked" (naked-eye astronomy),
    # "nipple" (anatomy), "escort" - a student app would hold far too many normal posts.
    "porn", "porno", "pornhub", "xxx", "nude", "nudes", "sexy", "sexting", "blowjob", "handjob",
    "orgasm", "erotic", "hentai", "onlyfans", "boobs", "horny", "camgirl",
    "chudai", "lauda", "bhabhi sex", "desi sex", "mms leak",
}
# Multi-word / obfuscated forms the plain word match would miss ("s e x", "p0rn", "nude pics").
_NSFW_PHRASE_RE = re.compile(
    r"(?:\bnude\s+(?:pics?|photos?|videos?|leak\w*)\b|\b(?:send|share|dm)\s+(?:me\s+)?(?:nudes?|nsfw)\b"
    r"|\bs\s*e\s*x\s*(?:video|chat|tape)\b|\b18\s*\+\s*(?:video|content|pics?)\b|\bnsfw\b)",
    re.IGNORECASE,
)

# Which optional checks each surface gets. profanity + repeated chars/words
# run everywhere; the rest are opt-in per surface.
_EXTRA_CHECKS = {
    "chat": {"link", "contact", "caps"},
    "comment": {"link", "contact"},
    "dm": set(),
    "post": set(),
    "story": set(),
    "bio": set(),
}

# Reason slug -> severity, used by the review queue to sort.
SEVERITY = {
    "profanity": "high",
    "nsfw": "high",
    "nsfw_media": "high",   # P-F15: an image / video the MEDIA_NSFW_PROVIDER called adult (common/media_moderation.py)
    "audio_copyright": "medium", "audio_copyright_match": "high",
    "duplicate_content": "low", "duplicate_media": "low",
    "ai_hate": "high", "ai_harassment": "high", "ai_sexual": "high",
    "ai_violence": "high", "ai_self_harm": "high", "ai_abuse": "high",
    "spam_contact_info": "medium", "spam_link": "low",
    "minor_contact_info": "high",  # P11: phone number / chat-app handle in an under-18 account's profile text
    "spam_repeated_chars": "low", "spam_repeated_words": "low", "spam_all_caps": "low",
}


def severity_for(reason: str) -> str:
    return SEVERITY.get(reason, "medium")


def profanity_words() -> set:
    configured = getattr(settings, "MODERATION_PROFANITY_WORDS", None) or getattr(
        settings, "TUITIONCLASS_PROFANITY_WORDS", None
    )
    return {w.lower() for w in configured} if configured else DEFAULT_PROFANITY_WORDS


def nsfw_words() -> set:
    configured = getattr(settings, "MODERATION_NSFW_WORDS", None)
    return {w.lower() for w in configured} if configured else DEFAULT_NSFW_WORDS


def normalize(text: str) -> str:
    return text.lower().translate(_LEET_MAP)


def has_contact_info(text) -> bool:
    """P11: True when `text` has a phone-number-like run of digits or names a chat app (WhatsApp, Telegram, Insta,
    Snapchat). Used for under-18 profiles, where such details are a safety problem, not just spam. Never raises."""
    try:
        if not text or not isinstance(text, str):
            return False
        return bool(_PHONE_RE.search(text) or _CONTACT_APP_RE.search(text))
    except Exception:
        return False


def screen_text(text, surface: str = "chat"):
    """Return (is_flagged, reason). Never raises."""
    if not text or not isinstance(text, str):
        return False, ""
    extra = _EXTRA_CHECKS.get(surface, set())
    try:
        normalized = normalize(text)

        for word in profanity_words():
            if re.search(rf"\b{re.escape(word)}\b", normalized):
                return True, "profanity"

        if "link" in extra and _URL_RE.search(text):
            return True, "spam_link"

        if "contact" in extra and _PHONE_RE.search(text):
            return True, "spam_contact_info"

        if _REPEATED_CHAR_RE.search(text):
            return True, "spam_repeated_chars"
        if _REPEATED_WORD_RE.search(normalized):
            return True, "spam_repeated_words"

        if "caps" in extra:
            letters = [c for c in text if c.isalpha()]
            if len(letters) >= 12 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.8:
                return True, "spam_all_caps"

        return False, ""
    except Exception:
        logger.exception("screen_text failed (surface=%s) — leaving it unflagged", surface)
        return False, ""


def screen_nsfw(text):
    """Return (is_flagged, reason). `reason` is always "nsfw" when flagged. Never raises.

    Separate from screen_text() so the PUBLISH gate can tell "abusive" (profanity) from
    "adult content" and treat them differently in the moderator queue. Same normalisation
    (leet-speak: "p0rn", "s3x") and whole-word matching, so "Essex" / "Sussex" are not hit."""
    if not text or not isinstance(text, str):
        return False, ""
    try:
        normalized = normalize(text)
        for word in nsfw_words():
            if re.search(rf"\b{re.escape(word)}\b", normalized):
                return True, "nsfw"
        if _NSFW_PHRASE_RE.search(text) or _NSFW_PHRASE_RE.search(normalized):  # raw too: "18+" is digits
            return True, "nsfw"
        return False, ""
    except Exception:
        logger.exception("screen_nsfw failed - leaving it unflagged")
        return False, ""
