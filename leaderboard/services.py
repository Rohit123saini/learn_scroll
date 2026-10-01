"""
leaderboard/services.py

Two jobs live here:
  1. Period helpers (`current_week_key`, `week_bounds`) — the single
     definition of what "this week" means, shared by tasks.py (writes) and
     views.py (reads) so they can never disagree on the current period_key.
  2. `compute_*_scores()` — pure aggregation functions, one per scope_type,
     each returning `[{"user_id", "score", "metadata"}, ...]` for ONE board.
     `leaderboard.tasks` calls these and writes the result into
     `LeaderboardEntry`; nothing here touches that table itself, so these
     stay trivially unit-testable against just testseries/campus/post data.
  3. `get_board`/`get_my_rank` — the cache-first READ path views.py uses.
     Every aggregation above (TestAttempt/Attendance/PostLike scans) only
     ever runs from `tasks.py` on a schedule; a request never recomputes a
     board itself, only reads the table + a short-TTL cache in front of it
     (Task G7's "Area: Backend (aggregation + caching)").

Every testseries/campus/post import below is LOCAL to its function — this
app must never force a load-order dependency on any of them (same lazy-
import convention `core/tasks.py` documents).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.core.cache import cache
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from .models import LeaderboardEntry

logger = logging.getLogger(__name__)

# How long a paged board response is cached before a request falls through
# to the DB again. Short enough that a just-run recompute (tasks.py runs
# every 30 min for weekly boards, see LearnScroll/settings.py
# CELERY_BEAT_SCHEDULE) is visible well within the same "session", long
# enough that a popular series/section's leaderboard tab doesn't hammer the
# DB on every open.
CACHE_TTL_SECONDS = 300
CACHE_VERSION = "v1"

# Engagement-board weights (Task G7, section B cross-reference: "app per
# engagement" scope). Posting is weighted highest since it's the scarcest,
# most valuable action; a plain like is the cheapest signal so it counts
# for least. Tunable here without touching tasks.py or the model.
ENGAGEMENT_WEIGHTS = {
    "posts": 3,
    "likes_received": 1,
    "comments_received": 2,
    "shares_received": 4,
    "comments_made": 1,
}

# Campus-section blend: attendance is the reliably-weekly signal (marked
# every school day); academics (ResultEntry) only land a few times a term,
# so it's kept a minority weight rather than making the whole board go
# blank between exam terms.
CAMPUS_ATTENDANCE_WEIGHT = 0.6
CAMPUS_ACADEMIC_WEIGHT = 0.4


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

def current_week_key(now=None) -> str:
    """ISO-8601 week key, e.g. '2026-W39'. Monday-start, matching Python's
    (and most schools') definition of a week — this is what makes the
    weekly board "reset": a Monday rollover is simply a new period_key with
    no rows yet until the next recompute writes them."""
    now = now or timezone.now()
    iso_year, iso_week, _ = now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def week_bounds(period_key: str):
    """[start, end) tz-aware datetimes (project TIME_ZONE) an ISO week key
    covers. `%G-W%V` alone isn't accepted by strptime the way `%G-W%V-%u`
    is, so a day-of-week is appended and then discarded."""
    start_date = datetime.strptime(f"{period_key}-1", "%G-W%V-%u").date()
    start = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    return start, start + timedelta(days=7)


# ---------------------------------------------------------------------------
# Score computation — one function per scope_type. Each takes the current
# period_type/period_key and returns unsorted-is-fine (tasks.py sorts and
# assigns rank) score rows for that one board.
# ---------------------------------------------------------------------------

def compute_test_series_scores(series_id, period_type: str, period_key: str):
    """Best (max) `final_score` per student on this series, restricted to
    fully `checked` attempts (an in-progress/partially-checked attempt has
    no settled score to rank on). Weekly boards only count attempts
    *submitted* inside that week — a student's best score from a week ago
    doesn't linger on this week's board."""
    from testseries.models import TestAttempt

    qs = TestAttempt.objects.filter(
        series_id=series_id, status=TestAttempt.Status.CHECKED, final_score__isnull=False,
    )
    if period_type == LeaderboardEntry.PeriodType.WEEKLY:
        start, end = week_bounds(period_key)
        qs = qs.filter(submitted_at__gte=start, submitted_at__lt=end)

    rows = (
        qs.values("student_id")
        .annotate(best_score=Max("final_score"), attempts=Count("id"))
    )
    return [
        {
            "user_id": row["student_id"],
            "score": float(row["best_score"]),
            "metadata": {"attempts": row["attempts"], "best_score": row["best_score"]},
        }
        for row in rows
    ]


def compute_campus_section_scores(section_id, period_type: str, period_key: str):
    """Blend of attendance % and academic % for every ACTIVE enrollment in
    this section. A student with zero activity in the period (no attendance
    row and no result row) is left off the board entirely, same as a test
    series student who never attempted — an empty board is more honest than
    everyone tied at zero."""
    from campus.models import Attendance, ResultEntry, StudentEnrollment

    enrollments = list(
        StudentEnrollment.objects.filter(section_id=section_id, status=StudentEnrollment.Status.ACTIVE)
        .values_list("id", "student_id")
    )
    if not enrollments:
        return []
    enrollment_to_student = dict(enrollments)
    enrollment_ids = list(enrollment_to_student.keys())

    attendance_qs = Attendance.objects.filter(enrollment_id__in=enrollment_ids)
    if period_type == LeaderboardEntry.PeriodType.WEEKLY:
        start, end = week_bounds(period_key)
        attendance_qs = attendance_qs.filter(date__gte=start.date(), date__lt=end.date())

    attendance_by_enrollment = {
        row["enrollment_id"]: (row["present"] / row["total"] * 100 if row["total"] else None)
        for row in attendance_qs.values("enrollment_id").annotate(
            present=Count("id", filter=Q(status__in=[Attendance.Status.PRESENT, Attendance.Status.LATE])),
            total=Count("id"),
        )
    }

    result_qs = ResultEntry.objects.filter(enrollment_id__in=enrollment_ids)
    if period_type == LeaderboardEntry.PeriodType.WEEKLY:
        start, end = week_bounds(period_key)
        # ResultEntry has no date of its own — it's dated by its exam term.
        result_qs = result_qs.filter(exam_term__end_date__gte=start.date(), exam_term__end_date__lt=end.date())

    academic_by_enrollment = {
        row["enrollment_id"]: (float(row["obtained"]) / float(row["max_marks"]) * 100 if row["max_marks"] else None)
        for row in result_qs.values("enrollment_id").annotate(
            obtained=Sum("marks_obtained"), max_marks=Sum("max_marks"),
        )
    }

    rows = []
    for enrollment_id, student_id in enrollments:
        attendance_pct = attendance_by_enrollment.get(enrollment_id)
        academic_pct = academic_by_enrollment.get(enrollment_id)
        if attendance_pct is None and academic_pct is None:
            continue
        if academic_pct is None:
            score = attendance_pct
        elif attendance_pct is None:
            score = academic_pct
        else:
            score = attendance_pct * CAMPUS_ATTENDANCE_WEIGHT + academic_pct * CAMPUS_ACADEMIC_WEIGHT
        rows.append({
            "user_id": student_id,
            "score": round(score, 2),
            "metadata": {
                "attendance_pct": round(attendance_pct, 1) if attendance_pct is not None else None,
                "academic_pct": round(academic_pct, 1) if academic_pct is not None else None,
            },
        })
    return rows


def compute_engagement_scores(period_type: str, period_key: str):
    """App-wide activity score: posts authored + likes/comments/shares
    *received* on those posts + comments the user made — a mix of "made
    good content" and "showed up and participated" (Task G7's third scope).
    Only approved, non-deleted posts count; only non-deleted comments."""
    from post.models import Post, PostComment, PostLike, PostShare

    def _clip(qs, field="created_at"):
        if period_type == LeaderboardEntry.PeriodType.WEEKLY:
            start, end = week_bounds(period_key)
            return qs.filter(**{f"{field}__gte": start, f"{field}__lt": end})
        return qs

    base_posts = Post.objects.filter(is_deleted=False, moderation_status="approved")
    posts_qs = _clip(base_posts)

    posts_by_user = dict(
        posts_qs.values("user_id").annotate(c=Count("id")).values_list("user_id", "c")
    )
    # Likes/comments/shares "received" are counted against posts CREATED in
    # the window for a weekly board (this week's new posts' engagement),
    # not every like landing this week on posts from any time — keeps a
    # weekly board about this week's *content*, not old posts trending late.
    post_ids_in_window = list(posts_qs.values_list("id", flat=True)) if period_type == LeaderboardEntry.PeriodType.WEEKLY else None

    likes_qs = PostLike.objects.filter(post__is_deleted=False)
    comments_qs = PostComment.objects.filter(is_deleted=False)
    shares_qs = PostShare.objects.filter(post__is_deleted=False)
    if post_ids_in_window is not None:
        likes_qs = likes_qs.filter(post_id__in=post_ids_in_window)
        comments_qs = comments_qs.filter(post_id__in=post_ids_in_window)
        shares_qs = shares_qs.filter(post_id__in=post_ids_in_window)

    likes_by_owner = dict(
        likes_qs.values("post__user_id").annotate(c=Count("id")).values_list("post__user_id", "c")
    )
    comments_received_by_owner = dict(
        comments_qs.values("post__user_id").annotate(c=Count("id")).values_list("post__user_id", "c")
    )
    shares_by_owner = dict(
        shares_qs.values("post__user_id").annotate(c=Count("id")).values_list("post__user_id", "c")
    )
    comments_made_qs = _clip(PostComment.objects.filter(is_deleted=False))
    comments_made_by_user = dict(
        comments_made_qs.values("user_id").annotate(c=Count("id")).values_list("user_id", "c")
    )

    user_ids = set(posts_by_user) | set(likes_by_owner) | set(comments_received_by_owner) | set(shares_by_owner) | set(comments_made_by_user)
    rows = []
    for user_id in user_ids:
        posts = posts_by_user.get(user_id, 0)
        likes_received = likes_by_owner.get(user_id, 0)
        comments_received = comments_received_by_owner.get(user_id, 0)
        shares_received = shares_by_owner.get(user_id, 0)
        comments_made = comments_made_by_user.get(user_id, 0)
        score = (
            posts * ENGAGEMENT_WEIGHTS["posts"]
            + likes_received * ENGAGEMENT_WEIGHTS["likes_received"]
            + comments_received * ENGAGEMENT_WEIGHTS["comments_received"]
            + shares_received * ENGAGEMENT_WEIGHTS["shares_received"]
            + comments_made * ENGAGEMENT_WEIGHTS["comments_made"]
        )
        if score <= 0:
            continue
        rows.append({
            "user_id": user_id,
            "score": float(score),
            "metadata": {
                "posts": posts,
                "likes_received": likes_received,
                "comments_received": comments_received,
                "shares_received": shares_received,
                "comments_made": comments_made,
            },
        })
    return rows


# ---------------------------------------------------------------------------
# Read path — cache-first over LeaderboardEntry. Never aggregates raw
# activity itself; that's tasks.py's job on a schedule.
# ---------------------------------------------------------------------------

def _cache_key(scope_type, scope_id, period_type, period_key, limit, offset):
    return f"leaderboard:{CACHE_VERSION}:{scope_type}:{scope_id or '-'}:{period_type}:{period_key}:{limit}:{offset}"


def get_board(scope_type, scope_id, period_type, period_key, limit=50, offset=0):
    """Cache-first page of one board. Returns (rows, total_count) — rows are
    `LeaderboardEntry` instances (with `.user` pre-selected) ordered by rank."""
    key = _cache_key(scope_type, scope_id, period_type, period_key, limit, offset)
    cached = cache.get(key)
    if cached is not None:
        return cached

    qs = (
        LeaderboardEntry.objects.filter(
            scope_type=scope_type, scope_id=scope_id, period_type=period_type, period_key=period_key,
        )
        .select_related("user")
        .order_by("rank")
    )
    total = qs.count()
    rows = list(qs[offset: offset + limit])
    cache.set(key, (rows, total), CACHE_TTL_SECONDS)
    return rows, total


def get_my_rank(user, scope_type, scope_id, period_type, period_key):
    """Uncached by design — a single indexed row lookup, and callers (e.g.
    the result screen right after a submit) need it to reflect the very
    latest recompute more faithfully than the paged/cached board does."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return LeaderboardEntry.objects.filter(
        scope_type=scope_type, scope_id=scope_id, period_type=period_type, period_key=period_key, user=user,
    ).first()


def invalidate_board(scope_type, scope_id, period_type, period_key):
    """Called by tasks.py right after rewriting a board so a stale cached
    page can't outlive the recompute that made it wrong. Best-effort: clears
    the handful of (limit, offset=0) combinations the frontend actually
    requests rather than trying to enumerate every possible page."""
    for limit in (10, 20, 50, 100):
        cache.delete(_cache_key(scope_type, scope_id, period_type, period_key, limit, 0))
