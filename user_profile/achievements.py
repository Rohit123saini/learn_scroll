"""
user_profile/achievements.py - P3 (Achievements + learning identity). NOT RUN, no migration needed.

Everything the profile "Achievements" screen shows, in one place:

  * badge catalogue + HOW TO EARN + PROGRESS (progress bars)      -> owner only for the locked ones
  * streak numbers + calendar heat-map (last 26 weeks) + study time -> OWNER ONLY (a child's study hours are
                                                                       never shown to visitors, same rule as the
                                                                       app-wide study-time board, S5)
  * "strong in" subject chips (auto, from checked test attempts)   -> visitors see subject + level, not the %
  * leaderboard rank chip (app-wide engagement board)              -> hidden from visitors for minors
  * certificate count + latest weekly recap pointer

No model changes: it only READS Badge / UserBadge / Streak / DailyUsage / TestAttempt / LeaderboardEntry /
Referral / WeeklyRecap. Heavy imports from other apps are function-local (same circular-import rule as the rest
of this app, see activity.py / recap.py).

Pure helpers (`heat_level`, `percent`, `skill_level`, `earned_note`) have no DB access so they can be unit tested
without fixtures.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Avg, Count, Max, Min, Sum
from django.db.models.functions import Lower
from django.utils import timezone

logger = logging.getLogger(__name__)

HEATMAP_WEEKS = 26
# seconds per day -> level 0..4 (GitHub-style). Level 0 = no usage, 4 = an hour or more.
HEAT_THRESHOLDS = (1, 10 * 60, 30 * 60, 60 * 60)
SKILL_LIMIT = 6


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------
def heat_level(seconds) -> int:
    """0 = nothing, 1 = under 10 min, 2 = under 30 min, 3 = under 60 min, 4 = an hour or more."""
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        return 0
    if s < HEAT_THRESHOLDS[0]:
        return 0
    level = 1
    for threshold in HEAT_THRESHOLDS[1:]:
        if s >= threshold:
            level += 1
    return level


def percent(current, target) -> int:
    """0..100, never above 100, never negative; a missing/zero target counts as 0."""
    try:
        current, target = int(current or 0), int(target or 0)
    except (TypeError, ValueError):
        return 0
    if target <= 0 or current <= 0:
        return 0
    return max(0, min(100, int(current * 100 / target)))


def skill_level(avg_percent: float, strong_at: float = 80.0) -> str:
    return "strong" if avg_percent >= strong_at else "good"


def earned_note(code: str, context) -> str:
    """One English line about THIS award (from `UserBadge.context`). '' when there is nothing specific to say."""
    ctx = context if isinstance(context, dict) else {}
    if code == "weekly_top10" and ctx.get("rank"):
        week = f" in week {ctx['week']}" if ctx.get("week") else ""
        return f"Ranked #{ctx['rank']}{week}."
    if code in ("referrer_first", "referrer_5") and ctx.get("referrals"):
        return f"{ctx['referrals']} friend(s) joined with your invite."
    if code == "founding_member":
        reason = ctx.get("reason")
        if reason == "referee":
            return "You joined through a friend's invite."
        if reason == "referrer":
            return "You brought enough friends to LearnScroll."
        if reason == "campus_invite":
            return "You joined through a campus invite."
    return ""


def week_start_for(day):
    return day - timedelta(days=day.weekday())


# ---------------------------------------------------------------------------
# badge catalogue + progress
# ---------------------------------------------------------------------------
# How a user earns each badge (English; the app has Hindi for the built-in codes and falls back to this text).
HOW_TO = {
    "streak_7": "Open LearnScroll 7 days in a row.",
    "streak_30": "Open LearnScroll 30 days in a row.",
    "weekly_top10": "Finish in the top 10 of the weekly leaderboard (posts, likes and comments count).",
    "first_test": "Complete any test and get it checked.",
    "teacher_verified": "Teachers are verified by the LearnScroll team.",
    "referrer_first": "Invite a friend who joins with your code.",
    "referrer_5": "Invite 5 friends who join with your code.",
    "founding_member": "Join through an invite while the founding-member window is open.",
}


def _catalog():
    """[(code, title, description, icon, sort_order)] - DB rows win, built-in defaults fill in codes that were never
    seeded (a badge row is only created the first time it is awarded)."""
    from .models import Badge
    from .services import BADGE_DEFAULTS

    items = {}
    for code, d in BADGE_DEFAULTS.items():
        items[code] = {
            "code": code, "title": d["title"], "description": d.get("description", ""),
            "icon": d.get("icon", ""), "sort_order": d.get("sort_order", 0), "rule_type": code,
        }
    inactive = set()
    for b in Badge.objects.all():
        if not b.is_active:
            inactive.add(b.code)
            continue
        items[b.code] = {
            "code": b.code, "title": b.title, "description": b.description, "icon": b.icon,
            "sort_order": b.sort_order, "rule_type": b.rule_type,
        }
    for code in inactive:
        items.pop(code, None)
    return sorted(items.values(), key=lambda x: (x["sort_order"], x["code"]))


def _progress_inputs(user) -> dict:
    """The few numbers every progress bar is computed from. One small query each, all by indexed user id."""
    from .models import Streak

    data = {"longest_streak": 0, "checked_tests": 0, "referrals": 0, "best_weekly_rank": None}

    streak = Streak.objects.filter(user=user).values("longest_streak").first()
    if streak:
        data["longest_streak"] = int(streak["longest_streak"] or 0)

    try:
        from testseries.models import TestAttempt

        data["checked_tests"] = TestAttempt.objects.filter(
            student=user, status=TestAttempt.Status.CHECKED,
        ).count()
    except Exception:
        logger.exception("achievements: checked-test count failed")

    try:
        from tuitionclass.models import Referral

        data["referrals"] = Referral.objects.filter(referrer=user).count()
    except Exception:
        logger.exception("achievements: referral count failed")

    try:
        from leaderboard.models import LeaderboardEntry

        data["best_weekly_rank"] = LeaderboardEntry.objects.filter(
            user=user, scope_type=LeaderboardEntry.ScopeType.ENGAGEMENT, scope_id__isnull=True,
            period_type=LeaderboardEntry.PeriodType.WEEKLY,
        ).aggregate(m=Min("rank"))["m"]
    except Exception:
        logger.exception("achievements: best weekly rank failed")
    return data


def _count(current, target, unit):
    return {"kind": "count", "current": min(int(current), int(target)), "target": int(target),
            "percent": percent(current, target), "unit": unit, "available": True}


def badge_progress(code: str, inputs: dict, user) -> dict | None:
    """Progress block for a NOT-yet-earned badge. `kind`:
         count - bar of current/target (days, tests, friends)
         rank  - lower is better: `current` = my best weekly rank (None = never ranked), `target` = top N
         flag  - yes/no, nothing the user can do to speed it up (teacher verification)
       `available` False = the badge can no longer be earned (founding window closed)."""
    from .services import founding_window_open, teacher_verified_field

    if code == "streak_7":
        return _count(inputs["longest_streak"], 7, "days")
    if code == "streak_30":
        return _count(inputs["longest_streak"], 30, "days")
    if code == "first_test":
        return _count(inputs["checked_tests"], 1, "tests")
    if code == "referrer_first":
        return _count(inputs["referrals"], int(getattr(settings, "REFERRER_BADGE_FIRST_AT", 1)), "friends")
    if code == "referrer_5":
        return _count(inputs["referrals"], int(getattr(settings, "REFERRER_BADGE_COMMUNITY_AT", 5)), "friends")
    if code == "weekly_top10":
        best = inputs["best_weekly_rank"]
        top_n = int(getattr(settings, "BADGE_WEEKLY_TOP_N", 10))
        return {"kind": "rank", "current": best, "target": top_n, "percent": 0, "unit": "rank", "available": True}
    if code == "teacher_verified":
        has = bool(getattr(user, teacher_verified_field(), False))
        return {"kind": "flag", "current": 1 if has else 0, "target": 1, "percent": 100 if has else 0,
                "unit": "", "available": True}
    if code == "founding_member":
        try:
            open_ = bool(founding_window_open())
        except Exception:
            open_ = False
        need = int(getattr(settings, "FOUNDING_MEMBER_REFERRALS_NEEDED", 3))
        block = _count(inputs["referrals"], need, "friends")
        block["available"] = open_
        return block
    return None


def badges_payload(user, *, include_locked: bool) -> list:
    """Earned badges (newest first) and - for the owner - the locked ones with progress, in catalogue order."""
    from .models import UserBadge

    earned = {
        ub.badge.code: ub
        for ub in UserBadge.objects.filter(user=user, badge__is_active=True).select_related("badge")
    }
    catalog = {c["code"]: c for c in _catalog()}
    out = []

    for code, ub in sorted(earned.items(), key=lambda kv: kv[1].earned_at, reverse=True):
        meta = catalog.get(code) or {
            "code": code, "title": ub.badge.title, "description": ub.badge.description,
            "icon": ub.badge.icon, "rule_type": ub.badge.rule_type,
        }
        out.append({
            "code": code, "title": meta["title"], "description": meta["description"], "icon": meta["icon"],
            "rule_type": meta["rule_type"], "earned": True, "earned_at": ub.earned_at.isoformat(),
            "how_to": HOW_TO.get(code, ""), "earned_note": earned_note(code, ub.context),
            "progress": {"kind": "done", "current": 1, "target": 1, "percent": 100, "unit": "", "available": True},
        })

    if include_locked:
        inputs = _progress_inputs(user)
        for code, meta in catalog.items():
            if code in earned:
                continue
            out.append({
                "code": code, "title": meta["title"], "description": meta["description"], "icon": meta["icon"],
                "rule_type": meta["rule_type"], "earned": False, "earned_at": None,
                "how_to": HOW_TO.get(code, ""), "earned_note": "",
                "progress": badge_progress(code, inputs, user),
            })
    return out


# ---------------------------------------------------------------------------
# streak + heat-map + study time (OWNER ONLY)
# ---------------------------------------------------------------------------
def streak_payload(user) -> dict:
    from .models import Streak

    s = Streak.objects.filter(user=user).first()
    if s is None:
        return {"current": 0, "longest": 0, "total_active_days": 0, "last_active_date": None, "freeze_tokens": 0}
    return {
        "current": s.current_streak, "longest": s.longest_streak, "total_active_days": s.total_active_days,
        "last_active_date": s.last_active_date.isoformat() if s.last_active_date else None,
        "freeze_tokens": s.freeze_tokens,
    }


def heatmap_and_study_time(user, today=None):
    """(heatmap, study_time) from ONE ranged DailyUsage query (+ one SUM for the all-time total).

    The grid starts on the Monday `HEATMAP_WEEKS - 1` weeks ago so the client can draw full week columns.
    Only days with usage are listed; the client fills the empty cells."""
    from .models import DailyUsage

    today = today or timezone.localdate()
    start = week_start_for(today) - timedelta(weeks=HEATMAP_WEEKS - 1)
    rows = list(
        DailyUsage.objects.filter(user=user, date__gte=start, date__lte=today)
        .values_list("date", "seconds").order_by("date")
    )

    days = [{"date": d.isoformat(), "seconds": int(s), "level": heat_level(s)} for d, s in rows if s and s > 0]

    week_start = week_start_for(today)
    last30 = today - timedelta(days=29)
    this_week = sum(int(s) for d, s in rows if d >= week_start)
    last_30 = sum(int(s) for d, s in rows if d >= last30)
    active_30 = sum(1 for d, s in rows if d >= last30 and s and s > 0)
    best = max(((int(s), d) for d, s in rows if s), default=None)
    total = DailyUsage.objects.filter(user=user).aggregate(t=Sum("seconds"))["t"] or 0

    heatmap = {
        "start": start.isoformat(), "end": today.isoformat(), "weeks": HEATMAP_WEEKS,
        "thresholds": list(HEAT_THRESHOLDS), "active_days": len(days), "days": days,
    }
    study = {
        "this_week_seconds": this_week,
        "last_30_days_seconds": last_30,
        "total_seconds": int(total),
        "avg_active_day_seconds": int(last_30 / active_30) if active_30 else 0,
        "best_day": {"date": best[1].isoformat(), "seconds": best[0]} if best else None,
    }
    return heatmap, study


# ---------------------------------------------------------------------------
# skills ("strong in"), rank chip, certificates, recap
# ---------------------------------------------------------------------------
def skill_chips(user, *, with_numbers: bool, limit: int = SKILL_LIMIT) -> list:
    """Subjects the user is doing well in, from CHECKED attempts on non-campus test series only (school/campus
    results are private to the campus and never become a public chip). A subject needs
    PROFILE_SKILL_MIN_ATTEMPTS (default 2) checked attempts and an average of PROFILE_SKILL_MIN_AVG (default 60)%.
    Visitors get the subject + level; the owner also gets the numbers."""
    try:
        from testseries.models import TestAttempt, TestSeries
    except Exception:
        return []

    min_attempts = int(getattr(settings, "PROFILE_SKILL_MIN_ATTEMPTS", 2))
    min_avg = float(getattr(settings, "PROFILE_SKILL_MIN_AVG", 60))
    strong_at = float(getattr(settings, "PROFILE_SKILL_STRONG_AT", 80))

    rows = (
        TestAttempt.objects.filter(student=user, status=TestAttempt.Status.CHECKED, percentage__isnull=False)
        .exclude(series__source=TestSeries.Source.CAMPUS)
        .exclude(series__subject="")
        .annotate(subject_key=Lower("series__subject"))
        .values("subject_key")
        .annotate(n=Count("id"), avg=Avg("percentage"), best=Max("percentage"))
    )
    chips = []
    for r in rows:
        avg = float(r["avg"] or 0)
        if r["n"] < min_attempts or avg < min_avg:
            continue
        chip = {"subject": str(r["subject_key"]).strip().title(), "level": skill_level(avg, strong_at)}
        if with_numbers:
            chip.update({"attempts": int(r["n"]), "avg_percent": round(avg, 1), "best_percent": round(float(r["best"] or 0), 1)})
        chips.append((avg, r["n"], chip))
    chips.sort(key=lambda t: (-t[0], -t[1], t[2]["subject"]))
    return [c for _, _, c in chips[:limit]]


def rank_chip(user) -> dict:
    """App-wide ENGAGEMENT board rank, weekly + all-time. A rank worse than PROFILE_RANK_CHIP_MAX (default 100) is
    not shown (\"#48213\" is not an achievement). Reads the table the leaderboard tasks wrote; never recomputes."""
    out = {"weekly": None, "all_time": None}
    try:
        from leaderboard import services as lb
        from leaderboard.models import LeaderboardEntry

        cap = int(getattr(settings, "PROFILE_RANK_CHIP_MAX", 100))
        for key, period, period_key in (
            ("weekly", LeaderboardEntry.PeriodType.WEEKLY, lb.current_week_key()),
            ("all_time", LeaderboardEntry.PeriodType.ALL_TIME, "all"),
        ):
            row = lb.get_my_rank(user, LeaderboardEntry.ScopeType.ENGAGEMENT, None, period, period_key)
            if row is not None and 0 < row.rank <= cap:
                out[key] = {"rank": int(row.rank), "score": float(row.score), "period_key": period_key}
    except Exception:
        logger.exception("achievements: rank chip failed (user=%s)", getattr(user, "pk", None))
    return out


def certificates_summary(user) -> dict:
    try:
        from testseries.models import TestCertificate

        return {"count": TestCertificate.objects.filter(student=user, revoked_at__isnull=True).count()}
    except Exception:
        logger.exception("achievements: certificate count failed")
        return {"count": 0}


def latest_recap_pointer(user):
    from .models import WeeklyRecap

    r = WeeklyRecap.objects.filter(user=user).order_by("-week_start").values("id", "week_start", "week_end").first()
    if not r:
        return None
    return {"id": r["id"], "week_start": r["week_start"].isoformat(), "week_end": r["week_end"].isoformat()}


# ---------------------------------------------------------------------------
# visibility + the one entry point the view calls
# ---------------------------------------------------------------------------
def viewer_may_see(owner, viewer) -> bool:
    """Same gate as the owner's posts / certificates: a private account shows nothing until the viewer follows.
    (A block is handled by the view as a 404 before this is called.)"""
    if viewer.pk == owner.pk or not getattr(owner, "is_private", False):
        return True
    from .models import Follow

    return Follow.objects.filter(follower=viewer, following=owner, status=Follow.Status.ACCEPTED).exists()


def build(owner, viewer) -> dict:
    is_self = owner.pk == viewer.pk
    base = {"username": owner.username, "is_self": is_self, "locked": False}

    if not viewer_may_see(owner, viewer):
        return {**base, "locked": True, "badges": [], "skills": [], "rank": {"weekly": None, "all_time": None},
                "certificates": {"count": 0}}

    from .privacy import is_minor_user

    mask_social = (not is_self) and is_minor_user(owner)  # minors: no rank / subject strengths for visitors

    data = {
        **base,
        "badges": badges_payload(owner, include_locked=is_self),
        "skills": [] if mask_social else skill_chips(owner, with_numbers=is_self),
        "rank": {"weekly": None, "all_time": None} if mask_social else rank_chip(owner),
        "certificates": certificates_summary(owner),
    }
    if is_self:
        heatmap, study = heatmap_and_study_time(owner)
        data.update({
            "streak": streak_payload(owner),
            "heatmap": heatmap,
            "study_time": study,
            "latest_recap": latest_recap_pointer(owner),
        })
    return data
