"""
copyrights/transparency.py - the public TRANSPARENCY REPORT: how many copyright claims came in, how much
content was removed, how much was restored (and the counter-notices, strikes, appeals, court / government
orders and automatic matches around it).

  * COUNTS ONLY - no names, no account ids, no content, no claimant organisations. Safe to publish.
  * Everything is computed from the same tables the review process writes (claims, takedowns, counter-notices,
    strikes, appeals, legal orders, audit log), so the report can't disagree with what really happened.
  * One report = one period. Pick it with  ?year=2026&half=1  |  ?year=2026  |  ?from=2026-01-01&to=2026-06-30
    (both ends inclusive). Default: the last COPYRIGHT_TRANSPARENCY_DEFAULT_DAYS (180) days. Max 366 days.
  * Served as JSON (GET /copyright/transparency/), as a web page (GET /legal/transparency-report/) and from the
    command  manage.py copyright_transparency_report  (json / csv / html file for the lawyer or the website).

How to read it (also printed on the page):
  - "received" counts notices by the day they were filed; "outcome of received" is where those notices stand NOW.
  - "decided" / "restored" / "removed" count events by the day they happened, so they can include notices that
    were filed in an earlier period.
  - "removed" = a claim was UPHELD in the period (content taken down + files quarantined). A temporary hold that
    ended without a decision is NOT a removal - it shows under "held at once" and under "restored".
  - Court / government orders are listed on their own: they are not copyright claims and give no strike.

Settings: COPYRIGHT_TRANSPARENCY_PUBLIC (true - false = the public endpoints answer 404, the command still works),
COPYRIGHT_TRANSPARENCY_CACHE_SECONDS (600; 0 = no cache), COPYRIGHT_TRANSPARENCY_DEFAULT_DAYS (180).
"""

import csv
import io
import statistics
from datetime import date, datetime, time, timedelta
from html import escape

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone

from .models import (
    CopyrightAppeal, CopyrightAuditLog, CopyrightClaim as C, CopyrightCounterNotice as CN, CopyrightStanding,
    CopyrightStrike, CopyrightTakedown as T, LegalOrder, LegalOrderItem,
)

MAX_DAYS = 366
MAX_DECISION_SAMPLE = 20000


def _cfg(name, default):
    return getattr(settings, name, default)


def is_public():
    return bool(_cfg("COPYRIGHT_TRANSPARENCY_PUBLIC", True))


# ------------------------------------------------------------------ period
def _day_start(d):
    return timezone.make_aware(datetime.combine(d, time.min))


def resolve_period(params=None, now=None):
    """-> (start, end_exclusive, first_day, last_day). Raises ValueError (message is safe to show) on bad input."""
    params = params or {}
    now = now or timezone.now()

    def get(name):
        v = params.get(name)
        return str(v).strip() if v not in (None, "") else ""

    year, half, d_from, d_to = get("year"), get("half"), get("from"), get("to")
    try:
        if d_from or d_to:
            if not (d_from and d_to):
                raise ValueError("Give both 'from' and 'to' (YYYY-MM-DD).")
            first, last = date.fromisoformat(d_from), date.fromisoformat(d_to)
        elif half and not year:
            raise ValueError("'half' needs a 'year'.")
        elif year:
            y = int(year)
            if not 2000 <= y <= 2100:
                raise ValueError("Year out of range.")
            if half in ("1", "2"):
                first, last = (date(y, 1, 1), date(y, 6, 30)) if half == "1" else (date(y, 7, 1), date(y, 12, 31))
            elif half:
                raise ValueError("'half' must be 1 or 2.")
            else:
                first, last = date(y, 1, 1), date(y, 12, 31)
        else:
            days = max(1, int(_cfg("COPYRIGHT_TRANSPARENCY_DEFAULT_DAYS", 180)))
            last = timezone.localtime(now).date()
            first = last - timedelta(days=days - 1)
    except ValueError as exc:
        text = str(exc)
        if "Give both" in text or "range" in text or "must be" in text or "needs a" in text:
            raise
        raise ValueError("Dates must look like 2026-01-31 and 'year' must be a number.") from exc

    if last < first:
        raise ValueError("'to' is before 'from'.")
    if (last - first).days + 1 > MAX_DAYS:
        raise ValueError(f"A report can cover at most {MAX_DAYS} days.")
    return _day_start(first), _day_start(last + timedelta(days=1)), first, last


# ------------------------------------------------------------------ counting helpers
def _by(qs, field, keys=()):
    """{value: count} with every key in `keys` present (so the JSON shape never changes)."""
    out = {k: 0 for k in keys}
    for row in qs.order_by().values(field).annotate(n=Count("pk")):
        key = row[field]
        out[key] = out.get(key, 0) + row["n"]
    return out


def _in(field, start, end):
    return {f"{field}__gte": start, f"{field}__lt": end}


def _enum(choices_cls):
    return [v for v, _ in choices_cls.choices]


def build_report(start, end, first=None, last=None):
    """Compute the report for [start, end). Pure read - nothing is written."""
    first = first or timezone.localtime(start).date()
    last = last or (timezone.localtime(end) - timedelta(seconds=1)).date()
    now = timezone.now()

    # ---------------- claims
    received = C.objects.filter(**_in("created_at", start, end))
    decided = C.objects.filter(**_in("decided_at", start, end))
    decided_hours = [
        (d - c).total_seconds() / 3600.0
        for c, d in decided.values_list("created_at", "decided_at")[:MAX_DECISION_SAMPLE]
    ]
    claims = {
        "received": received.count(),
        "by_source": _by(received, "source", _enum(C.Source)),
        "by_content_type": _by(received, "target_type", _enum(C.TargetType)),
        "outcome_of_received": _by(received, "status", _enum(C.Status)),
        "decided_in_period": {
            "upheld": decided.filter(status=C.Status.UPHELD).count(),
            "rejected": decided.filter(status=C.Status.REJECTED).count(),
            "withdrawn": decided.filter(status=C.Status.WITHDRAWN).count(),
            "rejected_as_bad_faith": decided.filter(bad_faith=True).count(),
        },
        "median_hours_to_decision": round(statistics.median(decided_hours), 1) if decided_hours else None,
    }

    # ---------------- content: held / removed / restored
    upheld = C.objects.filter(status=C.Status.UPHELD, **_in("decided_at", start, end))
    removed_items = upheld.values_list("target_type", "target_id").distinct().count()
    restored_qs = T.objects.filter(state=T.State.RESTORED, **_in("restored_at", start, end))
    reasons = {"claim_rejected": 0, "claim_withdrawn": 0, "counter_notice": 0, "other": 0}
    mapping = {"rejected": "claim_rejected", "withdrawn": "claim_withdrawn", "restored": "counter_notice"}
    for status, n in _by(restored_qs, "claim__status").items():
        reasons[mapping.get(status, "other")] += n
    content = {
        "held_at_once": T.objects.filter(auto_applied=True, **_in("applied_at", start, end)).count(),
        "removed": {"claims_upheld": upheld.count(), "content_items": removed_items},
        "restored": {"total": restored_qs.count(), "by_reason": reasons},
    }

    # ---------------- counter-notices
    counters = CN.objects.filter(**_in("filed_at", start, end))
    counter_notices = {
        "filed": counters.count(),
        "outcome_of_filed": _by(counters, "status", _enum(CN.Status)),
    }

    # ---------------- strikes / accounts
    strikes = {
        "issued": CopyrightStrike.objects.filter(**_in("issued_at", start, end)).count(),
        "revoked": CopyrightStrike.objects.filter(**_in("revoked_at", start, end)).count(),
        "accounts_terminated": CopyrightStanding.objects.filter(**_in("terminated_at", start, end)).count(),
    }

    # ---------------- appeals
    appeals_in = CopyrightAppeal.objects.filter(**_in("created_at", start, end))
    appeals_out = CopyrightAppeal.objects.filter(**_in("decided_at", start, end))
    appeals = {
        "received": appeals_in.count(),
        "by_kind": _by(appeals_in, "kind", _enum(CopyrightAppeal.Kind)),
        "decided_in_period": {
            "accepted": appeals_out.filter(status=CopyrightAppeal.Status.ACCEPTED).count(),
            "rejected": appeals_out.filter(status=CopyrightAppeal.Status.REJECTED).count(),
        },
    }

    # ---------------- court / government orders (not copyright claims, no strikes)
    orders_in = LegalOrder.objects.filter(**_in("received_at", start, end))
    orders_out = LegalOrder.objects.filter(**_in("decided_at", start, end))
    legal_orders = {
        "received": orders_in.count(),
        "by_type": _by(orders_in, "order_type", _enum(LegalOrder.OrderType)),
        "outcome_of_received": _by(orders_in, "status", _enum(LegalOrder.Status)),
        "decided_in_period": _by(orders_out, "status", [s for s in _enum(LegalOrder.Status)
                                                      if s in ("complied", "rejected", "reversed")]),
        "content_items": _by(LegalOrderItem.objects.filter(order__in=orders_in), "state",
                             _enum(LegalOrderItem.State)),
    }

    # ---------------- automatic detection (re-uploads of removed content)
    auto_claims = received.filter(source=C.Source.AUTO)
    automatic = {
        "matches_held_for_review": auto_claims.count(),
        "outcome": _by(auto_claims, "status", _enum(C.Status)),
        "uploads_refused_exact_copy": CopyrightAuditLog.objects.filter(
            action="reupload_blocked", **_in("created_at", start, end)).count(),
    }

    # ---------------- who files, and how often they are right (rights holders only)
    quality = {}
    for row in received.exclude(source=C.Source.AUTO).values("claimant_tier", "status", "bad_faith"):
        tier = row["claimant_tier"] or "unknown"
        q = quality.setdefault(tier, {"received": 0, "upheld": 0, "rejected": 0, "bad_faith": 0})
        q["received"] += 1
        if row["status"] == C.Status.UPHELD:
            q["upheld"] += 1
        elif row["status"] == C.Status.REJECTED:
            q["rejected"] += 1
        if row["bad_faith"]:
            q["bad_faith"] += 1

    return {
        "period": {"from": first.isoformat(), "to": last.isoformat(), "days": (last - first).days + 1},
        "generated_at": now.isoformat(timespec="seconds"),
        "claims": claims,
        "content": content,
        "counter_notices": counter_notices,
        "strikes_and_accounts": strikes,
        "appeals": appeals,
        "legal_orders": legal_orders,
        "automatic_detection": automatic,
        "claimant_quality": quality,
        "right_now": {
            "open_claims": C.objects.filter(status__in=C.OPEN_STATUSES).count(),
            "content_on_hold": T.objects.filter(state=T.State.HELD).count(),
            "content_removed": T.objects.filter(state=T.State.REMOVED).count(),
            "open_legal_orders": LegalOrder.objects.filter(
                status__in=(LegalOrder.Status.RECEIVED, LegalOrder.Status.IN_REVIEW)).count(),
        },
    }


def get_report(params=None, *, use_cache=True):
    """Resolve the period, then build (or fetch the cached) report. Raises ValueError on bad parameters."""
    start, end, first, last = resolve_period(params)
    seconds = int(_cfg("COPYRIGHT_TRANSPARENCY_CACHE_SECONDS", 600))
    if not use_cache or seconds <= 0:
        return build_report(start, end, first, last)
    key = f"cr_transparency:{first.isoformat()}:{last.isoformat()}"
    report = cache.get(key)
    if report is None:
        report = build_report(start, end, first, last)
        cache.set(key, report, seconds)
    return report


# ------------------------------------------------------------------ CSV (section, item, value)
def to_rows(report):
    rows = []

    def walk(prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(prefix + [str(k)], v)
        else:
            rows.append((prefix[0], ".".join(prefix[1:]) or "-", "" if value is None else value))

    for section, value in report.items():
        walk([section], value)
    return rows


def render_csv(report):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "item", "value"])
    w.writerows(to_rows(report))
    return buf.getvalue()


# ------------------------------------------------------------------ HTML page
_LABELS = {
    "received": "Received", "by_source": "By source", "by_content_type": "By type of content",
    "outcome_of_received": "Where these stand now", "decided_in_period": "Decided in the period",
    "median_hours_to_decision": "Median hours to a decision", "held_at_once": "Hidden at once while under review",
    "claims_upheld": "Claims upheld (content removed)", "content_items": "Content items",
    "by_reason": "Why it was restored", "total": "Total", "filed": "Filed", "outcome_of_filed": "Where these stand now",
    "issued": "Strikes issued", "revoked": "Strikes revoked", "accounts_terminated": "Accounts terminated",
    "by_kind": "By kind", "by_type": "By type", "matches_held_for_review": "Matches held for review",
    "uploads_refused_exact_copy": "Uploads refused (exact copy of removed file)", "outcome": "Outcome",
    "open_claims": "Open claims", "content_on_hold": "Content on hold now", "content_removed": "Content removed now",
    "open_legal_orders": "Open court / government orders",
}


def _label(key):
    return _LABELS.get(key, str(key).replace("_", " ").capitalize())


def _table(title, value, note=""):
    def rows(d, depth=0):
        out = []
        for k, v in d.items():
            pad = f' style="padding-left:{1 + depth * 1.2}rem"'
            if isinstance(v, dict):
                out.append(f'<tr class="grp"><td{pad} colspan="2">{escape(_label(k))}</td></tr>')
                out.extend(rows(v, depth + 1))
            else:
                shown = "&ndash;" if v is None else escape(str(v))
                out.append(f"<tr><td{pad}>{escape(_label(k))}</td><td class=\"n\">{shown}</td></tr>")
        return out

    body = "".join(rows(value))
    extra = f'<p class="muted">{escape(note)}</p>' if note else ""
    return f"<h2>{escape(title)}</h2>{extra}<table>{body}</table>"


def render_html(report):
    app = escape(getattr(settings, "APP_NAME", "") or "LearnScroll")
    p = report["period"]
    quality = {k: v for k, v in report["claimant_quality"].items()} or {"none": {"received": 0}}
    parts = [
        _table("Copyright claims", report["claims"],
               "Notices from rights holders, from user reports, from staff and automatic matches."),
        _table("Content hidden, removed and restored", report["content"],
               "Removed = a claim was upheld. Hidden-at-once content that was later released shows under restored."),
        _table("Counter-notices", report["counter_notices"]),
        _table("Strikes and accounts", report["strikes_and_accounts"]),
        _table("Appeals", report["appeals"]),
        _table("Court and government orders", report["legal_orders"],
               "These are not copyright claims and do not give a strike."),
        _table("Automatic detection of removed content", report["automatic_detection"],
               "A similar file is only held and reviewed by a person - it is never removed automatically."),
        _table("Notices by claimant trust level", quality,
               "trusted / publisher = staff-vetted rights holders; standard / new = everyone else."),
        _table("Right now", report["right_now"]),
    ]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transparency Report - {app}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;line-height:1.6;color:#222}}
h1,h2{{line-height:1.25}}table{{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}}
td{{border-bottom:1px solid #e4e4e4;padding:.3rem .5rem}}td.n{{text-align:right;font-variant-numeric:tabular-nums;width:6rem}}
tr.grp td{{font-weight:600;background:#f6f6f6}}.muted{{color:#666;font-size:.9rem}}</style></head><body>
<h1>Transparency Report</h1>
<p class="muted">{app} &middot; {escape(p['from'])} to {escape(p['to'])} ({p['days']} days) &middot; generated {escape(report['generated_at'][:10])}</p>
<p>How many copyright claims we received, how much content we removed and how much we restored.
Numbers only - no names or content. Read together with our <a href="/legal/copyright-policy/">Copyright Policy</a>.</p>
{''.join(parts)}
<h2>How to read this</h2>
<p class="muted">&ldquo;Received&rdquo; counts notices by the day they were filed; &ldquo;where these stand now&rdquo; is their status today.
Decided, removed and restored are counted by the day the event happened, so they can include notices filed in an earlier period.
A temporary hold that ends without a decision is not a removal. Numbers can change slightly when a decision is reversed later.</p>
</body></html>"""
