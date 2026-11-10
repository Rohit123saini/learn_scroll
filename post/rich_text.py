"""
post/rich_text.py - caption / description text of a post: sanitising + limits.

The caption is stored as PLAIN TEXT with a small markdown-like markup that the app renders itself
(lib/post/richtext/): **bold**, *italic*, `code`, ```code blocks```, "- " / "1. " lists, $math$ / $$math$$,
\\ce{H2O} chemistry, #hashtags, @mentions, links. Nothing here is ever rendered as HTML by the app, but the same
text reaches the web SEO pages, e-mails, push bodies, the admin and the share cards, so it is cleaned once, on the way in:

  * HTML is removed: <script>/<style>/<iframe>/... blocks with their content, every other real tag (a lone "<" or
    ">" - maths like "a < b" - stays untouched), HTML comments, and `javascript:` / `data:` / `vbscript:` schemes.
  * Control characters, NUL, bidi-override / isolate characters (text-direction spoofing), zero-width space and
    BOM are dropped. ZWJ / ZWNJ stay: Hindi and other Indic conjuncts need them.
  * Unicode is normalised to NFC (Devanagari matras compose the same way everywhere).
  * Blank lines are capped (3 in a row), trailing spaces are trimmed.
  * Length is counted in Unicode CHARACTERS (a Hindi caption is not "longer" because of its bytes) and an over-long
    text is REFUSED with a clear error - never silently cut in the middle of a word or a formula.

Pure functions, no DB: easy to reuse from serializers, views and tasks.
"""
import re
import unicodedata

from django.conf import settings
from rest_framework import serializers

DEFAULT_CONTENT_MAX = 5000
DEFAULT_TITLE_MAX = 300
DEFAULT_COMMENT_MAX = 2000
MAX_MENTIONS = 30
MAX_LINKS = 20
MAX_MATH_SEGMENT = 500   # one $...$ / \ce{...} piece; a longer one is a pasted document, not a formula

_BLOCK_TAGS = re.compile(
    r"<\s*(script|style|iframe|object|embed|svg|math|template|noscript|form|link|meta)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_OPEN_ONLY_TAGS = re.compile(r"<\s*/?\s*(script|style|iframe|object|embed|link|meta|base)\b[^>]*>?", re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ANY_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9:-]*(?:\s[^<>]*)?/?>|<![A-Za-z][^>]*>|<\?[^>]*>")
_BAD_SCHEME = re.compile(r"(?i)\b(?:java\s*script|vb\s*script|data)\s*:(?=\s*[\w/%<(])")
_CONTROL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]")
# bidi embeddings/overrides/isolates, zero-width space, word joiner, BOM, soft hyphen, line/para separators
_INVISIBLE = re.compile("[\u202A-\u202E\u2066-\u2069\u200B\u2060\uFEFF\u00AD\u2028\u2029]")
_BLANK_RUN = re.compile(r"\n{4,}")
_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_.]{1,30})")
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$([^\$\n]+?)\$|\\ce\{([^{}\n]*)\}", re.DOTALL)


def content_max():
    return int(getattr(settings, "POST_CONTENT_MAX_LENGTH", DEFAULT_CONTENT_MAX))


def title_max():
    return int(getattr(settings, "POST_TITLE_MAX_LENGTH", DEFAULT_TITLE_MAX))


def comment_max():
    return int(getattr(settings, "POST_COMMENT_MAX_LENGTH", DEFAULT_COMMENT_MAX))


def sanitize_rich_text(text):
    """Cleaned copy of [text] (see the module doc). None / non-str -> ''. Idempotent."""
    if text is None:
        return ""
    s = str(text)
    s = unicodedata.normalize("NFC", s.replace("\r\n", "\n").replace("\r", "\n"))
    s = _CONTROL.sub("", s.replace("\t", "    "))
    s = _INVISIBLE.sub("", s)
    # Run the tag strippers until nothing changes: "<scr<script>ipt>" must not turn into a tag after one pass.
    for _ in range(4):
        before = s
        s = _COMMENT.sub("", s)
        s = _BLOCK_TAGS.sub("", s)
        s = _OPEN_ONLY_TAGS.sub("", s)
        s = _ANY_TAG.sub("", s)
        if s == before:
            break
    s = _BAD_SCHEME.sub("", s)
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    s = _BLANK_RUN.sub("\n\n\n", s)
    return s.strip()


def extract_mentions(text, limit=MAX_MENTIONS):
    """Usernames mentioned in [text] ('@asha' -> 'asha'), unique, order kept, case-folded."""
    out = []
    for m in _MENTION_RE.finditer(text or ""):
        name = m.group(1).rstrip(".").lower()
        if name and name not in out:
            out.append(name)
        if len(out) >= limit:
            break
    return out


def extract_urls(text, limit=MAX_LINKS):
    out = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;:!?)]}'\"")
        if url and url not in out:
            out.append(url)
        if len(out) >= limit:
            break
    return out


def first_url(text):
    urls = extract_urls(text, limit=1)
    return urls[0] if urls else None


def clean_content(value, *, field="content", max_length=None):
    """Serializer helper: sanitise + enforce the length. Raises serializers.ValidationError."""
    cleaned = sanitize_rich_text(value)
    limit = max_length or content_max()
    if len(cleaned) > limit:
        raise serializers.ValidationError(
            f"Is text ki limit {limit} characters hai (abhi {len(cleaned)}). Isse chhota karein."
        )
    for m in _MATH_RE.finditer(cleaned):
        if len(m.group(0)) > MAX_MATH_SEGMENT:
            raise serializers.ValidationError(
                f"Ek formula {MAX_MATH_SEGMENT} characters se chhota hona chahiye."
            )
    if len(extract_urls(cleaned, limit=MAX_LINKS + 1)) > MAX_LINKS:
        raise serializers.ValidationError(f"Ek post me {MAX_LINKS} se zyada links nahi ho sakte.")
    if len(extract_mentions(cleaned, limit=MAX_MENTIONS + 1)) > MAX_MENTIONS:
        raise serializers.ValidationError(f"Ek post me {MAX_MENTIONS} se zyada @mentions nahi ho sakte.")
    return cleaned


def clean_title(value):
    cleaned = " ".join(sanitize_rich_text(value).split())  # a title is one line
    limit = title_max()
    if len(cleaned) > limit:
        raise serializers.ValidationError(f"Title {limit} characters se chhota hona chahiye.")
    return cleaned


def clean_comment(value):
    """A comment (or reply) body: same sanitising / limits as a caption, with the shorter comment length.
    Raises serializers.ValidationError (callers that are not serializers catch it and answer 400)."""
    return clean_content(value, max_length=comment_max())


def validation_message(exc):
    """First human message out of a serializers.ValidationError (str, list or dict detail)."""
    detail = getattr(exc, "detail", exc)
    while isinstance(detail, dict) and detail:
        detail = next(iter(detail.values()))
    while isinstance(detail, (list, tuple)) and detail:
        detail = detail[0]
    return str(detail)
