"""
leaderboard/permissions.py

Scope-level "may this user even see this board" checks. Kept as plain
functions (not DRF `BasePermission` classes) so `views.py` can call the
right one depending on `scope_type` — same reasoning `campus/permissions.py`
gives for its own helper-function style.
"""


def can_view_test_series_board(user, series_id) -> bool:
    """A test series' leaderboard is social proof for anyone deciding
    whether to take it — same spirit as Task G20's public star ratings —
    so any authenticated user can view it; no purchase/attempt required."""
    return bool(user and getattr(user, "is_authenticated", False))


def can_view_campus_section_board(user, section_id) -> bool:
    """Attendance/academic standing is sensitive: only people with a real
    stake in that section may see it — an active student enrolled there,
    campus staff, or a verified parent of a student in that section."""
    if not user or not getattr(user, "is_authenticated", False):
        return False

    from campus.models import CampusParentLink, StaffProfile, StudentEnrollment

    if StudentEnrollment.objects.filter(
        section_id=section_id, student=user, status=StudentEnrollment.Status.ACTIVE
    ).exists():
        return True

    section_campus_id = (
        StudentEnrollment.objects.filter(section_id=section_id)
        .values_list("section__school_class__campus_id", flat=True).first()
    )
    if section_campus_id and StaffProfile.objects.filter(
        campus_id=section_campus_id, user=user, is_active=True
    ).exists():
        return True

    student_ids_in_section = StudentEnrollment.objects.filter(
        section_id=section_id, status=StudentEnrollment.Status.ACTIVE
    ).values_list("student_id", flat=True)
    return CampusParentLink.objects.filter(parent=user, student_id__in=student_ids_in_section).exists()


def can_view_engagement_board(user) -> bool:
    """App-wide engagement is a growth/social-proof feature, same as any
    other public leaderboard — open to any authenticated user."""
    return bool(user and getattr(user, "is_authenticated", False))


def can_view_study_time_board(user, scope_id) -> bool:
    """S5 — study-room time board.

    * `scope_id` (Conversation.id): sirf us room/conversation ke ACTIVE members (left_at null).
    * `scope_id=None` (app-wide): settings.STUDY_TIME_GLOBAL_BOARD_ENABLED (default False) — bachon ka padhai-time sabko
      dikhane se pehle product/privacy decision; ON hone par koi bhi authenticated user."""
    if not (user and getattr(user, "is_authenticated", False)):
        return False
    if scope_id is None:
        from django.conf import settings

        return bool(getattr(settings, "STUDY_TIME_GLOBAL_BOARD_ENABLED", False))

    from message.models import ConversationParticipant

    return ConversationParticipant.objects.filter(
        conversation_id=scope_id, user=user, left_at__isnull=True,
    ).exists()
