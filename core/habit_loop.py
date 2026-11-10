"""
core/habit_loop.py - P-F3_F4: close the habit loop (streak / daily goal / revision deck / weekly report) across
push + in-app + e-mail, without any new table.

The loop, in the order a student meets it during a day:

    in-app      Home habit card (`today_snapshot`, also inside GET /core/continue-learning/ as "habit" and on its own at
                GET /core/habit/today/): streak, today's goal progress, revision cards waiting, last week's report.
    afternoon   REVISION_DECK_REMINDER push (`run_revision_reminders`)  - only if the user is NOT active yet today and
                has not already been nudged today.
    evening     STREAK_AT_RISK push (core.tasks.send_streak_at_risk_reminders) - at the user's OWN usual hour
                (`nudge_hour`, SMART TIMING), not one fixed server hour.
    Sunday      WEEKLY_RECAP_READY bell row + push (user_profile.tasks.generate_weekly_recaps) and, if the user allowed
                e-mail, ONE weekly report e-mail (`send_weekly_report_email`).

Rules that keep it from nagging (all enforced HERE or by the existing N1 / N5 / N6 gate `core.tasks._push_state_for`):
  * N1  per-type toggle / master push switch / muted type           -> no push (the bell row still exists unless muted)
  * N5  quiet hours / DND (user's own timezone) -> the push WAITS (row stays `push_state=pending`, retried each run, up
        to a grace window); Focus / exam mode drops non-urgent nudges.
  * N6  daily cap of non-urgent reminder pushes (NOTIF_REMINDER_DAILY_CAP) + same-type collapse window.
  * P-F3_F4  ONE habit nudge slot per user per day: a revision reminder is skipped on a day the user already got a study
        reminder / streak warning, and is never sent to someone who already studied today. The streak warning is never
        blocked by the slot (losing a streak is the expensive miss) but it takes the slot. The study reminder the user
        set up themselves (S8) is exempt: they asked for it.
  * Revision reminder cadence: at most one per `revision_every_days` (default 3) - it is a nudge, not a daily chore.

Smart timing = `typical_hours()`: the median local hour of the user's LAST heartbeat of each of the last 14 days
(`DailyUsage.last_heartbeat_at`, i.e. when they usually finish studying). The streak warning goes out `lead_hours` (1)
before that hour, clamped to [earliest_hour (18), latest_hour (22)]; fewer than `min_samples` (3) days of history ->
the old fixed `settings.STREAK_REMINDER_HOUR`. The streak "day" is the SERVER day (that is what
Streak.record_activity uses), so smart hours are computed in the server timezone too.

Config: `settings.HABIT_LOOP = {...}` overrides any key of DEFAULTS. Nothing here raises into a caller: every public
function degrades to "do nothing / None" and logs.
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULTS = {
    "smart_timing": True,          # False = every user gets settings.STREAK_REMINDER_HOUR
    "history_days": 14,
    "min_samples": 3,              # days of history needed before a personal hour is trusted
    "lead_hours": 1,               # nudge this long before the user's usual last-active hour
    "earliest_hour": 18,           # never earlier than this (server local hour)
    "latest_hour": 22,             # never later than this
    "revision_hour": 18,           # user-local hour the revision reminder window opens
    "revision_grace_minutes": 180,  # ...and stays open this long (quiet hours can still delay the push inside it)
    "revision_every_days": 3,      # at most one revision reminder per this many days
    "revision_min_items": 3,       # smaller decks are not worth a push (the in-app card still shows them)
    "revision_min_age_days": 2,    # an item saved today is not "due" yet
    "srs_every_days": 1,           # P-F8: users who review with spaced repetition get a due-cards reminder up to once a day
    "srs_min_due": 3,              # P-F8: due + new cards needed before an SRS user is nudged (the in-app card shows any number)
    "weekly_email": True,          # send the weekly report e-mail (still gated by channel_policy)
}

BATCH_SIZE = 500
_SLOT_TTL = 36 * 60 * 60
_SLOT_KEY = "habit:slot:{user_id}:{day}"


def cfg(name: str):
    try:
        overrides = getattr(settings, "HABIT_LOOP", None) or {}
    except Exception:  # noqa: BLE001
        overrides = {}
    return overrides.get(name, DEFAULTS[name])


def _int_cfg(name: str) -> int:
    try:
        return int(cfg(name))
    except (TypeError, ValueError):
        return int(DEFAULTS[name])


def _chunks(seq, size=BATCH_SIZE):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ---------------------------------------------------------------------------------------------------------------------
# SMART TIMING
# ---------------------------------------------------------------------------------------------------------------------
def typical_hours(user_ids, today=None) -> dict:
    """{user_id: usual last-active hour (0-23, server local tz)} for users with >= min_samples days of history.
    One query per chunk of 500 users. Users without enough history are simply absent from the result."""
    from user_profile.models import DailyUsage

    today = today or timezone.localdate()
    since = today - timedelta(days=_int_cfg("history_days"))
    min_samples = _int_cfg("min_samples")
    out: dict = {}
    for chunk in _chunks(user_ids):
        buckets = defaultdict(list)
        rows = DailyUsage.objects.filter(
            user_id__in=chunk, date__gte=since, date__lt=today, seconds__gt=0, last_heartbeat_at__isnull=False,
        ).values_list("user_id", "last_heartbeat_at")
        for uid, ts in rows:
            buckets[uid].append(timezone.localtime(ts).hour)
        for uid, hours in buckets.items():
            if len(hours) >= min_samples:
                out[uid] = int(statistics.median(hours))
    return out


def nudge_hour(typical, fallback_hour: int) -> int:
    """The server-local hour from which a user may get the streak warning. `typical` None -> `fallback_hour`."""
    if typical is None or not cfg("smart_timing"):
        return int(fallback_hour)
    lo, hi = _int_cfg("earliest_hour"), _int_cfg("latest_hour")
    return max(lo, min(hi, int(typical) - _int_cfg("lead_hours")))


def nudge_hours_for(user_ids, fallback_hour: int, today=None) -> dict:
    """{user_id: nudge hour} for every id (history-less users get `fallback_hour`)."""
    if not cfg("smart_timing"):
        return {uid: int(fallback_hour) for uid in user_ids}
    try:
        typical = typical_hours(user_ids, today)
    except Exception:  # noqa: BLE001 - smart timing is an optimisation; never lose the reminder over it
        logger.exception("habit_loop.typical_hours failed - using the fixed hour")
        typical = {}
    return {uid: nudge_hour(typical.get(uid), fallback_hour) for uid in user_ids}


# ---------------------------------------------------------------------------------------------------------------------
# ONE habit nudge slot per user per day
# ---------------------------------------------------------------------------------------------------------------------
def slot_taken(user_id, day) -> bool:
    try:
        return bool(cache.get(_SLOT_KEY.format(user_id=user_id, day=day)))
    except Exception:  # noqa: BLE001 - cache down => not taken (one extra reminder beats a lost one)
        return False


def take_slot(user_id, day, kind: str) -> None:
    try:
        cache.add(_SLOT_KEY.format(user_id=user_id, day=day), kind, _SLOT_TTL)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------------------------------------------------
# REVISION DECK
# ---------------------------------------------------------------------------------------------------------------------
def revision_stats(user_ids, now=None) -> dict:
    """{user_id: {"total": n, "due": n, "oldest_days": n}} for users who have a Revision deck.
    due = saved at least `revision_min_age_days` ago (an item saved today is not due)."""
    from django.db.models import Count, Min, Q

    from post.study_tools import REVISION_COLLECTION
    from post.models import PostSave

    now = now or timezone.now()
    cutoff = now - timedelta(days=_int_cfg("revision_min_age_days"))
    out: dict = {}
    for chunk in _chunks(user_ids):
        rows = (
            PostSave.objects.filter(user_id__in=chunk, collection_name=REVISION_COLLECTION)
            .values("user_id")
            .annotate(total=Count("id"), due=Count("id", filter=Q(created_at__lte=cutoff)), oldest=Min("created_at"))
        )
        for r in rows:
            out[r["user_id"]] = {
                "total": int(r["total"]),
                "due": int(r["due"]),
                "oldest_days": max(0, (now - r["oldest"]).days) if r["oldest"] else 0,
                "srs": False,
            }
        _apply_srs(out, chunk, now)
    return out


def _apply_srs(out: dict, user_ids, now) -> None:
    """P-F8: for users who already review with spaced repetition, `due` = cards the SM-2 schedule says are due + the new
    cards of the day (deck items saved >= `revision_min_age_days` ago), not "saved N days ago". Users who never started
    SRS keep the age rule. Any failure here keeps the age rule (a reminder is never lost over this)."""
    try:
        from spaced_repetition import conf as srs_conf, services as srs

        if not srs_conf.enabled() or not out:
            return
        started = set(srs.RevisionCard.objects.filter(user_id__in=[u for u in user_ids if u in out])
                      .values_list("user_id", flat=True).distinct())
        if not started:
            return
        counts = srs.reminder_counts(started, now, min_new_age_days=_int_cfg("revision_min_age_days"))
        for uid in started:
            c = counts.get(uid) or {"due": 0, "new": 0, "total": 0}
            out[uid]["due"] = int(c["total"])
            out[uid]["srs_due"] = int(c["due"])
            out[uid]["srs_new"] = int(c["new"])
            out[uid]["srs"] = True
    except Exception:  # noqa: BLE001
        logger.exception("habit_loop._apply_srs failed - keeping the age rule")


def compose_revision(due: int, oldest_days: int, language: str = "en"):
    """-> (title, message)."""
    if language == "hi":
        title = "रिवीज़न का समय \U0001F501"
        message = f"आपके रिवीज़न डेक में {due} आइटम इंतज़ार कर रहे हैं - 5 मिनट में दोहरा लें।"
        if oldest_days >= 7:
            message = f"आपका सबसे पुराना आइटम {oldest_days} दिन से रखा है। " + message
    else:
        title = "Time to revise \U0001F501"
        message = f"{due} item{'s' if due != 1 else ''} in your Revision deck {'are' if due != 1 else 'is'} waiting - a 5-minute review keeps them fresh."
        if oldest_days >= 7:
            message = f"Your oldest saved item is {oldest_days} days old. " + message
    return title, message


def compose_srs_due(due: int, new: int, language: str = "en"):
    """P-F8 -> (title, message) for a user who reviews with spaced repetition. `due` = cards scheduled for today (a card
    that is already due counts), `new` = fresh cards of the day."""
    total = due + new
    if language == "hi":
        title = "आज के रिवीज़न कार्ड \U0001F9E0"
        if due and new:
            message = f"आज {due} कार्ड दोहराने हैं और {new} नए आपका इंतज़ार कर रहे हैं - 5 मिनट काफ़ी हैं।"
        elif due:
            message = f"आज {due} कार्ड दोहराने का समय है - 5 मिनट में निपट जाएंगे।"
        else:
            message = f"आज के {new} नए कार्ड तैयार हैं - एक छोटा सत्र शुरू करें।"
    else:
        title = "Your revision cards are due \U0001F9E0"
        if due and new:
            message = f"{due} card{'s' if due != 1 else ''} due today plus {new} new - about 5 minutes."
        elif due:
            message = f"{due} card{'s' if due != 1 else ''} {'are' if due != 1 else 'is'} due today - a quick 5-minute session."
        else:
            message = f"{new} new card{'s' if new != 1 else ''} {'are' if new != 1 else 'is'} ready - start a short session."
    return title, message


def run_revision_reminders(now=None) -> dict:
    """Celery beat (every 15 min). Creates today's revision reminder for users whose local `revision_hour` window is open,
    then retries pushes held back by quiet hours / DND. Idempotent and safe to run any number of times."""
    from django.db.models import Count

    from post.models import PostSave
    from post.study_tools import REVISION_COLLECTION

    now = now or timezone.now()
    summary = {"checked": 0, "created": 0, "not_due": 0, "active_today": 0, "slot_taken": 0, "recent": 0,
               "muted": 0, "failed": 0, "flush": {}}
    try:
        summary["flush"] = flush_pending(now)
    except Exception:  # noqa: BLE001
        logger.exception("habit_loop.flush_pending failed")

    min_items = _int_cfg("revision_min_items")
    cutoff = now - timedelta(days=_int_cfg("revision_min_age_days"))
    candidates = list(
        PostSave.objects.filter(collection_name=REVISION_COLLECTION, created_at__lte=cutoff, user__is_active=True)
        .values("user_id").annotate(n=Count("id")).filter(n__gte=min_items).values_list("user_id", flat=True)
    )
    try:  # P-F8: users with SM-2 cards due now are candidates even if their deck is small
        from spaced_repetition import conf as srs_conf
        from spaced_repetition.models import RevisionCard

        if srs_conf.enabled():
            extra = RevisionCard.objects.filter(next_review_at__lte=now, user__is_active=True).values_list(
                "user_id", flat=True).distinct()
            candidates = list(set(candidates) | set(extra))
    except Exception:  # noqa: BLE001
        logger.exception("habit_loop: SRS candidates failed")
    for chunk in _chunks(candidates):
        try:
            _run_chunk(chunk, now, summary)
        except Exception:  # noqa: BLE001 - one bad chunk must not stop the rest
            logger.exception("habit_loop.run_revision_reminders chunk failed")
            summary["failed"] += len(chunk)
    return summary


def _run_chunk(user_ids, now, summary) -> None:
    from user_profile import learning_prefs
    from user_profile.models import Streak, UserPreference

    from .content_digest import push_pending_row
    from .models import Notification, NotificationPreference
    from .services import create_notification

    notif_type = Notification.NotifType.REVISION_DECK_REMINDER
    notif_prefs = {p.user_id: p for p in NotificationPreference.objects.filter(user_id__in=user_ids)}
    streaks = {s.user_id: s for s in Streak.objects.filter(user_id__in=user_ids)}
    langs = dict(UserPreference.objects.filter(user_id__in=user_ids).values_list("user_id", "language"))
    stats = revision_stats(user_ids, now)

    every = timedelta(days=_int_cfg("revision_every_days"))
    srs_every = timedelta(days=_int_cfg("srs_every_days"))
    last_sent = {}
    for rid, created in Notification.objects.filter(
        recipient_id__in=user_ids, notif_type=notif_type, created_at__gte=now - max(every, srs_every),
    ).values_list("recipient_id", "created_at"):
        if rid not in last_sent or created > last_sent[rid]:
            last_sent[rid] = created
    recent = {rid for rid, created in last_sent.items()
              if created >= now - (srs_every if (stats.get(rid) or {}).get("srs") else every)}
    day_start = now - timedelta(hours=24)
    nudged_today = set(Notification.objects.filter(
        recipient_id__in=user_ids, created_at__gte=day_start,
        notif_type__in=[Notification.NotifType.STUDY_REMINDER, Notification.NotifType.STREAK_AT_RISK],
    ).values_list("recipient_id", flat=True))

    hour = _int_cfg("revision_hour")
    grace = timedelta(minutes=_int_cfg("revision_grace_minutes"))
    for uid in user_ids:
        summary["checked"] += 1
        try:
            notif_pref = notif_prefs.get(uid)
            tz = learning_prefs.tz_from_name(getattr(notif_pref, "timezone", "") if notif_pref else "")
            local = learning_prefs.local_now(now, tz)
            start = local.replace(hour=hour, minute=0, second=0, microsecond=0)
            if not (timedelta(0) <= local - start < grace):
                summary["not_due"] += 1
                continue
            if uid in recent:
                summary["recent"] += 1
                continue
            streak = streaks.get(uid)
            today = local.date()
            if streak is not None and (streak.last_active_date == today or streak.goal_completed_date == today):
                summary["active_today"] += 1  # already studying today: the in-app card is enough
                continue
            if uid in nudged_today or slot_taken(uid, today):
                summary["slot_taken"] += 1
                continue
            if notif_pref is not None and (
                notif_type.value in (notif_pref.muted_types or []) or not notif_pref.in_app_allowed(notif_type)
            ):
                summary["muted"] += 1
                continue
            st = stats.get(uid) or {}
            due, oldest = int(st.get("due", 0)), int(st.get("oldest_days", 0))
            is_srs = bool(st.get("srs"))
            if due < _int_cfg("srs_min_due" if is_srs else "revision_min_items"):
                summary["not_due"] += 1
                continue
            lang = _language_from_code(langs.get(uid))
            data = {"type": "revision_deck_reminder", "kind": "revision_deck", "deck_size": due,
                    "oldest_days": oldest, "push_state": "pending"}
            if is_srs:
                title, message = compose_srs_due(int(st.get("srs_due", 0)), int(st.get("srs_new", 0)), lang)
                data.update({"srs": True, "srs_due": int(st.get("srs_due", 0)), "srs_new": int(st.get("srs_new", 0))})
            else:
                title, message = compose_revision(due, oldest, lang)
            row = create_notification(uid, notif_type, title, message, data=data)
            if row is None:
                summary["failed"] += 1
                continue
            take_slot(uid, today, "revision")
            push_pending_row(row, notif_pref, now)
            summary["created"] += 1
        except Exception:  # noqa: BLE001
            logger.exception("habit_loop revision reminder failed (user=%s)", uid)
            summary["failed"] += 1


def _language_from_code(code) -> str:
    return "hi" if (code or "").lower().startswith("hi") else "en"


def flush_pending(now) -> dict:
    """Revision pushes held back by quiet hours / DND: send when the window ends; expire after the grace window."""
    from .content_digest import push_pending_row
    from .models import Notification, NotificationPreference

    out = {"sent": 0, "still_pending": 0, "expired": 0, "skipped": 0}
    max_age = timedelta(minutes=_int_cfg("revision_grace_minutes"))
    rows = [
        r for r in Notification.objects.filter(
            notif_type=Notification.NotifType.REVISION_DECK_REMINDER, created_at__gte=now - timedelta(days=1),
        ) if (r.data or {}).get("push_state") == "pending"
    ]
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
            logger.exception("habit_loop.flush_pending failed (row=%s)", row.pk)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# IN-APP: today's habit snapshot (Home habit card)
# ---------------------------------------------------------------------------------------------------------------------
def today_snapshot(user, now=None) -> dict:
    """What the Home habit card shows. Read-only, <= 5 small indexed queries, every section has its own try/except.

        {
          "streak":  {"current", "longest", "active_today", "at_risk", "freeze_tokens"},
          "goal":    {"minutes", "done_seconds", "done_minutes", "percent", "completed", "remaining_minutes"},
          "revision": {"total", "due", "oldest_days", "srs", "new"} | null,  # null = no Revision deck at all; srs=True: SM-2 counts
          "weekly_report": {"week_start", "tests_attempted", "classes_attended", "streak_days", "study_minutes",
                            "active_days"} | null,                    # last week's, only for 7 days after it ends
          "next_action": "streak" | "goal" | "revision" | "weekly_report" | "none",
          "errors": [...]
        }

    `next_action` is the ONE thing the card should push right now: protect the streak first, then finish the goal, then
    revise, then read the weekly report."""
    from user_profile.models import DailyUsage, Streak, WeeklyRecap

    now = now or timezone.now()
    today = timezone.localdate(now)
    errors: list = []
    out: dict = {"streak": None, "goal": None, "revision": None, "weekly_report": None, "next_action": "none",
                 "errors": errors}

    streak = None
    try:
        streak = Streak.objects.filter(user=user).first()
        current = int(getattr(streak, "current_streak", 0) or 0)
        active_today = bool(streak and streak.last_active_date == today)
        out["streak"] = {
            "current": current,
            "longest": int(getattr(streak, "longest_streak", 0) or 0),
            "active_today": active_today,
            "at_risk": bool(current > 0 and not active_today),
            "freeze_tokens": int(getattr(streak, "freeze_tokens", 0) or 0),
        }
    except Exception:  # noqa: BLE001
        logger.warning("habit snapshot: streak failed", exc_info=True)
        errors.append("streak")

    try:
        goal_minutes = int(getattr(streak, "daily_goal_minutes", None) or 10)
        seconds = DailyUsage.objects.filter(user=user, date=today).values_list("seconds", flat=True).first() or 0
        completed = bool(streak and streak.goal_completed_date == today) or seconds >= goal_minutes * 60
        done_min = int(seconds // 60)
        out["goal"] = {
            "minutes": goal_minutes,
            "done_seconds": int(seconds),
            "done_minutes": done_min,
            "percent": 100 if completed else min(99, int(seconds * 100 // max(1, goal_minutes * 60))),
            "completed": completed,
            "remaining_minutes": 0 if completed else max(1, goal_minutes - done_min),
        }
    except Exception:  # noqa: BLE001
        logger.warning("habit snapshot: goal failed", exc_info=True)
        errors.append("goal")

    try:
        st = revision_stats([user.pk], now).get(user.pk)
        out["revision"] = None if not st else {
            "total": st["total"], "due": st["due"], "oldest_days": st["oldest_days"],
            "srs": bool(st.get("srs")), "new": int(st.get("srs_new", 0)),  # P-F8: due already includes `new` for SRS users
        }
    except Exception:  # noqa: BLE001
        logger.warning("habit snapshot: revision failed", exc_info=True)
        errors.append("revision")

    try:
        from user_profile.recap import week_bounds

        this_monday, _ = week_bounds(today)
        last_monday = this_monday - timedelta(days=7)
        # only the first days of the new week: after that the report is old news (the recap screen still has it)
        if (today - this_monday).days <= 2:
            recap = WeeklyRecap.objects.filter(user=user, week_start=last_monday).first()
            if recap is not None and recap.has_any_activity:
                out["weekly_report"] = {"week_start": recap.week_start.isoformat(), **weekly_numbers(user, recap)}
    except Exception:  # noqa: BLE001
        logger.warning("habit snapshot: weekly report failed", exc_info=True)
        errors.append("weekly_report")

    out["next_action"] = _next_action(out)
    return out


def _next_action(snap: dict) -> str:
    s, g, r, w = snap.get("streak"), snap.get("goal"), snap.get("revision"), snap.get("weekly_report")
    if s and s.get("at_risk"):
        return "streak"
    if g and not g.get("completed"):
        return "goal"
    if r and r.get("due", 0) >= _int_cfg("srs_min_due" if r.get("srs") else "revision_min_items"):
        return "revision"
    if w:
        return "weekly_report"
    return "none"


# ---------------------------------------------------------------------------------------------------------------------
# WEEKLY REPORT (numbers + e-mail)
# ---------------------------------------------------------------------------------------------------------------------
def weekly_numbers(user, recap) -> dict:
    """The recap row's counts + the two numbers it does not store (study minutes, active days) from DailyUsage."""
    from user_profile.models import DailyUsage

    start, end = recap.week_start, recap.week_end
    rows = list(DailyUsage.objects.filter(user=user, date__gte=start, date__lte=end).values_list("seconds", flat=True))
    return {
        "tests_attempted": int(recap.tests_attempted),
        "classes_attended": int(recap.classes_attended),
        "posts_liked_received": int(recap.posts_liked_received),
        "streak_days": int(recap.streak_days),
        "study_minutes": int(sum(rows) // 60),
        "active_days": sum(1 for s in rows if s and s > 0),
    }


def compose_weekly_report(numbers: dict, deck_due: int = 0, language: str = "en"):
    """-> (subject, [lines]). Plain data; `render_weekly_report_email` turns it into text + html."""
    n = numbers
    if language == "hi":
        subject = "आपका साप्ताहिक लर्निंग रिपोर्ट \U0001F4CA"
        lines = [
            f"पढ़ाई का समय: {n['study_minutes']} मिनट ({n['active_days']}/7 दिन सक्रिय)",
            f"टेस्ट दिए: {n['tests_attempted']} | क्लास अटेंड कीं: {n['classes_attended']}",
            f"आपकी पोस्ट पर लाइक: {n['posts_liked_received']}",
            f"मौजूदा स्ट्रीक: {n['streak_days']} दिन",
        ]
        if deck_due:
            lines.append(f"रिवीज़न डेक में {deck_due} आइटम इंतज़ार कर रहे हैं।")
    else:
        subject = "Your weekly learning report \U0001F4CA"
        lines = [
            f"Study time: {n['study_minutes']} min ({n['active_days']}/7 days active)",
            f"Tests taken: {n['tests_attempted']} | Classes attended: {n['classes_attended']}",
            f"Likes on your posts: {n['posts_liked_received']}",
            f"Current streak: {n['streak_days']} days",
        ]
        if deck_due:
            lines.append(f"{deck_due} item{'s' if deck_due != 1 else ''} waiting in your Revision deck.")
    return subject, lines


def send_weekly_report_email(user_id, recap_id=None, now=None) -> str:
    """ONE weekly report e-mail. -> "sent" | "off" | "no_recap" | "blocked:<reason>" | "failed". Never raises.

    Gate = channel_policy.decide(..., WEEKLY_RECAP_READY): verified address, not suppressed, e-mail switch on AND this
    type not muted for e-mail, under the daily e-mail cap. The bell row gets `data.emailed = True` so the periodic
    summary e-mail (tuitionclass.send_notification_digests) does not repeat the same recap."""
    if not cfg("weekly_email"):
        return "off"
    try:
        from django.contrib.auth import get_user_model
        from django.core.mail import EmailMultiAlternatives

        from user_profile.models import WeeklyRecap

        from . import channel_policy as policy
        from .channel_templates import language_for, render_event_email
        from .models import Notification

        user = get_user_model().objects.filter(pk=user_id, is_active=True).first()
        if user is None:
            return "no_recap"
        recap = WeeklyRecap.objects.filter(pk=recap_id).first() if recap_id else None
        if recap is None or recap.user_id != user.pk or not recap.has_any_activity:
            return "no_recap"
        notif_type = Notification.NotifType.WEEKLY_RECAP_READY
        decision = policy.decide(user, "email", notif_type.value, now=now)
        if not decision.allowed:
            return f"blocked:{decision.reason}"

        lang = language_for(user)
        deck = revision_stats([user.pk]).get(user.pk) or {}
        subject, lines = compose_weekly_report(weekly_numbers(user, recap), int(deck.get("due", 0)), lang)
        url = policy.unsubscribe_url(user, "email", "channel")
        _subject, text, html = render_event_email(subject, "\n".join(lines), lang, url)
        # render_event_email puts the message in one <p>; keep the lines readable in the html part too
        html = html.replace("\n", "<br>")
        mail = EmailMultiAlternatives(
            subject=subject, body=text, from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=[decision.address], headers=policy.email_headers(url),
        )
        mail.attach_alternative(html, "text/html")
        mail.send(fail_silently=False)
        policy.record_send(user, "email", notif_type.value)
        _mark_emailed(user.pk, recap, notif_type)
        return "sent"
    except Exception:  # noqa: BLE001
        logger.exception("habit_loop.send_weekly_report_email failed (user=%s)", user_id)
        return "failed"


def _mark_emailed(user_id, recap, notif_type) -> None:
    from .models import Notification

    try:
        row = Notification.objects.filter(
            recipient_id=user_id, notif_type=notif_type, data__recap_id=recap.id,
        ).order_by("-created_at").first()
        if row is not None:
            row.data = {**(row.data or {}), "emailed": True}
            row.save(update_fields=["data"])
    except Exception:  # noqa: BLE001 - bookkeeping only
        logger.warning("habit_loop: could not mark recap row emailed", exc_info=True)
