"""
post/story_stickers.py

STORIES UPGRADE - PART 2 (Stickers: mention, link, poll, question).

Every overlay placed on top of a story - @mention, link, poll and question -
is ONE `StorySticker` row with the same placement shape:

    x, y        centre of the sticker as a fraction of the story canvas (0..1)
    rotation    degrees, -180..180
    scale       0.4..4.0 (1.0 = the client's default size)
    z_index     stacking order, lowest first

Kind-specific content is validated here, by a per-kind validator registered in
`_VALIDATORS`. What viewers send back to a poll / question (votes, answers) is
handled in post/story_sticker_responses.py.

Wire format for creating (multipart field `stickers`, a JSON-encoded list):

    [
      {"kind": "mention", "x": 0.5, "y": 0.3, "rotation": -5, "scale": 1.0, "user_id": 12},
      {"kind": "link",    "x": 0.5, "y": 0.8, "url": "learnscroll.com/course/9", "label": "Join the course"},
      {"kind": "poll",    "x": 0.5, "y": 0.6, "question": "Ready for the test?", "options": ["Yes", "No"]},
      {"kind": "question","x": 0.5, "y": 0.2, "prompt": "Ask me anything about Physics"}
    ]

All of the rules live in this file so the create serializer and the tests
cannot drift apart.
"""
import ipaddress
import json
import math
import re
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.contrib.auth import get_user_model

from .models import CloseFriend, Story, StorySticker

User = get_user_model()

# --------------------------------------------------------------------------
# Limits (overridable from settings.py, e.g. STORY_MAX_MENTIONS = 10)
# --------------------------------------------------------------------------
DEFAULT_MAX_STICKERS = 10   # all kinds together
DEFAULT_MAX_MENTIONS = 5    # every mention notifies a person - keep it spam-proof
DEFAULT_MAX_LINKS = 1       # Instagram allows one link sticker per story
DEFAULT_MAX_POLLS = 1       # one poll per story keeps the results screen unambiguous
DEFAULT_MAX_QUESTIONS = 1   # same for the question box
DEFAULT_POLL_MAX_OPTIONS = 4
POLL_MIN_OPTIONS = 2

MAX_RAW_PAYLOAD_CHARS = 8000
MAX_URL_LENGTH = 2048
MAX_LABEL_LENGTH = 40
MAX_POLL_QUESTION_LENGTH = 100
MAX_POLL_OPTION_LENGTH = 25
MAX_QUESTION_PROMPT_LENGTH = 100

ROTATION_MIN, ROTATION_MAX = -180.0, 180.0
SCALE_MIN, SCALE_MAX = 0.4, 4.0

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s")
_HAS_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def max_stickers():
    return int(getattr(settings, "STORY_MAX_STICKERS", DEFAULT_MAX_STICKERS))


def max_mentions():
    return int(getattr(settings, "STORY_MAX_MENTIONS", DEFAULT_MAX_MENTIONS))


def max_links():
    return int(getattr(settings, "STORY_MAX_LINKS", DEFAULT_MAX_LINKS))


def max_polls():
    return int(getattr(settings, "STORY_MAX_POLLS", DEFAULT_MAX_POLLS))


def max_questions():
    return int(getattr(settings, "STORY_MAX_QUESTIONS", DEFAULT_MAX_QUESTIONS))


def poll_max_options():
    return max(POLL_MIN_OPTIONS, int(getattr(settings, "STORY_POLL_MAX_OPTIONS", DEFAULT_POLL_MAX_OPTIONS)))


class StickerError(ValueError):
    """A sticker in the payload is invalid. `index` is the 0-based position in
    the submitted list (None for list-level problems) so the client can point
    at the offending sticker."""

    def __init__(self, message, index=None, code="invalid"):
        super().__init__(message)
        self.message = message
        self.index = index
        self.code = code

    def as_text(self):
        if self.index is None:
            return self.message
        return f"Sticker {self.index + 1}: {self.message}"


class CleanSticker:
    """A validated sticker, ready to be written. Not a model instance so that
    nothing is saved until the whole list has passed."""

    __slots__ = ("kind", "x", "y", "rotation", "scale", "z_index", "data", "mentioned_user")

    def __init__(self, kind, x, y, rotation, scale, z_index, data=None, mentioned_user=None):
        self.kind = kind
        self.x = x
        self.y = y
        self.rotation = rotation
        self.scale = scale
        self.z_index = z_index
        self.data = data or {}
        self.mentioned_user = mentioned_user


# --------------------------------------------------------------------------
# Parsing the multipart field
# --------------------------------------------------------------------------
def parse_stickers_payload(raw):
    """`raw` is the value of the `stickers` field: a JSON string (multipart) or
    an already-decoded list. Returns a list of dicts or raises StickerError."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise StickerError("stickers must be UTF-8 JSON.", code="bad_json")
    if isinstance(raw, str):
        if len(raw) > MAX_RAW_PAYLOAD_CHARS:
            raise StickerError("stickers payload is too large.", code="too_large")
        try:
            raw = json.loads(raw)
        except ValueError:
            raise StickerError("stickers must be a JSON-encoded list.", code="bad_json")
    if not isinstance(raw, list):
        raise StickerError("stickers must be a list.", code="bad_json")
    if len(raw) > max_stickers():
        raise StickerError(
            f"A story can have at most {max_stickers()} stickers.", code="too_many",
        )
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise StickerError("each sticker must be an object.", index=i, code="bad_json")
    return raw


# --------------------------------------------------------------------------
# Shared placement validation
# --------------------------------------------------------------------------
def _number(raw, key, default, lo, hi, index):
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StickerError(f"'{key}' must be a number.", index=index, code="bad_placement")
    value = float(value)
    if not math.isfinite(value) or value < lo or value > hi:
        raise StickerError(f"'{key}' must be between {lo:g} and {hi:g}.", index=index, code="bad_placement")
    return value


def _placement(raw, index, default_z):
    z = raw.get("z_index", default_z)
    if isinstance(z, bool) or not isinstance(z, int) or not (0 <= z <= 100):
        raise StickerError("'z_index' must be a whole number between 0 and 100.", index=index, code="bad_placement")
    return {
        "x": _number(raw, "x", 0.5, 0.0, 1.0, index),
        "y": _number(raw, "y", 0.5, 0.0, 1.0, index),
        "rotation": _number(raw, "rotation", 0.0, ROTATION_MIN, ROTATION_MAX, index),
        "scale": _number(raw, "scale", 1.0, SCALE_MIN, SCALE_MAX, index),
        "z_index": z,
    }


# --------------------------------------------------------------------------
# Link sticker
# --------------------------------------------------------------------------
def normalize_story_link(url):
    """Return a safe, normalised http(s) URL or raise StickerError.

    * only http/https (no javascript:, data:, intent:, file:, mailto: ...)
    * a bare "example.com/x" gets https:// prepended
    * no credentials in the URL ("https://google.com@evil.com" is a classic
      phishing trick) and no IP-literal hosts / single-label hosts (localhost)
    * host is lower-cased; the rest of the URL is left as the user typed it
    """
    if not isinstance(url, str):
        raise StickerError("'url' is required.", code="bad_link")
    url = url.strip()
    if not url:
        raise StickerError("'url' is required.", code="bad_link")
    if len(url) > MAX_URL_LENGTH:
        raise StickerError(f"'url' is too long (max {MAX_URL_LENGTH} characters).", code="bad_link")
    if _CONTROL_CHARS.search(url) or _WHITESPACE.search(url):
        raise StickerError("'url' must not contain spaces or control characters.", code="bad_link")

    if "://" not in url:
        if url.startswith("//"):
            url = "https:" + url
        elif _HAS_SCHEME.match(url):
            # javascript:..., mailto:..., data:..., and "localhost:8000/x"
            raise StickerError("Only http and https links are allowed.", code="bad_link")
        else:
            url = "https://" + url

    try:
        parts = urlsplit(url)
        port = parts.port  # raises ValueError for an out-of-range / non-numeric port
    except ValueError:
        raise StickerError("'url' is not a valid link.", code="bad_link")

    if parts.scheme.lower() not in ("http", "https"):
        raise StickerError("Only http and https links are allowed.", code="bad_link")
    if "@" in parts.netloc:
        raise StickerError("Links with a username or password are not allowed.", code="bad_link")

    host = (parts.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        raise StickerError("'url' must have a valid website address.", code="bad_link")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # not an IP literal - good
    else:
        raise StickerError("Links to IP addresses are not allowed.", code="bad_link")
    if not _valid_hostname(host):
        raise StickerError("'url' must have a valid website address.", code="bad_link")

    for blocked in getattr(settings, "STORY_LINK_BLOCKED_DOMAINS", ()):
        blocked = str(blocked).lower().lstrip(".")
        if blocked and (host == blocked or host.endswith("." + blocked)):
            raise StickerError("This website can't be linked from a story.", code="link_blocked")

    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, parts.fragment))


def _valid_hostname(host):
    """RFC-1035-ish check. Unicode letters are accepted (internationalised
    domains); the last label may not be all digits ("1.2.3" is not a domain)."""
    if len(host) > 253:
        return False
    labels = host.split(".")
    for label in labels:
        if not label or len(label) > 63 or label.startswith("-") or label.endswith("-"):
            return False
        if not all(ch.isalnum() or ch == "-" for ch in label):
            return False
    return not labels[-1].isdigit()


def _clean_link(raw, index, owner, story_audience):
    url = normalize_story_link(raw.get("url"))
    label = raw.get("label", "")
    if label is None:
        label = ""
    if not isinstance(label, str):
        raise StickerError("'label' must be text.", index=index, code="bad_link")
    label = _CONTROL_CHARS.sub("", label).strip()
    if len(label) > MAX_LABEL_LENGTH:
        raise StickerError(f"'label' can be at most {MAX_LABEL_LENGTH} characters.", index=index, code="bad_link")
    host = urlsplit(url).hostname or ""
    return {"data": {"url": url, "label": label, "host": host}, "mentioned_user": None}


# --------------------------------------------------------------------------
# Mention sticker
# --------------------------------------------------------------------------
def _clean_mention(raw, index, owner, story_audience):
    from .close_friends_views import blocked_user_ids  # local: avoids a module-load cycle

    user_id = raw.get("user_id")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise StickerError("'user_id' is required for a mention.", index=index, code="bad_mention")
    if user_id == owner.id:
        raise StickerError("You can't mention yourself.", index=index, code="bad_mention")

    target = User.objects.filter(id=user_id, is_active=True).first()
    if target is None or target.id in blocked_user_ids(owner):
        # Same answer for "doesn't exist" and "blocked" so a block isn't revealed.
        raise StickerError("That person can't be mentioned.", index=index, code="bad_mention")

    if story_audience == Story.AUDIENCE_CLOSE_FRIENDS and not CloseFriend.objects.filter(
        owner=owner, friend=target
    ).exists():
        raise StickerError(
            "Only people on your Close Friends list can be mentioned in a Close Friends story.",
            index=index, code="mention_not_close_friend",
        )
    return {"data": {}, "mentioned_user": target}


# --------------------------------------------------------------------------
# Poll sticker + Question sticker
# --------------------------------------------------------------------------
def _clean_short_text(value, key, index, max_len, code):
    """Required single-line text: control characters removed, ends trimmed."""
    if not isinstance(value, str):
        raise StickerError(f"'{key}' is required.", index=index, code=code)
    value = _CONTROL_CHARS.sub("", value).strip()
    if not value:
        raise StickerError(f"'{key}' is required.", index=index, code=code)
    if len(value) > max_len:
        raise StickerError(f"'{key}' can be at most {max_len} characters.", index=index, code=code)
    return value


def _clean_poll(raw, index, owner, story_audience):
    question = _clean_short_text(raw.get("question"), "question", index, MAX_POLL_QUESTION_LENGTH, "bad_poll")
    options = raw.get("options")
    if not isinstance(options, list):
        raise StickerError("'options' must be a list.", index=index, code="bad_poll")
    if not (POLL_MIN_OPTIONS <= len(options) <= poll_max_options()):
        raise StickerError(
            f"A poll needs between {POLL_MIN_OPTIONS} and {poll_max_options()} options.",
            index=index, code="bad_poll",
        )
    cleaned, seen = [], set()
    for opt in options:
        text = _clean_short_text(opt, "option", index, MAX_POLL_OPTION_LENGTH, "bad_poll")
        if text.casefold() in seen:
            raise StickerError("Poll options must be different from each other.", index=index, code="bad_poll")
        seen.add(text.casefold())
        cleaned.append(text)
    return {"data": {"question": question, "options": cleaned}, "mentioned_user": None}


def _clean_question(raw, index, owner, story_audience):
    prompt = _clean_short_text(raw.get("prompt"), "prompt", index, MAX_QUESTION_PROMPT_LENGTH, "bad_question")
    return {"data": {"prompt": prompt}, "mentioned_user": None}


# --------------------------------------------------------------------------
# Registry. Each validator returns {"data": {...}, "mentioned_user": User | None}.
# --------------------------------------------------------------------------
_VALIDATORS = {
    StorySticker.KIND_MENTION: _clean_mention,
    StorySticker.KIND_LINK: _clean_link,
    StorySticker.KIND_POLL: _clean_poll,
    StorySticker.KIND_QUESTION: _clean_question,
}


def supported_kinds():
    return tuple(_VALIDATORS)


def clean_stickers(raw_list, owner, story_audience=Story.AUDIENCE_EVERYONE):
    """Validate a parsed list (see parse_stickers_payload). Returns a list of
    CleanSticker or raises StickerError. Nothing is written to the database."""
    cleaned = []
    seen_mentions = set()
    counts = {}

    for i, raw in enumerate(raw_list):
        kind = raw.get("kind")
        validator = _VALIDATORS.get(kind)
        if validator is None:
            known = ", ".join(supported_kinds())
            raise StickerError(f"Unsupported sticker kind. Use one of: {known}.", index=i, code="bad_kind")

        counts[kind] = counts.get(kind, 0) + 1
        if kind == StorySticker.KIND_MENTION and counts[kind] > max_mentions():
            raise StickerError(f"You can mention at most {max_mentions()} people in a story.", index=i, code="too_many_mentions")
        if kind == StorySticker.KIND_LINK and counts[kind] > max_links():
            raise StickerError(f"A story can have only {max_links()} link sticker.", index=i, code="too_many_links")

        if kind == StorySticker.KIND_POLL and counts[kind] > max_polls():
            raise StickerError(f"A story can have only {max_polls()} poll.", index=i, code="too_many_polls")
        if kind == StorySticker.KIND_QUESTION and counts[kind] > max_questions():
            raise StickerError(f"A story can have only {max_questions()} question box.", index=i, code="too_many_questions")

        placement = _placement(raw, i, default_z=i)
        result = validator(raw, i, owner, story_audience)

        mentioned = result["mentioned_user"]
        if mentioned is not None:
            if mentioned.id in seen_mentions:
                raise StickerError("The same person can only be mentioned once.", index=i, code="duplicate_mention")
            seen_mentions.add(mentioned.id)

        cleaned.append(CleanSticker(kind=kind, data=result["data"], mentioned_user=mentioned, **placement))
    return cleaned


def create_stickers(story, cleaned):
    """Write validated stickers for `story`. Call inside the same transaction
    that created the story. Returns the created StorySticker rows."""
    rows = [
        StorySticker(
            story=story, kind=c.kind, x=c.x, y=c.y, rotation=c.rotation, scale=c.scale,
            z_index=c.z_index, data=c.data, mentioned_user=c.mentioned_user,
        )
        for c in cleaned
    ]
    return StorySticker.objects.bulk_create(rows)
