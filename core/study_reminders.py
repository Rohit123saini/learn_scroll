"""
core/study_reminders.py - S8: the student's own daily "time to study" reminder (Settings > Learning).

Kya karta hai:
  * `UserPreference.study_reminder_enabled / _time / _days` (user ka LOCAL time, NotificationPreference.timezone) par
    ek bell row (`NotifType.STUDY_REMINDER`) + push banata hai - roz ya chuni hui weekdays par.
  * Ek din me ek hi baar: `UserPreference.last_study_reminder_on` (local date) ek `.update()` se likhi jaati hai
    (`updated_at` / ETag nahi badalta, isliye Settings ke offline-queue / conflict logic ko kuch nahi dikhta).
  * Reminder time ke baad REMINDER_GRACE_MINUTES (3 ghante) tak hi - beat band raha ya user DND me tha to bhi
    purana reminder raat ko nahi aata.
  * Aaj ka daily goal pehle hi pura (`Streak.goal_completed_date`) => reminder nahi (user ne padhai kar li).
  * PUSH: wahi N1 / N5 / N6 rules (`core.tasks._push_state_for`). STUDY_REMINDER URGENT tier me hai (user ne khud
    maanga hai), isliye Focus mode / daily reminder-cap isse nahi rokte; quiet hours / DND / push-off / "type muted"
    rokte hain. Quiet hours me row `push_state=pending` rehti hai aur har run me dobara try hoti hai - bas grace
    window tak, phir `expired` (bell row phir bhi rehti hai).

Idempotent: beat har REMINDER_RUN_EVERY (15 min) chalta hai; overlap / re-run safe hai (date marker + pending state).
Kabhi raise nahi karta: ek user ka error baaki users ko nahi rokta.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


# ---------------------------------------------------------------------------------------------------------------------
# text (en / hi) - core/content_digest.py jaisa: user ki UserPreference.language se
# ---------------------------------------------------------------------------------------------------------------------
def compose(goal_minutes: int, days_to_exam, language: str = "en"):
    """-> (title, message). `days_to_exam` None ya int >= 0."""
    goal = int(goal_minutes or 10)
    if language == "hi":
        title = "पढ़ाई का समय \U0001F4DA"
        message = f"आज का {goal} मिनट का लक्ष्य अभी बाकी है - थोड़ी देर पढ़ लें।"
        if days_to_exam is not None:
            message = (f"परीक्षा में {days_to_exam} दिन बचे हैं। " if days_to_exam > 0 else "आज परीक्षा है। ") + message
    else:
        title = "Time to study \U0001F4DA"
        message = f"Your {goal}-minute goal for today is still open - take a short study session."
        if days_to_exam is not None:
            lead = f"{days_to_exam} day{'s' if days_to_exam != 1 else ''} to your exam. " if days_to_exam > 0 else "Your exam is today. "
            message = lead + message
    return title, message


def _language(pref) -> str:
    return "hi" if (getattr(pref, "language", "") or "").lower().startswith("hi") else "en"


def _days_to_exam(pref, today):
    if getattr(pref, "exam_date", None) is None:
        return None
    diff = (pref.exam_date - today).days
    return diff if diff >= 0 else None


# ---------------------------------------------------------------------------------------------------------------------
# one user
# ---------------------------------------------------------------------------------------------------------------------
def run_for_pref(pref, now, *, tz=None, notif_pref=None, streak=None) -> str:
    """Create ONE user's reminder if it is due. -> "not_due" | "goal_done" | "muted" | "created" | "failed".
    Never raises. `tz` = user's tzinfo (None = server timezone), `notif_pref` = their NotificationPreference (or None),
    `streak` = their Streak (or None); run() passes prefetched ones, a direct call may leave them out."""
    from user_profile import learning_prefs
    from user_profile.models import UserPreference

    from .models import Notification
    from .services import create_notification

    try:
        local = learning_prefs.local_now(now, tz)
        if not learning_prefs.study_reminder_due(
            pref.study_reminder_enabled, pref.study_reminder_time, pref.study_reminder_days,
            pref.last_study_reminder_on, local,
        ):
            return "not_due"

        today = local.date()
        notif_type = Notification.NotifType.STUDY_REMINDER

        def _mark_done():
            # .update(): no auto_now / no ETag change on the user's settings row
            UserPreference.objects.filter(pk=pref.pk).update(last_study_reminder_on=today)

        if streak is not None and getattr(streak, "goal_completed_date", None) == today:
            _mark_done()
            return "goal_done"
        if notif_pref is not None and notif_type.value in (notif_pref.muted_types or []):
            _mark_done()
            return "muted"  # muted type: not even a bell row (same as the study digest)
        if notif_pref is not None and not notif_pref.in_app_allowed(notif_type):
            _mark_done()
            return "muted"

        goal = int(getattr(streak, "daily_goal_minutes", None) or 10)
        title, message = compose(goal, _days_to_exam(pref, today), _language(pref))
        row = create_notification(
            pref.user_id, notif_type, title, message,
            data={
                "type": "study_reminder",
                "kind": "study_reminder",
                "goal_minutes": goal,
                "days_to_exam": _days_to_exam(pref, today),
                "push_state": "pending",
            },
        )
        _mark_done()  # row None (create_notification swallowed an error) => try again tomorrow, not every 15 min
        if row is None:
            return "failed"
        push_pending_row(row, notif_pref, now)
        return "created"
    except Exception:  # noqa: BLE001
        logger.exception("study_reminders.run_for_pref failed (pref=%s)", getattr(pref, "pk", None))
        return "failed"


def push_pending_row(row, notif_pref, now) -> str:
    """pending -> sent | skipped (push off / muted) | stays pending (quiet hours / DND). Returns the new state."""
    from .content_digest import push_pending_row as _push

    return _push(row, notif_pref, now)


# ---------------------------------------------------------------------------------------------------------------------
# retry of held-back pushes
# ---------------------------------------------------------------------------------------------------------------------
def flush_pending(now) -> dict:
    """Quiet hours / DND me ruke hue reminders: window khatam ho to push; grace se purane `expired`."""
    from user_profile import learning_prefs

    from .models import Notification, NotificationPreference

    out = {"sent": 0, "still_pending": 0, "expired": 0, "skipped": 0}
    max_age = timedelta(minutes=learning_prefs.REMINDER_GRACE_MINUTES)
    rows = list(Notification.objects.filter(
        notif_type=Notification.NotifType.STUDY_REMINDER, created_at__gte=now - timedelta(days=1),
    ))
    rows = [r for r in rows if (r.data or {}).get("push_state") == "pending"]
    if not rows:
        return out
    prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user_id__in=[r.recipient_id for r in rows])}
    for row in rows:
        try:
            if now - row.created_at > max_age:
                row.data = {**(row.data or {}), "push_state": "expired"}
                row.save(update_fields=["data"])
                out["expired"] += 1
                continue
            state = push_pending_row(row, prefs.get(row.recipient_id), now)
            out["sent" if state == "sent" else "still_pending" if state == "pending" else "skipped"] += 1
        except Exception:  # noqa: BLE001
            logger.exception("study_reminders.flush_pending failed (row=%s)", row.pk)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the run (Celery beat: core.send_study_reminders, every 15 min)
# ---------------------------------------------------------------------------------------------------------------------
def run(now=None) -> dict:
    """Held-back pushes first, then create the reminders that are due. Safe to run any number of times."""
    from user_profile import learning_prefs
    from user_profile.models import Streak, UserPreference

    from .models import NotificationPreference

    now = now or timezone.now()
    summary = {"checked": 0, "created": 0, "not_due": 0, "goal_done": 0, "muted": 0, "failed": 0}
    summary["flush"] = flush_pending(now)

    qs = (
        UserPreference.objects.filter(study_reminder_enabled=True, user__is_active=True)
        .only("id", "user_id", "language", "exam_date", "study_reminder_enabled", "study_reminder_time",
              "study_reminder_days", "last_study_reminder_on")
    )
    chunk = []
    for pref in qs.iterator(chunk_size=BATCH_SIZE):
        chunk.append(pref)
        if len(chunk) >= BATCH_SIZE:
            _run_chunk(chunk, now, summary, learning_prefs, Streak, NotificationPreference)
            chunk = []
    if chunk:
        _run_chunk(chunk, now, summary, learning_prefs, Streak, NotificationPreference)
    return summary


def _run_chunk(chunk, now, summary, learning_prefs, Streak, NotificationPreference) -> None:
    ids = [p.user_id for p in chunk]
    notif_prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user_id__in=ids)}
    streaks = {s.user_id: s for s in Streak.objects.filter(user_id__in=ids)}
    for pref in chunk:
        notif_pref = notif_prefs.get(pref.user_id)
        tz = learning_prefs.tz_from_name(getattr(notif_pref, "timezone", "") if notif_pref else "")
        result = run_for_pref(pref, now, tz=tz, notif_pref=notif_pref, streak=streaks.get(pref.user_id))
        summary["checked"] += 1
        summary[result] += 1
