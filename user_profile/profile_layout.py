"""
user_profile/profile_layout.py - P5 "Role-wise profile layouts". NOT RUN. No model change => no migration.

    GET /profile/profile/<username>/layout/          (view: layout_views.ProfileLayoutView)

One answer that tells the app WHICH sections the profile should draw and gives the numbers those sections need, so the
Flutter profile is conditional on the server instead of guessing from `category_label`.

    {
      "username": "...", "is_self": bool, "locked": bool,
      "profile_layout": "student" | "teacher" | "creator" | "parent",     # primary layout (display decision only)
      "roles": ["student", "teacher", ...],                                # every role the profile shows
      "sections": ["teacher_stats", "upcoming_live", ...],                 # ordered; client skips unknown ones
      "teacher": {...} | null,  "creator": {...} | null,  "campus": {...} | null,  "parent": {...} | null
    }

Layouts (priority teacher > parent(owner only) > creator > student):
  student  exam target, class, streak, badges, certificates (all already on the profile; `sections` just orders them)
  teacher  classes taught, rating, students count, upcoming live classes, "Book trial class", verified-teacher badge, reviews
  creator  reels / series / highlights counts (tap -> reels / series list / highlights) + newest series preview (+ coins for the owner only)
  parent   link to the parent dashboard (owner only - a parent is never shown to visitors)
  campus   a chip (campus / class / section), NOT a layout - any layout can carry it

Privacy (same spirit as achievements.py):
  * blocked either way -> the view answers 404 (never reaches this module);
  * private account + viewer not an accepted follower -> `locked: true`, nothing else;
  * only PUBLIC facts for visitors: public classrooms (active, not deleted, not flagged - what Explore shows), published
    individual test series, public approved reels; reviews carry rating + comment + date, never the reviewer;
  * campus chip: the owner always sees it; a visitor only if the owner is NOT a minor AND the visitor belongs to the same
    approved campus (active enrollment or active staff row). There is no per-user "show my school" switch yet, so the safe
    default is "people of that campus only";
  * parent block and coins: owner only.
Every lookup is its own try/except: one broken table drops only its block (fail closed), never the whole profile.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.utils import timezone

logger = logging.getLogger(__name__)

LAYOUT_VERSION = 1
CREATOR_MIN_ITEMS = 3          # reels + published series needed before the creator layout takes over from "student"
MAX_UPCOMING = 3
MAX_SERIES_PREVIEW = 3
MAX_CLASSES = 5
MAX_REVIEWS = 3
COMMENT_CHARS = 200

SECTIONS = {
    "student": ["exam_target", "streak", "badges", "certificates"],
    "teacher": ["teacher_stats", "trial_cta", "upcoming_live", "classes", "reviews", "badges", "certificates"],
    "creator": ["creator_stats", "highlights", "series", "badges", "certificates"],
    "parent": ["parent_dashboard", "badges"],
}


def _safe(label, fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one broken lookup must not hide the rest of the profile
        logger.exception("profile_layout lookup failed: %s", label)
        return default


# --------------------------------------------------------------------------- teacher

def _public_classrooms(owner):
    from tuitionclass.models import Classroom

    return Classroom.objects.filter(teacher=owner, is_active=True, is_deleted=False, is_flagged=False)


def teacher_block(owner) -> dict | None:
    from tuitionclass.models import ClassroomReview, ClassSession
    from .services import teacher_verified_field

    rooms = list(_public_classrooms(owner).order_by("-enrolled_count", "-rating_count", "id")[:50])
    if not rooms:
        return None

    students = sum(int(r.enrolled_count or 0) for r in rooms)
    rating_count = sum(int(r.rating_count or 0) for r in rooms)
    rating_avg = None
    if rating_count:
        total = sum(Decimal(r.rating_avg or 0) * int(r.rating_count or 0) for r in rooms)
        rating_avg = round(float(total / rating_count), 2)

    ids = [r.pk for r in rooms]
    now = timezone.now()
    upcoming = []
    sessions = (
        ClassSession.objects.filter(classroom_id__in=ids, status__in=["scheduled", "live"], scheduled_end__gte=now)
        .select_related("classroom")
        .order_by("scheduled_start")[:MAX_UPCOMING]
    )
    for s in sessions:
        upcoming.append({
            "session_id": s.pk, "classroom_id": s.classroom_id, "title": s.classroom.title,
            "subject": s.classroom.subject, "status": s.status,
            "scheduled_start": s.scheduled_start.isoformat(),
        })

    trial = next((r for r in rooms if r.is_trial_enabled), None)
    reviews = []
    for rv in (
        ClassroomReview.objects.filter(classroom_id__in=ids).exclude(comment="")
        .order_by("-created_at")[:MAX_REVIEWS]
    ):
        text = " ".join((rv.comment or "").split())
        reviews.append({
            "rating": rv.rating,
            "comment": text if len(text) <= COMMENT_CHARS else text[: COMMENT_CHARS - 3].rstrip() + "...",
            "created_at": rv.created_at.isoformat(),
        })

    return {
        "verified": bool(getattr(owner, teacher_verified_field(), False)),
        "classes_count": len(rooms),
        "students_count": students,
        "rating_avg": rating_avg,
        "rating_count": rating_count,
        "upcoming_live": upcoming,
        "classes": [
            {"id": r.pk, "title": r.title, "subject": r.subject, "rating_avg": float(r.rating_avg or 0),
             "rating_count": int(r.rating_count or 0), "enrolled_count": int(r.enrolled_count or 0)}
            for r in rooms[:MAX_CLASSES]
        ],
        "trial": (
            {"classroom_id": trial.pk, "title": trial.title, "minutes": int(trial.trial_duration_minutes or 0)}
            if trial else None
        ),
        "reviews": reviews,
    }


# --------------------------------------------------------------------------- creator

def creator_block(owner, viewer=None, *, is_self: bool) -> dict:
    """Creator numbers + a small series preview so the profile can NAVIGATE (not only show counts).

    highlights_count: the owner sees every highlight of theirs; a visitor only the ones that have at least one story that
    visitor may see (same rule as GET /post/highlights/ - a Close-Friends-only highlight is not even counted).
    series_items: newest published individual series (what Explore shows), at most MAX_SERIES_PREVIEW.
    """
    from post.models import Post

    reels = Post.objects.filter(
        user=owner, post_type="video", is_deleted=False, is_archived=False,
        visibility="public", moderation_status="approved",
    ).count()
    series = _safe("series_count", lambda: _series_count(owner), 0)
    highlights = _safe("highlights_count", lambda: _highlights_count(owner, viewer, is_self), 0)
    data = {
        "reels_count": reels, "series_count": series, "highlights_count": highlights,
        "series_items": _safe("series_items", lambda: _series_items(owner), []),
    }
    if is_self:
        data["coins"] = int(getattr(owner, "coin", 0) or 0)   # monetisation numbers: owner only
    return data


def _series_qs(owner):
    from testseries.models import TestSeries

    return TestSeries.objects.filter(
        creator=owner, status=TestSeries.Status.PUBLISHED, source=TestSeries.Source.INDIVIDUAL
    )


def _series_count(owner) -> int:
    return _series_qs(owner).count()


def _series_items(owner) -> list:
    rows = _series_qs(owner).order_by("-created_at")[:MAX_SERIES_PREVIEW]
    return [
        {"id": str(s.pk), "title": " ".join((s.title or "").split())[:80], "subject": " ".join((s.subject or "").split())[:60],
         "is_paid": bool(s.is_paid)}
        for s in rows
    ]


def _highlights_count(owner, viewer, is_self: bool) -> int:
    from post.highlights import can_view_highlights_of, visible_story_ids
    from post.models import Highlight

    if is_self or viewer is None:
        return Highlight.objects.filter(user=owner).count() if is_self else 0
    if not can_view_highlights_of(owner, viewer):
        return 0
    return Highlight.objects.filter(user=owner, items__story_id__in=visible_story_ids(viewer)).distinct().count()


# --------------------------------------------------------------------------- campus chip

def _enrollment(owner):
    from campus.models import Campus, StudentEnrollment

    return (
        StudentEnrollment.objects.filter(
            student=owner, status=StudentEnrollment.Status.ACTIVE,
            section__school_class__campus__verification_status=Campus.VerificationStatus.APPROVED,
        )
        .select_related("section__school_class__campus")
        .order_by("section__school_class__campus_id")
        .first()
    )


def _viewer_campus_ids(viewer) -> set:
    from campus.models import Campus, StaffProfile, StudentEnrollment

    approved = Campus.VerificationStatus.APPROVED
    ids = set(
        StudentEnrollment.objects.filter(
            student=viewer, status=StudentEnrollment.Status.ACTIVE,
            section__school_class__campus__verification_status=approved,
        ).values_list("section__school_class__campus_id", flat=True)[:10]
    )
    ids |= set(
        StaffProfile.objects.filter(user=viewer, is_active=True, campus__verification_status=approved)
        .values_list("campus_id", flat=True)[:10]
    )
    return ids


def campus_block(owner, viewer, *, is_self: bool, is_minor: bool) -> dict | None:
    if not is_self and is_minor:
        return None
    enr = _enrollment(owner)
    if enr is None:
        return None
    campus = enr.section.school_class.campus
    if not is_self and campus.pk not in _viewer_campus_ids(viewer):
        return None
    return {
        "campus_id": str(campus.pk), "campus_name": campus.name,
        "class_name": enr.section.school_class.name, "section_name": enr.section.name,
    }


# --------------------------------------------------------------------------- parent (owner only)

def parent_block(owner) -> dict | None:
    from campus.models import CampusParentLink

    n = CampusParentLink.objects.filter(parent=owner).count()
    return {"children_count": n} if n else None


# --------------------------------------------------------------------------- assemble

def build(owner, viewer) -> dict:
    from .achievements import viewer_may_see
    from .privacy import is_minor_user

    is_self = owner.pk == viewer.pk
    base = {"version": LAYOUT_VERSION, "username": owner.username, "is_self": is_self, "locked": False}

    if not viewer_may_see(owner, viewer):
        return {**base, "locked": True, "profile_layout": "student", "roles": ["student"], "sections": [],
                "teacher": None, "creator": None, "campus": None, "parent": None}

    is_minor = bool(_safe("is_minor", lambda: is_minor_user(owner), False))
    teacher = _safe("teacher", lambda: teacher_block(owner))
    creator = _safe("creator", lambda: creator_block(owner, viewer, is_self=is_self))
    parent = _safe("parent", lambda: parent_block(owner)) if is_self else None
    campus = _safe("campus", lambda: campus_block(owner, viewer, is_self=is_self, is_minor=is_minor))

    creator_active = bool(creator) and (creator["reels_count"] + creator["series_count"]) >= CREATOR_MIN_ITEMS
    roles = ["student"]
    if teacher:
        roles.append("teacher")
    if creator_active:
        roles.append("creator")
    if parent:
        roles.append("parent")

    if teacher:
        layout = "teacher"
    elif parent:
        layout = "parent"
    elif creator_active:
        layout = "creator"
    else:
        layout = "student"

    sections = list(SECTIONS[layout])
    if layout == "teacher" and not (teacher or {}).get("trial"):
        sections.remove("trial_cta")
    if layout == "teacher" and not (teacher or {}).get("upcoming_live"):
        sections.remove("upcoming_live")
    if layout == "teacher" and not (teacher or {}).get("reviews"):
        sections.remove("reviews")
    if layout == "creator" and not (creator or {}).get("series_items"):
        sections.remove("series")   # nothing to open -> no empty block (the count still shows in creator_stats)
    if campus:
        sections.insert(0, "campus_chip")

    return {
        **base,
        "profile_layout": layout,
        "roles": roles,
        "sections": sections,
        "teacher": teacher,
        "creator": creator if creator_active else None,
        "campus": campus,
        "parent": parent,
    }
