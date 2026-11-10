"""
social_learning/accountability.py - accountability partner (P-F5).

Two people agree on a DAILY GOAL (minutes). Each day both either reach it or the shared streak breaks. Minutes come from
what the server can verify (study room presence, `message.StudyRoomAttendance.total_seconds`) plus an optional capped
"I studied offline" log (`ManualStudyLog`).

Safety rules (a pair is a private, repeated contact between two people - so it is stricter than a follow):
  * never with yourself, never across a block (either direction), never if the other person switched requests off,
  * the two must already be connected: an ACCEPTED follow (either direction) or a private (1:1) chat,
  * a minor and an ADULT can pair only when they have an accepted follow link (the minor's own opt-in) - and a minor can
    always pair with another minor,
  * limits: SOCIAL_PARTNER_MAX_ACTIVE active pairs and SOCIAL_PARTNER_MAX_PENDING_OUT open requests per user,
  * there is NO free text anywhere in a pair: nudges are fixed templates, check-ins are numbers. Nothing to harass with.
  * either person can end the pair at any time; a block ends it too (the pair is hidden from the other side at once).
"""
import logging
from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import conf
from .models import (
    AccountabilityMilestone,
    AccountabilityNudge,
    AccountabilityPair,
    ManualStudyLog,
    SocialPrefs,
)

logger = logging.getLogger(__name__)

Status = AccountabilityPair.Status

GOAL_MIN, GOAL_MAX = 10, 240
DEFAULT_GOAL = 30
NUDGE_TEMPLATES = ("keep_going", "lets_study", "you_got_this", "proud_of_you")
MAX_NUDGES_PER_DAY = 2                  # per sender per pair
MILESTONES = (7, 30, 100)               # shared-streak days that give each partner 1 streak freeze (non-cash)
DETAIL_DAYS = 14
STREAK_LOOKBACK_DAYS = 400


class PartnerError(Exception):
    def __init__(self, detail, code, http_status=400):
        super().__init__(detail)
        self.detail, self.code, self.http_status = detail, code, http_status


def pair_key(user_a_id, user_b_id) -> str:
    a, b = sorted([str(user_a_id), str(user_b_id)])
    return f"{a}:{b}"


def clamp_goal(value, default=DEFAULT_GOAL) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(GOAL_MIN, min(GOAL_MAX, value))


def _day_start(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def prefs_for(user_id) -> SocialPrefs:
    prefs, _ = SocialPrefs.objects.get_or_create(user_id=user_id)
    return prefs


# ----------------------------------------------------------------------------------------------------------------------
# who may pair with whom
# ----------------------------------------------------------------------------------------------------------------------
def _connected(requester, target) -> bool:
    from common import minor_policy
    from message.models import ConversationParticipant, ConversationType

    if minor_policy.linked_user_ids(requester, [target.id]):
        return True
    return ConversationParticipant.objects.filter(
        user=target, left_at__isnull=True, conversation__type=ConversationType.PRIVATE,
        conversation__memberships__user=requester, conversation__memberships__left_at__isnull=True,
    ).exists()


def active_count(user_id) -> int:
    return AccountabilityPair.objects.filter(status=Status.ACTIVE).filter(Q(requester_id=user_id) | Q(partner_id=user_id)).count()


def check_can_pair(requester, target):
    """Raises PartnerError with a machine code, or returns None when the request may go ahead."""
    from common import minor_policy
    from user_profile.services import is_blocked_pair

    if target is None or not getattr(target, "is_active", True):
        raise PartnerError("User not found.", "user_not_found", 404)
    if str(requester.id) == str(target.id):
        raise PartnerError("You cannot partner with yourself.", "self_pair", 400)
    if is_blocked_pair(requester, target):
        # same answer as "not found": the blocked side must not learn that a block exists
        raise PartnerError("User not found.", "user_not_found", 404)
    if not prefs_for(target.id).accepts_partner_requests:
        raise PartnerError("This person is not taking study-partner requests.", "not_accepting", 403)
    if not _connected(requester, target):
        raise PartnerError("You can pair with people you follow / chat with.", "not_connected", 403)
    r_minor, t_minor = minor_policy.is_minor(requester), minor_policy.is_minor(target)
    if r_minor != t_minor and not minor_policy.linked_user_ids(requester, [target.id]):
        raise PartnerError("Study partners across age groups need to follow each other first.", "minor_privacy_blocked", 403)
    if AccountabilityPair.objects.filter(pair_key=pair_key(requester.id, target.id), status__in=[Status.PENDING, Status.ACTIVE]).exists():
        raise PartnerError("You already have a request or pair with this person.", "pair_exists", 409)
    if active_count(requester.id) >= conf.partner_max_active():
        raise PartnerError("You have reached your study-partner limit.", "limit_active", 409)
    if active_count(target.id) >= conf.partner_max_active():
        raise PartnerError("This person has reached their study-partner limit.", "target_limit_active", 409)
    if AccountabilityPair.objects.filter(requester=requester, status=Status.PENDING).count() >= conf.partner_max_pending_out():
        raise PartnerError("Too many open requests. Wait for answers first.", "limit_pending", 409)


# ----------------------------------------------------------------------------------------------------------------------
# lifecycle
# ----------------------------------------------------------------------------------------------------------------------
def request_pair(requester, target, goal=None) -> AccountabilityPair:
    check_can_pair(requester, target)
    try:
        with transaction.atomic():
            pair = AccountabilityPair.objects.create(
                requester=requester, partner=target, pair_key=pair_key(requester.id, target.id),
                daily_goal_minutes=clamp_goal(goal),
            )
    except IntegrityError:                           # two people asked each other at the same moment
        raise PartnerError("You already have a request or pair with this person.", "pair_exists", 409)
    _notify(target, "partner_request", "New study partner request",
            "Someone wants to be your study partner.", actor=requester, pair=pair)
    return pair


def respond(pair, user, accept: bool) -> AccountabilityPair:
    with transaction.atomic():
        locked = AccountabilityPair.objects.select_for_update().get(pk=pair.pk)
        if str(locked.partner_id) != str(user.id):
            raise PartnerError("Only the invited person can answer.", "not_invited", 403)
        if locked.status != Status.PENDING:
            raise PartnerError("This request is no longer open.", "not_pending", 409)
        if accept:
            if active_count(user.id) >= conf.partner_max_active():
                raise PartnerError("You have reached your study-partner limit.", "limit_active", 409)
            if active_count(locked.requester_id) >= conf.partner_max_active():
                raise PartnerError("The other person has reached their limit.", "target_limit_active", 409)
        locked.status = Status.ACTIVE if accept else Status.DECLINED
        locked.responded_at = timezone.now()
        if not accept:
            locked.ended_at = locked.responded_at
        locked.save(update_fields=["status", "responded_at", "ended_at"])
    if accept:
        _notify(locked.requester, "partner_accepted", "Study partner accepted",
                "Your study partner request was accepted. Start your first day!", actor=user, pair=locked)
    return locked


def end_pair(pair, user) -> AccountabilityPair:
    if not pair.includes(user.id):
        raise PartnerError("Not your pair.", "not_found", 404)
    if pair.status not in (Status.PENDING, Status.ACTIVE):
        return pair
    pair.status = Status.ENDED
    pair.ended_at = timezone.now()
    pair.ended_by = user
    pair.save(update_fields=["status", "ended_at", "ended_by"])
    return pair


def set_goal(pair, user, goal) -> AccountabilityPair:
    if not pair.includes(user.id) or pair.status != Status.ACTIVE:
        raise PartnerError("Pair not found.", "not_found", 404)
    pair.daily_goal_minutes = clamp_goal(goal, pair.daily_goal_minutes)
    pair.save(update_fields=["daily_goal_minutes"])
    return pair


def pairs_for(user, statuses=(Status.PENDING, Status.ACTIVE)):
    """Visible pairs of `user`; pairs with a blocked person are dropped (and ended) so neither side sees them."""
    from user_profile.services import blocked_user_ids

    blocked = {str(i) for i in blocked_user_ids(user)}
    out = []
    for pair in AccountabilityPair.objects.filter(status__in=statuses).filter(Q(requester=user) | Q(partner=user)).select_related("requester", "partner").order_by("-created_at"):
        if str(pair.other_user_id(user.id)) in blocked:
            if pair.status in (Status.PENDING, Status.ACTIVE):
                AccountabilityPair.objects.filter(pk=pair.pk).update(status=Status.ENDED, ended_at=timezone.now())
            continue
        out.append(pair)
    return out


# ----------------------------------------------------------------------------------------------------------------------
# minutes / streak
# ----------------------------------------------------------------------------------------------------------------------
def minutes_by_day(user_ids, start_date, end_date) -> dict:
    """{user_id(str): {date: minutes}} for start_date..end_date inclusive. Verified room minutes + manual log."""
    from message.models import StudyRoomAttendance

    out = {str(u): {} for u in user_ids}
    rows = (
        StudyRoomAttendance.objects.filter(user_id__in=list(user_ids), attended_date__gte=start_date, attended_date__lte=end_date)
        .values("user_id", "attended_date").annotate(secs=Sum("total_seconds"))
    )
    for row in rows:
        out[str(row["user_id"])][row["attended_date"]] = int((row["secs"] or 0) // 60)
    for row in ManualStudyLog.objects.filter(user_id__in=list(user_ids), date__gte=start_date, date__lte=end_date).values("user_id", "date", "minutes"):
        d = out[str(row["user_id"])]
        d[row["date"]] = d.get(row["date"], 0) + int(row["minutes"])
    return out


def shared_streak(minutes_a: dict, minutes_b: dict, goal: int, today, started_on=None) -> int:
    """Consecutive days (ending today, or yesterday while today is still open) on which BOTH reached `goal`.
    Days before the pair started never count."""
    streak, day = 0, today
    while True:
        if started_on is not None and day < started_on:
            break
        if minutes_a.get(day, 0) >= goal and minutes_b.get(day, 0) >= goal:
            streak += 1
        elif day != today:                    # today is allowed to be unfinished; any earlier miss ends the run
            break
        day -= timedelta(days=1)
        if (today - day).days > STREAK_LOOKBACK_DAYS:
            break
    return streak


def overview(pair, viewer, now=None) -> dict:
    """Numbers for one pair card / detail screen. Never contains anything the partner typed (there is no free text)."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    other_id = pair.other_user_id(viewer.id)
    start = max(today - timedelta(days=STREAK_LOOKBACK_DAYS), timezone.localtime(pair.created_at).date())
    mins = minutes_by_day([viewer.id, other_id], start, today)
    mine, theirs = mins[str(viewer.id)], mins[str(other_id)]
    goal = pair.daily_goal_minutes
    started_on = timezone.localtime(pair.responded_at or pair.created_at).date()
    streak = shared_streak(mine, theirs, goal, today, started_on) if pair.status == Status.ACTIVE else 0
    days = []
    for i in range(DETAIL_DAYS - 1, -1, -1):
        d = today - timedelta(days=i)
        days.append({"date": d.isoformat(), "me": mine.get(d, 0), "partner": theirs.get(d, 0),
                     "both_done": mine.get(d, 0) >= goal and theirs.get(d, 0) >= goal})
    if streak > pair.best_streak:
        AccountabilityPair.objects.filter(pk=pair.pk).update(best_streak=streak)
        pair.best_streak = streak
    return {
        "today": {"me": mine.get(today, 0), "partner": theirs.get(today, 0), "goal": goal,
                  "me_done": mine.get(today, 0) >= goal, "partner_done": theirs.get(today, 0) >= goal},
        "streak": streak,
        "best_streak": pair.best_streak,
        "next_milestone": next((m for m in MILESTONES if m > streak), None),
        "days": days,
    }


def log_manual_minutes(user, minutes, now=None) -> ManualStudyLog:
    now = now or timezone.now()
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        raise PartnerError("minutes must be a number.", "bad_minutes", 400)
    cap = conf.partner_manual_max_minutes()
    if minutes < 0 or minutes > cap:
        raise PartnerError(f"minutes must be between 0 and {cap}.", "bad_minutes", 400)
    row, _ = ManualStudyLog.objects.update_or_create(user=user, date=timezone.localtime(now).date(), defaults={"minutes": minutes})
    return row


# ----------------------------------------------------------------------------------------------------------------------
# nudges + notifications
# ----------------------------------------------------------------------------------------------------------------------
_NUDGE_TEXT = {
    "keep_going": "Keep going - today's goal is waiting!",
    "lets_study": "Let's study together today?",
    "you_got_this": "You've got this!",
    "proud_of_you": "Proud of your effort. Keep the streak alive!",
}


def send_nudge(pair, sender, template="keep_going", now=None) -> AccountabilityNudge:
    now = now or timezone.now()
    if not pair.includes(sender.id) or pair.status != Status.ACTIVE:
        raise PartnerError("Pair not found.", "not_found", 404)
    if template not in NUDGE_TEMPLATES:
        raise PartnerError("Unknown nudge.", "bad_template", 400)
    today_start = _day_start(timezone.localtime(now).date())
    sent = AccountabilityNudge.objects.filter(pair=pair, sender=sender, kind=AccountabilityNudge.Kind.MANUAL, created_at__gte=today_start).count()
    if sent >= MAX_NUDGES_PER_DAY:
        raise PartnerError("You already nudged today.", "nudge_limit", 429)
    recipient = pair.partner if str(pair.requester_id) == str(sender.id) else pair.requester
    nudge = AccountabilityNudge.objects.create(pair=pair, sender=sender, template=template, created_at=now)
    _notify(recipient, "partner_nudge", "Study partner", _NUDGE_TEXT[template], actor=sender, pair=pair, push=True)
    return nudge


def _notify(recipient, kind, title, message, *, actor=None, pair=None, push=True):
    try:
        from user_profile.services import _notify as notify

        notify(recipient, kind, title, message, actor=actor, push=push,
               data={"type": kind, "pair_id": str(pair.id) if pair else None, "screen": "study_partner"})
    except Exception:
        logger.exception("partner notification failed kind=%s", kind)


# ----------------------------------------------------------------------------------------------------------------------
# daily jobs (called from tasks.py)
# ----------------------------------------------------------------------------------------------------------------------
def grant_milestones(now=None, limit=2000) -> int:
    """Each partner gets 1 streak freeze (non-cash) at 7 / 30 / 100 shared days. Exactly once per (pair, milestone)."""
    from user_profile.models import Streak

    now = now or timezone.now()
    granted = 0
    for pair in AccountabilityPair.objects.filter(status=Status.ACTIVE).select_related("requester", "partner")[:limit]:
        try:
            ov = overview(pair, pair.requester, now)
            for days in MILESTONES:
                if ov["streak"] < days:
                    continue
                with transaction.atomic():
                    _, created = AccountabilityMilestone.objects.get_or_create(pair=pair, days=days)
                if not created:
                    continue
                for user in (pair.requester, pair.partner):
                    try:
                        Streak.objects.grant_freeze(user, 1)
                    except Exception:
                        logger.exception("partner milestone freeze failed user=%s", user.id)
                    _notify(user, "partner_milestone", f"{days}-day study streak together!",
                            "You and your study partner kept your goal. A streak freeze was added.", pair=pair)
                granted += 1
        except Exception:
            logger.exception("partner milestone failed pair=%s", pair.id)
    return granted


def evening_nudges(now=None, limit=5000) -> int:
    """If exactly one of the two has met today's goal, remind the other one (once a day, only if they allow reminders)."""
    if not conf.partner_auto_nudge():
        return 0
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    today_start = _day_start(today)
    sent = 0
    for pair in AccountabilityPair.objects.filter(status=Status.ACTIVE).select_related("requester", "partner")[:limit]:
        try:
            if AccountabilityNudge.objects.filter(pair=pair, kind=AccountabilityNudge.Kind.AUTO, created_at__gte=today_start).exists():
                continue
            mins = minutes_by_day([pair.requester_id, pair.partner_id], today, today)
            a = mins[str(pair.requester_id)].get(today, 0) >= pair.daily_goal_minutes
            b = mins[str(pair.partner_id)].get(today, 0) >= pair.daily_goal_minutes
            if a == b:
                continue
            behind = pair.partner if a else pair.requester
            if not prefs_for(behind.id).partner_reminders:
                continue
            AccountabilityNudge.objects.create(pair=pair, sender=None, kind=AccountabilityNudge.Kind.AUTO, template="auto_evening")
            _notify(behind, "partner_evening", "Your study partner is done for today",
                    "Finish today's goal to keep your shared streak.", pair=pair)
            sent += 1
        except Exception:
            logger.exception("partner evening nudge failed pair=%s", pair.id)
    return sent
