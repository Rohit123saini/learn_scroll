"""
post/story_gifs.py

STORIES UPGRADE - F3 (GIF sticker). Two jobs, both pure Python so the rules and the tests cannot drift apart:

1. `clean_gif_url` - what a GIF sticker may point at. The create endpoint is public API, so without these rules anyone
   could store an arbitrary URL that every viewer's phone would then download (tracking pixel, huge file, `file://`,
   an internal address). Same approach as post/story_music.py:

   * https only, no credentials, no non-default port;
   * host must belong to the GIF provider (setting `STORY_GIF_ALLOWED_HOSTS`, default Giphy; subdomains OK);
   * path must be a `.gif` or `.webp` file; query string and fragment are DROPPED (a Giphy media file needs none, and
     this way a tracking token can never be stored).

2. `map_giphy_results` - turns a Giphy search / trending response into the rows the app shows
   (`{id, title, url, preview_url, width, height}`), already filtered to hosts + extensions we accept, so every row the
   picker offers is a row the create endpoint will accept.

The search proxy itself is `GET /post/gifs/search/` (views.gif_search); `GIPHY_API_KEY` stays on the server and results
are always requested with `rating=g` (a story reaches minors).
"""
import re
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings

DEFAULT_ALLOWED_HOSTS = ("giphy.com",)   # exact host or any subdomain (media0.giphy.com, i.giphy.com ...)
ALLOWED_EXTENSIONS = (".gif", ".webp")
MAX_URL_LENGTH = 500
MAX_TITLE_LENGTH = 60
MAX_ID_LENGTH = 40
MAX_DIMENSION = 4000

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,%d}$" % MAX_ID_LENGTH)


class GifError(ValueError):
    """A GIF payload that breaks a rule. `str(exc)` is the client-facing text."""


def allowed_hosts():
    hosts = getattr(settings, "STORY_GIF_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS)
    return tuple(h.strip().lower() for h in hosts if h and h.strip())


def host_is_allowed(host):
    host = (host or "").lower().rstrip(".")
    return bool(host) and any(host == h or host.endswith("." + h) for h in allowed_hosts())


def clean_gif_url(value, *, field="url"):
    """Validate + normalise a GIF file URL. Returns it without query string / fragment."""
    if not isinstance(value, str) or not value.strip():
        raise GifError(f"GIF {field} is required.")
    value = value.strip()
    if len(value) > MAX_URL_LENGTH:
        raise GifError(f"GIF {field} is too long.")
    if _CONTROL_CHARS.search(value) or re.search(r"\s", value):
        raise GifError(f"GIF {field} is invalid.")
    try:
        parts = urlsplit(value)
        port = parts.port  # raises ValueError on a malformed port
    except ValueError:
        raise GifError(f"GIF {field} is invalid.")
    if parts.scheme.lower() != "https":
        raise GifError(f"GIF {field} must be an https link.")
    if parts.username is not None or parts.password is not None:
        raise GifError(f"GIF {field} must not contain credentials.")
    if port not in (None, 443):
        raise GifError(f"GIF {field} must use the default https port.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host_is_allowed(host):
        raise GifError("GIFs must come from the GIF search.")
    path = parts.path or ""
    if not path.lower().endswith(ALLOWED_EXTENSIONS):
        raise GifError(f"GIF {field} must point to a gif or webp file.")
    return urlunsplit(("https", host, path, "", ""))


def clean_gif_text(value, *, field, max_length):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GifError(f"GIF {field} must be text.")
    value = _CONTROL_CHARS.sub("", value).strip()
    if len(value) > max_length:
        raise GifError(f"GIF {field} can be at most {max_length} characters.")
    return value


def clean_gif_id(value):
    if value is None or value == "":
        return ""
    text = str(value).strip() if not isinstance(value, bool) else ""
    if not _ID_RE.match(text):
        raise GifError("GIF id is invalid.")
    return text


def clean_gif_dimension(value, *, field):
    """A whole number 1..MAX_DIMENSION, or None when the client did not send one."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise GifError(f"GIF {field} must be a whole number.")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or not (1 <= value <= MAX_DIMENSION):
        raise GifError(f"GIF {field} must be between 1 and {MAX_DIMENSION}.")
    return value


def _int_or_none(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= MAX_DIMENSION else None


def map_giphy_results(payload):
    """Giphy `data` rows -> the rows the app shows. Rows without a usable (allowed host, .gif/.webp) file are skipped."""
    rows = []
    items = payload.get("data") if isinstance(payload, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        images = item.get("images") if isinstance(item.get("images"), dict) else {}
        main = images.get("fixed_width") or images.get("downsized") or images.get("original") or {}
        small = images.get("fixed_width_small") or images.get("fixed_width_downsampled") or main
        try:
            url = clean_gif_url((main or {}).get("url"))
            preview = clean_gif_url((small or {}).get("url"))
            gif_id = clean_gif_id(item.get("id"))
        except GifError:
            continue
        rows.append({
            "id": gif_id,
            "title": clean_gif_text(item.get("title") if isinstance(item.get("title"), str) else "", field="title",
                                    max_length=10_000)[:MAX_TITLE_LENGTH],
            "url": url,
            "preview_url": preview,
            "width": _int_or_none(main.get("width")),
            "height": _int_or_none(main.get("height")),
        })
    return rows
