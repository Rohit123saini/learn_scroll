# campus/staff_leave_push.py
"""
Notifications for staff leave. Six events, two audiences:

  requested            -> the campus's ADMINS / PRINCIPALS (not the applicant)   [push]
  approved / rejected  -> the APPLICANT                                          [push]
  cancelled_by_admin   -> the APPLICANT                                          [push]
  recorded_by_admin    -> the STAFF MEMBER an admin applied leave for            [push]
  edited               -> the ADMINS / PRINCIPALS (a pending leave was changed)  [push]
  cancelled_by_staff   -> the ADMINS / PRINCIPALS (only sent for a leave that was
                          already approved — cancelling your own pending
                          application tells nobody)                              [push]

The applicant's own `reason` and the decision note are NOT put in the push
(lock screens); the app shows them inside the leave screen.

Runs in a Celery worker (`tasks.send_staff_leave_notifications`). Cross-app
calls go through `campus.bridge` (design doc golden rule); preferences / quiet
hours / DND are honoured by the push layer.
"""
import logging

from . import bridge
from .bridge import NotifTypes
from .models import StaffLeave, StaffProfile
from .substitutes import display_name

logger = logging.getLogger(__name__)


def _span(leave) -> str:
    if leave.is_half_day:
        return f"{leave.date_from:%a %d %b} (half day)"
    if leave.date_from == leave.date_to:
        return f"{leave.date_from:%a %d %b}"
    return f"{leave.date_from:%d %b} - {leave.date_to:%d %b}"


def _payload(leave, notif_type):
    return {
        "type": notif_type,
        "leave_id": str(leave.pk),
        "campus_id": str(leave.campus_id),
        "status": leave.status,
        "date_from": leave.date_from.isoformat(),
        "date_to": leave.date_to.isoformat(),
    }


def approvers(leave, *, exclude_user_id=None):
    """Active admin / principal users of the leave's campus."""
    rows = StaffProfile.objects.filter(
        campus_id=leave.campus_id,
        is_active=True,
        role__in=(StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD),
    ).select_related("user")
    users, seen = [], set()
    for row in rows:
        if row.user_id in seen or row.user_id == exclude_user_id:
            continue
        seen.add(row.user_id)
        users.append(row.user)
    return users


def notify_leave_requested(leave) -> int:
    users = approvers(leave, exclude_user_id=leave.staff.user_id)
    name = display_name(leave.staff.user)
    bridge.notify(
        users=users,
        notif_type=NotifTypes.CAMPUS_LEAVE_REQUESTED,
        title=f"Leave request: {name}",
        body=f"{leave.get_leave_type_display()} leave · {_span(leave)} · {leave.days:g} day(s)",
        data=_payload(leave, NotifTypes.CAMPUS_LEAVE_REQUESTED),
        push=True,
    )
    return len(users)


def notify_leave_edited(leave) -> int:
    users = approvers(leave, exclude_user_id=leave.staff.user_id)
    bridge.notify(
        users=users,
        notif_type=NotifTypes.CAMPUS_LEAVE_REQUESTED,
        title=f"Leave request updated: {display_name(leave.staff.user)}",
        body=f"{leave.get_leave_type_display()} leave · {_span(leave)} · {leave.days:g} day(s)",
        data=_payload(leave, NotifTypes.CAMPUS_LEAVE_REQUESTED),
        push=True,
    )
    return len(users)


def notify_leave_decided(leave, kind) -> int:
    """`kind`: approved | rejected | cancelled_by_admin | cancelled_by_staff |
    recorded_by_admin."""
    ntype = NotifTypes.CAMPUS_LEAVE_DECIDED
    if kind == "cancelled_by_staff":
        users = approvers(leave, exclude_user_id=leave.staff.user_id)
        title = f"Leave cancelled: {display_name(leave.staff.user)}"
        body = f"{leave.get_leave_type_display()} leave · {_span(leave)}"
    else:
        users = [leave.staff.user]
        verb = {
            "approved": "approved",
            "rejected": "rejected",
            "cancelled_by_admin": "cancelled by your admin",
            "recorded_by_admin": "recorded by your admin",
        }.get(kind)
        if verb is None:
            return 0
        title = f"Your leave was {verb}"
        body = f"{leave.get_leave_type_display()} leave · {_span(leave)}"
    bridge.notify(
        users=users, notif_type=ntype, title=title, body=body, data=_payload(leave, ntype), push=True
    )
    return len(users)
