"""
common/media_moderation.py - the server-side hook for adult / unsafe IMAGES and VIDEOS (P-F15, point 3).

Text is already screened by common/moderation.py (profanity + adult words). Pictures cannot be judged without a model, and
this project does not ship one, so this module is only the SOCKET:

    settings.MEDIA_NSFW_PROVIDER = "pkg.module.func"
    func(name, file=None, path=None, kind="image") -> {"nsfw": True, "score": 0.97, "label": "porn"} | None

(`file` = an open upload, `path` = an assembled chunked file on disk; one of them is set. `kind` = image | video.)
Plug in any provider you choose (AWS Rekognition, Google Vision SafeSearch, a local NSFW model ...) by writing that one function.

    scan(items) -> list[MediaHit]       hits the provider reported; never raises, fails OPEN (a provider error = no hit)

The caller (post/publish_gate.check_media) decides what a hit does:
    MEDIA_NSFW_ACTION = "hold" (default)  score >= MEDIA_NSFW_MIN_SCORE -> the post is created hidden + moderator queue +
                                          owner notice with Appeal; a story (no hold state) is refused instead
                        "flag"            only queue it for a moderator
                        "off"             do nothing
Without a provider nothing happens and nothing is guessed. A hit below MEDIA_NSFW_MIN_SCORE is only flagged.
"""

import logging
import os
from dataclasses import dataclass

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
MAX_ITEMS = 6          # a post can carry many files; scanning is paid per call, so look at the first few only


@dataclass
class MediaHit:
    name: str
    kind: str
    score: float
    label: str = ""


def _cfg(name, default):
    return getattr(settings, name, default)


def action():
    a = str(_cfg("MEDIA_NSFW_ACTION", "hold")).lower()
    return a if a in ("hold", "flag", "off") else "hold"


def min_score():
    try:
        return float(_cfg("MEDIA_NSFW_MIN_SCORE", 0.85))
    except (TypeError, ValueError):
        return 0.85


def provider():
    path = _cfg("MEDIA_NSFW_PROVIDER", "")
    if not path:
        return None
    try:
        return import_string(path)
    except Exception:
        logger.exception("media moderation: MEDIA_NSFW_PROVIDER %r could not be imported", path)
        return None


def kind_of(name="", content_type="", declared=""):
    """'image' | 'video' | '' (audio / documents are not scanned here)."""
    ext = os.path.splitext(name or "")[1].lower()
    ct = (content_type or "").lower()
    declared = (declared or "").lower()
    if declared in ("image", "gif") or ct.startswith("image/") or ext in IMAGE_EXTENSIONS:
        return "image"
    if declared == "video" or ct.startswith("video/") or ext in VIDEO_EXTENSIONS:
        return "video"
    return ""


def scan(items):
    """`items` = objects with .name .content_type .declared_type .file .path (publish_gate.Item). -> list[MediaHit]."""
    if action() == "off":
        return []
    fn = provider()
    if fn is None:
        return []
    hits = []
    for it in list(items)[:MAX_ITEMS]:
        kind = kind_of(getattr(it, "name", "") or getattr(it, "path", ""), getattr(it, "content_type", ""),
                       getattr(it, "declared_type", ""))
        if not kind:
            continue
        try:
            res = fn(it.name, file=it.file, path=it.path, kind=kind)
        except Exception:
            logger.exception("media moderation: provider failed for %s - ignoring it", getattr(it, "name", ""))
            continue
        finally:
            try:
                if getattr(it, "file", None) is not None:
                    it.file.seek(0)      # the provider may have read the upload; the view must still be able to save it
            except Exception:
                pass
        if res and res.get("nsfw"):
            try:
                score = float(res.get("score", 1.0))
            except (TypeError, ValueError):
                score = 1.0
            hits.append(MediaHit(name=getattr(it, "name", "") or os.path.basename(getattr(it, "path", "")),
                                 kind=kind, score=score, label=str(res.get("label") or "")[:40]))
    return hits
