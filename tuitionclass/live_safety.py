"""
tuitionclass/live_safety.py  -  X12 LIVE SAFETY RULES

One place for the rules that keep a live class safe. Everything here is either PURE (text in -> decision out) or a tiny
read-only DB helper, and nothing here raises into a request: a broken rule must never take the live room down.

    1. WORD FILTER   filter_chat_text()   what to do with ONE chat line (allow / flag / mask / block)
    2. AGE RULES     age_group(), minors_present(), strict_contact_rule(), stage_invite_error(),
                     public_clip_blocked_by_minors()
    3. REC NOTICE    notice_payload(), recording_blocks_publish()   (the notice is stored by recording_consent.py)

HOW IT FITS WITH WHAT ALREADY EXISTS
    * The word list and the leet-speak normaliser are common/moderation.py's (ONE list for the whole app). This module
      only adds what a LIVE room needs on top: masking / blocking (common.screen_text only ever flags) and the
      minor-contact rule.
    * Age comes from login/age.py (DOB on file, ADULT_AGE = 18). A user with NO date of birth is "unknown" - treated like
      an adult by every rule below (nothing is silently assumed about them, same contract as login/age.py), EXCEPT where
      a rule says otherwise.

SETTINGS (all optional, defaults in brackets)
    LIVE_CHAT_FILTER_DEFAULT         off | flag | mask | block   ["mask"]   a session can override (chat_filter_mode)
    LIVE_MINOR_STAGE_POLICY          allow | with_notice | never ["with_notice"]
    LIVE_PUBLIC_RECORDING_WITH_MINORS  bool                       [False]    public / followers clip of a class that had a minor
    LIVE_MINOR_CACHE_SECONDS         int                          [60]
"""

import hashlib
import logging
import re
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def _cfg(name, default):
    return getattr(settings, name, default)


# ---------------------------------------------------------------------------
# 1. WORD FILTER
# ---------------------------------------------------------------------------
FILTER_MODES = ("off", "flag", "mask", "block")

# Contact details: the way a stranger moves a child off the platform. Kept here (not imported from common.moderation)
# because those regexes are private there and this rule needs the e-mail / more apps too.
_URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+?\d[\s-]?){9,13}\d")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CONTACT_APP_RE = re.compile(
    r"\b(whatsapp|whats\s*app|telegram|insta(?:gram)?|snap(?:chat)?|discord|signal|kik|skype|imo)\b", re.IGNORECASE,
)

DUPLICATE_WINDOW_SECONDS = 20  # live_abuse.py uses the same number for its repeat-message guard


@dataclass(frozen=True)
class FilterResult:
    text: str          # what to store / broadcast (masked when action == "mask")
    action: str        # allow | flag | mask | block
    reason: str = ""   # profanity | contact_info | spam_* ...

    @property
    def blocked(self) -> bool:
        return self.action == "block"

    @property
    def touched(self) -> bool:
        return self.action != "allow"


def effective_filter_mode(session) -> str:
    """The session's own mode, else settings.LIVE_CHAT_FILTER_DEFAULT, else "mask"."""
    mode = (getattr(session, "chat_filter_mode", "") or "").strip().lower()
    if mode in FILTER_MODES:
        return mode
    default = str(_cfg("LIVE_CHAT_FILTER_DEFAULT", "mask")).strip().lower()
    return default if default in FILTER_MODES else "mask"


def has_contact_info(text) -> bool:
    if not text or not isinstance(text, str):
        return False
    return bool(_URL_RE.search(text) or _PHONE_RE.search(text) or _EMAIL_RE.search(text) or _CONTACT_APP_RE.search(text))


def _profanity_spans(text):
    """-> (spans, mappable). `spans` are (start, end) of profane words in `text`. Matching is done on the leet-normalised
    copy (common.moderation.normalize); that copy has the same length as `text` for every normal input - when it does
    not (exotic Unicode case folding) the spans cannot be mapped back and `mappable` is False."""
    from common.moderation import normalize, profanity_words

    normalized = normalize(text)
    spans = []
    for word in profanity_words():
        for m in re.finditer(rf"\b{re.escape(word)}\b", normalized):
            spans.append(m.span())
    if not spans:
        return [], True
    spans.sort()
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(s) for s in merged], len(normalized) == len(text)


def mask_spans(text, spans) -> str:
    """"fuck" -> "f***", a 1-2 letter hit becomes all stars. Applied from the end so offsets stay valid."""
    out = text
    for start, end in reversed(spans):
        n = end - start
        replacement = "*" * n if n <= 2 else out[start] + "*" * (n - 1)
        out = out[:start] + replacement + out[end:]
    return out


def filter_chat_text(text, mode, *, strict_contact=False) -> FilterResult:
    """Decide what happens to ONE live-chat line. Never raises (any error -> allow).

    Order: (1) contact details when the age rule says so (applies even when the word filter is "off" - it protects
    children, the teacher cannot switch it off), (2) profanity -> flag / mask / block by `mode`, (3) the other spam
    signals of common.moderation (repeated characters, all caps ...) -> flag only.
    """
    if not text or not isinstance(text, str):
        return FilterResult(text or "", "allow")
    try:
        if strict_contact and has_contact_info(text):
            return FilterResult(text, "block", "contact_info")
        if mode == "off":
            return FilterResult(text, "allow")

        spans, mappable = _profanity_spans(text)
        if spans:
            if mode == "flag":
                return FilterResult(text, "flag", "profanity")
            if mode == "block" or not mappable:
                return FilterResult(text, "block", "profanity")
            return FilterResult(mask_spans(text, spans), "mask", "profanity")

        from common.moderation import screen_text

        flagged, reason = screen_text(text, surface="chat")
        if flagged:
            return FilterResult(text, "flag", reason or "spam")
        return FilterResult(text, "allow")
    except Exception:
        logger.exception("live chat filter failed - letting the message through")
        return FilterResult(text, "allow")


def text_fingerprint(text) -> str:
    """Short stable hash used by the duplicate-message guard (case / spacing insensitive)."""
    norm = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha1(norm.encode("utf-8", "ignore")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 2. AGE RULES
# ---------------------------------------------------------------------------
def age_group(user) -> str:
    """"minor" (DOB on file, under ADULT_AGE) | "adult" (DOB on file, 18+) | "unknown" (no DOB). Never raises."""
    try:
        if getattr(user, "is_minor", False):
            return "minor"
        if getattr(user, "is_verified_adult", False):
            return "adult"
    except Exception:
        logger.exception("age_group failed for user %s", getattr(user, "id", None))
    return "unknown"


def is_minor(user) -> bool:
    return age_group(user) == "minor"


def _minor_cutoff():
    from login.age import minor_cutoff_date

    return minor_cutoff_date()


def minors_present(session) -> bool:
    """True when a minor is in the room right now. Cached for LIVE_MINOR_CACHE_SECONDS: the chat path asks this for
    every line, the room's make-up changes much more slowly than that. DB trouble -> False (fail open, the sender's
    own age rule still applies)."""
    key = f"tc:live_minors:{session.id}"
    try:
        hit = cache.get(key)
        if hit is not None:
            return bool(hit)
    except Exception:
        hit = None
    try:
        present = session.participants.filter(left_at__isnull=True, user__date_of_birth__gt=_minor_cutoff()).exists()
    except Exception:
        logger.exception("minors_present failed for session %s", getattr(session, "id", None))
        return False
    try:
        cache.set(key, present, int(_cfg("LIVE_MINOR_CACHE_SECONDS", 60)))
    except Exception:
        pass
    return present


def note_participant_joined(session, user) -> None:
    """Join hook: a MINOR just entered, so the cached "is a minor in the room" answer is stale - drop it so the
    contact-info rule covers the room from the very next chat line instead of up to LIVE_MINOR_CACHE_SECONDS later."""
    try:
        if is_minor(user):
            cache.delete(f"tc:live_minors:{session.id}")
    except Exception:
        pass


def strict_contact_rule(session, sender, *, sender_is_staff: bool) -> bool:
    """RULE: nobody except the host tier may post phone numbers / links / e-mail / "add me on insta" lines in a live
    chat that a minor can read, and a minor may never post them. The teacher shares links to material all the time -
    staff are exempt. Off when there is no minor in the room and the sender is not one."""
    if sender_is_staff:
        return False
    return is_minor(sender) or minors_present(session)


def public_clip_blocked_by_minors(session) -> bool:
    """RULE: a recording that has had a minor in it (any time, any seat) may only be saved class-only - not to the
    public feed or to followers - unless settings.LIVE_PUBLIC_RECORDING_WITH_MINORS is on. Called by
    recording_save.create_save_job for visibility public / followers."""
    if bool(_cfg("LIVE_PUBLIC_RECORDING_WITH_MINORS", False)):
        return False
    try:
        return session.participants.filter(user__date_of_birth__gt=_minor_cutoff()).exists()
    except Exception:
        logger.exception("public_clip_blocked_by_minors failed for session %s", getattr(session, "id", None))
        return True  # cannot tell -> keep it private, the safe side


def stage_invite_error(session, participant):
    """RULE (stage invite of a minor). None = fine, else (code, English message).
        never        minors are never put on stage
        with_notice  a minor goes on stage only after they acknowledged the recording notice - when a recording runs
        allow        no extra rule
    """
    if not is_minor(participant.user):
        return None
    policy = str(_cfg("LIVE_MINOR_STAGE_POLICY", "with_notice")).strip().lower()
    if policy == "never":
        return "minor_stage_not_allowed", "Students under 18 cannot be invited on stage."
    if policy == "with_notice" and session.egress_id and participant.recording_notice_ack_at is None:
        return (
            "recording_notice_required",
            "This class is being recorded. Ask the student to acknowledge the recording notice before inviting them on stage.",
        )
    return None


# ---------------------------------------------------------------------------
# 3. RECORDING NOTICE
# ---------------------------------------------------------------------------
NOTICE_VERSION = "2026-10"

NOTICE_EN = {
    "title": "This class is being recorded",
    "body": (
        "The video, audio, screen share and chat of everyone in the class are recorded while the REC badge is on. "
        "The recording stays inside this classroom unless the teacher saves a clip to their profile. "
        "If you do not want to be recorded, leave the class or keep your camera and microphone off."
    ),
}
NOTICE_HI = {
    "title": "यह क्लास रिकॉर्ड हो रही है",
    "body": (
        "जब तक REC का निशान चालू है, क्लास में मौजूद सभी का वीडियो, आवाज़, स्क्रीन शेयर और चैट रिकॉर्ड होता है। "
        "रिकॉर्डिंग इसी क्लासरूम में रहती है, जब तक टीचर उसका कोई क्लिप अपनी प्रोफ़ाइल पर सेव न करें। "
        "अगर आप रिकॉर्ड नहीं होना चाहते, तो क्लास छोड़ दें या कैमरा और माइक बंद रखें।"
    ),
}


def recording_running(session) -> bool:
    return bool(getattr(session, "egress_id", ""))


def will_record(session) -> bool:
    """A recording is on, or will start by itself (classroom auto-record) - the notice must be shown either way."""
    classroom = session.classroom
    return recording_running(session) or bool(
        getattr(classroom, "recording_enabled", False) and getattr(classroom, "recording_auto", False)
    )


def notice_payload(session, user, participant=None) -> dict:
    """What the app needs to show the recording notice (join / token response, GET recording-notice/, and the
    `recording.started` broadcast). `ack_required` is True when this person cannot publish until they acknowledge
    (a minor, while the recording runs)."""
    running = recording_running(session)
    show = will_record(session)
    acked = bool(participant is not None and participant.recording_notice_ack_at)
    return {
        "version": NOTICE_VERSION,
        "show": show,
        "recording": running,
        "auto": bool(getattr(session.classroom, "recording_auto", False)),
        "acknowledged": acked,
        "ack_required": bool(running and is_minor(user) and not acked),
        "title_en": NOTICE_EN["title"], "body_en": NOTICE_EN["body"],
        "title_hi": NOTICE_HI["title"], "body_hi": NOTICE_HI["body"],
    }


def broadcast_notice_payload(session) -> dict:
    """Same text for the room-wide `recording.started` event (no per-person flags)."""
    return {
        "version": NOTICE_VERSION,
        "title_en": NOTICE_EN["title"], "body_en": NOTICE_EN["body"],
        "title_hi": NOTICE_HI["title"], "body_hi": NOTICE_HI["body"],
    }


def recording_blocks_publish(session, livekit_role, user) -> bool:
    """RULE: while a recording runs, a MINOR who has not acknowledged the notice gets a listen-only token (no mic /
    camera). The host tier is never blocked. Acknowledging lifts it (recording_consent.acknowledge also changes the
    live LiveKit permission, so no rejoin is needed)."""
    from .livekit_utils import ParticipantRole

    if not recording_running(session) or livekit_role in (ParticipantRole.HOST, ParticipantRole.CO_HOST):
        return False
    if not is_minor(user):
        return False
    try:
        # ANY row of this person in this session: a rejoin makes a new row, the acknowledgement must survive it.
        return not session.participants.filter(user=user, recording_notice_ack_at__isnull=False).exists()
    except Exception:
        logger.exception("recording_blocks_publish failed (session %s)", getattr(session, "id", None))
        return False
