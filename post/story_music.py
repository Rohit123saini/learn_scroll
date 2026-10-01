"""
post/story_music.py

STORIES UPGRADE - PART 3a (Music on a story).

A story can carry ONE background track. The client picks it from the existing
CC0-only Freesound proxy (`GET /post/music/search/`, whose rows are
`{id, name, artist, duration, preview_url, license, tags}`) and sends the choice
back with the story, as a JSON-encoded multipart field `music`:

    {"id": 123456, "title": "Calm piano", "artist": "some_user",
     "preview_url": "https://cdn.freesound.org/previews/123/123456_1-hq.mp3",
     "duration": 42.5, "start": 3.0, "license": "CC0"}

`url` is accepted as an alias of `preview_url`, `name` as an alias of `title`.

What is stored on `Story.music` is the CLEANED shape:

    {"id": "123456", "title": ..., "artist": ..., "url": ..., "duration": 42.5 | null,
     "start": 3.0, "license": "CC0"}

Why validate at all when the client got the row from our own proxy? Because the
create endpoint is public API: without these rules anyone could store an
arbitrary URL that every viewer's phone would then fetch (tracking pixel,
huge file, `file://`, an internal address...). So:

* https only, no credentials, no non-default port;
* host must be Freesound (setting `STORY_MUSIC_ALLOWED_HOSTS`, subdomains OK);
* path must be an `.mp3` / `.ogg` file; query string and fragment are DROPPED
  (a Freesound preview needs none, and this way a token can never be stored);
* only CC0 is accepted - it needs no attribution, which is the only kind of
  track the app can safely show without a credit line;
* text is stripped of control characters and length-capped;
* `start` must lie inside the track when the duration is known.

All of the rules live here so the serializer and the tests cannot drift apart.
"""
import json
import math
import re
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings

# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------
DEFAULT_ALLOWED_HOSTS = ("freesound.org",)   # exact host or any subdomain (cdn.freesound.org)
MAX_RAW_PAYLOAD_CHARS = 4000
MAX_TITLE_LENGTH = 100
MAX_ARTIST_LENGTH = 60
MAX_URL_LENGTH = 500
MAX_TRACK_SECONDS = 600.0                     # nobody attaches a 10+ minute track to a story
ALLOWED_EXTENSIONS = (".mp3", ".ogg")
LICENSE_CC0 = "CC0"

_ID_RE = re.compile(r"^\d{1,20}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class MusicError(ValueError):
    """A music payload that breaks a rule. `str(exc)` is the client-facing text."""

    def as_text(self):
        return str(self)


def allowed_hosts():
    hosts = getattr(settings, "STORY_MUSIC_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS)
    return tuple(h.strip().lower() for h in hosts if h and h.strip())


# --------------------------------------------------------------------------
# Small field cleaners
# --------------------------------------------------------------------------
def _clean_text(value, *, field, max_length, required):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise MusicError(f"Music {field} must be text.")
    value = _CONTROL_CHARS.sub("", value).strip()
    if required and not value:
        raise MusicError(f"Music {field} is required.")
    if len(value) > max_length:
        raise MusicError(f"Music {field} can be at most {max_length} characters.")
    return value


def _clean_number(value, *, field):
    """A finite, non-boolean number (int / float / numeric string)."""
    if isinstance(value, bool):
        raise MusicError(f"Music {field} must be a number.")
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise MusicError(f"Music {field} must be a number.")
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MusicError(f"Music {field} must be a number.")
    return float(value)


def _clean_id(value):
    if isinstance(value, bool) or value is None:
        raise MusicError("Music id is required.")
    text = str(value).strip()
    if not _ID_RE.match(text):
        raise MusicError("Music id is invalid.")
    return text


def clean_music_url(value):
    """Validate + normalise a track URL (see module docstring). Returns the
    URL without query string / fragment."""
    if not isinstance(value, str) or not value.strip():
        raise MusicError("Music url is required.")
    value = value.strip()
    if len(value) > MAX_URL_LENGTH:
        raise MusicError("Music url is too long.")
    if _CONTROL_CHARS.search(value) or re.search(r"\s", value):
        raise MusicError("Music url is invalid.")
    try:
        parts = urlsplit(value)
        port = parts.port  # raises ValueError on a malformed port
    except ValueError:
        raise MusicError("Music url is invalid.")
    if parts.scheme.lower() != "https":
        raise MusicError("Music url must be an https link.")
    if parts.username is not None or parts.password is not None:
        raise MusicError("Music url must not contain credentials.")
    if port not in (None, 443):
        raise MusicError("Music url must use the default https port.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise MusicError("Music url is invalid.")
    if not any(host == h or host.endswith("." + h) for h in allowed_hosts()):
        raise MusicError("Music must come from the music search.")
    path = parts.path or ""
    if not path.lower().endswith(ALLOWED_EXTENSIONS):
        raise MusicError("Music url must point to an mp3 or ogg file.")
    return urlunsplit(("https", host, path, "", ""))


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def parse_music_payload(raw):
    """Multipart sends a JSON string; JSON bodies may send the object itself.
    `None` / '' / 'null' / {} all mean "no music"."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        text = raw.strip()
        if text in ("", "null"):
            return None
        if len(text) > MAX_RAW_PAYLOAD_CHARS:
            raise MusicError("Music payload is too large.")
        try:
            raw = json.loads(text)
        except ValueError:
            raise MusicError("Music must be valid JSON.")
    if raw is None or raw == {}:
        return None
    if not isinstance(raw, dict):
        raise MusicError("Music must be an object.")
    return raw


def clean_music(payload):
    """Validate a parsed payload and return the shape stored on `Story.music`
    (or None for "no music")."""
    if not payload:
        return None

    title = payload.get("title", payload.get("name"))
    url = payload.get("url", payload.get("preview_url"))

    cleaned = {
        "id": _clean_id(payload.get("id")),
        "title": _clean_text(title, field="title", max_length=MAX_TITLE_LENGTH, required=True),
        "artist": _clean_text(payload.get("artist"), field="artist", max_length=MAX_ARTIST_LENGTH, required=False),
        "url": clean_music_url(url),
    }

    lic = payload.get("license")
    if lic not in (None, "") and (not isinstance(lic, str) or lic.strip().upper() != LICENSE_CC0):
        raise MusicError("Only CC0 (copyright-free) music can be used.")
    cleaned["license"] = LICENSE_CC0

    duration = payload.get("duration")
    if duration is None or duration == "":
        cleaned["duration"] = None
    else:
        duration = _clean_number(duration, field="duration")
        if not 0 < duration <= MAX_TRACK_SECONDS:
            raise MusicError(f"Music duration must be between 0 and {int(MAX_TRACK_SECONDS)} seconds.")
        cleaned["duration"] = round(duration, 2)

    start = payload.get("start")
    if start is None or start == "":
        start = 0.0
    else:
        start = _clean_number(start, field="start")
    if not 0 <= start <= MAX_TRACK_SECONDS:
        raise MusicError("Music start is out of range.")
    if cleaned["duration"] is not None and start >= cleaned["duration"]:
        raise MusicError("Music start must be inside the track.")
    cleaned["start"] = round(start, 2)

    return cleaned
