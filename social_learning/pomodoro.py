"""
social_learning/pomodoro.py - server-owned Pomodoro for a study room (P-F5, "study with me").

Why server-owned: the old timer (Flutter `study_room_timer.dart`) lived on every phone and was copied around with
`timer_update` events - a late joiner started at 25:00, a phone with a wrong clock disagreed, and nobody could be credited
for a finished pomodoro. Now ONE row (`PomodoroTimer`) per room says what phase it is and when it ends. Clients only draw it.

State machine (all transitions happen inside `transaction.atomic()` on a `select_for_update()` row):

    idle --start--> focus --(ends)--> short_break --(ends)--> focus ... after `cycles_before_long` focus rounds -> long_break
    focus/break --pause--> paused (is_running False, `paused_remaining_seconds` kept) --resume--> same phase
    any --skip--> next phase now      any --stop--> idle

Phases advance LAZILY: `advance_if_due()` runs on every read / control call (and from the sweeper), so a due phase is rolled
forward even if no Celery worker is alive. A room nobody has watched for a long time is simply stopped (MAX_CATCHUP).

Credit: a member claims a finished focus round (`claim_round`). The server accepts it only if the user was really in the room
(`message.StudyRoomPresence`, S5) for at least SOCIAL_POMODORO_MIN_PRESENCE_FRACTION of that round, once per round, within 24 h,
and under a daily cap.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import conf
from .models import PomodoroCompletion, PomodoroRound, PomodoroTimer

logger = logging.getLogger(__name__)

Phase = PomodoroTimer.Phase

MIN_FOCUS, MAX_FOCUS = 5, 120
MIN_BREAK, MAX_BREAK = 1, 60
MIN_CYCLES, MAX_CYCLES = 2, 8
MAX_CATCHUP = 24                       # phases rolled forward in one call; more => the room was abandoned, stop it
CLAIM_WINDOW = timedelta(hours=24)
END_TOLERANCE = timedelta(seconds=5)

EVT_TIMER_UPDATE = "timer_update"      # same WS action the old client already listens to (study_room_session.dart)


class PomodoroError(Exception):
    def __init__(self, detail, code, http_status=400):
        super().__init__(detail)
        self.detail, self.code, self.http_status = detail, code, http_status


# ----------------------------------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------------------------------
def get_or_create_timer(conversation) -> PomodoroTimer:
    timer, _ = PomodoroTimer.objects.get_or_create(conversation=conversation)
    return timer


def _planned_seconds(timer, kind) -> int:
    minutes = {
        Phase.FOCUS: timer.focus_minutes,
        Phase.SHORT_BREAK: timer.short_break_minutes,
        Phase.LONG_BREAK: timer.long_break_minutes,
    }[kind]
    return int(minutes) * 60


def _clamp(value, low, high, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def apply_settings(timer, data) -> None:
    """Validate + store durations (only when the caller sent them). Out-of-range values are clamped, not rejected."""
    data = data or {}
    if data.get("focus_minutes") is not None:
        timer.focus_minutes = _clamp(data["focus_minutes"], MIN_FOCUS, MAX_FOCUS, timer.focus_minutes)
    if data.get("short_break_minutes") is not None:
        timer.short_break_minutes = _clamp(data["short_break_minutes"], MIN_BREAK, MAX_BREAK, timer.short_break_minutes)
    if data.get("long_break_minutes") is not None:
        timer.long_break_minutes = _clamp(data["long_break_minutes"], MIN_BREAK, MAX_BREAK, timer.long_break_minutes)
    if data.get("cycles_before_long") is not None:
        timer.cycles_before_long = _clamp(data["cycles_before_long"], MIN_CYCLES, MAX_CYCLES, timer.cycles_before_long)


def _close_round(timer, now, completed):
    rnd = timer.current_round
    if rnd is not None and rnd.ended_at is None:
        rnd.ended_at = now
        rnd.completed = bool(completed)
        rnd.save(update_fields=["ended_at", "completed"])


def _begin_phase(timer, kind, started_at):
    seconds = _planned_seconds(timer, kind)
    rnd = PomodoroRound.objects.create(timer=timer, kind=kind, started_at=started_at, planned_seconds=seconds)
    timer.current_round = rnd
    timer.phase = kind
    timer.is_running = True
    timer.phase_started_at = started_at
    timer.phase_ends_at = started_at + timedelta(seconds=seconds)
    timer.paused_remaining_seconds = None
    return rnd


def _next_kind_after(timer, finished_kind):
    """Update `cycle_index` and return the phase that follows `finished_kind`."""
    if finished_kind == Phase.FOCUS:
        timer.cycle_index += 1
        if timer.cycle_index >= timer.cycles_before_long:
            timer.cycle_index = 0
            return Phase.LONG_BREAK
        return Phase.SHORT_BREAK
    return Phase.FOCUS


def _to_idle(timer):
    timer.phase = Phase.IDLE
    timer.is_running = False
    timer.phase_started_at = None
    timer.phase_ends_at = None
    timer.paused_remaining_seconds = None
    timer.current_round = None
    timer.cycle_index = 0


def advance_if_due(timer, now=None) -> bool:
    """Roll finished phases forward. Caller holds the row lock. Returns True when the row changed (caller saves)."""
    now = now or timezone.now()
    changed = False
    steps = 0
    while timer.is_running and timer.phase_ends_at is not None and timer.phase_ends_at <= now:
        steps += 1
        if steps > MAX_CATCHUP:                      # abandoned room: do not fabricate hundreds of rounds
            _close_round(timer, timer.phase_ends_at, completed=False)
            _to_idle(timer)
            changed = True
            break
        ended_at = timer.phase_ends_at
        finished = timer.phase
        _close_round(timer, ended_at, completed=True)
        _begin_phase(timer, _next_kind_after(timer, finished), ended_at)
        changed = True
    if changed:
        timer.version += 1
    return changed


# ----------------------------------------------------------------------------------------------------------------------
# state -> payload
# ----------------------------------------------------------------------------------------------------------------------
def remaining_seconds(timer, now=None) -> int:
    now = now or timezone.now()
    if timer.phase == Phase.IDLE:
        return int(timer.focus_minutes) * 60
    if timer.is_running and timer.phase_ends_at is not None:
        return max(0, int((timer.phase_ends_at - now).total_seconds()))
    return int(timer.paused_remaining_seconds or 0)


def payload(timer, now=None, focusing_now=None) -> dict:
    """JSON for REST + the `timer_update` WS event. The first five keys are exactly what the old client already parsed
    (`StudyTimerState.fromJson`), so an app that does not know the new keys still shows the right countdown."""
    now = now or timezone.now()
    is_break = timer.phase in (Phase.SHORT_BREAK, Phase.LONG_BREAK)
    body = {
        "isRunning": bool(timer.is_running),
        "isBreak": is_break,
        "focusMinutes": timer.focus_minutes,
        "breakMinutes": timer.long_break_minutes if timer.phase == Phase.LONG_BREAK else timer.short_break_minutes,
        "endAt": timer.phase_ends_at.isoformat() if (timer.is_running and timer.phase_ends_at) else None,
        # ---- new in P-F5
        "phase": timer.phase,
        "paused": bool(timer.phase != Phase.IDLE and not timer.is_running),
        "remainingSeconds": remaining_seconds(timer, now),
        "serverNow": now.isoformat(),
        "shortBreakMinutes": timer.short_break_minutes,
        "longBreakMinutes": timer.long_break_minutes,
        "cyclesBeforeLong": timer.cycles_before_long,
        "cycle": timer.cycle_index,
        "roundId": str(timer.current_round_id) if timer.current_round_id else None,
        "version": timer.version,
    }
    if focusing_now is not None:
        body["focusingNow"] = focusing_now
    return body


def focusing_now(conversation_id) -> int:
    """People connected to the room right now (open presence rows, S5). One COUNT query."""
    from message.models import StudyRoomPresence

    return StudyRoomPresence.objects.filter(conversation_id=conversation_id, left_at__isnull=True).values("user_id").distinct().count()


def _broadcast(timer, now=None):
    def _send():
        try:
            from message import study_room_events as events

            events.broadcast(timer.conversation_id, EVT_TIMER_UPDATE, payload(timer, now))
        except Exception:
            logger.warning("pomodoro broadcast failed conv=%s", timer.conversation_id, exc_info=True)

    transaction.on_commit(_send)


# ----------------------------------------------------------------------------------------------------------------------
# controls (each one: lock row -> catch up -> change -> save -> broadcast)
# ----------------------------------------------------------------------------------------------------------------------
def _locked(conversation_id) -> PomodoroTimer:
    PomodoroTimer.objects.get_or_create(conversation_id=conversation_id)
    return PomodoroTimer.objects.select_for_update().select_related("current_round").get(conversation_id=conversation_id)


def read_state(conversation) -> PomodoroTimer:
    """GET path: catch up if a phase is due (a write, so it takes the lock only when something is actually due)."""
    timer = get_or_create_timer(conversation)
    now = timezone.now()
    if timer.is_running and timer.phase_ends_at and timer.phase_ends_at <= now:
        with transaction.atomic():
            timer = _locked(conversation.id)
            if advance_if_due(timer, now):
                timer.save()
                _broadcast(timer, now)
    return timer


def start(conversation_id, user, settings_data=None, now=None) -> PomodoroTimer:
    now = now or timezone.now()
    with transaction.atomic():
        timer = _locked(conversation_id)
        advance_if_due(timer, now)
        if timer.is_running:
            raise PomodoroError("Timer is already running.", "already_running", 409)
        apply_settings(timer, settings_data)
        if timer.phase == Phase.IDLE:
            timer.cycle_index = 0
            _begin_phase(timer, Phase.FOCUS, now)
        else:                                          # paused -> start means resume
            timer.is_running = True
            timer.phase_started_at = now
            timer.phase_ends_at = now + timedelta(seconds=int(timer.paused_remaining_seconds or 0))
            timer.paused_remaining_seconds = None
        timer.version += 1
        timer.updated_by = user
        timer.save()
        _broadcast(timer, now)
        return timer


def pause(conversation_id, user, now=None) -> PomodoroTimer:
    now = now or timezone.now()
    with transaction.atomic():
        timer = _locked(conversation_id)
        advance_if_due(timer, now)
        if not timer.is_running:
            raise PomodoroError("Timer is not running.", "not_running", 409)
        timer.paused_remaining_seconds = remaining_seconds(timer, now)
        timer.is_running = False
        timer.phase_ends_at = None
        timer.version += 1
        timer.updated_by = user
        timer.save()
        _broadcast(timer, now)
        return timer


def resume(conversation_id, user, now=None) -> PomodoroTimer:
    now = now or timezone.now()
    with transaction.atomic():
        timer = _locked(conversation_id)
        advance_if_due(timer, now)
        if timer.phase == Phase.IDLE or timer.is_running:
            raise PomodoroError("Nothing to resume.", "not_paused", 409)
        timer.is_running = True
        timer.phase_started_at = now
        timer.phase_ends_at = now + timedelta(seconds=int(timer.paused_remaining_seconds or 0))
        timer.paused_remaining_seconds = None
        timer.version += 1
        timer.updated_by = user
        timer.save()
        _broadcast(timer, now)
        return timer


def skip(conversation_id, user, now=None) -> PomodoroTimer:
    """End the current phase now. A skipped FOCUS round is NOT completed (no credit); a skipped break just moves on."""
    now = now or timezone.now()
    with transaction.atomic():
        timer = _locked(conversation_id)
        advance_if_due(timer, now)
        if timer.phase == Phase.IDLE:
            raise PomodoroError("Timer is idle.", "idle", 409)
        finished = timer.phase
        _close_round(timer, now, completed=False)
        # a skipped focus is not a finished cycle (no cycle_index bump, no credit) and goes to a short break
        next_kind = Phase.SHORT_BREAK if finished == Phase.FOCUS else Phase.FOCUS
        _begin_phase(timer, next_kind, now)
        timer.version += 1
        timer.updated_by = user
        timer.save()
        _broadcast(timer, now)
        return timer


def stop(conversation_id, user, now=None) -> PomodoroTimer:
    now = now or timezone.now()
    with transaction.atomic():
        timer = _locked(conversation_id)
        advance_if_due(timer, now)
        _close_round(timer, now, completed=False)
        _to_idle(timer)
        timer.version += 1
        timer.updated_by = user
        timer.save()
        _broadcast(timer, now)
        return timer


# ----------------------------------------------------------------------------------------------------------------------
# credit
# ----------------------------------------------------------------------------------------------------------------------
def presence_overlap_seconds(user_id, conversation_id, start, end, now=None) -> int:
    """Seconds the user was connected to the room inside [start, end] (overlapping connections merged, so two devices
    do not count twice)."""
    from message.models import StudyRoomPresence

    now = now or timezone.now()
    rows = StudyRoomPresence.objects.filter(
        conversation_id=conversation_id, user_id=user_id, joined_at__lt=end,
    ).filter(Q(left_at__isnull=True) | Q(left_at__gt=start)).values_list("joined_at", "left_at")
    spans = []
    for joined, left in rows:
        left = left or now
        a, b = max(joined, start), min(left, end)
        if b > a:
            spans.append((a, b))
    spans.sort()
    total, cur_a, cur_b = 0.0, None, None
    for a, b in spans:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += (cur_b - cur_a).total_seconds()
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += (cur_b - cur_a).total_seconds()
    return int(total)


def claim_round(user, round_id, now=None):
    """Returns (credited: bool, reason: str, completion|None). `reason` is '' when credited."""
    now = now or timezone.now()
    rnd = PomodoroRound.objects.select_related("timer").filter(id=round_id).first()
    if rnd is None:
        raise PomodoroError("Round not found.", "round_not_found", 404)
    if rnd.kind != Phase.FOCUS:
        raise PomodoroError("Only a focus round can be claimed.", "not_focus_round", 400)
    if not rnd.completed or rnd.ended_at is None:
        raise PomodoroError("That focus round did not finish.", "round_not_completed", 409)
    if now - rnd.ended_at > CLAIM_WINDOW:
        raise PomodoroError("Too late to claim this round.", "claim_expired", 410)

    existing = PomodoroCompletion.objects.filter(user=user, round=rnd).first()
    if existing is not None:
        return False, "already_claimed", existing

    conversation_id = rnd.timer.conversation_id
    needed = int(rnd.planned_seconds * conf.pomodoro_min_presence_fraction())
    if presence_overlap_seconds(user.id, conversation_id, rnd.started_at, rnd.ended_at, now) < needed:
        return False, "not_enough_presence", None

    local_date = timezone.localtime(rnd.ended_at).date()
    cap = conf.pomodoro_daily_credit_cap()
    if PomodoroCompletion.objects.filter(user=user, local_date=local_date).count() >= cap:
        return False, "daily_cap", None

    completion, created = PomodoroCompletion.objects.get_or_create(
        user=user, round=rnd,
        defaults={"conversation_id": conversation_id, "minutes": max(1, rnd.planned_seconds // 60),
                  "completed_at": rnd.ended_at, "local_date": local_date},
    )
    return (True, "", completion) if created else (False, "already_claimed", completion)


def my_stats(user, now=None) -> dict:
    from django.db.models import Count, Sum

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    week_start = today - timedelta(days=today.weekday())
    qs = PomodoroCompletion.objects.filter(user=user)
    day = qs.filter(local_date=today).aggregate(n=Count("id"), m=Sum("minutes"))
    week = qs.filter(local_date__gte=week_start).aggregate(n=Count("id"), m=Sum("minutes"))
    total = qs.aggregate(n=Count("id"), m=Sum("minutes"))
    return {
        "today": {"pomodoros": day["n"] or 0, "minutes": day["m"] or 0},
        "week": {"pomodoros": week["n"] or 0, "minutes": week["m"] or 0},
        "total": {"pomodoros": total["n"] or 0, "minutes": total["m"] or 0},
        "daily_cap": conf.pomodoro_daily_credit_cap(),
    }


# ----------------------------------------------------------------------------------------------------------------------
# sweeper (celery)
# ----------------------------------------------------------------------------------------------------------------------
def sweep_idle_timers(now=None, idle_minutes=15, limit=500) -> int:
    """Stop running timers whose room has nobody connected and has not changed for `idle_minutes`. Also rolls due phases."""
    from message.models import StudyRoomPresence

    now = now or timezone.now()
    cutoff = now - timedelta(minutes=idle_minutes)
    stopped = 0
    for timer_id, conv_id in list(PomodoroTimer.objects.filter(is_running=True, updated_at__lt=cutoff).values_list("id", "conversation_id")[:limit]):
        if StudyRoomPresence.objects.filter(conversation_id=conv_id, left_at__isnull=True).exists():
            continue
        try:
            stop(conv_id, None, now)
            stopped += 1
        except Exception:
            logger.exception("pomodoro sweep failed conv=%s", conv_id)
    return stopped
