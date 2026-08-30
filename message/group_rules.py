# message/group_rules.py
#
# 🔥 NAYA — Group access-control enforcement. Ye file pehle 0 bytes thi
# (khaali stub) — `group_profile_screen.dart` already `message_permission`,
# `call_permission`, `study_room_permission`, `daily_message_limit` settings
# ke liye UI bhej raha tha, par backend me na field the na koi enforcement.
#
# Sab jagah (REST message send, WS message send, call initiate, study-room
# join) yahi 2 functions reuse hote hain taaki rule ek jagah rahe.
#
# 🔥 FIX (perf) — `is_group_admin_or_mod` sabse zyada-hit query hai in is
# poori app me (har pin/message/call/study-room/member-management action
# pe chalta hai). Ab `cache_utils.get_group_role_cached` ke peeche se guzarta
# hai (60s TTL + write-time invalidation) — role change hote hi caller ko
# `invalidate_group_role_cache(group_id, user_id)` bhi call karna hoga
# (see cache_utils.py docstring for call-sites).

from django.utils import timezone

from .cache_utils import get_group_role_cached


def is_group_admin_or_mod(group, user_id) -> bool:
    member = get_group_role_cached(group.id, user_id)
    if member is None:
        return False
    return member["role"] in ('admin', 'moderator') and not member["is_banned"]


def check_group_permission(group, user_id, permission_field: str) -> tuple[bool, str]:
    """
    `permission_field`: 'message_permission' | 'call_permission' | 'study_room_permission'
    Returns (allowed: bool, reason: str). Private group me admin/mod ban rules
    khud already `IsGroupAdminOrModerator` se cover hote hain, ye sirf
    ADMINS_ONLY-type restriction ke liye hai.
    """
    value = getattr(group, permission_field)
    if value == group.PermissionLevel.EVERYONE:
        return True, ""
    if is_group_admin_or_mod(group, user_id):
        return True, ""
    return False, "Ye action sirf group admin/moderator kar sakte hain."


def check_daily_message_limit(group, user, conversation) -> tuple[bool, str]:
    """
    Admin/moderator limit se exempt hain. Limit None/blank matlab koi limit
    nahi. Simple day-boundary count query — extra table ki zaroorat nahi,
    volume normal chat app ke liye kaafi chhota hai.
    """
    if not group.daily_message_limit:
        return True, ""
    if is_group_admin_or_mod(group, user.id):
        return True, ""

    from .models import Message  # local import — circular import se bachne ke liye

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = Message.objects.filter(
        conversation=conversation, sender=user, created_at__gte=today_start,
    ).count()

    if sent_today >= group.daily_message_limit:
        return False, f"Aaj ka {group.daily_message_limit} message ka limit khatam ho gaya."
    return True, ""