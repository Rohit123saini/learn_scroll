"""
post/feed_chips.py - H12: Subject / Category / Exam chips on the Home feed.

The Home screen shows a chip row above the feed (All · Physics · Maths · Chemistry · JEE · NEET · Boards).
A chip is just a query param on GET /post/feed/:

    ?subject=physics      posts whose `subcategory` is that subject, or whose hashtags name it
                          (#physics, #phy ...; "Data Structures" -> #datastructures / #data_structures)
    ?category=education   `Post.category` (any key of Post.CATEGORY_CHOICES, or its label, case-insensitive)
    ?exam=jee             posts with that exam's hashtags (feed_exam.EXAM_TAGS: jee | neet | board | upsc;
                          "boards" is accepted for "board")

The three can be combined (AND). Unknown values:
  * `category` / `exam`  -> 400 with the allowed values (the client only sends values it knows)
  * `subject`            -> free text (length / charset checked); an unknown subject is simply an empty-ish feed

WHY THE FILTER HAS A KEY (the "cursor mix" bug this prevents)
-------------------------------------------------------------
The feed is ranked once, frozen in a snapshot and paged with an opaque cursor (feed_snapshot.py). If a chip changed
between page 1 and page 2 the cursor's offsets would be applied to a DIFFERENT list. So the filter's `token`
(a short hash, "" when no chip is active) is
  1. part of the snapshot cache key        -> a filtered snapshot can never be read by an unfiltered request
  2. part of the candidate-cache key       -> same for the 30 s page-1 cache (feed_cache.py)
  3. inside the cursor ("f")               -> a cursor of one chip used with another chip is a 404
                                              (the app answers a 404 cursor by starting a fresh session)
An unfiltered request has token "" and keeps every old key / cursor byte-for-byte (old app builds keep working).

Exam Mode (feed_exam.apply_exam_mode) is applied on top of a chip, never instead of it: a chip can only narrow the
feed further, it cannot get round "distraction-free".
"""
from __future__ import annotations

import hashlib
import re
from typing import List, NamedTuple, Optional

from rest_framework.exceptions import ValidationError

# Canonical subject -> spellings that mean the same thing in `subcategory` / hashtags. A subject that is not
# listed here is used as typed (lower-cased).
SUBJECT_ALIASES = {
    "physics": ["physics", "phy"],
    "maths": ["maths", "math", "mathematics"],
    "chemistry": ["chemistry", "chem"],
    "biology": ["biology", "bio"],
    "english": ["english"],
    "hindi": ["hindi"],
}
_ALIAS_TO_CANONICAL = {alias: canon for canon, aliases in SUBJECT_ALIASES.items() for alias in aliases}

# Exam chip spellings the client / users may send -> key of feed_exam.EXAM_TAGS.
EXAM_ALIASES = {"boards": "board", "board": "board", "jee": "jee", "neet": "neet", "upsc": "upsc"}

_SUBJECT_RE = re.compile(r"^[\w .&+\-]{1,40}$", re.UNICODE)


class FeedFilter(NamedTuple):
    subject: str = ""
    category: str = ""
    exam: str = ""

    @property
    def active(self) -> bool:
        return bool(self.subject or self.category or self.exam)

    @property
    def key(self) -> str:
        """Canonical text of the filter ("" = none). Stable: the same chips always give the same key."""
        parts = []
        if self.subject:
            parts.append(f"s={self.subject}")
        if self.category:
            parts.append(f"c={self.category}")
        if self.exam:
            parts.append(f"e={self.exam}")
        return ";".join(parts)

    @property
    def token(self) -> str:
        """Short, cache-key / cursor safe id of the filter ("" = none)."""
        if not self.active:
            return ""
        return "f" + hashlib.sha1(self.key.encode("utf-8")).hexdigest()[:12]


NO_FILTER = FeedFilter()


def _first(query_params, name: str) -> str:
    value = query_params.get(name)
    return str(value).strip() if value is not None else ""


def parse(query_params) -> FeedFilter:
    """Request query params -> FeedFilter. Raises DRF ValidationError (400) for a bad category / exam / subject."""
    errors = {}

    subject = _first(query_params, "subject").lower()
    if subject:
        if not _SUBJECT_RE.match(subject):
            errors["subject"] = "Subject must be 1-40 letters, digits, spaces or . & + -"
        else:
            subject = " ".join(subject.split())  # collapse inner whitespace
            subject = _ALIAS_TO_CANONICAL.get(subject, subject)

    category = ""
    raw_category = _first(query_params, "category")
    if raw_category:
        from .serializers import category_error_message, normalize_category

        category = normalize_category(raw_category) or ""
        if not category:
            errors["category"] = category_error_message(raw_category)

    exam = ""
    raw_exam = _first(query_params, "exam").lower()
    if raw_exam:
        exam = EXAM_ALIASES.get(raw_exam, "")
        if not exam:
            errors["exam"] = f'"{raw_exam}" is not a valid exam. Allowed values: {", ".join(sorted(set(EXAM_ALIASES.values())))}.'

    if errors:
        raise ValidationError(errors)
    return FeedFilter(subject=subject, category=category, exam=exam)


def subject_spellings(subject: str) -> List[str]:
    """All spellings that count as `subject` ('maths' -> maths, math, mathematics)."""
    return SUBJECT_ALIASES.get(subject, [subject])


def apply(qs, flt: Optional[FeedFilter]):
    """Narrow a Post queryset to the chip. No chip -> `qs` unchanged (same object)."""
    if flt is None or not flt.active:
        return qs

    from django.db.models import Q

    from . import feed_context, feed_exam, feed_mix

    if flt.category:
        qs = qs.filter(category=flt.category)

    if flt.subject:
        q = Q()
        for spelling in subject_spellings(flt.subject):
            q |= Q(subcategory__iexact=spelling)
            for tag in feed_context.subject_tags(spelling):
                q |= feed_mix.hashtag_match_q(tag)
        qs = qs.filter(q)

    if flt.exam:
        q = Q()
        for tag in feed_exam.EXAM_TAGS.get(flt.exam, []):
            q |= feed_mix.hashtag_match_q(tag)
        qs = qs.filter(q)

    return qs
