"""
post/auto_title.py  (T3.1)

Title is OPTIONAL on every media / file post (notes, resource, lecture, image, video, document). When the author leaves it
empty the server makes one, so the feed card, search and share links never show a blank name:

    1. the first 50 characters of the author's text (cut at a word end, one line, no markdown markers),
    2. else the first file's name without its extension ("Chapter 3 notes.pdf" -> "Chapter 3 notes"),
    3. else "Post - 6 Nov 2026".

Pure functions (no DB) so they are trivial to unit-test. Written by reading the code; NOT run yet.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime

from django.utils import timezone

AUTO_TITLE_MAX = 50

_MARKERS = re.compile(r"[*_`~#>]+")          # bold / italic / code / heading / quote markers typed in the composer
_SPACES = re.compile(r"\s+")
_GENERIC_FILE_STEM = re.compile(r"^(img|image|vid|video|screenshot|photo|pxl|dsc|mov|file|document|scan)?[\s_\-]*[0-9_\-\s]*$", re.I)


def _one_line(text) -> str:
    return _SPACES.sub(" ", _MARKERS.sub("", str(text or ""))).strip()


def _cut(text: str, limit: int = AUTO_TITLE_MAX) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    space = head.rfind(" ")
    if space >= limit // 2:          # prefer a word end, but never throw away most of the text
        head = head[:space]
    return head.rstrip(" ,;:-.") + "\u2026"


def title_from_text(text) -> str:
    return _cut(_one_line(text))


def title_from_filename(name) -> str:
    """File name -> readable title; '' for camera-style names ("IMG_20261106_101530") that say nothing."""
    stem = os.path.splitext(os.path.basename(str(name or "")))[0]
    stem = _SPACES.sub(" ", stem.replace("_", " ").replace("-", " ")).strip()
    if not stem or _GENERIC_FILE_STEM.match(stem):
        return ""
    return _cut(stem)


def title_from_date(when=None) -> str:
    when = when or timezone.localdate()
    if isinstance(when, datetime):
        when = timezone.localtime(when).date() if timezone.is_aware(when) else when.date()
    assert isinstance(when, date)
    return "Post - %d %s %d" % (when.day, when.strftime("%b"), when.year)


def derive_title(text=None, file_names=(), when=None) -> str:
    """Never empty: text -> first usable file name -> dated fallback."""
    t = title_from_text(text)
    if t:
        return t
    for name in file_names or ():
        t = title_from_filename(name)
        if t:
            return t
    return title_from_date(when)


def ensure_title(title, text=None, file_names=(), when=None) -> str:
    """The author's own title when there is one, otherwise [derive_title]."""
    own = _one_line(title)
    return own if own else derive_title(text, file_names, when)
