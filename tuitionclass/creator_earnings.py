"""
tuitionclass/creator_earnings.py - P-F9_F10 earnings dashboard in ONE cached call.  (written, NOT RUN)

    GET /tuitionclass/creator-earnings/?days=7|30|90   (tuitionclass/creator_earnings_views.py)

Sources (coins, nothing new is stored):
  * class earnings     PassDailyCharge.amount for classrooms the caller TEACHES (the escrow release ledger - the same
                       source as /tuitionclass/my-earnings/, so the two screens can never disagree)
  * referral earnings  ReferralCommission.commission_coins, status PAID only (BLOCKED rows are fraud-held, never money)
Each is returned for the window and the previous window of the same length (+ change %), a daily series, and a
by-source breakdown. Test-series sales are NOT included. Coins only - no INR is computed here.
Cached CACHE_SECONDS per (user, days); a cache failure never breaks the endpoint.
Pure helpers (`clamp_days`, `change_pct`, `window_dates`, `fill`) keep the arithmetic testable without a database.
"""
import logging
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
MAX_DAYS = 90
CACHE_SECONDS = 600
CACHE_VERSION = "v1"
TOP_CLASSROOMS = 5


def cache_key(user_id, days):
    return f"creator_earnings:{CACHE_VERSION}:{user_id}:{days}"


def clamp_days(raw, default=DEFAULT_DAYS):
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return default
    return min(MAX_DAYS, max(1, days))


def change_pct(current, previous):
    if not previous:
        return None
    return round((float(current) - float(previous)) / float(previous) * 100.0, 1)


def window_dates(today, days):
    """(start, prev_start): last `days` days INCLUDING today; the previous window is the `days` before it."""
    start = today - timedelta(days=days - 1)
    return start, start - timedelta(days=days)


def fill(start, days, rows):
    """[{date, class_coins, referral_coins}] for every day; rows = {date: {key: n}}."""
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        row = rows.get(d, {})
        out.append({
            "date": d.isoformat(),
            "class_coins": int(row.get("class_coins", 0)),
            "referral_coins": int(row.get("referral_coins", 0)),
        })
    return out


def _class_charges(user):
    from .models import PassDailyCharge

    return PassDailyCharge.objects.filter(purchase__class_pass__classroom__teacher=user)


def _referral_rows(user):
    from .models import ReferralCommission

    return ReferralCommission.objects.filter(referrer=user, status=ReferralCommission.Status.PAID)


def build(user, days=DEFAULT_DAYS):
    days = clamp_days(days)
    today = timezone.localdate()
    start, prev_start = window_dates(today, days)

    charges = _class_charges(user)
    refs = _referral_rows(user)

    cls_cur = charges.filter(date__gte=start)
    cls_prev = charges.filter(date__gte=prev_start, date__lt=start)
    ref_cur = refs.filter(created_at__date__gte=start)
    ref_prev = refs.filter(created_at__date__gte=prev_start, created_at__date__lt=start)

    class_now = int(cls_cur.aggregate(t=Sum("amount"))["t"] or 0)
    class_prev = int(cls_prev.aggregate(t=Sum("amount"))["t"] or 0)
    ref_now = int(ref_cur.aggregate(t=Sum("commission_coins"))["t"] or 0)
    ref_prev_total = int(ref_prev.aggregate(t=Sum("commission_coins"))["t"] or 0)

    rows = {}
    for r in cls_cur.values("date").annotate(n=Sum("amount")):
        rows.setdefault(r["date"], {})["class_coins"] = int(r["n"] or 0)
    for r in ref_cur.annotate(d=TruncDate("created_at")).values("d").annotate(n=Sum("commission_coins")):
        rows.setdefault(r["d"], {})["referral_coins"] = int(r["n"] or 0)

    by_classroom = [
        {
            "classroom_id": r["classroom_id"], "title": r["classroom_title"] or "",
            "coins": int(r["coins"] or 0), "sessions": int(r["sessions"] or 0),
        }
        for r in cls_cur.values(
            classroom_id=F("purchase__class_pass__classroom_id"),
            classroom_title=F("purchase__class_pass__classroom__title"),
        ).annotate(coins=Sum("amount"), sessions=Count("id")).order_by("-coins")[:TOP_CLASSROOMS]
    ]
    by_kind = {
        r["kind"]: {"coins": int(r["coins"] or 0), "payouts": int(r["payouts"] or 0)}
        for r in ref_cur.values("kind").annotate(coins=Sum("commission_coins"), payouts=Count("id"))
    }

    total_now = class_now + ref_now
    total_prev = class_prev + ref_prev_total
    return {
        "days": days,
        "unit": "coins",
        "generated_at": timezone.now().isoformat(),
        "window_start": start.isoformat(),
        "window_end": today.isoformat(),
        "is_teacher": charges.exists() or cls_cur.exists(),
        "has_referral": refs.exists(),
        "totals": {
            "total": total_now, "prev_total": total_prev, "change_pct": change_pct(total_now, total_prev),
            "class_coins": class_now, "prev_class_coins": class_prev, "class_change_pct": change_pct(class_now, class_prev),
            "referral_coins": ref_now, "prev_referral_coins": ref_prev_total,
            "referral_change_pct": change_pct(ref_now, ref_prev_total),
        },
        "all_time": {
            "class_coins": int(charges.aggregate(t=Sum("amount"))["t"] or 0),
            "referral_coins": int(refs.aggregate(t=Sum("commission_coins"))["t"] or 0),
        },
        "daily": fill(start, days, rows),
        "by_classroom": by_classroom,
        "referral_by_kind": by_kind,
        "notes": {"testseries_sales_included": False, "blocked_referrals_excluded": True},
        "cache_seconds": CACHE_SECONDS,
    }


def get(user, days=DEFAULT_DAYS, use_cache=True):
    days = clamp_days(days)
    key = cache_key(user.pk, days)
    if use_cache:
        try:
            hit = cache.get(key)
            if hit is not None:
                return hit
        except Exception:
            logger.warning("creator earnings: cache read failed", exc_info=True)
    data = build(user, days)
    if use_cache:
        try:
            cache.set(key, data, CACHE_SECONDS)
        except Exception:
            logger.warning("creator earnings: cache write failed", exc_info=True)
    return data
