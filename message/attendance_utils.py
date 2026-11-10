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

S5 — streak ab "kam se kam N minute" se: ek din tabhi ginta hai jab `StudyRoomAttendance.total_seconds` (LiveKit
webhook se, `study_room_presence`) >= STUDY_ROOM_STREAK_MIN_MINUTES (default 10) ho. Purani (pre-S5) rows aur
STUDY_ROOM_PRESENCE_ENABLED=False me join hi kaafi hai (`study_room_presence.qualifies`). Abhi room me chal rahe
interval ka aaj ka hissa bhi jodte hain, taaki streak leave ka wait na kare.
"""
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from .models import StudyRoomAttendance


def _stats_from_sorted_dates(dates):
    """
    `dates`: this user's QUALIFYING `attended_date` values for ONE conversation,
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


def _build_stats(rows, conversation_ids, user):
    """
    `rows`: iterable of (conversation_id, attended_date, total_seconds, presence_tracked), conversation ke andar
    date DESC. Single + bulk dono yahin se jaate hain.

    S5 extra keys (purane keys jaisi hi rehti hain, client unhe ignore kar sakta hai):
      today_minutes / total_study_minutes — webhook se jama minutes (+ abhi room me chal raha interval),
      streak_min_minutes                  — din ginne ke liye zaroori minimum (0 = join kaafi),
      today_counted                       — aaj ka din abhi streak me ginta hai ya nahi.
    """
    from . import study_room_presence as presence

    today = timezone.localdate()
    live_today = presence.open_seconds_today(conversation_ids, user.id)

    qualifying = defaultdict(list)
    total_seconds = defaultdict(int)
    today_seconds = defaultdict(int)
    today_counted = defaultdict(bool)
    for conversation_id, attended_date, seconds, tracked in rows:
        seconds = int(seconds or 0)
        live = live_today.get(conversation_id, 0) if attended_date == today else 0
        total_seconds[conversation_id] += seconds + live
        if attended_date == today:
            today_seconds[conversation_id] += seconds + live
        if presence.qualifies(seconds, tracked, live):
            qualifying[conversation_id].append(attended_date)
            if attended_date == today:
                today_counted[conversation_id] = True

    min_minutes = presence.streak_min_minutes()
    out = {}
    for conversation_id in conversation_ids:
        stats = _stats_from_sorted_dates(qualifying.get(conversation_id, []))
        stats["today_minutes"] = today_seconds.get(conversation_id, 0) // 60
        stats["total_study_minutes"] = total_seconds.get(conversation_id, 0) // 60
        stats["streak_min_minutes"] = min_minutes
        stats["today_counted"] = bool(today_counted.get(conversation_id, False))
        out[conversation_id] = stats
    return out


def _fetch_rows(conversation_ids, user):
    return list(
        StudyRoomAttendance.objects.filter(conversation_id__in=conversation_ids, user=user)
        .order_by('conversation_id', '-attended_date')
        .values_list('conversation_id', 'attended_date', 'total_seconds', 'presence_tracked')
    )


def compute_attendance_stats(conversation, user):
    """
    Returns:
        {
            "current_streak": int,
            "longest_streak": int,
            "total_classes_attended": int,      # sirf wo din jo streak ke liye qualify karte hain (S5)
            "last_attended": "YYYY-MM-DD" | None,
            "today_minutes": int,               # S5
            "total_study_minutes": int,         # S5
            "streak_min_minutes": int,          # S5
            "today_counted": bool,              # S5
        }
    """
    conversation_id = conversation.id
    return _build_stats(_fetch_rows([conversation_id], user), [conversation_id], user)[conversation_id]


# ======================================================================
# 🔧 GAP FIX (N+1) — `ParentDashboardView` pehle har classroom ke liye
# alag se `compute_attendance_stats(conversation, student)` call karta
# tha (1 query PER classroom, N classrooms = N queries). Ye function EK
# hi query me saari `conversation_ids` ke liye rows le aata hai, phir
# Python me per-conversation group karke bilkul wahi `_build_stats()`
# helper use karta hai jo single-conversation path use karta
# hai — matlab dono function ka result HAMESHA identical rahega (same
# ek jagah se derive hota hai), bas is variant me DB round-trips
# conversation-count se independent (fixed ~2 query) ho jaate hain.
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
    return _build_stats(_fetch_rows(conversation_ids, user), conversation_ids, user)
