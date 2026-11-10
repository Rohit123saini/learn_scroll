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

F3 kinds (same placement, kind-specific fields):

      {"kind": "location",  "name": "Delhi Public Library", "lat": 28.61, "lng": 77.2}     (lat/lng optional, both or none)
      {"kind": "countdown", "title": "Board exam", "ends_at": "2026-12-01T09:00:00+05:30"}
      {"kind": "quiz",      "question": "2+2?", "options": ["3", "4"], "correct_index": 1, "explanation": "Basic sum"}
      {"kind": "add_yours", "prompt": "Show your desk", "chain_from": "<sticker uuid>"}   (chain_from optional)
      {"kind": "gif",       "id": "xT9", "url": "https://media0.giphy.com/.../giphy.gif", "title": "wow", "width": 480, "height": 270}

All of the rules live in this file so the create serializer and the tests
cannot drift apart.
"""
import ipaddress
import json
import math
import re
import uuid
from datetime import timedelta, timezone as dt_timezone
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import minor_privacy, story_gifs, story_post_card
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
DEFAULT_MAX_LOCATIONS = 1   # F3
DEFAULT_MAX_COUNTDOWNS = 1
DEFAULT_MAX_QUIZZES = 1
DEFAULT_MAX_ADD_YOURS = 1
DEFAULT_MAX_GIFS = 3

MAX_RAW_PAYLOAD_CHARS = 8000
MAX_URL_LENGTH = 2048
MAX_LABEL_LENGTH = 40
MAX_POLL_QUESTION_LENGTH = 100
MAX_POLL_OPTION_LENGTH = 25
MAX_QUESTION_PROMPT_LENGTH = 100
MAX_LOCATION_NAME_LENGTH = 60
MAX_COUNTDOWN_TITLE_LENGTH = 40
MAX_COUNTDOWN_DAYS = 365          # a countdown further away than a year is almost surely a mistake
MAX_QUIZ_EXPLANATION_LENGTH = 120
MAX_ADD_YOURS_PROMPT_LENGTH = 60
COORD_DECIMALS = 2                # ~1 km: enough to say "this city / neighbourhood", never a doorstep

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


def _max_setting(name, default):
    return int(getattr(settings, name, default))


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

    __slots__ = ("kind", "x", "y", "rotation", "scale", "z_index", "data", "mentioned_user", "chain_root")

    def __init__(self, kind, x, y, rotation, scale, z_index, data=None, mentioned_user=None, chain_root=None):
        self.kind = kind
        self.x = x
        self.y = y
        self.rotation = rotation
        self.scale = scale
        self.z_index = z_index
        self.data = data or {}
        self.mentioned_user = mentioned_user
        self.chain_root = chain_root   # F3 add_yours: the StorySticker that started the chain (or None)


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
    from user_profile import privacy as privacy_rules  # S4: "who can mention me" gets the same answer

    if not privacy_rules.can_mention(target, owner):
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
# F3 stickers: location, countdown, quiz, add_yours, gif
# --------------------------------------------------------------------------
def _clean_coord(value, key, lo, hi, index):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StickerError(f"'{key}' must be a number.", index=index, code="bad_location")
    value = float(value)
    if not math.isfinite(value) or not (lo <= value <= hi):
        raise StickerError(f"'{key}' must be between {lo:g} and {hi:g}.", index=index, code="bad_location")
    return round(value, COORD_DECIMALS)


def _clean_location(raw, index, owner, story_audience):
    """A place name (+ optional rounded coordinates). Minors never get here: `minor_privacy.blocked_sticker_kinds`."""
    name = _clean_short_text(raw.get("name"), "name", index, MAX_LOCATION_NAME_LENGTH, "bad_location")
    data = {"name": name}
    lat, lng = raw.get("lat"), raw.get("lng")
    if (lat is None) != (lng is None):
        raise StickerError("'lat' and 'lng' must be sent together.", index=index, code="bad_location")
    if lat is not None:
        data["lat"] = _clean_coord(lat, "lat", -90.0, 90.0, index)
        data["lng"] = _clean_coord(lng, "lng", -180.0, 180.0, index)
    return {"data": data, "mentioned_user": None}


def _parse_future_datetime(value, index):
    if not isinstance(value, str) or not value.strip():
        raise StickerError("'ends_at' is required.", index=index, code="bad_countdown")
    try:
        parsed = parse_datetime(value.strip())
    except ValueError:
        parsed = None
    if parsed is None:
        raise StickerError("'ends_at' must be an ISO date-time.", index=index, code="bad_countdown")
    if timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=dt_timezone.utc)   # a client that forgot the offset: treat as UTC
    return parsed.astimezone(dt_timezone.utc)


def _clean_countdown(raw, index, owner, story_audience):
    title = _clean_short_text(raw.get("title"), "title", index, MAX_COUNTDOWN_TITLE_LENGTH, "bad_countdown")
    ends_at = _parse_future_datetime(raw.get("ends_at"), index)
    now = timezone.now()
    if ends_at <= now:
        raise StickerError("'ends_at' must be in the future.", index=index, code="bad_countdown")
    if ends_at > now + timedelta(days=MAX_COUNTDOWN_DAYS):
        raise StickerError(
            f"A countdown can be at most {MAX_COUNTDOWN_DAYS} days away.", index=index, code="bad_countdown",
        )
    return {
        "data": {"title": title, "ends_at": ends_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")},
        "mentioned_user": None,
    }


def _clean_quiz(raw, index, owner, story_audience):
    question = _clean_short_text(raw.get("question"), "question", index, MAX_POLL_QUESTION_LENGTH, "bad_quiz")
    options = raw.get("options")
    if not isinstance(options, list):
        raise StickerError("'options' must be a list.", index=index, code="bad_quiz")
    if not (POLL_MIN_OPTIONS <= len(options) <= poll_max_options()):
        raise StickerError(
            f"A quiz needs between {POLL_MIN_OPTIONS} and {poll_max_options()} options.", index=index, code="bad_quiz",
        )
    cleaned, seen = [], set()
    for opt in options:
        text = _clean_short_text(opt, "option", index, MAX_POLL_OPTION_LENGTH, "bad_quiz")
        if text.casefold() in seen:
            raise StickerError("Quiz options must be different from each other.", index=index, code="bad_quiz")
        seen.add(text.casefold())
        cleaned.append(text)

    correct = raw.get("correct_index")
    if isinstance(correct, str) and correct.strip().isdigit():
        correct = int(correct.strip())
    if isinstance(correct, bool) or not isinstance(correct, int):
        raise StickerError("'correct_index' is required.", index=index, code="bad_quiz")
    if not (0 <= correct < len(cleaned)):
        raise StickerError("'correct_index' must point at one of the options.", index=index, code="bad_quiz")

    data = {"question": question, "options": cleaned, "correct_index": correct}
    explanation = raw.get("explanation")
    if explanation not in (None, ""):
        data["explanation"] = _clean_short_text(
            explanation, "explanation", index, MAX_QUIZ_EXPLANATION_LENGTH, "bad_quiz",
        )
    return {"data": data, "mentioned_user": None}


def _resolve_chain_root(raw_id, index, owner):
    """The add_yours sticker this one answers, as the chain's ROOT. Same gate as responding to a sticker: the story
    must exist, be live, be visible to `owner`, and not be written by somebody with a block against them."""
    from .close_friends_views import blocked_user_ids  # local: avoids a module-load cycle
    from .story_visibility import can_view_story

    try:
        sticker_id = uuid.UUID(str(raw_id))
    except (ValueError, AttributeError, TypeError):
        raise StickerError("'chain_from' is not a valid sticker id.", index=index, code="bad_chain")
    sticker = (
        StorySticker.objects.select_related("story", "chain_root__story")
        .filter(id=sticker_id, kind=StorySticker.KIND_ADD_YOURS).first()
    )
    # One answer for "no such sticker", "expired", "not visible" and "blocked" so nothing is revealed.
    gone = StickerError("That Add Yours chain is not available.", index=index, code="bad_chain")
    if sticker is None:
        raise gone
    story = sticker.story
    if story.is_deleted or story.is_expired or not can_view_story(story, owner) \
            or story.user_id in blocked_user_ids(owner):
        raise gone
    return sticker.chain_root if sticker.chain_root is not None else sticker


def _clean_add_yours(raw, index, owner, story_audience):
    chain_from = raw.get("chain_from")
    if chain_from not in (None, ""):
        root = _resolve_chain_root(chain_from, index, owner)
        # The chain's prompt is the root's - a participant can't change what the chain asks.
        prompt = str((root.data or {}).get("prompt", ""))
        return {"data": {"prompt": prompt}, "mentioned_user": None, "chain_root": root}
    prompt = _clean_short_text(raw.get("prompt"), "prompt", index, MAX_ADD_YOURS_PROMPT_LENGTH, "bad_add_yours")
    return {"data": {"prompt": prompt}, "mentioned_user": None, "chain_root": None}


def _clean_gif(raw, index, owner, story_audience):
    try:
        url = story_gifs.clean_gif_url(raw.get("url"))
        preview = raw.get("preview_url")
        data = {
            "id": story_gifs.clean_gif_id(raw.get("id")),
            "title": story_gifs.clean_gif_text(
                raw.get("title"), field="title", max_length=story_gifs.MAX_TITLE_LENGTH),
            "url": url,
            "preview_url": story_gifs.clean_gif_url(preview, field="preview_url") if preview not in (None, "") else url,
            "width": story_gifs.clean_gif_dimension(raw.get("width"), field="width"),
            "height": story_gifs.clean_gif_dimension(raw.get("height"), field="height"),
        }
    except story_gifs.GifError as exc:
        raise StickerError(str(exc), index=index, code="bad_gif")
    return {"data": data, "mentioned_user": None}


# --------------------------------------------------------------------------
# Post card (F3-AUD): a post / reel shared into the story. Only the id (+ kind) is stored.
# --------------------------------------------------------------------------
def _clean_post_card(raw, index, owner, story_audience):
    from .models import Post

    pid = story_post_card.parse_post_id(raw.get("post_id"))
    if pid is None:
        raise StickerError("'post_id' is required for a post card.", index=index, code="bad_post_card")
    post = Post.objects.filter(id=pid).first()
    ok, why = story_post_card.can_share_post_to_story(post, owner)
    if not ok:
        raise StickerError(why, index=index, code="bad_post_card")
    kind = raw.get("post_kind")
    if kind not in story_post_card.VALID_KINDS:
        kind = story_post_card.guess_post_kind(post)
    return {"data": {"post_id": str(post.id), "post_kind": kind}, "mentioned_user": None}


# --------------------------------------------------------------------------
# Registry. Each validator returns {"data": {...}, "mentioned_user": User | None} (add_yours also "chain_root").
# --------------------------------------------------------------------------
_VALIDATORS = {
    StorySticker.KIND_MENTION: _clean_mention,
    StorySticker.KIND_LINK: _clean_link,
    StorySticker.KIND_POLL: _clean_poll,
    StorySticker.KIND_QUESTION: _clean_question,
    StorySticker.KIND_LOCATION: _clean_location,
    StorySticker.KIND_COUNTDOWN: _clean_countdown,
    StorySticker.KIND_QUIZ: _clean_quiz,
    StorySticker.KIND_ADD_YOURS: _clean_add_yours,
    StorySticker.KIND_GIF: _clean_gif,
    StorySticker.KIND_POST_CARD: _clean_post_card,
}

# Per-kind cap inside one story (setting name, default). mention / link / poll / question keep their own getters above.
_NEW_KIND_LIMITS = {
    StorySticker.KIND_LOCATION: ("STORY_MAX_LOCATIONS", DEFAULT_MAX_LOCATIONS, "one location sticker"),
    StorySticker.KIND_COUNTDOWN: ("STORY_MAX_COUNTDOWNS", DEFAULT_MAX_COUNTDOWNS, "one countdown"),
    StorySticker.KIND_QUIZ: ("STORY_MAX_QUIZZES", DEFAULT_MAX_QUIZZES, "one quiz"),
    StorySticker.KIND_ADD_YOURS: ("STORY_MAX_ADD_YOURS", DEFAULT_MAX_ADD_YOURS, "one Add Yours sticker"),
    StorySticker.KIND_GIF: ("STORY_MAX_GIFS", DEFAULT_MAX_GIFS, "{n} GIFs"),
    StorySticker.KIND_POST_CARD: ("STORY_MAX_POST_CARDS", 1, "one post card"),
}


def supported_kinds():
    return tuple(_VALIDATORS)


def _check_mention_can_see(target, owner, audience, classroom_id, hidden_ids, index):
    """F3-AUD: never tag someone who could not open the story (they would get a notification for a 404)."""
    if target.id in set(hidden_ids or ()):
        raise StickerError("You hid this story from that person, so you can't mention them.",
                           index=index, code="mention_hidden")
    if audience == Story.AUDIENCE_FOLLOWERS:
        from user_profile.models import Follow
        if not Follow.objects.filter(follower=target, following=owner, status=Follow.Status.ACCEPTED).exists():
            raise StickerError("Only your followers can be mentioned in a Followers story.",
                               index=index, code="mention_not_follower")
    elif audience == Story.AUDIENCE_CLASS:
        from .audience import classroom_ids_of
        if classroom_id not in classroom_ids_of(target):
            raise StickerError("Only members of that class can be mentioned in a Class story.",
                               index=index, code="mention_not_in_class")


def clean_stickers(raw_list, owner, story_audience=Story.AUDIENCE_EVERYONE, classroom_id=None, hidden_ids=()):
    """Validate a parsed list (see parse_stickers_payload). Returns a list of
    CleanSticker or raises StickerError. Nothing is written to the database."""
    cleaned = []
    seen_mentions = set()
    counts = {}
    blocked_kinds = minor_privacy.blocked_sticker_kinds(owner)  # H23: question box (DM-like) is off for minors

    for i, raw in enumerate(raw_list):
        kind = raw.get("kind")
        if kind in blocked_kinds:
            raise StickerError("This sticker isn't available on your account.", index=i, code="minor_sticker_blocked")
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

        if kind in _NEW_KIND_LIMITS:
            name, default, label = _NEW_KIND_LIMITS[kind]
            cap = _max_setting(name, default)
            if counts[kind] > cap:
                raise StickerError(
                    f"A story can have only {label.format(n=cap)}.", index=i, code=f"too_many_{kind}",
                )

        placement = _placement(raw, i, default_z=i)
        result = validator(raw, i, owner, story_audience)

        mentioned = result["mentioned_user"]
        if mentioned is not None:
            _check_mention_can_see(mentioned, owner, story_audience, classroom_id, hidden_ids, i)
            if mentioned.id in seen_mentions:
                raise StickerError("The same person can only be mentioned once.", index=i, code="duplicate_mention")
            seen_mentions.add(mentioned.id)

        cleaned.append(CleanSticker(
            kind=kind, data=result["data"], mentioned_user=mentioned,
            chain_root=result.get("chain_root"), **placement,
        ))
    return cleaned


def create_stickers(story, cleaned):
    """Write validated stickers for `story`. Call inside the same transaction
    that created the story. Returns the created StorySticker rows."""
    rows = [
        StorySticker(
            story=story, kind=c.kind, x=c.x, y=c.y, rotation=c.rotation, scale=c.scale,
            z_index=c.z_index, data=c.data, mentioned_user=c.mentioned_user, chain_root=c.chain_root,
        )
        for c in cleaned
    ]
    return StorySticker.objects.bulk_create(rows)
