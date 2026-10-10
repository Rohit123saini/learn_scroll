"""
post/feed_exam.py - student's own study profile -> feed.

What the student TOLD us (UserPreference: exam target, class, focus subjects,
exam date, Exam Mode) -- as opposed to `feed_context.py`, which INFERS what they
study from classrooms / campus / test attempts. Two jobs:

  1. Boost (via feed_context): posts whose hashtags match the exam / class
     (#jee, #neet, #cbse, #class12 ...) get extra points; the profile's focus
     subjects are added to the viewer's subject list.
  2. Exam Mode filter (`apply_exam_mode`): while ON (and the exam date, if set,
     has not passed) the home feed is narrowed to study content:
         category "education"
       | subcategory / hashtag matches a studied subject, weak topic or exam tag
       | posts by the teachers of tuition classes the viewer joined
     Everything else - entertainment, memes, classmates' social posts - is left
     out of every pool (following, trending, recommended, exploration).
     Distraction-free means exactly that: there is NO "fall back to the normal
     feed when nothing matches" - an empty feed is the honest answer and the
     user can switch Exam Mode off.

Fails soft: any error loading the profile means "no profile" (no boost, no
filter) rather than a 500 on the home feed.
"""
from __future__ import annotations

import logging
from typing import List, NamedTuple, Tuple

logger = logging.getLogger(__name__)

# Hashtag spellings (lower-case, no '#') that mark a post as relevant to an exam.
EXAM_TAGS = {
    "jee": ["jee", "jeemains", "jeemain", "jeeadvanced", "iitjee", "iit"],
    "neet": ["neet", "neetug", "neetpreparation", "aiims"],
    "board": ["boards", "boardexam", "boardexams", "cbse", "icse", "ncert", "isc"],
    "upsc": ["upsc", "ias", "civilservices", "upscprep", "upscpreparation"],
}


class ExamProfile(NamedTuple):
    exam_target: str = ""
    class_level: str = ""
    subjects: Tuple[str, ...] = ()
    exam_mode: bool = False  # EFFECTIVE (on and exam date not passed)


EMPTY = ExamProfile()


def class_tags(level: str) -> List[str]:
    """'12' -> ['class12', 'class_12', '12th']; non-numeric levels have no tags."""
    level = (level or "").strip().lower()
    if not level.isdigit():
        return []
    return [f"class{level}", f"class_{level}", f"{level}th"]


def profile_tags(profile: ExamProfile) -> List[str]:
    """Hashtags that mark a post as relevant to the profile's exam / class."""
    tags = list(EXAM_TAGS.get(profile.exam_target, []))
    tags += class_tags(profile.class_level)
    return list(dict.fromkeys(tags))


def has_profile(profile: ExamProfile) -> bool:
    return bool(profile.exam_target or profile.class_level or profile.subjects)


def load_profile(user) -> ExamProfile:
    if not user or not getattr(user, "pk", None):
        return EMPTY
    try:
        from user_profile.models import UserPreference

        pref = UserPreference.objects.filter(user=user).first()
        if pref is None:
            return EMPTY
        subjects = tuple(str(s).strip() for s in (pref.focus_subjects or []) if str(s).strip())
        return ExamProfile(
            exam_target=pref.exam_target or "",
            class_level=pref.class_level or "",
            subjects=subjects,
            exam_mode=pref.exam_mode_active,
        )
    except Exception:  # pragma: no cover - profile problems must never break the feed
        logger.warning("feed_exam: load_profile failed", exc_info=True)
        return EMPTY


def apply_exam_mode(user, base_qs, ctx=None):
    """Narrow `base_qs` to study content when the viewer has Exam Mode on.

    `ctx` (a feed_context.ContextSignals, optional) adds the subjects / weak
    topics / class teachers inferred from the viewer's classes and tests, and
    carries the already-loaded profile (saves a query). No profile / Exam Mode
    off -> `base_qs` unchanged."""
    profile = getattr(ctx, "profile", None)
    if profile is None:
        profile = load_profile(user)
    if not profile.exam_mode:
        return base_qs

    from django.db.models import Q

    from . import feed_context, feed_mix

    subjects: List[str] = []
    for s in list(profile.subjects) + list(getattr(ctx, "subjects", None) or []) + list(getattr(ctx, "weak_topics", None) or []):
        s = (s or "").strip().lower()
        if s and s not in subjects:
            subjects.append(s)

    q = Q(category="education")
    if subjects:
        q |= Q(subcategory__in=subjects) | Q(subcategory__in=[s.title() for s in subjects])
    tags = profile_tags(profile)
    for s in subjects:
        tags += feed_context.subject_tags(s)
    for tag in dict.fromkeys(tags):
        q |= feed_mix.hashtag_match_q(tag)
    teachers = set(getattr(ctx, "class_authors", None) or ())
    if teachers:
        q |= Q(user_id__in=list(teachers))
    return base_qs.filter(q)
