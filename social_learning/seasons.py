"""
social_learning/seasons.py - leaderboard SEASONS with a real reset and NON-CASH rewards (P-F5).

A season is a fixed window (default 4 weeks, Monday 00:00 IST -> Monday 00:00 IST). Everyone starts a new season at 0 because the
score is computed ONLY from activity dated inside the window - that is the "reset"; nothing is deleted. Old seasons keep their
frozen final standings + rewards (history).

Score (all numbers overridable per season in `StudySeason.config`, see DEFAULT_CONFIG):
    minutes      verified study-room minutes (`message.StudyRoomAttendance.total_seconds`, which only counts real presence, S5)
                 with a per-day cap, so camping in a room 24 h cannot win
  + pomodoros    x pomodoro_points  - finished focus rounds that the server credited (`PomodoroCompletion`)
  + active days  x active_day_points - days with at least `active_day_min_minutes`  (consistency beats one long binge)
Manual / offline minutes NEVER count here (nothing verifies them). Opted-out and inactive accounts are left out.

Rewards when a season closes - badge and/or streak-freeze tokens ONLY (never coins, never cash; `freezes` is capped at 2 per tier
so a config typo cannot hand out a pile). Exactly once per (season, user): the reward row, the badge and the freeze are written in
one transaction, and a failed user is retried by the next tick.

Scoring is INCREMENTAL while a season runs (finished days are cached per user, `_frozen_totals`) and FULL for the final standings.

Lifecycle is driven by `tick()` (Celery every 15 min, or `manage.py social_season_tick`):
    upcoming -> active at starts_at ; active -> provisional standings refreshed ; ended -> finalize + rewards -> closed ;
    next season created automatically (SOCIAL_SEASON_AUTO_CREATE).
"""
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from . import conf
from .models import PomodoroCompletion, SeasonReward, SeasonStanding, SeasonUserTotal, SocialPrefs, StudySeason

logger = logging.getLogger(__name__)

Status = StudySeason.Status

DEFAULT_CONFIG = {
    "daily_cap_minutes": 360,
    "pomodoro_points": 3,
    "active_day_points": 5,
    "active_day_min_minutes": 15,
    "min_score_for_reward": 60,
    "tiers": [
        {"tier": "champion", "rank_from": 1, "rank_to": 1, "badge": "season_champion", "freezes": 2},
        {"tier": "top3", "rank_from": 2, "rank_to": 3, "badge": "season_top3", "freezes": 1},
        {"tier": "top10", "rank_from": 4, "rank_to": 10, "badge": "season_top10", "freezes": 1},
        {"tier": "finisher", "rank_from": 11, "rank_to": None, "badge": "season_finisher", "freezes": 0},
    ],
}
MAX_FREEZES_PER_TIER = 2
ALLOWED_BADGES = {"season_champion", "season_top3", "season_top10", "season_finisher"}
INSERT_BATCH = 2000


# ----------------------------------------------------------------------------------------------------------------------
# config
# ----------------------------------------------------------------------------------------------------------------------
def config_for(season) -> dict:
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_CONFIG.items()}
    raw = season.config if isinstance(season.config, dict) else {}
    for key in ("daily_cap_minutes", "pomodoro_points", "active_day_points", "active_day_min_minutes", "min_score_for_reward"):
        if key in raw:
            try:
                cfg[key] = max(0, int(raw[key]))
            except (TypeError, ValueError):
                pass
    if isinstance(raw.get("tiers"), list):
        cfg["tiers"] = raw["tiers"]
    cfg["tiers"] = _safe_tiers(cfg["tiers"])
    return cfg


def _safe_tiers(tiers) -> list:
    """Drop anything that is not a plain badge/freeze tier. Coins / cash / unknown badge codes are ignored on purpose."""
    out = []
    for t in tiers:
        try:
            rank_from = int(t["rank_from"])
            rank_to = None if t.get("rank_to") in (None, "") else int(t["rank_to"])
            badge = t.get("badge") or ""
            out.append({
                "tier": str(t.get("tier") or f"tier{rank_from}")[:16],
                "rank_from": max(1, rank_from),
                "rank_to": rank_to,
                "badge": badge if badge in ALLOWED_BADGES else "",
                "freezes": max(0, min(MAX_FREEZES_PER_TIER, int(t.get("freezes") or 0))),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda x: x["rank_from"])


def tier_for_rank(rank, tiers):
    for t in tiers:
        if rank >= t["rank_from"] and (t["rank_to"] is None or rank <= t["rank_to"]):
            return t
    return None


def public_rules(season) -> dict:
    """What the app shows on the 'How it works' sheet."""
    cfg = config_for(season)
    return {
        "daily_cap_minutes": cfg["daily_cap_minutes"],
        "pomodoro_points": cfg["pomodoro_points"],
        "active_day_points": cfg["active_day_points"],
        "active_day_min_minutes": cfg["active_day_min_minutes"],
        "min_score_for_reward": cfg["min_score_for_reward"],
        "tiers": [{k: t[k] for k in ("tier", "rank_from", "rank_to", "badge", "freezes")} for t in cfg["tiers"]],
        "cash_rewards": False,
    }


# ----------------------------------------------------------------------------------------------------------------------
# window + scores
# ----------------------------------------------------------------------------------------------------------------------
def window_dates(season):
    """[start_date, end_date) in the project time zone (IST)."""
    return timezone.localtime(season.starts_at).date(), timezone.localtime(season.ends_at).date()


def _partials(cfg, start, end) -> dict:
    """Counted numbers per user from raw activity dated in [start, end): {uid: {minutes, pomodoros, active_days}}.
    The per-day cap is applied per (user, day), so the result is additive over disjoint WHOLE-day ranges."""
    from message.models import StudyRoomAttendance

    cap, active_min = cfg["daily_cap_minutes"], cfg["active_day_min_minutes"]
    out = {}
    if start >= end:
        return out

    per_day = defaultdict(lambda: defaultdict(int))
    rows = (
        StudyRoomAttendance.objects.filter(attended_date__gte=start, attended_date__lt=end, total_seconds__gt=0)
        .values("user_id", "attended_date").annotate(secs=Sum("total_seconds"))
    )
    for row in rows.iterator(chunk_size=5000):
        per_day[row["user_id"]][row["attended_date"]] += int(row["secs"] or 0)
    for uid, days in per_day.items():
        counted = [min(secs // 60, cap) for secs in days.values()]
        out[uid] = {"minutes": sum(counted), "pomodoros": 0, "active_days": sum(1 for m in counted if m >= active_min)}

    pomo = PomodoroCompletion.objects.filter(local_date__gte=start, local_date__lt=end).values("user_id").annotate(n=Count("id"))
    for r in pomo:
        out.setdefault(r["user_id"], {"minutes": 0, "pomodoros": 0, "active_days": 0})["pomodoros"] = r["n"]
    return out


def _rollup_sig(cfg) -> str:
    return f"{cfg['daily_cap_minutes']}:{cfg['active_day_min_minutes']}"


def _frozen_totals(season, cfg, now):
    """(totals, through_date). Sums every finished day (strictly before yesterday's end, i.e. a ONE-day grace so a late
    LiveKit 'left' webhook after midnight is still counted) into SeasonUserTotal once, then only reads that table.
    Changing the scoring numbers mid-season drops the cache and rebuilds it."""
    start, end = window_dates(season)
    target = min(timezone.localdate(now) - timedelta(days=1), end)
    sig = _rollup_sig(cfg)
    with transaction.atomic():
        locked = StudySeason.objects.select_for_update().get(pk=season.pk)
        if locked.rollup_sig != sig:
            SeasonUserTotal.objects.filter(season=locked).delete()
            locked.rollup_through, locked.rollup_sig = None, sig
        frm = locked.rollup_through or start
        if target > frm:
            parts = _partials(cfg, frm, target)
            uids = list(parts)
            for i in range(0, len(uids), 5000):
                chunk = uids[i:i + 5000]
                existing = {t.user_id: t for t in SeasonUserTotal.objects.filter(season=locked, user_id__in=chunk)}
                fresh, changed = [], []
                for uid in chunk:
                    p = parts[uid]
                    row = existing.get(uid)
                    if row is None:
                        fresh.append(SeasonUserTotal(season=locked, user_id=uid, minutes=p["minutes"], pomodoros=p["pomodoros"], active_days=p["active_days"]))
                    else:
                        row.minutes += p["minutes"]
                        row.pomodoros += p["pomodoros"]
                        row.active_days += p["active_days"]
                        changed.append(row)
                if fresh:
                    SeasonUserTotal.objects.bulk_create(fresh)
                if changed:
                    SeasonUserTotal.objects.bulk_update(changed, ["minutes", "pomodoros", "active_days"])
            locked.rollup_through = target
        locked.save(update_fields=["rollup_through", "rollup_sig"])
    totals = {
        uid: {"minutes": m, "pomodoros": p, "active_days": a}
        for uid, m, p, a in SeasonUserTotal.objects.filter(season=season).values_list("user_id", "minutes", "pomodoros", "active_days").iterator(chunk_size=5000)
    }
    return totals, (locked.rollup_through or start)


def compute_scores(season, now=None, incremental=False) -> list:
    """Ranked scores for one season.

    incremental=False (default; used for the FINAL standings and by tests): every number comes straight from attendance.
    incremental=True (the 15-minute refresh): finished days come from the SeasonUserTotal cache, only the last day or two
    (the days after `rollup_through`) are scanned - the same result, far less work at scale."""
    cfg = config_for(season)
    start, end = window_dates(season)
    if incremental:
        base, through = _frozen_totals(season, cfg, now or timezone.now())
        live = _partials(cfg, through, end)
    else:
        base, live = {}, _partials(cfg, start, end)

    merged = {}
    for source in (base, live):
        for uid, p in source.items():
            cur = merged.setdefault(uid, {"minutes": 0, "pomodoros": 0, "active_days": 0})
            for key in cur:
                cur[key] += p[key]

    valid = _eligible_ids(set(merged))
    scored = []
    for uid in set(merged) & valid:
        m, p, a = merged[uid]["minutes"], merged[uid]["pomodoros"], merged[uid]["active_days"]
        score = m + p * cfg["pomodoro_points"] + a * cfg["active_day_points"]
        if score > 0:
            scored.append({"user_id": uid, "score": score, "minutes": m, "pomodoros": p, "active_days": a})
    # deterministic: higher score, then more active days, then a stable id order
    scored.sort(key=lambda r: (-r["score"], -r["active_days"], str(r["user_id"])))
    for index, row in enumerate(scored, start=1):
        row["rank"] = index
    return scored


def _eligible_ids(candidates) -> set:
    from django.contrib.auth import get_user_model

    if not candidates:
        return set()
    opted_out = set(SocialPrefs.objects.filter(season_opt_out=True, user_id__in=list(candidates)).values_list("user_id", flat=True)) if len(candidates) < 50000 \
        else set(SocialPrefs.objects.filter(season_opt_out=True).values_list("user_id", flat=True))
    ids = list(candidates - opted_out)
    valid = set()
    User = get_user_model()
    for i in range(0, len(ids), 5000):
        valid.update(User.objects.filter(id__in=ids[i:i + 5000], is_active=True).values_list("id", flat=True))
    return valid


def refresh_standings(season, final=False, now=None) -> int:
    """Rewrite the standings of ONE season. Refuses to touch a closed season. Provisional refreshes are incremental
    (cache of finished days); the FINAL one always recomputes everything from attendance, so a stale cache can never
    change who wins or who gets a reward."""
    if season.status == Status.CLOSED:
        return 0
    scored = compute_scores(season, now=now, incremental=not final)
    with transaction.atomic():
        SeasonStanding.objects.filter(season=season).delete()
        batch = []
        for row in scored:
            batch.append(SeasonStanding(
                season=season, user_id=row["user_id"], score=row["score"], rank=row["rank"],
                minutes=row["minutes"], pomodoros=row["pomodoros"], active_days=row["active_days"], is_final=final,
            ))
            if len(batch) >= INSERT_BATCH:
                SeasonStanding.objects.bulk_create(batch)
                batch = []
        if batch:
            SeasonStanding.objects.bulk_create(batch)
    return len(scored)


# ----------------------------------------------------------------------------------------------------------------------
# lifecycle
# ----------------------------------------------------------------------------------------------------------------------
def _monday_start(dt):
    local = timezone.localtime(dt)
    monday = local.date() - timedelta(days=local.weekday())
    return timezone.make_aware(datetime.combine(monday, time.min))


def current_season(now=None):
    now = now or timezone.now()
    return StudySeason.objects.filter(starts_at__lte=now, ends_at__gt=now).exclude(status=Status.CLOSED).order_by("-starts_at").first()


def next_season_after(now=None):
    now = now or timezone.now()
    return StudySeason.objects.filter(starts_at__gt=now).order_by("starts_at").first()


def create_season(starts_at, days=None, title=None, config=None):
    last = StudySeason.objects.order_by("-number").first()
    number = (last.number + 1) if last else 1
    return StudySeason.objects.create(
        number=number, title=title or f"Study Season {number}", starts_at=starts_at,
        ends_at=starts_at + timedelta(days=days or conf.season_days()), status=Status.UPCOMING, config=config or {},
    )


def ensure_seasons(now=None):
    """Keep one season running and one queued. Safe to call often (unique `number` + atomic)."""
    if not conf.season_auto_create():
        return None
    now = now or timezone.now()
    created = None
    with transaction.atomic():
        latest = StudySeason.objects.select_for_update().order_by("-ends_at").first()
        if latest is None or latest.ends_at <= now:
            start = _monday_start(now) if latest is None else max(latest.ends_at, _monday_start(now))
            created = create_season(start)
        elif current_season(now) is not None and next_season_after(now) is None and latest.ends_at - now <= timedelta(days=7):
            created = create_season(latest.ends_at)
    return created


def finalize_season(season) -> dict:
    """Freeze the standings, hand out rewards, close the season. Idempotent: a second call is a no-op."""
    with transaction.atomic():
        locked = StudySeason.objects.select_for_update().get(pk=season.pk)
        if locked.status == Status.CLOSED:
            return {"already_closed": True}
        refresh_standings(locked, final=True)
    summary = grant_rewards(locked)               # outside the big lock: one small transaction per user
    if summary["failed"]:
        logger.warning("season %s: %s reward(s) failed, will retry on the next tick", locked.number, summary["failed"])
        return summary                            # stays 'active'; next tick retries only what is missing
    with transaction.atomic():
        locked = StudySeason.objects.select_for_update().get(pk=season.pk)
        locked.status = Status.CLOSED
        locked.finalized_at = timezone.now()
        locked.save(update_fields=["status", "finalized_at"])
        SeasonUserTotal.objects.filter(season=locked).delete()   # cache only; the frozen standings are the record
    return summary


def grant_rewards(season) -> dict:
    from user_profile.models import Streak
    from user_profile.services import award_badge

    cfg = config_for(season)
    tiers, min_score = cfg["tiers"], cfg["min_score_for_reward"]
    given = failed = 0
    done = set(SeasonReward.objects.filter(season=season).values_list("user_id", flat=True))
    for st in SeasonStanding.objects.filter(season=season, score__gte=min_score).select_related("user").order_by("rank").iterator(chunk_size=1000):
        if st.user_id in done:
            continue
        tier = tier_for_rank(st.rank, tiers)
        if tier is None or (not tier["badge"] and not tier["freezes"]):
            continue
        try:
            with transaction.atomic():
                reward = SeasonReward.objects.create(
                    season=season, user_id=st.user_id, tier=tier["tier"], rank=st.rank, score=st.score,
                    badge_code=tier["badge"], freezes_requested=tier["freezes"],
                )
                if tier["badge"]:
                    award_badge(st.user_id, tier["badge"], context={"season": season.number, "rank": st.rank}, notify=False)
                if tier["freezes"]:
                    reward.freezes_granted = Streak.objects.grant_freeze(st.user, tier["freezes"])
                    reward.save(update_fields=["freezes_granted"])
            given += 1
            _notify_reward(season, reward)
        except Exception:
            failed += 1
            logger.exception("season %s reward failed user=%s", season.number, st.user_id)
    return {"given": given, "failed": failed}


_TIER_TEXT = {
    "champion": "You won the season!",
    "top3": "You finished in the top 3!",
    "top10": "You finished in the top 10!",
    "finisher": "You finished the season. Great consistency!",
}


def _notify_reward(season, reward):
    try:
        from user_profile.services import _notify

        extra = f" +{reward.freezes_granted} streak freeze" if reward.freezes_granted else ""
        _notify(reward.user_id, "season_reward", f"{season.title} is over",
                f"{_TIER_TEXT.get(reward.tier, 'Season complete.')} Rank #{reward.rank}.{extra}", push=True,
                data={"type": "season_reward", "season_id": str(season.id), "screen": "study_season"})
    except Exception:
        logger.exception("season reward notification failed")


def tick(now=None) -> dict:
    """The one periodic job. Never raises (each step is guarded) so one bad season cannot stop the rest."""
    now = now or timezone.now()
    out = {"activated": 0, "refreshed": 0, "closed": 0, "created": 0}
    try:
        out["activated"] = StudySeason.objects.filter(status=Status.UPCOMING, starts_at__lte=now, ends_at__gt=now).update(status=Status.ACTIVE)
        for season in StudySeason.objects.filter(status=Status.ACTIVE, ends_at__lte=now).order_by("ends_at"):
            try:
                result = finalize_season(season)
                if not result.get("failed"):
                    out["closed"] += 1
            except Exception:
                logger.exception("finalize season %s failed", season.number)
        active = current_season(now)
        if active is not None:
            out["refreshed"] = refresh_standings(active)
        if ensure_seasons(now) is not None:
            out["created"] = 1
    except Exception:
        logger.exception("season tick failed")
    return out


# ----------------------------------------------------------------------------------------------------------------------
# read helpers (views)
# ----------------------------------------------------------------------------------------------------------------------
def my_standing(season, user):
    st = SeasonStanding.objects.filter(season=season, user=user).first()
    if st is None:
        return None
    above = SeasonStanding.objects.filter(season=season, rank=st.rank - 1).values_list("score", flat=True).first() if st.rank > 1 else None
    return {
        "rank": st.rank, "score": st.score, "minutes": st.minutes, "pomodoros": st.pomodoros, "active_days": st.active_days,
        "points_to_pass_next": (above - st.score + 1) if above is not None else 0,
        "is_final": st.is_final,
    }
