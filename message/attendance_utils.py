# message/attendance_utils.py
"""
Single source of truth for "attendance streak/stats" calculation.

Used by both `StudyRoomStreakView` (student's own view, views.py) and
`ParentDashboardView` (parent read-only view, views_parent.py) — before
Parent Mode, this logic lived only inline inside `StudyRoomStreakView.get()`.
Copy-pasting it a second time for the parent dashboard would recreate
exactly the bug pattern this codebase already fixed once before (see
`permissions.py`'s `IsGroupAdminOrModerator` docstring: 4 independent
copies of the same "admin/mod, not banned" rule drifting out of sync) —
so it's pulled out here instead, and both views call this one function.
"""
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from .models import StudyRoomAttendance


def _stats_from_sorted_dates(dates):
    """
    `dates`: this user's `attended_date` values for ONE conversation,
    already distinct and sorted descending (newest first). Shared by
    both `compute_attendance_stats` and the bulk variant below so the
    two can never drift out of sync with each other.
    """
    if not dates:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "total_classes_attended": 0,
            "last_attended": None,
        }

    today = timezone.localdate()
    date_set = set(dates)

    # current streak: walk backward from today (or yesterday, if today's
    # class hasn't happened/been joined yet)
    current_streak = 0
    cursor = today if today in date_set else today - timedelta(days=1)
    while cursor in date_set:
        current_streak += 1
        cursor -= timedelta(days=1)

    # longest streak ever: walk the full sorted date list once
    longest_streak = 1
    running = 1
    for i in range(1, len(dates)):
        if (dates[i - 1] - dates[i]).days == 1:
            running += 1
            longest_streak = max(longest_streak, running)
        else:
            running = 1

    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "total_classes_attended": len(dates),
        "last_attended": dates[0].isoformat(),
    }


def compute_attendance_stats(conversation, user):
    """
    Returns:
        {
            "current_streak": int,
            "longest_streak": int,
            "total_classes_attended": int,
            "last_attended": "YYYY-MM-DD" | None,
        }
    """
    dates = list(
        StudyRoomAttendance.objects.filter(conversation=conversation, user=user)
        .order_by('-attended_date')
        .values_list('attended_date', flat=True)
        .distinct()
    )
    return _stats_from_sorted_dates(dates)


# ======================================================================
# 🔧 GAP FIX (N+1) — `ParentDashboardView` pehle har classroom ke liye
# alag se `compute_attendance_stats(conversation, student)` call karta
# tha (1 query PER classroom, N classrooms = N queries). Ye function EK
# hi query me saari `conversation_ids` ke liye rows le aata hai, phir
# Python me per-conversation group karke bilkul wahi `_stats_from_sorted_
# dates()` helper use karta hai jo single-conversation path use karta
# hai — matlab dono function ka result HAMESHA identical rahega (same
# ek jagah se derive hota hai), bas is variant me DB round-trips
# conversation-count se independent (fixed ~1 query) ho jaate hain.
# ======================================================================
def compute_attendance_stats_bulk(conversation_ids, user):
    """
    `conversation_ids`: iterable of conversation IDs to compute stats for.

    Returns: {conversation_id: <same dict shape as compute_attendance_stats>}
    Har `conversation_id` guaranteed key ke saath aata hai (all-zero dict
    agar us classroom me kabhi attendance nahi hui) — caller ko missing-
    key handling nahi karni padti.
    """
    conversation_ids = list(conversation_ids)
    if not conversation_ids:
        return {}

    rows = (
        StudyRoomAttendance.objects.filter(
            conversation_id__in=conversation_ids, user=user,
        )
        .order_by('conversation_id', '-attended_date')
        .values_list('conversation_id', 'attended_date')
        .distinct()
    )

    dates_by_conversation = defaultdict(list)
    for conversation_id, attended_date in rows:
        dates_by_conversation[conversation_id].append(attended_date)

    return {
        conversation_id: _stats_from_sorted_dates(dates_by_conversation.get(conversation_id, []))
        for conversation_id in conversation_ids
    }