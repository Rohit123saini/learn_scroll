"""
tuitionclass/live_push.py  -  X12: bell row + push that RESPECTS N9 (quiet hours / Do Not Disturb)

Every notification the live-safety feature sends (a report reached the moderators, you were warned / muted / banned,
the recording of a class is ready) goes through notify() below. What it does, in this order:

    1. BELL ROW for every recipient (core.services.create_bulk_notifications) - always, even in quiet hours: the user
       finds it in the app when they look. (Same contract as everything else in core: quiet hours silence the BUZZ, not
       the history.)
    2. PUSH only to users whose NotificationPreference allows it RIGHT NOW. Same rule as core/tasks.py::_push_state_for
       and NotificationPreference.allowed_channels_for:
            type muted, or push switched off      -> no push
            inside quiet hours / DND window       -> no push (unless the type is in QUIET_BYPASS_TYPES)
            otherwise                             -> push
       One query for the whole audience (the old per-user get_or_create in notify_session_live is N queries).
       Users without a preference row have the defaults: push on, no quiet hours.
    3. A push that is held back by quiet hours is NOT queued for later. These alerts go stale (a report about a class
       that ended, a mute that already expired); the bell row is the record. Counted in the return value (`quiet`) so a
       test / log can see it.

None of these types bypass quiet hours: a moderation alert is important, but not a call or an emergency. (An actual
child-safety escalation goes to platform staff through user_profile.ContentReport, which has its own urgent queue.)

central_push_allowed() is the same check for ONE user; notifications._send_push() calls it so the older tuitionclass
tasks that push through send_notification(channel="push") (waitlist, pass refund, notices, ...) also stop buzzing during
quiet hours. Kill switch: settings.TUITIONCLASS_PUSH_RESPECT_QUIET_HOURS = False.

Never raises.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# data["type"] strings the client switches on for the notifications of this feature.
TYPE_LIVE_REPORT = "live_report"
TYPE_LIVE_WARNING = "live_warning"
TYPE_LIVE_CHAT_MUTED = "live_chat_muted"
TYPE_LIVE_BANNED = "live_banned"
TYPE_RECORDING_READY = "recording_ready"


def _notif_types():
    from core.models import Notification

    return Notification.NotifType


def split_push_audience(user_ids, notif_type, classroom_id=None):
    """-> (push_ids, quiet_ids, off_ids) for a push of `notif_type`.
    quiet = would have been pushed but is inside quiet hours / DND; off = muted type or push switched off.
    N9: `classroom_id` given -> users who muted THAT class (core.NotificationScopeMute) land in `off` (bell row stays)."""
    from django.utils import timezone

    from core import notification_center
    from core.models import NotificationPreference

    ids = [int(u) for u in dict.fromkeys(user_ids) if u is not None]
    class_muted = set()
    if classroom_id is not None and ids:
        class_muted = set(ids) - set(
            notification_center.filter_unmuted_users(ids, notification_center.SCOPE_CLASS, classroom_id, notif_type)
        )
    prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user_id__in=ids)}
    NotificationPreference.prime_focus(prefs.values())  # N5: ONE query for everybody's focus / exam mode
    now = timezone.now()
    push, quiet, off = [], [], []
    for uid in ids:
        pref = prefs.get(uid)
        if uid in class_muted:                # N9: this class was muted by the user
            off.append(uid)
        elif pref is None:                    # brand-new user: defaults (push on, no quiet hours)
            push.append(uid)
        elif not pref.channel_allowed(notif_type, "push"):  # N1: master + muted + per-type override
            off.append(uid)
        elif pref.push_block_reason(notif_type, now) in ("focus", "low"):  # N5: dropped (bell row stays)
            off.append(uid)
        elif pref.push_block_reason(notif_type, now):  # dnd / quiet hours: held back
            quiet.append(uid)
        else:
            push.append(uid)
    return push, quiet, off


def notify(user_ids, title, message, *, push_type, notif_type=None, session=None, classroom=None, data=None) -> dict:
    """Bell row for everybody + push for the users N9 allows. `push_type` goes into data["type"] (what the app routes
    on). Returns {"bell": n, "pushed": n, "quiet": n, "off": n}. Never raises."""
    result = {"bell": 0, "pushed": 0, "quiet": 0, "off": 0}
    try:
        ids = [u for u in dict.fromkeys(int(x) for x in user_ids if x is not None)]
        if not ids:
            return result
        types = _notif_types()
        notif_type = notif_type or types.GENERIC
        payload = {"type": push_type}
        if session is not None:
            payload.update({"session_id": str(session.id), "live_session_id": str(session.id)})
            classroom = classroom or session.classroom
        if classroom is not None:
            payload["classroom_id"] = str(classroom.id)
        payload.update({k: str(v) for k, v in (data or {}).items()})

        from core.services import create_bulk_notifications

        try:
            create_bulk_notifications(ids, notif_type, title, message, classroom=classroom, session=session, data=payload)
            result["bell"] = len(ids)
        except Exception:
            logger.exception("live_push: bell rows failed (%s)", push_type)

        push_ids, quiet_ids, off_ids = split_push_audience(
            ids, notif_type, classroom_id=getattr(classroom, "id", None),  # N9: class muted by the user -> no push
        )
        result["quiet"], result["off"] = len(quiet_ids), len(off_ids)
        if push_ids:
            try:
                from message.push_utils import send_push_to_users

                send_push_to_users(push_ids, title, message, data=payload)
                result["pushed"] = len(push_ids)
            except Exception:
                logger.exception("live_push: push failed (%s, %d users)", push_type, len(push_ids))
    except Exception:
        logger.exception("live_push.notify failed (%s)", push_type)
    return result


# ---------------------------------------------------------------------------
# the notifications of this feature
# ---------------------------------------------------------------------------
def moderator_ids(session, *, exclude=()):
    """Teacher + every staff row of the classroom that may moderate a session (co-teacher / moderator; on an
    ORGANISATION classroom every staff row, same rule as views/access.py::_can_moderate_session)."""
    from .models import Classroom, ClassroomStaff

    classroom = session.classroom
    ids = {classroom.teacher_id}
    staff = ClassroomStaff.objects.filter(classroom=classroom)
    if classroom.classroom_type != Classroom.ClassroomType.ORGANISATION:
        staff = staff.filter(role__in=[ClassroomStaff.Role.CO_TEACHER, ClassroomStaff.Role.MODERATOR])
    ids.update(staff.values_list("user_id", flat=True))
    ids.discard(None)
    ids.difference_update(int(x) for x in exclude if x is not None)
    return sorted(ids)


def notify_moderators_of_report(session, event, *, exclude=()) -> dict:
    """A participant filed a report in this live class -> teacher + moderators (never the reporter or the reported
    person). The text never says WHO reported."""
    title = "New report in your live class"
    message = f"A participant reported something in \"{session.classroom.title}\". Open the moderation panel to review it."
    return notify(
        moderator_ids(session, exclude=exclude), title, message,
        push_type=TYPE_LIVE_REPORT, session=session, data={"event_id": event.id},
    )


def notify_user_of_action(session, user_id, action, *, minutes=None) -> dict:
    """Tell the person a moderator acted on them (warn / chat_mute / ban). Kick needs no message: they are already out."""
    room = session.classroom.title
    if action == "warn":
        push_type, title = TYPE_LIVE_WARNING, "Warning from your teacher"
        message = f"Please follow the class rules in \"{room}\"."
    elif action == "chat_mute":
        push_type, title = TYPE_LIVE_CHAT_MUTED, "Chat muted"
        message = f"A moderator muted your chat in \"{room}\"" + (f" for {minutes} min." if minutes else ".")
    elif action == "ban":
        push_type, title = TYPE_LIVE_BANNED, "Removed from classroom"
        message = f"You were removed from \"{room}\" by the teacher."
    else:
        return {"bell": 0, "pushed": 0, "quiet": 0, "off": 0}
    return notify([user_id], title, message, push_type=push_type, session=session)


def recording_ready_audience(session):
    """People who were IN the class (not the host tier) - the ones who may want the replay. Capped by
    settings.LIVE_NOTIFY_MAX_AUDIENCE (same knob as the live-now push)."""
    from .models import SessionParticipant

    cap = int(getattr(settings, "LIVE_NOTIFY_MAX_AUDIENCE", 5000))
    hosts = set(moderator_ids(session))
    ids = (
        SessionParticipant.objects.filter(session=session, role=SessionParticipant.Role.STUDENT, is_trial=False)
        .values_list("user_id", flat=True).distinct()
    )
    out = []
    for uid in ids:
        if uid in hosts:
            continue
        out.append(uid)
        if len(out) >= cap:
            break
    return out


def notify_recording_ready(session) -> dict:
    """The class recording finished processing and is playable. Bell + N9-aware push to the students who attended."""
    audience = recording_ready_audience(session)
    title = "Class recording is ready"
    message = f"You can watch the recording of \"{session.classroom.title}\" now."
    return notify(audience, title, message, push_type=TYPE_RECORDING_READY, session=session)


# ---------------------------------------------------------------------------
# the central gate used by notifications._send_push
# ---------------------------------------------------------------------------
def central_push_allowed(user, data=None) -> bool:
    """True when a push to `user` may buzz right now. The notification type is data["type"] when it is a NotifType
    value, else GENERIC. Fails OPEN on a lookup error (a broken preference table must not silence every push; the
    per-feature notify() above fails closed only because it also owns the bell row)."""
    if not bool(getattr(settings, "TUITIONCLASS_PUSH_RESPECT_QUIET_HOURS", True)):
        return True
    try:
        types = _notif_types()
        raw = str((data or {}).get("type") or "")
        notif_type = raw if raw in types.values else types.GENERIC
        push, _quiet, _off = split_push_audience([user.id], notif_type)
        return bool(push)
    except Exception:
        logger.exception("central_push_allowed failed for user %s - allowing the push", getattr(user, "id", None))
        return True
