"""
post/publish_gate.py - ONE pre-publish check for everything a user can put in front of other people:
a post (incl. reels / polls / doubts), a story, a class resource (notes), a doubt ANSWER (surface "answer"). Chunked uploads end up in
the same create views, so they go through this too (post_chunked_upload_complete calls it directly).

    verdict = publish_gate.evaluate(user, "post", texts=[...], files=[...], media_types=[...])
    if not verdict.ok:   return verdict.response()          # 429 / 409 / 422, never a 500
    ... create the object (moderation_status="flagged" when verdict.hold) ...
    publish_gate.after_create(verdict, user, "post", post)  # flags, fingerprints, rate counter, owner notice

What it checks, in this order (cheap first - big files are hashed last):

 1. SPAM RATE LIMIT   sliding window per user + surface (PUBLISH_RATE_LIMITS, default post 5 / 10 min and
                      40 / day). Accounts younger than PUBLISH_NEW_ACCOUNT_HOURS get PUBLISH_NEW_ACCOUNT_FACTOR
                      of the limit. Staff are exempt. -> 429 `publish_rate_limited` + Retry-After.
 2. ABUSIVE / NSFW TEXT  common.moderation.screen_text (profanity) + screen_nsfw over title, caption, media
                      captions, poll options (story: caption + sticker texts). PUBLISH_TEXT_ACTION:
                        "hold"  (default) the post IS created but hidden (moderation_status=flagged) and shown in the
                                moderator queue; the author gets a notice with an Appeal button. A STORY has no hold
                                state, so a story is refused instead (422 `text_not_allowed`).
                        "block" refuse everything (422).     "flag" publish, only queue it.
                      Spam-looking text (repeated characters / words, a pile of links) is only flagged (low).
 3. DUPLICATE CONTENT  same caption text or same file bytes (sha256) published again by the SAME user on the
                      same surface inside PUBLISH_DUP_WINDOW_HOURS (24) -> 409 `duplicate_content` (PUBLISH_DUP_ACTION
                      = block | flag | off). The same text / file by ANOTHER user is only flagged (copy-paste spam /
                      re-upload), never blocked. Deleted / removed originals do not count.
 4. AUDIO (MUSIC) COPYRIGHT FLAG   an uploaded audio file (or a video / audio post whose caption advertises a song)
                      that LOOKS like a commercial track - "Artist - Title.mp3", "official audio", "remix", full ID3
                      tags (artist + title + album / ISRC / label) - is published but flagged (medium) for a copyright
                      reviewer. An optional recogniser (settings.PUBLISH_AUDIO_ID_PROVIDER = "pkg.module.func",
                      func(name, file=None, path=None) -> {"match": True, "title":..., "artist":...} | None) that
                      reports a match HOLDS the post (high). Without a provider nothing is held on a guess.
                      Matches against music we already removed are still done by copyrights/ (fingerprints) - this adds
                      the "first upload of a new song" signal that fingerprints cannot give.
 5. MINOR PRIVACY     a minor's `location` is dropped (post/minor_privacy.py) - `verdict.location_allowed`.

Everything here FAILS OPEN: a bug / cache outage / unreadable file never stops a legitimate upload and
never turns into a 500 - the worst case is that one check is skipped (logged).
"""
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from common.moderation import screen_nsfw, screen_text, severity_for

from . import minor_privacy

logger = logging.getLogger(__name__)

SURFACES = ("post", "story", "resource", "answer")
# AutoModerationFlag.target_type for each surface (a resource IS a Post row). A doubt ANSWER has no flag target type
# (and no hold state), so its text problems are refused instead and nothing is queued.
_FLAG_TARGET = {"post": "post", "resource": "post", "story": "story"}
NO_HOLD_SURFACES = ("story", "answer")      # no moderation_status column: abusive / NSFW text is refused, never held

CODE_RATE = "publish_rate_limited"
CODE_DUPLICATE = "duplicate_content"
CODE_TEXT = "text_not_allowed"
CODE_MEDIA = "media_not_allowed"

REASON_PROFANITY = "profanity"
REASON_NSFW = "nsfw"
REASON_NSFW_MEDIA = "nsfw_media"
REASON_DUPLICATE_TEXT = "duplicate_content"
REASON_DUPLICATE_MEDIA = "duplicate_media"
REASON_AUDIO = "audio_copyright"
REASON_AUDIO_MATCH = "audio_copyright_match"

DEFAULT_RATE_LIMITS = {
    # surface: ((max_publishes, window_seconds), ...) - every window must have room
    "post": ((5, 600), (40, 86400)),
    "story": ((10, 600), (60, 86400)),
    "resource": ((5, 600), (30, 86400)),
    "answer": ((10, 600), (80, 86400)),
}
RATE_LOG_MAX = 300           # timestamps kept per (user, surface)
MAX_LINKS_IN_POST = 3        # more links than this in one caption = spam flag
HASH_CHUNK = 1024 * 1024

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr")
_URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
# Words that say "this is somebody's released song" in a file name or a caption.
_MUSIC_WORDS_RE = re.compile(
    r"(official\s+(?:audio|music|video|lyrics?)|lyrics?\s+video|full\s+song|\bremix\b|\bfeat\.?\s|\bft\.\s"
    r"|\bprod\.?\s+by\b|\bnightcore\b|\bslowed\s*(?:\+|and|&)?\s*reverb\b|mp3\s+download|\bsoundtrack\b|\bost\b|©)",
    re.IGNORECASE,
)
# "Artist - Title.mp3" (also en/em dash). Underscore-only names ("voice_note_12.m4a") do not match.
_ARTIST_TITLE_FILE_RE = re.compile(r"^[^\-\u2013\u2014/\\]{2,40}\s[-\u2013\u2014]\s[^\-\u2013\u2014/\\]{2,60}$")


def _cfg(name, default):
    return getattr(settings, name, default)


# ==========================================================================
# result object
# ==========================================================================
@dataclass
class Flag:
    reason: str
    severity: str = "medium"
    note: str = ""
    hold: bool = False     # True = this flag alone keeps the post hidden until staff approve it


@dataclass
class Item:
    """One uploaded file, whether it is an in-memory upload or an assembled chunked file on disk."""
    name: str = ""
    content_type: str = ""
    declared_type: str = ""        # what the composer said it is: image | video | audio | document | gif
    file: object = None
    path: str = ""

    @property
    def is_audio(self):
        ext = os.path.splitext(self.name or self.path or "")[1].lower()
        return (self.declared_type == "audio" or (self.content_type or "").lower().startswith("audio/")
                or ext in AUDIO_EXTENSIONS)

    @property
    def is_video(self):
        ext = os.path.splitext(self.name or self.path or "")[1].lower()
        return self.declared_type == "video" or (self.content_type or "").lower().startswith("video/") \
            or ext in (".mp4", ".mov", ".mkv", ".webm", ".m4v")


@dataclass
class Verdict:
    surface: str = "post"
    ok: bool = True
    status: int = 200
    code: str = ""
    message: str = ""
    retry_after: Optional[int] = None
    extra: dict = field(default_factory=dict)       # e.g. {"existing_id": "..."} for a duplicate
    flags: list = field(default_factory=list)
    hold: bool = False                              # create it, but hidden until staff approve
    location_allowed: bool = True
    text: str = ""                                  # the joined texts that were screened (snippet for flags)
    text_hash: str = ""
    media_hashes: list = field(default_factory=list)
    rate_checked: bool = False

    # ---- helpers for the views
    @property
    def moderation_status(self):
        return "flagged" if self.hold else "approved"

    @property
    def hold_reasons(self):
        return [f.reason for f in self.flags if f.hold]

    def response(self):
        from rest_framework.response import Response

        body = {"success": False, "code": self.code, "message": self.message}
        body.update(self.extra)
        resp = Response(body, status=self.status)
        if self.retry_after:
            resp["Retry-After"] = str(int(self.retry_after))
        return resp

    def held_payload(self):
        """Extra keys for a 201 whose post was held, so the app can say 'sent for review'."""
        if not self.hold:
            return {}
        return {
            "held": True,
            "moderation_status": "flagged",
            "hold_reasons": self.hold_reasons,
            "hold_message": "Your post was sent for review. It will go live as soon as a moderator approves it.",
        }


# ==========================================================================
# 1. rate limit
# ==========================================================================
def rate_limits_for(surface):
    configured = _cfg("PUBLISH_RATE_LIMITS", None) or {}
    return tuple(configured.get(surface) or DEFAULT_RATE_LIMITS.get(surface, ()))


def _is_new_account(user):
    hours = float(_cfg("PUBLISH_NEW_ACCOUNT_HOURS", 24))
    joined = getattr(user, "date_joined", None)
    return bool(hours and joined and joined > timezone.now() - timedelta(hours=hours))


def _rate_key(surface, user):
    return f"post:pubgate:rate:{surface}:{user.pk}"


def _limit_for(user, limit):
    if _is_new_account(user):
        return max(1, int(limit * float(_cfg("PUBLISH_NEW_ACCOUNT_FACTOR", 0.5))))
    return limit


def check_rate(user, surface):
    """-> (allowed, retry_after_seconds). Sliding log in the cache; fails open."""
    if not _cfg("PUBLISH_RATE_LIMIT_ENABLED", True) or getattr(user, "is_staff", False):
        return True, None
    try:
        now = time.time()
        log = [t for t in (cache.get(_rate_key(surface, user)) or []) if now - t < 86400 * 2]
        worst = 0
        for limit, window in rate_limits_for(surface):
            limit = _limit_for(user, limit)
            inside = sorted(t for t in log if now - t < window)
            if len(inside) >= limit:
                # the oldest publish that still counts must leave the window first
                worst = max(worst, int(inside[len(inside) - limit] + window - now) + 1)
        return (worst == 0), (worst or None)
    except Exception:
        logger.exception("publish gate: rate check failed - letting it through")
        return True, None


def record_publish(user, surface):
    if not _cfg("PUBLISH_RATE_LIMIT_ENABLED", True) or getattr(user, "is_staff", False):
        return
    try:
        now = time.time()
        key = _rate_key(surface, user)
        log = [t for t in (cache.get(key) or []) if now - t < 86400 * 2][-(RATE_LOG_MAX - 1):]
        log.append(now)
        cache.set(key, log, 86400 * 2)
    except Exception:
        logger.exception("publish gate: could not record the publish for rate limiting")


# ==========================================================================
# 2. text (abusive / nsfw / spam-looking)
# ==========================================================================
def _json_value(value):
    if isinstance(value, str):
        v = value.strip()
        if v[:1] in "[{":
            try:
                return json.loads(v)
            except ValueError:
                return None
    return value


def _as_list(value):
    value = _json_value(value)
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _strings(values):
    out = []
    for v in values:
        if isinstance(v, str):
            if v.strip():
                out.append(v.strip())
        elif isinstance(v, dict):
            for key in ("text", "label", "question", "prompt", "title", "caption"):
                if isinstance(v.get(key), str) and v[key].strip():
                    out.append(v[key].strip())
            out.extend(_strings(v.get("options") or []))
    return out


def texts_from_request(surface, data):
    """Every piece of text the user typed for this publish, from request.data (multipart or JSON)."""
    get = data.get if hasattr(data, "get") else (lambda k, d=None: d)
    texts = []
    if surface == "story":
        texts.append(get("caption") or "")
        texts.extend(_strings(_as_list(get("stickers"))))
    elif surface == "resource":
        for key in ("title", "description", "content", "subject"):
            texts.append(get(key) or "")
    elif surface == "answer":
        texts.append(get("content") or "")
        texts.extend(_strings(_as_list(get("steps"))))
    else:
        texts.append(get("title") or "")
        texts.append(get("content") or "")
        texts.extend(_strings(_as_list(get("media_captions"))))
        texts.extend(_strings(_as_list(get("media_alt_texts"))))
        texts.extend(_strings(_as_list(get("poll_options"))))
        _ps = get("poll_settings")
        if isinstance(_ps, str):
            try:
                _ps = __import__("json").loads(_ps)
            except ValueError:
                _ps = None
        if isinstance(_ps, dict):
            texts.append(_ps.get("explanation") or "")
    return [t for t in (str(x).strip() for x in texts if x) if t]


def _text_action(surface):
    action = str(_cfg("PUBLISH_TEXT_ACTION", "hold")).lower()
    if action not in ("hold", "block", "flag"):
        action = "hold"
    if action == "hold" and surface in NO_HOLD_SURFACES:
        action = "block"        # a story / an answer has no hold state (no moderation_status column)
    return action


def check_text(texts, surface, action=None):
    """-> list[Flag]. Never raises."""
    joined = "\n".join(t for t in texts if t)
    if not joined:
        return []
    action = action or _text_action(surface)
    hold = action in ("hold", "block")
    flags = []
    try:
        bad, reason = screen_text(joined, "post")
        if bad and reason == REASON_PROFANITY:
            flags.append(Flag(REASON_PROFANITY, severity_for(reason), "abusive language", hold=hold))
        elif bad:                                   # repeated characters / words
            flags.append(Flag(reason, severity_for(reason), "spam-looking text"))
        nsfw, reason = screen_nsfw(joined)
        if nsfw:
            flags.append(Flag(REASON_NSFW, severity_for(reason), "adult content", hold=hold))
        if len(_URL_RE.findall(joined)) > int(_cfg("PUBLISH_MAX_LINKS", MAX_LINKS_IN_POST)):
            flags.append(Flag("spam_link", "low", "many links"))
    except Exception:
        logger.exception("publish gate: text check failed - skipping it")
    return flags


# ==========================================================================
# 3. duplicate content
# ==========================================================================
def normalize_text(text):
    """Case, spacing and punctuation do not make a text 'new'. Letters, marks (Hindi matras) and digits stay."""
    t = unicodedata.normalize("NFKC", text or "").casefold()
    t = "".join(ch if unicodedata.category(ch)[0] in "LMN" else " " for ch in t)
    return " ".join(t.split())


def text_digest(texts, min_chars=None):
    min_chars = int(_cfg("PUBLISH_DUP_MIN_CHARS", 15)) if min_chars is None else min_chars
    norm = normalize_text("\n".join(texts))
    if len(norm) < min_chars:
        return ""
    return hashlib.sha1(norm.encode("utf-8", "ignore")).hexdigest()


def _size_of(item):
    try:
        if item.path:
            return os.path.getsize(item.path)
        return int(getattr(item.file, "size", 0) or 0)
    except Exception:
        return 0


def _sha256(item):
    h = hashlib.sha256()
    if item.path:
        with open(item.path, "rb") as fh:
            for chunk in iter(lambda: fh.read(HASH_CHUNK), b""):
                h.update(chunk)
        return h.hexdigest()
    f = item.file
    try:
        f.seek(0)
        for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
            h.update(chunk)
    finally:
        try:
            f.seek(0)
        except Exception:
            pass
    return h.hexdigest()


def media_digests(items):
    cap = int(float(_cfg("PUBLISH_DUP_MEDIA_MAX_MB", 150)) * 1024 * 1024)
    out = []
    for it in items:
        try:
            if _size_of(it) and _size_of(it) > cap:
                continue
            d = _sha256(it)
            if d and d not in out:
                out.append(d)
        except Exception:
            logger.exception("publish gate: could not hash %s", getattr(it, "name", ""))
    return out


def _alive(surface, target_id):
    """A removed / deleted original is not a duplicate of anything any more."""
    try:
        if surface == "story":
            from .models import Story

            return Story.objects.filter(pk=target_id, is_deleted=False).exists()
        if surface == "answer":
            from .models import PostAnswer

            return PostAnswer.objects.filter(pk=target_id).exists()
        from .models import Post

        return Post.objects.filter(pk=target_id, is_deleted=False).exclude(
            moderation_status__in=("rejected", "copyright_removed", "legal_removed")).exists()
    except Exception:
        return False


def check_duplicates(user, surface, text_hash, media_hashes):
    """-> (own_hit: tuple|None, other_flags: list[Flag]). own_hit = (hash_kind, target_id)."""
    action = str(_cfg("PUBLISH_DUP_ACTION", "block")).lower()
    if action == "off" or not (text_hash or media_hashes):
        return None, []
    try:
        from .models import PublishFingerprint

        since = timezone.now() - timedelta(hours=float(_cfg("PUBLISH_DUP_WINDOW_HOURS", 24)))
        cond = Q()
        if text_hash:
            cond |= Q(hash_kind="text", digest=text_hash)
        if media_hashes:
            cond |= Q(hash_kind="media", digest__in=media_hashes)
        rows = list(PublishFingerprint.objects.filter(cond, created_at__gte=since)
                    .order_by("-created_at")[:50])
        own, other = None, []
        seen_other = set()
        for row in rows:
            if row.user_id == user.pk:
                if own is None and row.surface == surface and _alive(row.surface, row.target_id):
                    own = (row.hash_kind, row.target_id)
                continue
            if row.hash_kind in seen_other or not _alive(row.surface, row.target_id):
                continue
            if row.hash_kind == "text":
                # a short sentence can honestly be typed by two people; copy-paste spam is long
                seen_other.add("text")
                other.append(Flag(REASON_DUPLICATE_TEXT, "low", "same text as another user's recent post"))
            else:
                seen_other.add("media")
                other.append(Flag(REASON_DUPLICATE_MEDIA, "low", "same file as another user's recent upload"))
        return own, other
    except Exception:
        logger.exception("publish gate: duplicate check failed - skipping it")
        return None, []


# ==========================================================================
# 4. audio (music) copyright flag
# ==========================================================================
def _id3_tags(item):
    try:
        import mutagen  # optional dependency
    except ImportError:
        return {}
    try:
        if item.file is not None:
            item.file.seek(0)
        mf = mutagen.File(item.path or item.file, easy=True)
        tags = {}
        for key in ("artist", "title", "album", "isrc", "copyright", "organization", "albumartist"):
            val = (mf.tags or {}).get(key) if mf is not None else None
            if isinstance(val, (list, tuple)):
                val = val[0] if val else ""
            if val:
                tags[key] = str(val)
        return tags
    except Exception:
        return {}
    finally:
        try:
            if item.file is not None:
                item.file.seek(0)
        except Exception:
            pass


def _looks_commercial(item):
    """-> reason text or ''. Pure heuristics on the file name and its tags - no audio decoding."""
    stem = os.path.splitext(os.path.basename(item.name or item.path or ""))[0]
    if _ARTIST_TITLE_FILE_RE.match(stem.replace("_", " ").strip()):
        return f"file name looks like 'Artist - Title' ({stem[:60]})"
    if _MUSIC_WORDS_RE.search(stem.replace("_", " ")):
        return f"file name says it is a released track ({stem[:60]})"
    tags = _id3_tags(item)
    if tags.get("artist") and tags.get("title") and (
            tags.get("album") or tags.get("isrc") or tags.get("copyright") or tags.get("organization")):
        return "audio tags name an artist, title and album / label"
    return ""


def _provider():
    path = _cfg("PUBLISH_AUDIO_ID_PROVIDER", "")
    if not path:
        return None
    try:
        from django.utils.module_loading import import_string

        return import_string(path)
    except Exception:
        logger.exception("publish gate: PUBLISH_AUDIO_ID_PROVIDER %r could not be imported", path)
        return None


def check_audio(items, texts):
    """-> list[Flag]. Audio uploads, plus video/audio whose caption advertises a song."""
    flags = []
    try:
        audio = [it for it in items if it.is_audio]
        provider = _provider() if audio else None
        for it in audio:
            if provider is not None:
                try:
                    hit = provider(it.name, file=it.file, path=it.path)
                except Exception:
                    logger.exception("publish gate: audio recogniser failed - ignoring it for %s", it.name)
                    hit = None
                if hit and hit.get("match"):
                    who = " - ".join(x for x in (hit.get("artist"), hit.get("title")) if x)
                    flags.append(Flag(REASON_AUDIO_MATCH, severity_for(REASON_AUDIO_MATCH),
                                      f"recognised as {who or 'a released track'}", hold=True))
                    continue
            why = _looks_commercial(it)
            if why:
                flags.append(Flag(REASON_AUDIO, severity_for(REASON_AUDIO), why))
        if not flags and any(it.is_audio or it.is_video for it in items):
            caption = "\n".join(texts)
            if _MUSIC_WORDS_RE.search(caption):
                flags.append(Flag(REASON_AUDIO, severity_for(REASON_AUDIO), "caption advertises a song / remix"))
    except Exception:
        logger.exception("publish gate: audio check failed - skipping it")
    # one audio flag per publish is enough for a reviewer
    seen, out = set(), []
    for f in flags:
        if f.reason not in seen:
            seen.add(f.reason)
            out.append(f)
    return out


# ==========================================================================
# 5. adult images / videos (optional provider, common/media_moderation.py)
# ==========================================================================
def check_media(items):
    """-> list[Flag]. Needs settings.MEDIA_NSFW_PROVIDER; without one nothing is guessed. A hit with a score below
    MEDIA_NSFW_MIN_SCORE (or MEDIA_NSFW_ACTION = 'flag') is only flagged; otherwise the post is held."""
    try:
        from common import media_moderation as mm

        hits = mm.scan(items)
        if not hits:
            return []
        hold = mm.action() == "hold"
        top = max(hits, key=lambda h: h.score)
        return [Flag(REASON_NSFW_MEDIA, severity_for(REASON_NSFW_MEDIA),
                     f"{top.kind} looks like adult content ({top.label or 'nsfw'}, score {top.score:.2f})",
                     hold=hold and top.score >= mm.min_score())]
    except Exception:
        logger.exception("publish gate: media check failed - skipping it")
        return []


# ==========================================================================
# the gate
# ==========================================================================
def build_items(files=(), paths=(), media_types=()):
    items = []
    types = [str(t or "").lower() for t in _as_list(media_types)]
    for i, f in enumerate(files or ()):
        items.append(Item(name=getattr(f, "name", "") or "", content_type=getattr(f, "content_type", "") or "",
                          declared_type=types[i] if i < len(types) else "", file=f))
    for j, p in enumerate(paths or ()):
        idx = len(items)
        items.append(Item(name=os.path.basename(p), path=p, declared_type=types[idx] if idx < len(types) else ""))
    return items


def evaluate(user, surface, *, texts=(), files=(), paths=(), media_types=(), check_only=False):
    """Run every check. `check_only=True` = the preflight endpoint: text + rate only (no hashing of files)."""
    v = Verdict(surface=surface)
    texts = [t for t in (texts or ()) if t]
    v.text = "\n".join(texts)
    v.location_allowed = not minor_privacy.location_blocked(user)

    # 1. rate limit
    allowed, retry = check_rate(user, surface)
    v.rate_checked = True
    if not allowed:
        v.ok, v.status, v.code, v.retry_after = False, 429, CODE_RATE, retry
        v.message = ("You are posting too fast. Please wait a little before posting again."
                     if retry is None or retry > 90 else
                     f"You are posting too fast. Please try again in {int(retry)} seconds.")
        v.extra = {"retry_after": retry}
        return v

    # 2. text
    action = _text_action(surface)
    v.flags.extend(check_text(texts, surface, action))
    if action == "block" and any(f.hold for f in v.flags):
        v.ok, v.status, v.code = False, 422, CODE_TEXT
        v.message = ("This can't be posted because it has abusive or adult content. "
                     "Please edit the text and try again.")
        v.extra = {"reasons": [f.reason for f in v.flags if f.hold]}
        return v

    if check_only:
        v.hold = any(f.hold for f in v.flags)
        return v

    items = build_items(files, paths, media_types)

    # 3. duplicates
    v.text_hash = text_digest(texts)
    v.media_hashes = media_digests(items)
    own, others = check_duplicates(user, surface, v.text_hash, v.media_hashes)
    if own is not None:
        if str(_cfg("PUBLISH_DUP_ACTION", "block")).lower() == "block":
            v.ok, v.status, v.code = False, 409, CODE_DUPLICATE
            v.message = "You already posted this. Please post something new."
            v.extra = {"duplicate_of": str(own[1]), "duplicate_kind": own[0]}
            return v
        v.flags.append(Flag(REASON_DUPLICATE_TEXT if own[0] == "text" else REASON_DUPLICATE_MEDIA, "low",
                            "you posted the same thing recently"))
    v.flags.extend(others)

    # 4. audio / music
    v.flags.extend(check_audio(items, texts))

    # 5. adult images / videos (only when MEDIA_NSFW_PROVIDER is set)
    media_flags = check_media(items)
    v.flags.extend(media_flags)
    if surface in NO_HOLD_SURFACES and any(f.hold for f in media_flags):
        # a story / an answer has no hold state, so it is refused instead (same rule as abusive text)
        v.ok, v.status, v.code = False, 422, CODE_MEDIA
        v.message = "This can't be posted because the picture or video looks like adult content."
        v.extra = {"reasons": [REASON_NSFW_MEDIA]}
        return v

    v.hold = any(f.hold for f in v.flags)
    return v


# ==========================================================================
# after the object exists
# ==========================================================================
def _record_flags(v, user, surface, target_id):
    if surface not in _FLAG_TARGET:
        return                      # e.g. a doubt answer: nothing to queue it under
    from user_profile.automod import record_flag

    target_type = _FLAG_TARGET.get(surface, "post")
    for f in v.flags:
        record_flag(
            target_type=target_type, target_id=target_id, user_id=user.pk,
            text=f"[gate:{f.reason}] {f.note} | {v.text}"[:2000], reason=f.reason,
            source="wordlist",
        )


def _record_fingerprints(v, user, surface, target_id):
    from .models import PublishFingerprint

    rows = []
    if v.text_hash:
        rows.append(PublishFingerprint(user=user, surface=surface, target_id=str(target_id),
                                       hash_kind="text", digest=v.text_hash))
    for d in v.media_hashes:
        rows.append(PublishFingerprint(user=user, surface=surface, target_id=str(target_id),
                                       hash_kind="media", digest=d))
    if rows:
        PublishFingerprint.objects.bulk_create(rows)


def _tell_author_held(v, user, post):
    """Notices screen row (with Appeal) + a notification, same helpers the report auto-hold uses."""
    from user_profile import moderation_log as mlog
    from user_profile.models import ModerationAction

    reasons = v.hold_reasons
    row = mlog.log_action(
        ModerationAction.Action.AUTO_HOLD, target_type="post", target_id=post.pk, owner=user,
        reason=(reasons[0] if reasons else "")[:20], count=0,
        note=("Held at publish: " + ", ".join(reasons))[:500], owner_visible=True, can_appeal=True,
    )
    mlog.tell_owner(
        user, row, "Your post is waiting for review",
        "We hold some posts for a quick check before others can see them. You didn't lose it - "
        "it goes live once a moderator approves it. If you think this is a mistake, you can appeal in Settings > Notices.",
    )


def after_create(v, user, surface, obj):
    """Call once the Post / Story exists. Never raises (the object is already saved)."""
    try:
        record_publish(user, surface)
        _record_fingerprints(v, user, surface, obj.pk)
        _record_flags(v, user, surface, obj.pk)
        if v.hold and surface not in NO_HOLD_SURFACES:
            _tell_author_held(v, user, obj)
    except Exception:
        logger.exception("publish gate: after_create bookkeeping failed for %s %s", surface, getattr(obj, "pk", None))


def purge_old_fingerprints(now=None):
    """Delete duplicate-memory rows older than PUBLISH_FINGERPRINT_KEEP_DAYS (default 30). Returns the count."""
    from .models import PublishFingerprint

    now = now or timezone.now()
    cutoff = now - timedelta(days=int(_cfg("PUBLISH_FINGERPRINT_KEEP_DAYS", 30)))
    deleted, _ = PublishFingerprint.objects.filter(created_at__lt=cutoff).delete()
    return deleted
