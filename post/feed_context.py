"""
post/feed_context.py - T1 Part 4: the "educational lens" + campus / class context.

LearnScroll's differentiator: the feed should know what the viewer STUDIES.

  subjects        what the viewer studies, collected from
                  * classrooms they were accepted into (Classroom.subject)
                  * their campus subjects (approved teacher assignments of the
                    sections they are enrolled in)
                  * test-series they attempted (TestSeries.subject)
                  A post matches when its `subcategory` equals a subject or one
                  of its hashtags is the subject (spaces removed) -> +POINTS_SUBJECT
  study time      during `study_hours` (local time) posts of the `education`
                  category get +POINTS_STUDY (+POINTS_STUDY_OFF outside the
                  window), but only for viewers who study somewhere
                  ("learners") - a pure-entertainment account is not nagged
  context authors authors the viewer shares a campus with (staff + enrolled
                  students) or whose tuition class they joined (the teacher)
                  -> +POINTS_CONTEXT. Candidate generation for these is the
                  normal recommended pool (public posts); the bonus lifts them.

Additive points only (like every other signal): nothing is hidden, and the
bonus never widens the candidate set, so blocked / muted / hidden authors
(already removed from `base_qs`) can't leak through it.

Cross-app reads are lazy and wrapped: if campus / tuitionclass / testseries is
missing or a query fails, that part is just empty - the feed never 500s.
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List, NamedTuple, Set, Tuple

logger = logging.getLogger(__name__)

POINTS_SUBJECT = 10.0
POINTS_STUDY = 6.0
POINTS_STUDY_OFF = 3.0
POINTS_CONTEXT = 12.0
POINTS_EXAM = 8.0  # post hashtags match the viewer's declared exam / class (feed_exam.EXAM_TAGS)
POINTS_WEAK = 10.0  # post is about a topic the viewer keeps getting wrong in tests

DEFAULT_CONTEXT = {
    "enabled": True,  # env FEED_CONTEXT_ENABLED=0 switches it off
    "study_hours": (16, 23),  # [start, end) local hours = "study time"
    "max_subjects": 10,
    "max_context_authors": 500,
    "points_subject": POINTS_SUBJECT,
    "points_study": POINTS_STUDY,
    "points_study_off": POINTS_STUDY_OFF,
    "points_context": POINTS_CONTEXT,
    "points_exam": POINTS_EXAM,
    "points_weak": POINTS_WEAK,
    # weak topics (from testseries answers): a topic counts as weak with at
    # least `weak_min_answers` graded answers and accuracy below
    # `weak_max_accuracy`; looks at the most recent `weak_row_cap` answers;
    # cached per user for `weak_cache_seconds` (0 = no cache) because the feed
    # is a hot path.
    "weak_min_answers": 3,
    "weak_max_accuracy": 0.5,
    "max_weak_topics": 5,
    "weak_row_cap": 300,
    "weak_cache_seconds": 600,
}


class ContextSignals(NamedTuple):
    subjects: List[str]  # lower-case subject names
    is_learner: bool
    campus_authors: Set  # user ids sharing a campus with the viewer
    class_authors: Set  # teachers of the viewer's joined tuition classes
    # Added with the exam profile / weak-topic work. Trailing + defaulted, so
    # existing 4-argument constructions keep working.
    exam_tags: Tuple[str, ...] = ()  # hashtags of the viewer's declared exam / class
    weak_topics: Tuple[str, ...] = ()  # lower-case topics the viewer is weak at
    # The loaded feed_exam.ExamProfile (None = not loaded, e.g. context disabled) - lets
    # feed_exam.apply_exam_mode reuse it instead of querying UserPreference a second time.
    profile: object = None


EMPTY = ContextSignals([], False, set(), set())


def get_config(overrides: dict | None = None) -> dict:
    from django.conf import settings

    cfg = dict(DEFAULT_CONTEXT)
    cfg.update(getattr(settings, "FEED_CONTEXT", None) or {})
    cfg.update(overrides or {})
    return cfg


def is_study_time(hour: int, study_hours) -> bool:
    start, end = study_hours
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end  # window wrapping midnight


def subject_tags(subject: str) -> List[str]:
    """Hashtag spellings of a subject: 'Data Structures' -> ['datastructures', 'data_structures']."""
    s = (subject or "").strip().lower().lstrip("#")
    if not s:
        return []
    out = [s.replace(" ", ""), s.replace(" ", "_")]
    return list(dict.fromkeys(t for t in out if t))


def _safe(label, fn, default):
    try:
        return fn()
    except Exception:  # pragma: no cover - other apps' schema issues must not break the feed
        logger.warning("feed_context: %s failed", label, exc_info=True)
        return default


def weak_topics_from_rows(rows: Iterable[Tuple[str, bool]], min_answers: int, max_accuracy: float, cap: int) -> List[str]:
    """Pure: (topic, is_correct) rows -> weakest topics first (lower-case).

    A topic is weak with >= `min_answers` graded answers and accuracy strictly
    below `max_accuracy`. Lowest accuracy first, then most answers, then name."""
    stats: Dict[str, List[int]] = {}
    for topic, correct in rows:
        t = (topic or "").strip().lower()
        if not t:
            continue
        s = stats.setdefault(t, [0, 0])
        s[0] += 1
        s[1] += 1 if correct else 0
    weak = [
        (correct / total, -total, t)
        for t, (total, correct) in stats.items()
        if total >= min_answers and (correct / total) < max_accuracy
    ]
    weak.sort()
    return [t for _, _, t in weak[: max(0, int(cap))]]


def load_weak_topics(user, cfg: dict) -> Tuple[str, ...]:
    """The viewer's weak topics from their graded testseries answers
    (Question.topic + QuestionResponse.is_correct), cached briefly per user."""
    from django.core.cache import cache

    ttl = int(cfg.get("weak_cache_seconds") or 0)
    key = f"feed_weak_topics:{user.pk}"
    if ttl > 0:
        hit = cache.get(key)
        if hit is not None:
            return tuple(hit)

    from testseries.models import QuestionResponse

    rows = list(
        QuestionResponse.objects.filter(attempt__student=user, is_correct__isnull=False)
        .exclude(question__topic="")
        .order_by("-attempt__started_at")
        .values_list("question__topic", "is_correct")[: int(cfg["weak_row_cap"])]
    )
    topics = weak_topics_from_rows(
        rows, int(cfg["weak_min_answers"]), float(cfg["weak_max_accuracy"]), int(cfg["max_weak_topics"]),
    )
    if ttl > 0:
        cache.set(key, topics, ttl)
    return tuple(topics)


def load_context(user, cfg: dict | None = None) -> ContextSignals:
    cfg = cfg or get_config()
    if not cfg["enabled"] or not user or not getattr(user, "pk", None):
        return EMPTY
    subjects: List[str] = []
    campus_authors: Set = set()
    class_authors: Set = set()
    learner = False
    cap = int(cfg["max_context_authors"])

    def classes():
        from tuitionclass.models import ClassJoinRequest

        rows = list(
            ClassJoinRequest.objects.filter(student=user, status="accepted")
            .values_list("classroom__subject", "classroom__teacher_id")[:100]
        )
        return rows

    rows = _safe("tuition classes", classes, [])
    for subject, teacher_id in rows:
        learner = True
        if subject:
            subjects.append(subject.strip().lower())
        if teacher_id and teacher_id != user.pk:
            class_authors.add(teacher_id)

    def campus():
        from campus.models import StaffProfile, StudentEnrollment, SubjectTeacherassigments

        enrolled = list(
            StudentEnrollment.objects.filter(student=user, status="active")
            .values_list("section_id", "section__school_class__campus_id")[:20]
        )
        staff_campuses = list(
            StaffProfile.objects.filter(user=user, is_active=True).values_list("campus_id", flat=True)[:20]
        )
        campus_ids = {c for _, c in enrolled} | set(staff_campuses)
        names = []
        people: Set = set()
        if enrolled:
            names = list(
                SubjectTeacherassigments.objects.filter(
                    section_id__in=[s for s, _ in enrolled], status="approved"
                ).values_list("subject__name", flat=True).distinct()[:30]
            )
        if campus_ids:
            people |= set(
                StaffProfile.objects.filter(campus_id__in=campus_ids, is_active=True)
                .values_list("user_id", flat=True)[:cap]
            )
            people |= set(
                StudentEnrollment.objects.filter(
                    section__school_class__campus_id__in=campus_ids, status="active"
                ).values_list("student_id", flat=True)[:cap]
            )
        return bool(enrolled), names, people

    enrolled_flag, names, people = _safe("campus", campus, (False, [], set()))
    learner = learner or enrolled_flag
    subjects += [n.strip().lower() for n in names if n]
    campus_authors = {p for p in people if p != user.pk}

    def attempts():
        from testseries.models import TestAttempt

        return list(
            TestAttempt.objects.filter(student=user).exclude(series__subject="")
            .order_by("-started_at").values_list("series__subject", flat=True)[:60]
        )

    att = _safe("test attempts", attempts, [])
    if att:
        learner = True
        subjects += [s.strip().lower() for s in att if s]

    # What the student told us (exam / class / focus subjects) - see feed_exam.py.
    from . import feed_exam

    profile = _safe("exam profile", lambda: feed_exam.load_profile(user), feed_exam.EMPTY)
    if feed_exam.has_profile(profile):
        learner = True
        subjects += [s.strip().lower() for s in profile.subjects if s.strip()]
    exam_tags = tuple(feed_exam.profile_tags(profile))
    weak = tuple(_safe("weak topics", lambda: load_weak_topics(user, cfg), ()))

    # most frequent first, de-duplicated, capped
    counts: Dict[str, int] = {}
    for s in subjects:
        counts[s] = counts.get(s, 0) + 1
    ordered = [s for s, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][: int(cfg["max_subjects"])]
    return ContextSignals(
        ordered, learner, campus_authors - class_authors, class_authors, exam_tags, weak, profile,
    )


def matching_subject(ctx: ContextSignals, post) -> str:
    """The studied subject a post matches (subcategory or hashtag), or ""."""
    sub = (getattr(post, "subcategory", "") or "").strip().lower()
    tags = {str(t).strip().lstrip("#").lower() for t in (getattr(post, "hashtags", None) or [])}
    for subject in ctx.subjects:
        if sub and sub == subject:
            return subject
        if tags & set(subject_tags(subject)):
            return subject
    return ""


def context_expression(ctx: ContextSignals, now_local_hour: int, cfg: dict | None = None):
    """SQL expression (>= 0) = subject + study-time + context-author points, or None."""
    from django.db.models import Case, FloatField, Q, Value, When

    from . import feed_mix

    cfg = cfg or get_config()
    parts = []

    def case(cond, pts):
        return Case(When(cond, then=Value(float(pts))), default=Value(0.0), output_field=FloatField())

    if ctx.subjects:
        q = Q(subcategory__in=list(ctx.subjects))
        q |= Q(subcategory__in=[s.title() for s in ctx.subjects])
        for s in ctx.subjects:
            for tag in subject_tags(s):
                q |= feed_mix.hashtag_match_q(tag)
        parts.append(case(q, cfg["points_subject"]))
    if ctx.is_learner:
        pts = cfg["points_study"] if is_study_time(now_local_hour, cfg["study_hours"]) else cfg["points_study_off"]
        parts.append(case(Q(category="education"), pts))
    if ctx.exam_tags:
        q = Q()
        for tag in ctx.exam_tags:
            q |= feed_mix.hashtag_match_q(tag)
        parts.append(case(q, cfg["points_exam"]))
    if ctx.weak_topics:
        q = Q(subcategory__in=list(ctx.weak_topics))
        q |= Q(subcategory__in=[t.title() for t in ctx.weak_topics])
        for t in ctx.weak_topics:
            for tag in subject_tags(t):
                q |= feed_mix.hashtag_match_q(tag)
        parts.append(case(q, cfg["points_weak"]))
    authors = ctx.campus_authors | ctx.class_authors
    if authors:
        parts.append(case(Q(user_id__in=list(authors)), cfg["points_context"]))
    if not parts:
        return None
    total = parts[0]
    for p in parts[1:]:
        total = total + p
    return total
