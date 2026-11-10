"""
core/notification_reminders.py - N6: reminders jo user ko happy karte hain (opt-in, throttled).

Teen cheezein, sab ek jagah (NO schema change - state cache me / Notification.data me rehti hai):

  1. FREQUENCY CAP     per user per local day max N *non-urgent* reminder push (settings NOTIF_REMINDER_DAILY_CAP,
                       default 3, 0 = cap off). Sirf reminder / nudge types gate hote hain (REMINDER_TYPE_NAMES minus
                       urgent tier): streak-at-risk, weekly recap, study digest, PTM reminder. Urgent reminders
                       (class starts, test / assignment / homework due) cap se KABHI nahi rukte, na chat / social /
                       receipts / replies (woh user ki apni activity ka jawab hain, nudge nahi).
  2. SAME-TYPE COLLAPSE  ek hi type ka dusra reminder-push NOTIF_REMINDER_COLLAPSE_MINUTES (default 240) ke andar
                       nahi jaata (bell row phir bhi banti hai). iOS me collapse-id type-wise hai (message/push_utils).
  3. ACTIONS           har reminder push par "Snooze" + "Turn off this type" buttons (PUSH_ACTIONS in core/services.py
                       isi module se banta hai) aur unke endpoints ka logic: `snooze_reminder()` / `turn_off_type()`.

Kyun cache, DB column nahi: assignments / push_reliability wahi pattern use karte hain (cache.add atomic, Redis in prod);
cache flush hone ka matlab ek extra reminder, kabhi lost reminder nahi. Cap / collapse kabhi exception nahi uchhalte -
koi bhi glitch => "send" (ek reminder miss karna, duplicate se zyada bura hai).

Locked (never-mute) types ke liye Turn off / Snooze kabhi nahi: `turn_off_type()` unhe refuse karta hai.
PURE-ish: models lazily import hote hain (notification_priority jaisa), import-time par DB / Django models nahi chhute.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# Types that get the Snooze / Turn off buttons (member NAMES, resolved lazily - a renamed member just drops out).
REMINDER_TYPE_NAMES = (
    "STREAK_AT_RISK", "SESSION_REMINDER", "CAMPUS_CLASS_STARTING", "assigments_DUE_SOON", "assigments_DUE_REMINDER",
    "HOMEWORK_DIARY_DUE", "TESTSERIES_DUE_SOON", "CAMPUS_PTM_REMINDER", "WEEKLY_RECAP_READY", "CONTENT_DIGEST",
    "STUDY_REMINDER",  # S8 (urgent tier => never cap-gated, but it still gets Snooze / Turn off)
    "REVISION_DECK_REMINDER",  # P-F3_F4 (core/habit_loop.py): normal reminder => cap-gated + Snooze / Turn off
)

# The three buttons on every reminder push (Android shows max 3; a live-class reminder that gets "Join now" first loses
# "Open" automatically - Join now already opens the class, see notification_rich.finalize_actions). Ids are what the Flutter push handler switches on (never rename).
ACTION_SNOOZE = "snooze_reminder"
ACTION_TURN_OFF = "turn_off_type"
ACTION_OPEN = "open_reminder"  # R9: 3rd button - opens the row's screen (same route as a tap on the push)
REMINDER_ACTIONS = (
    {"id": ACTION_SNOOZE, "label": "Snooze"},
    {"id": ACTION_TURN_OFF, "label": "Turn off"},
    {"id": ACTION_OPEN, "label": "Open"},
)

SNOOZE_DEFAULT_MINUTES = 60
SNOOZE_MIN_MINUTES = 10
SNOOZE_MAX_MINUTES = 24 * 60
# One Celery hop never waits longer than this: Redis re-delivers tasks whose ETA is past the broker visibility timeout
# (1h default), so a long snooze is chained in short hops instead (deliver_snoozed_reminder re-schedules itself).
SNOOZE_HOP_SECONDS = 50 * 60
SNOOZE_QUIET_RETRY_MINUTES = 30
SNOOZE_QUIET_MAX_RETRIES = 6

_DAY_TTL_SECONDS = 36 * 60 * 60
_CAP_KEY = "n6:cap:{user_id}:{day}"
_COLLAPSE_KEY = "n6:collapse:{user_id}:{notif_type}"
_SNOOZE_DONE_KEY = "n6:snooze_done:{notification_id}:{token}"

REASON_CAP = "cap"
REASON_COLLAPSED = "collapsed"


# ---------------------------------------------------------------------------------------------------------------------
# config (settings are read at call time so a test / ops change needs no restart of anything but the setting itself)
# ---------------------------------------------------------------------------------------------------------------------
def daily_cap() -> int:
    try:
        return max(0, int(getattr(settings, "NOTIF_REMINDER_DAILY_CAP", 3)))
    except (TypeError, ValueError):
        return 3


def collapse_minutes() -> int:
    try:
        return max(0, int(getattr(settings, "NOTIF_REMINDER_COLLAPSE_MINUTES", 240)))
    except (TypeError, ValueError):
        return 240


# ---------------------------------------------------------------------------------------------------------------------
# which types
# ---------------------------------------------------------------------------------------------------------------------
def reminder_types() -> frozenset:
    """Every reminder NotifType value that shows Snooze / Turn off. Locked (never-mute) types are dropped: they cannot be
    turned off, so offering the button would be a lie."""
    from .models import Notification, NotificationPreference

    out = set()
    for name in REMINDER_TYPE_NAMES:
        member = getattr(Notification.NotifType, name, None)
        if member is not None:
            out.add(member.value)
    return frozenset(out) - NotificationPreference.NEVER_MUTE_TYPES


def gated_types() -> frozenset:
    """Reminder types the daily cap / collapse apply to: non-urgent only (urgent reminders always go out)."""
    from . import notification_priority as prio

    return reminder_types() - prio.urgent_types()


def is_reminder(notif_type) -> bool:
    return str(notif_type) in reminder_types()


# ---------------------------------------------------------------------------------------------------------------------
# frequency cap + same-type collapse
# ---------------------------------------------------------------------------------------------------------------------
def _local_day(pref, now) -> str:
    try:
        tz = pref._tzinfo() if pref is not None else timezone.get_current_timezone()
        return now.astimezone(tz).date().isoformat()
    except Exception:  # noqa: BLE001 - a bad zone must never stop a reminder
        return now.date().isoformat()


def _cap_key(user_id, pref, now) -> str:
    return _CAP_KEY.format(user_id=user_id, day=_local_day(pref, now))


def blocked_reason(user_id, notif_type, pref=None, now=None):
    """READ-ONLY: None (a push of this type may go out) | "collapsed" | "cap". Use it to decide without taking a slot
    (e.g. core.tasks._push_state_for). Never raises."""
    try:
        notif_type = str(notif_type)
        if notif_type not in gated_types():
            return None
        now = now or timezone.now()
        if collapse_minutes() and cache.get(_COLLAPSE_KEY.format(user_id=user_id, notif_type=notif_type)):
            return REASON_COLLAPSED
        cap = daily_cap()
        if cap and int(cache.get(_cap_key(user_id, pref, now)) or 0) >= cap:
            return REASON_CAP
    except Exception:  # noqa: BLE001
        logger.exception("notification_reminders.blocked_reason failed (user=%s type=%s)", user_id, notif_type)
    return None


def claim_slot(user_id, notif_type, pref=None, now=None):
    """Take a cap slot + the same-type collapse window for ONE push that is about to be sent. Returns None (go ahead)
    or "collapsed" / "cap" (do NOT push; the bell row stays). Non-gated types always return None and cost nothing.
    Atomic enough for Redis (cache.add / incr); on any error => None (fail open)."""
    try:
        notif_type = str(notif_type)
        if notif_type not in gated_types():
            return None
        now = now or timezone.now()
        collapse_key = _COLLAPSE_KEY.format(user_id=user_id, notif_type=notif_type)
        minutes = collapse_minutes()
        if minutes and cache.get(collapse_key):
            return REASON_COLLAPSED

        cap = daily_cap()
        cap_key = _cap_key(user_id, pref, now)
        took_cap = False
        if cap:
            cache.add(cap_key, 0, timeout=_DAY_TTL_SECONDS)
            try:
                used = cache.incr(cap_key)
            except ValueError:  # key evicted between add and incr
                cache.set(cap_key, 1, timeout=_DAY_TTL_SECONDS)
                used = 1
            if used > cap:
                _decr(cap_key)
                return REASON_CAP
            took_cap = True

        if minutes and not cache.add(collapse_key, 1, timeout=minutes * 60):
            if took_cap:
                _decr(cap_key)  # lost the race to a same-type push: give the slot back
            return REASON_COLLAPSED
    except Exception:  # noqa: BLE001
        logger.exception("notification_reminders.claim_slot failed (user=%s type=%s)", user_id, notif_type)
    return None


def _decr(key) -> None:
    try:
        cache.decr(key)
    except Exception:  # noqa: BLE001
        pass


def reminders_pushed_today(user_id, pref=None, now=None) -> int:
    """How many gated reminder pushes this user already got today (for the settings hint / tests)."""
    try:
        return int(cache.get(_cap_key(user_id, pref, now or timezone.now())) or 0)
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------------------------------------------------
# Turn off this type  (+ undo)
# ---------------------------------------------------------------------------------------------------------------------
class ReminderActionError(ValueError):
    """`code` is stable for the API / client: not_a_reminder | locked | bad_minutes | missing."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def turn_off_type(user, notif_type) -> dict:
    """"Turn off this type": switch the PUSH chip off for `notif_type` (N1 channel_overrides). The bell row keeps
    coming (in-app untouched) and email / sms are not touched either - exactly what the per-type matrix would do.
    Locked types are refused. Idempotent."""
    from .models import NotificationPreference

    notif_type = str(notif_type)
    if notif_type in NotificationPreference.NEVER_MUTE_TYPES:
        raise ReminderActionError("locked", "This notification cannot be turned off.")
    pref = NotificationPreference.for_user(user)
    overrides = dict(pref.channel_overrides or {})
    entry = dict(overrides.get(notif_type) or {}) if isinstance(overrides.get(notif_type), dict) else {}
    entry["push"] = False
    overrides[notif_type] = entry
    pref.channel_overrides = overrides
    pref.save(update_fields=["channel_overrides", "updated_at"])
    return {"notif_type": notif_type, "push": False}


def turn_on_type(user, notif_type) -> dict:
    """Undo of turn_off_type(): drop the push=false deviation (an override stores only deviations from "on")."""
    from .models import NotificationPreference

    notif_type = str(notif_type)
    pref = NotificationPreference.for_user(user)
    overrides = dict(pref.channel_overrides or {})
    entry = overrides.get(notif_type)
    if isinstance(entry, dict) and "push" in entry:
        entry = {k: v for k, v in entry.items() if k != "push"}
        if entry:
            overrides[notif_type] = entry
        else:
            overrides.pop(notif_type, None)
        pref.channel_overrides = overrides
        pref.save(update_fields=["channel_overrides", "updated_at"])
    return {"notif_type": notif_type, "push": True}


# ---------------------------------------------------------------------------------------------------------------------
# Snooze
# ---------------------------------------------------------------------------------------------------------------------
def clamp_snooze_minutes(minutes) -> int:
    if minutes in (None, ""):
        return SNOOZE_DEFAULT_MINUTES
    if isinstance(minutes, bool):
        raise ReminderActionError("bad_minutes", "minutes must be a number.")
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        raise ReminderActionError("bad_minutes", "minutes must be a number.")
    if value < SNOOZE_MIN_MINUTES or value > SNOOZE_MAX_MINUTES:
        raise ReminderActionError(
            "bad_minutes", f"minutes must be between {SNOOZE_MIN_MINUTES} and {SNOOZE_MAX_MINUTES}."
        )
    return value


def snooze_reminder(row, minutes=None, now=None) -> dict:
    """"Remind me again in N minutes" for ONE bell row: writes snooze_* keys into row.data and schedules the delivery
    (core.deliver_snoozed_reminder, chained in <=50 min hops). A second snooze of the same row supersedes the first (new
    token), turning the type off cancels it at delivery time. Returns {"snooze_until", "minutes"}."""
    if not is_reminder(row.notif_type):
        raise ReminderActionError("not_a_reminder", "This notification cannot be snoozed.")
    minutes = clamp_snooze_minutes(minutes)
    now = now or timezone.now()
    until = now + timedelta(minutes=minutes)
    token = uuid.uuid4().hex[:12]
    row.data = {
        **(row.data if isinstance(row.data, dict) else {}),
        "snooze_until": until.isoformat(),
        "snooze_token": token,
        "snooze_state": "pending",
        "snooze_retries": 0,
    }
    row.save(update_fields=["data"])
    _schedule_delivery(row.id, token, until, now)
    return {"snooze_until": until.isoformat(), "minutes": minutes}


def unsnooze_reminder(row) -> dict:
    """R9 - undo of snooze_reminder(): the pending snooze of THIS row is cancelled (state "cancelled", `snooze_until`
    removed), so the Celery hop that is still queued finds `snooze_state != pending` and returns "superseded" - no second
    push. Idempotent: nothing pending -> {"cancelled": False}. Only reminder types, same as snooze."""
    if not is_reminder(row.notif_type):
        raise ReminderActionError("not_a_reminder", "This notification cannot be snoozed.")
    data = dict(row.data) if isinstance(row.data, dict) else {}
    if data.get("snooze_state") != "pending":
        return {"cancelled": False}
    data.pop("snooze_until", None)
    data["snooze_state"] = "cancelled"
    row.data = data
    row.save(update_fields=["data"])
    return {"cancelled": True}


def _schedule_delivery(notification_id, token, until, now=None) -> bool:
    from .async_utils import dispatch_after_commit
    from .tasks import deliver_snoozed_reminder

    now = now or timezone.now()
    wait = max(0, int((until - now).total_seconds()))
    countdown = min(wait, SNOOZE_HOP_SECONDS)
    try:
        # dispatch_after_commit has no countdown arg, so schedule directly (a failure falls back to "not delivered").
        deliver_snoozed_reminder.apply_async(
            args=[notification_id, token, until.isoformat()], countdown=countdown,
            retry=True, retry_policy={"max_retries": 1, "interval_start": 0, "interval_step": 0.2, "interval_max": 0.5},
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception("could not schedule snoozed reminder %s", notification_id)
        return False


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=dt_timezone.utc)


def still_relevant(row, now=None) -> bool:
    """Should a snoozed reminder still buzz? Types whose goal can be reached meanwhile say no:
    streak-at-risk once the user was active today, test-closing once the attempt is submitted / the window is over."""
    from .models import Notification

    now = now or timezone.now()
    t = row.notif_type
    data = row.data if isinstance(row.data, dict) else {}
    try:
        if t == Notification.NotifType.STREAK_AT_RISK:
            from user_profile.models import Streak

            today = timezone.localdate()
            last = Streak.objects.filter(user_id=row.recipient_id).values_list("last_active_date", flat=True).first()
            return not (last is not None and last >= today)
        if t == Notification.NotifType.TESTSERIES_DUE_SOON:
            from testseries.models import TestAttempt

            ends = _parse_dt(data.get("ends_at"))
            if ends is not None and ends <= now:
                return False
            attempt_id = data.get("attempt_id")
            if attempt_id:
                return TestAttempt.objects.filter(
                    pk=attempt_id, student_id=row.recipient_id, status=TestAttempt.Status.IN_PROGRESS,
                ).exists()
    except Exception:  # noqa: BLE001 - when unsure, deliver
        logger.exception("still_relevant check failed for notification %s", getattr(row, "id", None))
    return True


def deliver_snoozed(notification_id, token, until_iso, now=None) -> str:
    """Body of the Celery task. Returns a short outcome word (also what the tests assert):
    gone | superseded | waiting | not_relevant | duplicate | dropped | deferred | sent."""
    from .models import Notification, NotificationPreference
    from .tasks import _push_state_for

    now = now or timezone.now()
    row = Notification.objects.filter(pk=notification_id).first()
    if row is None:
        return "gone"
    data = row.data if isinstance(row.data, dict) else {}
    if data.get("snooze_token") != token or data.get("snooze_state") != "pending":
        return "superseded"

    until = _parse_dt(until_iso) or now
    if until - now > timedelta(seconds=30):  # long snooze: hop again
        _schedule_delivery(row.id, token, until, now)
        return "waiting"

    def _finish(state):
        row.data = {**data, "snooze_state": state}
        row.save(update_fields=["data"])

    if row.archived_at is not None or not still_relevant(row, now):
        _finish("skipped")
        return "not_relevant"

    pref = NotificationPreference.objects.filter(user_id=row.recipient_id).first()
    state = _push_state_for(pref, row.notif_type, now, ignore_reminder_cap=True)  # the user asked: cap does not apply
    if state == "skip":
        _finish("skipped")  # muted / push off / focus mode: the user said no, not our call to override
        return "dropped"
    if state == "defer":  # quiet hours / DND right now: try again after the window (bounded)
        retries = int(data.get("snooze_retries") or 0)
        if retries >= SNOOZE_QUIET_MAX_RETRIES:
            _finish("expired")
            return "dropped"
        row.data = {**data, "snooze_retries": retries + 1}
        row.save(update_fields=["data"])
        _schedule_delivery(row.id, token, now + timedelta(minutes=SNOOZE_QUIET_RETRY_MINUTES), now)
        return "deferred"

    if not cache.add(_SNOOZE_DONE_KEY.format(notification_id=row.id, token=token), 1, timeout=_DAY_TTL_SECONDS):
        return "duplicate"  # an earlier hop already delivered it (redis redelivery)
    from message.push_utils import send_push_for_notification

    send_push_for_notification(row, extra_data={"snoozed": "1"}, reminder_gate=False)  # the user asked for this one
    _finish("sent")
    return "sent"


# ---------------------------------------------------------------------------------------------------------------------
# push-side helpers (used by core/services.py + message/push_utils.py)
# ---------------------------------------------------------------------------------------------------------------------
def action_specs_for(notif_type) -> tuple:
    """Action buttons for a reminder push (empty for everything else)."""
    return REMINDER_ACTIONS if is_reminder(notif_type) else ()
