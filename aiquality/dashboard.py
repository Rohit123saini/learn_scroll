"""aiquality/dashboard.py - P-F6: the numbers behind the staff COST GUARD screen.

    cost_dashboard(days)  -> today vs budget, per-day, per-feature (calls, failures, latency, tokens, cost, cache hits),
                             top spenders, and the existing composer-helper global daily cap.

Cost is an estimate (see telemetry.py). Budget = settings.AI_DAILY_BUDGET_USD (0 = no budget set, no alert).
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from . import telemetry
from .models import AiCallLog


def _usd(micro):
    return round((micro or 0) / 1_000_000, 4)


def _budget():
    try:
        return max(float(getattr(settings, "AI_DAILY_BUDGET_USD", 0) or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def alert_level(spent_usd, budget_usd):
    """none | warn (>= 80 %) | over (>= 100 %)"""
    if budget_usd <= 0:
        return "none"
    ratio = spent_usd / budget_usd
    return "over" if ratio >= 1 else "warn" if ratio >= 0.8 else "none"


def today_spend_micro():
    start = timezone.localtime(timezone.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    return AiCallLog.objects.filter(created_at__gte=start).aggregate(s=Sum("cost_micro_usd"))["s"] or 0


def cost_dashboard(days=7, top_users=10):
    days = max(1, min(int(days), 30))
    now = timezone.now()
    local_now = timezone.localtime(now)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = today_start - timedelta(days=days - 1)
    qs = AiCallLog.objects.filter(created_at__gte=since)

    budget = _budget()
    today_qs = AiCallLog.objects.filter(created_at__gte=today_start)
    t = today_qs.aggregate(calls=Count("id"), failed=Count("id", filter=Q(ok=False)),
                           cost=Sum("cost_micro_usd"), tin=Sum("tokens_in"), tout=Sum("tokens_out"))
    spent = _usd(t["cost"])
    today = {
        "calls": t["calls"] or 0, "failed": t["failed"] or 0,
        "tokens_in": t["tin"] or 0, "tokens_out": t["tout"] or 0,
        "cost_usd": spent, "budget_usd": budget,
        "budget_used_pct": round(spent / budget * 100, 1) if budget else None,
        "alert": alert_level(spent, budget),
    }

    per_day = []
    for r in (qs.annotate(d=TruncDate("created_at", tzinfo=local_now.tzinfo))
              .values("d").annotate(calls=Count("id"), failed=Count("id", filter=Q(ok=False)), cost=Sum("cost_micro_usd"))
              .order_by("d")):
        per_day.append({"date": r["d"].isoformat(), "calls": r["calls"], "failed": r["failed"], "cost_usd": _usd(r["cost"])})

    day_keys = [(today_start - timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]
    features = []
    for r in (qs.values("surface", "feature")
              .annotate(calls=Count("id"), failed=Count("id", filter=Q(ok=False)), lat=Avg("latency_ms"),
                        tin=Sum("tokens_in"), tout=Sum("tokens_out"), cost=Sum("cost_micro_usd"),
                        estimated=Count("id", filter=Q(tokens_estimated=True)))
              .order_by("-cost")):
        hits = sum(telemetry.cache_hits(r["surface"], r["feature"], d) for d in day_keys)
        served = r["calls"] + hits
        features.append({
            "surface": r["surface"], "feature": r["feature"], "calls": r["calls"], "failed": r["failed"],
            "fail_rate": round(r["failed"] / r["calls"], 3) if r["calls"] else 0.0,
            "avg_latency_ms": int(r["lat"] or 0), "tokens_in": r["tin"] or 0, "tokens_out": r["tout"] or 0,
            "cost_usd": _usd(r["cost"]),
            "cost_per_call_usd": round(_usd(r["cost"]) / r["calls"], 5) if r["calls"] else 0.0,
            "cache_hits": hits, "cache_hit_rate": round(hits / served, 3) if served else 0.0,
            "estimated_tokens_share": round(r["estimated"] / r["calls"], 2) if r["calls"] else 0.0,
        })

    spenders = []
    for r in (qs.exclude(user__isnull=True).values("user_id", "user__username")
              .annotate(calls=Count("id"), cost=Sum("cost_micro_usd")).order_by("-cost")[:top_users]):
        spenders.append({"user_id": r["user_id"], "username": r["user__username"] or "", "calls": r["calls"], "cost_usd": _usd(r["cost"])})

    guard = {}
    try:
        from post import ai_assist_guard as g
        used = g._read(g._global_key())
        cap = g.global_daily_calls()
        guard = {"composer_calls_today": used, "composer_daily_cap": cap,
                 "composer_cap_used_pct": round(used / cap * 100, 1) if cap else None}
    except Exception:  # the dashboard must open even when the guard module changes
        guard = {}

    return {"days": days, "today": today, "per_day": per_day, "features": features, "top_users": spenders,
            "guard": guard, "price_per_m_usd": {"input": float(getattr(settings, "AI_PRICE_INPUT_PER_M_USD", 0.30)),
                                                "output": float(getattr(settings, "AI_PRICE_OUTPUT_PER_M_USD", 2.50))},
            "estimate_note": "Cost is an estimate from token counts x the configured price per 1M tokens."}
