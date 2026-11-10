"""
P-F1 -- step-by-step funnel for the first-session onboarding flow.

The first 60 seconds are:  role -> photo -> quick start (class / exam / interests) -> follows -> quick win
(one free test or one doubt) -> daily goal (+ streak start) -> finish.  Skip is always available.

The app reports, per step, one of three actions:
    viewed     the step was shown
    completed  the user pressed that step's main button (Continue / Start / Set goal ...)
    skipped    the user left the whole flow with "Skip" while on that step

`record_step()` stores ONE `FunnelEvent(onboarding_step)` row per (user, step, action) - the first time only
(dedupe_key), so a back-and-forth or a retry never double counts. Like every funnel write it NEVER raises.

`steps_report()` turns those rows into a drop-off table:  viewed / completed / skipped / left (= viewed but neither
completed nor skipped, i.e. closed the app there) per step, plus how many users who saw the quick win really started a
test or asked a doubt afterwards (taken from the server-written `first_test` / `first_doubt` rows, not from the app).

Security: the endpoint is authenticated and only accepts the allow-listed step / action / role names below; free text
never reaches the table.
"""
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.utils import timezone

from . import funnel
from .models_funnel import FunnelEvent

logger = logging.getLogger(__name__)
E = FunnelEvent.Event

#: Order = order in the flow (students see all of them; teachers skip quick_win / goal, parents see role, photo, finish).
STEPS = ("role", "photo", "quick_start", "follows", "quick_win", "goal", "finish")
ACTIONS = ("viewed", "completed", "skipped")
ROLES = ("student", "teacher", "parent")

#: A quick win counts as "taken" when the user's first test / first doubt happened within this long after they
#: saw the quick-win step (the rows are written by the server when the test starts / the doubt is asked).
QUICK_WIN_CONVERSION_WINDOW = timedelta(hours=24)


def valid_step(step) -> bool:
    return isinstance(step, str) and step in STEPS


def valid_action(action) -> bool:
    return isinstance(action, str) and action in ACTIONS


def record_step(user, step, action, *, role="", props=None):
    """Store one onboarding step row. Returns True only if a NEW row was stored.

    Unknown step / action / anonymous user -> False (nothing stored). Never raises."""
    try:
        if not getattr(user, "pk", None) or not valid_step(step) or not valid_action(action):
            return False
        role = role if role in ROLES else ""
        data = {"step": step, "action": action}
        if role:
            data["role"] = role
        for key, value in (props or {}).items():
            # only small scalar extras (e.g. {"goal_minutes": 10}); never free text
            if isinstance(value, bool) or isinstance(value, int):
                data[str(key)[:24]] = value
        return funnel.record(E.ONBOARDING_STEP, user=user, dedupe_key=f"onb_step:{user.pk}:{step}:{action}", props=data)
    except Exception:  # noqa: BLE001 - analytics must never break onboarding
        logger.exception("onboarding_steps.record_step failed")
        return False


def _window_start(days, today):
    days = max(1, min(int(days), 365))
    start_date = (today or timezone.localdate()) - timedelta(days=days - 1)
    return days, start_date, timezone.make_aware(datetime.combine(start_date, time.min))


def steps_report(days=30, today=None):
    """Per-step drop-off for users who did onboarding steps inside the window.

    Returns {"window": {...}, "steps": [{step, viewed, completed, skipped, left, completed_pct_of_viewed,
    skipped_pct_of_viewed}], "quick_win": {"viewed", "converted", "converted_pct", "ai_doubt_opened"}}.
    """
    days, start_date, tz_start = _window_start(days, today)
    rows = FunnelEvent.objects.filter(event=E.ONBOARDING_STEP, created_at__gte=tz_start).values_list(
        "user_id", "props", "created_at")

    users = {s: {a: set() for a in ACTIONS} for s in STEPS}
    quick_win_seen_at = {}
    ai_doubt_users = set()
    for uid, props, created_at in rows.iterator():
        props = props or {}
        step, action = props.get("step"), props.get("action")
        if uid is None or step not in users or action not in ACTIONS:
            continue
        users[step][action].add(uid)
        if step == "quick_win" and action == "completed" and props.get("ai_doubt") is True:
            ai_doubt_users.add(uid)
        if step == "quick_win" and action == "viewed":
            quick_win_seen_at.setdefault(uid, created_at)

    def pct(num, den):
        return round(100.0 * num / den, 1) if den else None

    steps = []
    for s in STEPS:
        viewed, done, skipped = users[s]["viewed"], users[s]["completed"], users[s]["skipped"]
        # "left" = saw the step, never pressed its button and never tapped Skip there (closed the app / went back)
        left = len(viewed - done - skipped)
        steps.append({
            "step": s,
            "viewed": len(viewed),
            "completed": len(done),
            "skipped": len(skipped),
            "left": left,
            "completed_pct_of_viewed": pct(len(done), len(viewed)),
            "skipped_pct_of_viewed": pct(len(skipped), len(viewed)),
        })

    converted = set()
    if quick_win_seen_at:
        by_user = defaultdict(list)
        uids = list(quick_win_seen_at)
        for i in range(0, len(uids), 5000):
            chunk = uids[i:i + 5000]
            for uid, ts in FunnelEvent.objects.filter(
                    event__in=(E.FIRST_TEST, E.FIRST_DOUBT), user_id__in=chunk).values_list("user_id", "created_at"):
                by_user[uid].append(ts)
        for uid, seen in quick_win_seen_at.items():
            if any(seen - timedelta(minutes=1) <= ts <= seen + QUICK_WIN_CONVERSION_WINDOW for ts in by_user.get(uid, ())):
                converted.add(uid)
    qw_viewed = len(users["quick_win"]["viewed"])
    return {
        "window": {"start": start_date.isoformat(), "days": days},
        "steps": steps,
        "quick_win": {
            "viewed": qw_viewed,
            "converted": len(converted),
            "converted_pct": pct(len(converted), qw_viewed),
            # opened "Ask AI a doubt" from the quick win (client-reported, not verifiable on the server)
            "ai_doubt_opened": len(ai_doubt_users),
        },
    }
